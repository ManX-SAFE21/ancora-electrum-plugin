"""The animated explainer "How BAL PROTOCOL works": story and scene (CHANGELOG #162, #226).

The plugin shows, in a window of its own, the SIMPLE version of the
animation the owner approved as a web page (the green
``come-funziona-bal-protocol-semplice.html`` of ``safe21-ancora/explainer``,
#226: the owner keeps only the simple one): 8 steps, the blockchain, your
computer, three will-executors with one copy each, two heirs and a time
line. The will is written, one copy for each will-executor, each keeps its
copy with its fee, nothing leaves while you are here, the delivery date
arrives, the copies are sent, the block accepts the first one, the bitcoin
go to the heirs and the fee to its will-executor, then the other copies
are refused.

This module has no Qt: it holds the texts (English msgids, translated by
the GUI; the same words as the web page), the live data (years, an
estimate of the block height) and :func:`scene`, a pure function that says
where every element is and how visible it is at a given second of a step.
The Qt side (``bal/gui/qt/explainer.py``) only paints what :func:`scene`
returns, so any frame of any step can be tested without a screen.

Display only: nothing here builds, signs, sends or changes a date.
"""

from datetime import datetime, timezone

from ..i18n import N_

# ---------------------------------------------------------------------------
# The 8 steps: (title, text), the English of the web page (its Italian and
# Spanish are in the catalogs). The tool is called "software", as on the
# page (owner, 2026-10-20: neutral, clear to non-technical viewers).
STEPS = (
    (N_("Write your will: choose the heirs and the date"),
     N_("In the software you write who inherits your bitcoin and from which "
        "date. Your bitcoin are recorded on the blockchain, at your address, "
        "and they stay yours.")),
    (N_("One copy for each will-executor, valid from the date you chose"),
     N_("Today the software signs one copy for each will-executor. It is "
        "like a post-dated cheque: it takes the bitcoin to your heirs, but "
        "the network accepts it only from the date you chose.")),
    (N_("Each will-executor keeps a copy, with its own fee"),
     N_("Each will-executor receives and keeps its copy. It does not have "
        "your keys: it can neither change the copy nor spend your bitcoin. "
        "Each copy contains a small fee, its pay, paid only on the delivery "
        "day.")),
    (N_("While you are here, nothing is sent: the bitcoin stay yours"),
     N_("Your bitcoin stay at your address and you use them as always. Until "
        "the delivery date, the will-executors send nothing and no bitcoin "
        "leaves your address.")),
    (N_("The delivery date arrives"),
     N_("The calendar reaches the date you chose. From that day the copy can "
        "be spent.")),
    (N_("On the chosen date, the copies are sent"),
     N_("Each will-executor takes its signed copy and sends it to the "
        "Bitcoin network, without needing you.")),
    (N_("The block accepts one copy only: the one sent first"),
     N_("The copies spend the same bitcoin, so the new block accepts only one "
        "of them, usually the one sent first. The other two wait in a row "
        "below the block.")),
    (N_("Bitcoin to the heirs, fee to the will-executor; then the others lapse"),
     N_("As soon as the copy is accepted, the bitcoin move from your address "
        "to your heirs' addresses, as set in the will, and a small fee goes "
        "to the will-executor whose copy was accepted. Only afterwards are "
        "the other two copies invalidated, because those bitcoin have "
        "already been spent.")),
)
STEP_COUNT = len(STEPS)

