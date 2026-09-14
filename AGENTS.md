# AGENTS.md

BAL — Bitcoin After Life, an Electrum plugin (inheritance / dead-man's-switch).
Source-of-truth docs: `README.md`, `HANDOFF.md`, `COMPATIBILITY.md`.

Paths below are relative to the repo, or use `$BAL_HOME` (the directory
containing this repo and the sibling `electrum/` checkout).

## Environments (critical)

Two separate venvs; using the wrong one is the #1 mistake.

- **Runtime env** (Electrum + PyQt6, has `electrum` importable):
  `source "$BAL_HOME/electrum/env/bin/activate"`
  This is an editable install of the Electrum 4.8.0 checkout at
  `$BAL_HOME/electrum`. Use it for anything that imports
  `electrum`, runs GUI code, or runs tests.
- **Lint venv** (repo-local `venv/`): ruff, black, flake8 only. It cannot
  import `electrum` or `PyQt6`. Do NOT use it to run tests.

The plugin's `bal/` directory is symlinked into
`electrum/electrum/plugins/bal` (internal-plugin install used during dev).

## Test & verify

Tests work **both** as standalone scripts and via pytest (tests use `def test_*`
naming and also have `if __name__ == "__main__"` blocks). Run a single file
directly:

```bash
source "$BAL_HOME/electrum/env/bin/activate"
python3 tests/test_core_heirs.py        # core, no Qt needed
QT_QPA_PLATFORM=offscreen python3 tests/test_gui_common.py   # GUI tests need offscreen
```

Or run a batch with pytest (as `make-release.sh` does):

```bash
source "$BAL_HOME/electrum/env/bin/activate"
QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_core_*.py -q
```

- Most core tests run offline (no wallet/network). Some files
  (`test_group_*.py`, `test_no_willexecutor_karen7.py`, `parallel_ping_test.py`)
  exercise will-executor/network flows and need the live servers — don't rely on
  them for quick verification.
- `tests/smoke_test.py` proves clean import under real Electrum:
  `QT_QPA_PLATFORM=offscreen python3 tests/smoke_test.py electrum.plugins.bal`
- `tests/external_zip_test.py` loads the built zip the way Electrum's plugin
  dialog does (`electrum_external_plugins.bal`); run it after `build_zip.py`.

## Lint / typecheck

- **Ruff is NOT clean** (hundreds of pre-existing errors in `bal/` and
  `tests/`). Do not run `--fix` wholesale and do not try to silence everything;
  just avoid adding new violations. Config: `pyproject.toml` (line-length 88,
  E501 ignored). Per-file ignores suppress `F403`/`F405` for the intentional
  `from .common import *` hub pattern in `bal/gui/qt/`.
- Lint via the repo venv: `./venv/bin/ruff`
- Typecheck: `pyright` (npm, `node_modules/`), config `pyrightconfig.json`
  (`extraPaths: ["../electrum"]`). Pyright reports many false positives on
  dynamically-attached attrs (e.g. `self.window`, `BalPlugin.*`); don't chase
  them.

## Architecture

- `bal/core/` = GUI-free logic (`heirs.py`, `will.py`, `willexecutors.py`,
  `plugin_base.py`, `util.py`, `checkalive.py`, `reminders.py`,
  `input_rules.py`).
  Must never import Qt.
- `bal/gui/qt/` = PyQt6 layer. `window.py` is the per-wallet controller,
  `plugin.py` is the Electrum `@hooks` entry. `qt.py` is a zipimport shim.
  `common.py` uses `import *` intentionally (ruff suppresses F403/F405 here);
  `bal/gui/qt/*.py` all import from it.
- `bal/cli/` = headless command-line layer (no Qt). `plugin.py` is the daemon
  entry point, `commands.py` registers `bal_*` commands with Electrum.
- `bal/wallet_util/` = wallet helper utilities for Qt and core.
- `bal/qt.py` and `bal/cmdline.py` are thin shims that Electrum discovers
  via `manifest.json`; they import the real `Plugin` class via `importlib`.
- `bal/manifest.json` = version source of truth (Electrum reads it; also read by
  `make-release.sh`).
- Compatibility constraint: must support Electrum **4.7.2 and 4.8.0**; the DB
  registration API differs between them (`json_db.register_dict` vs
  `stored_dict.register_name`).

## Build / release

```bash
python3 build_zip.py   # -> bal-electrum-plugin.zip (deterministic, prints sha256)
./make-release.sh [v0.x.y]   # bump manifest version, tag, sign, push Gitea release
```

- `make-release.sh` requires gpg and Gitea credentials (`~/.git-credentials`
  or `GITEA_USER`/`GITEA_TOKEN`). It bumps `bal/manifest.json` — bump the
  version there, never invent a new source of truth.
- Remote is Gitea (`origin` = bitcoin-after.life). `.env` holds a Gitea token
  (gitignored, never commit it).
