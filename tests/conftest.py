"""Shared pytest fixtures.

Guards every test against cross-file network pollution: several karen7
regtest modules historically flipped ``electrum.constants.net`` to regtest at
import time, which broke unrelated offline tests (e.g. the CLI controller
suite) run in the same pytest process.
"""

import pytest
from electrum import constants

# The network Electrum starts with (mainnet), taken when pytest loads this
# file: before any test module of this directory is imported.
_START_NET = constants.net


@pytest.fixture(autouse=True)
def _restore_network():
    """Run every test on the starting network and put it back after.

    CHANGELOG #97: restoring "the network before the test" was not enough.
    A module that switched to regtest while being imported (the karen7 ones)
    did it during collection, before the first test, so "before" was already
    regtest and 21 tests with mainnet addresses failed in the whole suite
    (alone they passed).
    """
    constants.net = _START_NET
    yield
    constants.net = _START_NET


@pytest.fixture
def regtest_net():
    """Regtest for the tests on regtest data (the karen7 wallet).

    Requested by those tests instead of switching the network at import
    time; ``_restore_network`` (autouse, so it runs first) puts the starting
    network back after the test.
    """
    constants.net = constants.BitcoinRegtest


@pytest.fixture
def yes_to_basic(monkeypatch):
    """Answer Yes to "Go back to BASIC mode?" (#174) without showing it.

    For the tests written before the question that leave ADVANCED and check
    what follows; ``tests/test_basic_mode_confirm.py`` tests the question.
    """
    from bal.gui.qt import common

    monkeypatch.setattr(common, "confirm_basic_mode", lambda parent=None: True)
