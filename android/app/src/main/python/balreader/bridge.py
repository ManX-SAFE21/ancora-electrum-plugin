"""Kotlin-facing helper: runs the plugin's exact import tail and returns JSON.

A serializable JSON contract keeps the Chaquopy bridge tiny on the Kotlin side
and avoids exposing ``PyObject`` tuple/container indexing to it.  The steps are
the same three calls the plugin's import dialog performs:

    session.resolve()          -> (transfer_text, compressed)
    qrtransfer.decode_transfer -> parts
    balreader.payload.decode_will_payload -> ("will"|"txs", data)
"""

import json

from bal.core import qrtransfer as _qrtransfer
from balreader import payload as _payload


def finish(session):
    """Run the import tail on a live ``AnimatedQrSession``.

    Returns a JSON string ``{"kind": ..., "payload": ..., "parts": [...]}``
    with ``kind`` either ``"will"`` or ``"txs"``.  On any failure it returns
    ``{"kind": "error", "payload": <message>, "parts": []}`` so a misbehaving
    session can never crash the UI thread.
    """
    try:
        transfer, compressed = session.resolve()
        parts = list(_qrtransfer.decode_transfer(transfer, compressed))
        payload = "\n".join(parts)
        kind, _data = _payload.decode_will_payload(payload)
        return json.dumps({"kind": kind, "payload": payload, "parts": parts})
    except Exception as exc:  # noqa: BLE001 - defensive bridge boundary
        return json.dumps({"kind": "error", "payload": str(exc), "parts": []})