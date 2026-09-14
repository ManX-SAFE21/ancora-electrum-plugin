# PLAN — Will transfer via QR codes / audio modem (Qt now, QML planned)

> Goal: let the user move an inheritance ("will") between devices over two
> air-gap channels:
>
> 1. **QR codes** (primary): export the **valid** inheritance transactions as
>    a sequence of QR codes, and import them back on another machine with the
>    camera;
> 2. **Audio modem** (secondary, when Electrum's `audio_modem` plugin is
>    enabled): send/receive the same payload through the PC speaker +
>    microphone.
>
> Both channels converge on the same review-and-sign flow afterwards.
>
> Status: APPROVED by owner (2026-08-25). No code written yet — this document
> is the implementation contract. Work top-down through §9 Checklist.
>
> Chat language: Italian; this document is in English (global rule R1).

---

## 1. Scope

### In scope

- **Desktop PyQt6 GUI** (primary, implemented by this plan):
  - New plugin setting: QR chunk size, offered as **4 standard presets**.
  - *"Export via QR"* action: serializes the **valid** will transactions,
    optionally compresses, splits the resulting string into fixed-size frames,
    shows one QR at a time with prev/next navigation, live re-chunking and
    progress (`i di N`).
  - *"Import via QR"* action: camera capture dialog with a slot grid
    (1..N); the user selects which shot he is about to capture, scans, the
    frame lands in its slot; when 1..N are filled the payload is assembled,
    parsed into `WillItem`s, validity-checked locally.
  - **Post-capture flow (owner decision D6, amended)**: after capture
    completes there is NO read-only preview. Instead a review-and-sign wizard
    walks through every transaction one at a time showing **outputs
    (address + amount), total outputs, total fees**, signs it (wallet
    password asked once), and at the end **proposes exporting the signed
    transactions** (to file and/or back via QR).
- **Audio-modem channel** (owner decision D7): when the Electrum
  `audio_modem` plugin is enabled and available, the export dialog gains a
  *"Send via Audio Modem…"* button and the import dialog a *"Receive via
  Audio Modem…"* button, reusing the same transfer string (no QR framing).
- **QML**: document-only update to `QML_PLAN.md` adding dedicated view specs
  (owner decision D1). No QML code in this feature.

### Out of scope

- Implementing the QML frontend (gated behind `QML_PLAN.md` Phases 0–2).
- Merging imported wills into the live wallet state (existing Merge flows
  stay unchanged).
- Broadcast of the reviewed transactions (user exports them; broadcasting
  remains an explicit action elsewhere).
- CLI/cmdline parity for QR transfer.

---

## 2. Owner decisions (locked)

| # | Decision |
|---|----------|
| D1 | QML part = update `QML_PLAN.md` document only; implementation later. |
| D2 | Frames carry a small **compact** ASCII header (`BAL1<total><index><flag>`, fixed 11 chars, base36 count fields) — import knows the total, auto-fills the grid, detects corrupt/duplicate/mismatched frames. Pure concatenation rejected. Legacy `BALQR1\|N\|i\|flags\|` export removed; legacy import kept. |
| D3 | Payload = **serialized transaction strings only** (`str(wi.tx)`), NOT the JSON will dump. Loses statuses/metadata on purpose; import rebuilds items like `merge_single_transaction` does. |
| D4 | Compression (zlib+base64 over the whole payload) exported as **best-of**: `encode_transfer_best` ships compressed when it is shorter, plain otherwise; flag `0` = plain, `Z` = compressed. No user-facing checkbox. |
| D5 | 4 standard size presets: **~150 / ~400 / ~900 / ~1800 bytes** of payload per QR (low-res cams → high-res cams). Error-correction level fixed **M**. Stored as plugin config default; selectable again inside the export dialog. |
| D6 | After capture completes: **review + sign each tx one at a time** (show outputs, total outputs, total fees), then **propose export of the signed txs** (file and/or QR). Supersedes the earlier "WillDetailDialog preview" answer. |
| D7 | Add an **audio-modem transfer path** gated on Electrum's `audio_modem` plugin being enabled and available (`amodem` importable). Same payload semantics as QR (transfer string of serialized txs), but NO BAL frame chunking — `amodem` handles transport framing internally. Buttons simply hidden when the plugin is absent/unavailable (info message pointing at `pip install amodem` when enabled-but-broken); graceful degradation, never a hard dependency. |

