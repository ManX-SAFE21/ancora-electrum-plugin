"""The will by versions, for the Will tab (CHANGELOG #129).

Every build of the will (Check, wallet close, Prepare, automatic rebuild)
makes one transaction per selected will-executor (plus one without
executor, the backup, when that option is on), and the older transactions
stay in the wallet with their flags. The Will tab used to list them all,
one flat row each: after a few changes the owner could no longer tell
which rows were "the will" and which were history.

This module groups the stored will items into VERSIONS, one per build, and
gives each version a state. Nothing new is stored in the wallet (owner
decision 2026-10-12): everything is derived at every refresh from the items
the plugin already keeps (transaction, heirs, will-executor, build ``time``,
fee rate, status flags) and, for the will in force and the paid
invalidation, from the wallet history.

Grouping rules (verified on flowkit histories, ``tests/test_core_will_versions.py``):

* ROOTS are the items that do not spend the output of another will item
  (``father`` of ``Will.add_willtree``, recomputed here without writing it).
  The other items (multiverse instalments) belong to their root's version.
* The VERSION KEY of a root is its content: locktime, sorted inputs, heirs
  without the will-executor pseudo-heirs (name, address, amount as set,
  heir date) and the fee rate. The build ``time`` is NOT part of the key:
  an item kept by the #122 reuse (or brought back by #128) keeps its old
  ``time``, so a will-executor added later would otherwise split one
  version in two.
* When one key holds two items for the same will-executor URL (or two
  backups), the build changed only something that is not in the key (for
  example a will-executor's own fee or fee address): the group is split,
  newest ``time`` first, then by txid; an URL found only once goes with the
  newest version (it was reused by that build).
* Roots without heirs (a single transaction merged from outside, a
  transaction list imported by QR) are not a will built by the plugin: they
  go to one "other transactions" group, never numbered.
* Versions are ordered by their build ``time`` (oldest first; versions
  without ``time``, from very old wallets, come first). Numbers count only
  the versions with an item signed (also partly) or sent: the oldest is
  #1. A version never signed nor sent that is not the current draft is a
  "discarded draft": unnumbered, shown only with "Show all history".

Version states (``VERSION_*``): draft, in force (the version holding the
item in force with the EARLIEST delivery, the one the will-executors would
execute first), replaced but still held by the will-executors (signed and
sent, still executable), replaced, invalidated, partially signed, executed,
not valid.

The paid invalidation is not a will item: it is found as the transaction
that spends the inputs of an invalidated version (``wallet.adb.get_spender``)
and is not itself a will item. It is shown only when it is recognisable as
an invalidation (the history label the plugin writes, or every output back
to the wallet); on any error, or without a wallet, it is simply left out.

Pure: no Qt. Each item is classified once per call
(:func:`bal.core.will_state.classify`), and the chain is asked at most once
per item (``Will._tx_still_executable``), so a multiverse will of a
thousand items stays fast.
"""

import hashlib
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import will_state
from .delivery import person_name
from .heirs import (
    HEIR_ADDRESS,
    HEIR_AMOUNT,
    HEIR_LOCKTIME,
    HEIR_REAL_AMOUNT,
    is_op_return_address,
)
from .util import Util
from .will import Will

# The reserved name prefix of the will-executor pseudo-heirs (the fee
# outputs, see ``bal/core/heirs.py``): not a person, not part of the key.
_EXECUTOR_HEIR = 'w!ll3x3c"'

# The history label the plugin writes on its invalidation transactions
# (``window.py``, ``dialogs.py``, ``cli/controller.py``, all through
# ``bal.engine.invalidate`` since #152). Stored data, never translated.
INVALIDATION_LABEL = "Safe21 Heartbeat invalidation"

