"""The engine's invalidation brick: build and send the invalidation (#152, #153).

Before, the same call was written in six places:

* the Will tab's "Invalidate will" and the Will Details window
  (``BalWindow.invalidate_will``);
* the automatic invalidation (``BalWindow._auto_invalidate_will``);
* the command line's ``bal_will_invalidate``
  (``BalController.invalidate_will``);
* the Check, for a postponed will and for a will expired only against its
  Check Alive (``BalBuildWillDialog.task_phase1``);
* the Check, for a Check Alive renewal
  (:func:`bal.engine.check.check_variables`).

Each one passed the same values to :meth:`Will.invalidate_will`: the will's
fee rate, the History label setting and the earliest locktime of the will,
with the reference date of the checks as the fallback. They live here now,
so a change of that rule is made in one place.

Sending the signed invalidation was written twice too (#153): the automatic
invalidation (``BalWindow._auto_invalidate_will``) and the Check's "Invalidate
now" (``BalBuildWillDialog.loop_broadcast_invalidating``). Both added the
wallet and network information, set the History label BEFORE broadcasting
and broadcast with a 120 s timeout: :func:`broadcast_invalidation` does it
now. Signing stays with each caller (the automatic one signs without a
password, the Check asks for it), and so do the error messages.

No Qt, no ``bal.cli`` (``bal/engine`` rules, see ``bal/engine/__init__``).
"""

from ..core import trace
from ..core.will import Will
from ..core.will_versions import INVALIDATION_LABEL
from ..i18n import _

__all__ = ["INVALIDATION_LABEL", "broadcast_invalidation", "build_invalidation"]

# How long the broadcast may take, in seconds (both the request and the wait
# for it), as in both old copies.
BROADCAST_TIMEOUT = 120


def build_invalidation(host, will=None):
    """Build the unsigned invalidation transaction of *host*'s will.

    *host* is a :class:`bal.engine.host.WalletHost`; it reads ``wallet``,
    ``willitems``, ``will_settings`` (``baltx_fees``, default 1 sat/byte),
    ``bal_plugin.HISTORY_LABEL`` and ``date_to_check`` (``None`` when the
    host has none yet). *will* is the will to invalidate, as
    ``{txid: WillItem}``; ``None`` means ``host.willitems``.

    Returns what :meth:`Will.invalidate_will` returns: a
    ``PartialTransaction``, or ``None`` when there is nothing to invalidate.
    Its errors propagate; nothing is signed, labelled or broadcast here.

    #173: the command trace gets ``bal_will_invalidate`` and its outcome.
    """
    trace.command("bal_will_invalidate")
    willitems = will if will is not None else host.willitems
    try:
        tx = Will.invalidate_will(
            willitems,
            host.wallet,
            host.will_settings.get("baltx_fees", 1),
            history_label=host.bal_plugin.HISTORY_LABEL.get(),
            will_locktime=Will.get_min_locktime(
                willitems,
                default_value=getattr(host, "date_to_check", None),
            ),
        )
    except Exception as e:
        trace.add(trace.ERROR, _("not built: {}").format(trace.reason(e)))
        raise
    if tx is None:
        trace.add(trace.OK, _("nothing to invalidate"))
    else:
        trace.add(trace.OK, _("invalidation transaction built, to sign and send"))
    return tx


def broadcast_invalidation(wallet, network, tx):
    """Label and broadcast the signed invalidation *tx*; return its txid.

    The steps of both old copies, in the same order: the information of
    *wallet* and *network* is added to *tx*, the History label
    ``INVALIDATION_LABEL`` is set, and *tx* is broadcast through *network*
    (timeout ``BROADCAST_TIMEOUT``).

    The label is set BEFORE broadcasting: ``set_label`` only writes the
    wallet's own metadata, so the label is there the moment the transaction
    appears in the History tab (task #21: the txid is read from *tx*, since
    ``Network.broadcast_transaction`` always returns ``None``). A *tx*
    without a txid is broadcast unlabelled and ``None`` is returned.

    Errors propagate (``TxBroadcastError``, ``BestEffortRequestFailed``, ...):
    each caller reports them in its own way.

    #173: the command trace gets Electrum's own ``broadcast`` command (the
    plugin has none for it) with the shortened txid, then the outcome.
    """
    trace.command("broadcast", _("invalidation {}").format(
        trace.short_txid(tx.txid())))
    try:
        txid = _broadcast(wallet, network, tx)
    except Exception as e:
        trace.add(trace.ERROR, _("not sent: {}").format(trace.reason(e)))
        raise
    trace.add(trace.OK, _("sent to the network"))
    return txid


def _broadcast(wallet, network, tx):
    """The steps of :func:`broadcast_invalidation` (see its docstring)."""
    tx.add_info_from_wallet(wallet)
    network.run_from_another_thread(tx.add_info_from_network(network))
    txid = tx.txid()
    if txid:
        wallet.set_label(txid, INVALIDATION_LABEL)
    network.run_from_another_thread(
        network.broadcast_transaction(tx, timeout=BROADCAST_TIMEOUT),
        timeout=BROADCAST_TIMEOUT,
    )
    return txid
