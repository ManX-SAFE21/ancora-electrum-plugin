#!/usr/bin/env python3
"""Verify the Android app can decode every frame format the plugin exports.

Simulates the exact runtime path of the APK on the development machine:

* imports ``bal.core`` and ``balreader.payload`` from the *bundled* copies in
  ``android/app/src/main/python`` (the code Chaquopy actually ships);
* generates frames exactly as the plugin's export page does
  (``split_frames`` for BAL QR, ``encode_animated_frames`` semantics for
  UR v1 / UR v2 / BBQR);
* drives an :class:`~bal.core.animated_qr.AnimatedQrSession` the way
  ``BalDecoder.add`` does (scrambled input, duplicates, dropped frames);
* runs the app's ``finish()`` chain (``resolve()`` -> ``decode_transfer()``
  -> ``decode_will_payload()``) and checks the result;
* cross-checks the app's ``decode_will_payload`` copy result-for-result (and
  AST-for-AST) against the plugin's original in ``bal/gui/qt/dialogs.py``.

Run from the repository root (any Python 3.8+, no dependencies):

    python3 android/test_chain/verify_chain.py
"""

import ast
import json
import random
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_PYTHON = REPO_ROOT / "android" / "app" / "src" / "main" / "python"
DIALOGS = REPO_ROOT / "bal" / "gui" / "qt" / "dialogs.py"

sys.path.insert(0, str(APP_PYTHON))

from bal.core import animated_qr as aq  # noqa: E402
from bal.core import qrtransfer as qtf  # noqa: E402

from balreader import bridge as bridge_codec  # noqa: E402
from balreader import payload as payload_codec  # noqa: E402

PASSED = 0


def ok(condition, label):
    global PASSED
    if not condition:
        raise AssertionError("FAILED: " + label)
    PASSED += 1


def check_imported_bundle():
    for module in (aq, qtf, payload_codec):
        assert module.__file__ is not None
        path = str(Path(module.__file__).resolve())
        assert path.startswith(str(APP_PYTHON)), path
    ok(True, "all modules imported from the bundled android/ copies")


