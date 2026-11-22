# NAS-Sicherung per SMB – Design

Stand: 2026-11-21 · Status: Entwurf zur Freigabe

## Warum

Bei vielen 3D-Druckern ist der Raspberry Pi samt Display fest im Gehäuse verbaut. Einen USB-Stick
einzustecken ist dort praktisch unmöglich. Für diese Nutzer ist die USB-Sicherung keine Option; die
NAS-Sicherung wird ihr eigentlicher Sicherungsweg. Sie muss deshalb alles können, was die
USB-Sicherung kann, und vollständig aus dem Dashboard bedienbar sein, ohne SSH.

## Ziele

- Neues Sicherungsziel „NAS (SMB)“ neben PC, USB-Stick und vorhandenen Images.
- Einmalige Einrichtung im Dashboard: Server, Freigabe, Benutzer, Passwort → „Verbinden“ →
  Sicherungsordner auf der Freigabe wählen (auch neuen Ordner anlegen) → „Speichern“.
- Danach: Karte anklicken → KIBM prüft die Verbindung live und zeigt „✓ Verbindung OK · //server/freigabe/ordner“
  oder ein rotes ✗ mit Grund. Gleiche Optionen wie USB: Shrink, Voll-Image zusätzlich behalten,
  Image prüfen (SHA-256), Druck-Bestätigung, „Image erstellen“.
- Gleicher Ablauf ab dem Kopieren wie USB: Journal-Flush, Kopie, Prüfen, `e2fsck`-Reparatur, Shrink,
  keine halbfertigen `.img`-Dateien.
- Images im NAS-Ordner erscheinen unter „Vorhandene Images“ mit Löschen und nachträglichem Shrink.
- „Zugangsdaten ändern“ und „Verbindung entfernen“.

## Nicht-Ziele

- Kein Zeitplan, keine automatischen Sicherungen (ausdrücklich nur manuell per Knopf).
- Keine automatische Löschung alter Backups.
- Kein Herunterladen von NAS-Images über den Browser (die Dateien liegen ja schon auf dem NAS).
- Keine Auflistung der Freigaben eines Servers (Freigabename wird eingetippt, siehe Ansätze).
- Kein NFS, keine anderen Protokolle.
- Mehrere NAS-Ziele: nein, genau eine gespeicherte Verbindung.

## Anti-Ziele (wäre ein Fehlschlag, auch wenn es technisch läuft)

- KIBM ändert `/etc/fstab` oder hinterlässt eine dauerhafte Einbindung – ein nicht erreichbares NAS
  darf nie den Bootvorgang oder andere Dienste aufhalten.
- Das gespeicherte Passwort gelangt zurück in den Browser, in Logs, in die Prozessliste oder in
  API-Antworten.
- Eingaben aus dem Formular können Mount-Optionen oder Pfade außerhalb des gewählten Ordners beeinflussen.
- KIBM löscht auf dem NAS etwas anderes als die einzelne, ausdrücklich ausgewählte `.img`-Datei
  oder die eigene versteckte `.part`-Datei eines abgebrochenen Laufs.

## Rahmenbedingungen

- Web-Dienst `kibm` läuft ohne Rechte; alles Privilegierte macht der Root-Worker `kibm-media` über
  den lokalen Unix-Socket (nur UID `kibm`, Anfragen ≤ 4096 Byte, JSON-Zeile).
- `kibm-media.service` ist abgeschottet (`ProtectSystem=strict`, `PrivateTmp`, eigener Mount-Namespace,
  `RestrictAddressFamilies=AF_UNIX AF_INET`, `ProtectKernelModules=true`). Einhängungen des Workers
  sind nur in seinem Namespace sichtbar – gewollt.
- `cifs-utils` (`mount.cifs`) ist auf MainsailOS vorhanden; `install.sh` muss es für andere Systeme
  in die Paketliste (Zeile 206) aufnehmen. Kein `smbclient`.
- Zielsystem Debian 11 (bullseye), Kernel 6.1, Pi 4; Images ~8–32 GB, Netzwerk typischerweise
  100 MBit–1 GBit. Ein Backup mit Shrink über das Netz dauert deutlich länger als auf USB.
