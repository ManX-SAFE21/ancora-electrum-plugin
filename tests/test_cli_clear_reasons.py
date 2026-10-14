#!/usr/bin/env python3
"""The command line names the real reason a will is rebuilt (CHANGELOG #78).

``bal_will_check`` used the old generic texts ("Found CHANGES to the DATE or
the HEIRS", "Will-Executor not present"); it now uses the sentences of the
Check dialog (``bal.core.messages``). ``bal_will_prepare`` adds a ``reason``
field when it rebuilds the will.

Offline, with the fake signing wallet of ``test_cli_autorebuild.py``.

Run:
    PYTHONPATH=electrum-src python3 tests/test_cli_clear_reasons.py
"""

import os
import sys
from contextlib import contextmanager

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

from electrum import constants
from electrum.util import UserFacingException
from test_cli_autorebuild import (
    THIRD_ADDRESS,
    FakeWallet,
    _make_controller,
    _no_willexecutors,
    _Plugin,
    make_funding_input,
)

from bal.cli.controller import _user_facing
from bal.core import will as will_mod
from bal.core.messages import will_change_message

HNF = will_mod.HeirNotFoundException


def _text(e):
    return str(_user_facing(e))


@contextmanager
def _mainnet():
    """Run on mainnet, then restore the previous network.

    The fake wallet uses a mainnet address; other test files switch
    Electrum's global network and do not restore it, which would make the
    address invalid when the whole suite runs (the reason the
    test_cli_autorebuild tests fail there).
    """
    old = constants.net
    constants.BitcoinMainnet.set_as_network()
    try:
        yield
    finally:
        old.set_as_network()


# ------------------------------------------------------------- _user_facing

def test_heir_changes_use_the_check_sentences():
    assert _text(HNF("carol", HNF.HEIR_ADDED)) == 'Heir "carol" was added: preparing a new will.'
    assert _text(HNF("bob", HNF.HEIR_REMOVED)) == 'Heir "bob" was removed: preparing a new will.'
    assert "amount of heir \"alice\" changed" in _text(HNF("alice", HNF.HEIR_AMOUNT))


def test_fee_and_executor_and_funds():
    assert "built with 13 sat/byte, now it is 14" in _text(
        will_mod.TxFeesChangedException("14:  13"))
    assert 'Will-executor "https://we0.test" is not covered' in _text(
        will_mod.WillExecutorNotPresent("https://we0.test"))
    assert "does not cover yet" in _text(
        will_mod.NotCompleteWillException("Some utxo in the wallet is not included"))


def test_no_willexecutor_keeps_its_cli_text():
    """Also raised by build_will for "nothing selected": kept unchanged."""
    assert _text(will_mod.NoWillExecutorNotPresent("x")) == (
        "No backup transaction or will-executor selected")


def test_same_text_as_the_check_dialog():
    e = HNF("carol", HNF.HEIR_ADDED)
    assert _text(e) == will_change_message(e)


def test_other_exceptions_unchanged():
    assert isinstance(_user_facing(will_mod.NoHeirsException()), UserFacingException)
    boom = RuntimeError("boom")
    assert _user_facing(boom) is boom


# ------------------------------------------------- prepare_will / will_check

def test_prepare_reports_new_funds():
    with _mainnet(), _no_willexecutors(), _Plugin() as plugin:
        plugin.NO_WILLEXECUTOR.set(True)
        wallet = FakeWallet([make_funding_input()])
        c = _make_controller(plugin, wallet)
        c.prepare_will()
        wallet._utxos.append(make_funding_input("22" * 32))
        result = c.prepare_will()
    assert result["result"] == "rebuilt", result
    assert "does not cover yet" in result["reason"], result["reason"]


def test_check_and_prepare_report_an_added_heir():
    with _mainnet(), _no_willexecutors(), _Plugin() as plugin:
        plugin.NO_WILLEXECUTOR.set(True)
        c = _make_controller(plugin, FakeWallet([make_funding_input()]))
        c.prepare_will()
        c.heirs_add("carol", THIRD_ADDRESS, "50000")
        try:
            c.will_check()
            raise AssertionError("will_check must fail after an heir is added")
        except UserFacingException as e:
            assert str(e) == 'Heir "carol" was added: preparing a new will.', str(e)
        result = c.prepare_will()
    assert result["result"] == "rebuilt", result
    assert result["reason"] == 'Heir "carol" was added: preparing a new will.'


if __name__ == "__main__":
    for name in sorted(dir()):
        if name.startswith("test_"):
            globals()[name]()
            print(f"  [OK] {name}")
    print("[OK] All CLI reason tests passed")
