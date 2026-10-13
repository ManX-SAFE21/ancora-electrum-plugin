"""
bal.i18n
========

Translation layer of the BAL plugin (gettext domain ``bal``).

Why BAL has its own catalog
---------------------------
Electrum translates its interface with gettext (domain ``electrum``). Its
translators only see Electrum's own source code, so the texts of an external
(zip) plugin such as BAL are not in Electrum's catalog, and Electrum offers no
way to translate external plugins. BAL therefore ships its own compiled
catalogs inside the plugin: ``locale/<lang>/LC_MESSAGES/bal.mo``.

Lookup order
------------
:func:`_` returns the first translation found in:

1. **Electrum's catalog.** When Electrum already translates the exact same
   English text, its translation wins. BAL then reads like Electrum and
   follows Electrum's own translation fixes; if Electrum ever ships BAL as an
   internal plugin, Electrum's translations take over with no change here
   (owner's decision D7 in ``PLAN_I18N.md``).
2. **BAL's catalog**, for the texts that only BAL has.
3. **The English source text.**

Language
--------
Electrum picks the GUI language once, at start-up (``run_electrum``), and a
change needs a restart. :func:`init_from_config` repeats Electrum's choice
once, when the Qt plugin is created. BAL never detects the language by
itself: otherwise part of a window could be in one language and part in
another. The CLI never calls it, so the CLI stays in English, like Electrum's.

Rules for translatable texts
----------------------------
* Wrap literal English text only: ``_("Sign the will")``.
* Put the variable parts in ``{}`` and fill them *after* translating:
  ``_("{} heirs").format(count)``. Never build the text with an f-string or
  ``%`` inside ``_()``: it would change at run time and never match a catalog
  entry (Ruff rules INT001-INT003 catch this).
* Texts evaluated at import time (class attributes, tables, constants) are
  marked with :func:`N_` and passed to ``_()`` when they are displayed. Every
  class body runs before :func:`init_from_config`, so a ``_()`` there would
  always return English.
* Never translate text that is stored or compared (wallet labels, the saved
  status history, config values used to recognise BAL transactions): stored
  data must not depend on the UI language.
"""

import gettext
import io
import string
from typing import Optional

from electrum.i18n import _ as _electrum_gettext
from electrum.logging import get_logger

_logger = get_logger(__name__)

#: gettext domain of the BAL catalogs.
DOMAIN = "bal"

# BAL catalog of the current language, or None (English, CLI, or no usable
# catalog). Set by set_language().
_catalog: Optional[gettext.GNUTranslations] = None

# Language code requested by set_language() (e.g. "it_IT"), or None.
_language: Optional[str] = None

# Languages that write decimals with a comma (and group thousands with a
# dot). Only used to format numbers inside messages, e.g. "55,6%".
_DECIMAL_COMMA_LANGUAGES = ("it", "es", "de", "fr", "pt", "nl")

# Source texts whose BAL translation was rejected: the warning is logged once,
# not at every repaint of the widget that shows the text.
_rejected: set = set()

_formatter = string.Formatter()


def keeps_format_fields(msg: str, translation: str) -> bool:
    """Tell whether ``translation`` keeps the ``{}`` fields of ``msg``.

    A translation that adds, drops or renames a replacement field would make
    the ``.format()`` call that follows raise, or put a value in the wrong
    place, so such a translation must not be used.

    The rules are copied from Electrum's
    ``_ensure_translation_keeps_format_string_syntax_similar``
    (``electrum/i18n.py``, MIT licence, Copyright (C) The Electrum developers)
    rather than importing that private function, which may change without
    notice. Keep them identical, so a BAL translation is accepted or rejected
    exactly like an Electrum one. The only addition: a malformed source text
    returns False here instead of raising.
    """
    try:
        # Tuples of (literal_text, field_name, format_spec, conversion).
        parsed1 = list(_formatter.parse(msg))
        parsed2 = list(_formatter.parse(translation))
    except ValueError:  # malformed format string
        return False
    if len(parsed1) != len(parsed2):
        return False
    # The set of field names must not change (re-ordering them is allowed).
    return {t[1] for t in parsed1} == {t[1] for t in parsed2}


