"""The staged deliveries (multiverse): the pure rules of the table (#167).

Owner decisions of 2026-10-19: the same dates for every heir, at most 3
dates and at least 2 (one date is a normal will), at least 28 days between
two dates, each cell a share of the WHOLE estate with at most two decimals,
the whole table 100 %, 0 % allowed, every date pays at least one heir,
every heir gets something at one date at least, a missing heir counts 0 %,
a name that is not an heir is refused. The editor starts from an example:
33 / 33 / 34 % of the estate, one year apart, split equally among the heirs.

The rules live in ``bal.core.delivery`` (pure, no clock). Nothing uses them
yet: the engine and the schema 3 come with M3, so the schema stays 2 here.
No will-executor is involved: these are rules on a table of percentages.

Run (standalone or with pytest):
    PYTHONPATH=electrum-src python3 -m pytest tests/test_core_delivery_instalments.py
"""

import os
import sys
from datetime import datetime, timezone
from decimal import Decimal

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from bal.core import delivery as dv

DAY = 86400
HEIRS = ("Luca", "John", "Maria")


def ts(y, m, d, hh=0, mm=0):
    """UTC timestamp of a calendar day (and time)."""
    return int(datetime(y, m, d, hh, mm, tzinfo=timezone.utc).timestamp())


NOW = ts(2026, 10, 19, 9, 30)
D1, D2, D3 = ts(2030, 1, 1), ts(2033, 1, 1), ts(2036, 1, 1)


def table(*rows):
    """A table from ``(date, {name: share})`` pairs."""
    return [{"locktime": lt, "shares": dict(shares)} for lt, shares in rows]


def owner_table():
    """The owner's example: 3 heirs, 33 / 33 / 34 % of the estate."""
    return table(
        (D1, {"Luca": "11%", "John": "11%", "Maria": "11%"}),
        (D2, {"Luca": "11%", "John": "11%", "Maria": "11%"}),
        (D3, {"Luca": "11.33%", "John": "11.33%", "Maria": "11.34%"}),
    )


def two_dates(gap_days=365, first=None, second=None):
    """A valid two-date table, 50 % + 50 %, ``gap_days`` apart."""
    lt = D1 + gap_days * DAY
    return table(
        (D1, first if first is not None else {"Luca": "25%", "John": "25%", "Maria": "0%"}),
        (lt, second if second is not None else {"Luca": "25%", "John": "0%", "Maria": "25%"}),
    )


# ------------------------------------------------------------------ constants

def test_constants_of_the_owner_decisions():
    """2 to 3 dates, 28 days apart at least; the schema stays 2 until M3."""
    assert dv.INSTALMENTS_KEY == "instalments"
    assert (dv.INSTALMENTS_MIN, dv.INSTALMENTS_MAX) == (2, 3)
    assert dv.MIN_GAP_DAYS == 28
    assert dv.DEFAULT_INSTALMENT_TOTALS == ("33", "33", "34")
    assert dv.DEFAULT_INSTALMENT_STEP_MONTHS == 12
    assert dv.SCHEMA_VERSION == 2


# -------------------------------------------------------------- the reader

@pytest.mark.parametrize("settings", [
    {}, {"instalments": None}, {"instalments": []}, None, "x", [],
])
def test_instalments_of_without_a_table_is_one_delivery(settings):
    """No table: the wallet has one delivery, as before #167."""
    assert dv.instalments_of(settings) == []


def test_instalments_of_returns_a_copy():
    """Changing what it returns never touches the wallet dict."""
    stored = owner_table()
    settings = {"instalments": stored}
    rows = dv.instalments_of(settings)
    assert rows == owner_table()
    rows[0]["locktime"] = 1
    rows[0]["shares"]["Luca"] = "99%"
    rows.append({})
    assert settings["instalments"] == owner_table()


@pytest.mark.parametrize("raw", ["1054d", 5, {"locktime": D1}, True])
def test_instalments_of_refuses_a_value_that_is_not_a_list(raw):
    """Read as "no table", it would pay everything at the first date."""
    with pytest.raises(ValueError, match="not a list"):
        dv.instalments_of({"instalments": raw})


