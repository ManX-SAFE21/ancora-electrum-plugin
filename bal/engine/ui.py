"""How the engine reports a running flow to the front-end (CHANGELOG #120).

Phase 5e, step 5e-08. The engine never writes a dialog row and never words
a status itself: while a flow runs it sends events, with codes and plain
data, to a ``FlowUI``. Each front-end turns them into its own rows and
sentences (the Check dialog: ``bal.gui.qt.flow_adapter.DialogFlowUI``,
which makes the very ``msg_*`` calls the dialog made before #120). The
events a flow sends are listed in that flow's docstring (for example
:func:`bal.engine.check.check_variables`).

Also here:

* ``CheckRun``: the state a Check keeps from one pass of phase 1 to the
  next (the dialog keeps it in its own attributes, see
  ``bal.gui.qt.flow_adapter.DialogRun``);
* ``NullUI`` (drops every event) and ``RecordingUI`` (keeps them, for the
  tests).

Only what step 5e-08 uses is here. The other events of the Phase 5e design
(the will-executors' states, progress, stop, wait) and the questions asked
between phases (password, renew, exclude, and the "cancelled" answer) come
with the steps that use them.

No Qt, no ``bal.cli`` (``bal/engine`` rules, see ``bal/engine/__init__``).
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol


# ``(str, Enum)`` and not ``enum.StrEnum`` (Python 3.11): Electrum 4.8.2
# still runs on Python 3.10 (its setup.py: MIN_PYTHON_VERSION = "3.10.0").
class Step(str, Enum):
    """The step rows a flow writes, one member per row moved so far.

    The value is a code, never shown: each front-end has its own label
    (the Check dialog writes "Checking variables" for ``VARIABLES``). A
    ``str`` subclass, so a member equals its code
    (``Step.VARIABLES == "variables"``).
    """

    VARIABLES = "variables"


class FlowUI(Protocol):
    """The events the engine sends while a flow runs.

    Called on the engine's thread (for the Check dialog: its worker
    thread, where the dialog wrote its rows before #120). The engine never
    reads what they return, so a front-end may drop an event (``NullUI``)
    without changing the flow. An exception raised by a front-end ends the
    flow, as a failing dialog row did before #120.
    """

    def step(self, step, state, **data) -> None:
        """A step row. ``state`` is ``"run"`` (the step starts: a new row)
        or an outcome code (the same row is rewritten); ``data`` holds
        plain values a front-end may show."""

    def note(self, code, **data) -> None:
        """A row of its own (an explanation, a reason, a warning), never
        rewritten, named by ``code``, with plain ``data``."""


@dataclass
class CheckRun:
    """The state a Check keeps from one pass of phase 1 to the next.

    The dialog runs phase 1 again after it broadcast an invalidation
    (``on_success_invalidate``) and after "Retry" (``_retry_check``);
    these values link the passes. Fields are added as the code that uses
    them moves to the engine.

    * ``invalidation_broadcast``: an invalidation was broadcast on an
      earlier pass and the wallet may not have seen it yet (never
      invalidate twice);
    * ``check_alive_info``: what ``check_alive_info()`` returned on the
      last pass whose Check Alive had passed (the Renew question is worded
      from it).
    """

    invalidation_broadcast: bool = False
    check_alive_info: Any = None


class NullUI:
    """A FlowUI that drops every event (a caller with nothing to show)."""

    def step(self, step, state, **data):
        """Drop the step event."""

    def note(self, code, **data):
        """Drop the note event."""


class RecordingUI:
    """A FlowUI that keeps every event in ``events``, in order (tests).

    A step is kept as ``("step", step, state, data)``, a note as
    ``("note", code, data)``.
    """

    def __init__(self):
        """Start with no event."""
        self.events = []

    def step(self, step, state, **data):
        """Keep ``("step", step, state, data)``."""
        self.events.append(("step", step, state, data))

    def note(self, code, **data):
        """Keep ``("note", code, data)``."""
        self.events.append(("note", code, data))
