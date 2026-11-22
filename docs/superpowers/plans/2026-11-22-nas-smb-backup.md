# NAS-Sicherung per SMB – Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Neues Sicherungsziel „NAS (SMB)“: einmal im Dashboard einrichten, danach per Knopf ein Image der
System-microSD (mit Prüfen, Reparatur, optional Shrink) auf eine SMB-Freigabe schreiben.

**Architecture:** Reine Logik (Eingabeprüfung, Speicherung, Fehlertexte, Parser) in neuem Modul
`backend/kibm/nas.py`, ohne Root-Aufrufe, voll testbar. Der Root-Worker `media_worker.py` hängt die Freigabe nur für
die Dauer einer Aktion in `/run/kibm-media/nas` ein; die bestehende USB-Pipeline wird so umgebaut, dass sie in ein
beliebiges Zielverzeichnis schreibt. Der Web-Dienst `main.py` reicht Anfragen mit Sitzung + CSRF an neue
Worker-Aktionen weiter; das Dashboard bekommt eine NAS-Karte.

**Tech Stack:** Python ≥ 3.9 (Debian 11), FastAPI, pytest, Vanilla-JS (`app.js`, `i18n.js`), `mount.cifs`,
`smbclient`, `avahi-browse`, systemd.

**Spec:** `docs/superpowers/specs/2026-11-21-nas-smb-backup-design.md` (inkl. Abschnitt „Ergebnis Hardware-Spike“)

## Global Constraints

- Keine Änderung an `/etc/fstab`, keine dauerhafte Einbindung; jede Einbindung wird im `finally` ausgehängt.
- Passwort nie in Mount-Optionen, Prozessliste, Logs, Fehlertexten oder API-Antworten; nur in 0600-Dateien.
- Gespeicherte Verbindung: `/var/lib/kibm-media/nas.json` (0600) und `/var/lib/kibm-media/nas-credentials` (0600), atomar geschrieben (Temp-Datei + `os.replace`).
- Temporäre Zugangsdaten (noch nicht gespeichert): `/run/kibm-media/nas-cred-<token_hex(8)>` (0600), nach der Aktion gelöscht.
- Einhängepunkt `/run/kibm-media/nas`; Mount-Optionen exakt `credentials=<datei>,nosuid,nodev,noexec,uid=0,gid=0,file_mode=0600,dir_mode=0700`; Mount-Timeout 15 s.
- Server: IPv4 oder Hostname `[A-Za-z0-9.-]`, ≤ 253 Zeichen. Freigabe: 1–80 Zeichen, ohne `/ \ , =` und Steuerzeichen. Benutzer/Domäne/Passwort: ohne Steuerzeichen/Zeilenumbrüche, Passwort ≤ 256, Benutzer nicht leer. Ordner: relativ, Segmente ohne `.`/`..`, ohne führenden `/`, ≤ 8 Ebenen.
- `DOMÄNE\benutzer` im Benutzerfeld wird in `domain` + `username` getrennt.
- Ordnerliste: nur Verzeichnisse, max. 200, Symlinks nicht verfolgt.
- Nur ein Medien-Auftrag gleichzeitig (gemeinsames `_job`) – USB und NAS schließen sich aus.
- Dateinamen wie USB: `<host>-<YYYYmmdd-HHMMSS>-sdkarte[-voll|-geshrinkt].img`; versteckte `.part`-Datei bis fertig.
- Auf dem NAS löscht KIBM nur die ausgewählte `.img`-Datei oder die eigene `.part`-Datei.
- Ohne Sitzung liefert `GET /api/backup/nas` nur `{"configured": bool}`; alles andere verlangt Sitzung (+ CSRF bei POST).
- Leeres Passwort bei „Zugangsdaten ändern“ = gespeichertes behalten, **nur** wenn server, share, username, domain unverändert.
- NAS-Images nie in der 10-s-Abfrage `images`; eigene Aktion `nas_images`, nur auf Nutzeraktion.
- Alle Dashboard-Texte DE im Code, EN-Übersetzung in `i18n.js`.
- Python-3.9-kompatibel (kein `match`, keine `X | Y`-Typen zur Laufzeit).
- Fehlertext für Mount-Fehler 13: „Anmeldung abgelehnt. Prüfe Benutzer, Passwort und die genaue Schreibweise des Freigabenamens (Groß-/Kleinschreibung).“

## Review Focus

1. **NAS fällt während der Kopie weg** (Netz/Strom): Auftrag endet mit `fehler`, `.part` wird zu löschen versucht, Aushängen hängt nicht ewig (`umount --lazy` als Rückfall) → Test in Task 4.
2. **Status-Prüfung, während ein NAS-Auftrag läuft**: `nas_check`/`nas_images` dürfen den belegten Einhängepunkt nicht erneut ein- oder aushängen → `busy: true` → Tests in Task 3 und 5.
3. **Zugangsdaten ändern mit leerem Passwort und geändertem Server**: abgelehnt, Passwort geht nie an einen anderen Host → Test in Task 3.
4. **Ordner, der per Symlink oder `..` aus der Freigabe zeigt**: abgelehnt (`os.path.realpath` gegen Einhängepunkt) → Tests in Task 1 und 3.
5. **Ordner nach dem Speichern auf dem NAS gelöscht/umbenannt**: `nas_check` meldet rot „Sicherungsordner nicht gefunden: …“, `nas_start` schreibt nichts → Tests in Task 3 und 4.

