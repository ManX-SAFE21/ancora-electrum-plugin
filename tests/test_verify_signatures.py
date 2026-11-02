"""Signatures are proven, and an altered file does not enter the will (#210).

Testnet4, 2026-11-01: ``bal_will_import_merge`` accepted a file whose outputs
were changed, ``bal_will_sign`` signed it silently, and a first signature made
invalid still reached "2 of 2", "signed". The owner's multisig is one person
against a hacked online computer, so what comes in a file is proven: every
signature with this wallet's keys, every copy against the will.

Test wallets: throw-away testnet keys made at every run (the wallets of
``tests/test_engine_merge.py``), three fake will-executors
(``tests/flowkit.py``). Nothing reaches the network.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_verify_signatures.py
"""

import json
import os
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

import flowkit as fk
from electrum.bitcoin import construct_witness
from electrum.transaction import (
    PartialTransaction,
    PartialTxInput,
    PartialTxOutput,
    TxOutpoint,
    TxOutput,
    tx_from_any,
)
from electrum.util import FileImportFailed, UserFacingException
from test_engine_merge import (  # noqa: F401 - the fixture
    PW,
    _clear_pushes,
    _complete,
    _export,
    _merge,
    _pushed_complete,
    _valid,
    world,
    write_json_file,
)
from test_offline_real_outputs import (
    D1,
    D2,
    D3,
    THIEF,
    _edit_file,
    _heir_output,
    _items,
    _no_password,
    _redirect,
    _redirect_heir,
    _stick,
)
from test_offline_sign import _dialog
from test_sign_offline_online import _online

from bal.cli.controller import BalController
from bal.core.will import Will, WillItem
from bal.engine import offline_sign as engine
from bal.engine.sign import sign_will
from bal.engine.signatures import asks_unsafe_signature, check_copy

#: "Merge done" with the whole will of three deliveries signed.
ALL_SIGNED = "Merge done: 39 transaction(s) signed, 0 still to sign."


def _sign(s, path, name, password=PW):
    """The computer of *s* signs the file at *path*: the path of the new file."""
    items = _items(s, path)
    result, _outcome = engine.sign_file(s.wallet, items, password)
    assert result.error is None and result.signed
    out = os.path.join(os.path.dirname(path), name)
    write_json_file(out, engine.signed_file_data(items))
    return out


def _flip(signature):
    """The same signature with one bit of its first number changed."""
    wrong = bytearray(signature)
    wrong[10] ^= 1
    return bytes(wrong)


def _break_partial(tx, _item):
    """Every signature waiting for the next signer becomes false."""
    for txin in tx.inputs():
        for key, signature in list(txin.sigs_ecdsa.items()):
            txin.sigs_ecdsa[key] = _flip(signature)


def _break_final(tx, _item):
    """Every signature of a finished transaction becomes false."""
    for txin in tx.inputs():
        txin.witness = construct_witness(
            [_flip(el) if el and el[0] == 0x30 else el
             for el in txin.witness_elements()])


def _three_deliveries(online):
    """The session *online* with its will in three deliveries, checked."""
    online.set_heirs(alice="50%", bob="50%")
    online.set_instalments((D1, {"alice": "30%", "bob": "0%"}),
                           (D2, {"alice": "0%", "bob": "30%"}),
                           (D3, {"alice": "20%", "bob": "20%"}))
    online.check()
    return online


def _refused(s):
    """The error shown when the merge of *s* was refused, or ``None``."""
    errors = [str(p) for p in s.bw.popups if "was not merged" in str(p)]
    return errors[-1] if errors else None


def _signed_copies(s):
    """How many copies of the will of *s* are signed."""
    return sum(bool(w.get_status("COMPLETE")) or _complete(w)
               for w in s.bw.willitems.values())


def _cli(s):
    """The command-line controller on the wallet of session *s*."""
    ctl = object.__new__(BalController)
    ctl.wallet = s.wallet
    ctl.plugin = s.bw.bal_plugin
    ctl.will_settings = s.bw.will_settings
    ctl.date_to_check = None
    ctl.willitems = {}
    ctl.willexecutors = {"https://we{}.test".format(i): {"selected": True}
                         for i in range(3)}
    ctl.save_willitems = MagicMock()
    ctl._save_to_history = MagicMock()
    ctl._refuse_when_held = MagicMock()
    return ctl


