#!/usr/bin/env python3
"""Synchronise the BAL QR codec modules into the Android app.

Copies ``bal/core/{__init__,animated_qr,qrtransfer}.py`` from the plugin repo
into ``android/app/src/main/python/bal/core/`` so Chaquopy ships exactly the
same code the desktop plugin runs. Afterwards the copies are imported
standalone and used for one quick encode/decode round trip.

Run from the repository root (any Python 3.8+, no dependencies):

    python3 android/scripts/sync_codecs.py
    python3 android/scripts/sync_codecs.py --check   # no writes

Re-run whenever ``bal/core/animated_qr.py`` or ``bal/core/qrtransfer.py``
changes; the bundled copies are committed for deterministic builds.
"""

import argparse
import hashlib
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CORE_SRC = REPO_ROOT / "bal" / "core"
PYTHON_DEST = REPO_ROOT / "android" / "app" / "src" / "main" / "python"
BAL_CORE_DEST = PYTHON_DEST / "bal" / "core"

FILES = ("__init__.py", "animated_qr.py", "qrtransfer.py")

ROUNDTRIP = (
    "import sys; "
    "sys.path.insert(0, {dest!r}); "
    "from bal.core.animated_qr import AnimatedQrSession, ur2_frames; "
    "import bal.core.qrtransfer as qtf; "
    "frames = ur2_frames(b'roundtrip-check', 400); "
    "assert frames, 'no frames produced'; "
    "s = AnimatedQrSession(); "
    "assert all(s.add_part(f) == 'ok' for f in frames); "
    "transfer, compressed = s.resolve(); "
    "assert not compressed and transfer == 'roundtrip-check', 'roundtrip failed'; "
    "print('standalone import + roundtrip OK'); "
)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def check_up_to_date() -> int:
    outdated = []
    for name in FILES:
        src = (CORE_SRC / name).read_bytes()
        dst = BAL_CORE_DEST / name
        if not dst.exists() or dst.read_bytes() != src:
            outdated.append(name)
    if outdated:
        print("OUT OF DATE: {}".format(", ".join(outdated)))
        print("run: python3 android/scripts/sync_codecs.py")
        return 1
    print("codec bundles are up to date")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the bundled copies are up to date without writing",
    )
    args = parser.parse_args(argv)

    if args.check:
        return check_up_to_date()

    BAL_CORE_DEST.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        src = (CORE_SRC / name).read_bytes()
        dst = BAL_CORE_DEST / name
        dst.write_bytes(src)
        print("synced {:16s} sha256={}".format(name, sha256(src)[:16]))

    run = subprocess.run(
        [sys.executable, "-c", ROUNDTRIP.format(dest=str(PYTHON_DEST))],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if run.returncode != 0:
        print(run.stdout, end="")
        print(run.stderr, end="")
        return 1
    print(run.stdout.strip())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
