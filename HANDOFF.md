# HANDOFF — BAL (Bitcoin After Life) Electrum plugin

> Purpose: let ANY future AI assistant (Claude or another model, more advanced
> or cheaper) resume work on this project with full context, without having to
> re-discover the codebase. Read this file FIRST, then `CHANGELOG.md` and
> `.agent_memory_tasks.md`.

---

## 0. TL;DR — what this project is

- **Product:** BAL ("Bitcoin After Life") — an inheritance plugin for the
  **Electrum 4.7.2 and 4.8.0** Bitcoin wallet (Qt / **PyQt6**).
- **Form:** external **ZIP plugin** (not bundled in Electrum). The user
  installs the ZIP from Electrum's plugin manager.
- **What it does:** lets a wallet owner pre-build, sign and (later) broadcast
  Bitcoin transactions that pay one or more **heirs** after a chosen **date**
  (a future UNIX-timestamp `nLockTime`). Optional **will-executors** (remote
  services) can be paid a fee to broadcast the inheritance when due. The owner
  periodically proves they are alive ("check-alive"); if the deadline passes,
  the inheritance becomes spendable.
- **Current version:** see the `"version"` field of `bal/manifest.json` (the single source of truth; read at runtime via `get_version()` in `bal/core/plugin_base.py`).

---

## 1. MANDATORY working rules (the owner set these — always follow them)

These are non-negotiable. They come from the owner directly.

- **R1 — LANGUAGE.** The CHAT language with the owner is **Italian**. But ALL
  *output* — source code, comments, docstrings, UI strings, docs, `CHANGELOG.md`,
  commit messages, this handoff — must be in **ENGLISH**.
- **R2 — DOCUMENTED CODE.** Every method/class gets a docstring + explanatory
  comments. Always explain *WHY* for any non-obvious decision.
- **R3 — NEVER INVENT.** If something is missing or unclear, STOP and ask the
  owner clear, simple questions. **The owner is NOT a programmer** — explain in
  plain language, avoid jargon. Be "100% sure" before acting.
- **R4 — HUMAN CHECKPOINT.** Before writing/modifying code, present the PLAN
  and WAIT for an explicit "OK" from the owner.
- **METHOD:** DISCOVER → PLAN (wait for OK) → EXECUTE → VERIFY → ITERATE
  (max ~8 attempts per problem, then step back and ask).
- **LOG:** keep a single `CHANGELOG.md`, in English, **one numbered entry per
  task** (newest entry appended at the END of the file).
- **ZIP-FIRST.** Deliver a test ZIP and let the owner test it BEFORE committing.
  **Commit ONLY after the owner explicitly confirms the ZIP works.**
- **ALWAYS** run `ruff` + the official test suite before committing / reporting
  / zipping.
- **CREDIT-SAVING (important).** The owner is low on funds. Minimize token /
  credit usage: report brief summaries (do NOT paste whole modified code
  blocks back), and batch work into a single ZIP/test cycle where possible.

---

## 2. Repository layout (what lives where)

```
bal/                         <- the plugin package (this is what ships in the ZIP)
  __init__.py                <- package docstring (no version here anymore)
  manifest.json              <- plugin manifest, "version" field (SINGLE SOURCE OF TRUTH for the version)
  qt.py                      <- zipimport shim used when loaded as an external ZIP plugin
  cmdline.py                 <- CLI entry-point shim (Electrum gui_name='cmdline')
  core/
    plugin_base.py           <- get_version() reads the version from manifest.json (zip-safe)
    heirs.py                 <- HEIRS + transaction building (prepare_lists,
                                prepare_transactions, buildTransactions). CORE LOGIC.
    will.py                  <- Will/WillItem, validation (check_amounts, check_will),
                                exceptions (AmountException, WillExpiredException, ...).
    willexecutors.py         <- remote will-executor services handling (is_selected / is_valid,
                                parallel push/check).
    util.py                  <- locktime parsing/most helpers (timestamps only).
    checkalive.py            <- resolve_date_to_check, check_alive_expired (GUI-free).
    reminders.py             <- compute_reminder_offsets, BALCalendar .ics generation (GUI-free).
    input_rules.py           <- locktime/threshold data models, Raw/Date selector logic (GUI-free).
  cli/                       <- headless command-line layer (no Qt)
    __init__.py              <- registers bal_* commands on import
    commands.py              <- bal_* daemon commands (@plugin_command, async, thin transport)
    controller.py            <- BalController: headless replica of BalWindow (no Qt)
    plugin.py                <- CLI Plugin entry point (extends BalPlugin, no Qt hooks)
  gui/qt/
    common.py                <- shared imports; every gui module does
                                `from .common import *`. Add new shared imports HERE.
    dialogs.py               <- the big build/sign/broadcast dialog
                                (BalBuildWillDialog, task_phase1/2), wizard glue.
    widgets.py               <- WillSettingsWidget + wizard widgets/labels.
    window.py                <- BalWindow, the per-wallet controller (build_will, check_will,
                                get_transactions, merge_will, on_close, menubar wiring).
    plugin.py                <- Electrum @hooks entry point (init_qt, tools menu, settings dialog).
    lists.py, calendar.py, theme.py (status colours), window_utils.py
  wallet_util/               <- standalone wallet-inspection helpers, no Qt
tests/                       <- standalone test scripts (see Section 3).
docs/                        <- user manual + inheritance-options guide (.md sources).
build_zip.py                 <- builds the shippable ZIP (36 files).
CHANGELOG.md                 <- numbered task log (English).
.agent_memory_tasks.md       <- terse internal memory notes per task batch.
HANDOFF.md                   <- this file.
```

