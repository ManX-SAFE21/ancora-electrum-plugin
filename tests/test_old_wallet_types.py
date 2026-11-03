"""Wallets with addresses starting with 1 or 3 are told they are not supported (#216).

An unsigned transaction on these coins has no txid, so the build stopped with
a message about a will-executor, which had nothing to do with it.

``tests/flowkit.py`` sessions with three fake will-executors, each with its own
fee address, with and without a password. Nothing reaches the network.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_old_wallet_types.py
"""

import hashlib
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

import flowkit as fk
from electrum import constants
from electrum.bip32 import BIP32Node
from electrum.wallet import restore_wallet_from_text

from bal.core.heirs import only_native_segwit
from bal.core.messages import build_failure_message

PASSWORDS = pytest.mark.parametrize("password", ["flowkit-test", None],
                                    ids=["password", "no_password"])


@pytest.fixture
def session(monkeypatch):
    """Opens a session on a wallet of the given key type."""
    fk.ensure_event_loop()
    opened = []

    def open_session(xtype, password):
        """The session, three will-executors."""

        def make(config, path, _password, label):
            """A wallet of ``xtype`` from the public ``label``: throw-away key."""
            seed = hashlib.sha256(label.encode()).digest()
            xprv = BIP32Node.from_rootseed(seed, xtype=xtype).to_xprv(
                net=constants.BitcoinTestnet)
            return restore_wallet_from_text(
                xprv, path=path, config=config, password=_password)["wallet"]

        monkeypatch.setattr(fk, "_make_wallet", make)
        s = fk.Session(monkeypatch, mode="advanced", password=password)
        s.world.add_executor()
        monkeypatch.setattr(s.wallet, "is_up_to_date", lambda: True)
        opened.append(s)
        return s

    try:
        yield open_session
    finally:
        for s in opened:
            s.close()
        fk.stop_event_loop()


@PASSWORDS
@pytest.mark.parametrize("xtype, first", [("standard", "m"), ("p2wpkh-p2sh", "2")])
def test_old_types_build_nothing_and_say_why(session, xtype, first, password):
    """Addresses of the old types (on testnet: m/n and 2): no copy, the real reason."""
    s = session(xtype, password)
    assert s.wallet.get_receiving_addresses()[0][0] in (first, "n")
    s.prepare()
    assert not s.bw.willitems
    assert s.bw.heirs.last_build_error == "WALLET_TYPE_NOT_SUPPORTED"
    assert s.world.pushed == []


@PASSWORDS
def test_native_segwit_still_builds(session, password):
    """A bc1 wallet: one copy per will-executor, as before."""
    s = session("p2wpkh", password)
    s.prepare()
    assert len(s.bw.willitems) == 3
    assert s.bw.heirs.last_build_error is None


def test_the_message_names_the_addresses():
    """The owner reads what to use, not a will-executor error."""
    text = build_failure_message("WALLET_TYPE_NOT_SUPPORTED", False)
    assert "bc1" in text and "will-executor" not in text


def test_one_old_coin_is_enough(monkeypatch):
    """An imported wallet can mix them: one old coin stops the build."""
    monkeypatch.setattr(constants, "net", constants.BitcoinTestnet)

    class Coin:
        """A coin on ``address``."""

        def __init__(self, address):
            self.address = address

    good = fk.address(1)
    assert only_native_segwit([Coin(good)])
    assert not only_native_segwit([Coin(good), Coin("mipcBbFg9gMiCh81Kj8tqqdgoZub1ZJRfn")])
    assert not only_native_segwit([Coin(None)])
