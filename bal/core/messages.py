"""User-facing reasons why a will is no longer coherent (GUI-free).

``Will.is_will_valid`` raises ``NotCompleteWillException`` or one of its
subclasses when the stored will stops matching the wallet, the heirs or the
will-executors. :func:`will_change_message` turns that exception into one
plain sentence. It is shared by the Check dialog, the main window
("Prepare") and the command line (CHANGELOG #78), so the three can no longer
drift apart. The texts are the ones approved by the owner in #77 and before.
It also holds the Check Alive renewal texts (CHANGELOG #79), the text
for the will-executor that cannot be deleted (CHANGELOG #108) and the
sentences of the Check dialog that need no widget (CHANGELOG #115, moved
unchanged from ``BalBuildWillDialog`` so a GUI-free engine can use them).

Must never import Qt (``bal/core`` rule).
"""

from datetime import datetime, timezone

from electrum.logging import get_logger

from ..i18n import _, number_separators
from .plugin_base import BalTimestamp
from .will import (
    HeirChangeException,
    HeirNotFoundException,
    NotCompleteWillException,
    NoWillExecutorNotPresent,
    SameDateCopiesException,
    TxFeesChangedException,
    Will,
    WillexecutorChangeException,
    WillExecutorNotPresent,
)

_logger = get_logger(__name__)


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
    if isinstance(e, SameDateCopiesException):
        # CHANGELOG #91: the state left by wills built before the fix.
        return _(
            'Will-executor "{}" holds two wills that can be executed on the '
            "same day, and one of them does not include all your coins: "
            "preparing a new will, dated one day earlier."
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


def protected_executor_message(url):
    """Why the Bitcoin After Life will-executor was not deleted or renamed.

    One text for the GUI and the command line (CHANGELOG #108), see
    ``Willexecutors.is_protected``.
    """
    return _(
        "{} is the Bitcoin After Life will-executor: it cannot be deleted or "
        "renamed. To stop using it, deselect it."
    ).format(url)

# ------------------------------------------------- Check dialog sentences (#115)
# Moved unchanged from BalBuildWillDialog (CHANGELOG #115). The dialog keeps
# its method names as one-line bridges that read its own state and call these.


def rebuild_reason_message(
    e, invalidation_broadcast, postpone_invalidation, unsigned_executors
):
    """Name the real reason when the core's sentence would mislead.

    Texts approved by the owner (CHANGELOG #77):

    * right after the Check broadcast an invalidation
      (``invalidation_broadcast``), the old items are all INVALIDATED and
      the core reports "no valid transaction"; the real news is that the old
      will is gone and the new one comes (with the new date for a postpone,
      ``postpone_invalidation``, else the time period set);
    * after a Cancel at signing, the next Check discards the unsigned items
      and reports "no valid transaction" or "will-executor not covered" (for
      an executor in ``unsigned_executors``, see
      :meth:`Will.unsigned_will_executors`): say the will was not signed.
      Any other cause (fee, heirs...) keeps its own message.

    Returns ``None`` when neither applies.
    """
    plain = no_valid_transaction(e)
    if plain and invalidation_broadcast:
        if postpone_invalidation:
            return _(
                "Old will invalidated: preparing the new will with the "
                "new date."
            )
        return _(
            "Old will invalidated: preparing the new will with the "
            "time period you set."
        )
    if unsigned_executors and (
        plain
        or (isinstance(e, WillExecutorNotPresent) and str(e) in unsigned_executors)
    ):
        return _(
            "The will prepared earlier was not signed: preparing it again."
        )
    return None


def amounts_message(info):
    """Turn :meth:`Will.describe_amounts` into one plain sentence.

    Texts approved by the owner (CHANGELOG #76). Percentages are shown
    with one decimal (none when whole) and amounts in satoshi with the
    separators of the current language (``bal.i18n.number_separators``).
    Returns ``None`` when ``info`` is ``None``.
    """
    if not info:
        return None
    decimal, thousands = number_separators()

    def pct(x):
        s = "{:.1f}".format(x)
        if s.endswith(".0"):
            s = s[:-2]
        return s.replace(".", decimal)

    def sats(n):
        return "{:,}".format(int(n)).replace(",", thousands)

    if info["kind"] == "fixed_exceeds":
        if info["percent_heirs"]:
            return _(
                "The fixed amounts ({fixed} sat) exceed the balance available "
                "for the heirs ({balance} sat): the heirs with a percentage "
                "({names}) would receive nothing. Reduce the fixed amounts."
            ).format(
                fixed=sats(info["fixed"]), balance=sats(info["balance"]),
                names=", ".join(info["percent_heirs"]),
            )
        return _(
            "The fixed amounts ({fixed} sat) exceed the balance available "
            "for the heirs ({balance} sat). Reduce the fixed amounts."
        ).format(fixed=sats(info["fixed"]), balance=sats(info["balance"]))
    shares = ", ".join(
        "{} {}%".format(name, pct(share)) for name, share in info["shares"]
    )
    if info["kind"] == "fixed_scaled":
        # Text approved by the owner (CHANGELOG #82).
        return _(
            "All the heirs have a fixed amount: the will always empties the "
            "whole wallet, so the amounts are increased in proportion "
            "({shares})."
        ).format(shares=shares)
    if info["with_fixed"]:
        return _(
            "The heirs' percentages add up to {total}%: the will always "
            "empties the whole wallet, so the shares of what remains after "
            "the fixed amounts are recalculated in proportion ({shares})."
        ).format(total=pct(info["total"]), shares=shares)
    return _(
        "The heirs' percentages add up to {total}%: the will always "
        "empties the whole wallet, so the shares are recalculated in "
        "proportion ({shares})."
    ).format(total=pct(info["total"]), shares=shares)


def past_date_message(will_settings):
    """Return the "delivery date already passed" message, or ``None``.

    Uses :meth:`Will.past_delivery_date` on ``will_settings``. WHY (BUG-4,
    CHANGELOG #75): with a past date the Check used to say that an heir
    was "not covered by the current will" and that there was "no delivery
    date left to build", without naming the real cause. Never raises.
    """
    try:
        past = Will.past_delivery_date(will_settings)
    except Exception as _err:
        _logger.debug(f"past_date_message: {_err}")
        return None
    if past is None:
        return None
    return _(
        "The delivery date ({}) has already passed. Choose a future date, "
        "then press Check again."
    ).format(BalTimestamp._safe_fromtimestamp(past).strftime("%Y-%m-%d"))


def build_failure_message(reason, date_passed):
    """Return a plain-language explanation of why the will was not built.

    ``reason`` is the code that ``Heirs.buildTransactions`` records in
    ``last_build_error`` every time it gives up (the codes are listed in
    that method), or ``None``. ``date_passed`` is True when the delivery
    date of the settings is in the past (:func:`past_date_message`). The
    code becomes ONE specific sentence telling the user what to fix.

    WHY: the Check used to print the same three "possible reasons" (low
    balance / dust shares / check-alive after the delivery date) whenever
    the build returned nothing. The owner hit a real case where all three
    were false - the actual cause was that no will-executor was usable,
    which the list did not even mention - so the message actively misled.
    When the reason is unknown we now SAY that it is unknown and list what
    to check, instead of asserting three guesses as if they were the only
    possibilities.
    """
    # "No future date" with a delivery date in the past: the past date is
    # the cause, already explained on the "Checking your will" row (#75).
    if reason == "NO_FUTURE_DATE" and date_passed:
        return _("Skipped")

    messages = {
        "NO_HEIRS": _(
            "No heirs: add at least one heir before building the will."
        ),
        "NO_UTXO": _(
            "The wallet has no spendable funds, so no inheritance "
            "transaction can be created."
        ),
        "NO_WILLEXECUTOR_USABLE": _(
            "No usable will-executor: none of the servers in the list is "
            "both selected and valid. Open the will-executor settings, "
            "select at least one server and check that it is reachable."
        ),
        "NO_FUTURE_DATE": _(
            "No delivery date left to build: every heir's date is already "
            "covered by the existing will. Choose a later delivery date, "
            "or change an heir's date."
        ),
        "WILLEXECUTOR_FEE": _(
            "The amount to send must cover the miner fees plus this "
            "will-executor's fee, and the wallet balance is not enough: "
            "select cheaper will-executors, or add funds to the wallet."
        ),
        "WILLEXECUTOR_FEE_TOO_HIGH": _(
            "A will-executor asks for more than the maximum fee you "
            "allowed: raise the maximum fee in the settings, or select a "
            "cheaper will-executor."
        ),
        "TX_BUILD_FAILED": _(
            "The inheritance transactions could not be assembled from the "
            "available funds (the balance may not cover the miner fees)."
        ),
        "FIXED_EXCEEDS_BALANCE": _(
            "The heirs' fixed amounts exceed the wallet balance left after "
            "the miner and will-executor fees: reduce the fixed amounts."
        ),
        "WILLEXECUTOR_TX_ERROR": _(
            "An unexpected error stopped the transactions being prepared "
            "for a will-executor, so it was skipped. Try again, or select "
            "a different will-executor."
        ),
    }

    if reason in messages:
        return "{}\n\n{}".format(messages[reason], _("Skipped"))

    return "{}\n\n{}".format(
        _(
            "Could not build the will, and the exact cause could not be "
            "determined. Please check that:\n"
            "- the wallet balance covers the miner and will-executor fees,\n"
            "- each heir's share is above the minimum (dust limit),\n"
            "- the Check Alive date is EARLIER than the delivery date,\n"
            "- at least one will-executor is selected and reachable."
        ),
        _("Skipped"),
    )
