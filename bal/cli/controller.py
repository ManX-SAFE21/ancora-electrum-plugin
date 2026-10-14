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
from collections.abc import Mapping

from electrum import bitcoin, constants
from electrum.i18n import _
from electrum.logging import get_logger
from electrum.transaction import tx_from_any
from electrum.util import (
    FileExportFailed,
    FileImportFailed,
    UserFacingException,
    read_json_file,
    write_json_file,
)

from ..core import checkalive, delivery
from ..core import heirs as heirs_mod
from ..core import will as will_mod
from ..core.checkalive import (
    CheckAliveAfterDeliveryError,
    CheckAliveError,
    resolve_date_to_check,
    resolve_guard_threshold,
)
from ..core.delivery import DeliveryHold
from ..core.heirs import Heirs, is_op_return_address
from ..core.messages import (
    check_alive_message,
    check_alive_passed_sentence,
    heir_error_message,
    protected_executor_message,
    will_change_message,
)
from ..core.plugin_base import BalConfig, BalPlugin
from ..core.util import Util
from ..core.will import Will, WillItem
from ..core.willexecutors import Willexecutors, is_onion_url, is_tor_active
from ..engine import build, context

_logger = get_logger(__name__)


def _user_facing(e):
    """Translate a ``bal.core`` domain exception into a user-facing message.

    The "will no longer coherent" family uses the same sentences as the Check
    dialog (``bal.core.messages``, CHANGELOG #78). Unknown exceptions are passed through unchanged so their real type reaches
    the caller (and, eventually, the Electrum log).
    """
    if isinstance(e, UserFacingException):
        return e
    if isinstance(e, DeliveryHold):
        return UserFacingException(_delivery_hold_message(e))
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
    if isinstance(e, (heirs_mod.NotAnAddress, heirs_mod.AmountNotValid,
                      heirs_mod.LocktimeNotValid)):
        # #135: the same plain sentence as the "Edit heir" dialog (one
        # helper; CHANGELOG #104: never "not an address, not an address").
        return UserFacingException(heir_error_message(e))
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


def _as_name_list(names):
    """A JSON array of names, or one name alone, as a list of strings.

    Electrum decodes a positional argument as JSON when it can: ``'["a"]'``
    becomes a list, a plain ``alice`` stays text and ``2024`` becomes a
    number. Iterating a text gave its letters (CHANGELOG #105).
    """
    if not isinstance(names, (list, tuple)):
        names = [names]
    return [str(n) for n in names]


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


# CHANGELOG #123: the delivery date and the fee rate are kept by each wallet,
# not in Electrum's global ``bal_will_settings`` (BAL's key, never written).
_WILL_SETTINGS_MOVED = (
    "bal_will_settings is not used any more: each wallet keeps its own "
    "delivery date and fee rate. See them with bal_delivery_show and change "
    "them with bal_delivery_set (--date YYYY-MM-DD, --fee N)."
)
# The setting names refused with that pointer (attribute and config key,
# normalized as in ``BalSettingsController._config_key``).
_MOVED_SETTINGS = ("WILL_SETTINGS", "BAL_WILL_SETTINGS")


def _delivery_hold_message(e, settings=None):
    """The command-line text of a hold of the delivery settings (#123).

    ``e``: ``DeliveryChoicePending`` or ``DeliverySettingsUnavailable``.
    ``settings``: the wallet's settings, for the two dates of the "which
    date?" question (without them the text has no dates). Says that nothing
    was done and which command answers. English, like every command-line
    text.
    """
    if isinstance(e, delivery.DeliverySettingsUnavailable):
        return _(
            "The delivery settings of this wallet could not be prepared: "
            "nothing was changed. Please restart Electrum and send the log."
        )
    dates = delivery.choice_dates(settings) if settings is not None else None
    keep, later = dates if dates else (None, None)
    if keep is None or later is None:
        return _(
            "The delivery date must be confirmed first, so nothing was built, "
            "signed or sent. See bal_delivery_show, then keep the date of the "
            "prepared will with bal_delivery_set --keep true, or set a later "
            "date with bal_delivery_set --date YYYY-MM-DD."
        )
    return _(
        "The delivery date must be confirmed first, so nothing was built, "
        "signed or sent. The will prepared for {} is not signed and sent yet, "
        "and an older setting asked for {}. To keep {} (no fee): "
        "bal_delivery_set --keep true. To use a later date (if the old will "
        "was sent, it must be invalidated first, miner fee): bal_delivery_set "
        "--date YYYY-MM-DD. Details: bal_delivery_show."
    ).format(
        context.utc_day_text(keep),
        context.utc_day_text(later),
        context.utc_day_text(keep),
    )


def _as_bool(value, name):
    """A command-line yes/no value as a bool (None means no).

    Electrum's parser gives a bool for ``--keep true``; JSON-RPC callers may
    send a bool, 0/1 or a text.
    """
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("true", "1", "yes", "on"):
            return True
        if low in ("false", "0", "no", "off"):
            return False
    raise UserFacingException(_("Expected true or false for {}: {}").format(name, value))


def _months_words(n):
    """N calendar months in English words, for the command line (#127):
    "1 month", "6 months", "36 months (3 years)", "35 months (2 years and
    11 months)". The GUI uses the translated ``messages.months_text``; the
    command line stays English."""
    n = int(n)
    head = "1 month" if n == 1 else "{} months".format(n)
    years, rest = divmod(n, 12)
    if years <= 0:
        return head
    inner = "1 year" if years == 1 else "{} years".format(years)
    if rest:
        inner += " and " + ("1 month" if rest == 1 else "{} months".format(rest))
    return "{} ({})".format(head, inner)


