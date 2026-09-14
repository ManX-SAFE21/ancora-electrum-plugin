"""
Tests for the "Import" (read-only will preview) flow.

Covers:
  - ``BalWindow._load_will_file`` round-trip (file -> WillItems).
  - ``import_will_into_details`` normalization + IMPORTED status.
  - ``BalWindow.sign_transactions`` operating ONLY on the passed (imported)
    will, never on the live wallet state.
  - ``WillWidget`` honouring an explicit ``will`` argument.
  - ``WillDetailDialog`` external-will mode (threshold + isolated buttons).

Run:
    source "$BAL_HOME/electrum/env/bin/activate"
    QT_QPA_PLATFORM=offscreen python3 tests/test_import_will_details.py
"""

import sys
import tempfile
from types import MethodType, SimpleNamespace

sys.path.insert(0, __file__.rsplit("/", 2)[0])

from PyQt6.QtWidgets import QApplication, QWidget

from bal.core.will import Will, WillItem
from bal.gui.qt import window as window_mod

_app = QApplication.instance() or QApplication(sys.argv)

# A valid serialized Bitcoin transaction hex (1 input + 1 P2PKH output, version 2)
_VALID_TX_HEX = (
    "01000000012a5c9a94fcde98f5581cd00162c60a13936ceb75389ea65b"
    "f38633b424eb4031000000006c493046022100a82bbc57a0136751e543"
    "3f41cf000b3f1a99c6744775e76ec764fb78c54ee100022100f9e80b7d"
    "e89de861dc6fb0c1429d5da72c2b6b2ee2406bc9bfb1beedd729d98501"
    "2102e61d176da16edd1d258a200ad9759ef63adf8e14cd97f53227bae3"
    "5cdb84d2f6ffffffff0140420f00000000001976a914230ac37834073a"
    "42146f11ef8414ae929feaafc388ac00000000"
)


def _make_willitem_dict(**overrides):
    """Return a minimal dict that can construct a WillItem."""
    d = {
        "tx": _VALID_TX_HEX,
        "heirs": {},
        "willexecutor": None,
        "status": "",
        "description": "",
        "time": 0,
        "change": "",
        "baltx_fees": 100,
    }
    d.update(overrides)
    return d


def _make_willitems(n, prefix="w"):
    """Create ``n`` WillItems with distinct txids/locktimes."""
    willitems = {}
    for i in range(n):
        wid = f"{prefix}{i}"
        wi = WillItem(_make_willitem_dict())
        wi.tx.locktime = 1000 + i
        wi._id = wid
        willitems[wid] = wi
    return willitems


# ------------------------------------------------------------------ #
# _load_will_file round-trip
# ------------------------------------------------------------------ #

def test_load_will_file_roundtrip():
    from bal.gui.qt.common import write_json_file

    src = _make_willitems(2)
    data = {wid: wi.to_dict() for wid, wi in src.items()}
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False
    ) as f:
        path = f.name
    write_json_file(path, data)
    try:
        loaded = window_mod.BalWindow._load_will_file(None, path)
        assert set(loaded) == {"w0", "w1"}
        for wid, wi in loaded.items():
            assert isinstance(wi, WillItem)
            assert wi.heirs == {}
            assert wi._id == wid
            assert wi.tx is not None
    finally:
        import os

        os.unlink(path)


# ------------------------------------------------------------------ #
# import_will_into_details normalization + IMPORTED status
# ------------------------------------------------------------------ #

def test_imported_will_is_normalized_and_marked_imported():
    willitems = _make_willitems(1)
    Will.normalize_will(willitems, None)
    for wi in willitems.values():
        wi.set_status("IMPORTED", True)
    assert all(wi.get_status("IMPORTED") for wi in willitems.values())
    assert all(wi.get_status("VALID") for wi in willitems.values())


# ------------------------------------------------------------------ #
# sign_transactions operates only on the passed (imported) will
# ------------------------------------------------------------------ #

