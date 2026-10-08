#!/usr/bin/env bash
# Checks what the Claude Power Pack installed and what still needs the user's hands.
# usage: bash tools/verify.sh
set -u
PACK="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
ok()   { printf '  ok       %s\n' "$*"; }
miss() { printf '  MISSING  %s\n' "$*"; FAIL=$((FAIL + 1)); }
opt()  { printf '  todo     %s\n' "$*"; TODO=$((TODO + 1)); }
FAIL=0; TODO=0
have() { command -v "$1" >/dev/null 2>&1; }

echo "Skills"
total=0; missing=""
for d in "$PACK"/skills/*/; do
  n="$(basename "$d")"; total=$((total + 1))
  [ -f "$CLAUDE_DIR/skills/$n/SKILL.md" ] || missing="$missing $n"
done
if [ -f "$PACK/skills-upstream.txt" ]; then
  while read -r n _ kind _; do
    case "$n" in ''|\#*) continue ;; esac
    [ "$kind" = extra ] && continue
    total=$((total + 1))
    [ -f "$CLAUDE_DIR/skills/$n/SKILL.md" ] || missing="$missing $n"
  done < "$PACK/skills-upstream.txt"
fi
if [ -z "$missing" ]; then ok "all $total pack skills are in $CLAUDE_DIR/skills"; else miss "not installed:$missing"; fi

echo "Working rules"
if grep -q "claude-power-pack:start" "$CLAUDE_DIR/CLAUDE.md" 2>/dev/null; then ok "CLAUDE.md has the pack's rules"; else miss "CLAUDE.md has no pack section"; fi

echo "Settings"
if python3 -c "import json,sys; json.load(open(sys.argv[1]))" "$CLAUDE_DIR/settings.json" 2>/dev/null; then
  ok "settings.json is valid JSON"
  python3 - "$CLAUDE_DIR/settings.json" <<'PY'
import json, sys
s = json.load(open(sys.argv[1]))
print(f"  ok       model: {s.get('model', '(default)')}, effort: {s.get('effortLevel', '(default)')}, workflows: {s.get('enableWorkflows', False)}")
PY
else
  miss "settings.json missing or not valid JSON"
fi

if have claude; then
  echo "Plugins"
  LIST="$(claude plugin list 2>/dev/null || true)"
  for p in superpowers@claude-plugins-official figma@claude-plugins-official andrej-karpathy-skills@karpathy-skills firebase@firebase brag@brag design@knowledge-work-plugins marketing@knowledge-work-plugins brand-voice@knowledge-work-plugins legal@knowledge-work-plugins; do
    if printf '%s' "$LIST" | grep -q "$p"; then ok "$p"; else miss "$p"; fi
  done
  echo "MCP servers"
  for m in playwright chrome-devtools; do
    if claude mcp get "$m" >/dev/null 2>&1; then ok "$m"; else miss "$m"; fi
  done
else
  echo "Plugins and MCP servers"
  miss "claude command not found, so plugins and MCP servers can't be checked"
fi

echo "Automatic updates"
if [ -f "$CLAUDE_DIR/power-pack/manifest.json" ]; then
  au="$(python3 "$CLAUDE_DIR/power-pack/pack_state.py" get --manifest "$CLAUDE_DIR/power-pack/manifest.json" auto_update 2>/dev/null)"
  ver="$(python3 "$CLAUDE_DIR/power-pack/pack_state.py" get --manifest "$CLAUDE_DIR/power-pack/manifest.json" version 2>/dev/null)"
  ok "version $ver, auto-update $au"
else
  miss "no power-pack/manifest.json (run install.sh again)"
fi

echo "Command-line tools (only needed for the skills that use them)"
for t in graphify:graphify playwright-cli:playwright-cli firecrawl:firecrawl-skills tvly:tavily-research higgsfield:higgsfield-skills yt-dlp:video-skills ffmpeg:video-skills whisper-cli:reel-reader uv:graphify gh:github-work; do
  bin="${t%%:*}"; why="${t#*:}"
  if have "$bin"; then ok "$bin"; else opt "$bin (for $why)"; fi
done
[ -f "$HOME/.cache/whisper-models/ggml-small.bin" ] && ok "whisper model" || opt "whisper model (for reel-reader; bash install.sh --tools downloads it)"
[ -d "$CLAUDE_DIR/skills/seo-checklist/node_modules" ] && ok "seo-checklist packages" || opt "seo-checklist packages (bash install.sh --tools)"
[ -d "$CLAUDE_DIR/skills/video-transcript-downloader/node_modules" ] && ok "video-transcript-downloader packages" || opt "video-transcript-downloader packages (bash install.sh --tools)"

echo
if [ "$FAIL" -eq 0 ]; then echo "Core install: OK"; else echo "Core install: $FAIL problem(s) above"; fi
[ "$TODO" -gt 0 ] && echo "$TODO optional tool(s) not installed yet; run: bash install.sh --tools"
echo "Logins and connectors are manual: see docs/SETUP-CHECKLIST.md"
[ "$FAIL" -eq 0 ]
