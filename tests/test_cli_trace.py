"""The command trace and its window "CLI command trace…" (CHANGELOG #173).

Covered:

* the trace (``bal/core/trace.py``, no Qt): numbered lines, the oldest
  dropped after :data:`~bal.core.trace.MAX_LINES`, clear, the plain text
  of Copy / Save (with and without the "Full" details), never a secret
  (a raw transaction, an extended private key, a full txid), never an
  error;
* the requests to the will-executors
  (``Willexecutors.send_request``): ``pushtxs`` with how many
  transactions and the answer with the seconds, ``searchtx`` only at the
  "Full" level with the shortened txid, a busy will-executor (429) and
  its new try, no answer in time; the transactions are never written;
* the engine bricks, through a real Check (flowkit ``Session``: a
  throw-away testnet wallet, three fake will-executors, each with its own
  fee address, nothing reaches the network): ``bal_will_check``,
  ``bal_will_prepare``, ``bal_will_sign``, ``bal_will_broadcast`` and
  ``bal_will_check_executor`` in this order, then a second Check that
  finds the will coherent; neither the password nor a transaction is in
  the trace;
* the window: read-only, black, the subtitle, the colours, new lines
  added by the timer, Pause / Resume, the detail level, Copy, Save to
  file…, Clear, one window per Electrum window; the Tools entry in BASIC
  and ADVANCED (``test_check_button`` has the menu order); Italian from
  the catalog.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_cli_trace.py
"""

import os
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

import flowkit as fk  # noqa: E402
from aiohttp import ClientResponseError  # noqa: E402
from PyQt6.QtWidgets import QApplication, QPlainTextEdit  # noqa: E402

from bal import i18n  # noqa: E402
from bal.core import trace  # noqa: E402
from bal.core import willexecutors as we_mod  # noqa: E402
from bal.core.willexecutors import Willexecutors  # noqa: E402
from bal.gui.qt import trace_window as tw  # noqa: E402

# Kept at module level: an unreferenced application object is destroyed at
# once, and creating a widget afterwards aborts Python.
_APP = QApplication.instance() or QApplication(sys.argv)

TXID = "a1b2c3d4" + "0" * 52 + "9f0e"
RAW = "02000000" + "ab" * 120          # a "raw transaction": long hex
XPRV = "tprv8ZgxMBicQKsPd9TeAdPADNnSyH9SSUUbTVeFszDE23Ki6TBB5nCefAdHkK8Fm3qMQR6sHwA56zqRmKmxnHk37JkiFzvncDqoKmPWubu7hDF"
TEST_PASSWORD = "flowkit-test"


@pytest.fixture(autouse=True)
def _empty_trace():
    """Every test starts and ends with an empty trace."""
    trace.clear()
    yield
    trace.clear()


def texts(lines=None):
    """The texts of *lines* (default: every line kept)."""
    return [line.text for line in (trace.lines_after(0) if lines is None else lines)]


# ------------------------------------------------------------ the trace

def test_lines_are_numbered_and_read_after_a_number():
    a = trace.command("bal_will_sign", "3 transactions")
    b = trace.add(trace.OK, "3 signed now")
    assert (a.kind, a.text) == (trace.COMMAND, "bal_will_sign  3 transactions")
    assert b.seq == a.seq + 1
    assert trace.lines_after(a.seq) == [b]
    assert trace.lines_after(b.seq) == []


def test_clear_keeps_the_numbers_going():
    first = trace.add(trace.OK, "one")
    trace.clear()
    assert trace.lines_after(0) == []
    assert trace.add(trace.OK, "two").seq == first.seq + 1


def test_the_oldest_lines_go_first():
    for i in range(trace.MAX_LINES + 5):
        trace.add(trace.ANSWER, "line {}".format(i))
    kept = trace.lines_after(0)
    assert len(kept) == trace.MAX_LINES
    assert kept[0].text == "line 5"


