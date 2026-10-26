"""The engine's state bricks: load, save and check the will (CHANGELOG #117).

The first pieces of the shared engine (Phase 5e, step 5e-05). They were
copied without rewriting from the command line's ``BalController``
(``bal/cli/controller.py``), which now calls them. The GUI's ``BalWindow``
has its own copies of the same steps and switches to these in the next step.

Every function works on a host (see :class:`bal.engine.host.WalletHost`):
it reads and changes the host's state and calls the host's hooks where the
GUI and the command line do different things (store a setting, write the
wallet file, redraw the History).

The delivery settings of the wallet (CHANGELOG #123) are loaded here too.
Each wallet keeps its own delivery date, fee rate and Check Alive bridge in
its own file, under the wallet key ``will_settings``; the rules live in
:mod:`bal.core.delivery`. :func:`load_will_settings` points
``host.will_settings`` at that live wallet dict, after a one-time migration
and the anchor check; :func:`set_will_setting` is the one validated write.
Electrum's global ``bal_will_settings`` (BAL's key) is never written: it is
read raw, once per wallet, only as a migration source. When the settings
cannot be prepared the host "fails closed" (``settings_unavailable``) and
:func:`init_class_variables` stops every flow. The later date the old
settings may have asked for is settled by :func:`apply_later_wish` at the
start of the flows, once the wallet shows which will is in force.

The delivery form of #127 (a fixed date, or a period of N calendar months
that the Check Alive renews) is changed only through :func:`set_delivery`,
and only while no will is in force (:func:`form_locked`); the period date is
moved by :func:`apply_delivery_policy` at the start of the flows, only when
no will is in force (:func:`will_in_force`).

No Qt, no ``bal.cli`` (``bal/engine`` rules, see ``bal/engine/__init__``).
"""

import json
import time
from collections.abc import MutableMapping
from datetime import datetime, timedelta, timezone

from electrum.logging import get_logger
from electrum.util import MyEncoder

from ..core import checkalive, delivery, trace, will_state
from ..core.heirs import Heirs
from ..core.messages import will_change_message
from ..core.summary import excluded_holders as _excluded_holders
from ..core.util import Util, copy_structure
from ..core.will import NoHeirsException, NotCompleteWillException, Will, WillItem
from ..core.willexecutors import Willexecutors
from ..i18n import _
from . import instalments
from .locks import flow_lock_for

_logger = get_logger(__name__)

#: The settings a front-end may change through :func:`set_will_setting`.
#: The other keys (schema, migration diagnostics, anchor, note, question,
#: later wish) are written only by the rules of :mod:`bal.core.delivery`.
SETTING_FIELDS = ("locktime", "baltx_fees", "threshold")


def load_willitems(host):
    """Build ``host.willitems`` (``{txid: WillItem}``) from ``host.will``."""
    host.willitems = {}
    for wid, w in host.will.items():
        host.willitems[wid] = WillItem(w, wallet=host.wallet)


def save_willitems(host):
    """Persist the in-memory willitems into the wallet's ``will`` dict.

    The transaction is stored serialized (exactly like the GUI, which
    avoids deep-copying a live Transaction holding a ``threading.RLock``)
    and every value is proven JSON-serializable before it is written.
    Then the anchor of the saved will (:func:`record_will_anchor`, #123)
    and ``host.flush_wallet_db()`` (the command line writes the file).
    """
    keys = list(host.will.keys())
    for k in keys:
        del host.will[k]
    for wid, w in host.willitems.items():
        d = w.to_dict()
        d["tx"] = str(d["tx"])
        try:
            json.dumps(d, cls=MyEncoder)
        except Exception as e:
            _logger.error(f"save_willitems: will {wid} is not serializable: {e!r}")
            raise
        host.will[wid] = d
    # Before the flush, so the command line writes both in one save.
    record_will_anchor(host)
    host.flush_wallet_db()


def save_to_history(host):
    """Persist the will state into the wallet's LOCAL history (best-effort).

    Only when the ``SAVE_HISTORY`` setting is enabled, and never raises.
    Then ``host.after_history_saved()`` (the GUI redraws the History tab;
    like ``BalWindow._save_will_to_history``, also after a failed save, but
    not when the setting is off).
    """
    try:
        if not bool(host.bal_plugin.SAVE_HISTORY.get()):
            return
        Will.save_valid_transactions_to_history(
            host.willitems, host.wallet, host.bal_plugin.HISTORY_LABEL.get()
        )
    except Exception as e:
        _logger.error(f"save_to_history failed: {e}")
    host.after_history_saved()


def available_utxos(host):
    """The wallet coins the will can use (the will's own local coins aside)."""
    return Util.get_available_utxos(
        host.wallet,
        host.bal_plugin.HISTORY_LABEL.get(),
        Will.get_min_locktime(host.willitems, default_value=host.date_to_check),
    )


def refresh_invalidated_items(host):
    """Mark the will items already invalidated on-chain (CHANGELOG #79).

    Runs before ``date_to_check`` and the Check Alive are computed, which
    read the VALID items: see ``Will.refresh_invalidated``. Never raises.
    """
    try:
        utxos = Util.get_available_utxos(
            host.wallet,
            host.bal_plugin.HISTORY_LABEL.get(),
            Will.get_min_locktime(host.willitems, default_value=time.time()),
        )
        Will.refresh_invalidated(host.willitems, utxos, host.wallet)
    except Exception as e:
        _logger.debug(f"refresh invalidated items failed: {e!r}")


def check_alive_info(host):
    """``check_alive_renewal`` for this wallet (None: nothing to do).

    Read-only and offline (no will-executor ping). See
    ``bal.core.checkalive.check_alive_renewal``. Never raises.
    """
    try:
        return checkalive.check_alive_renewal(
            host.bal_plugin.is_basic_mode(),
            host.will_settings,
            built_locktime=Will.get_min_locktime(host.willitems),
            creation_date=Will.get_creation_time(host.willitems),
        )
    except Exception as e:
        _logger.debug(f"check alive info failed: {e!r}")
        return None


def normalize_check_alive(host):
    """Convert an old Check Alive value to "-Nd" and store it (#80).

    A no-op once the value is "-Nd". The rules (same date kept) live in
    ``bal.core.checkalive.normalize_check_alive``; the new value goes
    through ``host.store_will_setting("threshold", new)``. Never raises.
    """
    try:
        old = host.will_settings.get("threshold")
        new = checkalive.normalize_check_alive(
            host.will_settings,
            built_locktime=Will.get_min_locktime(host.willitems),
            creation_date=Will.get_creation_time(host.willitems),
        )
        if new != old:
            _logger.info(f"check alive converted: {old!r} -> {new}")
            host.store_will_setting("threshold", new)
    except Exception as e:
        _logger.debug(f"check alive conversion failed: {e!r}")