def _file_in(ctl, path):
    """The copies of the file at *path*, as ``bal_will_import_merge`` reads it."""
    return BalController._load_will_file(ctl, path)


# ---------------------------------------------------------------- good files


@pytest.mark.parametrize("password", [True, False], ids=["password", "no password"])
def test_good_signatures_are_proven_on_both_computers(world, password):  # noqa: F811
    """One key, three deliveries (the later ones spend a change not on the
    blockchain yet), with and without a password: proven and merged."""
    stick, _folder = _stick(world, deliveries=3)
    online = _three_deliveries(world("watch"))
    assert set(_items(online, stick)) == set(online.bw.willitems)
    offline = world("keys")
    if not password:
        _no_password(offline)
    signed = _sign(offline, stick, "signed.json", PW if password else None)
    items = _items(offline, signed)
    results = [check_copy(offline.wallet, items, w) for w in items.values()]
    assert len(results) == 39
    assert all(r.have == r.required == 1 and not r.bad for r in results)
    # Watch-only, on the online computer: its keys are public, that is enough.
    everything = dict(online.bw.willitems)
    everything.update(items)
    for w in items.values():
        result = check_copy(online.wallet, everything, w)
        assert result.have == result.required == 1 and not result.bad
    assert _merge(online, signed) == ALL_SIGNED
    assert _refused(online) is None
    assert all(_complete(w) for w in _valid(online).values())
    _clear_pushes(online)
    online.check()
    assert _pushed_complete(online) and all(_pushed_complete(online))


def _multisig_online(world):  # noqa: F811
    """The online multisig wallet with a will in three deliveries: ``(s, stick)``."""
    online = _three_deliveries(world("msWatch"))
    return online, _export(online, tempfile.mkdtemp(prefix="bal_210_"), "will.json")


def test_multisig_counts_only_what_is_proven(world):  # noqa: F811
    """The owner's route: 1 of 2 on the second signer, 2 of 2 back online."""
    online, stick = _multisig_online(world)
    a, b = world("msA"), world("msB")
    after_a = _sign(a, stick, "after-a.json")
    items = _items(b, after_a)
    for w in items.values():
        result = check_copy(b.wallet, items, w)
        assert (result.have, result.required, result.bad) == (1, 2, False)
    assert engine.payment_problems(b.wallet, items) == []
    after_b = _sign(b, after_a, "after-b.json")
    items = _items(online, after_b)
    everything = dict(online.bw.willitems)
    everything.update(items)
    for w in items.values():
        result = check_copy(online.wallet, everything, w)
        assert (result.have, result.required, result.bad) == (2, 2, False)
    assert _merge(online, after_b) == ALL_SIGNED
    _clear_pushes(online)
    online.check()
    assert _pushed_complete(online) and all(_pushed_complete(online))


def test_signatures_can_come_back_one_at_a_time(world):  # noqa: F811
    """The first signature merged online, then the second: they add up."""
    online, stick = _multisig_online(world)
    after_a = _sign(world("msA"), stick, "after-a.json")
    _merge(online, after_a)
    assert _refused(online) is None
    assert not any(_complete(w) for w in _valid(online).values())
    after_b = _sign(world("msB"), after_a, "after-b.json")
    assert _merge(online, after_b) == ALL_SIGNED


def test_merging_again_or_with_no_signature_is_not_a_problem(world):  # noqa: F811
    """The same signed file twice; the file still to sign: nothing to refuse."""
    stick, _folder = _stick(world)
    online = world("watch")
    online.check()
    _merge(online, stick)
    assert _refused(online) is None
    signed = _sign(world("keys"), stick, "signed.json")
    _merge(online, signed)
    _merge(online, signed)
    assert _refused(online) is None
    assert all(_complete(w) for w in _valid(online).values())


