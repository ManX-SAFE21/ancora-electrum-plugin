"""The delivery form in calendar months: the pure rules (CHANGELOG #127).

Owner decisions of 2026-10-12: a will is delivered on a FIXED date or after
a PERIOD of N calendar months (1..240) that the Check Alive renews; the
form is locked while a will is in force; a new wallet gets today + 24
months. The rules live in ``bal.core.delivery`` (pure, no clock), the words
in ``bal.core.messages.months_text`` (the existing msgids only). Here:

* the month arithmetic of the design (plan section 2) and ``months_text``;
* ``as_months``, ``form_of``, ``period_months``, ``period_values``;
* the stored values: ``validate_values`` for ``form`` / ``period_months`` /
  ``period_start``, the v2 upgrade (``upgrade_values``);
* the two predicates on the will items: ``form_locked`` (a will in force
  locks the form) and ``will_in_force`` (the period date is moved only
  without one);
* ``check_alive_renewal`` of the period form: the new Check Alive is counted
  from the NEW delivery date (the plan's critic 2: counted from the old
  stored date the renewal was never offered).

Run (standalone or with pytest):
    PYTHONPATH=electrum-src python3 -m pytest tests/test_core_delivery_months.py
"""

import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from bal.core import delivery as dv
from bal.core.checkalive import check_alive_basic, check_alive_renewal
from bal.core.messages import months_text

DAY = 86400


def ts(y, m, d, hh=0, mm=0):
    """UTC timestamp of a calendar day (and time)."""
    return int(datetime(y, m, d, hh, mm, tzinfo=timezone.utc).timestamp())


NOW = ts(2026, 10, 11, 9, 30)


# ------------------------------------------------------------ month arithmetic

@pytest.mark.parametrize("start, n, expected", [
    (ts(2026, 10, 11), 36, ts(2029, 10, 11)),
    (ts(2026, 1, 31), 1, ts(2026, 2, 28)),
    (ts(2027, 1, 31), 13, ts(2028, 2, 29)),
    (ts(2028, 2, 29), 12, ts(2029, 2, 28)),
    (ts(2029, 8, 29), -6, ts(2029, 2, 28)),
    (ts(2026, 10, 11, 23, 59), 24, ts(2028, 10, 11)),
])
def test_add_months_cases_of_the_design(start, n, expected):
    """The PROVEN examples of plan section 2 (day clamped, 00:00 UTC)."""
    assert dv.add_months(start, n) == expected


def test_months_between_is_the_inverse_on_the_whole_range():
    """``months_between(d, add_months(d, n)) == n`` for every N the period
    accepts, from a day that clamps (the 31st)."""
    start = ts(2027, 1, 31)
    for n in range(dv.PERIOD_MIN, dv.PERIOD_MAX + 1):
        assert dv.months_between(start, dv.add_months(start, n)) == n, n


@pytest.mark.parametrize("n, text", [
    (1, "1 month"),
    (6, "6 months"),
    (11, "11 months"),
    (12, "12 months (1 year)"),
    (13, "13 months (1 year and 1 month)"),
    (24, "24 months (2 years)"),
    (35, "35 months (2 years and 11 months)"),
    (36, "36 months (3 years)"),
    (240, "240 months (20 years)"),
])
def test_months_text(n, text):
    """Only the existing msgids; the bracket joined outside ``_()``."""
    assert months_text(n) == text


def test_months_text_uses_no_bare_months_msgid():
    """Never a bare "months" (another catalog could translate it)."""
    import inspect

    from bal.core import messages

    source = inspect.getsource(messages.months_text)
    assert '_("months")' not in source and "_(' months')" not in source


# ------------------------------------------------------------ form helpers

@pytest.mark.parametrize("value, expected", [
    (1, 1), (240, 240), (36.0, 36), ("36", 36), (" 7 ", 7),
    (0, None), (241, None), (-3, None), (True, None), (1.5, None),
    ("36m", None), ("3y", None), ("", None), (None, None), ([36], None),
])
def test_as_months(value, expected):
    """Whole months from 1 to 240; a recipe or a bool is no period."""
    assert dv.as_months(value) == expected


