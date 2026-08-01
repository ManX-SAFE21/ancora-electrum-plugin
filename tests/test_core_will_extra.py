"""
Tests for wallet-dependent methods in ``bal.core.will``.

Uses mocking to simulate Electrum wallet, network, and db.

Run:
    source electrum/env/bin/activate
    QT_QPA_PLATFORM=offscreen python3 tests/test_core_will_extra.py
"""

import os
import sys
from binascii import unhexlify
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from electrum import crypto
from electrum.address_synchronizer import TX_HEIGHT_FUTURE, TX_HEIGHT_LOCAL
from electrum.bitcoin import public_key_to_p2wpkh
from electrum.descriptor import parse_descriptor
from electrum.transaction import (
    PartialTransaction,
    PartialTxInput,
    PartialTxOutput,
    Sighash,
    Transaction,
    TxOutpoint,
)

from bal.core.util import Util
from bal.core.will import Will, WillItem

_VALID_TX_HEX = (
    "01000000012a5c9a94fcde98f5581cd00162c60a13936ceb75389ea65b"
    "f38633b424eb4031000000006c493046022100a82bbc57a0136751e543"
    "3f41cf000b3f1a99c6744775e76ec764fb78c54ee100022100f9e80b7d"
    "e89de861dc6fb0c1429d5da72c2b6b2ee2406bc9bfb1beedd729d98501"
    "2102e61d176da16edd1d258a200ad9759ef63adf8e14cd97f53227bae3"
    "5cdb84d2f6ffffffff0140420f00000000001976a914230ac37834073a"
    "42146f11ef8414ae929feaafc388ac00000000"
)


# Patch Transaction.add_info_from_wallet so it's a no-op during all tests
_patcher = patch.object(Transaction, "add_info_from_wallet")
_patcher.start()


# ------------------------------------------------------------------ #
# Will.check_tx_height
# ------------------------------------------------------------------ #

def test_check_tx_height():
    wallet = MagicMock()
    wallet.get_tx_info.return_value.tx_mined_status.height.return_value = 100
    tx = MagicMock()
    assert Will.check_tx_height(tx, wallet) == 100


def test_check_tx_height_zero():
    wallet = MagicMock()
    wallet.get_tx_info.return_value.tx_mined_status.height.return_value = 0
    tx = MagicMock()
    assert Will.check_tx_height(tx, wallet) == 0





# ------------------------------------------------------------------ #
# Will.add_info_from_will
# ------------------------------------------------------------------ #

def test_add_info_from_will():
    wallet = MagicMock()
    willitem = MagicMock()
    will = {"wid": willitem}
    Will.add_info_from_will(will, "wid", wallet)
    willitem.tx.add_info_from_wallet.assert_called_once_with(wallet)


def test_add_info_from_will_no_wallet():
    willitem = MagicMock()
    will = {"wid": willitem}
    Will.add_info_from_will(will, "wid", None)


def test_add_info_from_will_tx_is_str():
    wallet = MagicMock()
    willitem = MagicMock()
    willitem.tx = _VALID_TX_HEX
    will = {"wid": willitem}
    Will.add_info_from_will(will, "wid", wallet)
    assert hasattr(willitem.tx, "add_info_from_wallet")


# ------------------------------------------------------------------ #
# Will.check_invalidated
# ------------------------------------------------------------------ #

def test_check_invalidated_confirmed():
    wallet = MagicMock()
    wallet.get_tx_info.return_value.tx_mined_status.height.return_value = 100
    item = WillItem({"tx": _VALID_TX_HEX, "heirs": {"a": ["addr", 100, "30d"]},
                     "willexecutor": None, "status": "", "description": "",
                     "time": 0, "change": "", "baltx_fees": 100})
    will = {"wid": item}
    Will.check_invalidated(will, [], wallet)
    assert item.get_status("CONFIRMED") is True


def test_check_invalidated_mempool():
    # PENDING was renamed to MEMPOOL (A2): a tx seen with height 0 (in the
    # mempool, not yet mined) is flagged as MEMPOOL.
    wallet = MagicMock()
    wallet.get_tx_info.return_value.tx_mined_status.height.return_value = 0
    item = WillItem({"tx": _VALID_TX_HEX, "heirs": {"a": ["addr", 100, "30d"]},
                     "willexecutor": None, "status": "", "description": "",
                     "time": 0, "change": "", "baltx_fees": 100})
    will = {"wid": item}
    Will.check_invalidated(will, [], wallet)
    assert item.get_status("MEMPOOL") is True


