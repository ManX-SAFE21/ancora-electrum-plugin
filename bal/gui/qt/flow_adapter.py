"""The Check dialog's side of the engine's events (CHANGELOG #120).

Phase 5e, step 5e-08. Phase 1 of the Check now starts in the engine
(:func:`bal.engine.check.check_variables`), which reports through a
FlowUI and keeps its run state in a CheckRun (:mod:`bal.engine.ui`). The
Building Will dialog (``BalBuildWillDialog``) gives it both:

* :class:`DialogFlowUI` turns every event into the very ``msg_*`` call the
  dialog made before #120: same label, status, colour, row and order, so
  the log rows, the step bar and the report's boxes stay the same (the
  golden files of #112/#114 compare them);
* :class:`DialogRun` reads and writes the run state in the dialog's own
  attributes, which the dialog's other methods keep using.

``task_phase1`` makes both anew at each pass, as it made the local
``varrow`` before: every pass writes its own "Checking variables" row,
and no dialog attribute is added (a dialog built without ``__init__``,
as in the tests and the scenario harness, works unchanged).

The texts are translated when the event arrives, never at import (the
catalog is loaded later).

Since #123 also the holds of the wallet's delivery settings: the "which
date?" question (state ``delivery_choice``; the dialog then asks it),
settings that could not be prepared (note
``delivery_settings_unavailable``) and, since #201, the rest of a will in
several deliveries cancelled (note ``delivery_rest_cancelled``). Their
texts are marked here and shared with the window (``bal.gui.qt.window``),
which shows them too.
"""

from ...core import datetext
from ...core.messages import check_alive_message
from ...engine.ui import Step
from ...i18n import N_, _

#: The delivery settings of the wallet could not be prepared (#123, fail
#: closed). Shown by the Check, Prepare and the Sign / Broadcast /
#: Invalidate commands.
SETTINGS_UNAVAILABLE = N_(
    "The delivery settings of this wallet could not be prepared: nothing "
    "was changed. Please restart Electrum and send the log."
)
#: The "which date?" question waits (#123): the Check asks it.
CHOOSE_DATE_FIRST = N_(
    "Nothing is signed or sent until you choose the delivery date: press "
    "'Check your will'."
)
#: The status of the "Checking variables" row while the question is asked.
CHOOSE_DATE = N_("Choose the delivery date")
#: #201: the rest of a will in several deliveries was cancelled after
#: delivery {} (the owner's Invalidate, or the coins spent): nothing is
#: prepared again on its own. Shown by the Check, Prepare and the Sign /
#: Broadcast / Invalidate commands.
REST_CANCELLED = N_(
    "Delivery {} was executed and the remaining deliveries were cancelled "
    "(the coins that paid them were spent): no will is in force, and "
    "nothing is prepared, signed or sent. To prepare a new will, press "
    "\"Back to one delivery\" in the window \"Inheritance in several "
    "deliveries\" (Tools in the Will tab, ADVANCED mode), then choose a new "
    "date and press 'Check your will'."
)


