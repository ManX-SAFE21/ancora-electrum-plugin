"""
bal.cli.commands
================

CLI commands (``bal_*``) for the Bitcoin After Life plugin.

This module is the *transport layer* of the command-line front-end: every
function is a coroutine decorated with ``@plugin_command`` so Electrum exposes
it as ``bal_<name>`` both on the command line and over JSON-RPC.  The functions
validate their arguments and delegate the real work to
:mod:`bal.cli.controller` (a headless replica of the Qt flows); this module
never imports Qt.

It must stay lightweight: Electrum imports it during the CLI pre-parse
(``run_electrum`` calls ``Plugins(config, cmd_only=True)``) and on every
GUI/daemon startup, before any wallet or network object exists.  The heavy
imports (``bal.core``, the controller) happen lazily inside each command.

Flags (see ``electrum.commands.plugin_command``):

    * ``n`` -> requires a running daemon/network (always set for plugins);
    * ``w`` -> resolves and injects the wallet from the daemon;
    * ``p`` -> requires the wallet password (for signing).
"""

from electrum.commands import plugin_command
from electrum.util import UserFacingException

from .controller import BalController, _user_facing

plugin_name = "bal"


def _controller(plugin, wallet):
    """Build the headless controller, or fail with a clear message."""
    if plugin is None:
        raise UserFacingException("the bal plugin is not enabled in this daemon")
    if wallet is None:
        raise UserFacingException("wallet not loaded")
    return BalController(plugin, wallet)


def _call(plugin, wallet, method, *args, **kwargs):
    controller = _controller(plugin, wallet)
    try:
        return getattr(controller, method)(*args, **kwargs)
    except Exception as e:
        raise _user_facing(e) from e


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
@plugin_command("n", plugin_name)
async def settings_list(self, plugin=None):
    """List all BAL plugin configuration options (key, name and value).

    Returns a JSON object mapping every BAL configuration option (``bal_*``)
    to an object with ``value``, ``default`` and ``name``.
    """
    return _call(plugin, None, "settings_list")


@plugin_command("n", plugin_name)
async def settings_get(self, key, plugin=None):
    """Show the current value of one BAL configuration option.

    arg:str:key:The configuration key (e.g. ``bal_tx_fees``).
    """
    return _call(plugin, None, "settings_get", key)


@plugin_command("n", plugin_name)
async def settings_set(self, key, value, plugin=None):
    """Set a BAL configuration option (booleans, integers, strings, JSON).

    arg:str:key:The configuration key (e.g. ``bal_user_type``).
    arg:str:value:The new value; JSON for object-typed keys such as ``bal_will_settings``.
    """
    return _call(plugin, None, "settings_set", key, value)


@plugin_command("n", plugin_name)
async def settings_reset(self, key, plugin=None):
    """Reset a BAL configuration option to its default value.

    arg:str:key:The configuration key (e.g. ``bal_tx_fees``).
    """
    return _call(plugin, None, "settings_reset", key)


# --------------------------------------------------------------------------- #
# Heirs
# --------------------------------------------------------------------------- #
@plugin_command("nw", plugin_name)
async def heirs_list(self, wallet=None, plugin=None):
    """List the heirs of the current wallet.

    Returns a JSON object mapping heir names to their ``[address, amount,
    locktime]`` values.
    """
    return _call(plugin, wallet, "heirs_list")


@plugin_command("nw", plugin_name)
async def heirs_show(self, name, wallet=None, plugin=None):
    """Show the details of a single heir.

    arg:str:name:The heir name.
    """
    return _call(plugin, wallet, "heirs_show", name)


@plugin_command("nw", plugin_name)
async def heirs_add(self, name, address, amount, locktime=None, wallet=None, plugin=None):
    """Add (or replace) an heir in the current wallet.

    arg:str:name:The heir name.
    arg:str:address:The destination address (or ``OP_RETURN:<hex>`` for an OP_RETURN heir).
    arg:str:amount:The amount in satoshis or a percentage like ``50%%``.
    arg:str:locktime:The delivery locktime (absolute timestamp or ``30d``/``1y``); defaults to the will locktime.
    """
    return _call(plugin, wallet, "heirs_add", name, address, amount, locktime)


@plugin_command("nw", plugin_name)
async def heirs_update(
    self,
    name,
    address=None,
    amount=None,
    locktime=None,
    wallet=None,
    plugin=None,
):
    """Update an existing heir (only the given fields).

    arg:str:name:The heir name.
    arg:str:address:The new destination address.
    arg:str:amount:The new amount in satoshis or a percentage.
    arg:str:locktime:The new delivery locktime.
    """
    return _call(plugin, wallet, "heirs_update", name, address, amount, locktime)