def _(msg: str) -> str:
    """Return ``msg`` translated into the GUI language.

    See the module docstring for the lookup order. ``_("")`` returns ``""``:
    for gettext the empty text is the key of the catalog header.
    """
    if msg == "":
        return ""
    translation = _electrum_gettext(msg)
    if translation != msg or _catalog is None:
        # Electrum's translation wins (Electrum has already checked its {}
        # fields); without a BAL catalog there is nothing else to look up.
        return translation
    translation = _catalog.gettext(msg)
    if translation == msg or keeps_format_fields(msg, translation):
        return translation
    if msg not in _rejected:
        _rejected.add(msg)
        _logger.warning(
            f"rejected BAL translation, replacement fields differ: "
            f"{msg!r} -> {translation!r}"
        )
    return msg


def N_(msg: str) -> str:  # noqa: N802 (the conventional gettext marker name)
    """Mark ``msg`` as translatable and return it unchanged.

    Used where a text is defined at import time (class attributes, tables,
    constants). Extraction tools (Babel, xgettext) collect ``N_("...")`` like
    ``_("...")``; the code passes the value to ``_()`` when it shows it.
    """
    return msg


def set_language(plugin, lang: Optional[str]) -> None:
    """Load the BAL catalog of ``lang`` (for example ``"it_IT"``), or none.

    ``None``, ``""`` and English (``"en_*"``) load no catalog, so the English
    source texts are shown, as Electrum does for ``en_*``.

    The catalog is read with ``plugin.read_file()``, the Electrum API BAL uses
    for its icons: it works both from the installed zip and from a development
    checkout. ``gettext.translation(localedir=...)`` is not used because,
    pointed inside a zip, it silently finds nothing.

    If ``locale/<lang>/`` has no catalog, the plain language code is tried
    (``locale/it/`` for ``it_IT``). A missing or unreadable catalog is logged
    and BAL stays in English: a translation problem must never stop the
    plugin.
    """
    global _catalog, _language
    _catalog = None
    _rejected.clear()
    _language = lang if isinstance(lang, str) else None
    if not isinstance(lang, str) or not lang or lang.startswith("en_"):
        return
    codes = [lang]
    if "_" in lang:
        codes.append(lang.split("_", 1)[0])
    for code in codes:
        path = "locale/{}/LC_MESSAGES/{}.mo".format(code, DOMAIN)
        try:
            data = plugin.read_file(path)
        except Exception:
            # Not shipped for this code (a zip raises KeyError, a development
            # folder raises OSError): try the next code.
            continue
        try:
            _catalog = gettext.GNUTranslations(io.BytesIO(data))
        except Exception as e:
            _logger.info(f"unreadable BAL catalog {path}, using English: {e!r}")
            return
        _logger.info(f"BAL catalog loaded: {path}")
        return
    _logger.info(f"no BAL catalog for {lang!r}, using English")


def language() -> Optional[str]:
    """Return the language code :func:`set_language` was given, or None.

    For example ``"it_IT"``; None in English without a code, on the command
    line and before the Qt plugin loads a catalog. The BASIC wizard (#134)
    uses it to write the month names in the same language as the texts
    around them (a ``QLocale`` of the system could be another language).
    """
    return _language


def number_separators() -> tuple:
    """Return ``(decimal, thousands)`` separators for the current language.

    ``(",", ".")`` for the languages in ``_DECIMAL_COMMA_LANGUAGES`` (Italian
    "55,6%", "2.000.000"), else ``(".", ",")``. A tiny local rule instead of
    a locale library: Babel is only a build-time dependency and Python's
    ``locale`` depends on what the OS has installed.
    """
    code = (_language or "").split("_", 1)[0].lower()
    return (",", ".") if code in _DECIMAL_COMMA_LANGUAGES else (".", ",")


def init_from_config(plugin, config) -> None:
    """Load the BAL catalog of the language Electrum's Qt GUI is using.

    Repeats Electrum's own choice in ``run_electrum``: the ``language``
    setting (Preferences > Language) or, when it is empty, the system language
    if Electrum supports it, else English. Called once, from the Qt plugin's
    ``__init__``. Never raises.
    """
    try:
        lang = config.LOCALIZATION_LANGUAGE
        if not lang:
            from electrum.gui.default_lang import get_default_language

            lang = get_default_language(gui_name="qt")
    except Exception as e:
        # Electrum also falls back to English when it cannot read the
        # system language.
        _logger.info(f"could not read the GUI language, using English: {e!r}")
        lang = None
    set_language(plugin, lang)
