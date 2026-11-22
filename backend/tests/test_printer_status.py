import threading

import pytest

from kibm import printer_status

GIB = 1024 ** 3


def job(phase, state=None, job_id="j1", **values):
    data = {
        "job_id": job_id, "state": state or phase, "phase": phase, "image_size_bytes": int(29.8 * GIB),
        "sent_bytes": 0, "verified_bytes": 0, "shrink_bytes": 0, "shrink_total_bytes": 0, "target": "nas",
    }
    data.update(values)
    return data


@pytest.mark.parametrize("data,expected", [
    (job("sichern", sent_bytes=int(10.6 * GIB)), "KIBM: 35 % gesichert (10,6 von 29,8 GB)"),
    (job("verifizieren", verified_bytes=int(17.9 * GIB)), "KIBM: 60 % geprüft (17,9 von 29,8 GB)"),
    (job("kopie", state="schrumpfen", shrink_bytes=4 * GIB, shrink_total_bytes=8 * GIB),
     "KIBM: 50 % Arbeitskopie (4,0 von 8,0 GB)"),
    (job("pruefsumme", state="schrumpfen", shrink_bytes=1 * GIB, shrink_total_bytes=4 * GIB), "KIBM: 25 % Prüfsumme"),
    (job("pruefsumme", state="reparieren", verified_bytes=15 * GIB), "KIBM: 50 % Prüfsumme"),
    (job("dateisystem"), "KIBM: Dateisystem wird geprüft"),
    (job("verkleinern"), "KIBM: Image wird verkleinert"),
    (job("vorbereitung", state="wird vorbereitet"), "KIBM: Sicherung startet"),
    (job("formatieren"), "KIBM: Sicherung startet"),
    (job("fertig", state="bereit"), "KIBM: Sicherung fertig"),
    (job("fehler"), "KIBM: Sicherung fehlgeschlagen"),
    (job("abgebrochen"), "KIBM: Sicherung abgebrochen"),
    (job("unbekannt"), None),
    (job("sichern", image_size_bytes=0), "KIBM: 0 % gesichert (0,0 von 0,0 GB)"),
])
def test_status_text(data, expected):
    assert printer_status.status_text(data) == expected


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def make(has_respond=True):
    sent = []
    clock = Clock()
    notifier = printer_status.Notifier(sent.append, lambda: has_respond, clock=clock, start_thread=False)
    return notifier, sent, clock


def displays(sent):
    return [script for script in sent if script.startswith("SET_DISPLAY_TEXT")]


def responds(sent):
    return [script for script in sent if script.startswith("RESPOND")]


def test_phase_change_sends_display_and_respond():
    notifier, sent, _ = make()
    notifier.update(job("sichern", sent_bytes=0))
    notifier.drain()
    assert sent == [
        'SET_DISPLAY_TEXT MSG="KIBM: 0 % gesichert (0,0 von 29,8 GB)"',
        'RESPOND MSG="KIBM: 0 % gesichert (0,0 von 29,8 GB)"',
    ]


def test_display_is_throttled_to_ten_seconds():
    notifier, sent, clock = make()
    notifier.update(job("sichern", sent_bytes=0))
    clock.now += 5
    notifier.update(job("sichern", sent_bytes=GIB))
    notifier.drain()
    assert len(displays(sent)) == 1
    clock.now += 5
    notifier.update(job("sichern", sent_bytes=2 * GIB))
    notifier.drain()
    assert len(displays(sent)) == 2


def test_respond_only_on_new_ten_percent_step():
    notifier, sent, clock = make()
    for percent in (0, 5, 9, 10, 15, 21):
        clock.now += 30
        notifier.update(job("sichern", sent_bytes=int(29.8 * GIB * percent / 100) + 1))
    notifier.drain()
    assert [script.split(": ")[1].split(" %")[0] for script in responds(sent)] == ["0", "10", "21"]


def test_no_respond_without_respond_command():
    notifier, sent, _ = make(has_respond=False)
    notifier.update(job("sichern"))
    notifier.update(job("fertig", state="bereit"))
    notifier.drain()
    assert responds(sent) == [] and len(displays(sent)) == 2


def test_respond_support_is_checked_once_per_job():
    calls = []

    def has_respond():
        calls.append(1)
        return True

    notifier = printer_status.Notifier(lambda script: None, has_respond, clock=Clock(), start_thread=False)
    notifier.update(job("sichern"))
    notifier.update(job("verifizieren"))
    notifier.update(job("sichern", job_id="j2"))
    notifier.drain()
    assert len(calls) == 2


def test_finished_job_clears_display_after_a_minute():
    notifier, sent, clock = make()
    notifier.update(job("fertig", state="bereit"))
    notifier.drain()
    assert sent[-1] == 'RESPOND MSG="KIBM: Sicherung fertig"'
    clock.now += 59
    notifier.drain()
    assert "SET_DISPLAY_TEXT" not in sent
    clock.now += 2
    notifier.drain()
    assert sent[-1] == "SET_DISPLAY_TEXT"
    clock.now += 120
    notifier.drain()
    assert sent.count("SET_DISPLAY_TEXT") == 1


def test_new_job_cancels_pending_clear():
    notifier, sent, clock = make()
    notifier.update(job("fertig", state="bereit"))
    clock.now += 30
    notifier.update(job("sichern", job_id="j2"))
    clock.now += 60
    notifier.drain()
    assert "SET_DISPLAY_TEXT" not in sent


def test_repeated_final_state_is_sent_once():
    notifier, sent, clock = make()
    notifier.update(job("fertig", state="bereit"))
    clock.now += 20
    notifier.update(job("fertig", state="bereit"))
    notifier.drain()
    assert len(sent) == 2


def test_quotes_and_comment_characters_are_removed():
    assert printer_status.gcode_text('a"b;c#d\ne\x7ff') == "abcdef"


def test_failing_send_does_not_stop_later_messages():
    sent = []

    def send(script):
        if not sent:
            sent.append("boom")
            raise OSError("moonraker down")
        sent.append(script)

    notifier = printer_status.Notifier(send, lambda: False, clock=Clock(), start_thread=False)
    notifier.update(job("sichern"))
    notifier.update(job("verifizieren"))
    notifier.drain()
    assert sent[-1].startswith('SET_DISPLAY_TEXT MSG="KIBM: 0 % geprüft')


def test_has_respond_failure_means_no_respond():
    def broken():
        raise OSError("down")

    sent = []
    notifier = printer_status.Notifier(sent.append, broken, clock=Clock(), start_thread=False)
    notifier.update(job("sichern"))
    notifier.drain()
    assert responds(sent) == [] and len(displays(sent)) == 1


def test_background_thread_sends():
    done = threading.Event()
    sent = []

    def send(script):
        sent.append(script)
        done.set()

    notifier = printer_status.Notifier(send, lambda: False)
    notifier.update(job("sichern"))
    assert done.wait(2)
    assert sent[0].startswith("SET_DISPLAY_TEXT")


def test_cancelled_job_clears_display_after_a_minute():
    notifier, sent, clock = make()
    notifier.update(job("abgebrochen"))
    clock.now += 61
    notifier.drain()
    assert sent[0] == 'SET_DISPLAY_TEXT MSG="KIBM: Sicherung abgebrochen"'
    assert sent[-1] == "SET_DISPLAY_TEXT"
