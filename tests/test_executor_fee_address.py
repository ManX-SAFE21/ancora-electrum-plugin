"""A will-executor's new fee address (CHANGELOG #141).

Found in the owner's ZIP test of #140 (2026-10-15): a Check asked to sign
again, and the "What changes" box said "No change". The log showed that one
will-executor gave another fee address at ``/info``. Read on the
bal-server sources (2026-10-15): a will-executor with an xpub gives one
address per user IP, so the address changes with the user's IP; at the
push it accepts a transaction paying ANY address it ever gave (with the
fee at least its fixed fee), and bal-pusher broadcasts the stored
transaction as it is, without checking again.

So:

* a copy the will-executor already received stays valid: the Check keeps
  it, asks no signature, sends nothing, and the next Check is clean;
* a copy not received yet is redone with the new address, and the Check
  says why (the owner's sentence);
* when the copies change anyway (an heir added), the received copy is
  built with the new address too.

Wallets, coins and will-executors come from ``tests/flowkit.py`` (three
fake will-executors, each with its own fee address; throw-away test wallet
with a password; fixed clock).

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_executor_fee_address.py
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

import flowkit as fk
from PyQt6.QtWidgets import QApplication

from bal.core import will_changes
from bal.core.messages import (
    excluded_after_build_message,
    executor_terms_message,
    will_change_message,
)
from bal.core.will import Will, WillExecutorNotPresent, WillExecutorTermsChanged

# Kept at module level: an unreferenced application object is destroyed at
# once, and creating a widget afterwards aborts Python.
_APP = QApplication.instance() or QApplication(sys.argv)

# Test value: the password of the throw-away test wallets that have one.
TEST_PASSWORD = "flowkit-test"

WE0, WE1, WE2 = "https://we0.test", "https://we1.test", "https://we2.test"
# The new fee addresses (a new user IP); nobody has their keys.
NEW_ADDR = {WE0: fk.address(5), WE1: fk.address(6), WE2: fk.address(7)}


@pytest.fixture
def flow_session(monkeypatch):
    """``tests/flowkit.py`` sessions with Electrum's asyncio loop."""
    fk.ensure_event_loop()
    made = []

    def make(**kw):
        """Start one session with three will-executors; close it later."""
        s = fk.Session(monkeypatch, **kw)
        s.world.add_executor()
        made.append(s)
        return s

    yield make
    for s in made:
        s.close()
    fk.stop_event_loop()


def _valid(s):
    """The VALID items of the session's will, by txid."""
    return {wid: w for wid, w in s.bw.willitems.items() if w.get_status("VALID")}


def _by_url(s):
    """``{url: [txid, ...]}`` of the VALID items."""
    out = {}
    for wid, w in _valid(s).items():
        out.setdefault((w.we or {}).get("url"), []).append(wid)
    return out


def _passwords(s):
    """How many times the password window was opened in the last Check."""
    return sum(1 for kind, _text in s.user.asked if kind == "password")


def _sent_will(make, down=()):
    """A will signed and sent; the will-executors in ``down`` do not answer
    at the sending, so their copy is signed but not received."""
    s = make(mode="basic", locktime=fk.D0, password=TEST_PASSWORD)
    s.world.down = set(down)
    s.check()
    s.world.down = set()
    assert len(_valid(s)) == 3
    for w in _valid(s).values():
        assert w.get_status("COMPLETE")
        assert Will.received(w) == (w.we["url"] not in down)
    return s


def _move(s, *urls):
    """The will-executors ``urls`` give a new fee address (new user IP)."""
    for url in urls:
        s.world.executors[url]["address"] = NEW_ADDR[url]


def _assert_clean(s):
    """A Check that finds the will in force: nothing signed, nothing sent."""
    before = dict(_valid(s))
    pushed = len(s.world.pushed)
    dlg = s.check()
    assert _passwords(s) == 0, s.user.asked
    assert len(s.world.pushed) == pushed
    assert set(_valid(s)) == set(before)
    assert "changes" not in dlg._report().snapshot()
    return dlg


# ---------------------------------------------------------------------------
# The core rules
# ---------------------------------------------------------------------------

def test_terms_change_reads_address_then_fee():
    """``Will.terms_change``: "address", "fee" or None."""
    we = {"url": WE0, "address": "a1", "base_fee": 1000}
    assert Will.terms_change(we, dict(we)) is None
    assert Will.terms_change(we, dict(we, address="a2")) == "address"
    assert Will.terms_change(we, dict(we, address="a2", base_fee=900)) == "address"
    assert Will.terms_change(we, dict(we, base_fee=900)) == "fee"
    assert Will.terms_change(we, None) is None
    assert Will.terms_change(we, dict(we, url=WE1, address="a2")) is None


