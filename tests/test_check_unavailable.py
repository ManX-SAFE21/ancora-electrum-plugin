"""A 503 to the check is "no answer", not a failure (CHECK_FAIL) (#180).

Owner's ZIP test of #178 (2026-10-22): we.divinaly.support accepted its
copies ("pushed: thx"), then answered "503 Service Temporarily Unavailable"
to the check (searchtx) of 25 of them. Taken for a failure, they lost PUSHED
and were sent again at every Check. Owner's choice B: a 503 is NO_ANSWER at
once, with no new try, like a 429 still busy (#172); only the 503 (owner),
the other errors stay failures.

``tests/flowkit.py`` wallets with three fake will-executors, each with its
own fee address, and the will with three delivery dates of
``test_multiverse_chain_rebuild.py``; the real ``check_transaction`` runs on
a fake ``send_request``. No wallet file, no network.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_check_unavailable.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

import test_multiverse_chain_rebuild as chain

from bal.core import willexecutors as we_mod
from bal.core.willexecutors import Willexecutors

# The real one: flowkit replaces it by the world's fake in each session.
REAL_CHECK = Willexecutors.check_transaction

# The sessions of the chain tests (three will-executors, synchronized).
make = chain.make

DOWN = "https://we2.test"


class _HttpError(Exception):
    """Like aiohttp's ``ClientResponseError`` (status, headers)."""

    def __init__(self, status):
        super().__init__("{}, message='Service Temporarily Unavailable'".format(status))
        self.status = status
        self.headers = {}


def _send(answers, calls):
    """A fake ``send_request`` giving ``answers`` in turn."""
    answers = list(answers)

    def fake(method, url, data=None, **kw):
        """One request: the next answer, raised when it is an error."""
        calls.append(url)
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer
    return fake


def test_a_503_is_no_answer_at_once(monkeypatch):
    """No new try, no pause, no "busy" notice: NO_ANSWER."""
    calls, pauses, busy = [], [], []
    monkeypatch.setattr(Willexecutors, "send_request", staticmethod(
        _send([_HttpError(503), {"tx": "the tx"}], calls)))
    monkeypatch.setattr(we_mod.time, "sleep", pauses.append)
    res = REAL_CHECK("abc", DOWN, on_busy=lambda url, s: busy.append(url))
    assert res is Willexecutors.NO_ANSWER
    assert calls == [DOWN + "/searchtx"]
    assert pauses == [] and busy == []


def test_another_http_error_is_raised_as_before(monkeypatch):
    """Only the 503 (owner): a 500 is still an error (CHECK_FAIL)."""
    calls = []
    monkeypatch.setattr(Willexecutors, "send_request", staticmethod(
        _send([_HttpError(500)], calls)))
    try:
        REAL_CHECK("abc", DOWN)
    except _HttpError as e:
        assert e.status == 500
    else:
        raise AssertionError("the error was not raised")
    assert len(calls) == 1


def _down_answers_503(s, monkeypatch):
    """The real check on the world: ``DOWN`` answers 503 to every searchtx,
    the others give what they hold."""
    def send(method, url, data=None, **kw):
        """A searchtx: 503 from ``DOWN``, else the copy the world holds."""
        assert url.endswith("/searchtx"), url
        base = url[:-len("/searchtx")]
        if base == DOWN:
            raise _HttpError(503)
        txid = data.decode("ascii")
        for u, wid, raw in reversed(s.world.pushed):
            if u == base and wid == txid:
                return {"tx": raw}
        return None
    monkeypatch.setattr(Willexecutors, "send_request", staticmethod(send))
    s.world.check_transaction = lambda txid, url, **kw: REAL_CHECK(txid, url)


def _copies(s):
    """The VALID copies of ``DOWN``, by id."""
    return {k: w for k, w in s.bw.willitems.items()
            if w.get_status("VALID") and (w.we or {}).get("url") == DOWN}


def test_the_copies_of_a_will_executor_answering_503_stay_sent(make, monkeypatch):
    """A will in three deliveries: ``DOWN`` accepts its copies, then
    answers 503 to the check. They stay sent (no CHECK_FAIL), the summary
    reads "received, no answer to the check", and the next Check asks
    again without sending them again; the others are verified."""
    s = make()
    _down_answers_503(s, monkeypatch)
    chain.three_dates(s)
    s.check()
    copies = _copies(s)
    assert len(copies) == 13
    for w in copies.values():
        assert w.get_status("PUSHED") and not w.get_status("CHECK_FAIL")
        assert not w.get_status("CHECKED")
    others = [w for w in s.bw.willitems.values()
              if w.get_status("VALID") and w.we and w.we.get("url") != DOWN]
    assert others and all(w.get_status("CHECKED") for w in others)
    pushed = len(s.world.pushed)
    snap = s.check()._report().snapshot()
    assert dict(snap["executors"])[DOWN][0] == "no_check_answer"
    assert not [u for u, _w, _r in s.world.pushed[pushed:] if u == DOWN]
    assert sorted(_copies(s)) == sorted(copies)
