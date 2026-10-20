"""
bal.cli.commands
================

CLI commands (``bal_*``) for the Àncòra plugin.

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

import asyncio
from functools import partial

from electrum.commands import plugin_command
from electrum.util import UserFacingException

from ..core.brand import NAME
from ..engine.locks import flow_lock_for
from ..i18n import _
from .controller import BalController, BalSettingsController, _user_facing

plugin_name = "bal"

# How long a command waits for another ``bal_*`` command that is still
# working on the same wallet, in seconds (owner decision, CHANGELOG #116).
BUSY_TIMEOUT = 20


def _controller(plugin, wallet):
    """Build the headless controller, or fail with a clear message."""
    if plugin is None:
        raise UserFacingException("the bal plugin is not enabled in this daemon")
    if wallet is None:
        raise UserFacingException("wallet not loaded")
    return BalController(plugin, wallet)


# CHANGELOG #148: the controller methods that change the wallet's heirs or
# will (or, for a few, the will-executor list). WHY: the plugin window of an
# open wallet keeps its own copies of the heirs and of the will and writes
# them back later (at its next save, and at wallet close, where the close
# Check may even rebuild, sign and send a will from its old heirs). A change
# made by a command (Electrum's Console, or the command line while the GUI is
# open) was then silently overwritten. The commands that only read, and the
# delivery date and fee (one dict shared with the window), stay allowed.
_WALLET_WRITES = frozenset({
    "heirs_add", "heirs_update", "heirs_delete", "heirs_import",
    # #169: the table of the deliveries goes with the heirs (its columns);
    # the window keeps its own view of both.
    "instalments_set", "instalments_clear",
    "prepare_will", "auto_rebuild", "sign_transactions", "broadcast_command",
    "export_will", "merge_will_from_file", "check_transactions",
})

# The will-executor list belongs to the plugin, not to one wallet: every open
# plugin window keeps a copy of it and writes it back (#148).
_EXECUTOR_WRITES = frozenset({
    "willexecutors_add", "willexecutors_update", "willexecutors_select",
    "willexecutors_delete", "willexecutors_ping", "willexecutors_download",
    "willexecutors_import",
})


def _open_windows(plugin):
    """The plugin windows that are open now (Qt GUI; none in a daemon).

    A window stays in ``plugin.bal_windows`` after its wallet closed, with
    ``disable_plugin`` set (``BalWindow.on_close``): it is not counted.
    """
    windows = getattr(plugin, "bal_windows", None) or {}
    return [
        w for w in list(windows.values())
        if not getattr(w, "disable_plugin", True)
    ]


def _refuse_while_window_open(plugin, wallet, method):
    """Refuse ``method`` when an open plugin window would overwrite it (#148).

    A wallet change (:data:`_WALLET_WRITES`) is refused while that wallet's
    plugin window is open; a will-executor list change
    (:data:`_EXECUTOR_WRITES`) while any plugin window is open. Every other
    command, and every command without the GUI, runs as before.
    """
    if method in _WALLET_WRITES:
        if any(getattr(w, "wallet", None) is wallet for w in _open_windows(plugin)):
            raise UserFacingException(_(
                "This wallet is open in the {} window: make this "
                "change there, or close the wallet and use the command line."
            ).format(NAME))
    elif method in _EXECUTOR_WRITES:
        if _open_windows(plugin):
            raise UserFacingException(_(
                "A wallet is open in the {} window: change the "
                "will-executors there, or close the wallets and use the command "
                "line."
            ).format(NAME))


def _call(plugin, wallet, method, *args, **kwargs):
    """Run ``method`` of the wallet's controller; domain errors become messages.

    #148: a change that an open plugin window would overwrite is refused
    first (:func:`_refuse_while_window_open`).
    """
    if plugin is not None and wallet is not None:
        _refuse_while_window_open(plugin, wallet, method)
    controller = _controller(plugin, wallet)
    try:
        return getattr(controller, method)(*args, **kwargs)
    except Exception as e:
        raise _user_facing(e) from e


def _settings_call(plugin, method, *args):
    """Run a settings ``method``: the settings belong to the plugin, so no
    wallet is needed (CHANGELOG #99: ``_call`` with no wallet always failed
    with "wallet not loaded")."""
    if plugin is None:
        raise UserFacingException("the bal plugin is not enabled in this daemon")
    try:
        return getattr(BalSettingsController(plugin), method)(*args)
    except Exception as e:
        raise _user_facing(e) from e


def _call_locked(plugin, wallet, method, *args, **kwargs):
    """:func:`_call` holding the wallet's flow lock (one command at a time).

    Runs on a worker thread. Waits up to ``BUSY_TIMEOUT`` seconds for a
    command that is still working on the same wallet, then gives up with a
    message. Without a wallet :func:`_call` reports "wallet not loaded".
    """
    if wallet is None:
        return _call(plugin, wallet, method, *args, **kwargs)
    lock = flow_lock_for(wallet)
    if not lock.acquire(timeout=BUSY_TIMEOUT):
        raise UserFacingException(
            _(
                "Another command is still working on this wallet: try again "
                "in a moment."
            )
        )
    try:
        return _call(plugin, wallet, method, *args, **kwargs)
    finally:
        lock.release()


async def _off_network_thread(func, *args, **kwargs):
    """Run ``func(*args, **kwargs)`` on a worker thread and await its result.

    WHY (CHANGELOG #116): Electrum runs every command body on its network
    (asyncio) thread. The plugin's calls to the will-executors go through
    ``Network.send_http_on_proxy``, which Electrum forbids on that thread
    (the welist download failed with "must not be called from asyncio
    thread") and which, from the worker threads of the parallel send, check
    and ping, waits for that same thread: the send and the check ended in
    "no answer in time" after 20 seconds (the request left only after the
    command had given up) and the ping blocked the daemon for good. On a
    worker thread the network thread stays free.
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, partial(func, *args, **kwargs))


async def _call_off_loop(plugin, wallet, method, *args, **kwargs):
    """:func:`_call` on a worker thread, one command at a time per wallet."""
    return await _off_network_thread(
        _call_locked, plugin, wallet, method, *args, **kwargs
    )


async def _settings_call_off_loop(plugin, method, *args):
    """:func:`_settings_call` on a worker thread.

    The settings belong to the plugin, not to a wallet, so no wallet lock.
    """
    return await _off_network_thread(_settings_call, plugin, method, *args)


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
@plugin_command("n", plugin_name)
async def settings_list(self, plugin=None):
    """List all BAL plugin configuration options (key, name and value).

    Returns a JSON object mapping every BAL configuration option (``bal_*``)
    to an object with ``value``, ``default`` and ``name``.
    """
    return await _settings_call_off_loop(plugin, "settings_list")


@plugin_command("n", plugin_name)
async def settings_get(self, key, plugin=None):
    """Show the current value of one BAL configuration option.

    The delivery date and the fee rate of a wallet are not plugin options:
    see ``bal_delivery_show``.

    arg:str:key:The configuration key (e.g. ``bal_user_type``).
    """
    return await _settings_call_off_loop(plugin, "settings_get", key)


@plugin_command("n", plugin_name)
async def settings_set(self, key, value, plugin=None):
    """Set a BAL configuration option (booleans, integers, strings, JSON).

    The delivery date and the fee rate of a wallet are not plugin options:
    change them with ``bal_delivery_set`` (e.g. ``--fee 20``).

    arg:str:key:The configuration key (e.g. ``bal_user_type``).
    arg:str:value:The new value; JSON for object-typed keys.
    """
    return await _settings_call_off_loop(plugin, "settings_set", key, value)


@plugin_command("n", plugin_name)
async def settings_reset(self, key, plugin=None):
    """Reset a BAL configuration option to its default value.

    The delivery date and the fee rate of a wallet are not plugin options:
    change them with ``bal_delivery_set``.

    arg:str:key:The configuration key (e.g. ``bal_user_type``).
    """
    return await _settings_call_off_loop(plugin, "settings_reset", key)


# --------------------------------------------------------------------------- #
# Delivery settings of the wallet (CHANGELOG #123)
# --------------------------------------------------------------------------- #
@plugin_command("nw", plugin_name)
async def delivery_show(self, wallet=None, plugin=None):
    """Show the delivery settings of the current wallet.

    Each wallet keeps its own delivery date and fee rate. Returns the date
    (``date``, UTC, and ``locktime``, its UNIX time), the fee rate
    (``baltx_fees``), ``settings_version``, where a migrated date came from
    (``migrated_from``), the old value found then (``legacy_locktime``),
    the note not yet shown (``note``), whether a "which date?" question is
    pending (``choice_pending``, with both dates in ``choice``), the
    date of the will in force (``will_in_force``), and the delivery form
    (``form`` fixed or period, ``period_months``, ``period``,
    ``period_start``; ``locked`` while a will is in force).
    """
    return await _call_off_loop(plugin, wallet, "delivery_show")


@plugin_command("nw", plugin_name)
async def delivery_set(
    self,
    date=None,
    fee=None,
    keep=None,
    confirm_earlier=None,
    months=None,
    wallet=None,
    plugin=None,
):
    """Change the delivery date or the fee rate of the current wallet.

    The delivery date is fixed: a period such as ``1y`` is refused. A date
    earlier than the will in force needs ``--confirm_earlier true``. A
    later date is stored; if the will was already sent, the next
    ``bal_will_prepare`` asks to invalidate it first (on-chain, miner fee).
    ``--keep true`` answers the "which date?" question by keeping the date
    of the prepared will. ``--months N`` changes the period of a wallet in
    the period form (see ``bal_delivery_form``): with no will in force the
    delivery date becomes today + N months, otherwise N applies at the next
    renewal. Returns the settings as ``bal_delivery_show``, with a
    ``message``.

    arg:str:date:The new delivery date, YYYY-MM-DD (00:00 UTC) or a UNIX timestamp.
    arg:int:fee:The new fee rate in sat/vByte (1 to 10000).
    arg:bool:keep:true keeps the date of the prepared will (answers the "which date?" question).
    arg:bool:confirm_earlier:true confirms a date earlier than the will in force.
    arg:int:months:The new period in calendar months, 1 to 240 (period form only).
    """
    return await _call_off_loop(
        plugin, wallet, "delivery_set", date, fee, keep, confirm_earlier, months
    )


@plugin_command("nw", plugin_name)
async def delivery_form(self, form, date=None, months=None, wallet=None, plugin=None):
    """Choose the delivery form of the current wallet: fixed or period.

    ``fixed``: a fixed delivery date (``--date``, or the stored date). ``period``:
    N calendar months (``--months``), renewed by the Check Alive: with no
    will in force the delivery date becomes today + N months. Refused while
    a will is in force: invalidate it first (bal_will_invalidate, on-chain
    fee) and wait until the invalidation is confirmed. Returns the settings
    as ``bal_delivery_show``, with a ``message``.

    arg:str:form:fixed or period.
    arg:str:date:The fixed delivery date, YYYY-MM-DD (00:00 UTC) or a UNIX timestamp (form fixed).
    arg:int:months:The period in calendar months, 1 to 240 (form period).
    """
    return await _call_off_loop(plugin, wallet, "delivery_form", form, date, months)


# --------------------------------------------------------------------------- #
# Staged inheritance: the table of the deliveries (CHANGELOG #169)
# --------------------------------------------------------------------------- #
@plugin_command("nw", plugin_name)
async def instalments_show(self, wallet=None, plugin=None):
    """Show the table of the deliveries of the current wallet (staged inheritance).

    Without a table the wallet has one delivery (``staged`` false). With
    one: ``deliveries`` (each with ``delivery``, ``date``, ``locktime`` and
    the ``shares`` of the whole estate per heir), ``delivery_totals``,
    ``heir_totals``, ``total`` (100%), ``gaps_days`` between the dates,
    ``fits_heirs`` (false when the heirs changed and the table did not:
    ``problem`` says why, and bal_will_prepare stops until it is updated)
    and ``will_items`` (the valid transactions of the will now, per
    delivery). ``deliveries`` can be given back to bal_instalments_set.
    """
    return await _call_off_loop(plugin, wallet, "instalments_show")


@plugin_command("nw", plugin_name)
async def instalments_set(
    self,
    table=None,
    path=None,
    example=None,
    confirm_earlier=None,
    wallet=None,
    plugin=None,
):
    """Write the table of the deliveries of the current wallet (staged inheritance).

    Two or three delivery dates, the same for every heir, at least 28 days
    apart; at each date each heir receives a share of the WHOLE estate; the
    shares of the whole table add up to exactly 100% (an heir may have 0%
    at a date). Give the table with ``--table`` (JSON) or ``--path`` (a JSON
    file), or ``--example true`` for the example table (33%, 33%, 34% split
    among the heirs, one year apart, from the current delivery date).
    Delivery 1 becomes the wallet's delivery date. A table by which the heirs
    would receive part of the estate earlier than now (a date moved earlier,
    shares moved to an earlier delivery, delivery 3 removed) needs
    ``--confirm_earlier true``. Nothing is written when a rule is broken. ADVANCED mode and the fixed delivery form only.
    Returns the table as ``bal_instalments_show``, with a ``message``.

    arg:json:table:The table, e.g. [{"date": "2030-03-01", "shares": {"Luca": "20%%", "Anna": "10%%"}}, {"date": "2031-03-01", "shares": {"Luca": "40%%", "Anna": "30%%"}}].
    arg:str:path:Path to a JSON file holding the table (the same form as --table).
    arg:bool:example:true writes the example table (33%%, 33%%, 34%%, one year apart).
    arg:bool:confirm_earlier:true confirms that the heirs may receive part of the estate earlier.
    """
    # The "%%" above: Electrum gives these lines to argparse as help texts,
    # which are %-formatted when the help is printed (see bal_heirs_add).
    return await _call_off_loop(
        plugin, wallet, "instalments_set", table, path, example, confirm_earlier
    )


@plugin_command("nw", plugin_name)
async def instalments_clear(self, confirm_earlier=None, wallet=None, plugin=None):
    """Remove the table of the deliveries: one delivery date again.

    Every heir then receives everything at the wallet's delivery date (the
    date of delivery 1), with the amounts of the heirs (bal_heirs_list),
    which the table had replaced: check them before bal_will_prepare. The
    later deliveries then arrive earlier: ``--confirm_earlier true`` is
    needed. Returns ``removed`` and the heirs, with a ``message``.

    arg:bool:confirm_earlier:true confirms that the heirs may receive the rest of the estate at delivery 1.
    """
    return await _call_off_loop(plugin, wallet, "instalments_clear", confirm_earlier)


# --------------------------------------------------------------------------- #
# Heirs
# --------------------------------------------------------------------------- #
@plugin_command("nw", plugin_name)
async def heirs_list(self, wallet=None, plugin=None):
    """List the heirs of the current wallet.

    Returns a JSON object mapping heir names to their ``[address, amount,
    locktime]`` values.
    """
    return await _call_off_loop(plugin, wallet, "heirs_list")


@plugin_command("nw", plugin_name)
async def heirs_show(self, name, wallet=None, plugin=None):
    """Show the details of a single heir.

    arg:str:name:The heir name.
    """
    return await _call_off_loop(plugin, wallet, "heirs_show", name)


@plugin_command("nw", plugin_name)
async def heirs_add(self, name, address, amount, locktime=None, wallet=None, plugin=None):
    """Add (or replace) an heir in the current wallet.

    arg:str:name:The heir name.
    arg:str:address:The destination address (or ``OP_RETURN:<hex>`` for an OP_RETURN heir).
    arg:str:amount:The amount in satoshis or a percentage like ``50%%``.
    arg:str:locktime:Not accepted (refused with a message): every heir gets the delivery date of the will, see bal_delivery_set and bal_instalments_set.
    """
    return await _call_off_loop(plugin, wallet, "heirs_add", name, address, amount, locktime)


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
    arg:str:locktime:Not accepted (refused with a message): every heir gets the delivery date of the will, see bal_delivery_set and bal_instalments_set.
    """
    return await _call_off_loop(plugin, wallet, "heirs_update", name, address, amount, locktime)


@plugin_command("nw", plugin_name)
async def heirs_delete(self, names, wallet=None, plugin=None):
    """Delete one or more heirs; ``not_found`` lists the unknown names.

    arg:str:names:A heir name, or a JSON array of names (e.g. ``["Alice","Bob"]``).
    """
    return await _call_off_loop(plugin, wallet, "heirs_delete", names)


@plugin_command("nw", plugin_name)
async def heirs_import(self, path, wallet=None, plugin=None):
    """Import heirs from a JSON file (validated, merged).

    Returns ``imported`` (heirs taken from the file), ``names``, ``skipped``
    (each refused entry with the reason) and ``total``.

    arg:str:path:Path to the JSON file.
    """
    return await _call_off_loop(plugin, wallet, "heirs_import", path)


@plugin_command("nw", plugin_name)
async def heirs_export(self, path, wallet=None, plugin=None):
    """Export the heirs to a JSON file.

    arg:str:path:Destination file path.
    """
    return await _call_off_loop(plugin, wallet, "heirs_export", path)


# --------------------------------------------------------------------------- #
# Will-Executors
# --------------------------------------------------------------------------- #
@plugin_command("nw", plugin_name)
async def willexecutors_list(self, wallet=None, plugin=None):
    """List the will-executors for the current network.

    Returns a JSON object mapping executor URLs to their records (address,
    base_fee, status, info, selected, ...).
    """
    return await _call_off_loop(plugin, wallet, "willexecutors_list")


# The parameter is ``executor_url``, never ``url`` (CHANGELOG #100): Electrum's
# command line reads any option called ``url`` as a payment request
# (``run_electrum``: "unknown command: <url>"), so show/add/update/select
# could not be used from the command line.
@plugin_command("nw", plugin_name)
async def willexecutors_show(self, executor_url, wallet=None, plugin=None):
    """Show the details of a single will-executor.

    arg:str:executor_url:The will-executor URL.
    """
    return await _call_off_loop(plugin, wallet, "willexecutors_show", executor_url)


@plugin_command("nw", plugin_name)
async def willexecutors_add(
    self,
    executor_url,
    address="",
    base_fee=0,
    info=None,
    wallet=None,
    plugin=None,
):
    """Add a new will-executor (not selected by default).

    arg:str:executor_url:The will-executor base URL.
    arg:str:address:The executor fee address for this network.
    arg:int:base_fee:The executor base fee in satoshis.
    arg:str:info:A human-readable description.
    """
    return await _call_off_loop(
        plugin, wallet, "willexecutors_add", executor_url, address, base_fee, info
    )


@plugin_command("nw", plugin_name)
async def willexecutors_update(
    self,
    executor_url,
    address=None,
    base_fee=None,
    info=None,
    promo_code=None,
    rename_to=None,
    wallet=None,
    plugin=None,
):
    """Update an existing will-executor (only the given fields).

    arg:str:executor_url:The will-executor URL to update.
    arg:str:address:The new fee address.
    arg:int:base_fee:The new base fee in satoshis.
    arg:str:info:The new description.
    arg:str:promo_code:The new promo code.
    arg:str:rename_to:Optionally move the record to a new URL.
    """
    return await _call_off_loop(
        plugin,
        wallet,
        "willexecutors_update",
        executor_url,
        address,
        base_fee,
        info,
        promo_code,
        rename_to,
    )


@plugin_command("nw", plugin_name)
async def willexecutors_select(
    self, executor_url, value=True, wallet=None, plugin=None
):
    """Select (or deselect) a will-executor.

    arg:str:executor_url:The will-executor URL.
    arg:bool:value:True to select, False to deselect (``--value false``).
    """
    return await _call_off_loop(plugin, wallet, "willexecutors_select", [executor_url], value)


@plugin_command("nw", plugin_name)
async def willexecutors_delete(self, urls, wallet=None, plugin=None):
    """Delete one or more will-executors; ``not_found`` lists the unknown URLs.

    arg:str:urls:A will-executor URL, or a JSON array of URLs (e.g. ``["https://we.example.com"]``).
    """
    return await _call_off_loop(plugin, wallet, "willexecutors_delete", urls)


@plugin_command("nw", plugin_name)
async def willexecutors_ping(self, urls=None, wallet=None, plugin=None):
    """Ping the selected (or the given) will-executor servers.

    Updates status/base_fee/address from each server and saves.  Returns
    ``{url: {status, ok}}``.

    arg:json:urls:Optional JSON array of URLs to ping; defaults to the selected executors.
    """
    return await _call_off_loop(plugin, wallet, "willexecutors_ping", urls)


@plugin_command("nw", plugin_name)
async def willexecutors_download(self, wallet=None, plugin=None):
    """Download the will-executor list from the welist server and merge it.

    Returns the number of records downloaded and the new total.
    """
    return await _call_off_loop(plugin, wallet, "willexecutors_download")


@plugin_command("nw", plugin_name)
async def willexecutors_import(self, path, wallet=None, plugin=None):
    """Import will-executors from a JSON file (``{url: record}``).

    arg:str:path:Path to the JSON file.
    """
    return await _call_off_loop(plugin, wallet, "willexecutors_import", path)


@plugin_command("nw", plugin_name)
async def willexecutors_export(self, path, wallet=None, plugin=None):
    """Export the will-executors to a JSON file.

    arg:str:path:Destination file path.
    """
    return await _call_off_loop(plugin, wallet, "willexecutors_export", path)


# --------------------------------------------------------------------------- #
# Will
# --------------------------------------------------------------------------- #
@plugin_command("nw", plugin_name)
async def will_status(self, wallet=None, plugin=None):
    """Show the current will: per-transaction status, locktime and executors.

    Returns a JSON object with a per-txid detail list and global status counts.
    """
    return await _call_off_loop(plugin, wallet, "will_status")


@plugin_command("nw", plugin_name)
async def will_check(self, wallet=None, plugin=None):
    """Check the local coherence of the will (heirs, executors, fees, locktime).

    Returns ``{"valid": true}`` when coherent, or raises a descriptive error.
    While the delivery settings are on hold nothing is checked: the answer
    is ``{"valid": false}`` with ``result`` ``delivery_choice_pending`` or
    ``delivery_settings_unavailable`` and a ``message`` saying what to do.
    """
    return await _call_off_loop(plugin, wallet, "will_check")


@plugin_command("nw", plugin_name)
async def will_prepare(self, wallet=None, plugin=None):
    """Run the full prepare/inheritance flow (check, rebuild, persist).

    Returns a JSON object with ``result`` (``coherent``, ``rebuilt``,
    ``expired``, ``postponed``) and, when needed, the invalidation
    transaction to sign and broadcast. While the delivery settings are on
    hold nothing is done: ``result`` is ``delivery_choice_pending`` or
    ``delivery_settings_unavailable``, with a ``message``.
    """
    return await _call_off_loop(plugin, wallet, "prepare_will")


@plugin_command("nw", plugin_name)
async def will_autorebuild(self, wallet=None, plugin=None):
    """Run the automatic rebuild flow in one shot (check, rebuild, sign, push).

    The same flow the GUI runs automatically on new wallet transactions:
    the delivery date is anticipated by one day to orphan the old will on-chain
    and, only when the anticipated locktime crosses the Check Alive threshold,
    an invalidation transaction is returned instead.  A Check Alive already in
    the past is reported (``check_alive_passed``) and never invalidated on its
    own.  Signing needs a passwordless wallet.  A coherent will that is not
    signed yet, or that no will-executor has received, is signed and sent.

    Returns a JSON object with ``result``: ``valid``, ``no_heirs``,
    ``check_alive_passed``, ``invalidated`` (with ``invalidation_tx``),
    ``nothing``, ``needs_signing``, ``rebuilt``, ``rebuilt_not_sent``,
    ``signed``, ``sent`` or ``not_sent``; or, while the delivery settings
    are on hold (nothing done), ``delivery_choice_pending`` or
    ``delivery_settings_unavailable`` with a ``message``.
    """
    return await _call_off_loop(plugin, wallet, "auto_rebuild")


@plugin_command("nwp", plugin_name)
async def will_sign(self, txid=None, password=None, wallet=None, plugin=None):
    """Sign the valid, not-yet-complete will transactions (or just one).

    Updates the COMPLETE status and the signature counters and persists.
    Refused while the delivery settings are on hold (see ``bal_delivery_show``).

    arg:str:txid:Optional transaction id to sign; signs all valid ones when omitted.
    """
    txids = [txid] if txid is not None else None
    txs = await _call_off_loop(plugin, wallet, "sign_transactions", password, txids)
    return {wid: str(tx) for wid, tx in txs.items()}


@plugin_command("nw", plugin_name)
async def will_broadcast(
    self, txid=None, force=False, wallet=None, plugin=None
):
    """Send the signed will transactions to their will-executors (in parallel).

    Updates the PUSHED/PUSH_FAIL statuses and persists.  Returns ``{url: status}``;
    ``_failed`` maps each failed will-executor to the reason.  Ends with an
    error (exit code 1) when no will-executor received the will. Refused
    while the delivery settings are on hold (see ``bal_delivery_show``).

    arg:str:txid:Optional transaction id to broadcast; all valid+signed ones when omitted.
    arg:bool:force:Force re-pushing transactions already marked as PUSHED.
    """
    txids = [txid] if txid is not None else None
    return await _call_off_loop(plugin, wallet, "broadcast_command", force, txids)


@plugin_command("nw", plugin_name)
async def will_export(self, path, wallet=None, plugin=None):
    """Export the whole will to a JSON file.

    arg:str:path:Destination file path.
    """
    return await _call_off_loop(plugin, wallet, "export_will", path)


@plugin_command("nw", plugin_name)
async def will_import_merge(self, path, wallet=None, plugin=None):
    """Merge a will file into the current will (PSBTs and statuses are merged).

    arg:str:path:Path to the will JSON file.
    """
    return await _call_off_loop(plugin, wallet, "merge_will_from_file", path)


@plugin_command("nw", plugin_name)
async def will_invalidate(self, wallet=None, plugin=None):
    """Build the on-chain invalidation transaction for the current will.

    Returns ``{txid, tx}`` (or nulls when there is nothing to invalidate); the
    transaction still needs to be signed and broadcast. Refused while the
    delivery settings are on hold (see ``bal_delivery_show``).
    """
    return await _call_off_loop(plugin, wallet, "invalidate_will_command")


@plugin_command("nw", plugin_name)
async def will_check_executor(self, txid=None, wallet=None, plugin=None):
    """Ask the will-executors whether they hold our pushed transactions.

    Runs the searchtx check in parallel, applies the per-item status and
    persists.  Returns ``{txid: {url, pushed, checked, check_fail}}``.

    arg:str:txid:Optional transaction id to check; checks all pending ones when omitted.
    """
    txids = [txid] if txid is not None else None
    return await _call_off_loop(plugin, wallet, "check_transactions", txids)
