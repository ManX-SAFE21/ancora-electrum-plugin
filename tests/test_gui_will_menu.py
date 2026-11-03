"""
Tests for the Will-tab context menu and Will-Executor bulk-selection helpers
in ``bal.gui.qt.lists``.

Covers ``_can_sign`` / ``_can_broadcast`` / ``_can_delete`` (CHANGELOG
#129: never a transaction sent to its will-executor; #179: never one sent
at least once, never a copy of the will being prepared or in force) and
``_apply_select_all`` (the pure logic behind the new context-menu actions and
the "Select All" dropdown).

Run:
    QT_QPA_PLATFORM=offscreen python3 tests/test_gui_will_menu.py
"""

import sys

sys.path.insert(0, __file__.rsplit("/", 2)[0])

from bal.gui.qt.lists import (
    _apply_select_all,
    _can_broadcast,
    _can_delete,
    _can_sign,
)


class FakeWillItem:
    """Minimal stand-in for ``bal.core.will.WillItem`` (get_status only)."""

    we = None

    def __init__(self, **status_flags):
        self._status = dict(status_flags)

    def get_status(self, name):
        return self._status.get(name, False)


def _we(selected=False):
    return {"selected": selected}


# ------------------------------------------------------------------ #
# _can_sign / _can_broadcast
# ------------------------------------------------------------------ #

def test_can_sign_unsigned():
    assert _can_sign(FakeWillItem()) is True


def test_can_sign_partially_signed():
    assert _can_sign(FakeWillItem(PARTIALLY_SIGNED=True)) is True


def test_can_sign_complete_false():
    assert _can_sign(FakeWillItem(COMPLETE=True)) is False


def test_can_sign_none_false():
    assert _can_sign(None) is False


def test_can_broadcast_complete():
    assert _can_broadcast(FakeWillItem(COMPLETE=True)) is True


def test_can_broadcast_unsigned_false():
    assert _can_broadcast(FakeWillItem()) is False


def test_can_broadcast_none_false():
    assert _can_broadcast(None) is False


# ------------------------------------------------------------------ #
# _can_delete
# ------------------------------------------------------------------ #

def test_can_delete_invalid():
    assert _can_delete(FakeWillItem(VALID=False, COMPLETE=True)) is True


def test_can_delete_unsigned_false():
    """#179: a draft of the will being prepared is not deleted by hand."""
    assert _can_delete(FakeWillItem(VALID=True)) is False


def test_can_delete_invalid_and_unsigned():
    assert _can_delete(FakeWillItem(VALID=False)) is True


def test_can_delete_valid_and_complete_false():
    assert _can_delete(FakeWillItem(VALID=True, COMPLETE=True)) is False


def test_can_delete_none_false():
    assert _can_delete(None) is False


def _signed_valid(we="https://we.example", **flags):
    """A VALID + COMPLETE (signed) item, optionally with a will-executor."""
    item = FakeWillItem(VALID=True, COMPLETE=True, **flags)
    item.we = {"url": we, "selected": True} if we else None
    return item


def test_can_delete_signed_not_sent_or_failed_false():
    """#179 (owner): a push without an answer or a failed check does not
    say the executor lacks it (we.divinaly.support: "pushed: thx", then
    503); a signed copy not sent yet is part of the will in force (in a
    will in several deliveries its delete left a hole nobody rebuilt)."""
    assert _can_delete(_signed_valid(PUSH_FAIL=True, CHECK_FAIL=True)) is False
    assert _can_delete(_signed_valid(CHECK_FAIL=True)) is False
    assert _can_delete(_signed_valid(PUSH_FAIL=True)) is False
    assert _can_delete(_signed_valid()) is False  # signed, never sent


def test_can_delete_signed_and_pushed_false():
    """The executor holds it: deleting locally would not cancel it."""
    assert _can_delete(_signed_valid(PUSHED=True)) is False
    assert _can_delete(_signed_valid(PUSHED=True, CHECKED=True)) is False
    assert _can_delete(_signed_valid(CHECKED=True)) is False


def test_can_delete_signed_backup_tx_without_executor_false():
    """Signed txs without a will-executor (backup) stay protected."""
    assert _can_delete(_signed_valid(we=None)) is False


