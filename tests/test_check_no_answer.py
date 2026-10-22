"""
A will-executor that does not answer the check in time (CHANGELOG #147).

Owner's case: divinaly received the will (the push answered "thx"), but
its searchtx took 5 s and more; the check gave up after 8 s, retried,
gave up again, and the will read "not received", no longer sent.

* the check makes ONE attempt of 15 s (the whole step still 20 s): a
  will-executor that answers in 10 s is now verified;
* no answer in time is ``Willexecutors.NO_ANSWER``: the item stays sent
  (PUSHED, no CHECK_FAIL) and the next check asks again;
* a will-executor that answers without our transaction is still "not
  received", as before;
* the Building Will dialog says "received, no answer to the check (15 s)"
  and counts it as received; the Check button (window) and the command
  line keep the item sent as well.

Three fake will-executors (``*.test``); nothing reaches a real server.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \
        python3 tests/test_check_no_answer.py
"""

import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from PyQt6.QtWidgets import QApplication
from test_build_report import _dialog, _item, _steps

from bal.cli.controller import BalController
from bal.core import willexecutors as we_mod
from bal.core.will import Will
from bal.core.willexecutors import Willexecutors
from bal.gui.qt import window as window_mod
from bal.gui.qt.widgets import BuildReportView

# Kept at module level: an unreferenced application object is destroyed at
# once, and creating a widget afterwards aborts Python.
_APP = QApplication.instance() or QApplication(sys.argv)

WE0 = "https://we0.test"
WE1 = "https://we1.test"
WE2 = "https://we2.test"


def _parallel(answers, dialog_or_items):
    """A check_transactions_parallel answering from ``answers``.

    ``answers``: ``{wid: "same" | "other" | "no_answer" | "deadline"}``:
    our tx, another tx, every attempt timed out (on_each with NO_ANSWER),
    the global deadline elapsed (on_timeout).
    """
    def items():
        if hasattr(dialog_or_items, "bal_window"):
            return dialog_or_items.bal_window.willitems
        return dialog_or_items

    def fake(targets, *, on_each=None, on_timeout=None, on_tick=None, **kw):
        for wid, url in targets:
            answer = answers[wid]
            if answer == "same":
                on_each(wid, url, {"tx": str(items()[wid].tx)}, None)
            elif answer == "other":
                on_each(wid, url, {"tx": "x"}, None)
            elif answer == "no_answer":
                on_each(wid, url, Willexecutors.NO_ANSWER, None)
        for wid, url in targets:
            if answers[wid] == "deadline":
                on_timeout(wid, url)

    return fake


# ------------------------------------------------------------ the request

def test_one_attempt_of_15_s_within_the_20_s_step():
    assert Willexecutors.CHECK_TIMEOUT == 15
    assert Willexecutors.CHECK_MAX_RETRIES == 0
    assert Willexecutors.CHECK_TIMEOUT < Willexecutors.CHECK_GLOBAL_DEADLINE == 20
    seen = {}

    def fake_send(method, url, data=None, **kw):
        seen.update(kw, url=url)
        return {"tx": "t"}

    with patch.object(Willexecutors, "send_request", side_effect=fake_send):
        Willexecutors.check_transaction("abc", WE0)
    assert seen["url"] == WE0 + "/searchtx"
    assert seen["timeout"] == 15 and seen["max_retries"] == 0


def _slow_executor(answer_after):
    """Electrum's send_http_on_proxy for a will-executor answering after
    ``answer_after`` seconds: a shorter timeout raises TimeoutError (no
    real waiting)."""
    calls = []

    def send_http_on_proxy(method, url, *, timeout, **kw):
        calls.append(timeout)
        if timeout < answer_after:
            raise TimeoutError()
        return {"tx": "the tx"}

    send_http_on_proxy.calls = calls
    return send_http_on_proxy


