# BAL — Bitcoin After Life (Electrum plugin)

Free and decentralized **Bitcoin inheritance** support for the
[Electrum](https://electrum.org) wallet. Build time-locked "will" transactions
that transfer your funds to your heirs if you stop refreshing them
(dead-man's switch), optionally relayed by will-executor servers.

This repository contains a **refactored and extended** version of the original
plugin. The logic was reorganized to cleanly separate **business logic** from the
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

BAL currently supports **standard (single-signature) wallets** and
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
2. Enable **Bitcoin After Life** and restart Electrum.
3. (Recommended) verify the downloaded zip's SHA-256 matches the value printed
   by `build_zip.py`.

### Install as an internal plugin

Copy the `bal/` directory into your Electrum installation's
`electrum/plugins/` directory, so that `electrum/plugins/bal/manifest.json`
exists, then enable it from **Tools → Plugins**.

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
wallets.

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
