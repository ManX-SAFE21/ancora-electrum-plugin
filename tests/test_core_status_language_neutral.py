"""
Stored will data must not depend on the GUI language (rule in bal/i18n.py).

Covers the two findings of the i18n review:

* the status history of a new will item is saved as English "New" (not
  ``_("New")``), and histories saved by test builds as "Nuovo"/"Nuevo" are
  mapped back to "New" on load;
* the push helper no longer writes transient fields (``txs``, ``txsids`` and
  the translated ``broadcast_status``) into the will item's executor record,
  and such fields saved by older versions are dropped on load.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \
        python3 tests/test_core_status_language_neutral.py
"""

import os
import re
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from electrum.transaction import Transaction

from bal.core.will import WillItem
from bal.core.willexecutors import Willexecutors

# WillItem parses a raw tx and calls add_info_from_wallet; no live wallet here.
_patcher = patch.object(Transaction, "add_info_from_wallet")
_patcher.start()

ROOT = Path(__file__).resolve().parent.parent

_VALID_TX_HEX = (
    "01000000012a5c9a94fcde98f5581cd00162c60a13936ceb75389ea65b"
    "f38633b424eb4031000000006c493046022100a82bbc57a0136751e543"
    "3f41cf000b3f1a99c6744775e76ec764fb78c54ee100022100f9e80b7d"
    "e89de861dc6fb0c1429d5da72c2b6b2ee2406bc9bfb1beedd729d98501"
    "2102e61d176da16edd1d258a200ad9759ef63adf8e14cd97f53227bae3"
    "5cdb84d2f6ffffffff0140420f00000000001976a914230ac37834073a"
    "42146f11ef8414ae929feaafc388ac00000000"
)
URL = "https://we.example"


def _item(status="", we=None):
    return WillItem(
        {"tx": _VALID_TX_HEX, "heirs": {}, "willexecutor": we, "status": status}
    )


# ------------------------------------------------------------------ #
# Status history "New"
# ------------------------------------------------------------------ #

def test_builders_store_english_new():
    """The will builder must not translate the stored status.

    Since CHANGELOG #119 the one builder is ``bal/engine/build.py`` (it was
    in ``window.py`` and the CLI controller); none of the three files may
    translate it.
    """
    src = (ROOT / "bal/engine/build.py").read_text(encoding="utf-8")
    assert re.search(r'tx\["status"\]\s*=\s*"New"', src)
    for rel in ("bal/engine/build.py", "bal/gui/qt/window.py", "bal/cli/controller.py"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert not re.search(r'tx\["status"\]\s*=\s*_\(', src), rel


def test_translated_new_is_mapped_back_on_load():
    assert _item("Nuovo.Signed.Pushed").status == "New.Signed.Pushed"
    assert _item("Nuevo").status == "New"
    assert _item("New.Signed").status == "New.Signed"


def test_only_the_leading_new_is_touched():
    assert _item(".Nuovo").status == ".Nuovo"
    assert _item("Signed.Nuovo").status == "Signed.Nuovo"


def test_translated_new_labels_match_the_catalogs():
    """Every catalog's translation of "New" must be recognised on load."""
    found = set()
    for po in (ROOT / "bal" / "locale").glob("*/LC_MESSAGES/bal.po"):
        text = po.read_text(encoding="utf-8")
        m = re.search(r'^msgid "New"\nmsgstr "(.*)"$', text, re.MULTILINE)
        if m and m.group(1):
            found.add(m.group(1))
    assert found, "no catalog translates 'New'"
    assert found <= set(WillItem.TRANSLATED_NEW_LABELS), found


# ------------------------------------------------------------------ #
# Transient push fields
# ------------------------------------------------------------------ #

class FakeItem:
    def __init__(self):
        self.we = {"url": URL, "selected": True, "address": "a", "base_fee": 1}
        self.tx = "rawtx"

    def get_status(self, name):
        return name in ("VALID", "COMPLETE")


def test_push_helper_does_not_modify_the_will_item():
    item = FakeItem()
    before = dict(item.we)
    out = Willexecutors.get_willexecutor_transactions({"t1": item})
    assert out[URL]["txsids"] == ["t1"]
    assert "broadcast_status" in out[URL]
    assert item.we == before
    assert out[URL] is not item.we


def test_transient_fields_saved_by_older_versions_are_dropped_on_load():
    we = {
        "url": URL,
        "selected": True,
        "txs": "raw\n",
        "txsids": ["t1"],
        "broadcast_status": "Riuscito",
    }
    item = _item("New", we)
    assert item.we == {"url": URL, "selected": True}
    assert "broadcast_status" not in item.to_dict()["willexecutor"]


def test_clean_executor_record_is_kept_as_is():
    we = {"url": URL, "selected": True}
    assert _item("New", we).we is we


if __name__ == "__main__":
    for name in sorted(dir()):
        if name.startswith("test_"):
            globals()[name]()
            print(f"  [OK] {name}")
    print("[OK] All language-neutral status tests passed")
