"""
Tests for ``bal.core.checkalive`` (pure, GUI-free).

Covers the CheckAliveError exception and the BASIC/ADVANCED date_to_check
policy (``resolve_date_to_check`` / ``check_alive_expired``).

Run:
    source "$BAL_HOME/electrum/env/bin/activate"
    python3 tests/test_core_checkalive.py
"""

import sys
import time

sys.path.insert(0, __file__.rsplit("/", 2)[0])

from bal.core.checkalive import (  # noqa: E402  (path insert above)
    CheckAliveError,
    check_alive_expired,
    resolve_date_to_check,
    resolve_guard_threshold,
)

# ------------------------------------------------------------------ #
# CheckAliveError
# ------------------------------------------------------------------ #


def test_check_alive_error_default():
    err = CheckAliveError(1000000)
    assert err.timestamp_to_check == 1000000


def test_check_alive_error_str():
    err = CheckAliveError(1000000)
    s = str(err)
    assert "Check alive expired" in s
    assert "1970" in s


def test_check_alive_error_subclass():
    assert issubclass(CheckAliveError, Exception)


# ------------------------------------------------------------------ #
# resolve_date_to_check
# ------------------------------------------------------------------ #


def test_basic_mode_uses_now():
    fake_now = 1_800_000_000.0
    assert resolve_date_to_check(True, {}, now=fake_now) == fake_now


def test_advanced_mode_uses_threshold_absolute():
    threshold = time.time() + 5 * 86400
    settings = {"threshold": threshold}
    result = resolve_date_to_check(False, settings)
    assert abs(result - threshold) < 1


def test_advanced_mode_parses_relative_threshold():
    # A relative threshold means "N days BEFORE the delivery": it resolves
    # against the stored locktime (backwards), not forward from now.
    from datetime import datetime, timedelta, timezone

    fake_now = 1_800_000_000.0
    locktime = fake_now + 90 * 86400
    settings = {"threshold": "30d", "locktime": locktime}
    result = resolve_date_to_check(False, settings, now=fake_now)
    # date_to_check = (locktime, midnight-normalised) - 30 days.
    expected = (datetime.fromtimestamp(locktime, tz=timezone.utc)
                .replace(hour=0, minute=0, second=0, microsecond=0)
                - timedelta(days=30)).timestamp()
    assert abs(result - expected) < 1
    # 90d delivery with a 30d window: the window starts 60 days after now.
    assert result > fake_now


def test_advanced_mode_relative_threshold_anchored_to_locktime():
    """A relative threshold never drifts with the clock: re-resolving it a day
    later, with the same fixed absolute locktime, yields the same date."""
    fake_now = 1_800_000_000.0
    locktime = fake_now + 90 * 86400
    settings = {"threshold": "30d", "locktime": locktime}
    first = resolve_date_to_check(False, settings, now=fake_now)
    # Next day: same stored settings (the fixed delivery), a later clock.
    second = resolve_date_to_check(False, settings, now=fake_now + 86400)
    assert first == second


def test_advanced_mode_relative_threshold_with_relative_locktime():
    """A relative locktime is resolved against 'now' first, then the relative
    threshold counts N days back from it (matches the settings widget)."""
    from datetime import datetime, timezone

    from bal.core.plugin_base import BalTimestamp

    fake_now = 1_800_000_000.0
    settings = {"threshold": "30d", "locktime": "90d"}
    result = resolve_date_to_check(False, settings, now=fake_now)
    # Recompute the expected value with the same resolution rules:
    # locktime = now + 90d (midnight-normalised), threshold = locktime - 30d.
    locktime_dt = BalTimestamp("90d").to_date(datetime.fromtimestamp(fake_now, tz=timezone.utc))
    expected = BalTimestamp("30d").to_date(locktime_dt, reverse=True).timestamp()
    assert abs(result - expected) < 1
    assert result > fake_now


def test_advanced_mode_relative_threshold_no_locktime_falls_back():
    # Without a locktime reference, fall back to the legacy forward resolution.
    settings = {"threshold": "30d"}
    result = resolve_date_to_check(False, settings)
    assert result > time.time()


def test_advanced_mode_relative_locktime_anchored_to_built_tx():
    """A RELATIVE stored locktime is anchored to the built will's frozen
    delivery date (built_locktime), not to "now": an unchanged will must not
    read as expired as the clock advances (the karen7 daily-invalidate bug)."""
    frozen = 1817424000  # frozen tx locktime (2027-08-05 00:00 UTC), built 2026-08-05
    settings = {"threshold": "30d", "locktime": "2y"}
    # On build day the frozen delivery is authoritative: date_to_check is
    # frozen - 30d and NEVER drifts, however much later the clock gets.
    first = resolve_date_to_check(
        False, settings, now=1_800_000_000.0, built_locktime=frozen
    )
    assert abs(first - (frozen - 30 * 86400)) < 1
    later = resolve_date_to_check(
        False, settings, now=1_800_000_000.0 + 10 * 86400, built_locktime=frozen
    )
    assert first == later
    # The check window must start BEFORE the frozen delivery (never expired).
    assert first < frozen


