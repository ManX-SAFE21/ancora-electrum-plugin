"""One shared "Merge" (#158): signatures made elsewhere come back safely.

"Merge" puts into the live will the transactions of a will file: the way
the offline signing and a second multisig signer bring their signatures
back. Before #158 it was written twice (window, command line) and the local
validity pass after it three times; now it is ``bal.engine.merge``.

The analysis of 2026-10-16 (owner: offline signing on a cold wallet) ran
the whole file route on test wallets; it becomes permanent here:

* a watch-only online wallet (no keys): the Check prepares the will, the
  file goes to the offline wallet (the same key), is signed in the Will
  Details window and comes back with "Merge"; the Check then sends the
  three copies, all signed;
* a multisig 2-of-3: cosigner A signs (1 of 2), cosigner B signs A's file
  (2 of 2), A merges it and sends; and a watch-only multisig with A and B
  both offline (after A only: "1 of 2", nothing sent);
* H4 (safety): a file that says "signed" with an unsigned transaction
  inside no longer makes a signed copy, so nothing unsigned is sent;
* H3: a merged signed copy counts all its signatures;
* the message after "Merge" (owner) and the command line's counts.

Test wallets: throw-away testnet keys derived from public labels, made at
every run, never holding real coins; three fake will-executors
(``tests/flowkit.py``). Nothing reaches the network.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_engine_merge.py
"""

import hashlib
import json
import os
import re
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

import flowkit as fk
from electrum import constants, keystore
from electrum.bip32 import BIP32Node
from electrum.storage import WalletStorage
from electrum.transaction import tx_from_any
from electrum.wallet import Multisig_Wallet, restore_wallet_from_text
from electrum.wallet_db import WalletDB
from PyQt6.QtWidgets import QApplication

from bal.core.will import Will
from bal.gui.qt.common import write_json_file

_APP = QApplication.instance() or QApplication(sys.argv)

PW = "flowkit-test"
ROOT = os.path.join(os.path.dirname(__file__), os.pardir, "bal")


# ------------------------------------------------------------- wallets


def _node(label, xtype):
    """A BIP32 node from a public label (throw-away testnet key)."""
    seed = hashlib.sha256(label.encode()).digest()
    return BIP32Node.from_rootseed(seed, xtype=xtype)


def _xprv(label, xtype="p2wpkh"):
    """The private master key of *label*."""
    return _node(label, xtype).to_xprv(net=constants.BitcoinTestnet)


def _xpub(label, xtype="p2wpkh"):
    """The public master key of *label*."""
    return _node(label, xtype).to_xpub(net=constants.BitcoinTestnet)


def _multisig(config, path, mine, password):
    """A 2-of-3 wallet: cosigner *mine* ("A", "B" or None) has its key."""
    db = WalletDB("", storage=WalletStorage(path), upgrade=False)
    for i, name in enumerate("ABC"):
        label = "flowkit cosigner " + name
        if name == mine:
            ks = keystore.from_xprv(_xprv(label, "p2wsh"))
        else:
            ks = keystore.from_xpub(_xpub(label, "p2wsh"))
        db.put("x%d" % (i + 1), ks.dump())
    db.put("wallet_type", "2of3")
    wallet = Multisig_Wallet(db, config=config)
    if password and mine:
        wallet.update_password(None, password, encrypt_storage=False)
    wallet.synchronize()
    return wallet


def _maker(kind):
    """A ``flowkit._make_wallet`` that makes a wallet of *kind*."""

    def make(config, path, password, label):
        """The wallet of *kind* (``label`` is not used)."""
        if kind == "keys":
            return restore_wallet_from_text(
                _xprv("flowkit single"), path=path, config=config,
                password=password)["wallet"]
        if kind == "watch":
            return restore_wallet_from_text(
                _xpub("flowkit single"), path=path, config=config,
                password=None)["wallet"]
        mine = {"msA": "A", "msB": "B", "msWatch": None}[kind]
        return _multisig(config, path, mine, password)

    return make


@pytest.fixture
def world(monkeypatch):
    """Opens sessions (one wallet each) that share the fake world."""
    fk.ensure_event_loop()
    sessions = []

    def open_session(kind):
        """A wallet of *kind* with three fake will-executors."""
        monkeypatch.setattr(fk, "_make_wallet", _maker(kind))
        password = None if kind in ("watch", "msWatch") else PW
        s = fk.Session(monkeypatch, mode="advanced", password=password)
        s.world.add_executor()
        monkeypatch.setattr(s.wallet, "is_up_to_date", lambda: True)
        sessions.append(s)
        s.others = sessions
        return s

    try:
        yield open_session
    finally:
        for s in sessions:
            s.close()
        fk.stop_event_loop()


# ------------------------------------------------------------- helpers


def _valid(s):
    """The VALID copies of the session's live will."""
    return {wid: w for wid, w in s.bw.willitems.items() if w.get_status("VALID")}


