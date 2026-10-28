"""The will by versions (CHANGELOG #129): ``bal.core.will_versions``.

The Will tab lists the will by VERSIONS (one per build) instead of one flat
row per transaction. Nothing is stored: the versions are derived from the
will items at every refresh. Here:

* realistic histories made with ``tests/flowkit.py`` (three fake
  will-executors, each with its own fee address; throw-away test wallets
  with the test password; fixed clock): sign; a new coin; a fee change
  cancelled at the password (draft); an earlier date; a later date with the
  paid invalidation and the rebuild; two coins; Exclude; a will-executor
  added later; the backup transaction; the #128 draft dropped and the will
  in force brought back; multiverse;
* small synthetic wills for the edge cases: the same will-executor twice in
  one content (split by build time), items without heirs or without time,
  a wallet not synchronized, a spender that is not an invalidation, errors
  from the wallet, the display order with "Show all history", and the
  time taken by a will of more than a thousand items;
* #200, a new will on a coin that came back from an executed copy (the
  owner's case end to end is in ``tests/test_new_will_after_executed.py``):
  the edges of ``new_will_links`` (unknown or unreadable data, a
  block-height date, the same build, a father not on the chain, a copy
  rebuilt before the father's date, the content of a kept delivery, a copy
  that pays only later deliveries, a loop in corrupted data), the Graph's
  own rule, and a thousand items whose deliveries 1 were executed (with
  and without copies built after the execution).

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_core_will_versions.py
"""

import os
import sys
import time
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

import flowkit as fk
from electrum.transaction import TxOutpoint
from PyQt6.QtWidgets import QApplication

from bal.core import will_versions as wv

# Kept at module level: flowkit's window needs a Qt application.
_APP = QApplication.instance() or QApplication(sys.argv)

# Test value: the password of the throw-away test wallets.
TEST_PASSWORD = "flowkit-test"
D = fk.ts(2027, 2, 27, 22)
SIGN = ("sign your will",)


@pytest.fixture
def make(monkeypatch):
    """``tests/flowkit.py`` sessions with three will-executors, synchronized."""
    fk.ensure_event_loop()
    made = []

    def _make(mode="basic", three=True, **kw):
        """One session; ``three=False`` starts with two will-executors."""
        kw.setdefault("password", TEST_PASSWORD)
        kw.setdefault("fee", 21)
        kw.setdefault("locktime", D)
        kw.setdefault("threshold", "-30d")
        s = fk.Session(monkeypatch, mode=mode, **kw)
        if three:
            s.world.add_executor()
        # The flowkit wallet is offline: synchronized for the #128 rules.
        monkeypatch.setattr(s.wallet, "is_up_to_date", lambda: True)
        made.append(s)
        return s

    yield _make
    for s in made:
        s.close()
    fk.stop_event_loop()


def _history(s):
    """The versions of the session's will, with its wallet."""
    return wv.build_history(s.bw.willitems, s.wallet)


def _summary(s, history=None):
    """``[(number, state, n roots, delivery day)]`` in display order."""
    history = history or _history(s)
    rows, _hidden = wv.display_rows(history, show_all=True)
    return [
        (o.number, o.state, len(o.roots), fk.fmt(o.locktime))
        if kind == "version" else ("invalidation", o.fee is not None, o.labelled)
        for kind, o in rows
    ]


def _urls(s, version):
    """The will-executor URLs of a version's roots (None: backup)."""
    return sorted(str((s.bw.willitems[w].we or {}).get("url")) for w in version.roots)


# ------------------------------------------------------------ flowkit

def test_a_realistic_history(make):
    """Sign; a new coin; fee 22 cancelled at the password (draft); fee 21
    and ten days earlier; a later date (paid invalidation + rebuild)."""
    s = make()
    s.check()
    assert _summary(s) == [(1, "in_force", 3, "2027-02-27")]

    s.clock.travel(1)
    s.add_coin(250_000)
    s.check()
    # The new coin anticipates one day; the first will is still held by
    # the will-executors and still executable (#128).
    assert _summary(s) == [
        (2, "in_force", 3, "2027-02-26"),
        (1, "replaced_held", 3, "2027-02-27"),
    ]

    s.clock.travel(1)
    s.set_fee(22)
    s.check(closing=True, cancel_on=SIGN)
    # The draft is on top and unnumbered; the numbers do not move.
    history = _history(s)
    assert _summary(s, history) == [
        (None, "draft", 3, "2027-02-25"),
        (2, "in_force", 3, "2027-02-26"),
        (1, "replaced_held", 3, "2027-02-27"),
    ]
    # Received by no will-executor: the list says "not sent".
    assert [v.executors_sent for v in history.versions] == [3, 3, 0]

    s.clock.travel(1)
    s.set_fee(21)
    s.set_date(D - 10 * fk.DAY)
    s.check()
    # The draft was dropped by the build; the new version is #3.
    assert _summary(s) == [
        (3, "in_force", 3, "2027-02-17"),
        (2, "replaced_held", 3, "2027-02-26"),
        (1, "replaced_held", 3, "2027-02-27"),
    ]

    s.clock.travel(1)
    s.set_date(D + 30 * fk.DAY)
    s.check()
    history = _history(s)
    # The paid invalidation ended #1, #2 and #3: its row is right above #3
    # (between the versions it ended and the next one).
    assert _summary(s, history) == [
        (4, "in_force", 3, "2027-03-29"),
        ("invalidation", True, True),
        (3, "invalidated", 3, "2027-02-17"),
        (2, "invalidated", 3, "2027-02-26"),
        (1, "invalidated", 3, "2027-02-27"),
    ]
    (inv,) = history.invalidations
    assert inv.txid == s.world.broadcast_log[0][1]
    assert inv.fee == s.wallet.adb.get_tx_fee(inv.txid) > 0
    assert inv.confirmed is False            # flowkit: in the mempool
    assert len(inv.ended) == 3
    by_vid = history.by_vid()
    assert by_vid[inv.anchor].number == 3
    # Every version holds the three will-executors, verified.
    for v in history.versions:
        assert _urls(s, v) == ["https://we0.test", "https://we1.test", "https://we2.test"]
        assert (v.executors_verified, v.executors_total) == (3, 3)
        assert [h[:2] for h in v.heirs] == [("alice", "60%"), ("bob", "40%")]
        assert v.fee_rate == 21

    s.clock.travel(1)
    s.check()
    assert _summary(s) == _summary(s, history)


