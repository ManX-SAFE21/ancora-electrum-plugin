"""Staged inheritance on the command line: the table of the deliveries (#169, M3b).

The engine of #168 builds a will in up to three deliveries from the table
``will_settings["instalments"]``; here the commands that write it, show it
and remove it (owner decisions of 2026-10-19, ``context/MULTIVERSE-PLAN.md``
M3, and the M3b answers: the heir commands work and say what the table
needs; the table as JSON or a JSON file):

* ``bal_instalments_show``: the table, the totals, the gaps, the items of
  the will per delivery; ``bal_delivery_show`` carries it too;
* ``bal_instalments_set``: ``--table`` (JSON), ``--path`` or
  ``--example true``; every rule checked before anything is written; a date
  earlier than the one in force, or shares that would arrive earlier (owner
  decision (a)), need ``--confirm_earlier true``; a later date says that
  the next prepare asks for the paid invalidation;
* ``bal_instalments_clear``: one delivery again (the rest of the estate
  arrives earlier: ``--confirm_earlier true``), the heirs' amounts shown;
* ``bal_delivery_set`` keeps delivery 1 at least 28 days before delivery 2;
  ``bal_delivery_form period`` is refused while there is a table;
* the heir commands add ``instalments_note``; a table that no longer fits
  stops ``bal_will_prepare`` with the commands that fix it;
* the texts name the person ("luca (delivery 2)", in a will in deliveries
  also "luca (delivery 1)"), never the row name "luca§2" (owner decision
  2), also in ``bal_will_status``.

Wallets, coins and will-executors come from ``tests/flowkit.py``: three fake
will-executors (each with its own fee address), throw-away test wallets with
the test password, a fixed clock. Nothing reaches a real will-executor.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_cli_instalments.py
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
from bal.core.messages import will_change_message
from bal.core.will import HeirNotFoundException

# Kept at module level: flowkit's window needs a Qt application.
_APP = QApplication.instance() or QApplication(sys.argv)

# Test value: the password of the throw-away test wallets.
TEST_PASSWORD = "flowkit-test"
D1 = fk.ts(2030, 3, 1)
D2 = fk.ts(2031, 3, 1)
D3 = fk.ts(2032, 3, 1)
ADDR = {"luca": fk.address(20), "john": fk.address(21), "maria": fk.address(22)}
_EXECUTOR_HEIR = 'w!ll3x3c"'
MISMATCH = "no longer matches the heirs"
# The owner's table: luca and john, 10/10 %, 10/10 %, 30/30 %.
OWNER_TABLE = [
    {"date": "2030-03-01", "shares": {"luca": "10%", "john": "10%"}},
    {"date": "2031-03-01", "shares": {"luca": "10%", "john": "10%"}},
    {"date": "2032-03-01", "shares": {"luca": "30%", "john": "30%"}},
]


@pytest.fixture
def make(monkeypatch):
    """flowkit sessions: ADVANCED, three will-executors, synchronized,
    the heirs luca and john (their amounts are not used with a table)."""
    fk.ensure_event_loop()
    made = []

    def _make(**kw):
        """One session; ``fk.Session`` arguments."""
        kw.setdefault("mode", "advanced")
        kw.setdefault("locktime", D1)
        s = fk.Session(monkeypatch, password=TEST_PASSWORD, fee=2, **kw)
        s.world.add_executor()
        # The flowkit wallet is offline: synchronized for the #128 rules.
        monkeypatch.setattr(s.wallet, "is_up_to_date", lambda: True)
        for name in list(s.bw.heirs.keys()):
            del s.bw.heirs[name]
        s.bw.heirs.save()
        for name in ("luca", "john"):
            s.bw.heirs[name] = [ADDR[name], "50%", D1]
        made.append(s)
        return s

    yield _make
    for s in made:
        s.close()
    fk.stop_event_loop()


# ------------------------------------------------------------------ helpers

def returned(result):
    """What the command returned (``Session.cli`` wraps it)."""
    assert "returned" in result, result
    return result["returned"]


def raised(result):
    """The message of the error the command raised, as the terminal shows it."""
    assert "raised" in result, result
    kind, _sep, text = result["raised"].partition(": ")
    assert kind == "UserFacingException", result
    return text


def settings(s):
    """The wallet's own delivery settings, as text (to compare)."""
    return json.dumps(dict(s.wallet.db.get_dict("will_settings")),
                      sort_keys=True, default=str)


