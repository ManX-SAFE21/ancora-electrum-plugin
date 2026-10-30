"""After delivery 1 of a will in several deliveries, nothing on its own (#201).

Owner's rule (2026-10-29): once delivery 1 of a will in several
deliveries (a Multiverse) was executed, the plugin builds, signs and sends
nothing on its own. While the plan is running it leaves it alone; when the
owner stopped it (Tools > Invalidate, or the coins spent) it does not
prepare it again.

Before #201 (round-2 probes of 2026-10-29, Electrum 4.8.2 and 4.8.3):

* with three deliveries the first Check, wallet close or
  ``bal_will_autorebuild`` after delivery 1 built 12 new copies (only the
  change address differed) and sent them, so each will-executor held two
  conflicting copies of delivery 2; on a wallet without a password with no
  question at all. The rows of delivery 1 were never found in a VALID copy
  (the executed copy is on the chain), so the will was "not coherent";
* after the owner's Invalidate, every Check, wallet close and automatic
  rebuild prepared delivery 2 again on the coin that came back; a wallet
  without a password signed and sent it with no question.

Two rebuilds stay, on purpose (owner, 2026-10-29): new coins in the wallet
(delivery 2 prepared again with them, one day earlier) and a will-executor
added (its own copy).

Here, with ``tests/flowkit.py`` (three fake will-executors, each with its
own fee address; throw-away test wallets with the test password, or none;
fixed clock), every flow: the Check, the wallet close, the GUI automatic
rebuild and ``bal_will_autorebuild``.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-483 \\
        python3 -m pytest tests/test_multiverse_after_delivery_1.py
"""

import copy
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

import flowkit as fk
from electrum.transaction import PartialTransaction, TxOutpoint
from electrum.util import TxMinedInfo
from PyQt6.QtWidgets import QApplication

from bal.core import delivery, delivery_progress
from bal.engine import context as engine_context
from bal.gui.qt.flow_adapter import REST_CANCELLED

# Kept at module level: flowkit's window needs a Qt application.
_APP = QApplication.instance() or QApplication(sys.argv)

# Test value: the password of the throw-away test wallets.
TEST_PASSWORD = "flowkit-test"
D = fk.ts(2026, 10, 16, 19)          # delivery 1
YEAR = 365 * fk.DAY
FLOWS = ("check", "close", "auto", "cli")
STOPPED = "Delivery 1 was executed and the remaining deliveries were cancelled"


@pytest.fixture
def make(monkeypatch):
    """flowkit sessions, delivery ``D``, three will-executors, synchronized."""
    fk.ensure_event_loop()
    made = []

    def _make(password=TEST_PASSWORD, three=True):
        """One session; ``password=None``: a wallet without a password;
        ``three=False`` starts with two will-executors."""
        s = fk.Session(monkeypatch, password=password, fee=21, locktime=D,
                       threshold="-30d")
        if three:
            s.world.add_executor()
        # The flowkit wallet is offline: synchronized for the #128 rules.
        monkeypatch.setattr(s.wallet, "is_up_to_date", lambda: True)
        made.append(s)
        return s

    yield _make
    for s in made:
        s.close()
    fk.stop_event_loop()


def _mine(s, wid, how="spv"):
    """Put the will item ``wid`` on the chain: in the mempool, or mined
    and verified (``"spv"``)."""
    tx = s.bw.willitems[wid].tx
    assert tx.is_complete()
    s.wallet.adb.add_transaction(tx)
    # The fake network knows it, so a later transaction may spend it.
    s.world.broadcast[tx.txid()] = str(tx)
    if how == "mempool":
        s.wallet.adb.add_unverified_or_unconfirmed_tx(tx.txid(), 0)
    elif how == "spv":
        s.wallet.db.put("stored_height", 300)
        s.wallet.adb.add_verified_tx(tx.txid(), TxMinedInfo(
            _height=101, timestamp=int(s.clock.now), txpos=1, header_hash="00" * 32))
    else:
        raise ValueError(how)


