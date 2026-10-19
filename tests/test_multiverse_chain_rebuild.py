"""Rebuilding a will with delivery instalments (multiverse, CHANGELOG #166).

With the hidden multiverse option each later delivery spends the change of
the one before, and every will-executor holds a copy of every delivery: a
chain of 3 + 9 (two dates) or 3 + 9 + 27 (three dates) transactions with
three will-executors. The first build always worked; every REBUILD that
moved a date (new funds, the miner fee rate, an heir changed) failed with
"list index out of range" or saved a broken will (children spending their
own txid, two keys for one item). Here, with ``tests/flowkit.py`` (three
fake will-executors, each with its own fee address; throw-away test wallet
with the test password; fixed clock):

* every rebuild class gives a correct chain: each child spends its
  parent's CURRENT txid and output, is dated after it, every VALID item is
  signed, sent and verified, the pushed transactions are the stored ones,
  and a second build changes nothing;
* the first delivery spends every coin (owner, solution C), so new funds
  that cover it alone can no longer leave the old will executable;
* a later delivery's share changed: the whole chain one day earlier (D6);
* the dispatch: a will with one date never takes the chain path;
* :meth:`Will.chain_links` refuses broken links with a clear error;
* known and left for M3 (F1): after an anticipated rebuild the heirs'
  dates are not moved, so the NEXT Check asks for the paid invalidation.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_multiverse_chain_rebuild.py
"""

import os
import sys
from collections import Counter

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

import flowkit as fk
from electrum.transaction import tx_from_any
from PyQt6.QtWidgets import QApplication

from bal.core.will import Will, WillChainError
from bal.engine import build as engine_build

# Kept at module level: flowkit's window needs a Qt application.
_APP = QApplication.instance() or QApplication(sys.argv)

# Test value: the password of the throw-away test wallets.
TEST_PASSWORD = "flowkit-test"
D1 = fk.ts(2030, 3, 1)
D2 = fk.ts(2033, 3, 1)
D3 = fk.ts(2036, 3, 1)
ADDR = {"luca": fk.address(20), "john": fk.address(21),
        "alice": fk.address(10), "bob": fk.address(11)}
RBF = 0xFFFFFFFD


@pytest.fixture
def make(monkeypatch):
    """flowkit sessions: three will-executors, multiverse on, synchronized."""
    fk.ensure_event_loop()
    made = []

    def _make(multiverse=True):
        """One ADVANCED session with a password, miner fee 2 sat/vB."""
        s = fk.Session(monkeypatch, mode="advanced", password=TEST_PASSWORD,
                       fee=2, locktime=D1)
        s.world.add_executor()
        # The flowkit wallet is offline: synchronized for the #128 rules.
        monkeypatch.setattr(s.wallet, "is_up_to_date", lambda: True)
        s.plugin.ENABLE_MULTIVERSE.set(multiverse)
        made.append(s)
        return s

    yield _make
    for s in made:
        s.close()
    fk.stop_event_loop()


def _set_heirs(s, rows):
    """Replace the heirs by ``rows``: ``{name: (address, share, date)}``."""
    for name in list(s.bw.heirs.keys()):
        del s.bw.heirs[name]
    s.bw.heirs.save()
    for name, (addr, share, date) in rows.items():
        s.bw.heirs[name] = [addr, share, date]


def two_dates(s):
    """Shape A: alice 60 % in 2030, bob 40 % in 2033 (3 + 9 items)."""
    _set_heirs(s, {"alice": (ADDR["alice"], "60%", D1),
                   "bob": (ADDR["bob"], "40%", D2)})


def three_dates(s):
    """Shape B, the owner's model written with today's rows (the same
    address for one person at each date): 10/10, 10/10, 30/30 %."""
    _set_heirs(s, {
        "luca@1": (ADDR["luca"], "10%", D1), "john@1": (ADDR["john"], "10%", D1),
        "luca@2": (ADDR["luca"], "10%", D2), "john@2": (ADDR["john"], "10%", D2),
        "luca@3": (ADDR["luca"], "30%", D3), "john@3": (ADDR["john"], "30%", D3),
    })


SHAPES = {"two_dates": (two_dates, {1: 3, 2: 9}),
          "three_dates": (three_dates, {1: 3, 2: 9, 3: 27})}


def _change(s, name, field, value):
    """Change one field (0 address, 1 share, 2 date) of the heir ``name``."""
    heir = list(s.bw.heirs[name])
    heir[field] = value
    s.bw.heirs[name] = heir


def _level(wid, will):
    """1 for an item spending only wallet coins, 2 for its children..."""
    for txin in will[wid].tx.inputs():
        parent = txin.prevout.txid.hex()
        if parent in will and parent != wid:
            return 1 + _level(parent, will)
    return 1


def _valid(s):
    """``{txid: item}`` of the VALID items."""
    return {k: w for k, w in s.bw.willitems.items() if w.get_status("VALID")}


