"""
bal.core.checkalive
===================

The "Check Alive" policy: the single reference timestamp (``date_to_check``)
against which every will-validity check is evaluated, and the BASIC/ADVANCED
mode rules that decide it.

A RELATIVE Check Alive value is anchored according to its SIGN:

* NEGATIVE (``"-30d"`` / ``"-1y"``): counted BACKWARDS from the delivery date,
  i.e. ``delivery + N days`` (``-30d`` = 30 days BEFORE the delivery). This is
  the historical meaning of a relative threshold.
* POSITIVE (``"30d"`` / ``"1y"``): counted FORWARD from the inheritance
  creation date (``min(WillItem.time)`` of the valid will items), i.e.
  ``creation + N days``. The creation date is frozen in the wallet DB, so the
  reference never drifts with the clock.
* When the anchor is unknown (no built will yet, or imported items without a
  ``time``). The reference falls back to *now*.

Pure, GUI-free. The GUI raises :class:`CheckAliveError` /
:class:`CheckAliveAfterDeliveryError` to trigger the postpone/invalidate flow;
the decision that they *should* be raised lives here.
"""

from datetime import datetime, timezone
from typing import Any

from .plugin_base import BalTimestamp


class CheckAliveError(Exception):
    """Raised when the "check alive" date is in the past."""

    def __init__(self, timestamp_to_check, reason: str | None = None):
        self.timestamp_to_check = timestamp_to_check
        self.reason = reason

    def __str__(self):
        message = "Check alive expired please update it: {}".format(
            datetime.fromtimestamp(self.timestamp_to_check, tz=timezone.utc).isoformat()
        )
        if self.reason:
            message += f" ({self.reason})"
        return message


class CheckAliveAfterDeliveryError(Exception):
    """Raised when the "check alive" date is not before the delivery date.

    Deliberately NOT a subclass of :class:`CheckAliveError`: the existing
    ``except CheckAliveError`` handlers invalidate the inheritance on-chain,
    while a Check Alive that lands on/after the delivery is a configuration
    error that must only abort the current action.
    """

    def __init__(self, timestamp_to_check, delivery_timestamp=None):
        self.timestamp_to_check = timestamp_to_check
        self.delivery_timestamp = delivery_timestamp

    def __str__(self):
        check_date = datetime.fromtimestamp(
            self.timestamp_to_check, tz=timezone.utc
        ).isoformat()
        if self.delivery_timestamp is None:
            return (
                "Check alive date must be after today and before the delivery "
                f"date: {check_date}"
            )
        delivery_date = datetime.fromtimestamp(
            self.delivery_timestamp, tz=timezone.utc
        ).isoformat()
        return (
            "Check alive date must be after today and before the delivery "
            f"date: {check_date} >= {delivery_date}"
        )


def resolve_delivery_date(
    will_settings: Any,
    now: float | None = None,
    built_locktime: float | int | None = None,
) -> float | None:
    """Return the delivery date every Check Alive calculation is anchored to.

    This is the single source of the delivery reference, shared by
    :func:`resolve_date_to_check` and :func:`resolve_guard_threshold`:

    * an ABSOLUTE stored locktime is used as-is;
    * a RELATIVE stored locktime (``"2y"``) is resolved forward from *now*,
      UNLESS a will has already been built: its frozen delivery date
      (``built_locktime``, the locktime inside the signed tx) is then
      authoritative, so an unchanged will never drifts as the clock advances.

    Returns ``None`` when no locktime is stored.
    """
    locktime_raw = will_settings.get("locktime")
    if locktime_raw is None:
        return None
    if BalTimestamp(locktime_raw).unit is not None and built_locktime:
        return BalTimestamp(int(built_locktime)).to_timestamp(now)
    return BalTimestamp(locktime_raw).to_timestamp(now)


def resolve_relative_check_alive_date(
    threshold_raw: Any,
    delivery_date: float | None,
    creation_date: float | None = None,
    now: float | None = None,
) -> float:
    """Resolve a RELATIVE Check Alive value to an absolute timestamp.

    The SIGN of the value selects the anchor (the single calculation rule):

    * negative (``"-30d"``): ``delivery_date + N days`` (N < 0 -> BEFORE the
      delivery);
    * positive (``"30d"``): ``creation_date + N days`` (the inheritance
      creation date);
    * when the selected anchor is unknown, *now* is used as a fallback.

    Args:
        threshold_raw: the stored ``"<n>d"`` / ``"<n>y"`` value (may be
            negative).
        delivery_date: the resolved delivery timestamp (see
            :func:`resolve_delivery_date`), or ``None``.
        creation_date: the inheritance creation timestamp
            (``Will.get_creation_time``), or ``None``.
        now: overridable clock for tests.

    Returns:
        The absolute reference timestamp (float, UNIX seconds).
    """
    threshold = BalTimestamp(threshold_raw)
    if threshold.unit is None:
        return threshold.to_timestamp()
    if threshold.value < 0:
        anchor = delivery_date
    else:
        anchor = creation_date
    if anchor is None:
        anchor = now if now is not None else datetime.now(tz=timezone.utc).timestamp()
    return threshold.to_date(anchor).timestamp()


