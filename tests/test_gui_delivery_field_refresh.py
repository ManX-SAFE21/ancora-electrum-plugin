"""The delivery field shows the date of the will in force (CHANGELOG #121).

Found in the owner's ZIP test of #120: in BASIC mode the delivery field of
the Will tab showed a date one day later than the will (also after a
restart), while the will summary and the wizard showed the will's date.
His delivery setting was a period ("1054d", saved in ADVANCED mode). In
BASIC mode the field shows a period as the date it gives: the date of the
will in force, or "today + the period" with no will. The fields of the Will
and Heirs tabs are built when the window opens, BEFORE the will is loaded,
so they showed "today + the period" and were never redrawn: the next day,
or after a rebuild that moved the will one day earlier, the field and the
will disagreed.

Now ``LockTimeWidget.refresh_shown_value`` shows a period again, and the
toolbars call it each time their list is redrawn
(``WillSettingsWidget.on_locktime_change``: after the will is loaded, after
each Check). It only shows: the saved setting never changes. A fixed date
and the ADVANCED Raw editor ("1054d") are shown as before.

Run (standalone or with pytest):
    QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src \\
        python3 -m pytest tests/test_gui_delivery_field_refresh.py
"""

import os
import sys
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

import flowkit as fk
from electrum.transaction import Transaction
from PyQt6.QtWidgets import QApplication

from bal.core.plugin_base import BalPlugin
from bal.core.util import Util
from bal.core.will import Will, WillItem
from bal.gui.qt.widgets import LockTimeWidget, WillSettingsWidget

# Kept at module level: an unreferenced application object is destroyed at
# once, and creating a widget afterwards aborts Python.
_APP = QApplication.instance() or QApplication(sys.argv)

DAY = 86400
TODAY = int(datetime.now(tz=timezone.utc).replace(
    hour=0, minute=0, second=0, microsecond=0).timestamp())
D = TODAY + 400 * DAY            # a fixed delivery date (midnight UTC)

_TX_HEX = (
    "01000000012a5c9a94fcde98f5581cd00162c60a13936ceb75389ea65b"
    "f38633b424eb4031000000006c493046022100a82bbc57a0136751e543"
    "3f41cf000b3f1a99c6744775e76ec764fb78c54ee100022100f9e80b7d"
    "e89de861dc6fb0c1429d5da72c2b6b2ee2406bc9bfb1beedd729d98501"
    "2102e61d176da16edd1d258a200ad9759ef63adf8e14cd97f53227bae3"
    "5cdb84d2f6ffffffff0140420f00000000001976a914230ac37834073a"
    "42146f11ef8414ae929feaafc388ac00000000"
)


# ------------------------------------------------------------ the field


class _Window:
    """Just what the delivery field uses from BalWindow (as in
    ``tests/test_delivery_field_keeps_value.py``)."""

    def __init__(self, locktime, basic=False):
        """A window whose saved delivery is ``locktime``; no will yet."""
        self.basic = basic
        self.will_settings = {"locktime": locktime}
        self.willitems = {}
        self.update_setting_widgets = MagicMock()
        self.update_combo_setting_widgets = MagicMock()
        self.bal_plugin = SimpleNamespace(
            is_basic_mode=lambda: self.basic,
            WILL_SETTINGS=SimpleNamespace(get=lambda: dict(self.will_settings)),
            default_will_settings=BalPlugin.default_will_settings,
            default_will_settings_relative=BalPlugin.default_will_settings_relative,
            default_will_settings_absolute=BalPlugin.default_will_settings_absolute,
        )


def _will(recipe, locktime):
    """A will in force (one VALID item) built from ``recipe``, its
    transaction dated ``locktime``."""
    with patch.object(Transaction, "add_info_from_wallet"):
        item = WillItem({
            "tx": _TX_HEX[:-8] + locktime.to_bytes(4, "little").hex(),
            "heirs": {"alice": ["tb1qalice", "100%", recipe]},
            "willexecutor": None,
            "baltx_fees": 13,
        })
    item.set_status("COMPLETE", True)
    item.set_status("PUSHED", True)
    return {item._id: item}


