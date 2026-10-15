"""The wallet file is not written from the Check's worker thread (CHANGELOG #143).

Found in the owner's logs (2026-10-14 and 2026-10-15): at every rebuild done
by the Check, the plugin log showed

    ERROR | save_db failed after history purge: daemon thread cannot write db

The Check runs on Electrum's ``TaskThread`` (a QThread), which Python reports
as a daemon thread, and Electrum's ``JsonDB.write`` refuses to write the
wallet file from a daemon thread. The change itself was never lost: it stays
in the wallet's memory and Electrum writes it at its next save.

Now ``Will._save_wallet_db`` leaves the write to Electrum on a daemon thread
(an INFO line, no ERROR) and saves as before on the main thread.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_history_save_thread.py
"""

import logging
import os
import sys
import threading
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from PyQt6.QtCore import QThread
from PyQt6.QtWidgets import QApplication

from bal.core.will import Will

# Kept at module level: an unreferenced application object is destroyed at
# once, and creating a QThread afterwards may abort Python.
_APP = QApplication.instance() or QApplication(sys.argv)

_HISTORY_TEMPLATE = "BAL Transaction {willexecutor}"


class _Wallet:
    """A wallet whose ``save_db`` refuses daemon threads, as Electrum's does."""

    def __init__(self):
        """No labels, an ``adb`` stub, no save yet."""
        self.adb = MagicMock()
        self.saves = 0

    def get_all_labels(self):
        """No will transaction in the local history."""
        return {}

    def save_db(self):
        """Count the save; raise like ``JsonDB.write`` on a daemon thread."""
        if threading.current_thread().daemon:
            raise Exception("daemon thread cannot write db")
        self.saves += 1


class _Capture(logging.Handler):
    """Collect the log records of ``bal.core.will``."""

    def __init__(self):
        """Start with no record."""
        super().__init__(logging.DEBUG)
        self.records = []

    def emit(self, record):
        """Keep the record."""
        self.records.append(record)


def _run_logged(fn):
    """Run ``fn`` and return its result with the ERROR and INFO messages."""
    handler = _Capture()
    root = logging.getLogger()
    old_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        result = fn()
    finally:
        root.removeHandler(handler)
        root.setLevel(old_level)
    errors = [r.getMessage() for r in handler.records if r.levelno >= logging.ERROR]
    infos = [r.getMessage() for r in handler.records if r.levelno == logging.INFO]
    return result, errors, infos


def _in_thread(fn, daemon):
    """Run ``fn`` on a ``threading.Thread`` and return what it returned."""
    out = {}
    t = threading.Thread(target=lambda: out.update(r=fn()), daemon=daemon)
    t.start()
    t.join()
    return out["r"]


def test_purge_on_daemon_thread_logs_no_error():
    """The Check's case: the purge on a daemon thread does not try to write
    the wallet file and logs no ERROR, only an INFO line."""
    wallet = _Wallet()
    _, errors, infos = _in_thread(
        lambda: _run_logged(
            lambda: Will.remove_stale_wallet_history(wallet, _HISTORY_TEMPLATE)
        ),
        daemon=True,
    )
    assert wallet.saves == 0
    assert errors == []
    assert any("not written after history purge" in m for m in infos), infos


def test_purge_on_main_thread_saves():
    """On the main thread the wallet file is written as before."""
    wallet = _Wallet()
    _, errors, _infos = _run_logged(
        lambda: Will.remove_stale_wallet_history(wallet, _HISTORY_TEMPLATE)
    )
    assert wallet.saves == 1
    assert errors == []


def test_purge_on_plain_worker_thread_saves():
    """A non-daemon worker thread may write: the save happens."""
    wallet = _Wallet()
    _in_thread(
        lambda: Will.remove_stale_wallet_history(wallet, _HISTORY_TEMPLATE),
        daemon=False,
    )
    assert wallet.saves == 1


def test_history_update_on_daemon_thread_logs_no_error():
    """The other save point (after the history update) follows the same rule."""
    wallet = _Wallet()
    _, errors, infos = _in_thread(
        lambda: _run_logged(
            lambda: Will.save_valid_transactions_to_history({}, wallet, _HISTORY_TEMPLATE)
        ),
        daemon=True,
    )
    assert wallet.saves == 0
    assert errors == []
    assert any("not written after history update" in m for m in infos), infos


def test_save_error_on_main_thread_is_still_logged():
    """A real failure of the write is still an ERROR."""
    wallet = MagicMock()
    wallet.save_db.side_effect = OSError("disk full")
    _, errors, _infos = _run_logged(lambda: Will._save_wallet_db(wallet, "history purge"))
    assert errors == ["save_db failed after history purge: disk full"]


def test_qthread_is_seen_as_daemon():
    """Electrum's ``TaskThread`` is a QThread: Python reports it as a daemon
    thread, so the write is left to Electrum there too."""
    wallet = _Wallet()
    seen = {}

    class Worker(QThread):
        """Runs the purge like the Check's TaskThread."""

        def run(self):
            """The purge, with its log captured."""
            seen["daemon"] = threading.current_thread().daemon
            seen["log"] = _run_logged(
                lambda: Will.remove_stale_wallet_history(wallet, _HISTORY_TEMPLATE)
            )

    w = Worker()
    w.start()
    assert w.wait(10000)
    assert seen["daemon"] is True
    _, errors, _infos = seen["log"]
    assert errors == []
    assert wallet.saves == 0


if __name__ == "__main__":
    for name in sorted(dir()):
        if name.startswith("test_"):
            globals()[name]()
            print(f"  [OK] {name}")
    print("[OK] All history-save-thread tests passed")
