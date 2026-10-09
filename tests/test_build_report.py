"""
The Building Will dialog's report: steps bar, result, boxes (CHANGELOG #84b).

* ``BuildReport`` (core): steps, boxes, will-executors, snapshot;
* ``heir_shares``: the heirs box (amount and percentage);
* ``BalBuildWillDialog``: the existing status rows feed the report (the
  steps' states, what changed, warnings, problems), the log keeps every row
  with its time, the ending fills the result sentence;
* ``BuildReportView``: draws a snapshot;
* after Close only the still-active warning is repeated in a popup.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \
        python3 tests/test_build_report.py
"""

import os
import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from electrum.transaction import Transaction
from PyQt6.QtWidgets import QApplication, QLabel

from bal.core.build_report import BuildReport, heir_shares
from bal.core.will import WillItem
from bal.gui.qt.dialogs import BalBuildWillDialog
from bal.gui.qt.widgets import BuildReportView

# Kept at module level: an unreferenced application object is destroyed at
# once, and creating a widget afterwards aborts Python.
_APP = QApplication.instance() or QApplication(sys.argv)

# WillItem parses a raw tx and calls add_info_from_wallet; no live wallet here.
_patcher = patch.object(Transaction, "add_info_from_wallet")
_patcher.start()

_TX_HEX = (
    "01000000012a5c9a94fcde98f5581cd00162c60a13936ceb75389ea65b"
    "f38633b424eb4031000000006c493046022100a82bbc57a0136751e543"
    "3f41cf000b3f1a99c6744775e76ec764fb78c54ee100022100f9e80b7d"
    "e89de861dc6fb0c1429d5da72c2b6b2ee2406bc9bfb1beedd729d98501"
    "2102e61d176da16edd1d258a200ad9759ef63adf8e14cd97f53227bae3"
    "5cdb84d2f6ffffffff0140420f00000000001976a914230ac37834073a"
    "42146f11ef8414ae929feaafc388ac00000000"
)
DELIVERY = int(datetime(2028, 9, 29, tzinfo=timezone.utc).timestamp())
HEIRS = {
    "Erede 1": ["tb1qalice", "20%", "4y", 300_000],
    "pocket manuel": ["tb1qbob", "20%", "4y", 100_000],
    'w!ll3x3c"https://we0.test': ["tb1qwe", 1000, "4y", 1000],
    "message": ["OP_RETURN:68656c6c6f", "0", "4y", 0],
}


def _item(*statuses, url="https://we0.test"):
    item = WillItem({
        "tx": _TX_HEX, "heirs": dict(HEIRS), "baltx_fees": 10,
        "willexecutor": {"url": url, "base_fee": 1000},
    })
    item.tx.locktime = DELIVERY
    for s in statuses:
        item.set_status(s, True)
    return item


def _dialog(willitems=None):
    """A BalBuildWillDialog without Qt set-up (like the scenario harness)."""
    d = BalBuildWillDialog.__new__(BalBuildWillDialog)
    d.labels = []
    d.updatemessage = MagicMock()
    d.check_row = d.inval_row = d.build_row = d.sign_row = d.push_row = None
    d.bal_window = MagicMock()
    d.bal_window.willitems = willitems or {}
    d.bal_window.heirs = {"Erede 1": [], "pocket manuel": []}
    d.bal_window.will_settings = {"locktime": "4y", "threshold": "-180d"}
    d.bal_window.bal_plugin.is_basic_mode.return_value = False
    d.bal_window._still_active_for_summary.return_value = []
    return d


def _steps(d):
    return {s: (state, note) for s, state, note in d._report().snapshot()["steps"]}


# ------------------------------------------------------------------ model

def test_model_steps_boxes_and_snapshot():
    r = BuildReport()
    assert [s[1] for s in r.snapshot()["steps"]] == ["todo"] * 5
    r.set_step("check", "ok")
    r.set_step("prepare", "ok")
    r.interrupt("sign")
    assert r.step("check") == ("ok", "ok")
    assert r.step("prepare") == ("ok", "updated")
    assert r.step("sign") == ("warn", "interrupted") and r.interrupted() == {"sign"}
    r.put("changes", 1, "fee changed")
    r.put("changes", 1, "fee changed again")        # same key: replaced
    r.put("warnings", 2, "careful")
    r.put("warnings", 2, None)                      # removed
    snap = r.snapshot()
    assert snap["boxes"] == {"changes": ["fee changed again"], "warnings": [],
                             "problems": []}
    r.reset()
    assert r.snapshot()["boxes"]["changes"] == [] and not r.interrupted()


