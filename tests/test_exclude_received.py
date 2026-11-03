"""A will-executor excluded after it received the will (CHANGELOG #178).

Owner's ZIP test of #177 (2026-10-21, a will with three delivery dates and
five will-executors): ``we.divinaly.support`` accepted its copies, then
never answered the check in time. "No answer in time" keeps a copy sent
and to be verified (#147), so after "Exclude" (which keeps the copies a
will-executor got: it holds them, #129) every Check asked it again, for
ever: "received, verifying..." and "Copies verified: 0 of 1". And Exclude
was the plugin's deselection (#137): the will-executor came back selected
as soon as it answered the ping, which divinaly does.

Owner's decision: the copies stay in the will, the Check no longer asks a
will-executor that is not selected, and the summary calls it "excluded:
has the will, no longer verified" (not counted in "x of N"); Exclude lasts
until the user selects it again (``test_executor_selection.py``). The
right click "check" and the command line with named transactions still
ask it: the user asks for them.

Owner's ZIP tests of #178 (2026-10-22): "excluded" also when its copies
were verified before (it read "received and verified"); Exclude keeps
every copy sent at least once (divinaly's 25 copies with a 503 to the
check were dropped though it held them); the List row of its copy reads
"In force, excluded", grey (it read "In force, to be verified").

``tests/flowkit.py`` wallets with three fake will-executors, each with its
own fee address, the will with three delivery dates of
``test_multiverse_chain_rebuild.py`` (3 + 9 + 27 items), the test
password, a fixed clock. ``we2`` gets its copies and never answers the
check in time.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_exclude_received.py
"""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

import test_multiverse_chain_rebuild as chain
from test_multiverse_chain_rebuild import _level, fk

from bal.core.report import report_block_reason
from bal.core.summary import executor_states, will_summary
from bal.core.will import Will
from bal.core.willexecutors import Willexecutors
from bal.engine.verify import verify_targets
from bal.gui.qt.theme import EXCLUDED_COLOR

# The sessions of the chain tests (three will-executors, synchronized).
make = chain.make

SILENT = "https://we2.test"


def _silent_check(s, monkeypatch):
    """``SILENT`` never answers the check in time (#147: NO_ANSWER); the
    others answer as flowkit's world does. Returns the list of the copies
    of ``SILENT`` asked, one list per verification."""
    asked = []
    original = Willexecutors.check_transactions_parallel

    def check(items, *, on_each=None, on_timeout=None, on_tick=None, **kw):
        """The world's check, with no answer in time from ``SILENT``."""
        items = list(items)
        silent = [(wid, url) for wid, url in items if url == SILENT]
        asked.append(sorted(wid for wid, _url in silent))
        for wid, url in silent:
            on_timeout(wid, url)
        return original([(wid, url) for wid, url in items if url != SILENT],
                        on_each=on_each, on_timeout=on_timeout,
                        on_tick=on_tick, **kw)
    monkeypatch.setattr(Willexecutors, "check_transactions_parallel",
                        staticmethod(check))
    return asked


def _copies(s):
    """The VALID copies of ``SILENT``."""
    return {k: w for k, w in s.bw.willitems.items()
            if w.get_status("VALID") and (w.we or {}).get("url") == SILENT}


def _summary(s):
    """The Will tab summary with the selection rule."""
    return will_summary(s.bw.willitems, 2, s.bw.will_settings, True,
                        now=fk.FIXED_NOW,
                        still_active=s.bw._still_active_for_summary(),
                        long_dates=True, selected=s.bw.selected_willexecutors())


def _row(smr, label):
    """One line of the summary."""
    return next(r for r in smr["rows"] if r["label"] == label)


def _excluded_after_no_answer(s, monkeypatch):
    """The owner's sequence: the will is built, signed and sent; ``SILENT``
    got its 13 copies but did not answer the check; then "Exclude"."""
    asked = _silent_check(s, monkeypatch)
    chain.three_dates(s)
    s.check()
    copies = _copies(s)
    assert len(copies) == 13
    assert all(w.get_status("PUSHED") and not w.get_status("CHECKED")
               for w in copies.values())
    assert asked and any(asked)
    assert s.bw.exclude_willexecutor(SILENT)
    asked.clear()
    return asked


