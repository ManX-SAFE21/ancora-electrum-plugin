"""The one date style the owner sees (CHANGELOG #134, owner decision).

A delivery date is a UTC moment (the nLockTime of the will). Everywhere the
owner reads one it is written the same way:

* long: "13 October 2028, 00:00 UTC" (:func:`utc_text`), optionally with a
  second line "In your time zone: 13 October 2028, 02:00"
  (:func:`local_line`);
* short, for tight places such as the version headers of the Will tab:
  "13 Oct 2028, 00:00 UTC" (``utc_text(ts, short=True)``);
* the day alone: "13 October 2028" (:func:`utc_day`).

Technical columns (the Locktime column, the txids) keep their own format.

The month names are msgids, so they follow the plugin's language like every
other text (no Qt locale needed, so the core can use them too). The short
month is the first three letters of the translated name: right for the
shipped languages ("Oct", "ott", "oct"; "May", "mag", "may"). The order of
day, month and year and the "ore" of Italian come from msgids too
("{day} {month} {year}", "{date}, {time}").

Pure: standard library only, no Qt, the clock is never read. 2038-safe
(``wizard_date.utc_moment``: "1970 + seconds", never ``fromtimestamp``).
"""

from ..i18n import N_, _
from . import wizard_date

#: The month names (msgids), January first.
MONTHS = (
    N_("January"), N_("February"), N_("March"), N_("April"), N_("May"),
    N_("June"), N_("July"), N_("August"), N_("September"), N_("October"),
    N_("November"), N_("December"),
)


def month_name(month):
    """The translated name of ``month`` (1..12): "October", "ottobre"."""
    return _(MONTHS[int(month) - 1])


def month_short(month):
    """The short name of ``month``: the first three letters of the name."""
    return month_name(month)[:3]


def moment_day(moment, short=False):
    """``"13 October 2028"`` (``short``: "13 Oct 2028") of a ``datetime``,
    its own day in its own zone."""
    if short:
        return _("{day} {month_short} {year}").format(
            day=moment.day, month_short=month_short(moment.month), year=moment.year)
    return _("{day} {month} {year}").format(
        day=moment.day, month=month_name(moment.month), year=moment.year)


def moment_text(moment, short=False):
    """``"13 October 2028, 02:00"``: the day and the hour of ``moment``."""
    return _("{date}, {time}").format(
        date=moment_day(moment, short),
        time="{:02d}:{:02d}".format(moment.hour, moment.minute),
    )


def utc_day(ts, short=False):
    """``"13 October 2028"``: the UTC day of ``ts`` (the will's day)."""
    return moment_day(wizard_date.utc_moment(ts), short)


def utc_text(ts, short=False):
    """``"13 October 2028, 00:00 UTC"`` (``short``: "13 Oct 2028, 00:00
    UTC"): the moment saved in the will. "UTC" is not translated."""
    return moment_text(wizard_date.utc_moment(ts), short) + " UTC"


def utc_short(ts):
    """``"13 Oct 2028, 00:00 UTC"``: the short form (``utc_text`` with
    ``short=True``), as a one-argument function for the callers that take
    a date formatter."""
    return utc_text(ts, short=True)


def local_text(ts, tz=None):
    """``"13 October 2028, 02:00"``: ``ts`` in zone ``tz`` (None: the
    computer's own zone, see ``wizard_date``)."""
    return moment_text(wizard_date.local_moment(ts, tz))


def local_short(ts, tz=None):
    """``"13 Oct 2028, 02:00"``: ``ts`` in zone ``tz`` (None: the
    computer's), the short form (#182: the verification times, which must
    fit next to the "Exclude" button)."""
    return moment_text(wizard_date.local_moment(ts, tz), short=True)


def local_line(ts, tz=None):
    """``"In your time zone: 13 October 2028, 02:00"``: the optional second
    line under a long date."""
    return _("In your time zone:") + " " + local_text(ts, tz)


def delivery_change(in_force, new):
    """How ``new`` moves the delivery of the will in force (``in_force``).

    "moved earlier by 2 years and 4 months", "moved later by 3 months —
    needs an invalidation transaction on the blockchain: …", or "same
    date".
    The span is ``bal.core.future.span_text`` (calendar months, the month
    arithmetic of the delivery dates; days only under a year). Moving the
    delivery earlier is free (the plugin pre-empts the will in force with an
    earlier one); moving it later needs the old will invalidated on-chain.
    Owner (#135 follow-up): never "paid": the invalidation sends the will's
    coins to a new address of the SAME wallet (``Will.invalidate_will``),
    the bitcoin stay the owner's and only the normal network fee is paid.
    """
    # Imported here: bal.core.future imports bal.core.summary, which uses
    # this module.
    from .future import span_text

    in_force, new = int(in_force), int(new)
    if new < in_force:
        return _("moved earlier by {}").format(span_text(new, in_force))
    if new > in_force:
        return _("moved later by {} — needs an invalidation transaction on the "
                 "blockchain: your bitcoin stay yours, you only pay the normal "
                 "network fee").format(span_text(in_force, new))
    return _("same date")


def in_force_change_lines(in_force, new):
    """The reference of the wizard's summary (owner, #134), two lines:

    "In force now: <date>" and "After signing: <date> → delivery moved
    earlier by …" (or later, with the invalidation transaction). ``[]`` when no
    will is in force (``in_force`` None) or the dates are the same: there
    is nothing to compare (owner, ZIP test).
    """
    if in_force is None or new is None or int(in_force) == int(new):
        return []
    return [
        _("In force now: {}").format(utc_text(in_force)),
        _("After signing: {}").format(utc_text(new)) + " → "
        + _("delivery {}").format(delivery_change(in_force, new)),
    ]
