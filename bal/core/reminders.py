"""
bal.core.reminders
==================

Pure, GUI-free logic for the dead-man's-switch calendar reminders: choosing the
reminder offsets (BASIC vs ADVANCED modes) and rendering them as an RFC-5545
iCalendar (.ics) document.

Everything in this module is stdlib-only, so it can be imported and tested
without Electrum or Qt (e.g. in the lint venv).
"""

import os
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Optional


def compute_reminder_offsets(days, count):
    """Return the reminder offsets (in days BEFORE the deadline) for an .ics event.

    Group D / D1. The reminders are spread uniformly across the check-alive
    period and always fall *before* the delivery deadline, i.e. every returned
    offset is ``>= 1`` (a reminder exactly on the deadline would be useless).

    Rules:
        * ``count`` is the requested number of reminders (the settings dialog
          caps it at 5, default 3).
        * at most ONE reminder per available day: the effective number is
          ``min(count, days)``;
        * with ``days`` available days, offsets are chosen as evenly spaced
          points inside ``[1, days]`` (1 = the day before the deadline, ``days``
          = the first day of the period), de-duplicated and returned sorted
          descending (earliest reminder first).

    Args:
        days: number of whole days between check-alive and the deadline.
        count: requested number of reminders.

    Returns:
        A list of integer day-offsets (each ``>= 1``), e.g. ``[22, 15, 8]`` for
        ``days=30, count=3``. Empty if there is no room for any reminder.
    """
    # No room for any reminder (deadline today or already passed).
    if days < 1 or count < 1:
        return []

    # Never more reminders than available days (one per day at most).
    effective = min(int(count), int(days))

    # A single reminder: put it one day before the deadline.
    if effective == 1:
        return [1]

    # Spread "effective" points evenly inside [1, days]. Using i/(effective-1)
    # for i in 0..effective-1 gives fractions 0..1; map them onto [1, days].
    # This places the first reminder at the start of the period (offset ~days)
    # and the last one one day before the deadline (offset 1).
    offsets = set()
    for i in range(effective):
        frac = i / (effective - 1)  # 0.0 .. 1.0
        # offset = days at frac 0 (start), 1 at frac 1 (just before deadline).
        offset = round(days - frac * (days - 1))
        offset = max(1, min(days, offset))
        offsets.add(offset)

    # Sorted descending: earliest reminder (largest offset) first.
    return sorted(offsets, reverse=True)


# Fixed reminder offsets (in days BEFORE the delivery date) used in BASIC mode.
# In BASIC the check-alive parameter is hidden/unmanaged, so reminders cannot be
# spread over it; instead the owner asked for three fixed reminders: 30, 10 and
# 1 day before the inheritance delivery date.
BASIC_REMINDER_OFFSETS = (30, 10, 1)


def basic_reminder_offsets(days_to_deadline):
    """Return the BASIC-mode reminder offsets that still fall in the future.

    BASIC mode uses the fixed offsets in ``BASIC_REMINDER_OFFSETS`` (30, 10 and
    1 day before the delivery date). Any offset that would land in the past is
    dropped, because a reminder before "today" is useless: if the delivery date
    is only ``days_to_deadline`` days away, only the offsets that are ``<=
    days_to_deadline`` are kept.

    Args:
        days_to_deadline: whole days from now until the delivery date.

    Returns:
        A list of integer day-offsets (each ``>= 1``), sorted as in
        ``BASIC_REMINDER_OFFSETS`` (descending: earliest reminder first). Empty
        when the delivery date is less than one day away.
    """
    horizon = max(int(days_to_deadline), 0)
    return [off for off in BASIC_REMINDER_OFFSETS if 1 <= off <= horizon]


def format_time(time) -> str:
    """Render a datetime as an RFC-5545 UTC timestamp (``YYYYMMDDTHHMMSSZ``)."""
    return time.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def fold_ical_line(line: str, limit: int = 75) -> str:
    """Fold a line to at most ``limit`` bytes per RFC-5545, without splitting
    multi-byte UTF-8 characters. Continuation lines start with a space."""
    encoded = line.encode("utf-8")
    parts = []
    while len(encoded) > limit:
        # cut without splitting a UTF-8 continuation byte
        cut = limit
        while (encoded[cut] & 0xC0) == 0x80:  # byte de continuazione UTF-8
            cut -= 1
        parts.append(encoded[:cut].decode("utf-8"))
        encoded = encoded[cut:]
    parts.append(encoded.decode("utf-8"))
    return "\r\n ".join(parts)


