"""The rules of the per-wallet delivery settings (CHANGELOG #123).

``bal.core.delivery`` is pure: no Qt, no wallet, no clock. These tests give
it plain values and check:

* the month arithmetic (the examples of the #123 plan, section 2);
* what a stored value means (``as_delivery``, ``is_fee``, ``is_current``,
  ``resolve_legacy_date``, ``validate_values``);
* each source of the one-time migration (will / sent / global period /
  global date / legacy wallet / default), the "plugin data" gate, the fee
  rules and the owner's own case (2029-08-29 00:00 UTC, fee 13, note
  "converted");
* every note kind and the "which date?" question;
* the anchor that follows a will changed by another program;
* the funds-safety rules of the #123 review: the anchor never follows an
  unsigned draft (FS-1), the later wish the migration keeps and when it is
  applied (FS-2), and no pending postpone with multiverse;
* the date the command line accepts;
* that the module never reads the clock and imports only the standard
  library.

The will items come as ``ItemView`` objects; each will has one item per
will-executor, three fake will-executors with their own fee address.

Run (standalone or with pytest):
    PYTHONPATH=electrum-src python3 -m pytest tests/test_core_delivery_settings.py -q
"""

import ast
import copy
import math
import os
import re
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from bal.core import delivery as dv

MODULE = os.path.join(os.path.dirname(__file__), os.pardir, "bal", "core", "delivery.py")

# Three fake will-executors, each with its own fee address (never contacted).
EXECUTORS = (
    ("https://we-one.invalid", "fee-address-one"),
    ("https://we-two.invalid", "fee-address-two"),
    ("https://we-three.invalid", "fee-address-three"),
)


def ts(y, m, d, hh=0, mm=0):
    """UNIX time of a UTC date and time."""
    return int(datetime(y, m, d, hh, mm, tzinfo=timezone.utc).timestamp())


# The test clock: 2026-10-11 09:30 UTC (the day of the owner decisions).
NOW = ts(2026, 10, 11, 9, 30)
TODAY = ts(2026, 10, 11)
# The owner's will in force: 2029-08-29 00:00 UTC.
OWNER = 1882656000


def will(locktime, recipe="1054d", fee=13, tag="a", **flags):
    """One will: an item per fake will-executor, the same heirs and date.

    ``flags`` are the WillItem status flags of every item (e.g. VALID=True).
    """
    items = []
    for n, (url, address) in enumerate(EXECUTORS):
        heirs = {
            "alice": ["alice-address", "50%", recipe],
            "bob": ["bob-address", "50%", recipe],
            'w!ll3x3c"' + url + '"' + str(locktime): [address, 10000, locktime],
        }
        items.append(dv.item_view("{}{}".format(tag, n), locktime, fee, flags, heirs))
    return items


def signed_will(locktime, **kw):
    """A will in force: VALID, signed (COMPLETE), sent (PUSHED)."""
    return will(locktime, VALID=True, COMPLETE=True, PUSHED=True, **kw)


class Recorder(dict):
    """A settings dict that records every write and refuses ``update`` and
    ``clear`` (on Electrum's StoredDict they skip the wallet-file patches)."""

    def __init__(self, *args, **kwargs):
        """A dict with an empty write log."""
        super().__init__(*args, **kwargs)
        self.writes = []

    def __setitem__(self, key, value):
        """Log the write, then set the item."""
        self.writes.append(("set", key))
        super().__setitem__(key, value)

    def __delitem__(self, key):
        """Log the delete, then delete the item."""
        self.writes.append(("del", key))
        super().__delitem__(key)

    def update(self, *args, **kwargs):
        """Refused: StoredDict.update skips the wallet-file patches."""
        raise AssertionError("update() must not be used on the wallet dict")

    def clear(self):
        """Refused: the dict is changed item by item only."""
        raise AssertionError("clear() must not be used on the wallet dict")


# ------------------------------------------------------------------ constants


def test_constants():
    """The names and values the engine, the CLI and Phase 6 rely on."""
    assert dv.DAY == 86400
    assert dv.LOCKTIME_MIN == 500_000_000
    assert dv.LOCKTIME_MAX == 2**32 - 1
    assert dv.WALLET_KEY == "will_settings"
    assert dv.SCHEMA_VERSION == 1
    assert dv.DEFAULT_FEE == 20
    assert dv.DEFAULT_MONTHS == 12
    assert dv.DEFAULT_THRESHOLD == "-180d"
    assert dv.LEGACY_DROP == ("tx_fees", "real_threshold", "real_locktime")


def test_the_owner_date_is_midnight_utc():
    """1882656000 is 2029-08-29 00:00 UTC."""
    assert ts(2029, 8, 29) == OWNER
    assert dv.utc_midnight(OWNER) == OWNER


# ------------------------------------------------------------------ months


@pytest.mark.parametrize(
    "start, months, expected",
    [
        ((2026, 10, 11), 36, (2029, 10, 11)),
        ((2026, 1, 31), 1, (2026, 2, 28)),
        ((2027, 1, 31), 13, (2028, 2, 29)),
        ((2028, 2, 29), 12, (2029, 2, 28)),
        ((2029, 8, 29), -6, (2029, 2, 28)),
        ((2026, 3, 31), -1, (2026, 2, 28)),
        ((2026, 1, 15), -13, (2024, 12, 15)),
        ((2100, 1, 31), 1, (2100, 2, 28)),
        ((2026, 10, 11), 0, (2026, 10, 11)),
    ],
)
def test_add_months_examples(start, months, expected):
    """Calendar months, the day clamped to a shorter month (plan section 2)."""
    assert dv.add_months(ts(*start), months) == ts(*expected)


def test_add_months_gives_midnight_utc():
    """The time of day is dropped: the result is 00:00 UTC."""
    assert dv.add_months(NOW, 0) == TODAY
    assert dv.add_months(ts(2026, 10, 11, 23, 59), 12) == ts(2027, 10, 11)


def test_utc_midnight():
    """The UTC day of a timestamp, at 00:00, as an int (floats too)."""
    assert dv.utc_midnight(NOW) == TODAY
    assert dv.utc_midnight(TODAY) == TODAY
    assert dv.utc_midnight(NOW + 0.5) == TODAY
    assert isinstance(dv.utc_midnight(NOW + 0.5), int)
    assert dv.utc_midnight(ts(2029, 8, 29, 23, 59)) == OWNER


def test_default_delivery_is_twelve_months_from_today():
    """A wallet with nothing to migrate from: today + 12 months, 00:00 UTC."""
    assert dv.default_delivery(NOW) == ts(2027, 10, 11)


# ------------------------------------------------------------------ values


@pytest.mark.parametrize(
    "value, expected",
    [
        (OWNER, OWNER),
        (OWNER + 0.9, OWNER),
        (float(OWNER), OWNER),
        (str(OWNER), OWNER),
        (" 1882656000.0 ", OWNER),
        (dv.LOCKTIME_MIN, dv.LOCKTIME_MIN),
        (dv.LOCKTIME_MAX, dv.LOCKTIME_MAX),
    ],
)
def test_as_delivery_accepts_dates(value, expected):
    """A number, or a text holding one, in the date range."""
    assert dv.as_delivery(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        False,
        "1054d",
        "1y",
        "36m",
        "-180d",
        "",
        "abc",
        dv.LOCKTIME_MIN - 1,
        800_000,
        0,
        86400,
        2**32,
        float("nan"),
        float("inf"),
        "nan",
        [OWNER],
        {"locktime": OWNER},
    ],
)
def test_as_delivery_refuses_the_rest(value):
    """Periods, block heights, 1970 values, bools and garbage are no date."""
    assert dv.as_delivery(value) is None


