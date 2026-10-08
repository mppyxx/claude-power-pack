#!/usr/bin/env bash
# resolve-colorist setup for macOS and Linux.
#
#   bash install.sh              create .venv (if missing) and install the Python packages
#   bash install.sh --recreate   delete and rebuild .venv
#   bash install.sh --dev        also install the test packages (colour-science)
#   bash install.sh --mcp        also register Resolve's AI assistant server with Claude Code
#                                (only if it is not registered yet and the ResolveMCP program exists)
#   bash install.sh --check-location   only check that this folder sits where Claude Code looks for skills
#
# It only writes inside this folder (.venv). No sudo, no system changes.

set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RECREATE=0
DEV=0
MCP=0
LOCATION_ONLY=0
for a in "$@"; do
  case "$a" in
    --recreate) RECREATE=1 ;;
    --dev) DEV=1 ;;
    --mcp) MCP=1 ;;
    --check-location) LOCATION_ONLY=1 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "Unknown option: $a (try --help)"; exit 2 ;;
  esac
done

OS="$(uname -s)"
say() { printf '%s\n' "$*"; }
warn() { printf 'WARNING: %s\n' "$*"; }
PROBLEMS=0

say "resolve-colorist setup in: $HERE"

# 1. Where the folder lives: Claude Code only finds a skill whose folder sits directly in a .claude/skills folder
#    (~/.claude/skills/resolve-colorist or <project>/.claude/skills/resolve-colorist). Unzip tools often add a
#    second folder level (resolve-colorist/resolve-colorist), which Claude Code never finds.
NAME="$(basename "$HERE")"
PARENT="$(dirname "$HERE")"
GRAND="$(dirname "$PARENT")"
PLACE_OK=0
if [ "$(basename "$PARENT")" = "skills" ]; then
  if [ "$(basename "$GRAND")" = ".claude" ]; then
    PLACE_OK=1
  elif [ -n "${CLAUDE_CONFIG_DIR:-}" ] && [ -d "$CLAUDE_CONFIG_DIR" ] \
       && [ "$(cd "$GRAND" && pwd -P)" = "$(cd "$CLAUDE_CONFIG_DIR" && pwd -P)" ]; then
    PLACE_OK=1      # a custom Claude Code config folder (CLAUDE_CONFIG_DIR) has its own skills folder
  fi
fi
if [ "$PLACE_OK" != 1 ]; then
  warn "this folder is in the wrong place, so Claude Code will not find the skill:"
  say "         $HERE"
  if [ "$(basename "$PARENT")" = "$NAME" ]; then
    say "         It is a folder inside a folder of the same name (the unzip tool added a level)."
    say "         Move the inner $NAME folder up one level so it replaces the outer one:"
    say "           ~/.claude/skills/$NAME/SKILL.md must exist, not ~/.claude/skills/$NAME/$NAME/SKILL.md"
  else
    say "         Move the whole folder so that this file exists: ~/.claude/skills/$NAME/SKILL.md"
    say "         (or <your project>/.claude/skills/$NAME/SKILL.md)."
  fi
  say "         Then run this script again from the new place. Nothing was installed."
  exit 2
fi
if [ "$LOCATION_ONLY" = 1 ]; then
  say "Location OK: Claude Code will find the skill here."
  exit 0
fi

# 2. Python 3.10 or newer
PY=""
for cand in python3.14 python3.13 python3.12 python3.11 python3.10 python3 python; do
  if command -v "$cand" >/dev/null 2>&1; then
    if "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
      PY="$(command -v "$cand")"
      break
    fi
  fi
done
if [ -z "$PY" ]; then
  say "Python 3.10 or newer was not found."
  if [ "$OS" = "Darwin" ]; then
    say "Install it with Homebrew (brew install python) or from https://www.python.org/downloads/ and run this again."
  else
    say "Install it with your package manager (for example: sudo apt install python3 python3-venv) and run this again."
  fi
  exit 1
fi
say "Python: $PY ($("$PY" -c 'import sys; print(sys.version.split()[0])'))"

# 3. The virtual environment
VENV="$HERE/.venv"
VPY="$VENV/bin/python"
if [ "$RECREATE" = 1 ] && [ -d "$VENV" ]; then
  say "Removing the old .venv"
  rm -rf "$VENV"
fi
if [ ! -x "$VPY" ]; then
  say "Creating .venv"
  if ! "$PY" -m venv "$VENV"; then
    say "Could not create the virtual environment."
    [ "$OS" = "Linux" ] && say "On Debian or Ubuntu install the venv module first: sudo apt install python3-venv"
    exit 1
  fi