def heirs_text(s):
    """The stored heirs, as text (to compare)."""
    return json.dumps(dict(s.wallet.db.get("heirs", {}) or {}), sort_keys=True)


def per_delivery(status):
    """``{delivery: count}`` of the VALID items of ``bal_will_status``."""
    return Counter(i["delivery"] for i in status["items"] if i["status"]["VALID"])


def sent_will(s, table=OWNER_TABLE):
    """Write ``table``, then prepare, sign and send the will by command."""
    returned(s.cli("instalments_set", table))
    assert returned(s.cli("prepare_will"))["result"] == "rebuilt"
    returned(s.cli("sign_transactions", TEST_PASSWORD))
    returned(s.cli("broadcast_command"))
    return returned(s.cli("will_status"))


# ----------------------------------------------------------- show and set

def test_show_without_a_table(make):
    """One delivery: said in words, with the command that stages it."""
    s = make()
    out = returned(s.cli("instalments_show"))
    assert out["staged"] is False and out["deliveries"] == []
    assert out["date"] == {"locktime": D1, "date": "2030-03-01"}
    assert "One delivery: every heir receives on 2030-03-01" in out["message"]
    assert "bal_instalments_set" in out["message"]
    show = returned(s.cli("delivery_show"))
    assert show["staged"] is False and show["deliveries"] == []


def test_set_stores_the_table_and_show_gives_it_back(make):
    """The table stored as the engine keeps it (schema 3, the wallet's
    date = delivery 1); the show answer, given back, is the same table."""
    s = make(locktime=D1 - 30 * fk.DAY)
    out = returned(s.cli("instalments_set", OWNER_TABLE))
    d = s.wallet.db.get_dict("will_settings")
    assert d["locktime"] == D1
    assert d["settings_version"] == delivery.INSTALMENTS_SCHEMA_VERSION
    assert [row["locktime"] for row in d["instalments"]] == [D1, D2, D3]
    assert out["staged"] is True and out["fits_heirs"] is True
    assert out["deliveries"] == [
        {"delivery": 1, "date": "2030-03-01", "locktime": D1,
         "shares": {"luca": "10%", "john": "10%"}},
        {"delivery": 2, "date": "2031-03-01", "locktime": D2,
         "shares": {"luca": "10%", "john": "10%"}},
        {"delivery": 3, "date": "2032-03-01", "locktime": D3,
         "shares": {"luca": "30%", "john": "30%"}},
    ]
    assert out["delivery_totals"] == ["20%", "20%", "60%"]
    assert out["heir_totals"] == {"luca": "50%", "john": "50%"}
    assert out["total"] == "100%"
    assert out["gaps_days"] == [365, 366]
    assert out["will_items"] == [0, 0, 0]
    assert ("The table of the deliveries is stored: delivery 1 on 2030-03-01, "
            "delivery 2 on 2031-03-01, delivery 3 on 2032-03-01.") in out["message"]
    assert "Run bal_will_prepare" in out["message"]
    before = settings(s)
    # The answer of the show command, given back as it is.
    shown = returned(s.cli("instalments_show"))
    returned(s.cli("instalments_set", shown))
    assert settings(s) == before
    assert returned(s.cli("delivery_show"))["deliveries"] == shown["deliveries"]


