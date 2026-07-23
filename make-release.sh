#!/usr/bin/env bash
# make-release.sh — Create a Gitea release for bal-electrum-plugin
#
# Usage:
#   ./make-release.sh           # read version from bal/manifest.json
#   ./make-release.sh v0.6.2    # bump manifest to 0.6.2, then release
#
# Requires: git, gpg, curl, python3, sha256sum
# Optional: ruff (lint skipped if not installed)
# Credentials: ~/.git-credentials or GITEA_USER / GITEA_TOKEN env vars

set -eo pipefail

# ── helpers ──────────────────────────────────────────────────────────
die()  { echo "Error: $*" >&2; exit 1; }
info() { echo ""; echo "── $* ──"; }

# ── 0. Resolve version ──────────────────────────────────────────────
MANIFEST="bal/manifest.json"
[ -f "$MANIFEST" ] || die "manifest not found: $MANIFEST"

# read current version from manifest
CURRENT_VER=$(python3 -c "import json; print(json.load(open('$MANIFEST'))['version'])")
[ -n "$CURRENT_VER" ] || die "cannot read version from $MANIFEST"

ARG="${1:-}"
if [ -n "$ARG" ]; then
    # normalise: accept "v0.6.2" or "0.6.2"
    NEW_VER="${ARG#v}"
    TAG="v${NEW_VER}"
else
    TAG="v${CURRENT_VER}"
    NEW_VER=""
fi

echo "=== Release ${TAG} ==="
echo "Current manifest version: ${CURRENT_VER}"
[ -n "$NEW_VER" ] && echo "New version (will bump):   ${NEW_VER}"

# ── 1. Bump version in manifest (if arg provided) ──────────────────
if [ -n "$NEW_VER" ] && [ "$NEW_VER" != "$CURRENT_VER" ]; then
    info "[1/10] Bumping version to ${NEW_VER} in ${MANIFEST}"
    python3 -c "
import json
f = open('${MANIFEST}')
d = json.load(f); f.close()
d['version'] = '${NEW_VER}'
json.dump(d, open('${MANIFEST}', 'w'), indent=4, ensure_ascii=False)
print(json.dumps(d, indent=4, ensure_ascii=False))
"
    git add "$MANIFEST"
else
    info "[1/10] Version already ${CURRENT_VER}, no bump needed"
fi

# ── 2. Clean caches ─────────────────────────────────────────────────
info "[2/10] Cleaning __pycache__ and .pyc"
find bal -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
find bal -name "*.pyc" -delete 2>/dev/null || true
find bal -name "*.pyo" -delete 2>/dev/null || true

# ── 3. Run tests ────────────────────────────────────────────────────
info "[3/10] Running test suite"
if QT_QPA_PLATFORM=offscreen PYTHONPATH=electrum-src python3 -m pytest \
      tests/test_core_*.py \
      tests/test_anticipate_past_locktime.py tests/test_anticipate_manual_locktime.py \
      tests/test_group_b_auto_sign.py tests/test_group_c_settings.py \
      tests/test_group_d_alarms.py tests/test_group_e_mock_karen7.py \
      tests/test_group_f_heir_change_rebuild.py tests/test_group_g_basic_calendar.py \
      tests/test_group_h_v048.py \
      -q 2>&1; then
    echo "All tests passed."
else
    die "Tests failed — aborting release."
fi

# ── 4. Lint (optional) ─────────────────────────────────────────────
info "[4/10] Lint with ruff"
if command -v ruff &>/dev/null; then
    RUFF_ERRORS=$(ruff check bal/ 2>&1 \
        | grep -oE "^[^ ]+\.py:[0-9]+:[0-9]+: [A-Z][0-9]+" \
        | grep -vE "F401|F403|F405|F841" || true)
    if [ -n "$RUFF_ERRORS" ]; then
        echo "New ruff errors:"
        echo "$RUFF_ERRORS"
        die "Lint errors found — fix before releasing."
    else
        echo "Lint clean (ignoring known pre-existing warnings)."
    fi
else
    echo "ruff not installed — skipping lint."
fi

