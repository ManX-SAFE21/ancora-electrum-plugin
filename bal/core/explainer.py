"""The animated explainer "How BAL PROTOCOL works": story and scene (CHANGELOG #162).

The plugin shows, in a window of its own, the same 17-step animation the
owner approved as a web page (``safe21-heartbeat/explainer``): the
blockchain, your computer, three will-executors with their rows of copies,
the heirs and a time line, and what happens to them step by step (the
copies, the fee in every copy, a routine update one day earlier, the
postponing with an invalidation, the delivery, one accepted copy, the
others refused, the bitcoin to the heirs, the fee to the will-executor,
one will-executor is enough).

This module has no Qt: it holds the texts (English msgids, translated by
the GUI), the live data (years, an estimate of the block height) and
:func:`scene`, a pure function that says where every element is and how
visible it is at a given second of a step. The Qt side
(``bal/gui/qt/explainer.py``) only paints what :func:`scene` returns, so
any frame of any step can be tested without a screen.

Display only: nothing here builds, signs, sends or changes a date.
"""

from datetime import datetime, timezone

from ..i18n import N_

# ---------------------------------------------------------------------------
# The 17 steps: (title, text, small note). ``{}`` in the texts of steps 9
# and 10 is the new delivery year (filled after translating).
STEPS = (
    (N_("Write your will"),
     N_("In the plugin you write who inherits your bitcoin and from which "
        "date. Your bitcoin are recorded on the blockchain, at your address, "
        "and they stay yours."), ""),
    (N_("The plugin prepares the copies"),
     N_("The plugin prepares one signed transaction for each will-executor "
        "you chose: it moves the bitcoin to your heirs' addresses, but only "
        "from the delivery date (locktime). Before that date the Bitcoin "
        "network refuses it."), ""),
    (N_("The copies go to the will-executors"),
     N_("Each will-executor receives and keeps its copy. It does not have "
        "your keys: it can neither change the copy nor spend your bitcoin."),
     ""),
    (N_("Each copy carries its fee"),
     N_("The copies are not all the same: each one contains a small fee for "
        "its will-executor, which is its pay. The fee is taken from the "
        "inheritance only on the delivery day, when the copy is accepted by "
        "the network."), ""),
    (N_("As long as you are here"),
     N_("Your bitcoin stay at your address and you use them as always. Until "
        "the delivery date, the will-executors send nothing and no bitcoin "
        "leaves your address."), ""),
    (N_("The amount changes"),
     N_("If the wallet amount changes, the plugin prepares a new copy for "
        "each will-executor, with the delivery date one day earlier than the "
        "inheritance date, because the old inheritance is no longer valid. "
        "This mechanism, invented by Bitcoin After Life, saves costly "
        "Bitcoin network fees at every change."), ""),
    (N_("The heirs change"),
     N_("The same happens if the heirs change: another copy, one more day "
        "earlier, is added on the right. The old ones stay where they are, "
        "but they will not be used: on delivery only the newest one is "
        "sent."), ""),
    (N_("Postponing the date: the copies are invalidated"),
     N_("To move the delivery later, the plugin invalidates the old copies "
        "with a small transaction on the blockchain (you only pay the "
        "network fee). The old copies stay where they are, but they are no "
        "longer valid: we mark them in red."), ""),
    (N_("Postponing the date: new copies"),
     N_("Then the plugin prepares new copies with the new date, {}, and "
        "sends them to the will-executors. The newest one, the one furthest "
        "right, is the only one that counts."), ""),
    (N_("The date arrives"),
     N_("The delivery date arrives: {}. From that day the newest copy can "
        "be spent."), ""),
    (N_("The will-executors send"),
     N_("All the will-executors send their newest copy, the one furthest "
        "right, to the Bitcoin network."), ""),
    (N_("The block accepts one copy"),
     N_("The copies spend the same bitcoin, so the new block accepts only "
        "one of them, usually the first to arrive."), ""),
    (N_("The others are refused"),
     N_("The other copies are refused, because those bitcoin have already "
        "been spent."), ""),
    (N_("The bitcoin go to the heirs"),
     N_("The bitcoin move from your address to your heirs' addresses, as set "
        "in the will."), ""),
    (N_("The fee goes to the will-executor"),
     N_("The fee, a small part of the inheritance written in the same "
        "transaction, goes to the will-executor whose copy was accepted: it "
        "is its pay for keeping and sending the will."), ""),
    (N_("A question"),
     N_("What happens if almost all the will-executors stop working? We "
        "will see it in the next step."), ""),
    (N_("One is enough"),
     N_("Even if two will-executors are off or gone, one that works is "
        "enough: it sends its copy, the network accepts it, the heirs "
        "receive the bitcoin and that will-executor collects its fee. This "
        "is why the plugin uses more than one."),
     N_("Note: for extreme cases you can save the transaction for the heirs "
        "without using will-executors, available in Advanced Mode.")),
)
STEP_COUNT = len(STEPS)
#: The step covered by the question card (no drawing under it).
QUESTION_STEP = 15
#: Steps whose text holds the new delivery year.
YEAR_STEPS = (8, 9)

