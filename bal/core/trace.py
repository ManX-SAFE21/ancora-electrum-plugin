"""
bal.core.trace
==============

The command trace (CHANGELOG #173): what the plugin does, written as
command lines, for the "CLI command trace…" window (Tools menu).

The GUI never runs the ``bal_*`` commands: the window and the command line
call the same engine bricks (build, sign, send, check the copies,
invalidate, merge, ping). Each brick writes one line named after the
``bal_*`` command that runs the same step, with its main parameters, then
the outcome; :meth:`bal.core.willexecutors.Willexecutors.send_request`
writes every request to a will-executor and its answer, with the seconds.

Rules (owner, 2026-10-21):

* in memory only, the last :data:`MAX_LINES` lines since Electrum started:
  nothing is written to disk (the window can save a copy);
* never a secret: no password, seed, key or raw transaction is ever
  passed here; txids are shortened (:func:`short_txid`) and
  :func:`_scrub` hides any long hex text or extended key that would slip
  through;
* thread-safe (the sending and the checks run in worker threads) and never
  raises: a trace problem must not stop a flow.

No Qt here: the command line writes the same lines.
"""

import re
import threading
import time
from collections import deque
from dataclasses import dataclass

# The kinds of line (colours in the window).
COMMAND = "command"
REQUEST = "request"
ANSWER = "answer"
OK = "ok"
WARNING = "warning"
ERROR = "error"

KINDS = (COMMAND, REQUEST, ANSWER, OK, WARNING, ERROR)

# How many lines are kept (the oldest go first).
MAX_LINES = 5000

# The longest text of one line; the rest is cut.
MAX_TEXT = 300

# A raw transaction or any other long hex text, and the extended keys
# (xprv/tprv/yprv/zprv/uprv/vprv...): never shown.
_LONG_HEX = re.compile(r"[0-9a-fA-F]{100,}")
_EXTENDED_KEY = re.compile(r"\b[a-zA-Z]prv[1-9A-HJ-NP-Za-km-z]{20,}")
_TXID = re.compile(r"\b[0-9a-fA-F]{64}\b")
_BREAKS = re.compile(r"[\r\n\t]+")


@dataclass(frozen=True)
class TraceLine:
    """One line of the trace.

    * ``seq``: its number (1, 2, 3...; never reused, also after a clear);
    * ``when``: the moment, ``time.time()``;
    * ``kind``: one of :data:`KINDS`;
    * ``text``: what it says;
    * ``full``: True for a detail shown only at the "Full" level.
    """

    seq: int
    when: float
    kind: str
    text: str
    full: bool = False


_lock = threading.Lock()
_lines = deque(maxlen=MAX_LINES)
_seq = 0


def short_txid(txid):
    """``"a1b2c3d4…9f0e"``: the first 8 and the last 4 characters of *txid*.

    A text of 12 characters or fewer, or not a text, is returned as it is
    (as a text).
    """
    text = str(txid) if txid is not None else ""
    if len(text) <= 12:
        return text
    return text[:8] + "…" + text[-4:]


def short_url(url):
    """*url* without ``https://`` / ``http://`` and without the last ``/``."""
    text = str(url or "")
    for scheme in ("https://", "http://"):
        if text.startswith(scheme):
            text = text[len(scheme):]
            break
    return text.rstrip("/")


def _scrub(text):
    """*text* on one line, without secrets, at most :data:`MAX_TEXT` long.

    A full txid is shortened, a long hex text (a raw transaction) and an
    extended private key are replaced by ``…``.
    """
    # One line; the double spaces between a command and its details stay.
    text = _BREAKS.sub(" ", str(text)).strip()
    text = _LONG_HEX.sub("…", text)
    text = _EXTENDED_KEY.sub("…", text)
    text = _TXID.sub(lambda m: short_txid(m.group(0)), text)
    if len(text) > MAX_TEXT:
        text = text[:MAX_TEXT - 1] + "…"
    return text


def add(kind, text, *, full=False):
    """Add one line; return it, or ``None`` when it could not be added.

    *kind* is one of :data:`KINDS` (an unknown one is written as
    :data:`ANSWER`); *full*: shown only at the "Full" level. Never raises.
    """
    global _seq
    try:
        if kind not in KINDS:
            kind = ANSWER
        clean = _scrub(text)
        with _lock:
            _seq += 1
            line = TraceLine(_seq, time.time(), kind, clean, bool(full))
            _lines.append(line)
        return line
    except Exception:
        return None


def command(name, details=""):
    """A command line: ``"bal_will_sign  3 transactions"``."""
    text = "{}  {}".format(name, details) if details else str(name)
    return add(COMMAND, text)


def reason(error):
    """The text of *error* for a trace line: its message, else its name."""
    return str(error) or type(error).__name__


def lines_after(seq=0):
    """The lines kept whose number is above *seq*, oldest first."""
    with _lock:
        return [line for line in _lines if line.seq > seq]


def clear():
    """Forget every line (the numbers go on from the last one)."""
    with _lock:
        _lines.clear()


def line_text(line):
    """One line as plain text: ``"14:02:11  > bal_will_check"``.

    A command starts with ``>``, a request with ``->``, an answer with
    ``<-``; the other kinds are indented.
    """
    clock = time.strftime("%H:%M:%S", time.localtime(line.when))
    mark = {COMMAND: "> ", REQUEST: "  -> ", ANSWER: "  <- "}.get(line.kind, "  ")
    return "{}  {}{}".format(clock, mark, line.text)


def plain_text(lines, *, full=True):
    """*lines* as plain text, one per row (for Copy and Save to file).

    Without *full* the "Full" details are left out.
    """
    return "\n".join(line_text(line) for line in lines if full or not line.full)
