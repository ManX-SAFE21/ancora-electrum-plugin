"""
bal.core.checkalive
===================

The "Check Alive" policy: the single reference timestamp (``date_to_check``)
against which every will-validity check is evaluated, and the BASIC/ADVANCED
mode rules that decide it.

Pure, GUI-free. The GUI raises :class:`CheckAliveError` to trigger the
postpone/invalidate flow; the decision that it *should* be raised lives here.
"""

from datetime import datetime, timezone
from typing import Any

from .plugin_base import BalTimestamp


class CheckAliveError(Exception):
    """Raised when the "check alive" date is in the past."""

    def __init__(self, timestamp_to_check):
        self.timestamp_to_check = timestamp_to_check

    def __str__(self):
        return "Check alive expired please update it: {}".format(
            datetime.fromtimestamp(self.timestamp_to_check, tz=timezone.utc).isoformat()
        )


def resolve_date_to_check(
    is_basic_mode: bool,
    will_settings: Any,
    now: float | None = None,
    built_locktime: float | int | None = None,
) -> float:
    """Return the reference timestamp for every will-validity check.

    ``date_to_check`` is the single reference timestamp that EVERY downstream
    check reads: the build filter, the heir count, ``check_will_expired``,
    ``check_amounts`` and the locktime-vs-threshold guard.

    * BASIC mode: the Check Alive is hidden and NOT editable, so it must never
      govern those checks. ``date_to_check`` is set to *now*: every check is
      evaluated against the current moment (the Check Alive effectively does not
      exist) while the delivery locktime is still fully enforced.
    * ADVANCED mode: the user-controlled stored threshold is used as-is. An
      ABSOLUTE threshold is returned unchanged; a RELATIVE one (``"30d"``/``"1y"``)
      means "N days BEFORE the delivery date" and is resolved against the stored
      locktime (matching the date the settings widget displays), so it stays in
      lockstep with the built transactions instead of drifting with the clock.

    A RELATIVE stored locktime is resolved against the frozen delivery date of
    the built will (``built_locktime``, the locktime inside the signed tx) when
    one exists: the will's real delivery date is authoritative, and resolving
    the relative locktime from *now* would drift ``date_to_check`` past the
    frozen tx locktime so an unchanged will wrongly reads as expired (asking to
    invalidate) every day.  Without a built will the legacy forward-from-now
    resolution is kept.

    Args:
        is_basic_mode: ``True`` for the SIMPLE / BASIC user type.
        will_settings: the per-wallet settings dict (``"threshold"`` and
            ``"locktime"`` keys).
        now: overridable clock for tests; defaults to ``datetime.now()``.
        built_locktime: the absolute locktime frozen inside the built will's
            transactions (``None`` when there is no built will yet).

    Returns:
        The reference timestamp (float, UNIX seconds).
    """
    if is_basic_mode:
        return (now if now is not None else datetime.now(tz=timezone.utc).timestamp())

    threshold = BalTimestamp(will_settings["threshold"])
    # A RELATIVE threshold ("30d"/"1y") means "N days BEFORE the delivery":
    # the settings widget resolves it as real_threshold = locktime - N days.
    # Resolving it FORWARD from now (BalTimestamp.to_timestamp) turns
    # date_to_check into a moving target that disagrees with the fixed
    # locktime of the built transactions (and with the date shown in the UI),
    # which can wrongly mark the will as expired/postponed.  Resolve it
    # against the delivery date instead.
    if threshold.unit is not None:
        locktime_raw = will_settings.get("locktime")
        if locktime_raw is None:
            # No delivery reference to anchor to: fall back to the legacy
            # forward-from-now resolution.
            return threshold.to_timestamp()
        locktime_dt = BalTimestamp(locktime_raw).to_date(now)
        # A RELATIVE stored locktime ("2y") is itself a moving target; when a
        # will has already been built, its frozen delivery date (the tx
        # locktime) is the authoritative anchor (see docstring).
        if BalTimestamp(locktime_raw).unit is not None and built_locktime:
            locktime_dt = BalTimestamp(int(built_locktime)).to_date(now)
        return threshold.to_date(locktime_dt, reverse=True).timestamp()
    return threshold.to_timestamp()


def resolve_guard_threshold(
    is_basic_mode: bool,
    will_settings: Any,
    now: float | None = None,
) -> float | None:
    """Resolve the "locktime is lower than threshold" guard's reference.

    The guard compares the stored settings on ONE reference frame: the
    delivery (``locktime``, kept as at the call site) against this threshold.

    Unlike :func:`resolve_date_to_check` -- which may be *anchored* to the
    built will's frozen tx locktime so that an unchanged will never reads as
    expired -- this helper resolves the threshold from the **stored settings
    alone**.  Otherwise, when the stored relative locktime is shorter than the
    frozen locktime of an old (still valid) built will (e.g. the delivery was
    shortened from ``"2y"`` to ``"1y"``), the guard would compare the fresh
    "1y" locktime against the old will's anchored threshold and wrongly fire,
    even though locktime > threshold by the settings themselves.

    * BASIC mode: no threshold exists.  Returns ``None`` and the caller falls
      back to comparing the locktime against ``date_to_check`` (= now), so its
      behaviour is unchanged.
    * ADVANCED mode with an ABSOLUTE threshold: returns the stored threshold
      as-is.
    * ADVANCED mode with a RELATIVE threshold (``"30d"``/``"1y"``, meaning
      "N days BEFORE the delivery"): the threshold is anchored to the locktime
      resolved forward from *now* (the settings' own delivery reading, never a
      built tx), keeping both sides of the comparison in the same reference
      frame, as the settings widget displays it.

    Returns ``None`` when there is no threshold to enforce (BASIC mode or a
    missing stored value).
    """
    if is_basic_mode:
        return None
    threshold_raw = will_settings.get("threshold")
    if threshold_raw is None:
        return None
    threshold = BalTimestamp(threshold_raw)
    if threshold.unit is None:
        return threshold.to_timestamp()
    now_dt = (
        datetime.fromtimestamp(now, tz=timezone.utc) if now is not None else None
    )
    locktime_dt = BalTimestamp(will_settings["locktime"]).to_date(now_dt)
    return threshold.to_date(locktime_dt, reverse=True).timestamp()


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
    current = now if now is not None else datetime.now(tz=timezone.utc).timestamp()
    return date_to_check < current
