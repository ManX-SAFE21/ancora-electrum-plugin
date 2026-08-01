"""
Tests for the "Save inheritance transactions in history" settings dialog rows.

Covers:
  - The history label line-edit is present and bound to the HISTORY_LABEL
    config (its text is the configured label).
  - The rows are hidden in BASIC mode and visible in ADVANCED mode (advanced
   -only settings, mirroring the other advanced rows).
  - The history label line-edit is disabled while the "Save inheritance
    transactions in history" checkbox is off, and re-enabled when it is on.

Run:
    source "$BAL_HOME/electrum/env/bin/activate"
    QT_QPA_PLATFORM=offscreen python3 tests/test_settings_history_dialog.py
"""

import sys
import tempfile

sys.path.insert(0, __file__.rsplit("/", 2)[0])

from unittest.mock import patch

from PyQt6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication(sys.argv)

from electrum.simple_config import SimpleConfig

import bal.gui.qt.plugin as plugin_mod
from bal.gui.qt.plugin import Plugin
from bal.gui.qt.widgets import BalCheckBox, BalLineEdit

DEFAULT_LABEL = "BitcoinAfterLife inheritance transaction - {willexecutor}"


def _isolated_config(**overrides):
    """An in-memory SimpleConfig that never touches the real Electrum config.

    A fresh ``electrum_path`` temp dir keeps every write isolated, so running
    the tests cannot pollute the user's config files.
    """
    opts = {"electrum_path": tempfile.mkdtemp(prefix="bal_test_")}
    opts.update(overrides)
    return SimpleConfig(opts)


def _build_dialog(user_type, **config_overrides):
    """Build the plugin settings dialog for *user_type* and return it.

    ``settings_dialog`` ends with a blocking ``show_modal(d)`` call; we patch it
    to capture the dialog and return immediately.
    """
    cfg = _isolated_config(**config_overrides)
    plugin = Plugin(None, cfg, "bal")
    plugin.get_window_title = lambda s: s
    plugin.read_file = lambda *a: b""
    plugin.broadcast_transactions = lambda *a, **k: None
    plugin.update_all = lambda *a, **k: None
    plugin.USER_TYPE.set(user_type)

    captured = []

    def fake_show_modal(dlg):
        captured.append(dlg)
        return True

    with patch.object(plugin_mod, "show_modal", side_effect=fake_show_modal):
        plugin.settings_dialog(None, None)
    assert captured, "settings_dialog did not build a dialog"
    return plugin, captured[0]


def _history_label_edit(dialog):
    edits = [
        w for w in dialog.findChildren(BalLineEdit) if w.text() == DEFAULT_LABEL
    ]
    assert len(edits) == 1, f"expected exactly one history label edit, got {len(edits)}"
    return edits[0]


def test_history_label_row_hidden_in_basic_visible_in_advanced():
    plugin, basic = _build_dialog("basic")
    assert _history_label_edit(basic).isHidden() is True
    basic.close()

    plugin, advanced = _build_dialog("advanced")
    edit = _history_label_edit(advanced)
    assert edit.isHidden() is False
    advanced.close()


def test_history_label_field_follows_checkbox():
    # Default: SAVE_HISTORY is ON, so the label field starts enabled.
    plugin, dialog = _build_dialog("advanced")
    edit = _history_label_edit(dialog)
    assert edit.isEnabled() is True

    # Find the checkbox that controls the label field's enabled state: it must
    # be the "Save inheritance transactions in history" checkbox (the only one
    # whose off-state disables the label field).
    toggler = None
    for box in dialog.findChildren(BalCheckBox):
        if not box.isChecked():
            continue
        box.setChecked(False)
        if not edit.isEnabled():
            toggler = box
            break
    assert toggler is not None, "no checkbox disables the history label field"

    # Toggling it back on re-enables the field.
    toggler.setChecked(True)
    assert edit.isEnabled() is True
    assert plugin.SAVE_HISTORY.get() is True
    dialog.close()


def test_history_label_field_disabled_from_start_when_off():
    plugin, dialog = _build_dialog(
        "advanced", **{"bal_save_history": False}
    )
    assert _history_label_edit(dialog).isEnabled() is False
    dialog.close()


# ------------------------------------------------------------------ #
# Main
# ------------------------------------------------------------------ #

if __name__ == "__main__":
    for name in sorted(dir()):
        if name.startswith("test_"):
            globals()[name]()
            print(f"  [OK] {name}")
    print("[OK] All settings-history dialog tests passed")