def test_is_fee():
    """A finite number >= 1, never rounded; texts and bools are not fees."""
    for fee in (1, 13, 13.001, 1.0, 10000):
        assert dv.is_fee(fee), fee
    for fee in (0, 0.5, -1, True, False, "13", None, float("nan"), float("inf")):
        assert not dv.is_fee(fee), fee


def current(**changes):
    """A current (migrated) settings dict, with ``changes`` applied."""
    d = {
        "settings_version": 1,
        "locktime": OWNER,
        "baltx_fees": 13,
        "threshold": "-180d",
    }
    d.update(changes)
    return {k: v for k, v in d.items() if v is not None}


def test_is_current_accepts_migrated_settings():
    """Schema 1, an int date and a fee; a later schema (downgrade) too."""
    assert dv.is_current(current())
    assert dv.is_current(current(baltx_fees=13.001))
    assert dv.is_current(current(settings_version=2))
    assert dv.is_current(current(settings_version=3, threshold=None))


@pytest.mark.parametrize(
    "changes",
    [
        {"settings_version": None},
        {"settings_version": 0},
        {"settings_version": True},
        {"settings_version": "1"},
        {"locktime": None},
        {"locktime": float(OWNER)},
        {"locktime": str(OWNER)},
        {"locktime": "1054d"},
        {"locktime": 86400},
        {"locktime": 800_000},
        {"locktime": 2**32},
        {"locktime": True},
        {"baltx_fees": None},
        {"baltx_fees": 0},
        {"baltx_fees": "13"},
    ],
)
def test_is_current_refuses(changes):
    """Type, range, period, float or 1970: migrated again (rule I2)."""
    assert not dv.is_current(current(**changes))


def test_is_current_refuses_a_non_mapping():
    """Garbage instead of a dict is not current."""
    assert not dv.is_current(None)
    assert not dv.is_current("1054d")
    assert not dv.is_current([("locktime", OWNER)])


def test_resolve_legacy_date_periods_count_from_today():
    """A period counts from 00:00 UTC of today (the old forward reading)."""
    assert dv.resolve_legacy_date("1054d", NOW) == TODAY + 1054 * dv.DAY
    assert dv.resolve_legacy_date("1054d", NOW) == ts(2029, 8, 30)
    assert dv.resolve_legacy_date("1y", NOW) == TODAY + 365 * dv.DAY
    assert dv.resolve_legacy_date("2Y", NOW) == TODAY + 730 * dv.DAY
    assert dv.resolve_legacy_date("1000d", NOW + 3 * dv.DAY) == (
        TODAY + 1003 * dv.DAY
    )


def test_resolve_legacy_date_dates_are_kept():
    """A date in the future is taken as it is (int, float or text)."""
    assert dv.resolve_legacy_date(OWNER, NOW) == OWNER
    assert dv.resolve_legacy_date(float(OWNER) + 0.5, NOW) == OWNER
    assert dv.resolve_legacy_date(str(OWNER), NOW) == OWNER


@pytest.mark.parametrize(
    "value",
    [None, "0d", "-5d", "36m", "abc", True, 800_000, ts(2026, 1, 1), NOW, TODAY],
)
def test_resolve_legacy_date_unusable(value):
    """Nothing usable: no value, a past date or today, a block height."""
    assert dv.resolve_legacy_date(value, NOW) is None


def test_validate_values_normalizes():
    """The one validator: an int date, the fee kept, the bridge as "-Nd"."""
    out = dv.validate_values(
        {
            "settings_version": 1,
            "locktime": str(OWNER),
            "baltx_fees": 13.001,
            "threshold": "-1y",
            "migrated_from": "will",
            "note_old": None,
            "choice_pending": 1,
        }
    )
    assert out == {
        "settings_version": 1,
        "locktime": OWNER,
        "baltx_fees": 13.001,
        "threshold": "-365d",
        "migrated_from": "will",
        "note_old": None,
        "choice_pending": 1,
    }
    assert dv.validate_values({"locktime": OWNER + 0.7})["locktime"] == OWNER


@pytest.mark.parametrize(
    "values, words",
    [
        ({"locktime": "1054d"}, "period"),
        ({"locktime": "1y"}, "period"),
        ({"locktime": "36m"}, "period"),
        ({"locktime": 800_000}, "not a delivery date"),
        ({"locktime": 86400}, "not a delivery date"),
        ({"locktime": True}, "not a delivery date"),
        ({"locktime": 2**32}, "not a delivery date"),
        ({"locktime": "29/08/2029"}, "not a delivery date"),
        ({"baltx_fees": 0}, "fee"),
        ({"baltx_fees": "13"}, "fee"),
        ({"baltx_fees": True}, "fee"),
        ({"threshold": "30d"}, "Check Alive"),
        ({"threshold": "-0d"}, "Check Alive"),
        ({"threshold": "-4000d"}, "Check Alive"),
        ({"threshold": OWNER}, "Check Alive"),
        ({"settings_version": 0}, "version"),
        ({"settings_version": True}, "version"),
        ({"settings_version": "1"}, "version"),
        ({"locktime": [OWNER]}, "plain value"),
        ({"note_kind": {"a": 1}}, "plain value"),
        ({"baltx_fees": float("nan")}, "plain value"),
    ],
)
def test_validate_values_refuses(values, words):
    """Periods, block heights, bad fees and nested values are refused."""
    with pytest.raises(ValueError, match=words):
        dv.validate_values(values)


def test_write_values_item_by_item_and_only_changes():
    """Writes go item by item; a second identical write writes nothing."""
    d = Recorder({"locktime": "1054d", "tx_fees": 13, "real_locktime": 5, "x": 1})
    values = dv.validate_values(current())
    dv.write_values(d, values, drop=dv.LEGACY_DROP)
    assert dict(d) == {**values, "x": 1}
    assert ("del", "tx_fees") in d.writes and ("del", "real_locktime") in d.writes
    d.writes.clear()
    dv.write_values(d, values, drop=dv.LEGACY_DROP)
    assert d.writes == []


def test_default_values():
    """Default settings are current and use the default date and fee."""
    values = dv.default_values(NOW)
    assert values == {
        "settings_version": 1,
        "locktime": ts(2027, 10, 11),
        "baltx_fees": 20,
        "threshold": "-180d",
    }
    assert dv.is_current(values)


# ------------------------------------------------------------------ item views


def test_item_view_reads_flags_and_real_heirs():
    """Missing flags are False; will-executor fee outputs are no heirs."""
    view = dv.item_view(
        "w1",
        OWNER,
        13,
        {"VALID": True, "PUSHED": 1},
        {
            "alice": ("alice-address", "50%", "1054d"),
            "bob": ["bob-address", "50%"],
            'w!ll3x3c"https://we-one.invalid"1': ["fee-address-one", 10000, OWNER],
        },
    )
    assert view.VALID and view.PUSHED
    assert not (view.COMPLETE or view.INVALIDATED or view.CONFIRMED)
    assert not (view.MEMPOOL or view.REPLACED)
    assert view.recipes == ("1054d",)
    assert (view.wid, view.locktime, view.fee) == ("w1", OWNER, 13)


