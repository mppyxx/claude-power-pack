#!/usr/bin/env bash
# Reel Reader: download a reel, transcribe it locally, pull caption and key frames.
# Usage:
#   reel.sh <url> [output-dir] [--lang xx] [-- extra yt-dlp args]
#   reel.sh --meta <fetch.json> [output-dir] [--lang xx]   (pre-resolved JSON with url, shortcode, video_url)
# Instagram often needs a login. Only if the user agrees, pass their own browser's cookies:
#   reel.sh <url> -- --cookies-from-browser chrome
set -euo pipefail

URL=""; OUT=""; META=""; LANG_OPT="auto"; EXTRA=()
while [ $# -gt 0 ]; do
  case "$1" in
    --lang) LANG_OPT="$2"; shift 2 ;;
    --meta) META="$2"; shift 2 ;;
    --) shift; EXTRA=("$@"); break ;;
    *) if [ -z "$URL" ] && [ -z "$META" ]; then URL="$1"; elif [ -z "$OUT" ]; then OUT="$1"; fi; shift ;;
  esac
done
[ -n "$URL$META" ] || { echo "usage: reel.sh <url> | --meta <fetch.json> [output-dir] [--lang xx] [-- yt-dlp args]"; exit 2; }

MODEL="${WHISPER_MODEL:-$HOME/.cache/whisper-models/ggml-small.bin}"
for tool in yt-dlp whisper-cli ffmpeg ffprobe node curl; do
  command -v "$tool" >/dev/null || { echo "missing $tool (brew install yt-dlp whisper-cpp ffmpeg node)"; exit 2; }
done
[ -f "$MODEL" ] || { echo "missing whisper model at $MODEL"; echo "get it: mkdir -p ~/.cache/whisper-models && curl -L -o ~/.cache/whisper-models/ggml-small.bin https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.bin"; exit 2; }

if [ -n "$META" ]; then
  META="$(cd "$(dirname "$META")" && pwd)/$(basename "$META")"
  ERR="$(node -e 'const j=require(process.argv[1]); process.stdout.write(j.error||"")' "$META")"
  [ -z "$ERR" ] || { echo "fetch error: $ERR"; exit 3; }
  ID="$(node -e 'process.stdout.write(require(process.argv[1]).shortcode)' "$META")"
  URL="$(node -e 'process.stdout.write(require(process.argv[1]).url)' "$META")"
else
  ID="$(yt-dlp --get-id "$URL" ${EXTRA[@]+"${EXTRA[@]}"} 2>/dev/null | head -1 || true)"
fi
[ -n "$ID" ] || ID="reel-$(date +%Y%m%d-%H%M%S)"
[ -n "$OUT" ] || OUT="${REEL_OUT:-$HOME/reels}/$ID"
mkdir -p "$OUT/frames"
cd "$OUT"

echo "== downloading $URL"
if [ -n "$META" ]; then
  cp "$META" info.json
  node -e '
    const fs = require("fs"), j = JSON.parse(fs.readFileSync("info.json", "utf8"));
    fs.writeFileSync("caption.txt", (j.caption || "").trim() + "\n");
    const meta = { webpage_url: j.url };
    for (const k of ["uploader", "full_name", "upload_date", "duration", "like_count", "comment_count", "play_count"]) if (j[k] != null) meta[k] = j[k];
    fs.writeFileSync("meta.json", JSON.stringify(meta, null, 2) + "\n");
  '
  VURL="$(node -e 'process.stdout.write(require("./info.json").video_url||"")')"
  if [ -z "$VURL" ]; then
    # image post / carousel without video: the slides are the content
    i=0
    node -e 'for (const u of require("./info.json").image_urls||[]) console.log(u)' | while read -r u; do
      i=$((i + 1)); curl -sSfL -o "frames/slide-$(printf '%02d' "$i").jpg" "$u"
    done
    { echo "# Post: $URL"; echo; echo '```json'; cat meta.json; echo '```'; echo
      echo "## Caption"; echo; cat caption.txt; echo
      echo "## Slides (image post, no audio)"; echo; ls frames | sed 's/^/- frames\//'; } > summary.md
    echo "== done: $OUT (image post)"; echo "$OUT/summary.md"; exit 0
  fi
  curl -sSfL -o video.mp4 "$VURL"
else
  yt-dlp -q --no-warnings --write-info-json -o "video.%(ext)s" --remux-video mp4 ${EXTRA[@]+"${EXTRA[@]}"} "$URL"
  INFO="$(ls video.info.json 2>/dev/null | head -1 || true)"
  if [ -n "$INFO" ]; then
    mv "$INFO" info.json
    node -e '
      const j = JSON.parse(require("fs").readFileSync("info.json", "utf8"));
      require("fs").writeFileSync("caption.txt", (j.description || j.title || "").trim() + "\n");
      const pick = ["uploader", "channel", "title", "upload_date", "duration", "view_count", "like_count", "comment_count", "webpage_url"];
      const meta = {}; for (const k of pick) if (j[k] != null) meta[k] = j[k];
      require("fs").writeFileSync("meta.json", JSON.stringify(meta, null, 2) + "\n");
    '
  fi
fi
VIDEO="$(ls video.mp4 video.* 2>/dev/null | grep -v "\.json$" | head -1)"

echo "== audio"
ffmpeg -v error -y -i "$VIDEO" -vn -ac 1 -ar 16000 -c:a pcm_s16le audio.wav

echo "== transcript (whisper $LANG_OPT)"
whisper-cli -m "$MODEL" -f audio.wav -l "$LANG_OPT" -otxt -osrt -of transcript -np >/dev/null 2>&1 || true
[ -f transcript.txt ] || : > transcript.txt

echo "== frames"
DUR="$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$VIDEO" | cut -d. -f1)"
ffmpeg -v error -y -i "$VIDEO" -vf "fps=1/2,scale=720:-2" -q:v 3 "frames/t-%03d.jpg"
# rename to timestamps (frame n is at (n-1)*2 seconds)
for f in frames/t-*.jpg; do
  n="$(basename "$f" .jpg | sed 's/t-0*//')"; n="${n:-0}"
  mv "$f" "frames/t-$(printf '%05.1f' "$(( (n - 1) * 2 ))").jpg" 2>/dev/null || true
done
# scene changes on top of the 2 s grid
ffmpeg -v error -y -i "$VIDEO" -vf "select='gt(scene,0.35)',showinfo,scale=720:-2" -vsync vfr -q:v 3 "frames/scene-%03d.jpg" 2>/dev/null || true

{
  echo "# Reel: $URL"
  echo
  if [ -f meta.json ]; then echo '```json'; cat meta.json; echo '```'; echo; fi
  echo "## Caption"; echo; cat caption.txt 2>/dev/null || echo "(none)"; echo
  echo "## Transcript (whisper, $LANG_OPT)"; echo
  if [ -s transcript.txt ]; then cat transcript.txt; else echo "(no speech detected: music or silent reel; read the frames)"; fi
  echo
  echo "## Frames"; echo; ls frames | sed 's/^/- frames\//'
} > summary.md

echo "== done: $OUT (${DUR}s)"
echo "$OUT/summary.md"
