#!/usr/bin/env python3
"""resolve-editor media lab: understand the footage before anyone edits it.

Usage:  media_lab.py LAB COMMAND [args]
        media_lab.py doctor | commands          (these two need no LAB)

LAB is the working folder of one edit: a path, or a plain name that lives under <home>/resolve-editor-labs
(RE_LABS_ROOT moves that root). The lab writes LAB/media/index.json plus low-res proxies, shots, word level
transcripts with pauses and fillers, music beats, loudness, per-shot quality and contact sheets. edit_lab.py and
the editing agents read those files. `media_lab.py commands` lists every command with its usage.

Needs Python 3.10+, numpy, Pillow, ffmpeg and ffprobe. Speech to text uses whisper.cpp when present (whisper-cli
with a ggml model file, or the pywhispercpp package); without it a transcript holds only the pauses.
Environment: RE_FFMPEG, RE_FFPROBE, RE_WHISPER_CLI, RE_WHISPER_MODEL, RE_LABS_ROOT.

Times: shots use media frames (half-open, from the first decoded video frame); words, pauses and beats use seconds
from the same origin. Rates are "num/den" strings in every JSON file.
"""
import argparse
import copy
import datetime
import glob
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction

import numpy as np

VERSION = "1.0"
HERE = os.path.dirname(os.path.abspath(__file__))
PRESETS_DIR = os.path.join(HERE, "presets")
MODELS_DIR = os.path.join(HERE, "models")

VIDEO_EXT = (".mp4", ".mov", ".mxf", ".mts", ".m2ts", ".avi", ".mkv", ".m4v", ".webm", ".mpg", ".mpeg", ".3gp",
             ".insv", ".mod", ".tod", ".wmv", ".flv", ".ts")
AUDIO_EXT = (".wav", ".aif", ".aiff", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".caf", ".wma", ".bwf")
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".heic", ".dpx", ".exr", ".tga")

# The verified whisper prompt: it makes whisper keep fillers such as "um" and "uh" and restores punctuation.
FILLER_PROMPT = "Umm, let me think like, hmm... Okay, here's what I'm, like, thinking."
FILLERS = {"um", "umm", "ummm", "uh", "uhh", "uhm", "er", "erm", "ah", "ahh", "hmm", "hm", "mm", "mmm"}
FLAG_WORDS = {"like", "so"}
FLAG_PHRASES = (("you", "know"), ("i", "mean"))

MAX_SIDE = 2000          # every image the lab writes stays at or under this many pixels on each side
MIN_SHOT_S = 0.4
PAUSE_MIN_S = 0.12
SENTENCE_PAUSE_S = 0.7
STEP_VERSION = {"proxies": 2, "shots": 1, "transcribe": 3, "beats": 5, "loudness": 1, "quality": 1, "sheets": 1}
MEDIA_STEPS = ("proxies", "shots", "transcribe", "beats", "loudness", "quality")
ALL_STEPS = MEDIA_STEPS + ("sheets",)

COMMANDS = [
    ("doctor", "media_lab.py doctor [--json]"),
    ("commands", "media_lab.py commands"),
    ("add", "media_lab.py LAB add PATH... [--from-dump LAB/resolve/dump.json [--with-timeline]] [--music ID...] "
            "[--voiceover ID...] [--sfx ID...]"),
    ("estimate", "media_lab.py LAB estimate [--only ID,...] [--json]"),
    ("ingest", "media_lab.py LAB ingest [--only ID,...] [--asr auto|whisper-cli|pywhispercpp|none] [--model small|base|PATH] "
               "[--language auto|en|...] [--speakers K] [--proxy-height 360|720] [--jobs N] [--force]"),
    ("proxies", "media_lab.py LAB proxies [--only ID,...] [--proxy-height 360|720] [--jobs N] [--force]"),
    ("shots", "media_lab.py LAB shots [--only ID,...] [--force]"),
    ("transcribe", "media_lab.py LAB transcribe [--only ID,...] [--asr auto|whisper-cli|pywhispercpp|none] [--model M] "
                   "[--language L] [--speakers K] [--force]"),
    ("beats", "media_lab.py LAB beats [--only ID,...] [--bpm N|auto] [--first-downbeat S|auto [--move-grid]] "
              "[--shift-bar N] [--force]"),
    ("loudness", "media_lab.py LAB loudness [--only ID,...] [--force]"),
    ("quality", "media_lab.py LAB quality [--only ID,...] [--force]"),
    ("sheets", "media_lab.py LAB sheets [--layout log|zoom] [--only ID,...|SHOT,...] [--out DIR] [--force]"),
    ("transcript", "media_lab.py LAB transcript ID [--from W] [--to W] [--width 110]"),
    ("shotlist", "media_lab.py LAB shotlist [ID]"),
    ("search", "media_lab.py LAB search \"TEXT\" [--media ID]"),
    ("shotlog-check", "media_lab.py LAB shotlog-check FILE [--json]"),
    ("shotlog-merge", "media_lab.py LAB shotlog-merge IN..."),
    ("qc", "media_lab.py LAB qc FILE --preset ID [--platform P] [--edl EDL] [--out DIR] [--json]"),
    ("status", "media_lab.py LAB status [--json]"),
    ("clean", "media_lab.py LAB clean [--proxies] [--all]"),
    ("script", "media_lab.py LAB script ID (--file PATH | --text \"TEXT\") [--force] [--json]  align the known "
               "voice-over text to the transcript"),
    ("fix-word", "media_lab.py LAB fix-word ID INDEX \"TEXT\"  one word's text (times kept)"),
    ("edge", "media_lab.py LAB edge ID INDEX SECONDS  move the edge before word INDEX (heard or seen on the waveform)"),
    ("click", "media_lab.py LAB click ID [--from S] [--dur S] [--out FILE.wav]  the music with a click on every beat "
              "and a high click on bar 1"),
    ("peek", "media_lab.py LAB peek MEDIA|SHOT --at T,T,... [--width 360] [--out DIR]  source frames side by side "
             "with the crop guide"),
]
COMMAND_NAMES = [c for c, _ in COMMANDS]
NO_LAB = ("doctor", "commands")


class Fail(Exception):
    """An error the user can fix; printed as one plain sentence without a traceback."""


# ---------------------------------------------------------------- small helpers
def tool(name):
    return os.environ.get("RE_" + name.upper()) or name


def ffmpeg_bin():
    return tool("ffmpeg")


def ffprobe_bin():
    return tool("ffprobe")


def iso_now():
    return datetime.datetime.now().replace(microsecond=0).isoformat()


def posix(p):
    return os.path.abspath(p).replace("\\", "/")


def load_json(fn):
    with open(fn, encoding="utf-8") as fh:
        try:
            return json.load(fh)
        except ValueError as e:
            raise Fail("%s is not valid JSON (%s); fix the file and run the command again" % (fn, e))


def write_json(fn, obj):
    """Atomic UTF-8 JSON write (name.tmp, then os.replace)."""
    d = os.path.dirname(os.path.abspath(fn))
    os.makedirs(d, exist_ok=True)
    tmp = fn + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=1, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, fn)
    return fn


def utf8_stdio():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def r3(x):
    return None if x is None else round(float(x), 3)


def parse_fps(x):
    """Rate as a Fraction. Accepts "30000/1001", "25", 25, 29.97 or "29.97"; NTSC rates snap to N*1000/1001."""
    if x is None:
        return None
    if isinstance(x, Fraction):
        return x if x > 0 else None
    if isinstance(x, str):
        s = x.strip()
        if not s:
            return None
        if "/" in s:
            a, b = s.split("/", 1)
            try:
                a, b = int(a), int(b)
            except ValueError:
                try:
                    a, b = float(a), float(b)
                except ValueError:
                    return None
            if not b or not a:
                return None
            if isinstance(a, int) and isinstance(b, int):
                f = Fraction(a, b)
                return _snap_fps(f)
            x = a / b
        else:
            try:
                x = float(s)
            except ValueError:
                return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if not v > 0 or math.isinf(v) or math.isnan(v):
        return None
    if abs(v - round(v)) < 1e-6:
        return Fraction(int(round(v)))
    return _snap_fps(Fraction(v).limit_denominator(1001))


def _snap_fps(f):
    v = float(f)
    for n in (24, 30, 48, 60, 120):
        if abs(v - n * 1000 / 1001) < 0.01:
            return Fraction(n * 1000, 1001)
    if abs(v - round(v)) < 1e-6:
        return Fraction(int(round(v)))
    return f


def fps_str(fr):
    fr = parse_fps(fr)
    return None if fr is None else "%d/%d" % (fr.numerator, fr.denominator)


def tc_to_seconds(tc, fps):
    """Timecode (HH:MM:SS:FF or HH:MM:SS;FF) to seconds at the nominal rate, with drop frame for ; at 29.97/59.94."""
    fr = parse_fps(fps)
    m = re.match(r"^(\d+):(\d+):(\d+)([:;.])(\d+)$", str(tc or "").strip())
    if not m or fr is None:
        return None
    h, mi, s, sep, f = int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4), int(m.group(5))
    nom = int(round(float(fr)))
    frames = ((h * 60 + mi) * 60 + s) * nom + f
    if sep == ";" and nom in (30, 60):
        drop = 2 * nom // 30
        mins = h * 60 + mi
        frames -= drop * (mins - mins // 10)
    return float(frames / fr)


def file_hash(path):
    """sha1(str(size) + first 4 MiB + last 1 MiB)[:16]; files under 5 MiB are hashed whole once."""
    size = os.path.getsize(path)
    h = hashlib.sha1(str(size).encode())
    with open(path, "rb") as fh:
        if size < 5 * 2 ** 20:
            h.update(fh.read())
        else:
            h.update(fh.read(4 * 2 ** 20))
            fh.seek(size - 2 ** 20)
            h.update(fh.read(2 ** 20))
    return h.hexdigest()[:16]


def safe_id(s):
    return re.sub(r"[^A-Za-z0-9_-]", "_", s) or "media"


def safe_lab_name(s):
    return re.sub(r"[^A-Za-z0-9_-]", "_", s) or "edit"


def labs_root():
    r = os.environ.get("RE_LABS_ROOT")
    return os.path.abspath(os.path.expanduser(r)) if r else os.path.join(os.path.expanduser("~"), "resolve-editor-labs")


def lab_path(arg):
    """A LAB argument is a path, or a plain edit name that lives under the labs root."""
    a = os.path.expanduser(arg)
    if os.path.isabs(a) or os.sep in a or "/" in a or a.startswith(".") or os.path.isdir(a):
        return os.path.abspath(a)
    return os.path.join(labs_root(), safe_lab_name(arg))


def run(cmd, timeout=None, check=True, text=True):
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout, text=text,
                           **({"encoding": "utf-8", "errors": "replace"} if text else {}))
    except FileNotFoundError:
        raise Fail("%s was not found. Install ffmpeg (macOS: brew install ffmpeg, Windows: winget install "
                   "Gyan.FFmpeg, Linux: sudo apt install ffmpeg) or set RE_FFMPEG and RE_FFPROBE" % cmd[0])
    except subprocess.TimeoutExpired:
        raise Fail("%s took longer than %d s and was stopped" % (os.path.basename(cmd[0]), timeout))
    if check and p.returncode:
        err = p.stderr if text else p.stderr.decode("utf-8", "replace")
        raise Fail("%s failed: %s" % (os.path.basename(cmd[0]), (err or "").strip()[-400:] or "exit %d" % p.returncode))
    return p


def ff_base():
    return [ffmpeg_bin(), "-nostdin", "-hide_banner", "-loglevel", "error"]


_FF_MAJOR = []


def passthrough_args():
    """Keep every decoded frame exactly once: -fps_mode passthrough (ffmpeg 5.1+) or -vsync passthrough (older)."""
    if not _FF_MAJOR:
        v = ffmpeg_version() or ""
        mm = re.match(r"n?(\d+)\.(\d+)", v)
        _FF_MAJOR.append((int(mm.group(1)), int(mm.group(2))) if mm else (99, 0))
    return ["-fps_mode", "passthrough"] if _FF_MAJOR[0] >= (5, 1) else ["-vsync", "passthrough"]


def decode_frames(path, w, h, pix="gray", every=1, batch=256, extra_vf=None):
    """Stream frames of `path` scaled to w x h through a pipe. Yields (first_index, array) where the index counts
    source frames (every `every`-th frame is decoded into the output). Never holds the whole clip in memory."""
    ch = {"gray": 1, "rgb24": 3}[pix]
    vf = []
    if every > 1:
        vf.append("select='not(mod(n,%d))'" % every)
    vf.append("scale=%d:%d:flags=area" % (w, h))
    if extra_vf:
        vf.append(extra_vf)
    cmd = ff_base() + ["-i", path, "-map", "0:v:0", "-vf", ",".join(vf)] + passthrough_args() + \
        ["-f", "rawvideo", "-pix_fmt", pix, "-"]
    fb = w * h * ch
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except FileNotFoundError:
        raise Fail("ffmpeg was not found; install it or set RE_FFMPEG")
    k = 0
    err = []
    t = threading.Thread(target=lambda: err.append(p.stderr.read()), daemon=True)
    t.start()
    try:
        while True:
            buf = p.stdout.read(fb * batch)
            if not buf:
                break
            n = len(buf) // fb
            if n == 0:
                break
            a = np.frombuffer(buf[:n * fb], np.uint8)
            a = a.reshape(n, h, w) if ch == 1 else a.reshape(n, h, w, 3)
            yield k * every, a
            k += n
    finally:
        try:
            p.stdout.close()
        except Exception:
            pass
        p.wait()
        t.join(timeout=5)
        try:
            p.stderr.close()
        except Exception:
            pass
    if p.returncode and k == 0:
        raise Fail("ffmpeg could not decode %s: %s" % (os.path.basename(path),
                                                        (err[0] if err else b"").decode("utf-8", "replace").strip()[-300:]))


def read_wav_i16(path):
    """First channel of a 16-bit PCM WAV as int16 (a long interview stays small in memory)."""
    import wave
    with wave.open(path, "rb") as w:
        ch, sw, n = w.getnchannels(), w.getsampwidth(), w.getnframes()
        if sw != 2:
            raise Fail("%s is not 16-bit PCM" % path)
        a = np.frombuffer(w.readframes(n), "<i2")
    return a[::ch].copy() if ch > 1 else a


