"""
bal.cli.controller
==================

Headless replica of the Qt flows (``bal.gui.qt.window.BalWindow``) for the
command-line front-end.

The CLI layer is a daemon, so there is no widget to drive: every operation
must run to completion synchronously and return a JSON-serializable result
(or raise ``electrum.util.UserFacingException`` with a human-readable message).
This controller therefore mirrors the *logic* of the GUI window (its state
object, the build/check/sign/push flows and the willexecutors CRUD) while
reusing only ``bal.core`` - it MUST never import PyQt.

Errors:
    - Domain exceptions from ``bal.core`` are translated into
      ``UserFacingException`` so the JSON-RPC layer can print them cleanly.
    - Network flows (ping/push/check/download) block the calling thread with
      the same timeouts the GUI uses, so the daemon never hangs forever.

This module is imported lazily (only when a ``bal_*`` command actually runs),
so a missing wallet or a network-less daemon can still start Electrum.
"""

import json
import time

from electrum import bitcoin, constants
from electrum.i18n import _
from electrum.logging import get_logger
from electrum.transaction import tx_from_any
from electrum.util import (
    FileExportFailed,
    FileImportFailed,
    MyEncoder,
    UserFacingException,
    read_json_file,
    write_json_file,
)

from ..core import checkalive
from ..core import heirs as heirs_mod
from ..core import will as will_mod
from ..core.checkalive import (
    CheckAliveAfterDeliveryError,
    CheckAliveError,
    check_alive_renewal,
    ensure_check_alive_valid,
    normalize_check_alive,
    resolve_date_to_check,
    resolve_delivery_date,
    resolve_guard_threshold,
)
from ..core.heirs import Heirs, is_op_return_address
from ..core.messages import (
    check_alive_message,
    check_alive_passed_sentence,
    will_change_message,
)
from ..core.plugin_base import BalConfig, BalPlugin
from ..core.util import Util, copy_structure
from ..core.will import Will, WillItem
from ..core.willexecutors import Willexecutors, is_onion_url, is_tor_active

_logger = get_logger(__name__)


def _user_facing(e):
    """Translate a ``bal.core`` domain exception into a user-facing message.

    The "will no longer coherent" family uses the same sentences as the Check
    dialog (``bal.core.messages``, CHANGELOG #78). Unknown exceptions are passed through unchanged so their real type reaches
    the caller (and, eventually, the Electrum log).
    """
    if isinstance(e, UserFacingException):
        return e
    if isinstance(e, will_mod.NoHeirsException):
        return UserFacingException(_("There are no valid heirs"))
    if isinstance(e, will_mod.WillExpiredException):
        return UserFacingException(
            _("The will is expired and must be invalidated on-chain and rebuilt")
        )
    if isinstance(e, will_mod.WillPostponedException):
        return UserFacingException(
            _(
                "This inheritance was already signed/sent to will-executors and "
                "you are postponing it. The invalidation transaction must be "
                "signed and broadcast FIRST, then the will prepared again."
            )
        )
    if isinstance(e, will_mod.NoWillExecutorNotPresent):
        # Kept as is: build_will below raises it too, for "nothing selected",
        # where the Check sentence ("no backup transaction") would be wrong.
        return UserFacingException(_("No backup transaction or will-executor selected"))
    if isinstance(e, will_mod.NotCompleteWillException):
        # CHANGELOG #78: the same sentence as the Check dialog (heir added /
        # removed / changed, fee rate, will-executor, new funds...).
        return UserFacingException(will_change_message(e))
    if isinstance(e, will_mod.AmountException):
        return UserFacingException(
            _(
                "In the inheritance process, the entire wallet will always be "
                "fully emptied. Your settings require an adjustment of the "
                "amounts: {}"
            ).format(e)
        )
    if isinstance(e, heirs_mod.WillExecutorFeeTooHighException):
        return UserFacingException(_("Will-executor fee too high: {}").format(e))
    if isinstance(e, heirs_mod.BalanceTooLowException):
        return UserFacingException(str(e))
    if isinstance(e, heirs_mod.HeirAmountIsDustException):
        return UserFacingException(str(e))
    if isinstance(e, heirs_mod.NotAnAddress):
        # The core text is "not an address,<address>": prefixing it again gave
        # "not an address, not an address,<address>" (CHANGELOG #104).
        text = str(e)
        prefix = "not an address,"
        if text.startswith(prefix):
            return UserFacingException(
                _("Not a valid address for this network: {}").format(text[len(prefix):])
            )
        return UserFacingException(text)
    if isinstance(e, heirs_mod.AmountNotValid):
        return UserFacingException(str(e))
    if isinstance(e, heirs_mod.LocktimeNotValid):
        return UserFacingException(str(e))
    if isinstance(e, checkalive.CheckAliveAfterDeliveryError):
        return UserFacingException(
            _(
                "Check Alive must be after today and before the delivery date, please update it"
            )
        )
    if isinstance(e, checkalive.CheckAliveError):
        return UserFacingException(_check_alive_cli_message(None))
    if isinstance(e, (FileImportFailed, FileExportFailed)):
        # A wrong path or a bad file is the user's input, not a bug: a message
        # instead of a traceback and "internal error while executing RPC"
        # (CHANGELOG #104). The text already says what failed and why.
        return UserFacingException(str(e))
    return e


def _check_alive_cli_message(info):
    """Passed Check Alive on the command line (CHANGELOG #79).

    The command line never invalidates on its own: it explains what the GUI
    would ask and how to renew by hand.
    """
    how = _(
        "To renew: run bal_will_invalidate, sign and broadcast the "
        "transaction, then run bal_will_prepare once it is confirmed."
    )
    if info is None:
        return _("The Check Alive date has passed.") + " " + how
    if info.get("renewable") and info.get("has_will"):
        return check_alive_passed_sentence(info) + " " + how
    return check_alive_message(info)


