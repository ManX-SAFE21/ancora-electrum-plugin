"""
bal.core.animated_qr
====================

GUI-free implementation of the interoperable animated-QR transfer formats
used to move BAL will data between devices.

Supported wire formats (each self-describing and order-independent on
receive):

* **BALQR** (native): ``BAL1<TTT><iii><flag><payload>`` compact fixed-width
  header (11 chars, 3-digit base36 count fields, no separators); legacy
  ``BALQR1|total|index|flags|payload`` still imported.
* **BC-UR v1** (BCR-2020-005 rev1 draft, May 2020)::
      ur:bytes/1of7/<bc32-digest>/<bc32-fragment>
    Fragments partition the BC32 rendering of the CBOR byte string; the
    SHA-256 digest of the wrapped payload ties the parts together.
* **BC-UR v2** (BCR-2020-005 rev 2 / BCR-2020-012)::
      ur:bytes/2-9/<bytewords-minimal fragment>
    Fountain-coded parts; each part is a CBOR array
    ``[seq_num, seq_len, message_len, checksum, data]`` whose CBOR bytes are
    bytewords-minimal encoded with a trailing per-part CRC-32.  The
    ``checksum`` field holds the CRC-32 of the whole wrapped message, so the
    parts are mixable and order-independent.
* **BBQR** (Coinkite)::
      B$<encoding><type><2 base36 total><2 base36 index><payload>
    Equal-length text frames; the payload is uppercase hex, RFC-4648
    base32, or raw-deflate (``wbits=-10``) zlib plus base32.

Everything is implemented from scratch on top of the Python standard library
only (``zlib``, ``hashlib``, ``base64``), so the shipped plugin zip stays a
self-contained bundle with no third-party dependencies (house rule).

This module never imports Qt or any Electrum GUI code (house rule).
"""

from __future__ import annotations

import base64
import hashlib
import zlib
from typing import Dict, FrozenSet, List, Optional, Sequence, Set, Tuple

# --------------------------------------------------------------------------- #
# Errors & safety caps
# --------------------------------------------------------------------------- #


class AnimatedQrError(ValueError):
    """Base error for all animated-QR codec failures."""


class FormatNotDetectedError(AnimatedQrError):
    """The scanned text does not look like any known animated-QR format."""


class TransferConflictError(AnimatedQrError):
    """An incoming frame belongs to a different transfer than the open one."""


class SessionLimitError(AnimatedQrError):
    """A receive session exceeded its safety caps."""


class ChecksumError(AnimatedQrError):
    """A part failed its checksum / digest validation."""


# Safety caps for untrusted scanner input.
_MAX_SESSION_PARTS = 20000
_MAX_MESSAGE_BYTES = 32 * 1024 * 1024

# --------------------------------------------------------------------------- #
# CBOR minimals (byte-string envelope + the fountain part header)
# --------------------------------------------------------------------------- #

_BYTE_STR_RES = 0x40  # byte string, length < 24
_BYTE_STR_1 = 0x58  # byte string, 1-byte length
_BYTE_STR_2 = 0x59  # byte string, 2-byte length
_BYTE_STR_4 = 0x60  # byte string, 4-byte length
_ARRAY_RES = 0x80
_UNSIGNED_RES = 0x00


def cbor_byte_string(data: bytes) -> bytes:
    """Wrap ``data`` in the minimal CBOR byte-string envelope (0x40..0x60)."""
    n = len(data)
    if n < 24:
        head = bytes([_BYTE_STR_RES + n])
    elif n <= 0xFF:
        head = bytes([_BYTE_STR_1, n])
    elif n <= 0xFFFF:
        head = bytes([_BYTE_STR_2]) + n.to_bytes(2, "big")
    elif n <= 0xFFFFFFFF:
        head = bytes([_BYTE_STR_4]) + n.to_bytes(4, "big")
    else:
        raise AnimatedQrError("payload too large for the UR byte-string envelope")
    return head + data


def unwrap_ur_cbor(message: bytes) -> bytes:
    """Strip the CBOR byte-string envelope, falling back to the raw bytes.

    Receivers keep working even when the emitter embedded the payload without
    any CBOR wrapping (some third-party ``ur:bytes`` emitters do).
    """
    if not message:
        raise AnimatedQrError("empty decoded message")
    b0 = message[0]
    if _BYTE_STR_RES <= b0 <= 0x57:
        header_len, n = 1, b0 - _BYTE_STR_RES
    elif b0 == _BYTE_STR_1 and len(message) >= 2:
        header_len, n = 2, message[1]
    elif b0 == _BYTE_STR_2 and len(message) >= 3:
        header_len, n = 3, int.from_bytes(message[1:3], "big")
    elif b0 == _BYTE_STR_4 and len(message) >= 5:
        header_len, n = 5, int.from_bytes(message[1:5], "big")
    else:
        return message
    if header_len + n != len(message):
        raise AnimatedQrError("decoded message has an inconsistent CBOR length")
    return message[header_len:]


def _cbor_unsigned(value: int) -> bytes:
    if value < 24:
        return bytes([_UNSIGNED_RES + value])
    if value <= 0xFF:
        return bytes([0x18, value])
    if value <= 0xFFFF:
        return bytes([0x19]) + value.to_bytes(2, "big")
    if value <= 0xFFFFFFFF:
        return bytes([0x1A]) + value.to_bytes(4, "big")
    return bytes([0x1B]) + value.to_bytes(8, "big")


