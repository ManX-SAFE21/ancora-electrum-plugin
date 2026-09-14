"""Standalone QR emitter for testing the Android reader on a real camera.

Replicates exactly what the plugin's QR export page puts on screen
(``BalQrExportWidget``): the same ``encode_transfer`` + per-format frame
encoders from ``bal.core``, rendered one QR at a time with ``qrcode``.

Run from the repo root with the runtime venv (has ``bal``, ``qrcode``,
PyQt6):

    source "$BAL_HOME/electrum/env/bin/activate"
    QT_QPA_PLATFORM=xcb python3 android/tools/emitter.py --format ur2 --loop

Controls:
    Left/Right         previous / next frame
    Space              toggle autoplay
    L                  toggle loop (default off)
    Q / Esc            quit
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import qrcode  # noqa: E402
from PyQt6.QtCore import Qt, QTimer  # noqa: E402
from PyQt6.QtGui import QColor, QImage, QPainter, QPixmap  # noqa: E402
from PyQt6.QtWidgets import QLabel, QMainWindow, QWidget  # noqa: E402

from bal.core import animated_qr as aq  # noqa: E402
from bal.core import qrtransfer as qtf  # noqa: E402


def build_frames(tx_strings, fmt, budget):
    """Frames exactly as BalQrExportWidget._refresh_frames produces them."""
    transfer = qtf.encode_transfer(tx_strings, compress=False)
    if fmt == "balqr":
        return qtf.split_frames(transfer, budget, compressed=False)
    payload = transfer.encode("utf-8")
    if fmt == "ur1":
        return aq.ur1_frames(payload, budget)
    if fmt == "ur2":
        return aq.ur2_frames(payload, budget)
    if fmt == "bbqr":
        return aq.bbqr_frames(payload, budget, encoding="Z")
    raise SystemExit("unknown format: {}".format(fmt))


def load_payload(args):
    """Return ``(tx_strings, description)`` mirroring ``_payload_strings``."""
    if args.will is not None:
        will = json.loads(Path(args.will).read_text())
        return (
            [json.dumps(will, ensure_ascii=False)],
            "whole-will JSON ({})".format(Path(args.will).name),
        )
    if args.txs is not None:
        raw = Path(args.txs).read_text()
        return [line.strip() for line in raw.split() if line.strip()], "txs"
    raise SystemExit("give --will FILE or --txs FILE")


def qr_pixmap(text, size):
    """Render ``text`` as a QR code fitted to a ``size``x``size`` image."""
    qr = qrcode.QRCode(border=2, error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(text)
    qr.make(fit=True)
    matrix = qr.modules
    n = len(matrix)
    border = qr.border
    scale = max(1, size // (n + 2 * border))
    dim = (n + 2 * border) * scale
    image = QImage(dim, dim, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.white)
    painter = QPainter(image)
    painter.fillRect(0, 0, dim, dim, QColor("white"))
    painter.setBrush(QColor("black"))
    painter.setPen(Qt.PenStyle.NoPen)
    for y, row in enumerate(matrix):
        for x, on in enumerate(row):
            if on:
                painter.fillRect(
                    (x + border) * scale, (y + border) * scale, scale, scale,
                    QColor("black"),
                )
    painter.end()
    return QPixmap.fromImage(image).scaled(
        size, size, Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


class EmitterWindow(QMainWindow):
    def __init__(self, frames, description, fmt, fps, loop):
        super().__init__()
        self.frames = frames
        self.fps = fps
        self.loop = loop
        self.index = 0
        self.autoplay = True

        central = QWidget(self)
        self.setCentralWidget(central)
        self.pix = QLabel(central)
        self.pix.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.caption = QLabel(central)
        self.caption.setAlignment(Qt.AlignmentFlag.AlignCenter)

        import PyQt6.QtWidgets as qt  # noqa: N813 - local import for clarity

        v = qt.QVBoxLayout(central)
        v.addWidget(self.pix, 1)
        v.addWidget(self.caption)

        self.setWindowTitle("BAL Reader emitter — {}".format(fmt))
        self.resize(900, 1000)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._step)
        self.timer.start(int(1000 / self.fps))

        self._render()
        self.caption.setText(
            "{desc} | {fmt} | frame {i}/{n} | autoplay={auto} loop={loop}".format(
                desc=description, fmt=fmt, i=self.index + 1, n=len(self.frames),
                auto="on" if self.autoplay else "off", loop="on" if loop else "off",
            )
        )
        self.show()

    def _render(self):
        self.pix.setPixmap(qr_pixmap(self.frames[self.index], min(self.width() - 60, 900)))

    def _update_caption(self):
        auto = "on" if self.autoplay else "off"
        loop = "on" if self.loop else "off"
        self.caption.setText(
            "frame {i}/{n} ({fmt}) | autoplay={auto} loop={loop}".format(
                i=self.index + 1, n=len(self.frames), fmt="", auto=auto, loop=loop)
        )

    def _step(self):
        if self.index + 1 < len(self.frames):
            self.index += 1
        elif self.loop:
            self.index = 0
        else:
            self.autoplay = False
            self.timer.stop()
        self._render()
        self._update_caption()

    def keyPressEvent(self, event):  # noqa: N802 - Qt override name
        key = event.key()
        if key == Qt.Key.Key_Right:
            self.autoplay = False
            self.index = min(self.index + 1, len(self.frames) - 1)
            self._render()
        elif key == Qt.Key.Key_Left:
            self.autoplay = False
            self.index = max(self.index - 1, 0)
            self._render()
        elif key == Qt.Key.Key_Space:
            self.autoplay = not self.autoplay
            if self.autoplay:
                self.timer.start(int(1000 / self.fps))
            else:
                self.timer.stop()
        elif key == Qt.Key.Key_L:
            self.loop = not self.loop
        elif key in (Qt.Key.Key_Q, Qt.Key.Key_Escape):
            self.close()
        self._update_caption()


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--format", choices=["balqr", "ur1", "ur2", "bbqr"],
                        default="balqr")
    parser.add_argument("--budget", type=int, default=400)
    parser.add_argument("--fps", type=float, default=1.0)
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--will", help="whole-will JSON file")
    parser.add_argument("--txs", help="file with serialized tx strings")
    args = parser.parse_args(argv)

    tx_strings, description = load_payload(args)
    frames = build_frames(tx_strings, args.format, args.budget)
    if len(frames) == 1:
        print("single-frame transfer ready ({} bytes)".format(len(frames[0])), file=sys.stderr)
    else:
        print("{} frames ready".format(len(frames)), file=sys.stderr)

    app = __import__("PyQt6.QtWidgets", fromlist=["QApplication"]).QApplication([])
    EmitterWindow(frames, description, args.format, args.fps, args.loop)
    app.exec()


if __name__ == "__main__":
    main(sys.argv[1:])
