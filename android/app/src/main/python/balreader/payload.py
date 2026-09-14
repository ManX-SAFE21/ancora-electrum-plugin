"""Will-payload autodetection for the BAL Reader app.

This file is a verbatim copy of ``decode_will_payload`` from
``bal/gui/qt/dialogs.py``.  ``android/test_chain/verify_chain.py`` compares the
two functions result-for-result so they can never drift apart.

Keep the function body identical to the plugin source.
"""

import json
import re
from typing import Any


def decode_will_payload(text) -> tuple[Any, Any]:
    """Autodetect: whole-will JSON or transaction list?

    Returns ``("will", dict_of_willitems_data)`` when ``text`` is a JSON
    object whose values are dicts containing a ``"tx"`` key (the whole-will
    format produced by :meth:`BalWindow.export_json_file` and friends).
    Otherwise returns ``("txs", [tx_strings])`` where the transaction
    strings were split on commas and/or newlines.
    """
    text = text.strip()
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        data = None
    if isinstance(data, dict) and data:
        if all(isinstance(v, dict) and "tx" in v for v in data.values()):
            return ("will", data)
    parts = [p for p in re.split(r"[,\r\n]+", text) if p.strip()]
    return ("txs", parts)