def test_instalments_of_leaves_bad_rows_to_the_validator():
    """Rows are copied as they are; validate_instalments refuses them."""
    rows = dv.instalments_of({"instalments": [7, {"locktime": D1, "shares": 3}]})
    assert rows == [7, {"locktime": D1, "shares": 3}]


# ------------------------------------------------------------------ a share

@pytest.mark.parametrize("raw, expected", [
    ("11.33%", "11.33"), ("11.33", "11.33"), (" 11.5 % ", "11.5"),
    (11, "11"), (11.5, "11.5"), (0.1, "0.1"), ("0%", "0"), (0, "0"),
    ("-0", "0"), ("100%", "100"), ("11.50%", "11.5"), ("1e1", "10"),
])
def test_as_share_accepts_two_decimals_from_0_to_100(raw, expected):
    """A text with or without %, or a number; two decimals at most."""
    assert dv.as_share(raw) == Decimal(expected)


@pytest.mark.parametrize("raw", [
    "11.333%", 11.333, "-1%", -0.01, "100.01%", 101, True, False, None,
    "abc", "", "%", float("nan"), float("inf"), "NaN", [], {"a": 1},
])
def test_as_share_refuses_the_rest(raw):
    """More decimals, outside 0..100, bools, garbage: None."""
    assert dv.as_share(raw) is None


@pytest.mark.parametrize("value, text", [
    ("11.33", "11.33%"), ("11.5", "11.5%"), ("11.50", "11.5%"),
    ("11", "11%"), ("11.00", "11%"), ("0", "0%"), ("100", "100%"),
    ("10", "10%"), ("0.05", "0.05%"),
])
def test_share_text_has_one_text_per_share(value, text):
    """The heirs' form "<n>%", no trailing zeros."""
    assert dv.share_text(Decimal(value)) == text


def test_minus_zero_is_stored_as_zero():
    """"-0" equals 0 as a number, but its text must be "0%", never "-0%"."""
    assert dv.share_text(dv.as_share("-0")) == "0%"
    rows = owner_table()
    rows[0]["shares"]["Luca"] = "-0%"
    rows[0]["shares"]["Maria"] = "22%"
    assert dv.validate_instalments(rows, HEIRS, NOW)[0]["shares"]["Luca"] == "0%"


# --------------------------------------------------------- the validator: OK

def test_the_owner_example_is_valid_and_kept():
    """3 heirs, 3 dates, 33 / 33 / 34 %: the table comes back as it is."""
    rows = owner_table()
    out = dv.validate_instalments(rows, HEIRS, NOW)
    assert out == owner_table()
    assert out is not rows and out[0] is not rows[0]


def test_numbers_are_read_exactly_and_stored_as_texts():
    """A share given as a number is read through its text (Decimal, no
    binary error) and stored as "<n>%", like the heirs' amounts."""
    rows = table(
        (D1, {"Luca": 33.33, "John": 33.33, "Maria": 0}),
        (D2, {"Luca": 0, "John": 0.0, "Maria": 33.34}),
    )
    out = dv.validate_instalments(rows, HEIRS, NOW)
    assert out[0]["shares"] == {"Luca": "33.33%", "John": "33.33%", "Maria": "0%"}
    assert out[1]["shares"] == {"Luca": "0%", "John": "0%", "Maria": "33.34%"}


def test_a_missing_heir_counts_zero_and_is_written():
    """Decision 3: a missing heir is 0 %; the row then names every heir,
    in the order of the heirs."""
    rows = table(
        (D1, {"John": "50%", "Luca": "10%"}),
        (D2, {"Maria": "40%"}),
    )
    out = dv.validate_instalments(rows, HEIRS, NOW)
    assert out == table(
        (D1, {"Luca": "10%", "John": "50%", "Maria": "0%"}),
        (D2, {"Luca": "0%", "John": "0%", "Maria": "40%"}),
    )
    assert list(out[0]["shares"]) == list(HEIRS)


