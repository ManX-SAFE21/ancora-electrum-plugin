"""The engine's proof of the signatures of a will copy (#210).

Electrum's "complete" and its signature count only look at how many signatures
a transaction holds, not whether they are right. Testnet4, 2026-11-01: a first
signature made invalid in a multisig file still reached "2 of 2", "signed".
The will-executors would then hold a transaction Bitcoin refuses, and the heirs
get nothing, with the owner believing the will is in force.

:func:`check_copy` proves each signature with the keys of THIS wallet and the
coin the transaction spends. What the file says about keys or scripts is never
believed: a signature only counts for a key the wallet derives itself, and it
must cover the whole transaction (SIGHASH_ALL), so nobody can change the
outputs afterwards. A file can also ASK for a weaker signature (a field of the
PSBT, the next signer's wallet obeys it): that counts as bad too.

The coin a signature covers is not the file's word either: the wallet's own
coin first, or a copy of the will whose transaction really has that txid; the
file's value only where nothing else knows the coin, and then a false value
makes a signature Bitcoin refuses, found when the file reaches the computer
that knows it.

A signature that cannot be proven is not counted: refusing a file costs a new
signature, believing a false one can cost the inheritance. It is not called
false either (``unproven``): that computer simply does not know the coin.

No Qt, no ``bal.cli`` (``bal/engine`` rules, see ``bal/engine/__init__``).
"""

from dataclasses import dataclass

from electrum.crypto import sha256d
from electrum.logging import get_logger
from electrum.transaction import (
    PartialTransaction,
    PartialTxOutput,
    Sighash,
    Transaction,
    script_GetOp,
    tx_from_any,
)

from ..core.will import Will

__all__ = ["SignatureCheck", "asks_unsafe_signature", "check_copy"]

_logger = get_logger(__name__)

#: The first byte of a DER signature: tells a signature from the other items
#: of a finished input (an empty item, a key, the multisig script).
_DER_START = 0x30


@dataclass(frozen=True)
class SignatureCheck:
    """What :func:`check_copy` proved.

    ``have`` / ``required``: the valid signatures and the ones needed, over
    all the inputs (0 / 0 for a copy with no signature yet); ``bad``: a
    signature is wrong, or a weak one is asked for, so the copy must not be
    used; ``unproven``: this wallet does not know the coin, so what the copy
    holds cannot be checked here.
    """

    have: int = 0
    required: int = 0
    bad: bool = False
    unproven: bool = False


def _finished_items(txin):
    """The signatures of an input already finished by Electrum (no PSBT data)."""
    items = list(txin.witness_elements()) if txin.witness else []
    if txin.script_sig:
        items += [op[1] for op in script_GetOp(txin.script_sig) if op[1]]
    return [item for item in items if item and item[0] == _DER_START]


def _is_finished(txin):
    """True when *txin* carries a final script or witness, not PSBT signatures."""
    return txin.script_sig is not None or txin.witness is not None


def asks_unsafe_signature(tx):
    """True when an input of *tx* asks for a signature that leaves the outputs
    free. The wallet obeys that field of the PSBT, and only warns about it."""
    return any(getattr(txin, "sighash", None) not in (None, Sighash.ALL)
               for txin in tx.inputs())


def _parent_tx(items, txin):
    """The copy of *items* that creates the coin of *txin*, or ``None``.

    Its transaction is hashed: a file can hold anything under a txid. The
    signatures are left out of the hash, as Bitcoin leaves them out of a segwit
    txid (Electrum's own ``txid()`` answers nothing on an unsigned chain).
    """
    wanted = txin.prevout.txid.hex()
    parent = items.get(wanted)
    if parent is None:
        return None
    tx = tx_from_any(str(parent.tx))
    raw = bytes.fromhex(tx.serialize_to_network(include_sigs=False))
    return tx if sha256d(raw)[::-1].hex() == wanted else None


