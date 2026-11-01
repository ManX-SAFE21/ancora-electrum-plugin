"""Tools > "Sign the will offline...": the online computer's steps (#161).

#160 guided the offline computer (the keys) only. On the online computer
(a watch-only wallet) the owner still had to know "Export, choose a
filter, save the file, carry it, Merge, Check": too complicated. The same
entry now opens, on a watch-only wallet, a window with the whole route at
the top ("You are here" on steps 1 and 3) and two big buttons:

1. "Prepare the USB stick": the Check prepares the will, then only the
   copies still to sign are saved, with no export choices;
3. "Load the signed will and send": the file is merged, then the Check
   sends and verifies it; the file still to sign, chosen by mistake, is
   refused with a warning.

The owner also asked for it to be easy to find: a big button in the Will
tab (watch-only wallets, and wallets with keys that show no balance), the
summary box's button when a watch-only will waits for its signatures, and
the wizard, which opens the window after creating such a will.

Test wallets: throw-away testnet keys made at every run (the wallets of
``tests/test_engine_merge.py``), three fake will-executors
(``tests/flowkit.py``). Nothing reaches the network.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_sign_offline_online.py
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

import test_engine_merge as tem
from electrum import keystore
from electrum.storage import WalletStorage
from electrum.wallet import Multisig_Wallet
from electrum.wallet_db import WalletDB
from PyQt6.QtWidgets import QApplication, QSizePolicy
from test_engine_merge import (  # noqa: F401 - the fixture
    _clear_pushes,
    _export,
    _pushed_complete,
    _valid,
    world,
)
from test_offline_sign import _dialog

from bal.core import summary as core_summary
from bal.engine import offline_sign as engine
from bal.gui.qt import offline_sign as gui

_APP = QApplication.instance() or QApplication(sys.argv)

STILL_TO_SIGN_WARNING = (
    "This is the will file still to sign: it holds no signature yet. Sign it "
    "first on the offline computer, then choose here the signed file (its "
    "name has \"will-signed\").")


def _online(s, monkeypatch, save_as=None, open_path=None):
    """The window of the Tools entry on the watch-only session *s*.

    Its Check is the session's Check (fake will-executors, recorded); its
    file windows answer *save_as* / *open_path*; ``dialog.checks`` counts
    the Checks it ran.
    """
    s.activate()
    d = _dialog(s, monkeypatch, save_as=save_as)
    assert isinstance(d, gui.OnlineSignDialog)
    d.checks = []
    d.run_check = lambda: d.checks.append(1) or s.check()
    d.ask_open_path = lambda: open_path
    return d


def _sign_on(s, monkeypatch, stick, out):
    """Step 2 in the window of #160 on the session *s* (with keys)."""
    s.activate()
    d = _dialog(s, monkeypatch, save_as=out)
    assert isinstance(d, gui.OfflineSignDialog)
    assert d.load_file(stick)
    s.act("sign all", d.sign_all)
    d.save_file()
    assert d.errors == [] and d.saved_path == out
    return d


def _link(d):
    """The text of the window's small link, or None when it is hidden."""
    return None if d.link.isHidden() else d.link.text()


