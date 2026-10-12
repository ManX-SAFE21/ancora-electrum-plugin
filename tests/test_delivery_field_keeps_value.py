"""
The delivery date field never changes the saved date by itself (CHANGELOG #81).

Found in the owner's ZIP test: switching BASIC -> ADVANCED replaced the
delivery date with "1y", and back to BASIC with today + 1 year; building the
widget in ADVANCED did the same, and the heir dialog wrote the heir's date
into the will's setting. Now:

* nothing is saved when the widget is built, when the mode changes or when
  another panel syncs it;
* ``save_to_settings=False`` (heir dialog) never writes the will's setting;
* core: a will built from a fixed date + a setting that is now a period
  giving the same date today is rebuilt now (a period cannot be anchored to
  a fixed-date will: the next day it read as a postpone, a paid
  invalidation).

CHANGELOG #123: a period is no longer a delivery date. The Raw/Date selector
is hidden in BOTH modes and the Date editor is always used; a period still
found in older settings is shown as the date it gives (nothing is saved).
The explicit Raw/Date switch of #81 is gone with the selector; the Raw code
stays until #127, and a period it could still produce is refused by the
window's validated write (``context.set_will_setting``): the stored date and
the fields stay as they were.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \
        python3 tests/test_delivery_field_keeps_value.py
"""

import os
import sys
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from electrum.transaction import Transaction
from PyQt6.QtWidgets import QApplication

from bal.core.plugin_base import BalPlugin
from bal.core.will import HeirNotFoundException, Will, WillItem
from bal.gui.qt.widgets import LockTimeWidget
from bal.gui.qt.window import BalWindow

_app = QApplication.instance() or QApplication(sys.argv)

DAY = 86400
TODAY = int(datetime.now(tz=timezone.utc).replace(
    hour=0, minute=0, second=0, microsecond=0).timestamp())
D = TODAY + 400 * DAY            # a fixed delivery date (midnight UTC)

# WillItem parses a raw tx and calls add_info_from_wallet; no live wallet here.
_patcher = patch.object(Transaction, "add_info_from_wallet")
_patcher.start()

_TX_HEX = (
    "01000000012a5c9a94fcde98f5581cd00162c60a13936ceb75389ea65b"
    "f38633b424eb4031000000006c493046022100a82bbc57a0136751e543"
    "3f41cf000b3f1a99c6744775e76ec764fb78c54ee100022100f9e80b7d"
    "e89de861dc6fb0c1429d5da72c2b6b2ee2406bc9bfb1beedd729d98501"
    "2102e61d176da16edd1d258a200ad9759ef63adf8e14cd97f53227bae3"
    "5cdb84d2f6ffffffff0140420f00000000001976a914230ac37834073a"
    "42146f11ef8414ae929feaafc388ac00000000"
)


class _Window:
    """Just what the field uses from BalWindow."""

    def __init__(self, locktime, basic=False):
        self.basic = basic
        self.will_settings = {"locktime": locktime}
        self.willitems = {}
        self.update_setting_widgets = MagicMock()
        self.update_combo_setting_widgets = MagicMock()
        self.bal_plugin = SimpleNamespace(
            is_basic_mode=lambda: self.basic,
            WILL_SETTINGS=SimpleNamespace(get=lambda: dict(self.will_settings)),
            default_will_settings=BalPlugin.default_will_settings,
            default_will_settings_relative=BalPlugin.default_will_settings_relative,
            default_will_settings_absolute=BalPlugin.default_will_settings_absolute,
        )


def _field(locktime, basic=False, **kw):
    bw = _Window(locktime, basic)
    return bw, LockTimeWidget(bw, None, locktime, **kw)


def _real_window(locktime, basic=False):
    """A ``BalWindow`` without Qt tabs whose ``update_setting_widgets`` is
    the real one (#123: the validated write into ``will_settings``).

    The two toolbars are stand-ins; ``update_widget_value`` records what
    each field is told to show.
    """
    bw = object.__new__(BalWindow)
    bw.will_settings = {"locktime": locktime}
    bw.willitems = {}
    bw.heir_list_widget = SimpleNamespace(name="heirs toolbar")
    bw.will_list_widget = SimpleNamespace(name="will toolbar")
    bw.update_widget_value = MagicMock()
    bw.update_combo_setting_widgets = MagicMock()
    bw.bal_plugin = SimpleNamespace(
        is_basic_mode=lambda: basic,
        default_will_settings=BalPlugin.default_will_settings,
        default_will_settings_relative=BalPlugin.default_will_settings_relative,
        default_will_settings_absolute=BalPlugin.default_will_settings_absolute,
    )
    return bw


# ------------------------------------------------------------ building it

def test_advanced_fixed_date_opens_the_date_editor_unchanged():
    """Before #81: Raw editor with "1y", saved -> the date moved."""
    bw, w = _field(D)
    assert w.current_index == 1 and w.get_value() == D
    bw.update_setting_widgets.assert_not_called()


def test_advanced_period_opens_the_date_editor_unchanged():
    """#123: ADVANCED shows a stored period in the Date editor too (before:
    the Raw editor with "4y"), as the date it gives; nothing is saved."""
    bw, w = _field("4y")
    assert w.current_index == 1
    assert w.get_value() == TODAY + 4 * 365 * DAY
    assert w.current_value == "4y"
    bw.update_setting_widgets.assert_not_called()