def test_model_executors_keep_a_live_timeout():
    r = BuildReport()
    r.set_executor("https://a.test", "timeout", 20)
    r.set_executor("https://b.test", "received")
    r.merge_executors({"https://a.test": "failed", "https://b.test": "to_verify",
                       "https://c.test": "verified"})
    assert r.executors() == {
        "https://a.test": ("timeout", 20),
        "https://b.test": ("to_verify", None),
        "https://c.test": ("verified", None),
    }


def test_heir_shares():
    shares = heir_shares({"a": _item("COMPLETE")})
    assert shares == [("Erede 1", 300_000, 75.0), ("pocket manuel", 100_000, 25.0)]
    assert heir_shares({"a": _item("COMPLETE", "REPLACED")}) == []   # not valid


# ----------------------------------------------- dialog rows -> report

def test_status_rows_feed_the_steps_and_boxes():
    d = _dialog()
    d.msg_set_status("Checking variables", None, d.msg_ok())
    assert _steps(d)["check"] == ("ok", "ok")
    d.msg_set_checking()                                       # Waiting
    assert _steps(d)["check"][0] == "run"
    assert d._report().snapshot()["result"] == ("run", "Checking your will…")
    d.msg_set_checking("Miner fee rate changed: a new will must be prepared.")
    assert _steps(d)["check"] == ("ok", "ok")
    assert d._report().snapshot()["boxes"]["changes"] == [
        "Miner fee rate changed: a new will must be prepared."]
    d.msg_set_building()
    assert _steps(d)["prepare"][0] == "run"
    d.msg_set_building(d.msg_ok())
    assert _steps(d)["prepare"] == ("ok", "updated")
    d.msg_set_signing("Aborted")
    assert _steps(d)["sign"] == ("warn", "interrupted")
    d.msg_set_pushing("Nothing to do")
    assert _steps(d)["send"] == ("skip", "not needed")


def test_errors_and_warnings_go_to_their_boxes():
    d = _dialog()
    d.msg_set_status("Checking variables", None, "Stopped", d.COLOR_ERROR)
    assert _steps(d)["check"][0] == "err"
    d.msg_edit_row(d.msg_error("The Check Alive date has passed."))
    d.msg_edit_row(d.msg_alert("The percentages add up to 40%."))
    d._log_only(d.msg_error("we0.test : Ko"))                 # log only
    boxes = d._report().snapshot()["boxes"]
    assert boxes["problems"] == ["The Check Alive date has passed."]
    assert boxes["warnings"] == ["The percentages add up to 40%."]
    assert len(d.labels) == 4


def test_a_row_updated_moves_between_boxes():
    d = _dialog()
    row = d.msg_set_building(d.msg_alert("could not build"))
    assert d._report().snapshot()["boxes"]["warnings"] == ["could not build"]
    d.msg_set_building(d.msg_ok())
    assert d._report().snapshot()["boxes"]["warnings"] == []
    assert row is None                       # msg_set_building returns nothing


def test_log_keeps_a_time_for_every_row():
    d = _dialog()
    d.msg_edit_row("a")
    row = d.msg_edit_row("b")
    d.msg_edit_row("c")
    d.msg_del_row(row)
    assert d.labels == ["a", "c"] and len(d._row_times()) == 2
    assert all(len(t) == 8 and t[2] == ":" for t in d._row_times())


def test_status_class():
    d = _dialog()
    assert d._status_class(d.msg_ok())[0] == "ok"
    assert d._status_class(d.msg_error("x"))[0] == "err"
    assert d._status_class(d.msg_warning("x"))[0] == "warn"
    assert d._status_class(d.msg_alert("x")) == ("warn", "x")
    assert d._status_class("Wait")[0] == "run"
    assert d._status_class("Broadcasting 1/2 (3s / 20s)")[0] == "run"
    assert d._status_class("2/2 (4s)")[0] == "run"
    assert d._status_class("Nothing to do")[0] == "skip"
    assert d._status_class("Aborted")[0] == "abort"
    assert d._status_class("<b>Moved one day earlier</b>") == (
        "text", "Moved one day earlier")