def _items(path):
    """The copies in a will file: ``{txid: dict}``."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ------------------------------------------------------------ the route


def test_single_key_route_from_the_online_window(world, monkeypatch):  # noqa: F811
    """Watch-only online, keys offline, the five steps (owner): 1 saves
    only the copies to sign, then the window shows 2 (take the stick to
    the offline computer, no button); 3 signs (#160), then 4 (bring it
    back); 5 merges and the Check sends the three copies, all signed."""
    folder = tempfile.mkdtemp(prefix="bal_route_")
    stick = os.path.join(folder, "to-sign.json")
    signed = os.path.join(folder, "signed.json")
    online = world("watch")
    d = _online(online, monkeypatch, save_as=stick, open_path=signed)
    # No will yet: nothing to sign, no step marked.
    assert d.title.text() == "There is no will to sign"
    assert d.bar.current is None and sorted(d.bar.boxes) == [1, 2, 3, 4, 5]
    assert "-will-TO-SIGN-" in d.default_file_name()

    online.check()
    d.show_next_step()
    assert d.bar.current == engine.STEP_PREPARE
    assert d.title.text() == "Prepare the USB stick"
    assert d.where.text() == "On THIS computer"
    assert d.pages.currentWidget() is d.prepare_page and not _link(d)

    d.just_checked = True
    assert d.prepare_stick() and d.checks == []
    # Step 2: the stick goes to the offline computer; no button here.
    assert d.bar.current == engine.STEP_CARRY
    assert d.where.text() == "On the OTHER computer (offline)"
    assert d.title.text() == "Sign the will on the offline computer"
    assert stick in d.result.text()
    assert "Open the menu Tools &gt; Sign the will offline." in d.text.text()
    assert d.pages.currentWidget() is d.empty_page
    assert _link(d) == "I already have the signed USB stick →"
    carried = _items(stick)
    assert sorted(carried) == sorted(_valid(online))
    assert all("Exported" in item["status"] for item in carried.values())

    off = _sign_on(world("keys"), monkeypatch, stick, signed)
    # Step 4 on the offline computer: bring the stick back.
    assert off.bar.current == engine.STEP_BRING_BACK
    assert off.where.text() == "On the OTHER computer (online)"
    assert "Open the menu Tools &gt; Sign the will offline." in off.text.text()

    online.activate()
    d.show_next_step()
    assert d.bar.current == engine.STEP_SEND
    assert d.pages.currentWidget() is d.send_page
    assert _link(d) == "I still have to sign it"
    _clear_pushes(online)
    assert d.load_and_send() and d.checks == [1]
    assert _pushed_complete(online) == [True, True, True]
    assert d.title.text() == "Will signed and sent"
    assert d.errors == []
    # In force: every step ticked, nothing to do.
    assert d.bar.current is None and d.next_step() is None
    assert "✓ 5" in d.bar.numbers[engine.STEP_SEND].text()

    assert not d.prepare_stick()
    assert d.title.text() == "The will is already signed"


def test_links_go_between_step_2_and_step_5(world, monkeypatch):  # noqa: F811
    """"I already have the signed USB stick →" shows step 5; "I still have
    to sign it" shows step 2 again."""
    online = world("watch")
    online.check()
    d = _online(online, monkeypatch, save_as=os.path.join(tempfile.mkdtemp(), "s.json"))
    d.just_checked = True
    assert d.prepare_stick() and d.bar.current == engine.STEP_CARRY
    d.link.click()
    assert d.bar.current == engine.STEP_SEND
    d.link.click()
    assert d.bar.current == engine.STEP_CARRY


