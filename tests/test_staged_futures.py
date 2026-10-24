"""Every possible future of a will in several deliveries pays the plan
(CHANGELOG #187).

Owner's study of 2026-10-24 (R-1, confirmed in flowkit, 13 variants): a
will-executor excluded or removed after it received the will keeps copies
nobody can recall; the build made new copies only for the selected
will-executors and a later delivery was dated only against its own parent,
so after a change of delivery 3's shares the excluded will-executor's
delivery-1 and delivery-2 copies kept old subtrees: 15 of 23 possible
futures paid the old shares, 6 of 7 with two will-executors, and the Check
said "will ok"; selecting it again changed nothing. Two rules now:

1. a rebuild that does not keep every VALID copy of the will in force as
   it is dates the WHOLE chain from delivery 1, one day before the earliest
   delivery-1 copy in force, of any will-executor (``Will.date_chain``);
2. the Check verifies the possible futures of the VALID copies against the
   plan (``Will.is_will_valid``, ``bal.core.futures``); a future paying
   another plan means "a new will must be prepared"; a rebuild that would
   still leave one is refused (``WillChainError``).

``tests/flowkit.py`` wallets: three fake will-executors, each with its own
fee address; the three-date will of ``test_multiverse_chain_rebuild.py``.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_staged_futures.py
"""

import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

import flowkit as fk
import test_multiverse_chain_rebuild as chain
from PyQt6.QtWidgets import QApplication

from bal.core import delivery, futures
from bal.core import will_changes as wc
from bal.core.will import FuturesOffPlanException, Will
from bal.engine import context, instalments

_APP = QApplication.instance() or QApplication(sys.argv)

make = chain.make
WE = ["https://we0.test", "https://we1.test", "https://we2.test"]
SIGN = ("sign your will",)
D1 = chain.D1


# ------------------------------------------------------------- the core

COIN = "{:064x}".format(999)


def txid(n, i):
    """The txid of the copy of delivery ``n`` by will-executor ``i``."""
    return "{:064x}".format(100 * n + i)


class Item:
    """A will item as the core reads it."""

    def __init__(self, heirs, locktime, url, parent=COIN):
        """``heirs``: ``{row name: [address, amount, date]}``."""
        self.heirs = heirs
        prevout = SimpleNamespace(prevout=SimpleNamespace(txid=bytes.fromhex(parent)))
        self.tx = SimpleNamespace(locktime=locktime, inputs=lambda: [prevout])
        self.we = {"url": url}


def rows(n, luca, john):
    """The rows of delivery ``n``."""
    suffix = "" if n == 1 else "§{}".format(n)
    return {"luca" + suffix: ["L", "{}%".format(luca), 0],
            "john" + suffix: ["J", "{}%".format(john), 0]}


PLAN = [futures.rows_paid(rows(1, 10, 10)), futures.rows_paid(rows(2, 10, 10)),
        futures.rows_paid(rows(3, 30, 30))]


def tree(dates=(1000, 2000, 3000), executors=3, shares=((10, 10), (10, 10), (30, 30))):
    """A full tree: every will-executor's copy of every delivery under
    every parent."""
    items = {}
    for i in range(executors):
        items[txid(1, i)] = Item(rows(1, *shares[0]), dates[0], WE[i])
    for i in range(executors):
        for p in range(executors):
            key = "{:064x}".format(2000 + 10 * p + i)
            items[key] = Item(rows(2, *shares[1]), dates[1], WE[i], txid(1, p))
            for j in range(executors):
                items["{:064x}".format(30000 + 100 * p + 10 * i + j)] = Item(
                    rows(3, *shares[2]), dates[2], WE[j], key)
    return items


