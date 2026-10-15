"""Each wallet keeps its own delivery settings: the real flows (CHANGELOG #123).

Before #123 the delivery date and the fee rate lived in Electrum's global
``bal_will_settings``, shared by every wallet. A period such as "1054d" was
read against the clock again at every Check: from day +2 the owner's
Check took his will for a postpone and asked for a paid invalidation.

Since #123 each wallet keeps its own settings in its own file (wallet key
``will_settings``). They are migrated once, when the wallet opens, to the
date of the will in force; the global is never written again (rule I1). The
anchor check follows a will changed by another program, a one-time note and
the "which date?" question cover the cases where the old value asked for
something else, and the plugin stops ("fails closed") when the settings
cannot be prepared. The last section proves the funds-safety fixes of the
#123 review (FS-1, FS-2): a delivery date never goes back to a will already
paid to leave, and never follows a will nobody signed.

The state "before #123" is made by the test itself: the wallet's own dict is
emptied (Safe21 before #123 never wrote it), and the old shared value is
written into Electrum's global, as the older plugin left it. Where the older
plugin built the wills from a period, the period is also written into the
heirs and into the stored will items. Then the window is opened again: that
is the update.

Wallets, coins and will-executors come from ``tests/flowkit.py``: three fake
will-executors (each with its own fee address), throw-away test wallets with
the test password, a fixed clock. Nothing reaches a real will-executor or
the network.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_per_wallet_settings_flow.py
"""

import json
import os
import shutil
import sys
import threading

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

import flowkit as fk
from electrum.storage import WalletStorage
from electrum.transaction import tx_from_any
from electrum.wallet import Wallet
from electrum.wallet_db import WalletDB
from PyQt6.QtWidgets import QApplication

from bal.cli import commands as cli_commands
from bal.cli.controller import BalController
from bal.core import delivery
from bal.core.will import WillItem
from bal.engine import context as engine_context
from bal.engine.locks import flow_lock_for
from bal.gui.qt.flow_adapter import CHOOSE_DATE_FIRST, SETTINGS_UNAVAILABLE

# Kept at module level: an unreferenced application object is destroyed at
# once, and creating a widget afterwards aborts Python.
_APP = QApplication.instance() or QApplication(sys.argv)

# Test value: the password of the throw-away test wallets.
TEST_PASSWORD = "flowkit-test"

# The owner's will in force: 2029-08-29 00:00 UTC.
OWNER_DATE = fk.ts(2029, 8, 29)
# The old shared value of the owner's Electrum (written by Safe21 before #123).
OLD_GLOBAL = {"locktime": "1054d", "baltx_fees": 13, "threshold": "-180d"}
# The prefix of the will-executor pseudo-heirs inside a will item.
_EXECUTOR_HEIR = 'w!ll3x3c"'
# The flags that say whether a will item is in force, signed or sent.
_STATE_FLAGS = ("VALID", "COMPLETE", "PUSHED", "REPLACED", "INVALIDATED")


@pytest.fixture
def make(monkeypatch):
    """Start ``tests/flowkit.py`` sessions with three will-executors.

    Electrum's asyncio loop runs during the test and is stopped after it
    (else pytest never exits); every session's folder is removed.
    """
    fk.ensure_event_loop()
    made = []

    def _make(**kw):
        """One session (``fk.Session`` arguments), three will-executors."""
        kw.setdefault("password", TEST_PASSWORD)
        s = fk.Session(monkeypatch, **kw)
        s.world.add_executor()
        made.append(s)
        return s

    yield _make
    for s in made:
        s.close()
    fk.stop_event_loop()


# ------------------------------------------------------------------ helpers

def wallet_dict(s):
    """The wallet's own delivery settings (the live dict of the wallet file)."""
    return s.wallet.db.get_dict("will_settings")


def last(s):
    """The last step the session recorded."""
    return s.recorder.steps[-1]


def rows(step):
    """The rows of a recorded step, without HTML."""
    return [fk.strip(r) for r in step.get("rows", [])]


def assert_quiet(step):
    """Nothing was asked, pushed or broadcast in ``step`` (dialogs opened by
    the window included: their questions are the session's questions)."""
    assert step["questions"] == [], step["questions"]
    assert step["pushed"] == [], step["pushed"]
    assert step["broadcasts"] == [], step["broadcasts"]


def assert_check_ok(step):
    """A Check that ended with "All done: Ok" and did nothing."""
    assert_quiet(step)
    assert "All done: Ok" in rows(step), rows(step)


def question_texts(step):
    """The texts of the questions asked in ``step``."""
    return [q[1] for q in step["questions"]]


def stored_states(s):
    """``{txid: flags}`` of the will as stored in the wallet file, with the
    raw transaction: a change means something was signed, sent or rebuilt."""
    out = {}
    for wid, item in s.wallet.db.get_dict("will").items():
        flags = tuple(f for f in _STATE_FLAGS if item.get(f) is True)
        out[wid] = (flags, str(item["tx"]))
    return out


def item_table(s):
    """``[(date, flags, executor)]`` of the window's will items, sorted."""
    out = []
    for w in s.bw.willitems.values():
        flags = tuple(f for f in _STATE_FLAGS if w.get_status(f))
        out.append((fk.fmt(w.tx.locktime), flags, (w.we or {}).get("url")))
    return sorted(out)


def set_item_flags(s, pick, **flags):
    """Change status flags of the stored will items that ``pick(item)``
    selects (as another program could have left them)."""
    will = s.wallet.db.get_dict("will")
    for wid in list(will):
        if pick(will[wid]):
            for key, value in flags.items():
                will[wid][key] = value


def set_item_recipes(s, value, pick=lambda item: True):
    """Write ``value`` as the heirs' delivery recipe of the stored will items
    that ``pick(item)`` selects: the older plugin built its wills from the
    shared value, so a period ("1054d") is kept inside its items."""
    will = s.wallet.db.get_dict("will")
    for wid in list(will):
        if not pick(will[wid]):
            continue
        heirs = will[wid]["heirs"]
        for name in list(heirs):
            if not name.startswith(_EXECUTOR_HEIR):
                heir = list(heirs[name])
                heir[2] = value
                heirs[name] = heir


def set_heirs_recipe(s, value):
    """Write ``value`` as every heir's delivery date in the wallet (the older
    plugin gave every heir the shared value)."""
    for name in list(s.bw.heirs):
        heir = list(s.bw.heirs[name])
        heir[2] = value
        s.bw.heirs[name] = heir


def current_anchor(s):
    """The fingerprint of the window's VALID will items (plan 3.2)."""
    views = delivery.views_from(s.bw.willitems)
    return delivery.fingerprint(delivery.valid_ids(views))


def watch_patches(s, monkeypatch):
    """Record every change written to the wallet file from now on."""
    patches = []
    real = s.wallet.db.add_patch

    def add_patch(patch):
        """Record one change, then make it."""
        patches.append(patch)
        return real(patch)

    monkeypatch.setattr(s.wallet.db, "add_patch", add_patch)
    return patches


def write_items(d, values):
    """Make the dict ``d`` equal to ``values``, item by item (rule I3)."""
    for key in list(d):
        if key not in values:
            del d[key]
    for key, value in values.items():
        d[key] = value


def update_from_old_plugin(s, old_global, heirs_recipe=None):
    """Bring the wallet back to the time before #123, then install #123.

    The wallet's own dict is emptied (Safe21 before #123 never wrote it),
    ``old_global`` is written into Electrum's global (BAL's key, as the older
    plugin left it; the plugin itself never writes it) and taken as the
    rule-I1 reference, ``heirs_recipe`` (if given) becomes every heir's date.
    Then the window opens again: the one-time migration runs there. Returns
    the wallet's own dict.
    """
    d = wallet_dict(s)
    write_items(d, {})
    if heirs_recipe is not None:
        set_heirs_recipe(s, heirs_recipe)
    s.config.set_key("bal_will_settings", dict(old_global))
    s.snapshot_global()
    s.reload_window()
    return d


