"""The engine's build brick: build the will, label it, move the date (#119).

Phase 5e, step 5e-07. Before, the same three steps were written in several
places:

* building the will items from the heirs: ``BalWindow.build_will`` (used by
  the Check dialog, the Prepare menu and the automatic rebuild) and
  ``BalController.build_will`` (command line);
* labelling the rebuilt transactions "Safe21 Heartbeat backup transaction"
  in the wallet: the Check dialog, the Prepare menu and the command line;
* moving the stored delivery date to the rebuilt will's date (CHANGELOG
  #107 / #110): the Check dialog, the automatic rebuild and the command line.

They live here now. The differences between the copies are kept as
parameters, so every caller behaves exactly as before; making them agree is
for later steps, after the owner's decisions.

The functions work on a host (:class:`bal.engine.host.WalletHost`). The
build reads ``bal_plugin``, ``wallet``, ``heirs``, ``will_settings``,
``willitems``, ``date_to_check`` and ``no_willexecutor``, and writes
``date_to_check``, ``willexecutors`` and ``willitems``.

No Qt, no ``bal.cli`` (``bal/engine`` rules, see ``bal/engine/__init__``).
"""

import time

from electrum.logging import get_logger

from ..core.checkalive import resolve_date_to_check
from ..core.util import Util, copy_structure
from ..core.will import NoWillExecutorNotPresent, Will, WillItem
from ..core.willexecutors import Willexecutors
from . import context, instalments

_logger = get_logger(__name__)

# The History label of the rebuilt transactions. Stored text: always English,
# never translated.
BACKUP_LABEL = "Safe21 Heartbeat backup transaction"


def build_will(host, *, with_creation_date):
    """Build (or rebuild) the inheritance transactions of ``host``.

    Copied from ``BalWindow.build_will`` (CHANGELOG #119):

    1. drop the stale wallet-LOCAL will placeholders, so their coins are
       available to this build;
    2. re-anchor ``date_to_check`` to the will being built: a (re)build may
       have anticipated the delivery (shorter heir recipes) while
       ``date_to_check`` is still anchored to the OLD built will, and that
       stale anchor as the build filter would block every future delivery
       ("NO_FUTURE_DATE"). Its locktime is the earliest delivery among the
       CURRENT heirs. ``with_creation_date``: also pass the old will's
       creation date to ``resolve_date_to_check`` (the GUI did, the command
       line did not);
    3. refresh the will-executors and raise ``NoWillExecutorNotPresent`` when
       none is selected and valid (unless the user is their own executor);
    4. build the transactions with ``Heirs.get_transactions`` against
       ``date_to_check`` and the coins the will can use. With a table of
       deliveries (#168) on the rows of every delivery
       (:func:`bal.engine.instalments.effective_heirs`): the chain of up to
       three deliveries, each spending the change of the one before;
    5. wrap each one in a ``WillItem`` (same creation time for all) and merge
       them into ``host.willitems`` (``Will.update_will`` drops the unsigned
       drafts, anticipates against the will in force and reuses or brings
       back its items, CHANGELOG #128; then ``Will.normalize_will``). One
       log line says how many drafts were dropped.

    Returns ``host.willitems`` (the whole live will), or ``{}`` when nothing
    was built. Exceptions are logged and raised again.
    """
    _logger.debug("building will...")
    Will.remove_stale_wallet_history(
        host.wallet, host.bal_plugin.HISTORY_LABEL.get()
    )
    # #168: with a table of deliveries the build gets the rows of every
    # delivery (in memory only); without one, host.heirs itself.
    heirs = instalments.effective_heirs(host)
    new_locktime = min(
        (Util.parse_locktime_string(h[2]) for h in heirs.values()),
        default=None,
    )
    if new_locktime:
        host.date_to_check = resolve_date_to_check(
            host.bal_plugin.is_basic_mode(),
            host.will_settings,
            built_locktime=new_locktime,
            creation_date=(
                Will.get_creation_time(host.willitems)
                if with_creation_date
                else None
            ),
        )
    will = {}
    try:
        host.willexecutors = Willexecutors.get_willexecutors(
            host.bal_plugin, update=False, task=False
        )
        if not host.no_willexecutor:
            found = False
            for _u, w in host.willexecutors.items():
                if Willexecutors.is_selected(w) and Willexecutors.is_valid(
                    w,
                    max_fee=host.bal_plugin.MAX_WILLEXECUTOR_FEE.get(),
                    dust=host.wallet.dust_threshold(),
                ):
                    found = True
            if not found:
                _logger.error("No Will-Executor or backup transaction selected")
                raise NoWillExecutorNotPresent(
                    "No Will-Executor or backup transaction selected"
                )
        # date_to_check already carries the reference timestamp for the
        # current mode (the Check Alive in ADVANCED, "now" in BASIC). The
        # available-UTXO view restores coins that a newer, wallet-local will
        # tx (stored in the history with a later locktime) nominally spent.
        txs = _transactions(host, heirs)
        if heirs is not host.heirs:
            # The reason of a build with no transaction is read from
            # host.heirs by the front-ends.
            host.heirs.last_build_error = heirs.last_build_error
        _logger.info(f"txs built: {txs}")
        creation_time = time.time()
        if not txs:
            _logger.info("No transactions was built")
            _logger.info(f"will-settings: {host.will_settings}")
            _logger.info(f"date_to_check:{host.date_to_check}")
            _logger.info(f"heirs: {heirs}")
            return {}
        for txid in txs:
            tx = {}
            tx["tx"] = txs[txid]
            tx["my_locktime"] = txs[txid].my_locktime
            tx["heirsvalue"] = txs[txid].heirsvalue
            tx["description"] = txs[txid].description
            tx["willexecutor"] = copy_structure(txs[txid].willexecutor)
            # Stored history: always English (translated only for display by
            # format_status_history); never _() here.
            tx["status"] = "New"
            tx["baltx_fees"] = txs[txid].tx_fees
            tx["time"] = creation_time
            tx["heirs"] = copy_structure(txs[txid].heirs)
            tx["txchildren"] = []
            will[txid] = WillItem(tx, _id=txid, wallet=host.wallet)
        # CHANGELOG #128: the drafts are dropped, the new will is dated
        # against the will in force only, which may come back.
        merged = Will.update_will(host.willitems, will, wallet=host.wallet)
        if merged["dropped"]:
            _logger.info(f"dropped {len(merged['dropped'])} unsigned drafts")
        if merged["brought_back"]:
            _logger.info(
                f"will in force brought back: {len(merged['brought_back'])} items"
            )
        host.willitems.update(will)
        Will.normalize_will(host.willitems, host.wallet)
    except Exception as e:
        _logger.info(f"Exception build_will: {e}")
        raise
    return host.willitems