def init_class_variables(host):
    """Prepare the state every check reads.

    First the two holds of the delivery settings (#123, rule I6, see
    :func:`check_holds`): ``DeliverySettingsUnavailable`` or
    ``DeliveryChoicePending`` (``bal.core.delivery``) stop the flow before
    anything is read or changed. Every flow passes through here: Check,
    Prepare, wallet close, the automatic rebuild and the command line.

    Then refreshes the invalidated items, applies the period form
    (:func:`apply_delivery_policy`, #127: with no will in force the period
    date becomes today + N months; it reads the refreshed items), settles
    the later wish of the migration (:func:`apply_later_wish`, #123: it
    reads the refreshed items, and the dates below read the delivery date
    it may store), refreshes the Check Alive value, sets ``date_to_check``,
    ``no_willexecutor`` and ``willexecutors``, checks the Check Alive
    against the delivery date, checks the table of the deliveries against
    the heirs (#168, :func:`bal.engine.instalments.check_table`) and gives
    every heir the delivery date. Raises ``NoHeirsException`` without
    heirs, the Check Alive errors of ``bal.core.checkalive`` and
    ``bal.core.delivery.InstalmentsMismatch``.
    """
    check_holds(host)
    if not host.heirs:
        raise NoHeirsException(_("Heirs are not defined"))
    # Items already invalidated on-chain must not anchor the dates (#79).
    refresh_invalidated_items(host)
    # #127: the period form. Right after the refresh (an invalidation the
    # wallet has just registered ends the will in force) and before every
    # date below is read.
    apply_delivery_policy(host)
    # #123 (FS-2): the will found at the migration may be one whose paid
    # postpone the wallet has only now registered.
    apply_later_wish(host)
    # An old Check Alive value becomes "N days before the delivery" (#80).
    normalize_check_alive(host)
    host.date_to_check = checkalive.resolve_date_to_check(
        host.bal_plugin.is_basic_mode(),
        host.will_settings,
        built_locktime=Will.get_min_locktime(host.willitems),
        creation_date=Will.get_creation_time(host.willitems),
    )
    host.no_willexecutor = bool(host.bal_plugin.NO_WILLEXECUTOR.get())
    host.willexecutors = Willexecutors.get_willexecutors(
        host.bal_plugin, update=True, task=False
    )
    delivery_date = checkalive.resolve_delivery_date(
        host.will_settings,
        built_locktime=Will.get_min_locktime(host.willitems),
    )
    checkalive.ensure_check_alive_valid(
        # #127: a period-form will keeps its Check Alive in BASIC too.
        checkalive.check_alive_basic(
            host.bal_plugin.is_basic_mode(), host.will_settings
        ),
        host.date_to_check,
        delivery_date=delivery_date,
    )
    # #168 (owner decision 3): a table that no longer fits the heirs stops
    # the flow here, after the Check Alive gate (a renewal is still
    # offered) and before anything is built. Never in check_holds: an
    # invalidation must stay possible.
    instalments.check_table(host)
    # #168 (D7): the stored person rows always carry the will's date (the
    # first delivery); the dates of the later deliveries live in the table.
    init_heirs_to_locktime(host)


def init_heirs_to_locktime(host, multiverse=False):
    """Give every heir the delivery date of the settings, then
    ``host.flush_wallet_db()``. Nothing with ``multiverse`` (each heir keeps
    its own date): BAL's model, no longer used by the plugin (#168, D7: a
    staged will keeps its dates in the table; the callers pass nothing)."""
    if multiverse:
        return
    locktime = host.will_settings["locktime"]
    if not isinstance(locktime, (int, float, str)):
        locktime = str(locktime)
    updates = {
        heir: [host.heirs[heir][0], host.heirs[heir][1], locktime]
        for heir in list(host.heirs)
    }
    for heir, value in updates.items():
        host.heirs[heir] = value
    host.flush_wallet_db()


def check_will(host, undo_anticipation=False):
    """``Will.is_will_valid`` on the host's will, settings, heirs and
    will-executors; raises the "will must be prepared again" exceptions.

    First (#135, owner's ZIP test) an heir that was only renamed gets its
    new name in the will items (``Will.sync_heir_names``: metadata only,
    the transactions do not change) and the will is saved, so the rename
    is said ONCE and no identical will is rebuilt at every Check.
    ``host.heir_renames``: ``[(old, new)]`` of this check (not stored).

    ``undo_anticipation`` (#135): also :func:`drop_anticipation_leftover`
    first. Only the FIRST check of a Check passes it, never the check
    after its own build (it would drop the drafts it just made).

    #168: with a table of deliveries, the heirs compared are the rows of
    every delivery (:func:`bal.engine.instalments.effective_heirs`).

    #173: the command trace gets ``bal_will_check`` and its outcome (the
    reason when the will is not coherent, :func:`_trace_reason`).
    """
    trace.command("bal_will_check")
    renames = []
    try:
        # #168: with a table the items carry the rows of every delivery
        # ("Luca", "Luca§2"...), so they are compared with those rows.
        renames = Will.sync_heir_names(
            host.willitems, instalments.effective_heirs(host))
        if renames:
            _logger.info(f"heirs renamed (names only): {renames}")
            save_willitems(host)
    except Exception as e:
        _logger.error(f"heir names not synchronized: {e!r}")
    host.heir_renames = renames
    host.anticipation_dropped = (
        drop_anticipation_leftover(host) if undo_anticipation else None)
    try:
        valid = Will.is_will_valid(
            host.willitems,
            host.date_to_check,
            host.will_settings["baltx_fees"],
            available_utxos(host),
            # After the leftover above: it may move delivery 1 back.
            heirs=instalments.effective_heirs(host),
            willexecutors=host.willexecutors,
            self_willexecutor=host.no_willexecutor,
            wallet=host.wallet,
        )
    except Exception as e:
        trace.add(trace.WARNING, _("not coherent: {}").format(_trace_reason(e)))
        raise
    trace.add(trace.OK, _("the will is coherent"))
    return valid


def _trace_reason(e):
    """Why :func:`check_will` found the will not coherent, for the trace.

    #173: the sentence of the command line's ``reason``
    (``will_change_message``) when the will must be prepared again, else
    the error's own text.
    """
    if isinstance(e, NotCompleteWillException):
        try:
            return will_change_message(e)
        except Exception:
            pass
    return trace.reason(e)


# --------------------------------------------------------------------------- #
# The wallet's delivery settings (CHANGELOG #123)
# --------------------------------------------------------------------------- #
def utc_day_text(ts):
    """``YYYY-MM-DD`` (UTC) of timestamp ``ts``, with the time when it is
    not 00:00 UTC (a legacy date is kept to the second). For the log and
    the command line (never translated).

    Plain arithmetic from 1970, not ``datetime.fromtimestamp``, which fails
    after 2038 where ``time_t`` is 32-bit.
    """
    moment = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=int(ts))
    if moment.hour or moment.minute or moment.second:
        return moment.strftime("%Y-%m-%d %H:%M:%S UTC")
    return moment.strftime("%Y-%m-%d")


def _wallet_settings(host):
    """The wallet's own live settings dict (``wallet.db.get_dict``).

    In a real wallet a ``StoredDict``, the same object at every call (an
    empty one is created the first time). Rule I3 of the #123 plan: it is
    changed item by item, never replaced with ``db.put`` (a reference
    already handed out would no longer be the stored one).
    """
    return host.wallet.db.get_dict(delivery.WALLET_KEY)


def _loaded_settings(host):
    """``host.will_settings`` when it is the wallet's own live dict, else None.

    The anchor is written only there: never into display-only values
    (settings unavailable), the plain dict of a test host, or a dict that
    belongs to Electrum's config.
    """
    d = getattr(host, "will_settings", None)
    if getattr(host, "settings_unavailable", False):
        return None
    if not isinstance(d, MutableMapping):
        return None
    try:
        return d if d is _wallet_settings(host) else None
    except Exception:
        return None


def stored_willitems(host):
    """The will as stored in the wallet, as ``{wid: WillItem}`` copies.

    Read at wallet open, before the window or the controller loads its own
    items. Each stored item is copied first (the plain values with
    ``copy_structure``, the transaction as text, so no live ``Transaction``
    is deep-copied: it holds a ``threading.RLock``). ``Util.fix_will_tx_fees``
    then runs on the COPY: a legacy item with only ``tx_fees`` gives its
    fee, not 0 (``WillItem`` reads ``baltx_fees`` with default 0), and the
    wallet is not changed here (``init_will`` fixes it later anyway). The
    items get no wallet: only their status flags, fee, heirs, creation
    ``time`` and ``tx.locktime`` are read. A broken item raises (the caller
    fails closed).
    """
    copies = {}
    for wid, w in host.wallet.db.get_dict("will").items():
        item = copy_structure({k: v for k, v in w.items() if k != "tx"})
        item["tx"] = str(w["tx"])
        copies[wid] = item
    Util.fix_will_tx_fees(copies)
    return {wid: WillItem(item, _id=wid) for wid, item in copies.items()}


