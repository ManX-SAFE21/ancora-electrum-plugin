"""How far a will in several deliveries has gone on the chain (CHANGELOG #201).

A will in several deliveries (a Multiverse) is a tree of copies: each
copy of delivery 1 spends the wallet's coins, each copy of delivery 2
spends the change of one copy of delivery 1, and so on
(:mod:`bal.core.futures`). Once a copy of delivery 1 is executed, two
situations can follow:

* the plan is RUNNING: the copies of the later deliveries spend the
  change of the executed copy and wait at the will-executors for their
  date. Nothing must be prepared again: the executed delivery is done,
  not an heir "added" to the will (:func:`executed_deliveries`);
* the plan was STOPPED: the change that paid the later deliveries was
  spent by a transaction that is not a copy of the will (the owner's
  Invalidate, or any other spend), so no copy can deliver any more
  (:func:`cancelled_rest`).

Owner's rule (2026-10-29): after delivery 1 the plugin builds, signs and
sends nothing on its own; a plan the owner stopped is not prepared again.

Read from the wallet only (``wallet.adb``), never from the stored status
flags: they may not be refreshed yet when a flow starts (an invalidation
the wallet has just registered), and a stale flag must never stop the
flows. When something cannot be read, the answer is the one that changes
nothing (no delivery executed, plan not stopped).

No Qt, nothing stored.
"""

from electrum.address_synchronizer import TX_HEIGHT_FUTURE, TX_HEIGHT_LOCAL

from . import delivery

#: The reserved name prefix of the will-executor fee outputs (not a person).
_EXECUTOR_HEIR = 'w!ll3x3c"'


def _on_chain(wallet, txid):
    """True when the wallet has ``txid`` in a block or in the mempool.

    A transaction kept only in the wallet's local history (a will copy
    never published, or one Electrum dropped from the mempool) is not on
    the chain: Electrum keeps it as LOCAL.
    """
    try:
        if wallet.adb.get_transaction(txid) is None:
            return False
        height = wallet.adb.get_tx_height(txid).height()
    except Exception:
        return False
    return height not in (TX_HEIGHT_LOCAL, TX_HEIGHT_FUTURE)


def delivery_of(item):
    """The delivery a copy pays: the highest delivery number of its person
    and message rows ("Luca§2" is delivery 2, a plain name delivery 1);
    ``None`` for a copy with no such row."""
    numbers = [
        delivery.instalment_number(name)
        for name in (getattr(item, "heirs", None) or {})
        if not str(name).startswith(_EXECUTOR_HEIR)
    ]
    return max(numbers) if numbers else None


def plan_outputs(wid, number, willitems):
    """The outputs of copy ``wid`` (delivery ``number``) that the copies of
    delivery ``number + 1`` spend: the change that pays the rest of the
    plan. An empty set when no copy of the next delivery hangs on it.

    WHY: a copy executed is part of THIS plan only when the next delivery
    hangs on it. A will executed earlier, with a new will in several
    deliveries built on the coin that came back (#200), must not count as
    "delivery 1 done" of the new will: the copies spending that coin are
    the new will's delivery 1, not a delivery 2.
    """
    outs = set()
    for item in willitems.values():
        if delivery_of(item) != number + 1:
            continue
        try:
            inputs = list(item.tx.inputs())
        except Exception:
            continue
        for txin in inputs:
            if txin.prevout.txid.hex() == wid:
                outs.add(txin.prevout.out_idx)
    return outs


def executed_deliveries(willitems, wallet, count):
    """The deliveries of the plan (``count`` rows) with a copy in a block
    or in the mempool: a set of delivery numbers. A copy counts when the
    next delivery hangs on it (:func:`plan_outputs`), or when it is the
    last delivery. Empty without a usable wallet."""
    if not wallet or not getattr(wallet, "adb", None):
        return set()
    willitems = willitems or {}
    done = set()
    for wid, item in willitems.items():
        number = delivery_of(item)
        if not number or not _on_chain(wallet, wid):
            continue
        if number >= count or plan_outputs(wid, number, willitems):
            done.add(number)
    return done


