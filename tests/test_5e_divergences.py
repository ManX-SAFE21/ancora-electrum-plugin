"""The differences between the copies of the will flows (CHANGELOG #114).

Phase 5e moves the flows into one engine. Before that, the same flow exists
in several copies (the "Check your will" dialog, the Prepare menu, the
automatic rebuild, the other Will tab menus, the command line), and the
copies do not behave the same. This file is the list of those differences,
numbered as in the Phase 5e analysis (2026-10-10, D1-D58) plus the ones
found while taking the photographs (D59-D61).

Every entry says one of:

* ``pinned``: reproduced on the offline world; a test below reads the
  golden files of ``test_5e_char_check.py`` / ``test_5e_char_copies.py``
  and asserts today's different outcomes side by side;
* ``partly``: as pinned, but only part of the description is reproduced
  here (the note says which part is not);
* ``intentional``: a difference of interface wanted by design (or approved
  by the owner); nothing to converge;
* ``not reproduced``: not reachable in this world (the note says why and
  which Phase 5e step deals with it).

Each later Phase 5e step that makes the copies agree changes the golden
files and flips the test of its entry here, in the same commit, after the
owner's decision. The tests read only JSON files: they run in a second.

Run (standalone or with pytest):
    python3 -m pytest tests/test_5e_divergences.py
"""

import json
import os
import re
import sys

import pytest

GOLDEN = os.path.join(os.path.dirname(__file__), "golden", "5e")