def _will_source(host, willitems=None):
    """The will items the delivery rules read, as ``{wid: WillItem}``.

    ``willitems`` when given and not empty, already loaded with their fees
    fixed (the command line loads them first); else the stored will,
    through :func:`stored_willitems`. An empty dict reads the stored will
    too: a window whose items are not loaded (yet) must never migrate as if
    the wallet had no will.
    """
    return willitems if willitems else stored_willitems(host)


def will_views(host, willitems=None):
    """The will items as the delivery rules read them (``ItemView`` list).

    The items of :func:`_will_source` (``willitems`` when given and not
    empty, else the stored will) through
    :func:`bal.core.delivery.views_from`.
    """
    return delivery.views_from(_will_source(host, willitems))


def _creation_time(host, willitems=None):
    """The creation time of the will (``Will.get_creation_time``), or None.

    Read from the items of :func:`_will_source`, only during the one-time
    migration: an old positive Check Alive ("30d" after the creation of the
    will) counts from it, as ``normalize_check_alive`` (#80) counted it
    (see :func:`bal.core.delivery.convert_check_alive`). Nothing else reads
    it, so an item without a usable ``time`` never stops the migration: the
    time is then unknown and such a value counts from today, as #80 did
    when the creation was unknown.
    """
    try:
        return Will.get_creation_time(_will_source(host, willitems))
    except Exception as e:
        _logger.info(f"will settings: the creation time of the will is unknown: {e!r}")
        return None


def _has_plugin_data(host):
    """True when the wallet already has heirs or a will.

    Only such a wallet may take its date or fee from the old shared value:
    a wallet created after the update gets the default, never the frozen
    global (owner decision 2026-10-11, critic 2).
    """
    db = host.wallet.db
    return bool(db.get("heirs")) or bool(db.get("will"))


def _global_raw(host):
    """Electrum's global ``bal_will_settings`` as stored (a copy), or None.

    Read RAW with ``config.get``: ``BalConfig.get`` hands back its mutable
    default when the key is missing. A copy, so nothing done with it can
    reach the config. Never written (rule I1): it is BAL's key, and BAL
    reads its ``threshold`` with no fallback.
    """
    plugin = host.bal_plugin
    raw = plugin.config.get(plugin.WILL_SETTINGS.name)
    return None if raw is None else copy_structure(raw)


def heirs_periods_to_date(host, locktime):
    """Give the delivery date to every heir that still holds a period.

    A migration step of #123: every heir gets the will's date anyway
    (:func:`init_heirs_to_locktime`), and an old recipe such as ``"1054d"``
    must not stay in the heirs, where it would be read against the clock
    again. (BAL's global multiverse option kept them; #168, D7: no longer
    read.) Uses ``host.heirs`` when it is loaded, else the wallet's
    ``Heirs`` (each change is saved by ``Heirs.__setitem__``). Returns the
    names changed.
    """
    heirs = host.heirs if host.heirs else Heirs(host.wallet)
    changed = []
    for name in list(heirs):
        value = list(heirs[name])
        if len(value) > 2 and Util._relative_days(value[2]) is not None:
            value[2] = locktime
            heirs[name] = value
            changed.append(name)
    return changed


def ensure_will_settings(host, willitems=None, now=None):
    """Prepare the wallet's own delivery settings; True when they can be used.

    ``willitems``: see :func:`will_views`. ``now``: the caller's clock
    (default ``time.time()``, the clock the test scenarios replace).

    1. The will items are read (:func:`will_views`).
    2. When the wallet dict is not current
       (:func:`bal.core.delivery.is_current`), the one-time migration:
       :func:`bal.core.delivery.migration_values` on the items, the global
       read raw (:func:`_global_raw`), a copy of the dict as found, the
       "plugin data" gate, the creation time of the will
       (:func:`_creation_time`) and the multiverse setting (each heir with
       its own date: the shared value was no wish); then the heirs'
       periods become the date (:func:`heirs_periods_to_date`) and the
       values are written item by item, the legacy keys dropped (rule I3).
       One log line says where the date came from.
    3. The v2 upgrade (#127, :func:`bal.core.delivery.upgrade_values`): a
       dict of schema 1 gets the form "fixed" and ``settings_version`` 2,
       item by item; nothing else changes. One log line.
    4. The anchor check (:func:`bal.core.delivery.check_anchor`): a will
       signed by another program is followed, never reverted; an unsigned
       draft of another program is not followed (one log line).
    5. ``host.will_settings`` becomes the live wallet dict and
       ``host.settings_unavailable`` False; ``host.flush_wallet_db()`` when
       something was written (the command line saves the file, the GUI
       leaves it to Electrum).

    Any error fails closed (plan 3.4): it is logged,
    ``host.settings_unavailable`` becomes True and ``host.will_settings``
    holds display-only values (:func:`bal.core.delivery.display_only_values`:
    the date and fee of the will in force, else the defaults). Never the
    global, never a raw legacy dict: a period there is the day +2 paid
    invalidation. Every flow then stops (:func:`check_holds`). Returns False.

    The caller holds the wallet's flow lock (see :func:`load_will_settings`).
    """
    if now is None:
        now = time.time()
    items = []
    try:
        items = will_views(host, willitems)
        d = _wallet_settings(host)
        before = dict(d)
        if not delivery.is_current(d):
            values = delivery.migration_values(
                items,
                _global_raw(host),
                copy_structure(d),
                _has_plugin_data(host),
                now,
                creation_date=_creation_time(host, willitems),
                # #168 (D7): BAL's global option is no longer read, and a
                # dict still to migrate holds no table of deliveries.
                multiverse=False,
                advanced=_advanced(host),
            )
            # The heirs first: when they cannot be written, nothing is
            # stored and the next load migrates again.
            heirs_periods_to_date(host, values["locktime"])
            delivery.write_values(d, values, drop=delivery.LEGACY_DROP)
            _logger.info(
                "will settings migrated: from {}, delivery {}, fee {}".format(
                    values["migrated_from"],
                    utc_day_text(values["locktime"]),
                    values["baltx_fees"],
                )
            )
            skipped = delivery.non_date_items(items)
            if skipped:
                _logger.info(
                    "will settings: items without a date locktime ignored: "
                    "{}".format(skipped)
                )
        upgrade = delivery.upgrade_values(d)
        if upgrade:
            delivery.write_values(d, delivery.validate_values(upgrade))
            _logger.info(
                "will settings: schema {} (delivery form {})".format(
                    upgrade["settings_version"], upgrade["form"]
                )
            )
        anchor = delivery.check_anchor(d, items)
        if anchor == delivery.NOTE_OUTSIDE_CHANGE:
            _logger.info(
                "will settings: the will was changed by another program; the "
                "delivery date follows it: {}".format(utc_day_text(d["locktime"]))
            )
        elif anchor == delivery.ANCHOR_UNSIGNED:
            _logger.info(
                "will settings: another program left a will that is not "
                "signed; the delivery date stays {}".format(
                    utc_day_text(d["locktime"])
                )
            )
        host.will_settings = d
        host.settings_unavailable = False
        if dict(d) != before:
            host.flush_wallet_db()
        return True
    except Exception as e:
        _logger.error(f"will settings migration failed: {e!r}")
        host.settings_unavailable = True
        host.will_settings = delivery.display_only_values(items, now)
        return False