def test_legacy_pending_migrates_to_mempool():
    # Backward-compatibility (A2, "Modo B"): a will saved by an older plugin
    # version stores the flag under the legacy "PENDING" key. Loading it must
    # carry that flag over to the new "MEMPOOL" status, so nothing is lost.
    item = WillItem({"tx": _VALID_TX_HEX, "heirs": {"a": ["addr", 100, "30d"]},
                     "willexecutor": None, "status": "", "description": "",
                     "time": 0, "change": "", "baltx_fees": 100,
                     "PENDING": True})
    assert item.get_status("MEMPOOL") is True


def test_new_mempool_wins_over_legacy_pending():
    # If both the new "MEMPOOL" key and the legacy "PENDING" key are present,
    # the new key wins: an explicit MEMPOOL=False is NOT overridden by a stale
    # legacy PENDING=True.
    item = WillItem({"tx": _VALID_TX_HEX, "heirs": {"a": ["addr", 100, "30d"]},
                     "willexecutor": None, "status": "", "description": "",
                     "time": 0, "change": "", "baltx_fees": 100,
                     "MEMPOOL": False, "PENDING": True})
    assert item.get_status("MEMPOOL") is False


def test_updated_status_keeps_valid():
    # A2 rule: setting UPDATED must NOT clear the VALID flag (the tx is replaced
    # by a new one that keeps the same locktime and same heirs).
    item = WillItem({"tx": _VALID_TX_HEX, "heirs": {"a": ["addr", 100, "30d"]},
                     "willexecutor": None, "status": "", "description": "",
                     "time": 0, "change": "", "baltx_fees": 100,
                     "VALID": True})
    item.set_status("UPDATED", True)
    assert item.get_status("UPDATED") is True
    assert item.get_status("VALID") is True


def test_anticipated_status_keeps_valid():
    # A2 rule: setting ANTICIPATED must NOT clear the VALID flag (anticipating
    # only moves the locktime 1 day earlier; the tx stays valid).
    item = WillItem({"tx": _VALID_TX_HEX, "heirs": {"a": ["addr", 100, "30d"]},
                     "willexecutor": None, "status": "", "description": "",
                     "time": 0, "change": "", "baltx_fees": 100,
                     "VALID": True})
    item.set_status("ANTICIPATED", True)
    assert item.get_status("ANTICIPATED") is True
    assert item.get_status("VALID") is True


def test_mempool_status_clears_valid():
    # A2 rule (unchanged from old PENDING behaviour): setting MEMPOOL clears the
    # VALID flag.
    item = WillItem({"tx": _VALID_TX_HEX, "heirs": {"a": ["addr", 100, "30d"]},
                     "willexecutor": None, "status": "", "description": "",
                     "time": 0, "change": "", "baltx_fees": 100,
                     "VALID": True})
    item.set_status("MEMPOOL", True)
    assert item.get_status("MEMPOOL") is True
    assert item.get_status("VALID") is False


def test_check_invalidated_invalidated():
    wallet = MagicMock()
    wallet.get_tx_info.return_value.tx_mined_status.height.return_value = -1
    # The funding is really consumed by a broadcast tx -> the will is dead.
    wallet.adb.get_spender.return_value = "ab" * 32
    item = WillItem({"tx": _VALID_TX_HEX, "heirs": {"a": ["addr", 100, "30d"]},
                     "willexecutor": None, "status": "", "description": "",
                     "time": 0, "change": "", "baltx_fees": 100})
    will = {"wid": item}
    Will.check_invalidated(will, [], wallet)
    assert item.get_status("INVALIDATED") is True


# ------------------------------------------------------------------ #
# Will.check_will (exercises check_invalidated + search_rai)
# ------------------------------------------------------------------ #

