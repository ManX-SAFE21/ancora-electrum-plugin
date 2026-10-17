"""The will summary shown at the top of the Will tab (CHANGELOG #84a).

The transaction list shows one row per will-executor copy, and the toolbar
shows the SETTINGS (delivery recipe, Check Alive). Neither answers the
owner's questions directly: is my will in force, when is it delivered, when
will I be asked to renew it, do the will-executors have it? (Owner's remark:
with "4y" in Raw mode he "lost the thread" and had to trust the wallet.)
:func:`will_summary` answers them in plain words, from the data the plugin
already has. It only reads: it never builds, signs, sends or invalidates.

The GUI draws the result (``WillSummaryWidget``) and maps ``action`` to the
existing commands (Check, Renew, the wizard). Texts approved by the owner
with the mockup of #84a.

Must never import Qt (``bal/core`` rule).
"""

from datetime import datetime, timezone

from ..i18n import _
from . import datetext
from . import delivery as _delivery
from .checkalive import check_alive_basic, check_alive_renewal, resolve_date_to_check
from .messages import _day, check_alive_cannot_renew
from .will import Will

_DAY = 86400

# Where a will-executor stands with the will in force, worst first: the
# summary reports an executor by its worst copy (a will usually has one tx
# per executor, but several coins can give several txs).
_EXECUTOR_ORDER = ("failed", "to_sign", "to_send", "to_verify", "verified",
                   "deselected")

#: The states a DESELECTED will-executor's copy turns into "deselected"
#: (#134): it never received the will, and the sending skips it
#: (``Willexecutors.get_willexecutor_transactions``), so it is not "to
#: send". A copy it already received keeps its state.
_NOT_RECEIVED = ("failed", "to_sign", "to_send")


def selected_urls(willexecutors):
    """The URLs of the will-executors selected now (#134), as a set.

    ``willexecutors``: the plugin's list (``Willexecutors.get_willexecutors``).
    The one rule shared by the will summary, the Check window's rows, the
    report's states and the Future view's squares: a copy for a will-executor
    not in this set, never received, is "deselected", not "to send".
    """
    return {url for url, we in (willexecutors or {}).items()
            if isinstance(we, dict) and we.get("selected")}


def _now(now):
    """``now`` or the current UTC time (same clock as the rest of the core)."""
    return now if now is not None else datetime.now(tz=timezone.utc).timestamp()


