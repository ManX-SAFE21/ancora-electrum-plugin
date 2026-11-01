"""The second computer shows the truth (#209).

The owner's multisig is one person with one key on the online computer and
one on an offline computer, against a hacked online computer. Testnet4,
2026-11-01: a file whose heir output was redirected showed the same heirs and
was signed. Now the check lists every output read from the transactions, stops
the signing when the file pays what the will does not say, and asks to compare
the addresses with the ones on paper before every signature.

Test wallets: throw-away testnet keys made at every run (the wallets of
``tests/test_engine_merge.py``), three fake will-executors
(``tests/flowkit.py``). Nothing reaches the network.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_offline_real_outputs.py
"""

import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

import flowkit as fk
from electrum import bitcoin
from electrum.transaction import PartialTxOutput, tx_from_any
from test_engine_merge import PW, _export, world  # noqa: F401 - the fixture
from test_offline_sign import _dialog, _page

from bal.core.will import Will
from bal.engine import offline_sign as engine
from bal.gui.qt import offline_sign as gui

D1 = fk.ts(2030, 3, 1)
D2 = fk.ts(2033, 3, 1)
D3 = fk.ts(2036, 3, 1)
#: The hacker's address (a test address of nobody).
THIEF = fk.address(66)


def _edit_file(path, edit, only=None):
    """Rewrite the transactions of the will file at *path*, as a hacked
    computer can: ``edit(tx, item)`` on each copy, or the first *only* ones.
    The txids of the file stay as they were: the check must read the
    transaction, not the label."""
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    for number, item in enumerate(raw.values()):
        if only is not None and number >= only:
            break
        tx = tx_from_any(item["tx"])
        edit(tx, item)
        tx.invalidate_ser_cache()
        item["tx"] = str(tx)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(raw, f)


def _heir_output(tx, item):
    """The output of *tx* that pays the first heir of *item*."""
    address = next(row[0] for name, row in item["heirs"].items()
                   if not name.startswith('w!ll3x3c"'))
    return next(out for out in tx.outputs() if out.address == address)


def _redirect(out, address=THIEF):
    """Make the output *out* pay *address*."""
    out.scriptpubkey = bitcoin.address_to_script(address)


def _redirect_heir(tx, item):
    """The attack of the testnet test: the heir's money to the hacker."""
    _redirect(_heir_output(tx, item))


def _items(s, path):
    """The will file at *path*, loaded as the window loads it."""
    with open(path, encoding="utf-8") as f:
        items = s.bw._load_will_payload(json.load(f))
    Will.normalize_will(items, s.wallet)
    return items


def _kinds(s, path):
    """The kinds of the problems of the file at *path*."""
    return {p.kind for p in engine.payment_problems(s.wallet, _items(s, path))}


def _stick(world, kind="watch", deliveries=1):  # noqa: F811
    """The online computer's will on the USB stick: ``(path, folder)``.

    *deliveries*: 1 (no change), or 3 (39 copies, with a change).
    """
    online = world(kind)
    if deliveries == 3:
        online.set_heirs(alice="50%", bob="50%")
        online.set_instalments((D1, {"alice": "30%", "bob": "0%"}),
                               (D2, {"alice": "0%", "bob": "30%"}),
                               (D3, {"alice": "20%", "bob": "20%"}))
    online.check()
    folder = tempfile.mkdtemp(prefix="bal_offline_")
    return _export(online, folder, "will.json"), folder


def _no_password(s):
    """The wallet of *s* without a password (kept for automations)."""
    s.wallet.update_password(PW, None, encrypt_storage=False)
    s.user.password = None
    return s


def _offline(world, password=True):  # noqa: F811
    """The offline computer, with or without a wallet password."""
    s = world("keys")
    return s if password else _no_password(s)


# ------------------------------------------------------------ a good will