# id: (title, status, golden files that show it, note)
DIVERGENCES = {
    1: ("Amounts that do not add up (percentages not 100, fixed amounts only)",
        "pinned", ["check/percentages_not_100", "copies/prepare_percentages_80",
                   "copies/cli_percentages_80", "copies/cli_one_heir_50",
                   "copies/cli_fixed_amounts_only"], ""),
    2: ("'locktime is lower than threshold' guard only in Prepare (and CLI prepare)",
        "partly", ["copies/prepare_expired"],
        "the CLI copy of the guard is not reached here: the amount check "
        "refuses first (D61)"),
    3: ("No usable will-executor: Prepare and CLI refuse even a coherent will",
        "pinned", ["copies/all_executors_deselected_after_sent",
                   "check/no_willexecutor", "copies/cli_no_willexecutor"],
        "the analysis expected Check to sign and send again; with the will "
        "already sent it just says 'nothing to do'"),
    4: ("A coherent will not signed or not sent: who finishes it",
        "partly", ["copies/unsigned_will_check", "copies/unsigned_will_auto_then_cli"],
        "the CLI finishing (#109) is not shown: the Prepare menu did not "
        "save the will (D16), so the CLI saw none and built it again"),
    5: ("What is saved when the will is coherent (Prepare vs CLI)",
        "not reproduced", [],
        "no visible difference in these scenarios (statuses equal)"),
    6: ("Will expired only against the Check Alive (#80, advanced)",
        "not reproduced", [],
        "needs a scenario with the #80 state; to be built in step 5e-09"),
    7: ("Postponing a signed and sent will: four invalidation policies",
        "pinned", ["check/date_later_sent", "copies/prepare_date_later",
                   "copies/auto_date_later_online", "copies/cli_date_later"], ""),
    8: ("No heirs / no valid heirs",
        "partly", ["check/no_heirs", "copies/prepare_no_heirs", "copies/cli_no_heirs"],
        "'no VALID heir after the date' (check_will NoHeirs) not reproduced"),
    9: ("The build produced nothing",
        "pinned", ["check/date_in_past", "copies/prepare_fixed_amount_too_big",
                   "copies/cli_date_in_past"], ""),
    10: ("Errors of the build (balance too low, dust, no will-executor)",
         "pinned", ["check/fee_too_high", "copies/prepare_fee_too_high",
                    "copies/cli_fee_too_high"], ""),
    11: ("After a rebuild the will needs signing (#107): Prepare shows an error",
         "pinned", ["check/fee_changed", "copies/prepare_fee_changed",
                    "copies/prepare_new_coin", "copies/cli_fee_changed"], ""),
    12: ("After a rebuild the anticipated date crosses the Check Alive",
         "not reproduced", [],
         "needs a Check Alive one day from the delivery; decision D10 asks "
         "for this scenario (step 5e-10)"),
    13: ("A will-executor valid by address but unusable for the build",
         "pinned", ["copies/executor_fee_raised_after_sent"],
         "Check: 'Will-Executor excluded: Skipped', 'Building' stays on "
         "'Wait'; the CLI and automatic copies are not run here"),
    14: ("The stored delivery date follows the rebuilt will (#110)",
         "pinned", ["check/new_coin", "copies/prepare_new_coin",
                    "copies/auto_new_coin", "copies/cli_new_coin"], ""),
    15: ("History labels after a rebuild",
         "pinned", ["check/new_coin", "copies/auto_new_coin"], ""),
    16: ("Saving the will after a rebuild",
         "pinned", ["copies/prepare_fee_changed", "copies/auto_fee_changed",
                    "copies/cli_fee_changed"], ""),
    17: ("When the stale local will copies are removed",
         "not reproduced", [], "no observable difference found"),
    18: ("date_to_check with or without the creation date",
         "not reproduced", [], "no observable difference found"),
    19: ("An executable (expired) will: transaction window vs unsigned PSBT",
         "intentional", ["check/expired_basic", "copies/cli_expired"],
         "both leave signing to the owner"),
    20: ("Auto-sign off: Check does not sign, the automatic rebuild does",
         "pinned", ["check/auto_sign_off", "copies/auto_auto_sign_off"],
         "the CLI autorebuild with Auto-sign off is not run here"),
    21: ("Verification after sending: only the Check dialog",
         "pinned", ["check/new_coin", "copies/cli_new_coin"], ""),
    22: ("Wallet with a password",
         "pinned", ["check/cancel_at_sign", "copies/auto_new_coin_password",
                    "copies/cli_password_wallet"], ""),
    23: ("The automatic rebuild never starts in Electrum (timer from the network thread)",
         "not reproduced", [],
         "needs Electrum's threads; known bug, fixed in step 5e-18 with its own test"),
    24: ("The automatic rebuild builds a first will only from the command line",
         "pinned", ["copies/auto_no_will", "copies/cli_percentages_80"], ""),
    25: ("The GUI automatic rebuild never sends the rebuilt will",
         "pinned", ["copies/auto_new_coin", "copies/auto_new_coin_after_menu_window",
                    "copies/cli_new_coin"], ""),
    26: ("The GUI automatic rebuild broadcasts an invalidation by itself",
         "pinned", ["copies/auto_date_later_online", "copies/auto_expired_online",
                    "copies/cli_date_later"],
         "owner decision 2026-10-10: never (step 5e-17)"),
    27: ("A signing error in the CLI autorebuild loses the rebuilt will",
         "not reproduced", [], "needs a signing error; test planned in step 5e-17"),
    28: ("A build error in the automatic rebuild: log vs message",
         "intentional", [], "no window to show it in the automatic copy"),
    29: ("Manual Sign: Cancel does not stop it, errors are silent",
         "pinned", ["copies/menu_sign_cancel"], ""),
    30: ("What the manual Sign saves",
         "partly", ["copies/menu_sign_with_password"],
         "the statuses are saved; the form of the stored transaction is not checked"),
    31: ("Signature counts refreshed only by some copies",
         "not reproduced", [], "single-signature test wallet: nothing to see"),
    32: ("Which will-executors receive the will",
         "pinned", ["copies/executor_fee_raised_after_sent"],
         "the Check skips the executor over the maximum fee, the CLI sends to it"),
    33: ("A will-executor that does not answer",
         "pinned", ["check/executor_down", "copies/menu_broadcast_executor_down",
                    "copies/cli_executor_down"], ""),
    34: ("Checking 'already present' answers",
         "partly", ["check/executor_already_has_it"],
         "the error path (an exception while checking) is not reproduced"),
    35: ("Saving after sending",
         "pinned", ["copies/menus_prepare_sign_broadcast_check", "copies/cli_first_will"], ""),
    36: ("Sending again what was already sent (force)",
         "intentional", [], "per-transaction re-send needs --force on the CLI"),
    37: ("The command line on an online daemon (network thread)",
         "not reproduced", [], "offline world; online probe in step 5e-04"),
    38: ("Row 'check': asks never-sent items, saves nothing",
         "pinned", ["copies/row_check_never_sent", "copies/cli_first_will"], ""),
    39: ("CLI invalidation reference date with no VALID item",
         "not reproduced", [], "needs only REPLACED/INVALIDATED items; step 5e-19"),
    40: ("Nothing to invalidate",
         "partly", ["copies/menu_invalidate_nothing", "copies/cli_invalidate_nothing"],
         "the Check dialog path (password asked, then no buttons) is not reproduced"),
    41: ("Check Alive passed and the delivery date passed too",
         "pinned", ["check/check_alive_expired_advanced",
                    "copies/prepare_check_alive_expired_advanced", "copies/cli_expired"], ""),
    42: ("Renewal: Prepare opens the Check, the CLI gives wrong steps for a fixed date",
         "partly", ["copies/prepare_check_alive_renew", "copies/cli_check_alive_fixed_date"],
         "since #123 the old shared '60d' of the Prepare scenario becomes a "
         "fixed date, so Prepare warns 'fixed date' instead of opening the "
         "Check (the one-click renewal comes back with #126); the CLI half "
         "is no longer divergent: since #123 bal_will_check and "
         "bal_will_prepare give the 'fixed date' text of "
         "bal_will_autorebuild, not the renewal steps; the period renewal "
         "itself is not reproduced here (no period is left after #123)"),
    43: ("Export marks the transactions EXPORTED",
         "partly", ["copies/export_gui_vs_cli"],
         "the export of the live will marks them in both copies; the filtered "
         "export window (which does not) needs its dialog: step 5e-25"),
    44: ("The command line deletes invalid heirs from the wallet",
         "pinned", ["copies/cli_invalid_heir_in_wallet"], ""),
    45: ("Settings written when a wallet is loaded",
         "not reproduced", [], "no observable outcome"),
    46: ("Writing the wallet file (save_db) after changes",
         "not reproduced", [], "the GUI relies on Electrum's own saves; not visible here"),
    47: ("Two in-memory copies of the same wallet (Console tab, RPC)",
         "not reproduced", [], "needs Electrum's Console / daemon; step 5e-27"),
    48: ("bal_will_check only checks",
         "intentional", ["copies/cli_coherent"], ""),
    49: ("Will status: summary vs raw flags",
         "intentional", ["copies/cli_first_will"], ""),
    50: ("Per-heir date and amount unit",
         "intentional", [], "owner-approved (#103)"),
    51: ("Heirs import: skipped entries and dates",
         "not reproduced", [], "GUI file dialog; step 5e-25"),
    52: ("Will-executor add / rename: duplicates and validation",
         "not reproduced", [], "GUI list dialog; step 5e-25"),
    53: ("Deleting the protected Bitcoin After Life executor",
         "intentional", [], "owner-approved (#108)"),
    54: ("Will-executor import: existing records, saving",
         "not reproduced", [], "GUI list dialog; step 5e-25"),
    55: ("Ping: default targets",
         "intentional", [], ""),
    56: ("Welist download in basic mode with nothing for this chain",
         "not reproduced", [], "network; step 5e-25"),
    57: ("Welist download can undo a deselection",
         "not reproduced", [], "GUI list dialog and network; step 5e-25"),
    58: ("Settings: validation and side effects",
         "not reproduced", [], "settings dialog (widgets); the command bar in step 5e-28"),
    # Found while taking the photographs (2026-10-10).
    59: ("No will-executor: two different CLI sentences",
         "pinned", ["copies/cli_no_willexecutor"],
         "bal_will_prepare and bal_will_autorebuild word it differently"),
    60: ("The GUI automatic invalidation is not saved",
         "pinned", ["copies/auto_date_later_online"],
         "the items become INVALIDATED in memory, the wallet keeps them VALID"),
    61: ("bal_will_prepare on a will past its date answers with an amount error",
         "pinned", ["copies/cli_expired", "copies/cli_date_in_past"],
         "the heirs' shares count 0 % once their date has passed"),
}