def test_views_from_reads_willitem_like_objects():
    """``views_from`` takes ``{wid: WillItem}`` (duck-typed: the STATUS
    table of WillItem gives the flags) and sorts by wid."""
    status = {f: [f.title(), False] for f in dv.ITEM_FLAGS}
    status["VALID"] = ["Valid", True]
    status["COMPLETE"] = ["Signed", True]
    items = {}
    for n, (url, address) in enumerate(reversed(EXECUTORS)):
        items["w{}".format(2 - n)] = SimpleNamespace(
            tx=SimpleNamespace(locktime=OWNER),
            tx_fees=13,
            STATUS=copy.deepcopy(status),
            heirs={
                "alice": ["alice-address", "100%", OWNER],
                'w!ll3x3c"' + url + '"1': [address, 10000, OWNER],
            },
        )
    views = dv.views_from(items)
    assert [v.wid for v in views] == ["w0", "w1", "w2"]
    assert all(v.VALID and v.COMPLETE and not v.PUSHED for v in views)
    assert all(v.recipes == (OWNER,) and v.fee == 13 for v in views)
    assert dv.views_from({}) == [] and dv.views_from(None) == []


def test_non_date_items_lists_block_heights_and_garbage():
    """VALID or sent items whose locktime is not a date, for the log."""
    items = (
        will(800_000, tag="h", VALID=True)
        + will("junk", tag="g", PUSHED=True)
        + will(800_000, tag="r", REPLACED=True)
        + signed_will(OWNER)
    )
    assert dv.non_date_items(items) == ["g0", "g1", "g2", "h0", "h1", "h2"]


# ------------------------------------------------------------------ migration

OWNER_GLOBAL = {"locktime": "1054d", "baltx_fees": 20, "threshold": "-180d"}


def owner_items():
    """The owner's will: VALID 2029-08-29 signed and sent, plus the sent
    and REPLACED wills of 2029-08-30 and 2029-08-31."""
    return (
        signed_will(OWNER, tag="v")
        + will(ts(2029, 8, 30), tag="p", COMPLETE=True, PUSHED=True, REPLACED=True)
        + will(ts(2029, 8, 31), tag="q", COMPLETE=True, PUSHED=True, REPLACED=True)
    )


def test_owner_wallet_migrates_to_the_date_of_his_will():
    """The owner's case: 2029-08-29 00:00 UTC, fee 13, from the will, note
    "converted", the old "1054d" kept for diagnosis, the global untouched."""
    glob = copy.deepcopy(OWNER_GLOBAL)
    out = dv.migration_values(owner_items(), glob, {}, True, NOW)
    assert out == {
        "settings_version": 1,
        "locktime": 1882656000,
        "baltx_fees": 13,
        "threshold": "-180d",
        "migrated_from": "will",
        "migrated_at": NOW,
        "legacy_locktime": "1054d",
        "note_kind": "converted",
        "note_new": 1882656000,
    }
    assert glob == OWNER_GLOBAL
    assert dv.is_current(out)
    assert dv.CHOICE_KEY not in out


@pytest.mark.parametrize("days", [0, 1, 2, 7])
def test_owner_date_does_not_move_with_the_clock(days):
    """Migrated on +0, +1, +2 or +7 days: always the will's own date (the
    old "1054d" moved one day per day: the day +2 paid invalidation)."""
    now = NOW + days * dv.DAY
    out = dv.migration_values(owner_items(), OWNER_GLOBAL, None, True, now)
    assert (out["locktime"], out["baltx_fees"], out["note_kind"]) == (
        OWNER,
        13,
        "converted",
    )
    assert out["migrated_at"] == now


def test_owner_case_with_an_absolute_recipe_is_also_converted():
    """A period against an absolute recipe is drift, not a wish: converted."""
    items = signed_will(OWNER, recipe=OWNER)
    out = dv.migration_values(items, OWNER_GLOBAL, None, True, NOW)
    assert out["note_kind"] == "converted" and dv.CHOICE_KEY not in out


def test_valid_wins_over_an_earlier_sent_will():
    """No "min(VALID, sent)": an earlier sent will must still read as a
    postpone of the VALID one (#69/#72)."""
    items = signed_will(OWNER, tag="v") + will(
        ts(2029, 8, 1), tag="s", COMPLETE=True, PUSHED=True, REPLACED=True
    )
    out = dv.migration_values(items, OWNER_GLOBAL, None, True, NOW)
    assert (out["locktime"], out["migrated_from"]) == (OWNER, "will")


def test_sent_will_without_valid_items():
    """No VALID item: the earliest sent item still pending, source "sent".
    Invalidated, confirmed, in the mempool or past items do not count."""
    items = (
        will(ts(2029, 9, 3), tag="b", COMPLETE=True, PUSHED=True, REPLACED=True)
        + will(ts(2029, 9, 1), tag="a", COMPLETE=True, PUSHED=True, REPLACED=True)
        + will(ts(2029, 8, 1), tag="i", PUSHED=True, INVALIDATED=True)
        + will(ts(2029, 8, 2), tag="c", PUSHED=True, CONFIRMED=True)
        + will(ts(2029, 8, 3), tag="m", PUSHED=True, MEMPOOL=True)
        + will(ts(2026, 10, 1), tag="o", PUSHED=True, REPLACED=True)
        + will(ts(2029, 7, 1), tag="n", REPLACED=True)
    )
    out = dv.migration_values(items, OWNER_GLOBAL, None, True, NOW)
    assert (out["locktime"], out["migrated_from"], out["baltx_fees"]) == (
        ts(2029, 9, 1),
        "sent",
        13,
    )
    # A period with no VALID item has no drift-free wish: converted.
    assert out["note_kind"] == "converted"


def test_will_in_force_falls_back_to_the_pending_sent_items():
    """``earliest_in_force``: the earliest VALID item; with none, the
    earliest sent item still pending (the selection of the source "sent");
    invalidated, confirmed, in the mempool or past items never count."""
    sent = (
        will(ts(2029, 9, 3), tag="b", COMPLETE=True, PUSHED=True, REPLACED=True)
        + will(ts(2029, 9, 1), tag="a", COMPLETE=True, PUSHED=True, REPLACED=True)
        + will(ts(2029, 8, 1), tag="i", PUSHED=True, INVALIDATED=True)
        + will(ts(2029, 8, 2), tag="c", PUSHED=True, CONFIRMED=True)
        + will(ts(2029, 8, 3), tag="m", PUSHED=True, MEMPOOL=True)
        + will(ts(2026, 10, 1), tag="o", PUSHED=True, REPLACED=True)
        + will(ts(2029, 7, 1), tag="n", REPLACED=True)
    )
    assert dv.earliest_valid(sent) is None
    assert sorted(i.wid for i in dv.pending_sent(sent, NOW)) == [
        "a0", "a1", "a2", "b0", "b1", "b2"]
    assert dv.earliest_in_force(sent, NOW) == ts(2029, 9, 1)
    # A VALID item wins, even a later one than the sent items.
    items = sent + signed_will(ts(2029, 9, 20), tag="v")
    assert dv.earliest_in_force(items, NOW) == ts(2029, 9, 20)
    # Nothing VALID and nothing pending: no will in force.
    assert dv.earliest_in_force(sent, ts(2029, 9, 4)) is None
    assert dv.earliest_in_force([], NOW) is None


