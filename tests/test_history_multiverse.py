"""Saving an unsigned will in several deliveries to the local history (#175).

Owner's log 2026-10-21: with "Save in the history" on, the plugin gave
Electrum the unsigned transactions of a will in several deliveries. A later
delivery spends an unsigned parent; its stored copy loses the txid, Electrum
refused it ("inconsistent txid ... != None", then "spending tx not in wallet
db") AFTER writing the spent coins and the outputs into the wallet index, and
the balance failed with an AssertionError at every refresh.

Here, with ``tests/flowkit.py`` (a throw-away testnet wallet, three fake
will-executors with their own fee addresses):

* a will in three deliveries, unsigned, saved: no entry of the wallet index
  points at a transaction the wallet does not have, the balance reads, no
  ERROR, the first delivery is still saved, the later ones are not;
* a will with one date: saved as before;
* the safety net: an add that Electrum refuses half-way leaves no entry;
* ``Will._survives_reparse`` on both kinds of transaction.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_history_multiverse.py
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

import flowkit as fk
import test_multiverse_chain_rebuild as mv
from PyQt6.QtWidgets import QApplication

from bal.core import will as will_mod
from bal.core.will import Will
from bal.engine import build as engine_build

# Kept at module level: flowkit's window needs a Qt application.
_APP = QApplication.instance() or QApplication(sys.argv)

LABEL = "BitcoinAfterLife inheritance transaction - {willexecutor}"


@pytest.fixture
def session(monkeypatch):
    """One ADVANCED flowkit session, three will-executors, synchronized."""
    fk.ensure_event_loop()
    s = fk.Session(monkeypatch, mode="advanced", password=mv.TEST_PASSWORD,
                   fee=2, locktime=mv.D1)
    s.world.add_executor()
    monkeypatch.setattr(s.wallet, "is_up_to_date", lambda: True)
    yield s
    s.close()
    fk.stop_event_loop()


@pytest.fixture
def errors(monkeypatch):
    """The ERROR and INFO lines of ``bal.core.will``, as two lists."""
    out = {"error": [], "info": []}
    monkeypatch.setattr(will_mod._logger, "error",
                        lambda m, *a, **k: out["error"].append(str(m)))
    monkeypatch.setattr(will_mod._logger, "info",
                        lambda m, *a, **k: out["info"].append(str(m)))
    return out


def _orphans(wallet):
    """Txids in the wallet index (spent coins, inputs, outputs) not stored."""
    db = wallet.db
    have = set(db.transactions.keys())
    found = {db.get_spent_outpoint(h, n) for h, n in db.list_spent_outpoints()}
    found |= set(db.txi.keys()) | set(db.txo.keys())
    return found - have


def _unsigned(s):
    """Build the will without signing it: ``{txid: item}`` of VALID items."""
    engine_build.build_will(s.bw, with_creation_date=True)
    valid = mv._valid(s)
    assert valid and not any(w.tx.is_complete() for w in valid.values())
    return valid


def _start(errors):
    """Forget the build's own log lines: only the save's are checked."""
    errors["error"].clear()
    errors["info"].clear()


def test_unsigned_deliveries_leave_the_index_whole(session, errors):
    """Three deliveries, unsigned: no broken entry, the balance reads."""
    mv.three_dates(session)
    valid = _unsigned(session)
    later = [k for k in valid if mv._level(k, session.bw.willitems) > 1]
    assert len(later) == 36
    _start(errors)
    Will.save_valid_transactions_to_history(session.bw.willitems,
                                            session.wallet, LABEL)
    assert _orphans(session.wallet) == set()
    session.wallet.get_balance()
    assert errors["error"] == []
    assert any("36 unsigned will transactions not saved" in m
               for m in errors["info"])
    db = session.wallet.db
    assert not any(db.get_transaction(k) for k in later)
    # The first delivery is still saved (one copy per coin stays: the
    # will-executors' copies spend the same coin, Electrum keeps the last).
    first = [k for k in valid if k not in later]
    assert sum(1 for k in first if db.get_transaction(k)) == 1


def test_one_date_saved_as_before(session, errors):
    """A will with one date: every copy is storable, nothing is skipped."""
    mv._set_heirs(session, ["alice", "bob"])
    valid = _unsigned(session)
    assert all(Will._survives_reparse(w.tx) for w in valid.values())
    _start(errors)
    Will.save_valid_transactions_to_history(session.bw.willitems,
                                            session.wallet, LABEL)
    assert _orphans(session.wallet) == set()
    assert errors["error"] == []
    assert not any("not saved" in m for m in errors["info"])
    assert any(session.wallet.db.get_transaction(k) for k in valid)


def test_refused_add_leaves_no_entry(session, errors, monkeypatch):
    """Safety net: Electrum refuses a tx after writing the index."""
    mv._set_heirs(session, ["alice", "bob"])
    valid = _unsigned(session)
    db = session.wallet.db

    def refuse(tx_hash, tx):
        """Electrum's late refusal, after the index writes."""
        raise Exception("trying to add tx to db with inconsistent txid")

    monkeypatch.setattr(db, "add_transaction", refuse)
    _start(errors)
    Will.save_valid_transactions_to_history(session.bw.willitems,
                                            session.wallet, LABEL)
    assert _orphans(session.wallet) == set()
    session.wallet.get_balance()
    assert len(errors["error"]) == len(valid)
    assert not any("undo" in m for m in errors["error"])


def test_survives_reparse(session):
    """The first delivery keeps its txid in its copy; a later one does not."""
    mv.two_dates(session)
    _unsigned(session)
    will = session.bw.willitems
    by_level = {}
    for k, w in mv._valid(session).items():
        by_level.setdefault(mv._level(k, will), []).append(Will._survives_reparse(w.tx))
    assert by_level == {1: [True] * 3, 2: [False] * 9}


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
