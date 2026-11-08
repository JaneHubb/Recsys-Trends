#!/usr/bin/env bash
# Assemble the static site: site/ + data/{summary,status,papers} → _site/
# Usage: bash scripts/build_site.sh [data root, default .]   (pass "sample" to use sample/data)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BASE="${1:-$ROOT}"
[ -d "$BASE/data" ] || BASE="$ROOT/$BASE"
OUT="$ROOT/_site"
rm -rf "$OUT"
mkdir -p "$OUT/data/papers"
cp -r "$ROOT/site/." "$OUT/"
for f in summary.json status.json; do
  [ -f "$BASE/data/$f" ] && cp "$BASE/data/$f" "$OUT/data/$f"
done
if compgen -G "$BASE/data/papers/*.json" > /dev/null; then
  cp "$BASE/data/papers/"*.json "$OUT/data/papers/"
fi
touch "$OUT/.nojekyll"
echo "built: $OUT ($(find "$OUT" -type f | wc -l) files)"
