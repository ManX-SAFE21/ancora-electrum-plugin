"""What changes compared with the will in force (CHANGELOG #135).

Before signing a new will (the Check window, at the signature) and on the
last page of the BASIC wizard, the owner sees ONLY what changes compared
with the will the will-executors hold: the delivery date, the heirs added,
removed or renamed, the real shares, the will-executors, the miner fee, the
OP_RETURN message. An identical will says so in one line; with no will in
force there is nothing to compare (no box). A will in several deliveries
(#185) is compared delivery by delivery, with the shares of the whole
estate.

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

import html
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
EXECUTOR_TERMS = "executor_terms"
FEE = "fee"
NONE = "none"
# #185: a will in several deliveries on one side at least: how many
# deliveries (when that changes), then one row per delivery that changed.
DELIVERY_COUNT = "delivery_count"
INSTALMENT = "instalment"


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
    # #141: the payment data of each will-executor's copy ({url: (fee
    # address, fee, received)}); empty when not known (the settings).
    terms: Dict[str, tuple] = field(default_factory=dict)
    # #185: a will in several deliveries: ``[(date, {address: percent})]``,
    # one entry per delivery in order, the percent a share of the WHOLE
    # estate (the table's cells); None for a will with one delivery.
    deliveries: Optional[list] = None


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
    # #185: the delivery number of an INSTALMENT row (1, 2, 3).
    number: int = 0


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
    side.terms = _terms(first_items)
    side.deliveries = _item_deliveries(items, side.delivery, first)
    return side


def _delivery_number(item):
    """The delivery of ``item`` (1, 2, 3) from its heir rows ("Luca§2" is
    delivery 2, #168); 1 for an item without person rows."""
    for name in (getattr(item, "heirs", None) or {}):
        if not str(name).startswith(_EXECUTOR_HEIR):
            return delivery.instalment_number(name)
    return 1


def _written_shares(heirs):
    """``{address: percent}`` of the person rows of ``heirs`` as written
    (with a table each is a share of the whole estate, #168); None when a
    row is not a percentage."""
    out = {}
    for name, value in (heirs or {}).items():
        if str(name).startswith(_EXECUTOR_HEIR):
            continue
        address = str(value[HEIR_ADDRESS])
        if is_op_return_address(address):
            continue
        pct = _percent(value[HEIR_AMOUNT])
        if pct is None:
            return None
        out[address] = out.get(address, 0.0) + pct
    return out


def _item_deliveries(items, first_date, first):
    """:attr:`WillSide.deliveries` of the will items ``items`` (#185).

    Each item pays the rows of ONE delivery ("Luca", "Luca§2"...), each
    written as a share of the whole estate (seen in a flowkit will in
    three deliveries: delivery 1 "luca 10%", delivery 3 "luca§3 30%").
    Delivery 1 is the side's own (``first_date`` and ``first``: its date
    and the copy read); a later one is read from its first copy in
    ``items`` (the drafts come first in the prepared will,
    ``engine.context.prepared_items``), with that copy's date. None for a
    will with one delivery, or when a delivery is missing (nothing
    invented).
    """
    picked = {}
    for item in items.values():
        n = _delivery_number(item)
        if n >= 2 and n not in picked and item.heirs:
            picked[n] = item
    if not picked or first is None:
        return None
    out = [(first_date, _written_shares(first.heirs))]
    for n in range(2, max(picked) + 1):
        item = picked.get(n)
        if item is None:
            return None
        out.append((_date_of(item), _written_shares(item.heirs)))
    return out


def _table_deliveries(heirs, table, first_date):
    """:attr:`WillSide.deliveries` of a delivery table (#185).

    ``table``: ``delivery.table_rows`` of the settings; each row's date
    (delivery 1 on ``first_date``, the wallet's date) and each person's
    cell at the person's address (0 % left out). None without a table of
    at least two rows.
    """
    if not table or len(table) < 2:
        return None
    out = []
    for n, row in enumerate(table, 1):
        row = row if isinstance(row, dict) else {}
        shares = {}
        for name, cell in (row.get("shares") or {}).items():
            value = (heirs or {}).get(name)
            share = delivery.as_share(cell)
            if not value or not share:
                continue
            address = str(value[HEIR_ADDRESS])
            if is_op_return_address(address):
                continue
            shares[address] = shares.get(address, 0.0) + float(share)
        date = first_date if n == 1 else delivery.as_delivery(row.get("locktime"))
        out.append((date, shares))
    return out


def settings_table(settings):
    """The delivery table of the wallet's settings for
    :func:`side_from_settings` (#185): ``delivery.table_rows``, or None
    when there is none or it cannot be read (then the heirs' amounts are
    compared, as before)."""
    try:
        return delivery.table_rows(settings or {}) or None
    except ValueError:
        return None


def _terms(items):
    """``{url: (fee address, fee, received)}`` of the copies ``items`` (#141).

    ``received``: every copy for that will-executor was received
    (``Will.received``). A copy REPLACED by another one for the same
    will-executor is left out: the prepared will holds the old copies in
    force next to the drafts that redo them
    (``engine.context.prepared_items``). A will-executor whose remaining
    copies disagree is left out.
    """
    from .will import Will

    by_url = {}
    for w in items.values():
        url = (w.we or {}).get("url")
        if url:
            by_url.setdefault(url, []).append(w)
    terms = {}
    for url, copies in by_url.items():
        current = [w for w in copies if not w.get_status("REPLACED")] or copies
        data = {(w.we.get("address"), w.we.get("base_fee")) for w in current}
        if len(data) == 1:
            terms[url] = data.pop() + (all(Will.received(w) for w in current),)
    return terms

def side_from_settings(heirs, locktime, executors, fee, table=None):
    """The :class:`WillSide` the settings would give (the wizard, #135).

    ``heirs``: the Heirs tab (``{name: [address, amount, date]}``);
    ``locktime``: the chosen delivery; ``executors``: the URLs selected now;
    ``fee``: the miner fee rate of the settings. The real shares are the
    written percentages in proportion (None with fixed amounts).
    ``table`` (#185, :func:`settings_table`): the wallet's delivery table.
    The heirs' amounts are then not used: the shares are the table's,
    delivery by delivery (:attr:`WillSide.deliveries`).
    """
    side = WillSide(delivery=delivery.as_delivery(locktime))
    side.heirs, side.messages = _heir_maps(heirs)
    side.all_names = _all_names(heirs)
    side.deliveries = _table_deliveries(heirs, table, side.delivery)
    if side.deliveries is None:
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


def _as_deliveries(side):
    """``[(date, {address: percent})]`` of ``side``: its deliveries, or its
    one delivery with the real shares (the whole estate at one date)."""
    if side.deliveries is not None:
        return side.deliveries
    return [(side.delivery, side.shares)]


def _same_shares(a, b):
    """True when two ``{address: percent}`` read the same with one decimal
    (a share of 0 % counts as absent; None, unknown, equals only None)."""
    if a is None or b is None:
        return a is b
    keys = set(a) | set(b)
    return all(_same_share(a.get(k, 0.0), b.get(k, 0.0)) for k in keys)


def _instalment_changes(old, new):
    """The rows of a will in several deliveries on one side at least (#185).

    Owner (preview approved, 2026-10-23): "Deliveries: one delivery → 3
    deliveries" when the number changes, then one INSTALMENT row per
    delivery whose date or shares changed (``old`` / ``new`` of the row:
    ``(date, {address: percent})``, None for a delivery missing on that
    side). The shares are of the whole estate: before, delivery 1 alone was
    compared, its shares recalculated inside it (10 % + 10 % read 50 % +
    50 %, the owner's "33% → 50%").
    """
    was, now = _as_deliveries(old), _as_deliveries(new)
    names = dict(old.heirs)
    names.update(new.heirs)
    # The heirs no longer in the will: the Heirs row says "removed", their
    # shares are not repeated as "→ 0%".
    gone = [a for a in old.heirs if a not in new.heirs]
    out = []
    if len(was) != len(now):
        out.append(Change(DELIVERY_COUNT, len(was), len(now)))
    for i in range(max(len(was), len(now))):
        a = was[i] if i < len(was) else None
        b = now[i] if i < len(now) else None
        if a is not None and b is not None and a[0] == b[0] and _same_shares(a[1], b[1]):
            continue
        out.append(Change(INSTALMENT, a, b, names=names, removed=gone,
                          number=i + 1))
    return out


def will_changes(old, new, staged=True):
    """The changes from the will in force ``old`` to ``new``.

    Both :class:`WillSide`. ``old`` None (no will in force): None, there is
    no box. Nothing changed: ``[Change(NONE)]``. Otherwise one
    :class:`Change` per kind of change, in the order of the rows.

    #185: when a side is a will in several deliveries, the date and the
    shares are compared delivery by delivery (:func:`_instalment_changes`)
    instead of the DELIVERY and SHARES rows. ``staged`` False keeps the
    comparison of delivery 1 alone: ``engine.context.cancelled_postpone_date``
    compares the first delivery on purpose.
    """
    if old is None or new is None:
        return None
    out = []
    instalments = staged and (old.deliveries is not None
                              or new.deliveries is not None)
    if instalments:
        out.extend(_instalment_changes(old, new))
    elif old.delivery != new.delivery and old.delivery and new.delivery:
        out.append(Change(DELIVERY, old.delivery, new.delivery))
    heirs = _heirs_change(old, new)
    if heirs is not None:
        out.append(heirs)
    if not instalments and old.shares is not None and new.shares is not None:
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
    terms = []
    for url in new.executors:
        if url in old.executors and url in old.terms and url in new.terms:
            was, now = old.terms[url], new.terms[url]
            if was[0] != now[0]:
                terms.append((url, "address", was[2]))
            elif was[1] != now[1]:
                terms.append((url, "fee", was[2]))
    if terms:
        out.append(Change(EXECUTOR_TERMS, added=terms))
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
        # After the delivery rows (#185: a will in several deliveries has
        # several of them).
        at = 0
        while at < len(out) and out[at].kind in (DELIVERY, DELIVERY_COUNT, INSTALMENT):
            at += 1
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


def _added_executors(urls, new_in_network=()):
    """"added <b>we2.test</b> (new in the network)" (#137): the hosts of
    ``urls``, the ones in ``new_in_network`` marked as new on the network
    (selected automatically, owner)."""
    names = []
    for url in urls:
        name = "<b>{}</b>".format(_host(url))
        if url in (new_in_network or ()):
            name += " " + _("(new in the network)")
        names.append(name)
    if len(names) == 1:
        return _("added {}").format(names[0])
    return _("added {list}").format(list=", ".join(names))


def _removed(names):
    """"removed <b>dave</b>" (one) / several (see :func:`_added`)."""
    if len(names) == 1:
        return _("removed {}").format(_names(names))
    return _("removed {list}").format(list=_names(names))


def _count_text(n):
    """"one delivery" / "3 deliveries" (#185)."""
    if n == 1:
        return _("one delivery")
    return _("{} deliveries").format(n)


def _day(ts):
    """The short UTC day of a delivery ("10 Mar 2030"); "?" unknown."""
    return datetext.utc_day(ts, short=True) if ts is not None else "?"


def _instalment_value(c):
    """The value of an INSTALMENT row (#185, owner's preview).

    "10 Mar 2030: luca 50% → <b>10%</b> · john 50% → <b>10%</b>"; a new
    delivery "10 Mar 2032 (new): luca <b>10%</b> · john <b>10%</b>"; a
    delivery taken away "removed (it was 10 Mar 2035: luca 30% · john
    30%)". A
    date moved reads "old → <b>new</b>". A share that is not on the old
    side (an heir added, or fixed amounts before) is written only with its
    new value; an heir no longer in the will (``c.removed``) is left out:
    the Heirs row says "removed".
    """
    old, new = c.old, c.new
    if new is None:
        shares = " · ".join("{} {}".format(c.names.get(a, a), _pct(p))
                            for a, p in (old[1] or {}).items() if round(p, 1))
        text = _day(old[0]) + (": " + shares if shares else "")
        # Its own text: "removed: {}" (the message row) is masculine in
        # Italian, a delivery is feminine ("tolta").
        return _("removed (it was {})").format(text)
    if old is None:
        day = _("{} (new)").format(_day(new[0]))
    elif old[0] != new[0]:
        day = "{} → <b>{}</b>".format(_day(old[0]), _day(new[0]))
    else:
        day = _day(new[0])
    was = old[1] if old is not None else None
    now = new[1] or {}
    parts = []
    for address in list(now) + [a for a in (was or {}) if a not in now]:
        name = c.names.get(address, address)
        b = now.get(address, 0.0)
        a = (was or {}).get(address, 0.0)
        if address in c.removed and not round(b, 1):
            continue
        if was is None or address not in was:
            if round(b, 1):
                parts.append("{} <b>{}</b>".format(name, _pct(b)))
        elif _same_share(a, b):
            if round(b, 1):
                parts.append("{} {}".format(name, _pct(b)))
        else:
            parts.append("{} {} → <b>{}</b>".format(name, _pct(a), _pct(b)))
    return day + (": " + " · ".join(parts) if parts else "")


def _instalment_rows(changes):
    """The rows of DELIVERY_COUNT and INSTALMENT changes (#185); the note
    of the last one says what the percentages are; a delivery 1 moved has
    the note of the delivery row (earlier, later: invalidation)."""
    rows = []
    for c in changes:
        if c.kind == DELIVERY_COUNT:
            rows.append((_("Deliveries"), "{} → <b>{}</b>".format(
                _count_text(c.old), _count_text(c.new)), None))
        elif c.kind == INSTALMENT:
            note = None
            if (c.number == 1 and c.old is not None and c.new is not None
                    and c.old[0] and c.new[0] and c.old[0] != c.new[0]):
                note = datetext.delivery_change(c.old[0], c.new[0])
                if c.new[0] < c.old[0]:
                    note = _("{} — free").format(note)
            rows.append([_("Delivery {n}").format(n=c.number),
                         _instalment_value(c), note])
    last = next((r for r in reversed(rows) if isinstance(r, list)), None)
    if last is not None:
        whole = _("The percentages are shares of the whole estate.")
        last[2] = "{} {}".format(last[2], whole) if last[2] else whole
    return [tuple(r) for r in rows]


def change_rows(changes, wizard=False, new_in_network=()):
    """``[(label, value, note)]`` of ``changes`` (rich text values; ``note``
    a muted line or None). None for no changes list (no will in force).

    ``wizard`` (#135): the BASIC wizard's summary, where an heir renamed
    (same address, one name each side) is a name-only change the Check will
    save without signing: :func:`name_only_text` with ``saved=False``.
    ``new_in_network`` (#137): URLs of will-executors seen for the first
    time on the network and selected automatically: "added
    <b>host</b> (new in the network)".
    """
    if changes is None:
        return None
    rows = _instalment_rows(changes)
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
            # #140: the texts are the owner's own, shown as rich text:
            # escaped, so a "<" or "&" is shown as written.
            old_text = _quoted(html.escape(" / ".join(c.old or [])))
            new_text = _quoted(html.escape(" / ".join(c.new or [])))
            if c.old and c.new:
                value = _("changed: {old} → {new}").format(
                    old=old_text, new="<b>{}</b>".format(new_text))
            elif c.new:
                value = _("added: {}").format("<b>{}</b>".format(new_text))
            else:
                value = _("removed: {}").format(old_text)
            rows.append((_("Message"), value, None))
        elif c.kind == EXECUTORS:
            value = _("from {old} to {new}").format(old=c.old, new="<b>{}</b>".format(c.new)) \
                if c.old != c.new else ""
            parts = []
            if c.added:
                parts.append(_added_executors(c.added, new_in_network))
            if c.removed:
                parts.append(_removed([_host(u) for u in c.removed]))
            detail = "; ".join(parts)
            value = "{}: {}".format(value, detail) if value and detail else (value or detail)
            rows.append((_("Will-executors"), value, None))
        elif c.kind == EXECUTOR_TERMS:
            # #141: a new fee address (it changes with the user's IP) or
            # fee. A copy not received is redone (the owner's sentence); a
            # received one only when it changes anyway.
            from .messages import executor_terms_message

            for url, change, received in c.added:
                if not received:
                    value = executor_terms_message(url, change)
                elif change == "fee":
                    value = _("{} has a new fee: the new copy uses it.").format(
                        _host(url))
                else:
                    value = _("{} has a new Bitcoin address to receive its "
                              "fee: the new copy uses it.").format(_host(url))
                rows.append((_("Will-executor fee"), value, None))
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
