"""The inheritance plan report: plain-text pages to save or print (CHANGELOG #92).

Owner's idea, approved with the example of #92: an "old style" ASCII page
that says who receives the bitcoin, how much and when, which will-executors
hold the will and the ids of its transactions. It is built from the same
data as the will summary of the Will tab (:func:`summary.will_summary`) and
the Building Will dialog, so the three always agree.

It never contains secrets: no seed, keys, xpub or passwords, and of the
signed transactions only their ids. It still says who gets what, so it is
marked private at the top.

Layout (owner: multi-page, "Page 1/2"): 78 columns, pages of
:data:`LINES_PER_PAGE` lines. A4 is 297 mm high; with 20 mm margins 257 mm
are left, and a 10 pt monospaced line with 1.2 spacing is 12 pt = 4.23 mm:
60 lines. 78 columns of 10 pt Courier (0.6 em = 2.12 mm) take 165 mm of the
170 mm left between the side margins. Every page ends with "Page n/N"; the
pages after the first start with a short reminder of the title.

Must never import Qt (``bal/core`` rule).
"""

import textwrap
from datetime import datetime
from urllib.parse import urlparse

from ..i18n import N_, _, number_separators
from .heirs import HEIR_REAL_AMOUNT, is_op_return_address
from .messages import _day
from .summary import executor_states, will_summary
from .will import Will

#: Characters per line.
WIDTH = 78
#: Lines per page (A4, 20 mm margins, 10 pt, see the module docstring).
LINES_PER_PAGE = 60
#: Brand name, not translated.
PRODUCT = "SAFE21 HEARTBEAT"
#: Column where the value of a "label ..... value" line starts.
_LABEL = 23
#: Widths of the heirs table columns: share and amount; the name takes the
#: rest of the line after the leading space, so the amounts end at column 78.
_SHARE, _AMOUNT = 10, 22
_NAME = WIDTH - 1 - _SHARE - _AMOUNT

#: Where each will-executor stands (the texts of the Building Will dialog).
_EXECUTOR_TEXTS = {
    "verified": N_("received and verified"),
    "to_verify": N_("received, to be verified"),
    "to_send": N_("to send"),
    "to_sign": N_("to sign"),
    "failed": N_("not received"),
}


# ---------------------------------------------------------------- lines

def _rule(char="-"):
    return char * WIDTH


def _center(text):
    return text.center(WIDTH).rstrip()


def _wrap(text, first=" ", rest=" "):
    """Wrap ``text`` to the page width, keeping its own line breaks."""
    lines = []
    for paragraph in str(text).split("\n"):
        lines += textwrap.wrap(
            paragraph, width=WIDTH, initial_indent=first, subsequent_indent=rest,
            break_long_words=True, break_on_hyphens=False,
        ) or [first.rstrip()]
        first = rest
    return lines


def _dotted(label, value):
    """`` Label ......... value`` (the value wrapped under itself)."""
    head = " {} ".format(label)
    if len(head) < _LABEL:
        head += "." * (_LABEL - len(head))
    else:
        head += ".."
    head += " "
    return _wrap(value, first=head, rest=" " * len(head))


def _percent(pct):
    """``60 %`` / ``6,25 %`` with the decimal separator of the language.

    Two decimals at most (a printed document: 6.25 must not read 6.2).
    """
    text = "{:.2f}".format(pct).rstrip("0").rstrip(".")
    return text.replace(".", number_separators()[0]) + " %"


def _host(url):
    return urlparse(url).hostname or url


def _section(number, title):
    return ["", _rule(), " {}. {}".format(number, title), _rule()]


# ----------------------------------------------------------------- data

def _will_items(willitems, still_active):
    """The will items the report describes.

    The VALID items (the will in force or just prepared); when there is
    none, the sent wills still executable (``still_active``), which the
    will summary reports as "still active" (#84a).
    """
    valid = Will.only_valid_list(willitems or {})
    if valid:
        return list(valid.values())
    return [item for item, _locktime, _url in (still_active or [])]


