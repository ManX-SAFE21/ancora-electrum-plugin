"""The staged deliveries in the engine: the heir rows of each delivery (#168).

A wallet may hold a table of up to three delivery dates, the same for every
heir, each heir with a share of the whole estate at each date
(``will_settings["instalments"]``, rules in :mod:`bal.core.delivery`).
The stored heirs keep ONE row per person; this module derives, in memory
only, the rows the build and the check need: "Luca" for delivery 1,
"Luca§2" and "Luca§3" for the next ones (:func:`effective_heirs`). The
build (``Heirs.get_transactions``, unchanged) then makes the chain: each
delivery spends the change of the one before.

A wallet without a table is untouched: every function here gives back
``host.heirs`` itself. The table replaces BAL's hidden global option
``bal_enable_multiverse``, which is no longer read (owner decision D7).

No Qt, no ``bal.cli`` (``bal/engine`` rules, see ``bal/engine/__init__``).
"""

from electrum.logging import Logger, get_logger

from ..core import delivery
from ..core.heirs import Heirs
from ..i18n import _

_logger = get_logger(__name__)


class DeliveryHeirs(Heirs):
    """The heir rows of every delivery, in memory only (never saved).

    A :class:`bal.core.heirs.Heirs`, because the build, the amount checks
    and the will check call its methods (``get_transactions``,
    ``normalize_perc`` ...). Unlike the wallet's ``Heirs`` it does not read
    the wallet file and :meth:`save` does nothing: the derived rows must
    never replace the stored person rows.
    """

    def __init__(self, rows, wallet):
        """``rows``: ``{row name: [address, amount, date]}``
        (:func:`bal.core.delivery.effective_rows`)."""
        Logger.__init__(self)
        self.db = None
        self.wallet = wallet
        self.last_build_error = None
        dict.update(self, rows)

    def save(self):
        """Nothing: these rows live only for one build or check."""


def has_table(host):
    """True when the wallet's settings hold a delivery table.

    A value that cannot be read counts as a table: :func:`check_table`
    then stops the flows (fail closed) instead of building one delivery
    that pays everything at the first date.
    """
    try:
        return bool(delivery.instalments_of(getattr(host, "will_settings", None)))
    except ValueError:
        return True


def check_table(host):
    """The wallet's table, checked against its heirs; ``[]`` without one.

    The shape, the heirs and the 100 % total are checked at every flow
    (owner decision 3: an heir added, removed or renamed without the table
    being updated stops everything, never a guess). Not the "after today"
    rule (delivery 1 may be past) nor the 28 days (the automatic one-day
    anticipations may shorten them, owner decision 5): those are checked
    when the owner changes the table or the date. Delivery 1 is on the
    wallet's date (:func:`bal.core.delivery.table_rows`). Raises
    :class:`bal.core.delivery.InstalmentsMismatch`.
    """
    try:
        rows = delivery.table_rows(host.will_settings)
        if not rows:
            return []
        names = delivery.payee_names(host.heirs)
        for name in names:
            if delivery.has_separator(name):
                raise ValueError(
                    "an heir name holds {!r}: {!r}".format(delivery.INSTALMENT_SEP, name))
        return delivery.validate_instalments(rows, names, None, gap=False)
    except ValueError as e:
        _logger.warning(f"delivery table does not fit the heirs: {e}")
        raise delivery.InstalmentsMismatch(
            _(
                "The table of the deliveries no longer matches the heirs, so "
                "nothing was built or signed. Update the table of the "
                "deliveries. Details: {}"
            ).format(e),
            reason=str(e),
        ) from None


def effective_heirs(host):
    """The heir rows the build and the check use.

    Without a table: ``host.heirs`` itself (nothing changes). With one: a
    :class:`DeliveryHeirs` of :func:`bal.core.delivery.effective_rows`,
    after :func:`check_table` (which may raise).
    """
    rows = check_table(host) if has_table(host) else []
    if not rows:
        return host.heirs
    return DeliveryHeirs(delivery.effective_rows(host.heirs, rows), host.wallet)


def first_delivery_heirs(host):
    """The heir rows of delivery 1 only (``host.heirs`` without a table).

    For the comparisons that look at the first delivery of the will in
    force (``will_changes.side_from_items``).
    """
    heirs = effective_heirs(host)
    if heirs is host.heirs:
        return heirs
    return {
        name: value for name, value in heirs.items()
        if delivery.instalment_number(name) == 1
    }


def delivery_dates(willitems):
    """``{n: earliest locktime}`` of the VALID items of each delivery.

    The delivery of an item is read from its heir-row names
    (:func:`bal.core.delivery.instalment_number`); the will-executor fee
    rows and the message rows (plain names, delivery 1) are not counted
    beyond that.
    """
    dates = {}
    for w in (willitems or {}).values():
        if not w.get_status("VALID"):
            continue
        numbers = {
            delivery.instalment_number(name)
            for name in (w.heirs or {})
            if not str(name).startswith('w!ll3x3c"')
        }
        if len(numbers) != 1:
            continue
        n = numbers.pop()
        locktime = int(w.tx.locktime)
        if n not in dates or locktime < dates[n]:
            dates[n] = locktime
    return dates


def sync_later_deliveries(host):
    """Move deliveries 2 and 3 of the table to the rebuilt will's dates.

    F1 of #166 (owner: fixed in M3): see
    :func:`bal.core.delivery.anticipated_rows`; dates only move earlier.
    The table is replaced whole (a stored list's inner dicts are not
    tracked by the wallet file). Delivery 1 follows ``locktime``
    (``build.sync_delivery_date``). Returns the new table, or None.
    """
    d = getattr(host, "will_settings", None)
    if not has_table(host):
        return None
    try:
        rows = delivery.instalments_of(d)
    except ValueError:
        return None
    new = delivery.anticipated_rows(rows, delivery_dates(host.willitems))
    if new is None:
        return None
    d[delivery.INSTALMENTS_KEY] = new
    _logger.info(
        "delivery table: later deliveries follow the rebuilt will: {}".format(
            [row["locktime"] for row in new]
        )
    )
    return new
