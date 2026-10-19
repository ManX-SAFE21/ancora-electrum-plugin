"""The ADVANCED defaults of a new will (CHANGELOG #146).

Owner decisions (2026-10-15, after the Check Alive study):

* BASIC has no Check Alive: a brand-new wallet keeps a fixed date there
  (24 months), and its Check Alive stays inert;
* the Check Alive of a brand-new wallet is one year before the delivery
  ("-365d"); the values of wallets that already had plugin data keep their
  conversion ("-180d" when nothing else is found);
* ADVANCED starts in the period form of 24 months: a brand-new wallet
  opened in ADVANCED, and the wizard's mode selector switched to ADVANCED
  while the form is not locked. "Fixed date" can still be chosen; a will in
  force keeps its form; multiverse never gets the period form.

The rules are pure (``bal.core.delivery.migration_values``); the window and
the wizard are the real ones on ``tests/flowkit.py`` wallets with three
fake will-executors, each with its own fee address.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_advanced_period_default.py
"""

import calendar
import os
import sys
from datetime import datetime
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

import flowkit as fk
from test_gui_delivery_form import settings_of, wizard_page
from test_gui_window_init_migration import real_window
from test_per_wallet_settings_flow import TEST_PASSWORD, assert_check_ok, last

from bal.core import checkalive
from bal.core import delivery as dv
from bal.core.will import Will
from bal.engine import context as engine_context
from bal.gui.qt import common as common_mod

DAY = 86400


@pytest.fixture
def make(monkeypatch):
    """``tests/flowkit.py`` sessions with three will-executors."""
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


@pytest.fixture
def phrase(monkeypatch):
    """What the owner types in the "at My Risk" box (``None``: Cancel)."""
    state = {"text": "at My Risk"}

    def get_text(parent, title, label, *a, **kw):
        """The box: the phrase typed, or Cancel."""
        if state["text"] is None:
            return "", False
        return state["text"], True

    monkeypatch.setattr(common_mod.QInputDialog, "getText", staticmethod(get_text))
    return state


def ts(y, m, d, hh=0, mm=0):
    """A UTC timestamp."""
    return calendar.timegm(datetime(y, m, d, hh, mm).timetuple())


NOW = ts(2026, 10, 15, 9, 30)
TODAY = ts(2026, 10, 15)


def new_wallet(advanced, multiverse=False, plugin_data=False, glob=None):
    """The settings the migration writes into a wallet with nothing in it."""
    return dv.migration_values([], glob, None, plugin_data, NOW,
                               multiverse=multiverse, advanced=advanced)


# ------------------------------------------------------------ the pure rule

def test_basic_new_wallet_is_a_fixed_date_with_the_one_year_check_alive():
    """BASIC: a fixed date 24 months away, the Check Alive "-365d" (inert)."""
    out = new_wallet(advanced=False)
    assert (out["form"], out["locktime"], out["threshold"]) == (
        "fixed", ts(2028, 10, 15), "-365d")
    assert "period_months" not in out and "period_start" not in out
    assert checkalive.check_alive_basic(True, out)
    assert checkalive.check_alive_renewal(True, out, now=ts(2028, 1, 1)) is None


def test_advanced_new_wallet_is_a_period_of_24_months():
    """ADVANCED: the period of 24 months from today, the same date as the
    fixed default, the Check Alive one year before it."""
    out = new_wallet(advanced=True)
    assert out["form"] == "period" and out["period_months"] == 24
    assert out["period_start"] == TODAY
    assert out["locktime"] == ts(2028, 10, 15) == dv.default_delivery(NOW)
    assert out["threshold"] == "-365d"
    assert dv.validate_values(out) == out
    check = checkalive.resolve_date_to_check(False, out, now=NOW)
    assert check == ts(2028, 10, 15) - 365 * DAY


def test_multiverse_never_gets_the_period_form():
    """Multiverse on: a fixed date also in ADVANCED."""
    out = new_wallet(advanced=True, multiverse=True)
    assert out["form"] == "fixed" and "period_months" not in out


def test_a_wallet_with_plugin_data_keeps_its_conversion():
    """Heirs already there: not a brand-new wallet. The form stays fixed and
    the Check Alive keeps the old bridge ("-180d" when nothing is found, a
    "-Nd" habit when there is one)."""
    out = new_wallet(advanced=True, plugin_data=True)
    assert (out["form"], out["threshold"]) == ("fixed", "-180d")
    out = new_wallet(advanced=True, plugin_data=True, glob={"threshold": "-90d"})
    assert (out["form"], out["threshold"]) == ("fixed", "-90d")