def test_block_height_will_falls_through():
    """A VALID block height is no date: the next source is used."""
    items = will(800_000, tag="h", VALID=True, COMPLETE=True, PUSHED=True)
    out = dv.migration_values(items, OWNER_GLOBAL, None, True, NOW)
    assert (out["locktime"], out["migrated_from"]) == (ts(2029, 8, 30), "global")
    assert dv.non_date_items(items) == ["h0", "h1", "h2"]


def test_global_period_without_a_will():
    """Heirs but no will: the shared period counts from today."""
    glob = {"locktime": "1054d", "baltx_fees": 13, "threshold": "-90d"}
    out = dv.migration_values([], glob, None, True, NOW)
    assert out == {
        "settings_version": 1,
        "locktime": ts(2029, 8, 30),
        "baltx_fees": 13,
        "threshold": "-90d",
        "migrated_from": "global",
        "migrated_at": NOW,
        "legacy_locktime": "1054d",
        "note_kind": "converted",
        "note_new": ts(2029, 8, 30),
    }


def test_global_date_without_a_will():
    """A shared date in the future is used as it is; no note (same date)."""
    glob = {"locktime": float(ts(2030, 1, 1)), "baltx_fees": 15}
    out = dv.migration_values([], glob, None, True, NOW)
    assert (out["locktime"], out["migrated_from"], out["baltx_fees"]) == (
        ts(2030, 1, 1),
        "global",
        15,
    )
    assert out["legacy_locktime"] == float(ts(2030, 1, 1))
    assert "note_kind" not in out


def test_past_global_date_falls_to_the_legacy_wallet_value():
    """A past shared date is unusable: the BAL 0.2.x wallet value is next."""
    glob = {"locktime": ts(2026, 1, 1)}
    legacy = {"locktime": ts(2031, 5, 5), "baltx_fees": 9}
    out = dv.migration_values([], glob, legacy, True, NOW)
    assert (out["locktime"], out["migrated_from"], out["baltx_fees"]) == (
        ts(2031, 5, 5),
        "legacy_wallet",
        9,
    )
    # The old value as found is the shared one; past, so no note.
    assert out["legacy_locktime"] == ts(2026, 1, 1)
    assert "note_kind" not in out


def test_legacy_wallet_value_only():
    """A BAL 0.2.x wallet dict ("2y", tx_fees only) and no global."""
    legacy = {"locktime": "2y", "tx_fees": 7, "threshold": "-30d"}
    out = dv.migration_values([], None, legacy, True, NOW)
    assert (out["locktime"], out["migrated_from"], out["baltx_fees"]) == (
        TODAY + 730 * dv.DAY,
        "legacy_wallet",
        7,
    )
    assert out["threshold"] == "-30d"
    assert out["note_kind"] == "converted"


def test_no_usable_source_gives_the_default():
    """Plugin data but nothing usable: today + 12 months, fee 20."""
    out = dv.migration_values([], {"locktime": "0d"}, {"locktime": "abc"}, True, NOW)
    assert (out["locktime"], out["migrated_from"], out["baltx_fees"]) == (
        ts(2027, 10, 11),
        "default",
        20,
    )


def test_new_wallet_never_inherits_the_shared_value():
    """No heirs and no will: the default, never the frozen shared value.
    The Check Alive bridge is a plugin-wide habit and is still taken."""
    glob = {"locktime": "1054d", "baltx_fees": 13, "threshold": "-90d"}
    out = dv.migration_values([], glob, {"locktime": "5y"}, False, NOW)
    assert out == {
        "settings_version": 1,
        "locktime": ts(2027, 10, 11),
        "baltx_fees": 20,
        "threshold": "-90d",
        "migrated_from": "default",
        "migrated_at": NOW,
    }


def test_migration_never_changes_its_inputs():
    """The global and the legacy dict are only read (invariant I1)."""
    glob = {"locktime": "1010d", "baltx_fees": [1], "threshold": "-1y"}
    legacy = {"locktime": "2y", "tx_fees": 7, "real_locktime": 3}
    before = copy.deepcopy((glob, legacy))
    dv.migration_values(owner_items(), glob, legacy, True, NOW)
    dv.migration_values([], glob, legacy, True, NOW)
    assert (glob, legacy) == before


def test_wallet_dict_already_migrated_is_read_first():
    """A migrated dict broken by another program is newer than the frozen
    global: its own values come first."""
    glob = {"locktime": "1000d", "baltx_fees": 20, "threshold": "-180d"}
    own = {"settings_version": 1, "locktime": "1054d", "baltx_fees": 15,
           "threshold": "-90d"}
    out = dv.migration_values([], glob, own, True, NOW)
    assert (out["locktime"], out["migrated_from"]) == (ts(2029, 8, 30), "legacy_wallet")
    assert (out["baltx_fees"], out["threshold"]) == (15, "-90d")
    assert out["legacy_locktime"] == "1054d"


# ------------------------------------------------------------------ fees


def test_fee_zero_is_never_used():
    """Items with fee 0 (a legacy item read before the fix) are ignored."""
    items = signed_will(OWNER, fee=0)
    out = dv.migration_values(items, OWNER_GLOBAL, None, True, NOW)
    assert out["baltx_fees"] == 20
    out = dv.migration_values(items, {"locktime": "1054d"}, None, True, NOW)
    assert out["baltx_fees"] == 20
    mixed = signed_will(OWNER, fee=0, tag="z") + signed_will(OWNER, fee=13)
    assert dv.migration_values(mixed, OWNER_GLOBAL, None, True, NOW)["baltx_fees"] == 13


def test_legacy_items_fixed_from_tx_fees_give_their_fee():
    """After ``Util.fix_will_tx_fees`` a tx_fees-only item carries fee 13."""
    items = signed_will(OWNER, fee=13)
    out = dv.migration_values(items, {"locktime": "1054d"}, None, True, NOW)
    assert out["baltx_fees"] == 13


def test_fee_is_kept_exactly():
    """13.001 stays 13.001: rounding would read as a fee change."""
    out = dv.migration_values(signed_will(OWNER, fee=13.001), None, None, True, NOW)
    assert out["baltx_fees"] == 13.001


def test_disagreeing_item_fees_use_the_old_settings():
    """Two fees in the will: the old fee (baltx_fees, then tx_fees), else 20."""
    items = signed_will(OWNER, fee=13, tag="a") + signed_will(OWNER, fee=15, tag="b")
    assert dv.migration_values(items, {"baltx_fees": 9}, None, True, NOW)["baltx_fees"] == 9
    assert dv.migration_values(items, {"tx_fees": 8}, None, True, NOW)["baltx_fees"] == 8
    assert dv.migration_values(items, None, {"tx_fees": 7}, True, NOW)["baltx_fees"] == 7
    assert dv.migration_values(items, {"baltx_fees": "9"}, None, True, NOW)["baltx_fees"] == 20
    assert dv.migration_values(items, {"baltx_fees": 9}, None, False, NOW)["baltx_fees"] == 20


