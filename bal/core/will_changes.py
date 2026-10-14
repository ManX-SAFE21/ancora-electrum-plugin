"""What changes compared with the will in force (CHANGELOG #135).

Before signing a new will (the Check window, at the signature) and on the
last page of the BASIC wizard, the owner sees ONLY what changes compared
with the will the will-executors hold: the delivery date, the heirs added,
removed or renamed, the real shares, the will-executors, the miner fee, the
OP_RETURN message. An identical will says so in one line; with no will in
force there is nothing to compare (no box).

Two steps, both pure (no Qt, no clock, nothing stored):

* the two sides are described as :class:`WillSide` from the will items
  (:func:`side_from_items`: the will in force, or the will just prepared)
  or from the settings (:func:`side_from_settings`: the wizard, before
  anything is built);
* :func:`will_changes` compares them and returns :class:`Change` objects;
  :func:`change_rows` writes the rows (``bal.core.datetext`` dates, the
  catalogs' texts).

Heirs are matched by ADDRESS, never by name: the same address with another
name is a renamed heir, not one removed and one added. The real shares are
what each heir really receives: the amounts the transactions pay (from the
items) or, before the build, the written percentages in proportion (each
over their total: ``Heirs.normalize_perc`` spends the whole balance), never
invented when fixed amounts are mixed in.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ..i18n import _, number_separators
from . import datetext, delivery
from .heirs import (
    HEIR_ADDRESS,
    HEIR_AMOUNT,
    HEIR_REAL_AMOUNT,
    OP_RETURN_PREFIX,
    is_op_return_address,
)
from .summary import executor_states

# The reserved name prefix of the will-executor fee outputs (not a person).
_EXECUTOR_HEIR = 'w!ll3x3c"'

#: Kinds of change, in the order of the rows.
DELIVERY = "delivery"
HEIRS = "heirs"
SHARES = "shares"
MESSAGE = "message"
EXECUTORS = "executors"
FEE = "fee"
NONE = "none"


@dataclass
class WillSide:
    """One side of the comparison.

    ``delivery``: the delivery date (UTC timestamp) or None; ``heirs``:
    ``{address: name}`` (OP_RETURN rows left out); ``shares``: ``{address:
    percent}`` of what each heir really receives, None when it cannot be
    known (fixed amounts before the build); ``written_total``: the sum of
    the written percentages (None with fixed amounts); ``messages``: the
    OP_RETURN texts; ``executors``: the will-executor URLs; ``fee``: the
    miner fee rate (sat/vB) or None.
    """

    delivery: Optional[int] = None
    heirs: Dict[str, str] = field(default_factory=dict)
    shares: Optional[Dict[str, float]] = None
    written_total: Optional[float] = None
    messages: List[str] = field(default_factory=list)
    executors: List[str] = field(default_factory=list)
    fee: Optional[float] = None
    # #135: every heir name at each address ({address: [names]}): two names
    # on one address are two heirs paid there, never one heir renamed.
    all_names: Dict[str, List[str]] = field(default_factory=dict)


@dataclass
class Change:
    """One row of the box: ``kind`` (see the module constants) and its data."""

    kind: str
    old: object = None
    new: object = None
    added: list = field(default_factory=list)
    removed: list = field(default_factory=list)
    renamed: list = field(default_factory=list)
    names: Dict[str, str] = field(default_factory=dict)
    # #135 (owner): heirs only renamed by this Check (the names are already
    # written in the will before the comparison, ``Will.sync_heir_names``).
    names_only: list = field(default_factory=list)
    # #135: ``[(name, other)]``: an heir added at the address of another.
    same_address: list = field(default_factory=list)


def _message_of(address):
    """The text of an ``OP_RETURN:<hex>`` address ("" when unreadable)."""
    try:
        return bytes.fromhex(str(address)[len(OP_RETURN_PREFIX):]).decode(
            "utf-8", errors="replace")
    except ValueError:
        return ""


def _percent(text):
    """``"60%"`` -> 60.0; None for anything else (a fixed amount)."""
    text = str(text).strip()
    if not text.endswith("%"):
        return None
    try:
        return float(text[:-1])
    except ValueError:
        return None


def _written(heirs):
    """``(shares, total)`` from the written amounts of ``{name: [address,
    amount, ...]}``: each percentage over their total (what the build pays,
    ``Heirs.normalize_perc``); ``(None, None)`` when a fixed amount is mixed
    in (the real shares depend on the balance: never invented)."""
    values = {}
    for value in heirs.values():
        address = str(value[HEIR_ADDRESS])
        if is_op_return_address(address):
            continue
        pct = _percent(value[HEIR_AMOUNT])
        if pct is None:
            return None, None
        values[address] = values.get(address, 0.0) + pct
    total = sum(values.values())
    if not total:
        return None, None
    return {a: 100.0 * v / total for a, v in values.items()}, total


def _heir_maps(heirs):
    """``({address: name}, [messages])`` of ``{name: [address, ...]}``,
    the will-executor fee outputs left out. Several names on one address
    (#135) are written "a + b" (one display name for the shares row; the
    comparison reads :func:`_all_names`)."""
    names, messages = {}, []
    for address, listed in _all_names(heirs).items():
        names[address] = " + ".join(listed)
    for name, value in (heirs or {}).items():
        if str(name).startswith(_EXECUTOR_HEIR):
            continue
        address = str(value[HEIR_ADDRESS])
        if is_op_return_address(address):
            messages.append(_message_of(address))
    return names, sorted(messages)


def _all_names(heirs):
    """``{address: [names]}`` of ``{name: [address, ...]}`` (sorted names;
    no will-executor fee outputs, no OP_RETURN rows)."""
    out = {}
    for name, value in (heirs or {}).items():
        if str(name).startswith(_EXECUTOR_HEIR):
            continue
        address = str(value[HEIR_ADDRESS])
        if not is_op_return_address(address):
            out.setdefault(address, []).append(str(name))
    return {a: sorted(n) for a, n in out.items()}


def _date_of(item):
    """The date nLockTime of ``item`` (None for a block height or garbage)."""
    try:
        return delivery.as_delivery(int(item.tx.locktime))
    except Exception:
        return None


def side_from_items(items, selected=None):
    """The :class:`WillSide` of the will items ``items`` (``{wid: item}``).

    ``items``: the copies of ONE will (the will in force, or the will just
    prepared). The heirs and the real amounts come from the first copy that
    has them (every copy pays the same heirs); the will-executors are the
    URLs of the copies, without those deselected now that never received
    the will (``summary.executor_states`` with ``selected``, the #134 rule);
    the delivery is the earliest date. None for no items.
    """
    if not items:
        return None
    side = WillSide()
    dates = [d for d in (_date_of(w) for w in items.values()) if d is not None]
    side.delivery = min(dates) if dates else None
    # The first delivery only (with multiverse each instalment has its own
    # heirs and date; the box compares the first one, the will's date).
    first_items = {wid: w for wid, w in items.items()
                   if side.delivery is None or _date_of(w) == side.delivery}
    first = next((w for w in first_items.values() if w.heirs), None)
    if first is not None:
        side.heirs, side.messages = _heir_maps(first.heirs)
        side.all_names = _all_names(first.heirs)
        real = {}
        for name, value in first.heirs.items():
            if str(name).startswith(_EXECUTOR_HEIR):
                continue
            address = str(value[HEIR_ADDRESS])
            if is_op_return_address(address):
                continue
            try:
                real[address] = real.get(address, 0) + int(value[HEIR_REAL_AMOUNT])
            except (IndexError, TypeError, ValueError):
                real = None
                break
        total = sum(real.values()) if real else 0
        side.shares = ({a: 100.0 * v / total for a, v in real.items()}
                       if total else None)
        _shares, side.written_total = _written(
            {n: v for n, v in first.heirs.items()
             if not str(n).startswith(_EXECUTOR_HEIR)})
        try:
            side.fee = float(first.tx_fees)
        except (TypeError, ValueError):
            side.fee = None
    states = executor_states(first_items, selected)
    side.executors = sorted(u for u, st in states.items() if st != "deselected")
    return side


def side_from_settings(heirs, locktime, executors, fee):
    """The :class:`WillSide` the settings would give (the wizard, #135).

    ``heirs``: the Heirs tab (``{name: [address, amount, date]}``);
    ``locktime``: the chosen delivery; ``executors``: the URLs selected now;
    ``fee``: the miner fee rate of the settings. The real shares are the
    written percentages in proportion (None with fixed amounts).
    """
    side = WillSide(delivery=delivery.as_delivery(locktime))
    side.heirs, side.messages = _heir_maps(heirs)
    side.all_names = _all_names(heirs)
    side.shares, side.written_total = _written(heirs or {})
    side.executors = sorted(executors or [])
    try:
        side.fee = float(fee) if fee is not None else None
    except (TypeError, ValueError):
        side.fee = None
    return side


def _same_share(a, b):
    """True when two percentages read the same with one decimal."""
    return round(a, 1) == round(b, 1)


def will_changes(old, new):
    """The changes from the will in force ``old`` to ``new``.

    Both :class:`WillSide`. ``old`` None (no will in force): None, there is
    no box. Nothing changed: ``[Change(NONE)]``. Otherwise one
    :class:`Change` per kind of change, in the order of the rows.
    """
    if old is None or new is None:
        return None
    out = []
    if old.delivery != new.delivery and old.delivery and new.delivery:
        out.append(Change(DELIVERY, old.delivery, new.delivery))
    heirs = _heirs_change(old, new)
    if heirs is not None:
        out.append(heirs)
    if old.shares is not None and new.shares is not None:
        moved = [(new.heirs.get(a, old.heirs.get(a, a)), old.shares[a], new.shares[a])
                 for a in new.shares
                 if a in old.shares and not _same_share(old.shares[a], new.shares[a])]
        if moved:
            out.append(Change(SHARES, new=new.written_total, added=moved))
    if old.messages != new.messages:
        out.append(Change(MESSAGE, old.messages, new.messages))
    gained = [u for u in new.executors if u not in old.executors]
    lost = [u for u in old.executors if u not in new.executors]
    if gained or lost:
        out.append(Change(EXECUTORS, len(old.executors), len(new.executors),
                          added=gained, removed=lost))
    if (old.fee is not None and new.fee is not None
            and int(old.fee) != int(new.fee)):
        out.append(Change(FEE, old.fee, new.fee))
    return out or [Change(NONE)]


def name_only_text(old, new, saved=True):
    """"renamed: luigi → <b>luigi new</b> (only the name changes, already
    saved: no need to sign)" (#135, owner).

    The Check writes a pure rename in the wallet at once
    (``Will.sync_heir_names``): names are not in the transactions nor sent
    to the will-executors, so nothing is signed for it and a Cancel at the
    password does not undo it. The one wording of a rename row.

    ``saved`` False: the BASIC wizard's summary, before "Create the will"
    (the Check then saves the name): "(only the name changes: no need to
    sign)", without "already saved" (owner, #135).
    """
    if not saved:
        return _("renamed: {old} → {new} (only the name changes: no need to "
                 "sign)").format(old=old, new="<b>{}</b>".format(new))
    return _("renamed: {old} → {new} (only the name changes, already saved: "
             "no need to sign)").format(old=old, new="<b>{}</b>".format(new))


def has_renames(changes):
    """True when ``changes`` holds heirs only renamed (already saved)."""
    return any(c.kind == HEIRS and c.names_only for c in (changes or []))


def with_renames(changes, renames):
    """``changes`` plus the heirs only renamed by this Check (#135).

    Owner: ONE box holds every change, the rename too. The Check writes the
    new name in the will before the comparison (``Will.sync_heir_names``),
    so the comparison no longer sees it: ``renames`` (``[(old, new)]``,
    ``host.heir_renames``) is added to the Heirs row ("renamed: luigi →
    luigi new (only the name changes)"), created after the delivery row
    when there is none; "No change" goes away. ``changes`` None (no will
    in force) stays None; no renames: ``changes`` unchanged.
    """
    if changes is None or not renames:
        return changes
    out = [c for c in changes if c.kind != NONE]
    heirs = next((c for c in out if c.kind == HEIRS), None)
    if heirs is None:
        heirs = Change(HEIRS)
        at = 1 if out and out[0].kind == DELIVERY else 0
        out.insert(at, heirs)
    heirs.names_only = list(renames)
    return out


def _heirs_change(old, new):
    """The HEIRS :class:`Change` from ``old`` to ``new``, or None.

    Matched by ADDRESS. One name on an address on both sides, another
    name: renamed. Several names on one address are several heirs paid
    there (#135, owner's ZIP test: the Edit dialog kept the old name and
    added the new one, and the box said "renamed" while a second heir took
    another share): a name new at an address that already has one is
    "added X (same address as Y)", never "renamed"; a name gone is
    "removed".
    """
    old_names = old.all_names or {a: [n] for a, n in old.heirs.items()}
    new_names = new.all_names or {a: [n] for a, n in new.heirs.items()}
    added, removed, renamed, same = [], [], [], []
    for address, names in new_names.items():
        before = old_names.get(address)
        if not before:
            added.append(names[0])
            same.extend((n, names[0]) for n in names[1:])
            continue
        if len(before) == 1 and len(names) == 1:
            if before[0] != names[0]:
                renamed.append((before[0], names[0]))
            continue
        for n in names:
            if n not in before:
                same.append((n, next(b for b in names if b != n)))
        removed.extend(n for n in before if n not in names)
    for address, names in old_names.items():
        if address not in new_names:
            removed.extend(names)
    if not (added or removed or renamed or same):
        return None
    return Change(HEIRS, added=added, removed=removed, renamed=renamed,
                  same_address=same)


# ----------------------------------------------------------------- texts

def _pct(value):
    """``38.5%`` with the language's decimal comma (``38,5%``); a whole
    number without decimals (``60%``)."""
    decimal, _thousands = number_separators()
    text = "{:.1f}".format(value)
    if text.endswith(".0"):
        text = text[:-2]
    return (text + "%").replace(".", decimal)


def _host(url):
    """The host of a will-executor URL (``we.example.org``)."""
    from urllib.parse import urlparse

    return urlparse(url).hostname or url


def _fee(value):
    """A fee rate without a useless ``.0``."""
    return "{:g}".format(float(value))


def _quoted(text):
    """``“text”``."""
    return "“{}”".format(text)


def _names(names):
    """``<b>a</b>, <b>b</b>``."""
    return ", ".join("<b>{}</b>".format(n) for n in names)


def _added(names):
    """"added <b>carol</b>" (one) / "added <b>a</b>, <b>b</b>" (several:
    another msgid, so Italian and Spanish can say "aggiunto" / "aggiunti")."""
    if len(names) == 1:
        return _("added {}").format(_names(names))
    return _("added {list}").format(list=_names(names))


def _removed(names):
    """"removed <b>dave</b>" (one) / several (see :func:`_added`)."""
    if len(names) == 1:
        return _("removed {}").format(_names(names))
    return _("removed {list}").format(list=_names(names))


def change_rows(changes, wizard=False):
    """``[(label, value, note)]`` of ``changes`` (rich text values; ``note``
    a muted line or None). None for no changes list (no will in force).

    ``wizard`` (#135): the BASIC wizard's summary, where an heir renamed
    (same address, one name each side) is a name-only change the Check will
    save without signing: :func:`name_only_text` with ``saved=False``.
    """
    if changes is None:
        return None
    rows = []
    for c in changes:
        if c.kind == NONE:
            rows.append((_("No change"),
                         _("the will stays the same as the one in force"), None))
        elif c.kind == DELIVERY:
            note = datetext.delivery_change(c.old, c.new)
            if c.new < c.old:
                note = _("{} — free").format(note)
            rows.append((_("Delivery"), "{} → <b>{}</b>".format(
                datetext.utc_text(c.old), datetext.utc_text(c.new)), note))
        elif c.kind == HEIRS:
            parts = []
            if c.added:
                parts.append(_added(c.added))
            if c.removed:
                parts.append(_removed(c.removed))
            for name, other in c.same_address:
                parts.append(_("added {name} (same address as {other})").format(
                    name="<b>{}</b>".format(name), other=other))
            for old, new in c.renamed:
                parts.append(name_only_text(old, new, saved=False) if wizard
                             else _("renamed: {old} → {new}").format(
                                 old=old, new="<b>{}</b>".format(new)))
            for old, new in c.names_only:
                parts.append(name_only_text(old, new))
            rows.append((_("Heirs"), " · ".join(parts), None))
        elif c.kind == SHARES:
            value = " · ".join("{} {} → <b>{}</b>".format(name, _pct(a), _pct(b))
                               for name, a, b in c.added)
            note = None
            if c.new is not None and abs(c.new - 100.0) > 1e-9:
                # #135 (owner): the shares said once: the total here, and
                # no separate "How the shares are divided" box.
                note = _("The percentages you wrote add up to {}: the will "
                         "always spends the whole balance, so the shares are "
                         "recalculated in proportion.").format(_pct(c.new))
            rows.append((_("Real shares"), value, note))
        elif c.kind == MESSAGE:
            if c.old and c.new:
                value = _("changed: {old} → {new}").format(
                    old=_quoted(" / ".join(c.old)),
                    new="<b>{}</b>".format(_quoted(" / ".join(c.new))))
            elif c.new:
                value = _("added: {}").format(
                    "<b>{}</b>".format(_quoted(" / ".join(c.new))))
            else:
                value = _("removed: {}").format(_quoted(" / ".join(c.old)))
            rows.append((_("Message"), value, None))
        elif c.kind == EXECUTORS:
            value = _("from {old} to {new}").format(old=c.old, new="<b>{}</b>".format(c.new)) \
                if c.old != c.new else ""
            parts = []
            if c.added:
                parts.append(_added([_host(u) for u in c.added]))
            if c.removed:
                parts.append(_removed([_host(u) for u in c.removed]))
            detail = "; ".join(parts)
            value = "{}: {}".format(value, detail) if value and detail else (value or detail)
            rows.append((_("Will-executors"), value, None))
        elif c.kind == FEE:
            rows.append((_("Miner fee"), "{} → <b>{} sat/vB</b>".format(
                _fee(c.old), _fee(c.new)), None))
    return rows


def real_shares_text(heirs, format_amount=None):
    """The wizard's "Heirs" value with the REAL shares (#135, owner).

    ``"alice 38.5% · bob 23.1% · carol 38.5%"``: each written percentage in
    proportion to their total (``Heirs.normalize_perc``), and ``True`` as
    second value when the written total is not 100 (the page adds the
    "recalculated in proportion" note). None when a fixed amount is mixed
    in: the real shares depend on the balance, the page keeps its own text.
    """
    shares, total = _written(heirs or {})
    if shares is None:
        return None
    parts = []
    for name in sorted(heirs):
        value = heirs[name]
        address = str(value[HEIR_ADDRESS])
        if is_op_return_address(address):
            parts.append("{} (OP_RETURN)".format(name))
            continue
        pct = _percent(value[HEIR_AMOUNT])
        parts.append("{} {}".format(name, _pct(100.0 * pct / total)))
    return " · ".join(parts), abs(total - 100.0) > 1e-9
