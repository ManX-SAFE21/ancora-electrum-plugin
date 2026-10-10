"""The ``bal_*`` commands called the way Electrum calls them.

``test_cli_controller_offline`` calls the controller methods directly, so
errors in the command layer (``bal/cli/commands.py``) passed unseen: the
settings commands always failed with "wallet not loaded" (CHANGELOG #99).
Here every command goes through Electrum's own ``Commands`` wrappers (the
``@command`` / ``@plugin_command`` decorators), with a small fake daemon that
hands them the plugin and the wallet.

Run:
    python3 -m pytest tests/test_cli_command_layer.py -q
"""

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from electrum.commands import Commands
from electrum.util import UserFacingException
from test_cli_controller_offline import FakeWallet, Plugin

import bal  # noqa: F401  (importing the package registers the bal_* commands)

WALLET_PATH = "test_wallet"


class FakeDaemon:
    """What Electrum's command wrappers read from the daemon: the config,
    the plugin (``_plugins.get_plugin``) and the loaded wallets."""

    def __init__(self, plugin, wallet=None):
        self.config = plugin.config
        self._plugin = plugin
        self._wallet = wallet
        self._plugins = self

    def get_plugin(self, name):
        """The bal plugin, as ``Plugins.get_plugin`` returns it."""
        return self._plugin if name == "bal" else None

    def get_wallet(self, path):
        """The wallet loaded at ``path`` (only one here), or ``None``."""
        return self._wallet if path and path.endswith(WALLET_PATH) else None


def run(plugin, name, *args, wallet=None, **kwargs):
    """Run the command ``bal_<name>`` as Electrum does and return its result.

    With ``wallet`` the command gets ``wallet_path``, like ``-w`` on the
    command line; without it no wallet is loaded.
    """
    cmds = Commands(config=plugin.config, daemon=FakeDaemon(plugin, wallet))
    if wallet is not None:
        kwargs["wallet_path"] = WALLET_PATH
    return asyncio.run(getattr(cmds, "bal_" + name)(*args, **kwargs))


@pytest.fixture
def plugin():
    """A real ``bal.cli.plugin.Plugin`` on a throwaway config directory."""
    with Plugin() as p:
        yield p


def test_settings_commands_need_no_wallet(plugin):
    """#99: list/get/set/reset run with no wallet loaded."""
    listing = run(plugin, "settings_list")
    assert "TX_FEES" in listing
    assert run(plugin, "settings_get", "bal_tx_fees")["value"] == 100
    assert run(plugin, "settings_set", "bal_tx_fees", "150")["value"] == 150
    assert run(plugin, "settings_get", "TX_FEES")["value"] == 150
    assert run(plugin, "settings_reset", "bal_tx_fees")["value"] == 100


def test_settings_set_will_settings_json(plugin):
    """#99: the will settings (a JSON object) can be saved from the command
    line; Electrum's own ``setconfig`` refuses the key."""
    value = '{"baltx_fees": 20, "threshold": "-180d", "locktime": "2y"}'
    out = run(plugin, "settings_set", "bal_will_settings", value)
    assert out["value"] == {"baltx_fees": 20, "threshold": "-180d", "locktime": "2y"}


def test_settings_unknown_key_is_a_message(plugin):
    """An unknown key gives a clear message, not a traceback."""
    with pytest.raises(UserFacingException, match="Unknown BAL setting"):
        run(plugin, "settings_get", "bal_does_not_exist")


def test_wallet_commands_still_need_a_wallet(plugin):
    """The wallet commands keep refusing to run without a wallet."""
    with pytest.raises(UserFacingException, match="wallet not loaded"):
        run(plugin, "heirs_list")
    assert run(plugin, "heirs_list", wallet=FakeWallet()) == {}
