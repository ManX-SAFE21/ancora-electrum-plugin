"""
Tests for ``bal.core.qrtransfer``.

Covers the BALQR frame encoding used for will transfer via QR codes /
audio modem: encoding, framing, reassembly, malformed input and the preset
list (optionally cross-checked against the ``qrcode`` library's EC-M
capacity when it is installed).

Run:
    source electrum/env/bin/activate
    python3 tests/test_core_qr_transfer.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from bal.core.qrtransfer import (
    CHUNK_PRESETS,
    MIN_CHUNK_SIZE,
    InconsistentTotalError,
    MissingFramesError,
    QrTransferError,
    assemble,
    decode_transfer,
    encode_transfer,
    parse_frame,
    preset_index_for_chunk_size,
    split_frames,
)


def _frames(tx_strings, chunk_size, compress=False):
    """Split a payload and return (payload, total, {index: payload})."""
    payload = encode_transfer(tx_strings, compress=compress)
    parsed = {}
    total = None
    for frame in split_frames(payload, chunk_size, compressed=compress):
        t, index, _compressed, p = parse_frame(frame)
        if total is not None:
            assert total == t
        total = t
        parsed[index] = p
    assert total is not None
    return payload, total, parsed


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
# Framing
# --------------------------------------------------------------------------- #


def test_single_frame():
    tx_strings = ["11" * 10]
    payload = encode_transfer(tx_strings)
    frames = split_frames(payload, 150)
    assert len(frames) == 1
    total, index, compressed, p = parse_frame(frames[0])
    assert (total, index, compressed) == (1, 1, False)
    assert p == payload


def test_multiple_frames_reassemble():
    tx_strings = ["ab" * 100, "cd" * 100]  # 600 chars -> multiple frames
    _payload, total, parsed = _frames(tx_strings, CHUNK_PRESETS[0][1])
    assert total > 1
    decoded = decode_transfer(assemble(parsed, total), compressed=False)
    assert decoded == tx_strings


def test_size_greater_than_payload():
    tx_strings = ["12" * 5]
    payload = encode_transfer(tx_strings)
    frames = split_frames(payload, 1800)
    assert len(frames) == 1
    _t, _i, _c, p = parse_frame(frames[0])
    assert p == payload


def test_exact_single_frame_boundary():
    # A 139-byte payload exactly fills the 150-byte preset budget (the 11-char
    # compact header plus payload), so the encoded frame is exactly 150.
    tx_strings = ["a" * 139]
    payload = encode_transfer(tx_strings)
    frames = split_frames(payload, 150)
    assert len(frames) == 1
    assert len(frames[0]) == 150
    _t, _i, _c, p = parse_frame(frames[0])
    assert p == payload


def test_frames_fit_chunk_size():
    tx_strings = ["".join("{:02x}".format(i) * 2) for i in range(300)]
    payload = encode_transfer(tx_strings)
    for _label, size in CHUNK_PRESETS:
        for frame in split_frames(payload, size):
            assert len(frame) <= size, (size, len(frame))


def test_single_tx_larger_than_chunk():
    # A huge serialized tx must be split over several frames and reassemble
    # exactly (positional slicing is safe for hex/base64 text).
    tx_strings = ["7b" * 1000]  # 2000 chars
    payload = encode_transfer(tx_strings)
    frames = split_frames(payload, 150)
    assert len(frames) > 1
    parsed = {parse_frame(f)[1]: parse_frame(f)[3] for f in frames}
    total = parse_frame(frames[0])[0]
    assert assemble(parsed, total) == payload


def test_compressed_frames_carry_flag():
    tx_strings = ["ab" * 40]
    frames = split_frames(encode_transfer(tx_strings, compress=True), 150, compressed=True)
    for frame in frames:
        _t, _i, compressed, _p = parse_frame(frame)
        assert compressed is True
    # Plain frames do not.
    frames_plain = split_frames(encode_transfer(tx_strings), 150)
    _t, _i, compressed, _p = parse_frame(frames_plain[0])
    assert compressed is False


def test_compressed_roundtrip_through_frames():
    tx_strings = ["ab" * 50, "cd" * 50, "12" * 60]
    payload = encode_transfer(tx_strings, compress=True)
    parsed = {}
    total = None
    for frame in split_frames(payload, 400, compressed=True):
        t, index, _c, p = parse_frame(frame)
        total = t
        parsed[index] = p
    assert total is not None
    decoded = decode_transfer(assemble(parsed, total), compressed=True)
    assert decoded == tx_strings


# --------------------------------------------------------------------------- #
# Compact v2 wire format ("BAL1")
# --------------------------------------------------------------------------- #


def test_v2_frame_header_structure():
    frames = split_frames(encode_transfer(["11" * 10]), 150)
    assert len(frames) == 1
    frame = frames[0]
    assert frame.startswith("BAL1")
    # Fixed 11-char header: magic + 3-char total + 3-char index + 1 flag.
    assert len(frame) > 11
    magic, total_s, index_s, flag, payload = (
        frame[:4],
        frame[4:7],
        frame[7:10],
        frame[10],
        frame[11:],
    )
    assert magic == "BAL1"
    assert total_s == "001"
    assert index_s == "001"
    assert flag == "0"
    assert payload == "11" * 10
    total, index, compressed, p = parse_frame(frame)
    assert (total, index, compressed) == (1, 1, False)
    assert p == payload


def test_v2_compressed_flag_is_z():
    frames = split_frames(
        encode_transfer(["11" * 10], compress=True), 150, compressed=True
    )
    assert frames[0][10] == "Z"
    _t, _i, compressed, _p = parse_frame(frames[0])
    assert compressed is True


def test_v2_header_fixed_width_high_counts():
    # A long transfer needs multi-digit counts; the v2 header stays exactly
    # 11 chars no matter how many frames (3-char base36 zero-padded counts).
    tx_strings = ["ab" * 300]  # 600 chars -> several frames at 150
    frames = split_frames(encode_transfer(tx_strings), 150)
    assert len(frames) > 1
    for frame in frames:
        # magic(4) + total(3) + index(3) + flag(1) = 11 chars, then payload.
        assert len(frame) - len(frame[11:]) == 11


def test_v2_max_frame_count():
    # A transfer needing more than 46655 frames must be rejected (3-char
    # base36 count fields cannot represent larger totals).
    from bal.core.qrtransfer import _MAX_TOTAL

    oversized = "A" * (_MAX_TOTAL * (150 - 11) + 1)
    try:
        split_frames(oversized, 150)
    except QrTransferError:
        pass
    else:
        raise AssertionError("expected QrTransferError above the frame cap")


def test_v2_boundary_at_max_count():
    from bal.core.qrtransfer import _MAX_TOTAL

    # Exactly at the cap: must still produce (bounded) frames with 3-char
    # counts "VVV" (46655) for the highest serialised part.
    payload = "B" * (_MAX_TOTAL * (150 - 11))
    frames = split_frames(payload, 150)
    assert len(frames) == _MAX_TOTAL
    total, index, _c, _p = parse_frame(frames[-1])
    assert total == _MAX_TOTAL
    assert index == _MAX_TOTAL
    assert frames[-1][:10] == "BAL1" + "ZZZ" + "ZZZ"


def test_encode_transfer_best():
    from bal.core.qrtransfer import encode_transfer_best

    # Redundant JSON-ish text compresses -> compressed (and longer source
    # must round-trip unchanged).
    txs = ['{"a": "%s"}' % ("x" * 300), '{"b": "%s"}' % ("y" * 300)]
    transfer, compressed = encode_transfer_best(txs)
    assert compressed is True
    assert decode_transfer(transfer, compressed) == txs

    # Already-compact input stays plain (never larger than the source).
    txs_small = ["ab", "cd"]
    transfer, compressed = encode_transfer_best(txs_small)
    assert compressed is False
    assert decode_transfer(transfer, compressed) == txs_small


def test_v2_malformed_frames():
    bad = (
        "BAL1",                       # header only, no fields
        "BAL1" + "001",               # truncated
        "BAL1" + "G-1" + "001" + "Z" + "p",  # non-base36 total
        "BAL1" + "001" + "G-1" + "Z" + "p",  # non-base36 index
        "BAL1" + "000" + "001" + "Z" + "p",  # total 0
        "BAL1" + "001" + "000" + "Z" + "p",  # index 0
        "BAL1" + "001" + "002" + "Z" + "p",  # index beyond total
        "BAL1" + "001" + "001" + "Q" + "p",  # unknown flag
    )
    for frame in bad:
        try:
            parse_frame(frame)
        except QrTransferError:
            continue
        raise AssertionError("expected QrTransferError for: {!r}".format(frame))


# --------------------------------------------------------------------------- #
# Malformed input
# --------------------------------------------------------------------------- #


def test_parse_bad_magic_and_version():
    for frame in (
        "BALQR|1|1||a",  # missing version
        "BALQR2|1|1||a",  # unknown version
        "XXXXX1|1|1||a",  # unknown magic
    ):
        try:
            parse_frame(frame)
        except QrTransferError:
            pass
        else:
            raise AssertionError("expected QrTransferError for: {}".format(frame))


def test_parse_bad_arity():
    for frame in ("BALQR1", "BALQR1|1|1|"):
        try:
            parse_frame(frame)
        except QrTransferError:
            pass
        else:
            raise AssertionError("expected QrTransferError for: {}".format(frame))


def test_parse_pipe_in_payload_is_folded():
    # maxsplit keeps the tail (including any inner '|') in the payload part.
    frame = "BALQR1|1|1||a|b|c"
    total, index, compressed, payload = parse_frame(frame)
    assert (total, index, compressed) == (1, 1, False)
    assert payload == "a|b|c"


def test_parse_bad_numbers():
    for frame in (
        "BALQR1|x|1||a",
        "BALQR1|1|y||a",
        "BALQR1|0|1||a",
        "BALQR1|1|0||a",
        "BALQR1|1|2||a",  # index beyond total
        "BALQR1|-1|1||a",
    ):
        try:
            parse_frame(frame)
        except QrTransferError:
            pass
        else:
            raise AssertionError("expected QrTransferError for: {}".format(frame))


def test_parse_bad_flags():
    try:
        parse_frame("BALQR1|1|1|Q|payload")
    except QrTransferError:
        pass
    else:
        raise AssertionError("expected QrTransferError for unknown flags")


def test_assemble_missing_frames():
    try:
        assemble({1: "a", 3: "c"}, total=3)
    except MissingFramesError as e:
        assert e.missing == [2]
    else:
        raise AssertionError("expected MissingFramesError")


def test_assemble_index_beyond_total():
    try:
        assemble({1: "a", 2: "b"}, total=1)
    except InconsistentTotalError:
        pass
    else:
        raise AssertionError("expected InconsistentTotalError")


def test_assemble_order_and_total_validation():
    assert assemble({1: "a", 2: "b"}, total=2) == "ab"
    try:
        assemble({}, total=0)
    except QrTransferError:
        pass
    else:
        raise AssertionError("expected QrTransferError")


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


def test_min_chunk_size_guard():
    try:
        split_frames("x" * 10, MIN_CHUNK_SIZE - 1)
    except QrTransferError:
        pass
    else:
        raise AssertionError("expected QrTransferError for tiny chunk size")


def test_split_frame_headers_consistent():
    tx_strings = ["ab" * 80]
    payload = encode_transfer(tx_strings)
    frames = split_frames(payload, 150)
    totals = {parse_frame(frame)[0] for frame in frames}
    assert len(totals) == 1
    assert totals.pop() == len(frames)


# --------------------------------------------------------------------------- #
# Optional: cross-check presets against the qrcode library (EC level M)
# --------------------------------------------------------------------------- #


def test_presets_fit_qrcode_ec_m():
    """Every preset budget must render inside a QR at EC level M."""
    try:
        import qrcode
        from qrcode.constants import ERROR_CORRECT_M
    except ImportError:
        print("qrcode not installed - skipping capacity check")
        return
    for _label, size in CHUNK_PRESETS:
        # Worst-case frame: header with the largest plausible total/index plus
        # a full payload of the preset budget.
        frame = "BALQR1|9999|9999|Z|" + "a" * (size - 14)
        qr = qrcode.QRCode(error_correction=ERROR_CORRECT_M, border=2)
        qr.add_data(frame)
        qr.get_matrix()  # raises DataOverflowError if it does not fit


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
