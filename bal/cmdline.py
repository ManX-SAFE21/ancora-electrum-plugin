"""
bal.cmdline
===========

Compatibility shim for Electrum's plugin loader (command-line front-end).

Electrum loads a plugin with ``gui_name='cmdline'`` by importing the
``cmdline`` module of the plugin package and looking for a ``Plugin`` class.
The real implementation lives in the ``bal.cli`` sub-package, so this module
re-exports ``Plugin`` from ``bal.cli.plugin``.

Like ``qt.py``, this file is not a one-line relative import because the very
same code may be loaded as an *external* plugin from a ``.zip``, where Electrum
imports the package under the synthetic top-level name
``electrum_external_plugins.bal`` and never registers the intermediate parent
packages.  See the module docstring of ``bal.qt`` for the full rationale.  The
shim resolves the run-time package name, backfills the missing parents into
``sys.modules`` and imports the real implementation via
:func:`importlib.import_module`.

Unlike ``qt.py``, this module MUST never import PyQt (the daemon loads it in a
headless process).
"""

import importlib
import sys


def _ensure_parent_packages(pkg_name: str) -> None:
    """Make sure every ancestor package of *pkg_name* is in ``sys.modules``.

    When loaded from a zip as an external plugin, Electrum only executes the
    plugin package ``__init__`` and the ``cmdline`` module.  The synthetic root
    package (e.g. ``electrum_external_plugins``) and any intermediate packages
    may be missing from ``sys.modules``, which breaks relative/absolute
    sub-module imports.  We backfill them here using this module's own loader
    so that ``importlib`` can find sibling sub-packages.
    """
    parts = pkg_name.split(".")
    # Walk from the top-most ancestor down to (but not including) pkg_name.
    for i in range(1, len(parts)):
        ancestor = ".".join(parts[:i])
        if ancestor in sys.modules:
            continue
        try:
            importlib.import_module(ancestor)
        except Exception:
            # The synthetic root (e.g. 'electrum_external_plugins') often has no
            # real spec.  Create a minimal namespace package stub so that the
            # import machinery can still resolve its children.
            import types

            module = types.ModuleType(ancestor)
            module.__path__ = []  # mark as a (namespace) package
            sys.modules[ancestor] = module


# The package this module belongs to. Could be 'electrum.plugins.bal' (internal)
# or 'electrum_external_plugins.bal' (external zip), depending on how Electrum
# loaded us.
_PKG = __package__ or "bal"

_ensure_parent_packages(_PKG)

# Import the real implementation using the fully-qualified, run-time package
# name so it works regardless of the synthetic prefix Electrum assigned.
_plugin_module = importlib.import_module(_PKG + ".cli.plugin")

Plugin = _plugin_module.Plugin  # noqa: F401  (re-exported for Electrum)
