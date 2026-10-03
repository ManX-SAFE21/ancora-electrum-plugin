"""
Tests for ``bal.core.checkalive`` (pure, GUI-free).

Covers the CheckAlive exceptions and the BASIC/ADVANCED date_to_check policy
(``resolve_date_to_check`` / ``resolve_relative_check_alive_date`` /
``is_check_alive_date_valid`` / ``ensure_check_alive_valid``), including the
sign rule: a NEGATIVE relative Check Alive counts back from the delivery, a
POSITIVE one counts forward from the inheritance creation date.

Run:
    source "$BAL_HOME/electrum/env/bin/activate"
    python3 tests/test_core_checkalive.py
"""

import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, __file__.rsplit("/", 2)[0])

from bal.core.checkalive import (  # noqa: E402  (path insert above)
    CheckAliveAfterDeliveryError,
    CheckAliveError,
    ensure_check_alive_valid,
    is_check_alive_date_valid,
    resolve_date_to_check,
    resolve_delivery_date,
    resolve_guard_threshold,
    resolve_relative_check_alive_date,
)
from bal.core.plugin_base import BalTimestamp  # noqa: E402
from bal.core.will import Will  # noqa: E402


def _midnight(ts):
    return (
        datetime.fromtimestamp(ts, tz=timezone.utc)
        .replace(hour=0, minute=0, second=0, microsecond=0)
        .timestamp()
    )


# ------------------------------------------------------------------ #
# CheckAliveError / CheckAliveAfterDeliveryError
# ------------------------------------------------------------------ #


def test_check_alive_error_default():
    err = CheckAliveError(1000000)
    assert err.timestamp_to_check == 1000000
    assert err.reason is None


def test_check_alive_error_str():
    err = CheckAliveError(1000000)
    s = str(err)
    assert "Check alive expired" in s
    assert "1970" in s


def test_check_alive_error_str_with_reason():
    err = CheckAliveError(1000000, reason="too early")
    assert "too early" in str(err)


def test_check_alive_error_subclass():
    assert issubclass(CheckAliveError, Exception)


def test_check_alive_after_delivery_error_is_sibling():
    # NOT a subclass of CheckAliveError: the existing ``except CheckAliveError``
    # handlers must not invalidate the will for this configuration error.
    assert not issubclass(CheckAliveAfterDeliveryError, CheckAliveError)
    err = CheckAliveAfterDeliveryError(2000, 1000)
    assert err.timestamp_to_check == 2000
    assert err.delivery_timestamp == 1000
    assert "delivery" in str(err)


# ------------------------------------------------------------------ #
# resolve_delivery_date
# ------------------------------------------------------------------ #


def test_delivery_absolute_locktime():
    settings = {"locktime": 1_800_000_000}
    assert resolve_delivery_date(settings) == 1_800_000_000


def test_delivery_relative_locktime_resolved_from_now():
    fake_now = 1_800_000_000.0
    settings = {"locktime": "90d"}
    result = resolve_delivery_date(settings, now=fake_now)
    expected = _midnight(fake_now) + 90 * 86400
    assert abs(result - expected) < 1


def test_delivery_relative_locktime_anchored_to_built_tx():
    frozen = 1817424000
    settings = {"locktime": "2y"}
    result = resolve_delivery_date(
        settings, now=1_800_000_000.0, built_locktime=frozen
    )
    assert result == frozen


def test_delivery_missing_locktime_returns_none():
    assert resolve_delivery_date({}) is None


# ------------------------------------------------------------------ #
# resolve_relative_check_alive_date (the single calculation rule)
# ------------------------------------------------------------------ #


def test_relative_negative_anchors_to_delivery():
    fake_now = 1_800_000_000.0
    delivery = fake_now + 90 * 86400
    result = resolve_relative_check_alive_date(
        "-30d", delivery_date=delivery, creation_date=fake_now, now=fake_now
    )
    expected = _midnight(delivery) - 30 * 86400
    assert abs(result - expected) < 1
    assert result < delivery


def test_relative_positive_anchors_to_creation():
    fake_now = 1_800_000_000.0
    creation = fake_now + 5 * 86400
    delivery = fake_now + 365 * 86400
    result = resolve_relative_check_alive_date(
        "30d", delivery_date=delivery, creation_date=creation, now=fake_now
    )
    expected = _midnight(creation) + 30 * 86400
    assert abs(result - expected) < 1
    # The delivery anchor is irrelevant for a positive value.
    assert result < delivery


