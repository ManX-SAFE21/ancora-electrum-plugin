"""
Tests for the QR / audio will-transfer dialogs (``bal.gui.qt.dialogs``).

Covers the unified ``WillExportDialog`` (transport radios, stacked pages,
QR build/navigation, chunk-size / autoplay, filter revert) and the unified
``WillImportDialog`` (QR frame capture, slot grid, complete-review enabling,
total mismatch reset, frame assembly -> decode). The wizard and the
camera/audio paths need a live wallet/hardware and are exercised only
through the shared frame-assembly path here.

Run:
    QT_QPA_PLATFORM=offscreen python3 tests/test_gui_qr_transfer.py
"""

import base64
import json
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, __file__.rsplit("/", 2)[0])

from electrum.transaction import Transaction
from PyQt6.QtWidgets import QApplication, QMainWindow

import bal.gui.qt.dialogs as dialogs
from bal.core.qrtransfer import CHUNK_PRESETS, encode_transfer, split_frames
from bal.core.will import WillItem

_app = QApplication.instance() or QApplication(sys.argv)

# A valid 1x1 transparent PNG, good enough for BalDialog's window icon.
_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

# A valid serialized Bitcoin transaction hex (1 input + 1 P2PKH output),
# reused for the WillItem status regression test.
_VALID_TX_HEX = (
    "01000000012a5c9a94fcde98f5581cd00162c60a13936ceb75389ea65b"
    "f38633b424eb4031000000006c493046022100a82bbc57a0136751e543"
    "3f41cf000b3f1a99c6744775e76ec764fb78c54ee100022100f9e80b7d"
    "e89de861dc6fb0c1429d5da72c2b6b2ee2406bc9bfb1beedd729d98501"
    "2102e61d176da16edd1d258a200ad9759ef63adf8e14cd97f53227bae3"
    "5cdb84d2f6ffffffff0140420f00000000001976a914230ac37834073a"
    "42146f11ef8414ae929feaafc388ac00000000"
)


class _Cfg:
    def __init__(self, value):
        self._value = value

    def get(self):
        return self._value


class FakePlugin:
    # Smallest QR preset: long transfers produce several frames.
    QR_CHUNK_SIZE = _Cfg(CHUNK_PRESETS[0][1])

    def read_file(self, path):
        return _PNG_BYTES


class FakeWindow(QMainWindow):
    config = {}

    def format_amount(self, amount):
        return "{:.8f}".format(amount)

    def format_amount_and_units(self, amount):
        return "{:.8f} sat".format(amount)


class FakeBalWindow:
    """Duck-typed stand-in for BalWindow (dialog layer only)."""

    def __init__(self):
        self.window = FakeWindow()
        self.bal_plugin = FakePlugin()
        self.willitems = {}

    def get_audio_modem_plugin(self):
        return None


class StubTx:
    def __init__(self, payload):
        self.payload = payload

    def txid(self):
        return "{:064x}".format(hash(self.payload) & 0xFFFFFFFFFFFFFFFF)

    def __str__(self):
        return self.payload


class StubWillItem:
    def __init__(self, payload, statuses=None):
        self.tx = StubTx(payload)
        self.statuses = statuses or {}

    def get_status(self, name):
        return self.statuses.get(name, False)

    def to_dict(self):
        return {"tx": str(self.tx), "status": self.statuses}


def _make_willitems(n=3, payload_len=120, payloads=None):
    if payloads is None:
        payloads = ["T{}".format(i) * payload_len for i in range(n)]
    return {
        "item{}".format(i): StubWillItem(p)
        for i, p in enumerate(payloads)
    }


# ------------------------------------------------------------------ #
# WillExportDialog (QR transport via d.qr_page)
# ------------------------------------------------------------------ #

def test_export_dialog_builds():
    bw = FakeBalWindow()
    bw.willitems = _make_willitems()
    d = dialogs.WillExportDialog(bw, bal_plugin=bw.bal_plugin, initial_mode="qr")
    page = d.qr_page
    assert d.transport_qr.isChecked()
    assert page.tx_strings
    assert page.frames
    assert len(page.frames) >= 1
    # Frame 1 is shown.
    assert page.qr_view.text == page.frames[0]
    assert "1" in page.progress_label.text()
    d.close()