def test_an_executor_answering_in_10_s_is_now_verified():
    slow = _slow_executor(10)
    with patch.object(we_mod.Network, "get_instance", return_value=object()), \
            patch.object(we_mod.Network, "send_http_on_proxy", side_effect=slow):
        # Before #147: two attempts of 8 s, both cut, and no answer.
        before = Willexecutors.check_transaction(
            "abc", WE0, timeout=8, max_retries=1, retry_sleep=0)
        assert before is Willexecutors.NO_ANSWER and slow.calls == [8, 8]
        slow.calls.clear()
        # Now: one attempt of 15 s, the answer arrives.
        assert Willexecutors.check_transaction("abc", WE0) == {"tx": "the tx"}
        assert slow.calls == [15]


def test_an_executor_that_never_answers_gives_no_answer_not_none():
    slow = _slow_executor(60)
    with patch.object(we_mod.Network, "get_instance", return_value=object()), \
            patch.object(we_mod.Network, "send_http_on_proxy", side_effect=slow):
        assert Willexecutors.check_transaction("abc", WE0) is Willexecutors.NO_ANSWER


def test_an_answer_that_is_not_a_dict_is_still_none():
    with patch.object(Willexecutors, "send_request", return_value="nope"):
        assert Willexecutors.check_transaction("abc", WE0) is None


def test_no_answer_is_false_and_named():
    assert not Willexecutors.NO_ANSWER
    assert repr(Willexecutors.NO_ANSWER) == "NO_ANSWER"


# --------------------------------------------------------- the will item

def test_no_answer_leaves_the_item_sent_and_to_check_again():
    w = _item("COMPLETE", "PUSHED", url=WE0)
    assert w.set_check_willexecutor(Willexecutors.NO_ANSWER) is False
    assert w.get_status("PUSHED")
    assert not w.get_status("CHECK_FAIL") and not w.get_status("CHECKED")
    assert Will.needs_server_check(w)


def test_an_answer_without_our_tx_is_still_not_received():
    w = _item("COMPLETE", "PUSHED", url=WE0)
    w.set_check_willexecutor({"tx": "another"})
    assert w.get_status("CHECK_FAIL") and not w.get_status("PUSHED")
    w = _item("COMPLETE", "PUSHED", url=WE0)
    w.set_check_willexecutor(None)
    assert w.get_status("CHECK_FAIL") and not w.get_status("PUSHED")


# ------------------------------------------------- the Building Will dialog

def test_dialog_no_answer_is_received_not_verified():
    d = _dialog({
        "a": _item("COMPLETE", "PUSHED", url=WE0),
        "b": _item("COMPLETE", "PUSHED", url=WE1),
        "c": _item("COMPLETE", "PUSHED", url=WE2),
    })
    fake = _parallel({"a": "same", "b": "no_answer", "c": "deadline"}, d)
    with patch.object(Willexecutors, "check_transactions_parallel", fake):
        d.loop_verify()
    items = d.bal_window.willitems
    assert items["a"].get_status("CHECKED")
    for wid in ("b", "c"):
        assert items[wid].get_status("PUSHED"), wid
        assert not items[wid].get_status("CHECK_FAIL"), wid
    assert d._report().executors() == {
        WE0: ("verified", None),
        WE1: ("no_check_answer", 15),     # the 15 s of the request
        WE2: ("no_check_answer", 20),     # the 20 s of the whole step
    }
    assert "Timeout - no answer" in " ".join(d.labels)
    d._finish_report()
    # The final word keeps the live "no answer"; the sending counts it as
    # received (3/3); the verification is a warning, not an error.
    assert d._report().executors()[WE1] == ("no_check_answer", 15)
    assert _steps(d)["verify"] == ("warn", "1/3")


