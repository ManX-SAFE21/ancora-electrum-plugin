"""
Tests for the Will-tab context menu and Will-Executor bulk-selection helpers
in ``bal.gui.qt.lists``.

Covers ``_can_sign`` / ``_can_broadcast`` / ``_can_delete`` and
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


def test_can_delete_unsigned():
    assert _can_delete(FakeWillItem(VALID=True)) is True


def test_can_delete_invalid_and_unsigned():
    assert _can_delete(FakeWillItem(VALID=False)) is True


def test_can_delete_valid_and_complete_false():
    assert _can_delete(FakeWillItem(VALID=True, COMPLETE=True)) is False


def test_can_delete_none_false():
    assert _can_delete(None) is False


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
