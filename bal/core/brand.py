"""
bal.core.brand
==============

The brand of the plugin (#163, kit ``ancora-brand_1``): its name, its slogan
and the two colours of the logo.

The name is a brand: it is the same in every language and keeps its accents
wherever they can be shown. It is therefore never put inside a text to
translate; the texts take it through ``{}`` (``_("... {} ...").format(NAME)``),
so no translation can change it. The slogan is a play on words that only works
in Italian, so it is shown in Italian in every language (owner, 2026-10-18).

The internal identity (package ``bal``, ``bal_*`` keys, the history labels
stored in the wallet) does not change here: that is Phase 6.
"""

#: The name shown to the user (titles, messages, README).
NAME = "Àncòra"
#: The name in Electrum's plugin list (``manifest.json`` ``fullname``).
FULLNAME = NAME + " (Safe21)"
#: The name without accents, where accented letters may be read wrongly
#: (file names, the ``PRODID`` of the calendar file).
NAME_ASCII = "Ancora"
#: The slogan, in Italian in every language: "àncora" (anchor) for you,
#: "ancòra" (still) for them.
SLOGAN = "Àncora per te, ancòra per loro."

#: The colours of the logo, used only to draw the logo itself; the colours of
#: the plugin's interface do not change (owner, 2026-10-18). #199: the ink
#: and the teal of the accents of the new logo, as they are in
#: ``assets/brand/svg/ancora-lockup.svg``.
INK = "#0D0E12"
WATER = "#1DAA9B"