def load_will_settings(host, willitems=None, lock_timeout=None):
    """Load the wallet's own delivery settings into ``host.will_settings``.

    Called when the wallet opens (``BalWindow.__init__``, the command-line
    controller, the test windows), before anything reads the settings.
    :func:`ensure_will_settings` does the work; this adds the wallet's flow
    lock (``bal.engine.locks``) for a caller that does not hold it:

    * ``lock_timeout=None``: no lock is taken (the command line already
      holds it, see ``bal.cli.commands._call_locked``);
    * a number of seconds (the GUI: 1.0): wait that long for the lock.
      When another flow still holds it (a command migrating the same
      wallet), nothing is migrated now: settings that are already current
      are used as they are; anything else fails closed. The caller retries
      with :func:`ensure_will_settings` later (the GUI in ``init_will``).

    Returns True when :func:`ensure_will_settings` ran and succeeded, False
    otherwise (a retry is needed).
    """
    if lock_timeout is None:
        return ensure_will_settings(host, willitems)
    lock = flow_lock_for(host.wallet)
    if not lock.acquire(timeout=lock_timeout):
        _logger.info("will settings: the wallet is busy, prepared later")
        _use_without_migration(host, willitems)
        return False
    try:
        return ensure_will_settings(host, willitems)
    finally:
        lock.release()


def _use_without_migration(host, willitems=None):
    """Settings for a wallet whose flow lock is busy (see
    :func:`load_will_settings`): the live dict when it is already current,
    else display-only values and ``settings_unavailable`` (fail closed)."""
    try:
        d = _wallet_settings(host)
        if delivery.is_current(d):
            host.will_settings = d
            host.settings_unavailable = False
            return
    except Exception as e:
        _logger.error(f"will settings could not be read: {e!r}")
    try:
        items = will_views(host, willitems)
    except Exception as e:
        _logger.error(f"will items could not be read: {e!r}")
        items = []
    host.settings_unavailable = True
    host.will_settings = delivery.display_only_values(items, time.time())


def check_holds(host):
    """Raise the hold of the delivery settings that applies (rule I6).

    ``DeliverySettingsUnavailable`` when ``host.settings_unavailable``
    (the settings could not be prepared), else ``DeliveryChoicePending``
    while the "which date?" question waits (``choice_pending``); both from
    :mod:`bal.core.delivery`. Nothing is built, signed, pushed or
    invalidated while one applies. Read with safe defaults: a host without
    these attributes or keys (the fake hosts of the tests, a plain dict) is
    never held.
    """
    delivery.check_holds(
        getattr(host, "will_settings", None),
        getattr(host, "settings_unavailable", False),
    )


def set_will_setting(host, field, value):
    """The one validated write of a delivery setting (#123).

    ``field``: one of :data:`SETTING_FIELDS`. The value must pass
    :func:`bal.core.delivery.validate_values`: a fee rate of at least 1, a
    delivery date of at least 500,000,000 (UNIX time), stored as an int (a
    float or a numeric text is truncated to whole seconds; a period such as
    ``"1054d"`` is refused: it would be read against the clock again; a
    block height, a bool, NaN/inf or garbage is refused too), a Check
    Alive ``"-Nd"``. Otherwise ``ValueError`` (English, for the log and
    the command line) and nothing is written. Refused with
    ``DeliverySettingsUnavailable`` while ``host.settings_unavailable``:
    those values are for display only.

    The value is written item by item into ``host.will_settings`` (after
    :func:`load_will_settings`, the live wallet dict; never ``db.put``,
    rule I3), and only when it changed. A delivery date written while the
    "which date?" question is pending answers it: the question and its note
    are cleared (to keep the date, write the stored date again; see
    :func:`answer_delivery_choice`). Every delivery date written also drops
    the later wish of the migration
    (:func:`bal.core.delivery.drop_later_wish`): a date chosen now (or the
    later wish itself, see :func:`apply_later_wish`) replaces it.

    Nothing else: the caller makes it durable (the command line saves the
    wallet) and refreshes what shows it. Returns the value as stored.
    """
    if getattr(host, "settings_unavailable", False):
        raise delivery.DeliverySettingsUnavailable()
    if field not in SETTING_FIELDS:
        raise ValueError("not a delivery setting: {!r}".format(field))
    values = delivery.validate_values({field: value})
    d = host.will_settings
    if field == "locktime":
        _check_first_delivery(d, values[field])
    delivery.write_values(d, values)
    if field == "locktime":
        if d.get(delivery.CHOICE_KEY):
            delivery.clear_choice(d)
        delivery.drop_later_wish(d)
    return values[field]


def _check_first_delivery(d, locktime):
    """Refuse a delivery date that is not before delivery 2 (#168).

    With a table, the wallet's delivery date is the date of delivery 1
    (:func:`bal.core.delivery.table_rows`): a date on or after delivery 2
    would break the order of the deliveries. Only the order: the 28 days
    are checked where the owner types the date (owner decision 5: the
    automatic one-day moves may shorten them). ``ValueError``, nothing
    written.
    """
    try:
        rows = delivery.instalments_of(d)
    except ValueError:
        return
    if len(rows) < 2 or not isinstance(rows[1], dict):
        return
    second = delivery.as_delivery(rows[1].get("locktime"))
    if second is not None and locktime >= second:
        raise ValueError(
            "the delivery date must be before delivery 2 ({}): change the "
            "table of the deliveries first".format(utc_day_text(second))
        )


def set_instalments(host, rows, now=None):
    """The one validated write of the table of deliveries (#168).

    ``rows``: the table (``[{"locktime", "shares"}, ...]``), checked in full
    by :func:`bal.core.delivery.validate_instalments` against the paid
    heirs (the message rows are not columns): 2 or 3 dates after ``now``,
    at least 28 days apart, shares of the whole estate adding up to 100 %.
    Refused with the period form (one renewable date, #127 decision 10) and
    while the settings are display-only.

    Then, all checked: the table is stored whole (a stored list's inner
    dicts are not tracked by the wallet file), the schema becomes
    :data:`bal.core.delivery.INSTALMENTS_SCHEMA_VERSION` (owner choice B:
    only together with a table) and the wallet's delivery date becomes the
    date of delivery 1 (:func:`set_will_setting`). Like every date change,
    an earlier date is a free rebuild and a later one the paid invalidation
    of the will in force (owner decision D6), decided by the next Check.
    The caller makes it durable. Returns the table as stored.
    """
    if getattr(host, "settings_unavailable", False):
        raise delivery.DeliverySettingsUnavailable()
    if now is None:
        now = time.time()
    d = host.will_settings
    if delivery.is_period(d):
        raise ValueError(
            "the period form has one delivery date: choose a fixed date first")
    names = delivery.payee_names(host.heirs)
    for name in names:
        if delivery.has_separator(name):
            raise ValueError("an heir name holds {!r}: {!r}".format(
                delivery.INSTALMENT_SEP, name))
    table = delivery.validate_instalments(rows, names, now)
    d[delivery.INSTALMENTS_KEY] = table
    version = d.get("settings_version")
    if not isinstance(version, int) or version < delivery.INSTALMENTS_SCHEMA_VERSION:
        d["settings_version"] = delivery.INSTALMENTS_SCHEMA_VERSION
    set_will_setting(host, "locktime", table[0]["locktime"])
    _logger.info(
        "delivery table stored: {}".format(
            [utc_day_text(row["locktime"]) for row in table]
        )
    )
    return table


def clear_instalments(host):
    """Remove the table of deliveries: one delivery again (#168).

    The wallet's delivery date stays (it was delivery 1's). The schema
    stays 3: it only says a table may be there. Returns True when a table
    was removed. The caller makes it durable.
    """
    if getattr(host, "settings_unavailable", False):
        raise delivery.DeliverySettingsUnavailable()
    d = host.will_settings
    if delivery.INSTALMENTS_KEY not in d:
        return False
    del d[delivery.INSTALMENTS_KEY]
    _logger.info("delivery table removed: one delivery date")
    return True