def test_form_of_missing_means_fixed():
    """Plan 1.1: a dict without ``form`` (a v1 dict, a test host's plain
    dict) or with an unknown one is a fixed date."""
    assert dv.form_of({}) == dv.FORM_FIXED
    assert dv.form_of({"form": "weird"}) == dv.FORM_FIXED
    assert dv.form_of(None) == dv.FORM_FIXED
    assert dv.form_of({"form": "period"}) == dv.FORM_PERIOD
    assert dv.period_months({"form": "fixed", "period_months": 36}) is None
    assert dv.period_months({"form": "period", "period_months": 36}) == 36
    assert dv.period_months({"form": "period", "period_months": "x"}) is None


def test_period_values_today_plus_n_months():
    """Computed once from today's UTC day, never backwards."""
    assert dv.period_values(NOW, 36) == {
        "locktime": ts(2029, 10, 11), "period_start": ts(2026, 10, 11)}
    with pytest.raises(ValueError):
        dv.period_values(NOW, 0)


def test_default_is_twenty_four_months():
    """Owner decision 6: a new will / nothing to migrate: today + 24."""
    assert dv.DEFAULT_MONTHS == 24
    assert dv.default_delivery(NOW) == ts(2028, 10, 11)
    assert dv.default_values(NOW)["form"] == dv.FORM_FIXED
    # The warning thresholds of #124 stay 12 and 6.
    assert (dv.WARN_YELLOW_MONTHS, dv.WARN_RED_MONTHS) == (12, 6)


def test_validate_values_of_the_form_keys():
    """``form`` one of the two codes; ``period_months`` 1..240 as an int;
    ``period_start`` a date as an int."""
    out = dv.validate_values({"form": "period", "period_months": "36",
                              "period_start": float(ts(2026, 10, 11))})
    assert out == {"form": "period", "period_months": 36,
                   "period_start": ts(2026, 10, 11)}
    for bad in ({"form": "monthly"}, {"period_months": 0},
                {"period_months": 241}, {"period_months": "36m"},
                {"period_start": "36m"}, {"period_start": 10}):
        with pytest.raises(ValueError):
            dv.validate_values(bad)


# ------------------------------------------------------------ v2 upgrade

def v1(**changes):
    """A current settings dict of schema 1 (#123)."""
    d = {"settings_version": 1, "locktime": ts(2029, 8, 29),
         "baltx_fees": 13, "threshold": "-180d"}
    d.update(changes)
    return d


def test_upgrade_v1_sets_the_fixed_form():
    """Every existing wallet starts fixed (owner decision), even one that
    holds another ``form`` value; nothing else changes."""
    assert dv.upgrade_values(v1()) == {"form": "fixed", "settings_version": 2}
    assert dv.upgrade_values(v1(form="period")) == {
        "form": "fixed", "settings_version": 2}


def test_upgrade_is_idempotent():
    """Once written, a second load writes nothing."""
    d = v1()
    dv.write_values(d, dv.upgrade_values(d))
    assert d["form"] == "fixed" and d["settings_version"] == 2
    assert dv.upgrade_values(d) == {}
    assert dv.upgrade_values(v1(settings_version=3)) == {}


def test_upgrade_skips_a_dict_that_is_not_current():
    """A broken or empty dict is migrated first (#123), never upgraded."""
    assert dv.upgrade_values({}) == {}
    assert dv.upgrade_values(v1(locktime="1054d")) == {}
    assert dv.upgrade_values(None) == {}


def test_a_v1_dict_is_still_current():
    """The v2 upgrade never triggers the #123 migration again."""
    assert dv.is_current(v1())
    assert dv.FIRST_SCHEMA_VERSION == 1 and dv.SCHEMA_VERSION == 2


# ------------------------------------------------------------ predicates

def item(wid, **flags):
    """An ``ItemView`` with the given flags."""
    return dv.item_view(wid, ts(2029, 8, 29), 13, flags)


def nothing_spent(_wid):
    """``executable``: no input spent."""
    return True


def all_spent(_wid):
    """``executable``: every input spent (by a confirmed tx for the lock)."""
    return False


def test_unsigned_valid_will_does_not_lock():
    """A VALID item never signed nor sent was never in force."""
    items = [item("a", VALID=True)]
    assert not dv.form_locked(items, True, nothing_spent)
    assert not dv.form_locked(items, False, nothing_spent)


@pytest.mark.parametrize("flags", [
    {"VALID": True, "COMPLETE": True},
    {"VALID": True, "PUSHED": True},
    {"VALID": True, "COMPLETE": True, "PUSHED": True},
])
def test_signed_or_sent_valid_will_locks(flags):
    """A VALID item signed or sent: a will in force."""
    assert dv.form_locked([item("a", **flags)], True, all_spent)


