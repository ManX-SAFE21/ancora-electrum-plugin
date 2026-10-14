"""Unsigned copies of deselected will-executors are never signed (#135).

Owner's ZIP test (his real sequence): a will in force, sent and verified
by 3 will-executors; he selected 2 more, the Check said "Will-executors
from 3 to 5: added …" and asked "Sign your will": Cancel (the 2 new copies
stayed unsigned drafts). He deselected those 2 and pressed Check: the
window said "No change: the will stays the same as the one in force" but
still asked "Sign your will", and signed copies that are never sent.

Root cause: the Check decided "to sign" from every VALID item not signed,
without the #134 rule (a copy for a will-executor not selected now that
never received the will is "deselected", not "to sign"), and signed every
VALID item. Now those leftovers are not counted and not signed (left as
they are: unsigned, only in this wallet, no will-executor holds them).

``tests/flowkit.py`` wallets: five fake will-executors here (as the owner),
each with its own fee address; the test password; a fixed clock; the wallet
counts as synchronized.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_deselected_leftovers.py
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

import flowkit as fk
from PyQt6.QtWidgets import QApplication

_APP = QApplication.instance() or QApplication(sys.argv)

TEST_PASSWORD = "flowkit-test"
NEW = ("https://we3.test", "https://we4.test")


def add_executor(s, url, n):
    """One more fake will-executor, selected, with its own fee address."""
    s.world.executors[url] = {
        "url": url, "selected": True, "base_fee": 1000,
        "address": fk.address(20 + n), "status": "200", "info": "fake"}


@pytest.fixture
def owner_case(monkeypatch):
    """The will in force at 3; 2 more selected, Check, Cancel at "Sign your
    will"; then the 2 deselected again. Returns the session."""
    fk.ensure_event_loop()
    s = fk.Session(monkeypatch, mode="basic", password=TEST_PASSWORD)
    s.world.add_executor()
    monkeypatch.setattr(s.wallet, "is_up_to_date", lambda: True)
    try:
        s.check()                                       # in force at 3
        for n, url in enumerate(NEW):
            add_executor(s, url, n)
        dlg = s.check(cancel_on=("sign your will",))
        rows = [list(r) for r in dlg._report().snapshot()["changes"]]
        assert rows[0][0] == "Will-executors" and rows[0][1].startswith("from 3 to <b>5</b>")
        for url in NEW:
            s.world.executors[url]["selected"] = False
        yield s
    finally:
        s.close()
        fk.stop_event_loop()


def leftovers(s):
    """The VALID items of the deselected will-executors."""
    return {wid: w for wid, w in s.bw.willitems.items()
            if (w.we or {}).get("url") in NEW and w.get_status("VALID")}


def test_owner_sequence_signs_nothing(owner_case):
    """No password, nothing signed or sent, the will is complete ("ok"),
    no "What changes" box; the leftovers stay unsigned and unsent."""
    s = owner_case
    before = {wid: dict((k, v[1]) for k, v in w.STATUS.items())
              for wid, w in s.bw.willitems.items()}
    s.user.asked.clear()
    pushed = len(s.world.pushed)
    snap = s.check()._report().snapshot()
    assert [k for k, _t in s.user.asked] == []                  # no password
    assert len(s.world.pushed) == pushed
    steps = {name: state for name, state, _note in snap["steps"]}
    assert steps["sign"] == "skip" and steps["send"] == "skip"
    assert snap["result"][0] == "ok"
    # No signing step, so no "What changes" box either (it is shown at the
    # signature): the box and the signing can no longer disagree.
    assert "changes" not in snap
    left = leftovers(s)
    assert len(left) == 2
    assert all(not w.get_status("COMPLETE") and not w.get_status("PUSHED")
               for w in left.values())
    # Nothing signed or sent was touched.
    for wid, w in s.bw.willitems.items():
        if before[wid].get("COMPLETE") or before[wid].get("PUSHED"):
            assert dict((k, v[1]) for k, v in w.STATUS.items()) == before[wid]


def test_second_check_is_clean(owner_case):
    """The next Check too: no password, no "What changes" box, ok."""
    s = owner_case
    s.check()
    s.user.asked.clear()
    snap = s.check()._report().snapshot()
    assert s.user.asked == []
    assert "changes" not in snap and snap["result"][0] == "ok"


def test_selected_again_is_signed_and_sent(owner_case):
    """A deselected will-executor selected again: its copy is signed and
    sent (the leftover was kept for that)."""
    s = owner_case
    s.world.executors[NEW[0]]["selected"] = True
    pushed = len(s.world.pushed)
    s.check()
    assert ["password", "Sign your will"] in s.user.asked
    assert [u for u, _t, _r in s.world.pushed[pushed:]] == [NEW[0]]