def test_never_a_secret_on_a_line():
    """A raw transaction, an extended private key and a full txid that
    slip into a text are hidden or shortened; a text stays on one line."""
    line = trace.add(trace.ERROR, "bad {} key {}\nid {}".format(RAW, XPRV, TXID))
    assert RAW not in line.text and "ab" * 50 not in line.text
    assert "tprv" not in line.text
    assert TXID not in line.text and "a1b2c3d4…9f0e" in line.text
    assert "\n" not in line.text


def test_short_forms():
    assert trace.short_txid(TXID) == "a1b2c3d4…9f0e"
    assert trace.short_txid("abc") == "abc"
    assert trace.short_txid(None) == ""
    assert trace.short_url("https://we1.test/") == "we1.test"
    assert trace.short_url("http://x.onion/bitcoin/pushtxs") == "x.onion/bitcoin/pushtxs"


def test_long_text_is_cut_and_an_unknown_kind_is_an_answer():
    line = trace.add("strange", "x" * 1000)
    assert line.kind == trace.ANSWER
    assert len(line.text) == trace.MAX_TEXT and line.text.endswith("…")


def test_add_never_raises():
    class Bad:
        def __str__(self):
            raise RuntimeError("no text")
    assert trace.add(trace.OK, Bad()) is None
    assert trace.reason(ValueError()) == "ValueError"
    assert trace.reason(ValueError("why")) == "why"


def test_plain_text_marks_and_full_details():
    trace.command("bal_will_broadcast", "3 will-executors")
    trace.add(trace.REQUEST, "POST we1.test/bitcoin/pushtxs  1 transactions")
    trace.add(trace.ANSWER, "thx  0.8 s")
    trace.add(trace.REQUEST, "POST we1.test/searchtx  a1b2c3d4…9f0e", full=True)
    trace.add(trace.OK, "received 3")
    rows = trace.plain_text(trace.lines_after(0)).split("\n")
    assert [r[10:] for r in rows] == [
        "> bal_will_broadcast  3 will-executors",
        "  -> POST we1.test/bitcoin/pushtxs  1 transactions",
        "  <- thx  0.8 s",
        "  -> POST we1.test/searchtx  a1b2c3d4…9f0e",
        "  received 3",
    ]
    assert rows[0][2] == ":" and rows[0][5] == ":"
    normal = trace.plain_text(trace.lines_after(0), full=False)
    assert "searchtx" not in normal and "received 3" in normal


# ------------------------------------------- requests to will-executors

def _network(monkeypatch, answer):
    """Electrum's network: online, every request answered by *answer*
    (a value, or an exception raised)."""
    calls = []

    def send_http_on_proxy(method, url, **kw):
        calls.append((method, url, kw))
        if isinstance(answer, BaseException):
            raise answer
        return answer

    monkeypatch.setattr(we_mod.Network, "get_instance", staticmethod(lambda: object()))
    monkeypatch.setattr(we_mod.Network, "send_http_on_proxy",
                        staticmethod(send_http_on_proxy))
    return calls


def test_pushtxs_says_how_many_transactions_never_which(monkeypatch):
    _network(monkeypatch, "thx")
    record = {"url": "https://we1.test", "txs": "{}\n{}\n{}\n".format(RAW, RAW, RAW)}
    assert Willexecutors.push_transactions_to_willexecutor(record) is True
    lines = trace.lines_after(0)
    assert [(line.kind, line.full) for line in lines] == [
        (trace.REQUEST, False), (trace.ANSWER, False)]
    assert lines[0].text == "POST we1.test/{}/pushtxs  3 transactions".format(
        we_mod.BalPlugin.chainname)
    assert lines[1].text.startswith("thx  ") and lines[1].text.endswith(" s")
    assert all("abab" not in t for t in texts())


def test_already_present_is_shown_as_the_server_says_it(monkeypatch):
    _network(monkeypatch, "already present")
    record = {"url": "https://we1.test", "txs": RAW + "\n"}
    with pytest.raises(Willexecutors.AlreadyPresentException):
        Willexecutors.push_transactions_to_willexecutor(record)
    assert texts()[-1].startswith("already present  ")


