"""
The will summary at the top of the Will tab (CHANGELOG #84a).

The list shows one row per will-executor copy and the toolbar shows the
settings; the summary answers in words: is my will in force, when is it
delivered, when will I be asked to renew it, do the will-executors have it?

* ``will_summary`` (core): one test per state (green, orange, red, grey),
  the lines and the button of each state;
* ``relative_date``: "(in 1 year and 11 months)", "(passed 5 days ago)";
* ``WillSummaryWidget``: shows / hides, draws the lines, runs the button;
* ``BalWindow``: the box sits between the toolbar and the list, is refreshed
  with them, and its button runs the EXISTING commands.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \
        python3 tests/test_will_summary.py
"""

import os
import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from electrum.transaction import Transaction
from PyQt6.QtWidgets import QApplication, QHBoxLayout, QVBoxLayout, QWidget

from bal.core.messages import check_alive_cannot_renew
from bal.core.summary import busy_summary, executor_counts, relative_date, will_summary
from bal.core.will import WillItem
from bal.gui.qt import window as win_mod
from bal.gui.qt.dialogs import BalDialog
from bal.gui.qt.widgets import WillSummaryWidget
from bal.gui.qt.window import BalWindow

DAY = 86400


def _ts(y, m, d):
    return int(datetime(y, m, d, tzinfo=timezone.utc).timestamp())


NOW = _ts(2026, 10, 9)
DELIVERY = _ts(2028, 10, 1)            # the owner's will, as in the mockup
SETTINGS = {"locktime": "4y", "threshold": "-180d"}

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


def _we(n=0):
    return {"url": "https://we{}.test".format(n), "base_fee": 1000, "address": "tb1qwe"}


def _item(*statuses, we=None, locktime=DELIVERY, no_we=False):
    item = WillItem({
        "tx": _TX_HEX,
        "heirs": {"alice": ["tb1qalice", "100%", "4y"]},
        "willexecutor": None if no_we else (we or _we()),
        "baltx_fees": 13,
        "time": NOW - 30 * DAY,
    })
    item.tx.locktime = locktime
    for s in statuses:
        item.set_status(s, True)
    return item


VERIFIED = ("COMPLETE", "PUSHED", "CHECKED")


def _summary(items, settings=SETTINGS, basic=False, heirs=2, now=NOW,
             still_active=None):
    return will_summary(items, heirs, settings, basic, now=now,
                        still_active=still_active)


def _row(summary, label):
    return next(r for r in summary["rows"] if r["label"] == label)


# ------------------------------------------------------------------ green

def test_all_ok_as_in_the_mockup():
    s = _summary({"a": _item(*VERIFIED)})
    assert (s["state"], s["kind"], s["title"]) == ("ok", "ok", "Your will is in force")
    assert s["action"] is None and s["note"] is None
    delivery = _row(s, "Delivery to the heirs")
    assert delivery["strong"] == "2028-10-01"
    assert delivery["text"] == "(in 1 year and 11 months)"
    assert not delivery["red"]
    check = _row(s, "Check Alive")
    assert check["strong"] == "2028-04-04"                 # 180 days before
    assert check["text"] == (
        "(in 1 year and 5 months) — from that day the wallet asks you to renew it")
    assert _row(s, "Will-Executor")["text"] == "1 of 1 received and verified it"
    assert _row(s, "Heirs")["text"] == "2"


def test_basic_mode_has_no_check_alive_line():
    s = _summary({"a": _item(*VERIFIED)}, basic=True)
    assert s["state"] == "ok"
    assert [r["label"] for r in s["rows"]] == [
        "Delivery to the heirs", "Will-Executor", "Heirs"]


def test_two_executors_verified_and_one_to_verify():
    s = _summary({
        "a": _item(*VERIFIED, we=_we(0)),
        "b": _item(*VERIFIED, we=_we(1)),
        "c": _item("COMPLETE", "PUSHED", we=_we(2)),
    })
    assert s["state"] == "ok"
    row = _row(s, "Will-Executor")
    assert row["text"] == "2 of 3 verified it · 1 to be verified"
    assert row["alarm"] == ""


