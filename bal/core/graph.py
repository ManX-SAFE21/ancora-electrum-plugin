"""The "Graph" view of the Will tab, as data (CHANGELOG #184).

Owner (2026-10-23, previews approved): a third view next to "List" and
"Future": time from left to right, one column per delivery, one box per
transaction with the will-executor's NAME inside and the colour of its
STATE, curved links from each copy to its children (the copies of the next
delivery), branches that open and close, the shares of each delivery under
its date.

This module turns the will items into a tree; no Qt, no clock. The widget
is ``bal/gui/qt/graph_view.py``. Display only: nothing here builds, signs,
sends or changes a date.
"""

from collections.abc import Mapping
from urllib.parse import urlparse

from . import delivery
from .heirs import is_op_return_address
from .summary import _executor_state
from .will_versions import new_will_links

# The state of a box (codes; the widget gives them colours and symbols).
OK = "ok"            # verified, or delivered (in the blockchain)
WARN = "warn"        # sent and to be verified, no answer, or signed to send
ERR = "err"          # the will-executor did not receive it
DRAFT = "draft"      # prepared, not signed yet
OFF = "off"          # will-executor disabled or excluded, or a replaced version

STATES = (OK, WARN, ERR, DRAFT, OFF)

_FROM_EXECUTOR_STATE = {
    "verified": OK,
    "to_verify": WARN,
    "to_send": WARN,
    "failed": ERR,
    "to_sign": DRAFT,
}


class Node:
    """One transaction of the will in the graph.

    ``detail`` is the precise state (``summary._executor_state`` codes,
    ``"delivered"``, ``"replaced"``, ``"excluded"``), for the tooltip.
    """

    def __init__(self, wid, item, state, detail):
        """``wid``: the item id; ``item``: the ``WillItem``."""
        self.wid = wid
        self.item = item
        self.state = state
        self.detail = detail
        self.url = (item.we or {}).get("url")
        self.depth = 0
        self.parent = None
        self.children = []

    @property
    def name(self):
        """The will-executor's host ("safe21.io"), "" for a backup copy."""
        if not self.url:
            return ""
        host = urlparse(self.url).hostname or self.url
        return host[3:] if host.startswith("we.") else host

    @property
    def delivered(self):
        """In the blockchain (mempool or confirmed): its path stays green."""
        return self.detail == "delivered"


def _delivered(w):
    """True when the copy ``w`` reached the blockchain."""
    return bool(w.get_status("CONFIRMED") or w.get_status("MEMPOOL"))


def node_state(w, selected=None):
    """``(state, detail)`` of the will item ``w`` (see :data:`STATES`).

    ``selected``: the URLs of the will-executors selected now
    (``summary.selected_urls``), or None (no rule): a copy of a
    will-executor not selected is OFF ("excluded").
    """
    if _delivered(w):
        return OK, "delivered"
    if not w.get_status("VALID"):
        return OFF, "replaced"
    detail = _executor_state(w)
    url = (w.we or {}).get("url")
    if selected is not None and url and url not in selected:
        return OFF, "excluded"
    return _FROM_EXECUTOR_STATE.get(detail, WARN), detail


def shown(w, show_replaced=False):
    """Whether the item ``w`` is drawn: the will (VALID), what reached the
    blockchain, and the replaced versions only when asked."""
    return bool(w.get_status("VALID") or _delivered(w) or show_replaced)


def _drop_finished_wills(nodes):
    """Leave out the copies that reached the blockchain with nothing in force
    after them (#200, the rule of the Graph only in :func:`build_graph`).

    A node stays when it is VALID or a VALID node hangs under it (a will in
    several deliveries whose delivery 1 was executed keeps its green
    delivery 1). One walk up from each VALID node, so a thousand items stay
    fast; a loop in corrupted data stops at a node already seen.
    """
    live = set()
    for node in nodes.values():
        if not node.item.get_status("VALID"):
            continue
        current = node
        while current is not None and current.wid not in live:
            live.add(current.wid)
            current = current.parent
    for wid in [w for w, n in nodes.items() if n.delivered and w not in live]:
        node = nodes.pop(wid)
        if node.parent is not None and node in node.parent.children:
            node.parent.children.remove(node)
        for child in node.children:
            child.parent = None


