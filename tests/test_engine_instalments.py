"""Staged deliveries in the engine: the table of deliveries (#168, M3a).

Owner decisions of 2026-10-19 (``context/MULTIVERSE-PLAN.md``, M3): a will
may be delivered in up to three deliveries, the same dates for every heir,
each heir with a share of the WHOLE estate at each date (the table adds up
to 100 %), stored per wallet in ``will_settings["instalments"]``. The
stored heirs keep one row per person; the build and the check use rows
derived in memory: "luca" for delivery 1, "luca§2" and "luca§3" after.
BAL's hidden global option ``bal_enable_multiverse`` is no longer read
(D7). A table that no longer fits the heirs stops every flow (decision 3).

Here, with ``tests/flowkit.py`` (three fake will-executors, each with its
own fee address; throw-away test wallet with the test password; fixed
clock):

* the chain of the table: 3 + 9 + 27 items, the row names of each
  delivery, the amounts of the whole estate, the person rows unchanged;
* a 0 % cell gives no row, the message goes with delivery 1;
* the one validated write (``context.set_instalments``) and its refusals,
  the wallet's date kept before delivery 2;
* an heir added or removed without the table: everything stops (Check,
  Prepare, command line), nothing is built or sent;
* a rename in the heirs and in the table is a pure rename (nothing signed);
* delivery 2 later: the paid invalidation; earlier: free (D6);
* the table removed: one delivery again; two wallets keep their own form;
  BAL's global option is not read; the table survives a reload.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_engine_instalments.py
"""

import json
import os
import sys
from collections import Counter

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

import flowkit as fk
from PyQt6.QtWidgets import QApplication

from bal.core import delivery
from bal.core.heirs import message_address
from bal.core.will import Will
from bal.engine import context

# Kept at module level: flowkit's window needs a Qt application.
_APP = QApplication.instance() or QApplication(sys.argv)

# Test value: the password of the throw-away test wallets.
TEST_PASSWORD = "flowkit-test"
D1 = fk.ts(2030, 3, 1)
D2 = fk.ts(2031, 3, 1)
D3 = fk.ts(2032, 3, 1)
ADDR = {"luca": fk.address(20), "john": fk.address(21),
        "maria": fk.address(22), "marco": fk.address(20)}
_EXECUTOR_HEIR = 'w!ll3x3c"'
MISMATCH = "no longer matches the heirs"


@pytest.fixture
def make(monkeypatch):
    """flowkit sessions: three will-executors, synchronized."""
    fk.ensure_event_loop()
    made = []

    def _make(**kw):
        """One ADVANCED session with a password, miner fee 2 sat/vB."""
        kw.setdefault("mode", "advanced")
        kw.setdefault("locktime", D1)
        s = fk.Session(monkeypatch, password=TEST_PASSWORD, fee=2, **kw)
        s.world.add_executor()
        # The flowkit wallet is offline: synchronized for the #128 rules.
        monkeypatch.setattr(s.wallet, "is_up_to_date", lambda: True)
        made.append(s)
        return s

    yield _make
    for s in made:
        s.close()
    fk.stop_event_loop()


def _people(s, *names):
    """Replace the heirs by one row per person (the amount is not used
    with a table)."""
    for name in list(s.bw.heirs.keys()):
        del s.bw.heirs[name]
    s.bw.heirs.save()
    for name in names:
        s.bw.heirs[name] = [ADDR[name], "50%", D1]


def owner_will(s):
    """luca and john, 10/10 %, 10/10 %, 30/30 % on D1, D2, D3."""
    _people(s, "luca", "john")
    s.set_instalments((D1, {"luca": "10%", "john": "10%"}),
                      (D2, {"luca": "10%", "john": "10%"}),
                      (D3, {"luca": "30%", "john": "30%"}))


def _edit(s, change):
    """Rewrite the stored table: ``change(rows)`` edits it in place."""
    rows = delivery.table_rows(s.bw.will_settings)
    change(rows)
    context.set_instalments(s.bw, rows)


def _valid(s):
    """``{txid: item}`` of the VALID items."""
    return {k: w for k, w in s.bw.willitems.items() if w.get_status("VALID")}


def _level(wid, will):
    """1 for an item spending only wallet coins, 2 for its children..."""
    for txin in will[wid].tx.inputs():
        parent = txin.prevout.txid.hex()
        if parent in will and parent != wid:
            return 1 + _level(parent, will)
    return 1


def _by_level(s):
    """``{level: [item]}`` of the VALID items."""
    out = {}
    for k, w in _valid(s).items():
        out.setdefault(_level(k, s.bw.willitems), []).append(w)
    return out


def _names(item):
    """The heir-row names of ``item``, will-executor fees left out."""
    return {n for n in (item.heirs or {}) if not str(n).startswith(_EXECUTOR_HEIR)}