def test_later_delivery_alone_finds_its_coin_in_the_will(world):  # noqa: F811
    """A file with only the last delivery: its coin is a change of the live
    will, which is not on the blockchain and not in the file."""
    online, stick = _multisig_online(world)
    after_b = _sign(world("msB"), _sign(world("msA"), stick, "a.json"), "b.json")
    with open(after_b, encoding="utf-8") as f:
        raw = json.load(f)
    last = max(tx_from_any(item["tx"]).locktime for item in raw.values())
    only = {wid: item for wid, item in raw.items()
            if tx_from_any(item["tx"]).locktime == last}
    assert 0 < len(only) < len(raw)
    part = os.path.join(os.path.dirname(after_b), "part.json")
    with open(part, "w", encoding="utf-8") as f:
        json.dump(only, f)
    _merge(online, part)
    assert _refused(online) is None


def test_new_copies_of_a_good_file_enter_a_will_with_none(world):  # noqa: F811
    """The same wallet on another computer, with no will: signed copies come
    in, with their change, which a finished transaction shows with no path."""
    stick, _folder = _stick(world, deliveries=3)
    signed = _sign(world("keys"), stick, "signed.json")
    other = world("watch")
    assert other.bw.willitems == {}
    _merge(other, signed)
    assert _refused(other) is None
    assert len(other.bw.willitems) == 39


# ------------------------------------------------------------ false signatures


def test_false_first_signature_stops_the_second_signer(world, monkeypatch):  # noqa: F811
    """The testnet finding: it reached "2 of 2". Now the window says it, and
    nothing can be signed, even if a window lets the call through."""
    online, stick = _multisig_online(world)
    after_a = _sign(world("msA"), stick, "after-a.json")
    _edit_file(after_a, _break_partial)
    b = world("msB")
    d = _dialog(b, monkeypatch)
    assert d.load_file(after_a) and d.blocked
    assert "Do not sign" in d.title.text() and "not valid" in d.details.text()
    assert "money" not in d.details.text()
    assert not d.sign_button.isVisibleTo(d)
    result, _outcome = engine.sign_file(b.wallet, d.items, PW)
    assert result.signed == [] and "not valid" in result.error
    assert not any(w.get_status("COMPLETE") for w in d.items.values())
    before = {wid: str(w.tx) for wid, w in online.bw.willitems.items()}
    _merge(online, after_a)
    assert "not valid" in _refused(online)
    assert {wid: str(w.tx) for wid, w in online.bw.willitems.items()} == before


def test_one_false_signature_of_39_stops_the_file(world):  # noqa: F811
    """One copy is enough, and nothing of the file enters the will."""
    online, stick = _multisig_online(world)
    after_b = _sign(world("msB"), _sign(world("msA"), stick, "a.json"), "b.json")
    _edit_file(after_b, _break_final, only=1)
    _merge(online, after_b)
    assert "not valid" in _refused(online)
    assert not any(_complete(w) for w in online.bw.willitems.values())


def test_a_signature_cannot_count_for_another_key(world):  # noqa: F811
    """Signer A's signature written under B's key: Electrum counts 2 of 2 and
    would finish the transaction; it is one signature, so it is not."""
    online, stick = _multisig_online(world)
    after_a = _sign(world("msA"), stick, "after-a.json")

    def twice(tx, _item):
        """The same signature under a second key of the wallet."""
        for txin in tx.inputs():
            signature = next(iter(txin.sigs_ecdsa.values()))
            other = next(k for k in txin.bip32_paths if k not in txin.sigs_ecdsa)
            txin.sigs_ecdsa[other] = signature

    _edit_file(after_a, twice)
    _merge(online, after_a)
    assert "not valid" in _refused(online)
    assert not any(_complete(w) for w in online.bw.willitems.values())


def test_a_signature_of_a_key_of_nobody_does_not_count(world):  # noqa: F811
    """A key the wallet does not derive, written in the file."""
    online, stick = _multisig_online(world)
    after_a = _sign(world("msA"), stick, "after-a.json")

    def stranger(tx, _item):
        """One more signature, under a key of nobody."""
        for txin in tx.inputs():
            signature = next(iter(txin.sigs_ecdsa.values()))
            txin.sigs_ecdsa[b"\x02" + b"\x07" * 32] = signature

    _edit_file(after_a, stranger)
    _merge(online, after_a)
    assert "not valid" in _refused(online)


