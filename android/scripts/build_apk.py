#!/usr/bin/env python3
"""Build the BAL Reader Android APK via Gradle.

Re-synchronises the bundled codec modules into the app (so the APK always
carries the current ``bal/core`` sources), then invokes the Gradle wrapper to
produce the APK, and finally prints the artifact path, size and sha256.

Run from the repository root (any Python 3.8+, needs JDK 17 and an Android
SDK; the first build also needs a network connection for Gradle downloads):

    python3 android/scripts/build_apk.py             # debug APK
    python3 android/scripts/build_apk.py --release   # (unsigned) release APK
    python3 android/scripts/build_apk.py --no-sync   # skip codec re-sync
    python3 android/scripts/build_apk.py --offline   # gradle --offline

The APK is written under ``android/app/build/outputs/apk/``.
"""

import argparse
import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ANDROID_DIR = REPO_ROOT / "android"
GRADLEW = ANDROID_DIR / "gradlew"
LOCAL_PROPERTIES = ANDROID_DIR / "local.properties"
WRAPPER_JAR = ANDROID_DIR / "gradle" / "wrapper" / "gradle-wrapper.jar"

VARIANTS = {
    "debug": "assembleDebug",
    "release": "assembleRelease",
}
APK_REL = {
    "debug": Path("app") / "build" / "outputs" / "apk" / "debug" / "app-debug.apk",
    "release": Path("app") / "build" / "outputs" / "apk" / "release" / "app-release-unsigned.apk",
}

SYNC_SCRIPT = ANDROID_DIR / "scripts" / "sync_codecs.py"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def run(cmd, cwd, verbose: bool) -> int:
    if verbose:
        print("+", " ".join(str(c) for c in cmd))
    result = subprocess.run(
        cmd, cwd=str(cwd), capture_output=not verbose, text=True
    )
    if not verbose:
        sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)
    return result.returncode


def check_prerequisites() -> None:
    if not (GRADLEW.exists() and WRAPPER_JAR.exists()):
        sys.exit(
            "error: gradle wrapper is incomplete ({} missing).\n"
            "hint: run `gradle wrapper` in android/ once, or re-clone.".format(
                WRAPPER_JAR if not WRAPPER_JAR.exists() else GRADLEW
            )
        )

    java_ok = None
    try:
        out = subprocess.run(
            ["java", "-version"], capture_output=True, text=True, check=False
        ).stderr
        match = re.search(r'version "(?:1\.)?(\d+)', out)
        java_ok = int(match.group(1)) if match else None
    except FileNotFoundError:
        java_ok = None
    if java_ok is None:
        sys.exit("error: no JDK found on PATH (need JDK 17 for AGP 8.10).")
    if java_ok < 17:
        sys.exit("error: JDK {} on PATH, but the Android build needs JDK 17.".format(java_ok))

    sdk = None
    if LOCAL_PROPERTIES.exists():
        for line in LOCAL_PROPERTIES.read_text().splitlines():
            if line.startswith("sdk.dir="):
                sdk = line.split("=", 1)[1]
    sdk = sdk or os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
    if not sdk or not Path(sdk).exists():
        sys.exit(
            "error: Android SDK not found.\n"
            "hint: set sdk.dir in android/local.properties or ANDROID_HOME."
        )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--release",
        action="store_true",
        help="build the (unsigned) release APK instead of the debug APK",
    )
    parser.add_argument(
        "--no-sync",
        action="store_true",
        help="skip re-synchronising the bundled codec modules",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="pass --offline to Gradle (no dependency downloads)",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="run the Gradle clean task before building",
    )
    parser.add_argument(
        "--verbose", action="store_true", help="stream Gradle output"
    )
    args = parser.parse_args(argv)

    check_prerequisites()
    variant = "release" if args.release else "debug"

    if not args.no_sync:
        sync = subprocess.run(
            [sys.executable, str(SYNC_SCRIPT)], cwd=str(REPO_ROOT), check=False
        )
        if sync.returncode != 0:
            print("error: codec re-sync failed; refusing to build a stale APK.")
            return sync.returncode

    tasks = []
    if args.clean:
        tasks.append("clean")
    tasks.append(VARIANTS[variant])
    cmd = [str(GRADLEW)]
    if args.offline:
        cmd.append("--offline")
    cmd.extend(tasks)

    rc = run(cmd, cwd=ANDROID_DIR, verbose=args.verbose)
    if rc != 0:
        print("error: Gradle {} failed (exit {}).".format(" ".join(tasks), rc))
        return rc

    apk = ANDROID_DIR / APK_REL[variant]
    if not apk.exists():
        print("error: expected APK not found at {}".format(apk))
        return 1
    data = apk.read_bytes()
    print("APK  : {}".format(apk.relative_to(REPO_ROOT)))
    print("size : {} bytes".format(len(data)))
    print("sha256: {}".format(sha256(data)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
