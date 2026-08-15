#!/usr/bin/env python3
"""Tests for the "Rebuild will on wallet close" (REBUILD_ON_CLOSE) setting.

Covers:

  * the persisted ``bal_rebuild_on_close`` configuration key exists and
    defaults to ON (True), and can be turned off and read back;
  * ``BalWindow.on_close()`` runs the "Build your will" wizard
    (``BalBuildWillDialog.build_will_task()``) when the setting is ON;
  * ``BalWindow.on_close()`` SKIPS the wizard when the setting is OFF, but
    still calls ``save_willitems()`` so the last built state is persisted.

The on_close tests drive the real ``BalWindow.on_close`` method with a
light-weight fake controller and a recording stub for ``BalBuildWillDialog``,
so no full wallet/GUI machinery is needed.

Run:
    source "$BAL_HOME/electrum/env/bin/activate"
    QT_QPA_PLATFORM=offscreen python3 tests/test_rebuild_on_close_setting.py
"""

import os
import sys
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bal.gui.qt.window as window_mod  # noqa: E402
from bal.core.plugin_base import BalConfig  # noqa: E402

CONFIG_KEY = "bal_rebuild_on_close"


# --------------------------------------------------------------------------- #
# Mocks
# --------------------------------------------------------------------------- #

class FakeConfig:
    """Minimal mock for Electrum's config object (key/value store)."""

    def __init__(self):
        self._store = {}

    def get(self, key, default=None):
        return self._store.get(key, default)

    def set_key(self, key, value, save=True):
        self._store[key] = value


class FakeBuildWillDialog:
    """Recording stub for BalBuildWillDialog, patched into window.py."""

    instances = []

    def __init__(self, bal_window):
        self.bal_window = bal_window
        FakeBuildWillDialog.instances.append(self)

    def build_will_task(self):
        self.bal_window._wizard_ran = True


class _Tabs:
    def update(self):
        pass


class _NoOp:
    willexecutors_action = None
    tabs = _Tabs()

    def close(self):
        pass

    def toggle_tab(self, tab):
        pass

    def update(self):
        pass

    def removeAction(self, action):
        pass


def _make_fake_window(rebuild_on_close):
    """Return a fake controller with the attributes on_close() touches."""
    fake = types.SimpleNamespace()
    fake.disable_plugin = False
    fake.bal_plugin = types.SimpleNamespace(
        REBUILD_ON_CLOSE=BalConfig(FakeConfig(), CONFIG_KEY, rebuild_on_close)
    )
    fake.willitems = {}
    fake.will = {}
    fake.saved = []
    fake.save_willitems = lambda: fake.saved.append("save")
    fake.heirs_tab = _NoOp()
    fake.will_tab = _NoOp()
    fake.tools_menu = _NoOp()
    fake.window = _NoOp()
    fake._menubar_initialized = True
    return fake


def _call_on_close(fake):
    original = window_mod.BalBuildWillDialog
    FakeBuildWillDialog.instances = []
    try:
        window_mod.BalBuildWillDialog = FakeBuildWillDialog
        window_mod.BalWindow.on_close(fake)
    finally:
        window_mod.BalBuildWillDialog = original


# --------------------------------------------------------------------------- #
# Config key
# --------------------------------------------------------------------------- #

def test_rebuild_on_close_config_defaults_on():
    """bal_rebuild_on_close must default to ON (True) when not yet stored."""
    cfg = FakeConfig()
    rebuild = BalConfig(cfg, CONFIG_KEY, True)
    assert rebuild.get() is True


def test_rebuild_on_close_config_can_be_disabled():
    """Once turned off and persisted, bal_rebuild_on_close reads back False."""
    cfg = FakeConfig()
    rebuild = BalConfig(cfg, CONFIG_KEY, True)
    rebuild.set(False)
    assert rebuild.get() is False
    # A fresh wrapper over the same config still sees the stored value.
    assert BalConfig(cfg, CONFIG_KEY, True).get() is False


# --------------------------------------------------------------------------- #
# on_close() behaviour
# --------------------------------------------------------------------------- #

def test_on_close_runs_wizard_when_enabled():
    """With REBUILD_ON_CLOSE ON, on_close() builds the will and saves it."""
    fake = _make_fake_window(True)
    _call_on_close(fake)
    assert len(FakeBuildWillDialog.instances) == 1, "wizard must be opened"
    assert fake._wizard_ran is True, "wizard build_will_task must run"
    assert fake.saved == ["save"], "save_willitems must run"


def test_on_close_skips_wizard_when_disabled():
    """With REBUILD_ON_CLOSE OFF, on_close() skips the wizard but saves."""
    fake = _make_fake_window(False)
    _call_on_close(fake)
    assert len(FakeBuildWillDialog.instances) == 0, "wizard must NOT be opened"
    assert not hasattr(fake, "_wizard_ran"), "wizard must not run"
    assert fake.saved == ["save"], "save_willitems must still run"


if __name__ == "__main__":
    test_rebuild_on_close_config_defaults_on()
    test_rebuild_on_close_config_can_be_disabled()
    test_on_close_runs_wizard_when_enabled()
    test_on_close_skips_wizard_when_disabled()
    print("OK: all tests passed")
