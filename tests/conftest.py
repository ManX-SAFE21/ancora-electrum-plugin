"""Shared pytest fixtures.

Guards every test against cross-file network pollution: several karen7
regtest modules historically flipped ``electrum.constants.net`` to regtest at
import time, which broke unrelated offline tests (e.g. the CLI controller
suite) run in the same pytest process.
"""

import pytest
from electrum import constants


@pytest.fixture(autouse=True)
def _restore_network():
    """Snapshot ``constants.net`` before each test and restore it after."""
    prev = constants.net
    yield
    constants.net = prev
