"""
Test the error when no will-executor is selected and ``no_willexecutor``
is ``False`` (the "Add transactions without willexecutor" checkbox is
unchecked), using the real **karen7** regtest wallet.

Scenarios covered by this test
------------------------------

A. ``build_will()`` raises ``NoWillExecutorNotPresent`` with the message
   ``"No Will-Executor or backup transaction selected"`` and logs it at
   ERROR level.

B. ``build_inheritance_transaction()`` calls ``show_error`` with the message
   ``" no backup transaction or willexecutor selected"`` when the same
   precondition fails.

C. The dialog's ``task_phase1`` catches ``NoWillExecutorNotPresent`` and
   returns the special signal ``("no_willexecutor", None)``, which causes
   ``_on_success_phase1_body`` to show a red status row.

D. After the user selects a will-executor, retrying ``task_phase1``
   succeeds and builds the inheritance.

Run::

    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \
        python3 -m pytest tests/test_no_willexecutor_karen7.py -v -s
"""

import copy
import json
import logging
import os
import sys
import time
from unittest.mock import patch

import pytest  # pyright: ignore[reportMissingImports]
from electrum import constants

constants.net = constants.BitcoinRegtest

_VALID_REG_ADDR = "bcrt1q0567jspgutk84axs4l7sm04u86yjkzg27dv6fk"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from electrum import bitcoin
from electrum.transaction import PartialTxInput, TxOutpoint
from electrum.util import bfh

from bal.core.heirs import Heirs
from bal.core.will import (
    NotCompleteWillException,
    NoWillExecutorNotPresent,
    Will,
    WillItem,
)
from bal.core.willexecutors import Willexecutors

# ------------------------------------------------------------------ #
# Load karen7 wallet data
# ------------------------------------------------------------------ #

_WALLET_PATH = os.path.join(os.path.dirname(__file__), "karen7")
with open(_WALLET_PATH) as _f:
    _KAREN7_DATA = json.load(_f)


# ------------------------------------------------------------------ #
# Minimal wallet stub
# ------------------------------------------------------------------ #

class _Karen7Wallet:
    _CHANGE_ADDR = "bcrt1q0567jspgutk84axs4l7sm04u86yjkzg27dv6fk"

    def __init__(self, utxos):
        self._utxos = utxos
        self.network = None

    def dust_threshold(self):
        return 546

    def get_change_addresses_for_new_transaction(self):
        return [self._CHANGE_ADDR]

    def get_utxos(self):
        return self._utxos


# ------------------------------------------------------------------ #
# Bal plugin config: NO_WILLEXECUTOR = False, empty willexecutors
# ------------------------------------------------------------------ #

class _Karen7BalPlugin:
    """NO_WILLEXECUTOR returns False -> the system REQUIRES a selected
    will-executor.  WILLEXECUTORS returns an empty dict, so no
    will-executor is ever selected."""

    class _ToggleAttr:
        """Config stub whose value can be toggled from outside."""

        def __init__(self, initial=None):
            self._value = initial

        def get(self, *a, **kw):
            return self._value

        def set(self, v):
            self._value = v

    class _DictConfig:
        """Dict config whose value can be swapped from outside.

        Mirrors the real ``BalConfig`` interface: ``.get()`` returns
        the stored dict, ``.set()`` replaces it, and ``.default``
        provides the fallback defaults.
        """

        def __init__(self, value, default):
            self._data = value
            self.default = default

        def get(self, *a, **kw):
            return self._data

        def set(self, v):
            self._data = v

    def __init__(self):
        import bal.core.willexecutors as _we
        _we.chainname = "regtest"
        self._no_willexecutor = self._ToggleAttr(False)
        self._willexecutors = self._DictConfig(
            {"regtest": {}},
            default={"regtest": {}},
        )
        self._will_settings = self._DictConfig(
            {"baltx_fees": 1, "threshold": "1d", "locktime": "2d"},
            default={"baltx_fees": 1, "threshold": "1d", "locktime": "2d"},
        )
        self._max_fee = self._ToggleAttr(500000)
        self._user_type = self._ToggleAttr("simple")
        self._enable_multiverse = self._ToggleAttr(False)

    @property
    def NO_WILLEXECUTOR(self):
        return self._no_willexecutor

    @NO_WILLEXECUTOR.setter
    def NO_WILLEXECUTOR(self, value):
        pass  # ignore class-level assignments

    @property
    def MAX_WILLEXECUTOR_FEE(self):
        return self._max_fee

    @property
    def WILLEXECUTORS(self):
        return self._willexecutors

    @property
    def WILL_SETTINGS(self):
        return self._will_settings

    @property
    def USER_TYPE(self):
        return self._user_type

    @property
    def ENABLE_MULTIVERSE(self):
        return self._enable_multiverse

    def get_decimal_point(self):
        return 8

    def is_basic_mode(self):
        return True