@plugin_command("nw", plugin_name)
async def heirs_delete(self, names, wallet=None, plugin=None):
    """Delete one or more heirs.

    arg:json:names:A JSON array of heir names (e.g. ``["Alice","Bob"]``).
    """
    return _call(plugin, wallet, "heirs_delete", names)


@plugin_command("nw", plugin_name)
async def heirs_import(self, path, wallet=None, plugin=None):
    """Import heirs from a JSON file (validated, merged).

    arg:str:path:Path to the JSON file.
    """
    return _call(plugin, wallet, "heirs_import", path)


@plugin_command("nw", plugin_name)
async def heirs_export(self, path, wallet=None, plugin=None):
    """Export the heirs to a JSON file.

    arg:str:path:Destination file path.
    """
    return _call(plugin, wallet, "heirs_export", path)


# --------------------------------------------------------------------------- #
# Will-Executors
# --------------------------------------------------------------------------- #
@plugin_command("nw", plugin_name)
async def willexecutors_list(self, wallet=None, plugin=None):
    """List the will-executors for the current network.

    Returns a JSON object mapping executor URLs to their records (address,
    base_fee, status, info, selected, ...).
    """
    return _call(plugin, wallet, "willexecutors_list")


@plugin_command("nw", plugin_name)
async def willexecutors_show(self, url, wallet=None, plugin=None):
    """Show the details of a single will-executor.

    arg:str:url:The will-executor URL.
    """
    return _call(plugin, wallet, "willexecutors_show", url)


@plugin_command("nw", plugin_name)
async def willexecutors_add(
    self,
    url,
    address="",
    base_fee=0,
    info=None,
    wallet=None,
    plugin=None,
):
    """Add a new will-executor (not selected by default).

    arg:str:url:The will-executor base URL.
    arg:str:address:The executor fee address for this network.
    arg:int:base_fee:The executor base fee in satoshis.
    arg:str:info:A human-readable description.
    """
    return _call(plugin, wallet, "willexecutors_add", url, address, base_fee, info)


@plugin_command("nw", plugin_name)
async def willexecutors_update(
    self,
    url,
    address=None,
    base_fee=None,
    info=None,
    promo_code=None,
    rename_to=None,
    wallet=None,
    plugin=None,
):
    """Update an existing will-executor (only the given fields).

    arg:str:url:The will-executor URL to update.
    arg:str:address:The new fee address.
    arg:int:base_fee:The new base fee in satoshis.
    arg:str:info:The new description.
    arg:str:promo_code:The new promo code.
    arg:str:rename_to:Optionally move the record to a new URL.
    """
    return _call(
        plugin,
        wallet,
        "willexecutors_update",
        url,
        address,
        base_fee,
        info,
        promo_code,
        rename_to,
    )


@plugin_command("nw", plugin_name)
async def willexecutors_select(
    self, url, value=True, wallet=None, plugin=None
):
    """Select (or deselect) a will-executor.

    arg:str:url:The will-executor URL.
    arg:bool:value:True to select, False to deselect.
    """
    return _call(plugin, wallet, "willexecutors_select", [url], value)


@plugin_command("nw", plugin_name)
async def willexecutors_delete(self, urls, wallet=None, plugin=None):
    """Delete one or more will-executors.

    arg:json:urls:A JSON array of executor URLs (e.g. ``["https://we.example.com"]``).
    """
    return _call(plugin, wallet, "willexecutors_delete", urls)


@plugin_command("nw", plugin_name)
async def willexecutors_ping(self, urls=None, wallet=None, plugin=None):
    """Ping the selected (or the given) will-executor servers.

    Updates status/base_fee/address from each server and saves.  Returns
    ``{url: {status, ok}}``.

    arg:json:urls:Optional JSON array of URLs to ping; defaults to the selected executors.
    """
    return _call(plugin, wallet, "willexecutors_ping", urls)


@plugin_command("nw", plugin_name)
async def willexecutors_download(self, wallet=None, plugin=None):
    """Download the will-executor list from the welist server and merge it.

    Returns the number of records downloaded and the new total.
    """
    return _call(plugin, wallet, "willexecutors_download")


@plugin_command("nw", plugin_name)
async def willexecutors_import(self, path, wallet=None, plugin=None):
    """Import will-executors from a JSON file (``{url: record}``).

    arg:str:path:Path to the JSON file.
    """
    return _call(plugin, wallet, "willexecutors_import", path)