def test_export_dialog_unified_transports():
    # One window hosts the three transports as stacked, radio-selected pages.
    bw = FakeBalWindow()
    bw.willitems = _make_willitems()
    d = dialogs.WillExportDialog(bw, bal_plugin=bw.bal_plugin)
    assert d.stacked.count() == 3
    assert d.transport_file.isChecked()
    assert d.stacked.currentWidget() is d.file_page
    d._on_mode_clicked(d.MODE_QR)
    assert d.stacked.currentWidget() is d.qr_page
    d._on_mode_clicked(d.MODE_AUDIO)
    assert d.stacked.currentWidget() is d.audio_page
    d.close()


def test_import_dialog_qr_page_has_no_audio():
    # Audio lives on the import dialog's own audio page, never in the QR page.
    bw = FakeBalWindow()
    d = dialogs.WillImportDialog(bw, bal_plugin=bw.bal_plugin)
    page = d.qr_page
    assert not hasattr(page, "_audio_receive")
    texts = [b.text() for b in page.findChildren(dialogs.QPushButton)]
    assert not any("Audio" in t for t in texts)
    assert d.receive_btn is not None
    d.close()


def test_export_dialog_empty_close():
    # An empty will shows a modal message and no widgets are built; stub the
    # message out for the test.
    orig = dialogs.MessageBoxMixin.show_message
    dialogs.MessageBoxMixin.show_message = lambda self, msg, icon=None: None
    try:
        bw = FakeBalWindow()
        d = dialogs.WillExportDialog(bw, bal_plugin=bw.bal_plugin)
        assert not d.isVisible()
        assert not hasattr(d, "qr_page")
        d.close()
    finally:
        dialogs.MessageBoxMixin.show_message = orig


def test_imported_item_status_not_none():
    # Regression: a WillItem built from a bare {"tx": hex} had a None status,
    # so set_status (e.g. IMPORTED / INVALIDATED from the import validity
    # pass) crashed with "unsupported operand type(s) for +=: 'NoneType' and
    # 'str'".
    with patch.object(Transaction, "add_info_from_wallet"):
        wi = WillItem({"tx": _VALID_TX_HEX}, wallet=None)
        assert wi.status == ""
        assert wi.set_status("IMPORTED", True) is True
        assert wi.set_status("INVALIDATED", True) is True
        assert "Imported" in wi.status and "Invalidated" in wi.status


def test_export_auto_scroll():
    bw = FakeBalWindow()
    bw.willitems = _make_willitems(n=6, payload_len=400)
    d = dialogs.WillExportDialog(bw, bal_plugin=bw.bal_plugin, initial_mode="qr")
    page = d.qr_page
    assert page.fps_spin is not None
    assert not page.auto_timer.isActive()

    page.fps_spin.setValue(2)
    page._toggle_auto()
    assert page.auto_timer.isActive()
    assert page.auto_btn.text() == dialogs._("Stop")
    page._auto_step()
    assert page.index == 1
    page._toggle_auto()
    assert not page.auto_timer.isActive()
    assert page.auto_btn.text() == dialogs._("Auto")

    # Advancing past the last frame stops the slideshow automatically.
    page._toggle_auto()
    page.index = len(page.frames) - 1
    page._auto_step()
    assert not page.auto_timer.isActive()
    d.close()


def test_export_auto_scroll_loop():
    bw = FakeBalWindow()
    bw.willitems = _make_willitems(n=6, payload_len=400)
    d = dialogs.WillExportDialog(bw, bal_plugin=bw.bal_plugin, initial_mode="qr")
    page = d.qr_page
    assert page.loop_check is not None

    # Loop on: reaching the last code wraps back to the first and keeps going.
    page.loop_check.setChecked(True)
    page._toggle_auto()
    assert page.auto_timer.isActive()
    page.index = len(page.frames) - 1
    page._auto_step()
    assert page.index == 0
    assert page.auto_timer.isActive()
    page._toggle_auto()

    # Loop off: reaching the last code stops the slideshow.
    page.loop_check.setChecked(False)
    page._toggle_auto()
    page.index = len(page.frames) - 1
    page._auto_step()
    assert not page.auto_timer.isActive()
    d.close()


