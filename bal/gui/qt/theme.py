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

from ...i18n import N_

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


def status_color(will_item, in_force=False) -> str:
    """Return the display colour (``"#rrggbb"``) for a :class:`WillItem`.

    ``in_force`` (CHANGELOG #129): the item belongs to the will in force
    (``will_state.IN_FORCE``). A later unsigned draft marks the sent will
    REPLACED (#128 keeps it in force): its row then takes the colour of
    where its will-executor stands, not the pink of a replaced item.

    This is a faithful, behaviour-preserving port of the old
    ``WillItem.get_color()`` method.  The slightly irregular handling of the
    push/check states (which is not a simple priority list) is reproduced
    exactly as in the original code.
    """
    # First, the simple priority-ordered statuses.
    for status, color in _STATUS_COLOR_PRIORITY:
        if in_force and status in ("REPLACED", "UPDATED"):
            continue
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


def will_status_text(will_item, in_force=False) -> str:
    """What this row is for the will TODAY, in plain words (CHANGELOG #83).

    ``in_force`` (CHANGELOG #129): see :func:`status_color`; the REPLACED /
    UPDATED flags (and the VALID flag they clear) are not read, so a copy
    of the will in force says "In force, verified" and so on.

    Each row of the transaction list is the copy of the will held by ONE
    will-executor, and its colour (:func:`status_color`) tells where that
    executor stands. The text follows the SAME priority as the colour, so
    the two never disagree (owner's remark: "In force" on a red row whose
    executor never received the will). It replaced the whole status history
    ("New.Signed.Pushed.Checked"), now in the tooltip
    (:func:`will_status_tooltip`), and the separate "Server" column. Texts
    approved by the owner. CHANGELOG #128: a VALID item never signed nor
    sent is a "Draft" (:func:`bal.core.will_state.is_draft`).
    """
    from ...core.will_state import is_draft
    from ...i18n import _

    if will_item.get_status("INVALIDATED"):
        return _("Invalidated on-chain")
    if not in_force and (
        will_item.get_status("REPLACED") or will_item.get_status("UPDATED")
    ):
        return _("Replaced by a newer will")
    if will_item.get_status("CONFIRMED"):
        return _("Executed")
    if will_item.get_status("MEMPOOL"):
        return _("Being executed (mempool)")
    if not in_force and not will_item.get_status("VALID"):
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
    if is_draft(will_item):
        # CHANGELOG #128: never signed nor sent, it exists only in this
        # wallet and the next build drops it (owner's report: "Da firmare
        # (0/1)" read as a will waiting for him). A partially signed item
        # keeps "To sign (n/m)".
        return _("Draft")
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


# CHANGELOG #129: the state of a will VERSION (``bal.core.will_versions``)
# in the Will tab. The colours are those of the will summary and of the
# remaining-time colours (#124: ``WillSummaryWidget._STYLES``,
# ``DELIVERY_LEVEL_COLOURS``): green in force, orange something to know or
# to do, red invalidated, grey history. The texts are marked with N_ here
# (import time) and translated when shown.
_VERSION_STATES = {
    "draft": (N_("Draft"), "#b26a00"),
    "in_force": (N_("In force"), "#2e7d32"),
    "replaced_held": (N_("Replaced (still held by the will-executors)"), "#b26a00"),
    "replaced": (N_("Replaced"), "#555555"),
    "invalidated": (N_("Invalidated"), "#c62828"),
    "partially_signed": (N_("Partially Signed"), "#b26a00"),
    "executed": (N_("Executed"), "#555555"),
    "not_valid": (N_("Not valid"), "#555555"),
    "discarded_draft": (N_("Discarded draft"), "#555555"),
    "other": ("", "#555555"),
}


def version_state_text(state) -> str:
    """The translated name of a version ``state`` (CHANGELOG #129)."""
    from ...i18n import _

    text = _VERSION_STATES.get(state, ("", ""))[0]
    return _(text) if text else ""


def version_state_colour(state) -> str:
    """The text colour (``"#rrggbb"``) of a version ``state`` (CHANGELOG #129)."""
    return _VERSION_STATES.get(state, ("", "#555555"))[1]
