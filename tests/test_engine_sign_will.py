"""One shared "sign the will" (#157), on a real test wallet.

Before, signing the will and putting the signed copies back into it was
written five times (the Will tab's "Sign", the Check, the automatic
rebuild, the command line, the review of an imported will). Now it is
``bal.engine.sign.sign_will``. Faults fixed with it, each a permanent test:

* A: a signing that failed half way left the copies signed before it
  marked COMPLETE with their signatures thrown away; the Check then sent
  unsigned copies to the will-executors, and nothing was said;
* F: the waiting window's "signed: ... / signing: ..." never showed;
* G: the review of an imported will (the offline signing) marked a copy
  COMPLETE but dropped the signature: the "signed file" carried back to the
  online computer held unsigned transactions.

Owner decision (#157): signing "all" (the Will tab's "Sign", the command
line without a txid) skips the copies of will-executors deselected after
the will was built that never received it (the #135 rule of the Check);
rows chosen by the owner (right click, ``bal_will_sign <txid>``) are signed
as chosen.

``tests/flowkit.py``: a real Electrum testnet wallet with a password, three
fake will-executors (each with its own fee address), a fixed clock.
Nothing reaches a real will-executor or the network.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_engine_sign_will.py
"""

import os
import re
import sys
import tempfile
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

import flowkit as fk
from electrum.transaction import tx_from_any
from PyQt6.QtWidgets import QApplication

from bal.engine.sign import is_deselected_leftover, sign_will, to_sign
from bal.gui.qt import dialogs as dialogs_mod
from bal.gui.qt.dialogs import WillTxReviewSignDialog
from bal.gui.qt.window import BalWindow

_APP = QApplication.instance() or QApplication(sys.argv)

TEST_PASSWORD = "flowkit-test"
WE2 = "https://we2.test"
ROOT = os.path.join(os.path.dirname(__file__), os.pardir, "bal")


@pytest.fixture
def s(monkeypatch):
    """A prepared, unsigned will for three will-executors (one copy each)."""
    fk.ensure_event_loop()
    session = fk.Session(monkeypatch, mode="advanced", password=TEST_PASSWORD)
    session.world.add_executor()
    monkeypatch.setattr(session.wallet, "is_up_to_date", lambda: True)
    try:
        session.prepare()
        assert len(_valid(session)) == 3
        assert not any(w.get_status("COMPLETE") for w in _valid(session).values())
        yield session
    finally:
        session.close()
        fk.stop_event_loop()


def _valid(s):
    """The VALID copies of the live will, ``{txid: item}``."""
    return {wid: w for wid, w in s.bw.willitems.items() if w.get_status("VALID")}


def _signed_for_real(w):
    """True when the copy *w* is COMPLETE and its stored tx is signed."""
    return bool(w.get_status("COMPLETE")) and tx_from_any(str(w.tx)).is_complete()


def _fail_at(monkeypatch, wallet, n):
    """The wallet's signing raises at its *n*-th call (1-based).

    Returns the call counter; ``counter["repaired"] = True`` makes the
    wallet sign normally again.
    """
    real = wallet.sign_transaction
    calls = {"n": 0}

    def sign_transaction(tx, password, **kw):
        """The real signing, except the *n*-th call."""
        calls["n"] += 1
        if calls["n"] == n and not calls.get("repaired"):
            raise RuntimeError("signing device unplugged (test)")
        return real(tx, password, **kw)

    monkeypatch.setattr(wallet, "sign_transaction", sign_transaction)
    return calls


def _we(w):
    """The URL of the copy's will-executor."""
    return (w.we or {}).get("url")


# ------------------------------------------------------------------ A


def test_a_menu_sign_failing_half_way_keeps_the_signed_ones(s, monkeypatch):
    """Will tab "Sign": the 2nd signing fails. The 1st copy is COMPLETE and
    signed for real, saved; the others stay to sign; the error is shown.
    Signing again completes the will."""
    calls = _fail_at(monkeypatch, s.wallet, 2)
    s.menu_sign()
    items = _valid(s)
    done = [w for w in items.values() if w.get_status("COMPLETE")]
    assert len(done) == 1
    assert _signed_for_real(done[0])
    assert all(not w.get_status("COMPLETE") for w in items.values() if w not in done)
    errors = [p for p in s.bw.popups if p[0] == "error"]
    assert errors and "signing device unplugged" in errors[0][1]
    # Saved: the window reads the same from the wallet.
    s.reload_window()
    assert sum(_signed_for_real(w) for w in _valid(s).values()) == 1
    # Signing again (the wallet works now) signs the rest.
    calls["repaired"] = True
    s.menu_sign()
    assert all(_signed_for_real(w) for w in _valid(s).values())


