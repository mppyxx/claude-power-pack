#!/usr/bin/env bash
# resolve-editor setup for macOS and Linux.
#
#   bash install.sh                  create .venv (if missing) and install the Python packages
#   bash install.sh --recreate       delete and rebuild .venv
#   bash install.sh --dev            also install the test packages
#   bash install.sh --mcp            also register Resolve's AI assistant server with Claude Code
#                                    (only if it is not registered yet and the ResolveMCP program exists)
#   bash install.sh --asr [base|small]   set up local transcription: the pywhispercpp package when whisper-cli
#                                    is missing, and the whisper model file (asks before downloading;
#                                    base 142 MB, small 466 MB, default small) into models/
#   bash install.sh --yes            with --asr: download the model without asking
#   bash install.sh --check-location only check that this folder sits where Claude Code looks for skills
#
# It only writes inside this folder (.venv and models). No sudo, no system changes.

set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RECREATE=0
DEV=0
MCP=0
ASR=""
YES=0
LOCATION_ONLY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --recreate) RECREATE=1 ;;
    --dev) DEV=1 ;;
    --mcp) MCP=1 ;;
    --yes|-y) YES=1 ;;
    --asr)
      ASR="small"
      if [ $# -gt 1 ] && [ "${2#-}" = "$2" ]; then ASR="$2"; shift; fi ;;
    --asr=*) ASR="${1#--asr=}" ;;
    --check-location) LOCATION_ONLY=1 ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1 (try --help)"; exit 2 ;;
  esac
  shift
done
case "$ASR" in
  ""|base|small) ;;
  *) echo "Unknown whisper model for --asr: $ASR (use base or small)"; exit 2 ;;
esac

OS="$(uname -s)"
say() { printf '%s\n' "$*"; }
warn() { printf 'WARNING: %s\n' "$*"; }
PROBLEMS=0

say "resolve-editor setup in: $HERE"

# 1. Where the folder lives: Claude Code only finds a skill whose folder sits directly in a .claude/skills folder
#    (~/.claude/skills/resolve-editor or <project>/.claude/skills/resolve-editor). Unzip tools often add a
#    second folder level (resolve-editor/resolve-editor), which Claude Code never finds.
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
    say "         Or point the skill at your own copy with the RE_FFMPEG and RE_FFPROBE environment variables."
    PROBLEMS=1
  fi
done

# 5. Local transcription (optional): whisper-cli (whisper.cpp) or the pywhispercpp package, plus a model file
WHISPER=""
if [ -n "${RE_WHISPER_CLI:-}" ] && [ -x "${RE_WHISPER_CLI}" ]; then
  WHISPER="$RE_WHISPER_CLI"
elif command -v whisper-cli >/dev/null 2>&1; then
  WHISPER="$(command -v whisper-cli)"
fi
has_pyw() { "$VPY" -c 'import pywhispercpp' >/dev/null 2>&1; }
find_model() {
  if [ -n "${RE_WHISPER_MODEL:-}" ] && [ -f "${RE_WHISPER_MODEL}" ]; then printf '%s\n' "$RE_WHISPER_MODEL"; return 0; fi
  for d in "$HERE/models" "$HOME/.cache/whisper-models" "$HOME/.cache/whisper.cpp"; do
    for f in "$d"/ggml-*.bin; do
      [ -f "$f" ] && { printf '%s\n' "$f"; return 0; }
    done
  done
  return 1
}