def test_the_engine_passes_the_mode_to_the_migration():
    """``ensure_will_settings`` on a fake host (no wallet file): ADVANCED
    gives the period form; a plugin without a readable mode is BASIC."""
    for basic, form in ((False, "period"), (True, "fixed"), (None, "fixed")):
        stored = {}
        db = SimpleNamespace(get_dict=lambda key, s=stored: s,
                             get={"heirs": None}.get)
        plugin = SimpleNamespace(
            config=SimpleNamespace(get=lambda key: None),
            WILL_SETTINGS=SimpleNamespace(name="bal_will_settings"),
            ENABLE_MULTIVERSE=SimpleNamespace(get=lambda: False))
        if basic is not None:
            plugin.is_basic_mode = lambda b=basic: b
        host = SimpleNamespace(wallet=SimpleNamespace(db=db), bal_plugin=plugin,
                               heirs={}, flush_wallet_db=lambda: None)
        assert engine_context.ensure_will_settings(host, {}, now=NOW) is True
        assert dv.form_of(stored) == form, basic
        assert stored["threshold"] == "-365d"


# ------------------------------------------------------------ the wizard

def test_switching_to_advanced_preselects_the_period(make, monkeypatch, phrase):
    """BASIC, no will: ADVANCED in the wizard gives the period form with N =
    the whole months to the stored date; "Fixed date" can still be chosen;
    BASIC again puts the fixed form back."""
    s = make(mode="basic")
    bw = real_window(s, monkeypatch)
    box = wizard_page(bw).form_box
    expected = box._initial_months()
    box.mode_combo.setCurrentIndex(1)
    assert s.plugin.USER_TYPE.get() == "advanced"
    d = settings_of(s)
    assert (d["form"], d["period_months"]) == ("period", expected)
    assert box.period_radio.isChecked() and not box.months_box.isHidden()
    box.fixed_radio.click()
    assert dv.form_of(d) == "fixed"
    box.mode_combo.setCurrentIndex(0)
    assert s.plugin.is_basic_mode() and dv.form_of(d) == "fixed"


def test_a_wrong_phrase_changes_nothing(make, monkeypatch, phrase):
    """The "at My Risk" phrase refused: still BASIC, still a fixed date."""
    s = make(mode="basic")
    bw = real_window(s, monkeypatch)
    box = wizard_page(bw).form_box
    phrase["text"] = "no"
    box.mode_combo.setCurrentIndex(1)
    assert s.plugin.is_basic_mode() and dv.form_of(settings_of(s)) == "fixed"


def test_a_will_in_force_keeps_its_fixed_date(make, monkeypatch, phrase):
    """A will signed and sent in BASIC: switching to ADVANCED never changes
    the locked form (that would need a paid invalidation)."""
    s = make(mode="basic")
    s.check()
    bw = real_window(s, monkeypatch)
    bw.init_will()
    box = wizard_page(bw).form_box
    assert box.locked
    before = dict(settings_of(s))
    box.mode_combo.setCurrentIndex(1)
    assert s.plugin.USER_TYPE.get() == "advanced"
    assert dict(settings_of(s)) == before


def test_multiverse_keeps_the_fixed_date_in_the_wizard(make, monkeypatch, phrase):
    """A table of deliveries (#168; BAL's global multiverse option before):
    ADVANCED in the wizard keeps the fixed date."""
    s = make(mode="basic")
    date = dv.as_delivery(settings_of(s)["locktime"])
    s.set_instalments((date, {"alice": "60%", "bob": "0%"}),
                      (date + 365 * 86400, {"alice": "0%", "bob": "40%"}))
    bw = real_window(s, monkeypatch)
    box = wizard_page(bw).form_box
    box.mode_combo.setCurrentIndex(1)
    assert s.plugin.USER_TYPE.get() == "advanced"
    assert dv.form_of(settings_of(s)) == "fixed"


# ------------------------------------------- the plugin Settings dialog

