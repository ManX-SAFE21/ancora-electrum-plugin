"""Tools > "Sign will offline...": the guided offline signing (#160).

The owner's cold-wallet route: the online computer (a watch-only wallet)
prepares the will and exports it on a USB stick; the offline computer, with
the keys, signs it; the stick goes back, "Merge" and the Check send it.
Before #160 the offline side was the Will tab's Import and the Will Details
window (the will's history, Sign / Broadcast / Merge buttons, no plain
words). Now one window in Electrum's Tools menu guides it in three steps:
choose the file, check what it gives to whom, sign all and save.

Also #160 (owner): "No will in force" is red only when the wallet shows a
balance; an empty wallet (or the offline computer, which never shows one)
gets a grey box that points to the offline signing.

Test wallets: throw-away testnet keys made at every run (the wallets of
``tests/test_engine_merge.py``), three fake will-executors
(``tests/flowkit.py``). Nothing reaches the network.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_offline_sign.py
"""

import html
import json
import os
import sys
import tempfile
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

from PyQt6.QtWidgets import QApplication
from test_engine_merge import (  # noqa: F401 - the fixture
    _clear_pushes,
    _complete,
    _export,
    _merge,
    _pushed_complete,
    _valid,
    world,
)

from bal.core import summary as core_summary
from bal.engine import offline_sign as engine
from bal.gui.qt import offline_sign as gui
from bal.gui.qt import window as window_mod

_APP = QApplication.instance() or QApplication(sys.argv)

PARTLY = "1 of 2 signatures: another signer must sign."


def _dialog(s, monkeypatch, save_as=None):
    """The window of Tools > "Sign the will offline..." on session *s*.

    Its message boxes are recorded in ``dialog.errors`` / ``dialog.asked``
    (#161: warnings in ``dialog.errors`` too); the save window answers
    *save_as*.
    """
    monkeypatch.setattr(s.plugin, "read_file", lambda path: b"", raising=False)
    s.bw.window = SimpleNamespace(
        wallet=s.wallet, config=s.config, top_level_window=lambda: None,
        get_decimal_point=lambda: 8,
        format_amount_and_units=lambda sat: "{} sat".format(sat))
    monkeypatch.setattr(window_mod, "show_on_top", lambda d: None)
    d = s.bw.show_offline_sign_dialog()
    d.errors, d.asked = [], []
    d.show_error = lambda text, *a, **kw: d.errors.append(text)
    d.show_warning = lambda text, *a, **kw: d.errors.append(text)
    d.question = lambda text, *a, **kw: d.asked.append(text) or True
    d.ask_save_path = lambda: save_as
    return d


def _file(s, folder, name):
    """The will file of *s* on the "USB stick" (the export of the Will tab)."""
    return _export(s, folder, name)


def _page(d):
    """The page shown: "choose", "check", "save" or "empty"."""
    return {d.choose_page: "choose", d.check_page: "check",
            d.save_page: "save", d.empty_page: "empty"}[d.pages.currentWidget()]


# ------------------------------------------------------------ the route


def test_watch_only_route_signed_offline_by_the_window(world, monkeypatch):  # noqa: F811
    """Online watch-only, keys offline: the window loads the file, shows
    the heirs, signs all with one password, saves; back online "Merge"
    and the Check send three signed copies."""
    folder = tempfile.mkdtemp(prefix="bal_offline_")
    online = world("watch")
    online.check()
    stick = _file(online, folder, "will.json")
    offline = world("keys")
    signed = os.path.join(folder, "will-signed.json")
    d = _dialog(offline, monkeypatch, save_as=signed)
    assert _page(d) == "choose" and d.bar.current == engine.STEP_SIGN
    assert d.load_file(stick) and _page(d) == "check"
    assert d.bar.current == engine.STEP_SIGN
    text = d.details.text()
    for word in ("alice", "bob", "we0.test", "we1.test", "we2.test",
                 "Transactions to sign: 3 of 3"):
        assert word in text, word
    offline.act("sign all", d.sign_all)
    assert offline.user.asked == [
        ["password", "Enter your wallet password to sign the will."]]
    assert _page(d) == "save" and d.bar.current == engine.STEP_SIGN
    assert "Signed 3 of 3 transactions." in d.result.text()
    d.save_file()
    assert d.saved_path == signed and _page(d) == "empty"
    # #161: next, bring the stick back to the online computer (step 4).
    assert d.bar.current == engine.STEP_BRING_BACK
    assert d.errors == []
    # The offline wallet's own will is not touched.
    assert offline.bw.willitems == {} or not any(
        w.get_status("COMPLETE") for w in offline.bw.willitems.values())
    message = _merge(online, signed)
    assert message == "Merge done: 3 transaction(s) signed, 0 still to sign."
    assert all(_complete(w) for w in _valid(online).values())
    _clear_pushes(online)
    online.check()
    assert _pushed_complete(online) == [True, True, True]


