"""Plain texts: the refused chain and the translated Yes / No (#189).

* When the safety net refuses a chain of deliveries (``WillChainError``)
  the owner read the raw reason ("chain: a loop between the
  transactions"). Now one plain sentence (``chain_refused_message``) is
  shown by the Check, the main window and the command line; the raw reason
  goes to the log.
* The Yes / No questions built with Qt's standard buttons (Electrum's
  ``question``, the deliveries window) stayed in English: Electrum loads no
  Qt translation. ``common.ask_yes_no`` adds the buttons with their own
  ``_()`` text; ``BalDialog.question`` and ``deliveries.ask`` use it.

Here: the real message box with its ``exec`` replaced (as in
``test_basic_mode_confirm``), the command line's ``build_will`` with the
engine's build replaced, and the catalogs read with babel.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \
        python3 -m pytest tests/test_texts_189.py
"""

import os
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from babel.messages.pofile import read_po
from electrum.util import UserFacingException
from PyQt6.QtWidgets import QApplication, QWidget

from bal.cli.controller import BalController
from bal.core import messages
from bal.core.will import WillChainError
from bal.gui.qt import common as common_mod
from bal.gui.qt import deliveries
from bal.gui.qt import dialogs as dialogs_mod

# Kept at module level: Qt widgets need an application object.
_APP = QApplication.instance() or QApplication(sys.argv)

LOCALE = os.path.join(os.path.dirname(__file__), os.pardir, "bal", "locale")
CHAIN_TEXT = (
    "The new will was not prepared: a safety check found a problem in the "
    "chain of deliveries. Nothing was changed: if you have a will in force, "
    "it stays as it is. The details are in Electrum's log."
)
ITALIAN = {"Yes": "Sì", "No": "No"}


@pytest.fixture
def answer(monkeypatch):
    """The owner's answer to a Yes / No box (True: Yes, None: Escape).

    The real box is built; its ``exec`` records title, text, buttons,
    default and Escape button, then clicks the answer. The plugin's
    translator is replaced by an Italian "Yes" / "No".
    """
    state = {"yes": True, "asked": []}

    def fake_exec(box):
        """Record the box and click the chosen button."""
        buttons = {b.text(): b for b in box.buttons()}
        state["asked"].append({
            "title": box.windowTitle(), "text": box.text(),
            "buttons": sorted(buttons),
            "default": box.defaultButton().text(),
            "escape": box.escapeButton().text()})
        if state["yes"] is not None:
            buttons[ITALIAN["Yes" if state["yes"] else "No"]].click()
        return 0

    monkeypatch.setattr(common_mod.QMessageBox, "exec", fake_exec)
    monkeypatch.setattr(common_mod, "_", lambda s: ITALIAN.get(s, s))
    return state


# ------------------------------------------------------------ Yes / No

@pytest.mark.parametrize("yes,expected", [(True, True), (False, False),
                                          (None, False)],
                         ids=["yes", "no", "escape"])
def test_ask_yes_no_has_translated_buttons_and_no_by_default(answer, yes, expected):
    """The buttons come from the plugin's catalogs; No is the default and
    the Escape button; only Yes answers True."""
    answer["yes"] = yes
    assert common_mod.ask_yes_no(None, "Exclude it?", "Title") is expected
    asked = answer["asked"][-1]
    assert asked["buttons"] == ["No", "Sì"]
    assert asked["default"] == "No" and asked["escape"] == "No"
    assert asked["title"] == "Title" and asked["text"] == "Exclude it?"


def test_dialog_question_uses_the_translated_box(answer):
    """``BalDialog.question`` (Exclude, Close anyway) is the translated box
    with Electrum's answer: True on Yes only."""
    fake = QWidget()      # the dialog itself is the parent of the box
    assert dialogs_mod.BalDialog.question(fake, "Exclude it?", parent=None) is True
    answer["yes"] = False
    assert dialogs_mod.BalDialog.question(fake, "Exclude it?", parent=None) is False
    assert all(a["buttons"] == ["No", "Sì"] for a in answer["asked"])


def test_deliveries_question_uses_the_translated_box(answer):
    """The questions of the deliveries window (#188 included) too."""
    assert deliveries.ask(None, "Back to one delivery?") is True
    asked = answer["asked"][-1]
    assert asked["buttons"] == ["No", "Sì"] and asked["default"] == "No"
    assert asked["title"] == "Inheritance in several deliveries"


def test_basic_mode_question_is_unchanged(answer):
    """"Go back to BASIC mode?" (#174) now goes through the same box."""
    answer["yes"] = False
    assert common_mod.confirm_basic_mode(None) is False
    asked = answer["asked"][-1]
    assert asked["title"] == "BASIC mode" and asked["default"] == "No"
    assert asked["text"].startswith("Go back to BASIC mode?")


@pytest.mark.parametrize("lang,yes", [("it_IT", "Sì"), ("es_ES", "Sí")])
def test_catalogs_translate_yes_and_no(lang, yes):
    """The catalogs hold the words the buttons show."""
    path = os.path.join(LOCALE, lang, "LC_MESSAGES", "bal.po")
    with open(path, "rb") as f:
        catalog = read_po(f)
    assert catalog["Yes"].string == yes
    assert catalog["No"].string == "No"


# ------------------------------------------------------------ the chain

def test_chain_message_is_plain_and_logs_the_reason():
    """One plain sentence; the raw reason only in the log."""
    error = WillChainError("chain: a loop between the transactions")
    with patch.object(messages, "_logger") as log:
        text = messages.chain_refused_message(error)
    assert text == CHAIN_TEXT
    assert "chain:" not in text
    log.info.assert_called_once()
    assert "a loop between the transactions" in log.info.call_args.args[0]


@pytest.mark.parametrize("lang,start", [
    ("it_IT", "Il nuovo testamento non è stato preparato"),
    ("es_ES", "El nuevo testamento no se ha preparado"),
])
def test_chain_message_is_translated(lang, start):
    """Italian and Spanish texts exist and are not fuzzy."""
    path = os.path.join(LOCALE, lang, "LC_MESSAGES", "bal.po")
    with open(path, "rb") as f:
        catalog = read_po(f)
    message = catalog[CHAIN_TEXT]
    assert message is not None and not message.fuzzy
    assert message.string.startswith(start)


def test_command_line_tells_the_refused_chain_in_plain_words():
    """The command line's ``build_will``: a ``UserFacingException`` with
    the plain sentence, no raw "chain:" text."""
    error = WillChainError("chain: two transactions with one txid")
    with patch("bal.cli.controller.build.build_will", side_effect=error):
        with pytest.raises(UserFacingException) as raised:
            BalController.build_will(SimpleNamespace())
    assert str(raised.value) == CHAIN_TEXT
