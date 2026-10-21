"""``Will.normalize_will`` on a will with more delivery dates (CHANGELOG #177).

A later delivery spends the change of the delivery before it, which is
not signed yet. ``normalize_will`` checks every item on a copy; the copy
re-parsed the transaction and lost the data of that input (the previous
output's address, value and script), so Electrum could not compute the
copy's txid and every build logged five ERROR lines "txid is none" per
later delivery (the owner's log of 2026-10-21: 360 lines in one
session). Here, with ``tests/flowkit.py`` (three fake will-executors, each
with its own fee address; throw-away test wallet with the test password;
fixed clock):

* a will with three delivery dates is built, signed and sent with no
  such line, and so is a rebuild with one date moved earlier;
* every copy has the txid of its key;
* an input whose previous output is really missing still gives one
  WARNING line per item, no ERROR line.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_normalize_will_chain.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

import test_multiverse_chain_rebuild as chain
from test_multiverse_chain_rebuild import _edit_table, _first, _level, fk

from bal.core import will as will_mod
from bal.core.will import Will, WillItem

# The sessions of the chain tests (three will-executors, synchronized).
make = chain.make


def _record_log(monkeypatch):
    """Record the ERROR and WARNING lines of ``bal.core.will``.

    Returns ``{"error": [...], "warning": [...]}``, filled as they come.
    """
    lines = {"error": [], "warning": []}
    for level in lines:
        original = getattr(will_mod._logger, level)

        def rec(msg, *args, _level=level, _original=original, **kwargs):
            """Keep the message, then log it as before."""
            lines[_level].append(str(msg))
            return _original(msg, *args, **kwargs)
        monkeypatch.setattr(will_mod._logger, level, rec)
    return lines


def _txid_lines(lines):
    """The lines about a txid that cannot be computed (old and new text)."""
    return [m for level in lines.values() for m in level
            if "txid is none" in m or "cannot be computed" in m]


def test_three_dates_build_logs_no_txid_line(make, monkeypatch):
    """The owner's case: the first build of a will with three dates (3 +
    9 + 27 items) logs no "txid is none" and no "cannot be computed"."""
    s = make()
    lines = _record_log(monkeypatch)
    _first(s, "three_dates")
    assert _txid_lines(lines) == []
    assert not any(m == "##########" for m in lines["error"])


def test_date_moved_earlier_logs_no_txid_line(make, monkeypatch):
    """A rebuild: the first date 30 days earlier, a new chain of 39 items
    to build, sign and send; still no line."""
    s = make()
    _first(s, "three_dates")
    s.clock.travel(1)

    def change(rows):
        """The first delivery 30 days earlier."""
        rows[0]["locktime"] -= 30 * fk.DAY
    _edit_table(s, change)
    lines = _record_log(monkeypatch)
    s.check()
    assert _txid_lines(lines) == []


def test_every_copy_has_the_txid_of_its_key(make, monkeypatch):
    """At each ``normalize_will`` of a build (the will NOT signed yet), a
    copy of each item, completed as ``normalize_will`` completes it, has
    the txid of its key, also for the later deliveries."""
    s = make()
    seen = []
    original = Will.normalize_will

    def spy(will, wallet=None, others_inputs=None):
        """Copy the items as normalize_will does, then run it."""
        copies = {wid: WillItem(will[wid], wallet=wallet) for wid in will}
        for wid in copies:
            Will.add_info_from_will(copies, wid, wallet)
        seen.append({wid: c.tx.txid() for wid, c in copies.items()})
        return original(will, wallet, others_inputs)
    monkeypatch.setattr(Will, "normalize_will", staticmethod(spy))
    _first(s, "three_dates")
    assert seen
    for txids in seen:
        assert all(txid == wid for wid, txid in txids.items())
    assert max(_level(k, s.bw.willitems) for k in s.bw.willitems) == 3


def test_missing_previous_output_gives_one_warning(make, monkeypatch):
    """An unsigned later delivery whose previous delivery is gone: its txid
    really cannot be computed. One WARNING line per such item, with its
    key; no ERROR line."""
    s = make()
    unsigned = {}
    original = Will.normalize_will

    def grab(will, wallet=None, others_inputs=None):
        """Keep the first unsigned will a build normalizes."""
        if not unsigned:
            unsigned.update(
                {wid: WillItem(will[wid], wallet=wallet) for wid in will})
        return original(will, wallet, others_inputs)
    monkeypatch.setattr(Will, "normalize_will", staticmethod(grab))
    _first(s, "three_dates")
    monkeypatch.setattr(Will, "normalize_will", staticmethod(original))

    roots = [k for k in unsigned if _level(k, unsigned) == 1]
    children = {k for k in unsigned if _level(k, unsigned) == 2}
    broken = dict(unsigned)
    for root in roots:
        del broken[root]
    lines = _record_log(monkeypatch)
    Will.normalize_will(broken, s.wallet)
    assert lines["error"] == []
    warned = [m for m in lines["warning"] if "cannot be computed" in m]
    assert len(warned) == len(children) == 9
    assert all(any(k in m for k in children) for m in warned)