def test_multisig_cosigners_sign_one_after_the_other(world, monkeypatch):  # noqa: F811
    """A watch-only multisig 2-of-3 online; cosigner A signs in the window
    ("1 of 2 signatures: another signer must sign."), then B: signed."""
    folder = tempfile.mkdtemp(prefix="bal_offline_")
    online = world("msWatch")
    online.check()
    stick = _file(online, folder, "will.json")
    a = world("msA")
    after_a = os.path.join(folder, "after-a.json")
    d = _dialog(a, monkeypatch, save_as=after_a)
    assert d.load_file(stick)
    a.act("sign all", d.sign_all)
    assert PARTLY in d.result.text()
    d.save_file()
    # A again: this wallet has already signed.
    d2 = _dialog(a, monkeypatch)
    assert not d2.load_file(after_a)
    assert d2.errors == [gui._PROBLEMS[engine.OTHER_WALLET]]
    b = world("msB")
    after_b = os.path.join(folder, "after-b.json")
    d3 = _dialog(b, monkeypatch, save_as=after_b)
    assert d3.load_file(after_a)
    b.act("sign all", d3.sign_all)
    assert "Signed 3 of 3 transactions." in d3.result.text()
    d3.save_file()
    message = _merge(online, after_b)
    assert message == "Merge done: 3 transaction(s) signed, 0 still to sign."


# ------------------------------------------------- what cannot be signed


def test_watch_only_wallet_cannot_sign(world, monkeypatch):  # noqa: F811
    """The signing window on a wallet with no keys: one page that says so,
    no button. #161: the Tools entry opens the online window there
    (``tests/test_sign_offline_online.py``); this page stays for safety."""
    s = world("watch")
    _dialog(s, monkeypatch)
    d = gui.OfflineSignDialog(s.bw)
    assert _page(d) == "empty"
    assert d.title.text() == "This wallet cannot sign"
    assert d.text.text() == html.escape(gui._PROBLEMS[engine.NO_KEYS], quote=False)


def test_files_that_cannot_be_signed_here(world, monkeypatch):  # noqa: F811
    """Already signed, another wallet's will, not a will file, unreadable:
    each one a plain message, and the window stays on step 1."""
    folder = tempfile.mkdtemp(prefix="bal_offline_")
    online = world("watch")
    online.check()
    stick = _file(online, folder, "will.json")
    offline = world("keys")
    signed = os.path.join(folder, "signed.json")
    d = _dialog(offline, monkeypatch, save_as=signed)
    assert d.load_file(stick)
    offline.act("sign all", d.sign_all)
    d.save_file()
    cases = []
    d = _dialog(offline, monkeypatch)
    cases.append((d, signed, gui._PROBLEMS[engine.ALREADY_SIGNED]))
    d = _dialog(world("msA"), monkeypatch)
    cases.append((d, stick, gui._PROBLEMS[engine.OTHER_WALLET]))
    txs = os.path.join(folder, "txs.txt")
    with open(txs, "w", encoding="utf-8") as f:
        f.write(",".join(str(w.tx) for w in _valid(online).values()))
    d = _dialog(offline, monkeypatch)
    cases.append((d, txs, "This is not a will file. Choose the will file saved "
                          "by the plugin on the online computer."))
    for d, path, error in cases:
        assert not d.load_file(path)
        assert d.errors == [error]
        assert _page(d) == "choose"
    d = _dialog(offline, monkeypatch)
    assert not d.load_file(os.path.join(folder, "missing.json"))
    assert d.errors[0].startswith("The file cannot be read:")


def test_engine_problems_in_order(world):  # noqa: F811
    """The engine's reasons, read directly: no keys first, then nothing
    valid, already signed, another wallet."""
    folder = tempfile.mkdtemp(prefix="bal_offline_")
    online = world("watch")
    online.check()
    data = json.load(open(_file(online, folder, "will.json"), encoding="utf-8"))
    items = online.bw._load_will_payload(data)
    assert engine.file_problem(online.wallet, items) == engine.NO_KEYS
    keys = world("keys")
    items = keys.bw._load_will_payload(json.loads(json.dumps(data)))
    assert engine.file_problem(keys.wallet, items) is None
    for w in items.values():
        w.set_status("VALID", False)
    assert engine.file_problem(keys.wallet, items) == engine.NOTHING_TO_SIGN


# ------------------------------------------------------- the user's way


