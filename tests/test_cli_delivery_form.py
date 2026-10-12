"""The delivery form on the command line (CHANGELOG #127).

English only, like every command-line text (never extracted for
translation):

* ``bal_delivery_show`` also says the form (``fixed`` / ``period``), the
  period N in months and in words, the day the period started and whether
  the form is locked (a will in force);
* ``bal_delivery_set --months N`` changes N of a period-form wallet (with
  no will in force the date becomes today + N months; with a will in force
  N applies at the next renewal); refused for a fixed date, and ``--date``
  is refused for the period form;
* ``bal_delivery_form form=fixed|period`` with ``date=`` / ``months=``,
  refused while a will is in force with the steps to follow (invalidate,
  wait for the confirmation, choose, prepare).

``tests/flowkit.py`` sessions: three fake will-executors with their own
fee addresses, a throw-away test wallet with the test password, the fixed
clock (2026-10-10 12:00 UTC), commands run through ``bal.cli.commands._call``
as the daemon runs them.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_cli_delivery_form.py
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

import flowkit as fk
from PyQt6.QtWidgets import QApplication

from bal.cli.controller import _months_words

# Kept at module level: an unreferenced application object is destroyed at
# once, and creating a widget afterwards aborts Python.
_APP = QApplication.instance() or QApplication(sys.argv)

# Test value: the password of the throw-away test wallets.
TEST_PASSWORD = "flowkit-test"


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


def dated(ts):
    """``{"locktime": ts, "date": "YYYY-MM-DD"}``, as the commands return it."""
    return {"locktime": ts, "date": fk.fmt(ts)}


def test_months_words_are_english():
    """The command line never uses the translated GUI words."""
    assert _months_words(1) == "1 month"
    assert _months_words(36) == "36 months (3 years)"
    assert _months_words(35) == "35 months (2 years and 11 months)"
    assert _months_words(13) == "13 months (1 year and 1 month)"


def test_show_reports_the_form(make):
    """A fixed date with no will: not locked, no period keys."""
    s = make(mode="advanced")
    out = returned(s.cli("delivery_show"))
    assert (out["form"], out["locked"]) == ("fixed", False)
    assert out["period_months"] is None and out["period"] is None
    assert out["period_start"] is None


def test_choose_the_period_then_change_n(make):
    """No will in force: ``bal_delivery_form form=period months=36`` dates
    the will today + 36 months; ``bal_delivery_set --months 24`` moves it
    to today + 24 months; ``bal_delivery_show`` says so."""
    s = make(mode="advanced")
    out = returned(s.cli("delivery_form", "period", months=36))
    assert out["form"] == "period" and out["period_months"] == 36
    assert out["period"] == "36 months (3 years)"
    assert out["date"] == "2029-10-10" and out["locked"] is False
    assert out["period_start"] == dated(fk.ts(2026, 10, 10))
    assert out["message"].startswith(
        "Period of 36 months (3 years): the delivery date is 2029-10-10.")
    out = returned(s.cli("delivery_set", months=24))
    assert (out["period_months"], out["date"]) == (24, "2028-10-10")
    assert "Period of 24 months (2 years): the delivery date is 2028-10-10." in (
        out["message"])
    # Back to a fixed date chosen on the command line.
    out = returned(s.cli("delivery_form", "fixed", date="2031-01-01"))
    assert (out["form"], out["date"], out["period_months"]) == (
        "fixed", "2031-01-01", None)
    assert out["message"].startswith("Fixed delivery date: 2031-01-01.")


def test_form_change_refused_while_a_will_is_in_force(make):
    """A fixed will signed and sent: the period form is refused with the
    plan's steps; nothing is written."""
    s = make(mode="advanced")
    s.check()
    assert returned(s.cli("delivery_show"))["locked"] is True
    msg = raised(s.cli("delivery_form", "period", months=36))
    assert msg == (
        "This will uses a fixed delivery date. The form cannot change while "
        "a will is in force: run bal_will_invalidate (on-chain fee), sign and "
        "broadcast it, wait until it is confirmed, then bal_delivery_form "
        "form=period months=36, then bal_will_prepare.")
    assert returned(s.cli("delivery_show"))["form"] == "fixed"


def test_period_will_n_waits_for_the_renewal_and_date_is_refused(make):
    """A period will signed and sent: ``--months 3`` is stored for the next
    renewal (the date stays); ``--date`` is refused (no fixed date in the
    period form); the form change to fixed is refused while locked."""
    s = make(mode="advanced", period_months=2, threshold="-30d")
    s.check()
    out = returned(s.cli("delivery_set", months=3))
    assert (out["period_months"], out["date"]) == (3, "2026-12-10")
    assert "applies at the next renewal (no fee now)" in out["message"]
    assert "--months N" in raised(s.cli("delivery_set", date="2031-01-01"))
    msg = raised(s.cli("delivery_form", "fixed", date="2031-01-01"))
    assert msg.startswith("This will uses a renewable period.")
    assert "bal_delivery_form form=fixed date=YYYY-MM-DD" in msg


@pytest.mark.parametrize("args, kwargs, text", [
    (("weekly",), {}, "Give form=fixed or form=period"),
    (("period",), {}, "Give months=N (1 to 240)."),
    (("period",), {"months": 0}, "Not a period: 0"),
    (("period",), {"months": 241}, "Not a period: 241"),
    (("period",), {"date": "2031-01-01"}, "The period form has no fixed date"),
    (("fixed",), {"months": 36}, "months is only for form=period."),
    (("fixed",), {"date": "1054d"}, "A period such as 1054d is not accepted"),
])
def test_bad_form_arguments(make, args, kwargs, text):
    """Every refusal is a message (exit 1), and nothing is written."""
    s = make(mode="advanced")
    before = dict(s.wallet.db.get_dict("will_settings"))
    assert text in raised(s.cli("delivery_form", *args, **kwargs))
    assert dict(s.wallet.db.get_dict("will_settings")) == before


def test_months_refused_for_a_fixed_date(make):
    """``--months`` is only for the period form."""
    s = make(mode="advanced")
    assert "--months is only for the period form" in raised(
        s.cli("delivery_set", months=12))


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
