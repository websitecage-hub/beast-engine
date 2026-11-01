#!/usr/bin/env bash
# clean_home.sh - keep the 4.8 GB /home mount from filling up.
#
# Only removes things that are regenerable or already delivered. It never touches
# source, research JSON, secrets, or the Hermes runtime.
#
#   ./scripts/clean_home.sh          # report + safe prune
#   ./scripts/clean_home.sh --dry    # report only
set -euo pipefail

HOME_DIR="${HOME}"
DRY=0
[ "${1:-}" = "--dry" ] && DRY=1
run() { if [ "$DRY" = "1" ]; then echo "  would: $*"; else eval "$@"; fi; }

echo "== before =="
df -h "$HOME_DIR" | tail -1

echo "== top consumers =="
du -sh "$HOME_DIR"/* "$HOME_DIR"/.[a-z]* 2>/dev/null | sort -h | tail -10

# 1. Delivered previews: keep the two newest per format.
echo "== previews =="
if [ -d "$HOME_DIR/beast-engine/outputs/previews" ]; then
  for fmt in caption_list_title_v1 social_moment_loop_v1; do
    # shellcheck disable=SC2012
    ls -t "$HOME_DIR/beast-engine/outputs/previews/${fmt}"-*.mp4 2>/dev/null \
      | tail -n +3 | while read -r f; do
          echo "  old preview: $(basename "$f")"
          run "rm -f '$f'"
        done
  done
fi

# 2. Hermes logs older than 14 days.
if [ -d "$HOME_DIR/.hermes/logs" ]; then
  echo "== hermes logs >14d =="
  while IFS= read -r f; do
    echo "  old log: $f"
    run "rm -f '$f'"
  done < <(find "$HOME_DIR/.hermes/logs" -type f -mtime +14 2>/dev/null | head -30)
fi

# 3. Regenerable caches inside home.
echo "== caches =="
for d in "$HOME_DIR/.cache/cloud-code" "$HOME_DIR/.npm/_cacache" "$HOME_DIR/.cache/pip"; do
  [ -e "$d" ] && { echo "  $d"; run "rm -rf '$d'"; }
done

# 4. Scratch that should never have been in home in the first place.
for d in "$HOME_DIR/beast-engine/tmp" "$HOME_DIR/beast-engine/outputs/proof"; do
  [ -e "$d" ] && { echo "  stale scratch: $d"; run "rm -rf '$d'"; }
done

echo "== after =="
df -h "$HOME_DIR" | tail -1

if [ "$(df --output=pcent "$HOME_DIR" | tail -1 | tr -dc '0-9')" -ge 90 ]; then
  echo "WARNING: still >=90%. Move a project to an external disk, or archive"
  echo "         outputs/ to /var/tmp (ephemeral) before the next render."
  exit 3
fi