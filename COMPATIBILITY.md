# Wallet Compatibility

BAL (Bitcoin After Life) builds and signs Electrum transactions using
Electrum's own wallet and signing infrastructure. Its compatibility therefore
depends on the wallet type in use.

| Wallet type                                    | Status                     | Notes |
|-------------------------------------------------|-----------------------------|-------|
| Standard wallet (single-signature, seed-based)  | ✅ Supported                | Primary, fully tested target |
| Hardware wallets (Ledger, Trezor, Coldcard, BitBox02, Jade, KeepKey, etc.) | ✅ Supported | Any hardware wallet supported by Electrum itself |
| Multisig wallets                                | ❌ Not yet supported        | Known limitation identified 2026-07-18. Support is planned for a future plugin release. |
| Electrum TrustedCoin (2FA) wallets              | ❓ Unknown / unsupported    | Known limitation identified 2026-07-18. It has not yet been determined whether or when this will be addressed. |

## What "not supported" means in practice

For multisig and TrustedCoin (2FA) wallets, BAL's behavior has not been
verified and should be considered **unreliable**. Do not rely on BAL to
protect an inheritance set up on one of these wallet types until this document
is updated to mark them as supported.

## Electrum version compatibility

See [`README.md`](README.md) for supported Electrum versions (currently 4.7.2
and 4.8.0).

## QR wire-format compatibility

BAL exports/imports wills as QR codes using **BBQR**:

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
