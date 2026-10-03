"""
Tests for ``bal.core.animated_qr`` (BBQR interop).

Validates the self-contained BBQR codec against round trips, out-of-order
assembly, compression default/fallback, malformed-input rejection and the
format detection / parse_for_detection helpers.

Run:
    source electrum/env/bin/activate
    python3 tests/test_core_animated_qr.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

import random

from bal.core import animated_qr as aq


# --------------------------------------------------------------------------- #
# BBQR
# --------------------------------------------------------------------------- #


def test_bbqr_all_encodings_roundtrip():
    payload = ("BBQR payload " * 8).encode()
    for encoding in ("Z", "2", "H"):
        frames = aq.bbqr_frames(payload, 90, encoding=encoding)
        assert len(frames) >= 1
        order = list(range(len(frames)))
        random.Random(3).shuffle(order)
        session = aq.AnimatedQrSession()
        for i in order:
            session.add_part(frames[i])
        assert session.done
        assert session.resolve()[0] == payload.decode()


def test_bbqr_compression_default_and_fallback():
    payload = ("repetitive data " * 40).encode()  # compresses well
    frames_z = aq.bbqr_frames(payload, 90, encoding="Z")
    # Highly compressible: Z yields one frame and a 'Z' flag.
    assert all(f[2] == "Z" for f in frames_z)
    assert len(frames_z) == 1
    raw = os.urandom(600)  # incompressible
    frames_2 = aq.bbqr_frames(raw, 90, encoding="Z")
    assert all(f[2] == "2" for f in frames_2)  # Z loses, '2' is used


def test_bbqr_hex_uppercase():
    payload = b"\xde\xad\xbe\xef"
    frame = aq.bbqr_frames(payload, 50, encoding="H")[0]
    assert "DEADBEEF" in frame
    encoding, _type, total, index, frag = aq.bbqr_parse_part(frame)
    assert (encoding, total, index) == ("H", 1, 0)


def test_bbqr_runt_last_part():
    payload = os.urandom(33)
    frames = aq.bbqr_frames(payload, 60, encoding="2")
    parts = [aq.bbqr_parse_part(f)[4] for f in frames]
    joined = aq._bbqr_decode(parts, "2")
    assert joined == payload
    assert len(parts[-1]) < len(parts[0])  # last part is a runt


def test_bbqr_zlib_bomb_rejected():
    compressed = aq._bbqr_encode(b"\x00" * 1000000, "Z")[1]
    try:
        aq._bbqr_decode(["0" * len(compressed)], "2")  # not zlib data
    except aq.AnimatedQrError:
        pass
    # direct inflate bomb guard:
    inflated = aq._bbqr_encode(b"\x00" * 1000000, "Z")
    assert inflated[0] == "Z"  # 1MB zeros compresses
    bomb = aq._bbqr_encode(b"\x00" * (aq._MAX_MESSAGE_BYTES + 100), "Z")[1]
    parts = [bomb[i : i + 90] for i in range(0, len(bomb), 90)]
    try:
        aq._bbqr_decode(parts, "Z")
    except aq.AnimatedQrError:
        pass
    else:
        raise AssertionError("expected AnimatedQrError for an oversized decompression")


def test_bbqr_part_number_limits():
    try:
        aq.bbqr_frames(os.urandom(30000), 40, encoding="2")
    except aq.AnimatedQrError:
        pass
    else:
        raise AssertionError("expected AnimatedQrError for too many BBQR parts")


# --------------------------------------------------------------------------- #
# Detection / parse_for_detection
# --------------------------------------------------------------------------- #


def test_detect_format_recognises_bbqr():
    assert aq.detect_format(aq.bbqr_frames(b"x", 50)[0]) == "bbqr"


def test_detect_format_rejects_garbage():
    for text in ("", "hello world", "BALQR1|1|1||payload", "ur:", "ur:bytes/xyz"):
        assert aq.detect_format(text) is None, text


def test_parse_for_detection_keys():
    bb = aq.parse_for_detection(aq.bbqr_frames(b"x" * 50, 40)[0])
    assert bb[0] == "bbqr" and bb[2] >= 1 and 0 <= bb[3] < bb[2]


def test_format_names_exist():
    assert aq.format_name("bbqr")
    assert aq.format_name("nope") == "nope"


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
