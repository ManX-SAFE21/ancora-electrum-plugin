"""The Bitcoin After Life will-executor cannot be deleted or renamed (CHANGELOG #108).

Owner decision: it can only be deselected. Before, deleting it "worked", but
on testnet it came back selected at the next start (``get_willexecutors``
adds the default list back); on mainnet it arrives from the welist download.
The rule is checked in the core helper, on the command line and in the GUI
will-executor list (Delete menu and URL cell edit).

Run:
    QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_protected_executor.py -q
"""

import copy
import os
import sys
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from electrum.util import UserFacingException
from test_cli_command_layer import run
from test_cli_controller_offline import FakeWallet, Plugin

from bal.core.willexecutors import Willexecutors
from bal.gui.qt.lists import WillExecutorListWidget

BAL_URL = "https://we.bitcoin-after.life"
OTHER_URL = "https://we.example.com"


@pytest.fixture
def plugin():
    """A real plugin on a throwaway config directory."""
    with Plugin() as p:
        yield p


def test_is_protected_by_url(plugin):
    """Recognised by its URL (a trailing slash or capitals do not matter)."""
    assert Willexecutors.is_protected(plugin, BAL_URL)
    assert Willexecutors.is_protected(plugin, BAL_URL.upper() + "/")
    assert not Willexecutors.is_protected(plugin, OTHER_URL)
    assert not Willexecutors.is_protected(plugin, None)


def _cli_with_both(plugin):
    """A wallet whose will-executor list holds the BAL one and another."""
    wallet = FakeWallet()
    run(plugin, "willexecutors_add", BAL_URL, wallet=wallet)
    run(plugin, "willexecutors_add", OTHER_URL, wallet=wallet)
    return wallet


def test_cli_delete_refused_and_nothing_deleted(plugin):
    """bal_willexecutors_delete refuses it, and deletes nothing else either."""
    wallet = _cli_with_both(plugin)
    with pytest.raises(UserFacingException, match="deselect it"):
        run(plugin, "willexecutors_delete", [OTHER_URL, BAL_URL], wallet=wallet)
    listing = run(plugin, "willexecutors_list", wallet=wallet)
    assert BAL_URL in listing and OTHER_URL in listing
    # The other ones can still be deleted.
    out = run(plugin, "willexecutors_delete", OTHER_URL, wallet=wallet)
    assert out["deleted"] == [OTHER_URL]


def test_cli_rename_refused_other_fields_allowed(plugin):
    """Renaming is deleting the old URL: refused. Other fields still change,
    and deselecting it works."""
    wallet = _cli_with_both(plugin)
    with pytest.raises(UserFacingException, match="cannot be deleted or renamed"):
        run(plugin, "willexecutors_update", BAL_URL, info="x",
            rename_to="https://other.example.com", wallet=wallet)
    shown = run(plugin, "willexecutors_show", BAL_URL, wallet=wallet)
    assert shown["willexecutor"]["info"] != "x", "nothing changes on a refusal"
    out = run(plugin, "willexecutors_update", BAL_URL, info="x", wallet=wallet)
    assert out["willexecutor"]["info"] == "x"
    assert run(plugin, "willexecutors_select", BAL_URL, value=False,
               wallet=wallet) == {BAL_URL: False}


def _gui_list(plugin):
    """The parts of the GUI will-executor list its methods use, faked."""
    messages = []
    parent = SimpleNamespace(
        bal_plugin=plugin,
        willexecutors_list={BAL_URL: {"selected": True}, OTHER_URL: {"selected": True}},
        save_willexecutors=lambda: None,
        show_message=messages.append,
    )
    view = SimpleNamespace(
        _bal_parent=parent,
        Columns=WillExecutorListWidget.Columns,
        update=lambda: None,
        get_decimal_point=lambda: 8,
    )
    return view, parent, messages


def test_gui_delete_keeps_it_and_says_why(plugin):
    """The Delete menu removes the others and keeps the BAL one, with the
    message to deselect it."""
    view, parent, messages = _gui_list(plugin)
    WillExecutorListWidget.delete(view, [BAL_URL, OTHER_URL])
    assert list(parent.willexecutors_list) == [BAL_URL]
    assert len(messages) == 1 and "deselect it" in messages[0]


def test_gui_url_edit_refused(plugin):
    """Editing the URL cell of the BAL one is refused (it would delete it)."""
    view, parent, messages = _gui_list(plugin)
    idx = SimpleNamespace(column=lambda: WillExecutorListWidget.Columns.URL)
    WillExecutorListWidget.on_edited(view, idx, BAL_URL, text="https://x.example.com")
    assert BAL_URL in parent.willexecutors_list
    assert "https://x.example.com" not in parent.willexecutors_list
    assert messages and "cannot be deleted or renamed" in messages[0]
    # Another will-executor can still be renamed.
    WillExecutorListWidget.on_edited(view, idx, OTHER_URL, text="https://y.example.com")
    assert "https://y.example.com" in parent.willexecutors_list


def test_built_in_defaults_never_change(plugin):
    """On a fresh install the stored list WAS the built-in default dict:
    adding a will-executor made it a "default" (so it could not be deleted
    any more), and a deleted default vanished from the defaults until the
    restart. Now only copies are handed out."""
    before = copy.deepcopy(plugin.WILLEXECUTORS.default)
    wallet = FakeWallet()
    run(plugin, "willexecutors_add", OTHER_URL, wallet=wallet)
    run(plugin, "willexecutors_select", OTHER_URL, wallet=wallet)
    assert plugin.WILLEXECUTORS.default == before
    assert not Willexecutors.is_protected(plugin, OTHER_URL)
    assert run(plugin, "willexecutors_delete", OTHER_URL, wallet=wallet)["deleted"] == [OTHER_URL]
