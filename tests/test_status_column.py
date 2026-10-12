"""
The "Status" column says what each row is for the will today (CHANGELOG #83).

It used to show the whole status history ("New.Signed.Pushed.Checked", cut
to its last 50 characters) next to a separate "Server" column. Each row is
the copy held by ONE will-executor: the text now follows the same priority
as the row colour (where that executor stands), the "Server" column is gone,
and the tooltip shows the executor and the whole history. The saved history
does not change.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \
        python3 tests/test_status_column.py
"""

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from electrum.transaction import Transaction

from bal.core.will import WillItem
from bal.gui.qt.lists import PreviewList
from bal.gui.qt.theme import status_color, will_status_text, will_status_tooltip

# WillItem parses a raw tx and calls add_info_from_wallet; no live wallet here.
_patcher = patch.object(Transaction, "add_info_from_wallet")
_patcher.start()

_TX_HEX = (
    "01000000012a5c9a94fcde98f5581cd00162c60a13936ceb75389ea65b"
    "f38633b424eb4031000000006c493046022100a82bbc57a0136751e543"
    "3f41cf000b3f1a99c6744775e76ec764fb78c54ee100022100f9e80b7d"
    "e89de861dc6fb0c1429d5da72c2b6b2ee2406bc9bfb1beedd729d98501"
    "2102e61d176da16edd1d258a200ad9759ef63adf8e14cd97f53227bae3"
    "5cdb84d2f6ffffffff0140420f00000000001976a914230ac37834073a"
    "42146f11ef8414ae929feaafc388ac00000000"
)
WE = {"url": "https://we0.test", "base_fee": 1000, "address": "tb1qwe"}


def _item(*statuses, we=WE):
    item = WillItem({
        "tx": _TX_HEX,
        "heirs": {"alice": ["tb1qalice", "100%", 1979683200]},
        "willexecutor": we,
        "baltx_fees": 13,
    })
    for s in statuses:
        item.set_status(s, True)
    return item


def test_texts_follow_the_executor_state():
    assert will_status_text(_item("COMPLETE", "PUSHED", "CHECKED")) == "In force, verified"
    assert will_status_text(_item("COMPLETE", "PUSHED")) == "In force, to be verified"
    assert will_status_text(_item("COMPLETE")) == "Signed, to be sent"
    # CHANGELOG #128: never signed nor sent, it exists only in this wallet.
    assert will_status_text(_item()) == "Draft"


def test_executor_without_the_will_is_never_in_force():
    """Owner's remark: the red divinaly row said "In force"."""
    assert will_status_text(_item("COMPLETE", "PUSH_FAIL")) == "Not received by the executor"
    assert will_status_text(_item("COMPLETE", "PUSHED", "CHECK_FAIL")) == (
        "Not received by the executor")


def test_backup_transaction_without_executor():
    assert will_status_text(_item("COMPLETE", we=None)) == "In force"


def test_partially_signed_shows_the_signatures():
    item = _item()
    item.sigs_have, item.sigs_required = 1, 2
    assert will_status_text(item) == "To sign (1/2)"


def test_a_draft_says_draft_not_its_signature_count():
    """CHANGELOG #128 (owner's report "Da firmare (0/1)"): a will prepared
    and never signed nor sent is a draft that the next build drops for free;
    a partially signed one keeps its count."""
    item = _item()
    item.sigs_have, item.sigs_required = 0, 1
    assert will_status_text(item) == "Draft"
    item.set_status("PARTIALLY_SIGNED", True)
    item.sigs_have, item.sigs_required = 1, 3
    assert will_status_text(item) == "To sign (1/3)"


def test_replaced_invalidated_mempool_executed_not_valid():
    assert will_status_text(_item("COMPLETE", "PUSHED", "REPLACED")) == "Replaced by a newer will"
    assert will_status_text(_item("COMPLETE", "UPDATED")) == "Replaced by a newer will"
    assert will_status_text(_item("COMPLETE", "INVALIDATED")) == "Invalidated on-chain"
    assert will_status_text(_item("COMPLETE", "MEMPOOL")) == "Being executed (mempool)"
    assert will_status_text(_item("COMPLETE", "CONFIRMED")) == "Executed"
    item = _item("COMPLETE", "PUSHED", "CHECKED")
    item.set_status("VALID", False)
    assert will_status_text(item) == "Not valid"


def test_text_and_colour_agree():
    """Same priority: one text per colour."""
    pairs = {
        "#8afa6c": "In force, verified",
        "#73f3c8": "In force, to be verified",
        "#e83845": "Not received by the executor",
        "#2bc8ed": "Signed, to be sent",
    }
    for statuses in (("COMPLETE", "PUSHED", "CHECKED"), ("COMPLETE", "PUSHED"),
                     ("COMPLETE", "PUSH_FAIL"), ("COMPLETE",)):
        item = _item(*statuses)
        assert pairs[status_color(item)] == will_status_text(item), statuses


def test_tooltip_shows_the_executor_and_the_history():
    tip = will_status_tooltip(_item("COMPLETE", "PUSHED", "CHECKED"))
    assert tip.startswith("Will-Executor: https://we0.test\nHistory: ")
    assert "Signed → Pushed → Checked" in tip
    assert will_status_tooltip(_item("COMPLETE", we=None)).startswith("No will-executor\n")


def test_no_server_column_any_more():
    names = [c.name for c in PreviewList.Columns]
    assert names == ["LOCKTIME", "TXID", "WILLEXECUTOR", "STATUS"]


def test_saved_history_is_unchanged():
    """Display only: the history stays English and dot separated."""
    item = _item("COMPLETE", "PUSHED")
    will_status_text(item)
    will_status_tooltip(item)
    assert item.status.endswith(".Signed.Pushed")


if __name__ == "__main__":
    for name in sorted(dir()):
        if name.startswith("test_"):
            globals()[name]()
            print(f"  [OK] {name}")
    print("[OK] All status-column tests passed")
