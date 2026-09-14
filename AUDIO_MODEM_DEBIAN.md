# Audio MODEM on Debian — setup & troubleshooting

How to make the optional **audio channel** of BAL (and Electrum's own
`audio_modem` plugin) work on Debian/Ubuntu. The channel lets you send a will
to another device as acoustic OFDM tones instead of scanning QR codes.

Recommended reading before starting: `CHANGELOG.md` entry 56
(Audio-environment notes) and `HANDOFF.md` (Dev-box audio prerequisites).

---

## 1. What you need (three independent pieces)

| Piece                    | Provides                                   | Where it comes from                  |
|--------------------------|--------------------------------------------|--------------------------------------|
| `amodem` (Python)        | OFDM modulation/demodulation               | `pip install amodem` (any venv)       |
| `libportaudio.so`        | sound I/O backend used by `amodem.audio`   | Debian package `libportaudio2` (+ dev symlink, see §2) |
| Electrum `audio_modem`   | the plugin whose `_send`/`_recv` BAL reuses | built into Electrum                   |

BAL shows the audio buttons only when the plugin is **enabled** (Tools →
Plugins → Audio Modem) and `amodem` is importable.

> On a headless/CI box there is no speaker/mic, but the channel can still be
> verified with the **sink-monitor loopback** in §4.

---

## 2. The two line fixes (this is the part everyone forgets)

Debian ships a versioned `libportaudio.so.2` but **not** the unversioned
`libportaudio.so` that old `amodem` code uses, and `amodem` uses NumPy APIs
removed in NumPy 2.x. Both fail **silently** (the plugin's `_send` runs the
load inside a `WaitingDialog` thread without an `on_error` handler).

### 2a. PortAudio unversioned symlink

Install the dev package (creates the unversioned symlink), or create it by
hand:

```bash
sudo apt install libportaudio2 libportaudio-dev   # preferred
# or, without the package:
sudo ln -s /usr/lib/x86_64-linux-gnu/libportaudio.so.2 \
           /usr/lib/x86_64-linux-gnu/libportaudio.so
```

Verify:

```bash
source "$BAL_HOME/electrum/env/bin/activate"
python3 -c "import amodem.audio; print(amodem.audio.Interface(config=None).load('libportaudio.so').call('GetVersionText'))"
# b'PortAudio V19...'  <-- success
```

> **No-sudo alternative** (fine for one-shot tests): point `LD_LIBRARY_PATH`
> at a directory containing a `libportaudio.so` symlink to the `.so.2`:
> ```bash
> mkdir -p /tmp/portaudio_stub
> ln -s /usr/lib/x86_64-linux-gnu/libportaudio.so.2 /tmp/portaudio_stub/libportaudio.so
> export LD_LIBRARY_PATH=/tmp/portaudio_stub:$LD_LIBRARY_PATH
> ```

### 2b. amodem vs NumPy 2.x (`tostring` removed)

`amodem` 1.16.0 calls `numpy.ndarray.tostring()`, removed in NumPy 2.x
(≥ 2.4.6 dies with `AttributeError` on the first sample write, so **no carrier
is ever emitted**). Either pin NumPy < 2, or patch the single line in the
installed package:

```bash
source "$BAL_HOME/electrum/env/bin/activate"
python3 -m pip install "numpy<2"   # option A (downgrade)
# option B (patch; path depends on your site-packages):
sed -i "s/sym.astype('int16').tostring()/sym.astype('int16').tobytes()/" \
  "$BAL_HOME/electrum/env/lib/python3.11/site-packages/amodem/common.py"
```

> This must be done on **every** machine that receives/sends audio (both ends
> of the channel use the same code), and again after reinstalling/upgrading
> `amodem`.

---

## 3. Environment checklist (dev box, already applied)

These were applied on the current dev box and do NOT need to be re-done:

- `amodem` installed in the runtime venv (`1.16.0`).
- `amodem/common.py` patched `tostring()` → `tobytes()`.
- System symlink or `LD_LIBRARY_PATH` stub for `libportaudio.so`.
- PulseAudio running; default sink `ALC236 Analog`, default source DMIC.

Check them in one command:

```bash
source "$BAL_HOME/electrum/env/bin/activate"
python3 - <<'EOF'
import amodem, ctypes, numpy, zlib
print("amodem", amodem.__version__)
print("numpy", numpy.__version__, "(2.x needs the tobytes patch)")
import amodem.audio
amodem.audio.Interface(config=None).load("libportaudio.so")
print("libportaudio.so loaded OK (symlink or LD_LIBRARY_PATH in place)")
EOF
```

---

## 4. Verifying the channel (no speakers/mic needed)

Full **send → sink → sink-monitor → recv** round-trip on one machine:

```bash
# 1) route capture at the loop and remember the original source
MON="$(pactl get-default-sink).monitor"; ORIG=$(pactl get-default-source)
pactl set-default-source "$MON"

# 2) run the round-trip (uses zlib-compressed payload like the plugin)
source "$BAL_HOME/electrum/env/bin/activate"
timeout 90 python3 /tmp/opencode/bal_audio_loopback.py
#   expected: bitrate 1.0 kbps ... send done ... RECV OK

# 3) restore the original source
pactl set-default-source "$ORIG"
```

Any payload you like: `python3 /tmp/opencode/bal_audio_loopback.py "BALQR|1|1|0|hi"`.

With real speakers + mic instead, skip the `pactl` swapping, put the devices
close, keep volumes high, and run the same script.

---

## 5. Testing through the real GUI

1. **Tools → (Plugins) → Audio Modem** → enable it. If asked for settings,
   pick a bitrate: default `slowest()` is ~1.0–1.2 kbps (a ~2 KB will takes
   ~15–20 s of audio); higher bitrates are faster but less robust.
2. Wallet A → BAL will list → **Export → QR Codes → Audio…**
   (the audio transport sends the raw newline-joined tx list, no BAL framing).
3. Wallet B → will list → **Import via QR → Audio…** → wait for
   "Waiting for audio (... kbps)…", a loading cursor while demodulating,
   then the decoded slots appear → review/sign wizard opens.
4. One machine only: apply the §4 monitor trick in the shell where Electrum
   runs (export plays to the sink; import records from the sink monitor).

---

## 6. Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| No sound at all, no error anywhere in the log | `libportaudio.so` not loadable (silent) | §2a symlink or `LD_LIBRARY_PATH` stub |
| Sound played, "Timeout waiting for carrier" on the receive end | Capture routed to the wrong device / mic muted / no speakers | §4 monitor trick; `pactl` source check; raise volume; move devices closer |
| "Decoding failed" after carrier, no payload | Send side died with numpy `tostring` → nothing modulated | §2b patch or `numpy<2` on BOTH machines |
| Buttons "Audio…" missing in BAL dialogs | `audio_modem` disabled in Plugins, or `amodem` not importable in the running venv | Enable plugin; `pip install amodem` |
| Audio too long / too slow | 1 kbps default | Raise bitrate in Audio Modem settings dialog |

---

## 7. No-sudo quick reference (all commands)

```bash
python3 -m pip install amodem
mkdir -p /tmp/portaudio_stub
ln -s /usr/lib/x86_64-linux-gnu/libportaudio.so.2 /tmp/portaudio_stub/libportaudio.so
export LD_LIBRARY_PATH=/tmp/portaudio_stub:$LD_LIBRARY_PATH
# numpy >= 2 (one of):
pip install "numpy<2"   # or patch amodem/common.py tobytes
```