def test_the_heirs_dict_itself_gives_the_names():
    """The caller may pass the wallet's ``heirs`` dict: its keys count."""
    heirs = {name: ["bc1q...", "", D1] for name in HEIRS}
    assert dv.validate_instalments(owner_table(), heirs, NOW) == owner_table()


def test_dates_are_stored_as_ints():
    """A date given as a text or a float is stored as an int."""
    rows = two_dates()
    rows[0]["locktime"] = str(D1)
    rows[1]["locktime"] = float(rows[1]["locktime"]) + 0.5
    out = dv.validate_instalments(rows, HEIRS, NOW)
    assert [type(r["locktime"]) for r in out] == [int, int]
    assert out[0]["locktime"] == D1


def test_exactly_28_days_is_enough():
    """The gap is "at least" 28 days: monthly deliveries across February."""
    out = dv.validate_instalments(two_dates(gap_days=28), HEIRS, NOW)
    assert out[1]["locktime"] - out[0]["locktime"] == 28 * DAY
    feb = table(
        (ts(2031, 2, 1), {"Luca": "50%", "John": "0%", "Maria": "0%"}),
        (ts(2031, 3, 1), {"Luca": "0%", "John": "25%", "Maria": "25%"}),
    )
    assert dv.validate_instalments(feb, HEIRS, NOW)


def test_one_heir_and_two_dates():
    """The smallest table: one heir, two dates."""
    rows = table((D1, {"Luca": "40%"}), (D2, {"Luca": "60%"}))
    assert dv.validate_instalments(rows, ["Luca"], NOW) == rows


# ------------------------------------------------------ the validator: refused

def _refused(rows, match, heirs=HEIRS, now=NOW):
    """validate_instalments raises ValueError with ``match`` in the text."""
    with pytest.raises(ValueError, match=match):
        dv.validate_instalments(rows, heirs, now)


def test_one_date_is_a_normal_will():
    """Decision 4: a table with one date is refused."""
    _refused(table((D1, {"Luca": "100%"})), "1 delivery dates")
    _refused([], "0 delivery dates")


def test_at_most_three_dates():
    """Decision D3: at most 3 dates."""
    four = owner_table() + table((ts(2039, 1, 1), {"Luca": "0%"}))
    _refused(four, "4 delivery dates")


@pytest.mark.parametrize("rows", ["x", None, {"locktime": D1}, 5])
def test_the_table_must_be_a_list(rows):
    """Anything else than a list of rows."""
    _refused(rows, "not a list")


@pytest.mark.parametrize("row", [
    {"locktime": D2},
    {"shares": {"Luca": "50%"}},
    {"locktime": D2, "shares": {}, "note": "x"},
    ["x"],
    None,
])
def test_each_row_has_exactly_a_date_and_shares(row):
    """No missing key and no extra key."""
    rows = two_dates()
    rows[1] = row
    _refused(rows, "delivery 2: not a row")


@pytest.mark.parametrize("raw, match", [
    ("1054d", "a period is not a delivery date"),
    ("36m", "a period is not a delivery date"),
    (800_000, "not a delivery date"),
    ("garbage", "not a delivery date"),
    (True, "not a delivery date"),
    (None, "not a delivery date"),
])
def test_each_date_is_a_date(raw, match):
    """A period, a block height or garbage is not a delivery date."""
    rows = two_dates()
    rows[0]["locktime"] = raw
    _refused(rows, "delivery 1: " + match)


@pytest.mark.parametrize("first", [NOW, NOW - DAY, ts(2026, 10, 19)])
def test_dates_are_in_the_future(first):
    """A date equal to or before ``now`` is refused."""
    rows = two_dates()
    rows[0]["locktime"] = first
    _refused(rows, "delivery 1: the date is not in the future")


def test_dates_are_strictly_increasing():
    """The same date twice, or a date before the one above, is refused."""
    rows = owner_table()
    rows[1]["locktime"] = D1
    _refused(rows, "delivery 2: the date is not after delivery 1")
    rows = owner_table()
    rows[2]["locktime"] = D1 + 365 * DAY
    _refused(rows, "delivery 3: the date is not after delivery 2")


