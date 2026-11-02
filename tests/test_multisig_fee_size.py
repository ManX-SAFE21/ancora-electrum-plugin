"""A multisig pays the chosen rate on its real size (#212).

A coin from ``wallet.get_utxos()`` has no script, and Electrum guesses a single
key from a multisig address: 184 vB against the 220 of a 2-of-3. The fee worked
out from that guess paid 10.87 sat/vB when 13 was chosen, and the invalidation
10.04. These tests read the amounts, and with two real signatures the bytes that
go on the network.

Test wallets: throw-away keys (``tests/flowkit.py``, ``tests/test_engine_merge``),
three fake will-executors, each with its own fee address. Nothing reaches the
network.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_multisig_fee_size.py
"""

import os
import sys
import tempfile
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

import flowkit as fk
from electrum import keystore
from electrum.storage import WalletStorage
from electrum.transaction import tx_from_any
from electrum.wallet import Multisig_Wallet
from electrum.wallet_db import WalletDB
from test_engine_merge import (  # noqa: F401 - world is the fixture
    _export,
    _merge,
    _sign_offline,
    _valid,
    _xpub,
    world,
)

from bal.core.invalidation_preview import invalidation_facts
from bal.core.util import Util
from bal.core.will import Will

D = [fk.ts(2030, 3, 1), fk.ts(2033, 3, 1), fk.ts(2036, 3, 1)]
#: The rate of a flowkit session, sat/vB.
RATE = 13


def _table(s):
    """Three deliveries: alice, then bob, then both."""
    s.set_heirs(alice="50%", bob="50%")
    s.set_instalments((D[0], {"alice": "30%", "bob": "0%"}),
                      (D[1], {"alice": "0%", "bob": "30%"}),
                      (D[2], {"alice": "20%", "bob": "20%"}))


def _fee(items, wallet, tx):
    """What ``tx`` leaves to the miner; its coins are copies or wallet coins."""
    spent = 0
    for txin in tx.inputs():
        parent = txin.prevout.txid.hex()
        source = items[parent].tx if parent in items else wallet.adb.get_transaction(parent)
        spent += source.outputs()[txin.prevout.out_idx].value
    return spent - sum(o.value for o in tx.outputs())


def _first_copies(s):
    """The VALID copies of the first delivery."""
    copies = [w for w in _valid(s).values()]
    first = min(w.tx.locktime for w in copies)
    return [w for w in copies if w.tx.locktime == first]


def _invalidation(s):
    """The invalidation, built as the engine builds it."""
    return Will.invalidate_will(
        s.bw.willitems, s.wallet, RATE,
        history_label=s.bw.bal_plugin.HISTORY_LABEL.get(),
        will_locktime=Will.get_min_locktime(s.bw.willitems, default_value=None),
    )


@pytest.fixture
def m_of_n(monkeypatch):
    """Opens a session on a watch-only m-of-n wallet of throw-away keys."""
    fk.ensure_event_loop()
    opened = []

    def open_session(m, n):
        """The session, three will-executors."""

        def make(config, path, password, label):
            """The wallet (``password`` and ``label`` are not used)."""
            db = WalletDB("", storage=WalletStorage(path), upgrade=False)
            for i in range(n):
                db.put("x%d" % (i + 1), keystore.from_xpub(
                    _xpub("flowkit cosigner %d" % i, "p2wsh")).dump())
            db.put("wallet_type", "%dof%d" % (m, n))
            wallet = Multisig_Wallet(db, config=config)
            wallet.synchronize()
            return wallet

        monkeypatch.setattr(fk, "_make_wallet", make)
        s = fk.Session(monkeypatch, mode="advanced", password=None)
        s.world.add_executor()
        monkeypatch.setattr(s.wallet, "is_up_to_date", lambda: True)
        opened.append(s)
        return s

    try:
        yield open_session
    finally:
        for s in opened:
            s.close()
        fk.stop_event_loop()


