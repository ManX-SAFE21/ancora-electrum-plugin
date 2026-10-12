"""A rebuilt will identical to the signed one keeps its signatures (CHANGELOG #122).

Found in the owner's wallet on 2026-10-11 and reproduced with
``tests/flowkit.py``: a will built from a FIXED delivery date was signed,
sent and verified; then the delivery setting became the period giving the
same day ("1054d", the Raw/Date switch of advanced mode). At wallet close
the check rebuilt the will (the #81 rule) and the transactions were
identical to the signed ones (same txids). ``Will.update_will`` reused an
old item only when its heirs were written exactly the same way ("Option A",
CHANGELOG #17, helper ``_same_heirs``); the date was written another way,
so the fresh UNSIGNED copies replaced the signed ones. The owner closed the
password window and the will was saved unsigned (the will-executors kept
the signed will).

Now a rebuilt transaction with the txid of a VALID old item keeps the old
item when the will-executor URL is the same (or there is none): it is the
same transaction, so its signatures are valid and its state (signed, sent,
verified) is right. The new heirs, will-executor, description and fee rate
are written on it. The same transaction for ANOTHER will-executor URL keeps
the fresh item, so it is sent there, but with the signed transaction (no
password). Until #128 a replaced old item was never brought back; since
#128 the will in force that an unsigned draft replaced comes back under five
conditions (see "bringing back" below), and every build first drops the
drafts (never signed nor sent).

Since CHANGELOG #123 a period is no longer accepted as the delivery
setting, so the owner's case is reproduced at the engine: the period is
written straight into the heirs before the build (as heirs may still carry
one: multiverse, an imported or older will). The rebuild rule is the same.

Wallets, coins and will-executors come from ``tests/flowkit.py`` (three
will-executors, throw-away test wallet with a password, fixed clock).

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_core_identical_rebuild.py
"""

import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

import flowkit as fk
from PyQt6.QtWidgets import QApplication

from bal.core.will import Will, WillItem
from bal.engine import build

# Kept at module level: an unreferenced application object is destroyed at
# once, and creating a widget afterwards aborts Python.
_APP = QApplication.instance() or QApplication(sys.argv)

# Test value: the password of the throw-away test wallets that have one.
TEST_PASSWORD = "flowkit-test"

# The state of a will that is signed, sent and verified.
SENT = ("VALID", "COMPLETE", "PUSHED", "CHECKED")


@pytest.fixture
def flow_session(monkeypatch):
    """``tests/flowkit.py`` sessions with Electrum's asyncio loop."""
    fk.ensure_event_loop()
    made = []

    def make(**kw):
        """Start one session with three will-executors; close it later."""
        s = fk.Session(monkeypatch, **kw)
        s.world.add_executor()
        made.append(s)
        return s

    yield make
    for s in made:
        s.close()
    fk.stop_event_loop()


def _sent_will(make):
    """A will built from the fixed date ``fk.D0``, signed, sent, verified.

    Returns the session and the VALID items by txid (one per executor).
    """
    s = make(mode="basic", locktime=fk.D0, password=TEST_PASSWORD)
    s.check()
    valid = {wid: w for wid, w in s.bw.willitems.items() if w.get_status("VALID")}
    assert len(valid) == 3
    assert all(w.get_status(flag) for w in valid.values() for flag in SENT)
    return s, valid


