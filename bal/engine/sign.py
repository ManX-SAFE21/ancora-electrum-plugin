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

#157: the step after (sign each chosen transaction, put the signed copy
back into the will, refresh the signature counts) was written five times:
the Will tab's "Sign" (``BalWindow.sign_transactions`` /
``ask_password_and_sign_transactions``), the Check
(``BalBuildWillDialog.task_phase2``), the automatic rebuild
(``BalWindow._auto_sign_save_push``), the command line
(``BalController.sign_transactions``) and the review of an imported will
(``WillTxReviewSignDialog._sign_current``, the offline signing). It is
:func:`sign_will` now. Three faults went with the copies:

* A: a signing that failed half way left the transactions signed before
  it marked COMPLETE with their signatures thrown away (the GUI returned
  nothing on an error), so they were never signed again and the Check
  sent unsigned copies. Now :func:`sign_will_item` stores the signed copy
  in the same step that marks it COMPLETE, and an error stops the loop
  with the ones signed before kept, signed for real;
* F: the "signing: ..." text of the waiting window never showed (the GUI
  loop's ``tx, _ = ...`` hid the translation function ``_``);
* G: the review of an imported will marked a transaction COMPLETE and
  dropped the signed copy, so the "signed file" carried back to the online
  computer held unsigned transactions.

Owner decision (#157): signing "all" skips the copies of will-executors
deselected after the will was built that never received it (the #135 rule
of the Check, now everywhere); the rows the owner chooses (the list's right
click, ``bal_will_sign <txid>``) are signed as chosen.

No Qt, no ``bal.cli`` (``bal/engine`` rules, see ``bal/engine/__init__``).
"""

from electrum.logging import get_logger

from ..core import trace
from ..core.will import Will
from ..i18n import _
from .merge import merge_summary

__all__ = [
    "SignResult",
    "is_deselected_leftover",
    "sign_targets",
    "sign_will",
    "sign_will_item",
    "signing_outcome",
    "to_sign",
]

_logger = get_logger(__name__)


def is_deselected_leftover(w, selected):
    """True when the copy *w* belongs to a deselected will-executor (#135).

    The #134 rule: a copy not signed yet, for a will-executor not in
    *selected* (the URLs selected now) that never received the will (not
    PUSHED), is "deselected", not "to sign". It is left as it is: unsigned,
    it exists only in this wallet and no will-executor holds it, so it
    cannot execute; a later rebuild drops it as a draft (#128), and if that
    will-executor is selected again it is signed and sent then.
    """
    if w.get_status("COMPLETE") or w.get_status("PUSHED"):
        return False
    url = (w.we or {}).get("url")
    return bool(url) and url not in selected


def sign_targets(willitems, txids=None, selected=None):
    """The txids of *willitems* to sign, in order.

    ``txids=None`` ("sign all"): every valid transaction
    (``Will.only_valid``), without the deselected leftovers
    (:func:`is_deselected_leftover`) when *selected* (a set of URLs) is
    given; ``None``: no such rule. Otherwise the txids of *txids*, in their
    order, that are in *willitems* and valid; the others are skipped without
    an error, and *selected* is not used: rows the owner chose are signed as
    chosen (owner, #157).
    """
    if txids is None:
        ids = Will.only_valid(willitems)
        if selected is not None:
            ids = [t for t in ids
                   if not is_deselected_leftover(willitems[t], selected)]
        return ids
    return [t for t in txids if t in willitems and willitems[t].get_status("VALID")]


def to_sign(willitems, txids=None, selected=None):
    """The txids of :func:`sign_targets` not signed yet (not COMPLETE).

    What the Check counts as "to sign" before it asks the password.
    """
    return [t for t in sign_targets(willitems, txids, selected)
            if not willitems[t].get_status("COMPLETE")]


def signing_outcome(willitems, selected=None):
    """How far the will is signed: a :class:`bal.engine.merge.MergeSummary`.

    Counts the copies of "sign all" (:func:`sign_targets`, without the
    deselected leftovers when *selected* is given): ``signed``, ``partial``
    (a multisig waiting for another signer, as ``(have, required)``) and
    ``unsigned`` (no signature: e.g. a watch-only wallet, whose keys are on
    another computer). #159: the Check says "Ok" for the signing only when
    nothing is left to sign (before, it said "Ok" for a watch-only wallet
    that signed nothing, H1, and for a multisig at "1 of 2", H2).
    """
    return merge_summary(willitems, sign_targets(willitems, selected=selected))


class SignResult:
    """What :func:`sign_will` did.

    * ``txs``: ``{txid: tx}`` of every target reached, signed now or
      already complete before;
    * ``signed``: the txids signed now, in order;
    * ``failed`` / ``error``: the txid whose signing raised and the
      exception (``None`` when every target was reached). The targets
      after it were not tried.
    """

    def __init__(self):
        """An empty result: nothing reached, no error."""
        self.txs = {}
        self.signed = []
        self.failed = None
        self.error = None


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
    ignored, as before) and the signed copy (re-parsed, as above) becomes
    the item's stored ``tx`` at once, before a complete tx sets the item's
    COMPLETE status (#157: before, each caller stored it later, and a
    caller that did not, or failed first, left a COMPLETE item holding the
    unsigned tx). A partly signed tx (a multisig) is stored too. The item's
    signature count (``sigs_have`` / ``sigs_required``, e.g. "1/2" for a
    multisig) is read from the freshly signed tx.

    Errors of the wallet (a wrong password...) propagate; the item is then
    not touched.
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
    # The signatures are still in the partial tx here (before any
    # finalization), so the will list can show the real count. WHY first:
    # turning a complete tx into text (``str(tx)`` below) finalizes it, and
    # the count read after that is 0.
    try:
        have, required = tx.signature_count()
        wi.sigs_have = int(have)
        wi.sigs_required = int(required)
    except Exception as e:
        _logger.debug(f"signature_count after signing failed: {e}")
    wi.tx = Will.get_tx_from_any(str(tx))
    if tx.is_complete():
        wi.set_status("COMPLETE", True)
    return tx, True


def sign_will(wallet, willitems, password, txids=None, *, selected=None,
              on_progress=None):
    """Sign the will: every target of :func:`sign_targets`, in order.

    Each one goes through :func:`sign_will_item` (the signed copy stored in
    its item at once). An error stops the loop: the ones signed before stay
    signed and stored, the one that failed and the rest stay to sign, and
    the result says which one and why. Then the signature counts and the
    "partly signed" status of the targets are refreshed
    (``Will.check_signatures``).

    ``on_progress(last_signed, signing)`` is called before each transaction
    not complete yet (``last_signed``: the txid signed just before, or
    ``None``). Nothing is saved here: each caller saves.
    Returns a :class:`SignResult`.

    #173: the command trace gets ``bal_will_sign``, then how many were
    signed now, or which one failed and why (the error's text: never the
    password).
    """
    result = SignResult()
    targets = sign_targets(willitems, txids, selected)
    # Not counted here: *targets* may be any iterable (read once below).
    trace.command("bal_will_sign")
    last = None
    for txid in targets:
        if on_progress is not None and not willitems[txid].get_status("COMPLETE"):
            on_progress(last, txid)
        try:
            tx, newly_signed = sign_will_item(wallet, willitems, txid, password)
        except Exception as e:
            _logger.info(f"sign: {txid} not signed: {e!r}")
            result.failed, result.error = txid, e
            break
        result.txs[txid] = tx
        if newly_signed:
            result.signed.append(txid)
            last = txid
    try:
        Will.check_signatures({t: willitems[t] for t in targets}, wallet)
    except Exception as e:
        _logger.error(f"check_signatures after signing failed: {e}")
    if result.error is not None:
        trace.add(trace.ERROR, _("{txid} not signed: {reason}").format(
            txid=trace.short_txid(result.failed), reason=trace.reason(result.error)))
    else:
        trace.add(trace.OK, _("{} signed now").format(len(result.signed)))
    return result
