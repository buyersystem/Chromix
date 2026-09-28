#!/usr/bin/env bash
# Apply the Chromix patch series onto a Chromium src tree.
# Usage: build/apply-patches.sh /path/to/chromium/src
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="${1:?usage: apply-patches.sh /path/to/chromium/src}"
PLATFORM="${2:-${CHROMIX_PATCH_PLATFORM:-}}"
if [ -z "$PLATFORM" ]; then
  case "$(uname -s)" in
    Linux) PLATFORM=linux ;;
    Darwin) PLATFORM=macos ;;
    MINGW*|MSYS*|CYGWIN*) PLATFORM=windows ;;
    *) echo "explicit patch platform required" >&2; exit 2 ;;
  esac
fi
SELECTED="$(python3 "$REPO/tools/patch_selection.py" --repo "$REPO" --platform "$PLATFORM" --src "$SRC" --paths)"
cd "$SRC" || { echo "no such src tree: $SRC" >&2; exit 1; }
ok=0
while IFS= read -r line || [ -n "$line" ]; do
  rel="$(printf '%s' "${line%%#*}" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"
  [ -z "$rel" ] && continue
  patch="$REPO/$rel"
  [ -f "$patch" ] || { echo "patch listed in series is missing: $rel" >&2; exit 1; }
  printf '  [apply] %s\n' "$(basename "$rel")"
  patch_bin="${PATCH_BIN:-$(command -v gpatch || command -v patch)}"
  "$patch_bin" -p1 --fuzz=0 --batch --forward --get=0 --no-backup-if-mismatch --reject-file=- -i "$patch"
  ok=$((ok + 1))
done <<< "$SELECTED"

printf '%s\n' "----------------------------------------------"
printf 'applied: %s\n' "$ok"
printf '%s\n' 'All Chromix patches applied cleanly.'