---

## 3. Verified facts (research done on the local checkouts)

All verified by reading source; references are `file:line`.

| # | Fact | Where |
|---|------|-------|
| F1 | Export today: `BalWindow.export_will()` writes `{wid: WillItem.to_dict()}` JSON; subsets All/Valid/Valid-NC built in `WillList` | `bal/gui/qt/window.py:1605-1621`, `lists.py:666-669, 743-767` |
| F2 | Batch signer: `BalWindow.sign_transactions(password, will, txids)` loops valid txs, resolves input values from change prevouts (`txin._trusted_value_sats` …), calls `wallet.sign_transaction`, updates `COMPLETE` + signature counts | `window.py:1017-1086` |
| F3 | External-will signing already supported (`will=` param, nothing saved to live wallet/history) | `window.py:1443-1484` |
| F4 | Single-tx import precedent: `merge_single_transaction` wraps `WillItem({"tx": str(tx)}, _id=tx.txid(), wallet=...)` | `window.py:1715-1724` |
| F5 | Local validity recomputation recipe (no network): `add_willtree` → `Util.get_available_utxos` → `check_invalidated` → `search_rai` → `check_signatures` | `window.py:1678-1701` |
| F6 | Plugin config accessor pattern `BalConfig`; keys declared in ctor | `bal/core/plugin_base.py:130-154, 211-264` |
| F7 | Plugin settings dialog: grid rows 0..12, `add_widget(grid,label,widget,row,help)` + `_make_reset_btn(cfgvar,widget,kind)`; ADVANCED-only rows wrapped with `_hide_if_basic(...)` | `bal/gui/qt/plugin.py:442-850` (rows at 677-850) |
| F8 | `BalDialog(parent, bal_plugin, title=None, icon=...)` base class anchors to top-level window | `bal/gui/qt/dialogs.py:92-129` |
| F9 | Fee display precedent: `fee = tx.input_value() - tx.output_value()`, fee rate = `fee / tx.estimated_size()` | `bal/gui/qt/widgets.py:1319-1328` |
| F10 | Input-value resolution helper exists: `Will.add_info_from_will(will, wid, wallet)` sets trusted input values from sibling will change outputs | `bal/core/will.py:118-136` |
| F11 | `str(tx)` = `tx.serialize()`: raw hex for complete txs; `PartialTransaction.serialize()` → base64 PSBT. Both accepted by `tx_from_any` (= `Will.get_tx_from_any`). This is exactly how BAL persists/reloads txs today | `electrum/transaction.py:907, 2539`; `will.py:106-113`; `window.py:1041-1044` |
| F12 | `QRCodeWidget` exists but **hardcodes `ERROR_CORRECT_L`** → cannot satisfy D5/M; must render our own `qrcode` instance | `electrum/gui/qt/qrcodewidget.py:35-37` |
| F13 | Camera scanning one-shot API with OS-permission handling: `scan_qrcode_from_camera(*, parent, config, callback(success: bool, error: str, data: Optional[str]))`; on Linux uses zbar CLI backend | `electrum/gui/qt/qrreader/__init__.py:47-64` |
| F14 | QR painting without PIL: `draw_qr(qr, paint_device, ...)` from `electrum.gui.common_qt.util` (what `QRCodeWidget.paintEvent` uses) | `electrum/gui/qt/qrcodewidget.py:63-72` |
| F15 | QR capacity sanity (byte mode, EC **M**): v40-M ≈ 2331 B ≥ 1800 ✓; v10-M ≈ 213 B ≥ 150 ✓; the `qrcode` lib auto-picks the version | `qrcode` lib |
| F16 | `build_zip.py` walks the tree with `os.walk` → new `.py` files ship automatically | `build_zip.py:39-47` |
| F17 | QML fork already has `QRImage.qml`, `QRScan.qml`, `ScanDialog.qml`; ScanDialog carries upstream comment "currently not used on android … qt6 camera support stops crashing" | `electrum/gui/qml/components/ScanDialog.qml:8-9` |
| F18 | `QML_PLAN.md` currently defers chunked multi-QR streams (Phase 3 note + risk R6) — this feature supersedes that deferral | `QML_PLAN.md:228-231, 304` |
| F19 | House test conventions: `def test_*` + `if __name__ == "__main__"` + `sys.path.insert(0, ..pardir)`; run standalone or via pytest | `tests/test_core_heirs.py:1-24` |
| F20 | Fork ships an `audio_modem` plugin. `_send(parent, blob)` zlib-compresses an **ASCII** blob, plays it via speaker through `amodem` inside a `WaitingDialog`; bit-rate selectable in the plugin's own settings (default = `amodem.config.slowest()`) | `electrum/plugins/audio_modem/qt.py:96-110` |
| F21 | `_recv(parent)` records from mic and delivers the decompressed ASCII text by calling `parent.setText(blob)` — the only integration contract is "an object with `setText(str)`"; there is no callback API | `audio_modem/qt.py:112-127` |
| F22 | Plugin lookup for enabled plugins: `window.plugins.get(name)` → instance or `None` (`Plugins.get`, electrum/plugin.py:575-576); availability check is the plugin's own `is_available()` (imports `amodem`). **`amodem` is NOT installed in the runtime env today** → optional dependency (pip `amodem` + libportaudio); feature must degrade gracefully | `electrum/plugin.py:575`, runtime-env check |
| F23 | `amodem.main.send/recv` stream the whole blob with their own framing/training → BAL must NOT apply QR frame chunking on this channel; and since `_send` compresses internally, BAL sends the **plain** transfer string to avoid double compression | consequence of F20/F21 |

