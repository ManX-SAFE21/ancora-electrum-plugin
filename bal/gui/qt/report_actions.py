"""Save and print the will report (CHANGELOG #92, #132).

The data comes from :func:`bal.core.report.will_report` (no Qt), the pages
from :mod:`bal.gui.qt.report_pages`. Here: the "Will report" menu ("Save as
PDF…", "Print…") of the Building Will dialog and of the Will tab's tools
menu, the PDF file and the printing. Both draw the SAME pages with the same
painter: one report page on one A4 sheet.

#132 (owner): the "Save as file…" entry (a 78-column ``.txt``) is removed:
the PDF is the clear report, and a text file printed from another program
breaks its pages where that program wants.
"""

import os
import re
from datetime import datetime

from electrum.gui.qt.util import getSaveFileName
from electrum.logging import get_logger
from PyQt6.QtCore import QMarginsF
from PyQt6.QtGui import QPageLayout, QPageSize
from PyQt6.QtWidgets import QDialog, QMenu

from ...core.plugin_base import get_version
from ...core.report import report_block_reason, will_report
from ...core.summary import selected_urls, will_summary
from ...core.willexecutors import Willexecutors
from ...i18n import _
from . import report_pages
from .calendar import BalCalendar

_logger = get_logger(__name__)


def _still_active(bal_window):
    """The sent wills still executable (the will summary's list), or None."""
    try:
        return bal_window._still_active_for_summary()
    except Exception as e:
        _logger.debug(f"report: still-active wills not read: {e!r}")
        return None


def _warn_months(bal_window):
    """The #124 colour thresholds of the plugin, or None (the defaults)."""
    try:
        value = bal_window.bal_plugin.warning_months()
    except Exception:
        return None
    return value if isinstance(value, tuple) else None


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
            selected=_selected(bal_window),
        )
    except Exception as e:
        _logger.debug(f"report: summary not read: {e!r}")
        summary = None
    return report_block_reason(summary, bal_window.willitems, _selected(bal_window))


def _selected(bal_window):
    """The URLs of the will-executors selected now (#134), or None."""
    try:
        return selected_urls(Willexecutors.get_willexecutors(bal_window.bal_plugin))
    except Exception:
        return None


def _format_amount(bal_window):
    """The wallet's amount formatter (owner: the wallet's unit, never fiat)."""
    return bal_window.window.config.format_amount_and_units


def build_report(bal_window):
    """The :class:`bal.core.report.WillReport` of ``bal_window`` (current state)."""
    from electrum.version import ELECTRUM_VERSION

    wallet = getattr(bal_window, "wallet", None)
    return will_report(
        bal_window.willitems,
        bal_window.heirs,
        bal_window.will_settings,
        bal_window.bal_plugin.is_basic_mode(),
        wallet=wallet,
        wallet_name=wallet.basename() if wallet is not None else "",
        plugin_version=get_version(),
        electrum_version=ELECTRUM_VERSION,
        still_active=_still_active(bal_window),
        warn_months=_warn_months(bal_window),
        selected=_selected(bal_window),
    )


def build_pages(bal_window):
    """``(pages, logo)``: the laid-out pages and the logo image (or None).

    The logo is read with the plugin's ``read_file`` (the plugin is loaded
    from a zip, never through ``os.path``); without it the vector logo is
    drawn.
    """
    report = build_report(bal_window)
    pages = report_pages.layout_report(report, _format_amount(bal_window))
    logo = report_pages.load_logo(bal_window.bal_plugin.read_file)
    return pages, logo


def report_menu(bal_window, parent, title=None):
    """The "Will report" menu: "Save as PDF…" and "Print…".

    Every time it opens it checks the owner's rule (:func:`block_reason`):
    when the will is not in force and verified the entries are grey and a
    first, grey line says why (a tooltip on a disabled entry is easy to
    miss).
    """
    menu = QMenu(title or "", parent)
    reason = menu.addAction("")
    reason.setEnabled(False)
    actions = [
        menu.addAction(_("Save as PDF…"), lambda: save_report_pdf(bal_window, parent)),
        menu.addAction(_("Print…"), lambda: print_report(bal_window, parent)),
    ]

    def refresh():
        """Grey the entries and show the reason when the rule says no."""
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


def default_file_name(wallet_name, today=None, extension="pdf"):
    """``safe21-report-<wallet>-YYYY-MM-DD.<extension>`` (only safe characters)."""
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", wallet_name or "").strip("._") or "wallet"
    day = (today or datetime.now()).strftime("%Y-%m-%d")
    return "safe21-report-{}-{}.{}".format(safe, day, extension)


def write_report_pdf(path, pages, logo=None):
    """Write the pages to a PDF: the same sheets as "Print…"."""
    from PyQt6.QtPrintSupport import QPrinter

    printer = new_printer()
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    printer.setOutputFileName(path)
    report_pages.print_pages(printer, pages, logo)


def save_report_pdf(bal_window, parent=None):
    """Ask where, then save the report as a PDF. Never raises."""
    if not _allowed(bal_window):
        return
    try:
        pages, logo = build_pages(bal_window)
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
        filter="PDF (*.pdf);;All files (*)",
        default_extension="pdf",
        config=bal_window.window.config,
    )
    if not target:
        return
    try:
        write_report_pdf(target, pages, logo)
    except Exception as e:
        _logger.error(f"report: PDF not saved: {e!r}")
        bal_window.show_warning(_("Could not save the report: {}").format(e))
        return
    bal_window.show_message(_("Report saved to:\n{}").format(target))


def new_printer():
    """A high-resolution printer set to A4 with the report's margins (15 mm)."""
    from PyQt6.QtPrintSupport import QPrinter

    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    margin = report_pages.MARGIN_MM
    printer.setPageMargins(QMarginsF(margin, margin, margin, margin),
                           QPageLayout.Unit.Millimeter)
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
        pages, logo = build_pages(bal_window)
        printer = new_printer()
        dialog = QPrintDialog(printer, parent or bal_window.window)
        dialog.setWindowTitle(_("Print the inheritance plan report"))
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        report_pages.print_pages(printer, pages, logo)
    except Exception as e:
        _logger.error(f"report: not printed: {e!r}")
        bal_window.show_warning(_("The report could not be printed: {}").format(e))
