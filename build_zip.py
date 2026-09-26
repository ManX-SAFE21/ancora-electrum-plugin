#!/usr/bin/env python3
"""Build a clean, zipimport-friendly distribution archive of the BAL plugin.

Electrum loads external plugins from a ``.zip`` using Python's ``zipimport``.
``zipimport`` is picky about the archive layout, so this builder deliberately:

  * writes **only files** (no explicit directory entries) — some Electrum
    portable builds choke on directory records inside the archive;
  * uses standard DEFLATE compression (well supported by ``zipimport``);
  * emits entries in a deterministic, sorted order so the archive is
    reproducible (stable SHA-256);
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
import time
import zipfile

SRC_ROOT = "bal"
DEFAULT_OUT = "bal-electrum-plugin.zip"


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
        out_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
    ) as z:
        entries = [(f, None) for f in files] + [
            (mo, po) for mo, po in catalogs.items()
        ]
        for f, po in sorted(entries):
            arc = f.replace(os.sep, "/")  # forward slashes inside the archive
            if po is None:
                z.write(f, arc)
            else:
                # Same timestamp rule as z.write(): the source file's mtime.
                info = zipfile.ZipInfo(arc, time.localtime(os.path.getmtime(po))[:6])
                info.compress_type = zipfile.ZIP_DEFLATED
                z.writestr(info, compile_catalog(po))

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