def golden(name):
    """The steps of a golden file."""
    with open(os.path.join(GOLDEN, name + ".json"), encoding="utf-8") as f:
        return json.load(f)


def step(name, action, nth=0):
    """The ``nth`` step of ``name`` whose action starts with ``action``."""
    found = [s for s in golden(name) if s["action"].startswith(action)]
    return found[nth]


def raised(s):
    """The error text of a step ('' when it returned)."""
    return s["result"].get("raised", "")


def returned(s):
    """What a step returned."""
    return s["result"].get("returned")


def popups(s, kind=None):
    """The popup texts of a step (of one kind)."""
    return [t for k, t in s["popups"] if kind is None or k == kind]


def rows(s):
    """The dialog rows of a Check step, as one text without HTML tags."""
    return re.sub(r"<[^>]+>", "", " | ".join(s["rows"])).replace("\t", " ")


def statuses(s, tx):
    """The statuses of a will item in a step."""
    return next(i["statuses"] for i in s["items"] if i["tx"] == tx)


# ---------------------------------------------------------------- the table

def test_every_divergence_has_an_entry():
    """D1-D61 are all listed, with a known status and existing golden files."""
    assert sorted(DIVERGENCES) == list(range(1, 62))
    for did, (title, status, files, note) in DIVERGENCES.items():
        assert title, did
        assert status in ("pinned", "partly", "intentional", "not reproduced"), did
        if status in ("pinned", "partly"):
            assert files, did
        if status in ("partly", "not reproduced"):
            assert note, did
        for name in files:
            assert os.path.exists(os.path.join(GOLDEN, name + ".json")), (did, name)