def test_two_coins_one_transaction_per_executor(make):
    s = make()
    s.add_coin(250_000)
    s.check()
    (v,) = _history(s).versions
    assert v.state == "in_force" and v.number == 1
    assert len(v.roots) == 3
    assert all(len(s.bw.willitems[w].tx.inputs()) == 2 for w in v.roots)


def test_exclude_keeps_one_version_with_two_copies(make):
    s = make()
    s.check()
    s.clock.travel(1)
    s.set_fee(22)
    s.world.down.add("https://we1.test")
    s.check()
    assert _summary(s) == [
        (2, "in_force", 3, "2027-02-26"),
        (1, "replaced_held", 3, "2027-02-27"),
    ]
    s.redo(lambda dlg: dlg._exclude_executor("https://we1.test"))
    history = _history(s)
    # #178 (owner): Exclude keeps every copy sent at least once; we1's copy
    # was sent (no answer: PUSH_FAIL), it may be on its server, so it
    # stays in the version in force (before: dropped, 2 copies).
    assert _summary(s, history) == [
        (2, "in_force", 3, "2027-02-26"),
        (1, "replaced_held", 3, "2027-02-27"),
    ]
    current = history.by_vid()[history.current]
    assert _urls(s, current) == ["https://we0.test", "https://we1.test",
                                 "https://we2.test"]
    pushed_to_we1 = [w for w in current.roots
                     if s.bw.willitems[w].we["url"] == "https://we1.test"]
    assert [s.bw.willitems[w].get_status("PUSH_FAIL") for w in pushed_to_we1] == [True]


def test_an_executor_added_later_does_not_split_the_version(make):
    """The #122 reuse keeps the old items with their old build time: the
    version is found by its content, not by the time."""
    s = make(three=False)
    s.check()
    s.clock.travel(1)
    s.world.add_executor()
    s.check()
    history = _history(s)
    (v,) = history.versions
    times = {s.bw.willitems[w].time for w in v.roots}
    assert len(times) == 2                   # two builds...
    assert v.number == 1 and v.state == "in_force"   # ...one version
    assert _urls(s, v) == ["https://we0.test", "https://we1.test", "https://we2.test"]


def test_the_backup_transaction_is_a_copy_of_the_version(make):
    s = make()
    s.plugin.NO_WILLEXECUTOR.set(True)
    s.bw.no_willexecutor = True
    s.check()
    (v,) = _history(s).versions
    assert _urls(s, v) == ["None", "https://we0.test", "https://we1.test",
                           "https://we2.test"]
    # The backup is listed last and is not counted among the executors.
    assert (s.bw.willitems[v.roots[-1]].we is None)
    assert (v.executors_verified, v.executors_total) == (3, 3)
    assert v.state == "in_force"


def test_draft_dropped_and_will_in_force_brought_back(make):
    """#128, the owner's case: fee 20 cancelled at the password (a draft),
    then the fee and the date back: the draft disappears and the will in
    force comes back, still #1."""
    s = make()
    s.check()
    first = set(s.bw.willitems)
    s.set_fee(20)
    s.check(closing=True, cancel_on=SIGN)
    assert _summary(s) == [
        (None, "draft", 3, "2027-02-26"),
        (1, "in_force", 3, "2027-02-27"),
    ]
    s.set_fee(21)
    s.set_date(D)
    s.check(cancel_on=SIGN)
    history = _history(s)
    assert _summary(s, history) == [(1, "in_force", 3, "2027-02-27")]
    assert set(history.versions[0].roots) == first