def owner_state(make, mode="basic"):
    """The owner's wallet on 2026-10-10 (see the module docstring).

    A will at the FIXED date 2029-08-31, signed and sent to three
    will-executors; the heirs change (a new will one day earlier, 08-30);
    a coin arrives (a new will one more day earlier, 08-29). The two older
    wills are REPLACED but still held by the will-executors. Built from the
    fixed date, so the items carry dates, not periods.
    """
    s = make(mode=mode, locktime=fk.ts(2029, 8, 31))
    s.check()
    s.set_heirs(alice="50%", bob="30%")
    s.check()
    s.add_coin()
    s.check()
    valid = ("VALID", "COMPLETE", "PUSHED")
    replaced = ("COMPLETE", "PUSHED", "REPLACED")
    executors = ["https://we{}.test".format(i) for i in range(3)]
    assert item_table(s) == sorted(
        [("2029-08-29", valid, url) for url in executors]
        + [("2029-08-30", replaced, url) for url in executors]
        + [("2029-08-31", replaced, url) for url in executors]
    )
    return s


# ---------------------------------------------------------- the owner's wallet

@pytest.mark.parametrize("mode", ["basic", "advanced"])
def test_owner_wallet_migrates_to_its_will_and_stays_quiet(make, monkeypatch, mode):
    """The owner's case: the shared "1054d" becomes the fixed date of his
    will in force, 29/08/2029 00:00 UTC, fee 13, note "converted". Then the
    Checks at +0, +1, +2 (and the command line at +2) and +7 days and the
    wallet close are all Ok: nothing asked, signed, sent or broadcast. The
    global keeps "1054d" byte for byte (I1); only the wallet's own dict is
    written, never with ``db.put`` (I3)."""
    assert OWNER_DATE == 1882656000
    s = owner_state(make, mode)

    def no_put(key, value):
        """``db.put`` must never replace the settings dict (rule I3)."""
        assert key != "will_settings", "rule I3: db.put on will_settings"
        return real_put(key, value)

    real_put = s.wallet.db.put
    monkeypatch.setattr(s.wallet.db, "put", no_put)
    d = update_from_old_plugin(s, OLD_GLOBAL, heirs_recipe="1054d")

    assert s.bw.will_settings is d                                       # I3
    assert s.bw.settings_unavailable is False
    assert dict(d, migrated_at=None, anchor_valid=None) == {
        "settings_version": 2,
        "form": "fixed",
        "locktime": OWNER_DATE,
        "baltx_fees": 13,
        "threshold": "-180d",
        "migrated_from": "will",
        "migrated_at": None,
        "legacy_locktime": "1054d",
        "note_kind": "converted",
        "note_new": OWNER_DATE,
        "anchor_valid": None,
        "anchor_locktime": OWNER_DATE,
    }
    assert d["migrated_at"] == fk.FIXED_NOW
    # The heirs no longer hold the period: it would be read against the
    # clock again.
    assert {h[2] for h in s.wallet.db.get("heirs").values()} == {OWNER_DATE}

    s.show_note()
    assert last(s)["popups"] == [
        ["message", "Your delivery date is now fixed: 29/08/2029."]]
    before = stored_states(s)
    for days, label in ((0, "+0"), (1, "+1"), (1, "+2"), (5, "+7")):
        s.clock.travel(days)
        if label == "+2":
            # The day the old code asked for the paid invalidation.
            assert s.cli("will_check") == {"returned": {"valid": True}}
            assert_quiet(last(s))
            s.reload_window()
        s.check()
        step = last(s)
        assert_check_ok(step)
        assert step["settings"]["locktime"] == "2029-08-29", label
    s.close_wallet()
    assert_quiet(last(s))
    assert stored_states(s) == before        # nothing signed, sent or rebuilt
    assert d["locktime"] == OWNER_DATE
    s.show_note()
    assert last(s)["popups"] == []           # the note is shown only once
    s.assert_global_unchanged()                                          # I1
    assert s.config.get("bal_will_settings") == OLD_GLOBAL


def test_owner_wallet_back_after_an_outside_postpone(make):
    """The roundtrip: another program that reads the shared "1054d" (BAL,
    or Safe21 before #123) postponed the will to 08-31 on day +2. It never
    touches the wallet's own dict, so back on #123 the dict still says 08-29
    and the anchor of the old will. The anchor check follows the will in
    force: the date becomes 08-31 with the note "outside_change", and the
    Check is Ok with nothing signed or sent (before the anchor the stored
    date won and a new will was signed at 08-29)."""
    s = owner_state(make)
    d = update_from_old_plugin(s, OLD_GLOBAL, heirs_recipe="1054d")
    s.check()
    assert_check_ok(last(s))
    old_dict = dict(d)
    old_anchor = d["anchor_valid"]

    # The other program at day +2: it invalidates the 08-29 will and sends
    # one at 08-31, built from the shared period (written into its items
    # and the heirs). The wallet's own dict is left as it was.
    s.clock.travel(2)
    s.set_date(fk.ts(2029, 8, 31))
    s.check()
    assert "Invalidate your old will" in question_texts(last(s))
    write_items(d, old_dict)
    set_item_recipes(s, "1054d", pick=lambda item: item.get("VALID") is True)
    set_heirs_recipe(s, "1054d")
    after_postpone = stored_states(s)

    s.clock.travel(1)
    s.reload_window()                         # back on #123
    new_date = fk.ts(2029, 8, 31)
    assert d["locktime"] == new_date
    assert (d["note_kind"], d["note_old"], d["note_new"]) == (
        "outside_change", OWNER_DATE, new_date)
    assert delivery.CHOICE_KEY not in d
    assert d["anchor_valid"] != old_anchor and d["anchor_locktime"] == new_date
    s.show_note()
    assert last(s)["popups"] == [["message", (
        "Your will was changed by another program. The delivery date now "
        "follows the will in force: 31/08/2029 (before: 29/08/2029).")]]
    for days in (0, 1):
        s.clock.travel(days)
        s.check()
        assert_check_ok(last(s))
        assert last(s)["settings"]["locktime"] == "2029-08-31"
    assert stored_states(s) == after_postpone
    s.assert_global_unchanged()


def test_owner_wallet_back_after_an_outside_anticipation(make):
    """The other direction: another program moved the SIGNED will EARLIER
    (08-27) on day +2 and left the wallet's own dict as it was. Back on
    #123 the anchor follows the signed will in force: the date becomes
    08-27 with the note "outside_change" (both dates), no question, and the
    note adds that the later date again is a postpone (miner fee). The next
    Checks are Ok with nothing signed or sent."""
    s = owner_state(make)
    d = update_from_old_plugin(s, OLD_GLOBAL, heirs_recipe="1054d")
    s.check()
    assert_check_ok(last(s))
    old_dict = dict(d)
    old_anchor = d["anchor_valid"]

    # The other program at day +2: an earlier date needs no invalidation,
    # the new will is signed and sent to the three will-executors.
    s.clock.travel(2)
    earlier = fk.ts(2029, 8, 27)
    s.set_date(earlier)
    s.check()
    assert question_texts(last(s)) == ["Sign your will"]
    assert len(last(s)["pushed"]) == 3 and last(s)["broadcasts"] == []
    write_items(d, old_dict)
    after_anticipation = stored_states(s)

    s.clock.travel(1)
    s.reload_window()                         # back on #123
    assert d["locktime"] == earlier
    assert (d["note_kind"], d["note_old"], d["note_new"]) == (
        "outside_change", OWNER_DATE, earlier)
    assert delivery.CHOICE_KEY not in d
    assert delivery.LATER_WISH_KEY not in d
    assert d["anchor_valid"] != old_anchor and d["anchor_locktime"] == earlier
    s.show_note()
    assert last(s)["popups"] == [["message", (
        "Your will was changed by another program. The delivery date now "
        "follows the will in force: 27/08/2029 (before: 29/08/2029).\n\n"
        "To use the later date again, choose it and press 'Check your will': "
        "first the old will needs an invalidation transaction (your bitcoin "
        "stay yours, you only pay the network fee).")]]
    for days in (0, 1):
        s.clock.travel(days)
        s.check()
        assert_check_ok(last(s))
        assert last(s)["settings"]["locktime"] == "2029-08-27"
    assert stored_states(s) == after_anticipation
    s.assert_global_unchanged()


