"""The Check dialog's side of the engine's events (CHANGELOG #120).

``bal.gui.qt.flow_adapter.DialogFlowUI`` turns each event of
``bal.engine.check.check_variables`` into the ``msg_*`` call the dialog
made before #120; ``DialogRun`` keeps the engine's run state in the
dialog's own attributes. Here each event is checked to write today's exact
row (text, colour, row, translation) and to feed the report the same way,
and every event the engine declares is checked to have a row.

The real dialog class runs without Qt set-up (like the scenario harness);
every row ever written is recorded, as in ``tests/flowkit.py``.

Run:
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_gui_flow_adapter.py -q
"""

import dataclasses
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from PyQt6.QtWidgets import QApplication

import bal.i18n
from bal.core import datetext
from bal.core.checkalive import check_alive_renewal
from bal.core.messages import check_alive_message
from bal.engine import check
from bal.engine.ui import CheckRun, Step
from bal.gui.qt.dialogs import BalBuildWillDialog
from bal.gui.qt.flow_adapter import (
    CHOOSE_DATE,
    REST_CANCELLED,
    SETTINGS_UNAVAILABLE,
    DialogFlowUI,
    DialogRun,
)

# Kept at module level: an unreferenced application object is destroyed at
# once, and creating a widget afterwards aborts Python.
_APP = QApplication.instance() or QApplication(sys.argv)

DAY = 86400
V = Step.VARIABLES
WAIT = "Checking variables:\t<b>Wait</b>"
AFTER_DELIVERY_TEXT = "Check Alive must be after today and before the delivery date"
ADJUST = (
    "In the inheritance process, the entire wallet will always be fully "
    "emptied. \nYour settings require an adjustment of the amounts"
)


def _renewal(locktime):
    """A complete passed-Check-Alive information (the sentences read every
    field): a will in force delivered in 20 days, Check Alive 180 days
    before."""
    now = 1_800_000_000 - 1_800_000_000 % DAY
    return check_alive_renewal(
        False, {"locktime": locktime, "threshold": "-180d"},
        built_locktime=now + 20 * DAY, creation_date=now - 300 * DAY, now=now,
    )


class _Dialog(BalBuildWillDialog):
    """The real dialog, recording every row ever written."""

    def msg_edit_row(self, line, row=None):
        """Record the row, then write it as the real dialog does."""
        self.history.append(line)
        return super().msg_edit_row(line, row)


def _dialog():
    """A dialog without Qt set-up (attributes of ``test_build_report``)."""
    d = _Dialog.__new__(_Dialog)
    d.history = []
    d.labels = []
    d.updatemessage = MagicMock()
    d.check_row = d.inval_row = d.build_row = d.sign_row = d.push_row = None
    d._invalidation_broadcast = False
    d._check_alive_info = None
    d.bal_window = MagicMock()
    d.bal_window.willitems = {}
    d.bal_window.bal_plugin.is_basic_mode.return_value = False
    d.bal_window._still_active_for_summary.return_value = []
    return d


def _report(d):
    """The report's Check step state and its non-empty boxes."""
    snap = d._report().snapshot()
    steps = {s: state for s, state, _note in snap["steps"]}
    return steps["check"], {k: v for k, v in snap["boxes"].items() if v}


# state -> (row, report's Check step, boxes), as the dialog wrote them
# before #120 (golden files of #112 for the ones they reach).
STATES = {
    "ok": ("Checking variables:\t<font color=#05ad05><b>Ok</b></font>",
           "ok", {}),
    "expired": ("Checking variables:\t<font color=#ff0000><b>Expired</b></font>",
                "err", {"problems": ["Expired"]}),
    "check_alive_passed": (
        "Checking variables:\t<font color=#cfa808><b>Check Alive passed</b></font>",
        "warn", {"warnings": ["Check Alive passed"]}),
    "stopped": ("Checking variables:\t<font color=#ff0000><b>Stopped</b></font>",
                "err", {}),
    # #204: a stop the owner chose: amber, and the box does not repeat the word.
    "held": ("Checking variables:	<font color=#cfa808><b>Stopped</b></font>",
             "warn", {}),
    "no_heirs": ("Checking variables:\t<b><font color='#cfa808'>&#9888;</font> "
                 "<b>No Heirs</b></b>", "warn", {"warnings": ["No Heirs"]}),
    "check_alive_before_delivery": (
        "Checking variables:\t<font color=#ff0000><b>{}</b></font>".format(
            AFTER_DELIVERY_TEXT),
        "err", {"problems": [AFTER_DELIVERY_TEXT]}),
    # CHANGELOG #123: the "which date?" question is asked next (amber).
    "delivery_choice": (
        "Checking variables:\t<font color=#cfa808><b>{}</b></font>".format(
            CHOOSE_DATE),
        "warn", {"warnings": [CHOOSE_DATE]}),
}


@pytest.mark.parametrize("state", sorted(STATES))
def test_each_state_writes_todays_row(state):
    """After "run", each outcome rewrites the same row with today's text
    and colour, and feeds the report as before."""
    row, step_state, boxes = STATES[state]
    d = _dialog()
    ui = DialogFlowUI(d)
    ui.step(V, "run")
    assert d.labels == [WAIT]
    ui.step(V, state)
    assert d.labels == [row]
    assert d.history == [WAIT, row]
    assert _report(d) == (step_state, boxes)


