"""
bal.core.will
=============

The "will": the set of time-locked inheritance transactions plus all the logic
to keep it coherent over time.

Two classes live here:

    * :class:`Will`     - a namespace of static methods operating on a *will*
                          dictionary (mapping ``txid -> WillItem``): building
                          the parent/child tree, anticipating locktimes,
                          detecting replaced/invalidated/confirmed entries,
                          validating that the will still matches the heirs and
                          will-executors, and building an "invalidation"
                          transaction.
    * :class:`WillItem` - a single will transaction together with its status
                          flags, heirs, will-executor and fee.

Separation of concerns
-----------------------
The original ``WillItem`` carried a ``get_color()`` method returning hard-coded
hex colours for the GUI.  That was pure presentation living inside the core
logic, so it has been **moved** to ``bal.gui.qt.theme.status_color(will_item)``.
The status flags themselves (the source of truth) stay here; only the mapping
"status -> colour" now lives in the GUI layer.  No behaviour changed.
"""

import math
import threading
from datetime import datetime, timezone

from electrum.address_synchronizer import TX_HEIGHT_FUTURE, TX_HEIGHT_LOCAL
from electrum.logging import Logger, get_logger
from electrum.transaction import (
    PartialTransaction,
    PartialTxInput,
    PartialTxOutput,
    Transaction,
    TxOutpoint,
    tx_from_any,
)
from electrum.util import (
    UnrelatedTransactionException,
    bfh,
)

from ..i18n import N_, _
from . import will_state
from .delivery import instalment_number
from .heirs import WillExecutorFeeTooHighException, address_key, is_op_return_address
from .util import Util, copy_structure
from .willexecutors import Willexecutors

MIN_LOCKTIME = 1
MIN_BLOCK = 1
# Confirmation target (in blocks) for the on-chain INVALIDATION transaction.
# WHY: the invalidation is sent TODAY (it spends the will's coins so the old
# pre-signed inheritance txs become unusable), so it must pay the CURRENT
# network fee for a quick confirmation - not ``baltx_fees``, which is a
# deliberately high rate reserved for inheritance txs that may be broadcast
# years from now. 2 blocks is Electrum's own default target (owner decision).
INVALIDATION_FEE_ETA_BLOCKS = 2
_logger = get_logger(__name__)