# Words drawn in the scene (English msgids; the GUI translates them).
LABELS = {
    "title": N_("How BAL PROTOCOL works"),
    "button": N_("How will-executors work"),
    "chain": N_("Bitcoin blockchain"),
    "accepted": N_("accepted"),
    "refused": N_("copies refused"),
    "spent": N_("those bitcoin are already spent"),
    "today": N_("today"),
    "delivery": N_("delivery"),
    "alive": N_("still here"),   # not "you are here": the #161 step bar has it (another Italian)
    "sheet": N_("Will"),
    "copy": N_("copy {}"),
    "no_beat": N_("no heartbeat"),
    "you": N_("You"),
    "your_address": N_("your address"),
    "heirs": N_("Heirs"),
    "heir_address": N_("address of heir {}"),
    "fee": N_("+ fee"),
    "tip_note_1": N_("each copy carries its fee"),
    "tip_note_2": N_("paid only on delivery"),
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
ADDRESS_HEIR_2 = "bc1qm5ht…9xk4"

# ---------------------------------------------------------------------------
# Live data.
#: Height of the chain tip read from mempool.space on 2026-10-18 13:04 UTC,
#: and that moment (Unix time). The owner chose an ESTIMATE (never a network
#: request, also when Electrum is online): about 144 blocks a day since then.
TIP0 = 971998
T0 = 1792328640


def years(now=None):
    """(this year, delivery year): this year + 0 and + 3.

    The time line shows this year to this year + 7; every new year the
    whole story moves by one year with no update of the plugin.
    """
    now = now or datetime.now()
    y = now.year
    return y, y + 3


def tip_estimate(now=None):
    """The estimated height of the chain tip: :data:`TIP0` plus 144 blocks
    a day since :data:`T0` (never lower than :data:`TIP0`)."""
    now = now or datetime.now(timezone.utc)
    seconds = now.timestamp() - T0
    return TIP0 + max(0, int(144 * seconds / 86400))


# ---------------------------------------------------------------------------
# Geometry of the scene (logical units of the web page's drawing; the GUI
# scales it to its window).
WIDTH, HEIGHT = 1030.0, 516.0
K, X0, BLK = 8, 40.0, 84.0          # blocks before the next block; first x; block step
DIVIDER_X = 700.0
NEXT_BLOCK_X = 712.0
SEPARATORS = (270.0, 754.0)
SX = 370.0                           # x of the will-executors' servers


def row_y(i):
    """Top of the server of will-executor ``i`` (0, 1, 2)."""
    return 140.0 + 90.0 * i


def home(i):
    """Where the copy for will-executor ``i`` comes out of the computer."""
    return (210.0, 160.0 + 40.0 * i)


def place(i):
    """Where the copy of will-executor ``i`` lies, at the right of its
    server."""
    return (SX + 174.0, row_y(i) - 4.0)


BLOCK = (718.0, 33.0)                       # the copy inside the next block
#: Where the other two copies wait: in a column right under the block.
NEAR = ((718.0, 104.0), (718.0, 140.0))
COIN_YOU = ((184.0, 376.0), (206.0, 384.0))
COIN_HEIRS = ((965.0, 277.0), (965.0, 325.0))
FEE_START = (195.0, 396.0)                  # the fee leaves from your address
TODAY_X = 96.0
DUE_X = 456.0                               # this year + 3

#: Who sends first, second, third (will-executor 2, then 1, then 3): the
#: copy of will-executor 2 is the one the block accepts (the winner is not
#: simply the nearest one).
SENDERS = (1, 0, 2)
WINNER = SENDERS[0]

#: How many blocks the strip has slid at each step: it moves only where
#: time really passes in the story (as on the web page, whose simple story
#: is steps 1, 2, 3+4, 5, 10, 11, 12 and 13 of the full one).
SLIDES = (0, 0, 0, 1, 5, 5, 6, 6)

# Times (seconds).
MOVE = 1.56          # a movement
FADE = 1.08          # a fade
SEND_AT = (0.8, 1.7, 2.6)   # step 6: when the first, second, third copy leaves
TEAR_AT = 5.2               # step 8: when the other two copies are refused


def slide(n):
    """How many blocks the strip has slid at step ``n``."""
    return SLIDES[n]


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


def track(t, value, target):
    """A value that glides from ``value`` to ``target`` from second 0 (like
    a CSS transition)."""
    return value + (target - value) * ease(t / MOVE)


# ---------------------------------------------------------------------------
def step_targets(n):
    """Where the moving parts must arrive at step ``n``: (strip offset,
    today x). The GUI keeps the values shown when the step starts, so they
    glide from there (also after a jump to any step)."""
    return BLK * slide(n), DUE_X if n >= 4 else TODAY_X


def _waiting(k):
    """Where the ``k``-th copy to be sent stops: the block for the first,
    under it for the others."""
    return BLOCK if k == 0 else NEAR[k - 1]


def scene(n, t, start=None):
    """Everything the GUI draws at second ``t`` of step ``n`` (0..7).

    ``start``: (strip offset, today x) shown when the step began (default:
    the targets of the step before, as when the steps run in order).
    Returns a dict of plain values (positions in logical units, opacities
    0..1, texts as label keys).
    """
    targets = step_targets(n)
    if start is None:
        start = step_targets(n - 1) if n > 0 else targets
    s = {"step": n, "t": t}
    s["chain_offset"] = track(t, start[0], targets[0])
    s["next_block_index"] = K + slide(n)
    s["due_x"] = DUE_X
    s["today_x"] = track(t, start[1], targets[1])
    s["today_label"] = 0.0 if n >= 4 else 1.0

    # The copies: will-executor i -> (x, y, opacity); one copy each.
    envs = {}
    tears = []
    if n == 1:
        for i in range(3):
            envs[i] = (*home(i), fade(t, 0.5 + 0.4 * i))
    elif n == 2:
        for i in range(3):
            envs[i] = (*move(t, 0.8, home(i), place(i)), 1.0)
    elif n in (3, 4):
        for i in range(3):
            envs[i] = (*place(i), 1.0)
    elif n == 5:
        for k, i in enumerate(SENDERS):
            envs[i] = (*move(t, SEND_AT[k], place(i), _waiting(k)), 1.0)
    elif n in (6, 7):
        for k, i in enumerate(SENDERS):
            if n == 7 and k > 0 and t >= TEAR_AT:
                continue            # replaced by its two torn halves
            envs[i] = (*_waiting(k), 1.0)
        if n == 7:
            tears = [(NEAR[0], TEAR_AT), (NEAR[1], TEAR_AT)]
    s["envelopes"] = envs
    s["tears"] = tears
    s["tips"] = fade(t, 2.5) if n == 2 else (1.0 if n >= 3 else 0.0)

    # The medals 1 / 2 / 3 (gold, silver, bronze): will-executor -> (rank,
    # opacity), shown when its copy reaches the network; they stay on.
    medals = {}
    if n >= 5:
        for k, i in enumerate(SENDERS):
            medals[i] = (k, fade(t, SEND_AT[k] + MOVE) if n == 5 else 1.0)
    s["medals"] = medals

    # The accepted block and the notes near the blockchain.
    s["new_block"] = fade(t, 0.9) if n == 6 else (1.0 if n == 7 else 0.0)
    s["refused"] = fade(t, TEAR_AT) if n == 7 else 0.0
    s["tip_note"] = fade(t, 3.2) if n == 2 else 0.0

    # The funds, the fee and the joy (step 8).
    if n == 7:
        s["coins"] = [move(t, 1.2, COIN_YOU[j], COIN_HEIRS[j]) for j in range(2)]
        target = (SX + 75.0, row_y(WINNER) + 13.0)
        s["fee"] = (move(t, 1.2, FEE_START, target), fade(t, 1.0))
        s["fee_tag"] = (WINNER, fade(t, 2.8))
        s["joy"] = fade(t, 2.8)
        s["transaction"] = fade(t, 0.6)
    else:
        s["coins"] = list(COIN_YOU)
        s["fee"] = None
        s["fee_tag"] = (None, 0.0)
        s["joy"] = 0.0
        s["transaction"] = 0.0

    # The computer's screen: (picture, opacity) pairs, more than one while
    # a picture fades into the next (step 3: sending, then the fees).
    if n == 0:
        screens = [("write", 1.0)]
    elif n == 1:
        screens = [("copies", 1.0)]
    elif n == 2:
        screens = [("send", 1.0 - _smooth((t - 2.5) / FADE)), ("tips", fade(t, 2.5))]
    elif n == 3:
        screens = [("beat", 1.0)]
    else:
        screens = [("flat", 1.0)]
    s["screens"] = screens
    return s


#: Autoplay pace: the web page's reading times halved (owner, 2026-10-19).
PACE = 0.5


def step_seconds(title, text):
    """How long autoplay stays on a step, as on the web page: attentive
    reading (3 words a second) plus 2.5 seconds, never under 9 seconds,
    all halved (:data:`PACE`)."""
    words = len((title + " " + text).split())
    return PACE * max(9.0, words / 3 + 2.5)
