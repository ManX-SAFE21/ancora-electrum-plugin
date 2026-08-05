"""
bal.core.checkalive
===================

The "Check Alive" policy: the single reference timestamp (``date_to_check``)
against which every will-validity check is evaluated, and the BASIC/ADVANCED
mode rules that decide it.

Pure, GUI-free. The GUI raises :class:`CheckAliveError` to trigger the
postpone/invalidate flow; the decision that it *should* be raised lives here.
"""

from datetime import datetime
from typing import Any

from .plugin_base import BalTimestamp


class CheckAliveError(Exception):
    """Raised when the "check alive" date is in the past."""

    def __init__(self, timestamp_to_check):
        self.timestamp_to_check = timestamp_to_check

    def __str__(self):
        return "Check alive expired please update it: {}".format(
            datetime.fromtimestamp(self.timestamp_to_check).isoformat()
        )


def resolve_date_to_check(
    is_basic_mode: bool, will_settings: Any, now: float | None = None
) -> float:
    """Return the reference timestamp for every will-validity check.

    ``date_to_check`` is the single reference timestamp that EVERY downstream
    check reads: the build filter, the heir count, ``check_will_expired``,
    ``check_amounts`` and the locktime-vs-threshold guard.

    * BASIC mode: the Check Alive is hidden and NOT editable, so it must never
      govern those checks. ``date_to_check`` is set to *now*: every check is
      evaluated against the current moment (the Check Alive effectively does not
      exist) while the delivery locktime is still fully enforced.
    * ADVANCED mode: the user-controlled stored threshold is used as-is.

    Args:
        is_basic_mode: ``True`` for the SIMPLE / BASIC user type.
        will_settings: the per-wallet settings dict (``"threshold"`` key).
        now: overridable clock for tests; defaults to ``datetime.now()``.

    Returns:
        The reference timestamp (float, UNIX seconds).
    """
    if is_basic_mode:
        return (now if now is not None else datetime.now().timestamp())
    return BalTimestamp(will_settings["threshold"]).to_timestamp()


def check_alive_expired(
    is_basic_mode: bool, date_to_check: float, now: float | None = None
) -> bool:
    """True when the Check Alive guard should fire (``CheckAliveError``).

    Only ADVANCED mode can be "expired": in BASIC mode the Check Alive is inert
    by construction, so a passed check-alive date must never force a postpone or
    rewrite of the will.
    """
    if is_basic_mode:
        return False
    current = now if now is not None else datetime.now().timestamp()
    return date_to_check < current