def test_backup_transaction_without_executor_is_in_force():
    s = _summary({"a": _item("COMPLETE", no_we=True)})
    assert s["state"] == "ok"
    assert _row(s, "Will-Executor")["text"] == "none (backup transaction only)"


def test_replaced_and_invalidated_items_are_ignored():
    old = _item("COMPLETE", "PUSHED", "REPLACED", locktime=DELIVERY + DAY)
    gone = _item("COMPLETE", "PUSHED", "INVALIDATED", locktime=NOW - DAY)
    s = _summary({"old": old, "gone": gone, "a": _item(*VERIFIED)})
    assert s["state"] == "ok"
    assert _row(s, "Delivery to the heirs")["strong"] == "2028-10-01"


# ----------------------------------------------------------------- orange

def test_one_executor_did_not_receive_it():
    s = _summary({
        "a": _item(*VERIFIED, we=_we(0)),
        "b": _item("COMPLETE", "PUSH_FAIL", we=_we(1)),
    })
    assert (s["state"], s["kind"]) == ("not_received", "warn")
    assert s["title"] == "In force, but 1 will-executor did not receive it"
    row = _row(s, "Will-Executor")
    assert row["text"] == "1 of 2 verified it"
    assert row["alarm"] == "1 did not receive it"
    assert (s["action"], s["action_text"]) == ("check", "Check again")


def test_check_failed_counts_as_not_received_and_plural():
    s = _summary({
        "a": _item(*VERIFIED, we=_we(0)),
        "b": _item("COMPLETE", "PUSHED", "CHECK_FAIL", we=_we(1)),
        "c": _item("COMPLETE", "PUSH_FAIL", we=_we(2)),
    })
    assert s["title"] == "In force, but 2 will-executors did not receive it"
    assert _row(s, "Will-Executor")["alarm"] == "2 did not receive it"


def test_no_executor_received_it():
    s = _summary({"a": _item("COMPLETE", "PUSH_FAIL")})
    assert (s["state"], s["kind"]) == ("none_received", "warn")
    assert s["title"] == "No will-executor received your will"
    assert s["action"] == "check"


def test_prepared_but_not_signed():
    s = _summary({"a": _item(we=_we(0)), "b": _item(we=_we(1))})
    assert (s["state"], s["kind"]) == ("to_sign", "warn")
    assert s["title"] == "Will prepared: it must be signed and sent"
    assert _row(s, "Will-Executor")["text"] == "to be signed and sent to 2 will-executors"
    assert (s["action"], s["action_text"]) == ("sign_send", "Sign and send")


def test_signed_but_not_sent():
    s = _summary({"a": _item("COMPLETE")})
    assert (s["state"], s["action"], s["action_text"]) == ("to_send", "send", "Send")
    assert s["title"] == "Will signed: it must be sent to the will-executors"
    assert _row(s, "Will-Executor")["text"] == "to be sent to 1 will-executor"


def test_check_alive_passed_offers_renew():
    delivery = NOW + 175 * DAY                     # Check Alive passed 5 days ago
    s = _summary({"a": _item(*VERIFIED, locktime=delivery)})
    assert (s["state"], s["kind"]) == ("check_alive", "warn")
    assert s["title"] == "The Check Alive date has passed: renew your will"
    check = _row(s, "Check Alive")
    assert check["red"] and check["text"] == "(passed 5 days ago)"
    assert (s["action"], s["action_text"]) == ("renew", "Renew")
    assert s["note"] == (
        "If you do not renew it, your will stays valid and will be delivered on "
        "{}.".format(datetime.fromtimestamp(delivery, tz=timezone.utc).strftime("%Y-%m-%d")))