def test_sign_transactions_external_only():
    class FakeWallet:
        def sign_transaction(self, tx, password, ignore_warnings=False):
            # No-op: never marks the tx complete.
            return None

    live = _make_willitems(1, prefix="live")
    wid_live = next(iter(live))
    imported = _make_willitems(2, prefix="imp")

    fake = SimpleNamespace(
        willitems=live,
        wallet=FakeWallet(),
        waiting_dialog=SimpleNamespace(update=lambda msg: None),
    )
    # sign_transactions dispatches to self._prepare_and_sign_tx; bind the real
    # implementation onto the fake so the external-sign run actually executes.
    fake._prepare_and_sign_tx = MethodType(
        window_mod.BalWindow._prepare_and_sign_tx, fake
    )

    result = window_mod.BalWindow.sign_transactions(fake, None, will=imported)

    assert result is not None
    assert set(result) == set(imported)
    assert wid_live not in result
    # The live will must be completely untouched by the external sign run.
    assert live[wid_live].get_status("COMPLETE") is False


# ------------------------------------------------------------------ #
# WillWidget honours an explicit ``will`` argument
# ------------------------------------------------------------------ #

def test_will_widget_explicit_will():
    from bal.gui.qt.widgets import WillWidget

    live = _make_willitems(1, prefix="live")
    imported = _make_willitems(2, prefix="imp")

    fake_parent = SimpleNamespace(
        decimal_point=8,
        base_unit_name="BTC",
        bal_window=SimpleNamespace(
            willitems=live,
            bal_plugin=SimpleNamespace(
                _hide_replaced=False, _hide_invalidated=False
            ),
            show_transaction=lambda *a, **k: None,
        ),
    )

    w = WillWidget(parent=fake_parent, will=imported)
    assert w.will is imported

    w2 = WillWidget(parent=fake_parent)
    assert w2.will is live


# ------------------------------------------------------------------ #
# WillWidget shows heir/willexecutor addresses and decodes OP_RETURN
# ------------------------------------------------------------------ #

def test_will_widget_shows_addresses_and_decodes_opreturn():
    from PyQt6.QtWidgets import QLabel

    from bal.core.will import WillItem
    from bal.gui.qt.widgets import WillWidget

    op_hex = "68656c6c6f"  # "hello" in hex
    heirs = {
        "bob": ["bc1qtestaddr", 1_000_000, 1000, 500_000],
        "msg": [f"OP_RETURN:{op_hex}", 0, 1000, 0],
    }
    wi = WillItem(
        _make_willitem_dict(
            heirs=heirs,
            willexecutor={
                "url": "https://exec.example",
                "address": "bc1qexecaddr",
                "base_fee": 200_000,
            },
        )
    )
    wi._id = "w0"
    fake_parent = SimpleNamespace(
        decimal_point=8,
        base_unit_name="BTC",
        bal_window=SimpleNamespace(
            willitems={},
            bal_plugin=SimpleNamespace(
                _hide_replaced=False, _hide_invalidated=False
            ),
            show_transaction=lambda *a, **k: None,
        ),
    )
    w = WillWidget(parent=fake_parent, will={"w0": wi})
    texts = [lbl.text() for lbl in w.findChildren(QLabel)]
    assert any("bc1qtestaddr" in t for t in texts), texts
    assert any("OP_RETURN: hello" in t for t in texts), texts
    assert any("bc1qexecaddr" in t for t in texts), texts


# ------------------------------------------------------------------ #
# WillDetailDialog external-will mode
# ------------------------------------------------------------------ #

def _make_fake_bal_window(window_widget):
    bal_plugin = SimpleNamespace(read_file=lambda path: b"")
    return SimpleNamespace(
        window=window_widget,
        bal_plugin=bal_plugin,
        wallet=SimpleNamespace(),
        show_transaction=lambda *a, **k: None,
        willitems=_make_willitems(1),
        will_settings={"real_threshold": 9999},
    )