class BalSettingsController:
    """Plugin settings (``bal_*`` config keys) for the command line.

    The settings belong to the plugin, not to a wallet, so this needs no
    wallet: ``bal_settings_*`` run with no wallet loaded (CHANGELOG #99;
    before, they always failed with "wallet not loaded").
    :class:`BalController` inherits these methods, so there is one copy of
    the code.
    """

    def __init__(self, plugin):
        """Keep the plugin whose ``BalConfig`` attributes are read and set."""
        self.plugin = plugin

    def _configs(self):
        """Return ``{ATTRIBUTE: BalConfig}`` for every plugin setting."""
        out = {}
        for attr in dir(self.plugin):
            if not attr.isupper():
                continue
            value = getattr(self.plugin, attr, None)
            if isinstance(value, BalConfig):
                out[attr] = value
        return out

    def _config_key(self, key):
        """Find a setting by attribute name (``TX_FEES``) or config key
        (``bal_tx_fees``); raise a clear message for an unknown one."""
        configs = self._configs()
        aliases = {}
        for attr, cfg in configs.items():
            aliases[attr.upper().replace("-", "_").replace(".", "_")] = (attr, cfg)
            aliases[cfg.name.upper().replace("-", "_").replace(".", "_")] = (attr, cfg)
        normalized = key.upper().replace("-", "_").replace(".", "_")
        if normalized not in aliases:
            raise UserFacingException(
                _("Unknown BAL setting: {} (use bal_settings_list)").format(key)
            )
        return aliases[normalized]

    def settings_list(self):
        """Every setting with its config key, default and current value."""
        out = {}
        for attr, cfg in self._configs().items():
            out[attr] = {
                "name": cfg.name,
                "default": cfg.default,
                "value": cfg.get(),
            }
        return out

    def settings_get(self, key):
        """One setting with its config key, default and current value."""
        attr, cfg = self._config_key(key)
        return {
            "key": attr,
            "name": cfg.name,
            "default": cfg.default,
            "value": cfg.get(),
        }

    def settings_reset(self, key):
        """Put one setting back to its default value."""
        attr, cfg = self._config_key(key)
        cfg.set(cfg.default)
        return {
            "key": attr,
            "name": cfg.name,
            "default": cfg.default,
            "value": cfg.get(),
        }

    def settings_set(self, key, value):
        """Set one setting; ``value`` is converted to the setting's type."""
        attr, cfg = self._config_key(key)
        coerced = self._coerce_config_value(cfg, value)
        cfg.set(coerced)
        return {
            "key": attr,
            "name": cfg.name,
            "default": cfg.default,
            "value": cfg.get(),
        }

    def _coerce_config_value(self, cfg, raw):
        """Convert the command-line text ``raw`` to the type of ``cfg``'s
        default (bool, int, JSON object or text)."""
        if isinstance(cfg.default, bool):
            if isinstance(raw, str):
                low = raw.strip().lower()
                if low in ("true", "1", "yes", "on"):
                    return True
                if low in ("false", "0", "no", "off"):
                    return False
                raise UserFacingException(
                    _("Invalid boolean for {}: {}").format(cfg.name, raw)
                )
            return bool(raw)
        if isinstance(cfg.default, int):
            try:
                return int(raw)
            except (TypeError, ValueError):
                raise UserFacingException(
                    _("Expected an integer for {}: {}").format(cfg.name, raw)
                ) from None
        if isinstance(cfg.default, dict):
            try:
                value = json.loads(raw) if isinstance(raw, str) else raw
            except (json.JSONDecodeError, TypeError):
                raise UserFacingException(
                    _("Expected a JSON object for {}").format(cfg.name)
                ) from None
            if not isinstance(value, dict):
                raise UserFacingException(
                    _("Expected a JSON object for {}").format(cfg.name)
                )
            return value
        return str(raw)