# ------------------------------------------------- the "which date?" question

def edge_state(make, mode="basic"):
    """A later date asked for and never carried out, with an unsigned will.

    Day 0, before #123, shared "1000d": a will at 07-06 is signed and sent;
    the heirs change and the owner cancels "Sign your will" (a new VALID
    will at 07-05, not signed; the 07-06 one is REPLACED but sent). Day +1:
    the shared value becomes "1010d" and the owner cancels the paid
    invalidation. Both wills were built from "1000d". Then #123: the old
    value asked for 07-15 (07-05 + 10 days, the drift-free rule), so the note
    "later_pending" and the question are set; the date stays 07-05.
    """
    s = make(mode=mode, locktime=fk.ts(2029, 7, 6))
    s.check()
    s.set_heirs(alice="50%", bob="30%")
    s.check(cancel_on={"Sign your will"})
    set_item_recipes(s, "1000d")
    s.clock.travel(1)
    d = update_from_old_plugin(
        s, {"locktime": "1010d", "baltx_fees": 13, "threshold": "-180d"},
        heirs_recipe="1010d")
    assert (d["locktime"], d["migrated_from"], d["legacy_locktime"]) == (
        fk.ts(2029, 7, 5), "will", "1010d")
    assert (d["note_kind"], d["note_old"], d["note_new"]) == (
        "later_pending", fk.ts(2029, 7, 15), fk.ts(2029, 7, 5))
    assert d[delivery.CHOICE_KEY] == 1
    return s, d


def test_question_holds_every_flow_until_answered(make):
    """While the question waits nothing is built, signed, sent or
    invalidated (rule I6): the note says so, Sign / Broadcast / Invalidate
    refuse, the automatic rebuild and the wallet close skip, the command
    line returns the code ``delivery_choice_pending``, and a Check whose
    question is cancelled stops with nothing changed."""
    s, d = edge_state(make)
    before = stored_states(s)
    s.show_note()
    text = last(s)["popups"][0][1]
    assert "15/07/2029" in text and "05/07/2029" in text, text
    assert fk.strip(CHOOSE_DATE_FIRST) in text
    # The question still needs both dates after the note was shown.
    assert d[delivery.CHOICE_KEY] == 1 and d["note_old"] == fk.ts(2029, 7, 15)
    for action in (s.menu_sign, s.menu_broadcast, s.menu_invalidate):
        action()
        assert last(s)["popups"] == [["message", fk.strip(CHOOSE_DATE_FIRST)]]
        assert_quiet(last(s))
    assert s.auto() == {"returned": False}
    assert_quiet(last(s))
    # bal_will_check, bal_will_prepare, bal_will_autorebuild.
    for method in ("will_check", "prepare_will", "auto_rebuild"):
        out = s.cli(method)["returned"]
        assert out["result"] == "delivery_choice_pending", (method, out)
        assert "bal_delivery_set --keep true" in out["message"]
        assert_quiet(last(s))
    s.reload_window()
    s.check(cancel_on={"delivery date"})
    step = last(s)
    assert [q[0] for q in step["questions"]] == ["delivery date"]
    assert any(r.endswith("The check stopped: nothing was changed.")
               for r in rows(step)), rows(step)
    assert step["pushed"] == [] and step["broadcasts"] == []
    s.close_wallet()
    assert last(s)["result"] == {"returned": None}
    assert_quiet(last(s))
    assert "dialogs_opened" not in last(s)          # no closing Check at all
    assert d[delivery.CHOICE_KEY] == 1 and d["locktime"] == fk.ts(2029, 7, 5)
    assert stored_states(s) == before
    s.assert_global_unchanged()


@pytest.mark.parametrize("mode", ["basic", "advanced"])
def test_question_keep_signs_the_prepared_will(make, mode):
    """"Keep 05/07/2029" (the default): the prepared will is signed and
    sent at 07-05, no invalidation; the question and its note are gone, and
    the next day is quiet."""
    s, d = edge_state(make, mode)
    s.user.delivery_answer = "keep"
    s.check()
    step = last(s)
    asked = step["questions"]
    assert asked[0][0] == "delivery date"
    assert asked[0][1] == " | ".join((
        "The will prepared for 05/07/2029 is not signed and sent yet. The "
        "shared setting (possibly changed from another wallet) asked for a "
        "later date: 15/07/2029. Which date do you want?",
        "Keep 05/07/2029 (the will already prepared, no fee)",
        "Use 15/07/2029 (later: needs an invalidation transaction, you only pay "
        "the network fee)"))
    assert question_texts(step)[1:] == ["Sign your will"]
    assert len(step["pushed"]) == 3 and step["broadcasts"] == []
    assert d["locktime"] == fk.ts(2029, 7, 5)
    for key in (delivery.CHOICE_KEY,) + delivery.NOTE_KEYS:
        assert key not in d, key
    assert ("2029-07-05", ("VALID", "COMPLETE", "PUSHED"), "https://we0.test") \
        in item_table(s)
    s.clock.travel(1)
    s.check()
    assert_check_ok(last(s))


def test_prepare_asks_the_question_first(make):
    """The Will tab's Prepare asks the same question as the Check before it
    prepares anything. Cancel: nothing changes and the question still waits.
    "Keep": the question is gone, the date stays 05/07/2029 and nothing is
    invalidated or broadcast."""
    s, d = edge_state(make)
    before = stored_states(s)
    s.prepare(cancel_on={"delivery date"})
    step = last(s)
    assert [q[0] for q in step["questions"]] == ["delivery date"]
    assert step["pushed"] == [] and step["broadcasts"] == []
    assert d[delivery.CHOICE_KEY] == 1 and stored_states(s) == before

    s.user.delivery_answer = "keep"
    s.prepare()
    step = last(s)
    assert step["questions"][0][0] == "delivery date", step["questions"]
    assert "Invalidate your old will" not in question_texts(step)
    assert step["broadcasts"] == []
    assert d["locktime"] == fk.ts(2029, 7, 5)
    for key in (delivery.CHOICE_KEY,) + delivery.NOTE_KEYS:
        assert key not in d, key
    s.assert_global_unchanged()


def test_question_use_later_asks_for_the_invalidation(make):
    """"Use 15/07/2029": the later date is stored and the Check then asks
    for the invalidation of the sent will (a paid postpone), as before #123.
    Cancelled here: nothing is signed or broadcast."""
    s, d = edge_state(make)
    before = stored_states(s)
    s.user.delivery_answer = "later"
    s.check(cancel_on={"Invalidate your old will"})
    step = last(s)
    assert step["questions"][0][0] == "delivery date"
    assert "Invalidate your old will" in question_texts(step)
    assert "Sign your will" not in question_texts(step)
    assert step["broadcasts"] == [] and step["pushed"] == []
    assert d["locktime"] == fk.ts(2029, 7, 15)
    assert delivery.CHOICE_KEY not in d
    assert stored_states(s) == before


# --------------------------------------------- sent wills and the will in force

def _will_copy(item):
    """A plain copy of one stored will item (JSON values only)."""
    return json.loads(json.dumps(item))


