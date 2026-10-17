"""Tools > "Sign will offline...": the guided offline signing (#160).

The offline computer of a cold wallet (the keys) signs the will prepared by
the online computer (a watch-only wallet), carried on a USB stick. Before
#160 that meant the Will tab's "Import" and the Will Details window: the
will's whole history as coloured boxes, a choice among Sign / Broadcast /
Export / Invalidate / Merge, and nothing in plain words about what was
being signed. The owner asked for one entry in Electrum's Tools menu that
guides the user, with big buttons and drawings in the plugin's colours:

1. choose the will file on the stick (the plugin says why a file cannot be
   signed here: no keys, already signed, another wallet...);
2. check what the will gives to whom: the heirs with their names, amounts
   and addresses, the delivery date, the will-executors;
3. sign everything (one password) and save the signed file on the stick.

The logic lives in :mod:`bal.engine.offline_sign` (no Qt); this window only
shows it. The file is a copy: this wallet's own will is never touched and
nothing is sent from this computer.
"""

import html
from datetime import datetime, timezone

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ...core import datetext
from ...core.will import Will
from ...engine import offline_sign as engine
from ...i18n import N_, _
from .common import (
    IMPORT_FILE_FILTER,
    getOpenFileName,
    getSaveFileName,
    write_json_file,
)
from .dialogs import BalDialog, decode_will_payload
from .lists import MAIN_BUTTON_STYLE

#: The plugin's colours (``brand/BRAND.md``): the Safe21 teal and the dark
#: navy of the wordmark; the orange of the will summary's "something to do".
TEAL = "#0D9488"
NAVY = "#0A0F1C"
GREY = "#B8C2CC"
ORANGE = "#b26a00"

#: The big buttons of this window: the look of the Will tab's main button
#: (#133/#134), a little taller.
_BIG = MAIN_BUTTON_STYLE + "QPushButton{min-height:40px; font-size:11pt;}"

#: Below this a locktime is a block height, above it a UNIX time (Bitcoin's
#: own rule for nLockTime).
_LOCKTIME_THRESHOLD = 500_000_000

#: What each engine problem says to the user (see ``engine.file_problem``).
_PROBLEMS = {
    engine.NO_KEYS: N_(
        "This wallet has no private keys (watch-only): it cannot sign. "
        "Open the wallet that has the keys, then choose Tools > Sign will "
        "offline again."),
    engine.NOTHING_TO_SIGN: N_("This file holds no will to sign."),
    engine.ALREADY_SIGNED: N_(
        "This will is already signed: take the USB stick back to the "
        "online computer."),
    engine.OTHER_WALLET: N_(
        "This wallet cannot sign this will: it is the will of another "
        "wallet, or this wallet has already signed it."),
}

STEPS = 3


def drawing(kind, size=88):
    """One of the window's drawings, painted in the plugin's colours.

    ``kind``: ``"usb"`` (step 1, the stick), ``"check"`` (step 2, a sheet
    under a magnifier), ``"lock"`` (step 3, the signing), ``"done"`` (a
    tick) or ``"stop"`` (this wallet cannot sign). Painted, not loaded from
    an image file: no image format plugin is needed, and the drawings
    follow the brand colours exactly. Returns a :class:`QPixmap`.
    """
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(size / 100.0, size / 100.0)
    teal, navy, grey = QColor(TEAL), QColor(NAVY), QColor(GREY)
    white = QColor("#FFFFFF")

    def pen(colour, width):
        """A round-capped pen."""
        out = QPen(colour, width)
        out.setCapStyle(Qt.PenCapStyle.RoundCap)
        out.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        return out

    if kind == "usb":
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(grey)
        p.drawRoundedRect(QRectF(36, 6, 28, 32), 3, 3)
        p.setBrush(navy)
        p.drawRect(QRectF(42, 14, 6, 6))
        p.drawRect(QRectF(52, 14, 6, 6))
        p.setBrush(teal)
        p.drawRoundedRect(QRectF(26, 34, 48, 60), 10, 10)
        p.setBrush(white)
        p.drawRoundedRect(QRectF(38, 52, 24, 24), 5, 5)
        p.setPen(pen(teal, 4))
        p.drawLine(QPointF(44, 64), QPointF(48, 69))
        p.drawLine(QPointF(48, 69), QPointF(56, 58))
    elif kind == "check":
        p.setPen(pen(navy, 3))
        p.setBrush(white)
        p.drawRoundedRect(QRectF(16, 8, 54, 74), 5, 5)
        p.setPen(pen(grey, 5))
        for y in (24, 36, 48):
            p.drawLine(QPointF(26, y), QPointF(60, y))
        p.setPen(pen(teal, 7))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(62, 62), 15, 15)
        p.setPen(pen(navy, 9))
        p.drawLine(QPointF(73, 73), QPointF(88, 88))
    elif kind == "lock":
        p.setPen(pen(navy, 9))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(QRectF(30, 10, 40, 44), 0, 180 * 16)
        p.drawLine(QPointF(30, 32), QPointF(30, 46))
        p.drawLine(QPointF(70, 32), QPointF(70, 46))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(teal)
        p.drawRoundedRect(QRectF(20, 42, 60, 50), 9, 9)
        p.setBrush(white)
        p.drawEllipse(QPointF(50, 62), 7, 7)
        p.drawRect(QRectF(47, 64, 6, 16))
    elif kind == "done":
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(teal)
        p.drawEllipse(QPointF(50, 50), 44, 44)
        p.setPen(pen(white, 10))
        p.drawLine(QPointF(29, 52), QPointF(44, 67))
        p.drawLine(QPointF(44, 67), QPointF(72, 36))
    else:  # "stop"
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(ORANGE))
        p.drawEllipse(QPointF(50, 50), 44, 44)
        p.setPen(pen(white, 11))
        p.drawLine(QPointF(50, 26), QPointF(50, 56))
        p.drawPoint(QPointF(50, 74))
    p.end()
    return pix


