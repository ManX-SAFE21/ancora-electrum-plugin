"""
bal.core.qrtransfer
===================

GUI-free helpers for moving BAL will data between devices via QR codes or
the Electrum ``audio_modem`` plugin (see ``PLAN_QR_TRANSFER.md``).

Scope
-----
* converts will transactions into a compact ``transfer_string``
  (newline-joined serialized transactions, optionally zlib + base64
  compressed);
* splits that string into fixed-size ``BAL1<TTT><iii><flag>`` frames for
  multi-QR export, and reassembles/validates them on import.

Wire format (v2, compact)
-------------------------
A frame is::

    BAL1<TTT><iii><flag><payload>

* ``BAL1``    - magic + format era (4 chars).
* ``TTT``     - frame total as exactly 3 base36 digits (1-based, cap 46655).
* ``iii``     - frame index as exactly 3 base36 digits (1-based).
* ``flag``    - one char: ``Z`` (zlib + base64) or ``0`` (plain ASCII).
* ``payload`` - every other character of the frame; the payloads of all
  frames, concatenated in index order, rebuild the transfer string.

The fixed 11-char header replaces the legacy ``BALQR1|N|i|flags|`` form
(same 5 pieces of information) without any pipe separator, so the whole
frame is scan-friendly and the overhead no longer grows with the frame
count.  Legacy ``BALQR1|…`` frames are still accepted on import.

The audio-modem channel deliberately bypasses the framing helpers here
(PLAN_QR_TRANSFER.md section 4.4): its transport compresses internally and
carries the whole transfer string in a single blob, so callers only use
:func:`encode_transfer` / :func:`decode_transfer`.

This module never imports Qt or any Electrum GUI code (house rule).
"""

from __future__ import annotations

import base64
import zlib

MAGIC = "BALQR"
VERSION = 1
FLAG_COMPRESSED = "Z"
FLAG_PLAIN = "0"

# 4 standard presets (label, payload budget in bytes per QR). Ordered from
# low-resolution cameras to high-resolution cameras (owner decision D5).
CHUNK_PRESETS = (
    ("Small - ~150 bytes/QR (low-res cameras)", 150),
    ("Medium - ~400 bytes/QR", 400),
    ("Large - ~900 bytes/QR", 900),
    ("XL - ~1800 bytes/QR (high-res cameras)", 1800),
)

# Smallest allowed payload budget per frame, below which the frame header
# could consume the whole budget.
MIN_CHUNK_SIZE = 40

# Legacy wire format (still imported); the exporter emits the v2 form below.
_FRAME_MAGIC_V1 = MAGIC + str(VERSION)

# Compact v2 wire format: fixed-width base36 count fields, no separators.
_FRAME_MAGIC_V2 = "BAL1"
_BASE36_DIGITS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_BASE36_WIDTH = 3
_HEADER_V2_LEN = len(_FRAME_MAGIC_V2) + 2 * _BASE36_WIDTH + 1
_MAX_TOTAL = 36 ** _BASE36_WIDTH - 1


class QrTransferError(ValueError):
    """Base error for will QR / audio transfer processing."""


class MissingFramesError(QrTransferError):
    """Some frame indices of a multi-QR transfer are missing."""

    def __init__(self, missing):
        self.missing = list(missing)
        super().__init__("Missing QR frames: {}".format(self.missing))


class InconsistentTotalError(QrTransferError):
    """Frames disagree about the advertised frame total."""


def encode_transfer(tx_strings, compress=False):
    """Join serialized transaction strings into a transfer string.

    ``compress=True`` wraps the joined text in zlib + base64 (ASCII-safe) so
    the whole bundle shrinks before being printed/scanned. The optional flag
    of the frame header lets the importer reverse this automatically.
    """
    return __compress("\n".join(tx_strings), enabled=compress)


def encode_transfer_best(tx_strings):
    """Encode ``tx_strings`` with the smaller of plain vs compressed form.

    Returns ``(transfer_string, compressed: bool)``.  Compressed wins only
    when zlib + base64 really is shorter (best-of, never larger).
    """
    joined = "\n".join(tx_strings)
    plain = joined
    compressed = __compress(joined, enabled=True)
    if len(compressed) < len(plain):
        return compressed, True
    return plain, False


def decode_transfer(transfer_string, compressed):
    """Inverse of :func:`encode_transfer`.

    Returns the list of serialized transaction strings; empty frames are
    dropped so a trailing newline (or an empty payload) cannot produce an
    empty trailing element.
    """
    text = __decompress(transfer_string, enabled=compressed)
    return [part for part in text.split("\n") if part]


def split_frames(transfer_string, chunk_size, compressed=False):
    """Split ``transfer_string`` into full compact ``BAL1`` frames.

    Every returned frame has the fixed 11-char v2 header followed by its
    share of the payload, so each frame is at most ``chunk_size`` characters
    long.  ``compressed`` stamps the ``Z`` flag into every frame so the
    importer knows how to reverse the encoding.

    Raises :class:`QrTransferError` when ``chunk_size`` is too small to hold
    the header plus any payload, or when the transfer needs more than
    :data:`_MAX_TOTAL` frames.
    """
    flag = FLAG_COMPRESSED if compressed else FLAG_PLAIN
    total = __compute_total(len(transfer_string), chunk_size)
    budget = chunk_size - _HEADER_V2_LEN
    frames = []
    pos = 0
    length = len(transfer_string)
    for index in range(1, total + 1):
        end = min(pos + budget, length)
        frames.append(
            _FRAME_MAGIC_V2
            + _base36(total)
            + _base36(index)
            + flag
            + transfer_string[pos:end]
        )
        pos = end
    if pos < length:
        # __compute_total guarantees this cannot happen; keep a safety net.
        raise QrTransferError("internal error: frames did not cover the transfer string")
    return frames