def test_sent_item_locks_until_its_invalidation_is_confirmed():
    """An INVALIDATED (or REPLACED) sent item still executable locks: an
    invalidation only in the mempool can be dropped. Spent by a confirmed
    transaction (``executable`` False): unlocked."""
    for flags in ({"PUSHED": True, "INVALIDATED": True},
                  {"PUSHED": True, "REPLACED": True}):
        items = [item("a", **flags)]
        assert dv.form_locked(items, True, nothing_spent), flags
        assert not dv.form_locked(items, True, all_spent), flags


def test_unsynchronized_wallet_any_sent_item_not_confirmed_locks():
    """Stale flags: conservative."""
    items = [item("a", PUSHED=True, INVALIDATED=True)]
    assert dv.form_locked(items, False, all_spent)
    assert not dv.form_locked([item("a", PUSHED=True, CONFIRMED=True)],
                              False, nothing_spent)


def test_confirmed_or_executed_items_do_not_lock():
    """A will already executed (CONFIRMED / MEMPOOL) is not in force."""
    for flags in ({"PUSHED": True, "CONFIRMED": True},
                  {"PUSHED": True, "MEMPOOL": True}):
        assert not dv.form_locked([item("a", **flags)], True, nothing_spent)
    assert not dv.form_locked([], True, nothing_spent)


def test_will_in_force_for_the_period_policy():
    """A VALID item (signed or not), or a sent item not invalidated and
    still executable; on an unsynchronized wallet any such sent item."""
    assert dv.will_in_force([item("a", VALID=True)], True, all_spent)
    sent = [item("a", PUSHED=True, REPLACED=True)]
    assert dv.will_in_force(sent, True, nothing_spent)
    assert not dv.will_in_force(sent, True, all_spent)
    assert dv.will_in_force(sent, False, all_spent)
    gone = [item("a", PUSHED=True, INVALIDATED=True)]
    assert not dv.will_in_force(gone, False, nothing_spent)
    assert not dv.will_in_force([], True, nothing_spent)


# ------------------------------------------------------------ renewal rule

def renewal(settings, basic=False, now=None):
    """``check_alive_renewal`` of a will in force delivered in 20 days."""
    now = now if now is not None else ts(2027, 1, 15)
    return check_alive_renewal(basic, settings, built_locktime=now + 20 * DAY,
                               creation_date=now - 300 * DAY, now=now)


def period(n, **changes):
    """Settings of the period form, the will in force in 20 days."""
    d = {"locktime": ts(2027, 2, 4), "threshold": "-30d", "form": "period",
         "period_months": n}
    d.update(changes)
    return d


def test_renewal_counts_the_new_check_alive_from_the_new_date():
    """N = 2: new delivery 2027-03-15, its Check Alive 30 days before
    (2027-02-13), after today: renewable. Counted from the OLD stored date
    (2027-02-04 - 30 days) it would be in the past: never offered."""
    info = renewal(period(2))
    assert info["renewable"] and info["reason"] is None
    assert info["new_delivery"] == ts(2027, 3, 15)
    assert info["new_check"] == ts(2027, 2, 13)
    assert info["period"] == 2


def test_renewal_still_past_and_fixed_date():
    """N = 1 with a Check Alive of 60 days: the renewed will would have its
    Check Alive in the past. A fixed date is never renewed."""
    assert renewal(period(1, threshold="-60d"))["reason"] == "still_past"
    fixed = {"locktime": ts(2027, 2, 4), "threshold": "-30d"}
    assert renewal(fixed)["reason"] == "fixed_date"
    assert renewal(period(2, period_months="x"))["reason"] == "fixed_date"


def test_check_alive_basic_only_for_a_fixed_date():
    """Owner decision 5: BASIC hides the choice, a period will keeps its
    Check Alive."""
    assert check_alive_basic(True, {"locktime": 1}) is True
    assert check_alive_basic(True, period(2)) is False
    assert check_alive_basic(False, {"locktime": 1}) is False
    assert renewal({"locktime": ts(2027, 2, 4), "threshold": "-30d"},
                   basic=True) is None
    assert renewal(period(2), basic=True)["renewable"]


def test_delivery_module_stays_pure():
    """No clock read and no Electrum or Qt import in ``bal.core.delivery``."""
    import inspect

    source = inspect.getsource(dv)
    for forbidden in ("time.time", "datetime.now", "import electrum",
                      "from electrum", "PyQt", "from ..", "from ."):
        assert forbidden not in source, forbidden


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