# Words drawn in the scene (English msgids; the GUI translates them).
LABELS = {
    "title": N_("How BAL PROTOCOL works"),
    "button": N_("How will-executors work"),
    "chain": N_("Bitcoin blockchain"),
    "accepted": N_("accepted"),
    "off": N_("off"),
    "refused": N_("copies refused"),
    "spent": N_("those bitcoin are already spent"),
    "today": N_("today"),
    "delivery": N_("delivery"),
    "alive": N_("still here"),   # not "you are here": the #161 step bar has it (another Italian)
    "sheet": N_("Will"),
    "copy": N_("copy {}"),
    "amount": N_("wallet amount"),
    "new_copy": N_("new copy"),
    "heirs_small": N_("heirs"),
    "due_date": N_("delivery date"),
    "no_beat": N_("no heartbeat"),
    "you": N_("You"),
    "your_address": N_("your address"),
    "heirs": N_("Heirs"),
    "heir_address": N_("address of heir {}"),
    "fee": N_("+ fee"),
    "tip_note_1": N_("each copy carries its fee"),
    "tip_note_2": N_("paid only on delivery"),
    "amount_changes": N_("the amount changes"),
    "heirs_change": N_("the heirs change"),
    "day_before": N_("one day earlier"),
    "day_before_2": N_("another day earlier"),
    "invalidated": N_("copies invalidated"),
    "question": N_("What if most of the WILL-EXECUTORS disappear?"),
    "step_of": N_("Step {} of {}"),
    "back": N_("Previous step"),
    "next": N_("Next step"),
    "play": N_("Play from this step"),
    "pause": N_("Pause"),
    "restart": N_("Play from the start"),
}

# Example addresses (invented, shortened like a wallet shows them).
ADDRESS_YOU = "bc1qk3rn…7m7h"
ADDRESS_HEIR_1 = "bc1qv9ef…3nks"
ADDRESS_HEIR_2_OLD = "bc1qm5ht…9xk4"
ADDRESS_HEIR_2_NEW = "bc1qe2yd…4wd7"

# ---------------------------------------------------------------------------
# Live data.
#: Height of the chain tip read from mempool.space on 2026-10-18 13:04 UTC,
#: and that moment (Unix time). The owner chose an ESTIMATE (never a network
#: request, also when Electrum is online): about 144 blocks a day since then.
TIP0 = 971998
T0 = 1792328640


def years(now=None):
    """(this year, delivery year, postponed year): this year + 0, 3, 7.

    The time line shows this year to this year + 7; every new year the
    whole story moves by one year with no update of the plugin.
    """
    now = now or datetime.now()
    y = now.year
    return y, y + 3, y + 7