VERSION_DRAFT = "draft"
VERSION_IN_FORCE = "in_force"
VERSION_HELD = "replaced_held"
VERSION_REPLACED = "replaced"
VERSION_INVALIDATED = "invalidated"
VERSION_PARTIALLY_SIGNED = "partially_signed"
VERSION_EXECUTED = "executed"
VERSION_NOT_VALID = "not_valid"
VERSION_OTHER = "other"
# CHANGELOG #129 (owner's ZIP test): a version none of whose items was ever
# signed nor sent, and that is no longer the current draft (a draft that a
# later build marked REPLACED before #128 dropped the drafts). Never
# numbered, shown only with "Show all history". Nothing is deleted.
VERSION_DISCARDED = "discarded_draft"

# How many versions the list shows before "Show all history" (owner).
RECENT_VERSIONS = 5


@dataclass
class Version:
    """One build of the will: its items and what can be said about it.

    ``roots``: the ids of its root items (one per will-executor, plus the
    backup); ``children``: ``{id: [child ids]}`` for the multiverse
    instalments; ``items``: every id of the version (roots first, then the
    descendants in tree order). ``order``: position from the oldest (0);
    ``number``: ``#n`` for the non-draft versions, ``None`` for drafts and
    the "other transactions" group.
    """

    vid: str
    roots: List[str]
    children: Dict[str, List[str]]
    items: List[str]
    locktime: Optional[int]
    time_min: Optional[float]
    time_max: Optional[float]
    fee_rate: Optional[float]
    heirs: list
    executors_total: int
    executors_verified: int
    # Copies a will-executor received (PUSHED or CHECKED); 0: "not sent".
    executors_sent: int = 0
    state: str = VERSION_NOT_VALID
    order: int = 0
    number: Optional[int] = None


@dataclass
class Invalidation:
    """A paid on-chain invalidation that ended one or more versions.

    ``fee``: its miner fee in sat (``None`` when unknown); ``confirmed``:
    mined (else in the mempool); ``ended``: the ``vid`` of the versions
    whose coins it spent; ``anchor``: the newest of them (the row goes
    right above it in the newest-first list).
    """

    txid: str
    fee: Optional[int]
    confirmed: bool
    labelled: bool
    ended: List[str] = field(default_factory=list)
    anchor: Optional[str] = None


@dataclass
class History:
    """The result of :func:`build_history`.

    ``versions``: every version except the "other transactions" group,
    oldest first; ``other``: that group or ``None``; ``states``: the
    ``will_state`` of each item; ``invalidations``: the paid invalidations
    found; ``current``: the ``vid`` of the version in force, or ``None``.
    """

    versions: List[Version]
    other: Optional[Version]
    states: Dict[str, str]
    invalidations: List[Invalidation]
    current: Optional[str]

    def by_vid(self):
        """``{vid: Version}`` of every version, the other group included."""
        out = {v.vid: v for v in self.versions}
        if self.other is not None:
            out[self.other.vid] = self.other
        return out


def _flag(item, name):
    """The status flag ``name`` of ``item`` (False when it has none)."""
    try:
        return bool(item.get_status(name))
    except (KeyError, AttributeError):
        return False


def _txid_of_input(inp):
    """The txid (hex) an input spends."""
    return inp.prevout.txid.hex()


def _inputs(item):
    """The sorted outpoints (``txid:n``) spent by ``item``."""
    try:
        return tuple(sorted(Util.utxo_to_str(i) for i in item.tx.inputs()))
    except Exception:
        return ()


def _url(item):
    """The will-executor URL of ``item``, or ``None`` (backup)."""
    we = getattr(item, "we", None)
    return we.get("url") if we else None


def _heirs_key(item):
    """The heirs of ``item`` as set by the owner, without the pseudo-heirs.

    (name, address, amount as set, heir date). The resolved amount (index
    ``HEIR_REAL_AMOUNT``) is left out: it depends on the fees of each
    will-executor's transaction, and must not split one build.
    """
    out = []
    for name, heir in (getattr(item, "heirs", None) or {}).items():
        if str(name).startswith(_EXECUTOR_HEIR):
            continue
        try:
            row = list(heir)
        except TypeError:
            row = [heir]
        row += [None] * 3
        out.append((
            str(name), str(row[HEIR_ADDRESS]), str(row[HEIR_AMOUNT]),
            str(row[HEIR_LOCKTIME]),
        ))
    return tuple(sorted(out))


