"""What a will item is today, derived from its flags (CHANGELOG #128).

Known issue "P2", the owner's case of 2026-10-12: a will signed and sent for
2027-02-27 was followed by two UNSIGNED drafts (a fee change, the password
window closed twice). Each rebuild had to beat the latest draft, so the
delivery date crept back one day at a time, although the will-executors
still held the will of 02-27.

The fix needs to tell the items apart. Nothing new is stored in the wallet
(owner decision 2026-10-12: no new wallet keys, no stored history): the
state is derived every time from the status flags the plugin already keeps
(``WillItem.STATUS``) and, for "in force", from the chain.

* :data:`DRAFT`: VALID, never signed (not COMPLETE, no signature at all)
  and never sent (not PUSHED). It exists only in this wallet: dropping it
  is free, and it must never decide the date of a new will.
* :data:`PARTIALLY_SIGNED`: some but not all signatures (multisig
  co-signing; the ``PARTIALLY_SIGNED`` flag of ``Will.check_signatures`` or
  a signature count above 0). Its own state: never a draft, never dropped,
  because a co-signer may complete it and send it.
* :data:`IN_FORCE`: signed (COMPLETE) and/or sent (PUSHED), VALID or
  REPLACED, not INVALIDATED / CONFIRMED / MEMPOOL, and still executable (no
  input spent by a broadcast transaction). Among those, the one with the
  earliest delivery is what the will-executors would execute. On a wallet
  that is not synchronized the spends are not known: such an item counts as
  in force (the safe side: a new will is dated against it, never after it).
* :data:`REPLACED`, :data:`INVALIDATED`, :data:`EXECUTED` (CONFIRMED or
  MEMPOOL) and :data:`NOT_VALID` (anything else) as the flags say.

The functions are duck-typed: an item needs ``get_status(flag)`` (and may
have ``sigs_have``), so they work on ``bal.core.will.WillItem`` and on
``bal.core.delivery.ItemView`` alike. Pure: no Qt, no Electrum, no clock;
the chain is asked through the ``executable`` callable the caller passes.
The grouped history of the Will tab (a later step) reuses :func:`classify`.
"""

DRAFT = "draft"
PARTIALLY_SIGNED = "partially_signed"
IN_FORCE = "in_force"
REPLACED = "replaced"
INVALIDATED = "invalidated"
EXECUTED = "executed"
NOT_VALID = "not_valid"


def _flag(item, name):
    """The status flag ``name`` of ``item`` (False when it has none)."""
    try:
        return bool(item.get_status(name))
    except (KeyError, AttributeError):
        return False


def is_partially_signed(item):
    """True when ``item`` carries some signatures but is not complete.

    The flag ``PARTIALLY_SIGNED`` is set by ``Will.check_signatures`` only
    for a multisig input; the stored count ``sigs_have`` is read too, so an
    item with any signature is never taken for a draft (WHY: dropping it
    would throw away a co-signer's work).
    """
    if _flag(item, "COMPLETE"):
        return False
    if _flag(item, "PARTIALLY_SIGNED"):
        return True
    try:
        return int(getattr(item, "sigs_have", 0) or 0) > 0
    except (TypeError, ValueError):
        return False


def is_signed_or_sent(item):
    """True when ``item`` was signed (COMPLETE) or sent (PUSHED)."""
    return _flag(item, "COMPLETE") or _flag(item, "PUSHED")


def is_draft(item):
    """True for a VALID item never signed nor sent (see :data:`DRAFT`)."""
    return (
        _flag(item, "VALID")
        and not is_signed_or_sent(item)
        and not is_partially_signed(item)
    )


def may_be_in_force(item):
    """The flag part of :func:`is_in_force`: signed or sent, VALID or
    REPLACED, not INVALIDATED / CONFIRMED / MEMPOOL."""
    if not is_signed_or_sent(item):
        return False
    if _flag(item, "INVALIDATED") or _flag(item, "CONFIRMED") or _flag(item, "MEMPOOL"):
        return False
    return _flag(item, "VALID") or _flag(item, "REPLACED")


def is_in_force(item, synced, executable):
    """True when ``item`` belongs to the will the executors may execute.

    ``synced``: the wallet is synchronized; ``executable()``: True when no
    input of the item is spent by a broadcast transaction (asked only on a
    synchronized wallet: before, the spends are not known, so the item
    counts, see the module docstring).
    """
    if not may_be_in_force(item):
        return False
    return (not synced) or bool(executable())


def classify(item, synced=False, executable=None):
    """The state of ``item``: one of the constants of this module.

    ``synced`` / ``executable``: see :func:`is_in_force` (without
    ``executable`` the item counts as executable). Order: executed,
    invalidated, in force, partially signed, draft, replaced, not valid.
    """
    if _flag(item, "CONFIRMED") or _flag(item, "MEMPOOL"):
        return EXECUTED
    if _flag(item, "INVALIDATED"):
        return INVALIDATED
    if is_in_force(item, synced, executable or (lambda: True)):
        return IN_FORCE
    if _flag(item, "VALID") and is_partially_signed(item):
        return PARTIALLY_SIGNED
    if is_draft(item):
        return DRAFT
    if _flag(item, "REPLACED"):
        return REPLACED
    return NOT_VALID


def draft_ids(will):
    """The ids of the drafts of ``{wid: item}``, sorted."""
    return sorted(wid for wid, item in (will or {}).items() if is_draft(item))