def test_will_detail_dialog_external_threshold():
    from bal.gui.qt.dialogs import WillDetailDialog

    window_widget = QWidget()
    bal_window = _make_fake_bal_window(window_widget)
    bal_window.window.config = SimpleNamespace()
    bal_window.window.format_amount = lambda *a, **k: "1.0"
    bal_window.window.base_unit = "BTC"
    bal_window.window.format_fiat_and_units = lambda *a, **k: "1.0"
    bal_window.window.fx = None
    bal_window.window.format_fee_rate = lambda *a, **k: "1.0"
    bal_window.window.get_decimal_point = lambda: 8

    imported = _make_willitems(2)
    # locktimes 1000 and 1001 -> threshold must be the max (1001).
    dialog = WillDetailDialog(bal_window, will=imported)

    assert dialog._external_will is True
    assert dialog.will is imported
    assert dialog.threshold == 1001

    dialog2 = WillDetailDialog(bal_window)
    assert dialog2._external_will is False
    assert dialog2.will is bal_window.willitems
    assert dialog2.threshold == 9999


def test_will_detail_dialog_buttons_pass_will():
    from bal.gui.qt.dialogs import WillDetailDialog

    window_widget = QWidget()
    bal_window = _make_fake_bal_window(window_widget)
    bal_window.window.config = SimpleNamespace()
    bal_window.window.format_amount = lambda *a, **k: "1.0"
    bal_window.window.base_unit = "BTC"
    bal_window.window.format_fiat_and_units = lambda *a, **k: "1.0"
    bal_window.window.fx = None
    bal_window.window.format_fee_rate = lambda *a, **k: "1.0"
    bal_window.window.get_decimal_point = lambda: 8

    imported = _make_willitems(1)
    dialog = WillDetailDialog(bal_window, will=imported)

    calls = []

    bal_window.ask_password_and_sign_transactions = lambda **k: calls.append(
        ("sign", k.get("will"))
    )
    bal_window.broadcast_transactions = lambda **k: calls.append(
        ("broadcast", k.get("will"))
    )
    bal_window.export_will = lambda **k: calls.append(("export", k.get("will")))
    bal_window.invalidate_will = lambda **k: calls.append(
        ("invalidate", k.get("will"))
    )

    dialog.ask_password_and_sign_transactions()
    dialog.broadcast_transactions()
    dialog.export_will()
    dialog.invalidate_will()

    assert len(calls) == 4
    for action, will in calls:
        assert will is imported, f"{action} did not receive the imported will"


# ------------------------------------------------------------------ #
# Merge flow (BalWindow.merge_will)
# ------------------------------------------------------------------ #

def _make_partial_tx(locktime=1000, signed=False):
    """A PartialTransaction derived from _VALID_TX_HEX.

    When ``signed`` the input scriptSig of the raw tx is copied over, which
    finalizes the (legacy P2PKH) input and makes ``is_complete()`` True.
    """
    from electrum.transaction import PartialTransaction, Transaction

    ptx = PartialTransaction.from_tx(Transaction(_VALID_TX_HEX))
    ptx.locktime = locktime
    if signed:
        raw = Transaction(_VALID_TX_HEX)
        ptx.inputs()[0].script_sig = raw.inputs()[0].script_sig
    return ptx


def _make_willitem_with_tx(tx, key=None):
    wi = WillItem(_make_willitem_dict())
    wi.tx = tx
    wi._id = key if key is not None else tx.txid()
    return wi


