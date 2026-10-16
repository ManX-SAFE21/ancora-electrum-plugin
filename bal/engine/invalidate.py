"""The engine's invalidation brick: build the invalidation transaction (#152).

Before, the same call was written in six places:

* the Will tab's "Invalidate will" and the Will Details window
  (``BalWindow.invalidate_will``);
* the automatic invalidation (``BalWindow._auto_invalidate_will``);
* the command line's ``bal_will_invalidate``
  (``BalController.invalidate_will``);
* the Check, for a postponed will and for a will expired only against its
  Check Alive (``BalBuildWillDialog.task_phase1``);
* the Check, for a Check Alive renewal
  (:func:`bal.engine.check.check_variables`).

Each one passed the same values to :meth:`Will.invalidate_will`: the will's
fee rate, the History label setting and the earliest locktime of the will,
with the reference date of the checks as the fallback. They live here now,
so a change of that rule is made in one place. Signing and broadcasting stay
with each caller.

No Qt, no ``bal.cli`` (``bal/engine`` rules, see ``bal/engine/__init__``).
"""

from ..core.will import Will
from ..core.will_versions import INVALIDATION_LABEL

__all__ = ["INVALIDATION_LABEL", "build_invalidation"]


def build_invalidation(host, will=None):
    """Build the unsigned invalidation transaction of *host*'s will.

    *host* is a :class:`bal.engine.host.WalletHost`; it reads ``wallet``,
    ``willitems``, ``will_settings`` (``baltx_fees``, default 1 sat/byte),
    ``bal_plugin.HISTORY_LABEL`` and ``date_to_check`` (``None`` when the
    host has none yet). *will* is the will to invalidate, as
    ``{txid: WillItem}``; ``None`` means ``host.willitems``.

    Returns what :meth:`Will.invalidate_will` returns: a
    ``PartialTransaction``, or ``None`` when there is nothing to invalidate.
    Its errors propagate; nothing is signed, labelled or broadcast here.
    """
    willitems = will if will is not None else host.willitems
    return Will.invalidate_will(
        willitems,
        host.wallet,
        host.will_settings.get("baltx_fees", 1),
        history_label=host.bal_plugin.HISTORY_LABEL.get(),
        will_locktime=Will.get_min_locktime(
            willitems,
            default_value=getattr(host, "date_to_check", None),
        ),
    )