def answer_delivery_choice(host, use_later=False):
    """Answer the pending "which date?" question (#123, plan 3.3).

    Keep (the default): the stored date, the will already prepared, stays
    (no fee). ``use_later``: the later date of the old settings is stored;
    the next Check then reads a postpone and asks for the invalidation of
    the old will (miner fee). Both go through :func:`set_will_setting`,
    which clears the question, its note and the later wish of the
    migration. ``ValueError`` when no question is pending or the date to
    store is unknown. Returns the date stored.
    """
    dates = delivery.choice_dates(getattr(host, "will_settings", None))
    if dates is None:
        raise ValueError("no delivery date question is pending")
    keep, later = dates
    chosen = later if use_later else keep
    if chosen is None:
        raise ValueError("the date of this answer is not known")
    return set_will_setting(host, "locktime", chosen)


def record_will_anchor(host):
    """Store the anchor of the will Safe21 has just saved (plan 3.2).

    :func:`bal.core.delivery.record_will_anchor` on the wallet's own
    settings with the will AS STORED in the wallet (:func:`will_views`
    with no items: ``wallet.db.get_dict("will")``), never with
    ``host.willitems``. The anchor is compared at every load with the
    stored will: taken from memory, it could describe a set the wallet
    does not hold (FS-1 of the #123 review: after a Will tab Sign that
    wrote only the signed items, the stale VALID flags of a will already
    invalidated looked like a change made by another program, and the
    stored date went back to that will). Called at the end of
    :func:`save_willitems`, the one path that writes the will. Only into
    the wallet's own live dict (:func:`_loaded_settings`). Never raises: a
    missed anchor is harmless (Safe21 keeps the stored date on the earliest
    VALID date, so the next load only records it again) and must never stop
    a save.
    """
    d = _loaded_settings(host)
    if d is None:
        return
    try:
        delivery.record_will_anchor(d, will_views(host))
    except Exception as e:
        _logger.warning(f"will anchor not recorded: {e!r}")


def _wallet_synced(host):
    """True when the wallet is synchronized (``wallet.is_up_to_date()``):
    the invalidated items it marks then follow the chain. False when it is
    not, or when it cannot be asked."""
    try:
        return bool(host.wallet.is_up_to_date())
    except Exception:
        return False


def apply_later_wish(host):
    """Settle the later wish the migration kept (#123, FS-2 of the review).

    Runs in :func:`init_class_variables`, after the holds and right after
    :func:`refresh_invalidated_items`, so the items carry their on-chain
    state. The decision is :func:`bal.core.delivery.later_wish_action`:

    * no VALID item is left (the will found at the migration has been
      invalidated, e.g. by the paid postpone of the old code, which the
      wallet had not registered at the update): the later wish becomes the
      delivery date through the host's validated write
      (``host.store_will_setting``, which also refreshes the GUI fields and
      drops the wish). It is later than the stored date: no anticipation
      and no new fee, and the will is then built at the date already paid
      for, as before #123;
    * the wallet is synchronized and VALID items still exist (the owner
      had cancelled the postpone): the wish is dropped;
    * otherwise it is kept.

    One log line for each change. Only on the wallet's own live dict
    (:func:`_loaded_settings`): a test host's plain dict is never touched.
    When the date cannot be stored, ``DeliverySettingsUnavailable`` stops
    the flow: building at the earlier date is exactly what this prevents.
    Returns the decision (None: no wish kept).
    """
    d = _loaded_settings(host)
    if d is None or delivery.LATER_WISH_KEY not in d:
        return None
    action = delivery.later_wish_action(
        d, delivery.views_from(host.willitems), _wallet_synced(host)
    )
    if action == delivery.WISH_APPLY:
        wish = delivery.as_delivery(d.get(delivery.LATER_WISH_KEY))
        _logger.info(
            "will settings: the will found at the update is no longer in "
            "force; the delivery date becomes the later date of the old "
            "settings: {}".format(utc_day_text(wish))
        )
        host.store_will_setting("locktime", wish)
        if delivery.as_delivery(d.get("locktime")) != wish:
            _logger.error("will settings: the later delivery date was not stored")
            raise delivery.DeliverySettingsUnavailable()
        delivery.drop_later_wish(d)
    elif action == delivery.WISH_DROP:
        _logger.info(
            "will settings: the will found at the update stays in force; the "
            "later date of the old settings is dropped"
        )
        delivery.drop_later_wish(d)
    return action


# --------------------------------------------------------------------------- #
# The delivery form: a fixed date or a renewable period (CHANGELOG #127)
# --------------------------------------------------------------------------- #
def _executable_until_confirmed(willitems, wallet, wid, _seen=None):
    """True when no input of will item ``wid`` is spent by a CONFIRMED tx.

    The lock of the delivery form (:func:`form_locked`) must hold until an
    invalidation is confirmed: one only in the mempool can still be dropped
    or replaced, and the old will executed. So a spender counts only when
    mined (``Will._funding_spender_height`` > 0; a wallet-local copy of a
    will tx is never a spender). When an input spends the output of another
    will item that is not on-chain, that item decides (recursively, as
    ``Will._tx_still_executable``; ``_seen`` guards against loops).
    """
    seen = set() if _seen is None else _seen
    if wid in seen:
        return False
    seen.add(wid)
    for inp in willitems[wid].tx.inputs():
        height = Will._funding_spender_height(wallet, Util.utxo_to_str(inp))
        if height is not None and height > 0:
            return False
        parent_id = inp.prevout.txid.hex()
        parent = willitems.get(parent_id)
        if (
            parent is not None
            and not parent.get_status("CONFIRMED")
            and not parent.get_status("MEMPOOL")
            and not _executable_until_confirmed(willitems, wallet, parent_id, seen)
        ):
            return False
    return True


def form_locked(host, refresh=True):
    """True when the delivery form cannot change: a will is in force (#127).

    :func:`bal.core.delivery.form_locked` on ``host.willitems``, the wallet's
    synchronization (:func:`_wallet_synced`) and
    :func:`_executable_until_confirmed`. ``refresh``: first mark the items
    already invalidated on-chain (:func:`refresh_invalidated_items`, never
    raises). When the will cannot be read the form is locked (the safe
    answer: a change could need a paid invalidation).
    """
    try:
        if refresh:
            refresh_invalidated_items(host)
        willitems = host.willitems or {}
        return delivery.form_locked(
            delivery.views_from(willitems),
            _wallet_synced(host),
            lambda wid: _executable_until_confirmed(willitems, host.wallet, wid),
        )
    except Exception as e:
        _logger.warning(f"delivery form lock not read, kept locked: {e!r}")
        return True


def will_in_force(host):
    """True when a will is in force, for the period re-anchor (#127).

    :func:`bal.core.delivery.will_in_force` on ``host.willitems`` (refreshed
    by the caller), the wallet's synchronization and
    ``Will._tx_still_executable`` (no input spent by a broadcast tx). When
    the will cannot be read it counts as in force (the date is not moved).
    """
    try:
        willitems = host.willitems or {}
        return delivery.will_in_force(
            delivery.views_from(willitems),
            _wallet_synced(host),
            lambda wid: Will._tx_still_executable(wid, willitems, host.wallet),
        )
    except Exception as e:
        _logger.warning(f"will in force not read, date kept: {e!r}")
        return True


