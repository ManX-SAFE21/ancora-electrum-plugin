"""Which will-executors the automatic selection selects (CHANGELOG #137).

Owner: the wizards' "download and select" selected every will-executor
that answered, silently: we.divinaly.support, deselected by him, came back
and was only shown as "added" on page 4. The rule now, three cases:

1. a will-executor NEW on the network (not in the list before this
   download) that answers: selected; page 4 says "(new in the network)";
2. a will-executor deselected by the USER (the Will-Executors window, the
   Select / Deselect all menu, the command line): stays deselected;
3. one deselected by the PLUGIN (it did not answer, or Exclude): selected
   again as soon as it answers.

The user's deselections are a user choice, stored in
``bal_willexecutors_user_deselected`` (``{chain: [url]}``); seeded ONCE
from the will-executors already deselected (owner decision A).

The core rule is tested on its own; then the REAL BASIC wizard (and the
ADVANCED page) on flowkit wallets with three fake will-executors, each with
its own fee address (a fourth, new on the network, for case 1). After each
scenario a Check, then a second Check that is clean.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_executor_selection.py
"""

import copy
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

import flowkit as fk
from PyQt6.QtWidgets import QApplication, QLabel

from bal.core.plugin_base import BalConfig, BalPlugin
from bal.core.willexecutors import Willexecutors

_APP = QApplication.instance() or QApplication(sys.argv)

TEST_PASSWORD = "flowkit-test"
WE = ["https://we0.test", "https://we1.test", "https://we2.test"]
NEW = "https://we3.test"


# ------------------------------------------------------------ core rule

class _Config(dict):
    """The two calls ``BalConfig`` makes on Electrum's config."""

    def set_key(self, name, value, save=True):
        self[name] = copy.deepcopy(value)


def _plugin():
    """A plugin object with only the setting of #137."""
    cfg = _Config()
    return SimpleNamespace(WILLEXECUTORS_USER_DESELECTED=BalConfig(
        cfg, "bal_willexecutors_user_deselected", None)), cfg


def _we(selected, status="200"):
    return {"selected": selected, "status": status}


def test_rule_cases_one_two_three_and_mixed():
    """Who answers is selected, except the user's deselections; the new
    ones that answer are returned; a known one that answers again is
    selected again (case 3)."""
    wes = {
        "a": _we(True),                 # known, answers: stays selected
        "b": _we(False),                # user deselected, answers: stays off
        "c": _we(False),                # plugin deselected (down), answers: on
        "d": _we(True, status="KO"),    # known, down: off
        "n": _we(False),                # new, answers: on, reported new
        "m": _we(False, status="KO"),   # new, down: off, not reported
        "u": _we(False, status="KO"),   # user deselected and down: off
    }
    new = Willexecutors.select_after_ping(wes, {"b", "u"}, {"a", "b", "c", "d", "u"})
    assert {k: v["selected"] for k, v in wes.items()} == {
        "a": True, "b": False, "c": True, "d": False, "n": True, "m": False,
        "u": False}
    assert new == {"n"}


def test_user_deselections_seeded_once_then_only_user_choices(monkeypatch):
    """Decision A: the first look seeds the deselected ones; a later
    plugin deselection is never added; the user's select / deselect
    change it."""
    plugin, cfg = _plugin()
    listed = {"a": _we(True), "b": _we(False), "c": _we(True)}
    monkeypatch.setattr(Willexecutors, "get_willexecutors",
                        staticmethod(lambda p: listed))
    chain = BalPlugin.chainname
    assert Willexecutors.user_deselected(plugin) == {"b"}
    assert cfg["bal_willexecutors_user_deselected"] == {chain: ["b"]}
    listed["c"]["selected"] = False             # the plugin deselects c
    assert Willexecutors.user_deselected(plugin) == {"b"}
    Willexecutors.set_user_choice(plugin, ["a"], False)
    Willexecutors.set_user_choice(plugin, ["b"], True)
    assert Willexecutors.user_deselected(plugin) == {"a"}
    assert cfg["bal_willexecutors_user_deselected"] == {chain: ["a"]}


# ------------------------------------------------------------ real windows