@plugin_command("nw", plugin_name)
async def willexecutors_export(self, path, wallet=None, plugin=None):
    """Export the will-executors to a JSON file.

    arg:str:path:Destination file path.
    """
    return _call(plugin, wallet, "willexecutors_export", path)


# --------------------------------------------------------------------------- #
# Will
# --------------------------------------------------------------------------- #
@plugin_command("nw", plugin_name)
async def will_status(self, wallet=None, plugin=None):
    """Show the current will: per-transaction status, locktime and executors.

    Returns a JSON object with a per-txid detail list and global status counts.
    """
    return _call(plugin, wallet, "will_status")


@plugin_command("nw", plugin_name)
async def will_check(self, wallet=None, plugin=None):
    """Check the local coherence of the will (heirs, executors, fees, locktime).

    Returns ``{"valid": true}`` when coherent, or raises a descriptive error.
    """
    return _call(plugin, wallet, "will_check")


@plugin_command("nw", plugin_name)
async def will_prepare(self, wallet=None, plugin=None):
    """Run the full prepare/inheritance flow (check, rebuild, persist).

    Returns a JSON object with ``result`` (``coherent``, ``rebuilt``,
    ``expired``, ``postponed``) and, when needed, the invalidation
    transaction to sign and broadcast.
    """
    return _call(plugin, wallet, "prepare_will")


@plugin_command("nw", plugin_name)
async def will_autorebuild(self, wallet=None, plugin=None):
    """Run the automatic rebuild flow in one shot (check, rebuild, sign, push).

    The same flow the GUI runs automatically on new wallet transactions:
    the delivery date is anticipated by one day to orphan the old will on-chain
    and, only when the anticipated locktime crosses the Check Alive threshold
    (or the threshold is already in the past), an invalidation transaction is
    returned instead.  Signing needs a passwordless wallet.

    Returns a JSON object with ``result``: ``valid``, ``no_heirs``,
    ``invalidated`` (with ``invalidation_tx``), ``nothing``,
    ``needs_signing`` or ``rebuilt``.
    """
    return _call(plugin, wallet, "auto_rebuild")


@plugin_command("nwp", plugin_name)
async def will_sign(self, txid=None, password=None, wallet=None, plugin=None):
    """Sign the valid, not-yet-complete will transactions (or just one).

    Updates the COMPLETE status and the signature counters and persists.

    arg:str:txid:Optional transaction id to sign; signs all valid ones when omitted.
    """
    txids = [txid] if txid is not None else None
    txs = _call(plugin, wallet, "sign_transactions", password, txids)
    return {wid: str(tx) for wid, tx in txs.items()}


@plugin_command("nw", plugin_name)
async def will_broadcast(
    self, txid=None, force=False, wallet=None, plugin=None
):
    """Send the signed will transactions to their will-executors (in parallel).

    Updates the PUSHED/PUSH_FAIL statuses and persists.  Returns ``{url: status}``.

    arg:str:txid:Optional transaction id to broadcast; all valid+signed ones when omitted.
    arg:bool:force:Force re-pushing transactions already marked as PUSHED.
    """
    txids = [txid] if txid is not None else None
    return _call(plugin, wallet, "push_transactions_to_willexecutors", force, txids)


@plugin_command("nw", plugin_name)
async def will_export(self, path, wallet=None, plugin=None):
    """Export the whole will to a JSON file.

    arg:str:path:Destination file path.
    """
    return _call(plugin, wallet, "export_will", path)


@plugin_command("nw", plugin_name)
async def will_import_merge(self, path, wallet=None, plugin=None):
    """Merge a will file into the current will (PSBTs and statuses are merged).

    arg:str:path:Path to the will JSON file.
    """
    return _call(plugin, wallet, "merge_will_from_file", path)


@plugin_command("nw", plugin_name)
async def will_invalidate(self, wallet=None, plugin=None):
    """Build the on-chain invalidation transaction for the current will.

    Returns ``{txid, tx}`` (or nulls when there is nothing to invalidate); the
    transaction still needs to be signed and broadcast.
    """
    return _call(plugin, wallet, "invalidate_will_command")


@plugin_command("nw", plugin_name)
async def will_check_executor(self, txid=None, wallet=None, plugin=None):
    """Ask the will-executors whether they hold our pushed transactions.

    Runs the searchtx check in parallel, applies the per-item status and
    persists.  Returns ``{txid: {url, pushed, checked, check_fail}}``.

    arg:str:txid:Optional transaction id to check; checks all pending ones when omitted.
    """
    txids = [txid] if txid is not None else None
    return _call(plugin, wallet, "check_transactions", txids)