def _mine_as_electrum(s, wid):
    """A copy mined the way Electrum learns it: the height first, then the
    transaction. ``add_transaction`` then removes every conflicting
    transaction, and what depends on it, by itself."""
    tx = s.bw.willitems[wid].tx
    s.world.broadcast[tx.txid()] = str(tx)
    s.wallet.adb.add_unverified_or_unconfirmed_tx(tx.txid(), 101)
    assert s.wallet.adb.add_transaction(tx)


def _copies(s, url=None, locktime=None):
    """The ids of the will items of ``url`` (any) dated ``locktime`` (any)."""
    return sorted(
        w for w, i in s.bw.willitems.items()
        if (url is None or (i.we or {}).get("url") == url)
        and (locktime is None or int(i.tx.locktime) == locktime))


def _table(s, rows):
    """alice at delivery 1, bob at the later deliveries."""
    if rows == 2:
        s.set_instalments((D, {"alice": "60%", "bob": "0%"}),
                          (D + YEAR, {"alice": "0%", "bob": "40%"}))
    else:
        s.set_instalments((D, {"alice": "50%", "bob": "0%"}),
                          (D + YEAR, {"alice": "0%", "bob": "25%"}),
                          (D + 2 * YEAR, {"alice": "0%", "bob": "25%"}))


def _sent(s, rows, password=TEST_PASSWORD, three=True):
    """A will in ``rows`` deliveries, signed and sent; ten days later."""
    s = s(password=password, three=three)
    _table(s, rows)
    s.check()
    s.clock.travel(10)
    return s


def _delivery_1_mined(s):
    """we1's copy of delivery 1 is mined; one day later. Returns its id."""
    (executed,) = _copies(s, "https://we1.test", D)
    _mine(s, executed)
    s.clock.travel(1)
    return executed


def _run(s, flow):
    """Run one flow; return what it gave (the Check's rows for the Check).

    After the wallet close and the command line the window reads the will
    again from the wallet: the close empties the window's copy, and the
    command line works on a copy of its own, so only the stored will tells
    what they did.
    """
    if flow == "check":
        return [fk.strip(str(x)) for x in s.check().labels]
    if flow == "auto":
        return s.auto()
    if flow == "close":
        out = s.close_wallet()
    elif flow == "cli":
        out = s.cli("auto_rebuild")
    else:
        raise ValueError(flow)
    s.reload_window()
    return out


class _Nothing:
    """Records the will, the pushes and the questions before a flow, to
    assert that nothing was built, signed or sent after it."""

    def __init__(self, s):
        self.s = s
        self.items = set(s.bw.willitems)
        self.pushed = len(s.world.pushed)

    def assert_nothing(self):
        """No new copy, nothing pushed or broadcast, no question."""
        s = self.s
        assert set(s.bw.willitems) == self.items
        assert len(s.world.pushed) == self.pushed
        assert s.world.broadcast_log == []
        assert s.user.asked == []


def _password(with_password):
    """The test password, or None (a wallet without a password)."""
    return TEST_PASSWORD if with_password else None


def _steps(flow, then):
    """The flows a test runs: ``flow`` then ``then``. Only the close alone:
    it tears the window down (heirs included), as Electrum does."""
    return (flow,) if flow == "close" else (flow, then)


# ------------------------------------------------- the plan is running

@pytest.mark.parametrize("flow", FLOWS)
@pytest.mark.parametrize("with_password", [True, False])
@pytest.mark.parametrize("rows", [2, 3])
def test_running_plan_nothing_on_its_own(make, rows, with_password, flow):
    """The first flow right after delivery 1 is mined, then a Check: no new
    copy, nothing sent, no question (before #201, with three deliveries:
    12 new copies sent, with no question on a wallet without a password)."""
    s = _sent(make, rows, _password(with_password))
    _delivery_1_mined(s)
    assert engine_context.done_deliveries(s.bw) == 1
    for step in _steps(flow, "check"):
        nothing = _Nothing(s)
        out = _run(s, step)
        nothing.assert_nothing()
        if step == "check":
            assert "Checking your will: Ok" in out, out
        s.clock.travel(1)
    assert engine_context.cancelled_rest(s.bw) is None


