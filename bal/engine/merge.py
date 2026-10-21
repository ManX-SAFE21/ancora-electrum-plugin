"""The engine's merge brick: bring signed copies back into the will (#158).

"Merge" puts into the live will the transactions of a will file (or of a
single transaction): the way the signatures made on another computer come
back (the offline signing, a second signer of a multisig). Before, it was
written twice, with the same rules:

* the GUI: ``BalWindow.merge_will`` (the Will tab menu "Merge", the Will
  Details window's "Merge", "Merge transaction" from the clipboard or a
  file);
* the command line: ``BalController.merge_will`` (``bal_will_merge``).

The local validity pass that follows a merge (which copies are still
valid, which coins are spent, the signature counts) was written three
times: in those two and in the QR / audio import
(``dialogs._local_validity_pass``). Both live here now.

Owner decisions (#158):

* a copy is marked signed (COMPLETE) only when its transaction is really
  signed. Before, the "signed" status was copied from the file first: a
  file saying "signed" with an unsigned transaction inside (an old file,
  a damaged one) marked the copy signed, and the Check then sent it
  unsigned to its will-executor;
* the signatures of a multisig add up (1 of 2 here + 1 of 2 in the file =
  2 of 2), as before;
* after a merge the owner is told what came in (:func:`merge_summary`).

No Qt, no ``bal.cli`` (``bal/engine`` rules, see ``bal/engine/__init__``).
"""

from electrum.logging import get_logger

from ..core import trace
from ..core.checkalive import resolve_date_to_check
from ..core.util import Util
from ..core.will import Will
from ..i18n import _

__all__ = [
    "MergeSummary",
    "local_validity_pass",
    "merge_items",
    "merge_summary",
    "merge_will",
]

_logger = get_logger(__name__)

# The statuses a merged copy takes from the file when the file has them.
# COMPLETE is not here on purpose: it follows the transaction (#158).
_CARRIED = ("PUSHED", "CHECKED", "MEMPOOL", "CONFIRMED")


def _is_complete(tx):
    """True when *tx* is fully signed (``False`` when it cannot be read)."""
    try:
        return bool(tx is not None and tx.is_complete())
    except Exception:
        return False


def _signed_count_full(wi):
    """A signed copy counts all its signatures (e.g. "2 of 2").

    The count read from a finalized transaction is 0 (the signatures moved
    into the witness), so after a merge a signed copy kept its old count
    ("1 of 2") although it was complete.
    """
    required = int(getattr(wi, "sigs_required", 0) or 0)
    if required and int(getattr(wi, "sigs_have", 0) or 0) < required:
        wi.sigs_have = required


def merge_items(willitems, imported):
    """Merge the copies *imported* into *willitems* (the rules, no check).

    For a copy already in the will, the live item is kept (never
    replaced): it takes the statuses sent / verified / in the mempool /
    confirmed that the imported copy has. When the live copy is not signed
    yet, the imported transaction is merged into it (the signatures are
    combined when both are the same transaction, otherwise the transaction
    is replaced); a signed live copy is left as it is. It becomes signed
    only when the resulting transaction is complete.

    A new copy is added as it is, except that "signed" is dropped when its
    transaction is not complete.

    Returns the ids of *imported*, in order.
    """
    for wid, wi in imported.items():
        if wid in willitems:
            live = willitems[wid]
            was_complete = live.get_status("COMPLETE")
            for status in _CARRIED:
                if wi.get_status(status):
                    live.set_status(status, True)
            if not was_complete:
                try:
                    if live.tx.txid() == wi.tx.txid():
                        live.tx.combine_with_other_psbt(wi.tx)
                    else:
                        live.tx = wi.tx
                except Exception:
                    live.tx = wi.tx
                if _is_complete(live.tx):
                    live.set_status("COMPLETE", True)
            if live.get_status("COMPLETE"):
                _signed_count_full(live)
        else:
            if wi.get_status("COMPLETE") and not _is_complete(wi.tx):
                # WHY: the file says signed, the transaction is not (#158).
                _logger.info(f"merge: {wid} is marked signed but is not")
                wi.set_status("COMPLETE", False)
            if wi.get_status("COMPLETE"):
                _signed_count_full(wi)
            willitems[wid] = wi
    return list(imported)


def local_validity_pass(wallet, history_label, items, date_to_check):
    """Local, wallet-only validity check of *items* (no server, no expiry).

    Recomputes the valid / invalidated / replaced statuses from the
    wallet's coins and refreshes the signature counts. Errors propagate:
    each caller shows or logs them.
    """
    Will.add_willtree(items)
    all_utxos = Util.get_available_utxos(
        wallet,
        history_label,
        Will.get_min_locktime(items, default_value=date_to_check),
    )
    Will.check_invalidated(items, Will.utxos_strs(all_utxos), wallet)
    Will.search_rai(
        Will.get_all_inputs(items, only_valid=True), all_utxos, items, wallet
    )
    Will.check_signatures(items, wallet)


def merge_will(host, imported):
    """Merge *imported* into the will of *host*, then check it locally.

    *host* is the GUI window or the command-line controller (``wallet``,
    ``willitems``, ``bal_plugin``, ``will_settings``, ``date_to_check``,
    ``save_willitems``). The reference date is set when the host has none
    yet (a merge can be the first action of a session). The will is saved
    after the merge and again after the check. Returns
    ``(ids, error)``: the merged ids and the error of the check, if any
    (the merge itself is kept).

    #173: the command trace gets ``bal_will_import_merge`` with how many
    copies, then the outcome.
    """
    trace.command("bal_will_import_merge", _("{} copies").format(_count(imported)))
    if getattr(host, "date_to_check", None) is None:
        host.date_to_check = resolve_date_to_check(
            host.bal_plugin.is_basic_mode(), host.will_settings
        )
    ids = merge_items(host.willitems, imported)
    Will.normalize_will(host.willitems, host.wallet)
    host.save_willitems()
    error = None
    try:
        local_validity_pass(
            host.wallet,
            host.bal_plugin.HISTORY_LABEL.get(),
            host.willitems,
            host.date_to_check,
        )
    except Exception as e:
        _logger.error(f"merge: validity check failed: {e!r}")
        error = e
    host.save_willitems()
    if error is None:
        trace.add(trace.OK, _("{} copies merged").format(_count(ids)))
    else:
        trace.add(trace.WARNING, _("{count} copies merged, check failed: {reason}").format(
            count=_count(ids), reason=trace.reason(error)))
    return ids, error


def _count(items):
    """How many *items* there are, for the trace; ``"?"`` when unknown (#173)."""
    try:
        return len(items)
    except TypeError:
        return "?"


class MergeSummary:
    """What a merge brought, among the merged copies still valid.

    * ``signed``: signed copies;
    * ``partial``: copies with some signatures but not all, as
      ``(have, required)``;
    * ``unsigned``: copies with no signature yet.
    """

    def __init__(self):
        """Nothing counted yet."""
        self.signed = 0
        self.partial = []
        self.unsigned = 0


def merge_summary(willitems, ids):
    """Count the copies *ids* of *willitems* that are valid (see the class)."""
    out = MergeSummary()
    for wid in ids:
        wi = willitems.get(wid)
        if wi is None or not wi.get_status("VALID"):
            continue
        if wi.get_status("COMPLETE"):
            out.signed += 1
            continue
        have = int(getattr(wi, "sigs_have", 0) or 0)
        required = int(getattr(wi, "sigs_required", 0) or 0)
        if have and required > have:
            out.partial.append((have, required))
        else:
            out.unsigned += 1
    return out
