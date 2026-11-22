# Backup-Fortschritt am Drucker (KlipperScreen / Drucker-Display)

## Warum
Während einer Sicherung soll man am Drucker sehen, wie weit sie ist, ohne die
Weboberfläche zu öffnen. Eine Zeile mit denselben Informationen wie in der
Weboberfläche reicht (Phase, Prozent, „10,6 GB von 29,8 GB“).

## Ziele
- Fortschritt von USB- und NAS-Sicherungen sowie vom nachträglichen
  Verkleinern am Drucker anzeigen.
- KlipperScreen: kurze Einblendung bei jedem Phasenwechsel und alle 10 %.
- Drucker-Display (z. B. Prusa-LCD) und Mainsail/Fluidd: dauerhafte Statuszeile.
- Ende: „fertig“ oder „fehlgeschlagen“, Statuszeile nach etwa 60 s wieder leer.

## Nicht-Ziele / Anti-Ziele
- Keine eigene KlipperScreen-Seite, keine Änderung an KlipperScreen.
- Kein Dialogfenster (`action:prompt`), weil es sich bei jeder Aktualisierung
  neu aufbaut und auch Mainsail blockiert.
- Download im Browser („Image herunterladen“) bleibt außen vor.
- Die Anzeige darf eine Sicherung nie verlangsamen oder scheitern lassen.

## Ansätze
- **A (gewählt):** `RESPOND MSG=…` → KlipperScreen blendet `echo:`-Meldungen
  als Banner ein; dazu `SET_DISPLAY_TEXT` für Display und Mainsail.
  Kosten: etwa 15 Konsolenzeilen pro Sicherung, auf KlipperScreen kein stehender Balken.
- B: `action:prompt`-Dialog. Verworfen: baut sich neu auf, erscheint auch in Mainsail.
- C: eigene KlipperScreen-Seite. Verworfen: stört KlipperScreen-Updates,
  braucht eigenen Zugang zu KIBM.

## Entwurf
- Der Root-Worker (`media_worker.py`) schickt die Befehle über Moonraker
  `POST http://127.0.0.1:7125/printer/gcode/script` (wie die bestehende
  Druckstatus-Abfrage, ohne Anmeldung, Zeitlimit 2 s).
- Senden in einem eigenen Hintergrund-Thread, damit Kopieren und Prüfen nie
  auf Moonraker warten. Fehler (Moonraker aus, API-Schlüssel nötig) werden
  still ignoriert.
- `SET_DISPLAY_TEXT` höchstens alle 10 s und bei Phasenwechsel.
- `RESPOND` nur, wenn Klipper den Befehl kennt (`[respond]` ist bei vielen
  nicht aktiv; sonst würde KlipperScreen „Unknown command“ melden). Einmal pro
  Auftrag über `/printer/gcode/help` prüfen.
- Texte beginnen mit „KIBM:“, z. B. `KIBM: NAS-Backup 40 % – 11,9 von 29,8 GB`,
  `KIBM: Prüfen 60 %`, `KIBM: Sicherung fertig`. Anführungszeichen und
  Steuerzeichen werden aus dem Text entfernt.

## Dem Umsetzer überlassen
Genaue Formulierung je Phase, Aufbau des Throttlings, Tests mit einem
gefälschten Moonraker-Aufruf.
