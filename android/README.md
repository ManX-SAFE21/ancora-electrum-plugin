# BAL Reader (Android)

A minimal Android app that reads a Bitcoin will exported by the
[BAL Electrum plugin](https://bitcoin-after.life) directly from your screen,
then lets you view, copy, share, or save the recovered data. **Reader only** —
it never signs or broadcasts.

It decodes **all four** transfer formats the plugin can export, auto-detecting
the format from the first frame:

- **BAL QR** (the plugin's default), single- and multi-frame, plain and
  zlib-compressed, compact `BAL1` header (legacy `BALQR1` frames are still
  accepted on import);
- **BC-UR v1** (single-part and `NofM` multipart);
- **BC-UR v2** (single-part and XOR-fountain multipart — it tolerates dropped,
  repeated, and out-of-order frames);
- **BBQR** (`Z`/`H`/`2` encodings).

Both payload kinds are handled: the **whole-will JSON** and the plain
**transaction-hex list**.

## How decoding works

The app does not reimplement the QR formats. It bundles the plugin's own
codec modules — `bal/core/__init__.py`, `bal/core/animated_qr.py`,
`bal/core/qrtransfer.py` — and runs them verbatim through **Chaquopy** (CPython
on Android). Kotlin is only camera glue and UI:

```
Camera (CameraX) → ML Kit QR detection (on-device, no API key)
    → BalDecoder.add(text)   → bal.core.animated_qr.AnimatedQrSession
    → when done → BalDecoder.finish()
        = session.resolve() → qrtransfer.decode_transfer()
        → balreader.payload.decode_will_payload()
    → ResultActivity: view / copy / share / save
```

The decode tail mirrors the plugin's import dialog function-for-function, and
`android/test_chain/verify_chain.py` proves the bundled code decodes every
format the way the desktop import does (including scrambled, duplicated, and
missing frames).

## Repository layout

```
android/
├── app/src/main/
│   ├── AndroidManifest.xml
│   ├── java/life/after/bitcoin/
│   │   ├── BalDecoder.kt      Chaquopy bridge over the bundled codecs
│   │   ├── MainActivity.kt    camera + ML Kit scan loop + progress
│   │   └── ResultActivity.kt  viewer (copy / share / save)
│   └── python/                bundled Python (regenerate, do not hand-edit)
│       ├── bal/core/          SYNCED COPY of the plugin codecs
│       └── balreader/payload.py  verbatim copy of dialogs.decode_will_payload
├── scripts/
│   ├── sync_codecs.py         re-copy + verify the bundled codecs
│   └── build_apk.py           resync codecs, run Gradle, print APK + sha256
└── test_chain/verify_chain.py decode-chain simulation for all formats
```

## Build

You need Android Studio (Jellyfish or newer), JDK 17, an Android SDK with
platform 35, and a network connection for the first Gradle sync.

1. Open this `android/` folder in Android Studio and let it sync (it will
   fetch the Gradle wrapper 8.14, AGP 8.10.0, Kotlin 2.0.21, Chaquopy 17.0.0,
   CameraX 1.3.4, and ML Kit).
2. Connect a phone (API 24+) or start an emulator and press **Run**.
3. Grant the camera permission when asked.

Alternatively, from the command line (from the repository root):

```bash
python3 android/scripts/build_apk.py            # debug APK + sha256
python3 android/scripts/build_apk.py --release  # (unsigned) release APK
```

The script re-synchronises the bundled codec modules first (so the APK always
carries the current `bal/core` sources), runs `./gradlew`, and prints the APK
path, size and sha256. Flags: `--no-sync` (skip the re-sync), `--offline`
(Gradle without downloads), `--clean`, `--verbose`.

Equivalent raw Gradle call:

```bash
cd android
./gradlew assembleDebug   # APK: android/app/build/outputs/apk/debug/app-debug.apk
```

### If the Gradle wrapper jar is missing

`gradle/wrapper/gradle-wrapper.jar` is committed so `./gradlew` works out of
the box. If it is ever absent, Android Studio regenerates it on the first
sync; no manual steps needed.

## Use

1. In Electrum + BAL, open the will's **export** dialog.
2. Pick a format — start with the default **BAL QR**, then try **BC-UR v1**,
   **BC-UR v2**, and **BBQR**.
3. Make sure the wording toggle shows a payload (business logic), then display
   the animated QR and keep it on screen.
4. Point the phone at the screen. The header shows the detected format and
   `received / total`; scanning stops automatically when the transfer is
   complete.
5. On the result screen: **Copy** the raw transfer, **Share** it, **Save** it
   as `will.json` (whole will) or `will_tx.txt` (transaction list), or press
   **Scan another**.

Notes:

- Keep the phone still and the whole QR inside the frame (the codec dedups
  repeated frames, so a slow capture is fine).
- If the camera glares off the screen, reduce brightness or tilt slightly.
- If scanning jumps between exports, the app detects the format switch,
  resets, and asks you to let it re-scan.

## Keeping the bundled code in sync with the plugin

The codecs under `app/src/main/python/bal/core/` are **committed copies** for
deterministic builds, but they must stay identical to the plugin. Re-run this
after changing `bal/core/animated_qr.py` or `bal/core/qrtransfer.py` (and
after any change to `decode_will_payload` in `bal/gui/qt/dialogs.py`, which
mirrors `balreader/payload.py`):

```bash
python3 android/scripts/sync_codecs.py        # copy
python3 android/scripts/sync_codecs.py --check # verify only (CI-friendly)
python3 android/test_chain/verify_chain.py     # full decode-chain regression
```

`verify_chain.py` fails if the app's `balreader/payload.py` ever drifts from
the plugin's `decode_will_payload` (AST identity + result parity).

## Version pins (see `PLAN_ANDROID_READER.md`)

| Item | Version |
|---|---|
| AGP | 8.10.0 |
| Gradle | 8.14 (wrapper) |
| Kotlin | 2.0.21 |
| Chaquopy | 17.0.0 (Python 3.12) |
| compile / target / min SDK | 35 / 35 / 24 |
| CameraX | 1.3.4 |
| ML Kit barcode-scanning | 17.3.0 |
| JDK | 17 |

## License

MIT. The bundled Python codec files inherit the plugin's MIT license
(`bal/LICENSE`); see `app/src/main/python/bal/` for attribution.