def test_multiverse_instalments_stay_under_their_root(make):
    """A table of deliveries (#168; BAL's multiverse option before): alice
    at D, bob one year later. Each instalment-1 transaction has its three
    children; one version, three roots."""
    s = make()
    s.set_instalments((D, {"alice": "60%", "bob": "0%"}),
                      (D + 365 * fk.DAY, {"alice": "0%", "bob": "40%"}))
    s.check()
    history = _history(s)
    (v,) = history.versions
    assert len(s.bw.willitems) == 12
    assert len(v.roots) == 3 and len(v.items) == 12
    assert all(len(v.children[r]) == 3 for r in v.roots)
    assert v.state == "in_force" and v.number == 1
    assert fk.fmt(v.locktime) == "2027-02-27"
    assert [h[0] for h in v.heirs] == ["alice", "bob"]
    # Nothing is written on the items (father is stored in the wallet).
    assert history.other is None


def test_nothing_is_written(make):
    """Display only: the items and the wallet's will stay as they were."""
    s = make()
    s.check()
    before = {w: (str(i.to_dict()), i.father) for w, i in s.bw.willitems.items()}
    _history(s)
    after = {w: (str(i.to_dict()), i.father) for w, i in s.bw.willitems.items()}
    assert before == after


def test_discarded_draft_is_not_numbered_and_not_deleted(make):
    """The owner's wallet after the ZIP test: a will signed and sent (#1),
    then one signed, sent and in force (#2), then a draft that a plugin
    before #128 marked REPLACED at the next build. It read "#3 ... not
    sent ... Replaced" above #1 and the owner thought the order was wrong:
    now it is a discarded draft, unnumbered, shown in its place by creation
    (second ZIP test: "Show all history" only adds older rows at the
    bottom); the will in force has the highest number. Nothing deleted."""
    from bal.core import will_state
    s = make()
    s.check()
    s.clock.travel(1)
    s.set_date(D - fk.DAY)
    s.check()
    s.clock.travel(1)
    s.set_fee(20)
    s.check(closing=True, cancel_on=SIGN)
    drafts = [w for w, i in s.bw.willitems.items() if will_state.is_draft(i)]
    assert len(drafts) == 3
    for wid in drafts:                       # as a plugin before #128 left it
        s.bw.willitems[wid].set_status("REPLACED", True)
        s.bw.willitems[wid].set_status("VALID", False)
    history = _history(s)
    assert _summary(s, history) == [
        (2, "in_force", 3, "2027-02-26"),
        (None, "discarded_draft", 3, "2027-02-25"),
        (1, "replaced_held", 3, "2027-02-27"),
    ]
    rows, hidden = wv.display_rows(history)
    assert [(o.number, o.state) for _k, o in rows] == [
        (2, "in_force"), (None, "discarded_draft"), (1, "replaced_held")]
    assert hidden == 0                       # nothing older to show
    # With fewer recent versions shown, the cut falls after the numbered
    # ones: the discarded draft between them stays, older rows go.
    rows, hidden = wv.display_rows(history, recent=1)
    assert [(o.number, o.state) for _k, o in rows] == [(2, "in_force")]
    assert hidden == 2
    rows, _hidden = wv.display_rows(history, recent=2)
    assert [o.number for _k, o in rows] == [2, None, 1]
    # The next build (another fee: another content, so the discarded
    # transactions are not rebuilt with the same txids) keeps them (never
    # deleted) and the new will is #3.
    s.clock.travel(1)
    s.set_fee(23)
    s.check()
    history = _history(s)
    assert all(w in s.bw.willitems for w in drafts)
    states = [(v.number, v.state) for v in history.versions]
    assert states[-1] == (3, "in_force")
    assert (None, "discarded_draft") in states
    assert max(v.number or 0 for v in history.versions) == 3


# ---------------------------------------------------------- synthetic

def _hex(n):
    """A fake txid number ``n``."""
    return "{:064x}".format(n)


class FakeTx:
    """The part of a transaction ``will_versions`` reads."""

    def __init__(self, txid, inputs, locktime, outputs=()):
        self._txid = txid
        self._inputs = [
            SimpleNamespace(prevout=TxOutpoint(bytes.fromhex(t), n)) for t, n in inputs
        ]
        self.locktime = locktime
        self._outputs = [SimpleNamespace(address=a, value=v) for a, v in outputs]

    def inputs(self):
        return self._inputs

    def outputs(self):
        return self._outputs

    def txid(self):
        return self._txid


class FakeItem:
    """A will item: tx, heirs, executor, build time, fee rate and flags."""

    def __init__(self, txid, url="https://we0.test", locktime=D, inputs=None,
                 t=1000.0, heirs=True, fee=21, flags=("VALID", "COMPLETE", "PUSHED")):
        self.tx = FakeTx(txid, inputs or [(_hex(1), 0)], locktime)
        self.we = {"url": url, "address": "addr-" + str(url)} if url else None
        self.heirs = (
            {"alice": ["tb1qalice", "60%", locktime], "bob": ["tb1qbob", "40%", locktime]}
            if heirs else None
        )
        self.time = t
        self.tx_fees = fee
        self.sigs_have = 0
        self.flags = set(flags)

    def get_status(self, name):
        return name in self.flags


