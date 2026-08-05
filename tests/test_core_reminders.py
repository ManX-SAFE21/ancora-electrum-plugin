"""
Tests for ``bal.core.reminders`` (pure, GUI-free).

Covers the reminder-offset rules (BASIC + ADVANCED), the RFC-5545 helpers
(format_time, ical_escape, fold_ical_line), write_temp_ics, and the unified
``build_ics_reminders`` builder.

This module imports only ``bal.core``, so it runs in the lint venv (no
Electrum/Qt needed) as well as the runtime venv:

Run:
    python3 tests/test_core_reminders.py
"""

import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, __file__.rsplit("/", 2)[0])

from bal.core.reminders import (  # noqa: E402  (path insert above)
    BASIC_REMINDER_OFFSETS,
    basic_reminder_offsets,
    build_ics_reminders,
    compute_reminder_offsets,
    fold_ical_line,
    format_time,
    ical_escape,
    write_temp_ics,
)

# ------------------------------------------------------------------ #
# compute_reminder_offsets - distribution rules
# ------------------------------------------------------------------ #


def test_offsets_long_period_three_reminders():
    """30-day period, 3 reminders: spread out, all before the deadline."""
    offsets = compute_reminder_offsets(30, 3)
    assert len(offsets) == 3
    # All strictly before the deadline (offset >= 1 means "n days before end").
    assert all(o >= 1 for o in offsets)
    # Sorted earliest-first (largest offset first).
    assert offsets == sorted(offsets, reverse=True)
    # One reminder near the start, one near the end.
    assert max(offsets) == 30
    assert min(offsets) == 1


def test_offsets_capped_at_one_per_day():
    """Short period: at most one reminder per available day."""
    # 2 days but 3 requested -> only 2 reminders, one per day.
    assert compute_reminder_offsets(2, 3) == [2, 1]
    # 1 day but 3 requested -> a single reminder, the day before the deadline.
    assert compute_reminder_offsets(1, 3) == [1]


def test_offsets_empty_when_no_room():
    """No reminders when there is no day before the deadline."""
    assert compute_reminder_offsets(0, 3) == []
    assert compute_reminder_offsets(-5, 3) == []
    # A non-positive count also yields nothing.
    assert compute_reminder_offsets(30, 0) == []


def test_offsets_never_exceed_requested_count():
    """The number of reminders never exceeds the requested count (max 5)."""
    offsets = compute_reminder_offsets(100, 5)
    assert len(offsets) == 5
    assert all(o >= 1 for o in offsets)
    # Distinct offsets only (no duplicate alarms on the same day).
    assert len(set(offsets)) == len(offsets)


def test_offsets_single_reminder_is_day_before_deadline():
    """A single requested reminder fires one day before the deadline."""
    assert compute_reminder_offsets(30, 1) == [1]


# ------------------------------------------------------------------ #
# basic_reminder_offsets - fixed BASIC-mode offsets
# ------------------------------------------------------------------ #


def test_basic_offsets_full_period():
    """A far-away delivery keeps all three fixed offsets (30, 10, 1)."""
    assert basic_reminder_offsets(365) == [30, 10, 1]


def test_basic_offsets_truncated_by_horizon():
    """Offsets beyond the delivery horizon are dropped."""
    assert basic_reminder_offsets(20) == [10, 1]
    assert basic_reminder_offsets(5) == [1]


def test_basic_offsets_empty():
    """No future offsets when the delivery is less than a day away."""
    assert basic_reminder_offsets(0) == []
    assert basic_reminder_offsets(-10) == []


def test_basic_offsets_always_from_fixed_set():
    """Every returned offset is one of the fixed BASIC offsets."""
    for horizon in range(0, 40):
        result = basic_reminder_offsets(horizon)
        assert set(result).issubset(set(BASIC_REMINDER_OFFSETS))


# ------------------------------------------------------------------ #
# format_time
# ------------------------------------------------------------------ #


def test_format_time_utc():
    dt = datetime(2025, 6, 1, 12, 30, 45, tzinfo=timezone.utc)
    assert format_time(dt) == "20250601T123045Z"


def test_format_time_non_utc():
    tz = timezone(timedelta(hours=2))
    dt = datetime(2025, 6, 1, 12, 30, 45, tzinfo=tz)
    assert format_time(dt) == "20250601T103045Z"


# ------------------------------------------------------------------ #
# ical_escape
# ------------------------------------------------------------------ #


def test_ical_escape_no_change():
    assert ical_escape("hello world") == "hello world"


def test_ical_escape_backslash():
    assert ical_escape("a\\b") == "a\\\\b"


def test_ical_escape_semicolon():
    assert ical_escape("a;b") == "a\\;b"


def test_ical_escape_comma():
    assert ical_escape("a,b") == "a\\,b"


def test_ical_escape_multiline():
    text = "line1\nline2"
    result = ical_escape(text)
    assert "line1" in result
    assert "line2" in result


def test_ical_escape_all():
    assert ical_escape("\\;,") == "\\\\\\;\\,"