- Nur ein Medien-Auftrag gleichzeitig (gemeinsames `_job` im Worker) – USB und NAS schließen sich aus.
- Alle Dashboard-Texte zweisprachig (DE/EN, `i18n.js`).

## Ansätze

1. **Worker hängt die Freigabe nur für die Dauer einer Aktion ein (gewählt).** Für Verbinden,
   Ordner listen, Status, Backup, Image-Liste, Löschen und Shrink hängt der Worker die Freigabe in
   seinem Namespace ein und danach wieder aus. Bestehende USB-Pipeline wird wiederverwendet.
   Kein Einfluss auf Boot oder andere Dienste.
2. **Feste Einbindung über `/etc/fstab` / systemd-Mount-Unit.** Verworfen: ein nicht erreichbares NAS
   hält den Boot auf (auf dem Pi des Nutzers bereits beobachtet: „A start job is running for
   /mnt/synology-backup … 1min 48s“), KIBM würde die Systemkonfiguration ändern, die Freigabe wäre
   für alle Prozesse sichtbar.
3. **Upload per `smbclient` ohne Einhängen.** Verworfen: `e2fsck`-Reparatur und PiShrink brauchen die
   Datei als Block-/Loop-Gerät; ohne Einhängen müsste das Image lokal zwischengespeichert werden,
   dafür fehlt auf dem Pi der Platz. Shrink war ein ausdrücklicher Wunsch.

Der Nutzer hat Ansatz 1 gewählt und akzeptiert, dass der Freigabename eingetippt wird
(Freigaben-Liste bräuchte `smbclient`).

## Was der Nutzer festgelegt hat

- Nur manuell per Knopf, kein Zeitplan.
- Erste Nutzung: Zugangsdaten eingeben → bei erfolgreicher Einbindung Ordner wählen → gespeichert.
- Folgende Nutzungen: direkt Optionen wie beim USB-Image (Voll-Image, Shrink, Prüfen …).
- Beim Anklicken der NAS-Karte sichtbarer Verbindungsstatus mit grünem Haken.
- Abgesegnet (vom Assistenten vorgeschlagen): roter Status mit Grund + „Zugangsdaten ändern“;
  NAS-Images in „Vorhandene Images“ mit Löschen/Shrink; „Verbindung entfernen“; Speichern der
  Zugangsdaten nur mit Pi-Konto-Anmeldung; Passwort wird nie wieder angezeigt; auf dem NAS wird nichts
  formatiert, die Formatier-Bestätigung entfällt.

## Entwurf

### Bedienung (Dashboard)

```
[ PC ]  [ USB-Stick ]  [ NAS (SMB) ]  [ Vorhandene Images ]

Erstes Mal:
  Server [192.168.1.100]  Freigabe [Backup]  Benutzer [..]  Passwort [••••]  [Verbinden]
  ✓ Verbunden. Ordner wählen:  /  ›  klipper  ›  [prusa]   [Neuer Ordner]   [Speichern]

Danach:
  ✓ Verbindung OK · //192.168.1.100/Backup/klipper/prusa      Zugangsdaten ändern · Verbindung entfernen
  ☐ PiShrink   ☐ Voll-Image behalten   ☑ Prüfen   ☐ Kein Druck läuft …   [Image erstellen]
```

- **NAS im Netzwerk suchen** (Nutzerwunsch): Button „NAS im Netzwerk suchen“ listet gefundene Geräte
  (Name + IP) zum Anklicken; danach nur Benutzer + Passwort → „Verbinden“ → KIBM listet die Freigaben
  des Geräts zum Anklicken → Ordner wählen → „Speichern“. Suche auf zwei Wegen parallel:
  mDNS/Bonjour `_smb._tcp` über den vorhandenen `avahi-daemon` (`avahi-browse -rpt`, Paket `avahi-utils`)
  und ein TCP-Verbindungstest auf Port 445 im eigenen IPv4-Netz des Pi (nur /24 oder kleiner, parallel,
  kurzer Timeout, insgesamt ≤ ~5 s). Namen aus mDNS, sonst Reverse-DNS, sonst nur IP. Freigabenliste per
  `smbclient -L //<ip> -A <credentials-datei> -g` (Paket `smbclient`), nur Freigaben vom Typ Disk, ohne
  versteckte `$`-Freigaben. „Manuell eingeben“ bleibt als Alternative (andere Netzbereiche, mDNS aus).
