"""What the Will tab list may delete (CHANGELOG #179).

Owner's ZIP test of #178 (2026-10-22, a will with three delivery dates):
deleting we.divinaly.support's copy of delivery 1 in the List left a hole.
The Check looks for each will-executor in the copies of every delivery
together: the copies of deliveries 2-3 under the other will-executors' copies
hid the missing one, so nothing was rebuilt or sent for it. Its copies with
a 503 to the check (CHECK_FAIL) could be deleted too, though divinaly had
answered "pushed: thx".

Owner's rule ("remove the ways to make mistakes", choice B): the List
deletes only the old versions never sent. Never a copy sent at least once
(also PUSH_FAIL / CHECK_FAIL), never a copy of the will being prepared or in
force (draft, partially signed, signed), whatever the delivery.

``tests/flowkit.py`` wallets with three fake will-executors, each with its
own fee address, and the will with three delivery dates of
``test_multiverse_chain_rebuild.py`` (3 + 9 + 27 items).

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_list_delete_rule.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

import test_multiverse_chain_rebuild as chain
from PyQt6.QtWidgets import QWidget
from test_multiverse_chain_rebuild import _level

from bal.gui.qt.lists import PreviewList

# The sessions of the chain tests (three will-executors, synchronized).
make = chain.make

SIGN = ("sign your will",)


def _list(s):
    """The real Will tab list on the session's window (no toolbar)."""
    s.bw.window.config = s.config
    s.host = QWidget()
    return PreviewList(s.bw, s.host, None)


def _first_level(s):
    """The ids of the VALID copies of delivery 1."""
    will = s.bw.willitems
    return sorted(k for k, w in will.items()
                  if w.get_status("VALID") and _level(k, will) == 1)


def test_no_copy_of_a_sent_will_is_deleted(make):
    """A will in three deliveries, signed, sent and verified: no copy can be
    deleted, also after a failed check (503 clears PUSHED, #147)."""
    s = make()
    chain.three_dates(s)
    s.check()
    lst = _list(s)
    keys = sorted(s.bw.willitems)
    assert len(keys) == 39
    assert not any(lst.is_deletable(k) for k in keys)
    first = _first_level(s)
    copy = s.bw.willitems[first[0]]
    copy.set_status("PUSHED", False)
    copy.set_status("CHECK_FAIL", True)
    assert not lst.is_deletable(first[0])
    lst.delete(keys)
    assert sorted(s.bw.willitems) == keys


def test_a_draft_copy_of_delivery_one_is_not_deleted(make):
    """The owner's hole: the drafts of a will in three deliveries (password
    window closed). Deleting the delivery-1 copy of one will-executor did
    nothing visible at the next Check, which signed and sent the will
    without it. Now it cannot be deleted; the Check signs the whole will."""
    s = make()
    chain.three_dates(s)
    s.check(cancel_on=SIGN)
    lst = _list(s)
    first = _first_level(s)
    assert len(first) == 3
    assert not any(s.bw.willitems[k].get_status("COMPLETE") for k in first)
    assert not any(lst.is_deletable(k) for k in s.bw.willitems)
    lst.delete(first[:1])
    assert sorted(_first_level(s)) == first
    s.check()
    chain.audit(s, {1: 3, 2: 9, 3: 27})


def test_an_old_version_never_sent_is_deleted(make):
    """A copy no longer VALID (replaced) and never sent exists only in this
    wallet: the List deletes it (#129), and only it."""
    s = make()
    chain.three_dates(s)
    s.check(cancel_on=SIGN)
    lst = _list(s)
    first = _first_level(s)
    old = first[0]
    s.bw.willitems[old].set_status("REPLACED", True)
    assert lst.is_deletable(old)
    others = sorted(k for k in s.bw.willitems if k != old)
    lst.delete(sorted(s.bw.willitems))
    assert sorted(s.bw.willitems) == others