def decode_audio(path, sr, channels=None, start=None, dur=None):
    """Any file with audio to float32 (n, ch) through ffmpeg."""
    cmd = ff_base()
    if start is not None:
        cmd += ["-ss", "%.3f" % start]
    if dur is not None:
        cmd += ["-t", "%.3f" % dur]
    cmd += ["-i", path, "-map", "0:a:0", "-f", "f32le", "-acodec", "pcm_f32le", "-ar", str(sr)]
    if channels:
        cmd += ["-ac", str(channels)]
    cmd += ["-"]
    p = run(cmd, text=False, timeout=3600)
    x = np.frombuffer(p.stdout, "<f4")
    if channels is None:
        channels = probe_audio_channels(path) or 1
    return x[: len(x) // channels * channels].reshape(-1, channels)


def probe_audio_channels(path):
    p = run([ffprobe_bin(), "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=channels", "-of",
             "csv=p=0", path], check=False, timeout=60)
    try:
        return int((p.stdout or "").strip().split("\n")[0])
    except ValueError:
        return None


def free_gb(path):
    p = path
    while p and not os.path.exists(p):
        p = os.path.dirname(p)
    try:
        return shutil.disk_usage(p or "/").free / 2 ** 30
    except OSError:
        return None


# ---------------------------------------------------------------- lab and index
class Lab:
    def __init__(self, arg, create=True):
        self.root = lab_path(arg)
        if not os.path.isdir(self.root):
            if not create:
                raise Fail("LAB folder %s does not exist; run add first" % self.root)
            os.makedirs(self.root, exist_ok=True)
        self.index_file = os.path.join(self.root, "media", "index.json")
        self.lock = threading.Lock()
        self.index = self._load()
        self._saved = json.dumps(self.index, sort_keys=True)
        self.writes = 0

    def _load(self):
        if os.path.exists(self.index_file):
            d = load_json(self.index_file)
            d.setdefault("media", {})
            return d
        return {"schema": "resolve-editor/media-index@1", "updated": iso_now(),
                "tools": {"media_lab": VERSION, "ffmpeg": None, "asr": None}, "media": {}}

    def p(self, *parts):
        return os.path.join(self.root, *parts)

    def rel(self, path):
        return os.path.relpath(path, self.root).replace("\\", "/")

    def absp(self, rel):
        return None if not rel else os.path.join(self.root, *rel.split("/"))

    def media(self, only=None):
        ids = list(self.index["media"].keys())
        if only:
            want = [x.strip() for x in only.split(",") if x.strip()] if isinstance(only, str) else list(only)
            bad = [x for x in want if x not in self.index["media"]]
            if bad:
                raise Fail("unknown media id %s; run status to see the ids" % ", ".join(bad))
            ids = [i for i in ids if i in want]
        return ids

    def get(self, mid):
        m = self.index["media"].get(mid)
        if m is None:
            raise Fail("unknown media id %s; run status to see the ids" % mid)
        return m

    def save(self, force=False):
        with self.lock:
            cur = json.dumps(self.index, sort_keys=True)
            if cur == self._saved and not force and os.path.exists(self.index_file):
                return False
            self.index["updated"] = iso_now()
            write_json(self.index_file, self.index)
            self._saved = json.dumps(self.index, sort_keys=True)
            self.writes += 1
            return True

    def put(self, m):
        with self.lock:
            self.index["media"][m["id"]] = copy.deepcopy(m)


def set_error(m, step, msg):
    m["errors"] = [e for e in m.get("errors", []) if not e.startswith(step + ": ")]
    if msg:
        m["errors"].append("%s: %s" % (step, msg))


def step_sig(*parts):
    return hashlib.sha1(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:16]


# ---------------------------------------------------------------- probe
def _rate(s):
    return parse_fps(s) if s and s not in ("0/0", "0/1") else None


def _start_time(st):
    try:
        return float(st.get("start_time"))
    except (TypeError, ValueError):
        return None


COMMON_RATES = [Fraction(24000, 1001), Fraction(24), Fraction(25), Fraction(30000, 1001), Fraction(30),
                Fraction(48000, 1001), Fraction(48), Fraction(50), Fraction(60000, 1001), Fraction(60), Fraction(100),
                Fraction(120000, 1001), Fraction(120), Fraction(240)]


def pick_rate(v):
    """(rate, variable) from ffprobe's r_frame_rate (base rate) and avg_frame_rate (frames over duration).
    They agree for constant rate files; phones often report a timebase-like r_frame_rate and a true average."""
    rr, av = _rate(v.get("r_frame_rate")), _rate(v.get("avg_frame_rate"))
    if rr and av and abs(float(av) - float(rr)) / float(rr) < 0.002:
        return rr, False
    if rr and not av:
        return rr, False
    if av:
        near = min(COMMON_RATES, key=lambda c: abs(float(c) - float(av)))
        snapped = near if abs(float(near) - float(av)) / float(near) < 0.005 else av
        return snapped, rr is not None
    return None, False


def probe(path):
    """Entry fields for one file (no id). Raises Fail when the file is missing or unreadable."""
    if not os.path.isfile(path):
        raise Fail("file not found: %s" % path)
    js = json.loads(run([ffprobe_bin(), "-v", "error", "-show_streams", "-show_format", "-of", "json", path],
                        timeout=120).stdout or "{}")
    streams = js.get("streams", [])
    fmt = js.get("format", {})
    ext = os.path.splitext(path)[1].lower()
    vids = [s for s in streams if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")]
    auds = [s for s in streams if s.get("codec_type") == "audio"]
    fname = fmt.get("format_name", "")
    is_image = ext in IMAGE_EXT or fname in ("image2", "png_pipe", "jpeg_pipe", "tiff_pipe", "webp_pipe", "bmp_pipe")
    if not vids and not auds:
        raise Fail("no video or audio stream in %s" % os.path.basename(path))
    e = {"path": posix(path), "name": os.path.basename(path), "hash": file_hash(path),
         "size_bytes": os.path.getsize(path)}
    v = vids[0] if vids else None
    a = auds[0] if auds else None
    if is_image and v:
        kind = "image"
    elif v and a:
        kind = "av"
    elif v:
        kind = "video"
    else:
        kind = "audio"
    e["kind"] = kind
    warnings = []
    flags = []
    if kind in ("av", "video"):
        fr, is_vfr = pick_rate(v)
        if fr is None:
            raise Fail("cannot read the frame rate of %s" % os.path.basename(path))
        if is_vfr:
            flags.append("vfr")
        frames = _count_frames(path, v, fr, fmt)
        e["fps"] = fps_str(fr)
        e["frames"] = frames
        e["duration_s"] = round(frames / float(fr), 3)
        tc = (v.get("tags") or {}).get("timecode") or (fmt.get("tags") or {}).get("timecode")
        if not tc:
            for s in streams:
                if s.get("codec_type") == "data" and (s.get("tags") or {}).get("timecode"):
                    tc = s["tags"]["timecode"]
                    break
        e["start_tc"] = tc or "00:00:00:00"
        if tc_to_seconds(e["start_tc"], fr) is None:
            warnings.append("unreadable timecode %r, using 00:00:00:00" % tc)
            e["start_tc"] = "00:00:00:00"
        e["width"], e["height"] = int(v.get("width") or 0), int(v.get("height") or 0)
        rot = 0
        for sd in v.get("side_data_list") or []:
            if "rotation" in sd:
                try:
                    rot = int(round(float(sd["rotation"])))
                except (TypeError, ValueError):
                    pass
        if not rot and (v.get("tags") or {}).get("rotate"):
            try:
                rot = int((v.get("tags") or {})["rotate"])
            except ValueError:
                pass
        rot = rot % 360
        e["rotation"] = rot
        if rot in (90, 270):
            flags.append("rotated")
        e["sar"] = (v.get("sample_aspect_ratio") or "1:1").replace("0:1", "1:1")
        e["codec"] = v.get("codec_name")
        e["pix_fmt"] = v.get("pix_fmt")
        e["gop"] = _gop(path)
        if e["gop"] == "long":
            flags.append("long_gop")
        trc = v.get("color_transfer") or ""
        e["color"] = {"primaries": v.get("color_primaries"), "transfer": v.get("color_transfer"),
                      "range": v.get("color_range"), "flat_log_guess": bool(re.search(r"log", trc, re.I))}
    else:
        e.update({"fps": None, "frames": None})
        if kind == "image":
            e["duration_s"] = None
            e["width"], e["height"] = int(v.get("width") or 0), int(v.get("height") or 0)
            e["codec"] = v.get("codec_name")
    if a is not None and kind != "image":
        rate = int(a.get("sample_rate") or 0)
        off = 0.0
        if v is not None:
            sa, sv = _start_time(a), _start_time(v)
            if sa is not None and sv is not None:
                off = round(sa - sv, 4)
        e["audio"] = {"channels": int(a.get("channels") or 0), "rate": rate, "codec": a.get("codec_name"),
                      "offset_s": off, "streams": [int(x.get("channels") or 0) for x in auds]}
        if kind == "audio":
            try:
                dur = float(a.get("duration") or fmt.get("duration"))
            except (TypeError, ValueError):
                dur = None
            e["duration_s"] = r3(dur)
            e["samples"] = int(round(dur * rate)) if dur and rate else None
    else:
        e["audio"] = None
        if kind == "video":
            flags.append("no_audio")
    e["_flags"] = flags
    e["_warnings"] = warnings
    return e


def _count_frames(path, v, fr, fmt):
    p = run([ffprobe_bin(), "-v", "error", "-select_streams", "v:0", "-count_packets", "-show_entries",
             "stream=nb_read_packets", "-of", "csv=p=0", path], timeout=1800, check=False)
    try:
        n = int((p.stdout or "").strip().split("\n")[0].strip(","))
        if n > 0:
            return n
    except ValueError:
        pass
    try:
        n = int(v.get("nb_frames") or 0)
        if n > 0:
            return n
    except ValueError:
        pass
    try:
        return int(round(float(v.get("duration") or fmt.get("duration")) * float(fr)))
    except (TypeError, ValueError):
        raise Fail("cannot count the frames of %s" % os.path.basename(path))


def _gop(path):
    p = run([ffprobe_bin(), "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=flags",
             "-read_intervals", "%+#200", "-of", "csv=p=0", path], check=False, timeout=120)
    fl = [x.strip() for x in (p.stdout or "").split("\n") if x.strip()]
    if not fl:
        return None
    return "intra" if all(x.startswith("K") for x in fl) else "long"


def new_entry(mid, pr):
    """Index entry in schema key order (5.1) plus the lab's bookkeeping keys at the end."""
    e = {"id": mid, "path": pr["path"], "name": pr["name"], "hash": pr["hash"], "kind": pr["kind"],
         "fps": pr.get("fps"), "frames": pr.get("frames"), "duration_s": pr.get("duration_s")}
    if pr["kind"] == "audio":
        e["samples"] = pr.get("samples")
    for k in ("start_tc", "width", "height", "rotation", "sar", "codec", "pix_fmt", "gop", "color"):
        if k in pr:
            e[k] = pr[k]
    e["audio"] = pr.get("audio")
    e["resolve"] = {"mpi_uid": None, "bin": None}
    e.update({"proxy": None, "proxy_frames": None, "proxy_size": None, "wav": None, "wav16k": None,
              "analysis": {"shots": None, "words": None, "beats": None, "quality": None, "loudness": None},
              "summary": {"shots": None, "speech_s": None, "words": None, "lufs": None, "bpm": None,
                          "flags": sorted(set(pr.get("_flags", [])))},
              "errors": ["probe: " + w for w in pr.get("_warnings", [])]})
    e["size_bytes"] = pr.get("size_bytes")
    e["music"] = False
    e["done"] = {}
    e["timings"] = {}
    return e


def add_flag(m, flag, on=True):
    fl = set(m["summary"].get("flags") or [])
    if on:
        fl.add(flag)
    else:
        fl.discard(flag)
    m["summary"]["flags"] = sorted(fl)


# ---------------------------------------------------------------- add / estimate / status
def scan_paths(paths):
    out = []
    for p in paths:
        p = os.path.abspath(os.path.expanduser(p))
        if os.path.isdir(p):
            for root, dirs, files in os.walk(p):
                dirs[:] = sorted(d for d in dirs if not d.startswith("."))
                for f in sorted(files):
                    if f.startswith("."):
                        continue
                    if os.path.splitext(f)[1].lower() in VIDEO_EXT + AUDIO_EXT + IMAGE_EXT:
                        out.append(os.path.join(root, f))
        else:
            out.append(p)
    seen, res = set(), []
    for p in out:
        if p not in seen:
            seen.add(p)
            res.append(p)
    return res


def sfx_peak_s(path, sr=48000, win_ms=20):
    """Seconds into a sound effect of its loudest 20 ms window: where a whoosh or a hit lands (assemble puts this
    moment on the event frame; fusion research reel_effects 4.15)."""
    x = decode_audio(path, sr, channels=1)[:, 0].astype(np.float64)
    if not len(x):
        return None
    w = max(1, int(sr * win_ms / 1000.0))
    e = np.convolve(x * x, np.ones(w) / w, mode="same")
    return round(float(np.argmax(e)) / sr, 4)


def cmd_add(lab, paths, from_dump=None, music=None, with_timeline=False, voiceover=None, sfx=None):
    extra = {}
    paths = list(paths or [])
    dump_notes = []
    if from_dump:
        d = load_json(from_dump)
        items = d.get("media", [])
        # a dump made with --bin marks where each clip was found: then only the bins' media is the footage (the
        # current timeline may hold anything); --with-timeline adds the timeline's clips too
        binned = any(it.get("from_bin") for it in items)
        left = set()
        for it in items:
            ty = str(it.get("type") or "")
            # timelines, compound clips and generators live in the pool but are not files
            if not it.get("path") or any(w in ty for w in ("Timeline", "Compound", "Generator", "Fusion")):
                continue
            if binned and not it.get("from_bin") and not with_timeline:
                left.add(posix(it["path"]))
                continue
            paths.append(it["path"])
            extra[posix(it["path"])] = {"mpi_uid": it.get("mpi_uid"), "bin": it.get("bin"),
                                        "fps": it.get("fps"), "frames": it.get("frames"),
                                        "audio_tracks": it.get("audio_tracks")}
        bins = d.get("bins") or {}
        if binned:
            dump_notes.append("from the dump: the media of the bin%s %s" % ("s" if len(bins) > 1 else "", ", ".join(
                "%s (%d)" % (k, v) for k, v in bins.items()) or "named in dump-script"))
        if left:
            dump_notes.append("%d file%s only on the current timeline left out (add --with-timeline to add them too)"
                              % (len(left), "" if len(left) == 1 else "s"))
        for k, v in bins.items():
            if not v:
                dump_notes.append("no bin named %r was found in the project (check the name, then run dump-script "
                                  "again)" % k)
    files = scan_paths(paths)
    if not files and not music and not voiceover and not sfx:
        raise Fail("no media files found in the paths given%s" % ("".join("; " + n for n in dump_notes)))
    by_path = {m["path"]: mid for mid, m in lab.index["media"].items()}
    # a file that moved (a renamed folder, another drive letter) is found again by its content and keeps its id
    gone = {m.get("hash"): mid for mid, m in lab.index["media"].items() if m.get("hash") and not os.path.exists(m["path"])}
    added, updated, same, failed, moved, remapped = [], [], [], [], [], []
    for f in files:
        pf = posix(f)
        mid = by_path.get(pf)
        try:
            pr = probe(f)
        except Fail as e:
            if mid:
                set_error(lab.index["media"][mid], "probe", str(e))
            failed.append((pf, str(e)))
            continue
        if mid is None and pr["hash"] in gone:
            mid = gone.pop(pr["hash"])
            m = lab.index["media"][mid]
            by_path.pop(m["path"], None)
            m["path"] = pf
            m["name"] = os.path.basename(pf)
            by_path[pf] = mid
            set_error(m, "probe", None)
            moved.append(mid)
        elif mid:
            m = lab.index["media"][mid]
            if m["hash"] != pr["hash"]:
                keep = {k: m.get(k) for k in ("resolve", "music")}
                if m.get("role") == "sfx":
                    keep["role"] = "sfx"
                m2 = new_entry(mid, pr)
                m2.update({k: v for k, v in keep.items() if v is not None})
                m2["errors"].append("probe: the file changed since it was added (hash_changed); analyses will run again")
                lab.index["media"][mid] = m2
                updated.append(mid)
            else:
                same.append(mid)
        else:
            base = safe_id(os.path.splitext(os.path.basename(f))[0])
            mid, k = base, 2
            while mid in lab.index["media"]:
                mid = "%s_%d" % (base, k)
                k += 1
            lab.index["media"][mid] = new_entry(mid, pr)
            by_path[pf] = mid
            added.append(mid)
        if pf in extra:
            m = lab.index["media"][mid]
            old_at = audio_map_key(m)
            m["resolve"] = {"mpi_uid": extra[pf].get("mpi_uid"), "bin": extra[pf].get("bin")}
            if extra[pf].get("audio_tracks"):
                m["resolve"]["audio_tracks"] = extra[pf]["audio_tracks"]
            if mid in same and old_at != audio_map_key(m):
                remapped.append(mid)
            # Resolve and the lab must count the same frames, or the build lands on other frames than the preview
            rf, rn = extra[pf].get("fps"), extra[pf].get("frames")
            if m.get("fps") and m.get("kind") in ("av", "video") and (rf or rn):
                diff = []
                try:
                    if rf and abs(float(parse_fps(rf)) - float(parse_fps(m["fps"]))) > 0.01:
                        diff.append("Resolve reads %s fps, the lab %s fps" % (rf, m["fps"]))
                except (ValueError, TypeError, ZeroDivisionError):
                    pass
                try:
                    if rn not in (None, "") and m.get("frames") is not None and abs(int(float(rn)) - int(m["frames"])) > 1:
                        diff.append("Resolve counts %s frames, the lab %d" % (rn, int(m["frames"])))
                except (ValueError, TypeError):
                    pass
                if diff:
                    try:
                        cfr = std_rate(float(parse_fps(rf))) if rf else std_rate(float(parse_fps(m["fps"])))
                    except (ValueError, TypeError, ZeroDivisionError):
                        cfr = m["fps"]
                    msg = ("resolve: %s; the preview and the build would disagree. Make a constant frame rate copy "
                           "(ffmpeg -i IN -fps_mode cfr -r %s OUT), relink it in Resolve and add it again"
                           % ("; ".join(diff), cfr))
                    if msg not in m["errors"]:
                        m["errors"].append(msg)
                    print("warning %s: %s" % (mid, msg))
    for x in music or []:
        mid = x if x in lab.index["media"] else by_path.get(posix(x))
        if mid is None:
            cand = [i for i, m in lab.index["media"].items() if m["name"] == x or os.path.splitext(m["name"])[0] == x]
            mid = cand[0] if len(cand) == 1 else None
        if mid is None:
            raise Fail("--music %s matches no media id, path or file name in this lab" % x)
        lab.index["media"][mid]["music"] = True
        add_flag(lab.index["media"][mid], "music")
    vo_marked = []
    for x in voiceover or []:
        # narration recorded on its own: no separate_sound flag (nothing to sync) and no beat analysis
        mid = x if x in lab.index["media"] else by_path.get(posix(x))
        if mid is None:
            cand = [i for i, m in lab.index["media"].items() if m["name"] == x or os.path.splitext(m["name"])[0] == x]
            mid = cand[0] if len(cand) == 1 else None
        if mid is None:
            raise Fail("--voiceover %s matches no media id, path or file name in this lab" % x)
        m = lab.index["media"][mid]
        if m.get("music"):
            raise Fail("%s is marked as music; a voice-over is not music" % mid)
        had_sep = "separate_sound" in ((m.get("summary") or {}).get("flags") or [])
        m["role"] = "voiceover"
        add_flag(m, "separate_sound", False)
        add_flag(m, "voiceover")
        vo_marked.append((mid, had_sep))
    sfx_marked = []
    for x in sfx or []:
        # the user's own sound effects (whooshes, hits, risers, pops): no beat analysis and no transcript; the
        # loudest moment is stored so assemble can land it on its event frame
        mid = x if x in lab.index["media"] else by_path.get(posix(x))
        if mid is None:
            cand = [i for i, m in lab.index["media"].items() if m["name"] == x or os.path.splitext(m["name"])[0] == x]
            mid = cand[0] if len(cand) == 1 else None
        if mid is None:
            raise Fail("--sfx %s matches no media id, path or file name in this lab" % x)
        m = lab.index["media"][mid]
        if m.get("music") or m.get("role") == "voiceover":
            raise Fail("%s is marked as %s; a sound effect is neither" % (mid, "music" if m.get("music") else
                                                                         "a voice-over"))
        if not m.get("audio"):
            raise Fail("%s has no sound to use as a sound effect" % mid)
        m["role"] = "sfx"
        add_flag(m, "separate_sound", False)
        add_flag(m, "sfx")
        try:
            pk = sfx_peak_s(m["path"])
        except Fail as e:
            pk = None
            set_error(m, "sfx", "the peak could not be measured: %s" % e)
        m["sfx"] = {"peak_s": pk}
        sfx_marked.append((mid, pk))
    if lab.index.setdefault("tools", {}).get("ffmpeg") is None:
        lab.index["tools"]["ffmpeg"] = ffmpeg_version()
    lab.save()
    for mid in added:
        m = lab.index["media"][mid]
        print("added   %-24s %-5s %s" % (mid, m["kind"], _dur_txt(m)))
    for mid in updated:
        print("changed %-24s the file changed; its analyses will run again" % mid)
    for mid in same:
        if mid in remapped:
            print("known   %-24s audio mapping changed in Resolve: ingest makes its sound and loudness again" % mid)
        else:
            print("known   %-24s unchanged" % mid)
    for mid in moved:
        print("moved   %-24s found again at %s (same content, same id); assemble the cut lists again" % (
            mid, lab.index["media"][mid]["path"]))
    for mid, had_sep in vo_marked:
        print("voiceover %-22s marked as narration (no beat analysis%s)" % (
            mid, "; the separate_sound note is cleared" if had_sep else ", no separate_sound note"))
    for mid, pk in sfx_marked:
        print("sfx     %-24s marked as a sound effect (no beats, no transcript), loudest at %s" % (
            mid, "%.3f s" % pk if pk is not None else "an unknown time"))
    for pf, err in failed:
        print("failed  %s: %s" % (pf, err))
    for n in dump_notes:
        print("NOTE %s" % n)
    print("WROTE %s" % posix(lab.index_file))
    if failed and not (added or updated or same or moved):
        raise Fail("no file could be read (%d failed)" % len(failed))


def _dur_txt(m):
    d = m.get("duration_s")
    s = ("%.2f s" % d) if d else "-"
    if m.get("fps"):
        s += " %s fps %d f %dx%d" % (m["fps"], m["frames"], m.get("width") or 0, m.get("height") or 0)
    return s


def ffmpeg_version():
    try:
        out = run([ffmpeg_bin(), "-version"], timeout=20).stdout.split("\n")[0]
        m = re.search(r"version\s+(\S+)", out)
        return m.group(1) if m else out
    except Fail:
        return None


PX_RATE = 1.3e9          # 4K H.264 software decode + 360p encode, pixels per second (measured 1.3e9 to 2.1e9)


def estimate_rows(lab, ids, proxy_height=360):
    rows = []
    engine = asr_engine("auto")[0]
    mac = sys.platform == "darwin"
    for mid in ids:
        m = lab.index["media"][mid]
        d = m.get("duration_s") or 0.0
        secs, mb = 0.0, 0.0
        if m["kind"] in ("av", "video"):
            px = (m.get("width") or 1920) * (m.get("height") or 1080) * (m.get("frames") or 0)
            secs += px / PX_RATE
            mb += (m.get("frames") or 0) * 6e-3 * (4 if proxy_height >= 720 else 1)
            secs += d / 250 + d / 14          # shots, quality
        if m.get("audio"):
            mb += d * (192e3 + 32e3) / 1e6
            if engine != "none":
                secs += d / (20 if mac else 5)
            secs += d / 100
        if m["kind"] == "audio" or m.get("music"):
            secs += d / 100
        rows.append({"id": mid, "kind": m["kind"], "duration_s": d, "minutes": round(secs / 60, 2), "mb": round(mb, 1)})
    return rows, engine


def cmd_estimate(lab, only=None, as_json=False):
    ids = lab.media(only)
    rows, engine = estimate_rows(lab, ids)
    tot_min = round(sum(r["minutes"] for r in rows), 1)
    tot_mb = round(sum(r["mb"] for r in rows), 1)
    fg = free_gb(lab.root)
    res = {"media": rows, "minutes": tot_min, "mb": tot_mb, "free_gb": None if fg is None else round(fg, 1), "asr": engine}
    if as_json:
        print(json.dumps(res, indent=1))
        return
    for r in rows:
        print("%-24s %-5s %8.1f s  about %5.1f min  %7.1f MB" % (r["id"], r["kind"], r["duration_s"], r["minutes"], r["mb"]))
    print("TOTAL about %.1f min and %.0f MB of proxies and audio (speech to text: %s)%s" % (
        tot_min, tot_mb, engine, "" if fg is None else ", %.1f GB free" % fg))
    if fg is not None and tot_mb / 1024 > fg * 0.8:
        print("WARNING: that is close to the free disk space; free some space or ingest fewer files (--only)")


def script_and_beat_notes(lab, m):
    """Plain lines for status: the script alignment of a transcript and the tempo flags of a music file."""
    out = []
    fl = set((m.get("summary") or {}).get("flags") or [])
    if "script_aligned" in fl:
        tr = load_transcript(lab, m) or {}
        sc = tr.get("script") or {}
        out.append("script aligned (%s): match %.2f, %d replaced, %d extra, %d joined, %d missing%s" % (
            sc.get("source") or "?", float(sc.get("match") or 0), int(sc.get("replaced") or 0),
            int(sc.get("extra") or 0), int(sc.get("joined") or 0), len(sc.get("missing") or []),
            ", listen to %d" % len(sc["listen"]) if sc.get("listen") else ""))
    rel = (m.get("analysis") or {}).get("beats")
    if rel and fl & {"music", "bpm_name_mismatch", "loop_bars_off", "bpm_from_name"} and os.path.exists(lab.absp(rel)):
        bt = load_json(lab.absp(rel))
        bfl = bt.get("flags") or []
        if bfl:
            out.append("tempo %s BPM from %s (detected %s, name %s%s): %s; check bar 1 by ear with click %s" % (
                bt.get("bpm"), bt.get("bpm_from") or "detected", bt.get("detected_bpm"), bt.get("name_bpm"),
                (", %d bars" % bt["loop_bars"]) if bt.get("loop_bars") else "", ", ".join(bfl), m["id"]))
    if rel and "music" in fl and os.path.exists(lab.absp(rel)):
        qb = quiet_bars(load_json(lab.absp(rel)))
        if qb:
            out.append("quietest bars (dB under the median bar, from bar_db in the beats file): %s; a short piece "
                       "plays a full section, not these" % ", ".join(
                           "bar %d at %.2f s -%.1f dB" % (n_, t_, d_) for n_, t_, d_ in qb if t_ is not None))
    return out


def cmd_status(lab, as_json=False):
    rows = []
    for mid, m in lab.index["media"].items():
        have = [k for k, v in (m.get("analysis") or {}).items() if v and os.path.exists(lab.absp(v))]
        prox = bool(m.get("proxy") and os.path.exists(lab.absp(m["proxy"])))
        rows.append({"id": mid, "kind": m["kind"], "duration_s": m.get("duration_s"), "proxy": prox,
                     "analyses": have, "summary": m.get("summary"), "errors": m.get("errors", [])})
    sheets = lab.p("media", "sheets", "sheets.json")
    res = {"lab": posix(lab.root), "media": rows, "sheets": posix(sheets) if os.path.exists(sheets) else None,
           "shotlog": posix(lab.p("shotlog.json")) if os.path.exists(lab.p("shotlog.json")) else None}
    if as_json:
        print(json.dumps(res, indent=1))
        return
    print("LAB %s  media %d" % (posix(lab.root), len(rows)))
    for r in rows:
        s = r["summary"] or {}
        extra = []
        if s.get("shots") is not None:
            extra.append("%d shots" % s["shots"])
        if s.get("words"):
            extra.append("%d words" % s["words"])
        if s.get("lufs") is not None:
            extra.append("%.1f LUFS" % s["lufs"])
        if s.get("bpm"):
            extra.append("%.1f BPM" % s["bpm"])
        print("%-24s %-5s %8s  proxy %-3s  %-40s %s %s" % (
            r["id"], r["kind"], "%.2f s" % r["duration_s"] if r["duration_s"] else "-", "yes" if r["proxy"] else "no",
            ",".join(r["analyses"]) or "-", " ".join(extra), ("flags " + ",".join(s.get("flags") or [])) if s.get("flags") else ""))
        bn = bar_line_note(lab.index["media"][r["id"]])
        if bn:
            print("    note: %s" % bn)
        for sn in script_and_beat_notes(lab, lab.index["media"][r["id"]]):
            print("    note: %s" % sn)
        for e in r["errors"]:
            print("    error: %s" % e)
    print("sheets: %s" % (res["sheets"] or "none yet"))
    print("shot log: %s" % (res["shotlog"] or "none yet"))


# ---------------------------------------------------------------- proxies and audio
def proxy_dims(m, height):
    w, h = m.get("width") or 16, m.get("height") or 9
    try:
        sn, sd = [int(x) for x in (m.get("sar") or "1:1").split(":")]
        sar = sn / sd if sn and sd else 1.0
    except ValueError:
        sar = 1.0
    dw, dh = w * sar, h
    if (m.get("rotation") or 0) in (90, 270):
        dw, dh = dh, dw
    if dh > dw:     # portrait: 360 wide
        W = height
        H = int(round(height * dh / dw / 2)) * 2
    else:
        H = height
        W = int(round(height * dw / dh / 2)) * 2
    return max(2, W), max(2, H)


def make_proxy(lab, m, height=360):
    src = m["path"]
    out = lab.p("media", "proxies", m["id"] + ".mp4")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    W, H = proxy_dims(m, height)
    tmp = out[:-4] + ".tmp.mp4"
    run(ff_base() + ["-y", "-i", src, "-map", "0:v:0", "-vf", "scale=%d:%d:flags=bilinear,setsar=1,format=yuv420p" % (W, H),
                     *passthrough_args(), "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                     "-x264-params", "keyint=1:scenecut=0", "-an", tmp], timeout=max(600, 20 * (m.get("duration_s") or 60)))
    n = count_packets(tmp)
    os.replace(tmp, out)
    m["proxy"] = lab.rel(out)
    m["proxy_frames"] = n
    m["proxy_size"] = [W, H]
    if n != m.get("frames"):
        raise Fail("the proxy has %s frames but the source has %s (variable frame rate or a damaged file); frame "
                   "positions from this proxy may be off" % (n, m.get("frames")))


def std_rate(f):
    """The standard frame rate nearest to f, as an ffmpeg rate string."""
    rates = ["24000/1001", "24", "25", "30000/1001", "30", "48", "50", "60000/1001", "60", "100", "120"]
    return min(rates, key=lambda r: abs(float(Fraction(r)) - float(f)) / float(Fraction(r)))


def count_packets(path):
    p = run([ffprobe_bin(), "-v", "error", "-select_streams", "v:0", "-count_packets", "-show_entries",
             "stream=nb_read_packets", "-of", "csv=p=0", path], timeout=600)
    try:
        return int(p.stdout.strip().split("\n")[0].strip(","))
    except ValueError:
        return None


def audio_track1(m):
    """The audio Resolve plays for this clip: the first track of its media pool audio mapping.

    Measured on 21.1: a clip whose pool mapping has several tracks (a 4 channel camera file maps as 4 mono tracks,
    two stereo streams as 2 stereo tracks) is placed with its first track only, and a mono track plays at unity on
    both sides. The mapping from the Resolve dump wins; without a dump the lab uses Resolve's default mapping:
    a 1 channel stream is mono, a 2 channel stream stereo, a wider stream one mono track per channel, and every
    further stream its own track(s)."""
    a = m.get("audio") or {}
    tracks = ((m.get("resolve") or {}).get("audio_tracks") or [])
    if tracks and tracks[0].get("channels"):
        t = tracks[0]
        return {"channels": [int(c) for c in t["channels"]], "type": str(t.get("type") or "").lower(),
                "tracks": len(tracks), "from": "resolve"}
    streams = [int(c) for c in (a.get("streams") or [a.get("channels") or 0]) if int(c or 0) > 0] or [1]
    first = streams[0]
    ntr = sum(1 if c <= 2 else c for c in streams)
    if first == 2:
        return {"channels": [1, 2], "type": "stereo", "tracks": ntr, "from": "default"}
    return {"channels": [1], "type": "mono", "tracks": ntr, "from": "default"}


def audio_map_key(m):
    """The placed audio track's channels, type and track count (None without audio): what the lab's WAV is made
    from, whether the mapping came from a dump or Resolve's default."""
    if not m.get("audio"):
        return None
    t = audio_track1(m)
    return [t["channels"], t["type"], t["tracks"]]


def _audio_graph(m, tr):
    """ffmpeg input maps and filters that pick the Resolve track's channels: (maps, stereo48 filter, mono16 filter).
    Channel numbers count across all audio streams in order, as Resolve counts them."""
    streams = [int(c) for c in ((m.get("audio") or {}).get("streams") or [(m.get("audio") or {}).get("channels") or 2])]
    streams = [c for c in streams if c > 0] or [2]
    total = sum(streams)
    chans = [c for c in tr["channels"] if 0 < c <= total]
    if len(streams) > 1 and max(chans or [1]) > streams[0]:
        pre = "".join("[0:a:%d]" % i for i in range(len(streams))) + "amerge=inputs=%d," % len(streams)
    else:
        pre = "[0:a:0]"
        chans = [c for c in chans if c <= streams[0]]

    def c(k):
        return "c%d" % (k - 1)
    if tr["type"] == "mono" and chans:
        st, mo = "pan=stereo|c0=%s|c1=%s" % (c(chans[0]), c(chans[0])), "pan=mono|c0=%s" % c(chans[0])
    elif tr["type"] == "stereo" and len(chans) == 2:
        st = "pan=stereo|c0=%s|c1=%s" % (c(chans[0]), c(chans[1]))
        mo = "pan=mono|c0=0.5*%s+0.5*%s" % (c(chans[0]), c(chans[1]))
    else:
        # a surround or adaptive track: an approximate downmix of its channels
        chans = chans or [1]
        mix = "+".join("%g*%s" % (1.0 / len(chans), c(k)) for k in chans)
        st, mo = "pan=stereo|c0=%s|c1=%s" % (mix, mix), "pan=mono|c0=%s" % mix
    return pre, st, mo


def channel_levels(m):
    """RMS level (dBFS) of every audio channel, counted across streams; None when unreadable."""
    out = []
    for i, n in enumerate(((m.get("audio") or {}).get("streams") or [])):
        p = run([ffmpeg_bin(), "-nostdin", "-hide_banner", "-loglevel", "info", "-i", m["path"], "-map", "0:a:%d" % i, "-af",
                             "astats=measure_perchannel=RMS_level:measure_overall=none", "-f", "null", "-"],
                check=False, timeout=max(600, 5 * (m.get("duration_s") or 60)))
        vals = re.findall(r"RMS level dB:\s*(-?inf|-?[\d.]+)", p.stderr or "")
        for v in vals[:n]:
            out.append(-120.0 if "inf" in v else round(float(v), 1))
        out += [None] * (n - len(vals[:n]))
    return out


HINT_NEAR_S = 0.07             # a bar line this close to a found beat (or 15 % of a beat) is on the grid
HINT_SNAP_S = 0.06             # the onset at a bar line is looked for this far around it (at most 1/8 of a beat)
HINT_HALF_TOL = 0.08           # the grid moves by itself only when it sits within this share of a beat of half a beat off,
HINT_PEAK = 0.5                # a real onset at least this share of the median beat onset is at the bar line,
HINT_SUPPORT = 0.5             # and the moved grid lands on onsets at least this share of the found grid's
LR_WIN_S = 0.1                 # window of the left/right balance measure
LR_ACTIVE_DB = -50.0           # a window counts when its louder side is above this (dBFS)
LR_ONE_SIDED_DB = 12.0         # a window this much louder on one side is one-sided
LR_LEAD_DB = 6.0               # a window this much louder on one side is led by that side (two voices, one per side)
LR_LOUD_DB = 20.0              # loud windows: within this of the 95th percentile level (speech, not room tone)
LR_TWO_VOICES = 0.10           # each side leads at least this share of the loud windows: a different voice per side,
LR_TWO_LEVEL_DB = 10.0         # ... at levels this close (a camera microphone leads only in the pauses, at room level)
LR_COH = 0.5                   # a window led by one side with its channels this little alike: that side's own sound
LR_STRETCH_S = 1.5             # a programme one-sided this long in one stretch (pauses of 0.3 s allowed) is one-sided,
LR_PROG_SHARE = 0.20           # ... or over this share of its loud windows,
LR_PROG_LEVEL_DB = 12.0        # ... when those windows are within this of its loud level (not the room tone of pauses)


def lr_windows(chunks, sr):
    """Left and right levels (dBFS) per 100 ms window and the sums for their correlation over active windows, from
    an iterator of (n, 2) float arrays. Returns a dict (or None when there is no stereo sound)."""
    # the correlation is taken on 0.5 ms block means (below about 1 kHz): a camera's own stereo microphones sit a few
    # centimetres apart and stay coherent there even for diffuse room sound, two separate microphones do not
    blk = max(1, int(sr // 2000))
    win = blk * max(1, int(round(LR_WIN_S * sr / blk)))
    rest = np.zeros((0, 2))
    dl, dr, dc = [], [], []
    sll = srr = slr = 0.0
    for x in chunks:
        x = np.concatenate([rest, np.asarray(x, np.float64).reshape(-1, 2)])
        k = len(x) // win
        if k:
            b = x[:k * win].reshape(k, win, 2)
            e = (b ** 2).mean(1)
            l_, r_ = 10 * np.log10(e[:, 0] + 1e-12), 10 * np.log10(e[:, 1] + 1e-12)
            act = np.maximum(l_, r_) > LR_ACTIVE_DB
            coh = np.ones(k)
            if act.any():
                ba = b[act].reshape(int(act.sum()), win // blk, blk, 2).mean(2)
                ba = ba - ba.mean(1, keepdims=True)
                w0, w1 = (ba[..., 0] ** 2).sum(1), (ba[..., 1] ** 2).sum(1)
                w01 = (ba[..., 0] * ba[..., 1]).sum(1)
                sll += float(w0.sum())
                srr += float(w1.sum())
                slr += float(w01.sum())
                # per window: how alike the two channels are (a voice panned with a pan pot stays near 1)
                coh[act] = w01 / np.sqrt(np.maximum(w0 * w1, 1e-30))
            dl.append(l_)
            dr.append(r_)
            dc.append(coh)
        rest = x[k * win:]
    if not dl:
        return None
    dl, dr, dc = np.concatenate(dl), np.concatenate(dr), np.concatenate(dc)
    mx = np.maximum(dl, dr)
    act = mx > LR_ACTIVE_DB
    if act.sum() < 5:
        return None
    imb = (dl - dr)[act]
    corr = (slr / np.sqrt(sll * srr)) if sll > 1e-12 and srr > 1e-12 else 0.0
    # signed shares on the loud windows (speech and music, not the room tone of the pauses): one side leading most of
    # them is one-sided sound, each side leading a real share of them is a different voice per side
    loud_db = float(np.percentile(mx[act], 95))
    loud = act & (mx >= loud_db - LR_LOUD_DB)
    dv = dl - dr
    # one side's own sound: 12 dB louder, or 6 dB louder with the channels unlike (a one-sided voice over a centred
    # music bed); a voice panned off-centre with a pan pot is alike in both channels and never counts
    own_l = act & ((dv > LR_ONE_SIDED_DB) | ((dv > LR_LEAD_DB) & (dc < LR_COH)))
    own_r = act & ((dv < -LR_ONE_SIDED_DB) | ((dv < -LR_LEAD_DB) & (dc < LR_COH)))
    side = np.where(own_l, 1, np.where(own_r, -1, 0))
    # the longest stretch one side carries alone (gaps up to 0.3 s: the pauses between words)
    best, best_at, cur, first, last, gap = 0, None, 0, None, None, 0
    for i, v in enumerate(side.tolist() + [2]):
        if v == cur and v in (1, -1):
            last, gap = i, 0
            continue
        if v == 0 and cur and gap < 3:
            gap += 1
            continue
        if cur and first is not None and last - first + 1 > best:
            best, best_at = last - first + 1, (first, last + 1, cur)
        cur, first, last, gap = (v, i, i, 0) if v in (1, -1) else (0, None, None, 0)
    n_l = max(1, int(loud.sum()))
    out = {"active_s": round(float(act.sum()) * LR_WIN_S, 1), "imbalance_db": round(float(np.median(imb)), 1),
           "one_sided_share": round(float(np.mean(np.abs(imb) > LR_ONE_SIDED_DB)), 3), "correlation": round(float(corr), 3),
           "left_db": round(float(np.median(dl[act])), 1), "right_db": round(float(np.median(dr[act])), 1),
           "left_share": round(float((loud & (dv > LR_ONE_SIDED_DB)).sum()) / n_l, 3),
           "right_share": round(float((loud & (dv < -LR_ONE_SIDED_DB)).sum()) / n_l, 3),
           "lead_left_share": round(float((loud & (dv > LR_LEAD_DB)).sum()) / n_l, 3),
           "lead_right_share": round(float((loud & (dv < -LR_LEAD_DB)).sum()) / n_l, 3),
           "own_left_share": round(float((loud & own_l).sum()) / n_l, 3),
           "own_right_share": round(float((loud & own_r).sum()) / n_l, 3),
           "loud_db": round(loud_db, 1), "longest_one_sided_s": round(best * LR_WIN_S, 1),
           "loud_s": round(n_l * LR_WIN_S, 1)}
    for k, sel, lv in (("lead_left_db", loud & (dv > LR_LEAD_DB), dl), ("lead_right_db", loud & (dv < -LR_LEAD_DB), dr),
                       ("own_left_db", loud & own_l, dl), ("own_right_db", loud & own_r, dr)):
        if sel.any():
            out[k] = round(float(np.median(lv[sel])), 1)
    if best_at:
        out["longest_at_s"] = [round(best_at[0] * LR_WIN_S, 1), round(best_at[1] * LR_WIN_S, 1)]
        out["longest_side"] = "left" if best_at[2] > 0 else "right"
    return out


LR_SECOND_VOICE_S = 1.0         # the quieter side having the sound to itself this long is a second voice (source clips)
LR_SECOND_VOICE_DB = 6.0        # ... at a level this close to the other side's own sound (room tone on a camera
                                # microphone in the pauses sits far lower and stays 'split'; a clip still judged
                                # one_sided while its other channel has sound gets advice to listen before choosing)


def own_quiet_s(st):
    """Seconds of the loud windows in which the QUIETER side has the sound to itself (own share times the loud time;
    older stats without loud_s use active_s, which errs toward calling it a second voice, the safe side)."""
    if not st or "own_left_share" not in st:
        return 0.0
    tot = float(st.get("loud_s") or st.get("active_s") or 0.0)
    return min(float(st.get("own_left_share") or 0.0), float(st.get("own_right_share") or 0.0)) * tot


def lr_verdict(st, programme=False):
    """'two_voices' (each side leads a real share of the loud windows: a different speaker on each channel, as from
    a two-microphone kit), 'one_sided' (the sound is mostly in one channel; for a finished programme also a stretch
    of 1.5 s or 20 % of it), 'split' (two different sources left and right: little correlation although both carry
    sound, as a lav on one side and the camera microphone on the other) or None."""
    if not st:
        return None
    if "own_left_share" in st and min(st["own_left_share"], st["own_right_share"]) >= LR_TWO_VOICES and \
            abs(float(st.get("own_left_db", -200)) - float(st.get("own_right_db", 200))) <= LR_TWO_LEVEL_DB:
        return "two_voices"
    # source clips only: when the quieter side has sound of its own for a real time at a speech level near the other
    # side's (an interviewer's one question on a lav before a long answer), it is a second voice. Calling it one_sided
    # would advise picking one channel, which drops that speaker; two_voices advises Pan Spread, which keeps both
    if not programme and own_quiet_s(st) >= LR_SECOND_VOICE_S and "own_left_db" in st and "own_right_db" in st and \
            abs(float(st["own_left_db"]) - float(st["own_right_db"])) <= LR_SECOND_VOICE_DB:
        return "two_voices"
    dom = max(st.get("left_share", 0.0), st.get("right_share", 0.0)) if "left_share" in st else st["one_sided_share"]
    if abs(st["imbalance_db"]) >= LR_ONE_SIDED_DB or dom >= 0.5:
        return "one_sided"
    if programme and "own_left_share" in st:
        k = "left" if st["own_left_share"] >= st["own_right_share"] else "right"
        near = float(st.get("own_%s_db" % k, -200)) >= float(st.get("loud_db", 0)) - LR_PROG_LEVEL_DB
        long_ = float(st.get("longest_one_sided_s") or 0) >= LR_STRETCH_S and st.get("longest_side") == k
        if near and (st["own_%s_share" % k] >= LR_PROG_SHARE or long_):
            return "one_sided"
    if st["correlation"] < 0.3 and dom < 0.25:
        return "split"
    return None


def lr_side(st):
    """'left' or 'right': the side that carries a one-sided sound."""
    if "own_left_share" in st and abs(float(st.get("imbalance_db") or 0)) < LR_ONE_SIDED_DB and \
            st["own_left_share"] != st["own_right_share"]:
        return "left" if st["own_left_share"] > st["own_right_share"] else "right"
    if "left_share" in st and st["left_share"] != st["right_share"]:
        return "left" if st["left_share"] > st["right_share"] else "right"
    return "left" if float(st.get("imbalance_db") or 0) >= 0 else "right"


def wav_lr(path, chunk_s=10):
    """lr_windows of a 16-bit stereo WAV, read in chunks."""
    import wave
    with wave.open(path) as w:
        if w.getnchannels() != 2 or w.getsampwidth() != 2:
            return None
        sr = w.getframerate()

        def gen():
            while True:
                raw = w.readframes(int(chunk_s * sr))
                if not raw:
                    return
                yield np.frombuffer(raw, "<i2").astype(np.float64).reshape(-1, 2) / 32768.0
        return lr_windows(gen(), sr)


def stereo_check(lab, m):
    """Measure the clip's placed audio track (the lab WAV) for one-sided or split channels and set the flags."""
    tr = (m.get("audio") or {}).get("track1") or audio_track1(m)
    st = None
    if tr.get("type") == "stereo" and m.get("wav") and os.path.exists(lab.absp(m["wav"])):
        st = wav_lr(lab.absp(m["wav"]))
    m["audio"]["lr"] = st or {}
    v = lr_verdict(st)
    add_flag(m, "one_sided", v == "one_sided")
    split_flag(m)
    return v


def split_flag(m):
    """split_channels and two_voices only for sound with speech: wind and wide ambience are uncorrelated too, and a
    song or a sound file may be wide on purpose."""
    fl = set(m["summary"].get("flags") or [])
    v = lr_verdict((m.get("audio") or {}).get("lr"))
    talk = not m.get("music") and "speech" in fl
    on = v == "split" and m.get("kind") in ("av", "video") and talk
    add_flag(m, "split_channels", on)
    add_flag(m, "two_voices", v == "two_voices" and m.get("kind") in ("av", "video", "audio") and talk)
    return on


def make_audio(lab, m):
    if not m.get("audio"):
        return False
    out = lab.p("media", "proxies", m["id"] + ".wav")
    out16 = lab.p("media", "proxies", m["id"] + ".16k.wav")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    t = max(600, 5 * (m.get("duration_s") or 60))
    tr = audio_track1(m)
    pre, st, mo = _audio_graph(m, tr)
    run(ff_base() + ["-y", "-i", m["path"], "-filter_complex", pre + st + "[a]", "-map", "[a]", "-ar", "48000",
                     "-c:a", "pcm_s16le", out[:-4] + ".tmp.wav"], timeout=t)
    os.replace(out[:-4] + ".tmp.wav", out)
    run(ff_base() + ["-y", "-i", m["path"], "-filter_complex", pre + mo + "[a]", "-map", "[a]", "-ar", "16000",
                     "-c:a", "pcm_s16le", out16[:-4] + ".tmp.wav"], timeout=t)
    os.replace(out16[:-4] + ".tmp.wav", out16)
    m["wav"], m["wav16k"] = lab.rel(out), lab.rel(out16)
    m["audio"]["track1"] = tr
    multi = tr["tracks"] > 1 or tr["type"] not in ("mono", "stereo")
    add_flag(m, "multi_track_audio", multi)
    m["audio"].pop("channel_db", None)
    if multi:
        m["audio"]["channel_db"] = channel_levels(m)
    stereo_check(lab, m)
    return True


def audio_note(m):
    """Plain notes for clips whose placed sound needs the user's attention: several audio tracks in Resolve, or a
    stereo track with the sound in one channel (one_sided), a different speaker on each channel (two_voices) or two
    different sources left and right (split_channels). [] for ordinary clips."""
    a = m.get("audio") or {}
    tr = a.get("track1")
    flags = set(m["summary"].get("flags") or [])
    out = []
    if not tr:
        return out
    redo = "run dump-script again, add --from-dump and ingest again"
    if "multi_track_audio" in flags:
        what = ("channel %d (mono)" % tr["channels"][0]) if tr["type"] == "mono" else (
            "channels %s (%s)" % (" and ".join(str(c) for c in tr["channels"]), tr["type"]))
        msg = ("%s has %d audio tracks in Resolve; Resolve places only its first track, so the preview and the build "
               "use %s" % (m["id"], tr["tracks"], what))
        lv = a.get("channel_db") or []
        used = [lv[c - 1] for c in tr["channels"] if 0 < c <= len(lv) and lv[c - 1] is not None]
        loud = [k + 1 for k, v in enumerate(lv) if v is not None and v > -45 and k + 1 not in tr["channels"]]
        if used and max(used) < -55 and loud:
            msg += ("; that is nearly silent while channel %s has sound. In Resolve set Clip Attributes > Audio for "
                    "this clip to the channels you want as one mono or stereo track, %s" % (
                        " and ".join(str(k) for k in loud), redo))
        else:
            msg += "; to use other channels, set Clip Attributes > Audio in Resolve, " + redo
        out.append(msg)
    st = a.get("lr") or {}
    ch = list(tr.get("channels") or [1, 2])
    fix = ("In Resolve select the clip in the Media Pool, open Clip Attributes > Audio, set the track's Format to Mono "
           "and its Source Channel to Embedded Channel %d; then " + redo)
    if "one_sided" in flags:
        left = lr_side(st) == "left"
        k = ch[0] if left or len(ch) < 2 else ch[1]
        lv = (float(st.get("left_db") or 0), float(st.get("right_db") or 0))
        other = ("is silent" if min(lv) <= -90 else "is %.0f dB quieter" % max(abs(float(st.get("imbalance_db") or 0)),
                                                                         abs(lv[0] - lv[1])))
        what = "the voice" if "speech" in flags else "the sound"
        msg = ("%s plays its sound in one ear only: the %s channel (channel %d of the file) carries it and the "
               "other %s, so a phone or headphones play %s on one side. " + fix) % (
            m["id"], "left" if left else "right", k, other, what, k)
        if min(lv) > -90:
            msg += (". Listen to the other channel first: it is not silent, and if it carries a second voice do not pick a "
                    "channel (that drops the speaker); use Pan Spread 1 (PNT) on its own track as for two voices instead")
        out.append(msg)
    elif "two_voices" in flags:
        out.append(("%s has a different voice on each channel (the left channel leads %d %% of the speech, the right "
                    "%d %%): usually two microphones, one per speaker, so the viewer hears one speaker in each ear. Do "
                    "not set it to one channel: that drops a speaker. This version cannot put both in the centre: after "
                    "the build, put this clip's items on an audio track of their own and in the Fairlight mixer open "
                    "that track's Pan window and set Spread to 1 (PNT), which plays both channels from the centre; "
                    "listen, then render") % (m["id"], round(100 * float(st.get("lead_left_share") or 0)),
                                              round(100 * float(st.get("lead_right_share") or 0))))
    elif "split_channels" in flags:
        out.append(("%s carries two different sources on its left and right channels (correlation %.2f: usually a lav "
                    "on one side and the camera microphone on the other), so the viewer hears one in each ear. Listen "
                    "to both and keep the better voice: " + fix.replace("Embedded Channel %d", "Embedded Channel %d or %d"))
                   % (m["id"], float(st.get("correlation") or 0), ch[0], ch[-1]))
    return out


def ensure_audio(lab, m):
    if not m.get("audio"):
        return False
    if m.get("wav") and m.get("wav16k") and os.path.exists(lab.absp(m["wav"])) and os.path.exists(lab.absp(m["wav16k"])):
        return True
    return make_audio(lab, m)


def frame_source(lab, m):
    """The proxy when it exists (fast), else the original file."""
    if m.get("proxy") and os.path.exists(lab.absp(m["proxy"])):
        return lab.absp(m["proxy"])
    return m["path"]


def step_proxies(lab, m, opt):
    if m["kind"] == "image":
        return "skip"
    did = []
    if m["kind"] in ("av", "video"):
        make_proxy(lab, m, opt.get("proxy_height", 360))
        did.append("proxy")
    if m.get("audio"):
        make_audio(lab, m)
        did.append("audio")
    return "ok"


# ---------------------------------------------------------------- shots
def adaptive_peaks(s, fps, ratio=3.0, win=2, min_val=8.0, min_len_s=MIN_SHOT_S):
    """Cut at i when s[i] (change from frame i-1 to i) is `ratio` times the mean of its `win` neighbours on each
    side (i excluded) and at least min_val; shots stay at least min_len_s long (the file start counts as a cut)."""
    n = len(s)
    if n < 3:
        return []
    s = np.asarray(s, dtype=float)
    sn = s.copy()
    sn[0] = np.nan                  # s[0] is a placeholder (no previous frame), never a neighbour
    pad = np.concatenate([np.full(win, np.nan), sn, np.full(win, np.nan)])
    nb = np.stack([pad[win + k: win + k + n] for k in range(-win, win + 1) if k != 0])
    with np.errstate(invalid="ignore"):
        cnt = np.sum(~np.isnan(nb), axis=0)
        mean = np.where(cnt > 0, np.nansum(nb, axis=0) / np.maximum(cnt, 1), 0.0)
    cand = np.where((s >= min_val) & (s >= ratio * (mean + 1e-3)))[0]
    min_len = max(1, int(round(min_len_s * float(fps))))
    cuts, last = [], 0
    for i in cand:
        if i < 1:
            continue
        if i - last >= min_len:
            cuts.append(int(i))
            last = int(i)
    return cuts


def shot_scores(path):
    """Per frame: s[i] = mean |f[i] - f[i-1]| (64x36 luma), back[i] = mean |f[i+3] - f[i-1]|, luma mean."""
    s, back, luma = [], [], []
    ring = deque(maxlen=4)
    n = 0
    for _, arr in decode_frames(path, 64, 36, "gray", batch=512):
        f16 = arr.astype(np.int16)
        for f in f16:
            s.append(float(np.abs(f - ring[-1]).mean()) if ring else 0.0)
            luma.append(float(f.mean()))
            if len(ring) == 4:      # ring holds frames n-4..n-1 and f is frame n: back[i] for i = n-3 is |f[i+3] - f[i-1]|
                back.append(float(np.abs(f - ring[0]).mean()))
            ring.append(f)
            n += 1
    s = np.array(s)
    b = np.full(n, np.nan)
    if back:
        b[1:1 + len(back)] = back
    return s, b, np.array(luma)


def detect_shots(path, fps, frames=None, look=3):
    s, back, luma = shot_scores(path)
    n = len(s)
    fps = float(parse_fps(fps))
    cand = adaptive_peaks(s, fps)
    keep, rejected = [], []
    for i in cand:
        if i + look >= n or i < 2 or np.isnan(back[i]):
            keep.append(i)
            continue
        jump = s[i]
        run_ = int((s[max(1, i - 3): i + 4] > 0.5 * jump).sum())
        if back[i] < 0.5 * jump:
            rejected.append({"frame": int(i), "why": "flash"})
            continue
        if run_ >= 4:
            rejected.append({"frame": int(i), "why": "whip"})
            continue
        keep.append(i)
    min_len = max(1, int(round(MIN_SHOT_S * fps)))
    total = frames if frames else n
    final = []
    for c in keep:
        if total - c < min_len:
            rejected.append({"frame": int(c), "why": "too_close_to_end"})
            continue
        final.append(c)
    return final, rejected, n


def scdet_frames(path, fps, first_pts=0.0):
    p = subprocess.run([ffmpeg_bin(), "-nostdin", "-hide_banner", "-i", path, "-map", "0:v:0", "-vf",
                        "scdet=threshold=5:sc_pass=0", "-an", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    ts = [float(x) for x in re.findall(r"lavfi\.scd\.time:\s*([\d.]+)", p.stderr or "")]
    return [int(round((t - first_pts) * float(fps))) for t in ts]


def step_shots(lab, m, opt):
    if m["kind"] not in ("av", "video"):
        return "skip"
    src = frame_source(lab, m)
    fps = parse_fps(m["fps"])
    cuts, rejected, n = detect_shots(src, fps, m.get("frames"))
    frames = m.get("frames") or n
    if n != frames:
        set_error(m, "shots", "decoded %d frames but the index says %d; shot positions after the difference may be off"
                  % (n, frames))
    cuts = [c for c in cuts if 0 < c < frames]
    bounds = [0] + cuts + [frames]
    width = 3 if len(bounds) - 1 > 99 else 2
    shots = []
    for k in range(len(bounds) - 1):
        shots.append({"id": "%s.s%0*d" % (m["id"], width, k + 1), "in": int(bounds[k]), "out": int(bounds[k + 1]),
                      "start": "file" if k == 0 else "cut"})
    try:
        sc = scdet_frames(src, fps, _first_pts(src))
    except Exception:
        sc = []
    out = lab.p("media", "analysis", m["id"] + ".shots.json")
    write_json(out, {"schema": "resolve-editor/shots@1", "media": m["id"], "fps": m["fps"], "frames": frames,
                     "method": "luma-mad-64x36 ratio3 floor8 + flash/whip check", "shots": shots,
                     "rejected": rejected, "gradual": [], "scdet_check": sc})
    m["analysis"]["shots"] = lab.rel(out)
    m["summary"]["shots"] = len(shots)
    return "ok"


def _first_pts(path):
    p = run([ffprobe_bin(), "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=start_time", "-of",
             "csv=p=0", path], check=False, timeout=60)
    try:
        return float((p.stdout or "0").strip().split("\n")[0] or 0)
    except ValueError:
        return 0.0


def load_shots(lab, m):
    rel = (m.get("analysis") or {}).get("shots")
    if not rel or not os.path.exists(lab.absp(rel)):
        return None
    return load_json(lab.absp(rel))


# ---------------------------------------------------------------- loudness
def ebur128(path, timeout=1800):
    """ffmpeg's EBU R128 meter: integrated I, LRA, true peak TP (dBTP), max momentary and short-term loudness."""
    p = subprocess.run([ffmpeg_bin(), "-nostdin", "-hide_banner", "-nostats", "-i", path, "-map", "0:a:0", "-af",
                        "ebur128=peak=true:framelog=info", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    log = p.stderr or ""
    if "Summary:" not in log:
        raise Fail("the loudness meter gave no result (%s)" % log.strip()[-200:])
    Ms, Ss = [], []
    for mm in re.finditer(r"\bM:\s*(-?[\d.]+|-inf|nan)\s+S:\s*(-?[\d.]+|-inf|nan)", log):
        try:
            Ms.append(float(mm.group(1)))
            Ss.append(float(mm.group(2)))
        except ValueError:
            pass
    summ = log[log.rfind("Summary:"):]

    def grab(label, block):
        mm = re.search(label + r"\s*(-?[\d.]+|-inf)", block)
        if not mm:
            return None
        try:
            v = float(mm.group(1))
        except ValueError:
            return None
        return None if math.isinf(v) or math.isnan(v) else v
    tp_block = summ[summ.find("True peak:"):] if "True peak:" in summ else ""
    Mv = [x for x in Ms if x > -120 and not math.isnan(x)]
    Sv = [x for x in Ss if x > -120 and not math.isnan(x)]
    I = grab(r"I:", summ)
    if I is not None and I <= -70:
        I = None if I < -70.5 else I
    return {"I": I, "TP": grab(r"Peak:", tp_block) if tp_block else None, "LRA": grab(r"LRA:", summ),
            "M_max": r3(max(Mv)) if Mv else None, "S_max": r3(max(Sv)) if Sv else None}


def step_loudness(lab, m, opt):
    if not m.get("audio"):
        return "skip"
    ensure_audio(lab, m)
    r = ebur128(lab.absp(m["wav"]))
    out = lab.p("media", "analysis", m["id"] + ".loudness.json")
    write_json(out, {"schema": "resolve-editor/loudness@1", "media": m["id"], "I": r["I"], "TP": r["TP"],
                     "LRA": r["LRA"], "M_max": r["M_max"], "S_max": r["S_max"]})
    m["analysis"]["loudness"] = lab.rel(out)
    m["summary"]["lufs"] = r["I"]
    add_flag(m, "silent", r["I"] is None or r["I"] < -60)
    if "silent" in (m["summary"].get("flags") or []):
        # words whisper found in a nearly silent clip (distant chatter under B-roll) are guesses
        tr = load_transcript(lab, m)
        if tr and tr.get("words") and mark_low_conf(tr["words"]):
            write_json(lab.absp(m["analysis"]["words"]), tr)
    return "ok"


def mark_low_conf(words):
    """Tag every word low_conf (a clip too quiet, or a language guess too unsure, to trust its words). True when
    something changed."""
    changed = False
    for w in words:
        tg = w.setdefault("tags", [])
        if "low_conf" not in tg:
            tg.append("low_conf")
            changed = True
    return changed


# ---------------------------------------------------------------- speech: energy, voiced spans, whisper, snap
DIGITAL_ZERO_DB = -99.0          # band_db of digital silence is 10 * log10(1e-10) = -100 dB


def band_db(a, sr=16000, hop=160, win=512, lo=250, hi=4000, chunk=4096):
    """Speech band (250 to 4000 Hz) energy in dB at 10 ms hops; computed in chunks so long files stay small."""
    a = np.asarray(a).reshape(-1)
    scale = np.float32(1 / 32768.0) if a.dtype == np.int16 else np.float32(1.0)
    n = (len(a) - win) // hop
    if n <= 0:
        return np.zeros(0), np.zeros(0)
    wnd = np.hanning(win).astype(np.float32) * scale
    f = np.fft.rfftfreq(win, 1 / sr)
    sel = (f >= lo) & (f <= hi)
    out = np.empty(n, np.float64)
    base = np.arange(win)[None, :]
    for s0 in range(0, n, chunk):
        s1 = min(n, s0 + chunk)
        idx = base + hop * np.arange(s0, s1)[:, None]
        spec = np.abs(np.fft.rfft(a[idx].astype(np.float32) * wnd, axis=1)) ** 2
        out[s0:s1] = spec[:, sel].sum(1)
    t = (np.arange(n) * hop + win / 2) / sr
    return t, 10 * np.log10(out + 1e-10)


def voiced_spans(t, db, rel_db=10.0, abs_floor=-60.0, min_gap=0.1, min_len=0.05):
    """Voiced spans from the speech band energy: threshold max(-60 dB, 10th percentile + 10 dB).

    The percentile is taken over frames that hold any signal: digital zero (padded voice-over, text to speech, a
    muted stretch) sits at the -100 dB epsilon of band_db, and when 10 % or more of the clip is digital zero it
    would pin the floor there, drop the threshold to -60 dB and count quiet room tone between words as voiced."""
    if len(db) == 0:
        return [], abs_floor, abs_floor
    live = db[db > DIGITAL_ZERO_DB]
    floor = float(np.percentile(live, 10)) if len(live) >= max(10, 0.02 * len(db)) else float(np.percentile(db, 10))
    thr = max(abs_floor, floor + rel_db)
    on = db > thr
    spans = []
    i, n = 0, len(db)
    edges = np.flatnonzero(np.diff(np.r_[0, on.astype(np.int8), 0]))
    for a, b in zip(edges[0::2], edges[1::2]):
        spans.append([float(t[a] - 0.005), float(t[b - 1] + 0.005)])
    out = []
    for s in spans:
        if out and s[0] - out[-1][1] < min_gap:
            out[-1][1] = s[1]
        else:
            out.append(s)
    return [s for s in out if s[1] - s[0] >= min_len], thr, floor


def normw(w):
    return re.sub(r"[^a-z0-9']", "", w.lower().replace("’", "'")).strip("'")


# a symbol a script writes, as it is said: "20%" is read "20 percent", "$49" "49 dollars", "@studio" "at studio"
CURRENCY_WORDS = {"$": "dollars", "€": "euros", "£": "pounds", "₹": "rupees", "¥": "yen"}
SYMBOL_WORDS = (("%", "percent"), ("+", "plus"), ("°", "degrees"), ("&", "and"), ("@", "at"), ("#", "hashtag"),
                ("=", "equals")) + tuple(CURRENCY_WORDS.items())
SYMBOL_CHARS = "".join(s for s, _ in SYMBOL_WORDS)
DASHES = "-\u2010\u2011\u2012\u2013\u2014"     # a hyphen and the dashes
UNIT_ALIASES = {"rs": "rupee", "inr": "rupee", "usd": "dollar"}    # a unit written as its short name


def norm_cmp(w):
    """normw for comparing a script with a transcript: every symbol is spelled the way it is said, in the order it is
    said ("20%" is "20percent", "$49" is "49dollars", "@studio" is "atstudio", "#1" is "number1"), so a spoken unit is
    never a word the script lacks. Punctuation alone ("...", a dash) is empty."""
    s = str(w).lower().replace("’", "'")
    s = re.sub(r"([$€£₹¥])\s*(\d[\d.,]*)", lambda m: m.group(2) + " " + CURRENCY_WORDS[m.group(1)], s)
    s = re.sub(r"#(?=\d)", "number ", s)
    s = re.sub(r"°\s*c\b", " degrees celsius ", s)
    s = re.sub(r"°\s*f\b", " degrees fahrenheit ", s)
    s = re.sub(r"(?<=\d)k\b", " thousand ", s)                     # "10k" is said "10 thousand"
    s = re.sub(r"(?<=\d)x\b", " times ", s)                        # "3x" is said "3 times"
    s = re.sub(r"(?<=[a-z0-9]{2})\.(?=(?:com|net|org|io|co|in|ai|app|shop|store|studio|me|tv|uk)\b)", " dot ", s)
    for sym, word in SYMBOL_WORDS:
        if sym in s:
            s = s.replace(sym, " %s " % word)
    # "1 dollar" and "$1" (said "one dollar") are the same words: a currency word compares without its plural
    s = re.sub(r"\b(dollar|euro|pound|rupee|buck)s\b", r"\1", s)
    s = re.sub(r"[^a-z0-9']", "", s).strip("'")
    return UNIT_ALIASES.get(s, s)


def whisper_words(js):
    """Words from whisper-cli -ml 1 -sow -ojf output (one word per segment). Times in seconds of the WAV."""
    ws = []
    depth, star = 0, False   # inside a non-speech note such as "(gentle music)", "[BLANK_AUDIO]" or "*laughs*"
    for seg in js.get("transcription", []):
        txt = (seg.get("text") or "").strip()
        if star:
            star = not txt.endswith("*")
            continue
        if txt.startswith("*"):
            star = not (len(txt) > 1 and txt.endswith("*"))
            continue
        if depth > 0 or txt.startswith(("(", "[")):
            depth = max(0, depth + txt.count("(") + txt.count("[") - txt.count(")") - txt.count("]"))
            continue
        if not txt or not re.search(r"\w", txt) or set(txt) <= set("♪♫♬#-~. "):
            if txt and ws and not re.search(r"[♪♫♬]", txt):
                ws[-1]["w"] += txt
            continue
        toks = [k for k in seg.get("tokens", []) if not (k.get("text") or "").strip().startswith("[")]
        s, e = seg["offsets"]["from"] / 1000.0, seg["offsets"]["to"] / 1000.0
        ps = [k.get("p") for k in toks if k.get("p") is not None]
        p = float(np.mean(ps)) if ps else None
        if not normw(txt):
            if ws:
                ws[-1]["w"] += txt
            continue
        d = [k["t_dtw"] / 100.0 for k in toks if (k.get("t_dtw", -1) or -1) >= 0 and normw(k.get("text") or "")]
        parts = txt.split()
        if len(parts) == 1:
            ws.append({"w": txt, "s": s, "e": max(e, s), "p": p, "dtw": d[0] if d else None})
        else:       # rare: two words in one segment, share the time by characters
            tot = sum(len(x) for x in parts) or 1
            c = s
            for x in parts:
                dd = (e - s) * len(x) / tot
                ws.append({"w": x, "s": c, "e": c + dd, "p": p, "dtw": None})
                c += dd
    return [w for w in ws if normw(w["w"]) or w["w"]]


DTW_LEAD = 0.2     # whisper.cpp DTW token times land about 0.1 to 0.3 s after the spoken onset (measured)


def snap_words(ws, spans):
    """Whisper decides WHERE the words and pauses are, the speech band envelope decides the exact EDGES.

    Each word goes to a voiced span: the span holding its DTW anchor (t_dtw - DTW_LEAD) when DTW times exist,
    else the span it overlaps most (else the nearest). The assignment stays monotonic. Then the first word of every
    span starts on the span start and the last word ends on the span end; inner words start at their DTW anchor
    (or whisper's start) clamped inside the span, and end where the next word starts."""
    if not spans or not ws:
        return [dict(w, snapped="") for w in ws]
    starts = np.array([s[0] for s in spans])
    ends = np.array([s[1] for s in spans])

    def nearest(t):
        return int(np.argmin(np.maximum(0, np.maximum(starts - t, t - ends))))
    assign = []
    for w in ws:
        if w.get("dtw") is not None:
            k = nearest(w["dtw"] - DTW_LEAD)
        else:
            ov = np.maximum(0, np.minimum(w["e"], ends) - np.maximum(w["s"], starts))
            k = int(np.argmax(ov)) if ov.max() > 0 else nearest((w["s"] + w["e"]) / 2)
        assign.append(k)
    for i in range(1, len(assign)):
        assign[i] = max(assign[i], assign[i - 1])
    out = [dict(w, snapped="") for w in ws]
    i = 0
    while i < len(out):
        j = i
        while j + 1 < len(out) and assign[j + 1] == assign[i]:
            j += 1
        s0, s1 = spans[assign[i]]
        grp = list(range(i, j + 1))
        n = len(grp)
        prev = s0
        for q, k in enumerate(grp):
            w = out[k]
            if q == 0:
                st = s0
            else:
                est = (w["dtw"] - DTW_LEAD) if w.get("dtw") is not None else w["s"]
                st = min(max(est, prev + 0.03), s1 - 0.03 * (n - q))
                st = max(st, prev)
            w["s"] = st
            prev = st
        for q, k in enumerate(grp):
            out[k]["e"] = out[grp[q + 1]]["s"] if q + 1 < n else s1
        out[grp[0]]["snapped"] = "both" if n == 1 else "start"
        if n > 1:
            out[grp[-1]]["snapped"] = "end"
        i = j + 1
    return out


GAP_MIN_S = 0.03          # a silence this long inside a voiced span is a measured edge (3 frames of 10 ms)
GAP_EARLY_S = 0.15        # after a filler, the next word may start this much before its estimated start
GAP_LATE_S = 0.02         # and never more than this after it (an edge only moves earlier)


def micro_gaps(t, db, thr):
    """Silences under the voiced threshold that last GAP_MIN_S or more, in file seconds: [(start, end)]. They include
    the short ones voiced_spans merges away (min_gap 0.1 s)."""
    off_ = np.r_[0, (np.asarray(db) <= thr).astype(np.int8), 0]
    ed = np.flatnonzero(np.diff(off_))
    out = []
    for a, b in zip(ed[0::2], ed[1::2]):
        if b - a >= max(1, int(round(GAP_MIN_S / 0.01))):
            out.append((float(t[a] - 0.005), float(t[b - 1] + 0.005)))
    return out


def refine_filler_edges(words, gaps, off=0.0):
    """Whisper's DTW start of the word after a filler (um, uh, a repeat) can land up to 0.1 s late inside one voiced
    span, so dropping the filler would clip the kept word's onset. Where a measured silence (micro_gaps) ends up to
    GAP_EARLY_S before that estimate (and at most GAP_LATE_S after it), the filler ends at the silence's start and
    the next word starts at its end. Fillers end on a vowel or a nasal, so the silence is between the words, never a
    stop inside the kept word. Words with a hand-set edge (edge) are left alone. Returns the indices moved."""
    moved = []
    for i in range(1, len(words)):
        a, b = words[i - 1], words[i]
        if not ({"filler", "repeat"} & set(a.get("tags") or [])) or b.get("edge") == "hand" or \
                a.get("edge_end") == "hand":
            continue
        est = float(b["t0"]) - off
        if float(a["t1"]) < float(b["t0"]) - 0.005:
            continue                                    # a pause already separates them (span edges are measured)
        lo = float(a["t0"]) - off + 0.05
        cand = [g for g in gaps if est - GAP_EARLY_S <= g[1] <= est + GAP_LATE_S and g[0] >= lo]
        if not cand:
            continue
        g0, g1 = max(cand, key=lambda g: g[1])
        if g1 >= est - 0.005 and g0 >= float(a["t1"]) - off - 0.005:
            continue
        a["t1"] = round(min(float(a["t1"]), g0 + off), 3)
        b["t0"] = round(min(g1 + off, float(b["t1"]) - 0.03), 3)
        b["edge"] = "gap"
        moved.append(i)
    return moved


def apply_hand_edges(words, edges):
    """Re-apply the edges set with the edge command: {index: seconds} (the word before ends, this word starts)."""
    for k, sec in sorted((edges or {}).items(), key=lambda kv: int(kv[0])):
        i = int(k)
        if 0 < i < len(words):
            words[i - 1]["t1"] = round(float(sec), 3)
            words[i]["t0"] = round(float(sec), 3)
            words[i]["edge"] = "hand"


def refine_words_from_wav(lab, m, words):
    """refine_filler_edges on the clip's 16 kHz wav (the thresholds of transcribe). [] when there is no wav."""
    p = m.get("wav16k")
    if not p or not os.path.exists(lab.absp(p)):
        return []
    t, db = band_db(read_wav_i16(lab.absp(p)))
    _, thr, _ = voiced_spans(t, db)
    return refine_filler_edges(words, micro_gaps(t, db, thr), float((m.get("audio") or {}).get("offset_s") or 0.0))


def tag_words(words):
    for i, w in enumerate(words):
        n = normw(w["w"])
        tags = []
        if n in FILLERS:
            tags.append("filler")
        if i > 0 and n and n == normw(words[i - 1]["w"]):
            tags.append("repeat")
        if n in FLAG_WORDS:
            tags.append("flag")
        for a, b in FLAG_PHRASES:
            if (n == a and i + 1 < len(words) and normw(words[i + 1]["w"]) == b) or \
               (n == b and i > 0 and normw(words[i - 1]["w"]) == a):
                if "flag" not in tags:
                    tags.append("flag")
        if w.get("p") is not None and w["p"] < 0.4:
            tags.append("low_conf")
        w["tags"] = tags


PHANTOM_MIN_S = 0.02           # a word this short (or shorter) has no sound of its own


def mark_phantom(words, spans=None, off=0.0, min_voiced_s=0.03):
    """Tag "phantom" (and low_conf) on words the recogniser invented: a word with no length, or, when the voiced spans
    of the speech band are known, a word that overlaps none of them (a guess over near silence or a music bed).
    Returns how many words were tagged."""
    n = 0
    for w in words:
        a, b = float(w["t0"]) - off, float(w["t1"]) - off
        tg = w.setdefault("tags", [])
        why = None
        if b - a <= PHANTOM_MIN_S + 1e-9:
            why = "zero"
        elif spans is not None:
            voiced = sum(max(0.0, min(b, s1) - max(a, s0)) for s0, s1 in spans)
            if voiced < min(min_voiced_s, 0.25 * (b - a)):
                why = "silent"
        if why and "phantom" not in tg:
            tg.append("phantom")
            if "low_conf" not in tg:
                tg.append("low_conf")
            n += 1
    return n


def is_joined(words, i):
    """True when word i is a later part of the word before it (M script: whisper wrote one spoken word as two)."""
    return 0 < i < len(words) and "joined" in (words[i].get("tags") or [])


LIST_NUM_WORD = re.compile(r"\(?\d{1,3}[.)]")   # a list item's number as a word ("1.", "2)"): never ends a sentence


def build_sentences(words, pauses_after):
    """Sentences end on end punctuation, a long pause or the end of a script's list item (line_end). A word the
    transcript wrote as several parts (joined) is one
    word here: no sentence ends before its last part, and whether it ends one is its first part's (script) text."""
    sents, cur = [], []
    for i, w in enumerate(words):
        if cur and w.get("speaker") != words[cur[-1]].get("speaker") and not is_joined(words, i):
            sents.append(cur)
            cur = []
        cur.append(i)
        if is_joined(words, i + 1):
            continue
        h = i
        while is_joined(words, h):
            h -= 1
        end = (re.search(r"[.?!][\"')\]]*$", words[h]["w"]) is not None and not LIST_NUM_WORD.fullmatch(words[h]["w"]))\
            or "line_end" in (words[i].get("tags") or [])
        if end or pauses_after.get(i, 0.0) >= SENTENCE_PAUSE_S:
            sents.append(cur)
            cur = []
    if cur:
        sents.append(cur)
    out = []
    for s in sents:
        spk = [words[i].get("speaker") for i in s]
        out.append({"i0": s[0], "i1": s[-1], "t0": words[s[0]]["t0"], "t1": words[s[-1]]["t1"],
                    "speaker": max(set(spk), key=spk.count),
                    "text": " ".join(words[i]["w"] for i in s if not is_joined(words, i))})
    return out


# numpy speaker clustering (MFCC mean and spread per sentence, agglomerative on cosine distance)
def _mel_fb(sr=16000, n_fft=512, n_mels=40, fmin=60, fmax=7600):
    hz2mel = lambda f: 2595 * np.log10(1 + f / 700)  # noqa: E731
    mel2hz = lambda m: 700 * (10 ** (m / 2595) - 1)  # noqa: E731
    pts = mel2hz(np.linspace(hz2mel(fmin), hz2mel(fmax), n_mels + 2))
    bins = np.floor((n_fft + 1) * pts / sr).astype(int)
    fb = np.zeros((n_mels, n_fft // 2 + 1))
    for m in range(1, n_mels + 1):
        l, c, r = bins[m - 1], bins[m], bins[m + 1]
        fb[m - 1, l:c] = (np.arange(l, c) - l) / max(1, c - l)
        fb[m - 1, c:r] = (r - np.arange(c, r)) / max(1, r - c)
    return fb


def mfcc_stats(a, sr=16000, hop=160, win=400):
    n = (len(a) - win) // hop
    if n < 5:
        return None
    idx = np.arange(win)[None, :] + hop * np.arange(n)[:, None]
    p = np.abs(np.fft.rfft(a[idx] * np.hamming(win)[None, :], 512, axis=1)) ** 2
    e = p.sum(1)
    keep = e > np.percentile(e, 30)
    mel = np.log(p[keep] @ _mel_fb().T + 1e-10)
    dct = np.cos(np.pi / 40 * (np.arange(40)[None, :] + 0.5) * np.arange(20)[:, None])
    c = (mel @ dct.T)[:, 1:]
    return np.concatenate([c.mean(0), c.std(0)])


def cluster_k(X, k):
    X = (X - X.mean(0)) / (X.std(0) + 1e-9)
    X = X / (np.linalg.norm(X, axis=1, keepdims=True) + 1e-9)
    S = X @ X.T
    groups = [[i] for i in range(len(X))]
    while len(groups) > k:
        best = None
        for i in range(len(groups)):
            for j in range(i + 1, len(groups)):
                d = 1 - float(S[np.ix_(groups[i], groups[j])].mean())
                if best is None or d < best[0]:
                    best = (d, i, j)
        _, i, j = best
        groups[i] = groups[i] + groups[j]
        del groups[j]
    lab = np.zeros(len(X), int)
    for gi, g in enumerate(groups):
        lab[g] = gi
    return lab


def diarize(words, sents, a16, k, offset):
    """Label sentences (and their words) S1..Sk by voice; labels ordered by first appearance."""
    feats, ok = [], []
    for si, s in enumerate(sents):
        seg = a16[int(max(0, s["t0"] - offset) * 16000): int(max(0, s["t1"] - offset) * 16000)]
        f = mfcc_stats(seg.astype(np.float32) / 32768.0)
        if f is not None:
            feats.append(f)
            ok.append(si)
    if len(feats) < 2:
        return False
    lab = cluster_k(np.stack(feats), min(k, len(feats)))
    order, names = [], {}
    for l in lab:
        if l not in names:
            names[l] = "S%d" % (len(names) + 1)
    for si, l in zip(ok, lab):
        sents[si]["speaker"] = names[l]
        for i in range(sents[si]["i0"], sents[si]["i1"] + 1):
            words[i]["speaker"] = names[l]
    return True


LANG_P_MIN = 0.6    # measured: speech 0.99; room tone, machine noise and music 0.23 to 0.55
LOW_LANG_P = 0.8    # between the two, words are transcribed but tagged low_conf


def detect_language(model, a16, spans, tmp_wav):
    """whisper-cli -dl on up to 30 s of the voiced material. Returns (language, probability) or (None, 0.0)."""
    parts, tot = [], 0.0
    for s0, s1 in spans:
        a, b = int(max(0, s0 - 0.05) * 16000), int((s1 + 0.05) * 16000)
        parts.append(a16[a:b])
        tot += (b - a) / 16000.0
        if tot >= 30:
            break
    if tot < 0.6:
        return None, 0.0
    x = np.concatenate(parts)[: 30 * 16000].astype("<i2")
    import wave
    with wave.open(tmp_wav, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(x.tobytes())
    try:
        p = run([whisper_cli_path(), "-m", model, "-f", tmp_wav, "-dl", "-t", str(max(2, min(8, os.cpu_count() or 4)))],
                timeout=300, check=False)
    finally:
        os.remove(tmp_wav)
    mm = re.search(r"auto-detected language:\s*(\w+)\s*\(p\s*=\s*([\d.]+)\)", (p.stderr or "") + (p.stdout or ""))
    if not mm:
        return None, 0.0
    return mm.group(1), float(mm.group(2))


def run_whisper_cli(model, wav16k, outbase, language, duration):
    dtw = dtw_name(model)
    cmd = [whisper_cli_path(), "-m", model, "-f", wav16k, "-ml", "1", "-sow", "-ojf", "-of", outbase, "-np",
           "-t", str(max(2, min(8, (os.cpu_count() or 4))))]
    if dtw:
        cmd += ["-dtw", dtw, "-nfa"]
    cmd += ["-l", language or "auto"]
    prompt = FILLER_PROMPT if (language or "auto") in ("en", "auto") else None
    if prompt:
        cmd += ["--prompt", prompt]
    os.makedirs(os.path.dirname(outbase), exist_ok=True)
    if os.path.exists(outbase + ".json"):
        os.remove(outbase + ".json")
    p = run(cmd, timeout=max(300, int(4 * (duration or 60))))
    if not os.path.exists(outbase + ".json"):
        raise Fail("whisper-cli wrote no result (%s)" % ((p.stderr or "").strip()[-300:] or "no message"))
    with open(outbase + ".json", encoding="utf-8", errors="replace") as fh:
        js = json.load(fh)
    return js, prompt


PYW_AHEADS = {"tiny": "WHISPER_AHEADS_TINY", "tiny.en": "WHISPER_AHEADS_TINY_EN", "base": "WHISPER_AHEADS_BASE",
              "base.en": "WHISPER_AHEADS_BASE_EN", "small": "WHISPER_AHEADS_SMALL", "small.en": "WHISPER_AHEADS_SMALL_EN",
              "medium": "WHISPER_AHEADS_MEDIUM", "medium.en": "WHISPER_AHEADS_MEDIUM_EN",
              "large.v1": "WHISPER_AHEADS_LARGE_V1", "large.v2": "WHISPER_AHEADS_LARGE_V2",
              "large.v3": "WHISPER_AHEADS_LARGE_V3", "large.v3.turbo": "WHISPER_AHEADS_LARGE_V3_TURBO"}


def pyw_tokens(lib, ctx, i):
    """Token rows of segment i from a pywhispercpp context, in the shape whisper-cli -ojf writes (text, p, t_dtw in
    10 ms units, -1 when unknown)."""
    out = []
    for j in range(int(lib.whisper_full_n_tokens(ctx, i))):
        try:
            text = lib.whisper_full_get_token_text(ctx, i, j)
        except Exception:
            text = ""
        if isinstance(text, bytes):
            text = text.decode("utf-8", "replace")
        d = lib.whisper_full_get_token_data(ctx, i, j)
        p = getattr(d, "p", None)
        t = getattr(d, "t_dtw", -1)
        out.append({"text": text or "", "p": float(p) if p is not None and p == p else None,
                    "t_dtw": int(t) if isinstance(t, (int, float)) else -1})
    return out


def run_pywhispercpp(model, wav16k, outbase, language, duration):
    """pywhispercpp engine. Word edges need whisper's DTW token times (like whisper-cli -dtw): with them words stay in
    their own voiced span; without them sentence-final words can drift across a pause by up to a second."""
    from pywhispercpp.model import Model  # optional dependency (requirements-asr.txt)
    prompt = FILLER_PROMPT if (language or "auto") in ("en", "auto") else None
    kw = dict(print_realtime=False, print_progress=False, token_timestamps=True, max_len=1, split_on_word=True)
    if prompt:
        kw["initial_prompt"] = prompt
    if language and language != "auto":
        kw["language"] = language
    mdl, lib, timing = None, None, "approximate"
    dtw = dtw_name(model)
    if dtw in PYW_AHEADS:
        try:
            import _pywhispercpp as lib
            preset = getattr(lib.whisper_alignment_heads_preset, PYW_AHEADS[dtw])
            # flash attention off: with it on whisper.cpp leaves t_dtw at -1
            mdl = Model(model, context_params={"flash_attn": False, "dtw_token_timestamps": True,
                                               "dtw_aheads_preset": preset}, **kw)
            timing = "dtw"
        except Exception:
            mdl, lib = None, None
    if mdl is None:
        mdl = Model(model, **kw)
    segs = mdl.transcribe(wav16k)
    ctx = getattr(mdl, "_ctx", None)
    trans = []
    for i, s in enumerate(segs):
        toks = None
        if timing == "dtw" and lib is not None and ctx is not None:
            try:
                toks = pyw_tokens(lib, ctx, i)
            except Exception:
                toks = None
        if not toks:
            p = getattr(s, "probability", None)
            toks = [{"text": s.text, "p": float(p) if p is not None and p == p else None, "t_dtw": -1}]
        trans.append({"offsets": {"from": int(s.t0) * 10, "to": int(s.t1) * 10}, "text": s.text, "tokens": toks})
    if not any((t.get("t_dtw", -1) or -1) >= 0 for seg in trans for t in seg["tokens"]):
        timing = "approximate"
    js = {"model": {"file": os.path.basename(model)}, "result": {"language": language or "auto"},
          "transcription": trans, "word_timing": timing}
    write_json(outbase + ".json", js)
    return js, prompt


def _echo_norm(w):
    return re.sub(r"[^a-z0-9']", "", str(w).lower().replace("\u2019", "'"))


def drop_prompt_echo(ws, prompt, min_run=5):
    """Remove runs of words that repeat the whisper prompt.

    On music or machine noise with a forced language, whisper often writes its own prompt again and again ("Okay,
    here's what I'm, like, thinking."). A run of at least `min_run` words that follows the prompt's word order
    (wrapping around) is an echo, never speech. Returns (kept words, number dropped)."""
    if not prompt or not ws:
        return ws, 0
    P = [x for x in (_echo_norm(t) for t in prompt.split()) if x]
    N = [_echo_norm(w.get("w", "")) for w in ws]
    kill = [False] * len(ws)
    i = 0
    while i < len(ws):
        best = 0
        for s0 in range(len(P)):
            k = 0
            while i + k < len(ws) and N[i + k] and N[i + k] == P[(s0 + k) % len(P)]:
                k += 1
            best = max(best, k)
        if best >= min_run:
            for j in range(i, i + best):
                kill[j] = True
            i += best
        else:
            i += 1
    return [w for w, k in zip(ws, kill) if not k], sum(kill)


def step_transcribe(lab, m, opt):
    if not m.get("audio") or m["kind"] == "image":
        return "skip"
    ensure_audio(lab, m)
    os.makedirs(lab.p("media", "analysis"), exist_ok=True)
    # a new transcript replaces the words a script was aligned to: the old backup and the script flags go with them
    if os.path.exists(asr_backup_path(lab, m)):
        os.remove(asr_backup_path(lab, m))
    add_flag(m, "script_aligned", False)
    add_flag(m, "script_missing_words", False)
    engine, model = opt.get("_engine") or asr_engine(opt.get("asr", "auto"), opt.get("model"))
    lang = opt.get("language") or "auto"
    a16 = read_wav_i16(lab.absp(m["wav16k"]))
    off = float((m.get("audio") or {}).get("offset_s") or 0.0)
    t, db = band_db(a16)
    spans, thr, floor = voiced_spans(t, db)
    out = lab.p("media", "analysis", m["id"] + ".words.json")
    doc = {"schema": "resolve-editor/transcript@1", "media": m["id"], "engine": engine,
           "model": os.path.splitext(os.path.basename(model))[0] if model else None, "language": None, "prompt": None,
           "noise_floor_db": round(floor, 1), "speakers": {"k": 1, "method": "none"}, "words": [], "sentences": [],
           "pauses": []}

    def gap_floor(a, b):
        sel = (t >= a - off) & (t <= b - off)
        return round(float(np.median(db[sel])), 1) if sel.any() else None
    def silence_only(note_text):
        """No words: the pauses are the silence spans of the adaptive energy floor (after is null)."""
        dur = len(a16) / 16000.0
        # with no voiced span at all the sound is constant (silence, music or machine noise): no pauses to report
        edges = ([0.0] + [x for s in spans for x in s] + [dur]) if spans else []
        for a, b in zip(edges[0::2], edges[1::2]):
            if b - a >= PAUSE_MIN_S:
                doc["pauses"].append({"after": None, "t0": r3(a + off), "t1": r3(b + off), "dur": r3(b - a),
                                      "floor_db": gap_floor(a + off, b + off)})
        if note_text:
            doc["note"] = note_text
        write_json(out, doc)
        m["analysis"]["words"] = lab.rel(out)
        m["summary"]["words"] = 0
        m["summary"]["speech_s"] = 0.0 if engine != "none" else r3(sum(b - a for a, b in spans))
        add_flag(m, "speech", False)
        split_flag(m)
        return "ok"
    if engine == "none":
        return silence_only(None)
    raw_base = lab.p("media", "analysis", m["id"] + ".whisper")
    # measured with whisper-cli small: after a lead-in of 1.5 s or more (a camera rolling before the first word, a
    # padded voice-over) whisper puts the first word at 0 s, and after seconds of digital zero on both ends it lost
    # the word times of the second sentence. Transcribe only the voiced stretch (0.3 s either side) and shift back
    wav_in, t_lo, trim_wav = lab.absp(m["wav16k"]), 0.0, None
    if spans:
        dur16 = len(a16) / 16000.0
        lo_, hi_ = max(0.0, spans[0][0] - 0.3), min(dur16, spans[-1][1] + 0.3)
        if lo_ > 0.5 or dur16 - hi_ > 0.5:
            import wave
            trim_wav = raw_base + ".trim.wav"
            with wave.open(trim_wav, "wb") as w_:
                w_.setnchannels(1)
                w_.setsampwidth(2)
                w_.setframerate(16000)
                w_.writeframes(np.asarray(a16[int(lo_ * 16000):int(hi_ * 16000)], "<i2").tobytes())
            wav_in, t_lo = trim_wav, lo_
            doc["whisper_offset_s"] = r3(lo_)
    try:
        return _transcribe_rest(lab, m, opt, doc, out, engine, model, lang, a16, spans, off, raw_base, wav_in, t_lo,
                                gap_floor, silence_only)
    finally:
        if trim_wav and os.path.exists(trim_wav):
            os.remove(trim_wav)


def _transcribe_rest(lab, m, opt, doc, out, engine, model, lang, a16, spans, off, raw_base, wav_in, t_lo, gap_floor,
                     silence_only):
    with WHISPER_LOCK:
        if engine == "whisper-cli" and lang == "auto":
            # whisper guesses a language even for room tone or music and then invents words; its own language
            # probability on the voiced material tells speech (about 0.99) from noise (0.2 to 0.55)
            det, prob = detect_language(model, a16, spans, raw_base + ".probe.wav")
            doc["language_p"] = round(prob, 3)
            if det is None or prob < LANG_P_MIN:
                if os.path.exists(raw_base + ".json"):
                    os.remove(raw_base + ".json")
                return silence_only("no clear speech found (language detection %s p %.2f); pass --language en "
                                    "(or the spoken language) to transcribe anyway" % (det or "none", prob))
            lang = det
        if engine == "whisper-cli":
            js, prompt = run_whisper_cli(model, wav_in, raw_base, lang, m.get("duration_s"))
        else:
            js, prompt = run_pywhispercpp(model, wav_in, raw_base, lang, m.get("duration_s"))
    doc["prompt"] = prompt
    doc["language"] = (js.get("result") or {}).get("language") or (lang if lang != "auto" else None)
    if js.get("word_timing") == "approximate":
        doc["word_timing"] = "approximate"
        doc["note"] = ("word timing approximate: this pywhispercpp build gave no DTW token times, so a word can sit "
                       "up to a second off at pauses; install whisper-cli (Windows: whisper-bin-x64.zip and "
                       "RE_WHISPER_CLI) for exact edges")
    ws, echoed = drop_prompt_echo(whisper_words(js), prompt)
    if echoed:
        doc["prompt_echo_dropped"] = echoed
        if not ws:
            return silence_only("whisper only repeated its prompt (%d words) on this audio: no clear speech" % echoed)
    for w in ws:
        w["s"], w["e"] = w["s"] + t_lo, w["e"] + t_lo
        if w.get("dtw") is not None:
            w["dtw"] += t_lo
        w["raw"] = [w["s"], w["e"]]
    snapped = snap_words(ws, spans)
    words = []
    for i, w in enumerate(snapped):
        words.append({"i": i, "w": w["w"], "t0": r3(w["s"] + off), "t1": r3(w["e"] + off),
                      "raw": [r3(w["raw"][0] + off), r3(w["raw"][1] + off)], "p": None if w["p"] is None else round(w["p"], 3),
                      "speaker": "S1", "tags": [], "snapped": w["snapped"]})
    tag_words(words)
    # words with no length, or over no voiced sound (near silence, a music bed): the recogniser's inventions
    ph_ = mark_phantom(words, None if doc.get("word_timing") == "approximate" else spans, off)
    if ph_:
        doc["phantom_words"] = ph_
    t_, db_ = band_db(a16)
    _, thr_, _ = voiced_spans(t_, db_)
    edged = refine_filler_edges(words, micro_gaps(t_, db_, thr_), off)
    if edged:
        doc["edges_measured"] = edged
    lp = doc.get("language_p")
    if (lp is not None and lp < LOW_LANG_P) or ("silent" in (m["summary"].get("flags") or [])):
        # whisper was unsure this is speech at all, or the clip is nearly silent: every word is a guess
        mark_low_conf(words)
    pauses, after = [], {}
    for i in range(len(words) - 1):
        g = words[i + 1]["t0"] - words[i]["t1"]
        if g >= PAUSE_MIN_S:
            p = {"after": i, "t0": words[i]["t1"], "t1": words[i + 1]["t0"], "dur": r3(g),
                 "floor_db": gap_floor(words[i]["t1"], words[i + 1]["t0"])}
            a, b = words[i]["t1"] - off, words[i + 1]["t0"] - off
            voiced = sum(max(0.0, min(b, s1) - max(a, s0)) for s0, s1 in spans)
            if voiced >= 0.05:      # sound whisper did not turn into words (a breath, a laugh or a missed word)
                p["voiced_s"] = r3(voiced)
            pauses.append(p)
            after[i] = g
    sents = build_sentences(words, after)
    k = int(opt.get("speakers") or 1)
    if k > 1 and diarize(words, sents, a16, k, off):
        doc["speakers"] = {"k": k, "method": "mfcc-agglomerative"}
        sents = build_sentences(words, after)
    doc["words"], doc["sentences"], doc["pauses"] = words, sents, pauses
    write_json(out, doc)
    m["analysis"]["words"] = lab.rel(out)
    m["summary"]["words"] = len(words)
    m["summary"]["speech_s"] = r3(sum(w["t1"] - w["t0"] for w in words))
    add_flag(m, "speech", len(words) >= 3)
    split_flag(m)
    # speech in a sound-only file is usually a lav, recorder or podcast track recorded next to the cameras; v1 has
    # no sync, so say so instead of letting it pass as music or narration
    add_flag(m, "separate_sound", m["kind"] == "audio" and not m.get("music") and len(words) >= 20
             and m.get("role") != "voiceover")
    return "ok"


WHISPER_LOCK = threading.Lock()


def load_transcript(lab, m):
    rel = (m.get("analysis") or {}).get("words")
    if not rel or not os.path.exists(lab.absp(rel)):
        return None
    return load_json(lab.absp(rel))


# ---------------------------------------------------------------- the known script over the transcript
SCRIPT_MIN_MATCH = 0.6          # a script matching fewer of the words is for another file (refused without --force)
TEXT_TAGS = ("filler", "repeat", "flag")


def asr_backup_path(lab, m):
    return lab.p("media", "analysis", m["id"] + ".words.asr.json")


def retag_text(words, k):
    """The filler, repeat and flag tags of word k from its (new) text; every other tag stays."""
    w = words[k]
    n = normw(w["w"])
    tg = [t for t in (w.get("tags") or []) if t not in TEXT_TAGS]
    if n in FILLERS:
        tg.append("filler")
    if k > 0 and n and n == normw(words[k - 1]["w"]):
        tg.append("repeat")
    nxt = normw(words[k + 1]["w"]) if k + 1 < len(words) else None
    prv = normw(words[k - 1]["w"]) if k > 0 else None
    if n in FLAG_WORDS or any((n == a and nxt == b) or (n == b and prv == a) for a, b in FLAG_PHRASES):
        tg.append("flag")
    w["tags"] = tg


def _drop_tag(w, tag):
    w["tags"] = [t for t in (w.get("tags") or []) if t != tag]


def _add_tag(w, tag):
    w.setdefault("tags", [])
    if tag not in w["tags"]:
        w["tags"].append(tag)


def fix_one_word(words, k, text):
    """Word k shows text (times kept); the transcript's own text stays in asr. True when the text changed."""
    w = words[k]
    if w["w"] == text:
        return False
    if "asr" not in w:
        w["asr"] = w["w"]
    w["w"] = text
    _add_tag(w, "script_fix")
    _drop_tag(w, "low_conf")
    _drop_tag(w, "joined")
    w.pop("joined_to", None)
    return True


SCRIPT_GROUP_MAX = 4        # one spoken word written as at most this many transcript words (or script words)


def _block_fillers(ws, idx):
    """Positions (within idx) of the words of a replaced run that really are fillers: "um" and the like, words the
    transcript already tagged filler, and the two words of a filler phrase such as "you know"."""
    out = set()
    nw = [normw(ws[i]["w"]) for i in idx]
    for k, i in enumerate(idx):
        if nw[k] in FILLERS or "filler" in (ws[i].get("tags") or []):
            out.add(k)
        if k + 1 < len(idx) and (nw[k], nw[k + 1]) in FLAG_PHRASES:
            out.update((k, k + 1))
    return sorted(out)


def norm_cmp_unit_first(w):
    """norm_cmp of a script word with its currency said before the number ("rupees 499", "dollar 49" for "\u20b9499",
    "$49"), or None when it has no currency sign before a number."""
    s = str(w).lower().replace("\u2019", "'")
    if not re.search(r"[$\u20ac\u00a3\u20b9\u00a5]\s*\d", s):
        return None
    return norm_cmp(re.sub(r"([$\u20ac\u00a3\u20b9\u00a5])\s*(\d[\d.,]*)",
                           lambda m: CURRENCY_WORDS[m.group(1)] + " " + m.group(2), s))


def said_equal(heard_cmp, script_raw, script_cmp=None):
    """True when the heard words (their norm_cmp joined) say the script words: in the order norm_cmp spells them, or
    with a currency said before its number."""
    s = "".join(script_cmp) if script_cmp is not None else "".join(norm_cmp(x) for x in script_raw)
    if heard_cmp == s:
        return True
    alt = "".join(norm_cmp_unit_first(x) or norm_cmp(x) for x in script_raw)
    return alt != s and heard_cmp == alt


def same_said(heard, script):
    """True when heard transcript words and script words differ only in how they are written: a symbol spelled out
    ("20 percent" for "20%", "at studio" for "@studio", "10 thousand" for "10k", "dot com" for ".com", "rupees 499"
    for "\u20b9499") or a hyphen ("Hands-on" for "Hands on"). A word only split in two ("Ink well" for "Inkwell") is
    not, nor a number written as a word ("twenty percent" for "20%"): listen to those."""
    h, s = "".join(norm_cmp(x) for x in heard), "".join(norm_cmp(x) for x in script)
    raw = " ".join(list(heard) + list(script))

    def plain(xs):
        return "".join(re.sub(r"[^a-z0-9']", "", str(x).lower().replace("\u2019", "'")).strip("'") for x in xs)
    spelled = plain(heard) != h or plain(script) != s      # a symbol or a unit was spelled out
    return bool(h) and said_equal(h, script) and (spelled or any(c in raw for c in SYMBOL_CHARS + DASHES + "/"))


def _ratio(x, y):
    import difflib
    return difflib.SequenceMatcher(None, x, y, autojunk=False).ratio() if (x or y) else 1.0


def _map_runs(tw, sw):
    """Map transcript words tw onto script words sw in order, every segment one transcript word to a run of script
    words or a run of transcript words (one spoken word written as several) to one script word, at most
    SCRIPT_GROUP_MAX long, choosing the segments whose joined text is most alike. tw and sw are normalised texts.
    Returns [(n_transcript, n_script), ...] or None when no such mapping exists (or the run is too long to search)."""
    n, m = len(tw), len(sw)
    if n == m:
        return [(1, 1)] * n
    G = SCRIPT_GROUP_MAX
    if n == 0 or m == 0 or n > G * m or m > G * n or n * m > 6000:
        return None
    NEG = -1e9
    best = [[NEG] * (m + 1) for _ in range(n + 1)]
    back = [[None] * (m + 1) for _ in range(n + 1)]
    best[0][0] = 0.0
    for i in range(n + 1):
        for j in range(m + 1):
            if best[i][j] == NEG:
                continue
            steps = [(k, 1) for k in range(1, G + 1)] + [(1, k) for k in range(2, G + 1)]
            for a, b in steps:
                if i + a > n or j + b > m:
                    continue
                v = best[i][j] + _ratio("".join(tw[i:i + a]), "".join(sw[j:j + b]))
                if v > best[i + a][j + b] + 1e-9:
                    best[i + a][j + b] = v
                    back[i + a][j + b] = (a, b)
    if best[n][m] == NEG:
        return None
    segs, i, j = [], n, m
    while i or j:
        a, b = back[i][j]
        segs.append((a, b))
        i, j = i - a, j - b
    return segs[::-1]


def _map_score(ws, idx, sw_raw, segs):
    tw = [norm_cmp(ws[i]["w"]) for i in idx]
    sw = [norm_cmp(t) for t in sw_raw]
    tot, i, j = 0.0, 0, 0
    for a, b in segs:
        tot += _ratio("".join(tw[i:i + a]), "".join(sw[j:j + b]))
        i, j = i + a, j + b
    return tot


def _script_cmp(tokens):
    """norm_cmp of each script token, where a handle written after the word "at" ("at @studio") is said once."""
    b = [norm_cmp(t) for t in tokens]
    for j in range(1, len(tokens)):
        if str(tokens[j]).startswith("@") and b[j - 1] == "at":
            b[j] = norm_cmp(str(tokens[j])[1:]) or b[j]
    return b


OPTIONAL_LIKE = 0.5      # how alike (difflib ratio) a heard word must be to an optional script word to read it
OPTIONAL_SHORT_LIKE = 0.9   # the same for an optional word of 3 letters or fewer ("VO", "Q"): only a near-exact read
# a list number said as a word ("One, keep it clean" or "First, ..." for "1. Keep it clean")
SAID_NUMBERS = {w: str(n) for n, w in enumerate("zero one two three four five six seven eight nine ten eleven "
                                                "twelve thirteen fourteen fifteen sixteen seventeen eighteen "
                                                "nineteen twenty".split())}
SAID_NUMBERS.update({w: str(n) for n, w in enumerate("first second third fourth fifth sixth seventh eighth ninth "
                                                     "tenth".split(), 1)})
SAID_NUMBERS.update({"number" + str(n): str(n) for n in range(21)})


def _read_optional(words, tokens, kinds):
    """Positions of the optional script tokens (speaker labels, stage directions) the voice reads: the ones the
    transcript matches word for word, and in a run that differs, as many as the transcript has words beyond the
    script's other words there (fillers not counted), the most alike first, when they look alike (OPTIONAL_LIKE).
    A label the voice reads is never lost."""
    import difflib
    a = [norm_cmp(w["w"]) for w in words]
    b = _script_cmp(tokens)
    read = set()
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        opt = [j for j in range(j1, j2) if kinds[j]]
        if not opt:
            continue
        if op == "equal":
            read.update(opt)
        elif op == "replace":
            idx = list(range(i1, i2))
            fil = set(_block_fillers(words, idx))
            heard = [idx[k] for k in range(len(idx)) if k not in fil]
            spare = len(heard) - ((j2 - j1) - len(opt))
            if spare > 0 and heard:
                # only a heard word that looks like the label ("Mith" for "MYTH") reads it: an unscripted "So" where
                # the script has "MAYA:" is an extra word like any other, never shown as the label
                like = {j: max(_ratio(SAID_NUMBERS.get(a[i], a[i]), b[j]) for i in heard) for j in opt}
                # a short label ("VO:", "V.O.:", "Q:") is read only when heard almost exactly: "So" or "No" is
                # half alike to "VO" by letters but is never the label read aloud
                best = sorted((j for j in opt if like[j] >= (OPTIONAL_SHORT_LIKE if len(b[j]) <= 3 else OPTIONAL_LIKE)),
                              key=lambda j: (-like[j], j))
                read.update(best[:spare])
    return read


def align_script(words, tokens, kinds=None, breaks=None):
    """Align the script's tokens to the transcript words without renumbering them (SequenceMatcher on the normalised
    text). kinds (from script_parts) marks optional tokens, speaker labels and stage directions: the ones the voice
    reads are aligned like any word, the others are left out (info["unread"]) and are never missing. Equal words take the script's spelling and punctuation. A replaced run never removes speech: its real
    fillers ("um", a filler phrase) go first when the transcript has more words there than the script (tagged extra
    and filler, so the preset's speech.remove drops them); the rest map in order, and where whisper wrote one spoken
    word as two ("Ink well" for "Inkwell") the first part takes the script word and the others are tagged joined
    (kept in the voice, hidden in the captions); where the script has more words, one transcript word takes several.
    Every replaced run whose word counts differ is listed under listen. Transcript words the script lacks entirely
    are extra (extra and filler); script words the voice skipped are listed as missing. Returns (words, info)."""
    import difflib
    import itertools
    kinds = list(kinds) if kinds is not None else [None] * len(tokens)
    brk_set = set(breaks or ())
    trip = [(t, k, j in brk_set) for j, (t, k) in enumerate(zip(tokens, kinds))]
    trip = [x for x in trip if norm_cmp(x[0])]          # punctuation alone ("...", a dash) is no word
    tokens, kinds = [x[0] for x in trip], [x[1] for x in trip]
    brk = [x[2] for x in trip]
    unread = []
    if any(kinds):
        read = _read_optional(words, tokens, kinds)
        unread = [{"text": tokens[j], "kind": kinds[j][0], "of": kinds[j][1]} for j in range(len(tokens))
                  if kinds[j] and j not in read]
        keep_j = [j for j in range(len(tokens)) if not kinds[j] or j in read]
        brk2 = [brk[j] for j in keep_j]
        for j in range(len(tokens)):                     # a line ending on a direction not read ends on the word before
            if brk[j] and j not in keep_j:
                prior = [p for p, jj in enumerate(keep_j) if jj < j]
                if prior:
                    brk2[prior[-1]] = True
        tokens, brk = [tokens[j] for j in keep_j], brk2
    a = [norm_cmp(w["w"]) for w in words]
    b = _script_cmp(tokens)
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    out = copy.deepcopy(words)
    matched, replaced, extra, missing, joined, listen = 0, [], [], [], [], []
    said = 0

    def take(k, text, heard=None):
        old = out[k]["w"]
        if normw(old) != normw(text):
            fix_one_word(out, k, text)
            retag_text(out, k)
            replaced.append({"i": k, "asr": old, "text": text, "heard": heard or old})
        else:
            out[k]["w"] = text
            _drop_tag(out[k], "low_conf")

    def mark_extra(k):
        _add_tag(out[k], "extra")
        _add_tag(out[k], "filler")
        extra.append({"i": k, "text": out[k]["w"]})

    def mark_joined(k, head):
        out[k]["tags"] = [t for t in (out[k].get("tags") or []) if t not in TEXT_TAGS and t != "low_conf"]
        _add_tag(out[k], "joined")
        out[k]["joined_to"] = head
        joined.append({"i": k, "text": out[k]["w"], "to": head})

    ends = set()        # heard words that end a list item (a caption ends after them)
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            for k in range(i2 - i1):
                out[i1 + k]["w"] = tokens[j1 + k]
                _drop_tag(out[i1 + k], "low_conf")
                if brk[j1 + k]:
                    ends.add(i1 + k)
                if j1 + k > 0 and b[j1 + k - 1] == b[j1 + k]:
                    _drop_tag(out[i1 + k], "repeat")        # the script says it twice: no stutter
                matched += 1
        elif op == "replace":
            n, mm = i2 - i1, j2 - j1
            idx = list(range(i1, i2))
            sw_raw = tokens[j1:j2]
            sw = b[j1:j2]
            drop = []
            fil = _block_fillers(out, idx)
            if n > mm and fil:
                r = min(len(fil), n - mm)
                if len(fil) > 8:
                    cands = [tuple(fil[:r])]
                else:
                    cands = list(itertools.combinations(fil, r))
                best = None
                for cset in cands:
                    keep = [idx[k] for k in range(n) if k not in cset]
                    if not keep:
                        continue
                    segs = _map_runs([norm_cmp(out[i]["w"]) for i in keep], sw)
                    sc = _map_score(out, keep, sw_raw, segs) if segs else -1.0
                    if best is None or sc > best[0] + 1e-9:
                        best = (sc, cset)
                if best is not None:
                    drop = [idx[k] for k in best[1]]
            keep = [i for i in idx if i not in drop]
            segs = _map_runs([norm_cmp(out[i]["w"]) for i in keep], sw)
            if segs is None:
                # too different to search: in order, the last word takes (or joins) the rest
                nk = len(keep)
                if nk >= mm:
                    segs = [(1, 1)] * (mm - 1) + [(nk - mm + 1, 1)]
                else:
                    segs = [(1, 1)] * (nk - 1) + [(1, mm - nk + 1)]
            for k in drop:
                mark_extra(k)
            ti = tj = 0
            for na, nb in segs:
                head = keep[ti]
                if said_equal("".join(norm_cmp(out[i]["w"]) for i in keep[ti:ti + na]), sw_raw[tj:tj + nb],
                              sw[tj:tj + nb]):
                    said += na + nb           # the same words, written another way ("20 percent" for "20%")
                take(head, " ".join(sw_raw[tj:tj + nb]), " ".join(words[i]["w"] for i in keep[ti:ti + na]))
                for k in keep[ti + 1:ti + na]:
                    mark_joined(k, head)
                if any(brk[j1 + tj:j1 + tj + nb]):
                    ends.add(keep[ti + na - 1])
                ti, tj = ti + na, tj + nb
            if len(keep) != mm and not same_said([words[i]["w"] for i in keep], sw_raw):
                listen.append({"words": [i1, i2 - 1], "heard": " ".join(words[i]["w"] for i in idx),
                               "script": " ".join(sw_raw)})
        elif op == "delete":
            for k in range(i1, i2):
                mark_extra(k)
        elif op == "insert":
            missing.append({"after": i1 - 1 if i1 > 0 else None, "text": " ".join(tokens[j1:j2])})
            if any(brk[j1:j2]) and i1 > 0:
                ends.add(i1 - 1)
    # "and you know you learn" against "and you learn": the matcher keeps the first "you" and drops "know you". The
    # same text results when the filler phrase itself ("you know") is the extra run, which is what was said, so slide
    # an extra run left while the word before it equals its last word and opens a filler phrase
    ex = {e["i"] for e in extra}
    k = 0
    while k < len(out):
        if k not in ex:
            k += 1
            continue
        e = k
        while e + 1 < len(out) and e + 1 in ex:
            e += 1
        s0 = k
        while (s0 > 0 and s0 - 1 not in ex and "script_fix" not in (out[s0 - 1].get("tags") or [])
               and "joined" not in (out[s0 - 1].get("tags") or [])
               and normw(out[s0 - 1]["w"]) == normw(out[e]["w"])
               and ((normw(out[s0 - 1]["w"]), normw(out[s0]["w"])) in FLAG_PHRASES
                    or normw(out[s0 - 1]["w"]) in FILLERS)):
            txt = out[s0 - 1]["w"]
            for t in ("extra", "filler"):
                _drop_tag(out[e], t)
            out[e]["w"] = txt
            retag_text(out, e)
            ex.discard(e)
            ex.add(s0 - 1)
            _add_tag(out[s0 - 1], "extra")
            _add_tag(out[s0 - 1], "filler")
            s0, e = s0 - 1, e - 1
        k = max(k, e) + 1 if s0 == k else e + 1
    extra = [{"i": i, "text": out[i]["w"]} for i in sorted(ex)]
    # a repeat is judged against the word heard before it once the extra words are gone: "that that" read from a
    # script with one "that" loses one of them as extra, and the one left is no stutter any more
    prev = None
    for k in range(len(out)):
        tg = out[k].get("tags") or []
        if "extra" in tg or "joined" in tg:
            continue
        if "repeat" in tg and (prev is None or normw(out[prev]["w"]) != normw(out[k]["w"])):
            _drop_tag(out[k], "repeat")
        prev = k
    for k in range(len(out)):
        (_add_tag if k in ends else _drop_tag)(out[k], "line_end")
    match = (2.0 * matched + said) / max(1, len(a) + len(b))
    return out, {"match": round(match, 3), "matched": matched, "replaced": replaced, "extra": extra,
                 "missing": missing, "joined": joined, "listen": listen, "unread": unread}


def _pauses_after(doc):
    return {int(p["after"]): float(p.get("dur") or 0.0) for p in doc.get("pauses") or [] if p.get("after") is not None}


def _script_text(file=None, text=None):
    if (file is None) == (text is None):
        raise Fail("give the voice-over script with --file PATH or --text \"TEXT\" (one of them)")
    if file is not None:
        p = os.path.expanduser(file)
        if not os.path.isfile(p):
            raise Fail("no script file at %s" % p)
        with open(p, encoding="utf-8-sig") as fh:
            return fh.read(), os.path.basename(p)
    return text, "text"


SCRIPT_LABELS = re.compile(r"^(?i:vo|v\.o\.|voice-?over|voice over|narrator|narration|speaker(?: ?\d{1,2})?|host|"
                           r"presenter|announcer|anncr|talent):$")
CAPS_LABEL = re.compile(r"^[A-Z][A-Z0-9.'_-]{0,15}(?: \d{1,2})?:$")
NAME_LABEL = re.compile(r"^[A-Z][a-z][a-z'-]{0,14}(?: \d{1,2})?:$")          # "Maya:", "Tip:" opening two lines
# a screenplay cue: "MAYA (V.O.):", "MAYA (O.S.)", "DR. SHAH (CONT'D)" (a label even once, with or without a colon)
EXT_LABEL = re.compile(r"^[A-Z][A-Za-z0-9.'_-]{0,15}(?: [A-Z][A-Za-z0-9.'_-]{0,15})? "
                       r"\((?i:v\.? ?o\.?|o\.? ?s\.?|o\.? ?c\.?|cont'?d\.?|cont\.?|off|on phone)\):?$")
CUE_LINE = re.compile(r"^[A-Z][A-Z0-9.'_-]{1,15}(?: [A-Z][A-Z0-9.'_-]{1,15})?$")   # "MAYA" alone on its line
# a line that is a direction, not speech: "SFX: buzz of the machine", "SUPER: 20% off", "INT. STUDIO - DAY"
DIRECTION_LINE = re.compile(r"^(?i:sfx|fx|sound(?: effects?)?|music|mx|super|title|text|caption|on[- ]?screen(?: text)?|"
                            r"ost|gfx|graphics?|visuals?|video|shot|scene|cut to|b-?roll|footage|lower third)\s*:|"
                            r"^(?:INT|EXT|INT\./EXT)\.\s")
# characters that are never a word and never belong in a caption: bullets, arrows, shapes, dingbats and emoji
PICTO = re.compile("[\u2022\u2023\u2043\u2190-\u21ff\u2300-\u23ff\u2460-\u24ff\u25a0-\u27bf\u2900-\u297f"
                   "\u2b00-\u2bff\u200d\u20e3\ufe0e\ufe0f\U0001f000-\U0001faff]")
GLUE_CLOSE = set(".,;:!?\u2026)]}\u00bb\u203a\u201d\u2019")     # sentence punctuation, closing marks: the word before
GLUE_OPEN = set("([{\u00ab\u2039\u201c\u2018")                  # opening marks: with the word after
MARK_EDGE = "*|\\`^_~"          # markdown emphasis, table bars and the like at a word's edges
LIST_NUM = re.compile(r"^\(?(\d{1,2})[.)]$")      # "1." or "2)" opening a list line
BULLET_ALONE = set("*+->\u2013\u2014")           # these open a list line only when they stand alone


def _script_word(t):
    """A script token as a caption may show it: no emoji, bullets or arrows, no markdown marks at its edges."""
    t = PICTO.sub("", t)
    return t.strip(MARK_EDGE) if norm_cmp(t.strip(MARK_EDGE)) else t


def _list_number(t):
    """The number of a list item's number token ("1.", "2)", a keycap "3\ufe0f\u20e3", a circled "\u2463"), else
    None."""
    s = str(t)
    m = LIST_NUM.match(s)
    if m:
        return int(m.group(1))
    if "\u20e3" in s and re.sub(r"[\ufe0e\ufe0f\u20e3]", "", s).isdigit():
        return int(re.sub(r"[\ufe0e\ufe0f\u20e3]", "", s))
    if len(s) == 1 and "①" <= s <= "⑳":
        return ord(s) - 0x2460 + 1
    return None


def _list_marker(t):
    """A token that opens a list item: a bullet, an arrow or an emoji (alone or glued to its word), a "*", "-" or "+"
    standing alone, or a list number ("1.", "2)")."""
    s = str(t)
    return bool(s) and (bool(PICTO.match(s)) or all(c in BULLET_ALONE for c in s) or _list_number(s) is not None)


def script_parts(raw):
    """The words of a voice-over script, each with its kind: None (speech), "label" (a speaker label opening a line)
    or "direction" (a stage direction: a bracketed span, "(beat)", or a line such as "SFX: ..." or "SUPER: ...");
    an optional word's kind is (kind, the label's name or the direction's text).
    Labels are "VO:", "NARRATOR:", "SPEAKER 1:", a screenplay cue ("MAYA (V.O.):") and a name that opens two lines or
    more ("MAYA:", "Maya:", or "MAYA" alone on its line). Labels and directions are optional: align_script keeps the
    ones the voice reads and drops the others without calling them missing. Sentence punctuation standing alone
    ("...", "!") goes with the word before it and an opening bracket or quote with the word after it; a dash, a
    bullet, an arrow, a slash, a bar or an emoji standing alone is no word, and emoji, bullets and arrows inside a
    word are removed. A list item's number ("1.", "2)") is optional too (kind "number"). info["breaks"] holds the
    indexes of the words that end a list item, or the line before the first item ("Three tips:"): a caption ends
    there. Returns ([(token, kind)], {"labels": [...], "directions": [...], "numbers": [...], "breaks": [...]})."""
    lines = [ln.split() for ln in str(raw).splitlines()]

    def label_of(toks):
        for n in (3, 2, 1):
            if len(toks) < n:
                continue
            lab_ = " ".join(toks[:n])
            if EXT_LABEL.match(lab_) and (n == len(toks) or lab_.endswith(":")):
                return n, lab_, True
            if not lab_.endswith(":"):
                continue
            if SCRIPT_LABELS.match(lab_):
                return n, lab_, True
            if CAPS_LABEL.match(lab_) or NAME_LABEL.match(lab_):
                return n, lab_, False
        if toks and CUE_LINE.match(" ".join(toks)):
            return len(toks), " ".join(toks), False
        return 0, None, False
    seen = {}
    for toks in lines:
        n, lab_, sure = label_of(toks)
        if n and not sure:
            key = lab_.rstrip(":")
            seen[key] = seen.get(key, 0) + 1
    out, labels, directions, numbers = [], [], [], []
    breaks, prev_last = set(), None       # prev_last: index in out of the last word of the last line with words
    pre = ""
    for toks in lines:
        n, lab_, sure = label_of(toks)
        kinds = [None] * len(toks)
        if n and (sure or seen.get(lab_.rstrip(":"), 0) >= 2):
            name = re.sub(r"\s*\(.*$", "", lab_.rstrip(":"))
            labels.append(name)
            kinds[:n] = [("label", name)] * n
        body = toks[n:] if kinds[:1] != [None] else toks
        k0 = len(toks) - len(body)
        if body and DIRECTION_LINE.match(" ".join(body)):
            directions.append(" ".join(body))
            kinds[k0:] = [("direction", " ".join(body))] * len(body)
        else:
            depth, start = 0, None
            for k in range(k0, len(toks)):
                t = toks[k]
                if depth == 0 and t[:1] in "[(" and not (k == k0 and _list_number(t) is not None):
                    start = k
                depth = max(0, depth + t.count("[") + t.count("(") - t.count("]") - t.count(")"))
                if start is not None and depth <= 0:
                    d_ = " ".join(toks[start:k + 1])
                    directions.append(d_)
                    kinds[start:k + 1] = [("direction", d_)] * (k + 1 - start)
                    depth, start = 0, None
        # a list item: the line (after its label) opens with a bullet, an arrow, an emoji or a number
        item = len(body) >= 2 and _list_marker(body[0])
        if item and kinds[k0] is None and _list_number(body[0]) is not None:
            numbers.append(str(_list_number(body[0])))
            kinds[k0] = ("number", str(_list_number(body[0])))
        n_before = len(out)
        for t, kd in zip(toks, kinds):
            w = _script_word(t)
            if norm_cmp(w):
                out.append((pre + w, kd))
                pre = ""
            elif not w or all(c in DASHES for c in w):
                # a bullet, an arrow or an emoji between two words separates them ("ink → Keep it clean")
                if PICTO.match(t) and len(out) > n_before:
                    breaks.add(len(out) - 1)
                continue
            elif all(c in GLUE_CLOSE for c in w):
                if out:
                    out[-1] = (out[-1][0] + w, out[-1][1])
            elif all(c in GLUE_OPEN for c in w):
                pre += w
        if len(out) > n_before:
            if item:
                breaks.add(len(out) - 1)
                if prev_last is not None:
                    breaks.add(prev_last)
            prev_last = len(out) - 1
    return out, {"labels": sorted(set(labels)), "directions": directions, "numbers": numbers,
                 "breaks": sorted(breaks)}


def script_tokens(raw):
    """The words of a voice-over script as they are read when none of its labels and directions are read (see
    script_parts). Returns (tokens, labels)."""
    parts, info = script_parts(raw)
    return [t for t, kd in parts if kd is None], info["labels"]


def cmd_script(lab, mid, file=None, text=None, force=False, as_json=False):
    """Align the known voice-over text to ID's transcript (see align_script). The first run backs the transcript up
    to <id>.words.asr.json and every run starts from that backup, so a second run with the same script changes
    nothing. Word fixes made with fix-word are applied again on top."""
    m = lab.get(mid)
    doc = load_transcript(lab, m)
    if not doc or not doc.get("words"):
        raise Fail("%s has no transcribed words; run transcribe first (a script needs the word times)" % mid)
    raw, source = _script_text(file, text)
    parts, _pinfo = script_parts(raw)
    tokens, kinds = [t for t, _ in parts], [k for _, k in parts]
    if not any(k is None for k in kinds):
        raise Fail("the script is empty" if not tokens else "the script has only speaker labels, directions and "
                   "list numbers")
    bk = asr_backup_path(lab, m)
    base = load_json(bk) if os.path.exists(bk) else copy.deepcopy(doc)
    words, info = align_script(base["words"], tokens, kinds, _pinfo.get("breaks"))
    labels = sorted({u["of"] for u in info["unread"] if u["kind"] == "label"})
    directions, numbers = [], []
    for u in info["unread"]:
        if u["kind"] == "direction" and u["of"] not in directions:
            directions.append(u["of"])
        elif u["kind"] == "number":
            numbers.append(u["of"])
    if info["match"] < SCRIPT_MIN_MATCH and not force:
        raise Fail("the script matches only %d %% of the words of %s (at least %d %% needed): is it the script of this "
                   "file? Check it, or add --force to align it anyway" % (round(100 * info["match"]), mid,
                                                                        round(100 * SCRIPT_MIN_MATCH)))
    fixes = doc.get("fixes") or {}
    for k, t in sorted(fixes.items(), key=lambda kv: int(kv[0])):
        if 0 <= int(k) < len(words):
            fix_one_word(words, int(k), t)
    edges = doc.get("edges") or {}
    apply_hand_edges(words, edges)
    edged = refine_words_from_wav(lab, m, words)       # the script can make a word a filler ("or" heard for "uh")
    new = copy.deepcopy(base)
    new["words"] = words
    new["sentences"] = build_sentences(words, _pauses_after(base))
    if fixes:
        new["fixes"] = fixes
    if edges:
        new["edges"] = edges
    if edged:
        new["edges_measured"] = edged
    else:
        new.pop("edges_measured", None)
    new["script"] = {"source": source, "hash": hashlib.sha1(" ".join(tokens).encode("utf-8")).hexdigest()[:16],
                     "aligned": True, "match": info["match"], "replaced": len(info["replaced"]),
                     "extra": len(info["extra"]), "joined": len(info["joined"]), "missing": info["missing"],
                     "listen": info["listen"], "labels_skipped": labels, "directions_skipped": directions,
                     "numbers_skipped": numbers, "created": iso_now()}
    old_cmp = dict(doc, script=dict(doc.get("script") or {}, created=None))
    new_cmp = dict(new, script=dict(new["script"], created=None))
    changed = json.dumps(old_cmp, sort_keys=True) != json.dumps(new_cmp, sort_keys=True)
    out = lab.absp(m["analysis"]["words"])
    if changed:
        if not os.path.exists(bk):
            write_json(bk, doc)
        write_json(out, new)
    add_flag(m, "script_aligned")
    add_flag(m, "script_missing_words", bool(info["missing"]))
    lab.save()
    res = {"id": mid, "match": info["match"], "replaced": info["replaced"], "extra": info["extra"],
           "joined": info["joined"], "listen": info["listen"], "missing": info["missing"], "changed": changed,
           "labels_skipped": labels, "directions_skipped": directions, "numbers_skipped": numbers,
           "words": posix(out), "backup": posix(bk)}
    if as_json:
        print(json.dumps(res, indent=1))
        return res
    print("SCRIPT %s  match %.2f  replaced %d  extra %d  joined %d  missing %d%s%s%s" % (
        mid, info["match"], len(info["replaced"]), len(info["extra"]), len(info["joined"]), len(info["missing"]),
        ("  (speaker labels not read: %s)" % ", ".join(labels)) if labels else "",
        ("  (directions not read: %s)" % "; ".join(directions)) if directions else "",
        ("  (list numbers not read: %s)" % ", ".join(numbers)) if numbers else ""))
    for r in info["replaced"]:
        hd = r.get("heard") or r["asr"]
        unlike = (not same_said([hd], [r["text"]])
                  and _ratio(SAID_NUMBERS.get(norm_cmp(hd), norm_cmp(hd)), norm_cmp(r["text"])) < 0.4)
        print("  word %d: %r -> %r (times kept%s)" % (r["i"], r["asr"], r["text"],
                                                      "; they look unlike: play it" if unlike else ""))
    for e in info["extra"]:
        print("  extra word %d %r: not in the script, tagged extra and filler (assemble drops it when a pause allows)"
              % (e["i"], e["text"]))
    for j in info["joined"]:
        print("  word %d %r joined to word %d: part of the same spoken word (kept in the voice, hidden in the "
              "captions)" % (j["i"], j["text"], j["to"]))
    for x in info["listen"]:
        print("  LISTEN words %d-%d: heard as %r where the script has %r; play them and check the voice says the "
              "script (fix one with fix-word)" % (x["words"][0], x["words"][1], x["heard"], x["script"]))
    for x in info["missing"]:
        print("  missing %s: %r is in the script but not heard (check the recording)" % (
            "at the start" if x["after"] is None else "after word %d" % x["after"], x["text"]))
    print("WROTE %s%s" % (posix(out), "" if changed else " (unchanged)"))
    return res


def cmd_fix_word(lab, mid, index, text):
    """One word's text by hand (times kept, the transcript's word stays in asr). Kept across script runs."""
    m = lab.get(mid)
    doc = load_transcript(lab, m)
    if not doc or not doc.get("words"):
        raise Fail("%s has no transcribed words; run transcribe first" % mid)
    W = doc["words"]
    if not 0 <= index < len(W):
        raise Fail("word %d is outside 0..%d of %s" % (index, len(W) - 1, mid))
    text = str(text).strip()
    if not text or len(text.split()) > 3:
        raise Fail("give the word as it should read (one to three words, not empty)")
    bk = asr_backup_path(lab, m)
    if not os.path.exists(bk):
        write_json(bk, doc)
    old = W[index]["w"]
    fix_one_word(W, index, text)
    retag_text(W, index)
    doc.setdefault("fixes", {})[str(index)] = text
    doc["sentences"] = build_sentences(W, _pauses_after(doc))
    write_json(lab.absp(m["analysis"]["words"]), doc)
    print("FIXED %s word %d: %r -> %r (times kept; the transcript's text is kept as asr)" % (mid, index, old, text))
    print("WROTE %s" % posix(lab.absp(m["analysis"]["words"])))


def cmd_edge(lab, mid, index, sec):
    """Move the edge before word INDEX to SEC seconds (the word before ends there, this word starts there): an edge
    found by ear or on the waveform where whisper's time is off and no silence marks it. Kept across script runs."""
    m = lab.get(mid)
    doc = load_transcript(lab, m)
    if not doc or not doc.get("words"):
        raise Fail("%s has no transcribed words; run transcribe first" % mid)
    W = doc["words"]
    if not 0 < index < len(W):
        raise Fail("word %d has no word before it in 1..%d of %s (the edge is between word INDEX-1 and INDEX)" % (
            index, len(W) - 1, mid))
    sec = float(sec)
    lo, hi = float(W[index - 1]["t0"]) + 0.03, float(W[index]["t1"]) - 0.03
    if not lo <= sec <= hi:
        raise Fail("%.3f s is outside %.3f..%.3f s: the edge must leave both words at least 30 ms (%r starts at %.3f, "
                   "%r ends at %.3f)" % (sec, lo, hi, W[index - 1]["w"], W[index - 1]["t0"], W[index]["w"],
                                         W[index]["t1"]))
    bk = asr_backup_path(lab, m)
    if not os.path.exists(bk):
        write_json(bk, doc)
    was = (W[index - 1]["t1"], W[index]["t0"])
    doc.setdefault("edges", {})[str(index)] = round(sec, 3)
    apply_hand_edges(W, {str(index): sec})
    doc["sentences"] = build_sentences(W, _pauses_after(doc))
    write_json(lab.absp(m["analysis"]["words"]), doc)
    print("EDGE %s before word %d (%r | %r): %.3f/%.3f -> %.3f s (kept across script runs)" % (
        mid, index, W[index - 1]["w"], W[index]["w"], was[0], was[1], sec))
    print("WROTE %s" % posix(lab.absp(m["analysis"]["words"])))


# ---------------------------------------------------------------- music: tempo, beats, downbeats, sections
BEAT_SR, NFFT, HOP = 24000, 1024, 240          # 10 ms hop, 23.4 Hz bins (same grid as 48 kHz / 2048 / 480)


def audio_features(x, chunk=4096):
    """Per STFT frame: full and low band (under 150 Hz) spectral flux, chroma (12), 5 band log energies, RMS."""
    x = np.asarray(x, dtype=np.float32).reshape(-1)
    n = 1 + (len(x) - NFFT) // HOP
    if n < 10:
        raise Fail("the audio is too short for beat analysis")
    freqs = np.fft.rfftfreq(NFFT, 1 / BEAT_SR)
    low = freqs < 150
    ok = (freqs > 60) & (freqs < 2000)
    pc = np.round(12 * np.log2(freqs[ok] / 261.63)).astype(int) % 12
    bands = [(20, 150), (150, 600), (600, 2500), (2500, 8000), (8000, 20000)]
    bsel = [(freqs >= a) & (freqs < b) for a, b in bands]
    wnd = np.hanning(NFFT).astype(np.float32)
    full_f, low_f = np.zeros(n), np.zeros(n)
    C, E, rms = np.zeros((n, 12), np.float32), np.zeros((n, 5), np.float32), np.zeros(n, np.float32)
    prev = None
    base = np.arange(NFFT)[None, :]
    for s0 in range(0, n, chunk):
        s1 = min(n, s0 + chunk)
        fr = x[base + HOP * np.arange(s0, s1)[:, None]]
        rms[s0:s1] = np.sqrt((fr ** 2).mean(1))
        S = np.abs(np.fft.rfft(fr * wnd, axis=1)).astype(np.float32)
        L = np.log1p(100 * S)
        Lp = np.vstack([prev[None, :] if prev is not None else L[:1], L])
        fl = np.maximum(0, np.diff(Lp, axis=0))
        full_f[s0:s1] = fl.sum(1)
        low_f[s0:s1] = fl[:, low].sum(1)
        prev = L[-1]
        So = S[:, ok]
        for k in range(12):
            C[s0:s1, k] = So[:, pc == k].sum(1)
        for j, sel in enumerate(bsel):
            E[s0:s1, j] = np.log1p(S[:, sel].sum(1))
    full_f[0] = low_f[0] = 0
    C /= np.linalg.norm(C, axis=1, keepdims=True) + 1e-9

    def norm(v):
        v = v - np.convolve(v, np.ones(40) / 40, mode="same")
        v = np.maximum(v, 0)
        return v / (v.std() + 1e-9)
    return norm(full_f), norm(low_f), C, E, rms


def tempo(oenv, fps, lo=60, hi=200, prior_bpm=115, prior_oct=1.0):
    o = oenv - oenv.mean()
    n = len(o)
    F = np.fft.rfft(o, 2 * n)
    ac = np.fft.irfft(np.abs(F) ** 2)[:n]
    lags = np.arange(n)
    bpm = 60 * fps / np.maximum(lags, 1)
    ok = (bpm >= lo) & (bpm <= hi) & (lags > 0)
    w = np.exp(-0.5 * (np.log2(np.maximum(bpm, 1e-9) / prior_bpm) / prior_oct) ** 2)
    score = np.where(ok, ac * w, -np.inf)
    k = int(np.argmax(score))
    # metrical level check: a kick on beats 1 and 3 makes the two-beat lag the most periodic one; when the half
    # lag still carries strong periodicity, take the level that sits closer to the prior tempo
    h = int(round(k / 2))
    if h >= 1 and ok[h] and ac[h] >= 0.35 * ac[k] and abs(np.log2(bpm[h] / prior_bpm)) < abs(np.log2(bpm[k] / prior_bpm)):
        k = h
    strength = float(ac[k] / (ac[0] + 1e-12))
    kf = float(k)
    if 1 <= k < n - 1:
        a, b, c = ac[k - 1], ac[k], ac[k + 1]
        den = a - 2 * b + c
        if abs(den) > 1e-12:
            kf = k + 0.5 * (a - c) / den
    return 60 * fps / kf, kf, strength


def dp_beats(oenv, period, alpha=100.0):
    """Ellis (2007) dynamic programming beat tracker."""
    n = len(oenv)
    score = oenv.astype(float).copy()
    back = -np.ones(n, int)
    lo, hi = max(1, int(round(0.5 * period))), max(2, int(round(2 * period)))
    taus_all = np.arange(lo, hi + 1)
    pen_all = alpha * np.log(taus_all / period) ** 2
    for t in range(lo, n):
        m = min(hi, t) - lo + 1
        taus = taus_all[:m]
        prev = t - taus
        cand = score[prev] - pen_all[:m]
        j = int(np.argmax(cand))
        score[t] = oenv[t] + cand[j]
        back[t] = prev[j]
    p = int(period)
    t = int(np.argmax(score[max(0, n - p - 1):])) + max(0, n - p - 1)
    beats = [t]
    while back[t] >= 0:
        t = back[t]
        beats.append(t)
    return np.array(beats[::-1])


def foote_sections(C, E, db_times, fps, k_bars=2):
    """Bar-synchronous chroma and band energy features, cosine self-similarity, checkerboard novelty, peaks."""
    feats = []
    n_fr = len(C)
    for a, b in zip(db_times[:-1], db_times[1:]):
        i = min(max(0, int(a * fps)), n_fr - 1)
        j = min(n_fr, max(i + 1, int(b * fps)))
        feats.append(np.concatenate([C[i:j].mean(0), E[i:j].mean(0) / 10, E[i:j].std(0) / 10]))
    if len(feats) < 2 * k_bars + 1:
        return [0], feats
    F = np.array(feats)
    F = (F - F.mean(0)) / (F.std(0) + 1e-9)
    F /= np.linalg.norm(F, axis=1, keepdims=True) + 1e-9
    S = F @ F.T
    n, k = len(F), k_bars
    kern = np.block([[np.ones((k, k)), -np.ones((k, k))], [-np.ones((k, k)), np.ones((k, k))]])
    P = np.pad(S, k, mode="edge")
    nov = np.array([(P[i:i + 2 * k, i:i + 2 * k] * kern).sum() for i in range(n)])
    pk = [i for i in range(1, n - 1) if nov[i] > nov[i - 1] and nov[i] >= nov[i + 1] and nov[i] > np.percentile(nov, 60)]
    return [0] + pk, feats


def _acf(v):
    o = v - v.mean()
    return np.fft.irfft(np.abs(np.fft.rfft(o, 2 * len(o))) ** 2)[:len(o)]


def analyse_music(x, bpm_hint=None, downbeat_hint=None, move_grid=False):
    """Tempo, beats, downbeats and sections. bpm_hint (the known tempo) and downbeat_hint (the time of any bar
    start, in seconds of this signal) override the guesses when the user or the song's metadata knows better;
    move_grid (the user confirmed that bar line by ear) moves the found beats onto it whatever the offset."""
    fps = BEAT_SR / HOP
    full, low, C, E, rms = audio_features(x)
    octave_close = False
    if bpm_hint:
        b = float(bpm_hint)
        bpm, lag, strength = tempo(full, fps, lo=0.97 * b, hi=1.03 * b, prior_bpm=b)
    else:
        bpm, lag, strength = tempo(full, fps)
        acl = _acf(low)
        # octave check on the kick band: a groove whose loudest broadband hits repeat every two beats (a clap before
        # beat 2, an off-beat open hat) makes the two-beat lag win and puts the grid on the off-beats; when the kick
        # band still repeats every beat, take the beat level
        if bpm < 90:
            k, h = int(round(lag)), int(round(lag / 2))
            if h >= 1 and acl[h] >= 0.5 * acl[k]:
                # a close call: a slow groove at 78 to 90 BPM (hip hop, reggae, a half-time break) doubles as readily
                # as drum and bass halves, and a weak kick-band repeat is no proof either. Measured on 291 loops and
                # grooves: all 10 wrong doublings fall in here (5 right ones too), so say the tempo may be wrong
                octave_close = bpm >= 78 or acl[h] < 0.9 * acl[k]
                bpm, lag = bpm * 2, lag / 2
        # the reverse: busy eighth-note hats make a 90 BPM groove look like 180; when the kick band repeats at the
        # slower level too, take it (every slower beat is still a real beat, which is what cuts need)
        elif bpm > 150:
            k, d = int(round(lag)), int(round(2 * lag))
            if d < len(acl) - 2 and acl[d - 1:d + 2].max() >= 0.5 * acl[k]:
                bpm, lag = bpm / 2, lag * 2
        # a clap or hat pattern can make a lag at 3:4 or 2:3 of the beat the most periodic one in the full band while
        # the kick band has no periodicity there at all; when the kick band is strongly periodic at its own tempo,
        # that tempo is the beat
        bl_, ll_, sl_ = tempo(low, fps)
        r_ = lag / ll_ if ll_ > 0 else 1.0
        octave = any(abs(r_ / o - 1.0) < 0.04 for o in (0.5, 1.0, 2.0))
        if not octave and sl_ >= 0.7:
            at = float(acl[max(0, int(round(lag)) - 1):int(round(lag)) + 2].max())
            own = float(acl[max(0, int(round(ll_)) - 1):int(round(ll_)) + 2].max())
            if at < 0.5 * own:
                bpm, lag = bl_, ll_
    # a syncopated groove (dancehall, rolling drum and bass, conga patterns) can make a lag at 2:3 or 4:3 of the
    # beat as periodic as the beat itself, and then the whole grid is at the wrong tempo; say so
    acf = _acf(full)
    kk = int(round(lag))
    rival = 0.0
    for r in (2 / 3.0, 4 / 3.0):
        j = int(round(lag * r))
        if 2 <= j < len(acf) - 2:
            rival = max(rival, float(acf[j - 1:j + 2].max()))
    tempo_rival = rival / (float(acf[max(0, kk - 1):kk + 2].max()) + 1e-12)
    bf = dp_beats(full, lag)
    # phase check on the kick band: four-on-the-floor music whose loudest broadband hits are off the beat (an
    # off-beat hi-hat, a clap a 16th before beat 2) pulls the broadband grid off the kicks by a half or a quarter
    # beat; when the kick sits clearly at one shift of the found beats, move the whole grid there
    if len(bf) >= 8:
        L = int(round(lag))

        def kick_at(idx):
            idx = [int(i) for i in idx if 0 <= i and i + 3 < len(low)]
            return float(np.mean([low[max(0, i - 2): i + 3].max() for i in idx])) if idx else 0.0
        on_b = kick_at(bf)
        best_s, best_k = 0, on_b
        for s_ in range(-(L // 2), L - L // 2):
            if abs(s_) < max(3, L // 8):
                continue
            k_ = kick_at(bf + s_)
            if k_ > best_k:
                best_s, best_k = s_, k_
        if best_s and best_k > 1.5 * on_b and best_k > 1.0:
            # the window finds the kick; the exact frame is where the kick band peaks on average
            def at(s2):
                idx = [int(i) + s2 for i in bf if 0 <= int(i) + s2 < len(low)]
                return float(np.mean(low[idx])) if idx else 0.0
            best_s = max(range(best_s - 3, best_s + 4), key=at)
            bf = np.array([int(i) + best_s for i in bf if 0 <= int(i) + best_s and int(i) + best_s + 3 < len(low)])
    # the tracker's first beats are often irregular (it starts before the groove settles): drop an irregular head
    # and walk back from the first regular beat one period at a time while there is an onset there
    if len(bf) >= 8:
        L = int(round(lag))
        j = 0
        while j < 3 and abs(int(bf[j + 1]) - int(bf[j]) - lag) > 0.1 * lag:
            j += 1
        if abs(int(bf[j + 1]) - int(bf[j]) - lag) <= 0.1 * lag:
            bf = bf[j:]

            def onset_at(i):
                return max(float(full[max(0, i - 2): i + 3].max()), float(low[max(0, i - 2): i + 3].max()))
            ref = float(np.median([onset_at(int(i)) for i in bf[:16]]))
            head = [int(bf[0])]
            while head[0] - L >= 0 and onset_at(head[0] - L) > 0.3 * ref:
                head = [head[0] - L] + head
            bf = np.array(head[:-1] + [int(i) for i in bf])
    t_off = NFFT / 2 / BEAT_SR
    beats = bf / fps + t_off
    thr = 0.3 * np.percentile(full, 99.5)
    on = np.flatnonzero(full > thr)
    on_l = np.flatnonzero(low > 0.3 * np.percentile(low, 99.5))
    firsts = [v for v in ((on[0] if len(on) else None), (on_l[0] if len(on_l) else None)) if v is not None]
    first_onset = float(min(firsts) / fps + t_off) if firsts else float(beats[0])
    beats = beats[beats >= first_onset - 0.05]
    warnings = []
    if (tempo_rival >= 0.6 or octave_close) and not bpm_hint:
        warnings.append("tempo_uncertain")
    if len(beats) < 8:
        warnings.append("few_beats")
    bi = np.clip(((beats - t_off) * fps).round().astype(int), 0, len(full) - 1)

    def flux(i, w=20):
        """New harmonic energy at a beat: the chroma that rises from the 0.2 s before to the 0.2 s after. A chord
        change on the bar line adds new notes; a decaying chord or a repeated one does not (the older cosine
        change measure picked beat 2 on house music whose chords decay across the bar)."""
        a = C[max(0, i - w):i].mean(0) if i > 0 else np.zeros(C.shape[1])
        b = C[i:i + w].mean(0)
        return float(np.maximum(0.0, b - a).sum() / (a.sum() + b.sum() + 1e-9))
    inside = beats < (beats[-1] - 1.0 if len(beats) else 0)
    strengths = []
    for ph in range(4):
        sel = bi[ph::4][inside[ph::4]] if len(bi) else []
        if len(sel) == 0:
            strengths.append(0.0)
            continue
        lowv = np.mean([low[max(0, i - 3): i + 4].max() for i in sel])
        strengths.append(np.mean([flux(i) for i in sel]) / 0.03 + 2.0 * lowv)
    order = np.argsort(strengths)[::-1]
    ph = int(order[0])
    best, second = strengths[order[0]], strengths[order[1]]
    conf = float((best - second) / best) if best > 0 else 0.0
    grid_shift = 0.0
    bar_line = None
    if downbeat_hint is not None and len(beats):
        # The user's bar line always picks bar 1. It moves the found grid by itself only when that is clearly right:
        # the found beats sit half a beat off it (the grid locked onto the off-beats, as with an off-beat bass or open
        # hat), a real onset sits at the bar line and the moved grid lands on onsets at least half as strong as the
        # found grid's. A bar-1 time typed by ear or read a frame or two late is often 60 to 100 ms off, and moving a
        # right grid by that puts every beat on a 16th hat. Any other offset is reported (bar_line_off_grid) and
        # applied only when the user confirms it (move_grid). Measured on 291 grooves with the true tempo and the bar
        # line typed -100 to +100 ms off: no grid that plain nearest-beat bar 1 had right was broken, and with the bar
        # line within 40 ms 269 to 271 were right (239 without moving).
        t_h = max(0.0, float(downbeat_hint))
        per0 = float(np.median(np.diff(beats))) if len(beats) > 1 else 60.0 / bpm
        dur_ = len(x) / BEAT_SR
        o_ = low + full
        # a bar line before the found grid (bar 1 of a beatless intro, often the song start): extend the grid back to
        # it one period at a time, so the bar line is compared with a grid position, never with a beat seconds away
        # only over sound: a bar line in the silence before the first beat (a song with a short lead-in typed as
        # bar 1 at 0 s) means the first beat, never a grid position in the silence
        snd_ = np.flatnonzero(rms > 0.01 * float(rms.max())) if len(rms) and float(rms.max()) > 0 else np.zeros(0, int)
        lim_ = max(t_h, (float(snd_[0]) / fps + t_off - 0.05) if len(snd_) else t_h)
        if lim_ < float(beats[0]) - 0.5 * per0:
            k_ = int(np.floor((float(beats[0]) - lim_) / per0 + 0.5))
            beats = np.concatenate([float(beats[0]) - per0 * np.arange(k_, 0, -1), beats])
        need = max(HINT_NEAR_S, 0.15 * per0)
        j_ = int(np.argmin(np.abs(beats - t_h)))
        off_, t_s, onset_ = 0.0, t_h, False

        def onset_str(bt):
            ii = np.clip(((np.asarray(bt) - t_off) * fps).round().astype(int), 0, len(o_) - 1)
            return np.array([float(o_[max(0, i - 2):i + 3].max()) for i in ii])
        if abs(float(beats[j_]) - t_h) > need:
            # snap to the strongest onset within 60 ms (at most 1/8 of a beat) of the bar line: a peak, not the rim of
            # the window, and a real one (at least half the median onset at the found beats)
            fi = int(round((t_h - t_off) * fps))
            w_ = max(2, min(int(round(0.125 * lag)), int(round(HINT_SNAP_S * fps))))
            a_, b_ = max(0, fi - w_), min(len(o_), fi + w_ + 1)
            ref = float(np.median(onset_str(beats[(beats >= 0) & (beats < dur_)])))
            if b_ - a_ >= 3:
                seg_ = o_[a_:b_]
                i_ = int(np.argmax(seg_))
                g_ = a_ + i_
                peak = 0 < g_ < len(o_) - 1 and o_[g_] >= o_[g_ - 1] and o_[g_] >= o_[g_ + 1]
                if peak and float(seg_[i_]) >= HINT_PEAK * ref:
                    t_s, onset_ = g_ / fps + t_off, True
            j2 = int(np.argmin(np.abs(beats - t_s)))
            off_ = ((t_s - float(beats[j2]) + 0.5 * per0) % per0) - 0.5 * per0
            if abs(off_) <= need:
                off_ = 0.0
            elif onset_:
                inside = beats[(beats >= 0) & (beats < dur_)]
                found = float(np.mean(onset_str(inside)))
                moved = float(np.mean(onset_str(inside + off_)))
                half = abs(abs(off_) / per0 - 0.5) <= HINT_HALF_TOL
                if (move_grid or half) and moved >= HINT_SUPPORT * found:
                    grid_shift, off_ = off_, 0.0
            if move_grid and off_ and not grid_shift:
                # the user said the bar line is exact: move onto it (onto its onset when one is there)
                grid_shift, off_ = off_, 0.0
        if grid_shift:
            last0 = float(beats[-1])
            beats = beats + grid_shift
            head = []
            t_ = float(beats[0]) - per0
            while t_ >= max(0.0, first_onset - 0.05):
                head.insert(0, t_)
                t_ -= per0
            beats = np.concatenate([np.array(head), beats]) if head else beats
            # keep the grid as long as it was
            while float(beats[-1]) + per0 <= last0 + 0.5 * per0 and float(beats[-1]) + per0 < dur_:
                beats = np.append(beats, float(beats[-1]) + per0)
        j_ = int(np.argmin(np.abs(beats - (t_s if grid_shift else t_h))))
        # beats before 0 s (a grid extended back to the song start) are dropped; bar 1 keeps its place in the bar
        j_ -= int(np.sum(beats < 0))
        beats = beats[(beats >= 0) & (beats < dur_)]
        ph = j_ % 4
        conf = 1.0
        b1 = float(beats[j_]) if 0 <= j_ < len(beats) else float(beats[ph]) - 4 * per0
        bar_line = {"given_s": r3(t_h), "bar1_s": r3(max(0.0, b1)), "moved_s": r3(grid_shift), "off_grid_s": r3(off_),
                    "onset_at_bar_line": bool(onset_)}
        if grid_shift:
            warnings.append("grid_moved")
        elif off_ or abs(b1 - t_h) > need:
            warnings.append("bar_line_off_grid")
    elif best <= 0 or (best - second) < 0.2 * best:
        # measured on 24 drum loops and synthetic songs: below this margin the bar start was wrong about half the
        # time (the kick and chord changes do not mark beat 1 clearly)
        warnings.append("downbeat_phase_uncertain")
    downbeats = beats[ph::4]
    # sections on bars
    dur = len(x) / BEAT_SR
    bounds_idx, _ = foote_sections(C, E, list(downbeats) + ([float(beats[-1]) + 60 / bpm] if len(beats) else []), fps)
    starts = [float(downbeats[i]) for i in bounds_idx if i < len(downbeats)] if len(downbeats) else [first_onset]
    ends = starts[1:] + [min(dur, float(beats[-1]) + 60 / bpm) if len(beats) else dur]
    sec_feats, sections = [], []
    en = []
    n_fr = len(C)
    for a, b in zip(starts, ends):
        i = min(max(0, int(a * fps)), n_fr - 1)
        j = min(n_fr, max(i + 1, int(b * fps)))
        en.append(float(rms[i:j].mean()))
        sec_feats.append(np.concatenate([C[i:j].mean(0), E[i:j].mean(0) / 10]))
    emax = max(en) if en and max(en) > 0 else 1.0
    labels = []
    for si, f in enumerate(sec_feats):
        lab = None
        for sj in range(si):
            a, b = sec_feats[sj], f
            if float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9)) > 0.985:
                lab = labels[sj]
                break
        if lab is None:
            lab = chr(ord("A") + min(25, len(set(labels))))
        labels.append(lab)
        sections.append({"t0": r3(starts[si]), "t1": r3(ends[si]), "label": lab, "energy": round(en[si] / emax, 2)})
    return {"bpm": round(float(bpm), 2), "beats": [r3(b) for b in beats], "downbeats": [r3(b) for b in downbeats],
            "bar_beats": 4, "downbeat_conf": round(conf, 2), "first_onset": r3(first_onset), "sections": sections,
            "warnings": warnings, "rhythm_strength": round(strength, 3), "duration_s": r3(dur),
            "grid_shift_s": r3(grid_shift), "bar_line": bar_line}


NAME_BPM = re.compile(r"(?<!\d)(\d{2,3})[\s_.\-]*bpm", re.I)
LOOP_MAX_S = 40.0               # a file this short that is a whole number of bars is treated as a loop
LOOP_BARS = (4, 8, 16)
LOOP_TOL = 0.01                 # whole bars within this share
NAME_BPM_TOL = 0.03             # a tempo in the file name this far from the detected one is a mismatch
LOOP_NEAR = 0.08                # without a tempo in the name, a tempo this close that makes whole bars is suspicious


def name_bpm(name):
    """The tempo a file name states ("Loop 100 BPM.wav", "groove_128bpm"), or None."""
    mt = NAME_BPM.search(os.path.splitext(os.path.basename(name or ""))[0])
    if not mt:
        return None
    v = float(mt.group(1))
    return v if 40.0 <= v <= 250.0 else None


def whole_bars(dur, bpm, tol=LOOP_TOL):
    """4, 8 or 16 when dur seconds is that many 4/4 bars at bpm (within tol), else None."""
    if not dur or not bpm:
        return None
    bars = float(dur) * float(bpm) / 240.0
    for b in LOOP_BARS:
        if abs(bars - b) <= tol * b:
            return b
    return None


def shift_downbeats(beats, downbeats, n):
    """The downbeats moved by n beats (n may be negative) along the beat list."""
    if not n or not beats or not downbeats:
        return downbeats
    b = np.asarray(beats, np.float64)
    j = int(np.argmin(np.abs(b - float(downbeats[0]))))
    ph = (j + int(n)) % 4
    return [r3(x) for x in b[ph::4]]


BAR_LOW_HZ = (30.0, 120.0)        # bar_low_db: the kick and bass band of each bar


def bar_levels(x, sr, downbeats, dur=None):
    """(bar_db, bar_low_db): the RMS level in dBFS of every bar (from each downbeat to the next; the last bar runs one
    median bar long, cut at the end of the audio), over the whole band and over 30 to 120 Hz (the low end that
    carries a section's weight). A quiet bar is where the music thins: a breakdown, a filtered build or an outro."""
    x = np.asarray(x, np.float64)
    if x.ndim > 1:
        x = x.mean(axis=1)
    n = len(x)
    db = [float(v) for v in downbeats or []]
    if not db or n == 0:
        return [], []
    end = float(dur) if dur else n / float(sr)
    bar = float(np.median(np.diff(db))) if len(db) > 1 else 4 * 0.5
    lv, lo = [], []
    for i, t0 in enumerate(db):
        t1 = db[i + 1] if i + 1 < len(db) else min(end, t0 + bar)
        a, b = int(round(t0 * sr)), int(round(min(t1, end) * sr))
        a, b = max(0, a), min(n, b)
        if b - a < int(0.05 * sr):
            lv.append(None)
            lo.append(None)
            continue
        seg = x[a:b]
        lv.append(round(10 * math.log10(float(np.mean(seg * seg)) + 1e-12), 1))
        sp = np.fft.rfft(seg)
        fr = np.fft.rfftfreq(len(seg), 1.0 / sr)
        band = (fr >= BAR_LOW_HZ[0]) & (fr <= BAR_LOW_HZ[1])
        pw = 2.0 * float(np.sum(np.abs(sp[band]) ** 2)) / float(len(seg)) ** 2
        lo.append(round(10 * math.log10(pw + 1e-12), 1))
    return lv, lo


def quiet_bars(doc, n=3, below_db=6.0):
    """[(bar number from 1, start s, dB under the median bar)] of the quietest bars at least below_db under it."""
    lv = doc.get("bar_db") or []
    downs = doc.get("downbeats") or []
    vals = [v for v in lv if v is not None]
    if len(vals) < 4:
        return []
    med = float(np.median(vals))
    rows = [(i + 1, float(downs[i]) if i < len(downs) else None, round(med - v, 1)) for i, v in enumerate(lv)
            if v is not None and med - v >= below_db]
    return sorted(rows, key=lambda r: -r[2])[:n]


def step_beats(lab, m, opt):
    if not m.get("audio") or not (m["kind"] == "audio" or m.get("music")) or m.get("role") == "voiceover":
        m["analysis"]["beats"] = None
        m["summary"]["bpm"] = None
        return "skip"
    ensure_audio(lab, m)
    x = decode_audio(lab.absp(m["wav"]), BEAT_SR, channels=1)[:, 0]
    off = float((m.get("audio") or {}).get("offset_s") or 0.0)
    hints = m.get("beat_hints") or {}
    dh = hints.get("first_downbeat_s")
    out = lab.p("media", "analysis", m["id"] + ".beats.json")
    prev = load_json(out) if os.path.exists(out) else {}
    an = analyse_music(x, bpm_hint=hints.get("bpm"), downbeat_hint=(float(dh) - off) if dh is not None else None,
                       move_grid=bool(hints.get("move_grid")))
    dur = float(m.get("duration_s") or an["duration_s"] or 0.0)
    nb = name_bpm(m.get("name"))
    flags, loop_bars = [], None
    if hints.get("bpm"):
        detected, bpm_from = prev.get("detected_bpm"), "user"
    else:
        detected, bpm_from = an["bpm"], "detected"
        lb = whole_bars(dur, nb) if nb and dur <= LOOP_MAX_S else None
        if lb:
            # the name states a tempo and the file is a whole number of bars at it (a loop): that tempo is the truth.
            # Measured grids drift on loops (a 100 BPM loop measured 111) or lock onto the off-beats; the loop starts
            # on bar 1, so bar 1 goes on the first strong onset unless the user gave one
            # the first strong sound: an onset on the file's first frame (a loop that starts on its kick) has no frame
            # before it, so the onset measure misses it and finds a later hit
            pk = float(np.abs(x).max()) if len(x) else 0.0
            loud = np.flatnonzero(np.abs(x) >= 0.25 * pk) if pk > 0 else np.zeros(0, int)
            t_snd = float(loud[0]) / BEAT_SR if len(loud) else float(an["first_onset"])
            dh2 = (float(dh) - off) if dh is not None else min(float(an["first_onset"]), t_snd)
            an = analyse_music(x, bpm_hint=nb, downbeat_hint=dh2, move_grid=bool(hints.get("move_grid")))
            # the tracker may still settle up to 3 % off the hint on a loose groove; a loop that is whole bars at the
            # stated tempo IS that tempo: lay the grid exactly, bar 1 on the chosen onset
            per = 60.0 / nb
            t1 = dh2
            while t1 - per >= -0.02:
                t1 -= per
            grid = [r3(max(0.0, t1 + k * per)) for k in range(int((an["duration_s"] - t1) / per) + 1)
                    if t1 + k * per < an["duration_s"] - 0.01]
            j1 = int(np.argmin(np.abs(np.asarray(grid) - dh2))) if grid else 0
            an["beats"], an["downbeats"], an["bpm"] = grid, grid[j1 % 4::4], round(nb, 2)
            an["downbeat_conf"] = 1.0
            if dh is None:
                an.pop("bar_line", None)
                an["warnings"] = [w for w in an["warnings"] if w not in ("grid_moved", "bar_line_off_grid")]
            an["warnings"] = [w for w in an["warnings"] if w not in ("tempo_uncertain", "downbeat_phase_uncertain")]
            bpm_from, loop_bars = "name+loop", lb
            flags.append("bpm_from_name")
    if nb and detected and abs(float(detected) - nb) / nb > NAME_BPM_TOL:
        flags.append("bpm_name_mismatch")
    if dur and dur <= LOOP_MAX_S:
        if whole_bars(dur, an["bpm"]):
            loop_bars = loop_bars or whole_bars(dur, an["bpm"])
        else:
            near = [nb] if nb else [bars * 240.0 / dur for bars in LOOP_BARS
                                    if abs(bars * 240.0 / dur - an["bpm"]) / an["bpm"] <= LOOP_NEAR]
            fit = [t for t in near if whole_bars(dur, t)]
            if fit:
                flags.append("loop_bars_off")
                loop_bars = whole_bars(dur, fit[0])
    # a tempo the name states and the loop length confirms is certain, however loose the groove measures
    if bpm_from != "name+loop" and ("tempo_uncertain" in an["warnings"] or an["rhythm_strength"] < 0.1
                                    or an["downbeat_conf"] < 0.1):
        flags.append("tempo_uncertain")
    if off:
        for k in ("beats", "downbeats"):
            an[k] = [r3(b + off) for b in an[k]]
        an["first_onset"] = r3(an["first_onset"] + off)
        for s in an["sections"]:
            s["t0"], s["t1"] = r3(s["t0"] + off), r3(s["t1"] + off)
        if an.get("bar_line"):
            for k in ("given_s", "bar1_s"):
                an["bar_line"][k] = r3(an["bar_line"][k] + off)
    sb = int(hints.get("shift_bar") or 0)
    if sb:
        # the user heard bar 1 N beats off on the click file: every downbeat moves N beats along the grid
        an["downbeats"] = shift_downbeats(an["beats"], an["downbeats"], sb)
    # the level of every bar (whole band and 30 to 120 Hz), so a cutter sees where a section thins
    bar_db, bar_low_db = bar_levels(x, BEAT_SR, [d - off for d in an["downbeats"]], an["duration_s"])
    doc = {"schema": "resolve-editor/beats@1", "media": m["id"], "duration_s": an["duration_s"], "bpm": an["bpm"],
           "bar_db": bar_db, "bar_low_db": bar_low_db,
           "beats": an["beats"], "downbeats": an["downbeats"], "bar_beats": 4, "downbeat_conf": an["downbeat_conf"],
           "first_onset": an["first_onset"], "sections": an["sections"], "warnings": an["warnings"],
           "rhythm_strength": an["rhythm_strength"], "flags": sorted(set(flags)),
           "detected_bpm": None if detected is None else round(float(detected), 2), "name_bpm": nb,
           "loop_bars": loop_bars, "bpm_from": bpm_from}
    if hints:
        doc["hints"] = hints
        if an.get("grid_shift_s"):
            # the found grid sat half a beat off the bar line the user gave (on the off-beats), or the user confirmed
            # the bar line with move_grid: it was moved onto it
            doc["grid_shift_s"] = an["grid_shift_s"]
    if an.get("bar_line"):
        doc["bar_line"] = an["bar_line"]
    write_json(out, doc)
    m["analysis"]["beats"] = lab.rel(out)
    m["summary"]["bpm"] = an["bpm"]
    # what the bar line did to the grid: shown by beats, ingest and status, so the user confirms it by ear
    if an.get("bar_line"):
        m["bar_line"] = an["bar_line"]
    else:
        m.pop("bar_line", None)
    add_flag(m, "grid_moved", "grid_moved" in an["warnings"])
    add_flag(m, "bar_line_off_grid", "bar_line_off_grid" in an["warnings"])
    words = m["summary"].get("words") or 0
    dur = m.get("duration_s") or an["duration_s"] or 1
    if m.get("music") or (m["kind"] == "audio" and an["rhythm_strength"] >= 0.2 and words < 0.5 * dur) \
            or bpm_from == "name+loop":
        add_flag(m, "music")
    # tempo and bar 1 doubts matter for music only (a voice-over gets a beat analysis too, but nobody cuts to it)
    mus = "music" in (m["summary"].get("flags") or [])
    add_flag(m, "tempo_uncertain", mus and "tempo_uncertain" in flags)
    add_flag(m, "bar1_uncertain", mus and "downbeat_phase_uncertain" in an["warnings"])
    for fl in ("bpm_name_mismatch", "loop_bars_off", "bpm_from_name"):
        add_flag(m, fl, (mus or bool(nb)) and fl in flags)
    return "ok"


def click_track(music, sr, beats, downbeats, t0, dur):
    """The music at -6 dB with a 20 ms click on every beat: a soft 1 kHz one, a louder 2 kHz one on bar 1."""
    n = int(round(dur * sr))
    y = np.zeros((n, 2), np.float32)
    k = min(n, len(music))
    y[:k] = music[:k] * np.float32(10 ** (-6 / 20.0))
    cn = int(0.020 * sr)
    tt = np.arange(cn) / float(sr)
    env = np.minimum(1.0, tt / 0.001) * np.exp(-tt / 0.006)
    soft = (0.25 * np.sin(2 * np.pi * 1000 * tt) * env).astype(np.float32)
    loud = (0.70 * np.sin(2 * np.pi * 2000 * tt) * env).astype(np.float32)
    down = np.asarray(downbeats, np.float64)
    placed = []
    for b in beats:
        if not t0 - 1e-6 <= float(b) < t0 + dur:
            continue
        i = int(round((float(b) - t0) * sr))
        is_down = bool(len(down)) and float(np.min(np.abs(down - float(b)))) < 0.002
        c = loud if is_down else soft
        j = min(n, i + cn)
        y[i:j] += c[:j - i, None]
        placed.append((round(float(b) - t0, 4), is_down))
    return np.clip(y, -1, 1), placed


def cmd_click(lab, mid, from_s=None, dur=None, out=None):
    m = lab.get(mid)
    bt = None
    rel = (m.get("analysis") or {}).get("beats")
    if rel and os.path.exists(lab.absp(rel)):
        bt = load_json(lab.absp(rel))
    if not bt or not bt.get("beats"):
        raise Fail("%s has no beat grid; run beats --only %s first" % (mid, mid))
    wav = (m["wav"] if os.path.isabs(m["wav"]) else lab.absp(m["wav"])) if m.get("wav") else None
    if not wav or not os.path.exists(wav):
        raise Fail("%s has no lab WAV; run proxies --only %s first" % (mid, mid))
    downs = bt.get("downbeats") or []
    t0 = float(from_s) if from_s is not None else float(downs[0] if downs else bt["beats"][0])
    total = float(m.get("duration_s") or bt.get("duration_s") or 0.0)
    d = float(dur) if dur is not None else 30.0
    if total:
        d = max(0.5, min(d, total - t0))
    if d <= 0:
        raise Fail("--from %.3f is past the end of %s (%.2f s)" % (t0, mid, total))
    off = float((m.get("audio") or {}).get("offset_s") or 0.0)
    sr = 48000
    music = decode_audio(wav, sr, channels=2, start=max(0.0, t0 - off), dur=d)
    y, placed = click_track(music, sr, bt["beats"], downs, t0, d)
    out = out or lab.p("media", "analysis", mid + ".click.wav")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    import wave
    with wave.open(out, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((y * 32767).astype("<i2").tobytes())
    print("CLICK %s: %.1f s from %.3f s, %d beats, %d bars, %.2f BPM (%s)" % (
        mid, d, t0, len(placed), sum(1 for _, dn in placed if dn), float(bt.get("bpm") or 0),
        bt.get("bpm_from") or "detected"))
    print("WROTE %s" % posix(out))
    print("QUESTION: Does the high click land on beat 1 of each bar: yes, it drifts, or N beats early or late?")
    print("  it drifts: media_lab.py LAB beats --only %s --bpm TEMPO; N beats early or late: media_lab.py LAB beats "
          "--only %s --shift-bar N (N later is +N)" % (mid, mid))
    return out


def bar_line_note(m):
    """One plain line on what the user's bar-1 time did to the beat grid (moved it, or was off the found beats), or
    None when the bar line sat on the grid."""
    bl = m.get("bar_line") or {}
    fl = set(m["summary"].get("flags") or [])
    mid = m["id"]
    if not bl or not fl & {"grid_moved", "bar_line_off_grid"}:
        return None
    g, b1 = float(bl.get("given_s") or 0), float(bl.get("bar1_s") or 0)
    if "grid_moved" in fl:
        return ("%s: the whole beat grid moved by %+d ms onto your bar line at %.3f s (bar 1 is now at %.3f s)%s; play "
                "the music against the beats to confirm" % (
                    mid, round(1000 * float(bl.get("moved_s") or 0)), g, b1,
                    "" if (m.get("beat_hints") or {}).get("move_grid") else
                    ": the found beats sat half a beat off it, on the off-beats"))
    d = round(1000 * (g - b1))
    if bl.get("onset_at_bar_line"):
        return ("%s: your bar line at %.3f s is %d ms %s the nearest found beat, and a strong onset sits at your time. "
                "The grid was kept and bar 1 is the beat at %.3f s. Play the music against the beats: if they sound "
                "right, nothing to do; if your time is exact (read on the waveform, not typed by ear), run beats --only "
                "%s --first-downbeat %.3f --move-grid" % (mid, g, abs(d), "after" if d > 0 else "before", b1, mid, g))
    return ("%s: no strong onset lies at your bar line %.3f s; bar 1 is the nearest found beat at %.3f s (%d ms %s). "
            "Play the music against the beats to confirm" % (mid, g, b1, abs(d), "earlier" if d > 0 else "later"))


# ---------------------------------------------------------------- per-shot technical quality
_LAP = ((0, 1, 0), (1, -4, 1), (0, 1, 0))
_IMM = ((1, -2, 1), (-2, 4, -2), (1, -2, 1))


def conv3(img, k):
    p = np.pad(img, 1, mode="edge")
    H, W = img.shape
    out = np.zeros_like(img)
    for i in range(3):
        for j in range(3):
            if k[i][j]:
                out += k[i][j] * p[i:i + H, j:j + W]
    return out


def noise_sigma(f):
    """Immerkaer (1996) fast noise estimate on the flatter half of the frame (8-bit code values)."""
    r = conv3(f, _IMM)[1:-1, 1:-1]
    g = np.abs(conv3(f, _LAP))[1:-1, 1:-1]
    msk = g <= np.percentile(g, 50)
    return float(np.sqrt(np.pi / 2) * np.abs(r[msk]).mean() / 6) if msk.any() else 0.0


def phase_shift(a, b, win):
    A = np.fft.rfft2(a * win)
    B = np.fft.rfft2(b * win)
    R = A * np.conj(B)
    R /= np.abs(R) + 1e-9
    r = np.fft.irfft2(R, s=a.shape)
    k = np.unravel_index(int(np.argmax(r)), r.shape)
    H, W = a.shape
    dy = k[0] if k[0] <= H // 2 else k[0] - H
    dx = k[1] if k[1] <= W // 2 else k[1] - W
    y, x = k

    def sub(vm, v0, vp):
        d = vm - 2 * v0 + vp
        return 0.5 * (vm - vp) / d if abs(d) > 1e-9 else 0.0
    dx += sub(r[y, (x - 1) % W], r[y, x], r[y, (x + 1) % W])
    dy += sub(r[(y - 1) % H, x], r[y, x], r[(y + 1) % H, x])
    return float(dx), float(dy)


def shake_metrics(d, eff_fps, width):
    """d: (n, 2) content motion per sample step. Jitter = RMS of (camera path - 0.5 s moving average), median of
    1 s windows, in % of frame width; pan = median smoothed speed in % of width per second."""
    if len(d) < 3:
        return 0.0, 0.0
    path = np.cumsum(d, axis=0)
    k = max(3, int(round(eff_fps * 0.5)) | 1)
    ker = np.ones(k) / k
    sm = np.stack([np.convolve(np.pad(path[:, i], k // 2, mode="edge"), ker, "valid") for i in range(2)], 1)
    e = np.sqrt(((path - sm) ** 2).sum(1))
    win = max(5, int(eff_fps))
    if len(e) <= win:
        wins = [np.sqrt((e ** 2).mean())]
    else:
        wins = [np.sqrt((e[i:i + win] ** 2).mean()) for i in range(0, len(e) - win + 1, max(1, win // 2))]
    vel = np.diff(sm, axis=0) * eff_fps
    pan = float(np.median(np.sqrt((vel ** 2).sum(1)))) if len(vel) else 0.0
    return float(np.median(wins) / width * 100), pan / width * 100


def step_quality(lab, m, opt):
    if m["kind"] not in ("av", "video"):
        return "skip"
    sh = load_shots(lab, m)
    if not sh:
        raise Fail("no shots yet; run shots first")
    shots = sh["shots"]
    fps = float(parse_fps(m["fps"]))
    src = frame_source(lab, m)
    sid = np.zeros(max(s["out"] for s in shots) + 1, int)
    for k, s in enumerate(shots):
        sid[s["in"]:s["out"]] = k
    ns = len(shots)
    # sharpness, noise and exposure at 640x360 (360x640 portrait), about 5 frames per second
    every = max(1, int(round(fps / 5)))
    W, H = proxy_dims(m, 360)
    sharp = [[] for _ in range(ns)]
    noise = [[] for _ in range(ns)]
    hist = np.zeros((ns, 256), np.int64)
    for i0, arr in decode_frames(src, W, H, "gray", every=every, batch=64):
        for j, f in enumerate(arr):
            idx = min(i0 + j * every, len(sid) - 1)
            k = sid[idx]
            ff = f.astype(np.float32)
            sharp[k].append(float(conv3(ff, _LAP).var()))
            noise[k].append(noise_sigma(ff))
            hist[k] += np.bincount(f.ravel(), minlength=256)
    # camera motion at 320x180 on every frame (every second frame above 30 fps)
    ev2 = max(1, int(round(fps / 25)))
    qW, qH = proxy_dims(m, 180)
    win = np.outer(np.hanning(qH), np.hanning(qW)).astype(np.float32)
    steps = [[] for _ in range(ns)]
    prev, prev_idx = None, None
    for i0, arr in decode_frames(src, qW, qH, "gray", every=ev2, batch=128):
        for j, f in enumerate(arr):
            idx = min(i0 + j * ev2, len(sid) - 1)
            ff = f.astype(np.float32)
            if prev is not None and sid[idx] == sid[prev_idx]:
                steps[sid[idx]].append(phase_shift(ff, prev, win))
            prev, prev_idx = ff, idx
    res = {}
    sharp_med = [float(np.median(x)) if x else 0.0 for x in sharp]
    smax = max(sharp_med) if sharp_med and max(sharp_med) > 0 else 1.0
    flat_count = 0
    for k, s in enumerate(shots):
        h = hist[k]
        tot = h.sum()
        if tot:
            c = np.cumsum(h) / tot
            p1, p50, p99 = [int(np.searchsorted(c, q)) for q in (0.01, 0.5, 0.99)]
        else:
            p1 = p50 = p99 = None
        jit, pan = shake_metrics(np.array(steps[k]), fps / ev2, qW) if steps[k] else (0.0, 0.0)
        rel = sharp_med[k] / smax
        flags = []
        if ns > 1 and rel < 0.4:
            flags.append("soft")
        if jit > 0.3:
            flags.append("shaky")
        if jit > 0.8:
            flags.append("very_shaky")
        if p50 is not None:
            if p50 < 35 or p99 < 70:
                flags.append("dark")
            if p50 > 200 or p1 > 170:
                flags.append("bright")
            if p1 > 20 and p99 < 200:
                flags.append("flat")
                flat_count += 1
        res[s["id"]] = {"sharpness": round(sharp_med[k], 1), "sharp_rel": round(rel, 2),
                        "noise": round(float(np.median(noise[k])), 2) if noise[k] else None,
                        "jitter_pct": round(jit, 3), "pan_speed_pct_s": round(pan, 2),
                        "luma_p1": p1, "luma_p50": p50, "luma_p99": p99, "flags": flags}
    out = lab.p("media", "analysis", m["id"] + ".quality.json")
    write_json(out, {"schema": "resolve-editor/quality@1", "media": m["id"],
                     "method": "laplacian variance and noise at 360p 5 fps; phase correlation jitter at 180p",
                     "shots": res})
    m["analysis"]["quality"] = lab.rel(out)
    flat = flat_count > ns / 2
    add_flag(m, "flat_log", flat)
    if m.get("color") is not None:
        m["color"]["flat_log_guess"] = bool(flat or re.search(r"log", m["color"].get("transfer") or "", re.I))
    return "ok"


def load_quality(lab, m):
    rel = (m.get("analysis") or {}).get("quality")
    if not rel or not os.path.exists(lab.absp(rel)):
        return None
    return load_json(lab.absp(rel))


# ---------------------------------------------------------------- contact sheets
def pil_font(px):
    from PIL import ImageFont
    try:
        return ImageFont.load_default(size=px)
    except TypeError:
        return ImageFont.load_default()


def view_stretch(frames):
    """For viewing only: when the frames look flat (p99 luma under 200 or p1 over 20), stretch p1..p99 to 16..235."""
    a = np.stack([np.asarray(f, np.float32) for f in frames])
    y = a[..., 0] * 0.2126 + a[..., 1] * 0.7152 + a[..., 2] * 0.0722
    p1, p99 = np.percentile(y, 1), np.percentile(y, 99)
    if not (p99 < 200 or p1 > 20) or p99 - p1 < 8:
        return frames, False
    out = np.clip((a - p1) * (219.0 / (p99 - p1)) + 16, 0, 255).astype(np.uint8)
    return list(out), True


def grab_frames(src, idxs, w, h):
    """{frame index: RGB array (h, w, 3)} for the wanted frame indices of one file, in one streaming decode."""
    want = set(int(i) for i in idxs)
    got = {}
    if not want:
        return got
    last = max(want)
    for i0, arr in decode_frames(src, w, h, "rgb24", batch=64):
        for j in range(len(arr)):
            if i0 + j in want:
                got[i0 + j] = arr[j].copy()
        if i0 + len(arr) > last:
            break
    return got


def load_still(m, width):
    """A still image as RGB: Pillow first, then one ffmpeg decode (HEIC, EXR, DPX), else a grey placeholder."""
    from PIL import Image
    try:
        with Image.open(m["path"]) as im:
            if im.mode in ("RGBA", "LA", "PA") or "transparency" in im.info:
                # transparency shows as a grey checkerboard (the pixels under it are not what Resolve shows)
                rgba = im.convert("RGBA")
                w_, h_ = rgba.size
                q = max(4, min(w_, h_) // 16)
                yy, xx = np.mgrid[0:h_, 0:w_]
                chk = np.where(((yy // q) + (xx // q)) % 2 == 0, 90, 60).astype(np.uint8)
                bg = Image.fromarray(np.stack([chk] * 3, 2), "RGB")
                bg.paste(rgba, mask=rgba.getchannel("A"))
                return bg
            return im.convert("RGB")
    except Exception:
        pass
    w = width
    h = int(round(width * (m.get("height") or 9) / max(1, m.get("width") or 16) / 2)) * 2 or 108
    try:
        raw = run(ff_base() + ["-i", m["path"], "-frames:v", "1", "-vf", "scale=%d:%d" % (w, h), "-f", "rawvideo",
                               "-pix_fmt", "rgb24", "-"], text=False, timeout=60).stdout
        if len(raw) >= w * h * 3:
            return Image.frombuffer("RGB", (w, h), raw[: w * h * 3])
    except Fail:
        pass
    return Image.new("RGB", (w, h), (60, 60, 60))


def edit_shape(lab):
    """(width, height) of the edit from project.json when it is vertical (a 9:16 or 4:5 piece), else None."""
    try:
        tl = (load_json(lab.p("project.json")) or {}).get("timeline") or {}
        w, h = int(tl.get("width") or 0), int(tl.get("height") or 0)
    except (Fail, OSError, ValueError, TypeError):
        return None
    return (w, h) if w > 0 and h > w else None


def draw_crop_guide(d, x0, y0, cw, ch, frac):
    """The centre crop a vertical edit shows of a wider frame (two dashed cyan lines) and a frame_x ruler (ticks at
    every tenth of the width, longer at the quarters and the middle) along the top of one frame."""
    for xf in ((1 - frac) / 2, (1 + frac) / 2):
        x = x0 + int(round(xf * (cw - 1)))
        for y in range(y0, y0 + ch, 8):
            d.rectangle([x - 1, y, x, min(y0 + ch - 1, y + 4)], fill=(0, 230, 255))
            d.rectangle([x - 1, min(y0 + ch - 1, y + 5), x, min(y0 + ch - 1, y + 7)], fill=(0, 0, 0))
    for k in range(11):
        x = min(x0 + cw - 2, max(x0 + 1, x0 + int(round(k / 10.0 * (cw - 1)))))
        ln = 9 if k in (0, 5, 10) else 6
        d.rectangle([x - 1, y0, x + 1, y0 + ln], fill=(0, 0, 0))
        d.line([(x, y0), (x, y0 + ln - 1)], fill=(0, 230, 255))


def build_sheet_rows(lab, items, layout):
    """items: list of (kind, media, shot or None). Returns (rows as PIL images, row labels ids, stretched ids).

    For a vertical edit every frame wider than the edit shows the centre crop (dashed cyan lines) and a ruler of
    tenths along its top edge, so a logger or cutter can read the subject's frame_x."""
    from PIL import Image, ImageDraw
    shape = edit_shape(lab)
    if layout == "log":
        cw, fracs, per_row, fpx = 192, (0.1, 0.3, 0.5, 0.7, 0.9), 2, 12
    else:
        cw, fracs, per_row, fpx = 320, (0.125, 0.375, 0.625, 0.875), 1, 14
    gap, lab_h = 4, fpx + 6
    font = pil_font(fpx)
    by_media = {}
    for kind, m, s in items:
        by_media.setdefault(m["id"], []).append((kind, m, s))
    strips = {}
    stretched = []
    for mid, lst in by_media.items():
        m = lst[0][1]
        if m["kind"] == "image":
            img = load_still(m, cw)
            ch = int(round(cw * img.height / max(1, img.width)))
            ch = min(ch, cw * 2)
            img = img.resize((cw, ch))
            strip = Image.new("RGB", (cw * len(fracs) + gap * (len(fracs) - 1), ch + lab_h), (24, 24, 24))
            strip.paste(img, (0, lab_h))
            d = ImageDraw.Draw(strip)
            d.rectangle([0, 0, strip.width, lab_h - 1], fill=(0, 0, 0))
            d.text((4, 2), "%s  %s  image" % (m["id"], m["name"]), font=font, fill=(255, 255, 0))
            strips[m["id"]] = strip
            continue
        W, H = proxy_dims(m, 360)
        ch = int(round(cw * H / W / 2)) * 2
        fps = float(parse_fps(m["fps"]))
        want = {}
        for kind, _, s in lst:
            n = s["out"] - s["in"]
            want[s["id"]] = [min(s["out"] - 1, s["in"] + int(f * n)) for f in fracs]
        frames = grab_frames(frame_source(lab, m), [i for v in want.values() for i in v], cw, ch)
        for kind, _, s in lst:
            fr = [frames.get(i) for i in want[s["id"]]]
            fr = [f if f is not None else np.full((ch, cw, 3), 40, np.uint8) for f in fr]
            fr, st = view_stretch(fr)
            if st:
                stretched.append(s["id"])
            strip = Image.new("RGB", (cw * len(fracs) + gap * (len(fracs) - 1), ch + lab_h), (24, 24, 24))
            for k, f in enumerate(fr):
                strip.paste(Image.fromarray(np.asarray(f, np.uint8)), (k * (cw + gap), lab_h))
            d = ImageDraw.Draw(strip)
            crop = (shape[0] / float(shape[1])) / (W / float(H)) if shape else 1.0
            if crop < 0.95:
                for k in range(len(fr)):
                    draw_crop_guide(d, k * (cw + gap), lab_h, cw, ch, crop)
            d.rectangle([0, 0, strip.width, lab_h - 1], fill=(0, 0, 0))
            a, b = s["in"] / fps, s["out"] / fps
            txt = "%s  %s  %.2f-%.2f (%.2f s)%s%s" % (s["id"], m["name"], a, b, b - a, "  contrast stretched" if st else "",
                                                  ("  crop %d %%" % round(100 * crop)) if crop < 0.95 else "")
            d.text((4, 2), txt, font=font, fill=(255, 255, 0))
            strips[s["id"]] = strip
    order = [(s["id"] if s else m["id"]) for kind, m, s in items]
    return [(k, strips[k]) for k in order if k in strips], per_row, stretched


def pack_sheets(strips, per_row, out_dir, prefix, start_no=1, quality=85):
    from PIL import Image
    gap = 6
    rows = [strips[i:i + per_row] for i in range(0, len(strips), per_row)]
    sheets, cur, cur_h = [], [], 0
    for r in rows:
        rh = max(s.height for _, s in r)
        add_h = rh + (gap if cur else 0)
        if cur and cur_h + add_h > MAX_SIDE:
            sheets.append(cur)
            cur, cur_h = [], 0
            add_h = rh
        cur.append(r)
        cur_h += add_h
    if cur:
        sheets.append(cur)
    out = []
    no = start_no
    for rows_ in sheets:
        rw = max(sum(s.width for _, s in r) + gap * (len(r) - 1) for r in rows_)
        W = min(MAX_SIDE, max(rw, 1))
        H = sum(max(s.height for _, s in r) for r in rows_) + gap * (len(rows_) - 1)
        img = Image.new("RGB", (W, min(MAX_SIDE, H)), (255, 255, 255))
        y = 0
        ids = []
        for r in rows_:
            x = 0
            for sid, s in r:
                img.paste(s, (x, y))
                x += s.width + gap
                ids.append(sid)
            y += max(s.height for _, s in r) + gap
        if img.width > MAX_SIDE or img.height > MAX_SIDE:
            img.thumbnail((MAX_SIDE, MAX_SIDE))
        path = os.path.join(out_dir, "%s_%02d.jpg" % (prefix, no))
        tmp = path + ".tmp.jpg"
        img.save(tmp, quality=quality)
        os.replace(tmp, path)
        out.append({"path": path, "size": [img.width, img.height],
                     "tokens": math.ceil(img.width / 28) * math.ceil(img.height / 28), "shots": ids})
        no += 1
    return out


def sheet_items(lab, ids=None):
    items = []
    for mid, m in lab.index["media"].items():
        if ids and mid not in ids:
            continue
        if m["kind"] == "image":
            items.append(("image", m, None))
            continue
        sh = load_shots(lab, m)
        if sh:
            for s in sh["shots"]:
                items.append(("shot", m, s))
    return items


def cmd_sheets_log(lab, force=False, only=None, out_dir=None):
    ids = lab.media(only) if only else None
    items = sheet_items(lab, ids)
    if not items:
        raise Fail("no shots to show yet; run ingest (or shots) first")
    default = out_dir is None
    out_dir = out_dir or lab.p("media", "sheets")
    os.makedirs(out_dir, exist_ok=True)
    js_path = os.path.join(out_dir, "sheets.json")
    parts = {}
    for _, m, _s in items:
        if m["id"] not in parts:
            rel = (m.get("analysis") or {}).get("shots")
            with open(lab.absp(rel) if rel else m["path"], "rb") as fh:
                content = hashlib.sha1(fh.read() if rel else b"image").hexdigest()
            parts[m["id"]] = (m["hash"], m["kind"], content)
    sig = step_sig("sheets", STEP_VERSION["sheets"], sorted(parts.items()), ids, edit_shape(lab))
    if default and not force and os.path.exists(js_path):
        old = load_json(js_path)
        if old.get("sig") == sig and all(os.path.exists(lab.absp(s["path"])) for s in old.get("sheets", [])):
            return js_path, False
    for f in glob.glob(os.path.join(out_dir, "log_*.jpg")):
        os.remove(f)
    strips, per_row, stretched = build_sheet_rows(lab, items, "log")
    sheets = pack_sheets(strips, per_row, out_dir, "log")
    for s in sheets:
        s["path"] = lab.rel(s["path"]) if default else posix(s["path"])
    write_json(js_path, {"schema": "resolve-editor/sheets@1", "layout": "log", "sheets": sheets,
                         "stretched": stretched, "sig": sig})
    return js_path, True


def cmd_sheets_zoom(lab, shots, out_dir=None):
    if not shots:
        raise Fail("zoom sheets need --only SHOT,... (shot ids such as CLIP.s02; see shotlist)")
    want = [x.strip() for x in shots.split(",") if x.strip()]
    items = []
    for sid in want:
        mid = sid.rsplit(".s", 1)[0]
        m = lab.index["media"].get(mid)
        sh = load_shots(lab, m) if m else None
        s = next((x for x in (sh or {}).get("shots", []) if x["id"] == sid), None)
        if s is None:
            raise Fail("unknown shot id %s; see shotlist" % sid)
        items.append(("shot", m, s))
    out_dir = os.path.abspath(out_dir) if out_dir else lab.p("media", "sheets")
    os.makedirs(out_dir, exist_ok=True)
    nums = [int(x) for x in re.findall(r"zoom_(\d+)\.jpg", " ".join(os.listdir(out_dir)))]
    strips, per_row, stretched = build_sheet_rows(lab, items, "zoom")
    sheets = pack_sheets(strips, per_row, out_dir, "zoom", start_no=(max(nums) + 1) if nums else 1)
    for s in sheets:
        s["path"] = posix(s["path"])
    js_path = os.path.join(out_dir, "zoom.json")
    old = load_json(js_path) if os.path.exists(js_path) else {"schema": "resolve-editor/sheets@1", "layout": "zoom", "sheets": []}
    old["sheets"] += sheets
    write_json(js_path, old)
    return js_path, sheets


# ---------------------------------------------------------------- shot log (loggers write parts, the lab checks and merges)
SIZES = {"ECU", "CU", "MCU", "MS", "MWS", "WS", "EWS", "insert", "screen", "other"}
MOVES = {"static", "pan_l", "pan_r", "tilt_u", "tilt_d", "push", "pull", "handheld", "arc", "slide", "track", "zoom", "other"}


def known_shots(lab):
    ids = {}
    for mid, m in lab.index["media"].items():
        sh = load_shots(lab, m)
        for s in (sh or {}).get("shots", []):
            ids[s["id"]] = mid
    return ids


def _frac(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and 0.0 <= x <= 1.0


def check_shotlog(doc, known):
    errs, warns = [], []
    if not isinstance(doc, dict) or not isinstance(doc.get("shots"), list):
        return ["the file needs {\"schema\": \"resolve-editor/shotlog@1\", \"by\": ..., \"shots\": [...]}"], []
    if doc.get("schema") != "resolve-editor/shotlog@1":
        warns.append("schema should be resolve-editor/shotlog@1")
    seen = set()
    for k, s in enumerate(doc["shots"]):
        where = "shots[%d]" % k
        if not isinstance(s, dict):
            errs.append("%s is not an object" % where)
            continue
        sid = s.get("shot")
        if sid:
            where = "%s (%s)" % (where, sid)
        for req in ("shot", "desc", "usable", "score"):
            if req not in s or s[req] in (None, ""):
                errs.append("%s: missing %s" % (where, req))
        if sid and sid not in known:
            errs.append("%s: unknown shot id %s (see shotlist)" % (where, sid))
        if sid in seen:
            warns.append("%s: shot listed twice, the later entry wins" % where)
        seen.add(sid)
        if sid and s.get("media") and sid in known and s["media"] != known[sid]:
            errs.append("%s: media %s does not match the shot id (should be %s)" % (where, s["media"], known[sid]))
        sc = s.get("score")
        if "score" in s and not (isinstance(sc, (int, float)) and not isinstance(sc, bool) and 0 <= sc <= 10):
            errs.append("%s: score must be a number from 0 to 10" % where)
        us = s.get("usable")
        if "usable" in s:
            if not isinstance(us, list):
                errs.append("%s: usable must be a list of [start, end] fractions" % where)
            else:
                for r in us:
                    if not (isinstance(r, list) and len(r) == 2 and _frac(r[0]) and _frac(r[1])):
                        errs.append("%s: usable range %s must be two fractions from 0 to 1" % (where, json.dumps(r)))
                    elif r[1] <= r[0]:
                        errs.append("%s: usable range %s ends before it starts" % (where, json.dumps(r)))
        if s.get("size") is not None and s["size"] not in SIZES:
            errs.append("%s: size %r is not one of %s" % (where, s["size"], " ".join(sorted(SIZES))))
        if s.get("move") is not None and s["move"] not in MOVES:
            errs.append("%s: move %r is not one of %s" % (where, s["move"], " ".join(sorted(MOVES))))
        for f in s.get("faces") or []:
            if isinstance(f, dict) and "x" in f and not _frac(f["x"]):
                errs.append("%s: face x %r must be a fraction from 0 to 1" % (where, f["x"]))
        for a in s.get("action") or []:
            if isinstance(a, dict) and "at" in a and not _frac(a["at"]):
                errs.append("%s: action at %r must be a fraction from 0 to 1" % (where, a["at"]))
    return errs, warns


def cmd_shotlog_check(lab, fn, as_json=False):
    doc = load_json(fn)
    errs, warns = check_shotlog(doc, known_shots(lab))
    result = "STOP" if errs else "WARN" if warns else "OK"
    if as_json:
        print(json.dumps({"result": result, "errors": errs, "warnings": warns, "shots": len(doc.get("shots") or [])}, indent=1))
        return result
    print("RESULT: %s" % result)
    for e in errs:
        print("ERROR %s" % e)
    for w in warns:
        print("WARN %s" % w)
    if not errs and not warns:
        print("%d shots, all fields valid" % len(doc.get("shots") or []))
    return result


def cmd_shotlog_merge(lab, files):
    known = known_shots(lab)
    merged, by, problems = {}, [], []
    for fn in files:
        doc = load_json(fn)
        errs, _ = check_shotlog(doc, known)
        bad = set()
        for e in errs:
            mm = re.match(r"shots\[(\d+)\]", e)
            if mm:
                bad.add(int(mm.group(1)))
            problems.append("%s: %s" % (os.path.basename(fn), e))
        b = doc.get("by")
        for x in (b if isinstance(b, list) else [b]):
            if x and x not in by:
                by.append(x)
        for k, s in enumerate(doc.get("shots") or []):
            if k in bad or not isinstance(s, dict):
                continue
            s = dict(s)
            s.setdefault("media", known.get(s["shot"]))
            merged[s["shot"]] = s
    order = {sid: i for i, sid in enumerate(known)}
    shots = sorted(merged.values(), key=lambda s: order.get(s["shot"], 10 ** 9))
    out = lab.p("shotlog.json")
    write_json(out, {"schema": "resolve-editor/shotlog@1", "by": by, "shots": shots})
    print("RESULT: %s" % ("WARN" if problems else "OK"))
    for p in problems:
        print("SKIPPED %s" % p)
    missing = [sid for sid in known if sid not in merged]
    print("%d shots logged, %d of %d shots in the lab have no entry yet" % (len(shots), len(missing), len(known)))
    print("WROTE %s" % posix(out))


# ---------------------------------------------------------------- deliverable QC
def load_preset(pid):
    p = os.path.expanduser(pid)
    if os.path.isfile(p):
        return load_json(p)
    f = os.path.join(PRESETS_DIR, pid + ".json")
    if not os.path.isfile(f):
        have = sorted(os.path.splitext(x)[0] for x in os.listdir(PRESETS_DIR)) if os.path.isdir(PRESETS_DIR) else []
        raise Fail("unknown preset %s; presets: %s" % (pid, ", ".join(have) or "none found"))
    return load_json(f)


class Runs:
    """Runs of True in a boolean stream fed chunk by chunk (samples); keeps runs of at least min_len."""

    def __init__(self, min_len):
        self.min_len, self.start, self.runs = min_len, None, []

    def feed(self, mask, off):
        if not len(mask):
            return
        m = np.r_[False, mask, False].astype(np.int8)
        d = np.diff(m)
        starts = list(np.flatnonzero(d == 1))
        ends = list(np.flatnonzero(d == -1))
        if self.start is not None:
            if mask[0]:
                starts[0] = None
            else:
                self._close(off)
        for a, b in zip(starts, ends):
            if b == len(mask):
                self.start = (self.start if a is None else off + a)
                return
            s = self.start if a is None else off + a
            self.start = None
            if off + b - s >= self.min_len:
                self.runs.append((s, off + b))
        if not mask[-1]:
            self.start = None

    def _close(self, end):
        if self.start is not None and end - self.start >= self.min_len:
            self.runs.append((self.start, end))
        self.start = None

    def finish(self, total):
        self._close(total)
        return self.runs


def audio_scan(path, sr=48000, chunk_s=10):
    """Streaming sample checks: clipping runs, digital silence, DC, L/R correlation, mono fold-down loss."""
    cmd = ff_base() + ["-i", path, "-map", "0:a:0", "-ac", "2", "-ar", str(sr), "-f", "f32le", "-acodec", "pcm_f32le", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    clip, quiet = Runs(3), Runs(int(0.02 * sr))
    acc = {"n": 0, "sL": 0.0, "sR": 0.0, "sLL": 0.0, "sRR": 0.0, "sLR": 0.0}
    fb = sr * chunk_s * 8

    def chunks():
        while True:
            buf = p.stdout.read(fb)
            if not buf:
                return
            x = np.frombuffer(buf[: len(buf) // 8 * 8], "<f4").astype(np.float64).reshape(-1, 2)
            mx = np.abs(x).max(1)
            clip.feed(mx >= 0.999, acc["n"])
            quiet.feed(mx < 10 ** (-90 / 20), acc["n"])
            L, R = x[:, 0], x[:, 1]
            acc["sL"] += L.sum()
            acc["sR"] += R.sum()
            acc["sLL"] += (L * L).sum()
            acc["sRR"] += (R * R).sum()
            acc["sLR"] += (L * R).sum()
            acc["n"] += len(x)
            yield x
    lr = lr_windows(chunks(), sr)
    p.wait()
    n, sL, sR, sLL, sRR, sLR = (acc[k] for k in ("n", "sL", "sR", "sLL", "sRR", "sLR"))
    res = {"samples": n, "rate": sr, "clip_runs": clip.finish(n), "quiet_runs": quiet.finish(n), "lr": lr}
    if n:
        vL, vR = sLL / n - (sL / n) ** 2, sRR / n - (sR / n) ** 2
        res["dc_db"] = round(20 * math.log10(max(abs(sL / n), abs(sR / n), 1e-12)), 1)
        if vL > 1e-12 and vR > 1e-12:
            res["lr_correlation"] = round((sLR / n - sL * sR / n / n) / math.sqrt(vL * vR), 3)
            mono = (sLL + sRR + 2 * sLR) / (4 * n)
            res["mono_fold_loss_db"] = round(10 * math.log10(mono / (0.5 * (sLL + sRR) / n) + 1e-20), 2)
    return res


def flash_scan(path, fps):
    """WCAG 2.3.1 simplified: a transition is a frame-to-frame change of 10 % of max relative luminance with the
    darker state under 0.8 over more than a quarter of the frame; a flash is a pair of opposing transitions."""
    events = []
    prev = None
    for i0, arr in decode_frames(path, 64, 36, "gray", batch=512):
        for j, f in enumerate(arr):
            v = np.clip((f.astype(np.float32) - 16) / 219.0, 0, 1) ** 2.4
            if prev is not None:
                d = v - prev
                dark = np.minimum(v, prev) < 0.8
                up = float(((d >= 0.1) & dark).mean())
                dn = float(((d <= -0.1) & dark).mean())
                if up > 0.25:
                    events.append((i0 + j, 1))
                elif dn > 0.25:
                    events.append((i0 + j, -1))
            prev = v
    flashes, last = [], None
    for fr, dirn in events:
        if last is not None and dirn != last[1]:
            flashes.append(fr)
            last = None
        else:
            last = (fr, dirn)
    win = max(1, int(round(float(fps))))
    worst, at = 0, None
    for k, fr in enumerate(flashes):
        c = sum(1 for g in flashes[k:] if g - fr < win)
        if c > worst:
            worst, at = c, fr
    return worst, at, len(flashes)


QC_CODECS = {"default": {"h264", "hevc"}, "youtube": {"h264", "hevc", "prores", "av1", "vp9", "dnxhd"},
             "linkedin": {"h264"}}
LOSSY_AUDIO = {"aac", "opus", "mp3", "ac3", "eac3", "vorbis"}


QC_SCAN_FPS = 4.0             # picture samples per second for log_picture, black_run and captions_missing
QC_SCAN_W = 270


def picture_scan(fn, w, h, want=()):
    """Luma p1 and p99, mean saturation and a black flag for 4 frames a second of a file (270 px wide), read as a
    stream; the frames at the times in want are kept whole (for the caption boxes)."""
    sw = QC_SCAN_W
    sh = max(2, int(round(sw * float(h) / max(1, w) / 2.0)) * 2)
    p = subprocess.Popen(ff_base() + ["-i", fn, "-map", "0:v:0", "-vf", "fps=%g,scale=%d:%d" % (QC_SCAN_FPS, sw, sh),
                                      "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    size = sw * sh * 3
    rows, keep = [], {}
    wantk = {int(round(t * QC_SCAN_FPS)): t for t in want}
    k = 0
    try:
        while True:
            b = p.stdout.read(size)
            if len(b) < size:
                break
            img = np.frombuffer(b, np.uint8).reshape(sh, sw, 3).astype(np.float32)
            y = img @ np.array([0.299, 0.587, 0.114], np.float32)
            mx, mn = img.max(axis=2), img.min(axis=2)
            sat = float(np.mean((mx - mn) / np.maximum(mx, 1.0)))
            p1, p99 = np.percentile(y, [1, 99])
            rows.append({"t": k / QC_SCAN_FPS, "p1": float(p1), "p99": float(p99), "sat": sat,
                         "black": bool(p99 < 30 and float(y.mean()) < 16)})
            if k in wantk:
                keep[k] = y
            k += 1
    finally:
        p.stdout.close()
        p.wait()
    return rows, keep, (sw, sh)


def qc_picture(add, fn, v, fps, edl=None):
    """log_picture (WARN), black_run (STOP) and, with the EDL, captions_missing (WARN)."""
    w, h = int(v.get("width") or 0), int(v.get("height") or 0)
    if not w or not h:
        return {}
    fr = float(fps) if fps else 25.0
    cues = []
    efps = fr
    ew, eh = w, h
    pic = None
    if edl:
        tl = edl.get("timeline") or {}
        efps = float(parse_fps(tl.get("fps") or "25/1"))
        ew, eh = int(tl.get("width") or w), int(tl.get("height") or h)
        cues = [c for tr in (edl.get("tracks") or {}).get("subtitle", []) for c in tr.get("items", []) if c.get("box")]
        pic = []
        for tr in (edl.get("tracks") or {}).get("video", []):
            for it in tr.get("items", []):
                if it.get("enabled", True) is not False:
                    pic.append((int(it["rec_in"]) / efps, int(it["rec_out"]) / efps))
    want = []
    for c in cues:
        a, b = int(c["rec_in"]) / efps, int(c["rec_out"]) / efps
        ks = [k for k in range(int(math.ceil(a * QC_SCAN_FPS)), int(math.ceil(b * QC_SCAN_FPS)))]
        if ks:
            want.append(min(ks, key=lambda k: abs(k / QC_SCAN_FPS - (a + b) / 2)) / QC_SCAN_FPS)
        else:
            want.append(None)
    rows, keep, (sw, sh) = picture_scan(fn, w, h, [t for t in want if t is not None])
    if not rows:
        return {}
    p1 = float(np.median([r["p1"] for r in rows]))
    p99 = float(np.median([r["p99"] for r in rows]))
    sat = float(np.mean([r["sat"] for r in rows]))
    if p99 < 215 and p1 > 30 and sat < 0.15:
        add("log_picture", "WARN", "the picture looks flat and grey (luma p1 %.0f, p99 %.0f, saturation %.2f): log "
            "footage that was not graded" % (p1, p99, sat),
            "grade it before delivery (a colour page grade, the resolve-colorist skill, or a Rec.709 conversion LUT)")
    end = rows[-1]["t"] + 1.0 / QC_SCAN_FPS
    k = 0
    while k < len(rows):
        if not rows[k]["black"]:
            k += 1
            continue
        j = k
        while j < len(rows) and rows[j]["black"]:
            j += 1
        t0, t1 = rows[k]["t"], rows[j - 1]["t"] + 1.0 / QC_SCAN_FPS
        inside = t0 > 0.0 and t1 < end - 1e-6
        if pic is not None:
            inside = any(a < t1 and t0 < b for a, b in pic)
        if t1 - t0 >= 0.5 - 1e-6 and inside:
            add("black_run", "STOP", "%.2f s of black at %.2f-%.2f s inside the programme" % (t1 - t0, t0, t1),
                "check the build at that time (a missing still or clip, an offline item) and render again",
                [int(round(t0 * fr))])
        k = j
    miss = []
    for c, t in zip(cues, want):
        if t is None:
            continue
        y = keep.get(int(round(t * QC_SCAN_FPS)))
        if y is None:
            continue
        x0, y0, bw, bh = [float(q) for q in c["box"]]
        sx, sy = sw / float(ew), sh / float(eh)
        crop = y[max(0, int(y0 * sy)):min(sh, int(math.ceil((y0 + bh) * sy))),
                 max(0, int(x0 * sx)):min(sw, int(math.ceil((x0 + bw) * sx)))]
        if crop.size and float((crop > 200).mean()) < 0.005:
            miss.append((c.get("id"), t))
    if miss:
        add("captions_missing", "WARN", "no caption text in the box of %d cue%s (%s)" % (
            len(miss), "" if len(miss) == 1 else "s", ", ".join("%s at %.2f s" % (i, t) for i, t in miss[:8])),
            "check the captions were built (Text+ on the caption track) or burned in at delivery",
            [int(round(t * fr)) for _, t in miss[:20]])
    return {"luma_p1": round(p1, 1), "luma_p99": round(p99, 1), "saturation": round(sat, 3)}


def cmd_qc(lab, fn, preset_id, platform=None, as_json=False, edl_path=None, out_dir=None):
    if not os.path.isfile(fn):
        raise Fail("file not found: %s" % fn)
    edl = load_json(edl_path) if edl_path else None
    pre = load_preset(preset_id)
    platform = platform or pre.get("platform")
    js = json.loads(run([ffprobe_bin(), "-v", "error", "-show_streams", "-show_format", "-of", "json", fn], timeout=120).stdout)
    v = next((s for s in js.get("streams", []) if s.get("codec_type") == "video"
              and not (s.get("disposition") or {}).get("attached_pic")), None)
    a = next((s for s in js.get("streams", []) if s.get("codec_type") == "audio"), None)
    checks, stats = [], {}

    def add(cid, level, msg, fix, at=None):
        checks.append({"id": cid, "level": level, "at_frames": at or [], "items": [], "msg": msg, "fix": fix})
    tl = pre.get("timeline") or {}
    au = pre.get("audio") or {}
    fps = None
    if v is None:
        add("format", "STOP", "no video stream", "render the programme with video")
    else:
        fps = _rate(v.get("avg_frame_rate")) or _rate(v.get("r_frame_rate"))
        w, h = int(v.get("width") or 0), int(v.get("height") or 0)
        stats.update({"width": w, "height": h, "fps": fps_str(fps), "codec": v.get("codec_name"), "pix_fmt": v.get("pix_fmt")})
        tw, th = tl.get("width"), tl.get("height")
        ok_sizes = {(tw, th)}
        pj = lab.p("project.json")
        try:
            ptl = (load_json(pj).get("timeline") or {}) if os.path.exists(pj) else {}
            pw_, ph_ = int(ptl.get("width") or 0), int(ptl.get("height") or 0)
        except (Fail, ValueError, TypeError):
            pw_ = ph_ = 0
        if isinstance(tw, int) and isinstance(th, int) and pw_ and ph_ and abs(pw_ * th - ph_ * tw) <= 0.01 * pw_ * th:
            ok_sizes.add((pw_, ph_))         # the edit's own raster (a UHD project keeps UHD)
        if isinstance(tw, int) and isinstance(th, int) and (w, h) not in ok_sizes:
            want = sorted(ok_sizes, key=lambda s: -s[0])[0]
            add("format", "STOP", "size %dx%d, the edit delivers %s" % (w, h, " or ".join("%dx%d" % s for s in sorted(ok_sizes))),
                "render at %dx%d" % want)
        want_fps = tl.get("fps")
        if want_fps == "project":
            pj = lab.p("project.json")
            want_fps = (load_json(pj).get("timeline") or {}).get("fps") if os.path.exists(pj) else None
        wf = parse_fps(want_fps) if want_fps else None
        if wf and fps and abs(float(wf) - float(fps)) > 0.005:
            add("format", "STOP", "frame rate %s, the edit runs at %s" % (fps_str(fps), fps_str(wf)),
                "render at the timeline frame rate %s" % fps_str(wf))
        codecs = QC_CODECS.get(platform, QC_CODECS["default"])
        if v.get("codec_name") not in codecs:
            add("format", "STOP", "video codec %s is not accepted for %s" % (v.get("codec_name"), platform or "upload"),
                "render H.264 (or HEVC) in an MP4 or MOV")
        if v.get("codec_name") == "h264" and re.search(r"10|12", v.get("pix_fmt") or ""):
            add("format", "STOP", "10-bit H.264 (%s) does not play on most phones and platforms" % v.get("pix_fmt"),
                "render 8-bit 4:2:0 H.264 (or 10-bit HEVC)")
        tags = [v.get("color_primaries"), v.get("color_transfer"), v.get("color_space")]
        stats["color_tags"] = tags
        if tags != ["bt709", "bt709", "bt709"]:
            add("tags", "WARN", "colour tags %s are not 1-1-1 (BT.709)" % "/".join(str(t) for t in tags),
                "set Rec.709 Gamma 2.4 output and tag the file Rec.709 (1-1-1)")
        worst, at, total = flash_scan(fn, fps or 25)
        stats["flashes_max_per_s"] = worst
        if worst > 3:
            add("photosensitive", "STOP", "%d flashes within one second (more than 3 is a seizure risk)" % worst,
                "slow the flashing or reduce the brightness change", [at])
        stats.update(qc_picture(add, fn, v, fps, edl))
    if a is None:
        add("loudness", "WARN", "no audio stream", "add the mix if the programme should have sound")
    else:
        try:
            r = ebur128(fn)
        except Fail as e:
            r = {"I": None, "TP": None, "LRA": None, "M_max": None, "S_max": None}
            add("loudness", "STOP", "the loudness meter failed: %s" % e, "check the audio stream")
        stats.update({"lufs": r["I"], "true_peak_db": r["TP"], "lra": r["LRA"], "s_max": r["S_max"],
                      "audio_codec": a.get("codec_name")})
        target, tol = au.get("lufs"), au.get("tol_lu", 1.0)
        if target is not None:
            if r["I"] is None:
                add("loudness", "STOP", "the programme is silent", "check the mix")
            elif abs(r["I"] - target) > tol:
                add("loudness", "STOP", "integrated %.1f LUFS, target %.1f (tolerance %.1f LU)" % (r["I"], target, tol),
                    "%+.1f dB is needed: on the Deliver page, Audio tab, tick Normalize Audio Levels and choose "
                    "Optimize to Standard at %.1f LUFS, then render again" % (target - r["I"], target))
        lossy = a.get("codec_name") in LOSSY_AUDIO
        tp_max = au.get("codec_tp_db") if lossy and au.get("codec_tp_db") is not None else au.get("true_peak_db")
        if tp_max is not None and r["TP"] is not None and r["TP"] > tp_max:
            add("true_peak", "STOP", "true peak %.1f dBTP over %.1f%s" % (r["TP"], tp_max, " (lossy audio codec)" if lossy else ""),
                "lower the limiter ceiling to %.1f dBTP" % (tp_max - 0.5))
        sc = audio_scan(fn)
        sr = sc["rate"]
        stats.update({k: sc.get(k) for k in ("lr_correlation", "mono_fold_loss_db", "dc_db")})
        fr = float(fps) if fps else 25.0
        if sc["clip_runs"]:
            s0 = sc["clip_runs"][0][0] / sr
            add("clipping", "STOP", "%d clipped runs (3 or more samples at full scale), first at %.2f s" % (len(sc["clip_runs"]), s0),
                "lower the mix into a true peak limiter", [int(round(s0 * fr))])
        inner = [(x, y) for x, y in sc["quiet_runs"] if x > int(0.5 * sr) and y < sc["samples"] - int(0.5 * sr)]
        if inner:
            s0 = inner[0][0] / sr
            add("dropout", "STOP", "%d digital silence dropouts, first at %.2f s for %d ms" % (len(inner), s0, round((inner[0][1] - inner[0][0]) / sr * 1000)),
                "fill the gap with room tone or fix the edit there", [int(round(x / sr * fr)) for x, _ in inner[:20]])
        lr = sc.get("lr") or {}
        if lr:
            stats["lr_imbalance_db"] = lr.get("imbalance_db")
            stats["lr"] = {k: lr.get(k) for k in ("left_share", "right_share", "lead_left_share", "lead_right_share",
                                                  "own_left_share", "own_right_share", "longest_one_sided_s",
                                                  "longest_at_s", "longest_side")}
        # judged on the loud parts of the programme, not on the whole-file median: a one-sided voice-over over a
        # centred music bed is one-sided while it speaks, and two speakers on separate channels take turns
        v = lr_verdict(lr, programme=True)
        dialogue = pre.get("spine_audio", "dialogue") == "dialogue"
        if v == "one_sided":
            side = lr_side(lr)
            other = "right" if side == "left" else "left"
            lv = {"left": float(lr.get("left_db") or 0), "right": float(lr.get("right_db") or 0)}
            if lv[other] <= -90:
                what = "the %s channel is silent" % other
            elif abs(float(lr["imbalance_db"])) >= LR_ONE_SIDED_DB:
                what = "the %s channel is %.0f dB louder over most of the programme" % (side, abs(float(lr["imbalance_db"])))
            else:
                at = lr.get("longest_at_s") or [0, 0]
                what = ("the %s channel carries it alone over %.0f %% of the loud parts (the longest stretch %.1f s, "
                        "%.1f to %.1f s)" % (side, 100 * float(lr.get("own_%s_share" % side, lr.get("%s_share" % side)) or 0),
                                             float(lr.get("longest_one_sided_s") or 0), at[0], at[1]))
            add("channel_balance", "STOP" if dialogue else "WARN", "the sound is on one side: %s, so a phone or "
                "headphones play it in one ear" % what,
                "make the clip with the voice mono in Resolve (Clip Attributes > Audio: Format Mono, Source Channel the "
                "one with the voice), then render again" + ("" if lv[other] <= -90 else "; the %s channel is not silent, "
                "so listen to it first: if it holds a second voice, keep both with Pan Spread 1 (PNT) on their own "
                "track instead of picking a channel" % other))
        elif v == "two_voices" and dialogue:
            add("channel_balance", "STOP", "left and right carry different voices in turn (the left channel has the "
                "sound to itself in %d %% of the loud parts, the right in %d %%): two speakers recorded on separate "
                "channels play one in each ear" % (round(100 * float(lr.get("own_left_share") or 0)),
                                                   round(100 * float(lr.get("own_right_share") or 0))),
                "do not set those clips to one channel (that drops a speaker): put their items on an audio track of "
                "their own, set that track's Pan Spread to 1 (PNT) in the Fairlight mixer, then render again")
        elif v == "split" and dialogue:
            add("channel_balance", "WARN", "left and right carry different sound (correlation %.2f): probably two "
                "microphones, one in each ear" % lr["correlation"],
                "make the dialogue clips mono from the better channel (Clip Attributes > Audio) and render again")
        corr = sc.get("lr_correlation")
        loss = sc.get("mono_fold_loss_db")
        if corr is not None and corr < -0.5:
            add("polarity", "WARN", "left and right correlate at %.2f: one channel is probably inverted" % corr,
                "invert the polarity of one channel")
        elif loss is not None and loss < -6:
            add("polarity", "WARN", "the mono fold-down loses %.1f dB" % -loss, "check phase between the channels")
    off = set(((pre.get("checks") or {}).get("off")) or [])
    checks = [c for c in checks if c["id"] not in off]
    result = "STOP" if any(c["level"] == "STOP" for c in checks) else "WARN" if checks else "OK"
    out = os.path.join(out_dir or lab.p("deliver"), "qc_%s.json" % safe_id(os.path.splitext(os.path.basename(fn))[0]))
    doc = {"schema": "resolve-editor/checks@1", "edl": posix(edl_path) if edl_path else None, "file": posix(fn),
           "preset": pre.get("id"),
           "platform": platform, "result": result, "checks": checks, "stats": stats}
    write_json(out, doc)
    if as_json:
        print(json.dumps(doc, indent=1))
        return doc
    print("RESULT: %s" % result)
    for c in checks:
        print("%s %s: %s; %s" % (c["level"], c["id"], c["msg"], c["fix"]))
    print("STATS %s" % " ".join("%s=%s" % (k, v) for k, v in stats.items() if v is not None and k != "color_tags"))
    print("WROTE %s" % posix(out))
    return doc


# ---------------------------------------------------------------- text views for agents
def mmss(t):
    t = max(0.0, float(t))
    h, rem = divmod(t, 3600)
    mi, s = divmod(rem, 60)
    return ("%d:%02d:%05.2f" % (h, mi, s)) if h >= 1 else ("%02d:%05.2f" % (mi, s))


def cmd_transcript(lab, mid, w_from=None, w_to=None, width=110):
    m = lab.get(mid)
    tr = load_transcript(lab, m)
    if tr is None:
        raise Fail("%s has no transcript yet; run ingest (or transcribe)" % mid)
    words = tr["words"]
    speech = sum(w["t1"] - w["t0"] for w in words)
    print("TRANSCRIPT %s  engine %s %s  lang %s  words %d  speech %.1f s of %.1f s" % (
        mid, tr.get("engine"), tr.get("model") or "", tr.get("language") or "-", len(words), speech, m.get("duration_s") or 0))
    if not words:
        print("no words: %s; silence spans of 0.12 s or more:" % (tr.get("note") or (
            "speech to text was not available" if tr.get("engine") == "none" else "no speech was heard")))
        for p in tr.get("pauses", []):
            print("  %s-%s (%.2f)" % (mmss(p["t0"]), mmss(p["t1"]), p["dur"]))
        if not tr.get("pauses"):
            print("  (none)")
        return
    print("key: [n] word index, * filler, ^ repeat, ~ meaning-dependent, ? low confidence, (0.82) pause in seconds")
    if any(set(w.get("tags") or []) & {"extra", "joined", "script_fix"} for w in words):
        print("     - not in the script (assemble drops it when there is a pause on both sides; else split the range "
              "around it), + part of the word before (one spoken word: a range or a drop takes the whole word), "
              "= text from the script or fix-word (heard differently)")
    pause_after = {p["after"]: p["dur"] for p in tr.get("pauses", []) if p.get("after") is not None}
    lo = 0 if w_from is None else max(0, int(w_from))
    hi = len(words) - 1 if w_to is None else min(len(words) - 1, int(w_to))
    for s in tr.get("sentences") or [{"i0": 0, "i1": len(words) - 1, "speaker": "S1"}]:
        if s["i1"] < lo or s["i0"] > hi:
            continue
        toks = []
        for i in range(max(lo, s["i0"]), min(hi, s["i1"]) + 1):
            w = words[i]
            mark = "".join(c for c, t in (("*", "filler"), ("^", "repeat"), ("~", "flag"), ("?", "low_conf"),
                                          ("-", "extra"), ("+", "joined"), ("=", "script_fix")) if t in w["tags"])
            tok = "[%d]%s%s" % (i, w["w"], mark)
            toks.append((i, tok))
            if i in pause_after and i < hi:
                toks.append((i, "(%.2f)" % pause_after[i]))
        line, first = [], None
        for i, tok in toks:
            if first is None:
                first = i
            pre = "%s %s " % (s.get("speaker") or words[first].get("speaker", "S1"), mmss(words[first]["t0"]))
            if line and len(pre) + len(" ".join(line + [tok])) > width:
                print(pre + " ".join(line))
                line, first = [], i
            line.append(tok)
        if line:
            print("%s %s %s" % (s.get("speaker") or "S1", mmss(words[first]["t0"]), " ".join(line)))


def cmd_peek(lab, target, at, width=360, out=None):
    """Source frames of a media id (times from its start) or a shot id (times from the shot's start), side by side at
    `width` pixels each, labelled, with the vertical crop guide when the edit is narrower than the source."""
    from PIL import Image, ImageDraw
    sid = None
    if target in lab.index["media"]:
        m, base = lab.get(target), 0
    elif ".s" in str(target) and str(target).rsplit(".s", 1)[0] in lab.index["media"]:
        m = lab.get(str(target).rsplit(".s", 1)[0])
        sh = [x for x in ((load_shots(lab, m) or {}).get("shots") or []) if x.get("id") == target]
        if not sh:
            raise Fail("shot %s is not in the shot list of %s (run shots first)" % (target, m["id"]))
        sid, base = target, int(sh[0]["in"])
    else:
        raise Fail("%s is neither a media id nor a shot id of this lab; run status or shotlist" % target)
    if m.get("kind") not in ("av", "video") or not m.get("fps"):
        raise Fail("%s has no video to peek at" % m["id"])
    try:
        times = [float(t) for t in str(at).split(",") if t.strip()]
    except ValueError:
        raise Fail("--at takes seconds separated by commas, like --at 0.5,1.2,2")
    if not times or len(times) > 12:
        raise Fail("give 1 to 12 times with --at")
    fps = float(parse_fps(m["fps"]))
    nfr = int(m.get("frames") or 0)
    idx = [min(max(0, nfr - 1), base + int(round(t * fps))) if nfr else base + int(round(t * fps)) for t in times]
    w = max(64, min(int(width), MAX_SIDE // min(4, len(times))))
    sw, sh_ = int(m.get("width") or 16), int(m.get("height") or 9)
    if (m.get("rotation") or 0) in (90, 270, -90):
        sw, sh_ = sh_, sw
    h = max(2, int(round(w * sh_ / float(sw) / 2.0)) * 2)
    got = grab_frames(frame_source(lab, m), idx, w, h)
    if not got:
        raise Fail("no frames could be read from %s" % m["id"])
    frames, stretched = view_stretch([got.get(i, np.zeros((h, w, 3), np.uint8)) for i in idx])
    shape = edit_shape(lab)
    frac = (shape[0] / float(shape[1])) / (sw / float(sh_)) if shape and sw / float(sh_) > shape[0] / float(shape[1]) else None
    per = min(4, len(frames))
    rows = int(math.ceil(len(frames) / float(per)))
    lab_h = 18
    sheet = Image.new("RGB", (per * w, rows * (h + lab_h)), (16, 16, 16))
    d = ImageDraw.Draw(sheet)
    font = pil_font(13)
    for k, (fr, t, i) in enumerate(zip(frames, times, idx)):
        x0, y0 = (k % per) * w, (k // per) * (h + lab_h)
        sheet.paste(Image.fromarray(np.asarray(fr, np.uint8)), (x0, y0 + lab_h))
        d.text((x0 + 4, y0 + 2), "%s %.2f s (f %d)" % (sid or m["id"], t, i), fill=(230, 230, 230), font=font)
        if frac:
            draw_crop_guide(d, x0, y0 + lab_h, w, h, frac)
    out_dir = out or lab.p("media", "peek")
    os.makedirs(out_dir, exist_ok=True)
    fn = os.path.join(out_dir, "%s_%s.jpg" % (safe_id(sid or m["id"]), "_".join("%g" % t for t in times)[:60]))
    sheet.save(fn, quality=88)
    print("PEEK %s: %d frame%s at %d px%s%s" % (sid or m["id"], len(frames), "" if len(frames) == 1 else "s", w,
                                               "; the cyan lines show the vertical crop, the ruler frame_x" if frac else "",
                                               "; flat picture stretched for viewing (not the grade)" if stretched else ""))
    print("WROTE %s" % posix(fn))
    return fn


def cmd_shotlist(lab, mid=None):
    ids = [mid] if mid else list(lab.index["media"].keys())
    log = {}
    if os.path.exists(lab.p("shotlog.json")):
        for s in load_json(lab.p("shotlog.json")).get("shots", []):
            log[s.get("shot")] = s
    for i in ids:
        m = lab.get(i)
        if m["kind"] == "image":
            print("MEDIA %s  %s  image %dx%d" % (i, m["name"], m.get("width") or 0, m.get("height") or 0))
            continue
        sh = load_shots(lab, m)
        if not sh:
            if m["kind"] in ("av", "video"):
                print("MEDIA %s  %s  no shots yet (run ingest)" % (i, m["name"]))
            continue
        q = (load_quality(lab, m) or {}).get("shots", {})
        fps = float(parse_fps(m["fps"]))
        print("MEDIA %s  %s  %s fps  %d frames  %.2f s  %d shots%s" % (
            i, m["name"], m["fps"], m["frames"], m["duration_s"], len(sh["shots"]),
            ("  flags " + ",".join(m["summary"].get("flags") or [])) if m["summary"].get("flags") else ""))
        for s in sh["shots"]:
            a, b = s["in"] / fps, s["out"] / fps
            fl = ",".join((q.get(s["id"]) or {}).get("flags") or [])
            extra = ""
            if s["id"] in log:
                e = log[s["id"]]
                extra = "  | score %s | %s" % (e.get("score"), e.get("desc"))
            print("%s %.2f-%.2f (%.2f s)%s%s" % (s["id"], a, b, b - a, (" " + fl) if fl else "", extra))


def cmd_search(lab, text, mid=None):
    q = [normw(x) for x in text.split() if normw(x)]
    if not q:
        raise Fail("search needs a word or a phrase")
    hits = 0
    ids = [mid] if mid else list(lab.index["media"].keys())
    for exact in (True, False):
        for i in ids:
            tr = load_transcript(lab, lab.get(i))
            if not tr or not tr["words"]:
                continue
            ws = tr["words"]
            nw = [normw(w["w"]) for w in ws]
            for k in range(len(ws) - len(q) + 1):
                ok = all((nw[k + j] == q[j]) if exact else nw[k + j].startswith(q[j]) for j in range(len(q)))
                if not ok:
                    continue
                a, b = max(0, k - 6), min(len(ws), k + len(q) + 6)
                ctx = " ".join(("[" + ws[j]["w"] + "]") if k <= j < k + len(q) else ws[j]["w"] for j in range(a, b))
                print("%s [%d] %s ...%s..." % (i, k, mmss(ws[k]["t0"]), ctx))
                hits += 1
        if hits:
            break
    if not hits:
        print("no hits for %r" % text)


def cmd_clean(lab, proxies=True, all_=False):
    freed = 0
    gone = []

    def rm(p):
        nonlocal freed
        if os.path.isfile(p):
            freed += os.path.getsize(p)
            os.remove(p)
            gone.append(p)
    for f in glob.glob(lab.p("media", "proxies", "*")):
        rm(f)
    for m in lab.index["media"].values():
        m.update({"proxy": None, "proxy_frames": None, "proxy_size": None, "wav": None, "wav16k": None})
        m.get("done", {}).pop("proxies", None)
    if all_:
        for f in glob.glob(lab.p("media", "analysis", "*")) + glob.glob(lab.p("media", "sheets", "*")):
            rm(f)
        for m in lab.index["media"].values():
            m["analysis"] = {k: None for k in ("shots", "words", "beats", "quality", "loudness")}
            m["done"] = {}
            m["summary"].update({"shots": None, "speech_s": None, "words": None, "lufs": None, "bpm": None})
    lab.save()
    print("deleted %d files, %.1f MB freed (source media untouched)" % (len(gone), freed / 2 ** 20))
    print("WROTE %s" % posix(lab.index_file))


# ---------------------------------------------------------------- ingest and single steps
STEP_FUNCS = {"proxies": step_proxies, "shots": step_shots, "transcribe": step_transcribe, "beats": step_beats,
              "loudness": step_loudness, "quality": step_quality}


def applies(step, m):
    k, au = m["kind"], bool(m.get("audio"))
    sfx = m.get("role") == "sfx"          # a sound effect: no transcript, no beat grid
    return {"proxies": k != "image", "shots": k in ("av", "video"), "transcribe": au and k != "image" and not sfx,
            "beats": au and (k == "audio" or bool(m.get("music"))) and m.get("role") not in ("voiceover", "sfx"),
            "loudness": au, "quality": k in ("av", "video")}[step]


def outputs_ok(lab, m, step):
    if not applies(step, m):
        return True

    def ex(rel):
        return bool(rel) and os.path.exists(lab.absp(rel))
    if step == "proxies":
        ok = True
        if m["kind"] in ("av", "video"):
            ok = ex(m.get("proxy"))
        if m.get("audio"):
            ok = ok and ex(m.get("wav")) and ex(m.get("wav16k"))
        return ok
    key = {"shots": "shots", "transcribe": "words", "beats": "beats", "loudness": "loudness", "quality": "quality"}[step]
    return ex(m["analysis"].get(key))


def analysis_digest(lab, m, key):
    rel = (m.get("analysis") or {}).get(key)
    if not rel or not os.path.exists(lab.absp(rel)):
        return None
    with open(lab.absp(rel), "rb") as fh:
        return hashlib.sha1(fh.read()).hexdigest()[:16]


def sig_for(lab, step, m, opt):
    base = [step, STEP_VERSION[step], m["hash"]]
    if step == "proxies":
        base += [opt.get("proxy_height", 360), audio_track1(m) if m.get("audio") else None]
    elif step == "transcribe":
        eng, mp = opt.get("_engine") or ("none", None)
        base += [eng, os.path.basename(mp) if mp else None, opt.get("language") or "auto", int(opt.get("speakers") or 1)]
    elif step == "beats":
        base += [bool(m.get("music")), m.get("beat_hints") or {}, audio_map_key(m)]
    elif step == "loudness":
        # measured on the lab's WAV of the placed audio track: a new mapping in Resolve (a one-sided clip set to
        # mono) changes the level
        base.append(audio_map_key(m))
    elif step == "quality":
        base.append(analysis_digest(lab, m, "shots"))
    return step_sig(*base)


def refresh_source(lab, m):
    """Re-hash the source; a changed file is probed again and every analysis runs again."""
    if not os.path.isfile(m["path"]):
        set_error(m, "probe", "the source file is missing: %s" % m["path"])
        return m, False
    h = file_hash(m["path"])
    if h == m["hash"]:
        return m, True
    pr = probe(m["path"])
    m2 = new_entry(m["id"], pr)
    m2["resolve"], m2["music"] = m.get("resolve"), m.get("music")
    if m.get("beat_hints"):
        m2["beat_hints"] = m["beat_hints"]
    if m.get("role") == "sfx":
        # a changed sound effect keeps its role; its peak is measured again
        m2["role"] = "sfx"
        add_flag(m2, "sfx")
        try:
            m2["sfx"] = {"peak_s": sfx_peak_s(m2["path"])}
        except Fail:
            m2["sfx"] = {"peak_s": None}
    if m2["music"]:
        add_flag(m2, "music")
    m2["errors"].append("probe: the file changed since it was added (hash_changed); analyses ran again")
    return m2, True


def run_media_steps(lab, mid, steps, opt, force=False):
    m = copy.deepcopy(lab.index["media"][mid])
    ran, skipped, failed = [], [], []
    try:
        m, ok = refresh_source(lab, m)
    except Fail as e:
        set_error(m, "probe", str(e))
        ok = False
    if not ok:
        lab.put(m)
        lab.save()
        return {"id": mid, "ran": ran, "skipped": skipped, "failed": ["probe"], "m": m}
    for step in steps:
        if not applies(step, m):
            if step == "beats":
                m["analysis"]["beats"] = None
            continue
        sig = sig_for(lab, step, m, opt)
        if not force and m["done"].get(step) == sig and outputs_ok(lab, m, step):
            skipped.append(step)
            continue
        set_error(m, step, None)
        t0 = time.time()
        try:
            STEP_FUNCS[step](lab, m, opt)
            m["done"][step] = sig
            ran.append((step, round(time.time() - t0, 2)))
        except Exception as e:  # one bad file or step never stops the whole ingest
            if os.environ.get("RE_DEBUG"):
                import traceback
                traceback.print_exc()
            msg = str(e) if isinstance(e, Fail) else "%s: %s" % (type(e).__name__, e)
            set_error(m, step, msg)
            m["done"].pop(step, None)
            failed.append(step)
        m["timings"][step] = round(time.time() - t0, 2)
        lab.put(m)
        lab.save()
    lr_new = False
    if "proxies" in steps and m.get("audio") and "lr" not in m["audio"] and m.get("wav") and \
            os.path.exists(lab.absp(m["wav"])):
        # a lab made before the stereo check: measure the existing WAV once
        try:
            stereo_check(lab, m)
            lr_new = bool(set(m["summary"].get("flags") or []) & {"one_sided", "split_channels", "two_voices"})
            lab.put(m)
            lab.save()
        except (OSError, EOFError, ValueError) as e:
            set_error(m, "proxies", "stereo check: %s" % e)
    return {"id": mid, "ran": ran, "skipped": skipped, "failed": failed, "m": m, "lr_new": lr_new}


def cmd_steps(lab, steps, opt, only=None, force=False, jobs=2, title="INGEST"):
    ids = lab.media(only)
    if not ids and "sheets" not in steps:
        raise Fail("no media in this lab yet; run add first")
    t_all = time.time()
    notes = []
    media_steps = [s for s in steps if s in MEDIA_STEPS]
    if "transcribe" in media_steps:
        opt["_engine"] = asr_engine(opt.get("asr") or "auto", opt.get("model"))
        eng, mp = opt["_engine"]
        lab.index["tools"]["asr"] = eng if eng == "none" else "%s %s" % (eng, os.path.splitext(os.path.basename(mp))[0])
        if eng == "none" and any(applies("transcribe", lab.index["media"][i]) for i in ids):
            notes.append("speech to text is not available (%s): transcripts hold only pauses; run doctor for the fix"
                         % ("--asr none" if (opt.get("asr") == "none") else "no whisper-cli or pywhispercpp with a model"))
    lab.index["tools"]["media_lab"] = VERSION
    if not lab.index["tools"].get("ffmpeg"):
        lab.index["tools"]["ffmpeg"] = ffmpeg_version()
    results = []
    if media_steps and ids:
        jobs = max(1, int(jobs or 1))
        if jobs == 1 or len(ids) == 1:
            results = [run_media_steps(lab, i, media_steps, opt, force) for i in ids]
        else:
            with ThreadPoolExecutor(min(jobs, len(ids))) as ex:
                results = list(ex.map(lambda i: run_media_steps(lab, i, media_steps, opt, force), ids))
    sheets_line = None
    if "sheets" in steps:
        t0 = time.time()
        try:
            js, made = cmd_sheets_log(lab, force=force)
            sheets_line = (js, made, round(time.time() - t0, 2))
        except Fail as e:
            notes.append("sheets: %s" % e)
    lab.save()
    for r in results:
        if {"proxies", "transcribe"} & set(x[0] for x in r["ran"]) or r.get("lr_new"):
            notes += audio_note(r["m"])
        if "transcribe" in [x[0] for x in r["ran"]] and "separate_sound" in (r["m"]["summary"].get("flags") or []):
            notes.append("%s looks like separately recorded sound (a sound-only file with %d words); this version "
                         "cannot sync it to the pictures, so the edit uses each camera's own sound. Use it only as a "
                         "voice-over, or sync it in Resolve first" % (r["id"], r["m"]["summary"].get("words") or 0))
        fl = set(r["m"]["summary"].get("flags") or [])
        if "beats" in [x[0] for x in r["ran"]] and bar_line_note(r["m"]):
            notes.append(bar_line_note(r["m"]))
        if "beats" in [x[0] for x in r["ran"]] and fl & {"tempo_uncertain", "bar1_uncertain"}:
            what = []
            if "tempo_uncertain" in fl:
                what.append("the tempo (%s BPM) may be wrong" % r["m"]["summary"].get("bpm"))
            if "bar1_uncertain" in fl:
                what.append("bar 1 is a guess")
            notes.append("%s: %s; listen, then run beats --only %s --bpm N --first-downbeat SECONDS"
                         % (r["id"], " and ".join(what), r["id"]))
    fails = [r for r in results if r["failed"]]
    hard = [r for r in fails if set(r["failed"]) & {"probe", "proxies", "shots"}]
    errors = [e for r in results for e in r["m"].get("errors", [])]
    result = "STOP" if results and len(hard) == len(results) else ("WARN" if fails or notes or errors else "OK")
    print("RESULT: %s" % result)
    for r in results:
        m = r["m"]
        ran = ", ".join("%s %.1fs" % x for x in r["ran"]) or "-"
        print("%-24s %-5s %9s  ran: %s  skipped: %s%s" % (
            r["id"], m["kind"], ("%.2f s" % m["duration_s"]) if m.get("duration_s") else "-", ran,
            ",".join(r["skipped"]) or "-", ("  FAILED: " + ",".join(r["failed"])) if r["failed"] else ""))
        for e in m.get("errors", []):
            print("    error: %s" % e)
    for n in notes:
        print("NOTE %s" % n)
    print("WROTE %s%s" % (posix(lab.index_file), "" if lab.writes else " (unchanged)"))
    if sheets_line:
        js, made, secs = sheets_line
        doc = load_json(js)
        print("WROTE %s%s" % (posix(js), "" if made else " (unchanged)"))
        for s in doc["sheets"]:
            print("SHEET %s %dx%d tokens %d shots %d" % (posix(lab.absp(s["path"])), s["size"][0], s["size"][1],
                                                          s["tokens"], len(s["shots"])))
    print("TIME %s %.1f s" % (title.lower(), time.time() - t_all))
    return result


# ---------------------------------------------------------------- doctor / commands
def whisper_models():
    """ggml model files in search order: RE_WHISPER_MODEL, SKILL/models, ~/.cache/whisper-models, ~/.cache/whisper.cpp."""
    out = []
    env = os.environ.get("RE_WHISPER_MODEL")
    if env and os.path.isfile(os.path.expanduser(env)):
        out.append(os.path.abspath(os.path.expanduser(env)))
    for d in (MODELS_DIR, os.path.join(os.path.expanduser("~"), ".cache", "whisper-models"),
              os.path.join(os.path.expanduser("~"), ".cache", "whisper.cpp")):
        out += sorted(glob.glob(os.path.join(d, "ggml-*.bin")))
    seen, res = set(), []
    for p in out:
        if p not in seen and "silero" not in os.path.basename(p).lower():
            seen.add(p)
            res.append(p)
    return res


def pick_model(model=None):
    """A model path from --model (a path or a size name such as small or base), else the best one found."""
    models = whisper_models()
    if model:
        mp = os.path.expanduser(model)
        if os.path.isfile(mp):
            return os.path.abspath(mp)
        for p in models:
            b = os.path.basename(p).lower()
            if b in ("ggml-%s.bin" % model.lower(), "ggml-%s.en.bin" % model.lower()) or b.startswith("ggml-%s" % model.lower()):
                return p
        raise Fail("no whisper model %r found (looked in RE_WHISPER_MODEL, the skill's models folder, "
                   "~/.cache/whisper-models and ~/.cache/whisper.cpp); run the install script with --asr" % model)
    if os.environ.get("RE_WHISPER_MODEL") and models and models[0] == os.path.abspath(os.path.expanduser(os.environ["RE_WHISPER_MODEL"])):
        return models[0]
    for pref in ("small", "base", "medium", "large-v3-turbo", "tiny"):
        for p in models:
            if re.match(r"ggml-%s(\.en)?(-q\w+)?\.bin$" % re.escape(pref), os.path.basename(p).lower()):
                return p
    return models[0] if models else None


def dtw_name(model_path):
    b = os.path.basename(model_path or "").lower()
    mm = re.match(r"ggml-(tiny|base|small|medium)(\.en)?(-q\w+)?\.bin$", b)
    if mm:
        return mm.group(1) + (mm.group(2) or "")
    mm = re.match(r"ggml-large-(v1|v2|v3)(-turbo)?(-q\w+)?\.bin$", b)
    if mm:
        return "large.%s%s" % (mm.group(1), ".turbo" if mm.group(2) else "")
    return None


def whisper_cli_path():
    env = os.environ.get("RE_WHISPER_CLI")
    if env:
        e = os.path.expanduser(env)
        return e if (os.path.isfile(e) or shutil.which(e)) else None
    return shutil.which("whisper-cli")


def has_pywhispercpp():
    try:
        import importlib.util
        return importlib.util.find_spec("pywhispercpp") is not None
    except Exception:
        return False


def asr_engine(want="auto", model=None):
    """(engine, model path) for --asr; engine is whisper-cli, pywhispercpp or none."""
    if want == "none":
        return "none", None
    mp = None
    try:
        mp = pick_model(model)
    except Fail:
        if want != "auto":
            raise
    if want in ("auto", "whisper-cli") and whisper_cli_path() and mp:
        return "whisper-cli", mp
    if want in ("auto", "pywhispercpp") and has_pywhispercpp() and mp:
        return "pywhispercpp", mp
    if want == "auto":
        return "none", None
    if not mp:
        raise Fail("no whisper model file found; run the install script with --asr small (or set RE_WHISPER_MODEL)")
    raise Fail("%s is not available; install whisper.cpp (macOS: brew install whisper-cpp) or pip install "
               "pywhispercpp, or use --asr none" % want)


def cmd_doctor(as_json=False):
    rows = []

    def add(ok, what, detail, level="problem"):
        rows.append({"ok": bool(ok), "check": what, "detail": detail, "level": "ok" if ok else level})
    v = sys.version_info
    add(v >= (3, 10), "python", "%d.%d.%d at %s" % (v[0], v[1], v[2], sys.executable) + ("" if v >= (3, 10) else ": need 3.10 or newer"))
    add(True, "numpy", np.__version__)
    try:
        import PIL
        from PIL import ImageFont
        ImageFont.load_default(size=14)
        add(True, "pillow", PIL.__version__)
    except Exception as e:
        add(False, "pillow", "missing or older than 10.1 (%s): run the install script again" % e)
    for t in (ffmpeg_bin(), ffprobe_bin()):
        try:
            out = run([t, "-version"], timeout=20).stdout.split("\n")[0]
            add(bool(out), os.path.basename(t), out or "no output")
        except Fail:
            add(False, os.path.basename(t), "not found. Install ffmpeg (macOS: brew install ffmpeg, Windows: winget "
                "install Gyan.FFmpeg, Linux: sudo apt install ffmpeg) or set RE_FFMPEG / RE_FFPROBE")
    try:
        fl = run([ffmpeg_bin(), "-hide_banner", "-filters"], timeout=20).stdout
        missing = [f for f in ("ebur128", "scdet", "silencedetect", "showinfo") if not re.search(r"\s%s\s" % f, fl)]
        add(not missing, "ffmpeg filters", "ebur128, scdet, silencedetect, showinfo present" if not missing else
            "missing %s: install a full ffmpeg build" % ", ".join(missing))
    except Fail:
        pass
    cli = whisper_cli_path()
    pyw = has_pywhispercpp()
    mp = pick_model()
    eng = "whisper-cli" if cli and mp else "pywhispercpp" if pyw and mp else "none"
    if eng != "none":
        add(True, "speech to text", "%s with %s%s" % (eng, os.path.basename(mp), (
            "; word edges use DTW token times when the model size is known (a transcript says \"word timing "
            "approximate\" when they were missing; whisper-cli is the exact route)") if eng == "pywhispercpp" else ""))
    else:
        why = []
        if not cli and not pyw:
            why.append("no whisper-cli and no pywhispercpp (macOS: brew install whisper-cpp; others: run the install script with --asr)")
        if not mp:
            why.append("no ggml model file (run the install script with --asr small, or set RE_WHISPER_MODEL)")
        add(False, "speech to text", "not available, transcripts will hold only pauses: " + "; ".join(why), level="warning")
    fg = free_gb(labs_root())
    add(fg is None or fg > 2, "free disk", ("%.1f GB free at %s" % (fg, posix(labs_root()))) if fg is not None else "unknown",
        level="warning" if fg is None or fg > 0.5 else "problem")
    problems = [r for r in rows if r["level"] == "problem"]
    ok = not problems
    print("DOCTOR: %s" % ("OK" if ok else "PROBLEMS"))
    if as_json:
        print(json.dumps({"ok": ok, "checks": rows, "labs_root": posix(labs_root())}, indent=1))
        return
    for r in rows:
        print("%-8s %-15s %s" % ({"ok": "ok", "warning": "warning", "problem": "PROBLEM"}[r["level"]], r["check"], r["detail"]))


def cmd_commands():
    for name, usage in COMMANDS:
        print("%-14s %s" % (name, usage))


# ---------------------------------------------------------------- command line
class _AP(argparse.ArgumentParser):
    def error(self, message):
        raise Fail("%s (usage: %s)" % (message, dict(COMMANDS).get(self.prog.split()[-1], self.prog)))


def _ap(cmd):
    return _AP(prog="media_lab.py " + cmd, add_help=True)


def _step_opts(a, cmd):
    if cmd in ("ingest", "transcribe"):
        a.add_argument("--asr", default="auto", choices=["auto", "whisper-cli", "pywhispercpp", "none"])
        a.add_argument("--model")
        a.add_argument("--language", default="auto")
        a.add_argument("--speakers", type=int, default=1)
    if cmd in ("ingest", "proxies"):
        a.add_argument("--proxy-height", type=int, default=360, choices=[360, 720])
        a.add_argument("--jobs", type=int, default=2)
    if cmd == "beats":
        a.add_argument("--bpm")
        a.add_argument("--first-downbeat", dest="first_downbeat")
        a.add_argument("--move-grid", dest="move_grid", action="store_true")
        a.add_argument("--shift-bar", dest="shift_bar", type=int)
    a.add_argument("--only")
    a.add_argument("--force", action="store_true")


def set_beat_hints(lab, only, bpm, first_downbeat, move_grid=False, shift_bar=None):
    """Store the known tempo or bar start of music media (from the song's metadata or the user's ear); the beats
    step then uses them instead of its guesses. "auto" clears a hint. move_grid says the bar start is exact (read on
    the waveform): the found beats move onto it whatever the offset. A new bar start without it clears it."""
    if move_grid and (first_downbeat is None or str(first_downbeat).lower() == "auto"):
        raise Fail("--move-grid needs --first-downbeat SECONDS (the exact time of a bar line)")
    if bpm is None and first_downbeat is None and not shift_bar:
        return
    if not only:
        raise Fail("--bpm, --first-downbeat and --shift-bar need --only ID (the music they describe)")
    for mid in lab.media(only):
        m = lab.index["media"][mid]
        h = dict(m.get("beat_hints") or {})
        for key, val, lo, hi in (("bpm", bpm, 30.0, 300.0), ("first_downbeat_s", first_downbeat, 0.0, 36000.0)):
            if val is None:
                continue
            if str(val).lower() == "auto":
                h.pop(key, None)
                continue
            try:
                v = float(val)
            except ValueError:
                raise Fail("%s must be a number or auto, not %r" % ("--bpm" if key == "bpm" else "--first-downbeat", val))
            if not lo <= v <= hi:
                raise Fail("%s %s is out of range (%g to %g)" % ("--bpm" if key == "bpm" else "--first-downbeat", val, lo, hi))
            h[key] = v
        if first_downbeat is not None:
            h.pop("move_grid", None)
            h.pop("shift_bar", None)
            if move_grid and "first_downbeat_s" in h:
                h["move_grid"] = True
        if shift_bar:
            # counted from the bar 1 the grid has now: two runs of --shift-bar 1 move it two beats
            k = (int(h.get("shift_bar") or 0) + int(shift_bar)) % 4
            if k:
                h["shift_bar"] = k
            else:
                h.pop("shift_bar", None)
        if h:
            m["beat_hints"] = h
        else:
            m.pop("beat_hints", None)
    lab.save()


def main(argv):
    utf8_stdio()
    if len(argv) < 2 or argv[1] in ("-h", "--help"):
        print(__doc__)
        return 0 if len(argv) >= 2 else 2
    if argv[1] in NO_LAB:
        lab_arg, cmd, rest = None, argv[1], argv[2:]
    else:
        if len(argv) < 3:
            raise Fail("give a LAB and a command, for example: media_lab.py LAB status (commands: %s)" % " ".join(COMMAND_NAMES))
        lab_arg, cmd, rest = argv[1], argv[2], argv[3:]
        if lab_arg in COMMAND_NAMES and cmd not in COMMAND_NAMES:
            raise Fail("the LAB folder comes first: media_lab.py LAB %s ..." % lab_arg)
    if cmd not in COMMAND_NAMES:
        raise Fail("unknown command %r. Commands: %s" % (cmd, " ".join(COMMAND_NAMES)))
    a = _ap(cmd)
    if cmd == "commands":
        cmd_commands()
        return 0
    if cmd == "doctor":
        a.add_argument("--json", action="store_true")
        cmd_doctor(a.parse_args(rest).json)
        return 0
    lab = Lab(lab_arg, create=cmd == "add")
    if cmd == "add":
        a.add_argument("paths", nargs="*")
        a.add_argument("--from-dump")
        a.add_argument("--with-timeline", action="store_true")
        a.add_argument("--music", nargs="+", default=[])
        a.add_argument("--voiceover", nargs="+", default=[])
        a.add_argument("--sfx", nargs="+", default=[])
        o = a.parse_args(rest)
        cmd_add(lab, o.paths, o.from_dump, o.music, o.with_timeline, o.voiceover, o.sfx)
    elif cmd == "estimate":
        a.add_argument("--only")
        a.add_argument("--json", action="store_true")
        o = a.parse_args(rest)
        cmd_estimate(lab, o.only, o.json)
    elif cmd == "status":
        a.add_argument("--json", action="store_true")
        cmd_status(lab, a.parse_args(rest).json)
    elif cmd in ("ingest",) + MEDIA_STEPS:
        _step_opts(a, cmd)
        o = a.parse_args(rest)
        opt = {k: getattr(o, k) for k in ("asr", "model", "language", "speakers", "proxy_height") if hasattr(o, k)}
        steps = list(ALL_STEPS) if cmd == "ingest" else [cmd]
        if cmd == "transcribe" and not os.path.isdir(lab.p("media")):
            raise Fail("no media yet; run add first")
        if cmd == "beats":
            set_beat_hints(lab, o.only, o.bpm, o.first_downbeat, o.move_grid, o.shift_bar)
        cmd_steps(lab, steps, opt, only=o.only, force=o.force, jobs=getattr(o, "jobs", 2), title=cmd.upper())
    elif cmd == "sheets":
        a.add_argument("--layout", default="log", choices=["log", "zoom"])
        a.add_argument("--only")
        a.add_argument("--out")
        a.add_argument("--force", action="store_true")
        o = a.parse_args(rest)
        if o.layout == "zoom":
            js, sheets = cmd_sheets_zoom(lab, o.only, o.out)
            for s in sheets:
                print("SHEET %s %dx%d tokens %d shots %d" % (s["path"], s["size"][0], s["size"][1], s["tokens"], len(s["shots"])))
                print("WROTE %s" % s["path"])
            print("WROTE %s" % posix(js))
        else:
            js, made = cmd_sheets_log(lab, force=o.force or bool(o.out), only=o.only, out_dir=o.out)
            doc = load_json(js)
            for s in doc["sheets"]:
                p = s["path"] if os.path.isabs(s["path"]) else posix(lab.absp(s["path"]))
                print("SHEET %s %dx%d tokens %d shots %d" % (p, s["size"][0], s["size"][1], s["tokens"], len(s["shots"])))
            print("WROTE %s%s" % (posix(js), "" if made else " (unchanged)"))
    elif cmd == "transcript":
        a.add_argument("id")
        a.add_argument("--from", dest="w_from", type=int)
        a.add_argument("--to", dest="w_to", type=int)
        a.add_argument("--width", type=int, default=110)
        o = a.parse_args(rest)
        cmd_transcript(lab, o.id, o.w_from, o.w_to, o.width)
    elif cmd == "shotlist":
        a.add_argument("id", nargs="?")
        cmd_shotlist(lab, a.parse_args(rest).id)
    elif cmd == "search":
        a.add_argument("text")
        a.add_argument("--media")
        o = a.parse_args(rest)
        cmd_search(lab, o.text, o.media)
    elif cmd == "shotlog-check":
        a.add_argument("file")
        a.add_argument("--json", action="store_true")
        o = a.parse_args(rest)
        cmd_shotlog_check(lab, o.file, o.json)
    elif cmd == "shotlog-merge":
        a.add_argument("files", nargs="+")
        cmd_shotlog_merge(lab, a.parse_args(rest).files)
    elif cmd == "qc":
        a.add_argument("file")
        a.add_argument("--preset", required=True)
        a.add_argument("--platform")
        a.add_argument("--edl")
        a.add_argument("--out")
        a.add_argument("--json", action="store_true")
        o = a.parse_args(rest)
        cmd_qc(lab, o.file, o.preset, o.platform, o.json, o.edl, o.out)
    elif cmd == "script":
        a.add_argument("id")
        a.add_argument("--file")
        a.add_argument("--text")
        a.add_argument("--force", action="store_true")
        a.add_argument("--json", action="store_true")
        o = a.parse_args(rest)
        cmd_script(lab, o.id, o.file, o.text, o.force, o.json)
    elif cmd == "fix-word":
        a.add_argument("id")
        a.add_argument("index", type=int)
        a.add_argument("text")
        o = a.parse_args(rest)
        cmd_fix_word(lab, o.id, o.index, o.text)
    elif cmd == "edge":
        a.add_argument("id")
        a.add_argument("index", type=int)
        a.add_argument("seconds", type=float)
        o = a.parse_args(rest)
        cmd_edge(lab, o.id, o.index, o.seconds)
    elif cmd == "peek":
        a.add_argument("target")
        a.add_argument("--at", required=True)
        a.add_argument("--width", type=int, default=360)
        a.add_argument("--out")
        o = a.parse_args(rest)
        cmd_peek(lab, o.target, o.at, o.width, o.out)
    elif cmd == "click":
        a.add_argument("id")
        a.add_argument("--from", dest="from_s", type=float)
        a.add_argument("--dur", type=float)
        a.add_argument("--out")
        o = a.parse_args(rest)
        cmd_click(lab, o.id, o.from_s, o.dur, o.out)
    elif cmd == "clean":
        a.add_argument("--proxies", action="store_true")
        a.add_argument("--all", action="store_true")
        o = a.parse_args(rest)
        cmd_clean(lab, True, o.all)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except Fail as e:
        sys.stderr.write("ERROR: %s\n" % e)
        sys.exit(2)
    except KeyboardInterrupt:
        sys.stderr.write("ERROR: stopped; run the same command again to resume\n")
        sys.exit(2)