def test_export_filter_valid_and_valid_nc():
    bw = FakeBalWindow()
    a = StubWillItem("A" * 120, statuses={"VALID": True, "COMPLETE": True})
    b = StubWillItem("B" * 120, statuses={"VALID": True})
    c = StubWillItem("C" * 120)
    bw.willitems = {"a": a, "b": b, "c": c}
    d = dialogs.WillExportDialog(bw, bal_plugin=bw.bal_plugin, initial_mode="qr")
    page = d.qr_page
    assert d.filter_combo.count() == 3
    # Whole-will default: a single JSON document carrying all selected items.
    assert d.content_check.isChecked()
    assert len(page.tx_strings) == 1
    data = json.loads(page.tx_strings[0])
    assert set(data) == {"a", "b", "c"}

    # Switch to tx-only content, then exercise the filters.
    d.content_check.setChecked(False)
    assert len(page.tx_strings) == 3

    # "Valid" filter -> only the valid items (a, b).
    d._on_filter_change(1)
    assert sorted(page.tx_strings) == ["A" * 120, "B" * 120]

    # "Valid NC" filter -> only the valid, not-complete item (b).
    d._on_filter_change(2)
    assert sorted(page.tx_strings) == ["B" * 120]
    assert page.qr_view.text == page.frames[0]
    d.close()


def test_export_filter_empty_reverts():
    bw = FakeBalWindow()
    # Only a COMPLETE valid item: "Valid NC" selects nothing -> revert.
    bw.willitems = {
        "a": StubWillItem("A" * 120, statuses={"VALID": True, "COMPLETE": True})
    }
    d = dialogs.WillExportDialog(bw, bal_plugin=bw.bal_plugin, initial_mode="qr")
    messages = []
    d.show_message = lambda msg: messages.append(msg)  # type: ignore[assignment]
    d._on_filter_change(2)  # "Valid NC" -> empty subset
    assert messages
    assert d._filter_index == 0  # reverted to "All"
    assert d.filter_combo.currentIndex() == 0
    assert len(d.qr_page.tx_strings) == 1
    d.close()


def test_export_navigation_and_chunk_change():
    # Low-redundancy serialized transactions resist deflate, so even the
    # compressed best-of transfer still needs several frames at the default
    # chunk and navigation across frames is exercised.
    def noisy(pad):
        return "".join("{:02x}".format((pad * 31 + j * 101 + j * j) % 256) for j in range(200))

    bw = FakeBalWindow()
    bw.willitems = _make_willitems(
        n=6, payload_len=400,
        payloads=["{}0{}".format(noisy(i), "T" * 50) for i in range(6)],
    )
    d = dialogs.WillExportDialog(bw, bal_plugin=bw.bal_plugin, initial_mode="qr")
    page = d.qr_page
    first_count = len(page.frames)
    assert first_count > 1  # long transfer, small default chunk

    assert not page.prev_btn.isEnabled()
    page._next()
    assert page.index == 1
    assert page.qr_view.text == page.frames[1]
    assert page.prev_btn.isEnabled()
    page._prev()
    assert page.index == 0
    assert page.qr_view.text == page.frames[0]

    # Switch to the largest preset: fewer, bigger frames.
    page._on_chunk_change(len(dialogs.CHUNK_PRESETS) - 1)
    assert len(page.frames) < first_count
    assert page.index == 0
    d.close()


# ------------------------------------------------------------------ #
# WillImportDialog (QR transport via d.qr_page)
# ------------------------------------------------------------------ #