def test_sent_will_earlier_than_the_will_in_force_is_a_postpone(make):
    """The critic's case: a will REPLACED but still sent and executable,
    EARLIER than the VALID will. Before and after the migration the Check
    asks for the invalidation of the earlier one (the #69/#72 protection)
    and nothing is signed: the migration keeps the date of the will in
    force, never the earlier sent one (it would read as an anticipation and
    sign the newer will at the earlier date)."""
    s = make(locktime=fk.D0)
    s.check()                                 # will A at D0, signed, sent
    will = s.wallet.db.get_dict("will")
    first = {wid: _will_copy(item) for wid, item in will.items()}
    # Will B at LATER, built while A is out of sight (as another program
    # may have done), then A comes back: REPLACED, still sent, executable.
    for wid in first:
        del will[wid]
    s.reload_window()
    s.set_date(fk.LATER)
    s.check()
    assert "Invalidate your old will" not in question_texts(last(s))
    for wid, item in first.items():
        item.update(VALID=False, REPLACED=True)
        will[wid] = item
    s.reload_window()
    assert {(day, flags) for day, flags, _url in item_table(s)} == {
        ("2032-09-23", ("COMPLETE", "PUSHED", "REPLACED")),
        ("2033-09-23", ("VALID", "COMPLETE", "PUSHED")),
    }
    before = stored_states(s)

    def check_asks_for_the_invalidation():
        """The Check stops at the invalidation (cancelled): nothing done."""
        s.check(cancel_on={"Invalidate your old will"})
        step = last(s)
        # #135: the invalidation step's "Invalidate now", then the password.
        assert question_texts(step) == ["Invalidate now", "Invalidate your old will"],             step["questions"]
        assert step["pushed"] == [] and step["broadcasts"] == []
        assert stored_states(s) == before

    check_asks_for_the_invalidation()          # before the migration
    d = update_from_old_plugin(s, OLD_GLOBAL)
    assert (d["locktime"], d["migrated_from"]) == (fk.LATER, "will")
    check_asks_for_the_invalidation()          # after the migration
    assert d["locktime"] == fk.LATER


def test_no_will_in_force_follows_the_earliest_sent_will(make):
    """No VALID item, only sent ones (REPLACED, built from periods), and a
    period in the global: the date is the earliest sent will still pending
    (source "sent"), so no sent will reads as a postpone. The Check prepares
    the will at that date with no invalidation, and two days later it is Ok."""
    s = owner_state(make)
    set_item_flags(s, lambda item: item.get("VALID") is True,
                   VALID=False, REPLACED=True)
    set_item_recipes(s, "1054d")
    d = update_from_old_plugin(s, OLD_GLOBAL, heirs_recipe="1054d")
    assert (d["locktime"], d["migrated_from"], d["baltx_fees"]) == (
        OWNER_DATE, "sent", 13)
    assert d["note_kind"] == "converted"
    s.check()
    step = last(s)
    assert "Invalidate your old will" not in question_texts(step)
    assert step["broadcasts"] == []
    assert "All done: Ok" in rows(step)
    assert step["settings"]["locktime"] == "2029-08-29"
    assert {day for day, flags, _url in item_table(s) if "VALID" in flags} == {
        "2029-08-29"}
    # The new will in force was saved by Safe21: its anchor is recorded, so
    # the next opening does not take it for a change made by another program.
    assert d["anchor_valid"] == current_anchor(s)
    assert d["anchor_locktime"] == OWNER_DATE
    s.clock.travel(2)
    s.reload_window()
    assert "note_kind" not in d or d["note_kind"] != "outside_change"
    s.check()
    assert_check_ok(last(s))


def test_legacy_items_with_tx_fees_give_their_fee_on_both_paths(make):
    """A will stored by an older BAL, whose items hold only ``tx_fees``:
    the fee is fixed on a copy first, so the migration finds 13, never 0
    (and not the global's 20). The window (stored will, wallet untouched)
    and the command line (its loaded items) give identical dicts."""
    s = make(locktime=fk.D0)
    s.check()
    will = s.wallet.db.get_dict("will")
    for wid in list(will):
        item = _will_copy(will[wid])
        item["tx_fees"] = item.pop("baltx_fees")
        will[wid] = item
    old = {"locktime": "1054d", "baltx_fees": 20, "threshold": "-180d"}
    d = update_from_old_plugin(s, old)
    gui = dict(d)
    assert (gui["baltx_fees"], gui["migrated_from"], gui["locktime"]) == (
        13, "will", fk.D0)
    # The window fixed a copy: the wallet's will is still the old one.
    assert all("tx_fees" in item and "baltx_fees" not in item
               for item in will.values())
    write_items(d, {})
    s.cli("delivery_show")
    assert dict(d) == gui
    s.reload_window()
    s.check()
    assert_check_ok(last(s))


# --------------------------------------------------------------- fail closed

def test_fail_closed_then_init_will_recovers(make, monkeypatch):
    """The will items cannot be read (an injected error): the settings are
    "unavailable". No period reaches the window's settings (only display
    values: a date), nothing is written, and every flow stops: the Check
    with the error row, Sign / Broadcast / Invalidate, Prepare, the
    automatic rebuild, the command line. Nothing is built, signed, sent or
    broadcast. The retry in ``init_will`` recovers once the items read."""
    s = make(locktime=fk.D0)
    s.check()
    pushed = len(s.world.pushed)
    real = engine_context.will_views
    broken = {"on": True}

    def will_views(host, willitems=None):
        """The item reading, broken while ``broken["on"]``."""
        if broken["on"]:
            raise RuntimeError("injected: the will items cannot be read")
        return real(host, willitems)

    monkeypatch.setattr(engine_context, "will_views", will_views)
    d = update_from_old_plugin(s, OLD_GLOBAL)
    bw = s.bw
    assert bw.settings_unavailable is True
    assert bw.will_settings is not d and dict(d) == {}
    for value in bw.will_settings.values():
        assert not (isinstance(value, str) and value.endswith(("d", "y"))
                    and not value.startswith("-")), bw.will_settings
    assert delivery.as_delivery(bw.will_settings["locktime"]) is not None
    assert not delivery.is_current(bw.will_settings)

    s.check()
    assert fk.strip(SETTINGS_UNAVAILABLE) in rows(last(s)), rows(last(s))
    assert_quiet(last(s))
    for action in (s.menu_sign, s.menu_broadcast, s.menu_invalidate):
        action()
        assert last(s)["popups"] == [["error", fk.strip(SETTINGS_UNAVAILABLE)]]
        assert_quiet(last(s))
    s.prepare()
    assert last(s)["popups"] == [["error", fk.strip(SETTINGS_UNAVAILABLE)]]
    assert s.auto() == {"returned": False}
    out = s.cli("will_check")["returned"]
    assert out["result"] == "delivery_settings_unavailable"
    with pytest.raises(delivery.DeliverySettingsUnavailable):
        s.set_date(fk.LATER)
    assert len(s.world.pushed) == pushed
    assert dict(d) == {}

    broken["on"] = False
    bw.init_will()
    assert bw.settings_unavailable is False and bw.will_settings is d
    assert (d["settings_version"], d["locktime"], d["migrated_from"]) == (
        2, fk.D0, "will")
    s.check()
    assert_check_ok(last(s))
    s.assert_global_unchanged()


def test_fail_closed_wallet_close_checks_nothing(make, monkeypatch):
    """With the settings unavailable the wallet closes without the closing
    Check (no dialog, no question): nothing is built, signed, sent or
    written, and the wallet's own dict stays empty."""
    s = make(locktime=fk.D0)
    s.check()

    def will_views(host, willitems=None):
        """The item reading fails."""
        raise RuntimeError("injected: the will items cannot be read")

    monkeypatch.setattr(engine_context, "will_views", will_views)
    d = update_from_old_plugin(s, OLD_GLOBAL)
    assert s.bw.settings_unavailable is True
    before = stored_states(s)
    s.close_wallet()
    assert last(s)["result"] == {"returned": None}
    assert_quiet(last(s))
    assert "dialogs_opened" not in last(s)          # no closing Check at all
    assert stored_states(s) == before and dict(d) == {}
    s.assert_global_unchanged()


# --------------------------------------------------- wallets, sources, idempotence