def test_the_table_as_json_text_and_as_a_file(make):
    """JSON-RPC callers may send the text; ``--path`` reads a file (the
    show answer saved, or a plain list)."""
    s = make()
    returned(s.cli("instalments_set", json.dumps(OWNER_TABLE)))
    first = settings(s)
    path = os.path.join(s.dir, "table.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(returned(s.cli("instalments_show")), f)
    returned(s.cli("instalments_clear", True))
    returned(s.cli("instalments_set", None, path))
    assert settings(s) == first


def test_the_example_table(make):
    """``--example true``: the owner's example (33 / 33 / 34 % split among
    the heirs) from the wallet's date, one year apart."""
    s = make()
    out = returned(s.cli("instalments_set", None, None, True))
    assert [r["date"] for r in out["deliveries"]] == [
        "2030-03-01", "2031-03-01", "2032-03-01"]
    assert [r["shares"] for r in out["deliveries"]] == [
        {"luca": "16.5%", "john": "16.5%"},
        {"luca": "16.5%", "john": "16.5%"},
        {"luca": "17%", "john": "17%"},
    ]
    assert out["total"] == "100%"


def _rows(*shares, dates=("2030-03-01", "2031-03-01")):
    """A table of ``dates`` with these shares."""
    return [{"date": d, "shares": sh} for d, sh in zip(dates, shares, strict=True)]


@pytest.mark.parametrize("table, expected", [
    (_rows({"luca": "50%"}, {"john": "40%"}), "add up to 90%"),
    (_rows({"luca": "50%"}, {"john": "50%"}, dates=("2030-03-01", "2030-03-28")),
     "less than 28 days"),
    ([{"date": "2030-03-01", "shares": {"luca": "100%"}}], "1 delivery dates"),
    (_rows({"luca": "50%"}, {"maria": "50%"}), "not an heir"),
    (_rows({"luca": "50.001%"}, {"john": "49.999%"}), "two decimals at most"),
    (_rows({"luca": "50%", "john": "0%"}, {"luca": "50%", "john": "0%"}),
     "john: gets nothing"),
    ([{"dat": "2030-03-01", "shares": {"luca": "50%"}}], "unknown field dat"),
    ([{"date": "2030-02-30", "shares": {"luca": "100%"}}], "No such day"),
    ([{"date": "1y", "shares": {"luca": "100%"}}], "A period such as 1y"),
    ([{"date": "2020-01-01", "shares": {"luca": "100%"}}], "not in the future"),
    ([{"shares": {"luca": "100%"}}], "Delivery 1: no date."),
    ([{"date": "2030-03-01"}], "Delivery 1: no shares."),
    ([{"delivery": 2, "date": "2030-03-01", "shares": {"luca": "100%"}}],
     "it says delivery 2"),
    ([{"delivery": True, "date": "2030-03-01", "shares": {"luca": "100%"}}],
     "Delivery 1: not a delivery number: True"),
    ([{"delivery": "first", "date": "2030-03-01", "shares": {"luca": "100%"}}],
     "not a delivery number"),
    ([{"date": "2030-03-01", "locktime": D2, "shares": {"luca": "100%"}}],
     "two different dates"),
    ({"luca": "100%"}, "must be a list of deliveries"),
    ("[{nope", "not valid JSON"),
    (["2030-03-01"], "not a delivery with date and shares"),
])
def test_a_bad_table_writes_nothing(make, table, expected):
    """Every rule is checked before anything is written; the message says
    which delivery and which rule."""
    s = make()
    returned(s.cli("instalments_set", OWNER_TABLE))
    before = settings(s)
    assert expected in raised(s.cli("instalments_set", table))
    assert settings(s) == before


@pytest.mark.parametrize("args", [(), (OWNER_TABLE, "x.json"), (OWNER_TABLE, None, True)])
def test_one_source_only(make, args):
    """--table, --path or --example: exactly one."""
    s = make()
    assert "one of --table, --path or --example true" in raised(
        s.cli("instalments_set", *args))
    assert "instalments" not in s.wallet.db.get_dict("will_settings")


def test_refused_in_basic_mode_with_the_period_form_and_without_heirs(make):
    """Owner decision D5 (ADVANCED only), #127 decision 10 (the period has
    one date) and no heirs: nothing written."""
    s = make()
    s.plugin.USER_TYPE.set("basic")
    assert "ADVANCED mode only" in raised(s.cli("instalments_set", OWNER_TABLE))
    s.plugin.USER_TYPE.set("advanced")
    returned(s.cli("delivery_form", "period", None, 36))
    assert "renewable period" in raised(s.cli("instalments_set", OWNER_TABLE))
    returned(s.cli("delivery_form", "fixed", "2030-03-01"))
    returned(s.cli("heirs_delete", ["luca", "john"]))
    assert "There are no heirs" in raised(s.cli("instalments_set", OWNER_TABLE))
    assert "instalments" not in s.wallet.db.get_dict("will_settings")


# --------------------------------------------- the whole cycle by command

def test_prepare_sign_send_and_status_name_the_people(make):
    """The table builds the chain (3 + 9 + 27 with three will-executors);
    the status names the people with their delivery; show counts the
    items; nothing is asked twice."""
    s = make()
    status = sent_will(s)
    assert per_delivery(status) == {1: 3, 2: 9, 3: 27}
    for item in status["items"]:
        people = {h for h in item["heirs"] if not h.startswith(_EXECUTOR_HEIR)}
        assert people == {"luca", "john"}, item
    assert all(i["status"]["PUSHED"] for i in status["items"] if i["status"]["VALID"])
    assert returned(s.cli("instalments_show"))["will_items"] == [3, 9, 27]
    assert returned(s.cli("prepare_will"))["result"] == "coherent"


def test_a_will_with_one_date_has_no_delivery_field(make):
    """Without a table ``bal_will_status`` is as before #169."""
    s = make()
    returned(s.cli("prepare_will"))
    items = returned(s.cli("will_status"))["items"]
    assert items and all("delivery" not in i for i in items)


def test_an_earlier_date_needs_the_confirmation(make):
    """As bal_delivery_set: a delivery earlier than the one in force
    (delivery 1, and delivery 2 of the will in force) is refused without
    --confirm_earlier true; nothing written."""
    s = make()
    sent_will(s)
    before = settings(s)
    earlier_1 = [dict(OWNER_TABLE[0], date="2030-02-01")] + OWNER_TABLE[1:]
    text = raised(s.cli("instalments_set", earlier_1))
    assert "Delivery 1: 2030-02-01 is earlier than the date in force (2030-03-01)" in text
    assert "--confirm_earlier true" in text
    earlier_2 = [OWNER_TABLE[0], dict(OWNER_TABLE[1], date="2031-02-01"), OWNER_TABLE[2]]
    assert "Delivery 2: 2031-02-01 is earlier" in raised(s.cli("instalments_set", earlier_2))
    assert settings(s) == before
    out = returned(s.cli("instalments_set", earlier_2, None, None, True))
    assert out["deliveries"][1]["date"] == "2031-02-01"


def test_a_later_delivery_says_and_prepare_asks_the_invalidation(make):
    """D6: delivery 2 later than the will in force is stored, the message
    says the next prepare asks for the paid invalidation, and it does."""
    s = make()
    sent_will(s)
    s.clock.travel(1)
    later = [OWNER_TABLE[0], dict(OWNER_TABLE[1], date="2031-04-01"), OWNER_TABLE[2]]
    out = returned(s.cli("instalments_set", later))
    assert "Delivery 2 is later than the will in force (2031-03-01)" in out["message"]
    assert "Delivery 1 is later" not in out["message"]
    result = returned(s.cli("prepare_will"))
    assert result["result"] == "postponed"
    assert result["invalidation_tx"]["txid"]


def test_an_earlier_delivery_2_is_a_free_rebuild_named_by_its_delivery(make):
    """Delivery 2 earlier (confirmed): rebuilt, nothing to invalidate, and
    the reason names the delivery (#169: every heir of delivery 2 has its
    date, so "the date of delivery 2 changed")."""
    s = make()
    sent_will(s)
    s.clock.travel(1)
    earlier = [OWNER_TABLE[0], dict(OWNER_TABLE[1], date="2031-02-01"), OWNER_TABLE[2]]
    returned(s.cli("instalments_set", earlier, None, None, True))
    result = returned(s.cli("prepare_will"))
    assert result["result"] == "rebuilt", result
    assert result["reason"] == "The date of delivery 2 changed: preparing a new will."
    assert "§" not in json.dumps(result, ensure_ascii=False)


def test_a_share_change_names_the_person_and_the_delivery(make):
    """Shares moved between deliveries 2 and 3: the reason names "luca
    (delivery 2)", never "luca§2"."""
    s = make()
    sent_will(s)
    s.clock.travel(1)
    moved = [OWNER_TABLE[0],
             dict(OWNER_TABLE[1], shares={"luca": "15%", "john": "10%"}),
             dict(OWNER_TABLE[2], shares={"luca": "25%", "john": "30%"})]
    # 5 % more by delivery 2: owner decision (a).
    assert "would receive 45% of the estate instead of 40%" in raised(
        s.cli("instalments_set", moved))
    returned(s.cli("instalments_set", moved, None, None, True))
    result = returned(s.cli("prepare_will"))
    assert result["result"] == "rebuilt", result
    assert result["reason"] == (
        'The amount of heir "luca (delivery 2)" changed: preparing a new will.')


def test_a_person_out_of_delivery_1_is_named_with_its_delivery(make):
    """luca 10/10/30 becomes 0/20/30: luca's row of delivery 1 goes, but
    luca still receives later: "luca (delivery 1)", never "Heir luca was
    removed" (review of #169)."""
    s = make()
    sent_will(s)
    s.clock.travel(1)
    later = [dict(OWNER_TABLE[0], shares={"luca": "0%", "john": "10%"}),
             dict(OWNER_TABLE[1], shares={"luca": "20%", "john": "10%"}),
             OWNER_TABLE[2]]
    returned(s.cli("instalments_set", later))
    result = returned(s.cli("prepare_will"))
    assert result["result"] == "rebuilt", result
    assert result["reason"] == (
        'Heir "luca (delivery 1)" was removed: preparing a new will.')


def test_shares_that_arrive_earlier_need_the_confirmation(make):
    """Owner decision (a): the same dates, but more of the estate by a date
    (rows swapped, delivery 3 removed) is refused without
    --confirm_earlier true; shares moved later need nothing."""
    s = make()
    sent_will(s)
    before = settings(s)
    swapped = [dict(OWNER_TABLE[0], shares={"luca": "30%", "john": "30%"}),
               OWNER_TABLE[1],
               dict(OWNER_TABLE[2], shares={"luca": "10%", "john": "10%"})]
    text = raised(s.cli("instalments_set", swapped))
    assert text.startswith(
        "Delivery 1 (2030-03-01): by this date the heirs would receive 60% of "
        "the estate instead of 20%: part of it would arrive earlier.")
    assert text.endswith(
        "Ask the owner; if this is what the owner wants, run the command "
        "again with --confirm_earlier true.")
    two = [OWNER_TABLE[0], dict(OWNER_TABLE[1], shares={"luca": "40%", "john": "40%"})]
    assert ("Delivery 2 (2031-03-01): by this date the heirs would receive 100% "
            "of the estate instead of 40%") in raised(s.cli("instalments_set", two))
    assert settings(s) == before
    later = [dict(OWNER_TABLE[0], shares={"luca": "5%", "john": "5%"}),
             dict(OWNER_TABLE[1], shares={"luca": "5%", "john": "5%"}),
             dict(OWNER_TABLE[2], shares={"luca": "40%", "john": "40%"})]
    returned(s.cli("instalments_set", later))
    out = returned(s.cli("instalments_set", swapped, None, None, True))
    assert out["delivery_totals"] == ["60%", "20%", "20%"]


def test_the_reference_is_the_will_in_force_else_the_stored_table(make):
    """Delivery 2 of the will in force is 2031-03-01; the stored table says
    2031-04-01 (not prepared yet): 2031-03-15 is not earlier than the will
    in force, so no confirmation. Without a will the stored table is the
    reference, and the message says so."""
    s = make()
    sent_will(s)
    moved = [OWNER_TABLE[0], dict(OWNER_TABLE[1], date="2031-04-01"), OWNER_TABLE[2]]
    returned(s.cli("instalments_set", moved))
    back = [OWNER_TABLE[0], dict(OWNER_TABLE[1], date="2031-03-15"), OWNER_TABLE[2]]
    out = returned(s.cli("instalments_set", back))
    assert out["deliveries"][1]["date"] == "2031-03-15"

    other = make()
    returned(other.cli("instalments_set", OWNER_TABLE))
    earlier = [OWNER_TABLE[0], dict(OWNER_TABLE[1], date="2031-02-01"), OWNER_TABLE[2]]
    text = raised(other.cli("instalments_set", earlier))
    assert text.startswith(
        "Delivery 2: 2031-02-01 is earlier than the current date of this "
        "delivery (2031-03-01): the heirs could receive the bitcoin earlier.")


def test_a_date_not_at_midnight_comes_back_as_it_is(make):
    """A wallet date at 01:00 UTC: the show answer says
    "2030-03-01 01:00:00 UTC" and, given back, changes nothing."""
    s = make()
    returned(s.cli("delivery_set", str(D1 + 3600)))
    returned(s.cli("instalments_set", None, None, True))
    shown = returned(s.cli("instalments_show"))
    assert shown["deliveries"][0]["date"] == "2030-03-01 01:00:00 UTC"
    before = settings(s)
    returned(s.cli("instalments_set", shown))
    assert settings(s) == before


def test_a_delivery_number_as_text_or_decimal(make):
    """Electrum's JSON gives decimals as text: "1" and 2.0 are accepted."""
    s = make()
    table = [dict(OWNER_TABLE[0], delivery="1"), dict(OWNER_TABLE[1], delivery=2.0),
             dict(OWNER_TABLE[2], delivery=3)]
    assert returned(s.cli("instalments_set", table))["total"] == "100%"


# ------------------------------------------------ the other commands

def test_delivery_set_keeps_28_days_before_delivery_2(make):
    """With a table the wallet's date is delivery 1: at most 28 days before
    delivery 2 (owner decision 5); the table follows."""
    s = make()
    returned(s.cli("instalments_set", OWNER_TABLE))
    before = settings(s)
    text = raised(s.cli("delivery_set", "2031-02-02"))
    assert "at least 28 days before delivery 2 (2031-03-01)" in text
    assert "bal_instalments_set" in text
    assert settings(s) == before
    out = returned(s.cli("delivery_set", "2031-02-01"))
    assert out["deliveries"][0]["date"] == "2031-02-01"
    assert out["date"] == "2031-02-01"


def test_delivery_form_fixed_keeps_the_28_days_and_period_is_refused(make):
    """The period form has one date: refused with the command that removes
    the table; a fixed date keeps its 28 days."""
    s = make()
    returned(s.cli("instalments_set", OWNER_TABLE))
    before = settings(s)
    text = raised(s.cli("delivery_form", "period", None, 36))
    assert "Remove the table first with bal_instalments_clear" in text
    assert "at least 28 days" in raised(s.cli("delivery_form", "fixed", "2031-02-15"))
    assert settings(s) == before


def test_the_heir_commands_say_what_the_table_needs(make):
    """Owner answer 1a: the heir commands work; with a table they say
    whether the heirs still fit it. A table that no longer fits stops
    prepare, with the commands that fix it; show says why."""
    s = make()
    out = returned(s.cli("heirs_add", "maria", ADDR["maria"], "10%"))
    assert "instalments_note" not in out
    returned(s.cli("heirs_delete", "maria"))
    returned(s.cli("instalments_set", OWNER_TABLE))

    out = returned(s.cli("heirs_add", "maria", ADDR["maria"], "10%"))
    assert "no longer match the table" in out["instalments_note"]
    text = raised(s.cli("prepare_will"))
    assert MISMATCH in text
    assert "bal_instalments_show" in text and "bal_instalments_set" in text
    show = returned(s.cli("instalments_show"))
    assert show["fits_heirs"] is False and "maria" in show["problem"]
    assert MISMATCH in show["message"]
    assert len(show["deliveries"]) == 3

    with_maria = [dict(r, shares=dict(r["shares"], maria="0%")) for r in OWNER_TABLE]
    with_maria[2]["shares"] = {"luca": "25%", "john": "25%", "maria": "10%"}
    returned(s.cli("instalments_set", with_maria))
    out = returned(s.cli("heirs_update", "luca", None, "1%"))
    assert "come from it" in out["instalments_note"]
    out = returned(s.cli("heirs_delete", "maria"))
    assert "no longer match the table" in out["instalments_note"]


def test_clear_gives_one_delivery_and_shows_the_heirs_amounts(make):
    """The table removed: the heirs' own amounts count again, so they are
    shown. #188: with the will in several deliveries sent, the command is
    refused first without ``--confirm_invalidation true`` (the will must
    be invalidated on the blockchain), then without ``--confirm_earlier
    true``; prepare then returns "postponed" with the invalidation
    transaction (before, it built one delivery one day earlier, free)."""
    s = make()
    sent_will(s)
    s.clock.travel(1)
    before = settings(s)
    text = raised(s.cli("instalments_clear"))
    assert text.startswith("Back to one delivery: your will in 3 deliveries")
    assert "--confirm_invalidation true" in text
    assert settings(s) == before
    text = raised(s.cli("instalments_clear", False, True))
    assert text.startswith(
        "With one delivery only, the heirs receive the whole estate on "
        "2030-03-01, the date of delivery 1. Today they receive 20% by that "
        "date: the rest would arrive earlier than planned.")
    assert "--confirm_earlier true" in text
    assert settings(s) == before
    out = returned(s.cli("instalments_clear", True, True))
    assert out["removed"] is True
    assert out["heirs"] == {"luca": [ADDR["luca"], "50%", D1],
                            "john": [ADDR["john"], "50%", D1]}
    assert "with the amounts of the heirs" in out["message"]
    assert "on 2030-03-01" in out["message"]
    again = returned(s.cli("instalments_clear"))
    assert again["removed"] is False and "There is no table" in again["message"]
    assert returned(s.cli("instalments_show"))["staged"] is False
    result = returned(s.cli("prepare_will"))
    assert result["result"] == "postponed", result
    assert result["message"].startswith("Back to one delivery: your will in 3 deliveries")
    assert result["invalidation_tx"]
    valid = [i for i in result["will"]["items"] if i["status"]["VALID"]]
    assert len(valid) == 39


def test_the_heirs_are_untouched_by_the_table_commands(make):
    """The table never rewrites the stored heirs (one row per person)."""
    s = make()
    before = heirs_text(s)
    returned(s.cli("instalments_set", OWNER_TABLE))
    returned(s.cli("instalments_clear", True))
    assert heirs_text(s) == before


def test_the_commands_are_parsed_by_electrum():
    """Electrum's own parser: ``--table`` is JSON (the list reaches the
    command), ``--example`` and ``--confirm_earlier`` are bools, ``--path``
    a text; a wallet is needed, no password."""
    from electrum.commands import get_parser, known_commands

    import bal.cli.commands  # noqa: F401 - registers the bal_* commands

    for name in ("bal_instalments_show", "bal_instalments_set",
                 "bal_instalments_clear"):
        cmd = known_commands[name]
        assert cmd.requires_wallet and not cmd.requires_password, name
    assert known_commands["bal_instalments_set"].arg_types == {
        "table": "json", "path": "str", "example": "bool", "confirm_earlier": "bool"}
    args = get_parser().parse_args(
        ["bal_instalments_set", "--table", json.dumps(OWNER_TABLE),
         "--confirm_earlier", "true"])
    assert args.table == OWNER_TABLE and args.confirm_earlier is True
    args = get_parser().parse_args(["bal_instalments_set", "--example", "true"])
    assert args.example is True and args.table is None and args.path is None


# ------------------------------------------------------- the texts

@pytest.mark.parametrize("key, change, all_heirs, staged, expected", [
    ("luca§2", HeirNotFoundException.HEIR_AMOUNT, False, True,
     'The amount of heir "luca (delivery 2)" changed: preparing a new will.'),
    ("luca§3", HeirNotFoundException.HEIR_REMOVED, False, True,
     'Heir "luca (delivery 3)" was removed: preparing a new will.'),
    ("luca§2", HeirNotFoundException.HEIR_ADDED, False, True,
     'Heir "luca (delivery 2)" was added: preparing a new will.'),
    ("luca§2", HeirNotFoundException.HEIR_ADDRESS, False, True,
     'The address of heir "luca (delivery 2)" changed: preparing a new will.'),
    ("luca§3", HeirNotFoundException.HEIR_DATE, True, True,
     "The date of delivery 3 changed: preparing a new will."),
    ("luca§3", HeirNotFoundException.HEIR_DATE, False, True,
     'The delivery date of heir "luca (delivery 3)" changed: preparing a new will.'),
    ("luca", HeirNotFoundException.HEIR_REMOVED, False, True,
     'Heir "luca (delivery 1)" was removed: preparing a new will.'),
    ("luca", HeirNotFoundException.HEIR_DATE, True, True,
     "The date of delivery 1 changed: preparing a new will."),
    # A will with one date: as before #169.
    ("luca", HeirNotFoundException.HEIR_DATE, True, False,
     "The delivery date changed: preparing a new will."),
    ("luca", HeirNotFoundException.HEIR_AMOUNT, False, False,
     'The amount of heir "luca" changed: preparing a new will.'),
    ("luca", HeirNotFoundException.HEIR_REMOVED, False, False,
     'Heir "luca" was removed: preparing a new will.'),
    # The table removed: the old rows keep their delivery.
    ("luca§2", HeirNotFoundException.HEIR_REMOVED, False, False,
     'Heir "luca (delivery 2)" was removed: preparing a new will.'),
    ("luca§2", None, False, True,
     'Heir "luca (delivery 2)" is not covered by the current will (it was added '
     "or removed, or its delivery date changed): a new will must be prepared."),
])
def test_the_check_messages_name_the_person(key, change, all_heirs, staged, expected):
    """Owner decision 2: the owner never reads "luca§2"; in a will in
    deliveries (``staged``) delivery 1 is named too."""
    e = HeirNotFoundException(key, change, all_heirs=all_heirs, staged=staged)
    assert will_change_message(e) == expected


def test_a_rename_is_worded_with_the_plain_names():
    """luca§2 renamed marco§2: "luca → marco"."""
    e = HeirNotFoundException("luca§2", HeirNotFoundException.HEIR_RENAMED,
                              new_name="marco§2")
    assert will_change_message(e) == "Heir renamed: luca → marco: preparing a new will."


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