def parse_frame(frame):
    """Parse a single frame.

    Accepts both the legacy ``BALQR1|total|index|flags|payload`` form and
    the compact ``BAL1<total><index><flag><payload>`` v2 form.

    Returns ``(total, index, compressed: bool, payload: str)``. Raises
    :class:`QrTransferError` on malformed input (bad magic/version, wrong
    arity, non-integer or out-of-range frame numbers, unknown flags).
    """
    if frame.startswith(_FRAME_MAGIC_V2):
        return _parse_v2(frame)
    return _parse_v1(frame)


def assemble(frames, total):
    """Concatenate frame payloads back into a transfer string.

    ``frames`` maps 1-based index -> payload. Every index ``1..total`` must
    be present (else :class:`MissingFramesError`) and no index may exceed
    ``total`` (else :class:`InconsistentTotalError`).
    """
    if total < 1:
        raise QrTransferError("invalid frame total")
    missing = [index for index in range(1, total + 1) if index not in frames]
    if missing:
        raise MissingFramesError(missing)
    extra = [index for index in frames if index > total]
    if extra:
        raise InconsistentTotalError()
    return "".join(frames[index] for index in range(1, total + 1))


def preset_index_for_chunk_size(chunk_size):
    """Return the :data:`CHUNK_PRESETS` index whose budget best matches a size."""
    best, best_diff = 0, abs(chunk_size - CHUNK_PRESETS[0][1])
    for index, (_label, budget) in enumerate(CHUNK_PRESETS):
        diff = abs(chunk_size - budget)
        if diff < best_diff:
            best, best_diff = index, diff
    return best


# --------------------------------------------------------------------------- #
# Internals
# --------------------------------------------------------------------------- #

def __compress(text, *, enabled):
    if not enabled:
        return text
    return base64.b64encode(zlib.compress(text.encode("utf-8"))).decode("ascii")


def __decompress(text, *, enabled):
    if not enabled:
        return text
    return zlib.decompress(base64.b64decode(text.encode("ascii"))).decode("utf-8")


def _base36(n):
    """Zero-padded :data:`_BASE36_WIDTH` base36 render of ``n``."""
    if not 0 <= n <= _MAX_TOTAL:
        raise QrTransferError("BAL QR part number out of range: {}".format(n))
    chars = []
    for _ in range(_BASE36_WIDTH):
        chars.append(_BASE36_DIGITS[n % 36])
        n //= 36
    return "".join(reversed(chars))


def _base36_decode(text):
    """Inverse of :func:`_base36`; raises ``ValueError`` on bad input."""
    if len(text) != _BASE36_WIDTH or any(c not in _BASE36_DIGITS for c in text):
        raise ValueError(text)
    n = 0
    for c in text:
        n = n * 36 + _BASE36_DIGITS.index(c)
    return n


def _parse_v1(frame):
    parts = frame.split("|", maxsplit=4)
    if len(parts) != 5:
        raise QrTransferError("Not a BAL will QR (bad frame structure)")
    magic_seen, total_s, index_s, flags, payload = parts
    if magic_seen != _FRAME_MAGIC_V1:
        raise QrTransferError("Not a BAL will QR (unknown magic/version)")
    try:
        total = int(total_s)
        index = int(index_s)
    except ValueError as e:
        raise QrTransferError("Not a BAL will QR (bad frame numbers)") from e
    if total < 1 or not 1 <= index <= total:
        raise QrTransferError("Not a BAL will QR (frame numbering out of range)")
    if flags not in ("", FLAG_COMPRESSED):
        raise QrTransferError("Not a BAL will QR (unknown flags)")
    return total, index, flags == FLAG_COMPRESSED, payload


def _parse_v2(frame):
    if len(frame) < _HEADER_V2_LEN:
        raise QrTransferError("Not a BAL will QR (bad frame structure)")
    # Magic is length _FRAME_MAGIC_V2; the two base36 fields and the flag
    # make up the rest of the fixed header.
    offset = len(_FRAME_MAGIC_V2)
    total_s = frame[offset : offset + _BASE36_WIDTH]
    index_s = frame[offset + _BASE36_WIDTH : offset + 2 * _BASE36_WIDTH]
    flag = frame[offset + 2 * _BASE36_WIDTH]
    try:
        total = _base36_decode(total_s)
        index = _base36_decode(index_s)
    except ValueError:
        raise QrTransferError("Not a BAL will QR (bad frame numbers)") from None
    if total < 1 or not 1 <= index <= total:
        raise QrTransferError("Not a BAL will QR (frame numbering out of range)")
    if flag not in (FLAG_PLAIN, FLAG_COMPRESSED):
        raise QrTransferError("Not a BAL will QR (unknown flags)")
    payload = frame[_HEADER_V2_LEN:]
    return total, index, flag == FLAG_COMPRESSED, payload


def __compute_total(transfer_len, chunk_size):
    """Smallest frame count whose budget covers the whole transfer string.

    The v2 header is fixed-width, so the budget is constant and the count is
    a plain ceiling division, capped at :data:`_MAX_TOTAL`.
    """
    if chunk_size < MIN_CHUNK_SIZE:
        raise QrTransferError(
            "chunk size too small to hold a BAL QR frame: {}".format(chunk_size)
        )
    budget = chunk_size - _HEADER_V2_LEN
    if budget <= 0:
        raise QrTransferError(
            "chunk size too small for the BAL QR frame header: {}".format(chunk_size)
        )
    total = -(-transfer_len // budget)
    if total < 1:
        total = 1
    if total > _MAX_TOTAL:
        raise QrTransferError(
            "BAL QR transfer demands too many frames: {}".format(total)
        )
    return total
