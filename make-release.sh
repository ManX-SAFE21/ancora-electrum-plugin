#!/usr/bin/env bash
# make-release.sh — prepare an Àncòra release (CHANGELOG #220)
#
# Usage (from the repository root, in Linux or WSL):
#   ELECTRUM_SRC=/path/to/electrum ./make-release.sh
#
# The version comes from bal/manifest.json: change it there in a normal step
# (branch, commit, merge) before running this script. The script never edits
# a tracked file.
#
# What it does, stopping at the first problem:
#   1. checks: clean working tree, tag ancora-v<version> not used yet (here
#      and on origin), Babel, ruff, gpg, the public key file;
#   2. the whole test suite, in the normal and in the reverse file order, and
#      ruff on bal/ and tests/;
#   3. builds dist/ancora_<version>.zip TWICE and checks both have the same
#      SHA-256 (the build is reproducible, #219), then writes the .sha256 file;
#   4. the owner signs the ZIP in PowerShell with the SAFE21dev key (the
#      private key never leaves Gpg4win); the script checks the signature
#      against SAFE21dev.asc;
#   5. asks whether the ZIP was tried in Electrum (ZIP first);
#   6. creates the annotated tag and, after one more "yes", pushes it to
#      origin (never --force);
#   7. writes dist/RELEASE-NOTES-ancora-v<version>.md for the GitHub release,
#      which the owner creates on the website and uploads the files to.
#
# Requires: git, python3 with Babel and pytest, ruff, gpg, sha256sum.
# Environment:
#   ELECTRUM_SRC  Electrum source checkout used by the tests (required)
#   PYTHON        Python interpreter (default: python3)
#   RUFF          ruff executable (default: ruff)
#   TEST_ORDERS   "both" (default) or "normal": "normal" is only for trying
#                 the script itself, a real release runs both orders
#   TEST_PATHS    test files or folders (default: tests/); same note
#   TAGGER_NAME / TAGGER_EMAIL  who signs off the tag (default: ManX,
#                 info@safe21.io, as the project's commits)

set -eo pipefail

# ── helpers ──────────────────────────────────────────────────────────
die()  { echo "Error: $*" >&2; exit 1; }
info() { echo ""; echo "── $* ──"; }
# ask_yes "question": true only on y/yes (any case); anything else is no.
ask_yes() {
    local answer
    read -r -p "  $1 [y/N] " answer
    case "$answer" in [yY]|[yY][eE][sS]) return 0 ;; *) return 1 ;; esac
}

PYTHON="${PYTHON:-python3}"
RUFF="${RUFF:-ruff}"
TEST_ORDERS="${TEST_ORDERS:-both}"
TEST_PATHS="${TEST_PATHS:-tests/}"
TAGGER_NAME="${TAGGER_NAME:-ManX}"
TAGGER_EMAIL="${TAGGER_EMAIL:-info@safe21.io}"

MANIFEST="bal/manifest.json"
DIST="dist"
PUBKEY_FILE="SAFE21dev.asc"
# The SAFE21dev signing key (owner's Gpg4win keyring). A signature is only
# accepted when it is made by this primary key.
SIGN_FPR="33E3393DFB10F4C45AE6F1E8206C20114CA96172"
SIGN_KEYID="206C20114CA96172"
REPO_URL="https://github.com/ManX-SAFE21/ancora-electrum-plugin"

# ── 1. Checks ───────────────────────────────────────────────────────
info "[1/7] Checks"
[ -f "$MANIFEST" ] || die "run the script from the repository root ($MANIFEST not found)"
VERSION=$("$PYTHON" -c "import json; print(json.load(open('$MANIFEST'))['version'])")
[ -n "$VERSION" ] || die "cannot read the version from $MANIFEST"
# Àncòra's own tag prefix, so a tag is never confused with BAL's vX.Y.Z.
TAG="ancora-v${VERSION}"
ZIP_NAME="ancora_${VERSION}.zip"
ZIP="$DIST/$ZIP_NAME"
echo "  Version: $VERSION   Tag: $TAG   Branch: $(git rev-parse --abbrev-ref HEAD)"

