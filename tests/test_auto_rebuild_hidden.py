"""
Tests for #149: the "Rebuild automatically on new transactions" setting is
hidden and the flow it started is switched off.

Covers:
  - The plugin settings dialog (BASIC and ADVANCED) has no row for it, while
    the "Rebuild will on wallet close" row is still there (ADVANCED only
    since #150).
  - "Reset to Default Setting" leaves a ``bal_auto_rebuild`` value stored by
    an older version untouched (the key is kept, never rewritten).
  - ``Plugin.GUI_AUTO_REBUILD_ENABLED`` is False, and with a stored
    ``bal_auto_rebuild = True`` wallet activity schedules nothing, for every
    open plugin window of that wallet.

Run:
    source "$BAL_HOME/electrum/env/bin/activate"
    QT_QPA_PLATFORM=offscreen python3 tests/test_auto_rebuild_hidden.py
"""

import sys
import tempfile

sys.path.insert(0, __file__.rsplit("/", 2)[0])

from unittest.mock import patch

from PyQt6.QtWidgets import QApplication, QLabel, QPushButton

_app = QApplication.instance() or QApplication(sys.argv)

from electrum.simple_config import SimpleConfig

import bal.gui.qt.plugin as plugin_mod
from bal.gui.qt.plugin import Plugin

HIDDEN_LABEL = "Rebuild automatically on new transactions"
KEPT_LABEL = "Rebuild will on wallet close"


def _isolated_config():
    """An in-memory SimpleConfig in a fresh temp dir (never the real config)."""
    return SimpleConfig({"electrum_path": tempfile.mkdtemp(prefix="bal_test_")})


def _plugin(user_type="basic"):
    """A real Plugin on an isolated config, with the GUI-only calls stubbed."""
    plugin = Plugin(None, _isolated_config(), "bal")
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


def _label_texts(dialog):
    """The texts of every QLabel in *dialog*."""
    return [w.text() for w in dialog.findChildren(QLabel)]


def test_no_auto_rebuild_row_in_basic_and_advanced():
    """Neither mode has the row; the wallet-close row is still built."""
    for user_type in ("basic", "advanced"):
        dialog = _build_dialog(_plugin(user_type))
        texts = _label_texts(dialog)
        assert HIDDEN_LABEL not in texts, user_type
        assert KEPT_LABEL in texts, user_type
        dialog.close()


def _kept_row_label(dialog):
    """The "Rebuild will on wallet close" label of *dialog*."""
    labels = [w for w in dialog.findChildren(QLabel) if w.text() == KEPT_LABEL]
    assert len(labels) == 1
    return labels[0]


def test_wallet_close_row_advanced_only():
    """#150: the wallet-close row is hidden in BASIC, shown in ADVANCED."""
    basic = _build_dialog(_plugin("basic"))
    assert _kept_row_label(basic).isHidden() is True
    basic.close()
    advanced = _build_dialog(_plugin("advanced"))
    assert _kept_row_label(advanced).isHidden() is False
    advanced.close()


def test_reset_defaults_keeps_stored_auto_rebuild():
    """The Reset button does not rewrite a value stored by an older version."""
    plugin = _plugin("advanced")
    plugin.AUTO_REBUILD.set(True)
    plugin.REBUILD_ON_CLOSE.set(False)
    dialog = _build_dialog(plugin)
    buttons = [
        b for b in dialog.findChildren(QPushButton)
        if b.text() == "Reset to Default Setting"
    ]
    assert len(buttons) == 1
    buttons[0].click()
    # The visible setting is reset; the hidden key is left as it was.
    assert plugin.REBUILD_ON_CLOSE.get() is True
    assert plugin.AUTO_REBUILD.get() is True
    dialog.close()


def test_switch_is_off():
    """The flow is switched off at class level."""
    assert Plugin.GUI_AUTO_REBUILD_ENABLED is False


def test_stored_true_schedules_nothing():
    """With bal_auto_rebuild = True, wallet activity starts no rebuild."""
    plugin = _plugin()
    plugin.AUTO_REBUILD.set(True)
    wallet = object()
    scheduled = []

    class _Win:
        """An open plugin window of *wallet* that records any schedule."""

        def __init__(self):
            self.wallet = wallet
            self.ok = True
            self.disable_plugin = False

        def schedule_auto_rebuild(self):
            """Record that a rebuild was scheduled."""
            scheduled.append(self)

    plugin.bal_windows = {1: _Win(), 2: _Win()}
    plugin._wallet_activity(wallet)
    plugin.on_event_new_transaction(wallet, None)
    plugin.on_event_wallet_updated(wallet)
    assert scheduled == []


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