def test_every_pinned_divergence_has_a_test():
    """Each pinned or partly pinned entry has its test_dNN function below."""
    module = sys.modules[__name__]
    for did, (_t, status, _f, _n) in DIVERGENCES.items():
        if status in ("pinned", "partly"):
            assert hasattr(module, "test_d{:02d}".format(did)), did


# ---------------------------------------------------------------- the tests

def test_d01():
    """Check and Prepare build with 80 %; bal_will_prepare refuses; autorebuild builds."""
    assert step("check/percentages_not_100", "check")["report"]["result"][0] == "ok"
    p = step("copies/prepare_percentages_80", "gui prepare")
    assert any("Perc amount(80.0)" in t for t in popups(p, "warning"))
    assert returned(p) == {"will items": 2}
    assert "Perc amount(80.0)" in raised(step("copies/cli_percentages_80", "cli prepare_will"))
    assert returned(step("copies/cli_percentages_80", "cli auto_rebuild"))["result"] == "rebuilt"
    assert "Perc amount(50.0)" in raised(step("copies/cli_one_heir_50", "cli prepare_will"))
    assert "Perc amount(0.0)" in raised(step("copies/cli_fixed_amounts_only", "cli prepare_will"))


def test_d02():
    """Prepare stops with 'locktime is lower than threshold'."""
    p = step("copies/prepare_expired", "gui prepare")
    assert "locktime is lower than threshold" in popups(p, "error")


def test_d03():
    """All executors deselected after sending: Check is fine, Prepare and CLI refuse."""
    c = step("copies/all_executors_deselected_after_sent", "check", 1)
    assert c["report"]["result"][0] == "ok" and not c["pushed"]
    p = step("copies/all_executors_deselected_after_sent", "gui prepare")
    assert "no backup transaction or willexecutor selected" in popups(p, "error")
    cli = step("copies/all_executors_deselected_after_sent", "cli prepare_will")
    assert "no backup transaction or willexecutor selected" in raised(cli)


def test_d04():
    """A prepared, unsigned will: Check signs and sends it, the GUI automatic rebuild does nothing."""
    c = step("copies/unsigned_will_check", "check")
    assert len(c["pushed"]) == 2
    a = step("copies/unsigned_will_auto_then_cli", "gui automatic rebuild")
    assert returned(a) is False and not a["pushed"]