class FakeWallet:
    """Spends, labels, fees and heights of a wallet (all optional)."""

    def __init__(self, spenders=None, labels=None, mine=(), outputs=None, fees=None,
                 heights=None, synced=True, broken=False):
        self.spenders = spenders or {}
        self.labels = labels or {}
        self.mine = set(mine)
        self.outs = outputs or {}
        self.fees = fees or {}
        self.heights = heights or {}
        self.synced = synced
        self.broken = broken
        self.adb = self

    def is_up_to_date(self):
        return self.synced

    def get_spender(self, outpoint):
        if self.broken:
            raise RuntimeError("wallet error")
        return self.spenders.get(outpoint)

    def get_label_for_txid(self, txid):
        return self.labels.get(txid, "")

    def get_transaction(self, txid):
        return FakeTx(txid, [], 0, self.outs.get(txid, ()))

    def is_mine(self, address):
        return address in self.mine

    def get_tx_fee(self, txid):
        return self.fees.get(txid)

    def get_tx_height(self, txid):
        return SimpleNamespace(height=lambda: self.heights.get(txid, 0))


def _will(*items):
    """``{txid: item}``."""
    return {i.tx.txid(): i for i in items}


def test_same_executor_twice_in_one_content_is_split_by_time():
    """Only a will-executor's own fee changed: same content, same URL
    twice. The newest build keeps the URLs found once (they were reused)."""
    old0 = FakeItem(_hex(10), "https://we0.test", t=100.0,
                    flags=("REPLACED", "COMPLETE", "PUSHED"))
    new0 = FakeItem(_hex(11), "https://we0.test", t=200.0)
    we1 = FakeItem(_hex(12), "https://we1.test", t=100.0)
    we2 = FakeItem(_hex(13), "https://we2.test", t=100.0)
    history = wv.build_history(_will(old0, new0, we1, we2))
    assert [sorted(v.roots) for v in history.versions] == [
        [_hex(10)], [_hex(11), _hex(12), _hex(13)]]
    assert [v.number for v in history.versions] == [1, 2]
    # Same delivery: the newest version is the one in force.
    assert history.current == history.versions[1].vid
    assert history.versions[0].state == "replaced_held"
    # The ids are stable: the older bucket keeps its id when nothing moves.
    again = wv.build_history(_will(old0, new0, we1, we2))
    assert [v.vid for v in again.versions] == [v.vid for v in history.versions]


def test_other_coins_or_other_heirs_make_another_version():
    """Same date and fee: other coins, or other heirs, are another build
    even when the two builds have the same time."""
    a = FakeItem(_hex(14), "https://we0.test", t=100.0)
    b = FakeItem(_hex(15), "https://we1.test", t=100.0, inputs=[(_hex(2), 0)])
    assert len(wv.build_history(_will(a, b)).versions) == 2
    c = FakeItem(_hex(16), "https://we1.test", t=100.0)
    c.heirs = {"alice": ["tb1qalice", "50%", D], "bob": ["tb1qbob", "50%", D]}
    history = wv.build_history(_will(a, c))
    assert len(history.versions) == 2
    # The will-executor pseudo-heirs (fee outputs) are not part of it.
    d = FakeItem(_hex(17), "https://we1.test", t=100.0)
    d.heirs['w!ll3x3c"https://we1.test"1'] = ["addr", 1000, D]
    assert len(wv.build_history(_will(a, d)).versions) == 1


def test_items_without_heirs_go_to_other_transactions():
    merged = FakeItem(_hex(20), None, heirs=False, t=None, inputs=[(_hex(2), 0)])
    v1 = FakeItem(_hex(21), "https://we0.test")
    history = wv.build_history(_will(merged, v1))
    assert [v.roots for v in history.versions] == [[_hex(21)]]
    assert history.other.roots == [_hex(20)]
    assert history.other.number is None and history.other.state == "other"
    rows, _hidden = wv.display_rows(history)
    assert rows[-1] == ("version", history.other)


def test_versions_without_time_come_first():
    """Very old items without a build time are the oldest versions."""
    old = FakeItem(_hex(30), t=None, locktime=D + 99, flags=("REPLACED", "COMPLETE"))
    new = FakeItem(_hex(31), t=500.0)
    history = wv.build_history(_will(old, new))
    assert [v.roots[0] for v in history.versions] == [_hex(30), _hex(31)]
    assert [v.number for v in history.versions] == [1, 2]
    assert history.versions[0].state == "replaced"


def test_in_force_is_the_earliest_delivery_and_drafts_are_not_numbered():
    later = FakeItem(_hex(40), locktime=D, t=100.0)
    earlier = FakeItem(_hex(41), locktime=D - 86400, t=200.0)
    draft = FakeItem(_hex(42), locktime=D - 2 * 86400, t=300.0, flags=("VALID",))
    history = wv.build_history(_will(later, earlier, draft))
    states = [(v.number, v.state) for v in history.versions]
    assert states == [(1, "replaced_held"), (2, "in_force"), (None, "draft")]
    rows, _hidden = wv.display_rows(history)
    assert [o.state for _k, o in rows] == ["draft", "in_force", "replaced_held"]
    # A newer version that is not in force (partially signed) comes after
    # the version in force: draft, in force, then the history newest first.
    partial = FakeItem(_hex(43), locktime=D - 3 * 86400, t=250.0,
                       inputs=[(_hex(4), 0)], flags=("VALID", "PARTIALLY_SIGNED"))
    history = wv.build_history(_will(later, earlier, draft, partial))
    rows, _hidden = wv.display_rows(history)
    assert [(o.number, o.state) for _k, o in rows] == [
        (None, "draft"), (2, "in_force"), (3, "partially_signed"), (1, "replaced_held")]