def cbor_part(seq_num: int, seq_len: int, message_len: int, checksum: int, data: bytes) -> bytes:
    """The CBOR body of a BC-UR v2 fountain part (``[seq, seq_len, message_len, checksum, data]``)."""
    out = bytearray([_ARRAY_RES + 5])
    out += _cbor_unsigned(seq_num)
    out += _cbor_unsigned(seq_len)
    out += _cbor_unsigned(message_len)
    out += _cbor_unsigned(checksum)
    out += cbor_byte_string(data)
    return bytes(out)


def _need(buf: bytes, pos: int, count: int) -> None:
    if pos + count > len(buf):
        raise AnimatedQrError("truncated CBOR part header")


def _cbor_read_unsigned(buf: bytes, pos: int) -> Tuple[int, int]:
    if pos >= len(buf):
        raise AnimatedQrError("truncated CBOR part header")
    octet = buf[pos]
    if octet & 0xE0 != _UNSIGNED_RES:
        raise AnimatedQrError("unexpected CBOR type in part header")
    pos += 1
    additional = octet & 0x1F
    if additional < 24:
        return additional, pos
    if additional == 24:
        _need(buf, pos, 1)
        return buf[pos], pos + 1
    if additional == 25:
        _need(buf, pos, 2)
        return int.from_bytes(buf[pos : pos + 2], "big"), pos + 2
    if additional == 26:
        _need(buf, pos, 4)
        return int.from_bytes(buf[pos : pos + 4], "big"), pos + 4
    if additional == 27:
        _need(buf, pos, 8)
        return int.from_bytes(buf[pos : pos + 8], "big"), pos + 8
    raise AnimatedQrError("unsupported CBOR integer width in part header")


def _cbor_read_bytes(buf: bytes, pos: int) -> Tuple[bytes, int]:
    if pos >= len(buf):
        raise AnimatedQrError("truncated CBOR part header")
    octet = buf[pos]
    pos += 1
    if octet & 0xE0 != _BYTE_STR_RES:
        raise AnimatedQrError("expected a CBOR byte string in part header")
    additional = octet & 0x1F
    if additional < 24:
        n = additional
    elif additional == 24:
        _need(buf, pos, 1)
        n, pos = buf[pos], pos + 1
    elif additional == 25:
        _need(buf, pos, 2)
        n, pos = int.from_bytes(buf[pos : pos + 2], "big"), pos + 2
    elif additional == 26:
        _need(buf, pos, 4)
        n, pos = int.from_bytes(buf[pos : pos + 4], "big"), pos + 4
    else:
        raise AnimatedQrError("unsupported CBOR byte-string width in part header")
    _need(buf, pos, n)
    return buf[pos : pos + n], pos + n


def _cbor_read_array(buf: bytes, pos: int) -> Tuple[int, int]:
    if pos >= len(buf):
        raise AnimatedQrError("truncated CBOR part header")
    octet = buf[pos]
    pos += 1
    if octet & 0xE0 != _ARRAY_RES:
        raise AnimatedQrError("expected a CBOR array in part header")
    additional = octet & 0x1F
    if additional < 24:
        return additional, pos
    if additional == 24:
        _need(buf, pos, 1)
        return buf[pos], pos + 1
    if additional == 25:
        _need(buf, pos, 2)
        return int.from_bytes(buf[pos : pos + 2], "big"), pos + 2
    raise AnimatedQrError("unsupported CBOR array header in part")


# --------------------------------------------------------------------------- #
# CRC-32 (same polynomial as ``zlib.crc32``, network byte order)
# --------------------------------------------------------------------------- #


def crc32_int(data: bytes) -> int:
    """CRC-32 over ``data`` as an unsigned 32-bit integer."""
    return zlib.crc32(data) & 0xFFFFFFFF


def crc32_bytes(data: bytes) -> bytes:
    """CRC-32 over ``data`` as 4 network-order (big-endian) bytes."""
    return crc32_int(data).to_bytes(4, "big")


# --------------------------------------------------------------------------- #
# Bytewords (BCR-2020-012)
# --------------------------------------------------------------------------- #

_BYTEWORDS = (
    "ableacidalsoapexaquaarchatomauntawayaxisbackbaldbarnbeltbetabiasbluebodybragbr"
    "ewbulbbuzzcalmcashcatschefcityclawcodecolacookcostcruxcurlcuspcyandarkdatadays"
    "delidicedietdoordowndrawdropdrumdulldutyeacheasyechoedgeepicevenexamexiteyesfa"
    "ctfairfernfigsfilmfishfizzflapflewfluxfoxyfreefrogfuelfundgalagamegeargemsgift"
    "girlglowgoodgraygrimgurugushgyrohalfhanghardhawkheathelphighhillholyhopehornhu"
    "tsicedideaidleinchinkyintoirisironitemjadejazzjoinjoltjowljudojugsjumpjunkjury"
    "keepkenokeptkeyskickkilnkingkitekiwiknoblamblavalazyleaflegsliarlimplionlistlo"
    "goloudloveluaulucklungmainmanymathmazememomenumeowmildmintmissmonknailnavyneed"
    "newsnextnoonnotenumbobeyoboeomitonyxopenovalowlspaidpartpeckplaypluspoempoolpo"
    "sepuffpumapurrquadquizraceramprealredorichroadrockroofrubyruinrunsrustsafesaga"
    "scarsetssilkskewslotsoapsolosongstubsurfswantacotasktaxitenttiedtimetinytoilto"
    "mbtoystriptunatwinuglyundouniturgeuservastveryvetovialvibeviewvisavoidvowswall"
    "wandwarmwaspwavewaxywebswhatwhenwhizwolfworkyankyawnyellyogayurtzapszerozestzi"
    "nczonezoom"
)