[ -n "$ELECTRUM_SRC" ] || die "set ELECTRUM_SRC to the Electrum source checkout used by the tests"
[ -d "$ELECTRUM_SRC/electrum" ] || die "no Electrum source in $ELECTRUM_SRC"
# The release is made from committed code only: what is tagged is what is built.
[ -z "$(git status --porcelain)" ] || die "the working tree has changes: commit or remove them first"
# A tag is never moved or overwritten: a used version needs a new number.
git rev-parse -q --verify "refs/tags/$TAG" >/dev/null && die "tag $TAG already exists here"
git ls-remote --exit-code --tags origin "refs/tags/$TAG" >/dev/null 2>&1 \
    && die "tag $TAG already exists on origin"
# Babel compiles the translations into the ZIP (build_zip.py) and the i18n
# tests need it: check it before the long test run.
"$PYTHON" -c "import babel" 2>/dev/null || die "Babel is missing for $PYTHON (pip install babel)"
command -v "$RUFF" >/dev/null || die "ruff not found (set RUFF)"
command -v gpg >/dev/null || die "gpg not found"
command -v sha256sum >/dev/null || die "sha256sum not found"
[ -f "$PUBKEY_FILE" ] || die "$PUBKEY_FILE (the public signing key) not found"
echo "  OK"

# ── 2. Tests and lint ───────────────────────────────────────────────
info "[2/7] Test suite and ruff"
find bal tests -name "__pycache__" -type d -prune -exec rm -rf {} + 2>/dev/null || true
run_tests() {
    QT_QPA_PLATFORM=offscreen PYTHONPATH="$ELECTRUM_SRC" "$PYTHON" -m pytest -q "$@" \
        || die "tests failed — aborting the release"
}
echo "  Normal order: $TEST_PATHS"
# shellcheck disable=SC2086  # TEST_PATHS may hold several paths
run_tests $TEST_PATHS
if [ "$TEST_ORDERS" = "both" ]; then
    # The same files in the reverse order: a test that passes only after
    # another one shows up here. The two name patterns are pytest's default
    # ones, so this is the same set of files as the normal run.
    # shellcheck disable=SC2086
    mapfile -t REVERSED < <(find $TEST_PATHS \( -name "test_*.py" -o -name "*_test.py" \) | sort -r)
    echo "  Reverse order: ${#REVERSED[@]} files"
    run_tests "${REVERSED[@]}"
else
    echo "  Reverse order SKIPPED (TEST_ORDERS=$TEST_ORDERS): not for a real release"
fi
"$RUFF" check bal/ tests/ || die "ruff found problems — fix them first"

# ── 3. Build the ZIP twice ──────────────────────────────────────────
info "[3/7] Building $ZIP (twice: the two must be identical)"
mkdir -p "$DIST"
rm -f "$ZIP" "$ZIP.sha256" "$ZIP.asc" "$DIST/check_$ZIP_NAME"
"$PYTHON" build_zip.py "$ZIP"
"$PYTHON" build_zip.py "$DIST/check_$ZIP_NAME" >/dev/null
cmp -s "$ZIP" "$DIST/check_$ZIP_NAME" || die "two builds differ: the ZIP is not reproducible"
rm -f "$DIST/check_$ZIP_NAME"
# sha256sum -c reads the file name next to the hash: run it from dist/.
(cd "$DIST" && sha256sum "$ZIP_NAME" > "$ZIP_NAME.sha256")
SHA256=$(cut -d' ' -f1 "$ZIP.sha256")
echo "  SHA-256: $SHA256"

