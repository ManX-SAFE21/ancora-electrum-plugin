"""An anticipation nobody needs any more, in a will in several deliveries
(CHANGELOG #186).

Owner's plan (2026-10-24): with one date, a change cancelled at the
password and then undone (e.g. the fee) brings back the will in force,
nothing to sign (#135, ``engine.context.drop_anticipation_leftover``). With
a table of deliveries the Check asked to sign again: the rebuild dates ONE
day earlier only the deliveries it changes (the fee: delivery 1; delivery
3's shares: delivery 3), so the drafts of the other deliveries keep their
dates and the rule, written for one date, never held. Now the settings are
compared with the will in force delivery by delivery (#185): only
deliveries exactly one day earlier with the same shares, and every draft
on the stored date of its delivery, are a leftover. A real change is kept.

``tests/flowkit.py`` wallets: three fake will-executors, each with its own
fee address, synchronized; the owner's table of ``test_gui_deliveries.py``
(10/10, 10/10, 30/30 %).

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_staged_leftover.py
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

import flowkit as fk
import test_gui_deliveries as tgd
from PyQt6.QtWidgets import QApplication

from bal.core import delivery, will_state
from bal.core import will_changes as wc
from bal.engine import context
from bal.engine import sign as engine_sign

_APP = QApplication.instance() or QApplication(sys.argv)

# The deliveries tests' sessions: ADVANCED, three will-executors, the heirs
# luca and john, delivery 1 on D1, synchronized.
make = tgd.make
D1, D2, D3 = tgd.D1, tgd.D2, tgd.D3
SIGN = ("sign your will",)


def in_force(s):
    """The ids of the will in force (the #128 notion)."""
    return set(context.in_force_items(s.bw))


def drafts(s):
    """The ids of the drafts (VALID, never signed nor sent)."""
    return set(will_state.draft_ids(s.bw.willitems))


def table_dates(s):
    """The stored dates of the table, delivery 1 on the wallet's date."""
    return [row["locktime"] for row in delivery.table_rows(s.bw.will_settings)]


def last_step(s):
    """The record of the last Check."""
    return s.recorder.steps[-1]


def nothing_to_sign(s):
    """The last Check asked nothing, sent nothing, broadcast nothing."""
    step = last_step(s)
    assert not [q for q in step["questions"] if q[0] == "password"], step["questions"]
    assert step["pushed"] == [] and step["broadcasts"] == []


def refused(s, caplog):
    """The rule says no by its own decision (judges' review U6): None, and
    not through the error it swallows ("anticipation leftover not
    checked")."""
    caplog.clear()
    assert context.drop_anticipation_leftover(s.bw) is None
    assert "anticipation leftover not checked" not in caplog.text


def kept_in_force(s, first, dates):
    """The will in force kept (judges' review U7): no drafts, the same
    copies in force, the table's dates, the wallet file holding exactly the
    will items; then a second Check asks nothing and keeps it all."""
    assert not drafts(s) and in_force(s) == first
    assert table_dates(s) == dates
    assert set(s.wallet.db.get_dict("will")) == set(s.bw.willitems)
    s.check()
    nothing_to_sign(s)
    assert in_force(s) == first and table_dates(s) == dates


def will_in_deliveries(make):
    """A will in three deliveries signed and sent; returns the session, the
    will in force and the table's dates."""
    s = make()
    tgd.owner_table(s.bw)
    s.check()
    first = in_force(s)
    assert len(first) == 39 and not drafts(s)
    return s, first, table_dates(s)


def set_shares(s, n, shares):
    """Delivery ``n``'s shares through the engine's one validated write."""
    rows = tgd.stored(s.bw)
    rows[n - 1]["shares"] = dict(shares)
    context.set_instalments(s.bw, rows)


# ------------------------------------------------------- the leftovers

def test_fee_changed_cancelled_and_put_back(make):
    """The owner's case: the fee changed (delivery 1 one day earlier, the
    other deliveries on their dates), Cancel at the password, the fee put
    back: the next Check drops the drafts, delivery 1 goes back to its date
    and the will in force stays, nothing to sign; a second Check is clean."""
    s, first, dates = will_in_deliveries(make)
    s.set_fee(3)
    s.check(cancel_on=SIGN)
    assert len(drafts(s)) == 39
    assert s.bw.will_settings["locktime"] == dates[0] - fk.DAY
    assert table_dates(s)[1:] == dates[1:]
    s.set_fee(2)
    s.check(cancel_on=SIGN)
    nothing_to_sign(s)
    kept_in_force(s, first, dates)


@pytest.mark.parametrize("n, changed", [
    (2, {"luca": "5%", "john": "15%"}),
    (3, {"luca": "20%", "john": "40%"}),
])
def test_shares_of_a_later_delivery_changed_and_put_back(make, n, changed):
    """Delivery n's shares changed, Cancel, the shares put back: the drafts
    dropped, the date back, nothing to sign. #187: every change redoes the
    whole chain from delivery 1, one day earlier (before, only delivery
    n's copies, its table date one day earlier)."""
    s, first, dates = will_in_deliveries(make)
    before = tgd.stored(s.bw)[n - 1]["shares"]
    set_shares(s, n, changed)
    s.check(cancel_on=SIGN)
    assert len(drafts(s)) == 39
    assert s.bw.will_settings["locktime"] == dates[0] - fk.DAY
    assert table_dates(s)[1:] == dates[1:]
    set_shares(s, n, before)
    s.check(cancel_on=SIGN)
    nothing_to_sign(s)
    kept_in_force(s, first, dates)


def test_twice_in_a_row(make):
    """The fee changed and put back two times: each time the will in force
    stays (no day lost at each round)."""
    s, first, dates = will_in_deliveries(make)
    for _round in range(2):
        s.set_fee(3)
        s.check(cancel_on=SIGN)
        s.set_fee(2)
        s.check(cancel_on=SIGN)
        nothing_to_sign(s)
        kept_in_force(s, first, dates)


# ---------------------------------------------- what is NOT a leftover

def test_a_change_not_undone_is_kept(make, caplog):
    """The fee stays changed: the drafts are the will to sign (asked again),
    the earlier day kept."""
    s, first, dates = will_in_deliveries(make)
    s.set_fee(3)
    s.check(cancel_on=SIGN)
    kept = drafts(s)
    # The rule itself (a wrong drop would be hidden by the rebuild of the
    # same Check: same inputs, same txids; second review).
    refused(s, caplog)
    assert drafts(s) == kept
    assert s.bw.will_settings["locktime"] == dates[0] - fk.DAY
    s.check(cancel_on=SIGN)
    assert [q for q in last_step(s)["questions"] if q[0] == "password"]
    assert drafts(s) == kept and in_force(s) == first
    assert s.bw.will_settings["locktime"] == dates[0] - fk.DAY


def test_shares_changed_differently_are_kept(make, caplog):
    """Delivery 3's shares changed, then changed again to other values (not
    back): a real change, asked to sign."""
    s, _first, dates = will_in_deliveries(make)
    set_shares(s, 3, {"luca": "20%", "john": "40%"})
    s.check(cancel_on=SIGN)
    pending = drafts(s)
    set_shares(s, 3, {"luca": "25%", "john": "35%"})
    # The rule itself (second review: the rebuild would hide a wrong drop).
    refused(s, caplog)
    assert drafts(s) == pending
    assert s.bw.will_settings["locktime"] == dates[0] - fk.DAY      # #187
    s.check(cancel_on=SIGN)
    assert [q for q in last_step(s)["questions"] if q[0] == "password"]
    assert drafts(s)


def test_a_date_chosen_more_than_one_day_earlier_is_kept(make):
    """The owner moves delivery 3 two days earlier and cancels: not the
    plugin's one-day step, kept and asked to sign again."""
    s, _first, dates = will_in_deliveries(make)
    rows = tgd.stored(s.bw)
    rows[2]["locktime"] = dates[2] - 2 * fk.DAY
    context.set_instalments(s.bw, rows)
    s.check(cancel_on=SIGN)
    s.check(cancel_on=SIGN)
    assert [q for q in last_step(s)["questions"] if q[0] == "password"]
    assert drafts(s)
    assert table_dates(s)[2] == dates[2] - 2 * fk.DAY


def test_a_new_will_in_force_is_never_dropped(make):
    """The fee changed and SIGNED (the new will in force, one day earlier),
    then changed and put back: the signed will stays in force; only the
    drafts of the second round go."""
    s, first, dates = will_in_deliveries(make)
    s.set_fee(3)
    s.check()
    second = in_force(s)
    assert second and second != first and not drafts(s)
    assert s.bw.will_settings["locktime"] == dates[0] - fk.DAY
    s.set_fee(4)
    s.check(cancel_on=SIGN)
    assert drafts(s)
    s.set_fee(3)
    s.check(cancel_on=SIGN)
    nothing_to_sign(s)
    assert s.bw.will_settings["locktime"] == dates[0] - fk.DAY
    kept_in_force(s, second, [dates[0] - fk.DAY] + dates[1:])


def test_the_table_removed_is_not_a_leftover(make, caplog):
    """Back to one delivery with drafts of the table pending: never read as
    a leftover (the number of deliveries changed)."""
    s, _first, _dates = will_in_deliveries(make)
    s.set_fee(3)
    s.check(cancel_on=SIGN)
    pending = drafts(s)
    context.clear_instalments(s.bw)
    refused(s, caplog)
    assert drafts(s) == pending


# ------------------------------- found by the independent review (#186)

def box_rows(dialog):
    """The "What changes" rows' labels of a Check dialog."""
    return [row[0] for row in dialog._report().snapshot().get("changes") or []]


def test_after_a_later_delivery_was_signed(make):
    """F1: delivery 3's shares changed and SIGNED (#187: the whole chain
    redone one day earlier; the old copies stay in force, pre-empted). The
    in-force side must read the copies that would really be published (the
    new ones): then a fee changed, cancelled and put back is a leftover
    again, and the box of the fee change does not show delivery 3 as
    changed."""
    s, _first, dates = will_in_deliveries(make)
    set_shares(s, 3, {"luca": "20%", "john": "40%"})
    s.check()
    second = in_force(s)
    assert not drafts(s)
    s.set_fee(3)
    dialog = s.check(cancel_on=SIGN)
    assert "Delivery 3" not in box_rows(dialog)
    assert "Miner fee" in box_rows(dialog)
    s.set_fee(2)
    s.check(cancel_on=SIGN)
    nothing_to_sign(s)
    kept_in_force(s, second, [dates[0] - fk.DAY] + dates[1:])


def test_back_to_one_date_then_a_leftover(make):
    """F1a: the table removed and the will with one date SIGNED; the old
    copies of deliveries 2-3 are children of a replaced delivery 1, never
    published. A fee changed, cancelled and put back: the rule of one date
    (#135) brings the will in force back, as before #186."""
    s, _first, _dates = will_in_deliveries(make)
    context.clear_instalments(s.bw)
    s.check()
    second = in_force(s)
    assert not drafts(s)
    s.set_fee(3)
    s.check(cancel_on=SIGN)
    assert drafts(s)
    s.set_fee(2)
    s.check(cancel_on=SIGN)
    nothing_to_sign(s)
    kept_in_force(s, second, [])


def test_an_heir_with_nothing_at_delivery_1(make):
    """F3: john has 0 % at delivery 1 (40 % at delivery 2). He is an heir of
    the will in force all the same: the fee changed shows no "Heirs" row,
    and put back it is a leftover."""
    s = make()
    tgd.owner_table(s.bw, [{"locktime": D1, "shares": {"luca": "60%", "john": "0%"}},
                           {"locktime": D2, "shares": {"luca": "0%", "john": "40%"}}])
    s.check()
    first = in_force(s)
    s.set_fee(3)
    dialog = s.check(cancel_on=SIGN)
    assert "Miner fee" in box_rows(dialog)          # the box is there
    assert "Heirs" not in box_rows(dialog)
    s.set_fee(2)
    s.check(cancel_on=SIGN)
    nothing_to_sign(s)
    assert s.bw.will_settings["locktime"] == D1
    kept_in_force(s, first, [D1, D2])


def test_a_delivery_without_drafts_is_not_moved_back(make):
    """F2: delivery 3's shares changed (#187: the whole chain, delivery 1
    one day earlier), Cancel, put back; meanwhile the owner moves delivery 2
    exactly one day earlier on purpose. The drafts of delivery 2 are not on
    that day: it is the owner's date, kept, and the will is asked to sign."""
    s, _first, dates = will_in_deliveries(make)
    before = tgd.stored(s.bw)[2]["shares"]
    set_shares(s, 3, {"luca": "20%", "john": "40%"})
    s.check(cancel_on=SIGN)
    rows = tgd.stored(s.bw)
    rows[2]["shares"] = before
    rows[1]["locktime"] = dates[1] - fk.DAY
    context.set_instalments(s.bw, rows)
    s.check(cancel_on=SIGN)
    assert [q for q in last_step(s)["questions"] if q[0] == "password"]
    assert table_dates(s)[1] == dates[1] - fk.DAY
    # Review R-8 is gone with #187: a later delivery never takes a day in a
    # cancelled round (only delivery 1 does).
    assert table_dates(s)[2] == dates[2]


def test_shares_differing_by_hundredths_are_a_change(make, caplog):
    """F5: the table allows two decimals: 30.04 / 29.96 % is not the 30 / 30
    % in force. Never read as a leftover (nothing dropped, no date moved
    back)."""
    s, _first, _dates = will_in_deliveries(make)
    set_shares(s, 3, {"luca": "20%", "john": "40%"})
    s.check(cancel_on=SIGN)
    set_shares(s, 3, {"luca": "30.04%", "john": "29.96%"})
    refused(s, caplog)
    assert drafts(s)


def test_a_failed_date_write_touches_nothing(make, monkeypatch):
    """F4: the dates are written first, the drafts deleted last: when the
    write of delivery 1's date fails, the drafts and the stored dates stay
    as they were."""
    s, _first, dates = will_in_deliveries(make)
    s.set_fee(3)
    s.check(cancel_on=SIGN)
    s.set_fee(2)
    pending = drafts(s)
    before = (s.bw.will_settings["locktime"], table_dates(s))

    def refuse(field, value):
        """The write is refused (e.g. the settings are display-only)."""
        raise ValueError("refused")

    monkeypatch.setattr(s.bw, "store_will_setting", refuse)
    assert context.drop_anticipation_leftover(s.bw) is None
    assert drafts(s) == pending
    assert (s.bw.will_settings["locktime"], table_dates(s)) == before


def test_one_date_in_force_table_added_then_removed(make, caplog):
    """Review R-9 (pinned): a will with one date in force, a table added,
    Check, Cancel, the table removed: not recognized as a leftover (the
    rule does nothing; the next Check asks once, then it heals itself)."""
    s = make()
    s.check()
    tgd.owner_table(s.bw)
    s.check(cancel_on=SIGN)
    pending = drafts(s)
    assert pending
    context.clear_instalments(s.bw)
    refused(s, caplog)
    assert drafts(s) == pending


def test_a_date_write_refused_without_an_error(make, monkeypatch, caplog):
    """The window's write of a date logs a refusal and returns (it never
    raises, second review): the date is read back; not written, nothing is
    dropped and the table stays."""
    s, _first, _dates = will_in_deliveries(make)
    s.set_fee(3)
    s.check(cancel_on=SIGN)
    s.set_fee(2)
    pending = drafts(s)
    before = (s.bw.will_settings["locktime"], table_dates(s))
    monkeypatch.setattr(s.bw, "store_will_setting", lambda field, value: None)
    refused(s, caplog)
    assert drafts(s) == pending
    assert (s.bw.will_settings["locktime"], table_dates(s)) == before


# ------------------------------------------------- the judges' review

def test_a_draft_off_its_delivery_date_is_kept(make, caplog):
    """U4: the fee changed and cancelled (drafts of delivery 3 on D3), then
    the owner moves delivery 3 one day earlier and puts the fee back. The
    drafts of delivery 3 are not on its stored date: the owner's D3-1 is
    never put back, nothing is dropped."""
    s, _first, dates = will_in_deliveries(make)
    s.set_fee(3)
    s.check(cancel_on=SIGN)
    pending = drafts(s)
    s.set_fee(2)
    rows = tgd.stored(s.bw)
    rows[2]["locktime"] = dates[2] - fk.DAY
    context.set_instalments(s.bw, rows)
    refused(s, caplog)
    assert drafts(s) == pending
    assert table_dates(s)[2] == dates[2] - fk.DAY
    assert s.bw.will_settings["locktime"] == dates[0] - fk.DAY


def _two_rounds(make):
    """U5: delivery 3's shares changed (Cancel), then the fee (Cancel), both
    put back. #187: each round redoes the whole chain, delivery 1 one day
    earlier (the same day both times: the drafts are dropped first and the
    will in force is the reference)."""
    s, first, dates = will_in_deliveries(make)
    before = tgd.stored(s.bw)[2]["shares"]
    set_shares(s, 3, {"luca": "20%", "john": "40%"})
    s.check(cancel_on=SIGN)
    s.set_fee(3)
    s.check(cancel_on=SIGN)
    set_shares(s, 3, before)
    s.set_fee(2)
    return s, first, dates


def test_two_deliveries_moved_back_together(make):
    """U5 (a): both put back; nothing to sign."""
    s, first, dates = _two_rounds(make)
    assert table_dates(s)[0] == dates[0] - fk.DAY
    assert table_dates(s)[1:] == dates[1:]
    s.check(cancel_on=SIGN)
    nothing_to_sign(s)
    kept_in_force(s, first, dates)


def test_two_deliveries_and_a_refused_write(make, monkeypatch, caplog):
    """U5 (b): the write of delivery 1's date refused without an error:
    nothing dropped, the dates untouched."""
    s, _first, dates = _two_rounds(make)
    pending = drafts(s)
    monkeypatch.setattr(s.bw, "store_will_setting", lambda field, value: None)
    refused(s, caplog)
    assert drafts(s) == pending
    assert table_dates(s) == [dates[0] - fk.DAY] + dates[1:]


def _sign(s, ids):
    """Sign the copies ``ids`` (the engine's signer, as the Will tab)."""
    result = engine_sign.sign_will(s.wallet, s.bw.willitems, tgd.TEST_PASSWORD,
                                   sorted(ids))
    assert all(s.bw.willitems[w].get_status("COMPLETE") for w in ids), result


def test_drafts_of_delivery_2_signed_after_the_cancel(make, caplog):
    """U3 (3): the fee changed, Cancel, then only the drafts of delivery 2
    signed from the Will tab, the fee put back: a signed copy spends a
    draft's output, so the rule touches nothing (signed copies and drafts
    stay as they are)."""
    s, _first, dates = will_in_deliveries(make)
    s.set_fee(3)
    s.check(cancel_on=SIGN)
    second = {w for w in drafts(s) if wc.delivery_number(s.bw.willitems[w]) == 2}
    assert len(second) == 9
    _sign(s, second)
    pending = drafts(s)
    s.set_fee(2)
    refused(s, caplog)
    assert drafts(s) == pending
    assert all(s.bw.willitems[w].get_status("COMPLETE") for w in second)
    assert s.bw.will_settings["locktime"] == dates[0] - fk.DAY


def test_drafts_of_delivery_1_signed_after_the_cancel(make, caplog):
    """U3 (1): only the drafts of delivery 1 signed (they are no drafts any
    more): the rule touches nothing."""
    s, _first, _dates = will_in_deliveries(make)
    s.set_fee(3)
    s.check(cancel_on=SIGN)
    first_level = {w for w in drafts(s) if wc.delivery_number(s.bw.willitems[w]) == 1}
    _sign(s, first_level)
    pending = drafts(s)
    s.set_fee(2)
    refused(s, caplog)
    assert drafts(s) == pending
    assert all(w in s.bw.willitems for w in first_level)
