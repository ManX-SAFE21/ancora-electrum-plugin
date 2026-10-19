"""The delivery form is locked while a will is in force (CHANGELOG #127).

Owner decision 2 (2026-10-12): a will delivered on a fixed date or after a
renewable period keeps its form while it is in force. To change the form
the will must be invalidated on-chain (miner fee) and the invalidation
CONFIRMED; then the new form is chosen and the new will prepared. Changing
the period N while a will is in force is allowed: N applies at the next
renewal (no fee now).

Here, on the real flows of ``tests/flowkit.py`` (three fake will-executors
with their own fee addresses, a throw-away test wallet with the test
password, a fixed clock):

* the lock predicate ``bal.engine.context.form_locked`` on a real wallet:
  a will prepared but never signed does not lock; a will signed and sent
  locks; an invalidation only in the mempool still locks (it can be
  dropped), a confirmed one unlocks; a wallet that is not synchronized
  stays locked;
* the refusals of ``context.set_delivery`` (the one validated write of the
  form) and the change of N with a will in force;
* the v2 upgrade of the wallet's settings (form "fixed", idempotent) and the
  hosts whose settings are a plain dict (tests, fakes), never touched.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_delivery_form_lock.py
"""

import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

import flowkit as fk
from electrum.util import TxMinedInfo
from PyQt6.QtWidgets import QApplication

from bal.core import delivery
from bal.engine import context as engine_context

# Kept at module level: an unreferenced application object is destroyed at
# once, and creating a widget afterwards aborts Python.
_APP = QApplication.instance() or QApplication(sys.argv)

# Test value: the password of the throw-away test wallets.
TEST_PASSWORD = "flowkit-test"


@pytest.fixture
def make(monkeypatch):
    """``tests/flowkit.py`` sessions with three will-executors."""
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


def synchronized(s, monkeypatch, value=True):
    """Make ``wallet.is_up_to_date()`` answer what the returned dict holds."""
    state = {"now": value}
    monkeypatch.setattr(s.wallet, "is_up_to_date", lambda: state["now"])
    return state


def wallet_dict(s):
    """The wallet's own delivery settings (the live dict)."""
    return s.wallet.db.get_dict("will_settings")


def invalidated_by_hand(s):
    """Will tab > Invalidate: the transaction window signs and broadcasts
    the invalidation (in the mempool). Returns its txid."""
    s.menu_invalidate()
    kinds = [k for k, _t in s.world.broadcast_log]
    assert kinds == ["invalidation(transaction window)"], kinds
    return s.world.broadcast_log[-1][1]


def confirm(s, txid, monkeypatch):
    """The invalidation is mined at height 100 and SPV-verified, the chain
    tip at 105: Electrum reports a mined height only once SPV-verified
    (``TxMinedInfo.height``), as a synchronized wallet does."""
    monkeypatch.setattr(s.wallet.adb, "get_local_height", lambda: 105)
    s.wallet.adb.add_verified_tx(txid, TxMinedInfo(_height=100, conf=1))
    assert s.wallet.adb.get_tx_height(txid).height() == 100


# ------------------------------------------------------------ the predicate

def test_unsigned_will_does_not_lock(make, monkeypatch):
    """Cancel at "Sign your will": the VALID items were never in force, the
    form can still change (synchronized or not)."""
    s = make(mode="advanced")
    s.check(cancel_on={"Sign your will"})
    assert any(w.get_status("VALID") for w in s.bw.willitems.values())
    assert not any(w.get_status("COMPLETE") for w in s.bw.willitems.values())
    state = synchronized(s, monkeypatch, False)
    assert engine_context.form_locked(s.bw) is False
    state["now"] = True
    assert engine_context.form_locked(s.bw) is False


def test_signed_and_sent_will_locks(make, monkeypatch):
    """The first Check signs and sends: locked, synchronized or not."""
    s = make(mode="advanced")
    s.check()
    state = synchronized(s, monkeypatch, True)
    assert engine_context.form_locked(s.bw) is True
    state["now"] = False
    assert engine_context.form_locked(s.bw) is True


def test_mempool_invalidation_still_locks_a_confirmed_one_unlocks(make, monkeypatch):
    """Invalidate by hand: while the invalidation is only in the mempool the
    old will could still be executed (it can be dropped or replaced), so
    the form stays locked. Mined: unlocked. A wallet that is not
    synchronized stays locked (its flags may be stale)."""
    s = make(mode="advanced")
    s.check()
    state = synchronized(s, monkeypatch, True)
    txid = invalidated_by_hand(s)
    assert engine_context.form_locked(s.bw) is True
    statuses = [w.get_status("INVALIDATED") for w in s.bw.willitems.values()]
    assert statuses and all(statuses)          # marked by the refresh
    confirm(s, txid, monkeypatch)
    assert engine_context.form_locked(s.bw) is False
    state["now"] = False
    assert engine_context.form_locked(s.bw) is True


def test_unreadable_will_counts_as_locked():
    """When the will cannot be read the form is locked (the safe answer)."""
    host = SimpleNamespace(willitems={"x": object()}, wallet=None,
                           bal_plugin=None)
    assert engine_context.form_locked(host, refresh=False) is True


# ------------------------------------------------------------ set_delivery

