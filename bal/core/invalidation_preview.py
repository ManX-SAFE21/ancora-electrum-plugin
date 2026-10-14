"""What an invalidation does, shown before its password (#135 follow-up).

Owner (ZIP test of #135): before signing the invalidation of a postpone
the Check window only asked the password "Invalidate your old will"; the
owner must see what is invalidated first. "Tools > Invalidate" already
opens Electrum's transaction window; the Check now shows a box with:

* the amount moved and where it goes: ``Will.invalidate_will`` spends the
  will's coins to ONE change address of the SAME wallet (a new address of
  the owner's wallet): the bitcoin stay the owner's;
* the network fee (sat) and its rate (sat/vB);
* the wills it makes useless: the versions of the Will tab
  (``will_versions.build_history``) that the will-executors hold or could
  still execute and that spend a coin of the invalidation, with their
  number, delivery date and how many copies the will-executors received.

Everything is derived from the built (unsigned) transaction and the will
items each time: nothing is stored. Qt-free (``bal/core`` rule), read-only:
it never signs, sends or changes anything.
"""

from . import will_versions

# The versions an invalidation really ends: those the will-executors hold
# (in force, or replaced but still held and executable) and a partially
# signed one (a co-signer may still complete it). Drafts and versions
# already invalidated or executed are left out: nothing to make useless.
_ENDED_STATES = (
    will_versions.VERSION_IN_FORCE,
    will_versions.VERSION_HELD,
    will_versions.VERSION_PARTIALLY_SIGNED,
)


def short_address(address):
    """``tb1qxy…k3m9z0``: the first 8 and the last 6 characters."""
    address = str(address or "")
    if len(address) <= 18:
        return address
    return "{}…{}".format(address[:8], address[-6:])


def _prevouts(tx):
    """The outpoints (``"txid:n"``) spent by ``tx``."""
    return {i.prevout.to_str() for i in tx.inputs()}


def ended_wills(tx, will, wallet=None, history=None):
    """``[(number, locktime, copies)]`` of the wills ``tx`` makes useless.

    ``will``: ``{wid: item}``; ``history``: an already built
    :func:`bal.core.will_versions.build_history` (built here from ``will``
    and ``wallet`` when None). A version counts when one of its items
    spends a coin that ``tx`` spends and its state is in force, replaced
    but held, or partially signed. ``copies``: the copies the
    will-executors received (``Version.executors_sent``). Earliest first.
    """
    will = dict(will or {})
    if history is None:
        history = will_versions.build_history(will, wallet)
    spent = _prevouts(tx)
    found = []
    for version in history.versions:
        if version.state not in _ENDED_STATES:
            continue
        hit = False
        for wid in version.items:
            item = will.get(wid)
            if item is None:
                continue
            try:
                if _prevouts(item.tx) & spent:
                    hit = True
                    break
            except Exception:
                continue
        if hit:
            found.append((version.number, version.locktime, version.executors_sent))
    found.sort(key=lambda f: (f[1] is None, f[1] or 0, f[0] or 0))
    return found


def invalidation_facts(tx, will, wallet=None, history=None):
    """The facts of the invalidation ``tx`` for its box, or None.

    A dict: ``amount`` (sat, the coins moved: the inputs), ``address`` (the
    destination, a change address of this wallet), ``received`` (sat, what
    arrives there), ``mine`` (True when ``wallet.is_mine`` says the
    destination is this wallet's, None without a wallet: checked, not
    assumed), ``fee`` (sat), ``rate`` (sat/vB, one decimal) and ``wills``
    (:func:`ended_wills`). None when ``tx`` cannot be read (the box is then
    not shown; the flow does not change).
    """
    try:
        outputs = tx.outputs()
        amount = int(tx.input_value())
        received = int(sum(o.value for o in outputs))
        fee = amount - received
        size = int(tx.estimated_size()) or 1
        address = str(outputs[0].address) if outputs else ""
        mine = None
        if wallet is not None:
            mine = all(bool(wallet.is_mine(o.address)) for o in outputs) if outputs else False
        return {
            "amount": amount,
            "address": address,
            "mine": mine,
            "received": received,
            "fee": fee,
            "rate": round(fee / size, 1),
            "wills": [list(w) for w in ended_wills(tx, will, wallet, history)],
        }
    except Exception:
        return None