def test_check_will():
    wallet = MagicMock()
    wallet.get_tx_info.return_value.tx_mined_status.height.return_value = 0
    # No broadcast tx spends the funding: the missing UTXO is only a local
    # (history) artifact, so the will is not invalidated by it.
    wallet.adb.get_spender.return_value = None
    item = WillItem({"tx": _VALID_TX_HEX, "heirs": {"a": ["addr", 100, "30d"]},
                     "willexecutor": None, "status": "", "description": "",
                     "time": 0, "change": "", "baltx_fees": 100})
    will = {"wid": item}
    # check_will signature is now (will, all_utxos, wallet, timestamp_to_check):
    # block_to_check was removed in A1 (locktimes are always timestamps).
    Will.check_will(will, [], wallet, 9999999999)
    # should be MEMPOOL (height=0); PENDING was renamed to MEMPOOL in A2.
    assert item.get_status("MEMPOOL") is True


# ------------------------------------------------------------------ #
# WillItem signature counts + PARTIALLY_SIGNED status
# ------------------------------------------------------------------ #

def _multisig_descriptor():
    """Return a 2-of-3 wsh multisig descriptor and its three pubkeys."""
    pubs = []
    for seed in (1, 2, 3):
        pubs.append(crypto.privkey_to_pubkey(bytes([seed] * 32)).hex())
    return parse_descriptor("wsh(multi(2,{}))".format(",".join(pubs))), pubs


def _make_multisig_ptx(nsigs, locktime=None):
    """A 2-of-3 wsh multisig PartialTransaction carrying ``nsigs`` signatures."""
    desc, pubs = _multisig_descriptor()
    txin = PartialTxInput(prevout=TxOutpoint(b"\x11" * 32, 0), script_sig=b"")
    txin.script_descriptor = desc
    txin._trusted_value_sats = 100000
    txin.sighash = Sighash.ALL
    sig = b"\x30\x44\x02\x20" + b"\x01" * 32 + b"\x02\x20" + b"\x02" * 32
    for i in range(nsigs):
        txin.sigs_ecdsa[unhexlify(pubs[i])] = sig
    addr = public_key_to_p2wpkh(bytes.fromhex(pubs[0]))
    txout = PartialTxOutput.from_address_and_value(addr, 50000)
    ptx = PartialTransaction()
    if locktime is not None:
        ptx.locktime = locktime
    ptx.add_inputs([txin])
    ptx.add_outputs([txout])
    return ptx


def _make_multisig_willitem(nsig):
    """A WillItem wrapping an unsigned 2-of-3 partial tx with ``nsig`` sigs.

    The script descriptor is re-attached after the WillItem round-trips the tx
    through serialization (PSBT serialization drops the descriptor but keeps
    the signatures), mirroring the real load-from-wallet flow.
    """
    w = {"tx": _make_multisig_ptx(nsig), "heirs": {"a": ["addr", 100, "30d"]},
         "willexecutor": None, "status": "", "description": "",
         "time": 0, "change": "", "baltx_fees": 100}
    item = WillItem(w, _id="mswill")
    item.tx.inputs()[0].script_descriptor = _multisig_descriptor()[0]
    return item


def test_willitem_sigs_fields_roundtrip():
    item = _make_multisig_willitem(1)
    assert item.sigs_required == 0
    assert item.sigs_have == 0
    item.sigs_required = 2
    item.sigs_have = 1
    item.set_status("PARTIALLY_SIGNED", True)
    d = item.to_dict()
    assert d["sigs_required"] == 2
    assert d["sigs_have"] == 1
    assert d["PARTIALLY_SIGNED"] is True
    item2 = WillItem(d, _id="mswill")
    assert item2.sigs_required == 2
    assert item2.sigs_have == 1
    assert item2.get_status("PARTIALLY_SIGNED") is True


def test_willitem_legacy_dict_defaults_sig_fields():
    # A will saved before the signature-tracking feature has no sig fields:
    # they must default to 0 and the flag to False.
    item = WillItem({"tx": _VALID_TX_HEX, "heirs": {"a": ["addr", 100, "30d"]},
                     "willexecutor": None, "status": "", "description": "",
                     "time": 0, "change": "", "baltx_fees": 100}, _id="legacy")
    assert item.sigs_required == 0
    assert item.sigs_have == 0
    assert item.get_status("PARTIALLY_SIGNED") is False


def test_willitem_partial_signed_keeps_valid():
    item = _make_multisig_willitem(1)
    item.set_status("PARTIALLY_SIGNED", True)
    assert item.get_status("PARTIALLY_SIGNED") is True
    assert item.get_status("VALID") is True
    assert "Partially Signed" in item.status