def test_import_frame_flow():
    bw = FakeBalWindow()
    d = dialogs.WillImportDialog(bw, bal_plugin=bw.bal_plugin)
    page = d.qr_page
    assert not page.review_btn.isEnabled()
    assert not page.slot_area.isVisible()

    transfer = encode_transfer(["A" * 120, "B" * 120, "C" * 120])
    frames = split_frames(transfer, 150)
    assert len(frames) > 1

    for frame in frames:
        page._add_frame(frame)
    assert page.total == len(frames)
    assert page.review_btn.isEnabled()
    # isVisible() needs a shown parent; assert the widget is not hidden instead.
    assert not page.slot_area.isHidden()
    assert len(page.slot_widgets) == page.total
    assert "All" in page.status_label.text()
    # Duplicate capture is harmless.
    page._add_frame(frames[0])
    assert len(page.frames) == page.total
    d.close()


def test_import_assembles_and_decodes():
    bw = FakeBalWindow()
    d = dialogs.WillImportDialog(bw, bal_plugin=bw.bal_plugin)
    page = d.qr_page
    captured = {}

    def fake_finish(payload):
        captured["payload"] = payload

    page._finish_import = fake_finish
    transfer = encode_transfer(["P" * 130, "Q" * 130])
    for frame in split_frames(transfer, 150):
        page._add_frame(frame)
    page._review_and_sign()
    # The payload is rejoined into an opaque string for autodetect.
    assert captured["payload"].split("\n") == ["P" * 130, "Q" * 130]
    d.close()


def test_decode_will_payload_autodetect():
    # Whole-will JSON is recognized as a will document.
    payload = json.dumps({"item1": {"tx": "AAAA", "status": {"VALID": True}}})
    kind, data = dialogs.decode_will_payload(payload)
    assert kind == "will"
    assert data["item1"]["tx"] == "AAAA"

    # A singleton dict whose value is not an item dict falls back to txs.
    kind, data = dialogs.decode_will_payload('{"foo": 1}')
    assert kind == "txs"

    # Comma and/or newline separated transactions.
    kind, data = dialogs.decode_will_payload("AAAA,BBBB\nCCCC")
    assert kind == "txs"
    assert data == ["AAAA", "BBBB", "CCCC"]

    # A single transaction with no separators.
    kind, data = dialogs.decode_will_payload("HEXHEX")
    assert kind == "txs"
    assert data == ["HEXHEX"]


def test_whole_will_qr_roundtrip():
    # "Whole will" produces a single JSON document that survives a full
    # QR encode -> frame capture -> assemble -> decode cycle.
    bw = FakeBalWindow()
    items = _make_willitems(2)
    bw.willitems = items
    d = dialogs.WillExportDialog(bw, bal_plugin=bw.bal_plugin, initial_mode="qr")
    page = d.qr_page
    assert d.content_check.isChecked()
    assert len(page.tx_strings) == 1

    # Rebuild the transfer from the dialog's own strings, as the importer does.
    transfer = encode_transfer(page.tx_strings)
    impl = dialogs.WillImportDialog(bw, bal_plugin=bw.bal_plugin)
    import_page = impl.qr_page
    for frame in split_frames(transfer, dialogs.CHUNK_PRESETS[0][1]):
        import_page._add_frame(frame)
    assert import_page.review_btn.isEnabled()
    caught = {}

    def fake_finish(payload):
        caught["payload"] = payload

    import_page._finish_import = fake_finish
    import_page._review_and_sign()
    kind, data = dialogs.decode_will_payload(caught["payload"])
    assert kind == "will"
    assert set(data) == {"item0", "item1"}
    d.close()
    impl.close()


def test_import_total_mismatch_resets():
    bw = FakeBalWindow()
    d = dialogs.WillImportDialog(bw, bal_plugin=bw.bal_plugin)
    page = d.qr_page
    warnings = []
    page.show_warning = lambda msg: warnings.append(msg)  # type: ignore[assignment]

    frames_a = split_frames(encode_transfer(["A" * 120, "B" * 120]), 150)
    frames_b = split_frames(encode_transfer(["A" * 120, "B" * 120, "C" * 120]), 150)
    for frame in frames_a:
        page._add_frame(frame)
    assert page.total == len(frames_a)

    # A frame with a different total wipes the import; the first frame of
    # the new transfer must be scanned afresh.
    page._add_frame(frames_b[0])
    assert warnings
    assert page.total == 0
    assert not page.frames
    assert not page.review_btn.isEnabled()
    d.close()