def _make_merge_fake(willitems):
    """A BalWindow-like object with a wallet stub sufficient for the local
    validity check that ``merge_will`` runs after merging.
    """
    class FakeWallet:
        def add_input_info(self, txin, **kwargs):
            pass

        def add_output_info(self, txout, **kwargs):
            pass

        def get_utxos(self):
            return []

        def get_tx_info(self, tx):
            return SimpleNamespace(
                tx_mined_status=SimpleNamespace(height=lambda: 0)
            )

        @property
        def db(self):
            return SimpleNamespace(get_transaction=lambda txid: None)

    calls = []
    fake = SimpleNamespace(
        willitems=willitems,
        will={},
        wallet=FakeWallet(),
        bal_window=None,
        date_to_check=1700000000,
        will_settings={"threshold": 1700000000},
        bal_plugin=SimpleNamespace(
            HISTORY_LABEL=SimpleNamespace(
                get=lambda: "BAL will history ({willexecutor})"
            ),
            # BASIC is the default user type; merge_will resolves date_to_check
            # through bal.core.checkalive when it is missing.
            is_basic_mode=lambda: True,
        ),
        update_all=lambda: calls.append("update_all"),
    )
    fake.save_willitems = MethodType(window_mod.BalWindow.save_willitems, fake)
    return fake, calls


def test_merge_will_same_id_unsigned_live_substitutes_signed_imported():
    # The realistic "signed on another machine, imported here" case: the live
    # will holds an unsigned PSBT (txid() -> None), the imported one is signed
    # (complete). The transaction must be substituted and COMPLETE set.
    wid = "same_id"
    live_tx = _make_partial_tx(locktime=1000, signed=False)
    imported_tx = _make_partial_tx(locktime=1000, signed=True)

    live = {wid: _make_willitem_with_tx(live_tx, key=wid)}
    imported = {wid: _make_willitem_with_tx(imported_tx, key=wid)}
    imported[wid].set_status("COMPLETE", True)
    imported[wid].set_status("PUSHED", True)
    imported[wid].set_status("CHECKED", True)

    fake, calls = _make_merge_fake(live)
    live_item = live[wid]

    window_mod.BalWindow.merge_will(fake, imported)

    # The live WillItem object is kept (never replaced) and the signed tx
    # substituted in.
    assert live[wid] is live_item
    assert live_item.tx is imported_tx
    assert live_item.tx.is_complete()
    assert live_item.get_status("COMPLETE") is True
    # Operational statuses were carried over.
    assert live_item.get_status("PUSHED") is True
    assert live_item.get_status("CHECKED") is True
    # The will was saved and the GUI refreshed.
    assert wid in fake.will
    assert fake.will[wid]["COMPLETE"] is True
    assert "update_all" in calls


def test_merge_will_same_id_both_signed_combines():
    # Live tx is signed but the COMPLETE flag was not set yet; the imported
    # signed tx with the same txid must be COMBINED into the live one (the live
    # tx object is kept) rather than substituted.
    from electrum.transaction import Transaction

    wid = "same_id_combine"
    imported_tx = _make_partial_tx(locktime=1000, signed=True)
    txid = imported_tx.txid()
    assert txid is not None

    live_tx = _make_partial_tx(locktime=1000, signed=True)
    live = {wid: _make_willitem_with_tx(live_tx, key=wid)}
    imported = {wid: _make_willitem_with_tx(imported_tx, key=wid)}
    imported[wid].set_status("COMPLETE", True)

    fake, _ = _make_merge_fake(live)
    live_item = live[wid]

    window_mod.BalWindow.merge_will(fake, imported)

    # Same txid -> combine_with_other_psbt: live tx object is preserved.
    assert live_item.tx is live_tx
    assert live_item.tx.is_complete()
    assert live_item.get_status("COMPLETE") is True
    raw_sig = Transaction(_VALID_TX_HEX).inputs()[0].script_sig
    assert live_item.tx.inputs()[0].script_sig == raw_sig


def test_merge_will_same_id_already_complete_never_touches_live_tx():
    wid = "already_complete"
    live_tx = _make_partial_tx(locktime=1000, signed=False)
    live = {wid: _make_willitem_with_tx(live_tx, key=wid)}
    live[wid].set_status("COMPLETE", True)

    imported_tx = _make_partial_tx(locktime=1000, signed=True)
    imported = {wid: _make_willitem_with_tx(imported_tx, key=wid)}
    imported[wid].set_status("COMPLETE", True)
    imported[wid].set_status("PUSHED", True)

    fake, _ = _make_merge_fake(live)
    live_item = live[wid]

    window_mod.BalWindow.merge_will(fake, imported)

    # An already-signed live will is left untouched: no combine, no substitute.
    assert live_item.tx is live_tx
    assert live_item.tx.is_complete() is False
    assert live_item.tx.inputs()[0].script_sig is None
    assert live_item.get_status("COMPLETE") is True
    assert live_item.get_status("PUSHED") is True