def test_d07():
    """Four copies, four ways to postpone a sent will."""
    c = step("check/date_later_sent", "check", 1)
    assert [k for k, _ in c["broadcasts"]] == ["invalidation(dialog)"] and len(c["pushed"]) == 2
    p = step("copies/prepare_date_later", "gui prepare")
    assert any("postponing it" in t for t in popups(p, "message"))
    assert [k for k, _ in p["broadcasts"]] == ["invalidation(transaction window)"]
    assert not p["pushed"]
    a = step("copies/auto_date_later_online", "gui automatic rebuild")
    assert [k for k, _ in a["broadcasts"]] == ["invalidation(automatic)"]
    assert returned(step("copies/cli_date_later", "cli prepare_will"))["result"] == "postponed"
    auto = returned(step("copies/cli_date_later", "cli auto_rebuild"))
    assert auto["result"] == "invalidated" and auto["reason"] == "expired"


def test_d08():
    """No heirs: buttons in Check, silence in Prepare, two answers on the CLI."""
    assert "no-heirs-buttons" in step("check/no_heirs", "check")["dialog_events"]
    p = step("copies/prepare_no_heirs", "gui prepare")
    assert returned(p) is None and not p["popups"]
    assert "Heirs are not defined" in raised(step("copies/cli_no_heirs", "cli prepare_will"))
    assert returned(step("copies/cli_no_heirs", "cli auto_rebuild")) == {"result": "no_heirs"}


def test_d09():
    """Nothing built: Check goes on to phase 2, Prepare shows an error, the CLI says 'nothing'."""
    c = step("check/date_in_past", "check", 1)
    assert "Skipped" in rows(c) and "Signing your will" in rows(c)
    p = step("copies/prepare_fixed_amount_too_big", "gui prepare")
    assert returned(p) == {"will items": 0}
    assert any("Error:WillException" in t for t in popups(p, "error"))
    assert returned(step("copies/cli_date_in_past", "cli auto_rebuild")) == {"result": "nothing"}


def test_d10():
    """Balance too low: a row in Check, an escaped error in Prepare, a message on the CLI."""
    assert "Wallet balance is too low" in rows(step("check/fee_too_high", "check"))
    p = step("copies/prepare_fee_too_high", "gui prepare")
    assert raised(p).startswith("BalanceTooLowException") and not p["popups"]
    assert "Balance too low" in raised(step("copies/cli_fee_too_high", "cli prepare_will"))


def test_d11():
    """After a rebuild: Check and CLI fine, Prepare shows the #107 error."""
    assert step("check/fee_changed", "check", 1)["report"]["result"][0] == "ok"
    for name in ("copies/prepare_fee_changed", "copies/prepare_new_coin"):
        p = step(name, "gui prepare")
        assert any("Please, check your heirs" in t for t in popups(p, "error")), name
    assert returned(step("copies/cli_fee_changed", "cli prepare_will"))["result"] == "rebuilt"


def test_d13():
    """An executor over the maximum fee: Check skips it and leaves 'Building' on 'Wait'."""
    c = step("copies/executor_fee_raised_after_sent", "check", 1)
    assert "Will-Executor excluded" in rows(c) and "Building your will: Wait" in rows(c)


def test_d14():
    """The stored date follows the rebuilt will everywhere except in Prepare."""
    assert step("check/new_coin", "check", 1)["settings"]["locktime"] == "2032-09-22"
    p = step("copies/prepare_new_coin", "gui prepare")
    assert p["settings"]["locktime"] == "2032-09-23"
    assert any(i["locktime"] == "2032-09-22" for i in p["items"])
    assert step("copies/auto_new_coin", "gui automatic")["settings"]["locktime"] == "2032-09-22"
    assert step("copies/cli_new_coin", "cli auto_rebuild")["settings"]["locktime"] == "2032-09-22"


def test_d15():
    """Labels on the rebuilt will: Check sets them, the automatic rebuild does not."""
    c = step("check/new_coin", "check", 1)
    assert c["labels"]["T3"] == "Safe21 Heartbeat backup transaction"
    a = step("copies/auto_new_coin", "gui automatic")
    assert a["labels"]["T3"] == ""


def test_d16():
    """After a rebuild: Prepare does not save it, the automatic rebuild and the CLI do."""
    p = step("copies/prepare_fee_changed", "gui prepare")
    assert len(p["items"]) == 4 and len(p["stored"]) == 2
    assert len(step("copies/auto_fee_changed", "gui automatic")["stored"]) == 4
    assert len(step("copies/cli_fee_changed", "cli prepare_will")["stored"]) == 4


