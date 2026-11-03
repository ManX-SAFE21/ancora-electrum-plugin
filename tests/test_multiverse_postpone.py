"""Postponing a will in several deliveries, to the end (audit 2026-10-31 item 33, #228).

The audit found that the postpone of a will in several deliveries (a
Multiverse) was tested only up to the question. Here the whole road, on a
will of three deliveries signed, sent and verified by three will-executors:
every date one year later, one Check. Probed first on 2026-11-03 (#227); no
fault found, so these tests only keep it so:

* the Check asks for the invalidation, broadcasts it and, in the same
  Check, prepares the 39 copies of the new dates, sends and verifies them;
* the old copies are no longer valid, the new ones spend the coin the
  invalidation gave back, every copy pays heirs + will-executor + change +
  one miner fee and nothing else (#211);
* once the invalidation is on the chain, a Check changes nothing and asks
  nothing (#136).

Test wallets: ``tests/flowkit.py`` throw-away testnet wallets, with and
without password; three fake will-executors, each with its own fee address.
Nothing reaches the network.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_multiverse_postpone.py
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

import flowkit as fk
from electrum.transaction import Transaction
from electrum.util import TxMinedInfo
from PyQt6.QtWidgets import QApplication
from test_miner_fee import _assert_each_copy_pays_once

_APP = QApplication.instance() or QApplication(sys.argv)

# Test value: the password of the throw-away test wallets (flowkit).
TEST_PASSWORD = "flowkit-test"
RATE = 20
YEAR = 365 * fk.DAY
D = [fk.ts(2030, 3, 1), fk.ts(2033, 3, 1), fk.ts(2036, 3, 1)]
THREE = [(D[0], {"alice": "20%", "bob": "15%"}),
         (D[1], {"alice": "20%", "bob": "15%"}),
         (D[2], {"alice": "15%", "bob": "15%"})]
LATER = [(d + YEAR, shares) for d, shares in THREE]


@pytest.fixture
def make(monkeypatch):
    """A flowkit session with three will-executors and a will in three
    deliveries signed, sent and verified."""
    fk.ensure_event_loop()
    made = []

    def _make(password):
        """The session; ``password=None``: a wallet without a password."""
        s = fk.Session(monkeypatch, mode="advanced", password=password,
                       fee=RATE, locktime=D[0])
        s.world.add_executor()
        # The flowkit wallet is offline: synchronized for the #128 rules.
        monkeypatch.setattr(s.wallet, "is_up_to_date", lambda: True)
        made.append(s)
        s.set_instalments(*THREE)
        s.check()
        return s

    yield _make
    for s in made:
        s.close()
    fk.stop_event_loop()


def _valid(s):
    """The VALID copies by txid."""
    return {wid: w for wid, w in s.bw.willitems.items() if w.get_status("VALID")}


def _sent(s):
    """The VALID copies sent to and verified by their will-executor."""
    return {wid: w for wid, w in _valid(s).items()
            if w.get_status("PUSHED") and w.get_status("CHECKED")}


def _mine(s, txid):
    """The broadcast transaction *txid* mined and verified."""
    tx = Transaction(s.world.broadcast[txid])
    s.wallet.adb.add_transaction(tx)
    s.wallet.db.put("stored_height", 300)
    s.wallet.adb.add_verified_tx(txid, TxMinedInfo(
        _height=101, timestamp=int(s.clock.now), txpos=1, header_hash="00" * 32))


@pytest.mark.parametrize("password", [TEST_PASSWORD, None])
def test_every_date_one_year_later_in_one_check(make, password):
    """Invalidated, rebuilt on the new dates, sent and verified at once."""
    s = make(password)
    old = set(_sent(s))
    assert len(old) == 39
    assert {int(w.tx.locktime) for w in _valid(s).values()} == set(D)
    pushed = len(s.world.pushed)
    s.clock.travel(5)
    s.set_instalments(*LATER)
    s.check()
    asked = [text for _kind, text in s.user.asked]
    assert "Invalidate now" in asked
    kinds = [kind for kind, _txid in s.world.broadcast_log]
    assert kinds == ["invalidation(dialog)"]
    new = _sent(s)
    assert len(new) == 39 and not old & set(new)
    assert {int(w.tx.locktime) for w in new.values()} == {d for d, _sh in LATER}
    assert len(s.world.pushed) - pushed == 39
    for wid in old:
        w = s.bw.willitems[wid]
        assert w.get_status("INVALIDATED") and not w.get_status("VALID")
    # the new will lives on the coin the invalidation gave back
    invalidation = s.world.broadcast_log[0][1]
    first = [w for w in new.values() if int(w.tx.locktime) == LATER[0][0]]
    assert {i.prevout.txid.hex() for w in first for i in w.tx.inputs()} == {invalidation}
    # every copy: heirs + will-executor + change + one miner fee
    _assert_each_copy_pays_once(s, RATE)
    assert s.bw.will_settings["locktime"] == LATER[0][0]


@pytest.mark.parametrize("password", [TEST_PASSWORD, None])
def test_once_on_the_chain_a_check_changes_nothing(make, password):
    """The invalidation mined: the next Check asks nothing, sends nothing."""
    s = make(password)
    s.clock.travel(5)
    s.set_instalments(*LATER)
    s.check()
    _mine(s, s.world.broadcast_log[0][1])
    s.clock.travel(1)
    before = set(s.bw.willitems)
    pushed = len(s.world.pushed)
    s.check()
    assert set(s.bw.willitems) == before
    assert not s.user.asked and not s.world.broadcast_log
    assert len(s.world.pushed) == pushed
    assert len(_sent(s)) == 39


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
