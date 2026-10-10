"""Save and print the inheritance plan report (CHANGELOG #92).

The text comes from :mod:`bal.core.report` (no Qt). Here: the "Report" menu
("Save as file…", "Save as PDF…", "Print…") of the Building Will dialog and
of the Will tab's tools menu, the ``.txt`` file (UTF-8, CRLF, for Notepad
too), the PDF (the same sheets as the printing) and the printing.

Printing puts ONE report page on ONE sheet, so "Page 1/2" on paper is
always right: A4, 20 mm margins, 10 pt monospaced (the size the report's
60 lines x 78 columns were computed for); on a smaller printable area (US
Letter, wide hardware margins) the font shrinks instead of breaking pages.
"""

import os
import re
from datetime import datetime

from electrum.gui.qt.util import getSaveFileName
from electrum.logging import get_logger
from PyQt6.QtCore import QMarginsF, QPointF
from PyQt6.QtGui import QFont, QFontMetricsF, QPageLayout, QPageSize, QPainter
from PyQt6.QtWidgets import QDialog, QMenu

from ...core.plugin_base import get_version
from ...core.report import (
    LINES_PER_PAGE,
    WIDTH,
    inheritance_report,
    report_block_reason,
    report_text,
)
from ...core.summary import will_summary
from ...i18n import _
from .calendar import BalCalendar

_logger = get_logger(__name__)

#: Paper set-up the report was laid out for (see bal.core.report).
MARGIN_MM = 20
FONT_POINTS = 10.0
LINE_SPACING = 1.2


def _still_active(bal_window):
    try:
        return bal_window._still_active_for_summary()
    except Exception as e:
        _logger.debug(f"report: still-active wills not read: {e!r}")
        return None


def block_reason(bal_window):
    """Why the report cannot be made now (owner's rule, #92), or ``None``.

    Read from the same will summary as the Will tab's box. Never raises:
    when the state cannot be read the report is not allowed.
    """
    try:
        summary = will_summary(
            bal_window.willitems,
            len(bal_window.heirs or {}),
            bal_window.will_settings,
            bal_window.bal_plugin.is_basic_mode(),
            still_active=_still_active(bal_window),
        )
    except Exception as e:
        _logger.debug(f"report: summary not read: {e!r}")
        summary = None
    return report_block_reason(summary, bal_window.willitems)


def build_report(bal_window):
    """The report pages for the wallet of ``bal_window`` (current state)."""
    from electrum.version import ELECTRUM_VERSION

    still_active = _still_active(bal_window)
    return inheritance_report(
        bal_window.willitems,
        bal_window.heirs,
        bal_window.will_settings,
        bal_window.bal_plugin.is_basic_mode(),
        # The wallet's unit only, never a fiat value (owner's decision).
        format_amount=bal_window.window.config.format_amount_and_units,
        wallet_name=bal_window.wallet.basename(),
        plugin_version=get_version(),
        electrum_version=ELECTRUM_VERSION,
        still_active=still_active,
    )


def report_menu(bal_window, parent, title=None):
    """The "Report" menu: "Save as file…", "Save as PDF…", "Print…".

    Every time it opens it checks the owner's rule (:func:`block_reason`):
    when the will is not in force and verified the three entries are grey
    and a first, grey line says why (a tooltip on a disabled entry is easy
    to miss).
    """
    menu = QMenu(title or "", parent)
    reason = menu.addAction("")
    reason.setEnabled(False)
    actions = [
        menu.addAction(_("Save as file…"), lambda: save_report(bal_window, parent)),
        menu.addAction(_("Save as PDF…"), lambda: save_report_pdf(bal_window, parent)),
        menu.addAction(_("Print…"), lambda: print_report(bal_window, parent)),
    ]

    def refresh():
        why = block_reason(bal_window)
        reason.setText(why or "")
        reason.setVisible(bool(why))
        for action in actions:
            action.setEnabled(not why)

    menu.aboutToShow.connect(refresh)
    refresh()
    return menu


def _allowed(bal_window):
    """True when the report may be made; otherwise say why. Never raises."""
    why = block_reason(bal_window)
    if why:
        bal_window.show_warning(why)
        return False
    return True


def default_file_name(wallet_name, today=None, extension="txt"):
    """``safe21-report-<wallet>-YYYY-MM-DD.<extension>`` (only safe characters)."""
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", wallet_name or "").strip("._") or "wallet"
    day = (today or datetime.now()).strftime("%Y-%m-%d")
    return "safe21-report-{}-{}.{}".format(safe, day, extension)


def write_report(path, pages):
    """Write the pages to ``path``: UTF-8, CRLF, 60 lines per page."""
    with open(path, "wb") as f:
        f.write(report_text(pages).encode("utf-8"))