def test_willitem_complete_clears_partially_signed():
    item = _make_multisig_willitem(1)
    item.set_status("PARTIALLY_SIGNED", True)
    item.set_status("COMPLETE", True)
    assert item.get_status("COMPLETE") is True
    assert item.get_status("PARTIALLY_SIGNED") is False


def test_check_signatures_partial():
    item = _make_multisig_willitem(1)
    Will.check_signatures({"mswill": item})
    assert item.sigs_have == 1
    assert item.sigs_required == 2
    assert item.get_status("PARTIALLY_SIGNED") is True
    assert item.get_status("VALID") is True


def test_check_signatures_unsigned_not_partial():
    item = _make_multisig_willitem(0)
    Will.check_signatures({"mswill": item})
    assert item.sigs_have == 0
    assert item.sigs_required == 2
    assert item.get_status("PARTIALLY_SIGNED") is False


def test_check_signatures_fully_signed_clears_flag():
    item = _make_multisig_willitem(2)
    item.set_status("PARTIALLY_SIGNED", True)
    Will.check_signatures({"mswill": item})
    assert item.get_status("PARTIALLY_SIGNED") is False


def test_check_signatures_complete_item_clears_flag():
    item = _make_multisig_willitem(1)
    item.set_status("PARTIALLY_SIGNED", True)
    item.set_status("COMPLETE", True)
    Will.check_signatures({"mswill": item})
    assert item.get_status("PARTIALLY_SIGNED") is False


def test_check_signatures_single_sig_required():
    # A single-signature (P2WPKH) will needs exactly 1 signature: 0 present is
    # "New", not "partially signed".
    pub = crypto.privkey_to_pubkey(bytes([7] * 32)).hex()
    item = _make_multisig_willitem(0)
    item.tx.inputs()[0].script_descriptor = parse_descriptor("wpkh({})".format(pub))
    Will.check_signatures({"mswill": item})
    assert item.sigs_required == 1
    assert item.sigs_have == 0
    assert item.get_status("PARTIALLY_SIGNED") is False


# ------------------------------------------------------------------ #
# WillItem.__init__ with wallet
# ------------------------------------------------------------------ #

def test_willitem_init_with_wallet():
    wallet = MagicMock()
    w = {"tx": _VALID_TX_HEX, "heirs": {"a": ["addr", 100, "30d"]},
         "willexecutor": None, "status": "", "description": "",
         "time": 0, "change": "", "baltx_fees": 100}
    item = WillItem(w, wallet=wallet)
    assert item is not None


def test_willitem_init_without_wallet():
    w = {"tx": _VALID_TX_HEX, "heirs": {"a": ["addr", 100, "30d"]},
         "willexecutor": None, "status": "", "description": "",
         "time": 0, "change": "", "baltx_fees": 100}
    item = WillItem(w)
    assert item is not None


# ------------------------------------------------------------------ #
# Will.save_valid_transactions_to_history (history persistence)
# ------------------------------------------------------------------ #

class FakeTxMinedStatus:
    def __init__(self, height):
        self._height = height

    def height(self):
        return self._height


class FakeTxInfo:
    def __init__(self, height):
        self.tx_mined_status = FakeTxMinedStatus(height)


class FakeWallet:
    """Minimal stand-in for an Electrum wallet used by history persistence."""

    def __init__(self, stored_txs=None, spenders=None, heights=None, outputs=None,
                 addresses=None):
        self.adb = FakeADB(
            stored_txs or {},
            spenders=spenders,
            heights=heights,
            outputs=outputs,
        )
        self.db = self.adb.db
        self.labels = {}
        self.save_db_called = 0
        self.addresses = list(addresses or [])

    def set_label(self, txid, label):
        if label is None:
            self.labels.pop(txid, None)
        else:
            self.labels[txid] = label

    def get_all_labels(self):
        return dict(self.labels)

    def save_db(self):
        self.save_db_called += 1

    def get_addresses(self):
        return self.addresses

    def get_label_for_txid(self, txid):
        return self.labels.get(txid, "")

    def get_tx_info(self, tx):
        height = self.adb.heights.get(tx.txid(), TX_HEIGHT_LOCAL)
        return FakeTxInfo(height)

    def get_utxos(self):
        utxos = []
        for outs in self.adb.outputs.values():
            for utxo in outs.values():
                if utxo.spent_height is None:
                    utxos.append(utxo)
        return utxos