def test_searchtx_is_a_full_detail_with_the_short_txid(monkeypatch):
    _network(monkeypatch, {"tx": RAW})
    answer = Willexecutors.check_transaction(TXID, "https://we2.test")
    assert answer == {"tx": RAW}
    lines = trace.lines_after(0)
    assert [line.full for line in lines] == [True, True]
    assert lines[0].text == "POST we2.test/searchtx  a1b2c3d4…9f0e"
    assert lines[1].text.startswith("answer with a transaction  ")
    assert all(RAW not in t and "abab" not in t for t in texts())


def test_a_busy_will_executor_and_its_new_try(monkeypatch):
    busy = ClientResponseError(None, (), status=429, headers={})
    _network(monkeypatch, busy)
    monkeypatch.setattr(we_mod.time, "sleep", lambda s: None)
    answer = Willexecutors.check_transaction(TXID, "https://we3.test",
                                             busy_retries=1)
    assert answer is we_mod.NO_ANSWER
    kinds = [(line.kind, line.text.split("  ")[0]) for line in trace.lines_after(0)]
    assert kinds == [
        (trace.REQUEST, "POST we3.test/searchtx"),
        (trace.ERROR, "busy (429)"),
        (trace.WARNING, "we3.test busy (429), new try in 2 s"),
        (trace.REQUEST, "POST we3.test/searchtx"),
        (trace.ERROR, "busy (429)"),
        (trace.WARNING, "we3.test still busy (429): no answer this time"),
    ]


def test_no_answer_in_time_then_new_tries(monkeypatch):
    _network(monkeypatch, TimeoutError())
    monkeypatch.setattr(we_mod.time, "sleep", lambda s: None)
    out = Willexecutors.send_request("get", "https://we1.test/bitcoin/info",
                                     max_retries=1, retry_sleep=1)
    assert out is None
    lines = trace.lines_after(0)
    assert [(line.kind, line.full) for line in lines] == [
        (trace.REQUEST, False), (trace.WARNING, True),
        (trace.REQUEST, False), (trace.ERROR, False)]
    assert lines[1].text.endswith("new try in 1 s")
    assert lines[3].text.startswith("no answer in ")


def test_an_http_error_shows_its_code(monkeypatch):
    _network(monkeypatch, ClientResponseError(None, (), status=500, headers={}))
    with pytest.raises(ClientResponseError):
        Willexecutors.send_request("get", "https://we1.test/bitcoin/info",
                                   max_retries=0)
    assert texts()[-1].startswith("error 500  ")


def test_ping_says_how_many_answered(monkeypatch):
    def info(url, we, **kw):
        we = dict(we)
        we["status"] = 200 if url != "https://we3.test" else "KO"
        return we
    monkeypatch.setattr(Willexecutors, "get_info_task", staticmethod(info))
    executors = {"https://we{}.test".format(i): {"url": "https://we{}.test".format(i)}
                 for i in (1, 2, 3)}
    Willexecutors.ping_servers_parallel(executors)
    assert texts() == ["bal_willexecutors_ping  3 will-executors",
                       "2 of 3 will-executors answered"]
    assert trace.lines_after(0)[-1].kind == trace.WARNING


# ------------------------------------------------- the engine, real Check

@pytest.fixture
def session(monkeypatch):
    """A will made by a real Check: throw-away testnet wallet with a
    password, three fake will-executors."""
    fk.ensure_event_loop()
    s = fk.Session(monkeypatch, mode="basic", locktime=fk.ts(2030, 12, 31, 1),
                   password=TEST_PASSWORD)
    s.world.add_executor()
    monkeypatch.setattr(s.wallet, "is_up_to_date", lambda: True)
    yield s
    s.close()
    fk.stop_event_loop()


