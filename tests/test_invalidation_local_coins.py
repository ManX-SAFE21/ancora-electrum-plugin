"""The invalidation spends only coins that exist on the network (#194).

Owner's ZIP test (2026-10-27): going back from a will in several
deliveries to one, the invalidation was refused by the network with
``bad-txns-inputs-missingorspent``. Besides the real coin it spent the
outputs of two older will transactions kept only in the wallet's LOCAL
history: Electrum counts the outputs of a local transaction as coins of
the wallet, and a later delivery spends them, so they looked like coins to
invalidate. Those transactions were never broadcast: the network does not
know them, and the whole invalidation was refused.

Now the invalidation skips a coin whose transaction is local or future
(not on the network). Spending the real coins is enough: every delivery of
every copy starts from them.

``tests/flowkit.py`` wallets with three fake will-executors, each with its
own fee address; the fake network refuses, like a real node, a transaction
that spends an output it has never seen. The fixtures of
``test_gui_deliveries.py`` (ADVANCED, luca and john, the owner's table
10/10, 10/10, 30/30 %).

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_invalidation_local_coins.py
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

import test_gui_deliveries as tgd
from electrum.address_synchronizer import TX_HEIGHT_FUTURE, TX_HEIGHT_LOCAL
from electrum.interface import TxBroadcastServerReturnedError
from electrum.transaction import tx_from_any
from PyQt6.QtWidgets import QApplication
from test_staged_back_to_one import sent_staged, valid

from bal.core import will_changes as wc
from bal.core.util import Util
from bal.core.will import Will
from bal.engine import context

_APP = QApplication.instance() or QApplication(sys.argv)

make = tgd.make

# A will-executor of an older version of the will: its local copies are
# not those of the will in force, so the history clean-up keeps them (as
# the owner's wallet did).
OLD_EXECUTOR = "https://we-old.test"


def local_copy(s):
    """Keep in the wallet's LOCAL history a delivery 1 copy of the will
    and the delivery 2 copy that spends its rest, as the owner's wallet
    had (labelled as will transactions). Returns the outpoint of the rest
    of delivery 1, which the network does not know."""
    items = list(valid(s).values())
    first = next(i for i in items if wc.delivery_number(i) == 1)
    first_txid = first.tx.txid()
    second = next(i for i in items if wc.delivery_number(i) == 2
                  and any(x.prevout.txid.hex() == first_txid
                          for x in i.tx.inputs()))
    rest = next(x.prevout.to_str() for x in second.tx.inputs()
                if x.prevout.txid.hex() == first_txid)
    label = s.bw.bal_plugin.HISTORY_LABEL.get().replace(
        "{willexecutor}", OLD_EXECUTOR)
    for item in (first, second):
        tx = tx_from_any(str(item.tx))
        s.wallet.adb.add_transaction(tx, allow_unrelated=True, is_new=False)
        s.wallet.set_label(tx.txid(), label)
    assert s.wallet.adb.get_tx_height(first_txid).height() in (
        TX_HEIGHT_LOCAL, TX_HEIGHT_FUTURE)
    # The plugin's view of the coins shows the rest of the local delivery 1
    # as a coin: its spender, the local delivery 2, is a will transaction
    # kept in the history, which ``get_available_utxos`` looks through.
    coins = Util.get_available_utxos(
        s.wallet, s.bw.bal_plugin.HISTORY_LABEL.get(),
        Will.get_min_locktime(s.bw.willitems))
    assert rest in {u.prevout.to_str() for u in coins}
    return rest


def invalidations(s):
    """The transactions the fake network accepted as invalidations."""
    return [tx_from_any(s.world.broadcast[t])
            for k, t in s.world.broadcast_log if k.startswith("invalidation")]


def spends_only_network_coins(s, tx):
    """True when every input of *tx* spends a transaction on the network."""
    return all(i.prevout.txid.hex() in s.world.broadcast for i in tx.inputs())


# ---------------------------------------------------------------- the core

def test_the_invalidation_skips_coins_of_local_transactions(make):
    """The built invalidation spends the real coins only: not the rest of
    a delivery kept in the local history."""
    s = sent_staged(make)
    rest = local_copy(s)
    tx = Will.invalidate_will(
        s.bw.willitems, s.wallet, 2,
        history_label=s.bw.bal_plugin.HISTORY_LABEL.get(),
        will_locktime=Will.get_min_locktime(s.bw.willitems))
    spent = {i.prevout.to_str() for i in tx.inputs()}
    assert rest not in spent
    assert spent and spends_only_network_coins(s, tx)
    # The real coins of the will: every one of them is spent.
    funding = {p for p in Will.get_all_inputs(s.bw.willitems)
               if p.split(":")[0] in s.world.broadcast}
    assert spent == funding


def test_the_fake_network_refuses_a_missing_coin(make):
    """The fake network refuses, like a real node, a transaction that
    spends an output it has never seen (the owner's error)."""
    s = sent_staged(make)
    first = next(i for i in valid(s).values() if wc.delivery_number(i) == 2)
    with pytest.raises(TxBroadcastServerReturnedError,
                       match="bad-txns-inputs-missingorspent"):
        s.world.record_broadcast(s.wallet, first.tx, "invalidation(test)")
    assert s.world.broadcast_log == []


# ---------------------------------------------------------------- the flows

def test_back_to_one_delivery_with_local_copies(make):
    """The owner's case: the table removed, Check. The invalidation is
    accepted by the network, it spends only real coins, then the one-date
    will is prepared and sent to the three will-executors."""
    s = sent_staged(make)
    rest = local_copy(s)
    context.clear_instalments(s.bw)
    s.check()
    sent = invalidations(s)
    assert len(sent) == 1
    assert rest not in {i.prevout.to_str() for i in sent[0].inputs()}
    assert spends_only_network_coins(s, sent[0])
    now = valid(s)
    assert len(now) == 3
    assert all(wc.delivery_number(i) == 1 for i in now.values())
    assert Will.staged_in_force(s.bw.willitems, s.wallet) == {}


def test_the_menu_invalidation_with_local_copies(make):
    """Tools > Invalidate: the transaction window broadcasts an
    invalidation the network accepts, real coins only."""
    s = sent_staged(make)
    rest = local_copy(s)
    s.menu_invalidate()
    sent = invalidations(s)
    assert len(sent) == 1
    assert rest not in {i.prevout.to_str() for i in sent[0].inputs()}
    assert spends_only_network_coins(s, sent[0])


def test_one_delivery_without_local_copies_unchanged(make):
    """A will in one delivery, no local copies: the invalidation spends
    the same real coins as before."""
    s = make()
    s.check()
    tx = Will.invalidate_will(
        s.bw.willitems, s.wallet, 2,
        history_label=s.bw.bal_plugin.HISTORY_LABEL.get(),
        will_locktime=Will.get_min_locktime(s.bw.willitems))
    funding = {p for p in Will.get_all_inputs(s.bw.willitems)}
    assert {i.prevout.to_str() for i in tx.inputs()} == funding
    assert spends_only_network_coins(s, tx)
