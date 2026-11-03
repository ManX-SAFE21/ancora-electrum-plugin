"""A new will built on a coin that came back from an executed copy (#200).

The owner's wallet: a will (alice 70 %, "me" 30 % at an address of the
wallet itself, three will-executors) was executed: one copy was mined,
alice got 70 % and 30 % came back to the wallet as a new coin. Then a new
will on that coin, other heirs, a later date. The new copies spend an
output of the executed copy, exactly like a later delivery of a will in
several deliveries, so the Will tab showed them as the next delivery of the
executed will: no row for the new will in the List, Future in red "The
delivery date has passed" with the old date, the Graph with the old will as
delivery 1. Now (``will_versions.new_will_links``) the new will is a
version of its own. Here, with ``tests/flowkit.py`` (three fake
will-executors, each with its own fee address; throw-away test wallets
with the test password; fixed clock):

* the owner's case, with the executed copy in the mempool, mined but not
  verified, mined and verified; without a wallet and on a wallet not
  synchronized: the versions, the Future, the Graph (also "show
  replaced"), the PDF report, the invalidation box; nothing is written;
* the stored ``father`` stays the executed copy, on purpose: a second
  Check keeps the new will; invalidating it ends only its copies (the paid
  invalidation is found); when the executed copy disappears (another copy
  mined instead) the stored tree is what invalidates the new will;
* the new will cancelled at the password (a draft);
* BASIC, a will executed and a new will on a new coin (not one that came
  back): the Graph draws only the new will, under its own date;
* the wills in several deliveries, which must not change: delivery 1
  executed; a will-executor added before delivery 1 was mined; delivery 2
  rebuilt after delivery 1 was executed (new coin, other fee: it stays
  with delivery 1, one version, the table's two columns in the Graph); a
  will-executor added after delivery 1 was executed (one version); a new
  will in several deliveries on the coin that came back.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_new_will_after_executed.py
"""

import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

import flowkit as fk
from electrum.util import TxMinedInfo
from PyQt6.QtWidgets import QApplication

from bal.core import delivery
from bal.core import future as core_future
from bal.core import graph as core_graph
from bal.core import invalidation_preview as ip
from bal.core import report as core_report
from bal.core import will_versions as wv
from bal.engine import context as engine_context

# Kept at module level: flowkit's window needs a Qt application.
_APP = QApplication.instance() or QApplication(sys.argv)

# Test value: the password of the throw-away test wallets.
TEST_PASSWORD = "flowkit-test"
D = fk.ts(2026, 10, 16, 19)          # the owner's executed will
NEW = fk.ts(2030, 10, 15, 19)        # the owner's new will
YEAR = 365 * fk.DAY
SIGN = ("sign your will",)
FLAGS = ("VALID", "CONFIRMED", "MEMPOOL", "INVALIDATED", "REPLACED")


@pytest.fixture
def make(monkeypatch):
    """flowkit sessions, delivery ``D``, three will-executors, synchronized."""
    fk.ensure_event_loop()
    made = []

    def _make(three=True):
        """One session; ``three=False`` starts with two will-executors."""
        s = fk.Session(monkeypatch, password=TEST_PASSWORD, fee=21, locktime=D,
                       threshold="-30d")
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


def _mine(s, wid, how="unverified"):
    """Put the will item ``wid`` on the chain: ``"mempool"``, mined but not
    verified (``"unverified"``: Electrum has no block time for it) or mined
    and verified (``"spv"``)."""
    tx = s.bw.willitems[wid].tx
    assert tx.is_complete()
    s.wallet.adb.add_transaction(tx)
    # The fake network knows it, so a later transaction may spend it.
    s.world.broadcast[tx.txid()] = str(tx)
    if how == "mempool":
        s.wallet.adb.add_unverified_or_unconfirmed_tx(tx.txid(), 0)
    elif how == "unverified":
        s.wallet.adb.add_unverified_or_unconfirmed_tx(tx.txid(), 101)
    elif how == "spv":
        s.wallet.db.put("stored_height", 300)
        s.wallet.adb.add_verified_tx(tx.txid(), TxMinedInfo(
            _height=101, timestamp=int(s.clock.now), txpos=1, header_hash="00" * 32))
    else:
        raise ValueError(how)


def _copies(s, url=None, locktime=None):
    """The ids of the will items of ``url`` (any) dated ``locktime`` (any)."""
    return sorted(
        w for w, i in s.bw.willitems.items()
        if (url is None or (i.we or {}).get("url") == url)
        and (locktime is None or int(i.tx.locktime) == locktime))


