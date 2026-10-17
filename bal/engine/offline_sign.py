"""The engine's offline signing of a will file (#160).

The owner's cold-wallet route (analysis of 2026-10-16, #157-#159): the
online computer holds a watch-only wallet, prepares the will and exports it
on a USB stick; the offline computer, with the keys, signs it; the stick
goes back and the online computer merges the signed file and sends it.

Before #160 the offline side was the Will tab's "Import" and the Will
Details window: decades of coloured boxes with the will's whole history,
and buttons that make no sense there ("Merge" would mix the file into the
offline wallet's own will, "Broadcast will" needs the network). The owner
asked for one guided window (Tools > "Sign will offline..."): choose the
file, check what it gives to whom, sign everything, save. This module is
that window's logic, without Qt, so the tests read it directly:

* :func:`file_problem`: why this wallet cannot sign this file (no keys,
  nothing to sign, already signed, another wallet), or ``None``;
* :func:`file_summary`: what the file gives to whom, in plain data (the
  heirs with their real amounts, the delivery date, the will-executors);
* :func:`sign_file`: sign every copy still to sign
  (:func:`bal.engine.sign.sign_will`, the one signing of #157);
* :func:`signed_file_data`: the content of the signed file to carry back
  (the same JSON will format as the export, so "Merge" reads it, #158).

The file's items are a COPY loaded from the stick: the offline wallet's own
will is never read or written here.

No Qt, no ``bal.cli`` (``bal/engine`` rules, see ``bal/engine/__init__``).
"""

from dataclasses import dataclass, field
from typing import List, Optional

from electrum.logging import get_logger

# The real amount paid to each heir and the heirs' addresses, read the way
# the Future view and the PDF report read them (#131/#132), so the three
# never disagree.
from ..core.future import _instalment
from ..core.report import _addresses, _messages, executor_fee
from ..core.will import Will
from .sign import sign_targets, sign_will, signing_outcome, to_sign

__all__ = [
    "NO_KEYS",
    "NOTHING_TO_SIGN",
    "ALREADY_SIGNED",
    "OTHER_WALLET",
    "FileSummary",
    "HeirLine",
    "file_problem",
    "file_summary",
    "sign_file",
    "signed_file_data",
]

_logger = get_logger(__name__)

#: The wallet has no private keys (watch-only): it cannot sign anything.
NO_KEYS = "no_keys"
#: The file holds no valid will transaction (an old will, or not a will).
NOTHING_TO_SIGN = "nothing_to_sign"
#: Every valid copy of the file is already signed: nothing to do here.
ALREADY_SIGNED = "already_signed"
#: This wallet holds no key of the copies still to sign: another wallet's
#: will, or a multisig cosigner that has already signed them.
OTHER_WALLET = "other_wallet"


@dataclass
class HeirLine:
    """One heir as the offline window shows it.

    ``sats``: what the will pays to the heir; ``sats_max`` is set only when
    the copies of the will-executors pay different amounts (each copy pays
    its own will-executor's fee), then the window shows "from - to".
    ``None`` for an heir below the dust limit, who receives nothing.
    """

    name: str
    address: str
    sats: Optional[int]
    sats_max: Optional[int] = None


@dataclass
class FileSummary:
    """What the will file gives to whom (see :func:`file_summary`).

    ``delivery``: the earliest locktime of the copies (a UNIX time, or a
    block height below 500 000 000); ``executors``: ``(url, fee)`` of each
    copy, ``url`` ``None`` for a backup copy; ``to_sign`` / ``copies``: how
    many copies are still to sign, out of the valid ones.
    """

    heirs: List[HeirLine] = field(default_factory=list)
    messages: List[str] = field(default_factory=list)
    delivery: Optional[int] = None
    executors: list = field(default_factory=list)
    copies: int = 0
    to_sign: int = 0


