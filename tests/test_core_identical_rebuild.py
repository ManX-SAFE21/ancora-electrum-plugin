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
password). A replaced old item is never brought back.

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
    signed transaction, its state and its history, and carries the period."""
    s, valid = _sent_will(flow_session)
    raw = {wid: str(w.tx) for wid, w in valid.items()}
    history = {wid: w.status for wid, w in valid.items()}
    period = _period_giving(s, Will.get_min_locktime(s.bw.willitems))
    s.set_date(period)
    s.bw.init_class_variables()         # every heir gets the period, as at a Check
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


def test_a_replaced_item_is_not_brought_back(flow_session):
    """Only VALID old items are reused: a replaced one with the same txid
    leaves the fresh item in place (as before #122)."""
    s, valid = _sent_will(flow_session)
    wid, old = next(iter(valid.items()))
    old.set_status("REPLACED", True)
    fresh = _fresh_copy(old)
    new_will = {wid: fresh}
    Will.update_will(s.bw.willitems, new_will)
    assert new_will[wid] is fresh
    assert not fresh.get_status("COMPLETE")


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
    s.set_date(_period_giving(s, Will.get_min_locktime(s.bw.willitems)))
    s.bw.init_class_variables()
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