def _dates(s):
    """``{level: {date}}`` of the VALID items."""
    out = {}
    for k, w in _valid(s).items():
        out.setdefault(_level(k, s.bw.willitems), set()).add(fk.fmt(w.tx.locktime))
    return out


def audit(s, levels, pushed_from=0):
    """Assert the will is a correct, signed and sent chain.

    ``levels``: the expected count of VALID items per level.
    """
    will = s.bw.willitems
    assert Counter(id(w) for w in will.values()).most_common(1)[0][1] == 1, \
        "one item under two keys"
    valid = _valid(s)
    assert dict(Counter(_level(k, will) for k in valid)) == levels
    for key, item in valid.items():
        assert item.tx.txid() == key
        assert item.get_status("COMPLETE") and item.get_status("PUSHED")
        assert item.get_status("CHECKED")
        for txin in item.tx.inputs():
            parent_id = txin.prevout.txid.hex()
            assert parent_id != key, "an item spends itself"
            if parent_id not in will:
                continue
            parent = will[parent_id]
            assert parent.get_status("VALID")
            assert txin.prevout.out_idx < len(parent.tx.outputs())
            assert int(item.tx.locktime) > int(parent.tx.locktime)
            assert txin.nsequence == RBF
    for _url, wid, raw in s.world.pushed[pushed_from:]:
        if raw:
            assert tx_from_any(raw).txid() == wid


def _second_build_changes_nothing(s):
    """A second build at the engine level: same items, nothing to sign."""
    before = {k: id(w) for k, w in _valid(s).items()}
    engine_build.build_will(s.bw, with_creation_date=True)
    assert {k: id(w) for k, w in _valid(s).items()} == before
    assert all(w.get_status("COMPLETE") for w in _valid(s).values())