class DialogFlowUI:
    """A FlowUI that writes the Check dialog's rows (``msg_*`` calls).

    It calls the dialog's own bound methods (``dialog.msg_set_status``,
    ``dialog.msg_edit_row``...), never the class functions: the test
    dialogs record every row by overriding ``msg_edit_row``. It runs on
    the thread that sends the events (the dialog's worker thread, as
    before). An event it does not know raises ``ValueError``, so an engine
    event without its row fails in the tests instead of vanishing.
    """

    def __init__(self, dialog):
        """``dialog``: the ``BalBuildWillDialog`` that shows the rows."""
        self._dialog = dialog
        # Step -> index of its row (the old local ``varrow``).
        self._rows = {}

    def step(self, step, state, **data):
        """Write (``"run"``: append) or rewrite the row of ``step``.

        ``Step.VARIABLES`` is the "Checking variables" row; each state makes
        the call ``task_phase1`` made before #120 (dialogs.py:862-1001).
        ``delivery_choice`` (#123) is new: an amber "Choose the delivery
        date".
        """
        d = self._dialog
        if step != Step.VARIABLES:
            raise ValueError(f"unknown step {step!r}")
        if state == "run":
            self._rows[step] = d.msg_set_status(_("Checking variables"))
            return
        row = self._rows.get(step)
        if state == "check_alive_before_delivery":
            # Not translated, on purpose: exactly the call before #120
            # (dialogs.py:866-870). In a language that translates "Checking
            # variables" the step bar does not recognise this label, so the
            # row goes to the problems box as "Checking variables: ..." and
            # the check step stays "running" (_step_of, _report_status).
            # Translating it would change that: an owner decision.
            d.msg_set_status(
                "Checking variables",
                row,
                "Check Alive must be after today and before the delivery date",
                d.COLOR_ERROR,
            )
        elif state == "no_heirs":
            # The alert itself is the status, with no colour argument.
            d.msg_set_status(
                _("Checking variables"), row, d.msg_alert(_("No Heirs"))
            )
        elif state == "ok":
            d.msg_set_status(_("Checking variables"), row, _("Ok"), d.COLOR_OK)
        elif state == "expired":
            d.msg_set_status(
                _("Checking variables"), row, _("Expired"), d.COLOR_ERROR
            )
        elif state == "check_alive_passed":
            d.msg_set_status(
                _("Checking variables"),
                row,
                _("Check Alive passed"),
                d.COLOR_WARNING,
            )
        elif state == "stopped":
            d.msg_set_status(
                _("Checking variables"), row, _("Stopped"), d.COLOR_ERROR
            )
        elif state == "delivery_choice":
            # #123: the "which date?" question is asked next (the dialog
            # does it on the GUI thread, then runs phase 1 again).
            d.msg_set_status(
                _("Checking variables"), row, _(CHOOSE_DATE), d.COLOR_WARNING
            )
        else:
            raise ValueError(f"unknown state {state!r} of step {step!r}")

    def note(self, code, **data):
        """Append a row of its own, as ``task_phase1`` did before #120.

        Errors in red (``msg_error``), warnings after the amber sign
        (``msg_alert``); ``msg_edit_row`` also puts them in the report's
        problems / warnings box. ``delivery_settings_unavailable`` (#123) is
        an error row with :data:`SETTINGS_UNAVAILABLE`;
        ``delivery_rest_cancelled`` (#201) a warning row with
        :data:`REST_CANCELLED`.
        """
        d = self._dialog
        if code == "check_alive_reason":
            d.msg_edit_row(d.msg_error(
                check_alive_message(data["info"], datetext.utc_text)
            ))
        elif code == "check_alive_error":
            d.msg_edit_row(d.msg_error(data["error"]))
        elif code == "fixed_amounts_exceed":
            d.msg_edit_row(d.msg_error(data["text"]))
        elif code == "amounts_explained":
            # #135 (owner): an orange box only when the shares change (or a
            # first will), else a quiet grey line; the dialog decides.
            d._amounts_note(data["text"], data.get("info"))
        elif code == "amounts_adjust":
            d.msg_edit_row(
                d.msg_alert(
                    _(
                        "In the inheritance process, "
                        "the entire wallet will always be fully emptied. \n"
                        "Your settings require an adjustment of the amounts"
                    )
                )
            )
        elif code == "executor_fee_too_high":
            d.msg_edit_row(
                d.msg_alert(
                    _("Will-executor fee too high: {}").format(data["error"])
                )
            )
        elif code == "delivery_settings_unavailable":
            # #123, fail closed: nothing was built, signed or sent.
            d.msg_edit_row(d.msg_error(_(SETTINGS_UNAVAILABLE)))
        elif code == "delivery_rest_cancelled":
            # #201: a state the owner chose, not an error: the amber sign.
            d.msg_edit_row(d.msg_alert(_(REST_CANCELLED).format(data["executed"])))
        else:
            raise ValueError(f"unknown note {code!r}")


class DialogRun:
    """The engine's CheckRun, kept in the dialog's own attributes.

    ``invalidation_broadcast`` is ``dialog._invalidation_broadcast``
    (``invalidate_task`` sets it, ``_on_success_phase1_body`` reads it);
    ``check_alive_info`` is ``dialog._check_alive_info`` (the Renew
    question is worded from it). Every read and write goes straight to the
    dialog at the moment the engine makes it, as the dialog's own code did
    before #120: nothing is copied, so the dialog's other methods always
    see the current value, also when the engine stops with an error.
    """

    def __init__(self, dialog):
        """``dialog``: the ``BalBuildWillDialog`` that holds the state."""
        self._dialog = dialog

    @property
    def invalidation_broadcast(self):
        """``dialog._invalidation_broadcast``."""
        return self._dialog._invalidation_broadcast

    @invalidation_broadcast.setter
    def invalidation_broadcast(self, value):
        """Set ``dialog._invalidation_broadcast``."""
        self._dialog._invalidation_broadcast = value

    @property
    def check_alive_info(self):
        """``dialog._check_alive_info``."""
        return self._dialog._check_alive_info

    @check_alive_info.setter
    def check_alive_info(self, value):
        """Set ``dialog._check_alive_info``."""
        self._dialog._check_alive_info = value
