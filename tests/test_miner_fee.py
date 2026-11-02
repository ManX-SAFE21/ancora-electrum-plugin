"""A delivery pays the miner once; funds too low for every delivery: refused (#211).

General audit of 2026-10-31, finding 3: ``prepare_transactions`` put the miner
fee in ``out_amount`` and ``get_change_output`` took it off again, so a delivery
with change paid it twice, out of what the later deliveries receive. 3172 tests
did not see it: none read the amounts of a chain, so these do.

Owner's rule (2026-11-02): when the funds cannot pay every delivery with its
fees the will is refused, and the owner reads what to do.

Test wallets: throw-away keys (``tests/flowkit.py``, ``tests/test_engine_merge``),
three fake will-executors, each with its own fee address. Nothing reaches the
network.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_miner_fee.py
"""

import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

import flowkit as fk
from electrum.transaction import PartialTxOutput
from electrum.util import UserFacingException
from test_engine_merge import world  # noqa: F401 - the fixture

from bal.cli.controller import _user_facing
from bal.core import heirs as heirs_mod
from bal.core.heirs import DeliveriesNotFundedException
from bal.core.messages import deliveries_not_funded_message
from bal.engine import build

D = [fk.ts(2030, 3, 1), fk.ts(2033, 3, 1), fk.ts(2036, 3, 1)]
ONE = None
TWO = [(D[0], {"alice": "30%", "bob": "20%"}),
       (D[1], {"alice": "30%", "bob": "20%"})]
THREE = [(D[0], {"alice": "20%", "bob": "15%"}),
         (D[1], {"alice": "20%", "bob": "15%"}),
         (D[2], {"alice": "15%", "bob": "15%"})]

#: In the sentence for the owner.
REFUSED = "The new will was not prepared"


@pytest.fixture
def session(monkeypatch):
    """Opens a session with one coin of ``coin`` sat and three will-executors."""
    fk.ensure_event_loop()
    opened = []

    def open_session(coin=1_000_000, fee=20, table=THREE, password="flowkit-test"):
        """The session, its delivery table stored."""
        real_add = fk.Session._add_coin
        monkeypatch.setattr(
            fk.Session, "_add_coin",
            lambda self, value, tag: real_add(self, coin if value == 1_000_000 else value, tag))
        s = fk.Session(monkeypatch, mode="advanced", password=password, fee=fee, locktime=D[0])
        s.world.add_executor()
        monkeypatch.setattr(s.wallet, "is_up_to_date", lambda: True)
        opened.append(s)
        if table:
            s.set_instalments(*table)
        return s

    try:
        yield open_session
    finally:
        for s in opened:
            s.close()
        fk.stop_event_loop()


def _valid(s):
    """The VALID copies of the session's will."""
    return [w for w in s.bw.willitems.values() if w.get_status("VALID")]


def _input_value(s, txin):
    """The value of the coin *txin* spends: a copy's change or a wallet coin."""
    parent = txin.prevout.txid.hex()
    if parent in s.bw.willitems:
        return s.bw.willitems[parent].tx.outputs()[txin.prevout.out_idx].value
    return s.wallet.adb.get_transaction(parent).outputs()[txin.prevout.out_idx].value


def _rows(dlg):
    """The rows of the Check window, as one text."""
    return " ".join(str(row) for row in dlg.labels)


def _assert_each_copy_pays_once(s, rate, low=1.0):
    """Every copy: inputs = heirs + will-executor + change + one miner fee.

    ``low``: the part of the rate a copy may pay at least. A multisig coin is
    measured as a single-key one when the will is built, a known limit of this
    step: see :func:`test_multisig_pays_the_miner_once`.
    """
    executors = {e["address"] for e in s.world.executors.values()}
    copies = _valid(s)
    assert copies
    for w in copies:
        size = w.tx.estimated_size()
        spent = sum(_input_value(s, txin) for txin in w.tx.inputs())
        paid = {"heirs": 0, "executor": 0, "change": 0}
        for out in w.tx.outputs():
            if out.address in executors:
                paid["executor"] += out.value
            elif s.wallet.is_mine(out.address):
                paid["change"] += out.value
            else:
                paid["heirs"] += out.value
        miner = spent - sum(paid.values())
        assert paid["heirs"] > 0 and paid["executor"] > 0
        # The build accepts a fee up to one dust limit over the size's.
        assert low * size * rate <= miner <= size * rate + s.wallet.dust_threshold(), (
            f"{miner} sat on {size} vB at {rate} sat/vB")


def _chain(s):
    """The copies of one will-executor's chain, first delivery first."""
    ids = {id(w): wid for wid, w in s.bw.willitems.items()}
    copies = sorted(_valid(s), key=lambda w: w.tx.locktime)
    chain = [copies[0]]
    while True:
        # The id in the will, not ``tx.txid()``: an unsigned multisig copy may
        # not know its own yet.
        children = [w for w in copies
                    if any(i.prevout.txid.hex() == ids[id(chain[-1])]
                           for i in w.tx.inputs())]
        if not children:
            return chain
        chain.append(children[0])