def in_force_delivery(host):
    """The delivery date of the will REALLY in force, or None (#134).

    The items of ``Will.in_force_items`` (``bal.core.will_state.is_in_force``:
    signed and/or sent, VALID or REPLACED, not invalidated or executed, still
    executable on a synchronized wallet), their earliest date locktime: the
    date the will-executors would execute. A prepared draft (never signed
    nor sent) is NOT the will in force, even when it is VALID and its date
    is the stored one (owner's ZIP test of #134: a draft for 13/10/2028 was
    shown as "the will in force" while the executors held 26/02/2031).
    Refreshes the invalidated items first; None when the will cannot be
    read (logged).
    """
    try:
        refresh_invalidated_items(host)
        willitems = host.willitems or {}
        items = Will.in_force_items(willitems, host.wallet, _wallet_synced(host))
        dates = [delivery.as_delivery(w.tx.locktime) for w in items.values()]
        dates = [d for d in dates if d is not None]
        return min(dates) if dates else None
    except Exception as e:
        _logger.warning(f"will in force not read: {e!r}")
        return None


def in_force_items(host):
    """The will items of the will REALLY in force, ``{wid: item}`` (#135).

    ``Will.in_force_items`` (the #128 notion, ``will_state.is_in_force``:
    signed and/or sent, not invalidated or executed, still executable on a
    synchronized wallet); a draft never. Refreshes the invalidated items
    first. ``{}`` when there is none or the will cannot be read (logged).
    """
    try:
        refresh_invalidated_items(host)
        willitems = host.willitems or {}
        return Will.in_force_items(willitems, host.wallet, _wallet_synced(host))
    except Exception as e:
        _logger.warning(f"will in force not read: {e!r}")
        return {}


def excluded_holders(host, selected):
    """The will-executors not selected now (excluded, or removed from the
    list) that hold a copy of the will in force, their URLs (#190).

    :func:`in_force_items` read through ``bal.core.summary.
    excluded_holders`` (a copy sent, still executable). ``selected``: the
    URLs selected now (``BalWindow.selected_willexecutors``); None: no rule,
    ``[]``. Read only; ``[]`` when the will cannot be read (logged).
    """
    if selected is None:
        return []
    try:
        return _excluded_holders(in_force_items(host), selected)
    except Exception as e:
        _logger.warning(f"excluded will-executors not read: {e!r}")
        return []


def prepared_items(host, in_force=None):
    """The will as it will be once the prepared drafts are signed (#135).

    ``{wid: item}``: the drafts (``will_state.draft_ids``: VALID, never
    signed nor sent) FIRST, then the items of the will in force
    (``in_force``, ``{wid: item}``) that are not drafts. Why both: when only
    a will-executor is added, the check prepares a copy for that executor
    alone and the copies already in force stay the will; comparing the
    drafts alone would read as "the other will-executors were removed".
    When the drafts pre-empt the will in force with an earlier date, the
    comparison keeps only the earliest date
    (``will_changes.side_from_items``), so the old copies drop out by
    themselves. Drafts first so their heirs and amounts are the ones read.
    """
    willitems = getattr(host, "willitems", None) or {}
    items = {wid: willitems[wid] for wid in will_state.draft_ids(willitems)}
    for wid, item in (in_force or {}).items():
        items.setdefault(wid, item)
    return items


def cancelled_postpone_date(host):
    """The delivery date to go back to after a cancelled postpone, or None.

    CHANGELOG #134 (owner, option B): a Check found a POSTPONE (the stored
    date is later than the will really in force) and the paid invalidation
    was not done. The stored date then goes back to the date the will items
    really use:

    * the prepared draft's date (``will_state.draft_ids``: VALID, never
      signed nor sent), when there is one and it is not later than the will
      in force (a later draft would read as a postpone again);
    * otherwise the date of the will really in force
      (:func:`in_force_delivery`, the #128 notion, untouched).

    None when there is nothing to go back to, when the stored date is not
    later than that date (then it was not a postpone), or when the stored
    date is the date of a VALID will already signed or sent (a newer will
    that is not undone: see the comment below). Pure reading.
    """
    stored = delivery.as_delivery((getattr(host, "will_settings", None) or {}).get("locktime"))
    if stored is None:
        return None
    willitems = getattr(host, "willitems", None) or {}
    # Funds safety (#123 FS tests): when the stored date IS the date of a
    # VALID will already signed or sent, it is not a wish to undo. That will
    # is the newer one; an older sent will dated earlier is what asks for
    # the invalidation. Going back to the earlier date would read as an
    # anticipation and sign the newer will at the earlier date.
    for item in willitems.values():
        try:
            lt = delivery.as_delivery(int(item.tx.locktime))
        except Exception:
            continue
        if (lt == stored and item.get_status("VALID")
                and will_state.is_signed_or_sent(item)):
            return None
    in_force = in_force_delivery(host)
    target = in_force
    drafts = []
    for wid in will_state.draft_ids(willitems):
        try:
            lt = delivery.as_delivery(int(willitems[wid].tx.locktime))
        except Exception:
            lt = None
        if lt is not None:
            drafts.append(lt)
    if drafts:
        draft = min(drafts)
        if in_force is None or draft <= in_force:
            target = draft
    if target is None or stored <= target:
        return None
    return target


#: One day: the step of the plugin's own anticipation (a will rebuilt
#: against the will in force is dated one day earlier, #128).
_ANTICIPATION_STEP = 86400


def drop_anticipation_leftover(host):
    """Undo an anticipation nobody needs any more (#135, owner's ZIP test).

    A rebuild against the will in force is dated one day earlier than it
    (#128) and that day is stored as the delivery date. Owner: such a
    rebuild was made by the plugin alone (an heir renamed back, under the
    old rule), Cancel at the password left its drafts and the earlier
    stored day; once the change was undone, every Check asked to sign a
    will whose ONLY difference from the will in force was that day.

    The rule, all of it must hold: a will is in force (#128); the stored
    delivery date is exactly ONE day before it (the anticipation step);
    there are drafts (VALID, never signed nor sent) and all of them carry
    that day; and the settings (Heirs tab, will-executors selected now,
    fee: ``will_changes.side_from_settings``, the wizard's comparison)
    differ from the will in force ONLY by that day. Then the drafts are
    dropped (they exist only in this wallet: free, #128) and the stored
    date goes back to the will in force's: the Check then finds the will
    in force again (#128 brings it back), nothing to sign.

    Funds-safe: signed or sent items are never touched, the will in force
    stays what the will-executors hold. A date the owner chose himself
    exactly one day earlier, then cancelled, would be put back too (it
    cannot be told apart without storing more; the will in force stays and
    the date can be chosen again); any other date, or any other change,
    is kept and asked to sign as before. Runs before the check, never on
    the drafts of the build of the same Check. Returns ``(the day dropped,
    the will in force's day)`` or None. Never raises (logged).

    #186: a will in several deliveries (a table, in force or in the
    settings) follows :func:`_drop_staged_leftover`: there the rebuild
    anticipates only the deliveries it changes, so the drafts of the
    others keep their dates and the test of one date below never held.
    """
    try:
        from ..core import will_changes

        willitems = host.willitems or {}
        drafts = will_state.draft_ids(willitems)
        if not drafts:
            return None
        stored = delivery.as_delivery((host.will_settings or {}).get("locktime"))
        in_force = in_force_items(host)
        if stored is None or not in_force:
            return None
        try:
            selected = host.selected_willexecutors()
        except Exception:
            selected = None
        if not isinstance(selected, (set, frozenset)):
            selected = None
        old = will_changes.side_from_items(in_force, selected)
        if old is None or old.delivery is None:
            return None
        # #186: a table in force or in the settings. A will with one date in
        # force while a table was added then removed again (review R-9) is
        # not recognized: one Check asks to sign, then it heals itself.
        if old.deliveries is not None or instalments.has_table(host):
            return _drop_staged_leftover(host, willitems, drafts, stored, old, selected)
        if old.delivery - stored != _ANTICIPATION_STEP:
            return None
        if any(delivery.as_delivery(willitems[w].tx.locktime) != stored for w in drafts):
            return None
        # #168: the will in force's side is its FIRST delivery: compare it
        # with the rows of delivery 1 (the person rows without a table).
        new = will_changes.side_from_settings(
            instalments.first_delivery_heirs(host) or {}, stored, sorted(selected) if selected is not None
            else list(old.executors), (host.will_settings or {}).get("baltx_fees"))
        # #185: staged=False: delivery 1 alone, as above (a will in several
        # deliveries never comes here since #186: _drop_staged_leftover).
        changes = will_changes.will_changes(old, new, staged=False)
        if not changes or [c.kind for c in changes] != [will_changes.DELIVERY]:
            return None
        for wid in drafts:
            del willitems[wid]
        _logger.info(
            "anticipation leftover: {} unsigned drafts dated {} dropped, the "
            "delivery date goes back to the will in force: {}".format(
                len(drafts), utc_day_text(stored), utc_day_text(old.delivery)))
        store = getattr(host, "store_will_setting", None)
        if store is not None:
            store("locktime", old.delivery)
        else:
            set_will_setting(host, "locktime", old.delivery)
        # The heirs carry the will's date too: the check gave them the
        # earlier day before this ran; give them the will in force's again,
        # as the check itself does.
        init_heirs_to_locktime(host)
        return stored, old.delivery
    except Exception as e:
        _logger.error(f"anticipation leftover not checked: {e!r}")
        return None