def test_two_wallets_keep_their_own_dates_and_a_new_wallet_gets_the_default(make):
    """Two wallets of one Electrum (one config, so one global "1054d"):
    each is migrated on the day it opens and keeps its own date; a Check in
    one never moves the other. A wallet created after the update (no heirs,
    no will) gets the default, today + 24 months (#127; it was 12) and fee
    20, never the frozen shared value."""
    a = make(legacy_global=dict(OLD_GLOBAL))
    da = wallet_dict(a)
    assert (da["locktime"], da["migrated_from"], da["baltx_fees"]) == (
        OWNER_DATE, "global", 13)
    a.check()
    a.clock.travel(5)
    b = a.second_wallet(seed=False, plugin_data=True, password=TEST_PASSWORD)
    c = a.second_wallet(seed=False, plugin_data=False, password=TEST_PASSWORD)
    db, dc = wallet_dict(b), wallet_dict(c)
    assert (db["locktime"], db["migrated_from"]) == (fk.ts(2029, 9, 3), "global")
    assert dc["locktime"] == fk.ts(2028, 10, 15)
    assert (dc["migrated_from"], dc["baltx_fees"], dc["threshold"]) == (
        "default", 20, "-365d")
    assert "legacy_locktime" not in dc and "note_kind" not in dc
    for s in (b, c):
        s.activate()
        s.check()
    for days in (0, 1, 1, 5):
        a.clock.travel(days)
        for s in (a, b, c):
            s.activate()
            s.check()
            assert_check_ok(last(s))
    assert da["locktime"] == OWNER_DATE
    assert db["locktime"] == fk.ts(2029, 9, 3)
    assert dc["locktime"] == fk.ts(2028, 10, 15)
    for s in (a, b, c):
        assert s.bw.will_settings is wallet_dict(s)
    a.assert_global_unchanged()


def test_no_will_and_a_shared_period_is_frozen_on_the_update_day(make):
    """No will yet, heirs, and the shared "1000d": the date is fixed on the
    update day (2029-07-06) and does not move when the wallet is opened
    again three days later; the first Check builds the will at that date."""
    old = {"locktime": "1000d", "baltx_fees": 13, "threshold": "-180d"}
    s = make(legacy_global=old)
    d = wallet_dict(s)
    frozen = fk.ts(2029, 7, 6)
    assert (d["locktime"], d["migrated_from"], d["legacy_locktime"]) == (
        frozen, "global", "1000d")
    assert d["note_kind"] == "converted"
    migrated_at = d["migrated_at"]
    s.clock.travel(3)
    s.reload_window()
    assert d["locktime"] == frozen and d["migrated_at"] == migrated_at
    s.check()
    step = last(s)
    assert question_texts(step) == ["Sign your will"]
    assert {day for day, flags, _url in item_table(s) if "VALID" in flags} == {
        "2029-07-06"}
    s.clock.travel(1)
    s.check()
    assert_check_ok(last(s))
    s.assert_global_unchanged()


def test_bal_061_one_year_with_a_will(make):
    """BAL 0.6.1 (advanced) kept "1y" in the global and built its will from
    it. The update keeps the date of that will (2027-10-10, note
    "converted"); the Checks two and seven days later are Ok (before, "1y"
    moved with the clock and read as a postpone)."""
    year = fk.ts(2027, 10, 10)
    s = make(mode="advanced", locktime=year)
    s.check()
    set_item_recipes(s, "1y")
    d = update_from_old_plugin(
        s, {"locktime": "1y", "baltx_fees": 13, "threshold": "-180d"},
        heirs_recipe="1y")
    assert (d["locktime"], d["migrated_from"], d["legacy_locktime"]) == (
        year, "will", "1y")
    assert d["note_kind"] == "converted"
    for days in (0, 2, 5):
        s.clock.travel(days)
        s.check()
        assert_check_ok(last(s))
        assert last(s)["settings"]["locktime"] == "2027-10-10"


def test_bal_02x_wallet_dict_without_a_marker(make):
    """A wallet dict of the BAL 0.2.x shape (``tx_fees``, ``threshold``,
    ``locktime``; no ``settings_version``), heirs and no global: it is not
    taken as current. The date comes from it (counted from the update day),
    the fee from its ``tx_fees``, the Check Alive from its ``threshold``;
    ``tx_fees`` is removed and an unknown key is left untouched."""
    old = {"locktime": "1y", "tx_fees": 15, "threshold": "-90d",
           "real_threshold": 1, "real_locktime": 2, "unknown_key": "kept"}
    s = make(legacy_wallet=old)
    d = wallet_dict(s)
    assert s.config.get("bal_will_settings") is None
    assert dict(d, migrated_at=None, anchor_valid=None) == {
        "settings_version": 2,
        "form": "fixed",
        "locktime": fk.ts(2027, 10, 10),
        "baltx_fees": 15,
        "threshold": "-90d",
        "migrated_from": "legacy_wallet",
        "migrated_at": None,
        "legacy_locktime": "1y",
        "note_kind": "converted",
        "note_new": fk.ts(2027, 10, 10),
        "anchor_valid": None,
        "unknown_key": "kept",
    }
    s.check()
    assert question_texts(last(s)) == ["Sign your will"]
    assert s.config.get("bal_will_settings") is None


def test_second_load_writes_nothing(make, monkeypatch):
    """Idempotent: once migrated, opening the wallet again in the window
    and in the command line writes no wallet DB patch at all, and the
    command line does not save the wallet file either."""
    s = owner_state(make)
    update_from_old_plugin(s, OLD_GLOBAL, heirs_recipe="1054d")
    patches = watch_patches(s, monkeypatch)
    saves = []
    real_save = s.wallet.save_db

    def save_db(*args, **kwargs):
        """Record every save of the wallet file."""
        saves.append(args)
        return real_save(*args, **kwargs)

    monkeypatch.setattr(s.wallet, "save_db", save_db)
    s.reload_window()
    cli_commands._call_locked(s.plugin, s.wallet, "delivery_show")
    BalController(s.plugin, s.wallet)
    assert patches == []
    assert saves == []


@pytest.mark.parametrize("field, value, words", [
    ("locktime", "1054d", "a period is not a delivery date"),
    ("locktime", "36m", "a period is not a delivery date"),
    ("locktime", 800_000, "not a delivery date"),          # a block height
    ("locktime", 2 ** 32, "not a delivery date"),
    ("baltx_fees", 0, "not a fee rate"),
    ("baltx_fees", "13", "not a fee rate"),
    ("threshold", "180d", "not a Check Alive value"),
    ("settings_version", 2, "not a delivery setting"),
    ("anchor_valid", "0" * 16, "not a delivery setting"),
    ("choice_pending", 1, "not a delivery setting"),
])
def test_the_validated_write_refuses(make, monkeypatch, field, value, words):
    """``context.set_will_setting`` is the one write of a delivery setting
    (the window's fields, the automatic rebuild, the command line). A
    period, a block height, a fee below 1, a Check Alive that is not
    "-Nd" or a bookkeeping key is refused, and nothing is written."""
    s = make(locktime=fk.D0)
    d = wallet_dict(s)
    before = dict(d)
    patches = watch_patches(s, monkeypatch)
    with pytest.raises(ValueError, match=words):
        engine_context.set_will_setting(s.bw, field, value)
    assert patches == [] and dict(d) == before
    assert s.bw.will_settings is d


def test_the_validated_write_stores_normalized_values(make, monkeypatch):
    """Accepted values are stored in the wallet's own dict, item by item:
    a date as an int, a fee rate exactly as given (13.001), a Check Alive
    as "-Nd". Writing the same value again writes nothing. A delivery date
    written while the question waits answers it; written with no question
    pending, it leaves a note not yet shown in place."""
    s = make(locktime=fk.D0)
    d = wallet_dict(s)
    assert engine_context.set_will_setting(s.bw, "locktime", float(fk.LATER)) == fk.LATER
    assert type(d["locktime"]) is int and d["locktime"] == fk.LATER
    s.set_fee(13.001)
    assert d["baltx_fees"] == 13.001
    s.set_threshold("-1y")
    assert d["threshold"] == "-365d"
    patches = watch_patches(s, monkeypatch)
    s.set_date(fk.LATER)
    s.set_fee(13.001)
    s.set_threshold("-365d")
    assert patches == []
    d["note_kind"] = "converted"
    s.set_date(fk.D0)
    assert d["note_kind"] == "converted"
    d[delivery.CHOICE_KEY] = 1
    d["note_old"] = fk.LATER
    s.set_date(fk.LATER)
    for key in (delivery.CHOICE_KEY,) + delivery.NOTE_KEYS:
        assert key not in d, key
    assert s.bw.will_settings is d
    s.assert_global_unchanged()