class FakeADB:
    def __init__(self, stored_txs, spenders=None, heights=None, outputs=None):
        self.db = FakeDB(stored_txs)
        self.added = []
        self.removed = []
        self.spenders = dict(spenders or {})
        self.heights = dict(heights or {})
        self.outputs = dict(outputs or {})

    def add_transaction(self, tx, *, allow_unrelated=False, is_new=True):
        self.added.append((tx, allow_unrelated))
        return True

    def remove_transaction(self, txid):
        self.removed.append(txid)

    def get_spender(self, outpoint):
        txid = self.spenders.get(outpoint)
        if txid is None:
            return None
        height = self.heights.get(txid, TX_HEIGHT_LOCAL)
        if height in (TX_HEIGHT_LOCAL, TX_HEIGHT_FUTURE):
            return None
        return txid

    def get_tx_height(self, txid):
        return FakeTxMinedStatus(self.heights.get(txid, TX_HEIGHT_LOCAL))

    def get_addr_outputs(self, addr):
        return self.outputs.get(addr, {})


class FakeDB:
    def __init__(self, stored_txs):
        self.stored = dict(stored_txs)

    def get_transaction(self, txid):
        return self.stored.get(txid)


def _make_simple_willitem(tx, valid=True, we_url=None):
    """A WillItem wrapping *tx* with an optional VALID status and executor."""
    w = {"tx": tx, "heirs": {"a": ["addr", 100, "30d"]},
         "willexecutor": {"url": we_url} if we_url else None, "status": "",
         "description": "", "time": 0, "change": "", "baltx_fees": 100,
         "VALID": valid}
    return WillItem(w, _id="wid")


def _exec_label(template, url):
    return template.replace("{willexecutor}", url)


def test_save_incomplete_valid_tx_to_history_adds_and_labels():
    # A still-unsigned / partially-signed ("New") partial tx is stored in the
    # local history, tagged with the decoded label.
    tx = _make_multisig_ptx(1)
    item = _make_simple_willitem(tx, valid=True, we_url="https://we.example")
    wallet = FakeWallet()
    Will.save_valid_transactions_to_history(
        {"wid": item}, wallet, "BitcoinAfterLife inheritance transaction - {willexecutor}"
    )
    assert [t.txid() for t, _ in wallet.adb.added] == [tx.txid()]
    txid = tx.txid()
    assert wallet.labels[txid] == "BitcoinAfterLife inheritance transaction - https://we.example"
    assert wallet.save_db_called == 1


def test_save_skips_complete_and_invalid_items():
    # A fully-signed ("Complete") tx must NOT be stored: it is removed from the
    # local history instead. Invalid items are never touched.
    complete = _make_multisig_willitem(2)
    complete.set_status("VALID", True)
    invalid = _make_simple_willitem(Transaction(_VALID_TX_HEX), valid=False)
    new = _make_simple_willitem(_make_multisig_ptx(1), valid=True)
    wallet = FakeWallet()
    Will.save_valid_transactions_to_history(
        {"complete": complete, "invalid": invalid, "new": new},
        wallet,
        "BitcoinAfterLife inheritance transaction - {willexecutor}",
    )
    assert [t.txid() for t, _ in wallet.adb.added] == [new.tx.txid()]
    assert len(wallet.labels) == 1
    assert wallet.labels[new.tx.txid()] == (
        "BitcoinAfterLife inheritance transaction - "
    )


def test_save_combines_sigs_when_stored_partial():
    # The tx to save is already present in the wallet as an incomplete partial
    # PSBT: the signatures are combined into it instead of blindly overwriting.
    # (Exercised on raw PartialTransactions; through WillItem a complete tx
    # round-trips to a plain Transaction, which overwrites instead - see
    # test_save_complete_tx_overwrites_stored_partial.)
    our = _make_multisig_ptx(2)
    stored_partial = _make_multisig_ptx(1)
    wallet = FakeWallet(stored_txs={our.txid(): stored_partial})
    Will._add_transaction_to_history(wallet, our, our.txid())
    assert len(wallet.adb.added) == 1
    saved, _ = wallet.adb.added[0]
    # The combine path was taken (the stored partial was re-added, not our tx).
    assert saved is stored_partial
    assert saved.is_complete()