def test_multisig_2_of_3_cosigners_sign_from_the_same_stick(world, monkeypatch):  # noqa: F811
    """A watch-only multisig 2-of-3: A signs ("1 of 2" under step 3, the
    stick goes to another cosigner); brought back too early, nothing
    unsigned is sent and the window says one more signature is needed;
    B signs the same file and step 5 sends the three copies signed."""
    folder = tempfile.mkdtemp(prefix="bal_route_")
    paths = {n: os.path.join(folder, n + ".json")
             for n in ("stick", "after-a", "after-b")}
    online = world("msWatch")
    online.check()
    d = _online(online, monkeypatch, save_as=paths["stick"],
                open_path=paths["after-a"])
    assert d.bar.texts[engine.STEP_CARRY].text() == "Take it to the cosigner's PC"
    d.just_checked = True
    assert d.prepare_stick()
    assert d.where.text() == "On the OTHER computers (cosigners)"
    assert "until 2 signatures are made" in d.text.text()
    assert d.bar.count.text() == "0 of 2"

    a = _sign_on(world("msA"), monkeypatch, paths["stick"], paths["after-a"])
    assert a.bar.current == engine.STEP_SIGN and a.bar.count.text() == "1 of 2"
    assert a.title.text() == "1 more signature needed"
    assert a.where.text() == "On the OTHER computer (next cosigner)"

    online.activate()
    _clear_pushes(online)
    assert d.load_and_send()
    assert d.title.text() == "1 more signature needed"
    assert d.bar.current == engine.STEP_SIGN and d.bar.count.text() == "1 of 2"
    assert not any(_pushed_complete(online))
    # The stick is still out (the next cosigner signs it): step 5 next.
    assert d.next_step() == engine.STEP_SEND

    b = _sign_on(world("msB"), monkeypatch, paths["after-a"], paths["after-b"])
    assert b.bar.current == engine.STEP_BRING_BACK
    online.activate()
    _clear_pushes(online)
    d.ask_open_path = lambda: paths["after-b"]
    assert d.load_and_send()
    assert d.title.text() == "Will signed and sent"
    assert _pushed_complete(online) == [True, True, True]


def _ms34_maker(mine):
    """A ``flowkit._make_wallet`` for a 3-of-4 multisig: cosigner *mine*
    ("A".."D", or None: watch-only) has its key (throw-away testnet keys
    from public labels, as ``tests/test_engine_merge.py``)."""

    def make(config, path, password, label):
        """The 3-of-4 wallet (``label`` is not used)."""
        db = WalletDB("", storage=WalletStorage(path), upgrade=False)
        for i, name in enumerate("ABCD"):
            seed_label = "flowkit 3of4 cosigner " + name
            if name == mine:
                ks = keystore.from_xprv(tem._xprv(seed_label, "p2wsh"))
            else:
                ks = keystore.from_xpub(tem._xpub(seed_label, "p2wsh"))
            db.put("x%d" % (i + 1), ks.dump())
        db.put("wallet_type", "3of4")
        wallet = Multisig_Wallet(db, config=config)
        if password and mine:
            wallet.update_password(None, password, encrypt_storage=False)
        wallet.synchronize()
        return wallet

    return make


def test_multisig_3_of_4_three_cosigners_then_send(world, monkeypatch):  # noqa: F811
    """The owner's example: a 3-of-4, watch-only online. The stick goes
    from A to B to C ("1 of 3", "2 of 3", then complete: step 4); D is
    not needed. Step 5 sends the three copies, all signed."""
    original = tem._maker
    kinds = {"ms34A": "A", "ms34B": "B", "ms34C": "C", "ms34Watch": None}
    monkeypatch.setattr(tem, "_maker", lambda kind: (
        _ms34_maker(kinds[kind]) if kind in kinds else original(kind)))
    folder = tempfile.mkdtemp(prefix="bal_route_")
    paths = {n: os.path.join(folder, n + ".json")
             for n in ("stick", "a", "b", "c")}
    online = world("ms34Watch")
    online.check()
    d = _online(online, monkeypatch, save_as=paths["stick"], open_path=paths["c"])
    d.just_checked = True
    assert d.prepare_stick()
    assert "until 3 signatures are made" in d.text.text()
    assert d.bar.count.text() == "0 of 3"
    before = paths["stick"]
    for name, count, title in (("A", "1 of 3", "2 more signatures needed"),
                               ("B", "2 of 3", "1 more signature needed")):
        w = _sign_on(world("ms34" + name), monkeypatch, before, paths[name.lower()])
        assert w.bar.current == engine.STEP_SIGN, name
        assert w.bar.count.text() == count and w.title.text() == title
        before = paths[name.lower()]
    c = _sign_on(world("ms34C"), monkeypatch, before, paths["c"])
    assert c.bar.current == engine.STEP_BRING_BACK
    assert c.title.text() == "Send the will from the online computer"
    online.activate()
    _clear_pushes(online)
    assert d.load_and_send()
    assert d.title.text() == "Will signed and sent"
    assert _pushed_complete(online) == [True, True, True]


