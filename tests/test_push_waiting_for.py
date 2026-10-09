"""
The "Broadcasting" line names the will-executors still awaited (CHANGELOG #85).

Owner's remark: "Broadcasting 0/1 (3s / 20s)" stayed on screen for up to 20
seconds without saying who the dialog was waiting for, so it looked broken.
The line now ends with "— waiting for: <host>" until that server answers or
the global deadline expires.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \
        python3 tests/test_push_waiting_for.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from bal.gui.qt.dialogs import BalBuildWillDialog

text = BalBuildWillDialog.push_status_text


def test_nothing_pending_keeps_the_old_line():
    assert text(2, 2, 1, 20) == "Broadcasting 2/2 (1s / 20s)"
    assert text(2, 2, 1, 20, set()) == "Broadcasting 2/2 (1s / 20s)"


def test_names_the_host_still_awaited():
    assert text(3, 4, 7, 20, {"https://we.divinaly.support"}) == (
        "Broadcasting 3/4 (7s / 20s) — waiting for: we.divinaly.support")


def test_several_hosts_sorted_and_capped_at_three():
    pending = {
        "https://we.d.org", "https://we.a.org", "http://we.c.org:8080/x",
        "https://we.b.org", "https://we.e.org",
    }
    assert text(0, 5, 2, 20, pending) == (
        "Broadcasting 0/5 (2s / 20s) — waiting for: "
        "we.a.org, we.b.org, we.c.org (+2 more)")


def test_url_without_host_is_shown_as_is():
    assert text(0, 1, 0, 20, {"we-local"}).endswith("waiting for: we-local")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print("{} tests passed".format(len(tests)))
