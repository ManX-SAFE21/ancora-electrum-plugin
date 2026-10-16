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
import json
import os
import sys
import time
import unittest.mock as mock
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from electrum.commands import Commands
from electrum.util import UserFacingException
from test_cli_controller_offline import VALID_ADDRESS, FakeWallet, Plugin

import bal  # noqa: F401  (importing the package registers the bal_* commands)
from bal.core import delivery
from bal.engine import context

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


def test_will_settings_commands_point_to_the_delivery_commands(plugin):
    """#99 saved the will settings (a JSON object) from the command line.

    Since #123 each wallet keeps its own delivery date and fee rate, and
    Electrum's global ``bal_will_settings`` (BAL's key) is never written
    again (plan R1-8, deliberate): set, get and reset refuse it with a
    pointer to ``bal_delivery_show`` / ``bal_delivery_set``, by config key
    or attribute name, and the list hides it. The global is not created.
    """
    value = '{"baltx_fees": 20, "threshold": "-180d", "locktime": "2y"}'
    with pytest.raises(UserFacingException, match="bal_delivery_set"):
        run(plugin, "settings_set", "bal_will_settings", value)
    for name in ("settings_get", "settings_reset"):
        for key in ("bal_will_settings", "WILL_SETTINGS"):
            with pytest.raises(UserFacingException, match="bal_delivery_show"):
                run(plugin, name, key)
    listing = run(plugin, "settings_list")
    assert "TX_FEES" in listing
    assert "WILL_SETTINGS" not in listing
    assert "bal_will_settings" not in [v["name"] for v in listing.values()]
    assert plugin.config.get("bal_will_settings") is None


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


def test_default_delivery_date_is_kept_by_the_wallet(plugin, monkeypatch):
    """#102: the first wallet command stores the default delivery date, so a
    daemon restarted on a later day (when the default would be later) keeps
    the delivery date of the will already built.

    Since #123 the date is stored in the WALLET (its ``will_settings``,
    migrated once; a wallet without heirs or a will gets the default, today
    + 24 months since #127) and Electrum's global ``bal_will_settings`` is never
    written (rule I1). The restart is three days later (on the engine's
    clock, which the migration reads): two days in a row can give the same
    default at the end of February.
    """
    wallet = FakeWallet()
    assert plugin.config.get("bal_will_settings") is None
    run(plugin, "heirs_list", wallet=wallet)
    saved = dict(wallet.db.get_dict("will_settings"))
    assert saved["settings_version"] == 2            # #127: schema 2
    assert saved["form"] == "fixed"
    assert saved["migrated_from"] == "default"
    assert delivery.as_delivery(saved["locktime"]) == saved["locktime"]
    assert plugin.config.get("bal_will_settings") is None
    # A restart three days later: the default would now be another date.
    later = time.time() + 3 * 24 * 3600
    assert delivery.default_delivery(later) != saved["locktime"]
    monkeypatch.setattr(context, "time", SimpleNamespace(time=lambda: later))
    out = run(plugin, "heirs_add", "alice", VALID_ADDRESS, "100%", wallet=wallet)
    assert out["value"][2] == saved["locktime"]
    assert dict(wallet.db.get_dict("will_settings")) == saved
    assert plugin.config.get("bal_will_settings") is None


def test_heir_locktime_refused_when_every_heir_shares_the_date(plugin):
    """#103: multiverse off (the default): a per-heir --locktime would be
    overwritten by the will's date at the next check, so it is refused. The
    message points to ``bal_delivery_set --date`` (#123: the date is the
    wallet's own setting; ``bal_will_settings`` is not used any more)."""
    wallet = FakeWallet()
    with pytest.raises(UserFacingException, match="bal_delivery_set --date"):
        run(plugin, "heirs_add", "bob", VALID_ADDRESS, "50%", locktime="2y",
            wallet=wallet)
    run(plugin, "heirs_add", "bob", VALID_ADDRESS, "50%", wallet=wallet)
    with pytest.raises(UserFacingException, match="bal_delivery_set --date"):
        run(plugin, "heirs_update", "bob", locktime="2y", wallet=wallet)
    # Changing another field keeps working.
    out = run(plugin, "heirs_update", "bob", amount="40%", wallet=wallet)
    assert out["value"][1] == "40%"