def test_save_removes_complete_item_from_history():
    # A fully-signed item is removed from the local history: its matching
    # entry (exact label) is deleted, and it is never re-added.
    our = _make_multisig_ptx(2)
    txid = our.txid()
    label = "BitcoinAfterLife inheritance transaction - https://we.example"
    item = _make_simple_willitem(our, valid=True, we_url="https://we.example")
    wallet = FakeWallet(stored_txs={txid: our})
    wallet.labels[txid] = label
    Will.save_valid_transactions_to_history({"wid": item}, wallet, label)
    assert wallet.adb.added == []
    assert txid in wallet.adb.removed
    assert txid not in wallet.labels


def test_save_cleanup_removes_stale_exact_label():
    tx = _make_multisig_ptx(1)
    txid = tx.txid()
    stale_txid = "ab" * 32
    other_txid = "cd" * 32
    label = "BitcoinAfterLife inheritance transaction - https://we.example"
    wallet = FakeWallet()
    # Pre-existing wallet labels: one current, one stale (same label), one with
    # a different executor URL that must be kept.
    wallet.labels[txid] = label
    wallet.labels[stale_txid] = label
    wallet.labels[other_txid] = "BitcoinAfterLife inheritance transaction - https://other.example"
    item = _make_simple_willitem(tx, valid=True, we_url="https://we.example")
    Will.save_valid_transactions_to_history({"wid": item}, wallet, label)
    assert stale_txid in wallet.adb.removed
    assert other_txid not in wallet.adb.removed
    assert txid not in wallet.adb.removed
    # The stale tx's label is dropped with it.
    assert stale_txid not in wallet.labels
    assert other_txid in wallet.labels
    assert wallet.labels[txid] == label


def test_save_no_wallet_or_no_adb_is_noop():
    tx = Transaction(_VALID_TX_HEX)
    item = _make_simple_willitem(tx, valid=True)
    Will.save_valid_transactions_to_history({"wid": item}, None, "LBL")
    Will.save_valid_transactions_to_history({"wid": item}, object(), "LBL")


def test_save_never_raises_on_adb_failure():
    tx = Transaction(_VALID_TX_HEX)
    item = _make_simple_willitem(tx, valid=True)

    class BoomWallet(FakeWallet):
        class BoomADB:
            def add_transaction(self, tx, *, allow_unrelated=False, is_new=True):
                raise RuntimeError("boom")

            def remove_transaction(self, txid):
                raise RuntimeError("boom")

        def __init__(self):
            self.adb = self.BoomADB()
            self.labels = {}

        def get_all_labels(self):
            return {"stale": "BitcoinAfterLife inheritance transaction - "}

    wallet = BoomWallet()
    Will.save_valid_transactions_to_history(
        {"wid": item}, wallet, "BitcoinAfterLife inheritance transaction - {willexecutor}"
    )
    # No exception propagates; a fresh fake works afterwards.
    assert True


def test_check_will_does_not_save_to_history():
    # History persistence is no longer triggered from check_will: it runs after
    # the will is signed (see the GUI hooks). check_will must not touch it.
    with patch.object(Will, "save_valid_transactions_to_history") as save_mock:
        Will.check_will({}, [], None, 9999999999)
        save_mock.assert_not_called()


def test_is_will_valid_calls_check_will_without_history_label():
    with patch.object(Will, "check_will") as cw_mock:
        Will.is_will_valid({}, 9999999999, 100, [])
        assert len(cw_mock.call_args[0]) == 4
        assert cw_mock.call_args[0] == ({}, [], False, 9999999999)


# ------------------------------------------------------------------ #
# Signature absorption + status fixes (local-history will tx)
# ------------------------------------------------------------------ #

def _make_willitem_keyed_by_txid(tx, valid=True, we_url=None):
    """A WillItem whose ``_id`` equals its txid (as in a real built will).

    The script descriptor is re-attached after the WillItem round-trips the tx
    through serialization, mirroring the real load-from-wallet flow.
    """
    w = {"tx": tx, "heirs": {"a": ["addr", 100, "30d"]},
         "willexecutor": {"url": we_url} if we_url else None, "status": "",
         "description": "", "time": 0, "change": "", "baltx_fees": 100,
         "VALID": valid}
    item = WillItem(w, _id=tx.txid())
    if isinstance(tx, PartialTransaction) and tx.inputs():
        desc = getattr(tx.inputs()[0], "script_descriptor", None)
        if desc is not None and isinstance(item.tx, PartialTransaction):
            item.tx.inputs()[0].script_descriptor = desc
    return item


