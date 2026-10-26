"""The excluded will-executor that still holds a copy (#190).

Owner's rule (study of 2026-10-24, point 4): a will-executor excluded, or
removed from the list, that still holds a copy of the will in force is
said in the summary, permanently, with what it means. Before, after an
Exclude the summary no longer counted it and nothing said it still holds
an executable will: a copy sent cannot be recalled.

The note (text approved by the owner, 2026-10-25), grey, the first
sentence in bold: "<host> is excluded but still holds a copy of your
will, and a will-executor cannot be asked to delete it." / "If you change
your will, the new one is dated one day earlier: when the other
will-executors publish it, the network executes it first and the old copy
is no longer valid." / "Only an invalidation on the blockchain cancels the
old copy too." Shown by the Will tab's summary, the Check window and the
PDF report; gone when no copy of it is executable any more or when it is
selected again.

``tests/flowkit.py`` wallets with three fake will-executors, each with its
own fee address (the sessions of ``test_multiverse_chain_rebuild.py``),
the test password, a fixed clock.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_excluded_holder_note.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

import test_multiverse_chain_rebuild as chain
from test_multiverse_chain_rebuild import fk

from bal.core import report as core_report
from bal.core import summary as core_summary
from bal.core.summary import (
    excluded_holders,
    excluded_holders_html,
    excluded_holders_paragraphs,
    will_summary,
)
from bal.core.willexecutors import Willexecutors
from bal.engine import context
from bal.gui.qt import report_pages as rp
from bal.gui.qt.widgets import WillSummaryWidget

make = chain.make

HELD = "https://we2.test"
LEAD = ("we2.test is excluded but still holds a copy of your will, and a "
        "will-executor cannot be asked to delete it.")
MIDDLE = ("If you change your will, the new one is dated one day earlier: "
          "when the other will-executors publish it, the network executes it "
          "first and the old copy is no longer valid.")
LAST = "Only an invalidation on the blockchain cancels the old copy too."


# ------------------------------------------------------------ the rule

class _Item:
    """A will item: its will-executor URL and its status flags."""

    def __init__(self, url, *flags):
        self.we = {"url": url} if url else None
        self.flags = set(flags)

    def get_status(self, name):
        """True for the flags given."""
        return name in self.flags


def test_only_copies_sent_to_a_will_executor_not_selected_count():
    """Sent (PUSHED) or verified (CHECKED) and not selected: held. Signed
    and never sent, selected, or no will-executor (backup): not held. No
    selection rule: nothing."""
    in_force = {
        "a": _Item("https://a.test", "VALID", "COMPLETE", "PUSHED"),
        "b": _Item("https://b.test", "REPLACED", "COMPLETE", "PUSHED", "CHECKED"),
        "c": _Item("https://c.test", "VALID", "COMPLETE"),
        "d": _Item("https://d.test", "VALID", "COMPLETE", "PUSHED"),
        "e": _Item(None, "VALID", "COMPLETE"),
        "f": _Item("https://a.test", "VALID", "COMPLETE", "PUSHED"),
    }
    assert excluded_holders(in_force, {"https://d.test"}) == [
        "https://a.test", "https://b.test"]
    assert excluded_holders(in_force, None) == []
    assert excluded_holders({}, set()) == []


def test_the_note_one_and_more_will_executors():
    """Three sentences; the hosts by name; the first in bold in the rich
    text, the names escaped there."""
    assert excluded_holders_paragraphs([HELD]) == [LEAD, MIDDLE, LAST]
    two = excluded_holders_paragraphs(["https://a.test", "https://b.test"])
    assert two[0] == ("2 excluded will-executors (a.test, b.test) still hold a "
                      "copy of your will, and a will-executor cannot be asked "
                      "to delete it.")
    assert two[1:] == [MIDDLE, LAST]
    assert excluded_holders_paragraphs([]) == []
    assert excluded_holders_html([HELD]) == "<b>{}</b><br>{}<br>{}".format(
        LEAD, MIDDLE, LAST)
    assert excluded_holders_html([]) == ""
    assert "&lt;x&gt;" in excluded_holders_html(["<x>"])


def test_no_note_while_the_summary_is_busy():
    """A busy summary (no data yet) has no note."""
    smr = core_summary._with_held({"state": "busy"}, [HELD])
    assert smr["held"] == [] and smr["held_text"] == []
    assert core_summary._with_held(None, [HELD]) is None


# ------------------------------------------------------------ the flows

def _held(s):
    """The excluded will-executors holding a copy, as the GUI reads them."""
    return context.excluded_holders(s.bw, s.bw.selected_willexecutors())


def _summary(s):
    """The Will tab summary, as the window computes it."""
    selected = s.bw.selected_willexecutors()
    return will_summary(s.bw.willitems, 2, s.bw.will_settings, False,
                        now=fk.FIXED_NOW,
                        still_active=s.bw._still_active_for_summary(),
                        long_dates=True, selected=selected,
                        held=context.excluded_holders(s.bw, selected))


def _choose(s, url, selected):
    """The user selects or deselects ``url`` in the Will-Executor tab (the
    writes of ``BalWindow.exclude_willexecutor``)."""
    Willexecutors.set_user_choice(s.bw.bal_plugin, [url], selected)
    s.world.executors[url]["selected"] = selected
    if url in (s.bw.willexecutors or {}):     # loaded by a first build
        s.bw.willexecutors[url]["selected"] = selected
        Willexecutors.save(s.bw.bal_plugin, s.bw.willexecutors)


def _excluded(s):
    """The will in three deliveries signed, sent and verified, then
    ``HELD`` excluded."""
    chain._first(s, "three_dates")
    assert _held(s) == []
    assert s.bw.exclude_willexecutor(HELD)


def test_excluded_after_it_received_the_will(make):
    """Excluded after it received the will: the summary, the Check window
    and the Will tab's box say it holds a copy; the box stays green."""
    s = make()
    _excluded(s)
    assert _held(s) == [HELD]
    smr = _summary(s)
    assert smr["held"] == [HELD] and smr["held_text"] == [LEAD, MIDDLE, LAST]
    assert smr["kind"] == "ok"
    snap = s.check()._report().snapshot()
    assert snap["held"] == [HELD]
    widget = WillSummaryWidget(None)
    widget.show_summary(smr)
    assert not widget.held_label.isHidden()
    assert widget.held_label.text().startswith("<b>" + LEAD + "</b>")