def _complete(w):
    """True when the copy's stored transaction is fully signed."""
    return tx_from_any(str(w.tx)).is_complete()


def _export(s, folder, name, mark_signed=False):
    """The will file as the export writes it (the USB stick).

    ``mark_signed``: the file says "signed" for every copy, whatever is
    inside (an old or damaged file, H4).
    """
    data = {wid: w.to_dict() for wid, w in _valid(s).items()}
    path = os.path.join(folder, name)
    write_json_file(path, data)
    if mark_signed:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        for item in raw.values():
            item["status"] = item["status"] + ".Signed"
            item["COMPLETE"] = True
        with open(path, "w", encoding="utf-8") as f:
            json.dump(raw, f)
    return path


def _sign_offline(s, path, folder, name):
    """The offline computer: import the file into Will Details, Sign, save."""
    with open(path, encoding="utf-8") as f:
        items = s.bw._load_will_payload(json.load(f))
    Will.normalize_will(items, s.wallet)
    for wi in items.values():
        wi.set_status("IMPORTED", True)
    s.act("offline sign", lambda: s.bw.ask_password_and_sign_transactions(will=items))
    out = os.path.join(folder, name)
    write_json_file(out, {wid: w.to_dict() for wid, w in items.items()})
    return out


def _merge(s, path):
    """The online computer: "Merge" the file; returns the message shown."""
    s.activate()
    s.act("merge", lambda: s.bw.merge_will_from_file(path))
    messages = [p[1] for p in s.bw.popups if p[0] == "message"]
    return messages[-1] if messages else None


def _pushed_complete(s):
    """For each copy sent: is it signed?

    Every session installs its own fake world when it opens, so the sends
    of an earlier session's Check are recorded in the world of the session
    opened last: :func:`_clear_pushes` / this read every world.
    """
    return [tx_from_any(raw).is_complete()
            for other in s.others for _u, _w, raw in other.world.pushed]


def _clear_pushes(s):
    """Forget the sends recorded so far (in every session's world)."""
    for other in s.others:
        other.world.pushed.clear()


# ---------------------------------------------------------- watch-only


def test_watch_only_online_offline_signing_by_file(world):
    """Watch-only online, keys offline: prepare, carry, sign, merge, send."""
    folder = tempfile.mkdtemp(prefix="bal_merge_")
    online = world("watch")
    assert online.wallet.is_watching_only()
    online.check()
    assert not any(w.get_status("COMPLETE") for w in _valid(online).values())
    assert _pushed_complete(online) == []
    will_file = _export(online, folder, "will.json")
    offline = world("keys")
    signed_file = _sign_offline(offline, will_file, folder, "will_signed.json")
    message = _merge(online, signed_file)
    assert message == "Merge done: 3 transaction(s) signed, 0 still to sign."
    for w in _valid(online).values():
        assert w.get_status("COMPLETE") and _complete(w)
        assert w.sigs_have == w.sigs_required == 1  # H3
    _clear_pushes(online)
    online.check()
    assert _pushed_complete(online) == [True, True, True]


def test_file_saying_signed_is_not_trusted(world):
    """H4: the file says "signed", the transactions inside are not: the
    copies stay unsigned, the message says so, the Check sends nothing."""
    folder = tempfile.mkdtemp(prefix="bal_merge_")
    online = world("watch")
    online.check()
    lying = _export(online, folder, "will_lying.json", mark_signed=True)
    with open(lying, encoding="utf-8") as f:
        assert all("Signed" in i["status"] for i in json.load(f).values())
    message = _merge(online, lying)
    assert message == "Merge done: 0 transaction(s) signed, 3 still to sign."
    assert not any(w.get_status("COMPLETE") for w in _valid(online).values())
    _clear_pushes(online)
    online.check()
    assert _pushed_complete(online) == []


def test_file_saying_signed_into_an_empty_will(world):
    """H4 for copies new to this wallet (the will was empty): added, but
    not marked signed."""
    folder = tempfile.mkdtemp(prefix="bal_merge_")
    first = world("watch")
    first.check()
    lying = _export(first, folder, "will_lying.json", mark_signed=True)
    fresh = world("watch")
    assert not fresh.bw.willitems
    _merge(fresh, lying)
    assert fresh.bw.willitems
    assert not any(w.get_status("COMPLETE") for w in fresh.bw.willitems.values())


# ------------------------------------------------------------ multisig


