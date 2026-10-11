"""The Check's phase 1 in the engine: variables, Check Alive, amounts (#120).

Phase 5e, step 5e-08. Ported line by line from the start of
``BalBuildWillDialog.task_phase1`` (``bal/gui/qt/dialogs.py`` before
#120): prepare the variables of the check, stop on the Check Alive gate,
check the heirs' amounts. The dialog now calls :func:`check_variables`;
its rows are written by ``bal.gui.qt.flow_adapter.DialogFlowUI`` with the
same texts, colours and order as before. The next parts of phase 1 (the
will in force, the rebuild) move in the next steps.

The functions work on a host (:class:`bal.engine.host.WalletHost`) that
also has ``init_class_variables()`` and ``check_alive_info()`` (both
``BalWindow`` and ``BalController`` do; they are called through the host
so the window keeps its error log), a run state
(:class:`bal.engine.ui.CheckRun`) and a FlowUI (:mod:`bal.engine.ui`).

No Qt, no ``bal.cli`` (``bal/engine`` rules, see ``bal/engine/__init__``).
"""

from dataclasses import dataclass
from typing import Any

from electrum.logging import get_logger

from ..core.checkalive import CheckAliveAfterDeliveryError, CheckAliveError
from ..core.heirs import WillExecutorFeeTooHighException
from ..core.messages import amounts_message, past_date_message

# NoHeirsException from bal.core.will, the class init_class_variables
# raises; bal.core.heirs has another class with the same name.
from ..core.will import AmountException, NoHeirsException, Will
from . import context
from .ui import Step

_logger = get_logger(__name__)

# The events check_variables sends: the states of its Step.VARIABLES row
# and the codes of its own rows. Codes, never shown; every front-end maps
# each one (tests/test_gui_flow_adapter.py checks the dialog does).
VARIABLES_STATES = (
    "run",
    "ok",
    "expired",
    "check_alive_passed",
    "stopped",
    "no_heirs",
    "check_alive_before_delivery",
)
VARIABLES_NOTES = (
    "check_alive_reason",
    "check_alive_error",
    "fixed_amounts_exceed",
    "amounts_explained",
    "amounts_adjust",
    "executor_fee_too_high",
)


@dataclass(frozen=True)
class Phase1Result:
    """Why phase 1 stops early, and what the dialog needs to go on.

    ``kind`` is the code ``task_phase1`` returned first before #120
    ("no_heirs", "check_alive_renew", ...; ``_on_success_phase1_body``
    decides on it). ``tx`` is the unsigned renewal invalidation for
    "check_alive_renew", else ``None``. The other ends of phase 1 (the
    invalidations, a failed build, the normal end with ``have_to_sign``
    and the built transactions) are added when their code moves.
    """

    kind: str
    tx: Any = None

    def legacy(self):
        """The tuple ``task_phase1`` returned before #120: ``(kind, tx)``.

        ``_on_success_phase1_body`` unpacks it as ``have_to_sign, tx``, so
        the dialog keeps returning it until that code moves too.
        """
        return (self.kind, self.tx)


def past_date_message_of(host):
    """:func:`bal.core.messages.past_date_message` of the host's will
    settings, or ``None`` when they cannot be read. Never raises.

    The rule of ``BalBuildWillDialog._past_date_message`` (CHANGELOG #115),
    moved here in #120; the dialog method now calls this one.
    """
    try:
        will_settings = host.will_settings
    except Exception as _err:
        _logger.debug(f"past_date_message_of: {_err}")
        return None
    return past_date_message(will_settings)


def amount_warning_is_false_alarm(host, available_utxos):
    """True when an amount warning of ``Will.check_amounts`` is not a real
    amount problem (BUG-5, CHANGELOG #76): the delivery date has passed,
    or no coin is visible right now (see
    :meth:`Will.amount_warning_is_false_alarm`).

    The rule of ``BalBuildWillDialog._amount_warning_is_false_alarm``
    (CHANGELOG #115), moved here in #120; the dialog method now calls this
    one. An error in the date test means "not a false alarm" (the warning
    is shown), as before.
    """
    try:
        date_passed = bool(past_date_message_of(host))
    except Exception as _err:
        _logger.debug(f"amount_warning_is_false_alarm: {_err}")
        return False
    return Will.amount_warning_is_false_alarm(date_passed, available_utxos)


