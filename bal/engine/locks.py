"""One flow at a time per wallet (Phase 5e, CHANGELOG #116).

The command line used to run every ``bal_*`` command on Electrum's network
thread, so two commands could never overlap: the first one blocked the
thread (and the network with it) until it ended. Since #116 each command
runs on a worker thread, and this lock keeps the old order: two flows on
the SAME wallet run one after the other, flows on different wallets do not
wait for each other. Later Phase 5e steps use the same lock for the GUI
(automatic rebuild, Check), so a command and a GUI flow never change one
will at the same time.

No Qt, no ``bal.cli`` (``bal/engine`` rules, see ``bal/engine/__init__``).
"""

import threading
import weakref

# One lock per wallet object. Weak keys: a closed and forgotten wallet does
# not keep its lock (or itself) alive.
_LOCKS = weakref.WeakKeyDictionary()
# Guards the creation of the per-wallet locks (two threads asking for the
# lock of a new wallet at the same moment must get the same lock).
_GUARD = threading.Lock()


def flow_lock_for(wallet):
    """Return the flow lock of ``wallet``, created on first use.

    A plain (non-reentrant) ``threading.Lock``: a flow that already holds it
    must not ask for it again.
    """
    with _GUARD:
        lock = _LOCKS.get(wallet)
        if lock is None:
            lock = threading.Lock()
            _LOCKS[wallet] = lock
        return lock