def test_a_signature_must_cover_the_outputs(world):  # noqa: F811
    """Not SIGHASH_ALL: whoever holds the file could change the outputs."""
    online, stick = _multisig_online(world)
    after_a = _sign(world("msA"), stick, "after-a.json")

    def other_kind(tx, _item):
        """The kind of the signature becomes SIGHASH_NONE."""
        for txin in tx.inputs():
            for key, signature in list(txin.sigs_ecdsa.items()):
                txin.sigs_ecdsa[key] = signature[:-1] + b"\x02"

    _edit_file(after_a, other_kind)
    _merge(online, after_a)
    assert "not valid" in _refused(online)


def test_finished_transaction_with_no_signature_is_not_signed(world):  # noqa: F811
    """A transaction written plainly says "complete" with nothing inside (#158
    trusted that): the Check would send it to the will-executors."""
    stick, _folder = _stick(world)
    online = world("watch")
    online.check()

    def plain(tx, item):
        """The transaction without its PSBT data, no signature."""
        item["tx"] = tx.serialize_to_network(include_sigs=False)

    with open(stick, encoding="utf-8") as f:
        raw = json.load(f)
    for item in raw.values():
        plain(tx_from_any(item["tx"]), item)
    with open(stick, "w", encoding="utf-8") as f:
        json.dump(raw, f)
    _merge(online, stick)
    assert "not valid" in _refused(online)
    assert not any(_complete(w) for w in online.bw.willitems.values())


def test_garbage_is_bad_and_raises_nothing(world):  # noqa: F811
    """Whatever the file holds, the answer is "bad"."""
    s = world("keys")
    assert check_copy(s.wallet, {}, SimpleNamespace(tx="not a transaction")).bad


def test_the_count_under_step_3_comes_from_the_transaction(world, monkeypatch):  # noqa: F811
    """The file writes a count of its own: it must not show."""
    online, stick = _multisig_online(world)
    with open(stick, encoding="utf-8") as f:
        raw = json.load(f)
    for item in raw.values():
        item["sigs_have"] = 1
    with open(stick, "w", encoding="utf-8") as f:
        json.dump(raw, f)
    d = _dialog(world("msA"), monkeypatch)
    assert d.load_file(stick)
    assert d.bar.count.text().startswith("0")


# --------------------------------------------------------------- altered files


@pytest.mark.parametrize("complete", [False, True], ids=["to sign", "says signed"])
def test_a_changed_copy_does_not_enter_the_will(world, complete):  # noqa: F811
    """The audit's finding: the file replaced the will's transaction. The
    statuses are the file's too: they cannot hide a copy from the check."""
    stick, _folder = _stick(world, deliveries=3)
    online = _three_deliveries(world("watch"))
    before = {wid: str(w.tx) for wid, w in online.bw.willitems.items()}
    assert set(_items(online, stick)) == set(before)
    _edit_file(stick, _redirect_heir, only=1)
    if complete:
        with open(stick, encoding="utf-8") as f:
            raw = json.load(f)
        for item in raw.values():
            item["COMPLETE"] = True
            item["VALID"] = False
        with open(stick, "w", encoding="utf-8") as f:
            json.dump(raw, f)
    _merge(online, stick)
    assert "it was changed" in _refused(online)
    assert {wid: str(w.tx) for wid, w in online.bw.willitems.items()} == before


def test_a_changed_copy_is_refused_also_when_the_will_is_signed(world):  # noqa: F811
    """A signed copy stays as it is, and the user hears about the file."""
    stick, _folder = _stick(world)
    s = world("keys")
    s.check()
    signed = {wid: str(w.tx) for wid, w in s.bw.willitems.items()}
    assert set(_items(s, stick)) == set(signed)
    _edit_file(stick, _redirect_heir)
    _merge(s, stick)
    assert "it was changed" in _refused(s)
    assert {wid: str(w.tx) for wid, w in s.bw.willitems.items()} == signed