def test_form_change_refused_while_locked(make, monkeypatch):
    """A fixed will signed and sent: the period form is refused with
    ``DeliveryFormLocked`` and nothing is written."""
    s = make(mode="advanced")
    s.check()
    synchronized(s, monkeypatch, True)
    d = wallet_dict(s)
    before = dict(d)
    with pytest.raises(delivery.DeliveryFormLocked) as e:
        engine_context.set_delivery(s.bw, form="period", months=36)
    assert e.value.current_form == "fixed" and e.value.code == "delivery_form_locked"
    assert dict(d) == before
    # A new FIXED date is not a form change: allowed (the paid postpone of
    # the Check follows, as before).
    out = engine_context.set_delivery(s.bw, form="fixed", date=fk.LATER)
    assert out["locktime"] == fk.LATER and out["form"] == "fixed"


def test_bad_values_are_refused_before_anything_is_written(make):
    """Every refusal of ``set_delivery`` writes nothing."""
    s = make(mode="advanced")
    d = wallet_dict(s)
    before = dict(d)
    for kw in ({"form": "monthly"},
               {"form": "period", "months": 0},
               {"form": "period", "months": 241},
               {"form": "period", "months": "36m"},
               {"form": "period"},                      # no N stored yet
               {"form": "period", "months": 36, "date": fk.LATER},
               {"form": "fixed", "months": 36},
               {"months": 36},                          # fixed form stored
               {"form": "fixed", "date": "1054d"}):
        with pytest.raises(ValueError):
            engine_context.set_delivery(s.bw, **kw)
        assert dict(d) == before, kw


def test_no_period_form_with_multiverse(make):
    """Owner decision 10: a will in deliveries (a table, #168; BAL's hidden
    multiverse option before) has no period form."""
    s = make(mode="advanced")
    date = delivery.as_delivery(wallet_dict(s)["locktime"])
    s.set_instalments((date, {"alice": "60%", "bob": "0%"}),
                      (date + 365 * 86400, {"alice": "0%", "bob": "40%"}))
    with pytest.raises(ValueError, match="table of deliveries"):
        engine_context.set_delivery(s.bw, form="period", months=36)
    assert delivery.form_of(wallet_dict(s)) == "fixed"


def test_settings_on_hold_refuse_the_form(make):
    """Display-only settings (fail closed, #123) are never written."""
    s = make(mode="advanced")
    s.bw.settings_unavailable = True
    with pytest.raises(delivery.DeliverySettingsUnavailable):
        engine_context.set_delivery(s.bw, form="period", months=36)


def test_period_with_no_will_in_force_dates_it_now(make):
    """No will yet: the period form stores N, today as ``period_start`` and
    today + N months as the delivery date (00:00 UTC). Back to fixed: the
    date stays, the period keys go."""
    s = make(mode="advanced")
    out = engine_context.set_delivery(s.bw, form="period", months=36)
    d = wallet_dict(s)
    assert out == {"form": "period", "period_months": 36,
                   "locktime": fk.ts(2029, 10, 10), "applied": True}
    assert d["period_start"] == fk.ts(2026, 10, 10)
    out = engine_context.set_delivery(s.bw, form="fixed")
    assert out["form"] == "fixed" and out["locktime"] == fk.ts(2029, 10, 10)
    assert "period_months" not in d and "period_start" not in d


def test_changing_n_with_a_will_in_force_waits_for_the_renewal(make, monkeypatch):
    """A period will signed and sent: N changes (no fee now), the delivery
    date and ``period_start`` do not; the Check is Ok with nothing to do."""
    s = make(mode="advanced", period_months=2, threshold="-30d")
    s.check()
    synchronized(s, monkeypatch, True)
    d = wallet_dict(s)
    date, start = d["locktime"], d["period_start"]
    out = engine_context.set_delivery(s.bw, months=3)
    assert out == {"form": "period", "period_months": 3, "locktime": date,
                   "applied": False}
    assert (d["locktime"], d["period_start"]) == (date, start)
    pushed = len(s.world.pushed)
    s.check()
    step = s.recorder.steps[-1]
    assert step["broadcasts"] == [] and len(s.world.pushed) == pushed
    assert step["settings"]["period_months"] == 3
    assert step["settings"]["locktime"] == fk.fmt(date)


# ------------------------------------------------------------ v2 upgrade

def as_version_1(s):
    """The wallet's settings as #123 left them: schema 1, no form."""
    d = wallet_dict(s)
    d["settings_version"] = 1
    if "form" in d:
        del d["form"]
    return d


def test_v1_settings_become_v2_fixed_once(make, monkeypatch):
    """At the next load a schema-1 dict gets ``form`` "fixed" and
    ``settings_version`` 2, nothing else changes; a second load writes
    nothing (no wallet-file flush)."""
    s = make(mode="advanced")
    s.check()
    d = as_version_1(s)
    before = dict(d)
    flushes = []
    monkeypatch.setattr(s.bw, "flush_wallet_db", lambda: flushes.append(1))
    s.reload_window()
    assert dict(d) == dict(before, settings_version=2, form="fixed")
    assert flushes == [1]
    s.reload_window()
    assert flushes == [1]                          # idempotent
    s.check()
    assert "All done" in " ".join(s.recorder.steps[-1]["rows"])


def test_plain_dict_hosts_are_never_touched():
    """A host whose settings are a plain dict without ``form`` (the tests'
    fakes): fixed, and neither the policy nor the later wish writes it."""
    settings = {"locktime": fk.D0, "baltx_fees": 13, "threshold": "-180d"}
    host = SimpleNamespace(will_settings=dict(settings), willitems={},
                           wallet=SimpleNamespace(db=None))
    assert delivery.form_of(host.will_settings) == "fixed"
    assert engine_context.apply_delivery_policy(host) is None
    period = dict(settings, form="period", period_months=2)
    host.will_settings = dict(period)
    assert engine_context.apply_delivery_policy(host) is None
    assert host.will_settings == period


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