@pytest.mark.parametrize("password", [True, False], ids=["password", "no password"])
def test_good_file_lists_every_payment_and_signs_after_the_tick(
        world, monkeypatch, password):  # noqa: F811
    """Heirs, will-executors, change and miner fee, each delivery on its own;
    "Sign all" stays off until the addresses were compared."""
    stick, folder = _stick(world, deliveries=3)
    s = _offline(world, password)
    d = _dialog(s, monkeypatch, save_as=os.path.join(folder, "signed.json"))
    assert d.load_file(stick) and _page(d) == "check"
    text = d.details.text()
    for word in (fk.HEIR_ADDR["alice"], fk.HEIR_ADDR["bob"], "we0.test", "we1.test",
                 "we2.test", "Returns to your wallet", "Miner fee", "Delivery 1",
                 "Delivery 3", "Transactions to sign: 39 of 39"):
        assert word in text, word
    for we in fk.WE_ADDR[:3]:
        assert we in text
    assert gui.signing_warning() == d.paper_warning.text()
    assert not d.sign_button.isEnabled()
    d.paper_tick.setChecked(True)
    assert d.sign_button.isEnabled()
    d.paper_tick.setChecked(False)
    assert not d.sign_button.isEnabled()
    d.paper_tick.setChecked(True)
    s.act("sign all", d.sign_button.click)
    assert _page(d) == "save" and "Signed 39 of 39" in d.result.text()


def test_each_delivery_shows_its_own_amounts(world):  # noqa: F811
    """Before #209 one "at least" mixed the amounts of all the deliveries."""
    stick, _folder = _stick(world, deliveries=3)
    s = world("keys")
    summary = engine.file_summary(s.wallet, _items(s, stick))
    assert [d.locktime for d in summary.deliveries] == sorted(
        d.locktime for d in summary.deliveries)
    assert [[h.name for h in d.heirs] for d in summary.deliveries] == [
        ["alice"], ["bob"], ["alice", "bob"]]
    # Each copy of a delivery pays the same: no "at least" here.
    assert all(h.sats_max is None for d in summary.deliveries for h in d.heirs)
    # The first two leave a change, the last one spends all.
    assert [bool(d.change) for d in summary.deliveries] == [True, True, False]


def test_change_beyond_the_offline_addresses_is_recognized(world, monkeypatch):  # noqa: F811
    """The offline wallet never goes online and generated only its first
    addresses: a good change further on must not stop a good will."""
    online = world("watch")
    for _ in range(40):
        online.wallet.create_new_address(for_change=True)
    far = online.wallet.get_change_addresses()[-1]
    monkeypatch.setattr(online.wallet, "get_change_addresses_for_new_transaction",
                        lambda *a, **kw: [far])
    online.set_heirs(alice="50%", bob="50%")
    online.set_instalments((D1, {"alice": "60%", "bob": "0%"}),
                           (D2, {"alice": "0%", "bob": "40%"}))
    online.check()
    folder = tempfile.mkdtemp(prefix="bal_offline_")
    stick = _export(online, folder, "will.json")
    s = world("keys")
    assert not s.wallet.is_mine(far)
    summary = engine.file_summary(s.wallet, _items(s, stick))
    assert summary.problems == []
    assert summary.deliveries[0].change[0].address == far


def test_multisig_signers_are_not_stopped(world, monkeypatch):  # noqa: F811
    """A 2-of-3 with a change: each signer reads the same check, and the
    first signature does not hide the change from the second."""
    stick, folder = _stick(world, kind="msWatch", deliveries=3)
    a = world("msA")
    after_a = os.path.join(folder, "after-a.json")
    d = _dialog(a, monkeypatch, save_as=after_a)
    assert d.load_file(stick) and not d.blocked
    d.paper_tick.setChecked(True)
    a.act("sign all", d.sign_button.click)
    d.save_file()
    b = world("msB")
    d2 = _dialog(b, monkeypatch)
    assert d2.load_file(after_a) and not d2.blocked
    assert "Returns to your wallet" in d2.details.text()