def test_multisig_second_signer_by_file(world):
    """2-of-3, online = cosigner A (signs 1 of 2, sends nothing); B signs
    A's file (2 of 2); A merges it and sends three signed copies."""
    folder = tempfile.mkdtemp(prefix="bal_merge_")
    a = world("msA")
    a.check()
    items = _valid(a)
    assert all(w.get_status("PARTIALLY_SIGNED") for w in items.values())
    assert all((w.sigs_have, w.sigs_required) == (1, 2) for w in items.values())
    assert _pushed_complete(a) == []
    file_a = _export(a, folder, "will_A.json")
    b = world("msB")
    file_ab = _sign_offline(b, file_a, folder, "will_AB.json")
    message = _merge(a, file_ab)
    assert message == "Merge done: 3 transaction(s) signed, 0 still to sign."
    for w in _valid(a).values():
        assert w.get_status("COMPLETE") and _complete(w)
        assert (w.sigs_have, w.sigs_required) == (2, 2)  # H3
    _clear_pushes(a)
    a.check()
    assert _pushed_complete(a) == [True, True, True]


def test_multisig_watch_only_two_offline_signers(world):
    """Watch-only multisig online; A signs offline: merged, "1 of 2" and
    nothing sent; then B signs A's file: merged, signed, sent."""
    folder = tempfile.mkdtemp(prefix="bal_merge_")
    online = world("msWatch")
    online.check()
    will_file = _export(online, folder, "will.json")
    a = world("msA")
    file_a = _sign_offline(a, will_file, folder, "will_A.json")
    message = _merge(online, file_a)
    assert message == (
        "Merge done: 0 transaction(s) signed, 3 still to sign.\n"
        "3 transaction(s) have only 1 of 2 signatures: another signer must "
        "sign them.")
    assert not any(w.get_status("COMPLETE") for w in _valid(online).values())
    _clear_pushes(online)
    online.check()
    assert _pushed_complete(online) == []
    b = world("msB")
    file_ab = _sign_offline(b, file_a, folder, "will_AB.json")
    message = _merge(online, file_ab)
    assert message == "Merge done: 3 transaction(s) signed, 0 still to sign."
    _clear_pushes(online)
    online.check()
    assert _pushed_complete(online) == [True, True, True]


# --------------------------------------------------------- command line


def test_cli_merge_counts(world):
    """``bal_will_merge``: the same rules, and the counts in its answer."""
    folder = tempfile.mkdtemp(prefix="bal_merge_")
    online = world("watch")
    online.check()
    online.bw.save_willitems()
    will_file = _export(online, folder, "will.json")
    lying = _export(online, folder, "will_lying.json", mark_signed=True)
    offline = world("keys")
    signed_file = _sign_offline(offline, will_file, folder, "will_signed.json")
    online.activate()
    out = online.cli("merge_will_from_file", lying)["returned"]
    assert out == {"merged": 3, "signed": 0, "partly_signed": 0, "unsigned": 3}
    out = online.cli("merge_will_from_file", signed_file)["returned"]
    assert out == {"merged": 3, "signed": 3, "partly_signed": 0, "unsigned": 0}


# ---------------------------------------------------------- file windows


def test_file_windows_show_every_file_and_save_json():
    """Owner's ZIP test (#158): the will export saved a name without
    ".json" and the import windows, JSON first, hid it. Now the export
    saves "will.json" and adds ".json" to a name typed without it; the
    import windows keep JSON first (owner) with every file second."""
    from types import SimpleNamespace
    from unittest.mock import patch

    from bal.gui.qt import common

    win = SimpleNamespace(config=None, wallet="w", show_critical=print,
                          show_message=lambda *a: None)
    assert common.IMPORT_FILE_FILTER == "JSON (*.json);;All files (*)"
    with patch.object(common, "getSaveFileName", return_value="") as save:
        common.export_meta_gui(win, "will.json", lambda path: None)
        assert save.call_args.kwargs["default_extension"] == "json"
        assert save.call_args.kwargs["filename"].endswith("will.json")
        common.export_meta_gui(win, "will_tx", lambda path: None)
        assert save.call_args.kwargs["default_extension"] is None
    with open(os.path.join(ROOT, "gui", "qt", "dialogs.py"), encoding="utf-8") as f:
        assert 'title = "will.json"' in f.read()


# ----------------------------------------------------------- one copy


def _code(path):
    """The code of *path* without comments."""
    with open(path, encoding="utf-8") as f:
        return "\n".join(line.split("#", 1)[0] for line in f)


def test_no_other_copy_of_the_merge():
    """Only ``bal/engine/merge.py`` merges will copies
    (``combine_with_other_psbt`` on a will item) or runs the local validity
    pass (``Will.search_rai``); ``bal/core/will.py`` keeps its own uses
    (the wallet history, the will check)."""
    combine = re.compile(r"\.tx\.combine_with_other_psbt\(")
    validity = re.compile(r"Will\.search_rai\(")
    found_combine, found_validity = set(), set()
    for folder, _dirs, files in os.walk(ROOT):
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(folder, name)
            rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
            code = _code(path)
            if combine.search(code):
                found_combine.add(rel)
            if validity.search(code):
                found_validity.add(rel)
    assert found_combine <= {"engine/merge.py", "core/will.py"}, found_combine
    assert "engine/merge.py" in found_combine
    assert found_validity == {"engine/merge.py", "core/will.py"}, found_validity


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