# ------------------------------------------------------- the wrong file


def test_step_3_refuses_the_wrong_files(world, monkeypatch):  # noqa: F811
    """The file still to sign: a warning, nothing merged, no Check. The will
    of another wallet, a file that is not a will: a plain message."""
    folder = tempfile.mkdtemp(prefix="bal_route_")
    other = world("msWatch")
    other.check()
    foreign = _export(other, folder, "foreign.json")
    stick = os.path.join(folder, "to-sign.json")
    online = world("watch")
    d = _online(online, monkeypatch, save_as=stick)
    assert d.prepare_stick()
    before = {wid: w.to_dict() for wid, w in online.bw.willitems.items()}
    txs = os.path.join(folder, "txs.txt")
    with open(txs, "w", encoding="utf-8") as f:
        f.write(",".join(str(w.tx) for w in _valid(online).values()))
    cases = [
        (stick, STILL_TO_SIGN_WARNING),
        (foreign, gui._PROBLEMS[engine.NOT_THIS_WALLET]),
        (txs, "This is not a will file. Choose the will file saved by the "
              "plugin on the online computer."),
    ]
    for path, message in cases:
        d.errors.clear()
        d.ask_open_path = lambda path=path: path
        assert not d.load_and_send()
        assert d.errors == [message]
    assert d.checks == [1]
    assert {wid: w.to_dict() for wid, w in online.bw.willitems.items()} == before
    d.ask_open_path = lambda: None
    assert not d.load_and_send() and d.checks == [1]


def test_engine_rules_of_the_online_steps(world):  # noqa: F811
    """The engine's reasons, read directly (see ``bal.engine.offline_sign``)."""
    folder = tempfile.mkdtemp(prefix="bal_route_")
    online = world("watch")
    assert engine.prepare_problem(online.bw.willitems) == engine.NO_WILL
    online.check()
    assert engine.prepare_problem(online.bw.willitems) is None
    assert sorted(engine.stick_items(online.bw.willitems)) == sorted(_valid(online))
    data = _items(_export(online, folder, "will.json"))
    items = online.bw._load_will_payload(data)
    assert engine.signed_file_problem(online.wallet, items) == engine.NOT_SIGNED
    for w in items.values():
        w.set_status("VALID", False)
    assert engine.signed_file_problem(online.wallet, items) == engine.NOTHING_TO_SIGN
    for w in online.bw.willitems.values():
        w.set_status("COMPLETE", True)
    assert engine.prepare_problem(online.bw.willitems) == engine.ALREADY_SIGNED
    assert engine.stick_items(online.bw.willitems) == {}


def test_signed_file_recognised_without_the_wallet_data(world, monkeypatch):  # noqa: F811
    """A fully signed transaction carries no input addresses: the engine
    reads them from the wallet's history, so the signed file is this
    wallet's even when Electrum adds no wallet data to it (found when the
    tests ran together: ``tests/test_will_summary.py`` turns that off)."""
    from electrum.transaction import Transaction

    folder = tempfile.mkdtemp(prefix="bal_route_")
    stick = os.path.join(folder, "to-sign.json")
    signed = os.path.join(folder, "signed.json")
    online = world("watch")
    d = _online(online, monkeypatch, save_as=stick)
    assert d.prepare_stick()
    _sign_on(world("keys"), monkeypatch, stick, signed)
    online.activate()
    monkeypatch.setattr(Transaction, "add_info_from_wallet",
                        lambda self, wallet, **kw: None)
    items = online.bw._load_will_payload(_items(signed))
    assert engine.signed_file_problem(online.wallet, items) is None
    other = world("msWatch")
    assert engine.signed_file_problem(other.wallet, items) == engine.NOT_THIS_WALLET


