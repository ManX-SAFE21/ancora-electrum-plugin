"""The will's copies in the wallet's local history (#195).

Owner's ZIP test of #194 (2026-10-27): after the invalidation the new will
was prepared and, before it was signed, its copy stayed in the LOCAL
history. One heir was paid at an address of the wallet itself, and
Electrum counts the output of a local transaction as a coin of the wallet:
every Check then said "The wallet contains funds that the current will
does not cover yet", rebuilt the will and asked to sign it again. A false
alarm: that coin is not on the network. The log line "utxo is not spent"
did not name the coin.

Now:

* the Check skips the outputs of transactions not on the network (the
  same rule as the invalidation, #194), and the log line names the coin;
* the clean-up of the local history after a Check removes every labelled
  local copy that is not among the copies just saved, for any
  will-executor (before, only those with the label of a will-executor of
  the current will), and never a transaction on the network (before, the
  label alone decided);
* with "Save inheritance transactions in history" off, the Check removes
  the copies saved while it was on (before, they stayed until a rebuild).

``tests/flowkit.py`` wallets with three fake will-executors, each with its
own fee address (the fixtures of ``test_gui_deliveries.py``: ADVANCED,
luca and john).

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_local_copies_history.py
"""

import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

import test_gui_deliveries as tgd
from electrum.address_synchronizer import TX_HEIGHT_FUTURE, TX_HEIGHT_LOCAL
from electrum.transaction import tx_from_any
from PyQt6.QtWidgets import QApplication

from bal.core.util import Util
from bal.core.will import Will

_APP = QApplication.instance() or QApplication(sys.argv)

make = tgd.make

FUNDS_ALARM = "does not cover yet"

# A will-executor no longer in the will (as the owner's discarded draft).
OLD_EXECUTOR = "https://we-old.test"


def label_of(s, url):
    """The history label of the will-executor ``url``."""
    return s.bw.bal_plugin.HISTORY_LABEL.get().replace("{willexecutor}", url)


def copies(s):
    """The txids labelled as will copies and not on the network (stored or
    label only), read from the wallet itself (not through the code under
    test)."""
    template = s.bw.bal_plugin.HISTORY_LABEL.get()
    out = []
    for txid, label in s.wallet.get_all_labels().items():
        if not Util._label_matches_history(label, template):
            continue
        height = s.wallet.adb.get_tx_height(
            txid, force_local_if_missing_tx=False).height()
        if height in (TX_HEIGHT_LOCAL, TX_HEIGHT_FUTURE):
            out.append(txid)
    return out


def stored(s, txid):
    """The transaction stored in the wallet for ``txid``, or None."""
    return s.wallet.adb.get_transaction(txid)


def heir_at_own_address(s):
    """luca is paid at an unused address of the wallet itself (the owner's
    test); returns that address."""
    own = s.wallet.get_receiving_addresses()[5]
    assert s.wallet.is_mine(own)
    s.bw.heirs["luca"] = [own, "50%", tgd.D1]
    s.bw.heirs.save()
    return own


def alarms(dialog):
    """The lines of the Check that say "new funds"."""
    return [h for h in dialog.history if FUNDS_ALARM in h]


def keep_local(s, tx, url):
    """Keep ``tx`` in the local history, labelled for ``url``."""
    tx = tx_from_any(str(tx))
    s.wallet.adb.add_transaction(tx, allow_unrelated=True, is_new=False)
    s.wallet.set_label(tx.txid(), label_of(s, url))
    return tx.txid()


# ------------------------------------------------- the owner's false alarm

def test_the_owners_case_no_false_alarm(make):
    """An heir at an address of the wallet, the will left unsigned (Cancel
    at the password): its copy stays in the local history and pays the
    wallet. The next Check finds nothing new: no rebuild, the same will
    to sign."""
    s = make()
    own = heir_at_own_address(s)
    s.check(cancel_on=("password",))
    # Electrum keeps one copy per coin: the copies of the three
    # will-executors spend the same coin, the last one added replaces the
    # others (see Will.save_valid_transactions_to_history).
    kept = [t for t in copies(s) if stored(s, t) is not None]
    assert len(kept) == 1
    assert own in {o.address for o in stored(s, kept[0]).outputs()}
    before = set(s.bw.willitems)
    second = s.check(cancel_on=("password",))
    assert alarms(second) == []
    assert set(s.bw.willitems) == before
    assert ["password", "Sign your will"] in s.user.asked


