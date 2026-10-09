"""
bal.gui.qt.theme
================

Pure presentation helpers for the Qt layer.

This is where colours and other look-and-feel decisions live, kept apart from
the core inheritance logic.  In particular it hosts :func:`status_color`, which
used to be ``WillItem.get_color()`` inside ``will.py``.

The status flags themselves are computed by the core layer
(:class:`bal.core.will.WillItem`); this module only translates a will item's
status into a colour for the transaction list / detail views.
"""

# Status -> hex colour.  The first matching status (checked in priority order)
# wins.  These are exactly the colours the original ``WillItem.get_color`` used,
# so the GUI looks identical after the refactor.
#
# The order matters: e.g. an INVALIDATED tx must show orange even if it also
# carries other flags, so INVALIDATED is checked before everything else.
_STATUS_COLOR_PRIORITY = (
    ("INVALIDATED", "#f87838"),  # orange  - tx can no longer be mined
    ("REPLACED", "#ff97e9"),     # pink    - superseded by another tx
    ("UPDATED", "#b266b2"),      # light violet - replaced keeping same locktime+heirs
    ("CONFIRMED", "#bfbfbf"),    # grey    - already mined
    ("MEMPOOL", "#ffce30"),      # yellow  - seen in the Electrum mempool
)

# Default colour used when no status in the priority list matches.
_DEFAULT_COLOR = "#ffffff"


def status_color(will_item) -> str:
    """Return the display colour (``"#rrggbb"``) for a :class:`WillItem`.

    This is a faithful, behaviour-preserving port of the old
    ``WillItem.get_color()`` method.  The slightly irregular handling of the
    push/check states (which is not a simple priority list) is reproduced
    exactly as in the original code.
    """
    # First, the simple priority-ordered statuses.
    for status, color in _STATUS_COLOR_PRIORITY:
        if will_item.get_status(status):
            return color

    # The remaining states need the original branching because of the
    # CHECK_FAIL / CHECKED interaction.
    if will_item.get_status("CHECK_FAIL") and not will_item.get_status("CHECKED"):
        return "#e83845"  # red - server check failed
    elif will_item.get_status("CHECKED"):
        return "#8afa6c"  # green - server confirmed it stored the tx
    elif will_item.get_status("PUSH_FAIL"):
        return "#e83845"  # red - failed to push to will-executor
    elif will_item.get_status("PUSHED"):
        return "#73f3c8"  # teal - pushed to will-executor
    elif will_item.get_status("PARTIALLY_SIGNED"):
        return "#ffb347"  # amber - some signatures present, more needed
    elif will_item.get_status("COMPLETE"):
        return "#2bc8ed"  # blue - signed
    else:
        return _DEFAULT_COLOR


def signature_suffix(will_item) -> str:
    """Return the ``" (added/required)"`` suffix for a non-signed will item.

    Used by the transaction list and the detail view to show how many of the
    required signatures have already been added, e.g. ``"(1/2)"`` for a 2-of-3
    transaction carrying one signature. Returns ``""`` for signed transactions
    or when the required count is unknown (no descriptor available yet).
    """
    if will_item.get_status("COMPLETE"):
        return ""
    required = int(getattr(will_item, "sigs_required", 0) or 0)
    added = int(getattr(will_item, "sigs_have", 0) or 0)
    if not required:
        return ""
    return " ({}/{})".format(added, required)


def will_status_text(will_item) -> str:
    """What this row is for the will TODAY, in plain words (CHANGELOG #83).

    Each row of the transaction list is the copy of the will held by ONE
    will-executor, and its colour (:func:`status_color`) tells where that
    executor stands. The text follows the SAME priority as the colour, so
    the two never disagree (owner's remark: "In force" on a red row whose
    executor never received the will). It replaced the whole status history
    ("New.Signed.Pushed.Checked"), now in the tooltip
    (:func:`will_status_tooltip`), and the separate "Server" column. Texts
    approved by the owner.
    """
    from ...i18n import _

    if will_item.get_status("INVALIDATED"):
        return _("Invalidated on-chain")
    if will_item.get_status("REPLACED") or will_item.get_status("UPDATED"):
        return _("Replaced by a newer will")
    if will_item.get_status("CONFIRMED"):
        return _("Executed")
    if will_item.get_status("MEMPOOL"):
        return _("Being executed (mempool)")
    if not will_item.get_status("VALID"):
        return _("Not valid")
    if will_item.get_status("CHECK_FAIL") and not will_item.get_status("CHECKED"):
        return _("Not received by the executor")
    if will_item.get_status("CHECKED"):
        return _("In force, verified")
    if will_item.get_status("PUSH_FAIL"):
        return _("Not received by the executor")
    if will_item.get_status("PUSHED"):
        return _("In force, to be verified")
    if will_item.get_status("COMPLETE"):
        # A backup transaction has no will-executor: nothing to send.
        if not getattr(will_item, "we", None):
            return _("In force")
        return _("Signed, to be sent")
    return _("To sign") + signature_suffix(will_item)


def will_status_tooltip(will_item) -> str:
    """The row's will-executor and its whole status history (CHANGELOG #83).

    "Will-Executor: https://... / History: New → Signed → Pushed → Checked".
    The saved history is unchanged (English, dot separated, see
    ``WillItem.set_status``); only its display changes.
    """
    from ...core.will import format_status_history
    from ...i18n import _

    we = getattr(will_item, "we", None)
    url = we.get("url") if we else None
    head = "{}: {}".format(_("Will-Executor"), url) if url else _("No will-executor")
    steps = [s for s in format_status_history(will_item.status).split(".") if s]
    return head + "\n" + _("History: {}").format(" → ".join(steps))
