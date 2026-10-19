"""The will report: the data of the PDF pages (CHANGELOG #92, #132).

#92 made an "old style" 78-column text report; #132 (owner's plan, mockup
approved 2026-10-13) replaces it with A4 pages drawn by the GUI
(``bal/gui/qt/report_pages.py``): a header with the Safe21 logo, the state
of the will, the #131 time line, the heirs with their full addresses, the
will-executors, how it works, then the technical details.

This module builds the DATA of those pages, Qt-free (``bal/core`` rule), so
the painter only draws and the tests can check every value. It reads the
same sources as the Will tab, so the report, the will summary and the
Future view always agree:

* the state box: :func:`bal.core.summary.will_summary` (title, colour);
* the will in force, the delivery, the time left, the Check Alive, the
  heirs with their REAL shares (the amounts the will transaction pays) and
  the will-executors: :func:`bal.core.future.build_future` on
  :func:`bal.core.will_versions.build_history` (#129/#131);
* the instalments (multiverse, a hidden option today): the levels of the
  version's transaction tree (instalment 1 = the roots, one per
  will-executor; instalment 2 = their children; ...);
* the wills sent before and still executable: ``still_active``
  (``Will.sent_still_executable``, the list the will summary uses).

It never contains secrets: no seed, keys, xpub or passwords, and of the
signed transactions only their ids. Read only: it never builds, signs,
sends, invalidates or writes anything.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional
from urllib.parse import urlparse

from ..i18n import N_, _, number_separators
from . import delivery as core_delivery
from . import future as core_future
from . import will_versions
from .heirs import HEIR_ADDRESS, is_op_return_address, message_text
from .summary import executor_states, will_summary
from .will import Will

#: Where each will-executor stands (the texts of the Building Will dialog;
#: also the legend of the #131 Future view).
_EXECUTOR_TEXTS = {
    "verified": N_("received and verified"),
    "to_verify": N_("received, to be verified"),
    "to_send": N_("to send"),
    "to_sign": N_("to sign"),
    "failed": N_("not received"),
    # #134: a deselected will-executor that never received the will.
    "deselected": N_("deselected: not sent"),
}

# The reserved name prefix of the will-executor pseudo-heirs (their fee
# outputs, ``bal/core/heirs.py``): not a person.
_EXECUTOR_HEIR = 'w!ll3x3c"'


def _percent(pct):
    """``60 %`` / ``6,25 %`` with the decimal separator of the language.

    Two decimals at most (a printed document: 6.25 must not read 6.2).
    """
    text = "{:.2f}".format(pct).rstrip("0").rstrip(".")
    return text.replace(".", number_separators()[0]) + " %"


def _host(url):
    """The host name of a will-executor URL (the URL itself when it has none)."""
    return urlparse(url).hostname or url


def executor_fee(item):
    """The will-executor's own fee paid by ``item`` (sat), or ``None``.

    The value of the output to the executor's fee address; when the
    transaction has none (it cannot be read), the executor's base fee.
    Moved here from ``bal/gui/qt/lists.py`` (#129) for the report (#132):
    the Will tab and the report show the same fee.
    """
    we = getattr(item, "we", None)
    if not we:
        return None
    address = we.get("address")
    try:
        for out in item.tx.outputs():
            if address and out.address == address:
                return int(out.value)
    except Exception:
        pass
    try:
        return int(we.get("base_fee"))
    except (TypeError, ValueError):
        return None


# ----------------------------------------------------------------- model

@dataclass
class ReportHeir:
    """One heir of the will in force, over all its instalments.

    ``sats``: what the will's transactions pay to the heir (``None`` for an
    heir below the dust limit, who receives nothing); ``percent``: the
    share of everything the heirs receive (#131 rule).
    """

    name: str
    address: str
    sats: Optional[int]
    percent: Optional[float]


@dataclass
class ReportTx:
    """One transaction listed in the technical details.

    ``url`` is ``None`` for a backup transaction (no will-executor);
    ``fee`` the will-executor's own fee (sat) or ``None``.
    """

    url: Optional[str]
    locktime: int
    txid: str
    fee: Optional[int] = None

    @property
    def host(self):
        """The will-executor's host, or ``None`` for a backup transaction."""
        return _host(self.url) if self.url else None


@dataclass
class ReportInstalment:
    """One instalment (one delivery date) of the will in force.

    ``heirs``: :class:`bal.core.future.HeirShare` paid on that date;
    ``tx_count``: how many transactions the will-executors hold for it
    (multiverse: N, N², N³, ...); ``states``: ``{url: state}`` of each
    will-executor at its worst copy of this instalment.
    """

    number: int
    locktime: int
    heirs: list = field(default_factory=list)
    tx_count: int = 0
    states: Dict[str, str] = field(default_factory=dict)

    @property
    def verified(self):
        """How many will-executors received and verified this instalment."""
        return sum(1 for s in self.states.values() if s == "verified")


@dataclass
class WillReport:
    """Everything the report pages show (see the module docstring).

    ``summary``: the will summary dict (``title``, ``kind``); ``future``:
    the :class:`bal.core.future.Future` of the will in force (the time
    line); ``miner_fee``: the fee rate of the settings (sat/vB, text) or
    ``None``; ``first_txs``: the transactions of instalment 1, one per
    will-executor (plus the backup); ``older``: the wills sent before and
    still executable, held by the will-executors.
    """

    created: datetime
    wallet_name: str
    plugin_version: str
    electrum_version: str
    summary: dict
    future: object
    miner_fee: Optional[str] = None
    heirs: List[ReportHeir] = field(default_factory=list)
    instalments: List[ReportInstalment] = field(default_factory=list)
    # #140 (owner): the message(s) written in the will in force (OP_RETURN).
    messages: List[str] = field(default_factory=list)
    first_txs: List[ReportTx] = field(default_factory=list)
    older: List[ReportTx] = field(default_factory=list)

    @property
    def multiverse(self):
        """True when the will in force has more than one instalment."""
        return len(self.instalments) > 1

    @property
    def total_sats(self):
        """Everything the heirs receive (sat)."""
        return sum(h.sats for h in self.heirs if h.sats)

    @property
    def delivery(self):
        """The first delivery date (UNIX time), or ``None``."""
        return self.future.delivery if self.future is not None else None


# ------------------------------------------------------------------ data

def _levels(version):
    """The ids of ``version`` by instalment: ``[[roots], [children], ...]``.

    Multiverse: each instalment spends the change of the one before at
    every will-executor, so level ``k`` holds the children of level
    ``k - 1``. Without multiverse there is one level (the roots).
    """
    out, seen, level = [], set(), list(version.roots)
    while level:
        out.append(level)
        seen.update(level)
        nxt = []
        for wid in level:
            nxt += [c for c in version.children.get(wid) or [] if c not in seen]
        level = list(dict.fromkeys(nxt))
    return out


def _addresses(items):
    """``{heir name: address}`` of the heirs of ``items`` (people only; the
    rows of later deliveries under the person's name, #168)."""
    out = {}
    for item in items:
        for name, heir in (getattr(item, "heirs", None) or {}).items():
            if str(name).startswith(_EXECUTOR_HEIR):
                continue
            try:
                address = str(list(heir)[HEIR_ADDRESS])
            except (TypeError, IndexError):
                continue
            if address and not is_op_return_address(address):
                # #168: by person (the rows "Luca§2"... are Luca's).
                out.setdefault(core_delivery.person_name(name), address)
    return out


def _messages(items):
    """The texts of the OP_RETURN messages of the will ``items`` (#140).

    Read from the first item that has any: every will-executor copy
    carries the same message. Sorted; empty with no message.
    """
    for item in items:
        texts = []
        for name, heir in (getattr(item, "heirs", None) or {}).items():
            if str(name).startswith(_EXECUTOR_HEIR):
                continue
            try:
                address = str(list(heir)[HEIR_ADDRESS])
            except (TypeError, IndexError):
                continue
            if is_op_return_address(address):
                texts.append(message_text(address))
        if texts:
            return sorted(texts)
    return []


def _heirs(deliveries, addresses):
    """The heirs over all instalments, by name (amounts and shares summed)."""
    rows = {}
    for inst in deliveries:
        for h in inst.heirs:
            row = rows.setdefault(h.name, ReportHeir(
                name=h.name, address=addresses.get(h.name, ""), sats=None, percent=None))
            if h.sats is not None:
                row.sats = (row.sats or 0) + h.sats
            if h.percent is not None:
                row.percent = (row.percent or 0.0) + h.percent
    return [rows[name] for name in sorted(rows)]


def _tx(item):
    """A :class:`ReportTx` of ``item``."""
    try:
        locktime = int(item.tx.locktime)
    except Exception:
        locktime = 0
    return ReportTx(url=(item.we or {}).get("url") or None, locktime=locktime,
                    txid=str(item._id), fee=executor_fee(item))


def _tx_order(tx):
    """Sort key: will-executors by host, the backup transaction last."""
    return (tx.url is None, tx.host or "", tx.txid)


def will_report(willitems, heirs, will_settings, is_basic_mode, *, wallet=None,
                wallet_name="", plugin_version="", electrum_version="",
                still_active=None, now=None, created=None, warn_months=None,
                selected=None):
    """The :class:`WillReport` of the wallet's will (see the module docstring).

    ``heirs``: the Heirs tab (only its size, for the summary); ``now``: the
    clock of the summary and the time line (tests); ``created``: the time
    printed as "Created on" (default: now, local); ``selected``: the URLs of
    the will-executors selected now (#134, ``summary.selected_urls``): a
    deselected one that never received the will is "deselected: not sent".
    Raises what the data raises: the GUI catches it and says the report
    could not be prepared.
    """
    will = dict(willitems or {})
    history = will_versions.build_history(will, wallet)
    summary = will_summary(
        willitems, len(heirs or {}), will_settings, is_basic_mode, now=now,
        still_active=still_active, warn_months=warn_months, selected=selected,
        # #135: the will in force by the one rule of the Will tab.
        in_force=will_versions.in_force_delivery(history, will),
    ) or {}
    future = core_future.build_future(
        will, will_settings, is_basic_mode, now=now, warn_months=warn_months,
        wallet=wallet, history=history, selected=selected,
    )
    fee = (will_settings or {}).get("baltx_fees")
    report = WillReport(
        created=created or datetime.now(), wallet_name=wallet_name,
        plugin_version=plugin_version, electrum_version=electrum_version,
        summary=summary, future=future,
        miner_fee=None if fee in (None, "") else "{} sat/vB".format(fee),
    )
    version = history.by_vid().get(history.current) if history.current else None
    if version is not None:
        levels = _levels(version)
        by_date = {inst.locktime: inst for inst in future.deliveries}
        for number, ids in enumerate(levels, 1):
            items = {wid: will[wid] for wid in ids if wid in will}
            locktimes = [t.locktime for t in map(_tx, items.values())]
            locktime = min(locktimes) if locktimes else 0
            inst = by_date.get(locktime)
            report.instalments.append(ReportInstalment(
                number=number, locktime=locktime,
                heirs=list(inst.heirs) if inst else [],
                tx_count=len(items), states=executor_states(items, selected),
            ))
        if levels:
            report.first_txs = sorted(
                (_tx(will[wid]) for wid in levels[0] if wid in will), key=_tx_order)
        report.heirs = _heirs(
            future.deliveries, _addresses(will[w] for w in version.items if w in will))
        report.messages = _messages(will[w] for w in version.items if w in will)
    in_force = set(version.items) if version is not None else set()
    report.older = sorted(
        (_tx(item) for item, _lt, _url in (still_active or [])
         if str(item._id) not in in_force),
        key=lambda t: (t.locktime, t.host or "", t.txid),
    )
    return report


# ---------------------------------------------------------------- gating

def report_block_reason(summary, willitems=None, selected=None):
    """Why the report cannot be made now, or ``None`` when it can.

    Owner's rule (#92): only for a will in force and verified: the green
    "Your will is in force" of the will summary (state ``"ok"``: signed,
    sent, delivery and Check Alive not passed) AND every will-executor of
    ``willitems`` has verified it (the green state also covers "received,
    to be verified"). A will with only a backup transaction (no
    will-executor) is allowed once signed, and the report says that nobody
    else holds it. ``selected`` (#134): a deselected will-executor that never
    received the will is not required.
    """
    states = executor_states(Will.only_valid_list(willitems or {}), selected)
    verified = all(state in ("verified", "deselected") for state in states.values())
    if summary and summary.get("state") == "ok" and verified:
        return None
    return _(
        "The report is available only when your will is in force and "
        "verified by every will-executor. Press Check."
    )
