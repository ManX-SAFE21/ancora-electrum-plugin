"""
Reproduce the 'NoneType object has no attribute get' error that appears
in the GUI when the user deletes old will transactions and clicks Check.

This exercises the exact same code path as BalBuildWillDialog.task_phase1
but without requiring a full Qt event loop.
"""

import contextlib
import copy
import json
import os
import sys
import time
import traceback
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

# Electrum source
ELECTRUM_DIR = os.path.expanduser("~/devel/bal/electrum")
if os.path.isdir(ELECTRUM_DIR):
    sys.path.insert(0, ELECTRUM_DIR)

from bal.core.heirs import Heirs
from bal.core.willexecutors import Willexecutors
from bal.core.will import Will, WillItem, NotCompleteWillException, NoHeirsException, NoWillExecutorNotPresent
from bal.core.plugin_base import BalPlugin, BalTimestamp

# ------------------------------------------------------------------ #
# Load karen7 wallet data
# ------------------------------------------------------------------ #
wallet_path = os.path.join(os.path.dirname(__file__), "karen7")
with open(wallet_path) as f:
    KAREN7_DATA = json.load(f)

# ------------------------------------------------------------------ #
# Minimal UTXO stub
# ------------------------------------------------------------------ #
class FakeUTXO:
    def __init__(self, txid, out_idx, value_sats):
        self._value = value_sats
        self.prevout = MagicMock()
        self.prevout.txid = txid
        self.prevout.out_idx = out_idx

    def value_sats(self):
        return self._value

def build_utxos(data):
    utxos = []
    txo = data.get("txo", {})
    for txid, outputs in txo.items():
        if not isinstance(outputs, dict):
            continue
        for addr, out_map in outputs.items():
            if not isinstance(out_map, dict):
                continue
            for idx, info in out_map.items():
                if not isinstance(info, list) or len(info) < 2:
                    continue
                value, spent = info[0], info[1]
                if spent is False:
                    utxos.append(FakeUTXO(txid, int(idx), value))
    return utxos


class FakeBalWindow:
    """Simulates bal_window for the dialog's task_phase1 flow."""

    def __init__(self, heirs_obj, will_settings, bal_plugin, wallet,
                 window_wallet, willitems=None):
        self.heirs = heirs_obj
        self.will_settings = will_settings
        self.bal_plugin = bal_plugin
        self.wallet = wallet
        self.window = MagicMock()
        self.window.wallet = window_wallet
        self.willitems = willitems or {}
        self.will = {}
        self.willexecutors = {}
        self.no_willexecutor = None
        self.date_to_check = None

    def init_class_variables(self):
        if not self.heirs:
            raise NoHeirsException("Heirs are not defined")
        from bal.core.plugin_base import BalTimestamp
        from datetime import datetime
        self.date_to_check = BalTimestamp(self.will_settings['threshold']).to_timestamp()
        self.no_willexecutor = self.bal_plugin.NO_WILLEXECUTOR.get()
        self.willexecutors = Willexecutors.get_willexecutors(
            self.bal_plugin, update=False, bal_window=self
        )
        if (
            not self.bal_plugin.is_basic_mode()
            and self.date_to_check < datetime.now().timestamp()
        ):
            pass

    def check_will(self):
        return Will.is_will_valid(
            self.willitems,
            self.date_to_check,
            self.will_settings["baltx_fees"],
            self.window.wallet.get_utxos(),
            heirs=self.heirs,
            willexecutors=self.willexecutors,
            self_willexecutor=self.no_willexecutor,
            wallet=self.wallet,
        )

    def build_will(self):
        will = {}
        self.willexecutors = Willexecutors.get_willexecutors(
            self.bal_plugin, update=False, bal_window=self
        )
        if not self.no_willexecutor:
            f = False
            for _u, w in self.willexecutors.items():
                if Willexecutors.is_selected(w):
                    f = True
            if not f:
                raise NoWillExecutorNotPresent(
                    "No Will-Executor or backup transaction selected"
                )
        txs = self.heirs.get_transactions(
            self.bal_plugin,
            self.window.wallet,
            self.will_settings["baltx_fees"],
            None,
            self.date_to_check,
        )

        creation_time = time.time()
        if txs:
            for txid in txs:
                tx = {}
                tx["tx"] = txs[txid]
                tx["my_locktime"] = txs[txid].my_locktime
                tx["heirsvalue"] = txs[txid].heirsvalue
                tx["description"] = txs[txid].description
                tx["willexecutor"] = copy.deepcopy(txs[txid].willexecutor)
                tx["status"] = "New"
                tx["baltx_fees"] = txs[txid].tx_fees
                tx["time"] = creation_time
                tx["heirs"] = copy.deepcopy(txs[txid].heirs)
                tx["txchildren"] = []
                will[txid] = WillItem(tx, _id=txid, wallet=self.wallet)
            Will.update_will(self.willitems, will)
            self.willitems.update(will)
            Will.normalize_will(self.willitems, self.wallet)
        else:
            return {}
        return self.willitems

    def update_all(self):
        pass


