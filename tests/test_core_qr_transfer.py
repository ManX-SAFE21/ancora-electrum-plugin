"""
Tests for ``bal.core.qrtransfer``.

Covers the transfer-string encoding used for will transfer via BBQR QR codes
/ audio modem: encoding, decoding and the preset list.

Run:
    source electrum/env/bin/activate
    python3 tests/test_core_qr_transfer.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from bal.core.qrtransfer import (
    CHUNK_PRESETS,
    decode_transfer,
    encode_transfer,
    preset_index_for_chunk_size,
)


# --------------------------------------------------------------------------- #
# Round trips
# --------------------------------------------------------------------------- #


def test_encode_decode_plain():
    tx_strings = ["00" * 32, "aa" * 40, "ff" * 50]
    payload = encode_transfer(tx_strings, compress=False)
    assert decode_transfer(payload, compressed=False) == tx_strings


def test_encode_decode_compressed():
    tx_strings = ["00" * 32, "aa" * 40, "ff" * 50]
    payload = encode_transfer(tx_strings, compress=True)
    assert decode_transfer(payload, compressed=True) == tx_strings


def test_empty_list_roundtrip():
    assert decode_transfer(encode_transfer([]), compressed=False) == []


# --------------------------------------------------------------------------- #
# Constants / presets
# --------------------------------------------------------------------------- #


def test_preset_count_and_order():
    assert len(CHUNK_PRESETS) == 4
    budgets = [budget for _label, budget in CHUNK_PRESETS]
    assert budgets == sorted(budgets)


def test_preset_index_for_chunk_size():
    for index, (_label, budget) in enumerate(CHUNK_PRESETS):
        assert preset_index_for_chunk_size(budget) == index
    assert preset_index_for_chunk_size(150) == 0
    assert preset_index_for_chunk_size(1800) == 3


# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import traceback

    failures = 0
    for _name, fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(fn):
            try:
                fn()
                print("ok: {}".format(_name))
            except Exception:
                failures += 1
                print("FAIL: {}".format(_name))
                traceback.print_exc()
    if failures:
        print("{} test(s) failed".format(failures))
        sys.exit(1)
    print("all tests passed")
