"""The possible futures of a will in several deliveries (CHANGELOG #187).

A will in several deliveries is a tree of transactions: the copies of
delivery 1 spend the wallet's coins, each copy of delivery 2 spends the
change of one copy of delivery 1, and so on. At each delivery only ONE copy
is mined: the first a will-executor publishes, which can only be one of
the copies with the EARLIEST date (the others are not valid yet). So every
path from a delivery-1 copy with the earliest date, through the
earliest-dated children at each level, down to a leaf is a POSSIBLE
FUTURE, and the will is safe only when every possible future pays the
plan the owner wants (the Graph view counts the same futures, #184).

Why this module (owner's study of 2026-10-24, R-1): a copy held by a
will-executor no longer selected (excluded or removed after it received
the will) cannot be recalled, and the build makes new copies only for the
selected will-executors. When a later delivery changed, that will-executor's
copies kept old subtrees that nothing pre-empted: with three will-executors
and one excluded, 15 of 23 possible futures paid the old shares, and the
Check said "will ok". Now the Check verifies the futures
(``Will.is_will_valid``) and a rebuild that would leave such a future is
refused (``Will.date_chain``).

Pure: no Qt, no wallet, nothing stored. Items are read through
``item.tx.locktime``, ``item.tx.inputs()`` (``txin.prevout.txid`` bytes)
and ``item.heirs``.
"""

from . import delivery

#: The reserved name prefix of the will-executor fee outputs (not a person).
_EXECUTOR_HEIR = 'w!ll3x3c"'


def rows_paid(heirs):
    """What a delivery pays: ``frozenset({(address, amount as written)})``
    of the person and message rows of ``heirs`` (``{name: [address,
    amount, ...]}``), the will-executor fee rows left out. Names do not
    matter for the payment (a rename changes nothing on the chain)."""
    out = set()
    for name, value in (heirs or {}).items():
        if str(name).startswith(_EXECUTOR_HEIR):
            continue
        try:
            out.add((str(value[0]), str(value[1])))
        except (IndexError, TypeError, KeyError):
            continue
    return frozenset(out)


def plan_of_rows(heirs):
    """The plan: ``[rows_paid of delivery 1, of delivery 2, ...]`` from the
    heir rows the build uses (``{name: [address, amount, date]}``; the rows
    of a later delivery are named "Luca§2", #168). A will with one date
    gives one entry."""
    by_number = {}
    for name, value in (heirs or {}).items():
        if str(name).startswith(_EXECUTOR_HEIR):
            continue
        by_number.setdefault(delivery.instalment_number(name), {})[name] = value
    if not by_number:
        return []
    return [rows_paid(by_number.get(n, {})) for n in range(1, max(by_number) + 1)]


def spent_ids(item):
    """The txids (hex) whose outputs ``item`` spends; None when its
    transaction cannot be read."""
    try:
        return {txin.prevout.txid.hex() for txin in item.tx.inputs()}
    except Exception:
        return None


def _locktime(item):
    """The date of ``item`` as an int; None when unreadable."""
    try:
        return int(item.tx.locktime)
    except (TypeError, ValueError, AttributeError):
        return None


def possible_futures(items):
    """Every possible future of ``items`` (``{txid: item}``): a list of
    paths, each a list of txids from a delivery-1 copy to a leaf.

    A delivery-1 copy spends no output of the will; only the copies with
    the earliest date can be mined first, so only they start a future; at
    each level only the earliest-dated children go on. An item whose
    transaction cannot be read is left out (never invented). ``[]`` for no
    items.
    """
    ids = set(items)
    parents = {}
    for wid, item in items.items():
        spent = spent_ids(item)
        if spent is None or _locktime(item) is None:
            continue
        parents[wid] = spent & ids
    children = {}
    for wid, ps in parents.items():
        for p in ps:
            children.setdefault(p, []).append(wid)
    roots = [wid for wid, ps in parents.items() if not ps]
    if not roots:
        return []
    first = min(_locktime(items[w]) for w in roots)
    out = []

    def walk(node, path, seen):
        """Depth first; a loop is cut."""
        kids = [k for k in children.get(node, []) if k not in seen]
        if not kids:
            out.append(path)
            return
        early = min(_locktime(items[k]) for k in kids)
        for k in sorted(kids):
            if _locktime(items[k]) == early:
                walk(k, path + [k], seen | {k})

    for root in sorted(roots):
        if _locktime(items[root]) == first:
            walk(root, [root], {root})
    return out


def futures_off_plan(items, plan):
    """The possible futures of ``items`` that do NOT pay ``plan``
    (:func:`plan_of_rows`): a path pays the plan when it has exactly one
    copy per delivery and each copy pays that delivery's rows. ``[]`` when
    every future pays the plan (or there is none)."""
    if not plan:
        return []
    bad = []
    for path in possible_futures(items):
        if len(path) != len(plan):
            bad.append(path)
            continue
        if any(rows_paid(items[wid].heirs) != plan[i] for i, wid in enumerate(path)):
            bad.append(path)
    return bad


def future_text(path, items):
    """``"we0.test -> we2.test -> we1.test"``: the will-executors of a
    future (``backup`` for the copy without one), for the log."""
    names = []
    for wid in path:
        url = ((getattr(items[wid], "we", None) or {}).get("url") or "backup")
        names.append(str(url).replace("https://", "").replace("http://", ""))
    return " -> ".join(names)