def test_plan_of_rows_and_rows_paid():
    """The plan from the rows the build uses; a rename changes nothing; the
    will-executor fee rows are left out."""
    heirs = dict(rows(1, 10, 10), **rows(2, 10, 10), **rows(3, 30, 30))
    heirs['w!ll3x3c"https://we0.test"1'] = ["F", 1000, 0]
    assert futures.plan_of_rows(heirs) == PLAN
    renamed = {"marco": ["L", "10%", 0], "john": ["J", "10%", 0]}
    assert futures.rows_paid(renamed) == PLAN[0]
    assert futures.plan_of_rows({}) == []


def test_futures_of_a_full_tree():
    """3 x 3 x 3 = 27 futures, all paying the plan."""
    items = tree()
    paths = futures.possible_futures(items)
    assert len(paths) == 27 and all(len(p) == 3 for p in paths)
    assert futures.futures_off_plan(items, PLAN) == []


def test_only_the_earliest_copies_start_a_future():
    """Old delivery-1 copies dated later cannot be mined first: left out."""
    items = tree()
    items[txid(1, 0)].tx.locktime = 999        # one root one day earlier
    paths = futures.possible_futures(items)
    assert len(paths) == 9 and {p[0] for p in paths} == {txid(1, 0)}


def test_a_subtree_with_old_shares_is_off_plan():
    """The copies of delivery 3 under one delivery-2 copy pay other shares:
    those futures are off plan, the others are not."""
    items = tree()
    bad_parent = "{:064x}".format(2000 + 10 * 0 + 2)       # we2's copy under we0
    for item in items.values():
        if futures.spent_ids(item) == {bad_parent}:
            item.heirs = rows(3, 20, 40)
    off = futures.futures_off_plan(items, PLAN)
    assert len(off) == 3 and all(p[1] == bad_parent for p in off)
    assert "we0.test -> we2.test -> " in futures.future_text(off[0], items)


def test_a_missing_level_is_off_plan():
    """A future that stops at delivery 2 does not pay the plan."""
    items = tree()
    gone = [w for w, i in items.items() if futures.spent_ids(i) == {"{:064x}".format(2000)}]
    for w in gone:
        del items[w]
    off = futures.futures_off_plan(items, PLAN)
    assert len(off) == 1 and len(off[0]) == 2


def test_strange_items_never_crash():
    """A copy spending its own id, one without a readable transaction, a
    loop: left out, no error, the other futures intact."""
    items = tree()
    loop = "{:064x}".format(7)
    items[loop] = Item(rows(2, 1, 1), 2000, WE[0], loop)
    broken = Item(rows(2, 1, 1), 2000, WE[0])
    broken.tx = SimpleNamespace(locktime=2000, inputs=lambda: (_ for _ in ()).throw(ValueError()))
    items["{:064x}".format(8)] = broken
    assert len(futures.possible_futures(items)) == 27
    assert futures.futures_off_plan({}, PLAN) == []
    assert futures.futures_off_plan(items, []) == []


# ------------------------------------------------------ the engine, flowkit

def plan(s):
    """The plan of the wallet: the rows the build uses."""
    return futures.plan_of_rows(instalments.effective_heirs(s.bw))


def valid_items(s):
    """The VALID copies, what the Check verifies."""
    return {w: i for w, i in s.bw.willitems.items() if i.get_status("VALID")}


def off_plan(s):
    """The possible futures of the VALID copies that pay another plan."""
    return futures.futures_off_plan(valid_items(s), plan(s))


def in_force_off_plan(s):
    """The same over the will in force (what the will-executors hold)."""
    return futures.futures_off_plan(context.in_force_items(s.bw), plan(s))


def d1_dates(s):
    """The dates of the VALID delivery-1 copies."""
    return sorted({int(i.tx.locktime) for i in valid_items(s).values()
                   if wc.delivery_number(i) == 1})


def set_shares(s, n, shares):
    """Delivery ``n``'s shares through the engine's one validated write."""
    rows_ = delivery.table_rows(s.bw.will_settings)
    rows_[n - 1]["shares"] = dict(shares)
    context.set_instalments(s.bw, rows_)


