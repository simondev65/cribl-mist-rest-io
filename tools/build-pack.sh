#!/usr/bin/env bash
#
# Build the installable .crbl for cribl-mist-rest-io.
#
#   bash tools/build-pack.sh
#
# A .crbl is a gzipped tar. It MUST be built with GNU tar: macOS/BSD tar writes
# extended pax headers and AppleDouble entries that Cribl's importer rejects, and
# the failure looks like a corrupt archive rather than a tar dialect problem.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if ! command -v gtar >/dev/null 2>&1; then
  echo "error: gtar (GNU tar) not found." >&2
  echo "       Install it with: brew install gnu-tar" >&2
  echo "       Do NOT substitute macOS/BSD tar - Cribl cannot install those archives." >&2
  exit 1
fi

NAME="$(python3 -c 'import json;print(json.load(open("package.json"))["name"])')"
VERSION="$(python3 -c 'import json;print(json.load(open("package.json"))["version"])')"

# package.json version and default/pack.yml version must agree - Cribl uses them
# to decide whether an installed pack needs upgrading.
PACK_VERSION="$(python3 -c 'import yaml;print(yaml.safe_load(open("default/pack.yml"))["version"])')"
if [[ "$VERSION" != "$PACK_VERSION" ]]; then
  echo "error: version mismatch - package.json=$VERSION default/pack.yml=$PACK_VERSION" >&2
  exit 1
fi

PACK_ID="$(python3 -c 'import yaml;print(yaml.safe_load(open("default/pack.yml"))["id"])')"
if [[ "$NAME" != "$PACK_ID" ]]; then
  echo "error: id mismatch - package.json name=$NAME default/pack.yml id=$PACK_ID" >&2
  exit 1
fi

# Every YAML file must parse before we ship it.
python3 - <<'PY'
import glob, sys, yaml
bad = 0
for path in sorted(glob.glob('default/**/*.yml', recursive=True)):
    try:
        yaml.safe_load(open(path))
    except Exception as exc:
        bad += 1
        print(f'error: {path}: {exc}', file=sys.stderr)
if bad:
    sys.exit(1)
print('  yaml ok')
PY

# Validate collector and Function confs against the schemas shipped inside a real
# Cribl install. Skips itself if there is no install / no jsonschema module - see
# the header of tools/validate-schema.py.
python3 tools/validate-schema.py

# And the offline dry run must pass in both output formats.
if command -v node >/dev/null 2>&1; then
  node tools/simulate.js      >/dev/null && echo "  simulate json ok"
  node tools/simulate.js ocsf >/dev/null && echo "  simulate ocsf ok"
else
  echo "  warning: node not found, skipping tools/simulate.js" >&2
fi

OUT="${NAME}_${VERSION}.crbl"
rm -f "$OUT"

gtar -czf "$OUT" \
  --exclude '.DS_Store' \
  --exclude '._*' \
  --exclude '__pycache__' \
  package.json README.md default data

echo
echo "built $OUT ($(du -h "$OUT" | cut -f1))"
echo "install: Cribl Stream > Processing > Packs > Add Pack > Import from File"
gtar -tzf "$OUT" | head -20
