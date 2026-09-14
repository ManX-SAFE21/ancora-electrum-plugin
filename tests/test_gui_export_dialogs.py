"""
Tests for the filter-based unified export/import dialogs and the BalWindow
transport helpers (``bal.gui.qt.dialogs``, ``bal.gui.qt.window``).

Covers the shared export filters (All / Valid / Valid NC), the unified
``WillExportDialog`` file page (whole item vs tx-only content, empty-filter
abort), the audio export/import pages (KB/sec wiring, missing plugin guard,
receive flow) and the comma-separated tx-only file writer. The audio pages
run against a stub plugin so no sound hardware is exercised.

Run:
    QT_QPA_PLATFORM=offscreen python3 tests/test_gui_export_dialogs.py
"""

import base64
import json
import sys
import zlib
from unittest.mock import patch

sys.path.insert(0, __file__.rsplit("/", 2)[0])

from PyQt6.QtWidgets import QApplication, QMainWindow

import bal.gui.qt.dialogs as dialogs
from bal.core.qrtransfer import CHUNK_PRESETS

_app = QApplication.instance() or QApplication(sys.argv)

# A valid 1x1 transparent PNG, good enough for BalDialog's window icon.
_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

# A valid serialized Bitcoin transaction hex (1 input + 1 P2PKH output,
# version 2); used for real-WillItem serialization tests.
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
    QR_CHUNK_SIZE = _Cfg(CHUNK_PRESETS[0][1])

    def read_file(self, path):
        return _PNG_BYTES


class FakeWindow(QMainWindow):
    config = {}

    def format_amount(self, amount):
        return "{:.8f}".format(amount)

    def format_amount_and_units(self, amount):
        return "{:.8f} sat".format(amount)


class FakeAudioPlugin:
    """Duck-typed ``audio_modem`` plugin for the audio pages."""

    def __init__(self):
        self.modem_config = None

    def is_available(self):
        return True


class _FakeModemConfig:
    def __init__(self, kbps):
        self.modem_bps = kbps * 1000


class FakeBalWindow:
    """Duck-typed stand-in for BalWindow (dialog layer only)."""

    def __init__(self, audio_plugin=None):
        self.window = FakeWindow()
        self.bal_plugin = FakePlugin()
        self.willitems = {}
        self.audio_plugin = audio_plugin
        self.bitrate_set = None
        self.audio_payloads = []

    def get_audio_modem_plugin(self):
        return self.audio_plugin

    def set_audio_modem_bitrate(self, kbps):
        self.bitrate_set = kbps
        if self.audio_plugin is not None:
            self.audio_plugin.modem_config = _FakeModemConfig(kbps)

    def _audio_send_payload(self, payload):
        self.audio_payloads.append(payload)

    def export_json_file(self, path, will=None):
        items = will if will is not None else self.willitems
        with open(path, "w", encoding="utf-8") as f:
            json.dump({wid: wi.to_dict() for wid, wi in items.items()}, f)

    def export_tx_file(self, path, will=None):
        items = will if will is not None else self.willitems
        with open(path, "w", encoding="utf-8") as f:
            f.write(",".join(str(wi.tx) for _, wi in items.items()))


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
        return {"tx": str(self.tx)}


def _make_willitems(n=3, payload_len=60, statuses=None):
    return {
        "item{}".format(i): StubWillItem(
            "T{}".format(i) * payload_len, statuses=statuses
        )
        for i in range(n)
    }


# ------------------------------------------------------------------ #
# Shared export filters
# ------------------------------------------------------------------ #

def test_export_filter_options():
    opts = dialogs.export_filter_options()
    assert [label for label, _fn in opts] == ["All", "Valid", "Valid NC"]
    complete = StubWillItem("C*", statuses={"VALID": True, "COMPLETE": True})
    valid = StubWillItem("V*", statuses={"VALID": True})
    plain = StubWillItem("P*")
    assert opts[0][1](complete) and opts[0][1](plain)
    assert opts[1][1](complete) and opts[1][1](valid) and not opts[1][1](plain)
    assert not opts[2][1](complete)
    assert opts[2][1](valid) and not opts[2][1](plain)


def test_filter_willitems_by_index():
    items = {
        "a": StubWillItem("A*", statuses={"VALID": True, "COMPLETE": True}),
        "b": StubWillItem("B*", statuses={"VALID": True}),
        "c": StubWillItem("C*"),
    }
    opts = dialogs.export_filter_options()
    assert set(dialogs.filter_willitems(items, opts, 0)) == {"a", "b", "c"}
    assert set(dialogs.filter_willitems(items, opts, 1)) == {"a", "b"}
    assert dialogs.filter_willitems(items, opts, 2) == {"b": items["b"]}