def asked(s):
    """The questions of the last Check."""
    return [q[0] for q in s.recorder.steps[-1]["questions"]]


def staged(make):
    """A will in three deliveries, signed, sent and verified."""
    s = make()
    chain.three_dates(s)
    s.check()
    assert off_plan(s) == [] and in_force_off_plan(s) == []
    return s


def test_no_exclusion_delivery_3_changed(make):
    """Rule 1: delivery 3's shares changed: the whole chain is redone from
    delivery 1, one day earlier; every future pays the new plan."""
    s = staged(make)
    set_shares(s, 3, {"luca": "20%", "john": "40%"})
    s.check()
    assert d1_dates(s) == [D1 - fk.DAY]
    assert off_plan(s) == [] and in_force_off_plan(s) == []
    assert len(valid_items(s)) == 39
    s.check()
    assert asked(s) == []                       # the second Check is clean


@pytest.mark.parametrize("how", ["exclude", "deselect", "remove"])
def test_excluded_executor_delivery_3_changed(make, how):
    """R-1: C excluded (or deselected, or removed), delivery 3's shares
    changed: no possible future pays the old shares."""
    s = staged(make)
    if how == "exclude":
        assert s.bw.exclude_willexecutor(WE[2])
    elif how == "deselect":
        s.bw.willexecutors[WE[2]]["selected"] = False
    else:
        del s.bw.willexecutors[WE[2]]
    set_shares(s, 3, {"luca": "20%", "john": "40%"})
    s.check()
    assert d1_dates(s) == [D1 - fk.DAY]
    assert off_plan(s) == [] and in_force_off_plan(s) == []
    s.check()
    assert asked(s) == []


def test_excluded_executor_delivery_2_changed(make):
    """R-1 at delivery 2: the same."""
    s = staged(make)
    assert s.bw.exclude_willexecutor(WE[2])
    set_shares(s, 2, {"luca": "5%", "john": "15%"})
    set_shares(s, 3, {"luca": "30%", "john": "30%"})
    s.check()
    assert off_plan(s) == [] and in_force_off_plan(s) == []


def test_excluded_executor_twice(make):
    """Delivery 3 changed twice with C excluded: still no old future, one
    day earlier each time."""
    s = staged(make)
    assert s.bw.exclude_willexecutor(WE[2])
    set_shares(s, 3, {"luca": "20%", "john": "40%"})
    s.check()
    set_shares(s, 3, {"luca": "10%", "john": "50%"})
    s.check()
    assert d1_dates(s) == [D1 - 2 * fk.DAY]
    assert off_plan(s) == [] and in_force_off_plan(s) == []


def test_two_executors_one_excluded(make):
    """Two will-executors, one excluded (6 of 7 futures were old)."""
    s = make()
    del s.world.executors[WE[2]]
    s.bw.willexecutors.pop(WE[2], None)
    chain.three_dates(s)
    s.check()
    assert s.bw.exclude_willexecutor(WE[1])
    set_shares(s, 3, {"luca": "20%", "john": "40%"})
    s.check()
    assert off_plan(s) == [] and in_force_off_plan(s) == []


def test_backup_copy_and_an_excluded_executor(make):
    """With the copy without a will-executor too."""
    s = make()
    s.plugin.NO_WILLEXECUTOR.set(True)
    chain.three_dates(s)
    s.check()
    assert s.bw.exclude_willexecutor(WE[2])
    set_shares(s, 3, {"luca": "20%", "john": "40%"})
    s.check()
    assert off_plan(s) == [] and in_force_off_plan(s) == []


def test_fee_changed_with_an_excluded_executor(make):
    """The fee: delivery 1 one day earlier, as before; no old future."""
    s = staged(make)
    assert s.bw.exclude_willexecutor(WE[2])
    s.set_fee(3)
    s.check()
    assert d1_dates(s) == [D1 - fk.DAY]
    assert off_plan(s) == []