# ---------------------------------------------------------- a hacked file


@pytest.mark.parametrize("password", [True, False], ids=["password", "no password"])
def test_redirected_output_stops_the_signing(world, monkeypatch, password):  # noqa: F811
    """The attack of the testnet test: the names stay, the money goes to the
    hacker. The window shows the address, no tick, and nothing can be signed,
    even if a window let the call through."""
    stick, _folder = _stick(world)
    _edit_file(stick, _redirect_heir)
    s = _offline(world, password)
    d = _dialog(s, monkeypatch)
    assert d.load_file(stick) and _page(d) == "check"
    assert d.blocked and THIEF in d.details.text()
    assert "Do not sign" in d.title.text()
    assert not d.paper_tick.isVisibleTo(d) and not d.sign_button.isVisibleTo(d)
    assert not d.sign_button.isEnabled()
    assert _kinds(s, stick) >= {engine.UNKNOWN_ADDRESS, engine.LESS_THAN_DECLARED}
    # The heir's line says the truth: nothing reaches that address now.
    summary = engine.file_summary(s.wallet, d.items)
    assert 0 in [h.sats for h in summary.deliveries[0].heirs]
    result, _outcome = engine.sign_file(s.wallet, d.items, PW if password else None)
    assert result.signed == [] and THIEF in result.error
    assert not any(w.get_status("COMPLETE") for w in d.items.values())


def test_whole_amount_has_no_lonely_point(world):  # noqa: F811
    """Real Electrum writes "0. mBTC": the owner's try read "at least 0. mBTC"."""
    stick, _folder = _stick(world)
    _edit_file(stick, _redirect_heir, only=1)
    s = world("keys")
    summary = engine.file_summary(s.wallet, _items(s, stick))
    text = gui.check_html(summary, lambda sats: "{}. mBTC".format(sats))
    assert ". mBTC" not in text and "0 mBTC" in text
    assert gui._no_lonely_point("6.96099 mBTC") == "6.96099 mBTC"
    assert gui._no_lonely_point("1.") == "1"


def test_one_copy_of_39_is_enough(world):  # noqa: F811
    """The hacker alters a single copy of a will in three deliveries."""
    stick, _folder = _stick(world, deliveries=3)
    _edit_file(stick, _redirect_heir, only=1)
    s = world("keys")
    problems = engine.payment_problems(s.wallet, _items(s, stick))
    assert engine.PaymentProblem(engine.UNKNOWN_ADDRESS, THIEF) in problems


def test_heir_receives_less(world):  # noqa: F811
    """The address is right, the amount is not."""
    stick, _folder = _stick(world)

    def smaller(tx, item):
        """Take 1000 sat from the heir."""
        _heir_output(tx, item).value -= 1000

    _edit_file(stick, smaller)
    s = world("keys")
    assert _kinds(s, stick) == {engine.LESS_THAN_DECLARED}


def test_change_redirected_with_its_path_is_not_the_wallet(world):  # noqa: F811
    """The path written in the file proves nothing: the hacker keeps the real
    change's path on an output that pays the hacker."""
    stick, _folder = _stick(world, deliveries=3)

    def steal_change(tx, item):
        """Redirect the output that carries a derivation path."""
        for out in tx.outputs():
            if out.bip32_paths:
                _redirect(out)

    _edit_file(stick, steal_change)
    s = world("keys")
    problems = engine.payment_problems(s.wallet, _items(s, stick))
    assert engine.PaymentProblem(engine.UNKNOWN_ADDRESS, THIEF) in problems


def test_hacker_rewrites_the_names_too(world, monkeypatch):  # noqa: F811
    """A file coherent with itself cannot be stopped: the false address is
    shown in full, and the address on paper is the defence (owner)."""
    stick, _folder = _stick(world)

    def rewrite(tx, item):
        """Redirect the heir and change the file's row to match."""
        out = _heir_output(tx, item)
        name = next(n for n, row in item["heirs"].items()
                    if row[0] == out.address)
        item["heirs"][name][0] = THIEF
        _redirect(out)

    _edit_file(stick, rewrite)
    d = _dialog(world("keys"), monkeypatch)
    assert d.load_file(stick) and not d.blocked
    assert THIEF in d.details.text()
    assert not d.sign_button.isEnabled()


