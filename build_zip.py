#!/usr/bin/env python3
"""Build a clean, zipimport-friendly distribution archive of the BAL plugin.

Electrum loads external plugins from a ``.zip`` using Python's ``zipimport``.
``zipimport`` is picky about the archive layout, so this builder deliberately:

  * writes **only files** (no explicit directory entries) — some Electrum
    portable builds choke on directory records inside the archive;
  * uses standard DEFLATE compression (well supported by ``zipimport``);
  * emits entries in a deterministic, sorted order, each with the same
    fixed date, permissions and "made on" system, so the archive is
    reproducible: the same sources give the same SHA-256 on any computer,
    whatever the files' modification times (CHANGELOG #219);
  * skips ``__pycache__`` directories and compiled ``*.pyc``/``*.pyo`` files;
  * compiles each translation catalog ``bal/locale/<lang>/LC_MESSAGES/bal.po``
    into ``bal.mo`` inside the archive (the ``.mo`` files are not kept in
    git, and the ``.po``/``.pot`` sources are not shipped). This needs Babel
    (``pip install babel``); without it the build stops, so a release can
    never ship without its translations by mistake.

The archive keeps the top-level ``bal/`` directory so that the package is
importable as ``bal`` (and Electrum derives ``dirname='bal'`` from the path of
``bal/manifest.json``).

Usage::

    python3 build_zip.py [output.zip]

Prints the resulting size and SHA-256 so the download can be integrity-checked.
"""

import hashlib
import io
import os
import sys
import zipfile

SRC_ROOT = "bal"
DEFAULT_OUT = "bal-electrum-plugin.zip"

# CHANGELOG #219: the fixed metadata of every entry. A file's modification
# time changes with a fresh checkout or a save without changes, and the
# "made on" system and permissions differ between Windows and Linux: any of
# them would change the SHA-256 although the plugin is the same.
# 1980-01-01 is the earliest date the ZIP format can store.
FIXED_DATE_TIME = (1980, 1, 1, 0, 0, 0)
FIXED_CREATE_SYSTEM = 3  # Unix, so the permissions below are read as such
FIXED_EXTERNAL_ATTR = 0o100644 << 16  # a regular file, rw-r--r--
COMPRESS_LEVEL = 6


def fixed_entry(arc: str) -> zipfile.ZipInfo:
    """Return the ``ZipInfo`` of archive path ``arc`` with the fixed metadata.

    Only the name differs from one entry to another, so the archive depends
    on the paths and contents of the files alone.
    """
    info = zipfile.ZipInfo(arc, FIXED_DATE_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = FIXED_CREATE_SYSTEM
    info.external_attr = FIXED_EXTERNAL_ATTR
    return info


def compile_catalog(po_path: str) -> bytes:
    """Return the compiled ``.mo`` bytes of the gettext catalog ``po_path``.

    Fuzzy (unreviewed) entries are left out, as ``msgfmt`` does.
    """
    try:
        from babel.messages.mofile import write_mo
        from babel.messages.pofile import read_po
    except ImportError:
        raise SystemExit(
            "ERROR: Babel is needed to compile the translations "
            "(pip install babel)"
        ) from None
    with open(po_path, "rb") as f:
        catalog = read_po(f)
    buf = io.BytesIO()
    write_mo(buf, catalog, use_fuzzy=False)
    return buf.getvalue()


def build(out_path: str) -> None:
    if os.path.exists(out_path):
        os.remove(out_path)

    files = []
    for dirpath, dirnames, filenames in os.walk(SRC_ROOT):
        # prune cache dirs in place so os.walk does not descend into them
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for fn in filenames:
            # .mo files are rebuilt from the .po sources below; the .po/.pot
            # sources themselves are not shipped.
            if fn.endswith((".pyc", ".pyo", ".po", ".pot", ".mo")):
                continue
            files.append(os.path.join(dirpath, fn))
    catalogs = {}  # archive path of the .mo -> source .po
    for dirpath, _dirnames, filenames in os.walk(os.path.join(SRC_ROOT, "locale")):
        for fn in filenames:
            if fn.endswith(".po"):
                po = os.path.join(dirpath, fn)
                catalogs[po[: -len(".po")] + ".mo"] = po
    files.sort()

    with zipfile.ZipFile(
        out_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=COMPRESS_LEVEL
    ) as z:
        entries = [(f, None) for f in files] + [
            (mo, po) for mo, po in catalogs.items()
        ]
        for f, po in sorted(entries):
            arc = f.replace(os.sep, "/")  # forward slashes inside the archive
            if po is None:
                # Not z.write(): it would store the file's own mtime and mode.
                with open(f, "rb") as src:
                    data = src.read()
            else:
                data = compile_catalog(po)
            z.writestr(fixed_entry(arc), data, compresslevel=COMPRESS_LEVEL)

    # Integrity + summary
    with zipfile.ZipFile(out_path) as z:
        bad = z.testzip()
        if bad is not None:
            raise SystemExit(f"ERROR: corrupt entry in archive: {bad}")
        names = z.namelist()
        if not any(n.endswith("manifest.json") for n in names):
            raise SystemExit("ERROR: manifest.json missing from archive")

    data = open(out_path, "rb").read()
    print(f"built : {out_path}")
    print(f"files : {len(files) + len(catalogs)}")
    print(f"langs : {', '.join(sorted(os.path.basename(os.path.dirname(os.path.dirname(m))) for m in catalogs)) or '-'}")
    print(f"size  : {len(data)} bytes")
    print(f"sha256: {hashlib.sha256(data).hexdigest()}")


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_OUT
    build(out)
