# PLAN_ANDROID_READER.md — BAL Reader: an Android QR will reader

**Date:** 2026-09-09 · **Status:** proposed · **Ties into:** BAL plugin QR export (BALQR / BC-UR v1 / BC-UR v2 / BBQR)

## 1. Goal

Ship a minimal single-activity Android app that reads any QR will exported by the BAL
Electrum plugin, decodes all four supported wire formats, and lets the user view, copy,
share, or save the recovered will data. The app is a **reader only** — it never signs or
broadcasts.

## 2. Scope

**In scope**

- Continuous camera capture (CameraX + ML Kit on-device barcode scanning, QR-only).
- Auto-detection of the four formats (`detect_format`: `"balqr" / "ur1" / "ur2" / "bbqr"`).
- Order-independent frame assembly: duplicates ignored, out-of-order accepted, UR v2
  XOR-fountain redundancy exploited, session-level reset on transfer switch.
- Whole-will JSON **and** tx-hex-list payloads, both decoded to a readable view.
- Copy raw transfer text / Share / Save via Storage Access Framework.
- Verified decode chain runnable on the dev machine (no Android needed).

**Out of scope**

- Signing, broadcasting, password handling, wallet integration.
- Export/authoring QR codes *from* the phone.
- Audio-modem transport (unchanged, plugin-only).

## 3. Architecture

### 3.1 Why Chaquopy (reuse the tested Python codecs)

`bal/core/animated_qr.py` (1178 lines) and `bal/core/qrtransfer.py` (212 lines) are
**pure stdlib** (`base64`, `hashlib`, `zlib`), GUI-free, Python-3.8-clean (verified: no
walrus/match/`X|Y`/PEP-585 generics), and `bal/core/__init__.py` is an empty docstring.
Chaquopy bundles CPython into the APK, so **the exact, battle-tested codec modules run
unchanged on Android** with zero port risk. The Kotlin side is only camera glue + UI.

### 3.2 Decode chain (mirror of the plugin's import tail)

The plugin's `_review_and_sign` (dialogs.py:3792) does exactly:

1. `session.resolve()` -> `(transfer_text, compressed: bool)`
   (UR v1/v2/BBQR for the first pair; `(text, flag)` for BAL QR).
2. `decode_transfer(transfer_text, compressed)` -> list of parts (tx hexes, or the single
   whole-will JSON).
3. `"\n".join(parts)` -> opaque payload.
4. `decode_will_payload(payload)` -> `("will", dict)` **or** `("txs", [strings])`.

The app reuses all of it verbatim through the `AnimatedQrSession` facade (`add_part`
auto-detects per frame, dedups, tracks `received/total/done`).

### 3.3 Data flow

```
CameraX ImageAnalysis -> ML Kit BarcodeScanning (QR) -> raw string
   -> BalDecoder.add(text)  [Chaquopy -> AnimatedQrSession.add_part]
   -> status: format chip + received/total, duplicate-tolerant
   -> when done: auto-stop -> BalDecoder.finish() (4-step decode)
   -> ResultActivity: JSON view OR tx-list view + Copy/Share/Save
```

## 4. Repository layout (new `android/` subfolder)

```
android/
  README.md                        # build (Android Studio), usage, codec-sync rule, MIT note
  settings.gradle.kts
  build.gradle.kts                 # root: plugins (AGP 8.10, Kotlin 2.0.21, Chaquopy 17, apply false)
  gradle.properties
  gradlew, gradlew.bat
  gradle/wrapper/                  # gradle-wrapper.properties + gradle-wrapper.jar (fetched; see §7)
  scripts/
    sync_codecs.py                 # copy bal/core/{__init__,animated_qr,qrtransfer}.py -> app python dir; import-check
  test_chain/
    verify_chain.py                # full decode-chain simulation, runs on dev machine
  app/
    build.gradle.kts               # com.android.application + com.chaquo.python; CameraX/ML Kit deps
    src/main/
      AndroidManifest.xml          # CAMERA permission
      java/life/after/bitcoin/
        MainActivity.kt            # permission + PreviewView + ImageAnalysis + status header
        BalDecoder.kt              # Chaquopy bridge (§5.1)
        ResultActivity.kt          # viewer + copy/share/save (§5.3)
      res/layout/activity_main.xml, activity_result.xml
      res/values/strings.xml
      python/bal/core/             # SYNCED COPIES (committed, deterministic; regenerate with script)
        __init__.py
        animated_qr.py
        qrtransfer.py
```

