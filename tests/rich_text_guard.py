"""Electrum 4.8.3's "no rich text by default" rule, in every test (#198).

Electrum 4.8.3 (its #10972, security) installs an event filter that turns
every QLabel still on the default AutoText into PlainText just before it is
first shown: a label the plugin fills with its own HTML then shows the tags
(``<b>``, ``<br>``) as text. Such a label must say RichText explicitly
(``bal.gui.qt.common.rich_text``).

:class:`RichTextGuard` does what that filter does, also when the tests run
on Electrum 4.8.2, and remembers each label that would show HTML as plain
text: when it is shown, and when it gets an HTML text later. ``conftest.py``
installs it around every test and fails the test that left one.
"""

import re

from PyQt6 import sip
from PyQt6.QtCore import QEvent, QObject, Qt
from PyQt6.QtWidgets import QLabel

# An HTML tag the plugin writes, anywhere in the text: Qt.mightBeRichText
# looks only at the first line.
_TAG = re.compile(r"<(b|br|i|p|a|ol|li|span|font|table|tr|td)\b[^>]*>", re.IGNORECASE)
# An escaped character (``&amp;``, ``&#x27;``): shown raw by a plain label.
_ENTITY = re.compile(r"&(#\d+|#x[0-9a-f]+|[a-z]+);", re.IGNORECASE)


def looks_like_html(text):
    """True if ``text`` would show tags or entities in a plain-text label."""
    text = str(text or "")
    return bool(Qt.mightBeRichText(text) or _TAG.search(text) or _ENTITY.search(text))


class RichTextGuard(QObject):
    """The 4.8.3 filter plus a record of the labels it would spoil.

    ``found`` lists the texts (cut to 120 characters) shown with raw HTML.
    """

    def __init__(self):
        """An empty record; install with ``app.installEventFilter``."""
        super().__init__()
        self.found = []
        self._plain = []  # labels turned into PlainText: their later texts

    def eventFilter(self, obj, event):
        """On a label's first Polish, as 4.8.3: AutoText -> PlainText."""
        if (event.type() == QEvent.Type.Polish and isinstance(obj, QLabel)
                and obj.textFormat() == Qt.TextFormat.AutoText):
            if looks_like_html(obj.text()):
                self.found.append(obj.text()[:120])
            obj.setTextFormat(Qt.TextFormat.PlainText)
            self._plain.append(obj)
        return False

    def check_later_texts(self):
        """Record the labels made plain that got an HTML text afterwards
        (e.g. a log line set when the window is already open)."""
        for label in self._plain:
            if sip.isdeleted(label):
                continue
            if label.textFormat() == Qt.TextFormat.PlainText and looks_like_html(label.text()):
                text = label.text()[:120]
                if text not in self.found:
                    self.found.append(text)
        self._plain = []