def test_partially_signed_executed_not_valid_and_signed_never_sent():
    partial = FakeItem(_hex(50), t=10.0, flags=("VALID", "PARTIALLY_SIGNED"))
    executed = FakeItem(_hex(51), t=20.0, inputs=[(_hex(5), 0)],
                        flags=("COMPLETE", "PUSHED", "CONFIRMED"))
    broken = FakeItem(_hex(52), t=30.0, inputs=[(_hex(6), 0)], flags=())
    # Signed but never sent, older than the will in force: only this
    # wallet has it, so it is "replaced", not "still held".
    unsent = FakeItem(_hex(53), t=40.0, inputs=[(_hex(7), 0)], locktime=D + 5,
                      flags=("COMPLETE", "REPLACED"))
    current = FakeItem(_hex(54), t=50.0, inputs=[(_hex(7), 0)])
    history = wv.build_history(_will(partial, executed, broken, unsent, current))
    # Never signed nor sent and not the current draft: a discarded draft,
    # without a number (owner, after the ZIP test).
    assert [v.state for v in history.versions] == [
        "partially_signed", "executed", "discarded_draft", "replaced", "in_force"]
    assert [v.number for v in history.versions] == [1, 2, None, 3, 4]


def test_not_synchronized_every_signed_version_counts():
    """Spends unknown: an old version spent by the invalidation still counts
    as held (the #128 safe side); the earliest delivery is in force."""
    old = FakeItem(_hex(60), locktime=D, t=100.0,
                   flags=("REPLACED", "COMPLETE", "PUSHED"))
    new = FakeItem(_hex(61), locktime=D - 86400, t=200.0)
    wallet = FakeWallet(spenders={_hex(1) + ":0": _hex(99)}, synced=False)
    history = wv.build_history(_will(old, new), wallet)
    assert [v.state for v in history.versions] == ["replaced_held", "in_force"]
    synced = wv.build_history(_will(old, new), FakeWallet(
        spenders={_hex(1) + ":0": _hex(99)}))
    # Synchronized, the spend is known: nothing is in force any more.
    assert [v.state for v in synced.versions] == ["replaced", "not_valid"]
    assert synced.current is None


def _invalidated(n, t):
    """A sent item marked invalidated, on coin ``_hex(1):0``."""
    return FakeItem(_hex(n), t=t, flags=("INVALIDATED", "REPLACED", "COMPLETE", "PUSHED"))


def test_a_spender_that_is_not_an_invalidation_has_no_row():
    """A payment to someone else also ends the will, but it is not the paid
    invalidation: no row (neither the label nor every output to the
    wallet). The versions still say invalidated."""
    will = _will(_invalidated(70, 1.0))
    payment = _hex(98)
    wallet = FakeWallet(spenders={_hex(1) + ":0": payment},
                        outputs={payment: [("their-address", 5000)]})
    history = wv.build_history(will, wallet)
    assert history.invalidations == []
    assert history.versions[0].state == "invalidated"
    # Every output back to the wallet: an invalidation without the label
    # (made by an older plugin, or the label lost).
    wallet = FakeWallet(spenders={_hex(1) + ":0": payment}, mine={"my-address"},
                        outputs={payment: [("my-address", 5000)]},
                        fees={payment: 310}, heights={payment: 120})
    (inv,) = wv.build_history(will, wallet).invalidations
    assert (inv.labelled, inv.fee, inv.confirmed) == (False, 310, True)


def test_wallet_errors_never_fail_the_list():
    will = _will(_invalidated(71, 1.0), FakeItem(_hex(72), t=2.0, inputs=[(_hex(3), 0)]))
    history = wv.build_history(will, FakeWallet(broken=True))
    assert history.invalidations == []
    assert [v.number for v in history.versions] == [1, 2]


def test_show_all_history_and_the_invalidation_row_follow_the_limit():
    """The last 5 non-draft versions (the version in force counts), the
    drafts on top and not counted; an invalidation is shown with the
    version it is anchored to."""
    items = [_invalidated(80 + i, float(i)) for i in range(5)]
    # Different dates: five versions, all ended by the same invalidation.
    for i, item in enumerate(items):
        item.tx.locktime = D + 10 * (i + 1)
    items.append(FakeItem(_hex(90), t=10.0, locktime=D + 1, inputs=[(_hex(2), 0)]))
    items.append(FakeItem(_hex(91), t=11.0, locktime=D, inputs=[(_hex(2), 0)]))
    items.append(FakeItem(_hex(92), t=12.0, locktime=D - 1, inputs=[(_hex(2), 0)],
                          flags=("VALID",)))
    paid = _hex(97)
    wallet = FakeWallet(spenders={_hex(1) + ":0": paid},
                        labels={paid: wv.INVALIDATION_LABEL})
    history = wv.build_history(_will(*items), wallet)
    rows, hidden = wv.display_rows(history)
    kinds = [(k, o.number if k == "version" else None) for k, o in rows]
    # Draft, #7 in force, #6, then the newest invalidated #5, #4, #3; the
    # invalidation is anchored to #5, right above it.
    assert kinds == [("version", None), ("version", 7), ("version", 6),
                     ("invalidation", None), ("version", 5), ("version", 4),
                     ("version", 3)]
    assert hidden == 2
    rows, hidden = wv.display_rows(history, show_all=True)
    assert hidden == 0 and len(rows) == 9
    # With fewer shown versions the anchor can be hidden: no row then.
    rows, _hidden = wv.display_rows(history, recent=2)
    assert [k for k, _o in rows] == ["version"] * 3