def _paid(item, address):
    """Satoshis ``item`` pays to ``address``."""
    return sum(o.value for o in item.tx.outputs() if o.address == address)


def _history(s):
    """Every row the last Check wrote, as plain text."""
    return " ".join(fk.strip(str(r)) for r in s.recorder.steps[-1].get("history", []))


def _sent_owner_will(make):
    """The owner's three-delivery will, signed, sent and verified."""
    s = make()
    owner_will(s)
    s.check()
    assert {lv: len(items) for lv, items in _by_level(s).items()} == {1: 3, 2: 9, 3: 27}
    assert all(w.get_status("COMPLETE") and w.get_status("PUSHED")
               for w in _valid(s).values())
    return s


# --------------------------------------------------------------- the build

def test_a_table_builds_the_chain_of_its_deliveries(make):
    """3 + 9 + 27 items; each delivery carries its own rows; the amounts are
    the shares of the whole estate (fees aside); the stored heirs keep one
    row per person, on the will's date."""
    s = _sent_owner_will(make)
    levels = _by_level(s)
    assert all(_names(w) == {"luca", "john"} for w in levels[1])
    assert all(_names(w) == {"luca§2", "john§2"} for w in levels[2])
    assert all(_names(w) == {"luca§3", "john§3"} for w in levels[3])
    assert sorted({int(w.tx.locktime) for w in levels[1]}) == [D1]
    assert sorted({int(w.tx.locktime) for w in levels[2]}) == [D2]
    assert sorted({int(w.tx.locktime) for w in levels[3]}) == [D3]
    for level, share in ((1, 0.10), (2, 0.10), (3, 0.30)):
        for name in ("luca", "john"):
            paid = _paid(levels[level][0], ADDR[name])
            assert paid == pytest.approx(share * 1_000_000, rel=0.01), (level, name)
    assert dict(s.bw.heirs) == {
        "luca": [ADDR["luca"], "50%", D1], "john": [ADDR["john"], "50%", D1]}
    assert [a[0] for a in s.user.asked] == ["password"]


def test_a_zero_share_makes_no_row(make):
    """john has 0 % at delivery 2: no row "john§2", no output to him."""
    s = make()
    _people(s, "luca", "john")
    s.set_instalments((D1, {"luca": "25%", "john": "25%"}),
                      (D2, {"luca": "20%", "john": "0%"}),
                      (D3, {"luca": "15%", "john": "15%"}))
    s.check()
    levels = _by_level(s)
    assert all(_names(w) == {"luca§2"} for w in levels[2])
    assert all(_paid(w, ADDR["john"]) == 0 for w in levels[2])
    assert all(_paid(w, ADDR["john"]) > 0 for w in levels[3])


def test_the_message_goes_with_delivery_1(make):
    """Owner decision 4: the OP_RETURN message is not a column of the
    table; it is in every copy of delivery 1 and in no later one."""
    s = make()
    _people(s, "luca", "john")
    s.bw.heirs["message"] = [message_address("Ciao"), "0", D1]
    s.set_instalments((D1, {"luca": "50%", "john": "0%"}),
                      (D2, {"luca": "0%", "john": "50%"}))
    s.check()

    def returns(w):
        return sum(1 for o in w.tx.outputs() if o.scriptpubkey[:1] == b"\x6a")
    levels = _by_level(s)
    assert [returns(w) for w in levels[1]] == [1, 1, 1]
    assert all(returns(w) == 0 for w in levels[2])
    assert all("message" in _names(w) for w in levels[1])


def test_the_amounts_check_reads_the_table(make):
    """The person rows' amounts are not used with a table (here 1 % each):
    the amounts check reads the rows of every delivery, which add up to
    100 %, so it says nothing about recalculated shares."""
    s = make()
    _people(s, "luca", "john")
    for name in ("luca", "john"):
        s.bw.heirs[name] = [ADDR[name], "1%", D1]
    s.set_instalments((D1, {"luca": "50%", "john": "0%"}),
                      (D2, {"luca": "0%", "john": "50%"}))
    s.check()
    step = json.dumps(s.recorder.steps[-1], default=str)
    assert "add up to" not in step and "in proportion" not in step
    assert "adjustment of the amounts" not in step
    assert {lv: len(items) for lv, items in _by_level(s).items()} == {1: 3, 2: 9}


def test_planned_delivery_reads_delivery_1(make):
    """The date in force is delivery 1's, whatever the dict order."""
    s = _sent_owner_will(make)
    assert Will.planned_delivery(s.bw.willitems, s.bw.will_settings["locktime"]) == (
        D1, True)


# --------------------------------------------------------- the one write