def save_report(bal_window, parent=None):
    """Ask where, then save the report as a ``.txt`` file. Never raises."""
    if not _allowed(bal_window):
        return
    try:
        pages = build_report(bal_window)
    except Exception as e:
        _logger.error(f"report: not built: {e!r}")
        bal_window.show_warning(_("The report could not be prepared: {}").format(e))
        return
    target = getSaveFileName(
        parent=parent or bal_window.window,
        title=_("Save the inheritance plan report"),
        # An absolute name makes Electrum open the Desktop, not the last
        # folder it remembers (same as the calendar file).
        filename=os.path.join(
            BalCalendar.desktop_dir(), default_file_name(bal_window.wallet.basename())
        ),
        filter="Text (*.txt);;All files (*)",
        default_extension="txt",
        config=bal_window.window.config,
    )
    if not target:
        return
    try:
        write_report(target, pages)
    except OSError as e:
        _logger.error(f"report: not saved: {e!r}")
        bal_window.show_warning(_("Could not save the report: {}").format(e))
        return
    bal_window.show_message(_("Report saved to:\n{}").format(target))


def write_report_pdf(path, pages):
    """Write the pages to a PDF: the same sheets as "Print…".

    Owner (#92): a .txt printed from another program (Notepad) breaks its
    pages where that program wants; the PDF keeps one report page per
    sheet wherever it is opened or printed.
    """
    from PyQt6.QtPrintSupport import QPrinter

    printer = new_printer()
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    printer.setOutputFileName(path)
    paint_pages(printer, pages)


def save_report_pdf(bal_window, parent=None):
    """Ask where, then save the report as a PDF. Never raises."""
    if not _allowed(bal_window):
        return
    try:
        pages = build_report(bal_window)
    except Exception as e:
        _logger.error(f"report: not built: {e!r}")
        bal_window.show_warning(_("The report could not be prepared: {}").format(e))
        return
    target = getSaveFileName(
        parent=parent or bal_window.window,
        title=_("Save the inheritance plan report"),
        filename=os.path.join(
            BalCalendar.desktop_dir(),
            default_file_name(bal_window.wallet.basename(), extension="pdf"),
        ),
        filter="PDF (*.pdf);;All files (*)",
        default_extension="pdf",
        config=bal_window.window.config,
    )
    if not target:
        return
    try:
        write_report_pdf(target, pages)
    except Exception as e:
        _logger.error(f"report: PDF not saved: {e!r}")
        bal_window.show_warning(_("Could not save the report: {}").format(e))
        return
    bal_window.show_message(_("Report saved to:\n{}").format(target))


def new_printer():
    """A high-resolution printer set to A4 with 20 mm margins."""
    from PyQt6.QtPrintSupport import QPrinter

    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    printer.setPageMargins(
        QMarginsF(MARGIN_MM, MARGIN_MM, MARGIN_MM, MARGIN_MM), QPageLayout.Unit.Millimeter
    )
    printer.setDocName(_("Inheritance plan report"))
    return printer


def print_report(bal_window, parent=None):
    """The printer dialog, then the report, one page per sheet. Never raises."""
    try:
        from PyQt6.QtPrintSupport import QPrintDialog
    except ImportError as e:
        bal_window.show_warning(_("Printing is not available: {}").format(e))
        return
    if not _allowed(bal_window):
        return
    try:
        pages = build_report(bal_window)
        printer = new_printer()
        dialog = QPrintDialog(printer, parent or bal_window.window)
        dialog.setWindowTitle(_("Print the inheritance plan report"))
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        paint_pages(printer, pages)
    except Exception as e:
        _logger.error(f"report: not printed: {e!r}")
        bal_window.show_warning(_("The report could not be printed: {}").format(e))


def _fixed_font(points):
    font = QFont("Courier New")
    font.setStyleHint(QFont.StyleHint.TypeWriter)
    font.setFixedPitch(True)
    font.setPointSizeF(points)
    return font


def _fit(printer, area):
    """The font and line step that put 78 x 60 characters in ``area``.

    10 pt with 1.2 spacing on A4 with 20 mm margins; smaller when the
    printable area is smaller, never larger.
    """
    dpi = printer.resolution()
    points = FONT_POINTS
    for _attempt in range(3):
        font = _fixed_font(points)
        metrics = QFontMetricsF(font, printer)
        step = max(metrics.lineSpacing(), points * LINE_SPACING / 72.0 * dpi)
        width = metrics.horizontalAdvance("M" * WIDTH)
        scale = min(1.0, area.width() / width, area.height() / (step * LINES_PER_PAGE))
        if scale >= 0.999:
            return font, metrics, step
        points *= scale * 0.995
    return font, metrics, step


def paint_pages(printer, pages):
    """Draw every report page on its own sheet of ``printer``."""
    painter = QPainter()
    if not painter.begin(printer):
        raise RuntimeError("the printer could not be opened")
    try:
        area = printer.pageLayout().paintRectPixels(printer.resolution())
        font, metrics, step = _fit(printer, area)
        painter.setFont(font)
        for number, page in enumerate(pages):
            if number:
                printer.newPage()
            for row, line in enumerate(page):
                if line:
                    painter.drawText(QPointF(0, row * step + metrics.ascent()), line)
    finally:
        painter.end()