def test_relative_positive_without_creation_falls_back_to_now():
    fake_now = 1_800_000_000.0
    result = resolve_relative_check_alive_date(
        "30d", delivery_date=fake_now + 365 * 86400, creation_date=None, now=fake_now
    )
    expected = _midnight(fake_now) + 30 * 86400
    assert abs(result - expected) < 1


def test_relative_negative_without_delivery_falls_back_to_now():
    fake_now = 1_800_000_000.0
    result = resolve_relative_check_alive_date(
        "-30d", delivery_date=None, creation_date=fake_now, now=fake_now
    )
    expected = _midnight(fake_now) - 30 * 86400
    assert abs(result - expected) < 1


def test_relative_absolute_value_returned_unchanged():
    assert resolve_relative_check_alive_date(1_700_000_000, None, None) == 1_700_000_000


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


def test_advanced_mode_negative_relative_anchored_to_delivery():
    fake_now = 1_800_000_000.0
    locktime = fake_now + 90 * 86400
    settings = {"threshold": "-30d", "locktime": locktime}
    result = resolve_date_to_check(False, settings, now=fake_now)
    expected = _midnight(locktime) - 30 * 86400
    assert abs(result - expected) < 1
    assert result > fake_now


def test_advanced_mode_positive_relative_anchored_to_creation():
    fake_now = 1_800_000_000.0
    creation = fake_now + 10 * 86400
    locktime = fake_now + 365 * 86400
    settings = {"threshold": "30d", "locktime": locktime}
    result = resolve_date_to_check(
        False, settings, now=fake_now, creation_date=creation
    )
    expected = _midnight(creation) + 30 * 86400
    assert abs(result - expected) < 1


def test_advanced_mode_negative_relative_never_drifts():
    """A negative relative threshold anchored to a fixed absolute delivery does
    not drift with the clock."""
    fake_now = 1_800_000_000.0
    locktime = fake_now + 90 * 86400
    settings = {"threshold": "-30d", "locktime": locktime}
    first = resolve_date_to_check(False, settings, now=fake_now)
    second = resolve_date_to_check(False, settings, now=fake_now + 86400)
    assert first == second


def test_advanced_mode_positive_relative_creation_never_drifts():
    """A positive relative threshold anchored to the frozen creation date does
    not drift with the clock."""
    fake_now = 1_800_000_000.0
    creation = fake_now + 2 * 86400
    settings = {"threshold": "30d", "locktime": fake_now + 365 * 86400}
    first = resolve_date_to_check(
        False, settings, now=fake_now, creation_date=creation
    )
    second = resolve_date_to_check(
        False, settings, now=fake_now + 5 * 86400, creation_date=creation
    )
    assert first == second


def test_advanced_mode_relative_locktime_anchored_to_built_tx():
    """A RELATIVE stored locktime is anchored to the built will's frozen
    delivery date (built_locktime), not to "now": an unchanged will must not
    read as expired as the clock advances (the karen7 daily-invalidate bug)."""
    frozen = 1817424000  # frozen tx locktime, built 2026-08-05
    settings = {"threshold": "-30d", "locktime": "2y"}
    first = resolve_date_to_check(
        False, settings, now=1_800_000_000.0, built_locktime=frozen
    )
    assert abs(first - (frozen - 30 * 86400)) < 1
    later = resolve_date_to_check(
        False, settings, now=1_800_000_000.0 + 10 * 86400, built_locktime=frozen
    )
    assert first == later
    assert first < frozen


def test_advanced_mode_relative_locktime_without_built_tx_falls_back():
    fake_now = 1_800_000_000.0
    settings = {"threshold": "-30d", "locktime": "90d"}
    result = resolve_date_to_check(False, settings, now=fake_now)
    locktime_dt = BalTimestamp("90d").to_date(fake_now)
    expected = BalTimestamp("-30d").to_date(locktime_dt).timestamp()
    assert abs(result - expected) < 1


# ------------------------------------------------------------------ #
# resolve_guard_threshold
# ------------------------------------------------------------------ #


def test_guard_threshold_basic_mode_returns_none():
    fake_now = 1_800_000_000.0
    threshold = resolve_guard_threshold(True, {"threshold": "-30d"}, now=fake_now)
    assert threshold is None


def _guard_locktime(settings, fake_now):
    """Reproduce the call-site locktime expression of the guard."""
    return BalTimestamp(settings["locktime"]).to_timestamp(fake_now)


