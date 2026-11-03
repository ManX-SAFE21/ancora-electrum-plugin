"""The manifest asks for Electrum 4.8.3 or later (CHANGELOG #221).

Before 4.8.3, adding a newer Àncòra ZIP with another file name left two
copies of the plugin ``bal`` in Electrum's plugins folder, and loading broke
("Error pre-loading electrum_external_plugins.bal", seen by the owner on
4.8.1). Electrum 4.8.3 replaces the old ZIP ("allow external plugin
upgrades") and asks for a restart. With ``min_electrum_version`` an older
Electrum skips the plugin instead (it logs "version mismatch").

These tests read the manifest the way Electrum does
(``PluginMetadata.from_json``), so a typo in the field or the value shows.

Run:
    QT_QPA_PLATFORM=offscreen PYTHONPATH=<electrum-src> \
        python3 -m pytest tests/test_manifest_min_electrum.py -q
"""

import json
import os
from unittest.mock import patch

import electrum.plugin as electrum_plugin
from electrum.plugin import PluginMetadata

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MANIFEST = os.path.join(_REPO, "bal", "manifest.json")


def _metadata():
    """The manifest as Electrum's own metadata object.

    ``path`` is required by Electrum; it reads the version fields only from
    the JSON, so the source file's path is enough.
    """
    with open(_MANIFEST, encoding="utf-8") as f:
        return PluginMetadata.from_json(json.load(f), path=_MANIFEST, dirname="bal")


def test_manifest_asks_for_electrum_483():
    """The manifest names 4.8.3 as the lowest Electrum version."""
    assert _metadata().min_electrum_version == "4.8.3"


def test_electrum_483_and_later_accept_the_plugin():
    """4.8.3 and later versions load the plugin."""
    for version in ("4.8.3", "4.8.4", "4.9.0", "5.0.0"):
        with patch.object(electrum_plugin, "ELECTRUM_VERSION", version):
            assert _metadata().is_electrum_version_compatible(), version


def test_older_electrum_skips_the_plugin():
    """4.8.2 and older skip the plugin instead of loading it."""
    for version in ("4.8.2", "4.8.1", "4.8.0", "4.7.2"):
        with patch.object(electrum_plugin, "ELECTRUM_VERSION", version):
            assert not _metadata().is_electrum_version_compatible(), version
