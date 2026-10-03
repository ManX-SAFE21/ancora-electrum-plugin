# PLAN_I18N — Italian translation of the BAL Electrum plugin (i18n)

> **Status:** DRAFT. Every phase still needs the owner's explicit "OK" before any code is written (HANDOFF.md, rule R4).
> **Prepared:** 2026-09-25, from a read-only analysis (no code was changed). Owner's decisions recorded on 2026-09-26 (section 6).
> **Code base analysed:** `bal-electrum-plugin` `main` @ `4a33116` (2026-09-19, manifest `0.7.0`) and Electrum `4.8.0`.
> **Line numbers** refer to that commit. After merging a newer `main`, find the code by content.

## 0. Read this first

1. Read `HANDOFF.md` (rules R1–R4, ZIP-FIRST, CREDIT-SAVING) and `AGENTS.md` (venvs, tests). They still apply.
2. **One exception to HANDOFF.md.** Section 5 ("Git / delivery workflow") and section 7, step 7, say: work directly on `main`, push to `origin/main`, run `make-release.sh`. **That does NOT apply to this task.** See section 3 (dedicated branch).
3. The owner's decisions (D1–D7) are recorded in section 6. For anything not covered there, ask the owner (rule R3).

## 1. Goal and scope

**Goal:** when Electrum is set to Italian, the whole BAL interface appears in Italian. The mechanism must allow adding more languages later just by adding one `.po` file.

**In scope**
- The Qt GUI of the plugin (`bal/gui/qt/`) and the GUI-free strings it displays (`bal/core/`).
- Languages: Italian (`it_IT`) and Spanish (`es_ES`).