# --------------------------------------------------------------- endings

def test_finish_all_ok_nothing_to_do():
    d = _dialog({"a": _item("COMPLETE", "PUSHED", "CHECKED")})
    d.msg_set_status("Checking variables", None, d.msg_ok())
    d.msg_set_checking(d.msg_ok())
    d.msg_set_signing("Nothing to do")
    d.msg_set_pushing("Nothing to do")
    d._finish_report()
    steps = _steps(d)
    assert steps["prepare"] == ("skip", "not needed")
    assert steps["verify"] == ("ok", "1/1")
    snap = d._report().snapshot()
    assert snap["result"][0] == "ok"
    assert snap["result"][1].startswith("<b>Your will is in force</b>")
    assert snap["executors"] == [("https://we0.test", ("verified", None))]
    assert snap["heirs"] is None and snap["will_now"] is None


def test_finish_rebuilt_and_sent_shows_heirs_and_counts():
    d = _dialog({"a": _item("COMPLETE", "PUSHED"),
                 "b": _item("COMPLETE", "PUSH_FAIL", url="https://we1.test")})
    d.msg_set_checking("Miner fee rate changed")
    d.msg_set_building(d.msg_ok())
    d.msg_set_signing(d.msg_ok())
    d._report().set_executor("https://we1.test", "timeout", 20)
    d.msg_set_pushing(d.msg_ok())
    d._finish_report()
    steps = _steps(d)
    assert steps["send"] == ("warn", "1/2")
    assert steps["verify"] == ("todo", "to do")
    snap = d._report().snapshot()
    assert [h[0] for h in snap["heirs"]] == ["Erede 1", "pocket manuel"]
    assert dict(snap["executors"])["https://we1.test"] == ("timeout", 20)
    assert snap["result"][0] == "warn"                # summary: not received


def test_finish_error_and_cancel_say_what_happened():
    d = _dialog({"a": _item("COMPLETE", "PUSHED", "CHECKED")})
    d.msg_set_building(d.msg_error("boom"))
    d._finish_report()
    snap = d._report().snapshot()
    assert snap["result"] == ("err", "<b>The new will was not prepared.</b>")
    assert snap["will_now"].startswith("Your will is in force")
    assert snap["boxes"]["problems"] == ["boom"]

    d = _dialog({"a": _item("COMPLETE", "PUSHED", "CHECKED")})
    d.msg_set_invalidating("Aborted")
    d._finish_report()
    snap = d._report().snapshot()
    assert snap["result"][0] == "warn"
    assert "was not invalidated" in snap["result"][1]
    assert _steps(d)["sign"][0] == "todo"            # not marked "not needed"


# ------------------------------------------------------------------ view

def test_view_draws_the_snapshot():
    r = BuildReport()
    r.set_step("check", "ok")
    r.set_result("ok", "<b>All fine</b>")
    r.put("changes", 1, "fee changed")
    r.set_heirs([("Erede 1", 300_000, 75.0)])
    r.set_executor("https://we.divinaly.support", "timeout", 20)
    view = BuildReportView(lambda sats: "{} mBTC".format(sats / 100_000))
    view.render(r.snapshot())
    texts = [w.text() for w in view.findChildren(QLabel)]
    assert "What changed" in texts and "fee changed" in texts
    assert "3.0 mBTC  (75%)" in texts
    assert "we.divinaly.support" in texts
    assert "✖ no answer (20 s)" in texts
    view.render(BuildReport().snapshot())               # redraw: old widgets gone
    assert "fee changed" not in [w.text() for w in view.findChildren(QLabel)
                                 if w.parent() is not None and not w.isHidden()]


