"""The Check says "Ok" for the signing only when the will is signed (#159).

The analysis of 2026-10-16 (owner: offline signing, multisig) found that
"Check your will" wrote "Signing your will: Ok" and "All done: Ok" when
nothing was signed:

* H1: a watch-only wallet (no private keys: they are on the offline
  computer) prepared the will, signed nothing, and said "Ok" twice;
* H2: a multisig 2-of-3 signed by one cosigner ("1 of 2") said "Ok" too.

Now the signing row says why the will is not signed yet, in orange, with a
line below (owner): the way to sign depends on the wallet type, which
Electrum shows in its window title; and the last row says "Will still to
sign", or "Will partly signed" when a signature was made here (owner). A
normal wallet, which signs everything, is unchanged.

#196 (owner): that last row is labelled "Not finished", not "All done"
("All done: Will still to sign" read as a contradiction); "All done: Ok"
stays for a signed will.

Test wallets: throw-away testnet keys made at every run (the wallets of
``tests/test_engine_merge.py``), three fake will-executors
(``tests/flowkit.py``). Nothing reaches the network.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_check_signing_honest.py
"""

import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

from test_engine_merge import _valid, world  # noqa: F401 - the fixture

from bal.engine.sign import signing_outcome

# #161: both name the guided route (Tools > "Sign the will offline").
WATCH_ONLY = ("Not signed: this wallet has no keys (watch-only). "
              "Sign it on the offline computer (Tools > Sign the will offline).")
WATCH_ONLY_MULTISIG = ("Not signed: this wallet has no keys (watch-only). "
                       "Sign it on the cosigners' computers (Tools > Sign the "
                       "will offline).")
STILL_TO_SIGN = "Will still to sign"
PARTLY = "Will partly signed: complete it with the missing signatures."
HINT = ("The way to sign depends on the wallet type, which Electrum shows "
        "in the window title.")


def _rows(s):
    """The last rows of the Check's log, as plain text (no colours)."""
    return [re.sub("<[^>]+>", "", str(line)) for line in s.last.history]


def _last(rows, label):
    """The last value written on the row *label* ("label:\\tvalue")."""
    values = [r.split("\t", 1)[1] for r in rows if r.startswith(label + ":\t")]
    return values[-1] if values else None


def _warnings(s):
    """The lines of the report's "warnings" box (the top of the window)."""
    return s.last._report().snapshot()["boxes"]["warnings"]


def _sign_step(s):
    """The state of the "sign" step in the report's bar."""
    steps = s.last._report().snapshot()["steps"]
    return [st for st in steps if st[0] == "sign"][0][1]


@pytest.mark.parametrize("kind, text, done", [
    ("watch", WATCH_ONLY, STILL_TO_SIGN),
    ("msWatch", WATCH_ONLY_MULTISIG, STILL_TO_SIGN),
    ("msA", "1 of 2 signatures: another signer must sign.", PARTLY),
])
def test_not_signed_is_not_ok(world, kind, text, done):  # noqa: F811
    """H1 / H2: watch-only (single and multisig) and a multisig at "1 of 2":
    the signing row says why, the hint follows, the last row is "Not
    finished" (#196), never "All done" (owner: "partly signed" when a
    signature was made here), nothing is sent; the second Check says the
    same."""
    s = world(kind)
    for _ in range(2):
        s.check()
        rows = _rows(s)
        assert _last(rows, "Signing your will") == text
        assert HINT in rows
        assert _last(rows, "Not finished") == done
        assert _last(rows, "All done") is None
        assert _sign_step(s) == "warn"
        assert text in _warnings(s) and HINT in _warnings(s)
        assert not any(w.startswith("All done") for w in _warnings(s))
        assert not any(w.get_status("COMPLETE") for w in _valid(s).values())
        assert s.world.pushed == []


def test_normal_wallet_unchanged(world):  # noqa: F811
    """A wallet with its keys signs everything: "Ok" as before, no hint."""
    s = world("keys")
    s.check()
    rows = _rows(s)
    assert _last(rows, "Signing your will") == "Ok"
    assert _last(rows, "All done") == "Ok"
    assert HINT not in rows
    assert _sign_step(s) == "ok"
    s.check()
    rows = _rows(s)
    assert _last(rows, "Signing your will") == "Nothing to do"
    assert _last(rows, "All done") == "Ok"


def test_signing_outcome_counts(world):  # noqa: F811
    """The engine's count behind the rows: unsigned, "1 of 2", signed."""
    watch = world("watch")
    watch.check()
    out = signing_outcome(watch.bw.willitems)
    assert (out.signed, out.partial, out.unsigned) == (0, [], 3)
    ms = world("msA")
    ms.check()
    out = signing_outcome(ms.bw.willitems)
    assert (out.signed, out.partial, out.unsigned) == (0, [(1, 2)] * 3, 0)
    keys = world("keys")
    keys.check()
    out = signing_outcome(keys.bw.willitems)
    assert (out.signed, out.partial, out.unsigned) == (3, [], 0)
