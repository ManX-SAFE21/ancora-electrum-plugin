"""The engine's state bricks: load, save and check the will (CHANGELOG #117).

The first pieces of the shared engine (Phase 5e, step 5e-05). They were
copied without rewriting from the command line's ``BalController``
(``bal/cli/controller.py``), which now calls them. The GUI's ``BalWindow``
has its own copies of the same steps and switches to these in the next step.

Every function works on a host (see :class:`bal.engine.host.WalletHost`):
it reads and changes the host's state and calls the host's hooks where the
GUI and the command line do different things (store a setting, write the
wallet file, redraw the History).

No Qt, no ``bal.cli`` (``bal/engine`` rules, see ``bal/engine/__init__``).
"""

import json
import time

from electrum.logging import get_logger
from electrum.util import MyEncoder

from ..core import checkalive
from ..core.util import Util
from ..core.will import NoHeirsException, Will, WillItem
from ..core.willexecutors import Willexecutors
from ..i18n import _

_logger = get_logger(__name__)


def load_willitems(host):
    """Build ``host.willitems`` (``{txid: WillItem}``) from ``host.will``."""
    host.willitems = {}
    for wid, w in host.will.items():
        host.willitems[wid] = WillItem(w, wallet=host.wallet)


def save_willitems(host):
    """Persist the in-memory willitems into the wallet's ``will`` dict.

    The transaction is stored serialized (exactly like the GUI, which
    avoids deep-copying a live Transaction holding a ``threading.RLock``)
    and every value is proven JSON-serializable before it is written.
    Then ``host.flush_wallet_db()`` (the command line writes the file).
    """
    keys = list(host.will.keys())
    for k in keys:
        del host.will[k]
    for wid, w in host.willitems.items():
        d = w.to_dict()
        d["tx"] = str(d["tx"])
        try:
            json.dumps(d, cls=MyEncoder)
        except Exception as e:
            _logger.error(f"save_willitems: will {wid} is not serializable: {e!r}")
            raise
        host.will[wid] = d
    host.flush_wallet_db()


def save_to_history(host):
    """Persist the will state into the wallet's LOCAL history (best-effort).

    Only when the ``SAVE_HISTORY`` setting is enabled, and never raises.
    Then ``host.after_history_saved()`` (the GUI redraws the History tab;
    like ``BalWindow._save_will_to_history``, also after a failed save, but
    not when the setting is off).
    """
    try:
        if not bool(host.bal_plugin.SAVE_HISTORY.get()):
            return
        Will.save_valid_transactions_to_history(
            host.willitems, host.wallet, host.bal_plugin.HISTORY_LABEL.get()
        )
    except Exception as e:
        _logger.error(f"save_to_history failed: {e}")
    host.after_history_saved()


def available_utxos(host):
    """The wallet coins the will can use (the will's own local coins aside)."""
    return Util.get_available_utxos(
        host.wallet,
        host.bal_plugin.HISTORY_LABEL.get(),
        Will.get_min_locktime(host.willitems, default_value=host.date_to_check),
    )


def refresh_invalidated_items(host):
    """Mark the will items already invalidated on-chain (CHANGELOG #79).

    Runs before ``date_to_check`` and the Check Alive are computed, which
    read the VALID items: see ``Will.refresh_invalidated``. Never raises.
    """
    try:
        utxos = Util.get_available_utxos(
            host.wallet,
            host.bal_plugin.HISTORY_LABEL.get(),
            Will.get_min_locktime(host.willitems, default_value=time.time()),
        )
        Will.refresh_invalidated(host.willitems, utxos, host.wallet)
    except Exception as e:
        _logger.debug(f"refresh invalidated items failed: {e!r}")


def check_alive_info(host):
    """``check_alive_renewal`` for this wallet (None: nothing to do).

    Read-only and offline (no will-executor ping). See
    ``bal.core.checkalive.check_alive_renewal``. Never raises.
    """
    try:
        return checkalive.check_alive_renewal(
            host.bal_plugin.is_basic_mode(),
            host.will_settings,
            built_locktime=Will.get_min_locktime(host.willitems),
            creation_date=Will.get_creation_time(host.willitems),
        )
    except Exception as e:
        _logger.debug(f"check alive info failed: {e!r}")
        return None


def normalize_check_alive(host):
    """Convert an old Check Alive value to "-Nd" and store it (#80).

    A no-op once the value is "-Nd". The rules (same date kept) live in
    ``bal.core.checkalive.normalize_check_alive``; the new value goes
    through ``host.store_will_setting("threshold", new)``. Never raises.
    """
    try:
        old = host.will_settings.get("threshold")
        new = checkalive.normalize_check_alive(
            host.will_settings,
            built_locktime=Will.get_min_locktime(host.willitems),
            creation_date=Will.get_creation_time(host.willitems),
        )
        if new != old:
            _logger.info(f"check alive converted: {old!r} -> {new}")
            host.store_will_setting("threshold", new)
    except Exception as e:
        _logger.debug(f"check alive conversion failed: {e!r}")


def init_class_variables(host):
    """Prepare the state every check reads.

    Refreshes the invalidated items and the Check Alive value, then sets
    ``date_to_check``, ``no_willexecutor`` and ``willexecutors``, checks the
    Check Alive against the delivery date and gives every heir the
    delivery date (unless each heir has its own). Raises
    ``NoHeirsException`` without heirs and the Check Alive errors of
    ``bal.core.checkalive``.
    """
    if not host.heirs:
        raise NoHeirsException(_("Heirs are not defined"))
    # Items already invalidated on-chain must not anchor the dates (#79).
    refresh_invalidated_items(host)
    # An old Check Alive value becomes "N days before the delivery" (#80).
    normalize_check_alive(host)
    host.date_to_check = checkalive.resolve_date_to_check(
        host.bal_plugin.is_basic_mode(),
        host.will_settings,
        built_locktime=Will.get_min_locktime(host.willitems),
        creation_date=Will.get_creation_time(host.willitems),
    )
    host.no_willexecutor = bool(host.bal_plugin.NO_WILLEXECUTOR.get())
    host.willexecutors = Willexecutors.get_willexecutors(
        host.bal_plugin, update=True, task=False
    )
    delivery_date = checkalive.resolve_delivery_date(
        host.will_settings,
        built_locktime=Will.get_min_locktime(host.willitems),
    )
    checkalive.ensure_check_alive_valid(
        host.bal_plugin.is_basic_mode(),
        host.date_to_check,
        delivery_date=delivery_date,
    )
    init_heirs_to_locktime(host, host.bal_plugin.ENABLE_MULTIVERSE.get())


def init_heirs_to_locktime(host, multiverse=False):
    """Give every heir the delivery date of the settings, then
    ``host.flush_wallet_db()``. Nothing with ``multiverse`` (each heir keeps
    its own date)."""
    if multiverse:
        return
    locktime = host.will_settings["locktime"]
    if not isinstance(locktime, (int, float, str)):
        locktime = str(locktime)
    updates = {
        heir: [host.heirs[heir][0], host.heirs[heir][1], locktime]
        for heir in list(host.heirs)
    }
    for heir, value in updates.items():
        host.heirs[heir] = value
    host.flush_wallet_db()


def check_will(host):
    """``Will.is_will_valid`` on the host's will, settings, heirs and
    will-executors; raises the "will must be prepared again" exceptions."""
    return Will.is_will_valid(
        host.willitems,
        host.date_to_check,
        host.will_settings["baltx_fees"],
        available_utxos(host),
        heirs=host.heirs,
        willexecutors=host.willexecutors,
        self_willexecutor=host.no_willexecutor,
        wallet=host.wallet,
    )