def test_one_date_then_staged_with_an_excluded_executor(make):
    """A will with one date, C excluded, then three deliveries: the new
    delivery 1 is one day earlier than the old will (of C too)."""
    s = make()
    s.check()
    assert s.bw.exclude_willexecutor(WE[2])
    chain.three_dates(s)
    s.check()
    assert d1_dates(s) == [D1 - fk.DAY]
    assert off_plan(s) == []


def test_an_executor_added_later_keeps_the_dates(make):
    """A will-executor added after the staged will: its copies are new, the
    copies in force are all kept: no day lost (as with one date)."""
    s = make()
    del s.world.executors[WE[2]]
    s.bw.willexecutors.pop(WE[2], None)
    chain.three_dates(s)
    s.check()
    s.world.add_executor()
    s.check()
    assert d1_dates(s) == [D1]
    assert off_plan(s) == [] and len(valid_items(s)) == 39


def test_the_check_finds_an_old_future_and_rebuilds(make, monkeypatch):
    """Rule 2 alone: a wallet left as before #187 (rule 1 off while the
    change was signed: C's copies keep old subtrees). The Check finds the
    futures off plan ("a new will must be prepared"), rebuilds from
    delivery 1 and heals; selecting C again is not needed."""
    s = staged(make)
    assert s.bw.exclude_willexecutor(WE[2])
    monkeypatch.setattr(Will, "_pre_empt_whole_chain", staticmethod(lambda *a, **k: False))
    monkeypatch.setattr(Will, "_check_futures", staticmethod(lambda *a, **k: None))
    set_shares(s, 3, {"luca": "20%", "john": "40%"})
    s.check()
    assert off_plan(s), "the old state must be reproduced"
    monkeypatch.undo()
    with pytest.raises(FuturesOffPlanException):
        context.check_will(s.bw)
    s.check()
    assert asked(s) == ["password"]
    assert off_plan(s) == [] and in_force_off_plan(s) == []
    s.check()
    assert asked(s) == []


def test_a_will_with_one_date_is_not_checked_for_futures(make):
    """One date: the futures rule does not apply (the plan has one level;
    the old rules cover it). The Check stays clean."""
    s = make()
    s.check()
    s.set_fee(3)
    s.check()
    s.check()
    assert asked(s) == []


def test_a_new_fee_address_of_a_copy_not_received_costs_no_day(make):
    """Independent review of #187: C's copies were signed but never received
    (the sending failed), then C gives a new fee address (#141): only C's
    copies are redone, nothing moves (nobody holds the old ones), no old
    future."""
    s = make()
    s.world.down = {WE[2]}
    chain.three_dates(s)
    s.check()
    s.world.down = set()
    before = {w: i for w, i in valid_items(s).items() if (i.we or {}).get("url") != WE[2]}
    assert all(not i.get_status("PUSHED") for i in valid_items(s).values()
               if (i.we or {}).get("url") == WE[2])
    s.world.executors[WE[2]]["address"] = fk.address(7)
    s.check()
    assert d1_dates(s) == [D1]
    assert {w for w in valid_items(s) if w in before} == set(before)
    assert off_plan(s) == []
    s.check()
    assert asked(s) == []


def test_an_executor_added_while_another_is_excluded(make):
    """Two will-executors, B excluded (its copies received, they cannot be
    recalled), then C added: the chain is redated one day earlier (rule 1:
    a held copy is not kept as it is), so B's copies are pre-empted; no old
    future."""
    s = make()
    del s.world.executors[WE[2]]
    s.bw.willexecutors.pop(WE[2], None)
    chain.three_dates(s)
    s.check()
    assert s.bw.exclude_willexecutor(WE[1])
    s.world.add_executor()                      # we2 appears, selected
    s.check()
    assert d1_dates(s) == [D1 - fk.DAY]
    assert off_plan(s) == [] and in_force_off_plan(s) == []
    s.check()
    assert asked(s) == []