---

## 4. Wire format specification

### 4.1 Transfer string

```
transfer_string = "\n".join( tx_str(tx) for tx in valid_txs_sorted_by_txid )
```

- `tx_str(tx)` = `str(tx)` (F11): hex for complete txs, base64-PSBT for
  partially-signed ones. Neither alphabet contains `\n` or `|`, so both are
  safe delimiters.
- Ordering: ascending `tx.txid()` → deterministic output for identical input.
- If compression enabled (D4):
  `transfer_string = base64_ascii( zlib_compress( transfer_string ) )`.

### 4.2 Frame layout (one frame = content of ONE QR code)

Wire format v2 (compact, current export):

```
BAL1<TTT><iii><F><payload>
```

- Magic+version literal `BAL1` (reject anything else with a clear message).
- `<TTT>` = `<iii>` — **base36** zero-padded 3-char strings (`000`…`ZZZ`),
  representing total N and index i, `1 ≤ i ≤ N ≤ 46655`. Fixed width means a
  3-digit count field costs the same for a 1-frame or a 46655-frame transfer.
- `<F>`: single flag char — `0` ⇒ plain, `Z` ⇒ zlib+base64 compressed.
- `<payload>`: the i-th slice of `transfer_string`, exactly
  `chunk_size` bytes each (last slice may be shorter). No separators: both
  base36 count fields are fixed-width, so the header is unambiguously 11
  chars and the payload starts at offset 11.
- Header overhead is a constant **11 bytes** → effective payload =
  `chunk_size − 11`; the chunker slices the transfer string so that
  **header+payload ≤ preset size**.

Legacy frames `BALQR1|<total>|<index>|<flags>|<payload>` (variable-width
decimal header, pipe-separated) are still **imported** by `parse_frame`
(`_parse_v1`), so old exports keep working; the exporter emits v2 only.
That is the one deliberate compatibility break: **v2 frames are not readable
by builds older than this change.**

### 4.3 Size presets (D5)

| Preset label | Payload budget (bytes/frame) | Typical QR version @EC-M |
|--------------|------------------------------|--------------------------|
| Small (low-res cameras) | 150 | ~v10 |
| Medium | 400 | ~v15 |
| Large | 900 | ~v22 |
| XL (high-res cameras) | 1800 | ~v40 |

EC level fixed **M** for scan reliability (D5). Presets live in
`bal/core/qrtransfer.py::CHUNK_PRESETS` so core tests can cover them.

### 4.4 Audio-modem channel (D7, F20-F23)

- Payload = the **plain** `transfer_string` of §4.1 — no BAL frames
  (`split_frames`/`parse_frame` are QR-only), no BAL compression (the plugin
  compresses internally; double compression wastes airtime).
- The existing core functions `encode_transfer(tx_strings,
  compress=False)` + `decode_transfer(text, compressed=False)` are reused
  unchanged; only the transport differs.