def _transactions(host, heirs):
    """Build the will's transactions for ``build_will`` (step 4).

    ``heirs``: the rows to build (``host.heirs``, or the rows of every
    delivery with a table, #168).

    CHANGELOG #141: a will-executor gives a new fee address when the
    user's IP changes (bal-server keeps one per IP), but it accepts every
    address it ever gave, and it broadcasts the copy it holds as it is. So
    a will-executor that received its copy keeps its old payment data
    (``Will.received_terms``) when that gives the same transaction again
    (``Will.same_payments``): nothing to sign or send for it. When the
    copy changes anyway (heirs, fee rate, new coins), it is built again
    with the will-executor's new data.
    """
    utxos = context.available_utxos(host)

    def build(pinned):
        """The transactions with the payment data ``pinned``."""
        return heirs.get_transactions(
            host.bal_plugin,
            host.wallet,
            host.will_settings["baltx_fees"],
            utxos,
            host.date_to_check,
            pinned=pinned,
        )

    pinned = Will.received_terms(host.willitems, host.willexecutors)
    txs = build(pinned)
    if not pinned or not txs:
        return txs
    kept = {
        url: data for url, data in pinned.items()
        if all(
            Will.same_payments(tx, host.willitems, url)
            for tx in txs.values()
            if (getattr(tx, "willexecutor", None) or {}).get("url") == url
        )
    }
    if kept != pinned:
        _logger.info(
            "will-executor copies changed anyway, built with the new "
            f"payment data: {sorted(set(pinned) - set(kept))}"
        )
        txs = build(kept)
    if kept:
        _logger.info(f"received copies kept with their payment data: {sorted(kept)}")
    return txs


def label_rebuilt(host, *, scope, on_error=None):
    """Label the rebuilt transactions in the wallet's History.

    ``scope``: ``"valid"`` labels the VALID items only (the Check dialog and
    the command line), ``"all"`` every item (the Prepare menu). With
    ``on_error`` a label that cannot be written is passed to it and the
    others are still labelled (the command line); without, the error is
    raised (the GUI). The wallet is read only when there is something to
    label.
    """
    if scope == "valid":
        wids = Will.only_valid(host.willitems)
    elif scope == "all":
        wids = list(host.willitems)
    else:
        raise ValueError(f"unknown label scope: {scope!r}")
    for wid in wids:
        try:
            host.wallet.set_label(wid, BACKUP_LABEL)
        except Exception as e:
            if on_error is None:
                raise
            on_error(e)


def sync_delivery_date(host, store, *, on_error=None):
    """Move the stored delivery date to the rebuilt will's date.

    A rebuild that spends the coins of the will already sent (e.g. after new
    funds) anticipates the locktime by one day, so the new transactions are
    valid first; the stored date must follow, or the next check takes it for
    a postponement and asks for an on-chain invalidation (CHANGELOG #107).
    The rule is ``Will.anticipated_delivery_to_store`` (CHANGELOG #110): the
    date only moves EARLIER and only when it is absolute.

    ``store(field, value)`` writes the new date the caller's way (it must
    change ``host.will_settings`` in place). With ``on_error`` an error of
    the rule is passed to it and nothing is stored (the automatic rebuild);
    without, it is raised. Returns the new date, or ``None``.

    With a table of deliveries (#168) the dates of deliveries 2 and 3 move
    earlier the same way (:func:`bal.engine.instalments.sync_later_deliveries`),
    or the next Check would read a postpone (F1 of #166).
    """
    try:
        new_locktime = Will.anticipated_delivery_to_store(
            host.willitems, host.will_settings["locktime"]
        )
    except Exception as e:
        if on_error is None:
            raise
        on_error(e)
        return None
    if new_locktime is not None:
        store("locktime", new_locktime)
    # #168 (F1 of #166): deliveries 2 and 3 of a table follow too.
    try:
        instalments.sync_later_deliveries(host)
    except Exception as e:
        if on_error is None:
            raise
        on_error(e)
    return new_locktime
