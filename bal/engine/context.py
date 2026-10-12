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

from ..core import checkalive, delivery
from ..core.heirs import Heirs
from ..core.util import Util, copy_structure
from ..core.will import NoHeirsException, Will, WillItem
from ..core.willexecutors import Willexecutors
from ..i18n import _
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
    against the delivery date and gives every heir the delivery date
    (unless each heir has its own). Raises ``NoHeirsException`` without
    heirs and the Check Alive errors of ``bal.core.checkalive``.
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
    init_heirs_to_locktime(host, host.bal_plugin.ENABLE_MULTIVERSE.get())


def init_heirs_to_locktime(host, multiverse=False):
    """Give every heir the delivery date of the settings, then
    ``host.flush_wallet_db()``. Nothing with ``multiverse`` (each heir keeps
    its own date)."""
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


def check_will(host):
    """``Will.is_will_valid`` on the host's will, settings, heirs and
    will-executors; raises the "will must be prepared again" exceptions."""
    return Will.is_will_valid(
        host.willitems,
        host.date_to_check,
        host.will_settings["baltx_fees"],
        available_utxos(host),
        heirs=host.heirs,
        willexecutors=host.willexecutors,
        self_willexecutor=host.no_willexecutor,
        wallet=host.wallet,
    )


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

    A migration step of #123, multiverse off only: then every heir gets the
    will's date anyway (:func:`init_heirs_to_locktime`), and an old recipe
    such as ``"1054d"`` must not stay in the heirs, where it would be read
    against the clock again. With multiverse on each heir keeps its own
    value. Uses ``host.heirs`` when it is loaded, else the wallet's
    ``Heirs`` (each change is saved by ``Heirs.__setitem__``). Returns the
    names changed.
    """
    if host.bal_plugin.ENABLE_MULTIVERSE.get():
        return []
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
                multiverse=bool(host.bal_plugin.ENABLE_MULTIVERSE.get()),
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
    delivery.write_values(d, values)
    if field == "locktime":
        if d.get(delivery.CHOICE_KEY):
            delivery.clear_choice(d)
        delivery.drop_later_wish(d)
    return values[field]


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


def _multiverse(host):
    """The hidden multiverse option: each heir keeps its own date, and the
    period form is not available (#127, owner decision 10)."""
    try:
        return bool(host.bal_plugin.ENABLE_MULTIVERSE.get())
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
    * Period (refused with multiverse on): ``period_months`` = N. With no
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
            raise ValueError("the period form is not available with multiverse")
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
