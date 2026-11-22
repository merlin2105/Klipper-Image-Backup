"""SMB/NAS target helpers: input checks, stored connection, mount arguments.

Runs no privileged commands itself; the media worker calls mount/umount.
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import secrets
from dataclasses import asdict, dataclass
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional

MOUNT_TIMEOUT_SECONDS = 15
MAX_FOLDER_DEPTH = 8
_SERVER = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")
_PASTED = re.compile(r"^(?:smb:)?[/\\]{2}([^/\\]+)[/\\]+([^/\\]+)[/\\]*$", re.IGNORECASE)
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_MOUNT_OPTIONS = "nosuid,nodev,noexec,uid=0,gid=0,file_mode=0600,dir_mode=0700"
_UNREACHABLE = "NAS nicht erreichbar. Prüfe IP-Adresse/Name und ob das NAS eingeschaltet ist."
_MOUNT_ERRORS = {
    13: "Anmeldung abgelehnt. Prüfe Benutzer, Passwort und die genaue Schreibweise des Freigabenamens (Groß-/Kleinschreibung).",
    2: "Freigabe nicht gefunden. Prüfe den Freigabenamen (Groß-/Kleinschreibung).",
    19: "Das SMB-Modul (cifs) ist nicht geladen. Starte den Pi neu oder installiere KIBM erneut.",
    110: _UNREACHABLE,
    112: _UNREACHABLE,
    113: _UNREACHABLE,
    115: _UNREACHABLE,
}


@dataclass(frozen=True)
class NasConfig:
    server: str
    share: str
    username: str
    domain: str
    folder: str

    def unc(self) -> str:
        return f"//{self.server}/{self.share}"

    def display(self) -> str:
        return f"{self.unc()}/{self.folder}" if self.folder else self.unc()

    def user_label(self) -> str:
        return f"{self.domain}\\{self.username}" if self.domain else self.username


def split_address(server: str, share: str) -> tuple[str, str]:
    """Accept a full address pasted into the server field (\\\\h\\S, //h/S, smb://h/S)."""
    match = _PASTED.fullmatch(server.strip(" "))
    if match:
        return match.group(1), match.group(2)
    return server.strip(" "), share.strip(" ")


def split_user(user: str) -> tuple[str, str]:
    if "\\" in user:
        domain, name = user.split("\\", 1)
        return name, domain
    return user, ""


def _plain_text(value: str, label: str, max_length: int) -> str:
    if _CONTROL.search(value) or len(value) > max_length:
        raise ValueError(f"{label} enthält ungültige Zeichen oder ist zu lang.")
    return value


def _segment(name: str, max_length: int) -> str:
    if (not name or name in {".", ".."} or len(name) > max_length
            or "/" in name or "\\" in name or _CONTROL.search(name)):
        raise ValueError(f"Ungültiger Ordnername: {name!r}")
    return name


def validate_segment(name: str) -> str:
    return _segment(name, 80)


def validate_folder(folder: str) -> str:
    if folder.startswith("/") or "\\" in folder or _CONTROL.search(folder):
        raise ValueError("Der Ordner muss ein Pfad innerhalb der Freigabe sein.")
    parts = [part for part in folder.split("/") if part]
    if len(parts) > MAX_FOLDER_DEPTH:
        raise ValueError(f"Der Ordner darf höchstens {MAX_FOLDER_DEPTH} Ebenen tief sein.")
    return "/".join(_segment(part, 255) for part in parts)


def validate_password(password: str) -> str:
    return _plain_text(password, "Das Passwort", 256)


def validate_config(server: str, share: str, user: str, folder: str) -> NasConfig:
    server, share = split_address(server, share)
    if not _SERVER.fullmatch(server):
        raise ValueError("Ungültige IP-Adresse oder ungültiger Name des NAS.")
    if not share or len(share) > 80 or re.search(r"[/\\,=]", share) or _CONTROL.search(share):
        raise ValueError("Ungültiger Freigabename.")
    username, domain = split_user(user.strip(" "))
    if not username:
        raise ValueError("Bitte einen Benutzer angeben.")
    _plain_text(username, "Der Benutzer", 256)
    _plain_text(domain, "Die Domäne", 256)
    return NasConfig(server, share, username, domain, validate_folder(folder))


def safe_subpath(root: Path, folder: str) -> Path:
    """root/folder without following symlinks out of (or inside) the share."""
    target = root
    for part in validate_folder(folder).split("/") if folder else []:
        target = target / part
        if target.is_symlink():
            raise ValueError("Der Sicherungsordner darf kein symbolischer Link sein.")
    real_root = os.path.realpath(root)
    real = os.path.realpath(target)
    if real != real_root and not real.startswith(real_root + os.sep):
        raise ValueError("Der Sicherungsordner liegt außerhalb der Freigabe.")
    return target


def credentials_text(config: NasConfig, password: str) -> str:
    text = f"username={config.username}\npassword={validate_password(password)}\n"
    return text + (f"domain={config.domain}\n" if config.domain else "")


def write_private(path: Path, text: str) -> None:
    staged = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    fd = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(staged, path)
    except BaseException:
        staged.unlink(missing_ok=True)
        raise


def load_config(path: Path) -> Optional[NasConfig]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    return NasConfig(**{key: str(data.get(key, "")) for key in ("server", "share", "username", "domain", "folder")})


def save_config(path: Path, config: NasConfig) -> None:
    write_private(path, json.dumps(asdict(config)) + "\n")


def mount_args(config: NasConfig, credentials: Path, target: Path) -> list[str]:
    return ["mount", "-t", "cifs", "-o", f"credentials={credentials},{_MOUNT_OPTIONS}", "--", config.unc(), str(target)]


def mount_error(stderr: str, timed_out: bool) -> str:
    if timed_out:
        return f"Zeitüberschreitung: Das NAS hat nicht innerhalb von {MOUNT_TIMEOUT_SECONDS} Sekunden geantwortet."
    if "could not resolve address" in stderr:
        return "Der Name des NAS konnte nicht aufgelöst werden. Versuche die IP-Adresse."
    match = re.search(r"error\((\d+)\)", stderr)
    if match and int(match.group(1)) in _MOUNT_ERRORS:
        return _MOUNT_ERRORS[int(match.group(1))]
    detail = (stderr.strip().splitlines() or [""])[0][:120]
    return f"Die Freigabe konnte nicht eingebunden werden ({detail})."


def _ip_key(address: str) -> ipaddress.IPv4Address:
    return ipaddress.IPv4Address(address)


def _avahi_unescape(value: str) -> str:
    return re.sub(r"\\(\d{3})", lambda m: chr(int(m.group(1))), value)


def parse_avahi(output: str) -> list[dict]:
    """Resolved IPv4 entries of `avahi-browse -rpt _smb._tcp`."""
    found: dict[str, str] = {}
    for line in output.splitlines():
        fields = line.split(";")
        if len(fields) < 9 or fields[0] != "=" or fields[2] != "IPv4":
            continue
        try:
            _ip_key(fields[7])
        except ValueError:
            continue
        found.setdefault(fields[7], _avahi_unescape(fields[3]))
    return [{"name": found[address], "address": address} for address in sorted(found, key=_ip_key)]


def parse_shares(output: str) -> list[str]:
    """Disk shares from `smbclient -g -L`, without hidden $ shares."""
    shares = {
        fields[1] for fields in (line.split("|") for line in output.splitlines())
        if len(fields) >= 2 and fields[0] == "Disk" and fields[1] and not fields[1].endswith("$")
    }
    return sorted(shares, key=str.lower)


def subnet_hosts(address: str) -> list[str]:
    network = ipaddress.IPv4Network(f"{address}/24", strict=False)
    return [str(host) for host in network.hosts() if str(host) != address]


def scan_port_445(hosts: list[str], connect: Callable[[str], bool], workers: int = 64) -> list[str]:
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(connect, hosts))
    return sorted((host for host, is_open in zip(hosts, results) if is_open), key=_ip_key)


def merge_discovered(mdns: list[dict], scanned: list[str], reverse_name: Callable[[str], Optional[str]]) -> list[dict]:
    names = {item["address"]: item["name"] for item in mdns}
    for address in scanned:
        if address not in names:
            names[address] = reverse_name(address) or address
    return [{"name": names[address], "address": address} for address in sorted(names, key=_ip_key)]