def _span(start, end):
    """The time from ``start`` to ``end`` in words: "12 days", "1 year and 11 months".

    Days up to 59 days, then calendar months and years: "1 year and 11
    months" reads better than "720 days" for a delivery years away.
    """
    days = int((end - start) // _DAY)
    if days < 60:
        return _("1 day") if days == 1 else _("{} days").format(days)
    months = _delivery.months_between(start, end)
    years, rest = divmod(months, 12)
    months_text = _("1 month") if rest == 1 else _("{} months").format(rest)
    if years == 0:
        return months_text
    years_text = _("1 year") if years == 1 else _("{} years").format(years)
    if rest == 0:
        return years_text
    return _("{years} and {months}").format(years=years_text, months=months_text)


def relative_date(ts, now=None):
    """``"(in 1 year and 11 months)"``, ``"(passed 5 days ago)"`` or ``"(today)"``.

    The parentheses are part of the texts on purpose: a bare "in {}" or
    "today" could match a different Electrum translation, which wins over
    BAL's catalog (see ``bal.i18n``).
    """
    current = _now(now)
    if abs(ts - current) < _DAY:
        return _("(today)")
    if ts > current:
        return _("(in {})").format(_span(current, ts))
    return _("(passed {} ago)").format(_span(ts, current))


def _thresholds(warn_months):
    """``(yellow, red)`` from ``warn_months``, the defaults when it is
    missing or not a valid pair (the colour must never break the summary)."""
    try:
        yellow, red = warn_months
    except (TypeError, ValueError):
        return _delivery.WARN_YELLOW_MONTHS, _delivery.WARN_RED_MONTHS
    return _delivery.warning_months(yellow, red)


def delivery_warning(delivery, now=None, warn_months=None):
    """The remaining-time colour of a delivery date and its tooltip (#124).

    ``warn_months`` is ``(yellow, red)`` in calendar months (default 12 and
    6, see ``BalPlugin.warning_months``). Returns ``(level, tooltip)``:
    ``level`` is ``"green"``, ``"yellow"``, ``"red"`` or ``"passed"``
    (:func:`bal.core.delivery.delivery_level`); the tooltip is empty for
    green. Texts approved by the owner on 2026-10-12.
    """
    yellow, red = _thresholds(warn_months)
    level = _delivery.delivery_level(int(delivery), _now(now), yellow, red)
    if level == _delivery.LEVEL_PASSED:
        tip = _("The delivery date has passed: the heirs can already receive the bitcoin.")
    elif level == _delivery.LEVEL_RED:
        tip = _(
            "Less than {} months to the delivery: if you are still here, "
            "choose a later date."
        ).format(red)
    elif level == _delivery.LEVEL_YELLOW:
        tip = _("Less than {} months to the delivery.").format(yellow)
    else:
        tip = ""
    return level, tip


def _row(label, strong="", text="", alarm="", red=False, level=None, tip=""):
    """One label/value line of the summary.

    ``strong`` is shown in bold (a date), ``text`` normally, ``alarm`` in
    red after a separator, and ``red`` turns the whole value red. ``level``
    (the delivery row only, #124) colours the date and its text green,
    yellow or red, and ``tip`` is shown when the mouse is over it.
    """
    return {"label": label, "strong": strong, "text": text, "alarm": alarm,
            "red": red, "level": level, "tip": tip}


def _delivery_row(delivery, now, warn_months, day=_day):
    """The "Delivery to the heirs" line, with its remaining-time colour.

    ``day``: how a date is written (see :func:`will_summary`).
    """
    level, tip = delivery_warning(delivery, now, warn_months)
    return _row(
        _("Delivery to the heirs"), strong=day(delivery),
        text=relative_date(delivery, now), red=delivery <= now,
        level=level, tip=tip,
    )


def _executor_state(w):
    """Where the will-executor of VALID item ``w`` stands.

    Same priority as the Status column (``will_status_text``, #83), so the
    summary and the list never disagree.
    """
    if w.get_status("CHECK_FAIL") and not w.get_status("CHECKED"):
        return "failed"
    if w.get_status("CHECKED"):
        return "verified"
    if w.get_status("PUSH_FAIL"):
        return "failed"
    if w.get_status("PUSHED"):
        return "to_verify"
    if w.get_status("COMPLETE"):
        return "to_send"
    return "to_sign"


def executor_states(valid, selected=None):
    """``{url: state}`` for the VALID items ``valid``, at each executor's worst copy.

    States: ``failed``, ``to_sign``, ``to_send``, ``to_verify``,
    ``verified`` and (#134) ``deselected``: with ``selected`` (the URLs
    selected now, :func:`selected_urls`), a copy for a will-executor not
    selected that never received the will. None: no selection rule (as
    before #134). Items without a will-executor (backup transactions) are
    left out. Also used by the Building Will dialog (#84b).
    """
    worst = {}
    for w in valid.values():
        url = (w.we or {}).get("url")
        if not url:
            continue
        state = _executor_state(w)
        if selected is not None and url not in selected and state in _NOT_RECEIVED:
            state = "deselected"
        old = worst.get(url)
        if old is None or _EXECUTOR_ORDER.index(state) < _EXECUTOR_ORDER.index(old):
            worst[url] = state
    return worst


def executor_counts(valid, selected=None):
    """``{state: number of will-executors}`` for the VALID items ``valid``.

    One entry per executor URL, at its worst copy (:func:`executor_states`,
    ``selected`` as there).
    """
    counts = {state: 0 for state in _EXECUTOR_ORDER}
    for state in executor_states(valid, selected).values():
        counts[state] += 1
    return counts


def _executors_row(counts):
    """The "Will-Executor" line: who received and verified the will.

    The deselected will-executors (#134) are not counted: they are told
    apart by their own line (:func:`deselected_text`).
    """
    counts = {k: v for k, v in counts.items() if k != "deselected"}
    total = sum(counts.values())
    label = _("Will-Executor")
    if total == 0:
        return _row(label, text=_("none (backup transaction only)"))
    if counts["verified"] == total:
        text = (
            _("1 of 1 received and verified it")
            if total == 1
            else _("{} of {} received and verified it").format(total, total)
        )
        return _row(label, text=text)
    if counts["to_sign"] == total:
        text = (
            _("to be signed and sent to 1 will-executor")
            if total == 1
            else _("to be signed and sent to {} will-executors").format(total)
        )
        return _row(label, text=text)
    if counts["to_send"] == total:
        text = (
            _("to be sent to 1 will-executor")
            if total == 1
            else _("to be sent to {} will-executors").format(total)
        )
        return _row(label, text=text)
    parts = []
    verified = counts["verified"]
    if verified:
        parts.append(
            _("1 of {} verified it").format(total)
            if verified == 1
            else _("{} of {} verified it").format(verified, total)
        )
    if counts["to_verify"]:
        parts.append(_("{} to be verified").format(counts["to_verify"]))
    if counts["to_send"]:
        parts.append(_("{} to be sent").format(counts["to_send"]))
    if counts["to_sign"]:
        parts.append(_("{} to be signed").format(counts["to_sign"]))
    failed = counts["failed"]
    alarm = ""
    if failed:
        alarm = (
            _("1 did not receive it")
            if failed == 1
            else _("{} did not receive it").format(failed)
        )
    return _row(label, text=" · ".join(parts), alarm=alarm)


def deselected_text(count):
    """Why some will-executors did not receive the will (#134).

    "2 will-executors are deselected, so they did not receive the will. To
    send it to them too, select them again." (singular for one). The Will
    tab summary and the Check window show it with an "Open Will-Executors"
    button.
    """
    if count == 1:
        return _(
            "1 will-executor is deselected, so it did not receive the will. "
            "To send it to it too, select it again."
        )
    return _(
        "{} will-executors are deselected, so they did not receive the will. "
        "To send it to them too, select them again."
    ).format(count)


def _heirs_row(heirs_count, prepared=True):
    """The "Heirs" line (their number; the names are in the Heirs tab)."""
    text = str(heirs_count)
    if not prepared:
        text += " " + _("(no transaction prepared)")
    return _row(_("Heirs"), text=text)


def will_summary(willitems, heirs_count, will_settings, is_basic_mode, now=None,
                 still_active=None, warn_months=None, long_dates=False,
                 selected=None, in_force=None, has_funds=None):
    """Describe the will in force for the summary box of the Will tab.

    Args:
        willitems: ``{txid: WillItem}`` (the plugin's ``willitems``).
        heirs_count: number of heirs defined in the wallet.
        will_settings: the will settings (``"locktime"``, ``"threshold"``).
        is_basic_mode: ``True`` in BASIC mode (no Check Alive line, except
            for a will in the period form, #127).
        now: overridable clock for tests.
        still_active: the sent txs still executable on-chain, as returned by
            ``Will.find_still_active_older_txs`` (``(item, locktime, url)``).
            Only used when no item is VALID: the plugin's records can be
            wrong, the blockchain is not (owner's wallet, #84a).
        warn_months: ``(yellow, red)`` thresholds of the delivery colour in
            months (#124); ``None`` uses the defaults 12 and 6.
        long_dates: CHANGELOG #134 (owner): the dates in the one long style
            of ``bal.core.datetext`` ("13 October 2028, 00:00 UTC") and, for
            a prepared will not sent yet whose delivery differs from the will
            in force, the two lines "In force now" / "After signing". The Will
            tab and the Check window pass True; the PDF report keeps the
            ``YYYY-MM-DD`` dates until its own step (#137).
        selected: the URLs of the will-executors selected now
            (:func:`selected_urls`, #134): a copy for a deselected one, never
            received, is not "to send" (the sending skips it); the summary
            then has ``deselected`` (their number) and ``deselected_text``.
            None: no selection rule.
        in_force: the delivery date (locktime) of THE will in force, the
            one rule of #128 used by the Will tab's List, the Future view,
            the wizard and the Check window (``engine.context.
            in_force_delivery``: the earliest delivery among the items
            signed and/or sent, VALID or REPLACED, still executable). Used
            for "In force now" / "After signing" and the note "the will sent
            before stays in force" (#135 follow-up: with ``still_active``
            alone, a copy already sent that the prepared will reuses, being
            VALID again, was left out and an older, LATER will was named as
            the will in force). None: ``still_active`` as before.
        has_funds: #160 (owner): False when the wallet shows no balance.
            Then "No will in force" is not red: with nothing in the wallet
            there is nothing to protect yet (and the offline computer of a
            cold wallet, which never shows a balance, only signs). The box
            says so in grey, keeps "Build Your Will" and points to Tools >
            "Sign will offline...". None (unknown) or True: red, as before.

    Returns:
        A dict: ``state`` (``"ok"``, ``"to_sign"``, ``"to_send"``,
        ``"not_received"``, ``"none_received"``, ``"check_alive"``,
        ``"expired"``, ``"still_active"``, ``"none"``, ``"executed"``,
        ``"mempool"``, ``"busy"``, ``"complete_send"`` (#134: in force at some
        will-executors, still to send to the others)), ``kind`` (the colour: ``"ok"`` green,
        ``"warn"`` orange, ``"bad"`` red, ``"info"`` and ``"busy"`` grey), ``title``, ``rows`` (see :func:`_row`), ``note``
        (or ``None``), ``action`` (``"check"``, ``"sign_send"``,
        ``"send"``, ``"renew"``, ``"wizard"`` or ``None``) and
        ``action_text`` (its button label). Never raises:
        on an unexpected error it returns ``None`` and the GUI hides the box.
    """
    try:
        return _with_deselected(_will_summary(willitems or {}, heirs_count, will_settings or {},
                             is_basic_mode, _now(now), still_active or [],
                             warn_months, datetext.utc_text if long_dates else _day,
                             long_dates, selected, in_force, has_funds),
                             willitems or {}, selected)
    except Exception:
        return None


def _is_deselected(w, selected, deselected):
    """True for a copy of a deselected will-executor that never received
    the will (#134); False when the rule is off (``deselected`` 0)."""
    if not deselected or selected is None:
        return False
    url = (w.we or {}).get("url")
    return bool(url) and url not in selected and _executor_state(w) in _NOT_RECEIVED


def _with_deselected(summary, willitems, selected):
    """Add ``deselected`` / ``deselected_text`` to ``summary`` (#134)."""
    if summary is None:
        return None
    count = 0
    if selected is not None and summary.get("state") not in ("busy", "none"):
        counts = executor_counts(Will.only_valid_list(willitems), selected)
        if any(v for k, v in counts.items() if k != "deselected"):
            count = counts["deselected"]
    summary["deselected"] = count
    summary["deselected_text"] = deselected_text(count) if count else ""
    return summary


def _will_summary(willitems, heirs_count, will_settings, is_basic_mode, now,
                  still_active, warn_months=None, day=_day, compare=False,
                  selected=None, in_force=None, has_funds=None):
    """:func:`will_summary` without the safety net (see there).

    ``day`` writes a date; ``compare`` adds the "In force now" / "After
    signing" lines (both from ``long_dates``); ``in_force``: the date of the
    will in force (see :func:`will_summary`).
    """
    valid = Will.only_valid_list(willitems)
    if not valid:
        # No will in force. A will already on its way to the heirs is not
        # "missing": say so instead of offering to build a new one.
        if any(w.get_status("CONFIRMED") for w in willitems.values()):
            return _summary("executed", "info", _("Your will has been executed"))
        if any(w.get_status("MEMPOOL") for w in willitems.values()):
            return _summary("mempool", "info", _("Your will is being executed (mempool)"))
        if still_active:
            return _still_active_summary(still_active, heirs_count, now, warn_months,
                                         day)
        invalidated = any(w.get_status("INVALIDATED") for w in willitems.values())
        if invalidated:
            note = _(
                "Your previous will was invalidated on the blockchain: build "
                "and sign a new one, otherwise nobody will receive your bitcoin."
            )
        elif has_funds is False:
            # #160 (owner): an empty wallet has nothing to protect yet, and
            # the offline computer of a cold wallet (no balance shown) only
            # signs: grey, not red, and the way to the offline signing.
            return _summary(
                "none", "info", _("No will yet"),
                rows=[_heirs_row(heirs_count, prepared=bool(willitems))],
                note=_(
                    "This wallet shows no balance: when it holds bitcoin, "
                    "create your will so it reaches your heirs.\nDo you only "
                    "need to sign a will brought on a USB stick? Use Tools > "
                    "Sign will offline."
                ),
                action="wizard", action_text=_("Build Your Will"),
            )
        else:
            note = _("Until you create your will, nobody will receive your bitcoin.")
        return _summary(
            "none", "bad", _("No will in force"),
            rows=[_heirs_row(heirs_count, prepared=bool(willitems))],
            note=note,
            action="wizard", action_text=_("Build Your Will"),
        )

    delivery = int(Will.get_min_locktime(valid))
    creation = Will.get_creation_time(valid)
    rows = []
    expired = delivery <= now
    rows.append(_delivery_row(delivery, now, warn_months, day))
    if expired:
        # The executors can broadcast it now: the Check invalidates it and
        # prepares a new one (same text as the Check Alive message, #79).
        return _summary(
            "expired", "bad", _("The delivery date has passed"), rows=rows,
            note=_(
                "The delivery date of your will ({date}) has already passed: the "
                "will can be executed now. Press Check to invalidate it and "
                "prepare a new one."
            ).format(date=day(delivery)),
            action="check", action_text=_("Check"),
        )

    renewal = None
    check_row = None
    # BASIC hides the Check Alive of a fixed date only: a period-form will
    # keeps its renewal in BASIC (#127, owner decision 5).
    if (not check_alive_basic(is_basic_mode, will_settings)
            and will_settings.get("threshold") is not None):
        renewal = check_alive_renewal(
            False, will_settings, built_locktime=delivery,
            creation_date=creation, now=now,
        )
        check_date = (
            renewal["check_date"] if renewal else resolve_date_to_check(
                False, will_settings, now=now,
                built_locktime=delivery, creation_date=creation,
            )
        )
        check_row = _row(
            _("Check Alive"), strong=day(check_date),
            text=relative_date(check_date, now), red=bool(renewal),
        )
        rows.append(check_row)

    counts = executor_counts(valid, selected)
    if counts["deselected"] and not any(
            v for k, v in counts.items() if k != "deselected"):
        # Every will-executor is deselected: nothing would be counted, so
        # the will reads as before (the rule only tells apart SOME).
        counts = executor_counts(valid)
    rows.append(_executors_row(counts))
    rows.append(_heirs_row(heirs_count))
    deselected = counts["deselected"]
    unsigned = [w for w in valid.values() if not w.get_status("COMPLETE")
                and not _is_deselected(w, selected, deselected)]

    if renewal:
        if renewal.get("renewable"):
            return _summary(
                "check_alive", "warn",
                _("The Check Alive date has passed: renew your will"),
                rows=rows,
                note=_(
                    "If you do not renew it, your will stays valid and will be "
                    "delivered on {}."
                ).format(day(delivery)),
                action="renew", action_text=_("Renew"),
            )
        # A fixed delivery date, or settings that would put the renewed
        # Check Alive in the past too: the owner must change the settings
        # first, so there is nothing to press here.
        return _summary(
            "check_alive", "warn", _("The Check Alive date has passed"),
            rows=rows, note=check_alive_cannot_renew(renewal, day),
        )

    if unsigned:
        if compare:
            _insert_comparison(rows, still_active, delivery, in_force)
        return _summary(
            "to_sign", "warn", _("Will prepared: it must be signed and sent"),
            rows=rows, note=_previous_will_note(still_active, day, delivery, in_force),
            action="sign_send", action_text=_("Sign and send"),
        )
    if counts["to_send"] and counts["verified"]:
        # #134 (owner's real test): the will is already in force at some
        # will-executors (received and verified) and only the others still
        # have to receive it (signed and not sent, or failed). Nothing of
        # "the will sent before stays in force": THIS will is in force. The
        # orange of an action to do, as "In force, but N did not receive
        # it" (state "not_received"); the button sends it.
        return _summary(
            "complete_send", "warn", _("Will in force: complete the sending"),
            rows=rows, note=_complete_send_note(counts),
            action="send", action_text=_("Send"),
        )
    if counts["to_send"]:
        if compare:
            _insert_comparison(rows, still_active, delivery, in_force)
        return _summary(
            "to_send", "warn",
            _("Will signed: it must be sent to the will-executors"),
            rows=rows, note=_previous_will_note(still_active, day, delivery, in_force),
            action="send", action_text=_("Send"),
        )
    failed = counts["failed"]
    if failed:
        received = counts["verified"] + counts["to_verify"]
        if not received:
            return _summary(
                "none_received", "warn",
                _("No will-executor received your will"),
                rows=rows, action="check", action_text=_("Check again"),
            )
        title = (
            _("In force, but 1 will-executor did not receive it")
            if failed == 1
            else _("In force, but {} will-executors did not receive it").format(failed)
        )
        return _summary(
            "not_received", "warn", title, rows=rows,
            action="check", action_text=_("Check again"),
        )

    if check_row is not None:
        # Tell the owner what the Check Alive date means for him (#79: from
        # that day the wallet asks "Renew / Not now" when it opens).
        check_row["text"] += " — " + _("from that day the wallet asks you to renew it")
    return _summary("ok", "ok", _("Your will is in force"), rows=rows)


def _in_force_date(still_active, in_force):
    """The delivery date of the will in force for the comparison, or None.

    ``in_force`` (the #128 rule, see :func:`will_summary`) when the caller
    knows it; otherwise the earliest of ``still_active`` (the older rule,
    kept for the callers without a wallet).
    """
    if in_force is not None:
        return int(in_force)
    if not still_active:
        return None
    return min(int(locktime) for _item, locktime, _url in still_active)


def _insert_comparison(rows, still_active, delivery, in_force=None):
    """Add "In force now" and "After signing" under the delivery row (#134).

    Owner (ZIP test): with a prepared will not sent yet, the summary showed
    only the new date and the reader had nothing to compare it with. When
    the will sent before (``still_active``, still executable on-chain) has
    another delivery date, two lines follow the delivery row: its date, and
    the prepared one with "moved earlier by …" or "moved later by …"
    (``datetext.delivery_change``). Nothing when the dates are equal or no
    sent will is in force. The delivery row stays first (the Check window's
    result sentence reads the first dated row). ``in_force``: see
    :func:`_in_force_date`.
    """
    in_force = _in_force_date(still_active, in_force)
    if in_force is None or in_force == int(delivery):
        return
    rows.insert(1, _row(_("In force now"), strong=datetext.utc_text(in_force)))
    rows.insert(2, _row(
        _("After signing"), strong=datetext.utc_text(delivery),
        text="→ " + _("delivery {}").format(datetext.delivery_change(in_force, delivery)),
    ))


def _complete_send_note(counts):
    """"3 of 5 will-executors received and verified it. Send it to the
    other 2 too, for more safety." (#134). The others: those still to send
    and those whose sending failed."""
    # The deselected will-executors (#134) are not part of "of m".
    total = sum(v for k, v in counts.items() if k != "deselected")
    verified = counts["verified"]
    others = counts["to_send"] + counts["failed"]
    first = (
        _("1 of {m} will-executors received and verified it.").format(m=total)
        if verified == 1
        else _("{n} of {m} will-executors received and verified it.").format(
            n=verified, m=total)
    )
    second = (
        _("Send it to the other one too, for more safety.")
        if others == 1
        else _("Send it to the other {} too, for more safety.").format(others)
    )
    return first + " " + second


def _previous_will_note(still_active, day=_day, current=None, in_force=None):
    """The will sent before, still in force until the new one is sent (#84b).

    After a Cancel at signing (or before sending) the executors still hold
    the will sent before, and it can be executed: say so, with its date,
    instead of letting "it must be signed" suggest there is no will. None
    when no sent will can still be executed, and (#134) when its date is the
    date of the will prepared now (``current``): then there is no different
    will to warn about (owner's real test: the sentence was false).
    ``in_force``: the date of the will in force (:func:`_in_force_date`).
    """
    delivery = _in_force_date(still_active, in_force)
    if delivery is None:
        return None
    if current is not None and delivery == int(current):
        return None
    return _(
        "Until you sign and send it, the will sent before stays in force: "
        "delivery on {}."
    ).format(day(delivery))


def _still_active_summary(still_active, heirs_count, now, warn_months=None,
                          day=_day):
    """No VALID item, but a will sent before can still be executed (#84a).

    Owner's wallet: after "later delivery date -> Cancel" the records marked
    the sent will "Replaced" while its coin was never spent, and the summary
    said "No will in force" although three will-executors still held an
    executable will. The earliest of those txs is the date the heirs would
    receive the bitcoin. No button: the records must be fixed first, and
    "Check" would rebuild from the same wrong records.
    """
    delivery = min(int(locktime) for _item, locktime, _url in still_active)
    urls = {url for _item, _locktime, url in still_active if url}
    rows = [_delivery_row(delivery, now, warn_months, day)]
    rows.append(_row(
        _("Will-Executor"),
        text=(
            _("1 will-executor holds it")
            if len(urls) == 1
            else _("{} will-executors hold it").format(len(urls))
        ),
    ))
    rows.append(_heirs_row(heirs_count))
    return _summary(
        "still_active", "warn",
        _("Your previous will is still active at the will-executors"),
        rows=rows,
        note=_(
            "The plugin marks it as replaced or invalidated, but its coins "
            "were never spent on the blockchain: it will be delivered on {}."
        ).format(day(delivery)),
    )


def busy_summary():
    """The summary while a working dialog is on screen (#84a).

    Grey, one sentence, no lines and no button: the dialog in front is the
    only place that talks and asks, and half-way states ("to sign") would
    only confuse (owner's remark).
    """
    return _summary(
        "busy", "busy", _("Operation in progress: follow the window in front")
    )


def _summary(state, kind, title, rows=None, note=None, action=None,
             action_text=None):
    """Build the dict returned by :func:`will_summary`."""
    return {
        "state": state,
        "kind": kind,
        "title": title,
        "rows": rows or [],
        "note": note,
        "action": action,
        "action_text": action_text,
    }