def test_import_manual_entry():
    bw = FakeBalWindow()
    d = dialogs.WillImportDialog(bw, bal_plugin=bw.bal_plugin)
    page = d.qr_page
    frames = split_frames(encode_transfer(["M" * 120]), 150)
    page.manual_edit.setText(frames[0])
    page._add_from_manual()
    assert page.manual_edit.text() == ""
    assert page.total == 1
    assert page.review_btn.isEnabled()
    d.close()


# ------------------------------------------------------------------ #
# Continuous camera scan (change/detection debounce + auto-finish)
# ------------------------------------------------------------------ #

def _fresh_debounce():
    return {
        "last_index": None,
        "last_payload": None,
        "pending_index": None,
        "pending_payload": None,
        "pending_count": 0,
    }


def test_qr_import_debounce_pending_then_accept():
    s = _fresh_debounce()
    # First sighting of a new identity: pending, not yet stored.
    assert dialogs.qr_import_accept_frame(s, "balqr", "balqr:2", 2, 1, "PAYLOAD1") == "pending"
    assert s["pending_count"] == 1
    assert s["last_index"] is None
    # A second stable read of the same identity: accepted.
    assert dialogs.qr_import_accept_frame(s, "balqr", "balqr:2", 2, 1, "PAYLOAD1") == "accept"
    assert s["last_index"] == 1
    assert s["last_payload"] == "PAYLOAD1"
    assert s["pending_count"] == 0


def test_qr_import_debounce_re_reading_last_is_ignored():
    s = _fresh_debounce()
    dialogs.qr_import_accept_frame(s, "balqr", "balqr:2", 2, 1, "P1")
    dialogs.qr_import_accept_frame(s, "balqr", "balqr:2", 2, 1, "P1")  # accepted
    # The exporter is still showing frame 1: must be ignored, not accepted.
    assert dialogs.qr_import_accept_frame(s, "balqr", "balqr:2", 2, 1, "P1") == "ignore"
    assert s["last_index"] == 1
    assert s["pending_count"] == 0


def test_qr_import_debounce_transition_pending_resets():
    s = _fresh_debounce()
    dialogs.qr_import_accept_frame(s, "balqr", "balqr:2", 2, 1, "P1")
    dialogs.qr_import_accept_frame(s, "balqr", "balqr:2", 2, 1, "P1")  # accept frame 1
    # A new identity interrupts the pending accumulation.
    assert dialogs.qr_import_accept_frame(s, "balqr", "balqr:2", 2, 2, "P2") == "pending"
    assert dialogs.qr_import_accept_frame(s, "balqr", "balqr:2", 2, 2, "P2") == "accept"
    # Same-index duplicate with different payload is treated as new identity.
    assert dialogs.qr_import_accept_frame(s, "balqr", "balqr:2", 2, 2, "P2") == "ignore"


def test_qr_import_debounce_total_mismatch_resets():
    s = _fresh_debounce()
    # In-range frame is accepted even though its declared total is ignored.
    assert dialogs.qr_import_accept_frame(s, "balqr", "balqr:3", 3, 2, "P2") == "pending"
    assert dialogs.qr_import_accept_frame(s, "balqr", "balqr:3", 3, 2, "P2") == "accept"
    # A frame that belongs to a different transfer.
    assert dialogs.qr_import_accept_frame(s, "balqr", "balqr:4", 4, 2, "P2B") == "reset"
    # Once the policy is rebased on the new transfer, frames resume normally
    # (the widget clears the debounce while wiping the import).
    s["key"] = None
    assert dialogs.qr_import_accept_frame(s, "balqr", "balqr:4", 4, 2, "P2B") == "pending"


