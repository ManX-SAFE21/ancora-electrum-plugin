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