## 5. Component specifications

### 5.1 `BalDecoder` (Kotlin, Chaquopy bridge)

- Lazy init: `Python.start(AndroidPlatform(context))`, `getModule("bal.core.animated_qr")`.
- `fun add(text: String): String` -> `session.add_part(text)`; surfaces `"ok"`/`"dup"`;
  maps `AnimatedQrError` subclasses to a result the UI can ignore (garbage frames) vs
  reset (transfer switch -> `TransferConflictError` -> tell user to rescan).
- `val format: String?` (`"balqr"/"ur1"/"ur2"/"bbqr"`), `val received: Int`,
  `val total: Int`, `val done: Boolean` (auto-converted by Chaquopy).
- `fun finish(): DecodedResult` - the 4-step chain; returns
  `data class DecodedResult(kind: "will"|"txs", data: Map<String,Any>|List<String>, rawTransfer: String)`.
- `fun reset()` -> new `AnimatedQrSession` (new scan).

### 5.2 `MainActivity` (camera + scan loop)

- Launches CameraX via `ProcessCameraProvider`; `PreviewView` fills screen; camera
  permission via `ActivityResultContracts.RequestPermission`.
- `ImageAnalysis` `STRATEGY_KEEP_ONLY_LATEST`; analyzer throttled (~100 ms) calls ML Kit
  `BarcodeScanning` with `Barcode.FORMAT_QR_CODE`.
- Thread-safe feed to `BalDecoder` (analyzer runs on a background executor); UI status
  via `runOnUiThread`.
- Header row: format chip + `received/total`; on `done` -> stop analyzer -> launch
  `ResultActivity` (results as Parcelable); "New scan" restarts.
- Garbage / incomplete frames silently ignored (same policy as the plugin); a
  `TransferConflictError` mid-scan resets the session and signals the user to rescan.

### 5.3 `ResultActivity` (viewer)

- `kind == "will"`: whole-will JSON - each item's `tx` hex shown truncated with full view
  on demand.
- `kind == "txs"`: list of tx hexes.
- Action bar: **Copy** (raw transfer text to clipboard), **Share** (ACTION_SEND
  text/plain), **Save** (SAF `ACTION_CREATE_DOCUMENT` -> `will.json` / `will_tx.txt`).
- "Scan another" button -> finish -> back to camera.

### 5.4 `scripts/sync_codecs.py`

- Copies the 3 files from `bal/core/` -> `app/src/main/python/bal/core/` (idempotent).
- Post-copy check (dev machine): import `bal.core.animated_qr`, run one UR v2 frame +
  decode cycle to prove the copy imports standalone.
- Documented as the rule after any codec change (README).

### 5.5 `test_chain/verify_chain.py`

Simulates the exact APK runtime path on the dev machine (no Android). Generates frames
precisely as the export page does, then feeds `AnimatedQrSession.add_part` in
scrambled/duplicated/dropped order and asserts correct results for:

- BAL QR multi-frame, plain **and** compressed (`Z` flag): `split_frames(encode_transfer(...))`.
- UR v1 single-part (`ur1_frames` headerless) and `1ofN` multipart.
- UR v2 single-part and fountain multipart with >=1 frame dropped and >=1 duplicated
  (exercises the XOR solve).
- BBQR `Z`/`H`/`2`: `bbqr_frames(..., encoding=...)`, with the `Z` auto-decompress branch.
- payload kinds: whole-will JSON **and** tx-hex list.
- transfer-switch: feed a frame of a different transfer mid-session -> expect conflict, as
  the UI will.

Runs under the runtime venv:
`source .../electrum/env/bin/activate && QT_QPA_PLATFORM=offscreen python3 android/test_chain/verify_chain.py`

## 6. Wire-format reference (bundled codecs)

- **BAL QR**: `BALQR1|<total>|<index>|<flags>|<payload>`, flags `""` or `Z` (zlib+base64).
  Concatenate payloads 1..total -> transfer string -> `decode_transfer` splits on `\n`.