---

## 3. How to build, test and lint

Two separate venvs — using the wrong one is the #1 mistake:

- **Runtime env** (Electrum + PyQt6, has `electrum` importable):
  `source "$BAL_HOME/electrum/env/bin/activate"` — an editable
  install of the Electrum **4.8.0** checkout at
  `$BAL_HOME/electrum`. Use it for anything that imports
  `electrum`, runs GUI code, or runs tests. The plugin's `bal/` directory is
  symlinked into `electrum/electrum/plugins/bal` (internal-plugin install used
  during dev).
- **Lint venv** (repo-local `venv/`): ruff, black, flake8 only. It cannot
  import `electrum` or `PyQt6`. Do NOT use it to run tests.

Run everything from the repo root (the checkout directory; set
`BAL_HOME` to its parent to use `$BAL_HOME/electrum`).

**Tests are standalone scripts (not pytest):** each `tests/test_*.py` runs its
`test_*` functions from `if __name__ == "__main__"`. Run a file directly:

```bash
source "$BAL_HOME/electrum/env/bin/activate"
python3 tests/test_core_heirs.py        # core, no Qt needed
QT_QPA_PLATFORM=offscreen python3 tests/test_gui_common.py   # GUI tests need offscreen
```

Most core tests run offline (no wallet/network). Some files
(`tests/test_group_*.py`, `tests/test_no_willexecutor_karen7.py`,
`parallel_ping_test.py`) exercise will-executor/network flows and need the live
servers — don't rely on them for quick verification.

**Current state of the suite: 427 tests collected.** The offline subset passes
(414 passed) apart from pre-existing failures that are NOT yours to fix without
asking: 13 failures in `tests/test_core_will_invalidate.py` (a `None` fee when a
UTXO has no fee value, `bal/core/will.py:482`) and 1 collection error in
`tests/test_group_i_basic_checkalive.py` (missing
`BalWindow.BASIC_MODE_CHECK_ALIVE_OFFSET_SECONDS`).

**Lint (only NEW errors matter; ignore pre-existing noise):**
```bash
./venv/bin/ruff check <files> \
  | grep -oE "^[^ ]+\.py:[0-9]+:[0-9]+: [A-Z][0-9]+" | grep -vE "F401|F403|F405|F841"
```
Ruff is NOT clean repo-wide (hundreds of pre-existing errors in `bal/` and
`tests/`); do NOT run `--fix` wholesale — just avoid adding new violations.
Pre-existing, KNOWN-OK noise: `F401/F403/F405` (star-imports via
`from .common import *`) and 2× `F841` (an unused `e` in two `except` blocks).
Do NOT "fix" these unless asked — they are intentional / out of scope.