# ── 5. Build ZIP via build_zip.py ───────────────────────────────────
info "[5/10] Building ZIP"
ZIP_NAME="bal_${TAG}.zip"
python3 build_zip.py "$ZIP_NAME"

# ── 6. GPG sign (armor + binary) + export public key ───────────────
info "[6/10] Signing with GPG"
GPG_KEY="A847D004DB91610711CA6A0DFE756706E833E0D1"
gpg --default-key "$GPG_KEY" --batch --yes --armor --detach-sign "$ZIP_NAME"
gpg --default-key "$GPG_KEY" --batch --yes       --detach-sign "$ZIP_NAME"
ASC_FILE="${ZIP_NAME}.asc"
SIG_FILE="${ZIP_NAME}.sig"
PGP_FILE="svatantrya.asc"
gpg --armor --export "$GPG_KEY" > "$PGP_FILE"
echo "  Signed: $ASC_FILE"
echo "  Signed: $SIG_FILE"
echo "  Public key: $PGP_FILE"

# ── 7. SHA-256 checksum ────────────────────────────────────────────
info "[7/10] Computing SHA-256"
SHA256_HASH=$(sha256sum "$ZIP_NAME" | cut -d' ' -f1)
echo "${SHA256_HASH}  ${ZIP_NAME}" > "${ZIP_NAME}.sha256"
echo "  SHA-256: ${SHA256_HASH}"

# ── 8. Pause for Electrum test ─────────────────────────────────────
info "[8/10] Test in Electrum (ZIP-FIRST policy)"
echo ""
echo "  ZIP ready: $(pwd)/${ZIP_NAME}"
echo ""
echo "  Install it in Electrum (Tools -> Plugins -> Install from file)."
echo "  IMPORTANT: fully restart Electrum (not just reload the plugin)."
echo ""
read -r -p "  Does the plugin work correctly in Electrum? [y/N] " CONFIRM
case "$CONFIRM" in
    [yY][eE][sS]|[yY]) echo "  Confirmed." ;;
    *) die "Aborted by user." ;;
esac

# ── 9. Git tag + push ──────────────────────────────────────────────
info "[9/10] Creating and pushing tag ${TAG}"
ORIGIN_URL="$(git remote get-url origin 2>/dev/null || true)"
[ -n "$ORIGIN_URL" ] || die "no git remote 'origin' found"

GITEA_HOST="$(echo "$ORIGIN_URL" | sed -n 's|https://\([^/]*\)/.*|\1|p')"
[ -n "$GITEA_HOST" ] || die "cannot parse Gitea host from origin URL"

TARGET_REPO="bitcoinafterlife/bal-electrum-plugin"

# credentials
GITEA_USER="${GITEA_USER:-}"
GITEA_TOKEN="${GITEA_TOKEN:-}"
if [ -z "$GITEA_USER" ] && [ -z "$GITEA_TOKEN" ]; then
    if [ -f ~/.git-credentials ]; then
        CREDS_LINE="$(grep "${GITEA_HOST}" ~/.git-credentials | head -n1)"
        if [ -n "$CREDS_LINE" ]; then
            CREDS="$(echo "$CREDS_LINE" | sed -n 's|https://\([^@]*\)@.*|\1|p')"
            GITEA_USER="$(echo "$CREDS" | cut -d: -f1)"
            GITEA_PASS="$(echo "$CREDS" | cut -d: -f2-)"
            GITEA_TOKEN="$GITEA_PASS"
        fi
    fi
fi
[ -n "$GITEA_TOKEN" ] || die "GITEA_TOKEN not set and no credentials in ~/.git-credentials"

API="https://$GITEA_HOST/gitea/api/v1"

# create annotated tag
git tag -d "$TAG" 2>/dev/null || true
git tag -a "$TAG" -m "$TAG" HEAD

# push tag
REMOTE_NAME="gitea-target"
REMOTE_URL="https://$GITEA_USER:$GITEA_TOKEN@$GITEA_HOST/gitea/$TARGET_REPO.git"
git remote rm "$REMOTE_NAME" 2>/dev/null || true
git remote add "$REMOTE_NAME" "$REMOTE_URL"
echo "  Pushing tag ${TAG} to ${TARGET_REPO}..."
git push "$REMOTE_NAME" "$TAG" --force

