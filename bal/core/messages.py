"""User-facing reasons why a will is no longer coherent (GUI-free).

``Will.is_will_valid`` raises ``NotCompleteWillException`` or one of its
subclasses when the stored will stops matching the wallet, the heirs or the
will-executors. :func:`will_change_message` turns that exception into one
plain sentence. It is shared by the Check dialog, the main window
("Prepare") and the command line (CHANGELOG #78), so the three can no longer
drift apart. The texts are the ones approved by the owner in #77 and before.
It also holds the Check Alive renewal texts (CHANGELOG #79).

Must never import Qt (``bal/core`` rule).
"""

from datetime import datetime, timezone

from ..i18n import _
from .will import (
    HeirChangeException,
    HeirNotFoundException,
    NotCompleteWillException,
    NoWillExecutorNotPresent,
    TxFeesChangedException,
    WillexecutorChangeException,
    WillExecutorNotPresent,
)


def no_valid_transaction(e):
    """True for the core's argument-less NotCompleteWillException.

    ``Will.is_will_valid`` raises it with no argument when the will holds no
    VALID transaction, and with a message when a wallet coin is not covered.
    ``WillException`` keeps only ``msg`` (default ``"WillException"``) and
    never fills ``e.args``, so the default message is what tells the two
    apart (CHANGELOG #77).
    """
    return type(e) is NotCompleteWillException and str(e) == "WillException"


def will_change_message(e):
    """Return one sentence saying why the will must be prepared again.

    The exceptions already carry the useful detail (heir name and kind of
    change, will-executor URL, old and new fee rate); every case is told
    apart from what the core raises, never by matching message text.
    An unrecognised subclass gets an honest "reason could not be
    determined" sentence instead of a guess.
    """
    # Subclasses first - they are all NotCompleteWillException.
    if isinstance(e, TxFeesChangedException):
        # The core raises TxFeesChangedException(f"{tx_fees}:  {w.tx_fees}"),
        # i.e. "current:  stored".  Show both rates when they can be read,
        # and fall back to a plain sentence if that format ever changes.
        try:
            now_fee, old_fee = [p.strip() for p in str(e).split(":", 1)]
            return _(
                "Miner fee rate changed (the will was built with {} "
                "sat/byte, now it is {}): a new will must be prepared."
            ).format(old_fee, now_fee)
        except Exception:
            return _("The miner fee rate changed: a new will must be prepared.")
    if isinstance(e, WillExecutorNotPresent):
        return _(
            'Will-executor "{}" is not covered by the current will: '
            "a new will must be prepared."
        ).format(str(e))
    if isinstance(e, NoWillExecutorNotPresent):
        return _(
            "Backup mode is enabled but the will has no backup "
            "transaction: a new will must be prepared."
        )
    if isinstance(e, HeirNotFoundException):
        # Owner decision (CHANGELOG #77): one sentence per kind of change,
        # as reported by the core (Will._heir_change). The generic
        # sentence stays for an exception without that detail.
        change = getattr(e, "change", None)
        if change == HeirNotFoundException.HEIR_REMOVED:
            return _('Heir "{}" was removed: preparing a new will.').format(str(e))
        if change == HeirNotFoundException.HEIR_ADDED:
            return _('Heir "{}" was added: preparing a new will.').format(str(e))
        if change == HeirNotFoundException.HEIR_DATE:
            return _(
                'The delivery date of heir "{}" changed: preparing a new will.'
            ).format(str(e))
        if change == HeirNotFoundException.HEIR_AMOUNT:
            return _(
                'The amount of heir "{}" changed: preparing a new will.'
            ).format(str(e))
        if change == HeirNotFoundException.HEIR_ADDRESS:
            return _(
                'The address of heir "{}" changed: preparing a new will.'
            ).format(str(e))
        return _(
            'Heir "{}" is not covered by the current will (it was added '
            "or removed, or its delivery date changed): a new will must "
            "be prepared."
        ).format(str(e))
    # Kept for completeness: nothing in the plugin raises these two today,
    # but they ARE NotCompleteWillException subclasses, so should future
    # code raise them they get a sensible message rather than the fallback.
    if isinstance(e, HeirChangeException):
        return _("The heirs changed: a new will must be prepared.")
    if isinstance(e, WillexecutorChangeException):
        return _("A will-executor changed: a new will must be prepared.")

    # A plain NotCompleteWillException: no valid transaction, or a wallet
    # coin not covered by the will (see no_valid_transaction).
    if type(e) is NotCompleteWillException:
        if not no_valid_transaction(e):
            return _(
                "The wallet contains funds that the current will does not "
                "cover yet: a new will must be prepared."
            )
        return _(
            "The will contains no valid transaction: a new will must be "
            "prepared."
        )

    # An unrecognised subclass: say so honestly instead of guessing.
    return _(
        "The will is no longer coherent and must be rebuilt; the exact "
        "reason could not be determined."
    )