@pytest.mark.parametrize("gap", [27 * DAY, 28 * DAY - 1, DAY])
def test_less_than_28_days_is_refused(gap):
    """The will-executors never retry: 28 days = twice the mempool expiry."""
    rows = two_dates()
    rows[1]["locktime"] = D1 + gap
    _refused(rows, "delivery 2: less than 28 days after delivery 1")


def test_a_name_that_is_not_an_heir():
    """Decision 3: a name that is not an heir is an error."""
    rows = owner_table()
    rows[1]["shares"]["Karen"] = "0%"
    _refused(rows, "delivery 2: not an heir: 'Karen'")


@pytest.mark.parametrize("bad", ["11.333%", "-5%", "200%", True, None, "x"])
def test_each_share_is_a_share(bad):
    """Two decimals at most, from 0 to 100."""
    rows = owner_table()
    rows[2]["shares"]["John"] = bad
    _refused(rows, "delivery 3: John: not a share")


def test_every_date_pays_one_heir_at_least():
    """A date where every heir has 0 % is refused."""
    rows = two_dates(second={"Luca": "0%", "John": "0%", "Maria": "0"})
    rows[0]["shares"] = {"Luca": "40%", "John": "30%", "Maria": "30%"}
    _refused(rows, "delivery 2: pays no heir")


def test_an_heir_with_nothing_must_be_removed():
    """Decision 2: an heir with 0 % at every date is refused."""
    rows = table(
        (D1, {"Luca": "50%", "John": "0%", "Maria": "0%"}),
        (D2, {"Luca": "25%", "John": "25%"}),
    )
    _refused(rows, "Maria: gets nothing at any delivery")


@pytest.mark.parametrize("last, total", [("11.33%", "99.99%"), ("11.35%", "100.01%"),
                                          ("50%", "138.66%")])
def test_the_table_adds_up_to_100(last, total):
    """The whole estate, exactly: not 99.99 %, not 100.01 %."""
    rows = owner_table()
    rows[2]["shares"]["Maria"] = last
    _refused(rows, "the shares add up to {}, not 100%".format(total))


def test_the_heirs_names():
    """No heirs, a name twice or a name that is not a text."""
    _refused(owner_table(), "there are no heirs", heirs=())
    _refused(owner_table(), "named twice", heirs=["Luca", "John", "Luca"])
    _refused(owner_table(), "not an heir name", heirs=["Luca", 7])


def test_the_rows_given_are_not_changed():
    """The validator works on a copy: the caller's rows stay as they were."""
    rows = table((D1, {"Luca": "100.00%"}), (D2, {"John": "0"}))
    before = [dict(r, shares=dict(r["shares"])) for r in rows]
    with pytest.raises(ValueError):
        dv.validate_instalments(rows, HEIRS, NOW)
    rows[1]["shares"]["Maria"] = 0
    with pytest.raises(ValueError):
        dv.validate_instalments(rows, ["Luca", "Maria"], NOW)
    assert rows[0] == before[0]


# ---------------------------------------------------------- the example table

def test_the_default_table_for_three_heirs():
    """The owner's example, recomputed: 11 / 11 / 11, 11 / 11 / 11,
    11.33 / 11.33 / 11.34; each heir about a third."""
    rows = dv.default_instalments(HEIRS, D1)
    assert rows == table(
        (D1, {"Luca": "11%", "John": "11%", "Maria": "11%"}),
        (ts(2031, 1, 1), {"Luca": "11%", "John": "11%", "Maria": "11%"}),
        (ts(2032, 1, 1), {"Luca": "11.33%", "John": "11.33%", "Maria": "11.34%"}),
    )