def _drop_staged_leftover(host, willitems, drafts, stored, old, selected):
    """:func:`drop_anticipation_leftover` for a will in several deliveries
    (#186, owner's plan of 2026-10-24).

    Seen in a flowkit will in three deliveries: a change cancelled at the
    password leaves drafts in which only the deliveries the rebuild changed
    are dated ONE day earlier (the fee: delivery 1, the stored date one day
    earlier; delivery 3's shares: delivery 3, its table date one day
    earlier); the drafts of the other deliveries keep their dates. Once the
    change was undone, every Check asked to sign them.

    The rule, all of it must hold: the settings (Heirs tab, the table,
    will-executors selected now, fee: ``side_from_settings`` with the
    table) compared with the will in force delivery by delivery
    (``will_changes``, #185) differ ONLY by deliveries exactly one day
    earlier with exactly the same shares (two decimals, review F5; no other
    row: heirs, message, will-executors, fee, number of deliveries; the
    settings side has no will-executor fee terms, review R-10: a new fee
    address shows only in the Check's own build); every draft carries the
    stored date of its delivery; and
    every delivery moved has drafts on that day (review F2: a delivery the
    owner moved one day on purpose, with no draft, is the owner's date,
    never put back); and no signed copy spends a draft's output (judges'
    review U3: drafts partly signed after the Cancel). Then the dates go
    back to the will in force's FIRST
    (review F4: a refused write leaves everything as it was; delivery 1
    through ``locktime``, read back; the later ones in the table, replaced
    whole as ``instalments.sync_later_deliveries`` writes it) and only then
    are the drafts dropped (unsigned, only in this wallet: free). Signed or
    sent items are never touched. Returns ``(the day dropped, the will in
    force's day)`` of the first delivery moved, or None.

    The limit of the one-date rule (review R-7): a delivery the owner moved
    exactly one day earlier himself, whose rebuild he then cancelled, is
    put back as well (it cannot be told from the plugin's step without a
    new wallet key); the date can be chosen again.
    """
    from ..core import will_changes

    settings = host.will_settings or {}
    new = will_changes.side_from_settings(
        host.heirs or {}, stored,
        sorted(selected) if selected is not None else list(old.executors),
        settings.get("baltx_fees"), table=will_changes.settings_table(settings))
    moved = {}
    for change in will_changes.will_changes(old, new) or []:
        if (change.kind != will_changes.INSTALMENT or change.old is None
                or change.new is None or change.old[0] is None
                or change.new[0] is None):
            return None
        if not will_changes.same_shares(change.old[1], change.new[1], decimals=2):
            return None
        if change.old[0] - change.new[0] != _ANTICIPATION_STEP:
            return None
        moved[change.number] = (change.new[0], change.old[0])
    if not moved or new.deliveries is None:
        return None
    dates = [date for date, _shares in new.deliveries]
    with_drafts = set()
    for wid in drafts:
        n = will_changes.delivery_number(willitems[wid])
        if n > len(dates) or delivery.as_delivery(willitems[wid].tx.locktime) != dates[n - 1]:
            return None
        with_drafts.add(n)
    if not set(moved) <= with_drafts:
        return None
    # Judges' review U3: a copy signed after the Cancel (e.g. the drafts of
    # delivery 2 signed from the Will tab) that spends a draft's output:
    # nothing is touched (the rule never decides around signed copies).
    draft_ids = set(drafts)
    for wid, item in willitems.items():
        if wid in draft_ids:
            continue
        try:
            spent = {txin.prevout.txid.hex() for txin in item.tx.inputs()}
        except Exception:
            return None
        if spent & draft_ids:
            return None
    if 1 in moved:
        store = getattr(host, "store_will_setting", None)
        if store is not None:
            store("locktime", moved[1][1])
        else:
            set_will_setting(host, "locktime", moved[1][1])
        # The window's write logs a refusal instead of raising: read back.
        if delivery.as_delivery(settings.get("locktime")) != moved[1][1]:
            return None
    later = {n: back for n, (_day, back) in moved.items() if n >= 2}
    if later:
        rows = delivery.instalments_of(settings)
        for n, back in later.items():
            rows[n - 1]["locktime"] = back
        settings[delivery.INSTALMENTS_KEY] = rows
    for wid in drafts:
        del willitems[wid]
    init_heirs_to_locktime(host)
    first = min(moved)
    _logger.info(
        "anticipation leftover: {} unsigned drafts dropped, deliveries {} "
        "back to the will in force's dates: {}".format(
            len(drafts), sorted(moved),
            [utc_day_text(moved[n][1]) for n in sorted(moved)]))
    return moved[first]


def revert_cancelled_postpone(host):
    """Put the stored delivery date back after a cancelled postpone (#134).

    The rule of :func:`cancelled_postpone_date`, written through the host's
    validated write (``host.store_will_setting``: ``set_will_setting``, the
    GUI fields refreshed on the GUI thread, rule I5). Called ONLY when the
    paid invalidation of a postpone was not done and certainly reached no
    network: Cancel at its password, or its signing failed. NEVER once an
    invalidation was broadcast (even with an error or not confirmed yet):
    then the postpone may be legitimately underway and the later date must
    stay. Returns ``(chosen, restored)`` (the later date that was not
    applied and the date stored now), or None when nothing was changed.
    Never raises (logged).
    """
    try:
        chosen = delivery.as_delivery(host.will_settings.get("locktime"))
        target = cancelled_postpone_date(host)
        if chosen is None or target is None:
            return None
        _logger.info(
            "will settings: postpone to {} cancelled (no invalidation): the "
            "delivery date goes back to {}".format(utc_day_text(chosen), utc_day_text(target))
        )
        host.store_will_setting("locktime", target)
        if delivery.as_delivery(host.will_settings.get("locktime")) != target:
            _logger.error("will settings: the date of the cancelled postpone was not restored")
            return None
        return chosen, target
    except Exception as e:
        _logger.error(f"cancelled postpone not reverted: {e!r}")
        return None


def _multiverse(host):
    """True when the wallet has a table of deliveries (#168, D7): then the
    period form is not available (#127, owner decision 10). It replaces
    BAL's hidden global option ``bal_enable_multiverse``, no longer read."""
    try:
        return instalments.has_table(host)
    except Exception:
        return False


