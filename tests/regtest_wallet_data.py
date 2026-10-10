"""A regtest test wallet made in code, in place of tests/karen7 (CHANGELOG #98).

``tests/karen7`` was the BAL author's regtest wallet, not in git
(``.gitignore``), so the tests that read it were skipped (CHANGELOG #97).
They read only two parts of it, both public, never a key:

* ``heirs``: six heirs with karen7's names, two of them OP_RETURN heirs,
  ``{name: [address, amount, locktime]}``;
* ``txo``: the coins, ``{txid: {address: {index: [satoshi, spent]}}}`` as in
  an Electrum wallet file.

Both are made here. Addresses are valid regtest addresses made from a hash of
a label: nobody holds a key for them, and the tests never sign. Txids are
hashes too. No wallet file, no seed, no key; the same data at every run.

Not a test module (no ``test_`` prefix): pytest does not collect it.
"""

import copy
import hashlib

from electrum import constants
from electrum.bitcoin import hash_to_segwit_addr


def _hash(label):
    """32 bytes that depend only on ``label`` (so the data never changes)."""
    return hashlib.sha256("safe21-heartbeat test: {}".format(label).encode()).digest()


def address(label):
    """A valid regtest P2WPKH address for ``label``; nobody has its key."""
    return hash_to_segwit_addr(_hash(label)[:20], witver=0, net=constants.BitcoinRegtest)


def txid(label):
    """A made-up transaction id for ``label`` (64 hex characters)."""
    return _hash("tx " + label).hex()


def op_return(text):
    """An OP_RETURN heir "address": the prefix BAL uses, then hex data."""
    return "OP_RETURN:" + text.encode().hex()


# karen7's heir names, kept so the tests that list them stay as they were.
# Percentages, a fixed amount and two OP_RETURN heirs, all with the same
# delivery date: the will then spends the whole wallet in one transaction with
# no change. Two dates chain a second transaction on the change of the first,
# which the tests with mocked transactions (test_e5,
# test_reproduce_none_type) cannot follow; they passed with karen7, so its
# heirs must have left no change either (karen7 itself was never seen here).
HEIRS = {
    "aaaa": [address("heir aaaa"), "30%", "1y"],
    "lucia": [address("heir lucia"), "20%", "1y"],
    "mario": [address("heir mario"), "150000", "1y"],
    "mario2": [address("heir mario2"), "50%", "1y"],
    "op_return": [op_return("safe21 test will 1"), "0", "1y"],
    "op_return2": [op_return("safe21 test will 2"), "0", "1y"],
}

# (label, output index, satoshi, spent): three coins to spend and one
# already spent, which every reader must leave out.
COINS = (
    ("coin a", 0, 1_000_000, False),
    ("coin b", 1, 2_500_000, False),
    ("coin c", 0, 600_000, False),
    ("coin spent", 0, 400_000, True),
)


def wallet_data():
    """The two parts of karen7 the tests read, as a fresh copy."""
    txo = {}
    for label, index, value, spent in COINS:
        txo.setdefault(txid(label), {})[address("wallet " + label)] = {
            str(index): [value, spent]
        }
    return {"heirs": copy.deepcopy(HEIRS), "txo": txo}
