"""
The inheritance plan report (CHANGELOG #92).

* ``bal.core.report``: pages of exactly 60 lines, never wider than 78
  columns, "Page n/N" on every page, a block never split across pages; the
  heirs with address, share and amount, the will-executors, the
  transaction ids; no raw transaction; the will not yet prepared;
* ``bal.gui.qt.report_actions``: the .txt file (UTF-8, CRLF), its name,
  printing one report page per sheet, saving, the "Report" button of the
  Building Will dialog.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \
        python3 tests/test_inheritance_report.py
"""

import os
import re
import sys
import tempfile
from datetime import datetime
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from PyQt6.QtWidgets import QApplication, QPushButton, QWidget

# The helpers of the #84b-1 tests: will items and a dialog without Qt set-up.
from test_build_report import _TX_HEX, _dialog, _item

import bal.i18n as i18n
from bal.core import report as core_report
from bal.core.report import LINES_PER_PAGE, WIDTH, inheritance_report, report_text
from bal.gui.qt import report_actions
from bal.gui.qt.dialogs import BalBuildWillDialog

_APP = QApplication.instance() or QApplication(sys.argv)

CREATED = datetime(2026, 10, 10, 15, 42)
SETTINGS = {"locktime": "4y", "threshold": "-180d", "baltx_fees": 10}
TAB_HEIRS = {"Erede 1": ["tb1qalice", "75%", "4y"], "pocket manuel": ["tb1qbob", "25%", "4y"]}


def _sat(sats):
    return "{} sat".format(sats)


def _report(willitems, heirs=TAB_HEIRS):
    return inheritance_report(
        willitems, heirs, SETTINGS, True, format_amount=_sat,
        wallet_name="wallet_test", plugin_version="0.7.0",
        electrum_version="4.8.2", created=CREATED,
    )


def _text(pages):
    return "\n".join(line for page in pages for line in page)


def _check_layout(pages):
    total = len(pages)
    for number, page in enumerate(pages, 1):
        assert len(page) == LINES_PER_PAGE
        assert all(len(line) <= WIDTH for line in page), max(page, key=len)
        assert page[-1].endswith("Page {}/{}".format(number, total))
        assert page[-1].startswith(" PRIVATE")


# ----------------------------------------------------------------- core

def test_one_page_will_in_force():
    item = _item("COMPLETE", "PUSHED", "CHECKED")
    pages = _report({item._id: item})
    _check_layout(pages)
    assert len(pages) == 1
    text = _text(pages)
    for expected in ("SAFE21 HEARTBEAT - INHERITANCE PLAN REPORT", "PRIVATE",
                     "wallet_test", "Safe21 Heartbeat 0.7.0 - Electrum 4.8.2",
                     "2026-10-10 15:42", "Your will is in force",
                     "Delivery to the heirs .. 2028-09-29", "Miner fee ............ 10 sat/vB",
                     "Erede 1", "address: tb1qalice", "75 %", "300000 sat",
                     "pocket manuel", "25 %", "100000 sat",
                     "Total to the heirs", "400000 sat",
                     "we0.test", "received and verified", "HOW IT WORKS",
                     "txid {}".format(item._id), "End of report"):
        assert expected in text, expected
    # Never the raw transaction, the executor fee entry or the OP_RETURN one.
    assert _TX_HEX not in text and str(item.tx) not in text
    assert 'w!ll3x3c' not in text and "OP_RETURN" not in text


def test_many_heirs_two_pages_no_block_split():
    item = _item("COMPLETE", "PUSHED", "CHECKED")
    item.heirs = {
        "heir {:02d}".format(i): ["tb1qheir{:02d}".format(i), "5%", "4y", 10_000]
        for i in range(20)
    }
    pages = _report({item._id: item})
    _check_layout(pages)
    assert len(pages) == 2
    assert pages[1][0].startswith(" SAFE21 HEARTBEAT - INHERITANCE PLAN REPORT - 2026-10-10")
    for page in pages:
        for row, line in enumerate(page):
            if "address:" in line:
                assert page[row - 1].startswith(" heir "), (row, page[row - 1])
            if line.startswith(" heir ") and not line.startswith(" heir 00"):
                assert page[row - 1] == "" or page[row - 1].startswith(" SAFE21")
    assert "5 %" in _text(pages) and "200000 sat" in _text(pages)


def test_no_will_yet_uses_the_heirs_tab():
    heirs = dict(TAB_HEIRS)
    heirs['w!ll3x3c"https://we0.test'] = ["tb1qwe", "1000", "4y"]
    pages = _report({}, heirs)
    _check_layout(pages)
    text = _text(pages)
    assert "No will in force" in text
    assert "75 %" in text and "address: tb1qbob" in text
    assert "No transaction is prepared yet" in text
    assert "w!ll3x3c" not in text


def test_percent_uses_the_language_separator():
    assert core_report._percent(6.25) == "6.25 %"
    assert core_report._percent(60.0) == "60 %"
    old = i18n._language
    i18n._language = "it_IT"
    try:
        assert core_report._percent(36.3636) == "36,36 %"
    finally:
        i18n._language = old


def test_report_text_crlf_and_file():
    item = _item("COMPLETE", "PUSHED", "CHECKED")
    pages = _report({item._id: item})
    text = report_text(pages)
    assert text.count("\r\n") == LINES_PER_PAGE * len(pages)
    assert "\n" not in text.replace("\r\n", "")
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "r.txt")
        report_actions.write_report(path, pages)
        data = open(path, "rb").read()
    assert data.decode("utf-8") == text


