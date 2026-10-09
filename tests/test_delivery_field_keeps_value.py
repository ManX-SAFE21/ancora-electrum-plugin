"""
The delivery date field never changes the saved date by itself (CHANGELOG #81).

Found in the owner's ZIP test: switching BASIC -> ADVANCED replaced the
delivery date with "1y", and back to BASIC with today + 1 year; building the
widget in ADVANCED did the same, and the heir dialog wrote the heir's date
into the will's setting. Now:

* the editor follows the form of the saved value, nothing is saved when the
  widget is built, when the mode changes or when another panel syncs it;
* an explicit Raw/Date switch saves the SAME delivery written the other way;
* ``save_to_settings=False`` (heir dialog) never writes the will's setting;
* core: a will built from a fixed date + a setting that is now a period
  giving the same date today is rebuilt now (a period cannot be anchored to
  a fixed-date will: the next day it read as a postpone, a paid
  invalidation).

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


# ------------------------------------------------------------ building it

def test_advanced_fixed_date_opens_the_date_editor_unchanged():
    """Before #81: Raw editor with "1y", saved -> the date moved."""
    bw, w = _field(D)
    assert w.current_index == 1 and w.get_value() == D
    bw.update_setting_widgets.assert_not_called()


def test_advanced_period_opens_the_raw_editor_unchanged():
    bw, w = _field("4y")
    assert w.current_index == 0 and w.get_value() == "4y"
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


def test_switch_to_advanced_keeps_a_period():
    bw, w = _field("4y", basic=True)
    bw.basic = False
    w.apply_user_type_editor_default()
    assert w.current_index == 0 and w.get_value() == "4y"
    bw.update_setting_widgets.assert_not_called()


# --------------------------------------------------- explicit Raw/Date switch

def test_user_switch_raw_to_date_saves_the_same_date():
    bw, w = _field("1y")
    w.combo.setCurrentIndex(1)          # what a click on "Date" does
    assert w.get_value() == TODAY + 365 * DAY
    bw.update_setting_widgets.assert_called_once()
    assert bw.update_setting_widgets.call_args.args[:2] == (TODAY + 365 * DAY, "locktime")


def test_user_switch_date_to_raw_saves_the_days_to_the_same_date():
    bw, w = _field(D)
    w.combo.setCurrentIndex(0)          # what a click on "Raw" does
    assert w.get_value() == "400d"
    assert bw.update_setting_widgets.call_args.args[:2] == ("400d", "locktime")


# -------------------------------------------------------- sync and heir dialog

def test_set_value_shows_but_never_saves():
    bw, w = _field("4y")
    w.set_value("5y")
    assert w.get_value() == "5y" and w.current_value == "5y"
    bw.update_setting_widgets.assert_not_called()


def test_heir_field_never_writes_the_will_setting():
    bw, w = _field("4y", save_to_settings=False)
    w.locktime_raw_e.set_value("2y")
    w.valueEdited.emit()                # the user finished editing
    assert w.current_value == "2y"
    bw.update_setting_widgets.assert_not_called()


def test_normal_edit_is_saved():
    bw, w = _field("4y")
    w.locktime_raw_e.set_value("2y")
    w.valueEdited.emit()
    assert bw.update_setting_widgets.call_args.args[:2] == ("2y", "locktime")


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