def test_still_said_after_a_new_will_was_sent_to_the_others(make):
    """A change after the Exclude, signed and sent to the other two: its
    copies are replaced but still executable (it holds them): still said."""
    s = make()
    _excluded(s)
    s.clock.travel(1)

    def change(rows):
        rows[2]["shares"]["luca"] = "25%"
        rows[2]["shares"]["john"] = "35%"
    chain._edit_table(s, change)
    pushed = len(s.world.pushed)
    s.check()
    assert len(s.world.pushed) > pushed
    assert not [u for u, _w, _r in s.world.pushed[pushed:] if u == HELD]
    mine = [w for w in s.bw.willitems.values() if (w.we or {}).get("url") == HELD]
    assert mine and not any(w.get_status("VALID") for w in mine)
    assert _held(s) == [HELD]
    assert _summary(s)["held"] == [HELD]


def test_gone_when_its_copies_are_no_longer_executable(make):
    """Its copies invalidated: nothing to say."""
    s = make()
    _excluded(s)
    for w in s.bw.willitems.values():
        if (w.we or {}).get("url") == HELD:
            w.set_status("INVALIDATED", True)
    assert _held(s) == []
    smr = _summary(s)
    assert smr["held"] == [] and smr["held_text"] == []


def test_gone_when_selected_again(make):
    """Selected again: it is a will-executor of the will, no note."""
    s = make()
    _excluded(s)
    _choose(s, HELD, True)
    assert HELD in s.bw.selected_willexecutors()
    assert _held(s) == []


def test_removed_from_the_list_is_said_too(make):
    """Removed from the list (not only excluded): it holds its copy too."""
    s = make()
    chain._first(s, "three_dates")
    del s.world.executors[HELD]
    assert HELD not in (s.bw.selected_willexecutors() or ())
    assert _held(s) == [HELD]


def test_deselected_before_it_received_anything_is_not_said(make):
    """Deselected before the first sending: it holds nothing; only the
    #134 note "deselected, did not receive the will"."""
    s = make()
    _choose(s, HELD, False)
    chain.three_dates(s)
    s.check()
    assert not [u for u, _w, _r in s.world.pushed if u == HELD]
    assert _held(s) == []
    smr = _summary(s)
    assert smr["held"] == [] and smr["held_text"] == []


def test_the_pdf_report_prints_the_note(make):
    """The PDF report: the three sentences under the state box."""
    s = make()
    _excluded(s)
    report = core_report.will_report(
        s.bw.willitems, s.bw.heirs, s.bw.will_settings, s.plugin.is_basic_mode(),
        wallet=s.wallet, wallet_name="wallet_test", plugin_version="0.8.0",
        electrum_version="4.8.2", now=s.clock.now,
        still_active=s.bw._still_active_for_summary(),
        selected=s.bw.selected_willexecutors(), held=_held(s))
    assert report.summary["held_text"] == [LEAD, MIDDLE, LAST]
    texts = " ".join(t for page in rp.layout_report(report, str)
                     for t in rp.page_texts(page))
    for sentence in (LEAD, MIDDLE, LAST):
        assert sentence in texts, sentence