- **BC-UR v1**: multipart `ur:bytes/<seq>of<seq_len>/<sha256-bc32-digest>/<bc32-frag>`;
  single-part `ur:bytes/<bc32>` (digest-less). BC32 = bech32_bis (XOR `0x3FFFFFFF`)
  5-bit alphabet.
- **BC-UR v2**: multipart `ur:bytes/<seq>-of-<seq_len>/<bytewords-minimal-part>` +
  single-part headerless; part body = CBOR `[seq, seq_len, msg_len, crc32, data]` +
  per-part CRC-32, bytewords-minimal; fountain via `choose_fragments` (xoshiro256** +
  alias/threshold), mixed by XOR - decoder solves from any sufficient subset.
- **BBQR**: `B$<encoding><type><base36 total><base36 index><payload>`; encodings `H`
  (upper hex), `2` (base32nal), `Z` (deflate `wbits=-10` -> base32).

## 7. Toolchain & versions

| Item | Version | Notes |
|---|---|---|
| Chaquopy | 17.0.0 | Python 3.10-3.14, AGP 7.3-9.2, minSdk 24 |
| AGP | 8.10.0 | in Chaquopy 17 range |
| Gradle wrapper | 8.14 | required by AGP 8.10 |
| Kotlin | 2.0.21 | |
| compile/target SDK | 35 / min 24 | |
| JDK | 17 (machine has OpenJDK 17) | |
| CameraX | 1.3.4 | `camera-core`, `camera-camera2`, `camera-lifecycle`, `camera-view` |
| ML Kit barcode | 17.3.0 | on-device, no API key |
| Python (codec) | 3.12 (Chaquopy) | codecs verified 3.8-clean |

Dev machine: Android Studio + JDK 17 present; SDK platforms/build-tools/gradle absent ->
the **first real APK build happens in Android Studio with network**. `gradle-wrapper.jar`
is fetched via `curl` (canonical location) so `./gradlew` works; if network is blocked,
README documents Android Studio regenerating it.

## 8. Build & run outline (for README)

1. Open `android/` in Android Studio (or `./gradlew assembleDebug`).
2. Allow Gradle to fetch wrapper/deps (network).
3. Install on device; grant camera permission.
4. In the plugin: export dialog -> pick format (start with BAL QR default; also test
   UR v1/UR v2/BBQR) -> show the animated QR on screen.
5. Point camera at screen; watch `received/total`; decoded result appears ->
   Copy/Share/Save.

## 9. Verification

**On this machine (no Android needed)**

1. `python3 android/scripts/sync_codecs.py` -> copy + import/roundtrip sanity.
2. `QT_QPA_PLATFORM=offscreen python3 android/test_chain/verify_chain.py` -> all
   formats/payload kinds/conflict cases pass.
3. `ruff check android/scripts/sync_codecs.py android/test_chain/verify_chain.py` -> clean.
4. `pytest tests/test_core_*.py tests/test_gui_*.py` -> still **445 passed**
   (payload code untouched; only new files).
5. `python3 build_zip.py` unaffected (no change under `bal/`).

**On-device (manual, user)**

- Walk the 4 formats against the plugin's export page, single- and multi-frame
  (animated), on a real phone.
- Confirm format chip, progress, auto-finish, Copy/Share/Save.

**Boundary** - no APK is produced by this machine's environment; the artifact is a
complete, independently buildable source tree + verified codec path.

## 10. Risks & notes

- First Gradle sync needs network (deps + wrapper). Pinned versions are conservative;
  knobs documented.
- Bundled codec copies must be regenerated after any `bal/core/animated_qr.py` /
  `qrtransfer.py` change - handled by `sync_codecs.py` + README note (copies are
  committed for deterministic builds).
- Animated QR reading depends on the camera catching enough distinct frames (ML Kit
  analyzer keeps scanning; the session dedups and accepts out-of-order). Slow phone
  screens / glare may raise time-to-complete - expected, same as the plugin.
- App name/package (`life.after.bitcoin`, label "BAL Reader") are placeholders - trivial
  to change.
- MIT: bundled codec files inherit the plugin's MIT license (noted in README).