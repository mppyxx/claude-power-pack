---
name: reel-reader
description: Watch an Instagram Reel, TikTok, YouTube Short or any public short video from a URL: download it, transcribe the speech locally with Whisper, pull the caption and on-screen key frames, and hand back a folder you can read and discuss. Use whenever the user shares a reel or short-video link and wants it understood, summarised, followed as instructions, or turned into a brief.
---

# Reel Reader

One command turns a reel into a folder with everything needed to understand it.

**TikTok, YouTube Shorts, anything else:**

```bash
~/.claude/skills/reel-reader/reel.sh "<url>" [output-dir]
```

**Instagram** usually works the same way for public reels. If yt-dlp says login is required, ask the user before using their own browser session, then pass its cookies through to yt-dlp:

```bash
~/.claude/skills/reel-reader/reel.sh "<instagram-url>" [output-dir] -- --cookies-from-browser chrome
```

Never type credentials, never solve login challenges, and never use someone else's account. If the user has a separate account for automation, prefer that browser profile.

Default output: `~/reels/<video-id>/` (or `$REEL_OUT/<video-id>/`, or the given dir). It contains:

| File | What |
|---|---|
| `info.json` | Metadata: uploader, caption, like/view counts, duration, upload date (from yt-dlp) |
| `caption.txt` | The post caption on its own |
| `video.mp4` | The reel |
| `audio.wav` | 16 kHz mono audio (what Whisper read) |
| `transcript.txt` | Clean speech transcript (spoken words only) |
| `transcript.srt` | The same with timestamps |
| `frames/` | One JPEG every 2 seconds plus scene changes, named by timestamp (`t-004.0.jpg`) |
| `summary.md` | Metadata block plus the transcript, ready to read first |

## How to use the output

1. Read `summary.md` first (caption + transcript).
2. Read the frames with the Read tool (they render as images) to see on-screen text, UI, products or steps the speech does not mention. Reels often carry the real instructions as burned-in text.
3. Then discuss, summarise, or follow the instructions with the user. Quote the transcript when precision matters; say when something comes from a frame rather than the audio.

## Notes

- Speech-to-text runs locally with `whisper-cli` and the multilingual `ggml-small` model, so most languages work; pass `--lang hi` or `--lang en` after the URL to force a language when auto-detect picks wrong. Music-only reels produce an empty transcript; the frames carry the content then.
- A private or deleted post fails to download. Say so; don't try to get around it.
- Requirements: `brew install yt-dlp whisper-cpp ffmpeg node`, plus the model: `mkdir -p ~/.cache/whisper-models && curl -L -o ~/.cache/whisper-models/ggml-small.bin https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.bin` (about 490 MB).
- Nothing is posted, liked or sent; the script only reads public content.