# ── 4. Signature ────────────────────────────────────────────────────
info "[4/7] Signature (by the owner, in PowerShell)"
ZIP_WIN="$ZIP"
command -v wslpath >/dev/null && ZIP_WIN=$(wslpath -w "$ZIP")
echo "  Run this in PowerShell (Gpg4win asks the key's password):"
echo ""
echo "    & \"C:\\Program Files\\GnuPG\\bin\\gpg.exe\" --local-user $SIGN_KEYID --armor --detach-sign \"$ZIP_WIN\""
echo ""
read -r -p "  Press Enter when $ZIP_NAME.asc is there... " _
[ -f "$ZIP.asc" ] || die "$ZIP.asc not found"
# Verify in an empty keyring that holds only SAFE21dev.asc, so the result
# does not depend on the keys of this computer.
VERIFY_HOME=$(mktemp -d)
trap 'rm -rf "$VERIFY_HOME"' EXIT
GNUPGHOME="$VERIFY_HOME" gpg --batch --quiet --import "$PUBKEY_FILE" 2>/dev/null
GNUPGHOME="$VERIFY_HOME" gpg --batch --with-colons --fingerprint 2>/dev/null \
    | grep -q "^fpr:::::::::${SIGN_FPR}:" || die "$PUBKEY_FILE is not the SAFE21dev key $SIGN_FPR"
# VALIDSIG ends with the fingerprint of the PRIMARY key: the signature must be
# good AND made by SAFE21dev.
GNUPGHOME="$VERIFY_HOME" gpg --batch --status-fd 1 --verify "$ZIP.asc" "$ZIP" 2>/dev/null \
    | grep -q "^\[GNUPG:\] VALIDSIG .* ${SIGN_FPR}$" || die "the signature is not a good SAFE21dev signature"
echo "  Good signature by SAFE21dev ($SIGN_FPR)"

# ── 5. Try the ZIP in Electrum ──────────────────────────────────────
info "[5/7] Try the ZIP in Electrum (ZIP first)"
echo "  ZIP: $ZIP_WIN"
echo "  Install it (Tools -> Plugins), restart Electrum fully, try it."
ask_yes "Does the plugin work correctly in Electrum?" || die "stopped by the owner"

# ── 6. Tag ──────────────────────────────────────────────────────────
info "[6/7] Tag $TAG"
# The tagger is given here, as for the project's commits: the computer that
# runs the script may have no git identity set.
git -c user.name="$TAGGER_NAME" -c user.email="$TAGGER_EMAIL" \
    tag -a "$TAG" -m "Àncòra $VERSION" HEAD
echo "  Created $TAG on $(git rev-parse --short HEAD)"
if ask_yes "Push $TAG to origin now?"; then
    git push origin "refs/tags/$TAG"
else
    echo "  Not pushed. Later: git push origin refs/tags/$TAG"
fi

# ── 7. Release notes ────────────────────────────────────────────────
info "[7/7] Release notes"
NOTES="$DIST/RELEASE-NOTES-$TAG.md"
cat > "$NOTES" <<EOF
# Àncòra $VERSION

*Àncora per te, ancòra per loro.*

<!-- Write here what is new in this release. -->

## Download and verify

Files: \`$ZIP_NAME\`, \`$ZIP_NAME.sha256\`, \`$ZIP_NAME.asc\` (signature),
\`$PUBKEY_FILE\` (the public signing key, also in the repository).

SHA-256:

\`\`\`
$SHA256  $ZIP_NAME
\`\`\`

\`\`\`bash
sha256sum -c $ZIP_NAME.sha256
gpg --import $PUBKEY_FILE
gpg --verify $ZIP_NAME.asc $ZIP_NAME
\`\`\`

Expected: \`Good signature from "SAFE21dev <info@safe21.io>"\`, key fingerprint
\`$SIGN_FPR\`.

The ZIP is reproducible: \`python3 build_zip.py\` on the tag \`$TAG\` gives the
same SHA-256.

## Install

Requires **Electrum 4.8.3 or later**.

In Electrum: Tools -> Plugins, add the ZIP, enable **Àncòra (Safe21)** and restart
Electrum.

## Update from an earlier version

In Electrum: Tools -> Plugins, add the new ZIP. Electrum replaces the old one and asks
to restart: restart Electrum (until then the old version keeps running). The heirs,
the will and the settings are kept.
EOF
echo "  $NOTES"

echo ""
echo "=== Done ==="
echo "Create the release on $REPO_URL/releases/new with tag $TAG,"
echo "paste $NOTES and upload:"
echo "  $ZIP"
echo "  $ZIP.sha256"
echo "  $ZIP.asc"
echo "  $PUBKEY_FILE"