def test_a_new_altered_copy_does_not_enter_a_will(world):  # noqa: F811
    """A will that does not know the copy: the file's own names are compared."""
    stick, _folder = _stick(world)
    _edit_file(stick, _redirect_heir)
    s = world("keys")
    assert s.bw.willitems == {}
    _merge(s, stick)
    assert THIEF in _refused(s)
    assert s.bw.willitems == {}


def test_online_window_says_it_and_sends_nothing(world, monkeypatch):  # noqa: F811
    """Step 5 with a false signature: the error, no Check, nothing sent."""
    stick, _folder = _stick(world)
    online = world("watch")
    online.check()
    signed = _sign(world("keys"), stick, "signed.json")
    _edit_file(signed, _break_final)
    d = _online(online, monkeypatch)
    assert d.load_file(signed) is False
    assert d.checks == []
    assert "not valid" in _refused(online)


# ---------------------------------------------------------- the command line


def test_cli_merge_refuses_and_changes_nothing(world):  # noqa: F811
    """``bal_will_import_merge`` on the altered file of the testnet test."""
    stick, _folder = _stick(world)
    _edit_file(stick, _redirect_heir)
    ctl = _cli(world("keys"))
    with pytest.raises(UserFacingException) as e:
        BalController.merge_will(ctl, _file_in(ctl, stick))
    assert THIEF in str(e.value) and ctl.willitems == {}
    ctl.save_willitems.assert_not_called()


def test_cli_sign_refuses_an_altered_will(world):  # noqa: F811
    """``bal_will_sign`` signed it silently, even if it is already in the will
    (merged before this step)."""
    stick, _folder = _stick(world)
    _edit_file(stick, _redirect_heir)
    ctl = _cli(world("keys"))
    ctl.willitems = _file_in(ctl, stick)
    Will.normalize_will(ctl.willitems, ctl.wallet)
    with pytest.raises(UserFacingException) as e:
        BalController.sign_transactions(ctl, PW)
    assert THIEF in str(e.value)
    assert not any(tx_from_any(str(w.tx)).is_complete() for w in ctl.willitems.values())
    ctl.save_willitems.assert_not_called()


@pytest.mark.parametrize("password", [True, False], ids=["password", "no password"])
def test_cli_second_signer_route_still_works(world, password):  # noqa: F811
    """The route of the testnet test on a good file: merge, sign, 2 of 2."""
    online, stick = _multisig_online(world)
    after_a = _sign(world("msA"), stick, "after-a.json")
    b = world("msB")
    if password is False:
        _no_password(b)
    ctl = _cli(b)
    BalController.merge_will(ctl, _file_in(ctl, after_a))
    assert len(ctl.willitems) == 39
    assert BalController.sign_transactions(ctl, PW if password else None)
    assert all(_complete(w) for w in ctl.willitems.values())
    for w in ctl.willitems.values():
        assert not check_copy(ctl.wallet, ctl.willitems, w).bad


def test_cli_refuses_a_false_first_signature_and_signs_nothing(world):  # noqa: F811
    """The second signer on the command line, false first signature."""
    online, stick = _multisig_online(world)
    after_a = _sign(world("msA"), stick, "after-a.json")
    _edit_file(after_a, _break_partial)
    ctl = _cli(world("msB"))
    with pytest.raises(UserFacingException):
        BalController.merge_will(ctl, _file_in(ctl, after_a))
    assert ctl.willitems == {}


# ------------------------------------------- the independent review of #210


def _no_history(s):
    """The real offline computer: it never went online, it knows no coin."""
    for txid in list(s.wallet.db.list_transactions()):
        s.wallet.adb.remove_transaction(txid)
    return s


def _ask_for(kind):
    """An edit of the file: every input asks for a signature of this *kind*."""

    def edit(tx, _item):
        """The PSBT field the next signer's wallet obeys."""
        for txin in tx.inputs():
            txin.sighash = kind

    return edit