_WORDS = [_BYTEWORDS[i : i + 4] for i in range(0, 1024, 4)]
_DIM = 26
_WORD_LOOKUP: Optional[List[int]] = None


def _word_lookup() -> List[int]:
    """First/last-letter lookup table (built lazily, mirrors Bytewords)."""
    global _WORD_LOOKUP
    if _WORD_LOOKUP is None:
        table = [-1] * (_DIM * _DIM)
        for i, word in enumerate(_WORDS):
            x = ord(word[0]) - ord("a")
            y = ord(word[3]) - ord("a")
            table[y * _DIM + x] = i
        _WORD_LOOKUP = table
    return _WORD_LOOKUP


def _decode_word(word: str, word_len: int) -> int:
    if len(word) != word_len:
        raise AnimatedQrError("invalid bytewords word length")
    x = ord(word[0]) - ord("a")
    y = ord(word[3] if word_len == 4 else word[1]) - ord("a")
    if not (0 <= x < _DIM and 0 <= y < _DIM):
        raise AnimatedQrError("invalid bytewords characters")
    value = _word_lookup()[y * _DIM + x]
    if value == -1:
        raise AnimatedQrError("invalid bytewords first/last pair")
    if word_len == 4:
        full = _WORDS[value]
        if word[1] != full[1] or word[2] != full[2]:
            raise AnimatedQrError("invalid bytewords middle letters")
    return value


def bytewords_minimal_encode(data: bytes) -> str:
    """BCR-2020-012 bytewords-minimal: one two-letter word per byte, then CRC."""
    crc = data + crc32_bytes(data)
    return "".join(_WORDS[b][0] + _WORDS[b][3] for b in crc)


def bytewords_minimal_decode(text: str) -> bytes:
    """Inverse of :func:`bytewords_minimal_encode` (validates the CRC-32)."""
    if len(text) % 2:
        raise AnimatedQrError("invalid bytewords length (odd)")
    values = [_decode_word(text[i : i + 2], 2) for i in range(0, len(text), 2)]
    payload = bytes(values)
    if len(payload) < 5:
        raise AnimatedQrError("bytewords payload too short")
    body, checksum = payload[:-4], payload[-4:]
    if crc32_bytes(body) != checksum:
        raise AnimatedQrError("bytewords CRC-32 mismatch")
    return body


# --------------------------------------------------------------------------- #
# BC32 (the deprecated bech32-derived codec used by BC-UR v1)
# --------------------------------------------------------------------------- #

_BC32_ALPHABET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
_BC32_REV = {ch: i for i, ch in enumerate(_BC32_ALPHABET)}
_BECH32_GENERATOR = [0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3]


def bech32_polymod(values: Sequence[int]) -> int:
    chk = 1
    for value in values:
        top = chk >> 25
        chk = (chk & 0x1FFFFFF) << 5 ^ value
        for i in range(5):
            if (top >> i) & 1:
                chk ^= _BECH32_GENERATOR[i]
    return chk


def _bc32_checksum(values: List[int]) -> List[int]:
    polymod = bech32_polymod([0] + values + [0] * 6) ^ 0x3FFFFFFF
    return [(polymod >> 5 * (5 - i)) & 31 for i in range(6)]


def _bc32_verify(values: List[int]) -> bool:
    return bech32_polymod([0] + values) == 0x3FFFFFFF


def bc32_encode(data: bytes) -> str:
    """BCR-2020-005 BC32: bech32 without the human-readable part and divider."""
    acc = 0
    bits = 0
    values: List[int] = []
    for byte in data:
        acc = (acc << 8) | byte
        bits += 8
        while bits >= 5:
            bits -= 5
            values.append((acc >> bits) & 31)
    if bits:
        values.append((acc << (5 - bits)) & 31)
    values += _bc32_checksum(values)
    return "".join(_BC32_ALPHABET[v] for v in values)


def bc32_decode(text: str) -> bytes:
    """Inverse of :func:`bc32_encode` (validates the 6-char checksum)."""
    lowered = text.lower()
    try:
        values = [_BC32_REV[ch] for ch in lowered]
    except KeyError:
        raise AnimatedQrError("invalid BC-UR v1 character") from None
    if len(values) < 6 or not _bc32_verify(values):
        raise AnimatedQrError("invalid BC-UR v1 checksum")
    data = values[:-6]
    acc = 0
    bits = 0
    out = bytearray()
    for value in data:
        acc = (acc << 5) | value
        bits += 5
        if bits >= 8:
            bits -= 8
            out.append((acc >> bits) & 0xFF)
    return bytes(out)


# --------------------------------------------------------------------------- #
# xoshiro256** + alias sampler (exact ports of the reference RNG chain)
# --------------------------------------------------------------------------- #

_MASK64 = (1 << 64) - 1


class _Xoshiro256:
    """xoshiro256** 1.0, seeded via SHA-256 of a byte sequence."""

    def __init__(self, seed: bytes):
        digest = hashlib.sha256(seed).digest()
        self._s = [
            int.from_bytes(digest[offset : offset + 8], "big")
            for offset in range(0, 32, 8)
        ]

    @staticmethod
    def _rotl(x: int, k: int) -> int:
        return ((x << k) | (x >> (64 - k))) & _MASK64

    def next(self) -> int:
        result = (self._rotl((self._s[1] * 5) & _MASK64, 7) * 9) & _MASK64
        t = (self._s[1] << 17) & _MASK64
        s = self._s
        s[2] ^= s[0]
        s[3] ^= s[1]
        s[1] ^= s[2]
        s[0] ^= s[3]
        s[2] ^= t
        s[3] = self._rotl(s[3], 45)
        return result

    def next_double(self) -> float:
        return self.next() / float(1 << 64)

    def next_int(self, low: int, high: int) -> int:
        return int(self.next_double() * (high - low + 1)) + low


