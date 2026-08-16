"""
Test: BAL plugin CLI commands are registered with Electrum.

Verifies that importing the plugin through Electrum's own plugin loader
(``Plugins(config, cmd_only=True)``, the exact code path ``run_electrum`` uses
to pre-parse the command line) registers every ``bal_*`` command with
``electrum.commands`` (``known_commands`` + the ``Commands`` class).

It also asserts the basic contract enforced by ``plugin_command``: each command
is a coroutine and carries the expected flags (all ``bal_*`` commands require a
daemon/network, i.e. the ``'n'`` flag; the wallet-bound ones the ``'w'`` flag;
signing also ``'p'``).

Run:
    source "$BAL_HOME/electrum/env/bin/activate"
    python3 tests/test_cli_commands_registered.py
"""

import inspect
import tempfile

from electrum import commands as electrum_commands
from electrum.plugin import Plugins
from electrum.simple_config import SimpleConfig

# The full command table lives in PLAN_CMDLINE_PLUGIN.md section 6; new commands
# added in later phases must be appended here so the registration test keeps
# proving the whole list is wired up.
EXPECTED_COMMANDS = {
    # Settings (no wallet required)
    "bal_settings_list": {
        "requires_network": True,
        "requires_wallet": False,
        "requires_password": False,
    },
    "bal_settings_get": {
        "requires_network": True,
        "requires_wallet": False,
        "requires_password": False,
    },
    "bal_settings_set": {
        "requires_network": True,
        "requires_wallet": False,
        "requires_password": False,
    },
    "bal_settings_reset": {
        "requires_network": True,
        "requires_wallet": False,
        "requires_password": False,
    },
    # Heirs
    "bal_heirs_list": {"requires_network": True, "requires_wallet": True, "requires_password": False},
    "bal_heirs_show": {"requires_network": True, "requires_wallet": True, "requires_password": False},
    "bal_heirs_add": {"requires_network": True, "requires_wallet": True, "requires_password": False},
    "bal_heirs_update": {"requires_network": True, "requires_wallet": True, "requires_password": False},
    "bal_heirs_delete": {"requires_network": True, "requires_wallet": True, "requires_password": False},
    "bal_heirs_import": {"requires_network": True, "requires_wallet": True, "requires_password": False},
    "bal_heirs_export": {"requires_network": True, "requires_wallet": True, "requires_password": False},
    # Will-Executors
    "bal_willexecutors_list": {"requires_network": True, "requires_wallet": True, "requires_password": False},
    "bal_willexecutors_show": {"requires_network": True, "requires_wallet": True, "requires_password": False},
    "bal_willexecutors_add": {"requires_network": True, "requires_wallet": True, "requires_password": False},
    "bal_willexecutors_update": {"requires_network": True, "requires_wallet": True, "requires_password": False},
    "bal_willexecutors_select": {"requires_network": True, "requires_wallet": True, "requires_password": False},
    "bal_willexecutors_delete": {"requires_network": True, "requires_wallet": True, "requires_password": False},
    "bal_willexecutors_ping": {"requires_network": True, "requires_wallet": True, "requires_password": False},
    "bal_willexecutors_download": {"requires_network": True, "requires_wallet": True, "requires_password": False},
    "bal_willexecutors_import": {"requires_network": True, "requires_wallet": True, "requires_password": False},
    "bal_willexecutors_export": {"requires_network": True, "requires_wallet": True, "requires_password": False},
    # Will
    "bal_will_status": {"requires_network": True, "requires_wallet": True, "requires_password": False},
    "bal_will_check": {"requires_network": True, "requires_wallet": True, "requires_password": False},
    "bal_will_prepare": {"requires_network": True, "requires_wallet": True, "requires_password": False},
    "bal_will_autorebuild": {"requires_network": True, "requires_wallet": True, "requires_password": False},
    "bal_will_sign": {"requires_network": True, "requires_wallet": True, "requires_password": True},
    "bal_will_broadcast": {"requires_network": True, "requires_wallet": True, "requires_password": False},
    "bal_will_export": {"requires_network": True, "requires_wallet": True, "requires_password": False},
    "bal_will_import_merge": {"requires_network": True, "requires_wallet": True, "requires_password": False},
    "bal_will_invalidate": {"requires_network": True, "requires_wallet": True, "requires_password": False},
    "bal_will_check_executor": {"requires_network": True, "requires_wallet": True, "requires_password": False},
}


def _isolated_config(**overrides):
    """A throwaway SimpleConfig that never touches the real Electrum config.

    A fresh ``electrum_path`` temp dir keeps every write isolated, so running
    the tests cannot pollute the user's config files.  The bal plugin is
    enabled because ``Plugins(cmd_only=True)`` skips any plugin that is not
    explicitly enabled (electrum.plugin.Plugins.find_directory_plugins).
    """
    opts = {"electrum_path": tempfile.mkdtemp(prefix="bal_test_")}
    opts.update(overrides)
    cfg = SimpleConfig(opts)
    cfg.enable_plugin("bal")
    return cfg


def test_commands_registered():
    cfg = _isolated_config()
    Plugins(cfg, cmd_only=True)
    for name, flags in EXPECTED_COMMANDS.items():
        assert name in electrum_commands.known_commands, f"{name} not registered"
        cmd = electrum_commands.known_commands[name]
        assert cmd.name == name
        assert cmd.requires_network is flags["requires_network"]
        assert cmd.requires_wallet is flags["requires_wallet"]
        assert cmd.requires_password is flags["requires_password"]


def test_commands_are_coroutines():
    cfg = _isolated_config()
    Plugins(cfg, cmd_only=True)
    for name in EXPECTED_COMMANDS:
        func = getattr(electrum_commands.Commands, name, None)
        assert func is not None, f"{name} missing from Commands"
        assert inspect.iscoroutinefunction(func), f"{name} is not a coroutine"


def test_no_duplicate_registration():
    """Loading the plugin twice must not raise "Command name bal_... already
    exists" (the guard in bal/__init__._register_cli_commands)."""
    cfg = _isolated_config()
    plugins = Plugins(cfg, cmd_only=True)
    plugins.maybe_load_plugin_init_method("bal")  # already imported -> no-op
    for name in EXPECTED_COMMANDS:
        assert name in electrum_commands.known_commands


def test_command_docstrings_document_all_args():
    """Every parameter/option must carry an ``arg:TYPE:NAME:DESC`` line (the
    CLI parser prints "undocumented argument ..." otherwise)."""
    cfg = _isolated_config()
    Plugins(cfg, cmd_only=True)
    for name in EXPECTED_COMMANDS:
        cmd = electrum_commands.known_commands[name]
        for varname in list(cmd.params) + list(cmd.options):
            if varname in ("wallet", "wallet_path", "plugin", "password"):
                continue
            assert varname in cmd.arg_descriptions, (
                f"{name}: undocumented argument {varname}"
            )


if __name__ == "__main__":
    for name in sorted(dir()):
        if name.startswith("test_"):
            globals()[name]()
            print(f"  [OK] {name}")
    print("[OK] All CLI registration tests passed")