---

## File Structure

- Create `backend/kibm/nas.py` – Validierung, Konfig-/Credentials-Dateien, Mount-Argumente, Fehlertexte, Parser (avahi, smbclient), Subnetz-Scan. Führt selbst keine Root-Befehle aus.
- Create `backend/tests/test_nas.py`
- Modify `backend/kibm/media_worker.py` – NAS-Einhängen, neue Aktionen, Pipeline mit Zielverzeichnis.
- Modify `backend/tests/test_media_worker.py`
- Modify `backend/kibm/main.py` – Modelle + Endpunkte `/api/backup/nas*`.
- Modify `backend/tests/test_api.py`
- Modify `backend/kibm/static/index.html`, `app.js`, `app.css`, `i18n.js`
- Modify `install.sh`, `uninstall.sh`, `systemd/kibm-media.service`
- Modify `README.md`, `README.en.md`, `docs/api.md`, `docs/security.md`, `docs/architecture.md`

Testbefehl überall: `uv run --extra dev pytest -q backend/tests` (Stand vor Beginn: 124 passed).

---

### Task 1: `nas.py` – Eingaben, Speicherung, Mount-Argumente, Fehlertexte

**Files:** Create `backend/kibm/nas.py`; Test `backend/tests/test_nas.py`

**Interfaces – Produces:**
- `@dataclass(frozen=True) class NasConfig`: `server, share, username, domain, folder: str`; `unc() -> str` = `//server/share`; `display() -> str` = `//server/share/folder` (ohne `/folder`, wenn leer); `user_label() -> str` = `DOMAIN\username` oder `username`.
- `split_address(server: str, share: str) -> tuple[str, str]` – zerlegt `\\h\S`, `//h/S`, `smb://h/S` im Server-Feld; sonst unverändert.
- `split_user(user: str) -> tuple[str, str]` – `"DOM\\u"` → `("u", "DOM")`, sonst `(user, "")`.
- `validate_folder(folder: str) -> str` – normalisiert (`a//b/` → `a/b`; `""` erlaubt).
- `validate_segment(name: str) -> str` – ein einzelner Ordnername (gleiche Regeln, kein `/`).
- `validate_config(server: str, share: str, user: str, folder: str) -> NasConfig` – wirft `ValueError` (deutscher Text).
- `validate_password(password: str) -> str`
- `safe_subpath(root: Path, folder: str) -> Path` – wirft `ValueError`, wenn `realpath(root/folder)` nicht unter `realpath(root)` liegt.
- `credentials_text(config: NasConfig, password: str) -> str`
- `write_private(path: Path, text: str) -> None` – atomar, 0600, `O_NOFOLLOW`.
- `load_config(path: Path) -> Optional[NasConfig]`, `save_config(path: Path, config: NasConfig) -> None` (JSON-Felder `server, share, username, domain, folder`).
- `mount_args(config: NasConfig, credentials: Path, target: Path) -> list[str]` = `["mount", "-t", "cifs", "-o", OPTS, "--", unc, str(target)]`.
- `mount_error(stderr: str, timed_out: bool) -> str`

- [ ] **Step 1: Write the failing tests**

