# Wallet Compatibility

Àncòra (based on Bitcoin After Life, BAL) builds and signs Electrum
transactions using Electrum's own wallet and signing infrastructure. Its
compatibility therefore depends on the wallet type in use.

| Wallet type                                    | Status                     | Notes |
|-------------------------------------------------|-----------------------------|-------|
| Standard wallet (single-signature, seed-based)  | ✅ Supported                | Primary, fully tested target |
| Multisig wallets                                | ✅ Supported                | Tested on testnet4 with a 2-of-3 wallet: the will was signed with two of the keys and a will-executor delivered it on its own at its date (2026-11-03). That test used the command line; signing on a second computer through the windows has not been tested yet. |
| Hardware wallets (Ledger, Trezor, Coldcard, BitBox02, Jade, KeepKey, etc.) | ❓ Not tested yet | Electrum signs with them as usual, but Àncòra has not been tested with them yet. |
| Wallets with addresses starting with `1` or `3` | ❌ Not supported | The plugin says so and builds no will. Use a wallet with `bc1q` addresses. |
| Electrum TrustedCoin (2FA) wallets              | ❓ Unknown / unsupported    | Not tested. It has not yet been determined whether or when this will be addressed. |

## What "not tested" and "not supported" mean in practice

For hardware wallets and TrustedCoin (2FA) wallets, Àncòra's behavior has
not been verified and should be considered **unreliable**. Do not rely on
Àncòra to protect an inheritance set up on one of these wallet types until
this document is updated to mark them as supported.

## Electrum version compatibility

Àncòra runs on **Electrum 4.8.3 or later**. The plugin's manifest says so, so
an older Electrum does not load it. Tested up to Electrum 4.8.4 (whole test
suite, 3353 tests, on 4.8.4 on 2026-11-09). A newer Electrum is not blocked, but
it is not covered until it has been tested.
See [`README.md`](README.md).

## QR wire-format compatibility

Àncòra exports/imports wills as QR codes using **BBQR**:

| Format    | Wire appearance            | Interop target                                       |
|-----------|----------------------------|------------------------------------------------------|
| BBQR      | `B$<enc><type><N><n>…`     | Coinkite BitKit / Coldcard's BBQR animated-QR mode   |

Import auto-detects the format of each scanned code; out-of-order and duplicate
BBQR frames are handled. Interop has not yet been cross-verified against
third-party libraries (`bbqr`, Coldcard firmwares).

## Reporting compatibility issues

If you find a compatibility problem not listed here, please open an issue on
this repository describing the wallet type, Electrum version, and the exact
error or unexpected behavior observed.
