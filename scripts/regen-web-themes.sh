#!/usr/bin/env bash
# Regenerate WorldMan web theme CSS into src/web_themes/.
# Source of truth: suite/worldman/themes (or SOPTOOLS_THEMES).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
THEMES="${SOPTOOLS_THEMES:-/home/cursor/soptools/suite/worldman/themes}"
GEN="$THEMES/scripts/generate-theme-css.mjs"
if [[ ! -f "$GEN" ]]; then
  echo "missing generator: $GEN" >&2
  echo "set SOPTOOLS_THEMES to the worldman themes tree" >&2
  exit 1
fi
mkdir -p "$ROOT/src/web_themes"
node "$GEN" --out "$ROOT/src/web_themes" --sets web
python3 - <<PY
from pathlib import Path
import json
root = Path("$THEMES")
rows = []
for line in (root / "catalog.tsv").read_text(encoding="utf-8").splitlines()[1:]:
    cols = line.split("\t")
    if len(cols) < 5:
        continue
    rows.append({
        "id": cols[0],
        "label": cols[1],
        "type": cols[2],
        "group": cols[3],
        "family": cols[4],
    })
out = Path("$ROOT/src/web_themes/catalog.json")
out.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
css = Path("$ROOT/src/web_themes/themes-web.css")
text = css.read_text(encoding="utf-8")
text = text.replace(str(root), "suite/worldman/themes")
css.write_text(text, encoding="utf-8")
print(f"wrote {len(rows)} themes; default dark-x-files")
PY
