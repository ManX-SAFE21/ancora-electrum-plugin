# Compatibility Roadmap: Multisig and TrustedCoin (2FA) Wallets

Status document, 2026-07-19. Companion to [`COMPATIBILITY.md`](COMPATIBILITY.md):
that file states *what* is supported today; this one explains *why* multisig and
TrustedCoin (2FA) wallets are currently unsupported and *how* support can be
added.

## Root cause (common to both)

BAL currently does two things that are only valid for standard
(single-signature) wallets:

1. **It builds transactions itself** with `PartialTransaction.from_io(...)`
   (`bal/core/will.py`), bypassing `wallet.make_unsigned_transaction`.
2. **It signs with a single call** — `wallet.sign_transaction(tx, password)`
   (`bal/gui/qt/window.py`, `sign_transactions`) — and considers the will ready
   when `tx.is_complete()` is true.

On a standard wallet one signature completes the transaction. On multisig and
2FA wallets **one signature is not enough**: the transaction stays incomplete,
is never marked `COMPLETE`, and can never be pushed to will-executors.

A secondary single-sig assumption: `plugin.py` uses `wallet.get_keystore()`
(singular); multisig wallets expose `get_keystores()` (plural).

## Multisig wallets — solvable, medium/large effort

A 2-of-3 multisig wallet typically holds **only one** of the required private
keys locally; the other cosigners hold theirs. `wallet.sign_transaction` adds
the local signature only, and BAL has no flow to collect the missing ones.

**Proposed solution: the standard PSBT coordination round** (the same flow
Electrum itself uses for multisig spending):

1. Build and sign locally as today.
2. If the transaction is not complete, **export the partially-signed
   transaction(s)** (file and/or QR) and mark the will with a new status such
   as `WAITING_COSIGNERS`.
3. Each cosigner signs in their own Electrum (native feature — no new
   software needed on their side).
4. BAL **re-imports and merges the signatures**; once complete, the will is
   pushed to will-executors as today.

Notes and caveats:

- **Chained will transactions** (a will tx spending the change of a previous
  will tx) remain workable: with segwit, the txid of an unsigned/partially
  signed transaction is already stable, so the whole chain can be exported as
  a batch of PSBTs in one round.
- **Every rebuild requires a new cosigner round.** Check Alive postponements
  and balance-change rebuilds re-sign the will, so each of them needs the
  cosigners again. This is inherent to multisig and must be clearly
  communicated in the UI.
- Implementation surface: export/import/merge pipeline, GUI for it, the new
  status in the transaction list, and tests.

Target: **next plugin release**, as announced.

## TrustedCoin (2FA) wallets — harder, with one blocking unknown

An Electrum 2FA wallet (`Wallet_2fa`, defined in Electrum's `trustedcoin`
plugin) is technically a **2-of-3 multisig whose second signer is the
TrustedCoin server**:

- signing requires a **one-time password (OTP) per transaction**
  (`server.sign(short_id, raw_tx, otp)`);
- the server co-signs only transactions that include **its billing fee**,
  which Electrum adds inside `Wallet_2fa.make_unsigned_transaction` — a code
  path BAL currently bypasses (see root cause #1).

So today: no billing output, no OTP prompt, local signature only → incomplete
transaction.

Even with full integration (building via the wallet's
`make_unsigned_transaction`, adding the OTP prompt flow), one **decisive
unknown** remains: will transactions carry a **locktime years in the future**.
Whether the TrustedCoin server agrees to co-sign a transaction with such a
far-future `nLockTime` is an undocumented server-side policy. If it refuses,
2FA support is **not achievable** without TrustedCoin's cooperation. This is
why `COMPATIBILITY.md` marks 2FA as *unknown*.

**Proposed plan:**

1. **Empirical test on testnet** (cheap, decisive): create a test 2FA wallet,
   build a far-future-locktime transaction through the proper 2FA path, and
   check whether the server signs it.
2. If it signs → implement support: build via `make_unsigned_transaction`
   (billing output included), integrate the OTP prompt, and document that
   every rebuild costs one OTP round and TrustedCoin fees.
3. If it refuses → document 2FA as unsupported, with the practical
   workaround: Electrum allows disabling 2FA by restoring the wallet from the
   full seed, which turns it into a standard wallet — fully supported by BAL.

## Recommended order of work

1. **Multisig first**: deterministic path, standard Electrum tooling, already
   announced for the next release.
2. **TrustedCoin empirical test in parallel**: low cost, and its outcome
   decides whether 2FA support is feasible at all.