def test_merge_will_statuses_are_monotonic():
    # Statuses that are True in the live item must never be cleared by a False
    # value coming from the imported item.
    wid = "monotonic"
    live_tx = _make_partial_tx(locktime=1000, signed=True)
    live = {wid: _make_willitem_with_tx(live_tx, key=wid)}
    live[wid].set_status("CONFIRMED", True)

    imported_tx = _make_partial_tx(locktime=1000, signed=True)
    imported = {wid: _make_willitem_with_tx(imported_tx, key=wid)}
    imported[wid].set_status("MEMPOOL", True)

    fake, _ = _make_merge_fake(live)
    live_item = live[wid]

    window_mod.BalWindow.merge_will(fake, imported)

    assert live_item.get_status("CONFIRMED") is True
    assert live_item.get_status("MEMPOOL") is True
    assert live_item.get_status("COMPLETE") is True


def test_merge_will_new_ids_added_wholesale():
    live_tx = _make_partial_tx(locktime=1000, signed=True)
    live = {live_tx.txid(): _make_willitem_with_tx(live_tx)}

    imported_tx = _make_partial_tx(locktime=2000, signed=True)
    new_id = imported_tx.txid()
    imported = {new_id: _make_willitem_with_tx(imported_tx)}
    imported[new_id].set_status("PUSHED", True)

    fake, _ = _make_merge_fake(live)

    window_mod.BalWindow.merge_will(fake, imported)

    assert len(live) == 2
    assert live[new_id] is imported[new_id]
    assert live[new_id].get_status("PUSHED") is True
    assert new_id in fake.will


def test_merge_will_missing_date_to_check_defaults_to_now():
    # Regression: merge_will read self.date_to_check (which is only set by
    # init_class_variables) and crashed with AttributeError when merging a
    # will file was the first action of a session.
    wid = "missing_dtc"
    imported_tx = _make_partial_tx(locktime=1000, signed=True)
    imported = {wid: _make_willitem_with_tx(imported_tx, key=wid)}
    fake, calls = _make_merge_fake({})
    del fake.date_to_check

    window_mod.BalWindow.merge_will(fake, imported)

    assert isinstance(fake.date_to_check, (int, float)), \
        "date_to_check must fall back to a timestamp"
    assert wid in fake.will
    assert "update_all" in calls


def test_merge_will_validity_error_logs_without_crashing():
    # Regression: the except handler passed log_error(e, self.bal_window);
    # BalWindow has no such attribute, so a validity failure raised a second
    # AttributeError that masked the original error.
    from unittest.mock import patch

    wid = "validity_err"
    imported_tx = _make_partial_tx(locktime=1000, signed=True)
    imported = {wid: _make_willitem_with_tx(imported_tx, key=wid)}
    fake, calls = _make_merge_fake({})
    fake.show_error = lambda msg: calls.append(("show_error", msg))

    with patch.object(
        window_mod.Util, "get_available_utxos", side_effect=RuntimeError("boom")
    ):
        window_mod.BalWindow.merge_will(fake, imported)

    assert any(c[0] == "show_error" for c in calls), \
        "the validity error must be surfaced via show_error"
    assert "update_all" in calls, "merge must complete after the error"


def test_merge_will_from_file_invalid_file_raises():
    from bal.gui.qt.common import FileImportFailed

    def bad_load(path):
        raise ValueError("bad file")

    fake = SimpleNamespace(_load_will_file=bad_load, wallet=None)

    try:
        window_mod.BalWindow.merge_will_from_file(fake, "/nonexistent.json")
    except FileImportFailed as e:
        assert "bad file" in str(e)
    else:
        raise AssertionError("expected FileImportFailed")