def test_real_dialog_draws_and_opens_the_log_on_error():
    """The real Qt dialog: report above, log closed, opened by an error."""
    from PyQt6.QtWidgets import QWidget

    bw = _dialog({"a": _item("COMPLETE", "PUSHED", "CHECKED")}).bal_window
    bw.bal_plugin.read_file.return_value = b""
    bw.window.format_amount_and_units.side_effect = lambda sats: "{} sat".format(sats)
    parent = QWidget()
    d = BalBuildWillDialog(bw, parent=parent)
    try:
        assert d.message_label.isHidden() and not d.log_toggle.isChecked()
        assert d.log_toggle.text() == "▸ Details (log)"
        d.msg_set_status("Checking variables", None, d.msg_ok())
        texts = [w.text() for w in d.report_view.findChildren(QLabel)]
        assert "✔  Checking" in texts
        assert "Checking variables" in d.message_label.text()      # the log
        d.msg_set_building(d.msg_error("boom"))
        d._finish_report()
        assert d.log_toggle.isChecked() and not d.message_label.isHidden()
        assert d.log_toggle.text() == "▾ Details (log)"
        d.log_toggle.setChecked(False)                    # the owner closes it
        d.msg_edit_row("later row")
        assert not d.log_toggle.isChecked()               # stays closed
    finally:
        d.thread.stop()
        d.deleteLater()


def test_dialog_height_follows_content_within_the_screen():
    """Owner: too short on a notebook. Opens tall enough, grows, never past
    85% of the screen, never shrinks while open."""
    from PyQt6.QtWidgets import QWidget

    bw = _dialog({"a": _item("COMPLETE", "PUSHED", "CHECKED")}).bal_window
    bw.bal_plugin.read_file.return_value = b""
    bw.window.format_amount_and_units.side_effect = lambda sats: "{} sat".format(sats)
    d = BalBuildWillDialog(bw, parent=QWidget())
    try:
        limit = max(int(d.screen().availableGeometry().height()
                        * d.MAX_SCREEN_SHARE), 300)
        d.msg_set_status("Checking variables", None, d.msg_ok())
        first = d.height()
        assert first >= min(d.OPEN_HEIGHT, limit) and first <= limit
        for i in range(40):                     # a lot of content
            d.msg_edit_row(d.msg_alert("warning number {}".format(i)))
        d._add_close_button()
        assert first <= d.height() <= limit
        tall = d.height()
        d._report().reset()                     # less content: no shrinking
        d.msg_update()
        assert d.height() == tall
    finally:
        d.thread.stop()
        d.deleteLater()


def test_signature_step_runs_while_the_password_is_asked():
    """Owner's screenshot: Signature stayed grey during the password prompt."""
    d = _dialog({"a": _item()})
    d._date_was_anticipated = False
    d._invalidation_broadcast = False
    d.thread = MagicMock()
    d.bal_window.bal_plugin.is_basic_mode.return_value = True      # auto-sign
    seen = {}

    def ask(message, parent=None):
        seen["step"] = _steps(d)["sign"]
        seen["result"] = d._report().snapshot()["result"]
        return False                                                # Cancel

    d.bal_window.get_wallet_password.side_effect = ask
    d._on_success_phase1_body((True, None))
    assert seen["step"] == ("run", "")
    assert seen["result"] == ("run", "Signing your will…")
    assert _steps(d)["sign"] == ("warn", "interrupted")


def test_password_window_has_electrums_icon():
    """Owner's decision: Electrum's own password window shows Electrum's icon."""
    from bal.gui.qt import plugin as plugin_mod

    with patch.object(plugin_mod, "PasswordDialog") as dialog_cls, \
         patch.object(plugin_mod, "read_QIcon", return_value="electrum-icon") as icon:
        dialog_cls.return_value.run.return_value = "secret"
        fake = MagicMock()
        assert plugin_mod.Plugin.password_dialog(fake, msg="Sign your will",
                                                 parent="win") == "secret"
    dialog_cls.assert_called_once_with("win", "Sign your will")    # text kept
    icon.assert_called_once_with("electrum.png")
    dialog_cls.return_value.setWindowIcon.assert_called_once_with("electrum-icon")


def test_only_the_still_active_warning_is_repeated_after_close():
    d = _dialog()
    d.close = MagicMock()
    d._still_active_warning = "older will still active"
    d._next_steps_hint = "press Sign"
    d._on_close_clicked()
    d.bal_window.show_warning.assert_called_once_with("older will still active")
    assert not d.bal_window.show_message.called


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print("{} tests passed".format(len(tests)))