def test_d20():
    """Auto-sign off: Check leaves the will unsigned, the automatic rebuild signs it."""
    c = step("check/auto_sign_off", "check", 1)
    assert "COMPLETE" not in statuses(c, "T3")
    a = step("copies/auto_auto_sign_off", "gui automatic")
    assert "COMPLETE" in statuses(a, "T3")


def test_d21():
    """Only Check verifies with the will-executors after sending."""
    assert step("check/new_coin", "check", 1)["verified"]
    cli = step("copies/cli_new_coin", "cli auto_rebuild")
    assert cli["pushed"] and not cli["verified"]


def test_d22():
    """Password: Check asks it, the automatic rebuild leaves the will unsigned in silence."""
    c = step("check/cancel_at_sign", "check", 1)
    assert ["password", "Sign your will"] in c["questions"]
    a = step("copies/auto_new_coin_password", "gui automatic")
    assert not a["questions"] and not a["popups"]
    assert statuses(a, "T3") == ["VALID"]
    assert returned(step("copies/cli_password_wallet", "cli auto_rebuild"))["result"] == "needs_signing"


def test_d24():
    """With no will: the GUI automatic rebuild does nothing, the CLI one builds a first will."""
    assert returned(step("copies/auto_no_will", "gui automatic")) is False
    cli = step("copies/cli_percentages_80", "cli auto_rebuild")
    assert returned(cli)["result"] == "rebuilt" and len(cli["pushed"]) == 2


def test_d25():
    """The GUI automatic rebuild signs but never sends; the CLI one sends."""
    for name in ("copies/auto_new_coin", "copies/auto_new_coin_after_menu_window"):
        a = step(name, "gui automatic")
        assert returned(a) is True and not a["pushed"], name
        assert "COMPLETE" in statuses(a, "T3"), name
    assert len(step("copies/cli_new_coin", "cli auto_rebuild")["pushed"]) == 2


def test_d26():
    """The GUI automatic rebuild broadcasts an invalidation; the CLI only returns it."""
    for name in ("copies/auto_date_later_online", "copies/auto_expired_online"):
        a = step(name, "gui automatic")
        assert [k for k, _ in a["broadcasts"]] == ["invalidation(automatic)"], name
    cli = step("copies/cli_date_later", "cli auto_rebuild")
    assert not cli["broadcasts"] and returned(cli)["invalidation_tx"]["tx"].startswith("<tx ")


def test_d29():
    """Manual Sign: after Cancel the signing task still runs, with no message."""
    s = step("copies/menu_sign_cancel", "gui menu sign")
    assert s["questions"][0][0] == "password"
    assert "waiting window: Signing transactions..." in s["window_events"]
    assert not s["popups"] and "COMPLETE" not in statuses(s, "T1")


def test_d30():
    """Manual Sign with the password: the signed statuses reach the wallet."""
    s = step("copies/menu_sign_with_password", "gui menu sign")
    assert s["stored"]["T1"] == ["COMPLETE", "VALID"]


def test_d32():
    """An executor over the maximum fee still gets the will from the CLI."""
    cli = step("copies/executor_fee_raised_after_sent", "cli broadcast_command")
    assert ["https://we1.test", "T2"] in cli["pushed"]


def test_d33():
    """An executor that does not answer: three copies, three outcomes."""
    c = step("check/executor_down", "check", 1)
    assert "PUSH_FAIL" in statuses(c, "T4")
    m = step("copies/menu_broadcast_executor_down", "gui menu broadcast")
    assert popups(m, "message") == ["All transactions are broadcasted to respective Will-Executors"]
    assert "PUSH_FAIL" not in statuses(m, "T2")
    cli = step("copies/cli_executor_down", "cli broadcast_command")
    assert returned(cli)["_failed"] == {"https://we1.test": "no answer in time"}
    assert cli["stored"]["T2"] == ["COMPLETE", "VALID"]


def test_d34():
    """'Already present': the Check row shows 'True' instead of 'Ok'."""
    c = step("check/executor_already_has_it", "check", 1)
    assert "checked https://we0.test - T3 : True" in rows(c)


