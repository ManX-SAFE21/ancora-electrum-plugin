"""
Tests for ``Willexecutors.get_willexecutor_transactions`` with the CURRENT
executor selection (``current_willexecutors``).

Every will item keeps its own copy of the executor record taken at build time
(``selected`` = True). Without the current selection, an executor the user
deselected after building the will would still receive the will.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \
        python3 tests/test_core_deselected_executor.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from bal.core.willexecutors import Willexecutors


class FakeItem:
    """Minimal will item: statuses, a tx and the build-time executor copy."""

    def __init__(self, url, **flags):
        self._status = {"VALID": True, "COMPLETE": True}
        self._status.update(flags)
        self.we = {"url": url, "selected": True}
        self.tx = f"rawtx-{url}"

    def get_status(self, name):
        return self._status.get(name, False)


A = "https://we.a.example"
B = "https://we.b.example"


def _will():
    return {"tx_a": FakeItem(A), "tx_b": FakeItem(B)}


def test_without_current_list_previous_behaviour():
    assert set(Willexecutors.get_willexecutor_transactions(_will())) == {A, B}


def test_deselected_executor_is_skipped():
    current = {A: {"selected": True}, B: {"selected": False}}
    out = Willexecutors.get_willexecutor_transactions(
        _will(), current_willexecutors=current
    )
    assert set(out) == {A}
    assert out[A]["txsids"] == ["tx_a"]


def test_executor_removed_from_list_is_skipped():
    current = {A: {"selected": True}}
    out = Willexecutors.get_willexecutor_transactions(
        _will(), current_willexecutors=current
    )
    assert set(out) == {A}


def test_already_pushed_still_excluded_unless_forced():
    will = {"tx_a": FakeItem(A, PUSHED=True)}
    current = {A: {"selected": True}}
    assert Willexecutors.get_willexecutor_transactions(
        will, current_willexecutors=current
    ) == {}
    assert set(
        Willexecutors.get_willexecutor_transactions(
            will, force=True, current_willexecutors=current
        )
    ) == {A}


if __name__ == "__main__":
    for name in sorted(dir()):
        if name.startswith("test_"):
            globals()[name]()
            print(f"  [OK] {name}")
    print("[OK] All deselected-executor tests passed")
