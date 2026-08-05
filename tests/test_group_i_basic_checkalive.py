"""
Tests for the "BASIC mode dynamic Check Alive" fix (Group I).

Background (reported by the owner): with the plugin left in BASIC mode (the
default), the "Check Alive" (threshold) field is hidden from the user and
stays stuck at its old/default value. If the user then anticipates the
delivery time (locktime) to something earlier than that stale threshold, the
checks that compare locktime against ``date_to_check`` would incorrectly treat
the will as "expired"/"invalid", even though the whole Check Alive concept is
supposed to be inert in BASIC mode.

The real fix lives in ``BalWindow.init_class_variables`` (window.py), which
sets ``date_to_check`` to *now* in BASIC mode, and is now implemented by the
pure policy in ``bal.core.checkalive``:

  * ``resolve_date_to_check(is_basic_mode, will_settings)`` - the single
    reference timestamp: "now" in BASIC, the stored threshold in ADVANCED;
  * ``check_alive_expired(is_basic_mode, date_to_check)`` - whether the
    Check-Alive guard should fire (never in BASIC).

These tests exercise the exact code used at runtime (no GUI/Electrum wallet
needed, and no Qt import).

Run:
    source "$BAL_HOME/electrum/env/bin/activate"
    python3 tests/test_group_i_basic_checkalive.py
"""

import sys
import time

sys.path.insert(0, __file__.rsplit("/", 2)[0])

from bal.core.checkalive import (  # noqa: E402  (path insert above)
    CheckAliveError,
    check_alive_expired,
    resolve_date_to_check,
)


def test_basic_mode_resolves_to_now():
    """BASIC mode: date_to_check is "now", never the stale stored threshold."""
    fake_now = 1_800_000_000.0
    result = resolve_date_to_check(True, {"threshold": time.time() + 86400}, now=fake_now)
    assert result == fake_now


def test_basic_mode_ignores_stored_threshold():
    """BASIC + delivery anticipated to 1 month: date_to_check stays "now" and
    does NOT jump to the old ~11-month threshold - this is the exact bug
    scenario reported by the owner."""
    stale_threshold = time.time() + 11 * 30 * 86400  # the old stuck value
    fake_now = 1_800_000_000.0
    result = resolve_date_to_check(True, {"threshold": stale_threshold}, now=fake_now)
    assert result == fake_now


def test_advanced_mode_uses_stored_threshold():
    """ADVANCED mode: behaviour stays exactly as before - the stored threshold
    is used as-is, regardless of the locktime value."""
    absolute_threshold = time.time() + 5 * 86400  # arbitrary user-chosen value
    result = resolve_date_to_check(False, {"threshold": absolute_threshold})
    assert abs(result - absolute_threshold) < 1


def test_advanced_mode_parses_relative_threshold():
    """ADVANCED + relative threshold ("30d") resolves to a future timestamp."""
    result = resolve_date_to_check(False, {"threshold": "30d"})
    assert result > time.time()


def test_basic_mode_never_expired():
    """BASIC mode can never be "expired": a passed check-alive date must never
    force a postpone/rewrite of the will."""
    assert check_alive_expired(True, time.time() - 10_000) is False
    assert check_alive_expired(True, 1_000_000_000.0) is False


def test_advanced_mode_expired_when_past():
    assert check_alive_expired(False, time.time() - 10_000) is True


def test_advanced_mode_not_expired_when_future():
    assert check_alive_expired(False, time.time() + 10_000) is False


def test_basic_mode_never_raises_check_alive_error():
    """Regression guard for the reported bug: whatever the delivery date,
    BASIC mode never raises CheckAliveError (the postpone/invalidate trigger)."""
    date_to_check = resolve_date_to_check(True, {"threshold": "30d"})
    assert not check_alive_expired(True, date_to_check)
    # If it did fire, this is the exact exception that would be raised.
    assert issubclass(CheckAliveError, Exception)


if __name__ == "__main__":
    for name in sorted(dir()):
        if name.startswith("test_"):
            globals()[name]()
            print(f"  [OK] {name}")
    print("[OK] All test_group_i_basic_checkalive tests passed.")
