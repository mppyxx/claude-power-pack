#!/usr/bin/env bash
# Claude Power Pack installer (macOS, Linux works for most of it).
#
#   bash install.sh             skills + working rules + settings + plugins + keyless MCP servers
#   bash install.sh --tools     also install the command-line tools the skills use (Homebrew, npm, uv)
#   bash install.sh --extras    also install the extra marketing/SEO skills in extras/
#   bash install.sh --force     replace skills you already have with the pack's copy (old copy is backed up)
#   bash install.sh --dry-run   show what would happen, change nothing
#   bash install.sh --skip-plugins / --skip-mcp
#   bash install.sh --no-auto-update   install without the daily background update check
#   bash install.sh --update    what the auto-updater runs: refresh only the pack's skills and rules you
#                               haven't changed, add new ones, merge new settings (no plugins, MCP, --tools or --force)
#
# Safe to run more than once. Everything it replaces is backed up first.

set -u
PACK="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="$HOME/.claude-power-pack-backup/$STAMP"

TOOLS=0; EXTRAS=0; FORCE=0; DRY=0; SKIP_PLUGINS=0; SKIP_MCP=0; UPDATE=0; AUTO_UPDATE=on
for a in "$@"; do
  case "$a" in
    --tools) TOOLS=1 ;;
    --extras) EXTRAS=1 ;;
    --force) FORCE=1 ;;
    --dry-run) DRY=1 ;;
    --skip-plugins) SKIP_PLUGINS=1 ;;
    --skip-mcp) SKIP_MCP=1 ;;
    --update) UPDATE=1 ;;
    --no-auto-update) AUTO_UPDATE=off ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
    *) echo "Unknown option: $a (try --help)"; exit 2 ;;
  esac
done

say()  { printf '%s\n' "$*"; }
section() { printf '\n== %s\n' "$*"; }
warn() { printf 'WARNING: %s\n' "$*"; WARNINGS=$((WARNINGS + 1)); }
exec 3>&1
run()  { if [ "$DRY" = 1 ]; then printf '  (dry run) %s\n' "$*" >&3; else "$@"; fi; }
WARNINGS=0
have() { command -v "$1" >/dev/null 2>&1; }
STATE="$CLAUDE_DIR/power-pack"
MANIFEST="$STATE/manifest.json"
VERSION="$(cat "$PACK/VERSION" 2>/dev/null || echo unknown)"
COMMIT="$(git -C "$PACK" rev-parse HEAD 2>/dev/null || echo unknown)"
if [ "$UPDATE" = 1 ]; then FORCE=0; TOOLS=0; SKIP_PLUGINS=1; SKIP_MCP=1; fi   # updates only touch skills, rules and settings
did()  { [ "$DRY" = 1 ] || say "$*"; }   # success lines, hidden in a dry run

say "Claude Power Pack $VERSION"
say "pack:   $PACK"
say "target: $CLAUDE_DIR"
[ "$DRY" = 1 ] && say "DRY RUN: nothing will be changed"

# ---------------------------------------------------------------- 0. checks
section "Checking what's installed"
if [ "$UPDATE" = 1 ]; then
  # updates only copy files and merge settings: python3 is all they need
  works_py=0; python3 -c 'import sys' >/dev/null 2>&1 && works_py=1
  if [ "$works_py" = 1 ]; then say "python3: ok"; else warn "python3 is required for updates"; fi
elif have claude; then
  say "claude: $(claude --version 2>/dev/null | head -1)"
else
  warn "Claude Code is not installed. Install it first, sign in with your Max account, then run this again:"
  say  "         curl -fsSL https://claude.ai/install.sh | bash"
  say  "  Skills, rules and settings will still be copied now; plugins and MCP servers need the claude command."
fi
works() {   # the command exists and actually runs (macOS has placeholder git/python3 until developer tools are installed)
  case "$1" in
    python3) python3 -c 'import sys' >/dev/null 2>&1 ;;
    git) git --version >/dev/null 2>&1 ;;
    *) have "$1" ;;
  esac
}
if [ "$UPDATE" = 0 ]; then
  for t in git node npm python3; do
    if works "$t"; then say "$t: ok"; else warn "$t is missing or not working (on a Mac run: xcode-select --install, or use --tools with Homebrew)"; fi
  done
  if ! works python3; then
    warn "python3 is required for the rules and settings steps. Install it, then run this again."
  fi