# ── 10. Create release + upload assets ──────────────────────────────
info "[10/10] Creating Gitea release"

RELEASE_BODY=$(python3 -c "
import json

sha256  = '${SHA256_HASH}'
zip_name = '${ZIP_NAME}'
asc_name = '${ASC_FILE}'
sig_name = '${SIG_FILE}'
pgp_file = '${PGP_FILE}'
tag      = '${TAG}'

body = f'''Release {tag}

## SHA-256 Checksum

\`\`\`
{sha256}  {zip_name}
\`\`\`

### Verify SHA-256

\`\`\`bash
sha256sum -c {zip_name}.sha256
\`\`\`

## Download

- \`{zip_name}\` - Plugin BAL {tag}
- \`{asc_name}\` - GPG signature (armor)
- \`{sig_name}\` - GPG signature (binary)
- \`{pgp_file}\` - Signing public key ([also available online](https://bitcoin-after.life/svatantrya.asc))

## GPG Verification

### Import the signing key

\`\`\`bash
gpg --fetch-key https://bitcoin-after.life/svatantrya.asc
\`\`\`

Or download \`{pgp_file}\` from the assets above:

\`\`\`bash
gpg --import {pgp_file}
\`\`\`

### Verify the signature (armor)

\`\`\`bash
gpg --verify {asc_name} {zip_name}
\`\`\`

### Verify the signature (binary)

\`\`\`bash
gpg --verify {sig_name} {zip_name}
\`\`\`

Expected output:
\`\`\`
gpg: Good signature from "Svātantrya <svatantrya@bitcoin-after.life>"
\`\`\`

Fingerprint: \`A847D004DB91610711CA6A0DFE756706E833E0D1\`
Public key: https://bitcoin-after.life/svatantrya.asc'''

print(json.dumps({'body': body}, ensure_ascii=False))
")

RESPONSE=$(curl -s -w "\n%{http_code}" -H "Authorization: token $GITEA_TOKEN" \
    -X POST "${API}/repos/${TARGET_REPO}/releases" \
    -H "Content-Type: application/json" \
    -d "{\"tag_name\":\"${TAG}\",\"name\":\"${TAG}\",\"body\":${RELEASE_BODY},\"draft\":false,\"prerelease\":false}")

HTTP_CODE=$(echo "$RESPONSE" | tail -1)
BODY=$(echo "$RESPONSE" | sed '$d')

if [ "$HTTP_CODE" != "201" ]; then
    echo "Error creating release: HTTP $HTTP_CODE"
    echo "$BODY"
    exit 1
fi

RELEASE_ID=$(echo "$BODY" | python3 -c "import json,sys; print(json.load(sys.stdin)['id'])")
HTML_URL=$(echo "$BODY" | python3 -c "import json,sys; print(json.load(sys.stdin)['html_url'])")
echo "  Release created: $HTML_URL (ID: $RELEASE_ID)"

# upload assets
for FILE in "$ZIP_NAME" "$ASC_FILE" "$SIG_FILE" "${ZIP_NAME}.sha256" "$PGP_FILE"; do
    BASENAME=$(basename "$FILE")
    echo "  Uploading $BASENAME ..."
    UPLOAD=$(curl -s -w "\n%{http_code}" -H "Authorization: token $GITEA_TOKEN" \
        -X POST "${API}/repos/${TARGET_REPO}/releases/${RELEASE_ID}/assets" \
        -F "attachment=@${FILE}" -F "name=${BASENAME}")
    UPLOAD_CODE=$(echo "$UPLOAD" | tail -1)
    if [ "$UPLOAD_CODE" == "201" ]; then
        echo "    OK ($BASENAME)"
    else
        echo "    FAILED ($BASENAME) - HTTP $UPLOAD_CODE"
    fi
done

echo ""
echo "=== Done ==="
echo "Release: $HTML_URL"
echo "Assets:"
echo "  ${ZIP_NAME}"
echo "  ${ASC_FILE}"
echo "  ${SIG_FILE}"
echo "  ${ZIP_NAME}.sha256"
echo "  ${PGP_FILE}"
echo "SHA-256: ${SHA256_HASH}"
