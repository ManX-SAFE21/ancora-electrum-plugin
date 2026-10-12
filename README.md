# Safe21 Heartbeat (Electrum plugin)

*As long as you're here, your bitcoin stays yours.*

Safe21 Heartbeat is a plugin for the [Electrum](https://electrum.org) wallet.
It builds pre-signed, time-locked backup transactions, optionally kept by
independent will-executor servers. While you keep refreshing them (your
heartbeat), nothing happens; if you stop, from the date you chose they send
your bitcoin to the people you chose.

Designed by [Safe21](https://safe21.io). Based on
[Bitcoin After Life (BAL)](https://bitcoin-after.life) by Svatantrya — MIT
licence (see [`bal/LICENSE`](bal/LICENSE)). The internal package name is still
`bal`.

This repository contains a **refactored and extended** version of the original
BAL plugin. The logic was reorganized to cleanly separate **business logic** from the
**PyQt GUI**, and new features have been added including a headless CLI,
auto-rebuild on new transactions, OP_RETURN heirs, and configurable calendar
reminders.

## Repository layout

```
bal/                     the installable Electrum plugin package
├── manifest.json        plugin metadata (Electrum reads this)
├── qt.py                Qt entry-point shim (re-exports Plugin)
├── cmdline.py           CLI entry-point shim (re-exports Plugin)
├── core/                GUI-free logic (importable without Qt)
│   ├── util.py
│   ├── plugin_base.py
│   ├── heirs.py
│   ├── will.py
│   ├── willexecutors.py
│   ├── checkalive.py
│   ├── reminders.py
│   ├── qrtransfer.py       transfer-string codec / QR size presets
│   ├── animated_qr.py      BBQR codec (stdlib-only)
│   └── input_rules.py
├── cli/                 headless command-line layer (no Qt)
│   ├── commands.py      bal_* daemon commands (@plugin_command)
│   ├── controller.py    headless BalController (replicates BalWindow)
│   └── plugin.py        CLI Plugin entry point
├── gui/qt/              PyQt6 presentation layer
│   ├── theme.py         status → color mapping
│   ├── common.py        shared imports / helpers
│   ├── widgets.py       leaf widgets
│   ├── calendar.py      calendar widget
│   ├── dialogs.py       dialog windows
│   ├── lists.py         tree/list views
│   ├── window.py        per-wallet GUI controller
│   ├── window_utils.py  GUI utility helpers
│   └── plugin.py        Plugin (Electrum @hooks → GUI)
├── icons/  wallet_util/  LICENSE  README.md
build_zip.py             builds a clean, zipimport-friendly distribution zip
tests/                   smoke + external-zip regression tests
```

## Requirements

- **Electrum 4.7.2 or 4.8.0** — the plugin detects which wallet-DB
  registration API is available (`json_db.register_dict` on 4.7.2,
  `stored_dict.register_name` on 4.8.0) and adapts automatically.
- **PyQt6** (bundled with the Electrum desktop GUI).

## Wallet compatibility

Safe21 Heartbeat currently supports **standard (single-signature) wallets** and
**hardware wallets** supported by Electrum. **Multisig wallets** and
**Electrum TrustedCoin (2FA) wallets** are **not yet supported** — see
[`COMPATIBILITY.md`](COMPATIBILITY.md) for the full compatibility matrix and
current status.

## Installation

### Build the distribution archive

```bash
python3 build_zip.py
# -> bal-electrum-plugin.zip  (prints size + SHA-256 for integrity checks)
```

The builder writes a `zipimport`-friendly archive (files only, standard
DEFLATE, deterministic order) to avoid loader errors seen on some Electrum
portable builds.

### Install as an external plugin (zip)

1. Electrum → **Tools → Plugins** → install from file → pick the built zip.
2. Enable **Safe21 Heartbeat** and restart Electrum.
3. (Recommended) verify the downloaded zip's SHA-256 matches the value printed
   by `build_zip.py`.

### Install as an internal plugin

Copy the `bal/` directory into your Electrum installation's
`electrum/plugins/` directory, so that `electrum/plugins/bal/manifest.json`
exists, then enable it from **Tools → Plugins**.

## Transfer a will with QR codes (or audio)

From the will list (**Export → QR Codes**) a will can be exported as a
sequence of QR codes and imported on another device (**Import via QR**). The
export offers All / Valid / Valid-NC filters plus a QR size preset
(150–1800 bytes/frame) and ships the **BBQR** format; the import
flow reviews and sign each transaction
one at a time, then proposes exporting the signed transactions. When
Electrum's `audio_modem` plugin is enabled (optional, requires `amodem` +
PortAudio) Send/Receive audio buttons complement the QR channel. See
[`PLAN_QR_TRANSFER.md`](PLAN_QR_TRANSFER.md) for the transfer wire-format spec.

### Animated-QR format (BBQR)

The export page emits **BBQR** (`B$…`, Coinkite, used by BitKit and Coldcard)
animated-QR sequences. The importer auto-detects each code it sees, so a BAL
export can be imported by any tool that understands BBQR. BBQR frames may
arrive in any order, and duplicates are ignored. The 32 MB message limit
(zlib-bomb guard) bounds untrusted scanner input.

## Command-line / headless usage

BAL can be used without the Qt GUI via Electrum's daemon mode. The CLI layer
exposes `bal_*` commands that replicate the full inheritance cycle.

### Prerequisites

- An **Electrum daemon** running (`electrum daemon -d`)
- A wallet loaded (`electrum load_wallet`)

### Available commands

| Category | Commands |
|----------|----------|
| Settings | `bal_settings_list`, `bal_settings_get`, `bal_settings_set`, `bal_settings_reset` |
| Delivery (per wallet) | `bal_delivery_show`, `bal_delivery_set` |
| Heirs | `bal_heirs_list`, `bal_heirs_show`, `bal_heirs_add`, `bal_heirs_update`, `bal_heirs_delete`, `bal_heirs_import`, `bal_heirs_export` |
| Will-Executors | `bal_willexecutors_list`, `bal_willexecutors_show`, `bal_willexecutors_add`, `bal_willexecutors_update`, `bal_willexecutors_select`, `bal_willexecutors_delete`, `bal_willexecutors_ping`, `bal_willexecutors_download`, `bal_willexecutors_import`, `bal_willexecutors_export` |
| Will | `bal_will_status`, `bal_will_check`, `bal_will_prepare`, `bal_will_autorebuild`, `bal_will_sign`, `bal_will_broadcast`, `bal_will_export`, `bal_will_import_merge`, `bal_will_invalidate`, `bal_will_check_executor` |

### Example workflow

```bash
electrum daemon -d
electrum load_wallet
electrum bal_heirs_list
electrum bal_will_prepare
electrum bal_will_sign --password '...'
electrum bal_will_broadcast
electrum stop
```

All commands require a running daemon (Electrum's `plugin_command` enforces
this). Wallet-bound commands (`bal_heirs_*`, `bal_will_*`, etc.) require the
wallet to be loaded first. Signing commands require `--password` for encrypted
wallets. The settings commands (`bal_settings_*`) need no wallet. The
delivery commands (`bal_delivery_*`) work on the loaded wallet: each wallet
keeps its own delivery date and fee rate.

### Syntax

`electrum <command> --help` prints the exact arguments of every command.
The most common forms:

```bash
# Heirs: name, address, amount (satoshis or a percentage)
electrum bal_heirs_add alice tb1q... 50%
electrum bal_heirs_update alice --amount 40%
electrum bal_heirs_delete alice                 # or a JSON array: '["alice","bob"]'

# Will-executors: the URL comes first, the other fields are options
electrum bal_willexecutors_add https://we.example.com --address tb1q... --base_fee 1000
electrum bal_willexecutors_update https://we.example.com --info "My executor"
electrum bal_willexecutors_select https://we.example.com --value false   # deselect
electrum bal_willexecutors_ping --urls '["https://we.example.com"]'

# Delivery date and fee rate of the loaded wallet
electrum bal_delivery_show
electrum bal_delivery_set --date 2029-08-29     # YYYY-MM-DD means 00:00 UTC
electrum bal_delivery_set --fee 20              # sat/vByte, 1 or more

# Plugin settings: key, then value (JSON for object-typed keys)
electrum bal_settings_get bal_user_type
electrum bal_settings_set bal_hide_replaced false
```

The delivery date is a fixed date (`YYYY-MM-DD` or a UNIX timestamp): a
period such as `1y` or `730d` is refused. A date earlier than the will in
force needs `--confirm_earlier true`. A later date is stored; if the will
was already sent, the next `bal_will_prepare` answers `postponed`: the old
will must be invalidated first (on-chain, miner fee). Every heir gets the
delivery date of the will, so `--locktime` for a single heir is refused
unless `bal_enable_multiverse` is on.

Where the delivery settings are kept: in the wallet file, under the wallet
key `will_settings`. Electrum's global `bal_will_settings` (the key of the
original BAL plugin) is never changed. It is read only once per wallet, the
first time the wallet is opened with this version. `bal_settings_list` no
longer shows it, and `bal_settings_get`, `set` and `reset` on it are
refused with a pointer to `bal_delivery_show` / `bal_delivery_set`.

That first time, the delivery date becomes the date of the wallet's will in
force. Without one, it is the earliest will already sent to the
will-executors and still waiting for its date; else, only when the wallet
already had heirs or a will, the old global setting (when it gives a future
date); else today + 12 months. The fee rate comes from the same will, else
from the old setting, else 20. A period saved by an older version (such as
`1054d`) becomes that fixed date once. `bal_delivery_show` shows where the
date came from (`migrated_from`) and the old value (`legacy_locktime`).

In rare cases the plugin must ask which date to use: a will is prepared
but not yet signed and sent, while the old setting asked for a later date.
Until it is answered, `bal_will_check`, `bal_will_prepare` and
`bal_will_autorebuild` answer `delivery_choice_pending`, and
`bal_will_sign`, `bal_will_broadcast` and `bal_will_invalidate` are
refused. Keep the date of the prepared will with
`bal_delivery_set --keep true` (no fee), or set a later date with
`bal_delivery_set --date YYYY-MM-DD`. If the delivery settings of a wallet
cannot be prepared, the same commands answer
`delivery_settings_unavailable` and nothing is built, signed or sent.

### Results and exit codes

Results are JSON. A command that cannot do its job ends with a message and
exit code 1, for example `bal_will_broadcast` when no will-executor received
the will (the message names each will-executor and the reason, such as
"You are offline."). When only some failed, the JSON result lists them in
`_failed`. `bal_will_autorebuild` answers `rebuilt_not_sent` when the will
was rebuilt and signed but not delivered. `bal_heirs_import` returns the
imported heirs and, in `skipped`, each refused entry with the reason.

## Inheritance safety: anticipate / postpone

A will transaction is signed with a **fixed, immutable locktime** and then
optionally sent to will-executor servers, which are economically incentivised
to broadcast it (they collect fees). Because the locktime is baked into the
signed transaction, simply changing the delivery time later is **not enough**:
the old, already-signed transaction keeps living on the will-executors.

The plugin handles the cases as follows (triggered when you press
**Prepare** on the **WILL** tab):

* **Anticipate** (new delivery time *earlier* than the signed locktime, still
  in the future): a plain **rebuild** — the transactions are re-created with
  the new, earlier locktime. **No on-chain invalidation and no Bitcoin fee**,
  even if the will was already signed/sent: moving the date earlier only makes
  the inheritance available *sooner*, so there is no early-execution risk.
* **Expire** (new delivery time now in the **past**): the will is genuinely
  expired and you are asked to **invalidate** the old transaction on-chain,
  then rebuild.
* **Postpone** (new delivery time *later* than the signed locktime) on a will
  that was already **signed and/or pushed**: the previously committed coins
  must be invalidated on-chain **first**, otherwise a will-executor could
  broadcast the old (earlier-locktime) transaction and execute the inheritance
  *too early*. The plugin detects this by comparing the requested locktime with
  the locktime **frozen inside the signed transaction** (`tx.locktime`), and
  asks you to sign and broadcast an invalidation transaction. After it is
  broadcast, press **Prepare** again to rebuild, re-sign and re-send the new
  (postponed) inheritance. Postponing a will that was *never* signed/sent just
  rebuilds it (no on-chain fee).

## Transaction list: the "Server" column

The will transaction list shows a dedicated **Server** column so you always
know whether each inheritance transaction is actually stored on the
will-executor servers, independently of the row colour:

| Label | Meaning |
| --- | --- |
| `Confirmed on server` | the will-executor confirmed it stored the transaction |
| `Sent (not checked)` | pushed to the will-executor, not yet re-checked |
| `Send failed` / `Not on server` | push failed or the server no longer has it |
| `Signed (not sent)` | signed locally, not sent to any will-executor |
| `Not sent` | not signed/sent yet |

Hovering the cell shows a tooltip with the will-executor URL and the current
state.

## Testing

Run the tests with the **runtime environment** active (see `HANDOFF.md` §3 for
the two venvs and how to activate them):

```bash
# imports + behavior
QT_QPA_PLATFORM=offscreen python3 tests/smoke_test.py electrum.plugins.bal

# external-zip loading regression (run after build_zip.py)
QT_QPA_PLATFORM=offscreen python3 tests/external_zip_test.py bal-electrum-plugin.zip
```

## ⚠️ Safety

This plugin builds real Bitcoin inheritance transactions with time-locks. Test
on **testnet** or a fund-less wallet first, and review the generated
transactions before broadcasting.

## License

MIT — see [`bal/LICENSE`](bal/LICENSE).