# ------------------------------------------------------------------ #
# fold_ical_line
# ------------------------------------------------------------------ #


def test_fold_ical_line_short():
    assert fold_ical_line("SUMMARY:Test") == "SUMMARY:Test"


def test_fold_ical_line_long():
    line = "SUMMARY:" + "a" * 100
    result = fold_ical_line(line, limit=75)
    parts = result.split("\r\n ")
    assert all(len(p.encode("utf-8")) <= 75 for p in parts)
    assert "".join(parts) == line


def test_fold_ical_line_unicode():
    line = "SUMMARY:" + "é" * 50
    result = fold_ical_line(line, limit=75)
    parts = result.split("\r\n ")
    assert all(len(p.encode("utf-8")) <= 75 for p in parts)
    assert "".join(parts) == line


# ------------------------------------------------------------------ #
# write_temp_ics
# ------------------------------------------------------------------ #


def test_write_temp_ics():
    content = "BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n"
    path = write_temp_ics(content)
    try:
        assert os.path.isfile(path)
        with open(path, "rb") as f:
            assert f.read() == content.encode("utf-8")
    finally:
        os.unlink(path)


def test_write_temp_ics_empty():
    path = write_temp_ics("")
    try:
        assert os.path.isfile(path)
        with open(path, "rb") as f:
            assert f.read() == b""
    finally:
        os.unlink(path)


# ------------------------------------------------------------------ #
# build_ics_reminders - unified builder
# ------------------------------------------------------------------ #

_LOCKTIME = datetime(2026, 7, 23, 9, 0, 0, tzinfo=timezone.utc)


def _common_kwargs():
    return dict(
        locktime=_LOCKTIME,
        description="BAL will of $wallet_name\r\n$heirs_complete",
        summary="BAL - Will execution of $wallet_name",
        wallet_name="karen7",
        heirs_details=" alice - bc1qalice, bob - bc1qbob",
        version="0.6.1",
    )


def test_build_ics_advanced_separate_events():
    """ADVANCED: 30-day period -> offsets [30, 16, 1], one VEVENT each."""
    content = build_ics_reminders(
        basic_mode=False,
        num_reminders=3,
        now=datetime(2026, 7, 1, 0, 0, 0, tzinfo=timezone.utc),
        threshold=datetime(2026, 6, 23, 0, 0, 0, tzinfo=timezone.utc),
        **_common_kwargs(),
    )
    assert content is not None
    assert content.startswith("BEGIN:VCALENDAR")
    assert content.endswith("\r\n")
    lines = content.split("\r\n")
    assert lines.count("BEGIN:VEVENT") == 3
    assert lines.count("END:VEVENT") == 3
    assert "BEGIN:VALARM" not in lines
    # Unique UIDs, numbered summaries, last event one day before the deadline.
    uids = [ln for ln in lines if ln.startswith("UID:")]
    assert len(uids) == len(set(uids)) == 3
    assert any("(reminder 1/3)" in ln for ln in lines)
    assert any("(reminder 3/3)" in ln for ln in lines)
    last_dt = format_time(_LOCKTIME - timedelta(days=1))
    assert f"DTSTART:{last_dt}" in lines
    # Template substitution; the CRLF inside the description stays as a line
    # break in the folded DESCRIPTION (no literal "\n" escaping in ical_escape).
    desc = [ln for ln in lines if ln.startswith("DESCRIPTION:")]
    assert desc and "karen7" in desc[0]
    assert any(ln.lstrip().startswith("alice") for ln in lines)

def test_build_ics_basic_uses_fixed_offsets():
    """BASIC: fixed offsets (30, 10, 1) filtered to the future, threshold not
    required."""
    content = build_ics_reminders(
        basic_mode=True,
        now=datetime(2026, 6, 1, 0, 0, 0, tzinfo=timezone.utc),
        **_common_kwargs(),
    )
    assert content is not None
    lines = content.split("\r\n")
    uids = [ln for ln in lines if ln.startswith("UID:")]
    assert len(uids) == 3
    # The first event is the 30-day offset.
    assert "bal-karen7-30d" in uids[0]


def test_build_ics_returns_none_when_no_reminders():
    """No future reminder -> None (the aligned behavior), not an empty file."""
    content = build_ics_reminders(
        basic_mode=True,
        now=datetime(2026, 7, 23, 0, 0, 0, tzinfo=timezone.utc),
        **_common_kwargs(),
    )
    assert content is None


def test_build_ics_advanced_requires_threshold():
    """ADVANCED mode without a threshold is a programming error."""
    try:
        build_ics_reminders(basic_mode=False, **_common_kwargs())
    except ValueError:
        return
    raise AssertionError("expected ValueError for missing threshold in ADVANCED")


# ------------------------------------------------------------------ #
# Main
# ------------------------------------------------------------------ #

if __name__ == "__main__":
    for name in sorted(dir()):
        if name.startswith("test_"):
            globals()[name]()
            print(f"  [OK] {name}")
    print("[OK] All core reminders tests passed")