- Bit-rate is owned by the audio_modem plugin's settings dialog — BAL adds
  no setting of its own.

---

## 5. New core module — `bal/core/qrtransfer.py`

GUI-free (never imports Qt — house rule). Public API:

```python
MAGIC = "BALQR"
VERSION = 1
FLAG_COMPRESSED = "Z"
CHUNK_PRESETS = [(label_en, budget_bytes), ...]   # §4.3 table

def encode_transfer(tx_strings: list[str], compress: bool = False) -> str
    """Join -> optional zlib+base64 -> return transfer_string."""

def split_frames(transfer_string: str, chunk_size: int) -> list[str]
    """Slice into frames 'BAL1<TTT><iii><F>payload' (v2) — header+payload <=
    chunk_size. Raises QrTransferError over the 46655-frame base36 cap, or
    ValueError if chunk_size < MIN_CHUNK_SIZE."""

def encode_transfer_best(tx_strings: list[str]) -> tuple[str, bool]
    """-> (transfer_string, compressed); ships the shorter of plain vs
    zlib+base64 so the export emits the densest frames."""

def parse_frame(frame: str) -> tuple[int, int, bool, str]
    """-> (total, index, compressed, payload); accepts v2 'BAL1…' and legacy
    'BALQR1|…' (wrapped as _parse_v1/_parse_v2); ValueError on bad magic/
    version/arity/non-numeric fields."""

def assemble(frames: dict[int, str]) -> str
    """Validate indices form exactly range(1..max_total) (taken from any
    frame header), concatenate payloads in order, decode flags ->
    transfer_string. Raises MissingFramesError(indexes) / InconsistentTotalError."""

def decode_transfer(transfer_string: str, compressed: bool) -> list[str]
    """Inverse of encode_transfer -> list of tx strings."""
```

Plus exceptions `QrTransferError(ValueError)`, `MissingFramesError`,
`InconsistentTotalError`. All docstrings/comments English; ruff-clean
(line-length 88, E501 ignored).

---

## 6. Settings (Qt)

1. `bal/core/plugin_base.py`: after `REBUILD_ON_CLOSE` (~line 264) add

   ```python
   self.QR_CHUNK_SIZE = BalConfig(config, "bal_qr_chunk_size", 150)
   ```

2. `bal/gui/qt/plugin.py::settings_dialog` (rows end at 12, ~line 844):
   append row **13** — visible in BASIC and ADVANCED (do NOT wrap with
   `_hide_if_basic`):

   - Label: `"QR Code Size"`
   - `QComboBox` fed from `CHUNK_PRESETS`; item text e.g.
     `"Small — ~150 bytes/QR (low-res cameras)"`; `currentIndexChanged`
     → `self.QR_CHUNK_SIZE.set(budget_bytes)`; initial index from
     `QR_CHUNK_SIZE.get()` (fallback to nearest preset if the stored value
     was customized).
   - `HelpButton` text: explains trade-off (small QR = more shots but easier
     to scan with poor cameras; large QR = fewer shots, needs good camera)
     and that the size can also be changed inside the export dialog.
   - Reset button via existing `_make_reset_btn(self.QR_CHUNK_SIZE, combo, ...)`
     pattern (plugin.py:684).

---

## 7. Qt export flow

### 7.1 Entry point

`bal/gui/qt/lists.py::WillList.create_toolbar` (menu block lines 666-670):

```python
export_menu.addAction(_("Via QR…"), self.export_will_valid_qr)
```

New `WillList.export_will_valid_qr()` mirrors `export_will_valid`
(lists.py:743-754): builds `{wid: wi}` subset of `VALID` items, empty →
`show_message(_("No valid will item to export"))`, else
`self.bal_window.export_will_via_qr(will=subset)`.

### 7.2 `BalWindow.export_will_via_qr(will=None)` (new, `window.py` near
`export_will`)

- Collect `tx_strings = [str(wi.tx) for wid, wi in sorted-by-txid ...]`
  (F11).
- Mark exported items `EXPORTED` (parity with `export_json_file`,
  window.py:1607-1609) — only when `will` came from the live list.
- Open `WillQrExportDialog(self, tx_strings)`.

Shared helper used by both dialogs:

