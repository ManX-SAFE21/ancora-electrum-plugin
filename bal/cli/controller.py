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
from decimal import Decimal, InvalidOperation

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

from ..core import checkalive, delivery, heir_notes
from ..core import heirs as heirs_mod
from ..core import will as will_mod
from ..core.checkalive import (
    CheckAliveAfterDeliveryError,
    CheckAliveError,
    resolve_guard_threshold,
)
from ..core.delivery import DeliveryHold
from ..core.heirs import Heirs, is_op_return_address
from ..core.messages import (
    chain_refused_message,
    check_alive_message,
    check_alive_passed_sentence,
    heir_error_message,
    low_fee_warning,
    protected_executor_message,
    staged_removed_message,
    will_change_message,
)
from ..core.plugin_base import BalConfig, BalPlugin
from ..core.summary import selected_urls
from ..core.util import Util
from ..core.will import Will, WillItem
from ..core.willexecutors import Willexecutors, is_onion_url, is_tor_active
from ..engine import build, context, instalments
from ..engine.invalidate import INVALIDATION_LABEL, build_invalidation
from ..engine.merge import merge_summary
from ..engine.merge import merge_will as engine_merge_will
from ..engine.push import (
    EXCLUDED_FEE,
    FAILED,
    RECEIVED,
    TIMEOUT,
    push_will,
    select_push,
)
from ..engine.sign import sign_will, to_sign
from ..engine.verify import verify_targets, verify_will

_logger = get_logger(__name__)

# The names of the will-executor fee rows inside a will item start with this
# reserved marker (``bal.core.heirs``): they are not heirs.
_EXECUTOR_HEIR = 'w!ll3x3c"'
# The fields of one delivery that ``bal_instalments_set`` reads (#169):
# ``delivery`` and ``locktime`` too, so the answer of
# ``bal_instalments_show`` can be given back as it is.
_TABLE_FIELDS = frozenset(("delivery", "date", "locktime", "shares"))
# The whole estate, in the shares of the table of the deliveries.
_HUNDRED = Decimal(100)


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
    if isinstance(e, delivery.InstalmentsMismatch):
        # #168: the table of the deliveries no longer fits the heirs; the
        # message (translated) says to update it. #169: and with which
        # commands (its last sentence, the rule broken, has no full stop).
        text = str(e).rstrip()
        if not text.endswith("."):
            text += "."
        return UserFacingException(
            text + " " + _(
                "See bal_instalments_show, then update the table with "
                "bal_instalments_set (or remove it with bal_instalments_clear)."
            )
        )
    if isinstance(e, will_mod.NoHeirsException):
        return UserFacingException(_("There are no valid heirs"))
    if isinstance(e, will_mod.WillExpiredException):
        return UserFacingException(
            _("The will is expired and must be invalidated on-chain and rebuilt")
        )
    if isinstance(e, will_mod.StagedWillRemovedException):
        # #188: back to one delivery: the same road as a postpone.
        return UserFacingException(
            staged_removed_message(e.deliveries) + " "
            + _("Run bal_will_invalidate, sign and broadcast the transaction, "
                "then bal_will_prepare."))
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
                      heirs_mod.LocktimeNotValid, heirs_mod.HeirNameNotValid,
                      heirs_mod.SecondMessageNotAllowed)):
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


def _confirm_earlier_hint():
    """The end of every "the heirs would receive earlier" refusal of the
    table commands (#169). In practice the command line is used by an AI
    (owner, 2026-10-19): the text tells it to ask the owner before it
    confirms."""
    return _(
        "Ask the owner; if this is what the owner wants, run the command "
        "again with --confirm_earlier true."
    )