class Will:
    @staticmethod
    def get_children(will, willid):
        out = []
        for _id in will:
            inputs = will[_id].tx.inputs()
            for idi in range(0, len(inputs)):
                _input = inputs[idi]
                if _input.prevout.txid.hex() == willid:
                    out.append([_id, idi, _input.prevout.out_idx])
        return out

    # build a tree with parent transactions
    @staticmethod
    def add_willtree(will):
        """(Re)build the parent/child links between the will transactions.

        Every item gets ``children`` (the will txs spending its outputs) and,
        when it spends the output of another will tx, a ``father``.

        WHY the reset below: ``father`` is persisted in the wallet (the
        ``"Father"`` field of ``WillItem.to_dict``) and was only ever set,
        never cleared. When the father tx was later removed from the will
        (rebuild, merge, replacement) the child kept pointing to a txid that
        no longer exists, and ``check_invalidated`` crashed with a
        ``KeyError`` on that txid (reported on a real wallet: the Check
        wizard and the window refresh both failed). The tree is therefore
        always recomputed from scratch from the transactions themselves.
        """
        for willid in will:
            will[willid].father = None
        for willid in will:
            will[willid].children = Will.get_children(will, willid)
            for child in will[willid].children:
                if not will[child[0]].father:
                    will[child[0]].father = willid

    @staticmethod
    def only_valid(will):
        for k, v in will.items():
            if v.get_status("VALID"):
                yield k

    @staticmethod
    def needs_server_check(w):
        """Return True if ``w`` should be queried on its will-executor server
        when the user presses Check (or on Electrum close).

        A will is queried only when it is VALID, has a will-executor assigned,
        was actually PUSHED (sent), and is not yet CHECKED.  The ``PUSHED``
        condition is essential: querying the server for a will that was *never*
        sent would make the server (correctly) answer "I don't have this tx",
        which ``WillItem.set_check_willexecutor`` then records as CHECK_FAIL.
        A freshly signed-but-not-sent will would therefore turn red, even though
        it is merely "signed, not sent" (which must stay blue / #2bc8ed, as in
        the original BAL behaviour).  Restricting the check to PUSHED wills
        matches the original ``check()`` logic and avoids that false failure.
        """
        return bool(
            w.get_status("VALID")
            and w.we
            and w.get_status("PUSHED")
            and not w.get_status("CHECKED")
        )

    @staticmethod
    def get_tx_from_any(x):
        try:
            a = str(x)
            return tx_from_any(a)

        except Exception as e:
            raise e

        return x

    @staticmethod
    def add_info_from_will(will, wid, wallet):
        if isinstance(will[wid].tx, str):
            will[wid].tx = Will.get_tx_from_any(will[wid].tx)
        if wallet:
            will[wid].tx.add_info_from_wallet(wallet)
        for txin in will[wid].tx.inputs():
            txid = txin.prevout.txid.hex()
            if txid in will:
                change = will[txid].tx.outputs()[txin.prevout.out_idx]
                txin._trusted_value_sats = change.value
                try:
                    txin.script_descriptor = change.script_descriptor
                except Exception:
                    pass
                txin.is_mine = True
                txin._TxInput__address = change.address
                txin._TxInput__scriptpubkey = change.scriptpubkey
                txin._TxInput__value_sats = change.value
                txin._trusted_value_sats = change.value

    @staticmethod
    def normalize_will(will, wallet=None, others_inputs=None):
        others_input = others_inputs if others_inputs is not None else {}
        to_delete = []
        to_add = {}
        # add info from wallet
        willitems = {}
        for wid in will:
            Will.add_info_from_will(will, wid, wallet)
            willitems[wid] = WillItem(will[wid], wallet=wallet)
        will = willitems
        errors = {}
        for wid in will:

            txid = will[wid].tx.txid()

            if txid is None:
                _logger.error("##########")
                _logger.error(wid)
                _logger.error(will[wid])
                _logger.error(will[wid].tx.to_json())

                _logger.error("txid is none")
                will[wid].set_status("ERROR", True)
                errors[wid] = will[wid]
                continue

            if txid != wid:
                outputs = will[wid].tx.outputs()
                ow = will[wid]
                ow.normalize_locktime(others_input)
                will[wid] = ow.copy()

                for i in range(0, len(outputs)):
                    Will.change_input(
                        will, wid, i, outputs[i], others_input, to_delete, to_add
                    )

                to_delete.append(wid)
                to_add[ow.tx.txid()] = ow.to_dict()

        # for eid, err in errors.items():
        #    new_txid = err.tx.txid()

        for k, w in to_add.items():
            will[k] = w

        for wid in to_delete:
            if wid in will:
                del will[wid]

    @staticmethod
    def new_input(txid, idx, change):
        prevout = TxOutpoint(txid=bfh(txid), out_idx=idx)
        inp = PartialTxInput(prevout=prevout)
        inp._trusted_value_sats = change.value
        inp.is_mine = True
        inp._TxInput__address = change.address
        inp._TxInput__scriptpubkey = change.scriptpubkey
        inp._TxInput__value_sats = change.value
        return inp

    @staticmethod
    def check_anticipate(ow: "WillItem", nw: "WillItem"):
        """The locktime the new item ``nw`` needs against the old item ``ow``.

        They spend at least one coin in common. When the new will differs
        (heirs, fees, or, since #91, more coins) it must be dated one day
        EARLIER, so it is executed first and the old one becomes invalid on
        the blockchain; an identical copy keeps the old date; ``2**32``
        (above any locktime) means no constraint.
        """
        anticipate = Util.anticipate_locktime(ow.tx.locktime, days=1)
        if int(nw.tx.locktime) >= int(anticipate):
            if Util.cmp_heirs_by_values(
                ow.heirs, nw.heirs, [0, 1], exclude_willexecutors=True
            ):
                if nw.we and ow.we:
                    if ow.we["url"] == nw.we["url"]:
                        if int(ow.we["base_fee"]) > int(nw.we["base_fee"]):
                            return anticipate
                        elif int(ow.tx_fees) != int(nw.tx_fees):
                            return anticipate
                        elif Will.covers_more_coins(ow, nw):
                            # CHANGELOG #91: funds received. The new will
                            # spends the old coins plus the new ones; with the
                            # same date the executor would hold two wills
                            # executable together, and if the old one were
                            # mined the new coins would never reach the heirs.
                            # (Without a will-executor BAL already anticipated
                            # here, on the changed amounts: see below.)
                            return anticipate
                else:
                    if nw.we == ow.we:
                        if not Util.cmp_heirs_by_values(ow.heirs, nw.heirs, [0, 3]):
                            return anticipate
                        else:
                            return ow.tx.locktime
                    else:
                        return ow.tx.locktime
            else:
                return anticipate
        return 4294967295 + 1

    @staticmethod
    def covers_more_coins(ow: "WillItem", nw: "WillItem"):
        """True when ``nw`` spends every coin ``ow`` spends, and more (#91)."""
        old = {i.prevout.to_str() for i in ow.tx.inputs()}
        new = {i.prevout.to_str() for i in nw.tx.inputs()}
        return old < new

    @staticmethod
    def find_same_date_copies(will):
        """The will-executor that holds an incomplete will not dated later (#91).

        Wills built before #91: after funds were received, the executor got
        a new will with the old coins plus the new ones, dated like the old
        one (or later), and both stayed VALID. At the delivery date both can
        be executed; if the old one is mined the new coins never reach the
        heirs. Returns the URL of the first executor in that state (the
        caller then rebuilds: the new will is dated one day earlier), or
        ``None``. Items without a will-executor are left out: BAL already
        dates them earlier when the amounts change. Read-only.
        """
        valid = [w for w in will.values() if w.get_status("VALID")]
        for old in valid:
            url = (old.we or {}).get("url")
            if not url:
                continue
            for new in valid:
                if new is old or (new.we or {}).get("url") != url:
                    continue
                if Will.covers_more_coins(old, new) and int(old.tx.locktime) <= int(
                    new.tx.locktime
                ):
                    return url
        return None

    @staticmethod
    def change_input(will, otxid, idx, change, others_inputs, to_delete, to_append):
        ow = will[otxid]
        ntxid = ow.tx.txid()
        if otxid != ntxid:
            for wid in will:
                w = will[wid]
                inputs = w.tx.inputs()
                outputs = w.tx.outputs()
                found = False
                old_txid = w.tx.txid()
                # ntx = None
                for i in range(0, len(inputs)):
                    if (
                        inputs[i].prevout.txid.hex() == otxid
                        and inputs[i].prevout.out_idx == idx
                    ):
                        if isinstance(w.tx, Transaction):
                            will[wid].tx = PartialTransaction.from_tx(w.tx)
                            will[wid].tx.set_rbf(True)
                        will[wid].tx._inputs[i] = Will.new_input(wid, idx, change)
                        found = True
                if found:
                    pass

                new_txid = will[wid].tx.txid()
                if old_txid != new_txid:
                    to_delete.append(old_txid)
                    to_append[new_txid] = will[wid]
                    outputs = will[wid].tx.outputs()
                    for i in range(0, len(outputs)):
                        Will.change_input(
                            will,
                            wid,
                            i,
                            outputs[i],
                            others_inputs,
                            to_delete,
                            to_append,
                        )

    @staticmethod
    def get_all_inputs(will, only_valid=False):
        all_inputs = {}
        for w, wi in will.items():
            if not only_valid or wi.get_status("VALID"):
                inputs = wi.tx.inputs()
                for i in inputs:
                    prevout_str = i.prevout.to_str()
                    inp = [w, will[w], i]
                    if prevout_str not in all_inputs:
                        all_inputs[prevout_str] = [inp]
                    else:
                        all_inputs[prevout_str].append(inp)
        return all_inputs

    @staticmethod
    def get_all_inputs_min_locktime(all_inputs):
        all_inputs_min_locktime = {}

        for i, values in all_inputs.items():
            min_locktime = min(values, key=lambda x: x[1].tx.locktime)[1].tx.locktime
            for w in values:
                if w[1].tx.locktime == min_locktime:
                    if i not in all_inputs_min_locktime:
                        all_inputs_min_locktime[i] = [w]
                    else:
                        all_inputs_min_locktime[i].append(w)

        return all_inputs_min_locktime

    @staticmethod
    def search_anticipate_rec(will, old_inputs):
        redo = False
        to_delete = []
        to_append = {}
        new_inputs = Will.get_all_inputs(will, only_valid=True)
        for nid, nwi in will.items():
            if nwi.search_anticipate(new_inputs):
                if nid != nwi.tx.txid():
                    redo = True
                    to_delete.append(nid)
                    to_append[nwi.tx.txid()] = nwi
                    outputs = nwi.tx.outputs()
                    for i in range(0, len(outputs)):
                        Will.change_input(
                            will, nid, i, outputs[i], new_inputs, to_delete, to_append
                        )
            if nwi.search_anticipate(old_inputs):
                if nid != nwi.tx.txid():
                    redo = True

                    to_delete.append(nid)
                    to_append[nwi.tx.txid()] = nwi
                    outputs = nwi.tx.outputs()
                    for i in range(0, len(outputs)):
                        Will.change_input(
                            will, nid, i, outputs[i], new_inputs, to_delete, to_append
                        )

        for w in to_delete:
            try:
                del will[w]
            except Exception:
                pass
        for k, w in to_append.items():
            will[k] = w
        if redo:

            Will.search_anticipate_rec(will, old_inputs)

    # ------------------------------------------------------------------ #
    # Chains of wills (multiverse): one explicit dating pass (#166)
    # ------------------------------------------------------------------ #
    #: How many times one generation of a chain may be dated again before
    #: the pass gives up (each pass only moves locktimes EARLIER, so a few
    #: passes always settle a real will; the guard stops a loop on data that
    #: cannot be settled).
    CHAIN_MAX_PASSES = 20

    @staticmethod
    def chain_links(will):
        """The parent links of a freshly built ``will``: a list of
        ``(child, txin, parent, out_idx)`` (CHANGELOG #166).

        One entry for every input whose previous transaction is an item of
        ``will`` (the multiverse instalments: each later delivery spends the
        change of the one before). Read once, while the keys are still the
        txids the build gave (``bal/core/heirs.py``), and kept by OBJECT:
        the item and the input themselves, never an index, because the
        relink below re-sorts the inputs. A will with one delivery date has
        no link: ``[]``.

        Raises :class:`WillChainError` for an input that spends its own
        item, or an output its parent does not have.
        """
        links = []
        for key, item in will.items():
            for txin in item.tx.inputs():
                ptxid = txin.prevout.txid.hex()
                if ptxid not in will:
                    continue
                parent = will[ptxid]
                if parent is item or ptxid == key:
                    raise WillChainError(
                        "chain: transaction {} spends its own output".format(key[:8]))
                idx = txin.prevout.out_idx
                if idx >= len(parent.tx.outputs()):
                    raise WillChainError(
                        "chain: transaction {} spends output {} that {} does not "
                        "have".format(key[:8], idx, ptxid[:8]))
                links.append((item, txin, parent, idx))
        return links

    @staticmethod
    def _fill_chain_input(txin, parent_out):
        """Give the chain input ``txin`` the data of the output it spends.

        The same fields :meth:`add_info_from_will` writes, in memory only:
        the build's re-parse (``WillItem`` from the PSBT text) drops them for
        an input whose previous transaction is not in the wallet, and
        without them Electrum cannot compute the txid of an unsigned
        transaction (``Transaction.txid`` returns None). Nothing new is
        written into the stored PSBT.
        """
        txin._trusted_value_sats = parent_out.value
        try:
            txin.script_descriptor = parent_out.script_descriptor
        except Exception:
            pass
        txin.is_mine = True
        txin._TxInput__address = parent_out.address
        txin._TxInput__scriptpubkey = parent_out.scriptpubkey
        txin._TxInput__value_sats = parent_out.value

    @staticmethod
    def _relink(child, txin, parent, idx):
        """Point ``txin`` of ``child`` at the CURRENT txid of ``parent``.

        WHY in place: the input keeps its sequence number and every other
        field, so a relinked child is the transaction a fresh build of the
        same chain would give (the #122 reuse and the "second Check is
        clean" rule need that). The inputs are then sorted as Electrum's
        ``PartialTransaction.from_io`` sorts them (BIP69), which also clears
        the cached txid. A previous transaction attached to the input is
        dropped when it is not the new parent (Electrum refuses an input
        whose ``utxo`` has another txid). Returns True when the input moved.
        """
        ptxid = parent.tx.txid()
        if ptxid is None:
            raise WillChainError(
                "chain: the txid of {} cannot be computed".format(
                    (parent._id or "?")[:8]))
        if txin.prevout.txid.hex() == ptxid and txin.prevout.out_idx == idx:
            return False
        utxo = getattr(txin, "_utxo", None)
        if utxo is not None and utxo.txid() != ptxid:
            txin._utxo = None
        txin.prevout = TxOutpoint(txid=bfh(ptxid), out_idx=idx)
        Will._fill_chain_input(txin, parent.tx.outputs()[idx])
        child.tx.BIP69_sort(outputs=False)
        return True

    @staticmethod
    def _real_heir_amounts(item):
        """``{(address, amount)}`` the item really pays its heirs (#166).

        Only the real amounts (index 3, an int) of the heirs that are not
        the will-executor pseudo-heirs; the DUST rows and stored errors are
        left out.
        """
        out = set()
        for name, heir in (item.heirs or {}).items():
            if str(name).startswith('w!ll3x3c"'):
                continue
            try:
                amount = heir[3]
            except (IndexError, TypeError):
                continue
            if isinstance(amount, int) and not isinstance(amount, bool):
                out.add((str(heir[0]), amount))
        return out

    @staticmethod
    def _same_url(a, b):
        """True when items ``a`` and ``b`` are for the same will-executor
        (both with one and the same URL, or both without: the backup)."""
        return (a.we or {}).get("url") == (b.we or {}).get("url")

    @staticmethod
    def _chain_set_anticipate(item, ow):
        """Date the new chain item ``item`` against the old item ``ow``.

        Today's rule first (:meth:`WillItem.set_anticipate`, which applies
        :meth:`check_anticipate`). Then the multiverse rule of #166 (owner
        decision D6: changing the deliveries while a will is in force gives
        a new will one day earlier): for the same will-executor, when the
        heirs' REAL amounts differ, one day before ``ow``. WHY: in a chain a
        share of a later delivery changes the amounts paid by the earlier
        ones (the percentages are shared out over every later delivery),
        while the heir rows they carry stay the same, so
        :meth:`check_anticipate` keeps the old date and the old and the new
        transaction could both be executed on the same coins on the same
        day. Returns True when the locktime moved.
        """
        moved = item.set_anticipate(ow)
        if (
            Will._same_url(item, ow)
            and Will._real_heir_amounts(item) != Will._real_heir_amounts(ow)
        ):
            earlier = int(Util.anticipate_locktime(ow.tx.locktime, days=1))
            if int(item.tx.locktime) > earlier:
                item.tx.locktime = earlier
                moved = True
        return moved

    @staticmethod
    def _chain_generations(will, links):
        """``{id(item): n}``: 0 for the first delivery, 1 for the items
        spending its change, and so on. Raises :class:`WillChainError` on a
        loop."""
        parents = {}
        for child, _txin, parent, _idx in links:
            parents.setdefault(id(child), []).append(parent)
        gen = {}

        def depth(item, seen):
            """The generation of ``item`` (``seen``: the path, for loops)."""
            key = id(item)
            if key in gen:
                return gen[key]
            if key in seen:
                raise WillChainError("chain: a loop between the transactions")
            seen.add(key)
            ps = parents.get(key, [])
            value = 0 if not ps else 1 + max(depth(p, seen) for p in ps)
            gen[key] = value
            return value

        for item in will.values():
            depth(item, set())
        return gen

    @staticmethod
    def date_chain(new_will, links, references, old_will):
        """Date and relink a freshly built CHAIN against the will in force.

        CHANGELOG #166. Used by :meth:`update_will` instead of
        :meth:`search_anticipate_rec` when the new will has delivery
        instalments (``links`` from :meth:`chain_links` not empty). The old
        relinking (``change_input`` over the dict, by keys) pointed every
        child at its own txid and left stale keys: every rebuild of a chain
        that moved a date failed or saved a broken will.

        ``new_will``: ``{txid: WillItem}`` of the build, changed in place;
        ``references``: the items the new will is dated against
        (:meth:`anticipation_references`); ``old_will``: the will so far.

        1. Every chain input gets the data of the output it spends, so each
           item's txid can be computed (and equals its key).
        2. Generation by generation (first delivery, then the next...):
           the children are pointed at their parents' FINAL txids, then the
           generation is dated to a fixed point with today's rules
           (:meth:`WillItem.set_anticipate`) plus the rule of
           :meth:`_chain_set_anticipate`. The first delivery is dated against
           the old items that spend the same coins; a later one only against
           old items that spend the same parent output (an old child of a
           parent kept unchanged): when its parent changed, the new parent,
           dated earlier, already makes the old one and its children
           unexecutable.
        3. Checks that refuse the will (:class:`WillChainError`, nothing is
           changed in the will in force) instead of saving a chain that
           could be executed wrongly: see :meth:`_check_chain`.
        4. The dict is re-keyed once by the final txids, in the same order
           (parents before children), and each item's ``_id`` follows.
        """
        for _child, txin, parent, idx in links:
            Will._fill_chain_input(txin, parent.tx.outputs()[idx])
        for key, item in new_will.items():
            if item.tx.txid() is None or item.tx.txid() != key:
                raise WillChainError(
                    "chain: transaction {} has no computable txid".format(key[:8]))
        gen = Will._chain_generations(new_will, links)
        link_parent_outputs = {id(txin) for _c, txin, _p, _i in links}
        ref_inputs = Will.get_all_inputs(references)
        items = list(new_will.values())
        for g in range(max(gen.values()) + 1):
            for child, txin, parent, idx in links:
                if gen[id(child)] == g:
                    Will._relink(child, txin, parent, idx)
            members = [it for it in items if gen[id(it)] == g]
            for _pass in range(Will.CHAIN_MAX_PASSES):
                if not Will._date_chain_pass(
                    new_will, members, ref_inputs, link_parent_outputs, g
                ):
                    break
            else:
                raise WillChainError("chain: the delivery dates do not settle")
        Will._check_chain(new_will, links, references, old_will, ref_inputs,
                          link_parent_outputs, gen)
        new_will.clear()
        for item in items:
            txid = item.tx.txid()
            if txid is None or txid in new_will:
                raise WillChainError("chain: two transactions with one txid")
            item._id = txid
            new_will[txid] = item

    @staticmethod
    def _date_chain_pass(new_will, members, ref_inputs, link_parent_outputs, g):
        """One dating pass over the generation ``members`` (see
        :meth:`date_chain`, step 2). Returns True when a locktime moved."""
        moved = False
        new_inputs = Will.get_all_inputs(new_will, only_valid=True)
        for item in members:
            for txin in item.tx.inputs():
                prevout = txin.prevout.to_str()
                for _wid, other, _i in new_inputs.get(prevout, []):
                    if other is not item and item.set_anticipate(other):
                        moved = True
                if g > 0 and id(txin) not in link_parent_outputs:
                    # A later delivery is dated only through its parent's
                    # output (see date_chain, step 2).
                    continue
                for _wid, old, _i in ref_inputs.get(prevout, []):
                    if Will._chain_set_anticipate(item, old):
                        moved = True
        return moved

    @staticmethod
    def _check_chain(new_will, links, references, old_will, ref_inputs,
                     link_parent_outputs, gen):
        """Refuse a chain that could be executed wrongly (#166).

        Raises :class:`WillChainError` when:

        * a link does not point at its parent's txid and output, or the
          input's amount is not that output's amount;
        * a later delivery is not dated strictly after the one it spends
          (the will-executors would broadcast it before its parent exists,
          and they never try again);
        * a transaction of the will in force that starts a chain (it spends
          only wallet coins) is for a will-executor that gets new
          transactions, and none of its new first deliveries spends one of
          its coins with an earlier date: it would stay executable together
          with the new will. Since #166 the first delivery spends every coin
          (``heirs.prepare_transactions``), so this is a safety net;
        * a new transaction and an old one of the same will-executor share
          a coin, pay other amounts to the heirs and the new one is not
          dated earlier.
        """
        for child, txin, parent, idx in links:
            if (txin.prevout.txid.hex() != parent.tx.txid()
                    or txin.prevout.out_idx != idx):
                raise WillChainError("chain: an instalment is not linked to its parent")
            if txin.value_sats() != parent.tx.outputs()[idx].value:
                raise WillChainError("chain: an instalment spends a wrong amount")
            if int(child.tx.locktime) <= int(parent.tx.locktime):
                raise WillChainError(
                    "chain: a later delivery is not dated after the one before")
        new_keys = {item.tx.txid() for item in new_will.values()}
        firsts = [it for it in new_will.values() if gen[id(it)] == 0]
        for _oid, old in references.items():
            spends_will = any(
                i.prevout.txid.hex() in old_will for i in old.tx.inputs())
            if spends_will or old.tx.txid() in new_keys:
                continue
            same = [n for n in firsts if Will._same_url(n, old)]
            if not same:
                continue
            if not any(
                Will._shares_coins(n, old) and int(n.tx.locktime) < int(old.tx.locktime)
                for n in same
            ):
                raise WillChainError(
                    "chain: the will in force would stay executable "
                    "together with the new one")
        for item in new_will.values():
            g = gen[id(item)]
            for txin in item.tx.inputs():
                if g > 0 and id(txin) not in link_parent_outputs:
                    continue
                for _wid, old, _i in ref_inputs.get(txin.prevout.to_str(), []):
                    if (
                        Will._same_url(item, old)
                        and old.tx.txid() != item.tx.txid()
                        and int(item.tx.locktime) >= int(old.tx.locktime)
                        and Will._real_heir_amounts(item) != Will._real_heir_amounts(old)
                    ):
                        raise WillChainError(
                            "chain: an old and a new transaction would be "
                            "executable together")

    @staticmethod
    def wallet_synced(wallet):
        """True when ``wallet`` is synchronized (``wallet.is_up_to_date()``).

        False without a wallet, or when it cannot be asked: the spends of
        the will's coins are then not known (CHANGELOG #128 takes the safe
        side, see :mod:`bal.core.will_state`).
        """
        if wallet is None:
            return False
        try:
            return bool(wallet.is_up_to_date())
        except Exception:
            return False

    @staticmethod
    def drop_drafts(will):
        """Remove the drafts from ``will`` (in place); return their ids.

        A draft (:func:`bal.core.will_state.is_draft`: VALID, never signed
        nor sent) exists only in this wallet, so dropping it is free
        (CHANGELOG #128). Signed, sent and partially signed items are never
        removed here.
        """
        dropped = will_state.draft_ids(will)
        for wid in dropped:
            del will[wid]
        return dropped

    @staticmethod
    def in_force_items(will, wallet, synced):
        """The items of ``will`` that belong to the will in force (#128).

        :func:`bal.core.will_state.is_in_force`: signed and/or sent, VALID or
        REPLACED, not INVALIDATED / CONFIRMED / MEMPOOL and, on a
        synchronized wallet, still executable
        (:meth:`_tx_still_executable`). Returns ``{wid: item}``.
        """
        return {
            wid: w
            for wid, w in will.items()
            if will_state.is_in_force(
                w,
                synced,
                lambda wid=wid: Will._tx_still_executable(wid, will, wallet),
            )
        }

    @staticmethod
    def anticipation_references(will, in_force):
        """The old items a rebuilt will is dated against (CHANGELOG #128).

        The will in force (``in_force``, see :meth:`in_force_items`) and the
        VALID partially signed items. WHY the latter: a co-signer may still
        complete one and send it, so a new will must not be dated after it
        (safe side). The drafts are not here: they were never sent, and
        beating them is what moved the owner's date back one day per
        cancelled signature (known issue "P2").
        """
        refs = dict(in_force)
        for wid, w in will.items():
            if w.get_status("VALID") and will_state.is_partially_signed(w):
                refs[wid] = w
        return refs

    @staticmethod
    def _shares_coins(a, b):
        """True when will items ``a`` and ``b`` spend at least one same coin."""
        mine = {i.prevout.to_str() for i in a.tx.inputs()}
        return any(i.prevout.to_str() in mine for i in b.tx.inputs())

    @staticmethod
    def bring_back_blocker(oid, will, new_item, wallet, synced, in_force):
        """Why the REPLACED item ``will[oid]`` cannot be brought back, or None.

        CHANGELOG #128 (owner decision 2026-10-12): a rebuilt transaction
        equal to a replaced item of the will in force gives that item back
        (VALID again, with its signatures and its sent / verified state)
        only when ALL of these hold:

        (a) same txid: the same transaction (inputs, outputs, locktime);
        (b) the old item is signed (COMPLETE) and sent (PUSHED);
        (c) it is still executable: not INVALIDATED / CONFIRMED / MEMPOOL
            and no input spent by a broadcast transaction;
        (d) no other signed or sent, still executable item on the same
            coins is dated EARLIER (``in_force``): the will-executors would
            execute that one, and the date shown would be false;
        (e) the wallet is synchronized (``synced``): before, (c) and (d)
            cannot be known.

        The will-executor URL must also be the same (another URL with the
        same transaction is the #122 case: a fresh item to send there).
        Returns a short English reason for the log, or None.
        """
        old = will[oid]
        if new_item.tx.txid() != old.tx.txid():
            return "another transaction"
        if not old.get_status("COMPLETE"):
            return "not signed"
        if not old.get_status("PUSHED"):
            return "not sent"
        if not synced:
            return "wallet not synchronized"
        if not will_state.may_be_in_force(old):
            return "invalidated or executed"
        if not Will._tx_still_executable(oid, will, wallet):
            return "no longer executable"
        for wid, w in in_force.items():
            if (
                wid != oid
                and int(w.tx.locktime) < int(old.tx.locktime)
                and Will._shares_coins(w, old)
            ):
                return "an earlier signed or sent will on the same coins"
        if (old.we or {}).get("url") != (new_item.we or {}).get("url"):
            return "another will-executor"
        return None

    @staticmethod
    def _keep_signatures(new, old):
        """Give the fresh item ``new`` the signatures of ``old`` (same txid).

        Used when a rebuilt transaction is identical to a REPLACED item that
        is signed (or partially signed) but cannot be brought back
        (CHANGELOG #128, :meth:`bring_back_blocker`). The fresh item takes
        the old one's place in the will (same txid), so without this an
        UNSIGNED copy would replace a signed transaction: the password would
        be asked again for nothing, and a VALID item neither signed nor sent
        is not seen by the postpone check (``check_willexecutors_and_heirs``)
        although the will-executors hold that transaction. As for the same
        transaction for another will-executor URL (#122): the fresh item gets
        the old signed transaction (``COMPLETE``, no password) and stays not
        sent, so the will is sent (again) to its will-executor. A partially
        signed transaction keeps its signatures too.
        """
        if old.get_status("COMPLETE"):
            new.tx = old.tx
            new.sigs_have = old.sigs_have
            new.sigs_required = old.sigs_required
            new.set_status("COMPLETE", True)
        elif will_state.is_partially_signed(old):
            new.tx = old.tx
            new.sigs_have = old.sigs_have
            new.sigs_required = old.sigs_required

    @staticmethod
    def update_will(old_will, new_will, wallet=None):
        """Prepare a rebuilt will ``new_will`` against the will in force.

        ``old_will`` is the will so far (changed in place), ``wallet`` the
        Electrum wallet (``None``: its synchronization and the spends are
        unknown). Returns ``{"dropped": [...], "brought_back": [...],
        "signed_copies": [...]}`` (ids), for the log and the callers.

        CHANGELOG #128 (known issue "P2", owner decisions 2026-10-12):

        1. The drafts are dropped first (:meth:`drop_drafts`): never signed
           nor sent, they exist only in this wallet. Before, an unsigned
           draft was the will a new one had to beat, so every cancelled
           signature moved the delivery date one more day earlier.
        2. The new transactions are dated only against the will in force
           and the partially signed items (:meth:`anticipation_references`,
           then ``search_anticipate_rec`` and ``normalize_will``). A sent
           item that an unsigned draft had marked REPLACED is still in force
           (the will-executors hold it), so a new will is never dated after
           it. A will with delivery instalments (multiverse) is dated by
           :meth:`date_chain` instead of ``search_anticipate_rec`` (CHANGELOG
           #166); it may raise :class:`WillChainError`. NB ``normalize_will``
           works on copies: it does not date the caller's items.
        3. A new transaction with the txid of a VALID old item is the SAME
           transaction (the txid covers inputs, outputs and locktime, not
           the signatures), so (CHANGELOG #122):

           * same will-executor URL (or none, a backup transaction): the OLD
             item is kept. Its signatures are valid and its state (signed,
             sent, verified) is right. The new heirs, will-executor,
             description and fee rate are written on it, so it carries what
             the will was rebuilt from and the next Check finds it coherent;
           * another will-executor URL (same fee address and fee, e.g. an
             executor that moved): the fresh item is kept, so the will is
             sent to that URL. When the old item was signed, the fresh item
             gets the signed transaction (``COMPLETE``: no password
             needed), and it stays not sent.
        4. The same for a REPLACED old item of the will in force: it is
           brought back (VALID again, REPLACED cleared, signatures and
           sent / verified state kept) when :meth:`bring_back_blocker` finds
           nothing against it. Otherwise the fresh item takes its place, as
           before, but never as an unsigned copy of a signed transaction
           (:meth:`_keep_signatures`): it is signed (no password) and will
           be sent again. A REPLACED item never signed holds nothing and is
           overwritten by the fresh copy, as before.

        Before #122 the old item was kept only when its heirs were written
        exactly the same way ("Option A", CHANGELOG #17, helper
        ``_same_heirs``). But the same transaction is also rebuilt when an
        heir is renamed, or when the delivery date is written in another
        form giving the same day (a period instead of a fixed date, the #81
        rule: the owner's wallet, 2026-10-11): the fresh UNSIGNED copies took
        the place of the signed and sent ones, and a Cancel at the password
        left the will saved unsigned.
        """
        dropped = Will.drop_drafts(old_will)
        synced = Will.wallet_synced(wallet)
        in_force = Will.in_force_items(old_will, wallet, synced)
        references = Will.anticipation_references(old_will, in_force)
        # check if the new input is already spent by a transaction of the
        # will in force: if it is, use the same locktime, or anticipate.
        # CHANGELOG #166: a will with delivery instalments (multiverse) is
        # dated by one explicit pass; a will with one date keeps today's.
        links = Will.chain_links(new_will)
        if links:
            Will.date_chain(new_will, links, references, old_will)
        else:
            Will.search_anticipate_rec(new_will, Will.get_all_inputs(references))
        other_inputs = Will.get_all_inputs(old_will, {})
        try:
            Will.normalize_will(new_will, others_inputs=other_inputs)
        except Exception as e:
            raise e

        for oid in Will.only_valid(old_will):
            if oid not in new_will:
                continue
            old, new = old_will[oid], new_will[oid]
            if (old.we or {}).get("url") != (new.we or {}).get("url"):
                # Same transaction for another will-executor URL: the
                # fresh item must be sent there (PUSHED/CHECKED stay
                # cleared), but it needs no new signature.
                if old.get_status("COMPLETE"):
                    new.tx = old.tx
                    new.sigs_have = old.sigs_have
                    new.sigs_required = old.sigs_required
                    new.set_status("COMPLETE", True)
                continue
            Will._carry_new_data(old, new)
            new_will[oid] = old

        brought_back = []
        signed_copies = []
        for oid, old in list(old_will.items()):
            if oid not in new_will or not old.get_status("REPLACED"):
                continue
            new = new_will[oid]
            blocker = Will.bring_back_blocker(
                oid, old_will, new, wallet, synced, in_force
            )
            if blocker is None:
                old.set_status("REPLACED", False)
                old.set_status("VALID", True)
                Will._carry_new_data(old, new)
                new_will[oid] = old
                brought_back.append(oid)
            elif old.get_status("COMPLETE") or will_state.is_partially_signed(old):
                _logger.info(
                    f"will in force {oid[:8]} not brought back ({blocker}): "
                    "its signed transaction is kept, to be sent again"
                )
                Will._keep_signatures(new, old)
                signed_copies.append(oid)
        Will.replace_unreceived_copies(old_will, new_will)
        return {
            "dropped": dropped,
            "brought_back": brought_back,
            "signed_copies": signed_copies,
        }

    @staticmethod
    def replace_unreceived_copies(old_will, new_will):
        """Mark REPLACED the old copies the new will redoes for a new fee
        address or fee (#141). Returns their ids.

        An old VALID copy its will-executor never received
        (:meth:`received`) is replaced by a new copy for the same
        will-executor that spends the same coins on the same date and pays
        another fee address or fee (:meth:`terms_change`). The rebuild keeps
        the same date for it (``check_anticipate``: same heirs), so without
        this both copies stayed VALID and the old one, with the old address,
        was sent too. A received copy is never touched: the will-executor
        holds it.
        """
        replaced = []
        for oid in list(Will.only_valid(old_will)):
            old = old_will[oid]
            if oid in new_will or not old.we or Will.received(old):
                continue
            for nid, new in new_will.items():
                if (
                    nid != oid
                    and new.we
                    and new.we.get("url") == old.we.get("url")
                    and int(new.tx.locktime) == int(old.tx.locktime)
                    and Will.terms_change(old.we, new.we)
                    and Will._shares_coins(new, old)
                ):
                    old.set_status("REPLACED", True)
                    replaced.append(oid)
                    break
        return replaced

    @staticmethod
    def _carry_new_data(old, new):
        """Write on the kept item ``old`` what the will was rebuilt from
        (heirs, will-executor, description, fee rate of ``new``), so the
        next Check finds it coherent (CHANGELOG #122)."""
        old.heirs = new.heirs
        old.we = new.we
        old.description = new.description
        old.tx_fees = new.tx_fees

    @staticmethod
    def replaced_ids(will):
        """The ids of the REPLACED items of ``will`` (a snapshot taken
        before a build, for :meth:`only_brought_back`)."""
        return {wid for wid, w in will.items() if w.get_status("REPLACED")}

    @staticmethod
    def only_brought_back(replaced_before, will):
        """True when a build gave back the will in force and nothing new.

        CHANGELOG #128: after the build, at least one VALID item was
        REPLACED before it (``replaced_before``, see :meth:`replaced_ids`):
        it was brought back; and every VALID item is signed and, when it
        has a will-executor, sent: nothing to sign, nothing to send. The
        callers then say that the will in force is kept, instead of "a new
        will is being prepared".
        """
        valid = Will.only_valid_list(will)
        if not valid or not any(wid in replaced_before for wid in valid):
            return False
        for w in valid.values():
            if not w.get_status("COMPLETE"):
                return False
            if w.we and not w.get_status("PUSHED"):
                return False
        return True

    @staticmethod
    def get_higher_input_for_tx(will):
        out = {}
        for wid in will:
            wtx = will[wid].tx
            found = False
            inp = None
            for inp in wtx.inputs():
                if inp.prevout.txid.hex() in will:
                    found = True
                    break
            if not found and inp is not None:
                out[inp.prevout.to_str()] = inp
        return out

    @staticmethod
    def get_invalidation_fee_rate(wallet, fallback_fee_per_byte):
        """Return ``(fee_rate, source)`` for the on-chain invalidation tx.

        ``fee_rate`` is in sat/vbyte. ``source`` is ``"network"`` when the rate
        comes from Electrum's live fee estimate, ``"fallback"`` otherwise.

        WHY: the invalidation is broadcast NOW, so it pays the current network
        fee for a confirmation within ``INVALIDATION_FEE_ETA_BLOCKS`` blocks.
        The will's ``baltx_fees`` is kept only as a fallback, used when no
        estimate is available (offline wallet, estimates not received yet,
        server error), which is exactly the behaviour of previous versions.

        The estimate is read from ``network.fee_estimates.eta_target_to_fee``
        (Electrum 4.7.2 / 4.8.x), which returns sat/kvbyte and already clamps
        the value between Electrum's conservative minimum relay fee (1
        sat/vbyte) and its maximum dynamic fee. This method never raises.

        Args:
            wallet: The Electrum wallet (its ``network`` may be ``None``).
            fallback_fee_per_byte: Rate (sat/vbyte) used when there is no
                usable estimate - normally the will's ``baltx_fees``.
        """
        network = getattr(wallet, "network", None) if wallet else None
        fee_per_kb = None
        if network is not None:
            try:
                fee_per_kb = network.fee_estimates.eta_target_to_fee(
                    INVALIDATION_FEE_ETA_BLOCKS
                )
            except Exception as e:
                _logger.warning(f"invalidation fee: no network estimate ({e!r})")
                fee_per_kb = None
        # Accept only a real positive integer (Electrum's type for sat/kvbyte);
        # ``bool`` is excluded explicitly because it is a subclass of ``int``.
        if (
            isinstance(fee_per_kb, int)
            and not isinstance(fee_per_kb, bool)
            and fee_per_kb > 0
        ):
            return fee_per_kb / 1000, "network"
        return fallback_fee_per_byte, "fallback"

    @staticmethod
    def invalidate_will(will, wallet, fees_per_byte, history_label=None,
                        will_locktime=None):
        """Build the (unsigned) on-chain invalidation transaction.

        The transaction spends, back to one of the wallet's own change
        addresses, EVERY wallet coin used as an input by ANY will item
        (current, replaced, invalidated...), so no pre-signed inheritance
        transaction held by a will-executor stays usable. Wallet coins that no
        will transaction uses are not touched.

        WHY every item and every input (BUG-3, CHANGELOG #71): this used to
        take only ONE input per VALID item. With several coins the coin left
        out depended on the coin order, and an older transaction already sent
        to an executor (REPLACED, or wrongly marked INVALIDATED, see BUG-2)
        could stay executable after "Tools > Invalidate". The item statuses
        are the plugin's bookkeeping; only spending a coin really kills the
        transactions that use it. Inputs that spend the output of another
        will transaction are not wallet coins and are naturally skipped.

        Fee: the CURRENT network fee for a confirmation within
        ``INVALIDATION_FEE_ETA_BLOCKS`` blocks (see
        :meth:`get_invalidation_fee_rate`). ``fees_per_byte`` (the will's
        ``baltx_fees``) is only the FALLBACK used when no network estimate is
        available. RBF stays enabled, so the fee can still be raised from
        Electrum's History ("Increase fee") if the transaction gets stuck.

        Returns the ``PartialTransaction``, or ``None`` when there is nothing
        to invalidate or the balance does not cover the fee.
        """
        _logger.debug("invalidate tx in will module")
        # Every outpoint used by any will item (see the docstring: BUG-3).
        prevout_to_spend = set(Will.get_all_inputs(will))
        if history_label is not None and will_locktime is not None:
            utxos = Util.get_available_utxos(wallet, history_label, will_locktime)
        else:
            utxos = wallet.get_utxos()
        current_height = Util.get_current_height(wallet.network)
        balance = 0
        utxo_to_spend = []
        for utxo in utxos:
            if utxo.is_coinbase_output() and utxo.block_height < current_height+100:
                _logger.debug("is not mature coinbase output")
                continue
            utxo_str = utxo.prevout.to_str()
            if utxo_str in prevout_to_spend:
                balance += utxo.value_sats()
                utxo_to_spend.append(utxo)
        _logger.debug("utxo to spend: {}".format(utxo_to_spend))
        if len(utxo_to_spend) > 0:
            change_addresses = wallet.get_change_addresses_for_new_transaction()
            out = PartialTxOutput.from_address_and_value(change_addresses[0], balance)
            out.is_change = True
            locktime = current_height
            tx = PartialTransaction.from_io(
                utxo_to_spend, [out], locktime=locktime, version=2
            )
            tx.set_rbf(True)
            fee_rate, fee_source = Will.get_invalidation_fee_rate(
                wallet, fees_per_byte
            )
            # The network rate can be fractional (e.g. 1.5 sat/vbyte): round
            # the total fee UP to whole satoshis so the output value is an
            # integer and the rate is never below the one requested.
            fee = int(math.ceil(tx.estimated_size() * fee_rate))
            _logger.info(
                f"invalidation fee: {fee_rate} sat/vbyte ({fee_source}), "
                f"total {fee} sat"
            )
            if balance - fee > 0:
                out = PartialTxOutput.from_address_and_value(
                    change_addresses[0], balance - fee
                )
                tx = PartialTransaction.from_io(
                    utxo_to_spend, [out], locktime=locktime, version=2
                )
                tx.set_rbf(True)

                _logger.debug(f"invalidation tx: {tx}")
                return tx

            else:
                _logger.debug(f"balance({balance}) - fee({fee}) <=0")
                pass
        else:
            _logger.debug("len utxo_to_spend <=0")
            pass

    @staticmethod
    def is_new(will):
        for _wid, w in will.items():
            if w.get_status("VALID") and not w.get_status("COMPLETE"):
                return True
        return False

    @staticmethod
    def search_rai(all_inputs, all_utxos, will, wallet):
        # will_only_valid = Will.only_valid_or_replaced_list(will)
        for inp, ws in all_inputs.items():
            inutxo = Util.in_utxo(inp, all_utxos)
            for w in ws:
                wi = w[1]
                if (
                    wi.get_status("VALID")
                    or wi.get_status("CONFIRMED")
                    or wi.get_status("MEMPOOL")
                ):
                    prevout_id = w[2].prevout.txid.hex()
                    if not inutxo:
                        if prevout_id in will:
                            wo = will[prevout_id]
                            if wo.get_status("REPLACED"):
                                wi.set_status("REPLACED", True)
                            if wo.get_status("INVALIDATED"):
                                wi.set_status("INVALIDATED", True)

                        else:
                            # The funding outpoint is not part of the will tree:
                            # decide from whether a broadcast transaction really
                            # spends it (a wallet-local history copy of the same
                            # will tx must neither turn the item CONFIRMED nor
                            # INVALIDATED - it is just a persistence artifact).
                            stored = None
                            if wallet and getattr(wallet, "db", None):
                                try:
                                    stored = wallet.db.get_transaction(wi._id)
                                except Exception:
                                    stored = None
                            spender_height = Will._funding_spender_height(wallet, inp)
                            if spender_height is None:
                                if stored:
                                    continue
                                # CHANGELOG #86: a coin hidden only by a
                                # wallet-LOCAL tx (an unsigned will copy saved
                                # in the history) is not spent: INVALIDATED
                                # means a broadcast tx spent it. Marking it here
                                # made a will that was never invalidated look
                                # dead (owner's wallet).
                                if Will._funding_spent_only_locally(wallet, inp):
                                    continue
                                wi.set_status("INVALIDATED", True)
                            elif spender_height == 0:
                                wi.set_status("MEMPOOL", True)
                            else:
                                wi.set_status("CONFIRMED", True)

                    for child in wi.search(all_inputs):
                        if child.tx.locktime < wi.tx.locktime:
                            _logger.debug("a child was found")
                            wi.set_status("REPLACED", True)
                else:
                    pass

    @staticmethod
    def utxos_strs(utxos):
        return [Util.utxo_to_str(u) for u in utxos]

    @staticmethod
    def set_invalidate(wid, will=None):
        will = will if will is not None else {}
        will[wid].set_status("INVALIDATED", True)
        children = will[wid].children or []
        # add_willtree builds a LIST of [child_id, input_idx, prevout_idx];
        # a dict keyed by child id is still accepted (older data / tests).
        child_ids = children.keys() if isinstance(children, dict) else [
            c[0] for c in children
        ]
        for child_id in child_ids:
            if child_id in will:
                Will.set_invalidate(child_id, will)

    @staticmethod
    def check_tx_height(tx, wallet):
        info = wallet.get_tx_info(tx)
        return info.tx_mined_status.height()

    # check if transactions are stil valid tecnically valid
    @staticmethod
    def check_invalidated(willtree, utxos_list, wallet):
        for wid, w in willtree.items():
            # Defensive: a father that is not (or no longer) part of the will
            # is treated as "no father" instead of raising KeyError, so the
            # item's inputs are checked against the wallet UTXOs like any
            # root transaction. See add_willtree for the root cause.
            father = willtree.get(w.father) if w.father else None
            if (
                father is None
                or father.get_status("CONFIRMED")
                or father.get_status("MEMPOOL")
            ):
                for inp in w.tx.inputs():
                    inp_str = Util.utxo_to_str(inp)
                    if inp_str not in utxos_list:
                        if not wallet or not getattr(wallet, "adb", None):
                            continue
                        height = Will.check_tx_height(w.tx, wallet)
                        if height < 0:
                            # The will tx itself is not on-chain. A missing
                            # funding UTXO is only a real problem when a
                            # broadcast transaction actually spends it; a
                            # wallet-local (history) copy of the same will tx
                            # marks the funding spent locally and must not
                            # invalidate the will.
                            if Will._funding_really_spent(wallet, inp_str):
                                Will.set_invalidate(wid, willtree)
                        elif height == 0:
                            w.set_status("MEMPOOL", True)
                        else:
                            w.set_status("CONFIRMED", True)

    @staticmethod
    def find_still_active_older_txs(will, wallet, current_locktime=None):
        """Return the sent txs of an OLDER will that could still be executed.

        Read-only safety check (CHANGELOG #69): it never builds, signs, sends
        or invalidates anything, and never changes a status.

        A will item is reported when ALL of these hold:

        1. it was sent to a will-executor (``PUSHED``; ``CHECKED`` implies
           it). Items that only failed to send are not reported (decision W2:
           the executor most likely does not hold them);
        2. it is not ``VALID`` (the current will is not a danger), and not
           ``INVALIDATED``, ``CONFIRMED`` or ``MEMPOOL``;
        3. its locktime is EARLIER than the current will: the earliest
           locktime of the ``VALID`` items or, when there is none,
           ``current_locktime`` (the delivery date of the settings);
        4. it is still executable: no input is spent by a broadcast
           transaction (see :meth:`_tx_still_executable`).

        WHY: executors keep every transaction they ever received, and the
        only way to kill one is to spend its coins on-chain. When a postpone
        or an invalidation goes wrong (BUG-2 after a Cancel, BUG-3 with
        several coins), an older transaction stays executable while the panel
        shows the new, later date. A "Replaced" item with a LATER date is the
        normal result of a fee or heir change and is not reported.

        Nothing is reported while the wallet is not synchronized
        (``wallet.is_up_to_date()`` is False, e.g. offline or still syncing):
        a coin spent by a transaction the wallet has not seen yet would
        otherwise raise a false alarm.

        Args:
            will: ``{txid: WillItem}`` (the plugin's ``willitems``).
            wallet: The Electrum wallet.
            current_locktime: Fallback reference date (UNIX timestamp) used
                when the will has no ``VALID`` item.

        Returns:
            A list of ``(willitem, locktime, executor_url)`` sorted by
            locktime (earliest first); empty when there is nothing to report.
            Never raises.
        """
        try:
            if not will:
                return []
            reference = Will.get_current_will_locktime(will, current_locktime)
            if not reference:
                return []
            return [
                found for found in Will.sent_still_executable(will, wallet)
                if found[1] < int(reference)
            ]
        except Exception as e:
            # A failure of this check must never break the Check wizard.
            _logger.error(f"still-active check failed: {e!r}")
            return []

    @staticmethod
    def sent_still_executable(will, wallet):
        """The sent txs that are not VALID but can still be executed, any date.

        Conditions 1, 2 and 4 of :meth:`find_still_active_older_txs`
        (which adds "dated earlier than the current will"): sent to a
        will-executor, not ``VALID``/``INVALIDATED``/``CONFIRMED``/
        ``MEMPOOL``, no input spent by a broadcast tx. Read-only, nothing
        while the wallet is not synchronized, never raises.

        WHY (CHANGELOG #84b): after a Cancel at signing the records hold an
        unsigned will (VALID) and the will sent before (now "Replaced"),
        which the executors still hold and can execute: the will summary
        must say that this one stays in force until the new one is signed.

        Returns ``[(willitem, locktime, executor_url)]``, earliest first.
        """
        try:
            if not will or not wallet or not getattr(wallet, "adb", None):
                return []
            if not wallet.is_up_to_date():
                return []
            found = []
            for wid, w in will.items():
                if (
                    not w.get_status("PUSHED")
                    or w.get_status("VALID")
                    or w.get_status("INVALIDATED")
                    or w.get_status("CONFIRMED")
                    or w.get_status("MEMPOOL")
                ):
                    continue
                if not Will._tx_still_executable(wid, will, wallet):
                    continue
                found.append((w, int(w.tx.locktime), (w.we or {}).get("url")))
            found.sort(key=lambda item: item[1])
            return found
        except Exception as e:
            _logger.error(f"sent-still-executable check failed: {e!r}")
            return []

    @staticmethod
    def past_delivery_date(will_settings, now=None):
        """Return the delivery date of the settings if it is already past.

        The stored ``locktime`` (absolute timestamp, or a relative "1y"/"30d"
        recipe, which always lies in the future) is resolved with
        :meth:`Util.parse_locktime_string`. Returns that UNIX timestamp when
        it is not later than ``now`` (default: the current time), else
        ``None``. Never raises.

        WHY (CHANGELOG #75, BUG-4): with a delivery date in the past the
        Check reported an heir "not covered by the current will" and then "no
        delivery date left to build"; the real cause is the past date.
        """
        try:
            delivery = Util.parse_locktime_string((will_settings or {})["locktime"])
            current = now if now is not None else datetime.now(tz=timezone.utc).timestamp()
            if delivery and int(delivery) <= int(current):
                return int(delivery)
        except Exception as e:
            _logger.debug(f"past_delivery_date: {e!r}")
        return None

    @staticmethod
    def executed_inheritance_status(willitems):
        """Return the on-chain status of an already-executed inheritance.

        Task #7a (owner request). After an inheritance is executed the wallet
        is fully emptied (the plugin always empties the wallet). Pressing
        CHECK on such an empty wallet makes ``check_will`` raise
        NotCompleteWillException (the heirs no longer match the empty
        wallet), which previously showed the alarming "Found CHANGES ... a
        NEW WILL must be prepared" message even though the inheritance was,
        in fact, correctly executed.

        To reassure the user we look at the will items, whose status is
        already set to CONFIRMED / MEMPOOL by ``Will.check_will``, and return
        a short code describing the real situation:

        Returns:
            "CONFIRMED" if at least one will transaction is confirmed
                on-chain (the inheritance is already executed);
            "MEMPOOL" if none is confirmed but at least one is in the
                mempool (waiting for confirmation);
            None if neither (the change really has to be rebuilt).

        CONFIRMED takes precedence over MEMPOOL, since a confirmed
        transaction is the strongest evidence that the inheritance has gone
        through. Moved unchanged from the Check dialog (CHANGELOG #115).
        """
        has_mempool = False
        try:
            for _wid, witem in willitems.items():
                if witem.get_status("CONFIRMED"):
                    return "CONFIRMED"
                if witem.get_status("MEMPOOL"):
                    has_mempool = True
        except Exception as _err:
            # Never let this purely informational check break the flow.
            _logger.debug(f"executed_inheritance_status error: {_err}")
        return "MEMPOOL" if has_mempool else None

    @staticmethod
    def unsigned_will_executors(willitems):
        """Will-executor URLs of a will prepared earlier but never signed.

        Returns the set of URLs of the VALID items that are neither signed
        (``COMPLETE``) nor sent (``PUSHED``) - typically left by a Cancel at
        the signing step (an item without will-executor adds ``None``) - or
        ``None`` when there is no such item. Read-only; never raises. Moved
        unchanged from the Check dialog (CHANGELOG #115).
        """
        try:
            urls = {
                (w.we or {}).get("url")
                for w in willitems.values()
                if w.get_status("VALID")
                and not w.get_status("COMPLETE")
                and not w.get_status("PUSHED")
            }
            return urls or None
        except Exception as e:
            _logger.debug(f"unsigned will check failed: {e!r}")
            return None

    @staticmethod
    def amount_warning_is_false_alarm(date_passed, available_utxos):
        """True when an AmountException is not a real amount problem.

        WHY (BUG-5, CHANGELOG #76): the "adjust the amounts" warning flashed
        in 12 Checks of the scenario matrix while no amount had changed. The
        two causes, traced in the harness:

        * the delivery date is in the past (``date_passed``): heirs dated
          before the reference are left out of the percentage sum, so it is
          0 % instead of 100 % (the past date is explained by
          :func:`bal.core.messages.past_date_message`);
        * no spendable coin is visible at this moment (e.g. right after a
          Cancel the wallet-local copy of the unsigned will still holds the
          coins; the build frees them just after): with a balance of 0 the
          fixed-amount check fails. A really empty wallet is reported by the
          build step ("no spendable funds").

        Never raises; on error the warning is shown (previous behaviour).
        Moved unchanged from the Check dialog (CHANGELOG #115).
        """
        try:
            if date_passed:
                return True
            return sum(u.value_sats() for u in (available_utxos or [])) <= 0
        except Exception as _err:
            _logger.debug(f"amount_warning_is_false_alarm: {_err}")
            return False

    @staticmethod
    def get_current_will_locktime(will, fallback=None):
        """Delivery date of the current will, as used by the still-active check.

        The earliest locktime among the ``VALID`` items (the date the current
        will can first be executed), or ``fallback`` (normally the delivery
        date of the settings) when the will has no valid item.
        """
        locktimes = [
            int(w.tx.locktime) for w in (will or {}).values() if w.get_status("VALID")
        ]
        return min(locktimes) if locktimes else fallback

    @staticmethod
    def _tx_still_executable(wid, will, wallet, _seen=None):
        """True when no input of will tx ``wid`` is spent by a broadcast tx.

        A wallet-local copy of a will tx (saved in the history) does not
        count as a spender (:meth:`_funding_really_spent`). When an input
        spends the output of another will tx that is not on-chain or in the
        mempool, that output only exists if the parent tx can still be
        executed, so the parent's inputs decide (recursively; ``_seen``
        guards against loops in corrupted data).
        """
        seen = set() if _seen is None else _seen
        if wid in seen:
            return False
        seen.add(wid)
        for inp in will[wid].tx.inputs():
            if Will._funding_really_spent(wallet, Util.utxo_to_str(inp)):
                return False
            parent_id = inp.prevout.txid.hex()
            parent = will.get(parent_id)
            if (
                parent is not None
                and not parent.get_status("CONFIRMED")
                and not parent.get_status("MEMPOOL")
                and not Will._tx_still_executable(parent_id, will, wallet, seen)
            ):
                return False
        return True

    # def reflect_to_children(treeitem):
    #    if not treeitem.get_status("VALID"):
    #        _logger.debug(f"{tree:item._id} status not valid looking for children")
    #        for child in treeitem.children:
    #            wc = willtree[child]
    #            if wc.get_status("VALID"):
    #                if treeitem.get_status("INVALIDATED"):
    #                    wc.set_status("INVALIDATED", True)
    #                if treeitem.get_status("REPLACED"):
    #                    wc.set_status("REPLACED", True)
    #                    if wc.children:
    #                        Will.reflect_to_children(wc)

    @staticmethod
    def check_amounts(heirs, willexecutors, all_utxos, timestamp_to_check, dust,
                      max_fee=None):
        fixed_heirs, fixed_amount, perc_heirs, perc_amount, fixed_amount_with_dust = (
            heirs.fixed_percent_lists_amount(timestamp_to_check, dust, reverse=True)
        )
        wallet_balance = 0
        for utxo in all_utxos:
            wallet_balance += utxo.value_sats()

        if fixed_amount >= wallet_balance:
            raise FixedAmountException(
                f"Fixed amount({fixed_amount}) >= {wallet_balance}"
            )
        if perc_amount != 100:
            raise PercAmountException(f"Perc amount({perc_amount}) =! 100%")

        for url, wex in willexecutors.items():
            if Willexecutors.is_selected(wex) and Willexecutors.is_valid(wex, max_fee=max_fee, dust=dust):
                if max_fee is not None and int(wex["base_fee"]) > max_fee:
                    raise WillExecutorFeeTooHighException(wex, max_fee)
                temp_balance = wallet_balance - int(wex["base_fee"])
                if fixed_amount >= temp_balance:
                    raise FixedAmountException(
                        f"Willexecutor{url} excess base fee({wex['base_fee']}), {fixed_amount} >={temp_balance}"
                    )

    @staticmethod
    def describe_amounts(heirs, willexecutors, all_utxos, timestamp_to_check, dust):
        """Explain what the build will do with the heirs' amounts.

        Same inputs as :meth:`check_amounts`, but instead of raising it says
        WHAT happens (CHANGELOG #76, verified in the scenario harness):

        * ``{"kind": "fixed_exceeds", "fixed": int, "balance": int,
          "percent_heirs": [names]}`` — the fixed amounts are not below the
          balance left for the heirs (wallet balance minus the highest base
          fee of the selected, valid will-executors). The build would pay the
          fixed heirs and silently leave the percentage heirs out;
        * ``{"kind": "percent", "total": float, "shares": [(name, share)],
          "with_fixed": bool}`` — the percentages do not add up to 100: the
          will always empties the wallet, so they are rescaled in proportion
          (of what remains after the fixed amounts, if any);
        * ``{"kind": "fixed_scaled", "shares": [(name, share)]}`` — only
          fixed amounts: they are paid in proportion, the wallet is emptied
          (CHANGELOG #82);
        * ``None`` — nothing to explain.

        "fixed_exceeds" is never reported when no coin is visible (balance 0),
        since the balance then says nothing about the amounts (#82).

        Never raises (``None`` on error).
        """
        try:
            fixed_heirs, fixed_amount, perc_heirs, perc_amount, _dust = (
                heirs.fixed_percent_lists_amount(timestamp_to_check, dust, reverse=True)
            )
            balance = sum(u.value_sats() for u in (all_utxos or []))
            base_fees = [
                int(we.get("base_fee", 0))
                for we in (willexecutors or {}).values()
                if Willexecutors.is_selected(we)
                and Willexecutors.is_valid(we, dust=dust)
            ]
            left = balance - (max(base_fees) if base_fees else 0)
            # CHANGELOG #82: with no coin visible at this moment (e.g. right
            # after a Cancel) the balance says nothing about the amounts:
            # never report "fixed amounts exceed" then.
            if fixed_heirs and balance > 0 and fixed_amount >= left:
                return {
                    "kind": "fixed_exceeds",
                    "fixed": int(fixed_amount),
                    "balance": int(left),
                    "percent_heirs": list(perc_heirs),
                }
            if perc_heirs and perc_amount and abs(perc_amount - 100) > 1e-9:
                shares = [
                    (name, float(entry[1][:-1]) * 100 / perc_amount)
                    for name, entry in perc_heirs.items()
                ]
                return {
                    "kind": "percent",
                    "total": float(perc_amount),
                    "shares": shares,
                    "with_fixed": bool(fixed_heirs),
                }
            if fixed_heirs and not perc_heirs and fixed_amount > 0:
                # CHANGELOG #82: only fixed amounts. The will always empties
                # the wallet, so the build pays them IN PROPORTION (verified
                # in the harness: 300,000 + 200,000 sat on a 1,000,000 sat
                # wallet became 60 % / 40 % of it). Heirs at or below the dust
                # threshold and OP_RETURN outputs are left out, as in the
                # build (fixed_amount does not count them).
                amounts = []
                for name in fixed_heirs:
                    entry = heirs[name]
                    if is_op_return_address(entry[0]):
                        continue
                    amount = int(math.floor(float(entry[1])))
                    if amount > dust:
                        amounts.append((name, amount))
                total = sum(a for _n, a in amounts)
                if total > 0:
                    return {
                        "kind": "fixed_scaled",
                        "shares": [(n, a * 100 / total) for n, a in amounts],
                    }
        except Exception as e:
            _logger.debug(f"describe_amounts: {e!r}")
        return None

    @staticmethod
    def _funding_really_spent(wallet, inp_str):
        """True when a broadcast transaction really spends the ``txid:n`` outpoint.

        ``wallet.adb.get_spender`` discards wallet-local spenders (the stored
        will tx from the local history) and future transactions, so this is True
        only when the funding was consumed by a real on-chain/mempool tx.
        """
        if not wallet or not getattr(wallet, "adb", None):
            return False
        try:
            return wallet.adb.get_spender(inp_str) is not None
        except Exception as e:
            _logger.error(f"get_spender failed for {inp_str}: {e}")
            return False

    @staticmethod
    def _funding_spent_only_locally(wallet, inp_str):
        """True when ``txid:n`` is "spent" only by a wallet-LOCAL/future tx.

        ``adb.get_spender`` hides such spenders (it returns None, like for a
        coin nobody spends); this looks at the raw spender recorded in the
        wallet DB. A local spender is never broadcast, so the coin is still
        unspent on-chain (CHANGELOG #86). False when nothing is recorded or
        on any lookup failure (then the caller keeps its old behaviour).
        """
        if not wallet or not getattr(wallet, "adb", None):
            return False
        try:
            prev_txid, index = inp_str.split(":")
            raw = wallet.db.get_spent_outpoint(prev_txid, int(index))
            if not raw:
                return False
            height = int(wallet.adb.get_tx_height(raw).height())
            return height in (TX_HEIGHT_LOCAL, TX_HEIGHT_FUTURE)
        except Exception as e:
            _logger.debug(f"local spender lookup failed for {inp_str}: {e!r}")
            return False

    @staticmethod
    def _funding_spender_height(wallet, inp_str):
        """Mined height of the broadcast tx spending ``inp_str``, or None.

        Returns ``None`` when no broadcast transaction spends the outpoint (a
        wallet-local history spender or a future tx are ignored by
        ``adb.get_spender``). The height is 0 for a mempool spender and positive
        for a confirmed one.
        """
        if not wallet or not getattr(wallet, "adb", None):
            return None
        try:
            spender = wallet.adb.get_spender(inp_str)
        except Exception as e:
            _logger.error(f"get_spender failed for {inp_str}: {e}")
            return None
        if spender is None:
            return None
        try:
            return int(wallet.adb.get_tx_height(spender).height())
        except Exception as e:
            _logger.error(f"get_tx_height failed for {spender}: {e}")
            return 0

    @staticmethod
    def _absorb_history_signatures(will, wallet):
        """Merge signatures from the wallet's stored local copy of each will tx.

        An incomplete will transaction saved into the local history (see
        ``save_valid_transactions_to_history``) may later accumulate signatures
        (e.g. after a manual merge from a more complete copy). The in-memory
        will item would otherwise miss those signatures on the next check. For
        every item whose stored wallet copy is the same partial transaction the
        signatures are merged into the in-memory one and, if it becomes fully
        signed, the item is marked COMPLETE.

        This method must never raise: history absorption is a convenience on top
        of the will check, so any failure is logged and ignored.
        """
        if not wallet or not getattr(wallet, "db", None):
            return
        for wi in will.values():
            try:
                if (
                    wi.tx is None
                    or not isinstance(wi.tx, PartialTransaction)
                    or wi.tx.is_complete()
                ):
                    continue
                stored = wallet.db.get_transaction(wi._id)
                if (
                    not isinstance(stored, Transaction)
                    or stored.txid() != wi.tx.txid()
                ):
                    continue
                wi.tx.combine_with_other_psbt(stored)
                if wi.tx.is_complete():
                    wi.set_status("COMPLETE", True)
            except Exception as e:
                _logger.error(f"absorb history signatures failed for item {wi._id}: {e}")

    @staticmethod
    def check_will(will, all_utxos, wallet, timestamp_to_check):
        """Validate a will against the current wallet state.

        Locktimes are always UNIX timestamps (block-height locktimes are no
        longer supported by this plugin), so expiry is decided purely by
        comparing each transaction's locktime against ``timestamp_to_check``.

        Args:
            will: The will dictionary (WillItem entries keyed by txid).
            all_utxos: The list of UTXOs currently available in the wallet.
            wallet: The Electrum wallet object.
            timestamp_to_check: The reference UNIX timestamp (usually "now")
                used to decide whether any transaction has expired.
        """
        Will._absorb_history_signatures(will, wallet)
        Will.add_willtree(will)
        utxos_list = Will.utxos_strs(all_utxos)

        Will.check_invalidated(will, utxos_list, wallet)

        all_inputs = Will.get_all_inputs(will, only_valid=True)
        all_inputs_min_locktime = Will.get_all_inputs_min_locktime(all_inputs)
        Will.check_will_expired(all_inputs_min_locktime, timestamp_to_check)

        all_inputs = Will.get_all_inputs(will, only_valid=True)

        Will.search_rai(all_inputs, all_utxos, will, wallet)

        Will.check_signatures(will, wallet)

    @staticmethod
    def planned_delivery(will, locktime_setting):
        """Return ``(timestamp, in_force)``: the delivery date really used.

        With a will in force, a relative setting such as ``"4y"`` is NOT
        counted from today: it stays anchored to the date frozen in the
        signed tx (``Util.resolve_locktime_against_tx``). Showing "today +
        4y" next to the field therefore showed a date that moved every day
        while the will kept its own (CHANGELOG #79). This returns the frozen
        date when the setting is unchanged (``in_force`` True), the date a
        changed setting would give, or - with no will in force - the date a
        new will gets (``in_force`` False). Read-only.

        #168: the setting is the date of the FIRST delivery, so it is read
        against the earliest VALID item (in a chain of deliveries the dict
        order may give a later one first).
        """
        valid = sorted(Will.only_valid_list(will).values(),
                       key=lambda w: int(w.tx.locktime))
        for w in valid:
            for name, their in (w.heirs or {}).items():
                if name[:9] == 'w!ll3x3c"':
                    continue
                tx_locktime = int(w.tx.locktime)
                resolved = Util.resolve_locktime_against_tx(
                    locktime_setting, their[2], tx_locktime
                )
                return resolved, resolved == tx_locktime
        return Util.parse_locktime_string(locktime_setting), False

    @staticmethod
    def refresh_invalidated(will, all_utxos, wallet):
        """Mark INVALIDATED the items whose coins are already spent on-chain.

        The same detection ``check_will`` runs (``add_willtree`` +
        ``check_invalidated``), but meant to run BEFORE the Check Alive and
        ``date_to_check`` are computed (CHANGELOG #79): those read the
        locktime of the VALID items, and right after a renewal the old,
        already-invalidated items would otherwise still count, so the passed
        Check Alive was found again and the new will was never prepared.
        Idempotent; never raises (a failure only logs: ``check_will`` runs
        the same detection again later).
        """
        try:
            Will.add_willtree(will)
            Will.check_invalidated(will, Will.utxos_strs(all_utxos), wallet)
        except Exception as e:
            _logger.debug(f"refresh_invalidated failed: {e!r}")

    @staticmethod
    def save_valid_transactions_to_history(will, wallet, history_label):
        """Keep the wallet's LOCAL history in sync with the current will state.

        Called after the will has been built/signed/checked (see the
        SAVE_HISTORY / HISTORY_LABEL settings). A will transaction belongs in the
        local history while it is still "New" (not yet fully signed - i.e. an
        incomplete partial transaction), and must be removed once it becomes
        "Complete" (fully signed), because at that point it is ready to be
        broadcast and will appear in the history on its own.

        For every will item that is valid and whose transaction has a txid it:

        1. decodes the label template, replacing "{willexecutor}" with the
           will-executor URL of the item,
        2. if the transaction is NOT complete, stores it via
           ``wallet.adb.add_transaction`` (merging signatures when an
           already-stored partial transaction is upgraded by a more complete
           one) and tags it with the decoded label; a transaction whose
           stored copy would lose its txid (``_survives_reparse``, #175) is
           not stored, with one INFO line for all of them,
        3. if the transaction IS complete, does not store it: its matching
           local-history entry is removed by the cleanup below.

        Finally it deletes every wallet-local transaction whose label exactly
        matches the decoded label of a current valid item but that is no longer
        among the just-saved transactions, so fully-signed, rebuilt or replaced
        wills do not pile up stale entries.

        This method must never raise: history persistence is a convenience on
        top of the will check, so any failure is logged and ignored.

        Args:
            will: The will dictionary (WillItem entries keyed by txid).
            wallet: The Electrum wallet object (may be falsy for offline
                checks, in which case this is a no-op).
            history_label: The label template to apply (may contain
                "{willexecutor}").
        """
        if not wallet or not getattr(wallet, "adb", None):
            return
        saved_txids = []
        not_storable = []
        try:
            current_labels = {
                history_label.replace(
                    "{willexecutor}", (wi.we or {}).get("url", "")
                )
                for wi in will.values()
                if wi.get_status("VALID")
                and wi.tx is not None
                and wi.tx.txid() is not None
            }
            for wi in will.values():
                if not wi.get_status("VALID"):
                    continue
                if wi.tx is None or wi.tx.txid() is None:
                    continue
                # Fully-signed (complete) transactions must NOT be saved: they
                # are removed from the local history so the list does not show a
                # placeholder for a transaction that will appear on its own once
                # broadcast/confirmed. Only the not-yet-complete "New" items are
                # stored. Note that fully-segwit partial txs have a txid even
                # when incomplete, so the txid() check alone is not enough.
                if wi.tx.is_complete():
                    continue
                # #175: a later delivery of a will in several deliveries
                # spends an unsigned parent; its stored copy loses the txid
                # and Electrum's half-done add broke the wallet's balance.
                if not Will._survives_reparse(wi.tx):
                    not_storable.append(wi._id)
                    continue
                try:
                    txid = wi.tx.txid()
                    label = history_label.replace(
                        "{willexecutor}", (wi.we or {}).get("url", "")
                    )
                    Will._add_transaction_to_history(wallet, wi.tx, txid)
                    try:
                        wallet.set_label(txid, label)
                    except Exception as e:
                        _logger.error(f"set_label failed for {txid}: {e}")
                    saved_txids.append(txid)
                except Exception as e:
                    _logger.error(f"save to history failed for item {wi._id}: {e}")
            if not_storable:
                _logger.info(
                    f"{len(not_storable)} unsigned will transactions not saved "
                    "to the local history: they spend a will transaction not "
                    "on the network yet, so the wallet cannot store them"
                )
            # Delete stale wallet-local txs whose label matches a current valid
            # item but that are no longer among the saved ones. This removes
            # entries for fully-signed (complete) items and for rebuilt/replaced
            # wills with the same executor.
            for txid, label in Will._wallet_labels(wallet):
                if txid in saved_txids:
                    continue
                if label not in current_labels:
                    continue
                try:
                    wallet.adb.remove_transaction(txid)
                    try:
                        wallet.set_label(txid, None)
                    except Exception:
                        pass
                except Exception as e:
                    _logger.error(f"remove from history failed for {txid}: {e}")
            Will._save_wallet_db(wallet, "history update")
        except Exception as e:
            _logger.error(f"save_valid_transactions_to_history failed: {e}")

    @staticmethod
    def remove_stale_wallet_history(wallet, history_label):
        """Delete wallet-LOCAL will transactions saved under ``history_label``.

        ``save_valid_transactions_to_history`` stores the not-yet-signed
        inheritance txs into the wallet's local history; those local
        placeholders nominally spend the coins they reference. When the will is
        REBUILT (prepare/build, auto-rebuild, on-close rebuild, CLI build) the
        stale placeholders must be removed so the coins become available again
        to the new build (see ``Util.get_available_utxos``). Only
        wallet-local/future (non-broadcast) txs whose label matches the history
        label template are removed; confirmed/broadcast history is never
        touched. Returns the txids that were removed.
        """
        if not wallet or not getattr(wallet, "adb", None):
            return []
        removed = []
        for txid, label in Will._wallet_labels(wallet):
            if not label or not Util._label_matches_history(label, history_label):
                continue
            try:
                height = int(wallet.adb.get_tx_height(txid).height())
            except Exception:
                continue
            if height not in (TX_HEIGHT_LOCAL, TX_HEIGHT_FUTURE):
                continue
            try:
                wallet.adb.remove_transaction(txid)
                removed.append(txid)
            except Exception as e:
                _logger.error(f"remove from history failed for {txid}: {e}")
                continue
            try:
                wallet.set_label(txid, None)
            except Exception as e:
                _logger.error(f"set_label failed for {txid}: {e}")
        Will._save_wallet_db(wallet, "history purge")
        return removed

    @staticmethod
    def _save_wallet_db(wallet, after):
        """Write the wallet file after a change of its local history (#143).

        Electrum refuses to write the wallet file from a daemon thread
        (``JsonDB.write``: "daemon thread cannot write db"). The Check runs
        on a ``TaskThread`` (a QThread), which Python reports as a daemon
        thread, so the save after the history purge logged an ERROR at every
        rebuild done by the Check (owner's logs, 2026-10-14 and 2026-10-15).

        Nothing is lost when the write is skipped: the change stays in the
        wallet's memory (the db's pending changes) and Electrum writes it at
        its next save, at the latest when the wallet is closed
        (``Abstract_Wallet.stop``). If Electrum died before that, the next
        rebuild would purge the same placeholders again. So on a daemon
        thread the write is left to Electrum, with an INFO line. ``after``
        names the change, for the log. Never raises.
        """
        if threading.current_thread().daemon:
            _logger.info(
                f"wallet file not written after {after} (worker thread): "
                "the change stays in memory and Electrum writes it at its "
                "next save"
            )
            return
        try:
            wallet.save_db()
        except Exception as e:
            _logger.error(f"save_db failed after {after}: {e}")

    @staticmethod
    def _survives_reparse(tx):
        """True when the wallet can store *tx*: its copy keeps the txid.

        Electrum stores a transaction as its text and reads it back
        (``WalletDB.add_transaction``), and refuses it when the txid read
        back differs. An unsigned transaction has a txid only while every
        input is known to be segwit; for an input that spends another
        unsigned will transaction (a later delivery of a will in several
        deliveries) that knowledge is not in the text, so the copy has no
        txid (#175, owner's log 2026-10-21: 80 such transactions). The
        refusal came AFTER Electrum had already written the spent coins and
        the outputs into the wallet's index, so the index pointed at
        transactions the wallet did not have and the balance failed with an
        AssertionError at every refresh. Never raises.
        """
        try:
            txid = tx.txid()
            return bool(txid) and tx_from_any(str(tx), sanitize=False).txid() == txid
        except Exception as e:
            _logger.debug(f"reparse check failed: {e!r}")
            return False

    @staticmethod
    def _undo_partial_add(wallet, txid):
        """Remove what a failed ``add_transaction`` left in the wallet index.

        Safety net of #175: when Electrum refuses a transaction half-way, the
        spent coins and outputs it already wrote stay in the index. If the
        wallet does not hold *txid*, ``adb.remove_transaction`` clears those
        entries (Electrum does the same at start-up,
        ``remove_local_transactions_we_dont_have``). A stored txid is left
        alone. Never raises.
        """
        try:
            if wallet.db.get_transaction(txid) is not None:
                return
            wallet.adb.remove_transaction(txid)
        except Exception as e:
            _logger.error(f"undo of a failed history add not done for {txid}: {e}")

    @staticmethod
    def _add_transaction_to_history(wallet, tx, txid):
        """Store *tx* into the wallet's local history via ``adb``.

        If a partial transaction with the same txid is already stored and *tx*
        carries additional signatures, the signatures are merged into the stored
        one before saving. ``allow_unrelated`` is retried as a fallback so that
        self-created txs (which are not yet part of the wallet's UTXO set) are
        still accepted.

        The tx is added with ``is_new=False`` (CHANGELOG #90). With the
        default ``is_new=True`` Electrum announces every added tx as a new
        transaction: its desktop notification said "4 new transactions: Total
        amount sent 2.12 mBTC" for the unsigned copies of the will (one per
        will-executor, their amounts summed), although nothing was sent and
        the copies leave the history once signed (owner's report).
        ``is_new=False`` is what Electrum itself uses when it re-adds a stored
        tx (same in 4.7.2 and 4.8.x): the tx is stored the same way, only the
        "new transaction" event is not raised. That event also cleared the
        wallet's tx-parents cache, so that is done here; the History tab is
        refreshed by the caller (``BalWindow._schedule_history_refresh``).

        #175: when the add fails, ``_undo_partial_add`` clears what Electrum
        already wrote into the wallet index, then the error is raised.
        """
        adb = wallet.adb
        existing = None
        try:
            existing = wallet.db.get_transaction(txid)
        except Exception:
            existing = None
        try:
            if (
                isinstance(existing, PartialTransaction)
                and not existing.is_complete()
                and isinstance(tx, PartialTransaction)
            ):
                existing.combine_with_other_psbt(tx)
                adb.add_transaction(existing, is_new=False)
            else:
                try:
                    adb.add_transaction(tx, is_new=False)
                except UnrelatedTransactionException:
                    adb.add_transaction(tx, allow_unrelated=True, is_new=False)
        except Exception as e:
            Will._undo_partial_add(wallet, txid)
            raise RuntimeError(f"add_transaction failed for {txid}: {e}") from e
        try:
            wallet.clear_tx_parents_cache()
        except Exception as e:
            _logger.debug(f"clear_tx_parents_cache not done: {e!r}")

    @staticmethod
    def _wallet_labels(wallet):
        """Return the wallet's ``(txid, label)`` pairs in a defensive way."""
        try:
            get_all_labels = wallet.get_all_labels
        except AttributeError:
            return []
        try:
            return list(get_all_labels().items())
        except Exception as e:
            _logger.error(f"get_all_labels failed: {e}")
            return []

    @staticmethod
    def check_signatures(will, wallet=None):
        """Refresh the per-item signature counts and the PARTIALLY_SIGNED status.

        The signature counts are derived from the transaction itself via
        Electrum's ``signature_count()``, which needs a script descriptor on
        each input (attached from the wallet when available). Items that already
        carry their own descriptors (e.g. imported/merged partial transactions)
        are counted even without a wallet.

        An item with at least one signature present but fewer than required is
        marked PARTIALLY_SIGNED. Items that are already signed (COMPLETE) or
        whose transaction is complete always clear the flag.
        """
        for wi in will.values():
            try:
                if wi.get_status("COMPLETE") or wi.tx is None or wi.tx.is_complete():
                    wi.set_status("PARTIALLY_SIGNED", False)
                    continue
                if wallet:
                    wi.tx.add_info_from_wallet(wallet)
                if not hasattr(wi.tx, "signature_count"):
                    continue
                have, required = wi.tx.signature_count()
                wi.sigs_have = int(have)
                wi.sigs_required = int(required)
                if required > 1 and 0 < have < required:
                    wi.set_status("PARTIALLY_SIGNED", True)
                else:
                    wi.set_status("PARTIALLY_SIGNED", False)
            except Exception as e:
                _logger.error(f"check_signatures failed for item {wi._id}: {e}")

    @staticmethod
    def anticipated_delivery_to_store(will, stored_locktime):
        """The delivery date to store after a rebuild, or ``None`` to keep it.

        One rule for every front-end (CHANGELOG #110): the Check dialog, the
        GUI's automatic rebuild and the command line used to carry their own
        copy of it, and the automatic rebuild had none (the next Check then
        asked to invalidate a will that was only anticipated).

        WHY: a rebuild that spends the coins of the will already sent (e.g.
        after new funds, or after an heir was removed) anticipates the
        locktime by one day (``check_anticipate``), so the new transactions
        are valid before the old ones. If the stored date stays one day
        later, the next check compares "stored > transaction", takes it for a
        postponement chosen by the user and asks for an on-chain
        invalidation.

        Rule: return the earliest locktime of the VALID transactions when it
        is strictly earlier than an ABSOLUTE stored date. A relative stored
        date ("30d"/"1y") is resolved against the built transactions on every
        check, so it is kept; a later date (a real postponement) is never
        overwritten; an unreadable stored date is left alone.
        """
        min_locktime = Will.get_min_locktime(will, None)
        if min_locktime is None:
            return None
        min_locktime = int(min_locktime)
        if isinstance(stored_locktime, str) and stored_locktime[-1:].lower() in ("d", "y"):
            return None
        try:
            current = int(Util.parse_locktime_string(stored_locktime))
        except Exception:
            return None
        return min_locktime if min_locktime < current else None

    @staticmethod
    def get_min_locktime(will,default_value=None):
        return min((v.tx.locktime for v in will.values() if v.get_status('VALID')), default=default_value)

    @staticmethod
    def get_creation_time(will):
        """Return the inheritance creation timestamp (oldest VALID will item).

        A positive relative Check Alive value counts forward from this date.
        The per-item ``time`` is stamped when the will is built and persisted
        in the wallet DB, so the anchor does not drift with the clock.  Only
        VALID items are considered; imported items without a ``time`` are
        ignored.  Returns ``None`` when no VALID item carries a timestamp.
        """
        return min(
            (
                v.time
                for v in will.values()
                if v.get_status('VALID') and v.time is not None
            ),
            default=None,
        )



    @staticmethod
    def is_will_valid(
        will,
        timestamp_to_check,
        tx_fees,
        all_utxos,
        heirs=None,
        willexecutors=None,
        self_willexecutor=False,
        wallet=False,
        callback_not_valid_tx=None,
    ):
        """Check whether the whole will is valid at the given timestamp.

        Locktimes are always UNIX timestamps, so the validity check only needs
        a single reference timestamp (no block height).

        Args:
            will: The will dictionary (WillItem entries keyed by txid).
            timestamp_to_check: Reference UNIX timestamp (usually "now").
            tx_fees: Fee rate used for the dust/coverage check.
            all_utxos: The list of UTXOs currently available in the wallet.
            heirs: Optional heirs dictionary.
            willexecutors: Optional will-executors dictionary.
            self_willexecutor: Whether the user acts as their own executor.
            wallet: The Electrum wallet object.
            callback_not_valid_tx: Optional callback invoked for invalid txs.

        Returns:
            True if the will is valid; raises an exception otherwise.
        """
        heirs = heirs if heirs is not None else {}
        willexecutors= willexecutors if willexecutors is not None else {}

        Will.check_will(will, all_utxos, wallet, timestamp_to_check)
        if heirs:
            # BUG-2 (CHANGELOG #72): a sent tx that is no longer VALID can
            # still be postponed; checked before the VALID-only check below.
            Will.check_sent_txs_not_postponed(will, heirs, wallet)
            if not Will.check_willexecutors_and_heirs(
                will,
                heirs,
                willexecutors,
                self_willexecutor,
                timestamp_to_check,
                tx_fees,
            ):
                raise NotCompleteWillException()

        all_inputs = Will.get_all_inputs(will, only_valid=True)

        _logger.info("check all utxo in wallet are spent")
        if all_inputs:
            for utxo in all_utxos:
                if utxo.value_sats() > 68 * tx_fees:
                    if not Util.in_utxo(utxo, all_inputs.keys()):
                        _logger.info("utxo is not spent", utxo.to_json())
                        _logger.debug(all_inputs.keys())
                        raise NotCompleteWillException(
                            "Some utxo in the wallet is not included"
                        )

        # CHANGELOG #91: every coin is covered, but maybe by a will that an
        # executor holds next to an older, incomplete one with the same date.
        url = Will.find_same_date_copies(will)
        if url is not None:
            raise SameDateCopiesException(url)

        _logger.info("will ok")
        return True

    @staticmethod
    def _short_will_id(will_id):
        """Return a human-friendly, shortened form of a will id (hash).

        Will ids are long hex strings (e.g. a 64-char txid) that are hard to
        read in a message box. This keeps only the first and last 8 characters
        joined by an ellipsis (e.g. ``9f1b0a75…fed9ae1b``). Short ids are left
        untouched.

        Args:
            will_id: The will identifier (hash) to shorten; coerced to ``str``.

        Returns:
            str: The shortened id, or the original string if it is short.
        """
        text = str(will_id)
        # Only shorten when there is something to gain (>= 20 chars), otherwise
        # the ellipsis form would not actually be shorter or clearer.
        if len(text) <= 20:
            return text
        return f"{text[:8]}\u2026{text[-8:]}"

    @staticmethod
    def _format_locktime(locktime):
        """Format a UNIX locktime timestamp as a readable UTC date string.

        Args:
            locktime: UNIX timestamp (seconds) to format.

        Returns:
            str: A string like ``2026-06-22 11:00 UTC``. If formatting fails
            for any reason, the raw timestamp is returned as a fallback so the
            caller always has something to show.
        """
        try:
            dt = datetime.fromtimestamp(int(locktime), tz=timezone.utc)
            return dt.strftime("%Y-%m-%d %H:%M UTC")
        except Exception:
            # Never let date formatting break the (already exceptional) flow.
            return str(locktime)

    @staticmethod
    def check_will_expired(all_inputs_min_locktime, timestamp_to_check):
        """Raise WillExpiredException if any valid transaction has expired.

        Locktimes are always UNIX timestamps, so a transaction is expired when
        its locktime is in the past relative to ``timestamp_to_check``.

        When a will is expired the message is written to be reassuring rather
        than alarming: being past the locktime is an EXPECTED situation that the
        plugin handles by invalidating the old will and re-signing it. The
        message therefore uses a shortened will id and a human-readable UTC date
        instead of raw values.

        Args:
            all_inputs_min_locktime: Mapping prevout -> will-item info, used to
                find the minimum locktime per input.
            timestamp_to_check: Reference UNIX timestamp (usually "now").
        """
        _logger.info("check if some transaction is expired")
        for _inputs, wid in all_inputs_min_locktime.items():
            for w in wid:
                if w[1].get_status("VALID"):
                    locktime = int(wid[0][1].tx.locktime)
                    # Locktimes are always timestamps: expired when in the past.
                    if locktime < int(timestamp_to_check):
                        # Build a clear, non-technical message: short id +
                        # readable date, and explain what will happen next.
                        short_id = Will._short_will_id(wid[0][0])
                        when = Will._format_locktime(locktime)
                        # The message is rendered as HTML by msg_warning(),
                        # so a <br> tag forces a line break: the second
                        # sentence goes on its own line to avoid an overly
                        # long single line in the wizard.
                        raise WillExpiredException(
                            "Will expired (id {id}, locktime {when}) \u2014<br>"
                            "too late to anticipate, the will will be "
                            "invalidated and re-signed.".format(
                                id=short_id, when=when
                            )
                        )
                    else:
                        _logger.debug(
                            f"Will Not Expired {wid[0][0]}: "
                            f"{Will._format_locktime(locktime)} > "
                            f"{Will._format_locktime(timestamp_to_check)}"
                        )

    # def check_all_input_spent_are_in_wallet():
    #    _logger.info("check all input spent are in wallet or valid txs")
    #    for inp, ws in all_inputs.items():
    #        if not Util.in_utxo(inp, all_utxos):
    #            for w in ws:
    #                if w[1].get_status("VALID"):
    #                    prevout_id = w[2].prevout.txid.hex()
    #                    parentwill = will.get(prevout_id, False)
    #                    if not parentwill or not parentwill.get_status("VALID"):
    #                        w[1].set_status("INVALIDATED", True)

    @staticmethod
    def only_valid_list(will):
        out = {}
        for wid, w in will.items():
            if w.get_status("VALID"):
                out[wid] = w
        return out

    @staticmethod
    def only_valid_or_replaced_list(will):
        out = []
        for wid, w in will.items():
            wi = w
            if wi.get_status("VALID") or wi.get_status("REPLACED"):
                out.append(wid)
        return out

    @staticmethod
    def check_sent_txs_not_postponed(will, heirs, wallet):
        """Raise WillPostponedException for a postponed, already SENT old tx.

        Covers the will items that :meth:`check_willexecutors_and_heirs` does
        not look at: items that are no longer ``VALID`` (``REPLACED``, or
        marked ``INVALIDATED`` without a real on-chain spend) but were sent to
        a will-executor (``PUSHED``), are not ``CONFIRMED``/``MEMPOOL`` and
        are still executable (no input spent by a broadcast transaction, see
        :meth:`_tx_still_executable`). If the delivery date now requested for
        one of their heirs is LATER than the date frozen in the transaction,
        the old transaction would be executed before the new will: it must be
        invalidated on-chain first, exactly like the postpone of a VALID
        signed will.

        WHY (BUG-2): after a Cancel at "Sign your will" the will is left with
        unsigned VALID txs and the old sent txs are REPLACED. When the date is
        then moved later, the unsigned txs are dropped, the will has no VALID
        tx and was simply rebuilt at the new date, while the executors kept
        the old txs with the EARLIER date. A fee or heir change alone is not a
        postpone: the replaced tx keeps the requested date or a later one.

        The requested date is resolved per heir with
        :meth:`Util.resolve_locktime_against_tx`, the same rule used for the
        VALID items (relative "1y"/"30d" recipes included). Heirs that are not
        in the old transaction (added, or pseudo-heirs of the executor fee) are
        skipped; the heirs present in both decide.

        Without a usable wallet nothing is decided (an unspent coin cannot be
        told apart from a spent one).
        """
        if not wallet or not getattr(wallet, "adb", None):
            return
        for wid, w in will.items():
            if (
                w.get_status("VALID")
                or not w.get_status("PUSHED")
                or w.get_status("CONFIRMED")
                or w.get_status("MEMPOOL")
            ):
                continue
            if not Will._tx_still_executable(wid, will, wallet):
                continue
            postponed = Will._postponed_heir(w, heirs)
            if postponed:
                wheir, tx_locktime, new_locktime = postponed
                raise WillPostponedException(
                    f"{wheir}: locktime postponed {tx_locktime}->"
                    f"{new_locktime} on a sent, replaced will tx {wid[:8]}"
                )

    @staticmethod
    def _postponed_heir(w, heirs):
        """Return ``(heir, tx_locktime, new_locktime)`` if ``w`` is postponed.

        For every heir present both in the will item ``w`` and in the current
        ``heirs`` settings (by name; pseudo-heirs of the executor fee are
        skipped), the requested delivery date is resolved with
        :meth:`Util.resolve_locktime_against_tx` against the locktime frozen
        in ``w.tx`` (relative "1y"/"30d" recipes included). The first heir
        whose requested date is LATER than the tx locktime is returned;
        ``None`` when no heir is postponed. Address or amount changes do not
        matter here: a later date alone makes the frozen tx execute before
        the new will. Shared by the VALID check (#73) and the sent-but-replaced
        check (#72).
        """
        tx_locktime = int(w.tx.locktime)
        for wheir, their in (w.heirs or {}).items():
            if 'w!ll3x3c"' == wheir[:9]:
                continue
            heir = heirs.get(wheir, None)
            if not heir:
                continue
            new_locktime = Util.resolve_locktime_against_tx(
                heir[2], their[2], tx_locktime
            )
            if new_locktime > tx_locktime:
                return wheir, tx_locktime, new_locktime
        return None

    @staticmethod
    def _heir_change(name, heir, will_only_valid):
        """Say what changed for an heir the VALID will items do not cover.

        Used only to word the Check message (CHANGELOG #77), never to decide
        anything. The first VALID item that carries ``name`` is compared with
        the settings entry ``heir`` ([address, amount, locktime]): a different
        address, then a different amount, else the delivery date. When no
        VALID item carries ``name`` the heir was added.
        """
        for w in will_only_valid.values():
            their = (w.heirs or {}).get(name)
            if not their:
                continue
            if heir[0] != their[0]:
                return HeirNotFoundException.HEIR_ADDRESS
            if heir[1] != their[1]:
                return HeirNotFoundException.HEIR_AMOUNT
            return HeirNotFoundException.HEIR_DATE
        return HeirNotFoundException.HEIR_ADDED

    @staticmethod
    def sync_heir_names(will, heirs):
        """Write the new name of an heir that was ONLY renamed (#135).

        Owner's ZIP test: after renaming an heir (same address, share and
        date) every Check said "Heir renamed …: preparing a new will",
        rebuilt the very same transactions and changed nothing. The copy of
        a will-executor that the rebuild does not make again (one
        deselected) kept the old name, so the next Check saw the rename
        again, forever.

        A pure rename is recognised up front, item by item (the VALID items
        and the will in force kept as REPLACED): an heir name of an item
        that the Heirs tab no longer has, and exactly ONE heir of the tab
        that the item does not carry with the SAME address and the same
        amount (:meth:`_pure_rename`). Then only
        the NAME is changed in the items' stored heirs (``WillItem.heirs``,
        the plugin's own metadata, same key and format). Funds-safe: the
        transaction (outputs, amounts, locktime), its signatures, statuses
        and what the will-executors hold do not change: names are not in
        the transaction; and the usual check then runs under the new name,
        so a date (or anything else) that changed too is still found and
        rebuilt. A different share, two candidates or a name used twice is
        not touched and goes to the usual check and rebuild.

        Returns ``[(old, new)]`` (sorted), empty when nothing was renamed.
        """
        current = dict(heirs or {})
        # The VALID items and the will in force kept as REPLACED (signed
        # and/or sent, #128: what the will-executors hold); never the
        # invalidated, executed or old unsigned items (their names are the
        # history). Owner's ZIP test (A -> B -> A): every decision is per
        # item and per name, so one item that cannot be matched (an old
        # name of another heir, another share) no longer blocks the others.
        targets = [w for w in (will or {}).values()
                   if w.get_status("VALID") or will_state.may_be_in_force(w)]
        renames = set()
        # Each reason is logged once per name and Check (the owner's log
        # had the same old heir ten times: one line per copy).
        logged = set()
        for w in targets:
            item_heirs = w.heirs or {}
            item_map = {}
            for name, their in item_heirs.items():
                if str(name).startswith('w!ll3x3c"') or name in current:
                    continue
                new_name = Will._pure_rename(name, their, current, item_heirs, logged)
                if new_name is not None:
                    item_map[name] = new_name
            if not item_map or len(set(item_map.values())) != len(item_map):
                continue
            w.heirs = {item_map.get(k, k): v for k, v in item_heirs.items()}
            renames.update(item_map.items())
        return sorted(renames)

    @staticmethod
    def _pure_rename(name, their, current, item_heirs, logged=None):
        """The new name of the heir ``name`` of one item, or None (#135).

        ``their``: its entry in the item; ``current``: the Heirs tab. The
        ONE heir of the tab that the item does not carry, at the same
        address (``heirs.address_key``) with the same amount (compared by
        value: "20%" and "20.0%" are the same share). #168: with a table of
        deliveries, a row of the same delivery only. Logged when it is not
        a pure rename, so the reason can be read in the log.
        """
        try:
            key = address_key(their[0])
            amount = their[1]
        except (IndexError, TypeError):
            return None
        if key is None:
            return None
        found = []
        # #168: with a table of deliveries a person has one row per
        # delivery on the same address ("Marco", "Marco§2"): only the row
        # of the SAME delivery can be the new name. Without a table every
        # row is delivery 1, as before.
        number = instalment_number(name)
        for new_name, heir in current.items():
            try:
                if (address_key(heir[0]) == key and new_name not in item_heirs
                        and instalment_number(new_name) == number):
                    found.append(new_name)
            except (IndexError, TypeError):
                continue
        reason = None
        if len(found) != 1:
            reason = f"{len(found)} heirs at its address"
        elif not Will._same_amount(current[found[0]][1], amount):
            reason = f"amount {amount!r} -> {current[found[0]][1]!r}"
        if reason is not None:
            if logged is None or (name, reason) not in logged:
                _logger.info(f"heir {name!r}: not a pure rename ({reason})")
                if logged is not None:
                    logged.add((name, reason))
            return None
        return found[0]

    @staticmethod
    def _same_amount(a, b):
        """True when two written amounts are the same share or sum ("20%"
        and "20.0%", "1000" and "1000.0"); a percentage never equals a
        fixed amount."""
        a, b = str(a).strip(), str(b).strip()
        if a == b:
            return True
        if a.endswith("%") != b.endswith("%"):
            return False
        try:
            return abs(float(a.rstrip("%")) - float(b.rstrip("%"))) < 1e-9
        except ValueError:
            return False

    @staticmethod
    def _renamed_to(old_name, their, heirs, will_heirs):
        """The new name of the heir ``old_name`` when it was only renamed.

        ``their``: its entry in the will item ([address, amount, ...]);
        ``heirs``: the Heirs tab now; ``will_heirs``: the item's heirs. The
        new name is the ONE heir of the Heirs tab with the same address that
        the item does not carry; ``None`` otherwise (removed, or ambiguous).
        Used only to word the Check message (#135 follow-up). #168: only a
        row of the same delivery counts.
        """
        try:
            address = str(their[0])
        except (IndexError, TypeError):
            return None
        # #168: only the row of the same delivery (see _pure_rename).
        number = instalment_number(old_name)
        found = [name for name, heir in (heirs or {}).items()
                 if name not in (will_heirs or {}) and str(heir[0]) == address
                 and instalment_number(name) == number]
        return found[0] if len(found) == 1 else None

    @staticmethod
    def received(w):
        """True when the will-executor of ``w`` has received it (#141).

        VALID with a will-executor and PUSHED: the push was accepted, or
        the verification found it (``set_check_willexecutor``; a failed
        verification clears PUSHED). The will-executor checked its fee
        address and fee when it accepted the copy, and broadcasts it as it
        is at the delivery (bal-server and bal-pusher read on 2026-10-15).
        """
        return bool(w.get_status("VALID") and w.we and w.get_status("PUSHED"))

    @staticmethod
    def terms_change(stored, current):
        """What changed in a will-executor's payment data (#141).

        ``stored``: the will-executor written in a will item; ``current``:
        the same URL in the will-executor list now. Returns ``"address"``
        (another fee address, maybe with another fee), ``"fee"`` (only the
        fee) or None (nothing, another URL, or no data to compare).
        """
        try:
            if not current or stored["url"] != current.get("url", stored["url"]):
                return None
            if stored["address"] != current["address"]:
                return "address"
            if stored["base_fee"] != current["base_fee"]:
                return "fee"
        except (KeyError, TypeError, AttributeError):
            return None
        return None

    @staticmethod
    def received_terms(will, willexecutors):
        """The payment data to keep for each will-executor (#141).

        ``{url: {"address": ..., "base_fee": ...}}`` for every will-executor
        whose received copies (:meth:`received`) were built with another fee
        address or fee than ``willexecutors`` gives now. A build with these
        data gives the same transactions again when nothing else changed,
        so the copies stay valid and nothing is signed again
        (``engine.build.build_will`` keeps only the ones that do).
        """
        pins = {}
        for w in Will.only_valid_list(will).values():
            if not Will.received(w):
                continue
            url = w.we.get("url")
            if Will.terms_change(w.we, willexecutors.get(url)):
                pins.setdefault(url, {
                    "address": w.we["address"], "base_fee": w.we["base_fee"]})
        return pins

    @staticmethod
    def same_payments(new_tx, will, url):
        """True when ``new_tx`` spends and pays the same as a received copy
        for ``url`` in ``will``, the date apart (#141).

        The date may still be moved by ``update_will`` (anticipation); the
        inputs and outputs tell whether the build with the kept payment
        data gave the transaction the will-executor already holds.
        """
        def payments(tx):
            ins = sorted(i.prevout.to_str() for i in tx.inputs())
            outs = sorted((o.scriptpubkey.hex(), o.value) for o in tx.outputs())
            return ins, outs

        target = payments(new_tx)
        for w in Will.only_valid_list(will).values():
            if Will.received(w) and w.we.get("url") == url:
                if payments(w.tx) == target:
                    return True
        return False

    @staticmethod
    def check_willexecutors_and_heirs(
        will, heirs, willexecutors, self_willexecutor, check_date, tx_fees
    ):
        """Check that the VALID will items still match the heirs and the
        will-executors; raise the reason when they do not.

        ``heirs``: the heir rows the will is built from (with a table of
        the deliveries, the rows of every delivery, #168). A postpone of a
        signed or sent item raises ``WillPostponedException``; an heir
        removed, renamed, added or changed raises ``HeirNotFoundException``
        (with ``all_heirs`` when the heirs of the same delivery all have
        the new date, #125 and #169, and ``staged`` when the will has
        deliveries 2 or 3, so the messages name the delivery);
        a will-executor change raises its own exception. Returns False when
        there is no VALID item.
        """
        _logger.debug("check willexecutors heirs")
        no_willexecutor = 0
        willexecutors_found = {}
        terms_changed = {}
        heirs_found = {}
        # #169: a will in deliveries ("Luca§2" rows): the messages name the
        # delivery of every row, also "Luca (delivery 1)".
        staged = any(instalment_number(x) > 1 for x in heirs)
        will_only_valid = Will.only_valid_list(will)
        if len(will_only_valid) < 1:
            return False
        # BUG-1 (CHANGELOG #73): detect the postpone of a signed/sent tx FIRST.
        # The loop below stops at the first difference it meets (fee rate,
        # heir address/amount) and used to report it as a plain update, so a
        # date moved later TOGETHER with a fee or heir change was rebuilt one
        # day before the old tx (overwriting the requested date) and the old
        # signed tx stayed in force. A later date on a signed/sent tx must be
        # invalidated on-chain first, whatever else changed with it.
        for w in will_only_valid.values():
            if not (w.get_status("COMPLETE") or w.get_status("PUSHED")):
                continue
            postponed = Will._postponed_heir(w, heirs)
            if postponed:
                wheir, tx_locktime, new_locktime = postponed
                raise WillPostponedException(
                    f"{wheir}: locktime postponed {tx_locktime}->"
                    f"{new_locktime} on a signed/sent will"
                )
        for wid in Will.only_valid_list(will):
            w = will[wid]
            if w.tx_fees != tx_fees:
                raise TxFeesChangedException(f"{tx_fees}:  {w.tx_fees}")
            for wheir in w.heirs:
                if not 'w!ll3x3c"' == wheir[:9]:
                    their = will[wid].heirs[wheir]
                    if heir := heirs.get(wheir, None):

                        if heir[0] == their[0] and heir[1] == their[1]:
                            # IMPORTANT: compare against the locktime that is
                            # actually frozen inside the already-signed Bitcoin
                            # transaction (w.tx.locktime), NOT against their[2].
                            # their[2] is the heir entry stored in the will item,
                            # which is updated in memory together with the new
                            # heirs dict when the user postpones, so it would
                            # always equal new_locktime and the postpone would go
                            # undetected.  w.tx.locktime is immutable once signed
                            # and is exactly what the will-executors hold.
                            tx_locktime = int(w.tx.locktime)
                            # The requested (possibly new) locktime for this heir.
                            # A RELATIVE recipe ("1y"/"30d") is resolved against
                            # the moment the signed tx was built, NOT against now:
                            # re-parsing it from "now" drifts it one day per day
                            # away from the frozen tx locktime, so an UNCHANGED
                            # will would be read as a POSTPONE and the plugin
                            # would ask to invalidate it every day.
                            new_locktime = Util.resolve_locktime_against_tx(
                                heir[2], their[2], tx_locktime
                            )
                            unanchored = (
                                Util._relative_days(heir[2]) is not None
                                and Util._relative_days(their[2]) is None
                            )
                            if new_locktime == tx_locktime and unanchored:
                                # CHANGELOG #81: the will was built from a
                                # FIXED date and the setting is now a period
                                # giving the same date TODAY ("365d"). A period
                                # can only be anchored to a will built from a
                                # period: tomorrow it would read one day later,
                                # i.e. a postpone (a paid invalidation). Rebuild
                                # now instead (no fee; anticipation rule).
                                # The rebuilt transactions are identical and
                                # update_will keeps the signed items (#122).
                                pass
                            elif new_locktime == tx_locktime:
                                # Unchanged: this heir is still coherent.
                                count = heirs_found.get(wheir, 0)
                                heirs_found[wheir] = count + 1
                            elif new_locktime > tx_locktime and (
                                w.get_status("COMPLETE") or w.get_status("PUSHED")
                            ):
                                # POSTPONE of an already signed/sent will: the
                                # old pre-signed tx must be invalidated on-chain
                                # first, otherwise a will-executor could
                                # broadcast the earlier-locktime tx and execute
                                # the inheritance too early.
                                raise WillPostponedException(
                                    f"{wheir}: locktime postponed "
                                    f"{tx_locktime}->{new_locktime} "
                                    f"on a signed/sent will"
                                )
                            # ANTICIPATE (new_locktime < tx_locktime): the user
                            # manually moved the delivery date EARLIER.
                            #   * If the new date is still in the FUTURE, this is
                            #     a plain ANTICIPATE: it falls through here and is
                            #     rebuilt via HeirNotFoundException (no on-chain
                            #     fee). It must NEVER invalidate on-chain, even if
                            #     the will was already signed/sent (A3, owner
                            #     decision D2 = A1).
                            #   * If the new date is in the PAST (relative to the
                            #     check date) the will is genuinely expired and
                            #     check_will_expired -> WillExpiredException handles
                            #     it (on-chain invalidation). That is a different
                            #     situation from "anticipate" and is intentionally
                            #     kept.
                            #
                            # new_locktime > tx_locktime on a will that was never
                            # signed/sent also falls through here -> a plain rebuild
                            # via HeirNotFoundException (no on-chain fee needed).
                    else:
                        # The will still carries this heir, but the heir is no
                        # longer present in the current heirs set: the user
                        # removed it.  This must trigger a rebuild exactly like
                        # "heir added" does, otherwise the removed heir would
                        # silently stay in the inheritance transaction.  Raising
                        # HeirNotFoundException reuses the same rebuild path used
                        # by the Check button and by on_close (Electrum quit).
                        _logger.debug(
                            f"heir removed, transaction is not valid:"
                            f"{wheir} {wid}, {w}"
                        )
                        # #135 follow-up (owner): a heir with the SAME
                        # address under a new name is a rename, worded as
                        # such. Only the message changes: same exception,
                        # same rebuild (recognising a rename without a
                        # rebuild is step #137).
                        renamed_to = Will._renamed_to(wheir, their, heirs, w.heirs)
                        if renamed_to is not None:
                            raise HeirNotFoundException(
                                wheir, HeirNotFoundException.HEIR_RENAMED,
                                new_name=renamed_to, staged=staged,
                            )
                        raise HeirNotFoundException(
                            wheir, HeirNotFoundException.HEIR_REMOVED,
                            staged=staged,
                        )

            if willexecutor := w.we:
                url = willexecutor["url"]
                count = willexecutors_found.get(url, 0)
                current = willexecutors.get(url, None)
                if Util.cmp_willexecutor(willexecutor, current):
                    willexecutors_found[url] = count + 1
                elif changed := Will.terms_change(willexecutor, current):
                    # CHANGELOG #141: the will-executor gives another fee
                    # address (it changes with the user's IP) or fee. A
                    # copy it already received stays valid: it accepted it
                    # and broadcasts it as it is. Only a copy not received
                    # yet is redone, and the reason is said.
                    if Will.received(w):
                        willexecutors_found[url] = count + 1
                    else:
                        terms_changed.setdefault(url, changed)

            else:
                no_willexecutor += 1
        # CHANGELOG #77: report an ADDED heir before a CHANGED one, so that
        # "carol added" (with the others' shares adjusted) names carol, not
        # the first heir whose share changed.
        missing = [h for h in heirs if h not in heirs_found]
        if missing:
            changes = {
                h: Will._heir_change(h, heirs[h], will_only_valid) for h in missing
            }
            added = [h for h in missing
                     if changes[h] == HeirNotFoundException.HEIR_ADDED]
            h = added[0] if added else missing[0]
            _logger.debug(f"heir: {h} not found ({changes[h]})")
            # CHANGELOG #125: when every heir has the same delivery date (the
            # normal case: multiverse off gives them the will's date), a new
            # date concerns the whole will, not the heir named first. #169:
            # with a table of the deliveries, the heirs of the SAME delivery
            # ("Luca§2", "John§2") share its date: a new date concerns that
            # delivery. A will with one date has delivery 1 only: as before.
            number = instalment_number(h)
            same_date = len({str(heirs[x][2]) for x in heirs
                             if instalment_number(x) == number}) == 1
            raise HeirNotFoundException(
                h, changes[h],
                all_heirs=changes[h] == HeirNotFoundException.HEIR_DATE and same_date,
                staged=staged,
            )
        count_heirs = 0
        for h in heirs:
            if Util.parse_locktime_string(heirs[h][2]) >= check_date:
                count_heirs += 1
        if not count_heirs:
            raise NoHeirsException("there are not valid heirs")
        if self_willexecutor and no_willexecutor == 0:
            raise NoWillExecutorNotPresent("Backup tx")
        for url, we in willexecutors.items():
            if Willexecutors.is_selected(we) and Willexecutors.is_valid(we):
                if url not in willexecutors_found:
                    if url in terms_changed:
                        _logger.debug(
                            f"will-executor: {url} new {terms_changed[url]}, "
                            "its copy was not received yet"
                        )
                        raise WillExecutorTermsChanged(url, terms_changed[url])
                    _logger.debug(f"will-executor: {url} not fount")
                    raise WillExecutorNotPresent(url)
        _logger.info("will is coherent with heirs and will-executors")
        return True