@pytest.mark.parametrize("names, rows_text", [
    (["Luca"], [["33%"], ["33%"], ["34%"]]),
    (["Luca", "John"], [["16.5%", "16.5%"], ["16.5%", "16.5%"], ["17%", "17%"]]),
    (["A", "B", "C", "D"], [["8.25%"] * 4, ["8.25%"] * 4, ["8.5%"] * 4]),
    (list("ABCDEFG"), [["4.71%"] * 6 + ["4.74%"], ["4.71%"] * 6 + ["4.74%"],
                       ["4.85%"] * 6 + ["4.9%"]]),
])
def test_the_default_table_for_other_numbers_of_heirs(names, rows_text):
    """Equal parts in cents; the cents left over go to the last heir."""
    rows = dv.default_instalments(names, D1)
    assert [list(r["shares"].values()) for r in rows] == rows_text
    assert [list(r["shares"]) for r in rows] == [names] * 3


@pytest.mark.parametrize("n", range(1, 13))
def test_the_default_table_is_always_valid(n):
    """Every row pays its share of 33 / 33 / 34 %; the table validates."""
    names = ["heir{}".format(i) for i in range(n)]
    rows = dv.default_instalments(names, D1)
    assert dv.validate_instalments(rows, names, NOW) == rows
    for row, total in zip(rows, dv.DEFAULT_INSTALMENT_TOTALS, strict=True):
        cells = sum(dv.as_share(v) for v in row["shares"].values())
        assert cells == Decimal(total)


def test_the_default_dates_one_year_apart():
    """The first date is the wallet's, kept as it is (even not at
    midnight); the others are 12 and 24 calendar months later."""
    first = ts(2028, 2, 29, 15, 45)
    rows = dv.default_instalments(HEIRS, first)
    assert [r["locktime"] for r in rows] == [
        first, ts(2029, 2, 28), ts(2030, 2, 28)]


def test_the_default_table_needs_heirs():
    """No heirs: no table."""
    with pytest.raises(ValueError, match="no heirs"):
        dv.default_instalments([], D1)


# ------------------------------------------- the rows of each delivery (#168)

MESSAGE = ["OP_RETURN:" + b"Ciao".hex(), "0", D1]


def person_rows(*names):
    """The stored heirs: one row per person (amount and date not used)."""
    return {name: ["addr-" + name, "50%", D1] for name in names}


def test_row_names_of_each_delivery():
    """Owner decision 2: "Luca", "Luca§2", "Luca§3"; the number is read
    back, the person is shown."""
    assert dv.INSTALMENT_SEP == "§" and dv.INSTALMENTS_SCHEMA_VERSION == 3
    assert [dv.instalment_key("Luca", n) for n in (1, 2, 3)] == [
        "Luca", "Luca§2", "Luca§3"]
    for key, number, person in [
        ("Luca", 1, "Luca"), ("Luca§2", 2, "Luca"), ("Luca§3", 3, "Luca"),
        ("a§b§2", 2, "a§b"), ("Luca§", 1, "Luca§"), ("§2", 1, "§2"),
        ("Luca§x", 1, "Luca§x"), ("Luca§1", 1, "Luca§1"), ("Luca§0", 1, "Luca§0"),
    ]:
        assert dv.instalment_number(key) == number, key
        assert dv.person_name(key) == person, key
    assert dv.has_separator("Lu§ca") and not dv.has_separator("Luca")


def test_message_rows_are_not_payees():
    """An OP_RETURN row is a message, not a column of the table."""
    heirs = dict(person_rows("luca", "john"), note=MESSAGE)
    assert dv.is_message_row(MESSAGE) and not dv.is_message_row(heirs["luca"])
    assert not dv.is_message_row([]) and not dv.is_message_row(None)
    assert dv.payee_names(heirs) == ["luca", "john"]


def test_effective_rows_of_a_table():
    """One row per person and delivery with a share; 0 % gives no row; the
    message goes with delivery 1; the dates are the table's."""
    heirs = dict(person_rows("luca", "john"), note=MESSAGE)
    rows = dv.validate_instalments(table(
        (D1, {"luca": "10%", "john": "10%"}),
        (D2, {"luca": "10%", "john": "0%"}),
        (D3, {"luca": "35%", "john": "35%"}),
    ), dv.payee_names(heirs), NOW)
    assert dv.effective_rows(heirs, rows) == {
        "note": [MESSAGE[0], "0", D1],
        "luca": ["addr-luca", "10%", D1], "john": ["addr-john", "10%", D1],
        "luca§2": ["addr-luca", "10%", D2],
        "luca§3": ["addr-luca", "35%", D3], "john§3": ["addr-john", "35%", D3],
    }