@pytest.mark.parametrize("kind", [3, 0x81, 0x83, 2])
def test_a_weak_signature_is_never_made(world, kind):  # noqa: F811
    """The outputs are all right, the field asks for a signature that leaves
    them free: the money could go anywhere after it. Nothing is signed."""
    stick, _folder = _stick(world)
    _edit_file(stick, _ask_for(kind))
    s = world("keys")
    items = _items(s, stick)
    assert engine.INVALID_SIGNATURE in {
        p.kind for p in engine.payment_problems(s.wallet, items)}
    result, _outcome = engine.sign_file(s.wallet, items, PW)
    assert result.signed == []
    # Any other route: the one signing refuses it by itself.
    direct = sign_will(s.wallet, items, PW)
    assert direct.signed == [] and direct.error is not None
    assert not any(_complete(w) for w in items.values())


def test_a_weak_signature_does_not_enter_the_will(world):  # noqa: F811
    """The field in a good partly signed file: merged, it would reach the next
    signature made here."""
    online, stick = _multisig_online(world)
    after_a = _sign(world("msA"), stick, "after-a.json")
    _edit_file(after_a, _ask_for(3))
    _merge(online, after_a)
    assert "not valid" in _refused(online)
    assert not any(engine_asks(w) for w in online.bw.willitems.values())


def engine_asks(item):
    """True when the copy *item* asks for a weak signature."""
    return asks_unsafe_signature(tx_from_any(str(item.tx)))


def _false_coin(tx, _item):
    """The file lies about the value of the coin spent."""
    for txin in tx.inputs():
        coin = txin.witness_utxo
        if coin is not None:
            # The whole previous transaction would give the lie away (the
            # property refuses None: this is what a file without it reads as).
            txin._utxo = None
            txin.witness_utxo = TxOutput(scriptpubkey=coin.scriptpubkey,
                                         value=coin.value + 1000)


def test_a_copy_cannot_invent_the_coin_it_proves_against(world):  # noqa: F811
    """A real offline computer signs a false value (Bitcoin will refuse it),
    and the file adds a made-up copy under the txid of the real coin. It
    proved the false signatures, the will became "signed" and was sent."""
    stick, _folder = _stick(world)
    online = world("watch")
    online.check()
    _edit_file(stick, _false_coin)
    signed = _sign(_no_history(world("keys")), stick, "signed.json")
    _merge(online, signed)
    assert "not valid" in _refused(online)
    with open(signed, encoding="utf-8") as f:
        raw = json.load(f)
    any_item = next(iter(raw.values()))
    spent = tx_from_any(any_item["tx"]).inputs()[0].prevout.txid.hex()
    own = online.wallet.get_receiving_addresses()[0]
    made_up = PartialTransaction.from_io(
        [PartialTxInput(prevout=TxOutpoint(txid=bytes(32), out_idx=0))],
        [PartialTxOutput.from_address_and_value(own, 2_000_000)] * 2, locktime=D1)
    raw[spent] = dict(any_item, tx=str(made_up), heirs={})
    with open(signed, "w", encoding="utf-8") as f:
        json.dump(raw, f)
    online.bw.popups.clear()
    _merge(online, signed)
    assert _refused(online) is not None
    assert not any(_complete(w) for w in online.bw.willitems.values())
    assert spent not in online.bw.willitems


def test_the_wallet_knows_the_coin_better_than_the_file(world):  # noqa: F811
    """A false value signed by the first signer is not "1 of 2" for a computer
    that knows the coin."""
    online, stick = _multisig_online(world)
    _edit_file(stick, _false_coin)
    after_a = _sign(_no_history(world("msA")), stick, "after-a.json")
    before = {wid: str(w.tx) for wid, w in online.bw.willitems.items()}
    online.activate()
    with pytest.raises(FileImportFailed):
        online.bw.merge_will_from_file(after_a)
    assert {wid: str(w.tx) for wid, w in online.bw.willitems.items()} == before