def test_set_instalments_stores_the_table_the_schema_and_the_date(make):
    """The whole table, schema 3 (only with a table, owner choice B), and
    the wallet's date = delivery 1."""
    s = make(locktime=D1 - 30 * fk.DAY)
    _people(s, "luca", "john")
    d = s.bw.will_settings
    assert d["settings_version"] == delivery.SCHEMA_VERSION
    table = s.set_instalments((D1, {"luca": "50%"}), (D2, {"john": "50%"}))
    assert d["instalments"] == table
    assert table[0]["shares"] == {"luca": "50%", "john": "0%"}
    assert d["settings_version"] == delivery.INSTALMENTS_SCHEMA_VERSION
    assert d["locktime"] == D1


@pytest.mark.parametrize("rows, match", [
    (((D1, {"luca": "50%"}), (D2, {"john": "40%"})), "add up to 90%"),
    (((D1, {"luca": "50%"}), (D1 + 27 * fk.DAY, {"john": "50%"})), "less than 28"),
    (((D1, {"luca": "100%"}),), "1 delivery dates"),
    (((D1, {"luca": "50%"}), (D2, {"maria": "50%"})), "not an heir"),
])
def test_a_bad_table_writes_nothing(make, rows, match):
    """Every rule is checked before anything is written."""
    s = make()
    _people(s, "luca", "john")
    before = dict(s.bw.will_settings)
    with pytest.raises(ValueError, match=match):
        s.set_instalments(*rows)
    assert dict(s.bw.will_settings) == before


def test_no_table_with_the_period_form(make):
    """#127 decision 10: the period form has one renewable date."""
    s = make(period_months=24)
    _people(s, "luca", "john")
    before = dict(s.bw.will_settings)
    date = s.bw.will_settings["locktime"]
    with pytest.raises(ValueError, match="period form"):
        s.set_instalments((date, {"luca": "50%"}), (date + 365 * fk.DAY, {"john": "50%"}))
    assert dict(s.bw.will_settings) == before


def test_no_period_form_with_a_table(make):
    """And the other way round: a wallet with a table cannot switch to the
    period form (#127 decision 10)."""
    s = make()
    owner_will(s)
    with pytest.raises(ValueError, match="table of deliveries"):
        context.set_delivery(s.bw, form="period", months=36)
    assert delivery.form_of(s.bw.will_settings) == "fixed"


def test_no_table_with_a_name_holding_the_separator(make):
    """An heir stored as "x§2" (before the rule) could collide."""
    s = make()
    _people(s, "luca")
    s.bw.heirs["x§2"] = [ADDR["john"], "50%", D1]
    with pytest.raises(ValueError, match="holds"):
        s.set_instalments((D1, {"luca": "50%"}), (D2, {"x§2": "50%"}))
    assert "instalments" not in s.bw.will_settings


def test_the_wills_date_stays_before_delivery_2(make):
    """Delivery 1 is the wallet's date: on or after delivery 2 is refused,
    an earlier date moves delivery 1 (the table's first row follows)."""
    s = make()
    owner_will(s)
    with pytest.raises(ValueError, match="before delivery 2"):
        s.set_date(D2)
    assert s.bw.will_settings["locktime"] == D1
    s.set_date(D1 - 10 * fk.DAY)
    assert delivery.table_rows(s.bw.will_settings)[0]["locktime"] == D1 - 10 * fk.DAY


# ----------------------------------------------- the table must fit the heirs

@pytest.mark.parametrize("change", ["added", "removed"])
def test_heirs_changed_without_the_table_stop_everything(make, change):
    """Owner decision 3: nothing is guessed. The Check, Prepare and the
    command line stop with the message; nothing is built, signed or sent;
    the will in force stays."""
    s = _sent_owner_will(make)
    before = set(_valid(s))
    pushed = len(s.world.pushed)
    if change == "added":
        s.bw.heirs["maria"] = [ADDR["maria"], "10%", D1]
    else:
        del s.bw.heirs["john"]
        s.bw.heirs.save()
    s.check()
    assert MISMATCH in _history(s)
    s.prepare()
    assert any(MISMATCH in str(p) for p in s.bw.popups)
    out = s.cli("prepare_will")
    assert MISMATCH in out.get("raised", ""), out
    assert set(_valid(s)) == before
    assert len(s.world.pushed) == pushed
    assert not s.user.asked


def test_a_rename_in_the_heirs_and_the_table_is_a_pure_rename(make):
    """luca becomes marco (same address, same shares), in the heirs and in
    the table: the items take the new names, delivery by delivery; nothing
    is signed or sent again."""
    s = _sent_owner_will(make)
    before = set(_valid(s))
    pushed = len(s.world.pushed)
    del s.bw.heirs["luca"]
    s.bw.heirs.save()
    s.bw.heirs["marco"] = [ADDR["marco"], "50%", D1]

    def rename(rows):
        for row in rows:
            row["shares"] = {"marco" if k == "luca" else k: v
                             for k, v in row["shares"].items()}
    _edit(s, rename)
    s.check()
    # Recognised as a rename up front (no rebuild), delivery by delivery.
    assert sorted(s.bw.heir_renames) == [
        ("luca", "marco"), ("luca§2", "marco§2"), ("luca§3", "marco§3")]
    assert set(_valid(s)) == before
    assert len(s.world.pushed) == pushed
    assert not s.user.asked
    names = set().union(*(_names(w) for w in _valid(s).values()))
    assert names == {"marco", "marco§2", "marco§3", "john", "john§2", "john§3"}