def _make_local_wallet(tx, stored, funding):
    """A FakeWallet where *tx* is stored locally and consumes *funding*."""
    return FakeWallet(
        stored_txs={tx.txid(): stored},
        spenders={funding: tx.txid()},
        heights={tx.txid(): TX_HEIGHT_LOCAL},
    )


def test_absorb_history_signatures_merges_and_completes():
    # The wallet's stored local copy of the will tx carries more signatures
    # than the in-memory item: they are merged in and the item becomes COMPLETE.
    item = _make_multisig_willitem(1)
    stored = _make_multisig_ptx(2)
    assert stored.txid() == item.tx.txid()
    wallet = FakeWallet(stored_txs={item._id: stored})
    Will._absorb_history_signatures({item._id: item}, wallet)
    assert item.tx.is_complete() is True
    assert item.get_status("COMPLETE") is True
    assert item.get_status("VALID") is True


def test_absorb_history_signatures_noop_without_stored_copy():
    # Nothing stored in the wallet: the in-memory item is left untouched.
    item = _make_multisig_willitem(1)
    wallet = FakeWallet()
    Will._absorb_history_signatures({item._id: item}, wallet)
    assert item.tx.is_complete() is False
    assert item.get_status("COMPLETE") is False


def test_check_invalidated_keeps_valid_on_local_spend():
    # The funding is missing from the wallet's UTXOs only because the will tx
    # itself was saved into the local history: a wallet-local spender must not
    # invalidate the will.
    tx = _make_multisig_ptx(1)
    item = _make_willitem_keyed_by_txid(tx)
    will = {tx.txid(): item}
    funding = tx.inputs()[0].prevout.to_str()
    wallet = _make_local_wallet(tx, tx, funding)
    Will.check_invalidated(will, [], wallet)
    assert item.get_status("INVALIDATED") is False
    assert item.get_status("VALID") is True


def test_check_invalidated_invalidates_on_real_spend():
    # The funding is consumed by a broadcast transaction: the will is dead.
    tx = _make_multisig_ptx(1)
    item = _make_willitem_keyed_by_txid(tx)
    will = {tx.txid(): item}
    funding = tx.inputs()[0].prevout.to_str()
    ext_spender = "ab" * 32
    wallet = FakeWallet(
        spenders={funding: ext_spender},
        heights={ext_spender: 100},
    )
    Will.check_invalidated(will, [], wallet)
    assert item.get_status("INVALIDATED") is True
    assert item.get_status("VALID") is False


def test_search_rai_local_artifact_keeps_valid():
    tx = _make_multisig_ptx(1)
    item = _make_willitem_keyed_by_txid(tx)
    will = {tx.txid(): item}
    funding = tx.inputs()[0].prevout.to_str()
    wallet = _make_local_wallet(tx, tx, funding)
    Will.search_rai(Will.get_all_inputs(will, only_valid=True), [], will, wallet)
    assert item.get_status("VALID") is True
    assert item.get_status("INVALIDATED") is False
    assert item.get_status("CONFIRMED") is False


def test_search_rai_confirmed_on_broadcast_spender():
    # The will tx is broadcast/confirmed: its own (real) spender marks it
    # CONFIRMED, not INVALIDATED.
    tx = _make_multisig_ptx(1)
    item = _make_willitem_keyed_by_txid(tx)
    will = {tx.txid(): item}
    funding = tx.inputs()[0].prevout.to_str()
    wallet = FakeWallet(
        stored_txs={tx.txid(): tx},
        spenders={funding: tx.txid()},
        heights={tx.txid(): 100},
    )
    Will.search_rai(Will.get_all_inputs(will, only_valid=True), [], will, wallet)
    assert item.get_status("CONFIRMED") is True
    assert item.get_status("INVALIDATED") is False