def resolve_date_to_check(
    is_basic_mode: bool,
    will_settings: Any,
    now: float | None = None,
    built_locktime: float | int | None = None,
    creation_date: float | None = None,
) -> float:
    """Return the reference timestamp for every will-validity check.

    ``date_to_check`` is the single reference timestamp that EVERY downstream
    check reads: the build filter, the heir count, ``check_will_expired``,
    ``check_amounts`` and the locktime-vs-threshold guard.

    * BASIC mode: the Check Alive is hidden and NOT editable, so it must never
      govern those checks. ``date_to_check`` is set to *now*.
    * ADVANCED mode: an ABSOLUTE stored threshold is returned unchanged; a
      RELATIVE one is resolved by :func:`resolve_relative_check_alive_date`
      (negative -> anchored to the delivery, positive -> anchored to the
      inheritance creation date).

    Args:
        is_basic_mode: ``True`` for the SIMPLE / BASIC user type.
        will_settings: the per-wallet settings dict (``"threshold"`` and
            ``"locktime"`` keys).
        now: overridable clock for tests; defaults to ``datetime.now()``.
        built_locktime: the absolute locktime frozen inside the built will's
            transactions (``None`` when there is no built will yet).
        creation_date: the inheritance creation timestamp
            (``Will.get_creation_time``); ``None`` falls back to *now*.

    Returns:
        The reference timestamp (float, UNIX seconds).
    """
    if is_basic_mode:
        return (now if now is not None else datetime.now(tz=timezone.utc).timestamp())

    threshold = BalTimestamp(will_settings["threshold"])
    if threshold.unit is None:
        return threshold.to_timestamp()
    delivery_date = resolve_delivery_date(
        will_settings, now=now, built_locktime=built_locktime
    )
    return resolve_relative_check_alive_date(
        will_settings["threshold"],
        delivery_date,
        creation_date=creation_date,
        now=now,
    )


def resolve_guard_threshold(
    is_basic_mode: bool,
    will_settings: Any,
    now: float | None = None,
    creation_date: float | None = None,
) -> float | None:
    """Resolve the "locktime is lower than threshold" guard's reference.

    The guard compares the stored settings on ONE reference frame: the
    delivery (``locktime``, kept as at the call site) against this threshold.

    Unlike :func:`resolve_date_to_check` -- which may be *anchored* to the
    built will's frozen tx locktime so that an unchanged will never reads as
    expired -- this helper resolves the threshold from the **stored settings
    alone** (no ``built_locktime``).  Otherwise, when the stored relative
    locktime is shorter than the frozen locktime of an old (still valid) built
    will (e.g. the delivery was shortened from ``"2y"`` to ``"1y"``), the guard
    would compare the fresh "1y" locktime against the old will's anchored
    threshold and wrongly fire, even though locktime > threshold by the
    settings themselves.

    * BASIC mode: no threshold exists.  Returns ``None`` and the caller falls
      back to comparing the locktime against ``date_to_check`` (= now), so its
      behaviour is unchanged.
    * ADVANCED mode with an ABSOLUTE threshold: returns the stored threshold
      as-is.
    * ADVANCED mode with a RELATIVE threshold: resolved by
      :func:`resolve_relative_check_alive_date` (negative -> delivery anchor,
      positive -> inheritance creation date), using *now* for the delivery
      reading and never a built tx.

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
    delivery_date = resolve_delivery_date(will_settings, now=now, built_locktime=None)
    return resolve_relative_check_alive_date(
        threshold_raw,
        delivery_date,
        creation_date=creation_date,
        now=now,
    )


def is_check_alive_date_valid(
    date_to_check: float | None,
    delivery_date: float | None,
    now: float | None = None,
) -> bool:
    """True when the Check Alive date is inside ``(now, delivery_date)``.

    The single validity rule for the Check Alive reference: it must be AFTER
    today and BEFORE the delivery.  A ``None`` ``delivery_date`` skips the
    upper bound (no delivery is known yet).
    """
    if date_to_check is None:
        return False
    current = now if now is not None else datetime.now(tz=timezone.utc).timestamp()
    if date_to_check < current:
        return False
    if delivery_date is not None and date_to_check >= delivery_date:
        return False
    return True


def ensure_check_alive_valid(
    is_basic_mode: bool,
    date_to_check: float | None,
    delivery_date: float | None = None,
    now: float | None = None,
) -> None:
    """Raise when the Check Alive date is not valid.

    Only ADVANCED mode can be invalid: in BASIC mode the Check Alive is inert
    by construction, so a passed check-alive date must never force a postpone
    or rewrite of the will.

    Raises:
        CheckAliveError: the date is in the past.
        CheckAliveAfterDeliveryError: the date is on/after the delivery.
    """
    if is_basic_mode:
        return
    if date_to_check is None:
        return
    current = now if now is not None else datetime.now(tz=timezone.utc).timestamp()
    if date_to_check < current:
        raise CheckAliveError(date_to_check)
    if delivery_date is not None and date_to_check >= delivery_date:
        raise CheckAliveAfterDeliveryError(date_to_check, delivery_date)
