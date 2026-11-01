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

#161 (owner): #160 guided only the offline half; on the online computer
the user still had to know "Export, choose a filter, save the file, carry
it, Merge, Check". The same entry, renamed Tools > "Sign the will
offline...", now opens the window that fits the wallet, and the whole
route has five steps (owner): 1 prepare the USB stick (online), 2 take it
to the offline PC, 3 sign (offline), 4 bring it back to the online PC,
5 send (online):

* a wallet with keys: :class:`OfflineSignDialog`, step 3 (the window of
  #160);
* a watch-only wallet (the online computer): :class:`OnlineSignDialog`,
  steps 1, 2, 4 and 5 (the Check prepares the will when needed, then only
  the copies to sign are saved; the signed file is merged and the Check
  sends and verifies it).

After the owner's ZIP tests: one screen, one thing to do. Each screen
says where it happens ("On THIS computer" / "On the OTHER computer"),
gives numbered instructions in a larger font and at most one big button;
the five-step bar (:class:`StepBar`) ticks the steps done. The window is
opened by Electrum's Tools menu and the Will tab's Tools menu, the will
summary's button and the wizard, always on the step to make now. (A big
button of the Will tab said the next step, but it was not there on the
offline computer and did not follow the steps: removed, owner. The
instructions name the Tools menu, which the owner always finds.)

Owner, after the next ZIP test: online and offline must look different.
Each computer has its colour (teal online, orange offline) in its drawing,
the step bar and the place label; the route starts with a map of the five
steps ("What we will do", :func:`route_map`), which "See the plan" shows
again at any time.

#209 (owner): "the reference is the bitcoin address on paper, which cannot be
changed". The check lists every payment read from the transactions
(:func:`check_html`); "Sign all" stays off until the user ticks that the
addresses were compared, and for good when the file pays what the will does
not say. :class:`SigningWarningDialog` is the same check for a signing outside
this window (:meth:`bal.gui.qt.window.BalWindow.confirm_signing`).
"""

import html
import re
from datetime import datetime, timezone

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QPainter,
    QPalette,
    QPen,
    QPixmap,
    QPolygonF,
)
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ...core import datetext
from ...core.will import Will
from ...engine import offline_sign as engine
from ...engine.sign import signing_outcome
from ...i18n import N_, _
from .common import (
    IMPORT_FILE_FILTER,
    getOpenFileName,
    getSaveFileName,
    rich_text,
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

#: #161 (owner: "online and offline should look different"): each computer
#: has its colour, the same in the drawings, the step bar, the place label
#: and the route map: teal for the online computer, orange for the offline
#: one, grey for the USB stick on its way. Each is ``(solid, tint, pale,
#: dark)``: the step to make now, a step done, a step still to make, text.
ONLINE_COLOURS = (TEAL, "#E0F2EF", "#9ED8CF", "#0B6B62")
OFFLINE_COLOURS = ("#D97706", "#FDF0DC", "#F2C98A", "#8A5A00")
CARRY_COLOURS = ("#64748B", "#EEF1F4", "#C3CCD6", "#475569")

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
        "Open the wallet that has the keys, then choose Tools > Sign the "
        "will offline again."),
    engine.NOTHING_TO_SIGN: N_("This file holds no will to sign."),
    engine.ALREADY_SIGNED: N_(
        "This will is already signed: take the USB stick back to the "
        "online computer."),
    engine.OTHER_WALLET: N_(
        "This wallet cannot sign this will: it is the will of another "
        "wallet, or this wallet has already signed it."),
    # #161: step 3 on the online computer.
    engine.NOT_THIS_WALLET: N_(
        "This will file belongs to another wallet: choose the file of this "
        "wallet's will."),
}

def _paint_computer(p, offline, stick):
    """Paint a computer on *p* (a 100 x 100 square), see :func:`drawing`.

    The online computer is teal with the waves of a network on its screen,
    the offline one orange with a padlock (no network). *stick*: a USB
    stick next to it (the stick goes to that computer).
    """
    colour = QColor(OFFLINE_COLOURS[0] if offline else ONLINE_COLOURS[0])
    white = QColor("#FFFFFF")
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(colour)
    p.drawRoundedRect(QRectF(6, 10, 88, 60), 8, 8)
    p.drawRect(QRectF(44, 70, 12, 12))
    p.drawRoundedRect(QRectF(28, 82, 44, 7), 3, 3)
    if offline:
        pen = QPen(white, 5)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(QRectF(41, 20, 18, 24), 0, 180 * 16)
        p.drawLine(QPointF(41, 32), QPointF(41, 36))
        p.drawLine(QPointF(59, 32), QPointF(59, 36))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(white)
        p.drawRoundedRect(QRectF(35, 34, 30, 24), 4, 4)
        p.setBrush(colour)
        p.drawEllipse(QPointF(50, 44), 3.5, 3.5)
        p.drawRect(QRectF(48.5, 45, 3, 8))
    else:
        pen = QPen(white, 6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        for radius in (26, 15):
            p.drawArc(QRectF(50 - radius, 56 - radius, 2 * radius, 2 * radius),
                      45 * 16, 90 * 16)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(white)
        p.drawEllipse(QPointF(50, 54), 4.5, 4.5)
    if stick:
        # A white outline keeps the stick apart from the computer under it.
        p.setPen(QPen(white, 2))
        p.setBrush(QColor(NAVY))
        p.drawRoundedRect(QRectF(64, 62, 30, 17), 3, 3)
        p.setBrush(QColor(GREY))
        p.drawRect(QRectF(56, 65, 9, 11))


def drawing(kind, size=88):
    """One of the window's drawings, painted in the plugin's colours.

    ``kind``: ``"usb"`` (step 1, the stick), ``"check"`` (step 2, a sheet
    under a magnifier), ``"lock"`` (step 3, the signing), ``"done"`` (a
    tick), ``"stop"`` (this wallet cannot sign) or, #161, ``"send"`` (the
    online computer sends the will: a sheet flying right). #161 (owner):
    the computer where the screen's step happens, ``"online"`` (teal,
    network) or ``"offline"`` (orange, padlock), and ``"to_online"`` /
    ``"to_offline"`` with the USB stick that goes there. Painted, not
    loaded from an image file: no image format plugin is needed, and the
    drawings follow the brand colours exactly. Returns a :class:`QPixmap`.
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

    if kind in ("online", "offline", "to_online", "to_offline"):
        _paint_computer(p, kind.endswith("offline"), kind.startswith("to_"))
    elif kind == "usb":
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
    elif kind == "send":
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(teal)
        p.drawEllipse(QPointF(50, 50), 44, 44)
        p.setBrush(white)
        p.drawPolygon(QPolygonF([QPointF(24, 48), QPointF(76, 28),
                                 QPointF(60, 76), QPointF(50, 56)]))
        p.setPen(pen(teal, 4))
        p.drawLine(QPointF(50, 56), QPointF(76, 28))
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


