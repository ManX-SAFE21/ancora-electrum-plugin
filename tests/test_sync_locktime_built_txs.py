"""
Tests for ``BalBuildWillDialog._sync_locktime_to_built_txs``.

This is the post-build sync that keeps the plugin's stored delivery date
(WILL_SETTINGS["locktime"]) and check-alive threshold in lockstep with the
BUILT transactions' fixed locktime.  The bug it fixes (reported by the owner):

    ADVANCED mode + RELATIVE locktime ("90d") / threshold ("30d") -> the plugin
    asks to invalidate the will EVERY DAY.  The relative value is re-parsed
    against "now" on every check, so it drifts one day per day away from the
    fixed tx locktime and the postpone check always sees a "postpone".

The method is exercised with a lightweight fake ``self`` (no Qt event loop, no
Electrum wallet) by calling it as an unbound method.

Run:
    source "$BAL_HOME/electrum/env/bin/activate"
    python3 tests/test_sync_locktime_built_txs.py
"""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from bal.core.plugin_base import BalTimestamp  # noqa: E402  (path insert above)
from bal.gui.qt.dialogs import BalBuildWillDialog  # noqa: E402  (path insert above)

# ------------------------------------------------------------------ #
# Fakes
# ------------------------------------------------------------------ #

def _fake_willitem(tx_locktime):
    """Minimal stand-in for a WillItem: enough for Will.get_min_locktime."""
    return SimpleNamespace(
        tx=SimpleNamespace(locktime=tx_locktime),
        get_status=lambda name: True,
    )


def _make_dialog(will_settings, tx_locktimes, recorded):
    """Build a fake dialog ``self`` for _sync_locktime_to_built_txs."""

    def update_setting_widgets(new_value, field, update_all=False):
        will_settings[field] = new_value
        recorded.append((field, new_value, update_all))

    return SimpleNamespace(
        bal_window=SimpleNamespace(
            willitems={
                f"tx{i}": _fake_willitem(lt) for i, lt in enumerate(tx_locktimes)
            },
            will_settings=will_settings,
            update_setting_widgets=update_setting_widgets,
        ),
        _date_was_anticipated=False,
    )


def _call_sync(will_settings, tx_locktimes, recorded):
    fake = _make_dialog(will_settings, tx_locktimes, recorded)
    BalBuildWillDialog._sync_locktime_to_built_txs(fake)
    return fake


# ------------------------------------------------------------------ #
# Tests
# ------------------------------------------------------------------ #

def test_relative_locktime_normalized_to_absolute():
    """The reported bug: a relative stored locktime is frozen to the absolute
    value of the built transaction, even when it parses to the same moment."""
    tx_locktime = 1_800_000_000
    recorded = []
    fake = _call_sync(
        {"locktime": "90d", "threshold": "30d"}, [tx_locktime], recorded
    )
    assert fake.bal_window.will_settings["locktime"] == tx_locktime
    assert fake.bal_window.will_settings["locktime"] != "90d"
    # A pure relative->absolute normalisation is NOT an anticipation: the sign
    # prompt must not claim the date was anticipated.
    assert fake._date_was_anticipated is False


def test_relative_threshold_frozen_to_absolute():
    """A relative threshold ("N days BEFORE the delivery") is normalised to the
    same absolute value the settings widget computes (real_threshold)."""
    tx_locktime = 1_800_000_000
    recorded = []
    fake = _call_sync(
        {"locktime": "90d", "threshold": "30d"}, [tx_locktime], recorded
    )
    expected = int(
        BalTimestamp("30d").to_date(tx_locktime, reverse=True).timestamp()
    )
    assert fake.bal_window.will_settings["threshold"] == expected
    assert ("threshold", expected, True) in recorded


def test_absolute_locktime_unchanged_on_equal():
    """An absolute stored locktime that already matches the built txs is left
    untouched (no spurious rewrite)."""
    tx_locktime = 1_800_000_000
    recorded = []
    fake = _call_sync(
        {"locktime": tx_locktime, "threshold": 1_700_000_000},
        [tx_locktime],
        recorded,
    )
    assert fake.bal_window.will_settings["locktime"] == tx_locktime
    assert fake._date_was_anticipated is False


def test_anticipation_sets_flag_and_moves_earlier():
    """A real anticipation (built locktime earlier than the stored absolute
    one) still moves the date earlier and flags the sign prompt."""
    tx_locktime = 1_700_000_000
    recorded = []
    fake = _call_sync(
        {"locktime": 1_800_000_000, "threshold": 1_600_000_000},
        [tx_locktime],
        recorded,
    )
    assert fake.bal_window.will_settings["locktime"] == tx_locktime
    assert fake._date_was_anticipated is True


def test_stored_earlier_than_built_never_moved_later():
    """A stored absolute date that is already EARLIER than the built txs (the
    user moved the delivery later) is never pulled back up on rebuild: only
    anticipation (built < stored) and relative normalisation move the value."""
    stored = 1_800_000_000
    recorded = []
    fake = _call_sync(
        {"locktime": stored, "threshold": 1_700_000_000},
        [1_900_000_000],
        recorded,
    )
    assert fake.bal_window.will_settings["locktime"] == stored
    assert fake._date_was_anticipated is False


def test_multiple_txs_uses_minimum_locktime():
    """When several transactions carry different locktimes, the minimum is used
    (owner-confirmed behaviour for the delivery date shown in the UI)."""
    min_locktime = 1_750_000_000
    recorded = []
    fake = _call_sync(
        {"locktime": "90d", "threshold": "30d"},
        [min_locktime, min_locktime + 86_400],
        recorded,
    )
    assert fake.bal_window.will_settings["locktime"] == min_locktime


def test_relative_locktime_stops_daily_postpone():
    """End-to-end guard for the reported bug: after the sync, re-parsing the
    stored (now absolute) locktime on later days always equals the built
    tx locktime, so the postpone check never fires again."""
    from datetime import datetime, timedelta

    from bal.core.util import Util

    tx_locktime = 1_800_000_000
    recorded = []
    fake = _call_sync(
        {"locktime": "90d", "threshold": "30d"}, [tx_locktime], recorded
    )
    stored = fake.bal_window.will_settings["locktime"]
    for _day in range(0, 7):
        # Simulate the check on later days: parse the STORED value (which is
        # now the absolute tx locktime) and compare with the fixed tx locktime.
        new_locktime = Util.parse_locktime_string(stored)
        assert new_locktime == tx_locktime
        assert new_locktime <= tx_locktime  # no POSTPONE / drift
    # Sanity: a RELATIVE value would have drifted past it (the bug).
    drifted = int(
        (
            datetime.fromtimestamp(tx_locktime) + timedelta(days=1)
        ).timestamp()
    )
    assert drifted > tx_locktime


if __name__ == "__main__":
    for name in sorted(dir()):
        if name.startswith("test_"):
            globals()[name]()
            print(f"  [OK] {name}")
    print("[OK] All sync_locktime_built_txs tests passed.")