def test_a_thousand_items_in_well_under_a_second():
    """Multiverse with 10 will-executors and 3 instalments: 10 + 100 +
    1000 items, the chain asked once per item. Measured and printed."""
    will = {}
    n = [1000]

    def add(father, depth, url):
        n[0] += 1
        txid = _hex(n[0])
        inputs = [(father, 1)] if father else [(_hex(1), 0)]
        will[txid] = FakeItem(txid, url, inputs=inputs, locktime=D + depth, t=1.0)
        return txid

    urls = ["https://we{}.test".format(i) for i in range(10)]
    for u in urls:
        r = add(None, 0, u)
        for u2 in urls:
            c = add(r, 1, u2)
            for u3 in urls:
                add(c, 2, u3)
    assert len(will) == 1110
    wallet = FakeWallet()
    calls = []
    real = wallet.get_spender
    wallet.get_spender = lambda o: (calls.append(o), real(o))[1]
    start = time.perf_counter()
    history = wv.build_history(will, wallet)
    elapsed = time.perf_counter() - start
    print("\n1110 items: {:.3f} s, {} spend lookups".format(elapsed, len(calls)))
    (v,) = history.versions
    assert len(v.roots) == 10 and len(v.items) == 1110
    assert v.state == "in_force"
    assert elapsed < 1.0
    # At most one executable() per item, each walking its parents once:
    # far from one lookup per pair of items.
    assert len(calls) <= 3 * len(will)


# ----------------------------- #200: a new will on a coin that came back

NEW200 = D + 4 * 365 * 86400
T_OLD = float(D - 200 * 86400)       # the first will, built before its date
T_NEW = float(D + 11 * 86400)        # the new will, after the execution
EXECUTED = ("COMPLETE", "PUSHED", "CHECKED", "CONFIRMED")


def _executed_and_new(father_flags=EXECUTED, t_old=T_OLD, t_new=T_NEW, out=1,
                      locktime=D):
    """we1's copy executed (``father_flags``), we0's copy of the same will
    dead, and the three copies of a new will (bob and carol, 2031) spending
    output ``out`` of the executed copy (1: the change or "me"; 0: an
    heir's output, which a wallet never gets back: the rule must not care)."""
    executed = FakeItem(_hex(1001), "https://we1.test", locktime=locktime, t=t_old,
                        flags=father_flags)
    dead = FakeItem(_hex(1002), "https://we0.test", locktime=locktime, t=t_old,
                    flags=("COMPLETE", "PUSHED", "CHECKED", "INVALIDATED"))
    new = [FakeItem(_hex(1010 + i), "https://we{}.test".format(i), locktime=NEW200,
                    t=t_new, inputs=[(_hex(1001), out)]) for i in range(3)]
    for item in new:
        item.heirs = {"bob": ["tb1qbob", "50%", NEW200],
                      "carol": ["tb1qcarol", "50%", NEW200]}
    return _will(executed, dead, *new)


def _shape(history):
    """``[(state, items)]`` of the versions, oldest first."""
    return [(v.state, len(v.items)) for v in history.versions]


SPLIT = [("executed", 2), ("in_force", 3)]     # the new will on its own
LINKED = [("executed", 5)]                     # today's link kept


@pytest.mark.parametrize("flags", [EXECUTED, ("COMPLETE", "PUSHED", "MEMPOOL")])
@pytest.mark.parametrize("out", [0, 1])
@pytest.mark.parametrize("wallet", [None, FakeWallet()])
def test_a_new_will_on_a_coin_back_from_an_executed_copy(flags, out, wallet):
    """Mined or in the mempool, either output, with or without a wallet:
    the new will is a version of its own and the version in force."""
    history = wv.build_history(_executed_and_new(flags, out=out), wallet)
    assert _shape(history) == SPLIT
    assert history.current == history.versions[1].vid
    assert sorted(history.versions[1].roots) == [_hex(1010), _hex(1011), _hex(1012)]


def test_a_father_without_a_build_time_is_another_build():
    """Very old wallets have items without ``time``: the new will (with a
    time, after the date) is still another build."""
    assert _shape(wv.build_history(_executed_and_new(t_old=None))) == SPLIT