def signing_warning():
    """What the user must do before any signature (#209, owner's words)."""
    return _("Compare the addresses with the ones you wrote on paper. The "
             "plugin cannot know whether they are the right ones: if the "
             "online computer has been hacked, the will may contain false "
             "addresses. Once signed, you will not be able to notice.")


def _least(amount, line, info=""):
    """The amount of *line* (``sats``, ``sats_max``) in words.

    Owner (ZIP test, #160): "from ... to ..." was not clear. Each
    will-executor's copy pays its own fee, so the amount depends a little on
    who delivers the will: show the least, *info* says why.
    """
    if line.sats is None:
        return html.escape(_("nothing (below the dust limit)"))
    if line.sats_max is not None:
        return html.escape(_("at least {}").format(amount(line.sats))) + info
    return html.escape(amount(line.sats))


def _output_rows(amount, name, line, info=""):
    """Two rows of a table: the name and the amount, then the full address."""
    return ("<tr><td><b>{}</b></td><td>&nbsp;{}</td></tr>"
            "<tr><td colspan='2' style='color:#555555;'><tt>{}</tt></td></tr>"
            ).format(html.escape(name), _least(amount, line, info),
                     html.escape(line.address))


def _delivery_html(amount, delivery, info):
    """The rich text of one delivery of the check (see :func:`check_html`)."""
    esc = html.escape
    parts = ["<p><b>{}</b><br>{}</p>".format(
        esc(_("Delivery to the heirs")), esc(delivery_text(delivery.locktime)))]
    parts.append("<p><b>{}</b></p><table cellspacing='0' cellpadding='3'>{}</table>"
                 .format(esc(_("Heirs")), "".join(
                     _output_rows(amount, heir.name, heir, info)
                     for heir in delivery.heirs)))
    if delivery.messages:
        parts.append("<p><b>{}</b><br>{}</p>".format(
            esc(_("Message in the will")),
            "<br>".join(esc(m) for m in delivery.messages)))
    rows = "".join(_output_rows(amount, line.url or "", line)
                   for line in delivery.executors)
    if delivery.backup:
        rows += "<tr><td colspan='2'>{}</td></tr>".format(
            esc(_("Backup transaction (no will-executor)")))
    parts.append("<p><b>{}</b></p><table cellspacing='0' cellpadding='3'>{}</table>"
                 .format(esc(_("Will-executors")), rows))
    if delivery.change:
        parts.append(
            "<p><b>{}</b></p><table cellspacing='0' cellpadding='3'>{}</table>".format(
                esc(_("Returns to your wallet")),
                "".join(_output_rows(amount, "", line) for line in delivery.change)))
    if delivery.miner_fee is not None:
        fee = amount(delivery.miner_fee)
        if delivery.miner_fee_max not in (None, delivery.miner_fee):
            fee = _("from {} to {}").format(fee, amount(delivery.miner_fee_max))
        parts.append("<p><b>{}</b><br>{}</p>".format(esc(_("Miner fee")), esc(fee)))
    return "".join(parts)


def _no_lonely_point(text):
    """*text* without the point Electrum leaves after a whole amount.

    Electrum writes zero as "0. mBTC" and one as "1. mBTC". The owner's try
    of #209: an heir left with nothing read "at least 0. mBTC".
    """
    return re.sub(r"\.(?=\s|$)", "", text)


def check_html(summary, amount, info=""):
    """The rich text of the check: what the copies to sign really pay (#209).

    *summary*: :func:`bal.engine.offline_sign.file_summary`; *amount*: an
    amount in the wallet's unit; *info*: what follows an amount that depends
    on the will-executor (the small "ⓘ"). A delivery after the other, with
    the problems that stop the signing on top, in red.
    """
    esc = html.escape
    wallet_amount = amount

    def amount(sats):
        """The amount as the wallet writes it, without a lonely point."""
        return _no_lonely_point(wallet_amount(sats))

    parts = []
    if summary.problems:
        parts.append("<p style='color:#B00020;'><b>{}</b><br>{}</p>".format(
            esc(_("Do not sign: the will's money does not go where it should.")),
            "<br>".join(esc(line) for line in engine.problem_lines(summary.problems))))
    many = len(summary.deliveries) > 1
    for number, delivery in enumerate(summary.deliveries, 1):
        if many:
            parts.append("<h3>{}</h3>".format(esc(_("Delivery {}").format(number))))
        parts.append(_delivery_html(amount, delivery, info))
    parts.append("<p>{}</p>".format(esc(
        _("Transactions to sign: {} of {}").format(summary.to_sign, summary.copies))))
    return "".join(parts)


class SigningWarningDialog(QDialog):
    """Before a signing outside the guided window: the check and a tick (#209).

    The owner: the plugin must warn "when signing offline, and probably
    always". It shows what is about to be signed, and "Sign" stays off until
    the user ticks that the addresses were compared.
    """

    def __init__(self, parent, details):
        """Build the window; *details*: the rich text of :func:`check_html`."""
        QDialog.__init__(self, parent)
        self.setWindowTitle(_("Check before signing"))
        self.setMinimumWidth(560)
        column = QVBoxLayout(self)
        label = QLabel(details)
        label.setWordWrap(True)
        label.setTextFormat(Qt.TextFormat.RichText)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        label.setAlignment(Qt.AlignmentFlag.AlignTop)
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setMinimumHeight(320)
        area.setWidget(label)
        column.addWidget(area, 1)
        warning = QLabel(signing_warning())
        warning.setWordWrap(True)
        column.addWidget(warning)
        self.tick = QCheckBox(_("I compared the addresses with the ones on paper"))
        column.addWidget(self.tick)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton(_("Cancel"))
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        self.sign_button = QPushButton(_("Sign"))
        self.sign_button.setEnabled(False)
        self.tick.toggled.connect(self.sign_button.setEnabled)
        self.sign_button.clicked.connect(self.accept)
        row.addWidget(self.sign_button)
        column.addLayout(row)


def is_multisig(wallet):
    """True for a multisig wallet (Electrum's ``Multisig_Wallet`` has ``n``
    cosigners): its signatures are made on the cosigners' computers."""
    return getattr(wallet, "n", 1) > 1


def missing_text(missing):
    """"1 more signature needed" / "N more signatures needed" (a multisig).

    Two texts, so each language says one and many correctly.
    """
    if missing == 1:
        return _("1 more signature needed")
    return _("{} more signatures needed").format(missing)