def has_heirs(item):
    """True when ``item`` names at least one heir (pseudo-heirs aside)."""
    return bool(_heirs_key(item))


def version_key(item):
    """The content key of a root item (see the module docstring)."""
    try:
        locktime = int(item.tx.locktime)
    except Exception:
        locktime = None
    return (locktime, _inputs(item), _heirs_key(item), getattr(item, "tx_fees", None))


def _digest(value):
    """A short stable id for ``value`` (its ``repr`` hashed)."""
    return hashlib.sha1(repr(value).encode("utf-8")).hexdigest()[:16]


def _fathers(will):
    """``{id: father id}``: the will item whose output each item spends.

    The same rule as ``Will.add_willtree`` (the first input spending a
    will item's output), computed here without writing ``father`` on the
    items (the list only reads; ``father`` is stored in the wallet).
    """
    fathers = {}
    for wid, item in will.items():
        try:
            inputs = item.tx.inputs()
        except Exception:
            continue
        for inp in inputs:
            parent = _txid_of_input(inp)
            if parent != wid and parent in will:
                fathers[wid] = parent
                break
    return fathers


def _root_of(wid, fathers):
    """The root of ``wid`` (loops in corrupted data stop at the start)."""
    seen = {wid}
    while wid in fathers:
        wid = fathers[wid]
        if wid in seen:
            break
        seen.add(wid)
    return wid


def _split_by_url(roots, will):
    """Split one key group so that no URL appears twice in a version.

    Roots are taken newest ``time`` first, then by txid; each goes into
    the first (newest) bucket that does not hold its URL yet. Returns the
    buckets newest first.
    """
    def newest_first(wid):
        t = getattr(will[wid], "time", None)
        return (-(float(t)) if t is not None else float("inf"), wid)

    buckets = []
    for wid in sorted(roots, key=newest_first):
        url = _url(will[wid])
        for bucket in buckets:
            if url not in bucket:
                bucket[url] = wid
                break
        else:
            buckets.append({url: wid})
    return [sorted(b.values()) for b in buckets]


def _descendants(root, children):
    """The descendants of ``root`` in tree order (depth first)."""
    out = []
    stack = list(reversed(children.get(root, [])))
    seen = {root}
    while stack:
        wid = stack.pop()
        if wid in seen:
            continue
        seen.add(wid)
        out.append(wid)
        stack.extend(reversed(children.get(wid, [])))
    return out


def _heir_rows(items):
    """``[(name, amount as set, resolved sats or None)]`` of the version.

    The first time a name appears wins (every executor copy carries the
    same heirs; multiverse instalments add theirs). OP_RETURN messages are
    left out. The name shown is the person's (#168: one row per delivery,
    each under the person's name).
    """
    rows = {}
    for item in items:
        for name, heir in (getattr(item, "heirs", None) or {}).items():
            if str(name).startswith(_EXECUTOR_HEIR) or name in rows:
                continue
            try:
                row = list(heir)
            except TypeError:
                continue
            if row and is_op_return_address(str(row[HEIR_ADDRESS])):
                continue
            row += [None] * 4
            try:
                sats = int(row[HEIR_REAL_AMOUNT])
            except (TypeError, ValueError):
                sats = None
            # #168: shown under the person's name ("Luca" for "Luca§2").
            rows[name] = (person_name(name), str(row[HEIR_AMOUNT]), sats)
    return [rows[name] for name in rows]


def _ever_signed_or_sent(item):
    """True when ``item`` was ever signed (also partly) or sent."""
    return (
        _flag(item, "COMPLETE")
        or _flag(item, "PUSHED")
        or _flag(item, "CHECKED")
        or will_state.is_partially_signed(item)
    )