# ------------------------------------------------------------- one miner fee


@pytest.mark.parametrize("password", ["flowkit-test", None], ids=["password", "no password"])
@pytest.mark.parametrize("table,dates", [(ONE, 1), (TWO, 2), (THREE, 3)], ids=["1", "2", "3"])
def test_each_copy_pays_the_miner_once(session, table, dates, password):
    """One, two and three deliveries, with and without a password."""
    s = session(table=table, password=password)
    s.check()
    _assert_each_copy_pays_once(s, 20)
    assert len(_chain(s)) == dates
    # #136: a second Check changes nothing and asks nothing.
    before = set(s.bw.willitems)
    s.check()
    assert set(s.bw.willitems) == before and not s.user.asked


def test_more_coins_pay_the_miner_once(session):
    """Three coins: the first delivery spends them all."""
    s = session()
    s.add_coin(70_000)
    s.add_coin(90_000)
    s.check()
    _assert_each_copy_pays_once(s, 20)
    assert len(_chain(s)) == 3


def test_multisig_pays_the_miner_once(world):  # noqa: F811
    """The owner's 2-of-3: one fee, never two.

    The build measures a multisig coin as a single-key one (184 vB against
    220), so a copy pays about 83 % of the rate: not double, and a limit that
    #211 did not make: the last delivery always paid so.
    """
    s = world("msWatch")
    s.set_heirs(alice="50%", bob="50%")
    s.set_instalments((D[0], {"alice": "30%", "bob": "0%"}),
                      (D[1], {"alice": "0%", "bob": "30%"}),
                      (D[2], {"alice": "20%", "bob": "20%"}))
    s.check()
    _assert_each_copy_pays_once(s, s.bw.will_settings["baltx_fees"], low=0.8)
    assert len(_chain(s)) == 3


def test_the_saving_reaches_the_later_deliveries(session):
    """Nothing leaves the coin but what the heirs, the executors and the miners get."""
    s = session()
    s.check()
    executors = {e["address"] for e in s.world.executors.values()}
    chain = _chain(s)
    heirs = sum(o.value for w in chain for o in w.tx.outputs()
                if o.address not in executors and not s.wallet.is_mine(o.address))
    miners = sum(w.tx.estimated_size() * 20 for w in chain)
    fees = sum(o.value for w in chain for o in w.tx.outputs() if o.address in executors)
    assert heirs + fees == pytest.approx(1_000_000 - miners, abs=3 * 546)


# --------------------------------------------------------- funds not enough


@pytest.mark.parametrize("coin,fee", [(100_000, 100), (30_000, 100), (50_000, 100)])
def test_three_deliveries_not_funded_are_refused(session, coin, fee):
    """Nothing to sign, and the owner reads what to do."""
    s = session(coin=coin, fee=fee)
    dlg = s.check()
    text = _rows(dlg)
    assert REFUSED in text
    assert "from 3 to 2" in text and "one delivery" in text
    assert str(coin) in text
    assert not s.bw.willitems and not s.user.asked and not s.world.pushed


def test_two_deliveries_not_funded_offer_one_delivery(session):
    """With two deliveries there is no "from 2 to 1": a simple inheritance."""
    s = session(coin=30_000, fee=150, table=TWO)
    dlg = s.check()
    text = _rows(dlg)
    assert REFUSED in text and "simple inheritance with one delivery" in text
    assert "from 2" not in text
    assert not s.bw.willitems


def test_refusal_leaves_the_will_in_force_alone(session):
    """A rebuild refused (the fee rate rose): the copies sent stay as they are."""
    s = session()
    s.check()
    before = set(s.bw.willitems)
    valid = {w.tx.txid() for w in _valid(s)}
    pushed = len(s.world.pushed)
    s.set_fee(5000)
    dlg = s.check()
    assert REFUSED in _rows(dlg)
    assert set(s.bw.willitems) == before
    assert {w.tx.txid() for w in _valid(s)} == valid
    assert len(s.world.pushed) == pushed


def test_prepare_menu_shows_the_sentence(session):
    """Tools > Prepare: the sentence, not an error escaping to the window."""
    s = session(coin=30_000, fee=100)
    result = s.prepare()
    assert "raised" not in result
    assert any(REFUSED in str(p) for p in s.bw.popups)


def test_command_line_says_the_same():
    """``bal_will_prepare`` gives the same sentence."""
    e = _user_facing(DeliveriesNotFundedException(30_000, 3))
    assert isinstance(e, UserFacingException)
    assert str(e) == deliveries_not_funded_message(DeliveriesNotFundedException(30_000, 3))


