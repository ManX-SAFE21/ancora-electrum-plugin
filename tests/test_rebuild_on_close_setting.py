#!/usr/bin/env python3
"""Tests for the "Rebuild will on wallet close" (REBUILD_ON_CLOSE) setting.

Covers:

  * the persisted ``bal_rebuild_on_close`` configuration key exists and
    defaults to ON (True), and can be turned off and read back;
  * ``BalWindow.on_close()`` runs the "Build your will" wizard
    (``BalBuildWillDialog.build_will_task()``) when the setting is ON;
  * ``BalWindow.on_close()`` SKIPS the wizard when the setting is OFF in
    ADVANCED mode, but still calls ``save_willitems()`` so the last built
    state is persisted;
  * #150: in BASIC mode the wizard ALWAYS runs, even with the setting OFF
    (``BalPlugin.rebuild_on_close_active``).

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
from bal.core.plugin_base import BalConfig, BalPlugin  # noqa: E402

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

    def __init__(self, bal_window, **kwargs):
        # kwargs: on_close() passes closing=True (CHANGELOG #84b-2).
        self.bal_window = bal_window
        self.kwargs = kwargs
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


def _make_fake_plugin(rebuild_on_close, user_type):
    """A fake plugin with the two settings and the real BalPlugin methods
    ``is_basic_mode`` / ``rebuild_on_close_active`` bound to it."""
    cfg = FakeConfig()
    plugin = types.SimpleNamespace(
        REBUILD_ON_CLOSE=BalConfig(cfg, CONFIG_KEY, True),
        USER_TYPE=BalConfig(cfg, "bal_user_type", "basic"),
    )
    plugin.REBUILD_ON_CLOSE.set(rebuild_on_close)
    plugin.USER_TYPE.set(user_type)
    plugin.is_basic_mode = types.MethodType(BalPlugin.is_basic_mode, plugin)
    plugin.rebuild_on_close_active = types.MethodType(
        BalPlugin.rebuild_on_close_active, plugin)
    return plugin


def _make_fake_window(rebuild_on_close, user_type="advanced"):
    """Return a fake controller with the attributes on_close() touches."""
    fake = types.SimpleNamespace()
    fake.disable_plugin = False
    fake.bal_plugin = _make_fake_plugin(rebuild_on_close, user_type)
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
    assert FakeBuildWillDialog.instances[0].kwargs == {"closing": True}, (
        "the dialog must know the wallet is closing (#84b-2)")
    assert fake._wizard_ran is True, "wizard build_will_task must run"
    assert fake.saved == ["save"], "save_willitems must run"


def test_on_close_skips_wizard_when_disabled():
    """With REBUILD_ON_CLOSE OFF in ADVANCED, on_close() skips the wizard but
    saves."""
    fake = _make_fake_window(False)
    _call_on_close(fake)
    assert len(FakeBuildWillDialog.instances) == 0, "wizard must NOT be opened"
    assert not hasattr(fake, "_wizard_ran"), "wizard must not run"
    assert fake.saved == ["save"], "save_willitems must still run"


def test_on_close_basic_always_runs_wizard():
    """#150: in BASIC mode the wizard runs whatever the stored value."""
    for stored in (True, False):
        fake = _make_fake_window(stored, user_type="basic")
        _call_on_close(fake)
        assert len(FakeBuildWillDialog.instances) == 1, stored
        assert fake._wizard_ran is True, stored
        assert fake.saved == ["save"], stored


def test_rebuild_on_close_active_by_mode():
    """#150: always True in BASIC (and for an unknown mode), the stored
    value in ADVANCED; the stored value itself is never rewritten."""
    for user_type, stored, expected in (
        ("basic", True, True), ("basic", False, True),
        ("", False, True), ("advanced", True, True),
        ("advanced", False, False),
    ):
        plugin = _make_fake_plugin(stored, user_type)
        assert plugin.rebuild_on_close_active() is expected, (user_type, stored)
        assert plugin.REBUILD_ON_CLOSE.get() is stored


if __name__ == "__main__":
    test_rebuild_on_close_config_defaults_on()
    test_rebuild_on_close_config_can_be_disabled()
    test_on_close_runs_wizard_when_enabled()
    test_on_close_skips_wizard_when_disabled()
    test_on_close_basic_always_runs_wizard()
    test_rebuild_on_close_active_by_mode()
    print("OK: all tests passed")
