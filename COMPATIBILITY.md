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

## Reporting compatibility issues

If you find a compatibility problem not listed here, please open an issue on
this repository describing the wallet type, Electrum version, and the exact
error or unexpected behavior observed.