def test_check_alive_bridge():
    """The first "-Nd" of the old dicts, as "-Nd"; else an old fixed date or
    positive period converted (see test_core_delivery_check_alive.py); else
    "-180d"."""
    def bridge(glob, legacy=None):
        """The migrated Check Alive bridge for these old dicts."""
        return dv.migration_values([], glob, legacy, True, NOW)["threshold"]

    assert bridge({"threshold": "-1y"}) == "-365d"
    assert bridge({"threshold": "30d"}, {"threshold": "-90d"}) == "-90d"
    assert bridge({"threshold": float(OWNER)}) == "-180d"
    assert bridge(None) == "-180d"


def test_every_migration_result_is_current():
    """Whatever the source, the result passes ``is_current`` (rule I2)."""
    cases = [
        (owner_items(), OWNER_GLOBAL, None, True),
        (will(ts(2029, 9, 1), PUSHED=True, REPLACED=True), OWNER_GLOBAL, None, True),
        ([], OWNER_GLOBAL, None, True),
        ([], None, {"locktime": "2y"}, True),
        ([], None, None, True),
        ([], OWNER_GLOBAL, None, False),
    ]
    for items, glob, legacy, plugin_data in cases:
        assert dv.is_current(dv.migration_values(items, glob, legacy, plugin_data, NOW))


# ------------------------------------------------------------------ notes

EDGE = ts(2029, 7, 5)


def test_later_pending_with_an_unsigned_will_asks():
    """The PROVEN edge case: "1010d" pending against the VALID "1000d"
    recipe of an UNSIGNED will. Both dates kept, and the question is set."""
    items = will(EDGE, recipe="1000d", VALID=True)
    out = dv.migration_values(items, {"locktime": "1010d"}, None, True, NOW)
    assert out["locktime"] == EDGE
    assert (out["note_kind"], out["note_old"], out["note_new"]) == (
        "later_pending",
        EDGE + 10 * dv.DAY,
        EDGE,
    )
    assert out["choice_pending"] == 1


def test_later_pending_with_a_signed_and_sent_will_only_informs():
    """Signed and sent: the two-date note, no question."""
    items = signed_will(EDGE, recipe="1000d")
    out = dv.migration_values(items, {"locktime": "1010d"}, None, True, NOW)
    assert out["note_kind"] == "later_pending" and "choice_pending" not in out


def test_later_pending_asks_when_one_copy_is_signed_but_not_sent():
    """Every earliest VALID copy must be COMPLETE and PUSHED."""
    items = signed_will(EDGE, recipe="1000d", tag="a") + will(
        EDGE, recipe="1000d", tag="b", VALID=True, COMPLETE=True
    )
    out = dv.migration_values(items, {"locktime": "1010d"}, None, True, NOW)
    assert out["choice_pending"] == 1


def test_later_pending_looks_only_at_the_earliest_items():
    """An unsigned copy dated later than the will does not ask."""
    items = signed_will(EDGE, recipe="1000d", tag="a") + will(
        EDGE + dv.DAY, recipe="1000d", tag="b", VALID=True
    )
    out = dv.migration_values(items, {"locktime": "1010d"}, None, True, NOW)
    assert out["note_kind"] == "later_pending" and "choice_pending" not in out


def test_one_heir_dated_later_is_enough():
    """Several period recipes: the shortest one decides (one later heir is
    a postpone, as in ``Will._postponed_heir``)."""
    items = [
        dv.item_view(
            "x{}".format(n),
            EDGE,
            13,
            {"VALID": True},
            {"alice": ["a", "50%", "1005d"], "bob": ["b", "50%", "1000d"]},
        )
        for n in range(3)
    ]
    out = dv.migration_values(items, {"locktime": "1003d"}, None, True, NOW)
    assert (out["note_kind"], out["note_old"]) == ("later_pending", EDGE + 3 * dv.DAY)


def test_the_earliest_item_with_a_period_recipe_is_used():
    """The earliest VALID item has an absolute recipe: the next one, with a
    period recipe, gives the drift-free wish."""
    items = signed_will(EDGE, recipe=EDGE, tag="a") + signed_will(
        EDGE + 2 * dv.DAY, recipe="1000d", tag="b"
    )
    out = dv.migration_values(items, {"locktime": "1001d"}, None, True, NOW)
    assert (out["note_kind"], out["note_old"], out["note_new"]) == (
        "later_pending",
        EDGE + 3 * dv.DAY,
        EDGE,
    )


def test_an_earlier_period_is_only_converted():
    """A period giving an EARLIER date is no pending postpone."""
    items = signed_will(EDGE, recipe="1000d")
    out = dv.migration_values(items, {"locktime": "990d"}, None, True, NOW)
    assert out["note_kind"] == "converted" and "note_old" not in out


def test_an_old_later_date_is_a_pending_postpone():
    """A shared DATE later than the will: later_pending too."""
    later = ts(2029, 9, 1)
    out = dv.migration_values(signed_will(OWNER), {"locktime": later}, None, True, NOW)
    assert (out["note_kind"], out["note_old"], out["note_new"]) == (
        "later_pending",
        later,
        OWNER,
    )
    assert "choice_pending" not in out


def test_date_differs():
    """A shared DATE at least one day earlier than the will: informative."""
    earlier = ts(2029, 8, 1)
    out = dv.migration_values(signed_will(OWNER), {"locktime": earlier}, None, True, NOW)
    assert (out["note_kind"], out["note_old"], out["note_new"]) == (
        "date_differs",
        earlier,
        OWNER,
    )
    assert out["locktime"] == OWNER


def test_date_differs_for_a_sent_will():
    """With source "sent" a later shared date is only informative."""
    sent = ts(2029, 9, 1)
    later = ts(2029, 10, 1)
    items = will(sent, COMPLETE=True, PUSHED=True, REPLACED=True)
    out = dv.migration_values(items, {"locktime": later}, None, True, NOW)
    assert (out["migrated_from"], out["note_kind"], out["note_old"]) == (
        "sent",
        "date_differs",
        later,
    )
    assert "choice_pending" not in out


def test_no_note_for_the_same_date_or_a_past_one():
    """Less than one day apart, or an old date already past: no note."""
    out = dv.migration_values(
        signed_will(OWNER), {"locktime": OWNER + 3600}, None, True, NOW
    )
    assert "note_kind" not in out
    out = dv.migration_values(
        signed_will(OWNER), {"locktime": ts(2026, 1, 1)}, None, True, NOW
    )
    assert "note_kind" not in out
    out = dv.migration_values(signed_will(OWNER), None, None, True, NOW)
    assert "note_kind" not in out and "legacy_locktime" not in out


def test_note_for_direct_calls():
    """``note_for`` alone: no old value, no note; garbage, no note."""
    valid = signed_will(OWNER)
    assert dv.note_for("will", OWNER, None, valid, NOW) == {}
    assert dv.note_for("will", OWNER, "abc", valid, NOW) == {}
    assert dv.note_for("default", OWNER, "1054d", [], NOW) == {
        "note_kind": "converted",
        "note_new": OWNER,
    }
    # A VALID block height among the items is skipped, not a crash.
    mixed = will(800_000, tag="h", VALID=True, recipe="1000d") + valid
    assert dv.note_for("will", OWNER, "1054d", mixed, NOW)["note_kind"] == "converted"