def test_qr_import_handle_scanned_text_autofinish():
    bw = FakeBalWindow()
    d = dialogs.WillImportDialog(bw, bal_plugin=bw.bal_plugin)
    page = d.qr_page
    reviewed = []
    page._review_and_sign = lambda: reviewed.append(True)  # type: ignore[assignment]

    frames = split_frames(encode_transfer(["A" * 120, "B" * 120]), 150)
    assert len(frames) > 1

    # The camera session is running and every frame needs two stable reads.
    page._scanning = True
    for frame in frames:
        for _rep in range(2):
            page._handle_scanned_text(frame)
    assert page.total == len(frames)
    assert len(page.frames) == len(frames)
    assert page.review_btn.isEnabled()

    # With all frames stored, the loop auto-finishes exactly once.
    _app.processEvents()
    assert reviewed == [True]
    # The camera loop was stopped before handing over to the review step.
    assert not page._scanning
    assert not page._scan_timer.isActive()
    d.close()


def test_qr_import_handle_scanned_text_manual_does_not_autofinish():
    # Without a camera session running, extra frames never auto-proceed.
    bw = FakeBalWindow()
    d = dialogs.WillImportDialog(bw, bal_plugin=bw.bal_plugin)
    page = d.qr_page
    reviewed = []
    page._review_and_sign = lambda: reviewed.append(True)  # type: ignore[assignment]

    frames = split_frames(encode_transfer(["A" * 120, "B" * 120]), 150)
    assert len(frames) > 1
    assert not page._scanning
    for frame in frames:
        for _rep in range(2):
            page._handle_scanned_text(frame)
    assert len(page.frames) == len(frames)
    _app.processEvents()
    assert reviewed == []
    d.close()


# ------------------------------------------------------------------ #
# Animated-QR formats (BC-UR v1/v2, BBQR) via the export/import pages
# ------------------------------------------------------------------ #

def test_export_format_combo_switches_codecs():
    bw = FakeBalWindow()
    bw.willitems = _make_willitems(n=6, payload_len=400)
    d = dialogs.WillExportDialog(bw, bal_plugin=bw.bal_plugin, initial_mode="qr")
    page = d.qr_page
    assert page.format == "balqr"
    assert page.frames[0].startswith("BAL1")
    assert page.format_combo.count() == 4

    page._on_format_change(1)  # BC-UR v1
    assert page.format == "ur1"
    assert page.frames[0].startswith("ur:bytes/")
    assert page.index == 0
    assert page.qr_view.text == page.frames[0]

    page._on_format_change(2)  # BC-UR v2
    assert page.format == "ur2"
    assert page.frames[0].startswith("ur:bytes/")

    page._on_format_change(3)  # BBQR
    assert page.format == "bbqr"
    assert page.frames[0].startswith("B$")
    d.close()


def test_export_animated_format_frames_fit_budget():
    bw = FakeBalWindow()
    bw.willitems = _make_willitems(n=6, payload_len=400)
    d = dialogs.WillExportDialog(bw, bal_plugin=bw.bal_plugin, initial_mode="qr")
    page = d.qr_page
    for index in range(1, 4):
        page._on_format_change(index)
        for frame in page.frames:
            assert len(frame) <= dialogs.CHUNK_PRESETS[0][1]
    d.close()


def _import_roundtrip_fmt(fmt_index):
    bw = FakeBalWindow()
    bw.willitems = _make_willitems(2)
    d = dialogs.WillExportDialog(bw, bal_plugin=bw.bal_plugin, initial_mode="qr")
    page = d.qr_page
    page._on_format_change(fmt_index)
    frames = list(page.frames)
    assert frames
    d.close()

    impl = dialogs.WillImportDialog(bw, bal_plugin=bw.bal_plugin)
    import_page = impl.qr_page
    caught = {}
    import_page._finish_import = lambda payload: caught.__setitem__("payload", payload)
    for frame in frames:
        import_page._add_frame(frame)
    assert import_page.review_btn.isEnabled()
    import_page._review_and_sign()
    kind, data = dialogs.decode_will_payload(caught["payload"])
    assert kind == "will"
    assert set(data) == {"item0", "item1"}
    impl.close()


def test_import_ur1_roundtrip():
    _import_roundtrip_fmt(1)


def test_import_ur2_roundtrip():
    _import_roundtrip_fmt(2)


def test_import_bbqr_roundtrip():
    _import_roundtrip_fmt(3)