```python
import pytest
from kibm import nas

def test_split_address_accepts_pasted_unc_forms():
    for raw in ("\\\\192.168.1.242\\Backup", "//192.168.1.242/Backup", "smb://192.168.1.242/Backup"):
        assert nas.split_address(raw, "") == ("192.168.1.242", "Backup")
    assert nas.split_address("nas.local", "Backup") == ("nas.local", "Backup")

def test_validate_config_splits_domain_and_keeps_share_case():
    config = nas.validate_config("192.168.1.242", "Backup", "WORK\\merlin", "klipper/prusa/")
    assert (config.server, config.share, config.username, config.domain, config.folder) == (
        "192.168.1.242", "Backup", "merlin", "WORK", "klipper/prusa")
    assert config.display() == "//192.168.1.242/Backup/klipper/prusa"
    assert config.user_label() == "WORK\\merlin"

@pytest.mark.parametrize("server", ["", "a b", "a,b", "a=b", "a/b", "x" * 254, "host\n", "-x"])
def test_validate_config_rejects_bad_server(server):
    with pytest.raises(ValueError):
        nas.validate_config(server, "S", "u", "")

@pytest.mark.parametrize("share", ["", "a/b", "a\\b", "a,b", "a=b", "x" * 81, "a\tb"])
def test_validate_config_rejects_bad_share(share):
    with pytest.raises(ValueError):
        nas.validate_config("h", share, "u", "")

@pytest.mark.parametrize("folder", ["/abs", "a/../b", "..", "./a", "a/b/c/d/e/f/g/h/i", "a\nb"])
def test_validate_folder_rejects_escape(folder):
    with pytest.raises(ValueError):
        nas.validate_folder(folder)

@pytest.mark.parametrize("password", ["pw\nusername=root", "x" * 257, "a\x00b"])
def test_validate_password_rejects_control_chars_and_length(password):
    with pytest.raises(ValueError):
        nas.validate_password(password)

def test_validate_config_rejects_empty_user():
    with pytest.raises(ValueError):
        nas.validate_config("h", "S", "", "")

def test_safe_subpath_rejects_symlink_out_of_root(tmp_path):
    (tmp_path / "root").mkdir(); (tmp_path / "outside").mkdir()
    (tmp_path / "root" / "link").symlink_to(tmp_path / "outside")
    with pytest.raises(ValueError):
        nas.safe_subpath(tmp_path / "root", "link")
    assert nas.safe_subpath(tmp_path / "root", "") == tmp_path / "root"

def test_credentials_text_and_private_write(tmp_path):
    config = nas.validate_config("h", "S", "D\\u", "")
    path = tmp_path / "cred"
    nas.write_private(path, nas.credentials_text(config, "geheim"))
    assert path.read_text() == "username=u\npassword=geheim\ndomain=D\n"
    assert path.stat().st_mode & 0o777 == 0o600

def test_config_roundtrip_has_no_password(tmp_path):
    config = nas.validate_config("h", "S", "u", "a/b")
    nas.save_config(tmp_path / "nas.json", config)
    assert "password" not in (tmp_path / "nas.json").read_text()
    assert (tmp_path / "nas.json").stat().st_mode & 0o777 == 0o600
    assert nas.load_config(tmp_path / "nas.json") == config
    assert nas.load_config(tmp_path / "missing.json") is None

def test_mount_args_never_contain_the_password(tmp_path):
    args = nas.mount_args(nas.validate_config("h", "S", "u", ""), tmp_path / "cred", tmp_path / "mnt")
    assert args == ["mount", "-t", "cifs", "-o",
                    f"credentials={tmp_path / 'cred'},nosuid,nodev,noexec,uid=0,gid=0,file_mode=0600,dir_mode=0700",
                    "--", "//h/S", str(tmp_path / "mnt")]

UNREACHABLE = "NAS nicht erreichbar. Prüfe IP-Adresse/Name und ob das NAS eingeschaltet ist."

@pytest.mark.parametrize("stderr,expected", [
    ("mount error(13): Permission denied", "Anmeldung abgelehnt. Prüfe Benutzer, Passwort und die genaue Schreibweise des Freigabenamens (Groß-/Kleinschreibung)."),
    ("mount error(2): No such file or directory", "Freigabe nicht gefunden. Prüfe den Freigabenamen (Groß-/Kleinschreibung)."),
    ("mount error(112): Host is down", UNREACHABLE),
    ("mount error(113): No route to host", UNREACHABLE),
    ("mount error(115): Operation now in progress", UNREACHABLE),
    ("mount error: could not resolve address for nas: Unknown error", "Der Name des NAS konnte nicht aufgelöst werden. Versuche die IP-Adresse."),
    ("mount error(19): No such device", "Das SMB-Modul (cifs) ist nicht geladen. Starte den Pi neu oder installiere KIBM erneut."),
    ("something else\nsecond line", "Die Freigabe konnte nicht eingebunden werden (something else)."),
])
def test_mount_error_texts(stderr, expected):
    assert nas.mount_error(stderr, timed_out=False) == expected

def test_mount_error_timeout():
    assert nas.mount_error("", timed_out=True) == "Zeitüberschreitung: Das NAS hat nicht innerhalb von 15 Sekunden geantwortet."
```

- [ ] **Step 2:** Run `uv run --extra dev pytest -q backend/tests/test_nas.py` → FAIL (`ModuleNotFoundError: kibm.nas`).
- [ ] **Step 3:** Implement `backend/kibm/nas.py`. Server-Regex `^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$`. Fallback-Detail von `mount_error`: erste stderr-Zeile, max. 120 Zeichen.
- [ ] **Step 4:** Run → PASS; gesamte Suite → PASS.
- [ ] **Step 5:** Commit `feat(nas): validation, storage and mount helpers for SMB target`.

---

### Task 2: `nas.py` – NAS-Suche und Freigabenliste

**Files:** Modify `backend/kibm/nas.py`; Test `backend/tests/test_nas.py`

**Interfaces – Produces:**
- `parse_avahi(output: str) -> list[dict]` – Zeilen `=;iface;IPv4;name;type;domain;host;address;port;txt` → `{"name", "address"}`, nur IPv4, je Adresse einmal, avahi-Escapes (`\032`) dekodiert, nach IP sortiert.
- `parse_shares(output: str) -> list[str]` – `smbclient -g`-Zeilen `Disk|Name|Kommentar`, ohne `$`-Endung, sortiert ohne Groß-/Kleinschreibung.
- `subnet_hosts(address: str) -> list[str]` – Hosts .1–.254 des /24 ohne `address`.
- `scan_port_445(hosts: list[str], connect: Callable[[str], bool], workers: int = 64) -> list[str]` – parallel (`ThreadPoolExecutor`), nach IP sortiert.
- `merge_discovered(mdns: list[dict], scanned: list[str], reverse_name: Callable[[str], Optional[str]]) -> list[dict]` – mDNS-Name vor Reverse-DNS vor IP; nach IP sortiert.