def test_note_lifecycle():
    """Shown once, then deleted; the question keeps its two dates."""
    d = Recorder(current())
    assert dv.pending_note(d) is None
    dv.set_note(d, dv.NOTE_CONVERTED, new=OWNER)
    assert dv.pending_note(d) == ("converted", None, OWNER)
    dv.mark_note_shown(d)
    assert dv.pending_note(d) is None
    assert not any(k in d for k in dv.NOTE_KEYS)

    later = OWNER + 10 * dv.DAY
    dv.set_note(d, dv.NOTE_LATER_PENDING, old=later, new=OWNER)
    d[dv.CHOICE_KEY] = 1
    assert dv.choice_dates(d) == (OWNER, later)
    dv.mark_note_shown(d)
    assert dv.pending_note(d) is None
    assert dv.choice_dates(d) == (OWNER, later)
    dv.clear_choice(d)
    assert dv.choice_dates(d) is None
    assert not any(k in d for k in dv.NOTE_KEYS + (dv.CHOICE_KEY,))
    assert dv.choice_dates({}) is None and dv.pending_note(None) is None


# ------------------------------------------------------------------ holds


def test_holds():
    """Unavailable settings win; a pending question holds; plain dicts never."""
    dv.check_holds({"locktime": OWNER})
    dv.check_holds(current())
    dv.check_holds(None)
    with pytest.raises(dv.DeliveryChoicePending) as e:
        dv.check_holds(current(choice_pending=1))
    assert e.value.code == "delivery_choice_pending" and str(e.value)
    with pytest.raises(dv.DeliverySettingsUnavailable) as e:
        dv.check_holds(current(choice_pending=1), unavailable=True)
    assert e.value.code == "delivery_settings_unavailable" and str(e.value)
    assert issubclass(dv.DeliveryChoicePending, dv.DeliveryHold)
    assert issubclass(dv.DeliverySettingsUnavailable, dv.DeliveryHold)


def test_display_only_values():
    """Fail closed: the will's date and fee, never a period, never current."""
    out = dv.display_only_values(owner_items(), NOW)
    assert out == {"locktime": OWNER, "baltx_fees": 13, "threshold": "-180d"}
    assert not dv.is_current(out)
    out = dv.display_only_values([], NOW)
    assert out == {"locktime": ts(2027, 10, 11), "baltx_fees": 20, "threshold": "-180d"}
    out = dv.display_only_values(will(800_000, VALID=True, fee=0), NOW)
    assert out["locktime"] == ts(2027, 10, 11) and out["baltx_fees"] == 20


# ------------------------------------------------------------------ anchor


def anchored(items, locktime=OWNER):
    """Current settings anchored on ``items`` (what Safe21 saved)."""
    d = Recorder(current(locktime=locktime))
    assert dv.check_anchor(d, items) == dv.ANCHOR_FIRST
    d.writes.clear()
    return d


def test_fingerprint():
    """16 hex characters, independent of the order, different per set."""
    a = dv.fingerprint(["b", "a", "c"])
    assert re.fullmatch(r"[0-9a-f]{16}", a)
    assert a == dv.fingerprint(["c", "b", "a"])
    assert a != dv.fingerprint(["a", "b"])
    items = owner_items()
    assert dv.valid_ids(items) == ["v0", "v1", "v2"]
    assert dv.earliest_valid(items) == OWNER
    assert dv.earliest_valid(will(OWNER, REPLACED=True)) is None


def test_anchor_first_load_records_it():
    """No anchor yet: recorded, nothing else changes."""
    d = Recorder(current())
    assert dv.check_anchor(d, owner_items()) == dv.ANCHOR_FIRST
    assert d["anchor_valid"] == dv.fingerprint(["v0", "v1", "v2"])
    assert d["anchor_locktime"] == OWNER
    assert set(d.writes) == {("set", "anchor_valid"), ("set", "anchor_locktime")}


def test_anchor_unchanged_writes_nothing():
    """The same will on every load: no write at all (idempotent)."""
    d = anchored(owner_items())
    for _ in range(3):
        assert dv.check_anchor(d, owner_items()) == dv.ANCHOR_SAME
    assert d.writes == []


def test_anchor_after_safe21_own_write():
    """Safe21 saved a new will and recorded it: the next load is the same."""
    d = anchored(owner_items())
    new = signed_will(OWNER, tag="n")
    dv.record_will_anchor(d, new)
    d.writes.clear()
    assert dv.check_anchor(d, new) == dv.ANCHOR_SAME
    assert d.writes == [] and d["locktime"] == OWNER


def test_anchor_outside_postpone_is_followed():
    """Another program postponed the will to 08-31: the date follows it,
    never reverted, and the note keeps both dates."""
    d = anchored(owner_items())
    later = ts(2029, 8, 31)
    outside = signed_will(later, tag="o")
    assert dv.check_anchor(d, outside) == dv.NOTE_OUTSIDE_CHANGE
    assert d["locktime"] == later
    assert dv.pending_note(d) == ("outside_change", OWNER, later)
    assert d["anchor_valid"] == dv.fingerprint(["o0", "o1", "o2"])
    assert d["anchor_locktime"] == later
    d.writes.clear()
    assert dv.check_anchor(d, outside) == dv.ANCHOR_SAME
    assert d.writes == []


def test_anchor_outside_anticipation_is_followed():
    """Another program moved the will EARLIER: followed too (no paid
    invalidation to get back to the stored date)."""
    d = anchored(owner_items())
    earlier = ts(2029, 8, 27)
    assert dv.check_anchor(d, signed_will(earlier, tag="o")) == dv.NOTE_OUTSIDE_CHANGE
    assert d["locktime"] == earlier
    assert dv.pending_note(d) == ("outside_change", OWNER, earlier)


def test_anchor_without_valid_items():
    """The will was invalidated elsewhere: re-anchored, the date kept."""
    d = anchored(owner_items())
    gone = will(OWNER, tag="v", INVALIDATED=True, PUSHED=True)
    assert dv.check_anchor(d, gone) == dv.ANCHOR_MOVED
    assert d["locktime"] == OWNER
    assert "anchor_locktime" not in d and dv.pending_note(d) is None
    assert d["anchor_valid"] == dv.fingerprint([])


def test_anchor_new_items_with_the_same_date():
    """Another VALID set at the stored date: re-anchored, no note."""
    d = anchored(owner_items())
    assert dv.check_anchor(d, signed_will(OWNER, tag="o")) == dv.ANCHOR_MOVED
    assert d["locktime"] == OWNER and dv.pending_note(d) is None


def test_anchor_outside_change_drops_the_question():
    """The will the question was about was replaced elsewhere."""
    d = anchored(will(EDGE, recipe="1000d", VALID=True), locktime=EDGE)
    dv.set_note(d, dv.NOTE_LATER_PENDING, old=EDGE + 10 * dv.DAY, new=EDGE)
    d[dv.CHOICE_KEY] = 1
    later = EDGE + 10 * dv.DAY
    assert dv.check_anchor(d, signed_will(later, tag="o")) == dv.NOTE_OUTSIDE_CHANGE
    assert dv.CHOICE_KEY not in d
    assert dv.pending_note(d) == ("outside_change", EDGE, later)
    dv.check_holds(d)


