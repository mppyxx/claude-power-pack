#!/usr/bin/env bash
# Claude Power Pack session-start hook. Kept tiny so Claude Code never starts slower:
#  1. If the last background update left a note, hand it to Claude once (so it can tell you what changed).
#  2. Start the updater in the background; it checks GitHub at most once a day.
CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
STATE="$CLAUDE_DIR/power-pack"
[ -d "$STATE" ] || exit 0

if [ -s "$STATE/notice.md" ] && command -v python3 >/dev/null 2>&1; then
  mv "$STATE/notice.md" "$STATE/notice.shown.md" 2>/dev/null
  python3 - "$STATE/notice.shown.md" <<'PY'
import json, sys
text = open(sys.argv[1]).read().strip()
msg = text + "\n\nMention this update to the user in one or two short lines at a natural moment, then carry on."
print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": msg}}))
PY
fi

if [ -f "$STATE/updater.sh" ]; then
  nohup bash "$STATE/updater.sh" >/dev/null 2>&1 </dev/null &
fi
exit 0