- [ ] **Step 1: Failing tests**

```python
AVAHI = ("+;eth0;IPv4;truenas;_smb._tcp;local\n"
         "=;eth0;IPv4;truenas;_smb._tcp;local;truenas.local;192.168.1.242;445;\n"
         "=;eth0;IPv6;truenas;_smb._tcp;local;truenas.local;fe80::1;445;\n"
         "=;wlan0;IPv4;truenas;_smb._tcp;local;truenas.local;192.168.1.242;445;\n"
         "=;wlan0;IPv4;Mein\\032NAS;_smb._tcp;local;nas.local;192.168.1.10;445;\n")

def test_parse_avahi_keeps_ipv4_and_decodes_names():
    assert nas.parse_avahi(AVAHI) == [{"name": "Mein NAS", "address": "192.168.1.10"},
                                      {"name": "truenas", "address": "192.168.1.242"}]

def test_parse_shares_skips_hidden_and_non_disk():
    out = "Disk|Backup|\nDisk|IPC$|\nIPC|IPC$|IPC Service\nDisk|archiv|Alt\nPrinter|hp|\n"
    assert nas.parse_shares(out) == ["archiv", "Backup"]

def test_subnet_hosts_is_slash_24_without_self():
    hosts = nas.subnet_hosts("192.168.1.30")
    assert len(hosts) == 253 and "192.168.1.30" not in hosts
    assert hosts[0] == "192.168.1.1" and hosts[-1] == "192.168.1.254"

def test_scan_and_merge():
    found = nas.scan_port_445(["192.168.1.242", "192.168.1.10", "192.168.1.9"], lambda h: h != "192.168.1.9")
    assert found == ["192.168.1.10", "192.168.1.242"]
    merged = nas.merge_discovered([{"name": "truenas", "address": "192.168.1.242"}], found,
                                  lambda ip: "pc.fritz.box" if ip == "192.168.1.10" else None)
    assert merged == [{"name": "pc.fritz.box", "address": "192.168.1.10"},
                      {"name": "truenas", "address": "192.168.1.242"}]
```

- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement; Sortierung mit `ipaddress.IPv4Address`.
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `feat(nas): discovery and share list parsing`.

---

### Task 3: Worker – Einhängen und Verwaltungsaktionen

**Files:** Modify `backend/kibm/media_worker.py`; Test `backend/tests/test_media_worker.py`

**Interfaces:**
- Consumes: Task 1/2. `nas` wird wie `partitions` geladen (relativer Import, sonst `importlib`-Fallback).
- Produces:
  - Konstanten `RUN_DIR = Path("/run/kibm-media")`, `NAS_MOUNT_PATH = RUN_DIR / "nas"`, `NAS_STATE_DIR = Path("/var/lib/kibm-media")`, `NAS_CONFIG_PATH = NAS_STATE_DIR / "nas.json"`, `NAS_CREDENTIALS_PATH = NAS_STATE_DIR / "nas-credentials"`, `NAS_MOUNT_TIMEOUT_SECONDS = 15`.
  - `MediaJob.target: str = "usb"`; `_job_data` liefert `"target"`.
  - `_nas_lock = threading.Lock()` – serialisiert alles, was `NAS_MOUNT_PATH` ein-/aushängt.
  - Austauschbare Modul-Helfer (für Tests): `_is_mounted(path: Path) -> bool`, `_avahi_output() -> str`, `_own_ipv4() -> str` (UDP-Socket `connect(("192.0.2.1", 9))` + `getsockname()`; kein Netlink, Sandbox erlaubt nur AF_UNIX/AF_INET), `_port_open(host: str) -> bool` (TCP 445, 0,4 s), `_reverse_name(ip: str) -> Optional[str]` (`socket.gethostbyaddr`, max. 1 s).
  - `@contextmanager _nas_mounted(config, credentials: Path) -> Iterator[Path]` – mkdir 0700; `ValueError`, wenn bereits eingehängt; `_run(nas.mount_args(...), timeout=15)`; `CalledProcessError`/`TimeoutExpired` → `ValueError(nas.mount_error(...))` (stderr nie roh weitergeben); `finally`: `umount --`, bei Fehler `umount --lazy --`.
  - `@contextmanager _nas_credentials(request: dict) -> Iterator[tuple[NasConfig, Path]]`:
    - ohne `server` in der Anfrage → gespeicherte Konfig + `NAS_CREDENTIALS_PATH` (sonst `ValueError("Keine NAS-Verbindung gespeichert.")`);
    - mit `server` und leerem `password` → gespeicherte Credentials nur, wenn `server, share, username, domain` gleich der gespeicherten Konfig, sonst `ValueError("Bitte das Passwort eingeben.")`;
    - sonst temporäre Datei `RUN_DIR / f"nas-cred-{secrets.token_hex(8)}"`, im `finally` gelöscht.
  - `_nas_job_busy() -> bool` – `_job` in `BUSY_STATES` und `_job.target == "nas"`.
  - Aktionen und Antworten:
    - `nas_get` → `{"configured": False}` oder `{"configured": True, "server", "share", "username": user_label(), "folder", "display"}`.
    - `nas_check` → `{"ok": True, "display", "free_bytes"}` | `{"ok": False, "error"}` | `{"ok": True, "busy": True, "display"}` (kein Einhängen bei laufendem NAS-Auftrag). Prüft Ordner (`safe_subpath`, existiert: sonst `"Sicherungsordner nicht gefunden: <folder>"`), beschreibbar (Probe-Datei `.kibm-write-test` anlegen + löschen; sonst „Keine Schreibrechte im Sicherungsordner.“).
    - `nas_browse` (`path`) → `{"path", "folders"}` – nur `entry.is_dir(follow_symlinks=False)`, ohne `.`-Namen, sortiert, ≤ 200.
    - `nas_mkdir` (`path`, `name` via `validate_segment`) → `{"path": "<path>/<name>"}` (bzw. `name` bei leerem `path`).
    - `nas_save` → einhängen, Ordner wie `nas_check` prüfen, dann `save_config`; Credentials-Datei nur neu schreiben, wenn ein Passwort übergeben wurde → `{"saved": True, "display"}`.
    - `nas_delete` → beide Dateien löschen → `{"deleted": True}`; während NAS-Auftrag abgelehnt.
    - `nas_shares` (`server`, `username`, `password`) → `{"shares"}` via `_run(["smbclient", "-g", "-L", "//" + server, "-A", cred], timeout=15)`; `NT_STATUS_LOGON_FAILURE` → Fehlertext von Mount-Fehler 13; `FileNotFoundError` → „smbclient ist nicht installiert. Installiere KIBM erneut.“; sonstiges → `mount_error(...)`.
    - `nas_discover` → `{"devices"}` = `merge_discovered(parse_avahi(_avahi_output()), scan_port_445(subnet_hosts(_own_ipv4()), _port_open), _reverse_name)`; avahi (timeout 5) und Scan parallel; Fehler von avahi ignorieren.
  - Alle `nas_*`-Aktionen außer `nas_get`/`nas_discover` laufen unter `_nas_lock`.