def build_graph(willitems, selected=None, show_replaced=False, order=None):
    """The tree of the will: ``(roots, nodes)``.

    ``nodes``: ``{wid: Node}``; ``roots``: the nodes that spend the
    wallet's own coins (delivery 1), the others are children of the copy
    whose output they spend. ``depth`` is the delivery index (0 for
    delivery 1). ``order``: the will-executor URLs in the order of the
    Will-Executor tab; the children are sorted by it (then by name), so a
    will-executor keeps its place in every column.

    #200 (owner's wallet): a new will built on a coin that came back from
    an executed copy is not a later delivery of it. Its copies are roots,
    by the same rule as the versions of the Will tab
    (``will_versions.new_will_links``, asked on the whole will: its rule 4
    needs the replaced children too). The rest of a will in several
    deliveries rebuilt after delivery 1 was executed (a new coin, another
    fee) stays under that delivery 1 by the same rule (its rule 5), so the
    Graph and the List never group the copies differently. One rule of the
    Graph only: without ``show_replaced``, when a VALID copy is drawn, the
    copies that reached the blockchain with no VALID copy under them are
    left out (the old will, executed before the will in force: the List
    keeps it as a version). A VALID copy is never left out, and "show
    replaced" draws them again.
    """
    will = willitems or {}
    nodes = {}
    for wid, w in will.items():
        if shown(w, show_replaced):
            state, detail = node_state(w, selected)
            nodes[wid] = Node(wid, w, state, detail)
    cut = new_will_links(will)
    for wid, node in nodes.items():
        for inp in node.item.tx.inputs():
            parent = inp.prevout.txid.hex()
            if parent in nodes and parent != wid:
                if (wid, parent) in cut:
                    continue
                node.parent = nodes[parent]
                break
    rank = {u: i for i, u in enumerate(order or [])}

    def key(n):
        """Will-Executor tab order, then name, then id."""
        return (rank.get(n.url, len(rank)), n.name, n.wid)

    for node in nodes.values():
        if node.parent is not None:
            node.parent.children.append(node)
    if not show_replaced and any(n.item.get_status("VALID") for n in nodes.values()):
        _drop_finished_wills(nodes)
    roots = sorted((n for n in nodes.values() if n.parent is None), key=key)

    def walk(node, depth, seen):
        """Set the depths and sort the children (a loop is cut)."""
        if node.wid in seen:
            return
        seen.add(node.wid)
        node.depth = depth
        node.children.sort(key=key)
        for child in node.children:
            walk(child, depth + 1, seen)

    seen = set()
    for root in roots:
        walk(root, 0, seen)
    return roots, nodes


def future_ranges(roots):
    """``{wid: (first, last)}``: the possible futures under each node (#184).

    Owner: every path from the wallet to the last delivery is a possible
    future (at each delivery only one will-executor publishes its copy, the
    first to arrive). They are numbered from 1 in the order of the tree; a
    node covers the futures of its leaves ("Futures 10–18").
    """
    out = {}
    counter = [1]

    def walk(node, seen):
        if node.wid in seen:
            return
        seen.add(node.wid)
        first = counter[0]
        if not node.children:
            counter[0] += 1
        for child in node.children:
            walk(child, seen)
        out[node.wid] = (first, max(counter[0] - 1, first))

    seen = set()
    for root in roots:
        walk(root, seen)
    return out


def column_dates(nodes):
    """``[locktime]`` of each depth: the earliest date of its copies."""
    dates = {}
    for node in nodes.values():
        lt = node.item.tx.locktime
        if node.depth not in dates or lt < dates[node.depth]:
            dates[node.depth] = lt
    return [dates[d] for d in sorted(dates)]


def column_shares(will_settings, heirs):
    """``[[(name, share)]]``: who receives what at each delivery.

    A will in several deliveries: the shares of its table
    (``delivery.table_rows``), 0 % left out. One delivery: the heirs and
    their amounts (a message row is not a person). ``[]`` when unknown.
    """
    try:
        rows = delivery.table_rows(will_settings or {})
    except Exception:
        rows = []
    if rows:
        out = []
        for row in rows:
            shares = (row.get("shares") or {}) if isinstance(row, Mapping) else {}
            out.append([(name, str(share)) for name, share in shares.items()
                        if str(share).strip() not in ("0", "0%", "0 %", "")])
        return out
    people = []
    for name, row in (heirs or {}).items():
        try:
            address, amount = row[0], row[1]
        except (TypeError, IndexError, KeyError):
            continue
        if is_op_return_address(address):
            continue
        people.append((name, str(amount)))
    return [people] if people else []