@pytest.fixture
def make(monkeypatch):
    """A will in force at three will-executors, the REAL window, the
    welist and ping faked from the world; ``s.network``: will-executors
    on the network that are not in the list yet."""
    from zoneinfo import ZoneInfo

    from test_gui_window_init_migration import real_window

    from bal.core import wizard_date
    from bal.gui.qt import basic_wizard

    fk.ensure_event_loop()
    made = []
    monkeypatch.setattr(wizard_date, "local_zone", lambda: ZoneInfo("Europe/Rome"))

    def _make(mode="basic"):
        s = fk.Session(monkeypatch, mode=mode, password=TEST_PASSWORD)
        s.world.add_executor()
        monkeypatch.setattr(s.wallet, "is_up_to_date", lambda: True)
        made.append(s)
        s.check()
        monkeypatch.setattr(basic_wizard, "time_now", s.clock.time)
        bw = real_window(s, monkeypatch)
        bw.init_will()
        s.network = {}
        s.world.offline = False
        bw.events = []          # the fake "Please wait" window records here
        bw.popups = []

        def fetch(old):
            listed = {u: dict(copy.deepcopy(we), selected=False)
                      for u, we in s.world.executors.items()}
            listed.update(copy.deepcopy(s.network))
            return listed

        def ping(wes, *, on_each=None, on_tick=None, **kw):
            for url, we in wes.items():
                ok = url not in s.world.down
                we["status"] = 200 if ok else "KO"
                if on_each:
                    on_each(url, we, ok)
            return wes

        monkeypatch.setattr(bw, "fetch_will_executors_list", fetch)
        monkeypatch.setattr(Willexecutors, "ping_servers_parallel", staticmethod(ping))
        for name in ("show_warning", "show_message", "show_error"):
            monkeypatch.setattr(bw, name, lambda *a, **k: None)
        return s, bw

    yield _make
    for s in made:
        s.close()
    fk.stop_event_loop()


def selected(s):
    return sorted(u for u, we in s.world.executors.items() if we.get("selected"))


def open_wizard(bw):
    from bal.gui.qt.dialogs import BalWizardDialog
    dialog = BalWizardDialog(bw)
    dialog.show()
    _APP.processEvents()
    return dialog


def next_page(dialog):
    dialog.widget.next_button.click()
    _APP.processEvents()
    return dialog.widget


def checks_then_clean(s):
    """A Check, then a second Check with nothing to say or ask."""
    s.check()
    s.user.asked.clear()
    snap = s.check()._report().snapshot()
    assert s.user.asked == []
    assert snap["boxes"]["changes"] == [] and "changes" not in snap


def executor_window(bw):
    """The real Will-Executors window's list (the select / deselect menu)."""
    from PyQt6.QtWidgets import QWidget

    from bal.gui.qt.lists import WillExecutorWidget
    host = QWidget()
    widget = WillExecutorWidget(host, bw, Willexecutors.get_willexecutors(bw.bal_plugin))
    return widget


def test_case_one_new_on_the_network_is_selected_and_said(make):
    """we3 appears on the network: selected, and page 4 says
    "added we3.test (new in the network)"."""
    s, bw = make()
    s.network[NEW] = {"url": NEW, "selected": False, "base_fee": 1000,
                      "address": fk.address(20), "status": "200", "info": "fake"}
    dialog = open_wizard(bw)
    try:
        next_page(dialog)
        next_page(dialog)
        assert selected(s) == WE + [NEW]
        page = next_page(dialog)
        texts = [w.text() for w in page.changes_box.findChildren(QLabel)]
        assert any("added <b>we3.test</b> (new in the network)" in t for t in texts)
    finally:
        dialog.close()                         # Cancel: flags back
    assert NEW not in selected(s)


def test_case_two_owner_divinaly_stays_deselected(make):
    """The owner's case: he deselects we1 in the Will-Executors window;
    the wizard's download, we1 answering, keeps it deselected."""
    s, bw = make()
    executor_window(bw).will_executor_list_widget.deselect([WE[1]])
    assert Willexecutors.user_deselected(bw.bal_plugin) == {WE[1]}
    bw.download_and_select_willexecutors(select_responding=True, ping_without_list=True)
    assert selected(s) == [WE[0], WE[2]]
    bw.download_and_select_willexecutors(select_responding=True, ping_without_list=True)
    assert selected(s) == [WE[0], WE[2]]
    checks_then_clean(s)
    # Selected again by him: the next download keeps it selected.
    executor_window(bw).will_executor_list_widget.select([WE[1]])
    bw.download_and_select_willexecutors(select_responding=True, ping_without_list=True)
    assert selected(s) == WE