def test_default_file_name():
    assert report_actions.default_file_name("My wallet (1)", CREATED) == (
        "safe21-report-My_wallet_1-2026-10-10.txt")
    assert report_actions.default_file_name("", CREATED) == (
        "safe21-report-wallet-2026-10-10.txt")


# ------------------------------------------------------------------ GUI

def test_printing_puts_one_report_page_on_each_sheet():
    from PyQt6.QtPrintSupport import QPrinter

    item = _item("COMPLETE", "PUSHED", "CHECKED")
    item.heirs = {"heir {:02d}".format(i): ["tb1qh{:02d}".format(i), "5%", "4y", 10_000]
                  for i in range(20)}
    pages = _report({item._id: item})
    assert len(pages) == 2
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "r.pdf")
        printer = report_actions.new_printer()
        printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
        printer.setOutputFileName(path)
        report_actions.paint_pages(printer, pages)
        pdf = open(path, "rb").read()
    assert len(re.findall(rb"/Type\s*/Page[^s]", pdf)) == 2


def _fake_window(willitems):
    bw = MagicMock()
    bw.willitems = willitems
    bw.heirs = TAB_HEIRS
    bw.will_settings = SETTINGS
    bw.bal_plugin.is_basic_mode.return_value = True
    bw.window.config.format_amount_and_units.side_effect = _sat
    bw.wallet.basename.return_value = "wallet_test"
    bw._still_active_for_summary.return_value = []
    return bw


def test_save_writes_the_chosen_file():
    item = _item("COMPLETE", "PUSHED", "CHECKED")
    bw = _fake_window({item._id: item})
    with tempfile.TemporaryDirectory() as tmp:
        target = os.path.join(tmp, "report.txt")
        with patch.object(report_actions, "getSaveFileName", return_value=target) as ask:
            report_actions.save_report(bw)
        assert ask.call_args.kwargs["filename"].endswith(
            report_actions.default_file_name("wallet_test"))
        text = open(target, "rb").read().decode("utf-8")
    assert "txid {}".format(item._id) in text
    bw.show_message.assert_called_once()
    assert not bw.show_warning.called


def test_save_as_pdf_one_page_per_sheet():
    item = _item("COMPLETE", "PUSHED", "CHECKED")
    item.heirs = {"heir {:02d}".format(i): ["tb1qh{:02d}".format(i), "5%", "4y", 10_000]
                  for i in range(20)}
    bw = _fake_window({item._id: item})
    with tempfile.TemporaryDirectory() as tmp:
        target = os.path.join(tmp, "report.pdf")
        with patch.object(report_actions, "getSaveFileName", return_value=target) as ask:
            report_actions.save_report_pdf(bw)
        assert ask.call_args.kwargs["filename"].endswith(
            report_actions.default_file_name("wallet_test", extension="pdf"))
        pdf = open(target, "rb").read()
    assert pdf.startswith(b"%PDF")
    assert len(re.findall(rb"/Type\s*/Page[^s]", pdf)) == 2
    bw.show_message.assert_called_once()
    assert not bw.show_warning.called


def test_report_only_for_a_verified_will_in_force():
    """Owner's rule: not to sign, to send, to verify, failed, Check Alive..."""
    assert core_report.report_block_reason({"state": "ok"}) is None
    for state in ("to_sign", "to_send", "not_received", "none_received",
                  "check_alive", "expired", "still_active", "none", None):
        assert core_report.report_block_reason({"state": state} if state else None)
    # Sent but not verified yet: the menu entries are grey, the reason shown.
    item = _item("COMPLETE", "PUSHED")
    bw = _fake_window({item._id: item})
    menu = report_actions.report_menu(bw, QWidget())
    actions = menu.actions()
    assert actions[0].isVisible() and "verified by every will-executor" in actions[0].text()
    assert not any(a.isEnabled() for a in actions)
    # And the actions refuse too (a defence: the menu is checked on opening).
    with patch.object(report_actions, "getSaveFileName") as ask:
        report_actions.save_report(bw)
        report_actions.save_report_pdf(bw)
    assert not ask.called and bw.show_warning.call_count == 2


def test_backup_only_will_is_allowed_and_explained():
    item = _item("COMPLETE", url=None)
    item.we = None
    bw = _fake_window({item._id: item})
    assert report_actions.block_reason(bw) is None
    text = _text(_report({item._id: item}))
    assert "none (backup transaction only)" in text
    assert "you keep the signed transaction yourself" in text
    assert "no will-executor (backup transaction)" in text


def test_building_will_dialog_has_the_report_button():
    bw = _dialog({"a": _item("COMPLETE", "PUSHED", "CHECKED")}).bal_window
    bw.bal_plugin.read_file.return_value = b""
    bw.window.format_amount_and_units.side_effect = _sat
    d = BalBuildWillDialog(bw, parent=QWidget())
    try:
        d._add_close_button()
        buttons = [b for b in d.findChildren(QPushButton) if b.text() == "Will report"]
        assert len(buttons) == 1 and buttons[0].menu() is not None
        visible = [a for a in buttons[0].menu().actions() if a.isVisible()]
        assert [a.text() for a in visible] == ["Save as file…", "Save as PDF…", "Print…"]
        assert all(a.isEnabled() for a in visible)              # verified will
    finally:
        d.thread.stop()
        d.deleteLater()


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print("{} tests passed".format(len(tests)))