def check_variables(host, run, ui):
    """Phase 1, first part: the variables, the Check Alive and the amounts.

    Ported line by line from ``BalBuildWillDialog.task_phase1``
    (dialogs.py:862-1004 before #120). In order:

    1. ``ui.step(Step.VARIABLES, "run")``, then
       ``host.init_class_variables()`` (which may store a converted Check
       Alive through ``host.store_will_setting``, as before);
    2. the Check Alive gate. After the delivery date: stop
       "check_alive_before_delivery". Passed: if ``run.invalidation_broadcast``
       stop "invalidation_pending" (never invalidate twice; no outcome
       row), else ``run.check_alive_info = host.check_alive_info()`` and:
       an expired will stops "invalidate_classic"; a renewable one gets its
       unsigned invalidation (``Will.invalidate_will``) and stops
       "check_alive_renew" with it, so the dialog asks Renew / Not now;
       otherwise stop "check_alive_stop" with the reason. No heirs: stop
       "no_heirs";
    3. the amounts (``Will.check_amounts`` on the coins the will can use).
       Fixed amounts above the balance stop "fixed_amounts_exceed"; any
       other amount warning, or a will-executor fee above the maximum,
       adds a note and phase 1 goes on.

    Events: ``ui.step(Step.VARIABLES, state)`` with ``state`` in
    ``VARIABLES_STATES``, and ``ui.note(code, **data)`` with ``code`` in
    ``VARIABLES_NOTES``:

    * ``"check_alive_reason"``, ``info``: why the will cannot be renewed in
      one click (``info`` as returned by ``check_alive_info()``);
    * ``"check_alive_error"``, ``error``: the Check Alive error as text,
      when there is no ``info``;
    * ``"fixed_amounts_exceed"`` and ``"amounts_explained"``, ``text`` and
      ``info``: ``Will.describe_amounts`` and its sentence
      (:func:`bal.core.messages.amounts_message`, computed before the
      outcome row, as before);
    * ``"amounts_adjust"``: an amount problem with no explanation;
    * ``"executor_fee_too_high"``, ``error``: the exception as text.

    Returns a :class:`Phase1Result` when phase 1 stops here, ``None`` when
    it goes on. Any other exception (from ``init_class_variables``, the
    coins, the amounts check or ``Will.invalidate_will``) propagates as
    before; the step row then stays on "run".
    """
    ui.step(Step.VARIABLES, "run")
    try:
        host.init_class_variables()
    except CheckAliveAfterDeliveryError:
        ui.step(Step.VARIABLES, "check_alive_before_delivery")
        return Phase1Result("check_alive_before_delivery")
    except CheckAliveError as cae:
        # CHANGELOG #79 (owner decision): a passed Check Alive is never
        # invalidated on its own any more. It used to invalidate and then
        # stop on "Checking variables: Wait" with NO will in force (the
        # renewed will was never prepared). Now the owner is asked to
        # renew (on_success_phase1); when a one-click renewal is not
        # possible the reason is shown and nothing is invalidated.
        _logger.debug(f"phase1: check alive passed: {cae}")
        if run.invalidation_broadcast:
            # Renewal invalidation already sent on the previous pass but
            # not yet seen by the wallet: never invalidate twice.
            return Phase1Result("invalidation_pending")
        info = host.check_alive_info()
        run.check_alive_info = info
        if info and info.get("reason") == "expired":
            # The will in force is already past its delivery date: it can
            # be executed now. That is the EXPIRED case (invalidate at
            # once, Electrum's classic window), not a Check Alive matter;
            # stopping here would leave it executable (scenario C3adv).
            ui.step(Step.VARIABLES, "expired")
            return Phase1Result("invalidate_classic")
        tx = None
        if info and info.get("renewable") and info.get("has_will"):
            fee_per_byte = host.will_settings.get("baltx_fees", 1)
            tx = Will.invalidate_will(
                host.willitems,
                host.wallet,
                fee_per_byte,
                history_label=host.bal_plugin.HISTORY_LABEL.get(),
                will_locktime=Will.get_min_locktime(
                    host.willitems,
                    default_value=host.date_to_check,
                ),
            )
        if tx:
            ui.step(Step.VARIABLES, "check_alive_passed")
            return Phase1Result("check_alive_renew", tx=tx)
        ui.step(Step.VARIABLES, "stopped")
        if info:
            ui.note("check_alive_reason", info=info)
        else:
            ui.note("check_alive_error", error=str(cae))
        return Phase1Result("check_alive_stop")
    except NoHeirsException:
        ui.step(Step.VARIABLES, "no_heirs")
        return Phase1Result("no_heirs")
    available_utxos = []
    try:
        _logger.debug("checking variables")
        # The coins the will can use: Util.get_available_utxos on the
        # wallet, the History label and the will's earliest date, as before.
        available_utxos = context.available_utxos(host)
        Will.check_amounts(
            host.heirs,
            host.willexecutors,
            available_utxos,
            host.date_to_check,
            host.wallet.dust_threshold(),
            max_fee=host.bal_plugin.MAX_WILLEXECUTOR_FEE.get(),
        )
        _logger.debug("variables ok")
        ui.step(Step.VARIABLES, "ok")
    except AmountException as e:
        # The explanation is computed in every case (CHANGELOG #82): the
        # rescaled percentages / proportional fixed amounts depend only on
        # the settings, not on the coins visible right now. With no coin
        # visible (e.g. after a Cancel) check_amounts fails on its first,
        # balance-based test, which used to hide the explanation too.
        info = Will.describe_amounts(
            host.heirs,
            host.willexecutors,
            available_utxos,
            host.date_to_check,
            host.wallet.dust_threshold(),
        )
        text = amounts_message(info)
        false_alarm = amount_warning_is_false_alarm(host, available_utxos)
        if info and info["kind"] == "fixed_exceeds":
            # Owner decision (CHANGELOG #76): the build would silently
            # leave the percentage heirs out, so stop and let the user
            # correct the fixed amounts. Own row: never overwritten.
            ui.step(Step.VARIABLES, "stopped")
            ui.note("fixed_amounts_exceed", text=text, info=info)
            return Phase1Result("fixed_amounts_exceed")
        # Every other case ends with "Ok" on the first row (it used to stay
        # on "Wait" when the warning below was overwritten, #82) and the
        # explanation, if any, on its own row the next steps never
        # overwrite.
        ui.step(Step.VARIABLES, "ok")
        if text:
            ui.note("amounts_explained", text=text, info=info)
        elif false_alarm:
            # BUG-5 (CHANGELOG #76): not an amount problem, see the helper.
            _logger.debug(f"amount check skipped (not an amount problem): {e!r}")
        else:
            ui.note("amounts_adjust")
    except WillExecutorFeeTooHighException as e:
        # Own row, never overwritten; the first row no longer stays on
        # "Wait" (CHANGELOG #82).
        ui.step(Step.VARIABLES, "ok")
        ui.note("executor_fee_too_high", error=str(e))
    return None
