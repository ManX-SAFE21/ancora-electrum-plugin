"""
Tests for ``bal.core.input_rules`` (pure, GUI-free).

Covers the locktime acceptance bounds, the RAW locktime sanitisation, and the
percentage-or-amount field normalisation that the Qt editors wrap.

Run:
    source "$BAL_HOME/electrum/env/bin/activate"
    python3 tests/test_core_input_rules.py
"""

import sys

sys.path.insert(0, __file__.rsplit("/", 2)[0])

from bal.core.input_rules import (  # noqa: E402  (path insert above)
    LockTimeEditor,
    normalize_locktime_raw_text,
    normalize_perc_amount_text,
    parse_perc_amount,
    replace_dy_suffixes,
)

# ------------------------------------------------------------------ #
# LockTimeEditor
# ------------------------------------------------------------------ #


def test_locktime_editor_is_acceptable():
    assert LockTimeEditor.is_acceptable_locktime(100) is True
    assert LockTimeEditor.is_acceptable_locktime(0) is True
    assert LockTimeEditor.is_acceptable_locktime(-1) is False
    assert LockTimeEditor.is_acceptable_locktime(None) is True


def test_locktime_editor_is_acceptable_string():
    assert LockTimeEditor.is_acceptable_locktime("100") is True
    assert LockTimeEditor.is_acceptable_locktime("abc") is False
    assert LockTimeEditor.is_acceptable_locktime("") is True


def test_locktime_editor_min_max():
    assert LockTimeEditor.min_allowed_value >= 0
    assert LockTimeEditor.max_allowed_value > LockTimeEditor.min_allowed_value


def test_locktime_editor_get_max_allowed_timestamp():
    # Always a valid, fromtimestamp-able value (the Windows 2038 clamp).
    ts = LockTimeEditor.get_max_allowed_timestamp()
    import datetime

    datetime.datetime.fromtimestamp(ts)  # must not raise
    assert ts <= 2**32 - 1


def test_locktime_editor_subclass_bounds():
    class Tight(LockTimeEditor):
        min_allowed_value = 1000
        max_allowed_value = 2000

    assert Tight.is_acceptable_locktime(1500) is True
    assert Tight.is_acceptable_locktime(999) is False
    assert Tight.is_acceptable_locktime(2001) is False


# ------------------------------------------------------------------ #
# replace_dy_suffixes / RAW locktime sanitisation
# ------------------------------------------------------------------ #


def test_replace_dy_suffixes():
    # replace_str only strips the day ("d") and year ("y") suffixes. The
    # block-height suffix ("b") was removed (A1), so "b" is NOT stripped
    # anymore (locktimes are always UNIX timestamps now).
    assert replace_dy_suffixes("123d") == "123"
    assert replace_dy_suffixes("456y") == "456"
    # "b" is left untouched (no longer a recognised suffix)
    assert replace_dy_suffixes("789b") == "789b"
    # only d/y are stripped; a stray "b" remains
    assert replace_dy_suffixes("12d34y56b") == "123456b"


def test_normalize_locktime_raw_empty():
    s, isdays, isyears, pos = normalize_locktime_raw_text("", 0)
    assert s == ""
    assert isdays is False
    assert isyears is False


def test_normalize_locktime_raw_days():
    s, isdays, isyears, pos = normalize_locktime_raw_text("30d", 3)
    assert s == "30d"
    assert isdays is True
    assert isyears is False


def test_normalize_locktime_raw_years():
    s, isdays, isyears, pos = normalize_locktime_raw_text("2y", 2)
    assert s == "2y"
    assert isdays is False
    assert isyears is True


def test_normalize_locktime_raw_strips_bad_chars():
    s, isdays, isyears, pos = normalize_locktime_raw_text("12a34b56", 8)
    assert s == "123456"
    assert isdays is False
    assert isyears is False


def test_normalize_locktime_raw_single_suffix():
    # "1y30d" collapses to a single suffix; "d" wins (processed first), so the
    # "y" is dropped along with all letters.
    s, isdays, isyears, pos = normalize_locktime_raw_text("1y30d", 5)
    assert isdays is True
    assert isyears is False
    assert s == "130d"


# ------------------------------------------------------------------ #
# Percentage-or-amount normalisation
# ------------------------------------------------------------------ #


def test_perc_normalize_percent():
    s, is_perc = normalize_perc_amount_text("50%", ".")
    assert is_perc is True
    assert s == "50%"


def test_perc_normalize_no_percent():
    s, is_perc = normalize_perc_amount_text("123", ".")
    assert is_perc is False
    assert s == "123"


def test_perc_normalize_strips_invalid():
    s, is_perc = normalize_perc_amount_text("1a2b3", ".")
    assert s == "123"


def test_perc_normalize_decimal_limit():
    # At most 8 decimals after the point.
    s, is_perc = normalize_perc_amount_text("1.1234567890123", ".")
    assert s == "1.12345678"


def test_perc_parse():
    from decimal import Decimal

    assert parse_perc_amount("50%", ".") == Decimal(50)
    assert parse_perc_amount("123.45", ".") == Decimal("123.45")
    assert parse_perc_amount("abc", ".") is None


# ------------------------------------------------------------------ #
# Main
# ------------------------------------------------------------------ #

if __name__ == "__main__":
    for name in sorted(dir()):
        if name.startswith("test_"):
            globals()[name]()
            print(f"  [OK] {name}")
    print("[OK] All core input_rules tests passed")