def test_delivery_2_executed_too(make):
    """Three deliveries, delivery 2 mined too: the Check and the wallet
    close still do nothing, and two deliveries are done."""
    s = _sent(make, 3)
    executed = _delivery_1_mined(s)
    s.check()
    s.clock.travel(365)
    (d2,) = [w for w in _copies(s, "https://we1.test", D + YEAR)
             if s.bw.willitems[w].get_status("VALID")
             and s.bw.willitems[w].tx.inputs()[0].prevout.txid.hex() == executed]
    _mine(s, d2)
    s.clock.travel(1)
    for flow in ("check", "close", "check"):
        nothing = _Nothing(s)
        _run(s, flow)
        nothing.assert_nothing()
        s.clock.travel(1)
    assert engine_context.done_deliveries(s.bw) == 2


# ------------------------------------------------- the two rebuilds that stay

@pytest.mark.parametrize("rows", [2, 3])
def test_new_coins_while_running_are_covered(make, rows):
    """New coins after delivery 1: delivery 2 is prepared again with them
    (one day earlier, on the executed copy's change and the new coin),
    asked and sent; the next Check does nothing."""
    s = _sent(make, rows)
    executed = _delivery_1_mined(s)
    s.check()
    s.clock.travel(1)
    s.add_coin(250_000)
    pushed = len(s.world.pushed)
    before = set(s.bw.willitems)
    s.check()
    new = set(s.bw.willitems) - before
    second = [w for w in new
              if delivery_progress.delivery_of(s.bw.willitems[w]) == 2]
    assert len(second) == 3
    for w in second:
        spent = {i.prevout.txid.hex() for i in s.bw.willitems[w].tx.inputs()}
        # The executed copy's change and the new coin (not a will item).
        assert executed in spent and len(spent - set(s.bw.willitems)) == 1
        assert int(s.bw.willitems[w].tx.locktime) == D + YEAR - fk.DAY
    assert len(s.world.pushed) - pushed == len(new)
    assert [q[0] for q in s.user.asked] == ["password"]
    s.clock.travel(1)
    nothing = _Nothing(s)
    s.check()
    nothing.assert_nothing()


def test_a_will_executor_added_while_running_gets_its_copy(make):
    """Two will-executors, a third one added after delivery 1: one copy of
    delivery 2 for it, asked and sent to it only; then nothing."""
    s = _sent(make, 2, three=False)
    _delivery_1_mined(s)
    s.check()
    s.world.add_executor()
    s.clock.travel(1)
    pushed = len(s.world.pushed)
    before = set(s.bw.willitems)
    s.check()
    new = sorted(set(s.bw.willitems) - before)
    assert len(new) == 1
    assert s.bw.willitems[new[0]].we["url"] == "https://we2.test"
    assert [url for url, _t, _r in s.world.pushed[pushed:]] == ["https://we2.test"]
    s.clock.travel(1)
    nothing = _Nothing(s)
    s.check()
    nothing.assert_nothing()


# ------------------------------------------------- the plan was stopped

def _stopped(make, rows, with_password=True):
    """Delivery 1 mined, the Check after it, then Tools > Invalidate."""
    s = _sent(make, rows, _password(with_password))
    executed = _delivery_1_mined(s)
    s.check()
    s.menu_invalidate()
    assert any("invalidation" in kind for kind, _t in s.world.broadcast_log)
    s.clock.travel(1)
    return s, executed


@pytest.mark.parametrize("flow", FLOWS)
@pytest.mark.parametrize("with_password", [True, False])
@pytest.mark.parametrize("rows", [2, 3])
def test_stopped_plan_is_not_prepared_again(make, rows, with_password, flow):
    """After the owner's Invalidate no flow prepares the rest again (before
    #201 every one did; without a password with no question). The Check
    says why and how to go on, the command line returns the hold's code,
    the wallet close and the automatic rebuild skip."""
    s, _executed = _stopped(make, rows, with_password)
    assert engine_context.cancelled_rest(s.bw) == 1
    for step in _steps(flow, flow):
        nothing = _Nothing(s)
        out = _run(s, step)
        nothing.assert_nothing()
        if step == "check":
            assert "Checking variables: Stopped" in out, out
            assert any(STOPPED in row for row in out), out
        elif step == "cli":
            assert out["returned"]["result"] == "delivery_rest_cancelled"
            assert STOPPED in out["returned"]["message"]
            assert "bal_instalments_clear" in out["returned"]["message"]
        s.clock.travel(1)


