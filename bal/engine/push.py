"""The engine's sending brick: send the will to its will-executors (#155).

Before, sending the signed will was written three times:

* the Will tab's "Broadcast will" (also the Will Details window, the will
  summary's "Send" and the settings' re-send):
  ``BalWindow.push_transactions_to_willexecutors``;
* the Check's "Sending" step: ``BalBuildWillDialog.loop_push``;
* the command line's ``bal_will_broadcast`` (and the sending inside
  ``bal_will_prepare`` / ``bal_will_autorebuild``):
  ``BalController.push_transactions_to_willexecutors``.

The core was the same (group the signed, not yet sent transactions by
will-executor, send them in parallel with a global deadline, mark them
PUSHED or PUSH_FAIL, check the copies of the will-executors that answer
"already present"), but three rules differed. Owner decisions 2026-10-16,
now the same for every caller:

* D1, a will-executor that does not answer before the global deadline: its
  transactions are PUSH_FAIL and it is reported (before, the menu said
  "All transactions are broadcasted" and the command line did not mark
  them);
* D2, which will-executors receive the will: the selected ones whose
  current record is still valid, i.e. a valid address and a fee between
  the dust limit and the maximum fee (before, only the Check checked the
  fee and the address); the others are reported as excluded, with the
  reason;
* D3, an error while checking an "already present" copy: logged, the copy
  counts as not verified (``set_check_willexecutor(None)``) and the next
  check goes on (before, only the command line went on).

What each caller shows stays with it (the Check's counter and report, the
menu's waiting window, the command line's JSON), through the callbacks.

No Qt, no ``bal.cli`` (``bal/engine`` rules, see ``bal/engine/__init__``).
"""

from dataclasses import dataclass, field

from electrum.logging import get_logger

from ..core.willexecutors import Willexecutors

__all__ = [
    "ALREADY_PRESENT",
    "EXCLUDED_FEE",
    "EXCLUDED_INVALID",
    "FAILED",
    "RECEIVED",
    "TIMEOUT",
    "PushResult",
    "push_will",
    "select_push",
]

_logger = get_logger(__name__)

# The outcome of one will-executor (stored nowhere: codes, not texts).
RECEIVED = "received"
ALREADY_PRESENT = "already_present"
FAILED = "failed"
TIMEOUT = "timeout"

# Why a will-executor is excluded before sending (D2).
EXCLUDED_FEE = "fee_above_maximum"
EXCLUDED_INVALID = "invalid"


def _exclusion_reason(current, max_fee):
    """Why the current record *current* cannot receive the will."""
    try:
        if current and max_fee is not None and int(current.get("base_fee", 0)) > max_fee:
            return EXCLUDED_FEE
    except (TypeError, ValueError):
        pass
    return EXCLUDED_INVALID


def select_push(willitems, current_willexecutors, *, force=False, max_fee=None,
                dust=None):
    """The will-executors to send *willitems* to, and the excluded ones.

    Returns ``(selected, excluded)``. ``selected`` is
    ``Willexecutors.get_willexecutor_transactions`` (the valid, signed
    transactions not sent yet, or sent too with *force*, grouped by
    will-executor; one deselected in *current_willexecutors* is skipped),
    without the will-executors whose CURRENT record is no longer valid
    (``Willexecutors.is_valid`` with *max_fee* and *dust*, D2).
    ``excluded`` maps each of those to ``EXCLUDED_FEE`` or
    ``EXCLUDED_INVALID``; nothing is marked on their transactions.
    """
    grouped = Willexecutors.get_willexecutor_transactions(
        willitems, force=force, current_willexecutors=current_willexecutors
    )
    current_willexecutors = current_willexecutors or {}
    selected, excluded = {}, {}
    for url, record in grouped.items():
        current = current_willexecutors.get(url)
        if Willexecutors.is_valid(current, max_fee=max_fee, dust=dust):
            selected[url] = record
        else:
            excluded[url] = _exclusion_reason(current, max_fee)
            _logger.info(f"will-executor {url} excluded from sending: {excluded[url]}")
    return selected, excluded


@dataclass
class PushResult:
    """What :func:`push_will` did.

    * ``outcomes``: ``{url: RECEIVED | ALREADY_PRESENT | FAILED | TIMEOUT}``;
    * ``errors``: ``{url: reason}`` for FAILED (the server's message, or the
      error text; ``""`` when there is none);
    * ``checks``: ``{txid: answer}`` of the "already present" checks;
    * ``stopped``: True when *should_stop* ended the checks early.
    """

    outcomes: dict = field(default_factory=dict)
    errors: dict = field(default_factory=dict)
    checks: dict = field(default_factory=dict)
    stopped: bool = False

    @property
    def any_failed(self):
        """True when a will-executor did not get the will (FAILED or TIMEOUT)."""
        return any(o in (FAILED, TIMEOUT) for o in self.outcomes.values())


def _mark(willitems, record, status):
    """Set *status* on every transaction of the will-executor *record*."""
    for wid in record.get("txsids", []):
        willitems[wid].set_status(status, True)


