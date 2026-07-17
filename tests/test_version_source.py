"""Tests for the single-source-of-truth plugin version.

The plugin version must come ONLY from ``bal/manifest.json`` (no hardcoded
copies, no ``bal/VERSION`` file), and it must be readable both from an extracted
package and from INSIDE a zip (the way Electrum loads external plugins via
zipimport). These tests lock that behavior.

Run:
    QT_QPA_PLATFORM=offscreen PYTHONPATH=<electrum-src> \
        python3 -m pytest tests/test_version_source.py -q
"""

import json
import os
import subprocess
import sys
import zipfile

import bal
from bal.core.plugin_base import BalPlugin, get_version

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MANIFEST = os.path.join(_REPO, "bal", "manifest.json")


def _manifest_version():
    with open(_MANIFEST, encoding="utf-8") as f:
        return json.load(f)["version"]


def test_get_version_matches_manifest():
    """get_version() returns exactly the manifest 'version' field."""
    assert get_version() == _manifest_version()


def test_get_version_is_not_unknown():
    """The manifest must be readable - never the 'unknown' fallback here."""
    assert get_version() != "unknown"
    # a plausible semantic version, e.g. 0.6.0
    assert get_version()[0].isdigit()


def test_version_property_matches_get_version():
    """BalPlugin.version is a property returning the same value."""
    assert isinstance(BalPlugin.version, property)
    # A bare object works as ``self`` because the property ignores instance
    # state and simply delegates to get_version().
    class _Dummy:
        version = BalPlugin.version
    assert _Dummy().version == get_version()


def test_no_hardcoded_class_version():
    """The old hardcoded BalPlugin.__version__ constant is gone."""
    assert "__version__" not in vars(BalPlugin)


def test_no_version_file_in_package():
    """The bal/VERSION file has been removed (manifest is the only source)."""
    assert not os.path.exists(os.path.join(_REPO, "bal", "VERSION"))


def test_get_version_cached():
    """Second call returns the cached value (same object)."""
    v1 = get_version()
    v2 = get_version()
    assert v1 == v2
    assert v1 is v2  # cached string, identical object


def test_version_readable_from_inside_zip(tmp_path):
    """The version must be readable when 'bal' is imported from a zip.

    Electrum loads external plugins via zipimport, so this is the real
    production scenario (and the one that used to break on Windows with
    os.path.join). We build a zip of the bal package and read the version in a
    fresh interpreter whose only path to 'bal' is that zip.
    """
    bal_dir = os.path.dirname(os.path.abspath(bal.__file__))
    zip_path = tmp_path / "bal_plugin.zip"
    with zipfile.ZipFile(zip_path, "w") as z:
        for root, _dirs, files in os.walk(bal_dir):
            for fn in files:
                if fn.endswith(".pyc"):
                    continue
                full = os.path.join(root, fn)
                # arcname keeps the leading 'bal/' package prefix
                arc = os.path.join(
                    "bal", os.path.relpath(full, bal_dir)
                )
                z.write(full, arc)

    # electrum must stay importable in the child, but 'bal' must resolve ONLY
    # from the zip. So: drop the on-disk repo (and this package's dir) from the
    # child's path, prepend the zip, and run from a neutral working directory.
    repo_real = os.path.realpath(_REPO)
    bal_parent_real = os.path.realpath(os.path.dirname(bal_dir))
    filtered = [
        p for p in sys.path
        if p and os.path.realpath(p) not in (repo_real, bal_parent_real)
    ]
    child_path = os.pathsep.join([str(zip_path)] + filtered)
    env = dict(os.environ, PYTHONPATH=child_path, QT_QPA_PLATFORM="offscreen")
    code = (
        "import bal, os;"
        "assert 'bal_plugin.zip' in bal.__file__.replace(os.sep, '/'),"
        "  'bal not loaded from zip: ' + bal.__file__;"
        "from bal.core.plugin_base import get_version;"
        "print(get_version())"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True, text=True,
        cwd=str(tmp_path),
    )
    assert out.returncode == 0, f"child failed: {out.stderr}"
    assert out.stdout.strip() == _manifest_version()