def test_check_alive_passed_with_fixed_date_only_explains():
    delivery = NOW + 175 * DAY
    settings = {"locktime": str(delivery), "threshold": "-180d"}
    s = _summary({"a": _item(*VERIFIED, locktime=delivery)}, settings=settings)
    assert s["state"] == "check_alive"
    assert s["title"] == "The Check Alive date has passed"
    assert s["action"] is None
    assert "fixed date" in s["note"]
    assert s["note"] == check_alive_cannot_renew({
        "reason": "fixed_date", "has_will": True,
        "check_date": delivery - 180 * DAY,
    })


def test_check_alive_ignored_in_basic_mode():
    s = _summary({"a": _item(*VERIFIED, locktime=NOW + 175 * DAY)}, basic=True)
    assert s["state"] == "ok"


# -------------------------------------------------------------------- red

def test_delivery_date_passed():
    s = _summary({"a": _item(*VERIFIED, locktime=NOW - 2 * DAY)})
    assert (s["state"], s["kind"]) == ("expired", "bad")
    assert s["title"] == "The delivery date has passed"
    delivery = _row(s, "Delivery to the heirs")
    assert delivery["red"] and delivery["text"] == "(passed 2 days ago)"
    assert (s["action"], s["action_text"]) == ("check", "Check")
    assert "Press Check to invalidate it" in s["note"]


def test_no_will():
    s = _summary({})
    assert (s["state"], s["kind"], s["title"]) == ("none", "bad", "No will in force")
    assert _row(s, "Heirs")["text"] == "2 (no transaction prepared)"
    assert s["note"] == "Until you create your will, nobody will receive your bitcoin."
    assert (s["action"], s["action_text"]) == ("wizard", "Build Your Will")


def test_only_invalidated_items_means_no_will():
    s = _summary({"a": _item("COMPLETE", "PUSHED", "INVALIDATED")})
    assert s["state"] == "none"
    # Transactions exist (invalidated): never "no transaction prepared".
    assert _row(s, "Heirs")["text"] == "2"
    assert s["note"].startswith("Your previous will was invalidated on the blockchain")


# ------------------------------------------- records wrong, chain right (#84a)

def test_sent_will_still_executable_is_reported():
    """Owner's wallet: sent txs marked Replaced, their coin never spent."""
    old0 = _item(*VERIFIED, "REPLACED", we=_we(0), locktime=DELIVERY - 2 * DAY)
    old1 = _item(*VERIFIED, "REPLACED", we=_we(1), locktime=DELIVERY - 2 * DAY)
    new = _item("INVALIDATED", we=_we(0), locktime=DELIVERY - 3 * DAY)
    items = {"o0": old0, "o1": old1, "n": new}
    active = [(old0, DELIVERY - 2 * DAY, "https://we0.test"),
              (old1, DELIVERY - 2 * DAY, "https://we1.test")]
    s = _summary(items, still_active=active)
    assert (s["state"], s["kind"]) == ("still_active", "warn")
    assert s["title"] == "Your previous will is still active at the will-executors"
    assert _row(s, "Delivery to the heirs")["strong"] == "2028-09-29"
    assert _row(s, "Will-Executor")["text"] == "2 will-executors hold it"
    assert "never spent" in s["note"] and "2028-09-29" in s["note"]
    assert s["action"] is None
    # Without the on-chain finding the records alone say "no will".
    assert _summary(items)["state"] == "none"


def test_still_active_is_ignored_while_a_will_is_valid():
    old = _item(*VERIFIED, "REPLACED", locktime=DELIVERY - DAY)
    s = _summary({"a": _item(*VERIFIED), "o": old},
                 still_active=[(old, DELIVERY - DAY, "https://we0.test")])
    assert s["state"] == "ok"


def test_busy_summary():
    s = busy_summary()
    assert (s["state"], s["kind"], s["action"], s["rows"]) == ("busy", "busy", None, [])
    assert s["title"] == "Operation in progress: follow the window in front"


# ------------------------------------------------------------------- grey

