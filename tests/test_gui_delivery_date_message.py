"""The Check names the will's date, not one heir, when the date changes (CHANGELOG #125).

Owner's ZIP-2 test (2026-10-12): after he changed the delivery date, the
Check said 'The delivery date of heir "1" changed', and he asked why only
one of his two heirs. Every heir has the will's date (multiverse off), so
the will's date changed: the Check now says "The delivery date changed:
preparing a new will." With multiverse on and heirs with different dates
(a hidden option), the sentence still names the heir whose date changed.

Wallets, coins and will-executors come from ``tests/flowkit.py`` (three
fake will-executors with their own fee addresses, throw-away test wallet
with the test password, fixed clock).

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_gui_delivery_date_message.py
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

import flowkit as fk
from PyQt6.QtWidgets import QApplication

# Kept at module level: an unreferenced application object is destroyed at
# once, and creating a widget afterwards aborts Python.
_APP = QApplication.instance() or QApplication(sys.argv)

# Test value: the password of the throw-away test wallets.
TEST_PASSWORD = "flowkit-test"

GENERIC = "The delivery date changed: preparing a new will."


@pytest.fixture
def make(monkeypatch):
    """``tests/flowkit.py`` sessions with three will-executors."""
    fk.ensure_event_loop()
    made = []

    def _make(**kw):
        """One session (``fk.Session`` arguments), three will-executors."""
        kw.setdefault("password", TEST_PASSWORD)
        s = fk.Session(monkeypatch, **kw)
        s.world.add_executor()
        made.append(s)
        return s

    yield _make
    for s in made:
        s.close()
    fk.stop_event_loop()


def _rows(s):
    """Every row the last step showed, in the Check dialogs it opened too."""
    step = s.recorder.steps[-1]
    rows = [fk.strip(r) for r in step.get("rows", [])]
    for d in step.get("dialogs_opened", []):
        rows += [fk.strip(r) for r in d["rows"]]
    return rows


def _two_heirs_sent_will(make, mode):
    """Two heirs (as the owner's), a will signed, sent and verified."""
    s = make(mode=mode, locktime=fk.ts(2029, 8, 28))
    s.set_heirs(alice="50%", bob="30%")
    s.check()
    return s


@pytest.mark.parametrize("mode", ["basic", "advanced"])
def test_an_earlier_date_names_the_will_not_one_heir(make, mode):
    """The owner's case: a new (earlier, free) date for the will; the Check
    names the will's date, never heir "alice" alone."""
    s = _two_heirs_sent_will(make, mode)
    s.set_date(fk.ts(2029, 8, 20))
    s.check(cancel_on=("sign your will",))
    rows = " ".join(_rows(s))
    assert GENERIC in rows, rows
    assert 'of heir "' not in rows, rows


def test_heirs_with_their_own_dates_still_name_the_heir(make):
    """Multiverse on (hidden option), heirs with different dates: only
    bob's date changes, and the Check names bob."""
    s = _two_heirs_sent_will(make, "basic")
    s.plugin.ENABLE_MULTIVERSE.set(True)
    bob = list(s.bw.heirs["bob"])
    bob[2] = fk.ts(2029, 8, 20)
    s.bw.heirs["bob"] = bob
    s.check(cancel_on=("sign your will",))
    rows = " ".join(_rows(s))
    assert 'The delivery date of heir "bob" changed: preparing a new will.' in rows, rows
    assert GENERIC not in rows


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
