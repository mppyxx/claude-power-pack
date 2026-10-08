#!/usr/bin/env bash
# Claude Power Pack auto-updater. Runs in the background from the session-start hook, at most once a day.
#
# What it does: fetches the official repo (github.com/mppyxx/claude-power-pack) into its own cache folder,
# and if there's a new version, runs that version's install.sh --update. Update mode only replaces pack
# skills and rules you haven't changed, adds new skills, and merges new settings keys. It never uses
# --force or --tools, never installs software, and never touches anything you edited or already had.
#
#   bash updater.sh          check if a day has passed since the last check, update if there's something new
#   bash updater.sh --now    check right away
set -u
CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
STATE="$CLAUDE_DIR/power-pack"
MANIFEST="$STATE/manifest.json"
CACHE="$STATE/repo"
LOG="$STATE/update.log"
URL="${POWER_PACK_REPO_URL:-https://github.com/mppyxx/claude-power-pack.git}"   # override exists only for tests
NOW=0; [ "${1:-}" = "--now" ] && NOW=1

[ -f "$MANIFEST" ] || exit 0
command -v git >/dev/null 2>&1 || exit 0
command -v python3 >/dev/null 2>&1 || exit 0
[ "$(python3 "$STATE/pack_state.py" get --manifest "$MANIFEST" auto_update 2>/dev/null)" = "off" ] && [ "$NOW" = 0 ] && exit 0

# once a day
if [ "$NOW" = 0 ] && [ -f "$STATE/last-check" ]; then
  if [ -n "$(find "$STATE/last-check" -mmin -1440 2>/dev/null)" ]; then exit 0; fi
fi

# one updater at a time (mkdir is atomic); a lock older than an hour is from a crashed run
LOCK="$STATE/update.lock"
if ! mkdir "$LOCK" 2>/dev/null; then
  [ -n "$(find "$LOCK" -maxdepth 0 -mmin +60 2>/dev/null)" ] || exit 0
  rmdir "$LOCK" 2>/dev/null; mkdir "$LOCK" 2>/dev/null || exit 0
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$LOG"; }
touch "$STATE/last-check"

if [ -d "$CACHE/.git" ]; then
  if ! git -C "$CACHE" fetch -q --depth 1 origin main 2>>"$LOG"; then log "fetch failed (offline?)"; exit 0; fi
  git -C "$CACHE" reset -q --hard FETCH_HEAD || { log "could not move the cache to the new version"; exit 0; }
else
  rm -rf "$CACHE.tmp"
  if ! git clone -q --depth 1 --branch main "$URL" "$CACHE.tmp" 2>>"$LOG"; then log "clone failed (offline?)"; exit 0; fi
  mv "$CACHE.tmp" "$CACHE"
fi

NEW_COMMIT="$(git -C "$CACHE" rev-parse HEAD)"
OLD_COMMIT="$(python3 "$STATE/pack_state.py" get --manifest "$MANIFEST" commit)"
if [ "$NEW_COMMIT" = "$OLD_COMMIT" ]; then log "up to date ($NEW_COMMIT)"; exit 0; fi

OLD_VERSION="$(python3 "$STATE/pack_state.py" get --manifest "$MANIFEST" version)"
NEW_VERSION="$(cat "$CACHE/VERSION" 2>/dev/null || echo unknown)"
log "updating $OLD_VERSION ($OLD_COMMIT) -> $NEW_VERSION ($NEW_COMMIT)"

OUT="$(bash "$CACHE/install.sh" --update 2>&1)"; STATUS=$?
printf '%s\n' "$OUT" >> "$LOG"
if [ "$STATUS" -ne 0 ]; then log "install.sh --update exited $STATUS"; exit 0; fi

# leave a note that the next session's start hook passes to Claude, but only when something you use changed
CHANGES="$(printf '%s\n' "$OUT" | grep -E '^(updated [1-9]|updated [0-9]+, added [1-9]|updated: |new: |kept your edited|no longer in the pack|settings: (set|added)|~?/.*CLAUDE.md: (updated|you edited))' | grep -vE '^updated 0, added 0, ' | sed 's/^/- /')"
if [ "$OLD_VERSION" = "$NEW_VERSION" ] && [ -z "$CHANGES" ]; then log "done (nothing that affects you changed)"; exit 0; fi
{
  if [ "$OLD_VERSION" = "$NEW_VERSION" ]; then
    echo "Claude Power Pack picked up the latest changes from GitHub (version $NEW_VERSION)."
  else
    echo "Claude Power Pack updated from $OLD_VERSION to $NEW_VERSION."
  fi
  [ -n "$CHANGES" ] && printf '%s\n' "$CHANGES"
  echo "What's new: https://github.com/mppyxx/claude-power-pack/blob/main/CHANGELOG.md"
  echo "Turn automatic updates off: bash ${STATE/#$HOME/~}/auto-update.sh off"
} > "$STATE/notice.md"
log "done"