def test_step_1_without_a_will(world, monkeypatch):  # noqa: F811
    """No will (the Check could not build one): step 1 says how to create
    it and saves nothing."""
    online = world("watch")
    d = _online(online, monkeypatch, save_as="never-used.json")
    d.run_check = lambda: d.checks.append(1)
    assert not d.prepare_stick()
    assert d.title.text() == "There is no will to sign"
    assert '"Build / Edit Your Will"' in d.text.text()


# --------------------------------------------------- easy to find (owner)


def test_wizard_opens_step_1_on_a_watch_only_wallet(world, monkeypatch):  # noqa: F811
    """After the wizard's "Create the will" (its Check), a watch-only will
    waiting for its signatures opens the window; step 1 then does not run
    the Check again. A wallet with keys, or no will: nothing opens."""
    keys = world("keys")
    _dialog(keys, monkeypatch)
    assert keys.bw.after_wizard_create() is None
    online = world("watch")
    _dialog(online, monkeypatch)
    assert online.bw.after_wizard_create() is None
    online.check()
    d = online.bw.after_wizard_create()
    assert isinstance(d, gui.OnlineSignDialog) and d.just_checked
    d.checks = []
    d.run_check = lambda: d.checks.append(1)
    d.ask_save_path = lambda: None
    d.prepare_stick()
    assert d.checks == []
    d.prepare_stick()
    assert d.checks == [1]


def test_summary_box_leads_to_the_window(world, monkeypatch):  # noqa: F811
    """A watch-only will prepared and not signed: the box says the keys are
    elsewhere and its button opens the window (not "Sign and send")."""
    online = world("watch")
    online.check()
    bw = online.bw
    args = (bw.willitems, 2, bw.will_settings, True)
    plain = core_summary.will_summary(*args)
    assert plain["state"] == "to_sign" and plain["action"] == "sign_send"
    out = core_summary.will_summary(*args, watch_only=True)
    assert out["state"] == "to_sign" and out["kind"] == "warn"
    assert out["action"] == "offline_sign"
    assert out["action_text"] == "Sign the will offline"
    assert "watch-only" in out["note"]
    opened = []
    monkeypatch.setattr(bw, "show_offline_sign_dialog",
                        lambda **kw: opened.append(kw))
    bw.on_will_summary_action("offline_sign")
    assert opened == [{}]


def test_tools_menu_opens_the_step_to_make_now(world, monkeypatch):  # noqa: F811
    """Owner (ZIP test): the big button of the Will tab said "1." after the
    stick was saved and was missing on the offline computer; it is gone.
    Tools > "Sign the will offline" opens the window on the step to make
    now, without opening a file window by itself: the map at the start,
    step 5 once the stick is out, the choice of the file with the keys.
    The will summary follows at once when the stick is saved."""
    folder = tempfile.mkdtemp(prefix="bal_route_")
    stick = os.path.join(folder, "to-sign.json")
    signed = os.path.join(folder, "signed.json")
    online = world("watch")
    online.check()
    bw = online.bw
    # ``_dialog`` opens it as Tools > "Sign the will offline" does.
    first = _dialog(online, monkeypatch)
    assert isinstance(first, gui.OnlineSignDialog)
    assert first.pages.currentWidget() is first.map_page
    first.close()
    d = _online(online, monkeypatch, save_as=stick, open_path=signed)
    refreshed = []
    original = bw.update_will_summary
    monkeypatch.setattr(bw, "update_will_summary",
                        lambda: refreshed.append(1) or original())
    d.just_checked = True
    d.map_button.click()
    d.prepare_page.button.click()
    assert d.stick_path == stick and refreshed
    again = _dialog(online, monkeypatch)
    assert again.bar.current == engine.STEP_SEND
    assert again.pages.currentWidget() is again.send_page
    again.close()

    keys = world("keys")
    keys.activate()
    opened = []
    monkeypatch.setattr(gui.OfflineSignDialog, "ask_open_path",
                        lambda self: opened.append(1))
    k = _dialog(keys, monkeypatch)
    QApplication.processEvents()
    assert isinstance(k, gui.OfflineSignDialog) and opened == []
    assert k.pages.currentWidget() is k.choose_page
    k.choose_page.button.click()
    assert opened == [1]
    k.close()


