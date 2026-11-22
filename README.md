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
- Für eine NAS-Sicherung: eine SMB-Freigabe (Synology, TrueNAS, Windows u. a.) mit einem Benutzer, der dort schreiben darf; am besten ist der Pi per Kabel verbunden.

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

KIBM prüft außerdem, ob die System-microSD Fehler meldet. Dazu durchsucht es die Kernelmeldungen seit dem letzten Start nach Lese- und Zeitüberschreitungsfehlern der Karte und liest den Fehlerzähler der ext4-Dateisysteme, der auch Neustarts überdauert. Findet es etwas, wird die Statusleiste gelb und zeigt „System-microSD meldet Fehler“; die letzte Kernelmeldung erscheint als Tooltip. Eine Sicherung bleibt trotzdem möglich. Was dann zu tun ist, steht unter [Die System-microSD meldet Lesefehler](#die-system-microsd-meldet-lesefehler).

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

### 3. Vollständiges Image auf einem NAS sichern (SMB)

Bei vielen Druckern ist der Raspberry Pi samt Display fest im Gehäuse verbaut, und ein USB-Stick lässt sich kaum anschließen. Für diese Fälle schreibt KIBM das Image direkt in einen Ordner auf einer Netzwerkfreigabe: Synology, TrueNAS, QNAP, eine Windows-Freigabe oder ein anderer SMB-Server (SMB 2 oder neuer).

#### Was du vorher brauchst

- Eine Freigabe auf dem NAS, zum Beispiel `Backup`, und einen NAS-Benutzer mit **Schreibrechten** auf diese Freigabe. Gastzugang wird nicht unterstützt.
- Genug freien Platz: mindestens die Größe des Karten-Images, mit PiShrink etwa das Doppelte, weil während des Verkleinerns eine Arbeitskopie entsteht. Die Größe steht im Dashboard unter **Image der System-microSD**.
- Am besten eine **Kabelverbindung** zwischen Pi und Netzwerk. Im Test schrieb ein Pi 4 über Kabel 78 MB/s auf das NAS, über WLAN nur 4,6 MB/s. Ein 15-GB-Image dauert über Kabel wenige Minuten, über WLAN fast eine Stunde.

#### Einmalig einrichten

1. Wähle **NAS (SMB) · Sicherung** und melde dich mit deinem Pi-Konto an.
2. **NAS im Netzwerk suchen** zeigt gefundene Geräte zum Anklicken. Alternativ trägst du die IP-Adresse (z. B. `192.168.1.100`) oder den Namen des NAS von Hand ein. Eine kopierte Adresse wie `\\192.168.1.100\Backup`, `//192.168.1.100/Backup` oder `smb://192.168.1.100/Backup` wird automatisch in NAS und Freigabe aufgeteilt.
3. Gib **Benutzer** und **Passwort** des NAS-Kontos ein. Für ein Domänenkonto schreibst du `DOMÄNE\benutzer` ins Benutzerfeld.
4. **Freigaben anzeigen** listet die Freigaben des NAS zum Anklicken. Funktioniert das nicht, trägst du den **Freigabenamen** von Hand ein (siehe [Fehlerbehebung](#nas-freigaben-werden-nicht-aufgelistet)). Der Name muss exakt stimmen, auch in Groß- und Kleinschreibung: `Backup` ist nicht `backup`.
5. **Verbinden**: KIBM hängt die Freigabe kurz ein und zeigt die Ordner darauf. Den Ordner tippst du nicht ein, du klickst dich hin. Über die Pfadzeile kommst du zurück, mit **Neuer Ordner** legst du einen Ordner an.
6. **Diesen Ordner verwenden** prüft, ob KIBM dort schreiben darf, und speichert die Verbindung.

#### Sichern

Beim Anklicken der NAS-Karte prüft KIBM die Verbindung live und zeigt **✓ Verbindung OK · //NAS/Freigabe/Ordner** mit dem freien Platz, oder ein rotes ✗ mit dem Grund. Erst bei grünem Haken erscheinen die Optionen und **Image erstellen**.

Die Optionen und der Ablauf entsprechen der USB-Sicherung: Root-Journal abschließen, Karte lesen, optional mit SHA-256 prüfen, Dateisystem im Image mit `e2fsck` reparieren, optional mit PiShrink verkleinern. Auf dem NAS wird nichts formatiert. Die Dateien heißen wie bei USB, zum Beispiel `PRUSA-20261122-101500-sdkarte.img`, mit Shrink `…-sdkarte-geshrinkt.img` und, falls behalten, `…-sdkarte-voll.img`.

**Während der Sicherung wirkt der Ordner leer.** KIBM schreibt zuerst in eine versteckte Datei `.…-sdkarte.img.part` und benennt sie erst nach Prüfung und Reparatur in `….img` um. So bleibt nach einem Abbruch keine halbfertige Datei liegen, die wie ein gültiges Image aussieht. Dateien mit einem Punkt am Anfang blenden Samba-Server wie TrueNAS standardmäßig aus. Im Windows-Explorer siehst du sie unter *Ansicht → Einblenden → Ausgeblendete Elemente*, in Linux-Dateimanagern mit `Strg+H`.

#### Ändern und entfernen

Unter der Statuszeile stehen **Zugangsdaten ändern** und **Verbindung entfernen**. Lässt du beim Ändern das Passwortfeld leer, behält KIBM das gespeicherte Passwort, aber nur solange NAS, Freigabe und Benutzer gleich bleiben. Für ein anderes NAS musst du das Passwort neu eingeben. **Verbindung entfernen** löscht nur die gespeicherten Zugangsdaten auf dem Pi; die Images auf dem NAS bleiben erhalten.

#### So geht KIBM mit dem NAS um

- Die Freigabe wird nur für die Dauer einer Aktion eingebunden (Prüfen, Ordner auflisten, Sichern, Images auflisten, Löschen, Verkleinern) und danach wieder ausgehängt. KIBM ändert `/etc/fstab` nicht. Ein ausgeschaltetes NAS verzögert also keinen Systemstart.
- Das Passwort liegt nur in `/var/lib/kibm-media/nas-credentials`, und diese Datei kann nur root lesen. Es erscheint nicht in Prozesslisten, Logs oder Antworten an den Browser.
- Auf dem NAS löscht KIBM nur die Image-Datei, die du ausdrücklich ausgewählt hast, oder seine eigene versteckte `.part`-Datei nach einem Abbruch.
- Die Liste vorhandener NAS-Images fragt KIBM nur beim Laden der Seite (angemeldet), nach einer NAS-Sicherung oder über **NAS-Images neu laden** ab, nie im Hintergrund. So kann das NAS schlafen.

### 4. Ein vorhandenes Image prüfen, verkleinern oder löschen

Wenn KIBM auf einem erkannten USB-Datenträger oder im NAS-Sicherungsordner `.img`-Dateien findet, erscheint **Vorhandene Images**. NAS-Images werden nur mit Anmeldung und nur beim Laden der Seite, nach einer NAS-Sicherung oder über **NAS-Images neu laden** abgefragt, damit das NAS schlafen kann. Für die Prüfung zählt der tatsächliche Inhalt des Images, nicht sein Dateiname. KIBM liest Partitions- und Dateisysteminformationen und zeigt gefundene Images als auswählbare Kacheln.

- **Verkleinern:** Nur wenn sich das Dateisystem sicher prüfen lässt und mindestens 256 MB geschätzter Shrink-Spielraum vorhanden sind, bietet KIBM PiShrink an. Es erstellt eine neue Kopie am selben Speicherort und behält das Original.
- **Bereits kompakt oder nicht sicher prüfbar:** KIBM bietet keinen weiteren Shrink an.
- **Löschen:** Entfernt ausschließlich die ausgewählte `.img`-Datei. Ein eigenes Bestätigungsfenster und gegebenenfalls die Pi-Konto-Anmeldung sind erforderlich. Das Löschen kann nicht rückgängig gemacht werden.

### Sicherung abbrechen

Eine laufende Sicherung auf USB oder NAS und das Verkleinern eines Images lassen sich unter dem Fortschrittsbalken mit **Vorgang abbrechen** beenden. KIBM stoppt in jeder Phase, auch während e2fsck oder PiShrink laufen, und löscht die unvollständige Image-Datei oder Arbeitskopie. Ältere Sicherungen bleiben unverändert. Wird während des Verkleinerns abgebrochen, bleibt das bereits fertige und geprüfte vollständige Image erhalten.

### Fortschritt am Drucker

Während einer Sicherung auf USB oder NAS und beim Verkleinern meldet KIBM den Fortschritt auch an Klipper, zum Beispiel `KIBM: 35 % gesichert (10,6 von 29,8 GB)`:

- **Drucker-Display und Mainsail/Fluidd:** Statuszeile über `SET_DISPLAY_TEXT`, höchstens alle 10 Sekunden aktualisiert. Eine Minute nach dem Ende wird sie wieder geleert.
- **KlipperScreen:** kurze Einblendung bei jedem Schritt und alle 10 %. Dafür muss `[respond]` in der Klipper-Konfiguration aktiv sein; fehlt es, schickt KIBM nur die Statuszeile. Diese Meldungen erscheinen auch in der Konsole von Mainsail/Fluidd.

Ist Moonraker nicht erreichbar, läuft die Sicherung ohne Anzeige am Drucker normal weiter. Beim Speichern auf dem PC über den Browser gibt es keine Anzeige am Drucker.

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
- **NAS-Zugangsdaten:** Das NAS-Passwort liegt nur in `/var/lib/kibm-media/nas-credentials` (nur root lesbar) und wird nie wieder an den Browser gesendet. Die Freigabe wird nur während einer Aktion eingebunden; KIBM löscht auf dem NAS nur die ausdrücklich ausgewählte `.img`-Datei.
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

### NAS-Freigaben werden nicht aufgelistet

**Freigaben anzeigen** fragt das NAS mit dem Programm `smbclient` nach seinen Freigaben. Das kann aus mehreren Gründen nicht klappen. Die Sicherung selbst braucht die Liste nie: Trag den Freigabenamen von Hand ein und klicke **Verbinden**.

- **Meldung „Die Freigabenliste ist auf diesem System nicht verfügbar (smbclient fehlt)“:** `smbclient` ist nicht installiert. Der Installer richtet es mit ein, behandelt es aber als optional, damit ein Update nicht daran scheitert. Häufigster Grund ist ein Betriebssystem, dessen Support ausgelaufen ist, zum Beispiel Debian 11 (bullseye, Grundlage älterer MainsailOS-Versionen). Der Debian-Sicherheitsserver bietet dann die neueste Samba-Version nicht mehr an (Fehler `404 Not Found`), während die Paketliste noch auf sie verweist. Ab KIBM 0.2.10b10 versucht der Installer in diesem Fall die passende Version aus dem normalen Debian-Archiv. Von Hand geht das so:

  ```bash
  sudo apt-get update
  sudo apt-get install -t bullseye smbclient
  ```

  `bullseye` ersetzt du durch den Namen deiner Debian-Version (`grep VERSION_CODENAME /etc/os-release`). Danach **Freigaben anzeigen** erneut klicken; ein Neustart von KIBM ist nicht nötig.
- **„Anmeldung abgelehnt“:** Benutzer oder Passwort stimmen nicht, oder das Konto darf sich per SMB nicht anmelden. Bei TrueNAS muss beim Benutzer *Samba Authentication* aktiv sein; wurde der Benutzer vor dem SMB-Dienst angelegt, hilft es oft, das Passwort einmal neu zu setzen. Manche NAS verweigern die Freigabenliste für Konten ohne Administratorrechte. Das Einhängen einer bekannten Freigabe klappt dann trotzdem.
- **Die gewünschte Freigabe fehlt in der Liste:** Freigaben, die im NAS als nicht sichtbar markiert sind (TrueNAS *Browsable to Network Clients* aus, bei Synology die Option zum Verbergen in der Netzwerkumgebung), und versteckte Freigaben mit `$` am Ende zeigt KIBM nicht an. Mit dem richtigen Namen von Hand eingetragen funktionieren sie normal.
- **„NAS nicht erreichbar“ oder Zeitüberschreitung:** IP-Adresse prüfen, NAS eingeschaltet und im selben Netz? Eine Firewall muss TCP-Port 445 vom Pi zulassen.

### NAS-Sicherung: weitere Probleme

- **„Anmeldung abgelehnt“ beim Verbinden:** zuerst die genaue Schreibweise des Freigabenamens prüfen, auch Groß- und Kleinschreibung. Danach Benutzer, Passwort und die Rechte des Benutzers auf der Freigabe (bei TrueNAS die ACL des Datasets).
- **„Freigabe nicht gefunden“:** Der Freigabename existiert so nicht. Häufig ist es ein Tippfehler oder die falsche Groß- und Kleinschreibung.
- **„Der Name des NAS konnte nicht aufgelöst werden“:** Trag statt des Namens die IP-Adresse ein. Am besten gibst du dem NAS im Router eine feste Adresse.
- **„Keine Schreibrechte im Sicherungsordner“:** Der NAS-Benutzer darf in diesem Ordner nicht schreiben. Rechte im NAS anpassen oder einen anderen Ordner wählen.
- **NAS im Netzwerk suchen findet nichts:** Die Suche fragt mDNS/Bonjour ab (nur wenn `avahi-utils` installiert ist) und prüft im eigenen /24-Netz des Pi, welche Geräte TCP-Port 445 öffnen. Ein NAS in einem anderen Netz oder hinter einer Firewall findet sie nicht. Gib die IP-Adresse dann von Hand ein.
- **Die Sicherung ist sehr langsam:** Prüfe, ob der Pi per WLAN statt per Kabel verbunden ist (`ip -br addr` zeigt, ob `eth0` eine Adresse hat). Bei aktiver Kabelverbindung bevorzugt der Pi das Kabel automatisch.
- **Der Ordner ist während der Sicherung leer:** Das ist normal, siehe oben. Die versteckte `.part`-Datei wird erst am Ende zur `.img`-Datei.

### Die System-microSD meldet Lesefehler

Bricht eine Sicherung mit „Die System-microSD meldet bei … GB einen Lesefehler“ ab oder zeigt die Statusleiste „System-microSD meldet Fehler“, ist die Karte sehr wahrscheinlich defekt. Billige oder alte Karten gehen nach Jahren Dauerbetrieb oft so kaputt. Typische Begleiterscheinungen: SSH bricht sofort ab, Logs lassen sich nicht mehr lesen, Dienste starten nicht.

1. **Keine weitere Sicherung starten** und möglichst wenig auf dem Pi verändern. Jeder Zugriff kann die Karte weiter beschädigen.
2. **Pi ausschalten**, die Karte in einen Kartenleser am PC stecken und mit `ddrescue` ein Abbild ziehen. Anders als `dd` überspringt es defekte Bereiche und versucht sie später erneut. Die Gerätedatei vorher mit `lsblk` prüfen:
   ```bash
   sudo ddrescue -d -r3 /dev/sdX klipper-sd.img klipper-sd.map
   ```
3. Das Abbild auf eine **neue, hochwertige Karte** schreiben und die Dateisysteme mit `fsck` prüfen. Alternativ Klipper neu aufsetzen und nur `printer_data/config` aus dem Abbild übernehmen.

Meldet eine Sicherung dagegen „Ein-/Ausgabefehler beim Zugriff auf das Sicherungsziel“, liegt das Problem beim NAS oder USB-Stick, nicht bei der Karte.

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