- [ ] **Step 1: Failing tests** – Fixture `_nas_handler(monkeypatch, tmp_path)` setzt `RUN_DIR`, `NAS_MOUNT_PATH`, `NAS_STATE_DIR`, `NAS_CONFIG_PATH`, `NAS_CREDENTIALS_PATH` auf `tmp_path`, `_job=None`, ersetzt `_run` durch einen Fake, der alle Aufrufe aufzeichnet und `mount`/`umount` über einen Merker beantwortet, den `_is_mounted` (gepatcht) liest. Tests:
  - `test_nas_save_stores_config_and_private_credentials` – Ergebnis `{"saved": True, "display": "//h/S/f"}`; `nas.json` ohne Passwort; `nas-credentials` 0600 mit Passwort; kein aufgezeichnetes Argument enthält das Passwort; `umount` aufgerufen; keine `nas-cred-*`-Datei übrig.
  - `test_nas_save_keeps_password_only_for_same_target` – leeres Passwort + gleiches Ziel → OK, Credentials-Datei unverändert; leeres Passwort + anderer Server → `{"error": "Bitte das Passwort eingeben."}`, kein `mount`.
  - `test_nas_get_without_config_reports_only_configured_false` → `[{"configured": False}]`.
  - `test_nas_check_reports_missing_folder` → `{"ok": False, "error": "Sicherungsordner nicht gefunden: f"}`, `umount` aufgerufen.
  - `test_nas_check_maps_mount_failure` – Fake-`mount` wirft `CalledProcessError(32, [...], stderr="mount error(13): Permission denied")` → `ok: False`, Text aus Task 1, kein `umount`-Fehler.
  - `test_nas_check_while_nas_job_runs_does_not_mount` → `{"ok": True, "busy": True, "display": ...}`, kein `mount`.
  - `test_nas_browse_lists_only_real_directories` – Unterordner, Datei, Symlink auf Ordner, `.versteckt` → nur Unterordner.
  - `test_nas_browse_rejects_symlink_escape` → Antwort mit `error`.
  - `test_nas_mkdir_rejects_bad_name` (`"a/b"`, `".."`) und `test_nas_mkdir_creates_folder`.
  - `test_nas_delete_removes_both_files`.
  - `test_nas_shares_parses_smbclient_output` – Fake liefert `"Disk|Backup|\n"` → `{"shares": ["Backup"]}`, keine `nas-cred-*` übrig.
  - `test_nas_discover_merges_sources` – gepatchte Helfer → erwartete Liste.
- [ ] **Step 2:** Run `uv run --extra dev pytest -q backend/tests/test_media_worker.py -k nas` → FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run gesamte Suite → PASS.
- [ ] **Step 5:** Commit `feat(nas): worker actions to connect, browse and save an SMB target`.

---

### Task 4: Worker – Backup-Pipeline mit Zielverzeichnis und `nas_start`

**Files:** Modify `backend/kibm/media_worker.py:480-648, 797-863`; Test `backend/tests/test_media_worker.py`