def ical_escape(text: str) -> str:
    """Escape a string per RFC-5545: backslash, semicolon, comma, newlines."""
    text = (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
    )
    return "\r\n".join(fold_ical_line(line) for line in text.split("\r\n"))


def write_temp_ics(content: str) -> str:
    """Write ``content`` to a temporary ``.ics`` file and return its path."""
    fd, path = tempfile.mkstemp(prefix="event_", suffix=".ics")
    with os.fdopen(fd, "wb") as f:
        f.write(content.encode("utf-8"))
    return path


def build_ics_reminders(
    *,
    locktime: datetime,
    basic_mode: bool,
    description: str,
    summary: str,
    wallet_name: str,
    heirs_details: str,
    version: str,
    num_reminders: int = 3,
    now: Optional[datetime] = None,
    threshold: Optional[datetime] = None,
) -> Optional[str]:
    """Build the ``.ics`` content with one VEVENT per reminder date.

    Group D / D1 (revised): N *separate* VEVENTs, one per reminder date, so the
    user sees several distinct appointments in their calendar. The reminder
    offsets come from :func:`basic_reminder_offsets` (BASIC mode) or
    :func:`compute_reminder_offsets` (ADVANCED mode, spread over the check-alive
    period).

    Args:
        locktime: the delivery deadline (datetime).
        basic_mode: use the fixed BASIC offsets instead of spreading over the
            check-alive period.
        description: raw EVENT_DESCRIPTION template; ``$wallet_name`` and
            ``$heirs_complete`` placeholders are substituted and escaped.
        summary: raw EVENT_SUMMARY template; ``$wallet_name`` is substituted.
        wallet_name: label used in the UID and template substitutions.
        heirs_details: pre-formatted heir list injected into ``description``.
        version: plugin version, embedded in the PRODID line.
        num_reminders: requested reminder count (ADVANCED mode only).
        now: "today" reference; defaults to ``datetime.now()``.
        threshold: check-alive date (ADVANCED mode only; required there).

    Returns:
        The ``.ics`` content string, or ``None`` when no reminder falls in the
        future (the delivery date is too close or already passed) so the caller
        can show a warning instead of producing an empty-looking file.
    """
    now = now if now is not None else datetime.now()

    if basic_mode:
        days_to_deadline = (locktime - now).days
        offsets = basic_reminder_offsets(days_to_deadline)
    else:
        if threshold is None:
            raise ValueError("threshold is required in ADVANCED mode")
        days = (locktime - threshold).days
        offsets = compute_reminder_offsets(days, num_reminders)

    # ToDo #2: no future reminder means there are no events to write. Return
    # None so the caller shows a clear warning instead of an empty .ics file.
    if not offsets:
        return None

    event_description = ical_escape(
        f"{description}"
        .replace("$wallet_name", str(wallet_name))
        .replace("$heirs_complete", heirs_details)
    )
    summary_base = f"{summary}".replace("$wallet_name", str(wallet_name))

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:-//Bitcoin After Life//Electrum Plugin/{version}",
    ]

    # One separate VEVENT per reminder offset (its own date in the calendar).
    total = len(offsets)
    for idx, offset in enumerate(offsets, start=1):
        # The visible date of this event: "offset" days before the deadline.
        event_dt = format_time(locktime - timedelta(days=offset))
        # Suffix the summary so the N events are easy to tell apart.
        event_summary = ical_escape(f"{summary_base} (reminder {idx}/{total})")
        lines.extend([
            "BEGIN:VEVENT",
            # Offset in the UID keeps each event unique (no merging).
            f"UID:bal-{str(wallet_name)}-{offset}d",
            f"DTSTAMP:{format_time(now)}",
            f"DTSTART:{event_dt}",
            f"DTEND:{event_dt}",
            f"SUMMARY:{event_summary}",
            f"DESCRIPTION:{event_description}",
            "END:VEVENT",
        ])

    lines.append("END:VCALENDAR")
    lines = [s.rstrip("\r\n") for s in lines]
    return "\r\n".join(lines) + "\r\n"
