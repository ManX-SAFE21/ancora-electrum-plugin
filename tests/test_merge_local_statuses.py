"""A merged file never says what only this computer knows (#224).

"Merge" took from the file the statuses sent / verified / in the mempool /
confirmed (#158 rule). A file saying "sent" or "verified" (an old file, a
damaged one, one altered by a hacked computer) made the first Check send
nothing and end "All done: Ok": the will-executors got the signed will only
at the second Check. A file saying "in the mempool" or "confirmed" made the
copies not valid: the Check rebuilt them and the signatures just brought
back were lost ("Will still to sign"). Now these statuses never come from
the file: the Check finds them (it sends, it asks the will-executors, it
reads the chain), and the copies already sent here keep theirs.

Test wallets: throw-away testnet keys made at every run (the wallets of
``tests/test_engine_merge.py``), with and without password, single key and
multisig 2-of-3; three fake will-executors (``tests/flowkit.py``). Nothing
reaches the network.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_merge_local_statuses.py
"""

import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

from test_engine_merge import (  # noqa: F401 - world is the fixture
    _clear_pushes,
    _export,
    _merge,
    _pushed_complete,
    _sign_offline,
    _valid,
    world,
)

#: What a file may falsely say ("checked_time": year 2100, #182).
CLAIMS = {
    "sent": {"PUSHED": True},
    "verified": {"PUSHED": True, "CHECKED": True, "checked_time": 4102444800},
    "mempool": {"MEMPOOL": True},
    "confirmed": {"CONFIRMED": True},
}
LOCAL = ("PUSHED", "CHECKED", "MEMPOOL", "CONFIRMED")
SIGNED_3 = "Merge done: 3 transaction(s) signed, 0 still to sign."


def _claim(path, claim):
    """Rewrite the will file at *path* so every copy says *claim*."""
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    for item in raw.values():
        item.update(CLAIMS[claim])
    with open(path, "w", encoding="utf-8") as f:
        json.dump(raw, f)
    return path


def _none_local(items):
    """True when no copy of *items* is sent / verified / seen on the chain."""
    return not any(w.get_status(s) for w in items.values() for s in LOCAL)


def _rows(s):
    """The last Check's log, as plain "label:\\tvalue" text."""
    import re

    return [re.sub("<[^>]+>", "", str(line)) for line in s.last.history]


@pytest.mark.parametrize("claim", list(CLAIMS))
def test_watch_only_first_check_sends_the_signed_will(world, claim):  # noqa: F811
    """Watch-only online, keys offline (password): whatever the file says,
    the merge keeps the signatures and the FIRST Check sends three signed
    copies, "All done: Ok"."""
    folder = tempfile.mkdtemp(prefix="bal_224_")
    online = world("watch")
    online.check()
    will_file = _export(online, folder, "will.json")
    offline = world("keys")
    signed = _claim(_sign_offline(offline, will_file, folder, "signed.json"), claim)
    assert _merge(online, signed) == SIGNED_3
    items = _valid(online)
    assert len(items) == 3 and _none_local(items)
    assert all(w.checked_time is None for w in items.values())
    _clear_pushes(online)
    online.check()
    assert _pushed_complete(online) == [True, True, True]
    assert "All done:\tOk" in _rows(online)


@pytest.mark.parametrize("claim", ["verified", "confirmed"])
def test_multisig_watch_only_first_check_sends(world, claim):  # noqa: F811
    """Watch-only 2-of-3 online, A and B sign offline, the file says
    *claim*: signed, sent at the first Check."""
    folder = tempfile.mkdtemp(prefix="bal_224_")
    online = world("msWatch")
    online.check()
    will_file = _export(online, folder, "will.json")
    file_a = _sign_offline(world("msA"), will_file, folder, "will_A.json")
    file_ab = _claim(_sign_offline(world("msB"), file_a, folder, "will_AB.json"), claim)
    assert _merge(online, file_ab) == SIGNED_3
    assert _none_local(_valid(online))
    _clear_pushes(online)
    online.check()
    assert _pushed_complete(online) == [True, True, True]


@pytest.mark.parametrize("claim", ["verified", "mempool"])
def test_new_copies_are_sent(world, claim):  # noqa: F811
    """Copies new to this wallet (empty will, password): the file's claim is
    dropped, the Check sends them."""
    folder = tempfile.mkdtemp(prefix="bal_224_")
    first = world("keys")
    first.check()
    path = _claim(_export(first, folder, "will.json"), claim)
    fresh = world("keys")
    assert not fresh.bw.willitems
    _merge(fresh, path)
    assert fresh.bw.willitems and _none_local(fresh.bw.willitems)
    assert all(w.checked_time is None for w in fresh.bw.willitems.values())
    _clear_pushes(fresh)
    fresh.check()
    assert _pushed_complete(fresh) == [True, True, True]
    assert "All done:\tOk" in _rows(fresh)


def test_copies_sent_here_keep_their_statuses(world):  # noqa: F811
    """A file that says nothing does not take away "sent / verified" from
    the copies this computer has sent (password)."""
    folder = tempfile.mkdtemp(prefix="bal_224_")
    s = world("keys")
    s.check()
    assert all(w.get_status("PUSHED") and w.get_status("CHECKED")
               for w in _valid(s).values())
    path = _export(s, folder, "will.json")
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    for item in raw.values():
        item.update({k: False for k in LOCAL})
    with open(path, "w", encoding="utf-8") as f:
        json.dump(raw, f)
    _merge(s, path)
    assert all(w.get_status("PUSHED") and w.get_status("CHECKED")
               for w in _valid(s).values())


def test_cli_merge_drops_the_claim(world):  # noqa: F811
    """``bal_will_merge``: the same rule on the command line."""
    folder = tempfile.mkdtemp(prefix="bal_224_")
    online = world("watch")
    online.check()
    online.bw.save_willitems()
    will_file = _export(online, folder, "will.json")
    signed = _claim(_sign_offline(world("keys"), will_file, folder, "signed.json"),
                    "verified")
    online.activate()
    out = online.cli("merge_will_from_file", signed)["returned"]
    assert out == {"merged": 3, "signed": 3, "partly_signed": 0, "unsigned": 0}
    assert _none_local(_valid(online))