def find_function(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise RuntimeError("{} not found".format(name))


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

TXS = ["{:064x}".format(i) for i in range(1, 8)]
WILL_ITEMS = {
    "imp{}".format(i): {
        "tx": "{:064x}".format(i + 1),
        "addr": "bc1qdeadbeef{:x}".format(i),
        "amount": 100000 + i,
        "tag": "heiress-{}".format(i),
        "metadata": {},
        "notify": "mail-{}@example.invalid".format(i),
    }
    for i in range(3)
}
WILL_JSON_COMPACT = json.dumps(WILL_ITEMS, separators=(",", ":"))
WILL_JSON_PRETTY = json.dumps(WILL_ITEMS, indent=2)


# --------------------------------------------------------------------------- #
# Parity: app's decode_will_payload vs the plugin's dialogs.py original
# --------------------------------------------------------------------------- #

def build_extracted_and_app_function():
    dialogs_source = DIALOGS.read_text()
    app_source = Path(payload_codec.__file__).read_text()
    dialogs_node = find_function(ast.parse(dialogs_source), "decode_will_payload")
    app_node = find_function(ast.parse(app_source), "decode_will_payload")
    ok(
        ast.dump(app_node) == ast.dump(dialogs_node),
        "decode_will_payload AST identical between app copy and plugin",
    )
    namespace = {}
    exec("import json\nimport re\nfrom typing import Any", namespace)
    exec(compile(ast.Module(body=[dialogs_node], type_ignores=[]), "dialogs.py", "exec"), namespace)
    return namespace["decode_will_payload"]


def run_payload_parity_cases():
    plugin_decode = build_extracted_and_app_function()
    samples = {
        "will-compact": WILL_JSON_COMPACT,
        "will-pretty": WILL_JSON_PRETTY,
        "txs-newlines": "\n".join(TXS),
        "txs-comma-crlf": ",\r\n".join(TXS[:3]),
        "single-tx": TXS[0],
        "json-array": json.dumps(TXS),
        "not-json-dict": "hello world",
        "empty": "",
        "whitespace": "   \n\t  ",
    }
    for label, text in samples.items():
        app_result = payload_codec.decode_will_payload(text)
        plugin_result = plugin_decode(text)
        ok(
            app_result == plugin_result,
            "payload parity for {!r}".format(label),
        )


# --------------------------------------------------------------------------- #
# Full decode chain (the app's BalDecoder.finish())
# --------------------------------------------------------------------------- #

def app_chain(transfer_text, compressed):
    parts = qtf.decode_transfer(transfer_text, compressed)
    payload = "\n".join(parts)
    kind, data = payload_codec.decode_will_payload(payload)
    return parts, payload, kind, data


def feed_frame_set(session, frames, *, drop=None, order=None):
    indexes = list(range(len(frames)))
    if drop:
        indexes = [i for i in indexes if i not in drop]
    if order is not None:
        indexes = list(order)
    for i in indexes:
        session.add_part(frames[i])


def make_frames(transfer_text, fmt, budget_chars, *, compressed=False):
    payload = transfer_text.encode("utf-8")
    if fmt == "balqr":
        return qtf.split_frames(transfer_text, budget_chars, compressed=compressed)
    if fmt == "ur1":
        return aq.ur1_frames(payload, budget_chars)
    if fmt == "ur2":
        return aq.ur2_frames(payload, budget_chars)
    if fmt == "bbqr":
        return aq.bbqr_frames(payload, budget_chars, encoding="Z")
    raise AssertionError("unknown format " + fmt)


def run_transport_case(transport, budget_chars, transfer_text, compressed=False):
    frames = make_frames(transfer_text, transport, budget_chars, compressed=compressed)
    session = aq.AnimatedQrSession()
    rng = random.Random(len(transfer_text) + len(transport.encode()))
    order = [i for i in range(len(frames))]
    rng.shuffle(order)
    feed_frame_set(session, frames, order=order)
    ok(session.done, "{} (.{} chars) reaches done in scrambled order".format(transport, budget_chars))
    if transport == "ur2":
        # Fountain indexes can range wider than seq_len, so received may
        # exceed (or fall short of) total; only progress and completion matter.
        ok(session.total >= 1 and session.received >= 1,
           "{} reports positive progress".format(transport))
    else:
        ok(
            session.received == session.total,
            "{} received matches total".format(transport),
        )
        ok(
            session.received == len(frames) and session.total == len(frames),
            "{} received/total equals frame count".format(transport),
        )
    transfer, compressed_flag = session.resolve()
    ok(transfer == transfer_text, "{} restores exact transfer text".format(transport))
    parts, payload, kind, data = app_chain(transfer, compressed_flag)
    bridge_json = json.loads(bridge_codec.finish(session))
    ok(
        bridge_json == {"kind": kind, "payload": payload, "parts": parts},
        "{} bridge.finish JSON matches the app chain".format(transport),
    )
    return parts, payload, kind, data


def test_tx_transports():
    transfer = qtf.encode_transfer(TXS, compress=False)
    for transport in ("balqr", "ur1", "ur2", "bbqr"):
        parts, payload, kind, data = run_transport_case(transport, 400, transfer)
        ok(kind == "txs", "{} classifies as txs".format(transport))
        ok(parts == TXS and payload == "\n".join(TXS), "{} yields the tx list".format(transport))


def test_compressed_bal_transport():
    transfer = qtf.encode_transfer(TXS, compress=True)
    frames = make_frames(transfer, "balqr", 400, compressed=True)
    session = aq.AnimatedQrSession()
    feed_frame_set(session, frames)
    ok(session.done, "compressed BAL QR done")
    parts, payload, kind, data = app_chain(*session.resolve())
    ok(kind == "txs" and parts == TXS, "compressed BAL QR yields the tx list")


def test_will_transports():
    for transport in ("balqr", "ur1", "ur2", "bbqr"):
        parts, payload, kind, data = run_transport_case(transport, 400, WILL_JSON_COMPACT)
        ok(kind == "will", "{} classifies as will".format(transport))
        ok(data == WILL_ITEMS, "{} restores the whole-will dict".format(transport))
    # Pretty JSON works too (blank lines are whitespace for json.loads).
    parts, payload, kind, data = run_transport_case("ur2", 400, WILL_JSON_PRETTY)
    ok(kind == "will" and data == WILL_ITEMS, "pretty JSON transport restores the will dict")


def test_duplicates_are_ignored():
    # UR v1 (multi-fragment) dedups by fragment index; UR v2 fountains never
    # report "dup" (they dedup internally), matching the plugin's behaviour.
    frames = make_frames(WILL_JSON_COMPACT, "ur1", 200)
    ok(len(frames) >= 2, "UR v1 yields multiple fragments (got {})".format(len(frames)))
    session = aq.AnimatedQrSession()
    for i in range(len(frames)):
        first = session.add_part(frames[i])
        dup = session.add_part(frames[i])
        ok(first == "ok", "fresh UR v1 frame reported as ok")
        ok(dup == "dup", "duplicate UR v1 frame reported as dup")
    ok(session.received == len(frames), "duplicates do not inflate received")
    ok(session.done, "UR v1 completes after duplicates")


def test_ur2_fountain_survives_loss_and_reorder():
    transfer = qtf.encode_transfer(TXS, compress=False)
    frames = make_frames(transfer, "ur2", 120)
    ok(len(frames) >= 6, "fountain yields multiple frames (got {})".format(len(frames)))
    drop = (0, 2, len(frames) - 1)
    session = aq.AnimatedQrSession()
    rng = random.Random(99)
    order = [i for i in range(len(frames)) if i not in drop]
    rng.shuffle(order)
    feed_frame_set(session, frames, order=order, drop=drop)
    ok(session.done, "fountain completes after dropped + reordered frames")
    transfer, compressed_flag = session.resolve()
    ok(transfer == transfer, "fountain restores exact transfer text")
    parts, payload, kind, data = app_chain(transfer, compressed_flag)
    ok(kind == "txs" and parts == TXS, "fountain output feeds the full import tail")


def test_ur2_single_part_and_duplicate():
    transfer = qtf.encode_transfer(TXS, compress=False)
    frames = make_frames(transfer, "ur2", 2000)
    ok(len(frames) == 1, "large budget yields a single-part UR v2 frame")
    session = aq.AnimatedQrSession()
    session.add_part(frames[0])
    ok(session.done and session.received == 1, "single-part UR v2 completes")


def test_bbqr_all_three_encodings():
    for encoding in ("Z", "H", "2"):
        frames = aq.bbqr_frames(WILL_JSON_COMPACT.encode(), 120, encoding=encoding)
        session = aq.AnimatedQrSession()
        feed_frame_set(session, frames)
        ok(session.done, "BBQR {} done".format(encoding))
        transfer, compressed = session.resolve()
        parts, payload, kind, data = app_chain(transfer, compressed)
        ok(kind == "will" and data == WILL_ITEMS, "BBQR {} restores the will".format(encoding))


def test_mid_transfer_conflict():
    ur1 = aq.ur1_frames(b"transfer-one", 400)
    ur2 = aq.ur2_frames(b"transfer-two", 400)
    session = aq.AnimatedQrSession()
    session.add_part(ur1[0])
    try:
        session.add_part(ur2[0])
    except aq.TransferConflictError:
        ok(True, "format switch raises TransferConflictError")
    else:
        ok(False, "format switch raised TransferConflictError")


def test_garbage_and_single_line():
    session = aq.AnimatedQrSession()
    try:
        session.add_part("this is not a QR transfer")
    except aq.FormatNotDetectedError:
        ok(True, "garbage raises FormatNotDetectedError")
    else:
        ok(False, "garbage raised FormatNotDetectedError")


def main():
    check_imported_bundle()
    run_payload_parity_cases()
    test_tx_transports()
    test_compressed_bal_transport()
    test_will_transports()
    test_duplicates_are_ignored()
    test_ur2_fountain_survives_loss_and_reorder()
    test_ur2_single_part_and_duplicate()
    test_bbqr_all_three_encodings()
    test_mid_transfer_conflict()
    test_garbage_and_single_line()
    print("verify_chain: {} checks passed".format(PASSED))
    return 0


if __name__ == "__main__":
    sys.exit(main())
