"""The derived state of a will item: draft, partially signed, in force (CHANGELOG #128).

Owner's case 2026-10-12 (known issue "P2"): a will signed and sent for
2027-02-27 22:00 UTC; the fee changed and the password window was closed,
twice. Each time an UNSIGNED draft became the will the next rebuild had to
beat (one day earlier), and the delivery date crept back to 02-25 although
the will-executors still held the will of 02-27.

#128 classifies the items, without storing anything new in the wallet
(``bal.core.will_state``, pure, used by the build, the Will tab and the
period policy of #127):

* a DRAFT is VALID, never signed (not COMPLETE, no partial signature) and
  never sent (not PUSHED): it exists only in this wallet, so dropping it is
  free;
* a PARTIALLY SIGNED item has some but not all of its signatures
  (multisig): never a draft, never dropped (a co-signer may finish it);
* the will IN FORCE is made of the items signed (COMPLETE) and/or sent
  (PUSHED), VALID or REPLACED, not INVALIDATED / CONFIRMED / MEMPOOL and
  still executable (no input spent by a broadcast transaction). On a wallet
  that is not synchronized the executability is not known, so such an item
  counts as in force (the safe side: the new will is dated against it).

The same rules apply to the ``ItemView`` of ``bal.core.delivery``.

Run (standalone or with pytest):
    PYTHONPATH=electrum-src python3 -m pytest tests/test_core_will_state.py
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from bal.core import delivery as dv
from bal.core import will_state as ws

FLAGS = ("VALID", "COMPLETE", "PUSHED", "CHECKED", "REPLACED", "INVALIDATED",
         "CONFIRMED", "MEMPOOL", "PARTIALLY_SIGNED")


class Item:
    """A stand-in for ``WillItem``: the status flags and the signature count."""

    def __init__(self, *flags, sigs_have=0):
        self.STATUS = {f: [f, f in flags] for f in FLAGS}
        self.sigs_have = sigs_have

    def get_status(self, flag):
        """``WillItem.get_status``."""
        return self.STATUS[flag][1]


def executable():
    """No input of the item is spent."""
    return True


def spent():
    """An input of the item is spent by a broadcast transaction."""
    return False


def test_a_valid_item_never_signed_nor_sent_is_a_draft():
    draft = Item("VALID")
    assert ws.is_draft(draft)
    assert ws.classify(draft, True, executable) == ws.DRAFT


@pytest.mark.parametrize("flags, sigs", [
    (("VALID", "PARTIALLY_SIGNED"), 0),
    (("VALID",), 1),
])
def test_a_partially_signed_item_is_never_a_draft(flags, sigs):
    """Its own state, whether the flag or only the count says it."""
    item = Item(*flags, sigs_have=sigs)
    assert ws.is_partially_signed(item)
    assert not ws.is_draft(item)
    assert ws.classify(item, True, executable) == ws.PARTIALLY_SIGNED


@pytest.mark.parametrize("flags", [
    ("VALID", "COMPLETE"),
    ("VALID", "COMPLETE", "PUSHED", "CHECKED"),
    ("VALID", "PUSHED"),
    ("REPLACED", "COMPLETE", "PUSHED"),
    ("REPLACED", "PUSHED"),
])
def test_signed_or_sent_and_still_executable_is_in_force(flags):
    item = Item(*flags)
    assert not ws.is_draft(item)
    assert ws.is_in_force(item, True, executable)
    assert ws.classify(item, True, executable) == ws.IN_FORCE


def test_a_spent_item_is_not_in_force_on_a_synchronized_wallet():
    """Its coins went elsewhere: it can never be executed."""
    item = Item("REPLACED", "COMPLETE", "PUSHED")
    assert not ws.is_in_force(item, True, spent)
    assert ws.classify(item, True, spent) == ws.REPLACED


def test_not_synchronized_counts_as_in_force():
    """The spends may not be known yet: the safe side."""
    item = Item("REPLACED", "COMPLETE", "PUSHED")
    assert ws.is_in_force(item, False, spent)


@pytest.mark.parametrize("flag, state", [
    ("INVALIDATED", ws.INVALIDATED),
    ("CONFIRMED", ws.EXECUTED),
    ("MEMPOOL", ws.EXECUTED),
])
def test_invalidated_or_executed_is_never_in_force(flag, state):
    item = Item("COMPLETE", "PUSHED", flag)
    assert not ws.is_in_force(item, False, executable)
    assert ws.classify(item, False, executable) == state


def test_an_unsigned_replaced_item_is_only_replaced():
    """A draft that a later draft replaced: never signed, never sent."""
    item = Item("REPLACED")
    assert not ws.is_draft(item)
    assert not ws.is_in_force(item, False, executable)
    assert ws.classify(item, True, executable) == ws.REPLACED


def test_the_drafts_of_a_will():
    will = {
        "d": Item("VALID"),
        "p": Item("VALID", "PARTIALLY_SIGNED"),
        "s": Item("VALID", "COMPLETE"),
        "r": Item("REPLACED", "COMPLETE", "PUSHED"),
    }
    assert ws.draft_ids(will) == ["d"]


def test_the_item_views_follow_the_same_rules():
    """``bal.core.delivery.ItemView`` (the pure rules of #123/#127)."""
    draft = dv.item_view("d", 1882656000, 13, {"VALID": True})
    partial = dv.item_view("p", 1882656000, 13,
                           {"VALID": True, "PARTIALLY_SIGNED": True})
    sent = dv.item_view("s", 1882656000, 13, {"REPLACED": True, "PUSHED": True})
    assert ws.is_draft(draft)
    assert ws.is_partially_signed(partial) and not ws.is_draft(partial)
    assert ws.is_in_force(sent, True, executable)


def test_a_draft_is_not_a_will_in_force_for_the_period_policy():
    """#127 kept the period date while any VALID item existed; a draft
    was never in force (it does not lock the form either), so it no longer
    holds the period date back. A signed or a partially signed VALID item
    still does."""
    def view(**flags):
        return dv.item_view("a", 1882656000, 13, flags)

    assert not dv.will_in_force([view(VALID=True)], True, executable)
    assert dv.will_in_force([view(VALID=True, COMPLETE=True)], True, executable)
    assert dv.will_in_force([view(VALID=True, PARTIALLY_SIGNED=True)], True,
                            executable)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
