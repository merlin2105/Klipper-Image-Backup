# Klipper Image Backup Manager (KIBM)

[Deutsch](README.md) · [English](README.en.md)

KIBM erstellt vollständige Images einer Klipper-System-microSD und zeigt wichtige Diagnoseinformationen des Raspberry Pi an. Das Dashboard läuft auf dem Pi und ist im lokalen Netzwerk über HTTPS erreichbar.

> **Wichtig:** KIBM befindet sich in der Beta. Ein Image einer laufenden Systemkarte ist keine zeitpunktgenaue Momentaufnahme: Dateien können sich während des Lesens ändern. USB-Sicherungen formatieren den ausdrücklich ausgewählten Stick vollständig als exFAT und löschen alle vorhandenen Daten darauf.

## Inhalt

- [Voraussetzungen](#voraussetzungen)
- [Installation auf dem Raspberry Pi](#installation-auf-dem-raspberry-pi)
- [Erster Aufruf und Anmeldung](#erster-aufruf-und-anmeldung)
- [Funktionen im Überblick](#funktionen-im-überblick)
- [Sicherheits- und Backup-Hinweise](#sicherheits--und-backup-hinweise)
- [Updates](#updates)
- [Fehlerbehebung und Administration](#fehlerbehebung-und-administration)
- [Entwicklungsstart](#entwicklungsstart)
- [Projektstatus und geplante Funktionen](#projektstatus-und-geplante-funktionen)
- [Lizenz](#lizenz)

## Voraussetzungen

- Raspberry Pi 4.
- Debian oder ein Debian-basiertes Betriebssystem. MainsailOS wird auf dem Raspberry Pi 4 unterstützt.
- Ein normales Linux-Benutzerkonto mit `sudo`-Rechten. Die Installation muss mit `sudo` aus diesem Benutzerkonto aufgerufen werden.
- Internetzugriff während der Installation, damit benötigte Pakete und Python-Abhängigkeiten geladen werden können. Für den Web-Updater wird ausgehendes HTTPS zu GitHub benötigt.
- Für eine USB-Sicherung: ein direkt am Pi angeschlossener USB-Datenträger mit ausreichend freiem Speicher.

Fluidd, Mainsail, Klipper und Moonraker werden durch KIBM weder ersetzt noch neu installiert. KIBM verwendet Moonraker lokal, wenn es den Druckstatus prüft.

## Installation auf dem Raspberry Pi

Die folgenden Befehle laden das GitHub-Repository und starten den mitgelieferten Installer. Er unterstützt derzeit Raspberry Pi 4 und Debian-basierte Systeme.

```sh
sudo apt-get update
sudo apt-get install -y git
git clone https://github.com/merlin2105/Klipper-Image-Backup.git
cd Klipper-Image-Backup
sudo ./install.sh
```

Prüfe die angezeigten Hardware-, Betriebssystem- und Datenträgerinformationen. Bestätige die Installation anschließend mit `j`. Der Installer richtet die benötigten Pakete, ein eingeschränktes KIBM-Dienstkonto, die Anmeldung über das vorhandene Linux-Konto, ein lokales HTTPS-Zertifikat und die KIBM-Systemdienste ein. Er prüft danach, ob die Dienste bereit sind.

Bei erfolgreichem Abschluss zeigt der Installer eine Adresse wie `https://<IP-Adresse-des-Pi>:8765/` an. Verwende die genaue Adresse aus der Installer-Ausgabe. Der Port 8765 ist für den Zugriff im vertrauenswürdigen lokalen Netzwerk gedacht; richte keine öffentliche Portweiterleitung ein.

## Erster Aufruf und Anmeldung

1. Öffne die vom Installer ausgegebene HTTPS-Adresse auf einem Gerät im selben Netzwerk.
2. Bestätige beim ersten Aufruf das lokale Zertifikat im Browser. Es wird vom Installer für den Pi erzeugt und ist nicht automatisch durch eine öffentliche Zertifizierungsstelle bestätigt.
3. Diagnoseinformationen sind im Dashboard sichtbar. Für Sicherungen und das Löschen eines Images authentifiziert KIBM mit dem bestehenden Linux-Konto, das die Installation aufgerufen hat.
4. Gib bei einer Aufforderung das aktuelle Linux-Passwort dieses Kontos ein. KIBM ändert oder speichert das Passwort nicht.

Oben rechts kannst du mit den Flaggen zwischen Deutsch und Englisch wechseln. KIBM merkt sich die Auswahl in diesem Browser.

## Funktionen im Überblick

### Systemdiagnose

Das Dashboard zeigt den erkannten Host, Betriebssystem, Kernel und Architektur sowie CPU-, Speicher- und Temperaturwerte an. Es listet Systemdatenträger und Partitionen samt Dateisystem, Größe und Einhängepunkt auf. Diese Diagnoseansicht liest Systeminformationen; sie bietet keine Funktion zum Ändern der Klipper-Konfiguration.

Der Moonraker-Status wird verwendet, um einen aktiven oder pausierten Druck zu erkennen. Wenn Moonraker nicht erreichbar ist, kann KIBM nicht automatisch prüfen, ob gedruckt wird. In diesem Fall ist vor einer Sicherung eine ausdrückliche Bestätigung nötig.

### 1. Vollständiges Image auf dem PC speichern

1. Wähle **PC · Vollständiges Image**.
2. Bestätige, dass kein Druck läuft oder während der Sicherung startet.
3. Melde dich bei Bedarf mit dem vorhandenen Pi-/Linux-Konto an.
4. Wähle im Browser den Speicherort für die `.img`-Datei.

KIBM liest die System-microSD bis zum Ende der letzten Partition und streamt das unkomprimierte Image an den Browser. Das Image ist damit nie größer als die genutzte Kartenaufteilung und passt auf eine Karte derselben Größe. Die Image-Datei wird dafür nicht erst auf der Pi-Systemkarte abgelegt. Der Browser speichert sie auf dem ausgewählten PC-Speicherort. Der Übertragungsstatus und die SHA-256-Prüfsumme werden nach Abschluss angezeigt. Dieser PC-Ablauf verkleinert das Image nicht und kann das Dateisystem darin nicht nachträglich reparieren, weil es direkt zum Browser fließt. Für ein Image, das zuverlässig startet, ist die USB-Sicherung der bessere Weg.

### 2. Vollständiges Image auf einem USB-Stick sichern

1. Schließe den USB-Datenträger am Raspberry Pi an und wähle **USB-Stick · Sicherung**.
2. Wähle den erkannten Datenträger anhand der angezeigten Geräteinformationen aus.
3. Prüfe, dass es der richtige Datenträger ist, und bestätige ausdrücklich die Formatierung.
4. Bestätige den sicheren Druckerstatus und melde dich bei Bedarf an.
5. Starte die Sicherung und warte, bis sie fertig ist.

**Die Formatierung als exFAT löscht alle bestehenden Daten auf dem ausgewählten USB-Datenträger.** KIBM prüft Datenträgeridentität und Speicherplatz erneut, bevor es schreibt. Optional kann es das Image nach dem Schreiben zurücklesen und anhand SHA-256 prüfen. Weil die Karte im laufenden Betrieb gelesen wird, hält KIBM vor dem Lesen das Root-Dateisystem kurz an, damit alle bis dahin gespeicherten Änderungen aus dem ext4-Journal auf der Karte stehen. Danach schließt KIBM im Image das ext4-Journal ab und repariert das Dateisystem mit `e2fsck`; die angezeigte Prüfsumme gilt für das reparierte Image. Erst dann erhält die Datei ihren Namen auf `.img`. Eine abgebrochene Sicherung hinterlässt keine Datei, die wie ein gültiges Image aussieht.

Die Option **Image nach der vollständigen Sicherung mit PiShrink verkleinern** erstellt auf demselben USB-Datenträger eine verkleinerte Kopie. Während der Verarbeitung müssen Platz für eine vollständige Arbeitskopie und das Original vorhanden sein. Wenn **Vollständiges Image zusätzlich behalten** nicht aktiviert ist, entfernt KIBM das vollständige Zwischen-Image erst nach erfolgreichem Shrink. Bei einem Fehler bleibt das vollständige Image erhalten. Eine Karte mit dem verkleinerten Image vergrößert ihre Root-Partition beim ersten Start einmalig auf die ganze Karte und startet dabei einmal neu. Für die optionale Verifikation liest KIBM das geschriebene Image erneut.

Nach Abschluss kann die Datei über den Browser heruntergeladen werden. Nutze anschließend **USB-Stick sicher auswerfen**, bevor du den Datenträger abziehst.

### 3. Ein vorhandenes USB-Image prüfen, verkleinern oder löschen

Wenn KIBM auf einem erkannten USB-Datenträger `.img`-Dateien findet, erscheint **Image auf USB-Stick**. Für die Prüfung zählt der tatsächliche Inhalt des Images, nicht sein Dateiname. KIBM liest Partitions- und Dateisysteminformationen und zeigt gefundene Images als auswählbare Kacheln.

- **Verkleinern:** Nur wenn sich das Dateisystem sicher prüfen lässt und mindestens 256 MB geschätzter Shrink-Spielraum vorhanden sind, bietet KIBM PiShrink an. Es erstellt eine neue Kopie auf demselben USB-Datenträger und behält das Original.
- **Bereits kompakt oder nicht sicher prüfbar:** KIBM bietet keinen weiteren Shrink an.
- **Löschen:** Entfernt ausschließlich die ausgewählte `.img`-Datei. Ein eigenes Bestätigungsfenster und gegebenenfalls die Pi-Konto-Anmeldung sind erforderlich. Das Löschen kann nicht rückgängig gemacht werden.

### GitHub-Update

Das Dashboard prüft GitHub auf eine neuere KIBM-Version und zeigt installierte sowie verfügbare Versionsnummer an. Mit **Auf Update prüfen** kannst du die Prüfung manuell starten. Ein verfügbares Update wird erst nach deiner Bestätigung installiert.

Während der Installation zeigt KIBM ein blockierendes Fortschrittsfenster. Lass den Raspberry Pi eingeschaltet und starte keine Sicherung, bis der Vorgang abgeschlossen ist. Danach meldet KIBM den Erfolg und bietet **Seite neu laden** an. Vor einem Update muss ein angeschlossener USB-Datenträger sicher ausgeworfen sein; ein laufender Sicherungsauftrag blockiert das Update ebenfalls.

Updates kommen ausschließlich von signierten Release-Tags (zum Beispiel `v0.3.0`), nicht vom aktuellen Stand von `main`. Der Updater lädt das bestätigte Release, prüft dessen SSH-Signatur gegen den bei der Installation hinterlegten Release-Schlüssel und führt erst dann den KIBM-Installer aus. Ein unsignierter oder fremd signierter Tag wird nicht installiert. Dafür muss der Pi GitHub über HTTPS erreichen können. Wie ein Release erstellt wird, steht in [Release erstellen](docs/release.md). Ein abgebrochener oder fehlgeschlagener Vorgang lässt sich über die unten genannten Administrationsbefehle untersuchen.

Vor einem Update sichert der Installer die zuletzt installierte KIBM-Version einschließlich ihrer Dienstkonfiguration. Wenn die neue Version die Bereitschaftsprüfung nicht besteht, stellt KIBM die vorherigen Dateien und die vorherige Python-Umgebung automatisch wieder her und prüft den Start erneut. Das Update wird dabei als fehlgeschlagen gemeldet; die Sicherung enthält keine USB-Images oder sonstigen Nutzerdaten.

## Sicherheits- und Backup-Hinweise

- **Systemkarte:** KIBM liest das Quellgerät `/dev/mmcblk0`; die normale Sicherungsfunktion schreibt nicht auf die laufende System-microSD.
- **Laufender Pi:** KIBM nimmt das Image auf, während der Pi eingeschaltet bleibt. Dateien oder Systemzustände können sich während des Lesens ändern; ein erfolgreiches Image ist keine Garantie für eine konsistente Momentaufnahme oder einen erfolgreichen Wiederherstellungsvorgang.
- **Druckstatus:** Ein von Moonraker gemeldeter aktiver oder pausierter Druck blockiert die Sicherung. Ohne Moonraker-Verbindung gibt es keine automatische Drucküberwachung; bestätige dann nur, wenn sicher kein Druck läuft oder startet.
- **USB-Ziel:** Nur der ausdrücklich ausgewählte Datenträger wird für die USB-Sicherung als exFAT formatiert. Diese Formatierung ist destruktiv und löscht dessen vorhandene Daten.
- **Passwort:** KIBM prüft das bestehende Linux-Konto über PAM. Es speichert oder ändert das Passwort nicht. Die Weboberfläche verwendet lokales HTTPS und CSRF-geschützte Aktionen.
- **Berechtigungen:** Der Webprozess läuft unprivilegiert. Authentifizierung und Medienoperationen laufen über getrennte lokale Dienste. Der Medien-Worker akzeptiert festgelegte Aktionen statt beliebiger Shell-Befehle.
- **Netzwerk:** Port 8765 nicht ins öffentliche Internet weiterleiten. Verwende KIBM im vertrauenswürdigen lokalen Netzwerk.

Weitere Details stehen in [Architektur](docs/architecture.md) und [Sicherheitsmodell](docs/security.md).

## Updates

Updates können nach der Installation im KIBM-Dashboard geprüft und nach Bestätigung installiert werden. Für den manuellen Wartungsweg über SSH stellt KIBM einen begrenzten Administrator-Befehl bereit:

```sh
sudo kibm-admin status
sudo kibm-admin logs
sudo kibm-admin usb
sudo kibm-admin restart
sudo kibm-admin update
```

`status`, `logs` und `usb` zeigen den Zustand bzw. Diagnoseinformationen. `restart` startet die KIBM-Dienste neu. `update` installiert das neueste signierte Release von GitHub. Der Wrapper akzeptiert keine beliebigen Shell-Befehle.

## Fehlerbehebung und Administration

### Dashboard nicht erreichbar

- Prüfe, ob du die HTTPS-Adresse und IP-Adresse aus der Installer-Ausgabe verwendest.
- Bestätige beim ersten Aufruf das lokale Zertifikat.
- Prüfe die Dienste mit `sudo kibm-admin status` und die Protokolle mit `sudo kibm-admin logs`.
- Stelle sicher, dass Port 8765 im lokalen Netzwerk erreichbar und nicht durch eine Firewall gesperrt ist.

### GitHub-Update wird nicht gefunden oder lässt sich nicht installieren

- Prüfe, ob der Pi ausgehende HTTPS-Verbindungen zu GitHub herstellen kann.
- Drücke **Auf Update prüfen**, um den Status erneut abzurufen.
- Wirf einen angeschlossenen USB-Datenträger in KIBM sicher aus und warte, bis alle Sicherungs- und Downloadvorgänge beendet sind.
- Prüfe `sudo kibm-admin status` und `sudo kibm-admin logs`.

### USB-Stick fehlt oder wird abgelehnt

- Prüfe die USB-Verbindung und starte **Voraussetzungen erneut prüfen**.
- `sudo kibm-admin usb` zeigt die vom Medien-Worker erkannten Geräte.
- Verwende einen Datenträger mit genügend Platz: Für einen normalen USB-Image-Ablauf wird mindestens die Größe des Karten-Images plus Reserve benötigt. Mit Shrink müssen während der Verarbeitung zwei vollständige Image-Kopien plus Reserve Platz finden.
- Sichere vorhandene Daten vor einer USB-Sicherung an einem anderen Ort. KIBM formatiert den ausgewählten Stick vor dem Schreiben.

### Unterstützung und Protokolle

Für einen Fehlerbericht sind die KIBM-Version aus dem Dashboard, der betroffene Ablauf und relevante, bereinigte Ausgabe von `sudo kibm-admin status` bzw. `sudo kibm-admin logs` hilfreich. Teile keine Passwörter, Sitzungstoken oder privaten Schlüssel.

## Entwicklungsstart

Für lokale Entwicklung wird Python 3.9 oder neuer benötigt:

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
uvicorn kibm.main:app --app-dir backend --host 127.0.0.1 --port 8765
```

Die lokale Entwicklungsinstanz benötigt keine Root-Rechte, kann aber keine USB-Operationen oder Pi-PAM-Anmeldung ohne die passenden Systemdienste ausführen.

## Projektstatus und geplante Funktionen

- Dashboard, Diagnose, lokale HTTPS-Verbindung, Pi-Konto-Anmeldung, PC-Image-Streaming, USB-Sicherung und PiShrink-Abläufe sind im Repository implementiert.
- Sicherungs- und USB-Abläufe müssen weiterhin auf der jeweiligen Pi-, Betriebssystem- und USB-Konfiguration überprüft werden.
- Ein direkter **KIBM-Button in Fluidd und Mainsail** ist für eine spätere Änderung vorgemerkt und wird aktuell nicht vom Installer eingerichtet.
- Recovery-Boot und Wiederherstellung eines Images sind nicht verfügbar. Das Recovery-Verzeichnis enthält derzeit nur Planungs- und Validierungswerkzeuge; KIBM installiert keinen Recovery-Bootloader und ändert keine Bootpartition.

## Lizenz

MIT. Siehe [LICENSE](LICENSE).
