"""
bal.core.qrtransfer
===================

GUI-free helpers for moving BAL will data between devices via QR codes or
the Electrum ``audio_modem`` plugin (see ``PLAN_QR_TRANSFER.md``).

Scope
-----
* converts will transactions into a compact ``transfer_string``
  (newline-joined serialized transactions, optionally zlib + base64
  compressed). The transfer string is the payload carried by BBQR QR frames
  (``bal.core.animated_qr``).

The audio-modem channel carries the whole transfer string in a single blob
(PLAN_QR_TRANSFER.md section 4.4), so callers only use
:func:`encode_transfer` / :func:`decode_transfer`.

This module never imports Qt or any Electrum GUI code (house rule).
"""

from __future__ import annotations

import base64
import zlib

# 4 standard presets (label, payload budget per QR). Ordered from
# low-resolution cameras to high-resolution cameras (owner decision D5).
CHUNK_PRESETS = (
    ("Small - ~150 bytes/QR (low-res cameras)", 150),
    ("Medium - ~400 bytes/QR", 400),
    ("Large - ~900 bytes/QR", 900),
    ("XL - ~1800 bytes/QR (high-res cameras)", 1800),
)


def encode_transfer(tx_strings, compress=False):
    """Join serialized transaction strings into a transfer string.

    ``compress=True`` wraps the joined text in zlib + base64 (ASCII-safe) so
    the whole bundle shrinks before being printed/scanned.
    """
    return __compress("\n".join(tx_strings), enabled=compress)


def decode_transfer(transfer_string, compressed):
    """Inverse of :func:`encode_transfer`.

    Returns the list of serialized transaction strings; empty frames are
    dropped so a trailing newline (or an empty payload) cannot produce an
    empty trailing element.
    """
    text = __decompress(transfer_string, enabled=compressed)
    return [part for part in text.split("\n") if part]


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
