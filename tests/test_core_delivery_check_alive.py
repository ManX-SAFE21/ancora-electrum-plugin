"""The Check Alive kept by the delivery settings migration (CHANGELOG #123).

Before #123 the Check Alive lived in Electrum's global ``bal_will_settings``.
An old form there (a fixed date, as BAL 0.6.1's default writes it, or a
positive "after the creation of the will" period) was converted on every
load to "N days before the delivery", keeping the same Check Alive date
(#80, ``checkalive.normalize_check_alive``).

The #123 migration first kept only a ``"-Nd"`` value and stored "-180d" for
every other form. With a delivery less than 180 days away that Check Alive
is already in the past, and the ADVANCED Check stopped at once (scenario
harness C1adv / C2adv / C3adv). Now the migration converts the old form
with the #80 rule against the migrated delivery date
(``delivery.convert_check_alive``), before falling back to "-180d".

This file checks:

* the pure rule (``bal.core.delivery``, no wallet), and that it agrees with
  ``checkalive.normalize_check_alive`` value by value;
* the order of the old dicts and the plugin-data gate;
* the engine path (``bal.engine.context.ensure_will_settings``) through a
  test window and through the command line, with ``tests/flowkit.py``:
  three fake will-executors, a throw-away test wallet with the test
  password, a fixed clock. Nothing reaches a real will-executor or the
  network.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_core_delivery_check_alive.py
"""

import os
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

from bal.core import checkalive
from bal.core import delivery as dv

DAY = dv.DAY


def ts(y, m, d, hh=0, mm=0):
    """UNIX time of a UTC date and time."""
    return int(datetime(y, m, d, hh, mm, tzinfo=timezone.utc).timestamp())


# The clock of the pure tests.
NOW = ts(2026, 10, 11, 9, 30)
# The owner's will in force: 2029-08-29 00:00 UTC.
OWNER = 1882656000
# A delivery 60 days away, as the scenario harness seeds it ("SOON").
SOON = ts(2026, 12, 10)


def item(locktime, fee=13, **flags):
    """A will item as the delivery rules read it (an ``ItemView``)."""
    return dv.item_view("t{}".format(locktime), locktime, fee, flags)


def signed(locktime, fee=13):
    """A will in force: VALID, signed (COMPLETE), sent (PUSHED)."""
    return item(locktime, fee, VALID=True, COMPLETE=True, PUSHED=True)


def bridge(global_raw, legacy_raw=None, items=(), plugin_data=True,
           creation_date=None):
    """The Check Alive the migration stores for these old dicts."""
    out = dv.migration_values(list(items), global_raw, legacy_raw,
                              plugin_data, NOW, creation_date=creation_date)
    return out["threshold"]


# ------------------------------------------------------------ the pure rule

def test_minus_nd_is_kept():
    """A "-Nd" / "-Ny" value is kept, in days."""
    assert dv.convert_check_alive("-30d", OWNER, None, NOW) == "-30d"
    assert dv.convert_check_alive(" -1y ", OWNER, None, NOW) == "-365d"


def test_a_fixed_date_keeps_the_same_check_alive_date():
    """A fixed date becomes the days between it and the delivery: the Check
    Alive date stays the same. Any number form of the date is read."""
    for value in (OWNER - 45 * DAY, float(OWNER - 45 * DAY),
                  str(OWNER - 45 * DAY)):
        assert dv.convert_check_alive(value, OWNER, None, NOW) == "-45d"
    assert OWNER - 45 * DAY == ts(2029, 7, 15)
    # Not at midnight: rounded to whole days, as #80 does.
    assert dv.convert_check_alive(OWNER - 45 * DAY - 5 * 3600, OWNER, None,
                                  NOW) == "-45d"
    assert dv.convert_check_alive(OWNER - 45 * DAY + 13 * 3600, OWNER, None,
                                  NOW) == "-44d"


def test_a_past_fixed_date_before_the_delivery_is_kept():
    """A Check Alive already passed but before the delivery is kept (#80):
    the owner is then asked about it, it is not silently moved."""
    assert dv.convert_check_alive(NOW - 10 * DAY, SOON, None, NOW) == "-{}d".format(
        round((SOON - (NOW - 10 * DAY)) / DAY))


