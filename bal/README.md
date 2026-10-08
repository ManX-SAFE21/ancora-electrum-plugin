# Safe21 Heartbeat
Safe21 Heartbeat Electrum Plugin

*As long as you're here, your bitcoin stays yours.* Pre-signed, time-locked
backup transactions, optionally kept by independent will-executor servers:
while you keep refreshing them nothing happens; if you stop, from the date you
chose they send your bitcoin to the people you chose.

Designed by Safe21 (https://safe21.io). Based on Bitcoin After Life (BAL) by
Svatantrya — MIT licence (see `LICENSE`).

## Key behaviours

- **Anticipate / postpone safety**: changing the delivery time of an
  already-signed will is handled safely. Postponing a signed/sent will first
  asks you to invalidate the old transaction on-chain (so a will-executor can
  never broadcast the earlier-locktime transaction and execute the inheritance
  too early), then lets you rebuild and re-send the new one via the
  **Prepare** button on the **WILL** tab.
- **"Server" column**: the will transaction list shows whether each transaction
  is actually stored on the will-executor servers
  (`Confirmed on server`, `Sent (not checked)`, `Send failed`,
  `Not on server`, `Signed (not sent)`, `Not sent`), with a tooltip showing the
  will-executor URL.

See the top-level [`README.md`](../README.md) for installation and testing.