def test_gui_and_command_line_share_one_dict(make):
    """The window and the command line work on the same live dict of the
    wallet file: a fee stored by ``bal_delivery_set`` is the window's at
    once, and the other way round."""
    s = make(locktime=fk.D0)
    d = s.bw.will_settings
    assert d is wallet_dict(s)
    s.cli("delivery_set", fee=17)
    assert s.bw.will_settings is d and d["baltx_fees"] == 17
    assert BalController(s.plugin, s.wallet).will_settings is d
    s.set_fee(19)
    assert s.cli("delivery_show")["returned"]["baltx_fees"] == 19
    s.assert_global_unchanged()


def test_migration_runs_under_the_flow_lock(make, monkeypatch):
    """The migration runs while the wallet's flow lock is held, in the
    window and in the command line. When a command holds the lock, the
    window waits one second, migrates nothing and fails closed (settings not
    current), and ``init_will`` prepares them afterwards; settings that are
    already current are used as they are."""
    s = make(locktime=fk.D0)
    s.check()
    lock = flow_lock_for(s.wallet)
    held = []
    real = delivery.migration_values

    def migration_values(*args, **kwargs):
        """Record whether the flow lock is held, then migrate."""
        held.append(lock.locked())
        return real(*args, **kwargs)

    monkeypatch.setattr(delivery, "migration_values", migration_values)
    d = update_from_old_plugin(s, OLD_GLOBAL)
    write_items(d, {})
    cli_commands._call_locked(s.plugin, s.wallet, "delivery_show")
    assert held == [True, True]
    assert d["settings_version"] == 2

    def command(started, finish):
        """Another command working on this wallet (holds the lock)."""
        with lock:
            started.set()
            finish.wait(10)

    def while_busy(action):
        """Run ``action`` while another thread holds the flow lock."""
        started, finish = threading.Event(), threading.Event()
        thread = threading.Thread(target=command, args=(started, finish))
        thread.start()
        assert started.wait(10)
        try:
            action()
        finally:
            finish.set()
            thread.join(10)

    write_items(d, {})
    while_busy(s.reload_window)
    assert s.bw._settings_ready is False
    assert s.bw.settings_unavailable is True and s.bw.will_settings is not d
    assert dict(d) == {} and len(held) == 2          # nothing migrated
    s.bw.init_will()
    assert s.bw.settings_unavailable is False and s.bw.will_settings is d
    assert d["settings_version"] == 2 and held[-1] is True
    while_busy(s.reload_window)
    assert s.bw._settings_ready is False
    assert s.bw.settings_unavailable is False and s.bw.will_settings is d


def test_anchor_recorded_after_every_save_of_the_will(make, monkeypatch, tmp_path):
    """Plan 3.2: the anchor (fingerprint of the VALID items, earliest VALID
    date) is recorded whenever Safe21 saves the will: the Check's build,
    the signing from the Will menu, the GUI export and the command-line
    export. Only into the wallet's own dict."""
    s = make(locktime=fk.D0)
    d = wallet_dict(s)
    s.check(cancel_on={"Sign your will"})            # built, not signed
    assert d["anchor_valid"] == current_anchor(s)
    assert d["anchor_locktime"] == fk.D0
    calls = []
    real = engine_context.record_will_anchor

    def record_will_anchor(host):
        """Record which host saved its will, then record the anchor."""
        calls.append(host)
        return real(host)

    monkeypatch.setattr(engine_context, "record_will_anchor", record_will_anchor)
    s.menu_sign()                        # signs only: nothing is sent
    assert last(s)["pushed"] == [] and last(s)["broadcasts"] == []
    assert all("COMPLETE" in flags
               for _day, flags, _url in item_table(s) if "VALID" in flags)
    assert s.bw in calls
    calls.clear()
    s.bw.export_json_file(str(tmp_path / "will.json"))
    assert calls == [s.bw]
    calls.clear()
    returned = s.cli("export_will", str(tmp_path / "cli.json"))["returned"]
    assert returned["exported"] == len(s.bw.willitems)
    assert len(calls) == 1 and isinstance(calls[0], BalController)
    assert d["anchor_valid"] == current_anchor(s)
    # Never into another dict (a test host's plain dict, display values).
    plain = {"settings_version": 1, "locktime": fk.D0, "baltx_fees": 13}
    s.bw.will_settings = plain
    try:
        real(s.bw)
    finally:
        s.bw.will_settings = d
    assert plain == {"settings_version": 1, "locktime": fk.D0, "baltx_fees": 13}
    s.assert_global_unchanged()


def test_no_plugin_code_uses_the_global_settings_object():
    """Rule I1 in the code: no module of the plugin reads or writes the
    global through ``WILL_SETTINGS.get/set/reset`` (``get`` hands back the
    MUTABLE default when the key is missing, so a later change of that dict
    could reach the global). The global is read raw, only as a migration
    source (``bal.engine.context._global_raw``)."""
    import re

    root = os.path.join(os.path.dirname(__file__), os.pardir, "bal")
    pattern = re.compile(r"WILL_SETTINGS\s*\.\s*(get|set|reset)\s*\(")
    found = []
    for folder, _dirs, files in os.walk(root):
        for name in files:
            if name.endswith(".py"):
                path = os.path.join(folder, name)
                with open(path, encoding="utf-8") as f:
                    for number, line in enumerate(f, 1):
                        if pattern.search(line):
                            found.append("{}:{}".format(name, number))
    assert found == []


def test_note_kept_when_showing_it_fails(make, monkeypatch):
    """The note's keys are deleted only after the message box returns: when
    showing it fails, it is shown at the next opening, then never again."""
    s = make(locktime=fk.D0)
    s.check()
    d = update_from_old_plugin(s, OLD_GLOBAL)
    assert d["note_kind"] == "converted"

    def broken(text):
        """A message box that cannot be shown."""
        raise RuntimeError("injected: no screen")

    monkeypatch.setattr(s.bw, "show_message", broken, raising=False)
    s.bw.show_delivery_note()
    assert d["note_kind"] == "converted" and d["note_new"] == fk.D0
    monkeypatch.delattr(s.bw, "show_message")
    s.show_note()
    assert last(s)["popups"] == [
        ["message", "Your delivery date is now fixed: 23/09/2032."]]
    assert "note_kind" not in d and "note_new" not in d
    s.show_note()
    assert last(s)["popups"] == []


# ------------------------------------ funds safety (#123 review FS-1, FS-2)

# The old shared value of a later date asked for before #123.
LATER_GLOBAL = {"locktime": fk.LATER, "baltx_fees": 13, "threshold": "-180d"}


def memory_flags(s):
    """``{txid: flags}`` of the window's will items (in memory)."""
    return {
        wid: tuple(f for f in _STATE_FLAGS if w.get_status(f))
        for wid, w in s.bw.willitems.items()
    }


def stored_flags(s):
    """``{txid: flags}`` of the will as stored in the wallet file."""
    return {wid: flags for wid, (flags, _tx) in stored_states(s).items()}


def stored_dates(s):
    """``{(date, flags)}`` of the will as stored in the wallet file (one
    entry for the copies of the three will-executors)."""
    return {
        (fk.fmt(tx_from_any(raw).locktime), flags)
        for flags, raw in stored_states(s).values()
    }


def pushed_dates(s, since):
    """The dates of the transactions sent to the will-executors after the
    first ``since`` pushes of the session, sorted."""
    return sorted({fk.fmt(tx_from_any(raw).locktime)
                   for _url, _wid, raw in s.world.pushed[since:]})


def wallet_synchronized(s, monkeypatch, value=False):
    """Make ``wallet.is_up_to_date()`` answer what the returned dict holds
    (``["now"]``, ``value`` first): the test decides when Electrum has
    finished synchronizing."""
    synced = {"now": value}
    monkeypatch.setattr(s.wallet, "is_up_to_date", lambda: synced["now"])
    return synced