def test_a_positive_period_counts_from_the_creation_of_the_will():
    """The old "after the creation" form counts from 00:00 UTC of the day the
    will was created, else from today."""
    created = ts(2026, 9, 1, 14, 0)
    # 2026-09-01 + 30 days = 2026-10-01; 2026-12-10 is 70 days later.
    assert dv.convert_check_alive("30d", SOON, created, NOW) == "-70d"
    # Unknown creation: from today (2026-10-11 + 30 = 2026-11-10).
    assert dv.convert_check_alive("30d", SOON, None, NOW) == "-30d"
    assert dv.convert_check_alive("1y", OWNER, created, NOW) == "-{}d".format(
        (OWNER - (ts(2026, 9, 1) + 365 * DAY)) // DAY)


@pytest.mark.parametrize("value, creation", [
    (SOON, None),                    # on the delivery
    (SOON + DAY, None),              # after the delivery
    (SOON - 3651 * DAY, None),       # more than 3650 days before
    ("-4000d", None),                # a negative period out of range
    ("-0d", ts(2026, 12, 10)),       # zero days before the delivery
    ("200d", None),                  # a positive period ending after it
    ("30d", "yesterday"),            # a creation date that is not a number
    (800_000, None),                 # a block height
    ("36m", None),                   # months were never a period
    (True, None),
    (None, None),
    ("garbage", None),
    ([SOON - 30 * DAY], None),       # not a flat value
])
def test_what_cannot_be_converted(value, creation):
    """Where #80 stores its default, the conversion gives None."""
    assert dv.convert_check_alive(value, SOON, creation, NOW) is None


def test_a_negative_period_out_of_range_is_never_counted():
    """A negative period beyond 3650 days is not counted from today, even
    when that would land in range (a will whose delivery has passed)."""
    expired = NOW - 100 * DAY
    assert dv.convert_check_alive("-3700d", expired, None, NOW) is None


def test_the_rule_agrees_with_check_alive_normalization():
    """Value by value, the pure rule gives what
    ``checkalive.normalize_check_alive`` gives for the same settings (its
    default "-180d" where the rule gives None). Dates before 2038 only:
    after that ``BalTimestamp`` may read a date wrongly where ``time_t`` is
    32-bit, which is why the rule is copied and not imported."""
    # The last one has passed (an expired will in force).
    deliveries = (SOON, OWNER, ts(2027, 3, 3, 7, 45), NOW - 100 * DAY)
    creations = (None, ts(2026, 9, 1, 14, 0))
    values = ["-30d", "-1y", "-3650d", "-3651d", "-3700d", "-4000d", "-0d",
              "0d", "1d",
              "30d", "180d", "1y", "2y", "400d", "36m", "garbage", None, True,
              800_000, 30]
    for delivery_date in deliveries:
        for days in (-5, 0, 1, 29, 30, 180, 365, 3650, 3651):
            for hours in (0, 5, 13):
                values.append(delivery_date - days * DAY - hours * 3600)
                values.append(float(delivery_date - days * DAY + hours * 3600))
    checked = 0
    for delivery_date in deliveries:
        for creation in creations:
            for value in values:
                mine = dv.convert_check_alive(value, delivery_date, creation, NOW)
                reference = checkalive.normalize_check_alive(
                    {"threshold": value, "locktime": delivery_date},
                    creation_date=creation, now=NOW)
                assert (mine or dv.DEFAULT_THRESHOLD) == reference, (
                    value, delivery_date, creation)
                checked += 1
    assert checked > 600


# ----------------------------------------------------- the migration bridge

def test_the_harness_seeding_keeps_its_check_alive():
    """The case of harness C1adv / C2adv / C3adv: an old global with a fixed
    delivery 60 days away and a fixed Check Alive 30 days before it. The
    wallet gets that delivery and "-30d", not "-180d" (already passed)."""
    old = {"locktime": SOON, "baltx_fees": 13, "threshold": SOON - 30 * DAY}
    out = dv.migration_values([], old, None, True, NOW)
    assert (out["migrated_from"], out["locktime"], out["threshold"]) == (
        "global", SOON, "-30d")
    assert out["locktime"] - 30 * DAY == old["threshold"]   # the same date


def test_converted_against_the_will_in_force():
    """With a will in force the delivery is the will's date: the old fixed
    Check Alive keeps its date against it, whatever the old shared delivery
    said ("1054d" or another fixed date)."""
    check = OWNER - 28 * DAY
    for old_locktime in ("1054d", OWNER + 2 * DAY):
        old = {"locktime": old_locktime, "baltx_fees": 13, "threshold": check}
        assert bridge(old, items=[signed(OWNER)]) == "-28d"


def test_a_minus_nd_value_still_comes_first():
    """A "-Nd" value of any old dict wins over a form to convert (the order
    the migration had before). A brand-new wallet (no plugin data) takes
    the one-year default of #146 instead."""
    fixed = {"threshold": OWNER - 30 * DAY}
    assert bridge(fixed, {"threshold": "-90d"}, items=[signed(OWNER)]) == "-90d"
    assert bridge({"threshold": "30d"}, {"threshold": "-90d"}) == "-90d"
    assert bridge({"threshold": "-90d"}, plugin_data=False) == "-365d"


def test_the_order_of_the_old_dicts():
    """The forms to convert are read in the order of the old dicts: the
    global, then the wallet's own dict; the wallet's own dict first when it
    already has a ``settings_version`` (written by Safe21 after #123)."""
    glob = {"threshold": OWNER - 30 * DAY}
    legacy = {"threshold": OWNER - 60 * DAY}
    items = [signed(OWNER)]
    assert bridge(glob, legacy, items) == "-30d"
    assert bridge(glob, dict(legacy, settings_version=1), items) == "-60d"
    # A value that cannot be converted is skipped: the next dict is read.
    assert bridge({"threshold": OWNER + DAY}, legacy, items) == "-60d"
    assert bridge({"threshold": "garbage"}, legacy, items) == "-60d"
    assert bridge({"threshold": OWNER + DAY}, None, items) == "-180d"


def test_a_wallet_without_plugin_data_does_not_convert():
    """A wallet created after the update inherits neither the old delivery
    date nor a Check Alive tied to it: "-365d" (#146) before its own default
    delivery (a fixed date in the past would stop its ADVANCED Check)."""
    old = {"locktime": SOON, "baltx_fees": 13, "threshold": NOW - 30 * DAY}
    out = dv.migration_values([], old, None, False, NOW)
    assert (out["migrated_from"], out["threshold"]) == ("default", "-365d")
    # With plugin data the same past date is kept (#80).
    assert bridge(old) == "-{}d".format(round((SOON - (NOW - 30 * DAY)) / DAY))


def test_a_positive_period_uses_the_creation_date_given():
    """``creation_date`` reaches the conversion of a positive period."""
    created = ts(2026, 9, 1, 14, 0)
    old = {"locktime": "1054d", "baltx_fees": 13, "threshold": "30d"}
    expected = "-{}d".format((OWNER - ts(2026, 10, 1)) // DAY)
    assert bridge(old, items=[signed(OWNER)], creation_date=created) == expected
    # The same as #80 gave before #123 for the will in force.
    assert expected == checkalive.normalize_check_alive(
        old, built_locktime=OWNER, creation_date=created, now=NOW)


def test_every_converted_result_is_current():
    """The converted value passes the validator: the dict is current (I2)."""
    for old in ({"locktime": SOON, "baltx_fees": 13, "threshold": SOON - 30 * DAY},
                {"locktime": "1054d", "baltx_fees": 13, "threshold": "30d"}):
        out = dv.migration_values([signed(OWNER)], old, None, True, NOW)
        assert dv.is_current(out)
        assert dv.validate_values({"threshold": out["threshold"]}) == {
            "threshold": out["threshold"]}


# ---------------------------------------------------------- the engine path

TEST_PASSWORD = "flowkit-test"


@pytest.fixture
def make(monkeypatch):
    """Start ``tests/flowkit.py`` sessions with three will-executors.

    Electrum's asyncio loop runs during the test and is stopped after it
    (else pytest never exits); every session's folder is removed.
    """
    import flowkit as fk

    fk.ensure_event_loop()
    made = []

    def _make(**kw):
        """One session (``fk.Session`` arguments), three will-executors."""
        kw.setdefault("password", TEST_PASSWORD)
        s = fk.Session(monkeypatch, **kw)
        s.world.add_executor()
        made.append(s)
        return s

    yield _make
    for s in made:
        s.close()
    fk.stop_event_loop()


def wallet_dict(s):
    """The wallet's own delivery settings (the live dict of the wallet file)."""
    return s.wallet.db.get_dict("will_settings")


def back_before_123(s, old_global):
    """The wallet as the plugin before #123 left it: its own dict empty (item
    by item), ``old_global`` in Electrum's global (taken as the rule-I1
    reference). Nothing is migrated yet."""
    d = wallet_dict(s)
    for key in list(d):
        del d[key]
    s.config.set_key("bal_will_settings", dict(old_global))
    s.snapshot_global()
    return d


def rows(s):
    """The rows of the last recorded step, without HTML."""
    import flowkit as fk

    return [fk.strip(r) for r in s.recorder.steps[-1].get("rows", [])]


def test_window_migrates_an_old_fixed_check_alive(make):
    """The window opens on an old global with a delivery 60 days away and a
    fixed Check Alive 30 days before it (the harness seeding). The wallet
    gets "-30d", the ADVANCED Check is not stopped by a passed Check Alive,
    and the first will is built at that delivery and sent to the three
    will-executors. The global is never written (rule I1, checked by the
    flowkit recorder at every step)."""
    import flowkit as fk

    from bal.engine import context

    soon = fk.ts(2026, 12, 9)            # 60 days after the flowkit clock
    old = {"locktime": soon, "baltx_fees": 13, "threshold": soon - 30 * DAY}
    s = make(mode="advanced", locktime=soon, legacy_global=old)
    d = wallet_dict(s)
    assert s.bw.will_settings is d and s.bw.settings_unavailable is False
    assert (d["migrated_from"], d["locktime"], d["baltx_fees"], d["threshold"]) == (
        "global", soon, 13, "-30d")
    context.init_class_variables(s.bw)   # no Check Alive error
    assert s.bw.date_to_check == soon - 30 * DAY

    s.check()
    step = s.recorder.steps[-1]
    assert rows(s)[0] == "Checking variables: Ok", rows(s)
    assert "All done: Ok" in rows(s), rows(s)
    assert step["questions"] == [["password", "Sign your will"]]
    assert sorted(url for url, _tx in step["pushed"]) == [
        "https://we0.test", "https://we1.test", "https://we2.test"]
    assert {fk.fmt(w.tx.locktime) for w in s.bw.willitems.values()} == {
        fk.fmt(soon)}
    assert all(w.get_status("VALID") for w in s.bw.willitems.values())
    assert d["threshold"] == "-30d" and d["locktime"] == soon
    s.assert_global_unchanged()


def test_window_and_command_line_count_a_positive_period_from_the_will(make):
    """A will in force, then the time before #123 with an old positive Check
    Alive ("30d" after the creation of the will) in the global. The window
    and the command line both migrate it against the will's date, counted
    from the creation time of the stored items: what #80 gave before #123.
    The next Check is Ok and changes nothing. The update comes 5 days after
    the will was built, so counting from today would give another value."""
    import flowkit as fk

    from bal.core.will import Will

    s = make(mode="advanced")
    s.check()
    built = Will.get_min_locktime(s.bw.willitems)
    created = Will.get_creation_time(s.bw.willitems)
    assert built == fk.D0 and created is not None
    s.clock.travel(5)
    old = {"locktime": fk.D0, "baltx_fees": 13, "threshold": "30d"}
    expected = checkalive.normalize_check_alive(
        old, built_locktime=built, creation_date=created, now=s.clock.time())
    assert expected == "-{}d".format(
        (fk.D0 - (dv.utc_midnight(created) + 30 * DAY)) // DAY)
    assert expected != checkalive.normalize_check_alive(
        old, built_locktime=built, creation_date=None, now=s.clock.time())

    # The command line migrates with the items it loaded itself.
    d = back_before_123(s, old)
    shown = s.cli("delivery_show")["returned"]
    assert (shown["threshold"], shown["migrated_from"]) == (expected, "will")
    assert d["threshold"] == expected

    # The window migrates with copies of the stored items.
    d = back_before_123(s, old)
    s.reload_window()
    assert s.bw.will_settings is d
    assert (d["threshold"], d["migrated_from"], d["locktime"]) == (
        expected, "will", fk.D0)

    s.check()
    step = s.recorder.steps[-1]
    assert step["questions"] == [] and step["pushed"] == []
    assert step["broadcasts"] == []
    assert "All done: Ok" in rows(s), rows(s)
    s.assert_global_unchanged()


def fake_host(old_global=None, heirs_stored=None):
    """A host with a plain wallet dict and no wallet file (see
    ``bal.engine.host``): ``old_global`` is Electrum's global as found,
    ``heirs_stored`` the wallet's ``heirs`` key (plugin data when not
    empty). Returns ``(host, stored settings dict)``."""
    stored = {}
    data = {"heirs": heirs_stored}
    db = SimpleNamespace(get_dict=lambda key: stored, get=data.get)
    plugin = SimpleNamespace(
        config=SimpleNamespace(get=lambda key: old_global),
        WILL_SETTINGS=SimpleNamespace(name="bal_will_settings"),
        ENABLE_MULTIVERSE=SimpleNamespace(get=lambda: False))
    # One heir with a date: nothing to convert in the heirs.
    heirs = {"alice": ["tb1q-alice", "100%", OWNER]}
    host = SimpleNamespace(wallet=SimpleNamespace(db=db), bal_plugin=plugin,
                           heirs=heirs, flush_wallet_db=lambda: None)
    return host, stored


def test_the_creation_date_reaches_the_rule(monkeypatch):
    """``ensure_will_settings`` passes ``Will.get_creation_time`` of the
    items it read to the migration (fake host, no wallet file)."""
    from bal.engine import context

    seen = {}
    real = dv.migration_values

    def spy(*args, **kw):
        """Record the creation date, then run the real rule."""
        seen["creation_date"] = kw.get("creation_date")
        return real(*args, **kw)

    monkeypatch.setattr(dv, "migration_values", spy)
    monkeypatch.setattr(context.Will, "get_creation_time",
                        staticmethod(lambda willitems: 1_790_000_000))
    monkeypatch.setattr(context.delivery, "views_from", lambda willitems: [])
    host, stored = fake_host()
    assert context.ensure_will_settings(host, {"x": object()}, now=NOW) is True
    assert seen["creation_date"] == 1_790_000_000
    assert stored["threshold"] == "-365d" and host.will_settings is stored


def test_an_unknown_creation_time_counts_from_today(monkeypatch):
    """Will items without a usable ``time`` (an old or a broken item) do not
    stop the migration: an old positive Check Alive then counts from today,
    as #80 did when the creation was unknown."""
    from bal.engine import context

    def no_time(willitems):
        """``Will.get_creation_time`` on items without ``time``."""
        raise AttributeError("'_Item' object has no attribute 'time'")

    monkeypatch.setattr(context.Will, "get_creation_time", staticmethod(no_time))
    monkeypatch.setattr(context.delivery, "views_from", lambda willitems: [])
    old = {"locktime": SOON, "baltx_fees": 13, "threshold": "30d"}
    host, stored = fake_host(old, heirs_stored={"alice": ["a", "100%", SOON]})
    assert context.ensure_will_settings(host, {"x": object()}, now=NOW) is True
    assert host.settings_unavailable is False
    # 2026-10-11 + 30 days = 2026-11-10, 30 days before 2026-12-10.
    assert (stored["migrated_from"], stored["locktime"], stored["threshold"]) == (
        "global", SOON, "-30d")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
