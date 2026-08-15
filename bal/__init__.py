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
        cli/             Headless command-line layer (no Qt)
            commands.py     The @plugin_command transport layer (registers
                            the ``bal_*`` commands)
            controller.py   Headless replica of the Qt flows (later phases)
            plugin.py       Plugin(BalPlugin) entry point for the daemon
        qt.py            Thin loader shim re-exporting `Plugin` for Electrum
        cmdline.py       Thin loader shim re-exporting `Plugin` for the daemon

Electrum discovers the plugin through ``manifest.json`` and loads the GUI
entry point from ``qt.py`` (the shim), which imports the real ``Plugin``
from ``gui.qt.plugin``; the command-line/daemon entry point is ``cmdline.py``
(the shim), which imports ``Plugin`` from ``cli.plugin``.

The plugin supports Electrum 4.7.2 and 4.8.0 with PyQt6.  Electrum 4.8.0 removed
``json_db.register_dict`` and replaced it with the path-based
``electrum.stored_dict.register_name``; ``core.plugin_base`` detects which API is
available and adapts, so both releases keep working.
"""

# The plugin version is NOT defined here. It lives only in ``bal/manifest.json``
# (the single source of truth) and is read at runtime via ``get_version()`` in
# ``bal/core/plugin_base.py`` (exposed as the ``BalPlugin.version`` property).
# Keeping a hardcoded ``__version__`` here would just be a stale duplicate.

# --------------------------------------------------------------------------- #
# CLI command registration
# --------------------------------------------------------------------------- #
# Electrum's CLI pre-parse (run_electrum calls ``Plugins(config, cmd_only=True)``)
# only imports the plugin package ``__init__`` to discover its commands.
# Importing ``bal.cli.commands`` here registers every ``bal_*`` command with
# ``electrum.commands`` (``known_commands`` + the ``Commands`` class), so the
# commands become available on the command line and over JSON-RPC without any Qt.
#
# The import must be zip-safe: when the plugin is loaded as an external zip,
# Electrum registers the package under the synthetic name
# ``electrum_external_plugins.bal``, but the module's ``__package__`` is only
# ``bal`` (the zip-internal directory name), which is not present in
# ``sys.modules`` and cannot be used for sub-module imports.  We therefore
# resolve the real package name and import through ``importlib`` (the same
# trick as ``qt.py``).
import importlib
import sys as _sys


def _resolve_package_name() -> str:
    """Return the name this package is registered under in ``sys.modules``.

    Internal plugins are imported as ``electrum.plugins.bal`` (a normal import,
    so ``__package__`` is already correct).  External zip plugins are imported
    under the synthetic name ``electrum_external_plugins.bal`` with
    ``__package__`` set to just the zip-internal directory name (``bal``); only
    the synthetic name is present in ``sys.modules``.
    """
    pkg = __package__ or "bal"
    if pkg in _sys.modules:
        return pkg
    synthetic = "electrum_external_plugins." + __name__
    if synthetic in _sys.modules:
        return synthetic
    return pkg


def _ensure_parent_packages(pkg_name: str) -> None:
    """Backfill missing ancestor packages in ``sys.modules``.

    When loaded from a zip as an external plugin, Electrum only executes the
    package ``__init__``; the synthetic root package (``electrum_external_plugins``)
    may be missing, which would break sub-module imports.  We stub it out as a
    namespace package so ``importlib`` can still resolve its children (same
    helper as ``qt.py``).
    """
    parts = pkg_name.split(".")
    for i in range(1, len(parts)):
        ancestor = ".".join(parts[:i])
        if ancestor in _sys.modules:
            continue
        try:
            importlib.import_module(ancestor)
        except Exception:
            import types

            module = types.ModuleType(ancestor)
            module.__path__ = []  # mark as a (namespace) package
            _sys.modules[ancestor] = module


def _register_cli_commands() -> None:
    """Import ``bal.cli.commands`` so Electrum registers the ``bal_*`` commands.

    Guarded so a dual install (internal package AND external zip) cannot
    register the same command names twice, which would make
    ``electrum.commands.plugin_command`` raise
    "Command name bal_... already exists".
    """
    from electrum import commands as _electrum_commands

    if getattr(_electrum_commands, "_bal_cli_commands_registered", False):
        return
    pkg = _resolve_package_name()
    _ensure_parent_packages(pkg)
    importlib.import_module(pkg + ".cli.commands")
    _electrum_commands._bal_cli_commands_registered = True


_register_cli_commands()
