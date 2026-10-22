"""What the "Building Will" dialog shows above its log (CHANGELOG #84b).

The dialog used to print one text row per step ("Checking variables: Ok",
"Building your will: Wait", ...) and the owner found it correct but hard to
follow. It now shows, from top to bottom (mockup approved by the owner):

* a bar with the five steps Checking -> Preparation -> Signature -> Sending
  -> Verification, each with a state (done, running, attention, error, not
  needed) and one short word under it;
* one result sentence;
* boxes only when they have something to say: what changed, warnings,
  problems, the heirs with their shares, the will-executors, the will in
  force after an error;
* the old rows, as a collapsible log with the time of each line.

:class:`BuildReport` holds that state. It is GUI-free (``bal/core`` rule) and
thread-safe: the dialog's worker thread updates it, the GUI thread draws a
:meth:`BuildReport.snapshot`. It decides nothing about the will: it only
records what the dialog's existing steps report.
"""

import threading

from ..i18n import N_, _
from .heirs import HEIR_REAL_AMOUNT, is_op_return_address
from .will import Will

#: The steps of the bar, in order.
STEPS = ("check", "prepare", "sign", "send", "verify")

#: Their labels (translated when shown).
STEP_LABELS = {
    "check": N_("Checking"),
    "prepare": N_("Preparation"),
    "sign": N_("Signature"),
    "send": N_("Sending"),
    "verify": N_("Verification"),
}

#: Step states. "todo": not reached (grey, no word under it).
STEP_STATES = ("todo", "run", "ok", "warn", "err", "skip")

#: The text boxes, in the order they are shown.
BOXES = ("changes", "warnings", "problems")

#: Will-executor states shown in the will-executors box ("verifying": asked
#: for the will by the Verification step, no answer yet, #84b-2).
EXECUTOR_STATES = (
    "waiting", "received", "verifying", "verified", "failed", "timeout",
    "to_sign", "to_send", "to_verify", "deselected",
    # #147: sent and received, but no answer to the check in time.
    "no_check_answer",
    # #178: excluded after it received the will, no longer asked.
    "excluded",
)


def _current_name(name, address, heirs):
    """The name the Heirs tab gives today to the heir paid at ``address``.

    ``heirs``: the Heirs tab (``{name: [address, ...]}``) or None. The one
    heir of the tab with that address; ``name`` (the name stored in the will
    item) when there is none or more than one (#135 follow-up).
    """
    if not heirs:
        return name
    found = []
    for n, h in heirs.items():
        try:
            if str(h[0]) == address:
                found.append(n)
        except (IndexError, KeyError, TypeError):
            continue        # an entry without an address: not this heir
    return str(found[0]) if len(found) == 1 else name


def heir_shares(willitems, heirs=None):
    """``[(name, satoshi, percent)]`` of the will in force, for the heirs box.

    Read from the first VALID item: every will-executor copy carries the same
    heirs with the same resolved amounts. Leaves out the will-executor fee
    entries (reserved ``w!ll3x3c"`` names) and OP_RETURN messages (no
    amount). Empty when the amounts are not resolved (not a number).

    ``heirs`` (the Heirs tab, optional, #135 follow-up): each heir is named
    as the tab names its address today. WHY: a copy already sent keeps the
    name it was built with, so after a rename (same address) the box showed
    the old name next to "renamed: old → new".
    """
    for w in Will.only_valid_list(willitems).values():
        rows = []
        for name, heir in (w.heirs or {}).items():
            if str(name)[:9] == 'w!ll3x3c"' or is_op_return_address(str(heir[0])):
                continue
            try:
                rows.append((_current_name(str(name), str(heir[0]), heirs),
                             int(heir[HEIR_REAL_AMOUNT])))
            except (IndexError, TypeError, ValueError):
                if "DUST" in str(heir[HEIR_REAL_AMOUNT:HEIR_REAL_AMOUNT + 1]):
                    continue        # excluded heir: reported in the warnings
                return []
        total = sum(sats for _name, sats in rows)
        if not total:
            return []
        return [(name, sats, 100.0 * sats / total) for name, sats in sorted(rows)]
    return []


def step_label(step):
    """The translated label of ``step``."""
    return _(STEP_LABELS[step])


def default_note(step, state):
    """The short word under a step when the caller gives none."""
    if state == "skip":
        return _("not needed")
    if state == "err":
        return _("error")
    if state == "warn":
        return _("attention")
    if state == "ok":
        return {
            "check": _("ok"),
            "prepare": _("updated"),
            "sign": _("signed"),
        }.get(step, _("ok"))
    return ""