def test_unknown_or_unreadable_data_keeps_the_link():
    """A new copy without a build time, a time or a date that cannot be
    read, a date in blocks (it cannot be compared with a build time):
    today's link."""
    assert _shape(wv.build_history(_executed_and_new(t_new=None))) == LINKED
    assert _shape(wv.build_history(_executed_and_new(locktime=800_000))) == LINKED
    # The list itself cannot read such times (``_make_version``): the rule
    # alone is asked here.
    for kw in ({"t_new": "not a time"}, {"t_old": "not a time"}):
        assert wv.new_will_links(_executed_and_new(**kw)) == set()
    will = _executed_and_new()
    will[_hex(1001)].tx.locktime = "not a date"
    assert not wv.built_after_on_chain(will[_hex(1010)], will[_hex(1001)])


def test_the_same_build_or_an_older_one_keeps_the_link():
    """The anticipation can date delivery 1 in the past: delivery 2 of the
    same build (same time, after delivery 1's date) stays under it once
    delivery 1 is mined; a child older than its father too."""
    same = float(D + 3600)
    assert _shape(wv.build_history(_executed_and_new(t_old=same, t_new=same))) == LINKED
    assert _shape(wv.build_history(_executed_and_new(t_old=T_NEW, t_new=T_NEW - 1))) == LINKED


def test_a_father_not_on_the_chain_keeps_the_link():
    """A delivery 1 still to come (signed, sent): its children are its
    next delivery, whatever their build time."""
    pending = ("VALID", "COMPLETE", "PUSHED", "CHECKED")
    assert len(wv.build_history(_executed_and_new(pending)).versions) == 1


def test_a_copy_rebuilt_before_the_fathers_date_keeps_the_link():
    """Delivery 2 rebuilt (a later build) while delivery 1 was still to
    come, then delivery 1 mined: built before delivery 1's date, so it
    cannot spend a coin that came back from it."""
    will = _executed_and_new(t_new=float(D - 86400))
    assert _shape(wv.build_history(will)) == LINKED


@pytest.mark.parametrize("sibling_flags", [
    ("VALID", "COMPLETE", "PUSHED", "CHECKED"),
    ("REPLACED", "COMPLETE", "PUSHED"),
    ("INVALIDATED", "COMPLETE", "PUSHED"),
])
def test_the_content_of_a_kept_delivery_stays_with_it(sibling_flags):
    """Rule 4: a copy built after the execution with the content of a
    delivery the executed copy already had (any flags) stays with it; a
    copy with other content starts a will of its own."""
    will = _executed_and_new()
    for i in range(3):                       # the new will's copies...
        del will[_hex(1010 + i)]
    kept = FakeItem(_hex(1020), "https://we0.test", locktime=NEW200, t=T_OLD,
                    inputs=[(_hex(1001), 1)], flags=sibling_flags)
    added = FakeItem(_hex(1021), "https://we2.test", locktime=NEW200, t=T_NEW,
                     inputs=[(_hex(1001), 1)])
    other = FakeItem(_hex(1022), "https://we1.test", locktime=NEW200, t=T_NEW,
                     inputs=[(_hex(1001), 1)])
    other.heirs = {"carol": ["tb1qcarol", "100%", NEW200]}
    will.update(_will(kept, added, other))
    assert wv.built_after_on_chain(added, will[_hex(1001)])
    assert wv.new_will_links(will) == {(_hex(1022), _hex(1001))}
    history = wv.build_history(will)
    assert [sorted(v.items) for v in history.versions] == [
        sorted([_hex(1001), _hex(1002), _hex(1020), _hex(1021)]), [_hex(1022)]]


def test_a_loop_in_corrupted_data_does_not_hang():
    """Two executed items of one build spending each other (the rule is
    asked on both links and cuts neither): the list and the Graph still
    come back, and nothing disappears from the list."""
    from bal.core import graph as core_graph
    a = FakeItem(_hex(1101), t=T_NEW, inputs=[(_hex(1102), 0)], flags=EXECUTED)
    b = FakeItem(_hex(1102), "https://we1.test", t=T_NEW, inputs=[(_hex(1101), 0)],
                 flags=EXECUTED)
    will = _will(a, b)
    history = wv.build_history(will)
    assert {w for v in history.versions for w in v.items} == set(will)
    _roots, nodes = core_graph.build_graph(will)
    assert sorted(nodes) == sorted(will)


def test_the_graph_draws_the_new_will_and_hides_the_executed_one():
    """The Graph's own rule: the executed copy with nothing in force under
    it is left out when a VALID copy is drawn; "show replaced" draws it
    again; alone (no new will) it stays."""
    from bal.core import graph as core_graph
    will = _executed_and_new()
    roots, nodes = core_graph.build_graph(will)
    assert sorted(nodes) == [_hex(1010), _hex(1011), _hex(1012)]
    assert all(n.parent is None and n.depth == 0 for n in roots)
    _roots, nodes = core_graph.build_graph(will, show_replaced=True)
    assert _hex(1001) in nodes and nodes[_hex(1001)].children == []
    alone = {w: i for w, i in will.items() if w in (_hex(1001), _hex(1002))}
    _roots, nodes = core_graph.build_graph(alone)
    assert list(nodes) == [_hex(1001)] and nodes[_hex(1001)].delivered