def _notes():
    """note code -> (data, row text, box) as the dialog wrote them."""
    info = _renewal("200d")
    return {
        "check_alive_reason": ({"info": info},
                               # #134: the GUI writes the dates in the one
                               # long style (bal.core.datetext).
                               ("error", check_alive_message(info, datetext.utc_text)),
                               "problems"),
        "check_alive_error": ({"error": "Check alive expired"},
                              ("error", "Check alive expired"), "problems"),
        "fixed_amounts_exceed": ({"text": "too much", "info": {}},
                                 ("error", "too much"), "problems"),
        "amounts_explained": ({"text": "80%", "info": {}},
                              ("alert", "80%"), "warnings"),
        "amounts_adjust": ({}, ("alert", ADJUST), "warnings"),
        "executor_fee_too_high": ({"error": "E: we1 fee:50000 > max:5000"},
                                  ("alert", "Will-executor fee too high: "
                                            "E: we1 fee:50000 > max:5000"),
                                  "warnings"),
        # CHANGELOG #123, fail closed: no data, the text is the adapter's.
        "delivery_settings_unavailable": ({}, ("error", SETTINGS_UNAVAILABLE),
                                          "problems"),
        # #201: a state the owner chose, not an error: the amber sign.
        "delivery_rest_cancelled": ({"executed": 2},
                                    ("alert", REST_CANCELLED.format(2)),
                                    "warnings"),
    }


@pytest.mark.parametrize("code", sorted(check.VARIABLES_NOTES))
def test_each_note_writes_its_own_row(code):
    """A note is a row of its own after the step row: errors in red,
    warnings after the amber sign, each in its report box."""
    data, (kind, text), box = _notes()[code]
    d = _dialog()
    ui = DialogFlowUI(d)
    ui.step(V, "run")
    ui.step(V, "ok")
    ui.note(code, **data)
    if kind == "error":
        row = "<font color='#ff0000'><b>{}</b></font>".format(text)
    else:
        row = "<font color='#cfa808'>&#9888;</font> <b>{}</b>".format(text)
    assert d.labels[1:] == [row]
    assert d.history[2:] == [row]
    if code == "amounts_explained":
        # #135 (owner): not an "Attention" box: the report's shares note.
        assert _report(d)[1] == {}
        assert d._report().snapshot()["amounts_note"][0]
    else:
        assert _report(d)[1] == {box: [text]}


def test_each_pass_gets_its_own_row():
    """A new adapter per pass: the second pass appends its own row."""
    d = _dialog()
    for _ in range(2):
        ui = DialogFlowUI(d)
        ui.step(V, "run")
        ui.step(V, "ok")
    ok = STATES["ok"][0]
    assert d.labels == [ok, ok]
    assert d.history == [WAIT, ok, WAIT, ok]


def test_texts_are_translated_but_the_after_delivery_row():
    """Labels and statuses are translated when written; the after-delivery
    row stays English, so the step bar does not recognise it in another
    language: the Check step stays running and the whole row goes to the
    problems box (as before #120)."""
    with patch.object(bal.i18n, "_electrum_gettext", lambda m: "T:" + m):
        d = _dialog()
        ui = DialogFlowUI(d)
        ui.step(V, "run")
        ui.step(V, "expired")
        assert d.history == [
            "T:Checking variables:\t<b>T:Wait</b>",
            "T:Checking variables:\t<font color=#ff0000><b>T:Expired</b></font>",
        ]
        d = _dialog()
        ui = DialogFlowUI(d)
        ui.step(V, "run")
        ui.step(V, "check_alive_before_delivery")
        assert d.labels == [STATES["check_alive_before_delivery"][0]]
        assert _report(d) == ("run", {"problems": [
            "Checking variables: " + AFTER_DELIVERY_TEXT]})
        d = _dialog()
        DialogFlowUI(d).note("amounts_adjust")
        assert d.labels == ["<font color='#cfa808'>&#9888;</font> <b>T:{}</b>".format(
            ADJUST)]


def test_unknown_events_raise():
    """An event without its row fails loudly instead of vanishing."""
    ui = DialogFlowUI(_dialog())
    with pytest.raises(ValueError):
        ui.step("other", "run")
    with pytest.raises(ValueError):
        ui.step(V, "other")
    with pytest.raises(ValueError):
        ui.note("other")


def test_every_engine_event_has_a_row():
    """Every state and note the engine declares writes a row."""
    notes = _notes()
    assert set(notes) == set(check.VARIABLES_NOTES)
    assert check.VARIABLES_STATES[0] == "run"
    assert set(check.VARIABLES_STATES[1:]) == set(STATES)
    for state in check.VARIABLES_STATES[1:]:
        d = _dialog()
        ui = DialogFlowUI(d)
        ui.step(V, "run")
        ui.step(V, state)
        assert d.labels == [STATES[state][0]]
    for code in check.VARIABLES_NOTES:
        d = _dialog()
        DialogFlowUI(d).note(code, **notes[code][0])
        assert len(d.labels) == 1


def test_dialog_run_reads_and_writes_the_dialog():
    """``DialogRun`` reads and writes the dialog's attributes at once."""
    d = _dialog()
    run = DialogRun(d)
    info = object()
    run.check_alive_info = info
    assert d._check_alive_info is info
    assert run.invalidation_broadcast is False
    d._invalidation_broadcast = True
    assert run.invalidation_broadcast is True
    run.invalidation_broadcast = False
    assert d._invalidation_broadcast is False


def test_dialog_run_has_the_check_run_fields():
    """``DialogRun`` maps exactly the fields of the engine's ``CheckRun``."""
    props = {n for n, v in vars(DialogRun).items() if isinstance(v, property)}
    assert props == {f.name for f in dataclasses.fields(CheckRun)}
