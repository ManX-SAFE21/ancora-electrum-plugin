"""
Tests for the relative-recipe anchoring in the will coherence check.

Regression for the reported bug: a wallet built with RELATIVE locktimes
(``"1y"`` on the heirs, relative will_settings) was asked to invalidate the
will EVERY DAY.  The relative recipes were re-parsed against *now* on every
check, so they drifted one day per day away from the fixed locktime frozen
inside the signed transaction and the check mistook the (unchanged) will for a
POSTPONE / EXPIRED one.

The two gates that produced the prompt are covered here:

  1. ``Will.check_willexecutors_and_heirs`` must treat an UNCHANGED relative
     recipe as coherent (resolved against the build moment, not "now"), while
     still detecting a genuinely lengthened recipe as a postpone.
  2. ``resolve_date_to_check`` (ADVANCED mode) must anchor a relative stored
     locktime to the built transactions' frozen delivery date, so the will is
     never read as EXPIRED because the check window drifts past the frozen
     tx locktime.

The reported state (reproduced hermetically here — the original live wallet
dump ``tests/karen7`` is gitignored and regenerated as the wallet evolves) is:
heirs with ``"1y"``, a signed/pushed/checked item whose frozen tx.locktime is
2027-08-05 (built 2026-08-05), and will_settings
``{"locktime": "2y", "threshold": "150d"}``.

Run:
    source "$BAL_HOME/electrum/env/bin/activate"
    python3 tests/test_heir_relative_anchor.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

import pytest  # noqa: E402  (path insert above)
from electrum import constants  # noqa: E402  (path insert above)

from bal.core.checkalive import resolve_date_to_check  # noqa: E402
from bal.core.util import copy_structure  # noqa: E402
from bal.core.will import (  # noqa: E402
    HeirNotFoundException,
    NoHeirsException,
    NotCompleteWillException,
    Will,
    WillItem,
    WillPostponedException,
)


@pytest.fixture(autouse=True)
def _regtest_net():
    """Run these regtest-focused tests with BitcoinRegtest, restoring mainnet
    afterwards so sibling test modules are unaffected by the net switch."""
    constants.net = constants.BitcoinRegtest
    yield
    constants.net = constants.BitcoinMainnet


# A valid serialized tx (1 input + 1 output, version 2). Its nLockTime is 0;
# the tests override ``tx.locktime`` to simulate the frozen signed locktime.
_VALID_TX_HEX = (
    "01000000012a5c9a94fcde98f5581cd00162c60a13936ceb75389ea65b"
    "f38633b424eb4031000000006c493046022100a82bbc57a0136751e543"
    "3f41cf000b3f1a99c6744775e76ec764fb78c54ee100022100f9e80b7d"
    "e89de861dc6fb0c1429d5da72c2b6b2ee2406bc9bfb1beedd729d98501"
    "2102e61d176da16edd1d258a200ad9759ef63adf8e14cd97f53227bae3"
    "5cdb84d2f6ffffffff0140420f00000000001976a914230ac37834073a"
    "42146f11ef8414ae929feaafc388ac00000000"
)

# The frozen tx.locktime of karen7's valid item: delivery 2027-08-05 00:00 UTC,
# i.e. a will built 2026-08-05 with a "1y" recipe.
_FROZEN = 1817424000


def _make_will_item(heirs, tx_locktime, status_complete=False):
    """Build a WillItem whose stored heirs == ``heirs`` and whose tx.locktime
    is forced to ``tx_locktime`` (the value frozen in the signed Bitcoin tx)."""
    d = {
        "tx": _VALID_TX_HEX,
        "heirs": copy_structure(heirs),
        "willexecutor": None,
        "status": "",
        "description": "",
        "time": 0,
        "change": "",
        "baltx_fees": 1,
    }
    item = WillItem(d, _id="willid_1")
    item.STATUS = WillItem.copy_status_table(WillItem.STATUS_DEFAULT)
    item.tx.locktime = tx_locktime
    if status_complete:
        item.set_status("COMPLETE", True)
    return item


def _run_heir_check(will_heirs, current_heirs, tx_locktime, status_complete):
    """Run ``check_willexecutors_and_heirs`` and return the outcome."""
    item = _make_will_item(will_heirs, tx_locktime, status_complete)
    will = {"willid_1": item}
    try:
        result = Will.check_willexecutors_and_heirs(
            will, current_heirs, {}, False, 0, 1
        )
        return f"coherent ({result})"
    except WillPostponedException as e:
        return f"POSTPONE: {e}"
    except HeirNotFoundException as e:
        return f"rebuild: {e}"
    except NoHeirsException as e:
        return f"NoHeirs: {e}"
    except NotCompleteWillException as e:
        return f"NotComplete: {e}"


def test_unchanged_relative_recipe_signed_is_coherent():
    """The reported bug: an unchanged "1y" recipe on a signed will must NOT be
    read as a postpone just because the clock has advanced past build day."""
    heirs = {"alice": ["addr_alice", 5000, "1y"]}
    outcome = _run_heir_check(
        copy_structure(heirs), copy_structure(heirs), _FROZEN, status_complete=True
    )
    assert outcome.startswith("coherent"), outcome


def test_unchanged_relative_recipe_unsigned_is_coherent():
    heirs = {"alice": ["addr_alice", 5000, "1y"]}
    outcome = _run_heir_check(
        copy_structure(heirs), copy_structure(heirs), _FROZEN, status_complete=False
    )
    assert outcome.startswith("coherent"), outcome


def test_relative_recipe_lengthened_on_signed_is_postpone():
    """A genuinely lengthened recipe ("1y" -> "2y") on a signed/sent will is
    still detected as a postpone (must invalidate on-chain first)."""
    built = {"alice": ["addr_alice", 5000, "1y"]}
    now = {"alice": ["addr_alice", 5000, "2y"]}
    outcome = _run_heir_check(built, now, _FROZEN, status_complete=True)
    assert outcome.startswith("POSTPONE"), outcome


def test_relative_recipe_shortened_on_signed_is_rebuild():
    """A shortened recipe ("1y" -> "30d") is an ANTICIPATE: plain rebuild, no
    on-chain invalidation."""
    built = {"alice": ["addr_alice", 5000, "1y"]}
    now = {"alice": ["addr_alice", 5000, "30d"]}
    outcome = _run_heir_check(built, now, _FROZEN, status_complete=True)
    assert outcome.startswith("rebuild"), outcome


def test_unchanged_absolute_recipe_is_coherent():
    built = {"alice": ["addr_alice", 5000, str(_FROZEN)]}
    outcome = _run_heir_check(
        copy_structure(built), copy_structure(built), _FROZEN, status_complete=True
    )
    assert outcome.startswith("coherent"), outcome


def test_absolute_postpone_on_signed_still_detected():
    built = {"alice": ["addr_alice", 5000, str(_FROZEN)]}
    now = {"alice": ["addr_alice", 5000, str(_FROZEN + 86400)]}
    outcome = _run_heir_check(built, now, _FROZEN, status_complete=True)
    assert outcome.startswith("POSTPONE"), outcome


# ------------------------------------------------------------------ #
# karen7 regression (hermetic, no live wallet fixture)
# ------------------------------------------------------------------ #

# karen7's reported state, reproduced hermetically: heirs "1y", a signed item
# frozen at delivery 2027-08-05 (built 2026-08-05), will_settings with a
# relative "150d" delivery window and a "2y" promised locktime.
_WILL_SETTINGS = {"locktime": "2y", "threshold": "150d"}


def test_karen7_frozen_delivery_not_expired():
    """ADVANCED date_to_check anchored to the frozen tx locktime: the check
    window opens BEFORE the delivery, so the will is never read as expired."""
    heirs = {"alice": ["addr_alice", 5000, "1y"]}
    item = _make_will_item(copy_structure(heirs), _FROZEN, status_complete=True)
    will = {"willid_1": item}
    built_locktime = Will.get_min_locktime(will)
    assert built_locktime is not None
    assert built_locktime == int(item.tx.locktime)

    date_to_check = resolve_date_to_check(
        False, _WILL_SETTINGS, now=1_800_000_000.0, built_locktime=built_locktime
    )
    assert int(date_to_check) < built_locktime
    # Re-evaluated 10 days later the window is identical (no daily drift).
    later = resolve_date_to_check(
        False, _WILL_SETTINGS, now=1_800_000_000.0 + 10 * 86400,
        built_locktime=built_locktime,
    )
    assert date_to_check == later


def test_karen7_unchanged_heirs_are_coherent():
    """Unchanged relative "1y" heirs are coherent with the frozen signed tx:
    the plugin must NOT ask to invalidate the will."""
    heirs = {"alice": ["addr_alice", 5000, "1y"]}
    # Use _FROZEN (a UTC-midnight value) so the check is compatible with
    # the UTC anchoring code.
    frozen_locktime = _FROZEN
    date_to_check = resolve_date_to_check(
        False, _WILL_SETTINGS,
        now=1_800_000_000.0,
        built_locktime=frozen_locktime,
    )
    outcome = _run_heir_check(
        copy_structure(heirs),
        copy_structure(heirs),
        frozen_locktime,
        status_complete=True,
    )
    assert outcome.startswith("coherent"), outcome
    assert int(date_to_check) < frozen_locktime


# ------------------------------------------------------------------ #
# Main
# ------------------------------------------------------------------ #

if __name__ == "__main__":
    constants.net = constants.BitcoinRegtest
    for name in sorted(dir()):
        if name.startswith("test_"):
            globals()[name]()
            print(f"  [OK] {name}")
    print("[OK] All heir-relative-anchor tests passed")