def postponed_from_the_will_tab(make, **kw):
    """The manual postpone that the Prepare message itself describes (FS-1).

    A will at D0 is signed and sent (the first Check). The owner sets the
    later date LATER: a paid postpone. Will tab > Prepare: the postpone
    message opens the classic invalidation window, where it is signed and
    broadcast (miner fee). Prepare again builds the LATER will in memory
    only (Prepare never saves the will). Then Will tab > Sign. Returns the
    session, the wallet's own dict and the txids of the paid invalidation.
    """
    s = make(locktime=fk.D0, **kw)
    s.check()
    d = wallet_dict(s)
    s.set_date(fk.LATER)
    s.prepare()
    paid = [t for k, t in s.world.broadcast_log if k.startswith("invalidation")]
    assert [k for k, _t in last(s)["broadcasts"]] == ["invalidation(transaction window)"]
    s.prepare()
    assert {(day, flags) for day, flags, _url in item_table(s)} == {
        ("2032-09-23", ("COMPLETE", "PUSHED", "INVALIDATED")),
        ("2033-09-23", ("VALID",)),
    }
    s.menu_sign()
    return s, d, paid


def reopen_from_the_file(s, paid):
    """Electrum is killed and started again: the wallet FILE is read into a
    fresh wallet object (a copy of the file, decrypted with the test
    password) and a fresh window, as at the next start; Electrum then
    registers again the invalidation it had broadcast (mempool). The session
    points at them."""
    copy = s.wallet.storage.path + ".crashcopy"
    shutil.copyfile(s.wallet.storage.path, copy)
    storage = WalletStorage(copy)
    if storage.is_encrypted():
        storage.decrypt(TEST_PASSWORD)
    wallet = Wallet(WalletDB(storage.read(), storage=storage, upgrade=True),
                    config=s.config)
    s.wallet = wallet
    s.bw = fk.FlowWindow(s.plugin, wallet, s.user, s.world)
    s.bw.willitems = {wid: WillItem(w, wallet=wallet) for wid, w in s.bw.will.items()}
    for txid in paid:
        wallet.adb.add_transaction(tx_from_any(s.world.broadcast[txid]))
        wallet.adb.add_unverified_or_unconfirmed_tx(txid, 0)
    return wallet.db.get_dict("will_settings")


def assert_nothing_back_at_d0(s, d, step, sent):
    """The Check ``step`` sent nothing dated D0 and broadcast nothing; the
    date stays LATER and the will in force is the LATER one."""
    assert "2032-09-23" not in pushed_dates(s, sent)
    assert step["broadcasts"] == []
    assert "Invalidate your old will" not in question_texts(step)
    assert d["locktime"] == fk.LATER
    assert {day for day, flags, _url in item_table(s) if "VALID" in flags} == {
        "2033-09-23"}


@pytest.mark.parametrize("trigger", ["cli_delivery_show", "reopen"])
def test_will_tab_sign_after_a_manual_postpone_keeps_the_later_date(make, trigger):
    """FS-1 (funds lens): the Will tab's Sign now saves the WHOLE will. The
    stored will is the one in memory (the D0 will INVALIDATED, the LATER
    will VALID and signed) and the anchor describes it. Then any command
    on the open wallet (even the read-only ``bal_delivery_show``) or a
    reopening leaves the date at LATER with no note, and the next Check
    sends nothing at D0 and pays nothing.

    Before the fix the Sign wrote only the signed items: the stale VALID
    flag of the D0 will stayed stored, the load took it for a change made
    by another program, the date went back to D0 (note "outside_change")
    and the next Check signed and sent a will one year EARLIER than the
    date the owner had paid for."""
    s, d, _paid = postponed_from_the_will_tab(make)
    assert stored_flags(s) == memory_flags(s)
    assert stored_dates(s) == {
        ("2032-09-23", ("COMPLETE", "PUSHED", "INVALIDATED")),
        ("2033-09-23", ("VALID", "COMPLETE")),
    }
    assert d["anchor_valid"] == current_anchor(s)
    assert d["anchor_locktime"] == fk.LATER
    sent = len(s.world.pushed)
    s.menu_broadcast()
    assert pushed_dates(s, sent) == ["2033-09-23"]
    if trigger == "cli_delivery_show":
        out = s.cli("delivery_show")["returned"]
        assert (out["date"], out["note"]) == ("2033-09-23", None)
    else:
        s.reload_window()
    assert d["locktime"] == fk.LATER and delivery.pending_note(d) is None
    sent = len(s.world.pushed)
    s.check()
    assert_nothing_back_at_d0(s, d, last(s), sent)
    s.assert_global_unchanged()


def test_will_tab_sign_after_a_manual_postpone_survives_a_crash(make):
    """FS-1: Electrum is killed right after the Will tab's Sign and
    Broadcast (no wallet close). The wallet FILE, read again at the next
    start, holds the will as it was in memory: the date stays LATER with no
    note, and the next Check sends nothing at D0 (before the fix: the file
    held the stale VALID D0 will, the date went back to D0 and the Check
    sent a will at D0)."""
    s, _d, paid = postponed_from_the_will_tab(make)
    s.menu_broadcast()
    assert s.plugin.SAVE_HISTORY.get()   # the Sign wrote the file (history)
    d = reopen_from_the_file(s, paid)
    assert d["locktime"] == fk.LATER and delivery.pending_note(d) is None
    assert stored_dates(s) == {
        ("2032-09-23", ("COMPLETE", "PUSHED", "INVALIDATED")),
        ("2033-09-23", ("VALID", "COMPLETE")),
    }
    sent = len(s.world.pushed)
    s.check()
    assert_nothing_back_at_d0(s, d, last(s), sent)


def test_will_tab_sign_after_a_manual_postpone_automatic_rebuild(make):
    """FS-1, a wallet without password and the automatic rebuild on: after
    the read-only ``bal_delivery_show`` the automatic rebuild signed a will
    at D0 with no question at all before the fix. Now no will at D0 is
    signed and the date stays LATER."""
    s, d, _paid = postponed_from_the_will_tab(make, password=None)
    s.menu_broadcast()
    s.cli("delivery_show")
    assert d["locktime"] == fk.LATER
    s.auto()
    assert last(s)["broadcasts"] == []
    assert not [w for w in s.bw.willitems.values()
                if fk.fmt(w.tx.locktime) == "2032-09-23" and w.get_status("VALID")]
    assert d["locktime"] == fk.LATER


def test_the_anchor_is_taken_from_the_stored_will(make):
    """FS-1, defence in depth: ``context.record_will_anchor`` fingerprints
    the will AS STORED, never the items in memory, so an anchor can never
    describe a set the wallet does not hold. Here one item is invalidated
    in memory only (as Prepare leaves it: it never saves): the anchor still
    describes the stored will, and the next load changes nothing."""
    s = make(locktime=fk.D0)
    s.check()
    d = wallet_dict(s)
    stored_anchor = d["anchor_valid"]
    s.bw.willitems[sorted(s.bw.willitems)[0]].set_status("INVALIDATED", True)
    assert current_anchor(s) != stored_anchor
    engine_context.record_will_anchor(s.bw)
    assert d["anchor_valid"] == stored_anchor
    assert d["anchor_locktime"] == fk.D0
    s.reload_window()
    assert d["locktime"] == fk.D0 and delivery.pending_note(d) is None


def test_export_saves_the_whole_will(make, tmp_path):
    """The GUI export saves the will through ``save_willitems`` (FS-1, same
    principle as the Sign): after a Prepare that was never saved, the
    stored will is exactly the will in memory, transactions as text, and
    the anchor describes it; the next load changes nothing."""
    s = make(locktime=fk.D0)
    s.check()
    d = wallet_dict(s)
    s.set_date(fk.LATER)
    s.prepare()
    s.prepare()
    s.bw.export_json_file(str(tmp_path / "will.json"))
    assert stored_flags(s) == memory_flags(s)
    stored = s.wallet.db.get_dict("will")
    assert set(stored) == set(s.bw.willitems)
    assert all(isinstance(item["tx"], str) for item in stored.values())
    assert d["anchor_valid"] == current_anchor(s)
    with open(tmp_path / "will.json", encoding="utf-8") as f:
        assert set(json.load(f)) == set(s.bw.willitems)
    s.reload_window()
    assert d["locktime"] == fk.LATER and delivery.pending_note(d) is None


