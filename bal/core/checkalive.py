"""
bal.core.checkalive
===================

The "Check Alive" policy: the single reference timestamp (``date_to_check``)
against which every will-validity check is evaluated, and the BASIC/ADVANCED
mode rules that decide it.

Since CHANGELOG #80 (owner decision D14) the Check Alive is saved only as
``"-Nd"``: N days BEFORE the delivery date. Older saved values (a fixed date,
a positive "after creation" value) are converted once by
:func:`normalize_check_alive`, keeping the same date. The resolvers below
still accept the old forms, so a value read before the conversion behaves as
it always did:

* NEGATIVE (``"-30d"`` / ``"-1y"``): counted BACKWARDS from the delivery date,
  i.e. ``delivery + N days`` (``-30d`` = 30 days BEFORE the delivery).
* POSITIVE (``"30d"`` / ``"1y"``, old form): counted FORWARD from the
  inheritance creation date (``min(WillItem.time)`` of the valid will items).
* When the anchor is unknown (no built will yet, or imported items without a
  ``time``). The reference falls back to *now*.

Pure, GUI-free. The GUI raises :class:`CheckAliveError` /
:class:`CheckAliveAfterDeliveryError`; the decision that they *should* be
raised lives here.  A passed Check Alive never invalidates on its own any
more: :func:`check_alive_renewal` tells the GUI/CLI whether to offer the
owner a renewal (CHANGELOG #79).
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


#: Owner decisions D14/#80: the Check Alive is "N days before the delivery".
CHECK_ALIVE_DEFAULT_DAYS = 180
CHECK_ALIVE_MAX_DAYS = 3650
_DAY = 86400


def check_alive_days(threshold_raw: Any) -> int | None:
    """Days before the delivery of a ``"-Nd"`` / ``"-Ny"`` value, else None.

    Returns ``None`` for every other form (a fixed date, a positive "after
    creation" value, garbage) and for values outside 1..3650 days.
    """
    s = str(threshold_raw).strip()
    if len(s) < 3 or s[0] != "-" or s[-1].lower() not in ("d", "y"):
        return None
    try:
        n = int(s[1:-1])
    except ValueError:
        return None
    days = n * 365 if s[-1].lower() == "y" else n
    if 1 <= days <= CHECK_ALIVE_MAX_DAYS:
        return days
    return None


def normalize_check_alive(
    will_settings: Any,
    built_locktime: float | int | None = None,
    creation_date: float | None = None,
    now: float | None = None,
) -> str:
    """Return the Check Alive as ``"-Nd"`` (N days before the delivery).

    Owner decision D14 (CHANGELOG #80): the Check Alive is only "N days
    before the delivery"; the fixed date and the positive "after creation"
    forms are gone because they do not follow the delivery when it moves
    (scenario C3adv). Old values keep the SAME Check Alive date (decision
    D2 = A), expressed as days before the delivery the will really uses
    (``resolve_delivery_date`` with ``built_locktime``):

    * ``"-30d"`` / ``"-1y"``: the same, in days (``"-30d"`` / ``"-365d"``);
    * a fixed date, or a positive value (anchored to ``creation_date``, or
      to *now* when unknown): ``delivery - date`` in days, rounded;
    * anything that cannot be expressed so (no delivery, a date on/after
      the delivery, more than 3650 days, unreadable): the default
      ``"-180d"``.

    A date already in the past but before the delivery is KEPT: it is a
    Check Alive that has passed, and the owner is then asked to renew
    (#79). The ``"-Nd"`` form is also what BAL understands. Pure: the
    caller saves the result.
    """
    default = f"-{CHECK_ALIVE_DEFAULT_DAYS}d"
    raw = will_settings.get("threshold") if will_settings else None
    days = check_alive_days(raw)
    if days is not None:
        return f"-{days}d"
    try:
        threshold = BalTimestamp(raw)
        if threshold.unit is not None and threshold.value < 0:
            return default            # negative but out of range
        current = (
            now if now is not None else datetime.now(tz=timezone.utc).timestamp()
        )
        if threshold.unit is None:
            check = float(threshold.value)
        else:
            anchor = creation_date if creation_date else current
            check = threshold.to_timestamp(anchor)
        delivery = resolve_delivery_date(
            will_settings, now=current, built_locktime=built_locktime
        )
    except Exception:
        return default
    if delivery is None:
        return default
    days = round((delivery - check) / _DAY)
    if 1 <= days <= CHECK_ALIVE_MAX_DAYS:
        return f"-{days}d"
    return default


def will_executable_now(built_locktime: float | int | None, now: float | None = None) -> bool:
    """True when the will in force is already past its delivery date.

    Tells a will that can really be executed now (truly EXPIRED: invalidate
    it at once) from one that is only "expired" with respect to a Check
    Alive moved later together with the delivery, i.e. a POSTPONE beyond the
    Check Alive margin (CHANGELOG #80). Uses the same clock as the rest of
    ``bal.core``.
    """
    if not built_locktime:
        return False
    current = now if now is not None else datetime.now(tz=timezone.utc).timestamp()
    return built_locktime <= current


def check_alive_renewal(
    is_basic_mode: bool,
    will_settings: Any,
    built_locktime: float | int | None = None,
    creation_date: float | None = None,
    now: float | None = None,
) -> dict | None:
    """Describe a PASSED Check Alive and whether the will can be renewed.

    Owner decision (CHANGELOG #79): a passed Check Alive means the owner is
    still here, so the plugin offers to RENEW the will (invalidate the old
    one, then prepare a new one at once) instead of invalidating on its own
    and leaving no will in force.  This helper only decides; it never builds,
    signs or invalidates anything.

    Renewing works because of an existing rule (see
    :func:`resolve_delivery_date`): with no will in force a RELATIVE delivery
    (``"4y"``) is counted from *now*.  So once the old will is invalidated,
    the new one is delivered ``now + 4y``.  A FIXED delivery date cannot be
    renewed this way: the owner must choose a later date.

    Args:
        is_basic_mode: ``True`` in BASIC mode (no Check Alive: returns None).
        will_settings: the per-wallet settings (``"locktime"``,
            ``"threshold"``).
        built_locktime: the earliest locktime of the VALID will items
            (``Will.get_min_locktime``), ``None`` when no will is in force.
        creation_date: ``Will.get_creation_time`` of the will in force.
        now: overridable clock for tests.

    Returns:
        ``None`` when the Check Alive has not passed (or BASIC mode), else a
        dict with ``check_date`` (the passed Check Alive), ``has_will``,
        ``renewable``, ``reason`` (``None``, ``"expired"``: the will in force
        is already past its delivery date, ``"fixed_date"`` or
        ``"still_past"``: even a renewed will would have its Check Alive in
        the past), ``delivery`` (= ``built_locktime``), and for a relative
        delivery ``new_delivery``,
        ``new_check``, ``period`` (``(value, unit)``, e.g. ``(4, "y")``).
    """
    if is_basic_mode:
        return None
    current = now if now is not None else datetime.now(tz=timezone.utc).timestamp()
    try:
        check_date = resolve_date_to_check(
            False, will_settings, now=current,
            built_locktime=built_locktime, creation_date=creation_date,
        )
    except Exception:
        return None
    if check_date >= current:
        return None
    info = {
        "check_date": check_date,
        "has_will": bool(built_locktime),
        "renewable": False,
        "reason": None,
        "new_delivery": None,
        "new_check": None,
        "period": None,
        "delivery": built_locktime,
    }
    if will_executable_now(built_locktime, now=current):
        # The will in force is already past its delivery date: it can be
        # executed now. That is the EXPIRED case (invalidate at once), not a
        # Check Alive one; the callers route it there (scenario C3adv).
        info["reason"] = "expired"
        return info
    locktime = BalTimestamp(will_settings.get("locktime"))
    if locktime.unit is None:
        info["reason"] = "fixed_date"
        return info
    # The renewed will: no will in force any more, delivery counted from now,
    # and a positive ("after creation") Check Alive anchored to the new will.
    new_delivery = locktime.to_timestamp(current)
    new_check = resolve_date_to_check(
        False, will_settings, now=current, built_locktime=None,
        creation_date=current,
    )
    info["new_delivery"] = new_delivery
    info["new_check"] = new_check
    info["period"] = (locktime.value, locktime.unit)
    if not is_check_alive_date_valid(new_check, new_delivery, now=current):
        info["reason"] = "still_past"
        return info
    info["renewable"] = True
    return info


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