def heir_rows(willitems, heirs, still_active=None, format_amount=str):
    """The heirs of the will: ``(rows, from_will)``.

    ``rows`` holds dicts ``name``, ``address``, ``share`` (text) and
    ``amount`` (text, or ``None``). From the first will item when there is
    one (every executor copy carries the same heirs; with #91 the copies in
    force are equivalent); otherwise from the Heirs tab, with the shares as
    set there (``from_will`` False). Will-executor fees and OP_RETURN
    messages are left out; an heir below the dust limit is listed as
    excluded.
    """
    items = _will_items(willitems, still_active)
    if items:
        entries = []
        for name, heir in (items[0].heirs or {}).items():
            if str(name)[:9] == 'w!ll3x3c"' or is_op_return_address(str(heir[0])):
                continue
            try:
                sats = int(heir[HEIR_REAL_AMOUNT])
            except (IndexError, TypeError, ValueError):
                sats = None
            entries.append((str(name), str(heir[0]), sats))
        total = sum(s for _n, _a, s in entries if s) or 0
        rows = []
        for name, address, sats in sorted(entries):
            if sats is None:
                rows.append({"name": name, "address": address, "share": "",
                             "amount": _("excluded (below the dust limit)")})
            else:
                rows.append({
                    "name": name, "address": address,
                    "share": _percent(100.0 * sats / total) if total else "",
                    "amount": format_amount(sats),
                })
        return rows, True
    rows = []
    for name, heir in sorted((heirs or {}).items()):
        if str(name)[:9] == 'w!ll3x3c"' or is_op_return_address(str(heir[0])):
            continue
        setting = str(heir[1]).strip()
        if setting.endswith("%"):
            share, amount = setting.replace("%", " %"), None
        else:
            try:
                share, amount = "", format_amount(int(setting))
            except ValueError:
                share, amount = setting, None
        rows.append({"name": str(name), "address": str(heir[0]),
                     "share": share, "amount": amount})
    return rows, False


def executor_rows(willitems, still_active=None):
    """``[(host, text)]``: each will-executor and where it stands."""
    valid = Will.only_valid_list(willitems or {})
    if valid:
        return [(_host(url), _(_EXECUTOR_TEXTS[state]))
                for url, state in sorted(executor_states(valid).items())]
    urls = sorted({url for _i, _l, url in (still_active or []) if url})
    return [(_host(url), _("holds the will sent before")) for url in urls]


def transaction_rows(willitems, still_active=None):
    """``[(holder, date, txid)]`` of the will's transactions."""
    rows = []
    for item in _will_items(willitems, still_active):
        url = (item.we or {}).get("url")
        holder = _host(url) if url else _("no will-executor (backup transaction)")
        rows.append((holder, _day(item.tx.locktime), item._id))
    return sorted(rows)


# --------------------------------------------------------------- report

def report_block_reason(summary, willitems=None):
    """Why the report cannot be made now, or ``None`` when it can.

    Owner's rule (#92): only for a will in force and verified: the green
    "Your will is in force" of the will summary (state ``"ok"``: signed,
    sent, delivery and Check Alive not passed) AND every will-executor of
    ``willitems`` has verified it (the green state also covers "received,
    to be verified"). A will with only a backup transaction (no
    will-executor) is allowed once signed, and the report says that nobody
    else holds it.
    """
    states = executor_states(Will.only_valid_list(willitems or {}))
    verified = all(state == "verified" for state in states.values())
    if summary and summary.get("state") == "ok" and verified:
        return None
    return _(
        "The report is available only when your will is in force and "
        "verified by every will-executor. Press Check."
    )