def _advanced(host):
    """True when the plugin is in ADVANCED mode; False when it cannot be read.

    Read by the migration of a brand-new wallet (#146): in ADVANCED its
    delivery starts in the period form. BASIC is the safe answer.
    """
    try:
        return not host.bal_plugin.is_basic_mode()
    except Exception:
        return False


def set_delivery(host, form=None, date=None, months=None, now=None):
    """The one validated write of the delivery form (#127).

    ``form``: :data:`bal.core.delivery.FORM_FIXED` or ``FORM_PERIOD``
    (None: keep the stored one). ``date``: the fixed delivery date (fixed
    form only). ``months``: N calendar months, 1..240 (period form only).
    ``now``: the caller's clock (default ``time.time()``).

    * A form different from the stored one while a will is in force
      (:func:`form_locked`) raises
      :class:`bal.core.delivery.DeliveryFormLocked`: changing it needs the
      paid invalidation of that will first (owner decision 2).
    * Fixed: ``form`` "fixed" (``period_months`` and ``period_start``
      removed); with ``date``, the date through :func:`set_will_setting`.
    * Period (refused with a table of deliveries, #168): ``period_months`` = N. With no
      will in force (the form not locked) also ``period_start`` = today and
      ``locktime`` = today + N months (:func:`bal.core.delivery.period_values`).
      With a will in force only N is stored: it applies at the next renewal
      (owner decision 2, no fee now).

    ``ValueError`` (English) for a bad or missing value, nothing written;
    ``DeliverySettingsUnavailable`` while the settings are display-only.
    Everything is checked before anything is written. The caller makes it
    durable (the command line saves the wallet) and refreshes the fields.
    Returns ``{"form", "period_months", "locktime", "applied"}``
    (``applied``: the delivery date was set now).
    """
    if getattr(host, "settings_unavailable", False):
        raise delivery.DeliverySettingsUnavailable()
    if now is None:
        now = time.time()
    d = host.will_settings
    current = delivery.form_of(d)
    new_form = current if form is None else form
    if new_form not in delivery.FORMS:
        raise ValueError(
            "not a delivery form: {!r} (use fixed or period)".format(form)
        )
    locked = form_locked(host)
    if new_form != current and locked:
        raise delivery.DeliveryFormLocked(current)
    applied = False
    if new_form == delivery.FORM_FIXED:
        if months is not None:
            raise ValueError("months is used only by the period form")
        if date is not None:
            delivery.validate_values({"locktime": date})
        delivery.write_values(d, {"form": delivery.FORM_FIXED})
        for key in ("period_months", "period_start"):
            if key in d:
                del d[key]
        if date is not None:
            set_will_setting(host, "locktime", date)
            applied = True
    else:
        if date is not None:
            raise ValueError(
                "the period form has no fixed date: give months instead"
            )
        if _multiverse(host):
            raise ValueError(
                "the period form is not available with a table of deliveries")
        n = delivery.period_months(d) if months is None else delivery.as_months(months)
        if n is None:
            raise ValueError(
                "not a period: {!r} (whole months from {} to {})".format(
                    months, delivery.PERIOD_MIN, delivery.PERIOD_MAX
                )
            )
        values = {"form": delivery.FORM_PERIOD, "period_months": n}
        dates = None
        if not locked:
            dates = delivery.period_values(now, n)
            values["period_start"] = dates["period_start"]
        delivery.write_values(d, delivery.validate_values(values))
        if dates is not None:
            set_will_setting(host, "locktime", dates["locktime"])
            applied = True
    return {
        "form": delivery.form_of(d),
        "period_months": d.get("period_months"),
        "locktime": d.get("locktime"),
        "applied": applied,
    }


def initial_months(settings, now):
    """N for the period form: the stored period, else the whole months from
    ``now`` to the stored date (1..240), else the default (24).

    The one rule of the wizard's month box and of :func:`follow_mode`, so
    a switch to the period form keeps the date about the same.
    """
    settings = settings or {}
    n = delivery.as_months(settings.get("period_months"))
    if n is not None:
        return n
    locktime = delivery.as_delivery(settings.get("locktime"))
    if locktime is not None:
        n = delivery.months_between(now, locktime)
        if delivery.PERIOD_MIN <= n <= delivery.PERIOD_MAX:
            return n
    return delivery.DEFAULT_MONTHS


def follow_mode(host, months=None, now=None):
    """The delivery form follows the plugin mode (owner, #127 and #146).

    Only while the form is not locked (no will in force,
    :func:`form_locked`):

    * BASIC: a period form goes back to the fixed date (the date stays);
    * ADVANCED, multiverse off: a fixed date becomes the period form of
      ``months`` (default :func:`initial_months`, so the date stays about
      the same); the Check Alive can then renew the will.

    The same rule for every place that switches the mode (the plugin
    Settings dialog and the wizard), so the order of the owner's steps does
    not matter. Writes through :func:`set_delivery`. Returns the form
    written, or None when nothing changed. Never raises: a refusal is
    logged and nothing is written.
    """
    try:
        d = host.will_settings
        period = delivery.is_period(d)
        advanced = _advanced(host)
        if period == advanced or form_locked(host):
            return None
        if not advanced:
            set_delivery(host, form=delivery.FORM_FIXED, now=now)
            return delivery.FORM_FIXED
        if _multiverse(host):
            return None
        if now is None:
            now = time.time()
        if months is None:
            months = initial_months(d, now)
        set_delivery(host, form=delivery.FORM_PERIOD, months=months, now=now)
        return delivery.FORM_PERIOD
    except Exception as e:
        _logger.warning(f"delivery form not changed with the mode: {e!r}")
        return None


def apply_delivery_policy(host, now=None):
    """The period form at the start of every flow (#127, plan policy b).

    Runs in :func:`init_class_variables`, after the holds and right after
    :func:`refresh_invalidated_items` (the order is needed: a renewal
    invalidation the wallet has just registered must already have ended the
    will in force). With the period form and NO will in force
    (:func:`will_in_force`): the first will, or the new will of a renewal
    after the old one was invalidated, is delivered N calendar months after
    today: ``period_start`` = today, ``locktime`` = today + N months, the
    date through the host's validated write (``host.store_will_setting``:
    thread-safe, it refreshes the GUI fields on the GUI thread, rule I5).
    With a will in force, a fixed date, a broken N, multiverse on, or a
    host whose settings are not the wallet's own live dict (a test host's
    plain dict): nothing. Unchanged values are not written again.

    One log line for each change. When the date cannot be stored,
    ``DeliverySettingsUnavailable`` stops the flow (building at the old date
    is what this prevents). Returns the date stored, else None.
    """
    d = _loaded_settings(host)
    if d is None:
        return None
    months = delivery.period_months(d)
    if months is None or _multiverse(host):
        return None
    if will_in_force(host):
        return None
    if now is None:
        now = time.time()
    dates = delivery.period_values(now, months)
    if (
        d.get("locktime") == dates["locktime"]
        and d.get("period_start") == dates["period_start"]
    ):
        return None
    _logger.info(
        "will settings: no will in force; period of {} months from {}: "
        "delivery {}".format(
            months, utc_day_text(dates["period_start"]),
            utc_day_text(dates["locktime"]),
        )
    )
    delivery.write_values(
        d, delivery.validate_values({"period_start": dates["period_start"]})
    )
    host.store_will_setting("locktime", dates["locktime"])
    if delivery.as_delivery(d.get("locktime")) != dates["locktime"]:
        _logger.error("will settings: the period delivery date was not stored")
        raise delivery.DeliverySettingsUnavailable()
    return dates["locktime"]