**Build the ZIP (always clear caches first so zipimport doesn't ship stale .pyc):**
```bash
find bal -name "__pycache__" -type d -exec rm -rf {} + ; find bal -name "*.pyc" -delete
python3 build_zip.py        # -> bal-electrum-plugin.zip (deterministic, prints sha256; 36 files)
```

**Bump version — ONE file only (single source of truth):**
```
bal/manifest.json         ->  "version": "X.Y.Z",
```
The code reads this at runtime via `get_version()` in `bal/core/plugin_base.py` (exposed as the `BalPlugin.version` property), so there is nothing else to keep in sync. There is no longer a `bal/VERSION` file nor a hardcoded `__version__`.

**IMPORTANT for the owner when testing:** after installing a ZIP, the owner
must **fully restart Electrum** (not just reload the plugin) — Electrum's
`zipimport` caches modules, so a partial reload runs stale code.

**Automated release:** use `./make-release.sh` to run the full release flow
(tests, lint, build, GPG sign, SHA-256, Electrum test pause, Gitea release).
See Section 5 for details.

---

## 4. Key technical knowledge (hard-won — saves you hours)

- **Locktimes are UNIX timestamps only.** Block-height locktimes were removed
  (CHANGELOG #1). Ordering/expiry compare timestamps.
- **`heirs.py` data shape.** An heir is a list indexed by constants
  (`heirs.py` top): `HEIR_ADDRESS=0`, `HEIR_AMOUNT=1` (sats or `"<n>%"`),
  `HEIR_LOCKTIME=2`, `HEIR_REAL_AMOUNT=3` (resolved sats, or the string
  `"DUST: <n>"` when below the dust limit), `HEIR_DUST_AMOUNT=4` (raw dust sats).
- **Will-executor pseudo-heirs.** Internally, each selected will-executor is
  injected as a fake "heir" whose NAME starts with the reserved marker
  `w!ll3x3c"` (i.e. `'w!ll3x3c"' + url + '"' + str(locktime)`). Its amount is
  the executor `base_fee` (always non-dust). When you count/iterate "real"
  heirs you MUST skip names starting with `w!ll3x3c"`.
- **Transaction-building pipeline:**
  `window.build_will()` → `Heirs.get_transactions()` (recursive over locktimes)
  → `Heirs.buildTransactions()` → `Heirs.prepare_lists()` (builds the
  `locktimes` dict for ALL future locktimes, resolves amounts, marks dust)
  and `prepare_transactions()` (builds ONE tx for the LOWEST locktime only;
  the recursion handles the others via leftover `available_utxos`).
- **DUST logic (v0.4.7 — verify before touching):**
  - The "all heirs are dust" guard lives at the END of `prepare_lists`
    (NOT in `prepare_transactions`). Reason: `prepare_transactions` only sees
    the single lowest locktime, so a guard there would FALSE-POSITIVE block a
    will whose later locktimes still have valid heirs. `prepare_lists` is the
    only place that sees ALL heirs across ALL locktimes with their final dust
    state (fixed AND percentage).
  - Guard: count real heirs (skip `w!ll3x3c"`); if there are real heirs but
    NONE has a valid (non-`"DUST"`) `HEIR_REAL_AMOUNT`, raise
    `HeirAmountIsDustException` (defined in `heirs.py`). A mix of dust + valid
    heirs keeps building normally.
  - **Critical nuance:** with FIXED amounts and a LARGE balance, leftover funds
    are REDISTRIBUTED (`normalize_perc(..., real=True)`), so small fixed
    amounts end up with a VALID `HEIR_REAL_AMOUNT` (not dust). The real
    all-dust case is **small balance + percentage heirs** (matches the owner's
    log: shares of 214 / 316 / 3 sat). Tests reproduce this with
    `prepare_lists(800, 100, wallet)` and `"40%"/"60%"` heirs.
  - The exception is NOT a `WillExecutorFeeException`, so it skips that handler
    in `buildTransactions` and propagates cleanly to the GUI.
  - GUI: `dialogs.py task_phase1` has a dedicated `except
    HeirAmountIsDustException` BEFORE the generic `except Exception`. It shows a
    RED message and stops (`return False, None`) — no signing/checking, no
    empty will in the list. `HeirAmountIsDustException` is imported in
    `common.py` and re-exported via `from .common import *`.
- **`broadcast_transaction` returns `None`** (Electrum `network.py`). To get a
  txid, use `tx.txid()` — do NOT rely on the broadcast return value
  (this was the root cause of the missing "BAL Invalidate transaction" label,
  CHANGELOG #21 / v0.4.5).
- **Qt label truncation gotcha (CHANGELOG #22).** A `QLabel` added with
  `alignment=Qt.AlignmentFlag.AlignLeft` is NOT stretched by Qt, so word-wrap
  computes on a narrow sizeHint and the text gets truncated. Fix: drop the
  alignment flag, add `setSizePolicy(Expanding, Minimum)` + `setMinimumWidth`.
  With `setWordWrap(True)`, an explicit `\n` in the text forces a line break.
- **`BalBuildWillDialog` report area.** Messages are accumulated as HTML in
  `self.labels` and joined by `msg_update` (`"<br><br>".join(...)`, `\n`→`<br>`).
  The report is inside a `QScrollArea` (v0.4.8: `setMinimumHeight(450)`,
  `setMaximumHeight(700)`); the Close button sits BELOW the scroll area so it
  stays reachable. Each report row is set via `msg_set_status(label, row,
  status, color)`; passing `row=None` APPENDS a new line (used to add an extra
  note without overwriting an existing row), passing the saved row id OVERWRITES
  it (used by `msg_set_checking`, which reuses `self.check_row`).
- **BASIC vs ADVANCED (USER TYPE).** A global setting `USER_TYPE`
  (`plugin_base.py`, config key `bal_user_type`, default `"basic"`). Read it via
  `bal_plugin.is_basic_mode()`. ADVANCED reveals the **Raw/Date selector** and
  the **Check-Alive** field; BASIC hides them and disables the check-alive
  postpone behaviour. Toggling it calls `BalWindow.update_all()`, which calls
  `WillSettingsWidget.apply_user_type_visibility()` on each open settings widget.
- **Raw/Date selector visibility (v0.4.8 / task #04 — important gotcha).** The
  WILL/HEIR tab toolbars are built ONCE and REUSED for the whole session; they
  are NOT rebuilt when USER TYPE changes. So any per-widget state decided only in
  `__init__` (like the Raw/Date combo's visibility) gets "stuck". The fix pattern:
  give the widget an `apply_user_type_visibility()` method that re-reads
  `is_basic_mode()` and re-applies visibility WITHOUT changing the value/editor,
  and call it from `WillSettingsWidget.apply_user_type_visibility()` (already
  wired for `locktime` and `threshold`). The wizard avoids the bug only because
  it is recreated on every open. Keep this in mind for any future per-widget
  ADVANCED-dependent UI.
- **Will-item status flags (CONFIRMED / MEMPOOL).** `Will.check_will`
  (`core/will.py`) sets each will item's status to `CONFIRMED` / `MEMPOOL`
  (read with `witem.get_status("CONFIRMED")` etc.). After an inheritance is
  executed the wallet is fully EMPTIED, so a later CHECK makes
  `check_willexecutors_and_heirs` raise `NotCompleteWillException` (heirs no
  longer match the empty wallet). v0.4.8 adds
  `dialogs.py::_executed_inheritance_status()` (CONFIRMED wins over MEMPOOL) to
  show a reassuring note instead of an alarming "the will must be rebuilt" only.
- **Dates shown in the plugin are LOCAL time, not UTC.** The Date editor
  (`LockTimeDateEdit`, a `QDateTimeEdit`) uses `datetime.fromtimestamp(ts)` with
  NO timezone, i.e. the user's local time. So an Italian user sees Italian time;
  the on-chain `nLockTime` is the same instant expressed in UTC (e.g. ts
  `1782370800` = 2026-06-25 09:00 Italy = 07:00 UTC). There is a helper
  `Will._format_locktime` that renders a timestamp in UTC. (A planned "(UTC)"
  label in the wizard is currently SUSPENDED — see suspended item below.)

---

## 5. Git / delivery workflow

- **Branch:** work directly on `main` (no PR flow anymore). Push straight to
  `origin/main` (Gitea).
- **Commit policy:** ZIP-FIRST — build a test ZIP, let the owner confirm it
  works, THEN commit. (This differs from "commit after every change"; the owner
  explicitly prefers ZIP-first because they manually test each build.)
- Before pushing: check `git status`/`git diff`, stage only the intended files
  (never secrets), commit with a concise message, then push to `origin/main`.
- **ZIPs are NOT committed** (`.gitignore` excludes `*.zip`). They are
  distributed via **Gitea Releases** using `make-release.sh`.
- **Release process** (`make-release.sh`):
  1. Version bump in `bal/manifest.json` (single source of truth)
  2. Clean `__pycache__` and `.pyc` files
  3. Run full test suite
  4. Lint with ruff (skip if not installed)
  5. Build ZIP via `build_zip.py` (deterministic order, SHA-256, manifest check)
  6. GPG sign: `.asc` (armor) + `.sig` (binary) with key `A847D004DB91610711CA6A0DFE756706E833E0D1`
  7. Export public key as `svatantrya.asc`
  8. SHA-256 checksum
  9. Interactive pause for Electrum testing (ZIP-FIRST policy)
  10. Create Gitea tag, push, create release, upload 5 assets (ZIP + .asc + .sig + .sha256 + svatantrya.asc)
- **Usage:**
  ```bash
  ./make-release.sh           # read version from bal/manifest.json
  ./make-release.sh v0.6.2    # bump manifest to 0.6.2, then release
  ```
- **Release assets** (5 files):
  - `bal_vX.Y.Z.zip` — the plugin
  - `bal_vX.Y.Z.zip.asc` — GPG signature (armor)
  - `bal_vX.Y.Z.zip.sig` — GPG signature (binary)
  - `bal_vX.Y.Z.zip.sha256` — SHA-256 checksum
  - `svatantrya.asc` — signing public key
- **GPG verification instructions** (included in release body):
  ```bash
  gpg --fetch-key https://bitcoin-after.life/svatantrya.asc
  gpg --verify bal_vX.Y.Z.zip.asc bal_vX.Y.Z.zip
  ```
- **Auth note:** if `git push` or Gitea API fails with "invalid credentials",
  update `GITEA_TOKEN` env var or `~/.git-credentials`, then retry.
- Older PR history (pre-`main` direct workflow): **#13** (v0.4.7), **#14**
  (docs/DUST section + translation), **#15** (v0.4.8), **#4** (v0.6.1 —
  manifest.json version); all merged into `main`.
- Releases: latest is **v0.7.0**; v0.6.1, v0.6.0 and v0.5.18 before it; the older
  v0.2.x line is kept in history.

---

## 6. Version history (short — full detail in CHANGELOG.md)

- **v0.4.5** — fix invalidation loop; add "BAL Invalidate transaction" label on
  the automatic path (root cause: `broadcast_transaction` returns None → use
  `tx.txid()`); fix wizard text truncation.
- **v0.4.6** — DUST one-line-per-heir report; heirs on one line; scrollable
  report area; wizard final check (`on_next_we` now calls
  `check_transactions`); wizard truncation fix (remove AlignLeft); anticipated-
  date notice styling.
- **v0.4.7** — report area opens 500px tall (max 700); heirs reverted to ONE
  per line (green/bold); explicit `\n` line breaks in two wizard texts
  (after "(or backup)" and after "miner fees"); **ALL-DUST guard** in
  `prepare_lists` that blocks (clear RED message) only when EVERY heir is dust;
  3 new tests pinning the dust behaviour. 258 tests pass.
- **v0.4.8** — seven UX fixes: (#04) Raw/Date selector now reappears on the
  WILL/HEIR tabs when switching to ADVANCED (and on a raw value from the wizard),
  via a new `BalTimeEditWidget.apply_user_type_visibility()`; (#3) Building Will
  report min height 500→450; (#5) "User Type" setting moved to the bottom (above
  "Rebroadcast transactions"); (#6) enabling ADVANCED now requires typing
  **"at My Risk"** (case-insensitive); (#7a) on CHECK with an emptied wallet, an
  extra note on "Checking your will": "Inheritance already executed (on
  blockchain)" GREEN / "Inheritance in mempool (waiting confirmation)" ORANGE;
  (#7b) "Balance is too low… Skipped" recoloured ORANGE + space fix; Reset button
  renamed "Reset to Default Setting". 8 new tests (`test_group_h_v048.py`).
  266 tests pass.
- **v0.5.1 — v0.5.10** — BASIC/ADVANCED ("user type") mode work: Windows
  settings-dialog flicker fix; Check Alive shown read-only in BASIC; BASIC
  builds the will against "now" (`date_to_check = now()`, Check Alive fully
  ignored); ADVANCED defaults to RAW (1y/30d); consistent Raw/Date default per
  mode; Check Alive red-highlight fixes; clearer "could not build the will"
  message; CHECK no longer resets a manual Date/RAW choice.
- **v0.5.11** — Electrum **4.8.0** compatibility (the `json_db.register_dict`
  DB-registration API was removed in 4.8; the plugin now supports 4.7.2 and
  4.8.0).
- **v0.5.12 — v0.5.18** — Check Alive soft-red highlight removed; short Tor
  (.onion) will-executor URLs; KeyError fix on .onion executor actions; skip
  .onion executors from download when Electrum is not on Tor; crash fix on a
  non-dict welist response; clearer message when the list download
  fails/times out over Tor.
- **v0.6.0** — version bump for the official repository release.
- **v0.6.1** — version read from `bal/manifest.json` (single source of truth);
  `bal/VERSION` file removed.
- **#47 / #48 (post-v0.6.1)** — `is_selected`/`is_valid` fee bounds (extremes
  allowed) and the `merge_will` missing-`date_to_check` crash fix (see
  CHANGELOG).
- **v0.7.0** — OP_RETURN heirs; core extraction (checkalive, reminders,
  input_rules); RLock pickle fix; `REBUILD_ON_CLOSE`; headless CLI layer
  (`bal/cli/`, `bal/cmdline.py`, 30 `bal_*` commands); `AUTO_REBUILD` on new
  transactions; `bal_will_autorebuild` CLI command; removed redundant
  "Add transaction without willexecutor" from settings dialog.

### Open / suspended / backlog items (see `.agent_memory_tasks.md` for detail)
- **SUSPENDED — "(UTC)" label in the wizard.** The owner asked to show an
  "(UTC - Greenwich Time)" hint next to the date with a tooltip. We discovered
  the date field shows LOCAL time, not UTC, so the label would be misleading.
  Options proposed (A: label it "Local time"; B: also show the UTC equivalent;
  C: convert the field to UTC). The owner suspended it ("salta le modifiche UTC
  per ora"). Decide the approach with the owner before implementing.
- **Backlog (NOT started, analysis saved):** #01 misleading "heir not found"
  message when the date was only anticipated; #02 will-executor list should
  green-check only servers that responded + green ping, re-evaluated each
  download; #03 missing "BAL Invalidate transaction" history label when
  invalidating from the AUTO-opened window (linked to a "unify invalidate
  procedure" task). Task #04 is DONE (v0.4.8).

---

## 7. How to resume (checklist for the next AI)

1. Read this file, then `CHANGELOG.md` (last entries) and `.agent_memory_tasks.md`.
2. Confirm the environment: `git status`, current branch, and the `"version"` field of `bal/manifest.json`.
3. Run the offline test files (Section 3) — expect the offline subset to pass
   (414 passed; the 13 pre-existing failures in `test_core_will_invalidate.py`
   and the 1 collection error in `test_group_i_basic_checkalive.py` are NOT
   yours to fix without asking).
4. Talk to the owner in **Italian**, write everything else in **English**.
5. For any change: present a PLAN, wait for "OK" (R4), then implement, test,
   build a ZIP, let the owner test, and only commit after explicit confirmation.
6. Keep credit usage low: summarize, don't paste big code blocks; batch work.
7. When the owner confirms a ZIP works: commit (ZIP-first) directly on `main`,
   push to `origin/main`, then run `./make-release.sh` to create the Gitea
   **Release** with the ZIP + signatures attached (it becomes the owner's
   "Latest" download). Always give the owner the Release URL.

### In progress: QR / audio will transfer (branch `feature/bal-qr-transfer`)

- The full QR-transfer feature (P0–P6) is implemented, tested and committed on
  `feature/bal-qr-transfer` (commits `d288b55`, `ce3e36d`, pushed to
  `origin`). PR creation URL:
  `https://bitcoin-after.life/gitea/bitcoinafterlife/bal-electrum-plugin/pulls/new/feature/bal-qr-transfer`
- Included: core scheduler (`bal/core/qrtransfer.py`), `QR_CHUNK_SIZE`
  setting (4 export presets), export/import dialogs + review/sign wizard +
  lists/window wiring, export filters, auto slideshow with per-second rate +
  loop option, audio send/receive buttons, and the crash fixes
  (`status` default, `invalidate_will` guard). Docs: README, CHANGELOG entry
  56, QML_PLAN, `AUDIO_MODEM_DEBIAN.md`.
- Follow-up refactor (CHANGELOG entry 57): all `copy.deepcopy` removed —
  `copy_structure()` in `bal/core/util.py`, `WillItem.copy()` / ctor
  serialize/deserialize, `copy_status_table()`. Working tree clean after the
  branch's three commits.
- Verification: batch 377 passed / 2 pre-existing `test_bt_to_date_*`
  failures; ruff no new violations; smoke + `build_zip.py` +
  external-zip OK; pyright clean. The isolated
  `test_heir_relative_anchor.py::test_karen7_frozen_delivery_not_expired`
  failure is pre-existing test pollution (fails identically on clean HEAD,
  passes inside the full batch) — not caused by entry 57.
- Remaining: manual on-device walkthrough of the QR path (and, if wanted,
  the audio path — buttons only appear when the `audio_modem` plugin +
  `amodem` are installed; see the prerequisites below).

### In progress: animated-QR interop (BC-UR v1/v2, BBQR)

- `bal/core/animated_qr.py` implements stdlib-only codecs for **BC-UR v1**
  (BC32 + SHA-256 digest; the bech32_bis checksum variant per
  BCR-2020-004/005), **BC-UR v2** (CBOR part structure, bytewords-minimal,
  CRC-32, xoshiro256-based fountain with alias-sampled mixing) and **BBQR**
  (Coinkite `B$…` base32/hex/zlib frames), plus one shared
  `AnimatedQrSession` with `detect_format` auto-detection and
  `parse_for_detection` frame identity for the GUI debounce.
- Current status as of this session: reference parity, GUI, and tests done;
  not yet committed.
  - **BC32/bytewords/codec parity:** BC32 reproduces the BCR-2020-004/005
    test vectors (`Hello, world`, `Hello world`, the long seed vector);
    bytewords-minimal round-trips with CRC rejection; UR v2 part encode +
    decode is byte-exact against the reference C++ bc-ur encoder for a
    single part, seq_len=2 (12 frames) and seq_len=7 (3 sampled mixes),
    validating CBOR framing, bytewords, alias+ary-threshold sampling,
    xoshiro256** and the XOR mix.
  - **Sessions:** UR v2 single-part (no seq header), out-of-order frames,
    duplicate drops, solve with a missing pure fragment (a second redundant
    mixed wave is emitted by `ur2_frames`), UR v1 single-part
    (digest-less `ur:bytes/<bc32>` accepted) and multipart, BBQR full-frame
    decode in any order for Z/2/H encodings.
  - **Safety:** `_MAX_SESSION_PARTS = 20000`, `_MAX_MESSAGE_BYTES = 32 MB`,
    `TransferConflictError` on a frame from a different transfer,
    `SessionLimitError`, BBQR zlib-bomb guard, UTF-8 payloads only.
  - **GUI:** `BalQrExportWidget` gained a Format selector (BAL QR default,
    BC-UR v1, BC-UR v2, BBQR) reusing the QR-size presets; the importer now
    routes every frame through `detect_format` +
    `AnimatedQrSession.add_part` with the shared
    `qr_import_accept_frame(state, fmt, session_key, frame_total, index,
    payload, stable_reads=2)` debounce (reset on session-key change).
    `_review_and_sign` resolves the session to the transfer text and decodes
    parts uniformly across formats.
  - **Verification:** `tests/test_core_animated_qr.py` (32 tests incl. the
    C++-reference parity vectors and BC32 spec vectors) and the extended
    `tests/test_gui_qr_transfer.py` pass; ruff clean on the new/changed
    files; pyright 0 errors; smoke test, `build_zip.py` and
    `external_zip_test.py` green. Only the pre-existing failures remain
    (`test_bt_to_date_*`, fee-exceeds-balance, karen7 pollution).
  - **Any remaining work:** manual on-device walkthrough of the QR path with
    the new formats; optionally validate against third-party libraries
    (`ur`, `bbqr`) once available; add the docstrings/branch notes already
    captured in `ag1.md`/`ag2.md` context where needed.

**Dev-box audio prerequisites (audio_modem channel):**
See `AUDIO_MODEM_DEBIAN.md` — the full Debian setup + verification, with the
two pitfalls (unversioned `libportaudio.so`, numpy>=2 `tostring` removal):
- `sudo ln -s /usr/lib/x86_64-linux-gnu/libportaudio.so.2 /usr/lib/x86_64-linux-gnu/libportaudio.so`
  (the unversioned name the plugin loads; Debian ships only `.so.2`).
- numpy>=2 patch in `electrum/env/.../amodem/common.py`: `tostring()` →
  `tobytes()` (already applied locally). Both are runtime-env fixes, not repo
  changes; see CHANGELOG entry 56.
- To loop-test on one machine without speakers/mic: during receive,
  `pactl set-default-source <sink>.monitor` (restore after).