```python
def get_audio_modem_plugin(self):        # on BalWindow
    """Return the loaded audio_modem plugin if enabled AND available
    (amodem importable), else None. Never raises."""
    p = self.window.plugins.get("audio_modem")   # F22
    return p if p is not None and p.is_available() else None
```

### 7.3 `WillQrExportDialog(BalDialog)` (new class in `dialogs.py`)

Layout:

```
[Size ▾ Small/Medium/Large/XL]   ← compressed best-of automatically (no checkbox)
[            QR image           ]   ← BalQrImage (see below)
«i di N»        [◀ Prev] [Next ▶]
[Save current QR as PNG…]  [Send via Audio Modem…]  [Close]
```

Behaviour:

- On any control change: rebuild `split_frames(encode_transfer_best(...))`,
  reset index to frame 1, refresh counter (owner requirement: "cambiare la
  risoluzione").
- `BalQrImage(QWidget)` ≈ trimmed copy of `QRCodeWidget`
  (`electrum/gui/qt/qrcodewidget.py:21-72`) but constructing
  `qrcode.QRCode(error_correction=ERROR_CORRECT_M, border=2)` and painting
  via `electrum.gui.common_qt.util.draw_qr` (F12/F14). ~30 lines.
- Prev/Next wrap or disable at ends (disable chosen: clearer).
- PNG export optional convenience via existing
  `getSaveFileName` + `QWidget.grab()` (same trick as
  `qrcodewidget.py:110`).
- **Send via Audio Modem…** (D7): shown only when
  `bal_window.get_audio_modem_plugin()` returns a usable instance (below);
  otherwise hidden. Handler: re-encode the payload **plain**
  (`encode_transfer(tx_strings, compress=False)`) and call the plugin's
  `_send(parent=self, blob=transfer_string)` — its own WaitingDialog owns
  progress/cancellation (F20). Tooltip when hidden is unnecessary; instead,
  if the plugin is enabled but `is_available()` is False, show an info
  message pointing to `pip install amodem` + portaudio (F22).

---

## 8. Qt import flow + review/sign wizard

### 8.1 Entry point

`lists.py` toolbar menu, next to Import/Merge (lines 670-671):

```python
menu.addAction(_("Import via QR…"), lambda: self.bal_window.import_will_via_qr())
```

`BalWindow.import_will_via_qr()` opens `WillQrImportDialog(self)`.

### 8.2 `WillQrImportDialog(BalDialog)`

State: `self.frames: dict[int, str]`, `self.total: int | None`,
`self.target_index: int | None`.

Layout:

```
«Captured k of N»          [Scan ▶]   [Reset]
[slot grid: push-buttons 1..N; states: empty / filled ✓ / selected-target]
hint line («Select a slot, then scan» / «Scan the first QR»)
              [Receive via Audio Modem…]  [Review & Sign ▶] [Close]
```

Behaviour:

- **Scan** → `scan_qrcode_from_camera(parent=self,
  config=self.bal_window.window.config, callback=self._on_scan)`
  (F13). One-shot per press; dialog stays open between shots (simplest,
  matches Electrum UX; no continuous mode).
- `_on_scan(success, error, data)`:
  - failure → `show_error(error)` (covers missing zbar/camera too);
  - `parse_frame` errors → `show_warning(_("Not a BAL will QR"))`;
  - first valid frame adopts `total` and materializes the slot grid;
  - frame whose `total` ≠ adopted total → warn + offer Reset (user may have
    restarted the export with another size);
  - valid → `frames[index] = payload`; auto-advance `target_index` to the
    lowest missing index; refresh grid + counter.
- Clicking an empty slot sets `target_index` (owner requirement: manual
  shot selection); a filled slot click asks to overwrite.
- **Receive via Audio Modem…** (D7): shown only when
  `bal_window.get_audio_modem_plugin()` returns a usable instance. Handler:
  build a tiny adapter object exposing `setText(str)` that stores the text
  and invokes the shared post-receive continuation, then call
  `plugin._recv(parent=self, ...)`-style flow (F21 contract). On success the
  received string is treated as the **whole payload**: skip frames/slots
  entirely → `decode_transfer(text, compressed=False)` → continue at §8.2's
  item-building step (WillItem construction + validity pass + wizard).
  Errors from the modem surface through the plugin's own dialog; empty
  result (user cancelled) is silently ignored.
- **Review & Sign** enabled only when `set(frames) == set(range(1, N+1))`:
  runs `assemble` + `decode_transfer` → `list[str]`; any `QrTransferError`
  surfaces as `show_error` and keeps the dialog open.
- Build items exactly like `merge_single_transaction` (F4):
  `WillItem({"tx": s}, wallet=self.wallet)` per string; failures per-string
  are collected and reported at the end (bad string ≠ fatal for the rest).
- Local validity pass (F5 recipe) on the resulting dict; items failing
  `VALID` are dropped and listed in a warning. Set
  `wi.set_status("IMPORTED", True)` on survivors (mirrors
  `import_will_into_details`, window.py:1753-1754).
- Then `close()` and start the wizard (§8.3) with the valid subset. Empty
  result → stop with a message.

### 8.3 `WillTxReviewSignDialog(BalDialog)` — post-capture wizard (D6)

Constructed with `(bal_window, willitems: dict[str, WillItem])` — the
imported subset lives **outside** the live wallet state (external mode,
F3).

Flow:

1. **Password once**: `password = bal_window.get_wallet_password()`
   (window.py:1088-1100). Returns `False` on cancel → abort wizard; `None`
   means unencrypted wallet → proceed without password.
2. **Per-transaction page** (one `QStackedWidget` step per tx, ordered by
   txid like export):

   ```
   Tx 2 of 5 — a1b2…c3d1 (short txid)
   Locktime: 2033-04-05            Status: unsigned (0/1 sigs)
   ┌ outputs ─────────────────────────────────┐
   │ bc1q…heir1            0,042 BTC          │
   │ bc1q…willexec fee     0,00012 BTC        │
   │ bc1q…change           0,00988 BTC        │
   └───────────────────────────────────────────┘
   Total outputs: 0,052 BTC        Fees: 420 sat (1.2 sat/vB)
   [Sign & Next ▶]   [Skip]   [Cancel all]
   ```
   - Outputs from `tx.outputs()` (address via `TxOutput.get_ui_address_str()`
     style helpers already imported in the qt layer; value via
     `bal_window.window.format_amount`).
   - Totals: `output_value()` sum; fees via `input_value() - output_value()`
     after resolving inputs with `Will.add_info_from_will(will, wid, wallet)`
     (F10); `-1`/unknown handled like widgets.py:1319-1324 (F9).
3. **Sign & Next** → sign this single tx through a **refactored helper**
   extracted from the loop body of `sign_transactions`
   (window.py:1037-1083 → `_sign_single_tx(tx, willitems, password)` kept
   byte-equivalent; batch method calls the helper per iteration so existing
   behaviour/tests are unaffected). Update `COMPLETE`/sig-counts exactly as
   today; then advance.
4. **Skip** leaves the tx untouched and advances. **Cancel all** stops; the
   already-signed txs remain in the wizard's local dict (still exportable —
   confirmation dialog warns about skipped ones).
