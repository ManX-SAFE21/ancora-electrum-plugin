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

#161 (owner): the online computer's two steps of the same route, guided by
the same Tools entry (the owner found "Export, choose a filter, save,
carry, Merge, Check" too complicated):

* step 1, "Prepare the USB stick": :func:`prepare_problem` (nothing to
  carry: no will, or already signed) and :func:`stick_items` (only the
  copies still to sign, the file the offline computer signs);
* step 3, "Load the signed will and send": :func:`signed_file_problem`
  (not this wallet's will, or the file still to sign chosen by mistake).

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
    "NO_WILL",
    "NOT_THIS_WALLET",
    "NOT_SIGNED",
    "STEP_PREPARE",
    "STEP_CARRY",
    "STEP_SIGN",
    "STEP_BRING_BACK",
    "STEP_SEND",
    "FileSummary",
    "HeirLine",
    "file_problem",
    "file_summary",
    "next_step",
    "prepare_problem",
    "sign_file",
    "signed_file_data",
    "signed_file_problem",
    "stick_items",
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
#: #161, step 1: the wallet has no valid will to carry (none built yet, or
#: the Check could not build it).
NO_WILL = "no_will"
#: #161, step 3: the file's copies do not spend this wallet's coins.
NOT_THIS_WALLET = "not_this_wallet"
#: #161, step 3: the file holds no signature: it is the file still to sign
#: (step 1's file), chosen by mistake instead of the signed one.
NOT_SIGNED = "not_signed"

#: #161 (owner): the five steps of the route, the numbers of the windows'
#: step bar and of the Will tab's big button (see :func:`next_step`):
#: prepare the USB stick (online), take it to the offline computer, sign
#: (offline), bring it back to the online computer, send (online). Steps 2
#: and 4 only move the stick: they have no button.
STEP_PREPARE = 1
STEP_CARRY = 2
STEP_SIGN = 3
STEP_BRING_BACK = 4
STEP_SEND = 5


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


# -- #161: the online computer ------------------------------------------------


def prepare_problem(willitems, selected=None):
    """Why step 1 has nothing to put on the USB stick, or ``None``.

    *willitems* is the wallet's own will (after the Check that prepares
    it). :data:`NO_WILL`: no valid copy; :data:`ALREADY_SIGNED`: every
    valid copy is signed, nothing to carry. *selected*: the URLs of the
    selected will-executors, so a copy of a deselected one that never
    received the will is not "to sign" (the Check's own rule, #134/#159).
    """
    if not list(sign_targets(willitems, selected=selected)):
        return NO_WILL
    if not to_sign(willitems, selected=selected):
        return ALREADY_SIGNED
    return None


def stick_items(willitems, selected=None):
    """The copies to put on the USB stick: ``{txid: item}``, still to sign.

    Only the valid copies not signed yet (a multisig copy with some of its
    signatures included: the next signer adds the others). The signed ones
    stay on this computer: nothing to sign there (owner, #161: "only the
    transactions to sign").
    """
    return {t: willitems[t] for t in to_sign(willitems, selected=selected)}


def next_step(watch_only, willitems, selected=None, has_funds=None):
    """The step of the route the user has to make now, or ``None`` (#161).

    The owner found the window not guided enough: the same "Sign the will
    offline" on the online computer prepared the stick or sent the will.
    The Will tab's big button now says and does only the next step that
    has a button (1, 3 or 5):

    * a watch-only wallet (the online computer): :data:`STEP_PREPARE` when
      some copies to sign are not on a USB stick yet (not EXPORTED: a new
      or changed will), else :data:`STEP_SEND` (the stick is out to be
      signed, by one or more cosigners of a multisig); ``None`` when
      nothing is to sign (no will, or the will is signed: in force);
    * a wallet with keys: :data:`STEP_SIGN` when it shows no balance (the
      offline computer, *has_funds* False, the rule of #160), else
      ``None`` (a wallet with bitcoin signs its own will directly).
    """
    if not watch_only:
        return STEP_SIGN if has_funds is False else None
    if prepare_problem(willitems, selected) is not None:
        return None
    pending = to_sign(willitems, selected=selected)
    if all(willitems[t].get_status("EXPORTED") for t in pending):
        return STEP_SEND
    return STEP_PREPARE


def _fresh_tx(wallet, item):
    """A copy of *item*'s transaction with this wallet's data on its inputs.

    A copy, so the item's own transaction is not touched; the wallet data
    (scripts, keys' paths) lets Electrum count the signatures and read the
    inputs' addresses.
    """
    tx = Will.get_tx_from_any(str(item.tx))
    try:
        tx.add_info_from_wallet(wallet)
    except Exception as e:
        _logger.info(f"offline sign: wallet info not added: {e!r}")
    return tx


def _spends_this_wallet(wallet, item):
    """True when every input of *item* is a coin of *wallet*. Never raises.

    The address of an input comes from the transaction when it carries it
    (a partly signed one), otherwise from the wallet's own history
    (Electrum's ``get_txin_address``): a fully signed transaction holds no
    address, and the coins it spends are in this wallet's history.
    """
    try:
        inputs = _fresh_tx(wallet, item).inputs()
        addresses = [wallet.adb.get_txin_address(txin) for txin in inputs]
        return bool(addresses) and all(
            address and wallet.is_mine(address) for address in addresses)
    except Exception as e:
        _logger.info(f"offline sign: inputs not read: {e!r}")
        return False


def _has_signature(wallet, item):
    """True when *item*'s transaction holds at least one signature.

    A complete transaction has them all; otherwise Electrum's count of the
    partial signatures (``signature_count``). Never raises.
    """
    try:
        tx = _fresh_tx(wallet, item)
        if tx.is_complete():
            return True
        have, _required = tx.signature_count()
        return have > 0
    except Exception as e:
        _logger.info(f"offline sign: signatures not counted: {e!r}")
        return False


def signed_file_problem(wallet, items):
    """Why step 3 must not merge the will file *items*, or ``None``.

    In this order: :data:`NOTHING_TO_SIGN` (no valid copy: not a will
    anymore), :data:`NOT_THIS_WALLET` (the copies spend another wallet's
    coins), :data:`NOT_SIGNED` (no signature at all: the file of step 1,
    chosen instead of the signed one). A file with signatures that this
    will already has is accepted: merging it changes nothing and the Check
    after it sends what is still to send.
    """
    valid = [items[t] for t in sign_targets(items)]
    if not valid:
        return NOTHING_TO_SIGN
    if not any(_spends_this_wallet(wallet, item) for item in valid):
        return NOT_THIS_WALLET
    if not any(_has_signature(wallet, item) for item in valid):
        return NOT_SIGNED
    return None
