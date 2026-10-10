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


def _bal_commands():
    """Every registered ``bal_*`` command (Electrum's ``known_commands``)."""
    from electrum.commands import known_commands

    return {n: c for n, c in known_commands.items() if n.startswith("bal_")}


def test_no_command_has_a_url_parameter():
    """#100: Electrum reads an option called ``url`` as a payment request and
    stops with "unknown command: <url>"; no bal_* command may use the name."""
    for name, cmd in _bal_commands().items():
        assert "url" not in list(cmd.params) + list(cmd.options), name


def test_command_line_parses_executor_commands():
    """#100: parsed by Electrum's own parser, the URL lands in
    ``executor_url`` and nothing in ``url`` (what ``run_electrum`` checks)."""
    from electrum.commands import get_parser

    url = "https://we.example.com"
    for argv in (
        ["bal_willexecutors_show", url],
        ["bal_willexecutors_add", url, "--base_fee", "1000"],
        ["bal_willexecutors_update", url, "--info", "x"],
        ["bal_willexecutors_select", url, "--value", "false"],
    ):
        options = vars(get_parser().parse_args(argv))
        assert options.get("url") is None, argv
        assert options["executor_url"] == url, argv
    options = vars(get_parser().parse_args(argv))
    assert options["value"] is False


def test_executor_commands_run(plugin):
    """#100: add, show, select and deselect through the command layer."""
    wallet = FakeWallet()
    url = "https://we.example.com"
    run(plugin, "willexecutors_add", url, base_fee=1000, wallet=wallet)
    shown = run(plugin, "willexecutors_show", url, wallet=wallet)
    assert shown["willexecutor"]["base_fee"] == 1000
    assert run(plugin, "willexecutors_select", url, wallet=wallet) == {url: True}
    assert run(plugin, "willexecutors_select", url, value=False, wallet=wallet) == {
        url: False
    }


def test_executor_update_with_every_field(plugin):
    """#101: the command passes every field by position, the promo code
    included; the controller used to raise ``TypeError``."""
    wallet = FakeWallet()
    url = "https://we.example.com"
    new_url = "https://we2.example.com"
    run(plugin, "willexecutors_add", url, wallet=wallet)
    out = run(
        plugin,
        "willexecutors_update",
        url,
        base_fee=2000,
        info="renamed",
        promo_code="SAFE21",
        rename_to=new_url,
        wallet=wallet,
    )
    assert out["url"] == new_url
    we = out["willexecutor"]
    assert (we["base_fee"], we["info"], we["promo_code"]) == (2000, "renamed", "SAFE21")
    out = run(plugin, "willexecutors_update", new_url, promo_code="", wallet=wallet)
    assert out["willexecutor"]["promo_code"] is None