5. **Summary page**: `signed X of Y`, skipped/failed lists, then:

   ```
   [Save signed file…]   [Show QR…]   [Close]
   ```
   - *Save file* = existing JSON path: `export_meta_gui(window,
     "will.json", writer)` writing `{wid: wi.to_dict()}` of the signed
     subset (same serializer as `export_json_file`, window.py:1605).
   - *Show QR* = `WillQrExportDialog` over `[str(wi.tx)]` of the signed
     subset (the online machine can scan them straight into Merge).
   - Nothing touches `self.willitems`/history (external-mode rule, F3).

---

## 9. Checklist (execution order — tick here when resuming work)

- [x] **P0** `bal/core/qrtransfer.py` + unit tests `tests/test_core_qr_transfer.py`
      (cases: round-trip plain/compressed; boundaries: len%size==0, size>len,
      min-size guard; bad magic/version; missing middle frame; duplicate
      overwrite; inconsistent totals; multi-PSBT mixes; presets sanity vs
      qrcode capacities F15). Run:
      `QT_QPA_PLATFORM=offscreen python3 tests/test_core_qr_transfer.py`
- [x] **P1** Settings: `QR_CHUNK_SIZE` config var + settings-dialog row 16
      (ø16) + reset kind (§6). Verify in `QT_QPA_PLATFORM=offscreen` GUI run.
- [x] **P2** Export: `BalWindow.export_will_via_qr`, `get_audio_modem_plugin`
      helper, `WillList` menu action, `WillQrExportDialog` + `BalQrImage`,
      audio-modem send button (§7).