def test_a_will_with_one_delivery_keeps_its_message(session):
    """Not funded with one delivery: nothing to reduce, the old sentence."""
    s = session(coin=30_000, fee=1000, table=ONE)
    dlg = s.check()
    text = _rows(dlg)
    assert "Wallet balance is too low" in text and REFUSED not in text


# -------------------------------------------------------- the chain is read


def _tx(txid, locktime, parents=()):
    """A built copy, for :func:`bal.engine.build._chain_complete`."""
    inputs = [SimpleNamespace(prevout=SimpleNamespace(txid=bytes.fromhex(p)))
              for p in parents]
    return txid, SimpleNamespace(my_locktime=locktime, inputs=lambda: inputs)


def test_chain_complete_reads_every_copy():
    """A date missing, or one executor's chain ending early, is not complete."""
    whole = dict([_tx("aa", 1), _tx("bb", 2, ["aa"]), _tx("cc", 3, ["bb"])])
    assert build._chain_complete(whole, [1, 2, 3])
    no_last = dict([_tx("aa", 1), _tx("bb", 2, ["aa"])])
    assert not build._chain_complete(no_last, [1, 2, 3])
    one_ends = dict([_tx("aa", 1), _tx("dd", 1), _tx("bb", 2, ["aa"]), _tx("cc", 3, ["bb"])])
    assert not build._chain_complete(one_ends, [1, 2, 3])


def test_a_delivery_without_change_for_the_next_is_refused(session, monkeypatch):
    """No change for delivery 2 raises nothing: the chain read says it is missing."""
    s = session()
    real = heirs_mod.get_change_output
    monkeypatch.setattr(heirs_mod, "get_change_output", lambda *a: None)
    dlg = s.check()
    monkeypatch.setattr(heirs_mod, "get_change_output", real)
    assert REFUSED in _rows(dlg)
    assert not s.bw.willitems


# --------------------------------------------------- a will already sent


def _build_with_old_code(s, monkeypatch):
    """The first Check of *s* with the change as it was before #211."""
    real_change = heirs_mod.get_change_output
    real_prepare = heirs_mod.prepare_transactions
    fees = {}

    def old_change(wallet, in_amount, out_amount):
        """The change before #211: the fee of this delivery taken off again."""
        amount = int(in_amount - out_amount - fees.get("now", 0))
        if amount <= wallet.dust_threshold():
            return None
        out = PartialTxOutput.from_address_and_value(
            wallet.get_change_addresses_for_new_transaction()[0], amount)
        out.is_change = True
        return out

    def prepare(locktimes, utxos, tx_fees, wallet):
        """Tells ``old_change`` the fee of the lowest date being built."""
        fees["now"] = tx_fees.get(min(int(k) for k in locktimes), 0)
        return real_prepare(locktimes, utxos, tx_fees, wallet)

    monkeypatch.setattr(heirs_mod, "get_change_output", old_change)
    monkeypatch.setattr(heirs_mod, "prepare_transactions", prepare)
    s.check()
    monkeypatch.setattr(heirs_mod, "get_change_output", real_change)
    monkeypatch.setattr(heirs_mod, "prepare_transactions", real_prepare)


@pytest.mark.parametrize("table", [ONE, THREE], ids=["1", "3"])
def test_a_will_built_before_the_fix_stays_untouched(session, monkeypatch, table):
    """Sent with the fee paid twice, then the plugin is updated: Checks change nothing."""
    s = session(table=table)
    _build_with_old_code(s, monkeypatch)
    before = set(s.bw.willitems)
    pushed = len(s.world.pushed)
    assert before
    for _ in range(2):
        s.check()
        assert set(s.bw.willitems) == before
        assert not s.user.asked and len(s.world.pushed) == pushed


def test_a_rebuild_after_the_fix_takes_over_from_the_old_will(session, monkeypatch):
    """A new will-executor after the update: the new copies pay the fee once and
    the first delivery is a day earlier, so it comes before the copies sent."""
    s = session()
    _build_with_old_code(s, monkeypatch)
    old_ids = set(s.bw.willitems)
    old_first = min(s.bw.willitems[wid].tx.locktime for wid in old_ids)
    s.world.executors["https://new1.test"] = {
        "url": "https://new1.test", "selected": True, "base_fee": 1000,
        "address": fk.address(45), "status": "200", "info": "fake"}
    s.check()
    new = [w for wid, w in s.bw.willitems.items()
           if wid not in old_ids and w.get_status("VALID")]
    assert new
    assert min(w.tx.locktime for w in new) == old_first - 86400
    assert not [wid for wid in old_ids if s.bw.willitems[wid].get_status("VALID")]
    # The old coin's copies stay in the list as replaced; the chain is whole.
    assert len({w.tx.locktime for w in new}) == 3
    _assert_each_copy_pays_once(s, 20)
    known = set(s.bw.willitems)
    s.check()
    assert set(s.bw.willitems) == known and not s.user.asked