def inheritance_report(willitems, heirs, will_settings, is_basic_mode, *,
                       format_amount, wallet_name="", plugin_version="",
                       electrum_version="", still_active=None, now=None,
                       created=None):
    """The report as a list of pages, each a list of at most 60 lines.

    ``format_amount(sats)`` gives an amount in the wallet's unit (owner:
    no dollars or euros). ``now`` is the clock of the will summary (tests);
    ``created`` the time printed as "Created on" (default: now, local).
    Use :func:`report_text` to get the text to save.
    """
    summary = will_summary(
        willitems, len(heirs or {}), will_settings, is_basic_mode, now=now,
        still_active=still_active,
    )
    created = created or datetime.now()
    blocks = [_title_block(wallet_name, plugin_version, electrum_version, created)]
    blocks += _status_blocks(summary, will_settings)
    blocks += _heirs_blocks(willitems, heirs, still_active, format_amount)
    blocks += _executor_blocks(willitems, still_active)
    blocks += _how_it_works_blocks()
    blocks += _technical_blocks(willitems, still_active)
    blocks.append(["", _rule("="), _center(_("End of report")), _rule("=")])
    reminder = " {} - {} - {}".format(
        PRODUCT, _("INHERITANCE PLAN REPORT"), created.strftime("%Y-%m-%d")
    )
    return paginate(blocks, reminder[:WIDTH])


def _title_block(wallet_name, plugin_version, electrum_version, created):
    lines = [
        _rule("="),
        _center("{} - {}".format(PRODUCT, _("INHERITANCE PLAN REPORT"))),
        _rule("="),
    ]
    lines += _wrap(_(
        "PRIVATE - It contains no seed, no private keys and no passwords, but "
        "it says who will receive your bitcoin, how much and when. Keep it in "
        "a safe place."
    ))
    lines.append("")
    lines += _dotted(_("Created on"), created.strftime("%Y-%m-%d %H:%M"))
    if wallet_name:
        lines += _dotted(_("Wallet"), wallet_name)
    lines += _dotted(_("Plugin"), "Safe21 Heartbeat {} - Electrum {}".format(
        plugin_version, electrum_version).replace("  ", " "))
    return lines


def _status_blocks(summary, will_settings):
    head = _section(1, _("STATUS"))
    if not summary:
        return [head + _wrap(_("The state of the will could not be read."))]
    lines = head + _wrap(summary["title"])
    hidden = (_("Will-Executor"), _("Heirs"))        # sections 2 and 3 say more
    for row in summary.get("rows") or []:
        if row["label"] in hidden:
            continue
        value = " ".join(p for p in (row.get("strong"), row.get("text")) if p)
        if row.get("alarm"):
            value += " - " + row["alarm"]
        lines += _dotted(row["label"], value)
    fee = (will_settings or {}).get("baltx_fees")
    if fee not in (None, ""):
        lines += _dotted(_("Miner fee"), "{} sat/vB".format(fee))
    blocks = [lines]
    if summary.get("note"):
        blocks.append([""] + _wrap(summary["note"]))
    return blocks


def _heirs_blocks(willitems, heirs, still_active, format_amount):
    rows, from_will = heir_rows(willitems, heirs, still_active, format_amount)
    # "Heir's share", not "Share": Electrum's catalog (which wins, see
    # bal.i18n) translates "Share" as the verb (Italian "Condividi").
    # The titles end where their columns end; a title wider than its column
    # (English "Heir's share") takes room from the name column.
    right = "{:>{s}}{:>{a}}".format(_("Heir's share"), _("Amount"), s=_SHARE, a=_AMOUNT)
    left = " " + _("Name")
    header = left + right.rjust(WIDTH - len(left))
    blocks = [_section(2, _("HEIRS")) + [header.rstrip()]]
    if not rows:
        blocks[0] += _wrap(_("No heir is set."))
        return blocks
    for index, row in enumerate(rows):
        name, share, amount = row["name"], row["share"], row["amount"] or ""
        # Owner: a blank line between one heir and the next (not before the
        # first: it follows the column titles).
        lines = [""] if index else []
        if len(name) > _NAME - 1:
            lines += _wrap(name)
            name = ""
        lines.append(" {:<{n}}{:>{s}}{:>{a}}".format(
            name, share, amount, n=_NAME, s=_SHARE, a=_AMOUNT).rstrip())
        lines += _wrap(_("address: {}").format(row["address"]), first="   ", rest="   ")
        blocks.append(lines)
    if from_will:
        sums = []
        for item in _will_items(willitems, still_active)[:1]:
            for name, heir in (item.heirs or {}).items():
                if str(name)[:9] == 'w!ll3x3c"' or is_op_return_address(str(heir[0])):
                    continue
                try:
                    sums.append(int(heir[HEIR_REAL_AMOUNT]))
                except (IndexError, TypeError, ValueError):
                    pass
        total = format_amount(sum(sums))
        dashes = "-" * min(len(total) + 1, _AMOUNT)
        blocks.append([
            dashes.rjust(WIDTH),
            " {:<{w}}{:>{a}}".format(_("Total to the heirs"), total,
                                     w=WIDTH - 1 - _AMOUNT, a=_AMOUNT).rstrip(),
        ])
    else:
        blocks.append([""] + _wrap(_(
            "No transaction is prepared yet: the shares are the ones set in "
            "the Heirs tab."
        )))
    return blocks


