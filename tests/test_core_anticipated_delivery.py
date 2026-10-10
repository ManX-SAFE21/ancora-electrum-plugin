"""The one rule for the delivery date after a rebuild (CHANGELOG #110).

``Will.anticipated_delivery_to_store`` is shared by the Check dialog, the
GUI's automatic rebuild and the command line (before, each had its own copy
and the automatic rebuild had none).

Run:
    python3 -m pytest tests/test_core_anticipated_delivery.py -q
"""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from bal.core.will import Will

DAY = 86400
STORED = 1_823_126_400  # an absolute delivery date


def _item(locktime, valid=True):
    """A will item as ``get_min_locktime`` reads it."""
    return SimpleNamespace(
        tx=SimpleNamespace(locktime=locktime),
        get_status=lambda name, v=valid: v if name == "VALID" else False,
    )


def test_anticipated_will_moves_an_absolute_date():
    """Rebuilt one day earlier -> store the earlier date."""
    will = {"a": _item(STORED - DAY), "b": _item(STORED)}
    assert Will.anticipated_delivery_to_store(will, STORED) == STORED - DAY
    assert Will.anticipated_delivery_to_store(will, float(STORED)) == STORED - DAY
    assert Will.anticipated_delivery_to_store(will, str(STORED)) == STORED - DAY


def test_same_or_later_date_is_kept():
    """Same date, or a later stored date (a real postponement): keep it."""
    assert Will.anticipated_delivery_to_store({"a": _item(STORED)}, STORED) is None
    assert Will.anticipated_delivery_to_store({"a": _item(STORED + DAY)}, STORED) is None


def test_relative_date_is_kept():
    """A relative date is resolved against the built will on every check."""
    will = {"a": _item(STORED - DAY)}
    assert Will.anticipated_delivery_to_store(will, "1y") is None
    assert Will.anticipated_delivery_to_store(will, "365D") is None


def test_no_valid_item_or_unreadable_date():
    """Nothing valid, or a stored date that cannot be read: keep it."""
    assert Will.anticipated_delivery_to_store({"a": _item(STORED - DAY, valid=False)}, STORED) is None
    assert Will.anticipated_delivery_to_store({}, STORED) is None
    assert Will.anticipated_delivery_to_store({"a": _item(STORED - DAY)}, None) is None
