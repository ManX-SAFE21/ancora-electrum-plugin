"""
Tests for #156: one shared verification of the will with its will-executors.

Covers, with three fake will-executors (never the real ones):
  - ``verify_targets`` (E1): the Check and the command line ask the sent
    copies not verified yet; the right click (``recheck``) asks the sent
    copies, verified ones too; a copy never sent, no longer valid or with no
    will-executor is never asked, and is listed as skipped when it was
    chosen.
  - ``verify_will``: our copy -> VERIFIED (CHECKED); another copy or an
    error -> FAILED (CHECK_FAIL, no longer sent); no answer in time or the
    global deadline -> NO_ANSWER, the copy stays sent (#147); a verified
    copy asked again that fails is no longer verified; the callback sees
    every answer; no target, no request.
  - The callers: the right click skips the never-sent copies with a
    message and asks nothing when none was sent; it saves the answers
    (E2); the command line still limits the choice to the given txids.
  - No module outside ``bal/engine/verify.py`` asks the will-executors for
    the copies by itself.

No wallet, no network, no will-executor is contacted.

Run:
    source "$BAL_HOME/electrum/env/bin/activate"
    QT_QPA_PLATFORM=offscreen python3 tests/test_engine_verify.py
"""

import os
import re
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from PyQt6.QtWidgets import QApplication

# Kept at module level: an unreferenced application object is destroyed at
# once, and creating a widget afterwards aborts Python.
_APP = QApplication.instance() or QApplication(sys.argv)

from test_build_report import _item

from bal.cli.controller import BalController
from bal.core.willexecutors import Willexecutors
from bal.engine.verify import (
    FAILED,
    NO_ANSWER,
    VERIFIED,
    verify_targets,
    verify_will,
)
from bal.gui.qt import window as window_mod

ROOT = os.path.join(os.path.dirname(__file__), os.pardir, "bal")

WE0 = "https://we0.test"
WE1 = "https://we1.test"
WE2 = "https://we2.test"


def _will():
    """Five copies on three will-executors, each in a different state."""
    return {
        "sent": _item("COMPLETE", "PUSHED", url=WE0),
        "verified": _item("COMPLETE", "PUSHED", "CHECKED", url=WE1),
        "unsent": _item("COMPLETE", url=WE2),
        "replaced": _item("COMPLETE", "PUSHED", "REPLACED", url=WE0),
        "sent2": _item("COMPLETE", "PUSHED", url=WE2),
    }


def _parallel(answers, willitems, asked=None):
    """A check_transactions_parallel answering from *answers*.

    ``{wid: "same" | "other" | "error" | "no_answer" | "deadline"}``: our
    tx, another tx, an exception, every attempt timed out, the global
    deadline elapsed. *asked* collects the targets.
    """
    def fake(targets, *, on_each=None, on_timeout=None, on_tick=None, **kw):
        if asked is not None:
            asked.extend(targets)
        if on_tick is not None:
            on_tick()
        for wid, url in targets:
            answer = answers[wid]
            if answer == "same":
                on_each(wid, url, {"tx": str(willitems[wid].tx)}, None)
            elif answer == "other":
                on_each(wid, url, {"tx": "x"}, None)
            elif answer == "error":
                on_each(wid, url, None, ConnectionError("down"))
            elif answer == "no_answer":
                on_each(wid, url, Willexecutors.NO_ANSWER, None)
        for wid, url in targets:
            if answers[wid] == "deadline":
                on_timeout(wid, url)

    return fake


# ------------------------------------------------------------ the choice

def test_targets_check_and_cli_ask_the_sent_not_verified():
    """#182: the verified copy is asked again too (verified before #182:
    no time, so due); never the unsent nor the replaced one."""
    targets, skipped = verify_targets(_will())
    assert targets == [("sent", WE0), ("verified", WE1), ("sent2", WE2)]
    assert skipped == []


def test_targets_recheck_asks_every_copy_sent_once():
    will = _will()
    targets, skipped = verify_targets(will, list(will), recheck=True)
    assert targets == [("sent", WE0), ("verified", WE1),
                       ("replaced", WE0), ("sent2", WE2)]
    # Only the copy never sent is skipped, and said.
    assert skipped == ["unsent"]


