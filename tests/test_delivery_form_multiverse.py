"""The delivery form of a will in several deliveries, the "Multiverse" (#191).

Owner (ZIP test of #190): the PDF report of a will in three deliveries said
"Delivery form: Fixed date" (the settings' form of such a will is "fixed").
Now the PDF and the Will tab's Future view say "Multiverse — 3 deliveries:
<the dates>"; one delivery is unchanged ("Fixed date" / "Period
(renewable) — N months"). The owner's nickname "Multiverse" (IT
"Multiverso", ES "Multiverso") also goes next to the name where the user
meets the form: the wizard's choice "Several deliveries (Multiverse)", the
window "Inheritance in several deliveries (Multiverse)" and its entry in
Tools (checked in ``test_gui_deliveries.py`` and ``test_check_button.py``).

``tests/flowkit.py`` wallets with three fake will-executors, each with its
own fee address (the sessions of ``test_multiverse_chain_rebuild.py``),
the test password, a fixed clock.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_delivery_form_multiverse.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

import test_multiverse_chain_rebuild as chain
from test_multiverse_chain_rebuild import D1, D2, D3

from bal.core import delivery
from bal.core import report as core_report
from bal.core.future import delivery_form_text
from bal.gui.qt import future_view as fv
from bal.gui.qt import report_pages as rp

make = chain.make

MULTIVERSE = "Multiverse — 3 deliveries: 1 Mar 2030, 1 Mar 2033, 1 Mar 2036"


def test_the_text_of_each_form():
    """More than one date: the Multiverse with the dates; one date or none:
    the form of the settings, as before."""
    assert delivery_form_text(delivery.FORM_FIXED, None, [D1, D2, D3]) == MULTIVERSE
    assert delivery_form_text(delivery.FORM_FIXED, None, [D1]) == "Fixed date"
    assert delivery_form_text(delivery.FORM_FIXED) == "Fixed date"
    assert delivery_form_text(delivery.FORM_PERIOD, 24, [D1]) == (
        "Period (renewable) — 24 months (2 years)")


def test_the_will_tab_says_multiverse(make, monkeypatch):
    """The Future view of a will in three deliveries: the Multiverse line
    right under the delivery, no "Fixed date"."""
    s = make()
    chain._first(s, "three_dates")
    monkeypatch.setattr(fv.FutureView, "_now", lambda self: s.clock.now)
    view = fv.FutureView(s.bw)
    view.resize(900, 500)
    view.show()
    view.refresh()
    texts = view.texts()
    line = "Delivery form: " + MULTIVERSE
    assert line in texts
    assert texts.index(line) == 1 + next(
        i for i, t in enumerate(texts) if t.startswith("Delivery to the heirs:"))
    assert "Delivery form: Fixed date" not in texts


def test_the_pdf_report_says_multiverse(make):
    """The PDF report's state box: "Delivery form: Multiverse — ..."."""
    s = make()
    chain._first(s, "three_dates")
    report = core_report.will_report(
        s.bw.willitems, s.bw.heirs, s.bw.will_settings, s.plugin.is_basic_mode(),
        wallet=s.wallet, wallet_name="wallet_test", plugin_version="0.8.0",
        electrum_version="4.8.2", now=s.clock.now,
        still_active=s.bw._still_active_for_summary(),
        selected=s.bw.selected_willexecutors())
    texts = " ".join(t for page in rp.layout_report(report, str)
                     for t in rp.page_texts(page))
    assert MULTIVERSE in texts
    assert "Fixed date" not in texts