- [x] **P3** Import: `import_will_via_qr`, `WillQrImportDialog` (§8.2),
      incl. camera error paths, audio-modem receive button (local mirror of
      `_recv`, `setText` sink replaced by a callback), plain-payload fast
      path into the wizard.
- [x] **P4** Wizard: `_prepare_and_sign_tx` refactor + `WillTxReviewSignDialog`
      (§8.3). Regression-gate: full batch sign still green
      (`tests/test_core_*.py` offline batch; `tests/test_gui_*.py` batch
      including new `tests/test_gui_qr_transfer.py`).
- [x] **P5** Docs & QML plan sync: update `QML_PLAN.md` — Phase 2 models +=
      `BalQrTransferModel` (thin QObject over `bal.core.qrtransfer`),
      Phase 3 += dedicated views `BalQrExportPage.qml` /
      `BalQrImportPage.qml` (slot grid + `QRScan` reuse), delete the
      "chunked streams deferred" note, rewrite R6 mitigation, add Android
      caveat quoting F17 with file/paste fallback; README/HANDOFF sections;
      CHANGELOG numbered entry 56 at END (house rule).
- [x] **P6** Release hygiene: `python3 build_zip.py` +
      `QT_QPA_PLATFORM=offscreen python3 tests/smoke_test.py
      electrum.plugins.bal` + external-zip test; ruff (repo venv
      `venv/bin/ruff`) no NEW violations; pyright false-positive policy per
      AGENTS.md. Version bump only via `make-release.sh` (owner-driven).
      Docs: document audio-modem as OPTIONAL channel — requires the
      Electrum `audio_modem` plugin enabled plus `pip install amodem`
      and libportaudio (not installed in the dev runtime env today, F22);
      manual test matrix gains an audiomodem round-trip row (two machines,
      default slowest bitrate) marked optional/skippable when hardware
      unavailable.

---

## 10. Risks & mitigations

| Risk | Mitigation |
|------|------------|
| High frame counts annoy users (e.g. 40+ QR at 150 B) | Presets span 150→1800; compress option; counter always visible |
| Big QR versions fail on cheap cameras | EC=M fixed; Small preset targets low-res cams (D5 rationale) |
| User rescans old export with different total | `InconsistentTotalError` → clear warning + Reset (§8.2) |
| `_sign_single_tx` refactor regresses batch signing | Byte-equivalent extraction; batch callers unchanged; offline core tests gate P4 |
| Imported txs reference UTXOs the importing wallet doesn't know | Validity pass drops them with an explicit report instead of silently merging garbage |
| zbar/camera unavailable (esp. Windows/macOS packaging) | `scan_qrcode_from_camera` error path → suggest file export/import fallback |
| `amodem`/portaudio not installed (current dev env state, F22) or audio_modem plugin disabled | Buttons simply hidden; QR/file remain the primary channels; P6 documents the optional dependency |
| Audio transfer fails mid-way (noise, wrong volume) | Plugin's WaitingDialog surfaces the error; user retries — nothing to clean up on BAL side (single atomic blob, no slot state touched) |
| Very slow airtime at default slowest bitrate | Bitrate is selectable in the audio_modem plugin's own settings (F20); BAL adds no knob; tooltip in export dialog hints at large payloads |
| Qt6 camera instability on Android (future QML work) | Recorded as caveat in QML_PLAN update (P5), file/paste stays the primary mobile fallback |

---

## 11. Findings log (append-only)

- 2026-08-25: plan drafted after code exploration; owner answered D1-D6
  (D6 amended live from "preview dialog" to "review+sign wizard").
- Verified F12 (QRCodeWidget hardcodes EC-L) and F11 (str(tx) round-trip
  guarantees) — both shaped §§4/7.
- 2026-08-25: owner requested an audio-modem transfer path → researched
  `electrum/plugins/audio_modem/qt.py`, added D7 + F20-F23, §4.4, buttons
  in §§7.3/8.2, checklist/risk updates. Key constraint found: `_recv`'s
  only contract is `parent.setText(blob)` (F21) → thin adapter object; and
  BAL must not chunk/compress on this channel (F23). `amodem` is NOT in
  the runtime env yet — feature is strictly optional.
