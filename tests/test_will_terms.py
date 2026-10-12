"""
One word for the will in the commands and their hints (CHANGELOG #94).

Owner: "Check Inheritance" / "Invalida il piano" / "Trasmetti il piano"
next to "testamento" everywhere else. The commands now say "will"
("Broadcast will", "Invalidate will", "Check your will", "Rebroadcast will")
and every hint names the button exactly as the button is translated: the
Italian hint said "press 'Trasmetti il piano'" while the menu said
"Trasmissione".

Run (standalone or with pytest):
    python3 tests/test_will_terms.py
"""

import os

from babel.messages.pofile import read_po

LOCALE = os.path.join(os.path.dirname(__file__), os.pardir, "bal", "locale")
COMMANDS = ("Broadcast will", "Invalidate will", "Check your will", "Rebroadcast will")
HINTS = (
    "Next steps (manual): press 'Sign' to sign your will, then 'Broadcast will' "
    "to send it to the will-executors.",
    "Next step (manual): press 'Broadcast will' to send your will to the "
    "will-executors.",
    "Your will has been rebuilt and now needs to be signed and re-sent to the "
    "will-executors.\n\nNext steps (manual):\n  1. Press 'Sign' to sign the new "
    "transaction.\n  2. Press 'Broadcast will' to send it to the will-executors.",
)
# Hints of the delivery settings (#123) that name the 'Check your will' button.
CHECK_HINTS = (
    "Nothing is signed or sent until you choose the delivery date: press "
    "'Check your will'.",
    "To use the later date again, choose it and press 'Check your will': the "
    "old will must be invalidated first (miner fee).",
)


def _catalog(lang):
    with open(os.path.join(LOCALE, lang, "LC_MESSAGES", "bal.po"), "rb") as f:
        return read_po(f, locale=lang)


def test_commands_say_will():
    for lang in ("it_IT", "es_ES"):
        cat = _catalog(lang)
        for msgid in COMMANDS:
            text = cat.get(msgid).string
            assert text, (lang, msgid)
            assert "piano" not in text.lower() and "eredit" not in text.lower()
            assert "plan" not in text.lower() and "herencia" not in text.lower()


def test_hints_name_the_real_button():
    for lang in ("it_IT", "es_ES"):
        cat = _catalog(lang)
        button = cat.get("Broadcast will").string
        sign = cat.get("Sign").string
        for msgid in HINTS:
            text = cat.get(msgid).string
            assert "'{}'".format(button) in text, (lang, text)
            if "'Sign'" in msgid:
                assert "'{}'".format(sign) in text, (lang, text)


def test_check_hints_name_the_real_button():
    """The #123 hints name the 'Check your will' button exactly as it is
    translated, quoted like the #94 hints (not "the Check button")."""
    for lang in ("it_IT", "es_ES"):
        cat = _catalog(lang)
        button = cat.get("Check your will").string
        assert button, lang
        for msgid in CHECK_HINTS:
            assert "'Check your will'" in msgid, msgid
            entry = cat.get(msgid)
            assert entry is not None and "fuzzy" not in entry.flags, (lang, msgid)
            assert "'{}'".format(button) in entry.string, (lang, entry.string)


def test_italian_will_rows_without_piano():
    cat = _catalog("it_IT")
    for msgid in ("Invalidate your old will", "Invalidating old will",
                  "Postponed: invalidating old will",
                  "Broadcasting your will to executors",
                  "Please sign and broadcast this transaction to invalidate current will",
                  "Broadcasting Transactions to Will-Executors:"):
        text = cat.get(msgid).string
        assert "piano" not in text and "testamento" in text, text


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print("{} tests passed".format(len(tests)))