class BalController(BalSettingsController):
    """Headless per-wallet controller replicating :class:`BalWindow`.

    One instance wraps one wallet; commands construct it on demand with
    ``BalController(plugin, wallet)``.  It mirrors the GUI's state object
    (``will_settings``, ``heirs``, ``will``, ``willitems``, ``willexecutors``,
    ``date_to_check``) and its flows.  The settings methods come from
    :class:`BalSettingsController`.
    """

    def __init__(self, plugin, wallet):
        """Load the wallet's heirs, will and the plugin's settings and
        will-executors."""
        super().__init__(plugin)
        self.wallet = wallet
        self.heirs = {}
        self.will = {}
        self.willitems = {}
        self.willexecutors = {}
        self.will_settings = {}
        self.date_to_check = None
        self.no_willexecutor = False

        # The GUI wires ``plugin.get_decimal_point`` from the window; here the
        # daemon reads Electrum's global unit setting (falling back to 8 when
        # the config object does not expose it).
        plugin.get_decimal_point = self._get_decimal_point

        self._init_settings()
        self._init_heirs()
        self._init_will()
        self._init_willexecutors()

    # ------------------------------------------------------------------ #
    # Init
    # ------------------------------------------------------------------ #
    def _get_decimal_point(self):
        try:
            return self.plugin.config.BTC_AMOUNTS_DECIMAL_POINT
        except AttributeError:
            return 8

    def _init_settings(self):
        """Load the will settings; save the default the first time.

        The default holds absolute dates computed when the plugin starts
        (today + 1 year). Never saved, it moved by one day at every daemon
        restart on a new day, and a signed will then looked "postponed",
        asking for an on-chain invalidation (CHANGELOG #102). The GUI saves
        the settings as soon as the wallet window opens; this does the same.
        """
        stored = self.plugin.config.get(self.plugin.WILL_SETTINGS.name)
        self.will_settings = self.plugin.WILL_SETTINGS.get()
        if not self.will_settings:
            self.will_settings = self.plugin.default_will_settings()
        Util.fix_will_settings_tx_fees(self.will_settings)
        if not stored:
            self.plugin.WILL_SETTINGS.set(self.will_settings)

    def _init_heirs(self):
        self.heirs = Heirs._validate(Heirs(self.wallet))

    def _init_will(self):
        self.will = self.wallet.db.get_dict("will")
        Util.fix_will_tx_fees(self.will)
        self.load_willitems()

    def _init_willexecutors(self):
        self.willexecutors = Willexecutors.get_willexecutors(
            self.plugin, update=False, task=False
        )
        self.no_willexecutor = bool(self.plugin.NO_WILLEXECUTOR.get())

    def load_willitems(self):
        self.willitems = {}
        for wid, w in self.will.items():
            self.willitems[wid] = WillItem(w, wallet=self.wallet)

    def save_willitems(self):
        """Persist the in-memory willitems into the wallet's ``will`` dict.

        The transaction is stored serialized (exactly like the GUI, which
        avoids deep-copying a live Transaction holding a ``threading.RLock``)
        and every value is proven JSON-serializable before it is written.
        """
        keys = list(self.will.keys())
        for k in keys:
            del self.will[k]
        for wid, w in self.willitems.items():
            d = w.to_dict()
            d["tx"] = str(d["tx"])
            try:
                json.dumps(d, cls=MyEncoder)
            except Exception as e:
                _logger.error(f"save_willitems: will {wid} is not serializable: {e!r}")
                raise
            self.will[wid] = d
        self.wallet.save_db()

    def _save_to_history(self):
        """Persist the will state into the wallet's LOCAL history (best-effort).

        Mirrors ``BalWindow._save_will_to_history``: only when the
        ``SAVE_HISTORY`` setting is enabled, and never raises.
        """
        try:
            if not bool(self.plugin.SAVE_HISTORY.get()):
                return
            Will.save_valid_transactions_to_history(
                self.willitems, self.wallet, self.plugin.HISTORY_LABEL.get()
            )
        except Exception as e:
            _logger.error(f"save_to_history failed: {e}")

    # ------------------------------------------------------------------ #
    # Serialization helpers
    # ------------------------------------------------------------------ #
    def _willitem_summary(self, wi):
        out = {
            "txid": wi._id,
            "tx": str(wi.tx) if wi.tx is not None else None,
            "locktime": int(wi.tx.locktime) if wi.tx is not None else None,
            "heirs": list((wi.heirs or {}).keys()) if wi.heirs is not None else [],
            "tx_fees": int(wi.tx_fees),
            "description": wi.description,
            "sigs_have": int(getattr(wi, "sigs_have", 0)),
            "sigs_required": int(getattr(wi, "sigs_required", 0)),
        }
        if wi.we:
            out["willexecutor"] = wi.we.get("url")
        out["status"] = {}
        for key, value in wi.STATUS.items():
            out["status"][key] = bool(value[1])
        return out

    def _will_status_dict(self):
        items = [self._willitem_summary(w) for w in self.willitems.values()]
        counts = {}
        for w in self.willitems.values():
            for key, value in w.STATUS.items():
                counts[key] = counts.get(key, 0) + (1 if value[1] else 0)
        return {
            "count": len(items),
            "items": items,
            "status_counts": counts,
            "date_to_check": self.date_to_check,
        }

    def _tx_out(self, tx):
        if tx is None:
            return {"txid": None, "tx": None}
        return {"txid": tx.txid(), "tx": str(tx)}

    def _available_utxos(self):
        return Util.get_available_utxos(
            self.wallet,
            self.plugin.HISTORY_LABEL.get(),
            Will.get_min_locktime(self.willitems, default_value=self.date_to_check),
        )

    # ------------------------------------------------------------------ #
    # Core flows (mirror of BalWindow)
    # ------------------------------------------------------------------ #
    def _refresh_invalidated_items(self):
        """Mirror of ``BalWindow._refresh_invalidated_items`` (#79)."""
        try:
            utxos = Util.get_available_utxos(
                self.wallet,
                self.plugin.HISTORY_LABEL.get(),
                Will.get_min_locktime(self.willitems, default_value=time.time()),
            )
            Will.refresh_invalidated(self.willitems, utxos, self.wallet)
        except Exception as e:
            _logger.debug(f"refresh invalidated items failed: {e!r}")

    def check_alive_info(self):
        """``check_alive_renewal`` for this wallet (see BalWindow)."""
        try:
            return check_alive_renewal(
                self.plugin.is_basic_mode(),
                self.will_settings,
                built_locktime=Will.get_min_locktime(self.willitems),
                creation_date=Will.get_creation_time(self.willitems),
            )
        except Exception as e:
            _logger.debug(f"check alive info failed: {e!r}")
            return None

    def _normalize_check_alive(self):
        """Mirror of ``BalWindow._normalize_check_alive`` (#80)."""
        try:
            old = self.will_settings.get("threshold")
            new = normalize_check_alive(
                self.will_settings,
                built_locktime=Will.get_min_locktime(self.willitems),
                creation_date=Will.get_creation_time(self.willitems),
            )
            if new != old:
                _logger.info(f"check alive converted: {old!r} -> {new}")
                self.will_settings["threshold"] = new
                self.plugin.WILL_SETTINGS.set(self.will_settings)
        except Exception as e:
            _logger.debug(f"check alive conversion failed: {e!r}")

    def init_class_variables(self):
        if not self.heirs:
            raise will_mod.NoHeirsException(_("Heirs are not defined"))
        # Items already invalidated on-chain must not anchor the dates (#79).
        self._refresh_invalidated_items()
        # An old Check Alive value becomes "N days before the delivery" (#80).
        self._normalize_check_alive()
        self.date_to_check = resolve_date_to_check(
            self.plugin.is_basic_mode(),
            self.will_settings,
            built_locktime=Will.get_min_locktime(self.willitems),
            creation_date=Will.get_creation_time(self.willitems),
        )
        self.no_willexecutor = bool(self.plugin.NO_WILLEXECUTOR.get())
        self.willexecutors = Willexecutors.get_willexecutors(
            self.plugin, update=True, task=False
        )
        delivery_date = resolve_delivery_date(
            self.will_settings,
            built_locktime=Will.get_min_locktime(self.willitems),
        )
        ensure_check_alive_valid(
            self.plugin.is_basic_mode(),
            self.date_to_check,
            delivery_date=delivery_date,
        )
        self._init_heirs_to_locktime(self.plugin.ENABLE_MULTIVERSE.get())

    def _init_heirs_to_locktime(self, multiverse=False):
        if multiverse:
            return
        locktime = self.will_settings["locktime"]
        if not isinstance(locktime, (int, float, str)):
            locktime = str(locktime)
        updates = {
            heir: [self.heirs[heir][0], self.heirs[heir][1], locktime]
            for heir in list(self.heirs)
        }
        for heir, value in updates.items():
            self.heirs[heir] = value
        self.wallet.save_db()

    def check_will(self):
        return Will.is_will_valid(
            self.willitems,
            self.date_to_check,
            self.will_settings["baltx_fees"],
            self._available_utxos(),
            heirs=self.heirs,
            willexecutors=self.willexecutors,
            self_willexecutor=self.no_willexecutor,
            wallet=self.wallet,
        )

    def build_will(self, ignore_duplicate=True, keep_original=True):
        """Build (or rebuild) the inheritance transactions.

        Mirrors ``BalWindow.build_will``; raises ``NoWillExecutorNotPresent``
        when no valid will-executor is selected and the user is not their own
        executor.
        """
        will = {}
        # Drop stale wallet-LOCAL will placeholders (mirror of the GUI
        # build_will) so their coins are available to this build.
        Will.remove_stale_wallet_history(
            self.wallet, self.plugin.HISTORY_LABEL.get()
        )
        # A (re)build may have anticipated the delivery (shorter heir recipes)
        # while ``date_to_check`` is still anchored to the OLD built will.
        # Recompute it for the will being built (earliest future delivery among
        # the CURRENT heirs), mirroring ``BalWindow.build_will``, so the
        # anticipated dates pass the build filter.
        _new_locktime = min(
            (
                Util.parse_locktime_string(h[2])
                for h in self.heirs.values()
            ),
            default=None,
        )
        if _new_locktime:
            self.date_to_check = resolve_date_to_check(
                self.plugin.is_basic_mode(), self.will_settings,
                built_locktime=_new_locktime,
            )
        self.willexecutors = Willexecutors.get_willexecutors(
            self.plugin, update=False, task=False
        )
        if not self.no_willexecutor:
            valid = False
            for _u, w in self.willexecutors.items():
                if Willexecutors.is_selected(w) and Willexecutors.is_valid(
                    w,
                    max_fee=self.plugin.MAX_WILLEXECUTOR_FEE.get(),
                    dust=self.wallet.dust_threshold(),
                ):
                    valid = True
            if not valid:
                raise will_mod.NoWillExecutorNotPresent(
                    "No Will-Executor or backup transaction selected"
                )
        txs = self.heirs.get_transactions(
            self.plugin,
            self.wallet,
            self.will_settings["baltx_fees"],
            self._available_utxos(),
            self.date_to_check,
        )
        creation_time = time.time()
        if txs:
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
                will[txid] = WillItem(tx, _id=txid, wallet=self.wallet)
            Will.update_will(self.willitems, will)
            self.willitems.update(will)
            Will.normalize_will(self.willitems, self.wallet)
        else:
            _logger.info("No transactions was built")
            return {}
        return self.willitems

    def invalidate_will(self, will=None):
        """Build the on-chain invalidation transaction (real fee).

        Returns the raw ``PartialTransaction`` (or ``None`` when there is
        nothing to invalidate); the caller decides how to show/sign it.
        """
        willitems = will if will is not None else self.willitems
        fee_per_byte = self.will_settings.get("baltx_fees", 1)
        tx = Will.invalidate_will(
            willitems,
            self.wallet,
            fee_per_byte,
            history_label=self.plugin.HISTORY_LABEL.get(),
            will_locktime=Will.get_min_locktime(
                willitems, default_value=self.date_to_check
            ),
        )
        if tx is not None:
            try:
                self.wallet.set_label(tx.txid(), "Safe21 Heartbeat invalidation")
            except Exception as e:
                _logger.debug(f"invalidate_will set_label failed: {e}")
        return tx

    def invalidate_will_command(self):
        """Command-facing wrapper of :meth:`invalidate_will` (JSON-safe)."""
        return self._tx_out(self.invalidate_will())

    def will_status(self):
        """Command-facing snapshot of the current will (JSON-safe)."""
        return self._will_status_dict()

    def will_check(self):
        """Check the local coherence of the will (heirs, executors, fees).

        Returns ``{"valid": true}`` or raises a translated domain exception.
        """
        try:
            self.init_class_variables()
            self.check_will()
        except Exception as e:
            raise _user_facing(e) from e
        return {"valid": True}

    def prepare_will(self, ignore_duplicate=True, keep_original=True):
        """Run the full "prepare inheritance" flow and return a status dict.

        Mirrors ``BalWindow.build_inheritance_transaction``:

        * ``coherent`` -> the existing will is still valid; nothing rebuilt.
        * ``rebuilt``  -> the will was not coherent and has been rebuilt;
          ``reason`` says why, in the words of the Check dialog.
        * ``expired`` / ``postponed`` -> an invalidation transaction must be
          signed and broadcast before a new will can be prepared.
        """
        if not self.heirs:
            raise UserFacingException(_("Heirs are not defined: add at least one heir first"))

        self.init_class_variables()
        if self.date_to_check is None:
            raise UserFacingException(_("cannot resolve the check-alive date"))
        date_to_check: float = self.date_to_check
        try:
            Will.check_amounts(
                self.heirs,
                self.willexecutors,
                self._available_utxos(),
                date_to_check,
                self.wallet.dust_threshold(),
                max_fee=self.plugin.MAX_WILLEXECUTOR_FEE.get(),
            )
        except Exception as e:
            raise _user_facing(e) from e

        locktime = Util.parse_locktime_string(self.will_settings["locktime"])
        threshold_ts = resolve_guard_threshold(
            self.plugin.is_basic_mode(),
            self.will_settings,
            creation_date=Will.get_creation_time(self.willitems),
        )
        if threshold_ts is not None:
            if locktime < threshold_ts:
                raise UserFacingException(_("locktime is lower than threshold"))
        elif locktime < date_to_check:
            raise UserFacingException(_("locktime is lower than threshold"))

        if not self.no_willexecutor:
            valid = False
            for _k, we in self.willexecutors.items():
                if Willexecutors.is_selected(we) and Willexecutors.is_valid(
                    we,
                    max_fee=self.plugin.MAX_WILLEXECUTOR_FEE.get(),
                    dust=self.wallet.dust_threshold(),
                ):
                    valid = True
            if not valid:
                raise UserFacingException(
                    _("no backup transaction or willexecutor selected")
                )

        try:
            self.check_will()
            self.save_willitems()
            return {
                "result": "coherent",
                "message": _("The will is coherent"),
                "will": self._will_status_dict(),
            }
        except will_mod.WillExpiredException:
            inv = self._tx_out(self.invalidate_will())
            return {
                "result": "expired",
                "message": _(
                    "The will is expired: sign and broadcast the invalidation "
                    "transaction, then prepare again"
                ),
                "invalidation_tx": inv,
                "will": self._will_status_dict(),
            }
        except will_mod.WillPostponedException as e:
            _logger.info(f"will postponed: {e}")
            inv = self._tx_out(self.invalidate_will())
            return {
                "result": "postponed",
                "message": _(
                    "This inheritance was already signed/sent to will-executors "
                    "and you are postponing it. Sign and broadcast the "
                    "invalidation transaction now, then prepare again."
                ),
                "invalidation_tx": inv,
                "will": self._will_status_dict(),
            }
        except will_mod.NotCompleteWillException as e:
            _logger.info(f"will not coherent ({type(e).__name__}): rebuilding")
            # CHANGELOG #78: say WHY it is rebuilt (returned as "reason"),
            # computed now: the rebuild below changes what the core reports.
            reason = will_change_message(e)
            self.build_will(ignore_duplicate, keep_original)
            rebuilt_ok = False
            try:
                self.check_will()
                for wid, _w in self.willitems.items():
                    try:
                        self.wallet.set_label(wid, "Safe21 Heartbeat backup transaction")
                    except Exception as label_err:
                        _logger.debug(f"prepare_will set_label failed: {label_err}")
                rebuilt_ok = True
            except will_mod.WillExpiredException:
                inv = self._tx_out(self.invalidate_will())
                return {
                    "result": "rebuilt_expired",
                    "message": _(
                        "The rebuilt will is expired: invalidate on-chain, "
                        "then prepare again"
                    ),
                    "invalidation_tx": inv,
                    "will": self._will_status_dict(),
                }
            except will_mod.NotCompleteWillException as e2:
                raise UserFacingException(
                    _("Error: {} Please, check your heirs, locktime and threshold!").format(
                        str(e2)
                    )
                ) from e2
            self.save_willitems()
            if rebuilt_ok:
                self._save_to_history()
                return {
                    "result": "rebuilt",
                    "message": _(
                        "The will was rebuilt and needs to be signed and "
                        "broadcast again"
                    ),
                    "reason": reason,
                    "will": self._will_status_dict(),
                }
            raise UserFacingException(_("will not rebuilt")) from None

    def auto_rebuild(self):
        """Headless equivalent of the GUI's automatic rebuild flow (one shot).

        Re-runs the same check the wizard runs at wallet close - anticipation
        of the delivery date by one day (orphaning the old will on-chain),
        on-chain invalidation only when the anticipated locktime crosses the
        Check Alive threshold - and, when the will is rebuilt, signs it
        (passwordless wallets only) and pushes it to its will-executors.
        A Check Alive already in the past never invalidates on its own
        (CHANGELOG #79): ``check_alive_passed`` is returned instead.

        Returns a JSON object with ``result``:

        * ``valid``         -> the will is still coherent; nothing was done.
        * ``no_heirs``      -> no valid heirs are configured.
        * ``check_alive_passed`` -> the Check Alive date has passed; nothing
          was done. ``message`` says how to renew (``renewable`` tells whether
          a renewal with the current settings is possible).
        * ``invalidated``   -> the old will must be invalidated on-chain first:
          the returned ``invalidation_tx`` must be signed and broadcast, then
          the command re-run.
        * ``nothing``       -> nothing was built.
        * ``needs_signing`` -> the will was rebuilt but this wallet is
          encrypted: run ``bal_will_sign`` (with the password) then
          ``bal_will_broadcast``.
        * ``rebuilt``       -> the will was rebuilt, signed and pushed; ``push``
          maps every will-executor URL to its broadcast status.
        """
        try:
            self.init_class_variables()
        except will_mod.NoHeirsException as e:
            _logger.info(f"auto rebuild: no heirs ({e})")
            return {"result": "no_heirs"}
        except CheckAliveAfterDeliveryError:
            _logger.info(
                "auto rebuild: Check Alive not before the delivery, nothing to do"
            )
            return {"result": "check_alive_before_delivery"}
        except CheckAliveError:
            # CHANGELOG #79: never invalidate on our own for a passed Check
            # Alive (the GUI used to be left with no will in force). Say so
            # and how to renew; the owner decides.
            _logger.info("auto rebuild: Check Alive already in the past")
            info = self.check_alive_info()
            if info and info.get("reason") == "expired":
                # Past the delivery date: the expired case, as before.
                return self._auto_rebuild_invalidate("expired")
            return {
                "result": "check_alive_passed",
                "renewable": bool(info and info.get("renewable")),
                "message": _check_alive_cli_message(info),
            }

        try:
            self.check_will()
            return {"result": "valid", "will": self._will_status_dict()}
        except will_mod.NoHeirsException as e:
            _logger.info(f"auto rebuild: no heirs ({e})")
            return {"result": "no_heirs"}
        except (will_mod.WillExpiredException, will_mod.WillPostponedException) as e:
            _logger.info(f"auto rebuild: {type(e).__name__}: must invalidate")
            return self._auto_rebuild_invalidate("expired")
        except will_mod.NotCompleteWillException:
            pass

        try:
            txs = self.build_will()
        except Exception as e:
            raise _user_facing(e) from e
        if not txs:
            _logger.info("auto rebuild: nothing was built")
            return {"result": "nothing"}

        try:
            self.check_will()
        except will_mod.NoHeirsException as e:
            _logger.info(f"auto rebuild: no heirs ({e})")
            return {"result": "no_heirs"}
        except (will_mod.WillExpiredException, will_mod.WillPostponedException) as e:
            _logger.info(
                f"auto rebuild: rebuilt will {type(e).__name__}: must invalidate"
            )
            return self._auto_rebuild_invalidate("anticipation_crossed")
        except will_mod.NotCompleteWillException:
            pass

        if self.wallet.has_keystore_encryption():
            _logger.warning(
                "auto rebuild: wallet is encrypted, signing requires a password"
            )
            self.save_willitems()
            self._save_to_history()
            return {
                "result": "needs_signing",
                "message": _(
                    "The will was rebuilt but this wallet is encrypted: sign it "
                    "with bal_will_sign and push it with bal_will_broadcast"
                ),
                "will": self._will_status_dict(),
            }

        try:
            self.sign_transactions(None)
        except Exception as e:
            raise _user_facing(e) from e
        push = {}
        try:
            push = self.push_transactions_to_willexecutors()
        except Exception as e:
            _logger.error(f"auto rebuild: push failed: {e}")
            push = {"_error": True, "_message": str(e)}
        return {
            "result": "rebuilt",
            "message": _(
                "The will was rebuilt, signed and pushed to its will-executors"
            ),
            "push": push,
            "will": self._will_status_dict(),
        }

    def _auto_rebuild_invalidate(self, reason):
        """Build the invalidation transaction for :meth:`auto_rebuild`.

        Returns the ``invalidated`` result dict; the transaction is NOT signed
        nor broadcast (the CLI never sends coins without being told to).
        """
        inv = self._tx_out(self.invalidate_will())
        return {
            "result": "invalidated",
            "reason": reason,
            "invalidation_tx": inv,
            "message": _(
                "Sign and broadcast the invalidation transaction, then run "
                "bal_will_autorebuild again"
            ),
        }

    def sign_transactions(self, password, txids=None):
        """Sign the valid will transactions (or a subset given by ``txids``).

        Returns ``{txid: raw_tx}`` for every signed transaction.  Raises
        ``UserFacingException`` when the wallet is encrypted and no password is
        given.
        """
        willitems = self.willitems
        if password is None and self.wallet.has_keystore_encryption():
            raise UserFacingException(_("Password required to sign transactions"))

        if txids is not None:
            targets = [
                t for t in txids if t in willitems and willitems[t].get_status("VALID")
            ]
        else:
            targets = Will.only_valid(willitems)

        txs = {}
        for txid in targets:
            wi = willitems[txid]
            tx = Will.get_tx_from_any(str(wi.tx))
            if wi.get_status("COMPLETE"):
                txs[txid] = tx
                continue
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

            self.wallet.sign_transaction(tx, password, ignore_warnings=True)
            if tx.is_complete():
                wi.set_status("COMPLETE", True)
            try:
                have, required = tx.signature_count()
                wi.sigs_have = int(have)
                wi.sigs_required = int(required)
            except Exception as e:
                _logger.debug(f"signature_count after signing failed: {e}")
            wi.tx = Will.get_tx_from_any(str(tx))
            txs[txid] = tx

        try:
            Will.check_signatures(willitems, self.wallet)
        except Exception as e:
            _logger.error(f"check_signatures after signing failed: {e}")
        self.save_willitems()
        self._save_to_history()
        return txs

    def push_transactions_to_willexecutors(self, force=False, txids=None):
        """Push the valid+signed will transactions to their will-executors.

        Returns ``{url: broadcast_status}`` and raises ``UserFacingException``
        when no transaction matched the (optional) filter.
        """
        willitems = self.willitems
        if txids is not None:
            willitems = {t: willitems[t] for t in txids if t in willitems}
        if not willitems:
            raise UserFacingException(_("No transaction matches the given txids"))

        # Skip executors the user deselected after building the will (the
        # controller's list is read from the config for every command).
        willexecutors = Willexecutors.get_willexecutor_transactions(
            willitems, force=force, current_willexecutors=self.willexecutors
        )
        if not willexecutors:
            return {}

        for url in willexecutors:
            willexecutors[url].setdefault("broadcast_status", _("waiting..."))

        error = {"flag": False}
        already_present = []

        def on_each(url, willexecutor, ok, exc):
            if isinstance(exc, Willexecutors.AlreadyPresentException):
                already_present.append(url)
                willexecutor["broadcast_status"] = _("checking...")
            elif ok:
                for wid in willexecutor.get("txsids", []):
                    willitems[wid].set_status("PUSHED", True)
                willexecutor["broadcast_status"] = _("Success")
            else:
                for wid in willexecutor.get("txsids", []):
                    willitems[wid].set_status("PUSH_FAIL", True)
                error["flag"] = True
                willexecutor["broadcast_status"] = _("Failed")
            willexecutor.pop("txs", None)

        Willexecutors.push_transactions_parallel(willexecutors, on_each=on_each)

        for url in already_present:
            willexecutor = willexecutors[url]
            for wid in willexecutor.get("txsids", []):
                w = self.willitems[wid]
                try:
                    w.set_check_willexecutor(
                        Willexecutors.check_transaction(wid, w.we["url"])
                    )
                except Exception as e:
                    _logger.error(f"check after already-present failed for {wid}: {e}")
                    w.set_check_willexecutor(None)

        self.save_willitems()
        out = {
            url: we.get("broadcast_status", _("unknown"))
            for url, we in willexecutors.items()
        }
        if error["flag"]:
            out["_error"] = True
        return out

    def check_transactions(self, txids=None):
        """Ask every will-executor whether it holds our (pushed) transactions.

        Returns ``{txid: {pushed, checked, check_fail}}``.
        """
        targets = []
        for wid, w in self.willitems.items():
            if not w.we:
                continue
            if txids is not None and wid not in txids:
                continue
            if Will.needs_server_check(w):
                targets.append((wid, w.we["url"]))

        def on_each(wid, url, res, exc):
            try:
                self.willitems[wid].set_check_willexecutor(res)
            except Exception as e:
                _logger.error(f"check on_each error for {wid}: {e}")

        def on_timeout(wid, url):
            try:
                self.willitems[wid].set_check_willexecutor(None)
            except Exception as e:
                _logger.error(f"check on_timeout error for {wid}: {e}")

        Willexecutors.check_transactions_parallel(
            targets, on_each=on_each, on_timeout=on_timeout
        )

        self.save_willitems()
        out = {}
        for wid, w in self.willitems.items():
            if w.we:
                out[wid] = {
                    "url": w.we.get("url"),
                    "pushed": bool(w.get_status("PUSHED")),
                    "checked": bool(w.get_status("CHECKED")),
                    "check_fail": bool(w.get_status("CHECK_FAIL")),
                }
        return out

    def export_will(self, path):
        """Export the whole will to ``path`` (JSON) and flag items EXPORTED."""
        for wid in self.willitems:
            self.willitems[wid].set_status("EXPORTED", True)
            self.will[wid] = self.willitems[wid].to_dict()
        write_json_file(path, self.will)
        self.save_willitems()
        return {"exported": len(self.willitems), "path": path}

    def _load_will_file(self, path):
        data = read_json_file(path)
        willitems = {}
        for k, v in data.items():
            data[k]["tx"] = tx_from_any(v["tx"])
            willitems[k] = WillItem(data[k], _id=k)
        return willitems

    def merge_will(self, imported):
        """Merge imported will items into the live will.

        Mirrors ``BalWindow.merge_will``: operational statuses are carried over,
        unsigned live transactions are combined/substituted, new transactions
        are added wholesale, then a local validity check recomputes the
        valid/invalidated/replaced statuses.
        """
        if self.date_to_check is None:
            self.date_to_check = resolve_date_to_check(
                self.plugin.is_basic_mode(), self.will_settings
            )
        for wid, wi in imported.items():
            if wid in self.willitems:
                live = self.willitems[wid]
                was_complete = live.get_status("COMPLETE")
                for status in (
                    "COMPLETE",
                    "PUSHED",
                    "CHECKED",
                    "MEMPOOL",
                    "CONFIRMED",
                ):
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
                    if live.tx.is_complete():
                        live.set_status("COMPLETE", True)
            else:
                self.willitems[wid] = wi
        Will.normalize_will(self.willitems, self.wallet)
        self.save_willitems()
        try:
            Will.add_willtree(self.willitems)
            all_utxos = self._available_utxos()
            Will.check_invalidated(
                self.willitems, Will.utxos_strs(all_utxos), self.wallet
            )
            Will.search_rai(
                Will.get_all_inputs(self.willitems, only_valid=True),
                all_utxos,
                self.willitems,
                self.wallet,
            )
            Will.check_signatures(self.willitems, self.wallet)
        except Exception as e:
            _logger.error(f"merge_will validity check failed: {e}")
        self.save_willitems()
        return {"merged": len(imported)}

    def merge_will_from_file(self, path):
        try:
            willitems = self._load_will_file(path)
        except Exception as e:
            raise UserFacingException(_("Invalid will file: {}").format(e)) from None
        Will.normalize_will(willitems, self.wallet)
        return self.merge_will(willitems)

    # ------------------------------------------------------------------ #
    # Heirs CRUD
    # ------------------------------------------------------------------ #
    def _build_heir_entry(self, name, address, amount, locktime):
        heir = [name, address, amount]
        if locktime is not None:
            heir.append(locktime)
        else:
            heir.append(self.will_settings["locktime"])
        if is_op_return_address(address):
            heir[2] = "0"
        return Heirs.validate_heir(heir[0], heir[1:])

    def _check_heir_locktime(self, locktime):
        """Refuse a per-heir delivery date that would not be used, or is wrong.

        CHANGELOG #103. With "multiverse" off (the default, and the only mode
        of the GUI) every heir gets the will's delivery date: the next
        check/prepare overwrote a per-heir ``--locktime`` without a word, so
        it is refused with a message that says how to change the date.
        With multiverse on, a date that is not understood ("domani" gave 0)
        or already past is refused; before, both were accepted.
        """
        if locktime is None:
            return
        if not self.plugin.ENABLE_MULTIVERSE.get():
            raise UserFacingException(
                _(
                    "Every heir gets the delivery date of the will, so --locktime "
                    "cannot be set for one heir. To change the date for every "
                    'heir, change "locktime" in the will settings: '
                    "bal_settings_get bal_will_settings, then bal_settings_set "
                    "bal_will_settings with the new JSON."
                )
            )
        when = Util.parse_locktime_string(locktime)
        if not when:
            raise UserFacingException(
                _(
                    "Delivery date not understood: {} (use a timestamp, or a "
                    "time from now such as 30d or 1y)"
                ).format(locktime)
            )
        if when <= time.time():
            raise UserFacingException(
                _("The delivery date is in the past: {}").format(locktime)
            )

    def heirs_add(self, name, address, amount, locktime=None):
        """Add (or replace) the heir ``name``; ``locktime`` only with
        multiverse on (see :meth:`_check_heir_locktime`)."""
        self._check_heir_locktime(locktime)
        value = self._build_heir_entry(name, address, amount, locktime)
        self.heirs[name] = value
        self.wallet.save_db()
        return {"name": name, "value": list(value)}

    def heirs_update(self, name, address=None, amount=None, locktime=None):
        """Change only the given fields of the heir ``name``; ``locktime``
        only with multiverse on (see :meth:`_check_heir_locktime`)."""
        if name not in self.heirs:
            raise UserFacingException(_("Heir not found: {}").format(name))
        self._check_heir_locktime(locktime)
        current = list(self.heirs[name])
        address = address if address is not None else current[0]
        amount = amount if amount is not None else current[1]
        locktime = locktime if locktime is not None else current[2]
        value = self._build_heir_entry(name, address, amount, locktime)
        self.heirs[name] = value
        self.wallet.save_db()
        return {"name": name, "value": list(value)}

    def heirs_delete(self, names):
        deleted = []
        for name in names:
            if name in self.heirs:
                self.heirs.pop(name)
                deleted.append(name)
        self.heirs.save()
        self.wallet.save_db()
        return {"deleted": deleted}

    def heirs_list(self):
        return {k: list(v) for k, v in self.heirs.items()}

    def heirs_show(self, name):
        if name not in self.heirs:
            raise UserFacingException(_("Heir not found: {}").format(name))
        return {"name": name, "value": list(self.heirs[name])}

    def heirs_import(self, path):
        self.heirs.import_file(path)
        self.wallet.save_db()
        return {"imported": len(self.heirs)}

    def heirs_export(self, path):
        self.heirs.export_file(path)
        return {"exported": len(self.heirs), "path": path}

    # ------------------------------------------------------------------ #
    # Will-Executors CRUD
    # ------------------------------------------------------------------ #
    def willexecutors_list(self):
        return {url: dict(we) for url, we in self.willexecutors.items()}

    def willexecutors_show(self, url):
        if url not in self.willexecutors:
            raise UserFacingException(_("Will-Executor not found: {}").format(url))
        return {"url": url, "willexecutor": dict(self.willexecutors[url])}

    def _validate_executor_address(self, address):
        if address and not bitcoin.is_address(address, net=constants.net):
            raise UserFacingException(
                _("Invalid will-executor address for this network: {}").format(address)
            )

    def willexecutors_add(self, url, address="", base_fee=0, info=None):
        if not url:
            raise UserFacingException(_("URL is required"))
        if url in self.willexecutors:
            raise UserFacingException(_("Will-Executor already present: {}").format(url))
        self._validate_executor_address(address)
        info = (info or "").strip() or "New Will Executor"
        self.willexecutors[url] = {
            "info": info,
            "base_fee": int(base_fee),
            "address": address,
            "selected": False,
            "status": "-1",
            "promo_code": None,
        }
        Willexecutors.save(self.plugin, self.willexecutors)
        return self.willexecutors_show(url)

    def willexecutors_update(self, url, address=None, base_fee=None, info=None,
                             promo_code=None, rename_to=None):
        """Change only the given fields of a will-executor and save.

        ``promo_code`` is in the same place as in ``bal_willexecutors_update``,
        which passes every field by position (CHANGELOG #101: without it the
        command failed with a ``TypeError``). An empty promo code removes it.
        """
        if url not in self.willexecutors:
            raise UserFacingException(_("Will-Executor not found: {}").format(url))
        we = self.willexecutors[url]
        if address is not None:
            self._validate_executor_address(address)
            we["address"] = address
        if base_fee is not None:
            we["base_fee"] = int(base_fee)
        if info is not None:
            we["info"] = info.strip() or "New Will Executor"
        if promo_code is not None:
            we["promo_code"] = promo_code.strip() or None
        if rename_to and rename_to != url:
            if rename_to in self.willexecutors:
                raise UserFacingException(
                    _("Will-Executor already present: {}").format(rename_to)
                )
            self.willexecutors[rename_to] = we
            del self.willexecutors[url]
            url = rename_to
        Willexecutors.save(self.plugin, self.willexecutors)
        return self.willexecutors_show(url)

    def willexecutors_delete(self, urls):
        deleted = []
        for url in urls:
            if url in self.willexecutors:
                del self.willexecutors[url]
                deleted.append(url)
        Willexecutors.save(self.plugin, self.willexecutors)
        return {"deleted": deleted}

    def willexecutors_select(self, urls=None, select=True):
        """Select/deselect one, many or all will-executors."""
        selected = {}
        targets = urls if urls is not None else list(self.willexecutors)
        for url in targets:
            if url not in self.willexecutors:
                continue
            self.willexecutors[url]["selected"] = bool(select)
            selected[url] = bool(select)
        Willexecutors.save(self.plugin, self.willexecutors)
        return selected

    def willexecutors_ping(self, urls=None):
        """Ping the (selected) will-executor servers and refresh their info.

        Returns ``{url: {"status": int, "ok": bool}}``.
        """
        targets = {}
        if urls is not None:
            for url in urls:
                if url not in self.willexecutors:
                    raise UserFacingException(
                        _("Will-Executor not found: {}").format(url)
                    )
                targets[url] = self.willexecutors[url]
        else:
            targets = {
                url: we
                for url, we in self.willexecutors.items()
                if Willexecutors.is_selected(we)
            }
        if not targets:
            raise UserFacingException(_("No will-executor is selected"))
        results = {}

        def on_each(url, we, ok):
            results[url] = {"status": we.get("status"), "ok": bool(ok)}

        Willexecutors.ping_servers_parallel(targets, on_each=on_each)
        Willexecutors.save(self.plugin, self.willexecutors)
        return results

    def _fetch_will_executors_list(self):
        """Download the will-executor list from the welist server.

        Mirrors ``BalWindow.fetch_will_executors_list`` (welist URL selection,
        Tor gating for ``.onion`` servers, per-entry validation).  Returns the
        downloaded dict, ``{}`` on failure.
        """
        chainname = BalPlugin.chainname
        basic = self.plugin.is_basic_mode()
        if basic:
            base = self.plugin.WELIST_SERVER.default
        else:
            base = self.plugin.WELIST_SERVER.get()
        base = base if base.endswith("/") else base + "/"
        url = f"{base}data/{chainname}?page=0&limit=100"

        result = {}
        last_error = None
        try:
            resp = Willexecutors.send_request(
                "get", url, timeout=10, max_retries=1, retry_sleep=1
            )
            if not isinstance(resp, dict):
                last_error = "invalid response format"
                _logger.warning(
                    f"fetch_will_executors_list: {url} -> unexpected response "
                    f"type {type(resp).__name__}, ignoring"
                )
            else:
                result = resp
                tor_on = is_tor_active()
                for w in list(result.keys()):
                    if w in ("status", "url"):
                        continue
                    if not isinstance(result.get(w), dict):
                        del result[w]
                        continue
                    if not tor_on and is_onion_url(w):
                        del result[w]
                        continue
                    Willexecutors.initialize_willexecutor(
                        result[w], w, None, self.willexecutors.get(w, None)
                    )
        except Exception as e:
            last_error = str(e)
            _logger.error(f"fetch_will_executors_list: {url} -> {type(e).__name__}: {e}")

        if not result and not basic:
            raise UserFacingException(
                _("Could not reach the configured welist server.\nServer: {}\nError: {}").format(
                    url, last_error or "empty response"
                )
            )
        return result

    def willexecutors_download(self):
        """Download the will-executor list and merge it into the local one."""
        result = self._fetch_will_executors_list()
        if result:
            self.willexecutors.update(result)
            Willexecutors.save(self.plugin, self.willexecutors)
        return {"downloaded": len(result), "total": len(self.willexecutors)}

    def willexecutors_import(self, path):
        data = read_json_file(path)
        if not isinstance(data, dict):
            raise UserFacingException(_("Invalid will-executors file"))
        for url, we in data.items():
            if not isinstance(we, dict):
                raise UserFacingException(
                    _("Invalid entry {} in will-executors file").format(url)
                )
            if url not in self.willexecutors:
                we = dict(we)
                we.setdefault("selected", False)
                we.setdefault("status", "New")
                we.setdefault("promo_code", None)
                self.willexecutors[url] = we
        Willexecutors.save(self.plugin, self.willexecutors)
        return {"imported": len(data), "total": len(self.willexecutors)}

    def willexecutors_export(self, path):
        write_json_file(path, self.willexecutors)
        return {"exported": len(self.willexecutors), "path": path}


def get_decimal_point(plugin):
    """Standalone helper returning Electrum's configured BTC decimal point."""
    try:
        return plugin.config.BTC_AMOUNTS_DECIMAL_POINT
    except AttributeError:
        return 8