def test_targets_recheck_in_force_copy_while_a_new_will_is_prepared():
    """Owner's case (ZIP test 2026-10-16): after Prepare, the copies in
    force are marked replaced (no longer VALID) but the will-executors hold
    them; the right click "check" on one of them asks it, it is not "not
    sent yet". The new draft is skipped."""
    will = {
        "in_force": _item("COMPLETE", "PUSHED", "CHECKED", url=WE0),
        "draft": _item(url=WE1),
    }
    will["in_force"].set_status("REPLACED")
    assert not will["in_force"].get_status("VALID")
    targets, skipped = verify_targets(will, ["in_force"], recheck=True)
    assert targets == [("in_force", WE0)] and skipped == []
    targets, skipped = verify_targets(will, list(will), recheck=True)
    assert targets == [("in_force", WE0)] and skipped == ["draft"]
    # The Check and the command line still ask only the VALID ones.
    assert verify_targets(will) == ([], [])


def test_targets_recheck_asks_a_copy_found_not_received():
    """A copy a previous check found missing (CHECK_FAIL, no longer PUSHED)
    or whose sending was refused (PUSH_FAIL) can be asked again."""
    will = {
        "missing": _item("COMPLETE", "CHECK_FAIL", url=WE0),
        "refused": _item("COMPLETE", "PUSH_FAIL", url=WE1),
        "signed": _item("COMPLETE", url=WE2),
    }
    assert not will["missing"].get_status("PUSHED")
    targets, skipped = verify_targets(will, list(will), recheck=True)
    assert targets == [("missing", WE0), ("refused", WE1)]
    assert skipped == ["signed"]


def test_targets_limited_to_the_given_ids():
    will = _will()
    targets, skipped = verify_targets(will, ["sent2", "unsent"])
    assert targets == [("sent2", WE2)]
    assert skipped == ["unsent"]


def test_targets_no_executor_is_skipped():
    will = {"a": _item("COMPLETE", "PUSHED", url=WE0)}
    will["a"].we = None
    assert verify_targets(will, ["a"], recheck=True) == ([], ["a"])


# ------------------------------------------------------------- the asking

def test_verify_records_every_outcome():
    will = {
        "a": _item("COMPLETE", "PUSHED", url=WE0),
        "b": _item("COMPLETE", "PUSHED", url=WE1),
        "c": _item("COMPLETE", "PUSHED", url=WE2),
        "d": _item("COMPLETE", "PUSHED", url=WE0),
        "e": _item("COMPLETE", "PUSHED", url=WE1),
    }
    seen = []
    targets = [(wid, w.we["url"]) for wid, w in will.items()]
    fake = _parallel({"a": "same", "b": "other", "c": "error",
                      "d": "no_answer", "e": "deadline"}, will)
    with patch.object(Willexecutors, "check_transactions_parallel", fake):
        out = verify_will(will, targets,
                          on_answer=lambda *a: seen.append((a[0], a[3], a[4])))
    assert out == {"a": VERIFIED, "b": FAILED, "c": FAILED,
                   "d": NO_ANSWER, "e": NO_ANSWER}
    assert will["a"].get_status("CHECKED") and will["a"].get_status("PUSHED")
    for wid in ("b", "c"):
        assert will[wid].get_status("CHECK_FAIL"), wid
        assert not will[wid].get_status("PUSHED"), wid
    for wid in ("d", "e"):
        assert will[wid].get_status("PUSHED"), wid
        assert not will[wid].get_status("CHECK_FAIL"), wid
    # timed_out only for the global deadline.
    assert seen == [("a", VERIFIED, False), ("b", FAILED, False),
                    ("c", FAILED, False), ("d", NO_ANSWER, False),
                    ("e", NO_ANSWER, True)]


def test_a_verified_copy_that_fails_is_no_longer_verified():
    will = {"v": _item("COMPLETE", "PUSHED", "CHECKED", url=WE1)}
    fake = _parallel({"v": "other"}, will)
    with patch.object(Willexecutors, "check_transactions_parallel", fake):
        out = verify_will(will, [("v", WE1)])
    assert out == {"v": FAILED}
    assert will["v"].get_status("CHECK_FAIL")
    assert not will["v"].get_status("CHECKED")
    assert not will["v"].get_status("PUSHED")