**Interfaces:**
- Consumes: `_nas_mounted`, `_nas_lock`, `NAS_CONFIG_PATH`, `NAS_CREDENTIALS_PATH`, `MediaJob.target` (Task 3).
- Produces:
  - `_shrink_image(job, full_path, final_path)` – Arbeitskopie `full_path.parent / f".{job.job_id}.img.part"`.
  - `_open_source() -> int` – aus `_run_job` herausgelöst (öffnet `SOURCE_DEVICE`, prüft Blockgerät, `posix_fadvise`).
  - `TARGET_TEXT: dict[str, dict[str, str]]` mit Schlüsseln `writing, verifying, done, failed`; `"usb"` = bisherige Texte unverändert; `"nas"`: „Vollständiges Karten-Image wird auf das NAS geschrieben.“, „Geschriebenes Image wird vom NAS zurückgelesen und geprüft.“, „Image ist auf dem NAS gespeichert.“, „NAS-Image konnte nicht sicher erstellt werden.“
  - `_capture_image(job: MediaJob, directory: Path, hostname: str) -> None` – bisheriger Teil von `_run_job` ab Dateiname bis Ergebnis inkl. Fehlerbehandlung, schreibt in `directory`, Texte aus `TARGET_TEXT[job.target]`.
  - `_run_job(job, device, hostname)` – USB-Prüfung/Formatieren/Platz, dann `_capture_image(job, VOLUME_PATH, hostname)`.
  - `_run_nas_job(job: MediaJob, config: NasConfig, hostname: str) -> None` – `with _nas_lock, _nas_mounted(config, NAS_CREDENTIALS_PATH) as root:` Ordner per `safe_subpath` (fehlt → `fehler` „Sicherungsordner nicht gefunden: …“), Platz via `statvfs` gegen `_usb_image_bytes(job.image_size, job.shrink)` (sonst „Auf dem NAS ist nicht genug freier Platz. Diese Auswahl benötigt rund X GiB.“), Drucker-Prüfung, dann `_capture_image`. Jeder Fehler vor `_capture_image` → `state="fehler"`.
  - Aktion `nas_start` → Handler `_nas_start(request)`: `_ensure_update_idle`; `confirmed is True` (sonst „Bestätige zuerst den sicheren Druckerstatus.“); gespeicherte Konfig; kein laufender Auftrag/Download; `MediaJob(target="nas", ...)`; Antwort `{"job", "device": {"path": config.display(), "model": "NAS (SMB)", "size_bytes": 0}}`.
  - `_download` lehnt `target == "nas"` ab: „NAS-Images liegen bereits auf dem NAS.“

- [ ] **Step 1: Failing tests**
  - `test_shrink_working_copy_lives_next_to_the_full_image` – Fake-PiShrink prüft, dass sein Argument in `full_path.parent` liegt.
  - `test_nas_job_writes_image_and_unmounts` – `_open_source` gibt FD einer Testdatei zurück, `_flush_root_journal`, `_repair_image` (→ False), `_printer_active` (→ False) gefaked → `state == "bereit"`, genau eine Datei `*-sdkarte.img` im Ordner, kein `.part`, `umount` aufgerufen.
  - `test_nas_job_failure_removes_part_and_unmounts` – `_write_all` wirft `OSError("Host is down")` → `state == "fehler"`, Meldung „NAS-Image konnte nicht sicher erstellt werden.“, kein `.part`, `umount` aufgerufen.
  - `test_nas_job_rejects_missing_folder_without_writing` → `fehler`, „Sicherungsordner nicht gefunden: f“.
  - `test_nas_job_rejects_too_little_space` – gepatchtes `os.statvfs`.
  - `test_nas_start_requires_confirmation_and_saved_config`.
  - `test_nas_start_refuses_while_usb_job_runs`.
  - `test_download_refuses_nas_job`.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement; bestehende USB-Tests bleiben unverändert grün.
- [ ] **Step 4:** Run gesamte Suite → PASS.
- [ ] **Step 5:** Commit `feat(nas): back up the system card to the saved SMB folder`.

---

### Task 5: Worker – NAS-Images auflisten, verkleinern, löschen

**Files:** Modify `backend/kibm/media_worker.py:266-269, 510-540, 717-795`; Test `backend/tests/test_media_worker.py`

**Interfaces:**
- Consumes: Task 3/4.
- Produces:
  - Aktion `nas_images` → `{"images": [{"id", "filename", "size_bytes", "shrinkability", "location": "nas"}], "display"}` (≤ 100, nur reguläre `.img`, keine `.`-Dateien) oder `{"images": [], "busy": True}` bei laufendem Auftrag. Einträge in `_listed_images` mit `"volume": {"kind": "nas", "identity": ["nas", server, share, folder]}`.
  - USB-Einträge bekommen `"location": "usb"` und `volume["kind"] = "usb"`. `_list_images` ersetzt in `_listed_images` nur `kind == "usb"`-Einträge, `nas_images` nur `kind == "nas"`.
  - `_run_existing_shrink` und `_delete_image` verzweigen nach `image["volume"]["kind"]`. NAS: unter `_nas_lock` mit gespeicherter Konfig einhängen; Identity muss zur aktuellen Konfig passen (sonst „Die NAS-Verbindung wurde geändert. Lade die Liste neu.“); dann dieselben Prüfungen wie USB (`_image_id`, regulär, shrinkable, Platz) im Ordner; Ergebnis `<stem>-<job_id>-geshrinkt.img` im selben Ordner; `MediaJob(target="nas")`; Erfolgstext „Verkleinerte Kopie ist auf dem NAS gespeichert.“