def test_excluded_is_no_longer_asked_and_reads_excluded(make, monkeypatch):
    """After Exclude: the next Check asks nothing of it and sends it
    nothing; its copies stay in the will (it holds them); the Check window
    says "excluded: has the will, no longer verified", Verification 2/2;
    the summary is green, "2 of 2"; the report is available; one more
    Check asks nothing."""
    s = make()
    asked = _excluded_after_no_answer(s, monkeypatch)
    pushed = len(s.world.pushed)
    snap = s.check()._report().snapshot()
    assert not any(asked), asked
    assert not [u for u, _w, _r in s.world.pushed[pushed:] if u == SILENT]
    assert len(_copies(s)) == 13
    executors = dict(snap["executors"])
    assert executors[SILENT][0] == "excluded"
    steps = {name: (state, word) for name, state, word in snap["steps"]}
    assert steps["verify"] == ("ok", "2/2")
    smr = _summary(s)
    assert smr["state"] == "ok"
    # #182: then the time of the last check.
    assert _row(smr, "Will-Executor")["text"].startswith(
        "2 of 2 received and verified it · last check ")
    assert smr["deselected"] == 0
    assert report_block_reason(smr, s.bw.willitems,
                               s.bw.selected_willexecutors()) is None
    s.user.asked.clear()
    s.check()
    assert s.user.asked == []
    assert not any(asked)


def test_exclude_keeps_the_copies_with_a_failed_check(make, monkeypatch):
    """Owner's ZIP test (2026-10-22 07:14 UTC): divinaly accepted its
    copies, did not answer the check of some in time and answered "503
    Service Temporarily Unavailable" to the others (CHECK_FAIL, which
    clears PUSHED). Exclude dropped those: the plugin forgot copies the
    will-executor holds. Now all 13 stay; nothing is sent to it again."""
    s = make()
    original = Willexecutors.check_transactions_parallel

    def check(items, *, on_each=None, on_timeout=None, on_tick=None, **kw):
        """``SILENT``: no answer in time for half of its copies, an error
        (503) for the other half; the others as the world answers."""
        items = list(items)
        silent = sorted(wid for wid, url in items if url == SILENT)
        for i, wid in enumerate(silent):
            if i % 2:
                on_each(wid, SILENT, None, ConnectionError("503 (flowkit)"))
            else:
                on_timeout(wid, SILENT)
        return original([(wid, url) for wid, url in items if url != SILENT],
                        on_each=on_each, on_timeout=on_timeout,
                        on_tick=on_tick, **kw)
    monkeypatch.setattr(Willexecutors, "check_transactions_parallel",
                        staticmethod(check))
    chain.three_dates(s)
    s.check()
    copies = _copies(s)
    assert len(copies) == 13
    failed = [k for k, w in copies.items() if w.get_status("CHECK_FAIL")]
    assert failed and len(failed) < 13
    assert all(not copies[k].get_status("PUSHED") for k in failed)
    assert s.bw.exclude_willexecutor(SILENT)
    assert sorted(_copies(s)) == sorted(copies)
    pushed = len(s.world.pushed)
    snap = s.check()._report().snapshot()
    assert sorted(_copies(s)) == sorted(copies)
    assert not [u for u, _w, _r in s.world.pushed[pushed:] if u == SILENT]
    assert dict(snap["executors"])[SILENT][0] == "excluded"
    steps = {name: (state, word) for name, state, word in snap["steps"]}
    assert steps["verify"] == ("ok", "2/2")


def test_excluded_after_it_verified_reads_excluded(make):
    """Owner's ZIP test of #178 (2026-10-22): divinaly had copies verified
    before; excluded, the Check said "received and verified", which is no
    longer true once nobody asks it. Now: "excluded", Verification 2/2,
    the summary "2 of 2", nothing asked of it, nothing sent to it."""
    s = make()
    chain.three_dates(s)
    s.check()
    assert all(w.get_status("CHECKED") for w in _copies(s).values())
    assert s.bw.exclude_willexecutor(SILENT)
    pushed = len(s.world.pushed)
    snap = s.check()._report().snapshot()
    assert dict(snap["executors"])[SILENT][0] == "excluded"
    steps = {name: (state, word) for name, state, word in snap["steps"]}
    assert steps["verify"] == ("ok", "2/2")
    assert _row(_summary(s), "Will-Executor")["text"].startswith(
        "2 of 2 received and verified it · last check ")
    # flowkit's world.verified holds the asks of the last Check only.
    assert not [u for u, _w in s.world.verified if u == SILENT]
    assert not [u for u, _w, _r in s.world.pushed[pushed:] if u == SILENT]
    assert len(_copies(s)) == 13


def test_every_executor_excluded_verification_not_needed(make):
    """Every will-executor deselected after it received the will (by hand
    in the Will-Executors window: Exclude never leaves none): all
    "excluded", nobody asked, and the Verification step says "not needed"
    (it stayed grey with no word, as if never reached); the will stays in
    force (#134: with nobody selected the summary reads as before)."""
    s = make()
    chain.three_dates(s)
    s.check()
    for we in s.world.executors.values():
        we["selected"] = False
    snap = s.check()._report().snapshot()
    assert {state for _url, (state, _sec) in snap["executors"]} == {"excluded"}
    steps = {name: (state, word) for name, state, word in snap["steps"]}
    assert steps["verify"] == ("skip", "not needed")
    assert snap["result"][0] == "ok"
    # flowkit's world.verified holds the asks of the last Check only.
    assert s.world.verified == []