def test_merge_will_partial_signatures_update_counts():
    # A live unsigned 2-of-3 multisig will merged with an imported copy that
    # carries 1 signature must end up PARTIALLY_SIGNED with the sig counts
    # refreshed on the live item (check_signatures runs after the merge).
    from binascii import unhexlify

    from electrum import crypto
    from electrum.descriptor import parse_descriptor
    from electrum.transaction import (
        PartialTransaction,
        PartialTxInput,
        PartialTxOutput,
        Sighash,
        TxOutpoint,
    )

    def make_multisig_tx(nsigs):
        pubs = []
        for seed in (1, 2, 3):
            pubs.append(crypto.privkey_to_pubkey(bytes([seed] * 32)).hex())
        desc = parse_descriptor("wsh(multi(2,{}))".format(",".join(pubs)))
        txin = PartialTxInput(prevout=TxOutpoint(b"\x11" * 32, 0), script_sig=b"")
        txin.script_descriptor = desc
        txin._trusted_value_sats = 100000
        txin.sighash = Sighash.ALL
        sig = b"\x30\x44\x02\x20" + b"\x01" * 32 + b"\x02\x20" + b"\x02" * 32
        for i in range(nsigs):
            txin.sigs_ecdsa[unhexlify(pubs[i])] = sig
        from electrum.bitcoin import public_key_to_p2wpkh

        addr = public_key_to_p2wpkh(bytes.fromhex(pubs[0]))
        ptx = PartialTransaction()
        ptx.add_inputs([txin])
        ptx.add_outputs([PartialTxOutput.from_address_and_value(addr, 50000)])
        ptx.locktime = 1000
        return ptx, desc

    wid = "multisig_will"
    live_tx, _ = make_multisig_tx(0)
    imported_tx, _ = make_multisig_tx(1)

    live = {wid: _make_willitem_with_tx(live_tx, key=wid)}
    imported = {wid: _make_willitem_with_tx(imported_tx, key=wid)}

    fake, _ = _make_merge_fake(live)
    live_item = live[wid]

    window_mod.BalWindow.merge_will(fake, imported)

    assert live_item.get_status("PARTIALLY_SIGNED") is True
    assert live_item.sigs_have == 1
    assert live_item.sigs_required == 2


def test_will_detail_dialog_merge_switches_to_live():
    from bal.gui.qt.dialogs import WillDetailDialog

    window_widget = QWidget()
    bal_window = _make_fake_bal_window(window_widget)
    bal_window.window.config = SimpleNamespace()
    bal_window.window.format_amount = lambda *a, **k: "1.0"
    bal_window.window.base_unit = "BTC"
    bal_window.window.format_fiat_and_units = lambda *a, **k: "1.0"
    bal_window.window.fx = None
    bal_window.window.format_fee_rate = lambda *a, **k: "1.0"
    bal_window.window.get_decimal_point = lambda: 8

    live = bal_window.willitems
    imported = _make_willitems(2)
    merged = []
    bal_window.merge_will = lambda will: merged.append(will)

    dialog = WillDetailDialog(bal_window, will=imported)

    assert dialog._external_will is True
    assert dialog.merge_button is not None
    assert dialog.merge_button.isHidden() is False

    dialog.merge_will()

    assert merged == [imported]
    assert dialog._external_will is False
    assert dialog.will is live
    assert dialog.threshold == bal_window.will_settings["real_threshold"]
    assert dialog.merge_button.isHidden() is True


# ------------------------------------------------------------------ #
# Main
# ------------------------------------------------------------------ #

if __name__ == "__main__":
    for name in sorted(dir()):
        if name.startswith("test_"):
            globals()[name]()
            print(f"  [OK] {name}")
    print("[OK] All import-will-details tests passed")
