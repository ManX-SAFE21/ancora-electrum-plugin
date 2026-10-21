"""
bal.gui.qt.trace_window
=======================

The window "CLI command trace…" of the Will tab's Tools menu (CHANGELOG
#173, owner 2026-10-21): the command trace of :mod:`bal.core.trace`, in the
style of AutoCAD's command line, on a black background.

* read-only: nothing can be typed (a command typed in Electrum's Console
  could overwrite a ``bal_*`` command, #148);
* in BASIC and ADVANCED alike;
* Pause (the view stops, the lines keep coming and appear at Resume),
  Copy, Save to file…, Clear, the detail level Normal / Full, Close;
* one window per Electrum window, shown again in front when it is
  already open.

The trace is kept only while Electrum is open (owner): "Save to file…"
keeps a copy.
"""

import html
import time

from electrum.gui.qt.util import getSaveFileName
from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QFontDatabase, QGuiApplication
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from ...core import trace
from ...core.brand import NAME
from ...i18n import _
from .common import _logger

# How often the window reads the new lines, in milliseconds.
POLL_MS = 300

# The colours on black (owner: black background), readable in both
# Electrum themes since the background never changes.
BACKGROUND = "#000000"
COLOURS = {
    trace.COMMAND: "#85B7EB",   # light blue
    trace.REQUEST: "#B4B2A9",   # grey
    trace.ANSWER: "#F1EFE8",    # almost white
    trace.OK: "#97C459",        # green
    trace.WARNING: "#EF9F27",   # orange
    trace.ERROR: "#F09595",     # light red
}
CLOCK_COLOUR = "#888780"

# The detail levels of the combo, in order.
LEVEL_NORMAL = "normal"
LEVEL_FULL = "full"


def line_html(line):
    """One trace line as coloured HTML (escaped): the clock in grey, then
    the mark and the text in the colour of its kind."""
    plain = trace.line_text(line)
    clock, _sep, rest = plain.partition("  ")
    colour = COLOURS.get(line.kind, COLOURS[trace.ANSWER])
    # A <pre>-like span keeps the indentation of the marks.
    return (
        '<span style="color:{c0}">{clock}</span>'
        '<span style="color:{c1}; white-space:pre">  {rest}</span>'
    ).format(c0=CLOCK_COLOUR, clock=html.escape(clock),
             c1=colour, rest=html.escape(rest))


def default_file_name(now=None):
    """``ancora-cli-trace-20261021-140211.txt``: the name offered by Save."""
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
    return "ancora-cli-trace-{}.txt".format(stamp)