def _form_locked_message(current, months=None):
    """Why ``bal_delivery_form`` refused: a will is in force (#127).

    ``current``: the form in force; ``months``: the period asked for, shown
    in the example (36 when none). The steps of owner decision 2: invalidate
    on-chain (miner fee), wait for the confirmation, then choose the form.
    """
    if current == delivery.FORM_PERIOD:
        return _(
            "This will uses a renewable period. The form cannot change while "
            "a will is in force: run bal_will_invalidate (on-chain fee), sign "
            "and broadcast it, wait until it is confirmed, then "
            "bal_delivery_form form=fixed date=YYYY-MM-DD, then "
            "bal_will_prepare."
        )
    return _(
        "This will uses a fixed delivery date. The form cannot change while a "
        "will is in force: run bal_will_invalidate (on-chain fee), sign and "
        "broadcast it, wait until it is confirmed, then bal_delivery_form "
        "form=period months={}, then bal_will_prepare."
    ).format(months if months is not None else 36)


# The highest fee rate bal_delivery_set accepts: the maximum of the GUI fee
# box (``bal/gui/qt/widgets.py``, a QSpinBox from 1 to 10000). Above it the
# GUI would show another rate than the one the will is built with, and a
# typo such as 2000 for 20 would make every rebuilt will pay that rate.
_MAX_FEE_RATE = 10000