fi

# ---------------------------------------------------------------- 1. backup
section "Backing up your current setup to $BACKUP"
if [ "$DRY" = 0 ]; then
  mkdir -p "$BACKUP"
  [ -f "$CLAUDE_DIR/settings.json" ] && cp "$CLAUDE_DIR/settings.json" "$BACKUP/"
  [ -f "$CLAUDE_DIR/CLAUDE.md" ] && cp "$CLAUDE_DIR/CLAUDE.md" "$BACKUP/"
  [ -d "$CLAUDE_DIR/skills" ] && ls "$CLAUDE_DIR/skills" > "$BACKUP/skills-before.txt"
fi
say "done"

# ---------------------------------------------------------------- 2. skills
# tools/pack_state.py copies the skills and records a fingerprint of each one it installs, so later
# updates can tell the pack's untouched copies apart from skills you edited or already had.
skill_mode=install; [ "$FORCE" = 1 ] && skill_mode=force; [ "$UPDATE" = 1 ] && skill_mode=update
sync_skills() {   # $1 = source folder, $2 = group name
  local extra=()
  [ "$DRY" = 1 ] && extra+=(--dry-run)
  python3 "$PACK/tools/pack_state.py" skills --src "$1" --dst "$CLAUDE_DIR/skills" --manifest "$MANIFEST" \
    --mode "$skill_mode" --group "$2" --backup "$BACKUP/skills" ${extra[@]+"${extra[@]}"} \
    || warn "could not install the skills from $1"
}
section "Installing skills into $CLAUDE_DIR/skills"
sync_skills "$PACK/skills" skills
had_extras=0
if [ -f "$MANIFEST" ] && grep -q '"group": "extras"' "$MANIFEST"; then had_extras=1; fi
if [ "$EXTRAS" = 1 ] || { [ "$UPDATE" = 1 ] && [ "$had_extras" = 1 ]; }; then
  EXTRAS=1
  section "Installing extra skills"
  sync_skills "$PACK/extras" extras
fi

# ---------------------------------------------------------------- 2b. skills fetched from their original repos
# skills-upstream.txt lists skills that are installed straight from their authors' GitHub repos
# (one "name owner/repo [extra]" per line) instead of being copied from this folder.
UPSTREAM="$PACK/skills-upstream.txt"
if [ "$UPDATE" = 0 ] && [ -f "$UPSTREAM" ] && grep -qvE '^[[:space:]]*(#|$)' "$UPSTREAM"; then
  section "Installing skills from their authors' GitHub repos"
  if ! have npx; then
    warn "Node.js (npx) is needed to fetch these skills. Install Node, then run this again."
  else
    up_ok=0; up_kept=0
    while read -r name repo kind _; do
      case "$name" in ''|\#*) continue ;; esac
      [ "$kind" = extra ] && [ "$EXTRAS" = 0 ] && continue
      if [ -e "$CLAUDE_DIR/skills/$name" ] && [ "$FORCE" = 0 ]; then up_kept=$((up_kept + 1)); continue; fi
      if [ "$DRY" = 1 ]; then printf '  (dry run) npx skills add %s --skill %s\n' "$repo" "$name" >&3; continue; fi
      if [ -e "$CLAUDE_DIR/skills/$name" ]; then mkdir -p "$BACKUP/skills"; mv "$CLAUDE_DIR/skills/$name" "$BACKUP/skills/$name"; fi
      if DISABLE_TELEMETRY=1 npx -y skills add "$repo" -g -a claude-code -s "$name" -y --copy </dev/null >/dev/null 2>&1 \
         && [ -f "$CLAUDE_DIR/skills/$name/SKILL.md" ]; then
        up_ok=$((up_ok + 1))
      else
        warn "could not fetch $name from github.com/$repo"
      fi
    done < "$UPSTREAM"
    did "fetched $up_ok, left $up_kept that were already there alone"
  fi
fi

# ---------------------------------------------------------------- 3. working rules (CLAUDE.md)
section "Installing the working rules (CLAUDE.md)"
rules_mode=install; [ "$FORCE" = 1 ] && rules_mode=force; [ "$UPDATE" = 1 ] && rules_mode=update
rules_extra=(); [ "$DRY" = 1 ] && rules_extra+=(--dry-run)
python3 "$PACK/tools/pack_state.py" rules --src "$PACK/rules/CLAUDE.md" --dst "$CLAUDE_DIR/CLAUDE.md" \
  --manifest "$MANIFEST" --mode "$rules_mode" ${rules_extra[@]+"${rules_extra[@]}"} \
  || warn "could not install the working rules (CLAUDE.md)"

