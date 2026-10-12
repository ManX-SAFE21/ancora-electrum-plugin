"""What the engine expects from the object it works for (CHANGELOG #117).

The engine keeps no state of its own: its functions (``bal.engine.context``)
work on the state of the caller, the "host". Today the host is the command
line's ``BalController``; from the next Phase 5e step also the GUI's
``BalWindow``. Both already hold the same state under the same names.

Where the two do different things for the same step, the engine calls one of
three hooks, and each host keeps its own behaviour:

* ``store_will_setting(field, value)``: a will setting changed (the Check
  Alive conversion of #80, the delivery date moved to the rebuilt will of
  #107/#110). Since #123 the host writes it with
  ``bal.engine.context.set_will_setting`` (the one validated write, into
  the wallet's own dict) and makes it durable: the command line saves the
  wallet; the GUI also refreshes the fields that show it, on the GUI
  thread. Electrum's global ``bal_will_settings`` is never written;
* ``flush_wallet_db()``: the will or the heirs changed in the wallet. The
  command line writes the wallet file at once; the GUI leaves it to
  Electrum;
* ``after_history_saved()``: the will was saved in the History. The GUI
  redraws the History tab; the command line does nothing.

No Qt, no ``bal.cli`` (``bal/engine`` rules, see ``bal/engine/__init__``).
"""

from typing import Any, Protocol


class WalletHost(Protocol):
    """The state and hooks the engine functions read and call.

    Attributes (the names the GUI and the command line already use):

    * ``bal_plugin``: the plugin (settings such as ``SAVE_HISTORY``,
      ``HISTORY_LABEL``, ``NO_WILLEXECUTOR``, ``ENABLE_MULTIVERSE``,
      ``is_basic_mode()``);
    * ``wallet``: the Electrum wallet;
    * ``will``: the wallet's stored ``will`` dict (``{txid: dict}``);
    * ``willitems``: the same will as ``{txid: WillItem}``;
    * ``heirs``: the wallet's ``Heirs``;
    * ``will_settings``: the delivery settings of THIS wallet (delivery
      date, fee rate, Check Alive bridge; CHANGELOG #123). After
      ``bal.engine.context.load_will_settings`` it IS the live dict of the
      wallet file (``wallet.db.get_dict("will_settings")``), changed item by
      item, never replaced. When the settings could not be prepared it
      holds display-only values instead;
    * ``settings_unavailable`` (optional, default False): True when the
      settings could not be prepared (fail closed). Then no flow builds,
      signs, pushes or invalidates (``init_class_variables`` raises
      ``DeliverySettingsUnavailable``) and ``set_will_setting`` refuses
      every write. A host without the attribute is never held;
    * ``willexecutors``: the will-executor list of the current network;
    * ``date_to_check``: the reference date of the checks;
    * ``no_willexecutor``: the user is their own will-executor.
    """

    bal_plugin: Any
    wallet: Any
    will: dict
    willitems: dict
    heirs: Any
    will_settings: dict
    willexecutors: dict
    date_to_check: Any
    no_willexecutor: bool

    def store_will_setting(self, field, value) -> None:
        """Write one delivery setting (``context.set_will_setting``) and
        make it durable; never Electrum's global key."""

    def flush_wallet_db(self) -> None:
        """Make the wallet's changed data durable (or leave it to Electrum)."""

    def after_history_saved(self) -> None:
        """React to the will being saved in the History (or do nothing)."""