def format_status_history(status: str) -> str:
    """Return the saved status history of a will item in the GUI language.

    ``WillItem.status`` is a dot-separated history such as
    ``".Signed.Pushed.NOT Valid"``. It is stored in English (see
    ``WillItem.set_status``) and translated only here, for display. A token
    that is not a known English label is shown unchanged: this keeps working
    the histories written by older versions, which stored the label already
    translated (for example ``"Firmato"``).
    """
    labels = {label for label, _flag in WillItem.STATUS_DEFAULT.values()}
    labels.update(WillItem.LEGACY_STATUS_LABELS)

    def translate(token):
        # "ERROR!!!" is glued to the end of the history when an item has no
        # id (see WillItem.__init__): keep it, translate what comes before.
        suffix = ""
        if token.endswith("ERROR!!!"):
            token, suffix = token[: -len("ERROR!!!")], "ERROR!!!"
        if token in labels:
            token = _(token)
        elif token.startswith("NOT ") and token[len("NOT "):] in labels:
            token = _("NOT {}").format(_(token[len("NOT "):]))
        return token + suffix

    return ".".join(translate(token) for token in status.split("."))


class WillItem(Logger):
    # Default status flags for an inheritance transaction.
    # Each entry maps an internal status key to [human-readable label, default
    # boolean value].
    #
    # A2 changes:
    #   * "PENDING" was renamed to "MEMPOOL" (the transaction has been seen in
    #     the Electrum mempool). Old saved wills that still carry the legacy
    #     "PENDING" key are migrated to "MEMPOOL" in __init__ (see below), so
    #     nothing is lost.
    #   * "UPDATED" was added: the transaction was spendable AND valid, and a new
    #     transaction replaces it while keeping the SAME locktime and SAME heirs.
    #     UPDATED keeps the VALID flag (see set_status).
    #
    # The labels are marked with N_() but stay English here: they are written
    # into the saved status history (see set_status) and translated only when
    # shown, by format_status_history().
    STATUS_DEFAULT = {
        "ANTICIPATED": [N_("Anticipated"), False],
        "BROADCASTED": [N_("Broadcasted"), False],
        "CHECKED": [N_("Checked"), False],
        "CHECK_FAIL": [N_("Check Failed"), False],
        "COMPLETE": [N_("Signed"), False],
        "CONFIRMED": [N_("Confirmed"), False],
        "ERROR": [N_("Error"), False],
        "EXPIRED": [N_("Expired"), False],
        "EXPORTED": [N_("Exported"), False],
        "IMPORTED": [N_("Imported"), False],
        "INVALIDATED": [N_("Invalidated"), False],
        "MEMPOOL": [N_("Mempool"), False],
        "PUSH_FAIL": [N_("Push failed"), False],
        "PUSHED": [N_("Pushed"), False],
        "PARTIALLY_SIGNED": [N_("Partially Signed"), False],
        "REPLACED": [N_("Replaced"), False],
        "RESTORED": [N_("Restored"), False],
        "UPDATED": [N_("Updated"), False],
        "VALID": [N_("Valid"), True],
    }

    # Labels found in histories saved by older versions but no longer
    # written: "New" opened every history (e.g. tests/samanta7).
    # format_status_history() still translates them.
    LEGACY_STATUS_LABELS = (N_("New"),)

    # Translations of "New" that test builds of the i18n work saved INTO the
    # status history (window.py / the CLI used ``_("New")`` when building).
    # They are mapped back to "New" on load so the history is language
    # neutral again. Keep in sync with the "New" msgstr of every catalog in
    # bal/locale (tests/test_core_status_language_neutral.py checks it).
    TRANSLATED_NEW_LABELS = ("Nuovo", "Nuevo")

    # Fields that Willexecutors.get_willexecutor_transactions /
    # push_transactions_to_willexecutor used to write into the item's own
    # executor record (older versions); they are transient push data.
    TRANSIENT_PUSH_FIELDS = ("txs", "txsids", "broadcast_status")

    @staticmethod
    def _untranslate_new_status(status):
        """Map a translated leading "New" of a saved history back to "New"."""
        head, sep, rest = status.partition(".")
        if head in WillItem.TRANSLATED_NEW_LABELS:
            return "New" + sep + rest
        return status

    @staticmethod
    def _without_transient_push_fields(we):
        """Return ``we`` without the transient push fields (a copy if needed)."""
        if not isinstance(we, dict) or not any(
            k in we for k in WillItem.TRANSIENT_PUSH_FIELDS
        ):
            return we
        return {
            k: v for k, v in we.items() if k not in WillItem.TRANSIENT_PUSH_FIELDS
        }

    def set_status(self, status, value=True):
        """Set a status flag and apply the related side effects.

        Some statuses imply that other statuses must change. The rules below
        match the inheritance state machine:

        VALID handling:
            * INVALIDATED, REPLACED, CONFIRMED, MEMPOOL -> clear VALID
              (the transaction can no longer be delivered as a valid will tx).
            * ANTICIPATED -> KEEPS VALID. Anticipating only moves the locktime
              earlier by 1 day; the transaction stays valid (it is NOT in the
              "clear VALID" list on purpose).
            * UPDATED -> KEEPS VALID. The transaction is replaced by a new one
              that keeps the SAME locktime and SAME heirs, so it stays valid
              (it is NOT in the "clear VALID" list on purpose).

        Other side effects:
            * CONFIRMED, MEMPOOL -> clear INVALIDATED (the tx is on-chain or in
              the mempool, so it is no longer considered invalidated).
            * PUSHED -> clear PUSH_FAIL and CHECK_FAIL.
            * CHECKED -> set PUSHED and clear PUSH_FAIL.

        Args:
            status: The status key to set (must exist in STATUS).
            value: True to set the flag, False to clear it. Defaults to True.

        Returns:
            The applied boolean value, or None if the flag was already set to
            that value (no change).
        """
        if self.STATUS[status][1] == bool(value):
            return None

        # The history is saved in the wallet, in exported files and in QR
        # transfers, so it is always written in English: the saved data must
        # not depend on the GUI language (older versions wrote the translated
        # label). format_status_history() translates it when it is shown.
        self.status += "." + (("NOT " if not value else "") + self.STATUS[status][0])
        self.STATUS[status][1] = bool(value)
        if value:
            # NOTE: ANTICIPATED and UPDATED are intentionally NOT in this list,
            # so they keep the VALID flag (see docstring above).
            if status in ["INVALIDATED", "REPLACED", "CONFIRMED", "MEMPOOL"]:
                self.STATUS["VALID"][1] = False

            if status in ["CONFIRMED", "MEMPOOL"]:
                self.STATUS["INVALIDATED"][1] = False

            if status in ["PUSHED"]:
                self.STATUS["PUSH_FAIL"][1] = False
                self.STATUS["CHECK_FAIL"][1] = False

            if status in ["CHECKED"]:
                self.STATUS["PUSHED"][1] = True
                self.STATUS["PUSH_FAIL"][1] = False

            if status in ["COMPLETE"]:
                self.STATUS["PARTIALLY_SIGNED"][1] = False

        return value

    def get_status(self, status):
        return self.STATUS[status][1]

    def __init__(self, w, _id=None, wallet=None):
        if isinstance(w, WillItem):
            # Copy a WillItem WITHOUT deepcopy. Serialize it to its plain-dict
            # form and deserialize from there: the tx is re-parsed into a fresh
            # object, STATUS is rebuilt from the clones below and heirs /
            # will-executors are cloned recursively, so the copy shares no
            # mutable state with the source. See also copy().
            data = w.to_dict()
            data["heirs"] = copy_structure(w.heirs) if w.heirs is not None else None
            data["willexecutor"] = (
                copy_structure(w.we) if w.we is not None else None
            )
            if not _id:
                _id = w._id
            w = data
        self.tx = Will.get_tx_from_any(w["tx"])
        self.heirs = w.get("heirs", None)
        self.we = WillItem._without_transient_push_fields(
            w.get("willexecutor", None)
        )
        self.status = WillItem._untranslate_new_status(w.get("status") or "")
        self.description = w.get("description", None)
        self.time = w.get("time", None)
        self.change = w.get("change", None)
        self.tx_fees = w.get("baltx_fees", 0)
        self.sigs_required = int(w.get("sigs_required", 0))
        self.sigs_have = int(w.get("sigs_have", 0))
        self.father = w.get("Father", None)
        self.children = w.get("Children", None)
        self.STATUS = WillItem.copy_status_table(WillItem.STATUS_DEFAULT)
        for s in self.STATUS:
            self.STATUS[s][1] = w.get(s, WillItem.STATUS_DEFAULT[s][1])
        # Backward-compatibility migration (A2): the "PENDING" status was
        # renamed to "MEMPOOL". Wills saved by older versions of the plugin
        # store the flag under the legacy "PENDING" key, so if that key is
        # present and set, carry it over to "MEMPOOL". This way no state is
        # lost when loading an older will. The new key always wins if both
        # happen to be present.
        if "MEMPOOL" not in w and w.get("PENDING"):
            self.STATUS["MEMPOOL"][1] = True
        if not _id:
            self._id = self.tx.txid()
        else:
            self._id = _id

        if not self._id:
            self.status += "ERROR!!!"
            self.valid = False

        if wallet:
            self.tx.add_info_from_wallet(wallet)

    def copy(self, wallet=None):
        """Return an independent copy of this WillItem (no deepcopy).

        The copy is produced by serializing this item and deserializing it:
        the transaction is re-parsed, the STATUS table is rebuilt and
        heirs / will-executors are cloned recursively, so the result shares no
        mutable state with ``self``.  Pass a ``wallet`` when the copy's tx
        needs its address/value information restored
        (``tx.add_info_from_wallet``).
        """
        return WillItem(self, _id=self._id, wallet=wallet)

    @staticmethod
    def copy_status_table(status_table):
        """Clone a STATUS table (``{flag: [label, bool]}``) without deepcopy.

        Both the outer dict and every inner ``[label, bool]`` list are new
        objects, so mutating the returned table never affects the source.
        """
        return {k: [label, value] for k, (label, value) in status_table.items()}

    def to_dict(self):
        out = {
            "_id": self._id,
            "tx": self.tx,
            "heirs": self.heirs,
            "willexecutor": self.we,
            "status": self.status,
            "description": self.description,
            "time": self.time,
            "change": self.change,
            "baltx_fees": self.tx_fees,
            "sigs_required": self.sigs_required,
            "sigs_have": self.sigs_have,
            "Father": self.father,
            "Children": self.children,
        }
        for key in self.STATUS:
            try:
                out[key] = self.STATUS[key][1]
            except Exception as e:
                _logger.error(f"{key},{self.STATUS[key]} {e}")

        return out

    def __repr__(self):
        return str(self)

    def __str__(self):
        return str(self.to_dict())

    def set_anticipate(self, ow: "WillItem"):
        nl = min(ow.tx.locktime, Will.check_anticipate(ow, self))
        if int(nl) < self.tx.locktime:
            self.tx.locktime = int(nl)
            return True
        else:
            return False

    def search_anticipate(self, all_inputs):
        anticipated = False
        for ow in self.search(all_inputs):
            if self.set_anticipate(ow):
                anticipated = True
        return anticipated

    def search(self, all_inputs):
        for inp in self.tx.inputs():
            prevout_str = inp.prevout.to_str()
            oinps = all_inputs.get(prevout_str, [])
            for oinp in oinps:
                ow = oinp[1]
                if ow._id != self._id:
                    yield ow

    def normalize_locktime(self, all_inputs):
        outputs = self.tx.outputs()
        for idx in range(0, len(outputs)):
            inps = all_inputs.get(f"{self._id}:{idx}", [])
            _logger.debug("****check locktime***")
            for inp in inps:
                if inp[0] != self._id:
                    iw = inp[1]
                    self.set_anticipate(iw)

    def set_check_willexecutor(self,resp):
        """Record the will-executor's answer ``resp`` to the check (searchtx).

        It holds exactly our transaction: PUSHED and CHECKED. Any other
        answer, or ``None`` (no usable answer): CHECK_FAIL and no longer
        PUSHED, so the copy is sent again. #147: ``Willexecutors.NO_ANSWER``
        (it did not answer in time) changes nothing: the copy stays sent and
        to be verified, and the next check asks again. Returns True for an
        answer, False otherwise.
        """
        if resp is Willexecutors.NO_ANSWER:
            _logger.info(f"no answer to the check for {self._id}: left as it was")
            return False
        try:
            if resp :
                if "tx" in resp and resp["tx"] == str(self.tx):
                    self.set_status("PUSHED")
                    self.set_status("CHECKED")
                else:
                    self.set_status("CHECK_FAIL")
                    self.set_status("PUSHED", False)
                return True
            else:
                self.set_status("CHECK_FAIL")
                self.set_status("PUSHED", False)
                return False
        except Exception as e:
            _logger.error(f"exception checking transaction: {e}")
            self.set_status("CHECK_FAIL")

    # NOTE: the former ``get_color()`` method (which returned hard-coded hex
    # colours for the GUI) has been moved out of the core logic to
    # ``bal.gui.qt.theme.status_color``.  The status flags above remain the
    # single source of truth; the GUI maps them to colours.