# ---------------------------------------------------------------- 4. settings
section "Merging settings (your existing values win)"
if [ "$DRY" = 1 ]; then
  python3 "$PACK/tools/merge_settings.py" "$PACK/settings/settings.json" "$CLAUDE_DIR/settings.json" --dry-run
else
  python3 "$PACK/tools/merge_settings.py" "$PACK/settings/settings.json" "$CLAUDE_DIR/settings.json" \
    || warn "could not merge settings.json (is it valid JSON? your file was not changed)"
fi

# ---------------------------------------------------------------- 5. plugins
MARKETPLACES="anthropics/claude-plugins-official anthropics/knowledge-work-plugins forrestchang/andrej-karpathy-skills firebase/firebase-tools https://github.com/latent-spaces/brag.git"
PLUGINS="superpowers@claude-plugins-official figma@claude-plugins-official andrej-karpathy-skills@karpathy-skills firebase@firebase brag@brag design@knowledge-work-plugins marketing@knowledge-work-plugins brand-voice@knowledge-work-plugins legal@knowledge-work-plugins"
if [ "$SKIP_PLUGINS" = 0 ] && have claude; then
  section "Adding plugin marketplaces"
  KNOWN="$(claude plugin marketplace list 2>/dev/null || true)"
  for m in $MARKETPLACES; do
    short="$(basename "$m" .git)"
    if printf '%s' "$KNOWN" | grep -q "$short"; then say "already added: $m"; continue; fi
    run claude plugin marketplace add "$m" >/dev/null 2>&1 && did "added: $m" || warn "could not add marketplace $m"
  done
  section "Installing plugins"
  INSTALLED="$(claude plugin list 2>/dev/null || true)"
  for p in $PLUGINS; do
    if printf '%s' "$INSTALLED" | grep -q "${p%@*}@${p#*@}"; then say "already installed: $p"; continue; fi
    if run claude plugin install "$p" --scope user; then did "installed: $p"; else warn "could not install $p (try inside Claude Code: /plugin install $p)"; fi
  done
fi

# ---------------------------------------------------------------- 6. MCP servers that need no keys
add_mcp() {   # name, then the command
  local name="$1"; shift
  if claude mcp get "$name" >/dev/null 2>&1; then say "already set up: $name"; return; fi
  if run claude mcp add --scope user "$name" -- "$@" >/dev/null 2>&1; then did "added: $name"; else warn "could not add MCP server $name"; fi
}
if [ "$SKIP_MCP" = 0 ] && have claude; then
  section "Adding MCP servers (no keys needed)"
  add_mcp playwright npx @playwright/mcp@latest
  add_mcp chrome-devtools npx -y chrome-devtools-mcp@latest
fi

