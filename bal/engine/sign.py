"""The engine's signing brick: sign the will transactions (#154).

Before, the steps that sign one will transaction were written twice:

* the GUI: ``BalWindow._prepare_and_sign_tx`` (the Will tab's "Sign", the
  Check, the Will Details window, the will summary and the review of a will
  imported by QR);
* the command line: the loop of ``BalController.sign_transactions``
  (``bal_will_sign``).

The choice of the transactions to sign (the valid ones, or the valid ones
among the txids asked for) was written twice too. Both live here now, so a
change of those rules is made in one place. What each caller does around
them stays with it: the GUI shows "signing: ..." and leaves the saving to
its caller; the command line checks the signatures, saves the will and the
History at once.

No Qt, no ``bal.cli`` (``bal/engine`` rules, see ``bal/engine/__init__``).
"""

from electrum.logging import get_logger

from ..core.will import Will

__all__ = ["sign_targets", "sign_will_item"]

_logger = get_logger(__name__)


def sign_targets(willitems, txids=None):
    """The txids of *willitems* to sign, in order.

    ``txids=None``: every valid transaction (``Will.only_valid``). Otherwise
    the txids of *txids*, in their order, that are in *willitems* and valid;
    the others are skipped without an error.
    """
    if txids is None:
        return Will.only_valid(willitems)
    return [t for t in txids if t in willitems and willitems[t].get_status("VALID")]


def sign_will_item(wallet, willitems, txid, password):
    """Sign the will transaction *txid* of *willitems* with *wallet*.

    Returns ``(tx, newly_signed)``. *tx* is a fresh copy of the stored
    transaction, re-parsed from its serialized form: a deepcopy would fail
    ("cannot pickle '_thread.RLock'", the stored tx carries wallet-derived
    objects), and re-parsing is how the will is saved and loaded anyway.
    ``newly_signed`` is False when the item was already COMPLETE: then
    nothing is signed and the item is not touched.

    Otherwise each input that spends an output of another transaction of the
    same will (a change of the will) gets that output's value, script
    descriptor, address and script, so the wallet can sign it although the
    parent is not on the blockchain yet. The wallet signs (warnings
    ignored, as before); a complete tx sets the item's COMPLETE status, and
    the item's signature count (``sigs_have`` / ``sigs_required``, e.g.
    "1/2" for a multisig) is read from the freshly signed tx. The item's
    stored ``tx`` is NOT replaced here: each caller decides when.

    Errors of the wallet (a wrong password...) propagate.
    """
    wi = willitems[txid]
    tx = Will.get_tx_from_any(str(wi.tx))
    if wi.get_status("COMPLETE"):
        return tx, False
    for txin in tx.inputs():
        prevout = txin.prevout.to_json()
        if prevout[0] in willitems:
            change = willitems[prevout[0]].tx.outputs()[prevout[1]]
            txin._trusted_value_sats = change.value
            try:
                txin.script_descriptor = change.script_descriptor
            except Exception:
                pass
            txin.is_mine = True
            txin._TxInput__address = change.address
            txin._TxInput__scriptpubkey = change.scriptpubkey
            txin._TxInput__value_sats = change.value

    wallet.sign_transaction(tx, password, ignore_warnings=True)
    if tx.is_complete():
        wi.set_status("COMPLETE", True)
    # The signatures are still in the partial tx here (before any
    # finalization), so the will list can show the real count.
    try:
        have, required = tx.signature_count()
        wi.sigs_have = int(have)
        wi.sigs_required = int(required)
    except Exception as e:
        _logger.debug(f"signature_count after signing failed: {e}")
    return tx, True