class WillException(Exception):
    def __init__(self,msg="WillException"):
        self.msg=msg
        Exception.__init__(self)
    def __str__(self):
        return self.msg



class WillChainError(WillException):
    """A chain of delivery instalments (multiverse) that cannot be built
    safely (CHANGELOG #166). Not a "will to prepare again" case: the will
    in force is left as it is and the reason is shown."""


class WillExpiredException(WillException):
    pass


class NotCompleteWillException(WillException):
    pass


class HeirChangeException(NotCompleteWillException):
    pass


class TxFeesChangedException(NotCompleteWillException):
    pass


class HeirNotFoundException(NotCompleteWillException):
    """An heir in the settings is not matched by the current will.

    ``str(e)`` is still the heir name (callers rely on it). ``change`` says
    what happened to that heir, so the Check dialog can name it (CHANGELOG
    #77): one of :data:`HEIR_REMOVED`, :data:`HEIR_ADDED`,
    :data:`HEIR_ADDRESS`, :data:`HEIR_AMOUNT`, :data:`HEIR_DATE`,
    :data:`HEIR_RENAMED` (#135 follow-up, with ``new_name``), or ``None``
    when unknown. ``all_heirs`` (CHANGELOG #125) is True for a date
    change when every heir has the same delivery date: the will's date
    changed, not one heir's. Since #169, with a table of the deliveries,
    every heir OF THE SAME DELIVERY: that delivery's date changed.
    ``staged`` (#169): the will has deliveries 2 or 3, so a row of
    delivery 1 is named "Luca (delivery 1)" too.
    """

    HEIR_REMOVED = "removed"
    HEIR_ADDED = "added"
    HEIR_ADDRESS = "address"
    HEIR_AMOUNT = "amount"
    HEIR_DATE = "date"
    # #135 follow-up: same address, new name (``new_name``).
    HEIR_RENAMED = "renamed"

    def __init__(self, heir, change=None, all_heirs=False, new_name=None,
                 staged=False):
        """Keep ``heir`` as the only arg so ``str(e)`` stays the heir name.

        ``new_name``: the heir's new name for :data:`HEIR_RENAMED`.
        ``staged``: the will has deliveries 2 or 3 (#169).
        """
        super().__init__(heir)
        self.heir = heir
        self.change = change
        self.all_heirs = all_heirs
        self.new_name = new_name
        self.staged = staged