# ----------------------------------------------------------- Check Alive (#79)


def _day(ts):
    """``YYYY-MM-DD`` of a UNIX timestamp (UTC, like the will locktimes)."""
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")


def _period(period):
    """``(4, "y")`` -> "4 years", ``(30, "d")`` -> "30 days" (translated)."""
    value, unit = period
    if unit in ("y", "Y"):
        return _("1 year") if value == 1 else _("{} years").format(value)
    return _("1 day") if value == 1 else _("{} days").format(value)


def check_alive_renew_question(info):
    """The renewal question shown when the Check Alive has passed.

    ``info`` comes from :func:`bal.core.checkalive.check_alive_renewal` and
    must be ``renewable``. Text approved by the owner (CHANGELOG #79).
    """
    return _(
        "Are you still here? The Check Alive date ({check}) has passed.\n\n"
        "Renew your will: the new delivery date will be {new} (today + "
        "{period}). The old will is invalidated on the blockchain (this costs "
        "a miner fee) and the new one is prepared right away.\n\n"
        "Not now: nothing changes, your current will stays valid."
    ).format(
        check=_day(info["check_date"]),
        new=_day(info["new_delivery"]),
        period=_period(info["period"]),
    )


def check_alive_cannot_renew(info):
    """Why a passed Check Alive cannot be renewed with one click.

    Nothing is invalidated in these cases: the owner must change the
    settings first (CHANGELOG #79).
    """
    check = _day(info["check_date"])
    if info.get("reason") == "expired":
        return _(
            "The delivery date of your will ({date}) has already passed: the "
            "will can be executed now. Press Check to invalidate it and "
            "prepare a new one."
        ).format(date=_day(info["delivery"]))
    if not info.get("has_will"):
        return _(
            "The Check Alive date ({check}) is already in the past with this "
            "delivery date: choose a later delivery date or a shorter Check "
            "Alive."
        ).format(check=check)
    if info.get("reason") == "fixed_date":
        return _(
            "The Check Alive date ({check}) has passed. Your delivery is a "
            "fixed date: choose a later delivery date to renew your will. "
            "Until then your current will stays valid."
        ).format(check=check)
    return _(
        "The Check Alive date ({check}) has passed, and with these settings "
        "a renewed will would have its Check Alive in the past too: choose a "
        "longer delivery time or a shorter Check Alive. Until then your "
        "current will stays valid."
    ).format(check=check)


def check_alive_passed_sentence(info):
    """Short form for the command line: date passed and the new delivery."""
    return _(
        "The Check Alive date ({check}) has passed: a renewed will would be "
        "delivered on {new} (today + {period})."
    ).format(
        check=_day(info["check_date"]),
        new=_day(info["new_delivery"]),
        period=_period(info["period"]),
    )


def check_alive_message(info):
    """The question when the will can be renewed, else the reason it cannot."""
    if info.get("renewable") and info.get("has_will"):
        return check_alive_renew_question(info)
    return check_alive_cannot_renew(info)
