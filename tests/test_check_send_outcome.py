"""The Check says "Ok" for the sending only when the will reached them all (#222).

Audit 2026-10-31, item 20: after the sending, the row "Broadcasting your
will to executors" always said "Ok", also when no will-executor received
the will, and the last row said "All done: Ok" because it looked only at
the signing. Now:

* every will-executor received it: "Ok" and "All done: Ok", as before;
* some did not (no answer, or an error): "Not sent to every will-executor";
* none did: "Not sent";
* in the last two cases the last row is "Not finished: Will not sent to
  every will-executor", or "Will not sent to the will-executors" when none
  received it (owner: "to every" read as if some had).

The rule is the one of Tools > Send (``bal.engine.push.not_delivered``,
#213).

Test wallets: throw-away testnet keys made at every run (the wallets of
``tests/test_engine_merge.py``), three fake will-executors
(``tests/flowkit.py``). Nothing reaches the network.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_check_send_outcome.py
"""

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

from test_engine_merge import world  # noqa: F401 - the fixture

SEND_ROW = "Broadcasting your will to executors"
NOT_ALL = "Not sent to every will-executor"
NONE = "Not sent"
NOT_FINISHED = "Will not sent to every will-executor"
NOT_FINISHED_NONE = "Will not sent to the will-executors"
URLS = ["https://we0.test", "https://we1.test", "https://we2.test"]


def _rows(s):
    """The last rows of the Check's log, as plain text (no colours)."""
    return [re.sub("<[^>]+>", "", str(line)) for line in s.last.history]


def _last(rows, label):
    """The last value written on the row *label* ("label:\\tvalue")."""
    values = [r.split("\t", 1)[1] for r in rows if r.startswith(label + ":\t")]
    return values[-1] if values else None


def test_all_received_is_ok(world):  # noqa: F811
    """The three will-executors receive the will: "Ok" twice, as before."""
    s = world("keys")
    s.check()
    rows = _rows(s)
    assert _last(rows, SEND_ROW) == "Ok"
    assert _last(rows, "All done") == "Ok"
    assert _last(rows, "Not finished") is None
    assert {url for url, _wid, _raw in s.world.pushed} == set(URLS)


def test_one_no_answer_is_not_ok(world):  # noqa: F811
    """One of three does not answer: orange, and the Check is not finished."""
    s = world("keys")
    s.world.down.add(URLS[1])
    s.check()
    rows = _rows(s)
    assert _last(rows, SEND_ROW) == NOT_ALL
    assert _last(rows, "Not finished") == NOT_FINISHED
    assert _last(rows, "All done") is None


def test_one_refused_is_not_ok(world):  # noqa: F811
    """One of three answers with an error: the same as no answer."""
    s = world("keys")
    s.world.refused.add(URLS[2])
    s.check()
    rows = _rows(s)
    assert _last(rows, SEND_ROW) == NOT_ALL
    assert _last(rows, "Not finished") == NOT_FINISHED
    assert _last(rows, "All done") is None
    assert URLS[2] not in {url for url, _wid, _raw in s.world.pushed}


def test_none_received_says_not_sent(world):  # noqa: F811
    """No will-executor gets the will (two silent, one refusing): "Not sent"."""
    s = world("keys")
    s.world.down.update(URLS[:2])
    s.world.refused.add(URLS[2])
    s.check()
    rows = _rows(s)
    assert _last(rows, SEND_ROW) == NONE
    assert _last(rows, "Not finished") == NOT_FINISHED_NONE
    assert _last(rows, "All done") is None
    assert s.world.pushed == []


def test_back_online_is_ok_again(world):  # noqa: F811
    """The silent one answers at the next Check: its copy is sent, "Ok"."""
    s = world("keys")
    s.world.down.add(URLS[0])
    s.check()
    assert _last(_rows(s), SEND_ROW) == NOT_ALL
    s.world.down.clear()
    s.check()
    rows = _rows(s)
    assert _last(rows, "Not finished") is None
    assert _last(rows, "All done") == "Ok"
    assert URLS[0] in {url for url, _wid, _raw in s.world.pushed}
