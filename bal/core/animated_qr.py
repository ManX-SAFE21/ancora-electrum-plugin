"""
bal.core.animated_qr
====================

GUI-free implementation of the BBQR animated-QR transfer format used to
move BAL will data between devices.

Supported wire format (self-describing and order-independent on receive):

* **BBQR** (Coinkite)::
      B$<encoding><type><2 base36 total><2 base36 index><payload>
    Equal-length text frames; the payload is uppercase hex, RFC-4648
    base32, or raw-deflate (``wbits=-10``) zlib plus base32.

Everything is implemented from scratch on top of the Python standard library
only (``zlib``, ``base64``), so the shipped plugin zip stays a
self-contained bundle with no third-party dependencies (house rule).

This module never imports Qt or any Electrum GUI code (house rule).
"""

from __future__ import annotations

import base64
import zlib
from typing import Dict, List, Optional, Sequence, Tuple

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


# Safety caps for untrusted scanner input.
_MAX_SESSION_PARTS = 20000
_MAX_MESSAGE_BYTES = 32 * 1024 * 1024

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
    if text.startswith(_BBQR_PREFIX):
        return "bbqr"
    return None


def parse_for_detection(text: str) -> Tuple[str, str, int, int]:
    """Parse a frame and return ``(format, session_key, frame_total, index)``.

    ``session_key`` identifies the transfer the frame belongs to and drives
    the shared reset/ignore/accept debounce. Raises
    :class:`AnimatedQrError` when the text cannot be parsed.
    """
    fmt = detect_format(text)
    if fmt == "bbqr":
        encoding, type_code, total, index, _payload = bbqr_parse_part(text)
        return "bbqr", "bbqr:{}{}:{}".format(encoding, type_code, total), total, index
    raise FormatNotDetectedError("Not a supported QR transfer format")


# --------------------------------------------------------------------------- #
# Receive session (order-independent assembly)
# --------------------------------------------------------------------------- #

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