def _field(locktime, basic=False):
    """A delivery field built before the will is loaded, as in a window."""
    bw = _Window(locktime, basic)
    return bw, LockTimeWidget(bw, None, locktime)


def _not_saved(bw, w, locktime):
    """Nothing was saved: no setting write, the field keeps the setting."""
    bw.update_setting_widgets.assert_not_called()
    bw.update_combo_setting_widgets.assert_not_called()
    assert w.current_value == locktime


def test_basic_period_shows_the_will_date_once_the_will_is_loaded():
    """The bug: built with no will, the field shows today + 1000 days; the
    will in force (one day earlier) is loaded; redrawn, it shows the will's
    date. The setting stays "1000d"."""
    bw, w = _field("1000d", basic=True)
    assert w.current_index == 1                     # the Date editor
    assert w.get_value() == TODAY + 1000 * DAY
    bw.willitems = _will("1000d", TODAY + 999 * DAY)
    w.refresh_shown_value()
    assert w.get_value() == TODAY + 999 * DAY
    _not_saved(bw, w, "1000d")


def test_basic_period_without_a_will_shows_today_plus_the_period():
    """No will in force: a new will gets today + the period, as before."""
    bw, w = _field("1000d", basic=True)
    w.refresh_shown_value()
    assert w.get_value() == TODAY + 1000 * DAY
    _not_saved(bw, w, "1000d")


def test_advanced_period_keeps_the_raw_text():
    """ADVANCED: the Raw editor shows the period itself, before and after."""
    bw, w = _field("1000d")
    assert w.current_index == 0 and w.get_value() == "1000d"
    bw.willitems = _will("1000d", TODAY + 999 * DAY)
    w.refresh_shown_value()
    assert w.get_value() == "1000d"
    _not_saved(bw, w, "1000d")


@pytest.mark.parametrize("basic", [True, False], ids=["basic", "advanced"])
def test_fixed_date_is_shown_as_saved(basic):
    """A fixed date is shown as saved, whatever the will says."""
    bw, w = _field(D, basic=basic)
    bw.willitems = _will(D, D - DAY)
    w.refresh_shown_value()
    assert w.get_value() == D
    _not_saved(bw, w, D)


def test_refresh_sends_no_edit():
    """Redrawing is not an edit: no ``valueEdited``, nothing saved."""
    bw, w = _field("1000d", basic=True)
    edits = []
    w.valueEdited.connect(lambda: edits.append(1))
    bw.willitems = _will("1000d", TODAY + 999 * DAY)
    w.refresh_shown_value()
    assert edits == []
    _not_saved(bw, w, "1000d")


# ------------------------------------------------- the toolbars, real flow


def _read_file(name):
    """The plugin's own files (icons), read from ``bal/`` (Electrum's
    ``BasePlugin.read_file`` needs the real plugin manager)."""
    path = os.path.join(os.path.dirname(__file__), os.pardir, "bal", name)
    with open(path, "rb") as f:
        return f.read()


class _List:
    """The Heirs / Will list of a window with its REAL toolbar.

    Like ``PreviewList`` / ``HeirListWidget`` (``bal/gui/qt/lists.py``):
    ``update_will`` redraws the list, and every redraw calls the toolbar's
    ``on_locktime_change``.
    """

    def __init__(self, toolbar):
        """``toolbar``: the list's ``WillSettingsWidget``."""
        self.will_settings_widget = toolbar
        self.will = None

    def update(self, *a):
        """Redraw the list: the toolbar is told."""
        self.will_settings_widget.on_locktime_change()

    def update_will(self, willitems):
        """New will items, then a redraw."""
        self.update()