def test_unsigned_earlier_drafts_left_by_another_program_are_not_followed(make):
    """FS-1 (migration lens): a will at LATER is signed and sent. Another
    program (BAL, or a ZIP before #123) reads the frozen shared date D0,
    prepares an EARLIER will and its "Sign your will" is cancelled: three
    unsigned VALID drafts at D0, the signed LATER will REPLACED (the
    will-executors still hold it). Safe21 makes that state itself here and
    then puts the wallet's own dict back: the other program never writes
    it. Back on #123 the drafts are not "the will in force": the date stays
    LATER, no note, only the anchor is recorded (before the fix the date
    became D0 with a note calling the drafts the will in force)."""
    s = make(locktime=fk.LATER)
    s.check()
    d = wallet_dict(s)
    old_dict = dict(d)
    s.set_date(fk.D0)
    s.check(cancel_on={"Sign your will"})
    assert "Sign your will" in question_texts(last(s))
    write_items(d, old_dict)
    assert stored_dates(s) == {
        ("2032-09-23", ("VALID",)),
        ("2033-09-23", ("COMPLETE", "PUSHED", "REPLACED")),
    }
    s.reload_window()
    assert d["locktime"] == fk.LATER
    assert delivery.pending_note(d) is None
    assert d["anchor_valid"] == current_anchor(s)
    s.show_note()
    assert last(s)["popups"] == []
    s.assert_global_unchanged()


def test_update_after_an_unseen_paid_postpone_builds_at_the_later_date(
    make, monkeypatch
):
    """FS-2, the reviewer's probe: before #123 the shared date was LATER;
    the Check invalidated the D0 will (miner fee) and stopped, but the
    wallet had not registered the invalidation, so the D0 will was saved
    VALID, signed and sent. Then #123: the migration takes D0 from the
    stored flags, with the note "later_pending" and the later wish kept.

    While the wallet has not registered the invalidation, the D0 will
    looks in force: nothing is done and the wish waits (showing the note
    does not drop it). Once Electrum registers the invalidation and is
    synchronized, the next Check finds no VALID item, stores LATER (no new
    fee) and sends the will at LATER, as the code before #123 did. Before
    the fix it rebuilt and sent the will at D0, the date already paid to
    leave."""
    s = make(locktime=fk.D0)
    s.check()
    synced = wallet_synchronized(s, monkeypatch)
    s.set_date(fk.LATER)
    s.world.wallet_lags = True
    s.check()
    paid = [t for k, t in s.world.broadcast_log if k.startswith("invalidation")]
    assert len(paid) == 1
    s.bw.save_willitems()        # the wallet close saves the will after its Check
    assert stored_dates(s) == {("2032-09-23", ("VALID", "COMPLETE", "PUSHED"))}
    d = update_from_old_plugin(s, LATER_GLOBAL)
    assert (d["locktime"], d["migrated_from"], d["note_kind"]) == (
        fk.D0, "will", "later_pending")
    assert d[delivery.LATER_WISH_KEY] == fk.LATER
    assert delivery.CHOICE_KEY not in d
    s.show_note()
    assert delivery.pending_note(d) is None
    assert d[delivery.LATER_WISH_KEY] == fk.LATER

    s.check()                    # the invalidation is still not registered
    step = last(s)
    assert step["pushed"] == [] and step["broadcasts"] == []
    assert d["locktime"] == fk.D0 and d[delivery.LATER_WISH_KEY] == fk.LATER

    s.world.wallet_lags = False  # Electrum registers what it had broadcast
    for txid in paid:
        s.wallet.adb.add_transaction(tx_from_any(s.world.broadcast[txid]))
        s.wallet.adb.add_unverified_or_unconfirmed_tx(txid, 0)
    synced["now"] = True
    s.reload_window()
    sent = len(s.world.pushed)
    s.check()
    step = last(s)
    assert pushed_dates(s, sent) == ["2033-09-23"]
    assert step["broadcasts"] == []
    assert "Invalidate your old will" not in question_texts(step)
    assert d["locktime"] == fk.LATER and delivery.LATER_WISH_KEY not in d
    s.clock.travel(1)
    s.check()
    assert_check_ok(last(s))
    s.assert_global_unchanged()


def test_update_after_a_cancelled_postpone_keeps_the_will_and_drops_the_wish(
    make, monkeypatch
):
    """FS-2, the edge case: before #123 the owner asked for LATER and
    pressed Cancel at "Invalidate your old will" (nothing paid). The D0 will
    is really in force. After #123 the later wish waits while the wallet is
    not synchronized, then is dropped: the date stays D0, every Check is Ok
    and nothing is signed, sent or paid."""
    s = make(locktime=fk.D0)
    s.check()
    synced = wallet_synchronized(s, monkeypatch)
    s.set_date(fk.LATER)
    s.check(cancel_on={"Invalidate your old will"})
    assert "Invalidate your old will" in question_texts(last(s))
    assert last(s)["broadcasts"] == []
    d = update_from_old_plugin(s, LATER_GLOBAL)
    assert (d["locktime"], d["note_kind"], d[delivery.LATER_WISH_KEY]) == (
        fk.D0, "later_pending", fk.LATER)
    before = stored_states(s)
    s.check()
    assert_check_ok(last(s))
    assert d[delivery.LATER_WISH_KEY] == fk.LATER       # not synchronized: kept
    synced["now"] = True
    s.check()
    assert_check_ok(last(s))
    assert delivery.LATER_WISH_KEY not in d and d["locktime"] == fk.D0
    s.clock.travel(1)
    s.check()
    assert_check_ok(last(s))
    s.close_wallet()
    assert_quiet(last(s))
    assert stored_states(s) == before
    s.assert_global_unchanged()


def test_a_date_chosen_by_the_owner_replaces_the_later_wish(make, monkeypatch):
    """Every delivery date written through the one validated write drops
    the later wish: the answer to the "which date?" question, the Will
    tab's date field and ``bal_delivery_set``."""
    s, d = edge_state(make)
    assert d[delivery.LATER_WISH_KEY] == fk.ts(2029, 7, 15)
    s.user.delivery_answer = "keep"
    s.check()
    assert delivery.LATER_WISH_KEY not in d and d["locktime"] == fk.ts(2029, 7, 5)

    s = make(locktime=fk.D0)
    s.check()
    s.set_date(fk.LATER)
    s.check(cancel_on={"Invalidate your old will"})
    d = update_from_old_plugin(s, LATER_GLOBAL)
    assert d[delivery.LATER_WISH_KEY] == fk.LATER
    s.bw.update_setting_widgets(fk.ts(2034, 1, 1), "locktime")
    assert delivery.LATER_WISH_KEY not in d and d["locktime"] == fk.ts(2034, 1, 1)
    d[delivery.LATER_WISH_KEY] = fk.LATER
    out = s.cli("delivery_set", date="2034-06-01")["returned"]
    assert out["locktime"] == fk.ts(2034, 6, 1), out
    assert delivery.LATER_WISH_KEY not in d


def test_multiverse_wallet_gets_no_pending_postpone(make):
    """Low finding (migration lens): with multiverse on, each heir carries
    its own date and the shared value never drove them. The engine passes
    the plugin's setting to the migration: a later shared date gives only
    the informative note, no question and no later wish."""
    s = make(locktime=fk.D0)
    s.check()
    s.plugin.ENABLE_MULTIVERSE.set(True)
    d = update_from_old_plugin(s, LATER_GLOBAL)
    assert (d["locktime"], d["note_kind"], d["note_old"]) == (
        fk.D0, "date_differs", fk.LATER)
    assert delivery.CHOICE_KEY not in d and delivery.LATER_WISH_KEY not in d


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