def test_stopped_plan_refuses_sign_broadcast_and_invalidate(make):
    """Sign, Broadcast and Invalidate are refused with the same text (rule
    I6): no copy could still be mined, there is nothing to invalidate."""
    s, _executed = _stopped(make, 2)
    for action in (s.menu_sign, s.menu_broadcast, s.menu_invalidate):
        nothing = _Nothing(s)
        action()
        nothing.assert_nothing()
        assert s.bw.popups == [["message", fk.strip(REST_CANCELLED.format(1))]]


@pytest.mark.parametrize("rows", [2, 3])
def test_new_coins_after_invalidate_stay_out(make, rows):
    """The plan was stopped: a new coin does not bring it back."""
    s, _executed = _stopped(make, rows)
    s.add_coin(250_000)
    nothing = _Nothing(s)
    out = _run(s, "check")
    nothing.assert_nothing()
    assert any(STOPPED in row for row in out), out


def test_back_to_one_delivery_then_a_new_will(make):
    """The way on: "Back to one delivery" (the table removed), a new date,
    the Check prepares a new will on the coin that came back."""
    s, _executed = _stopped(make, 2)
    assert engine_context.clear_instalments(s.bw)
    assert engine_context.cancelled_rest(s.bw) is None
    new_date = fk.ts(2028, 6, 1, 19)
    s.set_date(new_date)
    pushed = len(s.world.pushed)
    before = set(s.bw.willitems)
    s.check()
    new = sorted(set(s.bw.willitems) - before)
    assert len(new) == 3
    assert {int(s.bw.willitems[w].tx.locktime) for w in new} == {new_date}
    assert len(s.world.pushed) - pushed == 3
    assert [q[0] for q in s.user.asked] == ["password"]


def test_invalidate_with_delivery_1_in_the_mempool(make):
    """Delivery 1 only in the mempool, then Invalidate: stopped as well.
    When another will-executor's copy of delivery 1 is the one mined,
    Electrum drops the copy in the mempool and the invalidation that spent
    it: the copies of delivery 2 on the copy mined can deliver again, so
    the plan is no longer held as stopped."""
    s = _sent(make, 2)
    (in_mempool,) = _copies(s, "https://we1.test", D)
    (other,) = _copies(s, "https://we0.test", D)
    _mine(s, in_mempool, "mempool")
    s.clock.travel(1)
    s.check()
    s.menu_invalidate()
    s.clock.travel(1)
    assert engine_context.cancelled_rest(s.bw) == 1
    _mine_as_electrum(s, other)
    assert s.wallet.adb.get_transaction(in_mempool) is None
    assert engine_context.cancelled_rest(s.bw) is None


# ------------------------------------------------- in doubt, stop (2026-10-30)

def _add_copy(s, base, prevout, unsign=False):
    """A copy of the will item ``base`` whose first input spends
    ``prevout``; ``unsign``: its signatures removed (a draft). Stored
    under a made-up id, as the old copies of a long-used wallet are, and
    saved: the wallet close and the command line read the stored will."""
    item = copy.deepcopy(s.bw.willitems[base])
    if unsign:
        # A PartialTransaction made from a signed one keeps no signature.
        item.tx = PartialTransaction.from_tx(item.tx)
    item.tx.inputs()[0].prevout = prevout
    wid = "{:064x}".format(0xC0DE00 + len(s.bw.willitems))
    s.bw.willitems[wid] = item
    engine_context.save_willitems(s.bw)
    return wid