def test_real_offline_computers_are_not_stopped(world):  # noqa: F811
    """The owner's route with computers that know no coin at all."""
    online, stick = _multisig_online(world)
    after_a = _sign(_no_history(world("msA")), stick, "after-a.json")
    b = _no_history(world("msB"))
    assert engine.payment_problems(b.wallet, _items(b, after_a)) == []
    after_b = _sign(b, after_a, "after-b.json")
    assert _merge(online, after_b) == ALL_SIGNED


def test_a_good_file_is_not_called_false_where_it_cannot_be_checked(world):  # noqa: F811
    """A finished file on a computer that knows no coin: refused, and the
    message says why: nothing is wrong with the file."""
    stick, _folder = _stick(world)
    signed = _sign(world("keys"), stick, "signed.json")
    s = _no_history(world("keys"))
    _merge(s, signed)
    assert "cannot be checked" in _refused(s) and "not valid" not in _refused(s)


def test_will_details_cannot_send_a_false_file(world):  # noqa: F811
    """Import, Will Details, Broadcast will: it skipped every check."""
    stick, _folder = _stick(world)
    online = world("watch")
    online.check()
    signed = _sign(world("keys"), stick, "signed.json")
    _edit_file(signed, _break_final)
    items = _items(online, signed)
    _clear_pushes(online)
    online.act("send", lambda: online.bw.broadcast_transactions(will=items))
    assert any("not valid" in str(p) for p in online.bw.popups)
    assert _pushed_complete(online) == []


def test_transactions_with_no_names_can_be_signed_after_the_tick(world):  # noqa: F811
    """The QR and audio transfer holds bare transactions: #209 stopped every
    good will. The addresses are listed for the paper; the command line, with
    no such window, still refuses them."""
    stick, _folder = _stick(world)
    with open(stick, encoding="utf-8") as f:
        raw = json.load(f)
    s = world("keys")
    items = {wid: WillItem({"tx": item["tx"]}, _id=wid, wallet=s.wallet)
             for wid, item in raw.items()}
    for w in items.values():
        w.set_status("VALID", True)
    assert s.bw.confirm_signing(will=items) is True
    assert fk.HEIR_ADDR["alice"] in s.user.warned[-1]
    assert engine.payment_problems(s.wallet, items) != []
    assert engine.file_summary(s.wallet, items, unnamed=True).problems == []


def test_one_row_missing_is_not_a_copy_with_no_names(world):  # noqa: F811
    """Only a copy with no row at all is listed by address."""
    stick, _folder = _stick(world)
    _edit_file(stick, _redirect_heir)
    s = world("keys")
    items = _items(s, stick)
    summary = engine.file_summary(s.wallet, items, unnamed=True)
    assert engine.PaymentProblem(engine.UNKNOWN_ADDRESS, THIEF) in summary.problems


def test_a_strange_file_is_a_stop_not_a_crash(world):  # noqa: F811
    """Fields of the wrong type, a false coin read before the check."""
    stick, _folder = _stick(world)
    with open(stick, encoding="utf-8") as f:
        raw = json.load(f)
    for item in raw.values():
        item["willexecutor"] = "x"
    with open(stick, "w", encoding="utf-8") as f:
        json.dump(raw, f)
    s = world("keys")
    assert engine.payment_problems(s.wallet, _items(s, stick)) != []
    result, _outcome = engine.sign_file(s.wallet, _items(s, stick), PW)
    assert result.signed == []


def test_false_coin_on_the_command_line_is_told(world):  # noqa: F811
    """On a wallet that knows the coin, reading the file raises."""
    stick, _folder = _stick(world)
    _edit_file(stick, _false_coin)
    ctl = _cli(world("keys"))
    with pytest.raises(UserFacingException):
        BalController.merge_will_from_file(ctl, stick)
    assert ctl.willitems == {}


# ----------------------------------- the second pass of the independent review


def _marked_signed(path):
    """The file at *path* says "signed" for every copy, whatever is inside."""
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    for item in raw.values():
        item["COMPLETE"] = True
        item["VALID"] = True
    with open(path, "w", encoding="utf-8") as f:
        json.dump(raw, f)