def _owner_case(make, how="unverified", three=True):
    """The owner's wallet: the will executed by we1's copy, 30 % back to the
    wallet, eleven days later a new will on that coin. Returns
    ``(session, id of the executed copy)``."""
    s = make(three=three)
    me = s.wallet.get_receiving_addresses()[5]
    s.bw.heirs["alice"] = [fk.HEIR_ADDR["alice"], "70%", D]
    s.bw.heirs["me"] = [me, "30%", D]
    del s.bw.heirs["bob"]
    s.bw.heirs.save()
    s.check()
    s.clock.travel(10)
    (executed,) = _copies(s, "https://we1.test")
    _mine(s, executed, how)
    s.clock.travel(11)
    s.set_date(NEW)
    s.set_heirs(alice="40%", bob="30%", carol="30%")
    s.check()
    return s, executed


def _versions(history):
    """``[(number, state, delivery day, items)]``, oldest first."""
    return [(v.number, v.state, fk.fmt(v.locktime), len(v.items))
            for v in history.versions]


def _current(history):
    """The number of the version in force."""
    return history.by_vid()[history.current].number if history.current else None


def _future(s, history=None, wallet=True, synced=None):
    """``(title, [delivery days])`` of the Future view."""
    f = core_future.build_future(
        s.bw.willitems, s.bw.will_settings, False,
        wallet=s.wallet if wallet else None, synced=synced, history=history)
    return f.title, [fk.fmt(i.locktime) for i in f.deliveries]


def _columns(s, **kw):
    """The Graph: ``[(column day, [the deliveries the copies there pay])]``."""
    _roots, nodes = core_graph.build_graph(s.bw.willitems, **kw)
    out = []
    for depth, locktime in enumerate(core_graph.column_dates(nodes)):
        rows = sorted({
            delivery.instalment_number(name)
            for n in nodes.values() if n.depth == depth
            for name in (n.item.heirs or {}) if not name.startswith(wv._EXECUTOR_HEIR)})
        out.append((fk.fmt(locktime), rows))
    return out


def _stored(s):
    """What the wallet keeps of each item: its data, father and children."""
    return {w: (str(i.to_dict()), i.father, str(i.children))
            for w, i in s.bw.willitems.items()}


# ------------------------------------------------------- the owner's case

@pytest.mark.parametrize("how", ["mempool", "unverified", "spv"])
def test_the_new_will_is_a_version_of_its_own(make, how):
    """The List has the new will (#2, in force) above the executed one
    (#1); Future and Graph show the new will; nothing is written."""
    s, executed = _owner_case(make, how)
    will = s.bw.willitems
    assert will[executed].get_status("CONFIRMED") or will[executed].get_status("MEMPOOL")
    before = _stored(s)
    history = wv.build_history(will, s.wallet)
    assert _versions(history) == [(1, "executed", "2026-10-16", 3),
                                  (2, "in_force", "2030-10-15", 3)]
    assert _current(history) == 2
    rows, _hidden = wv.display_rows(history)
    assert [o.number for _k, o in rows] == [2, 1]
    (new,) = [v for v in history.versions if v.number == 2]
    assert sorted(new.items) == _copies(s, locktime=NEW)
    assert [h[:2] for h in new.heirs] == [("alice", "40%"), ("bob", "30%"),
                                          ("carol", "30%")]
    assert (new.executors_verified, new.executors_total) == (3, 3)
    assert _future(s, history) == ("Your will is in force", ["2030-10-15"])
    # The Graph: only the new will (three copies of 15 October 2030)...
    roots, nodes = core_graph.build_graph(will)
    assert len(roots) == 3 and len(nodes) == 3
    assert all(fk.fmt(n.item.tx.locktime) == "2030-10-15" for n in roots)
    assert _columns(s) == [("2030-10-15", [1])]
    # ...and with "show replaced" the executed copy is drawn again, as a
    # will of its own: the new copies are not its next delivery.
    roots, nodes = core_graph.build_graph(will, show_replaced=True)
    assert executed in nodes and nodes[executed].parent is None
    assert all(nodes[w].parent is None for w in _copies(s, locktime=NEW))
    # Display only: the wallet's items, their father and children are as
    # they were; the stored father of the new copies stays the executed one.
    assert _stored(s) == before
    assert all(will[w].father == executed for w in _copies(s, locktime=NEW))