- **Manuelle Eingabe in vier einzelnen Feldern** (Nutzerwunsch): „IP-Adresse oder Name des NAS“, „Freigabename“, „Benutzer“,
  „Passwort“, jeweils mit Beispiel als Platzhalter (`192.168.1.100`, `Backup`). Niemand muss eine
  SMB-Adresse wie `//server/freigabe` kennen; KIBM setzt sie selbst aus den Feldern zusammen und zeigt
  sie nur zur Information an. Eigene Entscheidung des Assistenten: fügt jemand doch eine komplette Adresse
  ins Server-Feld ein (`\\192.168.1.100\Backup`, `//192.168.1.100/Backup`, `smb://192.168.1.100/Backup`),
  teilt das Dashboard sie in Server und Freigabe auf, statt einen Fehler zu zeigen.
- Die NAS-Karte ist immer sichtbar (auch ohne USB-Stick).
- Ohne Anmeldung zeigt die Karte nur, ob eine Verbindung gespeichert ist (`configured: true/false`),
  sonst nichts – auch keine Server-, Freigabe-, Benutzer- oder Dateinamen. Suchen, Verbinden, Ordner,
  Speichern, Status-Prüfung, NAS-Image-Liste und Backup verlangen die Pi-Konto-Sitzung
  (bestehender Login-Dialog). *Entscheidung des Assistenten.*
- „Zugangsdaten ändern“ füllt Server/Freigabe/Benutzer/Ordner vor. Ein leeres Passwortfeld heißt
  „gespeichertes Passwort behalten“ – **nur** solange Server, Freigabe und Benutzer unverändert sind;
  sonst muss das Passwort neu eingegeben werden, damit es nie an einen anderen Host geht.
  *Entscheidung des Assistenten.*
- Kein eigenes Domänenfeld: Heim-NAS nutzen lokale Konten. Wer eine Domäne braucht, schreibt
  `DOMÄNE\benutzer` ins Benutzerfeld; KIBM trennt das für die Credentials-Datei. *Entscheidung des Assistenten.*
- Fortschritt, Ergebnisliste und Fehleranzeige wie bei USB; „USB-Stick sicher auswerfen“ entfällt.

### Speicherung der Verbindung (Worker)

- `kibm-media.service` bekommt `StateDirectory=kibm-media` (`/var/lib/kibm-media`, Modus 0700, root).
- `nas.json` (0600): `{"server": "...", "share": "...", "username": "...", "domain": "", "folder": "klipper/prusa"}`.
- `nas-credentials` (0600, Format von `mount.cifs credentials=`): `username=…`, `password=…`, ggf. `domain=…`.
  Das Passwort steht nur hier; es geht nie in Mount-Optionen, Logs oder Antworten.
- Schreiben atomar (temporäre Datei + `os.replace`). „Verbindung entfernen“ löscht beide Dateien.
- Noch nicht gespeicherte Zugangsdaten (beim ersten Verbinden/Ordnerwählen) schreibt der Worker pro
  Aktion in eine temporäre 0600-Datei unter `/run/kibm-media` und löscht sie danach.

### Eingabeprüfung (im Worker, Web-Dienst prüft zusätzlich vorab)

- Server: IPv4-Adresse oder Hostname (`[A-Za-z0-9.-]`, ≤ 253 Zeichen); kein `/`, `\`, `,`, `=`, Leerzeichen.
- Freigabe: 1–80 Zeichen, keine `/ \ , = ` Steuerzeichen.
- Benutzer/Domain/Passwort: keine Zeilenumbrüche/Steuerzeichen (Credentials-Datei ist zeilenbasiert);
  Passwort ≤ 256 Zeichen. Leerer Benutzer = Gast nicht unterstützt.
- Ordner: relativer Pfad aus Segmenten ohne `.`/`..`, ohne führenden `/`, keine Steuerzeichen,
  ≤ 8 Ebenen; aufgelöster Pfad muss unterhalb des Einhängepunkts liegen; Symlinks werden nicht verfolgt.

### Einhängen

- Einhängepunkt `/run/kibm-media/nas` (eigener Pfad, unabhängig vom USB-`VOLUME_PATH`).
- `mount -t cifs //<server>/<share> /run/kibm-media/nas -o credentials=<datei>,nosuid,nodev,noexec,uid=0,gid=0,file_mode=0600,dir_mode=0700`
  mit Timeout; SMB-Version handelt der Kernel aus (Standard ≥ 2.1). Danach immer `umount` im `finally`.