def _executor_blocks(willitems, still_active):
    rows = executor_rows(willitems, still_active)
    head = _section(3, _("WILL-EXECUTORS (each keeps a signed copy of the will)"))
    if not rows:
        # Owner (#92): say plainly what a backup-only will means.
        return [head + _wrap(_("none (backup transaction only)")) + [""] + _wrap(_(
            "No will-executor holds your will: you keep the signed transaction "
            "yourself (backup transaction). Nobody will broadcast it for you: "
            "after the delivery date it must be broadcast by you or by someone "
            "you gave it to."
        ))]
    lines = []
    for host, text in rows:
        left = " {} ".format(host)
        if len(left) < 42:
            left += "." * (42 - len(left))
        lines += _wrap(text, first=left + " ", rest=" " * (len(left) + 1))
    return [head + lines]


def _how_it_works_blocks():
    # Text approved by the owner with the example of #92.
    return [_section(4, _("HOW IT WORKS")) + _wrap(_(
        "As long as you renew the will before the delivery date (moving it "
        "later), your bitcoin stays yours. If you stop, from the delivery "
        "date the will-executors broadcast the transaction already signed "
        "and the heirs receive the amounts shown above."
    ))]


def _technical_blocks(willitems, still_active):
    rows = transaction_rows(willitems, still_active)
    blocks = [_section(5, _("TECHNICAL DETAILS"))]
    if not rows:
        blocks[0] += _wrap(_("No transaction is prepared yet."))
        return blocks
    for holder, day, txid in rows:
        blocks.append([" {}  ({})".format(holder, day)[:WIDTH],
                       "   txid {}".format(txid)])
    return blocks


# ------------------------------------------------------------ pagination

def paginate(blocks, reminder):
    """Lay the blocks out on pages of :data:`LINES_PER_PAGE` lines.

    A block (a heir with its address, a transaction with its id, a section
    title with its first lines) is never split, unless it is longer than a
    page. Every page ends with the footer "PRIVATE ... Page n/N"; the pages
    after the first start with ``reminder`` (the title, short).
    """
    footer_lines = 2
    # A blank line, not a rule: most blocks start with their own rule.
    head = [reminder, ""]
    pages = [[]]
    for block in blocks:
        for piece in _split(block, LINES_PER_PAGE - footer_lines - len(head)):
            page = pages[-1]
            room = LINES_PER_PAGE - footer_lines - len(page)
            if len(piece) > room and page:
                pages.append(list(head))
                page = pages[-1]
                while piece and not piece[0].strip():
                    piece = piece[1:]        # no blank line at a page top
            page.extend(piece)
    total = len(pages)
    out = []
    for number, page in enumerate(pages, 1):
        body = page + [""] * (LINES_PER_PAGE - footer_lines - len(page))
        right = _("Page {}/{}").format(number, total)
        left = " " + _("PRIVATE")
        out.append(body + [_rule(), left + right.rjust(WIDTH - len(left) - 1)])
    return out


def _split(block, size):
    """``block`` in pieces of at most ``size`` lines."""
    return [block[i:i + size] for i in range(0, len(block), size)] or [[]]


def report_text(pages, newline="\r\n"):
    """The pages as one text, every page exactly :data:`LINES_PER_PAGE` lines.

    CRLF by default: the file is meant for Windows' Notepad too.
    """
    return newline.join(line for page in pages for line in page) + newline
