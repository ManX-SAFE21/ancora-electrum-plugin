"""``bal_delivery_set --check_alive_days N``.

The Check Alive is stored as the ``threshold`` ``"-Nd"`` (N days before the
delivery date). The new option writes it through the same validated path as
the date and the fee (``bal.engine.context.set_will_setting``): 1..3650 days,
anything else refused with a clear message and nothing written. It works in
both delivery forms and with a will in force (only the reminder day moves).

Offline: the ``FakeWallet`` / real ``Plugin`` of
``test_cli_controller_offline``; no network.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from electrum.util import UserFacingException  # noqa: E402
from test_cli_controller_offline import Plugin, _make_controller  # noqa: E402

from bal.cli import commands as cli_commands  # noqa: E402
from bal.cli import controller as controller_mod  # noqa: E402
from bal.core import checkalive  # noqa: E402


@pytest.mark.parametrize("value, stored", [
    (1, "-1d"), (365, "-365d"), ("244", "-244d"), (" 183 ", "-183d"), (3650, "-3650d"),
])
def test_check_alive_days_is_stored_as_minus_n_days(value, stored):
    """N days (int or text) is stored and shown as the threshold ``-Nd``."""
    with Plugin() as plugin:
        c = _make_controller(plugin)
        out = c.delivery_set(check_alive_days=value)
        assert c.will_settings["threshold"] == stored
        assert out["threshold"] == stored
        assert "Check Alive" in out["message"]


@pytest.mark.parametrize("value", [0, -5, 3651, 10 ** 6, "abc", "12.5", 12.5, True, False, ""])
def test_check_alive_days_out_of_range_is_refused_and_nothing_written(value):
    """Zero, negatives, > 3650, fractions, bools and non-numbers are refused."""
    with Plugin() as plugin:
        c = _make_controller(plugin)
        before = dict(c.will_settings)
        with pytest.raises(UserFacingException, match="Not a Check Alive"):
            c.delivery_set(check_alive_days=value)
        assert c.will_settings == before


def test_a_refused_check_alive_writes_no_other_change():
    """Everything is checked before anything is written: a good fee given
    with a bad Check Alive is not stored either."""
    with Plugin() as plugin:
        c = _make_controller(plugin)
        fee = c.will_settings.get("baltx_fees")
        with pytest.raises(UserFacingException):
            c.delivery_set(fee=fee + 7, check_alive_days=0)
        assert c.will_settings.get("baltx_fees") == fee


def test_check_alive_days_with_the_period_form_and_months():
    """The option works in the period form, together with ``--months``."""
    with Plugin() as plugin:
        c = _make_controller(plugin)
        c.delivery_form("period", months=24)
        out = c.delivery_set(months=24, check_alive_days=365)
        assert out["form"] == "period" and out["period_months"] == 24
        assert c.will_settings["threshold"] == "-365d"
        # the renewal rule reads the new value: Check Alive = delivery - 365 days
        assert checkalive.check_alive_days(c.will_settings["threshold"]) == 365


def test_nothing_to_change_names_the_new_option():
    """The "Nothing to change" message lists ``--check_alive_days``."""
    with Plugin() as plugin:
        c = _make_controller(plugin)
        with pytest.raises(UserFacingException, match="--check_alive_days"):
            c.delivery_set()


def test_refused_while_the_settings_are_unavailable():
    """Like every delivery change, it stops while the settings are held."""
    with Plugin() as plugin:
        c = _make_controller(plugin)
        c.settings_unavailable = True
        with pytest.raises(UserFacingException):
            c.delivery_set(check_alive_days=100)


def test_the_command_through_electrums_commands():
    """``bal_delivery_set --check_alive_days`` the way Electrum calls it."""
    from test_cli_command_layer import run
    from test_cli_controller_offline import FakeWallet

    with Plugin() as plugin:
        wallet = FakeWallet()
        out = run(plugin, "delivery_set", wallet=wallet, check_alive_days=244)
        assert out["threshold"] == "-244d"
        with pytest.raises(UserFacingException, match="Not a Check Alive"):
            run(plugin, "delivery_set", wallet=wallet, check_alive_days=3651)
        assert run(plugin, "delivery_show", wallet=wallet)["threshold"] == "-244d"


def test_the_command_documents_the_option():
    """The command docstring declares the option for Electrum's CLI parser."""
    doc = cli_commands.delivery_set.__doc__ or ""
    assert "arg:int:check_alive_days:" in doc
    assert controller_mod._as_check_alive_days(10) == 10