else
  say ".venv already exists (use --recreate to rebuild it)"
fi

REQ="$HERE/requirements.txt"
[ "$DEV" = 1 ] && REQ="$HERE/requirements-dev.txt"
say "Installing packages from $(basename "$REQ")"
if ! "$VPY" -m pip install --disable-pip-version-check -q -r "$REQ"; then
  warn "pip could not install the packages (no internet connection?). Run this script again when you are online."
  PROBLEMS=1
fi
if "$VPY" -c 'import numpy, PIL' >/dev/null 2>&1; then
  say "Packages: $("$VPY" -c 'import numpy, PIL; print("numpy", numpy.__version__, "/ pillow", PIL.__version__)')"
else
  warn "numpy and pillow are not importable from .venv yet."
  PROBLEMS=1
fi

# 4. ffmpeg and ffprobe
for tool in ffmpeg ffprobe; do
  if command -v "$tool" >/dev/null 2>&1; then
    say "$tool: $(command -v "$tool")"
  else
    warn "$tool was not found on PATH."
    if [ "$OS" = "Darwin" ]; then
      say "         Install it with: brew install ffmpeg"
    else
      say "         Install it with your package manager, for example: sudo apt install ffmpeg"
    fi
    say "         Or point the skill at your own copy with the RC_FFMPEG and RC_FFPROBE environment variables."
    PROBLEMS=1
  fi
done
if command -v ffmpeg >/dev/null 2>&1; then
  if ! ffmpeg -hide_banner -filters 2>/dev/null | grep -q libvmaf; then
    say "Note: this ffmpeg has no libvmaf, so the optional social media check (social-qc) will skip some tests."
  fi
fi

# 5. Resolve's AI assistant server (optional registration)
if [ -n "${RESOLVE_MCP_PATH:-}" ]; then
  MCP_BIN="$RESOLVE_MCP_PATH"
elif [ "$OS" = "Darwin" ]; then
  MCP_BIN="/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Applications/ResolveMCP"
else
  MCP_BIN="/opt/resolve/bin/ResolveMCP"
fi
if [ "$MCP" = 1 ]; then
  if [ ! -x "$MCP_BIN" ]; then
    warn "ResolveMCP was not found at: $MCP_BIN"
    say "         It ships with DaVinci Resolve Studio 21.1 or newer. Set RESOLVE_MCP_PATH if it lives elsewhere."
    PROBLEMS=1
  elif ! command -v claude >/dev/null 2>&1; then
    warn "the claude command was not found, so the server could not be registered."
    PROBLEMS=1
  elif claude mcp get davinci-resolve >/dev/null 2>&1 || claude mcp get "DaVinci Resolve" >/dev/null 2>&1 || claude mcp get "DaVinci Resolve Studio" >/dev/null 2>&1; then
    say "Claude Code already knows a DaVinci Resolve server."
  else
    say "Registering the DaVinci Resolve server with Claude Code (user scope)"
    claude mcp add --scope user davinci-resolve -- "$MCP_BIN" || { warn "claude mcp add failed"; PROBLEMS=1; }
  fi
fi

# 6. Self check
if "$VPY" "$HERE/grade_lab.py" commands 2>/dev/null | grep -qx doctor; then
  say ""
  say "Running the skill's own check (grade_lab.py doctor):"
  DOC="$("$VPY" "$HERE/grade_lab.py" doctor 2>&1)" || PROBLEMS=1
  say "$DOC"
  # doctor exits 0 and reports problems on its first line
  case "$DOC" in
    "DOCTOR: OK"*) ;;
    *) PROBLEMS=1 ;;
  esac
fi

cat <<EOF

Next steps
1. DaVinci Resolve Studio 21.1 or newer: File > Setup AI Assistants, pick Claude Code. Then restart Claude Code.
   (Or run: bash install.sh --mcp)
2. If Resolve tools time out: Resolve > Preferences > System > General > External scripting using: Local.
3. Open your project and timeline in Resolve, start Claude Code and ask, for example:
   "Color grade my current Resolve timeline. It is an Instagram reel shot on a Sony in S-Log3."
EOF

if [ "$PROBLEMS" = 1 ]; then
  say ""
  say "Setup finished with warnings (see above)."
  exit 1
fi
say ""
say "Setup finished."
exit 0
