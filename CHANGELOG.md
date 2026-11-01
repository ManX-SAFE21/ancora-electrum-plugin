# CHANGELOG

This file records the work done on the BAL (Bitcoin After Life) Electrum
inheritance plugin, one numbered entry per task.

Each entry lists: task title, date, files changed, and outcome
(DONE / UNRESOLVED). It is meant to make it easy to review what was done and,
if needed, to roll back to a previous state.

---

<!-- New entries are added below, newest last. -->

## 1. A1 - Remove block-height locktimes (timestamps only)

**Date:** 2026-06-22

**Goal (OPUS plan, Group A / A1):** Remove block-height locktimes from the
codebase so that ALL ordering and comparison use UNIX timestamps only. The
`NLOCKTIME_BLOCKHEIGHT_MAX` guard is intentionally KEPT, because it forces every
locktime to be a timestamp (it is a safety "bouncer", not block-height ordering).

**What changed:**

- `bal/core/util.py`
  - `str_to_locktime`: removed the `"b"` (block) suffix; only `"d"` (days) and
    `"y"` (years) relative suffixes are accepted now.
  - `parse_locktime_string`: removed the block-height (`"<n>b"`) branch. The
    `w` (wallet) argument is kept only for call-site compatibility (now unused).
  - `int_locktime`: removed the `blocks` argument (and the `blocks * 600`
    seconds-per-block conversion). New signature:
    `int_locktime(seconds=0, minutes=0, hours=0, days=0)`.
  - `chk_locktime`: signature changed from
    `(timestamp_to_check, block_height_to_check, locktime)` to
    `(timestamp_to_check, locktime)`; comparison is now purely timestamp-based.
  - `anticipate_locktime`: removed the `blocks` argument and the block-height
    branch; it now only moves a timestamp earlier (by hours/days). The Windows
    overflow clamp and the `out < 1` clamp are kept.
  - Expanded the `LOCKTIME_THRESHOLD` comment to explain the timestamp-only model.

- `bal/core/will.py`
  - Removed the `from electrum.bitcoin import NLOCKTIME_BLOCKHEIGHT_MAX` import
    (it was only used by the dead block-height branch).
  - `check_will`: removed the `block_to_check` parameter.
  - `is_will_valid`: removed the `block_to_check` parameter.
  - `check_will_expired`: removed the `block_to_check` parameter and the
    `if locktime <= NLOCKTIME_BLOCKHEIGHT_MAX:` block-height branch; expiry is
    now decided purely by comparing the locktime against `timestamp_to_check`.
  - Added/expanded docstrings on the three methods above.
  - KEPT `utxo.block_height` in the coinbase-maturity check (line ~399): that is
    a legitimate Electrum UTXO attribute, NOT a BAL locktime concept.

- `bal/gui/qt/window.py`
  - `check_will`: stopped passing the removed `block_to_check` argument.
  - `init_class_variables`: removed the dead `locktime_blocks`, `current_block`
    and `block_to_check = 0` assignments (replaced by an explanatory comment).

- `bal/gui/qt/widgets.py`
  - `LockTimeRawEdit`: removed the `"b"` (block) suffix handling from
    `replace_str`, `numbify` and the `isblocks` flag; only `"d"` and `"y"` remain.
  - Added a clarifying comment on the kept guard
    `LockTimeDateEdit.min_allowed_value = NLOCKTIME_BLOCKHEIGHT_MAX + 1`.

- Tests updated for the new signatures / behaviour:
  - `tests/test_core_util.py`: `test_str_to_locktime` (now expects `"144b"` to be
    rejected), `test_int_locktime` (no `blocks`), `test_chk_locktime` (2-arg),
    `test_anticipate_locktime` (no `blocks`). Added `import pytest`.
  - `tests/test_core_will_extra.py`: `test_check_will` now calls the 4-arg
    `check_will`.
  - `tests/test_anticipate_past_locktime.py`: `chk_locktime` call updated to 2-arg.
  - `tests/test_gui_widgets.py`: `test_locktime_raw_edit_replace_str` now expects
    `"b"` to be left untouched.

**Verification:**
- `ruff check` on all modified production files: no new errors introduced
  (`bal/core/util.py` is clean; pre-existing baseline warnings unchanged).
- Full test suite: `190 passed`
  (`tests/test_core_*.py tests/test_gui_*.py tests/test_anticipate_past_locktime.py`).

**Dormant block-based config kept on purpose (owner decision: keep, comment why):**
- `bal/core/plugin_base.py` still defines two block-based stored configs that
  are no longer read anywhere after A1:
  - `LOCKTIME_BLOCKS` (`"bal_locktime_blocks"`)
  - `LOCKTIMEDELTA_BLOCKS` (`"bal_locktimedelta_blocks"`)
  Per the owner's decision they are KEPT in place (dormant) to avoid touching
  persisted config keys that may already exist in some users' saved settings.
  An explanatory comment was added above each line stating they are unused now
  and why they are intentionally retained.

**Outcome:** DONE.

---

## 2. A2 - Status definitions, colours and VALID rules (MEMPOOL rename + UPDATED)

**Date:** 2026-06-22

**Goal (OPUS plan, Group A / A2):** Define the inheritance statuses, their
colours and their VALID rules. The fine moment-by-moment assignment of
ANTICIPATED / UPDATED will be validated by the Group E tests; A2 sets up the
state machine and colours.

**Status meanings (for reference):**
- **ANTICIPATED** - locktime anticipated by 1 day vs a pre-existing tx with the
  same heirs; the tx STAYS VALID.
- **REPLACED** - an input is spent by a new tx with a LOWER locktime; loses
  VALID, cascades to children.
- **INVALIDATED** - an input is spent by a mempool/confirmed tx and the previous
  tx is no longer in the will; loses VALID.
- **UPDATED** (new) - the tx was spendable AND valid, and a new tx replaces it
  keeping the SAME locktime and SAME heirs; STAYS VALID.
- **MEMPOOL** (renamed from PENDING) - the tx has been seen in the Electrum
  mempool; loses VALID.
- **CONFIRMED** - the tx is confirmed in the blockchain; loses VALID.

**What changed:**

- `bal/core/will.py`
  - Renamed the status key `PENDING` -> `MEMPOOL` everywhere
    (`STATUS_DEFAULT`, `set_status`, and the three `get_status(...)` reads in
    `check_invalidated` / `search_rai`). Visible label: "Mempool".
  - Added the new status `UPDATED` to `STATUS_DEFAULT` (label "Updated").
  - `set_status`: VALID rules now documented and updated:
    - `INVALIDATED`, `REPLACED`, `CONFIRMED`, `MEMPOOL` -> clear VALID.
    - `ANTICIPATED` and `UPDATED` -> KEEP VALID (intentionally NOT in the
      clear-VALID list).
    - `CONFIRMED`, `MEMPOOL` -> clear INVALIDATED (unchanged behaviour).
  - Added a full docstring to `set_status` explaining all side effects.
  - **Backward-compatibility migration (owner decision "Modo B"):** in
    `__init__`, a will saved by an older plugin version that stores the legacy
    `PENDING` flag is migrated to `MEMPOOL`, so no state is lost on load. The new
    `MEMPOOL` key wins if both are present.

- `bal/gui/qt/theme.py`
  - Renamed the `PENDING` colour entry to `MEMPOOL` (#ffce30 yellow, unchanged).
  - Added `UPDATED` -> #800080 (violet) in the priority list, placed after
    REPLACED and before CONFIRMED/MEMPOOL.

- Tests:
  - `tests/test_gui_theme.py`: renamed the pending colour test to
    `test_color_mempool`, updated the "overrides lower" test, and added
    `test_color_updated` (#800080).
  - `tests/test_core_will_extra.py`: renamed `test_check_invalidated_pending`
    -> `test_check_invalidated_mempool`; updated `test_check_will`. Added new
    tests: `test_legacy_pending_migrates_to_mempool`,
    `test_new_mempool_wins_over_legacy_pending`, `test_updated_status_keeps_valid`,
    `test_anticipated_status_keeps_valid`, `test_mempool_status_clears_valid`.

**Verification:**
- `ruff check` on modified production files: no new errors introduced.
- Full test suite: `196 passed`
  (`tests/test_core_*.py tests/test_gui_*.py tests/test_anticipate_past_locktime.py`).

**Note:** the exact moment ANTICIPATED / UPDATED get assigned during real flows
is validated by the Group E tests (per the owner's decision).

**Outcome:** DONE.

---

## 3. A3 - "Move date earlier (anticipate)" must not invalidate

**Date:** 2026-06-22

**Goal (OPUS plan, Group A / A3):** (a) explain the inheritance-options table;
(b) make "Move date earlier (anticipate)" only anticipate (rebuild), NOT
invalidate; (c) regenerate a clearer English table.

**Owner decisions captured during DISCOVER:**
- D1 = the case to handle is the user MANUALLY setting a smaller locktime in the
  wizard (e.g. from "90 days" to "30 days").
- D2 = case A1 (smaller locktime, still in the future): plain REBUILD with the
  new locktime, NEVER invalidate, even if the tx was already signed/sent.
- D3 = the genuine-expiry case (new date in the past) keeps invalidating; only
  the documentation is wrong and must be fixed.
- Chosen path = X (fix BOTH code clarity and documentation).

**Analysis result (verified with tests, not assumed):**
The core logic was ALREADY correct for D2. A diagnostic confirmed:
- Case A1 (smaller, future locktime, signed OR unsigned) ->
  `HeirNotFoundException` (a rebuild signal), NEVER `WillExpiredException`. So no
  on-chain invalidation happens. This already matches the owner's decision.
- Case A2 (locktime in the past) -> `WillExpiredException` -> invalidation. This
  is a genuine expiry and is intentionally kept.

So the real defect was in the DOCUMENTATION (the table wrongly claimed
"anticipate -> always invalidate"). No behavioural code change was needed.

**What changed:**

- `bal/core/will.py`
  - Added a clarifying comment in `check_willexecutors_and_heirs` explaining that
    anticipating to a FUTURE date is a plain rebuild that NEVER invalidates (even
    when signed/sent), while only a date in the PAST is a genuine expiry handled
    by `check_will_expired`. No logic change.

- `tests/test_anticipate_manual_locktime.py` (new)
  - Permanent regression tests guaranteeing:
    `test_a1_anticipate_unsigned_triggers_rebuild_not_invalidate`,
    `test_a1_anticipate_signed_triggers_rebuild_not_invalidate` (A1 = rebuild, no
    invalidate, signed or not), and
    `test_a2_past_locktime_is_genuinely_expired` (A2 = WillExpired kept).

- `docs/inheritance-options.md`
  - Split the "Move date EARLIER" row into two: "still in the future" (rebuild,
    no fee, signed or not) vs "into the past" (invalidate, WillExpired).
  - Rewrote the "why anticipate" notes (anticipate is safe, opposite of postpone).
  - Fixed the quick-reference summary table and the Golden Rules accordingly.
  - Updated the status section to match A2 (MEMPOOL instead of PENDING; added
    ANTICIPATED/UPDATED keep-VALID rows) and rewrote the colour table in the exact
    priority order used by `gui/qt/theme.py` (incl. UPDATED #800080 violet).
  - Updated the Mermaid decision flow to the corrected branching.

- `docs/inheritance-options.html`
  - Mirrored all the above .md changes (status table, colour table with new pill
    colours, section 4.1 table + notes, summary table, Golden Rules, Mermaid).
  - Bumped the document version footer to v0.3.4.

- `docs/images/inheritance-flow.svg`
  - Reworked the "anticipate" branch: the decision is now "new date in the PAST?"
    -> invalidate (WillExpired); otherwise "moved earlier? (anticipate)" ->
    rebuild only, NEVER invalidates. Bumped subtitle to v0.3.4. Verified the SVG
    is still well-formed XML.

**Verification:**
- `ruff check` on changed Python files: no new errors introduced.
- Full test suite: `199 passed`
  (`tests/test_core_*.py tests/test_gui_*.py tests/test_anticipate_past_locktime.py
  tests/test_anticipate_manual_locktime.py`).

**Outcome:** DONE.

---

## 4. A2 follow-up - UPDATED status colour lightened

**Date:** 2026-06-22

**Goal:** After testing the v0.3.4 build, the original UPDATED colour
(`#800080`, dark violet) was reported as too dark to read in the list.
Lighten it to a more readable light violet while keeping it distinct from
MEMPOOL (yellow `#ffce30`).

**What changed:**

- `bal/gui/qt/theme.py`
  - UPDATED colour changed from `#800080` (dark violet) to `#b266b2`
    (light violet) in `_STATUS_COLOR_PRIORITY`. Priority position unchanged
    (after REPLACED, before CONFIRMED/MEMPOOL).

- `docs/inheritance-options.md` / `docs/inheritance-options.html`
  - Updated the colour table and the `.pill.violet` CSS to `#b266b2`
    ("light violet").

- `tests/test_gui_theme.py`
  - `test_color_updated` now asserts `#b266b2`.

**Verification:**
- Full test suite: see run below.

**Outcome:** DONE.

---

## 5. B1 + B2 - Guided wizard verification and Auto-sign on Check

**Date:** 2026-06-22

**Goal (OPUS plan, Group B):**
- **B1:** the "Create your will" button should open the step-by-step guided
  wizard.
- **B2:** the "Check" action should be able to automatically sign and broadcast
  the will, controlled by an "Auto-sign" checkbox in the settings dialog,
  default ON.

**B1 - finding (no code change needed):**
- The "Create your will" toolbar button is already wired to
  `BalWindow.init_wizard`, which opens `BalWizardDialog` - the step-by-step
  wizard (Heirs -> Locktime & Fee -> Will-Executor download -> Will-Executor ->
  build will). This already matches the requested behaviour, so no code change
  was required for B1.

**B2 - what changed:**

- `bal/core/plugin_base.py`
  - Added a new persisted setting `AUTO_SIGN`
    (`BalConfig(config, "bal_auto_sign", True)`), default ON, with an
    explanatory comment.

- `bal/gui/qt/plugin.py` (`settings_dialog`)
  - Added an "Auto-sign on Check" checkbox bound to `AUTO_SIGN`, with a tooltip
    explaining that the wallet password is requested only if the wallet is
    encrypted. Re-numbered the grid rows so the new checkbox (row 3) does not
    overlap the existing widgets; also fixed the "Event sescription" ->
    "Event description" label typo.

- `bal/gui/qt/window.py`
  - Added `auto_sign_and_broadcast()`: signs the will and, only after signing
    succeeds, broadcasts it to the will-executors. It reuses
    `ask_password_and_sign_transactions(callback=...)`; the existing
    `get_wallet_password()` already prompts only for encrypted wallets, so a
    password-less wallet is handled with no prompt.

- `bal/gui/qt/lists.py` (`check`)
  - After the server check, when `AUTO_SIGN` is enabled, calls
    `auto_sign_and_broadcast()`. When the setting is OFF the behaviour is
    unchanged (check only).

- `tests/test_group_b_auto_sign.py` (new)
  - 5 tests: AUTO_SIGN defaults ON and can be disabled; sign happens before
    broadcast; encrypted wallet prompts for the password; un-encrypted wallet
    signs and broadcasts with no prompt.

**Verification:**
- `ruff check` on changed Python files: no new errors introduced
  (new test file is ruff-clean).
- Full test suite: `204 passed`.

**Outcome:** DONE.

---

## 6. B2 follow-up - Remove duplicate broadcast and make broadcast one-shot

**Date:** 2026-06-22

**Problems reported after testing the v0.3.4 Group B build:**
1. The "Building Will" dialog still showed "Next step (manual): press
   'Broadcast'..." even though the will had already been broadcast.
2. A second, duplicate popup ("Informazioni") repeated the same manual hint.
3. When several transactions were broadcast to different will-executors and one
   server failed, the plugin tried to retry the broadcast, which could loop
   forever against a server that never answers.

**Root cause:**
- `lists.py check()` first runs `BalBuildWillDialog.build_will_task()` (which
  already checks, signs and broadcasts), and then a second
  `auto_sign_and_broadcast()` was triggered - a duplicate sign/broadcast cycle.
- The "Building Will" dialog always printed the manual "press Sign/Broadcast"
  hint and a follow-up popup, which is wrong when broadcasting is automatic.
- `loop_push()` used a `retry` flag and raised `Exception("retry")` whenever any
  will-executor failed.

**What changed (Fix A - no duplicate broadcast / no wrong manual hint):**

- `bal/gui/qt/lists.py`
  - `check()` no longer calls `auto_sign_and_broadcast()`. Signing and
    broadcasting are already done by `build_will_task()`; the duplicate cycle
    was removed (replaced by an explanatory comment).

- `bal/gui/qt/window.py`
  - Removed the now-unused `auto_sign_and_broadcast()` method.

- `bal/gui/qt/dialogs.py`
  - `_show_next_steps_hint()` returns early (no in-dialog hint, no popup) when
    `AUTO_SIGN` is ON, because the will has already been signed and broadcast.
    When `AUTO_SIGN` is OFF the previous manual hints are kept.

**What changed (Fix B - one-shot broadcast, no endless retry):**

- `bal/gui/qt/dialogs.py` (`loop_push`)
  - Removed the `retry` flag, the `retry_flag["value"] = True` assignments and
    the final `if retry: raise Exception("retry")`. The broadcast now contacts
    each selected will-executor ONCE: successful transactions become PUSHED,
    failed/timed-out ones are left as PUSH_FAIL and simply skipped (no automatic
    retry). The user can broadcast a failed transaction manually later.
  - Note: `get_willexecutor_transactions` already excludes PUSHED transactions,
    so the successful ones are never re-sent on a later run.

- `tests/test_group_b_auto_sign.py`
  - Rewritten for the new behaviour: AUTO_SIGN default/disable; manual hint
    suppressed when AUTO_SIGN ON and shown when OFF; PUSHED transactions are not
    re-collected for broadcast (one-shot), while not-yet-PUSHED ones are.

**Verification:**
- `ruff check` on changed Python files: no new errors introduced
  (new test file is ruff-clean).
- Full test suite: `206 passed`.

**Outcome:** DONE.

## 7. Group C - Settings-dialog improvements (editable dates, narrower RAW box, warning/reset/support, tooltips, bold locktime)

**Context / request:**
Group C bundles several usability improvements to the BAL settings dialog and
the will views, implemented together and delivered in a single ZIP.

**What changed (C2 - "Editable dates" option):**

- `bal/core/plugin_base.py`
  - New persisted config `EDITABLE_DATES = BalConfig(config, "bal_editable_dates",
    False)` (default OFF).

- `bal/gui/qt/widgets.py`
  - `WillSettingsWidget.__init__` reads `EDITABLE_DATES`. When OFF (default) the
    delivery-time and check-alive date fields stay display-only outside the
    "Build your will" wizard; when ON they become editable in the toolbar /
    Heirs tab. The fee field remains read-only outside the wizard.

- `bal/gui/qt/plugin.py`
  - New "Editable dates" checkbox in the settings dialog, bound to
    `EDITABLE_DATES`, with an explanatory tooltip. Toggling it refreshes the
    open windows so the date fields immediately reflect the new state.

**What changed (C3 - narrower RAW date box):**

- `bal/gui/qt/widgets.py`
  - `LockTimeRawEdit` width reduced from `12 * char_width_in_lineedit()` to
    `6 *` (roughly a third), enough for short relative values such as `30d` /
    `1y` while still fitting larger day counts.
  - `TimeRawEditWidget`'s trailing (empty) label shrunk from `10 *` to `2 *`
    character widths, removing the wasted blank space.

**What changed (C4 - warning, reset, support link):**

- `bal/gui/qt/plugin.py`
  - (a) Warning shown in bold red at the TOP of the dialog:
    "Warning: change these settings only if you know what you are doing."
  - (b) "Reset" button that restores the six dialog settings (Hide Replaced,
    Hide Invalidated, Auto-sign, Calendar App, Event summary, Event description)
    to their factory defaults, taking each default from `BalConfig.default`
    (single source of truth) and refreshing the widgets. It does NOT touch
    wills, will-executors or any other configuration.
  - (c) Clickable support link to `https://bitcoin-after.life`, opened via
    Electrum's `webopen` helper.
  - The dialog now uses an outer vertical layout (warning -> settings grid ->
    Reset/support row); the settings grid is unchanged apart from the new row.

- `bal/gui/qt/common.py`
  - Imported `webopen` from `electrum.gui.qt.util` (re-exported for the dialog).

**What changed (C5 - clearer tooltips):**

- `bal/gui/qt/widgets.py`
  - Calendar button tooltip: "Export reminder dates to your calendar (.ics)".
  - Fee field tooltip: "Mining fee rate in sat/vByte used for the will
    transactions".

**What changed (C6 - bold Locktime column):**

- `bal/gui/qt/lists.py`
  - In `PreviewList.replace()` the Locktime column item is now rendered in bold
    (via its own `QFont`), so the delivery time stands out in the list. The rest
    of the list is intentionally left unchanged.

- `tests/test_group_c_settings.py` (new)
  - Verifies `EDITABLE_DATES` defaults OFF and can be toggled/persisted, and
    that the Reset logic restores all six dialog settings to their defaults
    without touching unrelated configuration.

**Verification:**
- `ruff check` on changed Python files: no new errors introduced (the new test
  file is ruff-clean; the only added import flagged by ruff is the re-export
  `webopen`, matching the existing star-import pattern in `common.py`).
- Full test suite: `210 passed` (206 previous + 4 new Group C tests).

**Outcome:** DONE (delivered as a ZIP for user testing before commit).

### Group C - follow-up fixes (after first user test)

- **C2 (Editable dates) now takes effect immediately and is reset.**
  - `bal/gui/qt/widgets.py`: extracted the date-locking logic into
    `WillSettingsWidget.apply_editable_dates()`, which re-reads `EDITABLE_DATES`
    and locks/unlocks the delivery-time and check-alive fields (fee stays
    read-only). Called from `__init__` and re-callable afterwards.
  - `bal/gui/qt/window.py`: `update_all()` now calls `apply_editable_dates()` on
    the Heirs-tab and Will-tab settings widgets. Since the "Editable dates"
    checkbox already triggers `update_all()`, toggling it now updates the date
    fields instantly (same mechanism as the "Hide Invalidated" filter).
  - `bal/gui/qt/plugin.py`: added `EDITABLE_DATES` (and its checkbox) to the
    "Reset setting" list, so Reset also returns it to its default (OFF).

- **C3: fixed the broken date next to the RAW box.**
  - `bal/gui/qt/widgets.py`: the trailing label in `TimeRawEditWidget` shows the
    ABSOLUTE date computed from the RAW value (e.g. "30d" -> "2027-06-23").
    Its width was wrongly shrunk to 2 characters, truncating that date; it is
    restored to 10 characters. Only the RAW input box stays narrowed (6 chars).

- **C4b:** the reset button label is now "Reset setting".
- **C4c:** the support link text `bitcoin-after.life` is now shown in bold.

- `tests/test_group_c_settings.py`: the reset test now also covers
  `EDITABLE_DATES` (seven settings restored to default, flag back OFF).

**Verification (follow-up):** `ruff` clean on changed files; full suite
`210 passed`.

### Group C - second follow-up (after second user test)

- **Fee icon tooltip (C5):** the small "丰" help icon next to the fee field now
  shows the hover tooltip "Miner fee, click for more information" (the longer
  explanation still appears on click via the HelpButton).
- **C4c:** the word "Support:" before the link is now also shown in bold (not
  only the link text).
- **"Editable inheritance" Raw/Date default:** confirmed the existing behaviour
  is "remember the user's last choice" (the Raw/Date combo selection is
  persisted in `WILL_SETTINGS` whenever it changes), so no code change was
  needed - the dialog reopens on whichever mode the user last used.

**Verification (second follow-up):** `ruff` clean on changed files; full suite
`210 passed`.

### Group C - third follow-up (tooltip font fix)

- **Fee icon tooltip font:** the "丰" button used an unscoped
  `font-size: 16px` stylesheet that also enlarged its tooltip, making it bigger
  than the other tooltips (e.g. the calendar one). The rule is now scoped to
  `QPushButton{...}`, so only the glyph stays large while the tooltip uses the
  default font size like the others.

**Verification (third follow-up):** full suite `210 passed`.

## 8. Group D / D1 - Configurable, distributed calendar reminders and save-only .ics

**Context / request:**
The exported calendar (.ics) used to add one reminder (VALARM) for every single
day of the check-alive period (potentially hundreds), and tried to open the file
with a calendar app. Group D D1 makes the number of reminders configurable
(default 3, max 5), spreads them across the period before the deadline, and
changes the calendar button to simply SAVE the .ics file (asking the user where,
starting on the Desktop) instead of opening it. (D2 was intentionally skipped.)

**What changed (D1 - number of reminders):**

- `bal/core/plugin_base.py`
  - New persisted config `NUM_REMINDERS = BalConfig(config, "bal_num_reminders",
    3)` (default 3).

- `bal/gui/qt/widgets.py`
  - New `BalSpinBox` widget: an integer spin box bound to a `BalConfig`
    (mirrors `BalCheckBox` / `BalLineEdit`), with a clamped range.
  - New pure helper `compute_reminder_offsets(days, count)`: returns the
    reminder offsets (in days before the deadline) spread uniformly across the
    period. Every offset is >= 1 (reminders always fall before the deadline),
    at most one reminder per available day (`min(count, days)`), de-duplicated,
    sorted earliest-first. Examples: `(30, 3) -> [30, 16, 1]`,
    `(2, 3) -> [2, 1]`, `(1, 3) -> [1]`, `(0, 3) -> []`.
  - `create_alarms()` rewritten to read `NUM_REMINDERS`, use
    `compute_reminder_offsets`, and emit one VALARM per offset (with a
    DESCRIPTION reminder text, previously commented out).

- `bal/gui/qt/plugin.py`
  - New "Number of reminders" spin box in the settings dialog (range 1..5,
    default 3), with an explanatory tooltip, also included in the "Reset
    setting" list (resets back to 3).

**What changed (D1b - save-only .ics, ask where, default to Desktop):**

- `bal/gui/qt/calendar.py`
  - New `BalCalendar.desktop_dir()` helper returning the user's Desktop
    (`~/Desktop` when present, otherwise the home directory).

- `bal/gui/qt/widgets.py`
  - `open_or_save_calendar()` no longer tries to open the file with a calendar
    app. It always opens a "save as" dialog (via `getSaveFileName`) with an
    `.ics` filter, default filename `will_event.ics`, starting on the Desktop,
    then copies the generated file to the chosen path and shows a confirmation
    message. The unused `save_to_cwd` was replaced by `save_ics_to(target)`.
  - The "Calendar App" setting is left in place (now unused) as requested.

- `tests/test_group_d_alarms.py` (new)
  - Verifies `NUM_REMINDERS` default/change and all the distribution rules of
    `compute_reminder_offsets` (spread, before-deadline, one-per-day cap, empty
    when no room, never exceeding the requested count, single-reminder case).

**Verification:**
- `ruff check` on changed files: no new errors (new test file is ruff-clean).
- Full test suite: `217 passed` (210 previous + 7 new Group D tests).

**Outcome:** DONE (delivered as a ZIP for user testing before commit).

### Group D - follow-up ("Calendar App" removed from settings)

- `bal/gui/qt/plugin.py`: removed the "Calendar App" field from the settings
  dialog (and from the "Reset setting" list). Since the calendar button now only
  SAVES the .ics file (it no longer opens it with an external app), the setting
  was no longer needed. The `CALENDAR_APP` config and the `open_with_default_app`
  helper are left in the codebase (unused, harmless) to avoid touching unrelated
  code.
- The .ics "save as" behaviour is identical on Windows, Linux and macOS (always
  starts on the user's Desktop, with a home-directory fallback); no per-OS
  branching.

**Verification (follow-up):** full suite `217 passed`.

## 9. Group E - Mock tests with fake wallet "karen7"

**Goal:** add automated, GUI-free mock tests covering four behaviour areas of
the plugin, all driven by a single self-contained fake wallet named
"karen7" (no real Electrum wallet file is needed).

**What was added:**

- `tests/test_group_e_mock_karen7.py` (new) - 22 tests in four sections:
  - A fake wallet model (`Karen7Wallet`, `FakeDB`) whose `str(wallet)` is
    `"karen7"`, pre-loaded with two heirs (alice, bob).
  - **E1 - calendar / .ics:** reminder-offset distribution rules
    (`compute_reminder_offsets`), VALARM/TRIGGER shape
    (`TRIGGER;RELATED=END:-P{n}D`), iCalendar escaping of the event text, and
    writing a temporary `.ics` file (`BalCalendar.write_temp_ics`).
  - **E2 - inheritance / states:** loading/adding/removing karen7's heirs
    (`Heirs`), and `WillItem` status transitions (VALID -> COMPLETE,
    INVALIDATED clears VALID, PUSHED clears PUSH_FAIL), `Will.only_valid`, and
    heir-change detection (`HeirNotFoundException`).
  - **E3 - connectivity:** stubbing `Willexecutors.get_info_task` to prove that
    `ping_servers_parallel` contacts karen7's servers concurrently (total
    time far below the sequential sum), fires the per-server `on_each` callback
    once with the correct ok flag, and writes results back into the mapping;
    plus the empty-mapping no-op.
  - **E4 - will-executor:** `is_selected` default/set behaviour, the
    `get_willexecutor_transactions` filtering rule (only VALID + COMPLETE +
    not-PUSHED + selected wills are pushed; `force=True` re-includes PUSHED
    ones), and `compute_id`.

**Verification:**
- `ruff check` on the new test file: clean (all checks passed).
- Full test suite: `239 passed` (217 previous + 22 new Group E tests).

**Outcome:** DONE (delivered as a ZIP for user testing before commit).

## 10. Fix - black bar when cancelling the "invalidate old will" signature

**Bug:** when the user postpones a will's delivery date and the plugin asks to
invalidate the previous (earlier-locktime) will first, cancelling the signature
prompt left a black/undrawn rectangle at the bottom of the "Building Will"
dialog.

**Root cause:** in `bal/gui/qt/dialogs.py`, `on_success_phase1` handled the
cancelled-invalidation case with `self.wait(3)` followed by `self.close()`.
`wait()` uses `time.sleep()`, but `on_success_phase1` runs in the GUI thread, so
the sleep froze the interface for several seconds. While frozen, the dialog
could not repaint the area it had just resized, leaving an undrawn (black)
region at the bottom of the window.

**Fix (Variant A):**
- `bal/gui/qt/dialogs.py`
  - In `on_success_phase1`, the cancelled-invalidation branch no longer calls
    the blocking `self.wait(3)` + `self.close()`. It now marks the step as
    "Aborted" and shows the existing non-blocking "Close" button
    (`_add_close_button()`), so the GUI is never blocked and the bottom of the
    dialog repaints correctly. The user reads the outcome and dismisses the
    dialog when ready, consistent with the other end-of-flow branches.

**Verification:**
- `py_compile` on the changed file: OK.
- `ruff check`: no new errors (only the pre-existing F401/F403/F405 star-import
  noise and an unrelated F841 at line 615).
- Full test suite: `239 passed`.

**Outcome:** DONE (delivered as a ZIP for user testing before commit).

## 11. Fee field now follows the "Editable dates" setting

**Request:** the "Editable dates" checkbox in the plugin settings (added in
Group C / C2) lets the user edit the delivery-time and check-alive dates
outside the wizard. The mining-fee field next to them, however, stayed always
read-only. The user asked for the fee to follow the same rule as the dates.

**What changed:**
- `bal/gui/qt/widgets.py`
  - In `WillSettingsWidget.apply_editable_dates()`, the fee widget
    (`baltx_fees`) is now locked/unlocked with `set_read_only(not
    editable_dates)`, exactly like the locktime and threshold widgets, instead
    of being forced to `set_read_only(True)`. The method docstring was updated
    to state that the fee follows the same "Editable dates" rule.

**Effect:** with "Editable dates" ticked, the delivery time, check-alive date
AND the fee become editable outside the wizard; with it unticked, all three go
back to read-only. Inside the wizard everything stays editable as before. The
change takes effect immediately (the method is already re-run from
`BalWindow.update_all()`), with no need to reopen the window.

**Verification:**
- `py_compile` on the changed file: OK.
- `ruff check`: no new errors (only pre-existing star-import noise and unrelated
  F841 warnings at lines 547 / 792).
- Full test suite: `239 passed`.

**Outcome:** DONE (delivered together with fix #10 in a single ZIP for user
testing before commit).

## 12. Translated the refactoring docs to English and renamed two of them

**Request:** per rule R1 (all output, including documentation, must be in
English), translate the remaining Italian documentation files. The user also
asked to translate the file *names* of two of them.

**What changed:**
- `CHANGELOG_REFACTOR.md`
  - Translated sections 1-16 (the whole report header plus §1-§16) from Italian
    to English. Sections 17-18 were already in English and were left untouched.
  - All technical content was preserved verbatim: code blocks, commit hashes,
    tables, file/class/method names, version numbers and structure.
  - Updated the §12 history line to mention the new name of the diagnosis file
    (`GUI_DIAGNOSIS.md`, originally `DIAGNOSI_GUI.md`).
- `DIAGNOSI_GUI.md` → renamed to `GUI_DIAGNOSIS.md` (via `git mv`) and fully
  translated to English, **title included**. All code blocks, line references,
  severity emojis, tables and structure were preserved.
- `REPORT_NETWORKING_PARALLELO.md` → renamed to `PARALLEL_NETWORKING_REPORT.md`
  (via `git mv`). Its content was already in English, so only the file name was
  translated.

**Verification:**
- Scanned the translated regions for leftover Italian words: none found (only
  English false-positives from the regex).
- Confirmed sections 17-18 of `CHANGELOG_REFACTOR.md` are unchanged
  (the diff hunks stop before §17).
- Confirmed no other file in the repo still references the old file names.

**Outcome:** DONE (documentation-only change; no plugin code touched, so the
zip-first step does not apply).

## 13. Calendar (.ics): separate reminder events instead of one event with alarms

**Request:** the exported `.ics` added a single calendar entry (on the locktime)
with internal VALARM reminders, which most calendars show as just one
appointment. The user asked for **N separate events** (default 3), each with its
own visible date, with the **last one one day before the inheritance locktime**.

**What changed (option A):**
- `bal/gui/qt/widgets.py`
  - Rewrote `WillSettingsWidget.open_or_save_calendar()` to emit **one VEVENT
    per reminder**, each placed on `locktime - offset` days, instead of one
    VEVENT carrying VALARM blocks. The offsets come from the existing
    `compute_reminder_offsets()`, so they are spread uniformly across the
    check-alive period and the **last event is always one day before the
    locktime**.
  - Each event gets a **unique UID** (`bal-<wallet>-<offset>d`) so calendars do
    not merge them, and its summary is suffixed with **" (reminder N/total)"**
    to tell the events apart. The description still reuses `EVENT_DESCRIPTION`
    with the usual `$wallet_name` / `$heirs_complete` substitutions.
  - Removed the now-unused `create_alarms()` method (no more VALARM blocks).
  - Changed the default save filename from `will_event.ics` to
    **`BAL_will_event.ics`** (still defaulting to the Desktop).
- `bal/gui/qt/common.py`
  - Added `timedelta` to the `datetime` import (needed for the per-event date
    arithmetic; re-exported via the GUI star-import).
- `bal/core/plugin_base.py`
  - Updated the `NUM_REMINDERS` comment to describe the new "separate events"
    behaviour.
- `tests/test_group_e_mock_karen7.py`
  - Replaced the old VALARM-shape E1 test with
    `test_e1_build_separate_events_for_karen7`, which asserts the new
    structure: N distinct VEVENTs, no VALARM, unique UIDs, numbered summaries,
    and the last event one day before the locktime.

**Effect:** with the default of 3 reminders over, say, a 30-day period, the
`.ics` now produces 3 separate calendar appointments (e.g. 30, 16 and 1 day
before the deadline) instead of a single one. Short periods automatically
produce fewer events (at most one per day).

**Verification:**
- `py_compile` on the changed files: OK.
- `ruff check`: no new errors.
- Full test suite: see run below.

**Outcome:** DONE (delivered as a test ZIP v0.3.7 for the user to try before
commit).

## 14. Check-alive help text: DATA/RAW explanation and two typo fixes

**Request:** rewrite the text inside the CHECK ALIVE help popup to explain both
the DATA mode and the RAW mode, while keeping the **CHECK ALIVE** title in bold.

**What changed:**
- `bal/gui/qt/widgets.py`
  - Updated `ThresholdTimeWidget.help_text` with the new wording:
    - kept `<b>CHECK ALIVE</b>` bold;
    - added a **DATA mode** paragraph (set the check-alive date; on wallet open,
      if the date has passed, the plugin asks whether to postpone the
      inheritance, assuming the user is still alive and in control);
    - added a **RAW mode** paragraph (when less than this time is missing, ask
      to invalidate; failing to invalidate in time delivers the transactions to
      the heirs);
    - kept the existing "d / y suffix" explanation.
  - Fixed two typos in this help text: "less then" -> "less **than**", and
    "currrent" -> "**current**" (only within the CHECK ALIVE help text).

**Verification:**
- `py_compile` on the changed file: OK.
- `ruff check`: no new errors (only the pre-existing star-import noise and the
  pre-existing F841 at line 547).
- Full test suite: `239 passed`.

**Outcome:** DONE (delivered as a test ZIP v0.3.8 for the user to try before
commit).

## 15. UI batch (v0.3.9): clearer "will expired" message, History labels, settings checkbox, wizard button

**Context:** A batch of four small UI improvements requested by the user
(internally tracked as TASK A/B/C/D).

**Changes:**
- **(A) "Will expired" message — clearer and not alarming.** Rewrote the message
  raised by `Will.check_will_expired()` (`bal/core/will.py`): the will id is now
  shortened (first 8 + last 8 chars, e.g. `9f1b0a75…fed9ae1b`) and the locktime
  is shown as a readable UTC date (e.g. `2026-06-22 11:00 UTC`) instead of a raw
  UNIX timestamp, with an explanation that the will will be invalidated and
  re-signed. Two small helpers were added (`_short_will_id`, `_format_locktime`)
  and `datetime`/`timezone` are now imported at module top. In the "Build your
  will" wizard (`bal/gui/qt/dialogs.py`) an expired will is now shown in the
  WARNING colour (orange) instead of the ERROR colour (red), because it is an
  expected part of the flow, not an error.
- **(B) History tab labels (text only).** Renamed the labels written to
  Electrum's History tab: inheritance transactions now read
  `BAL Inheritance transaction` (was `BAL Transaction`, updated in both
  `dialogs.py` and `window.py`) and the invalidate transaction now reads
  `BAL Invalidate transaction` (was `BAL Invalidate`, in `window.py`). Colours
  are left to Electrum's defaults (outgoing transactions are shown in red by
  Electrum itself) to keep the change simple and robust.
- **(C) "No will-executor TX" checkbox in plugin settings.** Added a checkbox to
  the settings dialog (`bal/gui/qt/plugin.py`) bound to the existing
  `NO_WILLEXECUTOR` config (default ON), the same config used by the wizard's
  will-executor download window, so the two stay in sync. Its help button reads:
  "Create a will that does not require a Will-executor; it can be saved, for
  example, on a USB stick, and a copy can be given to the heirs." It is also
  included in the "Reset setting" action so a reset restores it to ON.
- **(D) Wizard button label.** The main wizard button now reads `Build Your Will`
  (was `Create your will`, `bal/gui/qt/lists.py`), matching the tooltip and the
  rest of the codebase which already call it the "Build your will" wizard.

- **(A2) Two-line expired message.** Following user feedback that the orange
  message was too long on a single line, a `<br>` line break was added in
  `Will.check_will_expired()` so the second sentence ("too late to anticipate,
  the will will be invalidated and re-signed.") is shown on its own line. The
  message is rendered as HTML by `msg_warning()`, so the `<br>` tag is honoured.
- **(A3) Auto-invalidate regression fix when adding an heir to an expired will.**
  While verifying TASK A a regression was found: when an heir was added to an
  already-expired will through the wizard, the automatic invalidation window no
  longer opened (the user had to restart Electrum or press Check). Root cause:
  in `dialogs.py::task_phase1`, adding an heir first raises
  `HeirNotFoundException` (so the will is rebuilt by `build_will()`), and the
  freshly rebuilt transactions are themselves expired, raising
  `WillExpiredException` on the INNER `check_will()`. The original handler there
  did `return False, None`, which never triggered invalidation. The fix makes
  the wizard, in that case, behave exactly like the "Tools -> Invalidate" menu:
  instead of trying to auto-open the invalidation transaction window (which
  proved unreliable — depending on the OS window manager the transaction window
  ended up BEHIND the main wallet window when the wizard closed, and an
  automatic re-check loop could ask to invalidate repeatedly before the
  invalidation tx reached the mempool), the wizard now closes and shows a clear
  instruction popup telling the user to run `Tools -> Invalidate` themselves and
  then press `Check`. That menu path is already known to work perfectly (its
  transaction window stays in front and sets the `BAL Invalidate transaction`
  history label) and it makes the user consciously aware of this deliberate,
  important action. The reasoning behind this choice is documented in the code.

**Verification:**
- `py_compile` on all changed files: OK.
- `ruff check`: no new errors (only the pre-existing star-import noise and
  pre-existing F841 warnings, none in the changed lines).
- Full test suite: `239 passed`.

**Outcome:** DONE (delivered as test ZIP v0.3.9, confirmed OK by the user before
commit).

---

## 16. v0.4.0 - Unified invalidate, clearer CHECK message, will-executor auto-select, SIMPLE/ADVANCED mode

**Date:** 2026-06-24

This release bundles five related improvements (Groups 1-3 of the user's
to-do list). Version bumped 0.3.9 -> 0.4.0.

### Group 1 - Invalidate / messages

**#01b - Clearer "Checking your will" message.**
The two misleading outcomes "Heir not found" and "New" were both replaced with a
single, accurate two-line message, because in practice that branch is most often
reached when the delivery date was anticipated, not when an heir is missing:

    Found CHANGES to the DATE or the HEIRS,
    a NEW WILL must be prepared.

- `bal/gui/qt/dialogs.py`: `task_phase1` - HeirNotFoundException branch and the
  no-subtype fallback (previously "New").
- `bal/gui/qt/window.py`: `build_inheritance_transaction` - HeirNotFoundException
  branch (kept consistent with the CHECK window).
- The other specific messages (Heirs changed / Will-Executor not present /
  Will-Executor changed / Txfees changed) are unchanged.

**UNIFY invalidate procedure + #03 - Same behaviour for CHECK and WIZARD, and the
history label is now always written.**
When a will is expired (CHECK on an expired will, or an heir added to an expired
will), the plugin now ALWAYS:
1. shows a warning popup (no more "use Tools -> Invalidate" wording);
2. automatically opens Electrum's classic transaction window via
   `BalWalletWindow.invalidate_will()` so the user signs and broadcasts the
   invalidation - this path already sets the "BAL Invalidate transaction" history
   label, which fixes #03 (the label was missing when invalidating from the
   automatically-opened window).
- `bal/gui/qt/dialogs.py`: the first `WillExpiredException` handler in
  `task_phase1` now returns the `"invalidate_classic"` signal (instead of routing
  to the label-less automatic path); `on_success_phase1` shows the approved popup
  text and opens the classic window with `QTimer.singleShot(0, ...)` AFTER closing
  the wizard, so the transaction window stays in front.

### Group 2 - Will-executor auto-select (#02)

In the wizard's "Automatically download and select willexecutors" flow, the green
SELECTED tick now follows the green ping dot: only servers that actually answered
the ping (status == 200) are selected, and every non-responding server is
explicitly DESELECTED. This discards dead/slow servers at the source (no more
getting stuck broadcasting to them) and is re-evaluated on every download, so a
server that failed before but now answers is selected again.
- `bal/gui/qt/dialogs.py`: `ping_on_done` in `BalWizardWEDownloadWidget._on_next`
  (robust `str(status) == "200"` comparison + safe `.get`).

### Group 3 - SIMPLE / ADVANCED mode (global)

A new global "USER TYPE" setting lets the user pick a simpler interface.
- New global config `USER_TYPE` (default `"basic"`) and helper
  `BalPlugin.is_basic_mode()` in `bal/core/plugin_base.py`. Stored in Electrum's
  global config, so it does not affect existing wallet files; an old wallet opens
  with the global value and its owner can switch to ADVANCED at will.
- `bal/gui/qt/plugin.py`: new "USER TYPE" row in the settings dialog - a two-choice
  combo [BASIC, ADVANCED] with a HelpButton, added at the top (row 0) and to the
  "Reset setting" list (reset -> BASIC).
- In BASIC mode:
  - the Raw/Date selector is hidden and every date field is forced to the calendar
    ("Date") editor, so the user never sees or uses RAW
    (`bal/gui/qt/widgets.py`, `BalTimeEditWidget`);
  - the whole "Check Alive" (threshold) row and its icon are hidden, while the
    "Delivery time" (locktime) row stays visible
    (`bal/gui/qt/widgets.py`, `WillSettingsWidget`);
  - the "check alive" postpone behaviour is disabled: `init_class_variables`
    skips raising `CheckAliveError`, so a passed check-alive date never forces a
    postpone/rewrite (`bal/gui/qt/window.py`). The delivery time is unaffected.

### Verification
- `ruff`: no new errors in the changed lines (only the usual pre-existing
  F401/F403/F405 star-import noise and one pre-existing F841).
- Full test suite: `239 passed`.

**Outcome:** DONE (delivered as test ZIP v0.4.0; commit only after the user
confirms the ZIP works).

---

## 17. v0.4.1 - Heir-change rebuild fix, all heirs in green, UI tidy-up, unified 20s deadline

**Date:** 2026-06-24

**Goal:** Address the user's v0.4.0 test feedback (points A-K), with the most
important items being two functional bugs surfaced via a log:

- **(E + F + K) Heir-change full rebuild (the core fix):** After deleting or
  changing an heir, pressing CHECK said "Found CHANGES... a NEW WILL must be
  prepared" but then "Signing: Nothing to do" / "Broadcasting: Nothing to do",
  and the rebuilt transactions were never signed or broadcast. The log showed
  that, right after `build_will()`, a second coherence check raised
  `HeirNotFoundException(<heir name>)`, which was (1) shown in RED with the heir
  name (bug E) and (2) made the dialog return `have_to_sign=False` (bugs F/K).
  Root cause: `Will.update_will` reused the OLD (already signed/COMPLETE)
  `WillItem` whenever a rebuilt transaction kept the same txid, copying only the
  new heirs onto it - so a stale heir set survived and the item stayed COMPLETE.
  Fix (user-approved "Option A"): a new helper `Will._same_heirs` compares the
  real heirs (address/amount/locktime, ignoring the reserved `w!ll3x3c"`
  will-executor pseudo-heirs); the old item is reused ONLY when the heirs are
  identical, otherwise the freshly built (unsigned) item is kept so it is
  correctly detected as needing signing and broadcasting. This also fixes K:
  once the rebuilt tx is not COMPLETE, the existing auto-sign + auto-push path
  (have_to_push when a will-executor is attached) fires automatically.
- **(E) Show all heirs in GREEN:** On a successful build, every heir is now
  listed on its own line in green ("Ok" colour) in the Building Will window,
  instead of a heir name ever appearing in red as exception text.
- **(A)** Delivery-time help popup: added a "(ONLY IN ADVANCED MODE)" line
  before the Raw-suffix explanation.
- **(B)** Plugin settings: added a blank gap below the red warning, above the
  first setting row.
- **(C)** Renamed the "USER TYPE" settings label to "User Type".
- **(D)** Renamed "Editable dates" to "Panel editable Date and Fee".
- **(G + K-label)** Moved the will-executor backup-tx checkbox up (now right
  below "Panel editable Date and Fee", above "Number of reminders") and renamed
  it from "No will-executor TX" to "Add transaction without willexecutor".
- **(H)** Wizard date-field layout: date rows are now a compact fixed width
  (no longer the inflated 40-char minimum), the calendar button is left-aligned
  and no longer stretches, and the mining-fee field is sized to ~5 characters.
  ADVANCED mode (with the extra Check-Alive row) stays aligned.
- **(J)** Network waits unified into a single shared
  `Willexecutors.NETWORK_DEADLINE = 20` constant (was 30s push/check and 45s
  download); push/check/ping/download all derive from it.

**Files changed:**
- `bal/core/will.py` - new `_same_heirs` helper; `update_will` reuses old item
  only when heirs are identical (Option A).
- `bal/gui/qt/dialogs.py` - list all heirs in green on successful build.
- `bal/gui/qt/widgets.py` - help-text line (A); compact, left-aligned wizard
  date/fee layout (H).
- `bal/gui/qt/plugin.py` - settings labels/spacing/row order (B, C, D, G, K-label).
- `bal/core/willexecutors.py` - single `NETWORK_DEADLINE = 20` (J).
- `bal/gui/qt/window.py` - download deadline derives from `NETWORK_DEADLINE` (J).
- `tests/test_group_f_heir_change_rebuild.py` - new unit tests for `_same_heirs`.
- Version bumped 0.4.0 -> 0.4.1 (`plugin_base.py`, `__init__.py`, `VERSION`,
  `manifest.json`).

**Verification:**
- `ruff`: no new errors (only the pre-existing F401/F403/F405 star-import noise
  and two pre-existing F841 at `will.py:151` and `dialogs.py:645`).
- Full test suite: `248 passed` (239 existing + 9 new Group F tests).

**Outcome:** PARTIAL (delivered as test ZIP v0.4.1). User testing showed that
the CORE bug (E/F/K) was STILL present: the `_same_heirs`/`update_will` fix was
misdirected - on a real heir change the rebuilt transactions get COMPLETELY NEW
txids, so `update_will`'s "reuse the old item when the txid matches" branch never
runs. The real cause was found and fixed in entry 18. The `_same_heirs` helper is
kept as a correct safety improvement. Items A, B, C, D, G, J, I were confirmed OK.

---

## 18. v0.4.2 - Real fix for the heir-change rebuild bug (E/F/K), wizard layout (H), tooltip

**Date:** 2026-06-24

**Goal:** Fix the bugs the user found while testing v0.4.1: after adding or
deleting an heir (or building from the wizard), the heir name was shown in RED
and the will was reported as "Nothing to do" (never signed/broadcast); the
green heir list disappeared; the wizard icons/fields were still misaligned and
the fee box was too small; and a tooltip needed rewording.

**Root cause of E/F/K (confirmed from the user's Electrum log):** In
`task_phase1` (dialogs.py), after `build_will()` rebuilds the whole will, a
SECOND `check_will()` re-validates it. When the heirs (or date) changed, this
re-validation legitimately raises a `NotCompleteWillException` subclass
(`HeirNotFoundException`) - the freshly rebuilt, still-unsigned transactions do
not yet "cover" the new heir set. That exception was caught by the GENERIC
`except Exception`, which printed the heir name in RED and returned
`have_to_sign=False`, so the rebuilt will was never signed ("Nothing to do").

**What changed:**

- **(E/F/K)** `bal/gui/qt/dialogs.py`: added a dedicated
  `except NotCompleteWillException` handler BEFORE the generic `except Exception`
  (and after the existing `WillExecutorNotPresent` / `WillExpiredException`
  handlers, whose order matters). It treats the post-build re-validation failure
  as what it really is - "the will was rebuilt and now needs signing" - instead
  of an error. It shows the green "Ok" result and the full heir list, then falls
  through to the existing `have_to_sign` detection, so the new (status "New")
  transactions are correctly signed and (with a will-executor attached)
  auto-broadcast. The heir-green-listing was extracted into a new
  `_build_success_report()` helper, used on BOTH the clean and the rebuilt
  paths, so the green heir list is always shown and the heir name never appears
  in red again.
- **(H)** `bal/gui/qt/widgets.py`: reworked the wizard's vertical layout. Every
  leading icon (delivery time, check-alive, calendar, fee) is forced to the same
  fixed width and the composites are stacked left-aligned, so the icons line up
  one under the other and every field starts at the same x just to their right.
  The fee field is WIDENED (~8 chars) so the spin-box arrows no longer cover the
  digits. The composites are kept INTACT (not split apart) because the
  delivery-time / check-alive widgets hold two editors plus a runtime Raw/Date
  selector in ADVANCED mode; splitting them would break that mode.
- **Tooltip** `bal/gui/qt/widgets.py`: the delivery-time icon tooltip now reads
  exactly "Delivery Time, click for more information".

**Files changed:**
- `bal/gui/qt/dialogs.py` - `NotCompleteWillException` handler + `_build_success_report`.
- `bal/gui/qt/widgets.py` - wizard layout (H) and tooltip wording.
- Version bumped 0.4.1 -> 0.4.2 (`plugin_base.py`, `__init__.py`, `VERSION`,
  `manifest.json`).

**Verification:**
- `ruff`: no new errors (only pre-existing star-import noise and the two
  pre-existing F841 at `widgets.py:563` and `dialogs.py:645`).
- Full test suite: `248 passed`.

**Outcome:** DONE (delivered as test ZIP v0.4.2; commit only after the user
confirms the ZIP works).

---

## 19. v0.4.3 - Sync delivery date after auto-anticipation; BASIC calendar reminders; sign-reason message

**Date:** 2026-06-24

**Goal:** Fix a follow-up bug reported by the owner after testing v0.4.2, add a
clearer message when signing is requested, and make the calendar export work in
BASIC mode.

**(1) Delivery date out of sync after an automatic anticipation (main bug).**

Root cause (confirmed from the code + the owner's report): when the will is
rebuilt while it still spends the same coins as a previous one (e.g. after
deleting an heir WITHOUT changing the date), the core engine AUTOMATICALLY
anticipates the transaction locktime by one day (Will.check_anticipate /
Util.anticipate_locktime) so the new transaction can be mined before the old
one. However the plugin's stored delivery date (WILL_SETTINGS["locktime"]) was
NOT updated, so on the next Check the plugin compared the stored date (original)
with the transaction locktime (original minus one day), mistook the automatic
anticipation for a user POSTPONE, and wrongly asked to invalidate the will.

Fix: after a (re)build, `BalBuildWillDialog._sync_locktime_to_built_txs` sets the
stored delivery date to the MINIMUM locktime among the valid built transactions
(via `Will.get_min_locktime`). The date is only ever moved EARLIER
(anticipation); a genuine user postpone is never overwritten. The update is
routed through `BalWindow.update_setting_widgets`, which stores the value,
persists it and refreshes the date widgets in every panel/wizard, so the visible
delivery date reflects the anticipated date and the calendar (.ics) export uses
it too (owner-confirmed behaviour; the minimum is used when several
transactions carry different locktimes).

**(2) Explain WHY signing is requested after an anticipation.**

When the date was auto-anticipated, the wizard now shows an orange note on the
"Building your will" row before the sign prompt: "The delivery date was
automatically moved one day earlier so the updated will can correctly replace
the previous one. Please sign (and broadcast) to confirm the change." (A
`_date_was_anticipated` flag set by the sync step drives this.)

**(3) Calendar reminders in BASIC mode.**

In BASIC mode the check-alive parameter is hidden and not managed by the user,
so spreading reminders over the check-alive period (the ADVANCED behaviour) is
meaningless and produced wrong/garbage dates. The calendar now uses three FIXED
reminders in BASIC - 30, 10 and 1 day before the inheritance delivery date -
dropping any offset that would fall in the past. ADVANCED mode is unchanged. The
logic is a pure helper `basic_reminder_offsets()` with unit tests.

**Files changed:**
- `bal/gui/qt/dialogs.py` - `_sync_locktime_to_built_txs` + `_date_was_anticipated`
  flag + sign-reason note.
- `bal/gui/qt/widgets.py` - BASIC calendar reminders (`basic_reminder_offsets`,
  `BASIC_REMINDER_OFFSETS`); the .ics export now uses the anticipated delivery
  date.
- `tests/test_group_g_basic_calendar.py` - new unit tests for the BASIC
  reminder offsets.
- Version bumped 0.4.2 -> 0.4.3 (`plugin_base.py`, `__init__.py`, `VERSION`,
  `manifest.json`).

**Verification:**
- `ruff`: no new errors (only pre-existing star-import noise and the two
  pre-existing F841 at `widgets.py:563` and `dialogs.py:645`).
- Full test suite: `255 passed` (248 + 7 new BASIC-calendar tests).

**Outcome:** DONE (delivered as test ZIP v0.4.3; commit only after the user
confirms the ZIP works).

---

## 20. v0.4.4 - Wizard hints, BASIC/ADVANCED help text, Check tooltip, ADVANCED check-alive visibility, backup-tx default OFF

**Date:** 2026-06-24

**Goal:** Apply a batch of owner-requested UI/wording fixes and fix a visibility
bug for the Check-Alive field.

**What changed:**

- **(P1)** Plugin settings, "Number of reminders" help text: rewritten to
  document BASIC and ADVANCED separately. BASIC: "Calendar reminder 30, 10 and
  1 days before."; ADVANCED: the previous "spread across the check-alive period"
  explanation (range 1 to 5, default 3).
- **(P2)** Build-your-will WIZARD only: added an explanatory label ABOVE the
  date field - "Enter the date on which you want the inheritance (or backup) of
  your Electrum wallet to take effect." - and a cautionary label BELOW the
  miner-fee field - "Please note: Do not reduce the miner fees unless you know
  what you're doing". These appear only in the wizard (vertical layout), not on
  the WILL/HEIR toolbars.
- **(P3)** WILL tab: the Check button tooltip changed from "Check" to
  "Check Inheritance" so the icon's purpose is clear.
- **(P4 - bug fix)** Check-Alive visibility on USER TYPE change: the WILL/HEIR
  toolbar settings widgets are created once and reused for the whole session, so
  switching from BASIC to ADVANCED previously did NOT re-show the Check-Alive
  field there (it reappeared only in the freshly-created wizard). Added
  `WillSettingsWidget.apply_user_type_visibility()`, called from
  `BalWindow.update_all()` (which the USER TYPE combo triggers), so the
  Check-Alive field is shown/hidden immediately on the existing WILL and HEIR
  tabs too, without restarting Electrum.
- **(P5)** "Add transaction without willexecutor" now defaults to OFF
  (`NO_WILLEXECUTOR` default False). A fresh wallet therefore does NOT create the
  extra no-will-executor backup transaction unless the user enables it from the
  wizard. The value is persisted in Electrum's configuration (as before), so the
  plugin always follows the saved choice; the new default only applies when no
  value has been stored yet.

**Files changed:**
- `bal/gui/qt/plugin.py` - reminders help text (P1).
- `bal/gui/qt/widgets.py` - wizard date hint + miner-fee note (P2);
  `apply_user_type_visibility` (P4).
- `bal/gui/qt/lists.py` - Check tooltip -> "Check Inheritance" (P3).
- `bal/gui/qt/window.py` - call `apply_user_type_visibility` from `update_all` (P4).
- `bal/core/plugin_base.py` - `NO_WILLEXECUTOR` default -> False (P5).
- Version bumped 0.4.3 -> 0.4.4 (`plugin_base.py`, `__init__.py`, `VERSION`,
  `manifest.json`).

**Verification:**
- `ruff`: no new errors (only pre-existing star-import noise and pre-existing
  F841 at `widgets.py:591`, `lists.py:185` and `lists.py:272`).
- Full test suite: `255 passed`.

**Outcome:** DONE (delivered as test ZIP v0.4.4; commit only after the user
confirms the ZIP works).

---

## 21. v0.4.5 - Fix invalidation loop (10s pause + stop-on-persistent-postpone), add "BAL Invalidate transaction" label on the automatic path, fix wizard text truncation

**Date:** 2026-06-24

**Reported issues (after testing v0.4.4):**

- **Issue 1 (wizard text truncated):** the explanatory hint above the delivery
  date and the miner-fee note below the fee field were displayed cut in half.
- **Issue 2 (invalidation loop):** when the user postpones the delivery date,
  the "Invalidate your old will" window appears; after signing, an IDENTICAL
  invalidation window immediately appears again (endless loop) while Electrum
  is still broadcasting the transaction. In addition, the
  "BAL Invalidate transaction" label did not appear in Electrum's on-chain
  history for this automatic ("postpone") path.

**Root causes:**

- Issue 1: the wizard QLabels had `setWordWrap(True)` but were added to the
  vertical layout with `AlignLeft`, so each label took its narrow `sizeHint`
  width. Word-wrap then computed line breaks against an almost-zero width and
  the text appeared truncated.
- Issue 2a (loop): after broadcasting the automatic invalidation,
  `on_success_invalidate` re-ran phase 1 immediately. Electrum had not yet seen
  the invalidation transaction, so phase 1 still detected a postpone and the
  wizard re-prompted to invalidate - forever.
- Issue 2b (missing label): the automatic broadcast in
  `loop_broadcast_invalidating` did not set any history label (unlike the
  Tools -> Invalidate menu path, which does).

**What changed (user-approved solution):**

- `bal/gui/qt/dialogs.py`
  - `loop_broadcast_invalidating`: set the `"BAL Invalidate transaction"`
    history label (fixes Issue 2b), matching the Tools -> Invalidate menu.
    IMPORTANT follow-up fix: the first attempt did not work because it took the
    txid from `Network.broadcast_transaction()`'s return value - but that method
    is declared `-> None` and ALWAYS returns None, so the `set_label()` call
    sat in an `else` branch that was never reached. The label is now taken from
    `tx.txid()` (the transaction is already signed and complete here, so this is
    the stable, correct id - exactly what the working Tools -> Invalidate path
    uses) and is set BEFORE broadcasting (set_label is local-only, no network).
  - `invalidate_task`: the post-broadcast pause is now 10 seconds (was 5) so
    Electrum has time to register the new transaction before phase 1 re-runs;
    a new `self._invalidation_broadcast` flag is set after the broadcast.
  - `on_success_phase1` (the `have_to_sign is None` branch): if
    `_invalidation_broadcast` is set and a postpone is STILL detected, STOP with
    a clear message ("Your old will has been invalidated and the transaction was
    broadcast... please wait until it is confirmed, then press Check again")
    instead of re-prompting to invalidate (fixes Issue 2a - no more loop).
  - `__init__`: initialise `self._invalidation_broadcast = False`.

- `bal/gui/qt/widgets.py`
  - Wizard branch: give the two explanatory QLabels (delivery-date hint and
    miner-fee note) a `setMinimumWidth(30 * char_width_in_lineedit())` so
    word-wrap uses the full dialog width and the whole text is visible
    (fixes Issue 1).

- Version bumped 0.4.4 -> 0.4.5 (`plugin_base.py`, `__init__.py`, `VERSION`,
  `manifest.json`).

**Verification:**

- `py_compile`: `dialogs.py` and `widgets.py` compile OK.
- Full test suite: `255 passed`.
- `ruff`: no new errors (only pre-existing star-import F401/F403/F405 noise and
  the pre-existing F841 `e` warnings).
- Headless wizard label check: with the 270px minimum width, both labels wrap
  to multiple lines (3 lines and 2 lines) instead of being truncated.

**Outcome:** DONE (delivered as test ZIP v0.4.5; commit only after the user
confirms the ZIP works).

---

## 22. v0.4.6 - DUST one-line-per-heir, heirs on one line, scrollable report, wizard final check, wizard text truncation fix, anticipated-date notice styling

**Date:** 2026-06-24

**Reported issues (after testing v0.4.5):**

- **Issue 1 (allegato13 - DUST):** when the wallet balance is below the dust
  limit the inheritance is not feasible. The dialog printed the "is DUST"
  exclusion ONCE PER (will-executor x heir): with 20 will-executors and 10
  heirs that is 200 identical rows. There should be one row PER HEIR.
- **Issue 2 (allegato18 - heirs):** heirs were listed one per line, wasting
  vertical space. They should be on a single line: "Heirs: a, b, c".
- **Issue 3 (allegato14 - overflow):** with many will-executors the "Building
  Will" window kept growing taller for every line until it ran off-screen and
  the bottom buttons became unreachable. It needs a scrollable area.
- **Issue 4 (allegato15 - wizard final check, case B):** the wizard did not run
  the final will-executor verification, so the user always had to press "Check"
  manually afterwards.
- **Issue 5 (allegato16 - wizard text truncated):** the wizard hint and fee
  note were still cut in half.
- **Issue 6 (allegato17 - notice styling):** the yellow "delivery date was
  moved" notice should be black bold and split onto two lines.

**Root causes:**

- Issue 1: a double loop over every valid will AND every heir printed the dust
  row N_executors x N_heirs times.
- Issue 4: the wizard's `on_next_we` only called `build_will_task()` and, unlike
  the "Check" button (`lists.py:check()`), never called
  `check_transactions()`.
- Issue 5: the two QLabels were added to the vertical layout WITH an
  `alignment=AlignLeft` flag. A widget added with an alignment flag is NOT
  stretched to the layout width, so the word-wrapped label used a narrow
  sizeHint width and the reserved height was too small, cutting the text.
  `setMinimumWidth` alone could not fix it because the alignment flag still
  blocked horizontal stretch.

**What changed:**

- `bal/gui/qt/dialogs.py`
  - `task_phase1` (DUST report): collect dust heirs in a de-duplicated dict and
    print ONE row per heir, without the will-executor reference (Issue 1).
  - `_build_success_report`: list all heirs on a SINGLE green/bold line
    ("Heirs: a, b, c") instead of one row each (Issue 2).
  - `__init__` + `msg_update`: wrap the report label in a `QScrollArea` with a
    capped maximum height (400px) and auto-scroll to the bottom, so the dialog
    no longer grows off-screen and the buttons stay reachable (Issue 3).
  - `BalWizardDialog.on_next_we`: after building, run the SAME final
    `check_transactions()` as the Check button (`Will.needs_server_check`),
    so the wizard performs the will-executor verification automatically
    (Issue 4).
  - `on_success_phase1`: the anticipated-date notice is now black bold and split
    onto two lines after "...previous one." (Issue 6).

- `bal/gui/qt/widgets.py`
  - Wizard branch: add the two explanatory QLabels WITHOUT an alignment flag and
    with an Expanding/Minimum size policy (plus a minimum width on the widget),
    so they stretch to the full width and word-wrap correctly instead of being
    truncated (Issue 5).

- Version bumped 0.4.5 -> 0.4.6 (`plugin_base.py`, `__init__.py`, `VERSION`,
  `manifest.json`).

**Verification:**

- `py_compile`: `dialogs.py` and `widgets.py` compile OK.
- Full test suite: `255 passed`.
- `ruff`: no new errors (only pre-existing star-import noise and the
  pre-existing F841 `e` warnings).
- Headless wizard label check (real wizard layout, 780px dialog): both labels
  expand to the full width (740px) and the full text fits - NOT truncated.

**Outcome:** DONE (delivered as test ZIP v0.4.6; commit only after the user
confirms the ZIP works).

---

## 23. v0.4.7 - Report area opens 500px tall, heirs back to one-per-line, explicit wizard line breaks, ALL-DUST guard (block only when EVERY heir is dust)

**Date:** 2026-06-24

**Goal (4 owner-approved changes from testing v0.4.6):**

1. (allegato1) The scrollable "Building Will" report opened far too short
   (~140px). Open it already 500px tall, growing up to 700px before the
   scrollbar takes over.
2. Revert the v0.4.6 "all heirs on one line" form back to ONE heir per line
   (green, bold). Heir names can be long and, now that the report scrolls,
   there is no need to compress them.
3. (allegato2) Add an explicit line break in two wizard texts at the exact
   spots the owner marked: after "(or backup)" in the date hint and after
   "miner fees" in the fee note.
4. (LOG analysis) When EVERY heir's share is below the dust limit the will was
   still built, signed, checked and listed - an "empty" inheritance that pays
   nobody. Block it with a clear message, but ONLY when ALL heirs are dust; a
   mix of dust + valid heirs must keep building normally.

**What changed:**

- `bal/gui/qt/dialogs.py`
  - `BalBuildWillDialog.__init__`: report `QScrollArea` now
    `setMinimumHeight(500)` / `setMaximumHeight(700)` (change 1).
  - `_build_success_report`: list each heir on its own line again, green +
    bold, de-duplicated and skipping the internal will-executor pseudo-heirs
    (change 2, revert of v0.4.6).
  - `task_phase1`: add a dedicated `except HeirAmountIsDustException` handler
    BEFORE the generic `except Exception`, showing a clear RED message
    ("All heirs' shares are below the dust limit: the inheritance cannot be
    created. Increase the amounts or reduce the number of heirs.") and stopping
    without signing/checking, so no empty will is created (change 4).

- `bal/gui/qt/widgets.py`
  - Wizard date hint: explicit `\n` after "(or backup)".
  - Wizard fee note: explicit `\n` after "miner fees" (change 3).

- `bal/core/heirs.py`
  - `prepare_lists`: NEW all-dust guard added at the END of the function, where
    the `locktimes` dict already contains EVERY heir of EVERY locktime with the
    final dust marking. It counts the real heirs (excluding the `w!ll3x3c"`
    will-executor pseudo-heirs) and how many have a valid, non-dust amount; if
    there are real heirs but none is payable it raises
    `HeirAmountIsDustException` (change 4).
  - WHY here and NOT in `prepare_transactions`: `prepare_transactions` only
    ever processes the single lowest locktime, so a guard there would wrongly
    block a will whose later locktimes still have valid heirs (false positive).
    `prepare_lists` is the only place that sees all heirs/locktimes AND the
    final dust state of both fixed and percentage heirs.
  - The `HeirAmountIsDustException` raised here propagates cleanly: it is not a
    `WillExecutorFeeException`, so it skips that handler in `buildTransactions`
    and reaches the GUI without the misleading "error preparing transactions"
    log.

- `bal/gui/qt/common.py`
  - Import `HeirAmountIsDustException` from `...core.heirs` so it is available
    to `dialogs.py` via `from .common import *`.

- `tests/test_core_heirs_extra.py`
  - Add 3 tests pinning the dust logic:
    `test_prepare_lists_all_dust_raises` (tiny balance + percentages -> raises),
    `test_prepare_lists_mixed_dust_continues` (dust + valid -> no raise),
    `test_prepare_lists_multi_locktime_continues` (dust on early date, valid on
    later date -> no raise; guards against the false positive).

- Version bumped 0.4.6 -> 0.4.7 (`plugin_base.py`, `__init__.py`, `VERSION`,
  `manifest.json`).

**Verification:**

- `py_compile`: `heirs.py`, `common.py`, `dialogs.py` and the test file compile OK.
- Full test suite: `258 passed` (255 previous + 3 new dust tests).
- `ruff`: no new errors (only pre-existing star-import noise and the
  pre-existing F841 `e` warnings).
- Manual dust trace (real `prepare_lists`, mocked wallet): all-dust raises;
  mixed and multi-locktime continue and keep the valid heir.

**Outcome:** DONE (delivered as test ZIP v0.4.7; commit only after the user
confirms the ZIP works).

---

## 25. v0.5.0 - Will-executor add/edit dialog with sync, double-click to edit, auto-sign fix

**Date:** 2026-06-30

**Goal:** Complete the will-executor add/edit dialog with sync ping, enable
double-click to edit, and fix the auto-sign toggle so it actually disables
signing when OFF and hides in BASIC mode.

**What changed:**

- `bal/gui/qt/lists.py`
  - `WillExecutorListWidget.on_activated` / `.on_double_click`: double-click or
    Enter on a will-executor opens the add/edit dialog in edit mode with
    pre-populated fields (URL, Info, Base Fee, Address).
  - `WillExecutorWidget.add()` accepts optional `edit_key` param: pre-populates
    fields, shows "Add another" only in add mode (not edit), renames URL key in
    `willexecutors_list` if changed, updates status/last_update on sync.
  - Added sync button (`↻`) with `TaskThread` + `Willexecutors.get_info_task()`;
    loading label shows `⟳` during async ping; on error shows
    `QMessageBox.warning(d, …)` then focuses URL input with `selectAll()`.

- `bal/gui/qt/dialogs.py`
  - `_on_success_phase1_body`: auto-sign guard — skips signing/broadcast when
    `AUTO_SIGN` is OFF in ADVANCED mode; basic mode always auto-signs.
  - `_show_next_steps_hint`: suppresses manual hints when auto-sign is
    effectively active (basic mode or AUTO_SIGN ON).

- `bal/gui/qt/plugin.py`
  - AUTO_SIGN setting row: named widgets (`lbl_auto_sign`, `heir_auto_sign`,
    `help_auto_sign`, `reset_btn_auto_sign`) added to both basic/advanced
    visibility toggle lists (hidden in basic).

- `bal/gui/qt/window.py`
  - Heir "Add another" button no longer the default (removed `setDefault(True)`).

- Version bumped 0.4.8 -> 0.5.0 (`plugin_base.py`, `__init__.py`, `VERSION`,
  `manifest.json`).

**Verification:**

- `py_compile`: `lists.py`, `dialogs.py`, `plugin.py`, `window.py` compile OK.
- `ruff`: no new errors (only pre-existing star-import / F841 noise).

**Outcome:** DONE.

---

## 24. v0.4.8 - Raw/Date selector on WILL/HEIR tabs, shorter report window, USER TYPE moved + "at My Risk" gate, executed/mempool inheritance note, "balance too low" recoloured, Reset button renamed

**Date:** 2026-06-25

**Goal (owner requests, this session):** seven small, independent UX fixes,
delivered together in one version.

**What changed:**

- **#04 - Raw/Date selector now reappears on the WILL/HEIR tabs.**
  `bal/gui/qt/widgets.py`
  - Added `BalTimeEditWidget.apply_user_type_visibility()`: re-reads
    `is_basic_mode()` and shows/hides ONLY the Raw/Date combo, WITHOUT changing
    the current value or active editor (owner request: keep the value to avoid
    confusing the delivery date with the inheritance).
  - `WillSettingsWidget.apply_user_type_visibility()` now also calls the new
    method on BOTH the `locktime` and `threshold` boxes. The reused toolbars used
    to hide the combo forever after construction; now switching to ADVANCED (or a
    raw value pushed from the wizard) reveals it without restarting Electrum.

- **#3 - Building Will report window shorter.**
  `bal/gui/qt/dialogs.py`: report scroll area minimum height `500 -> 450` px.

- **#5 - "User Type" moved to the bottom of the settings.**
  `bal/gui/qt/plugin.py`: the "User Type" row moved from the first grid row to
  the bottom, just above "Rebroadcast transactions"; the other rows were
  renumbered up by one.

- **#6 - "at My Risk" gate before enabling ADVANCED.**
  `bal/gui/qt/plugin.py` (`on_user_type_change`): selecting ADVANCED now prompts
  "Type 'at My Risk' to enable ADVANCED mode"; the phrase is accepted
  case-insensitively. A wrong phrase or a cancel reverts to BASIC.
  `bal/gui/qt/common.py`: `QInputDialog` added to the shared Qt import.

- **#7a - Reassuring note when the inheritance was already executed.**
  `bal/gui/qt/dialogs.py`: added `_executed_inheritance_status()` (reads the
  will items' CONFIRMED / MEMPOOL flags, CONFIRMED wins). In the
  `NotCompleteWillException` branch of `task_phase1`, when the wallet is empty
  because the inheritance went through, an extra line is shown on the "Checking
  your will" row: "Inheritance already executed (on blockchain)" in GREEN
  (CONFIRMED) or "Inheritance in mempool (waiting confirmation)" in ORANGE
  (MEMPOOL). The original "changes" message is still shown afterwards.

- **#7b - "balance too low" message recoloured.**
  `bal/gui/qt/dialogs.py`: the text is now
  "Balance is too low, or CheckAlive is in the past. Skipped" (a space added
  before "Skipped") and rendered in ORANGE (`COLOR_WARNING`) instead of red,
  since an empty wallet after execution is normal, not an error.

- **Reset button renamed.**
  `bal/gui/qt/plugin.py`: "Reset setting" -> "Reset to Default Setting".

- Added `tests/test_group_h_v048.py` with 8 GUI-free tests pinning the
  executed-inheritance detection rule (CONFIRMED > MEMPOOL > None) and the
  "at My Risk" case-insensitive gate.

- Version bumped 0.4.7 -> 0.4.8 (`plugin_base.py`, `__init__.py`, `VERSION`,
  `manifest.json`).

**Verification:**

- `py_compile`: `widgets.py`, `dialogs.py`, `plugin.py`, `common.py` compile OK.
- Full test suite: `266 passed` (258 previous + 8 new v0.4.8 tests).
- `ruff`: no new errors (only pre-existing star-import / F841 noise).

**Outcome:** DONE (delivered as test ZIP v0.4.8; commit only after the user
confirms the ZIP works).

---

## 25. v0.5.1 - Fix Windows Settings-dialog flicker (rollback to v0.5.0 + flicker fix only)

**Date:** 2026-07-02

**Context:** Starting from the clean v0.5.0 codebase (a set of later
experimental builds, 0.5.1-0.5.4, were discarded because they introduced
other problems). This release applies ONLY the Windows Settings-dialog
flicker fix and changes nothing else - in particular, NO changes to locktime,
delivery time, Check Alive, or any will-building logic. Only
`bal/gui/qt/plugin.py` is touched (verified byte-for-byte against v0.5.0:
every other file is identical).

**Bug:** On Windows only, opening the BAL Settings dialog in ADVANCED mode
briefly flashed one or two blank "ghost" windows before the dialog rendered
correctly. On Linux it did not occur.

**Root cause (found by bisection, each step tested on Windows by the user):**
the ~24 ADVANCED-only widgets (Welist Server, Number of reminders, Event
summary/description, Calendar app, Auto-sign, and their per-row reset buttons)
were added to the settings grid *visible* and then hidden all at once with a
`setVisible(False)` loop AFTER they were already part of the live layout. On
Windows, that visible->hidden transition inside an already-populated layout,
happening while the dialog's native window was being created, forced a
re-layout that flashed the dialog on screen. It only appeared in ADVANCED
mode because that is when those rows are toggled and the dialog is largest.
A minimal dialog (no ADVANCED-only rows) never flickered; re-adding only the
construction-time `setVisible()` toggle reproduced it; hiding each widget
BEFORE adding it to the layout eliminated it (all confirmed on Windows).

**What changed (`bal/gui/qt/plugin.py`, `settings_dialog` only):**

- Added a `basic_init` flag and a `_hide_if_basic(widget)` helper right after
  the dialog is created. Each ADVANCED-only widget is now wrapped in
  `_hide_if_basic(...)` at its `grid.addWidget(...)` call, so its final
  visibility is set BEFORE it ever joins the layout - it never transitions
  visible->hidden inside a live layout.
- Removed the old post-hoc `setVisible()` loop that hid all ~24 widgets at
  once after they were already added (the flicker cause).
- The runtime BASIC<->ADVANCED toggle (`on_user_type_change`, used while the
  dialog is already open and visible) is unaffected and still works exactly
  as before - only the one-time construction-time visibility set-up changed.

**Verification:**

- Full test suite: `266 passed` (same as clean v0.5.0), plus the same 2
  pre-existing, unrelated failures already present in v0.5.0
  (`test_core_plugin_base.py::test_default_will_settings` /
  `test_validate_will_settings`, expecting the old `baltx_fees=100` default
  that v0.5.0 already changed to `20`).
- `ruff`: no new errors (only pre-existing star-import noise).
- Confirmed on Windows by the user that the fix (validated as an isolated
  diagnostic build) removes the flicker.
- Only `bal/gui/qt/plugin.py` differs from clean v0.5.0; all other files,
  including all locktime/date/Check-Alive logic, are byte-identical.

**Outcome:** DONE (delivered as test ZIP v0.5.1; commit only after the user
confirms the ZIP works on Windows).

---

## 26. v0.5.2 - Show Check Alive in the main window in BASIC mode (read-only, Date-only)

**Date:** 2026-07-04

**Goal (owner request):** In BASIC mode the "Check Alive" (threshold) field is
currently hidden everywhere, so the owner cannot observe how it behaves behind
the scenes. Make it VISIBLE in the MAIN WINDOW toolbar in BASIC mode, but
strictly for observation: it must be ALWAYS read-only (never editable, not even
when "Panel editable Date and Fee" is enabled) and always shown as a Date
(never Raw). It must remain editable only in ADVANCED mode (as today), and it
must stay hidden in the "Build your will" wizard in BASIC mode. This is a
display-only change: no locktime/date/Check-Alive computation or validation
logic is touched (in particular, none of the discarded v0.5.4 auto-compute
behaviour is reintroduced).

**What changed (all in `bal/gui/qt/widgets.py`, GUI only):**

- `WillSettingsWidget.__init__`: the BASIC-mode hiding of the Check Alive row
  is now scoped to the WIZARD only (vertical layout, `layout_type != "h"`). In
  the main-window toolbar / Heirs tab (horizontal layout, `layout_type == "h"`)
  the Check Alive is now shown even in BASIC. Also stored `self.layout_type` so
  the per-update hooks can tell the two layouts apart.
- `WillSettingsWidget.apply_user_type_visibility`: in the horizontal layout the
  Check Alive is kept visible regardless of mode; in the wizard it is still
  hidden in BASIC / shown in ADVANCED as before. This keeps the field visible
  in the main window even after toggling BASIC<->ADVANCED at runtime.
- `WillSettingsWidget.apply_editable_dates`: in BASIC mode the Check Alive is
  now forced ALWAYS read-only, independently of the "Panel editable Date and
  Fee" (EDITABLE_DATES) setting. That setting still controls the delivery time
  (locktime) and the fee exactly as before - only the Check Alive is
  force-locked in BASIC. ADVANCED mode is unchanged.
- Date-only display needed no change: BASIC mode already forces every time
  field to the calendar ("Date") editor and hides the Raw/Date selector
  (`BalTimeEditWidget.__init__`, `self._basic_mode`), so the now-visible Check
  Alive is automatically shown as a Date with no Raw option.

**Verification:**

- Full test suite: `266 passed` (unchanged), plus the same 2 pre-existing,
  unrelated failures (stale `baltx_fees=100` expectation vs the v0.5.0 default
  of `20`).
- `ruff`: no new errors (only the pre-existing star-import noise and the
  pre-existing `F841` at widgets.py, both present in clean v0.5.0).
- Only `bal/gui/qt/widgets.py` (this task) and `bal/gui/qt/plugin.py` (the
  v0.5.1 flicker fix) differ from clean v0.5.0; all locktime/date/Check-Alive
  logic files are untouched.

**Outcome:** DONE (delivered as test ZIP v0.5.2; commit only after the owner
confirms the ZIP works, especially that in BASIC the Check Alive is visible,
read-only even with "Panel editable Date and Fee" on, and shown as a Date).

---

## 27. v0.5.3 - BASIC mode: build the will against "now" (not Check Alive) + clearer "cannot build" message

**Date:** 2026-07-04

**Goal (owner request, two related fixes):**

1. In BASIC mode, pressing Check (or rebuilding after a change) could refuse to
   (re)build the will and show "Balance is too low, or CheckAlive is in the
   past. Skipped", even with a healthy balance. Root cause found by tracing the
   real code with the owner: will construction filters heirs by
   `delivery_time > from_locktime`, and `from_locktime` is `date_to_check` = the
   Check Alive. In BASIC the Check Alive is hidden and NOT editable, so when its
   (fixed) default ends up LATER than the delivery time, every heir is excluded
   and the will cannot be built. The Check Alive must never govern construction
   in BASIC.
2. The "Skipped" message itself was misleading: it claimed the Check Alive was
   "in the past", whereas the real blocking condition is the opposite - the
   Check Alive is LATER than the delivery time.

**What changed:**

- `bal/gui/qt/window.py` (`build_will`): the `from_locktime` passed to
  `get_transactions` is now `datetime.now().timestamp()` when
  `is_basic_mode()` is true, instead of `self.date_to_check` (the Check Alive).
  In BASIC an heir is kept as long as its delivery time is in the future,
  independently of the non-editable Check Alive. ADVANCED mode is unchanged
  (still uses `date_to_check`). Deliberately scoped to CONSTRUCTION only:
  `check_will_expired` / `check_amounts` / `is_will_valid` still use
  `date_to_check` exactly as before, keeping the change targeted and low-risk
  (the broad `date_to_check` rewrite tried in the discarded v0.5.4 line is NOT
  reintroduced).
  - Known, owner-accepted limitation: a delivery time equal to "today" (same
    instant) may still not build, because `now()` includes the current time of
    day. The owner will test this edge case separately.
- `bal/gui/qt/dialogs.py`: replaced the misleading build-failure message with
  the owner-approved wording: "Could not build the will. Possible reasons: the
  Check Alive date is later than the delivery time (it must be earlier), the
  balance is too low, or the heirs' shares are below the minimum. Skipped".
  Text only; the message still appears under exactly the same condition
  (`build_will()` returned nothing) as before.

**Verification:**

- Full test suite: `266 passed`, plus the same 2 pre-existing, unrelated
  failures (stale `baltx_fees=100` expectation vs the v0.5.0 default of `20`).
- `ruff`: no new errors (only pre-existing star-import noise and the
  pre-existing `F841` at dialogs.py, both present in clean v0.5.0).
- Windows behaviour (especially the delivery-time-equals-today edge case) to be
  confirmed by the owner from the test ZIP.

**Outcome:** DONE (delivered as test ZIP v0.5.3; commit only after the owner
confirms it works, in particular that in BASIC the will now (re)builds and the
new message is clear).

---

## 28. v0.5.4 - BASIC mode: Check Alive fully ignored at the root (date_to_check = now())

**Date:** 2026-07-04

**Goal (owner-approved, Option B "fix the root"):** After v0.5.3 fixed only the
build path, the owner hit "No Heirs" on Check even with heirs present, in BASIC
AND ADVANCED. A full map of every Check Alive usage (see the analysis
spreadsheet) showed the Check Alive is read in ~11 places, all through the SAME
value `self.date_to_check`, and only some were mode-gated. Fixing them one by
one was whack-a-mole. Option B fixes the single root instead.

**Root cause:** `self.date_to_check` (window.py, init_class_variables) was
ALWAYS set to the Check Alive (threshold), in BASIC and ADVANCED. Every
downstream check reads it: the build filter (get_locktimes/get_transactions),
the heir count in check_willexecutors_and_heirs (source of "No Heirs" when
delivery < Check Alive), check_will_expired, check_amounts, and the
"locktime is lower than threshold" guard. In BASIC the Check Alive is hidden
and not editable, so whenever its fixed value ended up later than the delivery
time, these checks wrongly blocked the will.

**What changed:**

- `bal/gui/qt/window.py` (`init_class_variables`): in BASIC mode
  `self.date_to_check` is now set to `datetime.now().timestamp()` instead of
  the Check Alive. Every downstream check is therefore evaluated against "now"
  (i.e. the Check Alive effectively does not exist in BASIC), while the
  delivery time (locktime) is still fully enforced. ADVANCED mode is unchanged
  and keeps using the user-controlled Check Alive. This single change covers
  every point in the usage map at once.
- `bal/gui/qt/window.py` (`build_will`): removed the now-redundant per-mode
  branch added in v0.5.3 (it computed now() locally for the build filter).
  Since `date_to_check` is already now() in BASIC from the root fix, build_will
  simply passes `self.date_to_check` again - one source of truth, no duplicate
  logic.
- The CheckAliveError "you are alive -> postpone" guard was already skipped in
  BASIC and is unchanged.

**Why this differs from the discarded v0.5.4-era experiments:** the earlier
broken attempt set date_to_check to `locktime - 2h` (odd and fragile). This
sets it to plain `now()`, which is the natural meaning of "the Check Alive does
not apply in BASIC".

**Verification:**

- Full test suite: `266 passed`, plus the same 2 pre-existing, unrelated
  failures (stale `baltx_fees=100` expectation vs the v0.5.0 default of `20`).
- `ruff`: no new errors (only pre-existing star-import noise).
- Windows behaviour to be confirmed by the owner from the test ZIP: in BASIC
  the "No Heirs" / "cannot rebuild" problems should be gone; ADVANCED unchanged.

**Outcome:** DONE (delivered as test ZIP v0.5.4; commit only after the owner
confirms it works in BASIC and that ADVANCED is unaffected).

---

## 29. v0.5.5 - ADVANCED defaults to RAW (1y/30d); Check Alive hidden again in BASIC

**Date:** 2026-07-04

**Goal (owner request, two GUI changes):**

1. In ADVANCED mode, both the Delivery time and the Check Alive fields should
   default to the RAW editor (showing "1y" / "30d") instead of the calendar
   ("Date") editor.
2. In BASIC mode, hide the Check Alive field again everywhere (revert the
   v0.5.2 experiment that showed it read-only in the main window).

**What changed (all in `bal/gui/qt/widgets.py`, GUI only):**

- `BalTimeEditWidget.__init__` (default editor selection): in ADVANCED mode the
  default editor is now forced to RAW (index 0) for both fields (Delivery time
  and Check Alive both derive from this class). BASIC mode still forces the
  Date editor (index 1) and hides the Raw/Date selector, as before. Because the
  relative defaults already exist in plugin_base
  (`default_will_settings_relative` -> locktime "1y", threshold "30d"), the RAW
  editor opens showing "1y" / "30d". If the stored value happens to be an
  absolute timestamp, the forced-RAW path substitutes the relative default so
  the field never shows a bare timestamp number. The user can still switch to
  Date manually (the selector is visible in ADVANCED); only the initial choice
  changed.
- `WillSettingsWidget.__init__` and `apply_user_type_visibility`: the Check
  Alive (threshold) row is hidden in BASIC in BOTH layouts again (main window
  and wizard), reverting the v0.5.2 "visible read-only in the main window"
  behaviour.
- `apply_editable_dates`: reverted the v0.5.2 threshold read-only special-case
  back to the original single line (no longer needed now that the Check Alive
  is hidden in BASIC).

**Verification:**

- Full test suite: `266 passed`, plus the same 2 pre-existing, unrelated
  failures (stale `baltx_fees=100` expectation vs the v0.5.0 default of `20`).
- `ruff`: no new errors (only pre-existing star-import noise).
- Windows behaviour to be confirmed by the owner: in ADVANCED both fields open
  in RAW showing 1y/30d; in BASIC the Check Alive is no longer visible anywhere.

**Outcome:** DONE (delivered as test ZIP v0.5.5; commit only after the owner
confirms it works).

---

## 30. v0.5.6 - Five small UX fixes (tooltips, empty-ics warning, revert forced RAW, message reword, Check Alive red highlight)

**Date:** 2026-07-04

**Goal (owner request, five items in one release):**

1. Add a "Local time (not UTC)" note to the Delivery time AND Check Alive
   tooltips.
2. When the calendar export has no reminder events (delivery date too close or
   already passed), warn the user and do NOT create an empty .ics file.
3. Revert the v0.5.5 "always RAW in ADVANCED" default back to the 0.5.0
   behaviour (default editor follows the stored value's format).
4. Reword "Inheritance already executed (on blockchain)" to "An inheritance of
   this wallet is already executed (on blockchain)".
5. Highlight the Check Alive box with a soft red when it is later than the
   delivery time (ADVANCED only).

**What changed:**

- `bal/gui/qt/widgets.py`:
  - `ThresholdTimeWidget.help_text` and `LockTimeWidget.help_text`: appended a
    note that the date/time is in the computer's Local time, not UTC (#1).
  - `BalTimeEditWidget.__init__`: removed the v0.5.5 forced-RAW-in-ADVANCED
    block; the default editor again follows the stored value (absolute number
    -> Date, relative "1y"/"30d" -> Raw). BASIC still forces the Date editor
    and hides the selector (#3).
  - `WillSettingsWidget.on_locktime_change`: if the Check Alive timestamp is
    >= the delivery time, tint the Check Alive box soft red (#FCE4E4, border
    #E9A8A8) with an explanatory tooltip; clear it when the relationship is
    valid. ADVANCED only (the field is hidden in BASIC) (#5).
- `bal/gui/qt/dialogs.py`:
  - `_ics_provider`: returns None when there are no reminder events (instead of
    a header-only .ics), so the caller can warn (#2).
  - Reworded the "inheritance already executed" message (#4).
- `bal/gui/qt/calendar.py`:
  - `_ensure_ics`: when the provider returns no content, show the warning
    "No reminders were saved: the delivery date is too close (or already
    passed)" and create no file (#2).

**Verification:**

- Full test suite: `266 passed`, plus the same 2 pre-existing, unrelated
  failures (stale `baltx_fees=100` expectation vs the v0.5.0 default of `20`).
- `ruff`: no new errors (only pre-existing star-import noise and a pre-existing
  E401 in calendar.py, present in clean v0.5.0).
- Manual check: `_ics_provider` returns events for a far delivery date and None
  for a delivery date of "today" (0 days), confirming the empty-events warning
  path.
- Windows/visual confirmation (soft-red tint, tooltips, calendar warning) to be
  done by the owner from the test ZIP.

**Outcome:** DONE (delivered as test ZIP v0.5.6; commit only after the owner
confirms it works).

---

## 31. v0.5.7 - Consistent Raw/Date default per mode, including runtime switch

**Date:** 2026-07-04

**Goal (owner request):** Make the Raw/Date editor default simple and
consistent, both at startup and when toggling BASIC<->ADVANCED at runtime:
  * BASIC    -> always the Date (calendar) editor, selector hidden.
  * ADVANCED -> RAW by default (showing "1y"/"30d"), selector visible so the
               user can switch to Date manually; a manual Date choice must be
               respected on later runtime mode switches (not forced back to RAW).

**Background:** v0.5.6 (ToDo #3) had reverted to the value-driven default and
left the runtime switch untouched, so switching ADVANCED->BASIC at runtime could
leave a field stuck on RAW while the selector was hidden (and BASIC->ADVANCED
did not restore RAW). This makes the behaviour explicit and symmetric.

**What changed (all in `bal/gui/qt/widgets.py`):**

- `BalTimeEditWidget.__init__`: default editor is now chosen by MODE, not by the
  stored value's format: BASIC -> Date (index 1); ADVANCED -> RAW (index 0). In
  ADVANCED, if the stored value is an absolute timestamp, the relative default
  ("1y"/"30d") is loaded into the RAW editor so it never shows a bare number.
- Added `self._user_picked_editor` flag plus `_on_user_picked_editor`, connected
  to the combo's `activated` signal (fires only on real user interaction, not on
  programmatic index changes). This records a manual Raw/Date choice.
- `apply_user_type_visibility`: besides showing/hiding the selector, it now sets
  the active editor on a runtime switch: BASIC -> always Date; ADVANCED -> RAW
  unless the user had manually picked an editor, in which case their choice is
  left untouched. Wrapped in try/except so a cosmetic editor switch can never
  break mode toggling.

**Verification:**

- Full test suite: `266 passed`, plus the same 2 pre-existing, unrelated
  failures (stale `baltx_fees=100` expectation vs the v0.5.0 default of `20`).
- `ruff`: no new errors (only pre-existing star-import / E401 noise).
- Windows/interactive confirmation (startup defaults + runtime BASIC<->ADVANCED
  switch, including a manual Date choice being respected) to be done by the
  owner from the test ZIP.

**Outcome:** DONE (delivered as test ZIP v0.5.7; commit only after the owner
confirms it works).

---

## 32. v0.5.8 - Fix: Check Alive red highlight no longer hides the up/down arrows

**Date:** 2026-07-04

**Problem (owner-reported, screenshot):** the soft-red highlight added in v0.5.6
(ToDo #5) made the Check Alive box lose its native up/down spin arrows. Cause:
the stylesheet set a `border` on the whole container; styling the border of a
QDateTimeEdit / QAbstractSpinBox via stylesheet makes Qt stop drawing its native
sub-controls (the spin arrows).

**Fix (`bal/gui/qt/widgets.py`, `on_locktime_change`):** the warning styling now
sets ONLY a `background-color` (no border), and scopes the rule to the inner
editor widget types (`QDateTimeEdit, QLineEdit, QAbstractSpinBox`) instead of the
whole container, so the tint still shows but the native up/down arrows are
preserved.

**Verification:**
- Full test suite: `266 passed` (same 2 pre-existing unrelated failures).
- `ruff`: no new errors.
- Visual confirmation (arrows present + soft-red tint) to be done by the owner
  from the test ZIP.

**Outcome:** DONE (delivered as test ZIP v0.5.8; commit only after confirmation).

---

## 33. v0.5.9 - Clearer 3-point "could not build the will" message

**Date:** 2026-07-04

**Goal (owner request):** Now that the Check Alive box turns soft red when it is
later than the delivery time (v0.5.6/v0.5.8), that mistake is hard to make, so
the build-failure message was rewritten as a clearer, numbered 3-point list.

**What changed (`bal/gui/qt/dialogs.py`, text only):** the message shown when
`build_will()` returns nothing is now:

    Could not build the will ! Possible reasons:
    1- the Balance of wallet is too low to cover the fees for miners and will executors,
    2- the Heirs' shares are below the minimum (Dust UTXO, less than 546 Satoshi),
    3- the Check Alive Date/Time is later than the delivery time (it must be earlier),
    Skipped

Line breaks use "\n", which the message panel converts to <br> (see the render
that joins labels with <br><br> and replaces \n), so the three points appear on
separate lines. Same trigger condition and warning colour as before; text only.

**Verification:**
- Full test suite: `266 passed` (same 2 pre-existing unrelated failures).
- `ruff`: no new errors.
- Visual confirmation of the multi-line layout to be done by the owner.

**Outcome:** DONE (delivered as test ZIP v0.5.9; commit only after confirmation).

---

## 34. v0.5.10 - Pressing CHECK no longer resets a manual Date/RAW choice (Opzione 2)

**Date:** 2026-07-04

**Problem (owner-reported):** In ADVANCED, manually setting the Check Alive and
Delivery time boxes to Date and then pressing CHECK snapped them back to RAW.

**Cause:** v0.5.7 put the per-mode editor default (BASIC->Date, ADVANCED->RAW)
inside `BalTimeEditWidget.apply_user_type_visibility`, which is invoked from
`BalWindow.update_all()` - and update_all() also runs on every CHECK/refresh, not
only on a real USER TYPE change. So CHECK re-applied the RAW default.

**Fix (Opzione 2 - separate the two concerns):**

- `bal/gui/qt/widgets.py`:
  - `BalTimeEditWidget.apply_user_type_visibility`: reverted to flipping ONLY the
    Raw/Date combo visibility (no editor change), so it is side-effect free on
    every refresh/CHECK.
  - New `BalTimeEditWidget.apply_user_type_editor_default`: holds the per-mode
    editor default (BASIC->Date; ADVANCED->RAW unless the user picked manually).
  - New `WillSettingsWidget.apply_user_type_editor_default`: applies it to both
    the locktime and threshold boxes.
- `bal/gui/qt/plugin.py`:
  - New `_apply_editor_default_on_toolbars()` helper that reaches the WILL/HEIR
    toolbars of every open window and applies the editor default.
  - `on_user_type_change` now calls it ONLY on a real USER TYPE change (both the
    normal switch and the revert-to-BASIC path), right before `update_all()`.

**Result:** CHECK no longer touches the active editor (the transaction-list
refresh in update_all() at `will_list_widget.update_will(...)` is untouched, so
the list still updates on CHECK). The per-mode default is applied only when the
user actually switches BASIC<->ADVANCED, and a manual Date choice is preserved.

**Verification:**
- Full test suite: `266 passed` (same 2 pre-existing unrelated failures).
- `ruff`: no new errors.
- Interactive confirmation (Date stays on CHECK; switch still applies defaults;
  transaction list still refreshes) to be done by the owner from the test ZIP.

**Outcome:** DONE (delivered as test ZIP v0.5.10; commit only after confirmation).

---

## 35. v0.5.11 - Electrum 4.8.0 compatibility (json_db.register_dict removed)

**Date:** 2026-07-05

**Problem (owner-reported):** With Electrum 4.8.0 the plugin fails to load:

    AttributeError: module 'electrum.json_db' has no attribute 'register_dict'
    Exception: Error loading bal plugin: ...

**Root cause (verified against the official Electrum sources):** Electrum 4.8.0
REMOVED `json_db.register_dict` and moved the wallet-DB registration API to a new
module, `electrum.stored_dict`, switching from flat key names to '/'-separated
paths with a wildcard, and swapping the last two arguments:

    Electrum <= 4.7.2 : json_db.register_dict(NAME, method, _type)
    Electrum >= 4.8.0 : stored_dict.register_name('/NAME/*', _type, method)

Electrum migrated its own registrations the same way (e.g. `contacts`). The
conversion semantics are IDENTICAL in both versions (`_type is dict` ->
`method(**value)`, `_type is tuple` -> `method(*value)`, else `method(value)`);
only the registration API changed. `electrum.stored_dict` does not exist in
4.7.2, and `register_dict` no longer exists in 4.8.0, so a plain port would have
broken users still on 4.7.2.

**Fix (`bal/core/plugin_base.py`) - compatibility shim (owner's choice):** a
`_register_will_dict()` helper detects which API is available (`try: from
electrum.stored_dict import register_name` / `except ImportError:` fall back to
`json_db.register_dict`) and registers `heirs`, `will` and `will_settings`
through it. The plugin therefore works on BOTH Electrum 4.7.2 and 4.8.0.
`bal/__init__.py`: module docstring updated accordingly (text only).

**Verification:**
- Full test suite against **Electrum 4.7.2**: `266 passed` (same 2 pre-existing,
  unrelated `baltx_fees` failures).
- Full test suite against **Electrum 4.8.0**: `266 passed` (same 2 pre-existing
  failures) - previously ALL tests failed at collection with the AttributeError.
- Direct check: `heirs`, `will`, `will_settings` are confirmed REGISTERED in
  Electrum 4.7.2 (`json_db.registered_dicts`) and in Electrum 4.8.0
  (`stored_dict.registered_names`).
- `ruff`: all checks passed.

**Caveat:** the test suite covers the plugin logic, not the whole Qt GUI. Electrum
4.8.0 changed other things too, so further incompatibilities may only surface when
actually running the plugin; owner to confirm on Windows with the test ZIP.

**Outcome:** DONE (delivered as test ZIP v0.5.11; commit only after the owner
confirms the plugin loads and works on Electrum 4.8.0).

---

## 36. v0.5.12 - Fix: Check Alive soft-red tint could stay stuck (and used the wrong dates)

**Date:** 2026-07-14

**Problem (owner-reported, screenshot):** In ADVANCED the Check Alive box stayed
pink permanently even with perfectly valid dates (Check Alive 13/10/2026,
delivery 14/07/2030), and changing the values did not clear it.

**Two distinct bugs were found:**

1. **Stale tint (the actual cause of the stuck pink).** The tint was only
   refreshed from `on_locktime_change`, which is connected to `valueEdited` of
   the fields of THAT WillSettingsWidget copy. The main-window toolbar is
   read-only, so its fields never emit `valueEdited`: values reach it through
   `update_setting_widgets` -> `set_value`, and its tint was therefore never
   re-evaluated. A pink set earlier stayed frozen forever.
2. **Wrong comparison basis (real bug, wrong tint decisions).** The check used
   `BalTimestamp.to_timestamp()` with no base, which resolves relative values
   from *now* ("90d" -> today + 90 days). But the plugin's REAL Check Alive date
   is computed BACKWARDS from the delivery time
   (`to_date(min_locktime, reverse=True)`) and stored as
   `will_settings["real_threshold"]`. So the tint decision was made on values
   that are neither the ones displayed nor the ones the plugin uses. Example:
   Check Alive "5y" with delivery "4y" was wrongly tinted, although the real
   Check Alive (delivery - 5y) is BEFORE the delivery and thus valid.

**Fix (`bal/gui/qt/widgets.py`):**

- `on_locktime_change` now keeps the REAL resolved dates it already computes
  (`real_threshold_dt`, `real_locktime_dt` - the same values shown to the user
  and stored as `real_threshold` / `real_locktime`) and passes them to the tint.
- The tint logic moved into a new `WillSettingsWidget.update_check_alive_warning()`
  which compares those REAL dates, and can also recompute from
  `will_settings["real_threshold"/"real_locktime"]` when called without arguments.
- `WillSettingsWidget.apply_user_type_visibility` (invoked by `update_all()`, i.e.
  on every refresh) now also calls `update_check_alive_warning()`, so a stale tint
  can no longer stay stuck on a toolbar copy that never received the edit signal.
- Unchanged: ADVANCED-only, soft red #FCE4E4 background only (no border, so the
  native up/down arrows stay visible), tooltip, and cleared as soon as valid.

**Verification:**
- Full test suite against **Electrum 4.7.2**: `266 passed` (same 2 pre-existing
  unrelated failures). Against **Electrum 4.8.0**: `266 passed` (same 2).
- `ruff`: no new errors.
- Logic check: with the owner's values the tint clears; with Check Alive "5y" vs
  delivery "4y" the old code wrongly tinted and the new code does not; a Check
  Alive genuinely later than the delivery is still correctly tinted.

**Outcome:** DONE (delivered as test ZIP v0.5.12; commit only after confirmation).

---

## 37. v0.5.13 - Remove the Check Alive soft-red highlight

**Date:** 2026-07-14

**Goal (owner request):** Remove the soft-red tint on the Check Alive box
entirely. Introduced in v0.5.6 and patched twice (v0.5.8 for the disappearing
spin arrows, v0.5.12 for a stuck/incorrect tint), it kept causing problems and
the owner decided it is not worth keeping. No replacement warning was requested.

**What changed (`bal/gui/qt/widgets.py`):**

- Removed `WillSettingsWidget.update_check_alive_warning()` in full (the tint
  logic, the soft-red stylesheet and the "The Check Alive date must be earlier
  than the delivery time." tooltip).
- Removed its call from `on_locktime_change`, which is back to its pre-v0.5.6
  form: it still resolves and stores `real_threshold` / `real_locktime` exactly
  as before (those are used by the rest of the plugin).
- Removed its call from `apply_user_type_visibility` (the per-refresh
  re-evaluation added in v0.5.12, which existed only for the tint).
- No stylesheet is applied to the Check Alive box anymore, so it always renders
  with the normal Qt look (and its native up/down arrows).

**Kept:** the user is still warned when it matters - the build-failure message
lists "the Check Alive Date/Time is later than the delivery time (it must be
earlier)" as one of the three possible reasons (v0.5.9).

**Verification:**
- No remnants of the tint left in the codebase (grep clean).
- Full test suite against **Electrum 4.7.2**: `266 passed`; against **Electrum
  4.8.0**: `266 passed` (same 2 pre-existing, unrelated failures in both).
- `ruff`: no new errors.

**Outcome:** DONE (delivered as test ZIP v0.5.13; commit only after confirmation).

---

## 38. v0.5.14 - Shorten long Tor (.onion) will-executor URLs in the list

**Date:** 2026-07-15

**Goal (owner request):** In the will-executor list, a Tor (.onion) URL is very
long and overflows the URL column. Show it shortened (like the welist site at
welist.bitcoin-after.life), keeping the full address available.

**What changed (`bal/gui/qt/lists.py`, display only):**

- `WillExecutorListWidget.update()`: the URL column now displays a shortened
  form for long addresses - the first 37 characters + an ellipsis (matching the
  welist site) - while the FULL url is still stored as the row's key data and is
  set as the cell tooltip (so hovering shows the complete address). URLs of 40
  characters or fewer are shown unchanged.
- New `_FullUrlEditDelegate` installed on the URL column: when the user
  double-clicks the URL cell to edit it, the editor is preloaded with the FULL
  url (from the key role) instead of the shortened display text. Without this,
  editing a shortened URL would have saved the truncated text and corrupted the
  will-executor key. Verified against Electrum's edit-commit path
  (my_treeview on_commitData -> on_edited): the committed text is the editor's
  text (now the full URL) and the edit key is read from the role, so an
  unmodified URL round-trips intact.
- Only the URL column's presentation/editor changed; download, ping and
  connection logic are untouched.

**Verification:**
- Full test suite against **Electrum 4.7.2**: `266 passed`; against **Electrum
  4.8.0**: `266 passed` (same 2 pre-existing, unrelated failures in both).
- `ruff`: no new errors.
- Shortening check: the 69-char sample .onion becomes a 38-char display
  ("http://gxvdgcqdy2s7x5cujmkaqua72r2aqo..."), while short https URLs are shown
  unchanged.

**Note:** the separate ~5s delay observed when contacting the .onion server is
inherent to Tor building a circuit to the hidden service, not a plugin bug; it
was analysed but intentionally NOT changed here.

**Outcome:** DONE (delivered as test ZIP v0.5.14; commit only after confirmation).

---

## 39. v0.5.15 - Fix KeyError on delete/select/ping of shortened .onion will-executors

**Date:** 2026-07-15

**Problem (owner-reported):** After v0.5.14 shortened long .onion URLs in the
will-executor list, deleting (or selecting/deselecting/pinging) a server raised:

    KeyError: 'http://gxvdgcqdy2s7x5cujmkaqua72r2aqo...'

**Cause:** `WillExecutorListWidget.create_menu` collected the selected key with
`item.data(0)`, which returns the DISPLAY text - now the SHORTENED URL (with the
ellipsis). That truncated string was then used as a dict key in
delete/select/deselect/ping, none of which exist in `willexecutors_list` (keyed
by the full URL) -> KeyError. This was a regression introduced by v0.5.14.

**Fix (`bal/gui/qt/lists.py`, `create_menu`):** the selected key is now read from
the real key role (`ROLE_HEIR_KEY + Columns.URL`), which always holds the full
URL - the same mechanism already used by `get_edit_key_from_coordinate` and
`on_double_click` - with a fallback to `item.data(0)` only if the role is
missing. So the context-menu actions operate on the full URL again.

**Verification:**
- Full test suite against **Electrum 4.7.2**: `266 passed`; against **Electrum
  4.8.0**: `266 passed` (same 2 pre-existing, unrelated failures in both).
- `ruff`: no new errors.
- Checked there are no other `.data(0)` key usages left in the will-executor
  list (the remaining ones are in the heir/will lists, which do not shorten
  URLs).

**Outcome:** DONE (delivered as test ZIP v0.5.15; commit only after confirmation).

---

## 40. v0.5.16 - Skip Tor (.onion) will-executors from download when Electrum is not on Tor

**Date:** 2026-07-16

**Goal (owner request):** A .onion will-executor is only reachable through Tor.
Users connected via Tor should get the .onion servers in the welist download;
users NOT on Tor should NOT download them at all - contacting them would only
waste time (they are unreachable). No user-facing option: the behaviour is
driven solely by whether Electrum is connected through Tor.

**What changed:**
- `bal/core/willexecutors.py`: new helpers `is_onion_url(url)` (detects .onion
  hosts) and `is_tor_active()` (True only when `Network.is_proxy_tor is True`;
  False/None => not on Tor; read defensively for offline mode). Present in both
  Electrum 4.7.2 and 4.8.0.
- `bal/gui/qt/window.py` (`fetch_will_executors_list`): when Tor is NOT active,
  .onion servers are dropped from the downloaded list (not initialised, not
  shown). When Tor is active, all servers are kept as before.
- `bal/gui/qt/common.py`: re-export the two helpers for the Qt layer.

**Note:** this replaces an earlier, more complex experiment (a "Force onion"
setting plus a non-selectable "Tor off" row) that was never committed to Gitea;
the final behaviour is the simple Tor-driven filter described above.

**Verification:**
- Full test suite against **Electrum 4.7.2**: `266 passed`; against **Electrum
  4.8.0**: `266 passed` (same 2 pre-existing, unrelated failures in both).
- `ruff`: no new errors.
- Filter check: with Tor off the .onion server is removed from the list and only
  the https servers remain; with Tor on the .onion server is kept.

**Caveat:** the test suite does not simulate a real Tor connection, so the
decisive confirmation (with Electrum on Tor = celeste dot vs off = green dot)
must be done by the owner on Windows.

**Outcome:** DONE (delivered as test ZIP v0.5.16; commit only after confirmation).

---

## 41. v0.5.17 - Fix crash when the welist server returns a non-dict response

**Date:** 2026-07-16

**Problem (owner-reported, Electrum 4.8.0, Tor active):** pressing Download List
could crash with:

    ValueError: dictionary update sequence element #0 has length 1; 2 is required
    at window.py on_success -> self.willexecutors.update(result)

**Root cause:** the will-executor list download assumed the server always
returns a dict of {url: info}. `handle_response` does `json.loads(...)` and, when
the body is NOT valid JSON (e.g. an error/HTML/plain-text page - more likely over
Tor when a server misbehaves), it returns the RAW STRING instead. That string
then reached `self.willexecutors.update(result)`, and `dict.update("...")`
iterates the string characterwise, raising the ValueError. The bug was latent
(not introduced by the onion filter) and surfaced with Tor active.

**Fix (`bal/gui/qt/window.py`):**
- `fetch_will_executors_list`: only accept the response if it is a `dict`;
  otherwise log it, record "invalid response format" and try the next candidate
  (so the normal "download failed" message is shown instead of crashing). Also,
  each entry is now checked to be a dict before use (malformed entries skipped).
- `on_success`: second line of defense - only `self.willexecutors.update(result)`
  when `result` is a non-empty dict; otherwise show the clean download-failed
  warning. A non-dict can no longer crash the callback.

**Verification:**
- Anti-crash check: on_success with a string, a list or an empty dict shows the
  warning without crashing; a valid dict updates normally.
- Full test suite against **Electrum 4.7.2**: `266 passed`; against **Electrum
  4.8.0**: `266 passed` (same 2 pre-existing, unrelated failures in both).
- `ruff`: no new errors.

**Outcome:** DONE (delivered as test ZIP v0.5.17; commit only after confirmation).

---

## 42. v0.5.18 - Clearer message when the list download fails/times out over Tor

**Date:** 2026-07-16

**Context (owner-reported):** With Electrum connected through Tor, "Download
List" could sit until the timeout and download nothing, while the exact same
build worked fine without Tor (or when the user's exit IP changed via VPN). This
is not a plugin bug: it is a slow/obstructed Tor path on the user's connection.
The request was to make the failure message clearer (no timeout change).

**What changed (`bal/gui/qt/window.py`, text/UX only):**
- New `DOWNLOAD_FAILED_TOR_MESSAGE`: "Could not download the will-executors list
  over Tor. ... Your Tor connection may be slow. Please try again, or use a VPN
  (or temporarily disable Tor) for a faster connection."
- Both failure paths (`on_success` with an empty/invalid result, and the generic
  branch of `on_failure`) now show the Tor-specific message when
  `is_tor_active()` is true, and the existing generic message otherwise.
- The download timeout itself is unchanged.

**Verification:**
- Full test suite against **Electrum 4.7.2**: `266 passed`; against **Electrum
  4.8.0**: `266 passed` (same 2 pre-existing, unrelated failures in both).
- `ruff`: no new errors.

**Outcome:** DONE (delivered as test ZIP v0.5.18; commit only after confirmation).

---

## 43. v0.6.0 - Version bump for the official repository release

**Date:** 2026-07-17

**Context:** the plugin content of this version is IDENTICAL to v0.5.18 - no code
changes. A release was published on the official repository
(bitcoinafterlife/bal-electrum-plugin) tagged "v0.6.0", but the internal version
files still read "0.5.18" (a human oversight: the release tag was not matched by
a version bump in the code), so Electrum displayed "0.5.18" after installing it.
This entry aligns the internal version with the intended "0.6.0" release tag by
bumping `bal/VERSION`, `bal/manifest.json`, `bal/__init__.py` and
`bal/core/plugin_base.py` from 0.5.18 to 0.6.0. No functional changes.

**Verification:** full test suite against Electrum 4.7.2 and 4.8.0 (same 266
passed / 2 pre-existing unrelated failures as v0.5.18); `ruff` clean.

**Outcome:** DONE.

---

## 44. Repo: inheritance-logic test suite + first BAL CLI (no plugin version change)

**Date:** 2026-07-20

**Scope note:** this entry adds REPOSITORY files only (`tests/`, `bal_cli.py`).
The plugin package `bal/` is byte-for-byte unchanged, so the plugin version
stays **0.6.0** on purpose (no zip rebuild, no version bump - avoiding any new
tag/code mismatch).

**1) New logic test suite - `tests/test_inheritance_scenarios.py` (23 tests):**
- Multi-heir distribution via `Heirs.prepare_lists`: 50/50 percent split of
  (balance - fees); percentages normalized to their SUM (30+30 behaves like
  50/50); fixed heirs paid first with percents sharing the remainder; fixed
  amounts exceeding the balance scaled down proportionally (`onlyfixed`).
- Dust rules: below-dust heirs marked `DUST:` while valid heirs keep building;
  the leftover redistribution can LIFT dust fixed heirs above the threshold
  (pinned: 100:200 on 1M -> 333333/666666); all-dust wills refused
  (`HeirAmountIsDustException`); `BalanceTooLowException` when balance < fees.
- Will-executor fees: one pseudo-heir per distinct locktime; fees larger than
  the balance raise `WillExecutorFeeException`.
- Expired-heir exclusion vs `from_locktime`; grouping of heirs by locktime.
- Heir add/remove/locktime-change all flag the will as changed
  (`Will._same_heirs`); the `Heirs` mapping persists on add/remove.
- Will expiry (`Will.check_will_expired`): past locktime raises
  `WillExpiredException`, future/boundary (== now) does not, non-VALID items
  are ignored.
- Locktime parsing: int passthrough, `<n>d` -> future midnight, `1y` == `365d`.

**2) First BAL CLI - `bal_cli.py` (offline iteration):**
- Reuses `bal.core` directly with Electrum as a library (no Qt, headless).
- Commands: `heirs list/add/remove/export/import`, `status`.
- Safety: **testnet by default**, mainnet only with an explicit `--mainnet`
  flag (active network always printed); encrypted wallets via `--password` or
  `BAL_WALLET_PASSWORD` env var; address/locktime validation (rejects wrong
  network and past locktimes); guaranteed process termination (electrum leaves
  non-daemon threads even offline - the CLI stops the wallet and event loop,
  then hard-exits with the proper exit code, so machine callers never hang).
- Planned next iteration: `will build/sign/push/check` with an explicit
  `--yes` confirmation flag for automation.
- End-to-end smoke test `tests/test_cli_smoke.py`: creates a REAL testnet
  wallet via electrum-as-library in a subprocess, then drives
  add/list/reject-invalid-address/reject-past-locktime/export/remove/status.

**Verification:**
- Full suite against **Electrum 4.7.2**: `290 passed`; against **4.8.0**:
  `290 passed` (same 2 pre-existing, unrelated `baltx_fees` failures in both).
- `ruff`: no new errors on the three new files.

**Outcome:** DONE.

---

## 45. Repo: CLI will commands (build / sign / push / check) - no plugin version change

**Date:** 2026-07-21

**Goal:** complete the headless workflow started in entry #44, so a machine can
run the whole inheritance cycle without the Qt GUI.

**What was added (`bal_cli.py`):**
- `will build` - builds the will transactions offline through the same
  `bal.core` pipeline the GUI uses (`Heirs.get_transactions` -> `WillItem` ->
  `Will.update_will` / `normalize_will`), storing them as "New".
- `will sign` - signs the valid, incomplete transactions with the wallet
  password (`--password` / `BAL_WALLET_PASSWORD`). Prints a summary and asks
  for confirmation; `--yes` skips it for automation. Chained will inputs
  (spending a previous will's change) are patched exactly as the GUI does.
  Watching-only wallets and hardware keystores are rejected with a clear
  message (hardware devices need physical confirmation).
- `will push` - sends signed transactions to the selected will-executors via
  `Willexecutors.push_transactions_parallel`; `--yes` and `--force` supported;
  updates PUSHED / PUSH_FAIL statuses.
- `will check` - read-only report with **exit codes for scripts**: 0 valid,
  complete and pushed; 2 no will stored; 3 EXPIRED; 4 not fully signed;
  5 signed but not pushed; 6 heirs changed since the will was built.
- `_CliBalPlugin`: minimal stand-in exposing only what `bal.core` needs
  headless (`WILLEXECUTORS`, `NO_WILLEXECUTOR`, `get_decimal_point`).

**Three real bugs found and fixed while implementing this:**
1. `Will.only_valid()` returns a GENERATOR - `len()` on it raised
   `TypeError`; now wrapped in `list()`.
2. `copy.deepcopy(tx)` fails on Electrum 4.8 (`cannot pickle '_thread.RLock'`);
   signing now re-parses the transaction from its serialization instead.
3. **Silent will loss on save**: values loaded from the wallet DB are
   `StoredDict`s carrying the DB lock, and `JsonDB.put` deep-copies its value
   and returns False *silently* when that fails - the will appeared saved but
   was never written. Persistence now normalizes through a
   `json.dumps`/`loads` round-trip (which also validates serializability) and
   exits with an explicit error if anything is non-serializable.

Also added: pre-build validation of the selected executor addresses for the
active network (refreshed from the server when possible), and a guard that
catches exception sentinels embedded by `heirs.py` in a heir entry when an
output cannot be built.

**Verification:**
- New `test_cli_will_cycle` in `tests/test_cli_smoke.py`: creates a REAL
  testnet wallet, funds it offline with a handmade transaction, then drives
  `build -> check(4) -> sign --yes -> check(5) -> push (aborted at the
  confirmation prompt) -> status`. No network access.
- Full suite: **291 passed** against **Electrum 4.7.2** and **4.8.0** (same 2
  pre-existing, unrelated `baltx_fees` failures in both).
- `ruff`: all checks passed.

**Note:** repository-only change (CLI + tests). The plugin version is
unchanged; the Qt plugin code is untouched.

**Outcome:** DONE.

---

## 46. v0.6.1 - Version read from manifest.json (single source of truth) - PR #4

**Date:** 2026-07-22

**Goal (Truman):** stop keeping the version in four places (`bal/VERSION`,
`bal/manifest.json`, `bal/__init__.py`, `bal/core/plugin_base.py`) synced by a
pre-commit hook. The version must be read at runtime from `manifest.json` only.

**What changed:**
- `bal/core/plugin_base.py`: new module function `get_version()` reads the
  `"version"` field from `bal/manifest.json` using `importlib.resources`
  (zip-safe: works both extracted and from inside a zip, which is how Electrum
  loads external plugins via `zipimport`; no hand-built paths, so no Windows
  backslash-in-zip issue). The value is cached. `BalPlugin` now exposes a
  `version` **property** returning `get_version()`; the hardcoded
  `__version__` class attribute and the old `version()` method that read the
  `VERSION` file are removed.
- The three call sites that printed the version now use the property
  (`self.bal_window.bal_plugin.version` in `bal/gui/qt/widgets.py` and
  `bal/gui/qt/dialogs.py`) or `get_version()` where no plugin instance is
  available (`bal/core/willexecutors.py`, the HTTP user-agent, a static
  method).
- **Removed the `bal/VERSION` file** (the plugin package now ships 36 files
  instead of 37).
- `HANDOFF.md`: updated the four references that told maintainers to edit
  `bal/VERSION` / keep four files in sync - now they point to
  `manifest.json` as the single source of truth. (Historical version
  references in `CHANGELOG.md` and `.agent_memory_tasks.md` are left intact on
  purpose - they describe past events.)

**Verification:**
- New `tests/test_version_source.py` (7 tests): version equals the manifest
  field; the `version` property matches `get_version()`; no hardcoded
  `__version__` remains; no `bal/VERSION` file remains; the value is cached;
  and - the key one - the version is read correctly when `bal` is imported
  FROM INSIDE A ZIP in a child interpreter (the real Electrum/Windows
  scenario).
- Full suite: **298 passed** against **Electrum 4.7.2** and **4.8.0** (same 2
  pre-existing, unrelated `baltx_fees` failures in both).
- `ruff`: clean.
- Also verified the built distribution zip reports the manifest version via
  `get_version()` (bumped to 0.6.1 in this change).

**Note:** repository change targeting PR #4 (branch
`bitcoinafterlife-patch-5`). No functional change to inheritance behavior.

**Outcome:** DONE.

---

## 47. Willexecutors: fee-bound `is_valid` (extremes allowed), `is_selected` flag-only

**Date:** 2026-08-01

**Goal:** separate the "is this executor selected" question from the "is its fee
acceptable" question, and let the fee bounds be inclusive at both extremes so an
executor whose fee is *exactly* dust or *exactly* the max fee is valid.

**What changed (`bal/core/willexecutors.py`):**
- `is_selected()` no longer takes a `max_fee` parameter - it only reads/sets the
  `"selected"` flag. The previous max-fee cut-off inside `is_selected` rejected
  executors with `base_fee >= max_fee` even when they were otherwise valid.
- `is_valid()` bounds changed from strict (`<=`) to inclusive (`<` / `>`):
  - `base_fee < dust` is invalid (was `<= dust`), so a fee exactly equal to
    dust is now valid.
  - `base_fee > max_fee` is invalid (was `>= max_fee`), so a fee exactly equal
    to the max fee is now valid.
- Defensive `int(base_fee or 0)` in the fee-range import validation so a missing
  or empty `base_fee` no longer raises.

**Verification:**
- New tests in `tests/test_no_willexecutor_karen7.py` (class
  `TestNoWillexecutorKaren7`): `is_selected` only reads/sets the flag (setter,
  default-False); `is_valid` accepts fee == max and fee == dust, rejects
  fee > max and fee < dust, and requires a valid address.
- Existing `build_will` test confirms a fee above max still raises.

**Outcome:** DONE.

---

## 48. Fix `merge_will` crash on missing `date_to_check`

**Date:** 2026-08-01

**Goal:** allow *Will → Merge → Import file* to work as the very first action
of a session, without requiring the normal wizard flow to have run first.

**What changed (`bal/gui/qt/window.py`):**
- `merge_will()` crashed with `AttributeError` when `self.date_to_check` was not
  yet set (merge-from-file can run before `init_class_variables()`). It now
  falls back to `datetime.now().timestamp()` when the attribute is missing or
  `None`, so the local validity check and the trailing `update_all()` always
  have a reference timestamp.
- Several `log_error(exec_info, self.bal_window)` calls inside `BalWindow`
  methods (broadcast failure, will-building failure) pointed at a wrong object
  (`self.bal_window` does not exist on a `BalWindow`); corrected to
  `log_error(exec_info, self)` so the error-reporting path itself cannot fail.

**Verification:**
- New tests in `tests/test_import_will_details.py`:
  `test_merge_will_missing_date_to_check_defaults_to_now` and
  `test_merge_will_validity_error_logs_without_crashing`.

**Outcome:** DONE.

---

## 49. OP_RETURN support for heirs

**Date:** 2026-07-30

**Goal:** allow heirs to produce an OP_RETURN output instead of a regular BTC
payment. An address prefixed with `OP_RETURN:` carries hex data (max 80 bytes);
such heirs always have amount 0 and are excluded from the normal amount
calculations (percentage normalization, dust checks, leftover redistribution).

**What changed:**

- `bal/core/heirs.py`
  - `validate_heir`: new `OP_RETURN:<hex>` address validation (rejects non-hex,
    > 80 bytes). OP_RETURN heirs are stored with amount `"0"` and carry the raw
    hex data in the address field.
  - `prepare_lists`: OP_RETURN heirs are skipped during amount calculation
    (`normalize_perc`, dust checks). They are kept in the will but produce a
    zero-value output.
  - `buildTransactions`: when building the transaction, OP_RETURN heirs emit a
    `OP_RETURN <hex>` scriptPubKey output with value 0, matching Bitcoin's
    OP_RETURN output standard.
  - `get_transactions`: OP_RETURN heirs are grouped with their locktime peers
    but excluded from fee/dust arithmetic.

- `bal/gui/qt/window.py`
  - Heir dialog / wizard: the address field accepts `OP_RETURN:` prefix and
    shows a decoded-text message field when an OP_RETURN address is entered.
  - `build_will` / `_build_success_report`: OP_RETURN heirs display the
    decoded message text in the build report instead of a BTC address.

- `bal/gui/qt/lists.py`
  - Heir list: OP_RETURN heirs display the decoded message in the address
    column and show "0" for the amount.

- `bal/gui/qt/common.py`
  - Re-export the new `validate_op_return_hex` helper for the GUI layer.

- `tests/test_core_heirs.py`
  - 8 new tests: OP_RETURN validation (valid hex, too long, non-hex), OP_RETURN
    heirs excluded from amount calculations, OP_RETURN output shape in built
    transactions, mixed OP_RETURN + regular heirs.

**Verification:**
- `ruff check` on changed production files: no new errors.
- Full test suite: 307 passed.

**Outcome:** DONE.

---

## 50. Core extraction: GUI-free logic into bal/core/ (reminders, checkalive, input_rules); RLock pickle fix

**Date:** 2026-08-05

**Goal:** extract GUI-free business logic that was previously embedded in Qt
widgets (`bal/gui/qt/widgets.py`) into standalone `bal/core/` modules, making
them independently testable without Qt. Also fix a critical `copy.deepcopy(tx)`
crash on Electrum 4.8 (`RLock` cannot be pickled) and improve wallet-DB
persistence robustness.

**What changed:**

- `bal/core/checkalive.py` (new)
  - `resolve_date_to_check`: computes the effective check-alive timestamp from
    will-settings and user type (BASIC uses `now()`; ADVANCED uses the stored
    threshold). Extracted from `window.py init_class_variables`.
  - `check_alive_expired`: pure-logic test for whether the check-alive has
    passed. Previously duplicated inline in `window.py`.

- `bal/core/reminders.py` (new)
  - `compute_reminder_offsets`, `basic_reminder_offsets`, `BALCalendar.write_ics`,
    `BALCalendar._ics_provider`: all calendar/reminder logic extracted from
    `widgets.py`. Generates iCal (.ics) files with separate VEVENT entries per
    reminder date. No Qt dependency.

- `bal/core/input_rules.py` (new)
  - `LockTimeRawEdit`, `LockTimeDateEdit`, `BalTimeEditWidget`,
    `ThresholdTimeWidget`: GUI-free data models for locktime/threshold
    validation and the Raw/Date selector logic. The Qt widgets in
    `widgets.py` now thin-wrap these helpers.

- `bal/core/heirs.py`
  - Fixed `copy.deepcopy(tx)` failure on Electrum 4.8: the `Transaction`
    object contains a `_thread.RLock` that cannot be pickled. Will-item
    persistence now re-parses the transaction from its hex serialization
    instead of deep-copying.
  - Invalid heirs (sentinel values from failed builds) are now kept in the
    wallet DB instead of being silently dropped, so the user can see and
    correct them.

- `bal/core/plugin_base.py`
  - Minor adjustments to support the extracted modules.

- `bal/gui/qt/widgets.py`
  - Major slim-down: business logic delegates to `bal/core/checkalive.py`,
    `bal/core/reminders.py`, and `bal/core/input_rules.py`. Only Qt widget
    creation and layout remain.

- `bal/gui/qt/calendar.py`
  - Adapted to use `bal/core/reminders.py` for .ics generation.

- `bal/gui/qt/window.py`
  - `init_class_variables` now calls `resolve_date_to_check` from
    `bal/core/checkalive.py` instead of computing inline.

- `bal/gui/qt/common.py`
  - Updated re-exports for the new core modules.

- Tests reorganized: core-only tests moved to `tests/test_core_checkalive.py`,
  `tests/test_core_reminders.py`, `tests/test_core_input_rules.py` (run
  without Qt).

- `tests/karen7`: fixture file compressed/updated for the new test structure.

**Verification:**
- `ruff check` on changed files: no new errors.
- Full test suite: 388 passed (significant increase due to new core test modules).

**Outcome:** DONE.

---

## 51. "Rebuild will on wallet close" setting (`REBUILD_ON_CLOSE`)

**Date:** 2026-08-14

**Goal:** add a new plugin setting that lets the plugin rebuild the will
automatically when Electrum closes, skipping the full Build wizard. When
enabled, closing Electrum triggers a one-shot prepare/inheritance flow
(check, rebuild if needed, sign, broadcast) without showing the
`BalBuildWillDialog`.

**What changed:**

- `bal/core/plugin_base.py`
  - New persisted config `REBUILD_ON_CLOSE = BalConfig(config,
    "bal_rebuild_on_close", False)` (default OFF), with explanatory comment.

- `bal/gui/qt/plugin.py`
  - New "Rebuild on close" checkbox in the settings dialog, bound to
    `REBUILD_ON_CLOSE`, with a tooltip explaining the behaviour. Added to the
    "Reset to Default Setting" list.

- `bal/gui/qt/window.py`
  - `on_close`: when `REBUILD_ON_CLOSE` is enabled, the close flow runs
    the auto-rebuild path (check + rebuild + sign + push) instead of the
    full wizard dialog. The legacy wizard-on-close path is kept when the
    setting is OFF.

- `tests/test_rebuild_on_close_setting.py` (new)
  - 8 tests: default OFF, toggle/persist, close triggers rebuild when ON,
    close skips rebuild when OFF, reset restores default.

**Verification:**
- `ruff check` on changed files: no new errors.
- Full test suite: 396 passed.

**Outcome:** DONE.

---

## 52. Headless CLI layer (`bal/cli/`, `bal/cmdline.py`, `bal_*` daemon commands)

**Date:** 2026-08-14

**Goal:** expose the full BAL inheritance cycle via Electrum's command-line
interface (daemon mode), without the Qt GUI. This enables scripting,
automation, and headless server usage.

**What changed:**

- `bal/cmdline.py` (new)
  - Zip-import shim for Electrum's `gui_name='cmdline'` plugin loader.
  - Follows the same `importlib` pattern as `qt.py` but never imports Qt.
  - Re-exports `Plugin` from `bal.cli.plugin`.

- `bal/cli/__init__.py` (new)
  - Registers the `bal_*` commands with Electrum on import.

- `bal/cli/plugin.py` (new)
  - `Plugin(BalPlugin)` -- minimal entry point for the daemon (no Qt hooks,
    no `bal_windows`).

- `bal/cli/commands.py` (new)
  - 30 `@plugin_command` async functions registered as `bal_*` commands:
    settings (list/get/set/reset), heirs (list/show/add/update/delete/import/
    export), will-executors (list/show/add/update/select/delete/ping/download/
    import/export), will (status/check/prepare/sign/broadcast/export/
    import_merge/invalidate/check_executor).
  - Each command is a thin transport layer: validates args, delegates to
    `BalController`, returns JSON-serializable results.

- `bal/cli/controller.py` (new, ~1100 lines)
  - `BalController` -- headless replica of `BalWindow`. Reads/writes wallet DB,
    config, and will-executors without any Qt dependency.
  - Methods mirror `BalWindow` flows: `load_willitems`, `save_willitems`,
    `init_class_variables`, `build_inheritance_transaction`, `sign_transactions`,
    `push_transactions_to_willexecutors`, `check_transactions`, `export_json_file`,
    `merge_will_from_file`, `invalidate_will`.
  - Domain exceptions (`WillExpiredException`, `HeirNotFoundException`, etc.)
    are converted to `UserFacingException` with clear text.

- `bal/manifest.json`
  - `"available_for"` updated from `["qt"]` to `["qt", "cmdline"]`.

- `bal/__init__.py`
  - Added `from .cli import commands` to register `bal_*` commands on import
    (both CLI pre-parse and GUI startup).

- `tests/test_cli_commands_registered.py` (new)
  - 4 tests: all `bal_*` commands registered, all are coroutines, no duplicate
    registration, all args documented.

- `tests/test_cli_controller_offline.py` (new)
  - Offline CRUD tests for heirs, will-executors, settings, and will
    import/export merge via `BalController` (no network).

**Verification:**
- `ruff check` on new files: clean.
- Full test suite: 427 passed.
- `tests/test_cli_commands_registered.py`: all 4 tests pass.

**Outcome:** DONE.

---

## 53. Auto-rebuild on new transactions (`AUTO_REBUILD`)

**Date:** 2026-08-16

**Goal:** when new transactions are detected in the wallet (e.g. incoming
payments), automatically rebuild the will so the inheritance covers the
new UTXOs. The delivery date is anticipated by one day to orphan the old
will on-chain; an on-chain invalidation is only needed when the anticipated
locktime crosses the Check Alive threshold.

**What changed:**

- `bal/core/plugin_base.py`
  - New persisted config `AUTO_REBUILD = BalConfig(config,
    "bal_auto_rebuild", False)` (default OFF), with explanatory comment.

- `bal/gui/qt/plugin.py`
  - New "Auto-rebuild" checkbox in the settings dialog, bound to
    `AUTO_REBUILD`, with a tooltip. Added to the "Reset to Default Setting"
    list.

- `bal/gui/qt/window.py`
  - New `_auto_rebuild_on_new_tx()` method: triggered when Electrum detects
    a new transaction in the wallet. Runs the full prepare flow: check
    coherence, rebuild with the anticipated locktime (delivery date minus 1
    day), persist, sign (if passwordless), push to will-executors.
  - When the anticipated locktime crosses the Check Alive threshold, returns
    an invalidation transaction instead of auto-completing.
  - Connected to Electrum's `new_transaction` signal.

- `tests/test_auto_rebuild_on_new_tx.py` (new)
  - 16 tests: AUTO_REBUILD default OFF, toggle/persist, rebuild triggers on
    new tx, locktime anticipation by 1 day, threshold crossing returns
    invalidation, passwordless wallet signs automatically, encrypted wallet
    returns invalidation tx for manual signing.

**Verification:**
- `ruff check` on changed files: no new errors.
- Full test suite: 432 passed.

**Outcome:** DONE.

---

## 54. CLI `bal_will_autorebuild` command

**Date:** 2026-08-16

**Goal:** expose the auto-rebuild flow (entry #53) as a headless CLI command,
so scripts and daemons can trigger the one-shot check/rebuild/sign/push
cycle without the Qt GUI.

**What changed:**

- `bal/cli/commands.py`
  - New `bal_will_autorebuild` async command (flag `nw`): runs the full
    auto-rebuild flow via `BalController.auto_rebuild()`. Returns a JSON
    object with `result` (`valid`, `no_heirs`, `invalidated`, `nothing`,
    `needs_signing`, `rebuilt`) and, when applicable, the invalidation
    transaction.

- `bal/cli/controller.py`
  - New `auto_rebuild()` method: headless replica of the GUI auto-rebuild
    flow. Checks coherence, rebuilds with anticipated locktime, handles
    threshold-crossing (returns invalidation tx), signs passwordless wallets
    automatically, pushes to will-executors.

- `tests/test_cli_autorebuild.py` (new)
  - 10 tests: auto-rebuild returns `valid` when will is coherent, `rebuilt`
    when rebuilt, `invalidated` with invalidation tx when threshold crossed,
    `needs_signing` for encrypted wallets, `no_heirs` when heirs are missing.

- `tests/test_cli_commands_registered.py`
  - Updated `EXPECTED_COMMANDS` to include `bal_will_autorebuild`.

- `tests/test_cli_controller_offline.py`
  - Extended with auto-rebuild flow tests.

**Verification:**
- `ruff check` on changed files: no new errors.
- Full test suite: 438 passed.

**Outcome:** DONE.

---

## 55. Remove "Add transaction without willexecutor" from settings dialog

**Date:** 2026-08-17

**Goal (owner request):** the "Add transaction without willexecutor" checkbox
was already available in the Will-Executor tab; showing it redundantly in the
settings dialog created confusion. Remove it from the settings dialog and
renumber the grid rows.

**What changed:**

- `bal/gui/qt/plugin.py`
  - Removed the "Add transaction without willexecutor" checkbox
    (`NO_WILLEXECUTOR`) from the settings dialog grid. The setting is still
    functional (available from the Will-Executor tab and the wizard); only
    the settings-dialog exposure was removed.
  - Grid rows 5--16 renumbered to 4--15 to close the gap left by the removal.
  - Removed the corresponding reset-button widget for `NO_WILLEXECUTOR` from
    the "Reset to Default Setting" list.

**Verification:**
- `ruff check` on changed file: no new errors.
- Full test suite: 438 passed (unchanged).

**Outcome:** DONE.

## 56. QR / audio will transfer (chunked multi-QR export/import + review-and-sign wizard)

**Date:** 2026-08-27

**Goal (owner request):** export a will to another device via QR codes (with
an optional audio channel on top), and import it there with a per-transaction
review-and-sign flow. QR codes are chunked because a full will usually exceeds
a single code's capacity.

**What changed:**

- `bal/core/qrtransfer.py` (new, GUI-free): the wire format and chunk
  scheduler — `BALQR{version}|{total}|{index}|{flags}|{payload}` frames,
  `encode_transfer` / `decode_transfer`, `split_frames` / `parse_frame` /
  `assemble`, optional `Z` (zlib+base64) compression at export, four chunk
  presets (150/400/900/1800 bytes/frame, EC level M), plus
  `preset_index_for_chunk_size` and the `QrTransferError` exception family.
- `tests/test_core_qr_transfer.py` (new): round-trips (plain/compressed),
  boundaries (exact-fit, size>payload, `|` in payload), min-size guard, bad
  magic/version/numbers/flags, multi-frame reassembly, header consistency.
- `bal/core/plugin_base.py`: new `QR_CHUNK_SIZE` config (default 150).
- `bal/gui/qt/plugin.py`: settings-dialog row 16 "QR Code Size" combo
  (4 presets) + reset button, visible in BASIC and ADVANCED.
- `bal/gui/qt/dialogs.py`:
  - `BalQrImage`: QR widget with MEDIUM error correction (Electrum's
    `QRCodeWidget` is EC-L), reusing `draw_qr`.
  - `WillQrExportDialog`: walks the frames (Prev/Next, "i of N" progress),
    export filters **All / Valid / Valid-NC**, live chunk-preset selector,
    **Auto slideshow** (toggle button + "QR codes per second" spinbox, stops on
    the last frame and on filter/chunk changes), optional audio-send button.
  - `WillQrImportDialog`: camera scan (Electrum `scan_qrcode_from_camera`,
    one code at a time), manual paste fallback, slot grid (1..N) with
    green=stored, total-mismatch reset, optional audio-receive that mirrors
    the audio_modem `_recv` sink with a callback instead of `setText`.
  - `WillTxReviewSignDialog`: per-transaction review (outputs via
    `get_ui_address_str`, total outputs, fee) with Sign / Skip / Cancel and a
    single wallet password; final page offers "Save signed file…" and
    "Show signed QR…". Runs on the imported local copy only.
- `bal/gui/qt/window.py`: `export_will_via_qr`, `import_will_via_qr`,
  `get_audio_modem_plugin`, `_audio_send_payload`; `sign_transactions`
  refactored into a byte-equivalent batch loop plus the reusable
  `_prepare_and_sign_tx(…, txid, password)` single-transaction helper.
- `bal/gui/qt/lists.py`: will-list menu gains **Export → QR Codes** and
  **Import via QR**.
- `tests/test_gui_qr_transfer.py` (new): export build/navigation/chunk
  change, filters (Valid, Valid-NC, empty-revert), import frame flow,
  assembly+decode, total-mismatch reset, manual entry.
- `PLAN_QR_TRANSFER.md`: the full spec (wire format, settings, export,
  import, wizard checklist P0–P6, findings log).
- Docs: `README.md` QR-transfer section; `QML_PLAN.md` updated (Phase 2
  `BalQrTransferModel`, Phase 3 dedicated QML export/import pages, R6
  mitigation rewritten, deferred-chunks note removed).

**Audio channel caveats:**
- The audio send/receive buttons only appear when Electrum's `audio_modem`
  plugin is enabled *and* `amodem` + PortAudio are installed (not present in
  the current dev runtime — verified F22). On that channel the transport
  zlib-compresses internally, so no BAL framing/`Z` flag is used.
- `WaitingDialog` requires a real `QWidget` parent and the plugin's `_recv`
  hard-wires `parent.setText`, so receive uses a local mirror with a
  callback sink.

**Verification:**
- `python3 tests/test_core_qr_transfer.py`: all pass.
- `QT_QPA_PLATFORM=offscreen python3 tests/test_gui_qr_transfer.py`: all pass.
- Pytest batch `tests/test_core_*.py tests/test_gui_*.py`: 374 passed (only
  the two pre-existing `test_bt_to_date_*` datetime compare failures remain).
- `QT_QPA_PLATFORM=offscreen python3 tests/smoke_test.py
  electrum.plugins.bal`: passed (clean import under real Electrum).
- `ruff`: no new violations on changed files.

**Audio-environment notes (dev box, discovered while testing):**
- `amodem` 1.16.0 is old and uses `np.ndarray.tostring()`, removed in numpy 2.x;
  the runtime venv (numpy 2.4.6) needs the one-line patch
  `tostring()` → `tobytes()` in
  `electrum/env/lib/python3.11/site-packages/amodem/common.py` (done locally,
  not in the repo). Any machine with numpy>=2 and this amodem version needs
  the same patch (or numpy<2).
- Electrum's `audio_modem` plugin hardcodes `libportaudio.so` (unversioned).
  Debian/Ubuntu only ship `libportaudio.so.2`, so the load fails silently
  inside the plugin's `_send` `WaitingDialog` (no `on_error` → no sound, no
  message). Fix on the dev box:
  `sudo ln -s /usr/lib/x86_64-linux-gnu/libportaudio.so.2 /usr/lib/x86_64-linux-gnu/libportaudio.so`
  (created by the `libportaudio-dev` package; a `LD_LIBRARY_PATH` stub works
  without root). The audio buttons stay hidden unless the plugin is enabled
  and available.
- Verified on the dev box (no physical mic required) via a full
  send→sink→monitor→recv round-trip: set the default PulseAudio source to
  `<sink>.monitor` at receive time; payload returned byte-identical.

**Follow-up fixes (same session, reported during audio testing):**
- `WillItem.__init__` now defaults `status` to `""` instead of `None`. A
  `WillItem` built from a bare `{"tx": ...}` (QR/audio import, clipboard
  merge) crashed in `set_status` with
  `unsupported operand type(s) for +=: 'NoneType' and 'str'` during the
  validity pass / `IMPORTED` marking.
- `BalWindow.invalidate_will` guards a missing `date_to_check` (first-action
  case) like `merge_will` already did, fixing
  `AttributeError: 'BalWindow' object has no attribute 'date_to_check'` when
  invalidating before the periodic check initialized it.
- Regression test `test_imported_item_status_not_none` added to
  `tests/test_gui_qr_transfer.py`; QR GUI suite 12/12, batch 377 passed.
---

## 57. Name the real cause of a failed build instead of guessing

**Date:** 2026-09-04

**Goal (owner request):** the "Building Will" report was hard to read and often
misleading.

1. The long "could not build the will" block was printed entirely in amber
   (`COLOR_WARNING`), which the owner reported as barely legible.
2. Whenever the build produced nothing, the dialog printed a FIXED list of
   three "possible reasons" (low balance / dust shares / check-alive later than
   the delivery date) regardless of what had actually happened. In a case
   reproduced from the owner's log all three were false, and the real cause
   (no delivery date left to build) was not even in the list.
3. The "Checking your will" row had the same problem: the single sentence
   "Found CHANGES to the DATE or the HEIRS" was shown for five different
   situations, including one where it is plainly wrong - funds received, where
   neither the date nor the heirs changed.

**What changed:**

- `bal/core/heirs.py`
  - `Heirs.__init__` / `buildTransactions`: new `last_build_error` attribute
    recording WHY a build produced no transaction. Reset at the start of every
    build, and set at each path that previously returned empty with no
    explanation at all: `NO_HEIRS`, `NO_UTXO`, `NO_WILLEXECUTOR_USABLE`,
    `NO_FUTURE_DATE`, `WILLEXECUTOR_FEE`, `WILLEXECUTOR_FEE_TOO_HIGH`,
    `TX_BUILD_FAILED`, `WILLEXECUTOR_TX_ERROR`.
  - Added a `processed_willexecutors` counter so that "the loop skipped every
    will-executor" - which returned silently, with no log line whatsoever - is
    told apart from "we tried and the build failed".
  - Fixed a latent crash in the `prepare_transactions` exception handler. It
    read `e.heirname` in order to auto-deselect the offending will-executor,
    but NOTHING in the plugin sets that attribute any more (leftover from an
    older exception design), so the lookup itself raised AttributeError and the
    inner `except Exception: raise` re-raised THAT, aborting the whole build
    with a confusing secondary error instead of the real one. The handler now
    records `WILLEXECUTOR_TX_ERROR`, logs the actual exception together with
    the will-executor it happened on, and moves on to the next one - which is
    what the original code was clearly trying to do.

- `bal/gui/qt/dialogs.py`
  - New `msg_alert()`: an amber warning sign (U+26A0, written as a numeric HTML
    entity so the source stays ASCII) followed by text in the theme's default
    colour. Colour is what ATTRACTS attention, not what is read, so it is kept
    on the sign alone; the message body stays readable and still works under
    the dark theme, where a hard-coded black would disappear.
  - New `_build_failure_message()`: maps `last_build_error` to ONE specific
    sentence. When the code is missing or unrecognised it SAYS the cause could
    not be determined and lists what to check, instead of asserting three
    guesses as if they were the only possibilities.
  - New `_check_failure_message()`: replaces the single "Found CHANGES to the
    DATE or the HEIRS" line with seven precise messages, reusing the detail the
    exceptions already carry (heir name, will-executor URL, old and new fee
    rate). The two plain `NotCompleteWillException` cases are told apart
    STRUCTURALLY (raised with no argument vs. with one), not by matching
    message text, which would be fragile. No new exception classes were added
    (owner request).
  - Added a dedicated `except BalanceTooLowException` handler. The exception
    already carried the balance, the fees and the dust threshold, but was
    falling through to the generic handler, which printed the raw technical
    string in red and re-raised. It now shows the real figures.
  - "Checking variables" row: `No Heirs` now uses `msg_alert()`. The
    "Check Alive Threshold Passed" message deliberately STAYS red
    (`COLOR_ERROR`) because it is the more urgent situation (owner request).

- `bal/gui/qt/common.py`
  - Re-export `BalanceTooLowException` from `core.heirs` so the Qt layer can
    catch it.

**Verification:**
- `py_compile` clean on all 44 files of the package.
- The real `msg_alert`, `_build_failure_message` and `_check_failure_message`
  were extracted from the source via AST and executed against every reason code
  and every exception type, with the exception hierarchy rebuilt from
  `will.py`: 9 build cases and 8 check cases all produce the intended text.
- NOT RUN: the official test suite. The machine used for this task (Windows)
  has no importable `electrum` module, so `tests/` could not be executed.
- Manually tested by the owner in Electrum 4.8.1: `NO_FUTURE_DATE`,
  `WILLEXECUTOR_FEE` and `No Heirs` were all confirmed on screen.

## 57. Remove all `copy.deepcopy` (ad-hoc copy helpers; `WillItem` copies serialize/deserialize)

**Date:** 2026-08-28

**Goal (owner request):** eliminate every `copy.deepcopy` from the codebase
and replace it with ad-hoc copy methods; `WillItem` copies must be produced by
serializing and deserializing the item rather than by deep-copying live
runtime objects (which can hold a `threading.RLock` and cannot be pickled).

**What changed:**

- `bal/core/util.py`: new `copy_structure(value, _path="copy")` — the single
  JSON-safe, deepcopy-free recursive cloner (dict / list / tuple cloned
  structurally, JSON scalars kept as-is, any accidental runtime object coerced
  to `str` + logged). It replaces the old `heirs._json_safe` implementation.
- `bal/core/heirs.py`: `_json_safe` is now a thin backward-compatible alias of
  `bal.core.util.copy_structure`; `Heirs.save` behaviour is unchanged.
- `bal/core/will.py`:
  - `WillItem.__init__` on a `WillItem` argument no longer does
    `self.__dict__ = w.__dict__.copy()` + `copy.deepcopy`; instead it
    serializes (`to_dict()`) and deserializes: the tx is re-parsed into a fresh
    object, `STATUS` is rebuilt from a clone, and heirs / will-executors are
    cloned recursively, so the copy shares no mutable state with the source.
  - New `WillItem.copy(wallet=None)` (serialize/deserialize round trip; re-adds
    wallet tx info when a wallet is passed) and the static
    `WillItem.copy_status_table(table)` used for the `STATUS` tables.
  - `to_dict()` now also emits `Father` / `Children` so the round trip is
    faithful.
  - `normalize_will` routes copies through the constructor / `copy()`.
- `bal/gui/qt/window.py` and `bal/cli/controller.py`: the Build-will flow now
  uses `copy_structure(...)` instead of `copy.deepcopy(...)` for heirs and
  will-executors.
- Dropped now-unused `import copy` (`will.py`, `controller.py`, `qt/common.py`,
  `qt/window.py`).
- Tests updated to the same helpers: STATUS tables via
  `WillItem.copy_status_table`, heirs / built dicts via `copy_structure`
  (`test_core_will.py`, `test_core_will_invalidate.py`,
  `test_heir_relative_anchor.py`, `test_anticipate_manual_locktime.py`,
  `test_no_willexecutor_karen7.py`, `test_reproduce_none_type.py`,
  `test_group_e_mock_karen7.py`, `test_group_e_karen7_invalidate.py`,
  `sim_update_flows.py`).

**Verification:**
- Full offline batch `tests/test_core_*.py tests/test_gui_*.py`: 377 passed,
  only the two pre-existing `test_bt_to_date_*` datetime compare failures
  remain (identical to HEAD — no regression; `test_heir_relative_anchor`
  isolated-file failure is pre-existing test-pollution at HEAD too).
- Ad-hoc semantics check: `copy()`/ctor copy share no mutable state with the
  source (mutating source heirs/STATUS does not leak into the copy and vice
  versa), `copy_status_table` returns fresh lists, `normalize_will` runs.
- `ruff` on all touched files: no new violations (4 findings, all pre-existing
  at HEAD).
- `tests/smoke_test.py electrum.plugins.bal`: passed.
- `python3 build_zip.py`: 45 files, 343591 bytes, sha256 `aa8f8154…`;
  `tests/external_zip_test.py bal-electrum-plugin.zip`: passed (Plugin class
  loads via the zipimport shim).

**Outcome:** DONE.

---

## Next. Animated-QR interop (BC-UR v1/v2, BBQR)

**Date:** 2026-09-08

**Goal:** Let BAL export/import a will not only as its own BAL QR frame format
but also as BC-UR v1 (`ur:bytes`, BC32 + SHA-256), BC-UR v2 (`ur:bytes`, CBOR
bytewords-minimal fountain codes) and BBQR (Coinkite `B$…`) animated-QR
sequences, so transfers interoperate with Blockchain Commons / Coldcard-style
tools and BitKit. Codecs must be stdlib-only and the export must keep BAL QR
as the default.

**What changed:**

- `bal/core/animated_qr.py` (new): stdlib-only codec module.
  - BC32 (bech32_bis checksum, XOR `0x3FFFFFFF`) encode/decode matching the
    BCR-2020-004/005 reference vectors.
  - bytewords-minimal encode/decode (BCR-2020-012) with CRC-32 rejection;
    the word list was transcribed verbatim from the reference C++.
  - BC-UR v2: CBOR part writer/reader, CRC-32, `choose_fragments`
    (xoshiro256** + alias + ary-threshold sampler) and XOR-based fountain
    mixing/solving; emits a redundant mixed wave for loss tolerance.
  - BC-UR v1: multipart with SHA-256 digest and single-part digest-less
    frames; `1of1` handling.
  - BBQR: base32 (encoding `2`), hex (uppercase, `H`) and zlib (lowercase,
    `Z`, automatic compression fallback) frames; out-of-order reconstruction.
  - One `AnimatedQrSession` + `detect_format` + `parse_for_detection` for
    auto-detecting the incoming format and keying the GUI debounce.
  - Safety caps: `_MAX_SESSION_PARTS = 20000`, `_MAX_MESSAGE_BYTES = 32 MB`,
    zlib-bomb guard, `TransferConflictError`/`SessionLimitError`.
- `bal/gui/qt/dialogs.py`:
  - Export page (`BalQrExportWidget`) gained a **Format** selector
    (BAL QR default, BC-UR v1, BC-UR v2, BBQR) reusing the QR-size presets,
    with per-format intro/format-hint text.
  - Import page (`BalQrImportWidget`) now routes every frame through
    `parse_for_detection` + `AnimatedQrSession.add_part`, auto-detecting the
    format and resetting when the transfer's session key changes; the
    review/sign step resolves the session and decodes parts uniformly.
  - `qr_import_accept_frame` generalised to
    `(state, fmt, session_key, frame_total, index, payload, stable_reads=2)`.
- `tests/test_core_animated_qr.py` (new, 32 tests): BC32 spec vectors,
  bytewords round-trip/CRC, C++ reference-frame decode+re-encode parity
  (single-part 12B, seq_len=2, seq_len=7), fountain solve with missing pure
  part, out-of-order/duplicate handling, single/multipart UR v1, BBQR
  Z/2/H round-trips, runt last part, zlib-bomb guard, detection positive/
  negative.

**Verification:**

- `tests/test_core_animated_qr.py`: 32/32 pass.
- `tests/test_gui_qr_transfer.py` (now 36 tests) + `test_gui_export_dialogs.py`: pass.
- `ruff` clean on `animated_qr.py`, `dialogs.py` and both test files;
  `pyright` 0 errors on the touched modules.
- `tests/smoke_test.py electrum.plugins.bal`, `python3 build_zip.py` and
  `external_zip_test.py` all pass.
- Full regression: 462 passed; only pre-existing failures remain
  (`test_bt_to_date_*`, will-invalidate fee, unrelated `sign_transactions`
  stub test).

**Notes / caveats:**

- A real bug was found & fixed during this work: `_ur2_part_cost` used
  `2 * body_len` but `bytewords_minimal_encode` appends a 4-byte CRC, so every
  UR v2 frame was undercounted by 8 characters and could overflow the QR
  budget for large transfers.
- UR v1 multipart emits the digest-carrying `1of1/<digest>/<frag>` form for a
  single part (both headered and headerless single parts are accepted on
  import); this keeps deterministic digest verification.
- Imported payloads are UTF-8 text; the codec sessions do not decode raw
  binary transfer blobs.

**Outcome:** DONE (uncommitted).

---

## Animated-QR bugfix: QVideoSink signal wiring + will-export JSON crash

**Date:** 2026-09-08

**Goal:** Fix two runtime crashes found by manual testing of the QR paths.

**What changed:**

- `bal/gui/qt/dialogs.py`:
  - `_start_scan`/`_stop_scan` used `QVideoSink.videoFrame.connect/.disconnect`,
    but on PyQt6 `videoFrame` is the frame **getter method**, not a signal —
    this raised ``AttributeError: 'builtin_function_or_method' object has no
    attribute 'connect'`` on camera scan. Switched to the `videoFrameChanged`
    signal (same wiring Electrum's `QrReaderVideoSurface` uses).
  - `_stop_scan` now tolerates `AttributeError` when disconnecting the sink
    and guards the `errorOccurred` disconnect too, so a mid-init failure can
    never cascade into a second uncaught exception.
  - `_whole_will_json` (whole-will QR export) serialized ``WillItem.to_dict()``
    with plain `json.dumps`, crashing with ``TypeError: Object of type
    Transaction is not JSON serializable`` (the ``tx`` field holds a real
    ``Transaction``). Now uses Electrum's `MyEncoder`, matching `write_json_file`.
- `tests/test_gui_qr_transfer.py`: new `test_import_start_stop_scan_signal_wiring`
  drives the real `QVideoSink` life-cycle with a mocked camera and fails if
  the signal name regresses to `videoFrame`.
- `tests/test_gui_export_dialogs.py`: new
  `test_qr_whole_will_json_serializes_transaction` covers the JSON export.

**Verification:**

- `pytest tests/test_gui_qr_transfer.py tests/test_gui_export_dialogs.py -q`: pass.
- Full offline batch `tests/test_core_*.py tests/test_gui_*.py`: 444 passed.
- Regression test flips correctly (fails when reverted to the buggy call).
- `ruff` clean on touched files; `tests/smoke_test.py`, `build_zip.py`,
  `external_zip_test.py` all pass.

**Outcome:** DONE (uncommitted).

---

## Balanced-QR wire format v2: compact header + best-of compression

**Date:** 2026-09-13

**Goal:** Shrink the native BAL QR wire format to its minimum. The old
pipe-separated header (`BALQR1|total|index|flags|`) wasted 12-14 characters on
direction marker, separators and decimal count fields, and the export always
sent uncompressed hex text. New exports should fit a will in the fewest,
densest frames possible.

**What changed:**

- `bal/core/qrtransfer.py`:
  - New wire format v2: `BAL1<TTT><iii><F><payload>` — fixed 11-char header,
    no separators. `BAL1` magic, 3-digit **base36** zero-padded totals/index
    (values `00A`-`ZZZ`, cap 46655 frames), single flag char.
  - Flags: `0` = plain payload, `Z` = zlib+base64 compressed payload (the importer
    already decompressed `Z`; the exporter now produces it).
  - `encode_transfer_best(tx_strings)` returns the shorter of plain vs
    compressed; the export widget uses it as the default for BAL QR.
  - `parse_frame` is dual: old `BALQR1|total|index|flags|payload` frames still
    import unchanged (backwards-compatible receive).
  - Frame-count overflow (a transfer needing > 46655 frames) raises
    `QrTransferError` at encode time instead of emitting corrupt headers.
- `bal/gui/qt/dialogs.py` (`BalQrExportWidget`): BAL QR export now encodes via
  `encode_transfer_best`, so plain *or* compressed frames are emitted per
  transfer; import is untouched (already format-agnostic and flag-driven).
- `bal/core/animated_qr.py`: `detect_format` accepts `BAL1` in addition to the
  legacy `BALQR` prefix; wire-format docstring updated.
- `bal/gui/qt/widgets.py` (`WillWidget`): the will detail view now shows each
  heir's address (or the decoded UTF-8 text of an `OP_RETURN:` heir) and a
  dedicated Address row for the will-executor.

**Verification:**

- `tests/test_core_qr_transfer.py` (new v2 tests: header structure, field width,
  `Z` flag round-trip, best-of selection, malformed `BAL1` frames, 46655 cap and
  exact boundary): all pass; legacy `BALQR1` parse tests unchanged and green.
- `tests/test_core_animated_qr.py`: `BAL1` detection + `parse_for_detection`;
  `tests/test_gui_qr_transfer.py`: format-combo + chunk-navigation updated for
  the compact export.
- `pytest tests/test_core_*.py tests/test_import_will_details.py -q`: 345 passed.
- `ruff` clean on all touched files except the pre-existing `dialogs.py` I001
  (present on HEAD); `pyright` 0 errors on the codec modules.
- Android Chaquopy bundle re-synced (`sync_codecs.py` + `verify_chain.py`).

**Notes / caveats:**

- **Compatibility break (forward):** the new default export (compressed
  `BAL1…`) is NOT readable by older BAL versions — nor by the previously
  released Android APK — until those are updated to accept `BAL1`. Imports of
  legacy `BALQR1…` exports keep working on this version. Existing audio
  transfers are unaffected (they keep explicit `compress=False`, and the audio
  format was never flag-driven on receive).
- A typical multi-tx will now ships as a single dense frame instead of two
  sparse ones: header overhead dropped from 12-14 chars to a constant 11, and
  the base36 count fields are 3 chars regardless of how many frames exist.

**Outcome:** DONE.

## 58. i18n phase 1: BAL translation layer (Electrum first, then BAL)

**Date:** 2026-09-26 (branch `feature/i18n`, see `PLAN_I18N.md`)

**Goal (owner request):** make the whole plugin translatable, starting with
Italian, so that BAL follows the language chosen in Electrum. This phase adds
the mechanism only: nothing visible changes yet.

**What changed:**

- New `bal/i18n.py`, BAL's own gettext layer (domain `bal`, catalogs in
  `bal/locale/<lang>/LC_MESSAGES/bal.mo`, read with `plugin.read_file()` so it
  works from the zip and from a development checkout):
  - `_()` looks a text up in Electrum's catalog first, then in BAL's, then
    returns the English source (owner's decision D7: BAL reads like Electrum,
    and Electrum's translations would take over if BAL ever became an internal
    plugin). BAL translations whose `{}` fields differ from the source are
    rejected with the same rules as Electrum's.
  - `N_()` marks texts defined at import time, translated when shown.
  - `init_from_config()` repeats Electrum's own language choice (Preferences >
    Language, else the supported system language, else English) once, in the
    Qt `Plugin.__init__`. A missing or broken catalog never stops the plugin.
- `common.py`, `theme.py`, `core/will.py`, `core/willexecutors.py` now import
  `_` from `bal.i18n`. The CLI keeps Electrum's translator and stays English,
  like Electrum's own CLI.

**Verification:** new `tests/test_i18n.py`; results in entry 59.

**Outcome:** DONE (delivered in one test ZIP together with entry 59).

## 59. i18n phase 2: translatable texts and language-neutral stored data

**Date:** 2026-09-26 (branch `feature/i18n`, see `PLAN_I18N.md`)

**Goal:** make every user-visible text of the Qt GUI extractable and
translatable, without changing the English UI, and make sure no stored data
depends on the GUI language.

**What changed:**

- Texts built with f-strings or `.format()` inside `_()`, or glued together
  from pieces, are now whole sentences with `{}` fields (Ruff INT clean).
- Class-level texts (list headers, wizard titles/messages, help texts,
  download error messages, status labels) are marked with `N_()` and
  translated when shown: class bodies run before the catalog is loaded.
- Helpers no longer translate their argument (`add_widget`, `get_window_title`,
  `msg_set_status`, the will-detail `qlabel`): callers pass translated text.
  This also stops heir names and URLs from going through the translator.
- Newly translatable: the settings help texts, the wizard download options,
  the build report rows, the Ping/Select menu buttons, the QR size presets
  (marked in `common.QR_PRESET_LABELS`: `core/qrtransfer.py` stays free of
  Electrum imports because the Android reader ships a copy of it), and the
  "(reminder i/n)" suffix of the calendar events (`build_ics_reminders` gained
  `reminder_suffix`, English by default for the CLI).
- **Stored status history:** `WillItem.set_status` now always writes the
  English label; the new `format_status_history()` translates it for display
  (lists and will detail) and keeps unknown tokens as they are, so histories
  saved by older versions (e.g. `New.Firmato.Pushed`) still display fine.
- **Calendar defaults:** `EVENT_SUMMARY` / `EVENT_DESCRIPTION` defaults follow
  the GUI language (`BalConfig(..., translatable=True)`), while the English
  original is what gets stored; a text written by the user is kept as it is.
- **Not translated on purpose:** `HISTORY_LABEL` (used by
  `Util._label_matches_history` to recognise BAL's own local transactions),
  the "BAL Invalidate/Inheritance transaction" wallet labels,
  `heirs.TRANSACTION_LABEL` and the will-executor "info" fallback stored in the
  config.
- Two small visible fixes: the will detail showed "Transaction fees::" and
  "Status::" with a double colon.
- `pyproject.toml`: Ruff now also checks the `INT` (gettext) rules.

**Verification (Electrum 4.8.2, Python 3.14, offscreen):**

- `tests/test_i18n.py`: 20 passed (lookup order, rejected `{}` fields,
  missing/broken catalogs, language fallback, English status storage and its
  display translation, translatable config defaults, reminder suffix, QR
  preset labels).
- Release-gate batch (as `make-release.sh`): 403 passed, 1 failed; the other
  offline test files: 213 passed. Identical to the baseline taken before the
  change; the only failures are pre-existing (`test_e5` and
  `test_reproduce_none_type` need the local `tests/karen7` wallet,
  `test_import_start_stop_scan_signal_wiring` fails on `main` too,
  `test_gui_export_dialogs.py` hangs on `main` too).
- `smoke_test.py`, `build_zip.py` and `external_zip_test.py`: OK.
- Ruff: INT clean; no new errors (the only one left is the pre-existing
  `dialogs.py` I001).
- Babel extraction (GUI + core): 424 strings (about 3,000 words), was 323.

**Outcome:** DONE, pending the owner's test of the ZIP.

## 60. i18n phase 3: Italian catalog

**Date:** 2026-09-26 (branch `feature/i18n`, see `PLAN_I18N.md`)

**Goal (owner request):** with Electrum set to Italian, BAL shows in Italian.

**What changed:**

- `babel.cfg` (extraction mapping, CLI and wallet_util excluded),
  `bal/locale/bal.pot` (template, 424 texts) and the Italian catalog
  `bal/locale/it_IT/LC_MESSAGES/bal.po`: 43 entries copied from Electrum's
  own it_IT catalog (same English text), the others drafted following the
  owner's glossary (D1). Draft to be reviewed by the owner (D2).
- `build_zip.py` compiles every `bal.po` into `bal.mo` inside the zip
  (Babel required, the build stops without it); `.po`/`.pot` are not
  shipped and `*.mo` is ignored by git (D4).
- New `tests/test_translations.py`: every catalog compiles; `{}` fields and
  `$tokens` match the English text; no Bitcoin address, e-mail, URL or long
  letters-and-digits word that the English text does not have (patterns from
  Electrum's `electrum-locale/update.py`).
- `AGENTS.md`: how to update the catalogs, add a language, compile for the
  development install.

**Verification (Electrum 4.8.2, offscreen):** `tests/test_translations.py`
OK (it_IT: 424 translated, 0 untranslated, 0 fuzzy); all offline tests 640
passed, only the pre-existing failures of entry 59; the built zip contains
only `bal/locale/it_IT/LC_MESSAGES/bal.mo` for the translations and
`external_zip_test.py` loads it; Ruff: no new errors.

**Outcome:** DONE, pending the owner's review of the Italian texts and test
of the ZIP.
## 61. Signed relative Check Alive (negative = before delivery, positive = after creation)

**Date:** 2026-10-01

**Goal:** Change the meaning of a RELATIVE Check Alive value so that its SIGN
selects the anchor date:

- NEGATIVE (`-30d` / `-1y`): counted BACKWARDS from the delivery date
  (`delivery + N days`; `-30d` = 30 days BEFORE the delivery). This preserves
  the historical meaning of a relative threshold.
- POSITIVE (`30d` / `1y`): counted FORWARD from the inheritance creation date
  (`min(WillItem.time)` over the VALID will items, frozen in the wallet DB).
- When the anchor is unknown (no built will yet, or imported items without a
  `time`), the reference falls back to *now*.

No migration: existing stored positive values switch to the new meaning. The
Check Alive must always be strictly AFTER today and strictly BEFORE the
delivery; when it is not, the plugin raises an explicit error (see below).

**What changed:**

- `bal/core/checkalive.py` (centralised, GUI-free):
  - new `resolve_delivery_date(will_settings, now, built_locktime)` — the single
    source of the delivery reference (absolute locktime as-is; relative locktime
    resolved forward from *now* unless a built will freezes it).
  - new `resolve_relative_check_alive_date(threshold_raw, delivery_date,
    creation_date, now)` — the SINGLE calculation rule (sign selects the anchor;
    `BalTimestamp.to_date` is used forward on both anchors, no more
    `reverse=True`).
  - new `is_check_alive_date_valid(date_to_check, delivery_date, now)` and
    `ensure_check_alive_valid(is_basic_mode, date_to_check, delivery_date, now)`
    — the SINGLE validity gate: `now < date_to_check < delivery`. BASIC mode is
    a no-op (Check Alive is inert).
  - `resolve_date_to_check` / `resolve_guard_threshold`: gain a `creation_date`
    parameter and delegate to the shared rule; `resolve_guard_threshold` still
    resolves the delivery from the stored settings alone (no built anchor).
  - `check_alive_expired` removed (superseded by `ensure_check_alive_valid`).
  - new `CheckAliveAfterDeliveryError` (a SIBLING of `CheckAliveError`, not a
    subclass): raised when the Check Alive is on/after the delivery. It must not
    reuse the `except CheckAliveError` handlers, which invalidate the will
    on-chain; this is a configuration error that only aborts the action.
    `CheckAliveError` gained an optional `reason`.
- `bal/core/will.py`: new `Will.get_creation_time(will)` — the oldest
  `WillItem.time` among VALID items (`None` when unknown).
- `bal/core/input_rules.py`: `normalize_locktime_raw_text(..., allow_negative)`
  keeps a single leading `-` (Check Alive field only).
- `bal/gui/qt/widgets.py`: the Check Alive RAW editor accepts `-`
  (`ThresholdTimeWidget.allow_negative_raw`); `on_locktime_change` uses the
  shared resolver for `real_threshold`; updated help text.
- `bal/gui/qt/window.py`, `bal/gui/qt/dialogs.py`, `bal/cli/controller.py`: pass
  `creation_date=Will.get_creation_time(...)`, call
  `ensure_check_alive_valid`, and handle `CheckAliveAfterDeliveryError` by
  aborting (never invalidating). The wizard .ics provider and the CLI guard use
  the shared resolver too.
- `bal/core/plugin_base.py`: the default relative Check Alive is now `"-30d"`
  (30 days BEFORE the 1-year delivery), preserving the historical default; the
  absolute default threshold follows the same sign rule.

**Tests:**

- `tests/test_core_checkalive.py` rewritten (both signs, delivery/creation
  resolution, validity gate, new exceptions, `Will.get_creation_time`).
- Updated `tests/test_group_i_basic_checkalive.py`, `tests/test_core_will.py`,
  `tests/test_gui_will_flows.py`, `tests/test_cli_controller_offline.py`,
  `tests/test_core_plugin_base.py`, `tests/test_core_input_rules.py`.

**Outcome:** DONE.

## 62. Remove BALQR and BC-UR v1/v2 — keep only BBQR

**Date:** 2026-10-03

**Goal:** Drop the BALQR, BC-UR v1 and BC-UR v2 QR wire formats and keep only
**BBQR** for will transfer.

**What changed:**

- `bal/core/animated_qr.py`: removed the BC32, bytewords-minimal, CRC-32,
  CBOR, xoshiro256/fountain codecs and the BALQR / UR v1 / UR v2 receive
  sessions; the module now implements only the BBQR codec plus the
  `AnimatedQrSession` facade and `detect_format` / `parse_for_detection`
  (BBQR-only).
- `bal/core/qrtransfer.py`: removed the BALQR framing helpers (`split_frames`,
  `parse_frame`, `assemble`, `encode_transfer_best`, the frame errors);
  kept the transfer-string codec (`encode_transfer` / `decode_transfer`) and
  the QR-size presets.
- `bal/gui/qt/dialogs.py`: the export widget no longer shows a Format selector
  and always emits BBQR; the import widget treats BBQR indexes as 0-based.
- Removed the "QR Code Size" setting from the plugin settings dialog; the
  QR-size selector now lives only in the export dialog, and its chosen value
  is remembered (persisted to `QR_CHUNK_SIZE`) for the next export.
- Android reader: `bal/core/*` copies re-synced, `bal/encoder.py` and the
  Kotlin bridges (`BalDecoder`, `QrEncoder`, `QrSendActivity`, `MainActivity`,
  `strings.xml`) reduced to BBQR; `tools/emitter.py` and
  `test_chain/verify_chain.py` updated.
- Tests: `test_core_animated_qr.py`, `test_core_qr_transfer.py` and
  `test_gui_qr_transfer.py` rewritten for BBQR only.
- Docs: README, COMPATIBILITY, HANDOFF and android/README updated; translation
  catalogs refreshed (removed format strings marked obsolete).

**Outcome:** DONE.

---

## 63. i18n review fixes (Truman's review of the translation PR)

**Date:** 2026-10-07

**What changed:**

- `make-release.sh`
  - Step 3 now also runs `tests/test_i18n.py`, `tests/test_translations.py`,
    `tests/test_cli_*.py` and `tests/test_import_will_details.py` (the i18n
    tests were never run by the release gate; the CLI and will-import tests
    were not either).
  - Checks up front that Babel is importable by `python3` and stops with
    "pip install babel" otherwise: the i18n tests (step 3) and `build_zip.py`
    (step 5) both need it, so a release no longer stops half-way.
- `AGENTS.md`: Babel must be installed in the runtime env used for releases.
- `pyproject.toml`: `[tool.ruff.lint.isort] known-first-party = ["bal"]`.
  WHY: the reported I001 in `tests/test_translations.py` is not reproducible
  from the repository root (ruff 0.15.11 and 0.6.9: clean), but it appears as
  soon as ruff classifies `bal` as third-party (then 20 files report I001).
  Removing the blank line would break the check where `bal` is first-party;
  declaring it makes the result the same on every machine.
- `bal/core/willexecutors.py` - `get_willexecutor_transactions` returns COPIES
  of the executor records. It used to write `txs`, `txsids` and the
  translated `broadcast_status` into the will item's own `we` dict, which is
  saved in the wallet (stored data depended on the UI language).
- `bal/core/will.py` - `WillItem` drops those transient fields when loading a
  will saved by an older version, and maps a translated leading "New"
  ("Nuovo", "Nuevo" - saved by i18n test builds) back to "New".
- `bal/gui/qt/window.py`, `bal/cli/controller.py`: the status history of a new
  will item is saved as the literal "New" (was `_("New")`).
- `tests/test_core_status_language_neutral.py` (new, 7 tests; 5 fail on
  `main`): English "New" stored, translated "New" mapped back, the mapping
  covers every catalog's translation of "New", the push helper does not
  modify the will item, old transient fields dropped on load.

**Scope:** this entry contains ONLY the i18n review fixes. The other fixes
prepared in the same session (invalidation fee, Check `KeyError`, cascade,
deselected executor) are not part of it and will be proposed separately.

**Verification (on `main` 827a1d1):**

- New test file `tests/test_core_status_language_neutral.py`: 5 of 7 fail on
  `main`, 7 of 7 pass with this change.
- Extended release test list (as above) plus the new test file: 446 passed,
  1 failed (`main`: 439 passed, same failure). The failure is pre-existing:
  `test_group_e_mock_karen7::test_e5` needs `tests/karen7`, a wallet file that
  is not in the repository.
- Whole `tests/` directory: 609 passed, 26 failed, 3 collection errors
  (`main`: 602 passed, and the SAME 26 failures and 3 errors, test by test).
  All pre-existing and unrelated: 5 need `tests/karen7` (2 failures and the
  3 collection errors); 21 in
  `test_auto_rebuild_on_new_tx.py`, `test_cli_autorebuild.py` and
  `test_cli_controller_offline.py` fail only when the whole directory runs
  together (they pass when each file runs alone); 3 audio tests need an audio
  device. `test_gui_export_dialogs.py::test_audio_import_receive_wiring` hangs
  forever, also on `main`, so it was deselected.
- `ruff check bal/ tests/`: the same 3 pre-existing I001 as on `main`
  (`dialogs.py`, `test_core_animated_qr.py`, `test_core_qr_transfer.py`),
  none new.

**Outcome:** DONE (pending Truman's review).

---

## 64. Invalidation pays the current network fee (2 blocks) instead of `baltx_fees`

**Date:** 2026-09-26 (ported to `main` 827a1d1 on 2026-10-07)

**Goal (owner request):** the will's `baltx_fees` (default 20 sat/vbyte) is
deliberately high because the inheritance transactions may be broadcast by the
will-executors years from now, when network fees may be much higher. The
on-chain INVALIDATION, instead, is sent TODAY: it spends the will's coins back
to the wallet so the old pre-signed inheritance transactions become unusable.
It must therefore pay the current network fee, not the inheritance rate. After
the invalidation the plugin rebuilds the new inheritance with `baltx_fees` as
before (unchanged).

**What changed:**

- `bal/core/will.py`
  - New constant `INVALIDATION_FEE_ETA_BLOCKS = 2` (owner decision: confirm
    within 2 blocks, Electrum's own default target).
  - New `Will.get_invalidation_fee_rate(wallet, fallback_fee_per_byte)`:
    returns Electrum's live estimate for 2 blocks
    (`network.fee_estimates.eta_target_to_fee`, sat/kvbyte → sat/vbyte), or
    the fallback (the will's `baltx_fees`, i.e. the previous behaviour) when
    there is no usable estimate: offline wallet, estimates not received yet,
    network error or an invalid value. Never raises.
  - `Will.invalidate_will`: uses that rate; the total fee is rounded UP to
    whole satoshis (network rates can be fractional); logs the chosen rate and
    its source (`network` / `fallback`). The `fees_per_byte` argument is now
    only the fallback, so every caller (Tools → Invalidate, wizard
    postpone/expired paths, automatic invalidation, CLI `bal_will_invalidate`)
    gets the new behaviour without being changed. Docstrings added.
- `tests/test_core_invalidation_fee.py` (new): rate selection (2-block target,
  network value used as is also above `baltx_fees`, fractional rates, fallback
  when offline / no estimate / error / invalid values) and the fee actually
  deducted by `invalidate_will` (network vs fallback, rounding up).

**Why no special case for an already-expired will:** discussed with the owner
and intentionally left out. Before its locktime the old inheritance tx cannot
enter a block or the mempool, so the invalidation has no competitor. RBF stays
enabled on the invalidation, so its fee can be raised from Electrum's History
("Increase fee") if it ever gets stuck.

**Verification:**

- `tests/test_core_invalidation_fee.py`: 12 passed (pytest and standalone).
- `make-release.sh` test list + `tests/test_cli_*.py`: 435 passed, 1 failed.
  The failure is pre-existing and unrelated: `test_group_e_mock_karen7::test_e5`
  needs `tests/karen7`, a wallet file that is not in the repository.
  `tests/test_import_will_details.py`: 17 passed.
- `ruff check` clean on the touched files.
- Live, Electrum 4.8.2 testnet, offline daemon: `bal_will_invalidate` →
  "invalidation fee: 20 sat/vbyte (fallback), total 2200 sat" (unchanged
  behaviour when offline).
- Real Electrum wallet + real `FeeTimeEstimates` (1 input, 110 vbytes):
  market 3 sat/vbyte → 330 sat; 1.5 → 165 sat; 0.4 → 110 sat (Electrum clamps
  to its 1 sat/vbyte minimum relay fee); 45 → 4950 sat; no 2-block estimate →
  fallback 20 sat/vbyte (2200 sat).
- API checked in Electrum 4.7.2, 4.8.0 and 4.8.2
  (`FeeTimeEstimates.eta_target_to_fee`, `network.fee_estimates`).

**Notes:** on regtest Electrum returns a fixed 150 sat/vbyte when it has no
estimate, so regtest invalidations will pay that rate.

**Outcome:** DONE (pending the owner's test of the ZIP).

---

## 65. Check crashed with `KeyError: '<txid>'` (stale "father" link in the will tree)

**Date:** 2026-09-27

**Symptom (owner's mainnet wallet, 23 will items, Electrum 4.8.2):** the Check
wizard stopped at "Checking your will" with `Error: 'a65a682f…'`, and the
window refresh logged "error while updating window" with the same txid:

```
File "bal/core/will.py", line 657, in check_invalidated
    or willtree[w.father].get_status("CONFIRMED")
KeyError: 'a65a682f2975f0dffd9eccce9392833f227badf617fc67815674736d594d4115'
```

**Root cause:** every will item stores its parent will tx in `father`, which is
persisted in the wallet (`"Father"` in `WillItem.to_dict`). `Will.add_willtree`
only ever SET it (`if not father: father = ...`) and never cleared it, so when
the parent tx was later removed from the will (rebuild, merge, replacement) the
child kept pointing to a txid that no longer exists. `check_invalidated` then
indexed the will with it. Reproduced on unmodified `main` (not related to #64).

**What changed:**

- `bal/core/will.py`
  - `Will.add_willtree`: resets `father` on every item before rebuilding the
    links, so the tree is always recomputed from the transactions themselves
    (a stale value loaded from the wallet is discarded). Docstring added.
  - `Will.check_invalidated`: defensive lookup - a father that is not (or no
    longer) in the will is treated as "no father" instead of raising.
- `tests/test_core_willtree_father.py` (new): stale father discarded / replaced
  by the real parent, no crash in `check_invalidated` (with and without the
  tree rebuild), real parent/child link built, child of an unconfirmed parent
  still skipped. 4 of the 6 tests fail on `main` and pass with the fix; the 2
  "normal behaviour" tests pass on both.

**Verification:** make-release test list + `tests/test_cli_*.py` +
`tests/test_import_will_details.py`: 458 passed, 1 failed (pre-existing,
unrelated: `test_group_e_mock_karen7::test_e5` needs the missing `tests/karen7`
wallet file). `ruff check` clean on the touched files.

**Found while writing the tests, NOT fixed here (reported to the owner):**
`Will.set_invalidate` iterates `children.items()`, but `add_willtree` builds
`children` as a LIST, so invalidating an item that has child will txs raises
`AttributeError: 'list' object has no attribute 'items'` (reproduced on
`main`). The existing tests in `test_core_will_invalidate.py` set `children` to
dicts by hand, so they do not see it.

**Outcome:** DONE (pending the owner's test of the ZIP).

---

## 66. Invalidation cascade crashed on real will trees (`children` is a list)

**Date:** 2026-09-27

**Problem (found while fixing #65, reproduced on `main`):**
`Will.set_invalidate` iterated `children.items()`, but `Will.add_willtree`
builds `children` as a LIST of `[child_id, input_idx, prevout_idx]`, so
invalidating a will tx that has child will txs raised
`AttributeError: 'list' object has no attribute 'items'`. The existing tests
set `children` to dicts by hand, so they never saw it.

**What changed:** `bal/core/will.py` - `set_invalidate` accepts both shapes
(list from `add_willtree`, dict from older data/tests) and only recurses into
children that are still in the will. `tests/test_core_willtree_father.py`: new
test `test_set_invalidate_cascades_to_children_built_by_add_willtree` (fails
on `main`, passes with the fix).

**Verification:** 459 passed, 1 failed (pre-existing missing `tests/karen7`);
`ruff check` clean.

**Outcome:** DONE (included in the owner's test ZIP of 2026-09-27).

---

## 67. Deselected will-executor: its tx can be deleted and is no longer pushed

**Date:** 2026-09-27

**Problem (owner, mainnet wallet):** the will had a signed tx for
`we.divinaly.support` whose push had failed ("Push failed", "Not on server").
After deselecting that executor, Check still reported the will as valid, the
row stayed, and "delete" in the context menu was disabled (valid + signed
txs were never deletable). The broadcast also still targeted the deselected
executor, because each will item keeps its own build-time copy of the
executor record (`selected` = True).

**What changed (owner chose option "B"):**

- `bal/gui/qt/lists.py` - `_can_delete`: a valid, signed tx is now deletable
  when its will-executor NEVER received it (not PUSHED and not CHECKED). It
  stays protected when the executor holds it (deleting it locally would not
  cancel it on the server) and when it has no executor (backup tx).
- `bal/core/willexecutors.py` - `get_willexecutor_transactions(...,
  current_willexecutors=None)`: when the current executor list is given, an
  executor that is no longer selected (or no longer listed) is skipped.
- `bal/gui/qt/window.py` (manual broadcast) and `bal/cli/controller.py`
  (`bal_will_broadcast`) pass the current list read from the config. The
  wizard already filtered by the current selection.
- Tests: `tests/test_gui_will_menu.py` (+3: deletable when never received,
  protected when pushed/checked, protected backup tx) and new
  `tests/test_core_deselected_executor.py` (4).

**Not changed (proposal for Truman, option "A"):** Check still does not treat
a tx of a deselected executor as "not coherent" (no automatic rebuild).

**Verification:** 483 passed, 1 failed (pre-existing missing `tests/karen7`);
`ruff check` clean on the touched files. Live (Electrum 4.8.2 testnet, CLI):
will built for 2 executors, one deselected → `bal_will_broadcast` targets only
the selected one (before the fix it targeted both).

**Outcome:** DONE (pending the owner's test of the ZIP).

**Porting note (entries 64-67):** first written on `main` 4a33116 as
58-61 (numbers then taken by the i18n and Check Alive work), re-applied on
`main` 827a1d1 on 2026-10-07 together with #63: extended release test list
489 passed, 1 pre-existing failure (missing `tests/karen7`); `ruff` no new
errors.

---

## 68. Visible rebrand: Safe21 Heartbeat (name, icon, links, credits)

**Date:** 2026-10-08

**Goal (owner, PLAN Phase 3):** the plugin shows itself as **Safe21
Heartbeat**, a fork of Bitcoin After Life (BAL). Only what the user sees
changes; the will logic and the internal identity (`bal` package, `bal_*`
config keys, wallet keys, data folder, CLI `bal_*` commands) do not.

**What changed:**

- `bal/manifest.json`: `fullname` "Safe21 Heartbeat"; `author` "Safe21
  (based on Bitcoin After Life by Svatantrya)"; new English `description`
  (backup wording chosen by the owner). Version unchanged (0.7.0).
- `bal/LICENSE`: keeps `Copyright (c) 2024 svatantrya` and adds
  `Copyright (c) 2026 Safe21` (MIT text unchanged).
- Icons `bal/icons/bal16x16.png`, `bal32x32.png` (same file names): teal
  rounded square, white heart, teal "21" with the glyphs of the Safe21
  wordmark (owner's choice "F"). Source SVG kept outside the repository
  (`brand/safe21-heartbeat-icon.svg` in the Safe21 project folder).
- Window titles: "Safe21 Heartbeat - Heirs / - Will-Executors / - Will
  Settings", the `get_window_title` prefix "Safe21 Heartbeat - {}", "Safe21
  Heartbeat Wizard Setup"; status-bar tooltip "Safe21 Heartbeat" (a brand,
  not translated); restart message.
- Settings dialog: support link `https://safe21.io` (was
  `bitcoin-after.life`) and a new grey line "Based on Bitcoin After Life
  (BAL) by Svatantrya — MIT licence" under the bottom row.
- Wallet labels written in Electrum's History: "Safe21 Heartbeat backup
  transaction" (was "BAL Inheritance transaction") and "Safe21 Heartbeat
  invalidation" (was "BAL Invalidate transaction"), in the GUI and the CLI.
  Checked: these labels are only written, never read back to recognise a
  transaction. Labels already in a wallet stay as they are.
- Calendar (.ics) defaults: "Safe21 Heartbeat - Will execution of
  $wallet_name" and "Safe21 Heartbeat will execution of $wallet_name ...";
  `PRODID` "-//Safe21//Safe21 Heartbeat Electrum Plugin/<version>". A value
  the user already saved in the config is kept.
- `bal/wallet_util/bal_wallet_utils_qt.py` (standalone helper): window title
  and its three messages.
- Module docstrings, `README.md`, `bal/README.md`: new name, tagline and the
  credit "Based on Bitcoin After Life (BAL) by Svatantrya — MIT licence".
- Catalogs: `pybabel extract` + `pybabel update --no-fuzzy-matching` (new
  texts start empty instead of inheriting an old translation marked fuzzy;
  the catalogs carry no fuzzy entries). Italian: the 10 new texts
  translated. Spanish: left untranslated (English shown) until Truman is
  asked (decision D9). Most of the catalog diff is updated source line
  references.
- Tests updated to the new defaults: `test_gui_will_flows.py` (history label
  of a rebuilt will), `test_group_c_settings.py` (calendar defaults).

**Not changed on purpose:** welist and default executor URLs
(`bitcoin-after.life`), the executor description "Bitcoin After Life Will
Executor" (it describes BAL's own server), the `HISTORY_LABEL` default
"BitcoinAfterLife inheritance transaction - {willexecutor}" (stored text used
by `Util._label_matches_history`, moves with the internal identity), CLI
texts and command names, log messages, `make-release.sh`, `docs/`, the
catalog header comments.

**Warning:** until the internal identity is renamed, this build and BAL use
the same package name and wallet keys: do not install both in the same
Electrum.

**Verification (Electrum 4.8.2 source, Python 3.11, WSL):**

- `ruff check bal/ tests/`: the same 3 pre-existing I001, none new.
- Whole `tests/` directory: 634 passed, 27 failed, 3 errors; the failure
  list is identical, test by test, to `main` before this change (all
  pre-existing: missing `tests/karen7`, tests that fail only when the whole
  directory runs, no audio/camera device).
- Scenario harness `A1`: OK.
- `build_zip.py` + `tests/external_zip_test.py`: the zip loads the way
  Electrum loads an external plugin.

**Outcome:** DONE (pending the owner's test of the ZIP).

---

## 69. Warning "an older will is still active" (read-only safety net)

**Date:** 2026-10-08

**Problem:** executors keep every transaction they ever received; the only way
to kill one is to spend its coins on-chain. When a postpone or an
invalidation goes wrong (BUG-2: Cancel at "Sign your will", then postpone;
BUG-3: "Tools > Invalidate" with several coins spends only some of them), an
executor still holds a transaction of an OLDER will with an EARLIER date that
can still be executed, while the panel shows the new, later date. Until now
nothing told the user.

**What changed (owner decisions W1–W5, all as proposed):**

- `bal/core/will.py`
  - New `Will.find_still_active_older_txs(will, wallet, current_locktime)`:
    returns the items that were sent (`PUSHED`/`CHECKED`; failed pushes are
    not reported, W2), are not `VALID`/`INVALIDATED`/`CONFIRMED`/`MEMPOOL`,
    are dated EARLIER than the current will, and are still executable. It
    reports nothing while `wallet.is_up_to_date()` is False (offline or
    still syncing: avoids false alarms) and never raises.
  - New `Will.get_current_will_locktime` (earliest `VALID` locktime, else
    the settings date) and `Will._tx_still_executable` (no input spent by a
    broadcast tx; a child follows its parent will tx).
  - Read-only: nothing is built, signed, sent, invalidated, and no status
    changes.
- `bal/gui/qt/dialogs.py` (Check dialog): at the end of every Check (W1),
  after "All done", a red row "Warning: an older will is still active."
  with executor, old date, current date and the remedy; the same text in a
  popup when the dialog is closed (before the next-steps hint). Basic and
  advanced mode, also with auto-sign. No "fix now" button (W3).
- Catalogs: 3 new texts, translated in Italian and Spanish (the owner asked
  to translate Spanish too; this replaces decision W4/D9 from now on).
- Tests: `tests/test_core_still_active_warning.py` (14: BUG-2 state
  reported; correct postpone, fee change with later date, spent coin, failed
  push, confirmed/mempool, current tx not reported; settings date when no
  valid item; child follows parent; not synchronized is silent; never
  raises; sorting) and `tests/test_gui_still_active_warning.py` (4: row,
  popup, duplicate lines, HTML escaping, failure swallowed).

**Verification (Electrum 4.8.2 source, Python 3.11, WSL):**

- Scenario matrix (35 scenarios, 119 Checks per run) with an oracle check
  after EVERY Check: "an executor holds a still-executable tx dated earlier
  than the current will" vs "the warning is shown". 6 runs (2-coin
  scenarios depend on the coin order): 714 Checks, 0 mismatches. The
  warning appears in B2, B5, B6, B5fix, B5fix2 (also when it fails by
  chance), B5self2 (the Check before "Send Max"), B2adv; in none of the OK
  checks; not in the BUG-1 scenarios (A7, A9, A13, A7adv), as expected.
  With the wallet not synchronized: never shown.
- BUG-3 probes plus a final Check, 12 runs each: 24/24 correct ("after
  Cancel": warning in the 8 runs where older txs survived, silent in the 4
  where the invalidation worked; "without Cancel": surviving txs are dated
  later than the current will, no warning).
- Remedy in the message verified on this code: B5self2 (2 coins) — after
  sending the whole balance to an own address and pressing Check, the
  warning disappears and the real delivery date equals the intended one.
- `ruff check bal/ tests/`: the same 3 pre-existing I001, none new.

**Known limits:** the plugin only knows what the wallet knows (an executor
that lost a tx may still be listed; transactions exported by hand are not
tracked); all checks so far on a simulated wallet, not mainnet.

**Outcome:** DONE (pending the owner's test of the ZIP).

---

## 70. Spanish translations of the rebrand texts

**Date:** 2026-10-08

**Why:** #68 left its 10 new texts untranslated in `es_ES` (English shown),
waiting for Truman (decision D9). The owner then decided that Claude Code
translates Spanish too (see #69).

**What changed:** `bal/locale/es_ES/LC_MESSAGES/bal.po` only: the 10 texts
of #68 (window titles, wizard title, calendar defaults, support-link tooltip,
credits line, restart message), in the style of the previous BAL Spanish
translations. The Spanish catalog is now complete (0 untranslated, 0 fuzzy).
No code change.

**Verification:** `tests/test_translations.py` and `tests/test_i18n.py`
pass; whole `tests/` directory: same results as `main`; the zip builds and
loads as an external plugin.

**Outcome:** DONE (pending the owner's check of the ZIP).

---

## 71. F3 (BUG-3): the invalidation spends every coin of every will transaction

**Date:** 2026-10-08

**Problem:** `Will.invalidate_will` took only ONE input per VALID will
transaction (`filtered_inputs` was keyed by will id) and ignored the
transactions that were no longer VALID. With two or more coins, which coin was
left out depended on the coin order: after "Tools > Invalidate" an older
transaction already sent to an executor could stay executable (BUG-3 in
`FINDINGS-2026-10-08`, scenario B5fix2 and the two BUG-3 probes).

**What changed (owner's choice "B"):**

- `bal/core/will.py` - `invalidate_will` now spends EVERY wallet coin used as
  an input by ANY will item (current, replaced, invalidated...). Wallet coins
  that no will transaction uses are not touched; inputs that spend another
  will transaction's output are not wallet coins and are skipped. WHY not only
  the VALID / sent items: the statuses are the plugin's bookkeeping and are
  wrong exactly in the BUG-2 cases; only spending a coin really kills the
  transactions that use it. In practice the result is almost always the same
  as before plus the missing coins, since the will already uses all the
  wallet's coins.
- Consequence: when no item is VALID but some will coin is still in the
  wallet, an invalidation is now built instead of `None` (e.g. Check Alive
  passed on a will whose items were all marked invalidated without an
  on-chain spend). `tests/test_core_will_invalidate.py`:
  `test_no_valid_items_returns_none` became
  `test_unspent_coin_of_non_valid_item_is_still_spent`.
- New `tests/test_core_invalidate_all_coins.py` (5): a 2-input transaction
  spends both coins; the coin of an older sent (REPLACED) item is spent; the
  coin of an item marked INVALIDATED but still unspent is spent; a wallet
  coin outside the will is not touched; coins already spent are skipped. 4 of
  the 5 fail on `main` before this change.

**Verification (Electrum 4.8.2 source, Python 3.11, WSL):**

- BUG-3 probes followed by "Tools > Invalidate", 12 runs each: 24/24 with NO
  transaction left executable at the executors (before: the "after Cancel"
  probe left older ones alive in 8 of 12 runs).
- Scenario matrix, 6 runs: B5fix2 OK 6/6 (before: depended on the coin
  order); every other verdict unchanged; the #69 warning still matches the
  oracle on all 714 Checks.
- Real chain (private regtest Bitcoin Core 31.1 node, no real network or
  coins; the owner asked Claude Code to test instead of a testnet wallet):
  real coins, the invalidation sent to the node and mined, the executors'
  transactions judged by the node's UTXO set. With this change: 12/12 runs
  with 0 executable transactions left after "Tools > Invalidate". On `main`
  before it, the same "after Cancel" case left 2 executable in 3 of 6 runs.
  Then, with the private chain's clock moved past the will date, the new
  will transaction was refused before the date (`non-final`), accepted
  after it, executed and paid the heirs (60 % / 40 % as configured); the
  other executor's copy and every old transaction were refused
  (`missing-inputs`).
- `ruff check bal/ tests/`: the same 3 pre-existing I001, none new.

**Outcome:** DONE (verified on a private regtest chain instead of the owner's
ZIP test).

---

## 72. F2 (BUG-2): postponing after a Cancel now invalidates the old sent will

**Date:** 2026-10-09

**Problem (traced in the harness, scenario B5):** a signed will is sent to the
executors; a fee (or heir) change rebuilds it and the user presses Cancel at
"Sign your will" (the old sent txs become REPLACED, the new ones stay
unsigned); then the delivery date is moved LATER and Check is pressed. The
unsigned txs are dropped (marked INVALIDATED, the mechanism introduced by BAL
`251fd93`), the will has no VALID tx left and is simply rebuilt at the new
date. The postpone check (`check_willexecutors_and_heirs`) only looks at
VALID txs, so the old REPLACED txs, still held by the executors with the
EARLIER date, were never invalidated. The panel showed the new date.

**What changed:**

- `bal/core/will.py`
  - New `Will.check_sent_txs_not_postponed(will, heirs, wallet)`: for every
    item that is not VALID, was sent (`PUSHED`), is not
    `CONFIRMED`/`MEMPOOL` and is still executable (no input spent by a
    broadcast tx, `_tx_still_executable` from #69), the delivery date now
    requested for its heirs is resolved with
    `Util.resolve_locktime_against_tx` (the same rule as for VALID items,
    relative recipes included); if it is LATER than the date frozen in the
    tx, it raises `WillPostponedException`, so the existing postpone path
    invalidates on-chain first (all coins, #71) and then rebuilds. A status
    alone (`INVALIDATED` without a real spend) does not exclude a tx. Heirs
    not in the old tx are skipped. Without a usable wallet nothing is
    decided.
  - `Will.is_will_valid` calls it right after `check_will` and before the
    VALID-only check.
- New `tests/test_core_postpone_after_cancel.py` (10): the BUG-2 state is a
  postpone (on `main` it was a plain rebuild: `NotCompleteWillException`);
  same date, earlier date, spent coin, never sent, confirmed, VALID items
  are not; status INVALIDATED with an unspent coin still counts; heir added
  plus later date is a postpone; no wallet, no decision.

**Verification (Electrum 4.8.2 source, Python 3.11, WSL):**

- Scenario matrix, 6 runs: B2, B2adv, B5, B6 now OK 6/6 (were DANGER);
  every other verdict unchanged. After no Check is an older tx still
  executable (oracle 0 on 714 Checks), so the #69 warning no longer fires.
  Remaining DANGER: A7, A9, A13, A7adv (BUG-1, F1).
- Invalidations per scenario: one more only in the BUG-2 scenarios (B2,
  B2adv, B5, B5fix, B5fix2, B5self2, B6: exactly one, the needed one); none
  added to the fee/heir/earlier-date scenarios (no useless fees).
- Real chain (private regtest Bitcoin Core 31.1 node): the B5fix2 steps,
  oracle right after the postponing Check: 0 older txs executable in 3/3
  runs (on `main` before this change: 2 in 3/3). The automatic invalidation
  was accepted by the node and mined; the new will is `non-final` before its
  date, accepted after it, and pays the heirs.
- `ruff check bal/ tests/`: the same 3 pre-existing I001, none new.

**Outcome:** DONE (verified on a private regtest chain; the owner asked
Claude Code to test instead of a testnet wallet).

---

## 73. F1 (BUG-1): a postpone made together with a fee or heir change is detected

**Date:** 2026-10-09

**Problem (traced in the harness, scenarios A7 and A13):**
`check_willexecutors_and_heirs` stopped at the first difference it met in a
VALID tx: a changed fee rate (`TxFeesChangedException`) or a changed heir
address/amount (`HeirNotFoundException`) was reported BEFORE the delivery
date was compared. When the date was moved LATER together with a fee or heir
change, the will was rebuilt as a normal update, one day before the old
signed tx (the anticipate mechanism), and the settings date was overwritten
with that date. The old signed tx stayed in force; on the real chain the
node accepted transactions dated 2032 while the user had asked for 2033.

**What changed:**

- `bal/core/will.py`
  - New `Will._postponed_heir(w, heirs)`: for every heir present both in a
    will item and in the current settings (by name), the requested date is
    resolved with `Util.resolve_locktime_against_tx` (relative recipes
    included); returns the first heir whose requested date is LATER than
    the tx locktime. Address or amount changes do not matter here.
  - `check_willexecutors_and_heirs` first checks every VALID item that is
    signed (`COMPLETE`) or sent (`PUSHED`) with it and raises
    `WillPostponedException`: the old tx is invalidated on-chain first (all
    coins, #71), then the will is rebuilt with the new date and the new fee
    or heirs.
  - `check_sent_txs_not_postponed` (#72) uses the same helper.
- New `tests/test_core_postpone_with_other_changes.py` (9): fee change plus
  later date, heir amount change plus later date, signed-not-sent plus later
  date are postpones (the first three failed on `main`: wrong exception);
  fee change alone, heir change alone, fee change plus earlier date, an
  unsigned will with a later date are not; a plain postpone and an
  unchanged will keep working.

**Known side effect (accepted by the owner):** scenario B2 (fee and date
later, Cancel at "Sign your will", then the fee set back, Check). The first
Check now correctly invalidates the old will and prepares the new one at the
later date; the user cancels the signature. The second Check sees a fee
change on that unsigned will and applies the plugin's normal "one day
earlier" anticipation, so the will ends at the requested date MINUS ONE DAY
(2033-09-22 instead of 2033-09-23). No older tx stays executable, the amounts
are correct, no extra fee; the panel shows the new date and the Check says
the date was moved one day earlier. In practice: if the user stops
refreshing, the heirs can receive the funds one day earlier. Removing the
anticipation for unsigned wills was tried in a scratch copy and rejected:
the anticipation is how the plugin decides which tx replaces which, and
without it two versions of the will stayed VALID.

**Verification (Electrum 4.8.2 source, Python 3.11, WSL):**

- Scenario matrix, 6 runs: A7, A9, A13, A7adv now OK 6/6 (were DANGER); B2
  and B2adv "DANGER" by one day as described above; every other verdict
  unchanged; after no Check is an older tx executable (oracle 0 on 714
  Checks). One invalidation more only in the BUG-1/BUG-2 scenarios, none in
  the fee/heir/earlier-date ones.
- Real chain (private regtest Bitcoin Core 31.1 node): A7 with this change:
  invalidation accepted and mined, nothing executable before the requested
  date, settings kept at 2033-09-23, the new will executes and pays the
  heirs (2/2 runs); on `main` before it: txs dated 2032-09-22 and
  2032-09-23 executable, settings overwritten (2/2). B2: one day earlier as
  described (2/2), nothing older executable.
- `ruff check bal/ tests/`: the same 3 pre-existing I001, none new.

**Outcome:** DONE (verified on a private regtest chain; the owner asked
Claude Code to test instead of a testnet wallet).

---

## 74. F5: typo in the Check dialog's phase-2 error handler

**Date:** 2026-10-09

**Problem:** `BalBuildWillDialog.on_error_phase2` (`bal/gui/qt/dialogs.py`)
called `self.bal_window.upade_all()`. `BalWindow` has no such method: an
error while signing or sending the will raised `AttributeError` inside the
error handler itself, so the error row was never shown and the window was
not refreshed. Found by reading (BUG-8 in the scenarios report). Also present
in BAL `main` 346aeee (2026-10-08) and in the released v0.6.0 and v0.6.1.

**What changed:** `upade_all()` -> `update_all()`, docstring added. New
`tests/test_gui_phase2_error.py`: the handler refreshes the window and shows
the error row (it failed with `AttributeError: ... 'upade_all'` before).

**Verification:** `ruff` (same 3 pre-existing I001), whole `tests/` directory
(same failures as `main`), scenario matrix (unchanged), zip builds and loads.

**Outcome:** DONE.

---

## 75. F4/1 (BUG-4): clear message when the delivery date is in the past

**Date:** 2026-10-09

**Problem:** with a delivery date in the past (scenario A6, and C1 after an
expired will was invalidated) the Check said that an heir was "not covered by
the current will" and then "No delivery date left to build: every heir's date
is already covered by the existing will". Neither names the real cause.

**What changed (text approved by the owner):**

- `bal/core/will.py`: new `Will.past_delivery_date(will_settings, now=None)`
  — the settings delivery date if it is not later than now (relative
  recipes are always future), else `None`; never raises.
- `bal/gui/qt/dialogs.py`: new `_past_date_message()`; when it applies, the
  "Checking your will" row shows "The delivery date (YYYY-MM-DD) has already
  passed. Choose a future date, then press Check again." and the building row
  only "Skipped" (instead of the misleading "no delivery date left" text).
  Future dates: unchanged.
- Catalogs: the new text in Italian and Spanish.
- New `tests/test_msg_date_in_past.py` (7).

**Verification:** harness A6 and C1 show the new message; scenario matrix
verdicts unchanged; `ruff` (same 3 pre-existing I001); whole `tests/`
directory (same failures as `main`); zip builds and loads.

**Outcome:** DONE.

---

## 76. F4/2 (BUG-5): heirs' amounts — no false alarm, say what happens, stop when fixed amounts do not fit

**Date:** 2026-10-09

**Problems (all traced in the scenario harness):**

1. **False alarm.** "In the inheritance process, the entire wallet will
   always be fully emptied. Your settings require an adjustment of the
   amounts" flashed in 12 Checks of the scenario matrix (A6, B1, B2, B4, B5,
   B5fix, B6, B7, C1, B2adv) although no amount had changed: with a delivery
   date in the past the heirs are left out of the percentage sum (0 % instead
   of 100 %, `PercAmountException`); right after a Cancel no spendable coin is
   visible for a moment (the wallet-local copy of the unsigned will still
   holds the coins; the build frees them just after), so the fixed-amount
   check fails with a balance of 0.
2. **Misleading text.** With percentages that do not add up to 100 % (e.g.
   50 % + 40 %) the build already rescales them in proportion (55.6 % /
   44.4 %) and empties the wallet; the warning told the user to adjust the
   amounts, and was overwritten a moment later.
3. **Silent loss.** With fixed amounts larger than the balance the build
   scaled them down and then left the percentage heirs out (2,000,000 sat +
   50 % on a 1,000,000 sat wallet: one heir got everything, the other
   nothing), or with fixed heirs only (800,000 + 600,000 sat) paid them about
   71 % and sent 284,628 sat back to the owner's wallet as change, where no
   heir could use it. The Check ended with "Building your will: Ok".

**What changed (texts and the stop approved by the owner):**

- `bal/gui/qt/dialogs.py`
  - `_amount_warning_is_false_alarm(available_utxos)`: no warning when the
    delivery date is in the past (explained by #75) or no spendable coin is
    visible (an empty wallet is reported by the build step).
  - `_amounts_message(info)`: one sentence on its own row (not overwritten):
    "The heirs' percentages add up to 90%: the will always empties the whole
    wallet, so the shares are recalculated in proportion (alice 55.6%, bob
    44.4%)." (variant "of what remains after the fixed amounts" when fixed
    heirs exist); and, red, "The fixed amounts (2,000,000 sat) exceed the
    balance available for the heirs (999,000 sat): the heirs with a percentage
    (bob) would receive nothing. Reduce the fixed amounts." (variant without
    percentage heirs). The balance shown is the wallet balance minus the
    highest will-executor base fee, the amount the check really compares.
  - Fixed amounts that do not fit: "Checking variables: Stopped", the red
    row, and the Check ends with the Close button (new phase-1 signal
    `fixed_amounts_exceed`), no will is built.
  - New build-failure text for `FIXED_EXCEEDS_BALANCE` (see below).
- `bal/core/will.py`: new `Will.describe_amounts(...)` (GUI-free): what the
  build does with the amounts (`percent` rescaled / `fixed_exceeds` / None).
- `bal/core/heirs.py`: new `FixedAmountsExceedBalanceException`;
  `Heirs.prepare_lists` raises it instead of scaling the fixed amounts down
  when they exceed the balance left for the heirs (or leave nothing for the
  percentage heirs); `buildTransactions` records `FIXED_EXCEEDS_BALANCE`.
  So the automatic rebuilds (new coin, wallet close) and the CLI stop too.
- `bal/i18n.py`: `number_separators()` — "55,6%" / "2.000.000" for
  Italian, Spanish and other decimal-comma languages, "55.6%" / "2,000,000"
  otherwise.
- Catalogs: the new texts in Italian and Spanish.
- New tests: `tests/test_msg_amounts_false_alarm.py` (4),
  `tests/test_core_amounts_explained.py` (16: the stop in `prepare_lists`,
  `describe_amounts`, the messages and their number format).

**Verification:** harness cases — 50 % + 40 % and 60 % + 60 %: rescaled,
new message kept visible; fixed 100,000 + 50 %: message with "what remains
after the fixed amounts"; fixed 100,000 + 100 %: no message; fixed 2,000,000
+ 50 % and 800,000 + 600,000: "Stopped", red message, no will built. Scenario
matrix: the false alarm no longer appears (0 of 119 Checks, was 12), verdicts
unchanged. `ruff` (same 3 pre-existing I001); whole `tests/` directory (same
failures as `main`); zip builds and loads.

**Outcome:** DONE.

## 77. F4/3: "Checking your will" row — name the real reason

**Date:** 2026-10-09

**Problems (traced in the scenario harness):**

1. **Heirs.** One sentence covered every heir change: 'Heir "x" is not
   covered by the current will (it was added or removed, or its delivery
   date changed)'. It was wrong for an amount change (A12 said "its delivery
   date changed"), and with an heir added together with new shares for the
   others (A10: carol added, alice and bob 40 %) it named alice, the first
   heir whose share changed, not carol.
2. **After an invalidation** (postpone A4, B4, B6): once the old will is
   invalidated the Check runs again and said "The will contains no valid
   transaction: a new will must be prepared." True, but alarming.
3. **After a Cancel at signing** the next Check discards the unsigned will
   and said "The will contains no valid transaction" (B1) or 'Will-executor
   "https://we0.test" is not covered by the current will' (B4): misleading.
4. **New funds received** were reported as "no valid transaction" instead of
   "The wallet contains funds that the current will does not cover yet": the
   dialog told the two apart with `e.args`, but `WillException` never fills
   `e.args`, so that branch never ran.

**What changed (texts approved by the owner; owner decision: distinct heir
messages, replacing the earlier "too fine-grained" choice):**

- `bal/core/will.py`
  - `HeirNotFoundException(heir, change=None)`: `change` is one of
    `removed`, `added`, `address`, `amount`, `date` (constants on the
    class); `str(e)` is still the heir name, so the other callers (main
    window, CLI) are unchanged.
  - New `Will._heir_change(name, heir, will_only_valid)`: compares the
    settings entry with the first VALID item carrying the heir.
  - `check_willexecutors_and_heirs`: among the heirs not covered, an added
    heir is reported before a changed one. Same exception as before, same
    order with respect to `NoHeirsException`; nothing else changes.
- `bal/gui/qt/dialogs.py`
  - `_check_failure_message`: 'Heir "bob" was removed / Heir "carol" was
    added: preparing a new will.', 'The delivery date / amount / address of
    heir "alice" changed: preparing a new will.'; the generic sentence stays
    for an exception without `change`.
  - New `_rebuild_reason_message(e)`, used before `_check_failure_message`:
    right after this dialog broadcast an invalidation, "Old will
    invalidated: preparing the new will with the new date." (postpone) or
    "... with the time period you set." (Check Alive threshold); after a
    Cancel at signing, "The will prepared earlier was not signed: preparing
    it again." — only in place of the two misleading sentences above (for
    will-executors, only one the unsigned will had); a real change (fee,
    heirs, new will-executor, new funds) keeps its own message.
  - New `_unsigned_will_executors()` (snapshot taken before `check_will`,
    which discards the unsigned items) and `_no_valid_transaction(e)` (fixes
    problem 4).
- Catalogs: the new texts in Italian and Spanish.
- New tests: `tests/test_msg_rebuild_reasons.py` (14).

**Verification:** harness — A10 'Heir "carol" was added', A11 'Heir "bob" was
removed', A12 'The amount of heir "alice" changed', A5 'The delivery date of
heir "alice" changed', A4 / B4 / B6 "Old will invalidated: preparing the new
will with the new date.", B1 / B4 after the Cancel "The will prepared earlier
was not signed: preparing it again.", a second coin received "The wallet
contains funds that the current will does not cover yet". Scenario matrix x6
(119 Checks per run): verdicts unchanged (only the known B2, B2adv, C3adv),
#69 oracle 0 mismatches. The "time period you set" text is not reached in
the matrix: in C2adv the Check stops after the Check Alive invalidation
(unchanged behaviour, Phase 5b). `ruff` (same 3 pre-existing I001); whole
`tests/` directory (same failures as `main`); zip builds and loads.

**Outcome:** DONE.

## 78. F4/4: command line and "Prepare" popup name the real reason

**Date:** 2026-10-09

**Problem:** #77 made the Check dialog name the real reason a will must be
prepared again, but two other places still used the old generic texts:

- the command line: `bal_will_check` answered "Found CHANGES to the DATE or
  the HEIRS, a new WILL must be prepared", "Transaction fees are changed" or
  "Will-Executor not present" (without saying which), and `bal_will_prepare`
  rebuilt the will without saying why;
- the main window ("Prepare"): a popup with a vague title ("Txfees are
  changed", "Found CHANGES to the DATE or the HEIRS") followed by the raw
  exception text (e.g. "14:  13") and "will have to be built".

**What changed (approved by the owner):**

- New `bal/core/messages.py` (GUI-free): `will_change_message(e)` and
  `no_valid_transaction(e)`, moved unchanged from the Check dialog, so the
  dialog, the main window and the command line share one set of sentences
  (the existing Italian and Spanish translations apply as they are).
- `bal/gui/qt/dialogs.py`: `_check_failure_message` and
  `_no_valid_transaction` delegate to it; four imports no longer used removed.
- `bal/gui/qt/window.py`: the "Prepare" popup shows the same sentence, for
  the same cases as before (heirs, fee rate, will-executor); no popup is
  added for the other cases.
- `bal/cli/controller.py`: `_user_facing` uses the same sentence for the
  "will no longer coherent" family; `NoWillExecutorNotPresent` keeps its CLI
  text, because `build_will` raises it too for "nothing selected", where the
  Check sentence would be wrong. `prepare_will` adds a `reason` field to the
  `rebuilt` result (an extra field: existing callers are not affected). The
  two dialog-only reasons of #77 (after an invalidation, after a Cancel at
  signing) do not exist on the command line.
- Catalogs refreshed (texts moved, old popup texts removed).
- New tests: `tests/test_cli_clear_reasons.py` (7; they pin the mainnet
  network, see 5c), `tests/test_gui_prepare_reason.py` (3).
- `tests/test_gui_will_flows.py`: the anticipated-rebuild test looked for
  any message containing "delivery date" to catch the NO_FUTURE_DATE error;
  the popup now rightly says "The delivery date of heir ... changed", so it
  looks for the error text itself.

**Verification:** command line - `bal_will_check` after an heir is added answers 'Heir
"carol" was added: preparing a new will.'; `bal_will_prepare` returns the
same `reason`, and "The wallet contains funds that the current will does not
cover yet..." after a new coin. Main window - the Prepare popup shows the
same sentences (heir added, both fee rates); no popup for new funds, as
before. Check dialog - the #77 harness cases show the same texts as before.
Scenario matrix x6 (119 Checks per run): verdicts unchanged (only the known
B2, B2adv, C3adv), #69 oracle 0 mismatches. `ruff` (same 3 pre-existing
I001); whole `tests/` directory (same failures as `main`); zip builds
and loads.

**Outcome:** DONE.

## 79. 5b/1: Check Alive passed — ask to renew, show the real delivery date

**Date:** 2026-10-09

**Problems (traced in the scenario harness):**

1. **A passed Check Alive left no will in force.** Advanced mode, delivery in
   200 days, Check Alive 180 days before it; 25 days later the owner presses
   Check: the plugin invalidated the will on-chain (1,430 sat at 13 sat/vB)
   and then stopped on "Checking variables: Wait" without preparing the new
   will. Every later Check stopped the same way, with no message, while the
   will list still showed the old items as valid. Cause: after the
   invalidation, `date_to_check` was still anchored to the old (now
   invalidated) items, so the passed Check Alive was found again. The same
   happened at wallet close, and the automatic rebuild invalidated silently.
2. **Wrong invalidation on a settings change.** A will in force, then the
   delivery moved to 90 days ahead with a 180-day Check Alive: the plugin
   paid an invalidation (moving a date earlier must never need one) and
   stopped as above. A first will in the same situation stopped on "Wait"
   with no explanation.
3. **Wallet open.** The Check Alive help text promised a question when the
   wallet opens; nothing in the code did it.
4. **The delivery date next to a relative setting moved every day.** Next
   to "4y" the field showed "today + 4 years", while the will in force keeps
   the date frozen in the signed transaction.

**What changed (owner decisions: ask to renew, never invalidate on its own;
the question also when the wallet opens; delivery stays Raw or Date):**

- `bal/core/checkalive.py`: new `check_alive_renewal(...)` (decides only):
  `None` when the Check Alive has not passed, else whether the will can be
  renewed with one click and, if not, why (`expired`: past the delivery,
  invalidated as before; `fixed_date`: the owner must choose a later date;
  `still_past`: a renewed will would have its Check Alive in the past too;
  no will in force). Renewing relies on an existing rule: with no will in
  force a relative delivery ("4y") is counted from today.
- `bal/core/will.py`: new `Will.refresh_invalidated(...)` (the detection
  `check_will` already runs, done before the dates are computed) and
  `Will.planned_delivery(...)` (the real delivery date).
- `bal/core/messages.py`: the question and the reasons (texts approved by
  the owner): "Are you still here? The Check Alive date (...) has passed.
  Renew your will: the new delivery date will be ... (today + 4 years). The
  old will is invalidated on the blockchain (this costs a miner fee) and the
  new one is prepared right away. Not now: nothing changes, your current
  will stays valid."
- `bal/gui/qt/dialogs.py` (Check and wallet close): a passed Check Alive
  asks "Renew" / "Not now". Renew: the old will is invalidated and the new
  one is prepared in the same Check ("Old will invalidated: preparing the
  new will with the time period you set."). Not now: nothing is done. When
  a one-click renewal is not possible: "Checking variables: Stopped" and the
  reason, nothing invalidated. A will already past its delivery is still
  invalidated at once (Electrum's classic window). The task #21 loop guard
  also covers the renewal (never invalidate twice).
- `bal/gui/qt/window.py`: `init_class_variables` marks the items already
  invalidated on-chain first (fixes problem 1); `check_alive_on_open` (3 s
  after the wallet opens, advanced mode, a will in force),
  `offer_check_alive_renewal` (also used by Prepare), `ask_renew_will`
  ("Not now" is the default button); the automatic rebuild no longer
  invalidates for a passed Check Alive (it still does for an expired will).
- `bal/gui/qt/widgets.py`: the date next to the delivery field is the real
  one (the will in force, or the will that will be prepared, see tooltip).
- `bal/gui/qt/plugin.py`: `load_wallet` schedules the wallet-open check.
- `bal/cli/controller.py`, `bal/cli/commands.py`: the same refresh before
  the dates; `bal_will_autorebuild` returns `check_alive_passed` (with how
  to renew by hand) instead of an invalidation; an expired will is still
  invalidated as before.
- Catalogs: the new texts in Italian and Spanish.
- Tests: new `tests/test_check_alive_renew.py` (22); two tests that expected
  the automatic invalidation now expect the report
  (`test_auto_rebuild_on_new_tx.py`, `test_cli_controller_offline.py`).

**Verification:** harness (advanced mode, Check Alive 180 days before the
delivery) - relative delivery, 25 days later: "Renew" invalidates and
prepares the new will (today + 200 days) in ONE Check, the follow-up Check
is "Ok"; "Not now": nothing done, the old will stays executable, the next
Check asks again; Renew chosen at wallet open: same result. Fixed delivery:
"Stopped" + "choose a later delivery date", nothing invalidated. First will
with a delivery closer than the Check Alive, and delivery moved to 90 days
ahead: "Stopped" + reason, no invalidation (was one paid invalidation and
"Wait"). Private regtest node (`real_renew.py`): the node accepted and
mined the invalidation, both old transactions dead (missing-inputs), the new
will dated today + 200 days is non-final before that date, accepted after
it, and executed it pays the heirs 60/40. Scenario matrix x6 (119 Checks per
run): verdicts unchanged (only the known B2, B2adv, C3adv; C3adv still ends
with no will in force because its Check Alive is saved as a fixed date: #80
converts it), #69 oracle 0 mismatches; C2adv no longer invalidates (was 1,
leaving no will). `ruff` (same 3 pre-existing I001); whole `tests/`
directory: 27 failed as on `main` (the two renamed tests replace their old
versions in that list; they pass when run alone, like before - see 5c);
zip builds and loads.

**Outcome:** DONE.

## 80. 5b/2 (D14): Check Alive as "N days before the delivery"

**Date:** 2026-10-09

**Problem:** in advanced mode the Check Alive could be a fixed date, a
positive "N days after the will was created" or a negative "N days before
the delivery" (RAW). Only the last one follows the delivery when it moves;
the other two stayed behind, and scenario C3adv (a will expired, then a later
delivery chosen) ended with NO will in force. The default value was itself a
fixed date (30 days before a delivery one year ahead, saved as a date), so
most advanced users had the problematic form. The RAW field also asked the
user to type a sign.

**What changed (owner decisions D14, D1 = spin box in days, D2 = keep the
same date):**

- `bal/core/checkalive.py`: new `check_alive_days(...)` and
  `normalize_check_alive(...)`: the saved value becomes `"-Nd"` keeping the
  same Check Alive date, expressed as days before the delivery the will
  really uses (`"-1y"` -> `"-365d"`; a fixed date or a positive value ->
  `delivery - date`, rounded to whole days); a date already passed but before
  the delivery is kept (it is a passed Check Alive: the owner is asked to
  renew, #79); anything that cannot be expressed so (on/after the delivery,
  more than 3650 days, unreadable) becomes the default. New
  `will_executable_now(...)`. Constants: default 180 days, maximum 3650.
- `bal/core/plugin_base.py`: default Check Alive `"-180d"` (about six
  months) instead of a fixed date 30 days before the delivery.
- `bal/gui/qt/window.py`, `bal/cli/controller.py`: the conversion runs when
  the will is loaded and before every check, and is saved once (a no-op for
  a `"-Nd"` value).
- `bal/gui/qt/widgets.py`: `ThresholdTimeWidget` is now one spin box: "180
  days before the delivery (≈ 6 months) → 2027-04-19"; arrows step 30 days,
  1..3650, the number can be typed; the date is red when already past; new
  help text and tooltip. No Raw/Date selector and no sign to type any more.
  It keeps the old interface (`get_value()` returns `"-Nd"`), so the
  toolbars, the wizard and the calendar reminders are unchanged.
- `bal/gui/qt/dialogs.py`: with the Check Alive following the delivery, a
  delivery moved later than the Check Alive margin made the old will read as
  "expired" against the new Check Alive date: the Check said "Your will has
  expired", opened Electrum's invalidation window and needed a second Check.
  When the old will cannot be executed yet (advanced mode) this is now the
  postpone path: invalidate and prepare the new will in the same Check
  ("Old will invalidated: preparing the new will with the new date."). A
  will really past its delivery is still invalidated at once as before.
- Catalogs: the new texts in Italian and Spanish.
- Tests: new `tests/test_check_alive_days.py` (19); the two default-value
  tests of `tests/test_core_plugin_base.py` now expect `"-180d"`.

**Verification:** spin box rendered offscreen: "180 days before the delivery (≈ 6
months) → 2027-04-19", a past date in red, read-only without arrows.
Scenario matrix x6 (119 Checks per run): C3adv is now OK (its fixed Check
Alive is converted to "-30d" and follows the later delivery); A4adv/A7adv
postpone in one Check with the right message; the only verdicts not OK are
the known B2 and B2adv (one day earlier, accepted); #69 oracle 0
mismatches. Check Alive scenarios of #79 unchanged, and a fixed delivery
moved later after a passed Check Alive now renews in one Check (was the
classic window plus a second Check). Private regtest node (`real_renew.py`):
renewal again accepted and mined, old txs dead, new will non-final before its
date, accepted after it, pays the heirs 60/40. `ruff` (same 3 pre-existing
I001); whole `tests/` directory: same failures as `main`; zip builds and
loads.

**Outcome:** DONE.

## 81. 5d/1: the delivery date never changes by itself

**Date:** 2026-10-09

**Problems (found in the owner's ZIP test, then traced in the code and the
scenario harness; all present in BAL too):**

1. **Switching BASIC/ADVANCED moved the delivery date.** On the switch the
   delivery field put a default value in place of the saved one and saved
   it: "1y" in ADVANCED (the owner's 05/10/2028 became 09/10/2027), today +
   1 year back in BASIC. The next Check, or the wizard at wallet close,
   would then have prepared a will one year earlier than intended. Building
   the field in ADVANCED with a fixed date saved stored did the same.
2. **An explicit Raw/Date switch** also replaced the value with a default
   instead of the same delivery written the other way.
3. **The heir dialog wrote the heir's own date into the will's delivery
   setting** when it opened (and relied on that to read the heir's date
   back when confirmed).
4. **Fixed date -> equivalent period.** A will built from a fixed date
   (09/10/2027) and a setting changed to the period giving the same date
   today ("365d"): "Ok" that day, but the next day the period read one day
   later, i.e. a postpone: the plugin paid an invalidation (1,430 sat at 13
   sat/vB) and moved the delivery one day.

**What changed (owner rule: the delivery date changes only when the user
edits it):**

- `bal/gui/qt/widgets.py` `BalTimeEditWidget`: the editor follows the form
  of the saved value (Raw for a period, Date for a fixed date; BASIC always
  Date, a period then shown as its real date); nothing is saved while the
  field is built, on a mode switch (`apply_user_type_editor_default`) or
  when another panel syncs it (`set_value` now only shows the value); a
  Raw/Date switch made by the user saves the same delivery written the
  other way (Raw -> Date: the date the will really uses,
  `Will.planned_delivery`; Date -> Raw: the days from today to it). New
  option `save_to_settings=False`. Note: in ADVANCED a fixed date now opens
  in the Date editor (it always opened in Raw with "1y").
- `bal/gui/qt/window.py`: the heir dialog uses `save_to_settings=False` and
  reads the heir's date from its own field.
- `bal/core/will.py` `check_willexecutors_and_heirs`: a will built from a
  fixed date whose setting is now a period giving the same date today is
  rebuilt at once (no invalidation, the usual message 'The delivery date of
  heir "..." changed: preparing a new will.'): a period can be anchored
  only to a will built from a period.
- New tests: `tests/test_delivery_field_keeps_value.py` (13).

**Verification:** field tests (offscreen): a fixed date and a period are kept
when the field is built, on BASIC -> ADVANCED -> BASIC and when another
panel syncs it (nothing saved); a user Raw/Date switch saves the same
delivery ("1y" -> today + 365 days; a date 400 days ahead -> "400d"); the
heir field never writes the will's setting. Harness, fixed date then "365d"
(basic and advanced): rebuilt the same day without an invalidation, the
next day "Ok" with no invalidation (was a paid invalidation and +1 day).
Scenario matrix x6 (119 Checks per run): verdicts unchanged (only the known
B2 and B2adv, one day earlier, accepted), #69 oracle 0 mismatches. `ruff`
(same 3 pre-existing I001); whole `tests/` directory: same failures as
`main`; no new texts to translate; zip builds and loads.

**Outcome:** DONE.

## 82. 5d/2: the heirs' amounts are always explained

**Date:** 2026-10-09

**Problems (seen in the owner's ZIP test, then traced in the harness):**

1. **The "percentages add up to 40%" row disappeared** on the owner's wallet
   (two heirs at 20 % each). When no coin is visible at the moment of the
   Check (e.g. right after a Cancel, the unsigned will in the local history
   holds the coins), `check_amounts` fails on its first, balance-based test
   ("fixed amounts >= balance", true with 0 >= 0); #76 rightly treats that
   as a false alarm, but it then said nothing at all, also about the
   percentages, which depend only on the settings. Harness: the row was
   missing on the Check after a Cancel.
2. **Only fixed amounts** (e.g. 300,000 + 200,000 sat on a 1,000,000 sat
   wallet): the will empties the wallet, so they are paid in proportion
   (598,058 / 398,705 sat, 60 % / 40 %). The only warning ("the entire
   wallet will always be fully emptied...") was overwritten a moment later
   by the next row, and "Checking variables" stayed on "Wait".
3. The same "Wait" happened with "Will-executor fee too high".

**What changed (text approved by the owner):**

- `bal/core/will.py` `describe_amounts`: never reports "fixed amounts
  exceed" with no coin visible (balance 0); new kind `fixed_scaled` (only
  fixed amounts: the shares they are paid in; dust heirs and OP_RETURN
  outputs left out, as in the build).
- `bal/gui/qt/dialogs.py`:
  - the explanation is computed in every case, also when the check is a
    false alarm, so the percentage row always appears;
  - new message "All the heirs have a fixed amount: the will always empties
    the whole wallet, so the amounts are increased in proportion (alice
    60%, bob 40%).";
  - every warning of this step is on its own row (never overwritten) and the
    first row ends on "Ok" (or "Stopped" for fixed amounts that exceed the
    balance, unchanged from #76).
- Catalogs: the new text in Italian and Spanish.
- New tests: `tests/test_msg_amounts_always_explained.py` (9).

**Verification:** harness - owner's case (heirs "1" and "pocket manuel" at 20 %
each, fee 4 -> 6, Cancel at signing, Check again, then a Check with the
will in force): the percentage row ("add up to 40% ... (1 50%, pocket
manuel 50%)") now appears in all four Checks (was missing after the
Cancel); only fixed amounts (300,000 + 200,000 sat): "Checking variables:
Ok" and the new row "(alice 60%, bob 40%)", paid 598,058 / 398,705 sat.
Scenario matrix x6 (119 Checks per run, all with 60 % + 40 %): no amount
row appears, no first row left on "Wait", verdicts unchanged (only the
known B2 and B2adv), #69 oracle 0 mismatches. `ruff` (same 3
pre-existing I001); whole `tests/` directory: same failures as `main`;
translations complete; zip builds and loads.

**Outcome:** DONE.

## 83. 5d/3: one clear "Status" column, coherent with the row colour

**Date:** 2026-10-09

**Problem (owner's remarks during the ZIP tests):** the "Status" column of
the transaction list showed the whole status history on one line
("New.Signed.Pushed.Checked", cut to its last 50 characters when long),
next to a separate "Server" column. A first version showed only the will
state ("In force"), but each row is the copy held by ONE will-executor and
the row colour tells where that executor stands: a red row whose executor
never received the will still said "In force", and the text did not change
when the executor verified it while the colour did.

**What changed (texts approved by the owner):**

- `bal/gui/qt/theme.py`: new `will_status_text(...)`, with the SAME priority
  as the row colour (`status_color`): "In force, verified" (bright green),
  "In force, to be verified" (teal), "Not received by the executor" (red:
  sending or verification failed), "Signed, to be sent" (blue), "To sign"
  (white; "(1/2)" when signatures are missing in a multisig wallet), "In
  force" (a backup transaction without will-executor), "Replaced by a newer
  will", "Invalidated on-chain", "Being executed (mempool)", "Executed",
  otherwise "Not valid" (Italian: In vigore, verificato / In vigore, da
  verificare / Non ricevuto dall'executor / Firmato, da inviare / Da
  firmare / In vigore / Sostituita / Invalidata / In esecuzione (mempool) /
  Eseguita / Non valida). New `will_status_tooltip(...)`: the row's
  will-executor and the whole history, translated ("Will-Executor: https://…
  / History: New → Signed → Pushed → Checked"). The English texts of the
  column are a little longer than the history labels so that the Italian
  and Spanish ones can be feminine (a transaction) while the history keeps
  its own translations. `server_status_text` / `server_status_tooltip`
  removed (no longer used).
- `bal/gui/qt/lists.py`: the "Server" column is removed (its information is
  now in the Status column and its tooltip); the saved history and the row
  colours are unchanged.
- Catalogs: the new texts in Italian and Spanish (the old "Server" texts
  are gone).
- New tests: `tests/test_status_column.py` (9), including "text and colour
  agree".

The separate "Checking transactions" window that opens after the build
window closes (owner's remark) is planned inside the new build window
(#84b, step "Verify").

**Verification:** one test per state, text/colour agreement, tooltip,
columns, unchanged saved history. Display only (the scenario harness does
not draw the list): harness A1 OK; `ruff` (same 3 pre-existing I001); whole
`tests/` directory: same failures as `main`; translations complete; zip
builds and loads.

**Outcome:** DONE.

## 84. 5d/4a: a will summary at the top of the Will tab

**Date:** 2026-10-09

**Problem (owner's remark during the ZIP tests):** the Will tab showed the
settings (the delivery recipe such as "4y", the Check Alive) and one row per
will-executor copy, but nowhere said in plain words whether the will is in
force, when it is delivered, when the owner will be asked to renew it and
whether the will-executors have it. With a relative delivery ("4y") the
owner "lost the thread" and had to trust the wallet.

**What changed (mockup and texts approved by the owner):**

- New `bal/core/summary.py` (no Qt): `will_summary(...)` describes the will
  in force from the data the plugin already has. It only reads: it never
  builds, signs, sends or invalidates. States, with the colour of the box:
  - green "Your will is in force";
  - orange "In force, but 1 will-executor did not receive it" / "No
    will-executor received your will" (button "Check again"), "Will
    prepared: it must be signed and sent" ("Sign and send"), "Will signed:
    it must be sent to the will-executors" ("Send"), "The Check Alive date
    has passed: renew your will" ("Renew"; without a button when the will
    cannot be renewed with one click, with the reason of #79);
  - orange "Your previous will is still active at the will-executors"
    (no button): no item is VALID by the plugin's records, but a will
    already sent can still be executed because its coins were never spent
    (same on-chain check as the #69 warning). Found on the owner's wallet:
    after "fee changed -> Cancel at sign -> later date -> Cancel at the
    invalidation", the unsigned items were marked "Invalidated" and the
    sent ones "Replaced", so the box said "No will in force" while three
    will-executors held an executable will (2028-09-29). Reproduced with
    the harness; the wrong records are fixed separately (#86);
  - red "The delivery date has passed" ("Check") and "No will in force"
    ("Build Your Will"; after an on-chain invalidation it says so, and "no
    transaction prepared" only when there is really none);
  - grey "Your will has been executed" / "is being executed (mempool)";
  - grey "Operation in progress: follow the window in front", with no line
    and no button, while a working dialog (build/check, sign, send, wait)
    is on screen: the box no longer shows half-way states ("to sign") and
    a second button behind the dialog that is already asking the same
    thing (owner's remark). `BalDialog` tells the window when such a dialog
    is shown and hidden.

  The lines: "Delivery to the heirs" (the date of the will in force, e.g.
  "2028-10-01 (in 1 year and 11 months)"), "Check Alive" (advanced mode
  only; "from that day the wallet asks you to renew it", red when passed),
  "Will-Executor" (e.g. "1 of 2 verified it · 1 did not receive it",
  counted once per executor at its worst copy, with the same priority as
  the Status column of #83) and "Heirs" (their number).
- `bal/gui/qt/widgets.py`: new `WillSummaryWidget` draws it (explicit
  colours, so it stays readable with the dark theme) with at most one button.
- `bal/gui/qt/window.py`: the box goes between the toolbar and the
  transaction list (`create_will_tab`); `update_will_summary()` refreshes it
  when the list or the heirs are redrawn, when the delivery / Check Alive
  settings change and when the wallet opens; `on_will_summary_action()` maps
  the button to the EXISTING commands (Check; the Renew question of #79 with
  its fee warning; the wizard; Sign and Broadcast when "auto sign" is off).
- `bal/gui/qt/lists.py`: the transaction list refreshes the box with itself.
- Catalogs: the new texts in Italian and Spanish. The existing message
  "Press Check to invalidate it" now names the button as the Italian and
  Spanish interface shows it ("Premi Controlla", "Pulsa Comprobar").
- New tests: `tests/test_will_summary.py` (31): every state, the lines,
  relative dates, the widget, where the box goes, the button actions, the
  busy state and the on-chain check.

"Last check" (the date of the last verification) is not stored anywhere
today: it comes with the activity log (#84c), as agreed with the owner.

**Verification:** `ruff` (same 3 pre-existing I001); whole `tests/`
directory: same failures as `main`; harness A1 OK (the box only reads);
translations complete; the real widget rendered in Italian matches the
approved mockup; the owner's sequence reproduced with the harness
(Cancel at the invalidation: "still active", 2032-09-23 as the harness's
real delivery; Cancel at sign after the invalidation: "to be signed");
zip builds and loads.

**Outcome:** DONE.

## 86. 5d/5: a local copy of the will never makes it "Invalidated"

**Date:** 2026-10-09

**Problem (owner's wallet, found with the will summary of #84a):** fee
changed -> Check -> Cancel at signing, then a later delivery date -> Check
-> Cancel at the invalidation. Afterwards the unsigned will was marked
"Invalidated" and the will already sent "Replaced": the records said "no
will in force", while three will-executors held a will (2028-09-29) whose
coin was never spent, so still executable. Reproduced with the harness and
on the private regtest chain.

**Cause (two rules, verified step by step):**

1. After a Cancel at signing, the unsigned will is saved in the wallet's
   history as a LOCAL transaction, and Electrum then treats its coin as
   spent. `Util.get_available_utxos` gives such a coin back only when the
   local copy is dated strictly LATER than the will being checked; here the
   copy IS the will being checked (same date), so its own copy hid its coin.
2. `Will.search_rai` then found the coin missing with no broadcast spender
   and marked the item "Invalidated", against the meaning of the status
   ("an input is spent by a mempool/confirmed tx").

Fixing only rule 2 would break the next postpone (the invalidation would
not find the coin), so both are fixed together.

**What changed:**

- `bal/core/util.py`: a coin hidden by a local BAL will copy is given back
  when the copy is dated ON OR AFTER the will being checked (was: strictly
  after). Broadcast spenders, non-BAL labels and earlier local wills still
  hide the coin, as before.
- `bal/core/will.py`: new `Will._funding_spent_only_locally`; `search_rai`
  no longer marks "Invalidated" an item whose coin is "spent" only by a
  local/future transaction (never broadcast). A coin with no recorded
  spender at all keeps the old rule.
- Records already wrong in a wallet are not repaired automatically: the
  will summary (#84a) shows "still active" with the real date, and the next
  Check with the later date invalidates on-chain and prepares the new will
  as expected (verified with the harness on such records).
- New tests: `tests/test_local_copy_not_invalidated.py` (8).
  `tests/test_core_will_extra.py`: the test that pinned the old rule
  (same date: coin hidden) now checks the new one, plus a test for an
  EARLIER local will (coin still hidden); the pre-build purge test uses
  such an earlier placeholder. The old rule came from BAL (commit 251fd93)
  with no stated reason; the purge before every build still runs.
- Catalogs: line references only (no text changed).

**Verification:** `ruff` (same 3 pre-existing I001); whole `tests/`
directory: same failures as `main`; whole scenario matrix 6 times on `main`
and on this branch: same verdicts; the owner's sequence on the private
regtest chain: before the fix 2 items marked "Invalidated" with nothing
broadcast, after it 0; then the postpone invalidates on-chain (old txs
"missing-inputs"), the new will is refused by the node before its date and,
after it, executed paying the heirs 60/40; zip builds and loads.

**Outcome:** DONE.

## 87. 5d/6: no old line left in the grey "operation in progress" box

**Date:** 2026-10-09

**Problem (owner's screenshots of the #86 tests):** while a working dialog
was open, the grey will summary box ("Operation in progress: follow the
window in front", #84a) still showed an old line ("Delivery to the heirs:
2028-09-29") under its title.

**Cause:** the old lines were removed from the layout and scheduled with
`deleteLater`, which runs only when control returns to the event loop that
called it; the working dialog's own loop (exec) runs meanwhile, so the old
widgets stayed painted at their place.

**What changed:**

- `bal/gui/qt/widgets.py`: `WillSummaryWidget._clear_rows` hides and
  detaches each old line at once, then deletes it later.
- New test in `tests/test_will_summary.py`: switching to the grey box
  leaves no old line, with no event loop in between.

**Verification:** `ruff` (same 3 pre-existing I001); whole `tests/`
directory: same failures as `main`; harness A1 OK; zip builds and loads.

**Outcome:** DONE.

## 85. 5d/7: the "Broadcasting" line names the will-executors still awaited

**Date:** 2026-10-09

**Problem (owner's remark during the #84a tests):** while the will was sent
to the will-executors the dialog showed only "Broadcasting 0/1 (3s / 20s)"
for up to 20 seconds (the global deadline), without saying which server it
was waiting for: it looked as if something was broken.

**What changed (approved by the owner):**

- `bal/gui/qt/dialogs.py`: new `BalBuildWillDialog.push_status_text(...)`;
  the line now ends with "— waiting for: we.divinaly.support" (host name
  only, sorted, at most three plus "(+N more)"). A server leaves the list
  when it answers or when the deadline expires (the existing "Timeout - no
  answer" row is unchanged). The deadline and the push logic are unchanged.
- Catalogs: the two new texts in Italian ("in attesa di: {}", "(+ altri
  {})") and Spanish.
- New tests: `tests/test_push_waiting_for.py` (4).

**Verification:** harness A2 shows "Broadcasting 0/2 ... — waiting for:
we0.test, we1.test", then "1/2 ... — waiting for: we1.test", then "2/2";
`ruff` (same 3 pre-existing I001); whole `tests/` directory: same failures
as `main`; translations complete; zip builds and loads.

**Outcome:** DONE.

## 88. 5d/8 (#84b-1): the Building Will dialog with a steps bar and boxes

**Date:** 2026-10-09

**Problem (owner's remark):** the Building Will dialog printed one text row
per step ("Checking variables: Ok", "Building your will: Wait", ...): the
content was right but "highly disordered", hard to follow for the user.

**What changed (mockup and decisions approved by the owner):**

- New `bal/core/build_report.py` (no Qt): `BuildReport`, the dialog's
  structured content (thread-safe), and `heir_shares(...)`.
- `bal/gui/qt/widgets.py`: new `BuildReportView` draws, from top to bottom:
  - a bar with the five steps Checking -> Preparation -> Signature ->
    Sending -> Verification, each done / running / attention / error / not
    needed, with one word under it ("updated", "signed", "2/2",
    "interrupted"...);
  - one result sentence, coloured like the will summary of the Will tab:
    on a normal ending it IS that summary (#84a), so the two always agree;
    after an error or a Cancel it names the step ("The new will was not
    prepared.", "Interrupted: the new will was not signed.") and a box "Your
    will now" says which will is in force;
  - boxes only when needed: "What changed", "Attention", "What went wrong",
    "Heirs" (amount in the wallet's unit and percentage, owner's choice),
    "Will-Executor" (one line per executor, updated live while sending:
    waiting / received / no answer (20 s) / not received / verified).
- `bal/gui/qt/dialogs.py` (`BalBuildWillDialog`): the existing steps are
  unchanged; the rows they write feed the report (the step comes from the
  row label, the state from the colour of the status) and become a
  collapsible "Details (log)" with the time of each row, closed by default
  and opened by itself on an error (owner's decision). The next-steps hint
  is shown in the "Attention" box and no longer as a popup after Close;
  only the still-active warning (#69) is still repeated in a popup (owner's
  decision). The Signature step shows "running" while the password is
  asked (it stayed grey). The dialog opens at least 560 px tall and grows
  with its content up to 85% of the screen's available height (a notebook
  1366x768: about 620 px), then scrolls with the buttons always visible; it
  never shrinks while open (owner: it was too short and the buttons covered
  the will-executors box). The verification by the will-executors stays in
  its own window for now (#84b-2 moves it into this dialog).
- `bal/gui/qt/plugin.py` (`password_dialog`): Electrum's password window
  shows Electrum's icon (owner's decision). It IS Electrum's own dialog,
  Electrum checks the password and signs; Qt gave it the icon of the plugin
  window that opened it. The text that tells why the password is asked
  ("Sign your will") is kept.
- `bal/core/will.py`: new `Will.sent_still_executable(...)` (the sent txs
  still executable on-chain, any date); `find_still_active_older_txs` now
  uses it and keeps its result. `bal/core/summary.py`: new
  `executor_states(...)`; "Will prepared: it must be signed and sent" and
  "Will signed: it must be sent" now add "Until you sign and send it, the
  will sent before stays in force: delivery on ..." when that will can still
  be executed (after a Cancel at signing the box did not say it).
- Catalogs: the new texts in Italian and Spanish.
- New tests: `tests/test_build_report.py` (18, including the real Qt dialog);
  `tests/test_will_summary.py` updated (+1). The light fake dialogs of
  `tests/test_gui_still_active_warning.py` and `tests/test_gui_phase2_error.py`
  now also carry the new helpers (`_log_only`, `_report`, `_finish_report`).

Two defects found by the new tests and fixed before the ZIP: the "Details
(log)" link had no text until the first row, and the dialog redrew before
the report knew the new step (the bar was always one step behind).

**Verification:** `ruff` (same 3 pre-existing I001); whole `tests/`
directory: same failures as `main`; the scenario matrix 6 times on `main`
and on this branch: same verdicts; the real dialog rendered in Italian on
harness scenarios (nothing to do, fee changed, Cancel at signing, Cancel at
the invalidation) matches the approved mockup; translations complete; zip
builds and loads.

**Outcome:** DONE.

## 89. 5d/9 (#84b-2): the verification inside the Building Will dialog

**Date:** 2026-10-09

**Problem (owner's remarks):** the dialog had to close before the
will-executors were verified: the Check button and the wizard then opened a
separate "Check Transaction" window, so the "Verification" step of the new
bar (#84b-1) was always "to do". At wallet close and after a Check Alive
renewal the verification did not run at all, so a will just sent (for
example because new funds arrived) was not confirmed before the owner left.
At wallet close the dialog did not say that the wallet was closing nor why
it was working and asking for the password.

**What changed (plan and texts approved by the owner):**

- `bal/gui/qt/dialogs.py` (`BalBuildWillDialog`): new `loop_verify()`, the
  Verification step, run at the end of phase 2 right after the sending, so
  it runs for the Check button, the wizard, the renewal and the wallet
  close (owner's decision: everywhere, wallet close included). Every VALID
  item that was sent and is not verified yet (`Will.needs_server_check`, the
  items the Check button verified) is looked up on its will-executor, all
  at once, with the same 20 s limit as the sending
  (`Willexecutors.check_transactions_parallel`); each answer is recorded as
  before (`WillItem.set_check_willexecutor`). While it runs: the step
  counter ("0/2 · 3 s / 20 s"), the sentence "Verifying your will with the
  will-executors… waiting for: <hosts>", each executor "received,
  verifying…"; then "received and verified", "not received" or "no answer
  (20 s)". The word under the step is counted over every will-executor of
  the will, like the sending ("2/2"; "1/2" attention; "0/2" error; "to do"
  only when nothing was sent yet). A verification error never stops the
  dialog. Nothing to verify: no request at all.
- The "Sending" step shows its count ("2/2") as soon as the sending ends
  (`_merge_executors_and_send_note`), not only when the dialog ends.
- Defect of #84b-1 found by the new tests and fixed: while the sending ran,
  the result sentence lost "waiting for: <host>" (#85) at every progress
  row; it was only in the log, closed by default. The sending and the
  verification now keep their own counter and sentence.
- Found in the owner's test of the first ZIP: after a Cancel at the
  password the bar said "Sending: not needed". It was needed but could
  not happen (a copy is still to sign): the step now stays grey, with no
  word (`_finish_report`).
- At wallet close (`BalWindow.on_close` opens the dialog with
  `closing=True`): the title "Closing the wallet: checking your will", a
  light-blue box above the steps: "You are closing the wallet. Before it
  closes, the plugin checks that your will covers all your bitcoin,
  including those received after the last check. If something changed, it
  prepares a new will and asks for your password to sign it. Then it sends
  it to the will-executors and verifies that they received it." and, next
  to Close, "When you press Close, the wallet finishes closing." On the
  owner's request the box does NOT say how to turn this check off
  (dangerous: new funds would stay out of the will).
- `bal/gui/qt/lists.py` (`PreviewList.check`) and the wizard
  (`BalWizardDialog.on_next_we`): no separate "Check Transaction" window
  any more. The right-click "check" of a single row is unchanged.
- `bal/core/build_report.py`, `bal/gui/qt/widgets.py`: new executor state
  "verifying".
- Catalogs: the new texts in Italian and Spanish.
- New tests: `tests/test_verify_in_dialog.py` (13, including the real Qt
  dialog at wallet close). `tests/test_rebuild_on_close_setting.py`: its
  fake dialog accepts `closing=True` and checks it is passed.
- Scenario harness (outside the repository): the fake will-executor now
  answers the verification with the tx it received (it answered "x", which
  the verification would record as "not received").

**Not changed:** the automatic rebuild on new transactions (setting OFF by
default) still sends without a dialog and without verification; when the
dialog stops before the sending (no will-executor, no heirs, "Not now" at a
renewal, an error), the verification does not run and the step stays "to
do", as the Will tab says ("to be verified").

**Verification:** `ruff` (same 3 pre-existing I001); whole `tests/`
directory: same failures as #84b-1; the scenario matrix 6 times on `main`
and on this branch: same verdicts (0 differences; B2/B2adv end one day
earlier, known and accepted), and on this branch every verification
answered "Ok" (186 Verification steps, 372 executor answers); the real
dialog rendered in Italian at wallet close after new funds arrived
(owner's example): while verifying and at the end; translations
complete; zip builds and loads.

**Outcome:** DONE.

## 90. 5d/10: no "new transactions" notification for the will's local copies

**Date:** 2026-10-10

**Problem (owner's report, with screenshots):** Electrum's desktop
notification said "4 new transactions: Total amount sent 2.12291 mBTC" (and
"New transaction: sent 0.5388 mBTC" after adding a will-executor), but the
History tab showed nothing real. Nothing had been sent: the plugin saves the
unsigned copies of the will (one per will-executor) into the wallet's local
history (setting "Save inheritance transactions in history", ON by
default, shown only in ADVANCED mode), and Electrum announces every
transaction added to the wallet, local ones too, summing their amounts.
Once signed, the copies leave the history, so the owner found nothing
there. A user reading "sent 2 mBTC" is alarmed.

**What changed (plan approved by the owner):**

- `bal/core/will.py` (`Will._add_transaction_to_history`): the copies are
  added with `is_new=False`, which is what Electrum itself uses when it
  re-adds a stored transaction (same parameter in Electrum 4.7.2 and
  4.8.x). The copy is stored exactly as before; only Electrum's "new
  transaction" event, which feeds the notification, is not raised. The one
  other thing that event did for the wallet, clearing its tx-parents cache,
  is now done by the plugin; the History tab was already refreshed by the
  plugin itself. The setting and the copies are unchanged: whoever signs
  them elsewhere (multisig, hardware wallet, Electrum's transaction window)
  still finds them in the history.
- Side effects: with the "Rebuild automatically on new transactions"
  setting, saving the copies no longer re-triggers that rebuild; a local
  copy no longer makes Electrum re-check the paid status of invoices.
- New tests: `tests/test_history_no_notification.py` (5: the parameter
  exists in the Electrum we run on; new copy, "unrelated" fallback and
  signature merge are all added silently; the cache is cleared).

**Not changed (seen while checking, Electrum's own behaviour):** the copies
of the will spend the same coins, so Electrum keeps only the last one added
in the history; and while an unsigned copy is in the history Electrum counts
its coins as spent in the balance.

**Verification:** on a real in-process Electrum wallet (scenario harness,
Cancel at signing): `main` raised 2 "new_transaction" events for the will
copies, this change 0; the same copy kept in the history (LOCAL, with its
label) and the same balance in both. `ruff` (same 3 pre-existing I001);
whole `tests/` directory: same failures as before; the scenario matrix 6
times on `main` and on this branch: same verdicts (0 differences; B2/B2adv
end one day earlier, known and accepted); zip builds and loads.

**Outcome:** DONE.

## 91. 5d/11: funds received, the new will is dated one day earlier

**Date:** 2026-10-10

**Problem (found while preparing the report, confirmed on regtest):** when
funds arrived, the Check (or the wallet close) built a new will that spends
the old coins plus the new ones, but with a will-executor it kept the OLD
date. Each executor then held two wills, both valid, sent and verified,
executable on the same day: the old one (coin 1) and the new one (coins 1
and 2). Only one can be mined (they spend coin 1); if an executor broadcast
the old one first, coin 2 would never reach the heirs. On a private regtest
Bitcoin Core node, at the delivery date the node accepted all four copies,
and executing an old one left 250,000 sats out (3 runs out of 3).

Cause: BAL's `Will.check_anticipate` dates a new will one day earlier when
the heirs, the miner fee or the executor's fee change, and, without a
will-executor, when the amounts change; with the same will-executor it did
not look at the coins. Unchanged since BAL's first commit, with no stated
reason, and against the plugin's own promise ("the delivery date is
anticipated by one day so the new will replaces the previous one", help of
"Rebuild automatically on new transactions") and the owner's model of the
protocol (funds received -> one day earlier).

**What changed (plan approved by the owner):**

- `bal/core/will.py`: `Will.check_anticipate` also dates the new will one
  day earlier when, for the same will-executor, it spends every coin of the
  old will and more (`Will.covers_more_coins`). The old will then becomes
  "Replaced" and, at the delivery date, the complete will is the only one
  the network accepts. Identical copies, a coin spent, another executor:
  unchanged.
- Wallets already in that state (wills built before this change): new
  `Will.find_same_date_copies` and `SameDateCopiesException`. The Check
  (and the wallet close, the Prepare action, the command line) now says
  "Will-executor "<url>" holds two wills that can be executed on the same
  day, and one of them does not include all your coins: preparing a new
  will, dated one day earlier." and rebuilds; nothing is asked twice.
- `bal/core/messages.py`: that sentence (`will_change_message`), in Italian
  and Spanish too.
- Side effect, accepted by the owner: every time funds arrive the delivery
  moves one day earlier (as it already did for fee and heir changes).
- New tests: `tests/test_new_funds_anticipate.py` (8).
- Scenario harness (outside the repository): a new oracle, the will
  executed first must spend every wallet coin (the date-only oracle missed
  this case); new scenarios A17 (2nd coin received), A18 (2 coins, a Check
  after each), A19 (the state left by the old rule, then Check); new
  regtest check `real_newfunds.py`.

**Verification:** regtest (Bitcoin Core 31.1, private node), 3 runs each:
`main` - all four copies accepted at the delivery date, executing an old
one leaves 250,000 sats out; this change - the old copies are `non-final`
when the complete one (one day earlier) becomes valid, 0 sats left out.
Harness: A17, A18, A19 OK (`main`: delivery date unchanged and the first
will leaves coins out); A19 shows the new sentence, rebuilds once, the next
Check finds nothing. `ruff` (same 3 pre-existing I001); whole `tests/`
directory: same failures as `main`; the scenario matrix 6 times on `main`
and on this branch: only A17, A18 and A19 differ (`main`: date
unchanged and the first will leaves coins out, 6 runs out of 6; this
change: OK 6/6); every other scenario has the same verdict, and the new
oracle finds no coin left out in them (B2/B2adv end one day earlier,
known and accepted); translations complete; zip builds and
loads.

**Outcome:** DONE.

## 92. 5d/12: the inheritance plan report (save as file or PDF, print)

**Date:** 2026-10-10

**Request (owner's idea, example approved):** a report of the inheritance
plan, "old style" ASCII, to keep or print: who receives the bitcoin, how
much and when, which will-executors hold the will. Multi-page with "Page
1/2, 2/2..." sized for an A4 sheet with standard printer margins.

**What changed:**

- New `bal/core/report.py` (no Qt): `inheritance_report(...)` builds the
  report as pages, from the same data as the will summary of the Will tab
  and the Building Will dialog: 1. Status (the summary's title, delivery,
  Check Alive, miner fee, its note); 2. Heirs (name, share, amount in the
  wallet's unit, full address, total; before a will is prepared, the shares
  set in the Heirs tab); 3. Will-executors and where each stands; 4. How it
  works (owner's text); 5. Technical details (each executor's transaction
  id and date). Marked private at the top; never a seed, key, password or
  raw transaction (only the ids); no fiat value (owner's choice).
- Layout: 78 columns, pages of 60 lines. A4 is 297 mm high: with 20 mm
  margins 257 mm are left, and a 10 pt monospaced line with 1.2 spacing is
  4.23 mm: 60 lines; 78 columns of 10 pt Courier take 165 mm of the 170 mm
  between the side margins. Every page ends with "PRIVATE ... Page n/N";
  the next pages start with the title in short; a block (an heir and the
  address, a transaction and its id, a section title and its first lines)
  is never split across pages.
- New `bal/gui/qt/report_actions.py`: the "Report" menu with "Save as
  file…" (a `.txt`, UTF-8, Windows line ends, exactly 60 lines per page,
  proposed on the Desktop as `safe21-report-<wallet>-<date>.txt`) and
  "Print…" (the printer dialog, then one report page per sheet: A4, 20 mm
  margins, 10 pt; on a smaller printable area the font shrinks instead of
  breaking the pages, so "Page 1/2" on paper is always right).
- "Save as PDF…" (owner's request after printing the .txt from another
  program, whose pages did not match): the same sheets as "Print…",
  saved as `safe21-report-<wallet>-<date>.pdf`, so the pages stay right
  wherever the file is opened or printed.
- Owner's rule: the report can be made only for a will in force and
  verified (`report_block_reason`): the will summary is green ("Your will
  is in force": signed, sent, delivery and Check Alive not passed) AND
  every will-executor has verified it. Otherwise the three entries are
  grey and the menu's first line says why ("... Press Check."); the
  actions refuse too. A passed Check Alive: not allowed (renew first).
  A will with only a backup transaction (no will-executor): allowed, and
  the report says that the owner keeps the signed transaction and nobody
  will broadcast it for him.
- Where: a "Report" button next to Calendar and Close at the end of the
  Building Will dialog (`bal/gui/qt/dialogs.py`), and a "Report" submenu
  in the tools menu of the Will tab (`bal/gui/qt/lists.py`).
- From the owner's test of the first ZIP: a blank line between one heir
  and the next; the share column said "Condividi" (Electrum's catalog,
  which wins, translates "Share" as the verb): the texts are now
  "Heir's share" and, for the button and menu, "Will report" (a bare
  "Report" can mean "report a bug" in Electrum's catalog).
- Catalogs: the new texts in Italian (the example approved by the owner)
  and Spanish.
- New tests: `tests/test_inheritance_report.py` (12, including printing two
  pages to a PDF printer, "Save as PDF…", the dialog's button and the
  owner's rule).

Printing the `.txt` from Notepad uses Notepad's own font and margins, so
its pages may not match: "Print…" or the PDF are the reliable ways.

**Verification:** `ruff` (same 3 pre-existing I001); whole `tests/`
directory: same failures as `main`; reports generated in Italian from real
harness wills (2 heirs: one page; 16 heirs: two pages, no heir split from
its address); harness A1 OK (the will logic is unchanged); translations
complete; zip builds and loads.

**Outcome:** DONE.

## 93. 5d/13: a will-executor that does not answer: "Exclude" and "Retry"

**Date:** 2026-10-10

**Problem (owner's test):** while one will-executor does not answer, the
will stays "In force, but 1 will-executor did not receive it" and the
report (#92) stays locked. The way out, deselecting it in the Will-Executor
tab and pressing Check again, is hard to find; and pressing Check again
(which already sends the missing copies again) is not suggested anywhere.

**What changed (plan approved by the owner):**

- `bal/gui/qt/widgets.py` (`BuildReportView`): once the Building Will
  dialog has finished, the will-executors box shows "Exclude" next to each
  executor that did not get the will ("not received", "no answer") and
  "Retry" under the box.
- `bal/gui/qt/dialogs.py`: "Retry" runs the whole check again in the same
  dialog (a new worker thread: the finished run stopped its own); the copies
  that did not arrive are sent again and verified. "Exclude" asks first
  ("... did not receive your will. The other 4 will-executors have it. If
  you exclude it, your will is no longer sent to it and the check runs
  again without it. You can select it again in the Will-Executor tab."),
  then excludes it and runs the check again. Never the last selected
  executor (a message says to choose another one in the Will-Executor tab).
- `bal/gui/qt/window.py`: `BalWindow.can_exclude_willexecutor` and
  `exclude_willexecutor`: deselect and save like the Will-Executor tab, and
  drop the copies of the will meant for it that it never got. Found with the
  scenario harness: BAL only ignores a deselected executor, its unsent copy
  stayed VALID, so the summary stayed orange and the report locked even
  after deselecting it. Copies it did get are kept (dated later than the
  will in force, they cannot be executed first).
- Catalogs: the new texts in Italian and Spanish.
- New tests: `tests/test_failed_executor.py` (6).
- Scenario harness (outside the repository): `World.down`, an executor that
  does not answer (the push times out, the verification fails); scenario
  A20 (fee up with we1 not answering, Check, Exclude we1, Check).

**Verification:** harness (fee up, we1 not answering): after the Check
"In force, but 1 will-executor did not receive it", report locked; after
Exclude + Check "Your will is in force", report allowed, no password asked,
the next Check changes nothing; the last executor cannot be excluded.
`ruff` (same 3 pre-existing I001); whole `tests/` directory: same
failures as `main`; the scenario matrix 6 times on `main` and on this
branch: no scenario differs (A20 ends with the intended date on both: the
harness deselects we1 by hand on `main`; the difference is the summary
and the report, checked above); translations complete; zip builds and
loads.

**Outcome:** DONE.

## 94. 5d/14: one word for the will in the commands ("testamento")

**Date:** 2026-10-10

**Request (owner):** the tooltip of the Check button said "Controlla
l'eredità", the menu "Invalida il piano", the log "Trasmissione del piano",
next to "testamento" everywhere else. One term, aligned in every language.

**What changed (list approved by the owner):**

- The commands now say "will": "Broadcast will", "Invalidate will" (Will
  tab's tools menu and the will preview dialog), "Check your will" (the
  Check button's tooltip), "Rebroadcast will" (settings). Italian
  "Trasmetti testamento", "Invalida testamento", "Controlla testamento",
  "Trasmetti di nuovo il testamento"; Spanish "Transmitir testamento",
  "Invalidar testamento", "Comprobar testamento", "Retransmitir el
  testamento".
- A real mismatch fixed on the way: the Italian hints said "press
  'Trasmetti il piano'" while the menu entry read "Trasmissione"; every
  hint now names the button exactly as it is translated ("press 'Broadcast
  will'").
- "Your inheritance has been rebuilt..." -> "Your will has been rebuilt...".
- Italian rows and windows without "piano": "Invalida il vecchio
  testamento", "Invalidazione del vecchio testamento", "Posticipato:
  invalidazione del vecchio testamento", "Trasmissione del testamento ai
  Will-Executor", "Firma e trasmetti questa transazione per invalidare il
  testamento attuale", "Firma (e trasmetti) per confermare la modifica",
  "trasmetti" (row menu); "il testamento non può essere creato" (heirs all
  below the dust limit).
- Kept on purpose: "eredità" where it means the inheritance itself (an
  inheritance already executed, the inheritance transactions, the
  inheritance process), and the report's title "REPORT DEL PIANO DI
  EREDITÀ" (owner's choice).
- New test: `tests/test_will_terms.py` (3: the commands say "will" in
  Italian and Spanish, the hints name the real button, no "piano" in the
  Italian will rows).

**Verification:** `ruff` (same 3 pre-existing I001); whole `tests/` directory: same
failures as `main`; the scenario matrix 6 times on `main` and on this
branch: no scenario differs (only texts changed); no untranslated text
in Italian and Spanish; zip builds and loads.

**Outcome:** DONE.

## 95. 5d/15: the Check button as big as the Wizard, right after it

**Date:** 2026-10-10

**Request (owner):** the Check button should be much bigger, about as big
as the Wizard, and right after it. Check is the command used most after the
Wizard (new funds, a change, a will-executor that did not get the will), but
it was a small icon at the far right of the Will tab's toolbar, next to
"Tools", and easy to miss.

**What changed (mockups approved by the owner):**

- `bal/gui/qt/lists.py` (`PreviewList.create_toolbar`): the Check button
  has its name on it, "Check your will" (Italian "Controlla testamento",
  Spanish "Comprobar testamento", already translated in #94), with the same
  look as the Wizard (bold, 28 px icon, 40 px high), right after it:
  Wizard | Check | Delivery time | Check Alive | Calendar.
- New `PreviewList._big_button`, used by both buttons, so the two main
  commands always look the same.
- The tooltip explains what the button does: "Rebuild the will if something
  has changed, send it to the will-executors and check that they have it.
  With 'Auto-sign on Check' off, it shows how to sign and send it." The
  second sentence was added to the approved text: with that option off the
  Check does not sign and send by itself; the option is named exactly as it
  is translated.
- Room for it (owner asked to check ADVANCED mode, then chose the layout):
  `bal/gui/qt/widgets.py` (`WillSettingsWidget._arrange_toolbar_rows`, for
  the one-row layout of the Will and Heirs tab toolbars). BASIC keeps its
  one row and its spacing: delivery time | calendar | fee. ADVANCED, which
  also shows the Raw/Date selector and the Check Alive with its sentence,
  uses two rows next to the two big buttons: delivery time | calendar, then
  Check Alive | fee, packed to the left (the delivery field drops its
  40-character minimum there, which pushed the calendar ~140 px away from
  the date). A BASIC <-> ADVANCED switch rearranges the existing toolbars
  (called from `apply_user_type_visibility`, which runs on every refresh:
  the fields move only when the mode really changed). Minimum width of the
  Will tab toolbar, Italian, offscreen render (Linux fonts): BASIC 867 px
  before, 1024 px now; ADVANCED 1334 px before, 1499 px with the big button
  on one row, 1099 px on two rows (about 25 px taller). The wizard is
  unchanged.
- Catalogs: the tooltip in Italian and Spanish.
- New test: `tests/test_check_button.py` (6: order, same look as the
  Wizard, the button runs the Check and its tooltip names the option; BASIC
  one row with its spacing, ADVANCED two rows, a mode switch rearranges the
  existing toolbar and an ordinary refresh moves nothing).

**Verification:** `ruff` (same 3 pre-existing I001); whole `tests/` directory: same
failures as `main`; the real toolbar rendered in Italian, BASIC and
ADVANCED, before and after (widths above), also after switching BASIC ->
ADVANCED without a restart (same 1099 px); harness A1
OK (the will logic is unchanged); translations complete; zip builds and
loads.

**Outcome:** DONE.

## 96. 5d/16: the Building Will dialog fits its content and stays on the screen

**Date:** 2026-10-10

**Problem (owner's screenshots):** the Building Will dialog was much taller
than its content, and its bottom part, with the buttons, often ended below
the screen. A first fix made it too short: the will-executors box and the
"Details (log)" link ended below the fold, which the scroll bar showed.

**Causes (found by measuring the real dialog with the scenario harness):**

- The room around the report (margins, the row of buttons) was counted
  ~200 px too big: `QScrollArea.sizeHint()` keeps the size its content had
  when the dialog was created (~30 px), while the layout reserves at least
  the scroll area's minimum (240 px) for it. At the end of a normal Check
  the dialog asked for 464 px where 290 were enough.
- It grew while the run showed more (e.g. the log) up to 85% of the screen
  and never shrank.
- Growing moved its bottom edge down: Qt places a dialog inside the screen
  only when it opens.
- The fit ran right after the last redraw: Qt shows a widget added to a
  visible dialog only later (a queued event) and a widget not shown yet
  counts as empty, so whole boxes were missed (4 will-executors and a
  warning: 138 px hidden in the harness).

**What changed (plan approved by the owner, then "a middle way": the owner
sent a screenshot, 570 px high, with everything visible, as the minimum):** `bal/gui/qt/dialogs.py`
(`BalBuildWillDialog`):

- `_fit_height`: the room around the report is what the layout really
  reserves; the dialog is never shorter than 560 px (`OPEN_HEIGHT`, was 560
  for opening only) and takes at most 75% of the screen's available height
  (was 85%). While the run goes on it only grows, so it does not jump; when
  the run is over and when the log is closed it fits its content, down to
  560 px. On a 1366x768 notebook the cap (546 px) wins.
- New `_content_height`: shows the children not hidden on purpose before
  measuring, and takes the larger of the layout's hint and its height for
  the width the text really has (a wrapped sentence needs more height when
  narrow). A deferred second fit (`QTimer.singleShot(0)`) runs once Qt has
  processed its queued events.
- When the run is over (`_add_close_button`) the new row of buttons is shown
  at once, otherwise the fit did not count it (28 px short).
- New `_keep_on_screen`: after every change of height, a dialog whose bottom
  edge is below the screen moves up.
- `tests/test_build_report.py`: the #84b height test no longer expects the
  dialog to stay tall at the end.
- New test: `tests/test_dialog_fits_screen.py` (7: the owner's limits; a
  short report is not shorter than 560 px; a notebook screen caps it; the end of a run with 4
  will-executors and two long boxes hides nothing; the room around the
  report is counted right; the buttons of a visible dialog count; closing
  the log gives the room back; it moves up instead of ending below the
  screen).

**Verification:** real dialog in the harness, end of a run with 4
will-executors, a warning and a "what changed" box: 138 px hidden below the
fold after the first fix, 0 now; end of a normal Check (2
will-executors) on a 1040 px screen: 560 px, the same as before #96 but with
the room around the report counted right, so content that needs more is
shown whole and the dialog is never taller than it needs. `ruff` (same 3 pre-existing I001); whole `tests/`
directory: same failures as `main`; harness A1 OK (the will logic is
unchanged); zip builds and loads.

**Outcome:** DONE.

## 97. 5c: a clean test suite (0 failed, 0 errors, nothing deselected by hand)

**Date:** 2026-10-10

**Problem:** the whole `tests/` directory had 27 failures and 3 collection
errors inherited from BAL, and one test hung forever, so it had to be
deselected by hand at every run. Every check compared against that list:
a new failure could hide among the old ones (one did, see below).

**Causes (each file run on its own, and against BAL `827a1d1`):**

- Network pollution (21 failures). `test_group_e_karen7_invalidate.py` and
  `test_no_willexecutor_karen7.py` switched Electrum to regtest while being
  imported, during collection, before the first test. `conftest.py`
  restored "the network before each test", which was already regtest, so
  every test with mainnet addresses failed in the whole suite
  (`test_auto_rebuild_on_new_tx`, `test_cli_autorebuild`,
  `test_cli_controller_offline`); alone they passed.
- A failure hidden behind it, from CHANGELOG #80:
  `test_cli_autorebuild::test_auto_rebuild_invalidates_when_anticipation_crosses_threshold`
  put the Check Alive 12 hours before the delivery (an absolute date). Since
  #80 (decision D14) the Check Alive is only "N days before the delivery",
  at least 1: that date cannot be kept, becomes 180 days, here already
  passed (CheckAliveError). The scenario no longer exists by design: the
  Check Alive is counted back from the will's own locktime, so a one-day
  anticipation can never cross it. Not a code bug.
- `tests/karen7`, the BAL author's regtest wallet, is not in git
  (`.gitignore`): 3 modules failed while being collected, plus `test_e5`.
- `test_heir_relative_anchor::test_karen7_frozen_delivery_not_expired`
  failed already in BAL: its "150d" (after the creation) drifted with "now".
- Audio: the KB/sec list comes from `amodem` (the package of Electrum's
  audio_modem plugin). Without it 3 tests failed and
  `test_audio_import_receive_wiring` hung (checked: killed by a 30 s timeout
  without `amodem`, passes with it).
- QR: `test_import_start_stop_scan_signal_wiring` needed the zbar library
  (missing: `_start_scan` stopped before the camera wiring the test is about).

**What changed (plan approved by the owner; tests only, no plugin code):**

- `tests/conftest.py`: every test starts on the network Electrum starts
  with (mainnet, taken when pytest loads the file) and gets it back after;
  new `regtest_net` fixture for the tests on regtest data.
- The karen7 modules switch to regtest per test (`regtest_net`), no longer
  while being imported; without `tests/karen7` they are skipped, saying
  why (`test_group_e_karen7_invalidate.py`, `test_no_willexecutor_karen7.py`,
  `test_reproduce_none_type.py`, and `test_e5` in
  `test_group_e_mock_karen7.py`).
- `test_cli_autorebuild.py`: the hidden failure becomes
  `test_auto_rebuild_anticipation_never_crosses_the_check_alive` (Check
  Alive "-1d", one-day anticipation: the will is rebuilt, anticipated by
  one day, the old tx replaced, nothing invalidated).
- `test_heir_relative_anchor.py`: that test uses "-150d", the current form;
  the window is then anchored to the frozen locktime, as the test means.
- `test_gui_export_dialogs.py`: the 4 tests that need `amodem` are skipped,
  saying why, when it is missing (no more hang).
- `test_gui_qr_transfer.py`: the QR reader is mocked like the camera, so the
  wiring test runs without zbar.
- Test environment (outside the repository, owner's OK): `amodem` 1.16.0
  installed with its dependency `numpy` 2.4.6, so the audio tests run here.

**Verification:** whole `tests/` directory, nothing deselected: 949 passed,
4 skipped (all karen7, with the reason), 0 failed, 0 errors; the same with
the files in reverse order. Without `amodem` (uninstalled for the check):
the 4 audio tests are skipped with the reason, the rest pass, no hang.
`ruff` (same 3 pre-existing I001); harness A1 OK.

**Outcome:** DONE.

## 98. 5c/2: the karen7 tests run again, on a test wallet made in code

**Date:** 2026-10-10

**Problem:** after #97 the suite was clean, but 4 skips hid 34 tests: they
read `tests/karen7`, the BAL author's regtest wallet, not in git. Among them
the only tests that build a will with real Electrum transactions and then
its invalidation transaction, and the "no will-executor selected" path.

**What changed (plan approved by the owner; tests only, no plugin code):**

- New `tests/regtest_wallet_data.py`: the two parts of karen7 the tests read,
  made in code. Six heirs with karen7's names (percentages, a fixed amount,
  two OP_RETURN heirs, one delivery date) and four coins, one already spent.
  Valid regtest addresses from a hash of a label, made-up txids: no wallet
  file, no seed, no key (the tests never sign); the same data at every run.
- `test_group_e_karen7_invalidate.py`, `test_no_willexecutor_karen7.py`,
  `test_reproduce_none_type.py`, `test_e5` in `test_group_e_mock_karen7.py`:
  read that data; the karen7 file and the skips of #97 are gone.
- Found on the way:
  - #97 defect, invisible while the modules were skipped: the class setups
    are autouse fixtures, which pytest runs before the `usefixtures`
    marker, so they built the coins on mainnet ("invalid bitcoin address").
    They now request `regtest_net` themselves.
  - `test_all_invalidated_returns_none` expected nothing to invalidate when
    every item is marked INVALIDATED. Since CHANGELOG #71 (BUG-3) every
    wallet coin used by any will item is spent whatever its status (an item
    wrongly marked INVALIDATED could stay executable). Now two tests:
    items marked INVALIDATED still get their coins spent; no coin of the
    will left in the wallet gives nothing to invalidate.
  - The mocked transactions had ids like "faketx_0000", not hex: the build
    stopped with "non-hexadecimal number", and `test_reproduce_none_type`
    passed building nothing. Now 64 hex characters: it builds 1 transaction.
  - One delivery date for all heirs: two dates chain a second transaction
    on the change of the first, which the mocked tests cannot follow (they
    passed with karen7, so its heirs left no change either).

**Verification:** whole `tests/` directory, nothing deselected: 984 passed,
0 skipped, 0 failed, 0 errors (35 more tests than #97); the same with the
files in reverse order. `ruff` (same 3 pre-existing I001); harness A1 OK.

**Outcome:** DONE.

## 99. CLI/1: the settings commands work with no wallet

**Date:** 2026-10-10

**Problem:** `bal_settings_list`, `bal_settings_get`, `bal_settings_set` and
`bal_settings_reset` always failed with "wallet not loaded", from the
command line and over JSON-RPC. The commands passed no wallet to a helper
that requires one. The tests called the controller methods directly, so
they passed anyway. Found in the CLI review of 2026-09-24 (point 3),
confirmed live on `main` on 2026-10-10.

**What changed (plan approved by the owner):**

- `bal/cli/controller.py`: new `BalSettingsController(plugin)` with the
  settings methods (moved, not copied); `BalController` inherits them.
- `bal/cli/commands.py`: the four settings commands use the new
  `_settings_call`, which needs no wallet.
- New `tests/test_cli_command_layer.py`: the commands are called through
  Electrum's own `Commands` wrappers, with a small fake daemon, as on the
  command line (the gap that let this and #100/#101 through).

**Verification:** Whole `tests/` directory: 988 passed, 0 failed, 0 errors. `ruff check bal/ tests/`: only the 3 I001 inherited from BAL. Live (Electrum 4.8.2, testnet, offline
daemon, throwaway wallets): list/get/set/reset work with no wallet,
including `bal_settings_set bal_will_settings` with a JSON object.

**Outcome:** DONE.

## 100. CLI/2: the will-executor commands work from the command line

**Date:** 2026-10-10

**Problem:** `bal_willexecutors_show`, `_add`, `_update` and `_select` stopped
with "unknown command: <url>": Electrum's `run_electrum` reads any option
called `url` as a payment request and exits. Over JSON-RPC they worked.
CLI review point 4, confirmed live.

**What changed (plan approved by the owner):**

- `bal/cli/commands.py`: the parameter is `executor_url` (was `url`) in the
  four commands, with a comment that says why. JSON-RPC callers must use the
  new name; the CLI is new, so the cost is low.
- `bal_willexecutors_select` deselects with `--value false` (documented).
- Tests: no `bal_*` command has a `url` parameter; Electrum's own parser
  puts the URL in `executor_url`; add/show/select run.

**Verification:** Whole `tests/` directory: 991 passed, 0 failed, 0 errors. `ruff check bal/ tests/`: only the 3 I001 inherited from BAL. Live: show, add, update and select work from
the command line.

**Outcome:** DONE.

## 101. CLI/3: updating a will-executor no longer fails

**Date:** 2026-10-10

**Problem:** `bal_willexecutors_update` passed the promo code by position,
but `BalController.willexecutors_update` had no `promo_code` parameter:
`TypeError`, also over JSON-RPC. CLI review point 5.

**What changed (plan approved by the owner):** `willexecutors_update` takes
`promo_code` (saved in the record; an empty one removes it). Test: update
with every field through the command layer.

**Verification:** Whole `tests/` directory: 992 passed, 0 failed, 0 errors. `ruff check bal/ tests/`: only the 3 I001 inherited from BAL. Live: `--info ... --promo_code SAFE21` saved.

**Outcome:** DONE.

## 102. CLI/4: the delivery date no longer moves at every daemon restart

**Date:** 2026-10-10

**Problem:** the default will settings hold absolute dates computed when the
plugin starts (today + 1 year). The command line never saved them, so a
daemon restarted on a later day had a new default, one day later, and a
signed will looked "postponed": `bal_will_check` asked for an on-chain
invalidation (a fee). The GUI saves the settings when the wallet window
opens, so GUI users did not see it. CLI review point 2, confirmed live with
the daemon restarted with the clock one day later.

**What changed (plan approved by the owner):** `BalController._init_settings`
saves the default the first time it is used, like the GUI. Test: the first
wallet command saves it, and a new default one day later does not change
the date of a new heir.

**Verification:** Whole `tests/` directory: 993 passed, 0 failed, 0 errors. `ruff check bal/ tests/`: only the 3 I001 inherited from BAL. Live: prepare and sign, restart "one day
later": `bal_will_check` answers valid (before: postponed).

**Outcome:** DONE.

## 103. CLI/5: a per-heir delivery date is refused or checked, never dropped silently

**Date:** 2026-10-10

**Problem:** `bal_heirs_add`/`_update --locktime` accepted anything ("domani",
which gives 0, or a 2017 date). With "multiverse" off (the default, and the
only GUI mode) every heir gets the will's date, so the next check/prepare
overwrote the per-heir date without a word. CLI review point 6.

**What changed (plan approved by the owner):** `_check_heir_locktime`: with
multiverse off, `--locktime` is refused with a message that says how to
change the date for every heir (`bal_will_settings`); with multiverse on, a
date that is not understood or already past is refused. Tests for both
modes.

**Verification:** Whole `tests/` directory: 996 passed, 0 failed, 0 errors. `ruff check bal/ tests/`: only the 3 I001 inherited from BAL. Live: `--locktime 2y` and `1500000000`
refused with the message; adding the heir without it works.

**Outcome:** DONE.

## 104. CLI/6: messages instead of Python tracebacks

**Date:** 2026-10-10

**Problem:** a wrong path in `bal_heirs_import`/`_export`,
`bal_willexecutors_import`/`_export` and `bal_will_export` printed a Python
traceback and "internal error while executing RPC". A bad heir address said
"not an address, not an address,<address>". CLI review point 7 (its other
cases were already fixed by #78).

**What changed (plan approved by the owner):** `_user_facing` turns
Electrum's `FileImportFailed`/`FileExportFailed` into the message they carry
(what failed and why), and the address error into "Not a valid address for
this network: <address>". Tests for the five file commands and the address.

**Verification:** Whole `tests/` directory: 1002 passed, 0 failed, 0 errors. `ruff check bal/ tests/`: only the 3 I001 inherited from BAL. Live: every case gives the message, exit
code 1.

**Outcome:** DONE.

## 105. CLI/7: results that scripts and AI can read

**Date:** 2026-10-10

**Problem (CLI review point 10, confirmed live):**

- `bal_will_broadcast`: when every push failed it ended with exit code 0 and
  only "Failed"; the reason ("You are offline.", the server's answer) was
  lost in a debug log.
- `bal_will_autorebuild`: "rebuilt ... signed and pushed" even when no
  will-executor received the will.
- `bal_heirs_import`: `imported` was the total number of heirs, and invalid
  entries were dropped silently (a file with three bad heirs said
  `imported: 3` and added one).
- `bal_willexecutors_import`: `imported` counted the entries already present.
- `bal_heirs_delete`/`bal_willexecutors_delete`: a plain name (without the
  JSON brackets) was refused, and unknown names were not reported.

**What changed (plan approved by the owner):**

- `bal/core/willexecutors.py`: a failed push keeps its reason
  (`broadcast_error`, on the push copy of the record, never saved).
- `push_transactions_to_willexecutors` returns `_failed: {url: reason}`; a
  server still busy at the deadline is "no answer in time".
- New `broadcast_command`: when no will-executor received the will, the
  command ends with an error (exit code 1) that names each one and its
  reason; otherwise the JSON result, with `_failed` for the others.
- `bal_will_autorebuild` answers `rebuilt_not_sent` in that case.
- `bal_heirs_import` returns `imported`, `names`, `skipped` (reason per
  entry) and `total`; with multiverse on, a date not understood or past is
  skipped too. `bal_willexecutors_import` returns `imported` (added ones),
  `already_present` and `total`.
- The delete commands take one name or a JSON array and return `not_found`.

**Verification:** Whole `tests/` directory: 1010 passed, 0 failed, 0 errors. `ruff check bal/ tests/`: only the 3 I001 inherited from BAL. Live: broadcast offline ends with the reason
per will-executor and exit code 1; autorebuild after new funds offline
answers `rebuilt_not_sent`; the bad-heirs file gives `imported: 1` with two
reasons.

**Outcome:** DONE.

## 106. CLI/8: the command-line syntax in the README

**Date:** 2026-10-10

**Problem:** the README listed only the command names, and
`PLAN_CMDLINE_PLUGIN.md` (BAL's original design plan) shows a syntax that no
longer matches the code. CLI review point 11.

**What changed (plan approved by the owner):** README "Command-line /
headless usage": the most common forms (heirs, will-executors, settings),
the per-heir date rule, results and exit codes, and `electrum <command>
--help`. `PLAN_CMDLINE_PLUGIN.md`: a note at the top that its syntax is out
of date and where the current one is. Documentation only.

**Verification:** every README example was run live (2026-10-10).
Whole `tests/` directory: 1010 passed, 0 failed, 0 errors. `ruff check bal/ tests/`: only the 3 I001 inherited from BAL.

**Outcome:** DONE.

## 107. CLI/9: after new funds the command line rebuilds the will and keeps the date in step

**Date:** 2026-10-10

**Problem (found in the live CLI test of 2026-10-10, new):** after new funds
arrived in a wallet with a signed will:

- `bal_will_prepare` stopped with "Error: alice Please, check your heirs,
  locktime and threshold!": the will could not be prepared again. Right
  after a rebuild the check reports that the new will differs from the old
  one; the Check dialog treats that as "sign it" (bugs E/F/K), the command
  line as an error.
- `bal_will_autorebuild` rebuilt the will one day earlier (correct: the new
  will must be valid before the one the will-executors hold) but left the
  stored delivery date one day later, so the next `bal_will_check` /
  `bal_will_prepare` took it for a postponement and asked for an on-chain
  invalidation (a fee). The Check dialog moves the stored date
  (`_sync_locktime_to_built_txs`); the command line did not.

**What changed (plan approved by the owner):** `prepare_will` treats that
post-rebuild check as "rebuilt, needs signing" (and stops with a clear
message when nothing was built); after a rebuild, `prepare_will` and
`auto_rebuild` label the transactions and move the stored date to the
rebuilt will's date, with the Check dialog's rule (only earlier, only an
absolute date). Two tests reproduce both failures on the old code.

**Verification:** Whole `tests/` directory: 1012 passed, 0 failed, 0 errors. `ruff check bal/ tests/`: only the 3 I001 inherited from BAL. Live: new coin, then `bal_will_prepare`
answers rebuilt (before: error); new coin, then `bal_will_autorebuild`,
then `bal_will_prepare` answers coherent (before: postponed).

**Outcome:** DONE.

## 108. The Bitcoin After Life will-executor cannot be deleted, only deselected

**Date:** 2026-10-10

**Problem:** deleting the Bitcoin After Life will-executor "worked" from the
GUI and the command line, but on testnet it came back selected at the next
start (`get_willexecutors` adds the default list back), while in the same
session it stayed deleted. CLI review point 9. Found on the way:

- on a fresh install the stored will-executor list WAS the plugin's
  built-in default dict: adding, deleting or selecting a will-executor
  changed the defaults themselves in memory until Electrum was restarted (a
  will-executor added there became a "default"). That is why point 9
  behaved differently from one session to the next;
- on mainnet the BAL will-executor is not added by default at all: the
  plugin looks up the network "bitcoin", the default list says "mainnet"
  (since BAL v0.2.8). It arrives from the welist download of the Wizard.
  Left as it is (owner decision).

**What changed (owner decision: it cannot be deleted, only deselected, on
every network, recognised by its URL):**

- `bal/core/willexecutors.py`: `Willexecutors.is_protected`; `save` and
  `get_willexecutors` never hand out or change the built-in defaults, only
  copies.
- `bal/core/messages.py`: `protected_executor_message`, one text for GUI and
  command line (translated in Italian and Spanish).
- Command line: `bal_willexecutors_delete` refuses it (and deletes nothing
  else in that call); `--rename_to` refuses it (renaming deletes the old
  URL); other fields and deselecting still work.
- GUI will-executor list: Delete keeps it (the others are deleted) and
  shows the message; renaming it in the URL cell or in the Edit dialog is
  refused with the message.
- New `tests/test_protected_executor.py`.

**Verification:** Whole `tests/` directory: 1018 passed, 0 failed, 0 errors. `ruff check bal/ tests/`: only the 3 I001 inherited from BAL. Live: delete and rename refused with the
message, `--info` works, another will-executor is deleted normally.

**Outcome:** DONE.

## 109. CLI/10: bal_will_autorebuild finishes the job

**Date:** 2026-10-10

**Problem:** `bal_will_autorebuild` is the command meant to run unattended
(e.g. once a day), but a coherent will answered `valid` even when it was
never signed, or signed but never received by a will-executor (the last
send failed, e.g. offline), and nothing was retried.

**What changed (owner decision, on Claude's advice):** `_finish_coherent_will`:
a coherent will that is not signed is signed (passwordless wallets;
encrypted ones get `needs_signing`), and one that no will-executor has
received is sent again. New results: `signed`, `sent`, `not_sent` (with the
reasons). Sending gives the selected will-executors the same signed
transactions, as after a rebuild: no coin moves. The test that expected
`valid` for a never-signed will now signs it first; new tests for the four
cases.

**Verification:** Whole `tests/` directory: 1021 passed, 0 failed, 0 errors. `ruff check bal/ tests/`: only the 3 I001 inherited from BAL. Live: prepare, then autorebuild offline
answers `not_sent` (signed, reason "You are offline."); on the encrypted
wallet `needs_signing`.

**Outcome:** DONE.

## 110. One rule for the delivery date after a rebuild

**Date:** 2026-10-10

**Problem:** with "Rebuild automatically on new transactions" on (off by
default), new funds made the GUI rebuild the will one day earlier, but the
stored delivery date stayed one day later: the next Check read it as a
postponement and asked to sign and broadcast an on-chain invalidation (a
useless fee). Reproduced in-process on the plugin code with the default
(absolute) date; the existing test used a relative "1y", which never had
the problem. The Check dialog and the rebuild at wallet close already did
the right thing. The same rule existed twice (Check dialog, command line
#107) and was missing here.

**What changed (plan approved by the owner, first step towards one engine
for GUI and command line):**

- `bal/core/will.py`: `Will.anticipated_delivery_to_store`, the rule in one
  place (only earlier, only an absolute stored date).
- The Check dialog (`_sync_locktime_to_built_txs`) and the command line use
  it instead of their own copies.
- `bal/gui/qt/window.py`: the automatic rebuild stores the new date after
  the rebuild (worker thread) and refreshes the date widgets afterwards on
  the GUI thread.
- New `tests/test_core_anticipated_delivery.py`; a GUI test of the automatic
  rebuild with the default date (the next Check no longer asks to
  invalidate).

**Found while testing (not fixed here, owner decision: Phase 5e):** in the
real Electrum the automatic rebuild never starts at all. Electrum sends
wallet events on its asyncio thread, and the plugin schedules the rebuild
there with `QTimer.singleShot`, which never fires outside a Qt thread. The
rebuild at wallet close (on by default) covers new funds meanwhile. Details
and the proven fix in `PLAN.md`, Phase 5e.

**Verification:** Whole `tests/` directory: 1026 passed, 0 failed, 0 errors. `ruff check bal/ tests/`: only the 3 I001 inherited from BAL. The existing tests of the Check dialog's sync
(`test_sync_locktime_built_txs.py`, `test_build_report.py`) pass unchanged.
Live (owner, test ZIP, Electrum 4.8.2): after new funds, the rebuild at
wallet close moved the delivery date one day earlier through the shared
rule.

**Outcome:** DONE (the automatic rebuild path verified in-process only).

## 111. ruff: no findings left

**Date:** 2026-10-10

**Problem:** `ruff check bal/ tests/` reported 3 `I001` (unsorted imports)
inherited from BAL: `bal/gui/qt/dialogs.py`, `tests/test_core_animated_qr.py`,
`tests/test_core_qr_transfer.py`.

**What changed (owner request):** `ruff --select I001 --fix` on those three
files only: the imports of `dialogs.py` in alphabetical order (no statement
changed), one blank line removed in each test file.

**Verification:** `ruff check bal/ tests/`: All checks passed. Whole `tests/` directory: 1026 passed, 0 failed, 0 errors. `ruff check bal/ tests/`: all checks passed.

**Outcome:** DONE.

## 112. Phase 5e, step 1: photographs of today's "Check your will" dialog

**Date:** 2026-10-10

**Problem:** Phase 5e (plan approved by the owner on 2026-10-10) moves the
flows of the GUI and of the command line into one engine. The "Check your
will" dialog holds about 1,700 lines of decisions, and no pytest test ran
its real phase 1 or most of its endings: the existing tests call copies of
that code or single helpers. A move could change what the owner sees
without any test noticing.

**What changed (tests only, no plugin file):**

- New `tests/flowkit.py`: an offline world taken from the scenario harness
  (which stays outside the repository). A real Electrum testnet wallet made
  in a temporary folder from a key derived from a public label (no seed or
  key stored, no funds); fake will-executors and chain; a fixed clock
  (2026-10-10 12:00 UTC, moved only by a scenario); a stand-in for
  Electrum's `TaskThread` with Electrum's callback order (`cb_done` before
  `cb_success` / `cb_error`; the harness runs them the other way round).
  The REAL `BalBuildWillDialog` and `BalWindow` run; only their Qt and
  network edges are replaced and recorded. Everything is installed with
  pytest's `monkeypatch`, so nothing leaks into other tests.
- New `tests/test_5e_char_check.py`: 37 scenarios, each compared with a
  golden file in `tests/golden/5e/check/`: first will (basic, advanced),
  nothing changed, heir added / removed, fee changed, new coin, delivery
  later (signed and sent, basic and advanced), earlier, in the past,
  expired (basic, advanced), Check Alive passed with a fixed date / with a
  relative date (Renew, Not now, Renew already chosen at wallet open),
  Check Alive already past before the first will, no heirs, no
  will-executor, fixed amounts over the balance, percentages adding up to
  80 %, tiny fixed amounts, fee over the balance, Cancel at signing, Cancel
  at the invalidation, a will-executor that does not answer, one that
  answers "already present", one added, one deselected, Auto-sign off, an
  error while signing, the inheritance already executed, an invalidation
  Electrum does not see yet, the check at wallet close, Retry and Exclude.
  Recorded: every row (also the overwritten ones), the report, the button
  rows, popups, questions and password prompts, will items with statuses,
  dates, fees and History labels, settings, heirs, broadcasts, pushes and
  verifications. One more test runs a scenario twice and compares.
- A golden file is rewritten only with `BAL_GOLDEN_UPDATE=1`, and only
  together with a CHANGELOG line that says why.
- Outside the repository (owner's OK): the scenario harness baseline on
  `main` 5a4df32, `context/bal-test-harness/log-5e-baseline-5a4df32.txt`,
  for the comparisons of the next steps.

**Verification:** The 38 new tests pass 3 times in a row with the golden
files unchanged. Whole `tests/` directory (normal order and files in reverse
order): 1063 passed, 1 failed, 0 errors. The failure is not caused by this
change: `test_cli_controller_offline.py::test_auto_rebuild_threshold_passed_does_not_invalidate`
fails the same on unchanged `main` 5a4df32 whenever it runs after about
13:00 UTC (it sets the Check Alive one hour in the past; #80 converts it to
whole days before the delivery, rounded, and in the afternoon the rounding
lands on tomorrow, so the Check Alive is no longer past). It passed in the
morning runs of #99-#111; a separate fix is proposed to the owner.
`ruff check bal/ tests/`: all checks passed. Harness baseline: full matrix
as before (all OK or INFO, B2/B2adv one day earlier, the side effect
accepted in #73); A17, A18, A19, B5fix2, B5self2 OK in 6 runs out of 6;
both BUG-3 probes: nothing still executable, 6 runs out of 6; warn_matrix
synced: 134 Checks, 0 mismatches.

**Outcome:** DONE (tests only; the test ZIP would be identical, so none).

## 113. A command-line test failed every afternoon

**Date:** 2026-10-10

**Problem:** `tests/test_cli_controller_offline.py::test_auto_rebuild_threshold_passed_does_not_invalidate`
failed whenever the suite ran after about 13:00 UTC, also on unchanged
`main` (found during #112). The test sets the Check Alive one hour in the
past. Since #80 the plugin keeps the Check Alive as whole days before the
delivery, rounded: in the afternoon (UTC) "one hour ago" is rounded to
tomorrow, which is not past, so the test saw no passed Check Alive. The
plugin is right; the test depended on the time of day. The runs of
#99-#111 were in the morning.

**What changed (test only):** the test sets the Check Alive two days in the
past, with a comment that says why.

**Verification:** the test run with the clock set to 00:30, 06:30, 11:30,
12:30, 13:30, 15:30, 18:30 and 23:30 UTC (a scratch test with the
`tests/flowkit.py` clock): the old version fails at 4 of these hours, the
new one passes at all 8. Whole `tests/` directory: 1064 passed, 0 failed,
0 errors. `ruff check bal/ tests/`: all checks passed.

**Outcome:** DONE.

## 114. Phase 5e, step 2: photographs of the other copies and the list of their differences

**Date:** 2026-10-10

**Problem:** besides the "Check your will" dialog (#112), the same flows
exist in other copies: the Will tab menu Prepare, the automatic rebuild,
the menus Sign, Broadcast will and Invalidate will, the row command
"check", the check at wallet close and the command line. The Phase 5e
analysis listed 58 differences between the copies (D1-D58), most of them
deduced from the code and never run. Before one engine replaces the copies,
each difference must be known, shown on real runs and decided by the owner.

**What changed (tests only, no plugin file):**

- New `tests/test_5e_char_copies.py`: 56 scenarios, each compared with a
  golden file in `tests/golden/5e/copies/`: Prepare (13), the other menus,
  row check and wallet close (8), the automatic rebuild through
  `BalWindow.maybe_auto_rebuild` (11, with and without a network), the
  command line through `bal.cli.commands._call` (18, the message the
  terminal prints), and the same state through several copies (6).
- New `tests/test_5e_divergences.py`: the list D1-D61 with a status for each
  (pinned, partly, intentional, not reproduced, with the reason and the
  Phase 5e step that deals with it) and one test per pinned difference
  that reads the golden files and asserts today's outcomes side by side.
  A later step that makes the copies agree flips its test in the same
  commit, after the owner's decision.
- `tests/flowkit.py`: entry points for these copies; every record now also
  holds the will and the heirs as stored in the wallet and the History
  labels. Two corrections to the #112 world, to be closer to the plugin:
  the real `BalWindow.get_wallet_password` runs (a wallet without a
  password never opens the password window, as in Electrum; the two Cancel
  scenarios use a test wallet with a password and give the same result as
  before), and the classic invalidation runs the real
  `BalWindow.invalidate_will` (only Electrum's transaction window is
  replaced). The #112 golden files change only in the password questions,
  the new "stored" / "labels" fields and the two "expired" scenarios; rows,
  reports, statuses and dates are identical.

**Confirmed on real runs (were deductions):** D1 (`bal_will_prepare`
refuses the README example "alice 50%" and fixed-only heirs), D7, D11 (the
Prepare menu shows the #107 error after a fee change or new funds), D14
(Prepare does not move the stored date), D16 (Prepare does not save the
rebuilt will), D25 (the GUI automatic rebuild signs but never sends), D26
(with a network the GUI automatic rebuild broadcasts an invalidation by
itself; owner decision 2026-10-10: never), D29 (Cancel at the password
does not stop the menu Sign), D32, D33 (the menu says "All transactions are
broadcasted" when a will-executor did not answer), D35, D38, D44 (a
command-line read deletes an invalid heir from the wallet). D3 was only
partly right: with the will already sent, Check says "nothing to do".

**Found while taking the photographs:** D59 (two different CLI sentences
for "no will-executor"), D60 (the GUI automatic invalidation is not saved:
the wallet keeps the old will VALID), D61 (`bal_will_prepare` on a will past
its date answers with an amount error).

**Verification:** whole `tests/` directory, normal order and files in
reverse order: 1156 passed, 0 failed, 0 errors (1026 + 38 of #112 + 92
new). The new golden files are the same in 3 runs in a row. `ruff check
bal/ tests/`: all checks passed. No plugin file changed, so the scenario
harness and the test ZIP are unchanged (baseline of #112).

**Outcome:** DONE (tests only).

## 115. Phase 5e, step 3: the engine folder and the Check dialog's pure helpers in the core

**Date:** 2026-10-10

**Problem:** Phase 5e moves the flows of the plugin (check, build, sign,
send, invalidate) into one engine that the GUI and the command line share.
The engine must never need Qt, and the sentences and checks of the "Check
your will" dialog that need no widget were methods of the dialog, so a
GUI-free engine could not use them.

**What changed (no visible change):**

- New package `bal/engine/` (only its docstring for now): the shared
  engine of Phase 5e. Its rules: no Qt or GUI, no `bal.cli`, relative
  imports only (the zip loads the plugin as `electrum_external_plugins.bal`).
- New `tests/test_layering.py`: `bal/core`, `bal/engine` and `bal/cli`
  import no `PyQt*`, `electrum.gui` or `bal.gui` (also inside functions),
  the engine does not import `bal.cli`, and no layer imports the plugin
  with an absolute `bal.` name. Checked on the source and by importing
  every module of the three layers in a separate Python process, which
  must load no Qt module. The rules already held before this change.
- Moved without changing a word, with explicit arguments instead of the
  dialog's state:
  - to `bal/core/messages.py`: `rebuild_reason_message` (#77),
    `amounts_message` (#76, #82), `past_date_message` (#75),
    `build_failure_message`;
  - to `Will` in `bal/core/will.py`: `executed_inheritance_status` (#7a),
    `unsigned_will_executors` (#77), `amount_warning_is_false_alarm` (#76).
- `bal/gui/qt/dialogs.py`: the old methods keep their names as bridges
  that read the dialog's state and call the core (243 lines out, 44 in).
  They still never raise when the window state cannot be read, as before.
- `tests/test_group_h_v048.py` tested a copy of the "inheritance already
  executed" rule; it now tests the real one.
- New `tests/test_core_messages_moved.py`: the moved functions case by
  case, and the bridges (dialog state passed, never raising).
- Catalogs refreshed with pybabel: the same 597 entries in `bal.pot` and
  643 in each `.po`, with the same texts, translations and flags (compared
  entry by entry); only the order and the source locations changed.

**Verification:** whole `tests/` directory, normal order and files in
reverse order: 1173 passed, 0 failed, 0 errors (1156 + 17 new). The 93
golden files of #112 and #114 are unchanged (compared, not rewritten).
`ruff check bal/ tests/`: all checks passed. `build_zip.py`: the zip holds
`bal/engine/`; on Electrum 4.8.2 `external_zip_test.py` and
`smoke_test.py` pass, and `bal.engine` and the moved functions import and
run from the zip without Qt. Tests run on Electrum 4.8.2 only (owner
decision, 2026-10-10). Scenario harness (full matrix, the 2-coin scenarios and the
bug-3 probes 6 times, warn_matrix synced and not synced): the log is the
same as the #112 baseline line by line (transaction ids aside), including
the known B2/B2adv result; warn_matrix 134 Checks, 0 mismatches.

**Outcome:** DONE. The owner tested the ZIP: it works well.

## 116. Command line: the bal_* commands work off Electrum's network thread

**Date:** 2026-10-10

**Problem:** Electrum runs the body of every command on its network
(asyncio) thread, and the `bal_*` commands did all their work there. The
plugin talks to the will-executors through `Network.send_http_on_proxy`,
which Electrum refuses on that thread and which, called from the worker
threads of the parallel send, check and ping, waits for that same thread.
The live checks of #99-#111 used an offline daemon, so it never showed.
Probe on a daemon with a network (testnet test wallet, three fake
will-executors on 127.0.0.1, one answering after 3 seconds, nothing sent
outside the machine), before this change:

- `bal_will_broadcast`: after about 21 seconds "no answer in time" for all
  three and the will stays not sent; the requests reach the will-executors
  only after the command has given up;
- `bal_will_check_executor`: after about 22 seconds every item is marked
  CHECK_FAIL, loses PUSHED, and this is saved in the wallet;
- `bal_will_autorebuild`: signs, then "no will-executor received it" after
  about 22 seconds (same cause as the send);
- `bal_willexecutors_download`: fails at once with "must not be called
  from asyncio thread";
- `bal_willexecutors_ping`: never returns; the terminal gives up after 60
  seconds and the daemon answers no other command until it is killed;
- while any of these commands waits, the daemon does not even answer
  `getinfo`.

**What changed:**

- `bal/cli/commands.py`: every command (31) runs its work on a worker
  thread (`loop.run_in_executor`), so the network thread stays free. The
  controller (`bal/cli/controller.py`) is unchanged; `_call` and
  `_settings_call` are unchanged too (the golden files of #114 call
  `_call` directly).
- New `bal/engine/locks.py`: `flow_lock_for(wallet)`, one lock per wallet.
  Before, the commands could not overlap because each one blocked the
  network thread; now two commands on the same wallet run one after the
  other, while different wallets and the settings commands do not wait.
  A command that cannot get its wallet within 20 seconds (owner decision)
  ends with "Another command is still working on this wallet: try again in
  a moment." The command line stays English (`babel.cfg` does not extract
  `bal/cli`), so there is no new catalog entry. Later Phase 5e steps use
  the same lock for the GUI flows.
- New `tests/test_cli_off_loop.py` (12): the commands run through
  Electrum's own command wrappers on an event loop, with a fake `Network`
  that follows Electrum 4.8.2's rules (refuses the event-loop thread, runs
  each request as a coroutine on that loop) and three will-executors, one
  slow: ping, list download, send (also "already present", which asks the
  will-executor at once), check; one command at a time per wallet,
  different wallets in parallel, the busy message, settings while a wallet
  is busy, the lock released after an error. On the code before this change
  8 of the 12 fail (the other 4 cover what was already true).

**After this change** (same probe; the slow will-executor adds 3 seconds):
the send reaches all three and marks the will sent (4.6 s); the check marks
all three CHECKED (4.6 s); the automatic rebuild sends to all three (8.1 s);
the list download gets the three (1.9 s); the ping answers for all three
(4.2 s); the daemon answers `getinfo` while a command is working; a send
and a ping started together on the same wallet run one after the other.

**Verification:** whole `tests/` directory, normal order and files in
reverse order: 1185 passed, 0 failed, 0 errors (1173 + 12 new); the golden
files of #112 and #114 unchanged. `ruff check bal/ tests/`: all checks
passed. `build_zip.py`: the zip holds `bal/engine/locks.py`; on Electrum
4.8.2 `external_zip_test.py` and `smoke_test.py` pass. Scenario harness
(the GUI is not touched): log identical to the #112 baseline, warn_matrix
134 Checks, 0 mismatches.

**Outcome:** DONE. The owner tested the ZIP: it works.

## 117. Phase 5e, step 5e-05: the engine's state bricks, taken from the command line

**Date:** 2026-10-10

**Problem:** the steps that load, save and check the will state are written
twice: in the command line's `BalController` and in the GUI's `BalWindow`,
with small differences (when the wallet file is written, how a setting is
stored, the History redraw). Phase 5e gives them one home in the engine.

**What changed (no visible change):**

- New `bal/engine/context.py`: `load_willitems`, `save_willitems`,
  `save_to_history`, `available_utxos`, `refresh_invalidated_items`,
  `check_alive_info`, `normalize_check_alive`, `init_class_variables`,
  `init_heirs_to_locktime`, `check_will`, copied without rewriting from
  `bal/cli/controller.py`. They work on the state of their caller (the
  "host").
- New `bal/engine/host.py`: `WalletHost`, what the engine reads (the state
  names the GUI and the command line already use, `bal_plugin` included)
  and three hooks for what the two do differently: `store_will_setting`
  (the Check Alive conversion of #80), `flush_wallet_db` (the command line
  writes the wallet file at once), `after_history_saved` (the GUI redraws
  the History tab).
- `bal/cli/controller.py`: the ten methods keep their names as one-line
  calls to the engine (the flows and the tests use them). New
  `bal_plugin` property and the three hooks, which do what the controller
  did before: `WILL_SETTINGS.set`, `wallet.save_db()`, nothing.
- The engine uses BAL's translator, like `bal/core`: in the daemon it is
  Electrum's (BAL's catalog is loaded only by the Qt plugin), so the
  command line answers exactly as before; only in Electrum's Console tab
  can "Heirs are not defined" now appear in the GUI language.
- The GUI is not touched: `BalWindow` switches to these bricks in the next
  step.
- New `tests/test_engine_context.py` (16): each brick on a fake host with
  the real plugin settings and three will-executors, each hook called
  exactly where the old code did the same, and `BalController` as a host.

**Verification:** whole `tests/` directory, normal order and files in
reverse order: 1201 passed, 0 failed, 0 errors (1185 + 16 new); the 93
golden files of #112 and #114 (the 18 command-line ones included)
unchanged. `ruff check bal/ tests/`: all checks passed; `test_layering`
covers the two new modules. `build_zip.py`: the zip holds
`bal/engine/context.py` and `host.py`; on Electrum 4.8.2
`external_zip_test.py` and `smoke_test.py` pass. Live probe of #116 (daemon
with a network, three fake will-executors on 127.0.0.1): send, check,
automatic rebuild, list download and ping give the same results as after
#116. Scenario harness (the GUI is not touched): log identical to the #112
baseline, warn_matrix 134 Checks, 0 mismatches.

**Outcome:** DONE. The owner tested the ZIP: it works.

## 118. Phase 5e, step 5e-06: the GUI window uses the engine's state bricks

**Date:** 2026-10-10

**Problem:** after #117 the command line used the engine's state bricks
(`bal/engine/context.py`), while the GUI window `BalWindow` still had its
own copies of the same steps.

**What changed (no visible change):**

- `bal/gui/qt/window.py`: `load_willitems`, `save_willitems`,
  `_save_will_to_history`, `_refresh_invalidated_items`,
  `_normalize_check_alive`, `check_alive_info`, `init_class_variables`,
  `init_heirs_to_locktime` and `check_will` keep their names (the window's
  flows, the dialogs, the tests and the scenario harness use them) and run
  the engine functions. Kept in the window: the Will tab refresh after
  loading, and the error logging of `init_class_variables` ("no heirs" is
  raised without a log, as it came before the old `try`; every later error
  is logged with `log_error` and raised again).
- The window's three engine hooks do what the window did before:
  `store_will_setting` calls `update_setting_widgets(value, field,
  update_all=True)` (the call of the old `_normalize_check_alive`),
  `flush_wallet_db` does nothing (the window never wrote the wallet file in
  these steps), `after_history_saved` schedules the History redraw.
- Differences between the two copies checked to change nothing: the window
  stored `NO_WILLEXECUTOR` as read, the engine as `bool()` (only used as
  yes/no); the window passed `bal_window=self` to
  `Willexecutors.get_willexecutors` (the parameter is read only in
  commented-out code); the window passed `callback_not_valid_tx` to
  `Will.is_will_valid` (never called by the core; `delete_not_valid`
  raises `NotImplementedError`); the window read `self.window.wallet` in
  two steps, the engine `self.wallet` (the same object, set in
  `BalWindow.__init__`).
- Not in this step: `update_all` (the refresh of the statuses and the
  lists). Its status half exists in five copies; it gets its own step.
- Tests: `tests/flowkit.py` also gives `bal.engine.context` the scenario's
  fixed clock (the window's `_refresh_invalidated_items` read the window's
  clock and now runs there); `tests/test_import_will_details.py`: its
  hand-made window gets the `flush_wallet_db` hook. New
  `tests/test_gui_engine_host.py` (17): the window's hooks, every step
  calling the engine on the window, the Will tab refresh, the error logging.

**Verification:** whole `tests/` directory, normal order and files in
reverse order: 1218 passed, 0 failed, 0 errors (1201 + 17 new); the 93
golden files of #112 and #114 (they run the real `BalWindow`: Check,
Prepare, automatic rebuild, wallet close) unchanged. `ruff check bal/
tests/`: all checks passed. Scenario harness (it runs a `BalWindow`
subclass): log identical to the #112 baseline, warn_matrix 134 Checks, 0
mismatches. `build_zip.py`: on Electrum 4.8.2 `external_zip_test.py` and
`smoke_test.py` pass. Independent review: three reviewers (line-by-line
equivalence; callers, overrides and fakes; clocks, threads and
persistence) found no behaviour difference.

**Outcome:** DONE. The owner tested the ZIP: it works.

## 119. Phase 5e, step 5e-07: one build brick for the Check, Prepare, the automatic rebuild and the command line

**Date:** 2026-10-11

**Problem:** three steps of a rebuild were written in several places:
building the will items from the heirs (`BalWindow.build_will`, used by the
Check dialog, the Prepare menu and the automatic rebuild, and
`BalController.build_will` for the command line), labelling the rebuilt
transactions "Safe21 Heartbeat backup transaction" in the History (Check
dialog, Prepare menu, command line), and moving the stored delivery date to
the rebuilt will's date (Check dialog, automatic rebuild, command line;
CHANGELOG #107 / #110).

**What changed (no visible change):**

- New `bal/engine/build.py`: `build_will(host, with_creation_date)` (the
  GUI's version; the record of each item built once), `label_rebuilt(host,
  scope, on_error)` and `sync_delivery_date(host, store, on_error)`.
- The callers keep today's behaviour through the parameters (the owner
  decides later whether they should agree):
  - `with_creation_date`: the GUI passes the old will's creation date when
    it re-anchors `date_to_check`, the command line does not;
  - label `scope`: the Prepare menu labels every item, the Check dialog and
    the command line the VALID ones; `on_error`: the command line logs a
    label it cannot write and goes on, the GUI stops;
  - date `store`: the Check dialog remembers the anticipation (for the sign
    prompt) and refreshes the date fields; the automatic rebuild stores the
    setting on its worker thread and marks the date for the refresh on the
    GUI thread (an error of the rule is only logged); the command line
    stores it through its `store_will_setting` hook;
  - unchanged: the Prepare menu does not move the date, the automatic
    rebuild does not label.
- `bal/gui/qt/window.py`, `bal/gui/qt/dialogs.py`, `bal/cli/controller.py`:
  the method names stay (`build_will`, `_after_rebuild`, the two
  `_sync_*` methods; callers and tests use them) and call the brick.
  `BalWindow.update_will` (only used by the old `build_will`) is part of
  the brick now.
- Differences between the two `build_will` copies checked to change
  nothing: the GUI read `self.window.wallet` (the same object as
  `self.wallet`) and passed `bal_window=self` to `get_willexecutors` (read
  only in commented-out code); the GUI wrapped the build in a
  `try`/log/raise, the command line did not (the same exception reaches the
  caller); log lines.
- Tests: `tests/flowkit.py` gives `bal.engine.build` the scenario clock
  (the creation time of the built items); in the scenarios the command
  line's builds now get it too (before #119 they read the real clock; no
  golden file depends on it). `tests/test_core_status_language_neutral.py`
  checks the one builder `bal/engine/build.py` (the stored status "New" is
  never translated). New `tests/test_engine_build.py` (21), with three
  will-executors (one not selected, one over the maximum fee, one valid):
  each parameter and each caller's choice.

**Verification:** whole `tests/` directory, normal order and files in
reverse order: 1239 passed, 0 failed, 0 errors (1218 + 21 new); the 93
golden files of #112 and #114 unchanged (they build the will through the
Check dialog, Prepare, the automatic rebuild and the command line).
`ruff check bal/ tests/`: all checks passed. Scenario harness: log
identical to the #112 baseline, warn_matrix 134 Checks, 0 mismatches.
`build_zip.py`: the zip holds `bal/engine/build.py`; on Electrum 4.8.2
`external_zip_test.py` and `smoke_test.py` pass. Live command line (daemon
with a network, three fake will-executors on 127.0.0.1), the same before
and after: an heir added, `bal_will_prepare` rebuilds three items dated one
day earlier, the old ones REPLACED, the stored date follows (1823126400 ->
1823040000), sign and send reach the three. Independent review: three
reviewers (build line by line; labels and dates; callers, fakes and
patches) found no behaviour difference.

**Outcome:** DONE. The owner tested the ZIP on his wallet: a test heir
added, then removed, each followed by "Check your will": both rebuilds
were signed, sent 4/4 and verified 4/4 (delivery date now two days
earlier, as expected). The History tab shows no new rows: expected and
unchanged, since signed backup transactions are never kept in the wallet's
local history (`Will.save_valid_transactions_to_history` removes complete
ones); the "Safe21 Heartbeat backup transaction" label is stored on their
txids (the golden file `heir_removed` records it).

## 120. Phase 5e, step 5e-08: the Check's variables, Check Alive and amounts in the engine

**Date:** 2026-10-11

**Problem:** "Check your will" starts with the "Checking variables" row: the
heirs, the Check Alive gate (after the delivery, passed: expired / renew /
stop) and the heirs' amounts (fixed amounts above the balance, percentages
not adding up to 100, a will-executor fee too high). That code was written
inside the dialog (`BalBuildWillDialog.task_phase1`, about 150 lines), where
the engine shared by the dialog, the automatic rebuild and the command line
cannot use it.

**What changed (no visible change):**

- New `bal/engine/check.py`: `check_variables(host, run, ui)`, a line by line
  port of that part of `task_phase1`; `Phase1Result` (why phase 1 stops
  early) with `legacy()`, the `(kind, tx)` tuple `task_phase1` returned
  before; `past_date_message_of` and `amount_warning_is_false_alarm`, the
  rules of the dialog's `_past_date_message` and
  `_amount_warning_is_false_alarm` (CHANGELOG #115), now in one place.
- New `bal/engine/ui.py`: the engine reports with codes and plain data,
  never texts or colours: `FlowUI` (`step`, `note`), `Step`, `CheckRun` (the
  state a Check keeps between passes), `NullUI`, `RecordingUI` (tests).
- New `bal/gui/qt/flow_adapter.py`: `DialogFlowUI` turns each event into the
  very `msg_*` call the dialog made before (same label, translated or not,
  status, colour, row and order), so the rows, the step bar and the report
  boxes stay the same; `DialogRun` reads and writes the run state in the
  dialog's own attributes (`_invalidation_broadcast`, `_check_alive_info`)
  at the moment the engine does. Both are made anew at each pass, as the old
  local row index was: no dialog attribute is added.
- `bal/gui/qt/dialogs.py`: `task_phase1` calls `check_variables` and returns
  `legacy()` when it stops; the rest of `task_phase1` is unchanged.
  `_past_date_message` and `_amount_warning_is_false_alarm` call the engine
  helpers (same results, also for the tests that borrow them). Five imports
  with no use left are removed.
- Kept as they were (owner decisions for later): the "Check Alive must be
  after today and before the delivery date" row stays untranslated (in a
  translated GUI the step bar does not recognise it); the "Will-executor fee
  too high" branch, which `Will.check_amounts` seems never to reach (an
  executor above the maximum fee is not valid there), is ported unchanged;
  the settings write of a converted Check Alive still happens on the worker
  thread through `update_setting_widgets`.
- Differences from the written plan step (owner OK, 2026-10-11): the
  questions asked between phases (`Questions`) and the `CANCELLED` answer
  move to step 5e-11, where they are first used; `FlowUI` has only the two
  events and `CheckRun` only the two fields this part uses. The bare
  `except Exception as e: raise e` is not copied (the same exception reaches
  the dialog's error path). Debug log lines of this part now come from the
  logger `bal.engine.check`.
- Catalogs not refreshed: no text was added, removed or changed (the set of
  597 extracted texts is identical); only the source locations of eight
  texts moved to `flow_adapter.py`, and the locations are already out of
  date since #117. The next catalog refresh updates them all.
- Tests:
  - new `tests/test_gui_check_variables_rows.py` (25): written and run on
    the code BEFORE the move, unchanged after it (re-run on `f2e9a1b`
    without the engine module: 25 passed). It photographs every branch of
    this part on the real dialog class, also the ones the golden files do
    not reach (Check Alive after the delivery, an invalidation sent but not
    seen yet, a Check Alive with no information, a renewal with no
    transaction, a will-executor fee too high, other errors, a closing
    dialog), with the rows ever written, the report, the return value and
    the run state; the "invalidation sent but not seen yet" case also on
    the real flow of `tests/flowkit.py` (Renew, the wallet lags);
  - new `tests/test_engine_check_variables.py` (33): each branch through
    `RecordingUI` on a fake host with three will-executors (own fee
    addresses), two with the real heirs and amount checks (50% + 30%: the
    sentence of the golden file `percentages_not_100`);
  - new `tests/test_gui_flow_adapter.py` (18): each event writes today's
    exact row and report entry, also in a translated GUI; every event the
    engine declares has a row.

**Verification:** whole `tests/` directory, normal order and files in
reverse order: 1315 passed, 0 failed, 0 errors (1239 + 76 new); the 93
golden files of #112 and #114 unchanged. `ruff check bal/ tests/`: all
checks passed. Mutation check of the new tests: 12 of 14 deliberate errors
in the port make them fail; the other 2 change no behaviour. Scenario
harness: log identical to the #112 baseline (192 lines), warn_matrix 134
Checks, 0 mismatches. The full harness output (every dialog row, 3155
lines) on `main` and on the branch: the 206 "Checking variables" rows are
identical; the only other differences are the temporary folder names in
the error tracebacks and, in scenario A16 (three will-executors), the order
in which they answer the verification (it changes from run to run on `main`
too: six runs each) and once the shown fee rate of the invalidation (13.12
instead of 13.00 sat/vB, same fee; 13.00 in the twelve re-runs).
`build_zip.py`: the zip holds `bal/engine/check.py`, `bal/engine/ui.py` and
`bal/gui/qt/flow_adapter.py`; on Electrum 4.8.2 `external_zip_test.py` and
`smoke_test.py` pass. Independent review: three reviewers (the port line by
line; the rows, the report and the translation, with a probe comparing 48
runs of the old and the new code byte by byte; tests and rules), each
finding checked by two more: no behaviour difference; a wrong docstring and
two weak or undocumented test helpers were fixed.

**Outcome:** DONE. The owner tested the ZIP on his wallet (Electrum 4.8.2,
basic mode): 1) a test heir with a fixed amount (50,000,000,000 sat) above
the balance (59,576 sat): "Checking variables: Stopped" in red, the
explanation in the problems box, "The check stopped: nothing was changed",
only Close, no password asked; 2) the test heir removed, Check: Check ok,
nothing to prepare, sign or send, verified 4/4, the will unchanged; 3)
heirs at 50% + 30%: the amber "percentages add up to 80%" sentence, the will
rebuilt one day earlier, signed, sent 4/4 and verified 4/4.

Found during the test, not caused by #120 (a separate step): in basic mode
the delivery field of the Will tab kept showing a date one day later than
the will (also after a restart), while the will summary and the wizard
showed the will's date. Cause: the owner's delivery setting is relative
("1054d", set earlier in advanced mode); in basic mode the toolbar's date
field turns it into a date when the window opens, before the will is
loaded, so it shows "today + 1054 days" and is not redrawn afterwards.
Reproduced on a test wallet with the real toolbar and wizard widgets: the
same on this branch, on `main` `f2e9a1b` and on `74e3797` (before #119);
with a fixed delivery date the field follows the will. Not a safety
problem: a relative setting is resolved against the signed will, so the
next Checks (heirs restored, the next day) neither invalidate nor ask
anything (simulated on the test wallet).

## 121. In basic mode the delivery field shows the date of the will in force

**Date:** 2026-10-11

**Problem:** found in the owner's ZIP test of #120 (see #120): in basic mode
the delivery field of the Will tab showed a date one day later than the
will in force, also after a restart, while the will summary of the Will tab
and the wizard showed the will's date. The owner's delivery setting is a
period ("1054d", saved earlier in advanced mode). In basic mode the field
uses the Date editor, which shows a period as the date it gives: the date
of the will in force, or today + the period with no will. The fields of the
Will and Heirs tabs are built when the window opens, before the will is
loaded, so they showed "today + 1054 days" and were never redrawn: the next
day, or after a rebuild that moved the will one day earlier (#107), the
field and the will disagreed. Display only: the stored setting and the
Check were right (a period is resolved against the signed will, so the next
Check neither invalidates nor asks anything), and the calendar export and
the Check Alive date already used the earlier of the field and the will.
The same behaviour on `main` before #120 and before #119.

**What changed (visible: the date shown in basic mode):**

- `bal/gui/qt/widgets.py`: new `BalTimeEditWidget.refresh_shown_value()`:
  a saved period is shown again as the date it gives now. It only shows
  (signals blocked, nothing saved); a fixed date is shown as saved and the
  Raw editor of advanced mode shows the period itself, so neither changes.
  `WillSettingsWidget.on_locktime_change` (called each time the Will or
  Heirs list is redrawn: after the will is loaded, after each Check) calls
  it first; the method got its docstring.
- Not changed: the stored setting ("1054d" stays "1054d"), the Check, fixed
  delivery dates, advanced mode, the wizard (built after the will is loaded,
  it already showed the will's date), the heir dialog's own date field.

**Tests:** new `tests/test_gui_delivery_field_refresh.py` (9), written first
and run on `main` `a981d8d`: the two real-flow tests failed there by exactly
one day (the bug), the advanced / fixed-date test passed (no change). The
field alone: basic period with the will loaded later shows the will's date,
without a will today + the period, advanced Raw text unchanged, fixed date
unchanged in both modes, no edit signal and nothing saved. The real flow
(`tests/flowkit.py`, three will-executors) with the real Will and Heirs tab
toolbars and the wizard: a rebuild that moves the will one day earlier (both
toolbars follow, the setting stays "1000d"); the next day a new window
built before the will is loaded (both toolbars and the wizard show the
will's date once it is loaded); advanced with a period and basic with a
fixed date as before (#110).

**Verification:** whole `tests/` directory, normal order and files in
reverse order: 1324 passed, 0 failed, 0 errors (1315 + 9 new); the 93
golden files unchanged. `ruff check bal/ tests/`: all checks passed. The
owner's sequence on a test wallet with the real toolbar widgets (basic,
"1000d"): on `main` the field stays on today + 1000 days after the rebuild
and after the restart; on this branch it shows the will's date in both.
Scenario harness: log identical to the #112 baseline (192 lines),
warn_matrix 134 Checks, 0 mismatches (it draws no toolbar, as expected).
`build_zip.py` + `external_zip_test.py` + `smoke_test.py` on Electrum
4.8.2: pass. No text added or changed (no catalog update).

**Outcome:** DONE. The owner tested the ZIP on his wallet (basic mode,
delivery "1054d"): right after opening the wallet the delivery field of the
Will tab and of the Heirs tab show 29/08/2029, the date of the will in
force (before: 30/08/2029, today + 1054 days).

Seen in the same test, not caused by #121 (to be fixed in a step of its
own): the will items of the owner's wallet were saved unsigned. Electrum's
log shows the check at wallet close (03:45:39, still on the #120 ZIP)
finding "The delivery date of heir "1" changed" (the delivery setting had
been edited in advanced mode), rebuilding transactions identical to the
signed ones already sent (same txids), overwriting the signed copies, and
the dialog closing before the signature. The will-executors keep the
signed will.

## 122. A rebuilt will identical to the signed one keeps its signatures

**Date:** 2026-10-11

**Problem (the owner's wallet on 2026-10-11, see #121; reproduced with
`tests/flowkit.py` on `main` `647f8d5`, three will-executors, test wallet
with a password):** the will in force was built from a fixed delivery date,
signed, sent and verified. The delivery setting then became the period
giving the same day ("1054d", the Raw/Date switch of advanced mode). At
wallet close the check rebuilt the will (the #81 rule) and the
transactions were identical to the signed ones (same txids), but
`Will.update_will` kept an old item only when its heirs were written
exactly the same way ("Option A", #17, helper `_same_heirs`): the date was
written another way, so fresh UNSIGNED copies replaced the signed and sent
ones. The owner closed the password window: the will was saved unsigned,
with an unsigned local copy in the History (balance shown as 0). The
will-executors kept the signed will; the owner recovered with "Sign and
send" (same signatures). The same happened, without any question, in the
automatic rebuild and the command line, and for an heir renamed.

**What changed (visible: no password asked when nothing changed in the
transactions):**

- `bal/core/will.py` `Will.update_will` (docstring added): a rebuilt
  transaction with the txid of a VALID old item is the same transaction,
  so the old item is kept (signatures valid, state signed/sent/verified
  right) and the new heirs, will-executor, description and fee rate are
  written on it. Every rebuild goes through it (Check, wallet close,
  Prepare, automatic rebuild, command line). The same transaction for
  another will-executor URL (same fee address and fee, e.g. an executor
  that moved) keeps the fresh item, so it is sent to that URL; it gets the
  signed transaction when the old one was signed (no password). Before,
  `main` rewrote the URL on the old item and marked it sent there without
  sending it. `_same_heirs` is removed.
- The fee rate is copied too: a fee rate giving the same transactions
  (13.001, possible with a JSON setting) made every Check say "Miner fee
  rate changed" forever.
- `bal/gui/qt/window.py` (Prepare) and `bal/cli/controller.py`
  (`prepare_will`, `auto_rebuild`): after a rebuild that left nothing to
  sign, no more "sign and send": Prepare says "Your will is in force", or
  "Next step (manual): press 'Broadcast will'..." when only sending is left
  (existing texts, no catalog update); the command line says "The will is
  coherent" (or to send it), and `auto_rebuild` answers `valid`/`sent`
  instead of `needs_signing`.
- `bal/cli/controller.py` `auto_rebuild`: the will is called coherent
  after the rebuild only when the check after the rebuild passed.
- `make-release.sh`: the deleted `tests/test_group_f_heir_change_rebuild.py`
  replaced by `tests/test_gui_date_form_change_flow.py` in its test list.
- Not changed (known, see #123): the check still rebuilds when the date is
  written as the equal period (row 'The delivery date of heir ... changed:
  preparing a new will.', then "Signing your will: Nothing to do"); with a
  period as delivery setting, an old replaced will built from a fixed date
  is still taken for a postpone two days later (paid invalidation; also on
  `main`). #123 removes the periods and converts the stored ones once to
  the fixed date of the will in force. A first version of this step also
  tried to fix those two in the plugin; a multi-agent review found that it
  could hide a real postpone when the earlier will has other heirs, and the
  owner decided to remove the periods (Raw) and the Check Alive instead.

**Tests:** written first; run on `main` `647f8d5`: 19 failed, 1 passed (it
pins behaviour that does not change).

- `tests/test_core_identical_rebuild.py` (7, new; replaces
  `tests/test_group_f_heir_change_rebuild.py`, 9 tests of `_same_heirs`,
  removed with the helper): the owner's case at the engine (same txids,
  still signed, sent and verified, same signed transactions and history,
  the period written in them); the new heirs, will-executor, description
  and fee rate written on the kept item; the same transaction for another
  will-executor URL sent signed (and an unsigned one stays unsigned); a
  replaced item not brought back; a backup transaction without
  will-executor kept signed; a renamed heir keeps the signed will.
- `tests/test_gui_date_form_change_flow.py` (13, new): the owner's sequence
  with the real Check dialog: wallet close with Cancel at the password
  (basic and advanced, one will and two wills): nothing asked, signed or
  sent, the saved will still signed, sent and verified; one will: the
  following days (+1, +2, +7) in order; the Check button; Prepare says
  "Your will is in force"; command line `prepare_will` and `auto_rebuild`
  on a wallet with a password; an executor that moved gets the signed will
  without a password; a fee rate giving the same transactions settles
  after one rebuild; a real postpone still asks for the invalidation.

**Verification:** whole `tests/` directory, normal order and files in
reverse order: 1335 passed, 0 failed, 0 errors (1324 - 9 removed + 20
new); the 93 golden files unchanged. `ruff check bal/ tests/`: all checks
passed. Scenario harness: log identical to the #112 baseline (192 lines),
warn_matrix 134 Checks, 0 mismatches. `build_zip.py` +
`external_zip_test.py` + `smoke_test.py` on Electrum 4.8.2: pass. Probes
on main and the branch (owner's sequence, basic and advanced, one and two
wills, Cancel at the password, following days; Prepare, automatic rebuild
and command line; executor that moved; fee rate 13.001; renamed heir;
backup transaction). Multi-agent review of a first version (5 reviewers,
2-3 skeptics per finding, a completeness critic): its findings on this
part are fixed here (fee rate, executor that moved, Prepare and command
line messages, make-release.sh, docstrings, tests). No text added or
changed in the catalogs.

**Outcome:** DONE. The owner tested the ZIP on his wallet (basic mode,
four will-executors): Check "ok", preparation, signature and sending "not
needed", verification 4/4, "Your will is in force", delivery 2029-08-29.

Found by the review and left for steps of their own (also on `main`):
replacing every heir together with a later date is rebuilt one day
earlier instead of invalidated; a later date on a will left unsigned
after a Cancel goes back to the old date; with per-heir dates (hidden
"multiverse" option) `Will.change_input` links an anticipated child to
its own txid; the GUI automatic rebuild never sends (`waiting_dialog`).

## 123. Each wallet keeps its own delivery date and fee rate

**Date:** 2026-10-11

**Problem (the owner's wallet on 2026-10-11, see #121 and #122; reproduced
during the design with `tests/flowkit.py` probes on `c530db7`, the #122
tree, three will-executors, test wallet with a password):** the delivery
date, the fee rate and the Check Alive were ONE Electrum setting for every
wallet (`bal_will_settings`, the key of the original BAL plugin). Changing
them in one wallet changed them for all, and a new wallet got them too. The
owner's setting was the period "1054d" (saved in advanced mode with the
Raw/Date switch). A period is counted again from "today" at every Check, so
from the second day an older will that was sent and then replaced, built
from a fixed date, read as a postpone: the Check asked for a paid
invalidation (Cancel cost nothing). Known since #122, also on `main`.
Design: `context/reports/design-123/plan.md` (two critics); owner decisions
and answers of 2026-10-11 in `PLAN.md` (series #123-#129).

**What changed (visible: a one-time note when the wallet opens, the
delivery field is always a date, two new command-line commands):**

- Where the settings live: each wallet keeps its own delivery date
  (`locktime`, a UNIX time, always a date), fee rate (`baltx_fees`) and
  Check Alive (`threshold`, "-Nd", read until #128) in its own file, under
  the wallet key `will_settings` (already registered, no new key). They are
  read through `wallet.db.get_dict("will_settings")` and written item by
  item, never with `db.put` or `dict.update`. Electrum's global
  `bal_will_settings` is never written, deleted or reset again: it stays
  for BAL (which reads its `threshold` with no fallback) and is read raw,
  once per wallet, only as a migration source.
- `bal/core/delivery.py` (new; pure: standard library only, no Qt, no
  Electrum, never reads the clock): the rules. Month arithmetic
  (`utc_midnight`, `add_months`); what a stored value means (`as_delivery`,
  `is_fee`, `is_current`, `validate_values`: a delivery date must be a
  UNIX time of at least 500,000,000, so a period or a block height is
  refused; a float or a numeric text is truncated to whole seconds; a
  stored float is not current, so the wallet is migrated again; a fee rate
  must be at least 1); the one-time migration (`migration_values`,
  `note_for`, `convert_check_alive`); the anchor (`record_will_anchor`,
  `check_anchor`, `earliest_signed_valid`); the wish for a later date
  (`later_wish_action`); the will in force for the command line
  (`pending_sent`, `earliest_in_force`); the note and the question; the
  two holds (`DeliveryChoicePending`, `DeliverySettingsUnavailable`);
  `parse_cli_date` for the command line (`YYYY-MM-DD` means 00:00 UTC;
  periods, block heights, values above 2^32-1, past dates and today are
  refused).
- One-time migration, when a wallet's settings are not current (no
  `settings_version` 1, or the date is not an int date, or no fee rate of
  at least 1). The date, first source that gives one: the earliest VALID
  item of the stored will (`migrated_from` "will"); else the earliest item
  sent to a will-executor and still pending, by the stored flags ("sent");
  else, only for a wallet that already has heirs or a will, the old global
  value, then the wallet's own old value (a period counts from today 00:00
  UTC, an absolute date as is, only a future date); else today + 12 months
  ("default"). The fee: the single fee rate of those items (read after
  `Util.fix_will_tx_fees` on a copy, so a legacy item gives its fee, never
  0), else the old value (only with heirs or a will), else 20. The Check
  Alive: the first "-Nd" of the old values; else, only with heirs or a
  will, an old fixed date (BAL 0.6.1 writes one) or an old "after creation"
  period, converted to "-Nd" against the new date with the #80 rule (the
  Check Alive date stays the same); else "-180d". Heirs that still hold a
  period get the date (multiverse off). `tx_fees`, `real_threshold` and
  `real_locktime` are removed; the old value, as found, stays in
  `legacy_locktime` (for the owner "1054d"). One log line: "will settings
  migrated: from will, delivery 2029-08-29, fee 13". The owner's wallet:
  2029-08-29 00:00 UTC (1882656000), fee 13, note "converted". With the
  multiverse option on (each heir carries its own date) the migration
  gives only the informative note ("converted" or "date_differs"): no
  "later_pending", no question, no later wish.
- Anchor: every save of the will records the VALID items of the STORED
  will (`anchor_valid`, `anchor_locktime`), never the list in memory. At
  every load, when another program (BAL, an older Safe21, an older ZIP)
  changed the will in force and that will is signed, the stored date
  follows it (later or earlier), with a note, so this plugin never
  rebuilds earlier to undo a paid postpone and never asks for a paid
  invalidation to go back. Unsigned drafts left by another program are not
  followed (`ANCHOR_UNSIGNED`): the date stays, no note, the anchor is
  recorded again, one log line.
- Later wish (`later_wish`): when the old shared value asked for a date at
  least one day later than the will found at the update ("later_pending",
  or a "sent" will), the migration keeps it. At every Check, right after
  the invalidated items are refreshed (`context.apply_later_wish` in
  `init_class_variables`): if no VALID item is left (the old will was
  already invalidated, for example a paid postpone the wallet had not seen
  yet), the later date is stored through the validated write and used
  (no new fee); if the wallet is synced and VALID items remain, the wish
  is dropped; otherwise it is kept. If the date cannot be stored the Check
  stops (`DeliverySettingsUnavailable`), so nothing is built at the earlier
  date. Every date written by the owner (GUI field, `bal_delivery_set`,
  both answers to the question) drops the wish.
- One-time notes, shown 2 s after the wallet opens and deleted only after
  the message box closes: "converted" (a period became the date),
  "date_differs" and "later_pending" (the old shared value asked for
  another date; the wallet keeps the date of its will) and "outside_change"
  (the will was changed by another program). In the rare "later_pending"
  case with a will prepared but not signed and sent, the "which date?"
  question waits (`choice_pending`): Keep the date of the prepared will (no
  fee, the default) or Use the later date (the old will must be
  invalidated, miner fee).
- Holds (nothing is built, signed, sent or invalidated): while the question
  waits, or when the settings could not be prepared ("fail closed": any
  error in the migration leaves display-only values, never the shared value
  or an unchecked old dict). The Check asks the question or shows the error
  row; Prepare asks the question; wallet close and the automatic rebuild
  skip with a log line; Sign, Broadcast and Invalidate say why they cannot
  run.
- `bal/engine/context.py`: `load_will_settings` / `ensure_will_settings`
  (under the wallet flow lock), `stored_willitems`, `heirs_periods_to_date`,
  `set_will_setting` (the one validated write), `answer_delivery_choice`,
  `check_holds` (first step of `init_class_variables`), `apply_later_wish`,
  `record_will_anchor` (end of `save_willitems`, from the stored will).
  `bal/engine/check.py`: Phase 1 results "delivery_choice" and
  "delivery_settings_unavailable". `bal/engine/host.py` documents the live
  dict and `settings_unavailable`.
- `bal/gui/qt/window.py`: the settings are loaded before the Heirs and Will
  tabs are built (heirs first), and again in `init_will` when that failed
  or the lock was busy. Settings are written through `set_will_setting`;
  from a worker thread only the value is written and the fields are
  refreshed on the GUI thread. The note, the question and the holds above.
  The Will tab Sign and the export save the WHOLE live will through
  `save_willitems` (which records the anchor), never only the signed
  items. `bal/gui/qt/plugin.py`: the note is scheduled at wallet open.
  `bal/gui/qt/widgets.py`: the toolbars read the wallet's own settings; the
  Raw/Date selector is hidden in BOTH modes and the Date editor is always
  used (the Raw code stays until #127); the display caches are window
  attributes; the date editor takes focus only on a click or Tab and
  ignores the mouse wheel (pulled forward from #127: one wheel tick on the
  ADVANCED toolbar stored a new date at once, an earlier will or a paid
  invalidation). `bal/gui/qt/dialogs.py` and `bal/gui/qt/flow_adapter.py`:
  the question in the Check and its rows.
- Command line (`bal/cli/controller.py`, `bal/cli/commands.py`): new
  `bal_delivery_show` and `bal_delivery_set` (`--date YYYY-MM-DD`, `--fee
  N` from 1 to 10000 like the GUI fee box, `--keep true`; a date earlier
  than the will in force needs `--confirm_earlier true`). The will in force
  is the earliest VALID item, else the earliest sent and still pending
  item, so `bal_delivery_show.will_in_force` and the "later than the will
  in force" warning also see a will that is only sent. `bal_settings_get/
  set/reset bal_will_settings` are refused with a pointer to them, and
  `bal_settings_list` no longer shows it. The default settings are no
  longer saved into the global (#102). `bal_will_check`,
  `bal_will_prepare` and `bal_will_autorebuild` answer
  `delivery_choice_pending` / `delivery_settings_unavailable`;
  `bal_will_sign`, `bal_will_broadcast` and `bal_will_invalidate` are
  refused while a hold applies. On a passed Check Alive with a fixed date,
  `bal_will_check` and `bal_will_prepare` now give the same "choose a
  later delivery date" text as `bal_will_autorebuild` (D42: the command
  line no longer advises a paid invalidation that cannot renew a fixed
  date). The #103 message points to `bal_delivery_set --date`.
- `bal/core/plugin_base.py`: comments (the `will_settings` key; the global
  is BAL's key, never written, read raw only as a migration source) and a
  docstring for `BalPlugin.__init__`.
- Texts: 10 new GUI texts (the notes, the question and its two buttons, the
  two hold texts, the "Choose the delivery date" row), with Italian and
  Spanish translations, approved by the owner on 2026-10-11 with these
  changes: the real button name ('Check your will' / 'Controlla
  testamento' / 'Comprobar testamento'), "commissione dei miner",
  "portafogli", "manda il log", and the question says the prepared will
  "is not signed and sent yet". Existing texts reused where they fit
  ("Check your will", "Cancel", "The check stopped: nothing was changed.",
  "Checking variables"). Command-line texts stay English (not extracted).
  Catalogs refreshed with the usual `pybabel extract` / `update`
  (`--no-wrap`), then the existing entries put back in their committed
  order by a scratch script (`reorder_pot.py`, outside the repository): the
  template equals a fresh extraction in entries, locations and flags, but
  not in order; besides the location lines and the creation date the only
  change is the 10 new entries (597 -> 607).
- Docs: `README.md` (the delivery commands, where the settings are kept,
  the first date of each wallet, the hold codes); `docs/manual/README.md`
  and `manual.html` (the Delivery Time is always a date and is kept by each
  wallet; a saved duration becomes a fixed date once; do not use this
  plugin and BAL, or an older version, on the same wallet in turn: the
  other plugin reads the old Delivery Time shared by every wallet, so after
  any date change made here, including the automatic one-day move after new
  funds, it can prepare an EARLIER will and sign it when the wallet is
  closed, or ask for an invalidation, miner fee).
- `make-release.sh`: adds `tests/test_per_wallet_settings_flow.py` and
  `tests/test_gui_window_init_migration.py` to the release test list (the
  `test_core_*` and `test_cli_*` globs cover the other new files).
- `tests/flowkit.py`: test windows load the settings like the real window;
  `Session` seeds the wallet's own settings (options `legacy_global=` and
  `legacy_wallet=`, or a period as locktime, take the migration path); the
  Recorder checks at every step that the global is unchanged; a
  second-wallet helper; the test clock also covers the command line.
  Scenario harness (outside the repository): its window loads the settings
  the same way, the heirs are written before it opens, and the travelling
  clock also moves the engine.
- Not changed: the global value itself (the owner's stays "1054d", for
  BAL); the will-executor protocol; the wallet keys; the Check Alive field
  of advanced mode (until #128).
- Behaviour changes to know: `set_will_setting` refuses values the old
  code stored (a block-height delivery date written by the #107 sync makes
  the command-line prepare fail; a Check Alive outside 1..3650 days is only
  logged); `check_alive_on_open` is skipped while a hold applies; a wallet
  whose only will is "sent" (no VALID item) and whose old shared value is
  an absolute date at least one day later applies that later date at the
  first Check: if the sent will is still executable the Check asks for its
  invalidation (miner fee, Cancel costs nothing), as on `main`;
  `bal_delivery_set` on such a wallet with a later `--date` now gives the
  invalidation warning.
- Known, for later steps: the #79 renewal cannot be reached until #126
  brings back the period form (no public release in the middle of the
  series); the Date editor still shows local time (in Rome a 00:00 UTC date
  shows 02:00) until #127; the help texts of the delivery field and of User
  Type still mention the Raw/Date selector (#127); `bal/wallet_util`
  (`fix`, `uninstall`) drops Electrum 4.8's appended changes, including
  `will_settings` (documented in TECHNICAL-NOTES, a step of its own).
  Reinstalling a ZIP older than #123 reads the frozen global again: never
  do it on a migrated wallet. Pre-existing (also on `main` with one
  plugin): when another program leaves unsigned VALID drafts EARLIER than
  the stored date, the date stays and no note is written, but the next
  Check still rebuilds at the draft's date (`Will.check_anticipate` and
  `set_anticipate` keep the earlier locktime; the #107 sync then stores
  it): the row "The delivery date of heir ... changed: preparing a new
  will", then "Sign your will". The "which date?" question is also asked
  for a will that is signed but not sent. The `--fee` cap applies to
  `bal_delivery_set` only (a migrated legacy fee above 10000 is kept; the
  GUI box shows at most 10000).

**Review:** a multi-agent review of the whole change (5 reviewers: funds,
migration, GUI, command line, quality; skeptics on every finding): 12
confirmed, 0 refuted, 4 low unverified. Fixed here, each with a test that
fails without the fix (mutation runs: 11 of 11 and 14 of 14 caught):

- FS-1 (high): the Will tab Sign wrote only the signed items and recorded
  the anchor from memory, so the stored will kept stale VALID flags and
  the next load moved the date back to a will already paid to invalidate.
  Now Sign and the export save the whole will, and the anchor is always
  taken from the stored will.
- FS-1 (high, other plugin): BAL or an older ZIP could leave unsigned
  earlier drafts that the anchor presented as the will in force. Now only
  a signed outside will is followed.
- FS-2 (medium): the migration took the date of a will whose paid postpone
  invalidation the wallet had not registered yet, so the next Check
  re-signed at the earlier date. Now the later wish is kept and applied
  once no VALID item is left.
- GUI-1 (medium): the mouse-wheel guard on the date editor (pulled forward
  from #127).
- RQ-1 / RQ-2: the hold texts name the real 'Check your will' button
  (`tests/test_will_terms.py`), and the question says "not signed and sent
  yet". RQ-3: the "shared setting" wording is kept, as approved by the
  owner. RQ-4: catalogs refreshed on the final tree. RQ-5 / RQ-6:
  TECHNICAL-NOTES, the `plugin_base.py` comment and the `set_will_setting`
  docstring describe floats, periods and the global correctly. RQ-7: a
  test of the outside-anticipation note. RQ-8: this entry.
- Low: CLI-01 (`bal_will_check` / `bal_will_prepare` on a passed Check
  Alive with a fixed date), CLI-02 (the will in force includes a will that
  is only sent), CLI-03 (fee rate 1 to 10000), multiverse (only the
  informative note).

**Tests:** whole `tests/` directory: 1650 tests.

- `tests/test_core_delivery_settings.py` (new, 186): every source of the
  migration (will / sent / old global period and date / old wallet value /
  default), the "heirs or will" gate, the fee rules (legacy `tx_fees`
  items, 0 ignored, 13.001 kept), `is_current` (type, range, period, float,
  1970), every note, the anchor (unchanged / own save / outside postpone /
  outside anticipation / no VALID item / unsigned drafts not followed /
  a signed outside will followed, not its later draft), the later wish
  (stored, kept by the note, apply / drop / keep), multiverse (no pending
  postpone), the will in force with only sent items, `parse_cli_date`
  refusals, the `add_months` examples, a second load writes nothing, no
  clock reads and standard library only.
- `tests/test_core_delivery_check_alive.py` (new, 30): the Check Alive
  conversion of the migration, checked case by case against
  `checkalive.normalize_check_alive`; the order of the old values and the
  "heirs or will" gate; flowkit runs of an ADVANCED first build after the
  migration and of a "30d" Check Alive counted from the creation of the
  will.
- `tests/test_per_wallet_settings_flow.py` (new, 46, flowkit, three
  will-executors with their own fee addresses, password "flowkit-test"):
  the owner's state (migrated to 29/08/2029, note "converted"; Checks at
  +0, +1, +2 and +7 days, wallet close and command-line check: nothing
  asked, signed, pushed or sent; basic and advanced); the outside postpone
  followed; the owner's wallet back after an outside anticipation (note
  with the "To use the later date again" suffix, then quiet Checks);
  unsigned earlier drafts of another program not followed; the "which
  date?" question (every flow held until answered, Keep signs, Use later
  asks for the invalidation, Prepare asks first); a sent will earlier than
  the will in force still read as a postpone; no will in force: the
  earliest sent will; the Will tab Sign after a manual postpone keeps the
  later date (reopened from the file, after a crash, automatic rebuild on
  a wallet without password); the export saves the whole will; the anchor
  taken from the stored will; the update after an unseen paid postpone
  builds at the later date, and after a cancelled postpone keeps the will
  and drops the wish; a date chosen by the owner replaces the wish;
  multiverse; legacy `tx_fees` items on both paths; fail closed and
  recovery in `init_will`; two wallets and a wallet created after the
  update; no will with a shared period; BAL 0.6.1 "1y"; a BAL 0.2.x wallet
  dict; a second load writes nothing; the validated write; GUI and command
  line share one dict; the migration under the flow lock; the anchor after
  every save; no plugin code uses the global settings object; the note
  kept when showing it fails.
- `tests/test_gui_window_init_migration.py` (new, 8): the real
  `BalWindow.__init__` with a stub Electrum window, basic and advanced: the
  settings are migrated before the toolbars are built, which show
  29/08/2029; the note shown once after the window and kept when showing
  fails; a write from a worker thread touches no widget; a busy flow lock
  at opening, then `init_will`; a mouse-wheel tick (up and down) on the
  ADVANCED Heirs and Will toolbar dates stores nothing.
- `tests/test_cli_delivery_commands.py` (new, 40): `bal_delivery_show` /
  `bal_delivery_set` (date, fee, keep, confirm_earlier, refused values
  write nothing, fee rates up to 10000 stored and above refused, a new
  date answers the question, a sent will with no VALID item is the will in
  force), the settings unavailable, `bal_settings_*` on
  `bal_will_settings` refused with the pointer, the commands registered
  and parsed, the scenario clock, the #103 pointer, the window sees a
  change made by a command, commands on one wallet run one at a time.
- Rewritten (they read the global key, stored a period, used the Raw
  editor, or did not know the new rows): `test_5e_char_check.py`,
  `test_5e_char_copies.py`, `test_5e_divergences.py`,
  `test_auto_rebuild_on_new_tx.py`, `test_cli_autorebuild.py`,
  `test_cli_command_layer.py`, `test_cli_commands_registered.py`,
  `test_cli_controller_offline.py`, `test_cli_off_loop.py`,
  `test_core_identical_rebuild.py`, `test_delivery_field_keeps_value.py`,
  `test_engine_build.py`, `test_engine_context.py`,
  `test_gui_check_variables_rows.py`, `test_gui_date_form_change_flow.py`,
  `test_gui_delivery_field_refresh.py`, `test_gui_flow_adapter.py`,
  `test_gui_will_flows.py`, `test_import_will_details.py`;
  `test_will_terms.py` gets the check of the two hints that name 'Check
  your will'. Changes to know: the #122 GUI flows now run with multiverse
  ON, with the period written into the heirs (+2 new #123 tests);
  `test_core_identical_rebuild.py` writes the period into the heirs;
  `test_renewal_sent_but_not_seen_yet` is replaced by
  `test_passed_check_alive_after_the_update_invalidates_nothing` (the
  real-flow renewal comes back with #126); D42 goes from "pinned" to
  "partly" (its test now pins that `bal_will_check` and `bal_will_prepare`
  give the `bal_will_autorebuild` text; the period renewal is not
  reproduced); the two #81 explicit Raw<->Date switch tests are deleted.
- Golden files: the 5 period-renewal files changed, as the design expected
  (`check/check_alive_renew`, `check/check_alive_not_now`,
  `check/check_alive_renew_approved`, `copies/prepare_check_alive_renew`,
  `copies/auto_check_alive_passed`): their "60d" setting is now the fixed
  date of their will, so a passed Check Alive says "Your delivery is a
  fixed date: choose a later delivery date to renew your will" and nothing
  is invalidated. They change again in #126. One more file changed by the
  CLI-01 fix: `copies/cli_check_alive_fixed_date` (2 lines: both raised
  texts are now the "choose a later delivery date" text). Guard: golden
  files HEAD 93, now 93; only these 6 differ (JSON-aware); regeneration
  byte-identical.

**Verification:** whole `tests/` directory, normal order and files in
reverse order: 1650 passed each time, 0 failed, 0 errors, 0 skipped.
`ruff check bal/ tests/`: all checks passed. Release test list
(`make-release.sh`): 912 passed. Scenario harness: log identical to the
#122 log after masking random txids (192 lines; the 39 matrix scenarios
with the same verdicts, B2 and B2adv still DANGER as known; 2-coin scenarios and
the two bug-3 probes 6 runs); warn_matrix 134 Checks, 0 warnings, 0
mismatches (synced=1 and synced=0). `build_zip.py` (63 entries) +
`external_zip_test.py` + `smoke_test.py` on Electrum 4.8.2: pass. Probes
from the old tree to the new one (owner basic/advanced, edge, roundtrip,
multi, nowill, legacy061 basic/advanced; 8 runs, rc=0, no traceback): the
owner's wallet quiet at +0, +1, +2 (also `bal_will_check`) and +7 days and
at wallet close; output identical to the implementation run except the
approved question text. Review probes on the final tree: the FS-1 Sign
case keeps 2033-09-23 (also after reopening the file and on the
automatic rebuild), the FS-2 update after an unseen paid postpone pushes
the will at 2033-09-23, as on `main`; with 2904b16 standing in for BAL
("later, basic, Cancel") the stored 2030-08-29 is kept with no note (the
pre-existing rebuild at the draft's date above follows). Live Electrum
4.8.2 (offscreen, no network, testnet test wallet built by `2904b16` with
the old "1054d", fee 13 and a VALID will at three will-executors; ZIP
sha256
`4745a0517381677be00d96dac9267c9b6c0f1eb213719fc0b60ba57599abdc70`,
manifest 0.7.0): first open migrates (log "will settings migrated: from
will, delivery 2029-08-29, fee 13"), shows "Your delivery date is now
fixed: 29/08/2029." (Italian: "La tua data di consegna ora è fissa:
29/08/2029."), both toolbars 29/08/2029, the global `bal_will_settings`
byte-identical in the config file, will items unchanged, quiet close
(checking ok, preparation, signature and sending not needed,
verification 3/3); second open: no note, settings unchanged; Electrum's
clock moved +1 and +6 days: Check and close quiet; no traceback, no error
line. New texts approved by the owner on 2026-10-11 (English, Italian,
Spanish).

**Outcome:** DONE. The owner tested ZIP-1 (sha256 `4745a051...`) on his
wallet (basic mode, four will-executors). At the first open the note said
"La tua data di consegna ora è fissa: 28/08/2029." (not 29/08: the plugin
log shows that on 2026-10-11 at 13:54 UTC, before ZIP-1, a Check rebuilt the
will for the fee rate 20 -> 21, one day earlier, signed and sent it to the
four will-executors; the migration then logged "will settings migrated:
from will, delivery 2029-08-28, fee 21", the will really in force). Will
tab: the will in force, 2029-08-28, 4 of 4 received and verified. "Check
your will" on 2026-10-11 and 2026-10-12 (after 02:00 UTC): Check "ok",
preparation, signature and sending "not needed", verification 4/4, no
password, no question; closing and reopening: no note again.

## 124. The delivery date shows how much time is left (green, yellow, red)

**Date:** 2026-10-12

**Problem:** the owner had no quick way to see when the delivery date of
his will was getting close. Owner decision 5 (2026-10-11, series #123-#129):
green with more than 12 months left, yellow below 12, red below 6; the two
thresholds editable in the ADVANCED settings and NOT tied to the Check
Alive. Plan: `context/reports/design-123/plan.md` section 5.

**What changed (visible: colours and two ADVANCED settings rows; nothing in
the will, its transactions or the will-executors changes):**

- `bal/core/delivery.py`: `months_between` (whole calendar months, the
  same arithmetic as `add_months`), `warning_months` (the two thresholds:
  whole months 1..120, red below yellow, else the defaults 12 and 6) and
  `delivery_level` (`green` / `yellow` / `red` / `passed`, counted from
  today's UTC day). Pure, no clock read.
- `bal/core/plugin_base.py`: settings `bal_warn_yellow_months` (12) and
  `bal_warn_red_months` (6); `BalPlugin.warning_months()` returns the
  defaults in BASIC mode and the stored values in ADVANCED mode.
- `bal/core/summary.py`: the "Delivery to the heirs" line carries its
  colour and a tooltip (`delivery_warning`, texts approved by the owner on
  2026-10-12); `_months_between` replaced by `delivery.months_between`, so
  "(in 2 years and 10 months)" and the colour never disagree (the old one
  counted one month less on clamped month ends, e.g. 31/01 -> 28/02).
- Shown only on the line "Delivery to the heirs": in the will summary box
  (`WillSummaryWidget`, date and text coloured, tooltip) and in the result
  sentence of the Check window (`BalBuildWillDialog._summary_sentence`).
  The date field of the toolbars and of the wizard keeps its normal look
  (owner, after the first ZIP-2: a first version coloured it too).
- Settings (`bal/gui/qt/plugin.py`): two ADVANCED rows right below "User
  Type", "Yellow warning: inheritance date less than (months)" and "Red
  warning: inheritance date less than (months)" (names chosen by the owner),
  without keyboard tracking (typing "36" stores 36 once), each box bounding
  the other (red always below yellow); each row's reset button and the
  "Reset to Default Setting" button give back 12 and 6 (red first). The
  rows below were renumbered by two.
- Command line: `bal_settings_set` refuses a threshold outside 1..120
  months or a red not below yellow.
- Catalogs: 7 new msgids (3 tooltips, 2 labels, 2 help texts) with Italian
  and Spanish; the committed order of the other entries kept.
- `tests/flowkit.py`: the Recorder leaves the colour span out of the
  recorded result sentence (a style, not a text), so the golden files are
  unchanged.
- Manual (`docs/manual/README.md`, `manual.html`): a paragraph on the
  colours. `make-release.sh`: the new GUI test file.

**Tests:** `tests/test_core_delivery_colours.py` (pure: months between
dates, also against `add_months` on many dates; the thresholds; the
boundaries, the clamped end of month, passed and today; the tooltips) and
`tests/test_gui_delivery_colours.py` (three fake will-executors, test
wallet with a password: summary box and Check sentence green / yellow /
red; BASIC ignores the stored thresholds, ADVANCED follows them, a broken
pair gives the defaults; the toolbar date of the real window keeps its
normal look; the Settings rows hidden in BASIC and visible in ADVANCED
right below User Type, no keyboard tracking, red kept below yellow, both
reset buttons; `bal_settings_set` limits).

**Verification:** whole `tests/` directory, normal order and files in
reverse order: 1688 passed, 0 failed, 0 errors; the 93 golden files
unchanged; `ruff check bal/ tests/`: all checks passed. Scenario harness: log identical to the #123 log after masking the random ids; warn_matrix 134 Checks, 0 mismatches (synced=1 and 0).
`build_zip.py` + `external_zip_test.py` + `smoke_test.py` on Electrum 4.8.2: pass (ZIP sha256 `dfe53327...`).

**Outcome:** DONE. The owner tested ZIP-2 on his wallet (2026-10-12). A
first version also coloured the date field of the toolbar; the owner asked
for the colour only on the "Delivery to the heirs" line and for the names
of the two settings ("Avviso Giallo/Rosso data eredità inferiore a
(mesi)"). With the second ZIP-2 (sha256 `dfe53327...`): the line red with
the will 4 months away (the owner had moved his will to 2027-02-28 on
purpose to see the colour), the date field in its normal colour, the two
settings right below "User Type" with their new names and reset buttons.

## 125. A new delivery date is reported for the will, not for one heir

**Date:** 2026-10-12

**Problem (owner's ZIP-2 test of #124):** after changing the delivery date,
the Check said 'The delivery date of heir "1" changed: preparing a new
will.' The owner has two heirs and asked why only one. Every heir has the
will's date (multiverse off: `init_heirs_to_locktime` gives them the stored
date), so the will's date changed; the core named the first heir it found
(`Will.check_willexecutors_and_heirs`, CHANGELOG #77).

**What changed (visible: one sentence; nothing else):**

- `bal/core/will.py`: `HeirNotFoundException` gets `all_heirs` (default
  False). `check_willexecutors_and_heirs` sets it for a date change when
  every heir has the same delivery date.
- `bal/core/messages.py` `will_change_message`: with `all_heirs`, "The
  delivery date changed: preparing a new will." (text approved by the
  owner; Italian "La data di consegna è cambiata: preparo un nuovo
  testamento.", Spanish too). Heirs with different dates (the hidden
  multiverse option) keep the sentence with the heir's name. The owner's
  model for the future instalment inheritance (same dates for every heir,
  one share per date) is noted in `PLAN.md`; the multiverse sentences will
  be redone with it.
- Catalogs: 1 new msgid with Italian and Spanish.
- `make-release.sh`: the new test file.

**Tests:** `tests/test_gui_delivery_date_message.py` (new; three fake
will-executors, test wallet with a password): two heirs as the owner's, an
earlier date, basic and advanced: the Check says the generic sentence and
names no heir (both fail on `main` `12f7ae5`); multiverse with different
dates: the heir is still named (passes on `main` too: unchanged).
`tests/test_msg_rebuild_reasons.py`: the sentence for `all_heirs`.
`tests/test_gui_date_form_change_flow.py`: Prepare's popup now says the
generic sentence. Golden file `check/date_earlier`: the same sentence, the
only change (3 lines).

**Verification:** whole `tests/` directory, normal order and files in
reverse order: 1691 passed, 0 failed, 0 errors; `ruff check bal/ tests/`:
all checks passed. Scenario harness: log identical to the #124 log after masking the random ids; warn_matrix 134 Checks, 0 mismatches (synced=1 and 0). `build_zip.py` + `external_zip_test.py` + `smoke_test.py` on Electrum 4.8.2: pass (ZIP sha256 `1d4f90f0...`).

**Outcome:** DONE. The owner tried the ZIP on his wallet (2026-10-12): a
date one day earlier, then "Check your will": under "What changed" the
window said "La data di consegna è cambiata: preparo un nuovo testamento."
After this step the owner set a lighter rule for text-only steps (no
scenario harness, no owner ZIP test; CLAUDE.md section 3).

## 126. The calendar button of the toolbar works in BASIC mode

**Date:** 2026-10-12

**Problem (found while designing #123, plan section 6):** in BASIC mode the
calendar button of the Will and Heirs toolbars saved no .ics file; the log
said "failed to generate .ics". The toolbar passes the delivery date with
its time zone (`BalTimestamp._safe_fromtimestamp`, UTC-aware) and no "now";
`bal/core/reminders.py` `build_ics_reminders` took a naive
`datetime.now()`, so `locktime - now` raised "can't subtract offset-naive
and offset-aware datetimes". ADVANCED mode subtracts two aware dates and
worked.

**What changed:** `build_ics_reminders` takes "now" in the time zone of the
delivery date (`datetime.now(tz=locktime.tzinfo)`; naive when the delivery
date is naive). Nothing else: the reminder dates are those of #73 (BASIC:
30, 10 and 1 day before the delivery; ADVANCED unchanged).
`make-release.sh`: the new test file.

**Tests:** `tests/test_core_reminders.py`: BASIC with an aware delivery
date and no "now" (failed with the TypeError before the fix), and with a
naive one. `tests/test_gui_toolbar_calendar.py` (new; three fake
will-executors, test wallet with a password): the toolbars of the real
window build the .ics content in BASIC (three reminders, the first 30 days
before 27/02/2027; failed on `45101df`) and ADVANCED mode.

**Verification:** whole `tests/` directory, normal order and files in
reverse order: 1695 passed, 0 failed, 0 errors; the golden files unchanged;
`ruff check bal/ tests/`: all checks passed. No scenario harness (the
harness never uses the calendar). `build_zip.py` + `external_zip_test.py`
+ `smoke_test.py` on Electrum 4.8.2: pass (ZIP sha256 `a849706c...`).

**Outcome:** DONE. The owner tested the ZIP on his wallet (BASIC mode,
2026-10-12): the calendar button saved the .ics file, and his calendar
shows the three reminders on 28/01/2027, 17/02/2027 and 26/02/2027 for the
delivery of 27/02/2027 (23:00 Rome, the hour of his will; the UTC date
editor of a later step gives 00:00 UTC dates).

## 127. A will can be delivered after a renewable period of months

**Date:** 2026-10-12

**Problem (design `context/reports/design-123/plan.md`, section 7, there
numbered #126):** since #123 every delivery date is a fixed date, so the
Check Alive renewal of #79 ("Renew": invalidate the old will, prepare a new
one at today + the period) could no longer be reached: the five renewal
golden files of Phase 5e showed only "Your delivery is a fixed date". BASIC
mode ignored the Check Alive entirely, and a new wallet started with a
delivery date 12 months away.

**What changed (owner decisions of 2026-10-12):**

- Delivery form per wallet, schema 2 of `will_settings`
  (`bal/core/delivery.py`): `form` "fixed" (default; missing means fixed)
  or "period", `period_months` (1..240 calendar months), `period_start`.
  `locktime` stays the absolute date in both forms. A version-1 dict is
  upgraded once to `form` "fixed" and `settings_version` 2
  (`delivery.upgrade_values`, written item by item in
  `context.ensure_will_settings`); the #123 migration writes version 2.
  New wallet / nothing to migrate: today + 24 months (was 12; the #124
  warning thresholds stay 12/6).
- The form is locked while a will is in force (`context.form_locked`): a
  VALID item signed or sent, or a sent item (INVALIDATED included) whose
  coins are not spent by a CONFIRMED transaction; on a wallet that is not
  synchronized any sent item not confirmed. An unsigned will does not lock.
  `context.set_delivery` is the one validated write of the form
  (`DeliveryFormLocked` while locked); changing N with a will in force only
  stores N for the next renewal.
- `context.apply_delivery_policy`, in `init_class_variables` right after
  `refresh_invalidated_items` (before `apply_later_wish`): with the period
  form and no will in force, `period_start` = today and the delivery date =
  today + N months, through `host.store_will_setting`.
- Renewal (`checkalive.check_alive_renewal`): only the period form renews;
  the new delivery is today + N months and the new Check Alive is counted
  back from that NEW date (counted from the old stored date it was always
  "still past"). The texts show the period with `messages.months_text`
  ("36 months (3 years)", existing msgids only).
- BASIC mode (`checkalive.check_alive_basic`): the Check Alive is inert
  only for a fixed date; a period-form will keeps renewing in BASIC (Check,
  summary, question at wallet open).
- Wizard settings page (`widgets.DeliveryFormWidget`): "Plugin mode
  (applies to every wallet)" (the same `USER_TYPE` and the same "at My
  Risk" gate as the plugin Settings dialog, one shared function
  `common.switch_user_type`), "Fixed date" / "Period (renewable)" (period
  only in ADVANCED, both enabled only when not locked), a month box 1..240
  without keyboard tracking, the lock sentence. BASIC in the wizard sets an
  unlocked period back to fixed. After the owner's first ZIP test
  (2026-10-12): BASIC shows as little text as possible, so with a fixed
  date it shows neither radio nor the lock sentence (only the mode
  selector and the date); the lock sentence is shown only in ADVANCED.
- Wizard layout (`dialogs.BalWizardDialog`): after "Next", and again after
  choosing ADVANCED in the page's mode selector, the dialog kept its size,
  so the taller page was squeezed (the "BASIC" box 7 to 14 pixels high,
  rows overlapping) until the window was moved. `next_widget` now shows the
  new page at once, and after every new layout of its page
  (`LayoutRequest`) the dialog grows to its size hint (never shrinks).
- The toolbar button of the wizard reads "Build / Edit Your Will" (IT
  "Crea / Modifica il tuo testamento", ES "Crear / Modificar tu testamento";
  owner, 2026-10-12): the same wizard changes a will already made. The will
  summary, shown when there is no will yet, keeps "Build Your Will".
- Will and Heirs toolbars: the period form shows "36 months (3 years) ->
  12/10/2029" instead of the date editor, following every change of form,
  period or date in both directions.
- Command line (English): `bal_delivery_show` adds `form`,
  `period_months`, `period`, `period_start`, `locked`; `bal_delivery_set
  --months N` (period form only; `--date` refused there); new
  `bal_delivery_form fixed|period --date/--months`, refused while locked
  with the steps to follow.
- Multiverse (hidden option): no period form; otherwise unchanged.
- Catalogs: 5 new msgids with IT/ES ("Fixed date", "Period (renewable)",
  "Plugin mode (applies to every wallet)", the lock sentence,
  "Build / Edit Your Will"). Docs: `docs/manual/README.md` and `manual.html`
  (delivery form, 24 months, wizard mode selector, BASIC shows no choice,
  the button name), `README.md` (CLI). `make-release.sh`: the new
  GUI/flow test files.

**Tests:** new `tests/test_core_delivery_months.py` (month cases of the
design, `months_text`, form helpers, v2 upgrade, the two predicates, the
renewal rule, purity), `tests/test_delivery_form_lock.py` (lock on a real
wallet: unsigned will, signed will, mempool invalidation still locks,
SPV-confirmed one unlocks, unsynchronized wallet; `set_delivery` refusals;
v2 upgrade idempotent; plain-dict hosts untouched),
`tests/test_delivery_period_flow.py` (first period will; renewal really
offered: phase 1 ends "check_alive_renew" with form period, new will at
today + 2 months, Ok the next day; Not now; BASIC keeps renewing; fixed
date not renewed; new N used at the next renewal; manual Invalidate then
the policy; new coin anticipates one day and the stored date follows;
default today + 24 months), `tests/test_gui_delivery_form.py` (period
hidden in BASIC, mode selector and Settings dialog through the same gate,
lock sentence, BASIC resets an unlocked period, BASIC with a will in force
shows only the mode, the wizard grows to fit the settings page and again
when ADVANCED is chosen there, multiverse,
toolbars both ways), `tests/test_cli_delivery_form.py`. Updated: the renewal tests of
#79/#120 now use the period form (`test_check_alive_renew.py`,
`test_engine_check_variables.py`, `test_gui_check_variables_rows.py`, which
also gets back the pre-#123 "renewal sent but not seen yet" test,
`test_will_summary.py`), D42 of `test_5e_divergences.py` back to "Prepare
opens the Check", schema 2 / 24 months in the #123 tests,
`test_cli_commands_registered.py`. Mutation checks (each applied alone):
the old-date renewal rule, BASIC ignoring the period, the lock released by
a mempool invalidation, 12 months, toolbars without the period, no v2
upgrade, no lock in `set_delivery`, the gate bypassed, the period radio
in BASIC, the radios or the lock sentence shown in BASIC, and the wizard
not resized (the new page not shown first, or no growth after a new
layout) each make tests fail. `test_check_button.py`: the new button name.

**Golden files:** only the five renewal files, seeded now with the period
form of 2 months instead of the shared "60d":
`check/check_alive_renew`, `check/check_alive_not_now`,
`check/check_alive_renew_approved`, `copies/prepare_check_alive_renew`,
`copies/auto_check_alive_passed`. They show the renewal again: compared
with their version before #123 the only differences are the dates one day
later (2026-12-10 / 2027-01-19 instead of 12-09 / 01-18), "(in 2 months)"
instead of "(in 59 days)", "(today + 2 months)" instead of "(today + 60
days)" and the settings block (`form`, `period_months`, an absolute date
instead of "60d").

**Verification:** whole `tests/` directory, normal order and files in
reverse order: 1798 passed, 0 failed, 0 errors (both); `ruff check bal/
tests/`: all checks passed; no CRLF in the touched files. Scenario harness
(full matrix, 2-coin scenarios 6 times, BUG-3 probes, warn_matrix synced 1
and 0) on Electrum 4.8.2: identical to the #125 log (ids masked), same
verdicts. Real regtest renewal (`run_real.sh renew`, period of 7 months,
two runs): the question is shown, the old txs are dead per the node (2/2),
the new will is dated today + 7 months (2027-06-21, stored date and
`period_start` 2026-11-21 agree), non-final before, accepted after and
executed (alice 597200, bob 398133), the old txs then `missing-inputs`.
Catalogs: `check_catalogs.py` RESULT: OK, 5 new msgids, 0 fuzzy, 0 empty.

**Outcome:** DONE. The owner tested the ZIP on his wallet (2026-10-12):
BASIC shows only the mode, the date and the fee; ADVANCED (after "at My
Risk") shows the two forms, disabled, and the lock sentence, and the
wizard grows without squeezed rows; the button reads "Crea / Modifica il
tuo testamento". Still open, for later steps: the fee field in BASIC (to
hide in the new BASIC wizard, owner choice A), and P2 seen again on his
wallet: a later date asked on an unsigned draft goes back to the draft's
date at the Check (his signed will of 27/02/2027 stays in force).

## 128. Unsigned drafts no longer move the delivery date

**Date:** 2026-10-12

**Problem (known issue "P2", the owner's wallet, 2026-10-12):** a will
signed and sent to the will-executors for 2027-02-27 22:00 UTC, fee 21. The
fee became 20: the wallet-close Check prepared a new will one day earlier
(02-26) and the owner closed the password window. The fee went back to 21:
the next Check prepared another will one day earlier again (02-25), because
`Will.update_will` dated every rebuild against all the VALID items, so the
UNSIGNED draft of 02-26 was the will to beat (`WillItem.set_anticipate`
takes the minimum), and `search_rai` had marked the sent will REPLACED. The
date set back to 27/02 and a Check: still 02-25, and the password asked
again, while the will-executors held the will of 02-27 all along. The
scenario harness showed the same as B2 / B2adv ("DANGER: delivery EARLIER
than intended").

**What changed (owner decisions of 2026-10-12):**

- One derived classification of the will items, nothing new stored in the
  wallet (`bal/core/will_state.py`, pure): a **draft** is VALID, never
  signed and never sent; a **partially signed** item is its own state,
  never dropped; the **will in force** is made of the items signed and/or
  sent, VALID or REPLACED, not invalidated or executed and still executable
  (on a wallet not synchronized: counted, the safe side); then replaced,
  invalidated, executed. `delivery.ItemView` reads the same rules.
- Every build (Check, wallet close, Prepare, automatic rebuild, command
  line), in `Will.update_will`: first the drafts are dropped (log "dropped
  N unsigned drafts"), then the new will is dated only against the will in
  force (and the partially signed items, which a co-signer may still
  send).
- The will in force comes back: a rebuilt transaction identical to a
  REPLACED item (same txid) gives that item back, VALID again, with its
  signatures and its sent / verified state, only when it is signed and
  sent, still executable, no other signed or sent item on the same coins is
  dated earlier, and the wallet is synchronized. Otherwise the fresh item
  takes its place as before, but never as an unsigned copy of a signed
  transaction: it keeps the signed transaction (no password) and is sent
  again.
- Texts: when the build only gave back the will in force, the Check
  dialog's rows end as for a coherent will ("Checking your will: Ok",
  "Building your will: Nothing to do"; the result says "Your will is in
  force" with the date), the Will tab's Prepare no longer shows "... a new
  will must be prepared", and `bal_will_prepare` answers `coherent`. The
  Will tab shows a draft as "Draft" (IT "Bozza", ES "Borrador") instead of
  "To sign (0/1)"; a partially signed item keeps its signature count.
- The period form of #127: a draft is no longer a will in force for the
  period date (it never locked the form either).
- Unchanged: a signed or sent item is never deleted; a later date than a
  sent, still executable will (also a later hour of the same day) still
  needs the paid invalidation; an earlier hour is a new will to sign.
- Known, for later (owner): the fee put back ONLY, with the date left as
  the draft stored it, keeps that date (one day before the will in force);
  telling it apart needs stored state.
- Catalogs: 1 new msgid ("Draft") with IT/ES. Docs:
  `docs/manual/README.md` and `manual.html` (the "Draft" state). The
  technical notes describe the classification and the rules.
  `make-release.sh`: the new flow test file.

**Tests:** new `tests/test_core_will_state.py` (the classification, on
items and on `ItemView`), `tests/test_drafts_do_not_move_date.py` (three
fake will-executors, BASIC and ADVANCED: the owner's case with the password
cancelled at each step and its golden file `check/owner_drafts_128`, fee
then fee back with the date, an earlier date cancelled then back, a draft
never signed then a later date, a wallet not synchronized, the automatic
rebuild with a draft and a new coin (D-1, not D-2), the command line, a
signed earlier will kept and a later date still invalidated, the hour of
the day earlier and later, multiverse, the period form with a draft and
with a will in force, a draft left by another program). In
`tests/test_core_identical_rebuild.py` the test "a replaced item is not
brought back" became the tests of each condition (brought back when all
hold, not brought back when each one fails, drafts dropped, partially
signed and signed items kept). Updated: `test_status_column.py` ("Draft"),
`test_core_delivery_months.py` (a draft is not a will in force for the
period), and four tests that anticipated against an unsigned first will
now sign it in their setup, keeping their intent (#97, #110):
`test_auto_rebuild_on_new_tx.py` (2) and `test_cli_autorebuild.py` (2).
Before the fix the new tests failed for the expected reasons (dates one
day earlier per draft, the password asked again, `update_will` without a
wallet); the hour and postpone tests passed before and after. Mutation
checks (each applied alone): drafts not dropped, dating against the VALID
items, each of the signed / sent / executable / earlier-will /
synchronized conditions, the signed copy not kept, the "Draft" label, the
period rule, the dialog rows, the command-line answer and a partially
signed item taken for a draft each make tests fail. The explicit txid
check is guaranteed by the lookup by txid (no test can fail on it alone).

**Verification:** whole `tests/` directory, normal order and files in
reverse order: 1848 passed, 0 failed, 0 errors (both); `ruff check bal/
tests/`: all checks passed; no CRLF in the touched files. Golden files:
none changed, one new (`check/owner_drafts_128`). Scenario harness (full
matrix, 2-coin scenarios 6 times, BUG-3 probes, warn_matrix synced 1 and
0) on Electrum 4.8.2: identical to the #125 log (ids masked) except B2 and
B2adv, DANGER -> OK in every run (intended 2033-09-23: before 2033-09-22,
the rebuild dated against the unsigned draft; now 2033-09-23). Catalogs:
`check_catalogs.py` RESULT: OK, 1 new msgid, 0 fuzzy, 0 empty.

**Outcome:** DONE. The owner tested the ZIP on his wallet (BASIC mode,
2026-10-12), in the state of his P2 case: the signed will of 2027-02-27
22:00 UTC (fee 21) in force at 4 will-executors and unsigned drafts at
02-26 and 02-25. With the date 27/02/2027 23:00 and fee 21 the Check
asked no password: "Checking your will: Ok", preparing, signing and
sending not needed, verified 4/4, "Your will is in force", delivery
2027-02-27. The log: "dropped 4 unsigned drafts", "will in force brought
back: 4 items"; the wallet still holds its whole history (58 items; the
replaced and invalidated rows were only hidden by his list options). The
owner approved the deviation (an identical signed transaction that cannot
be brought back keeps its signatures and is sent again, no password).
Still open: putting back only the fee, with the date left where a draft
stored it, keeps that date (one day earlier at most).

## 129. The Will tab lists the will by versions

**Date:** 2026-10-12

**Problem:** every build of the will (Check, wallet close, Prepare,
automatic rebuild) makes one transaction per will-executor, and the older
transactions stay in the wallet. The Will tab listed them all, one flat row
each, sorted by locktime: after a few changes (a new coin, a new date, a
fee, a will-executor added or excluded) the owner could no longer tell
which rows were the will in force and which were history (his wallet holds
58 items). The "Hide Replaced" / "Hide Invalidated" options (on by default)
hid part of the history, but also the replaced transactions that #128
counts as still in force. A backup transaction said "None", the Will
tab's search box raised an error, and "delete" was offered on replaced or
invalidated transactions that a will-executor still held.

**What changed (plan approved by the owner, 2026-10-12):**

- New `bal/core/will_versions.py` (pure, no Qt): the will items are grouped
  into VERSIONS at every refresh; nothing new is stored in the wallet
  (owner decision). A version = the root transactions (not spending another
  will transaction) with the same content: date, coins, heirs as set (not
  the will-executor fee outputs) and fee rate; the multiverse instalments
  belong to their root. The build time is not part of the content, so a
  will-executor added later (old transactions kept by #122) stays in one
  version; the same will-executor twice in one content (only its own fee
  changed) splits by build time. Transactions without heirs (a single
  transaction merged from outside) go to "Other transactions".
- Version states, derived with `will_state` (#128), each item classified
  once per refresh: Draft (on top, no number), In force (the version the
  will-executors would deliver first: the earliest date among the signed
  and sent ones), Replaced (still held by the will-executors), Replaced,
  Invalidated, Partially signed, Executed, Discarded draft. Numbers count
  only the versions with a transaction signed (also partly) or sent, the
  oldest is #1. A version never signed nor sent that is not the current
  draft (a draft that a plugin before #128 marked REPLACED; the owner's
  "#17", made after the will in force, read 16, 17, 15) is a discarded
  draft: no number, shown in its place by creation (second ZIP test:
  shown only with "Show all history" it appeared in the middle of the
  rows already shown). Nothing is deleted.
- The paid invalidation gets its own row, between the versions it ended
  and the next one: found in the wallet history (the transaction spending
  the coins of the invalidated versions, with the plugin's label or every
  output back to the wallet), with its miner fee, txid and "On the
  blockchain" / "Mempool". Not found (or any wallet error): no row.
- The Will tab list (`PreviewList`, same name, toolbar and menus) is a
  two-level tree. Its header names the version level over the whole
  width, the same with every version closed or open: "N." (#4), "Version"
  ("Delivery 30/03/2027 00:00" in bold, local time and the toolbar's
  locale format with a four-digit year; below it "alice 60%, bob 40% ·
  Miner fee 21 sat/vB · sent and verified 3 of 3", or "not sent" when no
  will-executor received it) and "Status" (the state in the colours of the
  will summary, #124). Under an open version a grey row names the
  transaction columns, then one row per transaction: Locktime, Txid (in
  full), Will-Executor (the URL and its fee address below it, the row two
  lines high; "No will-executor (backup transaction)" instead of "None"),
  Executor fee (new column), Status (as #83), row colour as before, except
  that a transaction in force (`will_state`) reads where its will-executor
  stands ("In force, verified", green) even when a later unsigned draft
  marked it REPLACED. Order: draft, version in force, then the history
  newest first by creation; the list stops after the last five numbered
  versions (discarded drafts and invalidations among them stay); a push
  button under the list, "▼ Show all history ({} older versions)", adds
  the older rows at the bottom and "▲ Hide history" hides them again
  (second ZIP test: a text row was confused with the versions, and the
  history could not be hidden again). The draft and the version in
  force are open, the others closed; what the owner opens or closes is
  kept across the refreshes (by version, not by row number). Double click
  and the menu work on the transaction rows under a version (details opens
  that transaction); on a version row the double click opens or closes it
  and the menu offers sign and broadcast for its transactions (the
  existing functions), no "details" (it opened one window per
  will-executor). Header sorting off; the search box looks in versions and
  transactions (it raised AttributeError before).
- Delete: never a transaction sent to its will-executor (also when replaced
  or invalidated: deletable before), never a signed backup; only drafts,
  partially signed ones and transactions never sent.
- Settings: "Hide Replaced" and "Hide Invalidated" removed; the config keys
  `bal_hide_replaced` / `bal_hide_invalidated` stay readable, unused. Dead
  code removed: `PreviewList.hide_*`, `WillDetailDialog.toggle_*` (their
  buttons were never created), `BalPlugin.hide_*` / `sync_hide_filters`.
  The Will Details window (`WillWidget`) shows every transaction (it will
  be replaced in a later step).
- After the first preview the owner asked for four fixes (in-force rows
  not "Replaced", "not sent", the four-digit year, taller two-line rows);
  after his ZIP test on his wallet (17 versions) for three more (the
  header of the version level with the sub-header row, the discarded
  drafts, no "details" on a version row). All in.
- Catalogs: 12 new msgids with IT/ES ("Executor fee", "Replaced (still
  held by the will-executors)", "Paid invalidation", "On the blockchain",
  "Delivery", "sent and verified {} of {}", "not sent", "Show all history
  ({} older versions)", "Other transactions", "N.", "Version", "Discarded
  draft"); 8 msgids of the removed options are obsolete. Docs:
  `docs/manual/README.md` and `manual.html` (the Will tab by versions, the
  header and the sub-header, the states, Show all history and the
  discarded drafts, the options removed). The technical notes describe the
  grouping rules and the edge cases. `make-release.sh`: the new GUI test
  file.

**Tests:** new `tests/test_core_will_versions.py` (flowkit, three fake
will-executors with their own fee addresses: a realistic history - sign,
new coin, fee change cancelled at the password, an earlier date, a later
date with the paid invalidation and the rebuild -, two coins, Exclude, a
will-executor added later, the backup transaction, the #128 draft dropped
and the will in force brought back, the owner's discarded draft after the
will in force (unnumbered, hidden until Show all, kept by the next build,
the new will numbered next), multiverse, nothing written; synthetic wills:
the same will-executor twice split by build time with stable ids, other
coins or heirs, items without heirs, versions without time, the earliest
date in force and drafts unnumbered, partially signed / executed /
discarded / signed never sent, a wallet not synchronized, a spender that is
not an invalidation and one without the label, wallet errors, the last
five and Show all with the invalidation row, and 1110 items in about
0.01 s) and `tests/test_gui_will_versions_list.py` (the real list on
flowkit wills: version rows and the invalidation row, the header "N." /
"Version" / "Status" over the whole width and stable when closed or open,
the sub-header row seen only under an open version, the columns of a
transaction row, the backup row, last five and Show all, a discarded
draft only with Show all, open/closed rows kept across update() and a new
build, the current row kept, double click and menu on a transaction row,
the version row's menu without details and its double click opening no
window, the draft deletable and a sent transaction not, the search,
multiverse, the Settings dialog without the two options, the in-force rows
after a later draft, "not sent", the four-digit year in Italian and
English, the two-line row height). Updated: `test_status_column.py` (the
new Executor fee column; a copy in force reads in force with
`in_force=True`, unchanged without it) and `test_gui_will_menu.py` (the
delete rule: a sent replaced or invalidated transaction is no longer
deletable, a replaced backup neither, never-sent ones still are).
Mutation checks: 21, each applied alone (the content key with the build
time, without the coins, without the heirs; no split for the same
will-executor twice; the in-force version by the latest date, and the
oldest on a tie; never-signed versions numbered; no discarded-draft state;
discarded drafts shown without Show all; "still held" for a copy never
sent; the invalidation never / always recognised; the version in force not
shown second; sent items and the in-force backup deletable again; the
open/closed rows not restored or kept by row number; child rows read as
top-level rows; "Show all" not kept; no sub-header row; "details" back on
the version menu): each makes a test fail.

**Verification:** whole `tests/` directory, normal order and files in
reverse order: 1890 passed, 0 failed, 0 errors (both); `ruff check
bal/ tests/`: all checks passed; no CRLF in the touched files. Golden
files: none changed. Catalogs: `check_catalogs.py` RESULT: OK, 12 new
msgids, 0 fuzzy, 0 empty ("N." is "N." in Italian on purpose), 8 removed
msgids now obsolete (one, "Hide Invalidated", used by pybabel as a fuzzy
base and dropped); `test_translations` and `test_i18n` pass. Time: 1110
items (10 will-executors, 3 instalments) grouped in about 0.01 s; the list
redrawn with them in about 0.05 s. Display-only step on the list (the
delete rule aside): no scenario harness.

**Outcome:** DONE. The owner tested three ZIPs on his wallet (Windows,
17 versions, 2026-10-12). After the first: the header N. / Version /
Status with a grey sub-header under an open version (the transaction
columns were squeezed to the left when every version was closed); the
never-signed "#17" became a discarded draft without a number; no
"details" on a version row (it opened one window per will-executor). After
the second: "Hide history", and the discarded drafts in their place (they
appeared in the middle of the list with "Show all history"); the toggle
became a real button. The third ZIP: "ok molto bene". Agreed with the
owner for small follow-up fixes: only the affected tests per ZIP, the
whole suite in both orders once before the commit.

## 130. A simpler toolbar in BASIC mode and a big calendar button

**Date:** 2026-10-12

**Problem (the owner, after the ZIP test of #129):** in BASIC mode the
toolbars of the Will and Heirs tabs still showed the delivery date field
and the fee field, although BASIC changes them only in the wizard; and the
calendar button (the .ics reminders of #73 and #126) was a small icon
between those fields, hard to see next to the two big commands, and also
on the Heirs tab, where it does not belong.

**What changed:**

- `bal/gui/qt/widgets.py` `WillSettingsWidget._arrange_toolbar_rows`
  (toolbars only, `layout_type="h"`): in BASIC the delivery date and the
  fee are hidden (the Check Alive already was); ADVANCED keeps them, on two
  rows (delivery date, then Check Alive | fee). The visibility follows a
  mode change at once, on every refresh. The calendar button is never in
  these toolbars any more, so the Heirs tab has none.
- `bal/gui/qt/lists.py` `PreviewList.create_toolbar`: the Will tab has its
  own calendar button (`_big_calendar_button`), right after "Check your
  will", as tall as the two main commands, with a doubled icon (28 px),
  in BASIC and ADVANCED; the same menu (Open / Open with... / Save) and the
  same reminder dates (`WillSettingsWidget._ics_provider`). The Tools menu
  of the Will tab gets the same menu as "Calendar reminders" (IT
  "Promemoria nel calendario", ES "Recordatorios en el calendario").
- After the owner's ZIP test: the two big buttons stay on the LEFT of
  the toolbar in BASIC too (with every field hidden the row shrank and
  the toolbar's stretch pushed them right; the row now takes the free
  width and has its own stretch), the calendar on the right, next to
  Tools.
- Fix of #129 (owner's ZIP test): the rows of an older version still held
  by the will-executors (#15, "Replaced (still held by the
  will-executors)") read green "In force, verified"; only the rows of THE
  version in force do now (`PreviewList._add_variants`), the others keep
  their own status ("Replaced", in its colour).
- Catalogs: 1 new msgid; the stale `python-brace-format` flag of "Hide
  history" (#129, inherited from a fuzzy match) is gone.

**Tests:** `tests/test_check_button.py`: BASIC hides the date, the fee and
the calendar of the toolbar; ADVANCED keeps date and fee without the
calendar; a mode switch shows and hides them at once; the Will tab's
toolbar is Wizard | Check | Calendar | fields, the calendar button as tall
as the Wizard with the same icon size, and Tools has "Calendar reminders"
with Open / Open with... / Save (the button's own menu); the buttons on
the left and the calendar on the right. The rows of the toolbar fields
updated (no calendar). `tests/test_gui_will_versions_list.py`: only the
version in force has "In force" rows.

**Verification:** while fixing, only the affected tests (toolbar,
calendar, Will tab list, wizard form, window init: 51 passed); before the
commit the whole `tests/` directory once in the normal order (owner rule
for a step that changes only the look, 2026-10-12): 1895 passed, 0
failed, 0 errors; `ruff check bal/ tests/`: all checks passed; golden
files unchanged; `test_translations` / `test_i18n` pass; no scenario
harness (no logic changed). `build_zip.py` + `external_zip_test.py` +
`smoke_test.py` on Electrum 4.8.2: pass.

**Outcome:** DONE. The owner tested two ZIPs on his wallet (2026-10-12):
BASIC toolbar without date and fee, the big calendar button and the Tools
entry; after the first ZIP he asked to keep the two big buttons on the
left and the calendar on the right, and noticed the green "In force" rows
of the older version #15 (fixed here); the second ZIP: ok.

## 131. The Will tab shows the will's future on a timeline

**Date:** 2026-10-12

**Problem (the owner, after #129):** the Will tab lists the will by
versions, a precise history, but it does not say in one look what happens
if the owner does nothing more: when the heirs receive the bitcoin, how
much time is left, whether the will-executors hold the will and who gets
what. Tools -> Display (the Will Details window) shows technical details
only.

**What changed:**

- `bal/core/future.py` (new, no Qt): `build_future` builds the data of
  the view from the will items, the wallet's delivery settings and the
  clock. It describes THE will in force (`will_versions.build_history`,
  the in-force version with the earliest delivery, #128/#129), not the
  VALID items; the heirs with their REAL shares, read from the outputs of
  the will's transaction (the shares entered can sum to more or less than
  100 %: 60 % + 50 % gives 54.55 % / 45.45 %); the delivery date, the time
  left ("4 months and 17 days") and its #124 colour; the Check Alive (none
  in BASIC with a fixed date); the renewal period of the period form
  (#127); each will-executor and where it stands. A list of instalments,
  so multiverse (hidden option) does not break it: every instalment on
  the time line, and as lines under the sentence. Display only: nothing is
  written.
- `bal/gui/qt/future_view.py` (new): the selector "List | Future" (IT
  "Elenco" / "Futuro", ES "Lista" / "Futuro") under the will summary; the
  last choice is remembered in Electrum's config (`bal_will_view`,
  plugin-wide like the other view settings, never in a wallet). The Future
  view draws a ruler of the years on top (1 January of each year at its
  true place; a label every 1, 2, 5 or 10 years so labels never overlap,
  owner's idea after the first preview), today, the Check Alive flag, the
  delivery in its colour, the later instalments on the same scale, the
  will-executors as small squares (green received and verified, grey not
  yet, red not received; they wrap on several rows, tested with 30), a
  count line, a colour legend, one line that says what a will-executor is
  (IT "I will-executor conservano una copia del tuo testamento e la
  trasmettono alla data di consegna.") and the sentence "If the will is no
  longer checked or renewed, on {} the bitcoin will go to: {}" (IT "Se il
  testamento non viene più controllato né rinnovato, il {} i bitcoin
  andranno a: {}", ES "Si el testamento ya no se comprueba ni se renueva,
  el {} los bitcoin irán a: {}"). Tooltips: each square its URL and state,
  the sentence the amounts. Every date is the UTC day and time
  ("27/08/2027 22:00 UTC", owner's choice after the first preview: the
  local time read one day after the summary box for a delivery at 22:00
  UTC). Only a draft: "No will in force" with "Will
  prepared: it must be signed and sent"; a draft over the will in force:
  the summary's "Until you sign and send it, the will sent before stays in
  force"; no will: "No will in force". The drawing (`paint_future`) takes
  a plain scene and any `QPainter`, so the PDF report (#132) can reuse it.
  It follows the refreshes of the summary and is recomputed only while
  shown; an error is logged and only today is drawn.
- `bal/gui/qt/window.py`: `create_will_tab` adds the selector and the view;
  `update_will_summary` refreshes the view. `bal/core/plugin_base.py`:
  `WILL_VIEW`.
- `bal/gui/qt/dialogs.py` `WillDetailDialog` (Tools -> Display) stays,
  with a note at the top for the live will only: "Will plan view
  (technical details). The new graphic view is 'Future' in the Will tab."
  (IT "Vista del piano ereditario (dettagli tecnici). La nuova vista
  grafica è 'Futuro' nella scheda Testamento."); not for an imported will
  (shown after a Merge).
- Catalogs: 5 new msgids (EN/IT/ES); every other text reuses existing
  msgids (summary, report, #127/#128/#129).
- Manual (`docs/manual/README.md`, `manual.html`): "WILL tab: the Future
  view"; `make-release.sh`: the two new test files.
- The Will tab list (#129): every version now starts closed, the draft
  and the version in force too (owner: the transaction rows with txids
  and addresses only when the user opens a version, by double click or
  the arrow; too much data at first sight frightens a user). What the
  user opens stays open while the window lives.
- "Build / Edit Your Will", the main entry point of the Will tab, gets a
  2 px blue frame so it stands out (owner: option C of the mockup, then
  only on that button, not on "Check your will";
  `lists.FRAMED_BUTTON_STYLE`, with its own padding, rounded corners and
  hover / pressed backgrounds from the palette).

**Tests:** `tests/test_core_future.py` (flowkit, three fake
will-executors with their own fee addresses, test password, fixed clock):
will in force (dates, time left, red, 3 of 3 verified), real shares equal
to the transaction outputs, entered shares over 100 %, BASIC fixed date
(no Check Alive), ADVANCED (Check Alive), period form (24 months), only a
draft, a draft over the will in force, no will, a will-executor that did
not receive it, multiverse (two instalments), nothing written, the
time-left texts. `tests/test_gui_future_view.py`: the selector switches
and is remembered (config, not wallet), an unknown value reads as the
list, the view paints (pixmap) and its texts, the Check Alive and the
square tooltips, period / draft / no will, an error in the data, busy, 30
will-executors (rows inside the width, nothing over the sentence, count and
legend), the Display note for the live will only; after the owner's
preview: UTC dates (a 22:00 UTC delivery reads its UTC day, also after
2038), the year ticks at their true places with no overlapping labels
(2026 -> 2028, 2026 -> 2045 at four widths), the label rows, 9
will-executors x 4 instalments.
`tests/test_will_summary.py`: the Will tab layout with the selector.

**Verification:** `ruff check bal/ tests/`: all checks passed; the whole
`tests/` directory once in the normal order (owner rule for a step that
changes only the look): 1918 passed, 0 failed, 0 errors; golden files
unchanged; catalogs checked against HEAD and a fresh extraction (only the
5 new entries, translated, no fuzzy), `test_translations` / `test_i18n`
pass; no scenario harness (no logic that builds, signs, sends or dates).
After the owner's preview (UTC dates, year ruler; owner rule for small
fixes): the affected tests only (`test_core_future`,
`test_gui_future_view`, `test_will_summary`: 65 passed), ruff clean, the
catalogs checked again (unchanged texts, locations refreshed: OK).
Then the blue frame and the closed versions (affected tests: 81 passed).
Before the commit the whole `tests/` directory once in the normal order:
1926 passed, 0 failed, 0 errors; ruff clean; golden files unchanged;
`test_translations` / `test_i18n` pass; the ZIP equals the one the owner
tested. Offscreen previews in Italian: will in force (ADVANCED with Check Alive),
BASIC fixed date, the Will tab with the selector, 30 will-executors, 9
will-executors x 4 instalments.

**Outcome:** DONE. The owner tested the ZIPs on his wallet (2026-10-13)
and asked, after a simulation with 9 will-executors and 4 instalments,
for the year ruler and the UTC dates (option A, the same day as the
summary box); then the blue frame on "Build / Edit Your Will" only, and
every version of the list closed at first. Last ZIP: ok. The full
multiverse timeline (every instalment with its shares and will-executors)
is left for when the multiverse is enabled again.

## 132. The will report is a clear PDF

**Date:** 2026-10-13

**Problem (the owner, after #131):** the will report of #92 is a page of
78-column monospaced text ("old style" ASCII). It says everything, but it
is not easy to read for an heir or a notary: no logo, no colours, no time
line, the addresses squeezed under the names, the technical ids mixed with
the parts everyone must understand. Saved as `.txt` and printed from
another program, it also breaks its pages where that program wants.

**What changed:**

- `bal/core/report.py` (no Qt): `will_report` builds the data of the
  report from the same sources as the Will tab, so the report, the will
  summary and the Future view always agree: the summary (title and
  colour), THE will in force of #129/#131 (`build_history` once, then
  `build_future`), the heirs with their REAL amounts and shares (what the
  will's transactions pay) and their full addresses, the total, one
  transaction per will-executor with its own fee and full id, the
  instalments (the levels of the version's tree: with multiverse each
  instalment spends the change of the one before at every will-executor),
  and the wills sent before that the will-executors still hold and that
  can still be executed (the summary's `still_active`). Read only. The
  will-executor's fee (`executor_fee`) moved here from the Will tab list,
  which now uses it from here. The old text report (`inheritance_report`,
  `paginate`, `report_text`, ...) was used only by the menu (not by the
  command line): removed.
- `bal/gui/qt/report_pages.py` (new): the A4 pages, drawn with QPainter in
  a page space of points (A4 less 15 mm margins, fonts sized in points),
  scaled to the device: the same result in the PDF and on every printer.
  Page 1, for everyone: the SAFE21 logo (the logo only, without
  "Heartbeat", owner), "Will plan" (IT "Piano ereditario", ES "Plan
  sucesorio"), created on, wallet, plugin and Electrum versions; the
  #92 privacy line; the state box in the colours of the will summary
  (delivery to the heirs as UTC day and time with the time left, right
  under it the delivery form "Fixed date" or "Period (renewable) — 24
  months (2 years)", Check Alive, will-executors "3 of 3 received and
  verified it", miner fee); the
  #131 time line with its ruler of the years, scaled to the page width;
  the heirs (name, real share, amount, the FULL address under each name)
  and the total; the will-executors (one row each with a small square in
  the colour of its state, two per row above four, and the #131
  explanation line; 30 or 40 will-executors flow to the next page, a row
  never split); how it works (the #131 sentence and the #92 paragraph; on
  page 1 when it fits, otherwise at the top of the next page, never alone
  on a page). Then the
  technical details on a new page: locktime, miner fee, for each
  will-executor its fee and the full txid, then "Wills sent before and
  still held by the will-executors" or "None: the will-executors hold no
  other will that can still be executed.". Every page: "PRIVATE — keep it
  in a safe place" (IT "PRIVATO — conservalo in un luogo sicuro", ES
  "PRIVADO — guárdalo en un lugar seguro") and "Page x/y"; after the first,
  a small header (logo + subject). A row (an heir with its address, a
  transaction with its id) is never split across pages; a section title
  always stays with its first row.
- Multiverse ready (the option is off today; built and tested with
  synthetic data, owner's plan): page 1 also has the deliveries (number,
  UTC date, share of each heir, "sent and verified x of N"), the time line
  every delivery; "Will-executors per delivery" (a grid of squares,
  will-executor x delivery, at most 40 rows then "(+N more)", with a
  legend and the sentence on how the transactions multiply, with the real
  counts: 9, 81, 729, 6561; it continues the page when it fits whole); the
  technical details list the full ids of delivery 1 only, the later
  deliveries as counts and states, with "The full list is in the will
  file (Tools → Export)". The user reads "delivery" (IT "consegna", ES
  "entrega") where the code says "instalment" (owner: "rata" sounded like
  a loan).
- The logo: `bal/icons/safe21-logo.png` (1200 x 204 px, 22 KB), made once
  from `brand/safe21-logo-light.png` (the brand's own rendering of the
  SVG); read with the plugin's `read_file` (the plugin is loaded from a
  zip; `build_zip.py` ships it, checked). No QtSvg (not verified in
  Electrum's Windows build). If the image cannot be read, the tile with
  the keyhole and "SAFE"/"21" are drawn with lines from the SVG geometry.
- `bal/gui/qt/report_actions.py`: the "Will report" menu has only "Save as
  PDF…" and "Print…"; "Save as file…" (.txt) and its code are removed
  (owner). Both commands draw the same pages, one per A4 sheet. Same rule
  as before: only for a will in force and verified by every will-executor.
- `bal/gui/qt/future_view.py`: `_font` also enlarges or reduces a font sized
  in pixels (the report's fonts), so the time line keeps its bold and small
  texts in the PDF. The time line is redrawn (owner's mockup, shared by
  the Future view and the report, `timeline_layout` / `_paint_timeline`):
  alternating light bands per year (grouped by 2/5/10 years on long
  spans) with the year on top; a thick light axis with the stretch from
  today to the next delivery in its #124 colour and a rounded "pill" with
  the time left ("in 1 year and 4 months"; above the axis when it does
  not fit on the stretch); today as a blue dot with "Today" and the UTC
  date; the Check Alive as an orange-red flag (#D85A30, red once passed)
  with a card ("Check Alive", the date, the hint), placed first so its
  pole crosses nothing; numbered delivery circles (the next one bigger, in
  its colour; later ones light green) with one card each below the axis
  ("Delivery 1 · 01/03/2028", "22:00 UTC", "Luca 10% · John 10%"; a
  single delivery: "Delivery to the heirs · <day>"), linked by leader
  lines: one row when they fit, two staggered rows, otherwise a column;
  the next delivery's card highlighted; a small legend (today, Check
  Alive, next delivery, later deliveries, time left). Nothing overlaps
  (tested for dense and sparse dates at 300-1600 px); the drawing grows
  when needed. Still no flag in BASIC with a fixed date. Under the
  delivery line the view also says the delivery form (it replaces the
  "Period (renewable)" line).
- `bal/core/future.py`: `Future.form` (`delivery.form_of` of the
  settings) and `delivery_form_text` ("Fixed date" / "Period (renewable)
  — 24 months (2 years)", the #127 msgids and `months_text`).
- Catalogs: 14 new msgids (EN/IT/ES; "Delivery form" = IT "Forma di
  consegna", ES "Forma de entrega"; "Delivery {}", "DELIVERIES", "next
  delivery", "later deliveries", "time left" = IT "Consegna {}",
  "CONSEGNE", "prossima consegna", "consegne successive", "tempo che
  manca"; "Tools → Export" reads "Tools →
  Esporta" / "Tools → Exportar": Electrum's menu shows "Tools" in every
  language, and "Esporta" / "Exportar" are the plugin's own Export entry);
  12 msgids of the text report and
  "Save as file…" are now unused (obsolete). Every other text reuses
  existing msgids (summary, report, #129, #131).
- Manual (`docs/manual/README.md`, `manual.html`): "WILL tab: the will
  report (PDF)"; `make-release.sh`: `tests/test_inheritance_report.py`.

**Tests:** `tests/test_inheritance_report.py` rewritten (the tests of the
78-column text, the CRLF file and the .txt name went with the .txt):
flowkit wills with three fake will-executors, each with its own fee
address, test password, fixed clock: the report data (real amounts equal
to the transaction outputs, full addresses, total, one transaction per
will-executor with its fee and full id, one instalment of three
transactions), multiverse on (two instalments: 3 then 9 transactions, ids
of delivery 1 only), a will sent before listed in the technical
details, the fee; the pages: 2 pages, page 1 texts (title, state, UTC
delivery with the time left, heirs, full addresses, total, will-executors
line and explanation, how it works), page 2 every txid in full, footer and
"Page x/y" on every page, everything inside the page and above the
footer, no "Heartbeat" in the header, the logo drawn from the image and
as the vector logo (teal pixel in the tile); synthetic: 30 will-executors
and 40 will-executors (no row of big squares, each listed once with its
small square, two per row on the same line, the title with the first row),
9 will-executors x 4 deliveries (3 pages, "How it works" never alone,
table, grid page with 36 squares and the counts, 9 txids only, counts of
instalments 2-4, the Export sentence), a backup-only will; the menu has only "Save as
PDF…" and "Print…"; saving writes one PDF page per report page; printing
(printer dialog mocked) draws the same pages; the owner's rule; the
"Will report" button of the Building Will dialog.
`tests/test_gui_future_view.py`: the Check Alive pole crosses no date
(five dates and widths, the flag on the left and on the right); the
flag in orange-red (scene colour and painted pixels), no flag in BASIC with
a fixed date, the delivery form line (fixed and period); the new time
line: nothing overlaps (cards, today, pill, legend, the Check Alive pole
and flag, the leader lines) for six cases (four deliveries in two years
or in one year, one delivery far or near, no Check Alive, a 45-year span)
at 300, 420, 520, 900 and 1600 px; numbered circles, the next delivery
bigger and highlighted (painted), one card per delivery, the single
delivery card, the legend; dense cards at 300 px go in a column and the
drawing grows; no "instalment" / "rata" word in the catalogs.
`tests/test_inheritance_report.py` also: the delivery form row right
under the delivery (fixed, period 24 months), the orange-red flag on the
page, no flag nor Check Alive row in BASIC with a fixed date.

**Verification:** `ruff check bal/ tests/`: all checks passed; the whole
`tests/` directory once in the normal order (owner rule for a step that
changes only the look): 1929 passed, 0 failed, 0 errors; golden files
unchanged; no CRLF in the touched text files; catalogs checked against
HEAD and a fresh extraction (only the 10 new entries, translated, no
fuzzy; the 12 expected removals obsolete), `test_translations` /
`test_i18n` pass; `build_zip.py` ships `bal/icons/safe21-logo.png` (zip
listing). Offscreen previews in Italian: the real PDFs and a PNG of every
page of a flowkit will (ADVANCED, three will-executors, two heirs) and of
a synthetic multiverse will (9 will-executors x 4 instalments, Luca and
John 10/10/15/15 %).
After the owner's preview (big squares removed, the flag pole, "How it
works" never alone, "Tools → Esporta"; owner rule for small fixes): the
affected tests only (`test_inheritance_report`, `test_gui_future_view`,
`test_core_future`: 52 passed), ruff clean, the catalogs checked again
(RESULT OK); the previews made again, plus 30 will-executors (3 pages)
and the Future view. Second follow-up (flag colour, delivery form): the
affected tests (55 passed), ruff clean, catalogs RESULT OK; previews made
again, plus a period-form report and the Future view in BASIC (no flag).
Third follow-up ("delivery" instead of "instalment", the new time line):
the affected tests (82 passed), ruff clean, catalogs RESULT OK (14 new
msgids); all previews made again. Before the commit the whole `tests/`
directory once in the normal order: 1965 passed, 0 failed, 0 errors;
ruff clean; golden files unchanged; `test_translations` / `test_i18n`
pass; the ZIP equals the one the owner tested.

**Outcome:** DONE. The owner reviewed every page (2026-10-13) and asked:
the SAFE21 logo without "Heartbeat"; the heirs' addresses on page 1; the
small square next to each will-executor only (30 or more must fit); the
"RISERVATO" line kept under the logo; the Check Alive flag in orange-red,
none in BASIC with a fixed date; the delivery form in the status; a
clearer time line ("stupiscimi": year bands, cards, time-left pill,
legend); "Consegna" / "Delivery" instead of "Rata" / "Instalment" (and,
for the future multiverse form, "Eredità scaglionata" / "Staged
inheritance"). He tested the last ZIP on his wallet: ok.

## 133. The main button of the Will tab has a light blue background

**Date:** 2026-10-13

**Problem (the owner, after the ZIP of #132):** the 2 px blue frame that
#131 put on "Build / Edit Your Will" made the button look always selected.

**What changed:** `bal/gui/qt/lists.py` `FRAMED_BUTTON_STYLE` (the "Build
/ Edit Your Will" button only): no thick frame; a very light blue background
`#EAF3FD` (owner, after the first ZIP: a little lighter than `#DCEBFB`)
with a thin light blue border `#9EC3EE`, the rounded corners and the
padding as before, and slightly darker shades on hover (`#DCEBFB`) and
when pressed (`#CDE2F8`) (owner: option A of three mockups, blue, Safe21
teal and green).
"Check your will" keeps its plain look.

**Tests:** `tests/test_check_button.py`: the Wizard button has the light
blue background and the thin border, no 2 px frame; the Check button has
neither border nor background.

**Verification:** the affected tests (`tests/test_check_button.py`,
`tests/test_gui_will_versions_list.py`: 28 passed) and `ruff check bal/
tests/` (all checks passed); no whole-suite run (owner, for a button
colour). `build_zip.py` + `external_zip_test.py` + `smoke_test.py` on
Electrum 4.8.2: pass.

**Outcome:** DONE. The owner tested two ZIPs (2026-10-13): the first
background was a little too dark, the second ok.

## 134. A new wizard for BASIC mode: When, Heirs, Will-executors, Summary

**Date:** 2026-10-13

**Problem (the owner):** in BASIC mode the wizard "Build / Edit Your Will"
showed the same pages as ADVANCED: the heirs, a settings page with the
plugin mode, the delivery date editor, the miner fee and two notes, a
combo with four ways to get the will-executors, then the will-executor
list. Too many choices for a basic user, no clear question per page, the
date editor in local time with no word on UTC, and Cancel undid nothing
(every change was already saved).

**What changed:**

- `bal/gui/qt/basic_wizard.py` (new): the BASIC wizard, four pages with a
  step bar on top ("1 When", "2 Heirs", "3 Will-executors", "4 Summary")
  and a question as the first line of each page; Next has the light blue
  look of the #133 main button. The pages are built on the old
  `BalWizardWidget` (same Previous / Next / Cancel buttons and layout).
  - **1 When** ("When do the heirs receive the bitcoin?"): quick buttons
    "In 1 year", "In 2 years" (chosen when the wallet has no will, the
    default date of a new wallet), "In 5 years", "Choose the date…", and
    "Leave as it is" (offered and chosen only when the wallet has a will
    and a stored date in the future). "Choose the date…" shows Year |
    Month (the month name in the plugin's language) | Day | Hour (whole
    hours) in the computer's time, with a "?" whose new text mentions UTC
    and the network delay (the old text stays for ADVANCED); the day
    follows the month length. Under them the will's day in big letters
    with the time left, then "On your computer: … (your computer's time,
    UTC+2)" and "In the will: … UTC", and a line when the UTC day is the
    day before / after. A moment not after now is refused (Next off, a
    short message). A date later than the will in force shows the yellow
    note about the paid invalidation. A period-form wallet (#127) only
    sees its renewal. The box "Why two times?" explains UTC and the block
    clock delay. Bottom left, "Mode: Basic · Advanced" switches the plugin
    mode through the same "at My Risk" gate as the settings page
    (`common.switch_user_type`); confirmed, the wizard goes on with the
    old ADVANCED pages. No calendar button, no truck icon, no miner fee,
    no Check Alive (BASIC uses the stored fee).
  - **2 Heirs** ("Who receives the bitcoin?"): a reminder of the chosen
    day, the real heirs list, "+ Add heir" (today's New heir window),
    Import, Export, "Total: 100% of the wallet balance." (only for
    percentage heirs; a total other than 100 says that the shares count in
    proportion and the whole balance is spent, which is what
    `Heirs.normalize_perc` does; with fixed amounts no total line) and the
    note "Remember: 100% of the wallet balance is spent.". Next is off
    without heirs.
  - **3 Will-executors** ("Who keeps the will?"): a one-line explanation
    (no "Learn more" link: there is no published manual page to point
    to), then the list is downloaded and pinged at once and only the
    will-executors that answer are selected; "✔ List updated: 3
    will-executors answer, 3 will-executors selected." Nobody answers: a
    yellow note with Retry, Next off. One selected: "At least 2
    will-executors are recommended." (Next allowed). None selected: Next
    off. The full `WillExecutorWidget` is shown below. The old
    four-choice combo page is not used in BASIC.
  - **4 Summary** ("Check and create"): a light green box with the
    delivery (UTC day and time, time left, the computer's time), the
    heirs and their shares, the number of will-executors and their fee
    ("1,000 sat each", or "from … to … each"), two short notes, and
    "Create the will", which runs exactly the old last step
    (`BalWizardDialog.on_next_we`: the build / sign / send / verify
    window, unchanged).
  - **Cancel is linear (owner decision):** a `WizardSnapshot` is taken
    when the BASIC wizard opens (the heirs, the delivery date with the
    keys its write clears: the "which date?" question, its note and the
    later wish of the migration, and every will-executor's `selected`
    flag). Cancel, the window's close button and Esc put them back
    exactly, through the same writers (`Heirs.save`, the validated
    `update_setting_widgets`, `delivery.validate_values` +
    `write_values`, `Willexecutors.save`); will-executors downloaded
    meanwhile stay in the list, not selected. "Create the will" keeps the
    changes. The date is written when page 1 is left (a heir added on page
    2 gets it from the settings) and restored on Cancel. After a switch to
    ADVANCED the old pages behave as today (nothing restored).
- `bal/core/wizard_date.py` (new, no Qt): the date logic of page 1: the
  quick dates (00:00 UTC + 12/24/60 calendar months), the local boxes read
  in the time zone of the CHOSEN date (summer/winter time), the UTC offset
  text, the day change, the month length, the refused past, "Leave as it
  is" and "later than the will in force". 2038-safe.
- `bal/gui/qt/dialogs.py`: `BalWizardDialog` opens the BASIC flow in BASIC
  mode and the old pages in ADVANCED (unchanged); `switch_to_advanced`,
  `show_executors_step` (the build window's "no will-executor" button opens
  page 3 in BASIC, the old download page in ADVANCED), the restore on
  close / Esc, and "Create the will" keeps the changes. The old download
  page's "download and select" now calls the shared function below.
- `bal/gui/qt/window.py`: `download_and_select_willexecutors`, the
  download + ping + select-only-who-answers of Task #02 in one place,
  used by the old page and the new page 3 (the new page also pings the
  saved list when the download fails, so an offline computer shows
  "nobody answers").
- `bal/gui/qt/lists.py` `WillExecutorWidget` (the Tools menu window and
  both wizards; look only, owner option A): the empty size line is hidden,
  the rows alternate colours, the "Add transactions without willexecutor"
  row moved below the buttons in small grey text. Every column, button and
  setting is unchanged (the columns already fit their content).
- `bal/core/heirs.py`: `percent_total` (the total line of page 2).
- `bal/i18n.py`: `language()` (the month names follow the plugin's
  language).
- Catalogs: 54 new msgids (98 with the follow-ups below), Italian (the owner's wording of the approved
  prototype) and Spanish.
- `docs/manual/README.md` and `manual.html`: "The wizard in BASIC mode".
- `tests/test_gui_delivery_form.py`: the BASIC wizard now opens on the
  taller "When" page, so `test_wizard_grows_when_advanced_is_chosen`
  checks that the dialog opens at least 800x400 instead of exactly.

**Tests:** `tests/test_core_wizard_date.py` (new: quick dates, Europe/Rome
summer and winter, a zone behind UTC, UTC+5:30, the month length and 29
February, the past refused, "Leave as it is", later than the will in
force, dates after 2038) and `tests/test_gui_basic_wizard.py` (new, the
REAL window on flowkit wallets with three fake will-executors, the time
zone fixed to Europe/Rome, the welist and the ping faked: the four pages
in order and ADVANCED unchanged, the quick choices and the boxes, the day
change, the past refused, "Leave as it is" with a will in force and the
later-date note, the period form, a new heir gets the chosen date, only
the will-executors that answer are selected, nobody answers then Retry,
one selected, the summary texts, "Create the will" builds, signs, sends
to the three and verifies, Cancel / close / Esc restore heirs, date and
selection, the keys the date write clears come back, a new will-executor
stays unselected, the mode link, the look of the Tools window).

**Verification:** `ruff check bal/ tests/`: all checks passed. Whole
suite: 1999 passed in the normal order, 1999 passed in the reverse
order (first ZIP); goldens unchanged then (see below for the follow-up); no CRLF. Catalogs: 634 -> 688 entries (732 after the follow-ups) in the
three catalogs, no fuzzy, no empty, nothing else changed;
`tests/test_translations.py` + `tests/test_i18n.py` pass. `build_zip.py` +
`external_zip_test.py` + `smoke_test.py` on Electrum 4.8.2: pass. Italian
previews of the real pages checked by eye.

**After the owner's ZIP test (2026-10-13):** the owner tested the first
ZIP (all OK) and asked for these changes inside #134:

- **Wording:** "On your computer:" becomes "In your time zone:" (IT "Nel
  tuo fuso orario:") on page 1 and in the summary of page 4; the local line
  shows only the offset, "(UTC+2)".
- **Bug: a prepared draft was called "the will in force".** Owner's real
  wallet: a will sent and verified for 26/02/2031 and a will prepared (not
  signed nor sent) for 13/10/2028, the stored date. Page 1 offered "Leave
  as it is" with 13 October 2028 and said "The date of the will in force".
  Cause: `delivery.earliest_in_force` reads the VALID items first, and the
  draft is VALID. Now the wizard uses `engine.context.in_force_delivery`
  (new): the earliest date of `Will.in_force_items`
  (`will_state.is_in_force`: signed and/or sent, not invalidated, still
  executable), so a draft never counts. The note of "Leave as it is" says
  what the date really is: "The date of the will in force" only when it
  is; "The date you chose last time (prepared will, not sent yet)" with a
  draft; "The date already chosen" otherwise. The later-date note compares
  with the will really in force.
- **A reference for the comparison (owner: "serve un riferimento di
  confronto"):** page 1 shows, under the choices while a will is in force,
  "Will in force: … · Your choice: … (earlier — free)" / "(later — requires
  the paid invalidation)" / "(same date)"; page 4 adds "In force now: … →
  After signing: … — moved earlier by 1 year"; the will summary of the
  Will tab, for a will prepared or signed but not sent whose date differs
  from the will sent before (still executable), adds the two lines "In
  force now" and "After signing … → delivery moved earlier by 2 years and 4
  months" (or "later by … — requires the paid invalidation of the will in
  force"). The span is `bal.core.future.span_text` (calendar months).
- **One date style (owner decision):** `bal/core/datetext.py` (new, no Qt)
  writes every delivery date the owner reads: long "13 October 2028, 00:00
  UTC" (IT "13 ottobre 2028, ore 00:00 UTC"), with the optional second line
  "In your time zone: 13 October 2028, 02:00", and short "13 Oct 2028,
  00:00 UTC". The month names are msgids (the short name is the first
  three letters of the translated name). Used by the wizard (all pages;
  the Qt month names of the first ZIP are gone), the will summary of the
  Will tab (with the italic note), the version headers of the List view
  (before: the LOCAL time in the locale's format, "13/10/2028 02:00",
  without saying so), the result sentence and "Your will now" of the Check
  window, the Check Alive question and reasons in the GUI and the
  "older will still active" warning. `will_summary(long_dates=True)` and
  `messages.check_alive_message(info, day=...)` choose the style: the PDF
  report, the Future view and the command line keep `YYYY-MM-DD` (the
  report and the Future view in step #137). Technical columns (Locktime,
  txids) are unchanged.
- **Golden files:** 66 files of `tests/golden/5e` (check/ and copies/)
  changed, 114 lines, each line only because of the new date style of the
  Check window's result sentence, "Your will now" and the Check Alive
  texts (for example "Delivery to the heirs: <b>2032-09-23</b>" ->
  "<b>23 September 2032, 00:00 UTC</b>"); nothing else changed in them.
- Tests: `tests/test_gui_basic_wizard.py` +4 (the owner's draft case on
  page 1 and page 4, the Will tab summary lines, the date and change texts);
  the date literals of `test_gui_will_versions_list.py` (the header test now
  checks the short UTC form, also in Italian), `test_gui_check_variables_rows.py`,
  `test_gui_flow_adapter.py`, `test_gui_still_active_warning.py` and
  `test_delivery_period_flow.py` follow the new style. Affected tests and
  the golden tests: pass; the whole suite (both orders) runs before the
  commit. Catalogs: 712 entries, no fuzzy, no empty.

**After the owner's second ZIP test (2026-10-13):**

- **The will-executors' fee on page 4 is paid once, not "each":** every
  selected will-executor holds its own copy of the will with its own fee
  output, but the copies spend the same coins, so only one can be
  confirmed and one fee is paid. The row shows the fee (or "from <lowest>
  to <highest>", in the wallet's unit with the fiat value when Electrum
  shows it) and, in grey, "you will pay only one: the one of the
  will-executor that sends the will on the delivery day." ("…only one of
  these…" for a range; IT the owner's words "ne pagherai una sola (di
  queste): quella del will-executor che invia il testamento il giorno
  della consegna."). The "each" texts are gone.
- **The comparison is shown only when the dates differ, on two lines:**
  page 1 "Will in force: …" / "Your choice: … (earlier — free)" or
  "(later — requires the paid invalidation)"; page 4 "In force now: …" /
  "After signing: … → delivery moved earlier by …"
  (`datetext.in_force_change_lines`, empty for the same date); the Will
  tab summary already showed its two rows only for different dates. The
  value column of page 4 wraps inside the window instead of being cut.
- Tests: `tests/test_gui_basic_wizard.py` (28: the fee texts, the two
  lines, the same date shows no comparison on page 1 and page 4),
  `tests/test_will_summary.py`, `tests/test_translations.py`,
  `tests/test_i18n.py`: 86 passed; ruff clean; catalogs 714 entries, no
  fuzzy, no empty, the replaced msgids removed. No golden file changed
  (these texts are only in the wizard).

**After the owner's third look (2026-10-13):**

- **The Future view in the one date style:** it sits under the will
  summary, so it writes the dates the same way (`future_view.view_scene`):
  "Today 13 Oct 2026, 14:31 UTC" on the time line, the card "Delivery to
  the heirs" with "31 Dec 2030, 01:00 UTC" under it (the date left the
  title: "· 23 set 2032" was cut on a narrow card), and the long form in
  the lines under the time line, the sentence ("…, on 31 December 2030,
  01:00 UTC the bitcoin will go to …") and the italic note
  (`build_future(day=...)`). The PDF report calls the same functions with
  their defaults, so none of its texts changed, its time line included
  (the report's dates come in #137). The overlap test of the time line
  runs in both styles at 300 to 1600 px.
- **The main button of the Will tab** ("Build / Edit Your Will") now looks
  like the unchecked quick-choice buttons of the wizard's first page:
  white, thin light blue border, rounded corners, bold, light blue only on
  hover (`lists.MAIN_BUTTON_STYLE`); it replaces the #133 light blue
  background, which stays only on the wizard's primary button (Next).
  Same size (icon 28 px, height 40, same padding). "Check your will" is
  unchanged.
- No new msgid; no golden file changed (the Will tab's Future view and the
  button are not in the golden records). Tests: `test_gui_future_view.py`
  (the view's texts in the new style; the overlap test also in that
  style), `test_core_future.py`, `test_inheritance_report.py`,
  `test_gui_basic_wizard.py`, `test_will_summary.py`,
  `test_check_button.py` (the button's new look), `test_translations.py`,
  `test_i18n.py`: 208 passed; ruff clean; catalogs OK.

**After the owner's fourth look (2026-10-13):** with a will PREPARED
(a draft or a partly signed version, not sent) whose date differs from
the will in force, the Future view looked like a normal green "in force"
view under the orange "Will prepared: it must be signed and sent". Now
`core.future.build_future` sets `pending_locktime` (the prepared will's
date, from the same versions the view already reads: the draft version
differs from the version in force; derived, nothing stored) and the Will
tab's `future_view.view_scene` shows it in grey: the title "Current
situation at the will-executors", a light blue box "This is what the
will-executors hold today (delivery …). The prepared will (delivery …) is
not signed and sent yet: when you sign and send it, this view is in
colour again with the new date.", every colour of the will in force grey,
and the prepared date as a dashed grey ghost "prepared, not sent" (the
draft marker of #131 reused); the time-left pill keeps clear of it. The
italic note is left out (the box says the same). Without a different
prepared will the view is in colour as before. The PDF report never
greys (it calls `future_scene` directly): no report text changed. A
wallet with only a draft and nothing ever sent keeps today's view ("No
will in force", the draft's dashed date, "Will prepared: it must be
signed and sent"). A will signed but not sent counts as in force in the
versions of #128, so it is not greyed (unchanged). Tests:
`test_gui_future_view.py` (the grey scene and its texts, the colours back
without a different prepared will, the report's scene never grey; the
overlap test also with a prepared will one year earlier or later, at 300
to 1600 px), `test_core_future.py`, `test_inheritance_report.py`,
`test_gui_basic_wizard.py`, `test_will_summary.py`, `test_translations.py`,
`test_i18n.py`: pass; ruff clean; 3 new msgids (IT/ES).

Also (owner): the legend symbol of "time left" under the time line is now an elongated pill, 24 x 8 px like the one on the axis (it read as a dot); the same legend is drawn in the PDF report's time line, so the symbol changed there too (no text changed). `test_gui_future_view.py`: 143 passed.

**After the owner's fifth look (2026-10-13):** the Future view is in
colour only when at least one will-executor has received and verified
the will it shows (`future_view.nothing_received`: no executor mark of
that version is "verified", the same states as the summary's "x of N
received and verified"; view-only, the #128 notion of "in force" in
`will_versions` / `will_state` is untouched). Otherwise (a draft only, or
a will signed but not sent, e.g. signed offline) the view is grey and
drawn at 48% opacity under its title, with a big centred overlay on the
time line: "NOT SENT TO THE WILL-EXECUTORS YET" / "Until you send it,
nothing happens on the date." (IT "NON ANCORA INVIATO AI WILL-EXECUTOR" /
"Finché non lo invii, alla data non succede niente."); the draft-only
view keeps its title and note. A will with no will-executor at all (the
backup transaction kept by the owner) keeps its colours. An older will
really sent plus a prepared one with another date stays the grey case of
the fourth look. The PDF report never fades. The Will tab still opens on
"List" by default, List on the left and Future on the right (checked:
#134 did not touch `WILL_VIEW` or the selector). Tests:
`test_gui_future_view.py` (colour only with one verified executor, five
state mixes; draft only; backup only; the overlay inside the view under
the title at 300 to 1600 px; the overlap test also for the faded case),
`test_core_future.py`, `test_inheritance_report.py`,
`test_gui_basic_wizard.py`, `test_will_summary.py`, `test_translations.py`,
`test_i18n.py`: pass; ruff clean; 2 new msgids (IT/ES).

Also (owner): a will signed but not sent (every will-executor still "to send" or failed) has the faded view's title "Will signed, not sent yet" (IT "Testamento firmato, non ancora inviato") instead of "Your will is in force"; the draft-only view keeps its title.

Also (owner): BASIC hides the "Add transactions without willexecutor" option of the will-executor list (wizard page 3 and Tools → Will-Executors); ADVANCED shows it; the stored value is never changed; a window opened after a mode switch follows the mode.

**After the owner's ZIP test: cancelled postpone (owner, option B).**
The owner's real case: a will sent and verified for 31 Dec 2030, a draft
for 13 Oct 2028, the wizard's "In 5 years" (10 Oct 2031: a postpone),
"Create the will", Cancel at "Invalidate your old will". The later date
stayed in the settings with no will item carrying it: the summary never
showed it, the result said "nothing was changed", and every Check and
wallet close asked for the invalidation again. Now, when a postpone's paid
invalidation is not done and certainly reached no network, the stored date
goes back to the date the will items really use: the prepared draft's date
(when it is not later than the will in force), else the will in force's
date (`engine.context.cancelled_postpone_date` /
`revert_cancelled_postpone`, Qt-free, the validated write; the #128 notion
of "in force" is untouched; nothing new stored). The result says
"Interrupted: the old will was not invalidated. Your later date (10
October 2031, 00:00 UTC) was not applied: the will in force needs the paid
invalidation first. When you are ready, choose it again." Cases:

- Cancel at the invalidation's password in the Check window (the wizard's
  "Create the will", the Check button, the summary's "Sign and send" in
  BASIC, the Check at wallet close): reverted.
- The invalidation could not be signed: reverted (never broadcast).
- The invalidation was broadcast (also with a broadcast error, or not yet
  confirmed): NOT reverted, the postpone may be underway.
- An expired will or a renewal (#79): unchanged (not a postpone).
- The automatic rebuild: not reverted (it never asks; an encrypted wallet
  cannot invalidate there, so the next Check asks the owner).
- The command line (`bal_will_check` / prepare): not reverted; it returns
  the invalidation transaction to sign and broadcast and has no Cancel.

Golden file: `check/cancel_at_invalidate` ("date later, Cancel at the
invalidation, Check again"): the result of the cancelled Check is the new
sentence, the date goes back to 2032-09-23, and the second Check finds the
will coherent (nothing to invalidate, sign or send) instead of
invalidating. No other golden changed. Tests: `tests/test_cancelled_postpone.py`
(new, 7: the rule; the owner's case through the wizard with the next Check
not asking; no draft -> the will in force's date; wallet close; signing
failure; a broadcast invalidation keeps the later date; the command line);
all golden test files (247 passed) and the affected files pass; ruff clean;
1 new msgid (IT/ES).

**After the owner's ZIP test: complete the sending, deselected
will-executors.** Owner's real test: version #20 for 5 will-executors, 3
signed, sent and verified, 2 signed but not sent; the summary said "Will
signed: it must be sent", "3 of 5 verified it · 2 to be sent" and the
false italic "Until you sign and send it, the will sent before stays in
force" (same date, the same will). His log then showed the 2 had been
DESELECTED after the signing: the sending rightly skipped them, so Send
pushed nothing and the box asked to send forever.

- `bal/core/summary.py`: a will received and verified by at least one
  will-executor with SELECTED ones still to send: "Will in force: complete
  the sending" (orange, the colour of "In force, but N did not receive
  it"), "3 of 5 will-executors received and verified it. Send it to the
  other 2 too, for more safety.", the Send button; no "will sent before"
  note and no comparison rows. That note now appears only when the will
  sent before has another date than the one prepared or signed now.
- One rule for deselected will-executors (`summary.selected_urls`,
  `executor_states(..., selected)`), used by the summary, the Check
  window's rows and counts, the report's states and gating, and the Future
  view's squares: a copy for a will-executor deselected now that never
  received the will is "deselected: not sent", not "to send", and is not
  counted ("3 of 3 received and verified it": green, no Send). The summary
  box and the Check window add "2 will-executors are deselected, so they
  did not receive the will. To send it to them too, select them again."
  with a small secondary "Open Will-Executors" button (the Tools window);
  when it closes, the box and the rows follow the selection, so a
  will-executor selected again is "to send" and Send pushes it. What is
  pushed did not change. When every will-executor is deselected the rule
  is not applied (nothing would be counted).
- Golden files: none changed (all 247 golden tests pass unchanged). Tests:
  `tests/test_will_summary.py` (+3: the owner's 3/5 case, singular and
  failed, the note only for another date), `tests/test_deselected_executors.py`
  (new, 7: 3 verified + 2 deselected -> green 3/3 and the line; the Check
  pushes nothing to them, rows "deselected: not sent"; selected again ->
  "complete the sending", Send pushes it; the box's line and button and
  the refresh at the window's close; the window's close callbacks; the
  Check window's line and button); 9 new msgids (IT/ES).

Also (owner): the List's row of the will in force follows the same rule: "sent and verified 3 of 3 · 2 deselected" (IT "inviata e verificata 3 su 3 · 2 deselezionati"); older versions keep their counts as built (history). The Will-Executors window's close refreshes the summary box and the List. No golden changed (247 passed); `tests/test_deselected_executors.py` +1; 2 new msgids.

**Full-suite fix of the cancelled postpone (#123 funds-safety tests).**
Two tests of `tests/test_per_wallet_settings_flow.py` failed with option B
(both orders, 2 of 2159):

- `test_sent_will_earlier_than_the_will_in_force_is_a_postpone`: a VALID
  will B signed and sent at the stored date, and an older sent will A,
  REPLACED but still executable, EARLIER. The Check asks to invalidate A;
  Cancel. Option B took the "will in force" date (A's, the earliest) and
  wrote it back: the next Check would read an anticipation and sign the
  newer will at the earlier date, exactly what the test forbids. Fix: no
  revert when the stored date is the date of a VALID will already signed
  or sent (`engine.context.cancelled_postpone_date`): that is a newer will,
  not a wish to undo.
- `test_question_use_later_asks_for_the_invalidation`: the #123 "which
  date?" question answered "Use 15/07/2029 (later: the old will must be
  invalidated, miner fee)", then Cancel at the invalidation: the explicit
  answer must stay (as before #123/#134). Fix: no revert when the later
  date was chosen through that question in the same Check
  (`BalBuildWillDialog._later_by_question`).

The owner's option-B case (a later date chosen in the wizard or the
settings, carried by no will item) still goes back. Results:
`test_per_wallet_settings_flow.py` + `test_cancelled_postpone.py`: 53
passed; all golden tests: 247 passed, unchanged; ruff clean.

**Final verification before the commit:** `ruff check bal/ tests/`: all
checks passed. Whole `tests/` directory: 2159 passed in the normal order,
2159 passed in the reverse order, 0 failed, 0 errors. Golden files: the 66
of `tests/golden/5e` listed above (date style; `check/cancel_at_invalidate`
also for the cancelled postpone), nothing else. No CRLF in the touched
files. Catalogs: 732 entries, no fuzzy, no empty. `build_zip.py` +
`external_zip_test.py` + `smoke_test.py` on Electrum 4.8.2: pass.

**Outcome:** DONE. The owner tested the ZIPs on his wallet (2026-10-13)
and asked for every follow-up above; last ZIP OK.

## 135. "What changes compared with the will in force": one box before the signature and on the wizard summary

**Date:** 2026-10-13

**Problem (the owner):** when a will is already in force and the owner
changes something (a heir, the shares, the date, the will-executors, the
fee, the message), nothing says WHAT the new will changes before he signs
it. The Check window goes straight to the "Sign your will" password, and
page 4 of the BASIC wizard shows only the new settings. The heirs' shares
were shown as written (for example 50% + 30% + 50%), not what each heir
really receives when the total is not 100.

**What changed (informative only: the flow, the protocol and the stored
data do not change; nothing new is stored):**

- `bal/core/will_changes.py` (new, no Qt): the comparison. One side is the
  will in force (the #128/#134 notion: `Will.in_force_items`, without the
  will-executors deselected now that never received it, the #134 rule);
  the other is the will just prepared, or what the settings will give.
  Heirs are matched by ADDRESS (so a new name on the same address is
  "renamed"). Rows, only for what changes:
  - **Delivery**: old → new date, "moved earlier by … — free" or "moved
    later by … — requires the paid invalidation of the will in force"
    (the #134 `datetext` wording);
  - **Heirs**: added / removed / renamed;
  - **Real shares**: before → after real proportional share, only the
    heirs whose share changes; a grey note when the written total is not
    100 ("what each one really receives: the shares are recalculated in
    proportion, the whole balance is always spent", which is what
    `Heirs.normalize_perc` does). Not shown with fixed amounts (the real
    split then depends on the balance);
  - **Will-executors**: "from N to M: added …; removed …";
  - **Miner fee**: old → new sat/vB;
  - **Message** (OP_RETURN): changed / added / removed;
  - identical: one row "No change: the will stays the same as the one in
    force"; a first will (nothing in force): no box.
  With multiverse only the first delivery is compared (the will's date).
- `bal/engine/context.py`: `in_force_items(host)` (the will in force,
  refreshed, `{}` on error) and `prepared_items(host, in_force)` (the will
  as it will be once signed: the drafts first, then the copies in force
  they leave in place; when only a will-executor is added the check
  prepares a copy for it alone and the copies in force stay the will).
- `bal/core/build_report.py`: `set_changes(rows, when)`; the snapshot has
  `changes` / `changes_for` only when the box is shown.
- `bal/gui/qt/widgets.py`: `what_changes_box(rows)`, light blue with a
  left accent bar; the Check window's report view draws it under the
  result line, with the sentence "Check the list: by signing, this becomes
  your will. If something is wrong, press Cancel at the password request:
  nothing changes." at the signature.
- `bal/gui/qt/dialogs.py`: the Check window fills the box BEFORE the "Sign
  your will" password (in force vs prepared), and before "Invalidate your
  old will" of a postpone (in force vs the settings; no sentence, it is
  not the signature). Cancel at either password changes nothing, as
  before.
- `bal/gui/qt/basic_wizard.py`, page 4: the box under the green summary
  (in force vs the new settings); it replaces the #134 comparison lines
  under the delivery. The Heirs row shows the REAL proportional shares,
  with a grey "(recalculated in proportion)" when the total is not 100;
  with fixed amounts the old text stays.
- Catalogs: 18 new texts, Italian and Spanish.

**Golden files changed (only added lines):** 17 files of
`tests/golden/5e/` (`check/`: cancel_at_invalidate, cancel_at_sign,
closing, date_earlier, exclude_down, executor_added,
executor_already_has_it, executor_down, fee_changed, heir_added,
heir_removed, invalidation_not_seen_yet, new_coin, owner_drafts_128,
phase2_error, retry_after_down; `copies/`: wallet_close_fee_changed).
Why: the Check window's report snapshot now records the new box
(`changes`, `changes_for`) in every scenario that signs, or invalidates
for a postpone, with a will in force. 227 lines added, 0 removed; nothing
else in those files changed.

**Tests:** `tests/test_will_changes.py` (new): each kind of change alone,
several together (the owner's case), identical, first will, fixed amounts,
deselected will-executors; the Check window has the box before the "Sign
your will" password and Cancel there changes nothing (nothing sent, the
will in force stays); a first will has no box; a will-executor added says
"from 2 to 3: added we2.test" (not "removed"); the box before the
postpone invalidation; wizard page 4 rows and real shares.
`tests/test_gui_basic_wizard.py`: the page 4 comparison test now checks
the box instead of the #134 lines.

**Follow-up after the owner's ZIP test (same step, owner approved):**

- **Wording, never "paid".** The invalidation sends the will's coins to a
  new address of the SAME wallet (`Will.invalidate_will`): the bitcoin stay
  the owner's, only the normal network fee is paid. Replaced texts:
  "moved later by … — needs an invalidation transaction on the blockchain:
  your bitcoin stay yours, you only pay the normal network fee" (the box,
  the summary and the wizard's comparison), page 1's yellow note, page 1's
  "(later — needs an invalidation transaction: you only pay the network
  fee)", the Check result after a cancelled postpone, the List row
  "Invalidation transaction" (was "Paid invalidation"), and the two
  delivery-settings texts of the window ("To use the later date again…",
  "Use … (later: …)"). The 7 old msgids are removed from the catalogs (no
  obsolete entries left).
- **Invalidation step.** Before invalidating, the Check window shows the
  box "Invalidation of the old will": the amount moved, "a new address of
  your wallet: tb1q…" (checked with `wallet.is_mine`; an address not of
  this wallet would be said in red), the network fee in sat (with fiat
  when Electrum has a rate) and its sat/vB, the wills it makes useless
  ("#3 — delivery … — 3 copies at the will-executors": the versions held
  or in force, or partially signed, that spend one of its coins; the 3
  earliest, then "and N older versions"), and "Show the transaction",
  which opens Electrum's transaction window on a COPY of that transaction
  with its Sign and Broadcast buttons hidden (read-only). Under it
  "Cancel" and "Invalidate now": the password "Invalidate your old will"
  is asked ONLY after "Invalidate now" (owner's ZIP test: the box came
  while Electrum's modal password window was already on top, so its
  button could not be used). Cancel at the step or at the password, or
  closing the window: not invalidated, the later date goes back (#134
  option B); the box stays, with its button working (the transaction was
  never signed). The same for every invalidation of the Check (postpone,
  Check Alive renewal; also at wallet close, the owner is there); the
  expired will keeps Electrum's classic transaction window; the automatic
  rebuild is unchanged. `bal/core/invalidation_preview.py` (new, no Qt)
  derives it from the built transaction; nothing is stored, what is
  signed and broadcast does not change.
- **One "will in force".** Owner's wallet: the List said #23 (29 Dec) in
  force, the summary and the Check window said "In force now: 30 December",
  the wizard compared with 29 Dec. Root cause: the summary took the will in
  force from `Will.sent_still_executable`, which leaves out VALID items;
  after the rename the rebuild made, for the will-executors that already
  held the 29 Dec will, the very same transactions (same address, amount,
  date: same txid), so those copies, sent and verified, became VALID items
  of the prepared will and dropped out of that list, and the older 30 Dec
  will was named. The one rule now everywhere (List, Future view, wizard,
  "What changes" box, Will tab summary, Check window, PDF report): the
  #128 rule, `will_state.is_in_force` (signed and/or sent, VALID or
  REPLACED, not invalidated or executed, still executable), the EARLIEST
  delivery among those items: what can execute first, so the right date for
  the funds. `will_summary(in_force=…)` (from
  `engine.context.in_force_delivery`, or
  `will_versions.in_force_delivery(history)` in the report). #128/#123
  rules unchanged.
- **Rename said once (owner's ZIP test).** After renaming an heir (same
  address, share and date) every Check, and the wallet opening, said
  "Heir renamed: luigi → luigi modifica nome: preparing a new will",
  rebuilt the very same transactions ("not needed" to sign or send) and
  changed nothing. Root cause: `Will.update_will` writes the new heirs
  only on the items the rebuild makes again (same txid, #122); the copy of
  a will-executor the rebuild does not make (a deselected one: still
  VALID and sent) kept the old name, so the check by name saw the old name
  missing at every Check. Fix (`Will.sync_heir_names`, called first by
  `engine.context.check_will`): a pure rename (an heir name of a VALID
  item that the Heirs tab no longer has, and exactly one heir of the tab
  with the same address and the same amount, the same new name for every
  item) only changes the NAME in the items' stored heirs, and the will is
  saved. Funds-safe: names are not in the transactions; outputs, amounts,
  locktime, signatures, statuses and what the will-executors hold do not
  change; the usual check then runs under the new name, so a date (or
  anything else) that changed too is still found and rebuilt. The Check
  says "Heir renamed: luigi → luigi new: only the name changes, the will
  stays the same." once; the next Check is clean. A rename together with
  another change of the same heir (share) keeps the rebuild path, with
  "Heir renamed: …: preparing a new will."; the heirs box uses the names
  of the Heirs tab (by address).

- **Deselected leftovers are never signed (owner's ZIP test).** Will in
  force at 3 will-executors; 2 more selected, Check, Cancel at "Sign your
  will" (their 2 copies stayed unsigned drafts); the 2 deselected again,
  Check: "No change" but the password was asked and those copies were
  signed (never sent). Root cause: the Check's "to sign" counted every
  VALID item not signed, without the #134 rule, and signed every VALID
  item. Now (`BalBuildWillDialog._ids_to_sign`) a copy of a will-executor
  not selected now that never received the will is not counted and not
  signed: left as it is (unsigned, only in this wallet, no will-executor
  holds it: it cannot execute; a later rebuild drops it as a draft, #128;
  selected again, it is signed and sent). Signed or sent items are never
  touched. No signing step, so no "What changes" box: the box and the
  signing can no longer disagree.
- **One box of the changes (owner's decision).** "What changes compared
  with the will in force" holds every change, the rename too: the heirs
  only renamed by the Check (written before the comparison) are a Heirs
  row "renamed: luigi → luigi new (only the name changes)"
  (`will_changes.with_renames`); a Check with only a rename shows the box
  with that row and no signing step. When the box is shown, the old "What
  changed" box is not drawn (its technical sentences stay in "Details
  (log)"); it stays for what the new box does not cover (no will in force,
  an expired will, errors).

- **Shares recalculated in proportion, calmly (owner).** The "Attention:
  the heirs' percentages add up to 130%…" box was shown at every Check.
  Now the explanation stays in "Details (log)"; the Check window shows a
  box with a calm title ("How the shares are divided", "The percentages
  you wrote add up to 110%. The will always spends the whole balance, so
  each heir receives a share in proportion: …") only when the shares as
  written ({address: amount}, names not counted) differ from the will in
  force (its earliest delivery) or there is no will in force yet;
  otherwise one quiet grey line ("Shares recalculated in proportion:
  alice 54.5% · bob 45.5%").
- **Rename row wording (owner).** "renamed: luigi → luigi new (only the
  name changes, already saved: no need to sign)" (one function,
  `will_changes.name_only_text`); with other changes to sign, the sentence
  ends "nothing else changes (the new names are already saved)".
- **The will-executors box's note about deselected ones wraps fully**
  (it was right-aligned at its preferred width and cut off).

- **One way to edit an heir (owner's ZIP test).** The owner renamed
  "erede test" with the Edit dialog and the wallet then had BOTH names at
  the same address: the next will would have given that address a second
  share, and the box said "renamed". Root cause: `new_heir_dialog` saved
  the heir under the new name and never removed the old one (`set_heir`
  only adds), while the List's inline cell editing renamed by delete +
  add. Now: no inline editing in the Heirs List (no editable column or
  cell; the right-click menu's "Edit <column>" became "Edit"); a double
  click and right click > Edit open the same "Edit heir" dialog, also on
  the BASIC wizard's heirs page; a rename removes the old name AFTER the
  new one is saved (a refused entry never loses the heir); a name used by
  another heir is refused ("An heir named … already exists.", for a new
  heir and an edit); the dialog says what an edit means: "Only the name
  changes: no need to sign." / "You are changing who receives: the will
  must be signed again." / "The share changes: the will must be signed
  again.". `will_changes` no longer merges several names on one address:
  "added X (same address as Y)", never "renamed". The BASIC wizard's
  rename row: "(only the name changes: no need to sign)" (the same
  function, `name_only_text(saved=False)`). The manual does not describe
  the inline editing: unchanged.

- **No two heirs on one address (owner).** The "Edit heir" / "New heir"
  dialog (List and BASIC wizard) and the command line (`heirs_add`,
  `heirs_update`) refuse an address already used by ANOTHER heir: "This
  address is already used by the heir {name}." (the heir's own address
  stays allowed). Compared by `core.heirs.address_key`: spaces dropped,
  bech32 (bc1/tb1/bcrt1, case-insensitive by BIP173) in lower case, base58
  as written, OP_RETURN rows never compared. An import (GUI, command line)
  is not refused: the Heirs tab and the wizard's heirs page show "{a} and
  {b} have the same address: keep only one." and `heirs_import` returns
  `same_address`. Old wallets with such heirs are never changed and the
  Check is not blocked. Wallet formats and the protocol unchanged. Two
  command-line tests gave two heirs the test wallet's one address: each
  heir now has its own.

- **Plain heir errors (owner's ZIP test).** The "Edit heir" dialog showed
  Electrum's raw "not an address,bc1q…". One helper for the dialog and the
  command line (`core.messages.heir_error_message`): "Not a valid address
  for this network: {address}" and "Check that you copied all of it, with
  no extra or missing characters."; also the OP_RETURN message (not
  hexadecimal, too long), the heir's date (not valid, already past) and
  the amount.
- **The shares said once.** When the "What changes" box has the "Real
  shares" row, its note has the total ("The percentages you wrote add up
  to 130%: the will always spends the whole balance, so the shares are
  recalculated in proportion.") and the separate "How the shares are
  divided" box and the grey line are not drawn; the calm box stays for
  when there is no such row (a first will, the fixed-amount texts).
- **The grey line always there when needed.** When the written
  percentages do not add up to 100 and nothing explained it in this Check
  (the owner's rename-only Check at 130%), the grey "Shares recalculated
  in proportion: …" line is added at the end of the Check, from the Heirs
  tab; it is drawn in the box area, where the Heirs box would be, also
  when the window has no Heirs box.

- **Rename back (A -> B -> A) recognised (owner's ZIP test).** On his
  wallet (104 items) the rename back was not taken for a pure rename: the
  will was rebuilt a day earlier and the password asked. The pure-rename
  rule (`Will.sync_heir_names`) decided for all items at once: one item it
  could not match stopped every rename; amounts were compared as text.
  Now it decides item by item (`_pure_rename`), on the VALID items and on
  the will in force kept as REPLACED, compares addresses with
  `address_key` and amounts by value ("20%" = "20.0%"), and logs why a
  name is not a pure rename. The "Edit heir" dialog keeps the stored
  amount and date as they were when only the name changed.

- **An anticipation nobody needs is undone (owner's ZIP test).** A rebuild
  against the will in force is dated one day earlier and stores that day
  (#128); after Cancel and once the change was undone, every Check asked to
  sign a will different from the will in force only by that day. Now the
  FIRST check of a Check (`engine.context.drop_anticipation_leftover`, via
  `check_will(undo_anticipation=True)`, never the check after its own
  build) drops the drafts and puts the stored date (and the heirs' date)
  back to the will in force's when: a will is in force, the stored date is
  exactly one day before it, all drafts carry that day, and the settings
  (Heirs tab, will-executors selected now, fee) differ from the will in
  force only by that day. The will in force is then brought back (#128),
  nothing to sign. Signed or sent items are never touched; any other date
  or change is kept and asked to sign. This also covers the "fee put back"
  case that #128 left out of scope (`test_owner_case` updated). The log
  says each "not a pure rename" reason once per name and Check.
- Golden `check/owner_drafts_128`: the fee-put-back Check now keeps the
  will in force (no draft at 02-26, no password).

**Golden files changed by the follow-up (on top of the 17 above):**
`check/cancel_at_invalidate`, `check/invalidation_not_seen_yet` (the new
"moved later" / "Interrupted" wording); `check/cancel_at_sign`,
`check/phase2_error` ("Your will now" adds "Until you sign and send it, the
will sent before stays in force: delivery on 23 September 2032": the copies
of that will are still VALID, so the old rule did not see them; now true).

**Golden files changed by the invalidation step:** `check/cancel_at_invalidate`
(the owner now cancels at the step: "button: Invalidate now" instead of
the password; the box stays after the Cancel, so `invalidation` is in the
snapshot), `check/check_alive_renew`, `check/check_alive_renew_approved`,
`check/date_later_advanced`, `check/date_later_sent`,
`check/invalidation_not_seen_yet`, `copies/prepare_check_alive_renew`
(the new step: the event "invalidation-buttons" and the question
"button: Invalidate now" before the password). Nothing else in them.

**Golden files changed by the calm shares note:**
`check/percentages_not_100`, `copies/cli_invalid_heir_in_wallet` (first
wills with percentages not adding up to 100: the "Attention" line leaves
the warnings box; the snapshot has `amounts_note` with the calm text,
shown as a box). 24 golden files differ from HEAD in this step.

**Verification:** `ruff check bal/ tests/`: all checks passed. Whole
suite (normal order, after the invalidation step): 2184 passed (Electrum
4.8.2). After the last two fixes (deselected leftovers, one box), owner's
request: only the affected tests (`test_deselected_leftovers`,
`test_heir_rename_once`, `test_in_force_one_rule`, `test_will_changes`,
`test_build_report`, `test_verify_in_dialog`, `test_invalidation_preview`,
`test_check_button`, `test_cancelled_postpone`, the GUI flow tests: all
passed) and all golden tests (247 passed, none changed by these two
fixes, except the 2 of the calm shares note; 24 golden files changed in this step, listed above). Catalogs: 790
entries (732 before): 65 new msgids, all translated in Italian and Spanish
("{} sat" and "{} sat/vB" the same on purpose), none fuzzy, the 7
replaced msgids removed, no obsolete entry added. Test ZIP
`safe21-heartbeat_0.7.0-TEST-135-what-changes.zip` (sha256
`55a42f1f42e017fdd2953536cda27f5f5e49fe85d0c2f1beb4f22887b72b9725`):
external load and smoke test OK on 4.8.2.
Before the commit, whole suite in both orders: the first run had 1
failure, `test_gui_engine_host` (it expected `check_will` called without
the new `undo_anticipation` flag); the test was updated and
`test_check_will_passes_undo_anticipation` added. Second run: 2210 passed
in the normal order and 2210 passed in the reverse order (Electrum
4.8.2), ruff clean.

**Outcome:** owner's ZIP test OK (2026-10-14), on a test wallet:
- the single "What changes" box with the rename row;
- no signing for deselected executors' leftovers;
- "Show the transaction" opens in front;
- the edit hints and the right-click → Edit;
- rename A→B→A gives one heir and no signing;
- a duplicate name or address is refused;
- the invalid address message is translated;
- the shares are explained once, with the quiet grey line;
- the anticipation leftover drafts are dropped;
- the wizard rename row.

## 136. Will tab List: compact transaction rows, one line each

**Date:** 2026-10-14

**Problem (the owner):** in the Will tab's List, the transaction rows under
an open version ran off the right edge of the window: the delivery as
"2032-12-27T01:00:00+00:00" in bold, the full 64-character txid, the
will-executor URL with its full fee address on a second line, and the fee
with its fiat value ("0.15627 mBTC (13.01 USD)"). With many will-executors
the two-line rows were messy and harder to select.

**What changed (look only: no stored data, protocol or command-line
output changes):**

- `bal/gui/qt/lists.py`, transaction rows (`PreviewList._variant_row`),
  ONE line each, columns Locktime / Txid / Will-Executor / Fee address /
  Executor fee / Status:
  - the delivery in the short form of the one date style, as the version
    rows ("27 dic 2032, ore 01:00 UTC"), not bold;
  - the txid shortened in the middle, 8 + 8 characters
    ("9c0c1353…7f94553b");
  - the will-executor: its host only ("we.bitcoin-after.life");
  - the fee address in a new column "Fee address" ("Indirizzo
    commissione"), shortened the same way;
  - the will-executor's fee in the wallet's unit only (no fiat).
  Each cell's tooltip has the full value (the delivery in the long form
  and as the exact timestamp; the full txid; the full URL; the full
  address; the fee with its fiat value). The right-click menu of a
  transaction row has "Copy" > each column, which copies the FULL values
  (`ROLE_CLIPBOARD_DATA`), never the shortened text; "copy id" copies the
  full txid as before. The search box still looks in the full values. The
  List has no sorting (unchanged); opening a transaction still shows all.
  The two-line cell helper (`_TallCell`, #129) is gone. Each column ends
  14 px after its widest text (`_GapDelegate`: Electrum's item delegate,
  wider by the gap; the owner found the one-line columns too close), so
  the columns breathe and Status still fits at 1150 px.
- The invalidation transaction row: its txid shortened the same way; the
  full txid in the tooltip and in the search; "copy id" copies it whole.
- The version rows already used the short date style (#134): unchanged.
- Catalogs: one new text, "Fee address" (IT "Indirizzo commissione", ES
  "Dirección de la comisión").

**Tests:** `tests/test_gui_will_versions_list.py` (real `PreviewList`):
the row shows the short date (not bold), the shortened txid, the host, the
shortened fee address and the fee without fiat, with no line break in any
cell; every transaction row is one text line high, at 1150 px the Status
column is in view and each column is at least 14 px wider than its widest
text; the tooltips and "Copy" give the full values;
the invalidation row's txid is short, whole in the tooltip and the search;
the transaction-row menu has "Copy" before "copy id".

**Verification:** `ruff check bal/ tests/` clean. First part: the whole
suite 2212 passed in both orders. One-line follow-up (owner: no whole
suite): the affected tests (`test_gui_will_versions_list`,
`test_gui_will_flows`, `test_gui_future_view`, `test_protected_executor`,
`test_translations`, `test_i18n`, `test_will_terms`,
`test_inheritance_report`: 264 passed; after the column spacing, the List
tests again: 236 passed) and the golden files (247 passed, unchanged). Catalogs 791 entries, RESULT OK. Test ZIP
`safe21-heartbeat_0.7.0-TEST-136-compact-list.zip` (sha256
`cdc10539d498ace91700e4379aece2ce66c1709f2dcc0fa015a7eaad43d09bc0`):
external load and smoke test OK on 4.8.2.

**Outcome:** the owner checked the test ZIP in Electrum: OK (2026-10-14).
For a look-only follow-up he did not want the whole suite run again
before the commit.

**Follow-up (test only, after the merge):** the whole suite, run for
#137, found `tests/test_status_column.py::test_no_server_column_any_more`
still expecting the five columns before #136. It now expects
`FEE_ADDRESS` too. No plugin code changed. Lesson (owner OK,
2026-10-14): a look-only step also runs every test file that names the
touched class (here `PreviewList`).

## 137. Will-executor selection respects the user's choices

**Date:** 2026-10-14

**Problem (the owner):** the wizards' "download and select" (BASIC page 3,
and ADVANCED "Automatically download and select willexecutors") selected
EVERY will-executor that answered and deselected every one that did not.
A will-executor the owner had deselected on purpose (we.divinaly.support)
came back selected, and page 4 only said "added".

**What changed:**

- The rule (`Willexecutors.select_after_ping`, Qt-free), three cases:
  1. a will-executor NEW on the network (not in the list before this
     download) that answers: selected; page 4 says "added X (new in the
     network)";
  2. a will-executor deselected by the USER: stays deselected, even when
     it answers;
  3. one deselected by the PLUGIN (it did not answer, or Exclude):
     selected again when it answers, deselected when it does not.
  The same function serves BASIC and ADVANCED
  (`BalWindow.download_and_select_willexecutors`).
- New Electrum config key `bal_willexecutors_user_deselected`
  (`{chain: [url, ...]}`, the same global scope as `bal_willexecutors`):
  the will-executors the user deselected on purpose. A user choice, not
  derived state. `bal_willexecutors`, the wallet keys and the protocol are
  unchanged.
  - Seeding (owner decision A): the first time this version looks at a
    network (the wallet opens, a wizard, a download, Exclude, a selection),
    that network's entry is created from the will-executors deselected at
    that moment, i.e. before any new automatic deselection; from then on
    only the user's actions change it, so it is idempotent and a later
    plugin deselection is never added.
  - The user's actions (`Willexecutors.set_user_choice`): the
    Will-Executors window's select / deselect, "Select all", "Select only
    valid", "Deselect all", "Deselect invalid" (owner decision B), and the
    command line's select / deselect. Selecting cancels an earlier
    deselection. Exclude is the plugin's: not recorded.
  - The BASIC wizard's Cancel puts back the selected flags AND the user's
    deselections.
- The #134/#135 rules on deselected will-executors (not counted, never
  signed or sent, "· N deselected") are unchanged.
- Manual: the BASIC wizard's page 3 paragraph says the new rule.
- Catalogs: "(new in the network)" (IT "(nuovo nella rete)", ES "(nuevo
  en la red)").

**Tests:** `tests/test_executor_selection.py` (new): the core rule (cases
1/2/3 and mixed), the seeding (once, idempotent, user choices only); the
real BASIC wizard with three fake will-executors, each with its own fee
address, and a fourth new on the network: case 1 (selected, page 4 "new in
the network"), the owner's we.divinaly case (deselected in the real
Will-Executors window, stays off across downloads; selected again by him,
stays on), case 3 (down then back), Exclude then back, Select / Deselect
all, a mixed case, the wizard Cancel (flags and user list), the command
line, the ADVANCED page; after each scenario a Check, then a second Check
clean.

**Verification:** ruff clean; the affected tests (196 passed) and the
golden files (247 passed, unchanged); catalogs 792 entries, RESULT OK.
Test ZIP `safe21-heartbeat_0.7.0-TEST-137-executor-selection.zip` (sha256
`6664338fb89ff9775b122f2bf6f3b509da83a9c226a448908b7de6cfc56c00a8`):
external load and smoke test OK on 4.8.2. Whole suite in both orders,
after the owner's ZIP test: 2222 passed and 1 failed each time, in
`test_status_column::test_no_server_column_any_more`. That test was
left behind by #136; it was fixed in its own commit (038ac43, "#136
follow-up"), on which this step is based. The fixed file passes.

**Outcome:** owner's ZIP test OK (2026-10-14), on his wallet:
- a user-deselected will-executor stays off in the wizard;
- one selected again by him shows "added" without "(new in the network)";
- Cancel keeps his earlier choice;
- a second user deselection is respected;
- two Checks are clean.

## 138. Heir window: private notes, a clear address field, a QR button

**Date:** 2026-10-14

**Problem (the owner):** the New / Edit heir window had no place for the
owner's own notes on an heir (how to reach them, where the address comes
from). The Address field did not say what it wants, and an extended key
pasted there (an xpub, or worse an xprv) got only "not an address". There
was no way to read the heir's address from a QR code.

**What changed:**

- Notes (new, optional): a "Notes" box with its "?" help ("Private notes
  for you, kept only in this wallet; never sent to the will-executors or
  written on the blockchain."), at most 1000 characters (longer: "The
  notes are too long: at most {} characters." and the window stays open).
  - New wallet key `heir_notes` (`{heir name: text}`), registered like
    `will_settings` (`bal.core.heir_notes`, Qt-free). A user choice, not
    derived state. The `heirs` format (`[address, amount, locktime]`), the
    `will` / `will_settings` keys and the protocol are unchanged.
  - The notes follow the heir: rename (window), delete (window, Heirs
    list, command line `heirs_delete`), import (GUI and command line: the
    notes of the heirs still there are kept, the others dropped), the
    BASIC wizard's Cancel (the notes are put back with the heirs). When
    the wallet opens, a note whose heir is no longer stored is dropped: no
    orphans.
  - Shown as the tooltip of the heir's name in the Heirs list. Not in the
    will summary, the PDF, the export file or anything sent to the
    will-executors.
- Address field:
  - a grey line under it: "One single address of the heir (e.g. bc1q…),
    taken from Receive in the heir's wallet. Not an xpub/zpub.";
  - an extended public key (xpub, ypub, zpub, tpub, vpub...): "This is an
    extended public key (xpub/zpub), not an address: in the heir's wallet
    open Receive and copy one address.";
  - an extended private key (xprv, zprv...): "This is a PRIVATE key: never
    paste it anywhere. Use one address from Receive." The key is never put
    in the exception, the message or a log line (new
    `ExtendedPublicKeyNotAddress` / `ExtendedPrivateKeyNotAddress`, both
    `NotAnAddress`, through `heir_error_message`; the command line gives
    the same messages).
  - a camera button (Electrum's `add_qr_input_from_camera_button`): a
    `bitcoin:` QR code is read with `electrum.bip21.parse_bip21_URI` and
    only the address is kept (an amount in the code is ignored); a code
    without an address: "This QR code does not contain a bitcoin
    address.". Hidden in OP_RETURN mode, whose behaviour is unchanged
    (#139).
  - the text never runs under the camera button
    (`reserve_button_space`): Electrum's `OverlayControlMixin` only
    floats the button over the line and reserves no room (none of our
    style sheets touches this field), so the right text margin is set to
    the button width plus the frame, and back to 0 in OP_RETURN mode.
- The four existing "?" helps are unchanged.
- Manual: the [NEW HEIR] section explains the single address / xpub /
  xprv, the QR button and the notes.
- Catalogs: 7 new msgids, IT and ES.

**Tests:** `tests/test_heir_notes_and_address.py` (new, 11 tests, real
dialogs and the real window; 12 after the icon fix): a note saved, shown as tooltip, renamed,
deleted; the help text and the 1000-character cap; GUI import keeps and
drops; the BASIC wizard's Cancel restores the notes; an old wallet without
`heir_notes` opens; command line delete and import clean the notes; a new
heir with notes, then a Check and a second Check clean (three fake
will-executors, each with its own fee address); the xpub / zpub / xprv
messages (public BIP32 / BIP84 test vectors) and no xprv in the log; the
command line refusing an xprv without echo; the QR text with a plain
address, a URI with an amount and an invalid code; the camera button
shown, hidden in OP_RETURN mode; the right text margin at least the
button width with the button shown, 0 in OP_RETURN mode, back again for an
address (12 tests).

**Verification:** ruff clean; the affected tests (853 passed) and the
golden files (247 passed, unchanged); catalogs 799 entries, RESULT OK.
Test ZIP `safe21-heartbeat_0.7.0-TEST-138-heir-window.zip` (first sha256
`75de70c68d664d311415396b5ed90d3769f7c2c1c95c8500617c4d567c482ec8`):
external load and smoke test OK on 4.8.2. After the icon fix: ruff
clean; the affected tests (267 passed); golden files 247 passed,
unchanged; catalogs RESULT OK; ZIP rebuilt with the same name (sha256
`8fcf757a9f9aec2bb3b0ec30a6cefd30b56b68d23e341a77d37254fa4b3881f4`),
external load and smoke test OK on 4.8.2. Whole suite in both orders,
after the owner's final look (final ZIP below): 2235 passed each time;
ruff clean; golden files unchanged.

**Outcome:** owner's ZIP test OK for points 1-9 (2026-10-14): notes
saved, shown as tooltip, renamed, deleted; the 1000-character cap; the
xpub and PRIVATE-key messages; the grey line; the QR button; the camera
hidden in OP_RETURN mode; the wizard's Cancel; the notes after a restart;
two Checks clean. Two fixes from that test:
- the camera icon sat on the end of the address text (Windows, Italian):
  the field now reserves the button's room (see above);
- Italian: "nel portafogli dell'erede" became "nel portafoglio
  dell'erede" (the grey line and the xpub message); Spanish checked, no
  change.
- Italian (owner decision): one wallet is always "portafoglio". 32
  existing IT entries changed the singular "portafogli" (il, del, nel,
  questo, tuo, altro...) to "portafoglio"; the plural "tutti i portafogli"
  is kept; PLAN_I18N.md glossary updated. ZIP rebuilt with the same name
  (sha256 `185bac52b08b29d35b417ead38373f632c9c79c8adae47c47ee3ad525940e495`);
  catalogs RESULT OK, i18n tests and golden files (247) passed. The
  owner's final look at this ZIP: OK (2026-10-14).

## 139. Heir window: Percentage or Fixed amount buttons

**Date:** 2026-10-14

**Problem (the owner):** in the New / Edit heir window (also used by the
BASIC wizard and the Heirs list) the amount was one field with the help
"Fixed or Percentage amount if end with %": nothing on screen said whether
the number was a percentage or an amount, the "%" had to be typed by hand,
and the window did not explain how percentages and fixed amounts share the
wallet.

**What changed:**

- Two exclusive buttons next to the amount field: "% Percentage" and
  "Fixed amount". A new heir starts on Percentage; editing an heir
  preselects its stored mode ("30%": Percentage, anything else: Fixed
  amount).
- The field shows its unit at the right, in grey: "%" in Percentage (the
  user types only the number; the "%" is added when saving) or the
  wallet's base unit in Fixed amount (e.g. mBTC). The right text margin
  keeps the text out from under the unit (`PercAmountEdit.reserve_unit_space`,
  as #138's `reserve_button_space` for the address field). A "%" typed or
  pasted in Fixed amount switches to Percentage and is dropped; switching
  the mode keeps the number. The old placeholder of the empty field ("{}
  or perc value") is gone.
- A grey line under the field, following the mode:
  - Percentage: "A share of the wallet balance (e.g. 30). The heirs'
    percentages share, in proportion, what is left after the fixed
    amounts."
  - Fixed amount: "An exact amount in {unit}, paid first. If no heir has a
    percentage, what is left over is shared among the heirs in proportion:
    the wallet is always emptied completely."
  Checked against `Heirs.prepare_lists`, `fixed_percent_lists_amount` and
  `normalize_perc`: fixed amounts are taken first and exactly (more than
  the balance stops the build, #76); the percentages are weights sharing
  what is left after the fees and the fixed amounts; with no percentage
  heir the leftover is shared among the fixed heirs in proportion to their
  amounts; a sub-dust rounding rest goes to the fee (no change output).
- The "?" help of the amount says the same rules in one text (it replaces
  "Fixed or Percentage amount if end with %").
- OP_RETURN mode: the buttons, the unit and the grey line are hidden with
  the amount, as before; they come back for an address.
- Unchanged: the stored format ("30%" or integer satoshis through
  `Util.encode_amount`), the computation, the protocol, the command line.
  The #135 edit hint compares the amount as the window saves it
  (`PercAmountEdit.amount_text`: the number plus "%" in Percentage), so an
  heir opened and saved untouched, or only renamed, keeps "Only the name
  changes" and its stored amount. Old heirs open as before.
- Manual: the [NEW HEIR] section explains the two modes.
- After the owner's ZIP test of the OP_RETURN mode (A-D):
  - A (an old bug, any heir): OK with an empty Name put the key "" in the
    heirs and failed when saving with Electrum's raw `StoredDict`
    assertion ("key='', parent={...}"); the "" heir stayed in memory, so
    the next OK said "An heir named  already exists.". Now a name empty
    after strip gets "Write a name for the heir." and the window stays
    open with nothing stored. The core refuses it too
    (`Heirs.validate_heir` raises the new `HeirNameNotValid` before
    anything is put in the dict; `heir_error_message` and the command
    line's `_user_facing` give the same sentence), so the GUI, the command
    line and the imports are covered. Names are not stripped when saved:
    an existing name with spaces still matches.
  - B: the amount's "?" is hidden with the amount in OP_RETURN mode.
  - C: #138's grey address line is hidden in OP_RETURN mode.
  - D: the window fits its rows again when the mode changes
    (`adjustSize`, queued after the layout update), so no empty band is
    left between the address and the message rows.
  All come back for an address.
- Catalogs: 6 new msgids, IT and ES; the 2 replaced ones are kept as
  obsolete entries.

**Tests:** `tests/test_amount_mode.py` (new, 14 tests, real dialogs and the
real window, three fake will-executors each with its own fee address): the
two exclusive buttons; a new heir on Percentage; the stored mode
preselected for "40%" and for a fixed amount (satoshis shown in BTC); the
unit per mode (mBTC / %) and the right text margin holding it; switching
keeps the number; a "%" typed in Fixed amount switches to Percentage;
stored "10%" and satoshis; the grey line per mode; OP_RETURN hides the
buttons and the grey line and an address shows them again; an OP_RETURN
heir opens with them hidden; editing without changes keeps no hint, a
rename keeps "Only the name changes" and the stored amount (percentage and
fixed), a mode switch says the share changes; a new heir with a fixed
amount, then a Check and a second Check clean. After the owner's
OP_RETURN test: the amount's "?" and the grey address line hidden in
OP_RETURN mode and shown again; on screen, the message row right under the
address row (only the grid spacing) and the window shrinking and growing
with the mode; an empty and a blank name refused with the window kept
open, the heirs unchanged and no "" key, then a normal heir saved; the
core (`validate_heir`, `heir_error_message`) and the command line
(`heirs_add`) refusing an empty name and storing nothing.
`tests/test_gui_widgets.py` (a typed "50%" now shows "50" in Percentage)
and `tests/test_heir_notes_and_address.py` (the new "?" text; the first
three "?" compared as a set, the amount's is now made first) updated.

**Verification:** ruff clean; the new tests (10 passed); the affected
tests (610 passed); golden files and catalog tests (271 passed; golden
files unchanged); catalogs 802 entries, RESULT OK. Test ZIP
`safe21-heartbeat_0.7.0-TEST-139-amount-mode.zip` (sha256
`65508c15f0f1c34f667b08fd700c2f3b0e5c143d0d39dcf6bd80b6b086ddefa9`):
external load and smoke test OK on 4.8.2. After the A-D fixes: ruff
clean; the new tests (14 passed); the affected tests, now also every file
naming `validate_heir`, `heir_error_message`, `set_heir` or the heirs
command line (1024 passed); golden files, catalog tests and the new tests
(285 passed; golden files unchanged); catalogs 803 entries, RESULT OK;
offscreen screenshots of the address and OP_RETURN modes checked. ZIP
rebuilt with the same name (sha256
`ddb533682347607f0222af46362009cbd08a26303d1d43fe6c772e03138e6e60`),
external load and smoke test OK on 4.8.2. Whole suite in both orders,
after the owner's test of this ZIP: 2249 passed each time; ruff clean;
golden files unchanged.

**Outcome:** owner's ZIP test OK for points 1-10 (2026-10-14): the two
buttons and Percentage for a new heir; "30" saved as 30%; the unit and
the grey line in Fixed amount; the stored mode preselected on edit;
switching keeps the number and says the share changes; a "%" typed in
Fixed amount switches to Percentage; an heir opened and saved untouched
needs no signature (Check clean, "not needed"); the new "?" text;
OP_RETURN hides and shows them again; after a restart two Checks clean.
On the owner's MAINNET test wallet two percentages changed by hand were
put back to the values of the will in force: the next Check dropped the
unsigned draft and found the will in force (4 of 4 will-executors), the
second Check clean.
The owner's OP_RETURN test found A-D (an empty name failing with a raw
error; the amount's "?" and the grey address line still shown; an empty
band above the message), fixed here (see What changed).
The owner's test of the rebuilt ZIP (sha256
`ddb533682347607f0222af46362009cbd08a26303d1d43fe6c772e03138e6e60`), 2026-10-15:
an empty name says "Write a name for the heir." twice and stores nothing;
a named heir saved and still there after a restart; OP_RETURN mode
without the amount's "?", the grey address line and the gap; all back
for an address; two Checks clean. OK.

## 140. The will message: a button, 80 bytes, one per will

**Date:** 2026-10-15

**Problem (the owner):** a message in the will (an OP_RETURN output) could
only be written by typing `OP_RETURN:` in the Address field of the New heir
window.
- Its name had to be invented.
- Nothing counted the 80 bytes, and nothing said the message becomes public.
- Two messages gave two OP_RETURN outputs in one transaction, which nodes
  before Bitcoin Core 30 do not relay.
- The wizard's summary showed only "name (OP_RETURN)", and the PDF report
  did not show the message at all.

**What changed:**

- **A "✉ A message in the will" button.** It is on page 2 of the BASIC
  wizard and next to "👤 New Heir" in the Heirs tab (owner's choice A: in
  Advanced too).
  - The window asks only the text, in a box of two lines that shows all of
    it (one line of text: a line break becomes a space, Enter is left to
    the dialog).
  - The plugin names a new message "OP_RETURN message" (msgid; Italian
    "Messaggio OP_RETURN"). The stored name is plain: the ✉ is drawn only
    in the list.
  - If a payee already has that name, the message is "... 2" (owner's
    choice B).
  - A grey line says the name is only for the owner and is not written in
    the transaction. A message made before keeps its name.
  - Pressing the button again opens the same message. A double click or
    Edit on a message in the Heirs list opens this window too, not the heir
    window with the hexadecimal address (owner's ZIP test).
- **80 bytes.**
  - The counter "74 / 80 bytes" counts bytes, not letters: "è" is 2, an
    emoji 4.
  - A longer text is cut, never inside a character (`fit_message`,
    `message_size`).
  - The same counter is used in the Advanced `OP_RETURN:` mode (three lines
    there).
- **A fixed warning** (the wizard's yellow note): "The message will be
  public forever on the blockchain, and the will-executors can read it
  before the delivery. Do not write private data."
- **A "?" of its own**, one sentence per line (owner's ZIP test): what the
  message is, when it is written, where to read it (a block explorer), the
  byte count, one message per will, and the owner's two examples.
- **One message per will.** A second message is refused with "There is
  already a message in the will: edit that one."
  - It is refused in the windows, by the command line (`heirs_add`,
    `heirs_update`) and in the imports (GUI and command line: the next
    messages of the file are skipped and said so).
  - A message being edited may always change its text
    (`check_one_message`, `keep_one_message`, new `SecondMessageNotAllowed`).
  - An old wallet with two or more messages is not changed: a note in the
    Heirs tab and on wizard page 2 asks to delete the others.
- **The message is not an heir.** With only a message, the wizard's Next
  stays off and "Add at least one heir." stays shown.
- **Heirs list.**
  - The message is shown as "✉ name", with no "0.00000000" amount and its
    whole text as the tooltip.
  - The menu now uses the stored name (key role) for Edit/Delete, not the
    shown text.
- **Wizard page 4.**
  - The message leaves the "Heirs" row and gets a "Message" row with the
    text in quotes.
  - In the "What changes" box the message texts are now escaped (rich
    text).
- **Edit hint.** A new text says "The message changes: the will must be
  signed again."
- **PDF report.** A "MESSAGE IN THE WILL" section under the heirs, with the
  text in quotes and when it becomes public (`WillReport.messages`).
- **"Bitcoin address" (owner).** The Heirs list column and the heir
  window's label used to say "Address", which could be read as a home
  address. Italian "Indirizzo Bitcoin", Spanish "Dirección Bitcoin". The
  New Heir button gets "👤" (owner).
- **Unchanged:**
  - the stored format of a message (`OP_RETURN:<hex>`, amount "0");
  - the transaction (one OP_RETURN output);
  - the protocol and the wallet keys.
- **Manual and catalogs.**
  - Manual: a new section, "A message in the will (OP_RETURN)"; the wizard
    pages, the PDF report and the "Bitcoin address" field.
  - IT/ES catalogs: 16 new msgids.

**Tests:**
- `tests/test_will_message.py` (new, 21 tests). It uses real dialogs and
  the real window, with three fake will-executors, each with its own fee
  address. It covers:
  - bytes, the cut and the owner's examples (74 and 65 bytes);
  - the automatic name, the one-message rule and the import rule;
  - the counter, the two-line box and the "?" lines;
  - a new message, then a Check and a second Check clean, with every new
    will transaction carrying exactly ONE OP_RETURN output with the text;
  - the button opening the same message, an empty text and a taken name;
  - an old wallet with two messages, and the double click;
  - the Advanced mode (counter, warning, "?", automatic name, second
    message refused);
  - the list (✉, no amount, tooltip, Delete by name, "Bitcoin address",
    the two buttons);
  - the wizard (button, Next off with only a message, page 4 row, escaped
    "What changes" box);
  - the command line and the GUI import;
  - the PDF.
- Updated:
  - `tests/test_amount_mode.py`: the message window has no amount; the
    window fits its rows (the warning makes it no longer shorter);
  - `tests/test_heir_notes_and_address.py`: the message "?" is made first,
    and the notes box is found among two multi-line boxes.

**Verification:**
- ruff clean.
- Affected tests: 648 passed.
- Catalogs: RESULT OK; i18n tests passed.
- Test ZIP `safe21-heartbeat_0.7.0-TEST-140-will-message.zip`: external load
  and smoke test OK on 4.8.2.
- Whole suite in both orders: 2270 passed each time; golden files unchanged.

**Outcome:** the owner's ZIP test (2026-10-15), points 1-8: OK.

Fixes from that test:
- the message box on two lines;
- a double click or Edit on a message opens the ✉ window;
- the tooltip in the list;
- the "?" one sentence per line;
- "Indirizzo Bitcoin" (capital B, as elsewhere in the Italian catalog);
- "👤 Nuovo erede".

The owner retested them: OK.

His point 7 (a Check asking to sign with "No change") was not caused by the
message:
- `we.bitcoin-after.life` answered with a new fee address (will-executors
  derive it from an xpub and change it about monthly), so its copy had to
  be rebuilt;
- that copy had never been verified ("3 of 4");
- the misleading "No change" box and the address rotation are the next
  step (#141).

## 141. A will-executor's new fee address: a received copy stays valid

**Date:** 2026-10-15

**Problem (the owner's ZIP test of #140, point 7):** a Check asked to sign
again, and the "What changes" box said "No change".
- `we.bitcoin-after.life` had answered `/info` with another fee address.
- The plugin keeps a copy only when the URL, fee address and fee are the
  same as now (`Util.cmp_willexecutor`).
- So a copy was rebuilt and signed again after every change of address, even
  when the will-executor had already received it.

**What the will-executors do (bal-server read on the BAL Gitea, 2026-10-15,
current `main` and tag v0.2.2):**
- with an xpub, `/info` gives one address per user IP, so the address
  changes when the user's IP changes;
- at the push the server accepts a transaction that pays ANY address it ever
  derived, with at least its fixed fee;
- bal-pusher broadcasts the stored transaction as it is and does not check
  again.

**What changed:**

- **A received copy stays valid** (`Will.received`: VALID and PUSHED, i.e.
  the push was accepted or the verification found it).
  - In `check_willexecutors_and_heirs`, a received copy whose will-executor
    now gives another fee address or fee counts as present
    (`Will.terms_change`).
  - Nothing is signed or sent for it, and the next Check is clean.
- **A rebuild keeps the received copies** when nothing else changed for
  them (`engine.build._transactions`).
  - Those will-executors are built with the payment data of their received
    copy (`Will.received_terms`, new `pinned` argument of
    `Heirs.get_transactions` / `buildTransactions`).
  - This is kept only when it gives the same coins and outputs as the
    received copy (`Will.same_payments`).
  - Otherwise (heirs, miner fee, new coins) the build runs again with the
    will-executor's new data.
- **A copy not received yet is redone, and the Check says why.**
  - New exception `WillExecutorTermsChanged` (a `WillExecutorNotPresent`,
    with `change` "address" or "fee").
  - New sentence `messages.executor_terms_message`. The owner's text:
    "we.bitcoin-after.life has a new Bitcoin address to receive its fee. Its
    copy of the will is not yet confirmed as received: it must be redone
    with the new address and signed again." There is a variant for a new
    fee.
- **The old copy that is not received is no longer sent** (owner: "un punto
  importante").
  - `Will.replace_unreceived_copies` (called by `update_will`) marks it
    REPLACED when all of these hold:
    - a new copy for the same will-executor;
    - same coins and same date;
    - another fee address or fee;
    - the old copy never received.
  - Before, both stayed VALID (same date: `check_anticipate` sees the same
    heirs) and the old copy, with the old address, was sent too.
  - A received copy is never touched.
- **The "What changes" box** no longer says "No change" in that case.
  - It has a new row "Will-executor fee" (`EXECUTOR_TERMS`, from the copies'
    payment data, `WillSide.terms`).
  - For a copy not received, the row gives the sentence above. For a
    received copy that changes anyway, it reads "... has a new Bitcoin
    address to receive its fee: the new copy uses it."
  - A REPLACED copy is left out of the comparison.
- **Unchanged:** the protocol, the wallet keys, `Util.cmp_willexecutor`.
- **Manual and catalogs.**
  - Manual: "When the fee address changes" in the will-executor list
    section.
  - IT/ES catalogs: 5 new msgids. Italian: the owner's sentence.

**Tests:**
- `tests/test_executor_fee_address.py` (new, 10 tests). It uses three fake
  will-executors, each with its own fee address, and covers:
  - a received copy after a new address and after a new fee: no password,
    nothing sent, two clean Checks;
  - a copy not received with a new address:
    - only that copy is redone, with the new address;
    - the old copy is REPLACED and not sent;
    - the sentence is in the Check and in the box;
    - the next Check is clean;
  - a received copy kept with its old address while another is redone;
  - an heir added: the received copy is rebuilt with the new address;
  - `terms_change`, the messages and the box rows.
- Golden file updated: `tests/golden/5e/copies/executor_fee_raised_after_sent.json`.
  - The scenario: will-executor we1 raises its fee over the maximum after it
    received the will.
  - Before: "not covered by the current will", a rebuild, "Will-Executor
    excluded: Skipped".
  - Now: the Check is OK with nothing to do. we1 accepted the copy with the
    old fee and broadcasts it as it is, so the copy stays valid (the rule
    of this step).
- `tests/test_5e_divergences.py`: D13 (an executor valid by address but
  unusable for the build) read that golden file.
  - It is now "not reproduced": the scenario no longer reaches the build.
  - `test_d13` asserts the new outcome.

**Verification:**
- ruff clean.
- Affected tests: 172 passed; the new file: 10 passed.
- Catalogs: RESULT OK; i18n tests passed.
- Test ZIP `safe21-heartbeat_0.7.0-TEST-141-fee-address.zip`: external load
  and smoke test OK on 4.8.2.
- Whole suite in both orders: 2280 passed each time.

**Outcome:** the owner's ZIP test (2026-10-15) on his wallet: OK.
- Points 1-3: a received copy whose will-executor gets a new address is
  kept, with no signature and nothing sent.
- The log showed the rule for copies that change anyway: after an heir
  change, the copy of `we.bitcoin-after.life` was rebuilt with its new
  address.
- Point 4 (a copy not received) is covered by the tests: on the real
  wallet the will-executor's real address was back (Ping) before Prepare.

Found in that test (next step #142, known divergence D14): Prepare in the
WILL tab does not move the stored delivery date to the anticipated will.
- Then its own check showed "Error: <heir> / Please, check your heirs,
  locktime and threshold!".
- The next Check took it for a postpone and offered a paid invalidation.
  The owner set the date by hand instead.

## 142. Prepare moves the stored delivery date with the rebuilt will

**Date:** 2026-10-15

**Problem (the owner's ZIP test of #141, on his wallet):**
- He changed an heir's percentage, then pressed Prepare and Sign in the WILL
  tab, then Check.
- The rebuild spends the coins of the will in force with other amounts, so
  the new will is dated one day EARLIER (the anticipation rule).
- The Check, the command line and the automatic rebuild move the stored
  date with it (`build.sync_delivery_date`). Prepare did not (divergence
  D14).
- So, right after the build, Prepare's own check found every heir at the old
  date and showed "Error: erede test / Please, check your heirs, locktime
  and threshold!".
- The next Check read the old stored date as a POSTPONE of a signed will and
  offered a paid on-chain invalidation. Nobody had received that will. The
  owner cancelled and set the date by hand.
- In the offline world of the tests the invalidation was really broadcast.

The Check builds the same will and its own check after the build fails the
same way, but it treats that failure as "the will must be signed" (bugs
E/F/K) and then moves the stored date. Prepare treated it as an error.

**What changed:**

- **Prepare moves the stored date** (`BalWindow._sync_prepared_date`) right
  after the build:
  - it uses the same rule as the others: the date only moves EARLIER, and
    only when it is a fixed date;
  - the toolbar date fields show the new date (`update_setting_widgets`);
  - when the date moved, it then reads the delivery state again
    (`init_class_variables`), so the heirs carry the new date.
  - The check that follows now really finds the will coherent (it is not
    hidden as in the Check), and Prepare says "Your will has been rebuilt
    and now needs to be signed...".
- **Prepare's error popup gives the cause** in the Check's words
  (`will_change_message`) above "Please, check your heirs, locktime and
  threshold!", instead of "Error:<heir name>" or "Error:WillException".
- **Manual:** the Prepare command says the date moves with an anticipated
  will, as with Check.
- **Unchanged:** the Check, the command line, the automatic rebuild, the
  protocol. No new msgid.

**Tests:**
- `tests/test_prepare_date_sync.py` (new, 5 tests). It uses three fake
  will-executors, each with its own fee address, and covers:
  - the owner's case in Advanced and in Basic: no error popup, the stored
    date moves one day earlier, no invalidation, the will is sent, the next
    Check is clean;
  - new coins (the D14 scenario);
  - a date the owner moved earlier is kept;
  - the error popup with the cause.
  - Without the fix, 4 of the 5 fail; the fifth is the guard.
- Golden files updated (`tests/golden/5e/copies/`):
  - `prepare_fee_changed` and `prepare_new_coin`: the "rebuilt, to be
    signed" message instead of the error, and the stored date one day
    earlier;
  - `prepare_fixed_amount_too_big`: "The will contains no valid
    transaction: a new will must be prepared." instead of
    "Error:WillException".
- `tests/test_5e_divergences.py`:
  - D11 and D14 converged (Prepare now behaves as the Check);
  - D9 reads the new error text.

**Verification:**
- ruff clean.
- Affected tests: 249 passed.
- Test ZIP `safe21-heartbeat_0.7.0-TEST-142-prepare-date.zip`: external
  load and smoke test OK on 4.8.2.
- Whole suite in both orders: 2285 passed each time.

**Outcome:** the owner's ZIP test (2026-10-15), on his wallet, was OK:
- after a new percentage, Prepare gave no error popup and moved the date to
  25 December 2032;
- Sign, then Check: no invalidation; sent and verified 4 of 4;
- a second Check was clean.

## 143. No ERROR when the Check's worker thread cleans the wallet history

**Date:** 2026-10-15

**Problem (the owner's logs, 2026-10-14 and 2026-10-15):**
- At every rebuild done by the Check, the plugin log showed
  "ERROR | save_db failed after history purge: daemon thread cannot write db".
- Before a rebuild the plugin removes the old unsigned will transactions
  from the wallet's local history (`Will.remove_stale_wallet_history`) and
  then writes the wallet file.
- The Check runs on Electrum's `TaskThread` (a QThread), which Python
  reports as a daemon thread, and Electrum refuses to write the wallet file
  from a daemon thread (`JsonDB.write`).
- Nothing was lost: the change stays in the wallet's memory and Electrum
  writes it at its next save, at the latest when the wallet is closed. But
  a false ERROR hides the real ones.

**What changed:**
- New `Will._save_wallet_db(wallet, after)`, used by the two saves after a
  change of the local history (the purge and the history update):
  - on a daemon thread it does not write and logs an INFO line ("wallet file
    not written after history purge (worker thread): the change stays in
    memory and Electrum writes it at its next save");
  - on the main thread it writes as before; a real failure is still an
    ERROR.
- **Unchanged:** building, signing, sending, dates, the protocol, the texts
  (no new msgid).

**Tests:**
- `tests/test_history_save_thread.py` (new, 6 tests): the purge and the
  history update on a daemon thread (no ERROR, no write, the INFO line), on
  the main thread and on a non-daemon thread (written), a real write failure
  (still an ERROR), and a real QThread (seen as a daemon thread, like the
  Check's).
- Without the fix, 4 of the 6 fail with the owner's exact ERROR.

**Verification:**
- ruff clean.
- Affected tests: 58 passed.
- Test ZIP `safe21-heartbeat_0.7.0-TEST-143-save-db-thread.zip`: external
  load and smoke test OK on 4.8.2.
- Whole suite in both orders: 2291 passed each time.

**Outcome:** the owner's ZIP test (2026-10-15), on his wallet, was OK:
- a Check that rebuilt the will, then Sign and send: verified 4 of 4;
- a second Check was clean;
- the plugin log has no "save_db failed" line, only the new INFO line.

## 144. "Bitcoin address" with a capital B in the QR message

**Date:** 2026-10-15

**Problem:** the message shown when the camera of the heir window reads a
QR code with no address said "This QR code does not contain a bitcoin
address." (Italian "indirizzo bitcoin", Spanish "dirección bitcoin"). Every
other text of the plugin writes "Bitcoin address" / "indirizzo Bitcoin" /
"dirección Bitcoin".

**What changed:**
- `bal/gui/qt/window.py`: "This QR code does not contain a Bitcoin
  address."
- Catalogs refreshed: Italian "Questo codice QR non contiene un indirizzo
  Bitcoin.", Spanish "Este código QR no contiene una dirección Bitcoin.".
- The refresh also marks as obsolete the old Prepare popup text
  "Error:{}\n {}", no longer in the code since #142 (that step added no
  msgid and did not refresh the catalogs).
- Text only: no logic changed.

**Tests:** `tests/test_heir_notes_and_address.py` reads the new text.

**Verification (text-only step):**
- ruff clean.
- Catalog check: 1 new msgid translated in IT and ES, 2 expected removals,
  the template equals a fresh extraction; `test_translations` and
  `test_i18n` pass.
- Affected tests, goldens and divergence list: 166 passed; `tests/golden`
  unchanged.

## 145. The PDF report on paper: dates, heir addresses with a QR code, BTC

**Date:** 2026-10-15

**Problem (the owner, looking at the printed report):**
- The PDF still wrote its dates in three old styles ("2026-10-15 14:30",
  "24/12/2032 01:00 UTC"), while the rest of the plugin uses one style since
  #134 ("24 December 2032, 01:00 UTC").
- The heir's address was small and thin (Courier 8, grey): on paper it
  could fade with time. Addresses differ a lot in length (26 to 62
  characters), and there was no QR code to read them.
- The line between two heirs was barely visible.
- The amounts followed the wallet's unit of that day (BTC, mBTC, bits or
  sat): the printed plan is read by heirs and notaries, maybe years later.
- The message section did not say where the message is in the transaction,
  nor give the warning of the message window.

**What changed (`bal/gui/qt/report_pages.py`, `report_actions.py`):**
- **Dates** in the one style (`bal.core.datetext`): the delivery, the Check
  Alive, the time line (as the Future view of the Will tab), the technical
  page, the deliveries of a will in stages. The short form ("24 Dec 2032,
  01:00 UTC") only where the long one does not fit (`date_fit`); the day
  alone in the narrow columns of the grid. "Created on" is the local time
  of the computer that made the report ("15 October 2026, 14:30").
- **Heirs table:**
  - the first column is "Name and Bitcoin address";
  - the address under the name in a larger monospaced font (10 points),
    black, in groups of 4 characters (`address_groups`), wrapped before
    the share column; one sentence above the table says to copy the
    addresses without the spaces and that the QR code holds the same
    address;
  - a 15 mm QR code of the address (`qr_modules`, `paint_qr`: vector
    squares, sharp on any printer; the `qrcode` package of Electrum) right
    of the amount, from the top of the row;
  - the share and amount columns as wide as their values, so a usual
    address (42 characters) stays on one line; "Amount" over the start of
    the amounts;
  - a visible light line between two heirs; "Total to the heirs" in the
    block of the last heir, so it never opens a page alone.
- **Amounts always in BTC** with 8 decimals ("0.00496444 BTC",
  `report_actions.btc_amount`): the heirs, the total, the will-executors'
  fees. The miner fee stays in sat/vB (a rate). The rest of the plugin
  keeps the wallet's unit.
- **Message:** the heading is "MESSAGE IN THE WILL (OP_RETURN)" ("OP_RETURN"
  never translated); under the text, the warning of the message window
  (public forever, read by the will-executors before the delivery, no
  private data) instead of the old grey line.
- Catalogs: 2 new msgids (IT/ES); the old grey line of the message is
  obsolete.

**Tests:**
- `tests/test_pdf_heirs_qr.py` (new, 8 tests, three fake will-executors and
  a 62-character Taproot address): the address groups; the grouped address
  and a QR code for every heir, inside the page and right of the amount; a
  usual address on one line also in BTC; the painted QR code read back
  module by module (the three finder squares the right way up); the light
  lines; the total with the last heir; the amounts in BTC whatever the
  wallet's unit; the dates in the one style.
- `tests/test_inheritance_report.py` and `tests/test_will_message.py` read
  the new texts ("How it works" now opens page 2 of the realistic will).

**Verification (look and text step, owner's previews):**
- ruff clean; catalog check OK; affected tests 249 passed; `tests/golden`
  unchanged.
- PNG previews in Italian of every page, before and after, reviewed by the
  owner step by step (QR size, address font and wrap, columns, BTC, column
  title).
- Whole suite in both orders: 2299 passed each time.

## 146. ADVANCED starts with a renewable period of 2 years and a Check Alive of 1 year

**Date:** 2026-10-15

**Decision (owner, after the Check Alive study):** BASIC has no Check Alive
and keeps a fixed date. In ADVANCED the default is the period form of 24
months, renewed by a Check Alive one year before the delivery: one renewal
(one miner fee) a year, 12 months to renew, heirs wait 12 to 24 months.
The Check Alive stays in days ("-365d"), the form BAL understands.

**What changed:**
- **A brand-new wallet** (no heirs, no will, nothing to migrate,
  `bal/core/delivery.py`): the Check Alive is `NEW_WALLET_THRESHOLD`
  ("-365d") instead of "-180d"; opened in ADVANCED without multiverse it
  starts in the period form of 24 months (`period_values`, the same date
  as the fixed default). In BASIC it stays a fixed date. Wallets that
  already had plugin data keep their migrated Check Alive (still "-180d"
  when nothing else is found).
- **The form follows the mode** (`bal/engine/context.py`, `follow_mode`,
  `initial_months`): switching to ADVANCED turns a fixed date into the
  period form (N = the whole months to the stored date, so the date stays
  about the same); switching to BASIC turns a period back into the fixed
  date. Only while the form is not locked: a will in force never changes.
  The same rule from the plugin Settings dialog (`common.switch_user_type`,
  every open wallet) and from the wizard's mode selector, so the order of
  the owner's steps does not matter. "Fixed date" can still be chosen.
- **Fix found in the owner's ZIP test** (`bal/gui/qt/window.py`): on a
  wallet never checked, `update_all` stopped on the missing
  `date_to_check`, so switching to ADVANCED left the Will tab in BASIC
  ("error while updating window" in the log) until a restart.
- **Will tab toolbar in ADVANCED** (owner): the fee sits next to the
  delivery date, the Check Alive has the second row to itself, and its
  sentence ("before the delivery (≈ 12 months) → date") is never cut any
  more (it read "prima della c 2027-10-16").
- The Check Alive "?" says "Default for a new wallet: 365 days (one
  year)". Catalogs: 1 msgid replaced (IT/ES).

**Tests:**
- `tests/test_advanced_period_default.py` (new, 13 tests, three fake
  will-executors): the new wallet in BASIC and in ADVANCED, multiverse,
  plugin data, the engine passing the mode; the wizard switch (period
  preselected, wrong phrase, a will in force, multiverse); the Settings
  switch on a wallet never checked (fails without the `update_all` fix)
  and with a will in force; the toolbar rows; a whole flow: built for the
  24-month date, no renewal for a year, then the renewal offered.
- Updated to the one-year default: `test_core_delivery_check_alive.py`,
  `test_core_delivery_settings.py`, `test_per_wallet_settings_flow.py`;
  `test_cli_autorebuild.py` chooses the fixed form it needs;
  `test_check_button.py` reads the new rows.

**Verification:** ruff clean; catalog check OK; owner's ZIP test (8
checks, then the toolbar rows) OK; whole suite in both orders: 2312
passed each time.

## 147. A will-executor that does not answer the check is no longer "not received"

**Date:** 2026-10-15

**Owner's case:** divinaly received the will (the sending answered "thx"),
but its check (`searchtx`) answered in 5 s and more. The check gave up
after 8 s, tried again from the start, gave up again, and the will read
"✖ not received": the copy was marked not sent (CHECK_FAIL, no PUSHED),
as if divinaly did not hold it.

**What changed:**
- **One attempt of 15 s** (`bal/core/willexecutors.py`):
  `CHECK_TIMEOUT` 15 and `CHECK_MAX_RETRIES` 0 instead of 8 and 1. The
  whole step still stops at 20 s (`NETWORK_DEADLINE`), so the window is
  not longer.
- **No answer is not "not received"**: when every attempt timed out,
  `Willexecutors.check_transaction` returns `Willexecutors.NO_ANSWER`
  (it returned `None`, read as "not held"); the global deadline gives the
  same. `WillItem.set_check_willexecutor` leaves the copy as it was: sent,
  to be verified, and the next Check asks again. An executor that answers
  without our transaction is still "not received", as before. The same in
  the Building Will dialog, the Check button and the command line.
- **The Building Will dialog** (`bal/gui/qt/dialogs.py`,
  `bal/gui/qt/widgets.py`, `bal/core/build_report.py`): new executor
  state `no_check_answer`, "⚠ received, no answer to the check (15 s)"
  in orange, counted as received in the Sending step; the Verification
  step is a warning, not an error. Catalogs: 2 msgids added (IT/ES).

**Tests:**
- `tests/test_check_no_answer.py` (new, 14 tests, three fake
  will-executors): one attempt of 15 s; an executor answering in 10 s
  (before: two attempts of 8 s cut, now verified); no answer at all; an
  answer that is not a dict; the item left sent; another transaction
  still not received; the dialog (15 s, 20 s, the counts, the warning);
  the Check button and the command line. 11 of them fail without the fix.
- `tests/test_verify_in_dialog.py`: a timeout now leaves the copy sent.

**Verification:** ruff clean; catalog check OK; owner's ZIP test with
divinaly: first Check "received, no answer to the check (15 s)", the
will in force, Sending 5/5; second Check 5/5 received and verified;
whole suite in both orders: 2326 passed each time.

## 148. The bal_* commands no longer change a wallet whose plugin window is open

**Date:** 2026-10-16

**Found in the analysis of 2026-10-15** (probe with the window harness and
three fake will-executors): each `bal_*` command builds a new controller
that reads the heirs and the will from the wallet file, while the plugin
window of an open wallet keeps its own copies and writes them back later.
An heir added from Electrum's Console vanished at the next heir edit in
the window; a will prepared from the Console was thrown away at wallet
close, where the close Check (on by default) rebuilt, signed and sent a
will built from the OLD heirs. The delivery date and fee were safe (one
dict shared with the window).

**What changed** (`bal/cli/commands.py`, the one entry point of every
command, `_call`):
- While the wallet's plugin window is open, the commands that change its
  heirs or its will (`_WALLET_WRITES`: add, update, delete and import
  heirs; prepare, rebuild, sign, broadcast, check with the executors,
  export, import/merge) stop with "This wallet is open in the Safe21
  Heartbeat window: make this change there, or close the wallet and use
  the command line." Nothing is changed.
- The will-executor list belongs to the plugin, not to one wallet: its
  changes (`_EXECUTOR_WRITES`: add, update, select, delete, ping,
  download, import) stop while any plugin window is open.
- Everything else runs as before: the commands that only read, the
  delivery date and fee (`delivery_*`), the plugin settings, and every
  command once the wallet is closed (the window stays listed with
  `disable_plugin` set) or without the GUI (daemon).
- The messages stay in English, like the whole command line (`babel.cfg`
  ignores `bal/cli`): no catalog change.

**Tests:** `tests/test_cli_window_guard.py` (new, 25 tests, three fake
will-executors): every wallet change refused with the wallet unchanged;
every will-executor change refused, also for another wallet's window; the
owner's case (an heir added in the Console); the reads and the fee still
allowed and changing nothing; after the wallet closes and without the GUI
every command runs; every command classified (a new command must join a
list).

**Verification:** ruff clean; owner's ZIP test in Electrum's Console with
the wallet open: `bal_heirs_list()` and `bal_will_status()` answered,
`bal_heirs_delete(...)` and `bal_willexecutors_ping()` refused with their
messages, Heirs and Will tabs unchanged; whole suite in both orders: 2351
passed each time.

## 149. The "Rebuild automatically on new transactions" setting is hidden and switched off

**Date:** 2026-10-16

**Why:** the analysis of 2026-10-15 found that this flow rebuilds from
Electrum's network thread through a timer and can sign and send an
on-chain invalidation by itself, while the owner's rule is that an
invalidation (a paid fee) is never made without the user. The flow did
not start in practice, but the setting offered it. "Rebuild will on
wallet close" (on by default) already brings new coins into the will.

**What changed** (`bal/gui/qt/plugin.py`):
- The plugin settings dialog no longer shows the row (label, checkbox,
  help and reset button), in BASIC and ADVANCED; "Reset to Default
  Setting" no longer touches it.
- New class switch `Plugin.GUI_AUTO_REBUILD_ENABLED = False`, checked
  first in `_wallet_activity`: wallet activity never schedules the
  automatic rebuild, even with `bal_auto_rebuild = True` stored by an
  older version. The key is kept, never rewritten.
- The flow (`BalWindow.schedule_auto_rebuild`, `maybe_auto_rebuild`) and
  the command-line `bal_will_autorebuild` are unchanged: the Phase 5e
  engine will make the flow safe.
- Catalogs: the two texts of the row became obsolete entries (`#~`) in
  `bal.po` (IT, ES) and left `bal.pot`; nothing else changed.

**Tests:** `tests/test_auto_rebuild_hidden.py` (new, 4 tests): no row in
BASIC and ADVANCED while the wallet-close row stays; Reset keeps a stored
value; the switch is off; a stored True schedules nothing for any open
window. `tests/test_auto_rebuild_on_new_tx.py`: the two wiring tests
switch the flow on for their plugin object.

**Verification:** ruff clean; affected tests (217) passed; owner's ZIP
test: the row is gone, the other rows unchanged; whole suite in both
orders run together with #150 (see #150).

## 150. "Rebuild will on wallet close" is ADVANCED only; in BASIC it is always on

**Date:** 2026-10-16

**Why (owner):** with the automatic rebuild switched off (#149), the close
Check is what brings new coins into the will. A BASIC user who switched it
off could leave new coins out of the will without noticing.

**What changed:**
- `bal/core/plugin_base.py`: new `BalPlugin.rebuild_on_close_active()`:
  always True in BASIC mode (also for a missing or unknown mode), the
  stored `bal_rebuild_on_close` value in ADVANCED. The stored value is
  never rewritten, so ADVANCED finds it as it was.
- `bal/gui/qt/window.py`: `BalWindow.on_close` runs the close wizard
  through `rebuild_on_close_active()` (the delivery hold of #123 still
  skips it).
- `bal/gui/qt/plugin.py`: the settings row (label, checkbox, help, reset
  button) is hidden in BASIC and shown in ADVANCED, also when the mode is
  switched in the open dialog.
- Texts unchanged; the catalogs only follow the new line numbers.

**Tests:** `tests/test_rebuild_on_close_setting.py`: the fake plugin uses
the real `BalPlugin` methods; new: in BASIC the wizard runs with the
setting on and off; `rebuild_on_close_active` per mode, the stored value
untouched. `tests/test_auto_rebuild_hidden.py`: the row is hidden in BASIC
and shown in ADVANCED. `tests/test_cli_window_guard.py`: the closed-wallet
test switches to ADVANCED to keep the close Check off.

**Verification:** ruff clean; affected tests (95) passed; owner's ZIP test
in BASIC: no "Rebuild..." row in the settings; whole suite in both orders
run before the push together with #149 and #151 (see #151).

## 151. Settings dialog: no "Panel editable Date and Fee", warning in ADVANCED only, User Type first

**Date:** 2026-10-16

**Why (owner):** the BASIC settings dialog should show only what a BASIC
user needs. "Panel editable Date and Fee" changed nothing any more: BASIC
does not show the date and fee fields in the tabs (#130) and ADVANCED
always let them be edited. The red warning only worried BASIC users, who
see a few safe settings. "User Type" decides what the rest of the dialog
shows, so it comes first.

**What changed:**
- `bal/gui/qt/plugin.py`: the "Panel editable Date and Fee" row (label,
  checkbox, help, reset button) is gone in both modes, and "Reset to
  Default Setting" no longer touches it. The red warning is hidden in
  BASIC and shown in ADVANCED (also when the mode is switched in the open
  dialog); its gap below is now the label's own margin, so a hidden
  warning leaves no empty space. "User Type" is the first row in both
  modes. "Maximum Will-Executor fee" stays visible in BASIC (owner: the
  user should always see it; the reset button restores it).
- `bal/gui/qt/widgets.py`: `WillSettingsWidget.apply_editable_dates`
  follows the mode only: outside the wizard the date and fee fields are
  editable in ADVANCED and read-only in BASIC. A stored
  `bal_editable_dates` value is kept but no longer read
  (`bal/core/plugin_base.py` comment).
- Catalogs: the two texts of the row became obsolete entries (`#~`) in
  `bal.po` (IT, ES) and left `bal.pot`; nothing else changed.

**Tests:** `tests/test_settings_basic_rows.py` (new, 5 tests): no row in
either mode; the warning in ADVANCED only; "User Type" first in both
modes; the fields follow the mode whatever value is stored (never
rewritten); the wizard fields untouched.
`tests/test_gui_delivery_colours.py`: "User Type" is now expected in row 0
(it pinned row 5).

**Verification:** ruff clean; affected tests passed; owner's ZIP test
(screenshots): BASIC without the warning and the row, ADVANCED with the
warning and without the row; whole suite in both orders, run before the
push for #149, #150 and #151 together: 2363 passed each time (a first run
found only the pinned row 5, fixed above).

## 152. One shared build of the invalidation transaction

**Date:** 2026-10-16

**Why (owner):** the same steps were copied in several places, so a later
change could be made in one copy and forgotten in the others. First piece
of the de-duplication: the build of the invalidation transaction.

**What changed:**
- `bal/engine/invalidate.py` (new): `build_invalidation(host, will=None)`
  calls `Will.invalidate_will` with the will's fee rate (`baltx_fees`,
  default 1), the History label setting and the earliest locktime of the
  will, with `date_to_check` as the fallback. Nothing is signed, labelled
  or broadcast there.
- The six copies use it: the Will tab's "Invalidate will" and the Will
  Details window, the automatic invalidation (`bal/gui/qt/window.py`), the
  command line's `bal_will_invalidate` (`bal/cli/controller.py`), the
  Check for a postponed will and for a will expired only against its
  Check Alive (`bal/gui/qt/dialogs.py`) and the Check Alive renewal
  (`bal/engine/check.py`). The Will tab now reads the values when the task
  runs on the worker thread, not just before; they are the same values.
- The History label "Safe21 Heartbeat invalidation" is written through the
  `INVALIDATION_LABEL` constant (`bal/core/will_versions.py`) in all four
  places that set it.
- Nothing changes for the user: same fee, same date, same label. Signing
  and broadcasting are unchanged (next step, #153).
- Catalogs: line numbers only.

**Tests:** `tests/test_engine_invalidate.py` (new, 10 tests): the values
passed to the core; the default fee and no reference date; an imported
will; nothing to invalidate and errors; the command line, the Will tab and
the automatic invalidation build through the engine and use the label
(the automatic one labels before broadcasting); no other module calls
`Will.invalidate_will` or writes the label text by hand.

**Verification:** ruff clean; the 70 test files that mention invalidation:
1443 passed; ZIP built, external zip load and smoke test OK on 4.8.2;
owner's ZIP test: "Invalidate will" from the Will tab menu (History label
present) and a Check after a postpone (asks to invalidate, then signs and
sends the new will). The whole suite runs before the push, with #153 and
#154.

## 153. One shared broadcast of the signed invalidation

**Date:** 2026-10-16

**Why (owner):** second piece of the de-duplication, after #152: sending
the signed invalidation was written twice, so a later change could be made
in one copy and forgotten in the other.

**What changed:**
- `bal/engine/invalidate.py`: `broadcast_invalidation(wallet, network, tx)`
  adds the wallet and network information to the signed transaction, sets
  the History label (`INVALIDATION_LABEL`) BEFORE broadcasting and
  broadcasts with a 120 s timeout (`BROADCAST_TIMEOUT`); it returns the
  txid. Errors propagate to the caller.
- The two copies use it: the Check's "Invalidate now"
  (`BalBuildWillDialog.loop_broadcast_invalidating`, `bal/gui/qt/dialogs.py`)
  and the automatic invalidation (`BalWindow._auto_invalidate_will`,
  `bal/gui/qt/window.py`).
- Signing stays with each caller (the automatic one only for a wallet
  without a password, the Check asks for it), and so do the messages: the
  Check still shows "Ok" or the broadcast error in its row.
- Nothing changes for the user. The fixed 10-second pause after the
  Check's invalidation is untouched (#154 shows it).
- Catalogs: line numbers only.

**Tests:** `tests/test_engine_invalidate_broadcast.py` (new, 8 tests): the
steps in their old order with the 120 s timeout; no txid, no label; a
broadcast error propagates after the label; the Check sends through the
engine and shows "Ok", or the error, in its row; a closing Check sends
nothing; the automatic invalidation sends through the engine; no other
module broadcasts a transaction or pulls its network information.

**Verification:** ruff clean; the 86 test files that mention invalidation
or broadcasting: 1800 passed; ZIP built, external zip load and smoke test
OK on 4.8.2; owner's ZIP test: a Check after a postpone, "Invalidate now"
(History label present, the new will in force). The automatic
invalidation is off since #149, so only the tests cover it. The whole
suite runs before the push, with #152 and #154.

## 154. One shared signing of the will transactions

**Date:** 2026-10-16

**Why (owner):** third piece of the de-duplication, after #152 and #153:
the steps that sign a will transaction were written twice (GUI and
command line), so a later change could be made in one copy and forgotten
in the other.

**What changed:**
- `bal/engine/sign.py` (new):
  - `sign_targets(willitems, txids=None)`: the transactions to sign, i.e.
    every valid one, or the valid ones among the txids asked for, in their
    order;
  - `sign_will_item(wallet, willitems, txid, password)`: signs one will
    transaction. A COMPLETE item is not signed. An input that spends a
    change of the same will gets that output's value, descriptor, address
    and script; the wallet signs (warnings ignored); COMPLETE and the
    signature count ("1/2" for a multisig) are set. It returns
    `(tx, newly_signed)` and does not replace the stored tx.
- The GUI (`BalWindow._prepare_and_sign_tx`, kept as the name its callers
  use, and `BalWindow.sign_transactions`) and the command line
  (`BalController.sign_transactions`, `bal_will_sign`) use them. Each
  keeps what it did around the signing: the GUI shows "signing: ..." and
  its callers store and save; the command line stores the signed tx,
  checks the signatures and saves the will and the History.
- The GUI copy wrote the input's value twice; once is enough (Electrum's
  `script_descriptor` setter does not touch it).
- Nothing changes for the user. Signing an invalidation stays with its two
  callers (#153).
- Catalogs: line numbers only.

**Tests:** `tests/test_engine_sign.py` (new, 10 tests): the choice of the
transactions; a COMPLETE item not signed; the inputs from the same will
prepared and the tx signed with the password; COMPLETE and the count; no
count, no crash; a wallet error propagates; the GUI and the command line
sign through the engine, and the command line still stores, checks and
saves; no GUI or command-line module prepares the inputs or signs by
itself (the two invalidation signings excepted).

**Verification:** ruff clean; the 95 test files that mention signing:
1888 passed; ZIP built, external zip load and smoke test OK on 4.8.2;
owner's ZIP test: an heir added, Prepare, Sign (the 5 items "signed, to
send"), Broadcast and Check (in force, 5 of 5 after a retry: one
will-executor answered 502 Bad Gateway, then "already present"); the heir
removed and a Check (asks the password, signs, sends: in force 5 of 5,
anticipated by one day, no invalidation). Whole suite in both orders,
run before the push for #152, #153 and #154 together: 2391 passed each
time, goldens unchanged.

Noted for later: `bal/core` fills inputs from a change output the same
way where the will is built and loaded (`heirs.py`,
`Will.add_info_from_will`, `Will.new_input`); not a signing, not touched.

## 155. One shared sending of the will to the will-executors

**Date:** 2026-10-16

**Why (owner):** fourth piece of the de-duplication, after #152-#154:
sending the will was written three times (the Will tab menu, the Check,
the command line), and the three copies did not behave the same. Owner
decisions 2026-10-16 (D1-D3, all as recommended):
- D1: a will-executor that does not answer before the 20 s deadline:
  its transactions are "not received" (PUSH_FAIL) and a warning says so,
  everywhere. Before, the menu said "All transactions are broadcasted"
  and the command line did not mark them.
- D2: the same rule everywhere for who receives the will: the selected
  will-executors whose current record is valid (a valid address, a fee
  between the dust limit and the maximum fee). The others are excluded
  and the reason is shown. Before, only the Check checked it, silently.
- D3: an error while checking an "already present" copy is logged, the
  copy counts as not verified and the next check goes on, everywhere.
  Before, only the command line went on.

**What changed:**
- `bal/engine/push.py` (new): `select_push` (who receives the will, and
  who is excluded and why) and `push_will` (send once in parallel with
  the global deadline, mark PUSHED / PUSH_FAIL, check the "already
  present" copies; returns a `PushResult` with each will-executor's
  outcome). Nothing is saved there.
- `BalWindow.push_transactions_to_willexecutors` (menu),
  `BalBuildWillDialog.loop_push` (Check) and
  `BalController.push_transactions_to_willexecutors` (command line) use
  it and keep their own display: the waiting window (now also "no answer
  in time" and "excluded: ..."), the Check's counter, rows and report
  (now also a "Will-Executor excluded" warning row with the reason), the
  command line's JSON (`_failed` now also names an exclusion).
- New texts translated (IT, ES): "fee above the maximum", "not a valid
  will-executor", "excluded: fee above the maximum", "excluded: not a
  valid will-executor", "no answer in time".

**Tests:** `tests/test_engine_push.py` (new, 12 tests, three fake
will-executors): the choice and the exclusions with their reasons;
received / refused / silent at the deadline / never answered; the reason
from the exception; the "already present" checks going on after an error;
a closing window stopping them; the menu, the command line and the Check
reporting an exclusion; no other module sends the will.
Golden files (`tests/golden/5e/copies/`, BAL_GOLDEN_UPDATE): three
photographs change exactly as decided: `menu_broadcast_executor_down`
(PUSH_FAIL, "no answer in time", the warning instead of "All transactions
are broadcasted"), `cli_executor_down` (PUSH_FAIL stored),
`executor_fee_raised_after_sent` (the CLI no longer sends to the
will-executor over the maximum fee, `_failed` names it). The divergence
register (`tests/test_5e_divergences.py`): D32 and D33 converged, D34's
error path covered by the new tests.
`tests/test_cli_command_layer.py`: its fake parallel push accepts the
new timeout callback and its fake servers count as valid (the test is
about the failure reasons).

**Verification:** ruff clean; the 102 test files that mention sending:
1958 passed; ZIP built, external zip load and smoke test OK on 4.8.2;
owner's ZIP test: an heir added and a Check (prepared, signed, sent and
verified 5 of 5; a second Check clean); the maximum fee lowered to 15500
and a Check (the three will-executors over it are already removed at the
Prepare step, "from 5 to 2", so nothing is left to exclude at sending;
sent and verified 2 of 2); the maximum fee restored and a Check (from 2
to 5, verified 5 of 5). The "Will-Executor excluded" row itself (a will
already signed for a will-executor whose fee then goes over the maximum)
is covered by the golden file and the new tests, not by the ZIP test.
Whole suite in both orders, run before the push for #155 and #156
together (recorded in #156's commit): 2417 passed each time, goldens
unchanged.

## 156. One shared verification of the will with the will-executors

**Date:** 2026-10-16

**Why (owner):** fifth piece of the de-duplication, after #152-#155:
asking the will-executors whether they hold the will was written three
times (the Check's "Verification" step, the will list's right click
"check", the command line's `bal_will_check`), and the right click did
not behave like the other two. Owner decisions 2026-10-16:
- E1: only the copies that were sent are asked. The right click asked
  every selected row, also the ones never sent: the will-executor rightly
  answered "I do not have it" and the row turned red ("not received").
  Now the right click asks every selected row sent at least once (also
  the one in force while a new will is only prepared, a verified one, one
  a previous check found "not received"); only a row never sent (a draft,
  or signed and not sent) is skipped, and a message says how many. The
  Check and the command line ask, as before, the valid sent rows not
  verified yet.
- E2: the right click saves the answers, as the other two (before, they
  were lost at the next start).
- E3: the command line's sending does not verify afterwards; that is what
  `bal_will_check` is for. Kept on purpose (divergence D21, note).

**What changed:**
- `bal/engine/verify.py` (new): `verify_targets` (which copies to ask,
  and the chosen ones skipped) and `verify_will` (ask every will-executor
  at once with the global deadline, record each answer: verified,
  failed, or no answer in time, which leaves the copy sent as in #147;
  returns each copy's outcome). A verified copy asked again that now
  fails is no longer marked verified too (before it could be both).
  Nothing is saved there.
- `BalBuildWillDialog.loop_verify` (Check), `BalWindow.check_transactions`
  / `check_transactions_task` (right click) and
  `BalController.check_transactions` (command line) use it and keep their
  own display.
- New texts translated (IT, ES): "{} selected transaction(s) not sent
  yet: nothing to verify.", "Nothing to verify.".

**Tests:** `tests/test_engine_verify.py` (new, 14 tests, three fake
will-executors): the choice for the Check / command line and for the
right click; the skipped rows; the owner's case (the row in force
checked while a new will is only prepared: asked, not "not sent yet");
a row found "not received" or refused asked again; every outcome and
its statuses; a verified copy that fails, or does not answer; no target,
no request; the right click with nothing sent (no request, the message),
with sent and unsent rows (asks the sent ones, saves, the message); the
command line limited to the given ids; no other module asks the
will-executors for the copies.
Golden files (`tests/golden/5e/copies/`, BAL_GOLDEN_UPDATE): two
photographs change exactly as decided: `row_check_never_sent` (no
request, no red rows, the message) and
`menus_prepare_sign_broadcast_check` (the row check's answers are now
stored). The divergence register (`tests/test_5e_divergences.py`): D38
converged; D21 noted as intended (E3).

**Verification:** ruff clean; the 82 test files that mention the
verification: 1465 passed; ZIP built, external zip load and smoke test OK
on 4.8.2; owner's ZIP test: the right click "check" on 2 and on 3 sent
rows (asks 2, then 3; verified); on the 5 rows of a draft (no request,
"5 selected transaction(s) not sent yet", no red rows); on a row in force
while a new will is only prepared (asked, stays verified; a first ZIP
skipped it as "not sent yet" because those rows are marked replaced
after Prepare: fixed, the right click now asks every row sent at least
once, and the case is a permanent test); then a Check (signed, sent,
verified 4 of 4). Whole suite in both orders, run before the push for
#155 and #156 together: 2417 passed each time, goldens unchanged.

## 157. One shared "sign the will"; three signing faults fixed

**Date:** 2026-10-16

**Why (owner):** sixth piece of the de-duplication, after #152-#156, and the
base of the coming offline signing (USB stick, multisig). Signing the will
and putting the signed copies back into it was written five times: the
Will tab's "Sign" (also "Sign and send", the Will Details window and the
will summary), the Check, the automatic rebuild, the command line
(`bal_will_sign` and the commands that sign) and "Review and sign imported
will" (the offline signing). Three faults went with the copies, verified
in the code and now permanent tests:
- A: a signing that failed half way (e.g. the 5th of 20) left the
  transactions signed before it marked "signed" with their signatures
  thrown away: never signed again, and the Check sent unsigned copies to
  the will-executors, with no message;
- F: the waiting window's "signed: ... / signing: ..." never showed;
- G: "Review and sign imported will" marked each transaction signed but
  dropped the signature, so the "signed file" carried back to the online
  computer held unsigned transactions.
Owner decisions 2026-10-16: signing "all" skips the copies of
will-executors deselected after the will was built that never received it
(the #135 rule of the Check, now everywhere); rows the owner chooses (the
list's right click "sign", `bal_will_sign <txid>`) are signed as chosen.

**What changed:**
- `bal/engine/sign.py`: `sign_will` (sign every target in order, stop at
  the first error keeping the ones before, refresh the signature counts;
  returns what was signed and the error), `sign_targets` with the
  deselected rule for "sign all", `to_sign`, `is_deselected_leftover`.
  `sign_will_item` now stores the signed copy in the item in the same step
  that marks it complete (the signature count is read first: turning a
  complete transaction into text finalizes it).
- `BalWindow.sign_transactions` / `ask_password_and_sign_transactions`,
  `BalBuildWillDialog.task_phase2` / `_ids_to_sign`,
  `BalWindow._auto_sign_save_push`, `BalController.sign_transactions` and
  `WillTxReviewSignDialog._sign_current` use it. A signing error is now
  shown (the Will tab: an error box; the Check: the "Signing your will"
  row; the command line: saved first, then the error). Cancel at the
  password signs nothing (before, the signing still ran and failed in
  silence).
- No new texts.

**Tests:** `tests/test_engine_sign_will.py` (new, 11 tests, a real test
wallet with a password, three fake will-executors): the Will tab's "Sign"
failing at the 2nd transaction (the 1st signed for real and saved, the
error shown, signing again completes); the Check failing the same way
(every copy sent is really signed, the row says why); the engine's
order and report; the waiting window's progress; the offline review (the
will carried as a file, signed page by page, "Save signed file…": the file
holds signed transactions); "sign all" skips the deselected copy, the
right click signs it; the command line without and with a txid, and an
error (saved, then raised); the rule; no other module stores a signed copy.
Run on the code before #157, A, G and the deselected rule fail.
`tests/test_engine_sign.py`, `tests/test_gui_will_flows.py`,
`tests/test_import_will_details.py` follow the new return value.
Golden files (`tests/golden/5e/copies/`, BAL_GOLDEN_UPDATE): five
photographs change exactly as expected: the progress text appears
(`menu_sign_with_password`, `menus_prepare_sign_broadcast_check`,
`menu_broadcast_executor_down`, `auto_new_coin_after_menu_window`), and
`menu_sign_cancel` opens no waiting window. The divergence register:
D29 converged, D31 noted.

**Verification:** ruff clean; the 122 test files that mention the
signing: 2164 passed; ZIP built, external zip load and smoke test OK on
4.8.2; owner's ZIP test: a fee change, Check (signed, sent, verified 4 of
4); Cancel at "Sign your will" in the Check ("interrupted", nothing
signed); a will-executor added, then deselected: the "≡" menu's Sign left
its copy unsigned ("Draft"), the right click "sign" on that row signed it.
Whole suite in one order (owner rule 2026-10-16: both orders every
2-3 steps): 2429 passed, goldens unchanged.

## 158. One shared "Merge"; a signed copy only when really signed

**Date:** 2026-10-17

**Why (owner):** second step towards the offline signing (cold wallet,
USB stick, multisig). The analysis of 2026-10-16 ran the whole file route
on test wallets (a watch-only online wallet with the keys offline; a
multisig 2-of-3 with cosigner A online and B offline; a watch-only
multisig with A and B offline): prepare, export, import into Will Details
and Sign on the other computer, export, "Merge" on the online computer,
Check. It works; the faults found on the way back ("Merge"):
- H4 (safety): "Merge" copied the file's "signed" status before looking
  at the transaction. A file saying "signed" with unsigned transactions
  inside (an old file, a damaged one) marked the copies signed, and the
  Check then sent them unsigned to the will-executors (verified on the
  code before #158: three unsigned copies sent);
- H3: after a merge a signed copy kept its old signature count ("1 of 2");
- H5: "Merge" was written twice (window, command line) and the local
  validity pass after it three times (also the QR / audio import).
Owner decisions 2026-10-16: one "Merge"; a copy is signed only when its
transaction is; a message after "Merge" says what came in.

**What changed:**
- `bal/engine/merge.py` (new): `merge_items` (the rules: the live copy is
  kept; sent / verified / mempool / confirmed come from the file; the
  signatures are combined, a multisig's add up; signed only when the
  transaction is complete; a signed copy counts all its signatures),
  `local_validity_pass`, `merge_will` (merge, save, check, save) and
  `merge_summary`.
- `BalWindow.merge_will` (the Will tab menu "Merge", Will Details'
  "Merge", "Merge transaction"), `BalController.merge_will`
  (`bal_will_merge`) and the QR / audio import's validity pass use it.
- The message after a merge (owner): "Merge done: {} transaction(s)
  signed, {} still to sign.", and for a multisig waiting for another
  signer "{} transaction(s) have only {} of {} signatures: another signer
  must sign them.". The menu "Merge" opens its own file window ("Open
  will file") so Electrum's "Your will were successfully imported" no
  longer comes first. `bal_will_merge` also returns `signed`,
  `partly_signed`, `unsigned`. New texts translated (IT, ES).
- Files (owner's ZIP test: "Merge" did not find the exported will): the
  will export saved a file with no extension, which the "JSON" filter of
  the file windows hid. The export now saves "...will.json"
  (`export_meta_gui` passes `default_extension`); the file windows of
  "Merge" and "Import" show "JSON" first and "All files" second (owner:
  JSON first), for older files. The content is still JSON, unchanged.

**Tests:** `tests/test_engine_merge.py` (new, 8 tests, test wallets made
from throw-away testnet keys, three fake will-executors): the watch-only
wallet with the keys offline (prepared, carried, signed, merged: "3
signed", sent signed); a file saying "signed" with unsigned transactions,
into the will and into an empty will (not signed, nothing sent); the
multisig second signer (1 of 2, then 2 of 2, sent signed); the watch-only
multisig with two offline signers (after A: "1 of 2", nothing sent; after
B: signed, sent); the command line's counts; the file windows (JSON first, the export
saves ".json"); no other copy of the merge.
On the code before #158 the H4 and H3 cases fail (the Check sent three
unsigned copies). `tests/test_import_will_details.py`: its fake window
gets the message box. `tests/test_gui_export_dialogs.py`: the export
title is "will.json".

**Verification:** ruff clean; whole suite in one order: 2436 passed
(before the file-name fix); after it, the export / import and merge tests
passed; goldens unchanged. Owner's ZIP tests on Electrum 4.8.2: one message
after "Merge"; offline signing simulated by file (4 signed, sent and
verified 4/4); an unsigned file gives "0 signed, 4 still to sign", the
Check asks the password; export saves ".json", "Merge" / "Import" show
JSON first.

## 159. The Check says "Ok" for the signing only when the will is signed

**Date:** 2026-10-17

**Why (owner):** third step towards the offline signing (cold wallet,
multisig). The analysis of 2026-10-16 found that "Check your will" wrote
"Signing your will: Ok" and "All done: Ok" when nothing was signed:
- H1: a watch-only wallet (the keys are on the offline computer) prepared
  the will, signed nothing and said "Ok" twice;
- H2: a multisig 2-of-3 signed by one cosigner ("1 of 2") said "Ok" too.
The same "All done: Ok" showed after a Cancel at "Sign your will", with
the automatic signing off and after a signing error.
Owner decisions 2026-10-17: no wallet-type label in the Will tab (Electrum
already shows the type in its window title and status bar; a label of our
own could break with an Electrum change); at the signing, say that the way
to sign depends on the wallet type; a multisig signed here is "partly
signed", not "still to sign".

**What changed:**
- `bal/engine/sign.py`: `signing_outcome` (how far the copies of "sign
  all" are signed: signed, partly signed as "have of required", not
  signed), using the #158 `merge_summary`.
- `bal/gui/qt/dialogs.py` (the Check): the "Signing your will" row says
  "Ok" only when every copy is signed; otherwise, in orange: "Not signed:
  this wallet has no keys (watch-only). Sign it on the offline computer."
  ("... on the cosigners' computers." for a watch-only multisig), or "1 of
  2 signatures: another signer must sign.", and below it "The way to sign
  depends on the wallet type, which Electrum shows in the window title."
  The last row "All done" says "Ok" only when the will is signed;
  otherwise "Will still to sign", or "Will partly signed: complete it with
  the missing signatures." for a multisig signed here. These lines also go
  into the report's "Attention" box; the "Sign" step of the bar is orange.
  A normal wallet is unchanged. New texts translated (IT, ES).

**Tests:** `tests/test_check_signing_honest.py` (new, 5 tests, the test
wallets of `tests/test_engine_merge.py`, three fake will-executors):
watch-only, watch-only multisig and multisig "1 of 2" (the rows, the hint,
"All done", the orange step, nothing sent, the same at the second Check);
the normal wallet unchanged; the engine's counts. On the code before #159
the first three fail ("Ok"). Golden files `tests/golden/5e/check/`
`auto_sign_off`, `cancel_at_sign`, `phase2_error` and
`owner_drafts_128` (the #128 owner case, password cancelled): "All done" is
now "Will still to sign" (nothing was signed there).

**Verification:** ruff clean; affected tests 245 passed; whole suite in one
order: 2441 passed, 1 failed (the `owner_drafts_128` golden, the expected
"All done" change above; updated with the owner's OK, its file then 22
passed). Owner's ZIP test on Electrum 4.8.2: a watch-only wallet made
from the test wallet's public key (the rows above, "Sign" orange, nothing
sent); the normal test wallet unchanged (in force, verified 4/4).

## 160. Tools > "Sign will offline...": the guided offline signing

**Date:** 2026-10-17

**Why (owner):** fourth step towards the offline signing (cold wallet,
USB stick). The offline computer (the keys) signed the will carried from
the online computer (a watch-only wallet) through the Will tab's "Import"
and the Will Details window: the will's whole history as coloured boxes,
buttons that make no sense there ("Merge" would mix the file into the
offline wallet's own will, "Broadcast will"), and nothing in plain words
about what was signed. The plugin of the offline computer also showed a
red "No will in force ... Build Your Will", which invites the wrong
action. Owner decisions 2026-10-17: one entry in Electrum's Tools menu
that guides the signing (no "role" setting, no first-run question);
drawings of our own in the plugin's colours; "No will in force" red only
when the wallet shows a balance. Verified first: a plain Electrum (no
plugin) cannot read the plugin's will file nor its list of transactions
(one transaction per file only), so the offline computer needs the plugin;
it can sign a single will transaction, which the plugin merges back (kept
for a later step).

**What changed:**
- `bal/engine/offline_sign.py` (new, no Qt): `file_problem` (why this
  wallet cannot sign the file: no keys, nothing to sign, already signed,
  another wallet or a cosigner that has already signed, Electrum's
  `can_sign`), `file_summary` (the heirs with their real amounts and
  addresses, read as the Future view and the PDF do, the message, the
  delivery date, the will-executors and their fees), `sign_file` (the one
  signing of #157, "sign all" without the will-executor selection rule)
  and `signed_file_data` (the JSON will format of the export, so "Merge"
  reads it).
- `bal/gui/qt/offline_sign.py` (new): the window, "Step N of 3" with big
  buttons: 1. choose the will file on the USB stick; 2. check the heirs
  (name, amount, address), the delivery date and the will-executors;
  3. "Sign all" (one password, in the "Please wait" window) and "Save on
  the USB stick…" (`<wallet>-will-signed-<date>.json`); a last page says
  where the file is and what to do on the online computer. A watch-only
  wallet gets one page saying it cannot sign; a file that cannot be signed
  gets a plain message; closing a signed will not saved yet asks first.
  The drawings (USB stick, sheet under a magnifier, padlock, tick, orange
  warning) are painted in the brand colours, no image files. The file is a
  copy: the wallet's own will is never touched, nothing is sent.
- `bal/gui/qt/window.py`: the Tools menu entry "Sign will offline…"
  (`show_offline_sign_dialog`), `sign_will_file`; the will summary gets
  the balance (`wallet_has_funds`) and is redrawn when the wallet gets or
  loses its balance (`notify_wallet_activity`, through a queued signal to
  the GUI thread); `bal/gui/qt/plugin.py`: Electrum's wallet events call it.
- `bal/core/summary.py`: `has_funds`: with no will and no balance the box
  is grey, "No will yet", keeps "Build Your Will" and says "Do you only
  need to sign a will brought on a USB stick? Use Tools > Sign will
  offline."; with a balance (or unknown) it stays red as before; the other
  red boxes (previous will invalidated, delivery date passed) unchanged.
- New texts translated (IT, ES); the names of Electrum's Tools menu
  ("Strumenti", "Herramientas") checked in Electrum's own catalogs.

**Tests:** `tests/test_offline_sign.py` (new, 13 tests, the test wallets
of `tests/test_engine_merge.py`, three fake will-executors): the whole
route (watch-only online, the window signs offline, "Merge", the Check
sends three signed copies); a multisig 2-of-3 (A: "1 of 2 signatures",
A again refused, B: signed, merged); a watch-only wallet; files that
cannot be signed (already signed, another wallet, not a will file,
unreadable); the engine's reasons in order; Cancel at the password; the
question when closing unsaved; the file name and the drawings; the Tools
menu entry; the summary box red / grey by balance; the window following
the balance.

**Verification:** ruff clean; affected tests (summary, goldens, layering)
852 passed; ZIP built, external zip load and smoke test OK on 4.8.2;
screenshots of every page in Italian checked. Owner's ZIP test on
Electrum 4.8.2: watch-only wallet, Check, Export > File; the test wallet
with keys, Tools > "Sign will offline…", three steps, saved; back on the
watch-only wallet, "Merge" (the four copies blue: signed), Check (green:
sent and verified 4 of 4). Whole suite in both orders: 2455 passed each
time (26:49 and 25:42), goldens unchanged.

## 161. The online computer's steps of the offline signing; a five-step guided route

**Date:** 2026-10-18

**Why (owner):** #160 guided only the offline computer (the keys). On the
online computer (a watch-only wallet) the owner still had to know "Export,
choose a filter, save the file, carry it, Merge, Check". Owner: one guided
route, as simple as possible, "one screen, one thing to do", where each
screen says on which computer to act. After the ZIP tests the owner asked
for: five steps with the USB stick travelling as steps of their own; online
and offline that look different; a map of the whole route before starting;
no file window opening by itself; a window that fits its screen; larger
texts; no big button in the Will tab (it did not follow the steps and was
missing on the offline computer: the Tools menus, which the owner always
finds, open the window on the right step); the Will tab's Tools menu in a
new order; "Invia" / "Invalida" / "Verifica/Aggiorna" as the Italian names
of the Broadcast, Invalidate and Check commands.

**What changed:**
- `bal/engine/offline_sign.py` (no Qt): the route's steps (`STEP_PREPARE`
  1, `STEP_CARRY` 2, `STEP_SIGN` 3, `STEP_BRING_BACK` 4, `STEP_SEND` 5);
  `next_step` (the step to make now: watch-only, 1 while the will to sign
  is not on a stick, 5 once it is out, none in force); `prepare_problem`
  (no will / already signed); `stick_items` (only the copies still to
  sign); `signed_file_problem` (nothing to sign, another wallet, the file
  still to sign); the wallet's input addresses read from its history, so a
  fully signed file is recognised.
- `bal/gui/qt/offline_sign.py`: Tools > "Sign the will offline..." opens
  the window that fits the wallet: `OnlineSignDialog` (watch-only: steps 1,
  2, 4, 5) or `OfflineSignDialog` (keys: step 3, #160), always on the step
  to make now. Shared frame (`_GuidedDialog`): the five-step bar
  (`StepBar`, each step in the colour of its computer: teal online, orange
  offline, grey for the stick travelling; "you are here"; a signatures
  count "1 of 3" for a multisig), the place ("On THIS computer" / "On the
  OTHER computer ...", in that colour, larger), the drawing of that
  computer (teal with the network, orange with a padlock, with the stick
  when it must be moved), a big title, numbered instructions in a larger
  font, at most one big button, small links ("I already have the signed USB
  stick →", "I still have to sign it", "See the plan"), larger. The route
  starts with the map of the five steps ("What we will do",
  `route_map`, painted, the cosigners' computers for a multisig); "Start"
  shows step 1. No file window opens by itself: the screen's button opens
  it. The window fits the height of the screen shown (`fit_height`, the
  pages not shown ask for no room). Step 1 runs the Check when needed and
  saves only the copies to sign (marked EXPORTED); step 5 refuses the file
  still to sign with a warning, merges the signed file (#158) and runs the
  Check; a multisig goes from cosigner to cosigner on the same stick. The
  instructions name "Tools > Sign the will offline". In the check before
  signing, an amount that depends on the will-executor shows "at least
  ..." with a small "ⓘ" that says it is the least the recipient gets, if
  the most expensive will-executor sends the transaction.
- `bal/gui/qt/window.py`: `export_stick_file` (saves the will, then
  refreshes the will summary); `after_wizard_create` (a watch-only will
  waiting for its signatures opens the window after the wizard);
  `show_offline_sign_dialog` without a step; the summary box's
  "offline_sign" button opens the window.
- `bal/gui/qt/lists.py`: no big "Sign the will offline" button. The Check
  button moved to the right, before the calendar. The Will tab's Tools
  menu in the owner's order: Prepare, Sign, Broadcast will, Check,
  Invalidate will, Display, Export, Import, Merge, then under a line Sign
  the will offline..., Calendar reminders, Will report.
- `bal/core/summary.py`: a watch-only will to sign gets the action
  "offline_sign" and a note that the keys are on another computer.
- `bal/gui/qt/basic_wizard.py`: the summary page of a watch-only wallet
  says the will is signed offline (single key / multisig);
  `bal/gui/qt/dialogs.py`: the wizard's "Create the will" leads to the
  window; the Check's watch-only texts name Tools > Sign the will offline.
- `bal/gui/qt/widgets.py`: the Check window no longer draws "How the
  shares are divided".
- Catalogs (IT, ES): every new text translated; Italian "Broadcast will" =
  "Invia", "Invalidate will" = "Invalida", "Check your will" =
  "Verifica/Aggiorna" (Spanish "Enviar", "Invalidar",
  "Verificar/Actualizar"), and the hints that quote those buttons follow.

**Tests:** `tests/test_sign_offline_online.py` (new, three fake
will-executors): the single-key route through the five steps; the links
between 2 and 5; a multisig 2-of-3 and a 3-of-4 from the same stick; the
wrong files; the engine's rules; a signed file without the wallet data; no
will; the wizard; the summary box; the Tools menu opening the step to make
now (and the summary refreshed after the stick is saved); the step bar's
colours; the route map; the colour of each computer; the window fitting
its screen; "at least" with its "ⓘ". Updated: `test_offline_sign.py`,
`test_check_button.py` (toolbar order, Tools menu order),
`test_check_signing_honest.py`, `test_gui_basic_wizard.py`,
`test_shares_note.py`.

**Verification:** ruff clean; affected tests 201 passed; ZIP built,
external zip load and smoke test OK on 4.8.2; screenshots in Italian
checked. Owner's ZIP tests on Electrum 4.8.2 with the test wallets
(watch-only online, keys offline): the whole route, signed and sent; the
fixes asked after each test checked again. Whole suite in both orders:
2475 passed each time (24:14 and 28:22), goldens unchanged.


## 162. "How BAL PROTOCOL works": the animated explainer, offline, in the plugin

**Date:** 2026-10-18

**Why (owner):** the 17-step animation made for the website explains in
pictures what a will-executor does (copies, tips, a day earlier, the
invalidated copies, the delivery). Owner: put it in the plugin too,
offline, in the three languages; block numbers always shown as an
estimate. After the first ZIP the owner asked for: a visible button with
words and a "?" (the round "i" was nearly invisible), small and the same
in the wizard and in the will-executor window, in a tidy box; on page 1 of
the BASIC wizard no "Mode: Basic · Advanced" link and the "Why two times?"
box closed until clicked.

**What changed:**
- `bal/core/explainer.py` (new, no Qt): the story (17 steps, title, text,
  note), the labels, the geometry and `scene(n, t, start)`, everything
  drawn at a moment of a step (copies, red and torn copies, the accepted
  block, the delivery date that moves, heir 2's new address, coins, fee,
  the strip of blocks that slides when time passes); the years follow the
  clock (this year, + 3, + 7); the block height is an estimate from a
  stored tip (144 blocks a day, never lower); the reading time of a step.
- `bal/gui/qt/explainer.py` (new): the painter (`paint_scene`, logical
  1030x516 scaled to the window), `ExplainerCanvas` (33 ms repaint, the
  moving parts glide from what was shown), `ExplainerDialog` (back, next,
  play/pause, from the start, 17 step dots, the step's card; it plays by
  itself; closing stops every timer), `explainer_button` (a small
  rounded button "How will-executors work" with a drawn "?" badge),
  `explainer_intro` (a light teal box: the sentence on what a will-executor
  is and the button), `show_explainer` (one window per wallet window).
- `bal/gui/qt/lists.py`: the Will tab's Tools menu ends with "How BAL
  PROTOCOL works"; `bal/gui/qt/window.py`: `BalWindow.show_explainer`.
- `bal/gui/qt/dialogs.py`: the will-executor window starts with the
  explainer box; `BalWizardDialog.switch_to_advanced` removed (only the
  page 1 link used it).
- `bal/gui/qt/basic_wizard.py`: page 3 shows the explainer box; page 1
  without the mode link (the mode stays in the plugin settings) and with
  `Disclosure`, a "▸ Why two times?" line that opens and closes its text
  (the old text split into title and text, same translations).
- Catalogs (IT, ES): every new text translated; the two removed texts
  dropped.

**Tests:** `tests/test_gui_explainer.py` (new): the years, the block
estimate, the strip, the copies of every step, the dates, heir 2, coins
and fee, the reading time, every step painted, the window's controls and
autoplay, the glide, Italian; the button of page 3 and of the
will-executor window (real `BalWindow`, three fake will-executors, each
with its own fee address). Updated: `test_check_button.py` (the menu's
last entry), `test_gui_basic_wizard.py` (no mode link; "Why two times?"
closed, opens and closes on click).

**Verification:** ruff clean; affected tests 52 + 159 passed; ZIP built,
external zip load and smoke test OK on 4.8.2; screenshots in Italian
checked by the owner. Owner's ZIP test on Electrum 4.8.2: OK. Whole suite:
2487 passed (25:51).

## 163. The visible name becomes Àncòra (brand kit ancora-brand_1)

**Date:** 2026-10-18

**Why (owner):** the plugin has its final name: Àncòra, by Safe21 (kit
`ancora-brand_1`: logo, icons, colours, slogan). Owner decisions: the name
is the same in every language, with its accents wherever possible;
"Àncòra (Safe21)" in Electrum's plugin list; the slogan "Àncora per te,
ancòra per loro." stays Italian in every language; the will report shows
the new logo; the logo files are used unchanged and the colours of the
plugin's interface do not change.

**What changed:**
- `bal/core/brand.py` (new, no Qt): `NAME` ("Àncòra"), `FULLNAME`,
  `NAME_ASCII`, `SLOGAN` and the two colours of the logo. The texts take
  the name through `{}`, so it is never inside a text to translate (the
  only exception: the two calendar defaults, editable with `$` tokens,
  whose translations keep it as it is).
- `bal/manifest.json`: `fullname` "Àncòra (Safe21)", the description
  starts with the slogan, icon `icons/ancora-icon-light-128.png`; the accents
  written as `\u` escapes, so the file is plain ASCII.
- Icons: `bal16x16.png`, `bal32x32.png` and `safe21-logo.png` replaced by
  `ancora-icon-light-16/64/128.png` (the kit's `ancora-icon-light.svg`
  made into PNG, shapes and colours unchanged; owner: the light icon, with
  the anchor larger in its square, 76% instead of 66%:
  `assets/brand/svg/ancora-icon-light-plugin.svg`) and
  `ancora-lockup.png` (the kit's file): plugin list, status bar, settings
  dialog, window icons, will report.
- Window titles "Àncòra - …" (`get_window_title`, the wizard's pages), the
  wizard title, the restart message, the two command-line refusals, the
  wallet utility, the calendar texts; the calendar `PRODID` uses "Ancora"
  (no accents there).
- `bal/gui/qt/report_pages.py`: the report header shows the àncòra logo;
  the vector fallback draws the anchor and the word.
- `README.md`, `bal/README.md`: the new name, logo and slogan.
  `assets/brand/` (new): the kit's SVG and PNG files and a short README.
- Catalogs (IT, ES): the texts with the old name replaced by texts with
  `{}` and translated; the committed order of the entries kept.

**Not changed (Phase 6):** the internal identity (`bal` package, `bal_*`
keys and commands) and the labels stored in the wallet ("Safe21 Heartbeat
backup transaction", "Safe21 Heartbeat invalidation"): the plugin
recognises its own transactions by them.

**Tests:** `tests/test_brand.py` (new): the names, the manifest (name,
icon, description, plain ASCII), every icon named in the code is shipped,
no text to translate holds the old name and the new name only in the two
calendar defaults, kept by every translation. Updated:
`test_cli_window_guard.py` (the refusal texts), `test_inheritance_report.py`
(the logo's colours, with the image and with the vector fallback).

**Verification:** ruff clean; affected tests 226 passed (48 s); ZIP built,
external zip load and smoke test OK on 4.8.2; screenshots checked; after the first ZIP the owner chose the light icon, then a larger anchor
(choice B of three previews).

## 164. Development line 0.8.0 (branch `dev/0.8.0`)

**Date:** 2026-10-18

**Goal:** Keep `main` as the tested 0.7.0 release and start the 0.8.0 line
(the "multiverse" work) on its own branch, so that a test ZIP of the new line
can be told apart from the official one in Electrum's plugin list.

**What changed:**

- `bal/manifest.json`: `version` 0.7.0 -> 0.8.0, on `dev/0.8.0` only. `main`
  stays 0.7.0. Fixes made on `main` are merged into this branch from time to
  time; if the work does not succeed, the branch is dropped and `main` is not
  touched.

**Tests:** none needed; no code changed (no test reads the manifest version:
the report tests pass the version themselves).

**Verification:** manifest still pure ASCII and valid JSON.

**Outcome:** DONE.

## 165. Root LICENSE and "Origin and compatibility" in the README

**Date:** 2026-10-18

**Goal:** Prepare the repository to be made public: GitHub must find the
licence in the root, and readers must learn where Àncòra comes from.

**What changed:**

- `LICENSE` (new): a copy of `bal/LICENSE` (MIT; the original line
  `Copyright (c) 2024 svatantrya` is kept and the Safe21 line stays below it).
- `README.md`: new section "Origin and compatibility": fork of BAL, same
  will-executors and welist, not installable together with the official BAL
  plugin (internal name `bal`), release tags `ancora-vX.Y.Z`.

**Tests:** none needed; texts only.

**Verification:** `LICENSE` identical to `bal/LICENSE`; ruff unchanged.

**Outcome:** DONE.

## 166. Rebuilding a will with delivery instalments (multiverse) gives a correct chain

**Date:** 2026-10-19

**Problem (probe M0, `context/reports/multiverse-probe-2026-10-19.md`,
three fake will-executors, hidden option `bal_enable_multiverse` on):** with
heirs on different dates the will is a chain (each later delivery spends the
change of the one before; every will-executor holds a copy of every level:
3 + 9 or 3 + 9 + 27 transactions). The first build worked. Every REBUILD
that moved a date (new funds, a new miner fee rate, an heir address changed)
failed with "list index out of range" or saved a broken will: children
spending their own txid, one item under two keys, transactions without a
txid. A later delivery's share changed left the old and the new first
delivery both VALID on the same coins on the same day (78 VALID items). New
funds that covered the first delivery alone were spent by it alone, the old
coins went to a later delivery, and the old and the new first delivery could
both be executed. Causes (multi-agent analysis + probes): `Will.change_input`
relinked each child to its own key instead of the parent's new txid, deleted
by `tx.txid()` (None for chain children right after the build: the PSBT
re-parse drops their input data) so stale keys stayed, rebuilt the input with
another sequence number; `normalize_will` works on copies; `check_anticipate`
compares the heir rows, not the real amounts; the coin selection takes only
the coins a delivery needs.

**What changed:**

- `bal/core/will.py` `Will.update_will`: a will whose new items spend
  outputs of other new items (`Will.chain_links`) is dated by the new
  `Will.date_chain`; a will with one date keeps `search_anticipate_rec`,
  byte for byte. `date_chain`: the parent links are read once, by object;
  each chain input gets the data of the output it spends
  (`_fill_chain_input`, in memory only, as `add_info_from_will` does);
  generation by generation the children are pointed at their parents' FINAL
  txids (`_relink`: in place, same sequence number, inputs in BIP69 order as
  `from_io`, cached txid cleared), then dated with today's rules plus the
  #166 rule (`_chain_set_anticipate`: same will-executor, other REAL heir
  amounts -> one day earlier, owner decision D6). The first delivery is dated
  against the old items on the same coins; a later one only against old
  items spending the same parent output. `_check_chain` refuses (new
  `WillChainError`, the will in force untouched) a link not pointing at its
  parent, a later delivery not dated after the one it spends, an old first
  delivery left executable together with the new will, and an old and a new
  transaction of the same will-executor executable together. The dict is
  re-keyed once by the final txids and each `_id` follows.
- `bal/core/heirs.py` `prepare_transactions` (owner decision, solution C):
  with more than one delivery date the first delivery spends EVERY coin and
  the later ones only its change. One date: unchanged (that transaction
  already spends every coin). Noted in the module docstring as the only
  exception to "kept byte-for-byte identical".
- Not changed (owner decision, step M3 of `context/MULTIVERSE-PLAN.md`):
  after a rebuild that anticipated the chain, the heirs' own dates stay the
  old ones, so the next Check reads a postpone and asks for the paid
  invalidation (F1); the multiverse option stays hidden. `change_input`,
  `new_input` and `normalize_will` stay for the one-date path (cleanup
  later). `WillChainError` is shown as raw English text (text step later).
- `context/reports/multiverso-resoconto-per-truman-IT.md`: the bugs found
  and the fix, in Italian, for BAL.

**Tests:** `tests/test_multiverse_chain_rebuild.py` (new, three
will-executors, two and three dates): first build; new coin; a new coin that
covers the first delivery alone; three coins then a new one; fee rate
change; a later delivery's share changed; a later delivery's address changed
(the first delivery kept); delivery 1 or 2 earlier; each followed by a
second build that changes nothing, and an audit of the chain (key = txid, no
self-spend, child after parent, sequence 0xfffffffd, pushed raw = stored
txid); the dispatch (one date never takes the chain path); broken links
refused; F1 as a strict xfail. Before the fix the new-coin, fee, address,
share and big-coin cases failed (probe on the same code).

**Verification:** `ruff check bal/ tests/`: all checks passed.
Whole `tests/` directory, normal order and files in reverse order: 2506
passed, 1 xfailed (F1) each (23 and 19 minutes); golden files unchanged.
Scenario harness (full matrix, the 2-coin scenarios 6 times, warn_matrix
synced and not): log identical to the base `dev/0.8.0` (135 lines).
`build_zip.py` + `external_zip_test.py` + `smoke_test.py` on Electrum
4.8.2: pass. Test ZIP
`test-zips/ancora_0.8.0-TEST-166-multiverse-chain-rebuild.zip`, sha256
`c6ea1557...c6c5`.

**Outcome:** DONE. The owner tested the ZIP on his test wallet (one date,
four will-executors): Check "ok", preparation, signature and sending "not
needed", verification 4/4, "Your will is in force".

## 167. The rules of the staged deliveries (multiverse): the instalments table

**Date:** 2026-10-19

**Goal (step M2 of `context/MULTIVERSE-PLAN.md`, owner decisions of
2026-10-19):** the pure rules of a will paid in up to three deliveries on
the same dates for every heir. A table: one row per date, one cell per heir,
each cell a share of the WHOLE estate. Nothing uses the rules yet: the
engine, the command line and the schema 3 of the wallet settings come with
M3 (owner choice B: the schema moves to 3 only when a table is really
written), the editor with M4.

**What changed:**

- `bal/core/delivery.py`, new section "Staged deliveries":
  - `INSTALMENTS_KEY` (`"instalments"`, the settings key M3 writes),
    `INSTALMENTS_MIN` 2 (one date is a normal will), `INSTALMENTS_MAX` 3,
    `MIN_GAP_DAYS` 28 (the will-executors never retry a failed broadcast:
    twice Bitcoin Core's 14-day mempool expiry; monthly deliveries still
    work across February).
  - `instalments_of(settings)`: the stored table as a copy, `[]` when there
    is none (one delivery); a value that is not a list raises `ValueError`
    (read as "no table" it would pay everything at the first date).
  - `as_share` / `share_text`: a share from 0 to 100 with two decimals at
    most, read with `decimal.Decimal` (exact sums), stored as `"<n>%"`
    without trailing zeros.
  - `validate_instalments(rows, heirs, now)`: 2 or 3 rows with exactly
    `locktime` and `shares`; dates after `now`, strictly increasing, at least
    28 days apart (a period or a block height refused); a name that is not
    an heir refused, a missing heir counts 0 %; every date pays one heir at
    least; an heir with 0 % at every date refused ("remove the heir"); the
    whole table exactly 100 %. Returns the normalized table (dates as ints,
    every heir named in every row). `ValueError` in English on the first
    broken rule.
  - `default_instalments(heirs, first)`: the example the editor will start
    from: the wallet's date, +12 and +24 months; 33 / 33 / 34 % of the
    estate split equally among the heirs in cents, the cents left over to
    the last heir (three heirs: 11 / 11 / 11, 11 / 11 / 11,
    11.33 / 11.33 / 11.34 %).
  - Module docstring: the new section listed. `SCHEMA_VERSION` stays 2.
- `tests/test_core_delivery_settings.py`: `decimal` added to the standard
  library modules `delivery.py` may import.

**Tests:** `tests/test_core_delivery_instalments.py` (new, 116 tests): the
constants; the reader (no table, a copy, not a list); `as_share` and
`share_text` ("-0" stored as "0%"); the owner's example table valid and
kept; a missing heir written as 0 %; numbers read exactly; exactly 28 days
accepted (also across February); every rule refused with its message (1 or 4 dates, not a list,
bad rows, bad dates, past dates, dates not increasing, 27 days, a name that
is not an heir, bad shares, a date paying nobody, an heir getting nothing,
99.99 % / 100.01 %, bad heir names); the default table for 1, 2, 3, 4 and 7
heirs, valid for 1 to 12 heirs, its dates. No will-executor is involved.

**Verification:** `ruff check bal/ tests/`: all checks passed. Whole
`tests/` directory, normal order: 2621 passed, 1 xfailed (F1) (25 minutes);
the "-0" test was added after it (the new file alone: 116 passed).
Mutation check on a copy of the code: 25 rules broken one at a time, all 25
caught by the new tests.
No ZIP: nothing in the plugin calls the new functions yet (owner, choice B).

**Outcome:** DONE.

## 168. Staged deliveries in the engine: the table of deliveries builds the chain

**Date:** 2026-10-19

**Goal (step M3a of `context/MULTIVERSE-PLAN.md`; owner decisions of
2026-10-19, M3 study):** a wallet with a table of deliveries
(`will_settings["instalments"]`, rules of #167) gets a will in up to three
deliveries, the same dates for every heir, each heir with a share of the
whole estate at each date. BAL's hidden global option
`bal_enable_multiverse` (each heir with its own date) is no longer read
(D7). The command line (`bal_instalments_*`, M3b) and the editor (M4) come
next: until then a table can only be written by the engine.

**What changed:**

- `bal/core/delivery.py`: `INSTALMENT_SEP` ("§") and the row names of each
  delivery (owner decision 2): `instalment_key` ("Luca", "Luca§2",
  "Luca§3": the delivery NUMBER, never the date), `instalment_number`,
  `person_name`, `has_separator`; `is_message_row`, `payee_names` (the
  OP_RETURN message is not a column); `table_rows` (delivery 1 is always
  on the wallet's `locktime`, the one date every existing rule moves);
  `effective_rows` (one row per person and delivery with a share above
  0 %, the message with delivery 1, decision 4); `anticipated_rows` (F1:
  deliveries 2 and 3 follow an EARLIER date of the will, never a later
  one); `InstalmentsMismatch`; `INSTALMENTS_SCHEMA_VERSION` 3.
  `validate_instalments(rows, heirs, now, gap=True)`: `now` None skips
  "after today", `gap` False the 28 days (a stored table: delivery 1 may
  be past, the automatic one-day anticipations may shorten a gap,
  decision 5).
- `bal/engine/instalments.py` (new): `DeliveryHeirs` (the rows of every
  delivery, in memory only, never saved); `has_table`; `check_table` (the
  table against the heirs at every flow: an heir added, removed or renamed
  without the table stops everything, decision 3); `effective_heirs`
  (`host.heirs` itself without a table); `first_delivery_heirs`;
  `delivery_dates`; `sync_later_deliveries` (F1).
- `bal/engine/context.py`: `init_class_variables` checks the table (after
  the Check Alive gate, so a renewal is still offered; never in
  `check_holds`, so an invalidation stays possible) and always gives the
  person rows the will's date; `check_will` compares the items with the
  rows of every delivery (renames and the will check);
  `drop_anticipation_leftover` compares delivery 1 only; `_multiverse` =
  the wallet has a table (no period form); the migration no longer reads
  the global option. New `set_instalments(host, rows, now)` (the one
  validated write: refused with the period form; the table stored whole,
  schema 3 only together with a table, owner choice B; the wallet's date =
  delivery 1) and `clear_instalments(host)`. `set_will_setting`: with a
  table, a delivery date on or after delivery 2 is refused (the order only;
  the 28 days are checked where the owner types the date).
- `bal/engine/build.py`: the build uses the rows of every delivery (the
  chain of #166, unchanged `heirs.py`); `last_build_error` is copied back;
  `sync_delivery_date` also moves deliveries 2 and 3 earlier (F1 of #166:
  the next Check no longer reads an anticipation as a postpone).
  `bal/engine/check.py`, `bal/gui/qt/window.py` (Prepare) and
  `bal/cli/controller.py`: the amounts check reads the rows of every
  delivery; a table that does not fit stops Prepare with its message, and
  the command line shows it. `bal/gui/qt/dialogs.py`: the Check's quiet
  "Shares recalculated in proportion" line and its "shares changed"
  comparison read the table's rows (delivery 1 for the comparison), not
  the person rows.
- `bal/core/will.py`: `_pure_rename` and `_renamed_to` match a row of the
  same delivery only ("Marco§2" for "Luca§2"); `planned_delivery` reads the
  earliest VALID item (delivery 1).
- Display (the person, never "Luca§2"): `bal/core/future.py` (`_instalment`),
  `bal/core/report.py` (`_addresses`, so `_heirs` sums by person),
  `bal/core/will_versions.py` (`_heir_rows`). The other texts that may
  show a row name (Check messages, the Will tab, the CLI output) come with
  M3b and M4.
- D7, BAL's global option no longer read: the heir dialog has no Locktime
  row; `set_heir` always gives the will's date; the delivery form's period
  choice is off with a table (`widgets.py`); the command line always
  refuses `--locktime` and no longer checks the dates of an import
  (`_check_locktime_value` removed). `plugin_base.py`: the key stays
  defined, with a comment.
- Heir names with "§" refused (decision 2): the heir dialog, `heirs_add`,
  `heirs_import` (skipped with the reason). New texts, IT/ES catalogs
  updated: "The table of the deliveries no longer matches the heirs...",
  "An heir name cannot contain the sign {}."; "locktime" (the removed help
  button) dropped.

**Tests:**

- `tests/test_engine_instalments.py` (new, three will-executors): the
  chain of a table (3 + 9 + 27 items, the row names of each delivery,
  amounts within 1 % of 10 / 10 / 30 % of the estate, the person rows
  unchanged, one password prompt); a 0 % cell; the message with delivery
  1; `planned_delivery`; `set_instalments` and four refused tables
  (nothing written), the period form, a name with "§"; the wallet's date
  kept before delivery 2; an heir added or removed without the table:
  Check, Prepare and the command line stop, nothing built or sent; a rename
  in the heirs and the table is a pure rename; delivery 2 later asks the
  paid invalidation, earlier is free (D6); the table removed: one delivery
  one day earlier; BAL's global option not read; two wallets keep their
  own form; the table survives a reload; the amounts check reads the
  table (it found the Check dialog's own shares line reading the person
  rows: fixed in `bal/gui/qt/dialogs.py`, `_quiet_shares_line` and
  `_shares_change`); no period form with a table.
- `tests/test_core_delivery_instalments.py`: the row names, the message
  rows, `effective_rows`, `table_rows`, a stored table checked without
  `now` and the 28 days, `anticipated_rows`.
- `tests/test_multiverse_chain_rebuild.py`: the shapes are tables now
  (`context.set_instalments`); the share changes keep the table at 100 %;
  "a later delivery's address changed" became "shares moved between
  deliveries 2 and 3" (one address per person); F1 is no longer an xfail:
  it passes, also after a delivery 2 anticipated.
- Moved from the global option to a table (or to D7): 13 files
  (`test_advanced_period_default`, `test_cli_command_layer`,
  `test_cli_controller_offline`, `test_core_future`,
  `test_core_will_versions`, `test_delivery_form_lock`,
  `test_gui_date_form_change_flow` (the per-heir period kept by leaving the
  heirs' dates alone), `test_gui_delivery_date_message`,
  `test_gui_delivery_form`, `test_gui_will_flows`,
  `test_gui_will_versions_list`, `test_inheritance_report`,
  `test_per_wallet_settings_flow`). `tests/flowkit.py`:
  `Session.set_instalments`.

**Verification:** `ruff check bal/ tests/`: all checks passed. Whole
`tests/` directory: normal order 2653 passed (45 minutes, before the last
two fixes: the Check dialog's shares line and its test), files in reverse
order 2655 passed (35 minutes, with them); no xfail left (F1 passes);
golden files unchanged. Mutation check on a copy of the code: 16 rules
broken one at a time, all 16 caught. Scenario harness (full matrix, the
2-coin scenarios 6 times, warn_matrix synced and not): log identical to the
base `dev/0.8.0` 412c88b (135 lines) and to the #166 log. `build_zip.py` +
`external_zip_test.py` + `smoke_test.py` on Electrum 4.8.2: pass. Test ZIP
`test-zips/ancora_0.8.0-TEST-168-instalments-engine.zip`, sha256
`72cbfd12...423c`.

**Outcome:** DONE. The owner tested the ZIP on his test wallet (one date,
four will-executors): the heir dialog has no Locktime row; Check "ok",
nothing to prepare, sign or send, verification 4/4; an heir's share from
60 % to 70 %: the will rebuilt one day earlier (free), signed, verified
4/4, in force.

## 169. Staged deliveries on the command line: bal_instalments_show, set and clear

**Date:** 2026-10-20

**Goal (step M3b of `context/MULTIVERSE-PLAN.md`; owner answers of
2026-10-19):** the table of deliveries of #168 can be written, read and
removed from the command line (Electrum's Console, the terminal, JSON-RPC);
the command-line texts name the people, never the row names "Luca§2".
Owner answers: (1a) the heir commands keep working with a table and say
what it needs; (2) the table as JSON in the command, or a JSON file; (3)
the live test on the command line is made by Claude. After the review,
owner decision (a): any change by which the heirs would receive part of
the estate EARLIER needs `--confirm_earlier true` (not only a date moved
earlier: also shares moved to an earlier delivery, delivery 3 removed, the
table removed). WHY: earlier is a free, silent rebuild that only the paid
invalidation can undo once sent, and in practice the command line is used
by an AI (owner): the refusal tells it to ask the owner first.

**What changed:**

- `bal/cli/commands.py`: three commands. `bal_instalments_show` (read);
  `bal_instalments_set` (`--table` JSON, `--path` a JSON file, or
  `--example true`; `--confirm_earlier true`); `bal_instalments_clear`
  (`--confirm_earlier true`). Set and clear are wallet writes, refused
  while the wallet's plugin window is open (#148 list). The `--locktime`
  help of `bal_heirs_add` and `bal_heirs_update` now says it is always
  refused (#168, D7). The "%" of the help texts is written "%%": argparse
  %-formats them.
- `bal/cli/controller.py`:
  - `instalments_show`: `staged`, `date`, `deliveries` (`delivery`,
    `date`, `locktime`, `shares`: the form `bal_instalments_set` takes
    back, also for a date not at 00:00 UTC), `fits_heirs` and `problem`
    (owner decision 3), and when the table fits `delivery_totals`,
    `heir_totals`, `total`, `gaps_days`, `min_gap_days` and `will_items`
    (the VALID items per delivery).
  - `instalments_set`: one source; refused in BASIC mode (owner decision
    D5: staged inheritance is ADVANCED only), with the period form, without
    heirs; the table read strictly (`date` or `locktime`, `shares`,
    `delivery` that must match its place, as a number or a text such as
    "1"; an unknown field, a period, a block height, a past date or two
    different dates for one delivery are refused); every rule of #167
    checked before anything is written. `--confirm_earlier true` needed
    (decision (a)) for a date earlier than its reference (the VALID items
    of that delivery, as `bal_delivery_set`; else the stored date, with
    its own words "the current date of this delivery") and for a table
    that pays more of the estate by one of its dates than now
    (`_schedule_now`: the stored table's shares on the dates in force;
    `_first_earlier`, date by date). Every refusal of this kind ends with
    "Ask the owner; if this is what the owner wants, run the command again
    with --confirm_earlier true." Then `context.set_instalments` (#168).
    The message lists the dates, says when a delivery is later than the
    will in force (the next prepare asks for the paid invalidation, D6)
    and that the heirs' amounts are not used while the table is there.
  - `instalments_clear`: one delivery again, with `--confirm_earlier true`
    (the rest of the estate arrives at delivery 1); the answer lists the
    heirs, whose amounts count again, and says to check them.
  - `bal_delivery_set --date` and `bal_delivery_form fixed --date`: with a
    table the date is delivery 1, at least 28 days before delivery 2 (owner
    decision 5). `bal_delivery_form period` with a table: refused with
    "remove the table first" (before the "will in force" refusal, which
    pointed to an invalidation that does not help).
  - `bal_delivery_show`: `staged` and `deliveries`.
  - Heir commands (`add`, `update`, `delete`, `import`): with a table,
    `instalments_note` says whether the heirs still fit it (owner answer
    1a). A table that no longer fits: the message of #168 plus the commands
    that fix it.
  - `bal_will_status` (and every answer that shows the will): `heirs` and
    `description` name the people; with a table each item has its
    `delivery`. A will with one date gives the same answer as before (the
    golden files are unchanged).
- `bal/core/messages.py` (shared with the Check dialog): `heir_label`, the
  person and the delivery: "Heir "Luca (delivery 2)" was removed", "The
  amount of heir "Luca (delivery 2)" changed"; in a will in deliveries
  delivery 1 too ("Luca (delivery 1)": "Heir Luca was removed" was wrong
  when Luca still receives later); a rename "Luca → Marco"; "The date of
  delivery N changed: preparing a new will." when every heir of that
  delivery has the new date. `bal/core/will.py`:
  `check_willexecutors_and_heirs` (docstring added) computes `all_heirs`
  (the "same date" of #125) among the heirs of the same delivery and
  passes `staged` (the will has deliveries 2 or 3) to
  `HeirNotFoundException` (new attribute, docstring). A will with one date:
  the same messages as before.
- Catalogs: two new texts, "{name} (delivery {n})" and "The date of
  delivery {} changed: preparing a new will.", translated (IT "consegna",
  ES "entrega"). The command line stays English.
- `README.md`: the commands, examples, the rules (also the confirmation of
  decision (a)); the two sentences about `bal_enable_multiverse` (no
  longer read since #168) updated.

**Tests:**

- `tests/test_cli_instalments.py` (new, three will-executors, 58 tests):
  show without a table; set and show give back the same table (also from
  the show answer, a JSON text, a file, a date at 01:00 UTC); the example
  table; 19 bad tables write nothing, each with its message; a delivery
  number as text or decimal; one source only; BASIC, period form and no
  heirs refused; the whole cycle by command (3 + 9 + 27 items, the
  people's names, `will_items`, then coherent); a will with one date has
  no `delivery` field; an earlier delivery 1 or 2 needs the confirmation;
  the reference is the will in force, else the stored table (with its
  words); shares that arrive earlier (rows swapped, delivery 3 removed)
  need the confirmation, shares moved later do not; a later delivery 2:
  the message, then `postponed`; an earlier delivery 2: free rebuild,
  reason "The date of delivery 2 changed"; shares moved: reason "luca
  (delivery 2)"; luca out of delivery 1: "luca (delivery 1) was removed";
  `bal_delivery_set` and `bal_delivery_form` keep the 28 days, the period
  refused; the heir commands' note and the stop of prepare; clear: the
  confirmation, the heirs' amounts, one delivery one day earlier; the
  stored heirs untouched; Electrum's parser (`--table` JSON, bools); the
  Check sentences with the delivery, and unchanged for one date.
- `tests/test_cli_commands_registered.py`: the three commands, and the help
  of every `bal_*` command printed (a lone "%" broke it).
- `tests/test_cli_window_guard.py`: `instalments_show` is a read; the
  snapshot includes the delivery settings (the table).
- `tests/test_cli_delivery_commands.py`: `bal_delivery_show` has `staged`
  and `deliveries`.

**Verification:** `ruff check bal/ tests/`: all checks passed. Whole
`tests/` directory: normal order 2716 passed (26 minutes), files in
reverse order 2716 passed (27 minutes); golden files unchanged. Mutation
check on a copy of the code: 37 rules broken one at a time, all 37
caught. Independent review (one agent, read only): no crash, no partial
write, no change for wallets without a table; its points fixed (decision
(a), "delivery 1" in the messages, the delivery number, the words without
a will, two docstrings, four more tests); its claim that a failed staged
build loses its reason is not right (`engine/build.py` copies it back).
Scenario harness (full matrix, the 2-coin scenarios 6 times, warn_matrix
synced and not): log identical to the #168 log (135 lines). Test ZIP
`test-zips/ancora_0.8.0-TEST-169-instalments-cli.zip` (sha256
`85d43ce7...6b1c`): `external_zip_test.py` and `smoke_test.py` pass on
Electrum 4.8.2. Live command-line test on Electrum 4.8.2 (the ZIP's plugin,
an offline daemon, a throw-away testnet wallet, three fake will-executors,
removed afterwards): the help prints; a table with a 19-day gap refused;
the owner's table stored; prepare, 39 transactions signed (3 + 9 + 27),
the people's names, no "§" in any answer; broadcast says "You are
offline" for each will-executor; `bal_delivery_set` 2031-02-15 refused
(28 days); an heir added: the note, prepare stopped with the commands;
delivery 2 later: the message, prepare `postponed`; clear refused without
the confirmation, done with it; prepare: one delivery, one day earlier.

Seen in passing, not changed here: with the command line the will stays
unsigned between prepare and sign, and the "save to local history"
convenience logs "save to history failed" for the items of deliveries 2
and 3 (their parent is not in the wallet), log only;
`bal_delivery_form fixed --date` can move the date earlier without a
confirmation (since #127).

**Outcome:** DONE.

## 170. The window "Inheritance in several deliveries" (the table of the deliveries in Electrum's window)

**Date:** 2026-10-20

**Goal (step M4a of `context/MULTIVERSE-PLAN.md`; owner answers of
2026-10-20):** the table of the deliveries of #168 / #169 can be written,
changed and removed in Electrum's window, with the same rules as the
command line. Owner answers to the M4 plan: (1) M4 in two steps, M4a this
window, M4b the rest; (2) the window opens from the ADVANCED wizard (a third
choice next to "Fixed date" / "Period") and from Tools in the Will tab;
(3) with a table the date fields are read-only, the dates are changed only
in the window; (4) an heir renamed keeps its shares in the table; (5) one
message only, in delivery 1; (6) percentages only in the window (the real
amounts per delivery are in the Future view after signing). The name, chosen
by the owner: "Eredità in più consegne" / "Inheritance in several
deliveries" / "Herencia en varias entregas" (it replaces "Staged
inheritance" in the texts: the command line's refusal in BASIC, its help
and the README). Three drafts of the window, the owner's choices: the
table HORIZONTAL (the deliveries from left to right, as time in the Future
view and the chain; the heirs in rows), the shares of the heirs bigger
than the check sums (the totals small and grey, 100 % in green), at most 3
deliveries today and everything visible; later up to 12 deliveries (owner,
2026-10-20): only the deliveries scroll sideways then, with a bar at the
bottom, the names and the totals stay in place.

**What changed:**

- `bal/core/delivery.py`: the rules the command line and the window share
  (moved from `bal/cli/controller.py`, same behaviour): `schedule_of`,
  `schedule_now`, `first_earlier`, `first_earlier_date` (owner decision
  (a) of #169: what would arrive earlier), `later_than_in_force`,
  `too_close_to_second` (the 28 days of delivery 1), and
  `transactions_count` (N + N^2 + ... + N^K: the window's line, ready for
  the future limit of 5000 transactions, which depends on the
  will-executors). The rules of the table raise `TableRuleError` (a
  `ValueError`): `str(e)` is the English text (the command line), its
  template is in the catalogs (`N_`, a local marker: the module stays
  standard library only) and `rule_text` translates it for the window.
  Six of these texts lose the code word "instalments: " (#132: the owner
  never reads "instalment"; the catalogs are checked for it): e.g. "the
  shares add up to 95%, not 100%" (also on the command line).
- `bal/engine/instalments.py`: `dates_in_force` (moved from the command
  line), `rename_heir` (owner answer 4: the shares follow the new name;
  nothing without a table, for a name not in it or onto one already in
  it); the "Details" of the mismatch message are translated.
- `bal/cli/controller.py`: uses the shared rules (`_dates_in_force`,
  `_schedule_now` and `_first_earlier` removed; `_check_table_gap` calls
  `too_close_to_second`); the answers are unchanged. `bal/cli/commands.py`:
  the help names the inheritance in several deliveries.
- `bal/gui/qt/deliveries.py` (new): the window. A line with a dot per
  delivery, "Delivery n", a date picker (a date changed is 00:00 UTC of the
  day, as on the command line; one not touched keeps its exact time) and
  the time since the delivery before ("+2 years", "+4 years and 11
  months"); one row per heir with its share (bold, bigger), "%" next to
  it; the totals per heir and per delivery, 100 % ✓ in green. Every row
  has a fixed height and every delivery column a fixed width, so the three
  parts (names, deliveries, totals) line up; the deliveries are in a
  scrolling area that shows 3 columns. "Add a delivery" (12 months after
  the last, 0 %), "Remove the last delivery", "Example table" (33 / 33 /
  34 %, one year apart); grey lines: the transactions to sign ("With 3
  will-executors: 39 transactions to sign (3 + 9 + 27)", counted as the
  build does: selected and valid will-executors, plus the copy without a
  will-executor when that option is on), "Each will-executor takes its fee
  at every delivery", and, with a message, "The message in the will is in
  delivery 1". One problem at a time under the table, from
  `validate_instalments`, translated (in English the code word
  "instalments:" is left out); Save only without a problem. A delivery
  later than the will in force says that the next Check asks for the paid
  invalidation (D6). Save: what would arrive earlier is asked first (Yes /
  No, No is the default), then `context.set_instalments`. "Back to one
  delivery": asked (the rest of the estate would arrive at delivery 1, the
  heirs' amounts count again), then `context.clear_instalments`. Opened
  without a table: the example; with heirs changed since the table: a
  column of 0 % for an heir added, an heir removed left out, a note to
  check the shares. With a table, the date fields show "10/03/2030 (+2
  deliveries)" (`staged_field_text`), read-only, with a tooltip saying
  where the dates are changed.
- `bal/gui/qt/widgets.py`: the wizard's delivery form has "Several
  deliveries" (ADVANCED, a fixed date; with a table it is selected and
  "Edit the deliveries…" opens the window; "Fixed date" asks to go back to
  one delivery; the period is off with a table; BASIC shows a wallet's
  table as it is, without changing it); `show_period` takes a tooltip and
  `apply_delivery_form` shows the text of a table; the Will Details name
  "luca (delivery 2)", never "luca§2".
- `bal/gui/qt/lists.py`: Tools in the Will tab, "Inheritance in several
  deliveries…" (ADVANCED only, checked each time the menu opens); the
  Heirs list's Delete asks to open the table when it no longer fits. The
  Will tab list (owner's ZIP test): in a will in deliveries every column
  of a delivery-2 or delivery-3 row steps right by two figure spaces per
  level (the tree indented only the first column); "Copy", the tooltips
  and the search keep the plain values; a will with one date is
  unchanged.
- `bal/gui/qt/window.py`: `ask_deliveries` (the Yes / No questions of the
  window of the table go through the window, as `ask_renew_will`; in the
  test window of `tests/flowkit.py` they are answered No: a real box, never
  clicked in a test without a screen, kept the first suite run waiting for
  2 hours); `open_deliveries_editor` (refused with a message
  in BASIC, with the period form, without heirs, with the settings on
  hold); `table_mismatch` (Prepare with a table that no longer fits: in
  ADVANCED the question "Open the table of the deliveries now?");
  `offer_table_update` (after the heir window, an import or a delete, when
  the heirs changed and the table no longer fits: asked once, after the
  last "Add another heir"); the heir window says under the amount that it
  is not used with a table, and a rename carries the shares
  (`instalments.rename_heir`).
- `bal/gui/qt/dialogs.py`: the Check's "Heir" rows name each person once;
  the DUST rows name "luca (delivery 2)"; a rename is said once with the
  persons' names (`person_renames`; also in the "What changes" box); the
  Check stopped by a table that no longer fits has "Open the table of the
  deliveries" (ADVANCED).
- Catalogs: 53 new texts (the window, the 17 rules of the table), Italian
  and Spanish.
- `README.md`: the window; the new name.

**Tests:**

- `tests/flowkit.py`: `ask_deliveries` of the test window (recorded as a
  "question" popup, answered No).
- `tests/test_check_button.py`: the Tools menu has the new entry after
  "Merge".
- `tests/test_gui_deliveries.py` (new, three will-executors, 36 tests): the
  example table and its Save, the date fields after it; the stored table,
  its totals, gaps, no bar with 3 deliveries; 6 columns (maximum raised in
  the test) scroll, names and totals outside; four problems (28 days, not a
  share, 95 %, three decimals), Save off; the decimal comma and an empty
  cell; add and remove; an earlier date asked (No: nothing stored; Yes:
  stored); shares moved earlier asked; a later date not asked; a date
  later than the will in force said; back to one delivery asked, then the
  date editor again; heirs changed since the table; the wizard's choice
  (ADVANCED only, not with the period form, opens the window, Cancel keeps
  "Fixed date", "Fixed date" with a table asks); the Tools entry (ADVANCED
  only); the refusals; "+1 delivery"; a rename in the real heir window
  keeps the shares, nothing asked, the note shown; no note without a
  table; an heir added or deleted asks to open the table (not when
  nothing changed, not in BASIC, not after the heir window closed
  unchanged); Prepare's question; the transactions to sign (39; 84 with
  the copy without a will-executor); the flows' Prepare asks to open the
  table and the Check stopped by it has the button; the Check's rows and
  a rename (said once, "luca → marco") never show "§"; the Will Details.
- `tests/test_gui_will_versions_list.py`: each delivery steps right in
  every column, "Copy" plain, one date unchanged.
- `tests/test_core_delivery_instalments.py`: the rule errors (English kept,
  translatable, a broken translation falls back), every rule raises it,
  the schedules, what arrives earlier, later than the will in force, the
  28 days of delivery 1, the transaction counts (3 x 3 = 39, 2 x 11 = 4094,
  2 x 12 = 8190, 3 x 7 = 3279, 3 x 8 = 9840).

**Verification:** `ruff check bal/ tests/`: all checks passed. Whole
`tests/` directory, normal order: 2769 passed (33 minutes; a first run had
7 failures, fixed above: the Tools menu of the test toolbar and the word
"instalments" in the catalogs). Golden files unchanged. Mutation check on
a copy of the code: 40 rules broken one at a time (the shared rules, the
engine, the window, the wizard, Tools, the heirs, the Check texts), all 40
caught (two needed new tests). Scenario harness (full matrix, the 2-coin
scenarios 6 times, warn_matrix synced and not): log identical to the #169
log (135 lines). Screenshots of the window (offscreen, English and, from
the ZIP's catalog, Italian) checked: 3 deliveries fit, 6 scroll with the
names and totals in place. Test ZIP
`test-zips/ancora_0.8.0-TEST-170-several-deliveries.zip` (sha256
`9300450e...c808`): `external_zip_test.py` and `smoke_test.py` pass on
Electrum 4.8.2. Owner's ZIP test (2026-10-20, a new wallet, a base will
then several deliveries, delivery 1 moved earlier, 4 will-executors):
the will in force has 4 + 16 + 64 = 84 transactions, rebuilt from the
log's pushes as a complete chain; we.safe21.io holds its 21, WAITING,
each with its fee. Seen there, for the next steps (not changed here):
the will-executors answer "429 Too Many Requests" to the check of every
transaction (21 per server at once), so a 2nd or 3rd Check finishes the
verification; old delivery-3 copies shown as separate "Invalidated"
versions; "txid is none" log blocks from `Will.normalize_will` on
unsigned children. The "no server" message after the date change was the
owner's DNS failing at that moment.

**Outcome:** DONE.

## 171. The will export window works for a will too big for QR codes

**Date:** 2026-10-20

**Goal (owner's ZIP test of #170, 2026-10-20):** Tools > Export on the
owner's will in several deliveries (about 244 transactions with all its
versions) stopped with "BBQR transfer demands too many parts" and the
window did not open: it prepared the animated QR codes at once, also for
a file, and the BBQR format allows at most 1295 frames. Owner's decision:
when the will is too big for the QR codes, the QR and audio choices
disappear and only the file stays.

**What changed:**

- `bal/gui/qt/dialogs.py`, `WillExportDialog`: the QR frames of the
  selection are prepared in one place (`_refresh_transports`, at the
  start and after a change of filter or of "Whole will"). When they do
  not fit (`AnimatedQrError`), "QR Code" and "Audio" are hidden, "File"
  is selected and a note says "This will is too big for QR codes and
  audio: export it as a file. (The filter "Valid" makes it smaller.)";
  when the selection fits again, they come back. `BalQrExportWidget`: a
  QR size at which the will needs too many frames is not taken (the size
  before stays). Owner, after trying it: the window starts on the filter
  "Valid" (the will in force: what a copy, the offline signing or another
  device need), "All" only when nothing is valid; in BASIC mode only
  "File" is offered (QR and audio are ADVANCED tools; the guided offline
  route of BASIC uses the file).
- Catalogs: the note, in Italian and Spanish.

**Tests:** `tests/test_gui_export_dialogs.py`: a will too big for the QR
codes opens on "File" only, with the note, and QR and audio come back
when the filter makes it fit; a QR size too small is not taken; the
window starts on "Valid", else "All"; BASIC offers the file only.
`tests/test_gui_qr_transfer.py`: the two filter tests start on "Valid".

**Verification:** `ruff check bal/ tests/`: all checks passed. Whole `tests/` directory,
normal order: 2773 passed (25 minutes). Golden files unchanged. Scenario
harness: log identical to the #169 log (135 lines). Test ZIP
`test-zips/ancora_0.8.0-TEST-171-export-only-file.zip` (sha256
`bf79e240...00d1`): `external_zip_test.py` and `smoke_test.py` pass on
Electrum 4.8.2. Owner's ZIP test of the first version (2026-10-20): the
window opens on his will (214 items), "File" only with the note; the
filter "Valid" brings the QR codes back; the export file was written. Owner's ZIP test of this version: ADVANCED
starts on "Valid" with File, QR and audio, "All" leaves the file only;
BASIC offers the file only with both filters.

**Outcome:** DONE.

## 172. The Check asks the will-executors a few at a time

**Date:** 2026-10-21

**Goal (owner's ZIP test of #170, 2026-10-20):** on the owner's will in
several deliveries (84 transactions, 4 will-executors) the Check sent one
`searchtx` per transaction to every will-executor at once; the servers
answered 429 Too Many Requests and only the 2nd or 3rd Check verified
everything. Reading the code showed worse: a 429 was taken as "not
received", so the copy was marked CHECK_FAIL and sent again. Owner's
decisions: 3 requests at a time per will-executor; at most 2 minutes for a
Check; a 429 never makes a copy "failed"; the waiting must be clearly
visible and explained, so nobody worries.

**What changed:**

- `bal/core/willexecutors.py`:
  - `check_transactions_parallel`: at most `CHECK_PER_SERVER` (3)
    requests at a time to one will-executor (the next one leaves when one
    answers), the will-executors in parallel, at most `CHECK_MAX_WORKERS`
    (16) in all, sent in turn so a busy will-executor does not take every
    worker. At the deadline the requests running AND the ones not sent yet
    are reported as timed out. `on_busy(url, seconds)` reaches the calling
    thread; `should_stop()` stops the waiting when the window is closed.
  - `check_transaction`: a 429 (`busy_pause`) gives a pause (the server's
    Retry-After, else 2 s, 4 s, 8 s; at most 10 s) and a new try, at most 3
    times; still busy: `NO_ANSWER` (the copy stays sent, #147). Other
    errors are raised as before.
  - `check_deadline`: 20 s for a small will; 5 s for each round of
    requests, the rounds being the larger of the busiest will-executor (3
    at a time) and all the requests (16 at a time); at most 120 s. The
    owner's will: 35 s.
- `bal/engine/verify.py`: `verify_will` uses that deadline (or the one
  given by the window) and passes `on_busy`.
- `bal/engine/push.py`: the copies of a will-executor that answers
  "already present" are checked with the same parallel check, no longer
  one by one. Owner's test: we.divinaly.support answered "already
  present" and no check, 31 copies x 16 s = 8 minutes with the window still
  on "0 s / 20 s"; now at most the deadline (55 s for 31 copies), shown.
- `bal/gui/qt/dialogs.py`: `check_wait_lines` (shared with the window):
  "Copies checked: X of Y · N s (at most N s)" in bold, always; for a long
  Check, in grey, "Many transactions: the will-executors are asked a few
  at a time, so as not to overload them."; in orange, "<server> is busy:
  new try in N s.". Shown under the Verification step and while the
  "already present" copies are checked during the Sending step.
- `bal/gui/qt/window.py`: the right click "check" shows the same lines,
  with the grown deadline; its waiting text wraps at a normal width.
- Catalogs: the new texts, in Italian and Spanish.

**Tests:** `tests/test_check_throttle.py` (new, 17, three fake
will-executors): never more than 3 at a time per will-executor; the
deadline reports the running and the not sent; `on_busy` on the calling
thread; 429 then an answer; still busy is `NO_ANSWER`, not a failure;
Retry-After kept but capped; another error raised; `verify_will` with the
real parallel check keeps a busy will-executor's copies sent; the deadline
grows (owner's will 35 s, 10 x 30 copies 95 s, 30 x 1 copy 20 s, at most
120 s); the Check window, a small will and the right click window show the
lines; the "already present" copies are no longer checked one by one,
closing the window stops them, the Sending step shows them.
`tests/test_engine_push.py`, `tests/test_verify_in_dialog.py`,
`tests/flowkit.py` (the fake waiting window has a `message_label`) and the
golden file `copies/menus_prepare_sign_broadcast_check` follow the new
texts. Mutation check: 12 of 12 deliberate faults caught.

**Verification:** `ruff check bal/ tests/`: all checks passed. Whole `tests/`
directory, normal order: 2790 passed (32 minutes). Golden files: only the
one above changed (the waiting window text). Scenario harness: log
identical to the #171 log (135 lines). Test ZIP
`test-zips/ancora_0.8.0-TEST-172-check-throttle.zip`: `external_zip_test.py`
and `smoke_test.py` pass on Electrum 4.8.2. Owner's ZIP test (2026-10-21,
mainnet test wallet, 5 will-executors): 124 copies verified in one Check
in about 33 s (limit 55 s); the servers answered 429 fourteen times, every
new try succeeded; the waiting lines read clearly. The "already present"
fix was not tried in the ZIP yet (the same test wallet then hit the
separate local-history problem): owner's OK to commit with it covered by
the tests only.

**Outcome:** DONE.

## 175. Saving an unsigned will in several deliveries no longer breaks the wallet's balance

**Date:** 2026-10-21

**Goal (owner's log, 2026-10-21 08:17:57Z, ZIP of #172, mainnet test
wallet):** with "Save in the history" on (`bal_save_history`, default on),
the save of the will into the local history logged 80 errors ("trying to add
tx to db with inconsistent txid: ... != None", then "spending tx not in
wallet db"), and from then on every refresh of the window failed with an
AssertionError in Electrum's `get_balance`. Analysis, reproduced in a test:
an unsigned transaction has a txid only while every input is known to be
segwit; a later delivery spends an unsigned will transaction, and that
knowledge is not in the text Electrum stores, so the stored copy has no txid
(36 of the 39 transactions of a will in three deliveries with three
will-executors). Electrum refused the copy only AFTER writing the spent
coins and the outputs into the wallet index, so the index pointed at
transactions the wallet did not have. The funds and the signed will were
never touched (a signed transaction is never saved to the history), and
Electrum clears such entries when it opens the wallet
(`remove_local_transactions_we_dont_have`): with the option off, a restart
repairs the wallet.

**What changed:**

- `bal/core/will.py`:
  - `_survives_reparse(tx)`: True when the copy Electrum stores keeps the
    txid (`tx_from_any(str(tx)).txid() == tx.txid()`).
  - `save_valid_transactions_to_history`: a transaction that does not
    survive is not given to Electrum; one INFO line counts them ("N
    unsigned will transactions not saved to the local history: ..."). The
    first delivery is saved as before.
  - `_undo_partial_add(wallet, txid)`, safety net: when
    `add_transaction` still fails, what Electrum already wrote for a txid
    the wallet does not hold is removed (`adb.remove_transaction`, which
    works without the transaction), then the error is raised as before.

**Tests:** `tests/test_history_multiverse.py` (new, 4, three fake
will-executors, throw-away testnet wallet): a will in three deliveries,
unsigned, saved: no entry of the wallet index points at a missing
transaction, the balance reads, no ERROR, the 36 later transactions are not
stored and counted in the INFO line, the first delivery is stored; a will
with one date is saved as before; an add refused half-way leaves no entry;
`_survives_reparse` is True for the first delivery and False for the later
one. Mutation check: 5 of 5 deliberate faults caught.

**Verification:** `ruff check bal/ tests/`: all checks passed. Whole `tests/`
directory, normal order: 2794 passed (29 minutes). Golden files: none
changed. Scenario harness: log identical to the #172 log (135 lines). Test
ZIP `test-zips/ancora_0.8.0-TEST-175-history-reparse.zip`:
`external_zip_test.py` and `smoke_test.py` pass on Electrum 4.8.2. Owner's
ZIP test (2026-10-21, the same mainnet test wallet): at the opening Electrum
removed the 12 broken entries by itself; then several Checks with "Save in
the history" on and off: no AssertionError, no "inconsistent txid", no
traceback, the balance reads. The will there is fully signed, so the skip of
unsigned later deliveries was not exercised in the ZIP: covered by the tests
only.

Seen, not changed here: at each rebuild `normalize_will` logs "txid is none"
for 80 stored items of OLD versions of that will (later deliveries of
invalidated or replaced versions, the same lost txid when read back); they
are marked ERROR and skipped. Present before #172; a later step.

**Outcome:** DONE.

## 174. Going back to BASIC asks first; the mouse wheel no longer changes the user type

**Date:** 2026-10-21

**Goal (owner's report, 2026-10-21):** with the plugin Settings window open
in ADVANCED, scrolling with the pointer over "User Type" (or a wrong click)
put the mode back to BASIC without a word, and the advanced tools
disappeared. Owner decisions: the mouse wheel does not change the user type;
leaving ADVANCED asks "Go back to BASIC mode? The advanced tools will be
hidden. The will does not change." with Yes / No, No by default (and on
Escape); on No nothing changes. The same in the Settings dialog, its reset
buttons and the wizard.

**What changed:**

- `bal/gui/qt/common.py`:
  - `confirm_basic_mode(parent)`: the question box; True on Yes.
  - `is_advanced_mode(plugin)`: the mode in force.
  - `NoWheelComboBox`: a combo that leaves the wheel to the window, so
    scrolling over it scrolls the window.
  - `switch_user_type`: from ADVANCED to BASIC it asks first; on No it
    returns False and touches nothing (no windows refreshed, the delivery
    form not changed). From BASIC to BASIC nothing is asked.
- `bal/gui/qt/plugin.py` (Settings dialog): the "User Type" combo is a
  `NoWheelComboBox`; on No it goes back to ADVANCED. Its reset button and
  "Reset to Default Setting" no longer write the user type themselves: the
  combo's handler writes it after the question, so on No ADVANCED stays and
  the other settings are still reset.
- `bal/gui/qt/widgets.py` (wizard): the mode selector is a
  `NoWheelComboBox`; on No it goes back to ADVANCED and the delivery form is
  left as it is (a fixed date chosen in ADVANCED stays fixed).
- Catalogs: `bal.pot`, `it_IT`, `es_ES` refreshed; four new entries
  translated ("BASIC mode", the question, "Yes", "No"). In Italian and
  Spanish the question says BASE / BÁSICO, as the "User Type" combo does.

**Tests:** `tests/test_basic_mode_confirm.py` (new, 11): the question box
(text, Yes / No, No by default and on Escape); No keeps ADVANCED and touches
nothing; BASIC to BASIC asks nothing; the wheel does not change "User Type"
in either mode (and a plain combo did, the control); the Settings combo, its
reset button and "Reset to Default Setting" with No then Yes; the wizard
with No then Yes (three fake will-executors, throw-away testnet wallet); a
fixed date chosen in ADVANCED stays on No. `tests/conftest.py`: fixture
`yes_to_basic` (answers Yes without showing the box), used by 8 earlier
tests that leave ADVANCED and check what follows (in
`test_advanced_period_default`, `test_auto_rebuild_hidden`,
`test_gui_delivery_colours`, `test_gui_delivery_form`,
`test_gui_will_versions_list`). Mutation check: 10 of 10 deliberate faults
caught.

**Verification:** `ruff check bal/ tests/`: all checks passed.
`tests/test_translations.py`: 4 passed. Whole `tests/` directory, normal
order: 2804 passed (37 minutes; the eleventh new test was added after it
and passes). Golden files: none changed. Scenario harness: log identical to
the #175 log (135 lines). Test ZIP
`test-zips/ancora_0.8.0-TEST-174-basic-confirm.zip`:
`external_zip_test.py` and `smoke_test.py` pass on Electrum 4.8.2. Owner's
ZIP test (2026-10-21): OK.

Seen, not changed here (owner, at the ZIP test): switching between BASIC
and ADVANCED takes a few seconds to redraw the window on the mainnet test
wallet. Each switch runs the window's full refresh over every stored will
item (685 there, mostly old versions); not measured yet, a later step.

**Outcome:** DONE.

## 173. A read-only window shows the plugin's steps as command lines

**Date:** 2026-10-21

**Goal (owner's idea, 2026-10-21):** a window in the style of AutoCAD's
command line that shows what the plugin is doing, written as the `bal_*`
commands of the command line. Owner decisions: read-only (nothing can be
typed: a command typed in Electrum's Console can overwrite a `bal_*`
command, #148), black background, in BASIC and ADVANCED alike; the Tools
entry reads "CLI command trace…" ("Comandi CLI…" in Italian), the subtitle
"(CLI commands, view only)" ("(comandi CLI in sola visualizzazione)");
last entry of Tools, under its own line; the trace lives only while
Electrum is open ("Save to file…" keeps a copy).

The GUI never runs the `bal_*` commands: the window and the command line
call the same engine bricks, and each brick writes the line of the command
that runs the same step.

**What changed:**

- `bal/core/trace.py` (new, no Qt): the trace. The last 5000 lines in
  memory, numbered, thread-safe, never raises. Kinds: command, request,
  answer, ok, warning, error; a "Full" flag for the details. Never a
  secret: no password, seed, key or raw transaction is passed to it; a
  full txid is shortened (`a1b2c3d4…9f0e`), and a long hex text or an
  extended private key that would slip through is hidden. Plain text for
  Copy / Save.
- `bal/core/willexecutors.py`: `send_request` writes each request to a
  will-executor (method, server and path; for `pushtxs` how many
  transactions, for `searchtx` the shortened txid) and its answer with the
  seconds: a short text answer as the server gives it ("thx", "already
  present"), "answer with a transaction" (never the transaction), an HTTP
  error with its code (429: busy), no answer in time and the new tries.
  The copies' checks (`searchtx`) and the new tries are "Full" details.
  `check_transaction` writes the busy (429) pauses; `ping_servers_parallel`
  writes `bal_willexecutors_ping` and how many answered.
- Engine bricks, each a command line then its outcome:
  - `bal/engine/context.py` `check_will`: `bal_will_check`, then "the will
    is coherent" or "not coherent:" with the command line's reason
    (`will_change_message`);
  - `bal/engine/build.py` `build_will`: `bal_will_prepare`, then the valid
    transactions and the first delivery date (UTC), or the error;
  - `bal/engine/sign.py` `sign_will`: `bal_will_sign`, then how many were
    signed now, or which one failed and the error's text;
  - `bal/engine/push.py` `push_will`: `bal_will_broadcast` with how many
    will-executors, then the count of each outcome and each refusal;
  - `bal/engine/verify.py` `verify_will`: `bal_will_check_executor` with
    how many copies and the deadline, then the count of each outcome;
  - `bal/engine/invalidate.py`: `bal_will_invalidate` for the build of the
    invalidation, and Electrum's own `broadcast` (the plugin has no command
    for it) for its sending;
  - `bal/engine/merge.py` `merge_will`: `bal_will_import_merge`, then how
    many copies were merged.
- `bal/gui/qt/trace_window.py` (new): the window. Black, read-only, fixed
  width font, colours by kind; reads the new lines every 0.3 s and follows
  them when the view is at the bottom; Pause / Resume (the lines keep
  coming and appear at Resume), Copy, Save to file… (plain text,
  `ancora-cli-trace-<date>-<time>.txt`), Clear, Detail Normal / Full,
  Close. One window per Electrum window.
- `bal/gui/qt/window.py`: `BalWindow.show_trace`. `bal/gui/qt/lists.py`:
  the Tools entry, last, under a line, in BASIC and ADVANCED.
- Catalogs: `bal.pot`, `it_IT`, `es_ES` refreshed; 45 new entries
  translated.

**Tests:** `tests/test_cli_trace.py` (new, 30): the trace (numbers, the
oldest lines dropped, clear, plain text, never a secret, never an error);
the requests (`pushtxs` with the count and never the transactions,
`searchtx` as a detail with the short txid, a busy will-executor and its
new try, no answer in time, an HTTP error, the ping); a real Check on a
throw-away testnet wallet with three fake will-executors (the five
commands in order with their outcomes, then a second Check "coherent";
neither the password nor a transaction in the trace) and the same
`bal_will_check` line from the command line; the window (black,
read-only, subtitle, colours, new lines, Pause, Detail, Copy, Save,
Clear, the timer stops at Close, one window); the Tools entry in BASIC
and ADVANCED; Italian. `tests/test_check_button.py`: the Tools order now
ends with the line and the new entry. Mutation check: 14 of 14 deliberate
faults caught.

**Verification:** `ruff check bal/ tests/`: all checks passed.
`tests/test_translations.py`: 4 passed. Whole `tests/` directory, normal
order: 2835 passed (27 minutes). Golden files: none changed. Scenario
harness: log identical to the #174 log (135 lines). Test ZIP
`test-zips/ancora_0.8.0-TEST-173-cli-trace.zip`: `external_zip_test.py`
and `smoke_test.py` pass on Electrum 4.8.2. Owner's ZIP test
(2026-10-21): OK.

Seen, not changed here (owner, at the ZIP test):
- the scroll bar shows only when the lines no longer fit the window (Qt's
  default);
- the button "Clear" reads "Cancella" in Italian, Electrum's own
  translation of "Clear", which wins over the plugin's "Svuota";
- with several wallets open their lines share one trace, without the
  wallet's name.

**Outcome:** DONE.

## 176. A new delivery date uses a will-executor's new fee address; no more "Will-Executor excluded: Skipped"

**Date:** 2026-10-21

**Problem (the owner's ZIP test of #172, log 2026-10-21 07:16 UTC):** the
Check showed a red row "Will-Executor excluded: Skipped", which named no
will-executor and no reason.
- `we.bitcoin-after.life` gave a new fee address (it changes with the
  user's IP, #141) after it had received its copies.
- The owner then moved the delivery date one day earlier (17 -> 16
  October 2031): a new will.
- The build kept the old fee address for that will-executor
  (`Will.received_terms`, #141), because `Will.same_payments` compared
  the coins and the outputs but not the date.
- The new copies (new date, so new transactions, not received yet) had
  the old address; the Check right after the build
  (`check_willexecutors_and_heirs`) expects the new address for a copy
  not received, raised `WillExecutorTermsChanged`, and the dialog wrote
  its fixed text "Skipped" in red.
- The flow went on anyway: it signed and sent the new copies with the
  old address (the will-executor accepts every address it ever gave).
  The red row was a false alarm, and the copies did not use the new
  address as #141 says ("when the copy changes anyway, it is built again
  with the will-executor's new data").

**What changed:**

- `bal/core/will.py` `Will.same_payments`: the same coins, the same
  outputs **and the same date**. A new delivery date is a new copy, to
  sign and send again, so it is built with the will-executor's new fee
  address or fee; the build and the Check agree again. A received copy
  whose date does not change is still kept as it is (#141, unchanged).
- `bal/core/messages.py`: new `excluded_after_build_message(e)`, the text
  of the "Will-Executor excluded" row right after a build: for a new fee
  address or fee the owner's #141 sentence (`executor_terms_message`),
  else "{host}: not in the will just prepared".
- `bal/gui/qt/dialogs.py`: that row uses it, in orange (a warning: the
  flow goes on and signs and sends) instead of "Skipped" in red. The
  report already lists it among the warnings. With the fix above the row
  should not show in normal use.
- Catalogs: `bal.pot`, `it_IT`, `es_ES` refreshed; 1 new entry (Italian:
  "{}: non è nel testamento appena preparato"; Spanish: "{}: no está en
  el testamento recién preparado"). Texts and colour approved by the
  owner (2026-10-21).

**Tests:** `tests/test_executor_fee_address.py`, 4 new (14 in all; three
fake will-executors, each with its own fee address):
- the owner's case: every copy received, WE0 gives a new address, the
  date moves 10 days earlier; no "excluded" row, the three copies have
  the new date and are received, WE0's has its new address, WE1's its
  own, and the next Check is clean;
- `Will.same_payments` is False for the same coins and outputs on
  another date;
- the row's texts (both sentences, never "Skipped");
- a will-executor still missing right after a build: one orange row
  with its host, among the report's warnings.
Mutation check: 4 of 4 deliberate faults caught (date ignored again,
"Skipped" back, the #141 sentence lost, red again).

**Verification:** `ruff check bal/ tests/`: all checks passed.
`tests/test_translations.py`: 4 passed. The 67 test files near the
change: 1675 passed (15 minutes). Whole `tests/` directory, normal
order: 2839 passed (36 minutes). Golden files: none changed. Scenario
harness: log identical to the #173 log (135 lines). Test ZIP
`test-zips/ancora_0.8.0-TEST-175-executor-terms.zip` (built before the
step got its number): `external_zip_test.py` and `smoke_test.py` pass on
Electrum 4.8.2.

**Owner's ZIP test (2026-10-21), read in his Electrum log:** after Pings,
`we.divinaly.support` gave a new fee address; the owner moved the
delivery date one day earlier and pressed Check. Log: "will-executor
copies changed anyway, built with the new payment data:
['https://we.divinaly.support']", the will coherent right after the
build, signed and sent to the five will-executors, the next Check
clean; a second try the same. Seen: a message stayed on screen only
half a second (which one is not in the log; not changed here).

**Outcome:** DONE.

## 177. No more "txid is none" in the log when a will with more delivery dates is built

**Date:** 2026-10-21

**Problem (the owner's Electrum logs, 20 and 21 October 2026):** every
build of a will with more delivery dates wrote, for each later delivery,
five ERROR lines ending with "txid is none" (360 lines in the session of
21 October 17:42 UTC, from 20 October 11:55, when the tests of wills with
more delivery dates began). The will was right: it was signed, sent and
verified, and the next Check was clean; the lines were noise only.
- A later delivery spends the change of the delivery before it, which is
  not signed yet.
- `Will.normalize_will` completes every item with the wallet and with the
  will itself (`add_info_from_will`: an input that spends another item's
  output gets that output's address, value and script), then checks a
  COPY of each item.
- The copy (`WillItem(...)`) re-parses the transaction, so that input lost
  its data again; Electrum cannot compute the txid of an unsigned
  transaction with such an input, and `normalize_will` logged the item
  (`##########`, its key, the item, its transaction, "txid is none").
- It happened twice per build (`Will.update_will` and
  `engine/build._build_will` both call `normalize_will`): 72 lines for a
  will of 3 + 9 + 27 items with three will-executors.
- Not caused by deleting rows in the List: the lines came also for the
  transactions just built, half a second after they were built.

**What changed:**

- `bal/core/will.py` `Will.normalize_will`: the copies are completed from
  the will of copies too (`add_info_from_will` on each copy), so their
  txid is computed and equals their key. A txid that still cannot be
  computed (an input whose previous output is really missing) gives one
  WARNING line with the item's key instead of the five ERROR lines.
  Docstring added.
- Seen, not changed: `normalize_will` works on its copies, so its branch
  for an item whose txid differs from its key (`to_add`, `to_delete`)
  never reaches the caller's will. No effect today (the items are stored
  under their txid); a separate step if the owner wants it.

**Tests:** new `tests/test_normalize_will_chain.py`, 4 tests (three fake
will-executors, each with its own fee address; the will with three
delivery dates of `test_multiverse_chain_rebuild.py`):
- the first build logs no "txid is none" and no "cannot be computed";
- a rebuild with the first date 30 days earlier: the same;
- at each `normalize_will` of a build, every completed copy has the txid
  of its key, also for the third delivery;
- the 9 second deliveries whose first delivery is removed: one WARNING
  line each, no ERROR line.
Mutation check: 4 of 4 deliberate faults caught (copies not completed,
the five ERROR lines back, ERROR instead of WARNING, no line at all).

**Verification:** `ruff check bal/ tests/`: all checks passed.
`tests/test_normalize_will_chain.py` and `tests/test_multiverse_chain_rebuild.py`:
19 passed. The 74 test files near the change: 1743 passed (20 minutes).
Whole `tests/` directory, normal order: 2843 passed (30 minutes). Golden
files: none changed. Scenario harness: log identical to the #176 log
(135 lines). Test ZIP `test-zips/ancora_0.8.0-TEST-177-normalize-txid.zip`:
`external_zip_test.py` and `smoke_test.py` pass on Electrum 4.8.2.

**Owner's ZIP test (2026-10-21, 21:06 UTC), read in his Electrum log:** a
will with three delivery dates and five will-executors built, signed,
sent and verified: no "txid is none" line and no `##########` block (72
lines per build before). 24 WARNING lines "cannot be computed" remain,
one per item: old later deliveries of a version built that morning whose
first delivery is no longer in the will (orphans, maybe left by deleting
rows in the List; the List does not log its deletions). They were hidden
in the noise before; they are the open point D (orphan descendants), not
changed here.

Seen in the same test, not changed here (a separate step):
`we.divinaly.support` got its copies but answers "503 Service Temporarily
Unavailable" to every verification. After "Exclude" (the will-executor is
deselected, the copies it got are kept, #93/#137) and after the owner
deleted its 2031 transaction in the List, each Check verifies again one
copy of it still in the will (`b3f3432c...`, probably a later delivery);
nothing is sent again. The Exclude question says it "did not receive your
will", which does not fit a will-executor that got its copies.

**Outcome:** DONE.

## 178. An excluded will-executor is no longer verified; Exclude lasts until the user selects it again

**Date:** 2026-10-21

**Problem (the owner's ZIP test of #177, 2026-10-21 21:06 UTC, a will
with three delivery dates and five will-executors):**
`we.divinaly.support` accepted its copies ("pushed: thx"), then never
answered the check in time (later "503 Service Temporarily
Unavailable"). The owner pressed "Exclude" and, after more Checks,
deleted its 2031 transaction in the List; every Check still asked it
again: "received, verifying...", "Copies verified: 0 of 1".
- "No answer in time" keeps a copy sent and to be verified (#147), so
  the Check (`engine/verify.verify_targets`) asked it at every run.
- "Exclude" keeps the copies a will-executor already got (it holds them
  on its server; deleting them only here is what #129 forbids), and the
  Check asked the copies of every will-executor, selected or not: the
  copy `b3f3432c...` never answered in time, so it was asked for ever
  and the summary kept "received, to be verified".
- "Exclude" was the plugin's deselection (#137, case 3): selected again
  as soon as the will-executor answered the ping, which divinaly does.
- Nothing was sent again (one sending only, at 21:08 UTC): the Checks
  only verified.

**Owner's decisions (2026-10-21):** the copies of an excluded
will-executor stay in the will (safety: they can be broadcast at the
date; the plugin must know they exist), the Check no longer asks it, the
box says "excluded"; "Exclude" lasts until the user selects it again.

**What changed:**

- `bal/gui/qt/window.py` `exclude_willexecutor`: recorded as the user's
  deselection (`Willexecutors.set_user_choice`), like a deselection in
  the Will-Executor tab: the automatic selection after a download and a
  ping (`Willexecutors.select_after_ping`, #137 case 2) leaves it
  deselected; the user selects it again in the Will-Executors window.
  Exclude now drops only the copies NEVER sent (`engine.verify.ever_sent`,
  made public: no PUSHED, CHECKED, CHECK_FAIL or PUSH_FAIL). Owner's ZIP
  test 2026-10-22 07:14 UTC: divinaly accepted its copies ("pushed: thx"),
  answered "503 Service Temporarily Unavailable" to the check of 25 of
  them (CHECK_FAIL clears PUSHED), and Exclude, which dropped the copies
  "not PUSHED" (#93), forgot 25 copies divinaly holds. A copy sent at
  least once may be on the will-executor's server: it stays.
  A will-executor the plugin deselects because it does not answer the
  ping still comes back when it answers (#137 case 3, unchanged).
  Docstrings of `select_after_ping`, `user_deselected` and
  `download_and_select_willexecutors` updated.
- `bal/engine/verify.py` `verify_targets`: new `selected` (the URLs
  selected now); without `recheck`, a copy of a will-executor not
  selected is not asked. The Check (`dialogs.py`, verification step) and
  the command line `check_transactions` with no ids pass it; the right
  click "check" and `check_transactions` with named ids still ask (the
  user asks for them).
- `bal/core/summary.py` `executor_states`: new state `excluded`, a
  will-executor not selected that received the will, verified before or
  not (before: "to_verify" for ever, or "verified" from an old check:
  owner's ZIP test 2026-10-22 05:02 UTC, divinaly excluded read "received
  and verified", which is no longer true once nobody asks it). It is not
  counted in "x of N" (as
  "deselected", #134): the Will tab summary, the "complete the sending"
  note, the deselected line; it counts as having the will ("No
  will-executor received your will" is not said when it holds it).
- `bal/core/report.py`: its text "excluded: has the will, no longer
  verified"; the report is no longer locked by it
  (`report_block_reason`). `bal/core/build_report.py`: the state is
  known.
- `bal/gui/qt/dialogs.py`: the Sending runs only for the copies of the
  will-executors selected now (the sending already skipped the others):
  a signed, unsent copy of an excluded one, kept since this step, made a
  "Broadcasting 0/0" run and "Sending 1/1" instead of "not needed". The
  Sending step counts an excluded will-executor as received; the
  Verification step does not count it ("2/2", not "2/3"); when every
  will-executor is excluded (all deselected by hand after they received
  the will; Exclude never leaves none) the Verification step says "not
  needed" (Italian "non serviva", the word of the other steps with
  nothing to do; owner's choice) instead of staying grey with no word.
- `bal/gui/qt/widgets.py`: the box shows it grey with "–" and the text;
  `bal/gui/qt/future_view.py`: in the legend's grey states;
  `bal/gui/qt/lists.py`: the row of the will in force names it with the
  deselected ones ("sent and verified 2 of 2 · 1 deselected", no new
  text); the row of each of its copies of the will in force reads "In
  force, excluded" on grey (`theme.EXCLUDED_COLOR` #d6d6d6) instead of
  "In force, to be verified" on teal (owner's ZIP test). New
  `summary.is_excluded` (the same rule, copy by copy); the selection is
  read once per redraw (`WillList._selected_now`, also used by the
  version row). The rows of older versions are history: unchanged. The
  copies under an excluded will-executor's copy stay green when their
  will-executors verified them (owner's question: each row is the copy
  held by one will-executor; the branch matters only if that copy is
  the one broadcast).
- Catalogs: `bal.pot`, `it_IT`, `es_ES` refreshed; 2 new entries
  (Italian: "escluso: ha il testamento, non più verificato", "In vigore,
  escluso"; Spanish: "excluido: tiene el testamento, ya no se verifica",
  "En vigor, excluido"). Texts approved by the owner.

The same rule covers a will-executor deleted from the Will-Executors list
after it received the will: it is not selected, so its copies stay and
are "excluded", no longer verified (before: verified at every Check).

Not in this step (next steps, agreed with the owner): deleting a copy
in the List together with the copies of the later deliveries that spend
it (#179); the text of the Exclude question for a will-executor that got
its copies (#180); the old orphans (D).

Note for the owner's wallet: divinaly was excluded there with the old
rule (the plugin's deselection); after this version it may come back
selected at the first ping: exclude it again, or deselect it in the
Will-Executor window.

**Golden files regenerated (owner OK, 2026-10-22), only these lines:**
- `tests/golden/5e/check/executor_deselected.json` (we1 deselected after
  the will was sent and verified): we1 "verified" -> "excluded",
  Verification "2/2" -> "1/1";
- `tests/golden/5e/copies/all_executors_deselected_after_sent.json`
  (every will-executor deselected after the will was sent and verified):
  we0 and we1 "verified" -> "excluded", Verification "ok 2/2" -> "not
  needed".
- `tests/golden/5e/check/exclude_down.json` (we1 down at the sending,
  then Exclude): its copy (PUSH_FAIL) stays, T4 added; we1 "deselected"
  in the box; every step unchanged (Sending "not needed").
Every other golden file unchanged.

**Tests:** new `tests/test_exclude_received.py`, 9 tests (three fake
will-executors, each with its own fee address; the will with three
delivery dates, 3 + 9 + 27 items; `we2` gets its 13 copies and never
answers the check in time):
- after Exclude the next Check asks it nothing and sends it nothing, its
  13 copies stay; the box says "excluded", Verification "2/2"; the
  summary is green, "2 of 2 received and verified it"; the report is
  available; one more Check asks nothing;
- the owner's ZIP test: copies verified before, then Exclude: "excluded",
  Verification "2/2", summary "2 of 2", nothing asked or sent;
- the owner's ZIP test: no answer in time for half of its copies, an
  error (503) for the other half, then Exclude: all 13 copies stay,
  nothing sent to it again, "excluded", Verification "2/2";
- every will-executor deselected after it received the will: all
  "excluded", nobody asked, Verification "not needed", will in force;
- selected again by the user: the next Check asks its 13 copies;
- `check_transactions` with no ids skips it, with two named ids asks
  them;
- the texts: the box, the Future legend, the List row of the version
  ("2 of 2 · 1 deselected") and the row of its copy ("In force,
  excluded", grey; an older version's row unchanged);
- the rule of `executor_states` and of `verify_targets` on their own.
`tests/test_executor_selection.py`: `test_exclude_then_back_is_selected_again`
became `test_exclude_lasts_until_the_user_selects_it_again` (two downloads
leave it deselected; selected again by the user, it stays selected); the
module docstring follows the new case 2. Two test doubles now hold their
will-executor as selected, as the real objects do:
`tests/test_check_no_answer.py` (`test_cli_check_deadline_keeps_the_item_sent`:
the controller's `willexecutors`) and `tests/test_inheritance_report.py`
(`test_report_only_for_a_verified_will_in_force`: the plugin's list);
without it their copy read "excluded" and the test lost its point.
`tests/test_failed_executor.py`: `test_exclude_deselects_saves_and_drops_the_unsent_copies`
became `test_exclude_deselects_saves_and_drops_only_the_copies_never_sent`
(a copy with PUSH_FAIL or CHECK_FAIL stays, a copy never sent is dropped);
the module docstring follows. `tests/test_core_will_versions.py`
(`test_exclude_keeps_one_version_with_two_copies`): the version in force
keeps 3 copies (we1's, PUSH_FAIL, stays), not 2.
Mutation check: 19 of 19 deliberate faults caught (the Sending counts
every will-executor, Exclude drops the
copies not PUSHED again, the List row ignores the exclusion, is not
grey, keeps the old text, the selection not read at the redraw, a
verified copy of a deselected will-executor stays "verified", the Verification step grey
when every will-executor is excluded, the Check asks every
will-executor, `verify_targets` ignores the selection, no "excluded"
state, Exclude back to the plugin's deselection, the Verification step
or the summary row or the List row counts it, the report locked by it,
the command line asks every will-executor, the box text or the legend
entry missing).

**Verification:** `ruff check bal/ tests/`: all checks passed.
`tests/test_translations.py`: 4 passed. Whole `tests/` directory, normal
order: 2852 passed (34 minutes). Golden files: only the 3 above changed.
Scenario harness: log identical to the #177 log (135 lines; A20, the
Exclude flow, unchanged). Test ZIPs
`test-zips/ancora_0.8.0-TEST-178-exclude-sticky.zip`, `-v2.zip`, `-v3.zip`:
`external_zip_test.py` and `smoke_test.py` pass on Electrum 4.8.2; the
v3 changes and both translations checked inside the ZIP.

**Owner's ZIP tests (2026-10-22, read in his Electrum logs):**
- v1 (05:02 UTC): divinaly excluded read "received and verified" (its
  copies verified before) -> v2: "excluded" also then.
- v2 (07:09 UTC): deselected -> "excluded", Verification 4/4. A new will
  (4 October 2031, five will-executors) healed the missing 2031 copy.
  divinaly answered 503 to 25 copies, Exclude dropped them -> v3: Exclude
  keeps every copy sent once; the List row "In force, to be verified" ->
  v3: "In force, excluded", grey.
- v3 (11:41 UTC): divinaly selected, no answer to the check: the Check
  ends with the warning "received, no answer to the check", 4/5 (no
  freeze; no Exclude offered for that state: next step). Deselected
  (11:44 UTC): "excluded", Verification 4/4, Sending "not needed", the
  summary "4 of 4", the List row "In force, excluded" grey; no check and
  no sending to divinaly in the log.

**Next steps (agreed with the owner):** #179 the List deletes only drafts
and old versions never sent; #180 a 503 to the check is "no answer";
Exclude offered next to "received, no answer to the check", with its own
question; the word under each step of the Check window more readable.

**Outcome:** DONE.

## 179. The Will tab list deletes only the old versions never sent

**Date:** 2026-10-22

**Problem (the owner's ZIP test of #178, 2026-10-22, a will with three
delivery dates and five will-executors):** the owner deleted
`we.divinaly.support`'s 2031 transaction (delivery 1) in the List. The
Check looks for each will-executor in the copies of every delivery
together (`Will.check_willexecutors_and_heirs`): divinaly's copies of
deliveries 2-3, under the other will-executors' copies of delivery 1, hid
the missing one, so nothing was rebuilt or sent for it and divinaly stayed
without its delivery 1 (the owner healed it with a new will). Read in the
code, the same hole comes from deleting ONE draft copy of delivery 1 (the
password window closed): the next Check signs and sends the will without
it. And the copies with a failed check (`CHECK_FAIL`: divinaly answered
"pushed: thx", then "503" to the check) or a sending without an answer
(`PUSH_FAIL`) could be deleted, though the will-executor may hold them.

**Owner's decision (2026-10-22, "remove the ways to make mistakes, not
add logic"; no delete of the later-delivery copies, no presence check per
delivery):** the List deletes only what no will-executor can have and is
not part of the will being prepared or in force. Choice B: also the drafts
are not deleted by hand (the next Check drops them when the change is
undone, #128/#135). The way to stop using a will-executor is "Exclude".
No new text: the "delete" entry is grey, as before, when nothing selected
may be deleted (owner: no explanation).

**What changed:**

- `bal/gui/qt/lists.py` `_can_delete`: not deletable, besides the #129
  rules (PUSHED or CHECKED; a backup transaction in force), a copy sent at
  least once (`engine.verify.ever_sent`: also `PUSH_FAIL` and
  `CHECK_FAIL`) and every VALID copy (draft, partially signed, signed and
  not sent yet), in every delivery. Deletable: the old versions (no longer
  VALID: replaced, invalidated) never sent. A mixed selection still deletes
  only the rows that may be deleted. Docstring rewritten with the WHY.
- `tests/test_gui_will_menu.py`: the delete cases follow the rule (drafts,
  partially signed, signed not sent, PUSH_FAIL, CHECK_FAIL: not deletable;
  old versions never sent: deletable; old versions with PUSH_FAIL or
  CHECK_FAIL: not deletable); the fake item gets `we = None`.
- `tests/test_gui_will_versions_list.py`
  `test_draft_on_top_unnumbered_and_not_deletable` (was
  `..._and_deletable`): the draft row's "delete" is grey.
- New `tests/test_list_delete_rule.py` (3 tests, flowkit, three fake
  will-executors with their own fee addresses, the three-date will of
  `test_multiverse_chain_rebuild.py`, 39 items): no copy of a sent will is
  deleted, also after a 503 (CHECK_FAIL); a draft copy of delivery 1 is not
  deleted and the next Check signs and sends the whole chain (3 + 9 + 27);
  an old version never sent is deleted, and only it.

**Verification:** `ruff check bal/ tests/` clean; affected tests 49 passed
(16 s); mutation run 3/3 caught (the old rule; only `ever_sent`; only
VALID); whole suite 2856 passed (30 min). Scenario harness not run: no scenario deletes in the
List. ZIP `test-zips/ancora_0.8.0-TEST-179-list-delete-rule.zip` (b0cdd815...) loads as an external plugin and passes the smoke test on Electrum 4.8.2; the new rule checked inside the ZIP.

**Owner's ZIP test (2026-10-22):** an old "Discarded draft" version (its copies "Invalidated", never sent) was deleted ("Elimina" in Italian); every other row, divinaly's copy too, had "delete" grey and nothing else was deleted.

**Outcome:** DONE.

## 180. A 503 to the check is "no answer", not a failure

**Date:** 2026-10-22

**Problem (the owner's ZIP test of #178, 2026-10-22):**
`we.divinaly.support` accepted its copies ("pushed: thx"), then answered
"503 Service Temporarily Unavailable" to the check (`searchtx`) of 25 of
them. `Willexecutors.check_transaction` raised the error like any other, so
`WillItem.set_check_willexecutor` marked the copies `CHECK_FAIL` and no
longer `PUSHED`: the next Check sent them again, though the will-executor
holds them. A 429 still busy already gave `NO_ANSWER` (#172).

**Owner's decision (2026-10-22):** choice B, a 503 is "no answer" at once,
with no new try (a new try within seconds rarely helps a server that is
down, and with 25 copies it would hold the Check up to its 2-minute
deadline). Only the 503: the 502 and 504 and the other errors stay
failures.

**What changed:**

- `bal/core/willexecutors.py`: new `CHECK_UNAVAILABLE = 503` with the WHY;
  `check_transaction` returns `NO_ANSWER` for a 503, logs it and writes
  "{url} unavailable (503): no answer this time" in the command trace (no
  pause, no `on_busy`). The copy stays sent and to be verified, the summary
  reads "received, no answer to the check" and the next Check asks again.
  `_trace_error` shows "unavailable (503)" instead of "error 503".
  Docstrings updated.
- `bal/locale`: the two new texts, translated in Italian and Spanish.
- New `tests/test_check_unavailable.py` (3 tests; the real
  `check_transaction` on a fake `send_request`; flowkit, three fake
  will-executors with their own fee addresses, the three-date will of
  `test_multiverse_chain_rebuild.py`): a 503 is `NO_ANSWER` at once (one
  request, no pause, no busy notice); a 500 is still raised; a
  will-executor answering 503 to every check keeps its 13 copies sent (no
  `CHECK_FAIL`), the others are verified, the next Check calls it "no
  answer to the check" and sends nothing to it again.

**Verification:** `ruff check bal/ tests/` clean; affected tests 103
passed (19 s); mutation run (without the 503 rule) 2/3 caught as expected
(the 500 test passes both ways); whole suite 2859 passed (29 min). Scenario harness
not run: no scenario answers 503. ZIP
`test-zips/ancora_0.8.0-TEST-180-503-no-answer.zip` (fca93c85...) loads as
an external plugin and passes the smoke test on Electrum 4.8.2; the new
rule and the Italian text checked inside the ZIP.

**Owner's ZIP test (2026-10-22, the three-date will, five will-executors,
divinaly selected again after #178's Exclude):** seven Checks from several
IP addresses. `we.divinaly.support` never answered 503 this time: it gave
the right copy or no answer in time. The copies without an answer stayed
sent (no `CHECK_FAIL`), nothing was sent to it again, and the last Check
read "received and verified" for all five, Verification 5/5. The 503 branch
itself is covered by the automatic tests only.

**Outcome:** DONE.

## 181. "Exclude" next to "received, no answer to the check"

**Date:** 2026-10-22

**Problem (the owner's ZIP tests of #178-#180, 2026-10-22):**
`we.divinaly.support` received the will, then did not answer the check.
The Check window showed "received, no answer to the check" (⚠) with no
button: "Exclude" and "Retry" (#93) were offered only for a will-executor
that did not get the will. The only way to drop it was the Will-Executor
tab.

**Owner's decision (2026-10-22):** offer "Exclude" there too, with its own
question (the #93 text "did not receive your will" would be false), plus
"You can also press Retry later"; keep the two texts distinct. Text
approved by the owner.

**What changed:**

- `bal/gui/qt/widgets.py` `BuildReportView`: new `EXCLUDE_STATES` (the
  #93 `FAILED_STATES` plus `no_check_answer`); the will-executors box
  offers "Exclude" next to those rows and "Retry" under the box.
  `FAILED_STATES` itself is unchanged.
- `bal/gui/qt/dialogs.py` `_exclude_executor`: for a will-executor with no
  answer to the check the question reads: "<host>" received your will, but
  did not answer the check. / If you exclude it, it is no longer checked
  and your next wills are no longer sent to it. The copies it already
  received stay with it. You can select it again in the Will-Executor tab.
  / You can also press Retry later. / Exclude it? The #93 question for a
  will-executor that did not get the will is unchanged. After "Yes" the
  #178 Exclude runs as before (deselected by the user, the copies sent
  kept, the check runs again, "excluded" in the summary).
- `bal/locale`: the three new texts in Italian and Spanish.
- New `tests/test_exclude_no_check_answer.py` (5 tests; flowkit, three
  fake will-executors with their own fee addresses, the three-date will of
  `test_multiverse_chain_rebuild.py`): the box offers "Exclude" and "Retry"
  for the "no answer to the check" row only once asked, nothing when all
  are verified; its own question; the #93 question unchanged for "failed"
  and "timeout"; the whole way (a will-executor never answering the check,
  its button, "Yes": deselected, its 13 copies kept, the next Check does
  not ask it, sends it nothing and calls it "excluded").

**Verification:** `ruff check bal/ tests/` clean; affected tests 133
passed (38 s); mutation run 2/2 caught (the button only for the #93
states; the #93 question for every state); whole suite 2864 passed (34 min).
Scenario harness not run: the Exclude flow is covered by flowkit. ZIP
`test-zips/ancora_0.8.0-TEST-181-exclude-no-check-answer.zip`
(f70bd69b...) loads as an external plugin and passes the smoke test on
Electrum 4.8.2; the new rule and the Italian text checked inside the ZIP.

**Owner's ZIP test (2026-10-22):** the ZIP loads; every Check (several IP
addresses, also with the Wi-Fi off) read "received and verified" for all
five will-executors, so no row offered "Exclude", as expected. The "no
answer to the check" case could not be seen live: a copy verified once is
never asked again by the Check (`Will.needs_server_check`), which the owner
found to be a limit (next step #182). Owner: close #181 on the automatic
tests.

**Outcome:** DONE.

## 182. The Check asks again the copies already verified

**Date:** 2026-10-23

**Problem (the owner's ZIP test of #181, 2026-10-22):** every Check, from
several IP addresses and also with the Wi-Fi off, read "received and
verified" 5/5. A copy verified once was never asked again
(`Will.needs_server_check`): a will-executor that lost a copy (a server
rebuilt) or closed would read "verified" for years, and with no
connection the window still said "Verification 5/5".

**Owner's decisions (2026-10-22):**

- a will in several deliveries: its verified copies are asked again once
  their verification is a day old (13 copies per will-executor with three
  dates: every Check would be slow and bring 429 answers); a will with one
  delivery, basic or advanced mode: at every Check;
- a copy verified before that gets no answer now: "received, no answer to
  the check" (no longer verified, with "Exclude" of #181), never sent
  again;
- no connection at all is "no answer" too, like the 503 of #180 (else,
  offline, every copy asked again would be marked failed and sent again);
- the time of the last verification is written, with the next one for a
  will in several deliveries.

**What changed:**

- `bal/core/will.py`: `REVERIFY_SECONDS_MULTI` (one day) with the WHY;
  `WillItem.checked_time` (saved as `checked_time` only when known: the
  copies verified before keep their saved form, older plugins ignore it),
  set by `set_check_willexecutor` when the will-executor confirms the copy;
  `NO_ANSWER` on a verified copy clears CHECKED (PUSHED kept). New
  `Will.now_seconds`, `Will.reverify_period` (one day when the sent copies
  carry more than one date, else 0), `Will.needs_reverify`,
  `Will.last_verified`.
- `bal/engine/verify.py` `verify_targets`: without `recheck` (the Check,
  the command line) it also asks the verified copies due again
  (`Will.needs_reverify`), never those of an excluded will-executor (#178).
  The right click "check" is unchanged.
- `bal/core/willexecutors.py`: `Willexecutors.no_connection` (an
  `OSError` or aiohttp's `ClientConnectionError`, not an HTTP answer);
  `check_transaction` returns `NO_ANSWER` for it and writes "{url} not
  reached: no answer this time" in the command trace. `send_request`'s
  "You are offline." is now a `ConnectionError` (same text).
- `bal/core/datetext.py` `local_short` ("23 Oct 2026, 13:52", local time
  of the computer): owner's ZIP test, the long dates did not fit and
  pushed "Exclude" and "Retry" out of the window.
- `bal/core/summary.py`: `verified_times`, `verified_text`; the Will tab
  line reads "5 of 5 received and verified it · last check 23 Oct 2026,
  13:52 · next check from 24 Oct 2026, 13:52", the last part only for a
  will in several deliveries.
- `bal/core/build_report.py` `set_verified` and the snapshot's `verified`
  (only when known); `bal/gui/qt/dialogs.py` fills it;
  `bal/gui/qt/widgets.py`: the Check window's row reads "received and
  verified on 23 Oct 2026, 13:52"; the next check of a will in several
  deliveries once, a grey line under the box ("Next check from …"); the
  right-hand texts of the boxes wrap, so the row's button stays in a
  narrow window, and stay on one line up to `VALUE_ONE_LINE` (360 px):
  owner's ZIP test, the first version wrapped "verified on …, 13:52" with
  room to spare.
- `bal/locale`: the five new texts in Italian and Spanish.
- Golden files regenerated (33 in `tests/golden/5e/check`, 32 in
  `tests/golden/5e/copies`): a Check of a one-delivery will now asks the
  verified copies again (its rows), and the snapshots carry the
  verification times. Nothing else changed in them.
- Tests that held the old rule, updated with the reason:
  `test_check_throttle.py` ("another error" is a `ValueError`: a
  `ConnectionError` is "no answer"), `test_engine_verify.py` (the verified
  copy is asked again; no answer: no longer verified),
  `test_verify_in_dialog.py` (nothing to verify: a will in several
  deliveries verified now), `test_exclude_received.py` and
  `test_deselected_executors.py` (the Will tab line now ends with the last
  check).
- New `tests/test_reverify.py` (18 tests; flowkit, three fake
  will-executors with their own fee addresses, the three-date will of
  `test_multiverse_chain_rebuild.py` and a one-date will): the time kept
  and saved; the saved form of an old copy unchanged; no answer on a
  verified copy; one delivery asked at every Check; several deliveries
  after a day (39 copies asked again after a day, none within it); a copy
  verified before #182 asked; excluded, drafts and replaced copies never;
  no connection (offline, name not resolved, connection dropped) is no
  answer, a 500 still raised; the texts; the Check window's row and its
  "Next check" line (none for one delivery); "Exclude" and "Retry" inside
  a 600 px window, the row texts wrapping; the longest rows in English,
  Italian and Spanish (the real catalogs) whole on one line at 1000 px,
  inside the window with both buttons at 600 px; offline,
  a verified one-date will reads "no answer to the check" for the three
  will-executors, nothing marked failed nor sent again, and back online
  all verified.

**Verification:** `ruff check bal/ tests/` clean; related tests 131
passed (1 min 48 s); mutation run 5/5 caught (never ask a verified copy again; no day
for several deliveries; no answer keeps verified; no connection a failure
again; no time saved); whole suite 2880 passed and 1 failed (37 min): the Will tab line of
`test_deselected_executors.py`, expected (the last check now follows),
updated and its file rerun, 8 passed. After the one-line fix (look
only, owner: no new whole-suite run): related tests 60 passed (10 s), the
Check goldens and the Will menu 63 passed (10 s), the one-line width
mutation caught. Scenario harness not run:
the flows are covered by flowkit. ZIP
`test-zips/ancora_0.8.0-TEST-182-reverify.zip` (34ddbc25...) loads as an
external plugin and passes the smoke test on Electrum 4.8.2; the new rules
and the Italian texts checked inside the ZIP.

**Owner's ZIP tests (2026-10-23, the three-date will, five
will-executors):** the first Check asked every copy again (98 of 114 on
the way); `we.divinaly.support` did not answer: "received, no answer to
the check (15 s)" with "Exclude" and "Retry" (#181 seen live), then
"excluded", Verification 4/4. The first version's long dates pushed the
buttons out of the window and then wrapped with room to spare: fixed
(short dates, the next check once under the box, one line up to 360
px); the last ZIP showed every row whole on one line and "Next check
from 24 Oct 2026, 13:52" under the box. In basic mode the will (three
deliveries) is not asked again within the day, as decided. The Wi-Fi off
test is left for later (owner).

**Outcome:** DONE.

## 183. The word under each step of the Check window is easier to read

**Date:** 2026-10-23

**Problem (owner):** under the five step names of the Check window, the
word or counter ("ok", "not needed", "4/5", "0/5 · 27 s / 45 s") was small
(11 px) and grey (#555555), hard to read on the coloured cells.

**Owner's decision (2026-10-23):** a preview of three versions, drawn with
the plugin's own code in Italian (today; A: 12 px in the step's colour;
B: 13 px in the step's colour): proposal B. Look only.

**What changed:**

- `bal/gui/qt/widgets.py` `BuildReportView`: `STEP_WORD_SIZE = 13` and
  `STEP_WORD_GREY = "#444444"` with the WHY; `_steps_bar` writes the word
  in the step's own colour (green, orange, red, blue), the darker grey for
  the steps not reached or not needed.
- New `tests/test_step_words.py` (1 test): 13 px everywhere, the colour of
  a warning and of a running step, the grey of a step to do.

**Verification (look-only step, owner's rule: no whole suite):** `ruff
check bal/ tests/` clean; the Check window tests and goldens 110 passed
(27 s); mutation run (11 px) caught. ZIP
`test-zips/ancora_0.8.0-TEST-183-step-words.zip` (00875fa2...) loads as an
external plugin and passes the smoke test on Electrum 4.8.2; the new size
checked inside the ZIP.

**Owner's ZIP test (2026-10-23):** OK, the words under the steps read well.

**Outcome:** DONE.

## 184. The Will tab has a "Graph" view of the will

**Date:** 2026-10-23

**Owner's idea (2026-10-20, draft approved; previews of 2026-10-23 in
`safe21-ancora/previews/185-*` and `186-*`):** a third view next to "List"
and "Future", to see a will in several deliveries at a glance: which copy
holds which, where a will-executor did not receive or confirm its copy.

**Owner's decisions (2026-10-23):**

- time from left to right, one column per delivery with its date and, in
  green, who receives what ("luca 10% · john 10%");
- one box per transaction with the will-executor's NAME inside and the
  colour of its STATE (the colours of the Check window's steps): verified,
  no answer / to be verified, not received, draft not signed (dashed),
  disabled or excluded (grey); one legend of the states (no colour per
  will-executor, no number: the name is written);
- curved links from each copy to its children; [+] / [−] open and close a
  branch, "Expand all" / "Collapse all"; when the view appears the first
  branch is open (owner's ZIP test);
- the mouse over a box: the path from the wallet in green, the copies of
  the same will-executor outlined, a tooltip (delivery, txid, will-executor
  and fee, state and verification time); a click opens Electrum's
  transaction window, as the List does; the copies in the blockchain keep
  their path green;
- the replaced versions hidden, shown in grey on demand;
- owner's ZIP test: the bar in the middle with bigger buttons, and an
  adjustable zoom (30 % to 150 %, also Ctrl + wheel) with "Fit" for the
  whole open graph; boxes, links and texts scale together;
- every row is a possible future (owner, preview approved): at each
  delivery only one will-executor publishes its copy, the first to arrive,
  so every path to the last delivery is one future (3 x 3 x 3 = 27 with
  three will-executors and three deliveries); the rows on bands light and
  darker in turn, "Future n" at the right of each row ("Futures 10–18"
  for a closed branch, instead of "n hidden"), green on the path under the
  mouse, and a sentence over the graph that says what a row means;
- light colours like the rest of the Will tab; not in the PDF report.

**What changed:**

- New `bal/core/graph.py` (no Qt): `build_graph` (the tree from the will
  items: a copy is the child of the copy whose output it spends; children
  in the order of the Will-Executor tab), `node_state` (states and
  details), `shown`, `column_dates`, `column_shares` (the delivery table,
  or the heirs of a one-delivery will), `future_ranges` (the possible
  futures under each node).
- New `bal/gui/qt/graph_view.py`: `GraphView` (drawing, branches, hover,
  tooltip, click, zoom, fit) and `GraphPanel` (the bar and the scroll
  area). Display only: nothing builds, signs, sends or changes a date.
- `bal/gui/qt/future_view.py` `WillViewSwitch`: a third button "Graph"
  (`VIEW_GRAPH`, stored in `bal_will_view` like the others; without a
  graph widget the switch is as before). `bal/gui/qt/window.py`: the panel
  in the Will tab, refreshed with the will summary (only while shown).
- `bal/locale`: the new texts in Italian and Spanish.
- New `tests/test_graph_view.py` (15 tests; flowkit, three fake
  will-executors with their own fee addresses, the three-date will of
  `test_multiverse_chain_rebuild.py`): the tree (3 + 9 + 27, the tab's
  order, the depths, the dates, the shares), the states, a draft, one
  delivery, the first branch open then branches open and close, expand /
  collapse all, the hover path and tooltip, a click opening the
  transaction window, painting, the replaced versions on demand, the
  switch's third button, the zoom (sizes and clicks), "Fit", the bar and
  Ctrl + wheel, the futures (1 to 27, the groups of the closed branches,
  one delivery) and the sentence.

**Verification:** `ruff check bal/ tests/` clean; graph tests 15 passed
(11 s); related tests (Future view, translations, i18n, the List, the Will
menu) 269 passed (29 s); whole suite 2898 passed (38 min). ZIP
`test-zips/ancora_0.8.0-TEST-184-graph-view.zip` (87c4f339...) loads as an
external plugin and passes the smoke test on Electrum 4.8.2; the new files
and the Italian texts checked inside the ZIP.

**Owner's ZIP tests (2026-10-23):** first ZIP: "molto bene", asked for the
bar in the middle with more visible buttons, an adjustable zoom and the
first branch open; second ZIP: OK ("stupendo"); third ZIP, with the
futures: OK. The view reopens on the
last tab chosen (List, Future or Graph), as before: owner OK.

**Outcome:** DONE.

## 185. The texts of a will in several deliveries: "What changes", the version row, the Heirs tab, the heir window

**Date:** 2026-10-23

**Goal (step M4b of `context/MULTIVERSE-PLAN.md`, owner's plan of
2026-10-23, preview `safe21-ancora/previews/189-m4b-testi-consegne.html`
approved):** three places read a will in several deliveries as if it had
one date. M4b is split in three steps (owner): this one (#185), the
low-fee warning for every will, the message help text and BASIC with a
table (option (a): the table kept, the BASIC summary shows the
deliveries read-only).

**What changed:**

- `bal/core/will_changes.py`: the "What changes" box compared delivery 1
  alone and recalculated its shares inside it (10 % + 10 % read 50 % +
  50 %: the owner's "Real shares 33% → 50%", seen in the #172 ZIP test).
  Now a side may hold its deliveries (`WillSide.deliveries`: the date and
  the shares of the WHOLE estate of each delivery, from the will items or,
  with `table=`, from the wallet's table: `settings_table`). When a side
  is a will in several deliveries the box has "Deliveries: one delivery →
  3 deliveries" when the number changes, then one row per delivery whose
  date or shares changed ("Delivery 2: 10 Mar 2032 (new): luca 10% · john
  10%", "luca 50% → 10%", "removed (it was …)"), the note "The
  percentages are shares of the whole estate." on the last one and the
  note of the delivery row (earlier: free; later: invalidation) on
  delivery 1; no "Real shares" row. An heir added is written with its new
  share only, one removed only in the Heirs row. `will_changes(...,
  staged=False)` keeps the comparison of delivery 1 alone.
- `bal/engine/context.py`: `drop_anticipation_leftover` passes
  `staged=False` (a precaution: with a table its date test already stops
  before, see below).
- `bal/gui/qt/dialogs.py` (the box before the paid invalidation) and
  `bal/gui/qt/basic_wizard.py` (the summary's box): the settings side
  reads the table (before, the heirs' amounts, not used with a table).
- `bal/core/will_versions.py`: each heir row of a version carries its
  delivery. `bal/gui/qt/lists.py`: the version row of a will in several
  deliveries reads "Delivery 10 Mar 2030, 00:00 UTC (+2 deliveries)" and
  names each person once, "luca 10% + 10% + 30% · john 10% + 10% + 30%"
  ("0%" where the person receives nothing); one date: unchanged.
- `bal/gui/qt/deliveries.py` `person_shares` and `bal/gui/qt/lists.py`:
  with a table the Heirs tab's Amount column shows "10% + 10% + 30%" (the
  amount is not used), one line per delivery in the tooltip, "Delivery 1
  (10/03/2030): 10%"; no table: unchanged.
- After the owner's first ZIP test (2026-10-23):
  - `bal/gui/qt/window.py` `update_all`: the Heirs list is rebuilt too.
    `heirs_tab` is Electrum's plain tab widget, its `update()` only
    repaints: the Amount column kept "70%" after the table was saved, until
    the wallet was opened again (the test had rebuilt the list by hand).
  - The language's decimal separator: "16,5%" in the version row, the
    Amount column and its tooltip (`deliveries.shown_percent`); the stored
    "16.5%" is unchanged (the box already wrote "16,5%").
  - The heir window, owner's choice "B grey" (previews `190-*`, `191-*`,
    the real window `192-*`): with a table a row "Shares" over the amount
    ("16,5% + 16,5% + 17%", one line per delivery with its date, "Edit the
    deliveries…" in ADVANCED, opening the window of the deliveries over
    this one); an heir already saved shows its amount grey and locked, the
    mode buttons off, with "it counts only if you go back to one delivery,
    and then you can change it": OK saves the stored amount as it is. A
    new heir writes its amount (`Heirs.validate_amount` wants more than 0
    and the table has no share for it yet), "0% + 0% + 0%" and "After OK
    you will be asked to open the table of the deliveries". The amount
    and message rows move one row down.
  - `bal/gui/qt/deliveries.py` `confirm_back_to_one`: the question names
    the amounts that count again ("luca 50%, john 50%",
    `heir_amounts_text`).
- Catalogs: 13 new texts in Italian and Spanish ("Deliveries", "one
  delivery", "{} deliveries", "{} (new)", "removed (it was {})", the
  note, the tooltip line, "Shares", the two sentences of the heir
  window, its "?", "After OK…", the question's new sentence).

**Tests:** new `tests/test_staged_texts.py` (17 tests; the core with will
items, then flowkit with three fake will-executors, each with its own fee
address, and the owner's table 10/10, 10/10, 30/30 %): one date to three
deliveries, identical, one delivery changed, dates moved and a delivery
removed, back to one date, an heir added and one removed, `staged=False`,
the settings side from the table, one date unchanged; the Check window's
box, the box before the invalidation of a later delivery 3, the wizard
summary's box; the version row (+2 deliveries, a 0 % cell, one date); the
Heirs tab column and its tooltip. On the code before this step 14 of the
17 fail (the 3 others check what must not change).

**Seen in passing (not changed here, owner informed):** with a will in
several deliveries, a change cancelled at the password and then undone
(e.g. the fee) makes the next Check ask to sign again:
`drop_anticipation_leftover` was written for one date and stops when a
draft is not on delivery 1's day, which is always so with deliveries 2-3.
Proposed as the next step.

**Verification:** `ruff check bal/ tests/`: all checks passed. Related
tests (the box, the versions list, the deliveries window, the graph, the
BASIC wizard, the heirs and the heir window, translations, the golden-file
tests) 548 passed (8.5 minutes). Whole `tests/` directory: 2922 passed (32
minutes). Golden files unchanged. The real heir windows rendered offscreen
in Italian and checked (a wrapped line was cut: fixed). Test ZIPs
`test-zips/ancora_0.8.0-TEST-185-staged-texts.zip` (`e64b4ddd...1c41`)
and `...-185-staged-texts-2.zip` (`860418ac...d757`):
`external_zip_test.py` and `smoke_test.py` pass on Electrum 4.8.2; the
Italian texts checked inside the ZIP.

**Owner's ZIP tests (2026-10-23):** first ZIP: the box and the version
row as in the preview; seen: "16.5%" with a dot, the Heirs tab still
"70%" until Electrum was restarted, and the heir window showing only the
amount not used; the owner chose "B grey". Second ZIP: OK, commit and push asked.
Seen there, for a later step: in BASIC with a table a new heir shows "0% +
0% + 0%"; adding or deleting an heir in BASIC leaves a table that no
longer fits the heirs and the Check stops (its window is ADVANCED only).
Owner's choice (1): in BASIC, with a will in several deliveries, "New
heir" and "Delete" do not go on and explain why (the step "BASIC with a
table").

**Outcome:** DONE.

## 186. A change undone in a will in several deliveries no longer asks to sign again

**Date:** 2026-10-24

**Seen in #185 (owner informed; plan of 2026-10-24 approved, then "do all
the tests needed until the solution is robust and safe", with
independent reviews, judges and critics):** with one date, a change
cancelled at the password and then undone (e.g. the fee changed and put
back) brings the will in force back, nothing to sign (#135,
`engine.context.drop_anticipation_leftover`). With a table of deliveries
every Check asked to sign again: the rebuild dates ONE day earlier only
the deliveries it changes (seen in flowkit: the fee anticipates delivery 1
only, the drafts of deliveries 2-3 keep their dates; delivery 3's shares
rebuild only its 27 copies, its table date one day earlier), and the rule,
written for one date, wanted every draft on delivery 1's day.

**What changed:**

- `bal/engine/context.py`: `drop_anticipation_leftover` sends a will in
  several deliveries (a table in force or in the settings) to the new
  `_drop_staged_leftover`; the one-date branch is unchanged. The rule,
  all of it must hold: the settings (Heirs tab, the table, the
  will-executors selected now, the fee) compared with the will in force
  delivery by delivery (#185) differ ONLY by deliveries exactly one day
  earlier with exactly the same shares (two decimals); every draft is on
  the stored date of its delivery; every delivery moved has drafts on that
  day; no signed copy spends a draft's output. Then the dates go back to
  the will in force's first (delivery 1 through the validated write and
  read back, the later ones in the table, replaced whole), and only then
  are the drafts dropped (unsigned, only in this wallet: free). Signed or
  sent items are never touched; when in doubt the rule does nothing and the
  will is asked to sign, as before.
- `bal/core/will_changes.py` (also the "What changes" box of #185):
  - the will in force keeps older generations of copies (a delivery
    rebuilt one day earlier and signed leaves the old copies in force,
    pre-empted; after going back to one date the old deliveries 2-3 hang
    on a replaced delivery 1). A later delivery is now read along the
    chain: the copies that spend an output of the delivery before, of
    those the ones with the earliest date (`_item_deliveries`, `_parents`);
    before, the first copy found was read (often the old one);
  - the persons of the will in force are read from every delivery (an
    heir with 0 % at delivery 1 was read as "added"), each address under
    the names of the first delivery that pays it (a rename next to a
    rebuilt delivery read "removed luca");
  - two tables are compared and written with two decimals ("30.04%");
    no "→ 0%" when the new shares are not known (fixed amounts after the
    table removed); no error when the only name left at an address is new
    (`_heirs_change`); a rename already said is not repeated
    (`with_renames`);
  - `delivery_number` and `same_shares(decimals=)` public (the engine
    uses them).

**Reviews:** a first independent review found 5 problems, 3 confirmed in
flowkit (old generations read, the one-date rule no longer firing after
going back to one date, an heir with 0 % at delivery 1); a multi-agent
review (9 agents, read-only) found 3 more problems of the box and confirmed
the design; a second independent review found no serious bug, 3 test
gaps; judges and critics (9 agents, 4 new angles) found nothing that could
change what is paid, lose data or move a date silently, and 6 test gaps.
All fixed, each with a test written first and seen failing.

**Seen, not changed here (for the owner, separate steps):**

- R-1 (confirmed in flowkit, pre-existing): a will-executor excluded after
  it received the will keeps a branch with the OLD shares: after a change
  of delivery 3's shares, 5 of the 9 delivery-2 copies still in force have
  only delivery-3 children with the old shares; if the excluded
  will-executor's delivery 1 is published first, delivery 3 pays the old
  shares.
- R-2 (confirmed in flowkit, pre-existing, rare): going back to one date
  when the heirs' own amounts equal delivery 1's cells keeps delivery 1's
  date (no day earlier): the old will and the new one are both valid that
  day. With other amounts the new will is one day earlier (checked).
- U1 (pre-existing): a fee change cancelled and put back, then a real
  change of a later delivery: delivery 1 keeps the day (all copies to
  sign, shown in the box).
- U2 (pre-existing, old wallets only): two heirs on one address, the
  second paid only from delivery 2: the rule does nothing.
- R-7/R-8: a delivery the owner moved exactly one day earlier himself
  and whose rebuild he cancelled is put back (the limit of #135); a
  later delivery's day left by a cancelled round goes with the next real
  change. R-9: a will with one date, a table added then removed: one extra
  signature. R-11: the command line never runs the rule (as before).

**Tests:** new `tests/test_staged_leftover.py` (22 tests; flowkit, three
fake will-executors each with its own fee address, the owner's table
10/10, 10/10, 30/30 %): the fee and the shares of delivery 2 or 3 changed,
cancelled and put back (nothing to sign, the will in force kept, the
wallet file and a second clean Check); twice in a row; a new will in force
never dropped; two deliveries moved back together; what is NOT a leftover
(a change kept, other shares, two days earlier, the table removed, a
draft off its delivery's date, hundredths, a refused or silently refused
date write, drafts signed after the Cancel, a delivery without drafts, a
will with one date and a table added then removed), each checked as a
decision of the rule (not an error swallowed); after a later delivery was
signed; after going back to one date; an heir with 0 % at delivery 1.
`tests/test_staged_texts.py`: the fakes carry the chain; 7 more box tests
(two names on one address, hundredths, fixed amounts, an heir only at
delivery 2, a strange copy, a rename with a share of delivery 2, a rename
of an heir paid later). On the code before the fixes the new tests fail;
mutation checks (each guard removed in turn: read-back, two decimals,
moved deliveries with drafts, draft dates, signed copies) are each caught
by their test. The new tests 5 times in a row: always 64 passed.

**Verification:** `ruff check bal/ tests/`: all checks passed. Related
tests (the leftover rules, the box, the deliveries, the chain, the
versions list, the wizard, the golden-file tests) 561 passed (10
minutes). Whole `tests/` directory: 2951 passed in the normal order (37
minutes) and 2951 passed in the reverse file order (31 minutes). Golden
files unchanged. Scenario harness (full matrix, the 2-coin scenarios 6
times, warn_matrix synced and not): log identical to the #178 log (135
lines, 65 s). Test ZIP `test-zips/ancora_0.8.0-TEST-186-staged-leftover.zip`
(sha256 `8c615967...6bde`): `external_zip_test.py` and `smoke_test.py`
pass on Electrum 4.8.2.

**Owner's ZIP test (2026-10-24, test wallet, a will in three deliveries
at four will-executors):** the fee changed, Cancel, put back: nothing
asked (the log: "84 unsigned drafts dropped, deliveries [1] back to the
will in force's dates: ['2032-10-02']", 4 + 16 + 64 copies); delivery 3's
shares changed, Cancel, put back: nothing asked ("64 unsigned drafts
dropped, deliveries [3] ... ['2034-10-03']"); a change not undone: asked to
sign. "will ok" after each; no error in the log. Owner: OK.

**Studied with the owner afterwards (decisions and open points in
`context/STUDY-2026-10-24-staged-will-safety.md`):** going back from
several deliveries to one will need the on-chain invalidation, with a
warning before a will in several deliveries is made (owner decided); the
anticipation keeps ONE day (owner decided); R-1 to fix in its own step.

**Outcome:** DONE.

## 187. A will in several deliveries is always redone from delivery 1, and the Check verifies its possible futures

**Date:** 2026-10-24

**Owner's study (2026-10-24, `context/STUDY-2026-10-24-staged-will-safety.md`,
R-1; re-verified in flowkit with 13 variants, each run on its own):** a
will-executor excluded, deselected or removed after it received the will
keeps copies nobody can recall. The build makes new copies only for the
selected will-executors, and a later delivery was dated only against its
own parent: after a change of delivery 3's shares, deliveries 1 and 2 were
kept (same txids) and that will-executor's copies (its delivery 1; its
delivery 2 under everyone's delivery 1) kept OLD subtrees nothing
pre-empted. Counting every possible future (at each delivery only a copy
with the earliest date can be mined first): **15 of 23 paid the old
shares** with three will-executors, **6 of 7 with two**; the Check said
"will ok", and selecting the will-executor again changed nothing. The
owner's choices: every change redoes the whole chain (the future of 12
deliveries is set aside); the Check verifies the futures and rebuilds by
itself; the anticipation stays one day.

**What changed:**

- New `bal/core/futures.py` (no Qt): `possible_futures` (the tree from
  the copies' inputs; only the earliest-dated delivery-1 copies start a
  future, at each level only the earliest-dated children go on, as the
  Graph view counts them), `plan_of_rows` / `rows_paid` (what each
  delivery pays: address and amount as written, names left out),
  `futures_off_plan`, `future_text`.
- `bal/core/will.py` `date_chain`, rule 1: after delivery 1 is dated,
  `_pre_empt_whole_chain`: when the rebuild does not keep every VALID copy
  of the will in force as it is (other shares, another date, a
  will-executor no longer selected), delivery 1 is dated one day before
  the earliest delivery 1 in force, of ANY will-executor (the backup copy
  too), so the whole old tree dies when the new delivery 1 is mined; the
  later deliveries keep their dates on the new parents. Only the copies
  somebody HOLDS count (`_held_branch`, after the independent review:
  received by their will-executor, or the backup copy, and every copy they
  hang on held too): a copy never received (a sending that failed, #141)
  exists only in this wallet and is simply redone, no day lost; with a
  will-executor excluded or removed that still holds copies, any later
  change (a will-executor added too) redates the chain, the new delivery 1
  pre-empts them. A will-executor added later with every copy in force held
  and kept: nothing moves. Then
  `_check_futures`, rule 2's safety net: every possible future of the will
  in force plus the new chain must pay the new plan, else
  `WillChainError` (nothing saved).
- `bal/core/will.py` `is_will_valid`, rule 2: with a plan of two
  deliveries or more, every possible future of the VALID copies must pay
  it, else the new `FuturesOffPlanException` ("a new will must be
  prepared", the first such future and the count in the log): the Check
  rebuilds from delivery 1 by itself. One date: untouched.
- `bal/core/messages.py`: the sentence of the Check ("A copy of the will
  held by a will-executor no longer selected could still deliver the old
  shares (15 of 23 possible ways): preparing a new will, dated one day
  earlier."), Italian and Spanish.
- Gone with rule 1: U1 and R-8 of the #186 reviews (a day taken by a later
  delivery in a cancelled round; a leftover of delivery 1 carried into a
  later change).

**Tests:** new `tests/test_staged_futures.py` (21 tests): the core
(plan, a full tree of 27 futures, only the earliest copies start a future,
a subtree with old shares, a missing level, strange copies); flowkit with
three fake will-executors, each with its own fee address: delivery 3
changed without exclusion (the whole chain one day earlier, 39 copies, a
clean second Check), with a will-executor excluded / deselected / removed,
delivery 2 changed, twice, two will-executors, with the backup copy, the
fee, one date then three deliveries, a will-executor added later (no day
lost), the Check finding an old future in a wallet left as before #187
(rules off while the change was signed) and healing, a will with one date
not checked; after the review: a new fee address of a copy never received
costs no day, a will-executor added while another is excluded redates the
chain. On the code before: 9 fail. Mutation checks: rule 1 off (9
caught), rule 2 off (1 caught), both off (9 caught). Updated, with the
reason in their docstrings: `test_multiverse_chain_rebuild.py` (two tests
of #168/#169 said "delivery 1 is the signed one, not sent again": now
the whole chain, 39 copies, delivery 1 one day earlier) and
`test_staged_leftover.py` (seven tests said a later delivery's table date
moved: now delivery 1's date; the leftover rule of #186 unchanged) and
`test_engine_instalments.py` (one test of #169, "delivery 1 stays the
signed one" when delivery 2 is moved earlier: now one day earlier too).

**Reviews:** an independent review (read-only) found nothing unsafe and
three cases where rule 1 cost a day for nothing (a copy never received
redone for a new fee address or fee; a partial signature left over): fixed
with `_held_branch`; a second review of that refinement found the "kept"
comparison reading the txids of deliveries 2+ as the build set them,
before the first delivery was dated (a day lost for nothing when the
stored date lags the one in force; not reachable through the plugin's own
flows, which keep the stored date on the one in force): the children are
now relinked to their parents' current txids first. Noted for later: when
the safety net refuses a chain (`WillChainError`), its text is technical;
a friendlier sentence is a small step of its own.

**Verification:** `ruff check bal/ tests/`: all checks passed. Related
tests (the futures, the chain rebuilds, the leftover rule, the #141 fee
address, the deliveries window, the golden-file tests) 823 passed (10
minutes). Whole `tests/` directory: 2972 passed in the normal order (52
minutes) and in the reverse file order (47 minutes); the one failure of
those runs, the #169 test above, was then updated and its file run again
(a test-only change). Golden files unchanged. Scenario harness (full
matrix, the 2-coin scenarios 6 times, warn_matrix synced and not): log
identical to the #186 log (135 lines, 74 s). Test ZIP
`test-zips/ancora_0.8.0-TEST-187-staged-futures.zip` (sha256
`f8286060...8cf9`): `external_zip_test.py` and `smoke_test.py` pass on
Electrum 4.8.2; the new module, the rules and the Italian sentence checked
inside the ZIP.

**Owner's ZIP test (2026-10-24, test wallet, a will in three deliveries):**
a will-executor excluded, delivery 3's shares changed and signed: the log
says "chain: the will in force changes, the first delivery is dated one
day before the earliest one in force (#187)", the copies signed and sent,
the next Check "will ok"; no error in the log. Owner: "tutto ok".

**Outcome:** DONE.

## 188. Back to one delivery needs the invalidation on the blockchain

**Date:** 2026-10-25

**Owner decision (2026-10-24, `context/STUDY-2026-10-24-staged-will-safety.md`,
section 5):** going back from several deliveries to one is a change of the
TYPE of will, not a routine update: the copies in several deliveries held
by the will-executors are invalidated on the blockchain first (a network
fee; the coins spent together), then the one-date will is prepared. Before,
a one-date will dated one day earlier was built over them (the Check even
said "heir luca (delivery 2) removed"): pre-empted by one day only, and in
the R-2 case (the heirs' own amounts equal to delivery 1's cells) not even
that, two wills valid the same day. The owner must be warned BEFORE a will
in several deliveries is made: going back is not free. Going from one to
several deliveries stays free (the pre-emption of delivery 1, #187); from
three to two deliveries stays a normal change.

**What changed:**

- `bal/core/will.py`: new `StagedWillRemovedException`, a
  `WillPostponedException` (the Check and the command line already know
  that road: invalidation first, then the new will). `is_will_valid` →
  `check_staged_will_removed`, BEFORE the heirs check (which read the
  table's removal as heirs removed and built a one-date will over the
  copies in force): the rows the build uses have one delivery while copies
  of a delivery 2 or later are in force (`staged_in_force`: the #128
  notion, signed and/or sent, still executable; drafts never, so a table
  never sent is removed for free as before). Nothing with a table of
  several deliveries.
- `bal/gui/qt/dialogs.py`: the Check row "Back to one delivery:
  invalidating the will in several deliveries", the "Invalidate" step as
  for a postpone; cancelled there: no date is put back (there is none to
  put back; the #134 revert is for a postpone), the sentence says the
  copies stay in force, the next Check asks again, and the table window
  brings the deliveries back. After the broadcast the one-date will is
  prepared and sent in the same Check, as for a postpone.
- `bal/gui/qt/deliveries.py`: "Back to one delivery" says the
  invalidation FIRST when a will in several deliveries is in force, and
  then not "It is free" (true only for drafts: the question of before
  otherwise); its sentence on the dates says "With one delivery only, the
  heirs receive the whole estate on {}, the date of delivery 1. Today they
  receive {} by that date: the rest would arrive earlier than planned."
  (before "Without the table ...", not understood; the command line says
  the same); the FIRST table of a wallet asks
  "Think it over: going back ... will need an invalidation ... Changing
  the dates or the shares later stays free: it is going back to one
  delivery that costs. Save the table?" (`back_to_one_warning`, also under
  the example in the window); without a table but with a will in several
  deliveries in force (its removal not invalidated yet) the window opens
  on ITS dates and shares (`in_force_table`, read along the chain of
  copies, #186) with "save them to keep it (nothing to sign)", no warning
  asked: changing one's mind is one Save.
- `bal/core/messages.py` `staged_removed_message`: the sentence of the
  Check row's reason and of the command line ("Back to one delivery: your
  will in 3 deliveries is held by the will-executors and must be
  invalidated on the blockchain first (a network fee); then the new will
  is prepared.").
- `bal/cli/controller.py`, `commands.py`: `bal_instalments_clear` with a
  will in several deliveries in force is refused without
  `--confirm_invalidation true` (the sentence, the privacy note and the
  hint to ask the owner); `bal_will_check` / `prepare` return "postponed"
  with the sentence and the invalidation transaction, as for a postpone.
- Catalogs: 7 new texts and 1 changed, Italian and Spanish (the command line stays
  English by design, `babel.cfg`).

**Tests:** new `tests/test_staged_back_to_one.py` (16 tests; three fake
will-executors with their own fee addresses, the owner's table): the core
(`staged_in_force` 36 of 39, the rule only with a one-delivery plan, none
for one date); the Check: the invalidation asked first and cancelled
(nothing built or sent, 39 copies in force, the date untouched, the
sentence), asked again at the next Check, accepted (the invalidation
broadcast, then the 3 copies of the one-date will sent in the same Check,
a clean Check after), drafts only (free), the R-2 amounts, three to two
deliveries (normal, #187); the window: the question with the invalidation
(No keeps, Yes removes) and without a will in force, the first table's
warning (No, Yes, not repeated, shown under the example), the will in
force offered back (its cells, Save without a question, the next Check
clean), nothing offered without one; the command line: refused without
the confirmation, removed with it, "postponed" with the sentence, drafts
without confirmation; after the owner's ZIP test: the question starts with
the invalidation, before the dates, without "It is free" (with it for
drafts only); after the review: the note when an heir was
added since, no automatic invalidation (the headless flow itself).
Mutation check: the rule off, 6 tests catch it. Updated, with the reason
in their docstrings: `test_engine_instalments.py` and
`test_cli_instalments.py` (one test each of #169 said the table's removal
built a one-date will one day earlier, free: now the invalidation first,
then the one delivery on the wallet's date; the command refused without
the confirmation, prepare "postponed"); `test_gui_deliveries.py` and
`test_staged_texts.py` (three tests saving the first table of a wallet
answer the new question; one of them had no scripted answer and opened a
real window: the first run of the affected tests hung for six hours, then
a timeout was put on every run).

**Review (Opus 5.5, read-only, on the owner's request of two models
checking each other):** nothing that asks a healthy wallet to invalidate,
the flow the same as a postpone (after the broadcast the one-date will is
built in the same Check, as for a postpone). Fixed from its findings: the
automatic rebuild (off by default, `GUI_AUTO_REBUILD_ENABLED`) would have
invalidated on its own after the owner said no in the Check: never for a
back to one delivery (the command line's `bal_will_autorebuild` returns
the reason "back_to_one", it never sends by itself); the Will tab's
"Prepare" road showed the postpone's words: its own now; the window
offering the will in force back said "nothing to sign" even when an heir
was added or removed since: it says to check the shares; whether a copy in
force is still executable is asked whatever the wallet's sync state (a
spend the wallet already knows counts: this check leads to a paid action).
Known, for the owner: a wallet made before #188 that went back to one
delivery the old way (a one-date will over the staged copies, still
executable) is asked the invalidation at its first Check after the update;
the "Think it over" question is asked whenever no table is stored, so
also before a table made again after a back to one. Not counted: copies
of a will in several deliveries only partially signed (a co-signer could
complete one; the new will is dated one day before them, as #128).

**Verification:** `ruff check bal/ tests/`: all checks passed (four
import blocks sorted by `ruff --fix` on the files touched). Related tests
191 passed (2 minutes). Whole `tests/` directory: 2988 passed in the
normal order (50 minutes); in the reverse file order the first run had 8
failures, all in `test_gui_delivery_field_refresh.py` and
`test_delivery_field_keeps_value.py` (the period form, "today + the
period"): both files compute `TODAY` when the module is imported, and that
run crossed midnight UTC between the collection and those tests (a
fragility of those tests, not of #188: alone, together with every file of
#185-#188, and in a second complete reverse run, 44 minutes, all passed).
Golden files unchanged. Scenario harness (full matrix, the 2-coin
scenarios 6 times, warn_matrix synced and not): log identical to the #187
log (135 lines, 74 s). Test ZIP
`test-zips/ancora_0.8.0-TEST-188-back-to-one.zip` (sha256
`69de2c1c...a16711`): `external_zip_test.py` and `smoke_test.py` pass on
Electrum 4.8.2; the rule and the Italian texts checked inside the ZIP.
After the owner's ZIP test (two text fixes, the order of the question and
its sentence on the dates): ruff clean, the related tests (the deliveries
window, the staged texts, the command line, the translations) 165 passed
(1 minute), a new ZIP (sha256 `bd485072...def6f4`) loaded and smoke-tested
on Electrum 4.8.2.

**Owner's ZIP test (2026-10-25, test wallet, a will in three deliveries
sent):** "Back to one delivery", then the Check: it asks the invalidation
first (the log: "one delivery wanted, 196 copies of a will in 3
deliveries in force: the invalidation first (#188)"); cancelled: the
sentence "Interrupted ... stays in force"; the window "Inheritance in
several deliveries" opened on the dates and shares in force, saved: the
will in force again, nothing to sign. The owner found the question said
the invalidation last and "It is free" too, and did not understand
"Without the table": both fixed (above), a new ZIP checked by the owner:
"mi pare ok".

**Outcome:** DONE.

## 189. Plain texts: the refused chain, translated Yes / No, today in two tests

**Date:** 2026-10-25

**Owner decision (2026-10-25):** three small steps in one, as only texts,
translations and tests change (the text-only rule: ruff and the whole
suite, no scenario harness, no owner ZIP test). Noted in #187 and #188.

**What changed:**

- `bal/core/messages.py`: new `chain_refused_message`. When the safety net
  refuses a chain of deliveries (`WillChainError`), the owner read the raw
  reason ("chain: a loop between the transactions"). Now one plain
  sentence: "The new will was not prepared: a safety check found a problem
  in the chain of deliveries. Nothing was changed: if you have a will in
  force, it stays as it is. The details are in Electrum's log." The raw
  reason goes to the log.
- `bal/gui/qt/dialogs.py` (the Check): the sentence in the build row, and
  the dialog stops there (before, the raw text in red, then the error went
  up to the thread). `bal/gui/qt/window.py` (Prepare): the sentence in an
  error box, without the "preparing a new will" message before it.
  `bal/cli/controller.py`: `build_will` gives a `UserFacingException` with
  the sentence (the command line stays English, as before).
- `bal/gui/qt/common.py`: new `ask_yes_no`, a Yes / No question with the
  buttons added with their own `_()` text; No is the default button and
  the Esc key. Qt's standard Yes / No buttons (Electrum's `question`)
  stayed in English, because Electrum loads no Qt translation (checked in
  `electrum/gui/qt/util.py`), although the catalogs had "Sì" / "Sí". Used
  by `BalDialog.question` (Exclude the will-executor, close the offline
  signing with a will not saved), by `deliveries.ask` (the questions of
  the deliveries window, #188's included) and by `confirm_basic_mode`
  (#174, unchanged for the owner).
- `bal/locale/`: catalogs extracted and updated; the new sentence in
  Italian and Spanish, no fuzzy entries.
- `tests/test_gui_delivery_field_refresh.py`,
  `tests/test_delivery_field_keeps_value.py`: TODAY (and the date D built
  on it) computed again when each test starts (an autouse fixture). Computed
  once when the file was loaded, they went stale when the whole suite
  crossed midnight UTC before these files ran: the 8 failures of the #188
  reverse-order run. A test only change.
- Not changed (owner decision): the "OK" button of the plain message boxes
  stays in English, for the same reason (a step of its own if wanted).

**Tests:** `tests/test_texts_189.py` (new, 12 tests): the translated Yes /
No box (Yes, No, Escape; No by default and on Esc), `BalDialog.question`,
the deliveries question and "Go back to BASIC mode?" through it; "Yes" /
"No" in the Italian and Spanish catalogs; the plain chain sentence, the raw
reason only in the log, its Italian and Spanish texts not fuzzy; the
command line's `build_will` with a refused chain.

**Verification:** `ruff check bal/ tests/`: all checks passed. Related
tests (the new file, the BASIC question, the deliveries window, #188, the
Exclude question, the two date files, the chain rebuilds, the command line
deliveries, the offline signing, the messages) 244 passed (3.5 minutes).
Whole `tests/` directory in the normal order: 3000 passed (44 minutes).
Golden files unchanged. The date fix itself shows only in a run that
crosses midnight UTC.

**Outcome:** DONE.

## 190. The excluded will-executor that still holds a copy is said

**Date:** 2026-10-26

**Owner decision (study of 2026-10-24, `context/STUDY-2026-10-24-staged-will-safety.md`,
point 4; texts and look 2026-10-25):** a will-executor excluded, or removed
from the list, that still holds a copy of the will in force is said in the
summary, permanently, with what it means. Before, after an Exclude the
summary no longer counted it (#178) and nothing said that it still holds an
executable will: a copy sent cannot be recalled. A grey note, its first
sentence in bold; the box keeps its colour (owner: orange is "something to
do", here usually there is nothing to do). Also in the PDF report (owner).

**What changed:**

- `bal/core/summary.py`: new `excluded_holders(in_force, selected)`: the
  URLs of the will-executors not selected now with a copy SENT (`PUSHED` or
  `CHECKED`) among the items of the will in force (`Will.in_force_items`,
  the #128 notion: VALID or REPLACED, not invalidated or executed, still
  executable). A copy signed and never sent is not with it; a will-executor
  deselected before it received anything stays the #134 note only. New
  `excluded_holders_paragraphs` / `excluded_holders_html`: "<host> is
  excluded but still holds a copy of your will, and a will-executor cannot
  be asked to delete it." (plural: "2 excluded will-executors (a, b) still
  hold ...") / "If you change your will, the new one is dated one day
  earlier: when the other will-executors publish it, the network executes
  it first and the old copy is no longer valid." / "Only an invalidation on
  the blockchain cancels the old copy too." `will_summary(held=...)` adds
  `held` and `held_text` (nothing while busy).
- `bal/engine/context.py`: `excluded_holders(host, selected)`, read only.
- `bal/gui/qt/widgets.py`: the Will tab's summary box shows the note under
  the #134 line; the Check window (`BuildReportView`) under the
  will-executors box (`BuildReport.set_held`, snapshot key `held` only when
  shown). `bal/gui/qt/window.py`, `bal/gui/qt/dialogs.py`: they pass the
  list. `bal/core/report.py`, `bal/gui/qt/report_actions.py`,
  `bal/gui/qt/report_pages.py`: the PDF prints the note under the state box.
- `bal/locale/`: the four sentences in Italian (the owner's text) and
  Spanish, no fuzzy entries.
- `tests/golden/5e/`: three Check snapshots gain the `held` line (the
  scenarios where a will-executor was deselected or excluded AFTER it
  received the will: `executor_deselected`, `exclude_down`,
  `all_executors_deselected_after_sent`); nothing else changed in them.

**Tests:** `tests/test_excluded_holder_note.py` (new, 10 tests, three fake
will-executors with their own fee addresses): the rule (sent or verified
and not selected; signed only, selected, backup: not counted); the texts,
one and two will-executors, bold first sentence; excluded after it
received the will (summary green with the note, Check window, Will tab
box); still said after a change signed and sent to the other two (its
copies REPLACED, still executable); gone after an invalidation and when
selected again; removed from the list: said; deselected before the first
sending: not said; the PDF report prints the three sentences.

**Verification:** `ruff check bal/ tests/`: all checks passed. Related
tests (summary, Check window, report, exclusion, selection) 493 passed (3
minutes). Whole `tests/` directory in the normal order: 3007 passed, 3
failed (44 minutes): the three golden files above, only the new `held`
line; updated after the owner's OK, the two golden test files then 94
passed (52 s). Scenario harness (full matrix, the 2-coin scenarios 6
times, warn_matrix synced and not): log identical to #188's (135 lines,
87 s). Test ZIP `test-zips/ancora_0.8.0-TEST-190-excluded-holder.zip`
(sha256 `9507b251...6e7193`): `external_zip_test.py` and `smoke_test.py`
pass on Electrum 4.8.2; the rule and the Italian sentence checked inside.

**Owner's ZIP test (2026-10-26, test wallet, a will in three deliveries,
we.divinaly.support excluded earlier):** a fourth will-executor added,
Check, signed and sent: the Will tab shows the note (orange box while to
sign, green once in force, "4 of 4 received and verified it"); the PDF
report prints it under the state box. Seen, for later steps: the PDF says
"Delivery form: Fixed date" for a will in three deliveries; in the
toolbar "before the delivery (2032-03-29)" overlaps another date.

**Outcome:** DONE.

## 191. The Multiverse: the delivery form of a will in several deliveries, and the Check Alive sentence in one label

**Date:** 2026-10-26

**Owner (ZIP test of #190, 2026-10-26):** the PDF report of a will in three
deliveries said "Delivery form: Fixed date" (the settings' form of such a
will is "fixed"), and in the toolbar the Check Alive sentence "before the
delivery (≈ 6 months) →" and its date were drawn over each other. Owner
decisions: the will in several deliveries is nicknamed "Multiverse" (IT
"Multiverso", ES "Multiverso"), next to the name where the user meets the
form, not in every sentence; the delivery form lists the dates; for the
toolbar, choice A: one shorter sentence "before the delivery → <date>",
the approximate months in the tooltip.

**What changed:**

- `bal/core/future.py`: `delivery_form_text(form, months, locktimes=())`:
  with more than one delivery "Multiverse — 3 deliveries: 1 Mar 2030,
  1 Mar 2033, 1 Mar 2036" (the short UTC day); one delivery unchanged
  ("Fixed date" / "Period (renewable) — N months").
- `bal/gui/qt/report_pages.py` (PDF state box) and
  `bal/gui/qt/future_view.py` (Will tab, Future view): pass the dates of
  the deliveries of the will in force.
- The nickname: the wizard's choice "Several deliveries (Multiverse)"
  (`bal/gui/qt/widgets.py`), the window "Inheritance in several deliveries
  (Multiverse)" (`bal/gui/qt/deliveries.py`, title and heading) and its
  entry in Tools (`bal/gui/qt/lists.py`). Warnings, errors and help keep
  "several deliveries".
- `bal/gui/qt/widgets.py`, the Check Alive field: one label "before the
  delivery → 2032-03-29" (the date red when past); "(≈ 6 months)" and
  "This Check Alive date has already passed" in its tooltip. Cause of the
  overlap: on a screen narrower than the Will tab's top row (a maximized
  window, about 1365 points), Qt squeezes the row below its minimum; the
  sentence kept its whole width (#146) and the separate date label was
  laid over it. One label can only be cut at its end; the row's minimum
  also drops (547 -> 458 points in the probe). The old date label stays,
  hidden, for the callers of `editor.label`.
- `bal/locale/`: "Multiverse" and the three titles in Italian and Spanish,
  no fuzzy entries.

**Tests:** `tests/test_delivery_form_multiverse.py` (new, 3 tests, three
fake will-executors with their own fee addresses): the text of each form;
the Will tab and the PDF of a will in three deliveries say "Multiverse —
3 deliveries: ..." and no "Fixed date". `tests/test_gui_deliveries.py`:
the window title and the wizard's choice with the nickname;
`tests/test_check_button.py`: the Tools entry. `tests/test_check_alive_days.py`,
`tests/test_advanced_period_default.py`: the months read from the tooltip,
the date (red when past) inside the sentence.

**Verification:** `ruff check bal/ tests/`: all checks passed. Related
tests (delivery form, Future view, report, deliveries window, Tools menu,
Check Alive, #189 texts) 310 passed (35 s). No golden file holds these
texts. Text and look step: no whole suite and no scenario harness. A
probe of the Will tab squeezed to 1200 points (bigger font, Italian)
reproduced the owner's overlap before the change and shows the whole
sentence after it. Test ZIP `test-zips/ancora_0.8.0-TEST-191-multiverse.zip`
(sha256 `e40f8192...42bea2a0b`): `external_zip_test.py` and
`smoke_test.py` pass on Electrum 4.8.2; the Italian texts checked inside.

**Owner's ZIP test (2026-10-26):** toolbar, Future view, PDF report and
the nickname in the menu, window and wizard: all good.

**Outcome:** DONE.

## 192. The warning for a miner fee below 20 sat/vB

**Date:** 2026-10-26

**Owner decision (multiverse plan, section 4; text of 2026-10-26):**
lowering the miner fee is the user's choice, but years later, on the
delivery date, a busy network may not accept a transaction with a low fee.
A WARNING only, never a block, for every will, also one whose fee was
stored before this step. Owner's Italian text: "Commissione inferiore a 20
sat/vB: è una tua scelta. Alla data di consegna del tuo testamento la rete
Bitcoin potrebbe essere molto trafficata, e i nodi potrebbero non accettare
una transazione con una commissione bassa. Con 20 sat/vB o più questo
rischio si riduce molto e l'eredità ha più probabilità di essere eseguita
nei tempi stabiliti, senza ritardi."

**What changed:**

- `bal/core/messages.py`: `LOW_FEE_LIMIT = DEFAULT_FEE` (20 sat/vB) and
  `low_fee_warning(fee)`: the text below the limit, "" at 20 or more or
  for a value that is not a number.
- `bal/gui/qt/widgets.py`, the miner fee field (Will tab toolbar and the
  wizard's settings page, the same widget): an orange "⚠" next to it while
  the fee is below 20, the text in its tooltip; it follows the value typed.
- `bal/core/build_report.py`, `bal/gui/qt/dialogs.py`,
  `bal/gui/qt/widgets.py` (`BuildReportView`): the Check window shows an
  orange line "⚠ Miner fee below 20 sat/vB: ..."; the snapshot has a
  `low_fee` key only when the warning is shown. The will is signed and
  sent as before.
- `bal/cli/controller.py`: `bal_delivery_set --fee` with a fee below 20
  stores it and adds the warning to the answer.
- `bal/locale/`: the text in Italian (the owner's) and Spanish, no fuzzy
  entries.

**Tests:** `tests/test_low_fee_warning.py` (new, 5 tests, three fake
will-executors with their own fee addresses): the rule and the text; the
sign in both toolbars (a fee stored before, 20 hides it, 19 shows it and
is stored); the wizard's page; the Check window warns and the will is
pushed to the three will-executors, no warning at 20; the command line
warns at 19, not at 20. Golden files: 68 Check and copies goldens gained
the `low_fee` line (the test fee of flowkit is 13 sat/vB), nothing else
changed (owner OK).

**Verification:** `ruff check bal/ tests/`: all checks passed. Related
tests 109 passed (28 s); goldens 94 passed (56 s). Whole suite, normal
order: 3018 passed (49 min). Test ZIP
`test-zips/ancora_0.8.0-TEST-192-low-fee.zip` (sha256
`af738d2b...a88f91cedf0`): `external_zip_test.py` and `smoke_test.py`
pass on Electrum 4.8.2; the rule and the Italian text checked inside.

**Owner's ZIP test (2026-10-26):** all good.

**Outcome:** DONE.

## 193. BASIC with a will in several deliveries: no heir added, deleted or imported

**Date:** 2026-10-27

**Owner decision (1), 2026-10-23; Import too, 2026-10-26:** in BASIC the
table of the deliveries is read-only and its window exists only in
ADVANCED. An heir added, deleted or imported in BASIC would leave the table
out of step with the heirs, and the next Check would stop with no way to
fix it there. So in BASIC, with a will in several deliveries, "New heir",
"Delete" and "Import" do not go on and say: "This will is in several
deliveries: to add or remove an heir, switch to ADVANCED, where you give
them their shares in the table." Editing an heir and the message (it has
no share in the table) still work; ADVANCED and a will with one delivery
are unchanged.

**What changed:**

- `bal/gui/qt/window.py`: `basic_table_blocks(keys=None)` shows the
  sentence and returns True only in BASIC with a table (and, with `keys`,
  when at least one key is an heir and not the message). Used by
  `new_heir_dialog` (no key: a new heir; also the wizard's "+ Add heir")
  and `import_heirs`.
- `bal/gui/qt/window.py`, `new_heir_dialog`: a key that is not a string
  is taken as "new heir". WHY (owner's ZIP 1 test): the "👤 New Heir"
  button is connected straight to this method and Qt's `clicked` passes
  its `checked` flag (False); it was taken as the name of an heir to
  edit, so the block did not apply and, after OK, a phantom delete of
  "False" ran.
- `bal/gui/qt/lists.py`, `HeirListWidget.delete_heirs`: the same block;
  deleting the message alone goes on.
- `bal/gui/qt/widgets.py`: the message's "?" gains "In a will in several
  deliveries the message travels in delivery 1." (`message_delivery_text`);
  the message window shows the same sentence as a grey line, only with a
  table.
- `bal/locale/`: the texts in Italian and Spanish, no fuzzy entries.

**Tests:** `tests/test_basic_table_block.py` (new, 12 tests, three fake
will-executors with their own fee addresses, the real `BalWindow`): New
heir blocked; the real button and the real menu entries (New Heir,
Import) blocked; in ADVANCED the button opens a NEW heir window (fails
without the fix: `[False, None]`); Edit not blocked; Delete blocked, the
message alone deleted; Import blocked; ADVANCED and one delivery
unchanged; the "?" and the grey line of the message window.

**Verification:** `ruff check bal/ tests/`: all checks passed. New tests
12 passed (5 s); related tests 149 passed (49 s); goldens 94 passed
(60 s). Test ZIP `test-zips/ancora_0.8.0-TEST-193-basic-table-2.zip`
(sha256 `3333898a...d4d2882`).

**Owner's ZIP test (2026-10-27):** ZIP 1: the block worked from the menu
but the "👤 New Heir" button still opened the window (fixed above). ZIP 2:
all nine checks good. The owner's extra check (back from several
deliveries to one) found a fault in the invalidation, older than this
step (#188): it is the next step.

**Outcome:** DONE.

## 194. The invalidation spends only coins that exist on the network

**Date:** 2026-10-27

**Owner's ZIP test of #193 (2026-10-27):** going back from a will in
several deliveries to one, the Check asked for the invalidation and the
network refused it: "bad-txns-inputs-missingorspent". The log showed
three inputs: the real coin and the outputs of two will transactions
(delivery 1 and delivery 2 of a discarded draft) kept only in the wallet's
LOCAL history, never broadcast. The summary said "Amount moved 2.5317
mBTC": the real coin (1.35258) plus those two outputs. Older than #193:
it comes from #188.

**Cause:** Electrum counts the outputs of a local transaction as wallet
coins (`get_addr_outputs`), and `Util.get_available_utxos` looks through
the local will copies that spend them. A later delivery of a will in
several deliveries spends exactly such an output, so
`Will.invalidate_will` took it as a coin to invalidate. Its docstring
said these inputs "are naturally skipped": only while that transaction is
not in the wallet.

**What changed:**

- `bal/core/will.py`, `Will.invalidate_will`: a coin whose transaction is
  local or future (not on the network) is skipped. The real coins are
  enough: every will transaction descends from them. The docstring says
  why. Every invalidation goes through this function (Tools > Invalidate,
  the automatic one, the Check, the command line), and so does its
  summary (amount moved, wills made useless).
- `tests/flowkit.py`: the fake network refuses, like a real node, a
  transaction that spends an output it has never seen
  (`bad-txns-inputs-missingorspent`). Before it accepted anything, so the
  tests of #188 could not see the fault.

**Tests:** `tests/test_invalidation_local_coins.py` (new, 5 tests, three
fake will-executors with their own fee addresses; the owner's wallet: the
copies of delivery 1 and delivery 2 kept in the local history): the built
invalidation spends the real coins only; the fake network refuses a
missing coin; back to one delivery, the invalidation is accepted and the
one-date will is sent to the three will-executors; Tools > Invalidate;
one delivery without local copies unchanged. Without the fix 3 fail with
the owner's error.

**Verification:** `ruff check bal/ tests/`: all checks passed. New tests
5 passed (9 s); related tests 249 passed (124 s); goldens 130 passed
(51 s). Whole suite, normal order: 3035 passed (46 min 48 s). Test ZIP
`test-zips/ancora_0.8.0-TEST-194-invalidation-local-coins.zip` (sha256
`94e02d8e...a483eec9d`): `external_zip_test.py` and `smoke_test.py` pass
on Electrum 4.8.2; the fix checked inside.

**Owner's ZIP test (2026-10-27):** same wallet, local copies kept: "Amount
moved 1.35258 mBTC", one input (the real coin), fee 118 sat; the network
accepted the invalidation; the one-date will was signed and verified by
4 of 4 will-executors; Electrum removed the three local copies by itself.
Seen in the same test and left to #195: the final report said "The wallet
contains funds that the current will does not cover yet: a new will must
be prepared." (a false alarm: most likely an output of the new will's
local copy to an address of the same wallet), and the copies of a
discarded draft stay in the local history.

**Outcome:** DONE.

## 195. The will's copies in the local history: no false "new funds", a cleaner history

**Date:** 2026-10-27

**Owner's ZIP test of #194 (2026-10-27):** after the invalidation the new
will was prepared and, at the end, the report said "The wallet contains
funds that the current will does not cover yet: a new will must be
prepared." Twice in a row (17:32 and 17:33) the Check found "Some utxo in
the wallet is not included", rebuilt the will and asked to sign it again.
The log line "utxo is not spent" did not say which coin. The copies of a
discarded draft also stayed in the LOCAL history.

**Cause:**

- One heir was paid at an address of the wallet itself (the owner took it
  from Electrum's own addresses). Before it was signed, the will's copy
  stayed in the LOCAL history (by design, while the will is unsigned).
  Electrum counts the output of a local transaction as a coin of the
  wallet (`get_addr_outputs`), so `Will.is_will_valid` saw a coin the will
  does not spend: a false alarm, that coin is not on the network.
- The clean-up after a Check removed only the copies labelled for a
  will-executor of the current will: a copy of a will-executor no longer
  in the will stayed. The copies of the drafts of 2026-10-26 came from
  signings cancelled at the password (no "adding signature" in the log).
- Found while testing: the clean-up chose by the label alone, so a
  transaction ON THE NETWORK carrying the history label was removed from
  the wallet (the test removed the funding transaction).
- With "Save inheritance transactions in history" off, the copies saved
  while it was on stayed until a rebuild.

**What changed:**

- `bal/core/will.py`, `Will.is_will_valid`: the outputs of transactions
  not on the network (local or future) are not coins to cover, the same
  rule as the invalidation (#194). The log line names the coin
  (outpoint, amount, height).
- `bal/core/will.py`, new `Will.history_copies`: the txids of the will
  copies kept in the LOCAL history (label matching the history label for
  any will-executor, transaction local or future, never one on the
  network). It reads the height with `force_local_if_missing_tx=False`:
  by default Electrum reports a mined transaction whose raw data is not
  stored as local.
- `Will.save_valid_transactions_to_history`: removes every copy from
  `history_copies` that is not among the copies just saved, for any
  will-executor. The docstring notes that Electrum keeps one copy per
  coin: the copies of the will-executors spend the same coin and the last
  one added replaces the others ("removing tx from history" in the log).
- `Will.remove_stale_wallet_history`: uses `history_copies` too (never a
  network transaction).
- `bal/engine/context.py`, `save_to_history`: with the setting off, the
  copies saved while it was on are removed, then the History is redrawn;
  with no such copy nothing is done.
- The history label stays as it is (owner's choice).

**Tests:**

- `tests/test_local_copies_history.py` (new, 8 tests, three fake
  will-executors with their own fee addresses). An heir at an address of
  the wallet and the will left unsigned: the next Check has no alarm and
  the same will to sign; signed after it, no rebuild. A real new coin:
  the alarm stays and the log line names it. A copy of a will-executor no
  longer in the will is removed. A labelled transaction on the network is
  never removed. Setting off: the old copies are removed.
  `history_copies` lists the local copies only. Setting on: the copy of
  the will to sign stays (unchanged). Without the fix 7 of the 8 fail.
- `tests/test_engine_context.py`: setting off with old copies: removed,
  nothing saved, then the redraw.

**Verification:** `ruff check bal/ tests/`: all checks passed. New tests
8 passed (6 s); related tests 353 passed (3 min 50 s); goldens 231 passed
(2 min 11 s). Whole suite, normal order: 3043 passed (47 min 57 s). Test
ZIP `test-zips/ancora_0.8.0-TEST-195-local-copies-history.zip` (sha256
`ee0e45e6...df0da4a6b`): `external_zip_test.py` and `smoke_test.py` pass
on Electrum 4.8.2; the fix checked inside.

**Owner's ZIP test (2026-10-27):** the same wallet, the heir still at an
address of the wallet:

- fee changed (20 -> 19), Cancel at the password: the copy stays in the
  History as "Local";
- the second Check: "will ok", no "utxo is not spent", no rebuild, the
  same will to sign;
- signed: accepted by 4 of 4 will-executors, the local copies removed;
- setting on, fee changed, Cancel: a copy in the History; setting off,
  Check, Cancel: the four labelled copies removed, nothing saved.

Seen in the same test and left for later: the final row "All done: Will
still to sign" reads as a contradiction.

**Outcome:** DONE.

## 196. "Not finished", not "All done", when the will is still to sign

**Date:** 2026-10-27

**Owner's ZIP test of #195 (2026-10-27):** after a Cancel at the password
the "Attention" box of the Check said "All done: Will still to sign"
("Tutto fatto: Testamento ancora da firmare"): a contradiction.

**Owner's choice (option A):** change the label of the last row, not the
row itself. Option B (hide the row when the orange line "Interrupted: the
new will was not signed." already says it) was left out: that line shows
only after a Cancel, while the row also shows for a watch-only wallet and
for a multisig waiting for another signer (#159), where it may be the only
"still to sign" message.

**What changed:**

- `bal/gui/qt/dialogs.py`: the last row of the Check is "Not finished:
  Will still to sign" and "Not finished: Will partly signed: complete it
  with the missing signatures."; "All done: Ok" stays for a signed will.
- Catalogs: `pybabel extract` and `update`; "Not finished" is "Non finito"
  in Italian and "Sin terminar" in Spanish (pybabel had proposed the fuzzy
  "Finito" / "Terminado", the opposite: corrected, no fuzzy entry left).

**Tests:** `tests/test_check_signing_honest.py`: watch-only (single and
multisig) and a multisig at "1 of 2" say "Not finished" with the same
text as before, never "All done", also in the "Attention" box. The golden
files `check/cancel_at_sign`, `check/auto_sign_off`, `check/phase2_error`
and `check/owner_drafts_128` change only in that row (3 lines each:
"All done:" -> "Not finished:"); before the update they were the only 4
goldens that failed.

**Verification (text-only step):** `ruff check bal/ tests/`: all checks
passed. Touched tests (signing, translations, and the flows that read the
last row) 120 passed (52 s); goldens 243 passed (2 min 4 s). No whole
suite and no ZIP (owner rule for text-only steps).

**Outcome:** DONE.

## 197. A question when an heir is paid at an address of the wallet itself

**Date:** 2026-10-27

**Owner's idea (test of #195, 2026-10-27):** the false "new funds" of #195
came from an heir paid at an address copied from the wallet's own Receive
list. On the delivery date that heir's bitcoin would stay in the wallet.
Warn when an heir address belongs to the wallet itself: with a question,
never a block (the owner tests with his own addresses).

**Owner's choices:** the question text as proposed; the import asks once
for the whole file; the command line asks nothing and saves as before,
with only a "warning" line in its answer (choice c1); the heirs already
saved at such an address are not flagged (only an address entered or
changed is checked).

**What changed:**

- `bal/core/heirs.py`: new `is_own_address(wallet, address)` (compared
  with `address_key`, so a bech32 address typed in capitals is found; an
  OP_RETURN message is never one; never raises) and
  `own_address_heirs(wallet, heirs, stored=None)` (the names in order; a
  name saved in `stored` with the same address is left out).
  `Heirs.import_file` takes `confirm_own(names)`: on False nothing is
  imported and the new `OwnAddressImportDeclined` is raised. None (the
  default) asks nothing, as before.
- `bal/gui/qt/window.py`: the "New heir" / "Edit heir" window asks "This
  address belongs to this same wallet: on the delivery date this heir's
  bitcoin would stay in this wallet instead of going to the heir. It is
  usually a mistake (an address copied from Receive). Use it anyway?"
  (title "Heir address", No by default) after the checks already there;
  No leaves the window with what was typed; an edit that keeps the stored
  address asks nothing. The import asks once with the names: Yes imports
  the whole file, No imports nothing and says "Nothing was imported."
  (Electrum's `import_meta_gui` catches only `FileImportFailed`, an error
  box "Failed to import from file.": the No is caught in `import_heirs`).
- `bal/cli/controller.py`: `heirs_add`, `heirs_update` and
  `heirs_import` add `warning` to the answer when an heir saved there is
  at an address of the wallet; nothing else changes. The help of the three
  commands says so (`bal/cli/commands.py`). The command line stays in
  English (`babel.cfg`).
- Catalogs: the four texts of the window in Italian and Spanish
  ("portafoglio" / "cartera", the word of the rest of the plugin).

**Tests:** `tests/test_own_address_heir.py` (new, 12 tests). The check:
receive and change addresses, capitals, never an address of someone
else, a message or an empty text, a wallet that raises. The window (the
REAL dialog on flowkit wallets with three fake will-executors, each with
its own fee address): Yes saves, No saves nothing, an address of someone
else asks nothing, an edit keeping the address asks nothing and a change
asks again; after Yes the Check prepares the will and the second Check is
clean (no "new funds", #195). The import: No imports nothing, Yes the
whole file, the same saved heirs imported again ask nothing. The command
line: the heir saved and the "warning" (one heir, several heirs), none
for an update that keeps the address.

**Verification:** `ruff check bal/ tests/`: all checks passed. New tests
12 passed (6 s); related tests 286 passed (2 min 7 s); goldens 243 passed
(1 min 42 s). Whole suite, normal order: 3056 passed (49 min 30 s). Test
ZIP `test-zips/ancora_0.8.0-TEST-197-own-address-warning.zip` (sha256
`301bf3da...ac021f32`): `external_zip_test.py` and `smoke_test.py` pass
on Electrum 4.8.2; the change checked inside.

**Owner's ZIP test (2026-10-27):** `wallet_test_ottobre2026`, an address
of the wallet copied from Receive: the question "Indirizzo dell'erede"
shows with Sì / No; the steps (No, Yes, an edit, the import with No and
Yes, the same file again) "tutto bene". The log of the session has no
error of the plugin.

**Outcome:** DONE.

## 198. Electrum 4.8.3: the plugin's bold, colours and links show again

**Why:** Electrum 4.8.3 (23 Oct 2026, a security update, its #10972)
shows as plain text every label left on Qt's default AutoText, and the
help of the "?" buttons unless `rich_text=True` is passed (an argument
4.8.2 does not have). The plugin's own HTML then showed its tags: the
whole suite run on 4.8.3 with a probe found 12 places (the "?" of
"Delivery date", "Delivery time" and "Check alive"; the will details;
"What changes compared with the will in force"; the step numbers and the
result line of the offline signing; the Check's log; "Support:" and the
safe21.io link, which no longer opened). The logic was not touched by
4.8.3: before this change the whole suite already passed on it (3056
passed).

**What changed:**

- `bal/gui/qt/common.py`: new `rich_text(label)` (says RichText
  explicitly: the same on 4.8.2 and 4.8.3; it also renders escaped
  entities such as `&amp;`, which AutoText showed raw in a text without
  tags) and `help_button(text)` (Electrum's HelpButton; `rich_text=True`
  only on 4.8.3 and only for a help that looks like HTML, so a plain help
  keeps its line breaks). `add_widget` uses it.
- Every "?" of the plugin is made by `help_button` (`basic_wizard.py`,
  `lists.py`, `plugin.py`, `widgets.py`, `window.py`; a local variable
  `help_button` became `help_btn`).
- Labels marked with `rich_text`: the will details (Locktime, Heirs,
  Willexecutor; the titles and values are now escaped, as they are data:
  heir names, URLs), the "What changes" box (title, names, values on the
  right, notes), the quiet note of the Check, the Check's log, the step
  numbers and the result line of "Sign the will offline", "Support:" and
  the safe21.io link of the Settings.
- Tooltips: 4.8.3 makes them plain text too; none of the plugin's has
  HTML, nothing to change.

**Tests:** `tests/rich_text_guard.py` (new) does what 4.8.3 does to the
labels, also on 4.8.2, and records each label that would show raw HTML
(at its first show, or given HTML later); `tests/conftest.py` installs it
around every test (`_no_raw_html_labels`, autouse) and fails the test that
left one. `tests/test_rich_text_483.py` (new, 5 tests): `rich_text`,
`help_button` on both versions, no plain `HelpButton(` left in the plugin,
the guard itself, the "What changes" box with an "&" in a name.
`tests/test_heir_notes_and_address.py`: the spy of the "?" texts follows
`help_button`.

**Verification:** `ruff check bal/ tests/`: all checks passed. New tests 5
passed on 4.8.2 and on 4.8.3. Whole suite with the guard, normal order:
Electrum 4.8.3: 3060 passed, 1 failed (the spy above, then fixed and
passed on both: 17 passed, 6 s each) (54 min 36 s); Electrum 4.8.2: 3061
passed (45 min 42 s). Electrum 4.8.3 in WSL from the official tag `4.8.3`
(a separate git worktree; 4.8.2 kept). Test ZIP
`test-zips/ancora_0.8.0-TEST-198-electrum-483.zip` (sha256
`0c676cce...01f4c818`): `external_zip_test.py` and `smoke_test.py` pass
on Electrum 4.8.2 and 4.8.3 (11 s).

**Note:** 4.8.3 upgrades the wallet file (seed version 72 to 74, only
Lightning invoices and channels change): a wallet opened once with 4.8.3
no longer opens with 4.8.2.

**Outcome:** DONE.

## 199. The new logo: the "à" icon and "àncòra" with the slogan

**Why:** the owner made a new logo (2026-10-28) in place of the anchor of
#163: a lower-case "à" with two teal accents as the icon, and the word
"àncòra" with the slogan "àncora per te, ancòra per loro." below it. The
name does not change. Owner's choices: the icon on a white square with
rounded corners, so that it stands out on a dark theme too, with the "à"
at 90% of the square; the big logo only in the PDF report, which is always
printed on a light background (white paper); the Settings keep the small
icon. Then (same day): every page of the PDF is signed "àncòra di
Safe21" in the footer (proposal 4). Electrum's plugin dialog and plugin
list keep the "à": the owner tried the word "àncòra" on the white
square there, but Electrum takes one icon from the manifest for both
places (checked in its code, 4.8.2 and 4.8.3), and in the list the word
is too small to read.

**What changed:**

- `assets/brand/`: the files of the old kit (anchor, mark, wordmark,
  vertical and mono lockups) are replaced by `svg/ancora-mark.svg`,
  `svg/ancora-icon.svg`, `svg/ancora-lockup.svg` and
  `svg/ancora-lockup-dark.svg` with their PNGs (icon 16-1024 px, lockup
  1200 and 2400 px). They come from the owner's traced SVGs with the
  shapes and colours unchanged: the background is removed (the holes of
  the letters become real holes) and the drawing is cut out with a small
  margin. The dark lockup is the light one with the word in white: the
  owner's dark trace had dark slivers along the first "a". `README.md`
  describes the new files.
- `bal/icons/`: `ancora-icon-16/64/128.png` (the icon on its white square)
  replace `ancora-icon-light-*.png`; `ancora-lockup.png` is the new logo.
  The names are updated in `manifest.json` (Electrum's plugin list and
  plugin dialog), `bal_resources.py`, `dialogs.py`, `plugin.py` (status
  bar, Settings) and `window.py`.
- `bal/core/brand.py`: `INK` and `WATER` are the ink and the teal of the new
  logo (`#0D0E12`, `#1DAA9B`); the interface colours do not change.
- `bal/gui/qt/report_pages.py`: page 1 shows the logo with the slogan,
  40 points high (was 30) so the slogan can be read; the small header of
  the other pages shows only the word (`LOGO_WORD_PART`, the top part of the
  same image). The fallback when the image cannot be read is now text:
  "àncòra" in bold and, on page 1, the slogan, made smaller until it fits
  (the old fallback drew the anchor). The footer of every page shows, in
  the middle, the signature "àncòra by Safe21 · safe21.io" (new
  `signature_text()`; Italian "di Safe21", Spanish "de Safe21"): the
  name and the site stay out of the text to translate; "PRIVATE" gets
  the room on its left, so the two never overlap. `bal/locale`: the
  catalogs refreshed with the new text and its two translations.

**Tests:** `tests/test_brand.py`: the new icon names; new
`test_the_icon_is_the_a_on_a_white_rounded_square` (16, 64 and 128 px:
square, transparent corners, white edge, the ink "a" and the teal accents).
`tests/test_inheritance_report.py`: the logo test checks the word, its
accents and the slogan in the logo's colours with the image, the text
fallback, and the word alone on the pages after the first; every page has
the signature in the footer; new `test_footer_signature_in_every_language`
(English, Italian, Spanish: the three footer texts, each on one line,
never overlapping, the signature centred).

**Verification:** `ruff check bal/ tests/`: all checks passed. The
affected tests (test_brand, test_inheritance_report, test_build_report,
test_pdf_heirs_qr, test_will_message, test_excluded_holder_note,
test_delivery_form_multiverse, test_version_source, test_translations,
test_i18n): 118 passed on Electrum 4.8.2 (32 s) and on 4.8.3 (32 s). Pages 1
and 2 of a report rendered and checked by eye, with the image and with the
fallback; the footer rendered in English, Italian and Spanish. Test ZIP
`test-zips/ancora_0.8.0-TEST-199-new-icon.zip` (sha256
`6707b88f...bcb7944b`): `external_zip_test.py` and `smoke_test.py` pass on
Electrum 4.8.2 and 4.8.3 (13 s); the new icons are in it, the old ones are
not.

**Outcome:** DONE.

## 200. A new will on a coin that came back from an executed will is a will of its own

**Why:** the owner's wallet (2026-10-28). A one-date will (an heir 70%,
30% to an address of the wallet itself) was executed:
one copy was mined, the heir got 70% and 30% came back to the wallet as a
new coin. Then a new will on that coin (other heirs, 15 October 2030),
signed and sent. Its copies spend an output of the executed copy, exactly
like a later delivery of a will in several deliveries, so the Will tab
grouped them as the next delivery of the executed will: the List showed
no row for the new will (its copies were counted under the executed will
#34), Future said in
red "The delivery date has passed" with the old date (16 October 2026),
the Graph drew the old will as delivery 1 and the new one as delivery 2,
and the box before an invalidation would have said that it ends no will.
The wallet and the will were right: only the grouping of what is shown
was wrong. The rule was chosen by a panel (three proposals tried on test
wallets, two independent judges) and the owner approved it before it was
written. A review (three critics, three verifiers) found that the first
version also made the rest of a will in several deliveries, rebuilt after
its delivery 1 was executed, a version of its own, with a wrong List row
("(+1 delivery) · bob 0% + 40%") and its deliveries numbered differently
in Future and in the Graph; rule 5 below keeps that case as it was (owner
OK).

**What changed:**

- `bal/core/will_versions.py`: new `new_will_links()`,
  `built_after_on_chain()` and `_later_delivery_copy()`. A will item that
  spends an output of another will item starts a will of its own when 1.
  that item reached the blockchain (CONFIRMED or MEMPOOL), 2. it was built
  by a later build (its build `time`; an executed item without a time and
  a new one with a time are two builds) and 3. it was built after that
  item's delivery date: the blockchain accepts a transaction only after
  its locktime, so a coin that comes back from an executed copy exists
  only after that date, while the deliveries of one will are built before
  the date of delivery 1. 4. A copy with the content of a delivery the
  executed copy already had (a will-executor added after delivery 1 was
  executed) stays with that delivery, the #129 rule. 5. A copy that pays
  only persons of delivery 2 or later (the `§n` names; OP_RETURN message
  rows and the will-executor fee outputs do not count) is the rest of a
  will in several deliveries rebuilt after its delivery 1 was executed (a
  new coin, another fee): it stays with that delivery 1, as before. Unknown
  data (no build time, a time or a date that cannot be read, a date in
  blocks) keeps the old link. Only stored data is read (flags, build time,
  date): the same result without a wallet or before the wallet is
  synchronized. `_fathers()` uses it, so the List, Future, the PDF report
  and the invalidation box see the new will. Known limit, written in the
  module docstring: condition 3 reads the build time on the computer's
  clock; with a clock some hours slow, a new will built a few hours after
  the delivery date keeps the old grouping (display only).
- `bal/core/graph.py`: `build_graph()` uses the same rule on the whole
  will, so the Graph and the List never group the copies differently, and
  one rule of the Graph only: without "Show replaced versions", when a
  copy in force is drawn, a copy that reached the blockchain with nothing
  in force under it is left out (the old executed will; the List keeps it
  as a version, and "Show replaced versions" draws it again). This rule
  also changes BASIC, for a will executed and then a new will on a new
  coin: the Graph draws only the new will, under its own date (before,
  the executed copy stood in the same column and the column read its old
  date).
- Not changed, on purpose: the `father` and `children` stored in the wallet
  (`Will.add_willtree`), `check_invalidated`, `set_invalidate`,
  `search_rai`, the build, signing, sending and invalidation. The stored
  tree is a real spending dependency: when the executed copy disappears
  (another copy mined instead), it is what invalidates the new will. The
  "Display" window of the Will tab menu still draws that stored tree (the
  new will under the executed copy): a possible step of its own.
- Fact corrected during the analysis: a one-date will with fixed amounts
  does not give change back today (the fixed amounts are scaled up to the
  whole balance, #76); a coin comes back from an executed one-date will
  only through an heir that is an address of the wallet itself (#197), or
  from a will built before #76.

**Tests:** new `tests/test_new_will_after_executed.py` (flowkit, three fake
will-executors with their own fee addresses): the owner's case with the
executed copy in the mempool, mined and not verified, mined and verified
(versions, will in force, Future, Graph also with "Show replaced", nothing
written, the stored father unchanged); without a wallet and not
synchronized; the PDF report (one delivery, 2030); the invalidation box
(it names #2); a second Check (nothing sent, the new copies stay valid);
the paid invalidation of the new will (only its copies, the invalidation
row ends #2); the executed copy replaced by another copy (the stored tree
invalidates the new will); the new will cancelled at the password (a draft
on top); a will-executor added to the new will; BASIC, a will executed and
a new will on a new coin (the Graph draws only the new will under its
date, "Show replaced" draws the executed copy again); wills in several
deliveries: delivery 1 executed, a will-executor added before delivery 1
was mined, delivery 2 rebuilt after delivery 1 was executed (new coin,
other fee: one version, the table's two columns in the Graph), a
will-executor added after delivery 1 was executed (one version), a new will
in several deliveries on the coin that came back.
`tests/test_core_will_versions.py`: the edges with small synthetic wills
(either output, mined or in the mempool, with or without a wallet; an
executed copy without a build time; unknown or unreadable data and a date
in blocks; the same build or an older one; a father not on the chain; a
copy rebuilt before the father's date; the content of a kept delivery with
any flags; a copy that pays only later deliveries, with a message row and
each will-executor's own fee output, in the List and the Graph; a loop in
corrupted data; the Graph's own rule) and a thousand items whose
deliveries 1 were executed, with and without copies built after the
execution (list and Graph, each well under a second).
`tests/test_gui_will_versions_list.py`: the owner's case in the real List
(#2 in force on top, #1 executed, the menu of row #2 sends the copies of
the new will).

**Verification:** `ruff check bal/ tests/` clean; the whole suite on
Electrum 4.8.3, normal order: 3106 passed (53 min); the affected test files
646 passed; the tests of the owner's case fail on the code before #200,
and the four tests of rule 5 fail with that rule switched off; the test ZIP loads on Electrum
4.8.2 and 4.8.3 (external zip and smoke test). Owner ZIP test
(2026-10-29, Electrum 4.8.2, the owner's wallet): the List shows #35 in
force on top and #34 executed below; Future says the will is in force on
15 October 2030, no red text; the Graph leaves out the executed copy and
"Show replaced versions" draws it again (green).

**Outcome:** DONE.

## 201. After delivery 1 of a will in several deliveries, nothing on its own

**Why:** owner's rule (2026-10-29): once delivery 1 of a will in several
deliveries (a Multiverse) was executed, the plugin builds, signs and sends
nothing on its own. While the plan is running it leaves it alone; when the
owner stopped it (Tools > Invalidate, or the coins spent) it does not
prepare it again. Two probe rounds (fake will-executors, Electrum 4.8.2
and 4.8.3) showed that the plugin did not respect it:

- with three deliveries, the first Check, wallet close or
  `bal_will_autorebuild` after delivery 1 built 12 new copies, which differed
  from the copies held only in the change address, and sent them: each
  will-executor then held two conflicting copies of delivery 2. On a
  wallet without a password this happened with no question at all. Cause:
  the rows of delivery 1 were never found in a VALID copy (the executed
  copy is on the chain, no longer VALID), so the will was "not coherent"
  ("heir alice (delivery 1) added") and was prepared again at every flow;
- after the owner's Invalidate, every Check, wallet close, GUI automatic
  rebuild and `bal_will_autorebuild` prepared delivery 2 again on the coin
  that came back (a wallet without a password signed and sent it with no
  question). Nothing stored said that the owner had stopped the plan.

Two rebuilds stay, on purpose (owner, 2026-10-29): new coins in the wallet
after delivery 1 (delivery 2 is prepared again with them, one day earlier,
password asked as usual) and a will-executor added (its own copy).
Wallets without a password stay supported (owner: useful for automations).

**What changed:**

- New `bal/core/delivery_progress.py`, read from the wallet only (never
  from the stored flags, which may not be refreshed yet when a flow
  starts). `plan_outputs()`: the outputs of a copy that the copies of the
  next delivery spend; a copy executed counts for this plan only when the
  next delivery hangs on it, so a will executed earlier with a new will in
  several deliveries on the coin that came back (#200) is not "delivery 1
  done" of the new will. `done_deliveries()`: deliveries 1..n executed while
  some are left. `cancelled_rest()`: a copy of delivery n (not the last) is
  in a block or in the mempool, the change that pays the next delivery is
  spent by a transaction that is not a copy of the will, and no copy could
  still be mined (signed, every coin it spends unspent, or the output of
  another copy that could be mined). In doubt a copy is not live, so the
  hold applies (owner's rule, 2026-10-30: in doubt, stop): a draft (not
  signed), a copy spending a coin the wallet does not know, or one that
  cannot be read.
- `bal/core/will.py`: `is_will_valid(done_deliveries=)` and
  `check_willexecutors_and_heirs(done=)`: the rows of the deliveries
  already executed are not looked for in the VALID copies, and the plan of
  the possible futures (#187) starts at the next delivery. The default (0)
  changes nothing for the other callers.
- `bal/core/delivery.py`: a new hold, `DeliveryRestCancelled` (code
  `delivery_rest_cancelled`), next to the two of #123.
  `bal/engine/context.py`: `check_holds()` raises it when `cancelled_rest()`
  finds the rest cancelled; `check_will()` passes `done_deliveries()`.
  Every flow passes through `check_holds()` first, before the Check Alive
  gate, so one test covers the Check, Prepare, wallet close (skipped with
  one log line), the GUI automatic rebuild (skipped), the command line
  (result `delivery_rest_cancelled` with a message), and the Sign /
  Broadcast / Invalidate commands (refused with the message).
- The text (`bal/gui/qt/flow_adapter.py`, `REST_CANCELLED`; the Check shows
  it after "Checking variables: Stopped", amber): delivery N was executed
  and the remaining deliveries were cancelled; no will is in force and
  nothing is prepared, signed or sent; to prepare a new will, "Back to one
  delivery" in the window "Inheritance in several deliveries" (Tools in the
  Will tab, ADVANCED mode), a new date, then "Check your will". The
  command line names `bal_instalments_clear`, `bal_delivery_set --date` and
  `bal_will_prepare`. Italian and Spanish translations.
- Not changed, on purpose: one-date wills (after Invalidate the Check
  prepares the will again, the usual way to postpone); what the List,
  Future, the Graph and the PDF show for a will in several deliveries
  after delivery 1 (separate steps); an Invalidate while delivery 1 is only
  in the mempool (held as stopped; if another will-executor's copy of
  delivery 1 is the one mined, Electrum drops the copy in the mempool and
  the invalidation, the plan runs again: a warning is a separate step).

**Tests:** new `tests/test_multiverse_after_delivery_1.py` (flowkit, three
fake will-executors with their own fee addresses, wallets with and without
a password): the plan running, two and three deliveries, the first flow
right after delivery 1 is mined then a Check, for the Check, wallet close,
GUI automatic rebuild and `bal_will_autorebuild` (no new copy, nothing
sent, no question); delivery 2 executed too; new coins while running
(delivery 2 prepared again with them, one day earlier, then nothing); a
will-executor added while running (one copy for it); the plan stopped
after Invalidate, two and three deliveries, every flow (nothing prepared,
the Check's text, the command line's code and message); Sign, Broadcast
and Invalidate refused with the text; new coins after Invalidate stay out; "Back to one
delivery" and a new date prepare a new will; Invalidate with delivery 1 in
the mempool, then another copy mined the way Electrum learns it; a
one-date will after Invalidate is prepared again; nothing decided without
a wallet; in doubt the plan stays stopped, every flow, with and without a
password: an old copy whose coin the wallet does not know, and a draft of
delivery 2 on the coin that came back (a signed copy there is still live). `tests/test_gui_flow_adapter.py`: the new note's row.

**Verification:** `ruff check bal/ tests/` clean; the whole suite on
Electrum 4.8.3, normal order: 3151 passed, no failure (1 h 02 min); 16
setup errors came from the logging plugin switched off in that run (the
`caplog` tests), and those files rerun with it on: 81 passed. With the
previous "in doubt, live" rule 16 of the 17 new tests fail (the other is
the signed copy that must stay live). The test ZIP loads on Electrum 4.8.3
(external zip). `tests/test_engine_context.py`: the call of
`Will.is_will_valid` now carries `done_deliveries=0` without a table.

**Owner's test of 2026-10-30 (first ZIP):** delivery 1 executed on
mainnet; the Check after it did nothing (no password, nothing signed). After
Tools > Invalidate the Check prepared the rest again and asked to sign
(cancelled by the owner): the wallet held 1037 copies of earlier wills,
and one whose coin the wallet did not know counted as "could still be
mined", so the hold never applied. Reproduced with flowkit; fixed by
"in doubt, not live" above (the new tests: 16 of them fail with the
previous rule).

**Owner's test of the second ZIP (2026-10-30, mainnet, Electrum 4.8.2, wallet
with a password):** Tools > Invalidate, signed and sent, still in the
mempool; "Check your will" stopped with the text (no password, nothing
prepared, signed or sent); the wallet close asked nothing. Before that, the
close of the old plugin, still loaded while the second ZIP was installed,
signed and sent the draft left by the first test (a new will on the
coin that came back, then invalidated by this test).

**Outcome:** DONE.

## 202. "(today)" follows the UTC day, not 24 hours

**Why:** on the owner's real testnet test (2026-10-30) a delivery date of
the next day was shown as "(today)", because any date less than 24 hours
away counted as today, also on another UTC day.

**What changed:**

- `bal/core/summary.py`: new `same_utc_day`; `relative_date` says "(today)"
  only on the same UTC day. Another UTC day less than 24 hours away says
  "(in 1 day)" or "(passed 1 day ago)", never "0 days".
- `bal/core/future.py`: `time_left_text` does the same.
- `tests/test_will_summary.py`, `tests/test_core_future.py`: the new cases.

No new text to translate; display only: nothing that builds, signs or sends
changes.

**Verified:** ruff clean; 469 tests passed (the ones on these dates and the
golden files of the Check and of the copies, unchanged), 3 min 44 s, Electrum
4.8.3.

**Outcome:** DONE

## 203. The Check says nothing "changed" on a first will

**Why:** on the owner's real testnet test (2026-10-30) the first will showed
"What changed: The will contains no valid transaction: a new will must be
prepared", although no will had ever existed and nothing had changed.

**What changed:**

- `bal/gui/qt/dialogs.py`: new `_first_will`; with no valid transaction and
  no copy in the wallet, the "Checking your will" row says Ok and the "What
  changed" box is not drawn. With copies, none valid, the sentence stays.
- `tests/test_first_will_no_change_sentence.py`: the rule.
- `tests/golden/5e/`: 66 files updated; the only difference is this sentence.

The command line keeps its `reason`. Display only: nothing that builds, signs
or sends changes. No new text to translate.

**Verified:** ruff clean; 716 tests run (the ones on the Check rows and the
golden files), 13 min 41 s, Electrum 4.8.3: the one failure was the 66th golden
file, updated in the same way; that file's tests and the golden tests were then
run again and passed.

**Outcome:** DONE

## 204. A stop the owner chose is not shown as an error

**Why:** when the owner stopped a will in several deliveries after delivery 1
(#201), the plugin did the right thing, but the Check window showed it in red:
"Check: error" and a red final sentence. It looked like a fault.

**What changed:**

- `bal/engine/check.py`: new step state `held` for this stop; the settings that
  cannot be prepared keep `stopped`.
- `bal/gui/qt/flow_adapter.py`: `held` writes "Stopped" in amber.
- `bal/gui/qt/dialogs.py`: for this stop the step and the final sentence are
  amber; the warnings box does not repeat the bare word "Stopped".
  `have_to_sign` gets a class-level default, so the report also works on a
  dialog whose `__init__` did not run.
- `tests/test_multiverse_after_delivery_1.py`, `tests/test_gui_flow_adapter.py`:
  the new cases.

Real errors stay red. Look only: the stop works as before; nothing is built,
signed or sent. No new text to translate.

**Verified:** ruff clean; 273 tests passed (these flows and the golden files,
unchanged), 4 min 37 s, Electrum 4.8.3.

**Outcome:** DONE

## 205. The invalidation sentence translated into Italian and Spanish

**Why:** going back from several deliveries to one (#188), the plugin asks to
sign and send the invalidation transaction. The sentence "Please sign and
broadcast the invalidation transaction now, then press 'Prepare' again." had no
translation, so it stayed in English in the Italian and Spanish plugin.

**What changed:**

- `bal/locale/it_IT/LC_MESSAGES/bal.po`, `bal/locale/es_ES/LC_MESSAGES/bal.po`:
  the translation of that sentence.

Text only: no code changed.

**Verified:** ruff clean; 58 translation and text tests passed, 15 s, and 152
golden tests, 2 min 7 s, unchanged, Electrum 4.8.3. No catalog has an empty
translation now.

**Outcome:** DONE

## 206. The Italian postponing message names the invalidation transaction

**Why:** when a will already sent is postponed, the Italian message said "trasmetti
il piano" and "invalidare il piano": the owner signs and sends the invalidation
transaction, and what is invalidated is the coins already committed. The English
and Spanish texts already said so.

**What changed:**

- `bal/locale/it_IT/LC_MESSAGES/bal.po`: the translation of that message.

Text only: no code changed.

**Verified:** ruff clean; 156 translation and golden tests passed, 1 min 39 s,
goldens unchanged, Electrum 4.8.3.

**Outcome:** DONE

## 207. The Italian invalidation messages speak of the old will

**Why:** after #206, two Italian messages still said "invalidare il piano": the one
for an expired will and the one shown after the invalidation. The other messages
already say that the old will is invalidated, and so do the English and Spanish
texts.

**What changed:**

- `bal/locale/it_IT/LC_MESSAGES/bal.po`: the translation of those two messages.

Text only: no code changed.

**Verified:** ruff clean; 156 translation and golden tests passed, 2 min 32 s,
goldens unchanged, Electrum 4.8.3.

**Outcome:** DONE

## 208. One name for the Check button: "Controlla" in Italian, "Comprobar" in Spanish

**Why (owner):** the same button had several names. The button itself read
"Verifica/Aggiorna", while the menu and many messages said "Controlla" and others
"il prossimo Check". "Verifica" also recalled "Verifica se sei vivo", which is
another thing.

**What changed:**

- `bal/locale/it_IT/LC_MESSAGES/bal.po`: the button and the messages naming it read
  "Controlla"; "il prossimo Check" reads "il prossimo Controllo".
- `bal/locale/es_ES/LC_MESSAGES/bal.po`: the same with "Comprobar" and "la próxima
  Comprobación", the word the plugin already uses.
- `bal/gui/qt/lists.py`, `tests/test_check_button.py`: a comment and a docstring
  that quoted the old name.

Text only: no logic changed. "Check Alive" keeps its names.

**Verified:** ruff clean; 213 translation, button and golden tests passed,
2 min 48 s, goldens unchanged, Electrum 4.8.3.

**Outcome:** DONE

## 209. The second computer shows the real outputs and asks to compare them with paper

**Why (owner):** the multisig is one person, one key on the online computer and one
on an offline computer, against a hacked online computer. Testnet4, 2026-11-01: a
file whose heir output was redirected showed the same heirs and was signed. The
owner: the reference is the address on paper, and the plugin must warn when signing
offline, "and probably always".

**What changed:**

- `bal/engine/offline_sign.py`: `file_summary`, `payment_problems`, `problem_lines`
  read each copy to sign from its transaction, delivery by delivery: heirs,
  will-executors, change (only if this wallet's keys derive it, a path in the file
  proves nothing), messages. `sign_file` refuses a file with problems: an address
  the will does not name, an heir receiving less, one person with two addresses in
  a delivery, a transaction that ignores the date, an unreadable copy.
- `bal/gui/qt/offline_sign.py`: the check lists every payment with its full address.
  "Sign all" stays off until the tick "I compared the addresses with the ones on
  paper". A file with problems: "Do not sign this file", no tick, no button.
  `SigningWarningDialog` is the same check for the other signing routes. A whole
  amount loses Electrum's lonely point ("0. mBTC").
- `bal/gui/qt/window.py`, `plugin.py`, `dialogs.py`: `confirm_signing` before the
  password, on the Check, the Will tab and Will Details, with or without a wallet
  password; Will Details refuses a file with problems. Not on a watch-only wallet,
  on the unattended automatic rebuild or on the invalidation signature.
- `bal/locale/`: 12 new texts in Italian and Spanish.
- `tests/test_offline_real_outputs.py` (new), `tests/flowkit.py`,
  `tests/test_sign_offline_online.py`.

Limits, on purpose or for later: a file coherent with itself (names rewritten in all
copies) cannot be stopped, the false address is shown in full and the paper is the
defence. `bal_will_sign` on the command line and the file import do not check yet,
and signatures are not verified yet: next step.

**Verified:** whole suite 3192 passed, 1 h 8 min 28 s, Electrum 4.8.3, with a logger
of false stops: 951 signings, 9797 copies, no good will stopped (one signal, on a
fixed fake transaction of an old test). After the last small fixes: ruff clean,
51 tests of the offline windows passed in 53 s, 95 golden tests in 1 min 6 s. The owner asked Claude to make the ZIP test: real Electrum 4.8.3
windows, offscreen, throw-away wallets, no network, Italian: single key and multisig
2 of 3 to 39 of 39 signed, altered file stopped on both routes, tick and warning
working, no exception. Not covered: Windows, the install from the Plugins dialog
(the ZIP load test passed), a real Check with funds (fake will-executors only).

**Outcome:** DONE
