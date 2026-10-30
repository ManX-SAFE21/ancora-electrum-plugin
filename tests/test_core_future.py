"""The data of the Future view of the Will tab (CHANGELOG #131): ``bal.core.future``.

The Will tab can show the will on a timeline ("Future"): today, the Check
Alive, the delivery to the heirs with the time left in the #124 colour, the
will-executors, and the heirs with their REAL shares. Here, on realistic
wills made with ``tests/flowkit.py`` (three fake will-executors, each with
its own fee address; throw-away test wallets with the test password; fixed
clock 2026-10-10 12:00 UTC):

* the will in force: date, time left, colour, will-executors 3 of 3, real
  shares read from the transaction's outputs (also when the shares entered
  sum to more than 100 %);
* BASIC with a fixed date (no Check Alive), ADVANCED (Check Alive), the
  period form (renewal period);
* only a draft, a draft over the will in force, no will at all;
* a will-executor that did not receive the will;
* multiverse on (two instalments, no crash);
* the time-left texts on their own.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_core_future.py
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

import flowkit as fk
from PyQt6.QtWidgets import QApplication

from bal.core import future as cf
from bal.core.heirs import HEIR_REAL_AMOUNT

# Kept at module level: flowkit's window needs a Qt application.
_APP = QApplication.instance() or QApplication(sys.argv)

# Test value: the password of the throw-away test wallets.
TEST_PASSWORD = "flowkit-test"
D = fk.ts(2027, 2, 27, 22)
SIGN = ("sign your will",)
URLS = ["https://we0.test", "https://we1.test", "https://we2.test"]


@pytest.fixture
def make(monkeypatch):
    """flowkit sessions with three will-executors, synchronized."""
    fk.ensure_event_loop()
    made = []

    def _make(mode="basic", **kw):
        """One session (``fk.Session`` arguments)."""
        kw.setdefault("password", TEST_PASSWORD)
        kw.setdefault("fee", 21)
        kw.setdefault("locktime", D)
        kw.setdefault("threshold", "-30d")
        s = fk.Session(monkeypatch, mode=mode, **kw)
        s.world.add_executor()
        # The flowkit wallet is offline: synchronized for the #128 rules.
        monkeypatch.setattr(s.wallet, "is_up_to_date", lambda: True)
        made.append(s)
        return s

    yield _make
    for s in made:
        s.close()
    fk.stop_event_loop()


def _future(s):
    """The Future data of the session's will, at the scenario's clock."""
    return cf.build_future(
        s.bw.willitems, s.bw.will_settings, s.plugin.is_basic_mode(),
        now=s.clock.now, warn_months=s.plugin.warning_months(), wallet=s.wallet,
    )


def _shares(f, index=0):
    """``{name: rounded percent}`` of instalment ``index``."""
    return {h.name: round(h.percent, 2) for h in f.deliveries[index].heirs}


def test_will_in_force_basic_fixed_date(make):
    """BASIC, fixed date, signed and sent by the Check: in force, delivery
    2027-02-27 22:00 UTC, 4 months and 17 days from 2026-10-10, red (less
    than 6 months), no Check Alive, 3 of 3 will-executors verified."""
    s = make()
    s.check()
    f = _future(s)
    assert f.state == cf.STATE_IN_FORCE
    assert f.title == "Your will is in force"
    assert f.delivery == D and len(f.deliveries) == 1
    assert f.time_left == "(in 4 months and 17 days)"
    assert f.level == "red"
    assert f.check_alive is None and f.period_months is None
    assert [(m.url, m.state) for m in f.executors] == [(u, "verified") for u in URLS]
    assert f.executors_text == "3 of 3 received and verified it"
    assert f.note is None and f.draft_locktime is None


def test_real_shares_are_the_transaction_outputs(make):
    """The amounts are what the transaction pays to each heir's address
    (equal to the resolved amount stored with the heir); the share is over
    what all heirs receive (fee outputs and miner fee left out)."""
    s = make()
    s.check()
    f = _future(s)
    item = next(
        w for w in s.bw.willitems.values() if (w.we or {}).get("url") == URLS[0])
    paid = {o.address: o.value for o in item.tx.outputs()}
    sats = {h.name: h.sats for h in f.deliveries[0].heirs}
    assert sats == {
        "alice": paid[fk.HEIR_ADDR["alice"]], "bob": paid[fk.HEIR_ADDR["bob"]]}
    assert sats["alice"] == item.heirs["alice"][HEIR_REAL_AMOUNT]
    assert _shares(f) == {"alice": 60.0, "bob": 40.0}


def test_real_shares_when_the_entered_shares_exceed_100(make):
    """alice 60 % + bob 50 % = 110 %: the will spends the whole wallet,
    so the real shares are 54.55 % and 45.45 %."""
    s = make()
    s.set_heirs(alice="60%", bob="50%")
    s.check()
    f = _future(s)
    shares = _shares(f)
    assert shares == {"alice": pytest.approx(54.55, abs=0.01),
                      "bob": pytest.approx(45.45, abs=0.01)}
    assert sum(h.percent for h in f.deliveries[0].heirs) == pytest.approx(100.0)


def test_advanced_has_the_check_alive(make):
    """ADVANCED, Check Alive 30 days before the delivery: 2027-01-28
    00:00 UTC (``resolve_date_to_check`` counts whole UTC days, as the will
    summary shows it)."""
    s = make(mode="advanced")
    s.check()
    f = _future(s)
    assert f.state == cf.STATE_IN_FORCE
    assert f.check_alive == fk.ts(2027, 1, 28)
    assert f.check_alive_passed is False
    assert f.period_months is None


def test_period_form_shows_the_renewal_period(make):
    """The period form (#127): 24 months from 2026-10-10, Check Alive 30
    days before; the renewal period is in the data."""
    s = make(mode="advanced", period_months=24)
    s.check()
    f = _future(s)
    assert f.state == cf.STATE_IN_FORCE
    assert f.delivery == fk.ts(2028, 10, 10)
    assert f.period_months == 24
    assert f.check_alive == fk.ts(2028, 10, 10) - 30 * fk.DAY
    assert f.level == "green"
    assert f.time_left == "(in 2 years)"


def test_only_a_draft(make):
    """The will was never signed (password window closed): no will in
    force, the draft's date is known, the summary's sentence says it."""
    s = make()
    s.check(cancel_on=SIGN)
    f = _future(s)
    assert f.state == cf.STATE_DRAFT_ONLY
    assert f.title == "No will in force"
    assert f.note == "Will prepared: it must be signed and sent"
    assert f.draft_locktime == D
    assert f.deliveries == [] and f.executors == []


def test_draft_over_the_will_in_force(make):
    """A fee change cancelled at the password (#128 case): the draft is
    not in force; the will sent before is what the view draws."""
    s = make()
    s.check()
    s.clock.travel(1)
    s.set_fee(22)
    s.check(closing=True, cancel_on=SIGN)
    f = _future(s)
    assert f.state == cf.STATE_IN_FORCE
    assert f.delivery == D
    assert f.draft_locktime is not None
    assert f.note == (
        "Until you sign and send it, the will sent before stays in force: "
        "delivery on 2027-02-27.")
    assert all(m.state == "verified" for m in f.executors)


def test_no_will(make):
    """Nothing built yet."""
    s = make()
    f = _future(s)
    assert f.state == cf.STATE_NONE
    assert f.title == "No will in force"
    assert f.note == "Until you create your will, nobody will receive your bitcoin."
    assert f.delivery is None


def test_an_executor_that_did_not_receive_it(make):
    """we2 does not answer: its square is 'failed', the count says so."""
    s = make()
    s.world.down.add(URLS[2])
    s.check()
    f = _future(s)
    assert f.state == cf.STATE_IN_FORCE
    states = {m.url: m.state for m in f.executors}
    assert states[URLS[0]] == states[URLS[1]] == "verified"
    assert states[URLS[2]] == "failed"
    assert f.executors_text == "2 of 3 verified it · 1 did not receive it"


def test_multiverse_two_instalments(make):
    """A table of deliveries (#168; BAL's hidden option before): alice at
    D, bob one year later. No crash; two instalments, earliest first;
    shares over both; the person's name, never the row "bob§2"."""
    s = make()
    s.set_instalments((D, {"alice": "60%", "bob": "0%"}),
                      (D + 365 * fk.DAY, {"alice": "0%", "bob": "40%"}))
    s.check()
    f = _future(s)
    assert f.state == cf.STATE_IN_FORCE
    assert [i.locktime for i in f.deliveries] == [D, D + 365 * fk.DAY]
    assert [h.name for h in f.deliveries[0].heirs] == ["alice"]
    assert [h.name for h in f.deliveries[1].heirs] == ["bob"]
    total = sum(h.percent for i in f.deliveries for h in i.heirs)
    assert total == pytest.approx(100.0)
    assert len(f.executors) == 3


def test_nothing_is_written(make):
    """Display only: the will items stay as they were."""
    s = make()
    s.check()
    before = {w: str(i.to_dict()) for w, i in s.bw.willitems.items()}
    _future(s)
    assert before == {w: str(i.to_dict()) for w, i in s.bw.willitems.items()}


def test_time_left_texts():
    """Calendar months then the days left; years and months beyond a year."""
    now = fk.ts(2026, 10, 10, 12)
    assert cf.time_left_text(fk.ts(2027, 2, 24, 22), now) == "(in 4 months and 14 days)"
    assert cf.time_left_text(fk.ts(2027, 2, 10), now) == "(in 4 months)"
    assert cf.time_left_text(fk.ts(2026, 10, 22), now) == "(in 11 days)"
    assert cf.time_left_text(fk.ts(2026, 11, 11), now) == "(in 1 month and 1 day)"
    assert cf.time_left_text(fk.ts(2027, 12, 10), now) == "(in 1 year and 2 months)"
    assert cf.time_left_text(fk.ts(2028, 10, 10), now) == "(in 2 years)"
    assert cf.time_left_text(now + 3600, now) == "(today)"
    # #202: by UTC day, not by 24 hours; never "0 days".
    late = fk.ts(2026, 10, 10, 23)
    assert cf.time_left_text(fk.ts(2026, 10, 10), late) == "(today)"
    assert cf.time_left_text(late + 3600, late) == "(in 1 day)"
    assert cf.time_left_text(late - fk.DAY, fk.ts(2026, 10, 10, 1)) == "(passed 1 day ago)"
    assert cf.time_left_text(now - 3 * fk.DAY, now) == "(passed 3 days ago)"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