def _commands(lines):
    """The command names of *lines*, in order."""
    return [line.text.split("  ")[0] for line in lines if line.kind == trace.COMMAND]


def test_a_check_writes_its_steps_as_commands(session):
    """Check, prepare, sign, send, check the copies: in this order, each
    with its outcome; then a second Check finds the will coherent."""
    session.check()
    lines = trace.lines_after(0)
    names = _commands(lines)
    order = ["bal_will_check", "bal_will_prepare", "bal_will_sign",
             "bal_will_broadcast", "bal_will_check_executor"]
    positions = [names.index(n) for n in order]
    assert positions == sorted(positions), names
    by_text = texts(lines)
    assert any(t.startswith("not coherent: ") and "WillException" not in t
               for t in by_text)
    assert any(t.startswith("3 transactions in the will, delivery 20") for t in by_text)
    assert "bal_will_broadcast  3 will-executors" in by_text
    assert "received 3, already present 0, refused 0, no answer 0" in by_text
    assert "verified 3, failed 0, no answer 0" in by_text
    assert "3 signed now" in by_text
    # Never the password, never a transaction sent.
    joined = "\n".join(by_text)
    assert TEST_PASSWORD not in joined
    for _url, _wid, raw in session.world.pushed:
        assert raw and raw not in joined and raw[:100] not in joined

    start = lines[-1].seq
    session.check()
    again = trace.lines_after(start)
    assert _commands(again)[0] == "bal_will_check"
    assert again[1].kind == trace.OK and again[1].text == "the will is coherent"


def test_the_command_line_writes_the_same_lines(session):
    """``bal_will_check`` from the command line: the same brick, the same
    line (the GUI and the command line call the same engine)."""
    session.check()
    start = trace.lines_after(0)[-1].seq
    session.cli("will_check")
    assert texts(trace.lines_after(start))[:2] == [
        "bal_will_check", "the will is coherent"]


# ------------------------------------------------------------ the window

def _dialog(config=None):
    """The window, not polling by itself (the tests call read_new)."""
    return tw.TraceDialog(config=config, poll_ms=60_000)


def test_window_is_black_read_only_with_its_subtitle():
    d = _dialog()
    assert d.view.isReadOnly()
    assert isinstance(d.view, QPlainTextEdit)
    assert "#000000" in d.view.styleSheet()
    assert d.subtitle.text() == "(CLI commands, view only)"
    assert d.windowTitle().startswith("CLI command trace — ")
    assert d.view.toPlainText() == ""
    assert d.view.placeholderText()
    d.reject()


def test_window_shows_what_was_there_and_what_comes():
    trace.command("bal_will_check")
    d = _dialog()
    assert d.view.toPlainText().endswith("> bal_will_check")
    trace.add(trace.OK, "the will is coherent")
    d.read_new()
    assert d.view.toPlainText().split("\n")[-1].endswith("  the will is coherent")
    assert d.view.document().blockCount() == 2
    d.reject()


def test_line_html_colours_and_escapes():
    line = trace.add(trace.WARNING, "to prepare again: <b>x</b>")
    html_text = tw.line_html(line)
    assert tw.COLOURS[trace.WARNING] in html_text and tw.CLOCK_COLOUR in html_text
    assert "&lt;b&gt;x&lt;/b&gt;" in html_text and "<b>" not in html_text
    assert set(tw.COLOURS) == set(trace.KINDS)


def test_pause_keeps_the_lines_for_resume():
    d = _dialog()
    d.toggle_pause()
    assert d.pause_button.text() == "Resume"
    trace.add(trace.OK, "while paused")
    d.read_new()
    assert "while paused" not in d.view.toPlainText()
    d.toggle_pause()
    assert d.pause_button.text() == "Pause"
    assert "while paused" in d.view.toPlainText()
    d.reject()