def _toolbars(bw):
    """Real Will and Heirs tab toolbars on ``bw``, built as
    ``BalWindow.__init__`` builds them: before the will is loaded."""
    bw.bal_plugin.read_file = _read_file
    will_tb = WillSettingsWidget(bw, None)
    heirs_tb = WillSettingsWidget(bw, None)
    bw.will_list_widget = _List(will_tb)
    bw.heir_list_widget = _List(heirs_tb)
    bw.will_tab = SimpleNamespace(update=lambda: None)
    bw.heirs_tab = SimpleNamespace(update=lambda: None)
    return will_tb, heirs_tb


def _shown(toolbar):
    """What the toolbar's delivery field shows."""
    return toolbar.widgets["locktime"].get_value()


@pytest.fixture
def flow_session(monkeypatch):
    """``tests/flowkit.py`` sessions with Electrum's asyncio loop."""
    fk.ensure_event_loop()
    made = []

    def make(**kw):
        """Start one session with three will-executors; close it later."""
        s = fk.Session(monkeypatch, **kw)
        s.world.add_executor()
        made.append(s)
        return s

    yield make
    for s in made:
        s.close()
    fk.stop_event_loop()


def test_basic_toolbars_follow_a_rebuild_that_moves_the_will(flow_session):
    """The owner's case: BASIC, delivery "1000d", a will sent; the heirs
    change and the Check rebuilds the will one day earlier. Both toolbars
    then show the will's new date; the setting stays "1000d"."""
    s = flow_session(mode="basic", locktime="1000d")
    will_tb, heirs_tb = _toolbars(s.bw)
    s.check()
    first = Will.get_min_locktime(s.bw.willitems)
    assert _shown(will_tb) == first
    s.set_heirs(alice="50%", bob="30%")
    s.check()
    moved = Will.get_min_locktime(s.bw.willitems)
    assert moved == first - DAY
    s.bw.heir_list_widget.update()      # the Heirs tab redraws its list
    assert _shown(will_tb) == moved
    assert _shown(heirs_tb) == moved
    assert s.plugin.WILL_SETTINGS.get()["locktime"] == "1000d"


def test_basic_toolbars_show_the_will_after_a_restart(flow_session):
    """The next day Electrum is opened again: the toolbars are built before
    the will is loaded (today + 1000 days, one day after the will); once the
    will is loaded they show the will's date, like the wizard."""
    s = flow_session(mode="basic", locktime="1000d")
    s.check()
    will_date = Will.get_min_locktime(s.bw.willitems)
    s.clock.travel(1)
    bw = fk.FlowWindow(s.plugin, s.wallet, s.user, s.world)
    will_tb, heirs_tb = _toolbars(bw)
    assert _shown(will_tb) == Util.parse_locktime_string("1000d")
    assert _shown(will_tb) == will_date + DAY
    bw.load_willitems()                 # the window loads the will
    bw.heir_list_widget.update()
    assert _shown(will_tb) == will_date
    assert _shown(heirs_tb) == will_date
    wizard = WillSettingsWidget(bw, None, "v", read_only=False)
    assert _shown(wizard) == will_date
    assert s.plugin.WILL_SETTINGS.get()["locktime"] == "1000d"


def test_advanced_and_fixed_toolbars_unchanged(flow_session):
    """ADVANCED with a period: the Raw text "1000d" stays, the date next to
    it is the will's. BASIC with a fixed date: the field follows the stored
    date as before (CHANGELOG #110)."""
    s = flow_session(mode="advanced", locktime="1000d")
    will_tb, _heirs_tb = _toolbars(s.bw)
    s.check()
    s.set_heirs(alice="50%", bob="30%")
    s.check()
    assert _shown(will_tb) == "1000d"
    label = will_tb.widgets["locktime"].editor.label.text()
    assert label == datetime.fromtimestamp(
        Will.get_min_locktime(s.bw.willitems), tz=timezone.utc).strftime("%Y-%m-%d")
    s = flow_session(mode="basic", locktime=fk.D0)
    will_tb, _heirs_tb = _toolbars(s.bw)
    s.check()
    s.set_heirs(alice="50%", bob="30%")
    s.check()
    assert _shown(will_tb) == fk.D0 - DAY
    assert s.plugin.WILL_SETTINGS.get()["locktime"] == fk.D0 - DAY


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
