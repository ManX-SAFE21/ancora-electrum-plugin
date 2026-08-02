"""
bal.gui.qt.calendar
===================

iCalendar (.ics) generation and "open with default calendar app" helper.

When a will is built, the plugin can create a calendar event reminding the user
to "check in" before the locktime expires.  This module turns the event data
into an RFC-5545 .ics file and opens it with the OS default application.
"""

from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QToolButton

from .common import *
from .common import _, _logger  # underscore names are not re-exported by "import *"


class BalCalendarButton(QToolButton):
    """A QToolButton with a dropdown menu for .ics calendar file actions.

    Provides three actions: Open (with configured app), Open with..., and Save.
    Accepts an ``ics_provider`` callable that returns the .ics content string.
    """

    def __init__(self, bal_window, ics_provider, parent=None):
        super().__init__(parent)
        self._bal_window = bal_window
        self._ics_provider = ics_provider
        self._calendar_temp_path = None

        calendar_menu = QMenu(self)
        open_action = QAction(_("Open"), self)
        open_action.triggered.connect(self._on_open)
        calendar_menu.addAction(open_action)
        open_with_action = QAction(_("Open with..."), self)
        open_with_action.triggered.connect(self._on_open_with)
        calendar_menu.addAction(open_with_action)
        save_action = QAction(_("Save"), self)
        save_action.triggered.connect(self._on_save)
        calendar_menu.addAction(save_action)

        self.setMenu(calendar_menu)
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)

    # ------------------------------------------------------------------ #
    #  ICS generation (lazy: generated on first action)                  #
    # ------------------------------------------------------------------ #

    def _ensure_ics(self):
        """Generate the .ics content and cache the temp file path.

        If the provider returns no content because there are no reminder events
        to save (the delivery date is too close or already passed), warn the
        user and do NOT create a file (ToDo #2).
        """
        try:
            content = self._ics_provider()
            if content:
                self._calendar_temp_path = BalCalendar.write_temp_ics(content)
            else:
                self._calendar_temp_path = None
                self._bal_window.show_warning(
                    _(
                        "No reminders were saved: the delivery date is too "
                        "close (or already passed)"
                    )
                )
        except Exception as e:
            _logger.error(f"failed to generate .ics: {e}")
            self._calendar_temp_path = None
        return self._calendar_temp_path

    # ------------------------------------------------------------------ #
    #  Menu action handlers                                              #
    # ------------------------------------------------------------------ #

    def _on_open(self):
        """Open the .ics with the app configured in CALENDAR_APP."""
        path = self._ensure_ics()
        if not path:
            return
        import shlex
        import subprocess
        if self._bal_window.bal_plugin.is_basic_mode():
            app = self._bal_window.bal_plugin.CALENDAR_APP.default
        else:
            app = self._bal_window.bal_plugin.CALENDAR_APP.get()
        if not app:
            return
        try:
            args = shlex.split(app) + [path]
            subprocess.check_call(args)
        except Exception as e:
            _logger.error(f"opening calendar file failed: {e}")
            self._bal_window.show_warning(
                _("Could not open the calendar file: {}").format(e)
            )

    def _on_open_with(self):
        """Let the user pick an application and open the .ics with it."""
        path = self._ensure_ics()
        if not path:
            return
        app, ok = QInputDialog.getText(
            self,
            _("Open calendar with..."),
            _("Enter the application command:"),
        )
        if ok and app:
            app = app.strip()
            if app:
                try:
                    BalCalendar.open_with_default_app(app, path)
                except Exception as e:
                    _logger.error(f"opening calendar with custom app failed: {e}")
                    self._bal_window.show_warning(
                        _("Could not open the calendar file: {}").format(e)
                    )

    def _on_save(self):
        """Show a "Save As" dialog and save the .ics file."""
        path = self._ensure_ics()
        if not path:
            return
        desktop = BalCalendar.desktop_dir()
        default_path = os.path.join(desktop, "BAL_will_event.ics")
        target = getSaveFileName(
            parent=self._bal_window.window,
            title=_("Save calendar reminder (.ics)"),
            filename=default_path,
            filter="iCalendar (*.ics);;All files (*)",
            default_extension="ics",
            config=self._bal_window.window.config,
        )
        if not target:
            return
        try:
            with open(path, "rb") as src, open(target, "wb") as dst:
                dst.write(src.read())
            self._bal_window.show_message(
                _("Calendar file saved to:\n{}").format(target)
            )
        except Exception as e:
            _logger.error(f"saving .ics failed: {e}")
            self._bal_window.show_warning(
                _("Could not save the calendar file: {}").format(e)
            )


class BalCalendar:
    @staticmethod
    def write_temp_ics(content):
        fd, path = tempfile.mkstemp(prefix="event_", suffix=".ics")
        with os.fdopen(fd, "wb") as f:
            f.write(content.encode("utf-8"))
        return path

    @staticmethod
    def open_with_default_app(calendar_app, path):
        _logger.debug("opening calendar app")
        try:
            subprocess.check_call([calendar_app, path])
            return True
        except Exception as e:
            _logger.error(f"starting calendar app {e}")
            return False

    @staticmethod
    def desktop_dir():
        """Return the user's Desktop directory (Group D / D1b).

        Used as the initial folder of the "save .ics" dialog. On Windows this is
        normally ``C:\\Users\\<name>\\Desktop``; on Linux/macOS ``~/Desktop`` is
        used when it exists. If the Desktop cannot be located the home directory
        is returned as a safe fallback, so the save dialog always opens
        somewhere sensible.

        Returns:
            An absolute directory path (string).
        """
        home = os.path.expanduser("~")
        desktop = os.path.join(home, "Desktop")
        if os.path.isdir(desktop):
            return desktop
        return home


    @staticmethod
    def format_time(time):
        return time.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        #return time.astimezone(timezone.utc).strftime("%Y%m%d")

    @staticmethod
    def ical_escape(text: str) -> str:
        # escape per RFC5545: backslash, ; , newlines
        text = (
            text.replace("\\", "\\\\")
            .replace(";", "\\;")
            .replace(",", "\\,")
        )
        return "\r\n".join(
            BalCalendar.fold_ical_line(line)
            for line in text.split("\r\n")
        )

    @staticmethod
    def fold_ical_line(line: str, limit: int = 75) -> str:
        # ritorna linee separate da CRLF e folding con spazio iniziale sulle righe successive
        encoded = line.encode("utf-8")
        parts = []
        while len(encoded) > limit:
            # taglia senza spezzare byte UTF-8
            cut = limit
            while (encoded[cut] & 0xC0) == 0x80:  # byte di continuazione UTF-8
                cut -= 1
            parts.append(encoded[:cut].decode("utf-8"))
            encoded = encoded[cut:]
        parts.append(encoded.decode("utf-8"))
        return "\r\n ".join(parts)
