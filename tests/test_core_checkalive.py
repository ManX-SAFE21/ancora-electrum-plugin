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
    # "30d" resolves to a future timestamp (midnight-normalised).
    settings = {"threshold": "30d"}
    result = resolve_date_to_check(False, settings)
    assert result > time.time()


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