- Fehlermeldungen von `mount.cifs` werden in verständliche Texte übersetzt (Server nicht erreichbar,
  Anmeldung abgelehnt, Freigabe nicht gefunden, Zeitüberschreitung); das Passwort taucht darin nie auf.

### Worker-Aktionen (Socket)

| Aktion | Zweck |
|---|---|
| `nas_get` | gespeicherte Einstellungen ohne Passwort, `configured: bool` |
| `nas_check` | gespeicherte Verbindung einhängen, Ordner prüfen (existiert, beschreibbar), freien Platz melden, aushängen |
| `nas_browse` | mit übergebenen oder gespeicherten Zugangsdaten einhängen, Unterordner eines relativen Pfads auflisten (nur Verzeichnisse, max. 200) |
| `nas_mkdir` | einen Unterordner anlegen |
| `nas_save` | Verbindung testen, Ordner prüfen, dann speichern |
| `nas_delete` | Verbindung entfernen |
| `nas_start` | Backup ins gespeicherte NAS-Ziel starten (Optionen wie `start`) |

- Die 4096-Byte-Grenze pro Anfrage reicht für alle Felder.
| `nas_discover` | NAS im Netz suchen (mDNS + Port-445-Test), Liste aus Name + IP |
| `nas_shares` | mit übergebenen Zugangsdaten die Disk-Freigaben eines Servers auflisten |
| `nas_images` | gespeicherte Verbindung einhängen, `.img`-Dateien des Ordners mit Shrink-Einschätzung liefern |

- **NAS-Images nicht im 10-s-Abfrageintervall:** Die bestehende Aktion `images` (ohne Login, alle 10 s
  von jedem offenen Dashboard abgefragt) bleibt reine USB-Liste. NAS-Images liefert die eigene Aktion
  `nas_images`, nur mit Sitzung und nur, wenn der Nutzer „Vorhandene Images“ öffnet oder dort „Neu laden“
  drückt, sowie nach einem eigenen NAS-Auftrag. Ergebnis wird bis dahin gecacht. So schläft das NAS,
  und ein nicht erreichbares NAS bremst die USB-Liste nicht. Läuft ohne `_scan_lock` des USB-Scans.
  *Entscheidung des Assistenten.*
- `shrink_existing` und `delete_image` akzeptieren auch IDs aus `nas_images`. Image-IDs schließen die
  NAS-Identität (server/share/folder) ein.

### Backup-Ablauf

`_run_job` wird so aufgeteilt, dass das Ziel austauschbar ist: USB = formatieren + exFAT einhängen;
NAS = Freigabe einhängen + in den Ordner wechseln. Ab da identisch: Platzprüfung per `statvfs`
(mit Shrink: Platz für zwei Images), `os.sync()`, `_flush_root_journal()`, Kopie in versteckte
`.part`-Datei, optional SHA-256-Prüfung, `_repair_image` (Loop-Gerät auf der Datei auf dem NAS),
Umbenennen, optional PiShrink, am Ende aushängen. Dateinamen wie bei USB
(`<host>-<zeit>-sdkarte[-voll|-geshrinkt].img`). Bei Fehlern: `.part` löschen, vollständiges Image
behalten, falls schon fertig.

### Web-API (alle mit Sitzung + CSRF, außer GET ohne Geheimnisse)

- `GET /api/backup/nas` → ohne Sitzung nur `{configured}`; mit Sitzung zusätzlich
  `{server, share, username, folder}` (nie das Passwort).
- `POST /api/backup/nas/discover`, `/shares`, `/check`, `/browse`, `/mkdir`, `/save`, `/delete`,
  `/images`, `/start` → leiten an die
  Worker-Aktionen weiter. `start` verlangt `confirmed` wie USB, aber keine Formatier-Bestätigung,
  und dieselbe Preflight-Prüfung (`backup_available`, Update läuft nicht).
- Status/Fortschritt über die bestehenden USB-Job-Endpunkte (`/api/backup/usb/current`, Job-Status).