# ------------------------------------------------------------------ #
# WillExportDialog file page
# ------------------------------------------------------------------ #

def test_file_export_selects_by_filter():
    bw = FakeBalWindow()
    items = _make_willitems(3)
    items["item0"].statuses = {"VALID": True, "COMPLETE": True}
    items["item1"].statuses = {"VALID": True}
    d = dialogs.WillExportDialog(bw, will=items, bal_plugin=bw.bal_plugin)
    assert set(d._selected_items()) == set(items)

    d._on_filter_change(1)
    assert set(d._selected_items()) == {"item0", "item1"}
    d._on_filter_change(2)
    assert list(d._selected_items()) == ["item1"]
    d.close()


def test_file_export_run_tx_only(tmpdir):
    bw = FakeBalWindow()
    items = _make_willitems(3)
    d = dialogs.WillExportDialog(bw, will=items, bal_plugin=bw.bal_plugin)
    d.content_check.setChecked(False)
    captured = {}

    def fake_gui(window, title, exporter):
        captured["title"] = title
        captured["exporter"] = exporter

    with patch.object(dialogs, "export_meta_gui", side_effect=fake_gui):
        d._export_file()
    assert captured["title"] == "will_tx"
    out = tmpdir.join("will_tx.txt").strpath
    captured["exporter"](out)
    expected = ",".join(str(wi.tx) for _, wi in items.items())
    assert open(out, encoding="utf-8").read() == expected
    d.close()


def test_file_export_run_willitem(tmpdir):
    bw = FakeBalWindow()
    items = _make_willitems(2)
    d = dialogs.WillExportDialog(bw, will=items, bal_plugin=bw.bal_plugin)
    assert d.content_check.isChecked()
    captured = {}

    def fake_gui(window, title, exporter):
        captured["title"] = title
        captured["exporter"] = exporter

    with patch.object(dialogs, "export_meta_gui", side_effect=fake_gui):
        d._export_file()
    assert captured["title"] == "will"
    out = tmpdir.join("will.json").strpath
    captured["exporter"](out)
    data = json.load(open(out, encoding="utf-8"))
    assert set(data) == set(items)
    assert data["item0"]["tx"] == str(items["item0"].tx)
    d.close()


def test_file_export_empty_under_filter_aborts():
    bw = FakeBalWindow()
    items = {
        "a": StubWillItem("A*", statuses={"VALID": True, "COMPLETE": True})
    }
    d = dialogs.WillExportDialog(bw, will=items, bal_plugin=bw.bal_plugin)
    messages = []
    d.show_message = lambda msg: messages.append(msg)  # type: ignore[assignment]
    d._filter_index = 2  # force an empty "Valid NC" selection
    with patch.object(dialogs, "export_meta_gui") as gui:
        d._export_file()
    assert not gui.called
    assert messages
    d.close()


# ------------------------------------------------------------------ #
# BalWindow transport helpers
# ------------------------------------------------------------------ #

def test_bal_window_export_tx_file(tmpdir):
    import bal.gui.qt.window as window

    items = _make_willitems(3)
    bw = object.__new__(window.BalWindow)
    bw.willitems = items
    out = tmpdir.join("will_tx.txt").strpath
    bw.export_tx_file(out)
    expected = ",".join(str(wi.tx) for _, wi in items.items())
    assert open(out, encoding="utf-8").read() == expected


def test_bal_window_set_audio_modem_bitrate():
    try:
        import amodem.config
    except ImportError:
        return
    import bal.gui.qt.window as window

    class P:
        def __init__(self):
            self.modem_config = None

    probe = P()
    bw = object.__new__(window.BalWindow)
    bw.get_audio_modem_plugin = lambda: probe
    bw.set_audio_modem_bitrate(1)
    assert probe.modem_config is amodem.config.bitrates[1]


# ------------------------------------------------------------------ #
# WillExportDialog audio page
# ------------------------------------------------------------------ #

def test_audio_export_page_send():
    bw = FakeBalWindow(audio_plugin=FakeAudioPlugin())
    items = _make_willitems(3, payload_len=30)
    bw.willitems = items
    d = dialogs.WillExportDialog(bw, bal_plugin=bw.bal_plugin)
    assert d.audio_send_btn.text() == dialogs._("Send")
    assert d.audio_send_btn.isEnabled()
    assert d.kbps_combo.count() > 0

    kbps = int(d.kbps_combo.currentText())
    d._send_audio()
    assert bw.bitrate_set == kbps
    # Whole-will default: the audio payload is a single JSON document.
    assert len(bw.audio_payloads) == 1
    data = json.loads(bw.audio_payloads[0])
    assert set(data) == set(items)
    d.close()


