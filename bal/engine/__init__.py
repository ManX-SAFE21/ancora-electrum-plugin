"""The shared engine of the plugin's flows (Phase 5e, CHANGELOG #115).

Today the same operations (check the will, build it, sign it, send it to the
will-executors, invalidate it) are written in several copies: the Check
dialog, the Will tab menus, the automatic rebuild and the command line.
Phase 5e moves them here one piece at a time, so every caller runs the same
code. The package starts empty; each later step adds one piece and keeps the
old names as bridges.

Layer rules, checked by ``tests/test_layering.py``:

* no Qt and no GUI: never import ``PyQt*``, ``electrum.gui`` or ``bal.gui``
  (the dialogs and the windows talk to the engine through plain callbacks);
* no command line: never import ``bal.cli`` (the command line uses the
  engine, not the other way round);
* relative imports only (``from ..core import ...``): when Electrum loads the
  plugin from a zip the package is named ``electrum_external_plugins.bal``,
  so an absolute ``bal.`` import would fail there.

The engine may use ``bal.core`` and Electrum's non-GUI modules.
"""