@pytest.mark.parametrize("wallet, synced", [(False, None), (True, False)])
def test_the_same_without_a_wallet_or_not_synchronized(make, wallet, synced):
    """The rule reads only what the plugin stored (flags, build time,
    date): the same versions without a wallet or before the sync."""
    s, _executed = _owner_case(make)
    history = wv.build_history(s.bw.willitems, s.wallet if wallet else None, synced)
    assert [v[1:] for v in _versions(history)] == [
        ("executed", "2026-10-16", 3), ("in_force", "2030-10-15", 3)]
    assert _current(history) == 2
    assert _future(s, history, wallet, synced) == ("Your will is in force",
                                                  ["2030-10-15"])


def test_the_pdf_report_has_one_delivery(make):
    """The report (#132) prints the new will: one delivery, 2030."""
    s, _executed = _owner_case(make, "spv")
    report = core_report.will_report(s.bw.willitems, s.bw.heirs, s.bw.will_settings,
                                     False, wallet=s.wallet)
    assert [(i.number, fk.fmt(i.locktime), i.tx_count) for i in report.instalments] == [
        (1, "2030-10-15", 3)]
    assert report.multiverse is False


def test_the_invalidation_box_names_the_new_will(make):
    """The box before an invalidation (#135) lists the will it ends: the
    new one (before: none, the new copies belonged to the executed will)."""
    s, _executed = _owner_case(make)
    will = s.bw.willitems
    tx = SimpleNamespace(inputs=lambda: will[_copies(s, locktime=NEW)[0]].tx.inputs())
    assert ip.ended_wills(tx, will, s.wallet) == [(2, NEW, 3)]


def test_a_second_check_keeps_the_new_will(make):
    """The stored father (the executed copy) is harmless: a second Check
    sends nothing, asks nothing and leaves the new copies valid."""
    s, _executed = _owner_case(make)
    pushed = len(s.world.pushed)
    s.clock.travel(1)
    s.check()
    engine_context.refresh_invalidated_items(s.bw)
    assert all(s.bw.willitems[w].get_status("VALID") for w in _copies(s, locktime=NEW))
    assert len(s.world.pushed) == pushed
    assert s.world.broadcast_log == [] and s.user.asked == []
    assert [v[1:] for v in _versions(wv.build_history(s.bw.willitems, s.wallet))] == [
        ("executed", "2026-10-16", 3), ("in_force", "2030-10-15", 3)]


def test_invalidating_the_new_will_ends_only_its_copies(make):
    """The paid invalidation of the new will: its copies are invalidated,
    the executed copy stays executed, the invalidation row ends #2 (the
    Check after it builds the next will, #3, on the coin it gave back)."""
    s, executed = _owner_case(make)
    new = _copies(s, locktime=NEW)
    s.menu_invalidate()
    s.clock.travel(1)
    s.check()
    will = s.bw.willitems
    assert will[executed].get_status("CONFIRMED") or will[executed].get_status("MEMPOOL")
    assert all(will[w].get_status("INVALIDATED") for w in new)
    history = wv.build_history(will, s.wallet)
    assert [v[1:3] for v in _versions(history)] == [
        ("executed", "2026-10-16"), ("invalidated", "2030-10-15"),
        ("in_force", "2030-10-15")]
    (inv,) = history.invalidations
    assert [history.by_vid()[vid].number for vid in inv.ended] == [2]
    assert inv.labelled and inv.fee == s.wallet.adb.get_tx_fee(inv.txid) > 0


def test_the_stored_tree_invalidates_the_new_will_when_the_executed_copy_disappears(make):
    """The executed copy was only in the mempool and another copy of the
    same will is mined instead: the coin of the new will never exists.
    The stored tree (``father`` / ``children``, which #200 leaves as it is)
    is what marks the new copies invalidated (``Will.set_invalidate``)."""
    s, executed = _owner_case(make, "mempool")
    (other,) = _copies(s, "https://we0.test", D)
    s.wallet.adb.remove_transaction(executed)
    tx = s.bw.willitems[other].tx
    s.wallet.adb.add_transaction(tx)
    s.wallet.adb.add_unverified_or_unconfirmed_tx(tx.txid(), 102)
    engine_context.refresh_invalidated_items(s.bw)
    will = s.bw.willitems
    assert all(will[w].get_status("INVALIDATED") for w in _copies(s, locktime=NEW))
    assert not any(will[w].get_status("VALID") for w in _copies(s, locktime=NEW))