def test_d35():
    """After sending: the menu does not save PUSHED, the CLI does."""
    m = step("copies/menus_prepare_sign_broadcast_check", "gui menu broadcast")
    assert "PUSHED" in statuses(m, "T1") and "PUSHED" not in m["stored"]["T1"]
    cli = step("copies/cli_first_will", "cli broadcast_command")
    assert "PUSHED" in cli["stored"]["T1"]


def test_d38():
    """Row check on a never-sent will marks it failed and saves nothing."""
    r = step("copies/row_check_never_sent", "gui row check")
    assert "CHECK_FAIL" in statuses(r, "T1") and r["stored"] == {}
    cli = step("copies/cli_first_will", "cli check_transactions")
    assert "CHECKED" in cli["stored"]["T1"]


def test_d40():
    """Nothing to invalidate: a message in the menu, empty fields on the CLI."""
    m = step("copies/menu_invalidate_nothing", "gui menu invalidate")
    assert popups(m, "message") == ["No transactions to invalidate"]
    assert returned(step("copies/cli_invalidate_nothing", "cli invalidate")) == {
        "tx": None, "txid": None}


def test_d41():
    """Delivery passed (advanced): Check opens the invalidation, Prepare only warns."""
    c = step("check/check_alive_expired_advanced", "check", 1)
    assert ["transaction window", "opened"] in c["popups"]
    p = step("copies/prepare_check_alive_expired_advanced", "gui prepare")
    assert popups(p, "warning") and not p["broadcasts"]
    assert ["transaction window", "opened"] not in p["popups"]
    assert returned(step("copies/cli_expired", "cli auto_rebuild"))["result"] == "invalidated"


def test_d42():
    """Renewal: the copies agree for a fixed date since #123.

    Before #123 Prepare opened the Check for the renewal of the "60d"
    scenario, and ``bal_will_check`` / ``bal_will_prepare`` gave renewal
    steps (a paid invalidation) that cannot move a fixed date. Since #123
    that old shared period becomes a fixed date, so Prepare opens nothing
    and says the date is fixed (until #126), and the three commands give
    the same "choose a later delivery date" text."""
    p = step("copies/prepare_check_alive_renew", "gui prepare")
    assert p.get("dialogs_opened", []) == []
    assert any("Your delivery is a fixed date" in t for t in popups(p, "warning"))
    auto = returned(step("copies/cli_check_alive_fixed_date", "cli auto_rebuild"))
    assert "fixed date" in auto["message"]
    for action in ("cli will_check", "cli prepare_will"):
        msg = raised(step("copies/cli_check_alive_fixed_date", action))
        assert "Your delivery is a fixed date" in msg, (action, msg)
        assert "bal_will_invalidate" not in msg, (action, msg)
        assert msg == "UserFacingException: " + auto["message"], (action, msg)


def test_d43():
    """Exporting the live will marks EXPORTED in the GUI and in the CLI."""
    g = step("copies/export_gui_vs_cli", "gui export")
    assert "EXPORTED" in g["stored"]["T1"]
    assert returned(step("copies/export_gui_vs_cli", "cli export_will"))["exported"] == 2


def test_d44():
    """An invalid heir: Check keeps it in the wallet, a CLI read deletes it."""
    c = step("copies/cli_invalid_heir_in_wallet", "check")
    assert "mallory" in c["heirs"]
    cli = step("copies/cli_invalid_heir_in_wallet", "cli heirs_list")
    assert "mallory" not in cli["heirs"]


def test_d59():
    """No will-executor: two different sentences on the CLI."""
    a = raised(step("copies/cli_no_willexecutor", "cli prepare_will"))
    b = raised(step("copies/cli_no_willexecutor", "cli auto_rebuild"))
    assert "no backup transaction or willexecutor selected" in a
    assert "No backup transaction or will-executor selected" in b


def test_d60():
    """The GUI automatic invalidation is not saved."""
    a = step("copies/auto_date_later_online", "gui automatic")
    assert "INVALIDATED" in statuses(a, "T1") and "VALID" in a["stored"]["T1"]


def test_d61():
    """bal_will_prepare on a will past its date: an amount error."""
    for name in ("copies/cli_expired", "copies/cli_date_in_past"):
        assert "Perc amount(0.0)" in raised(step(name, "cli prepare_will")), name


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