def tip_estimate(now=None):
    """The estimated height of the chain tip: :data:`TIP0` plus 144 blocks
    a day since :data:`T0` (never lower than :data:`TIP0`)."""
    now = now or datetime.now(timezone.utc)
    seconds = now.timestamp() - T0
    return TIP0 + max(0, int(144 * seconds / 86400))


# ---------------------------------------------------------------------------
# Geometry of the scene (logical units; the GUI scales it to its window).
WIDTH, HEIGHT = 1030.0, 516.0
K, X0, BLK = 8, 40.0, 84.0          # blocks before the next block; first x; block step
DIVIDER_X = 700.0
NEXT_BLOCK_X = 712.0
SEPARATORS = (318.0, 754.0)
SX = 410.0                           # x of the will-executors' servers


def row_y(i):
    """Top of the server of will-executor ``i`` (0, 1, 2)."""
    return 140.0 + 90.0 * i


def home(i):
    """Where the copies for will-executor ``i`` come out of the computer."""
    return (210.0, 160.0 + 40.0 * i)


def place(i, k):
    """Place ``k`` (0 = oldest, left ... 3 = newest, right) of the row of
    copies of will-executor ``i``."""
    return (584.0 + 38.0 * k, row_y(i) - 4.0)


BLOCK = (718.0, 33.0)                       # a copy inside the next block
NEAR = ((790.0, 100.0), (850.0, 100.0))     # where a refused copy stops
COIN_YOU = ((184.0, 376.0), (206.0, 384.0))
COIN_HEIRS = ((965.0, 322.0), (965.0, 380.0))
FEE_START = (972.0, 70.0)
TODAY_X = 96.0
DUE_X = (456.0, 936.0)                      # this year + 3, this year + 7
DAY = 5.0                                   # "one day earlier" on the axis

#: The steps where time really passes: the block strip slides one block.
SLIDING_STEPS = (4, 5, 6, 8, 9, 11)

# Times (seconds).
MOVE = 1.56          # a movement
FADE = 1.08          # a fade


def slide(n):
    """How many blocks the strip has slid at step ``n``."""
    return sum(1 for s in SLIDING_STEPS if s <= n)


def ease(u):
    """The movement curve of the web page (CSS ``cubic-bezier(.45,.05,.2,1)``)."""
    if u <= 0:
        return 0.0
    if u >= 1:
        return 1.0
    x1, y1, x2, y2 = 0.45, 0.05, 0.2, 1.0
    lo, hi = 0.0, 1.0
    s = u
    for _ in range(30):        # find the curve parameter s with x(s) = u
        s = (lo + hi) / 2
        x = 3 * (1 - s) ** 2 * s * x1 + 3 * (1 - s) * s * s * x2 + s ** 3
        if x < u:
            lo = s
        else:
            hi = s
    return 3 * (1 - s) ** 2 * s * y1 + 3 * (1 - s) * s * s * y2 + s ** 3


def _smooth(u):
    """The fade curve (CSS ``ease``, close enough)."""
    u = min(1.0, max(0.0, u))
    return u * u * (3 - 2 * u)


def fade(t, start, out_at=None):
    """Opacity of an element that appears at ``start`` (and goes away from
    ``out_at``)."""
    a = _smooth((t - start) / FADE)
    if out_at is not None and t >= out_at:
        a *= 1 - _smooth((t - out_at) / FADE)
    return a


def move(t, start, p0, p1):
    """A point moving from ``p0`` to ``p1`` from second ``start``."""
    e = ease((t - start) / MOVE)
    return (p0[0] + (p1[0] - p0[0]) * e, p0[1] + (p1[1] - p0[1]) * e)


def track(t, value, keys):
    """A value that moves to each ``(second, target)`` of ``keys`` in turn,
    every time from where it is (like a CSS transition started again)."""
    for i, (start, target) in enumerate(keys):
        if t < start:
            break
        end = keys[i + 1][0] if i + 1 < len(keys) else None
        at = t if end is None or t < end else end
        e = ease((at - start) / MOVE)
        value = value + (target - value) * e
    return value