def test_step_bar_ticks_the_steps_done():
    """The five steps (owner): the ones before the current step have a
    tick, the current one is filled with the colour of its computer (step
    3: orange, offline) and says "you are here", the others are pale; all
    ticked when the will is signed and sent; step 3 shows the signatures'
    count."""
    bar = gui.StepBar()
    assert sorted(bar.boxes) == [1, 2, 3, 4, 5]
    assert [bar.texts[n].text() for n in range(1, 6)] == [
        "Prepare the USB stick", "Take it to the offline PC", "Sign",
        "Bring it back to the online PC", "Send"]
    bar.mark(engine.STEP_SIGN)
    ticked = {n for n, label in bar.numbers.items() if "✓" in label.text()}
    assert ticked == {1, 2}
    filled = [n for n, box in bar.boxes.items()
              if "background:{}".format(gui.OFFLINE_COLOURS[0]) in box.styleSheet()]
    assert filled == [3]
    assert [n for n, here in bar.here.items() if not here.isHidden()] == [3]
    assert bar.here[3].text() == "you are here"
    # The steps done keep the colour of their computer: 1 online (teal).
    assert gui.ONLINE_COLOURS[1] in bar.boxes[1].styleSheet()
    bar.mark(engine.STEP_SEND)
    assert "background:{}".format(gui.TEAL) in bar.boxes[5].styleSheet()
    bar.mark(None, all_done=True)
    assert all(here.isHidden() for here in bar.here.values())
    assert all("✓" in label.text() for label in bar.numbers.values())
    bar.set_count(2, 3)
    assert bar.count.text() == "2 of 3" and not bar.count.isHidden()
    bar.set_count()
    assert bar.count.isHidden()


# ------------------------------------- online and offline look different


def test_route_starts_with_the_map_and_see_the_plan(world, monkeypatch):  # noqa: F811
    """Owner: a picture of what we will do before starting. The window
    opens on the route map at step 1 (no bar, "Start"); "Start" saves the
    stick; "See the plan" shows the map again and "Back" returns to the
    screen the user was on. Not at step 5: the window opens on step 5."""
    folder = tempfile.mkdtemp(prefix="bal_route_")
    stick = os.path.join(folder, "to-sign.json")
    online = world("watch")
    online.check()
    d = _online(online, monkeypatch, save_as=stick)
    assert d.pages.currentWidget() is d.map_page
    assert d.title.text() == "What we will do"
    assert d.bar.isHidden() and d.picture.isHidden() and d.map_link.isHidden()
    assert not d.map_label.pixmap().isNull()
    assert d.map_button.text() == "Start"
    d.just_checked = True
    d.map_button.click()
    assert d.pages.currentWidget() is d.prepare_page and d.stick_path is None
    d.prepare_page.button.click()
    assert d.stick_path == stick and d.bar.current == engine.STEP_CARRY
    assert not d.bar.isHidden() and not d.map_link.isHidden()
    d.map_link.click()
    assert d.pages.currentWidget() is d.map_page and d.map_button.text() == "Back"
    d.map_button.click()
    assert d.bar.current == engine.STEP_CARRY
    assert d.title.text() == "Sign the will on the offline computer"
    assert _link(d) == "I already have the signed USB stick →"
    # The stick is out: the window opens on step 5, without the map.
    again = _online(online, monkeypatch)
    assert again.bar.current == engine.STEP_SEND
    assert again.pages.currentWidget() is again.send_page


