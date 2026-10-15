"""The heirs of the PDF report on paper (CHANGELOG #145, owner).

Owner, after a look at the printed report:

* the address was too small and thin (Courier 8, grey): it could fade with
  time. Now a larger bold monospaced font, black;
* a QR code of the heir's address right of it;
* addresses differ a lot in length (26 to 62 characters): written in groups
  of 4 characters, and the page says to copy them without the spaces;
* a visible light line between two heirs;
* the dates in the plugin's one style (CHANGELOG #134);
* the message section says "OP_RETURN" and gives the warning of the
  message window (tests/test_will_message.py).

Wallets, coins and will-executors come from ``tests/flowkit.py`` (three
fake will-executors, each with its own fee address; throw-away test wallet
with a password; fixed clock), through the fixtures of
``tests/test_inheritance_report.py``.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_pdf_heirs_qr.py
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

import flowkit as fk
from electrum.segwit_addr import encode_segwit_address
from PyQt6.QtGui import QColor
from test_inheritance_report import D, _report, _sat, make  # noqa: F401

from bal.core import datetext
from bal.gui.qt import report_pages as rp

#: A 62-character testnet Taproot address (nobody has its key).
LONG = encode_segwit_address("tb", 1, bytes([7]) * 32)
NOTE = ("The addresses are written in groups of 4 characters to read them "
        "better: copy them without the spaces. The QR code next to each one "
        "holds the same address.")


def _pages(s):
    """The report pages of the session's will."""
    return rp.layout_report(_report(s), _sat)


def _items(pages, kind):
    """``(page, item)`` of every item of ``kind``."""
    return [(p, i) for p in pages for i in p.items if i.kind == kind]


@pytest.fixture
def will(make):  # noqa: F811
    """A will in force: alice and bob, plus carol at a 62-character
    address, at three will-executors."""
    s = make()
    s.bw.heirs["carol"] = [LONG, "20%", D]
    s.bw.heirs["alice"] = [fk.HEIR_ADDR["alice"], "50%", D]
    s.bw.heirs["bob"] = [fk.HEIR_ADDR["bob"], "30%", D]
    s.check()
    return s


def test_address_groups():
    """Groups of 4 characters; without the spaces it is the address again."""
    assert rp.address_groups("bc1qg4w5qq75kv") == "bc1q g4w5 qq75 kv"
    grouped = rp.address_groups(LONG)
    assert len(LONG) == 62 and grouped.replace(" ", "") == LONG
    assert all(len(g) == 4 for g in grouped.split(" ")[:-1])
    assert rp.address_groups("") == "" and rp.address_groups(None) == ""


def test_each_heir_has_its_address_grouped_bold_and_a_qr(will):
    """Every heir: the grouped address in monospaced 10 points, black,
    and a 15 mm QR code of the address itself on its right; everything
    inside the page, the long address too."""
    pages = _pages(will)
    texts = [t for p in pages for t in rp.page_texts(p)]
    assert NOTE in texts
    # Owner: the first column holds the name and, under it, the address.
    assert "Name and Bitcoin address" in texts
    addresses = {h.address for h in _report(will).heirs}
    assert LONG in addresses
    shown = [i for _p, i in _items(pages, "text") if i.text in
             {rp.address_groups(a) for a in addresses}]
    assert len(shown) == len(addresses)
    for item in shown:
        assert item.font.fixedPitch() and item.font.pixelSize() == 10
        assert item.colour == rp.TEXT
        assert item.rect.right() <= rp.PAGE_W - rp.QR_SIDE
    qrs = _items(pages, "qr")
    assert sorted(i.text for _p, i in qrs) == sorted(addresses)
    for page, item in qrs:
        assert item.rect.width() == item.rect.height() == rp.QR_SIDE
        assert item.rect.right() <= rp.PAGE_W + 0.01
        assert item.rect.bottom() <= rp.PAGE_H - rp.FOOTER_H
        # Right of the amount, from the top of the row; never over the address.
        row = [i for i in page.items if i.kind == "text" and i.text == rp.address_groups(item.text)]
        assert row and row[0].rect.right() < item.rect.left()