def test_the_new_will_cancelled_at_the_password_is_a_draft_on_top(make):
    """The new will not signed (password window closed): a draft above the
    executed will, never one more delivery of it."""
    s = make()
    me = s.wallet.get_receiving_addresses()[5]
    s.bw.heirs["alice"] = [fk.HEIR_ADDR["alice"], "70%", D]
    s.bw.heirs["me"] = [me, "30%", D]
    del s.bw.heirs["bob"]
    s.bw.heirs.save()
    s.check()
    s.clock.travel(10)
    _mine(s, _copies(s, "https://we1.test")[0], "spv")
    s.clock.travel(11)
    s.set_date(NEW)
    s.set_heirs(alice="40%", bob="30%", carol="30%")
    s.check(cancel_on=SIGN)
    history = wv.build_history(s.bw.willitems, s.wallet)
    rows, _hidden = wv.display_rows(history)
    assert [(o.number, o.state, fk.fmt(o.locktime)) for _k, o in rows] == [
        (None, "draft", "2030-10-15"), (1, "executed", "2026-10-16")]


def test_an_executor_added_to_the_new_will_joins_it(make):
    """Two will-executors, the new will, then a third one added: one new
    version with the three copies."""
    s, _executed = _owner_case(make, three=False)
    s.clock.travel(2)
    s.world.add_executor()
    s.check()
    history = wv.build_history(s.bw.willitems, s.wallet)
    assert [v[1:] for v in _versions(history)] == [
        ("executed", "2026-10-16", 2), ("in_force", "2030-10-15", 3)]
    assert _columns(s) == [("2030-10-15", [1])]


def test_basic_a_new_will_on_a_new_coin_after_the_execution(make):
    """BASIC, one date: the will executed (no coin back), then a new coin
    and a new will on it. Its copies spend the new coin, not the executed
    copy, so the rule of ``new_will_links`` has nothing to cut; the Graph's
    own rule leaves the executed copy out (nothing in force under it), so
    the one column has the new will's date (before #200 the executed copy
    stood in it and the column read 16 October 2026 above the new copies).
    "Show replaced" draws it again; the List keeps both versions."""
    s = make()
    assert s.plugin.USER_TYPE.get() == "basic"
    s.check()
    s.clock.travel(10)
    (executed,) = _copies(s, "https://we1.test")
    _mine(s, executed)
    s.clock.travel(11)
    s.add_coin(300_000)
    s.set_date(NEW)
    s.check()
    will = s.bw.willitems
    new = _copies(s, locktime=NEW)
    assert len(new) == 3 and all(will[w].get_status("VALID") for w in new)
    assert wv.new_will_links(will) == set()
    roots, nodes = core_graph.build_graph(will)
    assert sorted(nodes) == new and sorted(n.wid for n in roots) == new
    assert _columns(s) == [("2030-10-15", [1])]
    _roots, nodes = core_graph.build_graph(will, show_replaced=True)
    assert executed in nodes and nodes[executed].delivered
    history = wv.build_history(will, s.wallet)
    assert [v[1:] for v in _versions(history)] == [
        ("executed", "2026-10-16", 3), ("in_force", "2030-10-15", 3)]


# ------------------------------------------- wills in several deliveries

def _table(s, first=D):
    """alice 60 % at ``first``, bob 40 % one year later (#168)."""
    s.set_instalments((first, {"alice": "60%", "bob": "0%"}),
                      (first + YEAR, {"alice": "0%", "bob": "40%"}))


def _delivery_1_executed(make, three=True):
    """A will in two deliveries; we1's delivery 1 mined, then a Check."""
    s = make(three=three)
    _table(s)
    s.check()
    s.clock.travel(10)
    (executed,) = _copies(s, "https://we1.test", D)
    _mine(s, executed)
    s.clock.travel(1)
    s.check()
    return s, executed


def test_delivery_1_executed_stays_one_version(make):
    """The rest of the will is delivery 2, still one version; the Graph
    keeps the executed delivery 1 with delivery 2 under it."""
    s, executed = _delivery_1_executed(make)
    history = wv.build_history(s.bw.willitems, s.wallet)
    assert len(history.versions) == 1
    assert len(history.versions[0].items) == len(s.bw.willitems)
    assert wv.new_will_links(s.bw.willitems) == set()
    roots, nodes = core_graph.build_graph(s.bw.willitems)
    assert [n.wid for n in roots] == [executed]
    assert _columns(s) == [("2026-10-16", [1]), ("2027-10-16", [2])]