**Out of scope** (owner's decisions, or by design)
- **BAL Easy Heirs**: excluded by the owner.
- **CLI output** (`bal/cli/`): stays English, exactly like Electrum (`run_electrum`: "the CLI is intentionally always non-localized").
- `bal/wallet_util/` (standalone helper scripts), the Android reader (`android/`, which uses Android resources), the manual, the website and the docs.
- Other languages: later. The infrastructure will support them.

## 2. Background (verified)

### 2.1 How Electrum 4.8.0 does it

- **gettext with one domain, `electrum`.** Catalogs live in `electrum/locale/locale/<lang>/LC_MESSAGES/electrum.mo` (`electrum/i18n.py`, `LOCALE_DIR`). Every string goes through `electrum.i18n._()`.
- **The language is set once, at startup, for the GUI only** (`run_electrum` L433–451):
  - it uses `config.LOCALIZATION_LANGUAGE` (config key `language`);
  - if that is empty, it uses `electrum.gui.default_lang.get_default_language(gui_name)`, i.e. `QLocale.system().name()`, **but only if** that value is in `electrum.i18n.languages`; otherwise `en_UK`;
  - `en_*` means no translation (the English source strings are shown);
  - changing the language requires a restart.
- **Strings evaluated too early stay English.** Anything evaluated before `set_language()` (for example at import time) is not translated.
- **Safety check inside `_()`.** The decorator `_ensure_translation_keeps_format_string_syntax_similar` rejects a translation whose `{}` fields differ from the source. `_("")` returns `""`. There is an optional `context=` argument (pgettext).
- **Coding rules** (comments in `i18n.py`): no f-strings inside `_()`, no `%` formatting; write `_("… {}").format(x)`. There are no plural forms (`ngettext` is not used anywhere).
- **Pipeline:**
  1. `contrib/locale/push_locale.py` runs xgettext over `electrum/**/*.py`. This includes *internal* plugins.
  2. The strings go to Crowdin, where volunteers translate them.
  3. The `.po` files are stored in the `electrum-locale` repo.
  4. `contrib/locale/build_locale.sh` compiles them with msgfmt into `.mo` files and writes `stats.json`.
- **Translation security:**
  - `electrum-locale/update.py` refuses any translation whose `msgstr` contains a Bitcoin address, an e-mail address, a URL, or a long word mixing letters and digits;
  - `llm_proofreader/` checks every translation PR for vandalism in CI.
- **External plugins:**
  - they are always ZIP files, imported with `zipimport` as `electrum_external_plugins.<name>`;
  - `BasePlugin.read_file(filename) -> bytes` reads a file from inside the ZIP, or from the internal plugin folder;
  - **Electrum provides nothing for translating external plugins.** Their strings are not in `electrum.mo`.
- `i18n.py`, `gui/default_lang.py` and `read_file()` are identical in 4.7.2, 4.8.0 and 4.8.2.
- **Swiss locales** (`it_CH`, `de_CH`, `fr_CH`) are not in Electrum's list, so Electrum starts in English until the user picks a language. BAL must simply follow Electrum and not detect the language itself. Otherwise the UI would be half in one language and half in another.

### 2.2 BAL today (`main` @ `4a33116`)

- Source strings are in English. Most are wrapped in `_`, imported from `electrum.i18n` in these places:
  - `gui/qt/common.py:55`, which re-exports it to every `gui/qt` module;
  - `gui/qt/theme.py:90,108`;
  - `core/will.py:32` and `core/willexecutors.py:25`;
  - `cli/controller.py:29`.
- The package has 483 `_()` calls in 11 files. xgettext finds 365 distinct strings (≈ 2,230 words).
- **With Electrum set to Italian, only 39 of the 365 strings (≈ 11 %) appear in Italian.** They are the generic words that Electrum already translates (Cancel, Save, Address…). Everything specific to BAL stays in English.
- **Issues to fix before translating** (exact locations are in the Appendix):
  - 14 f-strings or `.format()` calls inside `_()`: these strings can never be translated (Ruff INT001/INT002);
  - 15 `_()` calls with a non-literal argument: extraction tools cannot find the text;
  - 14 concatenations around `_()`: the word order breaks in other languages;
  - 18 `_()` calls evaluated at import time (in class bodies): they would stay English (see 4.2);
  - 2 × `_("")`: plain gettext returns the catalog header instead of an empty string;
  - 12 settings help texts (`HelpButton("…")`) and the help texts of 5 `add_widget()` calls are never translated (≈ 500 words in total);
  - other text shown to the user without `_()`.
- **Some stored data depends on the UI language.**
  - `WillItem.set_status()` (`core/will.py:1344`) appends the *translated* status label to `WillItem.status`.
  - That field is saved via `to_dict()` in the wallet DB (`wallet.db.get_dict("will")`, `gui/qt/window.py:232`), in exported files and in QR transfers.
  - Real example from `tests/samanta7`: `"New.Firmato.Pushed.Checked.Replaced"`. Electrum's Italian catalog already translates *Signed*, *Error* and *Expired*, so the saved history mixes languages today.
- **Default texts in `core/plugin_base.py` exist only in English:**
  - `HISTORY_LABEL` (around L246, token `{willexecutor}`);
  - `EVENT_DESCRIPTION` and `EVENT_SUMMARY` (around L331–337, tokens `$wallet_name` and `$heirs_complete`).

## 3. Branch and workflow for this task

The owner decided, on Truman's suggestion, to do this work on a **separate branch on Gitea**, not on `main`.

- **Branch:** `feature/i18n` (proposed name, see D6), created from `main`.
- **Never** commit or push to `main` during this task.
- **Never** run `make-release.sh` from the branch: it tags and publishes a Gitea release. Build test ZIPs with `python3 build_zip.py` only.
- **At the start of every phase:**
  1. merge `origin/main` into `feature/i18n`;
  2. resolve any conflicts;
  3. run the tests again.
- **Where conflicts are likely:**
  - `gui/qt/dialogs.py`, `window.py`, `lists.py`, `plugin.py`, `widgets.py` and `core/will.py`: all were changed on `main` on 2026-09-04, 09-14 and 09-19, and commit `fb88d75` alone changed ≈ 1,800 lines of `dialogs.py`;
  - `CHANGELOG.md`: both sides append at the end, so keep both entries and renumber.
- **ZIP-FIRST for every phase:**
  1. build the ZIP;
  2. the owner (and Truman) test it;
  3. only after the owner's explicit OK, commit on `feature/i18n` and push to `origin/feature/i18n` (who does this: see D3).
- Add one numbered `CHANGELOG.md` entry per phase, in English.
- Run ruff and the tests before every ZIP (HANDOFF.md). The known pre-existing test failures are listed in HANDOFF.md, section 7, step 3.
- **At the end:** open a Pull Request `feature/i18n` → `main` on Gitea (there is a precedent: `feature/bal-qr-transfer`). Release from `main` only after the merge.

## 4. Design (decided)

**Chosen:** a gettext catalog owned by BAL, domain `bal`, shipped inside the plugin ZIP, that follows Electrum's language and is consulted **after** Electrum's own catalog (D7).

**Rejected:**
- keep using `electrum.i18n._`: 89 % of the text stays English;
- Python dictionaries or JSON files: a home-made format with no tooling;
- Qt Linguist (`.ts` / `.qm`): Electrum desktop does not use it, and `core/` has no Qt.

### 4.1 New module `bal/i18n.py`

| Function | Behaviour |
|---|---|
| `_(msg)` | 1. `""` returns `""`. 2. **Electrum first (D7):** if `electrum.i18n._(msg)` differs from `msg`, return it (Electrum has already applied its own `{}` check). 3. Otherwise, if a BAL catalog is loaded and translates `msg`, apply the `{}` safety check and return the translation. The check uses the same logic as Electrum's decorator: **copy it** with an attribution comment (MIT); do not import Electrum's private function. 4. Otherwise return `msg` (English). The CLI and English behave exactly as in Electrum, because no BAL catalog is loaded there. |
| `N_(msg)` | Returns `msg` unchanged. It marks strings defined in tables, constants and class attributes, which are translated later with `_()` when displayed. Babel extracts `N_` by default. |
| `set_language(plugin, lang)` | `None`, `""` or `en_*`: no catalog. Otherwise read `locale/<lang>/LC_MESSAGES/bal.mo` with `plugin.read_file()`, falling back to `locale/<lang[:2]>/…`, and build `gettext.GNUTranslations(io.BytesIO(data))`. If the catalog is missing or broken, log at info level and continue in English. Never raise. |
| `init_from_config(plugin, config)` | `lang = config.LOCALIZATION_LANGUAGE`. If it is empty, use `electrum.gui.default_lang.get_default_language(gui_name="qt")`, guarding against `ImportError` as `run_electrum` does. Then call `set_language()`. |

- **Why Electrum first (D7):** BAL follows Electrum's wording wherever Electrum has it. If Electrum ever ships BAL as an internal plugin, Electrum's own translations (Crowdin) would be used automatically and the BAL catalog would only fill the gaps.
- **Trade-off of D7:** for a text that Electrum already translates, the BAL catalog cannot override Electrum's wording, not even with the D1 glossary. If one such case reads badly in BAL, handle that single case with the owner.
- A text that Electrum "translates" into the identical English text counts as not found and falls through to the BAL catalog. This is harmless: the BAL catalog is prefilled with Electrum's translations (3.3).
- **Do not use `gettext.translation(localedir=…)`.** When pointed inside a ZIP it silently returns `NullTranslations` (verified).
- `plugin.read_file()` is the same API BAL already uses for its icons. It works both for the ZIP install and for the dev symlink install (`AGENTS.md`).
- The state is module-level and set once. Changing the language requires restarting Electrum, as in Electrum itself.

### 4.2 Where the language is set

- In `bal/gui/qt/plugin.py`, `Plugin.__init__` (L67): call `init_from_config(self, config)` first.
- The CLI entry point (`bal/cli/plugin.py`) does not call it, so the CLI stays English.
- **Consequence:** `gui/qt/plugin.py` imports `dialogs`, `widgets` and `window` at module level (L49–51), and those import `lists`. So every class body is evaluated *before* `Plugin.__init__` runs, and any `_()` evaluated at import time would stay English. The cases in Appendix D must be converted in Phase 2.

### 4.3 Files and tools

```
babel.cfg                               # extraction mapping (repo root)
bal/locale/bal.pot                      # template, generated
bal/locale/it_IT/LC_MESSAGES/bal.po     # Italian translations (source of truth)
bal/locale/it_IT/LC_MESSAGES/bal.mo     # compiled (see D4)
```

The tool is **Babel** (`pip install babel`). It is pure Python and works on Windows without GNU gettext. These commands were tested on this code base:

```ini
# babel.cfg
[ignore: bal/cli/**]
[ignore: bal/wallet_util/**]
[python: bal/**.py]
```

```bash
mkdir -p bal/locale                                                     # extract does not create it
pybabel extract -F babel.cfg --no-wrap -o bal/locale/bal.pot .
pybabel init    -i bal/locale/bal.pot -d bal/locale -D bal -l it_IT     # once
pybabel update  -i bal/locale/bal.pot -d bal/locale -D bal --no-wrap    # after each extract
pybabel compile -d bal/locale -D bal --statistics
```

- Today this extracts 323 strings (≈ 1,900 words) from the GUI and core.
- After Phase 2, expect about 360 strings (≈ 2,500 words).
- Poedit (free) can be used to translate or review the `.po` file by hand.

### 4.4 Translation safety (as in Electrum)

- Add a check script (for example `scripts/check_translations.py`) and run it before every ZIP. It must check that:
  - every `.po` file compiles;
  - the `{}` fields are identical to the source (the same rule as the runtime check);
  - the `$tokens` are identical (`$wallet_name`, `$heirs_complete`, …): Electrum does not check these;
  - no `msgstr` contains a Bitcoin address, an e-mail address, a URL or a long letter-and-digit word (use the regexes from `electrum-locale/update.py`), unless the same text is in the `msgid`.

  It must also print the counts of translated, untranslated and fuzzy strings.
- Never put addresses or URLs inside translatable strings; pass them with `{}`.
- The owner reviews every Italian string, especially the warnings about invalidation, locktime, fees and signing.

## 5. Phases

Each phase follows HANDOFF.md: DISCOVER → PLAN (owner's "OK") → EXECUTE → VERIFY → ZIP → test by the owner and Truman → commit on `feature/i18n`.
Phases 1 and 2 change nothing visible, so they may be delivered as a single ZIP/test cycle (see D5).

### Phase 1 — Infrastructure (no visible change)

- NEW `bal/i18n.py` (section 4.1), with docstrings explaining *why* (rule R2).
- `bal/gui/qt/common.py:55`: replace `from electrum.i18n import _` with an import of `_` and `N_` from `bal.i18n`. Use a relative import, zip-safe like the existing `from ...core…` imports.
- `bal/gui/qt/theme.py:90,108`: change the local imports to `bal.i18n`.
- `bal/core/will.py:32` and `bal/core/willexecutors.py:25`: import from `bal.i18n`.
- `bal/cli/controller.py:29`: **unchanged** (the CLI stays English by design).
- `bal/gui/qt/plugin.py`, `Plugin.__init__`: call `init_from_config(self, config)`.
- NEW `tests/test_i18n.py`, covering:
  - the empty string;
  - Electrum's translation wins; the BAL catalog is used only when Electrum has none (D7);
  - rejection of a `{}` mismatch;
  - `en_UK` means no catalog;
  - a missing `.mo` does not crash;
  - a catalog loaded from bytes (use a small test catalog).
- **Verify:**
  - the existing tests (baseline as in HANDOFF.md, section 7);
  - `tests/smoke_test.py`;
  - `build_zip.py` followed by `tests/external_zip_test.py`;
  - manually on Windows 11 with Electrum 4.8.0, once in Italian and once in English: the UI must look exactly as before.

### Phase 2 — String clean-up and language-neutral stored data (no visible change)

- **2.0** Merge `origin/main` first.
- **2.1** Appendix A: turn `_(f"…")` and `_("…".format(x))` into `_("… {}").format(x)`. Include `cli/controller.py`, for lint consistency only.
- **2.2** Appendix C: turn each concatenation into one string with `{}`.
  - Keep HTML outside the strings where possible, e.g. `"<b>{}</b>".format(_("Support:"))`.
  - Fix the malformed `_("<b>Willexecutor:</b:")` (`widgets.py:1366`).
- **2.3** Appendix B: mark the literal with `N_()` where it is defined, or translate it at the call site.
  - The helpers `add_widget()` (`common.py:180`), `get_window_title()` (`plugin.py:1085`) and `qlabel()` (`widgets.py:1319`) need ONE rule: callers pass text that is already translated, and the helper does not call `_()` again.
  - Today `window.py:290` translates twice.
- **2.4** Appendix D: replace class-level `_()` with `N_()` and translate when the text is displayed.
  - In `lists.py`, `update_headers(self.__class__.headers)` (L248, 647, 1094) must receive a translated copy.
  - For the wizard `title` / `message` (`dialogs.py` L380, 419–420, 527–528, 547–548), use `_(self.title)` and `_(self.message)` at L325 and L327.
- **2.5** Appendix E: replace `_("")` with `""`.
- **2.6** Appendix F and G: make the settings help texts and labels in `plugin.py` translatable.
- **2.7** Appendix H, plus a full sweep for any other text shown to the user.
- **2.8** **Stored status** (`core/will.py:1344`):
  - store the **English** label (mark the `STATUS_DEFAULT` labels with `N_()`); no `_()` at write time;
  - add a helper `format_status_history(status) -> str` that splits on `.`, translates the tokens that are known English labels (with an optional `NOT ` prefix), and leaves unknown or legacy tokens unchanged (`New`, `Firmato`, …);
  - use it wherever the status is shown (`lists.py:589`, `widgets.py:1336`);
  - this stays backward compatible: existing wallets keep displaying correctly, and older BAL versions still read plain English;
  - test it with the real legacy strings in `tests/samanta7`.
- **2.9** **Default texts** (`core/plugin_base.py`: `HISTORY_LABEL`, `EVENT_DESCRIPTION`, `EVENT_SUMMARY`):
  - mark the English defaults with `N_()`;
  - when the stored value is missing **or equal to the English default**, use the translated default at the moment the text is used (calendar export, history label);
  - the tokens must survive translation.
  - **Changed during Phase 2 (safety):** `HISTORY_LABEL` stays English and is *not* translatable. `Util._label_matches_history()` uses it to recognise BAL's own local transactions (stale-history cleanup, spendable UTXOs), so a label that changed with the GUI language would no longer match the transactions saved before. Only `EVENT_SUMMARY` and `EVENT_DESCRIPTION` follow the language (`BalConfig(..., translatable=True)`). The same rule applies to every stored or compared text (wallet labels, `heirs.TRANSACTION_LABEL`, the status history).
- **2.10** `pyproject.toml`: add `"INT"` to `[tool.ruff.lint] select`.
  - Ruff's hint suggests `%` formatting: **do not follow it**. Use `.format()`, which is Electrum's rule.
  - `make-release.sh` treats Ruff errors as blocking, so INT must end up clean.
- **Verify:**
  - Ruff INT is clean;
  - the extraction count is about 360 (compare with 4.3);
  - the tests pass, plus new tests for 2.8 and 2.9;
  - manually in English: no visible text changed.

### Phase 3 — Italian catalog (visible change)

- **3.0** Merge `origin/main` first.
- **3.1** Add `babel.cfg` and the commands from 4.3, documented in `README.md` or `AGENTS.md`.
- **3.2** Generate `bal/locale/bal.pot` and create `it_IT/LC_MESSAGES/bal.po`.
- **3.3** Draft the translation using the glossary (D1); the owner reviews it (D2). Where Electrum's `it_IT` catalog already translates the exact same English text, copy Electrum's translation, so the two UIs match.
- **3.4** `build_zip.py`:
  - compile `.po` into `.mo`, according to D4;
  - put only the `.mo` files in the ZIP (no `.po` / `.pot`);
  - run the check script (4.4) before zipping.
- **Verify:**
  - Electrum in Italian shows BAL in Italian;
  - Electrum in English shows BAL in English;
  - missing entries fall back to English;
  - `external_zip_test.py` passes;
  - it works on Windows 11 with Electrum 4.8.0 (and 4.7.2);
  - manual walkthrough of: wizard, settings, heirs list, will list and detail, will-executors, check / sign / broadcast, invalidate, export / import, QR transfer, calendar export.

### Phase 4 — Final test and merge

- The owner and Truman test the final branch ZIP.
- Merge `origin/main` into the branch one last time and test again.
- Open the Pull Request `feature/i18n` → `main` on Gitea. After the merge, follow the normal release flow from `main`.

## 6. Decisions

Recorded on 2026-09-26 from the owner's answers.

- **D1 — Italian glossary.** Rule: follow Electrum's `it_IT` wording wherever Electrum already has the term or the sentence. Grammatical variants (nouns, past participles, plurals, capitals in headers and buttons) are adapted in the draft and checked by the owner during the review (D2).

  | English | Italian |
  |---|---|
  | will | testamento |
  | heir / heirs | erede / eredi |
  | will-executor | Will-Executor (kept in English) |
  | check-alive | verifica se sei vivo |
  | invalidate | invalida il piano |
  | invalidation (noun) | invalidazione del piano |
  | anticipate / postpone | anticipa / posticipa |
  | locktime | locktime (kept in English; owner, 2026-09-26) |
  | fee | commissione (as Electrum) |
  | broadcast | trasmetti il piano |
  | sign / signed | firma / firmato (as Electrum) |
  | wallet | portafogli (as Electrum) |

- **D2 — Who translates.** The developer drafts `it_IT/bal.po`; the owner reviews and corrects it (for example in Poedit). For later languages: Weblate (supports Gitea and `.po`) or Crowdin, as Electrum does.
- **D3 — Branch and commits.** The developer creates `feature/i18n`, and commits and pushes on it, each time only after the owner's explicit OK (ZIP-FIRST).
- **D4 — `.mo` files.** `build_zip.py` compiles `.po` into `.mo` at build time with Babel; `.mo` files stay out of git (add `*.mo` to `.gitignore`). If Babel is missing, the build stops with a clear error, so an English-only ZIP is never shipped by mistake. Reasons: the `.po` is the only source (no stale `.mo`), every change to a translation is readable in review, and it is how Electrum works.
- **D5 — Cycles.** Phases 1 and 2 are delivered in one ZIP/test cycle. Phase 1 is not merged into `main` early.
- **D6 — Branch name:** `feature/i18n`.
- **D7 — Lookup order.** Electrum's catalog first, then BAL's, then English (see 4.1). Proposed by the owner so that BAL and Electrum read as one, also if Electrum ever integrates BAL.

## 7. Reminders for the developer

- Talk to the owner in **Italian**, with simple words and no jargon (the owner is not a programmer). Code, comments, docstrings, UI source strings, docs and commits are in **English**.
- UI source strings stay in English; Italian lives only in the `.po` file.
- Never invent. When in doubt, stop and ask.
- Before writing any code, present the phase PLAN and wait for "OK".
- ZIP-FIRST; run ruff and the tests before every ZIP; one CHANGELOG entry per phase; keep reports brief (CREDIT-SAVING).
- Wallet data compatibility comes first: apart from 2.8 (status labels), do not change what is stored.

## Appendix — Inventory at `main` @ `4a33116`

Generated by a static scan (AST, Ruff `INT` and xgettext). The line numbers will move after merging a newer `main`: re-run the scan or search by content. These lists cover the known cases; Phase 2 still needs a full sweep.

#### A. f-string / `.format()` inside `_()` — Ruff INT001/INT002 (14)

| Location | Rule |
|---|---|
| `bal/cli/controller.py:92` | INT001 |
| `bal/cli/controller.py:98` | INT001 |
| `bal/cli/controller.py:104` | INT001 |
| `bal/gui/qt/common.py:225` | INT002 |
| `bal/gui/qt/common.py:240` | INT002 |
| `bal/gui/qt/dialogs.py:1226` | INT001 |
| `bal/gui/qt/dialogs.py:2491` | INT001 |
| `bal/gui/qt/window.py:831` | INT001 |
| `bal/gui/qt/window.py:836` | INT001 |
| `bal/gui/qt/window.py:1090` | INT001 |
| `bal/gui/qt/window.py:1091` | INT001 |
| `bal/gui/qt/window.py:2228` | INT001 |
| `bal/gui/qt/window.py:2236` | INT001 |
| `bal/gui/qt/window.py:2246` | INT001 |

#### B. `_()` with a non-literal argument (not extractable) (15)

| Location | Code |
|---|---|
| `bal/core/will.py:1344` | `_(self.STATUS[status][0])` |
| `bal/gui/qt/common.py:181` | `_(label)` |
| `bal/gui/qt/dialogs.py:327` | `_(self.message)` |
| `bal/gui/qt/dialogs.py:2288` | `_(msg)` |
| `bal/gui/qt/dialogs.py:2291` | `_(msg)` |
| `bal/gui/qt/lists.py:1240` | `_(label)` |
| `bal/gui/qt/plugin.py:1086` | `_(title)` |
| `bal/gui/qt/widgets.py:284` | `_(self.tooltip_text)` |
| `bal/gui/qt/widgets.py:1320` | `_(str(title))` |
| `bal/gui/qt/window.py:290` | `_(title)` |
| `bal/gui/qt/window.py:942` | `_(message)` |
| `bal/gui/qt/window.py:2217` | `_(self.DOWNLOAD_FAILED_TOR_MESSAGE)` |
| `bal/gui/qt/window.py:2219` | `_(self.DOWNLOAD_FAILED_MESSAGE)` |
| `bal/gui/qt/window.py:2253` | `_(self.DOWNLOAD_FAILED_TOR_MESSAGE)` |
| `bal/gui/qt/window.py:2255` | `_(self.DOWNLOAD_FAILED_MESSAGE)` |

#### C. Concatenations around `_()` (14)

| Location | Expression |
|---|---|
| `bal/core/will.py:1344` | `'.' + (('NOT ' if not value else '') + _(self.STATUS[status][0]))` |
| `bal/gui/qt/dialogs.py:1010` | `_('Wallet balance is too low: {} satoshi available, but the miner and…` |
| `bal/gui/qt/dialogs.py:2206` | `messages[reason] + '\n\n' + _('Skipped')` |
| `bal/gui/qt/dialogs.py:2209` | `_("Could not build the will, and the exact cause could not be determi…` |
| `bal/gui/qt/dialogs.py:2419` | `_('Expiration date: ') + str(BalTimestamp(self.threshold))` |
| `bal/gui/qt/dialogs.py:2425` | `_('Valid Txs:') + str(len(Will.only_valid_list(self.will)))` |
| `bal/gui/qt/dialogs.py:2427` | `_('Total Txs:') + str(len(self.will))` |
| `bal/gui/qt/lists.py:679` | `'  ' + _('Build Your Will')` |
| `bal/gui/qt/plugin.py:301` | `'Bal ' + _('Bitcoin After Life')` |
| `bal/gui/qt/plugin.py:1034` | `'<b>' + _('Support:') + '</b>'` |
| `bal/gui/qt/plugin.py:1086` | `_('BAL - ') + _(title)` |
| `bal/gui/qt/widgets.py:1320` | `'<b>' + _(str(title)) + f':</b>\t{str(value)}'` |
| `bal/gui/qt/window.py:1022` | `_('Electrum was unable to deserialize the transaction:') + '\n' + str…` |
| `bal/gui/qt/window.py:1091` | `msg + _(f'signing: {tosign}')` |

#### D. `_()` evaluated at import time (class body) (18)

| Location | Scope | Call |
|---|---|---|
| `bal/gui/qt/dialogs.py:419` | class body | `_('Bitcoin After Life Will-Executors')` |
| `bal/gui/qt/dialogs.py:420` | class body | `_('Choose willexecutors download method')` |
| `bal/gui/qt/dialogs.py:528` | class body | `_('Configure and select your willexecutors')` |
| `bal/gui/qt/dialogs.py:548` | class body | `_('')` |
| `bal/gui/qt/lists.py:131` | class body | `_('Name')` |
| `bal/gui/qt/lists.py:132` | class body | `_('Address')` |
| `bal/gui/qt/lists.py:133` | class body | `_('Amount')` |
| `bal/gui/qt/lists.py:340` | class body | `_('Locktime')` |
| `bal/gui/qt/lists.py:341` | class body | `_('Txid')` |
| `bal/gui/qt/lists.py:342` | class body | `_('Will-Executor')` |
| `bal/gui/qt/lists.py:343` | class body | `_('Status')` |
| `bal/gui/qt/lists.py:344` | class body | `_('Server')` |
| `bal/gui/qt/lists.py:907` | class body | `_('')` |
| `bal/gui/qt/lists.py:908` | class body | `_('Url')` |
| `bal/gui/qt/lists.py:909` | class body | `_('S')` |
| `bal/gui/qt/lists.py:910` | class body | `_('Base fee')` |
| `bal/gui/qt/lists.py:911` | class body | `_('Info')` |
| `bal/gui/qt/lists.py:912` | class body | `_('Default Address')` |

#### E. `_("")` (2)

| Location | Call |
|---|---|
| `bal/gui/qt/dialogs.py:548` | `_('')` |
| `bal/gui/qt/lists.py:907` | `_('')` |

#### F. Settings help texts never passed to `_()` (12)

| Location | Widget | Text |
|---|---|---|
| `bal/gui/qt/plugin.py:715` | HelpButton | When checking, automatically sign and broadcast the will transactions… |
| `bal/gui/qt/plugin.py:778` | HelpButton | `How many reminder alarms the exported calendar (.ics) event contains.…` |
| `bal/gui/qt/plugin.py:789` | HelpButton | `Default message to be used in event summary\nVariables:\n  $wallet_na…` |
| `bal/gui/qt/plugin.py:803` | HelpButton | `Default message to be used in event description\nVariables:\n  $walle…` |
| `bal/gui/qt/plugin.py:818` | HelpButton | URL of the server that provides the will-executor list.  Only availab… |
| `bal/gui/qt/plugin.py:829` | HelpButton | `Command used to open .ics calendar files.\nLeave empty to use the sys…` |
| `bal/gui/qt/plugin.py:844` | HelpButton | `After each check, save the valid will transactions into the wallet's …` |
| `bal/gui/qt/plugin.py:858` | HelpButton | `Label applied to the will transactions saved into the wallet's local …` |
| `bal/gui/qt/plugin.py:880` | HelpButton | Broadcast all transactions to willexecutors including those already p… |
| `bal/gui/qt/plugin.py:891` | HelpButton | `Run the 'Build your will' wizard every time the wallet is closed or E…` |
| `bal/gui/qt/plugin.py:909` | HelpButton | When a new transaction arrives for the wallet, automatically rebuild … |
| `bal/gui/qt/plugin.py:933` | HelpButton | `Payload size of a single QR code when exporting a will via QR.\n\nLar…` |

#### G. `add_widget()` calls (label translated at runtime, help text never) (5)

| Location | Label | Help text |
|---|---|---|
| `bal/gui/qt/plugin.py:697` | Hide Replaced | Hide replaced transactions from will detail and l… |
| `bal/gui/qt/plugin.py:705` | Hide Invalidated | Hide invalidated transactions from will detail an… |
| `bal/gui/qt/plugin.py:727` | Panel editable Date and Fee | When enabled, the delivery-time and check-alive d… |
| `bal/gui/qt/plugin.py:742` | `Max Will-Executor Fee (satoshi)` | `Maximum fee (in satoshi) allowed to be paid to a …` |
| `bal/gui/qt/plugin.py:757` | User Type | `Choose how much detail the plugin shows.\n\nBASIC…` |

#### H. Other user-visible text not (correctly) translatable — known cases, not exhaustive

| Location | Code | Note |
|---|---|---|
| `bal/gui/qt/dialogs.py:380` | `title = "Bitcoin After Life Heirs"` | Wizard title, shown untranslated at `dialogs.py:325` |
| `bal/gui/qt/dialogs.py:527` | `title = "Bitcoin After Life Will-Executors"` | Same |
| `bal/gui/qt/dialogs.py:547` | `title = "Bitcoin After Life Will Settings"` | Same |
| `bal/gui/qt/lists.py:1472` | `setText("New Will Executor")` | Not wrapped |
| `bal/gui/qt/plugin.py:673` | `QPushButton("Rebroadcast transactions")` | Not wrapped |
| `bal/gui/qt/widgets.py:1323` | `qlabel("Locktime", …)` | Translated inside `qlabel()` but not extractable |
| `bal/gui/qt/widgets.py:1324` | `qlabel("Creation Time", …)` | Same |
| `bal/gui/qt/widgets.py:1334` | `qlabel("Transaction fees:", …)` | Same; `qlabel()` adds another `:` |
| `bal/gui/qt/widgets.py:1336` | `qlabel("Status:", …)` | Same; also shows the stored status (see 2.8) |
| `bal/gui/qt/widgets.py:1339` | `QLabel("<b>Heirs:</b>")` | Not wrapped |
| `bal/gui/qt/widgets.py:1366` | `_("<b>Willexecutor:</b:")` | Malformed HTML inside the string |
| `bal/gui/qt/window.py:1613` | `msg = "Broadcasting Transactions to Will-Executors:\n"` | Not wrapped |

Not to translate: `widgets.py:122` (icon glyph), `plugin.py:1020` (link to bitcoin-after.life).

#### Numbers (`main` @ `4a33116`)

| Measure | Value |
|---|---|
| `_()` calls | 483 in 11 files |
| Distinct strings, whole package (xgettext) | 365 (≈ 2,230 words) |
| …of which Electrum `it_IT` already translates | 39 (≈ 11 %) |
| Strings used only by the CLI | 36 (≈ 315 words), out of scope |
| Babel extraction today, GUI + core | 323 (≈ 1,900 words) |
| Expected after Phase 2 | ≈ 360 (≈ 2,500 words) |

## Sources

- Electrum 4.8.0: `electrum/i18n.py`, `run_electrum` (L433–451), `electrum/gui/default_lang.py`, `electrum/plugin.py` (`maybe_load_plugin_init_method`, `read_file`), `contrib/locale/` — https://github.com/spesmilo/electrum/tree/4.8.0
- `electrum-locale` at the 4.8.0 submodule commit `3d7594c`: `update.py`, `llm_proofreader/` — https://github.com/spesmilo/electrum-locale
- Gitea "Update branch" button on pull requests — https://github.com/go-gitea/gitea/pull/9784
- Weblate: Gitea and gettext PO support — https://docs.weblate.org/en/latest/vcs.html