# ------------------------------------------------ dates of one delivery (D6)

def test_postponing_delivery_2_asks_the_paid_invalidation(make):
    """A later date of a signed delivery: the paid invalidation of the will
    in force, as for a will with one date."""
    s = _sent_owner_will(make)
    s.clock.travel(1)

    def later(rows):
        rows[1]["locktime"] += 30 * fk.DAY
    _edit(s, later)
    s.check(cancel_on=("invalidat",))
    assert any("nvalidat" in a[1] for a in s.user.asked)


def test_anticipating_delivery_2_is_free(make):
    """An earlier date of delivery 2: nothing to invalidate. #187 (owner's
    study, R-1): every change redoes the WHOLE chain, delivery 1 one day
    earlier than the signed one (before, delivery 1 stayed the signed one
    and a copy held by a will-executor no longer selected could keep an old
    subtree); deliveries 2 and 3 are new on it."""
    s = _sent_owner_will(make)
    firsts = {id(w) for w in _by_level(s)[1]}
    s.clock.travel(1)

    def earlier(rows):
        rows[1]["locktime"] -= 30 * fk.DAY
    _edit(s, earlier)
    s.check(cancel_on=("invalidat",))
    assert not any("nvalidat" in a[1] for a in s.user.asked)
    levels = _by_level(s)
    assert not ({id(w) for w in levels[1]} & firsts)
    assert sorted({int(w.tx.locktime) for w in levels[1]}) == [D1 - fk.DAY]
    assert sorted({int(w.tx.locktime) for w in levels[2]}) == [D2 - 30 * fk.DAY]
    assert all(w.get_status("PUSHED") for w in _valid(s).values())


# ----------------------------------------------------------- other cases

def test_removing_the_table_gives_one_delivery(make):
    """The table removed: one delivery on the wallet's date, one day
    earlier than the will in force (free); the old chain is replaced."""
    s = _sent_owner_will(make)
    old = set(_valid(s))
    s.clock.travel(1)
    assert context.clear_instalments(s.bw) is True
    assert context.clear_instalments(s.bw) is False
    _people(s, "luca", "john")
    s.check(cancel_on=("invalidat",))
    assert not any("nvalidat" in a[1] for a in s.user.asked)
    assert {lv: len(items) for lv, items in _by_level(s).items()} == {1: 3}
    assert {fk.fmt(w.tx.locktime) for w in _valid(s).values()} == {"2030-02-28"}
    assert not old & set(_valid(s))


def test_the_global_option_is_not_read(make):
    """D7: BAL's option on and heirs with their own dates, no table: every
    heir gets the will's date, one delivery."""
    s = make()
    s.plugin.ENABLE_MULTIVERSE.set(True)
    s.bw.heirs["bob"] = [fk.HEIR_ADDR["bob"], "40%", D2]
    s.check()
    assert {lv: len(items) for lv, items in _by_level(s).items()} == {1: 3}
    assert {h[2] for h in s.bw.heirs.values()} == {s.bw.will_settings["locktime"]}


def test_two_wallets_keep_their_own_form(make):
    """The table is the wallet's own: a second wallet in the same Electrum
    builds one delivery, the first keeps its chain."""
    s = _sent_owner_will(make)
    other = s.second_wallet(locktime=D1, fee=2, password=TEST_PASSWORD)
    other.monkeypatch.setattr(other.wallet, "is_up_to_date", lambda: True)
    other.check()
    assert Counter(_level(k, other.bw.willitems) for k in _valid(other)) == {1: 3}
    assert "instalments" not in other.bw.will_settings
    s.activate()
    pushed = len(s.world.pushed)
    s.check()
    assert len(s.world.pushed) == pushed
    assert Counter(_level(k, s.bw.willitems) for k in _valid(s)) == {1: 3, 2: 9, 3: 27}


def test_the_table_survives_a_reload(make):
    """The wallet read again: the table is still there and the will is in
    force (nothing to sign or send)."""
    s = _sent_owner_will(make)
    pushed = len(s.world.pushed)
    s.reload_window()
    assert len(delivery.instalments_of(s.bw.will_settings)) == 3
    s.check()
    assert len(s.world.pushed) == pushed
    assert not s.user.asked


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
