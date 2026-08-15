"""
bal.cli
=======

Headless command-line layer of the Bitcoin After Life (BAL) Electrum plugin.

This sub-package implements the ``"cmdline"`` front-end: it exposes the
plugin's functionality through Electrum ``bal_*`` commands while reusing only
the GUI-free logic from ``bal.core``.  Like ``bal.core``, it MUST never import
PyQt or ``electrum.gui``.

    * ``bal.cli.commands``   -> the ``@plugin_command`` transport layer
    * ``bal.cli.controller`` -> headless replica of the Qt flows (later phases)
    * ``bal.cli.plugin``     -> ``Plugin(BalPlugin)`` entry point for the daemon

Electrum discovers the plugin through ``manifest.json`` (``available_for``
includes ``"cmdline"``) and loads the entry point from ``cmdline.py``, a thin
zip-safe shim following the same pattern as ``qt.py``.
"""