def test_signed_after_the_false_alarm_is_gone(make):
    """The same wallet: the second Check signs and sends the will it
    found, with no rebuild."""
    s = make()
    heir_at_own_address(s)
    s.check(cancel_on=("password",))
    before = set(s.bw.willitems)
    second = s.check()
    assert alarms(second) == []
    assert set(s.bw.willitems) == before
    assert len(s.world.pushed) == 3
    assert all(stored(s, t) is None for t in copies(s))


def test_new_funds_are_still_found_and_named(make, caplog):
    """A real new coin: the alarm stays, and the log line names the coin
    (outpoint, amount, height)."""
    s = make()
    s.check()
    s.add_coin(250_000)
    new = next(u for u in s.wallet.get_utxos() if u.value_sats() == 250_000)
    caplog.set_level(logging.INFO)
    third = s.check()
    assert alarms(third)
    line = "utxo is not spent: {} (250000 sat, height {})".format(
        new.prevout.to_str(), new.block_height)
    assert any(line in r.getMessage() for r in caplog.records), [
        r.getMessage() for r in caplog.records if "utxo" in r.getMessage()]


# ---------------------------------------------------- the history clean-up

def test_copy_of_a_will_executor_no_longer_in_the_will_is_removed(make):
    """A copy labelled for a will-executor not in the will (a discarded
    draft): the Check's clean-up removes it, and its label."""
    s = make()
    s.check()
    item = next(iter(s.bw.willitems.values()))
    old = keep_local(s, item.tx, OLD_EXECUTOR)
    assert old in copies(s)
    s.check()
    assert stored(s, old) is None
    assert old not in copies(s)
    assert s.wallet.get_label_for_txid(old) in ("", None)


def test_a_labelled_transaction_on_the_network_is_never_removed(make):
    """A transaction on the network with the history label (here the coin
    the will spends) stays: before, the label alone decided and the Check
    removed it from the wallet."""
    s = make()
    s.check()
    funding = next(iter(s.world.broadcast))
    s.wallet.set_label(funding, label_of(s, "https://we0.test"))
    s.check()
    assert stored(s, funding) is not None
    assert funding not in copies(s)
    assert funding in {u.prevout.txid.hex() for u in s.wallet.get_utxos()}


def test_history_off_removes_the_old_copies(make):
    """"Save inheritance transactions in history" turned off: the next
    Check removes the copies saved while it was on."""
    s = make()
    s.check(cancel_on=("password",))
    assert any(stored(s, t) is not None for t in copies(s))
    s.bw.bal_plugin.SAVE_HISTORY.set(False)
    s.check(cancel_on=("password",))
    assert copies(s) == []


def test_history_copies_lists_the_local_copies_only(make):
    """``Will.history_copies``: the labelled copies not on the network,
    never a labelled transaction on the network."""
    s = make()
    s.check(cancel_on=("password",))
    funding = next(iter(s.world.broadcast))
    s.wallet.set_label(funding, label_of(s, "https://we0.test"))
    found = Will.history_copies(s.wallet, s.bw.bal_plugin.HISTORY_LABEL.get())
    assert funding not in found
    assert sorted(found) == sorted(copies(s))
    assert any(stored(s, t) is not None for t in found)


def test_history_on_keeps_the_copy_of_the_will_to_sign(make):
    """Unchanged: the copy of the will still to sign stays in the history
    while the will is not signed."""
    s = make()
    s.check(cancel_on=("password",))
    first = {t for t in copies(s) if stored(s, t) is not None}
    s.check(cancel_on=("password",))
    assert {t for t in copies(s) if stored(s, t) is not None} == first