def test_password_cancelled_signs_nothing(world, monkeypatch):  # noqa: F811
    """Cancel at the password: nothing signed, still on step 2."""
    folder = tempfile.mkdtemp(prefix="bal_offline_")
    online = world("watch")
    online.check()
    offline = world("keys")
    d = _dialog(offline, monkeypatch)
    assert d.load_file(_file(online, folder, "will.json"))
    offline.act("sign all", d.sign_all, cancel_on=("password",))
    assert _page(d) == "check" and not d.signed
    assert not any(w.get_status("COMPLETE") for w in d.items.values())


def test_closing_a_signed_will_not_saved_asks(world, monkeypatch):  # noqa: F811
    """Signed and not saved: closing asks first; after saving it does not."""
    folder = tempfile.mkdtemp(prefix="bal_offline_")
    online = world("watch")
    online.check()
    offline = world("keys")
    d = _dialog(offline, monkeypatch, save_as=os.path.join(folder, "s.json"))
    assert d.may_close() and d.asked == []
    assert d.load_file(_file(online, folder, "will.json"))
    offline.act("sign all", d.sign_all)
    assert d.may_close()
    assert d.asked == [
        "The signed will is not saved yet: if you close now you will have "
        "to sign it again. Close anyway?"]
    d.save_file()
    d.asked.clear()
    assert d.may_close() and d.asked == []


def test_default_file_name_and_drawings(world, monkeypatch):  # noqa: F811
    """The signed file's name says what it is; every drawing paints."""
    d = _dialog(world("keys"), monkeypatch)
    name = d.default_file_name()
    assert "-will-signed-" in name and name.endswith(".json")
    for kind in ("usb", "check", "lock", "done", "stop", "send",
                 "online", "offline", "to_online", "to_offline"):
        assert not gui.drawing(kind).isNull()


def test_tools_menu_entry(world, monkeypatch):  # noqa: F811
    """The entry is added to Electrum's Tools menu with the Will-Executors."""
    s = world("keys")
    added = []
    menu = SimpleNamespace(
        addSeparator=lambda: None,
        addAction=lambda text, slot: added.append((text, slot)) or SimpleNamespace())
    monkeypatch.setattr(s.plugin, "read_file", lambda path: b"", raising=False)
    s.bw._menubar_initialized = False
    s.bw.heirs_tab = s.bw.will_tab = SimpleNamespace(
        is_shown_cv=SimpleNamespace(get=lambda: False))
    s.bw.window = SimpleNamespace(
        tabs=[], view_menu=SimpleNamespace(
            addSeparator=lambda: None,
            addAction=lambda *a: SimpleNamespace(
                setCheckable=lambda v: None, setChecked=lambda v: None)))
    s.bw.init_menubar_tools(menu)
    texts = [t for t, _slot in added]
    # #161: the owner's name for the entry.
    assert "Sign the will offline…" in texts
    opened = []
    monkeypatch.setattr(s.bw, "show_offline_sign_dialog",
                        lambda **kw: opened.append(kw))
    dict(added)["Sign the will offline…"]()
    assert opened == [{}]


# ------------------------------------------------- the "no will" box


@pytest.mark.parametrize("has_funds, kind", [
    (None, "bad"), (True, "bad"), (False, "info")])
def test_no_will_box_is_red_only_with_funds(has_funds, kind):
    """No will: red with a balance (or unknown), grey without one, with
    "Build Your Will" kept and the way to the offline signing."""
    out = core_summary.will_summary({}, 2, {}, True, has_funds=has_funds)
    assert out["state"] == "none" and out["kind"] == kind
    assert out["action"] == "wizard"
    if has_funds is False:
        assert out["title"] == "No will yet"
        assert "Tools > Sign the will offline" in out["note"]
    else:
        assert out["title"] == "No will in force"


def test_window_follows_the_balance(world, monkeypatch):  # noqa: F811
    """The window passes the balance to the box, and redraws it when the
    wallet gets or loses its balance (not at every sync)."""
    s = world("keys")
    assert s.bw.wallet_has_funds() is True
    redraws = []
    monkeypatch.setattr(s.bw, "update_will_summary", lambda: redraws.append(1))
    s.bw._summary_has_funds = True
    s.bw._refresh_summary_if_funds_changed()
    assert redraws == []
    monkeypatch.setattr(s.wallet, "get_balance", lambda **kw: (0, 0, 0))
    assert s.bw.wallet_has_funds() is False
    s.bw._refresh_summary_if_funds_changed()
    assert redraws == [1]
    monkeypatch.setattr(s.wallet, "get_balance", lambda **kw: 1 / 0)
    assert s.bw.wallet_has_funds() is None