def _make_version(vid, roots, will, fathers_children):
    """A :class:`Version` of ``roots`` with its descendants and summary."""
    roots = sorted(roots, key=lambda w: (_url(will[w]) is None, str(_url(will[w])), w))
    items = []
    for root in roots:
        items.append(root)
        items.extend(_descendants(root, fathers_children))
    children = {w: list(fathers_children.get(w, [])) for w in items if fathers_children.get(w)}
    times = [float(will[w].time) for w in items if getattr(will[w], "time", None) is not None]
    first = will[roots[0]]
    try:
        locktime = int(first.tx.locktime)
    except Exception:
        locktime = None
    with_executor = [w for w in items if _url(will[w])]
    verified = [w for w in with_executor if _flag(will[w], "CHECKED")]
    return Version(
        vid=vid,
        roots=roots,
        children=children,
        items=items,
        locktime=locktime,
        time_min=min(times) if times else None,
        time_max=max(times) if times else None,
        fee_rate=getattr(first, "tx_fees", None),
        heirs=_heir_rows([will[w] for w in items]),
        executors_total=len(with_executor),
        executors_verified=len(verified),
        executors_sent=len([
            w for w in with_executor
            if _flag(will[w], "PUSHED") or _flag(will[w], "CHECKED")
        ]),
    )


def item_states(will, wallet=None, synced=None):
    """``{id: will_state}`` of every item, the chain asked once per item.

    ``synced`` defaults to ``Will.wallet_synced(wallet)``. On a wallet not
    synchronized the spends are not known and every signed or sent item
    counts as in force (the #128 safe side).
    """
    if synced is None:
        synced = Will.wallet_synced(wallet)
    states = {}
    for wid, item in will.items():
        def executable(wid=wid):
            """Asked by ``classify`` only for the items that may be in force."""
            try:
                return Will._tx_still_executable(wid, will, wallet)
            except Exception:
                # Unknown: the safe side, as on a wallet not synchronized.
                return True
        states[wid] = will_state.classify(item, synced, executable)
    return states


def _version_state(version, states, will, current):
    """The state of ``version`` from the states of its items."""
    found = [states.get(w) for w in version.items]
    if will_state.EXECUTED in found:
        return VERSION_EXECUTED
    if will_state.IN_FORCE in found:
        if version.vid == current:
            return VERSION_IN_FORCE
        # Only a copy a will-executor received is "still held" by them; a
        # signed copy never sent (or a backup) exists only in this wallet.
        held = any(
            states.get(w) == will_state.IN_FORCE
            and (_flag(will[w], "PUSHED") or _flag(will[w], "CHECKED"))
            for w in version.items
        )
        return VERSION_HELD if held else VERSION_REPLACED
    if will_state.PARTIALLY_SIGNED in found:
        return VERSION_PARTIALLY_SIGNED
    if found and all(s == will_state.DRAFT for s in found):
        return VERSION_DRAFT
    if will_state.INVALIDATED in found:
        return VERSION_INVALIDATED
    if will_state.REPLACED in found:
        return VERSION_REPLACED
    if will_state.DRAFT in found:
        return VERSION_DRAFT
    return VERSION_NOT_VALID


def _current(versions, states, will):
    """The ``vid`` of the version in force, or ``None``.

    The version holding the in-force item with the EARLIEST delivery: the
    will-executors execute that one first (#128 semantics; several sent
    versions can be executable at once). On a tie the newest version wins.
    """
    best = None
    for version in versions:
        for wid in version.items:
            if states.get(wid) != will_state.IN_FORCE:
                continue
            try:
                lt = int(will[wid].tx.locktime)
            except Exception:
                continue
            key = (lt, -version.order)
            if best is None or key < best[0]:
                best = (key, version.vid)
    return best[1] if best else None


