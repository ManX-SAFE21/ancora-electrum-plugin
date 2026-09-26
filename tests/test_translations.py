"""Safety checks on BAL's translation catalogs (PLAN_I18N.md, section 4.4).

Runs on every ``bal/locale/<lang>/LC_MESSAGES/bal.po`` (the sources that
build_zip.py compiles into the zip). A translation is shown to the user as
if BAL wrote it, so a wrong or malicious one must fail here, before a zip is
built:

* the catalog must parse and compile;
* the ``{}`` replacement fields must match the English text (the same rule
  bal.i18n applies at run time), and so must the ``$tokens`` of the calendar
  texts (``$wallet_name``, ``$heirs_complete``), which Electrum does not check;
* no Bitcoin address, e-mail address, URL or long letters-and-digits word may
  appear in a translation unless the English text has the same one: the
  patterns are copied from Electrum's ``electrum-locale/update.py`` (MIT
  licence, Copyright (C) The Electrum developers), which rejects translations
  that try to slip in an address or a link.

Run standalone (``python3 tests/test_translations.py``, prints a per-language
summary) or with pytest. Needs Babel, like build_zip.py.
"""

import io
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from babel.messages.mofile import write_mo  # noqa: E402
from babel.messages.pofile import read_po  # noqa: E402

from bal.i18n import keeps_format_fields  # noqa: E402

LOCALE_DIR = Path(__file__).resolve().parent.parent / "bal" / "locale"

# From electrum-locale/update.py (see the module docstring).
SUSPICIOUS = {
    "Bitcoin address": re.compile("([13]|bc1)[a-zA-Z0-9]{30,}"),
    "e-mail address": re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
    "URL": re.compile(r"\S+\.\S*\w+\S*/\S+"),
    "URL scheme": re.compile(r"http(s){0,1}://"),
    "letters-and-digits word": re.compile(
        r"(?a)(?=\w{16,})((\w*[a-zA-Z]\w*[0-9]\w*)|(\w*[0-9]\w*[a-zA-Z]\w*))"
    ),
}
TOKEN = re.compile(r"\$\w+")


def _catalogs():
    return sorted(LOCALE_DIR.glob("*/LC_MESSAGES/bal.po"))


def _messages(po_path):
    """Yield the translated, non-fuzzy (msgid, msgstr) pairs of a catalog."""
    with open(po_path, "rb") as f:
        catalog = read_po(f)
    for m in catalog:
        if m.id and isinstance(m.id, str) and m.string and not m.fuzzy:
            yield m.id, m.string


def check_catalog(po_path):
    """Return a list of problems found in one catalog (empty = fine)."""
    problems = []
    for msgid, msgstr in _messages(po_path):
        if not keeps_format_fields(msgid, msgstr):
            problems.append("{} fields differ: {!r}".format("{}", msgid))
        if sorted(TOKEN.findall(msgid)) != sorted(TOKEN.findall(msgstr)):
            problems.append("$tokens differ: {!r}".format(msgid))
        for name, regex in SUSPICIOUS.items():
            for match in regex.finditer(msgstr):
                if match.group(0) not in msgid:
                    problems.append(
                        "{} {!r} not in the English text: {!r}".format(
                            name, match.group(0), msgid
                        )
                    )
    return problems


def summary(po_path):
    """Return (translated, untranslated, fuzzy) counts of a catalog."""
    with open(po_path, "rb") as f:
        catalog = read_po(f)
    msgs = [m for m in catalog if m.id]
    fuzzy = sum(1 for m in msgs if m.fuzzy)
    translated = sum(1 for m in msgs if m.string and not m.fuzzy)
    return translated, len(msgs) - translated - fuzzy, fuzzy


def test_there_is_an_italian_catalog():
    assert LOCALE_DIR / "it_IT" / "LC_MESSAGES" / "bal.po" in _catalogs()


def test_catalogs_compile():
    for po in _catalogs():
        with open(po, "rb") as f:
            catalog = read_po(f)
        buf = io.BytesIO()
        write_mo(buf, catalog)
        assert buf.getvalue(), po


def test_translations_are_safe():
    problems = [
        "{}: {}".format(po.parent.parent.name, p)
        for po in _catalogs()
        for p in check_catalog(po)
    ]
    assert not problems, "\n".join(problems)


def test_suspicious_patterns_are_detected():
    # A translation that adds an address or a link must be caught.
    regexes = SUSPICIOUS.values()
    assert any(r.search("invia a bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq") for r in regexes)
    assert any(r.search("scarica da https://example.com") for r in regexes)
    assert any(r.search("scrivi a truffa@example.com") for r in regexes)
    assert not any(r.search("Firma il testamento") for r in regexes)


if __name__ == "__main__":
    test_there_is_an_italian_catalog()
    test_catalogs_compile()
    test_suspicious_patterns_are_detected()
    for po in _catalogs():
        translated, untranslated, fuzzy = summary(po)
        print(
            "{}: {} translated, {} untranslated, {} fuzzy".format(
                po.parent.parent.name, translated, untranslated, fuzzy
            )
        )
    test_translations_are_safe()
    print("[OK] translation catalogs are safe")
