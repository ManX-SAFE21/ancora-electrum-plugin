"""Tests for bal/i18n.py, BAL's translation layer (PLAN_I18N.md, phase 1).

The BAL catalogs are built in memory (see ``_mo_bytes``), so these tests need
no compiled .mo file and no Babel. Electrum's translator is replaced by a
small dictionary, so the tests do not depend on Electrum's own catalogs.

Run standalone (``python3 tests/test_i18n.py``) or with pytest.
"""

import array
import os
import struct
import sys
from contextlib import contextmanager
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from bal import i18n  # noqa: E402

IT_MO = "locale/it_IT/LC_MESSAGES/bal.mo"


def _mo_bytes(messages):
    """Return a GNU .mo catalog (UTF-8) holding ``messages`` {msgid: msgstr}.

    Same layout as CPython's Tools/i18n/msgfmt.py: header, the sorted msgid
    and msgstr index tables, then the NUL-terminated strings.
    """
    entries = {"": "Content-Type: text/plain; charset=UTF-8\n"}
    entries.update(messages)
    keys = sorted(entries)
    ids = strs = b""
    offsets = []
    for key in keys:
        msgid, msgstr = key.encode("utf-8"), entries[key].encode("utf-8")
        offsets.append((len(ids), len(msgid), len(strs), len(msgstr)))
        ids += msgid + b"\0"
        strs += msgstr + b"\0"
    keystart = 7 * 4 + 16 * len(keys)
    valuestart = keystart + len(ids)
    koffsets, voffsets = [], []
    for o1, l1, o2, l2 in offsets:
        koffsets += [l1, o1 + keystart]
        voffsets += [l2, o2 + valuestart]
    header = struct.pack(
        "Iiiiiii", 0x950412DE, 0, len(keys), 7 * 4, 7 * 4 + len(keys) * 8, 0, 0
    )
    return header + array.array("i", koffsets + voffsets).tobytes() + ids + strs


class FakePlugin:
    """Stands in for Electrum's BasePlugin: serves read_file() from a dict."""

    def __init__(self, files=None):
        self.files = files or {}
        self.requested = []

    def read_file(self, filename):
        self.requested.append(filename)
        if filename not in self.files:
            raise KeyError(filename)  # what a missing entry in the zip raises
        return self.files[filename]


@contextmanager
def _i18n_state(electrum=None, files=None, lang=None):
    """Fake Electrum's translations, load a fake BAL catalog, restore after.

    Restoring matters: bal.i18n keeps module-level state, shared with every
    other test of the same pytest run.
    """
    saved = (i18n._electrum_gettext, i18n._catalog)
    table = electrum or {}
    i18n._electrum_gettext = lambda msg: table.get(msg, msg)
    plugin = FakePlugin(files)
    try:
        i18n.set_language(plugin, lang)
        yield plugin
    finally:
        i18n._electrum_gettext, i18n._catalog = saved
        i18n._rejected.clear()


def test_empty_string_is_never_translated():
    # For gettext "" is the key of the catalog header.
    with _i18n_state(files={IT_MO: _mo_bytes({})}, lang="it_IT"):
        assert i18n._catalog is not None
        assert i18n._("") == ""


def test_electrum_translation_wins_over_bal_catalog():
    catalog = _mo_bytes({"Cancel": "Cancella", "Heirs": "Eredi"})
    with _i18n_state(
        electrum={"Cancel": "Annulla"}, files={IT_MO: catalog}, lang="it_IT"
    ):
        assert i18n._("Cancel") == "Annulla"  # Electrum has it: Electrum wins
        assert i18n._("Heirs") == "Eredi"  # only BAL has it


def test_english_source_when_nobody_translates():
    with _i18n_state(files={IT_MO: _mo_bytes({"Heirs": "Eredi"})}, lang="it_IT"):
        assert i18n._("Not in any catalog") == "Not in any catalog"