def test_effective_rows_refuse_a_name_with_the_separator():
    """A stored name with "§" could collide with a row name."""
    heirs = person_rows("luca", "luca§2")
    rows = table((D1, {"luca": "50%"}), (D2, {"luca": "50%"}))
    with pytest.raises(ValueError, match="holds"):
        dv.effective_rows(heirs, rows)


def test_table_rows_put_delivery_1_on_the_wallets_date():
    """``locktime`` is the one date of delivery 1 (anticipation, anchor,
    a cancelled postpone keep moving it); the stored row may be older."""
    stored = owner_table()
    settings = {"locktime": D1 - dv.DAY, "instalments": stored}
    rows = dv.table_rows(settings)
    assert rows[0]["locktime"] == D1 - dv.DAY
    assert rows[1:] == owner_table()[1:]
    assert stored[0]["locktime"] == D1                  # not changed in place
    assert dv.table_rows({"locktime": D1}) == []


def test_a_stored_table_is_checked_without_now_and_gap():
    """``now`` None: a past delivery 1 is fine; ``gap`` False: an automatic
    anticipation may have brought two dates under 28 days apart. The order
    and the shares are still checked."""
    rows = two_dates(gap_days=27)
    rows[0]["locktime"] = ts(2020, 1, 1)
    rows[1]["locktime"] = ts(2020, 1, 28)
    assert dv.validate_instalments(rows, HEIRS, None, gap=False)
    with pytest.raises(ValueError, match="less than 28 days"):
        dv.validate_instalments(rows, HEIRS, None)
    with pytest.raises(ValueError, match="not in the future"):
        dv.validate_instalments(rows, HEIRS, NOW, gap=False)
    rows[1]["locktime"] = rows[0]["locktime"]
    with pytest.raises(ValueError, match="not after"):
        dv.validate_instalments(rows, HEIRS, None, gap=False)


def test_anticipated_rows_move_later_deliveries_earlier_only():
    """F1: deliveries 2 and 3 follow an EARLIER date of the will; delivery
    1 (it follows ``locktime``) and a later date (a postpone) never."""
    rows = owner_table()
    new = dv.anticipated_rows(rows, {1: D1 - dv.DAY, 2: D2 - dv.DAY, 3: D3 + dv.DAY})
    assert [r["locktime"] for r in new] == [D1, D2 - dv.DAY, D3]
    assert rows == owner_table()                        # a new list
    assert dv.anticipated_rows(rows, {2: D2, 3: D3}) is None
    assert dv.anticipated_rows(rows, {}) is None
    assert dv.anticipated_rows(rows, {1: D1 - dv.DAY}) is None


# ------------------------------------- shared with the window since #170 (M4a)

def test_rule_errors_keep_their_english_and_can_be_translated():
    """``str(e)`` is the English of before #170 (the command line and the
    log read it); the template and its values give the window's
    translation; a broken translation falls back to the English."""
    rows = two_dates(gap_days=27)
    with pytest.raises(dv.TableRuleError) as info:
        dv.validate_instalments(rows, HEIRS, NOW)
    e = info.value
    assert isinstance(e, ValueError)
    assert str(e) == "delivery 2: less than 28 days after delivery 1"
    assert e.template == "delivery {}: less than {} days after delivery {}"
    assert e.values == (2, 28, 1)
    italian = {e.template: "consegna {}: meno di {} giorni dopo la consegna {}"}
    assert dv.rule_text(e, lambda t: italian.get(t, t)) == (
        "consegna 2: meno di 28 giorni dopo la consegna 1")
    assert dv.rule_text(e, lambda t: "{} {} {} {}") == str(e)    # a field too many
    assert dv.rule_text(ValueError("plain"), lambda t: "x") == "plain"
    assert dv.N_("text") == "text"