def test_executed_and_being_executed():
    s = _summary({"a": _item("COMPLETE", "CONFIRMED", locktime=NOW - DAY)})
    assert (s["state"], s["kind"], s["action"]) == ("executed", "info", None)
    s = _summary({"a": _item("COMPLETE", "MEMPOOL", locktime=NOW - DAY)})
    assert (s["state"], s["kind"]) == ("mempool", "info")


# ---------------------------------------------------------------- helpers

def test_relative_dates():
    assert relative_date(NOW + 3600, now=NOW) == "(today)"
    assert relative_date(NOW + DAY, now=NOW) == "(in 1 day)"
    assert relative_date(NOW + 45 * DAY, now=NOW) == "(in 45 days)"
    assert relative_date(_ts(2026, 12, 20), now=NOW) == "(in 2 months)"
    assert relative_date(_ts(2027, 10, 9), now=NOW) == "(in 1 year)"
    assert relative_date(_ts(2027, 11, 9), now=NOW) == "(in 1 year and 1 month)"
    assert relative_date(_ts(2030, 10, 9), now=NOW) == "(in 4 years)"
    assert relative_date(NOW - DAY, now=NOW) == "(passed 1 day ago)"
    assert relative_date(_ts(2026, 1, 9), now=NOW) == "(passed 9 months ago)"


def test_executor_counted_once_at_its_worst_copy():
    counts = executor_counts({
        "a": _item(*VERIFIED, we=_we(0)),
        "b": _item("COMPLETE", "PUSH_FAIL", we=_we(0)),
        "c": _item(*VERIFIED, we=_we(1)),
    })
    assert counts["failed"] == 1 and counts["verified"] == 1


def test_never_raises():
    assert will_summary({"a": object()}, 1, SETTINGS, False, now=NOW) is None


# ----------------------------------------------------------------- widget

# Kept at module level: an application object that is not referenced is
# destroyed at once, and creating a widget afterwards aborts Python.
_APP = QApplication.instance() or QApplication(sys.argv)


def test_widget_shows_lines_and_button():
    clicked = []
    w = WillSummaryWidget(clicked.append)
    assert w.isHidden()
    s = _summary({
        "a": _item(*VERIFIED, we=_we(0)),
        "b": _item("COMPLETE", "PUSH_FAIL", we=_we(1)),
    })
    w.show_summary(s)
    assert not w.isHidden()
    assert w.title.text() == s["title"]
    assert w.grid.rowCount() >= len(s["rows"])
    value = w.grid.itemAtPosition(0, 1).widget().text()
    assert "<b>2028-10-01</b>" in value
    alarm = w.grid.itemAtPosition(2, 1).widget().text()
    assert "1 did not receive it" in alarm and "#c62828" in alarm
    assert not w.button.isHidden() and w.button.text() == "Check again"
    w.button.click()
    assert clicked == ["check"]


def test_widget_without_button_and_hidden_for_none():
    w = WillSummaryWidget(lambda a: None)
    w.show_summary(_summary({"a": _item(*VERIFIED)}))
    assert w.button.isHidden() and w.note.isHidden()
    # Redrawing with fewer lines leaves no stale line behind.
    w.show_summary(_summary({}))
    assert w.grid.count() == 2 and not w.note.isHidden()
    w.show_summary(None)
    assert w.isHidden()


def test_busy_box_leaves_no_old_line_painted():
    """CHANGELOG #87: the old lines vanish at once, not at deleteLater."""
    w = WillSummaryWidget(lambda a: None)
    w.show_summary(_summary({"a": _item(*VERIFIED)}))
    old = [w.grid.itemAt(i).widget() for i in range(w.grid.count())]
    assert old and all(x.parent() is w for x in old)
    w.show_summary(busy_summary())          # no event loop runs in between
    assert w.grid.count() == 0
    assert all(x.isHidden() and x.parent() is None for x in old)


# ------------------------------------------------------------------ window