@pytest.mark.parametrize(
    "locktime, message",
    [("domani", "not understood"), ("1500000000", "in the past")],
)
def test_heir_locktime_checked_with_multiverse(plugin, locktime, message):
    """#103: multiverse on: a date that is not understood (it gave 0) or is
    already past is refused; a future one is kept."""
    plugin.ENABLE_MULTIVERSE.set(True)
    wallet = FakeWallet()
    with pytest.raises(UserFacingException, match=message):
        run(plugin, "heirs_add", "carol", VALID_ADDRESS, "10%", locktime=locktime,
            wallet=wallet)
    out = run(plugin, "heirs_add", "carol", VALID_ADDRESS, "10%", locktime="2y",
              wallet=wallet)
    assert out["value"][2] == "2y"


@pytest.mark.parametrize(
    "name, args",
    [
        ("heirs_import", ["/nonexistent/heirs.json"]),
        ("heirs_export", ["/nonexistent/dir/heirs.json"]),
        ("willexecutors_import", ["/nonexistent/we.json"]),
        ("willexecutors_export", ["/nonexistent/dir/we.json"]),
        ("will_export", ["/nonexistent/dir/will.json"]),
    ],
)
def test_file_errors_are_messages(plugin, name, args):
    """#104: a wrong path gives a message, not a Python traceback with
    "internal error while executing RPC"."""
    with pytest.raises(UserFacingException, match="Failed to (import from|export to) file"):
        run(plugin, name, *args, wallet=FakeWallet())


def test_bad_address_message_says_it_once(plugin):
    """#104: the address error used to repeat itself
    ("not an address, not an address,<address>")."""
    with pytest.raises(UserFacingException) as info:
        run(plugin, "heirs_add", "erin", "notanaddress", "10%", wallet=FakeWallet())
    # #135: the same two lines as the "Edit heir" dialog (one helper).
    assert str(info.value) == (
        "Not a valid address for this network: notanaddress\n"
        "Check that you copied all of it, with no extra or missing characters.")


def test_push_failure_keeps_the_reason():
    """#105: the push of one will-executor keeps why it failed."""
    from bal.core.willexecutors import Willexecutors

    we = {"url": "https://we.example.com", "txs": "00"}
    with mock.patch.object(
        Willexecutors, "send_request", side_effect=Exception("You are offline.")
    ):
        assert Willexecutors.push_transactions_to_willexecutor(we) is False
    assert we["broadcast_error"] == "You are offline."


def _push_with(plugin, answers):
    """Run the controller's push with fake servers.

    ``answers`` maps each URL to ``True`` (received), a reason (failed) or
    ``None`` (no answer before the global deadline).
    """
    from bal.cli.controller import BalController
    from bal.core.willexecutors import Willexecutors

    targets = {url: {"url": url, "txs": "00", "txsids": []} for url in answers}

    def fake_parallel(willexecutors, on_each, **kw):
        for url, we in willexecutors.items():
            answer = answers[url]
            if answer is True:
                on_each(url, we, True, None)
            elif answer is not None:
                we["broadcast_error"] = answer
                on_each(url, we, False, None)

    controller = BalController(plugin, FakeWallet())
    controller.willitems = {"x": object()}  # only checked for being non-empty
    # #155 (D2): only a valid current record receives the will; these fake
    # servers are valid, the test is about the reasons of the failures.
    with mock.patch.object(
        Willexecutors, "get_willexecutor_transactions", return_value=targets
    ), mock.patch.object(
        Willexecutors, "is_valid", return_value=True
    ), mock.patch.object(
        Willexecutors, "push_transactions_parallel", side_effect=fake_parallel
    ), mock.patch.object(BalController, "save_willitems"):
        try:
            return controller.broadcast_command()
        except UserFacingException as e:
            return e