if [ -n "$ASR" ]; then
  if [ -z "$WHISPER" ] && ! has_pyw; then
    say "Installing the whisper.cpp Python binding (requirements-asr.txt)"
    if ! "$VPY" -m pip install --disable-pip-version-check -q -r "$HERE/requirements-asr.txt"; then
      warn "pywhispercpp could not be installed."
      if [ "$OS" = "Darwin" ]; then
        say "         On a Mac install whisper.cpp instead: brew install whisper-cpp (then run this again)."
      else
        say "         Build whisper.cpp yourself and set RE_WHISPER_CLI to its whisper-cli, or run without transcripts."
      fi
      PROBLEMS=1
    fi
  fi
  MODEL_FILE="$HERE/models/ggml-$ASR.bin"
  case "$ASR" in base) SIZE="142 MB" ;; *) SIZE="466 MB" ;; esac
  URL="https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-$ASR.bin"
  EXISTING=""
  for f in "${RE_WHISPER_MODEL:-}" "$MODEL_FILE" "$HOME/.cache/whisper-models/ggml-$ASR.bin" "$HOME/.cache/whisper.cpp/ggml-$ASR.bin"; do
    if [ -n "$f" ] && [ -f "$f" ] && [ "$(basename "$f")" = "ggml-$ASR.bin" ]; then EXISTING="$f"; break; fi
  done
  if [ -n "$EXISTING" ]; then
    say "Whisper model already present: $EXISTING"
  else
    GO=0
    if [ "$YES" = 1 ]; then
      GO=1
    elif [ -t 0 ]; then
      printf 'Download the whisper model ggml-%s.bin (%s) from the whisper.cpp model repository into models/? [y/N] ' "$ASR" "$SIZE"
      read -r ans
      case "$ans" in y|Y|yes|YES) GO=1 ;; esac
    else
      warn "the whisper model ggml-$ASR.bin ($SIZE) was not downloaded: there is no terminal to ask in."
      say "         Ask the user first, then run again with: bash install.sh --asr $ASR --yes"
      PROBLEMS=1
      ASR_SKIPPED=1
    fi
    if [ "$GO" = 1 ]; then
      mkdir -p "$HERE/models"
      say "Downloading $URL ($SIZE)"
      OK=0
      if command -v curl >/dev/null 2>&1; then
        curl -L --fail --progress-bar -o "$MODEL_FILE.part" "$URL" && OK=1
      elif command -v wget >/dev/null 2>&1; then
        wget -q --show-progress -O "$MODEL_FILE.part" "$URL" && OK=1
      else
        warn "neither curl nor wget was found, so the model could not be downloaded."
      fi
      if [ "$OK" = 1 ] && [ "$(wc -c < "$MODEL_FILE.part" | tr -d ' ')" -gt 50000000 ]; then
        mv "$MODEL_FILE.part" "$MODEL_FILE"
        say "Model saved: models/ggml-$ASR.bin"
      else
        rm -f "$MODEL_FILE.part"
        warn "the model download failed. Run this again when you are online."
        PROBLEMS=1
      fi
    fi
  fi
fi

MODEL_FOUND="$(find_model || true)"
if [ -n "$WHISPER" ]; then
  say "Transcription engine: whisper-cli ($WHISPER)"
elif has_pyw; then
  say "Transcription engine: pywhispercpp"
else
  say "Note: no local transcription engine (optional, needed for word-based editing and captions)."
  if [ "$OS" = "Darwin" ]; then
    say "      Install it with: brew install whisper-cpp, then: bash install.sh --asr small"
  else
    say "      Install it with: bash install.sh --asr small"
  fi
fi
if [ -n "$WHISPER" ] || has_pyw; then
  if [ -n "$MODEL_FOUND" ]; then
    say "Whisper model: $MODEL_FOUND"
  else
    if [ "${ASR_SKIPPED:-0}" != 1 ]; then
      say "Note: no whisper model file yet. Get one with: bash install.sh --asr small"
    fi
  fi
fi

# 6. Resolve's AI assistant server (optional registration)
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

# 7. Self check: both labs report what is missing (doctor exits 0 and reports problems on its first line)
for lab in media_lab.py edit_lab.py; do
  if [ -f "$HERE/$lab" ] && "$VPY" "$HERE/$lab" commands 2>/dev/null | awk '{print $1}' | grep -qx doctor; then
    say ""
    say "Running the skill's own check ($lab doctor):"
    DOC="$("$VPY" "$HERE/$lab" doctor 2>&1)" || PROBLEMS=1
    say "$DOC"
    case "$DOC" in
      "DOCTOR: OK"*) ;;
      *) PROBLEMS=1 ;;
    esac
  fi
done

cat <<EOF

Next steps
1. DaVinci Resolve Studio 21.1 or newer: File > Setup AI Assistants, pick Claude Code. Then restart Claude Code.
   (Or run: bash install.sh --mcp)
2. If Resolve tools time out: Resolve > Preferences > System > General > External scripting using: Local.
3. Open your project in Resolve, start Claude Code and ask, for example:
   "Cut a 45 second Instagram reel from the interview on my current timeline, with captions."
EOF

if [ "$PROBLEMS" = 1 ]; then
  say ""
  say "Setup finished with warnings (see above)."
  exit 1
fi
say ""
say "Setup finished."
exit 0