def _add_coin_info(wallet, items, txin):
    """Give *txin* this wallet's script and the coin it spends (see the module)."""
    file_coin = txin.witness_utxo
    txin.witness_utxo = None
    wallet.add_input_info(txin)
    parent = _parent_tx(items, txin)
    if parent is None:
        if txin.value_sats() is None and file_coin is not None:
            txin.witness_utxo = file_coin
            wallet.add_input_info(txin)
        return
    # A fresh copy: add_output_info writes the wallet's data on the output, and
    # learns an address beyond the ones this computer generated.
    output = parent.outputs()[txin.prevout.out_idx]
    # (A finished parent has plain outputs, with no room for that data.)
    if isinstance(output, PartialTxOutput):
        wallet.add_output_info(output)
    Will.set_input_coin(txin, output)
    # WHY after: only the wallet says which script is its own, never the file.
    txin.script_descriptor = wallet.get_script_descriptor_for_address(output.address)


def _is_valid(tx, index, key, signature):
    """True when *signature* is *key*'s, on the whole of *tx*. Never raises."""
    try:
        # WHY only ALL: another kind lets the outputs change after the
        # signature, and the wallet never makes one.
        return signature[-1] == Sighash.ALL and bool(tx.verify_sig_for_txin(
            txin_index=index, pubkey_bytes=key, sig=signature))
    except Exception as e:
        _logger.info(f"signature not readable: {e!r}")
        return False


def _prove_input(tx, index, source, target):
    """Move the signatures of *source* to the clean input *target*, proving
    them. False when one is wrong, or a finished input is not exactly what its
    signatures make.
    """
    keys = target.script_descriptor.get_all_pubkeys()
    finished = _is_finished(source)
    if finished:
        for signature in _finished_items(source):
            key = next((k for k in keys if _is_valid(tx, index, k, signature)), None)
            if key is None:
                return False
            target.sigs_ecdsa[key] = signature
    else:
        for key, signature in source.sigs_ecdsa.items():
            if key not in keys or not _is_valid(tx, index, key, signature):
                return False
            target.sigs_ecdsa[key] = signature
    if not finished:
        return True
    # WHY rebuild: whatever else a finished input holds would go to Bitcoin
    # too. Only what this wallet's script and the proven signatures make passes.
    return (target.is_complete()
            and Transaction.serialize_witness(target) == source.witness
            and Transaction.input_script(target) == (source.script_sig or b""))


def _holds_signature(txin):
    """True when *txin* has anything that claims to be a signature."""
    return _is_finished(txin) or bool(getattr(txin, "sigs_ecdsa", None))


def _check(wallet, items, item):
    """See :func:`check_copy`, which catches what this raises."""
    tx = tx_from_any(str(item.tx))
    # A plain transaction says "complete" whatever it holds: it must prove it.
    plain = not isinstance(tx, PartialTransaction)
    if asks_unsafe_signature(tx):
        return SignatureCheck(bad=True)
    if not plain and not any(_holds_signature(txin) for txin in tx.inputs()):
        return SignatureCheck()
    clean = PartialTransaction.from_tx(tx) if plain else tx_from_any(str(item.tx))
    bad = False
    for index, (source, target) in enumerate(
            zip(tx.inputs(), clean.inputs(), strict=True)):
        target.sigs_ecdsa = {}
        target.script_sig = target.witness = None
        # WHY: Electrum prefers these fields of the file to the wallet's script.
        target.witness_script = target.redeem_script = None
        _add_coin_info(wallet, items, target)
        if target.script_descriptor is None or target.value_sats() is None:
            return SignatureCheck(unproven=True)
        if not _prove_input(clean, index, source, target):
            bad = True
    have, required = clean.signature_count()
    return SignatureCheck(have=have, required=required, bad=bad)


def check_copy(wallet, items, item):
    """Prove the signatures of the will copy *item* (a :class:`SignatureCheck`).

    *items*: the copies where the coins of later deliveries can be found.
    Read only: the item is not touched. Never raises: whatever a damaged or
    hostile file holds, the answer is "bad". A copy with no signature at all
    is not bad, it is still to sign.
    """
    try:
        return _check(wallet, items, item)
    except Exception as e:
        _logger.info(f"signatures not proven: {e!r}")
        return SignatureCheck(bad=True)