def test_messages_name_the_host_and_the_reason():
    """The owner's sentence (2026-10-15), with the host only."""
    text = executor_terms_message("https://we.bitcoin-after.life", "address")
    assert text.startswith("we.bitcoin-after.life has a new Bitcoin address")
    assert "not yet confirmed as received" in text
    assert "signed again" in text
    assert "new fee" in executor_terms_message(WE0, "fee")
    e = WillExecutorTermsChanged(WE2, "address")
    assert isinstance(e, WillExecutorNotPresent)
    assert str(e) == WE2
    assert will_change_message(e) == executor_terms_message(WE2, "address")


# ---------------------------------------------------------------------------
# The flows
# ---------------------------------------------------------------------------

def test_received_copy_stays_after_new_address(flow_session):
    """Every will-executor received its copy, then two give a new address:
    nothing to sign or send, and the next Check is clean too."""
    s = _sent_will(flow_session)
    _move(s, WE0, WE2)
    _assert_clean(s)
    _assert_clean(s)


def test_received_copy_stays_after_new_fee(flow_session):
    """The same with a new fee only (lower here: the copy already paid)."""
    s = _sent_will(flow_session)
    s.world.executors[WE1]["base_fee"] = 900
    _assert_clean(s)
    _assert_clean(s)


def test_copy_not_received_is_redone_with_the_reason(flow_session):
    """WE2 never received its copy and now gives a new address: only its
    copy is redone (signed again), with the owner's sentence; the others
    are kept. Then the Check is clean."""
    s = _sent_will(flow_session, down=(WE2,))
    old = _by_url(s)
    _move(s, WE2)
    pushed = len(s.world.pushed)
    dlg = s.check()
    now = _by_url(s)
    assert now[WE0] == old[WE0] and now[WE1] == old[WE1]
    assert now[WE2] != old[WE2]
    # The old copy (old address) is replaced and not sent: only the new one.
    stale = s.bw.willitems[old[WE2][0]]
    assert stale.get_status("REPLACED") and not stale.get_status("VALID")
    assert [(u, wid) for u, wid, _raw in s.world.pushed[pushed:]] == [(WE2, now[WE2][0])]
    item = s.bw.willitems[now[WE2][0]]
    assert item.we["address"] == NEW_ADDR[WE2]
    assert Will.received(item)
    assert _passwords(s) == 1
    reason = executor_terms_message(WE2, "address")
    assert any(reason in str(line) for line in dlg.history), dlg.history
    rows = dlg._report().snapshot().get("changes") or []
    assert ["Will-executor fee", reason, None] in [list(r) for r in rows], rows
    _assert_clean(s)


def test_received_copy_kept_while_another_is_redone(flow_session):
    """WE0 received its copy, WE2 did not; both give a new address. WE2's
    copy is redone with its new address; WE0's copy is kept as it is (its
    old address, no new signature for it)."""
    s = _sent_will(flow_session, down=(WE2,))
    old = _by_url(s)
    _move(s, WE0, WE2)
    s.check()
    now = _by_url(s)
    assert now[WE0] == old[WE0] and now[WE1] == old[WE1]
    assert s.bw.willitems[now[WE0][0]].we["address"] == fk.WE_ADDR[0]
    assert s.bw.willitems[now[WE2][0]].we["address"] == NEW_ADDR[WE2]
    _assert_clean(s)


def test_heir_added_uses_the_new_address(flow_session):
    """The copies change anyway (an heir added): the received copy of WE0
    is built with its new address."""
    s = _sent_will(flow_session)
    _move(s, WE0)
    s.set_heirs(alice="50%", bob="30%", carol="20%")
    s.check()
    now = _by_url(s)
    assert s.bw.willitems[now[WE0][0]].we["address"] == NEW_ADDR[WE0]
    assert s.bw.willitems[now[WE1][0]].we["address"] == fk.WE_ADDR[1]
    _assert_clean(s)


def _new_date(s, days):
    """The delivery date moved ``days`` earlier, for the will and its heirs
    (as the Will tab writes them)."""
    s.set_date(fk.D0 - days * fk.DAY)
    for name in list(s.bw.heirs.keys()):
        row = s.bw.heirs[name]
        s.bw.heirs[name] = [row[0], row[1], s.bw.will_settings["locktime"]]


def _excluded_rows(dlg):
    """The "Will-Executor excluded" rows of a Check."""
    return [str(line) for line in dlg.history if "Will-Executor excluded" in str(line)]


def test_new_date_after_new_address_uses_it(flow_session):
    """CHANGELOG #176, the owner's ZIP test of #172: every copy received,
    then WE0 gives a new address and the delivery date moves earlier. The
    copies are new (new date), so WE0's is built with its new address; the
    Check no longer calls WE0 "excluded: Skipped" right after the build,
    and the next Check is clean."""
    s = _sent_will(flow_session)
    _move(s, WE0)
    _new_date(s, 10)
    dlg = s.check()
    assert _excluded_rows(dlg) == [], dlg.history
    now = _by_url(s)
    for url in (WE0, WE1, WE2):
        item = s.bw.willitems[now[url][0]]
        assert int(item.tx.locktime) == fk.D0 - 10 * fk.DAY
        assert Will.received(item)
    assert s.bw.willitems[now[WE0][0]].we["address"] == NEW_ADDR[WE0]
    assert s.bw.willitems[now[WE1][0]].we["address"] == fk.WE_ADDR[1]
    _assert_clean(s)