def _signable(wallet, item):
    """True when *wallet* holds a key that can still sign *item*'s tx.

    Electrum's own test (``can_sign``: a key of the wallet for an input of
    the wallet, not signed by it yet), on a fresh copy of the transaction so
    the item's stored copy is not touched. Never raises.
    """
    try:
        tx = Will.get_tx_from_any(str(item.tx))
        return bool(wallet.can_sign(tx))
    except Exception as e:
        _logger.info(f"offline sign: can_sign failed: {e!r}")
        return False


def file_problem(wallet, items):
    """Why *wallet* cannot sign the will file *items*, or ``None``.

    In this order: :data:`NO_KEYS` (a watch-only wallet: the window says
    so before a file is chosen too), :data:`NOTHING_TO_SIGN` (no valid
    copy), :data:`ALREADY_SIGNED` (every valid copy is signed: take the
    file back to the online computer), :data:`OTHER_WALLET` (no copy still
    to sign has a key of this wallet).
    """
    if wallet.is_watching_only():
        return NO_KEYS
    # A list: with no selection rule ``sign_targets`` gives a generator,
    # which is always "true".
    if not list(sign_targets(items)):
        return NOTHING_TO_SIGN
    pending = to_sign(items)
    if not pending:
        return ALREADY_SIGNED
    if not any(_signable(wallet, items[t]) for t in pending):
        return OTHER_WALLET
    return None


def _host_order(item):
    """Sort key: the copies of the will-executors by URL, the backup last."""
    url = (item.we or {}).get("url")
    return (url is None, url or "", str(item._id))


def file_summary(items):
    """What the valid copies of *items* give to whom (a :class:`FileSummary`).

    The heirs and their amounts are read from the transactions (what they
    really pay, the rule of the Future view and the PDF report); the copies
    of the will-executors pay the same heirs, but each one also pays its
    own will-executor's fee, so an amount may differ a little between
    copies: then the line gives the smallest and the largest. Read only.
    """
    valid = sorted((items[t] for t in sign_targets(items)), key=_host_order)
    summary = FileSummary(copies=len(valid), to_sign=len(to_sign(items)))
    if not valid:
        return summary
    addresses = _addresses(valid)
    lines = {}
    for item in valid:
        for heir in _instalment(item).heirs:
            line = lines.get(heir.name)
            if line is None:
                lines[heir.name] = HeirLine(
                    name=heir.name, address=addresses.get(heir.name, ""),
                    sats=heir.sats)
                continue
            if heir.sats is None or line.sats is None:
                continue
            low = min(line.sats, heir.sats)
            high = max(line.sats_max or line.sats, heir.sats)
            line.sats, line.sats_max = low, (high if high != low else None)
    summary.heirs = [lines[name] for name in sorted(lines)]
    summary.messages = _messages(valid)
    locktimes = []
    for item in valid:
        try:
            locktimes.append(int(item.tx.locktime))
        except Exception:
            pass
    summary.delivery = min(locktimes) if locktimes else None
    summary.executors = [((item.we or {}).get("url") or None, executor_fee(item))
                         for item in valid]
    return summary


def sign_file(wallet, items, password, on_progress=None):
    """Sign every valid copy of *items* still to sign, with *wallet*.

    The one signing of the plugin (:func:`bal.engine.sign.sign_will`, #157):
    each signed copy is stored in its item at once, an error stops the loop
    keeping the ones signed before. "Sign all" with no will-executor
    selection rule: the offline computer does not know which will-executors
    the online one selected, and the file holds what the online computer
    exported. Returns ``(result, outcome)``: the
    :class:`bal.engine.sign.SignResult` and the
    :class:`bal.engine.merge.MergeSummary` of the copies afterwards (signed,
    a multisig waiting for another signer, unsigned).
    """
    result = sign_will(wallet, items, password, on_progress=on_progress)
    return result, signing_outcome(items)


def signed_file_data(items):
    """The content of the signed file: ``{txid: item dict}``, all the items.

    The JSON will format of the export (``BalWindow.export_json_file``), so
    the online computer's "Merge" reads it (#158: a copy is marked signed
    there only when its transaction really is).
    """
    return {wid: wi.to_dict() for wid, wi in items.items()}