def test_advanced_mode_relative_locktime_without_built_tx_falls_back():
    """Without a built will there is no anchor: keeps the legacy now-based
    resolution (a moving target, used only before the first build)."""
    from datetime import datetime, timezone

    from bal.core.plugin_base import BalTimestamp

    fake_now = 1_800_000_000.0
    settings = {"threshold": "30d", "locktime": "90d"}
    result = resolve_date_to_check(False, settings, now=fake_now)
    locktime_dt = BalTimestamp("90d").to_date(datetime.fromtimestamp(fake_now, tz=timezone.utc))
    expected = BalTimestamp("30d").to_date(locktime_dt, reverse=True).timestamp()
    assert abs(result - expected) < 1


# ------------------------------------------------------------------ #
# resolve_guard_threshold
# ------------------------------------------------------------------ #


def test_guard_threshold_basic_mode_returns_none():
    fake_now = 1_800_000_000.0
    threshold = resolve_guard_threshold(True, {"threshold": "30d"}, now=fake_now)
    assert threshold is None


def _guard_locktime(settings, fake_now):
    """Reproduce the call-site locktime expression of the guard."""
    from bal.core.plugin_base import BalTimestamp

    return BalTimestamp(settings["locktime"]).to_timestamp(fake_now)


def test_guard_threshold_absolute():
    fake_now = 1_800_000_000.0
    locktime = fake_now + 90 * 86400
    threshold = locktime - 30 * 86400
    settings = {"locktime": locktime, "threshold": threshold}
    assert resolve_guard_threshold(False, settings, now=fake_now) == threshold


def test_guard_threshold_relative_fresh_anchor():
    """A relative threshold must be anchored to the FRESH locktime so the
    guard and the settings always share one reference frame.

    Regression for the false positive where a still-valid built will frozen at
    a LONGER delivery ("2y") anchored ``date_to_check`` beyond the currently
    stored shorter delivery ("1y"): the old guard compared the fresh "1y"
    locktime against that anchored threshold and wrongly fired "locktime is
    lower than threshold", even though the settings themselves are consistent
    (locktime is 30d AFTER the threshold).
    """
    fake_now = 1_800_000_000.0
    settings = {"locktime": "1y", "threshold": "30d"}
    locktime = _guard_locktime(settings, fake_now)
    threshold = resolve_guard_threshold(False, settings, now=fake_now)
    assert threshold is not None
    assert locktime > threshold  # internally consistent: no fire
    assert threshold > fake_now
    # The helper takes no built anchor: a frozen "2y" built will must NOT
    # contaminate the result, although resolve_date_to_check (the expiry
    # reference) legitimately keeps using it.
    frozen_two_years = locktime + 365 * 86400
    anchored = resolve_date_to_check(
        False, settings, now=fake_now, built_locktime=frozen_two_years
    )
    assert anchored > threshold  # built anchor pushes date_to_check forward...
    assert locktime < anchored  # ...which is exactly what used to fire the bug


def test_guard_threshold_relative_locktime_absolute_threshold():
    fake_now = 1_800_000_000.0
    threshold = fake_now + 200 * 86400
    settings = {"locktime": "1y", "threshold": threshold}
    assert resolve_guard_threshold(False, settings, now=fake_now) == threshold
    # "1y" from now is later than the stored absolute threshold: allowed.
    locktime = _guard_locktime(settings, fake_now)
    assert locktime > threshold


def test_guard_threshold_missing_returns_none():
    fake_now = 1_800_000_000.0
    assert resolve_guard_threshold(False, {"locktime": "1y"}, now=fake_now) is None


# ------------------------------------------------------------------ #
# check_alive_expired
# ------------------------------------------------------------------ #


def test_basic_mode_never_expired():
    assert check_alive_expired(True, 1_000_000_000.0) is False
    assert check_alive_expired(True, time.time() - 10_000) is False


def test_advanced_expired_when_past():
    assert check_alive_expired(False, time.time() - 10_000) is True


def test_advanced_not_expired_when_future():
    assert check_alive_expired(False, time.time() + 10_000) is False


# ------------------------------------------------------------------ #
# Main
# ------------------------------------------------------------------ #

if __name__ == "__main__":
    for name in sorted(dir()):
        if name.startswith("test_"):
            globals()[name]()
            print(f"  [OK] {name}")
    print("[OK] All core checkalive tests passed")