def test_selected_again_it_is_asked_again(make, monkeypatch):
    """The user selects it again: the next Check asks its 13 copies."""
    s = make()
    asked = _excluded_after_no_answer(s, monkeypatch)
    s.check()
    assert not any(asked)
    Willexecutors.set_user_choice(s.bw.bal_plugin, [SILENT], True)
    s.world.executors[SILENT]["selected"] = True
    s.check()
    assert sorted(set(sum(asked, []))) == sorted(_copies(s))


def test_command_line_check_skips_it_unless_named(make, monkeypatch):
    """``check_transactions`` with no ids does what the Check does; with
    the ids of its copies it asks them (the user asks for them)."""
    s = make()
    asked = _excluded_after_no_answer(s, monkeypatch)
    s.cli("check_transactions")
    assert not any(asked)
    ids = sorted(_copies(s))[:2]
    s.cli("check_transactions", ids)
    assert sorted(sum(asked, [])) == ids


def test_window_texts(make, monkeypatch):
    """The texts the owner sees: the will-executors box and the Future
    view's legend say "excluded: has the will, no longer verified"; the
    List's row of the will in force names it with the deselected ones:
    "sent and verified 2 of 2 · 1 deselected" (no new text there)."""
    from test_gui_window_init_migration import real_window

    from bal.core import will_versions
    from bal.gui.qt.future_view import _executor_legend
    from bal.gui.qt.widgets import BuildReportView

    text = "excluded: has the will, no longer verified"
    assert BuildReportView.executor_text("excluded") == text
    legend = _executor_legend([SimpleNamespace(state="verified"),
                               SimpleNamespace(state="excluded")])
    assert any(text in part for _colour, part in legend)

    s = make()
    _excluded_after_no_answer(s, monkeypatch)
    s.check()
    bw = real_window(s, monkeypatch)
    bw.init_will()
    history = will_versions.build_history(dict(bw.willitems), bw.wallet)
    version = history.by_vid()[history.current]
    lst = bw.will_list_widget
    assert lst._version_texts(version)[2].endswith(
        "sent and verified 2 of 2 · 1 deselected")
    # Its own row in the List (owner's ZIP test: "In force, to be
    # verified", teal): "In force, excluded", grey. Its rows of an older
    # version are history: unchanged.
    lst.rebuild()
    will = bw.willitems
    root = next(k for k, w in will.items()
                if (w.we or {}).get("url") == SILENT and _level(k, will) == 1)
    row = lst._variant_row(root, will[root], in_force=True)
    assert row[lst.Columns.STATUS].text() == "In force, excluded"
    assert row[0].background().color().name() == EXCLUDED_COLOR
    old = lst._variant_row(root, will[root], in_force=False)
    assert old[lst.Columns.STATUS].text() != "In force, excluded"


def _item(url, *flags):
    """A will item with only the flags ``flags`` and the will-executor ``url``."""
    return SimpleNamespace(we={"url": url},
                           get_status=lambda f, _on=set(flags): f in _on)


def test_states_rule():
    """The rule on its own: a will-executor not selected that received
    the will is "excluded", verified before or not (owner's ZIP test:
    divinaly excluded read "received and verified"); never received stays
    "deselected" (#134); a selected one keeps its state; no selection
    rule: as before."""
    items = {
        "a": _item("https://a", "VALID", "COMPLETE", "PUSHED"),
        "b": _item("https://b", "VALID", "COMPLETE", "PUSHED", "CHECKED"),
        "c": _item("https://c", "VALID", "COMPLETE"),
        "d": _item("https://d", "VALID", "COMPLETE", "PUSHED"),
    }
    assert executor_states(items, {"https://d"}) == {
        "https://a": "excluded", "https://b": "excluded",
        "https://c": "deselected", "https://d": "to_verify"}
    assert executor_states(items)["https://a"] == "to_verify"
    assert executor_states(items)["https://b"] == "verified"


def test_verify_targets_rule():
    """``verify_targets``: with ``selected`` a copy of a will-executor not
    selected is not asked by the Check; the right click (recheck) asks
    it; no ``selected``: as before."""
    items = {
        "a": _item("https://a", "VALID", "COMPLETE", "PUSHED"),
        "d": _item("https://d", "VALID", "COMPLETE", "PUSHED"),
    }
    assert verify_targets(items, selected={"https://d"})[0] == [("d", "https://d")]
    assert sorted(verify_targets(items)[0]) == [("a", "https://a"),
                                                ("d", "https://d")]
    targets, _skipped = verify_targets(items, ["a"], recheck=True,
                                       selected={"https://d"})
    assert targets == [("a", "https://a")]
    assert Will.needs_server_check(items["a"])
