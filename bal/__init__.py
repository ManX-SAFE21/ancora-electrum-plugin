"""BAL - Bitcoin After Life Electrum plugin.

Free and decentralized Bitcoin inheritance support for the Electrum wallet.

This package was reorganized (Approach A: conservative, behavior-preserving)
to cleanly separate logic from presentation. The original monolithic plugin
mixed the business logic with the PyQt GUI; here the two concerns live in
distinct sub-packages:

    bal/
        core/            GUI-free business logic (importable without Qt)
            util.py          Generic helpers (encoding, validation, ...)
            plugin_base.py   BasePlugin subclass, config, timestamp handling
            heirs.py         Heir list model + transaction building
            will.py          Will / WillItem domain model
            willexecutors.py Will-executor (dead-man's switch) networking
        gui/
            qt/          PyQt6 presentation layer
                theme.py     Colors / status -> color mapping (status_color)
                common.py    Shared imports and small GUI helpers
                widgets.py   Leaf widgets (editors, labels, checkboxes, ...)
                calendar.py  BalCalendar widget
                dialogs.py   Dialog windows (wizard, build-will, detail, ...)
                lists.py     Tree/list views (heirs, preview, will-executors)
                window.py    BalWindow controller (per-wallet GUI state)
                plugin.py    Plugin class wiring Electrum @hooks to the GUI
        qt.py            Thin loader shim re-exporting `Plugin` for Electrum

Electrum discovers the plugin through ``manifest.json`` and loads the GUI
entry point from ``qt.py`` (the shim), which imports the real ``Plugin``
from ``gui.qt.plugin``.

The plugin supports Electrum 4.7.2 and 4.8.0 with PyQt6.  Electrum 4.8.0 removed
``json_db.register_dict`` and replaced it with the path-based
``electrum.stored_dict.register_name``; ``core.plugin_base`` detects which API is
available and adapts, so both releases keep working.
"""

# The plugin version is NOT defined here. It lives only in ``bal/manifest.json``
# (the single source of truth) and is read at runtime via ``get_version()`` in
# ``bal/core/plugin_base.py`` (exposed as the ``BalPlugin.version`` property).
# Keeping a hardcoded ``__version__`` here would just be a stale duplicate.