# ---------------------------------------------------------------- 7. command-line tools (optional)
if [ "$TOOLS" = 1 ]; then
  section "Installing command-line tools the skills use"
  if [ "$(uname -s)" = "Darwin" ]; then
    if ! have brew; then
      warn "Homebrew is missing. Install it from https://brew.sh, then run: bash install.sh --tools"
    else
      for f in node git python uv gh ffmpeg yt-dlp whisper-cpp; do
        if brew list --formula "$f" >/dev/null 2>&1; then say "brew: $f already installed"; else run brew install "$f" || warn "brew install $f failed"; fi
      done
      for f in auto-editor; do
        if brew list --formula "$f" >/dev/null 2>&1; then say "brew: $f already installed"; else run brew install "$f" || warn "brew install $f failed (ai-reel-editor can work without it)"; fi
      done
    fi
  else
    say "Not a Mac: install node, git, python3, uv, gh, ffmpeg, yt-dlp and whisper.cpp with your package manager."
  fi
  if have npm; then
    for pkg in @playwright/cli@latest firecrawl-cli@1.19.6; do
      run npm install -g "$pkg" >/dev/null 2>&1 && did "npm: $pkg" || warn "npm install -g $pkg failed"
    done
  fi
  if have uv; then
    run uv tool install --upgrade graphifyy >/dev/null 2>&1 && did "uv: graphifyy (graphify)" || warn "uv tool install graphifyy failed"
  else
    warn "uv not found, skipped graphify (install uv, then: uv tool install graphifyy)"
  fi
  if [ -f "$CLAUDE_DIR/skills/seo-checklist/package.json" ] && have npm; then
    run npm ci --prefix "$CLAUDE_DIR/skills/seo-checklist" --silent && did "seo-checklist: npm packages installed" || warn "npm ci in seo-checklist failed"
  fi
  if [ -f "$CLAUDE_DIR/skills/video-transcript-downloader/package.json" ] && have npm; then
    run npm ci --prefix "$CLAUDE_DIR/skills/video-transcript-downloader" --silent && did "video-transcript-downloader: npm packages installed" || warn "npm ci in video-transcript-downloader failed"
  fi
  MODEL="$HOME/.cache/whisper-models/ggml-small.bin"
  if [ ! -f "$MODEL" ]; then
    say "Downloading the Whisper speech model for reel-reader (about 490 MB)"
    run mkdir -p "$(dirname "$MODEL")"
    if run curl -fL --progress-bar -o "$MODEL.part" https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.bin; then
      run mv "$MODEL.part" "$MODEL"
    else
      warn "Whisper model download failed (run --tools again to retry)"
    fi
  fi
  RESOLVE_MCP="/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Applications/ResolveMCP"
  if [ -x "$RESOLVE_MCP" ]; then
    say "DaVinci Resolve Studio found: setting up the two Resolve skills"
    run bash "$CLAUDE_DIR/skills/resolve-colorist/install.sh" --mcp || warn "resolve-colorist setup reported a problem"
    run bash "$CLAUDE_DIR/skills/resolve-editor/install.sh" || warn "resolve-editor setup reported a problem"
  else
    say "DaVinci Resolve Studio not found: skipped the Resolve skills' setup (see docs/SETUP-CHECKLIST.md)"
  fi
fi

# ---------------------------------------------------------------- 8. automatic updates
section "Automatic updates"
if [ "$DRY" = 1 ]; then
  say "  (dry run) would record version $VERSION and copy the updater to $STATE (auto-update: $AUTO_UPDATE)"
else
  mkdir -p "$STATE"
  for f in pack_state.py updater.sh session-start.sh auto-update.sh; do cp "$PACK/tools/$f" "$STATE/$f"; done
  au="$AUTO_UPDATE"; [ "$UPDATE" = 1 ] && au=keep
  python3 "$PACK/tools/pack_state.py" stamp --manifest "$MANIFEST" --version "$VERSION" --commit "$COMMIT" --auto-update "$au" >/dev/null \
    || warn "could not record the installed version"
  if [ "$(python3 "$PACK/tools/pack_state.py" get --manifest "$MANIFEST" auto_update)" = "on" ]; then
    say "on: once a day, in the background, Claude Power Pack checks GitHub for a new version and updates only"
    say "the pack's skills and rules you haven't changed. Turn it off: bash $STATE/auto-update.sh off"
  else
    say "off. Update any time with: bash $STATE/auto-update.sh now"
  fi
fi

if [ "$UPDATE" = 1 ]; then
  if [ "$WARNINGS" -gt 0 ]; then say "Update finished with $WARNINGS warning(s)."; exit 1; fi
  say "Update finished."
  exit 0
fi

# ---------------------------------------------------------------- 9. done
section "Checking the result"
[ "$DRY" = 1 ] || bash "$PACK/tools/verify.sh" || warn "the check above found problems in the core install"

section "What's left for you (needs your logins, so it can't be scripted)"
say "Open docs/SETUP-CHECKLIST.md. In short:"
say "  1. claude.ai > Settings > Connectors: connect the apps listed there (Gmail, Drive, Notion, Figma, ...)"
say "  2. Claude desktop app > Settings > Extensions: add the desktop extensions listed there"
say "  3. Log in to the skill CLIs you'll use: firecrawl login, tvly login, higgsfield auth login"
say "  4. Restart Claude Code, then type /plugin and /mcp to see everything loaded"
[ "$DRY" = 1 ] || say "Backup of your previous setup: $BACKUP"
if [ "$WARNINGS" -gt 0 ]; then say ""; say "Finished with $WARNINGS warning(s). Scroll up for the WARNING lines."; else say ""; say "Finished with no warnings."; fi