def test_dialog_no_answer_only_is_a_warning_not_an_error():
    d = _dialog({
        "a": _item("COMPLETE", "PUSHED", url=WE0),
        "b": _item("COMPLETE", "PUSHED", url=WE1),
        "c": _item("COMPLETE", "PUSHED", url=WE2),
    })
    fake = _parallel({"a": "no_answer", "b": "no_answer", "c": "deadline"}, d)
    with patch.object(Willexecutors, "check_transactions_parallel", fake):
        d.loop_verify()
    d._finish_report()
    assert _steps(d)["verify"] == ("warn", "0/3")


def test_dialog_a_different_tx_is_still_not_received():
    d = _dialog({
        "a": _item("COMPLETE", "PUSHED", url=WE0),
        "b": _item("COMPLETE", "PUSHED", url=WE1),
        "c": _item("COMPLETE", "PUSHED", url=WE2),
    })
    fake = _parallel({"a": "other", "b": "no_answer", "c": "same"}, d)
    with patch.object(Willexecutors, "check_transactions_parallel", fake):
        d.loop_verify()
    assert d._report().executors() == {
        WE0: ("failed", None), WE1: ("no_check_answer", 15),
        WE2: ("verified", None)}
    d._finish_report()
    assert _steps(d)["verify"] == ("warn", "1/3")


def test_dialog_the_sending_counts_no_answer_as_received():
    d = _dialog({
        "a": _item("COMPLETE", "PUSHED", url=WE0),
        "b": _item("COMPLETE", "PUSHED", "CHECKED", url=WE1),
        "c": _item("COMPLETE", "PUSHED", url=WE2),
    })
    d._report().set_step("send", "ok")
    d._report().set_executor(WE0, "no_check_answer", 15)
    d._report().set_executor(WE2, "no_check_answer", 20)
    d._merge_executors_and_send_note()
    assert _steps(d)["send"] == ("ok", "3/3")


def test_the_box_text():
    assert BuildReportView.executor_text("no_check_answer", 15) == (
        "received, no answer to the check (15 s)")
    assert BuildReportView.executor_text("no_check_answer") == (
        "received, no answer to the check")
    assert "no_check_answer" not in BuildReportView.FAILED_STATES


# ------------------------------------------- the Check button and the CLI

def test_check_button_deadline_keeps_the_item_sent(monkeypatch):
    will = {
        "a": _item("COMPLETE", "PUSHED", url=WE0),
        "b": _item("COMPLETE", "PUSHED", url=WE1),
        "c": _item("COMPLETE", "PUSHED", url=WE2),
    }
    host = MagicMock()
    monkeypatch.setattr(window_mod.time, "sleep", lambda s: None)
    fake = _parallel({"a": "deadline", "b": "other", "c": "same"}, will)
    with patch.object(Willexecutors, "check_transactions_parallel", fake):
        window_mod.BalWindow.check_transactions_task(host, will)
    assert will["a"].get_status("PUSHED") and not will["a"].get_status("CHECK_FAIL")
    assert will["b"].get_status("CHECK_FAIL") and not will["b"].get_status("PUSHED")
    assert will["c"].get_status("CHECKED")


def test_cli_check_deadline_keeps_the_item_sent():
    willitems = {
        "a": _item("COMPLETE", "PUSHED", url=WE0),
        "b": _item("COMPLETE", "PUSHED", url=WE1),
        "c": _item("COMPLETE", "PUSHED", url=WE2),
    }
    # #178: the controller asks only the will-executors selected now, as the
    # real one holds them (``BalController.willexecutors``).
    host = SimpleNamespace(
        willitems=willitems, save_willitems=lambda: None,
        willexecutors={url: {"url": url, "selected": True} for url in (WE0, WE1, WE2)})
    fake = _parallel({"a": "deadline", "b": "no_answer", "c": "other"}, willitems)
    with patch.object(Willexecutors, "check_transactions_parallel", fake):
        out = BalController.check_transactions(host)
    for wid in ("a", "b"):
        assert out[wid]["pushed"] and not out[wid]["check_fail"], wid
        assert not out[wid]["checked"], wid
    assert out["c"]["check_fail"] and not out["c"]["pushed"]


if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([__file__, "-q"]))