def test_simulate_task_phase1():
    """Simulate the exact steps that task_phase1 runs, catching any NoneType error."""

    # 1. Build Heirs from karen7 data
    heirs_data = KAREN7_DATA["heirs"]
    h = Heirs.__new__(Heirs)
    h.update(heirs_data)
    assert len(h) == 4

    # 2. Build UTXOs
    utxos = build_utxos(KAREN7_DATA)
    assert len(utxos) > 0, "no unspent UTXOs"

    # 3. Create will_settings dict
    default_settings = BalPlugin.default_will_settings()
    will_settings = {
        "baltx_fees": 1,
        "threshold": default_settings["threshold"],
        "locktime": default_settings["locktime"],
    }

    # 4. Create mock wallet
    wallet = MagicMock()
    wallet.dust_threshold.return_value = 500
    wallet.get_change_addresses_for_new_transaction.return_value = [
        "bcrt1q0000000000000000000000000000000000000"
    ]
    wallet.get_utxos.return_value = utxos
    wallet.db = MagicMock()
    wallet.db.get.return_value = heirs_data

    window_wallet = MagicMock()
    window_wallet.get_utxos.return_value = utxos
    window_wallet.dust_threshold.return_value = 500

    # 5. Create bal_plugin
    bal_plugin = MagicMock()
    bal_plugin.NO_WILLEXECUTOR.get.return_value = True
    bal_plugin.ENABLE_MULTIVERSE.get.return_value = False
    bal_plugin.WILL_SETTINGS.get.return_value = will_settings
    bal_plugin.is_basic_mode.return_value = True
    bal_plugin.get_decimal_point.return_value = 8

    # 6. Create FakeBalWindow with EMPTY willitems (user deleted all transactions)
    bw = FakeBalWindow(
        heirs_obj=h,
        will_settings=will_settings,
        bal_plugin=bal_plugin,
        wallet=wallet,
        window_wallet=window_wallet,
        willitems={},
    )

    # 7. Mock the Electrum-heavy parts for buildTransactions
    _fake_outputs = []
    def _fake_from_address_and_value(address, value):
        out = MagicMock()
        out.value = value
        out.address = address
        out.scriptpubkey = b""
        out.script_descriptor = ""
        _fake_outputs.append(out)
        return out

    _fake_txs = []
    def _fake_from_io(inputs, outputs, locktime, version):
        tx = MagicMock()
        tx.txid.return_value = f"faketx_{len(_fake_txs):04x}"
        tx.estimated_size.return_value = 100
        tx.get_fee.return_value = 100
        tx.input_value.return_value = sum(inp.value_sats() for inp in inputs)
        tx.output_value.return_value = sum(out.value for out in outputs)
        tx.get_output_idxs_from_address.return_value = [0]
        tx.description = ""
        tx.heirsvalue = 0
        tx.my_locktime = 0
        tx.willexecutor = None
        tx.heirs = {}
        tx.available_utxos = []
        tx.tx_fees = 1
        _fake_txs.append(tx)
        return tx

    patches = [
        patch("bal.core.heirs.bitcoin.is_address", return_value=True),
        patch("bal.core.heirs.PartialTxOutput.from_address_and_value",
              side_effect=_fake_from_address_and_value),
        patch("bal.core.heirs.PartialTransaction.from_io",
              side_effect=_fake_from_io),
    ]

    # Monkey-patch Will.get_tx_from_any to accept MagicMock
    original_get_tx_from_any = Will.get_tx_from_any
    def patched_get_tx_from_any(a):
        if isinstance(a, MagicMock):
            return a
        return original_get_tx_from_any(a)
    Will.get_tx_from_any = staticmethod(patched_get_tx_from_any)

    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)

        # STEP A: init_class_variables
        try:
            bw.init_class_variables()
            print("[OK] init_class_variables")
        except Exception as e:
            tb = traceback.format_exc()
            print(f"[FAIL] init_class_variables raised {type(e).__name__}: {e}")
            print(tb)
            raise

        # STEP B: check_amounts (expect PercAmountException for 101%)
        try:
            Will.check_amounts(
                bw.heirs,
                bw.willexecutors,
                bw.window.wallet.get_utxos(),
                bw.date_to_check,
                bw.window.wallet.dust_threshold(),
            )
            print("[OK] check_amounts")
        except Exception as e:
            print(f"[NOTE] check_amounts raised {type(e).__name__}: {e}")

        # STEP C: check_will (empty willitems -> NotCompleteWillException)
        have_to_build = False
        try:
            bw.check_will()
            print("[OK] check_will")
        except NotCompleteWillException:
            have_to_build = True
            print("[OK] check_will: NotCompleteWillException (expected for empty will)")
        except Exception as e:
            print(f"[FAIL] check_will raised unexpected {type(e).__name__}: {e}")
            traceback.print_exc()
            raise

        # STEP D: build_will
        if have_to_build:
            try:
                txs = bw.build_will()
                if txs:
                    print(f"[OK] build_will: built {len(txs)} transaction(s)")
                    # Now simulate the post-build steps from task_phase1
                    for wid in Will.only_valid(bw.willitems):
                        heirs = bw.willitems[wid].heirs
                        print(f"  - will txid: {wid[:16]}..., heirs: {list(heirs.keys())}")
                else:
                    print("[NOTE] build_will returned empty")
            except Exception as e:
                tb = traceback.format_exc()
                print(f"[FAIL] build_will raised {type(e).__name__}: {e}")
                print(tb)
                raise
        else:
            print("[SKIP] build_will")

        # STEP E: Simulate what check() does after dialog returns
        print("\n--- Post-dialog steps ---")
        will_to_check = {}
        for wid, w in bw.willitems.items():
            if Will.needs_server_check(w):
                will_to_check[wid] = w
        print(f"[OK] needs_server_check: {len(will_to_check)} need checking")

    print("\n[DONE] All steps completed without NoneType error")


if __name__ == "__main__":
    test_simulate_task_phase1()