def test_a_usual_address_fits_one_line(will):
    """Owner (#145 preview): narrow share and amount columns, so a usual
    address (42 characters, 11 groups) is on one line, also with amounts in
    BTC (the widest unit); the 62-character one wraps on two."""
    from electrum.util import format_satoshis

    def btc(sats):
        """An amount in BTC, as Electrum writes it."""
        return format_satoshis(sats, decimal_point=8) + " BTC"

    pages = rp.layout_report(_report(will), btc)
    one = rp.report_font(10, mono=True).pixelSize()
    for name in ("alice", "bob"):
        text = rp.address_groups(fk.HEIR_ADDR[name])
        assert len(fk.HEIR_ADDR[name]) == 42
        item = next(i for _p, i in _items(pages, "text") if i.text == text)
        assert item.rect.height() < 2 * one, name
    item = next(i for _p, i in _items(pages, "text") if i.text == rp.address_groups(LONG))
    assert item.rect.height() >= 2 * one


def test_amounts_always_in_btc(will):
    """Owner (#145): the PDF amounts are in BTC with 8 decimals, whatever
    unit the wallet shows (here sat)."""
    from bal.gui.qt import report_actions

    assert report_actions.btc_amount(496444) == "0.00496444 BTC"
    assert report_actions.btc_amount(1000) == "0.00001000 BTC"
    assert report_actions.btc_amount(123456789012) == "1234.56789012 BTC"
    assert will.bw.window.config.format_amount_and_units(1000) == "1000 sat"
    pages, _logo = report_actions.build_pages(will.bw)
    texts = [t for p in pages for t in rp.page_texts(p)]
    r = _report(will)
    for heir in r.heirs:
        assert report_actions.btc_amount(heir.sats) in texts
    assert report_actions.btc_amount(r.total_sats) in texts
    assert "Executor fee: 0.00001000 BTC" in texts
    assert not any(t.endswith(" sat") for t in texts)


def test_a_light_line_between_two_heirs(will):
    """One light line under the column titles and under every heir."""
    pages = _pages(will)
    lines = [i for _p, i in _items(pages, "line") if i.colour == rp.HEIR_RULE]
    assert len(lines) == 1 + len(_report(will).heirs)
    assert all(i.width == 0.8 for i in lines)


def test_the_total_stays_with_the_last_heir(will):
    """"Total to the heirs" is never alone at the top of a page."""
    pages = _pages(will)
    page = next(p for p in pages if "Total to the heirs" in rp.page_texts(p))
    last = rp.address_groups(_report(will).heirs[-1].address)
    assert last in rp.page_texts(page)


def test_the_qr_is_painted_as_a_qr_code(will):
    """The painted code has the three finder squares of a QR code, the
    right way up: dark corners top-left, top-right and bottom-left,
    light bottom-right module next to them."""
    page, item = _items(_pages(will), "qr")[0]
    modules = rp.qr_modules(item.text)
    n = len(modules)
    dpi = 300
    image = rp.page_image(page, dpi=dpi)
    scale = dpi / 72.0
    left = rp.MARGIN_MM * dpi / 25.4 + item.rect.x() * scale
    top = rp.MARGIN_MM * dpi / 25.4 + item.rect.y() * scale
    side = item.rect.width() * scale / n

    def dark(row, col):
        """The pixel at the centre of module (row, col) is black."""
        x = int(left + (col + 0.5) * side)
        y = int(top + (row + 0.5) * side)
        return QColor(image.pixel(x, y)).lightness() < 80

    for r, c in ((0, 0), (0, n - 1), (n - 1, 0), (3, 3), (3, n - 4), (n - 4, 3)):
        assert dark(r, c), (r, c)
    for r, c in ((1, 1), (1, n - 2), (n - 2, 1)):
        assert not dark(r, c), (r, c)
    assert all(dark(r, c) == bool(modules[r][c]) for r in range(n) for c in range(n))


def test_dates_in_the_one_style(will):
    """Delivery, Check Alive and the technical page in the plugin's one
    style; the short form only where the long one does not fit."""
    texts = [t for p in _pages(will) for t in rp.page_texts(p)]
    assert any(t.startswith("27 August 2027, 22:00 UTC") for t in texts)
    assert any(t.startswith("29 May 2027, 00:00 UTC") for t in texts)
    assert not any("/2027" in t for t in texts)
    font = rp.report_font(9)
    assert rp.date_fit(D, font, 1000) == datetext.utc_text(D)
    assert rp.date_fit(D, font, 10) == datetext.utc_short(D)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