def test_names_rewritten_in_one_copy_only_stop(world):  # noqa: F811
    """One person with two addresses in a delivery: the list shows one per
    person, so the second one would stay out of sight in a copy of many."""
    stick, _folder = _stick(world)

    def rewrite(tx, item):
        """As above, on one copy."""
        out = _heir_output(tx, item)
        name = next(n for n, row in item["heirs"].items()
                    if row[0] == out.address)
        item["heirs"][name][0] = THIEF
        _redirect(out)

    _edit_file(stick, rewrite, only=1)
    s = world("keys")
    assert _kinds(s, stick) == {engine.TWO_ADDRESSES}


def test_transaction_that_ignores_the_date_stops(world):  # noqa: F811
    """A final sequence number makes Bitcoin ignore the delivery date: the
    money could leave today, whatever date the window shows."""
    stick, _folder = _stick(world)

    def final(tx, item):
        """Turn the locktime off."""
        for txin in tx.inputs():
            txin.nsequence = 0xFFFFFFFF

    _edit_file(stick, final)
    s = world("keys")
    assert _kinds(s, stick) == {engine.NO_DATE}


def test_money_burned_in_a_script_stops(world):  # noqa: F811
    """An output with no address, with money on it, is no will's message."""
    stick, _folder = _stick(world)

    def burn(tx, item):
        """Add an OP_RETURN output with money."""
        tx._outputs.append(PartialTxOutput(value=1000, scriptpubkey=b"\x6a\x01\x00"))

    _edit_file(stick, burn)
    s = world("keys")
    assert _kinds(s, stick) == {engine.UNKNOWN_ADDRESS}


def test_will_details_route_refuses_the_hacked_file(world):  # noqa: F811
    """The older route (Will tab, Import, Will Details, Sign) is refused too."""
    stick, _folder = _stick(world)
    _edit_file(stick, _redirect_heir)
    s = world("keys")
    items = _items(s, stick)
    s.act("offline sign", lambda: s.bw.ask_password_and_sign_transactions(will=items))
    assert s.user.asked == []
    assert any(THIEF in str(p) for p in s.bw.popups)
    assert not any(w.get_status("COMPLETE") for w in items.values())


# ---------------------------------------------------- the online computer


@pytest.mark.parametrize("password", [True, False], ids=["password", "no password"])
def test_check_shows_the_addresses_before_signing(world, password):  # noqa: F811
    """Signing on the computer with the keys asks too, password or not."""
    s = _offline(world, password)
    s.check()
    assert len(s.user.warned) == 1
    assert fk.HEIR_ADDR["alice"] in s.user.warned[0]
    assert all(w.get_status("COMPLETE") for w in s.bw.willitems.values()
               if w.get_status("VALID"))


def test_no_to_the_warning_signs_nothing(world):  # noqa: F811
    """Saying no is the Cancel at the password: nothing is signed or asked."""
    s = world("keys")
    s.check(cancel_on=("paper",))
    assert len(s.user.warned) == 1 and s.user.asked == []
    assert not any(w.get_status("COMPLETE") for w in s.bw.willitems.values())


def test_watch_only_signs_nothing_and_is_not_warned(world):  # noqa: F811
    """No keys, no signature: nothing to warn about."""
    s = world("watch")
    s.check()
    assert s.user.warned == []


def test_warning_window_signs_only_after_the_tick():
    """The real window: "Sign" is off until the tick."""
    d = gui.SigningWarningDialog(None, "<p>alice</p>")
    assert not d.sign_button.isEnabled()
    d.tick.setChecked(True)
    assert d.sign_button.isEnabled()
