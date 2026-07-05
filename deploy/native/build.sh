#!/usr/bin/env bash
# Build + publish the ce-sensor-camera NATIVE deploy vehicle: a single-file Python zipapp
# (.pyz) shipped as a `native` ceapp so it runs on a node that predates the `script` runtime
# tier (and can't self-update over a GitHub-blocked network). One executable file = one
# content-addressed blob; the board runs it directly with app:install/app:native.
#
# Usage: deploy/native/build.sh [registry]   (registry default https://ce-net.com)
set -euo pipefail
cd "$(dirname "$0")/../.."
REG="${1:-https://ce-net.com}"

STAGE="$(mktemp -d)"
cp main.py ce.py capauth.py "$STAGE"/
mkdir -p "$STAGE/camera"
cp camera/__init__.py camera/service.py camera/source.py camera/frame.py "$STAGE/camera"/
PYZ="$(mktemp -d)/ce-sensor-camera.pyz"
python3 -m zipapp "$STAGE" -o "$PYZ" -m "main:main" -p "/usr/bin/env python3"
rm -rf "$STAGE"

HEX="$(shasum -a 256 "$PYZ" | awk '{print $1}')"
echo "pyz sha256=$HEX size=$(wc -c <"$PYZ")"

# Content-addressed upload (unauthenticated; the install side re-hashes on fetch).
curl -fsS -X PUT "$REG/blobs/$HEX" --data-binary @"$PYZ" -H 'content-type: application/octet-stream' >/dev/null
echo "uploaded $REG/blobs/$HEX"

# Pin the digest into the native manifest, then publish manifest + signature.
python3 - "$HEX" deploy/native/ceapp.toml <<'PY'
import sys, re
hexd, path = sys.argv[1], sys.argv[2]
t = open(path).read()
t = re.sub(r'"linux-arm64"\s*=\s*"sha256:[0-9a-f]*"', f'"linux-arm64" = "sha256:{hexd}"', t)
open(path, "w").write(t)
PY
ce app publish deploy/native/ceapp.toml --registry "$REG"
echo "Now: (from a dir WITHOUT a ./ce-sensor-camera folder)  ce app install ce-sensor-camera --on node=camnode --yes"
