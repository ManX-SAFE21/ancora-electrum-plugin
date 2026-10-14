"""One rule for "the will in force" everywhere (#135 follow-up).

Owner's real wallet: a will sent for 31 Dec, then (anticipation) 30 Dec,
then 29 Dec sent to fewer will-executors; then an heir RENAMED (same
address, share and date) and one more will-executor, Check, Cancel at
"Sign your will". The Will tab's List said the 29 Dec will is in force,
the summary box and the Check window said "In force now: 30 December"
with "the will sent before stays in force: 30 December", and the wizard
compared with 29 Dec.

Root cause: the summary read the will in force from
``Will.sent_still_executable``, which leaves out the VALID items. The
rebuild after the rename made the same transactions for the
will-executors that already held the 29 Dec will (same address, amount and
date: same txid), so those copies, already sent and verified, became VALID
items of the prepared will and dropped out of that list; the summary then
named the older 30 Dec will. The List, the Future view, the wizard and the
"What changes" box use the #128 rule (``will_state.is_in_force``: signed
and/or sent, VALID or REPLACED, still executable; the earliest delivery is
what the will-executors execute first): 29 Dec, the right answer for the
funds. The summary now takes its date from that same rule.

Also: the Check said 'Heir "bob" was removed' and listed the heirs with the
old name; now "Heir renamed: bob → bob renamed" and the current names.

``tests/flowkit.py`` wallets: three fake will-executors with their own fee
addresses (two hold the 29 Dec will, none ever a single one), the test
password, a fixed clock; the wallet counts as synchronized.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_in_force_one_rule.py
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

import flowkit as fk
from PyQt6.QtWidgets import QApplication

from bal.core import future as core_future
from bal.core import will_versions
from bal.core.summary import will_summary
from bal.core.will import Will
from bal.engine import context as engine_context

_APP = QApplication.instance() or QApplication(sys.argv)

TEST_PASSWORD = "flowkit-test"
DEC31 = fk.ts(2030, 12, 31)
DEC30 = fk.ts(2030, 12, 30)
DEC29 = fk.ts(2030, 12, 29)
WE2 = "https://we2.test"


@pytest.fixture
def owner_case(monkeypatch):
    """The owner's sequence, ending with the Check cancelled at "Sign your
    will" after the rename; returns ``(session, check window)``."""
    fk.ensure_event_loop()
    s = fk.Session(monkeypatch, mode="basic", password=TEST_PASSWORD, locktime=DEC31)
    s.world.add_executor()
    monkeypatch.setattr(s.wallet, "is_up_to_date", lambda: True)
    try:
        s.check()                                   # 31 Dec, 3 copies
        s.set_heirs(alice="50%", bob="50%")
        s.check()                                   # 30 Dec (anticipation)
        s.world.executors[WE2]["selected"] = False
        s.set_fee(14)
        s.check()                                   # 29 Dec, 2 copies
        s.world.executors[WE2]["selected"] = True
        share = s.bw.heirs["bob"][1]
        del s.bw.heirs["bob"]
        s.bw.heirs.save()
        s.bw.heirs["bob renamed"] = [fk.HEIR_ADDR["bob"], share,
                                     s.bw.will_settings["locktime"]]
        dlg = s.check(cancel_on=("sign your will",))
        yield s, dlg
    finally:
        s.close()
        fk.stop_event_loop()


def test_one_date_everywhere(owner_case):
    """List (version in force), Future view, wizard / "What changes"
    (``in_force_delivery``) and the summary all say 29 Dec; the summary no
    longer names the older 30 Dec will."""
    s, _dlg = owner_case
    bw = s.bw
    history = will_versions.build_history(bw.willitems, bw.wallet, True)
    current = history.by_vid()[history.current]
    assert current.locktime == DEC29
    assert will_versions.in_force_delivery(history, bw.willitems) == DEC29
    assert engine_context.in_force_delivery(bw) == DEC29
    future = core_future.build_future(bw.willitems, bw.will_settings, True,
                                      now=s.clock.time(), wallet=bw.wallet,
                                      history=history)
    assert future.delivery == DEC29
    # The old summary input still lists the 30 Dec copies (they are held
    # and executable, but LATER): the in-force date must not come from it.
    still = Will.sent_still_executable(bw.willitems, bw.wallet)
    assert min(lt for _w, lt, _u in still) == DEC30
    summary = will_summary(bw.willitems, len(bw.heirs), bw.will_settings, True,
                           now=s.clock.time(), still_active=still, long_dates=True,
                           in_force=engine_context.in_force_delivery(bw))
    assert summary["state"] == "to_sign"
    labels = [r["label"] for r in summary["rows"]]
    assert "In force now" not in labels and "After signing" not in labels
    assert summary["note"] is None
    assert summary["rows"][0]["strong"] == "29 December 2030, 00:00 UTC"


def test_check_window_says_the_same(owner_case):
    """The Check window's "Your will now" has no 30 Dec, the change row
    names the rename, the heirs box uses the current names."""
    _s, dlg = owner_case
    snap = dlg._report().snapshot()
    assert "30 December" not in (snap["will_now"] or "")
    changes = snap["boxes"]["changes"]
    # The rename is recognised up front (names only, #135 ZIP test), then
    # the new will-executor needs the new will.
    assert ("Heir renamed: bob → bob renamed: only the name changes, the will "
            "stays the same.") in changes
    assert not any("was removed" in c for c in changes)
    names = [name for name, _sats, _pct in snap["heirs"]]
    assert names == ["alice", "bob renamed"]
    # The box matches heirs by ADDRESS: never "removed bob, added bob
    # renamed"; the rename written up front by the Check is its own row.
    rows = [list(r) for r in snap["changes"]]
    # ONE box (owner): the rename (written up front) and the new executor.
    assert rows == [
        ["Heirs", "renamed: bob → <b>bob renamed</b> (only the name changes, already saved: no need to sign)", None],
        ["Will-executors", "from 2 to <b>3</b>: added <b>we2.test</b>", None]]


def test_without_in_force_the_old_input_is_kept():
    """A caller that does not pass ``in_force`` keeps the older rule (the
    earliest of ``still_active``), e.g. the report's block reason."""
    from bal.core import summary as core_summary

    assert core_summary._in_force_date([(None, 5, "u"), (None, 3, "v")], None) == 3
    assert core_summary._in_force_date([(None, 5, "u")], 7) == 7
    assert core_summary._in_force_date([], None) is None
