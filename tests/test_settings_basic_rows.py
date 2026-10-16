"""
Tests for #151: the plugin settings dialog without "Panel editable Date and
Fee", and the red warning in ADVANCED only.

Covers:
  - Neither BASIC nor ADVANCED has the "Panel editable Date and Fee" row.
  - The red warning is hidden in BASIC and shown in ADVANCED.
  - "User Type" is the first row of the grid in both modes.
  - ``WillSettingsWidget.apply_editable_dates`` follows the mode only: the
    date and fee fields are read-only in BASIC and editable in ADVANCED,
    whatever ``bal_editable_dates`` value is stored (it is never rewritten).

Run:
    source "$BAL_HOME/electrum/env/bin/activate"
    QT_QPA_PLATFORM=offscreen python3 tests/test_settings_basic_rows.py
"""

import sys
import tempfile
import types

sys.path.insert(0, __file__.rsplit("/", 2)[0])

from unittest.mock import patch

from PyQt6.QtWidgets import QApplication, QGridLayout, QLabel

_app = QApplication.instance() or QApplication(sys.argv)

from electrum.simple_config import SimpleConfig

import bal.gui.qt.plugin as plugin_mod
from bal.gui.qt.plugin import Plugin
from bal.gui.qt.widgets import WillSettingsWidget

GONE_LABEL = "Panel editable Date and Fee"
WARNING = "Warning: change these settings only if you know what you are doing."


def _plugin(user_type):
    """A real Plugin on an isolated config, with the GUI-only calls stubbed."""
    cfg = SimpleConfig({"electrum_path": tempfile.mkdtemp(prefix="bal_test_")})
    plugin = Plugin(None, cfg, "bal")
    plugin.get_window_title = lambda s: s
    plugin.read_file = lambda *a: b""
    plugin.broadcast_transactions = lambda *a, **k: None
    plugin.update_all = lambda *a, **k: None
    plugin.USER_TYPE.set(user_type)
    return plugin


def _build_dialog(plugin):
    """Build the settings dialog of *plugin* without showing it modally."""
    captured = []

    def fake_show_modal(dlg):
        """Capture the dialog instead of running its event loop."""
        captured.append(dlg)
        return True

    with patch.object(plugin_mod, "show_modal", side_effect=fake_show_modal):
        plugin.settings_dialog(None, None)
    assert captured, "settings_dialog did not build a dialog"
    return captured[0]


def _labels(dialog, text):
    """The QLabels of *dialog* whose text is *text*."""
    return [w for w in dialog.findChildren(QLabel) if w.text() == text]


def test_no_editable_dates_row():
    """Neither mode has the "Panel editable Date and Fee" row."""
    for user_type in ("basic", "advanced"):
        dialog = _build_dialog(_plugin(user_type))
        assert _labels(dialog, GONE_LABEL) == [], user_type
        dialog.close()


def test_warning_advanced_only():
    """The red warning is hidden in BASIC and shown in ADVANCED."""
    basic = _build_dialog(_plugin("basic"))
    [label] = _labels(basic, WARNING)
    assert label.isHidden() is True
    basic.close()
    advanced = _build_dialog(_plugin("advanced"))
    [label] = _labels(advanced, WARNING)
    assert label.isHidden() is False
    advanced.close()


def test_user_type_first_row():
    """The "User Type" row comes before every other row, in both modes."""
    for user_type in ("basic", "advanced"):
        dialog = _build_dialog(_plugin(user_type))
        [grid] = dialog.findChildren(QGridLayout)
        rows = {}
        for i in range(grid.count()):
            widget = grid.itemAt(i).widget()
            if widget is None or widget.isHidden():
                continue
            rows[widget] = grid.getItemPosition(i)[0]
        [label] = _labels(dialog, "User Type")
        assert rows[label] == min(rows.values()), user_type
        assert sum(1 for r in rows.values() if r == rows[label]) <= 4
        dialog.close()


class _Field:
    """A date or fee field that records its read-only state."""

    def __init__(self):
        self.read_only = None

    def set_read_only(self, read_only=True):
        """Record the requested state."""
        self.read_only = read_only


def _apply(user_type, stored):
    """Run the real apply_editable_dates on a fake toolbar widget."""
    cfg = SimpleConfig({"electrum_path": tempfile.mkdtemp(prefix="bal_test_")})
    plugin = Plugin(None, cfg, "bal")
    plugin.USER_TYPE.set(user_type)
    plugin.EDITABLE_DATES.set(stored)
    fake = types.SimpleNamespace(
        read_only=True,
        bal_window=types.SimpleNamespace(bal_plugin=plugin),
        widgets={k: _Field() for k in ("locktime", "threshold", "baltx_fees")},
    )
    WillSettingsWidget.apply_editable_dates(fake)
    assert plugin.EDITABLE_DATES.get() is stored
    return {k: f.read_only for k, f in fake.widgets.items()}


def test_fields_follow_the_mode_only():
    """BASIC: read-only; ADVANCED: editable; the stored value is ignored."""
    for stored in (True, False):
        assert set(_apply("basic", stored).values()) == {True}, stored
        assert set(_apply("advanced", stored).values()) == {False}, stored


def test_wizard_fields_untouched():
    """Inside the wizard (read_only=False) nothing is changed."""
    fake = types.SimpleNamespace(
        read_only=False,
        widgets={k: _Field() for k in ("locktime", "threshold", "baltx_fees")},
    )
    WillSettingsWidget.apply_editable_dates(fake)
    assert {f.read_only for f in fake.widgets.values()} == {None}


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
