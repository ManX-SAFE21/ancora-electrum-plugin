"""The engine's verification brick: do the will-executors hold the will? (#156)

Before, asking the will-executors for their copies was written three times:

* the Check's last step "Verification": ``BalBuildWillDialog.loop_verify``;
* the will list's right click > "check": ``BalWindow.check_transactions_task``;
* the command line's ``bal_will_check``:
  ``BalController.check_transactions``.

The core was the same (ask every will-executor at once, with the global
deadline ``CHECK_GLOBAL_DEADLINE``, record each answer with
``WillItem.set_check_willexecutor``, no answer in time leaves the copy sent,
#147), but two rules differed. Owner decisions 2026-10-16:

* E1, which copies are asked: only copies that were sent. The Check and
  the command line ask the VALID ones not verified yet
  (``Will.needs_server_check``); the right click (``recheck``) asks every
  selected copy sent at least once: the one in force while a new will is
  only prepared (marked replaced, still held by the will-executors), a
  verified one, one a previous check found "not received". Only a copy
  never sent (a draft, or signed and not sent) is skipped, so the message
  "not sent yet" matches what the list shows. Before, the right click also
  asked copies never sent, and the will-executor's correct "I do not have
  it" turned them red;
* E2, the answers are saved by every caller (before, the right click did not
  save them); saving stays with the caller, as for the other bricks.

E3 (owner, 2026-10-16): the command line's sending does not verify
afterwards; ``bal_will_check`` is the command for that. Intended, not a gap.

What each caller shows stays with it (the Check's counter and rows, the
waiting window, the command line's JSON), through the callback.

No Qt, no ``bal.cli`` (``bal/engine`` rules, see ``bal/engine/__init__``).
"""

from electrum.logging import get_logger

from ..core.will import Will
from ..core.willexecutors import Willexecutors

__all__ = [
    "FAILED",
    "NO_ANSWER",
    "VERIFIED",
    "verify_targets",
    "verify_will",
]

_logger = get_logger(__name__)

# The outcome of one copy (codes, not texts).
VERIFIED = "verified"
FAILED = "failed"
NO_ANSWER = "no_answer"


# A copy with one of these was sent at least once (a check that found it
# missing clears PUSHED and sets CHECK_FAIL; a refused sending sets
# PUSH_FAIL).
_EVER_SENT = ("PUSHED", "CHECKED", "CHECK_FAIL", "PUSH_FAIL")


def _ever_sent(w):
    """True when the copy *w* has a will-executor and was sent at least once."""
    return bool(w.we) and any(w.get_status(s) for s in _EVER_SENT)


def verify_targets(willitems, txids=None, *, recheck=False):
    """The copies to ask, as ``[(txid, url)]``, and the ones skipped.

    Returns ``(targets, skipped)``. Without *recheck* (the Check, the
    command line) a copy is asked when it is VALID, sent and not verified
    yet (``Will.needs_server_check``). With *recheck* (the right click) a
    copy is asked when it has a will-executor and was sent at least once,
    whatever its state now (E1). *txids* limits the choice to those ids
    (``None``: every copy). ``skipped`` lists the ids of *txids* never sent
    (or with no will-executor): the ones the message "not sent yet" names.
    """
    targets, skipped = [], []
    for wid, w in willitems.items():
        if txids is not None and wid not in txids:
            continue
        if recheck:
            ask = _ever_sent(w)
        else:
            ask = Will.needs_server_check(w)
        if ask:
            targets.append((wid, w.we["url"]))
        elif txids is not None and not _ever_sent(w):
            skipped.append(wid)
    return targets, skipped


def verify_will(willitems, targets, *, on_answer=None, on_tick=None,
                on_busy=None, deadline=None):
    """Ask the will-executors for the copies *targets* and record the answers.

    *targets* comes from :func:`verify_targets`. The will-executors are
    asked in parallel, a few requests at a time each
    (``Willexecutors.check_transactions_parallel``, #172). The global
    *deadline* (seconds) is ``Willexecutors.check_deadline(targets)`` when
    not given: 20 s for a small will, more for many transactions, at most
    2 minutes; a window that shows the seconds passes the same value.
    ``on_busy(url, seconds)``: a will-executor answered 429 Too Many
    Requests and is asked again in *seconds*. Each answer is recorded with
    ``WillItem.set_check_willexecutor``:

    * it holds exactly the copy that was sent: VERIFIED (CHECKED);
    * any other answer, or an error: FAILED (CHECK_FAIL, no longer sent, so
      it is sent again);
    * no answer in time (the request's limit or the global deadline):
      NO_ANSWER, the copy stays sent and the next check asks again (#147).

    A copy that was verified before and now fails loses CHECKED too: before,
    asking a verified copy again could leave it both verified and failed.

    ``on_answer(txid, url, answer, outcome, timed_out)`` is called for each,
    from the thread that runs this function (``timed_out``: the global
    deadline, not the request's own limit). ``on_tick()`` is called about
    once a second while waiting. Nothing is saved here: each caller saves.
    Returns ``{txid: outcome}``.
    """
    outcomes = {}

    def _record(wid, url, res, timed_out):
        """Store one answer on its copy and tell the caller."""
        w = willitems[wid]
        try:
            w.set_check_willexecutor(res)
        except Exception as e:
            _logger.error(f"verify: recording the answer for {wid} failed: {e!r}")
        if res is Willexecutors.NO_ANSWER:
            outcome = NO_ANSWER
        elif w.get_status("CHECKED") and not w.get_status("CHECK_FAIL"):
            outcome = VERIFIED
        else:
            outcome = FAILED
            # WHY: CHECK_FAIL does not clear CHECKED (WillItem.set_status);
            # a verified copy asked again must not stay "verified".
            w.set_status("CHECKED", False)
        outcomes[wid] = outcome
        if on_answer is not None:
            on_answer(wid, url, res, outcome, timed_out)

    def on_each(wid, url, res, exc):
        """An answer, or an error (recorded as no usable answer)."""
        if exc is not None:
            _logger.info(f"verify: {url} did not answer for {wid}: {exc!r}")
            res = None
        _record(wid, url, res, False)

    def on_timeout(wid, url):
        """No answer before the global deadline (#147)."""
        _record(wid, url, Willexecutors.NO_ANSWER, True)

    if targets:
        if deadline is None:
            deadline = Willexecutors.check_deadline(targets)
        Willexecutors.check_transactions_parallel(
            targets, on_each=on_each, on_timeout=on_timeout, on_tick=on_tick,
            on_busy=on_busy, deadline=deadline,
        )
    return outcomes