def _as_fee(value):
    """The fee rate typed on the command line, as a number.

    An int or a float, or a text holding one; ``delivery.validate_values``
    then refuses anything below 1. A bool is not a fee rate, and a rate
    above :data:`_MAX_FEE_RATE` (the GUI fee box maximum) is refused.
    """
    if isinstance(value, bool):
        raise UserFacingException(_("Not a fee rate: {}").format(value))
    if isinstance(value, (int, float)):
        number = value
    else:
        text = str(value).strip()
        try:
            number = int(text)
        except ValueError:
            try:
                number = float(text)
            except ValueError:
                raise UserFacingException(
                    _("Not a fee rate: {}").format(value)
                ) from None
    # validate_values refuses NaN, the infinities and anything below 1.
    if number > _MAX_FEE_RATE:
        raise UserFacingException(
            _("The fee rate {} is too high: at most {} sat/vByte.").format(
                value, _MAX_FEE_RATE
            )
        )
    return number


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
        """Return ``{ATTRIBUTE: BalConfig}`` for every plugin setting.

        ``WILL_SETTINGS`` (``bal_will_settings``) is left out since #123:
        the delivery date and fee rate are kept by each wallet
        (``bal_delivery_show`` / ``bal_delivery_set``).
        """
        out = {}
        for attr in dir(self.plugin):
            if not attr.isupper() or attr in _MOVED_SETTINGS:
                continue
            value = getattr(self.plugin, attr, None)
            if isinstance(value, BalConfig):
                out[attr] = value
        return out

    def _config_key(self, key):
        """Find a setting by attribute name (``TX_FEES``) or config key
        (``bal_tx_fees``); raise a clear message for an unknown one, and a
        pointer to ``bal_delivery_show`` / ``bal_delivery_set`` for
        ``bal_will_settings`` (#123: never read or written here again)."""
        normalized = key.upper().replace("-", "_").replace(".", "_")
        if normalized in _MOVED_SETTINGS:
            raise UserFacingException(_WILL_SETTINGS_MOVED)
        configs = self._configs()
        aliases = {}
        for attr, cfg in configs.items():
            aliases[attr.upper().replace("-", "_").replace(".", "_")] = (attr, cfg)
            aliases[cfg.name.upper().replace("-", "_").replace(".", "_")] = (attr, cfg)
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
        self._check_warning_months(attr, coerced)
        cfg.set(coerced)
        return {
            "key": attr,
            "name": cfg.name,
            "default": cfg.default,
            "value": cfg.get(),
        }

    def _check_warning_months(self, attr, value):
        """Refuse a remaining-time threshold out of range (#124).

        ``WARN_YELLOW_MONTHS`` / ``WARN_RED_MONTHS`` take 1 to 120 calendar
        months, and red must stay below yellow (as the Settings boxes allow).
        Other settings are not checked here.
        """
        pair = {"WARN_YELLOW_MONTHS": "WARN_RED_MONTHS",
                "WARN_RED_MONTHS": "WARN_YELLOW_MONTHS"}
        if attr not in pair:
            return
        if not 1 <= value <= 120:
            raise UserFacingException(
                _("{}: expected 1 to 120 months, not {}").format(attr, value)
            )
        other = getattr(self.plugin, pair[attr]).get()
        yellow, red = (value, other) if attr == "WARN_YELLOW_MONTHS" else (other, value)
        try:
            ok = int(red) < int(yellow)
        except (TypeError, ValueError):
            ok = True
        if not ok:
            raise UserFacingException(
                _("The red warning ({}) must be below the yellow warning ({}) months.").format(red, yellow)
            )

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
        """Load the wallet's heirs, will and delivery settings, and the
        plugin's will-executors.

        The order matters since #123: the delivery settings are migrated
        from the will already loaded (its earliest VALID date and fee) and
        only for a wallet with heirs or a will. The commands build the
        controller under the wallet's flow lock (``commands._call_locked``),
        so the migration never runs twice at once.
        """
        super().__init__(plugin)
        self.wallet = wallet
        self.heirs = {}
        self.will = {}
        self.willitems = {}
        self.willexecutors = {}
        self.will_settings = {}
        self.settings_unavailable = False
        self.date_to_check = None
        self.no_willexecutor = False

        # The GUI wires ``plugin.get_decimal_point`` from the window; here the
        # daemon reads Electrum's global unit setting (falling back to 8 when
        # the config object does not expose it).
        plugin.get_decimal_point = self._get_decimal_point

        self._init_heirs()
        self._init_will()
        self._init_settings()
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
        """Load the wallet's own delivery settings (CHANGELOG #123).

        See :func:`bal.engine.context.load_will_settings`: ``will_settings``
        becomes the live dict of the wallet file, migrated once from the
        will in force (or, for a wallet with plugin data, the old shared
        value read raw; else today + 24 months, #127) and saved with the wallet.
        The date is stored once, so it no longer moves at a daemon restart
        (the #102 problem) and nothing is written to Electrum's global
        ``bal_will_settings`` any more. When the settings cannot be
        prepared, ``settings_unavailable`` is set and every flow stops.
        """
        context.load_will_settings(self, self.willitems)

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

    # ------------------------------------------------------------------ #
    # Engine host (bal.engine.host.WalletHost, CHANGELOG #117)
    # ------------------------------------------------------------------ #
    @property
    def bal_plugin(self):
        """The plugin, under the name the engine (and the GUI) use."""
        return self.plugin

    def store_will_setting(self, field, value):
        """Engine hook: the validated write of one delivery setting into the
        wallet's own dict (:func:`bal.engine.context.set_will_setting`),
        then the wallet file is saved. Raises ``ValueError`` for a value
        that is not accepted (nothing is written)."""
        context.set_will_setting(self, field, value)
        self.wallet.save_db()

    def flush_wallet_db(self):
        """Engine hook: the command line writes the wallet file at once."""
        self.wallet.save_db()

    def after_history_saved(self):
        """Engine hook: nothing to redraw on the command line."""

    # The state steps below live in bal.engine.context since CHANGELOG #117;
    # the names stay (the controller's flows and the tests call them).

    def load_willitems(self):
        """See :func:`bal.engine.context.load_willitems`."""
        context.load_willitems(self)

    def save_willitems(self):
        """See :func:`bal.engine.context.save_willitems`."""
        context.save_willitems(self)

    def _save_to_history(self):
        """See :func:`bal.engine.context.save_to_history`."""
        context.save_to_history(self)

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
        """See :func:`bal.engine.context.available_utxos`."""
        return context.available_utxos(self)

    # ------------------------------------------------------------------ #
    # Core flows (mirror of BalWindow)
    # ------------------------------------------------------------------ #
    def _refresh_invalidated_items(self):
        """See :func:`bal.engine.context.refresh_invalidated_items` (#79)."""
        context.refresh_invalidated_items(self)

    def check_alive_info(self):
        """See :func:`bal.engine.context.check_alive_info`."""
        return context.check_alive_info(self)

    def _normalize_check_alive(self):
        """See :func:`bal.engine.context.normalize_check_alive` (#80)."""
        context.normalize_check_alive(self)

    def init_class_variables(self):
        """See :func:`bal.engine.context.init_class_variables`."""
        context.init_class_variables(self)

    def _delivery_hold(self):
        """The hold of the delivery settings that applies now, or None.

        See :func:`bal.engine.context.check_holds` (#123, rule I6): the
        "which date?" question is pending, or the settings could not be
        prepared.
        """
        try:
            context.check_holds(self)
        except DeliveryHold as e:
            return e
        return None

    def _hold_result(self, e):
        """The JSON answer of a flow stopped by a delivery hold: ``result``
        is the hold's code (``delivery_choice_pending`` or
        ``delivery_settings_unavailable``) and ``message`` says what to do."""
        return {
            "result": e.code,
            "message": _delivery_hold_message(e, self.will_settings),
        }

    def _refuse_when_held(self):
        """Rule I6 for the commands that do not pass through
        ``init_class_variables`` (sign, send, invalidate): a delivery hold
        stops them with its message."""
        hold = self._delivery_hold()
        if hold is not None:
            raise UserFacingException(
                _delivery_hold_message(hold, self.will_settings)
            ) from None

    def _init_heirs_to_locktime(self, multiverse=False):
        """See :func:`bal.engine.context.init_heirs_to_locktime`."""
        context.init_heirs_to_locktime(self, multiverse)

    def check_will(self):
        """See :func:`bal.engine.context.check_will`."""
        return context.check_will(self)

    def build_will(self, ignore_duplicate=True, keep_original=True):
        """Build (or rebuild) the inheritance transactions.

        See :func:`bal.engine.build.build_will` (CHANGELOG #119): raises
        ``NoWillExecutorNotPresent`` when no valid will-executor is selected
        and the user is not their own executor. The command line does not
        pass the old will's creation date when it re-anchors
        ``date_to_check`` (``with_creation_date=False``), as before.
        ``ignore_duplicate`` / ``keep_original`` were never used.
        """
        return build.build_will(self, with_creation_date=False)

    def _after_rebuild(self):
        """Label the rebuilt transactions and keep the delivery date in step.

        The VALID items get the label; one that cannot be written is only
        logged (CHANGELOG #119: bal.engine.build.label_rebuilt). Then
        :meth:`_sync_locktime_to_built_txs` (CHANGELOG #107).
        """
        build.label_rebuilt(
            self,
            scope="valid",
            on_error=lambda e: _logger.debug(f"set_label after rebuild failed: {e}"),
        )
        self._sync_locktime_to_built_txs()

    def _sync_locktime_to_built_txs(self):
        """Move the stored delivery date to the rebuilt transactions' date.

        Mirror of the GUI's ``BalBuildWillDialog._sync_locktime_to_built_txs``
        (see its docstring for the full why). A rebuild that spends the coins
        of the will already sent (e.g. after new funds) anticipates the
        locktime by one day, so the new transactions are valid first. The
        stored date stayed one day later, and the next check took that for a
        postponement and asked for an on-chain invalidation (CHANGELOG #107).
        The date only ever moves EARLIER, and only when it is an absolute
        date: a relative one ("1y") is resolved against the built
        transactions on every check, and a later date chosen by the user is
        never overwritten.
        """
        # The rule lives in bal.core (CHANGELOG #110) and the steps in
        # bal.engine.build (CHANGELOG #119), shared with the GUI.

        def store(field, value):
            _logger.info(f"delivery date synced to the rebuilt will: -> {value}")
            self.store_will_setting(field, value)

        build.sync_delivery_date(self, store)

    def invalidate_will(self, will=None):
        """Build the on-chain invalidation transaction (real fee).

        Returns the raw ``PartialTransaction`` (or ``None`` when there is
        nothing to invalidate); the caller decides how to show/sign it.
        Refused while a delivery hold applies (#123, rule I6).
        """
        self._refuse_when_held()
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
        While a delivery hold applies (#123) nothing is checked: the answer
        is ``{"valid": false, "result": <code>, "message": ...}`` with the
        code ``delivery_choice_pending`` or ``delivery_settings_unavailable``
        and the command that answers it. A passed Check Alive raises the
        text of ``bal_will_autorebuild`` (see ``_check_alive_error``).
        """
        try:
            self.init_class_variables()
            self.check_will()
        except DeliveryHold as e:
            return dict(self._hold_result(e), valid=False)
        except CheckAliveError as e:
            raise self._check_alive_error() from e
        except Exception as e:
            raise _user_facing(e) from e
        return {"valid": True}

    def _check_alive_error(self):
        """The message of a passed Check Alive for ``bal_will_check`` and
        ``bal_will_prepare``: the same text as ``bal_will_autorebuild``.

        Built from this wallet's ``check_alive_info``: a fixed delivery date
        gets "choose a later delivery date" (renewal by invalidation cannot
        move a fixed date, since #123 every wallet has one), a renewable
        will gets the renewal steps. Before, both commands always gave the
        renewal steps (D42).
        """
        return UserFacingException(_check_alive_cli_message(self.check_alive_info()))

    def prepare_will(self, ignore_duplicate=True, keep_original=True):
        """Run the full "prepare inheritance" flow and return a status dict.

        Mirrors ``BalWindow.build_inheritance_transaction``:

        * ``coherent`` -> the existing will is still valid; nothing rebuilt.
          Since CHANGELOG #128 also when the rebuild only dropped unsigned
          drafts and gave back the will in force (signed and sent: nothing
          to sign or send).
        * ``rebuilt``  -> the will was not coherent and has been rebuilt;
          ``reason`` says why, in the words of the Check dialog.
        * ``expired`` / ``postponed`` -> an invalidation transaction must be
          signed and broadcast before a new will can be prepared.
        * ``delivery_choice_pending`` / ``delivery_settings_unavailable``
          -> a delivery hold (#123): nothing was done; ``message`` says
          which command answers it.

        A passed Check Alive raises the text of ``bal_will_autorebuild``
        (see ``_check_alive_error``).
        """
        hold = self._delivery_hold()
        if hold is not None:
            return self._hold_result(hold)
        if not self.heirs:
            raise UserFacingException(_("Heirs are not defined: add at least one heir first"))

        try:
            self.init_class_variables()
        except CheckAliveError as e:
            raise self._check_alive_error() from e
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
            # CHANGELOG #128: which items were REPLACED before the build, to
            # tell when the build only gave back the will in force.
            replaced_before = Will.replaced_ids(self.willitems)
            if not self.build_will(ignore_duplicate, keep_original):
                raise UserFacingException(
                    _(
                        "The will was not built ({}): check the heirs, the "
                        "amounts, the delivery date and the will-executors."
                    ).format(getattr(self.heirs, "last_build_error", None) or "?")
                ) from None
            rebuilt_ok = False
            try:
                self.check_will()
                rebuilt_ok = True
            except will_mod.NotCompleteWillException as e2:
                # CHANGELOG #107, mirror of the Check dialog (bugs E/F/K): right
                # after a rebuild the check reports that the new will differs
                # from the old one (e.g. the heir's date, anticipated by one
                # day after new funds). That only means the new transactions
                # must be signed. Before, the command line stopped here with
                # "Error: <heir> Please, check your heirs..." and the will
                # could not be prepared again after receiving funds.
                _logger.info(f"rebuilt will needs signing ({type(e2).__name__})")
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
            self.save_willitems()
            if rebuilt_ok:
                self._after_rebuild()
                self._save_to_history()
                if Will.only_brought_back(replaced_before, self.willitems):
                    # CHANGELOG #128: the drafts were dropped and the will in
                    # force, signed and sent, came back: nothing to sign or
                    # send, so the answer is the one of a coherent will, not
                    # "rebuilt" with the reason of the unsigned draft.
                    return {
                        "result": "coherent",
                        "message": _("The will is coherent"),
                        "will": self._will_status_dict(),
                    }
                # CHANGELOG #122: the rebuilt transactions can be the ones
                # already signed and sent (update_will keeps them).
                if Will.is_new(self.willitems):
                    message = _(
                        "The will was rebuilt and needs to be signed and "
                        "broadcast again"
                    )
                elif Willexecutors.get_willexecutor_transactions(
                    self.willitems, current_willexecutors=self.willexecutors
                ):
                    message = _(
                        "The will was rebuilt with the same signed "
                        "transactions: send it with bal_will_broadcast"
                    )
                else:
                    message = _("The will is coherent")
                return {
                    "result": "rebuilt",
                    "message": message,
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

        * ``valid``         -> the will is still coherent, signed and with its
          will-executors; nothing was done.
        * ``signed`` / ``sent`` / ``not_sent`` -> the will was coherent but not
          finished: it was signed (passwordless wallets) and/or sent to the
          will-executors that did not have it yet (CHANGELOG #109, see
          :meth:`_finish_coherent_will`). ``needs_signing`` here means a
          coherent will that an encrypted wallet must sign.
        * ``no_heirs``      -> no valid heirs are configured.
        * ``delivery_choice_pending`` / ``delivery_settings_unavailable``
          -> a delivery hold (#123): nothing was built, signed or sent;
          ``message`` says which command answers it.
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
        * ``rebuilt_not_sent`` -> the will was rebuilt and signed, but no
          will-executor received it (``push["_failed"]`` says why, e.g.
          offline): run ``bal_will_broadcast`` later (CHANGELOG #105; before,
          this case also answered ``rebuilt``, "signed and pushed").
        """
        try:
            self.init_class_variables()
        except DeliveryHold as e:
            _logger.info(f"auto rebuild: delivery hold ({e.code}), nothing to do")
            return self._hold_result(e)
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
            return self._finish_coherent_will()
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

        coherent = False
        try:
            self.check_will()
            coherent = True
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
        self._after_rebuild()

        if coherent and not Will.is_new(self.willitems):
            # CHANGELOG #122: the rebuilt will is coherent and its transactions
            # are the ones already signed (same txids: update_will kept them).
            # Nothing to sign, also on an encrypted wallet: save, then
            # send what a will-executor does not have yet (#109).
            self.save_willitems()
            self._save_to_history()
            return self._finish_coherent_will()

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
        failed = push.get("_failed") or {}
        sent = [url for url in push if not url.startswith("_") and url not in failed]
        if push.get("_error") and not sent:
            return {
                "result": "rebuilt_not_sent",
                "message": _(
                    "The will was rebuilt and signed, but no will-executor "
                    "received it: run bal_will_broadcast when they can be reached"
                ),
                "push": push,
                "will": self._will_status_dict(),
            }
        return {
            "result": "rebuilt",
            "message": _(
                "The will was rebuilt, signed and pushed to its will-executors"
            ),
            "push": push,
            "will": self._will_status_dict(),
        }

    def _finish_coherent_will(self):
        """Sign and send what a coherent will still lacks (CHANGELOG #109).

        ``bal_will_autorebuild`` is the command meant to run unattended (e.g.
        once a day), so it must leave the will really in force. Before, a
        coherent will answered ``valid`` even when it was never signed, or
        signed but never received by a will-executor (sending failed, e.g.
        offline), and nothing was retried. Sending means giving the selected
        will-executors the same signed transactions, as after a rebuild: no
        coin moves.

        Returns ``valid`` (nothing missing), ``needs_signing`` (encrypted
        wallet), ``signed`` (signed, nothing to send), ``sent`` (at least one
        will-executor received it; ``push`` has the details) or ``not_sent``
        (none did; ``push["_failed"]`` says why).
        """
        unsigned = [
            wid for wid in Will.only_valid(self.willitems)
            if not self.willitems[wid].get_status("COMPLETE")
        ]
        signed = 0
        if unsigned:
            if self.wallet.has_keystore_encryption():
                return {
                    "result": "needs_signing",
                    "message": _(
                        "The will is not signed yet and this wallet is "
                        "encrypted: sign it with bal_will_sign and push it with "
                        "bal_will_broadcast"
                    ),
                    "will": self._will_status_dict(),
                }
            try:
                signed = len(self.sign_transactions(None, unsigned))
            except Exception as e:
                raise _user_facing(e) from e
        pending = Willexecutors.get_willexecutor_transactions(
            self.willitems, current_willexecutors=self.willexecutors
        )
        if not pending:
            if signed:
                return {"result": "signed", "signed": signed,
                        "will": self._will_status_dict()}
            return {"result": "valid", "will": self._will_status_dict()}
        try:
            push = self.push_transactions_to_willexecutors()
        except Exception as e:
            _logger.error(f"auto rebuild: push failed: {e}")
            push = {"_error": True, "_message": str(e)}
        failed = push.get("_failed") or {}
        sent = [url for url in push if not url.startswith("_") and url not in failed]
        if push.get("_error") and not sent:
            return {
                "result": "not_sent",
                "message": _(
                    "The will is valid, but no will-executor received it: run "
                    "bal_will_broadcast when they can be reached"
                ),
                "signed": signed,
                "push": push,
                "will": self._will_status_dict(),
            }
        return {
            "result": "sent",
            "message": _("The will was sent to the will-executors that did not have it"),
            "signed": signed,
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
        given, and while a delivery hold applies (#123, rule I6: signing the
        prepared will would answer the "which date?" question by itself).
        """
        self._refuse_when_held()
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
        when no transaction matched the (optional) filter. When a push fails,
        ``_error`` is true and ``_failed`` maps each failed URL to the reason
        (CHANGELOG #105: before, only "Failed" was returned, so a script could
        not tell "offline" from "refused by the server"). Refused while a
        delivery hold applies (#123, rule I6).
        """
        self._refuse_when_held()
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
        failed = {}

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
                failed[url] = (
                    willexecutor.get("broadcast_error")
                    or (str(exc) if exc else "")
                    or _("unknown error")
                )
            willexecutor.pop("txs", None)

        Willexecutors.push_transactions_parallel(willexecutors, on_each=on_each)
        # A server still busy at the global deadline never reached on_each.
        answered = set(failed) | set(already_present) | {
            url
            for url, we in willexecutors.items()
            if we.get("broadcast_status") == _("Success")
        }
        for url in willexecutors:
            if url not in answered:
                error["flag"] = True
                willexecutors[url]["broadcast_status"] = _("Failed")
                failed[url] = _("no answer in time")

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
            out["_failed"] = failed
        return out

    def broadcast_command(self, force=False, txids=None):
        """``bal_will_broadcast``: push, and fail when nothing was sent.

        CHANGELOG #105: when every push failed the command used to end with
        exit code 0 and ``"Failed"`` only. Now, if no will-executor received
        the will, it ends with an error that names each will-executor and its
        reason; when at least one received it, the JSON result is returned
        (``_failed`` lists the others).
        """
        out = self.push_transactions_to_willexecutors(force, txids)
        failed = out.get("_failed") or {}
        sent = [url for url in out if not url.startswith("_") and url not in failed]
        if failed and not sent:
            lines = [_("The will was not sent to any will-executor:")]
            lines += ["- {}: {}".format(url, reason) for url, reason in failed.items()]
            raise UserFacingException("\n".join(lines))
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
        # Also records the anchor of the will (#123, plan 3.2): the direct
        # item writes above are covered by this save.
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
    # Delivery settings of this wallet (CHANGELOG #123)
    # ------------------------------------------------------------------ #
    def _will_in_force(self):
        """The date of the will in force, or None.

        The earliest date of the VALID will items; with no VALID item, the
        earliest date of the items still pending at the will-executors
        (sent, not invalidated or confirmed, in the future: the source
        "sent" of the migration). See
        :func:`bal.core.delivery.earliest_in_force`. Block heights are
        ignored.
        """
        return delivery.earliest_in_force(
            delivery.views_from(self.willitems), time.time()
        )

    @staticmethod
    def _dated(ts):
        """``{"locktime": ts, "date": "YYYY-MM-DD"}`` (UTC; the time is added
        when it is not 00:00), or None when ``ts`` is not a date."""
        ts = delivery.as_delivery(ts)
        if ts is None:
            return None
        return {"locktime": ts, "date": context.utc_day_text(ts)}

    def delivery_show(self):
        """``bal_delivery_show``: the delivery settings of this wallet.

        Read only. ``date`` / ``locktime``: the delivery date (UTC) and its
        UNIX time; ``baltx_fees``: the fee rate; ``threshold``: the Check
        Alive bridge; ``settings_version``, ``migrated_from``,
        ``migrated_at`` and ``legacy_locktime`` (the old value found at the
        migration, e.g. "1054d"); ``note``: the one-time note not yet shown
        in the GUI, with its dates; ``choice_pending`` and ``choice`` (the
        two dates of the "which date?" question); ``will_in_force``: the
        date of the will in force (see ``_will_in_force``: the VALID items,
        else the items still pending at the will-executors);
        ``settings_unavailable``:
        the settings could not be prepared (the values are for display
        only). Since #127 the delivery form: ``form`` ("fixed" or
        "period"), ``period_months`` and ``period`` (N in words) and
        ``period_start`` (the day the period started) for the period form,
        and ``locked`` (a will is in force: the form cannot change, see
        ``bal_delivery_form``).
        """
        ws = self.will_settings if isinstance(self.will_settings, Mapping) else {}
        locktime = delivery.as_delivery(ws.get("locktime"))
        out = {
            "date": context.utc_day_text(locktime) if locktime is not None else None,
            "locktime": locktime,
            "baltx_fees": ws.get("baltx_fees"),
            "threshold": ws.get("threshold"),
            "settings_version": ws.get("settings_version"),
            "migrated_from": ws.get("migrated_from"),
            "migrated_at": ws.get("migrated_at"),
            "legacy_locktime": ws.get("legacy_locktime"),
            "note": None,
            "choice_pending": bool(ws.get(delivery.CHOICE_KEY)),
            "will_in_force": self._dated(self._will_in_force()),
            "settings_unavailable": bool(self.settings_unavailable),
            "form": delivery.form_of(ws),
            "period_months": None,
            "period": None,
            "period_start": None,
            "locked": bool(context.form_locked(self)),
        }
        months = delivery.period_months(ws)
        if months is not None:
            out["period_months"] = months
            out["period"] = _months_words(months)
            out["period_start"] = self._dated(ws.get("period_start"))
        note = delivery.pending_note(ws)
        if note is not None:
            kind, old, new = note
            out["note"] = {"kind": kind, "old": self._dated(old), "new": self._dated(new)}
        dates = delivery.choice_dates(ws)
        if dates is not None:
            keep, later = dates
            out["choice"] = {"keep": self._dated(keep), "later": self._dated(later)}
        if self.settings_unavailable:
            out["message"] = _delivery_hold_message(
                delivery.DeliverySettingsUnavailable()
            )
        return out

    def delivery_set(self, date=None, fee=None, keep=None, confirm_earlier=None,
                     months=None):
        """``bal_delivery_set``: change this wallet's delivery date or fee rate.

        ``date``: ``YYYY-MM-DD`` (00:00 UTC) or a UNIX timestamp, read by
        :func:`bal.core.delivery.parse_cli_date` (a period such as "1y", a
        block height or a date not in the future is refused). A date
        EARLIER than the will in force (or, with no will in force, than the
        stored date) needs ``confirm_earlier`` (the heirs could receive
        earlier; a typo in a script must not do it). A later date is
        stored; if the will was already sent, the next ``bal_will_prepare``
        asks to invalidate it first (miner fee), as the GUI Check does.
        ``fee``: a fee rate from 1 to 10000 (the GUI fee box). ``keep``:
        answer the "which date?" question by keeping the date of the
        prepared will; a new ``date`` answers it too. ``keep`` and ``date`` together are refused.
        ``months`` (#127, the period form only): the new period N, 1..240
        calendar months; with no will in force the delivery date becomes
        today + N months, with a will in force N applies at the next
        renewal (no fee now). The period form has no date: ``date`` is
        refused there (see ``bal_delivery_form``).

        Everything is checked before anything is written (through
        :func:`bal.engine.context.set_will_setting`), then the wallet file
        is saved. Refused while the settings could not be prepared. Returns
        :meth:`delivery_show` with a ``message``.
        """
        keep = _as_bool(keep, "keep")
        confirm_earlier = _as_bool(confirm_earlier, "confirm_earlier")
        if self.settings_unavailable:
            raise UserFacingException(
                _delivery_hold_message(delivery.DeliverySettingsUnavailable())
            )
        if date is None and fee is None and not keep and months is None:
            raise UserFacingException(
                _(
                    "Nothing to change: give --date YYYY-MM-DD, --fee N, "
                    "--months N or --keep true."
                )
            )
        if keep and date is not None:
            raise UserFacingException(_("Give either --keep true or --date, not both."))
        period_form = delivery.is_period(self.will_settings)
        if months is not None and not period_form:
            raise UserFacingException(
                _(
                    "This wallet uses a fixed delivery date: --months is only "
                    "for the period form (see bal_delivery_form)."
                )
            )
        if date is not None and period_form:
            raise UserFacingException(
                _(
                    "This wallet uses a renewable period: change it with "
                    "--months N, or choose a fixed date with bal_delivery_form "
                    "form=fixed date=YYYY-MM-DD."
                )
            )
        if months is not None and delivery.as_months(months) is None:
            raise UserFacingException(
                _("Not a period: {} (whole months from {} to {}).").format(
                    months, delivery.PERIOD_MIN, delivery.PERIOD_MAX
                )
            )
        changes = {}
        in_force = self._will_in_force()
        # The reference of --confirm_earlier reads the VALID items only (with
        # none, the stored date, which equals the earliest sent one).
        valid_in_force = delivery.earliest_valid(delivery.views_from(self.willitems))
        if date is not None:
            try:
                new_date = delivery.parse_cli_date(date, time.time())
            except ValueError as e:
                raise UserFacingException(str(e)) from None
            stored = delivery.as_delivery(self.will_settings.get("locktime"))
            reference = valid_in_force if valid_in_force is not None else stored
            if reference is not None and new_date < reference and not confirm_earlier:
                if valid_in_force is not None:
                    text = _(
                        "{} is earlier than the will in force ({}): the heirs "
                        "could receive the bitcoin earlier. If this is what you "
                        "want, run the command again with --confirm_earlier true."
                    )
                else:
                    text = _(
                        "{} is earlier than the current delivery date ({}). If "
                        "this is what you want, run the command again with "
                        "--confirm_earlier true."
                    )
                raise UserFacingException(
                    text.format(
                        context.utc_day_text(new_date), context.utc_day_text(reference)
                    )
                )
            changes["locktime"] = new_date
        if fee is not None:
            changes["baltx_fees"] = _as_fee(fee)
        try:
            delivery.validate_values(changes)
        except ValueError as e:
            raise UserFacingException(str(e)) from None

        messages = []
        if keep:
            if delivery.choice_dates(self.will_settings) is None:
                messages.append(_("No question was pending: nothing to keep."))
            else:
                kept = context.answer_delivery_choice(self, use_later=False)
                messages.append(
                    _("The date of the prepared will is kept: {}.").format(
                        context.utc_day_text(kept)
                    )
                )
        for field, value in changes.items():
            context.set_will_setting(self, field, value)
        if months is not None:
            result = context.set_delivery(self, months=months, now=time.time())
            if result["applied"]:
                messages.append(
                    _("Period of {}: the delivery date is {}.").format(
                        _months_words(result["period_months"]),
                        context.utc_day_text(result["locktime"]),
                    )
                )
            else:
                messages.append(
                    _(
                        "Period of {} stored: a will is in force, so it "
                        "applies at the next renewal (no fee now)."
                    ).format(_months_words(result["period_months"]))
                )
        if changes:
            messages.append(_("Stored."))
            new_date = changes.get("locktime")
            if new_date is not None and in_force is not None and new_date > in_force:
                messages.append(
                    _(
                        "The new date is later than the will in force ({}). If "
                        "that will was already sent to the will-executors, the "
                        "next bal_will_prepare asks to invalidate it first "
                        "(on-chain, miner fee)."
                    ).format(context.utc_day_text(in_force))
                )
            messages.append(
                _("Run bal_will_prepare to prepare the will with the new settings.")
            )
        self.wallet.save_db()
        out = self.delivery_show()
        out["message"] = " ".join(messages)
        return out

    def delivery_form(self, form=None, date=None, months=None):
        """``bal_delivery_form``: choose the delivery form of this wallet (#127).

        ``form``: ``fixed`` (``date`` optional: YYYY-MM-DD at 00:00 UTC or a
        UNIX timestamp; without it the stored date stays) or ``period``
        (``months``: N calendar months, 1..240; with no will in force the
        delivery date becomes today + N months, renewed by the Check Alive).
        Refused while a will is in force (owner decision 2: invalidate it
        first, on-chain, and wait for the confirmation) with the steps to
        follow, and while the settings could not be prepared. Everything is
        checked before anything is written (:func:`bal.engine.context.
        set_delivery`), then the wallet file is saved. Returns
        :meth:`delivery_show` with a ``message``.
        """
        if self.settings_unavailable:
            raise UserFacingException(
                _delivery_hold_message(delivery.DeliverySettingsUnavailable())
            )
        form = None if form is None else str(form).strip().lower()
        if form not in delivery.FORMS:
            raise UserFacingException(
                _("Give form=fixed or form=period (now: {}).").format(form)
            )
        if form == delivery.FORM_FIXED and months is not None:
            raise UserFacingException(_("months is only for form=period."))
        if form == delivery.FORM_PERIOD:
            if date is not None:
                raise UserFacingException(
                    _("The period form has no fixed date: give months=N.")
                )
            if months is None and delivery.period_months(self.will_settings) is None:
                raise UserFacingException(_("Give months=N (1 to 240)."))
            if months is not None and delivery.as_months(months) is None:
                raise UserFacingException(
                    _("Not a period: {} (whole months from {} to {}).").format(
                        months, delivery.PERIOD_MIN, delivery.PERIOD_MAX
                    )
                )
        new_date = None
        if date is not None:
            try:
                new_date = delivery.parse_cli_date(date, time.time())
            except ValueError as e:
                raise UserFacingException(str(e)) from None
        try:
            result = context.set_delivery(
                self, form=form, date=new_date, months=months, now=time.time()
            )
        except delivery.DeliveryFormLocked as e:
            raise UserFacingException(
                _form_locked_message(e.current_form, months)
            ) from None
        except ValueError as e:
            raise UserFacingException(str(e)) from None
        self.wallet.save_db()
        out = self.delivery_show()
        if result["form"] == delivery.FORM_PERIOD:
            if result["applied"]:
                text = _("Period of {}: the delivery date is {}.").format(
                    _months_words(result["period_months"]),
                    context.utc_day_text(result["locktime"]),
                )
            else:
                text = _(
                    "Period of {} stored: a will is in force, so it applies "
                    "at the next renewal (no fee now)."
                ).format(_months_words(result["period_months"]))
        else:
            text = _("Fixed delivery date: {}.").format(
                context.utc_day_text(result["locktime"])
            )
        out["message"] = text + " " + _(
            "Run bal_will_prepare to prepare the will with the new settings."
        )
        return out

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
                    "heir, use bal_delivery_set --date YYYY-MM-DD (see the "
                    "current date with bal_delivery_show)."
                )
            )
        self._check_locktime_value(locktime)

    def _check_locktime_value(self, locktime):
        """Refuse a delivery date that is not understood or already past."""
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
        self._check_address_free(address, name)
        value = self._build_heir_entry(name, address, amount, locktime)
        self.heirs[name] = value
        self.wallet.save_db()
        return {"name": name, "value": list(value)}

    def _check_address_free(self, address, name):
        """Refuse an address already used by ANOTHER heir (#135, owner: no
        two heirs on one address); ``name``'s own address is allowed."""
        other = heirs_mod.heir_with_address(self.heirs, address, except_name=name)
        if other is not None:
            raise UserFacingException(
                _("This address is already used by the heir {}.").format(other))

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
        self._check_address_free(address, name)
        value = self._build_heir_entry(name, address, amount, locktime)
        self.heirs[name] = value
        self.wallet.save_db()
        return {"name": name, "value": list(value)}

    def heirs_delete(self, names):
        """Delete heirs by name; ``not_found`` lists the unknown names.

        A single name is accepted too (CHANGELOG #105): Electrum passes a
        plain name as text, and the loop then ran over its letters.
        """
        deleted, not_found = [], []
        for name in _as_name_list(names):
            if name in self.heirs:
                self.heirs.pop(name)
                deleted.append(name)
            else:
                not_found.append(name)
        self.heirs.save()
        self.wallet.save_db()
        return {"deleted": deleted, "not_found": not_found}

    def heirs_list(self):
        return {k: list(v) for k, v in self.heirs.items()}

    def heirs_show(self, name):
        if name not in self.heirs:
            raise UserFacingException(_("Heir not found: {}").format(name))
        return {"name": name, "value": list(self.heirs[name])}

    def heirs_import(self, path):
        """Import heirs from a JSON file and say what was skipped and why.

        CHANGELOG #105: ``imported`` used to be the total number of heirs,
        and invalid entries were dropped silently (only an INFO log line).
        Now ``imported`` counts the heirs taken from the file, ``names`` lists
        them, ``skipped`` maps each refused entry to the reason and ``total``
        is the number of heirs afterwards. With multiverse on, a delivery date
        that is not understood or already past is refused too; with it off,
        every heir gets the will's date anyway.
        """
        data = read_json_file(path)
        if isinstance(data, dict) and isinstance(data.get("heirs"), dict):
            data = data["heirs"]  # the nesting Heirs._validate accepts
        if not isinstance(data, dict):
            raise UserFacingException(_("Invalid heirs file: {}").format(path))
        multiverse = self.plugin.ENABLE_MULTIVERSE.get()
        valid, skipped = {}, {}
        for name, value in data.items():
            try:
                valid[name] = Heirs.validate_heir(name, value)
                if multiverse:
                    self._check_locktime_value(value[2])
            except Exception as e:
                valid.pop(name, None)
                skipped[name] = str(_user_facing(e))
        self.heirs.update(valid)
        self.heirs.save()
        self.wallet.save_db()
        return {
            "imported": len(valid),
            "names": list(valid),
            "skipped": skipped,
            "total": len(self.heirs),
            # #135: heirs on one address after the import (a warning: the
            # file is imported as it is; keep only one of each pair).
            "same_address": [list(p) for p in heirs_mod.shared_addresses(self.heirs)],
        }

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
        if rename_to and rename_to != url and Willexecutors.is_protected(self.plugin, url):
            # Renaming is deleting the old URL (CHANGELOG #108); checked
            # before any field changes.
            raise UserFacingException(protected_executor_message(url))
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
        """Delete will-executors by URL; ``not_found`` lists the unknown ones.

        A single URL is accepted too (CHANGELOG #105), as in ``heirs_delete``.
        The Bitcoin After Life will-executor cannot be deleted (CHANGELOG
        #108): when it is in the list, nothing is deleted and the message
        says to deselect it.
        """
        urls = _as_name_list(urls)
        for url in urls:
            if Willexecutors.is_protected(self.plugin, url):
                raise UserFacingException(protected_executor_message(url))
        deleted, not_found = [], []
        for url in urls:
            if url in self.willexecutors:
                del self.willexecutors[url]
                deleted.append(url)
            else:
                not_found.append(url)
        Willexecutors.save(self.plugin, self.willexecutors)
        return {"deleted": deleted, "not_found": not_found}

    def willexecutors_select(self, urls=None, select=True):
        """Select/deselect one, many or all will-executors."""
        selected = {}
        targets = urls if urls is not None else list(self.willexecutors)
        for url in targets:
            if url not in self.willexecutors:
                continue
            self.willexecutors[url]["selected"] = bool(select)
            selected[url] = bool(select)
        # #137: the user's choice (the wizards' automatic selection keeps a
        # will-executor he deselected deselected).
        Willexecutors.set_user_choice(self.plugin, list(selected), bool(select))
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
        """Add the will-executors of a JSON file that are not in the list yet.

        Will-executors already in the list are left as they are. CHANGELOG
        #105: ``imported`` counted every entry of the file, those already
        present too; now it counts the added ones, and ``already_present``
        lists the others.
        """
        data = read_json_file(path)
        if not isinstance(data, dict):
            raise UserFacingException(_("Invalid will-executors file"))
        added, already_present = [], []
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
                added.append(url)
            else:
                already_present.append(url)
        Willexecutors.save(self.plugin, self.willexecutors)
        return {
            "imported": len(added),
            "already_present": already_present,
            "total": len(self.willexecutors),
        }

    def willexecutors_export(self, path):
        write_json_file(path, self.willexecutors)
        return {"exported": len(self.willexecutors), "path": path}


def get_decimal_point(plugin):
    """Standalone helper returning Electrum's configured BTC decimal point."""
    try:
        return plugin.config.BTC_AMOUNTS_DECIMAL_POINT
    except AttributeError:
        return 8