@pytest.mark.parametrize("m,n,size", [(2, 3, 220), (3, 5, 256), (1, 2, 194)])
def test_every_multisig_pays_the_chosen_rate(m_of_n, m, n, size):
    """Every delivery pays the rate on its real size, up to what the build allows.

    The last delivery has no change, one output less (12 vB); a stored copy
    that spends a change does not read its real size, so the sizes are here.
    """
    s = m_of_n(m, n)
    _table(s)
    s.check()
    assert s.bw.will_settings["baltx_fees"] == RATE
    for w in _first_copies(s):
        assert w.tx.estimated_size() == size
        assert _fee(s.bw.willitems, s.wallet, w.tx) == size * RATE
    sizes = {D[0]: size, D[1]: size, D[2]: size - 12}
    dust = s.wallet.dust_threshold()
    copies = _valid(s).values()
    assert {w.tx.locktime for w in copies} == set(sizes)
    for w in copies:
        wanted = sizes[w.tx.locktime] * RATE
        assert wanted <= _fee(s.bw.willitems, s.wallet, w.tx) <= wanted + dust
    # #136: a second Check changes nothing and asks nothing.
    before = set(s.bw.willitems)
    s.check()
    assert set(s.bw.willitems) == before and not s.user.asked


def test_two_real_signatures_pay_the_rate(world):  # noqa: F811
    """With a password: the bytes of the signed copies, whatever the sums say."""
    folder = tempfile.mkdtemp(prefix="bal_fee_")
    a = world("msA")
    _table(a)
    a.check()
    b = world("msB")
    signed = _sign_offline(b, _export(a, folder, "a.json"), folder, "ab.json")
    _merge(a, signed)
    items = _valid(a)
    assert len(items) == 39
    for w in items.values():
        tx = tx_from_any(str(w.tx))
        assert tx.is_complete()
        assert _fee(items, a.wallet, tx) / tx.estimated_size() >= RATE


def test_invalidation_of_a_multisig_pays_the_rate(world):  # noqa: F811
    """The fee on the real size, and the preview says the same rate."""
    s = world("msWatch")
    _table(s)
    s.check()
    tx = _invalidation(s)
    fee = tx.input_value() - tx.output_value()
    assert fee == 158 * RATE
    facts = invalidation_facts(tx, s.bw.willitems, s.wallet)
    assert facts["fee"] == fee and facts["rate"] == RATE


def test_single_key_amounts_do_not_change(world):  # noqa: F811
    """On a single key the guess was right: same size, same fee."""
    s = world("keys")
    _table(s)
    s.check()
    for w in _first_copies(s):
        assert w.tx.estimated_size() == 172
        assert _fee(s.bw.willitems, s.wallet, w.tx) == 172 * RATE
    tx = _invalidation(s)
    assert Util.real_tx_size(tx, s.wallet) == tx.estimated_size()
    assert tx.input_value() - tx.output_value() == tx.estimated_size() * RATE


def test_a_will_of_the_old_size_stays_as_it_is(world, monkeypatch):  # noqa: F811
    """A will built before #212: a Check leaves it, and asks nothing."""
    s = world("msWatch")
    _table(s)
    real = Util.real_tx_size
    monkeypatch.setattr(Util, "real_tx_size",
                        staticmethod(lambda tx, wallet: tx.estimated_size()))
    s.check()
    before = set(s.bw.willitems)
    fees = {w.tx.locktime: _fee(s.bw.willitems, s.wallet, w.tx) for w in _first_copies(s)}
    assert set(fees.values()) == {2392}
    monkeypatch.setattr(Util, "real_tx_size", staticmethod(real))
    s.check()
    assert set(s.bw.willitems) == before and not s.user.asked
    assert {w.tx.locktime: _fee(s.bw.willitems, s.wallet, w.tx)
            for w in _first_copies(s)} == fees


def test_measuring_changes_nothing_in_the_transaction(world):  # noqa: F811
    """The script stays out of the input and of the stored text."""
    s = world("msWatch")
    _table(s)
    s.check()
    tx = _invalidation(s)
    text = str(tx)
    assert Util.real_tx_size(tx, s.wallet) == 158
    assert str(tx) == text
    assert all(txin.script_descriptor is None for txin in tx.inputs())


def test_a_script_the_wallet_cannot_give_stays_on_the_guess(world):  # noqa: F811
    """Without a script from the wallet the guess is still used."""
    s = world("msWatch")
    _table(s)
    s.check()
    tx = _invalidation(s)
    nothing = SimpleNamespace(get_script_descriptor_for_address=lambda address: None)
    assert Util.real_tx_size(tx, nothing) == tx.estimated_size()