def qt_plugin(s, monkeypatch, bw):
    """Give the test plugin what the Qt ``Plugin`` has: the open windows
    and ``update_all`` (the shared switch refreshes them)."""
    monkeypatch.setattr(s.plugin, "bal_windows", {"w": bw}, raising=False)
    monkeypatch.setattr(s.plugin, "update_all", lambda: bw.update_all(),
                        raising=False)


def test_settings_switch_on_a_wallet_never_checked(make, monkeypatch, phrase, caplog):
    """The owner's ZIP test (2026-10-15): a new wallet in BASIC, never
    checked, then ADVANCED from the plugin Settings: the period form, and
    the Will tab shows its ADVANCED fields at once (the refresh used to stop
    on the missing ``date_to_check``)."""
    s = make(mode="basic")
    bw = real_window(s, monkeypatch)
    assert not hasattr(bw, "date_to_check")
    qt_plugin(s, monkeypatch, bw)
    toolbar = bw.will_list_widget.will_settings_widget
    assert toolbar.widgets["locktime"].isHidden()
    expected = engine_context.initial_months(settings_of(s), s.clock.now)
    with caplog.at_level("ERROR"):
        assert common_mod.switch_user_type(s.plugin, True)
    assert not [r for r in caplog.records if "updating window" in r.getMessage()]
    d = settings_of(s)
    assert (d["form"], d["period_months"]) == ("period", expected)
    assert not toolbar.widgets["locktime"].isHidden()
    assert not toolbar.widgets["threshold"].isHidden()
    assert common_mod.switch_user_type(s.plugin, False)
    assert dv.form_of(d) == "fixed"
    assert toolbar.widgets["locktime"].isHidden()


def test_settings_switch_keeps_a_will_in_force(make, monkeypatch, phrase):
    """A will signed and sent in BASIC: ADVANCED from the Settings keeps
    its fixed date (a change would need a paid invalidation)."""
    s = make(mode="basic")
    s.check()
    bw = real_window(s, monkeypatch)
    bw.init_will()
    qt_plugin(s, monkeypatch, bw)
    before = dict(settings_of(s))
    assert common_mod.switch_user_type(s.plugin, True)
    assert dict(settings_of(s)) == before


def test_will_tab_rows_fee_on_top_and_the_whole_check_alive_sentence(make, monkeypatch):
    """The owner's ZIP test: in ADVANCED the Check Alive sentence was cut
    ("prima della c 2027-10-16"). The fee now sits next to the delivery
    date and the sentence keeps the width of its whole text."""
    s = make(mode="advanced")
    bw = real_window(s, monkeypatch)
    toolbar = bw.will_list_widget.will_settings_widget
    w = toolbar.widgets
    first, second = (
        [r.itemAt(i).widget() for i in range(r.count()) if r.itemAt(i).widget()]
        for r in toolbar._rows
    )
    assert first == [w["locktime"], w["baltx_fees"]] and second == [w["threshold"]]
    hint = w["threshold"].hint
    assert hint.text().endswith(" →") and "(≈ " in hint.text()
    assert hint.minimumWidth() == hint.sizeHint().width() > 0


# ------------------------------------------------------------ the whole flow

def test_new_advanced_wallet_builds_and_renews_after_one_year(make):
    """A wallet created in ADVANCED (three will-executors): the period of 24
    months, the will built for that date, no renewal for a year, then the
    renewal offered with the new date 24 months after that day."""
    a = make(mode="advanced")
    c = a.second_wallet(seed=False, plugin_data=False, password=TEST_PASSWORD)
    d = c.wallet.db.get_dict("will_settings")
    start = dv.utc_midnight(a.clock.now)
    delivery_date = dv.add_months(start, 24)
    assert (d["form"], d["period_months"], d["threshold"]) == ("period", 24, "-365d")
    assert d["locktime"] == delivery_date
    c.activate()
    c.check()                                     # signs and sends the will
    assert Will.get_min_locktime(c.bw.willitems) == delivery_date
    c.check()                                     # the second Check is clean
    assert_check_ok(last(c))
    assert engine_context.check_alive_info(c.bw) is None
    c.clock.travel(364)
    assert engine_context.check_alive_info(c.bw) is None
    c.clock.travel(2)
    info = engine_context.check_alive_info(c.bw)
    assert info is not None and info["renewable"]
    assert info["new_delivery"] == dv.add_months(dv.utc_midnight(c.clock.now), 24)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