def push_will(willitems, selected, *, on_result=None, on_tick=None,
              should_stop=None, on_check_start=None, on_check_done=None,
              on_checks_begin=None, on_check_tick=None, on_busy=None):
    """Send the will to the *selected* will-executors and record the outcome.

    *selected* comes from :func:`select_push`. The will-executors are
    contacted once, in parallel (``Willexecutors.push_transactions_parallel``,
    global deadline ``PUSH_GLOBAL_DEADLINE``); there is no automatic retry,
    so a dead server cannot keep the flow waiting. On each answer:

    * received: the transactions become PUSHED;
    * "already present": checked afterwards, see below;
    * refused or unreachable: PUSH_FAIL, the reason in ``errors``;
    * no answer before the deadline (D1): PUSH_FAIL.

    ``on_result(url, record, outcome, exc)`` is called for each, from the
    thread that runs this function or from a sending worker; it must only
    do thread-safe book-keeping (a Qt caller emits signals). ``on_tick()``
    is called about once a second while waiting (the caller's counter).

    Then each transaction of an "already present" will-executor is checked
    and the answer stored with ``set_check_willexecutor``. #172: with the
    Check's verification (``Willexecutors.check_transactions_parallel``: a
    few at a time per will-executor, a new try after a 429, the deadline of
    ``Willexecutors.check_deadline``); no answer in time is ``NO_ANSWER``.
    An error is logged and stored as ``None`` (not verified), and the other
    checks go on (D3). ``on_checks_begin(count, deadline)`` comes first
    (the window shows the count and the seconds), then
    ``on_check_start(url, txid)`` for every copy; ``on_check_done(url,
    txid, answer)`` for each answer, ``on_check_tick()`` about once a
    second and ``on_busy(url, seconds)`` after a 429, all from the thread
    that runs this function. ``should_stop()`` True stops the waiting (the
    copies not answered yet stay as they were).

    Nothing is saved here: each caller saves as before. Returns a
    :class:`PushResult`.
    """
    result = PushResult()
    already = []

    def on_each(url, record, ok, exc):
        """Record one answer (any thread)."""
        if isinstance(exc, Willexecutors.AlreadyPresentException):
            outcome = ALREADY_PRESENT
            already.append(url)
        elif ok:
            outcome = RECEIVED
            _mark(willitems, record, "PUSHED")
        else:
            outcome = FAILED
            _mark(willitems, record, "PUSH_FAIL")
            result.errors[url] = record.get("broadcast_error") or (str(exc) if exc else "")
        result.outcomes[url] = outcome
        # The raw transactions are not needed any more.
        record.pop("txs", None)
        if on_result is not None:
            on_result(url, record, outcome, exc)

    def on_timeout(url, record):
        """No answer before the global deadline (D1)."""
        _mark(willitems, record, "PUSH_FAIL")
        result.outcomes[url] = TIMEOUT
        if on_result is not None:
            on_result(url, record, TIMEOUT, None)

    Willexecutors.push_transactions_parallel(
        selected, on_each=on_each, on_timeout=on_timeout, on_tick=on_tick
    )
    # A will-executor that reached neither callback (a worker error) did not
    # get the will either: the same as no answer (D1).
    for url, record in selected.items():
        if url not in result.outcomes:
            on_timeout(url, record)

    # #172 (owner's test 2026-10-21): these checks went one by one, 16 s
    # each for a will-executor that does not answer them; 31 copies of a will
    # in several deliveries kept the window still for 8 minutes. Now they go
    # through the Check's own verification: a few at a time per
    # will-executor, a new try after a 429, a deadline that grows with the
    # copies (at most 2 minutes); not answered in time: NO_ANSWER (#147).
    targets = [(wid, url) for url in already
               for wid in selected[url].get("txsids", [])]
    if not targets:
        return result
    if should_stop is not None and should_stop():
        result.stopped = True
        return result
    deadline = Willexecutors.check_deadline(targets)
    if on_checks_begin is not None:
        on_checks_begin(len(targets), deadline)
    if on_check_start is not None:
        for wid, url in targets:
            on_check_start(url, wid)

    def record(wid, url, answer):
        """Store one answer on its copy and tell the caller."""
        willitems[wid].set_check_willexecutor(answer)
        result.checks[wid] = answer
        if on_check_done is not None:
            on_check_done(url, wid, answer)

    def on_answer(wid, url, answer, exc):
        """An answer, or an error (stored as None: not verified, D3)."""
        if exc is not None:
            _logger.error(f"check after already-present failed for {wid}: {exc}")
            answer = None
        record(wid, url, answer)

    Willexecutors.check_transactions_parallel(
        targets, on_each=on_answer, deadline=deadline,
        on_timeout=lambda wid, url: record(wid, url, Willexecutors.NO_ANSWER),
        on_tick=on_check_tick, on_busy=on_busy, should_stop=should_stop,
    )
    if should_stop is not None and should_stop() and len(result.checks) < len(targets):
        result.stopped = True
    return result
