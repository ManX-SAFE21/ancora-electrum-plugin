"""The plugin's HTML texts on Electrum 4.8.3 (#198).

Electrum 4.8.3 (23 Oct 2026, its #10972, security) shows as plain text
every QLabel left on the default AutoText, and the "?" help of HelpButton
unless ``rich_text=True`` (an argument 4.8.2 does not have). The plugin's
own HTML (bold, line breaks, colours, the safe21.io link) then showed its
tags. Found by running the whole suite on 4.8.3 with a probe (12 places).

Now:

* ``bal.gui.qt.common.rich_text(label)`` marks a label that shows the
  plugin's HTML: the same on 4.8.2 and 4.8.3; it also renders escaped
  entities (``&amp;``), which AutoText showed raw in a text without tags;
* ``bal.gui.qt.common.help_button(text)`` passes ``rich_text=True`` only on
  4.8.3 and only for an HTML help (a plain help keeps its line breaks);
  every "?" of the plugin is made by it;
* ``tests/rich_text_guard.py`` applies 4.8.3's rule in every test (from
  ``conftest.py``) and fails the test that shows a label with raw HTML.

Run (standalone or with pytest), on 4.8.2 or 4.8.3:
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_rich_text_483.py
"""

import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QLabel
from rich_text_guard import RichTextGuard, looks_like_html

_app = QApplication.instance() or QApplication([])

from electrum.gui.qt.util import HelpButton

from bal.gui.qt import common
from bal.gui.qt.common import help_button, rich_text
from bal.gui.qt.widgets import what_changes_box

BAL = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "bal")
#: Electrum 4.8.3 has HelpButton(text, rich_text=...); 4.8.2 does not.
ELECTRUM_HAS_RICH_HELP = common._HELP_BUTTON_RICH


def shown_under_guard(widget):
    """Show ``widget`` with 4.8.3's rule on; the guard's record."""
    guard = RichTextGuard()
    _app.installEventFilter(guard)
    try:
        widget.show()
        _app.processEvents()
        guard.check_later_texts()
    finally:
        _app.removeEventFilter(guard)
        widget.hide()
    return guard.found


def test_rich_text_marks_the_label_and_returns_it():
    """rich_text() says RichText explicitly, so 4.8.3 leaves it alone."""
    label = QLabel("<b>x</b>")
    assert rich_text(label) is label
    assert label.textFormat() == Qt.TextFormat.RichText
    assert shown_under_guard(label) == []


def test_help_button_renders_html_help_on_both_versions():
    """An HTML help is rich on 4.8.3 (4.8.2 shows it as AutoText, rich);
    a plain help stays plain, so its "\\n" line breaks are kept."""
    html_help = help_button("<b>TITLE</b><br><br>text")
    plain_help = help_button("first line.\nsecond line")
    assert isinstance(html_help, HelpButton)
    assert isinstance(plain_help, HelpButton)
    if ELECTRUM_HAS_RICH_HELP:
        assert html_help.rich_text is True
        assert plain_help.rich_text is False
    else:
        assert not hasattr(html_help, "rich_text")


def test_every_help_button_of_the_plugin_uses_help_button():
    """No plain HelpButton(...) is left in the plugin: only help_button
    creates one (a new "?" with HTML help would show its tags on 4.8.3)."""
    calls = []
    for root, _dirs, files in os.walk(BAL):
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(root, name)
            with open(path, encoding="utf-8") as f:
                for n, line in enumerate(f, 1):
                    if re.search(r"\bHelpButton\(", line) and not line.lstrip().startswith("#"):
                        calls.append("{}:{}".format(os.path.relpath(path, BAL), n))
    assert calls == [os.path.join("gui", "qt", "common.py") + ":{}".format(n)
                     for n in _help_button_lines()]


def _help_button_lines():
    """The lines of common.py where help_button() itself calls HelpButton."""
    with open(os.path.join(BAL, "gui", "qt", "common.py"), encoding="utf-8") as f:
        return [n for n, line in enumerate(f, 1)
                if re.search(r"\bHelpButton\(", line) and not line.lstrip().startswith("#")]


def test_guard_finds_what_4_8_3_would_spoil():
    """The guard of conftest.py: raw tags at the first show, escaped
    entities, and an HTML text set after the label was made plain."""
    assert shown_under_guard(QLabel("<b>Support:</b>")) == ["<b>Support:</b>"]
    assert shown_under_guard(QLabel("Tom &amp; Jerry")) == ["Tom &amp; Jerry"]
    assert shown_under_guard(QLabel("plain text, 2 < 3")) == []

    guard = RichTextGuard()
    label = QLabel("")
    _app.installEventFilter(guard)
    try:
        label.show()
        _app.processEvents()
        label.setText("line one<br>line two")
        guard.check_later_texts()
    finally:
        _app.removeEventFilter(guard)
        label.hide()
    assert guard.found == ["line one<br>line two"]
    assert looks_like_html("first line\nsecond <b>bold</b>")  # past line 1


def test_what_changes_box_title_and_names_are_rich():
    """The "What changes" box (#135): its title in bold and the escaped
    names (an "&" in a name) render, on 4.8.3 too."""
    box = what_changes_box([("Tom & Jerry", "added <b>Tom &amp; Jerry</b>", "a & b")])
    assert shown_under_guard(box) == []
    texts = {label.text(): label.textFormat() for label in box.findChildren(QLabel)}
    assert texts["• Tom &amp; Jerry"] == Qt.TextFormat.RichText
    assert any(t.startswith("<b>") and f == Qt.TextFormat.RichText for t, f in texts.items())


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