### Installer und Doku

- `install.sh`: `cifs-utils`, `smbclient`, `avahi-utils` in die Paketliste; `cifs`-Modul per
  `modules-load.d` wie `kibm-loop.conf`. `uninstall.sh` entfernt `/var/lib/kibm-media`.
- `systemd/kibm-media.service`: `StateDirectory=kibm-media`, `ReadWritePaths` um `/var/lib/kibm-media`
  ergänzen, falls nötig; Adressfamilien/Kernelmodul siehe Hardware-Prüfung.
- README (DE/EN), `docs/security.md`, `docs/api.md`, `docs/architecture.md` ergänzen.

## Reihenfolge: erst Hardware-Spike

Bevor UI oder API gebaut werden, prüft ein kurzer Wegwerf-Test auf dem Pi des Nutzers (.237) mit seiner
Synology: `mount -t cifs` im Worker-Sandbox, Loop-Gerät + `e2fsck` + PiShrink auf einer Image-Datei auf
der Freigabe, `avahi-browse` und `smbclient -L`. Der Nutzer führt die nötigen `sudo`-Befehle aus.
Fallback, falls Reparatur/Shrink auf der Freigabe nicht zuverlässig laufen: siehe Abschnitt
„Entscheidung des Nutzers zum Fallback“ unten.

## Entscheidung des Nutzers zum Fallback

- Laufen `e2fsck`/PiShrink auf einer Datei auf der Freigabe im Spike nicht zuverlässig, wird die
  NAS-Funktion **nicht** gebaut (keine abgespeckte Variante „nur Voll-Image ohne Reparatur“). Dann
  Ergebnis des Spikes berichten und einen anderen Weg besprechen.

## Ergebnis Hardware-Spike (2026-11-22, Pi 4, TrueNAS 192.168.1.242, Kabel 1 GBit)

Ausgeführt mit `systemd-run` im exakten Sandbox-Profil von `kibm-media.service`:

- `mount -t cifs` mit `credentials=`-Datei klappt im Sandbox (SMB 3.1.1 ausgehandelt, Passwort erscheint
  nicht in `/proc/mounts`). `RestrictAddressFamilies=AF_UNIX AF_INET` reicht bei IP-Adresse; Hostname
  ungetestet. `cifs`-Modul war bereits geladen – `modules-load.d` bleibt trotzdem nötig.
- Tempo: Schreiben 78 MB/s, Lesen 99 MB/s (über WLAN vorher 4,6 MB/s).
- Loop-Gerät auf CIFS-Datei, `mkfs.ext4`, `e2fsck -fy` (rc 0, 1 s) und PiShrink (1200 → 366 MB in 24 s,
  danach `e2fsck -n` sauber) laufen zuverlässig auf der Freigabe. **Fallback-Bedingung nicht erfüllt →
  NAS-Funktion wird gebaut.**
- TrueNAS unterscheidet Groß-/Kleinschreibung beim Freigabenamen (`backup` → error 13, `Backup` → OK).
  Fehlertext für error 13 muss „Freigabename oder Zugangsdaten falsch“ nennen.
- `smbclient` und `avahi-utils` waren nicht installiert → Freigabenliste und NAS-Suche erst nach
  Installation auf Hardware prüfen.

## Offen für den Umsetzer / auf Hardware zu prüfen

- Ob `mount -t cifs` im Worker-Sandbox funktioniert: Laden des `cifs`-Kernelmoduls trotz
  `ProtectKernelModules=true` (sonst `/etc/modules-load.d/kibm-cifs.conf` per Installer), und ob
  `mount.cifs` für Hostnamen-Auflösung weitere Adressfamilien (`AF_INET6`, `AF_NETLINK`) braucht.
- Ob `losetup`/`e2fsck` und PiShrink auf einer Datei auf CIFS zuverlässig laufen (Geschwindigkeit,
  `cache=`-Option), Test mit echtem NAS (Synology des Nutzers).
- Sinnvolle Timeouts für Einhängen/Status (Vorschlag: 15 s) und Verhalten bei Netzabbruch während der
  Kopie (Fehler → `.part` löschen).
- Exakte Fehlertext-Zuordnung der `mount.cifs`-Rückgaben.