def carry_text(multisig):
    """The name of step 2: the USB stick goes to the offline computer (to
    the cosigners' computers for a multisig)."""
    return (_("Take it to the cosigner's PC") if multisig
            else _("Take it to the offline PC"))


#: The colours of each step (see :data:`ONLINE_COLOURS`).
_STEP_COLOURS = {
    engine.STEP_PREPARE: ONLINE_COLOURS,
    engine.STEP_CARRY: CARRY_COLOURS,
    engine.STEP_SIGN: OFFLINE_COLOURS,
    engine.STEP_BRING_BACK: CARRY_COLOURS,
    engine.STEP_SEND: ONLINE_COLOURS,
}

#: The size of the route map's drawing (:func:`route_map`), before scaling.
_MAP_WIDTH, _MAP_HEIGHT = 680, 490


def route_map(multisig=False, ink=None, width=600):
    """The map of the whole route (#161, owner: "a picture of what we are
    going to do, before we start").

    The two computers at the top (online teal with the network, offline
    orange with the padlock; the cosigners' computers for a *multisig*),
    under them the steps where they happen: 1 Prepare and 5 Send on the
    online side, 3 Sign on the offline side, and the USB stick travelling
    between them (2 and 4). *ink*: the colour of the text and lines out of
    the boxes (the window's text colour, so it reads on a dark theme too).
    Painted at twice the size for sharp screens; *width* in screen points.
    Returns a :class:`QPixmap`.
    """
    ink = QColor(ink if ink is not None else NAVY)
    scale = width / float(_MAP_WIDTH)
    ratio = 2.0
    pix = QPixmap(int(width * ratio), int(_MAP_HEIGHT * scale * ratio))
    pix.setDevicePixelRatio(ratio)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    p.scale(scale, scale)

    def text(rect, string, size, bold=False, colour=ink):
        """*string* centred in *rect*, a smaller font when it is too long
        (the translations are longer than the English)."""
        font = QFont()
        font.setBold(bold)
        font.setPixelSize(size)
        while size > 9 and QFontMetrics(font).horizontalAdvance(string) > rect.width() - 8:
            size -= 1
            font.setPixelSize(size)
        p.setFont(font)
        p.setPen(colour)
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, string)

    def computer(x, offline, name, wallet):
        """A computer (see :func:`drawing`) with its name and wallet."""
        p.save()
        p.translate(x, 0)
        p.scale(1.4, 1.4)
        _paint_computer(p, offline, False)
        p.restore()
        text(QRectF(x - 45, 130, 230, 24), name, 18, bold=True)
        text(QRectF(x - 45, 154, 230, 22), wallet, 15)

    def box(x, y, colours, title, sub):
        """A step where it happens, in the colours of that computer."""
        solid, tint, _pale, dark = colours
        p.setPen(QPen(QColor(solid), 1.5))
        p.setBrush(QColor(tint))
        p.drawRoundedRect(QRectF(x, y, 230, 60), 8, 8)
        text(QRectF(x, y + 5, 230, 28), title, 17, bold=True, colour=QColor(dark))
        text(QRectF(x, y + 32, 230, 24), sub, 15, colour=QColor(dark))

    def travel(y, x_from, x_to, label):
        """The USB stick going from one side to the other (an arrow)."""
        pen = QPen(ink, 1.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPolyline(QPolygonF([QPointF(x_from, y - 30), QPointF(x_from, y),
                                  QPointF(x_to, y), QPointF(x_to, y + 24)]))
        p.setBrush(ink)
        p.drawPolygon(QPolygonF([QPointF(x_to - 6, y + 22), QPointF(x_to + 6, y + 22),
                                 QPointF(x_to, y + 30)]))
        # The stick on the arrow, its plug towards where it goes.
        p.setPen(QPen(ink, 1))
        p.setBrush(QColor(GREY))
        p.drawRoundedRect(QRectF(327, y - 7, 26, 14), 3, 3)
        p.setBrush(QColor(NAVY))
        plug = 353 if x_to > x_from else 319
        p.drawRect(QRectF(plug, y - 4, 8, 8))
        text(QRectF(170, y - 28, 340, 22), label, 15)

    if multisig:
        offline_name = _("Cosigners' PCs")
        offline_wallet = _("wallets with the keys")
        sign_sub = _("every cosigner signs in turn")
    else:
        offline_name = _("Offline PC")
        offline_wallet = _("wallet with the keys")
        sign_sub = _("check and sign everything")
    computer(90, False, _("Online PC"), _("watch-only wallet"))
    computer(450, True, offline_name, offline_wallet)
    box(45, 182, ONLINE_COLOURS, "1 · " + _("Prepare the USB stick"),
        _("save the file to sign"))
    travel(272, 160, 520, "2 · " + carry_text(multisig))
    box(405, 302, OFFLINE_COLOURS, "3 · " + _("Sign"), sign_sub)
    travel(392, 520, 160, "4 · " + _("Bring it back to the online PC"))
    box(45, 422, ONLINE_COLOURS, "5 · " + _("Send"),
        _("to the will-executors"))
    p.end()
    return pix


class StepBar(QWidget):
    """The five steps of the route at the top of both windows (#161, owner).

    "1 Prepare the USB stick", "2 Take it to the offline PC", "3 Sign",
    "4 Bring it back to the online PC", "5 Send": each step has the colour
    of its computer (:data:`_STEP_COLOURS`: 1 and 5 teal, online; 3
    orange, offline; 2 and 4 grey, the stick travelling). The step to make
    now is filled with its colour and says "you are here", the steps done
    have a tick, the others are pale (:meth:`mark`). Step 3 can show a
    count of signatures ("2 of 3", a multisig: :meth:`set_count`). Owner:
    the route had three boxes and did not say that the stick must travel;
    carrying it is now a step of its own, both ways.
    """

    def __init__(self, multisig=False, parent=None):
        """Build the five steps; *multisig* names the cosigners' computers."""
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        texts = (
            (engine.STEP_PREPARE, _("Prepare the USB stick")),
            (engine.STEP_CARRY, carry_text(multisig)),
            (engine.STEP_SIGN, _("Sign")),
            (engine.STEP_BRING_BACK, _("Bring it back to the online PC")),
            (engine.STEP_SEND, _("Send")),
        )
        self.boxes, self.numbers, self.texts, self.here = {}, {}, {}, {}
        self.count = QLabel()
        self.current = None
        for step, text in texts:
            box = QFrame()
            box.setObjectName("routeStep")
            column = QVBoxLayout(box)
            column.setContentsMargins(4, 6, 4, 6)
            column.setSpacing(1)
            # The step number in bold (rich text, see mark()).
            number = rich_text(QLabel())
            number.setAlignment(Qt.AlignmentFlag.AlignCenter)
            column.addWidget(number)
            label = QLabel(text)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setWordWrap(True)
            column.addWidget(label)
            if step == engine.STEP_SIGN:
                self.count.setAlignment(Qt.AlignmentFlag.AlignCenter)
                self.count.hide()
                column.addWidget(self.count)
            here = QLabel(_("you are here"))
            here.setAlignment(Qt.AlignmentFlag.AlignCenter)
            here.hide()
            column.addWidget(here)
            column.addStretch(1)
            row.addWidget(box, 1)
            self.boxes[step], self.numbers[step], self.texts[step] = box, number, label
            self.here[step] = here
        self.mark(None)

    def mark(self, current, all_done=False):
        """Mark *current* as the step to make now (the earlier ones done).

        ``current`` None: no step marked, all grey; ``all_done``: every
        step ticked (the will is signed and sent).
        """
        self.current = current
        for step, box in self.boxes.items():
            done = all_done or (current is not None and step < current)
            now = not all_done and step == current
            solid, tint, pale, dark = _STEP_COLOURS[step]
            if now:
                frame, width, colour, background = solid, 2, "#FFFFFF", solid
            elif done:
                frame, width, colour, background = solid, 1, dark, tint
            else:
                frame, width, colour, background = pale, 1, "#8A939C", "transparent"
            box.setStyleSheet(
                "QFrame#routeStep{{border:{}px solid {}; border-radius:8px;"
                " background:{};}} QFrame#routeStep QLabel{{color:{};"
                " background:transparent;}}".format(width, frame, background, colour))
            self.here[step].setVisible(now)
            self.numbers[step].setText(
                "<b style='font-size:13pt'>{}{}</b>".format(
                    "✓ " if done else "", step))

    def set_count(self, have=None, required=None):
        """Show "N of M" signatures under step 3 (None: hidden)."""
        if required:
            self.count.setText(_("{} of {}").format(have or 0, required))
            self.count.show()
        else:
            self.count.hide()


#: The link of the "ⓘ" after an amount that depends on the will-executor.
_AMOUNT_INFO_LINK = "bal:amount-info"

#: #161: where the step to make now happens (owner: the window never said
#: on which computer to act), in the colour of that computer (owner: online
#: and offline must look different): teal online, orange offline.
_WHERE_STYLE = ("QLabel{{font-weight:bold; padding:4px 10px; border-radius:6px;"
                " background:{}; color:{};}}")
ONLINE = "online"
OFFLINE = "offline"


class _GuidedDialog(BalDialog):
    """What the two windows share (#161): one screen, one thing to do.

    From the top: the five-step bar, the place ("On THIS computer", "On
    the OTHER computer ...", in the colour of the computer where the step
    happens), the drawing of that computer with a line of result (green
    tick or orange warning) and the file saved, a big title, the
    instructions as numbered lines in a larger font, then at most ONE big
    button (the page of the screen) and a small link to go back or
    forward, "See the plan" and "Close" (owner: "one screen, one thing").
    "See the plan" shows the map of the whole route (:meth:`show_map`).
    Also the reading of a will file.
    """

    def __init__(self, bal_window):
        """Build the frame for *bal_window*'s wallet."""
        BalDialog.__init__(self, bal_window.window, bal_window.bal_plugin,
                           _("Sign the will offline"))
        self.bal_window = bal_window
        self.wallet = bal_window.wallet
        self.setMinimumWidth(680)

        outer = QVBoxLayout(self)
        outer.setSpacing(12)
        self.bar = StepBar(multisig=is_multisig(self.wallet))
        outer.addWidget(self.bar)
        head = QHBoxLayout()
        head.setSpacing(18)
        self.picture = QLabel()
        self.picture.setFixedSize(88, 88)
        head.addWidget(self.picture, 0, Qt.AlignmentFlag.AlignTop)
        column = QVBoxLayout()
        column.setSpacing(8)
        self.where = QLabel()
        # Owner: the place must be seen at once (about half as big again).
        font = QFont(self.where.font())
        font.setPointSizeF(font.pointSizeF() * 1.5)
        self.where.setFont(font)
        where_row = QHBoxLayout()
        where_row.addWidget(self.where)
        where_row.addStretch(1)
        column.addLayout(where_row)
        # The result line: a coloured tick or warning (rich text, see screen()).
        self.result = rich_text(QLabel())
        self.result.setWordWrap(True)
        self.result.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        column.addWidget(self.result)
        self.title = QLabel()
        font = QFont(self.title.font())
        font.setPointSizeF(font.pointSizeF() * 1.45)
        font.setBold(True)
        self.title.setFont(font)
        self.title.setWordWrap(True)
        column.addWidget(self.title)
        # Owner: "what to do now" must be easy to read: a larger font and
        # one numbered line per action.
        self.text = QLabel()
        self.text.setWordWrap(True)
        self.text.setTextFormat(Qt.TextFormat.RichText)
        font = QFont(self.text.font())
        font.setPointSizeF(font.pointSizeF() * 1.3)
        self.text.setFont(font)
        self.text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        column.addWidget(self.text)
        column.addStretch(1)
        head.addLayout(column, 1)
        outer.addLayout(head)

        self.pages = QStackedWidget()
        outer.addWidget(self.pages, 1)
        self.empty_page = QWidget()
        self.pages.addWidget(self.empty_page)
        self.map_page = self._map_page()
        # The screen shown before the map, to go back to (:meth:`show_map`).
        self._last_screen = None

        bottom = QHBoxLayout()
        # A small link to the step before or after (never a second big
        # button: one thing to do per screen).
        self.link = QPushButton()
        self.link.setFlat(True)
        self.link.setStyleSheet(
            "QPushButton{color:#1F6FB2; border:none; text-decoration:underline;"
            " padding:2px;}")
        self.link.clicked.connect(lambda: self._link_slot and self._link_slot())
        self._link_slot = None
        self.link.hide()
        bottom.addWidget(self.link)
        self.map_link = QPushButton(_("See the plan"))
        self.map_link.setFlat(True)
        self.map_link.setStyleSheet(self.link.styleSheet())
        self.map_link.clicked.connect(lambda: self.show_map())
        bottom.addWidget(self.map_link)
        bottom.addStretch(1)
        self.close_button = QPushButton(_("Close"))
        self.close_button.clicked.connect(self.close)
        bottom.addWidget(self.close_button)
        # Owner (ZIP test): the links at the bottom were too small to see.
        for widget in (self.link, self.map_link, self.close_button):
            font = QFont(widget.font())
            font.setPointSizeF(font.pointSizeF() * 1.25)
            widget.setFont(font)
        outer.addLayout(bottom)

    def _button_page(self, label, slot):
        """A page with one big button, under the text (aligned with it)."""
        page = QWidget()
        column = QVBoxLayout(page)
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
        self.pages.addWidget(page)
        return page

    def _map_page(self):
        """The page of the route map: the map and one big button ("Start"
        or "Back", see :meth:`show_map`)."""
        page = QWidget()
        column = QVBoxLayout(page)
        column.setContentsMargins(0, 0, 0, 0)
        self.map_label = QLabel()
        self.map_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        column.addWidget(self.map_label)
        row = QHBoxLayout()
        row.addStretch(1)
        self.map_button = QPushButton()
        self.map_button.setStyleSheet(_BIG)
        self.map_button.clicked.connect(lambda: self._map_slot and self._map_slot())
        self._map_slot = None
        row.addWidget(self.map_button)
        row.addStretch(1)
        column.addLayout(row)
        column.addStretch(1)
        self.pages.addWidget(page)
        return page

    def _show_page(self, page):
        """Show *page* and fit the window's height to the screen.

        Owner (ZIP test): the window was far too tall, and stayed so after
        "See the plan". A :class:`QStackedWidget` asks for the room of its
        biggest page (the map), so the pages not shown are made to ask for
        none; then the window is fitted once the new screen is laid out.
        """
        for i in range(self.pages.count()):
            other = self.pages.widget(i)
            policy = (QSizePolicy.Policy.Preferred if other is page
                      else QSizePolicy.Policy.Ignored)
            other.setSizePolicy(policy, policy)
        self.pages.setCurrentWidget(page)
        self.pages.updateGeometry()
        QTimer.singleShot(0, self.fit_height)

    def fit_height(self):
        """Fit the window's height to the screen shown (its width kept):
        the words wrap at the current width, so the layout's height for
        that width is used."""
        layout = self.layout()
        layout.activate()
        if layout.hasHeightForWidth():
            height = layout.totalHeightForWidth(self.width())
        else:
            height = layout.totalSizeHint().height()
        self.resize(self.width(), max(height, self.minimumSizeHint().height()))

    def show_map(self, start=None):
        """The map of the whole route ("What we will do", #161 owner).

        *start*: the first screen of the route opens on the map, and its
        button "Start" calls *start*. Otherwise (the link "See the plan")
        the button "Back" shows again the screen the user was on.
        """
        self.bar.hide()
        self.picture.hide()
        self.where.hide()
        self.result.hide()
        self.link.hide()
        self.map_link.hide()
        self.title.setText(_("What we will do"))
        # Owner: in bold, it was hard to read; "private keys (seed)" says
        # which keys never go online.
        self.text.setText("<b>{}</b>".format(html.escape(_(
            "The USB stick carries the will between the two computers: the "
            "private keys (seed) never go online."), quote=False)))
        self.text.show()
        ink = self.palette().color(QPalette.ColorRole.WindowText)
        self.map_label.setPixmap(route_map(is_multisig(self.wallet), ink, width=500))
        if start is not None:
            self.map_button.setText(_("Start"))
            self._map_slot = start
        else:
            last = self._last_screen
            self.map_button.setText(_("Back"))
            self._map_slot = (lambda: self.screen(**last)) if last else None
        self._show_page(self.map_page)

    def screen(self, step, picture, title, lines=(), where=None,
               where_text=None, result=None, page=None, link=None,
               all_done=False):
        """Show one screen (see the class).

        ``step``: the bar's step to make now; ``picture``: the drawing (see
        :func:`drawing`); ``where``: :data:`ONLINE` or :data:`OFFLINE` (the
        computer where the step happens, its colour) with ``where_text``;
        ``result``: ``(ok, text)`` the line of result (green tick or orange
        warning); ``lines``: the numbered instructions; ``page``: the page
        with the one button (or none); ``link``: ``(text, slot)`` of the
        small link (or none). The screen is remembered, so "Back" on the
        route map comes back to it.
        """
        self._last_screen = dict(
            step=step, picture=picture, title=title, lines=lines, where=where,
            where_text=where_text, result=result, page=page, link=link,
            all_done=all_done)
        self.bar.show()
        self.picture.show()
        self.map_link.show()
        self.bar.mark(step, all_done=all_done)
        if where is None:
            self.where.hide()
        else:
            colours = ONLINE_COLOURS if where == ONLINE else OFFLINE_COLOURS
            self.where.setStyleSheet(_WHERE_STYLE.format(colours[1], colours[3]))
            self.where.setText(where_text or _("On THIS computer"))
            self.where.show()
        if result is None:
            self.result.hide()
        else:
            ok, text = result
            self.result.setText("<span style='color:{}'>{} {}</span>".format(
                "#1A8F3C" if ok else ORANGE, "✔" if ok else "⚠",
                html.escape(text, quote=False).replace("\n", "<br>")))
            self.result.show()
        self.picture.setPixmap(drawing(picture))
        self.title.setText(title)
        # Text, not markup: "<", ">" and "&" escaped (quotes kept as written).
        if len(lines) == 1:
            self.text.setText(
                html.escape(lines[0], quote=False).replace("\n", "<br>"))
        else:
            self.text.setText("<ol style='margin-left:-20px'>{}</ol>".format(
                "".join("<li style='margin-bottom:4px'>{}</li>".format(
                    html.escape(line, quote=False)) for line in lines)))
        self.text.setVisible(bool(lines))
        self._show_page(page or self.empty_page)
        if link is None:
            self._link_slot = None
            self.link.hide()
        else:
            self.link.setText(link[0])
            self._link_slot = link[1]
            self.link.show()

    def ask_open_path(self):
        """The file window to choose a will file (the tests replace it)."""
        return getOpenFileName(
            parent=self, title=_("Choose the will file"),
            filter=IMPORT_FILE_FILTER, config=self.bal_window.window.config)

    def read_will_file(self, path):
        """The will items of the file at *path*, or None (the error shown).

        The JSON will format of the plugin (the export, the signed file);
        the inputs get this wallet's data (``Will.normalize_will``), so the
        wallet can sign them or count their signatures. A copy: this
        wallet's own will is not touched here.
        """
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
        except Exception as e:
            self.show_error(_("The file cannot be read: {}").format(e))
            return None
        kind, data = decode_will_payload(text)
        if kind != "will":
            self.show_error(_(
                "This is not a will file. Choose the will file saved by the "
                "plugin on the online computer."))
            return None
        try:
            items = self.bal_window._load_will_payload(data)
            Will.normalize_will(items, self.wallet)
        except Exception as e:
            self.show_error(_("The file cannot be read: {}").format(e))
            return None
        return items


class OfflineSignDialog(_GuidedDialog):
    """Step 3 of the route, on the computer with the keys (see the module).

    Screens: choose the file, check it ("Sign all"), signed ("Save on the
    USB stick"), then saved: take the stick back to the online computer
    (step 4), or, for a multisig still missing signatures, to another
    cosigner (step 3 again). A watch-only wallet gets one screen that says
    it cannot sign (the Tools entry opens :class:`OnlineSignDialog` for
    it; kept for safety).
    """

    def __init__(self, bal_window):
        """Build the window for *bal_window*'s wallet."""
        _GuidedDialog.__init__(self, bal_window)
        self.items = None
        self.signed = False
        # #209: the file chosen has a payment problem: it cannot be signed.
        self.blocked = False
        # A multisig signed here that still needs another cosigner:
        # ``(have, required)`` of the signatures.
        self.partly = None
        self.saved_path = None

        self.choose_page = self._button_page(
            _("Choose the will file…"), self.choose_file)
        self.check_page = self._check_page()
        self.save_page = self._button_page(
            _("Save on the USB stick…"), self.save_file)

        if self.wallet.is_watching_only():
            self.screen(None, "stop", _("This wallet cannot sign"),
                        [_(_PROBLEMS[engine.NO_KEYS])])
        else:
            self._show_choose()

    def _check_page(self):
        """What the will gives to whom, and "Sign all"."""
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(0, 0, 0, 0)
        self.details = QLabel()
        self.details.setWordWrap(True)
        self.details.setTextFormat(Qt.TextFormat.RichText)
        self.details.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self.details.setAlignment(Qt.AlignmentFlag.AlignTop)
        # The small "ⓘ" after an amount that depends on the will-executor.
        self.details.linkActivated.connect(self.show_amount_info)
        area = QScrollArea()
        area.setWidgetResizable(True)
        # The check fits its text without scrolling in most wills (owner: the
        # window now fits its screen, so this is the room the check gets).
        area.setMinimumHeight(320)
        area.setWidget(self.details)
        box.addWidget(area, 1)
        self.paper_warning = QLabel(signing_warning())
        self.paper_warning.setWordWrap(True)
        box.addWidget(self.paper_warning)
        self.paper_tick = QCheckBox(_("I compared the addresses with the ones on paper"))
        self.paper_tick.toggled.connect(self._paper_ticked)
        box.addWidget(self.paper_tick)
        row = QHBoxLayout()
        self.sign_button = QPushButton(_("Sign all"))
        # #209: off until the tick, and it must look off.
        self.sign_button.setStyleSheet(
            _BIG + "QPushButton:disabled{color:#9AA5B1; border-color:#D5DBE1;}")
        self.sign_button.setEnabled(False)
        self.sign_button.clicked.connect(self.sign_all)
        row.addWidget(self.sign_button)
        row.addStretch(1)
        box.addLayout(row)
        self.pages.addWidget(page)
        return page

    # -- choose the file -----------------------------------------------------

    def _show_choose(self):
        """Insert the stick and choose the will file."""
        self.items = None
        self.bar.set_count()
        self.screen(
            engine.STEP_SIGN, "offline", _("Sign the will"),
            [_("Choose the will file on the USB stick."),
             _("Check the heirs, the amounts and the date."),
             _("Sign and save it on the USB stick.")],
            where=OFFLINE, where_text=_("On THIS computer (offline)"),
            page=self.choose_page)

    def choose_file(self):
        """Read the chosen file; check it, or say why it cannot be signed."""
        path = self.ask_open_path()
        if path:
            self.load_file(path)

    def load_file(self, path):
        """Load the will file at *path* and show the check (True), or say
        why not (False): not readable, not a will file, or one of the
        engine's problems (:func:`bal.engine.offline_sign.file_problem`)."""
        items = self.read_will_file(path)
        if items is None:
            return False
        # As the Will tab's import does (``import_will_into_details``).
        for wi in items.values():
            wi.set_status("IMPORTED", True)
        problem = engine.file_problem(self.wallet, items)
        if problem is not None:
            self.show_error(_(_PROBLEMS[problem]))
            return False
        self.items = items
        self._show_check()
        return True

    # -- check ---------------------------------------------------------------

    def _amount(self, sats):
        """An amount in the wallet's unit (e.g. "0.00598058 BTC")."""
        return self.bal_window.window.format_amount_and_units(sats)

    def details_html(self):
        """The rich text of the check (:func:`check_html`), with the small
        "ⓘ" that says why an amount is "at least" (:meth:`show_amount_info`).
        """
        return check_html(
            engine.file_summary(self.wallet, self.items), self._amount,
            info=("&nbsp;<a href='{}' style='text-decoration:none;'>"
                  "&#9432;</a>".format(_AMOUNT_INFO_LINK)))

    def amount_info_text(self):
        """What the "ⓘ" after "at least ..." says (owner's words)."""
        return _("The least the recipient will get, if the transaction is "
                 "sent by the most expensive of the will-executors chosen.")

    def show_amount_info(self, link):
        """The "ⓘ" after an amount was clicked: say what the amount is."""
        if link == _AMOUNT_INFO_LINK:
            self.show_message(self.amount_info_text())

    def _show_check(self):
        """Check what the will really pays before signing (#209).

        A file with a problem can only be read: no tick and no signature.
        """
        self.details.setText(self.details_html())
        self._show_signature_count()
        self.blocked = bool(engine.payment_problems(self.wallet, self.items))
        self.paper_tick.setChecked(False)
        self.paper_tick.setVisible(not self.blocked)
        self.paper_warning.setVisible(not self.blocked)
        self.sign_button.setVisible(not self.blocked)
        self.sign_button.setEnabled(False)
        if self.blocked:
            title = _("Do not sign this file")
            line = _("This file pays what the will does not say. The online "
                     "computer may have been hacked: do not sign, and do not "
                     "use that computer for your bitcoin.")
        else:
            title = _("Check before signing")
            line = _("Make sure the heirs, the amounts and the date are the "
                     "ones you chose.")
        self.screen(
            engine.STEP_SIGN, "offline", title, [line],
            where=OFFLINE, where_text=_("On THIS computer (offline)"),
            page=self.check_page,
            link=(_("Choose another file"), self._show_choose))

    def _paper_ticked(self, ticked):
        """The tick of the addresses compared turns "Sign all" on (#209)."""
        self.sign_button.setEnabled(bool(ticked) and not self.blocked)

    def _show_signature_count(self):
        """A multisig: "N of M" signatures under step 3 of the bar."""
        if not is_multisig(self.wallet) or not self.items:
            self.bar.set_count()
            return
        outcome = signing_outcome(self.items)
        required = getattr(self.wallet, "m", None)
        if outcome.partial:
            have, required = min(outcome.partial)
        elif outcome.signed and not outcome.unsigned:
            have = required
        else:
            have = 0
        self.bar.set_count(have, required)

    # -- sign and save -------------------------------------------------------

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
        self.partly = min(outcome.partial) if outcome.partial else None
        if self.partly:
            text = _("{} of {} signatures: another signer must sign.").format(
                *self.partly)
        else:
            text = _("Signed {} of {} transactions.").format(outcome.signed, total)
        self._show_signature_count()
        self.screen(
            engine.STEP_SIGN, "offline", _("Will signed"),
            [_("Now save the signed will on the USB stick.")],
            where=OFFLINE, where_text=_("On THIS computer (offline)"),
            result=(not self.partly, text), page=self.save_page)

    def default_file_name(self):
        """``<wallet>-will-signed-<YYYY-MM-DD>.json`` (translated words)."""
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return "{}-{}-{}.json".format(str(self.wallet), _("will-signed"), day)

    def ask_save_path(self):
        """The file window of the signed will (the tests replace it)."""
        return getSaveFileName(
            parent=self, title=_("Save the signed will"),
            filename=self.default_file_name(), filter=IMPORT_FILE_FILTER,
            default_extension="json", config=self.bal_window.window.config)

    def save_file(self):
        """Save the signed file, then say where the USB stick goes next."""
        path = self.ask_save_path()
        if not path:
            return
        try:
            write_json_file(path, engine.signed_file_data(self.items))
        except Exception as e:
            self.show_error(_("The file cannot be saved: {}").format(e))
            return
        self.saved_path = path
        saved = _("Saved on the USB stick:\n{}").format(path)
        if self.partly:
            have, required = self.partly
            missing = required - have
            # A multisig still missing signatures: the stick goes to another
            # cosigner first; the bar stays on step 3 with the count.
            self.screen(
                engine.STEP_SIGN, "to_offline",
                missing_text(missing),
                [_("Take the USB stick to another cosigner's computer."),
                 _("There open the menu Tools > Sign the will offline."),
                 _("When the signatures are complete, take it to the online "
                   "computer and open the menu Tools > Sign the will offline "
                   "there.")],
                where=OFFLINE, where_text=_("On the OTHER computer (next cosigner)"),
                result=(False, "{}\n{}".format(_(
                    "{} of {} signatures.").format(have, required), saved)))
            return
        # Owner: name what the user finds: Electrum's Tools menu (the big
        # button of the Will tab was not there when needed, now removed).
        self.screen(
            engine.STEP_BRING_BACK, "to_online",
            _("Send the will from the online computer"),
            [_("Take the USB stick out and put it in the online computer."),
             _("Open the watch-only wallet."),
             _("Open the menu Tools > Sign the will offline.")],
            where=ONLINE, where_text=_("On the OTHER computer (online)"),
            result=(True, "{}\n{}".format(_("Will signed."), saved)))

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


#: #161: why step 5 does not load a file (see
#: ``engine.signed_file_problem``); the file still to sign has its own
#: warning (:meth:`OnlineSignDialog.load_file`).
_LOAD_PROBLEMS = {
    engine.NOTHING_TO_SIGN: N_("This file holds no valid will to load."),
    engine.NOT_THIS_WALLET: _PROBLEMS[engine.NOT_THIS_WALLET],
}


class OnlineSignDialog(_GuidedDialog):
    """Steps 1, 2, 4 and 5 of the route, on the online computer (#161).

    The watch-only wallet that the Tools entries, the summary box and the
    wizard bring here (see the module). It opens
    on the step to make now (:func:`engine.next_step`): prepare the USB
    stick (1), or send the signed will (5); after saving the stick it
    shows step 2 (take it to the offline computer, no button), and a small
    link goes between 2 and 5. ``just_checked``: the Check has just run
    (the wizard's "Create the will"), so step 1 does not run it again the
    first time.

    Owner: at the start of the route (step 1) the window first shows the
    map of the whole route, "What we will do" (:meth:`show_intro`); its
    "Start" button shows the screen that prepares the USB stick.
    """

    def __init__(self, bal_window, just_checked=False):
        """Build the window for *bal_window*'s watch-only wallet."""
        _GuidedDialog.__init__(self, bal_window)
        self.just_checked = just_checked
        self.stick_path = None
        self.prepare_page = self._button_page(
            _("Save on the USB stick…"), self.prepare_stick)
        self.send_page = self._button_page(
            _("Send the will…"), self.load_and_send)
        if self.next_step() == engine.STEP_PREPARE:
            self.show_intro()
        else:
            self.show_next_step()

    def next_step(self):
        """The step to make now (1 or 5), or None: nothing to sign."""
        return engine.next_step(True, self.bal_window.willitems,
                                self.bal_window.selected_willexecutors())

    def show_next_step(self):
        """Show the screen of the step to make now (see the class)."""
        step = self.next_step()
        if step == engine.STEP_PREPARE:
            self.show_prepare()
        elif step == engine.STEP_SEND:
            self.show_send()
        else:
            self._show_nothing_to_do()

    def show_intro(self):
        """The start of the route: the map of what we will do, "Start"."""
        self.show_map(start=self.start_route)

    def start_route(self):
        """"Start" on the map: the screen of step 1.

        Owner (ZIP test): the file window must not open by itself, it
        covered the instructions; it opens when the user presses "Save on
        the USB stick…".
        """
        self.show_prepare()

    def run_check(self):
        """The Will tab's Check (the tests replace it).

        It prepares the will when something changed and, after a merge,
        sends the signed copies and verifies them (its own window, modal).
        Then this window comes back to the front.
        """
        self.bal_window.will_list_widget.check()
        self.raise_()
        self.activateWindow()

    def _signatures_needed(self):
        """How many signatures a copy needs: the multisig's M (1 otherwise)."""
        return getattr(self.wallet, "m", 1) if is_multisig(self.wallet) else 1

    # -- the screens ---------------------------------------------------------

    def show_prepare(self):
        """Step 1: prepare the USB stick."""
        self.bar.set_count()
        self.screen(
            engine.STEP_PREPARE, "online", _("Prepare the USB stick"),
            [_("Insert the USB stick."),
             _("Press the button and save the file on the USB stick.")],
            where=ONLINE, page=self.prepare_page)

    def show_carry(self):
        """Step 2: take the stick to the offline computer (no button)."""
        result = None
        if self.stick_path:
            result = (True, _("Saved on the USB stick:\n{}").format(self.stick_path))
        if is_multisig(self.wallet):
            self.bar.set_count(0, self._signatures_needed())
            title = _("Have the will signed by the cosigners")
            lines = [
                _("Take the USB stick to a cosigner's computer."),
                _("There open its wallet and the menu Tools > Sign the will "
                  "offline."),
                _("Repeat with the next cosigners until {} signatures are "
                  "made.").format(self._signatures_needed()),
                _("Bring the USB stick back here.")]
            where = _("On the OTHER computers (cosigners)")
        else:
            title = _("Sign the will on the offline computer")
            lines = [
                _("Take the USB stick out and put it in the offline computer."),
                _("Open the wallet that has the keys."),
                # Owner: name what the user finds, Electrum's Tools menu.
                _("Open the menu Tools > Sign the will offline."),
                _("Bring the USB stick back here.")]
            where = _("On the OTHER computer (offline)")
        self.screen(engine.STEP_CARRY, "to_offline", title, lines, where=OFFLINE,
                    where_text=where, result=result,
                    link=(_("I already have the signed USB stick →"),
                          self.show_send))

    def show_send(self):
        """Step 5: send the signed will."""
        self.bar.set_count()
        self.screen(
            engine.STEP_SEND, "online", _("Send the signed will"),
            [_("Put the signed USB stick in this computer."),
             _("Press the button and choose the signed file.")],
            where=ONLINE, page=self.send_page,
            link=(_("I still have to sign it"), self.show_carry))

    def _show_no_will(self):
        """No valid will in this wallet: the way to create it."""
        self.bar.set_count()
        self.screen(
            None, "stop", _("There is no will to sign"),
            [_("This wallet has no will ready yet: create it with \"{}\" in "
               "the Will tab.").format(_("Build / Edit Your Will"))])

    def _show_nothing_to_do(self):
        """No will, or the will is signed: nothing to carry."""
        problem = engine.prepare_problem(self.bal_window.willitems,
                                         self.bal_window.selected_willexecutors())
        if problem == engine.NO_WILL:
            self._show_no_will()
            return
        self.bar.set_count()
        self.screen(
            None, "done", _("The will is already signed"),
            [_("Every transaction of the will is signed: there is nothing "
               "to carry to the offline computer. The box at the top of "
               "the Will tab says whether the will-executors have it.")],
            all_done=True)

    # -- step 1 --------------------------------------------------------------

    def default_file_name(self):
        """``<wallet>-will-TO-SIGN-<YYYY-MM-DD>.json`` (translated words)."""
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return "{}-{}-{}.json".format(str(self.wallet), _("will-TO-SIGN"), day)

    def ask_save_path(self):
        """The file window of step 1 (a method, so the tests replace it)."""
        return getSaveFileName(
            parent=self, title=_("Save the will to sign"),
            filename=self.default_file_name(), filter=IMPORT_FILE_FILTER,
            default_extension="json", config=self.bal_window.window.config)

    def prepare_stick(self):
        """Step 1: prepare the will (the Check) and save what is to sign.

        The Check runs first (unless it has just run): it builds the will
        when it is missing or no longer matches the heirs and settings, so
        the stick never carries an old will. Then only the copies still to
        sign are saved (:func:`engine.stick_items`), with no export choices
        to make, and the window goes on to step 2. Returns True when the
        file is saved.
        """
        if not self.just_checked:
            self.run_check()
        self.just_checked = False
        willitems = self.bal_window.willitems
        selected = self.bal_window.selected_willexecutors()
        if engine.prepare_problem(willitems, selected) is not None:
            self._show_nothing_to_do()
            return False
        items = engine.stick_items(willitems, selected)
        path = self.ask_save_path()
        if not path:
            return False
        try:
            self.bal_window.export_stick_file(path, items)
        except Exception as e:
            self.show_error(_("The file cannot be saved: {}").format(e))
            return False
        self.stick_path = path
        self.show_carry()
        return True

    # -- step 5 --------------------------------------------------------------

    def load_and_send(self):
        """Step 5: choose the signed file, then :meth:`load_file`."""
        path = self.ask_open_path()
        if not path:
            return False
        return self.load_file(path)

    def load_file(self, path):
        """Merge the signed will file at *path* and run the Check (True), or
        say why not (False).

        The file still to sign (step 1's file, no signature inside) is
        refused with a warning: merging it would change nothing and the
        user would believe the will was signed.
        """
        items = self.read_will_file(path)
        if items is None:
            return False
        problem = engine.signed_file_problem(self.wallet, items)
        if problem == engine.NOT_SIGNED:
            self.show_warning(_(
                "This is the will file still to sign: it holds no signature "
                "yet. Sign it first on the offline computer, then choose here "
                "the signed file (its name has \"{}\").").format(_("will-signed")))
            return False
        if problem is not None:
            self.show_error(_(_LOAD_PROBLEMS[problem]))
            return False
        # The Merge of the Will tab (#158), without its message box: this
        # window says what happened once the Check has run.
        self.bal_window.merge_will(items, show=False)
        self.run_check()
        self._show_after_send()
        return True

    def _show_after_send(self):
        """After step 5: signed and sent, or what is still missing."""
        willitems = self.bal_window.willitems
        selected = self.bal_window.selected_willexecutors()
        if engine.prepare_problem(willitems, selected) == engine.NO_WILL:
            self._show_no_will()
            return
        outcome = signing_outcome(willitems, selected=selected)
        if outcome.partial:
            have, required = min(outcome.partial)
            # The file on the stick has the signatures made so far: the next
            # cosigner signs it (step 3 again, from the stick).
            self.bar.set_count(have, required)
            self.screen(
                engine.STEP_SIGN, "to_offline",
                missing_text(required - have),
                [_("Take the USB stick to another cosigner's computer."),
                 _("There open the menu Tools > Sign the will offline."),
                 _("Bring the USB stick back here and open the menu Tools > "
                   "Sign the will offline again.")],
                where=OFFLINE, where_text=_("On the OTHER computer (next cosigner)"),
                result=(False, _("{} of {} signatures.").format(have, required)))
        elif outcome.unsigned:
            self.bar.set_count()
            self.screen(
                engine.STEP_PREPARE, "stop", _("The will is not signed yet"),
                [_("Some transactions are still to sign: prepare the USB "
                   "stick again.")],
                where=ONLINE, page=self.prepare_page)
        else:
            self.bar.set_count()
            self.screen(
                None, "done", _("Will signed and sent"),
                [_("The signatures are in your will and the Check sent it to "
                   "the will-executors. The box at the top of the Will tab "
                   "says whether they received it.")],
                all_done=True)


def open_sign_offline(bal_window, just_checked=False):
    """The window of Tools > "Sign the will offline..." for this wallet.

    #161: :class:`OnlineSignDialog` (steps 1, 2, 4, 5) for a watch-only
    wallet, :class:`OfflineSignDialog` (step 3, #160) for a wallet with
    keys. ``just_checked``: see :class:`OnlineSignDialog`.
    """
    if bal_window.wallet.is_watching_only():
        return OnlineSignDialog(bal_window, just_checked=just_checked)
    return OfflineSignDialog(bal_window)