def done_deliveries(willitems, wallet, count):
    """How many deliveries are done while the plan still has some left:
    ``n`` when deliveries 1..n all have an executed copy and ``n < count``
    (``count``: the rows of the table), else 0.

    0 without a table (one delivery: nothing changes), and 0 when every
    delivery was executed (the will is over: nothing changes either).
    """
    if count < 2:
        return 0
    done = executed_deliveries(willitems, wallet, count)
    n = 0
    while n + 1 in done:
        n += 1
    return n if n < count else 0


def _spent_by_other(wid, outs, willitems, wallet):
    """True when one of the outputs ``outs`` of copy ``wid`` (the change
    that pays the rest of the plan, :func:`plan_outputs`) is spent by a
    transaction that is not a copy of the will. Electrum's ``get_spender``
    ignores local spenders."""
    for index in outs:
        try:
            spender = wallet.adb.get_spender("{}:{}".format(wid, index))
        except Exception:
            continue
        if spender and spender not in willitems:
            return True
    return False


def _signed(item):
    """True when the copy is fully signed; False for a draft or when it
    cannot be read."""
    try:
        return bool(item.tx.is_complete())
    except Exception:
        return False


def _live_items(willitems, wallet):
    """The copies that could still be mined: signed, not on the chain
    themselves, and every coin they spend still unspent (a coin on the
    chain that no transaction spends, or the output of another copy that
    is itself live).

    In doubt a copy is NOT live, so the hold of :func:`cancelled_rest`
    applies (owner's rule, 2026-10-30: in doubt, stop; a hold builds,
    signs and sends nothing). WHY: the owner's wallet of 2026-10-30 held
    1037 copies of earlier wills; one copy whose coin the wallet did not
    know counted as live, the hold never applied, and the Check prepared
    the cancelled rest again and asked to sign it. So:

    * a draft (not signed) cannot be mined: not live. The draft the wrong
      Check left in that wallet must not keep the hold away either;
    * a coin whose transaction the wallet does not know (an old copy whose
      parent was purged) or that cannot be read: the copy is not live;
    * a copy whose inputs cannot be read: not live.
    """
    memo = {}

    def live(wid, stack):
        """Memoised; a loop of copies is not live."""
        if wid in memo:
            return memo[wid]
        if wid in stack:
            return False
        item = willitems[wid]
        result = False
        if not _on_chain(wallet, wid) and _signed(item):
            try:
                inputs = list(item.tx.inputs())
            except Exception:
                inputs = []
            # No input that can be read: in doubt, not live.
            result = bool(inputs)
            for txin in inputs:
                parent = txin.prevout.txid.hex()
                outpoint = "{}:{}".format(parent, txin.prevout.out_idx)
                if parent in willitems and not _on_chain(wallet, parent):
                    if not live(parent, stack | {wid}):
                        result = False
                        break
                    continue
                try:
                    if wallet.adb.get_transaction(parent) is None:
                        result = False  # a coin the wallet does not know
                        break
                    spender = wallet.adb.get_spender(outpoint)
                except Exception:
                    result = False  # cannot be judged: in doubt, not live
                    break
                if spender and spender != wid:
                    result = False
                    break
        memo[wid] = result
        return result

    return [wid for wid in willitems if live(wid, frozenset())]


def cancelled_rest(willitems, wallet, count):
    """The delivery executed before the rest of the plan was cancelled,
    or ``None``.

    ``count``: the rows of the table (fewer than 2: ``None``). The rest is
    cancelled when a copy of delivery ``n < count`` is in a block or in
    the mempool, the change that pays the next delivery
    (:func:`plan_outputs`) is spent by a transaction that is not a copy of
    the will, and no copy of the will could still be mined
    (:func:`_live_items`): a will signed after that spend is a will in
    force, and the flows go on as usual (a draft is not). With several such copies the
    highest delivery is returned.
    """
    if count < 2 or not wallet or not getattr(wallet, "adb", None):
        return None
    willitems = willitems or {}
    found = None
    for wid, item in willitems.items():
        number = delivery_of(item)
        if not number or number >= count or not _on_chain(wallet, wid):
            continue
        outs = plan_outputs(wid, number, willitems)
        if outs and _spent_by_other(wid, outs, willitems, wallet):
            found = max(found or 0, number)
    if found is None or _live_items(willitems, wallet):
        return None
    return found