class _RandomAliasSampler:
    """Vose's alias method, built in the exact order of the reference code."""

    def __init__(self, probs: Sequence[float]):
        total = sum(probs)
        assert total > 0
        n = len(probs)
        normalized = [p * float(n) / total for p in probs]

        small: List[int] = []
        large: List[int] = []
        for i in range(n - 1, -1, -1):
            (small if normalized[i] < 1 else large).append(i)

        self._probs = [0] * n
        self._aliases = [0] * n
        while small and large:
            a = small.pop()
            g = large.pop()
            self._probs[a] = normalized[a]
            self._aliases[a] = g
            normalized[g] += normalized[a] - 1
            (small if normalized[g] < 1 else large).append(g)

        while large:
            self._probs[large.pop()] = 1
        while small:
            self._probs[small.pop()] = 1

    def next(self, rng: _Xoshiro256) -> int:
        r1 = rng.next_double()
        r2 = rng.next_double()
        n = len(self._probs)
        i = int(float(n) * r1)
        return i if r2 < self._probs[i] else self._aliases[i]


def choose_fragments(seq_num: int, seq_len: int, checksum: int) -> Set[int]:
    """The fragments mixed into a BC-UR v2 fountain part (reference seed math).

    Sequence numbers ``1..seq_len`` emit the pure fragment ``{seq_num - 1}``;
    every larger sequence number deterministically mixes a pseudo-random
    subset of fragments seeded by ``SHA256(seq ‖ checksum)``.
    """
    if seq_num <= seq_len:
        return {seq_num - 1}
    seed = seq_num.to_bytes(4, "big") + checksum.to_bytes(4, "big")
    rng = _Xoshiro256(seed)
    probs: List[float] = [1.0 / i for i in range(1, seq_len + 1)]
    degree = _RandomAliasSampler(probs).next(rng) + 1
    remaining = list(range(seq_len))
    shuffled: List[int] = []
    while remaining:
        index = rng.next_int(0, len(remaining) - 1)
        shuffled.append(remaining.pop(index))
    return set(shuffled[:degree])


def _partition_message(message: bytes, fragment_len: int) -> List[bytes]:
    fragments: List[bytes] = []
    for offset in range(0, len(message), fragment_len):
        fragment = message[offset : offset + fragment_len]
        if len(fragment) < fragment_len:
            fragment += b"\x00" * (fragment_len - len(fragment))
        fragments.append(fragment)
    return fragments


def _mix_fragments(fragments: Sequence[bytes], indexes: Set[int], fragment_len: int) -> bytes:
    result = bytearray(fragment_len)
    for index in indexes:
        for i, byte in enumerate(fragments[index]):
            result[i] ^= byte
    return bytes(result)


# --------------------------------------------------------------------------- #
# BC-UR v2 (bytewords-minimal + fountain)
# --------------------------------------------------------------------------- #


def _ur2_header(seq_num: int, seq_len: int) -> str:
    return "ur:bytes/{}-{}/".format(seq_num, seq_len)


def _ur2_part_string(seq_num: int, seq_len: int, message_len: int, checksum: int, data: bytes) -> str:
    body = cbor_part(seq_num, seq_len, message_len, checksum, data)
    return _ur2_header(seq_num, seq_len) + bytewords_minimal_encode(body)


def _ur2_part_cost(seq_num: int, seq_len: int, message_len: int, checksum: int, data_len: int) -> int:
    body_len = len(cbor_part(seq_num, seq_len, message_len, checksum, b"\x00" * data_len))
    # bytewords_minimal_encode appends a 4-byte CRC over the body.
    return len(_ur2_header(seq_num, seq_len)) + 2 * (body_len + 4)