def test_translation_with_different_fields_is_rejected():
    catalog = _mo_bytes(
        {
            "{} heirs": "eredi",  # field dropped: rejected
            "Fee: {}": "Commissione: {}",
            "{0} of {1}": "{1} di {0}",  # re-ordered: allowed
            "Hello {name}": "Ciao {nome}",  # field renamed: rejected
        }
    )
    with _i18n_state(files={IT_MO: catalog}, lang="it_IT"):
        assert i18n._("{} heirs") == "{} heirs"
        assert i18n._("Fee: {}") == "Commissione: {}"
        assert i18n._("{0} of {1}") == "{1} di {0}"
        assert i18n._("Hello {name}") == "Hello {name}"


def test_keeps_format_fields():
    assert i18n.keeps_format_fields("Amount: {}", "Importo: {}")
    assert not i18n.keeps_format_fields("Amount: {}", "Importo")
    assert not i18n.keeps_format_fields("Amount: {}", "Importo: {} {}")
    assert not i18n.keeps_format_fields("Amount: {}", "Importo: {")  # malformed
    assert i18n.keeps_format_fields("$wallet_name", "$wallet_name")


def test_english_loads_no_catalog():
    with _i18n_state(files={IT_MO: _mo_bytes({"Heirs": "Eredi"})}, lang="en_UK") as p:
        assert i18n._catalog is None
        assert p.requested == []
        assert i18n._("Heirs") == "Heirs"


def test_no_language_loads_no_catalog():
    for lang in (None, ""):
        with _i18n_state(files={IT_MO: _mo_bytes({"Heirs": "Eredi"})}, lang=lang):
            assert i18n._catalog is None


def test_missing_catalog_falls_back_to_english():
    with _i18n_state(files={}, lang="it_IT") as plugin:
        assert i18n._catalog is None
        assert plugin.requested == [IT_MO, "locale/it/LC_MESSAGES/bal.mo"]
        assert i18n._("Heirs") == "Heirs"


def test_broken_catalog_falls_back_to_english():
    with _i18n_state(files={IT_MO: b"this is not a catalog"}, lang="it_IT"):
        assert i18n._catalog is None
        assert i18n._("Heirs") == "Heirs"


def test_language_code_without_country_is_tried():
    files = {"locale/it/LC_MESSAGES/bal.mo": _mo_bytes({"Heirs": "Eredi"})}
    with _i18n_state(files=files, lang="it_IT"):
        assert i18n._("Heirs") == "Eredi"


def test_n_marks_without_translating():
    with _i18n_state(files={IT_MO: _mo_bytes({"Heirs": "Eredi"})}, lang="it_IT"):
        assert i18n.N_("Heirs") == "Heirs"
        assert i18n._(i18n.N_("Heirs")) == "Eredi"


def test_init_from_config_uses_electrum_language_setting():
    config = SimpleNamespace(LOCALIZATION_LANGUAGE="it_IT")
    plugin = FakePlugin({IT_MO: _mo_bytes({"Heirs": "Eredi"})})
    with _i18n_state():
        i18n.init_from_config(plugin, config)
        assert i18n._("Heirs") == "Eredi"


def test_init_from_config_without_setting_uses_system_language():
    import electrum.gui.default_lang as default_lang

    saved = default_lang.get_default_language
    default_lang.get_default_language = lambda gui_name=None: "it_IT"
    config = SimpleNamespace(LOCALIZATION_LANGUAGE="")
    plugin = FakePlugin({IT_MO: _mo_bytes({"Heirs": "Eredi"})})
    try:
        with _i18n_state():
            i18n.init_from_config(plugin, config)
            assert i18n._("Heirs") == "Eredi"
    finally:
        default_lang.get_default_language = saved


def test_init_from_config_never_raises():
    plugin = FakePlugin({IT_MO: _mo_bytes({"Heirs": "Eredi"})})
    with _i18n_state():
        i18n.init_from_config(plugin, object())  # config without the setting
        assert i18n._catalog is None


# --- Stored data stays language-neutral (PLAN_I18N.md, phase 2) -------------


