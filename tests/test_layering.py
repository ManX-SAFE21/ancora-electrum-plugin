"""The layer rules of the plugin, checked on the source (CHANGELOG #115).

``bal/core``, ``bal/engine`` and ``bal/cli`` must work without a GUI: the
command line runs in a daemon with no Qt, and Phase 5e moves the flows into
``bal/engine`` so the GUI and the command line share them. The rules:

* no Qt and no GUI in the three layers (``PyQt*``, ``electrum.gui``,
  ``bal.gui``), not even inside a function;
* the engine never imports the command line (``bal.cli``);
* imports of the plugin's own modules are relative: loaded from a zip the
  package is ``electrum_external_plugins.bal``, so ``import bal.x`` fails.

Two checks: a scan of every ``import`` in the source (it also sees the
imports inside functions), and a real import of every module in a separate
Python process, which must not load any Qt or GUI module.

Run:
    PYTHONPATH=electrum-src python3 -m pytest tests/test_layering.py -q
"""

import ast
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
NO_GUI_LAYERS = ("core", "engine", "cli")


def _under(name, prefix):
    """True when module ``name`` is ``prefix`` or one of its submodules."""
    return name == prefix or name.startswith(prefix + ".")


def _is_gui(name):
    """True for a Qt or GUI module (PyQt5/PyQt6, electrum.gui, bal.gui)."""
    return (
        name.split(".")[0].startswith("PyQt")
        or _under(name, "electrum.gui")
        or _under(name, "bal.gui")
    )


def _violations(source, package, layer):
    """Return the broken layer rules of one module, as readable strings.

    ``package`` is the module's package as a dotted name (``"bal.core"``),
    used to resolve relative imports; ``layer`` is ``"core"``, ``"engine"``
    or ``"cli"``. Each ``from X import a`` is checked both as ``X`` and as
    ``X.a``, so ``from .. import gui`` is caught too.
    """
    found = []
    parts = package.split(".")
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            targets = [(alias.name, True) for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            absolute = node.level == 0
            if absolute:
                base = node.module or ""
            else:
                stem = parts[: len(parts) - (node.level - 1)]
                base = ".".join(stem + ([node.module] if node.module else []))
            targets = [(base, absolute)] + [
                (base + "." + alias.name, absolute) for alias in node.names
            ]
        else:
            continue
        for name, absolute in targets:
            if _is_gui(name):
                found.append(f"line {node.lineno}: GUI import {name}")
            if absolute and _under(name, "bal"):
                found.append(f"line {node.lineno}: absolute import {name}")
            if layer == "engine" and _under(name, "bal.cli"):
                found.append(f"line {node.lineno}: engine imports {name}")
    return sorted(set(found))


def _layer_files(layer):
    """Every ``.py`` file of one layer, with its dotted package name."""
    base = ROOT / "bal" / layer
    for path in sorted(base.rglob("*.py")):
        rel = path.relative_to(ROOT).with_suffix("")
        package = ".".join(rel.parts[:-1])
        yield path, package


# ------------------------------------------------------------------ scanner


def test_scanner_finds_each_kind_of_violation():
    """The scan is not vacuous: each rule is caught on a made-up module."""
    src = (
        "from PyQt6.QtWidgets import QWidget\n"
        "import electrum.gui.qt\n"
        "from ..gui.qt import dialogs\n"
        "from .. import gui\n"
        "import bal.core.will\n"
        "from ..cli import commands\n"
        "def later():\n"
        "    from PyQt6 import QtCore\n"
    )
    found = "\n".join(_violations(src, "bal.engine", "engine"))
    assert "line 1: GUI import PyQt6.QtWidgets" in found
    assert "line 2: GUI import electrum.gui.qt" in found
    assert "line 3: GUI import bal.gui.qt" in found
    assert "line 4: GUI import bal.gui" in found
    assert "line 5: absolute import bal.core.will" in found
    assert "line 6: engine imports bal.cli" in found
    assert "line 8: GUI import PyQt6" in found


def test_scanner_accepts_the_allowed_imports():
    """Core modules, Electrum's non-GUI modules and other packages pass."""
    src = (
        "from ..core.will import Will\n"
        "from ..i18n import _\n"
        "from . import util\n"
        "from electrum.transaction import Transaction\n"
        "import bal_wallet_utils\n"
    )
    assert _violations(src, "bal.engine", "engine") == []
    # The command line may use itself; only the engine may not.
    assert _violations("from . import controller\n", "bal.cli", "cli") == []


# ------------------------------------------------------------------ source


def test_layers_exist():
    """The three layers are packages (``bal/engine`` since CHANGELOG #115)."""
    for layer in NO_GUI_LAYERS:
        assert (ROOT / "bal" / layer / "__init__.py").is_file(), layer


def test_no_layer_breaks_the_rules():
    """No module of core, engine or cli imports a GUI, cli (for the engine)
    or the plugin with an absolute name."""
    problems = []
    for layer in NO_GUI_LAYERS:
        for path, package in _layer_files(layer):
            for v in _violations(path.read_text(encoding="utf-8"), package, layer):
                problems.append(f"{path.relative_to(ROOT)} {v}")
    assert problems == []


# ------------------------------------------------------------------ runtime


def test_importing_the_layers_loads_no_gui():
    """Import every module of the three layers in a fresh Python process and
    check that no Qt or GUI module was loaded on the way."""
    code = (
        "import importlib, pkgutil, sys\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        "names = []\n"
        f"for layer in {NO_GUI_LAYERS!r}:\n"
        "    pkg = importlib.import_module('bal.' + layer)\n"
        "    names.append(pkg.__name__)\n"
        "    for info in pkgutil.walk_packages(pkg.__path__, pkg.__name__ + '.'):\n"
        "        importlib.import_module(info.name)\n"
        "        names.append(info.name)\n"
        "bad = sorted(m for m in sys.modules\n"
        "             if m.split('.')[0].startswith('PyQt')\n"
        "             or m == 'electrum.gui' or m.startswith('electrum.gui.')\n"
        "             or m == 'bal.gui' or m.startswith('bal.gui.'))\n"
        "print('imported:', names)\n"
        "print('GUI:', bad)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(ROOT),
        env=dict(os.environ),
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stderr
    assert "GUI: []" in result.stdout, result.stdout
    for name in ("'bal.engine'", "'bal.core.messages'", "'bal.cli.commands'"):
        assert name in result.stdout, result.stdout


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