def test_a_rebuilt_later_delivery_stays_with_its_executed_delivery_1():
    """Rule 5: copies built after the execution that pay only persons of
    delivery 2 or later (the rest of the table rebuilt after delivery 1
    was executed: a new coin, another fee) stay with the executed delivery
    1, in the List and in the Graph alike (before the rule the List made
    them a version "(+1 delivery) · bob 0% + 40%"). An OP_RETURN message
    row or a will-executor fee output does not change that; a person of
    delivery 1 does, in both views."""
    from bal.core import graph as core_graph
    from bal.core.heirs import message_address
    will = _executed_and_new()
    for i in range(3):
        url = "https://we{}.test".format(i)
        will[_hex(1010 + i)].heirs = {
            "bob§2": ["tb1qbob", "40%", NEW200],
            "note": [message_address("ciao"), "0", NEW200],
            # Each will-executor's own fee output (``Heirs`` names it so).
            'w!ll3x3c"{}"{}'.format(url, NEW200): ["addr-" + url, 1000, NEW200]}
    assert wv.new_will_links(will) == set()
    assert _shape(wv.build_history(will)) == LINKED
    _roots, nodes = core_graph.build_graph(will)
    assert sorted(c.wid for c in nodes[_hex(1001)].children) == [
        _hex(1010), _hex(1011), _hex(1012)]
    assert {nodes[_hex(1010 + i)].depth for i in range(3)} == {1}
    will[_hex(1010)].heirs["alice"] = ["tb1qalice", "60%", NEW200]
    assert wv.new_will_links(will) == {(_hex(1010), _hex(1001))}
    _roots, nodes = core_graph.build_graph(will)
    assert nodes[_hex(1010)].parent is None
    assert [len(v.items) for v in wv.build_history(will).versions] == [4, 1]


def test_a_thousand_items_with_executed_deliveries_in_well_under_a_second():
    """The thousand items above, every delivery-1 copy executed, so the
    rule is asked on every link (children of the same build): one version,
    the list and the Graph each well under a second. Measured and printed."""
    from bal.core import graph as core_graph
    will = {}
    n = [5000]

    def add(father, depth, url, flags):
        n[0] += 1
        txid = _hex(n[0])
        inputs = [(father, 1)] if father else [(_hex(1), 0)]
        will[txid] = FakeItem(txid, url, inputs=inputs, locktime=D + depth, t=1.0,
                              flags=flags)
        return txid

    urls = ["https://we{}.test".format(i) for i in range(10)]
    for u in urls:
        r = add(None, 0, u, EXECUTED)
        for u2 in urls:
            c = add(r, 1, u2, ("VALID", "COMPLETE", "PUSHED"))
            for u3 in urls:
                add(c, 2, u3, ("VALID", "COMPLETE", "PUSHED"))
    assert len(will) == 1110
    start = time.perf_counter()
    history = wv.build_history(will, FakeWallet())
    list_time = time.perf_counter() - start
    start = time.perf_counter()
    _roots, nodes = core_graph.build_graph(will)
    graph_time = time.perf_counter() - start
    print("\n1110 items, deliveries 1 executed: list {:.3f} s, graph {:.3f} s".format(
        list_time, graph_time))
    assert len(history.versions) == 1 and len(history.versions[0].items) == 1110
    assert len(nodes) == 1110
    assert list_time < 1.0 and graph_time < 1.0


def test_a_thousand_items_with_copies_built_after_the_execution():
    """The same thousand items, the children built after delivery 1 was
    executed, so every link to an executed copy goes through the whole
    rule (conditions 1-5): under each executed copy five children paying
    delivery-1 persons start a new will (ten new versions, one per coin),
    five paying only ``§2`` persons stay. The list and the Graph each well
    under a second. Measured and printed."""
    from bal.core import graph as core_graph
    will = {}
    n = [7000]

    def add(father, depth, url, flags, t, later=False):
        n[0] += 1
        txid = _hex(n[0])
        inputs = [(father, 1)] if father else [(_hex(1), 0)]
        item = FakeItem(txid, url, inputs=inputs, locktime=D + depth, t=t, flags=flags)
        if later:
            item.heirs = {"bob§2": ["tb1qbob", "40%", D + depth]}
        will[txid] = item
        return txid

    urls = ["https://we{}.test".format(i) for i in range(10)]
    pending = ("VALID", "COMPLETE", "PUSHED")
    for u in urls:
        r = add(None, 0, u, EXECUTED, T_OLD)
        for i, u2 in enumerate(urls):
            c = add(r, 1, u2, pending, T_NEW, later=bool(i % 2))
            for u3 in urls:
                add(c, 2, u3, pending, T_NEW)
    assert len(will) == 1110
    start = time.perf_counter()
    history = wv.build_history(will, FakeWallet())
    list_time = time.perf_counter() - start
    start = time.perf_counter()
    roots, nodes = core_graph.build_graph(will)
    graph_time = time.perf_counter() - start
    print("\n1110 items, children built after the execution: "
          "list {:.3f} s, graph {:.3f} s".format(list_time, graph_time))
    assert len(wv.new_will_links(will)) == 50
    assert len(history.versions) == 11
    assert sum(len(v.items) for v in history.versions) == 1110
    assert len(nodes) == 1110 and len(roots) == 60
    assert list_time < 1.0 and graph_time < 1.0


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
