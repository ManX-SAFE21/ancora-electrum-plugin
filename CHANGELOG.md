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