def test_anchor_ignores_block_heights():
    """Only date locktimes count for the earliest VALID date."""
    d = anchored(owner_items())
    later = ts(2029, 8, 31)
    outside = will(800_000, tag="h", VALID=True) + signed_will(later, tag="o")
    assert dv.check_anchor(d, outside) == dv.NOTE_OUTSIDE_CHANGE
    assert d["locktime"] == later


# ------------------------------------------- funds safety (#123 review)

DRAFT = ts(2029, 8, 29)
IN_FORCE = ts(2030, 8, 29)


def test_anchor_does_not_follow_unsigned_drafts_of_another_program():
    """FS-1 (migration lens), the reviewer's probe: the signed will in force
    is dated 2030-08-29. Another program read the frozen shared date,
    prepared an EARLIER will (2029-08-29) and its "Sign your will" was
    cancelled: three unsigned VALID drafts, the signed will REPLACED (the
    will-executors still hold it). The drafts were never sent: the stored
    date stays, no note calls them "the will in force", only the anchor is
    recorded again (before the fix the date became 2029-08-29 with the note
    "outside_change", and the next Check signed and sent that will)."""
    d = anchored(signed_will(IN_FORCE, tag="s"), locktime=IN_FORCE)
    after = will(IN_FORCE, tag="s", COMPLETE=True, PUSHED=True, REPLACED=True)
    after += will(DRAFT, tag="u", VALID=True)
    assert dv.check_anchor(d, after) == dv.ANCHOR_UNSIGNED
    assert d["locktime"] == IN_FORCE
    assert dv.pending_note(d) is None
    assert d["anchor_valid"] == dv.fingerprint(["u0", "u1", "u2"])
    assert d["anchor_locktime"] == DRAFT
    assert set(d.writes) == {("set", "anchor_valid"), ("set", "anchor_locktime")}
    d.writes.clear()
    assert dv.check_anchor(d, after) == dv.ANCHOR_SAME
    assert d.writes == []


def test_anchor_unsigned_drafts_keep_the_date_in_every_shape():
    """No signed VALID item at all (even LATER drafts), or an unsigned
    earliest draft before the stored date next to a signed will: the
    stored date and the question stay, no note. ``earliest_signed_valid``
    reads only signed (COMPLETE) VALID items with a date."""
    later_drafts = will(ts(2031, 1, 1), tag="u", VALID=True)
    assert dv.earliest_signed_valid(later_drafts) is None
    d = anchored(signed_will(IN_FORCE, tag="s"), locktime=IN_FORCE)
    assert dv.check_anchor(d, later_drafts) == dv.ANCHOR_UNSIGNED
    assert d["locktime"] == IN_FORCE and dv.pending_note(d) is None

    mixed = signed_will(IN_FORCE, tag="s") + will(DRAFT, tag="u", VALID=True)
    assert dv.earliest_valid(mixed) == DRAFT
    assert dv.earliest_signed_valid(mixed) == IN_FORCE
    d = anchored(signed_will(IN_FORCE, tag="t"), locktime=IN_FORCE)
    d[dv.CHOICE_KEY] = 1
    assert dv.check_anchor(d, mixed) == dv.ANCHOR_UNSIGNED
    assert d["locktime"] == IN_FORCE and dv.pending_note(d) is None
    assert d[dv.CHOICE_KEY] == 1
    # A signed copy at the earliest date: that date is signed, followed.
    signed_too = will(DRAFT, tag="u", VALID=True) + signed_will(DRAFT, tag="c")
    assert dv.earliest_signed_valid(signed_too) == DRAFT
    d = anchored(signed_will(IN_FORCE, tag="t"), locktime=IN_FORCE)
    assert dv.check_anchor(d, signed_too) == dv.NOTE_OUTSIDE_CHANGE
    assert d["locktime"] == DRAFT


def test_anchor_follows_a_signed_outside_will_not_its_later_draft():
    """A signed outside postpone is still followed (both directions are
    covered above). An unsigned draft between the stored date and the
    signed will is not earlier than the stored date: the signed will is
    followed; the later wish of the migration is dropped with the
    question, because the will they were about has been replaced."""
    d = anchored(signed_will(IN_FORCE, tag="s"), locktime=IN_FORCE)
    d[dv.LATER_WISH_KEY] = IN_FORCE + 30 * dv.DAY
    signed_later = ts(2030, 12, 1)
    outside = signed_will(signed_later, tag="o") + will(
        IN_FORCE + 5 * dv.DAY, tag="u", VALID=True
    )
    assert dv.check_anchor(d, outside) == dv.NOTE_OUTSIDE_CHANGE
    assert d["locktime"] == signed_later
    assert dv.pending_note(d) == ("outside_change", IN_FORCE, signed_later)
    assert dv.LATER_WISH_KEY not in d


def test_the_later_wish_is_kept_with_a_pending_postpone():
    """FS-2: a signed and sent will at the migrated date and an old later
    DATE. The will may already be invalidated by the paid postpone of the
    old code (the wallet had not seen it): the later date is kept as
    ``later_wish`` next to the note. With an unsigned will, the question
    too."""
    later = ts(2029, 9, 30)
    out = dv.migration_values(signed_will(OWNER), {"locktime": later}, None, True, NOW)
    assert (out["note_kind"], out["note_old"], out["note_new"]) == (
        "later_pending", later, OWNER)
    assert out[dv.LATER_WISH_KEY] == later and "choice_pending" not in out
    assert dv.is_current(out)
    out = dv.migration_values(
        will(EDGE, recipe="1000d", VALID=True), {"locktime": "1010d"}, None, True, NOW
    )
    assert out["choice_pending"] == 1
    assert out[dv.LATER_WISH_KEY] == EDGE + 10 * dv.DAY


def test_the_later_wish_of_a_sent_will():
    """Source "sent" (no VALID item): an old DATE at least one day later
    is kept as the later wish next to the informative note; an earlier
    one, a period, or no date difference give none."""
    sent = ts(2029, 9, 1)
    later = ts(2029, 10, 1)
    items = will(sent, COMPLETE=True, PUSHED=True, REPLACED=True)
    out = dv.migration_values(items, {"locktime": later}, None, True, NOW)
    assert (out["migrated_from"], out["note_kind"], out[dv.LATER_WISH_KEY]) == (
        "sent", "date_differs", later)
    out = dv.migration_values(items, {"locktime": ts(2029, 8, 1)}, None, True, NOW)
    assert out["note_kind"] == "date_differs" and dv.LATER_WISH_KEY not in out
    out = dv.migration_values(items, {"locktime": "1054d"}, None, True, NOW)
    assert out["note_kind"] == "converted" and dv.LATER_WISH_KEY not in out
    out = dv.migration_values(items, {"locktime": sent + 3600}, None, True, NOW)
    assert "note_kind" not in out and dv.LATER_WISH_KEY not in out


def test_no_later_wish_without_a_later_date():
    """An earlier old date, a converted period or the owner's case: no
    later wish."""
    out = dv.migration_values(signed_will(OWNER), {"locktime": ts(2029, 8, 1)},
                              None, True, NOW)
    assert out["note_kind"] == "date_differs" and dv.LATER_WISH_KEY not in out
    out = dv.migration_values(owner_items(), OWNER_GLOBAL, {}, True, NOW)
    assert out["note_kind"] == "converted" and dv.LATER_WISH_KEY not in out


