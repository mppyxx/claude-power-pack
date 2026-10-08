#!/usr/bin/env bash
# Turn Claude Power Pack's automatic updates on or off, see their status, or update right now.
#
#   bash ~/.claude/power-pack/auto-update.sh status
#   bash ~/.claude/power-pack/auto-update.sh off
#   bash ~/.claude/power-pack/auto-update.sh on
#   bash ~/.claude/power-pack/auto-update.sh now     (check GitHub and update immediately)
set -u
CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
STATE="$CLAUDE_DIR/power-pack"
MANIFEST="$STATE/manifest.json"
[ -f "$MANIFEST" ] || { echo "Claude Power Pack isn't installed here (no $MANIFEST)."; exit 1; }

set_flag() {
  python3 - "$MANIFEST" "$1" <<'PY'
import json, sys
p, v = sys.argv[1], sys.argv[2] == "on"
d = json.load(open(p)); d["auto_update"] = v
json.dump(d, open(p, "w"), indent=2, sort_keys=True); open(p, "a").write("\n")
PY
}

case "${1:-status}" in
  on)  set_flag on;  echo "Automatic updates are on. Claude Power Pack checks GitHub once a day in the background." ;;
  off) set_flag off; echo "Automatic updates are off. Run 'bash $0 now' whenever you want to update by hand." ;;
  now)
    bash "$STATE/updater.sh" --now
    if [ -s "$STATE/notice.md" ]; then cat "$STATE/notice.md"; else tail -n 1 "$STATE/update.log" 2>/dev/null; fi ;;
  status)
    get() { python3 "$STATE/pack_state.py" get --manifest "$MANIFEST" "$1"; }
    echo "version:      $(get version)"
    echo "auto-update:  $(get auto_update)"
    if [ -f "$STATE/last-check" ]; then
      echo "last check:   $(date -r "$STATE/last-check" '+%Y-%m-%d %H:%M' 2>/dev/null || stat -c %y "$STATE/last-check" 2>/dev/null | cut -c1-16)"
    else
      echo "last check:   never"
    fi
    echo "log:          $STATE/update.log" ;;
  *) sed -n '2,8p' "$0"; exit 2 ;;
esac
