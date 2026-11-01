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
* :func:`file_summary`: what the copies to sign really pay, read from their
  outputs and sorted by delivery (the heirs, the will-executors, the change,
  the miner fee), and why they must not be signed, if so (see #209 below);
* :func:`sign_file`: sign every copy still to sign
  (:func:`bal.engine.sign.sign_will`, the one signing of #157), unless
  :func:`payment_problems` refuses the file;
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

#209 (owner): the multisig protects from a hacked online computer, which
holds a valid key and prepares this file, so the offline computer's window
is the real security check and must not trust the file. Before, it listed
the file's heirs and hid any output that was not one of them: a file whose
money was redirected looked the same as a good one. Now every output of
every copy to sign is listed and classified: an heir, a will-executor, the
change (only if THIS wallet recognizes the address), or a problem that stops
the signing (:func:`payment_problems`). A hacker can also rewrite the names
in the file, so a stop is not the whole defence: the address on paper is the
reference, and the window asks the user to compare them.

No Qt, no ``bal.cli`` (``bal/engine`` rules, see ``bal/engine/__init__``).
"""

from dataclasses import dataclass, field
from typing import List, Optional

from electrum.logging import get_logger

from ..core import delivery as core_delivery
from ..core.heirs import (
    HEIR_ADDRESS,
    HEIR_REAL_AMOUNT,
    create_op_return_script,
    get_op_return_hex,
    is_op_return_address,
    message_text,
)
from ..core.will import Will
from ..i18n import _
from .sign import SignResult, sign_targets, sign_will, signing_outcome, to_sign

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
    "UNKNOWN_ADDRESS",
    "LESS_THAN_DECLARED",
    "TWO_ADDRESSES",
    "NO_DATE",
    "UNREADABLE",
    "DeliveryLines",
    "FileSummary",
    "HeirLine",
    "OutputLine",
    "PaymentProblem",
    "file_problem",
    "file_summary",
    "next_step",
    "payment_problems",
    "prepare_problem",
    "problem_lines",
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

#: #209: why a copy must not be signed (:attr:`PaymentProblem.kind`).
#: An output pays an address that is no heir, no will-executor and not this
#: wallet's.
UNKNOWN_ADDRESS = "unknown_address"
#: An heir receives less than the file says.
LESS_THAN_DECLARED = "less_than_declared"
#: One person has two addresses in the same delivery.
TWO_ADDRESSES = "two_addresses"
#: The copy could be sent at any time: no date, or its inputs turn it off.
NO_DATE = "no_date"
#: The copy cannot be read, so nothing can be said about what it pays.
UNREADABLE = "unreadable"

#: An input with this sequence number makes Bitcoin ignore the locktime.
_SEQUENCE_FINAL = 0xFFFFFFFF
#: The name of an heir row that is really a will-executor's fee.
_EXECUTOR_HEIR = 'w!ll3x3c"'


@dataclass
class HeirLine:
    """One heir of one delivery, as the offline window shows it.

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
class OutputLine:
    """A will-executor's fee or the change of one delivery.

    ``url``: the will-executor (``None`` for the change). ``sats_max``: as
    in :class:`HeirLine`.
    """

    address: str
    sats: int
    sats_max: Optional[int] = None
    url: Optional[str] = None


@dataclass
class DeliveryLines:
    """What the copies of one delivery really pay (see :func:`file_summary`).

    ``locktime``: a UNIX time, or a block height below 500 000 000;
    ``backup``: one of the copies is the backup, with no will-executor;
    ``miner_fee`` / ``miner_fee_max``: the smallest and the largest fee of
    the copies (``None`` when it cannot be read).
    """

    locktime: int
    heirs: List[HeirLine] = field(default_factory=list)
    executors: List[OutputLine] = field(default_factory=list)
    change: List[OutputLine] = field(default_factory=list)
    messages: List[str] = field(default_factory=list)
    backup: bool = False
    miner_fee: Optional[int] = None
    miner_fee_max: Optional[int] = None


@dataclass(frozen=True)
class PaymentProblem:
    """Why a copy must not be signed: ``kind`` is one of the constants above.

    ``address``: the address concerned (the output script for an output with
    no address); ``name``: the heir concerned.
    """

    kind: str
    address: str = ""
    name: str = ""


@dataclass
class FileSummary:
    """What the copies to sign pay (see :func:`file_summary`).

    ``deliveries``: earliest first; ``problems``: not empty when the file
    must not be signed; ``to_sign`` / ``copies``: how many copies are still
    to sign, out of the valid ones.
    """

    deliveries: List[DeliveryLines] = field(default_factory=list)
    problems: List[PaymentProblem] = field(default_factory=list)
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


def _widen(line, sats):
    """Make *line* (``sats``, ``sats_max``) cover the amount *sats* too.

    Each copy pays its own will-executor's fee, so an amount may differ a
    little between the copies of a delivery.
    """
    if sats is None or line.sats is None:
        return
    low = min(line.sats, sats)
    high = max(line.sats_max or line.sats, sats)
    line.sats, line.sats_max = low, (high if high != low else None)


def _is_own(wallet, output):
    """True when *wallet* itself recognizes the address of *output*. Never raises.

    Electrum's own test, on a fresh copy of the transaction: an address of
    the wallet, or one it derives again from the path written in the file
    (a change address can be beyond the addresses this computer generated,
    because it never goes online). The path alone proves nothing: Electrum
    derives the address from this wallet's keys and compares it, so a hacked
    file cannot pass another address off as the user's own.
    """
    try:
        wallet.add_output_info(output)
        return bool(output.is_mine)
    except Exception as e:
        _logger.info(f"offline sign: output not recognized: {e!r}")
        return False


def _read_rows(item):
    """The rows of *item*'s file, by kind: what the file SAYS the copy pays.

    Returns ``(people, declared, executors, messages)``. ``people``:
    ``[(person, address, amount or None)]``, ``None`` for a row with no
    amount, such as an heir below the dust limit (#168: the person, never the
    row of a later delivery); ``declared``: ``{address: sats}`` the heirs must
    receive; ``executors``: ``{address: url}``, the backup copy has none;
    ``messages``: ``{output script in hex: text}`` of the OP_RETURN messages.
    """
    people, declared, executors, messages = [], {}, {}, {}
    we = item.we or {}
    if we.get("address"):
        executors[we["address"]] = we.get("url")
    for name, heir in (item.heirs or {}).items():
        row = list(heir) + [None] * 4
        address = str(row[HEIR_ADDRESS] or "")
        if str(name).startswith(_EXECUTOR_HEIR):
            # The row's name is w!ll3x3c"<url>"<locktime>.
            executors.setdefault(address, str(name).split('"')[1])
            continue
        if not address:
            continue
        if is_op_return_address(address):
            script = create_op_return_script(get_op_return_hex(address)).hex()
            messages[script] = message_text(address)
            continue
        try:
            amount = int(row[HEIR_REAL_AMOUNT])
        except (TypeError, ValueError):
            amount = None
        people.append((core_delivery.person_name(name), address, amount))
        if amount is not None:
            declared[address] = declared.get(address, 0) + amount
    return people, declared, executors, messages


def _add_output(lines, sats, address, url=None):
    """Add one output to *lines* (:class:`OutputLine`), by address and URL."""
    for line in lines:
        if line.address == address and line.url == url:
            _widen(line, sats)
            return
    lines.append(OutputLine(address=address, sats=sats, url=url))


def _read_outputs(wallet, tx, delivery, problems, rows):
    """Sort every output of *tx* by who gets it; returns ``{address: sats}``
    of the heirs. *rows*: see :func:`_read_rows`.

    Every output is read, whatever the rows say: the rows only give a name
    to an address.
    """
    people, _declared, executors, messages = rows
    heir_addresses = {address for _n, address, _a in people}
    paid = {}
    for output in tx.outputs():
        address, sats = output.address, int(output.value)
        if not address:
            script = output.scriptpubkey.hex()
            if sats == 0 and script in messages:
                if messages[script] not in delivery.messages:
                    delivery.messages.append(messages[script])
            else:
                # No address: money burned, or a script the will never writes.
                problems.append(PaymentProblem(UNKNOWN_ADDRESS, script))
        elif address in heir_addresses:
            paid[address] = paid.get(address, 0) + sats
        elif address in executors:
            _add_output(delivery.executors, sats, address, executors[address])
        elif _is_own(wallet, output):
            _add_output(delivery.change, sats, address)
        else:
            problems.append(PaymentProblem(UNKNOWN_ADDRESS, address))
    return paid


def _read_heirs(delivery, problems, rows, paid):
    """Add the heirs of one copy to *delivery* (*paid*: what they really get)."""
    people, declared, _executors, _messages = rows
    addresses = [address for _n, address, _a in people]
    known = {line.name: line for line in delivery.heirs}
    for name, address, amount in people:
        # Two heirs on one address: the output cannot say whose part is what.
        # No output for an heir with an amount: 0, the truth, not the file's
        # figure; no amount (below the dust limit): nothing to compare.
        if addresses.count(address) > 1:
            sats = amount
        else:
            sats = paid.get(address, None if amount is None else 0)
        if paid.get(address, 0) < declared.get(address, 0):
            problems.append(PaymentProblem(LESS_THAN_DECLARED, address, name))
        line = known.get(name)
        if line is None:
            known[name] = HeirLine(name=name, address=address, sats=sats)
            delivery.heirs.append(known[name])
        elif line.address != address:
            # WHY a stop: the list shows one address per person, so a second
            # one in a single copy of the 39 would stay out of sight.
            problems.append(PaymentProblem(
                TWO_ADDRESSES, "{}, {}".format(line.address, address), name))
        else:
            _widen(line, sats)


def _read_copy(wallet, item, delivery, problems):
    """Add what the copy *item* really pays to *delivery*; *problems* gets why
    not to sign it."""
    # A fresh copy: :func:`_is_own` writes the wallet's data on its outputs.
    # The same text is what ``sign_will_item`` signs.
    tx = Will.get_tx_from_any(str(item.tx))
    rows = _read_rows(item)
    if not tx.locktime or any(
            txin.nsequence == _SEQUENCE_FINAL for txin in tx.inputs()):
        problems.append(PaymentProblem(NO_DATE))
    paid = _read_outputs(wallet, tx, delivery, problems, rows)
    _read_heirs(delivery, problems, rows, paid)
    if not (item.we or {}).get("url"):
        delivery.backup = True
    try:
        fee = item.tx.get_fee()
    except Exception:
        fee = None
    if fee is not None:
        fees = [f for f in (fee, delivery.miner_fee, delivery.miner_fee_max)
                if f is not None]
        delivery.miner_fee, delivery.miner_fee_max = min(fees), max(fees)


def file_summary(wallet, items, targets=None):
    """What the copies *targets* of *items* really pay (a :class:`FileSummary`).

    *targets*: the txids; by default the copies still to sign
    (:func:`bal.engine.sign.to_sign`), the only ones signed here: the user
    must see what the signature is given to. The amounts come from the
    transactions; the copies are grouped by delivery date, because a will in
    several deliveries pays each heir a different amount at each date (before
    #209 the window mixed them into one "at least"). Read only.
    """
    pending = to_sign(items) if targets is None else list(targets)
    summary = FileSummary(copies=len(list(sign_targets(items))),
                          to_sign=len(pending))
    deliveries = {}
    problems = []
    for item in sorted((items[t] for t in pending), key=_host_order):
        try:
            locktime = int(item.tx.locktime)
            delivery = deliveries.setdefault(
                locktime, DeliveryLines(locktime=locktime))
            _read_copy(wallet, item, delivery, problems)
        except Exception as e:
            # Whatever a damaged or hostile file holds, the answer is "stop".
            _logger.info(f"offline sign: copy not read: {e!r}")
            problems.append(PaymentProblem(UNREADABLE))
    summary.deliveries = [deliveries[t] for t in sorted(deliveries)]
    for delivery in summary.deliveries:
        delivery.heirs.sort(key=lambda line: line.name)
    # One line for a problem found in many copies.
    summary.problems = list(dict.fromkeys(problems))
    return summary


def payment_problems(wallet, items):
    """Why the copies still to sign must not be signed: an empty list if none.

    See :class:`PaymentProblem`. An empty list does not mean the addresses
    are the right ones: only the address on paper can say that (#209).
    """
    return file_summary(wallet, items).problems


def problem_lines(problems):
    """The text of each :class:`PaymentProblem`, for the windows."""
    texts = {
        UNKNOWN_ADDRESS: _("Pays an address that is not in the will: {address}"),
        LESS_THAN_DECLARED: _(
            "{name} receives less than the will says, at the address {address}"),
        TWO_ADDRESSES: _("{name} has two different addresses: {address}"),
        NO_DATE: _("A transaction has no delivery date: it could be sent at "
                   "any time."),
        UNREADABLE: _("A transaction of the file cannot be read."),
    }
    return [texts[p.kind].format(address=p.address, name=p.name) for p in problems]


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

    #209: a file with a :func:`payment_problems` is not signed at all (the
    result's ``error`` says why), even if a window lets the call through.
    """
    problems = payment_problems(wallet, items)
    if problems:
        result = SignResult()
        result.error = "\n".join(problem_lines(problems))
        return result, signing_outcome(items)
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