- [ ] **Step 1: Failing tests**
  - `test_nas_images_lists_images_and_unmounts` – Ordner mit `a.img`, `.x.img.part`, `notes.txt`, Unterordner `b.img/` → nur `a.img`, `location == "nas"`, `umount` aufgerufen.
  - `test_usb_image_scan_keeps_listed_nas_images` – nach `nas_images` und `images` ist die NAS-ID noch in `_listed_images`.
  - `test_delete_nas_image_removes_only_selected_file`.
  - `test_delete_nas_image_rejects_changed_connection` – Konfig nach Listing geändert → Fehlertext oben, nichts gelöscht.
  - `test_nas_images_while_job_runs_reports_busy`.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run gesamte Suite → PASS.
- [ ] **Step 5:** Commit `feat(nas): list, shrink and delete images on the NAS`.

---

### Task 6: Web-API

**Files:** Modify `backend/kibm/main.py`; Test `backend/tests/test_api.py`

**Interfaces:**
- Consumes: Worker-Aktionen aus Task 3–5; `nas.validate_config`, `nas.validate_password`, `nas.validate_folder`, `nas.validate_segment` für Vorabprüfung → HTTP 400.
- Produces:
  - `_media_request(payload: dict, timeout: float = 15) -> dict`; alle NAS-Aufrufe mit `timeout=45`.
  - Modelle: `NasConnectionRequest(server: str = "" ≤ 300, share: str = "" ≤ 300, username: str = "" ≤ 300, password: str = "" ≤ 256, folder: str = "" ≤ 1024)` – leerer `server` = gespeicherte Verbindung; `NasBrowseRequest(NasConnectionRequest)` + `path ≤ 1024 = ""`; `NasMkdirRequest(NasBrowseRequest)` + `name ≤ 80`; `NasStartRequest(shrink=False, keep_full=False, verify_image=True, confirmed: bool)`.
  - Routen (alle `def`; `test_routes_with_blocking_io_run_in_the_threadpool` muss sie abdecken):
    - `GET /api/backup/nas` – ohne Sitzung `{"configured"}`, mit Sitzung volle `nas_get`-Antwort.
    - `POST /api/backup/nas/{discover,shares,check,browse,mkdir,save,delete,images}` – `_require_csrf`, Vorabprüfung, Worker; Worker-Fehler → 409 mit Text; bei `check` wird `{"ok": False, "error"}` als 200 durchgereicht.
    - `POST /api/backup/nas/start` – `confirmed` Pflicht (400), `_ensure_update_idle`, `services.backup_preflight()` (409 bei Blockern), dann `_start_media_job(session_id, {"action": "nas_start", ...})`.
  - Shrink/Löschen vorhandener NAS-Images über bestehende `/api/backup/usb/shrink` bzw. `/api/backup/usb/images/delete`; Fortschritt über `/api/backup/usb/jobs/{id}` und `/current`.

- [ ] **Step 1: Failing tests**
  - `test_nas_get_without_session_reveals_only_configured` – Fake-Worker liefert volle Daten; ohne Login genau `{"configured": True}`.
  - `test_nas_post_routes_require_session_and_csrf` – jede Route: ohne Login 401, mit Login ohne CSRF 403.
  - `test_nas_save_rejects_invalid_input_before_worker` – `share="a/b"` → 400, Worker nicht aufgerufen.
  - `test_nas_start_requires_confirmation_and_preflight` – `confirmed=False` → 400; Preflight blockiert → 409; OK → Antwort mit `download_token` und `device.model == "NAS (SMB)"`.
  - `test_nas_save_response_never_contains_password`.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run gesamte Suite → PASS.
- [ ] **Step 5:** Commit `feat(nas): dashboard API for the SMB target`.

---

### Task 7: Dashboard

**Files:** Modify `backend/kibm/static/index.html:56-97`, `app.js`, `app.css`, `i18n.js`

**Interfaces – Consumes:** Routen aus Task 6.

**Verhalten:**
- Karte `#destination-nas` („NAS (SMB) · Sicherung“, Untertitel „Auf Netzwerkfreigabe speichern“), immer sichtbar, mit Info-Tooltip wie die anderen Karten.
- Panel `#nas-options`:
  - **Einrichten** (`#nas-setup`): Button „NAS im Netzwerk suchen“ → Treffer (Name + IP) zum Anklicken, füllt Server; Felder „IP-Adresse oder Name des NAS“ (Platzhalter `192.168.1.100`), „Freigabename“ (`Backup`), „Benutzer“, „Passwort“ (`autocomplete="new-password"`); Button „Freigaben anzeigen“ (aktiv bei Server + Benutzer + Passwort) → Freigaben zum Anklicken; „Verbinden“ → Ordnerauswahl (Pfadzeile `/ › a › b` mit klickbaren Teilen, Unterordner-Buttons, „Neuer Ordner“, „Diesen Ordner verwenden“ → `save`). Einfügen einer kompletten Adresse ins Server-Feld teilt sie (JS-Gegenstück zu `nas.split_address`).
  - **Verbunden** (`#nas-connected`): Statuszeile `#nas-status` – beim Auswählen der Karte „Verbindung wird geprüft …“, dann „✓ Verbindung OK · //…“ + freier Platz oder „✗ <Grund>“; Links „Zugangsdaten ändern“ (Felder vorbefüllt, Passwort leer mit Hinweis „Leer lassen = gespeichertes Passwort behalten“) und „Verbindung entfernen“ (Bestätigungsdialog).
  - Ohne Login: Karte zeigt „Anmelden, um das NAS einzurichten“ bzw. „Verbindung gespeichert – zum Prüfen anmelden“ und öffnet den bestehenden Login-Dialog.