def _orphan_copy(s):
    """An old copy whose coin the wallet does not know (its transaction
    was purged): the owner's wallet of 2026-10-30, 1037 copies."""
    unknown = TxOutpoint(txid=bytes.fromhex("11" * 32), out_idx=0)
    wid = _add_copy(s, next(iter(s.bw.willitems)), unknown)
    assert s.wallet.adb.get_transaction("11" * 32) is None
    return wid


def _draft_on_the_coin_that_came_back(s, executed):
    """A draft (not signed) of delivery 2 on the invalidation's output: a
    coin on the chain that nothing spends. The Check of 2026-10-30 left
    such a draft in the owner's wallet."""
    (index,) = delivery_progress.plan_outputs(executed, 1, s.bw.willitems)
    invalidation = s.wallet.adb.get_spender("{}:{}".format(executed, index))
    assert invalidation and invalidation not in s.bw.willitems
    coin = TxOutpoint(txid=bytes.fromhex(invalidation), out_idx=0)
    second = next(w for w, i in s.bw.willitems.items()
                  if delivery_progress.delivery_of(i) == 2)
    wid = _add_copy(s, second, coin, unsign=True)
    assert not s.bw.willitems[wid].tx.is_complete()
    assert s.wallet.adb.get_spender("{}:0".format(invalidation)) is None
    return wid


@pytest.mark.parametrize("flow", FLOWS)
@pytest.mark.parametrize("with_password", [True, False])
@pytest.mark.parametrize("doubt", ["orphan", "draft"])
def test_stopped_plan_in_doubt_stays_stopped(make, doubt, with_password, flow):
    """Owner's test of 2026-10-30: an old copy whose coin the wallet does
    not know, or a draft, counted as "could still be mined", so the hold
    never applied and the Check prepared the rest again and asked to sign
    it (without a password: signed and sent with no question). In doubt a
    copy is not live: the plan stays stopped and nothing is done."""
    s, executed = _stopped(make, 3, with_password)
    if doubt == "orphan":
        _orphan_copy(s)
    else:
        _draft_on_the_coin_that_came_back(s, executed)
    assert delivery_progress._live_items(s.bw.willitems, s.wallet) == []
    assert engine_context.cancelled_rest(s.bw) == 1
    for step in _steps(flow, flow):
        nothing = _Nothing(s)
        out = _run(s, step)
        nothing.assert_nothing()
        if step == "check":
            assert any(STOPPED in row for row in out), out
        elif step == "cli":
            assert out["returned"]["result"] == "delivery_rest_cancelled"
        s.clock.travel(1)


def test_a_signed_copy_on_the_coin_that_came_back_is_live(make):
    """What stays: a copy that is signed and spends a coin nothing spends
    could be mined, so the plan is not held as stopped."""
    s, executed = _stopped(make, 2)
    wid = _draft_on_the_coin_that_came_back(s, executed)
    s.bw.willitems[wid].tx.is_complete = lambda: True
    assert delivery_progress._live_items(s.bw.willitems, s.wallet) == [wid]
    assert engine_context.cancelled_rest(s.bw) is None


# ------------------------------------------------- what must not change

def test_one_date_will_after_invalidate_is_prepared_again(make):
    """Without a table nothing changes: after Invalidate the Check prepares
    the will again (the usual way to postpone), with the password."""
    s = make()
    s.set_date(D + YEAR)
    s.check()
    s.clock.travel(10)
    s.menu_invalidate()
    s.clock.travel(1)
    assert engine_context.cancelled_rest(s.bw) is None
    pushed = len(s.world.pushed)
    before = set(s.bw.willitems)
    s.check()
    assert len(set(s.bw.willitems) - before) == 3
    assert len(s.world.pushed) - pushed == 3


def test_nothing_read_without_a_wallet():
    """Without a usable wallet nothing is decided (never a hold)."""
    assert delivery_progress.cancelled_rest({}, None, 3) is None
    assert delivery_progress.done_deliveries({}, None, 3) == 0
    assert delivery_progress.executed_deliveries({}, object(), 3) == set()
    hold = delivery.DeliveryRestCancelled(2)
    assert hold.code == "delivery_rest_cancelled" and hold.executed == 2
    assert isinstance(hold, delivery.DeliveryHold)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