def _period_giving(s, locktime):
    """The period ("Nd") that gives ``locktime`` today (the Date -> Raw
    switch of advanced mode, CHANGELOG #81)."""
    today = datetime.fromtimestamp(s.clock.now, tz=timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0)
    return "{}d".format((int(locktime) - int(today.timestamp())) // fk.DAY)


def _heirs_carry(s, recipe):
    """Write ``recipe`` as the delivery of every heir, straight into the
    wallet's heirs.

    CHANGELOG #123: the delivery setting can no longer be a period (the
    validated write refuses it, the Raw editor is hidden), so the owner's
    #122 case is reproduced at the engine: after ``init_class_variables``
    (which, multiverse off, gives every heir the stored date) the heirs
    carry the period, and the build reads them as before #123.
    """
    for name in list(s.bw.heirs):
        heir = list(s.bw.heirs[name])
        heir[2] = recipe
        s.bw.heirs[name] = heir


def _recipes(item):
    """The delivery recipes of the real heirs of a will item."""
    return {h[2] for name, h in item.heirs.items() if not name.startswith('w!ll3x3c"')}


def _fresh_copy(item, heirs=None, we=None, description=None, fees=None):
    """A freshly built, unsigned-state item for the same transaction.

    Same transaction text (the txid does not depend on the signatures),
    status "New" with no flag set, other data as given.
    """
    return WillItem({
        "tx": str(item.tx),
        "heirs": heirs if heirs is not None else {n: list(h) for n, h in item.heirs.items()},
        "willexecutor": we if we is not None else dict(item.we),
        "status": "New",
        "description": description if description is not None else item.description,
        "baltx_fees": fees if fees is not None else item.tx_fees,
        "time": 1.0,
    }, _id=item._id)


def test_identical_rebuild_keeps_the_signed_items(flow_session):
    """The owner's case at the engine: the date written as the equal
    period, then the will is rebuilt. Same txids; every item keeps its
    signed transaction, its state and its history, and carries the period.

    Since #123 the period is written into the heirs (:func:`_heirs_carry`);
    as a delivery setting it is refused."""
    s, valid = _sent_will(flow_session)
    raw = {wid: str(w.tx) for wid, w in valid.items()}
    history = {wid: w.status for wid, w in valid.items()}
    period = _period_giving(s, Will.get_min_locktime(s.bw.willitems))
    with pytest.raises(ValueError, match="period"):
        s.set_date(period)
    s.bw.init_class_variables()
    _heirs_carry(s, period)             # every heir gets the period
    build.build_will(s.bw, with_creation_date=True)
    now = {wid: w for wid, w in s.bw.willitems.items() if w.get_status("VALID")}
    assert set(now) == set(valid)       # identical transactions
    for wid, w in now.items():
        assert all(w.get_status(flag) for flag in SENT), w.status
        assert str(w.tx) == raw[wid]    # the signed transaction
        assert w.status == history[wid]
        assert _recipes(w) == {period}  # the new data


def test_the_old_item_gets_the_new_data(flow_session):
    """``update_will`` keeps the old item object for the same txid and
    writes on it the new heirs, will-executor, description and fee rate;
    the signing time and the state stay."""
    s, valid = _sent_will(flow_session)
    wid, old = next(iter(valid.items()))
    old_time = old.time
    heirs = {n: list(h) for n, h in old.heirs.items()}
    for name, h in heirs.items():
        if not name.startswith('w!ll3x3c"'):
            h[2] = "2175d"
    we = dict(old.we)
    we["info"] = "new info"
    fresh = _fresh_copy(old, heirs=heirs, we=we, description="alice\nbob (new)",
                        fees=13.001)
    new_will = {wid: fresh}
    Will.update_will(s.bw.willitems, new_will)
    assert new_will[wid] is old
    assert all(old.get_status(flag) for flag in SENT)
    assert old.heirs == heirs
    assert old.we["info"] == "new info"
    assert old.description == "alice\nbob (new)"
    assert old.tx_fees == 13.001        # else every Check: "fee rate changed"
    assert old.time == old_time


def test_the_same_transaction_for_another_executor_is_sent_signed(flow_session):
    """The same transaction for another will-executor URL (same fee address
    and fee, e.g. an executor that moved): the fresh item stays, so the will
    is sent to that URL, but it carries the signed transaction (no password)."""
    s, valid = _sent_will(flow_session)
    wid, old = next(iter(valid.items()))
    we = dict(old.we)
    we["url"] = "https://mirror.test"
    fresh = _fresh_copy(old, we=we)
    new_will = {wid: fresh}
    Will.update_will(s.bw.willitems, new_will)
    assert new_will[wid] is fresh
    assert fresh.get_status("COMPLETE")
    assert str(fresh.tx) == str(old.tx)
    assert not fresh.get_status("PUSHED")
    assert not fresh.get_status("CHECKED")


def test_an_unsigned_old_item_for_another_executor_stays_unsigned(flow_session):
    """Only a signed old transaction is reused for the other URL."""
    s, valid = _sent_will(flow_session)
    wid, old = next(iter(valid.items()))
    unsigned = _fresh_copy(old)             # VALID, not signed
    s.bw.willitems[wid] = unsigned
    we = dict(old.we)
    we["url"] = "https://mirror.test"
    fresh = _fresh_copy(old, we=we)
    new_will = {wid: fresh}
    Will.update_will(s.bw.willitems, new_will)
    assert new_will[wid] is fresh
    assert not fresh.get_status("COMPLETE")


# ------------------------------------------------- bringing back (CHANGELOG #128)
#
# Until #128 a REPLACED old item was never brought back: the fresh copy of
# the same transaction took its place (the test
# ``test_a_replaced_item_is_not_brought_back``). Since #128 the will in force
# that an unsigned draft had replaced comes back, keeping its signatures and
# its sent / verified state, only when ALL of these hold: (a) same txid;
# (b) the old item is signed (COMPLETE) and sent (PUSHED); (c) it is still
# executable; (d) no other signed or sent, still executable item on the same
# coins is dated earlier; (e) the wallet is synchronized. Otherwise the fresh
# item takes its place, as before, but a signed transaction is never
# replaced by an UNSIGNED copy: the fresh item gets the signed transaction
# (no password) and is sent again (the #122 rule for another URL).


def _replaced(s, valid, monkeypatch, synced=True):
    """The first item, marked REPLACED (as a draft leaves it), on a wallet
    that says it is synchronized (or not)."""
    monkeypatch.setattr(s.wallet, "is_up_to_date", lambda: synced)
    wid, old = next(iter(valid.items()))
    old.set_status("REPLACED", True)
    return wid, old


def _assert_not_brought_back(new_will, wid, old):
    """The old item is not brought back: it stays REPLACED; the fresh item
    takes its place with the signed transaction, not sent."""
    new = new_will[wid]
    assert new is not old
    assert old.get_status("REPLACED") and not old.get_status("VALID")
    assert new.get_status("COMPLETE") and str(new.tx) == str(old.tx)
    assert not new.get_status("PUSHED") and not new.get_status("CHECKED")


def test_a_replaced_will_in_force_is_brought_back(flow_session, monkeypatch):
    """All five conditions hold: the old item comes back, VALID again, with
    its signed transaction, its sent and verified state and the new data."""
    s, valid = _sent_will(flow_session)
    wid, old = _replaced(s, valid, monkeypatch)
    raw = str(old.tx)
    fresh = _fresh_copy(old, description="new description")
    new_will = {wid: fresh}
    result = Will.update_will(s.bw.willitems, new_will, wallet=s.wallet)
    assert new_will[wid] is old
    assert result["brought_back"] == [wid]
    assert all(old.get_status(flag) for flag in SENT)
    assert not old.get_status("REPLACED")
    assert str(old.tx) == raw
    assert old.description == "new description"


def test_not_brought_back_another_transaction(flow_session, monkeypatch):
    """(a) Another transaction (another date): nothing comes back, the new
    one is kept as built."""
    s, valid = _sent_will(flow_session)
    wid, old = _replaced(s, valid, monkeypatch)
    fresh = _fresh_copy(old)
    fresh.tx.locktime = int(old.tx.locktime) - 2 * fk.DAY
    nid = fresh.tx.txid()
    new_will = {nid: fresh}
    Will.update_will(s.bw.willitems, new_will, wallet=s.wallet)
    assert new_will == {nid: fresh}
    assert old.get_status("REPLACED") and not old.get_status("VALID")


@pytest.mark.parametrize("clear", ["PUSHED", "COMPLETE"])
def test_not_brought_back_unless_signed_and_sent(flow_session, monkeypatch, clear):
    """(b) Signed but never sent, or sent but not signed: not brought back."""
    s, valid = _sent_will(flow_session)
    wid, old = _replaced(s, valid, monkeypatch)
    old.STATUS[clear][1] = False
    old.STATUS["CHECKED"][1] = False
    new_will = {wid: _fresh_copy(old)}
    Will.update_will(s.bw.willitems, new_will, wallet=s.wallet)
    new = new_will[wid]
    assert new is not old
    assert old.get_status("REPLACED") and not old.get_status("VALID")
    # Signed, never sent: the signature is kept (no password asked again).
    assert new.get_status("COMPLETE") == (clear == "PUSHED")
    assert not new.get_status("PUSHED")


def test_not_brought_back_when_no_longer_executable(flow_session, monkeypatch):
    """(c) An input spent by a broadcast transaction: never executable."""
    s, valid = _sent_will(flow_session)
    wid, old = _replaced(s, valid, monkeypatch)
    spent = {i.prevout.to_str() for i in old.tx.inputs()}
    monkeypatch.setattr(Will, "_funding_really_spent",
                        staticmethod(lambda wallet, inp: inp in spent))
    new_will = {wid: _fresh_copy(old)}
    Will.update_will(s.bw.willitems, new_will, wallet=s.wallet)
    assert new_will.get(wid) is not old
    assert old.get_status("REPLACED") and not old.get_status("VALID")


@pytest.mark.parametrize("flag", ["VALID", "REPLACED"])
def test_not_brought_back_with_an_earlier_signed_will(flow_session, monkeypatch, flag):
    """(d) Another signed and sent item on the same coins, dated earlier:
    the will-executors would execute that one, so the old date would be
    false.

    In a real build the anticipation already dates the new transaction on
    or before that earlier item (``WillItem.set_anticipate`` never leaves
    it later than a will it shares coins with), so it cannot have the old
    item's txid: (d) is a safety net, tested here alone with the
    anticipation switched off."""
    monkeypatch.setattr(Will, "search_anticipate_rec",
                        staticmethod(lambda will, old_inputs: None))
    s, valid = _sent_will(flow_session)
    wid, old = _replaced(s, valid, monkeypatch)
    earlier = old.copy()
    earlier.tx.locktime = int(old.tx.locktime) - 3 * fk.DAY
    earlier._id = earlier.tx.txid()
    earlier.set_status("REPLACED", flag == "REPLACED")
    earlier.STATUS["VALID"][1] = flag == "VALID"
    s.bw.willitems[earlier._id] = earlier
    new_will = {wid: _fresh_copy(old)}
    Will.update_will(s.bw.willitems, new_will, wallet=s.wallet)
    _assert_not_brought_back(new_will, wid, old)


def test_not_brought_back_on_a_wallet_not_synchronized(flow_session, monkeypatch):
    """(e) The wallet does not know yet whether a coin was spent."""
    s, valid = _sent_will(flow_session)
    wid, old = _replaced(s, valid, monkeypatch, synced=False)
    new_will = {wid: _fresh_copy(old)}
    Will.update_will(s.bw.willitems, new_will, wallet=s.wallet)
    _assert_not_brought_back(new_will, wid, old)


def test_without_a_wallet_nothing_is_brought_back(flow_session):
    """No wallet given: synchronization unknown, as (e)."""
    s, valid = _sent_will(flow_session)
    wid, old = next(iter(valid.items()))
    old.set_status("REPLACED", True)
    new_will = {wid: _fresh_copy(old)}
    Will.update_will(s.bw.willitems, new_will)
    _assert_not_brought_back(new_will, wid, old)


def test_a_replaced_draft_is_overwritten(flow_session, monkeypatch):
    """A REPLACED item never signed nor sent holds nothing: the fresh copy
    of the same transaction takes its place, as before #128."""
    s, valid = _sent_will(flow_session)
    wid, old = _replaced(s, valid, monkeypatch)
    unsigned = _fresh_copy(old)
    unsigned.set_status("REPLACED", True)
    s.bw.willitems[wid] = unsigned
    fresh = _fresh_copy(old)
    new_will = {wid: fresh}
    Will.update_will(s.bw.willitems, new_will, wallet=s.wallet)
    assert new_will[wid] is fresh


# ------------------------------------------------- drafts (CHANGELOG #128)

def test_drafts_are_dropped_signed_and_partially_signed_items_are_not(flow_session):
    """Every build drops the drafts first; a signed item and a partially
    signed one (multisig: its own state, a co-signer may finish it) stay."""
    s, valid = _sent_will(flow_session)
    wid, signed = next(iter(valid.items()))
    draft = _fresh_copy(signed)
    partial = _fresh_copy(signed)
    partial.set_status("PARTIALLY_SIGNED", True)
    counted = _fresh_copy(signed)
    counted.sigs_have, counted.sigs_required = 1, 2
    for old_will, kept in (({"d": draft}, set()),
                           ({"p": partial}, {"p"}),
                           ({"c": counted}, {"c"}),
                           ({"s": signed}, {"s"})):
        result = Will.update_will(old_will, {})
        assert set(old_will) == kept
        assert result["dropped"] == ([] if kept else ["d"])


def test_a_backup_transaction_keeps_its_signature(flow_session):
    """A backup transaction (no will-executor) rebuilt identical: kept signed."""
    s = flow_session(mode="basic", locktime=fk.D0, password=TEST_PASSWORD)
    s.plugin.NO_WILLEXECUTOR.set(True)
    for we in s.world.executors.values():
        we["selected"] = False
    s.check()
    valid = {wid: w for wid, w in s.bw.willitems.items() if w.get_status("VALID")}
    assert len(valid) == 1 and all(w.we is None and w.get_status("COMPLETE")
                                   for w in valid.values())
    raw = {wid: str(w.tx) for wid, w in valid.items()}
    s.bw.init_class_variables()
    _heirs_carry(s, _period_giving(s, Will.get_min_locktime(s.bw.willitems)))
    build.build_will(s.bw, with_creation_date=True)
    now = {wid: w for wid, w in s.bw.willitems.items() if w.get_status("VALID")}
    assert set(now) == set(valid)
    assert all(w.get_status("COMPLETE") and str(w.tx) == raw[wid] for wid, w in now.items())


def test_a_renamed_heir_keeps_the_signed_will(flow_session):
    """An heir renamed (same address, share and date): the transactions do
    not change (heir names are not in them), the signed items stay."""
    s, valid = _sent_will(flow_session)
    raw = {wid: str(w.tx) for wid, w in valid.items()}
    alice = list(s.bw.heirs["alice"])
    del s.bw.heirs["alice"]
    s.bw.heirs.save()
    s.bw.heirs["alicia"] = alice
    s.bw.init_class_variables()
    build.build_will(s.bw, with_creation_date=True)
    now = {wid: w for wid, w in s.bw.willitems.items() if w.get_status("VALID")}
    assert set(now) == set(valid)
    for wid, w in now.items():
        assert all(w.get_status(flag) for flag in SENT), w.status
        assert str(w.tx) == raw[wid]
        assert "alicia" in w.heirs and "alice" not in w.heirs


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