@pytest.mark.parametrize("will", [True, False], ids=["will signed", "no will"])
def test_will_details_cannot_send_unsigned_copies_that_say_signed(world, will):  # noqa: F811
    """The file says "signed" with no signature inside: the choice of what to
    send reads that flag, and the merge alone had nothing to say about it."""
    stick, _folder = _stick(world)
    _marked_signed(stick)
    s = world("keys")
    if will:
        s.check()
    items = _items(s, stick)
    _clear_pushes(s)
    s.act("send", lambda: s.bw.broadcast_transactions(will=items))
    assert any("not valid" in str(p) for p in s.bw.popups)
    assert _pushed_complete(s) == []


def test_will_details_sends_the_file_not_the_signed_will(world):  # noqa: F811
    """The will is signed, so the merge leaves it as it is; but what leaves
    is the file's copy, with its false signature."""
    stick, _folder = _stick(world)
    signed = _sign(world("keys"), stick, "signed.json")
    _edit_file(signed, _break_final)
    s = world("keys")
    s.check()
    items = _items(s, signed)
    _clear_pushes(s)
    s.act("send", lambda: s.bw.broadcast_transactions(will=items))
    assert any("not valid" in str(p) for p in s.bw.popups)
    assert _pushed_complete(s) == []


def test_will_details_sends_a_good_signed_file(world):  # noqa: F811
    """The same route on a good file: it leaves."""
    stick, _folder = _stick(world)
    signed = _sign(world("keys"), stick, "signed.json")
    online = world("watch")
    online.check()
    items = _items(online, signed)
    assert engine.send_problems(online.wallet, online.bw.willitems, items) == []


def test_one_copy_rewritten_as_a_will_executor_stops(world):  # noqa: F811
    """One copy of three: the heir's money to the hacker, the heir's row gone,
    a row that calls the hacker a will-executor. The list looked the same."""
    stick, _folder = _stick(world)

    def rewrite(tx, item):
        """The theft, written in the rows of this copy."""
        out = _heir_output(tx, item)
        name = next(n for n, row in item["heirs"].items()
                    if row[0] == out.address)
        del item["heirs"][name]
        item["heirs"]['w!ll3x3c"https://we0.test"1'] = [THIEF, 1000, 1, 1000]
        _redirect(out)

    _edit_file(stick, rewrite, only=1)
    s = world("keys")
    items = _items(s, stick)
    kinds = {p.kind for p in engine.payment_problems(s.wallet, items)}
    assert kinds == {engine.EXECUTOR_PAID_MORE, engine.COPIES_DIFFER}
    result, _outcome = engine.sign_file(s.wallet, items, PW)
    assert result.signed == [] and THIEF in result.error


def test_good_wills_name_the_same_people_in_every_copy(world):  # noqa: F811
    """Three deliveries with a change, and the file with one will-executor
    fewer: no false stop from the new rules."""
    stick, _folder = _stick(world, deliveries=3)
    s = world("keys")
    assert engine.payment_problems(s.wallet, _items(s, stick)) == []
    with open(stick, encoding="utf-8") as f:
        raw = json.load(f)
    only = {wid: item for wid, item in raw.items()
            if (item.get("willexecutor") or {}).get("url") != "https://we2.test"}
    assert 0 < len(only) < len(raw)
    with open(stick, "w", encoding="utf-8") as f:
        json.dump(only, f)
    summary = engine.file_summary(s.wallet, _items(s, stick))
    assert not {engine.COPIES_DIFFER, engine.EXECUTOR_PAID_MORE} & {
        p.kind for p in summary.problems}


def test_one_copy_with_no_names_among_named_ones_stops(world):  # noqa: F811
    """Listing by address is for a list of bare transactions, not for one copy
    of a file with its names taken away."""
    stick, _folder = _stick(world)

    def strip(tx, item):
        """The theft, and no row left to compare with."""
        _redirect_heir(tx, item)
        item["heirs"] = {}
        item["willexecutor"] = None

    _edit_file(stick, strip, only=1)
    s = world("keys")
    summary = engine.file_summary(s.wallet, _items(s, stick), unnamed=True)
    assert engine.PaymentProblem(engine.UNKNOWN_ADDRESS, THIEF) in summary.problems
