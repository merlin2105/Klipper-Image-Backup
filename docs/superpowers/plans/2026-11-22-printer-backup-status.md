# Backup-Fortschritt am Drucker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Der Media-Worker meldet den Fortschritt laufender Sicherungen über Moonraker an Klipper (Statuszeile + KlipperScreen-Banner).

**Architecture:** Neues Modul `backend/kibm/printer_status.py` (Text aus Job-Daten, Drosselung, Hintergrund-Thread zum Senden). `media_worker._set_job` reicht nach jeder Änderung eine Momentaufnahme an den Notifier weiter; nur der Thread spricht mit Moonraker.

**Tech Stack:** Python 3.9 (Pi), stdlib (`urllib`, `threading`, `queue`), pytest.

**Spec:** `docs/superpowers/specs/2026-11-22-printer-backup-status-design.md`

## Global Constraints
- Python 3.9 kompatibel (Pi), keine neuen Abhängigkeiten.
- Moonraker: `POST http://127.0.0.1:7125/printer/gcode/script` mit `{"script": ...}`, `GET /printer/gcode/help`; Zeitlimit 2 s; jeder Fehler wird ignoriert.
- `SET_DISPLAY_TEXT` höchstens alle 10 s oder bei Phasenwechsel; `RESPOND` bei Phasenwechsel und je 10-%-Stufe, nur wenn `RESPOND` in `/printer/gcode/help` steht (einmal pro Auftrag geprüft).
- Endzustand (`fertig`/`fehler`): beide Meldungen sofort, `SET_DISPLAY_TEXT` (leer) nach 60 s, außer ein neuer Auftrag beginnt.
- Text beginnt mit `KIBM: `, Prozent vorn (Prusa-LCD zeigt nur 20 Zeichen); `"`, `;`, `#` und Steuerzeichen werden entfernt. Größen wie die Web-UI: Basis 1024, eine Nachkommastelle, Komma, „GB“.
- `media_worker.py` läuft als Skript: Import über den bestehenden `_sibling`-Fallback.

## Review Focus
- Moonraker hängt (Timeout) → Kopieren läuft unverändert schnell weiter.
- Klipper ohne `[respond]` → kein `RESPOND`, keine „Unknown command“-Meldung.
- Zwei Aufträge kurz hintereinander → Löschen des alten Endtexts darf den neuen nicht löschen.
- Fehlermeldungen mit Sonderzeichen → Endtext ist fester Text ohne Fehlerdetails.
- Tests des Workers schicken nichts an echtes Moonraker.

---

### Task 1: `printer_status`-Modul

**Files:** Create `backend/kibm/printer_status.py`; Test `backend/tests/test_printer_status.py`

**Interfaces – Produces:**
- `status_text(job: dict) -> Optional[str]` (Eingabe wie `media_worker._job_data`)
- `class Notifier(send, has_respond, clock=time.monotonic, start_thread=True)` mit `update(job: dict) -> None` (blockiert nie) und `drain() -> None` (verarbeitet die Warteschlange synchron; für Tests)
- `moonraker_send(script: str) -> None`, `moonraker_has_respond() -> bool`

Texte:
- `sichern`: `KIBM: 35 % gesichert (10,6 von 29,8 GB)`
- `verifizieren`: `KIBM: 60 % geprüft (…)`
- `kopie`: `KIBM: 40 % Arbeitskopie (…)` (shrink_bytes/shrink_total_bytes)
- `pruefsumme`: `KIBM: 50 % Prüfsumme` (state `schrumpfen`: shrink-Felder, sonst verified_bytes/image_size)
- `dateisystem`: `KIBM: Dateisystem wird geprüft`; `verkleinern`: `KIBM: Image wird verkleinert`
- `vorbereitung`/`formatieren`: `KIBM: Sicherung startet`
- `fertig`: `KIBM: Sicherung fertig`; `fehler`: `KIBM: Sicherung fehlgeschlagen`

- [ ] Tests: Texte je Phase; Drosselung (gleiche Phase < 10 s → keine neue Zeile, ≥ 10 s → neue); RESPOND nur bei neuer 10-%-Stufe; ohne RESPOND kein RESPOND; Endzustand → Löschen nach 60 s, neuer Auftrag verhindert Löschen; Sonderzeichen entfernt; `send` wirft → nächste Meldung geht trotzdem.
- [ ] Tests laufen lassen → FAIL (Modul fehlt).
- [ ] Modul implementieren.
- [ ] `uv run --extra dev pytest -q backend/tests/test_printer_status.py` → PASS.

### Task 2: Einhängen in den Worker

**Files:** Modify `backend/kibm/media_worker.py` (Import + `_set_job`), `backend/tests/test_media_worker.py` (autouse-Fixture + Test).

- [ ] Test: `_set_job(phase=...)` gibt `_job_data(_job)` an `media_worker.PRINTER_STATUS.update`; autouse-Fixture ersetzt `PRINTER_STATUS` durch eine Attrappe.
- [ ] FAIL sehen, dann `PRINTER_STATUS = printer_status.Notifier(printer_status.moonraker_send, printer_status.moonraker_has_respond)` und Aufruf in `_set_job`.
- [ ] Ganze Suite `uv run --extra dev pytest -q backend/tests` → PASS (inkl. Skriptstart-Test).

### Task 3: Prüfung am Pi
- [ ] Nach Ja des Nutzers als Beta veröffentlichen, Sicherung starten; Nutzer bestätigt Anzeige auf KlipperScreen und LCD (Umlaute auf dem Prusa-LCD prüfen).