class BuildReport:
    """The structured content of the Building Will dialog (thread-safe)."""

    def __init__(self):
        self._lock = threading.Lock()
        self.reset()

    def reset(self):
        """Start again (the dialog re-runs its checks after a fix)."""
        with self._lock:
            self._steps = {s: ["todo", ""] for s in STEPS}
            self._boxes = {b: {} for b in BOXES}
            self._executors = {}
            self._heirs = None
            self._will_now = None
            self._result = None
            self._interrupted = set()
            # #135: the "What changes" box (rows) and whether it is shown
            # before the signature (with its sentence) or the invalidation.
            self._changes = None
            self._changes_for = None
            self._changes_renamed = False
            # #135: the shares recalculated in proportion: (text, prominent).
            self._amounts_note = None
            # #135 follow-up: the facts of the invalidation about to be
            # signed (``invalidation_preview.invalidation_facts``) or None.
            self._invalidation = None

    # ---- updates (any thread) ---------------------------------------- #
    def set_step(self, step, state, note=None):
        """Set the state of ``step`` and the word under it."""
        if step not in STEPS or state not in STEP_STATES:
            return
        with self._lock:
            self._steps[step] = [state, default_note(step, state) if note is None else note]

    def interrupt(self, step):
        """``step`` was cancelled by the owner (Cancel at a password)."""
        with self._lock:
            self._interrupted.add(step)
            self._steps[step] = ["warn", _("interrupted")]

    def put(self, box, key, text):
        """Add (or replace, same ``key``) a line of ``box``; None removes it."""
        if box not in BOXES:
            return
        with self._lock:
            if text:
                self._boxes[box][key] = text
            else:
                self._boxes[box].pop(key, None)

    def set_executor(self, url, state, seconds=None):
        """Where the will-executor ``url`` stands (live during the sending)."""
        if not url or state not in EXECUTOR_STATES:
            return
        with self._lock:
            self._executors[url] = (state, seconds)

    def merge_executors(self, states):
        """Final states ``{url: state}`` from the will items.

        A live "timeout" is kept over a final "failed" (it says more), and an
        executor only seen live keeps its live state. #147: so is a live
        "no_check_answer" over the final "to_verify" (sent, not verified).
        """
        with self._lock:
            for url, state in states.items():
                live = self._executors.get(url)
                if live and live[0] == "timeout" and state == "failed":
                    continue
                if live and live[0] == "no_check_answer" and state == "to_verify":
                    continue
                self._executors[url] = (state, None)

    def set_heirs(self, heirs):
        """``[(name, satoshi, percent)]`` of the will just prepared, or None."""
        with self._lock:
            self._heirs = list(heirs) if heirs else None

    def set_changes(self, rows, when="sign", renamed=False):
        """The rows of "What changes compared with the will in force"
        (``bal.core.will_changes.change_rows``; None: no box) and ``when``
        it is shown: ``"sign"`` (before the signature), ``"invalidate"``
        (before the invalidation of a postpone) or ``"check"`` (a Check
        with only heirs renamed, nothing to sign). #135. ``renamed``: the
        rows hold an heir rename, already saved (the Cancel sentence says
        so)."""
        with self._lock:
            self._changes = list(rows) if rows else None
            self._changes_for = when if rows else None
            self._changes_renamed = bool(rows) and bool(renamed)

    def set_amounts_note(self, text, prominent=False):
        """The note "shares recalculated in proportion" (#135, owner).

        ``prominent``: the shares change compared with the will in force
        (or it is the first will): a box with a calm title; otherwise one
        quiet grey line by the heirs. None/empty ``text``: no note.
        """
        with self._lock:
            self._amounts_note = (str(text), bool(prominent)) if text else None

    def set_invalidation(self, facts):
        """The box "Invalidation of the old will" shown before its password
        (``bal.core.invalidation_preview.invalidation_facts``; None: no
        box). #135 follow-up."""
        with self._lock:
            self._invalidation = dict(facts) if facts else None

    def set_will_now(self, text):
        """The will in force after an error or a Cancel (None: no box)."""
        with self._lock:
            self._will_now = text

    def set_result(self, kind, text):
        """The result sentence; ``kind``: ok, warn, err, run or info."""
        with self._lock:
            self._result = (kind, text)

    # ---- reads -------------------------------------------------------- #
    def step(self, step):
        """``(state, note)`` of ``step``."""
        with self._lock:
            return tuple(self._steps[step])

    def interrupted(self):
        """The steps cancelled by the owner."""
        with self._lock:
            return set(self._interrupted)

    def executors(self):
        """``{url: (state, seconds)}``."""
        with self._lock:
            return dict(self._executors)

    def snapshot(self):
        """A copy of everything, for the GUI thread to draw."""
        with self._lock:
            return {
                "steps": [(s, *self._steps[s]) for s in STEPS],
                "result": self._result,
                "boxes": {b: list(self._boxes[b].values()) for b in BOXES},
                "heirs": list(self._heirs) if self._heirs else None,
                "executors": sorted(self._executors.items()),
                "will_now": self._will_now,
                # #135: only when shown, so the snapshots (and the golden
                # files) of the dialogs without the box are unchanged.
                **({"changes": [list(r) for r in self._changes],
                    "changes_for": self._changes_for} if self._changes else {}),
                **({"changes_renamed": True} if self._changes_renamed else {}),
                **({"amounts_note": list(self._amounts_note)}
                   if self._amounts_note else {}),
                # #135 follow-up: only when shown, as "changes".
                **({"invalidation": dict(self._invalidation)}
                   if self._invalidation else {}),
            }