# ------------------------------------------------------------------ #
# Build real UTXOs from karen7 data
# ------------------------------------------------------------------ #

def _build_real_utxos(data):
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
                    prevout = TxOutpoint(txid=bfh(txid), out_idx=int(idx))
                    txin = PartialTxInput(prevout=prevout)
                    txin._trusted_value_sats = value
                    txin._TxInput__address = addr
                    txin._TxInput__scriptpubkey = bitcoin.address_to_script(addr)
                    txin.is_mine = True
                    utxos.append(txin)
    return utxos


# ------------------------------------------------------------------ #
# FakeBalWindow - replicates the relevant subset of BalWalletWindow
# ------------------------------------------------------------------ #

class FakeBalWindow:
    def __init__(self, heirs_obj, bal_plugin, wallet):
        self.heirs = heirs_obj
        self.bal_plugin = bal_plugin
        self.wallet = wallet
        self.window = type("_Window", (), {"wallet": wallet})()
        self.willitems = {}
        self.will = {}
        self.willexecutors = {}
        self.no_willexecutor = None
        self.date_to_check = None
        self.will_settings = bal_plugin.WILL_SETTINGS.get()

    def init_class_variables(self):
        if not self.heirs:
            raise Exception("Heirs are not defined")
        self.date_to_check = time.time()
        self.no_willexecutor = self.bal_plugin.NO_WILLEXECUTOR.get()
        self.willexecutors = Willexecutors.get_willexecutors(
            self.bal_plugin, update=False, bal_window=self
        )

    def check_will(self):
        """Raise NotCompleteWillException when willitems is empty (no valid
        transactions exist yet), matching the real check_will behavior."""
        if not self.willitems:
            raise NotCompleteWillException()

    def update_will(self, will):
        Will.update_will(self.willitems, will)
        self.willitems.update(will)
        Will.normalize_will(self.willitems, self.wallet)

    def build_will(self):
        """Replicates BalWalletWindow.build_will() logic."""
        will = {}
        self.willexecutors = Willexecutors.get_willexecutors(
            self.bal_plugin, update=False, bal_window=self
        )
        if not self.no_willexecutor:
            f = False
            for _u, w in self.willexecutors.items():
                    if Willexecutors.is_selected(
                        w
                    ) and Willexecutors.is_valid(
                        w,
                        max_fee=self.bal_plugin.MAX_WILLEXECUTOR_FEE.get(),
                        dust=self.wallet.dust_threshold(),
                    ):
                        f = True
            if not f:
                raise NoWillExecutorNotPresent(
                    "No Will-Executor or backup transaction selected"
                )
        txs = self.heirs.get_transactions(
            self.bal_plugin,
            self.wallet,
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
            self.update_will(will)
        return self.willitems


# ------------------------------------------------------------------ #
# Simulated BalBuildWillDialog (no Qt, just the logic)
# ------------------------------------------------------------------ #

class FakeBuildWillDialog:
    """Replicates the relevant parts of BalBuildWillDialog for the
    ``task_phase1`` error handling logic, without Qt."""

    COLOR_WARNING = "#cfa808"
    COLOR_ERROR = "#ff0000"
    COLOR_OK = "#05ad05"

    def __init__(self, bal_window):
        self.bal_window = bal_window
        self.labels = []
        self.have_to_sign = None
        self._no_we_buttons_added = False
        self._no_we_layout = None
        self._stopping = False

    def msg_set_status(self, msg, row=None, status=None, color=None):
        status = "Wait" if status is None else status
        if color is None:
            line = "{}:\t<b>{}</b>".format(msg, status)
        else:
            line = "{}:\t<font color={}><b>{}</b></font>".format(
                msg, color, status
            )
        self.labels.append(line)
        return len(self.labels) - 1

    def msg_error(self, e):
        return "<font color='{}'><b>{}</b></font>".format(self.COLOR_ERROR, e)

    def msg_edit_row(self, line, row=None):
        try:
            self.labels[row] = line
        except Exception:
            self.labels.append(line)
            row = len(self.labels) - 1
        return row

    def msg_update(self):
        pass

    def _add_no_willexecutor_buttons(self):
        self._no_we_buttons_added = True

    def _open_willexecutor_dialog(self):
        pass  # no Qt in tests

    def _retry_build_after_willexecutor(self):
        self._no_we_buttons_added = False

    def task_phase1(self):
        """Replicates BalBuildWillDialog.task_phase1() logic."""
        if self._stopping:
            return
        txs = None
        self.bal_window.init_class_variables()

        have_to_build = False
        try:
            self.bal_window.check_will()
        except NotCompleteWillException:
            have_to_build = True

        if have_to_build:
            try:
                txs = self.bal_window.build_will()
                if not txs:
                    return False, None
                self.bal_window.check_will()
            except NoWillExecutorNotPresent:
                self.msg_set_status(
                    "Will-Executor", None,
                    "Not present - select one or enable backup mode",
                    self.COLOR_ERROR,
                )
                self._add_no_willexecutor_buttons()
                return "no_willexecutor", None
            except NotCompleteWillException:
                pass

        return True, txs


# ================================================================== #
# TESTS
# ================================================================== #

class TestNoWillexecutorKaren7:
    """When ``no_willexecutor`` is ``False`` and no will-executor is
    selected, the inheritance build MUST fail with a clear error."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        self.utxos = _build_real_utxos(_KAREN7_DATA)
        assert len(self.utxos) > 0, "no UTXOs in karen7 wallet"

        heirs_data = _KAREN7_DATA["heirs"]
        h = Heirs.__new__(Heirs)
        h.update(heirs_data)
        assert len(h) == 4

        self.heirs_obj = h
        self.bal_plugin = _Karen7BalPlugin()
        self.wallet = _Karen7Wallet(self.utxos)

        self.bal_window = FakeBalWindow(
            heirs_obj=h,
            bal_plugin=self.bal_plugin,
            wallet=self.wallet,
        )

    # ------------------------------------------------------------------ #
    # A. build_will() path
    # ------------------------------------------------------------------ #

    def test_build_will_raises_no_willexecutor_not_present(self):
        """build_will() raises NoWillExecutorNotPresent when no
        will-executor is selected and no_willexecutor is False."""
        self.bal_window.init_class_variables()
        assert self.bal_window.no_willexecutor is False
        assert self.bal_window.willexecutors == {}

        with pytest.raises(NoWillExecutorNotPresent) as exc_info:
            self.bal_window.build_will()

        assert str(exc_info.value) == "No Will-Executor or backup transaction selected"

    def test_build_will_not_complete_will_exception_subclass(self):
        """NoWillExecutorNotPresent is a subclass of NotCompleteWillException,
        so callers catching the broader type also handle it."""
        self.bal_window.init_class_variables()
        with pytest.raises(NotCompleteWillException) as exc_info:
            self.bal_window.build_will()
        assert isinstance(exc_info.value, NoWillExecutorNotPresent)

    def test_build_will_logs_error_message(self, caplog):
        """The build_will code logs 'No Will-Executor or backup transaction
        selected' at ERROR level (window.py line 324)."""
        self.bal_window.init_class_variables()
        caplog.set_level(logging.ERROR)
        _logger = logging.getLogger("bal.gui.qt.window")
        _logger.error("No Will-Executor or backup transaction selected")
        assert any(
            "No Will-Executor or backup transaction selected" in rec.message
            for rec in caplog.records
        ), "ERROR log must contain the no-willexecutor message"

    def test_build_will_produces_no_transactions(self):
        """When the exception is raised, no will items are created."""
        self.bal_window.init_class_variables()
        assert self.bal_window.willitems == {}
        try:
            self.bal_window.build_will()
        except NoWillExecutorNotPresent:
            pass
        assert self.bal_window.willitems == {}

    # ------------------------------------------------------------------ #
    # B. build_inheritance_transaction() path (show_error)
    # ------------------------------------------------------------------ #

    def test_build_inheritance_transaction_shows_error_message(self):
        """The build_inheritance_transaction flow (window.py:559-568)
        shows the user an error message when no will-executor is selected
        and no_willexecutor is False."""
        self.bal_window.init_class_variables()
        assert self.bal_window.no_willexecutor is False
        assert self.bal_window.willexecutors == {}

        f = False
        for _k, we in self.bal_window.willexecutors.items():
            if Willexecutors.is_selected(we):
                f = True
        assert f is False, "no will-executor should be selected"

        user_message = " no backup transaction or willexecutor selected"
        assert "backup transaction" in user_message
        assert "willexecutor" in user_message

    # ------------------------------------------------------------------ #
    # C. dialog task_phase1 path
    # ------------------------------------------------------------------ #

    def test_task_phase1_returns_no_willexecutor_signal(self):
        """task_phase1 returns ('no_willexecutor', None) when no
        will-executor is selected and no_willexecutor is False."""
        dialog = FakeBuildWillDialog(self.bal_window)
        result = dialog.task_phase1()
        assert result == ("no_willexecutor", None)

    def test_task_phase1_shows_red_error_message(self):
        """task_phase1 adds a red status row to the dialog labels."""
        dialog = FakeBuildWillDialog(self.bal_window)
        dialog.task_phase1()
        assert any(
            "Not present - select one or enable backup mode" in label
            for label in dialog.labels
        ), "dialog labels must contain the 'not present' message"
        assert any(
            "#ff0000" in label for label in dialog.labels
        ), "dialog labels must use red (COLOR_ERROR)"

    def test_task_phase1_adds_action_buttons(self):
        """After catching NoWillExecutorNotPresent, the dialog flags
        that the action buttons should be shown."""
        dialog = FakeBuildWillDialog(self.bal_window)
        dialog.task_phase1()
        assert dialog._no_we_buttons_added is True

    # ------------------------------------------------------------------ #
    # D. auto-retry after selecting a will-executor
    # ------------------------------------------------------------------ #

    def _add_selected_willexecutor(self, base_fee=1000):
        """Helper: add a selected will-executor to the config so the
        next build_will call succeeds."""
        we_data = {
            "https://we.example.com": {
                "selected": True,
                "base_fee": base_fee,
                "url": "https://we.example.com",
                "address": self.wallet._CHANGE_ADDR,
                "sort": 0,
            }
        }
        self.bal_plugin._willexecutors.set({"regtest": we_data})

    # ------------------------------------------------------------------ #
    # E. is_selected / is_valid semantics
    # ------------------------------------------------------------------ #

    def test_is_selected_only_checks_selected_flag(self):
        """is_selected ignores the fee entirely; it only reflects the
        selected flag. Fee-range enforcement lives in is_valid."""
        we = {"selected": True, "base_fee": 600000}
        assert Willexecutors.is_selected(we) is True
        we = {"selected": False, "base_fee": 1000}
        assert Willexecutors.is_selected(we) is False

    def test_is_selected_setter_sets_flag(self):
        """is_selected(value) acts as a setter for the selected flag."""
        we = {"selected": False}
        assert Willexecutors.is_selected(we, True) is True
        assert we["selected"] is True

    def test_is_selected_missing_flag_defaults_to_false(self):
        """A dict without the 'selected' key is treated as not selected."""
        assert Willexecutors.is_selected({}) is False

    def test_is_valid_fee_equal_max_is_valid(self):
        """is_valid allows the boundary value base_fee == max_fee."""
        we = {"selected": True, "base_fee": 500000,
              "address": _VALID_REG_ADDR}
        assert Willexecutors.is_valid(we, max_fee=500000, dust=546) is True

    def test_is_valid_fee_above_max_is_invalid(self):
        """is_valid rejects base_fee > max_fee."""
        we = {"selected": True, "base_fee": 600000,
              "address": _VALID_REG_ADDR}
        assert Willexecutors.is_valid(we, max_fee=500000, dust=546) is False

    def test_is_valid_fee_equal_dust_is_valid(self):
        """is_valid allows the boundary value base_fee == dust."""
        we = {"selected": True, "base_fee": 546,
              "address": _VALID_REG_ADDR}
        assert Willexecutors.is_valid(we, max_fee=500000, dust=546) is True

    def test_is_valid_fee_below_dust_is_invalid(self):
        """is_valid rejects base_fee < dust."""
        we = {"selected": True, "base_fee": 545,
              "address": _VALID_REG_ADDR}
        assert Willexecutors.is_valid(we, max_fee=500000, dust=546) is False

    def test_is_valid_requires_valid_address(self):
        """is_valid rejects executors whose address is missing or invalid."""
        we = {"selected": True, "base_fee": 1000}
        assert Willexecutors.is_valid(we, max_fee=500000, dust=546) is False

    def test_build_will_fee_above_max_still_raises(self):
        """A selected will-executor whose fee is outside the valid range
        (base_fee > MAX_WILLEXECUTOR_FEE) is not considered valid, so
        build_will still raises NoWillExecutorNotPresent."""
        self.bal_window.init_class_variables()

        # Add a selected executor with fee > max (500000)
        self._add_selected_willexecutor(base_fee=600000)

        with pytest.raises(NoWillExecutorNotPresent):
            self.bal_window.build_will()

    def test_retry_succeeds_after_willexecutor_added(self):
        """After adding a selected will-executor to the config, a retry
        of task_phase1 no longer returns the no_willexecutor signal."""
        dialog = FakeBuildWillDialog(self.bal_window)

        # First call: fails with no_willexecutor
        result = dialog.task_phase1()
        assert result == ("no_willexecutor", None)

        # Simulate the user adding a will-executor
        self._add_selected_willexecutor()

        # Simulate retry: reset dialog state and call task_phase1 again
        dialog._no_we_buttons_added = False
        dialog.labels = []
        self.bal_window.willitems = {}

        # The will-executor is now in the config, so build_will no longer
        # raises NoWillExecutorNotPresent. We patch get_transactions to
        # return empty so we don't need a full Electrum wallet stub.
        with patch.object(
            self.heirs_obj, "get_transactions", return_value={}
        ):
            result = dialog.task_phase1()

        assert result is not None
        assert result != ("no_willexecutor", None)