def test_a_check_failing_half_way_sends_only_signed_copies(s, monkeypatch):
    """The Check: the 2nd signing fails. Every copy sent to a will-executor
    is really signed (before, the 1st was sent unsigned), and the
    "Signing your will" row says why."""
    _fail_at(monkeypatch, s.wallet, 2)
    dlg = s.check()
    assert s.world.pushed, "the signed copy is sent"
    for _url, _wid, raw in s.world.pushed:
        assert tx_from_any(raw).is_complete(), "an unsigned copy was sent"
    assert any("signing device unplugged" in str(line) for line in dlg.history)
    signed = [w for w in _valid(s).values() if w.get_status("COMPLETE")]
    assert len(signed) == 1 and _signed_for_real(signed[0])


def test_a_engine_keeps_order_and_reports(s, monkeypatch):
    """``sign_will`` directly: stops at the failing one, says which, keeps
    the ones before signed and stored, leaves the rest untouched."""
    _fail_at(monkeypatch, s.wallet, 2)
    items = s.bw.willitems
    order = to_sign(items)
    result = sign_will(s.wallet, items, TEST_PASSWORD)
    assert result.signed == order[:1]
    assert result.failed == order[1]
    assert "signing device unplugged" in str(result.error)
    assert _signed_for_real(items[order[0]])
    assert not items[order[1]].get_status("COMPLETE")
    assert not items[order[2]].get_status("COMPLETE")
    assert set(result.txs) == {order[0]}


# ------------------------------------------------------------------ F


def test_f_waiting_window_shows_the_progress(s):
    """The waiting window says "signing: <first>", then "signed: <first> /
    signing: <second>", ... (before, a local ``_`` hid the translation)."""
    texts = []
    s.bw.waiting_dialog = types.SimpleNamespace(update=texts.append)
    order = to_sign(s.bw.willitems)
    BalWindow.sign_transactions(s.bw, TEST_PASSWORD)
    assert texts[0] == "signing: {}".format(order[0])
    assert texts[1] == "signed: {}\nsigning: {}".format(order[0], order[1])
    assert len(texts) == 3


# ------------------------------------------------------------------ G


def _carry(willitems, folder, name):
    """Save the will as the export does and read it back (the USB stick)."""
    from bal.gui.qt.common import write_json_file

    path = os.path.join(folder, name)
    write_json_file(path, {wid: wi.to_dict() for wid, wi in willitems.items()})
    return BalWindow._load_will_file(None, path)


def test_g_offline_review_signs_into_the_saved_file(s, monkeypatch):
    """The offline signing: the will carried as a file, each copy signed in
    "Review and sign imported will", then "Save signed file…": the file
    holds the SIGNED transactions (before: COMPLETE, but unsigned)."""
    folder = tempfile.mkdtemp(prefix="bal_offline_")
    imported = _carry(_valid(s), folder, "will.json")
    review = WillTxReviewSignDialog.__new__(WillTxReviewSignDialog)
    review.bal_window = s.bw
    review.items = imported
    review.txids = sorted(imported)
    review.password = TEST_PASSWORD
    review.i = 0
    review._advance = lambda: setattr(review, "i", review.i + 1)
    review.show_message = lambda *a, **kw: None
    review.show_error = lambda text: pytest.fail("signing failed: {}".format(text))
    for _txid in review.txids:
        review._sign_current()
    saved = os.path.join(folder, "will_signed.json")
    monkeypatch.setattr(dialogs_mod, "export_meta_gui",
                        lambda parent, name, save: save(saved))
    review._save_signed()
    back = BalWindow._load_will_file(None, saved)
    assert set(back) == set(imported)
    for wid, wi in back.items():
        assert wi.get_status("COMPLETE"), wid
        assert wi.tx.is_complete(), "the signed file holds an unsigned tx"
    # The live will was not touched by the review.
    assert not any(w.get_status("COMPLETE") for w in _valid(s).values())


# ------------------------------------------------- deselected (owner #157)