class TraceDialog(QDialog):
    """The black, read-only window of the command trace."""

    def __init__(self, parent=None, config=None, poll_ms=POLL_MS):
        """Build the window and start reading the trace every *poll_ms*.

        *config*: Electrum's config, for the folder of "Save to file…"
        (``None``: the dialog asks without a remembered folder).
        """
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("{} — {}".format(_("CLI command trace"), NAME))
        self.resize(900, 560)
        self.paused = False
        self.last_seq = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        self.subtitle = QLabel(_("(CLI commands, view only)"))
        layout.addWidget(self.subtitle)

        self.view = QPlainTextEdit(self)
        self.view.setReadOnly(True)
        self.view.setUndoRedoEnabled(False)
        self.view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.view.setMaximumBlockCount(trace.MAX_LINES)
        self.view.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.view.setPlaceholderText(
            _("The commands appear here while the plugin works."))
        self.view.setStyleSheet(
            "QPlainTextEdit {{ background: {bg}; color: {fg}; border: none; }}".format(
                bg=BACKGROUND, fg=COLOURS[trace.ANSWER]))
        layout.addWidget(self.view, 1)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.pause_button = QPushButton(_("Pause"))
        self.pause_button.clicked.connect(self.toggle_pause)
        self.copy_button = QPushButton(_("Copy"))
        self.copy_button.clicked.connect(self.copy_text)
        self.save_button = QPushButton(_("Save to file…"))
        self.save_button.clicked.connect(self.save_text)
        self.clear_button = QPushButton(_("Clear"))
        self.clear_button.clicked.connect(self.clear)
        for b in (self.pause_button, self.copy_button, self.save_button,
                  self.clear_button):
            bar.addWidget(b)
        bar.addStretch(1)
        bar.addWidget(QLabel(_("Detail")))
        self.level_combo = QComboBox()
        self.level_combo.addItem(_("Normal"), LEVEL_NORMAL)
        self.level_combo.addItem(_("Full"), LEVEL_FULL)
        self.level_combo.currentIndexChanged.connect(lambda _i: self.redraw())
        bar.addWidget(self.level_combo)
        self.close_button = QPushButton(_("Close"))
        self.close_button.clicked.connect(self.reject)
        bar.addWidget(self.close_button)
        layout.addLayout(bar)

        self.redraw()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.read_new)
        self.timer.start(poll_ms)

    def done(self, result):
        """Close the window: stop reading the trace first."""
        self.timer.stop()
        super().done(result)

    def full(self):
        """True when the "Full" detail level is chosen."""
        return self.level_combo.currentData() == LEVEL_FULL

    def _shown(self, lines):
        """The *lines* of the chosen level."""
        full = self.full()
        return [line for line in lines if full or not line.full]

    def _append(self, lines):
        """Add *lines* at the bottom; follow them when the view was at the
        bottom (a user reading higher up is not moved)."""
        bar = self.view.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 2
        for line in self._shown(lines):
            self.view.appendHtml(line_html(line))
        if at_bottom:
            bar.setValue(bar.maximum())

    def redraw(self):
        """Draw every line kept again (after a change of level)."""
        self.view.clear()
        lines = trace.lines_after(0)
        self._append(lines)
        if lines:
            self.last_seq = lines[-1].seq

    def read_new(self):
        """Add the lines written since the last read (not while paused:
        they appear when the view is resumed)."""
        if self.paused:
            return
        try:
            lines = trace.lines_after(self.last_seq)
            if lines:
                self.last_seq = lines[-1].seq
                self._append(lines)
        except Exception as e:
            _logger.debug(f"trace window not updated: {e!r}")

    def toggle_pause(self):
        """Pause or resume the view; the button says what it will do."""
        self.paused = not self.paused
        self.pause_button.setText(_("Resume") if self.paused else _("Pause"))
        if not self.paused:
            self.read_new()

    def text(self):
        """The lines kept, as plain text, at the chosen level."""
        return trace.plain_text(trace.lines_after(0), full=self.full())

    def copy_text(self):
        """Copy the lines (plain text) to the clipboard."""
        QGuiApplication.clipboard().setText(self.text())

    def save_text(self):
        """Ask where, then save the lines as a plain text file.

        Returns the path written, or ``None`` (cancelled or not written).
        """
        target = None
        try:
            if self.config is not None:
                target = getSaveFileName(
                    parent=self,
                    title=_("Save the command trace"),
                    filename=default_file_name(),
                    filter="{} (*.txt);;{} (*)".format(_("Text files"), _("All files")),
                    default_extension="txt",
                    config=self.config,
                )
            else:
                target, _f = QFileDialog.getSaveFileName(
                    self, _("Save the command trace"), default_file_name(),
                    "{} (*.txt)".format(_("Text files")))
        except Exception as e:
            _logger.error(f"trace: save dialog failed: {e!r}")
            return None
        if not target:
            return None
        try:
            with open(target, "w", encoding="utf-8", newline="\n") as f:
                f.write(self.text() + "\n")
        except Exception as e:
            _logger.error(f"trace: saving {target} failed: {e!r}")
            QMessageBox.warning(self, NAME, _("Could not save the file: {}").format(e))
            return None
        return target

    def clear(self):
        """Forget the lines (the trace and the view)."""
        trace.clear()
        self.view.clear()


def show_trace(bal_window, parent=None):
    """Open the window "CLI command trace…" (one per Electrum window; shown
    again, in front, if it is already open). Returns it."""
    dialog = getattr(bal_window, "trace_dialog", None)
    if dialog is None:
        main = getattr(bal_window, "window", None)
        dialog = TraceDialog(parent or main, config=getattr(main, "config", None))
        bal_window.trace_dialog = dialog
        dialog.finished.connect(lambda *_a: setattr(bal_window, "trace_dialog", None))
    from .window_utils import show_on_top
    show_on_top(dialog, modal_to_window=False)
    return dialog