def test_import_animated_scan_debounce_autofinish():
    bw = FakeBalWindow()
    bw.willitems = _make_willitems(2)
    d = dialogs.WillExportDialog(bw, bal_plugin=bw.bal_plugin, initial_mode="qr")
    page = d.qr_page
    page._on_format_change(2)  # BC-UR v2 fountain
    frames = list(page.frames)
    d.close()

    impl = dialogs.WillImportDialog(bw, bal_plugin=bw.bal_plugin)
    import_page = impl.qr_page
    reviewed = []
    import_page._review_and_sign = lambda: reviewed.append(True)  # type: ignore[assignment]
    import_page._scanning = True
    for frame in frames:
        for _rep in range(2):
            import_page._handle_scanned_text(frame)
    assert import_page.review_btn.isEnabled()
    # The fountain transfer's part count is ``len(frames) // 2`` (pure + one
    # redundant mixed wave).
    assert import_page.total == len(frames) // 2
    assert len(import_page.frames) >= import_page.total
    assert import_page.review_btn.isEnabled()
    _app.processEvents()
    assert reviewed == [True]
    assert not import_page._scanning
    impl.close()


def test_import_garbage_scan_is_ignored():
    bw = FakeBalWindow()
    d = dialogs.WillImportDialog(bw, bal_plugin=bw.bal_plugin)
    page = d.qr_page
    for garbage in ("hello world", "12345", "B$ZZ", ""):
        page._handle_scanned_text(garbage)
        assert not page.frames
        assert page.total == 0
        assert not page.review_btn.isEnabled()
    d.close()


def test_import_different_animated_transfer_resets():
    bw = FakeBalWindow()
    d = dialogs.WillImportDialog(bw, bal_plugin=bw.bal_plugin)
    page = d.qr_page
    warnings = []
    page.show_warning = lambda msg: warnings.append(msg)  # type: ignore[assignment]

    frames_a = split_frames(encode_transfer(["A" * 120, "B" * 120]), 150)
    frames_b = split_frames(encode_transfer(["A" * 120, "B" * 120, "C" * 120]), 150)
    for frame in frames_a:
        page._add_frame(frame)
    assert page.total == len(frames_a)
    assert not warnings

    page._add_frame(frames_b[0])
    assert warnings
    assert page.total == 0
    assert not page.frames
    assert not page.review_btn.isEnabled()
    d.close()


def test_import_start_stop_scan_signal_wiring():
    """Regression: _start_scan/_stop_scan must use the QVideoSink signal
    videoFrameChanged, not the videoFrame frame getter.

    On PyQt6, ``QVideoSink.videoFrame`` is a method (the frame getter), so
    ``.videoFrame.connect(...)`` raises AttributeError. This test drives the
    real sink life-cycle with a mocked camera and asserts the scan session
    starts/ends cleanly with no error.
    """
    from PyQt6.QtMultimedia import QCamera, QMediaCaptureSession, QMediaDevices

    bw = FakeBalWindow()
    d = dialogs.WillImportDialog(bw, bal_plugin=bw.bal_plugin)
    page = d.qr_page
    errors = []
    page.show_error = lambda msg: errors.append(msg)  # type: ignore[assignment]

    fake_device = MagicMock()
    fake_device.isNull.return_value = False

    with (
        patch.object(QMediaDevices, "defaultVideoInput", return_value=fake_device),
        # Mock camera + capture session; the QVideoSink stays real so the
        # videoFrameChanged connect/disconnect wiring is exercised for real.
        patch.object(QCamera, "__new__", return_value=MagicMock()),
        patch.object(QMediaCaptureSession, "__new__", return_value=MagicMock()),
    ):
        page._start_scan()
        assert page._scanning is True
        assert not errors

        page._stop_scan()
    assert page._scanning is False
    assert page._camera is None
    assert page._video_sink is None
    assert not errors
    d.close()


# ------------------------------------------------------------------ #
# Main
# ------------------------------------------------------------------ #

if __name__ == "__main__":
    for name in sorted(dir()):
        if name.startswith("test_"):
            globals()[name]()
            print(f"  [OK] {name}")
    print("[OK] All QR transfer GUI tests passed")