def test_case_three_down_then_back_is_selected_again(make):
    """we2 does not answer: deselected by the plugin; it answers again:
    selected again."""
    s, bw = make()
    s.world.down = {WE[2]}
    bw.download_and_select_willexecutors(select_responding=True, ping_without_list=True)
    assert selected(s) == WE[:2]
    assert Willexecutors.user_deselected(bw.bal_plugin) == set()
    s.world.down = set()
    bw.download_and_select_willexecutors(select_responding=True, ping_without_list=True)
    assert selected(s) == WE
    checks_then_clean(s)


def test_exclude_then_back_is_selected_again(make):
    """Exclude (the plugin's offer for a will-executor that did not
    answer) is not a user deselection: it comes back when it answers."""
    s, bw = make()
    assert bw.exclude_willexecutor(WE[0])
    assert selected(s) == WE[1:]
    assert Willexecutors.user_deselected(bw.bal_plugin) == set()
    bw.download_and_select_willexecutors(select_responding=True, ping_without_list=True)
    assert selected(s) == WE
    checks_then_clean(s)


def test_select_all_menu_is_the_users_choice(make):
    """Decision B: "Deselect all" then "Select only valid" are the user's
    choices: a later download respects them."""
    s, bw = make()
    widget = executor_window(bw)
    widget.set_select_all(False)
    assert Willexecutors.user_deselected(bw.bal_plugin) == set(WE)
    widget.set_select_all(True, only_valid=True)
    assert Willexecutors.user_deselected(bw.bal_plugin) == set()
    widget.set_select_all(False)
    bw.download_and_select_willexecutors(select_responding=True, ping_without_list=True)
    assert selected(s) == []


def test_mixed_user_down_and_new(make):
    """we1 deselected by the user, we2 down, we3 new: we0 and we3."""
    s, bw = make()
    executor_window(bw).will_executor_list_widget.deselect([WE[1]])
    s.world.down = {WE[2]}
    s.network[NEW] = {"url": NEW, "selected": False, "base_fee": 1000,
                      "address": fk.address(20), "status": "200", "info": "fake"}
    bw.download_and_select_willexecutors(select_responding=True, ping_without_list=True)
    assert selected(s) == [WE[0], NEW]
    assert bw.new_willexecutors == {NEW}
    checks_then_clean(s)


def test_wizard_cancel_restores_flags_and_user_list(make):
    """Cancel restores the selected flags AND the user's deselections."""
    s, bw = make()
    executor_window(bw).will_executor_list_widget.deselect([WE[1]])
    before_flags = selected(s)
    dialog = open_wizard(bw)
    executor_window(bw).will_executor_list_widget.select([WE[1]])
    executor_window(bw).will_executor_list_widget.deselect([WE[0]])
    dialog.close()
    assert selected(s) == before_flags
    assert Willexecutors.user_deselected(bw.bal_plugin) == {WE[1]}


def test_command_line_select_and_deselect(make):
    """The command line's select / deselect are the user's choices."""
    from bal.cli.controller import BalController
    s, bw = make()
    controller = BalController.__new__(BalController)
    controller.plugin = bw.bal_plugin
    controller.willexecutors = Willexecutors.get_willexecutors(bw.bal_plugin)
    controller.willexecutors_select([WE[2]], select=False)
    assert Willexecutors.user_deselected(bw.bal_plugin) == {WE[2]}
    controller.willexecutors_select([WE[2]], select=True)
    assert Willexecutors.user_deselected(bw.bal_plugin) == set()


def test_advanced_page_uses_the_same_rule(make):
    """ADVANCED: the old page's "Automatically download and select" keeps
    the user's deselection and selects a new will-executor."""
    from bal.gui.qt.dialogs import BalWizardWEDownloadWidget
    s, bw = make(mode="advanced")
    executor_window(bw).will_executor_list_widget.deselect([WE[1]])
    s.network[NEW] = {"url": NEW, "selected": False, "base_fee": 1000,
                      "address": fk.address(20), "status": "200", "info": "fake"}
    page = BalWizardWEDownloadWidget(bw, None, lambda: None, lambda: None, lambda: None)
    page.combo.setCurrentIndex(0)
    page._on_next()
    assert selected(s) == [WE[0], WE[2], NEW]
    checks_then_clean(s)