def test_same_payments_needs_the_same_date(flow_session):
    """``Will.same_payments``: the same coins and outputs on another date
    are not the copy the will-executor holds (#176)."""
    s = _sent_will(flow_session)
    w = _valid(s)[_by_url(s)[WE0][0]]
    assert Will.same_payments(w.tx, s.bw.willitems, WE0)

    class _Moved:
        """``w.tx`` with another locktime."""

        locktime = int(w.tx.locktime) - fk.DAY

        def inputs(self):
            """The same inputs."""
            return w.tx.inputs()

        def outputs(self):
            """The same outputs."""
            return w.tx.outputs()

    assert not Will.same_payments(_Moved(), s.bw.willitems, WE0)


def test_excluded_after_build_names_who_and_why():
    """The row's text (owner, 2026-10-21): the owner's #141 sentence for a
    new address or fee, else the host and "not in the will just
    prepared"; never "Skipped"."""
    e = WillExecutorTermsChanged("https://we.bitcoin-after.life", "address")
    assert excluded_after_build_message(e) == executor_terms_message(
        "https://we.bitcoin-after.life", "address")
    assert excluded_after_build_message(WillExecutorTermsChanged(WE1, "fee")) == (
        executor_terms_message(WE1, "fee"))
    assert excluded_after_build_message(WillExecutorNotPresent(WE2)) == (
        "we2.test: not in the will just prepared")


def test_excluded_row_after_build_is_an_orange_warning(flow_session, monkeypatch):
    """Should the Check still find a will-executor missing right after a
    build, the row names it in orange (the flow goes on: it signs and
    sends) and the report lists it among the warnings (#176)."""
    s = _sent_will(flow_session)
    _new_date(s, 10)
    built = {"done": False}
    build_will, check_will = s.bw.build_will, s.bw.check_will

    def build_then_mark(*a, **kw):
        """The real build, then the next check_will fails."""
        out = build_will(*a, **kw)
        built["done"] = True
        return out

    def check_after_build(*a, **kw):
        """WE2 missing on the will just prepared, once."""
        if built["done"]:
            built["done"] = False
            raise WillExecutorNotPresent(WE2)
        return check_will(*a, **kw)

    monkeypatch.setattr(s.bw, "build_will", build_then_mark)
    monkeypatch.setattr(s.bw, "check_will", check_after_build)
    dlg = s.check()
    rows = _excluded_rows(dlg)
    assert len(rows) == 1, dlg.history
    assert "we2.test: not in the will just prepared" in rows[0]
    assert "Skipped" not in rows[0]
    assert dlg.COLOR_WARNING in rows[0] and dlg.COLOR_ERROR not in rows[0]
    warnings = dlg._report().snapshot()["boxes"]["warnings"]
    assert any("we2.test: not in the will just prepared" in str(w) for w in warnings), warnings


# ---------------------------------------------------------------------------
# The "What changes" box
# ---------------------------------------------------------------------------

class _Item:
    """A will item with only what ``will_changes._terms`` reads."""

    def __init__(self, url, address, received):
        self.we = {"url": url, "address": address, "base_fee": 1000}
        self._received = received

    def get_status(self, flag):
        """VALID and, when received, PUSHED."""
        return flag == "VALID" or (flag == "PUSHED" and self._received)


def _sides(old_items, new_items):
    """Two sides with only executors and terms."""
    old = will_changes.WillSide(executors=sorted({w.we["url"] for w in old_items.values()}))
    new = will_changes.WillSide(executors=sorted({w.we["url"] for w in new_items.values()}))
    old.terms = will_changes._terms(old_items)
    new.terms = will_changes._terms(new_items)
    return old, new


def test_box_row_for_a_copy_not_received():
    """The box no longer says "No change": the owner's sentence."""
    old, new = _sides({"a": _Item(WE2, "x", False)}, {"b": _Item(WE2, "y", False)})
    rows = will_changes.change_rows(will_changes.will_changes(old, new))
    assert rows == [("Will-executor fee", executor_terms_message(WE2, "address"), None)]


def test_box_row_for_a_received_copy_changed_anyway():
    """A received copy rebuilt for other reasons: it uses the new address."""
    old, new = _sides({"a": _Item(WE0, "x", True)}, {"b": _Item(WE0, "y", False)})
    rows = will_changes.change_rows(will_changes.will_changes(old, new))
    assert rows == [("Will-executor fee",
                     "we0.test has a new Bitcoin address to receive its fee: "
                     "the new copy uses it.", None)]


def test_box_settings_side_has_no_terms():
    """The wizard's side (settings) knows no addresses: no row."""
    old, _new = _sides({"a": _Item(WE0, "x", False)}, {})
    new = will_changes.side_from_settings({}, None, [WE0], None)
    assert all(c.kind != will_changes.EXECUTOR_TERMS
               for c in will_changes.will_changes(old, new))