def _first(s, shape):
    """The first will of ``shape``, signed, sent and verified."""
    setup, levels = SHAPES[shape]
    setup(s)
    s.check()
    audit(s, levels)
    return levels


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_first_build(make, shape):
    """The first will: 12 or 39 items, all signed, sent and verified, one
    password prompt; each executor holds one copy of every possible chain."""
    s = make()
    levels = _first(s, shape)
    pushed = Counter(url for url, _w, _r in s.world.pushed)
    assert set(pushed.values()) == {sum(levels.values()) // 3}
    assert [a[0] for a in s.user.asked] == ["password"]


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_new_coin_rebuilds_a_correct_chain(make, shape):
    """The owner's probe B2/A2: a new coin. The first delivery spends both
    coins one day earlier, the children follow their new parents, the old
    chain is replaced, and a second build changes nothing."""
    s = make()
    levels = _first(s, shape)
    old = set(_valid(s))
    s.clock.travel(1)
    s.add_coin(250_000)
    pushed = len(s.world.pushed)
    s.check()
    audit(s, levels, pushed)
    assert not old & set(_valid(s))
    dates = _dates(s)
    assert dates[1] == {"2030-02-28"}
    assert dates[2] == {"2033-03-01"}
    for k, w in _valid(s).items():
        if _level(k, s.bw.willitems) == 1:
            assert len(w.tx.inputs()) == 2
    assert all(s.bw.willitems[k].get_status("REPLACED") for k in old)
    _second_build_changes_nothing(s)


def test_new_coin_covering_the_first_delivery_alone(make):
    """Solution C (owner): a new coin big enough for the first delivery
    alone. Before, the new first delivery spent only it and left the old
    coin to the next delivery: the old and the new first delivery could
    both be executed. Now it spends both coins, one day earlier."""
    s = make()
    levels = _first(s, "three_dates")
    s.clock.travel(1)
    s.add_coin(300_000)
    pushed = len(s.world.pushed)
    s.check()
    audit(s, levels, pushed)
    for k, w in _valid(s).items():
        lv = _level(k, s.bw.willitems)
        assert len(w.tx.inputs()) == (2 if lv == 1 else 1)
    assert _dates(s)[1] == {"2030-02-28"}
    _second_build_changes_nothing(s)


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_three_coins_then_a_new_one(make, shape):
    """Several coins from the start: the first delivery spends all of them,
    the later ones only the change; a new coin then joins the first one."""
    s = make()
    s.add_coin(500_000)
    s.add_coin(70_000)
    levels = _first(s, shape)
    for k, w in _valid(s).items():
        lv = _level(k, s.bw.willitems)
        assert len(w.tx.inputs()) == (3 if lv == 1 else 1)
    s.clock.travel(1)
    s.add_coin(2_000_000)
    pushed = len(s.world.pushed)
    s.check()
    audit(s, levels, pushed)
    for k, w in _valid(s).items():
        lv = _level(k, s.bw.willitems)
        assert len(w.tx.inputs()) == (4 if lv == 1 else 1)
    _second_build_changes_nothing(s)


def test_fee_rate_change(make):
    """A new miner fee rate: the chain one day earlier, relinked."""
    s = make()
    levels = _first(s, "three_dates")
    s.clock.travel(1)
    s.set_fee(3)
    pushed = len(s.world.pushed)
    s.check()
    audit(s, levels, pushed)
    assert _dates(s) == {1: {"2030-02-28"}, 2: {"2033-03-01"}, 3: {"2036-03-01"}}
    _second_build_changes_nothing(s)


def test_later_delivery_share_changed(make):
    """luca's share of delivery 2 from 10 % to 15 %: the amounts of
    delivery 1 change too (the shares are spread over every delivery)
    although its heir rows do not. Before, the old and the new first
    delivery stayed VALID on the same coins on the same day (78 VALID
    items); now the whole chain is one day earlier (owner decision D6)."""
    s = make()
    levels = _first(s, "three_dates")
    old = set(_valid(s))
    s.clock.travel(1)
    _change(s, "luca@2", 1, "15%")
    pushed = len(s.world.pushed)
    s.check()
    audit(s, levels, pushed)
    assert not old & set(_valid(s))
    assert _dates(s)[1] == {"2030-02-28"}
    _second_build_changes_nothing(s)


def test_later_delivery_address_changed_keeps_the_first(make):
    """john's address of delivery 2 changed: delivery 1 is the signed one
    (not sent again); delivery 2 one day earlier, delivery 3 relinked."""
    s = make()
    levels = _first(s, "three_dates")
    firsts = {k for k in _valid(s) if _level(k, s.bw.willitems) == 1}
    s.clock.travel(1)
    _change(s, "john@2", 0, fk.address(30))
    pushed = len(s.world.pushed)
    s.check()
    audit(s, levels, pushed)
    assert {k for k in _valid(s) if _level(k, s.bw.willitems) == 1} == firsts
    assert len(s.world.pushed) - pushed == 36
    assert _dates(s) == {1: {"2030-03-01"}, 2: {"2033-02-28"}, 3: {"2036-03-01"}}
    _second_build_changes_nothing(s)


@pytest.mark.parametrize("which,expected", [
    ("1", {1: {"2030-01-30"}, 2: {"2033-03-01"}, 3: {"2036-03-01"}}),
    ("2", {1: {"2030-03-01"}, 2: {"2033-01-30"}, 3: {"2036-03-01"}}),
])
def test_one_delivery_earlier(make, which, expected):
    """One delivery 30 days earlier: only that delivery and the later ones
    are new; the earlier ones stay the signed ones."""
    s = make()
    levels = _first(s, "three_dates")
    s.clock.travel(1)
    for name in ("luca@" + which, "john@" + which):
        _change(s, name, 2, s.bw.heirs[name][2] - 30 * fk.DAY)
    pushed = len(s.world.pushed)
    s.check()
    audit(s, levels, pushed)
    assert _dates(s) == expected
    _second_build_changes_nothing(s)


def test_one_date_never_takes_the_chain_path(make, monkeypatch):
    """The dispatch (#166): with one delivery date (multiverse off, the
    default) the chain pass never runs and today's dating runs, also for a
    new coin and a fee change."""
    calls = []
    real = Will.date_chain
    monkeypatch.setattr(Will, "date_chain", staticmethod(
        lambda *a, **k: (calls.append(1), real(*a, **k))[1]))
    s = make(multiverse=False)
    s.check()
    s.clock.travel(1)
    s.add_coin(250_000)
    s.check()
    s.clock.travel(1)
    s.set_fee(3)
    s.check()
    assert calls == []
    assert Will.chain_links(_valid(s)) == []
    assert all(w.get_status("COMPLETE") and w.get_status("PUSHED")
               for w in _valid(s).values())
    assert len(_valid(s)) == 3


def test_broken_links_are_refused(make):
    """:meth:`Will.chain_links` names the problem instead of an IndexError:
    an input spending its own item, an output its parent does not have."""
    s = make()
    _first(s, "two_dates")
    will = {k: w.copy() for k, w in _valid(s).items()}
    links = Will.chain_links(will)
    assert len(links) == 9
    child, txin, parent, idx = links[0]
    from electrum.transaction import TxOutpoint
    from electrum.util import bfh
    key = child.tx.txid()
    txin.prevout = TxOutpoint(txid=bfh(parent.tx.txid()), out_idx=99)
    with pytest.raises(WillChainError, match="does not have"):
        Will.chain_links(will)
    txin.prevout = TxOutpoint(txid=bfh(key), out_idx=idx)
    with pytest.raises(WillChainError, match="its own output"):
        Will.chain_links(will)


@pytest.mark.xfail(strict=True, reason="F1, left for M3: the heirs' dates "
                   "do not follow the one-day anticipation of a chain")
def test_next_check_after_an_anticipated_rebuild_asks_nothing(make):
    """After the new-coin rebuild, the next Check should find the will in
    force. Today it reads the heirs' dates (2030-03-01) as a postpone of the
    signed chain (2030-02-28) and asks for the paid invalidation."""
    s = make()
    _first(s, "three_dates")
    s.clock.travel(1)
    s.add_coin(250_000)
    s.check()
    s.clock.travel(1)
    s.check(cancel_on=("invalidat",))
    assert not any("nvalidat" in a[1] for a in s.user.asked)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
