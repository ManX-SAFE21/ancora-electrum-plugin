"""#203: the Check says nothing "changed" on a first will.

The golden files show the first will end to end (the row says Ok and the
"What changed" box is empty) and the executed will keeping the sentence.
These tests fix the rule itself: only no valid transaction AND no copy.
"""

from types import SimpleNamespace

from bal.core.will import NotCompleteWillException
from bal.gui.qt.dialogs import BalBuildWillDialog


def _first_will(willitems, error):
    """``_first_will`` on a stand-in holding only what it reads."""
    dialog = SimpleNamespace(bal_window=SimpleNamespace(willitems=willitems))
    return BalBuildWillDialog._first_will(dialog, error)


def test_no_copy_and_no_valid_transaction_is_a_first_will():
    assert _first_will({}, NotCompleteWillException())


def test_copies_in_the_wallet_keep_the_sentence():
    """With copies, none valid, "no valid transaction" is true: it stays."""
    assert not _first_will({"txid": object()}, NotCompleteWillException())


def test_a_coin_not_covered_is_not_a_first_will():
    """The other plain exception has its own sentence, also with no copy."""
    assert not _first_will({}, NotCompleteWillException("coin not covered"))