def test_detail_level_shows_the_full_lines_only_at_full():
    trace.command("bal_will_check_executor", "3 copies, at most 20 s")
    trace.add(trace.REQUEST, "POST we1.test/searchtx  a1b2c3d4…9f0e", full=True)
    d = _dialog()
    assert "searchtx" not in d.view.toPlainText()
    assert "searchtx" not in d.text()
    d.level_combo.setCurrentIndex(1)
    assert d.full()
    assert "searchtx" in d.view.toPlainText() and "searchtx" in d.text()
    d.reject()


def test_copy_puts_the_plain_text_on_the_clipboard():
    trace.command("bal_will_sign")
    d = _dialog()
    d.copy_text()
    assert QApplication.clipboard().text().endswith("> bal_will_sign")
    d.reject()


def test_save_writes_the_plain_text(tmp_path):
    trace.command("bal_will_sign")
    trace.add(trace.OK, "3 signed now")
    target = tmp_path / "trace.txt"
    config = SimpleNamespace(IO_DIRECTORY=str(tmp_path))
    d = _dialog(config)
    with patch.object(tw, "getSaveFileName", return_value=str(target)) as ask:
        assert d.save_text() == str(target)
    assert ask.call_args.kwargs["filename"].startswith("ancora-cli-trace-")
    assert ask.call_args.kwargs["config"] is config
    content = target.read_text(encoding="utf-8")
    assert content.endswith("> bal_will_sign\n" + content.split("\n")[1] + "\n")
    assert content.split("\n")[1].endswith("  3 signed now")
    with patch.object(tw, "getSaveFileName", return_value=None):
        assert d.save_text() is None
    d.reject()


def test_clear_empties_the_trace_and_the_view():
    trace.command("bal_will_sign")
    d = _dialog()
    d.clear()
    assert d.view.toPlainText() == "" and trace.lines_after(0) == []
    d.reject()


def test_close_stops_the_timer():
    d = tw.TraceDialog(poll_ms=10)
    assert d.timer.isActive()
    d.reject()
    assert not d.timer.isActive()


def test_one_window_per_electrum_window():
    host = SimpleNamespace(window=None)
    with patch("bal.gui.qt.window_utils.show_on_top") as top:
        first = tw.show_trace(host)
        assert tw.show_trace(host) is first
        assert top.call_count == 2
        assert top.call_args.kwargs == {"modal_to_window": False}
    first.reject()
    assert host.trace_dialog is None


@pytest.mark.parametrize("basic", [True, False])
def test_tools_entry_in_basic_and_advanced(basic):
    """#173 (owner): the entry is there in BASIC and ADVANCED alike (the
    deliveries entry, ADVANCED only, follows the mode)."""
    from test_check_button import _row
    host, _settings, _widgets = _row()
    host.bal_window.bal_plugin.is_basic_mode.return_value = basic
    host.menu.aboutToShow.emit()
    entry = host.menu.actions()[-1]
    assert entry.text() == "CLI command trace…" and entry.isVisible()
    assert host.deliveries_action.isVisible() is (not basic)


def test_italian_texts():
    """The texts the owner chose, from the repo's catalog (compiled as the
    ZIP has it)."""
    class _Catalogs:
        """The repo's catalogs, compiled as the ZIP has them."""

        def read_file(self, path):
            """The compiled catalog *path* (``.mo``) from its ``.po``."""
            from build_zip import compile_catalog
            root = os.path.join(os.path.dirname(__file__), os.pardir, "bal")
            return compile_catalog(os.path.join(root, path).replace(".mo", ".po"))

    try:
        i18n.set_language(_Catalogs(), "it_IT")
        assert i18n._("CLI command trace…") == "Comandi CLI…"
        assert i18n._("(CLI commands, view only)") == (
            "(comandi CLI in sola visualizzazione)")
        d = _dialog()
        try:
            assert d.subtitle.text() == "(comandi CLI in sola visualizzazione)"
            assert d.pause_button.text() == "Pausa"
        finally:
            d.reject()
    finally:
        i18n.set_language(None, None)