def test_each_computer_has_its_colour(world, monkeypatch):  # noqa: F811
    """The place label has the colour of the computer where the step
    happens: teal online (steps 1 and 5), orange offline (step 2 goes to
    the offline computer, step 3 is there)."""
    folder = tempfile.mkdtemp(prefix="bal_route_")
    stick = os.path.join(folder, "to-sign.json")
    online = world("watch")
    online.check()
    d = _online(online, monkeypatch, save_as=stick)
    d.show_prepare()
    assert gui.ONLINE_COLOURS[1] in d.where.styleSheet()
    d.just_checked = True
    assert d.prepare_stick()
    assert gui.OFFLINE_COLOURS[1] in d.where.styleSheet()
    keys = world("keys")
    keys.activate()
    k = _dialog(keys, monkeypatch)
    assert gui.OFFLINE_COLOURS[1] in k.where.styleSheet()
    assert k.where.text() == "On THIS computer (offline)"


def test_route_map_paints_both_kinds():
    """The map paints for one key and for the cosigners of a multisig, at
    the size asked (twice the pixels, for sharp screens)."""
    for multisig in (False, True):
        pix = gui.route_map(multisig, width=600)
        assert not pix.isNull()
        assert pix.width() == 1200 and pix.devicePixelRatio() == 2.0


def test_window_fits_the_screen_shown(world, monkeypatch):  # noqa: F811
    """Owner: the window was far too tall and stayed so after "See the
    plan". Only the page shown asks for room, and the window fits it: the
    screen of step 2 is shorter than the map, also after "Back"."""
    online = world("watch")
    online.check()
    d = _online(online, monkeypatch,
                save_as=os.path.join(tempfile.mkdtemp(), "s.json"))
    d.resize(820, 1400)
    d.fit_height()
    with_map = d.height()
    assert with_map < 1400
    d.just_checked = True
    d.map_button.click()
    d.prepare_page.button.click()
    d.fit_height()
    step_2 = d.height()
    assert step_2 < with_map
    d.map_link.click()
    d.fit_height()
    d.map_button.click()
    d.fit_height()
    assert d.height() == step_2
    for i in range(d.pages.count()):
        page = d.pages.widget(i)
        shown = page is d.pages.currentWidget()
        assert (page.sizePolicy().verticalPolicy()
                == (QSizePolicy.Policy.Preferred if shown
                    else QSizePolicy.Policy.Ignored))


def test_heir_amount_says_the_least(world, monkeypatch):  # noqa: F811
    """Owner (solution B): an amount that depends on the will-executor that
    delivers the will shows the least the heir gets ("at least ...") and a
    small "ⓘ" that says why; a fixed amount shows as is."""
    from types import SimpleNamespace

    d = _dialog(world("keys"), monkeypatch)
    d._amount = lambda sats: "{} sat".format(sats)
    heirs = [SimpleNamespace(name="alice", sats=1000, sats_max=1500, address="a1"),
             SimpleNamespace(name="bob", sats=700, sats_max=None, address="b1")]
    delivery = SimpleNamespace(
        heirs=heirs, messages=[], locktime=None, executors=[], change=[],
        backup=False, miner_fee=None, miner_fee_max=None)
    summary = SimpleNamespace(deliveries=[delivery], problems=[], to_sign=1, copies=1)
    monkeypatch.setattr(gui.engine, "file_summary", lambda wallet, items: summary)
    text = d.details_html()
    assert "at least 1000 sat" in text and "1500" not in text
    assert "700 sat" in text and "at least 700" not in text
    # One "ⓘ" (after alice's amount); a click says what the amount is.
    assert text.count(gui._AMOUNT_INFO_LINK) == 1
    shown = []
    d.show_message = lambda msg, *a, **kw: shown.append(msg)
    d.show_amount_info(gui._AMOUNT_INFO_LINK)
    assert shown == [d.amount_info_text()]
    assert "most expensive of the will-executors" in shown[0]
    heirs[0].sats_max = None
    assert gui._AMOUNT_INFO_LINK not in d.details_html()