def test_basic_period_is_shown_as_its_date_without_saving():
    bw, w = _field("1y", basic=True)
    assert w.current_index == 1
    assert w.get_value() == TODAY + 365 * DAY
    bw.update_setting_widgets.assert_not_called()


# ------------------------------------------------------------ mode switch

def test_switch_to_basic_and_back_keeps_a_fixed_date():
    bw, w = _field(D)
    bw.basic = True
    w.apply_user_type_editor_default()
    assert w.current_index == 1 and w.get_value() == D
    bw.basic = False
    w.apply_user_type_editor_default()
    assert w.current_index == 1 and w.get_value() == D
    bw.update_setting_widgets.assert_not_called()


def test_switch_to_advanced_keeps_the_date_editor():
    """#123: BASIC -> ADVANCED keeps the Date editor (before: the Raw
    editor for a period); the shown date and the stored value stay."""
    bw, w = _field("4y", basic=True)
    bw.basic = False
    w.apply_user_type_editor_default()
    w.apply_user_type_visibility()
    assert w.current_index == 1
    assert w.get_value() == TODAY + 4 * 365 * DAY
    assert w.current_value == "4y"
    bw.update_setting_widgets.assert_not_called()


# ---------------------------------------------- the Raw/Date selector (#123)

def test_the_raw_date_selector_is_hidden_in_both_modes():
    """#123 replaces the explicit Raw/Date switch of #81: the selector is
    hidden in BASIC and ADVANCED, also after a mode change, so a period
    cannot be typed as the delivery date."""
    for basic in (True, False):
        for value in (D, "4y"):
            bw, w = _field(value, basic=basic)
            assert w.combo.isHidden(), (basic, value)
            bw.basic = not basic
            w.apply_user_type_visibility()
            w.apply_user_type_editor_default()
            assert w.combo.isHidden(), (basic, value)
            assert w.current_index == 1, (basic, value)
            bw.update_setting_widgets.assert_not_called()


def test_a_period_from_the_raw_code_is_refused():
    """The Raw code stays until #127; a period it still produces (here the
    hidden selector switched by code; before #123 a click on "Raw" saved
    "400d") is refused by the window's validated write: the stored date
    stays and both toolbars are told to show it again."""
    bw = _real_window(D)
    w = LockTimeWidget(bw, None, D)
    w.combo.setCurrentIndex(0)
    assert w.get_value() == "400d"
    assert bw.will_settings == {"locktime": D}
    shown = [c.args[1:] for c in bw.update_widget_value.call_args_list[-2:]]
    assert shown == [("locktime", D), ("locktime", D)]


# -------------------------------------------------------- sync and heir dialog

def test_set_value_shows_but_never_saves():
    """Another panel syncs the field: shown, never saved (#123: a date;
    before, a period)."""
    bw, w = _field(D)
    w.set_value(D + 30 * DAY)
    assert w.get_value() == D + 30 * DAY and w.current_value == D + 30 * DAY
    bw.update_setting_widgets.assert_not_called()


def test_heir_field_never_writes_the_will_setting():
    """The heir dialog's field (``save_to_settings=False``): a date picked
    there never reaches the will's setting (#123: in the Date editor;
    before, a period typed in the Raw editor)."""
    bw, w = _field(D, save_to_settings=False)
    w.locktime_date_e.set_value(D + 30 * DAY)    # the user picks a date
    assert w.current_value == D + 30 * DAY
    bw.update_setting_widgets.assert_not_called()


def test_normal_edit_is_saved():
    """A date picked in the Date editor is stored by the window (#123: in
    its own settings, through the validated write)."""
    bw = _real_window(D)
    w = LockTimeWidget(bw, None, D)
    w.locktime_date_e.set_value(D + 30 * DAY)    # the user picks a date
    assert w.current_value == D + 30 * DAY
    assert bw.will_settings == {"locktime": D + 30 * DAY}


# ------------------------------------------------------------------- core

def _will(recipe, locktime):
    item = WillItem({
        "tx": _TX_HEX[:-8] + locktime.to_bytes(4, "little").hex(),
        "heirs": {"alice": ["tb1qalice", "100%", recipe]},
        "willexecutor": None,
        "baltx_fees": 13,
    })
    item.set_status("COMPLETE", True)
    item.set_status("PUSHED", True)
    return {item._id: item}


def _check(will, recipe):
    return Will.check_willexecutors_and_heirs(
        will, {"alice": ["tb1qalice", "100%", recipe]}, {}, False, 0, 13)


def test_fixed_date_will_with_an_equal_period_is_rebuilt_now():
    try:
        _check(_will(D, D), "400d")
    except HeirNotFoundException as e:
        assert e.change == HeirNotFoundException.HEIR_DATE
    else:
        raise AssertionError("expected a rebuild (HeirNotFoundException)")


def test_period_will_with_the_same_period_is_coherent():
    assert _check(_will("400d", D), "400d") is True


def test_fixed_date_will_with_the_same_date_is_coherent():
    assert _check(_will(D, D), D) is True


if __name__ == "__main__":
    for name in sorted(dir()):
        if name.startswith("test_"):
            globals()[name]()
            print(f"  [OK] {name}")
    print("[OK] All delivery-field tests passed")