def test_box_goes_between_toolbar_and_list():
    lst = QWidget()

    def create_list_tab(widget):
        # Same layout as Electrum's ElectrumWindow.create_list_tab.
        tab = QWidget()
        vbox = QVBoxLayout(tab)
        vbox.addLayout(QHBoxLayout())
        vbox.addWidget(widget)
        return tab

    fake = MagicMock()
    fake.window.create_list_tab = create_list_tab
    with patch.object(win_mod, "PreviewList", return_value=lst):
        tab = BalWindow.create_will_tab(fake)
    layout = tab.layout()
    assert layout.indexOf(fake.will_summary_widget) == 1
    assert layout.indexOf(lst) == 2
    fake.update_will_summary.assert_called_once()


def test_update_will_summary_feeds_the_box():
    fake = MagicMock()
    fake._will_summary_busy = 0
    fake._still_active_for_summary.return_value = []
    fake.willitems = {}
    fake.heirs = {"alice": [], "bob": []}
    fake.will_settings = dict(SETTINGS)
    fake.bal_plugin.is_basic_mode.return_value = False
    BalWindow.update_will_summary(fake)
    summary = fake.will_summary_widget.show_summary.call_args[0][0]
    assert summary["state"] == "none"
    assert summary["rows"][0]["text"].startswith("2 ")


def test_busy_while_a_working_dialog_is_shown():
    fake = MagicMock()
    fake._will_summary_busy = 0
    BalWindow.set_will_summary_busy(fake, True)
    BalWindow.set_will_summary_busy(fake, True)
    assert fake._will_summary_busy == 2
    BalWindow.update_will_summary(fake)
    assert fake.will_summary_widget.show_summary.call_args[0][0]["state"] == "busy"
    BalWindow.set_will_summary_busy(fake, False)
    BalWindow.set_will_summary_busy(fake, False)
    BalWindow.set_will_summary_busy(fake, False)       # never below zero
    assert fake._will_summary_busy == 0


def test_dialog_show_hide_keeps_the_counter_balanced():
    window = MagicMock()
    dialog = BalDialog.__new__(BalDialog)                # no Qt set-up needed
    dialog._summary_window = window
    dialog._summary_busy_on = False
    BalDialog._set_summary_busy(dialog, True)
    BalDialog._set_summary_busy(dialog, True)          # a second show event
    BalDialog._set_summary_busy(dialog, False)
    BalDialog._set_summary_busy(dialog, False)
    assert [c.args for c in window.set_will_summary_busy.call_args_list] == [
        (True,), (False,)]


def test_still_active_check_only_without_a_valid_will():
    fake = MagicMock()
    fake.willitems = {"a": _item(*VERIFIED)}
    with patch.object(win_mod.Will, "find_still_active_older_txs") as find:
        assert BalWindow._still_active_for_summary(fake) == []
        assert not find.called
        fake.willitems = {"a": _item(*VERIFIED, "REPLACED")}
        find.return_value = ["x"]
        assert BalWindow._still_active_for_summary(fake) == ["x"]
        assert find.call_args.kwargs["current_locktime"] == 2**32


def test_buttons_run_the_existing_commands():
    def run(action, auto=True, basic=False):
        fake = MagicMock()
        fake.bal_plugin.is_basic_mode.return_value = basic
        fake.bal_plugin.AUTO_SIGN.get.return_value = auto
        BalWindow.on_will_summary_action(fake, action)
        return fake

    assert run("wizard").init_wizard.called
    assert run("renew").offer_check_alive_renewal.called
    assert run("check").will_list_widget.check.called
    assert run("sign_send").will_list_widget.check.called
    assert run("send", auto=False, basic=True).will_list_widget.check.called
    fake = run("sign_send", auto=False)
    assert not fake.will_list_widget.check.called
    fake.ask_password_and_sign_transactions.assert_called_once_with(
        callback=fake.broadcast_transactions)
    fake = run("send", auto=False)
    assert fake.broadcast_transactions.called
    assert not fake.will_list_widget.check.called


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print("{} tests passed".format(len(tests)))
