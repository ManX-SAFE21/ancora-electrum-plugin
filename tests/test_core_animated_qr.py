"""
Tests for ``bal.core.animated_qr`` (BC-UR v1, BC-UR v2, BBQR interop).

Validates the self-contained codecs against the published spec vectors
(BCR-2020-004/005 BC32, BCR-2020-012 bytewords) and against byte-exact
output captured from the reference C++ bc-ur encoder (fountain/xoshiro/
alias-sampler parity), plus round trips, out-of-order assembly, missing-part
fountain solving and malformed-input rejection for all four formats.

Run:
    source electrum/env/bin/activate
    python3 tests/test_core_animated_qr.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

import random

from bal.core import animated_qr as aq


def _payload(plen: int) -> bytes:
    """Deterministic payload matching the C++ reference driver (``(i*7)&0xff``)."""
    return bytes((i * 7) & 0xFF for i in range(plen))


# --------------------------------------------------------------------------- #
# BC32 (BCR-2020-004 / bcr-2020-005 rev1 reference implementation vectors)
# --------------------------------------------------------------------------- #


def test_bc32_official_vectors():
    cases = [
        (b"Hello, world", "fpjkcmr09ss8wmmjd3jq6ax7w9"),
        (b"Hello world", "fpjkcmr0ypmk7unvvsh4ra4j"),
        (
            bytes.fromhex("d934063e82001eec0585ee41ab5d8e4b703a4be1f73aec21e143912c56"),
            "my6qv05zqq0wcpv9aeq6khvwfdcr5jlp7uawcg0pgwgjc4shjm6xu",
        ),
    ]
    for payload, encoded in cases:
        assert aq.bc32_encode(payload) == encoded
        assert aq.bc32_decode(encoded) == payload


def test_bc32_checksum_rejected():
    good = aq.bc32_encode(b"Hello, world")
    corrupted = good[:-1] + ("a" if good[-1] != "a" else "b")
    try:
        aq.bc32_decode(corrupted)
    except aq.AnimatedQrError:
        pass
    else:
        raise AssertionError("expected AnimatedQrError for corrupted BC32")


def test_bc32_bad_char_rejected():
    try:
        aq.bc32_decode("1" * 26)
    except aq.AnimatedQrError:
        pass
    else:
        raise AssertionError("expected AnimatedQrError for '1' (not in alphabet)")


# --------------------------------------------------------------------------- #
# Bytewords (BCR-2020-012)
# --------------------------------------------------------------------------- #


def test_bytewords_minimal_roundtrip():
    samples = [bytes(range(256)), _payload(59), b"\x00"] + [
        os.urandom(64) for _ in range(4)
    ]
    for data in samples:
        words = aq.bytewords_minimal_encode(data)
        assert len(words) == (len(data) + 4) * 2  # 2 chars per byte incl. CRC
        assert aq.bytewords_minimal_decode(words) == data


def test_bytewords_rejects_corrupted_crc():
    data = _payload(40)
    words = aq.bytewords_minimal_encode(data)
    flip = "a" if words[-1] != "a" else "b"
    try:
        aq.bytewords_minimal_decode(words[:-1] + flip)
    except aq.AnimatedQrError:
        pass
    else:
        raise AssertionError("expected AnimatedQrError for corrupted CRC")


def test_bytewords_rejects_odd_length():
    try:
        aq.bytewords_minimal_decode("abc")
    except aq.AnimatedQrError:
        pass
    else:
        raise AssertionError("expected AnimatedQrError for odd-length bytewords")


# --------------------------------------------------------------------------- #
# BC-UR v2: byte-exact parity with the reference C++ encoder
# --------------------------------------------------------------------------- #

# Reference frames from the bc-ur C++ fountain encoder
# (payload x=(i*7)&0xFF, cbor wrapped, single-part and multipart).
REF_V2_SINGLE_12 = "ur:bytes/gsaeatbabzcecndrehetfhfggtoeemhpmo"

REF_V2_MULTI_59 = [
    "ur:bytes/2-2/lpaoaocsfscyrpdpjzbyhdctsbtdtavtvdwyykztaxbkbycsctdsdpeefrfwgagdhghyihjzjkknlylomymtaeeccasket",
    "ur:bytes/3-2/lpaxaocsfscyrpdpjzbyhdcthdfraeatbabzcecndrehetfhfggtghhpidinjoktkblplkmunyoypdperpryssimryrldt",
    "ur:bytes/4-2/lpaaaocsfscyrpdpjzbyhdctsbtdtavtvdwyykztaxbkbycsctdsdpeefrfwgagdhghyihjzjkknlylomymtaefeimteue",
    "ur:bytes/5-2/lpahaocsfscyrpdpjzbyhdctmuwltavdwlzowlurdtfrdtdihkjekkjlhkdnesdidtuywlzmwluydtdiesdnssgdaontls",
    "ur:bytes/6-2/lpamaocsfscyrpdpjzbyhdctmuwltavdwlzowlurdtfrdtdihkjekkjlhkdnesdidtuywlzmwluydtdiesdnssisescmwt",
    "ur:bytes/7-2/lpataocsfscyrpdpjzbyhdcthdfraeatbabzcecndrehetfhfggtghhpidinjoktkblplkmunyoypdperprysslsspplgm",
    "ur:bytes/8-2/lpayaocsfscyrpdpjzbyhdctsbtdtavtvdwyykztaxbkbycsctdsdpeefrfwgagdhghyihjzjkknlylomymtaeonlrzebg",
    "ur:bytes/9-2/lpasaocsfscyrpdpjzbyhdctsbtdtavtvdwyykztaxbkbycsctdsdpeefrfwgagdhghyihjzjkknlylomymtaeaaryknzt",
    "ur:bytes/10-2/lpbkaocsfscyrpdpjzbyhdctmuwltavdwlzowlurdtfrdtdihkjekkjlhkdnesdidtuywlzmwluydtdiesdnsslotsfrfn",
    "ur:bytes/11-2/lpbdaocsfscyrpdpjzbyhdctmuwltavdwlzowlurdtfrdtdihkjekkjlhkdnesdidtuywlzmwluydtdiesdnssdtwyrstd",
    "ur:bytes/12-2/lpbnaocsfscyrpdpjzbyhdctsbtdtavtvdwyykztaxbkbycsctdsdpeefrfwgagdhghyihjzjkknlylomymtaegswnvdin",
    "ur:bytes/13-2/lpbtaocsfscyrpdpjzbyhdctmuwltavdwlzowlurdtfrdtdihkjekkjlhkdnesdidtuywlzmwluydtdiesdnsshknlptee",
]

# Reference message for the 59-byte payload: byte-string head (0x58,0x3b) + data.
REF_V2_MULTI_59_MSG = bytes([0x58, 0x3B]) + _payload(59)


def test_v2_single_part_matches_reference():
    frames = aq.ur2_frames(_payload(12), len(REF_V2_SINGLE_12))
    assert frames == [REF_V2_SINGLE_12]


def test_v2_reference_frames_decode_and_reencode_exactly():
    message = REF_V2_MULTI_59_MSG
    fragment_len = -(-len(message) // 2)
    for frame in REF_V2_MULTI_59:
        seq, seq_len, message_len, checksum, data = aq.ur2_parse_part(frame)
        assert seq_len == 2
        assert message_len == len(message)
        assert checksum == aq.crc32_int(message)
        assert len(data) == fragment_len
        # re-encoding the parsed values reproduces the reference line exactly
        assert aq._ur2_part_string(seq, seq_len, message_len, checksum, data) == frame
        # our choose_fragments + partition + xor reproduces the reference data
        indexes = aq.choose_fragments(seq, seq_len, checksum)
        assert seq_num_indexes_valid(seq, seq_len, indexes)
        mixed = aq._mix_fragments(aq._partition_message(message, fragment_len), indexes, fragment_len)
        assert mixed == data


def seq_num_indexes_valid(seq, seq_len, indexes):
    # pure part for seq <= seq_len contains exactly fragment seq-1
    if seq <= seq_len:
        return indexes == {seq - 1}
    return set(indexes) <= set(range(seq_len)) and bool(indexes)


def test_v2_multipart_encoder_matches_reference_from_seq2():
    # Our frames start at seq 1 (spec-aligned); parts seq 2.. must equal the
    # reference (which starts at seq 2 due to first_seq_num=1).
    mine = aq.ur2_frames(_payload(59), 120)
    assert mine[0].split("/", 1)[1].startswith("1-2") or "1-2" in mine[0].split("/")[1]
    assert mine[1:4] == REF_V2_MULTI_59[:3]


def test_v2_reference_seq7_mix_parity():
    # Higher-degree mixed parts (seq_len=7) also match: message uses the
    # reference head 0x58|0x00 for the 256-byte driver payload.
    message = bytes([0x58, 0x00]) + _payload(256)
    seq_len = 7
    fragment_len = -(-len(message) // seq_len)
    frames = [
        "ur:bytes/9-7/lpasatcfadaocyfysnjlsrhddaykztaxbkbycsctdsdpeefrfwgagdhghyihjzjkknlylomymtntoxpyprrhrtsttotluovlwdwnsrfejzhd",
        "ur:bytes/10-7/lpbkatcfadaocyfysnjlsrhddazeahbnbwcycldedlenfsfygrgmhkhniojtkpkelslememkneolpmqzrksasotitsuevwwpwfzswzpmdrvo",
        "ur:bytes/11-7/lpbdatcfadaocyfysnjlsrhddawkwtbbbefnaefnbebbjojybebnaebndybbbewkwtceaecedyeebebbjobnaebnbeeedybbbeztwproyapd",
    ]
    for frame in frames:
        seq, sl, mlen, checksum, data = aq.ur2_parse_part(frame)
        assert sl == seq_len and mlen == len(message)
        assert checksum == aq.crc32_int(message)
        mixed = aq._mix_fragments(
            aq._partition_message(message, fragment_len),
            aq.choose_fragments(seq, seq_len, checksum),
            fragment_len,
        )
        assert mixed == data


# --------------------------------------------------------------------------- #
# BC-UR v2: sessions / fountain decoding
# --------------------------------------------------------------------------- #


def test_v2_roundtrip_in_order():
    payload = ("BAL transfer " * 9).encode()
    frames = aq.ur2_frames(payload, 120)
    seq_len = int(frames[0].split("/")[1].split("-")[1])
    assert len(frames) == 2 * seq_len  # pure wave + redundant mixed wave
    session = aq.AnimatedQrSession()
    for frame in frames:
        session.add_part(frame)
    assert session.done
    assert session.received == session.total
    text, _ = session.resolve()
    assert text == payload.decode()


def test_v2_out_of_order_and_duplicate():
    payload = ("BAL transfer " * 9).encode()
    frames = aq.ur2_frames(payload, 120)
    order = list(range(len(frames)))
    random.Random(11).shuffle(order)
    session = aq.AnimatedQrSession()
    for i in order:
        status = session.add_part(frames[i])
        assert status in ("ok", "dup")
    session.add_part(frames[0])  # duplicate of an already-received part
    assert session.done
    assert session.resolve()[0] == payload.decode()


def test_v2_solves_without_a_pure_fragment():
    payload = ("BAL transfer " * 9).encode()
    frames = aq.ur2_frames(payload, 120)
    session = aq.AnimatedQrSession()
    for frame in frames[1:]:  # drop the first pure fragment
        session.add_part(frame)
    assert session.done
    assert session.resolve()[0] == payload.decode()


def test_v2_single_part_import():
    session = aq.AnimatedQrSession()
    session.add_part(REF_V2_SINGLE_12)
    assert session.done and session.total == 1
    assert session.resolve()[0] == _payload(12).decode("latin-1")


def test_v2_conflicting_transfer_rejected():
    payload_a = b"AAAAAAAAAAAAAAAA"
    payload_b = b"BBBBBBBBBBBBBBBB"
    fa = aq.ur2_frames(payload_a, 500)[0]
    fb = aq.ur2_frames(payload_b, 500)[0]
    session = aq.AnimatedQrSession()
    session.add_part(fa)
    try:
        session.add_part(fb)
    except aq.TransferConflictError:
        pass
    else:
        raise AssertionError("expected TransferConflictError for a different transfer")


def test_v2_corrupt_crc_rejected():
    frame = list(REF_V2_MULTI_59[0])
    idx = len(frame) - 1
    frame[idx] = "a" if frame[idx] != "a" else "b"
    try:
        aq.ur2_parse_part("".join(frame))
    except aq.AnimatedQrError:
        pass
    else:
        raise AssertionError("expected AnimatedQrError for a corrupt v2 part")


def test_v2_session_cap_rejected():
    part = aq._ur2_part_string(1, 30000, 100, 1234, b"\x00" * 100)
    session = aq._Ur2Session()
    try:
        session.add(part)
    except aq.SessionLimitError:
        pass
    else:
        raise AssertionError("expected SessionLimitError for oversized seq_len")


# --------------------------------------------------------------------------- #
# BC-UR v1
# --------------------------------------------------------------------------- #


def test_v1_multipart_roundtrip():
    payload = ("v1 transfer payload " * 6).encode()
    frames = aq.ur1_frames(payload, 120)
    assert len(frames) > 1
    session = aq.AnimatedQrSession()
    for frame in reversed(frames):
        session.add_part(frame)
    assert session.done
    assert session.resolve()[0] == payload.decode()


def test_v1_single_part_roundtrip():
    payload = b"hello, bal"
    frames = aq.ur1_frames(payload, 400)
    assert len(frames) == 1
    session = aq.AnimatedQrSession()
    session.add_part(frames[0])
    assert session.done and session.total == 1
    assert session.resolve()[0] == payload.decode()


def test_v1_headerless_single_part_import():
    # bcr-2020-005 rev1 allows omitting the sequence header + digest entirely.
    payload = b"hello, bal"
    message = aq.cbor_byte_string(payload)
    single = "ur:bytes/" + aq.bc32_encode(message)
    assert aq.detect_format(single) == "ur1"
    session = aq.AnimatedQrSession()
    session.add_part(single)
    assert session.done
    assert session.resolve()[0] == payload.decode()


def test_v1_digest_mismatch_rejected():
    frame = aq.ur1_frames(b"hello, bal", 400)[0]
    tampered = frame[:-4] + "abcd"
    session = aq.AnimatedQrSession()
    session.add_part(tampered)
    try:
        session.resolve()
    except aq.ChecksumError:
        pass
    else:
        raise AssertionError("expected ChecksumError for a tampered v1 digest")


def test_v1_part_numbers_validated():
    for bad in (
        "ur:bytes/0of1/{}full".format("x" * 51),
        "ur:bytes/2of1/{}full".format("x" * 51),
        "ur:bytes/1of0/{}full".format("x" * 51),
        "ur:bytes/1aof1/{}full".format("x" * 51),
    ):
        try:
            aq.ur1_parse_part(bad)
        except aq.AnimatedQrError:
            pass
        else:
            raise AssertionError("expected AnimatedQrError for: {}".format(bad))


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


def test_detect_format_recognises_all_formats():
    assert aq.detect_format("BALQR1|1|1||payload") == "balqr"
    assert aq.detect_format("BAL1" + "001" + "001" + "0" + "payload") == "balqr"
    assert aq.detect_format(aq.ur1_frames(b"x", 400)[0]) == "ur1"
    assert aq.detect_format(aq.ur2_frames(b"x", 400)[0]) == "ur2"
    assert aq.detect_format(aq.bbqr_frames(b"x", 50)[0]) == "bbqr"
    assert aq.detect_format(REF_V2_SINGLE_12) == "ur2"
    assert aq.detect_format("ur:bytes/" + aq.bc32_encode(aq.cbor_byte_string(b"x"))) == "ur1"


def test_detect_format_rejects_garbage():
    for text in ("", "hello world", "BALQ|1|1||a", "ur:", "ur:txn/xyz"):
        assert aq.detect_format(text) is None, text
    # Lenient prefix probe: a string that merely *starts* with "balqr" is
    # reported as balqr (the strict parse then rejects it downstream).
    assert aq.detect_format("BALQRX|1|1||a") == "balqr"


def test_parse_for_detection_keys():
    bal = aq.parse_for_detection("BALQR1|3|2||payload")
    assert bal == ("balqr", "balqr:3", 3, 2)
    # Compact v2 frame (fixed 11-char header) is detected too.
    bal_v2 = aq.parse_for_detection("BAL1" + "007" + "004" + "0" + "payload")
    assert bal_v2 == ("balqr", "balqr:7", 7, 4)
    v2 = aq.parse_for_detection(aq.ur2_frames(b"x"*50, 400)[0])
    assert v2[0] == "ur2" and v2[2] == 1 and v2[3] == 1
    v1 = aq.parse_for_detection(aq.ur1_frames(b"x"*50, 120)[0])
    assert v1[0] == "ur1" and v1[2] > 1 and 1 <= v1[3] <= v1[2]
    bb = aq.parse_for_detection(aq.bbqr_frames(b"x"*50, 40)[0])
    assert bb[0] == "bbqr" and bb[2] >= 1 and 0 <= bb[3] < bb[2]


def test_format_names_exist():
    for fmt in ("balqr", "ur1", "ur2", "bbqr"):
        assert aq.format_name(fmt)
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