- Gemeinsame Optionen (Shrink, Voll-Image, Prüfen, Druck-Bestätigung) wie USB; Formatier-Bestätigung ausgeblendet; „Image erstellen“ nur aktiv, wenn `nas-status` OK.
- Platzhinweis, wenn `free_bytes` kleiner als benötigt (gleiche Formel wie `requiredUsbCapacity`).
- Fortschritt über bestehendes `pollUsbJob`; bei `job.target === "nas"` Titel „NAS-Sicherung …“, kein „USB-Stick sicher auswerfen“, keine Download-Links, stattdessen „Gespeichert auf //…/<datei>“.
- „Vorhandene Images“: auch sichtbar bei gespeicherter NAS-Verbindung + Login; lädt beim Öffnen und per „Neu laden“ `POST /api/backup/nas/images`; Einträge mit Kennzeichnung „USB“/„NAS“; Löschen/Verkleinern wie bisher.
- Jeder neue DE-Text (inkl. Worker-Texte aus Task 1/3/4/5) bekommt einen EN-Eintrag in `i18n.js`; dynamische Texte als Regex wie die vorhandenen Muster.

- [ ] **Step 1:** Implement HTML/CSS/JS.
- [ ] **Step 2:** Lokal prüfen: Dev-Server aus `.claude/launch.json` (im Haupt-Checkout bzw. gleiches Muster) mit gefaktem Worker starten; im Browser-Pane Einrichten, Verbunden, Fehlerstatus, Fortschritt, 375 px Breite, DE/EN prüfen; Konsole ohne Fehler.
- [ ] **Step 3:** `node --check backend/kibm/static/app.js && node --check backend/kibm/static/i18n.js` → keine Ausgabe; Suite PASS.
- [ ] **Step 4:** Commit `feat(nas): dashboard card for the SMB target`.

---

### Task 8: Installer, systemd, Doku

**Files:** Modify `install.sh` (Paketliste Zeile 206, Modul-Block bei `modprobe loop`), `uninstall.sh`, `systemd/kibm-media.service`, `README.md`, `README.en.md`, `docs/api.md`, `docs/security.md`, `docs/architecture.md`

- [ ] **Step 1:** `install.sh`: `cifs-utils smbclient avahi-utils` in die Paketliste; `/etc/modules-load.d/kibm-cifs.conf` mit `cifs` + `modprobe cifs` analog zu `loop`. `uninstall.sh`: `kibm-cifs.conf` und `/var/lib/kibm-media` entfernen.
- [ ] **Step 2:** `kibm-media.service`: `StateDirectory=kibm-media`, `StateDirectoryMode=0700`, `ReadWritePaths=/run/kibm-media /var/lib/kibm-media`.
- [ ] **Step 3:** Doku: Einrichtung; Hinweis Kabel statt WLAN (Test: 78 MB/s vs. 4,6 MB/s); Groß-/Kleinschreibung des Freigabenamens; API-Routen; Sicherheitsmodell (Speicherorte, 0600, kein fstab, Einhängen nur pro Aktion im Worker-Namespace).
- [ ] **Step 4:** `bash -n install.sh && bash -n uninstall.sh`; `systemd-analyze verify systemd/kibm-media.service` falls verfügbar → keine Fehler.
- [ ] **Step 5:** Commit `feat(nas): install SMB tools and document the NAS target`.

---

### Task 9: Abnahme auf dem Pi (192.168.1.30, TrueNAS 192.168.1.242)

- [ ] Branch bzw. Beta-Release auf dem Pi installieren (Ablauf `docs/release.md`; Nutzer führt `sudo` aus).
- [ ] NAS-Suche findet 192.168.1.242; Freigabenliste zeigt `Backup`; Verbinden → `klipperbackup` → speichern; Karte zeigt grünen Haken.
- [ ] Backup mit Shrink + Voll-Image + Prüfen; geshrinktes Image auf Ersatzkarte schreiben → Pi und KIBM starten.
- [ ] Vorhandene Images: NAS-Image erscheint; Test-Image löschen funktioniert.
- [ ] Fehlerfälle: falsches Passwort → ✗ mit Text; NAS aus → ✗ „nicht erreichbar“; Pi-Neustart mit NAS aus bootet ohne Verzögerung.
- [ ] Nutzer prüft: Passwort nur in `/var/lib/kibm-media/nas-credentials`, nicht in `journalctl -u kibm -u kibm-media`.
