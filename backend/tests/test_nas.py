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
    assert nas.validate_config("h", "S", "u", "").display() == "//h/S"


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


def test_validate_folder_normalises():
    assert nas.validate_folder("a//b/") == "a/b"
    assert nas.validate_folder("") == ""


@pytest.mark.parametrize("name", ["", "a/b", "..", ".", "a\nb", "x" * 81])
def test_validate_segment_rejects_bad_names(name):
    with pytest.raises(ValueError):
        nas.validate_segment(name)


@pytest.mark.parametrize("password", ["pw\nusername=root", "x" * 257, "a\x00b"])
def test_validate_password_rejects_control_chars_and_length(password):
    with pytest.raises(ValueError):
        nas.validate_password(password)


def test_validate_config_rejects_empty_user():
    with pytest.raises(ValueError):
        nas.validate_config("h", "S", "", "")


def test_safe_subpath_rejects_symlink_out_of_root(tmp_path):
    (tmp_path / "root").mkdir()
    (tmp_path / "outside").mkdir()
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
    assert args == [
        "mount", "-t", "cifs", "-o",
        f"credentials={tmp_path / 'cred'},nosuid,nodev,noexec,uid=0,gid=0,file_mode=0600,dir_mode=0700",
        "--", "//h/S", str(tmp_path / "mnt"),
    ]


UNREACHABLE = "NAS nicht erreichbar. Prüfe IP-Adresse/Name und ob das NAS eingeschaltet ist."


@pytest.mark.parametrize("stderr,expected", [
    ("mount error(13): Permission denied",
     "Anmeldung abgelehnt. Prüfe Benutzer, Passwort und die genaue Schreibweise des Freigabenamens (Groß-/Kleinschreibung)."),
    ("mount error(2): No such file or directory", "Freigabe nicht gefunden. Prüfe den Freigabenamen (Groß-/Kleinschreibung)."),
    ("mount error(112): Host is down", UNREACHABLE),
    ("mount error(113): No route to host", UNREACHABLE),
    ("mount error(115): Operation now in progress", UNREACHABLE),
    ("mount error: could not resolve address for nas: Unknown error",
     "Der Name des NAS konnte nicht aufgelöst werden. Versuche die IP-Adresse."),
    ("mount error(19): No such device",
     "Das SMB-Modul (cifs) ist nicht geladen. Starte den Pi neu oder installiere KIBM erneut."),
    ("something else\nsecond line", "Die Freigabe konnte nicht eingebunden werden (something else)."),
])
def test_mount_error_texts(stderr, expected):
    assert nas.mount_error(stderr, timed_out=False) == expected


def test_mount_error_timeout():
    assert nas.mount_error("", timed_out=True) == "Zeitüberschreitung: Das NAS hat nicht innerhalb von 15 Sekunden geantwortet."


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