@pytest.mark.parametrize("rows, match", [
    (two_dates(first={"Luca": "50%", "John": "0%", "Maria": "0%"},
               second={"Luca": "0%", "John": "0%", "Maria": "40%"}),
     "John: gets nothing"),
    (two_dates(first={"Luca": "60%"}, second={"John": "30%", "Maria": "30%"}),
     "add up to 120%"),
    ([], "0 delivery dates"),
])
def test_every_rule_raises_a_table_rule_error(rows, match):
    """Each rule of the table raises the translatable error."""
    with pytest.raises(dv.TableRuleError, match=match):
        dv.validate_instalments(rows, HEIRS, NOW)


def test_schedules_of_a_table_and_of_now():
    """``schedule_of``: each date and its share of the estate;
    ``schedule_now``: the stored table on its reference dates, or 100 % on
    delivery 1 without a table."""
    rows = owner_table()
    assert dv.schedule_of(rows) == [(D1, Decimal(33)), (D2, Decimal(33)),
                                    (D3, Decimal(34))]
    reference = {1: (D1, True), 2: (D2 - DAY, True), 3: (None, False)}
    assert dv.schedule_now(reference, rows) == [(D1, Decimal(33)), (D2 - DAY, Decimal(33))]
    assert dv.schedule_now({1: (D1, False)}, []) == [(D1, Decimal(100))]
    assert dv.schedule_now({1: (None, False)}, []) == []


def test_what_arrives_earlier():
    """Owner decision (a): the first date by which more of the estate
    would be paid; a date earlier than its reference; nothing for a
    later or equal table."""
    now = [(D1, Decimal(33)), (D2, Decimal(33)), (D3, Decimal(34))]
    assert dv.first_earlier(now, now) is None
    later = [(D1, Decimal(33)), (D2 + DAY, Decimal(33)), (D3, Decimal(34))]
    assert dv.first_earlier(later, now) is None
    moved = [(D1, Decimal(66)), (D3, Decimal(34))]
    assert dv.first_earlier(moved, now) == (1, D1, Decimal(66), Decimal(33))
    assert dv.first_earlier([(D1, Decimal(100))], now) == (
        1, D1, Decimal(100), Decimal(33))
    reference = {1: (D1, False), 2: (D2, True), 3: (D3, False)}
    rows = owner_table()
    assert dv.first_earlier_date(rows, reference) is None
    rows[1]["locktime"] = D2 - DAY
    assert dv.first_earlier_date(rows, reference) == (2, D2 - DAY, D2, True)
    assert dv.first_earlier_date(rows, {1: (None, False)}) is None


def test_dates_later_than_the_will_in_force():
    """The deliveries after the will in force's date of the same delivery."""
    rows = owner_table()
    assert dv.later_than_in_force(rows, {1: D1, 2: D2, 3: D3}) == []
    assert dv.later_than_in_force(rows, {1: D1 - DAY, 2: None, 3: D3 - DAY}) == [
        (1, D1 - DAY), (3, D3 - DAY)]


def test_delivery_1_keeps_its_28_days_before_delivery_2():
    """Delivery 2's date when delivery 1 typed by the owner comes less than
    28 days before it; None without a table or with one unreadable."""
    settings = {"locktime": D1, "instalments": owner_table()}
    assert dv.too_close_to_second(settings, D1) is None
    assert dv.too_close_to_second(settings, D2 - 28 * DAY) is None
    assert dv.too_close_to_second(settings, D2 - 27 * DAY) == D2
    assert dv.too_close_to_second({"locktime": D1}, D2) is None
    assert dv.too_close_to_second({"locktime": D1, "instalments": "x"}, D2) is None


@pytest.mark.parametrize("branches, deliveries, total", [
    (3, 3, 39), (3, 1, 3), (2, 11, 4094), (2, 12, 8190), (3, 7, 3279),
    (3, 8, 9840), (1, 12, 12), (0, 3, 0),
])
def test_transactions_count(branches, deliveries, total):
    """N + N**2 + ... + N**K (``context/MULTIVERSE-PLAN.md``: the future
    5000 limit depends on the will-executors)."""
    levels = dv.transactions_count(branches, deliveries)
    assert len(levels) == deliveries and sum(levels) == total


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
