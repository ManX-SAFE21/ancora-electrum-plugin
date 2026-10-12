"""The delivery settings of one wallet: the rules (CHANGELOG #123).

Since #123 every wallet keeps its own delivery date, fee rate and Check
Alive bridge in its own file, under the wallet key ``will_settings``
(:data:`WALLET_KEY`, registered by ``plugin_base._register_will_dict``).
Electrum's global key ``bal_will_settings`` belongs to BAL: Safe21 never
writes it again and reads it only once per wallet, as a migration source.

This module holds the rules only. The engine (``bal.engine.context``)
reads the wallet and the global, calls these functions and writes the
result. The rules here:

* month arithmetic (:func:`utc_midnight`, :func:`add_months`);
* what a stored value means (:func:`as_delivery`, :func:`is_fee`,
  :func:`is_current`, :func:`resolve_legacy_date`, :func:`validate_values`);
* the one-time migration (:func:`migration_values`, :func:`note_for`,
  :func:`convert_check_alive`);
* the anchor that notices a will changed by another program
  (:func:`record_will_anchor`, :func:`check_anchor`);
* the one-time note and the "which date?" question (:func:`set_note`,
  :func:`pending_note`, :func:`mark_note_shown`, :func:`choice_dates`,
  :func:`clear_choice`) and the two holds that stop every flow
  (:func:`check_holds`);
* the later date the old settings asked for, kept until the wallet shows
  whether the will found at the migration is still in force
  (:func:`later_wish_action`, :func:`drop_later_wish`);
* the date the command line accepts (:func:`parse_cli_date`);
* the delivery form of #127: a fixed date or a period of N calendar
  months renewed by the Check Alive (:func:`form_of`, :func:`period_months`,
  :func:`period_values`), the v2 upgrade (:func:`upgrade_values`), and the
  two predicates on the will items: the form is locked while a will is in
  force (:func:`form_locked`), the period date is re-anchored only when no
  will is in force (:func:`will_in_force`).

Pure: standard library only, no Qt, no Electrum objects, no I/O, and the
clock is NEVER read here. Every "now" comes from the caller, so the test
clocks of ``tests/flowkit.py`` and of the harness reach these rules through
their callers (``tests/test_core_delivery_settings.py`` checks it).

The will items reach these rules as :class:`ItemView` objects (see
:func:`item_view` and :func:`views_from`), never as ``WillItem`` objects.

Writing into the wallet dict (rule I3 of the #123 plan): the functions that
change a settings dict (:func:`write_values`, :func:`record_will_anchor`,
:func:`check_anchor`, the note helpers, :func:`drop_later_wish`) change it
item by item, with
``d[key] = value`` and ``del d[key]``. They never call ``d.update()`` or
``d.clear()`` and never replace the dict: on Electrum's ``StoredDict``
``dict.update`` skips the wallet-file patches, and replacing the dict
(``db.put``) leaves the references already handed out out of sync.
Unchanged values are not written again, so a second load writes nothing.
"""

import calendar
import hashlib
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #
DAY = 86400
#: Below this an nLockTime is a block height, not a date (Bitcoin rule; the
#: same boundary as ``bal.core.util.LOCKTIME_THRESHOLD``).
LOCKTIME_MIN = 500_000_000
#: The largest nLockTime a transaction can hold (2106-02-07 06:28:15 UTC).
LOCKTIME_MAX = 2**32 - 1

#: The wallet key of the settings. Phase 6 must carry it to the new identity:
#: the name lives only here and in ``plugin_base._register_will_dict``.
WALLET_KEY = "will_settings"
#: The schema written now: 2 since #127 (``form``, ``period_months``,
#: ``period_start``). A version-1 dict is still current for the rules of
#: the #123 migration (:func:`is_current`); :func:`upgrade_values` brings it
#: to version 2 (form "fixed") without migrating it again.
SCHEMA_VERSION = 2
#: The lowest ``settings_version`` that :func:`is_current` accepts (#123).
FIRST_SCHEMA_VERSION = 1
DEFAULT_FEE = 20
#: The delivery date of a wallet with nothing to migrate from, in calendar
#: months after today (owner decision 6 of #127, 2026-10-12: 24; it was 12).
DEFAULT_MONTHS = 24

# The delivery form (#127, owner decision 1): a fixed date, or a period of
# N calendar months that the Check Alive renews. Stored codes, never
# translated. A dict without ``form`` is a fixed date.
FORM_FIXED = "fixed"
FORM_PERIOD = "period"
FORMS = (FORM_FIXED, FORM_PERIOD)
#: The limits of the period, in calendar months (plan, decision 11).
PERIOD_MIN = 1
PERIOD_MAX = 240
#: The Check Alive bridge kept until #128: "180 days before the delivery",
#: the default of ``BalPlugin.default_will_settings_relative`` (#80).
DEFAULT_THRESHOLD = "-180d"
#: Legacy keys removed at migration: ``tx_fees`` is folded into
#: ``baltx_fees``; the two ``real_*`` keys were display caches.
LEGACY_DROP = ("tx_fees", "real_threshold", "real_locktime")

# ``migrated_from``: where the migrated date came from. Diagnostic codes,
# stored in the wallet and never translated.
SOURCE_WILL = "will"
SOURCE_SENT = "sent"
SOURCE_GLOBAL = "global"
SOURCE_LEGACY = "legacy_wallet"
SOURCE_DEFAULT = "default"

# ``note_kind``: the one-time note shown after the main window opens.
NOTE_CONVERTED = "converted"
NOTE_LATER_PENDING = "later_pending"
NOTE_DATE_DIFFERS = "date_differs"
NOTE_OUTSIDE_CHANGE = "outside_change"
NOTE_KEYS = ("note_kind", "note_old", "note_new")
#: Set to 1 while the "which date?" question waits for an answer.
CHOICE_KEY = "choice_pending"
#: The later delivery date the old settings asked for (an int), written by
#: the migration next to the notes "later_pending" (and "date_differs" for
#: a sent will). Not part of the note: showing the note never deletes it.
#: It waits until the wallet shows whether the will found at the migration
#: is still in force (:func:`later_wish_action`).
LATER_WISH_KEY = "later_wish"

# What :func:`check_anchor` found (for the log line of the caller).
ANCHOR_FIRST = "first"          # no anchor yet: recorded now
ANCHOR_SAME = "same"            # the will in force is the one Safe21 saved
ANCHOR_MOVED = "moved"          # another VALID set, same delivery date
ANCHOR_UNSIGNED = "unsigned"    # another VALID set led by an unsigned draft
# ...or NOTE_OUTSIDE_CHANGE: another signed VALID set with another date,
# followed.

# What :func:`later_wish_action` decided (for the engine and its log line).
WISH_APPLY = "apply"    # the will found at the migration is gone: use the wish
WISH_DROP = "drop"      # that will is in force, or the wish is unusable
WISH_KEEP = "keep"      # not known yet: the wallet is not synchronized

#: The status flags of ``bal.core.will.WillItem`` the rules read (same names).
ITEM_FLAGS = (
    "VALID",
    "COMPLETE",
    "PUSHED",
    "INVALIDATED",
    "CONFIRMED",
    "MEMPOOL",
    "REPLACED",
)

