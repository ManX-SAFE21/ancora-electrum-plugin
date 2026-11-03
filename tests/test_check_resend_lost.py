"""A will-executor that lost the will gets it again in the same Check (#225).

Before, the verification saw it ("Verifying: Ko", "In force, but 1
will-executor did not receive it"), but the copy was sent again only at
the NEXT Check, and the last row said "All done: Ok". Probed on test wallets
(owner: "fai test approfonditi"): one, two or all three will-executors
losing the will, one holding another transaction, three deliveries, the
Check at wallet close. Now:

* the copies the verification finds missing are sent again at once, to
  those will-executors only, and verified again (once: no loop);
* still not held after that (the resend refused, or taken and not kept):
  "Not finished: Will not held by every will-executor", or "Will not sent
  to every will-executor" when the resend failed;
* "Not sent" / "The will was not sent to the will-executors" only when no
  will-executor of the will holds it: before, a failed resend to the one
  that lost it said so while the other two held it.

Test wallets: throw-away testnet keys made at every run (the wallets of
``tests/test_engine_merge.py``), with and without password, single key and
multisig 2-of-3; three fake will-executors (``tests/flowkit.py``). Nothing
reaches the network.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_check_resend_lost.py
"""

import os
import re
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

from test_engine_merge import (  # noqa: F401 - world is the fixture
    _export,
    _merge,
    _sign_offline,
    world,
)
from test_multisig_fee_size import _table

URLS = ["https://we0.test", "https://we1.test", "https://we2.test"]
SEND_ROW = "Broadcasting your will to executors"
VERIFY_ROW = "Verifying your will with the executors"
NOT_HELD = "Will not held by every will-executor"
NOT_SENT_ALL = "Will not sent to every will-executor"


def _rows(s):
    """The last Check's log, as plain text (no colours)."""
    return [re.sub("<[^>]+>", "", str(line)) for line in s.last.history]


def _last(rows, label):
    """The last value written on the row *label* ("label:\\tvalue")."""
    values = [r.split("\t", 1)[1] for r in rows if r.startswith(label + ":\t")]
    return values[-1] if values else None


def _world(s):
    """The fake world in use: every session installs its own when it opens,
    so it is the one of the session opened last (see
    ``test_engine_merge._pushed_complete``)."""
    return s.others[-1].world


def _lose(s, urls):
    """The will-executors *urls* lose every transaction they held."""
    _world(s).pushed = [p for p in _world(s).pushed if p[0] not in urls]


def _held(s):
    """Per will-executor: does it hold every one of its copies in force?"""
    out = {}
    for w in s.bw.willitems.values():
        if not (w.get_status("VALID") and w.we):
            continue
        url = w.we["url"]
        have = any(u == url and wid == w._id and raw == str(w.tx)
                   for u, wid, raw in _world(s).pushed)
        out[url] = out.get(url, True) and have
    return out


def _headline(s):
    """The first line of the Check window's result sentence."""
    kind, text = s.last._report().snapshot()["result"]
    return kind, re.sub("<[^>]+>", "", text or "")


def _ok_and_held(s):
    """Every will-executor holds the will; the Check says so."""
    rows = _rows(s)
    assert _held(s) == {u: True for u in URLS}
    assert _last(rows, VERIFY_ROW) == "Ok"
    assert _last(rows, "All done") == "Ok"
    assert _last(rows, "Not finished") is None
    assert _headline(s)[1].startswith("Your will is in force")


@pytest.mark.parametrize("lost", [URLS[1:2], URLS[:2], URLS])
def test_lost_will_is_sent_again_in_the_same_check(world, lost):  # noqa: F811
    """One, two or all three lose it (password): sent again at once."""
    s = world("keys")
    s.check()
    _lose(s, lost)
    before = len(s.world.pushed)
    s.check()
    _ok_and_held(s)
    assert {u for u, _w, _r in s.world.pushed[before:]} == set(lost)
    assert "Not held by {}: sent again".format(
        ", ".join(u[8:] for u in lost)) in _rows(s)
    # Asked again only the ones sent again (the others were just verified).
    asked = [u for u, _wid in s.world.verified]
    assert {u: asked.count(u) for u in URLS} == {
        u: 2 if u in lost else 1 for u in URLS}


def test_other_transaction_is_replaced(world):  # noqa: F811
    """One holds another transaction (an old copy): sent again."""
    s = world("keys")
    s.check()
    s.world.pushed = [(u, w, "00" + r[2:]) if u == URLS[1] else (u, w, r)
                      for u, w, r in s.world.pushed]
    s.check()
    _ok_and_held(s)


