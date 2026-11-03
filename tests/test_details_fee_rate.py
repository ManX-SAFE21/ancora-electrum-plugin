"""The Details window shows the miner fee each copy really pays (#223).

The "Transaction fees" row of the Details window (``WillWidget``) read the
amount from ``tx.input_value()``: a signed copy, and a later delivery read
back from the wallet file, have no amounts on their inputs, so it showed
"-1 (-0.006 sats/vbyte)". For a multisig it also divided by Electrum's
single-key guess of the size (184 vB against 220 for a 2-of-3): 15.5 sat/vB
for a chosen 13. The fee paid was right; only the window was wrong.

Now the amounts come from the outputs the copy spends (the copy before it,
or the wallet's history) and the size from the wallet's real scripts, also
for an input that spends the change of the copy before. The code that
builds the will (``Util.real_tx_size``) is not changed (owner).

Test wallets: throw-away testnet keys made at every run (the wallets of
``tests/test_engine_merge.py``), three fake will-executors
(``tests/flowkit.py``), three deliveries. Nothing reaches the network.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_details_fee_rate.py
"""

import os
import re
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

from PyQt6.QtWidgets import QLabel
from test_engine_merge import _valid, world  # noqa: F401 - the fixture
from test_multisig_fee_size import RATE, _fee, _table

from bal.gui.qt.widgets import WillWidget, copy_fee, fee_text

#: "2860 (13.0 sats/vbyte)"
_TEXT = re.compile(r"^(\d+) \(([\d.]+) sats/vbyte\)$")


def _rate(text):
    """The fee and the rate read from a "Transaction fees" value."""
    m = _TEXT.match(text)
    assert m, text
    return int(m.group(1)), float(m.group(2))


def _deliveries(items):
    """The distinct delivery dates of *items*, earliest first."""
    return sorted({w.tx.locktime for w in items.values()})


@pytest.mark.parametrize("kind", ["keys", "msA"])
@pytest.mark.parametrize("reload", [False, True])
def test_every_delivery_shows_the_chosen_rate(world, kind, reload):  # noqa: F811
    """Single key and 2-of-3, in memory and after reopening: every copy of
    the three deliveries shows the fee it pays and the chosen rate."""
    s = world(kind)
    _table(s)
    s.check()
    if reload:
        s.reload_window()
    items = _valid(s)
    assert len(_deliveries(items)) == 3
    for w in items.values():
        fee, rate = _rate(fee_text(w.tx, items, s.wallet))
        assert fee == _fee(items, s.wallet, w.tx)
        # A signature one byte shorter than the estimate makes 13.08.
        assert RATE <= rate < RATE + 0.1, (w.tx.locktime, rate)


def test_unknown_amount_shows_a_question_mark(world):  # noqa: F811
    """A copy whose spent output is nowhere: "?", never "-1"."""
    s = world("keys")
    _table(s)
    s.check()
    items = _valid(s)
    later = [w for w in items.values() if w.tx.locktime != min(_deliveries(items))]
    lonely = {"x": later[0]}
    assert copy_fee(later[0].tx, lonely, None) is None
    assert fee_text(later[0].tx, lonely, None) == "? (? sats/vbyte)"


def test_the_window_shows_it(world):  # noqa: F811
    """The Details window itself: no "-1", the chosen rate on every row."""
    s = world("msA")
    _table(s)
    s.check()
    s.reload_window()
    items = _valid(s)
    # What WillWidget reads of WillDetailDialog.
    parent = SimpleNamespace(bal_window=s.bw, decimal_point=8, base_unit_name="BTC")
    widget = WillWidget(parent=parent, will=items)
    texts = [label.text() for label in widget.findChildren(QLabel)]
    fees = [t.split("\t", 1)[1] for t in texts if t.startswith("<b>Transaction fees:</b>")]
    assert len(fees) == len(items)
    for text in fees:
        _fee_sat, rate = _rate(text)
        assert RATE <= rate < RATE + 0.1, text