def _deselect_we2(s):
    """The third will-executor is deselected after the will was built."""
    s.world.executors[WE2]["selected"] = False
    leftover = [wid for wid, w in _valid(s).items() if _we(w) == WE2]
    assert len(leftover) == 1
    return leftover[0]


def test_sign_all_skips_the_deselected_copy(s):
    """Will tab "Sign" (sign all): the deselected will-executor's copy, never
    sent, is not signed; the other two are."""
    leftover = _deselect_we2(s)
    s.menu_sign()
    items = _valid(s)
    assert not items[leftover].get_status("COMPLETE")
    assert all(_signed_for_real(w) for wid, w in items.items() if wid != leftover)


def test_chosen_rows_are_signed_as_chosen(s):
    """The right click on the deselected copy's row: signed (owner: rows
    chosen by hand are signed as chosen)."""
    leftover = _deselect_we2(s)
    s.act("gui right click sign", lambda: s.bw.ask_password_and_sign_transactions(
        txids=[leftover]))
    items = _valid(s)
    assert _signed_for_real(items[leftover])
    assert not any(w.get_status("COMPLETE") for wid, w in items.items()
                   if wid != leftover)


def test_cli_sign_all_skips_and_txid_signs(s):
    """``bal_will_sign`` without a txid skips the deselected copy; with its
    txid it signs it."""
    leftover = _deselect_we2(s)
    s.bw.save_willitems()  # the command line reads the will from the wallet
    s.cli("sign_transactions", TEST_PASSWORD)
    s.reload_window()
    items = _valid(s)
    assert not items[leftover].get_status("COMPLETE")
    assert all(_signed_for_real(w) for wid, w in items.items() if wid != leftover)
    s.cli("sign_transactions", TEST_PASSWORD, [leftover])
    s.reload_window()
    assert _signed_for_real(_valid(s)[leftover])


def test_cli_failure_saves_the_signed_ones_then_raises(s, monkeypatch):
    """The command line: the 2nd signing fails; the 1st is saved, signed,
    and the command ends with the error."""
    s.bw.save_willitems()  # the command line reads the will from the wallet
    _fail_at(monkeypatch, s.wallet, 2)
    out = s.cli("sign_transactions", TEST_PASSWORD)
    assert "raised" in out and "signing device unplugged" in out["raised"]
    s.reload_window()
    assert sum(_signed_for_real(w) for w in _valid(s).values()) == 1


def test_rule_of_the_leftover():
    """``is_deselected_leftover``: only a copy not signed, never sent, of a
    will-executor not selected now."""

    class W:
        """A copy with statuses and a will-executor."""

        def __init__(self, url, **st):
            self.we = {"url": url} if url else None
            self.st = st

        def get_status(self, name):
            """The status *name*."""
            return self.st.get(name, False)

    sel = {"https://a"}
    assert is_deselected_leftover(W("https://b"), sel)
    assert not is_deselected_leftover(W("https://a"), sel)
    assert not is_deselected_leftover(W("https://b", PUSHED=True), sel)
    assert not is_deselected_leftover(W("https://b", COMPLETE=True), sel)
    assert not is_deselected_leftover(W(None), sel)


# ------------------------------------------------------------ one copy


def _code(path):
    """The code of *path* without comments and docstring lines (rough)."""
    with open(path, encoding="utf-8") as f:
        return "\n".join(line.split("#", 1)[0] for line in f)


def test_no_other_copy_of_sign_and_store():
    """Only ``bal/engine/sign.py`` stores a signed copy into a will item
    (``.tx = Will.get_tx_from_any(str(tx))`` right after signing) or loops
    over ``sign_will_item``; the GUI's single call is
    ``BalWindow._prepare_and_sign_tx`` (the review page)."""
    store = re.compile(r"\.tx\s*=\s*Will\.get_tx_from_any\(\s*str\(\s*(tx|signed)\b")
    calls = re.compile(r"\bsign_will_item\(")
    found_store, found_calls = set(), set()
    for folder, _dirs, files in os.walk(ROOT):
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(folder, name)
            rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
            code = _code(path)
            if store.search(code):
                found_store.add(rel)
            if calls.search(code.replace("def sign_will_item(", "")):
                found_calls.add(rel)
    assert found_store == {"engine/sign.py"}, found_store
    assert found_calls == {"engine/sign.py", "gui/qt/window.py"}, found_calls


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