def ur2_frames(payload: bytes, budget_chars: int) -> List[str]:
    """Encode ``payload`` into BC-UR v2 fountain frames.

    ``budget_chars`` is the largest frame string the carrying QR code may
    hold. The first ``seq_len`` frames are pure (one fragment each); a second
    wave of ``seq_len`` mixed (fountain) frames follows so the receiver can
    recover with a few parts still missing.
    """
    message = cbor_byte_string(payload)
    message_len = len(message)
    checksum = crc32_int(message)
    single_cost = len("ur:bytes/") + len(bytewords_minimal_encode(message))
    if single_cost <= budget_chars:
        return ["ur:bytes/" + bytewords_minimal_encode(message)]

    fragment_len = message_len
    fragment_count = 1
    while True:
        seq_len = fragment_count
        worst_seq = 2 * seq_len  # the export loop emits up to 2*seq_len parts
        cost = _ur2_part_cost(worst_seq, seq_len, message_len, checksum, fragment_len)
        if cost <= budget_chars:
            break
        fragment_count += 1
        fragment_len = -(-message_len // fragment_count)
        if fragment_count > message_len:
            raise AnimatedQrError("QR budget too small for a BC-UR v2 part")

    fragments = _partition_message(message, fragment_len)
    frames: List[str] = []
    for seq_num in range(1, seq_len + 1):
        frames.append(_ur2_part_string(seq_num, seq_len, message_len, checksum, fragments[seq_num - 1]))
    for seq_num in range(seq_len + 1, 2 * seq_len + 1):
        data = _mix_fragments(fragments, choose_fragments(seq_num, seq_len, checksum), fragment_len)
        frames.append(_ur2_part_string(seq_num, seq_len, message_len, checksum, data))
    return frames


def ur2_parse_part(frame_text: str) -> Tuple[int, int, int, int, bytes]:
    """Parse a BC-UR v2 part into ``(seq, seq_len, message_len, checksum, data)``."""
    frame_text = frame_text.strip().lower()
    prefix = "ur:bytes/"
    if not frame_text.startswith(prefix):
        raise AnimatedQrError("not a BC-UR v2 part")
    tail = frame_text[len(prefix) :]
    if "/" not in tail:
        body = bytewords_minimal_decode(tail)
        return 1, 1, len(body), crc32_int(body), body
    seq_head, words = tail.split("/", 1)
    try:
        seq_num_s, seq_len_s = seq_head.split("-", 1)
        seq_num, seq_len = int(seq_num_s), int(seq_len_s)
    except ValueError:
        raise AnimatedQrError("bad BC-UR v2 sequence header") from None
    if seq_len < 1 or not 1 <= seq_num <= 2**32 - 1:
        raise AnimatedQrError("bad BC-UR v2 sequence numbers")
    body = bytewords_minimal_decode(words)
    arr, pos = _cbor_read_array(body, 0)
    if arr != 5:
        raise AnimatedQrError("bad BC-UR v2 part header arity")
    seq_again, pos = _cbor_read_unsigned(body, pos)
    seq_len_again, pos = _cbor_read_unsigned(body, pos)
    message_len, pos = _cbor_read_unsigned(body, pos)
    checksum, pos = _cbor_read_unsigned(body, pos)
    data, pos = _cbor_read_bytes(body, pos)
    if pos != len(body):
        raise AnimatedQrError("trailing garbage in BC-UR v2 part header")
    if seq_again != seq_num or seq_len_again != seq_len:
        raise AnimatedQrError("BC-UR v2 part header mismatch")
    return seq_num, seq_len, message_len, checksum, bytes(data)


# --------------------------------------------------------------------------- #
# BC-UR v1 (BCR-2020-005 rev1: BC32 fragments + SHA-256 digest)
# --------------------------------------------------------------------------- #


def _ur1_digest(message: bytes) -> str:
    return bc32_encode(hashlib.sha256(message).digest())


def _ur1_prefix(index: int, total: int, digest: str) -> str:
    return "ur:bytes/{}{}/{}/".format(
        index, "of{}".format(total), digest
    )


def ur1_frames(payload: bytes, budget_chars: int) -> List[str]:
    """Encode ``payload`` into BC-UR v1 fragments (``NofM`` + BC32 + digest)."""
    message = cbor_byte_string(payload)
    digest = _ur1_digest(message)
    full = bc32_encode(message)

    total = 1
    while True:
        longest = _ur1_prefix(total, total, digest)
        capacity = budget_chars - len(longest)
        if capacity < 1:
            raise AnimatedQrError("QR budget too small for BC-UR v1")
        if len(full) <= capacity * total:
            break
        total += 1
        if total > _MAX_SESSION_PARTS:
            raise AnimatedQrError("BC-UR v1 transfer demands too many parts")

    frames: List[str] = []
    pos = 0
    for index in range(1, total + 1):
        prefix = _ur1_prefix(index, total, digest)
        capacity = budget_chars - len(prefix)
        frames.append(prefix + full[pos : pos + capacity])
        pos += capacity
    return frames


def ur1_parse_part(frame_text: str) -> Tuple[int, int, str, str]:
    """Parse a BC-UR v1 part into ``(index, total, digest, fragment)``.

    Accepts both the multipart form (``ur:bytes/NofM/<digest>/<frag>``) and
    the single-part form (``ur:bytes/<frag>``, no sequence header or digest).
    """
    frame_text = frame_text.strip().lower()
    prefix = "ur:bytes/"
    if not frame_text.startswith(prefix):
        raise AnimatedQrError("not a BC-UR v1 part")
    tail = frame_text[len(prefix) :]
    parts = tail.split("/")
    if len(parts) == 1:
        return 1, 1, "", parts[0]
    if len(parts) != 3:
        raise AnimatedQrError("bad BC-UR v1 part structure")
    seq_head, digest, fragment = parts
    if "of" not in seq_head:
        raise AnimatedQrError("BC-UR v1 part misses the sequence header")
    try:
        index_s, total_s = seq_head.split("of", 1)
        index, total = int(index_s), int(total_s)
    except ValueError:
        raise AnimatedQrError("bad BC-UR v1 sequence header") from None
    if total < 1 or not 1 <= index <= total:
        raise AnimatedQrError("bad BC-UR v1 sequence numbers")
    if len(digest) != 58:
        raise AnimatedQrError("bad BC-UR v1 digest")
    return index, total, digest, fragment


# --------------------------------------------------------------------------- #
# BBQR (Coinkite)
# --------------------------------------------------------------------------- #

_BBQR_PREFIX = "B$"


def _bbqr_base36(n: int) -> str:
    if not 0 <= n <= 1295:
        raise AnimatedQrError("BBQR part count out of range")

    def digit(x: int) -> str:
        return chr(48 + x) if x < 10 else chr(65 + x - 10)

    return digit(n // 36) + digit(n % 36)


def _bbqr_base32(data: bytes) -> str:
    return base64.b32encode(data).decode("ascii").rstrip("=")


def _bbqr_encode(raw: bytes, encoding: str) -> Tuple[str, str, int]:
    """Return ``(encoding, encoded_text, split_mod)`` honouring the reference."""
    if encoding == "H":
        return "H", raw.hex().upper(), 2
    if encoding == "Z":
        compressor = zlib.compressobj(wbits=-10)
        compressed = compressor.compress(raw) + compressor.flush()
        if len(compressed) < len(raw):
            return "Z", _bbqr_base32(compressed), 8
        encoding = "2"
    if encoding != "2":
        raise AnimatedQrError("unknown BBQR encoding")
    return "2", _bbqr_base32(raw), 8


def bbqr_frames(payload: bytes, budget_chars: int, encoding: str = "Z", type_code: str = "B") -> List[str]:
    """Encode ``payload`` into BBQR frames (``B$<enc><type><N><n>…``)."""
    if len(type_code) != 1 or not type_code.isalnum():
        raise AnimatedQrError("bad BBQR type code")
    encoding, encoded, split_mod = _bbqr_encode(payload, encoding)
    chunk = budget_chars - 8
    if chunk < split_mod:
        raise AnimatedQrError("QR budget too small for a BBQR frame")
    chunk -= chunk % split_mod
    if chunk < 1:
        raise AnimatedQrError("QR budget too small for a BBQR frame")
    if len(payload) > _MAX_MESSAGE_BYTES:
        raise AnimatedQrError("BBQR payload exceeds the size cap")
    total = -(-len(encoded) // chunk)
    if total > 1295:
        raise AnimatedQrError("BBQR transfer demands too many parts")
    header = _BBQR_PREFIX + encoding + type_code + _bbqr_base36(total)
    frames: List[str] = []
    pos = 0
    for index in range(total):
        frames.append(header + _bbqr_base36(index) + encoded[pos : pos + chunk])
        pos += chunk
    return frames


def bbqr_parse_part(frame_text: str) -> Tuple[str, str, int, int, str]:
    """Parse a BBQR frame into ``(encoding, type_code, total, index, payload)``."""
    frame_text = frame_text.strip()
    if len(frame_text) < 10 or not frame_text.startswith(_BBQR_PREFIX):
        raise AnimatedQrError("not a BBQR frame")
    encoding = frame_text[2]
    type_code = frame_text[3]
    if encoding not in ("H", "2", "Z"):
        raise AnimatedQrError("unknown BBQR encoding")
    try:
        total = int(frame_text[4:6], 36)
        index = int(frame_text[6:8], 36)
    except ValueError:
        raise AnimatedQrError("bad BBQR part numbers") from None
    if total < 1 or not 0 <= index < total:
        raise AnimatedQrError("bad BBQR part numbers")
    if index >= _MAX_SESSION_PARTS:
        raise AnimatedQrError("BBQR part number out of range")
    return encoding, type_code, total, index, frame_text[8:]


def _bbqr_decode(encoded_parts: Sequence[str], encoding: str) -> bytes:
    pieces: List[bytes] = []
    for part in encoded_parts:
        if encoding == "H":
            try:
                pieces.append(bytes.fromhex(part))
            except ValueError:
                raise AnimatedQrError("invalid BBQR hex payload") from None
            continue
        padding = (8 - (len(part) % 8)) % 8
        try:
            pieces.append(base64.b32decode(part + "=" * padding))
        except (ValueError, TypeError):
            raise AnimatedQrError("invalid BBQR base32 payload") from None
    raw = b"".join(pieces)
    if encoding == "Z":
        try:
            inflater = zlib.decompressobj(wbits=-10)
            out = inflater.decompress(raw, _MAX_MESSAGE_BYTES + 1)
        except zlib.error:
            raise AnimatedQrError("invalid BBQR zlib payload") from None
        if len(out) > _MAX_MESSAGE_BYTES or inflater.unconsumed_tail:
            raise AnimatedQrError("BBQR payload exceeds the size cap")
        return out
    return raw


# --------------------------------------------------------------------------- #
# Format detection & per-frame identity for the shared debounce
# --------------------------------------------------------------------------- #

FORMAT_LABELS = {
    "balqr": "BAL QR",
    "ur1": "BC-UR v1",
    "ur2": "BC-UR v2",
    "bbqr": "BBQR",
}


def format_name(fmt: str) -> str:
    """Human-readable name of a wire format for UI labels."""
    return FORMAT_LABELS.get(fmt, fmt)


def detect_format(text: str) -> Optional[str]:
    """Return the wire format of a scanned string, or ``None``."""
    text = text.strip()
    if not text:
        return None
    lowered = text.lower()
    if lowered.startswith(("balqr", "bal1")):
        return "balqr"
    if text.startswith(_BBQR_PREFIX):
        return "bbqr"
    if not lowered.startswith("ur:"):
        return None
    if lowered.startswith("ur:bytes/"):
        remainder = lowered[len("ur:bytes/") :]
        first = remainder.split("/", 1)[0]
        if "of" in first:
            return "ur1"
        if "-" in first:
            return "ur2"
        # Single-part: the whole remainder is the body. Prefer a bytewords v2
        # body (CBOR byte-string head 0x40..0x60), then BC32 v1.
        try:
            body = bytewords_minimal_decode(remainder)
        except AnimatedQrError:
            pass
        else:
            if body and 0x40 <= body[0] <= 0x60:
                return "ur2"
        try:
            bc32_decode(remainder)
        except AnimatedQrError:
            return None
        return "ur1"
    return None


def parse_for_detection(text: str) -> Tuple[str, str, int, int]:
    """Parse a frame and return ``(format, session_key, frame_total, index)``.

    ``session_key`` identifies the transfer the frame belongs to and drives
    the shared reset/ignore/accept debounce. Raises
    :class:`AnimatedQrError` when the text cannot be parsed.
    """
    fmt = detect_format(text)
    if fmt == "balqr":
        total, index, _compressed, _payload = _parse_balqr(text)
        return "balqr", "balqr:{}".format(total), total, index
    if fmt == "ur1":
        index, total, digest, _frag = ur1_parse_part(text)
        return "ur1", "ur1:{}".format(digest), total, index
    if fmt == "ur2":
        seq, seq_len, message_len, checksum, _data = ur2_parse_part(text)
        return "ur2", "ur2:{}-{}-{}".format(seq_len, message_len, checksum), seq_len, seq
    if fmt == "bbqr":
        encoding, type_code, total, index, _payload = bbqr_parse_part(text)
        return "bbqr", "bbqr:{}{}:{}".format(encoding, type_code, total), total, index
    raise FormatNotDetectedError("Not a supported QR transfer format")


def _parse_balqr(text: str) -> Tuple[int, int, bool, str]:
    from bal.core.qrtransfer import parse_frame

    return parse_frame(text)


# --------------------------------------------------------------------------- #
# Receive sessions (order-independent assembly per format)
# --------------------------------------------------------------------------- #

class _BalQrSession:
    def __init__(self):
        self._frames: Dict[int, str] = {}
        self._total = 0
        self._compressed = False

    @property
    def total(self) -> int:
        return self._total

    @property
    def received(self) -> int:
        return len(self._frames)

    @property
    def done(self) -> bool:
        return bool(self._total) and len(self._frames) >= self._total

    def add(self, text: str) -> str:
        total, index, compressed, payload = _parse_balqr(text)
        if self._total and total != self._total:
            raise TransferConflictError("BAL QR transfer total changed")
        if len(self._frames) >= _MAX_SESSION_PARTS:
            raise SessionLimitError("too many BAL QR frames")
        if not self._total:
            self._total = total
            self._compressed = compressed
        if index in self._frames:
            return "dup"
        self._frames[index] = payload
        return "ok"

    def resolve(self) -> Tuple[str, bool]:
        from bal.core.qrtransfer import assemble

        text = assemble(self._frames, self._total)
        return text, self._compressed


class _Ur1Session:
    def __init__(self):
        self._total = 0
        self._digest = ""
        self._fragments: Dict[int, str] = {}

    @property
    def total(self) -> int:
        return self._total

    @property
    def received(self) -> int:
        return len(self._fragments)

    @property
    def done(self) -> bool:
        return bool(self._total) and len(self._fragments) >= self._total

    def add(self, text: str) -> str:
        index, total, digest, fragment = ur1_parse_part(text)
        if self._total:
            if total != self._total or digest != self._digest:
                raise TransferConflictError("BC-UR v1 transfer digest changed")
        else:
            self._total = total
            self._digest = digest
            if total > _MAX_SESSION_PARTS:
                raise SessionLimitError("BC-UR v1 demands too many parts")
        if index in self._fragments:
            return "dup"
        self._fragments[index] = fragment
        return "ok"

    def resolve(self) -> Tuple[str, bool]:
        full = "".join(self._fragments[i] for i in range(1, self._total + 1))
        try:
            message = bc32_decode(full)
        except AnimatedQrError:
            raise ChecksumError("BC-UR v1 checksum mismatch") from None
        if self._digest and _ur1_digest(message) != self._digest:
            raise ChecksumError("BC-UR v1 digest mismatch")
        return _transfer_text(unwrap_ur_cbor(message)), False


class _Ur2Session:
    """Fountain decoder mirroring the reference (C++/python) semantics."""

    def __init__(self):
        self._seq_len = 0
        self._message_len = 0
        self._checksum = 0
        self._fragment_len = 0
        self._received: Set[int] = set()
        self._simple: Dict[FrozenSet[int], bytes] = {}
        self._mixed: Dict[FrozenSet[int], bytes] = {}
        self._queue: List[Tuple[FrozenSet[int], bytes]] = []
        self._processed = 0
        self._result: Optional[bytes] = None
        self._bad = False

    @property
    def total(self) -> int:
        return self._seq_len

    @property
    def received(self) -> int:
        return self._processed

    @property
    def done(self) -> bool:
        return self._result is not None

    def add(self, text: str) -> str:
        seq, seq_len, message_len, checksum, data = ur2_parse_part(text)
        if self._seq_len:
            if not self._validate(seq_len, message_len, checksum, len(data)):
                raise TransferConflictError("BC-UR v2 transfer header changed")
        else:
            self._seq_len = seq_len
            self._message_len = message_len
            self._checksum = checksum
            self._fragment_len = len(data)
            if seq_len > _MAX_SESSION_PARTS or message_len > _MAX_MESSAGE_BYTES:
                raise SessionLimitError("BC-UR v2 session exceeds safety caps")
        indexes = frozenset(choose_fragments(seq, self._seq_len, self._checksum))
        self._receive(indexes, bytes(data))
        return "ok"

    def _validate(self, seq_len: int, message_len: int, checksum: int, data_len: int) -> bool:
        return (
            seq_len == self._seq_len
            and message_len == self._message_len
            and checksum == self._checksum
            and data_len == self._fragment_len
        )

    def _receive(self, indexes: FrozenSet[int], data: bytes) -> None:
        if self._result is not None or self._bad:
            return
        self._queue.append((indexes, data))
        while self._result is None and not self._bad and self._queue:
            self._process(self._queue.pop(0))
        self._processed += 1

    def _process(self, item: Tuple[FrozenSet[int], bytes]) -> None:
        indexes, data = item
        if len(indexes) == 1:
            self._process_simple(indexes, data)
        else:
            self._process_mixed(indexes, data)

    def _process_simple(self, indexes: FrozenSet[int], data: bytes) -> None:
        fragment_index = next(iter(indexes))
        if fragment_index in self._received:
            return
        self._simple[indexes] = data
        self._received.add(fragment_index)
        if self._received == set(range(self._seq_len)):
            self._finish()
            return
        self._reduce_mixed_by(indexes, data)

    def _reduce_mixed_by(self, indexes: FrozenSet[int], data: bytes) -> None:
        new_mixed: Dict[FrozenSet[int], bytes] = {}
        for other_indexes, other_data in self._mixed.items():
            reduced = self._reduce_part(other_indexes, other_data, indexes, data)
            if len(reduced[0]) == 1:
                self._queue.append(reduced)
            else:
                new_mixed[reduced[0]] = reduced[1]
        self._mixed = new_mixed

    def _process_mixed(self, indexes: FrozenSet[int], data: bytes) -> None:
        if indexes in self._mixed:
            return
        reduced_indexes, reduced_data = indexes, data
        for simple_indexes, simple_data in self._simple.items():
            reduced_indexes, reduced_data = self._reduce_part(
                reduced_indexes, reduced_data, simple_indexes, simple_data
            )
        for other_indexes, other_data in list(self._mixed.items()):
            reduced_indexes, reduced_data = self._reduce_part(
                reduced_indexes, reduced_data, other_indexes, other_data
            )
        if len(reduced_indexes) == 1:
            self._queue.append((reduced_indexes, reduced_data))
        else:
            self._reduce_mixed_by(reduced_indexes, reduced_data)
            if reduced_indexes not in self._mixed:
                self._mixed[reduced_indexes] = reduced_data

    @staticmethod
    def _reduce_part(
        a_indexes: FrozenSet[int], a_data: bytes, b_indexes: FrozenSet[int], b_data: bytes
    ) -> Tuple[FrozenSet[int], bytes]:
        if b_indexes == a_indexes or not b_indexes.issubset(a_indexes):
            return a_indexes, a_data
        new_indexes = a_indexes - b_indexes
        new_data = bytes(x ^ y for x, y in zip(a_data, b_data, strict=True))
        return new_indexes, new_data

    def _finish(self) -> None:
        fragments = []
        for index in range(self._seq_len):
            key = frozenset([index])
            if key not in self._simple:
                self._bad = True
                return
            fragments.append(self._simple[key])
        message = b"".join(fragments)[: self._message_len]
        if crc32_int(message) != self._checksum:
            self._bad = True
            return
        self._result = message

    def resolve(self) -> Tuple[str, bool]:
        if self._bad:
            raise ChecksumError("BC-UR v2 message checksum mismatch")
        if self._result is None:
            raise AnimatedQrError("BC-UR v2 session is not complete")
        return _transfer_text(unwrap_ur_cbor(self._result)), False


class _BbqrSession:
    def __init__(self):
        self._total = 0
        self._encoding = ""
        self._type_code = ""
        self._parts: Dict[int, str] = {}

    @property
    def total(self) -> int:
        return self._total

    @property
    def received(self) -> int:
        return len(self._parts)

    @property
    def done(self) -> bool:
        return bool(self._total) and len(self._parts) >= self._total

    def add(self, text: str) -> str:
        encoding, type_code, total, index, payload = bbqr_parse_part(text)
        if self._total:
            if (encoding, type_code, total) != (self._encoding, self._type_code, self._total):
                raise TransferConflictError("BBQR frame header changed")
        else:
            self._total = total
            self._encoding = encoding
            self._type_code = type_code
        if index in self._parts:
            return "dup"
        self._parts[index] = payload
        return "ok"

    def resolve(self) -> Tuple[str, bool]:
        ordered = [self._parts[i] for i in range(self._total)]
        raw = _bbqr_decode(ordered, self._encoding)
        return _transfer_text(raw), False


def _transfer_text(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise AnimatedQrError("decoded transfer is not valid UTF-8") from None


class AnimatedQrSession:
    """Facade over the per-format receive sessions used by the QR import page."""

    _DIALECTS = (("balqr", "_BalQrSession"), ("ur1", "_Ur1Session"), ("ur2", "_Ur2Session"), ("bbqr", "_BbqrSession"))

    def __init__(self):
        self._inner: Optional[object] = None
        self._fmt: Optional[str] = None

    @property
    def format(self) -> Optional[str]:
        return self._fmt

    def add_part(self, text: str) -> str:
        """Feed one scanned frame; returns ``"ok"``/``"dup"``, raises on bad input."""
        fmt = detect_format(text)
        if fmt is None:
            raise FormatNotDetectedError("Not a supported QR transfer format")
        if self._inner is None:
            self._fmt = fmt
            self._inner = self._make(fmt)
        elif fmt != self._fmt:
            raise TransferConflictError(
                "Switched QR format mid-import ({} -> {})".format(self.format, fmt)
            )
        return self._inner.add(text)  # type: ignore[no-any-return]

    @staticmethod
    def _make(fmt: str) -> object:
        if fmt == "balqr":
            return _BalQrSession()
        if fmt == "ur1":
            return _Ur1Session()
        if fmt == "ur2":
            return _Ur2Session()
        if fmt == "bbqr":
            return _BbqrSession()
        raise AssertionError("unknown animated-QR format {}".format(fmt))

    @property
    def total(self) -> int:
        return self._inner.total if self._inner is not None else 0

    @property
    def received(self) -> int:
        return self._inner.received if self._inner is not None else 0

    @property
    def done(self) -> bool:
        return bool(self._inner is not None and self._inner.done)

    def resolve(self) -> Tuple[str, bool]:
        if self._inner is None:
            raise AnimatedQrError("no transfer has been received")
        return self._inner.resolve()  # type: ignore[no-any-return]
