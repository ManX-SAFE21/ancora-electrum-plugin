"""What the engine expects from the object it works for (CHANGELOG #117).

The engine keeps no state of its own: its functions (``bal.engine.context``)
work on the state of the caller, the "host". Today the host is the command
line's ``BalController``; from the next Phase 5e step also the GUI's
``BalWindow``. Both already hold the same state under the same names.

Where the two do different things for the same step, the engine calls one of
three hooks, and each host keeps its own behaviour:

* ``store_will_setting(field, value)``: a will setting changed (today only
  the Check Alive conversion of #80). The host sets
  ``will_settings[field] = value`` and stores the settings; the GUI also
  refreshes the fields that show them;
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
    * ``will_settings``: the will settings (delivery date, Check Alive,
      fee);
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
        """Set ``will_settings[field] = value`` and store the settings."""

    def flush_wallet_db(self) -> None:
        """Make the wallet's changed data durable (or leave it to Electrum)."""

    def after_history_saved(self) -> None:
        """React to the will being saved in the History (or do nothing)."""