# ---------------------------------------------------------------------------
def step_targets(n):
    """Where the moving parts must arrive at step ``n``: (strip offset,
    delivery x, today x). The GUI keeps the values shown when the step
    starts, so they glide from there (also after a jump to any step)."""
    if n >= 9:
        due = DUE_X[1]
    elif n == 6:
        due = DUE_X[0] - DAY
    elif n in (7, 8):
        due = DUE_X[0] - 2 * DAY
    else:
        due = DUE_X[0]
    today = DUE_X[1] if n >= 9 else TODAY_X
    return BLK * slide(n), due, today


def _due_keys(n):
    """The delivery marker's moves inside step ``n`` (after the first one)."""
    if n == 5:
        return [(1.8, DUE_X[0] - DAY)]
    if n == 6:
        return [(1.7, DUE_X[0] - 2 * DAY)]
    if n == 8:
        return [(1.2, DUE_X[1])]
    return []


def scene(n, t, start=None):
    """Everything the GUI draws at second ``t`` of step ``n`` (0..16).

    ``start``: (strip offset, delivery x, today x) shown when the step
    began (default: the targets of the step before, as when the steps run
    in order). Returns a dict of plain values (positions in logical units,
    opacities 0..1, texts as label keys).
    """
    targets = step_targets(n)
    if start is None:
        start = step_targets(n - 1) if n > 0 else targets
    s = {"step": n, "t": t}
    s["chain_offset"] = track(t, start[0], [(0.0, targets[0])])
    s["next_block_index"] = K + slide(n)
    s["due_x"] = track(t, start[1], [(0.0, targets[1])] + _due_keys(n))
    s["today_x"] = track(t, start[2], [(0.0, targets[2])])
    s["due_year"] = 2 if (n >= 9 or (n == 8 and t >= 1.2)) else 1
    s["today_label"] = 0.0 if n >= 9 else 1.0
    s["due_cross"] = fade(t, 0.9) if n == 7 else 0.0

    # The copies: (layer k, will-executor i) -> (x, y, opacity, red mark).
    envs = {}
    tears = []

    def put(k, i, p, a=1.0, bad=False):
        envs[(k, i)] = (p[0], p[1], a, 1.0 if bad else 0.0)

    if n == 1:
        for i in range(3):
            put(0, i, home(i), fade(t, 0.5 + 0.4 * i))
    elif n == 2:
        for i in range(3):
            put(0, i, move(t, 0.8, home(i), place(i, 0)))
    elif n in (3, 4):
        for i in range(3):
            put(0, i, place(i, 0))
    elif n in (5, 6):
        new = n - 4                       # the layer that arrives
        for i in range(3):
            for k in range(new):
                put(k, i, place(i, k))
            put(new, i, move(t, 1.7, home(i), place(i, new)), fade(t, 1.4))
    elif n == 7:
        for i in range(3):
            for k in range(3):
                put(k, i, place(i, k), bad=t >= 0.9)
    elif n == 8:
        for i in range(3):
            for k in range(3):
                put(k, i, place(i, k), bad=True)
            put(3, i, move(t, 1.3, home(i), place(i, 3)), fade(t, 1.0))
    elif 9 <= n <= 13:
        targets3 = (BLOCK, NEAR[0], NEAR[1])
        for i in range(3):
            for k in range(3):
                a = 1.0 - (_smooth((t - 3.2) / FADE) if n == 13 else 0.0)
                put(k, i, place(i, k), a, bad=True)
            if n == 9:
                put(3, i, place(i, 3))
            elif n == 10:
                put(3, i, move(t, 0.8 + 0.3 * i, place(i, 3), targets3[i]))
            elif n == 11:
                a = 1.0 - _smooth((t - 0.9) / FADE) if i == 0 else 1.0
                put(3, i, targets3[i], a)
            elif n == 12 and i > 0 and t < 0.7:
                put(3, i, targets3[i])
        if n == 12:
            tears = [(NEAR[0], 0.7), (NEAR[1], 0.7)]
    elif n == 16:
        for i in range(3):
            a = 0.25 if i < 2 else 1.0
            p = place(i, 0)
            if i == 2:
                p = move(t, 0.78, p, BLOCK)
                a = 1.0 - _smooth((t - 2.6) / FADE)
            put(3, i, p, a)
    s["envelopes"] = envs
    s["tears"] = tears
    s["tips"] = (fade(t, 0.0) if n == 3 else 1.0) if n >= 3 else 0.0

    # The accepted block, the notes near the blockchain.
    s["new_block"] = {11: fade(t, 0.9), 16: fade(t, 2.6)}.get(
        n, 1.0 if n in (12, 13, 14) else 0.0)
    s["refused"] = fade(t, 0.7) if n == 12 else 0.0
    s["invalidated"] = fade(t, 0.9) if n == 7 else 0.0
    s["tip_note"] = fade(t, 0.9) if n == 3 else 0.0
    s["change_tag"] = (("amount_changes" if n == 5 else "heirs_change"),
                       fade(t, 0.7, 6.0)) if n in (5, 6) else (None, 0.0)
    s["day_tag"] = {5: ("day_before", fade(t, 1.8, 6.0)),
                    6: ("day_before_2", fade(t, 1.7, 6.0))}.get(n, (None, 0.0))

    # Heir 2 changes address at step 7 (index 6): its box flashes orange.
    s["heir2_new"] = n >= 7 or (n == 6 and t >= 1.7)
    s["heir2_flash"] = n == 6 and 1.7 <= t < 4.5

    # The funds and the fee.
    if n == 13:
        coins = [move(t, 1.2, COIN_YOU[j], COIN_HEIRS[j]) for j in range(2)]
    elif n == 14:
        coins = list(COIN_HEIRS)
    elif n == 16:
        coins = [move(t, 4.9, COIN_YOU[j], COIN_HEIRS[j]) for j in range(2)]
    else:
        coins = list(COIN_YOU)
    s["coins"] = coins
    s["transaction"] = {13: fade(t, 0.6), 14: 1.0, 16: fade(t, 4.3)}.get(n, 0.0)
    s["fee"] = None
    s["fee_tag"] = (None, 0.0)
    if n in (14, 16):
        row = 0 if n == 14 else 2
        appear, go, tag = (0.4, 1.1, 2.7) if n == 14 else (4.5, 4.9, 6.3)
        target = (SX + 75.0, row_y(row) + 13.0)
        s["fee"] = (move(t, go, FEE_START, target), fade(t, appear))
        s["fee_tag"] = (row, fade(t, tag))

    # The will-executors switched off in the last step.
    s["off"] = (0, 1) if n == 16 else ()

    # The computer's screen.
    if n == 0:
        screen = "write"
    elif n >= 9:
        screen = "flat"
    else:
        screen = ("write", "copies", "send", "tips", "beat", "amount",
                  "heirs", "invalid", "calendar")[n]
    s["screen"] = (screen, fade(t, 0.0))
    s["amount_bar"] = 0.45 + 0.40 * _smooth((t - 1.8) / 1.5) if n == 5 else 0.45
    s["screen_new"] = fade(t, 1.8 if n == 5 else 1.7) if n in (5, 6) else 0.0
    s["calendar_year"] = 2 if (n == 8 and t >= 1.2) else 1
    s["question"] = fade(t, 0.0) if n == QUESTION_STEP else 0.0
    return s


def step_seconds(title, text, n):
    """How long autoplay stays on a step: comfortable reading (4.4 words a
    second) plus 2 seconds, never under 9 seconds (6 for the question)."""
    words = len((title + " " + text).split())
    return max(6.0 if n == QUESTION_STEP else 9.0, words / 4.4 + 2.0)