def in_force_delivery(history, will):
    """The delivery date (locktime) of THE will in force, or ``None`` (#135).

    The earliest locktime among the items whose state is
    ``will_state.IN_FORCE`` in ``history`` (:func:`build_history`), the
    same items and the same rule as ``History.current`` and as
    ``engine.context.in_force_delivery`` (``Will.in_force_items``). For the
    callers that already have the history (the PDF report).
    """
    dates = []
    for wid, state in (history.states or {}).items():
        if state != will_state.IN_FORCE or wid not in will:
            continue
        try:
            dates.append(int(will[wid].tx.locktime))
        except Exception:
            continue
    return min(dates) if dates else None


def _chronology(version):
    """Sort key, oldest first: versions without ``time`` first, then by time."""
    if version.time_min is None:
        return (0, 0.0, 0.0, version.locktime or 0, version.vid)
    return (1, version.time_min, version.time_max, version.locktime or 0, version.vid)


def _invalidations(versions, will, wallet):
    """The paid invalidations that ended the invalidated versions.

    For each version with an invalidated item, the spender of its root's
    inputs (``wallet.adb.get_spender``; Electrum leaves out wallet-local
    copies) that is not a will item. Kept only when recognisable (the
    plugin's history label, or every output back to this wallet). Any
    error leaves the row out: the list must never fail because of it.
    """
    if wallet is None or getattr(wallet, "adb", None) is None:
        return []
    found = {}
    for version in versions:
        if version.state != VERSION_INVALIDATED:
            continue
        for wid in version.roots:
            for outpoint in _inputs(will[wid]):
                try:
                    spender = wallet.adb.get_spender(outpoint)
                except Exception:
                    spender = None
                if not spender or spender in will:
                    continue
                inv = found.get(spender)
                if inv is None:
                    inv = _describe_spender(wallet, spender)
                    if inv is None:
                        found[spender] = False
                        continue
                    found[spender] = inv
                if inv and version.vid not in inv.ended:
                    inv.ended.append(version.vid)
    out = [inv for inv in found.values() if inv]
    order = {v.vid: v.order for v in versions}
    for inv in out:
        inv.anchor = max(inv.ended, key=lambda vid: order.get(vid, -1))
    out.sort(key=lambda inv: order.get(inv.anchor, -1))
    return out


def _describe_spender(wallet, txid):
    """An :class:`Invalidation` for spender ``txid``, or ``None``.

    ``None`` when the transaction is not recognisable as an invalidation
    (neither the plugin's label nor every output to this wallet: a payment
    to someone else also ends the will, but it is not the paid
    invalidation) or on any error.
    """
    try:
        try:
            label = wallet.get_label_for_txid(txid)
        except Exception:
            label = None
        labelled = label == INVALIDATION_LABEL
        if not labelled:
            tx = wallet.adb.get_transaction(txid)
            outputs = tx.outputs() if tx is not None else []
            if not outputs or not all(
                o.address and wallet.is_mine(o.address) for o in outputs
            ):
                return None
        try:
            fee = wallet.adb.get_tx_fee(txid)
        except Exception:
            fee = None
        try:
            height = wallet.adb.get_tx_height(txid).height()
        except Exception:
            height = 0
        return Invalidation(
            txid=txid, fee=fee, confirmed=bool(height and height > 0),
            labelled=labelled,
        )
    except Exception:
        return None