def test_check_will_merges_history_sigs_and_stays_valid():
    # End-to-end: a will tx stored in the local history gained a signature.
    # check_will must absorb it, not invalidate the will for the local spend.
    now = 1700000000
    locktime = 2000000000
    tx = _make_multisig_ptx(1, locktime)
    stored = _make_multisig_ptx(2, locktime)
    assert stored.txid() == tx.txid()
    item = _make_willitem_keyed_by_txid(tx)
    will = {tx.txid(): item}
    funding = tx.inputs()[0].prevout.to_str()
    wallet = _make_local_wallet(tx, stored, funding)
    Will.check_will(will, [], wallet, now)
    assert item.get_status("COMPLETE") is True
    assert item.get_status("VALID") is True
    assert item.get_status("INVALIDATED") is False


# ------------------------------------------------------------------ #
# Util.get_available_utxos (UTXO-view restoration)
# ------------------------------------------------------------------ #

def _make_utxo(prevout_hex="22", idx=0, value=100000,
               spent_txid=None, spent_height=None):
    txin = PartialTxInput(
        prevout=TxOutpoint(bytes.fromhex(prevout_hex) * 32, idx), script_sig=b""
    )
    txin._trusted_value_sats = value
    txin.spent_txid = spent_txid
    txin.spent_height = spent_height
    return txin


_HISTORY_TEMPLATE = "BitcoinAfterLife inheritance transaction - {willexecutor}"
_HISTORY_LABEL = _HISTORY_TEMPLATE.replace("{willexecutor}", "https://we.example")


def _wallet_with_local_spend(locktime):
    addr = "bcrt1qexample"
    spender = "ab" * 32
    utxo = _make_utxo(spent_txid=spender, spent_height=TX_HEIGHT_LOCAL)
    wallet = FakeWallet(
        stored_txs={spender: _make_multisig_ptx(0, locktime=locktime)},
        heights={spender: TX_HEIGHT_LOCAL},
        outputs={addr: {utxo.prevout.to_str(): utxo}},
        addresses=[addr],
    )
    wallet.labels[spender] = _HISTORY_LABEL
    return wallet, utxo


def test_get_available_utxos_restores_future_bal_local_spend():
    # A later-locktime BAL history tx locally spent the coin: it is restored.
    wallet, utxo = _wallet_with_local_spend(locktime=2000)
    result = Util.get_available_utxos(wallet, _HISTORY_TEMPLATE, 1000)
    assert [u.prevout.to_str() for u in result] == [utxo.prevout.to_str()]


def test_get_available_utxos_does_not_restore_unlabeled_spend():
    wallet, utxo = _wallet_with_local_spend(locktime=2000)
    wallet.labels["ab" * 32] = "some other label"
    result = Util.get_available_utxos(wallet, _HISTORY_TEMPLATE, 1000)
    assert result == []


def test_get_available_utxos_does_not_restore_not_later_locktime():
    # The stored spender's locktime equals the will's locktime (same will):
    # its spend is NOT ignored.
    wallet, utxo = _wallet_with_local_spend(locktime=1000)
    result = Util.get_available_utxos(wallet, _HISTORY_TEMPLATE, 1000)
    assert result == []


def test_get_available_utxos_does_not_restore_confirmed_spend():
    # A broadcast (confirmed) spender is never ignored.
    addr = "bcrt1qexample"
    spender = "ab" * 32
    utxo = _make_utxo(spent_txid=spender, spent_height=100)
    wallet = FakeWallet(
        stored_txs={spender: _make_multisig_ptx(0, locktime=2000)},
        heights={spender: 100},
        outputs={addr: {utxo.prevout.to_str(): utxo}},
        addresses=[addr],
    )
    wallet.labels[spender] = _HISTORY_LABEL
    result = Util.get_available_utxos(wallet, _HISTORY_TEMPLATE, 1000)
    assert result == []


def test_get_available_utxos_none_locktime_is_raw_view():
    # No reference locktime: the raw wallet.get_utxos() view is returned, so a
    # locally-spent coin stays hidden.
    wallet, utxo = _wallet_with_local_spend(locktime=2000)
    result = Util.get_available_utxos(wallet, _HISTORY_TEMPLATE, None)
    assert result == []
    assert Util.get_available_utxos(None, _HISTORY_TEMPLATE, 1000) == []


# ------------------------------------------------------------------ #
# Main
# ------------------------------------------------------------------ #

if __name__ == "__main__":
    for name in sorted(dir()):
        if name.startswith("test_"):
            globals()[name]()
            print(f"  [OK] {name}")
    print("[OK] All will-extra tests passed")