def test_audio_export_page_send_tx_only():
    bw = FakeBalWindow(audio_plugin=FakeAudioPlugin())
    items = _make_willitems(3, payload_len=30)
    bw.willitems = items
    d = dialogs.WillExportDialog(bw, bal_plugin=bw.bal_plugin)
    d.content_check.setChecked(False)

    kbps = int(d.kbps_combo.currentText())
    d._send_audio()
    assert bw.bitrate_set == kbps
    expected = "\n".join(dialogs.serialize_tx_list(items))
    assert bw.audio_payloads == [expected]
    d.close()


def test_audio_export_page_plugin_missing():
    # The window stays usable: only the audio option is disabled.
    bw = FakeBalWindow(audio_plugin=None)
    bw.willitems = _make_willitems(2)
    d = dialogs.WillExportDialog(bw, bal_plugin=bw.bal_plugin)
    assert not d.audio_send_btn.isEnabled()
    assert "not available" in d.audio_warn_label.text()
    assert len(d.qr_page.frames) >= 1  # QR still usable
    assert d.file_export_btn.isEnabled()
    d.close()


# ------------------------------------------------------------------ #
# WillImportDialog audio page
# ------------------------------------------------------------------ #

def test_audio_import_page_build():
    bw = FakeBalWindow(audio_plugin=FakeAudioPlugin())
    d = dialogs.WillImportDialog(bw, bal_plugin=bw.bal_plugin)
    assert d.kbps_combo.count() > 0
    assert d.receive_btn.text() == dialogs._("Receive by audio…")
    assert d.receive_btn.isEnabled()
    d.close()


def test_audio_import_page_plugin_missing():
    bw = FakeBalWindow(audio_plugin=None)
    d = dialogs.WillImportDialog(bw, bal_plugin=bw.bal_plugin)
    assert not d.receive_btn.isEnabled()
    assert "not available" in d.audio_warn_label.text()
    assert d.qr_page is not None  # QR import still usable
    d.close()


def test_audio_import_receive_wiring():
    bw = FakeBalWindow(audio_plugin=FakeAudioPlugin())
    d = dialogs.WillImportDialog(bw, bal_plugin=bw.bal_plugin)
    captured = {}

    class FakeWaitingDialog:
        def __init__(self, parent, msg, task, on_success=None, on_error=None):
            captured["msg"] = msg
            captured["task"] = task
            captured["success"] = on_success
            captured["error"] = on_error

    imported = []

    def fake_complete(bal_window, bal_plugin, payload, **kwargs):
        imported.append(payload)

    blob = zlib.compress(b"A" * 40 + b"\n" + b"B" * 40)
    with patch.object(dialogs, "WaitingDialog", FakeWaitingDialog), patch.object(
        dialogs, "_complete_import", side_effect=fake_complete
    ):
        d._audio_receive()
        kbps = int(d.kbps_combo.currentText())
        assert bw.bitrate_set == kbps
        assert captured["task"] is not None
        captured["success"](blob)
    # Payload is the raw decompressed text; autodetect handles the splitting.
    assert imported == ["A" * 40 + "\n" + "B" * 40]
    d.close()


# ------------------------------------------------------------------ #
# Whole-will JSON payload serializes a real Transaction (MyEncoder)
# ------------------------------------------------------------------ #

def test_qr_whole_will_json_serializes_transaction():
    """Regression: _whole_will_json must not raise
    "Object of type Transaction is not JSON serializable".

    Real WillItems keep a ``Transaction`` object in ``tx``; the whole-will
    QR payload (default content scope) must serialize it via MyEncoder the
    same way write_json_file does.
    """
    from bal.core.will import WillItem

    item = WillItem({
        "tx": _VALID_TX_HEX,
        "heirs": {},
        "willexecutor": None,
        "status": "",
        "description": "",
        "time": 0,
        "change": "",
        "baltx_fees": 100,
    })
    bw = FakeBalWindow()
    d = dialogs.WillExportDialog(
        bw, will={"imp0": item}, bal_plugin=bw.bal_plugin, initial_mode="qr"
    )
    j = d._whole_will_json()
    data = json.loads(j)
    assert data["imp0"]["tx"] == _VALID_TX_HEX
    d.close()


# ------------------------------------------------------------------ #
# Main
# ------------------------------------------------------------------ #

if __name__ == "__main__":
    import inspect
    import os
    import tempfile

    class _Path:
        def __init__(self, d, name):
            self.strpath = os.path.join(d, name)

    class _Tmp:
        def __init__(self):
            self._d = tempfile.mkdtemp()

        def join(self, name):
            return _Path(self._d, name)

    tmp = _Tmp()
    for name in sorted(dir()):
        if name.startswith("test_"):
            fn = globals()[name]
            fn(tmp) if inspect.signature(fn).parameters else fn()
            print("  [OK] {}".format(name))
    print("[OK] All export dialog GUI tests passed")