def build_history(will, wallet=None, synced=None):
    """Group ``will`` (``{id: item}``) into versions; see the module docstring.

    ``wallet`` (optional) is asked for the spends (the will in force) and
    the paid invalidations; ``synced`` defaults to
    ``Will.wallet_synced(wallet)``. Never writes anything.
    """
    will = dict(will or {})
    states = item_states(will, wallet, synced)
    fathers = _fathers(will)
    children = {}
    for wid, father in fathers.items():
        children.setdefault(father, []).append(wid)
    for kids in children.values():
        kids.sort()
    roots = [wid for wid in will if _root_of(wid, fathers) == wid]
    # Items in a father loop (corrupted data) have no root: each becomes
    # its own root so that nothing disappears from the list.
    reached = set()
    for root in roots:
        reached.add(root)
        reached.update(_descendants(root, children))
    roots += [wid for wid in will if wid not in reached]

    groups = {}
    others = []
    for wid in roots:
        if not has_heirs(will[wid]):
            others.append(wid)
            continue
        groups.setdefault(version_key(will[wid]), []).append(wid)

    versions = []
    for key, members in groups.items():
        base = _digest(key)
        urls = [_url(will[w]) for w in members]
        if len(set(urls)) == len(urls):
            versions.append(_make_version(base, members, will, children))
            continue
        buckets = _split_by_url(members, will)
        # Numbered from the oldest bucket, so a newer split does not change
        # the id of the older ones (the list keeps their expanded state).
        for index, bucket in enumerate(reversed(buckets)):
            versions.append(_make_version(
                "{}:{}".format(base, index), bucket, will, children))

    versions.sort(key=_chronology)
    for order, version in enumerate(versions):
        version.order = order
    current = _current(versions, states, will)
    number = 0
    for version in versions:
        version.state = _version_state(version, states, will, current)
        ever = any(_ever_signed_or_sent(will[w]) for w in version.items)
        if not ever and version.state != VERSION_DRAFT:
            version.state = VERSION_DISCARDED
        # Numbers only for the versions with an item signed (also partly)
        # or sent: the owner's "#17" was a discarded draft created after
        # the will in force, so the will in force was not the highest.
        if ever:
            number += 1
            version.number = number

    other = None
    if others:
        other = _make_version("other", sorted(others), will, children)
        other.state = VERSION_OTHER
        other.order = len(versions)

    return History(
        versions=versions,
        other=other,
        states=states,
        invalidations=_invalidations(versions, will, wallet),
        current=current,
    )


def display_rows(history, show_all=False, recent=RECENT_VERSIONS):
    """The rows of the list, in order, and how many versions are hidden.

    Returns ``(rows, hidden)``; each row is ``("version", Version)`` or
    ``("invalidation", Invalidation)``. Order (owner): the drafts (newest
    first), the version in force, then the history newest first (by
    creation); a paid invalidation goes right above the newest version it
    ended. Without ``show_all`` the list stops after the ``recent`` newest
    numbered versions (the version in force counts as one of them, drafts
    are not counted); the discarded drafts and invalidations among them stay
    in their place. Owner, ZIP test of #129: "Show all history" must only
    ADD older rows at the bottom, never make rows appear between the ones
    already shown (before, the discarded drafts appeared in the middle).
    ``hidden`` counts every version left out, discarded drafts included.
    The "other transactions" group is always last.
    """
    drafts = [v for v in history.versions if v.state == VERSION_DRAFT]
    drafts.sort(key=lambda v: v.order, reverse=True)
    rest = [v for v in history.versions if v.state != VERSION_DRAFT]
    rest.sort(key=lambda v: v.order, reverse=True)
    current = [v for v in rest if v.vid == history.current]
    rest = current + [v for v in rest if v.vid != history.current]
    hidden = 0
    if not show_all:
        numbered = 0
        cut = len(rest)
        for i, version in enumerate(rest):
            if version.state != VERSION_DISCARDED:
                numbered += 1
                if numbered == recent:
                    cut = i + 1
                    break
        hidden = len(rest) - cut
        rest = rest[:cut]
    by_anchor = {}
    for inv in history.invalidations:
        by_anchor.setdefault(inv.anchor, []).append(inv)
    rows = [("version", v) for v in drafts]
    for version in rest:
        for inv in by_anchor.get(version.vid, []):
            rows.append(("invalidation", inv))
        rows.append(("version", version))
    if history.other is not None:
        rows.append(("version", history.other))
    return rows, hidden