class WillPostponedException(NotCompleteWillException):
    """An already signed/sent will is being postponed.

    When a will that has already been signed (``COMPLETE``) and/or pushed to
    will-executors (``PUSHED``) gets its locktime moved to a LATER date, the
    previously committed coins must be invalidated on-chain BEFORE rebuilding
    the new inheritance.  Otherwise a will-executor could broadcast the old
    (earlier-locktime) transaction and execute the inheritance too early to
    collect the fees.  Invalidating spends the same UTXOs now, permanently
    voiding the old pre-signed transaction.
    """

    pass


class WillexecutorChangeException(NotCompleteWillException):
    pass


class SameDateCopiesException(NotCompleteWillException):
    """A will-executor holds an incomplete will not dated later (#91).

    ``str(e)`` is the executor URL. See ``Will.find_same_date_copies``.
    """

    pass


class NoWillExecutorNotPresent(NotCompleteWillException):
    pass


class WillExecutorNotPresent(NotCompleteWillException):
    pass


class WillExecutorTermsChanged(WillExecutorNotPresent):
    """A will-executor gives a new fee address or fee, and its copy of the
    will was not received yet: the copy is redone (#141).

    ``str(e)`` is the URL (as for ``WillExecutorNotPresent``); ``change`` is
    ``"address"`` or ``"fee"`` (:meth:`Will.terms_change`).
    """

    def __init__(self, url, change):
        """Keep the URL as the message and the kind of change."""
        super().__init__(url)
        self.change = change


class NoHeirsException(WillException):
    pass
class AmountException(WillException):
    pass


class PercAmountException(AmountException):
    pass


class FixedAmountException(AmountException):
    pass
