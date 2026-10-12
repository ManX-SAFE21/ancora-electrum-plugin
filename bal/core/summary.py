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
from . import delivery as _delivery
from .checkalive import check_alive_basic, check_alive_renewal, resolve_date_to_check
from .messages import _day, check_alive_cannot_renew
from .will import Will

_DAY = 86400

# Where a will-executor stands with the will in force, worst first: the
# summary reports an executor by its worst copy (a will usually has one tx
# per executor, but several coins can give several txs).
_EXECUTOR_ORDER = ("failed", "to_sign", "to_send", "to_verify", "verified")


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


def _delivery_row(delivery, now, warn_months):
    """The "Delivery to the heirs" line, with its remaining-time colour."""
    level, tip = delivery_warning(delivery, now, warn_months)
    return _row(
        _("Delivery to the heirs"), strong=_day(delivery),
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


def executor_states(valid):
    """``{url: state}`` for the VALID items ``valid``, at each executor's worst copy.

    States: ``failed``, ``to_sign``, ``to_send``, ``to_verify``,
    ``verified``. Items without a will-executor (backup transactions) are
    left out. Also used by the Building Will dialog (#84b).
    """
    worst = {}
    for w in valid.values():
        url = (w.we or {}).get("url")
        if not url:
            continue
        state = _executor_state(w)
        old = worst.get(url)
        if old is None or _EXECUTOR_ORDER.index(state) < _EXECUTOR_ORDER.index(old):
            worst[url] = state
    return worst


def executor_counts(valid):
    """``{state: number of will-executors}`` for the VALID items ``valid``.

    One entry per executor URL, at its worst copy (:func:`executor_states`).
    """
    counts = {state: 0 for state in _EXECUTOR_ORDER}
    for state in executor_states(valid).values():
        counts[state] += 1
    return counts


def _executors_row(counts):
    """The "Will-Executor" line: who received and verified the will."""
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


def _heirs_row(heirs_count, prepared=True):
    """The "Heirs" line (their number; the names are in the Heirs tab)."""
    text = str(heirs_count)
    if not prepared:
        text += " " + _("(no transaction prepared)")
    return _row(_("Heirs"), text=text)


def will_summary(willitems, heirs_count, will_settings, is_basic_mode, now=None,
                 still_active=None, warn_months=None):
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

    Returns:
        A dict: ``state`` (``"ok"``, ``"to_sign"``, ``"to_send"``,
        ``"not_received"``, ``"none_received"``, ``"check_alive"``,
        ``"expired"``, ``"still_active"``, ``"none"``, ``"executed"``,
        ``"mempool"``, ``"busy"``), ``kind`` (the colour: ``"ok"`` green,
        ``"warn"`` orange, ``"bad"`` red, ``"info"`` and ``"busy"`` grey), ``title``, ``rows`` (see :func:`_row`), ``note``
        (or ``None``), ``action`` (``"check"``, ``"sign_send"``,
        ``"send"``, ``"renew"``, ``"wizard"`` or ``None``) and
        ``action_text`` (its button label). Never raises:
        on an unexpected error it returns ``None`` and the GUI hides the box.
    """
    try:
        return _will_summary(willitems or {}, heirs_count, will_settings or {},
                             is_basic_mode, _now(now), still_active or [],
                             warn_months)
    except Exception:
        return None


def _will_summary(willitems, heirs_count, will_settings, is_basic_mode, now,
                  still_active, warn_months=None):
    """:func:`will_summary` without the safety net (see there)."""
    valid = Will.only_valid_list(willitems)
    if not valid:
        # No will in force. A will already on its way to the heirs is not
        # "missing": say so instead of offering to build a new one.
        if any(w.get_status("CONFIRMED") for w in willitems.values()):
            return _summary("executed", "info", _("Your will has been executed"))
        if any(w.get_status("MEMPOOL") for w in willitems.values()):
            return _summary("mempool", "info", _("Your will is being executed (mempool)"))
        if still_active:
            return _still_active_summary(still_active, heirs_count, now, warn_months)
        invalidated = any(w.get_status("INVALIDATED") for w in willitems.values())
        if invalidated:
            note = _(
                "Your previous will was invalidated on the blockchain: build "
                "and sign a new one, otherwise nobody will receive your bitcoin."
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
    rows.append(_delivery_row(delivery, now, warn_months))
    if expired:
        # The executors can broadcast it now: the Check invalidates it and
        # prepares a new one (same text as the Check Alive message, #79).
        return _summary(
            "expired", "bad", _("The delivery date has passed"), rows=rows,
            note=_(
                "The delivery date of your will ({date}) has already passed: the "
                "will can be executed now. Press Check to invalidate it and "
                "prepare a new one."
            ).format(date=_day(delivery)),
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
            _("Check Alive"), strong=_day(check_date),
            text=relative_date(check_date, now), red=bool(renewal),
        )
        rows.append(check_row)

    counts = executor_counts(valid)
    rows.append(_executors_row(counts))
    rows.append(_heirs_row(heirs_count))

    if renewal:
        if renewal.get("renewable"):
            return _summary(
                "check_alive", "warn",
                _("The Check Alive date has passed: renew your will"),
                rows=rows,
                note=_(
                    "If you do not renew it, your will stays valid and will be "
                    "delivered on {}."
                ).format(_day(delivery)),
                action="renew", action_text=_("Renew"),
            )
        # A fixed delivery date, or settings that would put the renewed
        # Check Alive in the past too: the owner must change the settings
        # first, so there is nothing to press here.
        return _summary(
            "check_alive", "warn", _("The Check Alive date has passed"),
            rows=rows, note=check_alive_cannot_renew(renewal),
        )

    if any(not w.get_status("COMPLETE") for w in valid.values()):
        return _summary(
            "to_sign", "warn", _("Will prepared: it must be signed and sent"),
            rows=rows, note=_previous_will_note(still_active),
            action="sign_send", action_text=_("Sign and send"),
        )
    if counts["to_send"]:
        return _summary(
            "to_send", "warn",
            _("Will signed: it must be sent to the will-executors"),
            rows=rows, note=_previous_will_note(still_active),
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


def _previous_will_note(still_active):
    """The will sent before, still in force until the new one is sent (#84b).

    After a Cancel at signing (or before sending) the executors still hold
    the will sent before, and it can be executed: say so, with its date,
    instead of letting "it must be signed" suggest there is no will. None
    when no sent will can still be executed.
    """
    if not still_active:
        return None
    delivery = min(int(locktime) for _item, locktime, _url in still_active)
    return _(
        "Until you sign and send it, the will sent before stays in force: "
        "delivery on {}."
    ).format(_day(delivery))


def _still_active_summary(still_active, heirs_count, now, warn_months=None):
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
    rows = [_delivery_row(delivery, now, warn_months)]
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
        ).format(_day(delivery)),
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
