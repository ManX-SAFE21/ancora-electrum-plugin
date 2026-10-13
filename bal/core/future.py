"""The future of the will, for the "Future" view of the Will tab (CHANGELOG #131).

The Will tab lists the will by versions (#129): a precise history, but it
does not say in one look what happens if the owner does nothing more. The
"Future" view draws it on a timeline: today, the Check Alive (when there is
one), the delivery to the heirs with the time left in the #124 colour, the
will-executors that hold the will, and one sentence: "If the will is no
longer checked or renewed, on <date> the bitcoin will go to: <heirs>".

This module builds the data of that view, Qt-free (``bal/core`` rule), so
the same data can also be drawn in the PDF report (#132). It only reads:
it never builds, signs, sends, invalidates or writes anything.

Where each value comes from (verified on the code, see
``context/TECHNICAL-NOTES.md``):

* THE will in force: :func:`bal.core.will_versions.build_history`
  (``History.current``: the version holding the in-force item with the
  EARLIEST delivery, #128/#129). Not the VALID items: after a fee change
  cancelled at the password the VALID items are an unsigned draft, while
  the will-executors still hold (and would execute) the will sent before.
* The delivery: the locktime of that version's transactions; with
  multiverse on, each later instalment (a child transaction spending the
  change of its parent) is one more :class:`Instalment`.
* The heirs and their REAL shares: the outputs of one transaction of the
  version (the heirs' addresses), not the shares entered in the Heirs tab
  (they can sum to more or less than 100 %; the will always spends the
  whole wallet). The share is the heir's amount over the amount paid to
  all heirs (the will-executor's fee, the miner fee and the change left
  out), the same rule as the report's ``heir_rows``.
* The colour: :func:`bal.core.summary.delivery_warning` (#124).
* The will-executors: :func:`bal.core.summary.executor_states` on the
  version's transactions (each executor at its worst copy).
* The Check Alive: as the will summary computes it
  (``checkalive.check_alive_renewal`` / ``resolve_date_to_check``); none
  in BASIC mode with a fixed date (``checkalive.check_alive_basic``).
* The period form (#127): ``delivery.period_months`` of the settings.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

from ..i18n import _
from . import delivery as _delivery
from . import will_versions
from .checkalive import check_alive_basic, check_alive_renewal, resolve_date_to_check
from .heirs import HEIR_ADDRESS, HEIR_REAL_AMOUNT, is_op_return_address
from .messages import _day, months_text
from .summary import _executors_row, delivery_warning, executor_counts, executor_states

_DAY = 86400

# The reserved name prefix of the will-executor pseudo-heirs (their fee
# outputs, ``bal/core/heirs.py``): not a person.
_EXECUTOR_HEIR = 'w!ll3x3c"'

STATE_IN_FORCE = "in_force"
STATE_DRAFT_ONLY = "draft_only"
STATE_NONE = "none"
STATE_EXECUTED = "executed"
STATE_MEMPOOL = "mempool"


@dataclass
class HeirShare:
    """One heir of an instalment: ``sats`` paid by the transaction and
    ``percent`` of everything the heirs receive (``None`` for an heir below
    the dust limit, who receives nothing)."""

    name: str
    sats: Optional[int]
    percent: Optional[float]


@dataclass
class Instalment:
    """One delivery date and the heirs paid on it (one per date; several
    only with multiverse on)."""

    locktime: int
    heirs: List[HeirShare] = field(default_factory=list)


@dataclass
class ExecutorMark:
    """A will-executor of the will in force and where it stands
    (``verified``, ``to_verify``, ``to_send``, ``to_sign``, ``failed``;
    the states of :func:`bal.core.summary.executor_states`)."""

    url: str
    state: str


@dataclass
class Future:
    """The data of the Future view (see the module docstring).

    ``state``: one of the ``STATE_*`` constants. ``title`` / ``note``:
    translated sentences (existing msgids of the will summary).
    ``deliveries``: the instalments of the will in force, earliest first
    (empty without one). ``level`` / ``level_tip``: the #124 colour of the
    first delivery. ``time_left``: "(in 4 months and 14 days)" or "(passed
    3 days ago)". ``check_alive``: the Check Alive date (``None`` when the
    will has none), ``check_alive_passed`` when it is past. ``period_months``:
    N of the period form (#127), ``None`` for a fixed date. ``executors``:
    the will-executors of the will in force. ``executors_text``: the
    summary's "3 of 3 received and verified it". ``draft_locktime``: the
    delivery of a draft not signed or sent yet, ``None`` without one.
    ``form``: the delivery form of the settings (#127, ``delivery.form_of``:
    ``"fixed"`` or ``"period"``; #132 shows it in the report and the view).
    """

    state: str
    now: float
    title: str = ""
    note: Optional[str] = None
    deliveries: List[Instalment] = field(default_factory=list)
    level: Optional[str] = None
    level_tip: str = ""
    time_left: str = ""
    check_alive: Optional[float] = None
    check_alive_passed: bool = False
    period_months: Optional[int] = None
    executors: List[ExecutorMark] = field(default_factory=list)
    executors_text: str = ""
    draft_locktime: Optional[int] = None
    form: str = _delivery.FORM_FIXED

    @property
    def delivery(self):
        """The first delivery date (UNIX time), or ``None``."""
        return self.deliveries[0].locktime if self.deliveries else None


def delivery_form_text(form, months=None):
    """``"Fixed date"`` or ``"Period (renewable) — 24 months (2 years)"`` (#132).

    The #127 msgids of the delivery form and ``messages.months_text``; a
    period form whose N cannot be read says only "Period (renewable)".
    """
    if form != _delivery.FORM_PERIOD:
        return _("Fixed date")
    text = _("Period (renewable)")
    return text + " — " + months_text(months) if months else text


def _now(now):
    """``now`` or the current UTC time (the clock of the rest of the core)."""
    return now if now is not None else datetime.now(tz=timezone.utc).timestamp()


def _days_text(days):
    """``"1 day"`` / ``"14 days"`` (existing msgids)."""
    return _("1 day") if days == 1 else _("{} days").format(days)


def _months_only(months):
    """``"1 month"`` / ``"4 months"`` (existing msgids)."""
    return _("1 month") if months == 1 else _("{} months").format(months)


def _and(first, second):
    """``"<first> and <second>"``.

    WHY the msgid "{years} and {months}": it is the only "A and B" joiner
    in the catalogs (IT "{years} e {months}", ES "{years} y {months}"), and
    the owner asked to reuse the existing month/year/day texts. Its
    placeholders are only names: "4 months and 14 days" reads right in the
    three languages.
    """
    return _("{years} and {months}").format(years=first, months=second)


def span_text(start, end):
    """The time from ``start`` to ``end`` in words, e.g. "4 months and 14 days".

    Calendar months (``delivery.months_between``, the month arithmetic of
    the delivery dates and of the #124 colours), then the days left over:
    "1 year and 2 months" (days dropped beyond a year: they would not help),
    "4 months and 14 days", "4 months", "12 days".
    """
    start, end = float(start), float(end)
    months = _delivery.months_between(start, end)
    years, rest = divmod(months, 12)
    if years:
        years_text = _("1 year") if years == 1 else _("{} years").format(years)
        return _and(years_text, _months_only(rest)) if rest else years_text
    if months:
        days = int(
            (_delivery.utc_midnight(end) - _delivery.add_months(start, months)) // _DAY
        )
        if days > 0:
            return _and(_months_only(months), _days_text(days))
        return _months_only(months)
    return _days_text(max(int((end - start) // _DAY), 0))


def time_left_text(ts, now):
    """``"(in 4 months and 14 days)"``, ``"(passed 3 days ago)"`` or ``"(today)"``.

    The bracketed msgids of the will summary (``summary.relative_date``),
    with :func:`span_text` inside.
    """
    if abs(ts - now) < _DAY:
        return _("(today)")
    if ts > now:
        return _("(in {})").format(span_text(now, ts))
    return _("(passed {} ago)").format(span_text(ts, now))


def _flag(item, name):
    """The status flag ``name`` of ``item`` (False when it has none)."""
    try:
        return bool(item.get_status(name))
    except (KeyError, AttributeError):
        return False


def _paid_by_address(item):
    """``{address: sats}`` of the outputs of ``item``'s transaction ({} on error)."""
    paid = {}
    try:
        for out in item.tx.outputs():
            if out.address:
                paid[out.address] = paid.get(out.address, 0) + int(out.value)
    except Exception:
        return {}
    return paid


def _instalment(item):
    """The :class:`Instalment` paid by ``item``: its date and its heirs.

    The amount of each heir is what the transaction pays to the heir's
    address (its outputs); when that cannot be read, or two heirs share an
    address, the resolved amount stored with the heir
    (``HEIR_REAL_AMOUNT``, the value ``prepare_transactions`` put in that
    output). Below the dust limit (stored as ``"DUST: n"``): no amount.
    OP_RETURN messages and the will-executor's fee are left out.
    """
    paid = _paid_by_address(item)
    rows = []
    for name, heir in (getattr(item, "heirs", None) or {}).items():
        if str(name).startswith(_EXECUTOR_HEIR):
            continue
        try:
            row = list(heir)
        except TypeError:
            continue
        address = str(row[HEIR_ADDRESS]) if row else ""
        if not address or is_op_return_address(address):
            continue
        row += [None] * 4
        try:
            stored = int(row[HEIR_REAL_AMOUNT])
        except (TypeError, ValueError):
            stored = None
        rows.append([str(name), address, stored])
    addresses = [address for _n, address, _s in rows]
    heirs = []
    for name, address, stored in sorted(rows):
        sats = stored
        if stored is not None and addresses.count(address) == 1 and address in paid:
            sats = paid[address]
        heirs.append(HeirShare(name=name, sats=sats, percent=None))
    try:
        locktime = int(item.tx.locktime)
    except Exception:
        locktime = 0
    return Instalment(locktime=locktime, heirs=heirs)


def _chain(version, will):
    """One transaction per instalment of ``version``, earliest first.

    The first root (a will-executor's copy before the backup: roots are
    sorted that way by ``will_versions``), then its first child, and so on
    (multiverse: each instalment spends the change of the one before; every
    copy of an instalment pays the same heirs).
    """
    chain = []
    if not version.roots:
        return chain
    wid = version.roots[0]
    seen = set()
    while wid is not None and wid not in seen and wid in will:
        seen.add(wid)
        chain.append(will[wid])
        kids = version.children.get(wid) or []
        wid = kids[0] if kids else None
    return chain


def _deliveries(version, will):
    """The :class:`Instalment` list of ``version`` with the REAL shares.

    Instalments with the same date are merged; each heir's ``percent`` is
    its amount over the amount paid to all heirs of every instalment.
    """
    by_date = {}
    for item in _chain(version, will):
        inst = _instalment(item)
        if inst.locktime in by_date:
            by_date[inst.locktime].heirs.extend(inst.heirs)
        else:
            by_date[inst.locktime] = inst
    out = [by_date[lt] for lt in sorted(by_date)]
    total = sum(h.sats for inst in out for h in inst.heirs if h.sats)
    for inst in out:
        for h in inst.heirs:
            if h.sats and total:
                h.percent = 100.0 * h.sats / total
    return out


def _draft_version(history):
    """The newest version not signed or sent yet (a draft or partly signed), or None."""
    drafts = [
        v for v in history.versions
        if v.state in (will_versions.VERSION_DRAFT, will_versions.VERSION_PARTIALLY_SIGNED)
    ]
    return max(drafts, key=lambda v: v.order) if drafts else None


def _check_alive(future, version, will_settings, is_basic_mode, now):
    """Fill the Check Alive date of ``future`` as the will summary does.

    BASIC mode with a fixed date has none (``check_alive_basic``); without
    a stored ``threshold`` neither. A passed Check Alive keeps the date
    that has passed (``check_alive_renewal``).
    """
    if check_alive_basic(is_basic_mode, will_settings):
        return
    if will_settings.get("threshold") is None:
        return
    creation = version.time_min
    renewal = check_alive_renewal(
        False, will_settings, built_locktime=future.delivery,
        creation_date=creation, now=now,
    )
    if renewal:
        future.check_alive = float(renewal["check_date"])
        future.check_alive_passed = True
        return
    future.check_alive = float(resolve_date_to_check(
        False, will_settings, now=now,
        built_locktime=future.delivery, creation_date=creation,
    ))


def build_future(willitems, will_settings, is_basic_mode, now=None,
                 warn_months=None, wallet=None, synced=None, history=None):
    """The data of the Future view (a :class:`Future`).

    Args:
        willitems: ``{txid: WillItem}`` (the window's ``willitems``).
        will_settings: the wallet's delivery settings (``locktime``,
            ``threshold``, ``form``, ``period_months``).
        is_basic_mode: True in BASIC mode.
        now: overridable clock for tests.
        warn_months: ``(yellow, red)`` of the #124 colours
            (``BalPlugin.warning_months``); None: the defaults.
        wallet, synced: passed to ``will_versions.build_history`` (the
            chain decides which sent will is still executable).
        history: a ``will_versions.History`` already built, to reuse.

    Raises whatever the data raises: the GUI catches it, logs it and shows
    a neutral view (see ``bal/gui/qt/future_view.py``).
    """
    now = _now(now)
    will = dict(willitems or {})
    settings = will_settings or {}
    if history is None:
        history = will_versions.build_history(will, wallet, synced)
    current = history.by_vid().get(history.current) if history.current else None
    draft = _draft_version(history)
    future = Future(state=STATE_NONE, now=now)
    if draft is not None and draft.locktime:
        future.draft_locktime = int(draft.locktime)

    if current is None:
        if any(_flag(w, "CONFIRMED") for w in will.values()):
            future.state = STATE_EXECUTED
            future.title = _("Your will has been executed")
        elif any(_flag(w, "MEMPOOL") for w in will.values()):
            future.state = STATE_MEMPOOL
            future.title = _("Your will is being executed (mempool)")
        elif draft is not None:
            future.state = STATE_DRAFT_ONLY
            future.title = _("No will in force")
            future.note = _("Will prepared: it must be signed and sent")
        else:
            future.state = STATE_NONE
            future.title = _("No will in force")
            future.note = _("Until you create your will, nobody will receive your bitcoin.")
        return future

    future.state = STATE_IN_FORCE
    future.deliveries = _deliveries(current, will)
    if not future.deliveries:
        future.deliveries = [Instalment(locktime=int(current.locktime or 0))]
    first = future.delivery
    future.level, future.level_tip = delivery_warning(first, now, warn_months)
    future.time_left = time_left_text(first, now)
    if first <= now:
        future.title = _("The delivery date has passed")
    else:
        future.title = _("Your will is in force")
    _check_alive(future, current, settings, is_basic_mode, now)
    future.period_months = _delivery.period_months(settings)
    future.form = _delivery.form_of(settings)

    items = {wid: will[wid] for wid in current.items if wid in will}
    states = executor_states(items)
    future.executors = [ExecutorMark(url=url, state=states[url]) for url in sorted(states)]
    row = _executors_row(executor_counts(items))
    future.executors_text = row["text"] + (" · " + row["alarm"] if row["alarm"] else "")
    if draft is not None and draft.vid != current.vid:
        # The same sentence as the will summary (#84b): a newer will is
        # prepared but the executors still hold this one.
        future.note = _(
            "Until you sign and send it, the will sent before stays in force: "
            "delivery on {}."
        ).format(_day(first))
    return future