def test_an_executor_added_before_delivery_1_was_mined_stays_one_version(make):
    """Two will-executors, a third added later (its copies have a newer
    build time than the delivery-1 copies kept by the #122 reuse), then
    delivery 1 mined: one version."""
    s = make(three=False)
    _table(s)
    s.check()
    s.clock.travel(1)
    s.world.add_executor()
    s.check()
    s.clock.travel(10)
    kept = [w for w in _copies(s, locktime=D) if s.bw.willitems[w].get_status("VALID")]
    _mine(s, kept[0])
    engine_context.refresh_invalidated_items(s.bw)
    history = wv.build_history(s.bw.willitems, s.wallet)
    assert len([v for v in history.versions if v.state != "discarded_draft"]) == 1
    assert wv.new_will_links(s.bw.willitems) == set()


@pytest.mark.parametrize("change", ["coin", "fee"])
def test_delivery_2_rebuilt_after_delivery_1_was_executed(make, change):
    """After delivery 1 was executed the plugin rebuilds delivery 2 (a new
    coin, or another fee). Its copies pay only delivery 2's person, so they
    are the rest of the same will (rule 5 of ``new_will_links``), as before
    #200: one version in the List (as a version of its own its row read
    "(+1 delivery) · bob 0% + 40%"), and the Graph keeps the table's
    columns, the executed delivery 1 of 16 October 2026 with the new
    delivery 2 under it. The state that version shows is the separate step
    on wills in several deliveries partly executed: not pinned here."""
    s, executed = _delivery_1_executed(make)
    if change == "coin":
        s.add_coin(300_000)
    else:
        s.set_fee(22)
    s.clock.travel(1)
    s.check()
    will = s.bw.willitems
    new = [w for w, i in will.items()
           if i.get_status("VALID") and int(i.tx.locktime) > D]
    assert len(new) == 3
    assert all(wv.built_after_on_chain(will[w], will[executed]) for w in new)
    assert wv.new_will_links(will) == set()
    history = wv.build_history(will, s.wallet)
    (version,) = history.versions
    assert set(version.items) == set(will)
    assert _columns(s) == [("2026-10-16", [1]), ("2027-10-15", [2])]
    roots, nodes = core_graph.build_graph(will)
    assert [n.wid for n in roots] == [executed]
    assert sorted(c.wid for c in nodes[executed].children) == sorted(new)


def test_an_executor_added_after_delivery_1_was_executed_stays_one_version(make):
    """Rules 4 and 5 of ``new_will_links``: the added will-executor's copy
    of delivery 2 is built after delivery 1 was executed, so conditions 1-3
    alone would make it a will of its own ("in force" with one copy, and
    an "executed" version still holding the two others); it has the
    content of the other copies of delivery 2 (rule 4) and pays only
    delivery 2's person (rule 5), so it stays with them. Rule 4 alone, for
    plain names, is in ``tests/test_core_will_versions.py``."""
    s, executed = _delivery_1_executed(make, three=False)
    s.clock.travel(1)
    s.world.add_executor()
    s.check()
    will = s.bw.willitems
    (added,) = _copies(s, "https://we2.test")
    assert will[added].father == executed
    assert wv.built_after_on_chain(will[added], will[executed])
    assert wv.new_will_links(will) == set()
    history = wv.build_history(will, s.wallet)
    assert len(history.versions) == 1
    assert added in history.versions[0].items
    assert _columns(s) == [("2026-10-16", [1]), ("2027-10-16", [2])]


def test_a_new_will_in_several_deliveries_on_the_coin_that_came_back(make):
    """The owner's case with a table of deliveries as the new will: its
    delivery 1 starts a version, its delivery 2 stays under it."""
    s = make()
    me = s.wallet.get_receiving_addresses()[5]
    s.bw.heirs["alice"] = [fk.HEIR_ADDR["alice"], "70%", D]
    s.bw.heirs["me"] = [me, "30%", D]
    del s.bw.heirs["bob"]
    s.bw.heirs.save()
    s.check()
    s.clock.travel(10)
    _mine(s, _copies(s, "https://we1.test")[0])
    s.clock.travel(11)
    s.set_heirs(alice="50%", bob="50%")
    _table(s, NEW)
    s.check()
    history = wv.build_history(s.bw.willitems, s.wallet)
    assert _versions(history) == [(1, "executed", "2026-10-16", 3),
                                  (2, "in_force", "2030-10-15", 12)]
    assert _future(s, history) == ("Your will is in force",
                                   ["2030-10-15", "2031-10-15"])
    assert _columns(s) == [("2030-10-15", [1]), ("2031-10-15", [2])]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