# The name prefix of the will-executor fee outputs stored among the heirs of
# a will item (the same marker as ``bal.core.heirs``; not imported, so this
# module needs no Electrum).
_EXECUTOR_HEIR = 'w!ll3x3c"'

_EPOCH_DAY = date(1970, 1, 1)
_ISO_DAY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_DIGITS = re.compile(r"[0-9]+")
# "1054d", "1y", "36m", "-180d": a duration, never a date.
_PERIOD = re.compile(r"[+-]?[0-9]+\s*[A-Za-z]")


# --------------------------------------------------------------------------- #
# Holds: the two states that stop every flow (rule I6)
# --------------------------------------------------------------------------- #
class DeliveryHold(Exception):  # noqa: N818 - named after the state, not an error
    """Base of the two holds: no flow builds, signs, pushes or invalidates.

    ``code`` is the machine-readable code the command line returns.
    """

    code = "delivery_hold"
    default_message = "The delivery settings are on hold."

    def __init__(self, message=None):
        """Keep ``message``, or the class default when none is given."""
        super().__init__(message or self.default_message)


class DeliveryChoicePending(DeliveryHold):  # noqa: N818 - name from the #123 plan
    """The "which date?" question is not answered yet (``choice_pending``).

    Raised before anything else by every flow; the GUI Check asks the
    question, wallet close and the automatic rebuild skip, the command line
    returns :attr:`code`.
    """

    code = "delivery_choice_pending"
    default_message = (
        "The delivery date must be confirmed first: keep the date of the "
        "prepared will or use the later date."
    )


class DeliverySettingsUnavailable(DeliveryHold):  # noqa: N818 - name from the #123 plan
    """The settings of this wallet could not be prepared (fail closed, 3.4).

    The session then holds display-only values (:func:`display_only_values`)
    and no flow may act on them.
    """

    code = "delivery_settings_unavailable"
    default_message = "The delivery settings of this wallet could not be prepared."


def check_holds(settings, unavailable=False):
    """Raise the hold that applies, else do nothing.

    ``unavailable`` (the host's ``settings_unavailable``) wins: those
    settings cannot be trusted at all. Then ``choice_pending`` in
    ``settings``. A dict without that key (the fake hosts of the tests, a
    plain dict) is never held.
    """
    if unavailable:
        raise DeliverySettingsUnavailable()
    if isinstance(settings, Mapping) and settings.get(CHOICE_KEY):
        raise DeliveryChoicePending()


# --------------------------------------------------------------------------- #
# Month arithmetic (UTC days)
# --------------------------------------------------------------------------- #
def utc_midnight(ts):
    """00:00 UTC of the UTC day of timestamp ``ts``, as an int.

    Plain arithmetic: POSIX time has no leap seconds, so every UTC day is
    exactly :data:`DAY` seconds. No ``datetime.fromtimestamp``, which fails
    after 2038 where ``time_t`` is 32-bit (see ``BalTimestamp``).
    """
    return math.floor(ts) // DAY * DAY