def test_several_deliveries_next_day(world):  # noqa: F811
    """Three deliveries: verified once a day (#182); the day after, the
    missing copies are found and sent again in that Check."""
    s = world("keys")
    _table(s)
    s.check()
    _lose(s, [URLS[1]])
    s.clock.travel(1)
    s.check()
    _ok_and_held(s)


def test_at_wallet_close(world):  # noqa: F811
    """The Check at wallet close sends it again too."""
    s = world("keys")
    s.check()
    _lose(s, [URLS[1]])
    s.close_wallet()
    s.last = s.bw.opened_dialogs[-1]
    rows = _rows(s)
    assert _last(rows, "All done") == "Ok"
    assert URLS[1] in {u for u, _w, _r in s.world.pushed}


def test_watch_only_no_password(world):  # noqa: F811
    """Watch-only online, signed offline: no key is needed to send again."""
    folder = tempfile.mkdtemp(prefix="bal_225_")
    online = world("watch")
    online.check()
    will_file = _export(online, folder, "will.json")
    signed = _sign_offline(world("keys"), will_file, folder, "signed.json")
    _merge(online, signed)
    online.check()
    _lose(online, [URLS[0]])
    online.check()
    _ok_and_held(online)


def test_multisig(world):  # noqa: F811
    """2-of-3 watch-only, A and B offline: sent again after a loss."""
    folder = tempfile.mkdtemp(prefix="bal_225_")
    online = world("msWatch")
    online.check()
    will_file = _export(online, folder, "will.json")
    file_a = _sign_offline(world("msA"), will_file, folder, "will_A.json")
    file_ab = _sign_offline(world("msB"), file_a, folder, "will_AB.json")
    _merge(online, file_ab)
    online.check()
    _lose(online, [URLS[2]])
    online.check()
    _ok_and_held(online)


def test_resend_refused_says_not_sent_to_every(world):  # noqa: F811
    """The one that lost it refuses the resend; the other two hold it:
    orange "not sent to every", never "not sent to the will-executors"."""
    s = world("keys")
    s.check()
    _lose(s, [URLS[1]])
    s.world.refused.add(URLS[1])
    s.check()
    rows = _rows(s)
    assert _last(rows, SEND_ROW) == "Not sent to every will-executor"
    # Not sent again, not asked again: the verification stays "Ko".
    assert _last(rows, VERIFY_ROW) == "Ko"
    assert _last(rows, "Not finished") == NOT_SENT_ALL
    assert _last(rows, "All done") is None
    kind, text = _headline(s)
    assert "was not sent" not in text
    assert text.startswith("In force, but 1 will-executor did not receive it")


def test_none_holds_it_and_all_refuse(world):  # noqa: F811
    """All three lost it and refuse it: then "Not sent" is true."""
    s = world("keys")
    s.check()
    _lose(s, URLS)
    s.world.refused.update(URLS)
    s.check()
    rows = _rows(s)
    assert _last(rows, SEND_ROW) == "Not sent"
    assert _last(rows, "Not finished") == "Will not sent to the will-executors"


def test_taken_but_not_kept_no_loop(world, monkeypatch):  # noqa: F811
    """It says "received" and forgets again: one resend, then "Not finished:
    Will not held by every will-executor" (no loop)."""
    s = world("keys")
    s.check()
    _lose(s, [URLS[1]])
    real_push = s.world.push_transactions_parallel
    resends = []

    def forgetful(willexecutors, **kw):
        """we1 answers "received" but keeps nothing."""
        resends.append(sorted(willexecutors))
        real_push(willexecutors, **kw)
        _lose(s, [URLS[1]])

    from bal.core.willexecutors import Willexecutors

    monkeypatch.setattr(Willexecutors, "push_transactions_parallel",
                        staticmethod(forgetful))
    s.check()
    rows = _rows(s)
    assert resends == [[URLS[1]]]
    assert _last(rows, VERIFY_ROW) == "Ko"
    assert _last(rows, "Not finished") == NOT_HELD
    assert _last(rows, "All done") is None


def test_no_answer_to_the_verification_is_left_as_before(world):  # noqa: F811
    """#147 (owner): no answer to the verification is not "not held":
    nothing is sent again, "All done: Ok" as before."""
    s = world("keys")
    s.check()
    s.world.down.add(URLS[1])
    before = len(s.world.pushed)
    s.check()
    rows = _rows(s)
    assert len(s.world.pushed) == before
    assert _last(rows, VERIFY_ROW) == "Timeout - no answer"
    assert _last(rows, "All done") == "Ok"