def test_a_verified_copy_with_no_answer_is_no_longer_verified():
    """#182 (owner): "no answer to the check", still sent, not sent again
    (before #182 it stayed verified)."""
    will = {"v": _item("COMPLETE", "PUSHED", "CHECKED", url=WE1)}
    fake = _parallel({"v": "deadline"}, will)
    with patch.object(Willexecutors, "check_transactions_parallel", fake):
        assert verify_will(will, [("v", WE1)]) == {"v": NO_ANSWER}
    assert not will["v"].get_status("CHECKED")
    assert will["v"].get_status("PUSHED") and not will["v"].get_status("CHECK_FAIL")


def test_no_target_no_request():
    fake = MagicMock()
    with patch.object(Willexecutors, "check_transactions_parallel", fake):
        assert verify_will({}, []) == {}
    fake.assert_not_called()


# ------------------------------------------------------------- the callers

def _window(will):
    """A BalWindow stand-in for the right click "check"."""
    host = MagicMock()
    host.willitems = will
    host.check_transactions_task = (
        lambda w, targets=None: window_mod.BalWindow.check_transactions_task(
            host, w, targets))
    return host


def test_right_click_with_nothing_sent_asks_nothing():
    will = {"u1": _item("COMPLETE", url=WE0), "u2": _item("COMPLETE", url=WE1)}
    host = _window(will)
    with patch.object(window_mod, "BalWaitingDialog") as wd:
        window_mod.BalWindow.check_transactions(host, will)
    wd.assert_not_called()
    host.show_message.assert_called_once_with(
        "2 selected transaction(s) not sent yet: nothing to verify.")
    assert not will["u1"].get_status("CHECK_FAIL")


def test_right_click_asks_the_sent_saves_and_says_the_skipped(monkeypatch):
    will = {
        "s0": _item("COMPLETE", "PUSHED", url=WE0),
        "s1": _item("COMPLETE", "PUSHED", "CHECKED", url=WE1),
        "u2": _item("COMPLETE", url=WE2),
    }
    host = _window(will)
    asked = []
    monkeypatch.setattr(window_mod.time, "sleep", lambda s: None)
    fake = _parallel({"s0": "same", "s1": "same"}, will, asked)

    def waiting(parent, msg, task, on_success, on_failure, exe=False):
        """Run the task at once, then its success callback."""
        dialog = MagicMock()
        dialog._stopping = False
        host.waiting_dialog = dialog
        dialog.exe = lambda: on_success(task())
        return dialog

    with patch.object(Willexecutors, "check_transactions_parallel", fake), \
            patch.object(window_mod, "BalWaitingDialog", side_effect=waiting):
        window_mod.BalWindow.check_transactions(host, will)
    assert asked == [("s0", WE0), ("s1", WE1)]
    assert will["s0"].get_status("CHECKED") and will["s1"].get_status("CHECKED")
    host.save_willitems.assert_called_once()
    host.show_message.assert_called_once_with(
        "1 selected transaction(s) not sent yet: nothing to verify.")


def test_cli_limits_to_the_txids_and_saves():
    will = _will()
    saved = []
    host = SimpleNamespace(willitems=will, save_willitems=lambda: saved.append(1))
    asked = []
    fake = _parallel({"sent": "same", "sent2": "same", "verified": "same"},
                     will, asked)
    with patch.object(Willexecutors, "check_transactions_parallel", fake):
        out = BalController.check_transactions(host, ["sent2", "verified", "unsent"])
    # #182: the verified copy is asked again too.
    assert asked == [("verified", WE1), ("sent2", WE2)]
    assert out["sent2"]["checked"] and saved == [1]


# --------------------------------------------------- one copy of the asking

def _code_lines(path):
    """The lines of *path* without comments."""
    with open(path, encoding="utf-8") as f:
        for line in f:
            stripped = line.split("#", 1)[0]
            if stripped.strip():
                yield stripped


def test_no_other_copy_of_the_verification():
    """Only the engine asks the will-executors for the copies (in parallel).

    ``check_transaction`` (one copy) is still used by the sending, to check
    an "already present" copy (``bal/engine/push.py``).
    """
    pattern = re.compile(r"\b(check_transactions_parallel|set_check_willexecutor)\s*\(")
    found = set()
    for folder, _dirs, files in os.walk(ROOT):
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(folder, name)
            rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
            for line in _code_lines(path):
                if pattern.search(line) and "def " not in line:
                    found.add(rel)
    # push.py records the answer of an "already present" copy (#155).
    assert found == {"engine/verify.py", "engine/push.py"}, found


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn) and "monkeypatch" not in fn.__code__.co_varnames:
            fn()
            print("ok", name)
