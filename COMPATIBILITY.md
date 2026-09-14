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

BAL exports/imports wills as QR codes. **BAL QR** (the default) is the plugin's
own frame format and is only understood by BAL itself. The export page also
supports **BC-UR v1**, **BC-UR v2** and **BBQR**:

| Format    | Wire appearance            | Interop target                                       |
|-----------|----------------------------|------------------------------------------------------|
| BAL QR    | `BAL1<total><index><flag>…` (v2) / `BALQR1\|total\|index\|…` (legacy import-only) | Past/other BAL versions: **v2 exports are NOT readable by old builds**; old `BALQR1` exports still import here (default, best-of compression, flag `0` = plain, `Z` = deflate) |
| BC-UR v1  | `ur:bytes/<bc32>`          | Blockchain Commons / Coldcard-style UR (BC32, SHA-256 digest, part counts per part) |
| BC-UR v2  | `ur:bytes/<seq>-<seqlen>/<bytewords>` | BC-UR 2.x fountain codes (CBOR parts, CRC-32, bytewords-minimal) |
| BBQR      | `B$<enc><type><N><n>…`     | Coinkite BitKit / Coldcard's BBQR animated-QR mode   |

Import auto-detects the format of each scanned code; out-of-order, duplicate
and (for UR v2) partially-lost fountain frames are handled. Interop is
validation-tested against the reference C++ bc-ur encoder output and the
BCR-2020-004/005 BC32 test vectors; it has not yet been cross-verified against
third-party libraries (`ur`, `bbqr`, Coldcard firmwares).

## Reporting compatibility issues

If you find a compatibility problem not listed here, please open an issue on
this repository describing the wallet type, Electrum version, and the exact
error or unexpected behavior observed.