def test_multiverse_never_gives_a_pending_postpone():
    """The reviewer's probe: with multiverse each heir carries its own
    period ("1000d", "1100d"), the shared "1054d" never drove them. No
    "later_pending", no question, no later wish: only the informative
    note. Without multiverse the same items give the pending postpone."""
    shared = {"locktime": "1054d", "baltx_fees": 13, "threshold": "-180d"}
    first = ts(2029, 7, 6)

    def heirs_items(**flags):
        """Two items, each heir with its own period."""
        return [
            dv.item_view("h1", first, 13, flags, {"alice": ["a", "50%", "1000d"]}),
            dv.item_view("h2", ts(2029, 10, 14), 13, flags, {"bob": ["b", "50%", "1100d"]}),
        ]

    for flags in ({"VALID": True, "COMPLETE": True, "PUSHED": True}, {"VALID": True}):
        out = dv.migration_values(heirs_items(**flags), shared, {}, True, NOW,
                                  multiverse=True)
        assert out["locktime"] == first
        assert out["note_kind"] == "converted"
        for key in ("note_old", "choice_pending", dv.LATER_WISH_KEY):
            assert key not in out, key
        plain = dv.migration_values(heirs_items(**flags), shared, {}, True, NOW)
        assert plain["note_kind"] == "later_pending"
    later = ts(2029, 9, 30)
    out = dv.migration_values(signed_will(OWNER), {"locktime": later}, None, True, NOW,
                              multiverse=True)
    assert (out["note_kind"], out["note_old"]) == ("date_differs", later)
    assert dv.LATER_WISH_KEY not in out
    assert dv.note_for("will", OWNER, later, signed_will(OWNER), NOW,
                       multiverse=True) == {
        "note_kind": "date_differs", "note_old": later, "note_new": OWNER}


def test_later_wish_action():
    """Applied once no VALID item is left (the will found at the migration
    was invalidated); dropped once the wallet is synchronized and a VALID
    item is still there (the postpone had been cancelled); kept while not
    synchronized; dropped when it is not a date later than the stored one.
    Nothing is written."""
    wish = OWNER + 365 * dv.DAY
    d = current(later_wish=wish)
    gone = will(OWNER, tag="v", COMPLETE=True, PUSHED=True, INVALIDATED=True)
    in_force = signed_will(OWNER, tag="v")
    assert dv.later_wish_action(d, gone, synced=False) == dv.WISH_APPLY
    assert dv.later_wish_action(d, gone, synced=True) == dv.WISH_APPLY
    assert dv.later_wish_action(d, [], synced=False) == dv.WISH_APPLY
    assert dv.later_wish_action(d, in_force, synced=False) == dv.WISH_KEEP
    assert dv.later_wish_action(d, in_force, synced=True) == dv.WISH_DROP
    assert d == current(later_wish=wish)
    for bad in (OWNER, OWNER - dv.DAY, "1054d", "garbage", None):
        stale = dict(current(), later_wish=bad)
        assert dv.later_wish_action(stale, gone, synced=False) == dv.WISH_DROP
    assert dv.later_wish_action(current(), gone, synced=False) is None
    assert dv.later_wish_action(None, gone, synced=False) is None


def test_the_later_wish_is_not_part_of_the_note():
    """Showing the note never deletes the later wish; ``drop_later_wish``
    does, item by item; ``validate_values`` stores it as an int date."""
    d = Recorder(current(later_wish=OWNER + dv.DAY))
    dv.set_note(d, dv.NOTE_LATER_PENDING, old=OWNER + dv.DAY, new=OWNER)
    dv.mark_note_shown(d)
    assert dv.pending_note(d) is None and d[dv.LATER_WISH_KEY] == OWNER + dv.DAY
    d.writes.clear()
    dv.drop_later_wish(d)
    dv.drop_later_wish(d)
    assert d.writes == [("del", dv.LATER_WISH_KEY)]
    assert dv.validate_values({dv.LATER_WISH_KEY: "1882656000.0"}) == {
        dv.LATER_WISH_KEY: OWNER}
    for bad in ("1054d", "garbage", 800_000, True):
        with pytest.raises(ValueError):
            dv.validate_values({dv.LATER_WISH_KEY: bad})


# ------------------------------------------------------------------ command line


@pytest.mark.parametrize(
    "text, expected",
    [
        ("2029-08-29", OWNER),
        (" 2029-08-29 ", OWNER),
        ("1882656000", OWNER),
        (1882656000, OWNER),
        (1882656000.0, OWNER),
        ("2026-10-12", ts(2026, 10, 12)),
        (dv.LOCKTIME_MAX, dv.LOCKTIME_MAX),
    ],
)
def test_parse_cli_date(text, expected):
    """YYYY-MM-DD is 00:00 UTC of that day; a timestamp is kept."""
    assert dv.parse_cli_date(text, NOW) == expected


@pytest.mark.parametrize(
    "text, words",
    [
        ("1054d", "period"),
        ("1y", "period"),
        ("36m", "period"),
        ("-180d", "period"),
        ("29/08/2029", "Not a date"),
        ("2029-8-29", "Not a date"),
        ("2029-08-29T00:00", "Not a date"),
        ("", "Not a date"),
        ("abc", "Not a date"),
        (True, "Not a date"),
        (None, "Not a date"),
        (1882656000.5, "Not a date"),
        (float("nan"), "Not a date"),
        ("2029-02-30", "No such day"),
        ("499999999", "block height"),
        (800_000, "block height"),
        (2**32, "too late"),
        ("2026-10-11", "not in the future"),
        ("2026-10-10", "not in the future"),
        (NOW, "not in the future"),
        (str(ts(2020, 1, 1)), "not in the future"),
    ],
)
def test_parse_cli_date_refusals(text, words):
    """Periods, other formats, block heights, too late, past or today."""
    with pytest.raises(ValueError, match=words):
        dv.parse_cli_date(text, NOW)


# ------------------------------------------------------------------ purity


def _source():
    """The source text of ``bal/core/delivery.py``."""
    with open(MODULE, encoding="utf-8") as f:
        return f.read()


def test_the_module_never_reads_the_clock():
    """Every "now" comes from the caller (the test clocks cover it)."""
    src = _source()
    for pattern in (
        r"time\.time",
        r"time\.monotonic",
        r"datetime\.now",
        r"datetime\.utcnow",
        r"date\.today",
        r"^\s*import time\b",
        r"^\s*from time\b",
    ):
        assert not re.search(pattern, src, flags=re.MULTILINE), pattern


def test_the_module_imports_only_the_standard_library():
    """No Qt, no Electrum, no other plugin module: pure rules."""
    allowed = {
        "calendar",
        "hashlib",
        "math",
        "re",
        "collections.abc",
        "dataclasses",
        "datetime",
    }
    found = set()
    for node in ast.walk(ast.parse(_source())):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "relative import"
            found.add(node.module)
    assert found <= allowed, found - allowed


def test_no_float_or_nan_reaches_a_stored_date():
    """Every date the module produces is an int (JSON-stable)."""
    out = dv.migration_values([], {"locktime": float(ts(2030, 1, 1)) + 0.25}, None, True, NOW)
    assert isinstance(out["locktime"], int)
    assert not isinstance(out["locktime"], bool)
    assert math.isfinite(out["locktime"])


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