def _delivery_number(value):
    """The number of a delivery as typed in the table (#169): an int, or a
    whole number as a number or a text ("1", 1.0: Electrum's JSON gives
    decimals as text). None otherwise (a bool is not a number)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite() or number != number.to_integral_value():
        return None
    return int(number)


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
    def _willitem_summary(self, wi, staged=False):
        """One will item for the commands (JSON-safe).

        #169: ``heirs`` and ``description`` name the people ("Luca", never
        the row name "Luca§2" of a later delivery, owner decision 2 of M3;
        the stored item keeps its row names); with a table
        of the deliveries (``staged``) ``delivery`` is the item's delivery
        number. A will with one date gives the same answer as before.
        """
        names = list((wi.heirs or {}).keys()) if wi.heirs is not None else []
        out = {
            "txid": wi._id,
            "tx": str(wi.tx) if wi.tx is not None else None,
            "locktime": int(wi.tx.locktime) if wi.tx is not None else None,
            "heirs": [delivery.person_name(name) for name in names],
            "tx_fees": int(wi.tx_fees),
            # One row name per line (``bal.core.heirs``): the people here too.
            "description": (
                "\n".join(delivery.person_name(line)
                          for line in wi.description.split("\n"))
                if isinstance(wi.description, str) else wi.description
            ),
            "sigs_have": int(getattr(wi, "sigs_have", 0)),
            "sigs_required": int(getattr(wi, "sigs_required", 0)),
        }
        if staged:
            numbers = {
                delivery.instalment_number(name)
                for name in names
                if not str(name).startswith(_EXECUTOR_HEIR)
            }
            out["delivery"] = numbers.pop() if len(numbers) == 1 else None
        if wi.we:
            out["willexecutor"] = wi.we.get("url")
        out["status"] = {}
        for key, value in wi.STATUS.items():
            out["status"][key] = bool(value[1])
        return out

    def _will_status_dict(self):
        """The will for the commands: its items, the count of each status
        and the Check Alive date (JSON-safe)."""
        staged = instalments.has_table(self)
        items = [self._willitem_summary(w, staged) for w in self.willitems.values()]
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
        #189: a chain of deliveries refused by the safety net
        (``WillChainError``) is told in plain words, the raw reason logged.
        """
        try:
            return build.build_will(self, with_creation_date=False)
        except will_mod.WillChainError as e:
            raise UserFacingException(chain_refused_message(e)) from None

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
        Refused while a delivery hold applies (#123, rule I6). Built by
        :func:`bal.engine.invalidate.build_invalidation`, as in the GUI
        (#152).
        """
        self._refuse_when_held()
        # The shared build of the GUI and the command line (#152).
        tx = build_invalidation(self, will)
        if tx is not None:
            try:
                self.wallet.set_label(tx.txid(), INVALIDATION_LABEL)
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
                # #168: the rows of every delivery with a table.
                instalments.effective_heirs(self),
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
            if isinstance(e, will_mod.StagedWillRemovedException):
                # #188: back to one delivery: the same road, its own words.
                message = staged_removed_message(e.deliveries) + " " + _(
                    "Sign and broadcast the invalidation transaction now, "
                    "then prepare again.")
            else:
                message = _(
                    "This inheritance was already signed/sent to will-executors "
                    "and you are postponing it. Sign and broadcast the "
                    "invalidation transaction now, then prepare again."
                )
            return {
                "result": "postponed",
                "message": message,
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
            # #188: its own reason (the transaction is never sent by itself).
            reason = ("back_to_one" if isinstance(e, will_mod.StagedWillRemovedException)
                      else "expired")
            return self._auto_rebuild_invalidate(reason)
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
        # #157: "sign all" skips the deselected will-executors' leftovers.
        unsigned = to_sign(self.willitems, selected=selected_urls(self.willexecutors))
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
        The signing is the GUI's too since #157
        (:func:`bal.engine.sign.sign_will`): without *txids* ("sign all")
        the copies of deselected will-executors are skipped (owner); when a
        signing fails, the ones signed before are saved, then the error is
        raised.
        """
        self._refuse_when_held()
        willitems = self.willitems
        if password is None and self.wallet.has_keystore_encryption():
            raise UserFacingException(_("Password required to sign transactions"))

        selected = selected_urls(self.willexecutors) if txids is None else None
        result = sign_will(self.wallet, willitems, password, txids,
                           selected=selected)
        self.save_willitems()
        self._save_to_history()
        if result.error is not None:
            raise result.error
        return result.txs

    def push_transactions_to_willexecutors(self, force=False, txids=None):
        """Push the valid+signed will transactions to their will-executors.

        Returns ``{url: broadcast_status}`` and raises ``UserFacingException``
        when no transaction matched the (optional) filter. When a push fails,
        ``_error`` is true and ``_failed`` maps each failed URL to the reason
        (CHANGELOG #105: before, only "Failed" was returned, so a script could
        not tell "offline" from "refused by the server"). Refused while a
        delivery hold applies (#123, rule I6).

        The sending is the GUI's too since #155
        (:mod:`bal.engine.push`): a will-executor that does not answer in
        time now leaves its transactions PUSH_FAIL (D1), and one whose
        current record is over the maximum fee or not valid is excluded,
        reported in ``_failed`` with the reason (D2).
        """
        self._refuse_when_held()
        willitems = self.willitems
        if txids is not None:
            willitems = {t: willitems[t] for t in txids if t in willitems}
        if not willitems:
            raise UserFacingException(_("No transaction matches the given txids"))

        # Skip executors the user deselected after building the will (the
        # controller's list is read from the config for every command).
        selected, excluded = select_push(
            willitems,
            self.willexecutors,
            force=force,
            max_fee=self.plugin.MAX_WILLEXECUTOR_FEE.get(),
            dust=self.wallet.dust_threshold(),
        )
        if not selected and not excluded:
            return {}

        result = push_will(willitems, selected)
        self.save_willitems()

        out = {}
        failed = {}
        for url, outcome in result.outcomes.items():
            if outcome == RECEIVED:
                out[url] = _("Success")
            elif outcome == FAILED:
                out[url] = _("Failed")
                failed[url] = result.errors.get(url) or _("unknown error")
            elif outcome == TIMEOUT:
                out[url] = _("Failed")
                failed[url] = _("no answer in time")
            else:
                # "Already present": its copy was checked (the item's status).
                out[url] = _("checking...")
        for url, reason in excluded.items():
            out[url] = _("Excluded")
            failed[url] = (
                _("excluded: fee above the maximum")
                if reason == EXCLUDED_FEE
                else _("excluded: not a valid will-executor")
            )
        if failed:
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

        Returns ``{txid: {pushed, checked, check_fail}}``. #156: the choice
        (the sent copies not verified yet) and the asking are the engine's
        (:func:`bal.engine.verify.verify_targets` /
        :func:`~bal.engine.verify.verify_will`), shared with the Check and
        the right click "check". #178: without ``txids`` (every copy) the
        will-executors not selected are not asked, as by the Check; named
        ``txids`` are asked: the user asks for them.
        """
        selected = selected_urls(self.willexecutors) if txids is None else None
        targets, _skipped = verify_targets(self.willitems, txids, selected=selected)
        verify_will(self.willitems, targets)

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
        """Merge imported will items into the live will (``bal_will_merge``).

        The GUI's rules since #158 (:func:`bal.engine.merge.merge_will`): a
        copy becomes signed only when its transaction is really signed, the
        signatures of a multisig add up, then the local validity check.
        Returns ``{merged, signed, partly_signed, unsigned}``: the merged
        copies and, among the valid ones, how many are signed, partly signed
        (a multisig waiting for another signer) and not signed.
        """
        ids, error = engine_merge_will(self, imported)
        if error is not None:
            _logger.error(f"merge_will validity check failed: {error}")
        summary = merge_summary(self.willitems, ids)
        return {
            "merged": len(imported),
            "signed": summary.signed,
            "partly_signed": len(summary.partial),
            "unsigned": summary.unsigned,
        }

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
        ``bal_delivery_form``). Since #169 ``staged`` (the wallet has a
        table of the deliveries) and ``deliveries`` (see
        ``bal_instalments_show``).
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
        # #169: staged inheritance, the table of the deliveries (details:
        # bal_instalments_show).
        out["staged"] = bool(instalments.has_table(self))
        out["deliveries"] = self._deliveries_view()[0]
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
        refused there (see ``bal_delivery_form``). With a table of the
        deliveries (#169) the date is delivery 1's: at least 28 days before
        delivery 2 (:meth:`_check_table_gap`).

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
            # #169: with a table the date is delivery 1's.
            self._check_table_gap(new_date)
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
            # #192: a fee below 20 sat/vB is stored, with the warning.
            warning = low_fee_warning(changes.get("baltx_fees"))
            if warning:
                messages.append(warning)
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
        :meth:`delivery_show` with a ``message``. #169: the period is
        refused while the wallet has a table of the deliveries, and a fixed
        date keeps its 28 days before delivery 2 (:meth:`_check_table_gap`).
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
        if form == delivery.FORM_PERIOD and instalments.has_table(self):
            # #169: said before the "will in force" refusal, which would
            # point to an invalidation that does not help.
            raise UserFacingException(
                _(
                    "This wallet has a table of the deliveries "
                    "(bal_instalments_show): the renewable period has one "
                    "delivery date. Remove the table first with "
                    "bal_instalments_clear."
                )
            )
        new_date = None
        if date is not None:
            try:
                new_date = delivery.parse_cli_date(date, time.time())
            except ValueError as e:
                raise UserFacingException(str(e)) from None
            self._check_table_gap(new_date)
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
    # Staged inheritance: the table of the deliveries (#169)
    # ------------------------------------------------------------------ #
    def _check_table_gap(self, new_date):
        """Refuse a delivery date too close to delivery 2 (#169).

        With a table the wallet's delivery date is delivery 1
        (:func:`bal.core.delivery.table_rows`): typed by the owner, it must
        stay at least :data:`bal.core.delivery.MIN_GAP_DAYS` days before
        delivery 2 (owner decision 5 of M3; only the automatic one-day
        anticipations may shorten the gap). Nothing to check without a
        table, or with one that cannot be read (the flows stop on it).
        """
        if not instalments.has_table(self):
            return
        # #170: the rule is shared with the window.
        second = delivery.too_close_to_second(self.will_settings, new_date)
        if second is not None:
            raise UserFacingException(
                _(
                    "This wallet has a table of the deliveries: its delivery date "
                    "is delivery 1, which must be at least {} days before "
                    "delivery 2 ({}). To move the other deliveries too, use "
                    "bal_instalments_set (see bal_instalments_show)."
                ).format(delivery.MIN_GAP_DAYS, context.utc_day_text(second))
            )

    def _deliveries_view(self):
        """The stored table as the commands show it (#169).

        Returns ``(rows, problem)``. ``rows``: one dict per delivery,
        ``{"delivery": n, "date", "locktime", "shares"}``, delivery 1 on the
        wallet's date (:func:`bal.core.delivery.table_rows`): the form
        ``bal_instalments_set`` takes back. ``problem``: None when the table
        fits the heirs (the rows are then normalized: every heir named, in
        the heirs' order), else the broken rule
        (:func:`bal.engine.instalments.check_table`) and the rows as stored.
        ``([], None)`` without a table; a value that cannot be read gives
        no rows and its reason.
        """
        try:
            stored = delivery.table_rows(self.will_settings)
        except ValueError as e:
            return [], str(e)
        if not stored:
            return [], None
        problem = None
        try:
            stored = instalments.check_table(self)
        except delivery.InstalmentsMismatch as e:
            problem = e.reason or str(e)
        rows = []
        for n, row in enumerate(stored, 1):
            row = row if isinstance(row, Mapping) else {}
            dated = self._dated(row.get("locktime"))
            shares = row.get("shares")
            rows.append({
                "delivery": n,
                "date": dated["date"] if dated else None,
                "locktime": dated["locktime"] if dated else row.get("locktime"),
                "shares": dict(shares) if isinstance(shares, Mapping) else shares,
            })
        return rows, problem

    def _items_per_delivery(self, count):
        """How many VALID will items each of ``count`` deliveries holds.

        The delivery of an item is read from its heir-row names ("Luca" is
        delivery 1, "Luca§2" delivery 2: :func:`bal.core.delivery.
        instalment_number`); the will-executor fee rows are left out. A
        list, delivery 1 first.
        """
        counts = [0] * count
        for w in self.willitems.values():
            if not w.get_status("VALID"):
                continue
            numbers = {
                delivery.instalment_number(name)
                for name in (w.heirs or {})
                if not str(name).startswith(_EXECUTOR_HEIR)
            }
            if len(numbers) == 1:
                n = numbers.pop()
                if 1 <= n <= count:
                    counts[n - 1] += 1
        return counts

    def instalments_show(self):
        """``bal_instalments_show``: the table of the deliveries (#169).

        Read only. ``staged``: the wallet has a table; ``date``: the
        wallet's delivery date (delivery 1); ``deliveries``: see
        :meth:`_deliveries_view`; ``fits_heirs`` and ``problem``: the table
        against the heirs (owner decision 3: when it no longer fits,
        ``bal_will_prepare`` stops until it is updated). When it fits:
        ``delivery_totals`` (the share of the estate paid at each date),
        ``heir_totals`` (each heir over the table), ``total`` (100%),
        ``gaps_days`` (whole days between two dates; the owner's changes
        keep at least ``min_gap_days``) and ``will_items`` (the VALID
        transactions of the will now, per delivery). ``message`` says it
        in words.
        """
        ws = self.will_settings if isinstance(self.will_settings, Mapping) else {}
        locktime = delivery.as_delivery(ws.get("locktime"))
        rows, problem = self._deliveries_view()
        staged = bool(instalments.has_table(self))
        out = {
            "staged": staged,
            "date": self._dated(locktime),
            "deliveries": rows,
            "fits_heirs": problem is None,
            "problem": problem,
            "min_gap_days": delivery.MIN_GAP_DAYS,
        }
        if not staged:
            out["message"] = _(
                "One delivery: every heir receives on {}. To stage the "
                "inheritance in 2 or 3 deliveries, write a table with "
                "bal_instalments_set (--example true writes an example to "
                "change)."
            ).format(context.utc_day_text(locktime) if locktime else "?")
            return out
        if problem is not None:
            out["message"] = _(
                "The table of the deliveries no longer matches the heirs ({}): "
                "bal_will_prepare stops until the table is updated with "
                "bal_instalments_set."
            ).format(problem)
            return out
        totals = {}
        delivery_totals = []
        for row in rows:
            paid = Decimal(0)
            for name, share in row["shares"].items():
                value = delivery.as_share(share)
                paid += value
                totals[name] = totals.get(name, Decimal(0)) + value
            delivery_totals.append(delivery.share_text(paid))
        out["delivery_totals"] = delivery_totals
        out["heir_totals"] = {
            name: delivery.share_text(value) for name, value in totals.items()
        }
        out["total"] = delivery.share_text(sum(totals.values(), Decimal(0)))
        dates = [row["locktime"] for row in rows]
        out["gaps_days"] = [
            int((b - a) // delivery.DAY) for a, b in zip(dates, dates[1:], strict=False)
        ]
        out["will_items"] = self._items_per_delivery(len(rows))
        out["message"] = _("{} deliveries: {}.").format(
            len(rows),
            ", ".join(
                "{} ({})".format(row["date"], total)
                for row, total in zip(rows, delivery_totals, strict=True)
            ),
        )
        return out

    def _table_from_input(self, raw, now):
        """The table typed by the owner as engine rows (#169).

        ``raw``: a list of deliveries, or the text of one (JSON), or the
        answer of ``bal_instalments_show`` (its ``deliveries`` are read).
        Each delivery: ``date`` (YYYY-MM-DD at 00:00 UTC, or a UNIX
        timestamp; :func:`bal.core.delivery.parse_cli_date`: a period, a
        block height or a past date is refused) or ``locktime`` (both are
        accepted when they are the same date, as the show command gives
        them), ``shares`` (``{heir: "n%"}``), and optionally ``delivery``
        (its number, which must match its place). Any other field is
        refused: a typo must not be skipped. Returns
        ``[{"locktime", "shares"}, ...]``; the rules of the table are
        checked afterwards (:func:`bal.core.delivery.validate_instalments`).
        """
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except ValueError as e:
                raise UserFacingException(
                    _("The table is not valid JSON: {}").format(e)
                ) from None
        if isinstance(raw, Mapping) and "deliveries" in raw:
            raw = raw["deliveries"]
        if not isinstance(raw, (list, tuple)):
            raise UserFacingException(
                _(
                    'The table must be a list of deliveries, e.g. [{"date": '
                    '"2030-03-01", "shares": {"Luca": "50%"}}, {"date": '
                    '"2031-03-01", "shares": {"Luca": "50%"}}].'
                )
            )
        rows = []
        for n, row in enumerate(raw, 1):
            if not isinstance(row, Mapping):
                raise UserFacingException(
                    _("Delivery {}: not a delivery with date and shares: {}").format(
                        n, row
                    )
                )
            unknown = sorted(str(k) for k in row if k not in _TABLE_FIELDS)
            if unknown:
                raise UserFacingException(
                    _("Delivery {}: unknown field {}: give date and shares.").format(
                        n, ", ".join(unknown)
                    )
                )
            if "delivery" in row:
                number = _delivery_number(row["delivery"])
                if number is None:
                    raise UserFacingException(
                        _("Delivery {}: not a delivery number: {}").format(
                            n, row["delivery"]
                        )
                    )
                if number != n:
                    raise UserFacingException(
                        _(
                            "Delivery {}: it says delivery {}: give the "
                            "deliveries in their order."
                        ).format(n, number)
                    )
            if "date" not in row and "locktime" not in row:
                raise UserFacingException(_("Delivery {}: no date.").format(n))
            if "shares" not in row:
                raise UserFacingException(_("Delivery {}: no shares.").format(n))
            try:
                if "locktime" in row:
                    ts = delivery.parse_cli_date(row["locktime"], now)
                    if "date" in row and str(row["date"]).strip() != (
                        context.utc_day_text(ts)
                    ):
                        try:
                            other = delivery.parse_cli_date(row["date"], now)
                        except ValueError:
                            other = None
                        if other != ts:
                            raise UserFacingException(
                                _(
                                    "Delivery {}: date and locktime are two "
                                    "different dates: give only one."
                                ).format(n)
                            )
                else:
                    ts = delivery.parse_cli_date(row["date"], now)
            except ValueError as e:
                raise UserFacingException(
                    _("Delivery {}: {}").format(n, e)
                ) from None
            rows.append({"locktime": ts, "shares": row["shares"]})
        return rows

    def instalments_set(self, table=None, path=None, example=None,
                        confirm_earlier=None):
        """``bal_instalments_set``: write the table of the deliveries (#169).

        One source: ``table`` (a list, or its JSON text), ``path`` (a JSON
        file of the same form) or ``example`` (the owner's example table,
        :func:`bal.core.delivery.default_instalments`: 33 / 33 / 34 %
        split among the heirs, from the wallet's date, one year apart).
        Refused in BASIC mode (owner decision D5: staged inheritance is
        ADVANCED only), with the period form (one renewable date, #127
        decision 10), without heirs, and while the settings could not be
        prepared. The rules of the table (owner, M2: 2 or 3 dates in the
        future, at least 28 days apart, shares of the whole estate adding up
        to 100 %, only the heirs' names) are checked before anything is
        written. A delivery date EARLIER than the one in force (the VALID
        items of that delivery, else the stored date) needs
        ``confirm_earlier``, as in ``bal_delivery_set``: the heirs could
        receive earlier, and a typo in a script must not do it. So does a
        table by which the heirs would receive more of the estate by one of
        its dates than now (shares moved earlier, delivery 3 removed: owner
        decision (a), :func:`bal.core.delivery.first_earlier`). Then
        :func:`bal.engine.context.set_instalments` stores it (delivery 1
        becomes the wallet's date) and the wallet file is saved. A later
        date is stored too: the next ``bal_will_prepare`` asks for the paid
        invalidation of a will already sent (D6). Returns
        :meth:`instalments_show` with a ``message``.
        """
        example = _as_bool(example, "example")
        confirm_earlier = _as_bool(confirm_earlier, "confirm_earlier")
        if self.settings_unavailable:
            raise UserFacingException(
                _delivery_hold_message(delivery.DeliverySettingsUnavailable())
            )
        sources = [table is not None, path is not None, example]
        if sum(1 for given in sources if given) != 1:
            raise UserFacingException(
                _(
                    "Give the table with one of --table, --path or "
                    "--example true."
                )
            )
        if self.plugin.is_basic_mode():
            raise UserFacingException(
                _(
                    "The inheritance in several deliveries is available in "
                    "ADVANCED mode only: bal_settings_set bal_user_type "
                    "advanced, then try again."
                )
            )
        if delivery.is_period(self.will_settings):
            raise UserFacingException(
                _(
                    "This wallet uses a renewable period, which has one "
                    "delivery date. Choose a fixed date first with "
                    "bal_delivery_form form=fixed date=YYYY-MM-DD, then write "
                    "the table."
                )
            )
        names = delivery.payee_names(self.heirs)
        if not names:
            raise UserFacingException(
                _("There are no heirs: add them first with bal_heirs_add.")
            )
        now = time.time()
        if example:
            first = delivery.as_delivery(self.will_settings.get("locktime"))
            if first is None:
                raise UserFacingException(
                    _(
                        "The wallet has no delivery date: set one with "
                        "bal_delivery_set --date YYYY-MM-DD."
                    )
                )
            rows = delivery.default_instalments(names, first)
        else:
            raw = read_json_file(path) if path is not None else table
            rows = self._table_from_input(raw, now)
        try:
            for name in names:
                if delivery.has_separator(name):
                    raise ValueError(
                        "an heir name holds {!r}: {!r}: rename it first".format(
                            delivery.INSTALMENT_SEP, name
                        )
                    )
            rows = delivery.validate_instalments(rows, names, now)
        except ValueError as e:
            raise UserFacingException(
                _("The table was not stored: {}").format(e)
            ) from None

        # The dates in force, per delivery, read before anything is written
        # (#170: the rules of owner decision (a) are shared with the window).
        in_force, reference, old = instalments.dates_in_force(self)
        if not confirm_earlier:
            hit = delivery.first_earlier_date(rows, reference)
            if hit is not None:
                n, date, ref, from_will = hit
                if from_will:
                    text = _(
                        "Delivery {}: {} is earlier than the date in force "
                        "({}): the heirs could receive the bitcoin earlier."
                    )
                else:
                    # No will in force for this delivery: its stored date
                    # (as bal_delivery_set words it).
                    text = _(
                        "Delivery {}: {} is earlier than the current date of "
                        "this delivery ({}): the heirs could receive the "
                        "bitcoin earlier."
                    )
                raise UserFacingException(
                    text.format(
                        n,
                        context.utc_day_text(date),
                        context.utc_day_text(ref),
                    ) + " " + _confirm_earlier_hint()
                )
            earlier = delivery.first_earlier(
                delivery.schedule_of(rows), delivery.schedule_now(reference, old))
            if earlier is not None:
                n, date, more, before = earlier
                raise UserFacingException(
                    _(
                        "Delivery {} ({}): by this date the heirs would receive "
                        "{} of the estate instead of {}: part of it would "
                        "arrive earlier."
                    ).format(
                        n,
                        context.utc_day_text(date),
                        delivery.share_text(more),
                        delivery.share_text(before),
                    ) + " " + _confirm_earlier_hint()
                )
        try:
            stored_rows = context.set_instalments(self, rows, now=now)
        except ValueError as e:
            raise UserFacingException(
                _("The table was not stored: {}").format(e)
            ) from None
        self.wallet.save_db()

        messages = [
            _("The table of the deliveries is stored: {}.").format(
                ", ".join(
                    _("delivery {} on {}").format(
                        n, context.utc_day_text(row["locktime"])
                    )
                    for n, row in enumerate(stored_rows, 1)
                )
            )
        ]
        for n, date in delivery.later_than_in_force(stored_rows, in_force):
            messages.append(
                _(
                    "Delivery {} is later than the will in force ({}): if "
                    "that will was already sent to the will-executors, the "
                    "next bal_will_prepare asks to invalidate it first "
                    "(on-chain, miner fee)."
                ).format(n, context.utc_day_text(date))
            )
        messages.append(
            _(
                "While the table is there, the heirs' shares come from it, "
                "not from the amounts of the heirs."
            )
        )
        messages.append(
            _("Run bal_will_prepare to prepare the will with the new table.")
        )
        out = self.instalments_show()
        out["message"] = " ".join(messages)
        return out

    def instalments_clear(self, confirm_earlier=None, confirm_invalidation=None):
        """``bal_instalments_clear``: remove the table (#169).

        #188: with a will in several deliveries in force (signed or sent),
        going back to one delivery means its invalidation on the blockchain
        first (the next ``bal_will_check`` says so): refused without
        ``confirm_invalidation``.

        One delivery again (:func:`bal.engine.context.clear_instalments`):
        everything at the wallet's date (delivery 1's), with the amounts of
        the heirs, which the table had replaced: the answer lists them
        (``heirs``) and the message says to check them before
        ``bal_will_prepare``. The heirs would then receive the rest of the
        estate at delivery 1, earlier: refused without ``confirm_earlier``
        (owner decision (a), :func:`bal.core.delivery.first_earlier`). Refused while the
        settings could not be prepared. ``removed``: False when there was
        no table.
        """
        confirm_earlier = _as_bool(confirm_earlier, "confirm_earlier")
        confirm_invalidation = _as_bool(confirm_invalidation, "confirm_invalidation")
        if self.settings_unavailable:
            raise UserFacingException(
                _delivery_hold_message(delivery.DeliverySettingsUnavailable())
            )
        if instalments.has_table(self) and not confirm_invalidation:
            staged = will_mod.Will.staged_in_force(self.willitems, self.wallet)
            if staged:
                count = max(
                    delivery.instalment_number(name)
                    for w in staged.values() for name in (w.heirs or {})
                    if not str(name).startswith('w!ll3x3c"'))
                raise UserFacingException(
                    staged_removed_message(count) + " " + _(
                        "The will's coins are spent together in one transaction "
                        "visible to everybody. Ask the owner; if this is what the "
                        "owner wants, run the command again with "
                        "--confirm_invalidation true."))
        first = delivery.as_delivery(self.will_settings.get("locktime"))
        if instalments.has_table(self) and not confirm_earlier and first is not None:
            _in_force, reference, old = instalments.dates_in_force(self)
            earlier = delivery.first_earlier(
                [(first, _HUNDRED)], delivery.schedule_now(reference, old)
            )
            if earlier is not None:
                _n, date, _more, before = earlier
                raise UserFacingException(
                    _(
                        # Same words as the window (#188, owner's ZIP test).
                        "With one delivery only, the heirs receive the whole "
                        "estate on {}, the date of delivery 1. Today they "
                        "receive {} by that date: the rest would arrive "
                        "earlier than planned."
                    ).format(context.utc_day_text(date), delivery.share_text(before))
                    + " " + _confirm_earlier_hint()
                )
        removed = context.clear_instalments(self)
        if removed:
            self.wallet.save_db()
        locktime = delivery.as_delivery(self.will_settings.get("locktime"))
        day = context.utc_day_text(locktime) if locktime is not None else "?"
        out = {
            "removed": removed,
            "date": self._dated(locktime),
            "heirs": self.heirs_list(),
        }
        if removed:
            out["message"] = _(
                "The table of the deliveries was removed: every heir receives "
                "everything on {}, with the amounts of the heirs (see heirs), "
                "which the table had replaced. Check them (bal_heirs_update), "
                "then run bal_will_prepare."
            ).format(day)
        else:
            out["message"] = _(
                "There is no table of the deliveries: the wallet has one "
                "delivery date ({})."
            ).format(day)
        return out

    # ------------------------------------------------------------------ #
    # Heirs CRUD
    # ------------------------------------------------------------------ #
    def _with_table_note(self, out):
        """Add ``instalments_note`` to an heir command's answer (#169).

        Only for a wallet with a table of the deliveries (owner answer 1a
        of M3b: the heir commands work, and say what follows): the heirs
        still fit the table (their shares come from it, not from their
        amounts), or they no longer do (``bal_will_prepare`` stops until
        the table is updated, owner decision 3). Returns ``out``.
        """
        if not instalments.has_table(self):
            return out
        try:
            instalments.check_table(self)
        except delivery.InstalmentsMismatch:
            out["instalments_note"] = _(
                "The heirs no longer match the table of the deliveries: "
                "bal_will_prepare stops until the table is updated with "
                "bal_instalments_set (see bal_instalments_show)."
            )
        else:
            out["instalments_note"] = _(
                "This wallet has a table of the deliveries: the heirs' shares "
                "come from it (bal_instalments_show), not from the amounts of "
                "the heirs."
            )
        return out

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

        CHANGELOG #103. Every heir gets the will's delivery date: the next
        check/prepare overwrote a per-heir ``--locktime`` without a word, so
        it is refused with a message that says how to change the date.
        #168 (D7): always, BAL's global multiverse option is no longer read
        (staged deliveries keep their dates in the table).
        """
        if locktime is None:
            return
        raise UserFacingException(
            _(
                "Every heir gets the delivery date of the will, so --locktime "
                "cannot be set for one heir. To change the date for every "
                "heir, use bal_delivery_set --date YYYY-MM-DD (see the "
                "current date with bal_delivery_show)."
            )
        )

    def _check_heir_name(self, name):
        """Refuse an heir name holding the delivery separator (#168).

        With a table of deliveries the rows of deliveries 2 and 3 are named
        "Luca§2", "Luca§3" (``bal.core.delivery.INSTALMENT_SEP``): a person
        named so would collide with them (owner decision 2).
        """
        if delivery.has_separator(name):
            raise UserFacingException(
                _("An heir name cannot contain the sign {}.").format(
                    delivery.INSTALMENT_SEP))

    def heirs_add(self, name, address, amount, locktime=None):
        """Add (or replace) the heir ``name``; ``locktime`` is refused (see
        :meth:`_check_heir_locktime`), and so is a name with "§"
        (:meth:`_check_heir_name`, #168). #140: never a second message
        (:meth:`_check_one_message`). #169: with a table of the deliveries
        the answer has ``instalments_note`` (:meth:`_with_table_note`).
        #197: ``warning`` when the address belongs to this wallet
        (:meth:`_with_own_address_warning`); the heir is saved anyway."""
        self._check_heir_locktime(locktime)
        self._check_heir_name(name)
        self._check_address_free(address, name)
        self._check_one_message(address, name)
        value = self._build_heir_entry(name, address, amount, locktime)
        own = heirs_mod.own_address_heirs(self.wallet, {name: value}, stored=self.heirs)
        self.heirs[name] = value
        self.wallet.save_db()
        return self._with_table_note(self._with_own_address_warning(
            {"name": name, "value": list(value)}, own))

    @staticmethod
    def _with_own_address_warning(out, names):
        """``out`` with ``warning`` when ``names`` is not empty (#197).

        ``names``: the heirs just saved at an address of this wallet
        (:func:`bal.core.heirs.own_address_heirs`). The owner's choice for
        the command line: no question and no new option (a script cannot
        answer), the heir is saved as before; only this line in the
        answer, the same check as the heir window.
        """
        if not names:
            return out
        if len(names) == 1:
            out["warning"] = _(
                "The address of {} belongs to this wallet: on the delivery "
                "date the bitcoin would stay in this wallet."
            ).format(names[0])
        else:
            out["warning"] = _(
                "The addresses of {} belong to this wallet: on the delivery "
                "date their bitcoin would stay in this wallet."
            ).format(", ".join(names))
        return out

    def _check_one_message(self, address, name):
        """Refuse a second OP_RETURN message (#140, owner: one per will);
        ``name``'s own message may change its text."""
        try:
            heirs_mod.check_one_message(self.heirs, name, address)
        except heirs_mod.SecondMessageNotAllowed as e:
            raise UserFacingException(heir_error_message(e)) from None

    def _check_address_free(self, address, name):
        """Refuse an address already used by ANOTHER heir (#135, owner: no
        two heirs on one address); ``name``'s own address is allowed."""
        other = heirs_mod.heir_with_address(self.heirs, address, except_name=name)
        if other is not None:
            raise UserFacingException(
                _("This address is already used by the heir {}.").format(other))

    def heirs_update(self, name, address=None, amount=None, locktime=None):
        """Change only the given fields of the heir ``name``; ``locktime``
        is refused (see :meth:`_check_heir_locktime`). #140:
        never a second message (:meth:`_check_one_message`). #169: with a
        table of the deliveries the answer has ``instalments_note``
        (:meth:`_with_table_note`). #197: ``warning`` when the address is
        changed to one of this wallet (:meth:`_with_own_address_warning`)."""
        if name not in self.heirs:
            raise UserFacingException(_("Heir not found: {}").format(name))
        self._check_heir_locktime(locktime)
        current = list(self.heirs[name])
        address = address if address is not None else current[0]
        amount = amount if amount is not None else current[1]
        locktime = locktime if locktime is not None else current[2]
        self._check_address_free(address, name)
        self._check_one_message(address, name)
        value = self._build_heir_entry(name, address, amount, locktime)
        own = heirs_mod.own_address_heirs(self.wallet, {name: value}, stored=self.heirs)
        self.heirs[name] = value
        self.wallet.save_db()
        return self._with_table_note(self._with_own_address_warning(
            {"name": name, "value": list(value)}, own))

    def heirs_delete(self, names):
        """Delete heirs by name; ``not_found`` lists the unknown names.

        A single name is accepted too (CHANGELOG #105): Electrum passes a
        plain name as text, and the loop then ran over its letters. #169:
        with a table of the deliveries the answer has ``instalments_note``
        (:meth:`_with_table_note`).
        """
        deleted, not_found = [], []
        for name in _as_name_list(names):
            if name in self.heirs:
                self.heirs.pop(name)
                deleted.append(name)
            else:
                not_found.append(name)
        self.heirs.save()
        # #138: the notes of the deleted heirs go with them.
        heir_notes.delete(self.wallet, deleted)
        self.wallet.save_db()
        return self._with_table_note({"deleted": deleted, "not_found": not_found})

    def heirs_list(self):
        """The stored heirs, ``{name: [address, amount, date]}`` (one row
        per person, also with a table of the deliveries)."""
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
        is the number of heirs afterwards. Every heir gets the will's date
        anyway (#168: BAL's per-heir dates are no longer used); a name with
        the delivery separator "§" is skipped (#168). #140: a message
        (OP_RETURN) when the heirs already have one is skipped, with the
        reason. #169: with a table of the deliveries the answer has
        ``instalments_note`` (:meth:`_with_table_note`). #197: ``warning``
        names the imported heirs paid at an address of this wallet
        (:meth:`_with_own_address_warning`); they are imported anyway.
        """
        data = read_json_file(path)
        if isinstance(data, dict) and isinstance(data.get("heirs"), dict):
            data = data["heirs"]  # the nesting Heirs._validate accepts
        if not isinstance(data, dict):
            raise UserFacingException(_("Invalid heirs file: {}").format(path))
        valid, skipped = {}, {}
        for name, value in data.items():
            try:
                # #168: a name with the delivery separator is refused (the
                # rows of deliveries 2 and 3 are named "Luca§2"...).
                self._check_heir_name(name)
                valid[name] = Heirs.validate_heir(name, value)
            except Exception as e:
                valid.pop(name, None)
                skipped[name] = str(_user_facing(e))
        # #140 (owner): one message per will; the next ones of the file
        # are skipped and said so.
        valid, extra = heirs_mod.keep_one_message(self.heirs, valid)
        for name in extra:
            skipped[name] = heir_error_message(heirs_mod.SecondMessageNotAllowed(name))
        # #197: read before the update (an heir already saved with the
        # same address is not warned again).
        own = heirs_mod.own_address_heirs(self.wallet, valid, stored=self.heirs)
        self.heirs.update(valid)
        self.heirs.save()
        # #138: no note of an heir that is no longer there.
        heir_notes.clean(self.wallet, list(self.heirs.keys()))
        self.wallet.save_db()
        return self._with_table_note(self._with_own_address_warning({
            "imported": len(valid),
            "names": list(valid),
            "skipped": skipped,
            "total": len(self.heirs),
            # #135: heirs on one address after the import (a warning: the
            # file is imported as it is; keep only one of each pair).
            "same_address": [list(p) for p in heirs_mod.shared_addresses(self.heirs)],
        }, own))

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
