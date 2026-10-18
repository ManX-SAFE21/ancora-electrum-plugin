"""The brand of the plugin (#163): the name Àncòra is the same in every
language, the plugin list shows the manifest name and icon, and every icon
the code reads is shipped.

Owner, 2026-10-18: the brand name stays the same in all languages, with its
accents wherever possible; the slogan stays in Italian.
"""
import json
import os
import re

from babel.messages.pofile import read_po

from bal.core import brand

BAL = os.path.join(os.path.dirname(__file__), os.pardir, "bal")
LOCALE = os.path.join(BAL, "locale")
CATALOGS = [os.path.join(LOCALE, lang, "LC_MESSAGES", "bal.po") for lang in ("it_IT", "es_ES")]

#: The only texts to translate that hold the name: the two calendar defaults,
#: editable in the settings with their $tokens (the name cannot come through
#: ``{}`` there), so every translation must keep it as it is.
CALENDAR_MSGIDS = {
    "Àncòra will execution of $wallet_name\r\n heirs list:  \r\n$heirs_complete",
    "Àncòra - Will execution of $wallet_name",
}


def _manifest():
    """``bal/manifest.json`` read as Electrum reads it (bytes, from the zip)."""
    with open(os.path.join(BAL, "manifest.json"), "rb") as f:
        return json.load(f)


def _catalog(path):
    """The messages of a .po file."""
    with open(path, "rb") as f:
        return [m for m in read_po(f) if m.id]


def test_the_names():
    """The name with its accents, the plugin-list name and the plain name."""
    assert brand.NAME == "Àncòra"
    assert brand.FULLNAME == "Àncòra (Safe21)"
    assert brand.NAME_ASCII == "Ancora" and brand.NAME_ASCII.isascii()
    assert brand.SLOGAN == "Àncora per te, ancòra per loro."


def test_manifest_name_icon_and_description():
    """Electrum's plugin list shows the full name, the anchor icon and the
    English description, which starts with the Italian slogan."""
    m = _manifest()
    assert m["fullname"] == brand.FULLNAME
    assert m["icon"] == "icons/ancora-icon-light-128.png"
    assert os.path.isfile(os.path.join(BAL, m["icon"]))
    assert m["description"].startswith(brand.SLOGAN + " " + brand.NAME + " builds")
    assert "Heartbeat" not in m["fullname"] + m["description"].split(".")[0]
    # The internal identity stays until Phase 6.
    assert m["name"] == "bal"


def test_manifest_is_plain_ascii():
    """The accents are written as \\u escapes: the file reads the same with
    any encoding Electrum might use to open it."""
    with open(os.path.join(BAL, "manifest.json"), "rb") as f:
        assert f.read().isascii()


def test_every_icon_the_code_reads_is_shipped():
    """Every ``icons/<file>.png`` named in the plugin's code exists (the old
    icons were removed by #163)."""
    names = set()
    for folder, _dirs, files in os.walk(BAL):
        for name in files:
            if name.endswith((".py", ".json")):
                with open(os.path.join(folder, name), encoding="utf-8") as f:
                    names.update(re.findall(r"icons/([\w.-]+\.png)", f.read()))
    assert {"ancora-icon-light-16.png", "ancora-icon-light-64.png", "ancora-icon-light-128.png",
            "ancora-lockup.png"} <= names
    for name in names:
        assert os.path.isfile(os.path.join(BAL, "icons", name)), name


def test_the_name_is_never_translated():
    """No text to translate holds the old name, and the new name only in the
    two calendar defaults; their translations keep it unchanged."""
    with open(os.path.join(LOCALE, "bal.pot"), "rb") as f:
        ids = [m.id for m in read_po(f) if m.id]
    assert not [i for i in ids if "Safe21 Heartbeat" in str(i)]
    assert {i for i in ids if brand.NAME in str(i)} == CALENDAR_MSGIDS
    for path in CATALOGS:
        for m in _catalog(path):
            if m.id in CALENDAR_MSGIDS and m.string:
                assert m.string.startswith(brand.NAME), (path, m.string)