def test_status_history_is_stored_in_english():
    from bal.core.will import WillItem

    catalog = _mo_bytes({"Signed": "Firmato", "Valid": "Valida"})
    with _i18n_state(files={IT_MO: catalog}, lang="it_IT"):
        # A bare item: set_status() only needs its status fields.
        item = WillItem.__new__(WillItem)
        item.status = ""
        item.STATUS = WillItem.copy_status_table(WillItem.STATUS_DEFAULT)
        item.set_status("COMPLETE", True)
        item.set_status("VALID", False)
        assert item.status == ".Signed.NOT Valid"


def test_status_history_is_translated_for_display():
    from bal.core.will import format_status_history

    catalog = _mo_bytes(
        {
            "Signed": "Firmato",
            "Valid": "Valida",
            "NOT {}": "NON {}",
            "New": "Nuovo",
            "Replaced": "Sostituita",
        }
    )
    with _i18n_state(files={IT_MO: catalog}, lang="it_IT"):
        assert format_status_history(".Signed.NOT Valid") == ".Firmato.NON Valida"
        # A real history saved by an older version (tests/samanta7), which
        # stored some labels already translated: known English labels are
        # translated, the other tokens are kept as they are.
        assert (
            format_status_history("New.Firmato.Pushed.Checked.Replaced")
            == "Nuovo.Firmato.Pushed.Checked.Sostituita"
        )
        assert format_status_history(".SignedERROR!!!") == ".FirmatoERROR!!!"
    with _i18n_state():  # English GUI: shown exactly as stored
        assert format_status_history("New.Firmato.Pushed") == "New.Firmato.Pushed"


class _FakeConfig(dict):
    """Minimal stand-in for Electrum's SimpleConfig (get / set_key)."""

    def get(self, key, default=None):
        return dict.get(self, key, default)

    def set_key(self, key, value, save=True):
        self[key] = value


def test_translatable_config_default_follows_language():
    from bal.core.plugin_base import BalConfig

    config = _FakeConfig()
    summary = BalConfig(config, "summary", "Will of $wallet_name", translatable=True)
    plain = BalConfig(config, "label", "BAL label")
    catalog = _mo_bytes(
        {"Will of $wallet_name": "Testamento di $wallet_name", "BAL label": "X"}
    )
    with _i18n_state(files={IT_MO: catalog}, lang="it_IT"):
        assert summary.get() == "Testamento di $wallet_name"  # nothing stored
        assert summary.localized_default() == "Testamento di $wallet_name"
        summary.set("Testamento di $wallet_name")  # e.g. the reset button
        assert config["summary"] == "Will of $wallet_name"  # stored in English
        assert summary.get() == "Testamento di $wallet_name"
        summary.set("My own text")  # the user's text is kept as it is
        assert summary.get() == "My own text"
        assert plain.get() == "BAL label"  # not translatable: never translated
    with _i18n_state():
        summary.set("Will of $wallet_name")
        assert summary.get() == "Will of $wallet_name"


def test_calendar_reminder_suffix_is_translatable():
    from datetime import datetime, timedelta

    from bal.core.reminders import build_ics_reminders

    now = datetime(2026, 1, 1)
    kwargs = dict(
        locktime=now + timedelta(days=400), basic_mode=True, description="d",
        summary="s", wallet_name="w", heirs_details="", version="0", now=now,
    )
    assert "s (reminder 1/" in build_ics_reminders(**kwargs)  # CLI default
    ics = build_ics_reminders(reminder_suffix="(promemoria {idx}/{total})", **kwargs)
    assert "s (promemoria 1/" in ics


def test_qr_preset_labels_match_chunk_presets():
    # The labels are marked for translation in common.py because
    # qrtransfer.py must stay free of Electrum imports (Android copy).
    from bal.core.qrtransfer import CHUNK_PRESETS
    from bal.gui.qt.common import QR_PRESET_LABELS

    assert tuple(label for label, _budget in CHUNK_PRESETS) == QR_PRESET_LABELS


def test_real_translator_is_electrums():
    # Outside the fakes above, BAL asks Electrum's real translator first.
    from electrum.i18n import _ as electrum_gettext

    assert i18n._electrum_gettext is electrum_gettext


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
    print(f"[OK] All {len(tests)} i18n tests passed")