def test_guard_threshold_absolute():
    fake_now = 1_800_000_000.0
    locktime = fake_now + 90 * 86400
    threshold = locktime - 30 * 86400
    settings = {"locktime": locktime, "threshold": threshold}
    assert resolve_guard_threshold(False, settings, now=fake_now) == threshold


def test_guard_threshold_negative_relative_fresh_anchor():
    """A negative relative threshold is anchored to the FRESH locktime so the
    guard and the settings always share one reference frame."""
    fake_now = 1_800_000_000.0
    settings = {"locktime": "1y", "threshold": "-30d"}
    locktime = _guard_locktime(settings, fake_now)
    threshold = resolve_guard_threshold(False, settings, now=fake_now)
    assert threshold is not None
    assert locktime > threshold  # internally consistent: no fire
    assert threshold > fake_now


def test_guard_threshold_positive_relative_anchored_to_creation():
    fake_now = 1_800_000_000.0
    creation = fake_now + 4 * 86400
    settings = {"locktime": "1y", "threshold": "30d"}
    threshold = resolve_guard_threshold(
        False, settings, now=fake_now, creation_date=creation
    )
    expected = _midnight(creation) + 30 * 86400
    assert abs(threshold - expected) < 1


def test_guard_threshold_missing_returns_none():
    fake_now = 1_800_000_000.0
    assert resolve_guard_threshold(False, {"locktime": "1y"}, now=fake_now) is None


# ------------------------------------------------------------------ #
# is_check_alive_date_valid / ensure_check_alive_valid
# ------------------------------------------------------------------ #


def test_is_valid_inside_window():
    now = 1_800_000_000.0
    assert is_check_alive_date_valid(now + 100, now + 1000, now=now) is True


def test_is_valid_rejects_past():
    now = 1_800_000_000.0
    assert is_check_alive_date_valid(now - 100, now + 1000, now=now) is False


def test_is_valid_rejects_on_or_after_delivery():
    now = 1_800_000_000.0
    delivery = now + 1000
    assert is_check_alive_date_valid(delivery, delivery, now=now) is False
    assert is_check_alive_date_valid(delivery + 1, delivery, now=now) is False


def test_is_valid_without_delivery_skips_upper_bound():
    now = 1_800_000_000.0
    assert is_check_alive_date_valid(now + 100, None, now=now) is True


def test_is_valid_none_is_false():
    assert is_check_alive_date_valid(None, 1000) is False


def test_basic_mode_never_expired():
    assert ensure_check_alive_valid(True, time.time() - 10_000) is None
    assert ensure_check_alive_valid(True, 1_000_000_000.0) is None


def test_advanced_raises_when_past():
    try:
        ensure_check_alive_valid(False, time.time() - 10_000)
    except CheckAliveError:
        pass
    else:
        raise AssertionError("expected CheckAliveError")


def test_advanced_raises_when_after_delivery():
    now = time.time()
    try:
        ensure_check_alive_valid(False, now + 100, delivery_date=now + 50)
    except CheckAliveAfterDeliveryError:
        pass
    else:
        raise AssertionError("expected CheckAliveAfterDeliveryError")


def test_advanced_valid_does_not_raise():
    now = time.time()
    assert ensure_check_alive_valid(False, now + 100, delivery_date=now + 1000) is None


# ------------------------------------------------------------------ #
# Will.get_creation_time
# ------------------------------------------------------------------ #


class _FakeWillItem:
    def __init__(self, time_value, valid=True):
        self.time = time_value
        self._valid = valid

    def get_status(self, status):
        return self._valid if status == "VALID" else False


def test_get_creation_time_min_of_valid_items():
    will = {
        "a": _FakeWillItem(2000),
        "b": _FakeWillItem(1000),
        "c": _FakeWillItem(3000),
    }
    assert Will.get_creation_time(will) == 1000


def test_get_creation_time_ignores_invalid_and_none():
    will = {
        "a": _FakeWillItem(500, valid=False),
        "b": _FakeWillItem(None),
        "c": _FakeWillItem(2000),
    }
    assert Will.get_creation_time(will) == 2000


def test_get_creation_time_empty_returns_none():
    assert Will.get_creation_time({}) is None


# ------------------------------------------------------------------ #
# Main
# ------------------------------------------------------------------ #

if __name__ == "__main__":
    for name in sorted(dir()):
        if name.startswith("test_"):
            globals()[name]()
            print(f"  [OK] {name}")
    print("[OK] All core checkalive tests passed")