def _day_of(ts):
    """The UTC calendar day (a ``date``) of timestamp ``ts``."""
    return _EPOCH_DAY + timedelta(days=math.floor(ts) // DAY)


def _ts_of(day):
    """00:00 UTC of calendar day ``day`` (a ``date``), as an int."""
    return (day - _EPOCH_DAY).days * DAY


def add_months(ts, n):
    """``n`` calendar months after the UTC day of ``ts``, at 00:00 UTC.

    ``n`` may be negative. The day of the month is kept, or clamped to the
    last day of a shorter month: 2026-01-31 + 1 = 2026-02-28 and
    2028-02-29 + 12 = 2029-02-28. The result is not invertible
    (``add_months(add_months(d, n), -n)`` is not always ``d``), so a date is
    computed once and stored, never recomputed backwards.
    """
    day = _day_of(ts)
    index = day.year * 12 + (day.month - 1) + int(n)
    year, month = divmod(index, 12)
    month += 1
    last = calendar.monthrange(year, month)[1]
    return _ts_of(date(year, month, min(day.day, last)))


def default_delivery(now):
    """The delivery date of a wallet with nothing to migrate from:
    :data:`DEFAULT_MONTHS` months after today (UTC day of ``now``)."""
    return add_months(utc_midnight(now), DEFAULT_MONTHS)


def months_between(start, end):
    """Whole calendar months from ``start`` to ``end`` (timestamps).

    The largest ``k >= 0`` with ``add_months(start, k)`` on or before the
    UTC day of ``end``: the same month arithmetic as the delivery date, so
    "in 2 years and 10 months" and the warning colours (#124) never
    disagree with the dates the plugin computes. 0 when ``end`` is before
    ``start``.
    """
    a, b = _day_of(start), _day_of(end)
    k = (b.year - a.year) * 12 + (b.month - a.month)
    limit = utc_midnight(end)
    while k > 0 and add_months(start, k) > limit:
        k -= 1
    return max(k, 0)


# --------------------------------------------------------------------------- #
# Remaining-time colours (#124)
# --------------------------------------------------------------------------- #
#: Defaults of the two thresholds, in calendar months (owner, 2026-10-11).
WARN_YELLOW_MONTHS = 12
WARN_RED_MONTHS = 6
#: Highest threshold the settings accept (ten years).
WARN_MONTHS_MAX = 120

LEVEL_GREEN = "green"
LEVEL_YELLOW = "yellow"
LEVEL_RED = "red"
LEVEL_PASSED = "passed"


def warning_months(yellow, red):
    """The two thresholds ``(yellow, red)`` to use, from stored values.

    Each must be a whole number of months from 1 to :data:`WARN_MONTHS_MAX`
    and red must be lower than yellow; otherwise both defaults are used, so
    a broken setting can never hide the warning. Not tied to the Check
    Alive (owner decision 5).
    """
    values = []
    for x in (yellow, red):
        if isinstance(x, bool):
            return WARN_YELLOW_MONTHS, WARN_RED_MONTHS
        try:
            n = int(x)
        except (TypeError, ValueError):
            return WARN_YELLOW_MONTHS, WARN_RED_MONTHS
        if n != x and not (isinstance(x, str) and x.strip() == str(n)):
            return WARN_YELLOW_MONTHS, WARN_RED_MONTHS
        values.append(n)
    yellow, red = values
    if not (1 <= red < yellow <= WARN_MONTHS_MAX):
        return WARN_YELLOW_MONTHS, WARN_RED_MONTHS
    return yellow, red


def delivery_level(delivery, now, yellow=WARN_YELLOW_MONTHS, red=WARN_RED_MONTHS):
    """How close the delivery date is: green, yellow, red or passed.

    ``passed`` when ``delivery`` is not after ``now``; ``red`` when it is
    before ``red`` calendar months after today (UTC day of ``now``);
    ``yellow`` when before ``yellow`` months; otherwise ``green``. Only a
    colour: it never changes the will.
    """
    if delivery <= now:
        return LEVEL_PASSED
    today = utc_midnight(now)
    if delivery < add_months(today, red):
        return LEVEL_RED
    if delivery < add_months(today, yellow):
        return LEVEL_YELLOW
    return LEVEL_GREEN


# --------------------------------------------------------------------------- #
# Stored values
# --------------------------------------------------------------------------- #
def _is_int(x):
    """True for an int that is not a bool."""
    return isinstance(x, int) and not isinstance(x, bool)


def _is_number(x):
    """True for a finite int or float that is not a bool."""
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return False
    return math.isfinite(x)


def as_delivery(x):
    """The delivery date held by a stored value, as an int, or None.

    A number, or a text holding a number (``"1882656000"``,
    ``"1882656000.0"``), between :data:`LOCKTIME_MIN` and
    :data:`LOCKTIME_MAX`. A float is truncated to whole seconds. Everything
    else gives None: a period (``"1054d"``), a block height, a bool, garbage.
    """
    if isinstance(x, str):
        try:
            x = float(x.strip())
        except ValueError:
            return None
    if not _is_number(x):
        return None
    value = int(x)
    if LOCKTIME_MIN <= value <= LOCKTIME_MAX:
        return value
    return None


def is_fee(x):
    """True for a fee rate: a finite number (not a bool, not a text) >= 1.

    The value is never rounded: an item fee of 13.001 must equal the
    setting, or the check reads a fee change and rebuilds.
    """
    return _is_number(x) and x >= 1


def is_current(d):
    """True when ``d`` already holds migrated settings (rule I2).

    ``settings_version`` an int >= :data:`FIRST_SCHEMA_VERSION` (1: a
    version-1 dict of #123 is current too, :func:`upgrade_values` only adds
    the form; a newer schema written by a later step counts too),
    ``locktime`` an int date (a float, a period, a block height or a 1970
    value is not) and ``baltx_fees`` a fee rate. Anything else is migrated
    again.
    """
    if not isinstance(d, Mapping):
        return False
    version = d.get("settings_version")
    locktime = d.get("locktime")
    return (
        _is_int(version)
        and version >= FIRST_SCHEMA_VERSION
        and _is_int(locktime)
        and LOCKTIME_MIN <= locktime <= LOCKTIME_MAX
        and is_fee(d.get("baltx_fees"))
    )


def _period_days(value):
    """Days of a legacy ``"Nd"`` / ``"Ny"`` recipe (a year is 365 days), else None.

    The reading of ``Util._relative_days`` (not imported: ``bal.core.util``
    needs Electrum). ``"36m"`` is not a period here, as in the old code.
    """
    if not isinstance(value, str):
        return None
    s = value.strip()
    if len(s) < 2 or s[-1] not in "dDyY":
        return None
    try:
        n = int(s[:-1])
    except ValueError:
        return None
    return n * 365 if s[-1] in "yY" else n


def _minus_days(value):
    """Days of a Check Alive ``"-Nd"`` / ``"-Ny"`` value in 1..3650, else None.

    The reading of ``checkalive.check_alive_days`` (not imported: #128
    removes it, while this bridge value is still written for downgrades).
    """
    s = str(value).strip()
    if len(s) < 3 or s[0] != "-" or s[-1].lower() not in ("d", "y"):
        return None
    try:
        n = int(s[1:-1])
    except ValueError:
        return None
    days = n * 365 if s[-1].lower() == "y" else n
    return days if 1 <= days <= 3650 else None


def resolve_legacy_date(value, now):
    """The date an old shared or legacy ``locktime`` value gives today, or None.

    A period ``"Nd"`` / ``"Ny"`` counts from 00:00 UTC of the day of ``now``
    (what ``Util.parse_locktime_string`` did on every check); a date is
    taken as it is. None when the value is unusable, is not a date (a block
    height), or is not after ``now``.
    """
    days = _period_days(value)
    if days is not None:
        ts = utc_midnight(now) + days * DAY
        if not LOCKTIME_MIN <= ts <= LOCKTIME_MAX:
            return None
    else:
        ts = as_delivery(value)
        if ts is None:
            return None
    return ts if ts > now else None


def _is_scalar(value):
    """True for a flat JSON scalar: text, bool, None or a finite number."""
    if value is None or isinstance(value, (str, bool)):
        return True
    return _is_number(value)


def validate_values(values):
    """Check settings values before they are written; return them normalized.

    The one validator of the migration and of every settings write
    (``context.set_will_setting``). Every value must be a flat JSON scalar.
    Then, for the keys present:

    * ``locktime``: a date (:func:`as_delivery`), stored as an int; a period
      is refused (#123: the Raw editor is hidden);
    * ``baltx_fees``: a fee rate (:func:`is_fee`), kept as it is;
    * ``threshold``: ``"-Nd"`` / ``"-Ny"`` in 1..3650 days, stored as
      ``"-Nd"`` (what ``normalize_check_alive`` would store);
    * ``settings_version``: an int >= 1;
    * ``later_wish`` (:data:`LATER_WISH_KEY`): a date, as ``locktime``,
      stored as an int;
    * ``form`` (#127): one of :data:`FORMS`;
    * ``period_months`` (#127): whole calendar months from
      :data:`PERIOD_MIN` to :data:`PERIOD_MAX` (:func:`as_months`), stored
      as an int;
    * ``period_start`` (#127): a date, as ``locktime``, stored as an int.

    Returns a new dict; raises ``ValueError`` (English, for the log and the
    command line) on the first bad value.
    """
    out = {}
    for key, value in values.items():
        if not _is_scalar(value):
            raise ValueError("{}: not a plain value: {!r}".format(key, value))
        out[key] = value
    for key in ("locktime", LATER_WISH_KEY, "period_start"):
        if key not in out:
            continue
        raw = out[key]
        if isinstance(raw, str) and _PERIOD.fullmatch(raw.strip()):
            raise ValueError("a period is not a delivery date: {!r}".format(raw))
        locktime = as_delivery(raw)
        if locktime is None:
            raise ValueError("not a delivery date: {!r}".format(raw))
        out[key] = locktime
    if "baltx_fees" in out and not is_fee(out["baltx_fees"]):
        raise ValueError("not a fee rate: {!r}".format(out["baltx_fees"]))
    if "threshold" in out:
        days = _minus_days(out["threshold"])
        if days is None:
            raise ValueError("not a Check Alive value: {!r}".format(out["threshold"]))
        out["threshold"] = "-{}d".format(days)
    if "settings_version" in out:
        version = out["settings_version"]
        if not _is_int(version) or version < 1:
            raise ValueError("not a settings version: {!r}".format(version))
    if "form" in out and out["form"] not in FORMS:
        raise ValueError("not a delivery form: {!r}".format(out["form"]))
    if "period_months" in out:
        months = as_months(out["period_months"])
        if months is None:
            raise ValueError(
                "not a period: {!r} (whole months from {} to {})".format(
                    out["period_months"], PERIOD_MIN, PERIOD_MAX
                )
            )
        out["period_months"] = months
    return out


def write_values(d, values, drop=()):
    """Write ``values`` into the settings dict ``d`` item by item.

    The keys in ``drop`` are deleted first (:data:`LEGACY_DROP` at
    migration). A value equal to the stored one is not written again. Never
    ``d.update()`` (see the module docstring). ``values`` should come from
    :func:`validate_values`.
    """
    for key in drop:
        if key in d:
            del d[key]
    for key, value in values.items():
        _put(d, key, value)


def _put(d, key, value):
    """``d[key] = value`` only when the key is missing or the value differs."""
    if key not in d or d[key] != value or type(d[key]) is not type(value):
        d[key] = value


def _drop(d, key):
    """Delete ``key`` from ``d`` when it is there."""
    if key in d:
        del d[key]


def default_values(now):
    """The settings of a wallet with nothing to migrate from.

    Delivery :func:`default_delivery`, fee :data:`DEFAULT_FEE`, Check Alive
    bridge :data:`DEFAULT_THRESHOLD`, schema :data:`SCHEMA_VERSION`, form
    :data:`FORM_FIXED` (#127).
    """
    return {
        "settings_version": SCHEMA_VERSION,
        "locktime": default_delivery(now),
        "baltx_fees": DEFAULT_FEE,
        "threshold": DEFAULT_THRESHOLD,
        "form": FORM_FIXED,
    }


# --------------------------------------------------------------------------- #
# Will items as the rules see them
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ItemView:
    """What the rules need of one stored will item.

    * ``wid``: the key of the item in the wallet's ``will`` dict;
    * ``locktime``: the nLockTime of its transaction, as stored (a block
      height or garbage is possible in legacy data: see :func:`as_delivery`);
    * ``fee``: its ``baltx_fees`` (``WillItem.tx_fees``), read AFTER
      ``Util.fix_will_tx_fees`` so a legacy ``tx_fees``-only item gives its
      fee, not 0;
    * the status flags of :data:`ITEM_FLAGS`, under the ``WillItem`` names;
    * ``recipes``: the delivery value of each real heir of the item
      (``heirs[name][2]``), the will-executor fee outputs left out. Only
      :func:`note_for` reads them (the drift-free rule for periods).

    Build it with :func:`item_view` or :func:`views_from`.
    """

    wid: str
    locktime: object
    fee: object = None
    VALID: bool = False
    COMPLETE: bool = False
    PUSHED: bool = False
    INVALIDATED: bool = False
    CONFIRMED: bool = False
    MEMPOOL: bool = False
    REPLACED: bool = False
    recipes: tuple = ()


def _flag(value):
    """A flag as a bool: a plain value, or a ``[label, bool]`` pair of
    ``WillItem.STATUS``."""
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return bool(value[1])
    return bool(value)


def item_view(wid, locktime, fee, flags, heirs=None):
    """Build an :class:`ItemView`.

    ``flags`` maps the flag names of :data:`ITEM_FLAGS` to bools, or is the
    ``WillItem.STATUS`` table (``{flag: [label, bool]}``); a missing flag is
    False. ``heirs`` is the item's ``{name: [address, amount, locktime]}``;
    the will-executor fee outputs (names starting with ``w!ll3x3c"``) are
    skipped.
    """
    marks = {name: _flag(flags.get(name, False)) for name in ITEM_FLAGS}
    recipes = []
    for name, heir in (heirs or {}).items():
        if str(name).startswith(_EXECUTOR_HEIR):
            continue
        if isinstance(heir, (list, tuple)) and len(heir) > 2:
            recipes.append(heir[2])
    return ItemView(
        wid=str(wid), locktime=locktime, fee=fee, recipes=tuple(recipes), **marks
    )


def views_from(willitems):
    """The :class:`ItemView` list of ``{wid: WillItem}`` (sorted by wid).

    Duck-typed, so this module needs no ``WillItem``: each value must have
    ``tx.locktime``, ``tx_fees``, ``STATUS`` and ``heirs``.
    """
    return [
        item_view(wid, w.tx.locktime, w.tx_fees, w.STATUS, w.heirs)
        for wid, w in sorted((willitems or {}).items())
    ]


def _dated(items, predicate):
    """The items that pass ``predicate`` and have a date locktime."""
    return [i for i in items if predicate(i) and as_delivery(i.locktime) is not None]


def _earliest(items):
    """The earliest date locktime of ``items`` (all dated), or None."""
    return min((as_delivery(i.locktime) for i in items), default=None)


def non_date_items(items):
    """Wids of the VALID or sent items whose locktime is not a date.

    The migration ignores them (a block height would become a 1970 date);
    the caller writes them in the log.
    """
    return sorted(
        i.wid
        for i in items
        if (i.VALID or i.PUSHED) and as_delivery(i.locktime) is None
    )


# --------------------------------------------------------------------------- #
# One-time migration (#123, schema 1)
# --------------------------------------------------------------------------- #
def _raw_get(raw, key):
    """``raw[key]`` when ``raw`` is a mapping, else None."""
    return raw.get(key) if isinstance(raw, Mapping) else None


def _first_fee(candidates):
    """The first fee rate (:func:`is_fee`) among ``candidates``, else None."""
    for value in candidates:
        if is_fee(value):
            return value
    return None


def _fees_of(raw):
    """The fee values of an old settings dict: ``baltx_fees``, then the
    legacy ``tx_fees``."""
    return (_raw_get(raw, "baltx_fees"), _raw_get(raw, "tx_fees"))


def _first_minus_nd(raws):
    """The first Check Alive ``"-Nd"`` / ``"-Ny"`` of the old dicts, as
    ``"-Nd"``, else None."""
    for raw in raws:
        days = _minus_days(_raw_get(raw, "threshold"))
        if days is not None:
            return "-{}d".format(days)
    return None


def convert_check_alive(value, locktime, creation_date, now):
    """An old Check Alive ``value`` as ``"-Nd"`` before ``locktime``, or None.

    The rule of ``checkalive.normalize_check_alive`` (#80, owner decision
    D2 = A: the SAME Check Alive date is kept), with ``locktime`` the
    delivery date the wallet now stores:

    * ``"-Nd"`` / ``"-Ny"`` in 1..3650 days: the same, in days;
    * a date (:func:`as_delivery`, e.g. the absolute value BAL 0.6.1
      writes): ``locktime`` minus that date, in days, rounded;
    * a positive period ``"Nd"`` / ``"Ny"`` (the old "after the creation of
      the will" form): counted from 00:00 UTC of the day of
      ``creation_date`` (``Will.get_creation_time``: the oldest VALID item),
      or of ``now`` when it is unknown; then as a date.

    None where ``normalize_check_alive`` stores its default ``"-180d"``: any
    other value (a negative period out of range, a block height, garbage),
    a creation date that is not a number, or a result outside 1..3650 days
    (a date on or after the delivery, or too far before it). The migration
    then tries the next old dict, and the default comes last.

    Copied, not imported: ``bal.core.checkalive`` needs Electrum, and its
    ``BalTimestamp`` reads dates with ``datetime.fromtimestamp``, which fails
    after 2038 where ``time_t`` is 32-bit. Plain UTC-day arithmetic here;
    ``tests/test_core_delivery_check_alive.py`` checks that both rules agree.
    """
    days = _minus_days(value)
    if days is not None:
        return "-{}d".format(days)
    period = _period_days(value)
    if period is not None:
        if period < 0:
            return None             # a negative period out of range
        anchor = creation_date if creation_date else now
        if not _is_number(anchor):
            return None
        check = utc_midnight(anchor) + period * DAY
    else:
        check = as_delivery(value)
        if check is None:
            return None
    days = round((locktime - check) / DAY)
    return "-{}d".format(days) if 1 <= days <= 3650 else None


def _check_alive_bridge(sources, old_raws, locktime, creation_date, now):
    """The Check Alive the migration stores, always ``"-Nd"``.

    1. The first ``"-Nd"`` / ``"-Ny"`` of the old dicts, in the order of
       :func:`_old_sources`. Not gated on plugin data: it is a plugin-wide
       habit, and #128 makes it a plugin setting.
    2. Else the first old value of ``old_raws`` (the dicts of a wallet with
       plugin data) that :func:`convert_check_alive` turns into ``"-Nd"``
       against the migrated delivery date ``locktime``: the same Check
       Alive date, as #80 converted it on every load before #123 (a fixed
       date, e.g. BAL 0.6.1's default, or a positive period). Without this
       an old fixed date became ``"-180d"``, already in the past for a
       delivery less than 180 days away, and the ADVANCED Check stopped.
       Gated: such a value belongs to the old shared delivery date, which a
       wallet without plugin data does not inherit.
    3. Else :data:`DEFAULT_THRESHOLD`.
    """
    found = _first_minus_nd(raw for _name, raw in sources)
    if found is not None:
        return found
    for raw in old_raws:
        converted = convert_check_alive(
            _raw_get(raw, "threshold"), locktime, creation_date, now
        )
        if converted is not None:
            return converted
    return DEFAULT_THRESHOLD


def _old_raw_locktime(raws):
    """The first old ``locktime`` value as found (a text or a number), else
    None."""
    for raw in raws:
        value = _raw_get(raw, "locktime")
        if isinstance(value, str) or _is_number(value):
            return value
    return None


def _old_sources(global_raw, legacy_raw):
    """The old dicts in the order they are read: ``[(source, dict)]``.

    The global first, then the wallet's own dict (a BAL 0.2.x value), as in
    the #123 plan. Except when the wallet's dict already has a
    ``settings_version``: Safe21 wrote it after #123, while the global is
    frozen since then, so the wallet's own values are the newest and come
    first. That only happens when another program broke a migrated dict.
    """
    if _is_int(_raw_get(legacy_raw, "settings_version")):
        return [(SOURCE_LEGACY, legacy_raw), (SOURCE_GLOBAL, global_raw)]
    return [(SOURCE_GLOBAL, global_raw), (SOURCE_LEGACY, legacy_raw)]


def migration_values(
    items,
    global_raw,
    legacy_raw,
    has_plugin_data,
    now,
    creation_date=None,
    multiverse=False,
):
    """The settings to write into a wallet whose dict is not current.

    ``items``: :class:`ItemView` of the STORED will (stored flags only: the
    wallet is not synchronized at open). ``global_raw``: Electrum's global
    ``bal_will_settings`` read raw (``config.get``), or None. ``legacy_raw``:
    a plain copy of the wallet's own ``will_settings`` as found (a BAL 0.2.x
    value, or nothing). ``has_plugin_data``: the wallet has heirs or a will.
    ``now``: the caller's clock. ``creation_date``: the creation time of the
    stored will (``Will.get_creation_time``), or None; only an old positive
    Check Alive reads it (see :func:`convert_check_alive`). ``multiverse``:
    the plugin's multiverse setting (each heir carries its own date); only
    the note reads it (see :func:`note_for`).

    The delivery date, the first source that gives one:

    1. ``"will"``: the earliest VALID item with a date locktime (owner
       decision 6). Never mixed with the sent items: a sent item EARLIER
       than the VALID ones must keep reading as a postpone (#69/#72);
    2. ``"sent"``: no VALID item; the earliest item sent to a will-executor,
       not VALID / INVALIDATED / CONFIRMED / MEMPOOL, dated after ``now``.
       Every other sent item is later, so nothing reads as postponed;
    3. only when ``has_plugin_data`` (a new wallet must not inherit the
       frozen shared value): ``"global"`` then ``"legacy_wallet"``
       (:func:`_old_sources` gives the order), through
       :func:`resolve_legacy_date` (a period counts from today);
    4. ``"default"``: :func:`default_delivery`.

    Block heights and garbage never count as dates (:func:`non_date_items`
    lists them for the log). The fee: the single fee rate of the items of
    rule 1 or 2, else (only with plugin data) the first fee rate of the old
    dicts, else :data:`DEFAULT_FEE`. The Check Alive bridge
    (:func:`_check_alive_bridge`): the first ``"-Nd"`` of the old dicts (not
    gated: it is a plugin-wide habit, and #128 makes it a plugin setting),
    else (only with plugin data) the first old fixed date or positive period
    converted against the migrated date, keeping the same Check Alive date
    (#80), else :data:`DEFAULT_THRESHOLD`.

    Also ``migrated_from``, ``migrated_at``, ``legacy_locktime`` (the old
    value as found, only with plugin data), ``form`` :data:`FORM_FIXED`
    (#127; the schema is :data:`SCHEMA_VERSION`) and the note of
    :func:`note_for` (with the question and the later wish it may add).
    Everything passes :func:`validate_values` (``ValueError`` otherwise:
    the caller fails closed). Returns a new dict; nothing is written here.
    """
    items = list(items)
    sources = _old_sources(global_raw, legacy_raw)
    old = sources if has_plugin_data else []
    old_raws = [raw for _name, raw in old]

    valid = _dated(items, lambda i: i.VALID)
    sent = pending_sent(items, now)
    if valid:
        base, source = valid, SOURCE_WILL
    elif sent:
        base, source = sent, SOURCE_SENT
    else:
        base, source = [], None

    if base:
        locktime = _earliest(base)
    else:
        locktime = None
        for name, raw in old:
            locktime = resolve_legacy_date(_raw_get(raw, "locktime"), now)
            if locktime is not None:
                source = name
                break
        if locktime is None:
            locktime, source = default_delivery(now), SOURCE_DEFAULT

    fees = {i.fee for i in base if is_fee(i.fee)}
    if len(fees) == 1:
        fee = fees.pop()
    else:
        fee = _first_fee(value for raw in old_raws for value in _fees_of(raw))
        if fee is None:
            fee = DEFAULT_FEE

    out = {
        "settings_version": SCHEMA_VERSION,
        "locktime": locktime,
        "baltx_fees": fee,
        "threshold": _check_alive_bridge(
            sources, old_raws, locktime, creation_date, now
        ),
        "migrated_from": source,
        "migrated_at": int(now),
        # #127: every migrated wallet starts with a fixed date (owner
        # decision 1); the period form is chosen later in the wizard.
        "form": FORM_FIXED,
    }
    old_raw = _old_raw_locktime(old_raws)
    if old_raw is not None:
        out["legacy_locktime"] = old_raw
    out.update(note_for(source, locktime, old_raw, valid, now, multiverse=multiverse))
    return validate_values(out)


def _wish_against_will(days, valid):
    """The date an old period aimed at, drift-free, or None.

    Against the earliest VALID item whose heirs carry a period recipe:
    ``tx_locktime + (days - recipe_days) * DAY``, the idea of
    ``Util.resolve_locktime_against_tx`` kept on the tx's own time of day, so
    an unchanged recipe gives exactly the will's date. With several period
    recipes in that item the shortest one is used: like
    ``Will._postponed_heir``, one heir dated later is enough. None when no
    VALID item has a period recipe: a period against an absolute recipe
    drifts with the clock by design (the day +2 false postpone), so it is
    not a wish.
    """
    for item in sorted(valid, key=lambda i: (as_delivery(i.locktime), i.wid)):
        recipes = [d for d in (_period_days(r) for r in item.recipes) if d is not None]
        if recipes:
            return as_delivery(item.locktime) + (days - min(recipes)) * DAY
    return None


def note_for(source, locktime, old_raw, valid, now, multiverse=False):
    """The one-time note of a migration, as settings keys (maybe none).

    ``old_raw``: the old ``locktime`` value as found (``legacy_locktime``;
    None when there is none or it must not count: no plugin data);
    ``valid``: the VALID items (only those with a date locktime are read);
    ``locktime``: the migrated date; ``multiverse``: the plugin's
    multiverse setting. ``old`` is the date the code before #123 really
    aimed at for this wallet:

    * an old DATE after ``now``: itself;
    * an old PERIOD: :func:`_wish_against_will`.

    The note, first match:

    1. ``later_pending``: ``source == "will"`` and ``old`` at least one day
       after ``locktime`` (a postpone asked for and cancelled at Invalidate,
       or carried out by a paid invalidation the wallet has not seen yet).
       ``choice_pending = 1`` too when an earliest VALID item is not signed
       and sent (COMPLETE and PUSHED): nothing is signed until the owner
       chooses;
    2. ``date_differs``: an old DATE at least one day away from ``locktime``;
    3. ``converted``: the old value was a period (the owner's case);
    4. none.

    ``note_old`` is the old wish, ``note_new`` the date now stored;
    ``converted`` has only ``note_new``.

    The later wish (FS-2 of the #123 review): with rule 1, and with rule 2
    when ``source == "sent"`` and the old DATE is at least one day after
    ``locktime``, ``later_wish`` (:data:`LATER_WISH_KEY`) = ``old`` too. The
    migration reads STORED flags: the will found may already be invalidated
    on-chain by a paid postpone the wallet had not registered yet. Then
    keeping its date would rebuild the will EARLIER than the date already
    paid for. :func:`later_wish_action` settles it once the wallet shows
    which will is really in force.

    With ``multiverse`` each heir carries its own date and the shared value
    never drove them: it is no wish. Rule 1 is skipped (no question, no
    later wish); at most the informative note of rule 2 or 3.
    """
    if old_raw is None:
        return {}
    valid = _dated(valid, lambda i: i.VALID)
    days = _period_days(old_raw)
    if days is None:
        old = as_delivery(old_raw)
        if old is not None and old <= now:
            old = None
    else:
        old = _wish_against_will(days, valid)
    later = not multiverse and old is not None and old >= locktime + DAY
    if source == SOURCE_WILL and later:
        out = {
            "note_kind": NOTE_LATER_PENDING,
            "note_old": old,
            "note_new": locktime,
            LATER_WISH_KEY: old,
        }
        earliest = [i for i in valid if as_delivery(i.locktime) == locktime]
        if any(not (i.COMPLETE and i.PUSHED) for i in earliest):
            out[CHOICE_KEY] = 1
        return out
    if days is None and old is not None and abs(old - locktime) >= DAY:
        out = {"note_kind": NOTE_DATE_DIFFERS, "note_old": old, "note_new": locktime}
        if source == SOURCE_SENT and later:
            out[LATER_WISH_KEY] = old
        return out
    if days is not None:
        return {"note_kind": NOTE_CONVERTED, "note_new": locktime}
    return {}


# --------------------------------------------------------------------------- #
# Anchor: a will changed by another program (#123, plan 3.2)
# --------------------------------------------------------------------------- #
def fingerprint(ids):
    """16 hex characters that identify a set of wids (order does not matter)."""
    text = "\n".join(sorted(str(i) for i in ids))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def valid_ids(items):
    """The sorted wids of the VALID items (any locktime)."""
    return sorted(i.wid for i in items if i.VALID)


def earliest_valid(items):
    """The earliest date locktime of the VALID items, or None.

    Block heights and garbage are ignored (:func:`as_delivery`).
    """
    return _earliest(_dated(items, lambda i: i.VALID))


def pending_sent(items, now):
    """The items sent to a will-executor that may still be in force.

    ``PUSHED``, not VALID / INVALIDATED / CONFIRMED / MEMPOOL, with a date
    locktime after ``now`` (block heights and garbage are ignored). The
    source ``"sent"`` of :func:`migration_values` (used only when no item
    is VALID), and the command line's will in force when no item is VALID.
    """
    return [
        i
        for i in _dated(
            items,
            lambda i: i.PUSHED
            and not (i.VALID or i.INVALIDATED or i.CONFIRMED or i.MEMPOOL),
        )
        if as_delivery(i.locktime) > now
    ]


def earliest_in_force(items, now):
    """The date of the will in force: :func:`earliest_valid`, or, when no
    item is VALID, the earliest of :func:`pending_sent`; None when neither
    gives a date."""
    valid = earliest_valid(items)
    if valid is not None:
        return valid
    return _earliest(pending_sent(items, now))


def earliest_signed_valid(items):
    """The earliest date locktime of the VALID items that are signed
    (``COMPLETE``), or None.

    Only a signed item can be the will in force: an unsigned draft (left,
    for example, by another program whose "Sign your will" was cancelled)
    was never sent. Block heights and garbage are ignored.
    """
    return _earliest(_dated(items, lambda i: i.VALID and i.COMPLETE))


def record_will_anchor(d, items):
    """Store the anchor of the will Safe21 has just saved.

    ``anchor_valid``: :func:`fingerprint` of the VALID wids;
    ``anchor_locktime``: their earliest date (diagnostic; removed when there
    is none). Called after every save of the will items, with the items AS
    STORED (the engine reads them back from the wallet), so the anchor never
    describes a set that differs from the stored will.
    """
    items = list(items)
    _put(d, "anchor_valid", fingerprint(valid_ids(items)))
    earliest = earliest_valid(items)
    if earliest is None:
        _drop(d, "anchor_locktime")
    else:
        _put(d, "anchor_locktime", earliest)


def check_anchor(d, items):
    """At every load: follow a will that another program changed.

    Runs after the migration, on current settings. Returns what it found:

    * :data:`ANCHOR_FIRST`: no anchor yet; recorded now;
    * :data:`ANCHOR_SAME`: the VALID set is the one Safe21 saved;
    * :data:`ANCHOR_MOVED`: another VALID set, but with the stored date (or
      no VALID item): the anchor is recorded again, nothing else changes;
    * :data:`ANCHOR_UNSIGNED`: another VALID set with no signed item, or
      whose earliest item is an unsigned draft EARLIER than the stored date
      (e.g. BAL or an older ZIP read the frozen shared date, prepared an
      earlier will and its "Sign your will" was cancelled). A draft was
      never sent, so it is not the will in force: the stored date is kept,
      no note is written, only the anchor is recorded again;
    * :data:`NOTE_OUTSIDE_CHANGE`: another VALID set whose earliest SIGNED
      item (:func:`earliest_signed_valid`) has another date (BAL, an older
      Safe21 or an older ZIP postponed or anticipated the will and signed
      it). The stored date FOLLOWS the will really in force and is never
      reverted, so no paid invalidation and no earlier rebuild. The
      one-time note keeps both dates; a pending "which date?" question and
      the later wish of the migration (:data:`LATER_WISH_KEY`) are dropped,
      because the will they were about has been replaced.
    """
    items = list(items)
    if "anchor_valid" not in d:
        record_will_anchor(d, items)
        return ANCHOR_FIRST
    if fingerprint(valid_ids(items)) == d["anchor_valid"]:
        return ANCHOR_SAME
    result = ANCHOR_MOVED
    stored = as_delivery(d.get("locktime"))
    earliest = earliest_valid(items)
    signed = earliest_signed_valid(items)
    if earliest is not None and (
        signed is None
        or (earliest < signed and (stored is None or earliest < stored))
    ):
        # FS-1 of the #123 review: following an unsigned draft made the
        # next Check sign and send a will EARLIER than the one in force.
        result = ANCHOR_UNSIGNED
    elif signed is not None and signed != stored:
        set_note(d, NOTE_OUTSIDE_CHANGE, old=stored, new=signed)
        _drop(d, CHOICE_KEY)
        _drop(d, LATER_WISH_KEY)
        _put(d, "locktime", signed)
        result = NOTE_OUTSIDE_CHANGE
    record_will_anchor(d, items)
    return result


# --------------------------------------------------------------------------- #
# The one-time note and the "which date?" question (#123, plan 3.3)
# --------------------------------------------------------------------------- #
def set_note(d, kind, old=None, new=None):
    """Store the one-time note ``kind`` with its dates (None: no such date)."""
    _put(d, "note_kind", kind)
    for key, value in (("note_old", old), ("note_new", new)):
        if value is None:
            _drop(d, key)
        else:
            _put(d, key, value)


def pending_note(d):
    """``(kind, old, new)`` of the note still to show, or None.

    ``old`` / ``new`` are None when the note has no such date.
    """
    if not isinstance(d, Mapping) or not d.get("note_kind"):
        return None
    return d["note_kind"], d.get("note_old"), d.get("note_new")


def mark_note_shown(d):
    """Delete the note once it has been shown (call after the message box).

    While the "which date?" question is pending its two dates stay: the
    Check question reads them (:func:`choice_dates`). The later wish of the
    migration (:data:`LATER_WISH_KEY`) is not part of the note and stays
    (see :func:`later_wish_action`).
    """
    _drop(d, "note_kind")
    if not d.get(CHOICE_KEY):
        _drop(d, "note_old")
        _drop(d, "note_new")


def choice_dates(d):
    """``(keep, later)`` of the pending "which date?" question, or None.

    ``keep``: the stored date (the will already prepared, no fee);
    ``later``: the old wish (``note_old``; None if it was lost). None when
    no question is pending.
    """
    if not isinstance(d, Mapping) or not d.get(CHOICE_KEY):
        return None
    return as_delivery(d.get("locktime")), as_delivery(d.get("note_old"))


def clear_choice(d):
    """End the "which date?" question: delete ``choice_pending`` and the note.

    Called by the one validated write when the owner keeps the date or
    stores another one.
    """
    _drop(d, CHOICE_KEY)
    for key in NOTE_KEYS:
        _drop(d, key)


# --------------------------------------------------------------------------- #
# The later wish of the migration (FS-2 of the #123 review)
# --------------------------------------------------------------------------- #
def later_wish_action(d, items, synced):
    """What to do with the later wish the migration kept; nothing is written.

    The migration takes the date of the will it finds from the STORED
    flags (the wallet is not synchronized at open). When the old settings
    asked for a later date (:func:`note_for` stores it as
    :data:`LATER_WISH_KEY`), that will may already have been invalidated
    on-chain by the paid postpone of the old code, with the wallet not yet
    aware of it. Keeping its date would then rebuild the will one postpone
    EARLIER than the date already paid for.

    ``d``: the settings; ``items``: :class:`ItemView` of the will AFTER the
    items already invalidated on-chain were marked
    (``Will.refresh_invalidated``); ``synced``: the wallet is synchronized
    (``wallet.is_up_to_date()``), so those marks follow the chain. Returns
    None when no wish is kept, else:

    * :data:`WISH_APPLY`: no VALID item is left: the will found at the
      migration has been invalidated. The wish becomes the delivery date:
      it is later than the stored date, so no anticipation and no new fee
      (the old code built at that date too);
    * :data:`WISH_DROP`: the wallet is synchronized and VALID items still
      exist: that will is really in force (the owner had cancelled the
      postpone). Also when the wish is not a date later than the stored
      date: applying it could only anticipate the will;
    * :data:`WISH_KEEP`: VALID items exist but the wallet is not
      synchronized: their flags may be stale, ask again next time.
    """
    if not isinstance(d, Mapping) or LATER_WISH_KEY not in d:
        return None
    wish = as_delivery(d.get(LATER_WISH_KEY))
    stored = as_delivery(d.get("locktime"))
    if wish is None or stored is None or wish <= stored:
        return WISH_DROP
    if not any(i.VALID for i in items):
        return WISH_APPLY
    if synced:
        return WISH_DROP
    return WISH_KEEP


def drop_later_wish(d):
    """Delete the later wish of the migration (:data:`LATER_WISH_KEY`).

    Called when it was applied or found obsolete (:func:`later_wish_action`)
    and by the one validated write whenever a delivery date is written (the
    owner chose a date, or answered the "which date?" question).
    """
    _drop(d, LATER_WISH_KEY)


# --------------------------------------------------------------------------- #
# Fail closed (#123, plan 3.4)
# --------------------------------------------------------------------------- #
def display_only_values(items, now):
    """Values to SHOW when the settings could not be prepared.

    The earliest VALID date and its single fee rate, else the defaults; the
    default Check Alive bridge. Never the shared global value, never a raw
    legacy dict (a period there is the day +2 false postpone). There is no
    ``settings_version``, so these values never pass :func:`is_current`; no
    flow may act on them (:class:`DeliverySettingsUnavailable`).
    """
    valid = _dated(list(items), lambda i: i.VALID)
    locktime = _earliest(valid)
    if locktime is None:
        locktime = default_delivery(now)
    fees = {i.fee for i in valid if is_fee(i.fee)}
    fee = fees.pop() if len(fees) == 1 else DEFAULT_FEE
    return {"locktime": locktime, "baltx_fees": fee, "threshold": DEFAULT_THRESHOLD}


# --------------------------------------------------------------------------- #
# The delivery form: a fixed date or a renewable period (#127)
# --------------------------------------------------------------------------- #
class DeliveryFormLocked(Exception):  # noqa: N818 - named after the state, not an error
    """The delivery form cannot change while a will is in force (#127).

    Owner decision 2: a will signed or sent keeps its form; to change it
    the will must be invalidated on-chain (miner fee) and the invalidation
    confirmed. ``current_form`` is the form in force (:data:`FORMS`);
    ``code`` is the machine-readable code the command line returns.
    """

    code = "delivery_form_locked"

    def __init__(self, current_form):
        """Keep the form in force and an English message for the log."""
        self.current_form = current_form
        super().__init__(
            "The delivery form ({}) cannot change while a will is in "
            "force.".format(current_form)
        )


def as_months(x):
    """A period in calendar months as an int, or None.

    A whole number (an int, a float without a fraction, or a text holding
    one) from :data:`PERIOD_MIN` to :data:`PERIOD_MAX`. A bool, a fraction,
    a recipe such as ``"36m"`` or anything else gives None.
    """
    if isinstance(x, bool):
        return None
    if isinstance(x, str):
        text = x.strip()
        if not _DIGITS.fullmatch(text):
            return None
        x = int(text)
    if not _is_number(x) or x != int(x):
        return None
    n = int(x)
    return n if PERIOD_MIN <= n <= PERIOD_MAX else None


def form_of(settings):
    """The delivery form of ``settings``: :data:`FORM_PERIOD` or
    :data:`FORM_FIXED`.

    Missing or unknown means fixed (plan 1.1): a version-1 dict, the plain
    dict of a test host or anything that is not a mapping is a fixed date,
    with nothing changed.
    """
    if isinstance(settings, Mapping) and settings.get("form") == FORM_PERIOD:
        return FORM_PERIOD
    return FORM_FIXED


def is_period(settings):
    """True when ``settings`` hold the period form (:func:`form_of`)."""
    return form_of(settings) == FORM_PERIOD


def period_months(settings):
    """The stored period N of the period form (:func:`as_months`), or None.

    None for a fixed date and for a period form whose N is missing or
    broken: the callers then do not move any date.
    """
    if not is_period(settings):
        return None
    return as_months(settings.get("period_months"))


def period_values(now, months):
    """The dates of a period will prepared today: ``{"locktime", "period_start"}``.

    ``period_start`` is 00:00 UTC of today (the UTC day of ``now``) and
    ``locktime`` :func:`add_months` of it by ``months``. Computed once and
    stored, at the first build with no will in force and at a renewal; never
    recomputed backwards (the month arithmetic is not invertible, plan
    section 2). ``ValueError`` when ``months`` is not a period.
    """
    n = as_months(months)
    if n is None:
        raise ValueError("not a period: {!r}".format(months))
    today = utc_midnight(now)
    return {"locktime": add_months(today, n), "period_start": today}


def upgrade_values(d):
    """The values that bring a current settings dict to schema 2 (#127).

    A dict of version 1 (#123) gets ``form`` :data:`FORM_FIXED` (owner
    decision: every existing wallet starts fixed, whatever ``form`` it may
    hold) and ``settings_version`` 2. A dict already at version 2 or later,
    or one that is not current (:func:`is_current`: the full migration runs
    first), needs nothing: ``{}``. Pure: the caller writes the result item
    by item (:func:`write_values`), so a second load writes nothing.
    """
    if not is_current(d) or d.get("settings_version") >= SCHEMA_VERSION:
        return {}
    return {"form": FORM_FIXED, "settings_version": SCHEMA_VERSION}


def form_locked(items, synced, executable):
    """True when the delivery form cannot change: a will is in force (#127).

    ``items``: :class:`ItemView` of the will (after the items already
    invalidated on-chain were marked); ``synced``: the wallet is
    synchronized (``wallet.is_up_to_date()``); ``executable(wid)``: True
    when no input of that item is spent by a CONFIRMED transaction (an
    invalidation only in the mempool can still be dropped or replaced, so
    the old will may still be executed). Locked when:

    * a VALID item is signed (COMPLETE) or sent (PUSHED); or
    * the wallet is synchronized and an item sent to a will-executor, not
      VALID, CONFIRMED or MEMPOOL (INVALIDATED included: an invalidation
      is marked as soon as it is in the mempool), is still ``executable``;
    * the wallet is not synchronized and any sent item is not CONFIRMED
      (its flags may be stale: conservative).

    A VALID item never signed nor sent does not lock: it was never in
    force. Owner decision 2 (2026-10-12).
    """
    items = list(items)
    if any(i.VALID and (i.COMPLETE or i.PUSHED) for i in items):
        return True
    for i in items:
        if not i.PUSHED or i.CONFIRMED:
            continue
        if not synced:
            return True
        if i.VALID or i.MEMPOOL:
            continue
        if executable(i.wid):
            return True
    return False


def will_in_force(items, synced, executable):
    """True when a will is in force, for the period re-anchor (#127, policy b).

    The meaning of #79: a VALID item exists (signed or not), or an item
    sent to a will-executor, not VALID / INVALIDATED / CONFIRMED / MEMPOOL,
    is still ``executable(wid)`` (no input spent by a broadcast
    transaction: ``Will.sent_still_executable``). On a wallet that is not
    synchronized (``synced`` False) any such sent item counts, so the
    re-anchor waits. While a will is in force the period date is never
    moved: only a renewal or the first build sets it.
    """
    items = list(items)
    if any(i.VALID for i in items):
        return True
    for i in items:
        if (
            not i.PUSHED
            or i.VALID
            or i.INVALIDATED
            or i.CONFIRMED
            or i.MEMPOOL
        ):
            continue
        if not synced or executable(i.wid):
            return True
    return False


# --------------------------------------------------------------------------- #
# Command line
# --------------------------------------------------------------------------- #
def parse_cli_date(text, now):
    """The delivery date typed on the command line, as an int.

    ``YYYY-MM-DD`` is 00:00 UTC of that day (``Util.str_to_locktime`` is not
    used: it reads LOCAL midnight). A UNIX timestamp (digits, or an int) is
    taken as it is. Refused with ``ValueError`` (English: the command line
    is not translated): periods (``1054d``, ``1y``, ``36m``), other formats,
    impossible days, values below :data:`LOCKTIME_MIN` (block heights) or
    above :data:`LOCKTIME_MAX`, and dates not after ``now``.
    """
    hint = "Use a date as YYYY-MM-DD (00:00 UTC) or a UNIX timestamp."
    if isinstance(text, bool):
        raise ValueError("Not a date: {!r}. {}".format(text, hint))
    if isinstance(text, int):
        ts = text
    elif isinstance(text, float):
        if not math.isfinite(text) or text != int(text):
            raise ValueError("Not a date: {!r}. {}".format(text, hint))
        ts = int(text)
    elif isinstance(text, str):
        s = text.strip()
        if _ISO_DAY.fullmatch(s):
            try:
                ts = _ts_of(date.fromisoformat(s))
            except ValueError:
                raise ValueError("No such day: {}. {}".format(s, hint)) from None
        elif _DIGITS.fullmatch(s):
            ts = int(s)
        elif _PERIOD.fullmatch(s):
            raise ValueError(
                "A period such as {} is not accepted: the delivery date is "
                "fixed. {}".format(s, hint)
            )
        else:
            raise ValueError("Not a date: {!r}. {}".format(s, hint))
    else:
        raise ValueError("Not a date: {!r}. {}".format(text, hint))
    if ts < LOCKTIME_MIN:
        raise ValueError(
            "{} is below {}: Bitcoin reads it as a block height, not a date. "
            "{}".format(ts, LOCKTIME_MIN, hint)
        )
    if ts > LOCKTIME_MAX:
        raise ValueError(
            "{} is too late: the last date a Bitcoin transaction can hold is "
            "2106-02-07.".format(ts)
        )
    if ts <= now:
        raise ValueError("{} is not in the future.".format(_iso(ts)))
    return ts


def _iso(ts):
    """``YYYY-MM-DD HH:MM UTC`` of timestamp ``ts`` (messages only)."""
    seconds = math.floor(ts) % DAY
    return "{} {:02d}:{:02d} UTC".format(
        _day_of(ts).isoformat(), seconds // 3600, seconds % 3600 // 60
    )