def test_broadcast_fails_with_the_reasons_when_nothing_was_sent(plugin):
    """#105: no will-executor received the will -> an error (exit code 1)
    that names each one and its reason, not "Failed" with exit code 0."""
    error = _push_with(plugin, {"https://a.example": "You are offline.",
                                "https://b.example": None})
    assert isinstance(error, UserFacingException)
    text = str(error)
    assert "not sent to any will-executor" in text
    assert "https://a.example: You are offline." in text
    assert "https://b.example: no answer in time" in text


def test_broadcast_partial_success_returns_the_failed_ones(plugin):
    """#105: when at least one received it, the JSON result lists the
    others in ``_failed``."""
    out = _push_with(plugin, {"https://a.example": True,
                              "https://b.example": "refused"})
    assert out["https://a.example"] == "Success"
    assert out["_error"] is True
    assert out["_failed"] == {"https://b.example": "refused"}


def test_heirs_import_reports_what_it_skipped(plugin, tmp_path):
    """#105: ``imported`` was the total of heirs and invalid entries vanished
    silently; now the count, the names and the reasons are returned."""
    path = tmp_path / "heirs.json"
    path.write_text(json.dumps({
        "ok": [VALID_ADDRESS, "50%", "1y"],
        "bad_address": ["notanaddress", "10%", "1y"],
        "bad_amount": [VALID_ADDRESS, "abc", "1y"],
    }))
    wallet = FakeWallet()
    run(plugin, "heirs_add", "alice", VALID_ADDRESS, "50%", wallet=wallet)
    out = run(plugin, "heirs_import", str(path), wallet=wallet)
    assert (out["imported"], out["names"], out["total"]) == (1, ["ok"], 2)
    assert set(out["skipped"]) == {"bad_address", "bad_amount"}
    assert "notanaddress" in out["skipped"]["bad_address"]


def test_heirs_import_checks_dates_with_multiverse(plugin, tmp_path):
    """#105: with multiverse on, a date that is not understood is refused
    ("ieri" was imported with a delivery date of 0)."""
    plugin.ENABLE_MULTIVERSE.set(True)
    path = tmp_path / "heirs.json"
    path.write_text(json.dumps({"x3": [VALID_ADDRESS, "10%", "ieri"]}))
    out = run(plugin, "heirs_import", str(path), wallet=FakeWallet())
    assert out["imported"] == 0
    assert "not understood" in out["skipped"]["x3"]


def test_delete_takes_one_name_and_reports_unknown_ones(plugin):
    """#105: one name without the JSON brackets works (it ran over the
    letters), and unknown names are listed in ``not_found``."""
    from electrum.commands import get_parser

    wallet = FakeWallet()
    run(plugin, "heirs_add", "alice", VALID_ADDRESS, "100%", wallet=wallet)
    # The command line accepts a plain name (it used to refuse non-JSON).
    assert vars(get_parser().parse_args(["bal_heirs_delete", "alice"]))["names"] == "alice"
    out = run(plugin, "heirs_delete", ["alice", "nobody"], wallet=wallet)
    assert out == {"deleted": ["alice"], "not_found": ["nobody"]}
    run(plugin, "heirs_add", "bob", VALID_ADDRESS, "100%", wallet=wallet)
    assert run(plugin, "heirs_delete", "bob", wallet=wallet)["deleted"] == ["bob"]
    url = "https://we.example.com"
    run(plugin, "willexecutors_add", url, wallet=wallet)
    assert run(plugin, "willexecutors_delete", url, wallet=wallet)["deleted"] == [url]


def test_willexecutors_import_counts_only_the_added_ones(plugin, tmp_path):
    """#105: ``imported`` counted the entries already in the list too."""
    wallet = FakeWallet()
    known = "https://we.example.com"
    run(plugin, "willexecutors_add", known, wallet=wallet)
    path = tmp_path / "we.json"
    path.write_text(json.dumps({
        known: {"base_fee": 1, "address": "", "info": "x"},
        "https://new.example.com": {"base_fee": 1, "address": "", "info": "y"},
    }))
    out = run(plugin, "willexecutors_import", str(path), wallet=wallet)
    assert out["imported"] == 1
    assert out["already_present"] == [known]