def delivery_text(locktime):
    """The delivery date in the plugin's one date style (#134), or a block."""
    if locktime is None:
        return _("unknown")
    if locktime < _LOCKTIME_THRESHOLD:
        return _("block {}").format(locktime)
    return datetext.utc_text(locktime)


class OfflineSignDialog(BalDialog):
    """The guided window of Tools > "Sign will offline..." (see the module).

    Pages: choose the file, check it, then sign and save it, and a last
    page that says where the file was saved and what to do with it. A
    watch-only wallet gets a single page that says it cannot sign.
    """

    def __init__(self, bal_window):
        """Build the window for *bal_window*'s wallet."""
        BalDialog.__init__(self, bal_window.window, bal_window.bal_plugin,
                           _("Sign will offline"))
        self.bal_window = bal_window
        self.wallet = bal_window.wallet
        self.items = None
        self.signed = False
        self.saved_path = None
        self.setMinimumWidth(620)

        outer = QVBoxLayout(self)
        outer.setSpacing(14)
        head = QHBoxLayout()
        head.setSpacing(18)
        self.picture = QLabel()
        self.picture.setFixedSize(88, 88)
        head.addWidget(self.picture, 0, Qt.AlignmentFlag.AlignTop)
        column = QVBoxLayout()
        self.step_label = QLabel()
        self.step_label.setStyleSheet("color:{};".format(TEAL))
        column.addWidget(self.step_label)
        self.title = QLabel()
        font = QFont(self.title.font())
        font.setPointSizeF(font.pointSizeF() * 1.35)
        font.setBold(True)
        self.title.setFont(font)
        self.title.setWordWrap(True)
        column.addWidget(self.title)
        self.text = QLabel()
        self.text.setWordWrap(True)
        self.text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        column.addWidget(self.text)
        column.addStretch(1)
        head.addLayout(column, 1)
        outer.addLayout(head)

        self.pages = QStackedWidget()
        self.choose_page = self._button_page(
            _("Choose the will file…"), self.choose_file)
        self.check_page = self._check_page()
        self.save_page = self._button_page(
            _("Save on the USB stick…"), self.save_file)
        self.empty_page = QWidget()
        for page in (self.choose_page, self.check_page, self.save_page,
                     self.empty_page):
            self.pages.addWidget(page)
        outer.addWidget(self.pages, 1)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        self.close_button = QPushButton(_("Close"))
        self.close_button.clicked.connect(self.close)
        bottom.addWidget(self.close_button)
        outer.addLayout(bottom)

        if self.wallet.is_watching_only():
            self._show("stop", None, _("This wallet cannot sign"),
                       _(_PROBLEMS[engine.NO_KEYS]), self.empty_page)
        else:
            self._show_choose()

    # -- building ------------------------------------------------------------

    def _button_page(self, label, slot):
        """A page with one big button."""
        page = QWidget()
        column = QVBoxLayout(page)
        # Under the text, aligned with it (the drawing is 88 px wide).
        column.setContentsMargins(106, 0, 0, 0)
        box = QHBoxLayout()
        button = QPushButton(label)
        button.setStyleSheet(_BIG)
        button.clicked.connect(slot)
        box.addWidget(button)
        box.addStretch(1)
        column.addLayout(box)
        column.addStretch(1)
        page.button = button
        return page

    def _check_page(self):
        """Step 2: what the will gives to whom, and "Sign all"."""
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(0, 0, 0, 0)
        self.details = QLabel()
        self.details.setWordWrap(True)
        self.details.setTextFormat(Qt.TextFormat.RichText)
        self.details.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self.details.setAlignment(Qt.AlignmentFlag.AlignTop)
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setMinimumHeight(220)
        area.setWidget(self.details)
        box.addWidget(area, 1)
        row = QHBoxLayout()
        self.sign_button = QPushButton(_("Sign all"))
        self.sign_button.setStyleSheet(_BIG)
        self.sign_button.clicked.connect(self.sign_all)
        row.addWidget(self.sign_button)
        self.other_button = QPushButton(_("Choose another file"))
        self.other_button.clicked.connect(self._show_choose)
        row.addWidget(self.other_button)
        row.addStretch(1)
        box.addLayout(row)
        return page

    def _show(self, picture, step, title, text, page):
        """Show one page: its drawing, "Step N of 3", title and text."""
        self.picture.setPixmap(drawing(picture))
        self.step_label.setText(
            _("Step {} of {}").format(step, STEPS) if step else "")
        self.step_label.setVisible(bool(step))
        self.title.setText(title)
        self.text.setText(text)
        self.pages.setCurrentWidget(page)

    # -- step 1: the file ----------------------------------------------------

    def _show_choose(self):
        """Step 1: insert the stick and choose the will file."""
        self.items = None
        self._show(
            "usb", 1, _("Choose the will file"),
            _("Insert the USB stick with the will prepared on the online "
              "computer, then choose its file.\nThis window only signs: "
              "nothing is sent from this computer and this wallet's own "
              "will is not changed."),
            self.choose_page)

    def ask_open_path(self):
        """The file window of step 1 (a method, so the tests replace it)."""
        return getOpenFileName(
            parent=self, title=_("Choose the will file"),
            filter=IMPORT_FILE_FILTER, config=self.bal_window.window.config)

    def choose_file(self):
        """Read the chosen file; go to step 2, or say why it cannot be signed."""
        path = self.ask_open_path()
        if path:
            self.load_file(path)

    def load_file(self, path):
        """Load the will file at *path* and show step 2 (True), or say why
        not (False): not readable, not a will file, or one of the engine's
        problems (:func:`bal.engine.offline_sign.file_problem`)."""
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
        except Exception as e:
            self.show_error(_("The file cannot be read: {}").format(e))
            return False
        kind, data = decode_will_payload(text)
        if kind != "will":
            self.show_error(_(
                "This is not a will file. Choose the will file saved by the "
                "plugin on the online computer."))
            return False
        try:
            items = self.bal_window._load_will_payload(data)
            # The inputs' wallet data, so the wallet can sign, as the
            # Will tab's import does (``import_will_into_details``).
            Will.normalize_will(items, self.wallet)
            for wi in items.values():
                wi.set_status("IMPORTED", True)
        except Exception as e:
            self.show_error(_("The file cannot be read: {}").format(e))
            return False
        problem = engine.file_problem(self.wallet, items)
        if problem is not None:
            self.show_error(_(_PROBLEMS[problem]))
            return False
        self.items = items
        self._show_check()
        return True

    # -- step 2: check -------------------------------------------------------

    def _amount(self, sats):
        """An amount in the wallet's unit (e.g. "0.00598058 BTC")."""
        return self.bal_window.window.format_amount_and_units(sats)

    def details_html(self):
        """The rich text of step 2, from :func:`engine.file_summary`."""
        s = engine.file_summary(self.items)
        esc = html.escape
        parts = ["<p><b>{}</b></p><table cellspacing='0' cellpadding='3'>".format(
            esc(_("Heirs")))]
        for heir in s.heirs:
            if heir.sats is None:
                amount = _("nothing (below the dust limit)")
            elif heir.sats_max is not None:
                amount = _("{} to {}").format(
                    self._amount(heir.sats), self._amount(heir.sats_max))
            else:
                amount = self._amount(heir.sats)
            parts.append(
                "<tr><td><b>{}</b></td><td>&nbsp;{}</td></tr>"
                "<tr><td colspan='2' style='color:#555555;'>"
                "<tt>{}</tt></td></tr>".format(
                    esc(heir.name), esc(amount), esc(heir.address)))
        parts.append("</table>")
        if s.messages:
            parts.append("<p><b>{}</b><br>{}</p>".format(
                esc(_("Message in the will")),
                "<br>".join(esc(m) for m in s.messages)))
        parts.append("<p><b>{}</b><br>{}</p>".format(
            esc(_("Delivery to the heirs")), esc(delivery_text(s.delivery))))
        lines = []
        for url, fee in s.executors:
            if url is None:
                lines.append(esc(_("Backup transaction (no will-executor)")))
            elif fee is None:
                lines.append(esc(url))
            else:
                lines.append(esc(_("{} (fee {})").format(url, self._amount(fee))))
        parts.append("<p><b>{}</b><br>{}</p>".format(
            esc(_("Will-executors")), "<br>".join(lines)))
        parts.append("<p>{}</p>".format(esc(
            _("Transactions to sign: {} of {}").format(s.to_sign, s.copies))))
        return "".join(parts)

    def _show_check(self):
        """Step 2: check what the will gives to whom before signing."""
        self.details.setText(self.details_html())
        self._show(
            "check", 2, _("Check before signing"),
            _("Make sure the heirs, the amounts and the date are the ones "
              "you chose."),
            self.check_page)

    # -- step 3: sign and save -----------------------------------------------

    def sign_all(self):
        """Ask the password once and sign every copy still to sign."""
        password = self.bal_window.get_wallet_password(
            message=_("Enter your wallet password to sign the will."),
            parent=self)
        if password is False:
            return
        self.bal_window.sign_will_file(self.items, password, self.after_signing)

    def after_signing(self, done):
        """The signing ended: say how it went, then offer to save.

        *done* is ``(result, outcome)`` of :func:`engine.sign_file`. An
        error is shown; what was signed before it is kept and can be saved.
        """
        result, outcome = done
        if result.error is not None:
            self.show_error(
                _("Could not sign the transaction: {}").format(result.error))
        if not result.signed:
            return
        self.signed = True
        total = outcome.signed + len(outcome.partial) + outcome.unsigned
        if outcome.partial:
            have, required = min(outcome.partial)
            text = _("{} of {} signatures: another signer must sign.").format(
                have, required)
        else:
            text = _("Signed {} of {} transactions.").format(outcome.signed, total)
        text += "\n" + _("Now save the signed will on the USB stick.")
        self._show("lock", 3, _("Will signed"), text, self.save_page)

    def default_file_name(self):
        """``<wallet>-will-signed-<YYYY-MM-DD>.json`` (translated words)."""
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return "{}-{}-{}.json".format(str(self.wallet), _("will-signed"), day)

    def ask_save_path(self):
        """The file window of step 3 (a method, so the tests replace it)."""
        return getSaveFileName(
            parent=self, title=_("Save the signed will"),
            filename=self.default_file_name(), filter=IMPORT_FILE_FILTER,
            default_extension="json", config=self.bal_window.window.config)

    def save_file(self):
        """Save the signed file and show the last page (what to do next)."""
        path = self.ask_save_path()
        if not path:
            return
        try:
            write_json_file(path, engine.signed_file_data(self.items))
        except Exception as e:
            self.show_error(_("The file cannot be saved: {}").format(e))
            return
        self.saved_path = path
        self._show(
            "done", None, _("Done"),
            _("The signed will is saved in:\n{}\n\nTake the USB stick back "
              "to the online computer. There, in the Will tab menu (≡), "
              "choose Merge and this file, then press Check: the will is "
              "sent to the will-executors.").format(path),
            self.empty_page)

    def may_close(self):
        """False when the user keeps a will signed and not saved yet open."""
        if not self.signed or self.saved_path:
            return True
        return bool(self.question(_(
            "The signed will is not saved yet: if you close now you will "
            "have to sign it again. Close anyway?")))

    def closeEvent(self, event):  # noqa: N802 - Qt's name
        """Ask before closing a will signed and not saved yet."""
        if not self.may_close():
            event.ignore()
            return
        super().closeEvent(event)

    def reject(self):
        """Esc: the same question as the close button."""
        if self.may_close():
            super().reject()
