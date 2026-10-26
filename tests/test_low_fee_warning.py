"""The warning for a miner fee below 20 sat/vB (#192).

Owner decision (multiverse plan, section 4; text of 2026-10-26): lowering
the miner fee is the user's choice; years later, on the delivery date, a
busy network may not accept a transaction with a low fee. A WARNING only,
never a block, for every will (also a fee stored before #192): an orange
sign next to the fee field (toolbar and wizard, the same widget) with the
text in its tooltip, an orange line in the Check window, a sentence in the
answer of ``bal_delivery_set --fee``.

``tests/flowkit.py`` wallets with three fake will-executors, each with its
own fee address, the test password, a fixed clock.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_low_fee_warning.py
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

import flowkit as fk
from PyQt6.QtWidgets import QApplication
from test_gui_window_init_migration import real_window, toolbars

from bal.core.messages import LOW_FEE_LIMIT, low_fee_warning
from bal.engine import context
from bal.gui.qt.widgets import WillSettingsWidget

# Kept at module level: an unreferenced application object is destroyed at
# once, and creating a widget afterwards aborts Python.
_APP = QApplication.instance() or QApplication(sys.argv)

# Test value: the password of the throw-away test wallets.
TEST_PASSWORD = "flowkit-test"

WARNING = (
    "Miner fee below 20 sat/vB: it is your choice. On the delivery date of "
    "your will the Bitcoin network may be very busy, and the nodes may not "
    "accept a transaction with a low fee. With 20 sat/vB or more this risk "
    "is much smaller and the inheritance is more likely to be executed on "
    "time, without delays."
)


@pytest.fixture
def make(monkeypatch):
    """flowkit sessions: ADVANCED, three will-executors, synchronized."""
    fk.ensure_event_loop()
    made = []

    def _make(fee):
        """One session with the miner fee ``fee`` (sat/vB)."""
        s = fk.Session(monkeypatch, mode="advanced", password=TEST_PASSWORD,
                       fee=fee)
        s.world.add_executor()
        monkeypatch.setattr(s.wallet, "is_up_to_date", lambda: True)
        made.append(s)
        return s

    yield _make
    for s in made:
        s.close()
    fk.stop_event_loop()


def returned(result):
    """What the command returned (``Session.cli`` wraps it)."""
    assert "returned" in result, result
    return result["returned"]


# ------------------------------------------------------------ the rule

def test_the_rule_and_the_text():
    """Below 20: the owner's text; 20 or more, or not a number: nothing."""
    assert LOW_FEE_LIMIT == 20
    assert low_fee_warning(19) == WARNING
    assert low_fee_warning(19.5) == WARNING
    assert low_fee_warning("1") == WARNING
    assert low_fee_warning(20) == ""
    assert low_fee_warning("21") == ""
    assert low_fee_warning(None) == ""
    assert low_fee_warning("abc") == ""


# ------------------------------------------------------------ the field

def test_the_fee_field_shows_the_sign_below_20(make, monkeypatch):
    """A fee stored before (13): the sign in both toolbars, the text in its
    tooltip; 20 typed: gone; 19 typed: back, and the 19 is stored (never
    refused)."""
    s = make(13)
    bw = real_window(s, monkeypatch)
    for toolbar in toolbars(bw):
        fee = toolbar.widgets["baltx_fees"]
        assert not fee.warning.isHidden()
        assert fee.warning.toolTip() == WARNING
    fee = toolbars(bw)[1].widgets["baltx_fees"]
    fee.txfee_widget.setValue(20)
    assert fee.warning.isHidden() and fee.warning.toolTip() == ""
    fee.txfee_widget.setValue(19)
    assert not fee.warning.isHidden()
    assert bw.will_settings["baltx_fees"] == 19


def test_the_wizard_page_shows_it_too(make, monkeypatch):
    """The wizard's settings page uses the same field: the sign there too."""
    s = make(5)
    bw = real_window(s, monkeypatch)
    fee = WillSettingsWidget(bw, None, "v", read_only=False).widgets["baltx_fees"]
    assert not fee.warning.isHidden() and fee.warning.toolTip() == WARNING


# ------------------------------------------------------------ the Check

def test_the_check_window_warns_and_the_will_is_made(make):
    """Fee 13: the Check window has the warning, and the will is signed and
    sent to the three will-executors all the same; fee 20: no warning."""
    s = make(13)
    snap = s.check()._report().snapshot()
    assert snap["low_fee"] == WARNING
    assert {u for u, _w, _r in s.world.pushed} == set(s.world.executors)
    context.set_will_setting(s.bw, "baltx_fees", 20)
    snap = s.check()._report().snapshot()
    assert "low_fee" not in snap


# ------------------------------------------------------------ the command line

def test_the_command_line_stores_the_fee_and_warns(make):
    """``bal_delivery_set --fee 19``: stored, with the warning; 20: no
    warning."""
    s = make(20)
    out = returned(s.cli("delivery_set", fee=19))
    assert out["baltx_fees"] == 19
    assert WARNING in out["message"]
    out = returned(s.cli("delivery_set", fee=20))
    assert out["baltx_fees"] == 20
    assert WARNING not in out["message"]