def _old(we="https://we.example", **flags):
    """A signed item that is no longer VALID (replaced or invalidated)."""
    item = FakeWillItem(VALID=False, COMPLETE=True, **flags)
    item.we = {"url": we, "selected": True} if we else None
    return item


def test_can_delete_sent_replaced_or_invalidated_false():
    """CHANGELOG #129: a transaction SENT to its will-executor is never
    deleted, also when it is replaced (still executable, part of the will
    in force for #128) or invalidated. Before #129 both were deletable
    (not VALID)."""
    assert _can_delete(_old(REPLACED=True, PUSHED=True)) is False
    assert _can_delete(_old(REPLACED=True, PUSHED=True, CHECKED=True)) is False
    assert _can_delete(_old(INVALIDATED=True, REPLACED=True, PUSHED=True)) is False


def test_can_delete_replaced_never_sent():
    """Never sent: only this wallet has it, deleting it is free (#129)."""
    assert _can_delete(_old(REPLACED=True)) is True
    assert _can_delete(_old(INVALIDATED=True)) is True


def test_can_delete_old_version_sent_once_false():
    """#179: an old version whose push got no answer or whose check failed
    may be on the will-executor's server: kept (before, deletable)."""
    assert _can_delete(_old(REPLACED=True, PUSH_FAIL=True)) is False
    assert _can_delete(_old(INVALIDATED=True, CHECK_FAIL=True)) is False


def test_can_delete_replaced_backup_still_in_force_false():
    """A signed backup (no will-executor) that is replaced still counts as
    in force for ``will_state`` (#128): protected like the VALID one."""
    assert _can_delete(_old(we=None, REPLACED=True)) is False


def test_can_delete_draft_and_partially_signed_false():
    """#179 (owner, choice B): drafts and partially signed copies belong to
    the will being prepared: the Check drops or completes them (before,
    deletable)."""
    assert _can_delete(FakeWillItem(VALID=True)) is False
    assert _can_delete(FakeWillItem(VALID=True, PARTIALLY_SIGNED=True)) is False
    draft = FakeWillItem(VALID=True)
    draft.we = {"url": "https://we.example", "selected": True}
    assert _can_delete(draft) is False


# ------------------------------------------------------------------ #
# _apply_select_all
# ------------------------------------------------------------------ #

def test_select_all_sets_everything():
    wes = {"a": _we(False), "b": _we(False)}
    _apply_select_all(wes, True)
    assert wes["a"]["selected"] is True
    assert wes["b"]["selected"] is True


def test_deselect_all_clears_everything():
    wes = {"a": _we(True), "b": _we(True)}
    _apply_select_all(wes, False)
    assert wes["a"]["selected"] is False
    assert wes["b"]["selected"] is False


def test_select_only_valid():
    wes = {"a": _we(False), "b": _we(True), "c": _we(True)}
    valid = {"a": True, "b": True, "c": False}
    _apply_select_all(wes, True, valid)
    # a, b are valid -> selected; c is invalid -> deselected
    assert wes["a"]["selected"] is True
    assert wes["b"]["selected"] is True
    assert wes["c"]["selected"] is False


def test_deselect_only_invalid():
    wes = {"a": _we(True), "b": _we(True), "c": _we(True)}
    valid = {"a": True, "b": False, "c": True}
    _apply_select_all(wes, False, valid)
    # b is invalid -> deselected; a, c are valid -> keep their state
    assert wes["a"]["selected"] is True
    assert wes["b"]["selected"] is False
    assert wes["c"]["selected"] is True


def test_select_only_valid_missing_url_treated_invalid():
    wes = {"a": _we(False), "b": _we(True)}
    valid = {"a": True}
    _apply_select_all(wes, True, valid)
    assert wes["a"]["selected"] is True
    assert wes["b"]["selected"] is False


# ------------------------------------------------------------------ #
# Main
# ------------------------------------------------------------------ #

if __name__ == "__main__":
    for name in sorted(dir()):
        if name.startswith("test_"):
            globals()[name]()
            print(f"  [OK] {name}")
    print("[OK] All will-menu tests passed")
