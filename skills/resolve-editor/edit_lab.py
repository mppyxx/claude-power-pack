#!/usr/bin/env python3
"""resolve-editor edit lab: cut lists, EDLs, offline previews, review packs, checks and Resolve snippets.

Usage:  edit_lab.py LAB COMMAND [args]
        edit_lab.py doctor | commands | schema NAME | presets        (these need no LAB)

LAB is the working folder of one edit (normally <home>/resolve-editor-labs/<name>; a bare name without a path
separator that is not a folder here resolves under that root, and RE_LABS_ROOT overrides the root).
`edit_lab.py commands` lists every command with a one line usage.

The EDL JSON (resolve-editor/edl@1) is the single source of truth: integer frames, half open ranges [in, out).
rec_* count timeline frames from the timeline start, src_* count frames of the media at the media's own rate
(audio-only media at the timeline rate). The offline preview and the Resolve build are two renders of one file.
"""
import copy
import glob
import datetime
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import wave
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction

import numpy as np

import fx_lab

HERE = os.path.dirname(os.path.abspath(__file__))
PRESETS_DIR = os.path.join(HERE, "presets")
SEMANTICS_PATH = os.path.join(HERE, "resolve_semantics.json")
VERSION = "1.0"
RENDERER_VERSION = 10         # bump when the preview renderer changes: invalidates every cached chunk
# every preview chunk and title patch ends in full-range 4:2:0: the pieces are joined without re-encoding, and the
# joined file keeps the first piece's range tag, so a chunk of another range (black gaps and stills came out
# limited range while camera proxies are full range) would decode with lifted blacks
OUT_RANGE = "scale=out_range=full,format=yuv420p"
MAX_SIDE = 2000               # every image an agent reads stays at or under this many pixels per side
GROUP_INPUTS = 25             # at most this many inputs in one ffmpeg filter graph
SR = 48000

COMMANDS = [
    ("doctor", "doctor [--json]  check python, numpy, pillow, ffmpeg, ffprobe, presets, resolve_semantics.json"),
    ("commands", "commands  list every command with its usage"),
    ("schema", "schema edl|cutlist|outline|preset|checks  print an annotated example"),
    ("presets", "presets  list the presets with their one line description"),
    ("init", "LAB init --name NAME [--preset ID] [--fps R] [--size WxH] [--start-tc TC] [--premium yes|no] "
             "[--fx none|light|normal|bold]  create the lab and project.json"),
    ("dump-script", "LAB dump-script [--bin NAME]...  read-only Resolve snippet: writes LAB/resolve/dump.json"),
    ("validate", "LAB validate EDL  RESULT plus the EDL's errors and warnings"),
    ("assemble", "LAB assemble CUTLIST OUT.json  cut list to EDL (snapped, padded, captions) plus OUT.assemble.json"),
    ("diff", "LAB diff A.json B.json  what changed between two EDL versions, by item id"),
    ("adopt", "LAB adopt EDL [--by NAME] [--summary TEXT]  copy an EDL into LAB/edits/vNNN.json"),
    ("preview", "LAB preview EDL [--out DIR]  render review_<stem>/preview.mov from the proxies"),
    ("review", "LAB review EDL [--tier quick|standard|best] [--out DIR]  preview, report, images and checks"),
    ("check", "LAB check EDL [--out DIR]  the gates only (EDL level, plus preview level when a preview exists)"),
    ("frames", "LAB frames EDL T[,T...] [--frames N,...] [--width 640] [--out DIR] [--true-levels]  zoom frames from "
               "the preview (log footage is contrast stretched unless --true-levels)"),
    ("export-srt", "LAB export-srt EDL OUT.srt [--spoken | --picture]  the captions as an SRT file: the picture's "
                   "cues to burn in, or with --spoken (the default when the preset's captions.deliver is file) every "
                   "spoken cue for a platform upload"),
    ("export-fcpxml", "LAB export-fcpxml EDL OUT.fcpxml  handoff timeline (FCPXML 1.10) for File > Import > Timeline"),
    ("edl-from-dump", "LAB edl-from-dump [OUT.json]  the Resolve timeline in LAB/resolve/dump.json as EDL v000"),
    ("stem", "LAB stem EDL --track A2 OUT.wav  render one audio track with gains, fades and volume envelope"),
    ("items", "LAB items EDL [--json]  one flat table of every item (track, times, media, source, text, why)"),
    ("explain", "explain ID [ID...]  what a check, verify, qc or media flag id means and how to fix it; also a "
                "catalogue key (a transition, fx kind, anim or genre), or transitions|fx|anims|genres for the lists"),
    ("mix", "LAB mix EDL OUT.wav [--json]  the preview mix normalised to the preset's loudness with a true-peak "
            "limiter, plus OUT.json"),
    ("level", "LAB level EDL OUT.json [--json]  the EDL with the mix gain (\"mix\": {\"gain_db\"}) that brings its fresh "
              "preview to the preset's loudness, as far as the true peak allows; prints the gain and the residual"),
    ("backup-script", "LAB backup-script  snippet: DRT, OTIO and JSON of the current timeline into LAB/backup/<stamp>/"),
    ("build-script", "LAB build-script EDL [--name NAME] [--chunk 80] [--import-missing] [--text textplus|markers] "
                     "[--captions burn|file]  snippets that build a NEW timeline (clips, stills, Text+ titles and "
                     "captions)"),
    ("verify-script", "LAB verify-script EDL  read-only snippet: reads the built timeline back into LAB/verify/<ver>/"),
    ("verify", "LAB verify EDL [--grabs] [--proof FILE] [--json]  compare the read-back timeline with the EDL (and its "
               "pixels with the preview): RESULT and verify.json"),
    ("grab-script", "LAB grab-script EDL [--max 60]  read-only snippet: Edit-page frame grabs of the built timeline into "
                    "LAB/verify/<ver>/grabs/ for verify --grabs"),
    ("deliver-script", "LAB deliver-script EDL [--platform P] [--start] [--out DIR]  snippet: a render job for the built "
                       "timeline (ask the user first; never an upload option)"),
    ("proof-script", "LAB proof-script EDL  snippet: a small proof render into LAB/verify/<ver>/ that deletes its job, "
                     "for verify --proof"),
]
NO_LAB = ("doctor", "commands", "schema", "presets", "explain")


class Fail(Exception):
    """An error the user can fix; printed as ERROR: without a traceback, exit code 2."""


# ---------------------------------------------------------------------------------------------------------- basics
def load_json(fn):
    try:
        with open(fn, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        raise Fail("%s does not exist" % fn)
    except ValueError as e:
        raise Fail("%s is not valid JSON (%s); fix the file and run the command again" % (fn, e))


def load_json_or(fn, default):
    if not fn or not os.path.exists(fn):
        return default
    return load_json(fn)


def write_json(fn, obj):
    """Atomic UTF-8 write, indent 1 (write name.tmp, then os.replace)."""
    d = os.path.dirname(os.path.abspath(fn))
    os.makedirs(d, exist_ok=True)
    tmp = fn + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, indent=1, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, fn)
    return fn


def iso_now():
    return datetime.datetime.now().replace(microsecond=0).isoformat()


def posix(p):
    return os.path.abspath(p).replace("\\", "/")


def safe_name(s):
    """Letters, digits, - and _ are kept; everything else becomes _."""
    s = re.sub(r"[^A-Za-z0-9_-]", "_", str(s).strip())
    return s.strip("_") or "edit"


def labs_root():
    r = os.environ.get("RE_LABS_ROOT", "").strip()
    return os.path.abspath(os.path.expanduser(r)) if r else os.path.join(os.path.expanduser("~"), "resolve-editor-labs")


def resolve_lab_arg(arg):
    """A path, or a bare edit name (no separator, not a folder here) that lives under the labs root."""
    if "/" not in arg and "\\" not in arg and arg not in (".", "..") and not os.path.isdir(arg):
        return os.path.join(labs_root(), safe_name(arg))
    return os.path.abspath(arg)


def ffmpeg_bin():
    return os.environ.get("RE_FFMPEG", "").strip() or "ffmpeg"


def ffprobe_bin():
    return os.environ.get("RE_FFPROBE", "").strip() or "ffprobe"


def run(cmd, what=None, **kw):
    """subprocess.run with a list; a failure becomes Fail with the tail of stderr."""
    try:
        r = subprocess.run([str(c) for c in cmd], capture_output=True, **kw)
    except FileNotFoundError:
        raise Fail("%s is not installed or not on PATH (set RE_FFMPEG / RE_FFPROBE to the programs)" % cmd[0])
    if r.returncode:
        err = r.stderr.decode("utf-8", "replace") if isinstance(r.stderr, bytes) else str(r.stderr)
        raise Fail("%s failed: %s" % (what or os.path.basename(str(cmd[0])), err.strip()[-800:]))
    return r


def file_hash(path):
    """sha1(str(size) + first 4 MiB + last 1 MiB)[:16]; whole file once when under 5 MiB (same as media_lab)."""
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


_HASH_MEMO = {}


def file_hash_cached(path):
    st = os.stat(path)
    key = (os.path.abspath(path), st.st_size, st.st_mtime_ns)
    if key not in _HASH_MEMO:
        _HASH_MEMO[key] = file_hash(path)
    return _HASH_MEMO[key]


def sha1_of(obj):
    return hashlib.sha1(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:16]


def utf8_stdio():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def rnd(x):
    """Round half up (seconds to frames and similar); never int(), never banker's rounding."""
    return int(math.floor(float(x) + 0.5))


def yes_no(v, what, default=False):
    """A yes/no answer of a cut list or project.json: true or false, or the words true, false, yes or no in any case.
    bool() would read the string "no" as true; anything else is refused."""
    if v is None:
        return bool(default)
    if isinstance(v, bool):
        return v
    if isinstance(v, str) and v.strip().lower() in ("true", "yes"):
        return True
    if isinstance(v, str) and v.strip().lower() in ("false", "no"):
        return False
    raise Fail("%s must be true or false (or yes or no), got %r" % (what, v))


def vis_tokens(w, h):
    return math.ceil(w / 28) * math.ceil(h / 28)


def workers():
    return max(2, min(8, (os.cpu_count() or 4) // 2))


# ------------------------------------------------------------------------------------------- frame rates and TC
_NTSC = {23.976: Fraction(24000, 1001), 29.97: Fraction(30000, 1001), 47.952: Fraction(48000, 1001),
         59.94: Fraction(60000, 1001), 119.88: Fraction(120000, 1001)}


def parse_fps(x):
    """A rational string, int, float or float string -> Fraction. 23.976, 29.97, 47.952, 59.94 and 119.88 (within
    0.01) map to the exact N*1000/1001 rates. Raises ValueError for anything else or a rate <= 0."""
    if isinstance(x, Fraction):
        f = x
    elif isinstance(x, bool) or x is None:
        raise ValueError("no frame rate")
    elif isinstance(x, int):
        f = Fraction(x)
    else:
        if isinstance(x, str):
            s = x.strip().split()[0] if x.strip() else ""
            if "/" in s:
                n, d = s.split("/", 1)
                f = Fraction(Fraction(n.strip()), Fraction(d.strip()))
                if f.denominator in (1, 1001) or f.denominator == 1:
                    if f <= 0:
                        raise ValueError("frame rate must be above 0")
                    return f
                x = float(f)
            else:
                x = float(s)
        x = float(x)
        if not math.isfinite(x) or x <= 0:
            raise ValueError("frame rate must be above 0")
        for k, v in _NTSC.items():
            if abs(x - k) < 0.01:
                return v
        f = Fraction(x).limit_denominator(1001)
    if f <= 0:
        raise ValueError("frame rate must be above 0")
    return f


def fps_str(f):
    f = parse_fps(f)
    return "%d/%d" % (f.numerator, f.denominator)


def fps_label(f):
    f = parse_fps(f)
    return str(f.numerator) if f.denominator == 1 else ("%.3f" % float(f)).rstrip("0").rstrip(".")


def nominal_fps(fps):
    return int(round(float(fps)))


def is_drop_capable(fps):
    f = float(fps)
    return abs(f - 29.97) < 0.01 or abs(f - 59.94) < 0.01


def tc_to_frames(tc, fps, drop=None):
    """'HH:MM:SS:FF' (';' or '.' before FF for drop frame) -> frames since 00:00:00:00. Raises on a timecode that
    does not exist (for example 00:01:00;00 in drop frame)."""
    s = str(tc).strip()
    if drop is None:
        drop = (";" in s or "." in s) and is_drop_capable(fps)
    parts = s.replace(";", ":").replace(".", ":").split(":")
    if len(parts) != 4:
        raise ValueError("bad timecode %r (use HH:MM:SS:FF)" % (tc,))
    h, m, sec, fr = [int(p) for p in parts]
    nom = nominal_fps(fps)
    if not (0 <= m < 60 and 0 <= sec < 60 and 0 <= fr < nom and h >= 0):
        raise ValueError("bad timecode %r at %s fps" % (tc, fps_label(fps)))
    frames = ((h * 60 + m) * 60 + sec) * nom + fr
    if drop:
        dropped = 2 if nom == 30 else 4
        if m % 10 != 0 and sec == 0 and fr < dropped:
            raise ValueError("timecode %r does not exist in drop frame" % (tc,))
        total_min = h * 60 + m
        frames -= dropped * (total_min - total_min // 10)
    return frames


def frames_to_tc(frames, fps, drop=False):
    frames = int(frames)
    nom = nominal_fps(fps)
    drop = bool(drop) and is_drop_capable(fps)
    if drop:
        dropped = 2 if nom == 30 else 4
        per10 = nom * 600 - dropped * 9
        per1 = nom * 60 - dropped
        d, m = divmod(frames, per10)
        if m > dropped:
            frames += dropped * 9 * d + dropped * ((m - dropped) // per1)
        else:
            frames += dropped * 9 * d
    fr = frames % nom
    sec = (frames // nom) % 60
    mi = (frames // (nom * 60)) % 60
    h = frames // (nom * 3600)
    return "%02d:%02d:%02d%s%02d" % (h, mi, sec, ";" if drop else ":", fr)


def tc_to_seconds(tc, fps, drop=None):
    s = str(tc)
    if drop is None:
        drop = (";" in s or "." in s) and is_drop_capable(fps)
    n = tc_to_frames(tc, fps, drop)
    return n / (float(fps) if drop else float(nominal_fps(fps)))


def is_drop_tc(tc):
    return ";" in str(tc) or "." in str(tc)


# ------------------------------------------------------------------------------------------ platforms and presets
# safe box (x, y, w, h) at the platform's render size; boxes scale linearly with the raster
PLATFORMS = {
    "reels": {"aspect": "9:16", "size": [1080, 1920], "safe": [65, 269, 950, 979], "max_s": 180,
              "_doc": "Meta 14 % top, 35 % bottom, 6 % sides; over 3 min not recommended"},
    "tiktok": {"aspect": "9:16", "size": [1080, 1920], "safe": [44, 130, 896, 1306], "max_s": 600,
               "_doc": "secondary sources; caption band moves up"},
    "shorts": {"aspect": "9:16", "size": [1080, 1920], "safe": [48, 288, 840, 960], "max_s": 180,
               "_doc": "Google overlay template; 3 min max"},
    "vertical_all": {"aspect": "9:16", "size": [1080, 1920], "safe": [65, 288, 823, 960], "max_s": 180,
                     "_doc": "union of reels, tiktok and shorts (default for vertical when unsure)"},
    "youtube": {"aspect": "16:9", "size": [1920, 1080], "safe": [96, 54, 1728, 972], "max_s": None,
                "_doc": "title safe 90 % (organic uploads)"},
    "linkedin": {"aspect": "any", "size": None, "title_safe": 0.9, "max_s": 600, "_doc": "MP4 only, at most 30 Mb/s"},
    "x": {"aspect": "any", "size": None, "title_safe": 0.9, "max_s": 140, "_doc": "1920x1080 or 1080x1920"},
    "square": {"aspect": "1:1", "size": [1080, 1080], "safe": [48, 48, 931, 642], "max_s": None,
               "_doc": "Google square template"},
}


def safe_box(platform, W, H):
    """Platform safe box [x, y, w, h] in timeline pixels for a W x H raster."""
    p = PLATFORMS.get(platform or "", {})
    if p.get("safe") and p.get("size"):
        sx, sy = W / p["size"][0], H / p["size"][1]
        x, y, w, h = p["safe"]
        return [x * sx, y * sy, w * sx, h * sy]
    ts = p.get("title_safe", 0.9)
    return [W * (1 - ts) / 2, H * (1 - ts) / 2, W * ts, H * ts]


DEFAULT_PRESET = {
    "schema": "resolve-editor/preset@1", "id": "_default", "_doc": "built-in defaults under every preset",
    "platform": "vertical_all",
    "timeline": {"width": 1080, "height": 1920, "fps": "project", "input_sizing": "scaleToCrop"},
    "length_s": {"min": 5, "target": 45, "max": 180},
    "hook": {"visual_change_by_s": 3.0, "proposition_by_s": 3.0, "first_word_by_s": 1.0, "gate": False},
    "pacing": {"median_shot_s": [0.8, 4.0], "cv": [0.3, 1.4], "visual_change_max_s": 6.0, "densest_window_after": 0.4},
    "speech": {"pad_ms": [60, 150], "pause_keep_ms": [150, 450], "handoff_ms": [400, 600],
               "remove": ["filler", "repeat"], "flag_only": ["flag"], "clean_gap_ms": 60, "nat_gain_db": -12.0},
    "spine_audio": "dialogue",
    "music": {"use": "optional", "cut_on": "downbeat", "beat_tol_frames": [-2, 1], "duck_lu": 14, "end": "resolve",
              "duck_hold_s": 1.05, "duck_max_db": None},
    "captions": {"style": "word_chunks", "words": [1, 4], "max_chars_line": 32, "max_lines": 2, "min_s": 0.35,
                 "cps_max": None, "y_band": [0.50, 0.61], "font_px": 64, "look": "caption_bold", "verify": "warn",
                 "deliver": "burn"},
    # the type system the shipped presets use (I8): the hook at least as large as a super, the CTA larger than the
    # name on the card and below it, clean looks; a lab preset without its own titles block gets these
    "titles": {"hook_top": {"y": 0.20, "font_px": 96, "max_chars_line": 22, "look": "hook_clean"},
               "lower_third": {"y": 0.62, "font_px": 54, "max_chars_line": 28, "look": "lower_clean"},
               "center": {"y": 0.45, "font_px": 80, "max_chars_line": 18, "look": "boxed"},
               "super": {"y": 0.45, "font_px": 88, "max_chars_line": 16, "look": "super_clean"},
               "cta": {"y": 0.58, "font_px": 84, "max_chars_line": 20, "look": "cta_card"},
               "end_card": {"y": 0.47, "font_px": 64, "max_chars_line": 18, "look": "end_card"}},
    # text looks (units em of the font size); "brand" and "brand.<colour>" come from LAB/brand.json. boxed and
    # caption_bold are the preview's look before looks existed
    "looks": {
        "boxed": {"font": "Arial", "style": "Bold", "font_file": None, "case": "as_written", "color": "#FFFFFF",
                  "stroke": {"color": "#000000", "em": 0.11}, "shadow": None,
                  "box": {"color": "#000000", "opacity": 0.6, "pad_em": [0.4, 0.2], "round": 0.0},
                  "line_spacing": 1.25, "align": "center"},
        "caption_bold": {"font": "Arial", "style": "Bold", "font_file": None, "case": "as_written", "color": "#FFFFFF",
                         "stroke": {"color": "#000000", "em": 0.11}, "shadow": None, "box": None,
                         "line_spacing": 1.25, "align": "center"},
        # the look the clean_box caption animation fades in (fx_catalog look_hint: box opacity 0.65)
        "caption_clean_box": {"font": "Arial", "style": "Bold", "font_file": None, "case": "as_written",
                              "color": "#FFFFFF", "stroke": None, "shadow": None,
                              "box": {"color": "#000000", "opacity": 0.65, "pad_em": [0.35, 0.18], "round": 0.25},
                              "line_spacing": 1.25, "align": "center"},
        "hook_bold": {"font": "brand", "style": "Bold", "font_file": None, "case": "as_written", "color": "#FFFFFF",
                      "stroke": {"color": "#000000", "em": 0.08}, "shadow": {"color": "#000000", "opacity": 0.5},
                      "box": None, "line_spacing": 1.15, "align": "center"},
        "super_heavy": {"font": "brand", "style": "Bold", "font_file": None, "case": "upper", "color": "#FFFFFF",
                        "stroke": {"color": "#000000", "em": 0.06}, "shadow": {"color": "#000000", "opacity": 0.6},
                        "box": None, "line_spacing": 1.1, "align": "center"},
        "cta_pill": {"font": "brand", "style": "Bold", "font_file": None, "case": "as_written", "color": "brand.text",
                     "stroke": None, "shadow": None,
                     "box": {"color": "brand.primary", "opacity": 1.0, "pad_em": [0.6, 0.3], "round": 1.0},
                     "line_spacing": 1.2, "align": "center"},
        "end_card": {"font": "brand", "style": "Bold", "font_file": None, "case": "as_written", "color": "#FFFFFF",
                     "stroke": None, "shadow": {"color": "#000000", "opacity": 0.4}, "box": None,
                     "line_spacing": 1.2, "align": "center"},
        # the clean type system: no outline, a soft shadow (titles), a thin stroke (captions), a pill for the CTA
        "hook_clean": {"font": "brand", "style": "Bold", "font_file": None, "case": "as_written", "color": "#FFFFFF",
                       "stroke": None, "shadow": {"color": "#000000", "opacity": 0.45}, "box": None,
                       "line_spacing": 1.1, "align": "center"},
        "super_clean": {"font": "brand", "style": "Bold", "font_file": None, "case": "upper", "color": "#FFFFFF",
                        "stroke": None, "shadow": {"color": "#000000", "opacity": 0.5}, "box": None,
                        "line_spacing": 1.05, "align": "center"},
        "caption_soft": {"font": "Arial", "style": "Bold", "font_file": None, "case": "as_written", "color": "#FFFFFF",
                         "stroke": {"color": "#000000", "em": 0.04}, "shadow": {"color": "#000000", "opacity": 0.55},
                         "box": None, "line_spacing": 1.2, "align": "center"},
        "cta_card": {"font": "brand", "style": "Bold", "font_file": None, "case": "as_written", "color": "#111111",
                     "stroke": None, "shadow": None,
                     "box": {"color": "#FFFFFF", "opacity": 1.0, "pad_em": [0.6, 0.3], "round": 1.0},
                     "line_spacing": 1.15, "align": "center"},
        # a name plate in the clean type system: a soft dark box, no outline (premium-safe)
        "lower_clean": {"font": "brand", "style": "Bold", "font_file": None, "case": "as_written", "color": "#FFFFFF",
                        "stroke": None, "shadow": None,
                        "box": {"color": "#000000", "opacity": 0.6, "pad_em": [0.4, 0.2], "round": 0.15},
                        "line_spacing": 1.2, "align": "center"}},
    "transitions": {"default": "cut", "allowed": ["cross_dissolve", "dip_to_black"], "max_share": 0.05},
    "audio": {"lufs": -14.0, "true_peak_db": -1.0, "tol_lu": 0.5, "codec_tp_db": -2.0},
    "checks": {"off": [], "soft": []},
    "workflow": {"angles": ["hook_first", "emotional_arc"], "styles": ["tight", "breathing"]},
    "judging": {"weights": {"emotion_story": 25, "narrative": 15, "rhythm_pacing": 15, "audiovisual": 15,
                            "continuity": 10, "composition_graphics": 10, "message": 10}},
}


def merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def preset_path(pid, lab=None):
    if not pid:
        return None
    if str(pid).lower().endswith(".json") and os.path.isfile(pid):
        return os.path.abspath(pid)
    cands = []
    if lab is not None:
        cands.append(lab.p("presets", "%s.json" % pid))
    cands.append(os.path.join(PRESETS_DIR, "%s.json" % pid))
    for c in cands:
        if os.path.isfile(c):
            return c
    return None


def load_preset(pid, lab=None, required=True):
    """Preset merged over the built-in defaults. Lookup: a .json path, LAB/presets/<id>.json, SKILL/presets/<id>.json."""
    if not pid:
        return copy.deepcopy(DEFAULT_PRESET)
    fn = preset_path(pid, lab)
    if fn is None:
        if required:
            raise Fail("unknown preset %r: run `edit_lab.py presets` for the list (or put %s.json into LAB/presets/)"
                       % (pid, pid))
        p = copy.deepcopy(DEFAULT_PRESET)
        p["id"] = pid
        return p
    return merge(DEFAULT_PRESET, load_json(fn))


def preset_names():
    if not os.path.isdir(PRESETS_DIR):
        return []
    return sorted(os.path.splitext(f)[0] for f in os.listdir(PRESETS_DIR) if f.endswith(".json"))


_SEM_CACHE = {}


def load_semantics():
    """resolve_semantics.json merged over the defaults (cached per file version; callers get their own copy)."""
    try:
        st = os.stat(SEMANTICS_PATH)
        key = (st.st_mtime_ns, st.st_size)
    except OSError:
        key = None
    if key not in _SEM_CACHE:
        _SEM_CACHE.clear()
        _SEM_CACHE[key] = _load_semantics()
    return copy.deepcopy(_SEM_CACHE[key])


def _load_semantics():
    base = {"schema": "resolve-editor/semantics@1", "append_end": "exclusive", "append_end_at_media_frames": "unknown",
            "record_frame": "absolute", "create_empty_timeline_makes_current": "unknown", "nearest_retime": "floor",
            "mixed_rate_duration": "floor", "set_speed_no_ripple": "unknown", "pan_positive": "right",
            "tilt_positive": "up",
            "transition_types": {"cross_dissolve": ["Cross Dissolve", "simple"],
                                 "dip_to_black": ["Dip To Color Dissolve", "simple"],
                                 "audio_xfade_3db": ["Cross Fade +3 dB", "audio"],
                                 "audio_xfade_0db": ["Cross Fade 0 dB", "audio"]},
            "subtitle_keys": "unknown", "notes": []}
    if os.path.exists(SEMANTICS_PATH):
        try:
            base = merge(base, load_json(SEMANTICS_PATH))
        except Fail:
            pass
    return base


_LOAD_SEMANTICS = load_semantics


# --------------------------------------------------------------------------------------------------------- the lab
class Lab:
    def __init__(self, path):
        self.path = os.path.abspath(path)
        self._index = None

    def p(self, *parts):
        return os.path.join(self.path, *parts)

    def rel(self, path):
        """LAB-relative paths in JSON files -> absolute paths (absolute ones pass through)."""
        if not path:
            return None
        return path if os.path.isabs(path) else os.path.normpath(os.path.join(self.path, path))

    def relto(self, path):
        a = os.path.abspath(path)
        try:
            r = os.path.relpath(a, self.path)
        except ValueError:
            return posix(a)
        return a.replace("\\", "/") if r.startswith("..") else r.replace("\\", "/")

    @property
    def project(self):
        return load_json_or(self.p("project.json"), {})

    @property
    def index(self):
        if self._index is None:
            self._index = load_json_or(self.p("media", "index.json"), {"schema": "resolve-editor/media-index@1",
                                                                        "media": {}})
        return self._index

    def media(self, mid):
        m = self.index.get("media", {}).get(mid)
        if m is None:
            raise Fail("media %r is not in LAB/media/index.json: add it with media_lab.py first" % mid)
        return m

    def analysis(self, mid, key):
        m = self.index.get("media", {}).get(mid) or {}
        fn = (m.get("analysis") or {}).get(key)
        if not fn:
            guess = self.p("media", "analysis", "%s.%s.json" % (mid, key))
            fn = guess if os.path.exists(guess) else None
        return load_json_or(self.rel(fn), None) if fn else None


_WORDS_MEMO = {}


def load_words_file(path):
    if not path or not os.path.exists(path):
        return None
    st = os.stat(path)
    key = (path, st.st_mtime_ns)
    if key not in _WORDS_MEMO:
        _WORDS_MEMO[key] = load_json(path)
    return _WORDS_MEMO[key]


# ----------------------------------------------------------------------------------------------------- EDL helpers
EDL_SCHEMA = "resolve-editor/edl@1"
TRACK_RE = {"video": re.compile(r"^V([1-9]\d*)$"), "audio": re.compile(r"^A([1-9]\d*)$"),
            "subtitle": re.compile(r"^ST([1-9]\d*)$")}


def load_edl(path):
    edl = load_json(path)
    if not isinstance(edl, dict) or edl.get("schema") != EDL_SCHEMA:
        raise Fail("%s is not a resolve-editor EDL (schema %r); a cut list goes through `assemble` first"
                   % (path, edl.get("schema") if isinstance(edl, dict) else None))
    edl.setdefault("tracks", {})
    for k in ("video", "audio", "subtitle"):
        edl["tracks"].setdefault(k, [])
    edl.setdefault("media", {})
    edl.setdefault("transitions", [])
    edl.setdefault("markers", [])
    return edl


def tl_fps(edl):
    return parse_fps(edl["timeline"]["fps"])


def media_fps(edl, mid):
    m = edl["media"].get(mid) or {}
    if m.get("kind") == "audio" or m.get("fps_from") == "timeline" or not m.get("fps"):
        return tl_fps(edl)
    return parse_fps(m["fps"])


def track_num(tid):
    m = re.match(r"^(?:V|A|ST)(\d+)$", str(tid))
    return int(m.group(1)) if m else 0


def iter_items(edl, kinds=("video", "audio", "subtitle")):
    for kind in kinds:
        for tr in edl["tracks"].get(kind, []):
            for it in tr.get("items", []):
                yield kind, tr, it


def rec_len(it):
    return int(it["rec_out"]) - int(it["rec_in"])


def speed_of(it):
    try:
        return float(it.get("speed", 1.0) or 1.0)
    except (TypeError, ValueError):
        return 1.0


def ratio(edl, it):
    """Source frames per timeline frame for a clip item (speed times media rate over timeline rate)."""
    return speed_of(it) * float(media_fps(edl, it["media"]) / tl_fps(edl))


def expected_src_len(edl, it):
    return rnd(rec_len(it) * ratio(edl, it))


def src_at(edl, it, k):
    """Source frame shown at item-relative timeline frame k (Nearest retime, floor)."""
    return int(it["src_in"]) + int(math.floor(k * ratio(edl, it) + 1e-9))


def placed_len(n, mf, tf):
    """Timeline frames Resolve gives n source frames of a clip at media rate mf on a timeline at rate tf (speed 1).

    Measured on 21.1 in the sandbox (24, 23.976, 25, 29.97, 30, 50 and 60 fps clips on 23.976, 24, 25, 29.97, 30,
    50 and 60 fps timelines, about 4700 placements): floor(n * tf / mf), and never less than 1 frame."""
    n = int(n)
    if n <= 0:
        return 0
    mf, tf = parse_fps(mf), parse_fps(tf)
    if mf == tf:
        return n
    rule = _sem_value("mixed_rate_duration", "floor")
    x = Fraction(n) * tf / mf
    v = int(math.floor(x + Fraction(1, 2))) if rule == "round" else int(math.floor(x))
    return max(1, v)


def src_for_len(L, mf, tf):
    """The fewest source frames that Resolve places as exactly L timeline frames, or None when no count does.

    Media slower than the timeline skips some lengths: a 24 fps clip on a 30 fps timeline can be 3 or 5 frames
    long but never 4 (3 source frames give 3, 4 give 5)."""
    L = int(L)
    if L <= 0:
        return None
    mf, tf = parse_fps(mf), parse_fps(tf)
    if mf == tf:
        return L
    x = Fraction(L) * mf / tf
    for n in range(max(1, int(math.floor(x)) - 1), int(math.ceil(x)) + 2):
        if placed_len(n, mf, tf) == L:
            return n
    return None


def nearest_placeable(L, mf, tf, up=False):
    """The nearest length at or below L (at or above when up) that Resolve can place, or None below 1."""
    k = int(L)
    step = 1 if up else -1
    for _ in range(200):
        if k < 1:
            return None
        if src_for_len(k, mf, tf) is not None:
            return k
        k += step
    return None


def _sem_value(key, default=None):
    if load_semantics is not _LOAD_SEMANTICS:          # a test replaced it: honour the replacement
        return load_semantics().get(key, default)
    load_semantics()
    return next(iter(_SEM_CACHE.values())).get(key, default)


def keeps_duration():
    return _sem_value("set_speed_no_ripple") == "keeps_duration"


def place_src(edl, it):
    """Source frames the build places for this clip item before any SetSpeed (None when no count gives its length).

    Under keeps_duration a retimed item is placed at speed 1 at its final length, so it needs the same count as a
    speed 1 item of that length, whatever it plays after SetSpeed."""
    if speed_of(it) != 1.0 and not keeps_duration():
        return int(it["src_out"]) - int(it["src_in"])
    return src_for_len(rec_len(it), media_fps(edl, it["media"]), tl_fps(edl))


def programme_frames(edl):
    ends = [int(it["rec_out"]) for kind, tr, it in iter_items(edl, ("video", "audio"))]
    return max(ends) if ends else 0


def v1_track(edl):
    for tr in edl["tracks"]["video"]:
        if tr.get("id") == "V1":
            return tr
    return edl["tracks"]["video"][0] if edl["tracks"]["video"] else {"id": "V1", "items": []}


def sorted_items(tr):
    return sorted(tr.get("items", []), key=lambda i: (int(i["rec_in"]), int(i["rec_out"])))


MARKER_COLORS = ("Blue", "Cyan", "Green", "Yellow", "Red", "Pink", "Purple", "Fuchsia", "Rose", "Lavender", "Sky",
                 "Mint", "Lemon", "Sand", "Cocoa", "Cream")


def std_rate(fps):
    """The standard frame rate nearest to a measured one, as an ffmpeg rate string (for constant rate copies)."""
    try:
        f = float(fps)
    except (TypeError, ValueError):
        return str(fps)
    rates = ["24000/1001", "24", "25", "30000/1001", "30", "48", "50", "60000/1001", "60", "100", "120"]
    return min(rates, key=lambda r: abs(float(Fraction(r)) - f) / float(Fraction(r)))


def image_units(edl, mid):
    """Timeline pixels per unit of Resolve's Pan and Tilt for this media, or None when the size is unknown.

    Measured on 21.1: Pan and Tilt move the image by value * (image width or height after input sizing) / (timeline
    width or height), and zoom does not change that. So on a 9:16 crop timeline a 16:9 source moves 3.16 timeline
    pixels per unit of Pan but 1 pixel per unit of Tilt. EDL pan_px and tilt_px are the pixels you see."""
    m = (edl.get("media") or {}).get(mid) or {}
    try:
        w, h = float(m.get("width") or 0), float(m.get("height") or 0)
    except (TypeError, ValueError):
        return None
    if w <= 0 or h <= 0:
        return None
    sar = str(m.get("sar") or "1:1").replace("/", ":").split(":")
    try:
        if len(sar) == 2 and float(sar[0]) > 0 and float(sar[1]) > 0:
            w *= float(sar[0]) / float(sar[1])
    except ValueError:
        pass
    if int(round(float(m.get("rotation") or 0))) % 180 == 90:
        w, h = h, w
    W, H = float(edl["timeline"]["width"]), float(edl["timeline"]["height"])
    sizing = (edl["timeline"].get("resolve") or {}).get("input_sizing") or "scaleToFit"
    if sizing in ("stretch", "scaleToStretch"):
        return 1.0, 1.0
    k = max(W / w, H / h) if sizing in ("scaleToCrop", "crop", "fill") else min(W / w, H / h)
    return w * k / W, h * k / H


def is_media_item(it):
    return it.get("kind", "clip") == "clip" and it.get("media")


def edl_hash(edl):
    e = {k: v for k, v in edl.items() if k != "version"}
    return sha1_of(e)


def canon_path(p):
    p = os.path.normpath(str(p or "")).replace("\\", "/")
    return p.lower() if sys.platform in ("darwin", "win32") else p


# --------------------------------------------------------------------------------------------------------- validate
def _issue(code, item, msg):
    return {"code": code, "item": item, "msg": msg}


def transition_handles(edl, x, items_by_id):
    """(frames needed after `from`, before `to`) in media frames for a transition of d frames, by the rule measured
    in Resolve 21.1 (fusion/transitions s3): centre needs head(B) >= d and tail(A) >= ceil(d/2), left head(B) >= d,
    right tail(A) >= d and head(B) >= d (shorter handles shorten it silently). A clip-pair transition (no
    transition item) needs none."""
    if ((x.get("resolve") or {}).get("build")) == "clip_pair":
        return 0, 0
    d = int(x.get("frames", 0))
    al = x.get("alignment", "center")
    need_tl_out, need_tl_in = fx_lab.need_handles(d, al)
    a = items_by_id.get(x.get("from"))
    b = items_by_id.get(x.get("to"))
    na = int(math.ceil(need_tl_out * ratio(edl, a) - 1e-9)) if a and is_media_item(a) else 0
    nb = int(math.ceil(need_tl_in * ratio(edl, b) - 1e-9)) if b and is_media_item(b) else 0
    return na, nb


def handle_frames(edl, it, side):
    """Unused source after (side "tail") or before ("head") a clip item, in timeline frames; stills unlimited."""
    if it is None or not is_media_item(it):
        return 0
    m = (edl.get("media") or {}).get(it.get("media")) or {}
    if m.get("kind") == "image":
        return 10 ** 6
    r = ratio(edl, it)
    if side == "head":
        return int(math.floor(int(it["src_in"]) / r + 1e-9))
    fr_ = m.get("frames")
    if fr_ is None:
        return 10 ** 6
    return max(0, int(math.floor((int(fr_) - int(it["src_out"])) / r + 1e-9)))


def validate(edl, lab=None, check_files=True):
    """(errors, warnings): lists of {code, item, msg}. Codes per the EDL contract."""
    errs, warns = [], []
    if not isinstance(edl, dict) or edl.get("schema") != EDL_SCHEMA:
        return [_issue("schema", None, "schema must be %s" % EDL_SCHEMA)], []
    try:
        tfps = tl_fps(edl)
    except (KeyError, ValueError, TypeError, ZeroDivisionError):
        return [_issue("fps_invalid", None, "timeline fps %r is not a frame rate" % (edl.get("timeline", {}).get("fps"),))], []
    media = edl.get("media", {})
    for mid, m in media.items():
        if m.get("kind") in ("av", "video") and not (m.get("fps_from") == "timeline"):
            try:
                parse_fps(m.get("fps"))
            except (ValueError, TypeError, ZeroDivisionError):
                errs.append(_issue("fps_invalid", mid, "media %s has no valid fps (%r)" % (mid, m.get("fps"))))
        if check_files and m.get("path"):
            p = lab.rel(m["path"]) if lab else m["path"]
            if not os.path.exists(p):
                errs.append(_issue("missing_file", mid, "media %s: %s does not exist" % (mid, m["path"])))
            elif m.get("hash"):
                try:
                    h = file_hash_cached(p)
                except OSError as e:
                    h = None
                    errs.append(_issue("missing_file", mid, "media %s cannot be read (%s)" % (mid, e)))
                if h and h != m["hash"]:
                    errs.append(_issue("hash_changed", mid, "media %s changed on disk since it was analysed" % mid))
    seen, by_id = set(), {}
    track_ids = set()
    for kind in ("video", "audio", "subtitle"):
        for tr in edl["tracks"].get(kind, []):
            tid = tr.get("id")
            if not TRACK_RE[kind].match(str(tid)) or tid in track_ids:
                errs.append(_issue("bad_track", tid, "%s track id %r is not valid or repeated" % (kind, tid)))
            track_ids.add(tid)
            for it in tr.get("items", []):
                iid = it.get("id")
                if not iid or iid in seen:
                    errs.append(_issue("dup_id", iid, "item id %r is missing or used twice" % (iid,)))
                seen.add(iid)
                by_id[iid] = it
                try:
                    ri, ro = int(it["rec_in"]), int(it["rec_out"])
                except (KeyError, TypeError, ValueError):
                    errs.append(_issue("empty_range", iid, "%s has no integer rec_in/rec_out" % iid))
                    continue
                if ro <= ri or ri < 0:
                    errs.append(_issue("empty_range", iid, "%s record range %d..%d is empty or negative" % (iid, ri, ro)))
                k = it.get("kind", "clip")
                if kind == "subtitle":
                    continue
                if k not in ("clip", "title", "solid"):
                    errs.append(_issue("bad_track", iid, "%s kind %r is not clip, title or solid" % (iid, k)))
                    continue
                if kind == "audio" and k != "clip":
                    errs.append(_issue("bad_track", iid, "%s: a %s cannot sit on an audio track" % (iid, k)))
                    continue
                if k != "clip":
                    continue
                mid = it.get("media")
                if mid not in media:
                    errs.append(_issue("unknown_media", iid, "%s uses media %r that is not in the EDL media list" % (iid, mid)))
                    continue
                m = media[mid]
                if kind == "video" and m.get("kind") == "audio":
                    errs.append(_issue("bad_track", iid, "%s puts audio-only media on a video track" % iid))
                    continue
                if kind == "audio" and m.get("kind") in ("video", "image"):
                    errs.append(_issue("bad_track", iid, "%s puts media without sound on an audio track" % iid))
                    continue
                if m.get("kind") == "image":
                    continue        # a still has no source range to check; the build places it by marks
                try:
                    si, so = int(it["src_in"]), int(it["src_out"])
                except (KeyError, TypeError, ValueError):
                    errs.append(_issue("empty_range", iid, "%s has no integer src_in/src_out" % iid))
                    continue
                if so <= si:
                    errs.append(_issue("empty_range", iid, "%s source range %d..%d is empty" % (iid, si, so)))
                    continue
                frames = m.get("frames")
                if frames is not None and (si < 0 or so > int(frames)):
                    errs.append(_issue("src_range", iid, "%s source %d..%d is outside the media 0..%d" % (iid, si, so, int(frames))))
                elif (frames is not None and so == int(frames)
                      and load_semantics().get("append_end_at_media_frames") != "ok"):
                    warns.append(_issue("last_media_frame", iid, "%s ends on the last media frame; the build sends it "
                                                                  "one frame short" % iid))
                sp = speed_of(it)
                if not isinstance(it.get("speed", 1.0), (int, float)) or sp <= 0:
                    errs.append(_issue("speed_mismatch", iid, "%s speed %r must be a number above 0" % (iid, it.get("speed"))))
                    continue
                elif abs((so - si) - expected_src_len(edl, it)) > 1:
                    errs.append(_issue("speed_mismatch", iid, "%s source length %d does not match %d timeline frames at "
                                                              "speed %g (expected %d)" % (iid, so - si, ro - ri, sp,
                                                                                         expected_src_len(edl, it))))
                try:
                    mf_ = media_fps(edl, mid)
                except (ValueError, TypeError, ZeroDivisionError):
                    mf_ = None
                if mf_ is not None and ro > ri:
                    need = place_src(edl, it)
                    if need is None:
                        L = ro - ri
                        lo_ = nearest_placeable(L, mf_, tfps)
                        hi_ = nearest_placeable(L, mf_, tfps, up=True)
                        errs.append(_issue("rate_length", iid, "%s is %d frames long, but a %s fps clip on a %s fps "
                                           "timeline can only be placed at %s frames; make it one of those" % (
                                               iid, L, fps_label(mf_), fps_label(tfps),
                                               " or ".join(str(x) for x in (lo_, hi_) if x))))
                    elif frames is not None and si + need > int(frames):
                        if sp < 1.0 and keeps_duration():
                            k_ = si + need - int(frames)
                            errs.append(_issue("slowmo_handle", iid, "%s at %g %% needs %d source frames after its in "
                                               "point for Resolve's placement (it places the item at 100 %% first), "
                                               "only %d exist; start it %d frames earlier or shorten it" % (
                                                   iid, sp * 100, need, int(frames) - si, k_)))
                        else:
                            errs.append(_issue("rate_length", iid, "%s needs %d source frames after its in point for "
                                               "Resolve's placement at this frame rate, only %d exist; shorten it by "
                                               "a frame or start it earlier" % (iid, need, int(frames) - si)))
                    if sp < 1.0 and float(mf_) * sp < float(tfps) * 0.999:
                        need_pct = math.ceil(float(tfps) / float(mf_) * 1000) / 10.0
                        warns.append(_issue("slowmo_repeat", iid, "%s at %g %% of a %s fps clip on a %s fps timeline "
                                            "repeats frames (%s)" % (
                                                iid, sp * 100, fps_label(mf_), fps_label(tfps),
                                                "clean slow motion needs a speed of at least %g %%" % need_pct
                                                if need_pct < 100 else "this clip has too few frames for clean slow "
                                                "motion on this timeline; use a high frame rate clip")))
                if m.get("vfr"):
                    warns.append(_issue("vfr_media", iid, "%s uses variable frame rate media %s; the preview and "
                                        "Resolve can disagree by frames. Make a constant rate copy (ffmpeg -i IN "
                                        "-fps_mode cfr -r %s OUT) and use that" % (iid, mid, std_rate(mf_ or tfps))))
                if kind == "video" and track_num(tr.get("id")) > 1:
                    tf = it.get("transform") or {}
                    if float(tf.get("opacity", 100)) < 100 or float(tf.get("zoom", 1.0)) < 1.0:
                        warns.append(_issue("unsupported_preview", iid, "%s: opacity under 100 or a picture in picture "
                                                                        "renders opaque in the preview" % iid))
    # overlaps per track
    trans_pairs = {(x.get("from"), x.get("to")) for x in edl.get("transitions", [])}
    for kind in ("video", "audio", "subtitle"):
        for tr in edl["tracks"].get(kind, []):
            items = [i for i in sorted_items(tr) if isinstance(i.get("rec_in"), int)]
            for a, b in zip(items, items[1:]):
                if int(b["rec_in"]) < int(a["rec_out"]) and (a.get("id"), b.get("id")) not in trans_pairs:
                    errs.append(_issue("overlap", b.get("id"), "%s overlaps %s on %s (%d < %d)" % (
                        b.get("id"), a.get("id"), tr.get("id"), int(b["rec_in"]), int(a["rec_out"]))))
    # transitions
    for x in edl.get("transitions", []):
        a, b = by_id.get(x.get("from")), by_id.get(x.get("to"))
        xid = x.get("id")
        if not a or not b:
            errs.append(_issue("transition_handles", xid, "transition %s refers to unknown items" % xid))
            continue
        if int(a["rec_out"]) != int(b["rec_in"]):
            errs.append(_issue("transition_handles", xid, "transition %s: %s and %s do not meet (%d vs %d)" % (
                xid, a["id"], b["id"], int(a["rec_out"]), int(b["rec_in"]))))
            continue
        key_ = str(x.get("type") or "cross_dissolve")
        row_ = fx_lab.tr_row(key_)
        if row_ is None or ((row_.get("resolve") or {}).get("category") == "audio"):
            errs.append(_issue("transition_unknown", xid, "transition %s: type %r is not a video transition of "
                               "fx_catalog.json" % (xid, key_)))
            continue
        if not (is_media_item(a) and is_media_item(b)):
            continue
        if ((row_.get("resolve") or {}).get("build")) == "clip_pair" or \
                ((x.get("resolve") or {}).get("build")) == "clip_pair":
            continue
        al_ = x.get("alignment", "center")
        img_a = (media.get(a["media"]) or {}).get("kind") == "image"
        img_b = (media.get(b["media"]) or {}).get("kind") == "image"
        if al_ in ("left", "right") and not img_b and handle_frames(edl, b, "head") <= 0:
            errs.append(_issue("transition_through_black", xid, "transition %s is %s aligned and %s has no frames before "
                               "its in point: Resolve renders a fade through black, not a dissolve; give it head "
                               "frames or centre it" % (xid, al_, b["id"])))
            continue
        na, nb = transition_handles(edl, x, by_id)
        fa = media.get(a["media"], {}).get("frames")
        if not img_a and fa is not None and int(a["src_out"]) + na > int(fa):
            errs.append(_issue("transition_handles", xid, "transition %s needs %d frames of %s after its out point; "
                               "only %d exist" % (xid, na, a["id"], int(fa) - int(a["src_out"]))))
        if not img_b and int(b["src_in"]) - nb < 0:
            errs.append(_issue("transition_handles", xid, "transition %s needs %d frames of %s before its in point "
                               "(Resolve places a %s %d-frame transition shorter otherwise); only %d exist" % (
                                   xid, nb, b["id"], al_, int(x.get("frames", 0)), int(b["src_in"]))))
    # effects, retimes and text animations (fx_catalog.json; FUSION_PLAN s2.3)
    ov_ = fx_lab.item_overlaps(edl) if edl.get("transitions") else {}
    stills_ok = bool(fx_lab.load_catalog().get("stills_ok"))
    for tr in edl["tracks"].get("video", []):
        for it in tr.get("items", []):
            iid = it.get("id")
            an_ = it.get("anim") or {}
            if an_.get("id") not in (None, "none") and (fx_lab.anim_row(an_.get("id")) or {}).get("enabled") is not True:
                errs.append(_issue("anim_unknown", iid, "%s: anim %r is not in fx_catalog.json (or switched off)" % (
                    iid, an_.get("id"))))
            fxs = it.get("fx") or []
            has = bool(fxs or it.get("motion") or it.get("accents") or it.get("retime"))
            if not has or not isinstance(it.get("rec_in"), int):
                continue
            h_, t_ = ov_.get(iid, (0, 0))
            for x in fxs:
                frow = fx_lab.fx_row(x.get("kind"))
                if frow is None or not frow.get("enabled", True):
                    errs.append(_issue("fx_unknown", iid, "%s: fx %s kind %r is not in fx_catalog.json (or switched "
                                       "off)" % (iid, x.get("id"), x.get("kind"))))
                    continue
                f_ = x.get("f") or []
                # the frames the effect can be seen: this item's, plus its sibling pieces' when assemble split the
                # segment around a dropped filler (s01, s01_2, ...: one curve over contiguous pieces)
                base_ = str(x.get("id") or "").split(".fx")[0]
                sib_ = [s for s in tr.get("items", []) if isinstance(s.get("rec_in"), int) and (
                    s.get("id") == base_ or re.match(re.escape(base_) + r"_\d+$", str(s.get("id") or "")))]
                if it not in sib_:
                    sib_ = [it]
                lo_ = min(int(s["rec_in"]) - ov_.get(s.get("id"), (0, 0))[0] for s in sib_) - int(it["rec_in"])
                hi_ = max(int(s["rec_out"]) + ov_.get(s.get("id"), (0, 0))[1] for s in sib_) - 1 - int(it["rec_in"])
                if len(f_) == 2 and (int(f_[0]) < lo_ or int(f_[1]) > hi_):
                    a_, b_ = max(int(f_[0]), lo_), min(int(f_[1]), hi_)
                    seen_ = ("only frames %d..%d of it are seen, the rest is cut off" % (a_, b_) if a_ <= b_
                             else "none of it is seen")
                    warns.append(_issue("fx_span", iid, "%s: fx %s runs over frames %d..%d, but the item is seen on "
                                        "frames %d..%d: %s" % (iid, x.get("id"), int(f_[0]), int(f_[1]), lo_, hi_,
                                                               seen_)))
            zk_ = ((it.get("motion") or {}).get("keys") or {}).get("zoom") or []
            if zk_:
                # a keyed zoom under the item's framing shows the picture's edge (mirrored in the build, black in
                # the preview); the static zoom of a full-frame item covers some of it
                lo_v, hi_v = -h_, rec_len(it) + t_ - 1
                zs_ = [fx_lab.value_at(zk_, f) for f in range(lo_v, hi_v + 1)]
                st_ = float((it.get("transform") or {}).get("zoom", 1.0) or 1.0)
                if zs_ and min(zs_) * max(1.0, st_) < 1.0 - 1e-6:
                    errs.append(_issue("fx_edges", iid, "%s: the keyed zoom dips to %.3f (with the static zoom %.3f): "
                                       "the picture's edge shows; keep every keyed zoom at 1 or above" % (
                                           iid, min(zs_), st_)))
            m_ = media.get(it.get("media")) or {}
            if m_.get("kind") == "image" and not stills_ok:
                errs.append(_issue("fx_still_untested", iid, "%s: keyed effects on a still are not built yet (comp time "
                                   "on a still item is unmeasured); give it a static zoom" % iid))
            rt_ = it.get("retime")
            if rt_:
                if abs(speed_of(it) - 1.0) > 1e-9:
                    errs.append(_issue("retime_speed", iid, "%s carries retime keys and speed %g: a retimed item plays "
                                       "at speed 1" % (iid, speed_of(it))))
                ks_ = [v for f, v in rt_.get("keys") or []]
                span_ = rt_.get("src_span") or ([min(ks_), max(ks_)] if ks_ else None)
                fr_ = m_.get("frames")
                if span_ and (int(span_[0]) < 0 or (fr_ is not None and int(span_[1]) >= int(fr_))):
                    errs.append(_issue("retime_range", iid, "%s: the retime shows source frames %d..%d, outside the "
                                       "media 0..%s" % (iid, int(span_[0]), int(span_[1]),
                                                        int(fr_) - 1 if fr_ is not None else "?")))
    for tr in edl["tracks"].get("subtitle", []):
        for c in tr.get("items", []):
            an_ = c.get("anim") or {}
            if an_.get("id") not in (None, "none") and (fx_lab.anim_row(an_.get("id")) or {}).get("enabled") is not True:
                errs.append(_issue("anim_unknown", c.get("id"), "%s: anim %r is not in fx_catalog.json (or switched "
                                   "off)" % (c.get("id"), an_.get("id"))))
    # the mix block (one uniform gain on every enabled audio item, for the loudness target)
    mx_ = edl.get("mix")
    if mx_ is not None:
        g_ = mx_.get("gain_db") if isinstance(mx_, dict) else mx_
        if not isinstance(mx_, dict) or (g_ is not None and (isinstance(g_, bool) or not isinstance(g_, (int, float))
                                                             or not math.isfinite(float(g_)))):
            errs.append(_issue("bad_mix", None, "mix must be {\"gain_db\": a number in dB} (got %r)" % (mx_,)))
        elif g_ is not None and not MIX_GAIN_RANGE[0] <= float(g_) <= MIX_GAIN_RANGE[1]:
            errs.append(_issue("bad_mix", None, "mix.gain_db %g is outside %g to %+g dB" % (
                float(g_), MIX_GAIN_RANGE[0], MIX_GAIN_RANGE[1])))
    tgt = (edl.get("targets") or {}).get("duration_frames")
    if tgt and programme_frames(edl) != int(tgt):
        warns.append(_issue("duration_target", None, "length %d frames differs from the target %d" % (programme_frames(edl), int(tgt))))
    return errs, warns


MIX_GAIN_RANGE = (-20.0, 12.0)      # dB: the EDL mix block's uniform gain


def mix_gain_of(edl):
    """The EDL mix block's uniform gain in dB (absent, null or 0: no change)."""
    try:
        return float(((edl or {}).get("mix") or {}).get("gain_db") or 0.0)
    except (TypeError, ValueError, AttributeError):
        return 0.0


def print_result(result, lines, wrote=()):
    print("RESULT: %s" % result)
    for ln in lines:
        print(ln)
    for w in wrote:
        print("WROTE %s" % posix(w))


def cmd_validate(lab, edl_path, as_json=False):
    edl = load_edl(edl_path)
    errs, warns = validate(edl, lab)
    res = "STOP" if errs else ("WARN" if warns else "OK")
    out = {"schema": "resolve-editor/validate@1", "edl": posix(edl_path), "result": res, "errors": errs, "warnings": warns}
    if as_json:
        print(json.dumps(out, indent=1))
        return out
    print_result(res, ["ERROR %s %s: %s" % (e["code"], e["item"] or "-", e["msg"]) for e in errs]
                 + ["WARN %s %s: %s" % (w["code"], w["item"] or "-", w["msg"]) for w in warns])
    return out


# ------------------------------------------------------------------------------------------------------ diff, adopt
def _item_index(edl):
    out = {}
    for kind, tr, it in iter_items(edl):
        out[it.get("id")] = (tr.get("id"), it)
    return out


def _short_json(v, n=48):
    s = json.dumps(v, sort_keys=True)
    return s if len(s) <= n else s[:n - 3] + "..."


def _param_changes(a, b, prefix, depth=0):
    """["<prefix><key> old -> new", ...] for every key that differs (nested dicts by their keys)."""
    out = []
    for k in sorted(set(a) | set(b), key=str):
        p, q = a.get(k), b.get(k)
        if p == q:
            continue
        if isinstance(p, dict) and isinstance(q, dict) and depth < 3:
            out += _param_changes(p, q, "%s%s." % (prefix, k), depth + 1)
        else:
            out.append("%s%s %s -> %s" % (prefix, k, _short_json(p), _short_json(q)))
    return out


def fx_changes(x, y):
    """What changed in an item's effects (fx by id, field by field), its baked motion, retime and accents, its text
    animation (id and parameters), and a title's or caption's style, role, size, look and box."""
    out = []

    def by_id(it):
        return {str(e.get("id") or "%s#%d" % (e.get("kind"), i)): e for i, e in enumerate(it.get("fx") or [])
                if isinstance(e, dict)}
    fa, fb = by_id(x), by_id(y)
    for k in sorted(set(fa) | set(fb)):
        if k not in fa:
            out.append("fx %s added (%s)" % (k, fb[k].get("kind")))
        elif k not in fb:
            out.append("fx %s removed (%s)" % (k, fa[k].get("kind")))
        else:
            out += _param_changes({kk: vv for kk, vv in fa[k].items() if kk != "why"},
                                  {kk: vv for kk, vv in fb[k].items() if kk != "why"}, "fx %s " % k)
    for key in ("motion", "retime", "accents"):
        p, q = x.get(key), y.get(key)
        if p == q:
            continue
        if isinstance(p, dict) and isinstance(q, dict):
            out += _param_changes({kk: vv for kk, vv in p.items() if kk != "keys"},
                                  {kk: vv for kk, vv in q.items() if kk != "keys"}, "%s " % key)
            if p.get("keys") != q.get("keys"):
                out.append("%s keys changed" % key)
        else:
            out.append("%s %s" % (key, "added" if not p else ("removed" if not q else "changed")))
    aa, ab = x.get("anim") or {}, y.get("anim") or {}
    if aa != ab:
        if aa.get("id") != ab.get("id"):
            out.append("anim %s -> %s" % (aa.get("id") or "none", ab.get("id") or "none"))
        out += _param_changes(aa.get("params") or {}, ab.get("params") or {}, "anim params ")
        rest = sorted(k_ for k_ in set(aa) | set(ab) if k_ not in ("id", "params") and aa.get(k_) != ab.get(k_))
        if rest:
            out.append("anim %s changed" % ", ".join(rest))
    for key in ("style", "role", "font_px"):
        if x.get(key) != y.get(key):
            out.append("%s %s -> %s" % (key, _short_json(x.get(key)), _short_json(y.get(key))))
    la, lb = x.get("look") or {}, y.get("look") or {}
    if la != lb:
        if la.get("id") != lb.get("id"):
            out.append("look %s -> %s" % (la.get("id") or "none", lb.get("id") or "none"))
        else:
            out += _param_changes(la, lb, "look ")
    if x.get("box") != y.get("box") and (x.get("box") or y.get("box")):
        out.append("box %s -> %s" % (_short_json(x.get("box")), _short_json(y.get("box"))))
    return out


def diff_edls(a, b):
    ia, ib = _item_index(a), _item_index(b)
    added = sorted(k for k in ib if k not in ia)
    removed = sorted(k for k in ia if k not in ib)
    changed = []
    for k in sorted(set(ia) & set(ib)):
        (ta, x), (tb, y) = ia[k], ib[k]
        ch = []
        if ta != tb:
            ch.append("track %s -> %s" % (ta, tb))
        if x.get("media") != y.get("media"):
            ch.append("media %s -> %s" % (x.get("media"), y.get("media")))
        if speed_of(x) != speed_of(y):
            ch.append("speed %g -> %g" % (speed_of(x), speed_of(y)))
        if "src_in" in x and "src_in" in y and x.get("media") == y.get("media"):
            dh = int(y["src_in"]) - int(x["src_in"])
            dt = int(y["src_out"]) - int(x["src_out"])
            if dh:
                ch.append("trimmed head %+d f (src %d -> %d)" % (dh, int(x["src_in"]), int(y["src_in"])))
            if dt:
                ch.append("trimmed tail %+d f (src %d -> %d)" % (dt, int(x["src_out"]), int(y["src_out"])))
        if int(x["rec_in"]) != int(y["rec_in"]):
            ch.append("moved %+d f (rec %d -> %d)" % (int(y["rec_in"]) - int(x["rec_in"]), int(x["rec_in"]), int(y["rec_in"])))
        if rec_len(x) != rec_len(y):
            ch.append("length %d -> %d f" % (rec_len(x), rec_len(y)))
        if x.get("text") != y.get("text"):
            ch.append("text %r -> %r" % (x.get("text"), y.get("text")))
        for f in ("gain_db", "fade_in", "fade_out", "transform", "volume_env", "enabled"):
            if x.get(f) != y.get(f):
                ch.append("%s %s -> %s" % (f, json.dumps(x.get(f)), json.dumps(y.get(f))))
        ch += fx_changes(x, y)
        if ch:
            changed.append({"id": k, "changes": ch})
    xa = {(t.get("from"), t.get("to"), t.get("type"), t.get("frames")) for t in a.get("transitions", [])}
    xb = {(t.get("from"), t.get("to"), t.get("type"), t.get("frames")) for t in b.get("transitions", [])}
    ma = {(m.get("frame"), m.get("name")) for m in a.get("markers", [])}
    mb = {(m.get("frame"), m.get("name")) for m in b.get("markers", [])}
    pa, pb = programme_frames(a), programme_frames(b)
    if mix_gain_of(a) != mix_gain_of(b):
        changed.append({"id": "mix", "changes": ["mix gain_db %+.1f -> %+.1f" % (mix_gain_of(a), mix_gain_of(b))]})
    return {"schema": "resolve-editor/diff@1", "added": added, "removed": removed, "changed": changed,
            "transitions": {"added": sorted(map(list, xb - xa), key=str), "removed": sorted(map(list, xa - xb), key=str)},
            "markers": {"added": sorted(map(list, mb - ma), key=str), "removed": sorted(map(list, ma - mb), key=str)},
            "length": [pa, pb],
            "summary": "%d added, %d removed, %d changed, length %d -> %d f" % (len(added), len(removed), len(changed), pa, pb)}


def cmd_diff(lab, a_path, b_path, as_json=False):
    d = diff_edls(load_edl(a_path), load_edl(b_path))
    d["a"], d["b"] = posix(a_path), posix(b_path)
    if as_json:
        print(json.dumps(d, indent=1))
        return d
    print("DIFF %s" % d["summary"])
    for k in d["added"]:
        print("added   %s" % k)
    for k in d["removed"]:
        print("removed %s" % k)
    for c in d["changed"]:
        print("changed %s: %s" % (c["id"], "; ".join(c["changes"])))
    for x in d["transitions"]["added"]:
        print("transition added %s" % x)
    for x in d["transitions"]["removed"]:
        print("transition removed %s" % x)
    for x in d["markers"]["added"]:
        print("marker added %s" % x)
    for x in d["markers"]["removed"]:
        print("marker removed %s" % x)
    return d


def adopted_versions(lab):
    d = lab.p("edits")
    if not os.path.isdir(d):
        return []
    return sorted(f for f in os.listdir(d) if re.match(r"^v\d{3,}\.json$", f))


def cmd_adopt(lab, edl_path, by=None, summary=None):
    edl = load_edl(edl_path)
    errs, _ = validate(edl, lab)
    if errs:
        raise Fail("the EDL does not validate (%s); fix it before adopting" % "; ".join(
            "%s %s" % (e["code"], e["item"]) for e in errs[:5]))
    vers = adopted_versions(lab)
    n = (int(vers[-1][1:-5]) + 1) if vers else 1
    vid = "v%03d" % n
    parent = vers[-1][:-5] if vers else None
    changes = []
    if parent:
        d = diff_edls(load_edl(lab.p("edits", parent + ".json")), edl)
        changes = ["added %s" % k for k in d["added"]] + ["removed %s" % k for k in d["removed"]] + [
            "%s %s" % (c["id"], "; ".join(c["changes"])) for c in d["changed"]]
    old = edl.get("version") or {}
    edl["version"] = {"id": vid, "parent": parent, "by": by or old.get("by") or "user", "created": iso_now(),
                      "summary": summary or old.get("summary") or "", "changes": changes[:200],
                      "from_cutlist": old.get("from_cutlist"), "from": lab.relto(edl_path)}
    out = lab.p("edits", vid + ".json")
    write_json(out, edl)
    print("WROTE %s" % posix(out))
    return out


# ------------------------------------------------------------------------------------------------------------ init
def _most_common_fps(lab):
    rates = {}
    for m in lab.index.get("media", {}).values():
        if m.get("kind") in ("av", "video") and m.get("fps"):
            try:
                f = fps_str(m["fps"])
            except ValueError:
                continue
            rates[f] = rates.get(f, 0) + float(m.get("duration_s") or 1)
    return max(rates, key=rates.get) if rates else None


def _proj_fps_str(v):
    try:
        return fps_str(v) if v not in (None, "") else None
    except (ValueError, TypeError, ZeroDivisionError):
        return None


def resolve_block_from_dump(dump):
    tl = dump.get("timeline") or {}
    pr = dump.get("project") or {}
    st = pr.get("settings") or {}
    return {"product": dump.get("product"), "version": dump.get("version"), "studio": dump.get("studio"),
            "project": pr.get("name"), "project_uid": pr.get("uid"), "timeline": tl.get("name"),
            "timeline_uid": tl.get("uid"), "fps": tl.get("fps"), "width": tl.get("width"), "height": tl.get("height"),
            "project_fps": _proj_fps_str(st.get("timelineFrameRate")) or tl.get("fps"),
            "frame_rate_mismatch": st.get("timelineFrameRateMismatchBehavior"),
            "input_sizing": st.get("timelineInputResMismatchBehavior"), "retime": st.get("videoTimelineRetimeProcess")}


def cmd_init(lab, name, preset=None, fps=None, size=None, start_tc=None, premium=None, fx=None):
    if premium not in (None, "yes", "no"):
        raise Fail("--premium must be yes or no")
    if fx not in (None, "none", "light", "normal", "bold"):
        raise Fail("--fx must be none, light, normal or bold")
    os.makedirs(lab.path, exist_ok=True)
    for d in ("edits", "wf", "snippets"):
        os.makedirs(lab.p(d), exist_ok=True)
    old = lab.project
    dump = load_json_or(lab.p("resolve", "dump.json"), None)
    pid = preset or old.get("preset")
    P = load_preset(pid, lab) if pid else copy.deepcopy(DEFAULT_PRESET)
    dtl = (dump or {}).get("timeline") or {}
    f = None
    for cand in (fps, ((dump or {}).get("project") or {}).get("settings", {}).get("timelineFrameRate"), dtl.get("fps"),
                 None if str(P["timeline"].get("fps")) == "project" else P["timeline"].get("fps"),
                 (old.get("timeline") or {}).get("fps"), _most_common_fps(lab), "25/1"):
        if cand in (None, "", "project"):
            continue
        try:
            f = parse_fps(cand)
            break
        except (ValueError, TypeError):
            if cand is fps:
                raise Fail("--fps %r is not a frame rate (use 25, 29.97 or 30000/1001)" % fps)
    if size:
        m = re.match(r"^\s*(\d+)\s*[xX]\s*(\d+)\s*$", size)
        if not m:
            raise Fail("--size must look like 1080x1920")
        W, H = int(m.group(1)), int(m.group(2))
    elif pid and P["timeline"].get("width"):
        W, H = int(P["timeline"]["width"]), int(P["timeline"]["height"])
        ps_ = ((dump or {}).get("project") or {}).get("settings") or {}
        try:
            pw_ = int(float(ps_.get("timelineResolutionWidth") or 0))
            ph_ = int(float(ps_.get("timelineResolutionHeight") or 0))
        except (TypeError, ValueError):
            pw_ = ph_ = 0
        # a project of the preset's shape keeps its own raster (a UHD project gets a UHD edit and a UHD timeline);
        # only a project of another shape (a vertical piece from a 16:9 project) gets the preset's raster, which the
        # build then sets as the new timeline's own settings
        if pw_ > 0 and ph_ > 0 and abs(pw_ * H - ph_ * W) <= 0.01 * pw_ * H:
            W, H = pw_, ph_
    elif dtl.get("width"):
        W, H = int(dtl["width"]), int(dtl["height"])
    else:
        W, H = 1920, 1080
    stc = start_tc or dtl.get("start_tc") or (old.get("timeline") or {}).get("start_tc") or "01:00:00:00"
    try:
        tc_to_frames(stc, f)
    except ValueError as e:
        raise Fail("--start-tc: %s" % e)
    proj = {"schema": "resolve-editor/project@1", "name": name or old.get("name") or os.path.basename(lab.path),
            "created": old.get("created") or iso_now(), "preset": pid,
            "mode": ("full" if (dump or {}).get("studio") else ("handoff" if dump else "offline")),
            "timeline": {"fps": fps_str(f), "width": W, "height": H, "start_tc": stc, "audio_rate": SR},
            "resolve": resolve_block_from_dump(dump) if dump else (old.get("resolve") or {})}
    # the brief's effects answers (FUSION_PLAN s2.1): a premium or luxury brand takes the premium budget, and the
    # appetite scales every preset's effects budget (none 0, light 0.5, normal 1, bold 1.25)
    if premium is not None or "premium" in old:
        proj["premium"] = (premium == "yes") if premium is not None else yes_no(old.get("premium"),
                                                                                 "project.json premium")
    if fx is not None or "fx_appetite" in old:
        proj["fx_appetite"] = fx or old.get("fx_appetite") or "normal"
    fn = write_json(lab.p("project.json"), proj)
    print("WROTE %s" % posix(fn))
    print("lab %s  preset %s  timeline %dx%d %s fps start %s  mode %s%s" % (
        posix(lab.path), pid or "(none)", W, H, fps_label(f), stc, proj["mode"],
        ("  premium %s  effects %s" % ("yes" if proj.get("premium") else "no", proj.get("fx_appetite", "normal")))
        if ("premium" in proj or "fx_appetite" in proj) else ""))
    return proj


# ------------------------------------------------------------------------------------------- words on the timeline
EPS_S = 1e-6


def timeline_words(edl, lab):
    """Transcript words heard on the timeline through audio items (music tracks excluded).

    An edge that removes more than 20 ms of a word while more than 20 ms stays is CLIPPED; a word with 20 ms or less
    left on the timeline is not listed (a sliver of a neighbour's tail)."""
    tfps = tl_fps(edl)
    out = []
    for tr in edl["tracks"].get("audio", []):
        if tr.get("role") == "music":
            continue
        for it in tr.get("items", []):
            if not is_media_item(it) or it.get("enabled") is False:
                continue
            m = edl["media"].get(it["media"]) or {}
            wf = m.get("words")
            data = load_words_file(lab.rel(wf) if lab else wf) if wf else None
            if not data or not data.get("words"):
                continue
            mf = float(media_fps(edl, it["media"]))
            sp = speed_of(it)
            a = int(it["src_in"]) / mf
            b = a + rec_len(it) / float(tfps) * sp
            r0 = int(it["rec_in"]) / float(tfps)
            # an edge that no frame (or no placeable length) can put between two abutting words may run up to one
            # source frame into a word (edge_tol); check reports that as rate_edge instead
            thr_out = max(0.020, float((it.get("edge_tol") or {}).get("out") or 0.0))
            thr_in = max(0.020, float((it.get("edge_tol") or {}).get("in") or 0.0))
            for w in data["words"]:
                t0, t1 = float(w["t0"]), float(w["t1"])
                if t1 <= a or t0 >= b:
                    continue
                cut_in = (a - t0) if t0 < a else 0.0
                cut_out = (t1 - b) if t1 > b else 0.0
                kept = min(t1, b) - max(t0, a)
                # 20 ms is the contract; EPS keeps an edge exactly half a 25 fps frame into a word (whisper times
                # sit on a 20 ms grid) from counting through float error
                if (cut_in > 0 or cut_out > 0) and kept <= (thr_out if cut_out > 0 else thr_in) + EPS_S:
                    continue
                clipped = cut_in > thr_in + EPS_S or cut_out > thr_out + EPS_S
                out.append({"i": w.get("i"), "w": w.get("w", ""), "media": it["media"], "item": it["id"],
                            "track": tr.get("id"), "t0": round(r0 + (max(t0, a) - a) / sp, 4),
                            "t1": round(r0 + (min(t1, b) - a) / sp, 4), "src_t0": t0, "src_t1": t1,
                            "speaker": w.get("speaker") or "S1", "tags": list(w.get("tags") or []),
                            "clipped": clipped, "cut_s": round(max(cut_in, cut_out), 3),
                            "edge": "in" if cut_in >= cut_out else "out"})
    out.sort(key=lambda r: (r["t0"], r["t1"]))
    return out


def _nw_(s):
    return re.sub(r"[^a-z0-9']", "", str(s).lower().replace("’", "'")).strip("'")


def _camel(t):
    """A word written with a capital inside and a small first letter (iPhone, eBay, iOS) keeps its first letter
    small when it opens a sentence."""
    return any(c.isupper() for c in str(t)[1:])


def joined_head(W, i):
    """The first part of the spoken word that transcript word i belongs to (i itself unless M script tagged it
    joined: a later part of one word whisper wrote as several)."""
    if not 0 < i < len(W) or "joined" not in (W[i].get("tags") or []):
        return i
    jt = W[i].get("joined_to")
    if isinstance(jt, int) and 0 <= jt < i:
        return jt
    k = i - 1
    while k > 0 and "joined" in (W[k].get("tags") or []):
        k -= 1
    return k


def joined_parts(W, h):
    """The later parts (joined words) of word h, in order ([] for most words)."""
    return [k for k in range(h + 1, min(len(W), h + 13)) if "joined" in (W[k].get("tags") or []) and
            joined_head(W, k) == h]


def joined_last(W, h):
    parts = joined_parts(W, h)
    return parts[-1] if parts else h


def words_changed(edl, lab):
    """Dialogue items whose heard words differ from the words the cut list asked for (their `words` range).

    A word counts as heard when more than 20 ms of it (half of it for a shorter word) plays. Returns a list of
    {item, words, lost: [(i, text)], added: [(i, text)], at}."""
    out = []
    tfps = tl_fps(edl)
    for tr in edl["tracks"].get("audio", []):
        if tr.get("role") == "music":
            continue
        for it in tr.get("items", []):
            if not is_media_item(it) or not it.get("words") or it.get("enabled") is False:
                continue
            m = edl["media"].get(it["media"]) or {}
            wf = m.get("words")
            data = load_words_file(lab.rel(wf) if lab else wf) if wf else None
            W = (data or {}).get("words") or []
            if not W:
                continue
            f, l = int(it["words"][0]), int(it["words"][-1])
            mf = float(media_fps(edl, it["media"]))
            a = int(it["src_in"]) / mf
            b = a + rec_len(it) / float(tfps) * speed_of(it)
            heard = set()
            tol_out = float((it.get("edge_tol") or {}).get("out") or 0.0)
            tol_in = float((it.get("edge_tol") or {}).get("in") or 0.0)
            for k, w in enumerate(W):
                t0, t1 = float(w["t0"]), float(w["t1"])
                thr = min(0.020, 0.5 * max(0.0, t1 - t0))
                if t1 > b and k > l:
                    thr = max(thr, tol_out)
                if t0 < a and k < f:
                    thr = max(thr, tol_in)
                if min(t1, b) - max(t0, a) > thr + EPS_S and t1 > a and t0 < b:
                    heard.add(k)
            want = set(range(f, l + 1))
            lost, added = sorted(want - heard), sorted(heard - want)
            if lost or added:
                out.append({"item": it["id"], "words": [f, l], "at": int(it["rec_in"]),
                            "lost": [(i, W[i].get("w", "")) for i in lost if 0 <= i < len(W)],
                            "added": [(i, W[i].get("w", "")) for i in added]})
    return out


def words_changed_msg(wc):
    parts = []
    if wc["lost"]:
        parts.append("lost %s" % ", ".join("%r (word %d)" % (t, i) for i, t in wc["lost"]))
    if wc["added"]:
        parts.append("added %s" % ", ".join("%r (word %d)" % (t, i) for i, t in wc["added"]))
    return "%s asked for words %d..%d but %s" % (wc["item"], wc["words"][0], wc["words"][1], " and ".join(parts))


def project_fps(lab):
    """The Resolve project's timeline rate from the last dump (new timelines get it), or None when unknown."""
    if lab is None:
        return None
    r = lab.project.get("resolve") or {}
    for k in ("project_fps", "fps"):
        if r.get(k):
            try:
                return parse_fps(r[k])
            except (ValueError, TypeError, ZeroDivisionError):
                continue
    return None


def fps_mismatch_msg(edl, r_fps):
    return ("the edit runs at %s fps but the Resolve project runs at %s fps, and the build needs them equal: run "
            "`edit_lab.py LAB init --name NAME --preset ID` again (it takes the project's rate from the dump), then "
            "assemble again" % (fps_label(tl_fps(edl)), fps_label(r_fps)))


FACE_MARGIN = 0.05     # a face is inside a crop when its centre is this far (half a typical face) inside the edge


def frame_geometry(edl, it):
    """(image width, image height) in timeline pixels at zoom 1 after input sizing, and whether it fills the frame.
    None when the media size is unknown."""
    m = (edl.get("media") or {}).get(it.get("media")) or {}
    try:
        w, h = float(m.get("width") or 0), float(m.get("height") or 0)
    except (TypeError, ValueError):
        return None
    if w <= 0 or h <= 0:
        return None
    sar = str(m.get("sar") or "1:1").replace("/", ":").split(":")
    try:
        if len(sar) == 2 and float(sar[0]) > 0 and float(sar[1]) > 0:
            w *= float(sar[0]) / float(sar[1])
    except ValueError:
        pass
    if int(round(float(m.get("rotation") or 0))) % 180 == 90:
        w, h = h, w
    W, H = float(edl["timeline"]["width"]), float(edl["timeline"]["height"])
    sizing = (edl["timeline"].get("resolve") or {}).get("input_sizing") or "scaleToFit"
    if sizing in ("stretch", "scaleToStretch"):
        return W, H, True
    k = max(W / w, H / h) if sizing in ("scaleToCrop", "crop", "fill") else min(W / w, H / h)
    iw, ih = w * k, h * k
    return iw, ih, iw >= W - 1 and ih >= H - 1


_BORDER_CACHE = {}
CARD_BLACK_MAX = 24       # 8-bit luma: a still whose outer band stays at or under this is a card on black


def image_border_black(path):
    """True when a still's outer band (2 % of each side, flattened over black as Resolve draws it) is black: shrunk
    or moved on a black frame, its edge cannot show."""
    try:
        st = os.stat(path)
    except (OSError, TypeError):
        return False
    key = (path, st.st_size, st.st_mtime_ns)
    if key not in _BORDER_CACHE:
        res = False
        try:
            from PIL import Image
            with Image.open(path) as im0:
                rgba = im0.convert("RGBA")
                flat = Image.new("RGB", rgba.size, (0, 0, 0))
                flat.paste(rgba, mask=rgba.getchannel("A"))
                im = flat.convert("L")
                im.thumbnail((400, 400))
                g = np.asarray(im, np.float64)
            if min(g.shape) >= 12:
                by, bx = max(2, int(round(0.02 * g.shape[0]))), max(2, int(round(0.02 * g.shape[1])))
                band = np.concatenate([g[:by].ravel(), g[-by:].ravel(), g[:, :bx].ravel(), g[:, -bx:].ravel()])
                res = float(band.max()) <= CARD_BLACK_MAX
        except Exception:
            res = False
        _BORDER_CACHE[key] = res
    return _BORDER_CACHE[key]


def card_on_black(edl, it, lab=None):
    """A still with a black border on an empty frame (a logo card on black): a zoom under 1 or a move shows black
    around it, the same black as its own background, so it has no visible edge."""
    m = (edl.get("media") or {}).get(it.get("media")) or {}
    if m.get("kind") != "image":
        return False
    tf = it.get("transform") or {}
    try:
        if float(tf.get("opacity", 100) if tf.get("opacity") is not None else 100) < 99.95 or \
                abs(float(tf.get("rotation", 0) or 0)) > 1e-6:
            return False
    except (TypeError, ValueError):
        return False
    path = m.get("image") or m.get("path")
    return image_border_black(lab.rel(path) if lab is not None else path)


def frame_edge_issues(edl, lab=None):
    """Picture items whose pan, tilt or zoom shows the black past the image's edge: [(item, rec_in, msg, fix)]."""
    out = []
    W, H = float(edl["timeline"]["width"]), float(edl["timeline"]["height"])
    for tr in edl["tracks"].get("video", []):
        tn = track_num(tr.get("id"))
        for it in tr.get("items", []):
            if not is_media_item(it) or it.get("enabled") is False:
                continue
            tf = it.get("transform") or {}
            try:
                z = float(tf.get("zoom", 1.0) or 1.0)
                pan, tilt = float(tf.get("pan_px", 0) or 0), float(tf.get("tilt_px", 0) or 0)
            except (TypeError, ValueError):
                continue
            if tn > 1 and z < 1.0 - 1e-6:
                continue                          # a picture in picture on an overlay track
            geo = frame_geometry(edl, it)
            if geo is None or not geo[2]:
                continue                          # size unknown, or fitted with bars on purpose
            if card_on_black(edl, it, lab):
                continue                          # a logo card on black: the frame around it is its own black
            iw, ih = geo[0], geo[1]
            rx, ry = (iw * z - W) / 2.0, (ih * z - H) / 2.0
            bad = []
            if z < 1.0 - 1e-6:
                bad.append("zoom %g leaves borders" % z)
            # a zoom under 1 already says why the edges show; a pan or tilt of 0 adds nothing to that
            if pan and abs(pan) > rx + 1:
                bad.append("pan_px %g is past the %d px of spare width" % (pan, max(0, int(rx))))
            if tilt and abs(tilt) > ry + 1:
                bad.append("tilt_px %g is past the %d px of spare height" % (tilt, max(0, int(ry))))
            if bad:
                need = max(1.0, (W + 2 * abs(pan)) / iw, (H + 2 * abs(tilt)) / ih)
                out.append((it["id"], int(it["rec_in"]), "%s shows a black edge: %s" % (it["id"], "; ".join(bad)),
                            "zoom %s to at least %.3f, or keep pan_px within +-%d and tilt_px within +-%d" % (
                                it["id"], need, max(0, int(rx)), max(0, int(ry)))))
    return out


# ------------------------------------------------------------------------------------------------------- assemble
def _ms_pair(v, default):
    if v is None:
        v = default
    if isinstance(v, (int, float)):
        return float(v) / 1000.0, float(v) / 1000.0
    return float(v[0]) / 1000.0, float(v[-1]) / 1000.0


def edl_media_entry(lab, mid, tfps):
    m = lab.media(mid)
    kind = m.get("kind") or "av"
    an = m.get("analysis") or {}
    e = {"kind": kind, "name": m.get("name"), "path": m.get("path"), "hash": m.get("hash")}
    if kind in ("audio", "image"):
        e["fps"], e["fps_from"] = fps_str(tfps), "timeline"
        dur = m.get("duration_s")
        if dur is None and m.get("samples") and (m.get("audio") or {}).get("rate"):
            dur = m["samples"] / float(m["audio"]["rate"])
        e["frames"] = int(math.floor(float(dur) * float(tfps) + 1e-6)) if (kind == "audio" and dur) else None
    else:
        e["fps"], e["fps_from"] = fps_str(m["fps"]), "media"
        e["frames"] = m.get("frames")
    flags = (m.get("summary") or {}).get("flags") or []
    e.update({"start_tc": m.get("start_tc") or "00:00:00:00", "width": m.get("width"), "height": m.get("height"),
              "rotation": m.get("rotation") or 0, "sar": m.get("sar"), "audio": m.get("audio"), "proxy": m.get("proxy"), "wav": m.get("wav"), "words": an.get("words"),
              "shots": an.get("shots"), "loudness": an.get("loudness"),
              "flat_log": bool((m.get("color") or {}).get("flat_log_guess") or "flat_log" in flags),
              "mpi_uid": (m.get("resolve") or {}).get("mpi_uid")})
    if "vfr" in flags:
        e["vfr"] = True
    if {"one_sided", "split_channels", "two_voices"} & set(flags):
        e["stereo"] = "one_sided" if "one_sided" in flags else "two_voices" if "two_voices" in flags else "split"
    if kind == "image":
        e["image"] = m.get("path")
    return e


def _shot_lookup(lab, shot_id):
    if ".s" not in str(shot_id):
        raise Fail("shot id %r should look like <media id>.s01" % shot_id)
    mid = str(shot_id).rsplit(".s", 1)[0]
    data = lab.analysis(mid, "shots")
    if not data:
        raise Fail("no shots for media %s: run media_lab.py shots first" % mid)
    for s in data.get("shots", []):
        if s.get("id") == shot_id:
            return mid, int(s["in"]), int(s["out"])
    raise Fail("shot %s is not in the shot list of %s" % (shot_id, mid))


FUNCTION_WORDS = {"a", "an", "the", "to", "of", "with", "and", "but", "or", "for", "in", "on", "at", "by", "from", "into",
                  "is", "are", "was", "were", "be", "does", "do", "did", "has", "have", "had", "will", "can", "my",
                  "your", "our", "their", "his", "her", "its", "this", "that", "these", "those", "as", "if", "so"}


def _function_word(w):
    t = str(w).strip()
    return bool(t) and t[-1].isalpha() and t.lower() in FUNCTION_WORDS


def _ends_sentence(w):
    """A word ends a sentence on . ? or !, except a list item's number ("1.", "2)"), which opens its item."""
    s = str(w).rstrip()
    return s.endswith((".", "?", "!")) and not re.fullmatch(r"\(?\d{1,3}[.)]", s)


def _ends_phrase(w):
    """A caption ends after this word: its sentence ends, or it ends a list item of the script (M script tags it
    line_end: "Keep it clean" on a bullet line)."""
    return _ends_sentence(w.get("w", "")) or "line_end" in (w.get("tags") or [])


def _wrap(text, n):
    return textwrap.wrap(str(text), max(4, int(n)), break_long_words=False, break_on_hyphens=False) or [""]


def _wrap_cue(text, n, keep=()):
    """_wrap for a caption: when it gives two lines and the second is one word, or the break splits a
    captions.keep_together phrase, the break moves to the most even place where both lines fit and no phrase is
    split (a single word stays alone only when nothing else fits)."""
    lines = _wrap(text, n)
    ws = str(text).split()
    if len(lines) != 2 or len(ws) < 3:
        return lines
    nw = [_nw_(x) for x in ws]
    glue = set()
    for ph in keep or ():
        pt = [_nw_(x) for x in str(ph).split() if _nw_(x)]
        for j in range(len(ws) - len(pt) + 1):
            if len(pt) >= 2 and nw[j:j + len(pt)] == pt:
                glue.update(range(j, j + len(pt) - 1))
    k0 = len(lines[0].split())
    if len(lines[1].split()) > 1 and (k0 - 1) not in glue:
        return lines
    best = None
    for k in range(1, len(ws)):
        a, b = " ".join(ws[:k]), " ".join(ws[k:])
        if len(a) > max(4, int(n)) or len(b) > max(4, int(n)):
            continue
        score = ((k - 1) in glue, k == 1 or k == len(ws) - 1, abs(len(a) - len(b)))
        if best is None or score < best[0]:
            best = (score, [a, b])
    return best[1] if best else lines


def char_em(look=None):
    """Average character width in em: capitals run wider than mixed case."""
    return 0.64 if (look or {}).get("case") == "upper" else 0.58


def look_pad(look=None):
    """Box padding [x, y] in em: the look's box, else the margin the text box always had (0.4, 0.2)."""
    bx = (look or {}).get("box") or {}
    pe = bx.get("pad_em") if isinstance(bx, dict) else None
    try:
        return [float(pe[0]), float(pe[-1])]
    except (TypeError, ValueError, IndexError):
        return [0.4, 0.2]


def text_width(lines, font_px, look=None):
    """Estimated width in pixels of the widest line (about 0.58 em per character plus the box margin)."""
    lw = max(len(l) for l in lines) if lines else 1
    return int(round(lw * font_px * char_em(look) + font_px * 2 * look_pad(look)[0]))


def text_box(lines, font_px, cx, cy, sbox, W, H, look=None):
    """Box [x, y, w, h] for lines of text at font_px, centred at (cx, cy), kept inside the safe box when it fits.
    The look sets the line pitch (line_spacing em) and the padding; without one the box is what it always was."""
    lw = max(len(l) for l in lines) if lines else 1
    pad = look_pad(look)
    pitch = float((look or {}).get("line_spacing") or 1.25)
    bw = min(sbox[2], lw * font_px * char_em(look) + font_px * 2 * pad[0])
    bh = len(lines) * font_px * pitch + font_px * 2 * pad[1]
    x = cx - bw / 2
    y = cy - bh / 2
    x = min(max(x, sbox[0]), sbox[0] + sbox[2] - bw)
    y = min(max(y, sbox[1]), sbox[1] + sbox[3] - bh)
    return [int(round(x)), int(round(y)), int(round(bw)), int(round(bh))]


# ------------------------------------------------------------------------------------------ text looks and roles
TITLE_ROLES = ("hook", "offer", "proof", "brand", "cta", "name", "other")
TITLE_STYLES = ("hook_top", "lower_third", "center", "super", "cta", "end_card")
CARD_STYLES = ("end_card", "cta")      # these centre on the frame (W / 2, on the logo), not on the safe box
LOOK_CASES = ("as_written", "upper", "sentence")
HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def load_brand(lab):
    """LAB/brand.json (resolve-editor/brand@1) or None."""
    b = load_json_or(lab.p("brand.json"), None) if lab is not None else None
    return b if isinstance(b, dict) else None


def brand_color(tok, brand, fallback):
    """A #RRGGBB colour: as given, or "brand.<name>" from brand.json colors, else the fallback."""
    if isinstance(tok, str) and tok.startswith("brand."):
        c = ((brand or {}).get("colors") or {}).get(tok[6:])
        return c.upper() if isinstance(c, str) and HEX_RE.match(c) else fallback
    return tok.upper() if isinstance(tok, str) and HEX_RE.match(tok) else fallback


def resolve_look(P, look_id, brand, default_id="boxed", caption=False):
    """(look with every field resolved, note or None). Brand tokens resolve from brand.json; without it the font is
    Arial Bold, text is white and box, stroke and shadow colours are black."""
    looks = P.get("looks") or {}
    base_default = DEFAULT_PRESET["looks"].get(default_id) or DEFAULT_PRESET["looks"]["boxed"]
    note, lid = None, look_id or default_id
    if lid not in looks:
        if look_id:
            note = "look %r is not in the preset's looks; %s used" % (look_id, default_id)
        lid = default_id
    lk = merge(base_default, looks.get(lid) or {})
    font, style, ffile = lk.get("font") or "Arial", lk.get("style") or "Bold", lk.get("font_file")
    if font == "brand":
        b = brand or {}
        bf = (b.get("caption_font") if caption and isinstance(b.get("caption_font"), dict) else b.get("font")) or {}
        font = bf.get("family") or "Arial"
        style = bf.get("style") or style
        ffile = bf.get("file") or ffile
    st, sh, bx = lk.get("stroke"), lk.get("shadow"), lk.get("box")

    def num(d, k, dflt):
        try:
            return float(d.get(k, dflt))
        except (TypeError, ValueError, AttributeError):
            return float(dflt)
    out = {"id": lid, "font": str(font), "style": str(style), "font_file": ffile,
           "case": lk.get("case") if lk.get("case") in LOOK_CASES else "as_written",
           "color": brand_color(lk.get("color"), brand, "#FFFFFF"),
           "stroke": {"color": brand_color(st.get("color"), brand, "#000000"), "em": num(st, "em", 0.08)}
           if isinstance(st, dict) else None,
           "shadow": {"color": brand_color(sh.get("color"), brand, "#000000"), "opacity": num(sh, "opacity", 0.5)}
           if isinstance(sh, dict) else None,
           "box": {"color": brand_color(bx.get("color"), brand, "#000000"), "opacity": num(bx, "opacity", 0.6),
                   "pad_em": [float(v) for v in (bx.get("pad_em") or [0.4, 0.2])][:2],
                   "round": num(bx, "round", 0.0)} if isinstance(bx, dict) else None,
           "line_spacing": num(lk, "line_spacing", 1.25), "align": lk.get("align") or "center"}
    return out, note


def apply_case(text, case):
    t = str(text)
    if case == "upper":
        return t.upper()
    if case == "sentence":
        s = t.lstrip()
        return t[:len(t) - len(s)] + s[:1].upper() + s[1:]
    return t


def default_role(style, at_s):
    if style == "hook_top" and at_s < 3.0:
        return "hook"
    if style in ("cta", "end_card"):
        return "cta"
    if style == "lower_third":
        return "name"
    return "other"


# music ducking under speech (Assembler.duck_env) and the duck_pump check that guards it
DUCK_PRE_S, DUCK_POST_S = 0.30, 0.25       # a speech region runs from this before a word to this after it
DUCK_ATTACK_S, DUCK_RELEASE_S = 0.25, 0.80
DUCK_PUMP_S = 2.5                          # a rise and fall of 4 dB or more within this is a pump
DUCK_PUMP_DB = 4.0
DUCK_OPEN_MIN_S = 1.0                      # a release must give the music at least this long at the open level
DUCK_START_S = 1.5                         # speech this close to the music's start: the music starts ducked
DUCK_END_HOLD_S = 2.0                      # no release in the last this many seconds of the programme
DUCK_EARLY_S = 0.3                         # a duck ramp that starts this early reads as the bed opening loud
DUCK_LIFT_RISE_S, DUCK_LIFT_FALL_S = 0.20, 0.15   # a lift ("lift": true on a picture-only spine segment): the bed
DUCK_LIFT_OPEN_S = 0.25                    # rises this fast after the last word and falls this fast before the next,
                                           # and needs at least DUCK_LIFT_OPEN_S at full level in between


class Assembler:
    def __init__(self, lab, cl, cutlist_path=None, out_path=None):
        self.lab, self.cl, self.cutlist_path, self.out_path = lab, cl, cutlist_path, out_path
        self.rep = {"schema": "resolve-editor/assemble@1", "cutlist": posix(cutlist_path) if cutlist_path else None,
                    "result": "OK", "snaps": [], "pads": [], "fillers": {"dropped": [], "kept": []},
                    "pauses_shortened": [], "beats": [], "warnings": [], "errors": []}
        proj = lab.project
        self.pid = cl.get("preset") or proj.get("preset")
        if self.pid and preset_path(self.pid, lab) is None:
            self.warn("preset_unknown", None, "preset %r not found; built-in defaults used" % self.pid)
        self.P = load_preset(self.pid, lab, required=False)
        self.platform = cl.get("platform") or self.P.get("platform")
        tlc, ptl = cl.get("timeline") or {}, proj.get("timeline") or {}
        f = tlc.get("fps") or ptl.get("fps")
        if not f and str(self.P["timeline"].get("fps")) != "project":
            f = self.P["timeline"].get("fps")
        f = f or _most_common_fps(lab) or "25/1"
        try:
            self.tfps = parse_fps(f)
        except ValueError:
            raise Fail("timeline fps %r is not a frame rate" % f)
        self.W = int(tlc.get("width") or ptl.get("width") or self.P["timeline"]["width"])
        self.H = int(tlc.get("height") or ptl.get("height") or self.P["timeline"]["height"])
        self.start_tc = tlc.get("start_tc") or ptl.get("start_tc") or "01:00:00:00"
        self.media = {}
        self.v = {1: []}
        self.a = {}
        self.sub = []
        self.sub_suppressed = []     # caption cues suppress_under_titles took off the picture (kept for the SRT)
        self.seg_items = {}          # cut list segment id -> [video item ids], [audio item ids]
        self.words_played = {}       # media id -> (segment id, word indexes) of the last spine segment that used it
        self.fx_warned = set()
        self.beat_frames, self.downbeat_frames, self.music_block = [], [], None
        self.music_src = None          # (in_s, at_s, downbeats in song seconds) of the grid music, for hints
        self.first_beat_seg = True
        self.keeps = keeps_duration()
        tol = beat_tol({"timeline": {"fps": str(self.tfps)}}, self.P)
        self.btol = (int(tol[0]), int(tol[1]))
        self.beat_early = abs(min(0.0, float(tol[0])))
        self.shotlog = {}
        sl = load_json_or(lab.p("shotlog.json"), None)
        for s in (sl or {}).get("shots", []):
            self.shotlog[s.get("shot")] = s
        self.brand = load_brand(lab)
        # effects (FUSION_PLAN s2): the brief's premium and appetite answers, clip-pair transition sides per item
        if cl.get("premium") is not None:
            self.premium = yes_no(cl.get("premium"), "the cut list's premium")
        else:
            self.premium = yes_no(proj.get("premium"), "project.json premium")
        self.appetite = str(cl.get("fx_appetite") or proj.get("fx_appetite") or "normal")
        if self.appetite not in ("none", "light", "normal", "bold"):
            raise Fail("fx_appetite must be none, light, normal or bold (got %r)" % self.appetite)
        self.fx_used = False
        self.pairs = {}
        self.sfx_tracks = set()

    # -- report helpers
    def warn(self, code, seg, msg):
        self.rep["warnings"].append({"code": code, "seg": seg, "msg": msg})

    def error(self, code, seg, msg):
        self.rep["errors"].append({"code": code, "seg": seg, "msg": msg})

    def use(self, mid):
        if mid not in self.media:
            self.media[mid] = edl_media_entry(self.lab, mid, self.tfps)
        return self.media[mid]

    def mfps(self, mid):
        m = self.use(mid)
        return self.tfps if m.get("fps_from") == "timeline" else parse_fps(m["fps"])

    def r(self, mid, speed=1.0):
        return float(speed) * float(self.mfps(mid) / self.tfps)

    def frames_of(self, mid):
        fr = self.use(mid).get("frames")
        return int(fr) if fr is not None else None

    def num(self, seg, key, default, positive=False, what="segment"):
        """A number from the cut list, or Fail with a message the cutter can act on (no traceback)."""
        v = seg.get(key, default)
        if v is None:
            v = default
        if isinstance(v, bool):
            v = None
        # a transition has no id of its own: its "what" names it ("transition after s02 (zoom)")
        lbl = what if seg.get("id") is None else "%s %s" % (what, seg.get("id"))
        try:
            x = float(v)
        except (TypeError, ValueError):
            raise Fail("%s: %s must be a number (got %r)" % (lbl, key, seg.get(key)))
        if not math.isfinite(x) or (positive and x <= 0):
            raise Fail("%s: %s must be above 0 (got %r)" % (lbl, key, seg.get(key)))
        return x

    def speed(self, seg, what="segment"):
        return self.num(seg, "speed", 1.0, positive=True, what=what)

    # -- lengths Resolve can place (see placed_len) and the source they need
    def placeable(self, mid, n, speed=1.0):
        if n < 1:
            return False
        if abs(float(speed) - 1.0) > 1e-12 and not self.keeps:
            return True
        return src_for_len(n, self.mfps(mid), self.tfps) is not None

    def fit_len(self, mid, n, speed=1.0, sid=None):
        """The longest length at or below n that Resolve can place for this clip (media slower than the timeline
        skips some lengths); the next one up when nothing below fits."""
        if n < 1 or self.placeable(mid, n, speed):
            return n
        mf = self.mfps(mid)
        k = nearest_placeable(n, mf, self.tfps) or nearest_placeable(n, mf, self.tfps, up=True)
        self.rep["snaps"].append({"seg": sid, "edge": "length", "moved_f": k - n,
                                  "why": "a %s fps clip on a %s fps timeline cannot be %d frames long in Resolve"
                                         % (fps_label(mf), fps_label(self.tfps), n)})
        return k

    def need_src(self, mid, n, speed=1.0):
        """Source frames after the in point that an item of n timeline frames needs: what it plays and, when Resolve
        places it at 100 % first (keeps_duration), what that placement takes."""
        play = max(1, rnd(n * self.r(mid, speed)))
        if abs(float(speed) - 1.0) < 1e-12 or self.keeps:
            k = src_for_len(n, self.mfps(mid), self.tfps)
            if k is None:
                k = int(math.ceil(n * float(self.mfps(mid) / self.tfps) - 1e-9))
            return max(play, k)
        return play

    def fit_source(self, mid, src_in, n, speed, fr):
        """The longest placeable length at or below n whose source fits inside the media from src_in."""
        while n >= 1 and (src_in + self.need_src(mid, n, speed) > fr or not self.placeable(mid, n, speed)):
            n -= 1
        return n

    def shot_floor(self, seg):
        """The first source frame a segment may use when its in point has to move earlier (its shot's start)."""
        if seg.get("shot"):
            try:
                return _shot_lookup(self.lab, seg["shot"])[1]
            except Fail:
                return 0
        return 0

    def words_of(self, mid):
        tr = self.lab.analysis(mid, "words") or {}
        return tr.get("words") or []

    def rec_of_src_time(self, it, t):
        """Record frame (float) at which source time t (seconds) of this item's media plays."""
        mf = float(self.mfps(it["media"]))
        return int(it["rec_in"]) + (t * mf - int(it["src_in"])) / self.r(it["media"], speed_of(it))

    # -- music first (the beat grid is needed by the spine)
    def prepare_music(self):
        for mu in self.cl.get("music", []) or []:
            mid = mu.get("media")
            if not mid:
                self.error("music_media", mu.get("id"), "music item without media")
                continue
            self.use(mid)
            if self.music_block is None or mu.get("grid"):
                off = rnd((float(mu.get("at_s", 0.0)) - float(mu.get("in_s", 0.0))) * float(self.tfps))
                beats = self.lab.analysis(mid, "beats")
                bpath = ((self.lab.media(mid).get("analysis") or {}).get("beats"))
                self.music_block = {"media": mid, "beats": bpath, "offset_frames": off}
                if beats:
                    self.beat_frames = [round(off + float(b) * float(self.tfps), 6) for b in beats.get("beats", [])]
                    self.downbeat_frames = [round(off + float(b) * float(self.tfps), 6)
                                            for b in (beats.get("downbeats") or beats.get("beats") or [])]
                    self.music_src = (float(mu.get("in_s", 0.0)), float(mu.get("at_s", 0.0)),
                                      [float(b) for b in (beats.get("downbeats") or beats.get("beats") or [])])
                elif mu.get("grid"):
                    self.warn("no_beats", mu.get("id"), "music %s has no beats.json; beat segments fall back to "
                                                        "their natural length" % mid)

    # -- source ranges
    def word_runs(self, seg, mid):
        tr = self.lab.analysis(mid, "words")
        if not tr or not tr.get("words"):
            raise Fail("segment %s uses words but media %s has no transcript (run media_lab.py transcribe)" % (seg.get("id"), mid))
        W = tr["words"]
        ws = seg["words"] if isinstance(seg["words"], (list, tuple)) and seg["words"] else [None]
        if not all(isinstance(x, int) or (isinstance(x, float) and x.is_integer()) for x in ws):
            raise Fail("segment %s: words must be two whole word numbers [first, last] from the transcript (got %r)"
                       % (seg.get("id"), seg["words"]))
        a, b = int(ws[0]), int(ws[-1])
        if not (0 <= a <= b < len(W)):
            raise Fail("segment %s: words %s outside 0..%d" % (seg.get("id"), seg["words"], len(W) - 1))
        # one spoken word the transcript wrote as several parts (M script tags the later parts joined, `+` in M
        # transcript) is one word: a range that ends on its first part runs to its last part, a range that starts on
        # a later part starts on its first part, and a drop takes the whole word or none of it. When the segment of
        # this media just before it in the spine already plays that word (its range ran over the whole word), the
        # range starts after the word instead: [0, 1] then [2, 5] plays every word once
        a0 = a
        b = joined_last(W, joined_head(W, b))
        a = joined_head(W, a)
        if a != a0:
            psid, played = self.words_played.get(mid) or (None, set())
            after = joined_last(W, a) + 1
            if a in played and after <= b:
                self.warn("joined_split", seg.get("id"), "words %s start on word %d, a part of word %d %r (one spoken "
                          "word) that %s already plays: the range starts at word %d so the word plays once; start at "
                          "word %d to say so" % (list(seg["words"]), a0, a, W[a].get("w"), psid, after, after))
                a = after
            else:
                self.warn("joined_split", seg.get("id"), "words %s start on word %d, a part of word %d %r (one spoken "
                          "word): the range starts at word %d so the whole word plays%s; start at word %d or at word "
                          "%d to say so" % (list(seg["words"]), a0, a, W[a].get("w"), a,
                                            (" (%s plays it too: the word is heard twice)" % psid) if a in played
                                            else "", a, after))
        if b != int(ws[-1]):
            self.rep["snaps"].append({"seg": seg.get("id"), "edge": "joined", "moved_f": 0, "words": [a, b],
                                      "why": "word %d %r is one spoken word with word%s %s: the range runs to word %d"
                                             % (joined_head(W, b), W[joined_head(W, b)].get("w"),
                                                "s" if b - joined_head(W, b) > 1 else "",
                                                ", ".join(str(k) for k in joined_parts(W, joined_head(W, b))), b)})
        sp = self.P["speech"]
        pad_in, pad_out = _ms_pair(seg.get("pad_ms"), sp.get("pad_ms"))
        clean = float(sp.get("clean_gap_ms", 60)) / 1000.0
        kmin, kmax = _ms_pair(seg.get("pause_keep_ms"), sp.get("pause_keep_ms"))
        hmin, hmax = _ms_pair(None, sp.get("handoff_ms"))
        mf = float(self.mfps(mid))
        speed = self.speed(seg)
        nframes = self.frames_of(mid)
        dur = (nframes / mf) if nframes else None
        drop = sorted(set(int(x) for x in (seg.get("drop") or []) if a <= int(x) <= b))
        for x in seg.get("drop") or []:
            if not a <= int(x) <= b:
                self.warn("drop_outside", seg.get("id"), "drop index %s is outside the word range" % x)
        # a drop of a joined part alone would cut the spoken word in half: refused; a drop of a first part drops
        # every part of that word
        for x in list(drop):
            h = joined_head(W, x)
            if h != x and h not in drop:
                drop.remove(x)
                self.warn("joined_split", seg.get("id"), "drop of word %d refused: it is a part of word %d %r (one "
                          "spoken word, heard as %r); drop word %d to remove the whole word" % (
                              x, h, W[h].get("w"), " ".join(str(W[k].get("asr") or W[k].get("w")) for k in
                                                              [h] + joined_parts(W, h)), h))
        drop = sorted(set(drop) | {k for x in drop for k in joined_parts(W, x) if a <= k <= b})
        # words the voice-over script lacks (M script tags them extra and filler) go by default when the preset removes
        # their tags; "keep": [index, ...] on the segment keeps one
        rm = set(self.P["speech"].get("remove") or [])
        keep_ = {int(x) for x in (seg.get("keep") or []) if isinstance(x, (int, float))}
        auto = [i for i in range(a, b + 1) if i not in keep_ and "extra" in (W[i].get("tags") or [])
                and "joined" not in (W[i].get("tags") or []) and rm & set(W[i].get("tags") or [])]
        if auto and len(set(auto) | set(drop)) < b - a + 1:
            drop = sorted(set(drop) | set(auto))
        dropped = set()
        runs, cur = [], []
        for d in drop:
            if cur and d == cur[-1] + 1:
                cur.append(d)
            else:
                if cur:
                    runs.append(cur)
                cur = [d]
        if cur:
            runs.append(cur)
        for rr in runs:
            d1, d2 = rr[0], rr[-1]
            gb = (W[d1]["t0"] - W[d1 - 1]["t1"]) if d1 > a else None
            ga = (W[d2 + 1]["t0"] - W[d2]["t1"]) if d2 < b else None
            ok = (gb is None or gb >= clean) and (ga is None or ga >= clean)
            text = " ".join(W[i]["w"] for i in rr)
            if ok:
                dropped.update(rr)
                self.rep["fillers"]["dropped"].append({"seg": seg.get("id"), "words": rr, "text": text})
            else:
                self.rep["fillers"]["kept"].append({"seg": seg.get("id"), "words": rr, "text": text,
                                                    "why": "filler_kept_unclean", "gap_before_s": gb, "gap_after_s": ga})
        keep = [i for i in range(a, b + 1) if i not in dropped]
        if not keep:
            raise Fail("segment %s drops every word" % seg.get("id"))
        groups, cur = [], [keep[0]]
        for i in keep[1:]:
            if i == cur[-1] + 1:
                cur.append(i)
            else:
                groups.append((cur, "drop"))
                cur = [i]
        groups.append((cur, "drop"))
        # split at long pauses (the pause middle is cut out)
        parts = []
        for g, _ in groups:
            cur, start_kind = [g[0]], "edge"
            for i, j in zip(g, g[1:]):
                gap = W[j]["t0"] - W[i]["t1"]
                hand = (W[i].get("speaker") or "S1") != (W[j].get("speaker") or "S1")
                lim, tgt = ((hmax, (hmin + hmax) / 2) if hand else (kmax, (kmin + kmax) / 2))
                if gap > lim + 1e-9 and joined_head(W, j) == j:
                    parts.append((cur, start_kind, ("pause", tgt)))
                    self.rep["pauses_shortened"].append({"seg": seg.get("id"), "after_word": i, "pause_s": round(gap, 3),
                                                         "kept_s": round(tgt, 3), "handoff": hand})
                    cur, start_kind = [j], ("pause", tgt)
                else:
                    cur.append(j)
            parts.append((cur, start_kind, "edge"))
        out = []
        for ws, sk, ek in parts:
            f, l = ws[0], ws[-1]
            prev = W[f - 1] if f > 0 else None
            nxt = W[l + 1] if l + 1 < len(W) else None
            if sk == "edge":
                t_in = min(W[f]["t0"], max(W[f]["t0"] - pad_in, (prev["t1"] + 0.010) if prev else 0.0))
            else:
                t_in = min(W[f]["t0"], max(W[f]["t0"] - sk[1] / 2, (prev["t1"] + 0.010) if prev else 0.0))
            if ek == "edge":
                t_out = max(W[l]["t1"], min(W[l]["t1"] + pad_out, (nxt["t0"] - 0.010) if nxt else (dur or 1e9)))
            else:
                t_out = max(W[l]["t1"], min(W[l]["t1"] + ek[1] / 2, (nxt["t0"] - 0.010) if nxt else (dur or 1e9)))
            # "edge_ms": {"in": -80, "out": 40} nudges the segment's outer edges (negative is earlier), clamped to the
            # measured pause so a nudge never reaches into a neighbouring word
            em = seg.get("edge_ms") or {}
            for side_, key_ in (("in", sk == "edge"), ("out", ek == "edge")):
                if not key_ or em.get(side_) in (None, 0):
                    continue
                try:
                    d_ = float(em[side_]) / 1000.0
                except (TypeError, ValueError):
                    raise Fail("segment %s: edge_ms.%s must be milliseconds (got %r)" % (seg.get("id"), side_, em[side_]))
                if side_ == "in":
                    lo_ = (prev["t1"] + 0.010) if prev else 0.0
                    want_ = t_in + d_
                    t_new = min(W[f]["t0"], max(lo_, want_))
                    moved_, t_in = t_new - t_in, t_new
                else:
                    hi_ = (nxt["t0"] - 0.010) if nxt else (dur or 1e9)
                    want_ = t_out + d_
                    t_new = max(W[l]["t1"], min(hi_, want_))
                    moved_, t_out = t_new - t_out, t_new
                self.rep["snaps"].append({"seg": seg.get("id"), "edge": "edge_ms_" + side_, "moved_f": 0,
                                          "asked_ms": round(1000 * d_), "moved_ms": round(1000 * moved_),
                                          "why": "edge_ms %s%s" % (side_, "" if abs(moved_ - d_) < 5e-4 else
                                                                   " (clamped to the pause)")})
            t_in = max(0.0, t_in)
            if dur:
                t_out = min(t_out, dur)
            f0 = rnd(t_in * mf)
            e0 = f0 / mf
            # when two words abut (fluent speech, no pause) no frame edge may lie within 20 ms of their boundary: at
            # 23.976 fps half a frame is 20.9 ms. Then either edge cuts into a word; keep the one that cuts less and
            # record it (edge_tol), so check reports it as rate_edge (WARN) instead of a STOP nobody can fix. Half a
            # short word is compared with EPS_S of slack: 1.2 - 1.185 is 0.01499999 in floating point
            tight_in = tight_out = None
            half_f = 0.5 * max(0.0, float(W[f]["t1"]) - float(W[f]["t0"]))
            half_l = 0.5 * max(0.0, float(W[l]["t1"]) - float(W[l]["t0"]))
            if e0 > W[f]["t0"] + 0.0005:
                if prev is None or (f0 - 1) / mf >= prev["t1"] - 0.0005:
                    f0 -= 1
                    self.rep["snaps"].append({"seg": seg.get("id"), "edge": "in", "word": f, "moved_f": -1, "why": "rounding landed inside the word"})
                else:
                    # the smaller intrusion, unless it would cut away half of the first word itself (a short word)
                    if prev["t1"] - (f0 - 1) / mf < e0 - W[f]["t0"] or e0 - W[f]["t0"] >= half_f - EPS_S:
                        f0 -= 1
                    tight_in = "abut"
                    self.warn("edge_inside_word", seg.get("id"), "in point of word %d cannot leave the word (no pause)" % f)
            elif prev is not None and e0 < prev["t1"] - 0.0005:
                if (f0 + 1) / mf <= W[f]["t0"] + 0.0005:
                    f0 += 1
                    self.rep["snaps"].append({"seg": seg.get("id"), "edge": "in", "word": f, "moved_f": 1, "why": "rounding landed inside the previous word"})
                else:
                    if (f0 + 1) / mf - W[f]["t0"] < prev["t1"] - e0 and (f0 + 1) / mf - W[f]["t0"] < half_f - EPS_S:
                        f0 += 1
                    tight_in = "abut"
                    self.warn("edge_inside_word", seg.get("id"), "in point of word %d cannot leave the word (no pause)" % f)
            f0 = max(0, f0)
            n = max(1, rnd((t_out - f0 / mf) * float(self.tfps) / speed))
            e1 = lambda n_: f0 / mf + n_ / float(self.tfps) * speed
            if e1(n) < W[l]["t1"] - 0.0005:
                if nxt is None or e1(n + 1) <= nxt["t0"] + 0.0005:
                    n += 1
                    self.rep["snaps"].append({"seg": seg.get("id"), "edge": "out", "word": l, "moved_f": 1, "why": "rounding landed inside the word"})
                else:
                    if e1(n + 1) - nxt["t0"] < W[l]["t1"] - e1(n) or W[l]["t1"] - e1(n) >= half_l - EPS_S:
                        n += 1
                    tight_out = "abut"
                    self.warn("edge_inside_word", seg.get("id"), "out point of word %d cannot leave the word (no pause)" % l)
            elif nxt is not None and e1(n) > nxt["t0"] + 0.0005 and n > 1:
                if e1(n - 1) >= W[l]["t1"] - 0.0005:
                    n -= 1
                    self.rep["snaps"].append({"seg": seg.get("id"), "edge": "out", "word": l, "moved_f": -1, "why": "rounding landed inside the next word"})
                else:
                    if W[l]["t1"] - e1(n - 1) < e1(n) - nxt["t0"] and W[l]["t1"] - e1(n - 1) < half_l - EPS_S:
                        n -= 1
                    tight_out = "abut"
                    self.warn("edge_inside_word", seg.get("id"), "out point of word %d cannot leave the word (no pause)" % l)
            if not self.placeable(mid, n, speed):
                # media slower than the timeline: Resolve skips some lengths; take the nearest one that keeps the
                # words whole, else the one that cuts least into a word
                clean, near, best = None, None, None
                for dn in (1, -1, 2, -2, 3, -3, 4, -4, 5, -5):
                    k = n + dn
                    if k < 1 or not self.placeable(mid, k, speed):
                        continue
                    if nframes is not None and f0 + self.need_src(mid, k, speed) > nframes:
                        continue
                    near = near or k
                    over = max(0.0, e1(k) - nxt["t0"]) if nxt is not None else 0.0
                    under = max(0.0, W[l]["t1"] - e1(k))
                    # never a length that cuts away half of the last word (a short "and" would be lost)
                    if under < half_l - EPS_S and (best is None or max(over, under) < best[0] - 1e-9):
                        best = (max(over, under), k)
                    if e1(k) >= W[l]["t1"] - 0.0005 and (nxt is None or e1(k) <= nxt["t0"] + 0.0005):
                        clean = k
                        break
                k = clean or (best[1] if best else near)
                if k:
                    self.rep["snaps"].append({"seg": seg.get("id"), "edge": "length", "word": l, "moved_f": k - n,
                                              "why": "a %s fps clip on a %s fps timeline cannot be %d frames long in "
                                                     "Resolve" % (fps_label(self.mfps(mid)), fps_label(self.tfps), n)})
                    n = k
                    if clean is not None:
                        tight_out = None
                    else:
                        # no placeable length ends between this word and the next (no pause): the edge runs up to
                        # one source frame into a word. Record it so check reports it as rate_edge (WARN) instead
                        # of a clipped or changed word the cutter cannot fix by padding
                        tight_out = "rate"
                        over = (e1(k) - nxt["t0"]) if nxt is not None and e1(k) > nxt["t0"] else 0.0
                        under = (W[l]["t1"] - e1(k)) if e1(k) < W[l]["t1"] else 0.0
                        self.warn("rate_edge", seg.get("id"), "a %s fps clip on a %s fps timeline has no length that ends "
                                  "between word %d and word %d (no pause): the out point runs %d ms into %r" % (
                                      fps_label(self.mfps(mid)), fps_label(self.tfps), l, l + 1,
                                      round(1000 * max(over, under)), (nxt or {}).get("w") if over else W[l]["w"]))
            if nframes is not None:
                while n > 1 and (f0 + self.need_src(mid, n, speed) > nframes or not self.placeable(mid, n, speed)):
                    n -= 1
            self.rep["pads"].append({"seg": seg.get("id"), "words": [f, l], "pad_in_s": round(W[f]["t0"] - f0 / mf, 3),
                                     "pad_out_s": round(e1(n) - W[l]["t1"], 3)})
            out.append({"src_in": f0, "rec_len": n, "words": [f, l]})
            # the real intrusion of each unavoidable edge, from the final frames
            tol = {}
            if tight_in:
                x_ = f0 / mf
                tol["in"] = max(x_ - W[f]["t0"], (prev["t1"] - x_) if prev is not None else 0.0, 0.0)
            if tight_out:
                x_ = e1(n)
                tol["out"] = max(W[l]["t1"] - x_, (x_ - nxt["t0"]) if nxt is not None else 0.0, 0.0)
            et = {k_: round(v_ + 0.0005, 4) for k_, v_ in tol.items() if v_ > 0.020 + EPS_S}
            if et:
                et["why"] = {k_: (tight_in if k_ == "in" else tight_out) for k_ in et}
                out[-1]["edge_tol"] = et
        return out

    def seg_range(self, seg):
        """(media id, src_in, rec_len or None, src_len or None) for shot, in_s/dur_s and in_f/out_f segments."""
        self.speed(seg)
        if seg.get("shot"):
            mid, s_in, s_out = _shot_lookup(self.lab, seg["shot"])
            trim = seg.get("trim")
            if trim is None:
                us = (self.shotlog.get(seg["shot"]) or {}).get("usable") or []
                trim = us[0] if us else [0.0, 1.0]
            try:
                t0_, t1_ = float(trim[0]), float(trim[-1])
            except (TypeError, ValueError, IndexError):
                raise Fail("segment %s: trim must be two numbers between 0 and 1 (got %r)" % (seg.get("id"), trim))
            L = s_out - s_in
            lo = s_in + rnd(t0_ * L)
            hi = s_in + rnd(t1_ * L)
            if hi <= lo:
                hi = lo + 1
            return mid, lo, None, hi - lo
        mid = seg.get("media")
        if not mid:
            raise Fail("segment %s needs words, shot, in_s or in_f" % seg.get("id"))
        mf = float(self.mfps(mid))
        if seg.get("in_f") is not None:
            a = int(self.num(seg, "in_f", 0))
            if seg.get("out_f") is None:
                return mid, a, None, None
            b = int(self.num(seg, "out_f", 0))
            if b <= a:
                raise Fail("segment %s: out_f %d must be after in_f %d" % (seg.get("id"), b, a))
            return mid, a, None, b - a
        if seg.get("in_s") is not None:
            return mid, rnd(self.num(seg, "in_s", 0.0) * mf), None, None
        raise Fail("segment %s needs words, shot, in_s or in_f" % seg.get("id"))

    def beat_period(self):
        g = self.beat_frames
        if len(g) < 2:
            return None
        return float(np.median(np.diff(np.array(g, np.float64))))

    def length_from(self, seg, mid, src_in, src_len, rec_head):
        speed = self.speed(seg)
        r = self.r(mid, speed)
        if seg.get("beats"):
            n = int(self.num(seg, "beats", 1, positive=True))
            if self.beat_frames:
                # a beat just after the head is the beat this cut anticipates (the segment starts on it, up to the
                # preset's early tolerance before it), so counting starts at the beat after it: `beats: 2` lasts
                # two beats
                cands = [bf for bf in self.beat_frames if bf > rec_head + 1.5 + self.beat_early]
                if len(cands) >= n:
                    rec_out = rnd(cands[n - 1]) - 1
                    if rec_out <= rec_head and len(cands) > n:
                        rec_out = rnd(cands[n]) - 1
                    if rec_out > rec_head:
                        self.rep["beats"].append({"seg": seg.get("id"), "beats": n, "rec_out": rec_out,
                                                  "beat_frame": cands[n - 1]})
                        per = self.beat_period()
                        if per and rec_out - rec_head < (n - 0.5) * per:
                            self.warn("beats_short", seg.get("id"), "segment %s asked for %d beats but lasts %.1f "
                                      "(it starts %.0f frames before a beat); move its start onto a beat"
                                      % (seg.get("id"), n, (rec_out - rec_head) / per,
                                         min(abs(bf - rec_head) for bf in self.beat_frames)))
                        return rec_out - rec_head
                self.warn("not_enough_beats", seg.get("id"), "fewer than %d beats after frame %d" % (n, rec_head))
            elif seg.get("dur_s") is None and src_len is None:
                raise Fail("segment %s: beats needs a music item with \"grid\": true whose media has a beats.json "
                           "(run media_lab.py beats); or give dur_s" % seg.get("id"))
            else:
                self.warn("no_beat_grid", seg.get("id"), "beats need a music item with grid and a beats.json")
        if seg.get("dur_s") is not None:
            d = self.num(seg, "dur_s", 1.0, positive=True)
            return self.speed_len(max(1, rnd(d * float(self.tfps))), speed, seg.get("id"))
        if src_len is None:
            if seg.get("beats") and self.beat_frames:
                left = [bf for bf in self.beat_frames if bf > rec_head + 1.5 + self.beat_early]
                raise Fail("segment %s asks for %s beats but the music has only %d beat%s left after %.2f s (its last "
                           "beat is at %.2f s); give it dur_s, use fewer beats or a longer song" % (
                               seg.get("id"), seg.get("beats"), len(left), "" if len(left) == 1 else "s",
                               rec_head / float(self.tfps), max(self.beat_frames) / float(self.tfps)))
            raise Fail("segment %s needs dur_s, beats or a source range (out_f, a shot or words)" % seg.get("id"))
        return self.speed_len(max(1, rnd(src_len / r)), speed, seg.get("id"))

    def speed_len(self, n, speed, sid=None):
        """The length of a retimed item. Resolve keeps the placed length when the speed is set (keeps_duration,
        measured), so any length works; under the older model the source span had to be whole frames."""
        if abs(speed - 1.0) < 1e-12 or self.keeps:
            return n
        den = Fraction(speed).limit_denominator(100).denominator
        m = max(den, int(round(n / float(den))) * den)
        if abs(m - n) <= 1:
            return m
        self.warn("speed_len_changed", sid, "at %g %% a length of %d frames does not play a whole number of source "
                                            "frames; Resolve may end it a frame off" % (speed * 100, n))
        return n

    def snap_to_grid(self, seg, head, prefer_downbeat=True):
        """Move the write head onto a (down)beat minus 1 frame by trimming or extending the previous V1 item.

        A dialogue item only moves inside the room its words leave: not before its last kept word ends (+40 ms),
        not into the next word of its source (-40 ms) and not more than the longest kept pause past that word, so
        the cutter's words never change. Linked audio moves with it."""
        grid_db = self.downbeat_frames or self.beat_frames
        grid_b = self.beat_frames or self.downbeat_frames
        if not grid_b:
            return head
        pools = ([grid_db, grid_b] if prefer_downbeat else [grid_b])

        def in_tol(h, g):
            # beat_sync's own tolerance (by default 2 frames early to 1 late): a cut there needs no move
            return any(self.btol[0] <= h - rnd(d) <= self.btol[1] for d in g)
        if not self.v[1] and int(head) > 0:
            # nothing on V1 before it (the segments before are sound only, such as voice-over word segments):
            # there is no picture item to trim onto the beat, and this is not the programme's start
            if not in_tol(head, pools[0]):
                self.warn("beat_snap_impossible", seg.get("id"), "segment %s starts at %.2f s after segments with no "
                          "picture on V1 (sound only), so no V1 item can be trimmed to put it on the beat; give it a "
                          "V1 picture before it, or let the cut sit off the beat" % (
                              seg.get("id"), int(head) / float(self.tfps)))
            return head
        if not self.v[1]:
            if not in_tol(head, pools[0]):
                hint = ""
                if self.music_src:
                    in_s, at_s, dbs = self.music_src
                    later = [d for d in dbs if d >= in_s - 1e-6]
                    if later:
                        hint = " (music in_s %.3f puts a downbeat 1 frame after the start)" % max(
                            0.0, later[0] - 1.0 / float(self.tfps) - at_s)
                self.warn("beat_snap_impossible", seg.get("id"), "the first beat segment opens the programme: start "
                                                                "the music on a downbeat instead" + hint)
            return head
        prev = self.v[1][-1]
        if int(prev["rec_out"]) != int(head) or not is_media_item(prev):
            return head
        mid = prev["media"]
        sp = speed_of(prev)
        fr = self.frames_of(mid)
        reach = max(12, int(float(self.tfps)))
        lo, hi = max(int(prev["rec_in"]) + 3, head - reach), head + reach
        words = prev.get("words")
        if words:
            W = self.words_of(mid)
            l = int(words[1])
            if 0 <= l < len(W):
                kmax = _ms_pair(None, self.P["speech"].get("pause_keep_ms"))[1]
                lo = max(lo, int(math.ceil(self.rec_of_src_time(prev, float(W[l]["t1"]) + 0.040) - 1e-6)))
                top = self.rec_of_src_time(prev, float(W[l]["t1"]) + max(kmax, 0.040))
                if l + 1 < len(W):
                    nxt = self.rec_of_src_time(prev, float(W[l + 1]["t0"]) - 0.040)
                    top = min(top, nxt)
                    top = max(top, min(head, nxt))
                else:
                    top = max(top, head)
                hi = min(hi, int(math.floor(top + 1e-6)))

        def fits(t):
            L = t - int(prev["rec_in"])
            if not (lo <= t <= hi) or L < 3 or not self.placeable(mid, L, sp):
                return False
            return fr is None or int(prev["src_in"]) + self.need_src(mid, L, sp) <= fr
        target = None
        for g in pools:
            ok = [rnd(d) - 1 for d in g if fits(rnd(d) - 1)]
            if ok:
                target = min(ok, key=lambda x: (abs(x - head), x))
                break
        if target is None:
            if in_tol(head, grid_b):
                return head
            if words:
                self.warn("beat_snap_impossible", seg.get("id"), "the words of %s leave no room to reach a %s before "
                          "%s; lengthen its tail with pad_ms or move the music" % (
                              prev["id"], "downbeat" if prefer_downbeat else "beat", seg.get("id")))
            else:
                self.warn("beat_snap_impossible", seg.get("id"), "cannot move the cut before %s onto a beat" % seg.get("id"))
            return head
        delta = target - head
        if delta == 0:
            return head
        r = self.r(mid, sp)
        linked = [x for tr in self.a.values() for x in tr if x.get("link") and x.get("link") == prev.get("link")
                  and x["rec_out"] == prev["rec_out"]]
        prev["rec_out"] += delta
        prev["src_out"] = prev["src_in"] + max(1, rnd(rec_len(prev) * r))
        for x in linked:
            x["rec_out"] = prev["rec_out"]
            x["src_out"] = x["src_in"] + max(1, rnd(rec_len(x) * self.r(x["media"], speed_of(x))))
        self.rep["beats"].append({"seg": seg.get("id"), "snap_prev": prev["id"], "moved_f": delta, "rec": target})
        return target

    # -- item factories
    def frame_pan(self, seg, mid, zoom):
        """pan_px that puts the subject at frame_x (its centre, a fraction of the source frame width) in the middle of
        the timeline frame, clamped so no black edge shows. (pan_px, frame_x or None)."""
        sid = seg.get("id")
        fx = self.num(seg, "frame_x", 0.5)
        if not 0.0 <= fx <= 1.0:
            raise Fail("segment %s: frame_x is the subject's centre as a fraction of the source frame width, 0 (left "
                       "edge) to 1 (right edge); got %r" % (sid, seg.get("frame_x")))
        if seg.get("pan_px") not in (None, 0, 0.0):
            self.warn("frame_x_and_pan", sid, "%s gives both frame_x and pan_px; frame_x is used" % sid)
        sizing = self.P["timeline"].get("input_sizing", "scaleToCrop")
        geo = frame_geometry({"media": {mid: self.use(mid)}, "timeline": {"width": self.W, "height": self.H,
                                                                        "resolve": {"input_sizing": sizing}}}, {"media": mid})
        if geo is None:
            self.warn("frame_x_ignored", sid, "%s: the size of %s is unknown, so frame_x cannot be turned into a pan"
                      % (sid, mid))
            return 0.0, None
        iw = geo[0] * zoom
        rx = max(0.0, (iw - self.W) / 2.0)
        want = iw * (0.5 - fx)
        pan = max(-rx, min(rx, want))
        if abs(pan - want) > 0.5 and (sid, mid, zoom, fx) not in self.fx_warned:
            self.fx_warned.add((sid, mid, zoom, fx))
            at = (self.W / 2.0 + pan - want) / self.W
            span = 1.0 - abs(1.0 - 2.0 * fx)
            need = (self.W / (geo[0] * span)) if span > 1e-6 else None
            self.warn("frame_x_clamped", sid, "%s: a subject at frame_x %.2f cannot be centred, the image ends first; it "
                      "sits at %.0f %% of the frame width%s" % (
                          sid, fx, 100.0 * at, (", zoom %.2f centres it" % need) if need and need < 4 else ""))
        return round(pan, 1), fx

    def v_item(self, vid, mid, src_in, n, rec, seg, speed, tags=None, track=1):
        r = self.r(mid, speed)
        zoom = self.num(seg, "zoom", 1.0, positive=True)
        pan, fx = self.num(seg, "pan_px", 0.0), None
        if seg.get("frame_x") is not None:
            pan, fx = self.frame_pan(seg, mid, zoom)
        it = {"id": vid, "kind": "clip", "media": mid, "src_in": int(src_in), "src_out": int(src_in) + max(1, rnd(n * r)),
              "rec_in": int(rec), "rec_out": int(rec + n), "speed": speed, "enabled": True,
              "transform": {"zoom": zoom, "pan_px": pan, "tilt_px": self.num(seg, "tilt_px", 0.0),
                            "rotation": 0.0, "opacity": 100},
              "fade_in": 0, "fade_out": 0, "why": seg.get("why", ""), "tags": list(tags or [])}
        if fx is not None:
            it["frame_x"] = fx
        self.v.setdefault(track, []).append(it)
        return it

    def a_item(self, aid, mid, src_in, n, rec, seg, speed, track, gain, link=None):
        r = self.r(mid, speed)
        try:
            gain = float(gain)
        except (TypeError, ValueError):
            raise Fail("segment %s: gain_db must be a number (got %r)" % (seg.get("id"), gain))
        it = {"id": aid, "kind": "clip", "media": mid, "src_in": int(src_in), "src_out": int(src_in) + max(1, rnd(n * r)),
              "rec_in": int(rec), "rec_out": int(rec + n), "speed": speed, "gain_db": gain, "pan": 0,
              "fade_in": 0, "fade_out": 0}
        if link:
            it["link"] = link
        self.a.setdefault(track, []).append(it)
        return it

    def place_spine(self):
        head = 0
        sp_audio = self.P.get("spine_audio", "dialogue")
        nat_gain = float(self.P["speech"].get("nat_gain_db", -12.0))
        ids = set()
        prev_beats = False
        for n, seg in enumerate(self.cl.get("spine") or []):
            if not isinstance(seg, dict):
                raise Fail("spine entry %d is not an object" % (n + 1))
            sid = str(seg.get("id") or "s%02d" % (n + 1))
            seg["id"] = sid
            if sid in ids:
                raise Fail("segment id %s is used twice in the cut list" % sid)
            ids.add(sid)
            if not seg.get("why"):
                self.warn("why_missing", sid, "segment %s has no why" % sid)
            forms = [k for k in ("words", "shot", "in_s", "in_f") if seg.get(k) is not None]
            if len(forms) != 1:
                raise Fail("segment %s needs exactly one of words, shot, in_s, in_f (got %s)" % (sid, forms or "none"))
            speed = self.speed(seg)
            if seg.get("retime"):
                # a ramp or freeze is keyed in the clip's Fusion comp (TimeStretcher): the item stays at speed 1 and
                # plays no sound (FUSION_PLAN s1.4)
                if abs(speed - 1.0) > 1e-12:
                    raise Fail("segment %s has both speed and retime: a retimed segment plays at speed 1 (its ramp "
                               "sets the speed); remove \"speed\"" % sid)
                if seg.get("words") is not None:
                    raise Fail("segment %s: a retime cannot run on a word segment (the words would play at another "
                               "speed); give the ramp or freeze to a shot, in_s or in_f segment" % sid)
            if seg.get("beats") and (self.first_beat_seg or not prev_beats):
                # the first beat segment lands on a downbeat; a later one that follows a non-beat segment (often
                # dialogue) lands on the nearest beat, inside the room the previous item's words leave
                head = self.snap_to_grid(seg, head, prefer_downbeat=self.first_beat_seg)
                self.first_beat_seg = False
            prev_beats = bool(seg.get("beats"))
            vids, aids = [], []
            tags = list(seg.get("tags") or [])
            if seg.get("beats"):
                tags.append("on_beat")
            if seg.get("words") is not None:
                mid = seg.get("media")
                if not mid:
                    raise Fail("segment %s uses words and needs media" % sid)
                kind = self.use(mid)["kind"]
                runs = self.word_runs(seg, mid)
                self.words_played[mid] = (sid, {k for r_ in runs for k in range(r_["words"][0], r_["words"][1] + 1)})
                for k, run in enumerate(runs):
                    vid = sid if k == 0 else "%s_%d" % (sid, k + 1)
                    link = "g_" + vid
                    if kind in ("av", "video"):
                        self.v_item(vid, mid, run["src_in"], run["rec_len"], head, seg, speed, tags)["words"] = run["words"]
                        vids.append(vid)
                    if kind in ("av", "audio"):
                        ai = self.a_item(vid + "a", mid, run["src_in"], run["rec_len"], head, seg, speed, 1,
                                         seg.get("gain_db", 0.0), link if kind == "av" else None)
                        ai["words"] = run["words"]
                        if run.get("edge_tol"):
                            ai["edge_tol"] = dict(run["edge_tol"])
                        aids.append(vid + "a")
                        if kind == "av":
                            self.v[1][-1]["link"] = link
                    head += run["rec_len"]
            else:
                mid, src_in, n0, src_len = self.seg_range(seg)
                kind = self.use(mid)["kind"]
                n = self.length_from(seg, mid, src_in, src_len, head)
                n = self.fit_len(mid, n, speed, sid)
                fr = self.frames_of(mid)
                r = self.r(mid, speed)
                if src_len is not None and (seg.get("beats") or seg.get("dur_s") is not None) \
                        and rnd(n * r) > src_len + 1 and seg.get("shot"):
                    self.warn("trim_overrun", sid, "segment %s plays %.2f s past the end of its trim (the logged "
                              "usable part of %s); trim it later or give it fewer beats" % (
                                  sid, (rnd(n * r) - src_len) / float(self.mfps(mid)), seg["shot"]))
                if fr is not None and src_in + self.need_src(mid, n, speed) > fr:
                    need = self.need_src(mid, n, speed)
                    floor_in = self.shot_floor(seg)
                    if speed < 1.0 and self.keeps and fr - need >= floor_in:
                        self.warn("slowmo_moved", sid, "segment %s at %g %%: Resolve places a slowed clip at full "
                                  "speed first, so it needs %d source frames after its in point; the in point moved "
                                  "%d frames earlier (%.2f s) to fit inside the clip" % (
                                      sid, speed * 100, need, src_in - (fr - need),
                                      (src_in - (fr - need)) / float(self.mfps(mid))))
                        src_in = fr - need
                    else:
                        n2 = self.fit_source(mid, src_in, n, speed, fr)
                        self.warn("source_short", sid, "segment %s wants %d f but the media ends; shortened to %d f"
                                  % (sid, n, n2))
                        n = n2
                if n < 1:
                    raise Fail("segment %s has less than one timeline frame of %s left after its in point" % (sid, mid))
                audio = seg.get("audio")
                if seg.get("retime") and kind == "av" and audio not in (None, "none"):
                    self.warn("retime_audio", sid, "segment %s: a ramped or frozen clip plays no sound; its audio %r "
                              "is dropped" % (sid, audio))
                    audio = "none"
                if not audio:
                    # dialogue presets: B-roll without speech plays as nat sound (-12 dB on A3), never as dialogue at
                    # 0 dB; slowed sound is dropped unless the cut list asks for it
                    audio = sp_audio
                    why_a = None
                    if seg.get("retime") and kind == "av":
                        audio, why_a = "none", "a ramped or frozen clip plays no sound"
                    elif abs(speed - 1.0) > 1e-9 and kind == "av":
                        audio, why_a = "none", "slowed sound is dropped; set \"audio\" to keep it"
                    elif sp_audio == "dialogue" and kind == "av" and len(self.words_of(mid)) < 3:
                        audio, why_a = "nat", "no speech in this clip: nat sound at %g dB" % nat_gain
                    if why_a:
                        self.rep["snaps"].append({"seg": sid, "edge": "audio", "moved_f": 0, "why": why_a})
                if kind in ("av", "video", "image"):
                    it = self.v_item(sid, mid, src_in, n, head, seg, speed, tags)
                    vids.append(sid)
                if kind in ("av", "audio") and audio != "none":
                    if audio == "nat":
                        self.a_item(sid + "a", mid, src_in, n, head, seg, speed, 3, seg.get("gain_db", nat_gain))
                    else:
                        link = ("g_" + sid) if kind == "av" else None
                        self.a_item(sid + "a", mid, src_in, n, head, seg, speed, 1, seg.get("gain_db", 0.0), link)
                        if link:
                            it["link"] = link
                    aids.append(sid + "a")
                head += n
            self.seg_items[sid] = (vids, aids)
        return head

    def place_overlays(self, N):
        for n, ov in enumerate(self.cl.get("overlays") or []):
            oid = str(ov.get("id") or "r%02d" % (n + 1))
            track = int(ov.get("track", 2))
            if track < 2:
                raise Fail("overlay %s must sit on track 2 or higher" % oid)
            at = ov.get("at") or {}
            if at.get("seg"):
                vids, aids = self.seg_items.get(at["seg"], ([], []))
                items = [x for x in self.v[1] if x["id"] in vids] or [x for tr in self.a.values() for x in tr if x["id"] in aids]
                if not items:
                    raise Fail("overlay %s points at unknown segment %s" % (oid, at["seg"]))
                rec = min(x["rec_in"] for x in items) + rnd(float(at.get("offset_s", 0)) * float(self.tfps))
                at_exact = min(x["rec_in"] for x in items) / float(self.tfps) + float(at.get("offset_s", 0))
            else:
                rec = rnd(round(float(ov.get("at_s", 0.0)) * float(self.tfps), 6))
                at_exact = float(ov.get("at_s", 0.0))
            seg = dict(ov, id=oid)
            mid, src_in, _, src_len = self.seg_range(seg)
            speed = self.speed(ov, "overlay")
            n = self.length_from(seg, mid, src_in, src_len, rec)
            if ov.get("dur_s") is not None and not ov.get("beats"):
                # the end rounded to a frame, not the length: overlays chained with at + dur_s meet edge to edge
                end_ = rnd(round((at_exact + self.num(ov, "dur_s", 1.0, positive=True, what="overlay"))
                                 * float(self.tfps), 6))
                n = self.speed_len(max(1, end_ - rec), speed, oid)
            n = self.fit_len(mid, n, speed, oid)
            fr = self.frames_of(mid)
            if fr is not None and src_in + self.need_src(mid, n, speed) > fr:
                need = self.need_src(mid, n, speed)
                if speed < 1.0 and self.keeps and fr - need >= self.shot_floor(seg):
                    self.warn("slowmo_moved", oid, "overlay %s at %g %%: the in point moved %d frames earlier so "
                              "Resolve can place it" % (oid, speed * 100, src_in - (fr - need)))
                    src_in = fr - need
                else:
                    n = self.fit_source(mid, src_in, n, speed, fr)
            if rec + n > N:
                self.warn("overlay_clamped", oid, "overlay %s ran past the programme end; clamped" % oid)
                n = N - rec
                if n >= 1 and not self.placeable(mid, n, speed):
                    n = nearest_placeable(n, self.mfps(mid), self.tfps) or 0
            if n < 1 or rec < 0:
                self.warn("overlay_dropped", oid, "overlay %s has no room inside the programme" % oid)
                continue
            if not ov.get("why"):
                self.warn("why_missing", oid, "overlay %s has no why" % oid)
            self.v_item(oid, mid, src_in, n, rec, seg, speed, list(ov.get("tags") or []) + ["overlay"], track)

    def free_track(self, rec, n, lo=2):
        """The lowest video track from lo up with nothing in [rec, rec + n)."""
        k = lo
        while any(x["rec_in"] < rec + n and rec < x["rec_out"] for x in self.v.get(k, [])):
            k += 1
        return k

    def place_holds(self, N):
        """Long J and L cuts on V2 that continue the picture exactly (0.5 to 2 s overlaps).

        "hold_prev_s": x on a spine segment keeps the previous segment's picture going for its first x seconds (a J
        cut: this segment's voice starts under the old picture); the overlay starts at the previous item's source
        out point. "early_s": x shows this segment's picture x seconds early over the end of the previous segment
        (an L cut: the old voice runs on under the new picture); the overlay ends at this item's source in point.
        Either way the picture never jumps back inside a shot."""
        v1 = sorted(self.v.get(1, []), key=lambda x: x["rec_in"])
        f = float(self.tfps)
        self.hold_src = {}
        for seg in self.cl.get("spine") or []:
            sid = seg.get("id")
            for key, tag in (("hold_prev_s", "hold"), ("early_s", "early")):
                if seg.get(key) in (None, 0, 0.0):
                    continue
                x = self.num(seg, key, 0.0, positive=True)
                vids, _ = self.seg_items.get(sid, ([], []))
                mine = [it for it in v1 if it["id"] in vids]
                if not mine:
                    self.warn("hold_ignored", sid, "%s on %s: the segment has no picture on V1" % (key, sid))
                    continue
                first = mine[0]
                prev = [it for it in v1 if it["rec_out"] == first["rec_in"]]
                if not prev:
                    self.warn("hold_ignored", sid, "%s on %s: nothing plays on V1 right before it" % (key, sid))
                    continue
                prev = prev[0]
                oid = "%s_%s" % (sid, tag)
                want = max(1, rnd(x * f))
                if key == "hold_prev_s":
                    src, room = prev, rec_len(first)
                else:
                    src, room = first, rec_len(prev)
                mid, speed = src["media"], speed_of(src)
                n = self.fit_len(mid, min(want, room), speed, oid)
                fr = self.frames_of(mid)
                if key == "hold_prev_s":
                    s_in = int(src["src_out"])
                    if fr is not None:
                        n = self.fit_source(mid, s_in, n, speed, fr)
                    rec = int(first["rec_in"])
                else:
                    while n >= 1 and (not self.placeable(mid, n, speed) or int(src["src_in"]) - max(1, rnd(n * self.r(mid, speed))) < 0):
                        n -= 1
                    s_in = int(src["src_in"]) - max(1, rnd(n * self.r(mid, speed))) if n >= 1 else 0
                    rec = int(first["rec_in"]) - n
                if n < 1:
                    self.warn("hold_ignored", sid, "%s on %s: the %s has no source frames %s" % (
                        key, sid, "previous shot" if key == "hold_prev_s" else "shot",
                        "after its out point" if key == "hold_prev_s" else "before its in point"))
                    continue
                if n < want:
                    self.warn("hold_clamped", sid, "%s on %s: %.2f s asked, %.2f s placed (%s)" % (
                        key, sid, want / f, n / f, "the segment is shorter" if min(want, room) < want else
                        "the media has no more frames there"))
                seg_like = {"id": oid, "why": "%s cut: %s" % ("J" if tag == "hold" else "L", seg.get("why", "")),
                            "zoom": (src.get("transform") or {}).get("zoom", 1.0),
                            "pan_px": (src.get("transform") or {}).get("pan_px", 0.0),
                            "tilt_px": (src.get("transform") or {}).get("tilt_px", 0.0)}
                self.v_item(oid, mid, s_in, n, rec, seg_like, speed, ["overlay", tag], self.free_track(rec, n))
                self.hold_src[oid] = src["id"]

    def place_music(self, N, words_tl):
        prev_auto = None          # (media, rec_out, auto depth) of the last music item with an auto duck
        for n, mu in enumerate(self.cl.get("music") or []):
            mid = mu.get("media")
            if not mid:
                continue
            aid = str(mu.get("id") or "m%02d" % (n + 1))
            rec = rnd(float(mu.get("at_s", 0.0)) * float(self.tfps))
            until = mu.get("until", "end")
            end = N if until in (None, "end") else rnd(float(until) * float(self.tfps))
            end = min(end, N)
            src_in = rnd(float(mu.get("in_s", 0.0)) * float(self.mfps(mid)))
            r = self.r(mid)
            fr = self.frames_of(mid)
            n_f = end - rec
            natural = False
            if fr is not None and src_in + rnd(n_f * r) >= fr:
                n_f = int(math.floor((fr - src_in) / r + 1e-9))
                natural = True
            if n_f < 1:
                self.warn("music_dropped", aid, "music %s has no room" % aid)
                continue
            it = self.a_item(aid, mid, src_in, n_f, rec, {}, 1.0, 2, mu.get("gain_db", -6.0))
            if natural and fr is not None:
                it["src_out"] = min(it["src_out"], fr)
            fo = mu.get("fade_out_s")
            if fo is None:
                fo = 0.0 if natural else 1.0
            it["fade_out"] = min(n_f, rnd(float(fo) * float(self.tfps)))
            # the fade never starts before the last word under it ends plus 0.2 s (a word fading with the music
            # sounds cut off); it keeps 12 frames, and ending_tight reports an ending too tight for that
            under = [float(w["t1"]) for w in words_tl if float(w["t0"]) < (rec + n_f) / float(self.tfps)]
            if under and it["fade_out"] > 12 and mu.get("fade_out_s") is None:
                room = rec + n_f - rnd((max(under) + 0.2) * float(self.tfps))
                if room < it["fade_out"]:
                    it["fade_out"] = max(12, room)
                    self.rep["snaps"].append({"seg": aid, "edge": "music_fade", "moved_f": 0,
                                              "why": "the fade starts after the last word plus 0.2 s"})
            it["fade_in"] = min(n_f, rnd(float(mu.get("fade_in_s", 0.0)) * float(self.tfps)))
            if src_in > 0 and it["fade_in"] == 0:
                it["fade_in"] = 1          # a song started inside the file clicks without one frame of fade
            duck = mu.get("duck", "auto")
            if duck == "auto" and not any(float(w["t1"]) > rec / float(self.tfps) and
                                          float(w["t0"]) < (rec + n_f) / float(self.tfps) for w in words_tl):
                duck = "none"              # no word under this music: nothing to duck under, the bed keeps its level
            if duck not in (None, "none", 0, False):
                mcfg = self.P.get("music") or {}
                hold = self.num(mu, "duck_hold_s", float(mcfg.get("duck_hold_s", 1.05) or 0.0), what="music")
                mx = mu.get("duck_max_db", mcfg.get("duck_max_db"))
                mx = None if mx is None else abs(self.num({"id": aid, "duck_max_db": mx}, "duck_max_db", 0.0,
                                                          what="music"))
                if duck == "auto" and prev_auto and prev_auto[0] == mid and prev_auto[1] == rec:
                    # a back-to-back copy of the same media (a chained loop) ducks as deep as the first copy: its
                    # own measure under a few words would jump the bed at the seam
                    depth = prev_auto[2]
                    self.note("duck_shared", aid, "music %s continues %s's loop: its auto duck keeps the first "
                              "copy's depth (%.1f dB)" % (aid, mid, depth))
                else:
                    depth = (self.duck_depth(mid, float(mu.get("gain_db", -6.0)), words_tl, rec, src_in)
                             if duck == "auto" else abs(float(duck)))
                prev_auto = (mid, rec + n_f, depth) if duck == "auto" else None
                shift = 0.0
                if duck == "auto" and mx is not None and depth > mx + 0.05:
                    # a deep auto duck swings the bed hard between the lines: lower the whole bed instead, and duck
                    # only by the cap (the level under the voice stays where the auto depth put it)
                    shift = depth - mx
                    depth = mx
                    it["gain_db"] = round(float(it["gain_db"]) - shift, 2)
                lifts = self.lift_windows()
                env = self.duck_env(words_tl, rec, n_f, depth, hold, N, [x[1:] for x in lifts])
                if env:
                    it["volume_env"] = env
                it["duck"] = {"mode": duck, "depth_db": round(depth, 1), "hold_s": round(hold, 2), "max_db": mx,
                              "base_shift_db": round(shift, 1)}
                tf = float(self.tfps)
                used = [x for x in lifts if tuple(x[1:]) in set(self.lift_used)]
                if used:
                    it["duck"]["lifts"] = [[x[0], rnd(x[1] * tf), rnd(x[2] * tf)] for x in used]
                for L0, L1, why in self.lift_refused:
                    sid = [x[0] for x in lifts if (x[1], x[2]) == (L0, L1)]
                    if L1 > rec / tf and L0 < (rec + n_f) / tf:
                        self.warn("lift_refused", sid[0] if sid else None, "music %s does not lift over %s "
                                  "(%.2f to %.2f s): %s" % (aid, sid[0] if sid else "the segment", L0, L1, why))
                for L0, L1, db0, late_ms in self.lift_late:
                    sid = [x[0] for x in lifts if (x[1], x[2]) == (L0, L1)]
                    self.warn("lift_late", sid[0] if sid else None, "music %s is still %.1f dB down on the first "
                              "frame of %s (%.2f s) and reaches full level %d ms later: the last word ends too close "
                              "to the cut; start the segment 1 to 3 frames later or end the words before it earlier "
                              "(pad_ms)" % (aid, db0, sid[0] if sid else "the lift", L0, late_ms))
            elif self.lift_windows():
                self.warn("lift_ignored", None, "music %s is not ducked, so \"lift\" changes nothing" % aid)

    def lift_windows(self):
        """[(segment id, t0, t1), ...] programme seconds of the spine segments the cut list marked "lift": true."""
        out = []
        tf = float(self.tfps)
        for seg in self.cl.get("spine") or []:
            if not seg.get("lift"):
                continue
            vids, auds = self.seg_items.get(seg.get("id"), ([], []))
            its = [x for tr in list(self.v.values()) + list(self.a.values()) for x in tr
                   if x["id"] in set(vids) | set(auds)]
            if its:
                out.append((seg.get("id"), min(int(x["rec_in"]) for x in its) / tf,
                            max(int(x["rec_out"]) for x in its) / tf))
        # lifted segments that follow each other are one lift ("s02+s03"): the music stays up across the cut
        out.sort(key=lambda x: x[1])
        merged = []
        for sid, a, b in out:
            if merged and a <= merged[-1][2] + 1.0 / tf + 1e-9:
                merged[-1] = (merged[-1][0] + "+" + str(sid), merged[-1][1], max(merged[-1][2], b))
            else:
                merged.append((str(sid), a, b))
        return merged

    def duck_by_energy(self, music_mid, music_gain, words_tl, rec, src_in, lu):
        """The depth that puts the music duck_lu under the voice by the preview's own speech_music_gap measure: the
        mean power of the dialogue during its words against the music's under the same words (the mono mix of each,
        gains included). None when a WAV is missing."""
        if not words_tl:
            return None
        mm = self.lab.media(music_mid)
        fm = audio_file_of(self.lab, mm)
        if not fm or not os.path.exists(fm):
            return None
        msrc = wav_source(fm)
        m_off = float((mm.get("audio") or {}).get("offset_s") or 0.0)
        m_t0 = src_in / float(self.mfps(music_mid)) - m_off - rec / float(self.tfps)
        gains = {x["id"]: float(x.get("gain_db", 0.0)) for tr in self.a.values() for x in tr}
        ev = em = 0.0
        ws = words_tl if len(words_tl) <= 400 else [words_tl[int(k * len(words_tl) / 400)] for k in range(400)]
        for w in ws:
            m = self.lab.media(w["media"])
            fv = audio_file_of(self.lab, m)
            if not fv or not os.path.exists(fv):
                return None
            vsrc = wav_source(fv)
            v_off = float((m.get("audio") or {}).get("offset_s") or 0.0)
            a, b = int((float(w["src_t0"]) - v_off) * vsrc.sr), int((float(w["src_t1"]) - v_off) * vsrc.sr)
            if b <= a:
                continue
            v = vsrc.read(a, b).mean(axis=1).astype(np.float64)
            ev += float((v * v).sum()) * 10 ** (gains.get(w["item"], 0.0) / 10.0)
            ma, mb = int((m_t0 + float(w["t0"])) * msrc.sr), int((m_t0 + float(w["t1"])) * msrc.sr)
            mu_ = msrc.read(ma, max(ma, mb)).mean(axis=1).astype(np.float64)
            em += float((mu_ * mu_).sum()) * (b - a) / max(1, mb - ma)
        if ev <= 0 or em <= 0:
            return None
        return float(min(40.0, max(0.0, 10 * math.log10(em / ev) + music_gain + lu)))

    def duck_depth(self, music_mid, music_gain, words_tl=None, rec=0, src_in=0):
        lu = float(self.P["music"].get("duck_lu", 14))
        try:
            d = self.duck_by_energy(music_mid, music_gain, words_tl, rec, src_in, lu)
        except (Fail, OSError, ValueError):
            d = None
        if d is not None:
            return d
        lm = (self.lab.analysis(music_mid, "loudness") or {}).get("I")
        voices = [x["media"] for x in self.a.get(1, [])]
        lv = None
        if voices:
            vals = [(self.lab.analysis(v, "loudness") or {}).get("I") for v in sorted(set(voices))]
            vals = [v for v in vals if isinstance(v, (int, float))]
            lv = max(vals) if vals else None
        if lm is None or lv is None:
            return 12.0
        return float(min(40.0, max(0.0, (lm + music_gain) - (lv - lu))))

    def duck_env(self, words_tl, rec, n, depth, hold_s=1.05, end_frame=None, lifts=None):
        """Volume envelope [[frame, dB], ...] of a music item (frames from its start) that ducks under the speech.

        The speech regions (each word from 0.30 s before to 0.25 s after) duck with a 0.25 s attack and a 0.80 s
        release. It breathes, never pumps: (a) when the first region starts within 1.5 s of the item's start the
        music starts ducked (no dip before the first word); (b) a gap shorter than hold_s stays ducked, and a release
        happens only when the music then gets at least 1.0 s at the open level and the whole rise and fall takes
        more than 2.5 s, so on its own the bed comes up only in a voice gap over about 3.1 s; (c) no release in the
        last 2 s of the programme (the music stays down into its fade).

        lifts: [(t0, t1), ...] programme seconds of picture-only segments the cut list marked "lift": true (the drop
        in a voice-over piece). The bed is at full level on the segment's first frame (the drop's hit is never
        ducked): it rises in 0.2 s (longer for a deep duck) to end on that frame, starting no earlier than 0.05 s
        after the last word; when the words leave less room the rise is faster, down to 2 frames, and when even that
        does not fit it ends 2 frames after the rise starts and self.lift_late records the level of the first frame.
        It falls in 0.15 s to end by the earlier of the segment's end and 0.05 s before the next word. The segment
        must be at least rise + fall + 0.25 s long (about 0.6 s; more for a deep duck) and keep 0.25 s at full
        level. Returns the envelope; self.lift_used lists the lifts made and self.lift_refused the ones that did not
        fit."""
        self.lift_used, self.lift_refused, self.lift_late = [], [], []
        if depth <= 0:
            return None
        spans = [[w["t0"], w["t1"]] for w in words_tl]
        if not spans:
            spans = [[x["rec_in"] / float(self.tfps), x["rec_out"] / float(self.tfps)] for x in self.a.get(1, [])]
        if not spans:
            return None
        spans.sort()
        f = float(self.tfps)
        t_in, t_out = rec / f, (rec + n) / f
        t_end = min(t_out, end_frame / f) if end_frame is not None else t_out
        rise_s = max(DUCK_LIFT_RISE_S, 2 * math.ceil(depth / DUCK_STEP_DB) / f)
        fall_s = max(DUCK_LIFT_FALL_S, 2 * math.ceil(depth / DUCK_STEP_DB) / f)
        lifts = sorted(tuple(x) for x in (lifts or []))

        fast_s = 2.0 / f           # the fastest rise: 2 frames (the drop's hit masks a fast step)

        def lift_between(t1_prev, t0_next):
            for L0, L1 in lifts:
                if max(L0, t1_prev) < min(L1, t0_next):
                    lo, f1 = t1_prev + 0.05, min(L1, t0_next - 0.05)
                    r1 = max(L0, lo + fast_s)         # full level on the segment's first frame when there is room
                    r0 = max(lo, r1 - rise_s)
                    if L1 - L0 >= rise_s + fall_s + DUCK_LIFT_OPEN_S - 1e-9 and \
                            (f1 - fall_s) - r1 >= DUCK_LIFT_OPEN_S - 1e-9:
                        return (L0, L1, r0, r1, f1)
            return None
        for L0, L1 in lifts:
            inside = [sp for sp in spans if sp[0] < L1 - 0.05 and sp[1] > L0 + 0.05]
            if inside:
                self.lift_refused.append((L0, L1, "words are heard inside it (at %.2f s)" % inside[0][0]))
        regions = []          # [start, end, last word end, lift after this region or None]
        for t0, t1 in spans:
            a, b = t0 - DUCK_PRE_S, t1 + DUCK_POST_S
            if regions:
                lf = lift_between(regions[-1][2], t0)
                if lf is not None:
                    regions[-1][3] = lf
                    regions.append([a, b, t1, None])
                    continue
                gap = a - regions[-1][1]
                open_s = (a - DUCK_ATTACK_S) - (regions[-1][1] + DUCK_RELEASE_S)
                if gap < hold_s or gap <= DUCK_PUMP_S or open_s < DUCK_OPEN_MIN_S:
                    regions[-1][1] = max(regions[-1][1], b)
                    regions[-1][2] = max(regions[-1][2], t1)
                    continue
            regions.append([a, b, t1, None])
        for L0, L1 in lifts:
            used = any(r[3] is not None and r[3][:2] == (L0, L1) for r in regions)
            if used:
                self.lift_used.append((L0, L1))
            elif not any(x[:2] == (L0, L1) for x in self.lift_refused):
                self.lift_refused.append((L0, L1, "it is not between two spoken lines under this music, or it is "
                                                  "shorter than %.2f s (a %.2f s rise, a %.2f s fall and %.2f s at "
                                                  "full level) or the next word leaves under %.2f s at full level" % (
                                                      rise_s + fall_s + DUCK_LIFT_OPEN_S, rise_s, fall_s,
                                                      DUCK_LIFT_OPEN_S, DUCK_LIFT_OPEN_S)))
        regions = [r for r in regions if r[1] > t_in and r[0] < t_out]
        if not regions:
            return None
        if regions[-1][1] + 1e-9 >= t_end - DUCK_END_HOLD_S and regions[-1][3] is None:
            regions[-1][1] = t_out + 1.0
        pts = []
        for k_, (a, b, _t1, lf) in enumerate(regions):
            prev_lf = regions[k_ - 1][3] if k_ else None
            if k_ == 0 and a - t_in <= DUCK_START_S:
                seq = [(0, -depth)]
            elif prev_lf is not None:
                seq = [(rnd((prev_lf[4] - fall_s) * f) - rec, 0.0), (rnd(prev_lf[4] * f) - rec, -depth)]
            else:
                seq = [(rnd((a - DUCK_ATTACK_S) * f) - rec, 0.0), (rnd(a * f) - rec, -depth)]
            if lf is not None:
                ra, rb = rnd(lf[2] * f), rnd(lf[3] * f)
                if rb <= ra:
                    rb = ra + 1
                seq += [(ra - rec, -depth), (rb - rec, 0.0)]
                first = rnd(lf[0] * f)
                if first < rb:
                    db0 = -depth * min(1.0, (rb - first) / float(rb - ra))
                    self.lift_late.append((lf[0], lf[1], round(db0, 1), round(1000.0 * (rb - first) / f)))
            elif b >= t_out:
                seq.append((n, -depth))
            else:
                seq += [(rnd(b * f) - rec, -depth), (rnd((b + DUCK_RELEASE_S) * f) - rec, 0.0)]
            for fr, db in seq:
                fr = min(max(fr, 0), n)
                if pts and fr < pts[-1][0]:
                    fr = pts[-1][0]
                pts.append([int(fr), round(db, 2)])
        if pts and pts[0][0] > 0:
            pts.insert(0, [0, 0.0])
        out = []
        for p in pts:
            if out and out[-1] == p:
                continue
            out.append(p)
        return out

    def fit_chars(self, fpx, sbox, maxc, look=None):
        """Characters per line that fit the safe box at this font size (about 0.58 em per character)."""
        return max(8, min(int(maxc), int((sbox[2] - 2 * look_pad(look)[0] * fpx) / (char_em(look) * fpx))))

    def look_for(self, look_id, default_id, what, caption=False):
        lk, note = resolve_look(self.P, look_id, self.brand, default_id, caption)
        if note and note not in self.fx_warned:
            self.fx_warned.add(note)
            self.warn("look_unknown", what, note)
        return lk

    def place_titles(self, N):
        items, pending = [], []
        top = max(self.v) if self.v else 1
        track = max(top + 1, 2)
        sbox = safe_box(self.platform, self.W, self.H)
        for n, t in enumerate(self.cl.get("titles") or []):
            tid = str(t.get("id") or "t%02d" % (n + 1))
            style = t.get("style", "hook_top")
            if style not in TITLE_STYLES and style not in (self.P.get("titles") or {}):
                self.warn("title_style_unknown", tid, "title %s: style %r is unknown (%s); hook_top used" % (
                    tid, style, ", ".join(TITLE_STYLES)))
            st = (self.P.get("titles") or {}).get(style) or DEFAULT_PRESET["titles"].get(style) or DEFAULT_PRESET["titles"]["hook_top"]
            fpx = float(st.get("font_px", 72)) * min(self.W, self.H) / 1080.0
            if not str(t.get("text", "")).strip():
                raise Fail("title %s has no text" % tid)
            y = self.num(t, "y", float(st.get("y", 0.2)), what="title")
            if not 0.0 <= y <= 1.0:
                raise Fail("title %s: y is a fraction of the frame height from the top, 0 to 1 (got %r)" % (tid, t.get("y")))
            at = t.get("at") or {}
            anchor = None
            if t.get("at_s") is None and isinstance(at, dict) and at.get("seg"):
                # the overlays' anchor: {"seg": "s02", "offset_s": 0.4} starts the title that far into a segment
                vids, aids = self.seg_items.get(at["seg"], ([], []))
                segs_ = [x for x in self.v.get(1, []) if x["id"] in vids] or [
                    x for tr in self.a.values() for x in tr if x["id"] in aids]
                if not segs_:
                    raise Fail("title %s points at unknown segment %s" % (tid, at["seg"]))
                seg_s = min(int(x["rec_in"]) for x in segs_) / float(self.tfps)
                off_s = self.num(at, "offset_s", 0.0, what="title")
                at_s = seg_s + off_s
                anchor = (at["seg"], seg_s, off_s)
            elif t.get("at_s") is None:
                self.warn("title_start_missing", tid, "title %s has no start (\"at_s\", or \"at\": {\"seg\": ..., "
                                                      "\"offset_s\": ...}): it starts at 0.00 s" % tid)
                at_s = 0.0
            else:
                at_s = self.num(t, "at_s", 0.0, what="title")
            if at_s < 0:
                if anchor:
                    raise Fail("title %s: \"at\": {\"seg\": %r, \"offset_s\": %g} starts %.2f s before the programme "
                               "(segment %s starts at %.2f s): the start must be 0 or more; give offset_s %.2f or more "
                               "(an offset counts into the segment, so 0 is its first frame)" % (
                                   tid, anchor[0], anchor[2], -at_s, anchor[0], anchor[1], abs(anchor[1]) and -anchor[1]))
                raise Fail("title %s: at_s must be 0 or more (got %r)" % (tid, at_s))
            role = t.get("role") or default_role(style, at_s)
            if role not in TITLE_ROLES:
                raise Fail("title %s: role %r is not one of %s" % (tid, role, ", ".join(TITLE_ROLES)))
            look = self.look_for(t.get("look") or st.get("look"), "boxed", tid)
            text = apply_case(t.get("text", ""), look["case"])
            # the end card's text centres on the frame (on the logo), not on the safe box: it wraps inside the widest
            # box centred at W / 2 that stays in the safe area. Every other style centres on the safe box
            tbox = sbox
            if style in CARD_STYLES:
                half_ = max(1.0, min(self.W / 2.0 - sbox[0], sbox[0] + sbox[2] - self.W / 2.0))
                tbox = [self.W / 2.0 - half_, sbox[1], 2 * half_, sbox[3]]
            # a line break written in the text ("Line one\nline two") is kept; each line wraps on its own
            mc_ = self.fit_chars(fpx, tbox, st.get("max_chars_line", 22), look)
            lines = [ln for part in str(text).split("\n") for ln in _wrap(part.strip(), mc_) if ln] or [""]
            box = text_box(lines, fpx, tbox[0] + tbox[2] / 2, y * self.H, tbox, self.W, self.H, look)
            yc_ = (box[1] + box[3] / 2.0) / float(self.H)
            if t.get("y") is not None and abs(yc_ - y) > 0.01:
                self.warn("title_y_clamped", tid, "title %s: y %.2f would put %d line%s partly outside the %s safe "
                          "area, so it sits at y %.2f; moving it %s does nothing, move it %s or shorten it" % (
                              tid, y, len(lines), "" if len(lines) == 1 else "s", self.platform or "frame", yc_,
                              "down" if y > yc_ else "up", "up" if y > yc_ else "down"))
            # start and END rounded to frames (not the length): titles chained with exact durations meet exactly
            # (to 6 decimals first, so 8.56 + 1.18 and a typed 9.74 land on the same frame)
            r0 = rnd(round(at_s * float(self.tfps), 6))
            dur_ = self.num(t, "dur_s", 2.0, positive=True, what="title")
            r1 = min(N, max(r0 + 1, rnd(round((at_s + dur_) * float(self.tfps), 6))))
            if r1 <= r0:
                self.warn("title_dropped", tid, "title %s has no room" % tid)
                continue
            items.append({"id": tid, "kind": "title", "text": t.get("text", ""), "style": style, "rec_in": r0,
                          "rec_out": r1, "box": box, "lines": lines, "font_px": round(fpx, 1),
                          "text_w": text_width(lines, fpx, look), "why": t.get("why", ""), "role": role,
                          "look": look})
            pending.append((items[-1], t, st, style, tid, lines))
        # tracks (a second title track for two titles at once), 1 to 3 frame gaps closed, edges onto picture cuts
        tracks_ = self.title_timing(items, N, track)
        # animations last: the reading-floor fallback judges the final timing
        for it_, t, st, style, tid, lines in pending:
            asked = t.get("anim") if "anim" in t else None
            items = [it_]
            an = self.anim_for(asked if asked is not None else st.get("anim"), "title", tid, explicit=asked is not None)
            if an:
                words = " ".join(lines).split()
                an.update({"word_frames": [0] * len(words), "words": words, "keyword": None})
                if asked is not None:
                    # the cut list named it: it counts as an effect event (fx_density); a default does not
                    an["asked"] = True
                items[-1]["anim"] = an
                if asked is None:
                    # a title on the first frame loses its entrance first (it is whole from frame 1, so its reading
                    # time starts there), then the reading-floor fallback judges what is left
                    self.open_title_whole(items[-1], tid)
                    self.fit_default_title_anim(items[-1], tid, words)
                    if (items[-1].get("anim") or {}).get("id") == st.get("anim"):
                        # an older cut list without anim fields now animates: say so, and how to keep it static
                        self.rep.setdefault("fx_anims", []).append({
                            "item": tid, "anim": st.get("anim"), "why": "the preset's default for %s titles; "
                                                                       "\"anim\": \"none\" keeps it static" % style})
                    # a fallback the reading floor chose (fade) starts whole on the first frame too
                    self.open_title_whole(items[-1], tid)
        self.join_title_chains(tracks_)
        for k_, its_ in sorted(tracks_.items()):
            if its_:
                self.v.setdefault(k_, []).extend(its_)

    def join_title_chains(self, tracks):
        """Two titles that meet on one track (the earlier one ends on the later one's first frame) swap the text in
        place: when the earlier one's exit and the later one's entrance would leave the text off (alpha under 0.5,
        a pop under half its size, nothing revealed) for 1 to TITLE_BLINK_MAX_F frames, the earlier title's exit
        and the later title's entrance are skipped, so the text never blinks at the join (note title_chain_joined).
        A title whose animation then moves nothing goes static. Leave a pause of TITLE_BLINK_MAX_F + 2 frames or
        more to keep the fades."""
        f = float(self.tfps)
        joined = []
        for k in sorted(tracks):
            its = sorted(tracks[k], key=lambda x: int(x["rec_in"]))
            for a, b in zip(its, its[1:]):
                if int(a["rec_out"]) != int(b["rec_in"]) or not (a.get("anim") or b.get("anim")):
                    continue
                oa, ob = title_off_frames(a, f), title_off_frames(b, f)
                ta = next((j for j, v in enumerate(reversed(oa)) if not v), len(oa))
                hb = next((j for j, v in enumerate(ob) if not v), len(ob))
                if not 1 <= ta + hb <= TITLE_BLINK_MAX_F:
                    continue
                if ta and a.get("anim"):
                    a["anim"]["skip_out"] = True
                if hb and b.get("anim"):
                    b["anim"]["skip_in"] = True
                joined.append("%s->%s (%d frame%s)" % (a["id"], b["id"], ta + hb, "" if ta + hb == 1 else "s"))
        if not joined:
            return
        static = []
        for k in tracks:
            for it in tracks[k]:
                an = it.get("anim")
                if not an or not (an.get("skip_in") or an.get("skip_out")):
                    continue
                try:
                    stt = fx_lab.anim_states(it, f)
                except Exception:
                    continue
                if not any(abs(s_["alpha"] - 1.0) > 1e-9 or abs(s_["scale"] - 1.0) > 1e-9 or abs(s_["dy"]) > 1e-9
                           or s_["visible"] is not None or s_.get("active") is not None
                           or s_.get("keyword") is not None for s_ in stt):
                    it.pop("anim", None)
                    static.append(it["id"])
        self.note("title_chain_joined", None, "titles that meet on one track swap the text in place: the earlier "
                  "title's exit and the later title's entrance are skipped where they would leave the text off at the "
                  "join (%s)%s; leave a pause of %d frames or more between them to keep those fades" % (
                      "; ".join(joined), ("; nothing else of %s moves, so %s static" % (
                          ", ".join(static), "it is" if len(static) == 1 else "they are")) if static else "",
                      TITLE_BLINK_MAX_F + 2))

    def note(self, code, seg, msg):
        """An assemble note: a change assemble made on its own that the cutter should know about (not a warning)."""
        self.rep.setdefault("notes", []).append({"code": code, "seg": seg, "msg": msg})

    def picture_cuts(self, N):
        """Frames where the picture on top changes (V1 and overlays; titles do not count)."""
        tops = [None] * N
        for k_, x in sorted(((k_, x) for k_, tr_ in self.v.items() for x in tr_ if x.get("kind") != "title"),
                            key=lambda kx: kx[0]):
            a_, b_ = max(0, int(x["rec_in"])), min(N, int(x["rec_out"]))
            if b_ > a_:
                tops[a_:b_] = [x["id"]] * (b_ - a_)
        return [f_ for f_ in range(1, N) if tops[f_] != tops[f_ - 1]]

    def title_timing(self, items, N, base):
        """{track: [titles]}. A title that overlaps one already on the base title track goes on the next track
        ("Titles 2", note title_track_2); three at once is an error. On each track a gap of 1 to 3 frames between two
        titles closes (the earlier one runs on to the later one's start, note title_gap_closed), and a free title edge
        within 3 frames of a picture cut (or an end within 3 frames of the programme end) moves onto it when it
        overlaps nothing and keeps the title's reading time (note title_snapped)."""
        f = float(self.tfps)
        tracks = {base: [], base + 1: []}
        moved = []
        for it in sorted(items, key=lambda x: int(x["rec_in"])):
            def hits(k):
                return [x for x in tracks[k] if int(x["rec_in"]) < int(it["rec_out"]) and int(it["rec_in"]) < int(x["rec_out"])]
            if not hits(base):
                tracks[base].append(it)
            elif not hits(base + 1):
                tracks[base + 1].append(it)
                moved.append(it["id"])
            else:
                others = [x["id"] for x in hits(base) + hits(base + 1)]
                self.error("titles_three", it["id"], "more than two titles at once at %.2f s (%s with %s): the build has "
                           "two title tracks; end one title before the next starts" % (
                               max(int(it["rec_in"]), *[int(x["rec_in"]) for x in hits(base) + hits(base + 1)]) / f,
                               it["id"], ", ".join(others)))
                tracks[base + 1].append(it)
        if moved:
            self.note("title_track_2", ",".join(moved), "title%s %s overlap%s a title on the title track, so %s on a "
                      "second title track (\"Titles 2\") above it" % (
                          "s" if len(moved) > 1 else "", ", ".join(moved), "" if len(moved) > 1 else "s",
                          "they go" if len(moved) > 1 else "it goes"))
        for k in tracks:
            tracks[k].sort(key=lambda x: int(x["rec_in"]))
            for a, b in zip(tracks[k], tracks[k][1:]):
                g = int(b["rec_in"]) - int(a["rec_out"])
                if 1 <= g <= TITLE_GAP_CLOSE_F:
                    self.note("title_gap_closed", "%s,%s" % (a["id"], b["id"]), "titles %s and %s left a %d frame gap "
                              "at frame %d (%.2f s): %s now runs on to %s's start, so the text does not blink" % (
                                  a["id"], b["id"], g, int(a["rec_out"]), int(a["rec_out"]) / f, a["id"], b["id"]))
                    a["rec_out"] = int(b["rec_in"])
        cuts = self.picture_cuts(N)
        snapped = []

        def floor_f(it):
            txt = str(it.get("text", ""))
            need = max(0.833, len(txt) / 20.0, len(txt.split()) * 0.33)
            return int(math.ceil(need * f - 1e-9)) + rnd(0.4 * f)
        for k in tracks:
            its = tracks[k]
            for j, it in enumerate(its):
                prev = its[j - 1] if j else None
                nxt = its[j + 1] if j + 1 < len(its) else None
                for edge in ("rec_in", "rec_out"):
                    e = int(it[edge])
                    if (edge == "rec_in" and (e == 0 or (prev is not None and int(prev["rec_out"]) == e))) or \
                            (edge == "rec_out" and (e >= N or (nxt is not None and int(nxt["rec_in"]) == e))):
                        continue          # the first frame, the end, or a chain edge (it moves with its neighbour)
                    # the programme end counts as a cut for an end: a title that stops 1 to 3 frames short of it
                    # would drop off for the last frames
                    near = [c for c in cuts + ([N] if edge == "rec_out" else []) if 1 <= abs(c - e) <= TITLE_SNAP_F]
                    if not near:
                        continue
                    c = min(near, key=lambda x: (abs(x - e), x))
                    a0, a1 = (c, int(it["rec_out"])) if edge == "rec_in" else (int(it["rec_in"]), c)
                    if a1 - a0 < int(it["rec_out"]) - int(it["rec_in"]) and a1 - a0 < floor_f(it):
                        continue          # shorter than its reading time
                    lo = int(prev["rec_out"]) if prev is not None else 0
                    hi = int(nxt["rec_in"]) if nxt is not None else N
                    if a0 < lo or a1 > hi or a1 <= a0:
                        continue          # it would overlap the title before or after it
                    if (prev is not None and 1 <= a0 - lo <= TITLE_GAP_CLOSE_F) or \
                            (nxt is not None and 1 <= hi - a1 <= TITLE_GAP_CLOSE_F):
                        continue          # it would open a blink gap to a neighbour
                    snapped.append("%s %s %d->%d" % (it["id"], "in" if edge == "rec_in" else "out", e, c))
                    it[edge] = c
        if snapped:
            self.note("title_snapped", None, "title edges moved onto the picture cut within %d frames: %s" % (
                TITLE_SNAP_F, "; ".join(snapped)))
        return tracks

    def open_title_whole(self, it, tid):
        """A title on the programme's first frame with the preset's default animation is whole on that frame
        (KNOWLEDGE H7 and the cover rule: in short form the hook text is on screen from frame 1, and the first frame
        is the cover): its entrance (fade in, slide, pop from 0, typing) is skipped; its exit stays. When nothing
        else moves, the animation goes. The assemble report says so; start the title a frame later to keep the
        entrance. An anim the cut list names stays and first_frame_text (WARN) judges it."""
        an = it.get("anim")
        if not an or int(it["rec_in"]) != 0:
            return
        fps = float(self.tfps)
        try:
            st0 = (fx_lab.anim_states(it, fps) or [None])[0]
        except Exception:
            return
        if st0 is None or fx_lab.state_whole(it, st0):
            return
        an["skip_in"] = True
        stt = fx_lab.anim_states(it, fps)
        moves = any(abs(s_["alpha"] - 1.0) > 1e-9 or abs(s_["scale"] - 1.0) > 1e-9 or abs(s_["dy"]) > 1e-9
                    or s_["visible"] is not None for s_ in stt)
        if not moves:
            it.pop("anim", None)
        self.rep.setdefault("fx_anims", []).append({
            "item": tid, "anim": an["id"] if moves else "none",
            "why": "the title opens the piece: its %s entrance is skipped so the first frame carries the whole text "
                   "(the cover)%s" % (an["id"], "" if moves else "; nothing else of it moves, so it is static")})

    def fit_default_title_anim(self, it, tid, words):
        """A title animation the cut list did not ask for (the preset's default) never costs the title its reading
        time: when the title would be whole on screen for less than its reading floor after the entrance
        (anim_read_floor), it falls back to fade, then to no animation, and the assemble report says so. An older cut
        list keeps passing its gates; an anim the cut list names stays and the check judges it."""
        fps = float(self.tfps)
        if not it.get("anim"):
            return

        def short(item):
            try:
                stt = fx_lab.anim_states(item, fps) or []          # the states fx_checks' read floor uses
            except Exception:
                return False
            if not stt:
                return False
            n_ = len(fx_lab.shown_text(item))
            full = next((k for k, s_ in enumerate(stt) if (s_["visible"] is None or s_["visible"] >= n_)
                         and s_["alpha"] >= 0.99 and s_["scale"] >= 0.95), len(stt))
            return (len(stt) - full) / fps + 1e-9 < fx_lab._read_floor(item.get("text", ""))
        first = it["anim"]["id"]
        if not short(it):
            return
        for cand in [c for c in ("fade",) if c != first] + [None]:
            if cand is None:
                it.pop("anim", None)
                break
            row = fx_lab.anim_row(cand)
            if row is None or not row.get("enabled", True):
                continue
            an = self.anim_for(cand, "title", tid, explicit=True)
            an.update({"word_frames": [0] * len(words), "words": words, "keyword": None})
            it["anim"] = an
            if not short(it):
                break
        self.rep.setdefault("fx_anims", []).append({
            "item": tid, "anim": (it.get("anim") or {}).get("id", "none"),
            "why": "the preset's %s would leave the title too short to read after its entrance" % first})

    def anim_for(self, name, kind, what, explicit=True):
        """A title's or the captions' animation: {"id", "params"} with the preset's anims block over the catalogue
        defaults (ms based), or None. A premium brief turns a default that bounces into fade (titles) or clean_box
        (captions); an explicit bounce stays and fx_text_motion weighs it (FUSION_PLAN s2.4)."""
        if name in (None, "", "none"):
            return None
        if not isinstance(name, str):
            raise Fail("%s: anim must be one name (a string) or none, got %r" % (what, name))
        if not explicit and self.appetite == "none":
            # effects appetite none: the brief wants no effects, so the preset's default title and caption
            # animations are off too (they count as effects); an animation the cut list names stays
            self.rep.setdefault("fx_anims", []).append({
                "item": what, "anim": "none", "why": "effects appetite none: the preset's default %s is off" % name})
            return None
        row = fx_lab.anim_row(name)
        if row is None or not row.get("enabled", True):
            known = sorted(k for k, v in (fx_lab.load_catalog().get("anims") or {}).items() if v.get("enabled", True))
            raise Fail("%s: anim %r is not one of %s (or none)" % (what, name, ", ".join(known)))
        if row.get("kind") not in ("any", kind):
            raise Fail("%s: anim %r animates %ss, not %ss" % (what, name, row.get("kind"), kind))
        if self.premium and row.get("bounce") and not explicit:
            name = "fade" if kind == "title" else "clean_box"
            self.rep.setdefault("fx_anims", []).append({"item": what, "anim": name, "why": "premium brief: no bounce"})
            row = fx_lab.anim_row(name)
        params = json.loads(json.dumps(row.get("params") or {}))
        over = (self.P.get("anims") or {}).get(name)
        if isinstance(over, dict):
            params.update(json.loads(json.dumps(over)))
        # brand tokens ("brand.primary") in the accent colours become the brand's colour (or the catalogue's)
        for blk, key, dflt in (("active", "color", "#FFD93D"), ("active", "box", "#7C3AED"),
                               ("keyword", "color", "#FFD93D")):
            b_ = params.get(blk)
            if isinstance(b_, dict) and isinstance(b_.get(key), str):
                b_[key] = brand_color(b_[key], self.brand, dflt)
        self.fx_used = True
        return {"id": name, "params": params}

    def caption_word(self, w, prev):
        """The word as captioned: capitalised when the start of its sentence was cut away, so it now opens the
        sentence (\"So, um, every cut\" cut to \"Every cut\")."""
        txt = str(w["w"]).strip()
        i = w.get("i")
        if not txt or not isinstance(i, int) or i < 1 or not txt[0].islower() or _camel(txt):
            return txt
        if prev is not None and prev.get("media") == w.get("media") and prev.get("i") == i - 1:
            return txt
        W = self.words_of(w["media"])
        if i >= len(W) + 1:
            return txt
        s0 = i
        while s0 > 0 and not _ends_sentence(W[s0 - 1].get("w", "")):
            s0 -= 1
        if s0 < i and not (prev is not None and prev.get("media") == w.get("media")
                           and isinstance(prev.get("i"), int) and s0 <= prev["i"] < i):
            return txt[0].upper() + txt[1:]
        return txt

    def place_captions(self, N, words_tl):
        cfg = self.cl.get("captions")
        cap = self.P.get("captions") or {}
        if not cfg or cfg.get("from") != "dialogue" or cap.get("style", "word_chunks") == "none":
            return
        style = cfg.get("style") or cap.get("style", "word_chunks")
        wmin, wmax = (cap.get("words") or [1, 4])[0], (cap.get("words") or [1, 4])[-1]
        maxl = int(cap.get("max_lines", 2))
        sbox = safe_box(self.platform, self.W, self.H)
        fpx = float(cap.get("font_px", 64)) * min(self.W, self.H) / 1080.0
        maxc = self.fit_chars(fpx, sbox, cap.get("max_chars_line", 32))
        min_s = float(cap.get("min_s", 0.35))
        lines_ok = 1 if style == "word_chunks" else maxl
        # captions.text_fix {"<media>:<word index>": text}: a word shown as this text ("" hides it in the captions
        # only). The cues are cut from the transcript's words, so a fix never moves a cue
        tfix = cfg.get("text_fix") or {}
        if not isinstance(tfix, dict) or not all(isinstance(v, str) for v in tfix.values()):
            raise Fail("captions.text_fix must map \"<media id>:<word index>\" to the text to show (\"\" hides it)")
        for key in tfix:
            if not re.match(r"^.+:\d+$", str(key)):
                raise Fail("captions.text_fix key %r must be \"<media id>:<word index>\", like \"vo:12\"" % key)
        used_fix = set()
        words = []
        for k, w in enumerate(words_tl):
            words.append(dict(w, w=self.caption_word(w, words_tl[k - 1] if k else None)))
            key = "%s:%s" % (w.get("media"), w.get("i"))
            if key in tfix:
                used_fix.add(key)
                words[-1]["show"] = tfix[key].strip()
            elif "joined" in (w.get("tags") or []):
                # M script: a later piece of one spoken word the transcript wrote as two ("Ink well" for the script's
                # "Inkwell"): heard, but the word before it already shows the script's word
                words[-1]["show"] = ""
        for key in sorted(set(tfix) - used_fix):
            self.warn("text_fix_unused", None, "captions.text_fix %s: that word is not heard on the timeline" % key)
        # a sentence whose opening words text_fix hides ("") opens on the next word shown: that one is capitalised
        for k, w in enumerate(words):
            if w.get("show") != "" or "joined" in (w.get("tags") or []):
                continue
            if k > 0 and not _ends_sentence(words[k - 1]["w"]):
                continue
            j = k + 1
            while j < len(words) and (words[j].get("show") == "" or "joined" in (words[j].get("tags") or [])):
                j += 1
            if j < len(words):
                key_ = "show" if "show" in words[j] else "w"
                t_ = str(words[j][key_]).strip()
                if t_[:1].islower() and not _camel(t_):
                    words[j][key_] = t_[0].upper() + t_[1:]
        # captions.keep_together ["free studio visit", ...]: never split these phrases between cues
        def _nw(s):
            return re.sub(r"[^a-z0-9']", "", str(s).lower().replace("’", "'"))
        glue = set()
        keep_ = [str(ph) for ph in (cfg.get("keep_together") or [])]
        for ph in cfg.get("keep_together") or []:
            pt = [_nw(x) for x in str(ph).split() if _nw(x)]
            for j in range(len(words) - len(pt) + 1):
                if len(pt) >= 2 and all(_nw(words[j + t]["w"]) == pt[t] for t in range(len(pt))):
                    glue.update(range(j, j + len(pt) - 1))
        for k, w in enumerate(words):
            w["_k"] = k
        cues, cur = [], []
        for w in words:
            if cur:
                cand = " ".join(x["w"].strip() for x in cur + [w])
                brk = (w["t0"] - cur[-1]["t1"] >= 0.30 or _ends_phrase(cur[-1]) or w["speaker"] != cur[-1]["speaker"]
                       or len(_wrap(cand, maxc)) > lines_ok or (style == "word_chunks" and len(cur) >= wmax)
                       or (style == "word_chunks" and len(cur) >= 2 and str(cur[-1]["w"]).rstrip().endswith((",", ";", ":")))
                       or (style != "word_chunks" and w["t1"] - cur[0]["t0"] > 7.0))
                # a keep_together phrase stays in one cue while the cue still fits the preset's max_lines (a long
                # phrase at a large caption size breaks where a third line would start: caption_layout is a STOP)
                if brk and cur[-1]["_k"] in glue and w["speaker"] == cur[-1]["speaker"] \
                        and len(_wrap_cue(cand, maxc, keep_)) <= maxl:
                    brk = False
                if brk:
                    # a chunk that breaks only because it is full (words or line length) does not end on "the",
                    # "with", "does" or a possessive ("my", "their"): that word opens the next chunk instead
                    full_only = (w["t0"] - cur[-1]["t1"] < 0.30 and not _ends_phrase(cur[-1])
                                 and w["speaker"] == cur[-1]["speaker"]
                                 and not str(cur[-1]["w"]).rstrip().endswith((",", ";", ":")))
                    if full_only and len(cur) >= 2 and _function_word(cur[-1]["w"]) and cur[-2]["_k"] not in glue:
                        cues.append(cur[:-1])
                        cur = cur[-1:]
                    else:
                        cues.append(cur)
                        cur = []
            cur.append(w)
        if cur:
            cues.append(cur)
        # no orphan: a one-word cue that only broke off because the one before was full takes a word from it,
        # unless the full cue ends on a comma (a natural break) and the lone word is long enough to read
        for k in range(len(cues) - 1):
            a_, b_ = cues[k], cues[k + 1]
            nb = cues[k + 2][0]["t0"] if k + 2 < len(cues) else b_[-1]["t1"] + 0.3
            readable = min(nb, b_[-1]["t1"] + 0.3) - b_[0]["t0"] >= min_s + 0.15
            # take one word, or two when the one left at the end would be "the", "of" or "my"
            mv = 2 if len(a_) >= 4 and _function_word(a_[-2]["w"]) else 1
            # (a keep_together phrase already split at this boundary, because it outgrew max_lines, may give a word)
            if (len(b_) == 1 and len(a_) >= 3 and b_[0]["t0"] - a_[-1]["t1"] < 0.30 and not _ends_phrase(a_[-1])
                    and (a_[-mv - 1]["_k"] not in glue or a_[-1]["_k"] in glue)
                    and not (str(a_[-1]["w"]).rstrip().endswith((",", ";", ":")) and readable)
                    and b_[0]["speaker"] == a_[-1]["speaker"]
                    and len(_wrap(" ".join(x["w"].strip() for x in a_[-mv:] + b_), maxc)) <= lines_ok):
                cues[k], cues[k + 1] = a_[:-mv], a_[-mv:] + b_
        f = float(self.tfps)
        cuts = sorted(set(int(x["rec_in"]) / f for x in self.v.get(1, []) if int(x["rec_in"]) > 0))
        times = []
        for k, c in enumerate(cues):
            t0, last = c[0]["t0"], c[-1]["t1"]
            nxt = cues[k + 1][0]["t0"] if k + 1 < len(cues) else None
            t1 = nxt if (nxt is not None and nxt - last <= 0.7) else last + 0.3
            if nxt is not None:
                t1 = min(t1, nxt)
            # a caption ends at a hard cut that comes within 0.5 s after its last word, or stays 0.5 s past it
            for cut in cuts:
                if last - 1e-6 <= cut < t1 - 1e-6 and cut - last <= 0.5 + 1e-6:
                    if cut - t0 >= min_s - 0.5 / f:
                        t1 = cut
                    elif nxt is None or cut + 0.5 <= nxt:
                        t1 = max(t1, cut + 0.5)
                    break
            times.append([t0, min(t1, N / f)])
        # a blink under 0.5 s between two cues of one sentence reads as a flicker: the first holds until the next
        for k in range(len(times) - 1):
            g0, g1 = times[k][1], times[k + 1][0]
            if 0 < g1 - g0 < 0.5 and not _ends_phrase(cues[k][-1]) \
                    and not any(g0 - 1e-6 <= cut <= g1 + 1e-6 for cut in cuts):
                times[k][1] = g1
        min_f = int(math.ceil(min_s * f - 1e-9))
        fr = []
        for k, (t0, t1) in enumerate(times):
            a, b = rnd(t0 * f), rnd(t1 * f)
            if fr and a < fr[-1][1]:
                a = fr[-1][1]
            b = max(b, a + min_f)
            fr.append([a, min(b, N)])
        for k in range(len(fr) - 1):
            if fr[k][1] > fr[k + 1][0]:
                fr[k + 1][0] = fr[k][1]
                if fr[k + 1][1] < fr[k + 1][0] + min_f:
                    fr[k + 1][1] = min(N, fr[k + 1][0] + min_f)
        yb = cap.get("y_band") or [0.5, 0.61]
        # a spine segment or an overlay may move the captions shown over it ("caption_y": fraction of the height)
        # off the action it shows
        seg_y = {}
        for sg in self.cl.get("spine") or []:
            if isinstance(sg, dict) and sg.get("caption_y") is not None:
                yv = self.num(sg, "caption_y", 0.5)
                if not 0.0 <= yv <= 1.0:
                    raise Fail("segment %s: caption_y is a fraction of the frame height from the top, 0 to 1" % sg.get("id"))
                for vid in self.seg_items.get(sg.get("id"), ([], []))[0]:
                    seg_y[vid] = yv
        for n_, ov in enumerate(self.cl.get("overlays") or []):
            if isinstance(ov, dict) and ov.get("caption_y") is not None:
                oid = str(ov.get("id") or "r%02d" % (n_ + 1))
                yv = self.num(ov, "caption_y", 0.5, what="overlay")
                if not 0.0 <= yv <= 1.0:
                    raise Fail("overlay %s: caption_y is a fraction of the frame height from the top, 0 to 1" % oid)
                seg_y[oid] = yv
        # two bands at most: the preset's band (y_m) and one alternate band, the median of the caption_y values
        # farther than 0.06 from it; every cue snaps to the nearer one (caption_y_snapped), so the block never hunts
        y_m = float(np.mean([float(v) for v in yb]))
        far_ = [v for v in seg_y.values() if abs(v - y_m) > CAPTION_BAND_SNAP + 1e-9]
        # the alternate band comes from the asks on ONE side of the main band (the side with more asks; on a tie
        # the one asked farther away): a median across both sides would land next to the main band and lose every
        # move. Asks on the other side go back to the main band, and assemble warns (caption_y_dropped)
        up_ = [v for v in far_ if v < y_m]
        down_ = [v for v in far_ if v > y_m]
        side_ = up_ if (len(up_), max([y_m - v for v in up_] or [0])) >= (len(down_), max([v - y_m for v in down_] or [0])) \
            else down_
        y_alt = float(np.median(side_)) if side_ else None
        lost_y = {v for v in far_ if v not in side_}
        snapped_, lost_cues = [], []

        def cue_anchor(yv):
            """The first line's centre in pixels for a band at yv: yv * H, kept where a cue of max(max_lines, 1)
            lines fits inside the safe box (when it can fit at all)."""
            pitch_a = float(clook.get("line_spacing") or 1.25) * fpx
            pad_a = look_pad(clook)[1] * fpx
            n_a = max(1, maxl)
            lo_a = sbox[1] + pad_a + 0.5 * pitch_a
            hi_a = sbox[1] + sbox[3] - pad_a - (n_a - 0.5) * pitch_a
            y_a = yv * self.H
            return min(max(y_a, lo_a), hi_a) if hi_a >= lo_a else y_a
        hold_src = getattr(self, "hold_src", None) or {}
        # the picture on top at every frame (a J or L cut's held or early picture counts as the item it comes from)
        # and whether it is an overlay; higher tracks are painted last
        tops = [(None, False)] * N
        for k_, x in sorted(((k_, x) for k_, tr_ in self.v.items() for x in tr_ if x.get("kind") != "title"),
                            key=lambda kx: kx[0]):
            a_, b_ = max(0, int(x["rec_in"])), min(N, int(x["rec_out"]))
            if b_ > a_:
                tops[a_:b_] = [(hold_src.get(x["id"], x["id"]), k_ > 1)] * (b_ - a_)
        # a cue that changes (appears, ends or hands over) within 2 frames of an overlay cut changes on the cut, so
        # the caption never jumps a frame before or after the picture it follows
        ov_cuts = [fr_ for fr_ in range(1, N) if tops[fr_][0] != tops[fr_ - 1][0] and (tops[fr_][1] or tops[fr_ - 1][1])]

        def near_cut(x):
            best = None
            for c_ in ov_cuts:
                if abs(c_ - x) <= 2 and c_ != x and (best is None or abs(c_ - x) < abs(best - x)):
                    best = c_
            return best
        for k in range(len(fr)):
            a, b = fr[k]
            c_ = near_cut(a)
            if c_ is not None:
                if k > 0 and fr[k - 1][1] == a:
                    if c_ - fr[k - 1][0] >= min_f and b - c_ >= min_f:
                        fr[k - 1][1] = fr[k][0] = c_
                elif (k == 0 or fr[k - 1][1] <= c_) and b - c_ >= min_f:
                    fr[k][0] = c_
            a, b = fr[k]
            c_ = near_cut(b)
            if c_ is not None and not (k + 1 < len(fr) and fr[k + 1][0] == b) and c_ - a >= min_f \
                    and (k + 1 == len(fr) or c_ <= fr[k + 1][0]) and c_ <= N:
                fr[k][1] = c_
        ttl = [x for tr_ in self.v.values() for x in tr_ if x.get("kind") == "title"]
        # captions.anim (else the preset's captions.anim) and captions.keywords ["<media>:<index>" or the word]
        can = self.anim_for(cfg.get("anim") if "anim" in cfg else cap.get("anim"), "caption", "captions",
                            explicit="anim" in cfg)
        if can and "anim" not in cfg and can["id"] == cap.get("anim"):
            self.rep.setdefault("fx_anims", []).append({
                "item": "captions", "anim": can["id"], "why": "the preset's default; captions.anim \"none\" keeps "
                                                              "them static"})
        look_id = cfg.get("look") or cap.get("look")
        if can and can["id"] == "clean_box" and not cfg.get("look"):
            # clean_box is a fade into a boxed caption: the box is the look's (the catalogue's look_hint). With the
            # preset's own caption look and no box in it (caption_bold), the box would never draw: use the preset's
            # caption_clean_box look. A look the cut list names stays.
            base_ = resolve_look(self.P, look_id, self.brand, "caption_bold", True)[0]
            if not base_.get("box") and "caption_clean_box" in (self.P.get("looks") or {}):
                self.rep.setdefault("fx_anims", []).append({
                    "item": "captions", "anim": "clean_box", "why": "look caption_clean_box (the box clean_box fades "
                                                                    "in; the preset's %s has none)" % base_["id"]})
                look_id = "caption_clean_box"
        clook = self.look_for(look_id, "caption_bold", "captions", caption=True)
        kws_ = cfg.get("keywords") or []
        if not isinstance(kws_, list):
            raise Fail("captions.keywords must be a list of \"<media id>:<word index>\" or words")
        kw_ids = {str(k_) for k_ in kws_ if re.match(r"^.+:\d+$", str(k_))}
        kw_text = {_nw(k_) for k_ in kws_ if not re.match(r"^.+:\d+$", str(k_))}
        kw_ok = bool(can) and not (isinstance((can.get("params") or {}).get("active"), dict)
                                   or (can.get("params") or {}).get("word_scale_ms"))
        if kws_ and can is not None and not kw_ok:
            self.warn("keywords_ignored", None, "captions.keywords need a caption animation without a spoken-word "
                      "highlight (keyword, clean_box or fade); %s highlights the spoken word" % can["id"])
        for k, c in enumerate(cues):
            a, b = fr[k]
            if b <= a:
                self.warn("caption_dropped", None, "caption %d has no room" % (k + 1))
                continue
            vis = [x for x in c if x.get("show", None) != ""]
            if not vis:
                continue          # every word of the cue is hidden by text_fix
            text = apply_case(" ".join(str(x.get("show", x["w"])).strip() for x in vis).strip(), clook["case"])
            if cfg.get("suppress_under_titles"):
                # a cue that mostly repeats a title on screen with it is dropped (one message, not the same twice)
                cw = [_nw(x) for x in text.split() if _nw(x)]
                dup = None
                for t in ttl:
                    if int(t["rec_in"]) < b and a < int(t["rec_out"]):
                        tw = {_nw(x) for x in " ".join(t.get("lines") or [str(t.get("text", ""))]).split()}
                        if cw and sum(1 for x in cw if x in tw) >= 0.6 * len(cw):
                            dup = t["id"]
                            break
                if dup:
                    self.rep["snaps"].append({"seg": "c%03d" % (k + 1), "edge": "caption", "moved_f": 0,
                                              "why": "suppressed: it repeats title %s" % dup})
                    # not drawn or built, but still spoken: an SRT for the platform's caption upload keeps it
                    self.sub_suppressed.append({"id": "c%03d" % (k + 1), "rec_in": a, "rec_out": b, "text": text,
                                                "lines": _wrap_cue(text, maxc, keep_), "under_title": dup})
                    continue
            lines = _wrap_cue(text, maxc, keep_)
            # the caption follows the picture on top for most of the cue
            votes = {}
            for fr_ in range(a, b):
                yv_ = seg_y.get(tops[fr_][0] if fr_ < N else None)
                votes[yv_] = votes.get(yv_, 0) + 1
            yc = max(votes.items(), key=lambda kv: kv[1])[0] if votes else None
            if yc is None:
                yc = (float(yb[0]) + float(yb[-1])) / 2
            asked_ = yc
            yc = y_alt if (y_alt is not None and abs(yc - y_alt) < abs(yc - y_m)) else y_m
            if abs(asked_ - yc) > 0.005:
                snapped_.append("c%03d %.2f->%.2f" % (k + 1, asked_, yc))
                if asked_ in lost_y:
                    lost_cues.append("c%03d %.2f->%.2f" % (k + 1, asked_, yc))
            # the first line sits where a one-line cue's line sits: a two-line cue grows downward from it. The band's
            # first-line height is set so a cue of the preset's max_lines still fits the safe box (a low 16:9 band
            # moves up a little), so text_box never clamps a longer cue and every cue of a band shares one line
            pitch_ = float(clook.get("line_spacing") or 1.25)
            box = text_box(lines, fpx, sbox[0] + sbox[2] / 2,
                           cue_anchor(yc) + (len(lines) - 1) * fpx * pitch_ / 2.0, sbox, self.W, self.H, clook)
            cue = {"id": "c%03d" % (k + 1), "rec_in": a, "rec_out": b, "text": text,
                   "speaker": c[0]["speaker"], "box": box, "lines": lines, "font_px": round(fpx, 1),
                   "text_w": text_width(lines, fpx, clook), "look": clook,
                   "src_words": [[x.get("media"), x.get("i")] for x in vis]}
            fixed = [[x.get("media"), x.get("i")] for x in c if "show" in x]
            if fixed:
                cue["fixed"] = True
                cue["fixed_words"] = fixed
            if can is not None:
                # word start frames from the cue start (the spoken word's frame on the timeline), and the keyword
                # as a word index of the shown text (a fixed word may show as two)
                words_ = [str(x.get("show", x["w"])).strip() for x in vis]
                wf_ = [max(0, min(b - a - 1, rnd(float(x["t0"]) * f) - a)) for x in vis]
                kw_, nkw_, tok_ = None, 0, 0
                for j, x in enumerate(vis):
                    hit_ = ("%s:%s" % (x.get("media"), x.get("i")) in kw_ids) or (_nw(words_[j]) in kw_text)
                    if hit_ and kw_ok:
                        nkw_ += 1
                        if kw_ is None:
                            kw_ = tok_
                    tok_ += max(1, len(words_[j].split()))
                cue["anim"] = dict(can, word_frames=wf_, words=words_, keyword=kw_)
                if nkw_ > 1:
                    cue["anim"]["keywords_in_cue"] = nkw_
            self.sub.append(cue)
        if snapped_:
            self.note("caption_y_snapped", None, "captions keep two heights, the band at y %.2f%s; moved (asked->placed): "
                      "%s" % (y_m, (" and one alternate band at y %.2f" % y_alt) if y_alt is not None else "",
                               ", ".join(snapped_)))
        if lost_cues:
            self.warn("caption_y_dropped", None, "caption_y asks sit on both sides of the caption band (y %.2f): the "
                      "alternate band is y %.2f, so these cues went back to the band and may cover what they were moved "
                      "off (asked->placed): %s; captions keep one band plus one alternate, so move the other shots' "
                      "captions to the same side, or reframe those shots (frame_x, zoom) instead" % (
                          y_m, y_alt, ", ".join(lost_cues)))

    def audio_leads(self):
        for al in self.cl.get("audio_leads") or []:
            vids, aids = self.seg_items.get(al.get("seg"), ([], []))
            if not aids:
                self.warn("lead_ignored", al.get("seg"), "audio lead on a segment without audio")
                continue
            items = [x for tr in self.a.values() for x in tr if x["id"] == aids[0]]
            it = items[0]
            trk = [x for x in self.a[[k for k, tr in self.a.items() if it in tr][0]]]
            prev = [x for x in trk if x["rec_out"] == it["rec_in"] and x is not it]
            want = rnd(self.num(al, "lead_ms", 0.0, what="audio lead") / 1000.0 * float(self.tfps))
            mid = it["media"]
            sp = speed_of(it)
            r = self.r(mid, sp)
            mf = float(self.mfps(mid))
            lead = min(want, int(math.floor(it["src_in"] / r + 1e-9)))
            why = []
            # (1) the lead may only reach back through the silence before the first kept word of its own source,
            # never into an earlier word (a flubbed take or a dropped filler would come back)
            if it.get("words"):
                W = self.words_of(mid)
                f0 = int(it["words"][0])
                if 0 < f0 < len(W):
                    earliest = int(math.ceil((float(W[f0 - 1]["t1"]) + 0.040) * mf - 1e-6))
                    room = int(math.floor((int(it["src_in"]) - earliest) / r + 1e-9))
                    if room < lead:
                        lead, why = max(0, room), why + ["the pause before word %d" % f0]
            # (2) and only through the previous item's tail after its last kept word
            if prev:
                p = prev[0]
                if p.get("words"):
                    Wp = self.words_of(p["media"])
                    lp = int(p["words"][1])
                    if 0 <= lp < len(Wp):
                        end_w = int(math.ceil(self.rec_of_src_time(p, float(Wp[lp]["t1"]) + 0.040) - 1e-6))
                        room = int(p["rec_out"]) - end_w
                        if room < lead:
                            lead, why = max(0, room), why + ["the tail of %s after its last word" % p["id"]]
                lead = min(lead, rec_len(p) - 1)
            # (3) both new lengths must be ones Resolve can place
            while lead > 0 and not (self.placeable(mid, rec_len(it) + lead, sp)
                                    and (not prev or self.placeable(prev[0]["media"], rec_len(prev[0]) - lead,
                                                                    speed_of(prev[0])))):
                lead -= 1
            if lead <= 0:
                self.warn("lead_ignored", al.get("seg"), "no room for an audio lead%s; for a J cut longer than the "
                          "pause, put the outgoing picture on V2 over the start of %s (KNOWLEDGE section 6)" % (
                              (" (" + ", ".join(why) + ")") if why else "", al.get("seg")))
                continue
            if lead < want:
                self.warn("lead_clamped", al.get("seg"), "audio lead of %s clamped to %d ms (%s allows no more); for "
                          "a longer J cut put the outgoing picture on V2 over the start of %s" % (
                              al.get("seg"), rnd(lead * 1000.0 / float(self.tfps)),
                              ", ".join(why) or "the source", al.get("seg")))
            it["rec_in"] -= lead
            it["src_in"] = it["src_out"] - rnd(rec_len(it) * r)
            if prev:
                p = prev[0]
                p["rec_out"] -= lead
                p["src_out"] = p["src_in"] + rnd(rec_len(p) * self.r(p["media"], speed_of(p)))
            self.rep["snaps"].append({"seg": al.get("seg"), "edge": "audio_lead", "moved_f": -lead, "why": "J cut"})

    def default_fades(self, N):
        for trk in (1, 3):
            items = sorted(self.a.get(trk, []), key=lambda x: x["rec_in"])
            for k, it in enumerate(items):
                prev = items[k - 1] if k else None
                nxt = items[k + 1] if k + 1 < len(items) else None

                def seamless(x, y):
                    return (x is not None and y is not None and x["media"] == y["media"] and x["rec_out"] == y["rec_in"]
                            and x["src_out"] == y["src_in"] and speed_of(x) == speed_of(y) == 1.0)
                if it["rec_in"] > 0 and not seamless(prev, it):
                    it["fade_in"] = max(it.get("fade_in", 0), 1)
                if it["rec_out"] < N and not seamless(it, nxt):
                    it["fade_out"] = max(it.get("fade_out", 0), 1)

    def build(self):
        cl = self.cl
        self.prepare_music()
        N = self.place_spine()
        if N <= 0:
            raise Fail("the cut list has no spine")
        self.place_holds(N)
        self.place_overlays(N)
        self.audio_leads()
        edl = self.edl_skeleton(N)
        words_tl = timeline_words(edl, self.lab)
        self.place_music(N, words_tl)
        self.place_titles(N)
        self.default_fades(N)
        edl = self.edl_skeleton(N)
        words_tl = timeline_words(edl, self.lab)
        self.place_captions(N, words_tl)
        edl = self.edl_skeleton(N)
        # transitions (fx_catalog.json; the generic whip and zoom take their route from the handles, D5 and D6),
        # then the effects baked into the items (they need the transitions: a comp starts where the item is first
        # seen, D3), then the sound effects on their events
        trans = self.place_transitions(edl)
        self.place_fx(trans, words_tl)
        self.place_sfx(N, trans)
        edl = self.edl_skeleton(N)
        edl["transitions"] = trans
        if self.fx_used:
            # the preset's own fx genre when it names one (a lab preset may), else the catalogue's mapping; a
            # premium brief takes the premium budget (FUSION_PLAN s2.4)
            g_ = (self.P.get("fx") or {}).get("genre") if isinstance(self.P.get("fx"), dict) else None
            g_ = g_ if g_ in (fx_lab.load_catalog().get("genres") or {}) else fx_lab.genre_of(self.pid, False)
            g_ = fx_lab.premium_genre(g_, self.premium)
            edl["fx_meta"] = {"genre": g_, "premium": self.premium,
                              "appetite": self.appetite, "catalog": fx_lab.load_catalog().get("version")}
        if self.rep.get("fx_refused"):
            # the refusals travel with the EDL: check, the review and every later gate STOP on them (fx_refused)
            # until the cut list drops or fixes the effect, not only this assemble's report
            edl["fx_refused"] = [{"id": str(r.get("id")), "why": str(r.get("why"))} for r in self.rep["fx_refused"]]
        for mk in cl.get("markers") or []:
            if mk.get("color", "Blue") not in MARKER_COLORS:
                raise Fail("marker %r: color %r is not a Resolve marker colour (%s)" % (
                    mk.get("name", ""), mk.get("color"), ", ".join(MARKER_COLORS)))
            if rnd(self.num(mk, "at_s", 0.0, what="marker") * float(self.tfps)) >= N:
                self.warn("marker_dropped", None, "marker %r at %.2f s is past the programme end (%.2f s)" % (
                    mk.get("name", ""), float(mk.get("at_s", 0.0)), N / float(self.tfps)))
                continue
            edl["markers"].append({"frame": rnd(float(mk.get("at_s", 0.0)) * float(self.tfps)),
                                   "color": mk.get("color", "Blue"), "name": mk.get("name", ""),
                                   "note": mk.get("note", ""), "duration": int(mk.get("duration", 1))})
        tg = cl.get("targets") or {}
        if tg.get("duration_s") is not None:
            edl["targets"]["duration_frames"] = rnd(float(tg["duration_s"]) * float(self.tfps))
        ok_off, bad_off = split_checks_off(cl.get("checks_off"))
        if bad_off:
            self.warn("checks_off_ignored", None, "checks_off %s ignored: a cut list may switch off only warnings and "
                      "%s" % (", ".join(bad_off), ", ".join(sorted(EDL_OFF_OK))))
        # the EDL keeps the whole request: check honours only ok_off and reports the rest as checks_off_ignored
        edl["checks_off"] = [str(c) for c in (cl.get("checks_off") or [])]
        # "mix": {"gain_db": X}: one uniform gain on every audio item for the loudness target (loudness_off says X)
        mx = cl.get("mix")
        if mx is not None:
            g_ = mx.get("gain_db") if isinstance(mx, dict) else None
            if not isinstance(mx, dict) or (g_ is not None and (isinstance(g_, bool) or not isinstance(g_, (int, float)))):
                raise Fail("mix must be {\"gain_db\": a number in dB}, like \"mix\": {\"gain_db\": 1.5} (got %r)" % (mx,))
            if g_ is not None and not MIX_GAIN_RANGE[0] <= float(g_) <= MIX_GAIN_RANGE[1]:
                raise Fail("mix.gain_db %g is outside %g to %+g dB" % (float(g_), MIX_GAIN_RANGE[0], MIX_GAIN_RANGE[1]))
            if g_ is not None:
                edl["mix"] = {"gain_db": round(float(g_), 2), "by": "cutlist"}
        self.overlay_short(N)
        return edl

    def overlay_short(self, N):
        """overlay_short: frames with no picture at all right where an overlay ends or starts: the voice segment under
        it grew past its overlays (a pad or edge change), so the fix is the overlay's length, not a new shot."""
        if N <= 0:
            return
        cov = np.zeros(N, bool)
        ovs = []
        for k_, tr_ in self.v.items():
            for x in tr_:
                if x.get("kind") == "title":
                    continue
                cov[max(0, int(x["rec_in"])):min(N, int(x["rec_out"]))] = True
                if k_ >= 2:
                    ovs.append(x)
        f = float(self.tfps)
        for a, b in _runs(~cov):
            prev = [x for x in ovs if int(x["rec_out"]) == a]
            nxt = [x for x in ovs if int(x["rec_in"]) == b]
            if not prev and not nxt:
                continue          # a plain gap: check reports it as black_gap
            who = (prev or nxt)[0]
            self.warn("overlay_short", who["id"], "frames %d to %d (%.2f to %.2f s, %d frame%s) have no picture next to "
                      "overlay %s: the voice runs past its overlays there; %s %s by %d frame%s (dur_s %+.2f) or cut "
                      "the voice shorter" % (a, b, a / f, b / f, b - a, "" if b - a == 1 else "s", who["id"],
                                             "lengthen" if prev else "start earlier", who["id"], b - a,
                                             "" if b - a == 1 else "s", (b - a) / f))

    def place_transitions(self, edl):
        """The cut list's transitions as EDL entries. cross_dissolve and dip_to_black without extra options keep
        the short entry they always had; every other type is a fx_catalog.json key (or whip and zoom, which take
        the Fusion transition when the handles allow it and the clip-comp pair otherwise) and carries its Resolve
        strings, preview recipe, tolerance, parameters, audio cross fade and event frame."""
        v1 = sorted(self.v[1], key=lambda x: x["rec_in"])
        trans = []
        f = float(self.tfps)
        cuts_done = {}
        for n, x in enumerate(self.cl.get("transitions") or []):
            if not isinstance(x, dict):
                raise Fail("transition %d is not an object" % (n + 1))
            typ = str(x.get("type", "cross_dissolve"))
            fr_ = x.get("frames", 12)
            if isinstance(fr_, bool) or not isinstance(fr_, (int, float)) or fr_ < 1 or abs(fr_ - round(fr_)) > 1e-9:
                raise Fail("transition after %s (%s): frames must be a whole number above 0 (got %r)" % (
                    x.get("after"), typ, fr_))
            if x.get("after") in cuts_done:
                raise Fail("transition after %s (%s): that cut already has a transition (%s); one transition per cut"
                           % (x.get("after"), typ, cuts_done[x.get("after")]))
            cuts_done[x.get("after")] = typ
            vids, _ = self.seg_items.get(x.get("after"), ([], []))
            if not vids:
                if fx_lab.tr_row(typ) is None and typ not in ("whip", "zoom"):
                    raise Fail("transition after %s: type %r is unknown; use a key of fx_catalog.json (`E explain "
                               "transitions`), whip or zoom" % (x.get("after"), typ))
                ov_ids = {str(o_.get("id") or "r%02d" % (k_ + 1)) for k_, o_ in enumerate(self.cl.get("overlays") or [])
                          if isinstance(o_, dict)}
                sp_ids = {str(s_.get("id")) for s_ in (self.cl.get("spine") or []) if isinstance(s_, dict)}
                af_ = x.get("after")
                if str(af_) in ov_ids:
                    why_ = ("%s is an overlay (it sits over V1, so its edges are not cuts between V1 segments); put "
                            "the transition after the V1 segment where the picture should change" % af_)
                elif str(af_) in sp_ids:
                    why_ = "%s is a sound-only segment: it has no picture to leave" % af_
                else:
                    why_ = "there is no segment %r in the cut list's spine or overlays" % (af_,)
                self.warn("transition_ignored", af_, "transition after %s (%s) ignored: %s" % (af_, typ, why_))
                continue
            a = max((i for i in v1 if i["id"] in vids), key=lambda i: i["rec_in"])
            b = [i for i in v1 if i["rec_in"] == a["rec_out"]]
            xid = x.get("id") or "x%03d" % (n + 1)
            legacy = typ in fx_lab.LEGACY_TRANSITIONS and not (
                set(x) - {"after", "type", "frames", "alignment", "id", "why"})
            if legacy:
                if not b:
                    self.warn("transition_ignored", x.get("after"), "transition after %s (%s) ignored: no V1 "
                              "segment starts where %s ends" % (x.get("after"), typ, a["id"]))
                    continue
                trans.append({"id": xid, "track": "V1", "from": a["id"], "to": b[0]["id"], "type": typ,
                              "frames": int(x.get("frames", 12)), "alignment": x.get("alignment", "center")})
                continue
            if not b:
                route = fx_lab.transition_route(x, 0, 0)
                if route["code"] in ("transition_unknown", "transition_broken", "transition_amateur"):
                    raise Fail("transition after %s (%s): %s" % (x.get("after"), typ, route["why"]))
                self.warn("transition_ignored", x.get("after"), "transition after %s (%s) ignored: no V1 "
                          "segment starts where %s ends" % (x.get("after"), typ, a["id"]))
                continue
            b = b[0]
            ed_ = {"media": self.media, "timeline": {"fps": fps_str(self.tfps)}}
            tail, head = handle_frames(ed_, a, "tail"), handle_frames(ed_, b, "head")
            route = fx_lab.transition_route(x, tail, head)
            if route["code"] in ("transition_unknown", "transition_broken", "transition_amateur"):
                raise Fail("transition after %s (%s): %s" % (x.get("after"), typ, route["why"]))
            key, row = route["key"], route["row"]
            d = int(x.get("frames", 12))
            al = str(x.get("alignment") or "center")
            fam = row.get("family")
            params = {}
            if typ in ("whip", "zoom"):
                params["asked"] = typ
            if fam == "whip" or key in ("custom_whip", "whip_pair"):
                dr = str(x.get("direction") or "left")
                if dr not in ("left", "right", "up", "down"):
                    raise Fail("transition after %s: direction must be left, right, up or down" % x.get("after"))
                params["direction"] = dr
            if key == "custom_whip":
                params.update({"quality": 8, "shutter": 360})
            if key in ("custom_zoom_overlap", "custom_zoom_through"):
                xw_ = "transition after %s (%s)" % (x.get("after"), typ)
                params["peak"] = round(self.num(x, "peak", 2.5, positive=True, what=xw_), 4)
                try:
                    params["point"] = fx_lab._point(x.get("point"), "transition after %s" % x.get("after"))
                except fx_lab.FxError as e:
                    raise Fail(str(e))
                if key == "custom_zoom_overlap":
                    params["mix"] = int(self.num(x, "mix", 2, positive=True, what=xw_))
                    params["motion_blur"] = x.get("motion_blur", True) is not False
            if row.get("motion_blur"):
                params["motion_blur"] = x.get("motion_blur", True) is not False
            pair = route["build"] == "clip_pair"
            if pair:
                n_out = (d // 2) if key == "custom_zoom_through" else max(1, int(round(d * 3 / 7.0)))
                params.update({"n_out": n_out, "n_in": max(1, d - n_out)})
            linked = [it_ for k_ in (1, 3) for it_ in self.a.get(k_, []) if int(it_["rec_out"]) == int(a["rec_out"])
                      or int(it_["rec_in"]) == int(b["rec_in"])]
            audio = str(x.get("audio") or ("plus3" if (linked and not pair) else "none"))
            if audio not in ("plus3", "zero", "none"):
                raise Fail("transition after %s: audio must be plus3, zero or none" % x.get("after"))
            res_ = dict(row.get("resolve") or {})
            if not params.get("motion_blur", True):
                res_["macro_inputs"] = {}
            ent = {"id": xid, "track": "V1", "from": a["id"], "to": b["id"], "type": key, "frames": d,
                   "alignment": al, "params": params, "resolve": res_,
                   "preview": {"recipe": (row.get("preview") or {}).get("recipe"),
                               "class": (row.get("preview") or {}).get("class")},
                   "tolerance": row.get("tolerance"), "audio": audio, "event_f": int(a["rec_out"])}
            if x.get("look_is_brief") is True:
                ent["look_is_brief"] = True
            if x.get("why"):
                ent["why"] = str(x["why"])
            if not route["ok"] and route["code"] not in ("transition_handles", "transition_through_black"):
                # (short handles and a fade through black are reported by validate, with the frames to add)
                self.error(route["code"], x.get("after"), "transition %s (%s): %s" % (xid, key, route["why"]))
            if pair:
                side = {"kind": "whip" if key == "whip_pair" else "zoom", "id": xid,
                        "direction": params.get("direction", "left"), "peak": params.get("peak", 2.5),
                        "point": params.get("point")}
                self.pairs.setdefault(a["id"], []).append(dict(side, side="out", n=params["n_out"],
                                                                n_other=params["n_in"]))
                self.pairs.setdefault(b["id"], []).append(dict(side, side="in", n=params["n_in"],
                                                                n_other=params["n_out"]))
            if typ in ("whip", "zoom") or pair:
                self.rep.setdefault("fx_routes", []).append({"transition": xid, "after": x.get("after"),
                                                             "asked": typ, "key": key, "route": route["build"],
                                                             "why": route.get("why") or "named in the cut list"})
            self.fx_used = True
            trans.append(ent)
        return trans

    def place_fx(self, trans, words_tl):
        """Bake every segment's and overlay's fx and retime (fx_lab.bake_segment_fx) and give each item its slice of
        the curves: frames item-relative, from minus the frames seen under an incoming transition to the item's
        length plus those under the outgoing one (D3). A segment split around a dropped filler gets one curve cut
        into pieces with equal values at the joins."""
        heads, tails = {}, {}
        for x in trans:
            if ((x.get("resolve") or {}).get("build")) == "clip_pair":
                continue
            a_after, b_before = fx_lab.overlap_of(int(x["frames"]), x.get("alignment", "center"))
            tails[x["from"]] = tails.get(x["from"], 0) + a_after
            heads[x["to"]] = heads.get(x["to"], 0) + b_before
        segs = [(s.get("id"), s, True) for s in (self.cl.get("spine") or []) if isinstance(s, dict)]
        for n, ov in enumerate(self.cl.get("overlays") or []):
            if isinstance(ov, dict):
                segs.append((str(ov.get("id") or "r%02d" % (n + 1)), ov, False))
        all_v = [it for k in self.v for it in self.v[k] if it.get("kind", "clip") == "clip"]
        rep_ref, rep_clip = [], []
        self._place_fx_segs(segs, all_v, heads, tails, words_tl, rep_ref, rep_clip)
        if rep_ref:
            self.rep.setdefault("fx_refused", []).extend(rep_ref)
        if rep_clip:
            self.rep.setdefault("fx_clipped", []).extend(rep_clip)

    def _place_fx_segs(self, segs, all_v, heads, tails, words_tl, rep_ref, rep_clip):
        for sid, seg, spine in segs:
            vids = self.seg_items.get(sid, ([], []))[0] if spine else [sid]
            aids = self.seg_items.get(sid, ([], []))[1] if spine else []
            items = sorted([it for it in all_v if it["id"] in vids], key=lambda it: int(it["rec_in"]))
            if not items:
                continue
            pairs = [p for p in self.pairs.get(items[0]["id"], []) if p["side"] == "in"] + \
                    [p for p in self.pairs.get(items[-1]["id"], []) if p["side"] == "out"]
            proc = seg.get("retime_process")
            if proc is not None and proc not in ("nearest", "speed_warp"):
                raise Fail("segment %s: retime_process must be nearest or speed_warp" % sid)
            if proc == "speed_warp" and seg.get("retime"):
                # a ramp or freeze is keyed on whole source frames with the item at 100 %: Speed Warp would have
                # nothing to retime and the slow part would still repeat frames (measured on the fake build keys)
                raise Fail("segment %s: retime_process speed_warp works only with a constant segment speed under 1; "
                           "a ramp or freeze plays whole source frames at 100 %%, so Speed Warp cannot smooth it. Drop "
                           "retime_process, or use 50 or 60 fps media for the slow part" % sid)
            if not (seg.get("fx") or seg.get("retime") or pairs or proc == "speed_warp"):
                continue
            self.fx_used = True
            mid = items[0]["media"]
            rec0 = int(items[0]["rec_in"])
            L = int(items[-1]["rec_out"]) - rec0
            ctx = {"id": sid, "L": L, "head": heads.get(items[0]["id"], 0), "tail": tails.get(items[-1]["id"], 0),
                   "rec_in": rec0, "W": self.W, "H": self.H,
                   "kind": "image" if self.use(mid).get("kind") == "image" else "clip", "media": mid,
                   "media_fps": float(self.mfps(mid)), "src_in": int(items[0]["src_in"]),
                   "media_frames": self.frames_of(mid), "speed": speed_of(items[0]),
                   "item_ids": [it["id"] for it in items], "audio_ids": list(aids), "split": len(items) > 1,
                   "pairs": pairs, "overlay": not spine}
            try:
                # programme beats: {"beat": 0} is the first beat on or after the programme's first frame
                beats_ = [b for b in self.beat_frames if float(b) > -0.5]
                fxl, mo, acc, rt = fx_lab.bake_segment_fx(seg, ctx, float(self.tfps), beats_, words_tl)
            except fx_lab.FxError as e:
                rep_ref.append({"id": sid, "why": str(e)})
                self.error("fx_refused", sid, str(e))
                continue
            for x in fxl:
                if x.get("refused"):
                    rep_ref.append({"id": x["id"], "why": x["refused"]})
                    self.error(x.get("code") or "fx_refused", sid, "%s (%s): %s" % (x["id"], x.get("kind"), x["refused"]))
                elif x.get("clipped"):
                    rep_clip.append({"id": x["id"], "f": x.get("f"), "L": L})
            good = [x for x in fxl if not x.get("refused")]
            for k, it in enumerate(items):
                o = int(it["rec_in"]) - rec0
                Li = rec_len(it)
                lo_i = o - heads.get(it["id"], 0)
                hi_i = o + Li + tails.get(it["id"], 0) - 1
                # a whip pair keys one frame past the cut on each side (its motion blur samples move there too)
                mlo_i = lo_i - (1 if k == 0 and any(p["side"] == "in" and p["kind"] == "whip" for p in pairs) else 0)
                mhi_i = hi_i + (1 if k == len(items) - 1 and any(p["side"] == "out" and p["kind"] == "whip"
                                                                 for p in pairs) else 0)
                if mo:
                    ks = {}
                    for name, keys in (mo.get("keys") or {}).items():
                        sl = fx_lab.shift_keys(fx_lab.slice_keys(keys, mlo_i, mhi_i), -o)
                        if fx_lab.keys_vary(sl, 1.0 if name == "zoom" else 0.0):
                            ks[name] = sl
                    if ks:
                        mo_i = {"point": mo.get("point"), "edges": mo.get("edges") if any(
                            n_ in ks for n_ in ("x_px", "y_px", "angle")) else None,
                                "motion_blur": mo.get("motion_blur") if (
                                    (k == 0 and any(p["side"] == "in" for p in pairs)) or
                                    (k == len(items) - 1 and any(p["side"] == "out" for p in pairs))) else None,
                                "keys": ks}
                        if mo.get("cover_zoom"):
                            mo_i["cover_zoom"] = mo["cover_zoom"]
                        it["motion"] = mo_i
                if acc:
                    acc_i = {}
                    for name in ("flash", "rgb_px"):
                        sl = fx_lab.shift_keys(fx_lab.slice_keys(acc.get(name) or [], lo_i, hi_i), -o)
                        acc_i[name] = sl if fx_lab.keys_vary(sl, 0.0) else []
                    # every leak and glitch that OVERLAPS the frames this item is seen goes to it (a leak centred
                    # on the cut starts before the item's first frame; each piece of a split segment gets its part)
                    lk = acc.get("leak")
                    acc_i["leak"] = (dict(lk, f=[lk["f"][0] - o, lk["f"][1] - o])
                                     if lk and lk["f"][0] <= hi_i and lk["f"][1] >= lo_i else None)
                    gl = [dict(g, f=[g["f"][0] - o, g["f"][1] - o]) for g in acc.get("glitch") or []
                          if g["f"][0] <= hi_i and g["f"][1] >= lo_i]
                    if gl:
                        acc_i["glitch"] = gl
                    if acc_i["flash"] or acc_i["rgb_px"] or acc_i["leak"] or gl:
                        it["accents"] = acc_i
                mine = []
                for x in good:
                    ev = int(x.get("event_f", (x.get("f") or [0])[0]))
                    # the item that shows the event frame owns the record (before the first item: the first,
                    # after the last: the last)
                    owner = len(items) - 1
                    for j_, it_ in enumerate(items):
                        if ev < int(it_["rec_in"]) - rec0 + rec_len(it_):
                            owner = j_
                            break
                    if owner == k:
                        y = dict(x)
                        y["f"] = [int(x["f"][0]) - o, int(x["f"][1]) - o]
                        y["event_f"] = ev - o
                        if y.get("land_f") is not None:
                            y["land_f"] = int(y["land_f"]) - o
                        if y.get("anchor_f") is not None:
                            y["anchor_f"] = int(y["anchor_f"]) - o
                        mine.append(y)
                if mine:
                    it["fx"] = mine
                if rt is not None and len(items) == 1:
                    it["retime"] = rt
                if proc:
                    it["retime_process"] = proc

    def place_sfx(self, N, trans):
        """Sound effects (FUSION_PLAN s1.8): each file's loudest moment (its peak, stored by `M add --sfx`) lands on
        its event frame: a transition's cut, an fx's event, a title's first frame or a time. Items go on SFX tracks
        (A4 up), one more track where two overlap."""
        f = float(self.tfps)
        for n, s in enumerate(self.cl.get("sfx") or []):
            if not isinstance(s, dict):
                raise Fail("sfx %d is not an object" % (n + 1))
            sxid = str(s.get("id") or "x%02d" % (n + 1))
            mid = s.get("media")
            if not mid or mid not in self.lab.index["media"]:
                raise Fail("sfx %s: media %r is not in this lab (add the user's sound effects with `M add PATH "
                           "--sfx ID`)" % (sxid, mid))
            m = self.use(mid)
            if m.get("kind") not in ("audio", "av"):
                raise Fail("sfx %s: media %s has no sound" % (sxid, mid))
            on = s.get("on") or {}
            if not isinstance(on, dict):
                raise Fail("sfx %s: on must be an object: {\"transition\": seg}, {\"seg\": id, \"fx\": n}, "
                           "{\"title\": id} or {\"at_s\": t} (got %r)" % (sxid, on))
            ev, target = None, None
            if "transition" in on:
                xs = [x for x in trans if x["from"] in self.seg_items.get(on["transition"], ([], []))[0]]
                if not xs:
                    raise Fail("sfx %s: no transition after segment %r" % (sxid, on["transition"]))
                ev, target = int(xs[0].get("event_f", 0)), xs[0]["id"]
            elif "seg" in on:
                fn_ = on.get("fx", 1)
                if isinstance(fn_, bool) or not isinstance(fn_, (int, float)) or fn_ < 1 or fn_ != int(fn_):
                    raise Fail("sfx %s: on.fx must be the fx number in segment %s, counted from 1 (got %r)" % (
                        sxid, on["seg"], fn_))
                fid = "%s.fx%d" % (on["seg"], int(fn_))
                for it in [it for k in self.v for it in self.v[k]]:
                    for x in it.get("fx") or []:
                        if x.get("id") == fid:
                            # a punch's sound on its word, the others on their event (fx_lab.sound_frame)
                            ev, target = int(it["rec_in"]) + fx_lab.sound_frame(x), fid
                if ev is None:
                    raise Fail("sfx %s: segment %s has no fx %s (fx count from 1)" % (sxid, on["seg"], on.get("fx", 1)))
            elif "title" in on:
                tt = [it for k in self.v for it in self.v[k] if it.get("id") == on["title"] and it.get("kind") == "title"]
                if not tt:
                    raise Fail("sfx %s: title %r is unknown" % (sxid, on["title"]))
                ev, target = int(tt[0]["rec_in"]), on["title"]
            elif "at_s" in on:
                ev, target = rnd(self.num(on, "at_s", 0.0, what="sfx") * f), "at_%gs" % float(on["at_s"])
            else:
                raise Fail("sfx %s: on must be {\"transition\": seg}, {\"seg\": id, \"fx\": n}, {\"title\": id} or "
                           "{\"at_s\": t}" % sxid)
            pk = (self.lab.media(mid).get("sfx") or {}).get("peak_s")
            if pk is None:
                wavp = self.lab.media(mid).get("wav")
                x_ = fx_lab.read_wav_mono(self.lab.rel(wavp)) if wavp else None
                if x_ is not None and len(x_):
                    pk = fx_lab.audio_peak_s(x_, getattr(fx_lab.read_wav_mono, "sr", SR))
                else:
                    pk = 0.0
                    self.warn("sfx_peak_unknown", sxid, "sfx %s: the peak of %s is unknown (run `M add PATH --sfx ID` and "
                              "`M ingest`); its start sits on the event" % (sxid, mid))
            pf = rnd(float(pk) * f)
            rec, src_in = ev - pf, 0
            if rec < 0:
                src_in, rec = -rec, 0
            total = self.frames_of(mid) or rnd(float(m.get("duration_s") or 1.0) * f)
            nlen = min(int(total) - src_in, N - rec)
            if nlen < 1:
                self.warn("sfx_dropped", sxid, "sfx %s has no room inside the programme" % sxid)
                continue
            k = 4
            while k in self.a and (k not in self.sfx_tracks or any(
                    x["rec_in"] < rec + nlen and rec < x["rec_out"] for x in self.a[k])):
                k += 1
            gain_ = self.num(dict(s, id=sxid), "gain_db", -6.0, what="sfx")
            it = self.a_item("sfx_%s" % sxid, mid, src_in, nlen, rec, {"id": sxid}, 1.0, k, gain_)
            it.update({"for": target, "event_f": ev, "peak_f": rec + pf - src_in})
            if src_in > 0:
                it["fade_in"] = 1
            self.sfx_tracks.add(k)
            self.fx_used = True

    def edl_skeleton(self, N):
        stem = os.path.splitext(os.path.basename(self.out_path or "v001.json"))[0]
        vid = stem if re.match(r"^v\d{3,}$", stem) else "v001"
        names = {1: "Picture", 2: "Overlays"}
        video = []
        n_titles = 0
        for k in sorted(self.v):
            if self.v[k] or k == 1:
                kinds = {x.get("kind") for x in self.v[k]}
                if kinds == {"title"}:
                    n_titles += 1
                    name = "Titles" if n_titles == 1 else "Titles %d" % n_titles
                else:
                    name = names.get(k, "Video %d" % k)
                video.append({"id": "V%d" % k, "name": name, "items": sorted(self.v[k], key=lambda x: x["rec_in"])})
        roles = {1: ("Dialogue", "dialogue"), 2: ("Music", "music"), 3: ("Nat sound", "nat")}
        for k in self.sfx_tracks:
            roles[k] = ("SFX" if k == min(self.sfx_tracks) else "SFX %d" % (k - min(self.sfx_tracks) + 1), "sfx")
        audio = [{"id": "A%d" % k, "name": roles.get(k, ("Audio %d" % k, "other"))[0],
                  "role": roles.get(k, ("", "other"))[1], "items": sorted(self.a[k], key=lambda x: x["rec_in"])}
                 for k in sorted(self.a) if self.a[k]]
        sub = [{"id": "ST1", "items": self.sub}] if self.sub else []
        return {"schema": EDL_SCHEMA,
                "version": {"id": vid, "parent": None, "by": self.cl.get("by") or self.cl.get("id") or "assemble",
                            "created": iso_now(), "summary": self.cl.get("summary", ""), "changes": [],
                            "from_cutlist": self.lab.relto(self.cutlist_path) if self.cutlist_path else None},
                "preset": self.pid, "platform": self.platform,
                "timeline": {"name": self.lab.project.get("name") or os.path.basename(self.lab.path),
                             "fps": fps_str(self.tfps), "drop_frame": is_drop_tc(self.start_tc), "width": self.W,
                             "height": self.H, "start_tc": self.start_tc, "audio_rate": SR,
                             "resolve": {"frame_rate_mismatch": "resolve",
                                         "input_sizing": self.P["timeline"].get("input_sizing", "scaleToCrop"),
                                         "retime": "nearest"}},
                "media": self.media, "tracks": {"video": video, "audio": audio, "subtitle": sub},
                "transitions": [], "markers": [], "music": self.music_block,
                "captions_suppressed": list(self.sub_suppressed),
                "targets": {"duration_frames": None, "loudness_lufs": self.P["audio"].get("lufs"),
                            "true_peak_db": self.P["audio"].get("true_peak_db")},
                "checks_off": []}


def cmd_assemble(lab, cutlist_path, out_path, as_json=False):
    cl = load_json(cutlist_path)
    if cl.get("schema") != "resolve-editor/cutlist@1":
        raise Fail("%s is not a cut list (schema resolve-editor/cutlist@1); `E schema cutlist` shows one" % cutlist_path)
    A = Assembler(lab, copy.deepcopy(cl), cutlist_path, out_path)
    edl = A.build()
    ends = [mu for mu in cl.get("music") or [] if isinstance(mu, dict) and mu.get("align") == "end" and mu.get("media")]
    align_note = None
    if ends:
        # "align": "end": start the song where its natural end lands on the programme end. The beat grid moves with
        # in_s and the beat segments move with the grid, so search in_s (frame steps, up to two bars either side)
        # for a length that settles; else take the closest one with the song still playing at the end
        mu = ends[0]
        f = float(tl_fps(edl))
        dur = lab.media(mu["media"]).get("duration_s")
        if dur:
            at = float(mu.get("at_s", 0.0))
            want0 = float(dur) - (programme_frames(edl) / f - at)
            bpm = float((lab.analysis(mu["media"], "beats") or {}).get("bpm") or 120.0)
            span = int(math.ceil(2 * 4 * 60.0 / bpm * f))
            best = None
            for k in range(-span, span + 1):
                ins = round(want0 + k / f, 4)
                if ins < 0:
                    continue
                c2 = copy.deepcopy(cl)
                [m2 for m2 in c2["music"] if m2.get("align") == "end"][0]["in_s"] = ins
                try:
                    e2 = Assembler(lab, c2, cutlist_path, out_path).build()
                except Fail:
                    continue
                g = float(dur) - (programme_frames(e2) / f - at) - ins
                key = (0 if abs(g) <= 0.5 / f else 1, abs(g) if abs(g) <= 0.5 / f else (g if g > 0 else 1e9 - g), abs(k))
                if best is None or key < best[0]:
                    best = (key, ins, g)
            if best:
                mu["in_s"] = best[1]
                A = Assembler(lab, copy.deepcopy(cl), cutlist_path, out_path)
                edl = A.build()
                if abs(best[2]) > 0.5 / f:
                    align_note = ("music %s: no start point puts the song's end exactly on the programme end with these "
                                  "beat segments; it now runs %.2f s past the end (lengthen the last segment by %d "
                                  "frames, or let it fade)" % (mu.get("id") or mu["media"], best[2], rnd(best[2] * f)))
            elif want0 < 0:
                align_note = ("music %s: the song (%.2f s) is shorter than the programme after its start, so it cannot "
                              "end with it; it plays from in_s %.2f and stops %.2f s before the end (use a longer song, "
                              "shorten the edit, or cover the end with another music item)" % (
                                  mu.get("id") or mu["media"], float(dur), float(mu.get("in_s", 0.0)), -want0))
    outro_note = None
    if ends:
        # a short piece aligned to the song's end plays only its outro, usually the quietest part: say where a
        # strong section starts instead
        mu = ends[0]
        bt = lab.analysis(mu["media"], "beats") or {}
        secs = [s for s in bt.get("sections") or [] if isinstance(s, dict) and s.get("energy") is not None]
        dur = lab.media(mu["media"]).get("duration_s")
        plen = programme_frames(edl) / float(tl_fps(edl)) - float(mu.get("at_s", 0.0))
        ins = float(mu.get("in_s", 0.0))
        if secs and dur and plen < 0.5 * float(dur):
            used = [s for s in secs if float(s["t1"]) > ins and float(s["t0"]) < ins + plen]
            top = max(float(s["energy"]) for s in secs)
            e_used = (sum(float(s["energy"]) * (min(float(s["t1"]), ins + plen) - max(float(s["t0"]), ins)) for s in used)
                      / max(1e-6, plen)) if used else 0.0
            if top > 0 and e_used < 0.6 * top:
                fit = [s for s in secs if float(s["t0"]) + plen <= float(dur)] or secs
                best = max(fit, key=lambda s: (float(s["energy"]), -float(s["t0"])))
                outro_note = ("music %s: aligned to its end, this %.1f s piece plays only the song's last part, which is "
                              "quiet (energy %.2f of its loudest section): for a short piece start at a strong section "
                              "instead, for example \"in_s\": %.2f (section %s, energy %.2f) without \"align\", and "
                              "fade it out (\"fade_out_s\")" % (mu.get("id") or mu["media"], plen, e_used / top,
                                                               float(best["t0"]), best.get("label"), float(best["energy"])))
    errs, warns = validate(edl, lab)
    rep = A.rep
    if outro_note:
        rep["warnings"].append({"code": "align_end_outro", "seg": ends[0].get("id"), "msg": outro_note})
    for mu in ends[:1]:
        rep["snaps"].append({"seg": mu.get("id") or mu["media"], "edge": "music", "moved_f": 0,
                             "why": "align end: music in_s %.3f" % float(mu.get("in_s", 0.0))})
    if align_note:
        rep["warnings"].append({"code": "align_end_off", "seg": ends[0].get("id"), "msg": align_note})
    for wc in words_changed(edl, lab):
        rep["errors"].append({"code": "words_changed", "seg": wc["item"], "msg": words_changed_msg(wc)})
    for iid, at, msg, fix in frame_edge_issues(edl, lab):
        # the same finding is a STOP in check and review, so assemble says so at once
        rep["errors"].append({"code": "frame_edge", "seg": iid, "msg": "%s; fix: %s" % (msg, fix)})
    r_fps = project_fps(lab)
    if r_fps is not None and r_fps != tl_fps(edl):
        msg = fps_mismatch_msg(edl, r_fps)
        if (cl.get("timeline") or {}).get("fps"):
            msg = ("the cut list sets timeline.fps %s but the Resolve project runs at %s fps: set the cut list's "
                   "timeline.fps to null (the lab's rate then applies) and assemble again"
                   % ((cl.get("timeline") or {}).get("fps"), fps_label(r_fps)))
        rep["errors"].append({"code": "fps_mismatch", "seg": None, "msg": msg})
    rep["edl"] = posix(out_path)
    rep["validate"] = {"errors": errs, "warnings": warns}
    res = "STOP" if (errs or rep["errors"]) else ("WARN" if (warns or rep["warnings"] or rep["fillers"]["kept"]) else "OK")
    rep["result"] = res
    rep["length_frames"] = programme_frames(edl)
    write_json(out_path, edl)
    side = os.path.splitext(out_path)[0] + ".assemble.json"
    write_json(side, rep)
    if as_json:
        print(json.dumps(rep, indent=1))
        return edl, rep
    lines = ["LENGTH %d f = %.2f s at %s fps" % (rep["length_frames"], rep["length_frames"] / float(tl_fps(edl)), fps_label(tl_fps(edl)))]
    lines += ["ERROR %s %s: %s" % (e["code"], e["item"] or "-", e["msg"]) for e in errs]
    lines += ["ERROR %s %s: %s" % (e["code"], e["seg"] or "-", e["msg"]) for e in rep["errors"]]
    lines += ["WARN %s %s: %s" % (w["code"], w["item"] or "-", w["msg"]) for w in warns]
    lines += ["WARN %s %s: %s" % (w["code"], w["seg"] or "-", w["msg"]) for w in rep["warnings"]]
    lines += ["WARN filler_kept_unclean %s: words %s %r (gaps %s / %s s): no pause to cut at; to lose it, split the "
              "range around it (end the segment at word %d, start the next at word %d)" % (
                  k["seg"], k["words"], k["text"],
                  k["gap_before_s"] if k["gap_before_s"] is None else round(k["gap_before_s"], 3),
                  k["gap_after_s"] if k["gap_after_s"] is None else round(k["gap_after_s"], 3),
                  k["words"][0] - 1, k["words"][-1] + 1) for k in rep["fillers"]["kept"]]
    lines += ["note dropped %s: words %s %r" % (d["seg"], d["words"], d["text"]) for d in rep["fillers"]["dropped"]]
    lines += ["note pause shortened %s after word %d: %.2f s -> %.2f s" % (p["seg"], p["after_word"], p["pause_s"], p["kept_s"])
              for p in rep["pauses_shortened"]]
    lines += ["note route %s after %s: %s -> %s (%s): %s" % (r_["transition"], r_["after"], r_["asked"], r_["key"],
                                                          r_["route"], r_.get("why") or "")
              for r_ in rep.get("fx_routes") or []]
    lines += ["note anim %s: %s (%s)" % (a_["item"], a_["anim"], a_["why"]) for a_ in rep.get("fx_anims") or []]
    lines += ["note %s: %s" % (n_["code"], n_["msg"]) for n_ in rep.get("notes") or []]
    lines += ["note fx clipped %s at frames %s of a %d-frame segment" % (c_["id"], c_.get("f"), c_.get("L", 0))
              for c_ in rep.get("fx_clipped") or []]
    print_result(res, lines, [out_path, side])
    return edl, rep


# ------------------------------------------------------------------------------------------------ preview: picture
def even(x):
    return max(2, int(round(float(x) / 2.0)) * 2)


def preview_size(W, H):
    a = W / float(H)
    if abs(a - 16 / 9.0) < 0.02:
        return 640, 360
    if abs(a - 9 / 16.0) < 0.02:
        return 360, 640
    if abs(a - 1.0) < 0.02:
        return 480, 480
    return (640, even(640 / a)) if a >= 1 else (even(640 * a), 640)


def review_dir(edl_path, out=None):
    if out:
        return os.path.abspath(out)
    stem = os.path.splitext(os.path.basename(edl_path))[0]
    here = os.path.dirname(os.path.abspath(edl_path))
    if os.path.basename(here) == "edits" and os.path.exists(os.path.join(os.path.dirname(here), "project.json")):
        # adopted versions stay untouched: their reviews go to LAB/reviews/
        return os.path.join(os.path.dirname(here), "reviews", "review_%s" % stem)
    return os.path.join(here, "review_%s" % stem)


def count_frames(path):
    r = run([ffprobe_bin(), "-v", "error", "-count_packets", "-select_streams", "v:0", "-show_entries",
             "stream=nb_read_packets", "-of", "csv=p=0", path], "ffprobe")
    txt = r.stdout.decode().strip().split(",")[0].strip()
    return int(txt) if txt.isdigit() else -1


def visible_video(edl):
    """Video items that can be seen (clip or solid, enabled), bottom track first."""
    out = []
    for tr in sorted(edl["tracks"]["video"], key=lambda t: track_num(t.get("id"))):
        for it in tr.get("items", []):
            if it.get("kind", "clip") in ("clip", "solid") and it.get("enabled", True) is not False:
                out.append((track_num(tr.get("id")), it))
    return out


def flatten(edl, N=None, lab=None, shots=False):
    """Top visible item per timeline frame -> pieces: ('seg', r0, r1, item), ('gap', r0, r1), ('xfade', r0, r1, x).
    Also returns notes (transitions that could not be previewed).

    shots=True is the shot model of the checks and the report: an item the pictures under it show through
    (shot_see_through: a logo with transparency, a zoomed-out or moved overlay, an opacity under 100) is not the
    shot; the first item under it that covers the frame is, so its cuts count. Where nothing under it covers the
    frame (a logo on black) the see-through item is the shot. A whole picture fit to the frame whose bars leave most
    of the frame covered (a DCI, drone or 4:3 cutaway on a 16:9 timeline) is the shot. The preview uses shots=False
    and composites such items."""
    N = programme_frames(edl) if N is None else N
    vis = visible_video(edl)
    top = np.full(N, -1, dtype=np.int64)
    seen = np.zeros(N, dtype=bool)
    for k, (tn, it) in enumerate(vis):
        a, b = max(0, int(it["rec_in"])), min(N, int(it["rec_out"]))
        if b > a:
            if shots and shot_see_through(edl, lab, it):
                # under the covering items of lower tracks, over black and over other see-through items
                top[a:b] = np.where(seen[a:b], top[a:b], k)
                continue
            top[a:b] = k
            seen[a:b] = True
    pos = {id(it): k for k, (tn, it) in enumerate(vis)}
    by_id = {it["id"]: it for tn, it in vis}
    notes, xfs = [], []
    for x in edl.get("transitions", []):
        a, b = by_id.get(x.get("from")), by_id.get(x.get("to"))
        if a is None or b is None or not (is_media_item(a) and is_media_item(b)):
            continue
        if ((x.get("resolve") or {}).get("build")) == "clip_pair":
            continue        # a whip pair or zoom-through keyed in the two items' comps: a plain cut, no overlap
        d = int(x.get("frames", 0))
        c = int(a["rec_out"])
        al = x.get("alignment", "center")
        w0 = c - d // 2 if al == "center" else (c - d if al == "left" else c)
        w1 = w0 + d
        if d <= 0 or w0 < 0 or w1 > N or int(b["rec_in"]) != c:
            notes.append("transition %s not previewed (outside the programme)" % x.get("id"))
            continue
        ok_ids = {pos[id(a)], pos[id(b)]}
        if not set(np.unique(top[w0:w1]).tolist()) <= ok_ids:
            notes.append("transition %s not previewed (covered by an overlay)" % x.get("id"))
            continue
        k = len(xfs)
        xfs.append(x)
        top[w0:w1] = -2 - k
    pieces = []
    i = 0
    while i < N:
        j = i + 1
        while j < N and top[j] == top[i]:
            j += 1
        v = int(top[i])
        if v >= 0:
            pieces.append(("seg", i, j, vis[v][1]))
        elif v == -1:
            pieces.append(("gap", i, j, None))
        else:
            x = xfs[-2 - v]
            pieces.append(("xfade", i, j, (x, by_id[x["from"]], by_id[x["to"]])))
        i = j
    return pieces, notes


def overlay_changes(edl, lab, N):
    """Frames (inside the programme) where a see-through item comes in or goes out: a visual change the shot model
    does not count as a cut (a logo bug appearing over a shot)."""
    out = set()
    for tn, it in visible_video(edl):
        if shot_see_through(edl, lab, it):
            for x in (int(it["rec_in"]), int(it["rec_out"])):
                if 0 < x < N:
                    out.add(x)
    return sorted(out)


def _media_key(lab, m):
    if m.get("hash"):
        return m["hash"]
    p = lab.rel(m.get("proxy") or m.get("path") or "")
    try:
        st = os.stat(p)
        return "%s|%d|%d" % (p, st.st_size, st.st_mtime_ns)
    except OSError:
        return p


def piece_spec(edl, lab, it, k0, n):
    """Everything the renderer needs for n frames of item `it` starting at item-relative frame k0 (may be < 0 or run
    past the item: transitions use the handles)."""
    tf = it.get("transform") or {}
    base = {"n": int(n), "fade_in": 0, "fade_out": 0,
            "zoom": float(tf.get("zoom", 1.0)), "pan": float(tf.get("pan_px", 0)), "tilt": float(tf.get("tilt_px", 0))}
    op = float(tf.get("opacity", 100) if tf.get("opacity") is not None else 100)
    if op < 99.95:
        base["opacity"] = round(max(0.0, op), 2)
    L = rec_len(it)
    # a fade is drawn where the piece overlaps it, also when the piece starts or ends inside it (an item split under
    # a see-through overlay); a piece that runs past the item (transition handles) keeps no fade on that side
    fi, fo = int(it.get("fade_in", 0) or 0), int(it.get("fade_out", 0) or 0)
    if fi > 0 and 0 <= k0 < fi:
        base["fade_in"] = fi
        if k0:
            base["fade_in_at"] = -int(k0)
    if fo > 0 and k0 + n <= L and k0 + n > L - fo:
        base["fade_out"] = fo
        base["fade_out_at"] = int(L - fo - k0)
    if it.get("kind") == "solid":
        base.update({"kind": "solid", "color": str(it.get("color", "black"))})
        return base
    piece_fx(base, it, k0, n)
    m = edl["media"][it["media"]]
    if base.get("motion") or base.get("accents"):
        # the picture's own size: a clip comp's move and accents stay inside the picture's rectangle (bars, a
        # zoom under 1 or a pan gap stay black, fx_lab.picture_rect)
        sz_ = _item_size(edl, lab, it)
        if sz_:
            base["pic"] = [round(sz_[0], 3), round(sz_[1], 3)]
    if m.get("kind") == "image":
        f_ = lab.rel(m.get("image") or m.get("path"))
        base.update({"kind": "image", "file": f_, "mkey": _media_key(lab, m)})
        if image_has_alpha(f_):
            base["alpha"] = True
        return base
    r = ratio(edl, it)
    x = k0 * r + 1e-9
    fl = math.floor(x)
    s0 = int(it["src_in"]) + int(fl)
    q = x - fl
    nsrc = int(math.floor(q + (n - 1) * r)) + 1
    src = m.get("proxy")
    src_fps = media_fps(edl, it["media"])
    base.update({"kind": "clip", "file": lab.rel(src) if src else lab.rel(m.get("path")), "mkey": _media_key(lab, m),
                 "proxy": bool(src), "fps": fps_str(src_fps), "s0": s0, "q": round(q, 9), "r": round(r, 12), "nsrc": nsrc})
    rt = (it.get("retime") or {}).get("keys")
    if rt:
        # the retime's source frame on every frame of the piece (linear between its keys, whole frames, D1): the
        # piece reads from the lowest one and ramp_setpts places each source frame (fx_lab.ramp_setpts)
        pk = fx_lab.piece_keys(rt, k0, n)
        vals = [fx_lab.value_at(pk, f_) for f_ in range(n)]
        lo_, hi_ = int(math.floor(min(vals) + 1e-6)), int(math.floor(max(vals) + 1e-6))
        base.update({"s0": lo_, "q": 0.0, "r": 1.0, "nsrc": hi_ - lo_ + 1,
                     "retime": [[int(f_), round(float(v) - lo_, 6)] for f_, v in pk]})
    return base


def piece_fx(base, it, k0, n):
    """The item's keyed move and accents for the n frames from item frame k0, as piece frames 0..n-1."""
    mo = it.get("motion") or {}
    ks = {}
    for name, keys in (mo.get("keys") or {}).items():
        pk = fx_lab.piece_keys(keys, k0, n)
        if fx_lab.keys_vary(pk, 1.0 if name == "zoom" else 0.0):
            if mo.get("motion_blur"):
                # the shutter samples half a frame past the piece's ends: keep the curve one frame further
                pk = fx_lab.shift_keys(fx_lab.slice_keys(keys, k0 - 1, k0 + n), -k0)
            ks[name] = pk
    if ks:
        base["motion"] = {"point": mo.get("point") or [0.5, 0.5], "edges": mo.get("edges"),
                          "motion_blur": mo.get("motion_blur"), "keys": ks}
    acc = it.get("accents") or {}
    out = {}
    for name in ("flash", "rgb_px"):
        pk = fx_lab.piece_keys(acc.get(name) or [], k0, n)
        if fx_lab.keys_vary(pk, 0.0):
            out[name] = pk
    lk = acc.get("leak")
    if lk and int(lk["f"][1]) >= k0 and int(lk["f"][0]) < k0 + n:
        out["leak"] = dict(lk, f=[int(lk["f"][0]) - k0, int(lk["f"][1]) - k0])
    if out:
        base["accents"] = out


def _q(s):
    return s.replace("\\", "/").replace("'", "'\\''")


def spec_inputs(spec, tfps, raster):
    """ffmpeg input arguments for one spec."""
    fr = "%d/%d" % (tfps.numerator, tfps.denominator)
    if spec["kind"] == "solid" or spec["kind"] == "gap":
        c = spec.get("color", "black")
        return ["-f", "lavfi", "-i", "color=c=%s:s=%dx%d:r=%s" % (c, raster[0], raster[1], fr)]
    if spec["kind"] == "image":
        return ["-loop", "1", "-framerate", fr, "-t", "%.6f" % (spec["n"] / float(tfps) + 1.0), "-i", spec["file"]]
    sf = parse_fps(spec["fps"])
    ss = max(0.0, (spec["s0"] - 0.5) / float(sf))
    return ["-ss", "%.6f" % ss, "-i", spec["file"]]


def spec_chain(spec, tfps, raster, W, H, sizing, tag="s"):
    pw, ph = raster
    n = spec["n"]
    fr = "%d/%d" % (tfps.numerator, tfps.denominator)
    tb = "%d/%d" % (tfps.denominator, tfps.numerator)
    f = []
    if spec["kind"] == "clip" and spec.get("retime"):
        # a ramp or freeze: TimeStretcher keys as source frames per frame (fx_lab.ramp_setpts, [PRV] s5)
        f += ["trim=start_frame=0:end_frame=%d" % spec["nsrc"], "setpts=PTS-STARTPTS", "settb=%s" % tb,
              fx_lab.ramp_setpts(spec["retime"], fr)]
    elif spec["kind"] == "clip":
        f.append("trim=start_frame=0:end_frame=%d" % spec["nsrc"])
        r, q = spec["r"], spec["q"]
        if abs(r - 1.0) < 1e-12 and q < 1e-6:
            f += ["setpts=PTS-STARTPTS", "settb=%s" % tb, "setpts=N"]
        else:
            f += ["settb=%s" % tb, "setpts='max(0,ceil((N-%.9f)/%.12f-1e-7))'" % (q, r),
                  "select='lt(ceil((n-%.9f)/%.12f-1e-7),ceil((n+1-%.9f)/%.12f-1e-7))'" % (q, r, q, r)]
        f.append("fps=fps=%s:round=near" % fr)
    else:
        f += ["trim=end_frame=%d" % n, "setpts=PTS-STARTPTS", "settb=%s" % tb, "setpts=N"]
    f += ["tpad=stop_mode=clone:stop=%d" % n, "trim=end_frame=%d" % n, "setpts=PTS-STARTPTS"]
    # layer: drawn over the pictures under it (kept transparent outside the picture and where a still is
    # transparent); alpha: a still with transparency and nothing under it, drawn over black as Resolve shows it
    layer, alpha = bool(spec.get("layer")), bool(spec.get("alpha"))
    bg = "black@0" if layer else "black"
    if layer or alpha:
        # scaled premultiplied: a straight-alpha scale lets the colour under alpha 0 bleed into the edge (a white
        # matte drew a light line along a logo's transparent border that Resolve does not draw, measured in 21.1)
        f += ["format=rgba", "premultiply=inplace=1"]
    if spec["kind"] != "gap" and spec["kind"] != "solid" and spec.get("motion"):
        # a keyed move (punch, bump, push, shake, whip or zoom-through keys): the static framing and the keyed move
        # in one sub-pixel perspective transform per frame (fx_lab.motion_chain; never zoompan, [PRV] s4)
        f.append(fx_lab.motion_chain(spec, raster, W, H, sizing, tfps, tag=tag, bg=bg))
    elif spec["kind"] != "gap" and spec["kind"] != "solid":
        z, pan, tilt = spec.get("zoom", 1.0), spec.get("pan", 0.0), spec.get("tilt", 0.0)
        if abs(z - 1.0) <= 1e-6 and not pan and not tilt:
            if sizing in ("scaleToCrop", "crop", "fill"):
                f.append("scale=%d:%d:force_original_aspect_ratio=increase:flags=bilinear,crop=%d:%d" % (pw, ph, pw, ph))
            elif sizing in ("stretch", "scaleToStretch"):
                f.append("scale=%d:%d:flags=bilinear" % (pw, ph))
            else:
                f.append("scale=%d:%d:force_original_aspect_ratio=decrease:flags=bilinear,pad=%d:%d:(ow-iw)/2:(oh-ih)/2"
                         ":%s" % (pw, ph, pw, ph, bg))
        else:
            # Resolve order: input sizing scales the WHOLE image (for crop sizing it overhangs the frame), zoom
            # scales it about the centre, pan and tilt move it; only then is it cut to the frame. So a pan on a
            # cropped 16:9 source reveals picture that was outside the frame, not black.
            if sizing in ("scaleToCrop", "crop", "fill"):
                f.append("scale=%d:%d:force_original_aspect_ratio=increase:flags=bilinear" % (pw, ph))
            elif sizing in ("stretch", "scaleToStretch"):
                f.append("scale=%d:%d:flags=bilinear" % (pw, ph))
            else:
                f.append("scale=%d:%d:force_original_aspect_ratio=decrease:flags=bilinear" % (pw, ph))
            if abs(z - 1.0) > 1e-6:
                f.append("scale=trunc(iw*%.6f/2)*2:trunc(ih*%.6f/2)*2:flags=bilinear" % (z, z))
            px, py = pan * pw / float(W), tilt * ph / float(H)
            m = even(abs(px) + abs(py) + pw + ph + 2)
            f.append("pad=iw+%d:ih+%d:%d:%d:%s" % (2 * m, 2 * m, m, m, bg))
            f.append("crop=%d:%d:trunc(((iw-%d)/2-(%.4f))/2)*2:trunc(((ih-%d)/2+(%.4f))/2)*2" % (pw, ph, pw, px, ph, py))
    f.append("setsar=1")
    if layer:
        f.append("unpremultiply=inplace=1")
    rgb_ = (spec.get("accents") or {}).get("rgb_px")
    if rgb_ and not layer:
        c_ = fx_lab.rgb_chain(rgb_, n, raster, W, tfps)
        if c_:
            f.append(c_)
    af = ":alpha=1" if layer else ""
    if spec.get("fade_in"):
        F, at = int(spec["fade_in"]), int(spec.get("fade_in_at", 0))
        if at == 0:
            f.append("fade=t=in:s=0:n=%d%s" % (F, af))
        else:
            # the piece starts inside the fade (at < 0): draw the frames before it too, then cut them off
            f += ["tpad=start=%d:start_mode=clone" % -at, "fade=t=in:s=0:n=%d%s" % (F, af),
                  "trim=start_frame=%d" % -at, "setpts=PTS-STARTPTS"]
    if spec.get("fade_out"):
        F = int(spec["fade_out"])
        at = int(spec.get("fade_out_at", n - F))
        pre, post = max(0, -at), max(0, at + F - n)
        if not pre and not post:
            f.append("fade=t=out:s=%d:n=%d%s" % (at, F, af))
        else:
            f += ["tpad=start=%d:stop=%d:start_mode=clone:stop_mode=clone" % (pre, post),
                  "fade=t=out:s=%d:n=%d%s" % (at + pre, F, af),
                  "trim=start_frame=%d:end_frame=%d" % (pre, pre + n), "setpts=PTS-STARTPTS"]
    if layer:
        if spec.get("opacity") is not None:
            f.append("colorchannelmixer=aa=%.4f" % (float(spec["opacity"]) / 100.0))
        f.append("format=rgba")
        return ",".join(f)
    f.append("format=yuv420p")
    return ",".join(f)


_ALPHA_CACHE = {}


def image_has_alpha(path):
    """True when a still has transparent pixels (an alpha channel under 250 somewhere)."""
    try:
        st = os.stat(path)
    except OSError:
        return False
    key = (path, st.st_size, st.st_mtime_ns)
    if key not in _ALPHA_CACHE:
        res = False
        try:
            from PIL import Image
            with Image.open(path) as im:
                if im.mode in ("RGBA", "LA", "PA", "RGBa", "La") or "transparency" in im.info:
                    a = np.asarray(im.convert("RGBA").getchannel("A"))
                    res = bool(a.size and int(a.min()) < 250)
        except Exception:
            res = False
        _ALPHA_CACHE[key] = res
    return _ALPHA_CACHE[key]


def _item_size(edl, lab, it):
    """(width, height) of an item's picture as Resolve lays it out (rotation applied), or None."""
    m = edl["media"].get(it.get("media")) or {}
    w, h = m.get("width"), m.get("height")
    if (not w or not h) and m.get("kind") == "image":
        try:
            from PIL import Image
            with Image.open(lab.rel(m.get("image") or m.get("path")) if lab else (m.get("image") or m.get("path"))) as im:
                w, h = im.size
        except Exception:
            return None
    if not w or not h:
        return None
    if int(m.get("rotation") or 0) % 180 == 90:
        w, h = h, w
    return float(w), float(h)


def see_through(edl, lab, it):
    """True when the pictures under an item show through it in Resolve: a still with transparency, an opacity under
    100, a rotation, or a size, zoom and position that leave part of the frame uncovered."""
    if it.get("kind") == "solid":
        return False
    tf = it.get("transform") or {}
    m = edl["media"].get(it.get("media")) or {}
    if m.get("kind") == "image" and image_has_alpha(lab.rel(m.get("image") or m.get("path")) if lab
                                                    else (m.get("image") or m.get("path"))):
        return True
    if float(tf.get("opacity", 100) if tf.get("opacity") is not None else 100) < 99.95:
        return True
    if abs(float(tf.get("rotation", 0) or 0)) > 1e-6:
        return True
    z = float(tf.get("zoom", 1.0) or 1.0)
    px, py = abs(float(tf.get("pan_px", 0) or 0)), abs(float(tf.get("tilt_px", 0) or 0))
    W, H = float(edl["timeline"]["width"]), float(edl["timeline"]["height"])
    sz = _item_size(edl, lab, it)
    if sz is None:
        return abs(z - 1.0) > 1e-6 and z < 1.0
    w, h = sz
    sizing = (edl["timeline"].get("resolve") or {}).get("input_sizing", "scaleToFit")
    if sizing in ("scaleToCrop", "crop", "fill"):
        k = max(W / w, H / h)
        dw, dh = w * k, h * k
    elif sizing in ("stretch", "scaleToStretch"):
        dw, dh = W, H
    else:
        k = min(W / w, H / h)
        dw, dh = w * k, h * k
    return not (dw * z + 1.0 >= W + 2 * px and dh * z + 1.0 >= H + 2 * py)


SHOT_COVER_MIN = 0.70      # a whole picture fit to the frame is the shot when its bars leave this much covered


def shot_see_through(edl, lab, it):
    """see_through for the shot model. A clip that shows its whole picture fit to the frame (zoom 1 or more, not
    moved, fully opaque, no rotation, not a still with transparency) is a cutaway, not an overlay, when the bars that
    fit leaves (scale to fit a picture of another shape: DCI 4K, a 17:9 drone, 4:3 action camera footage on a 16:9
    timeline) cover less than 30 % of the frame: it is the shot, and the cuts under it are hidden. A logo, a picture
    in picture, a zoomed-out or moved item and a picture that leaves more of the frame uncovered stay see-through."""
    if not see_through(edl, lab, it):
        return False
    tf = it.get("transform") or {}
    m = edl["media"].get(it.get("media")) or {}
    if m.get("kind") == "image" and image_has_alpha(lab.rel(m.get("image") or m.get("path")) if lab
                                                    else (m.get("image") or m.get("path"))):
        return True
    if float(tf.get("opacity", 100) if tf.get("opacity") is not None else 100) < 99.95:
        return True
    if abs(float(tf.get("rotation", 0) or 0)) > 1e-6:
        return True
    z = float(tf.get("zoom", 1.0) or 1.0)
    W, H = float(edl["timeline"]["width"]), float(edl["timeline"]["height"])
    if z < 1.0 - 1e-6 or abs(float(tf.get("pan_px", 0) or 0)) > 0.01 * W or \
            abs(float(tf.get("tilt_px", 0) or 0)) > 0.01 * H:
        return True
    sz = _item_size(edl, lab, it)
    if sz is None:
        return True
    w, h = sz
    k = min(W / w, H / h)
    return min(W, w * k * z) * min(H, h * k * z) < SHOT_COVER_MIN * W * H - 1.0


def build_pieces(edl, lab, pieces):
    """Pieces -> render units: {"key", "n", "specs": [spec] or [specA, specB], "xfade": type or None}. A top item the
    pictures under it show through (see_through) becomes composite units: the pictures under it (down to the first
    one that covers the frame, else black) with it drawn over them ("comp": true, specs bottom first)."""
    units = []
    vis = visible_video(edl)
    st_cache = {}

    def st(it):
        if it["id"] not in st_cache:
            st_cache[it["id"]] = see_through(edl, lab, it)
        return st_cache[it["id"]]

    def under(obj, f):
        tn0 = [tn for tn, it in vis if it is obj]
        tn0 = tn0[0] if tn0 else 1
        below = sorted([(tn, it) for tn, it in vis if tn < tn0 and int(it["rec_in"]) <= f < int(it["rec_out"])],
                       key=lambda q: -q[0])
        out = []
        for tn, it in below:
            out.append(it)
            if not st(it):
                break
        return tuple(reversed(out))

    def alpha_still(it):
        m = edl["media"].get(it.get("media")) or {}
        return m.get("kind") == "image" and image_has_alpha(lab.rel(m.get("image") or m.get("path")))
    for kind, r0, r1, obj in pieces:
        n = r1 - r0
        if kind == "seg" and st(obj):
            cuts = sorted({r0, r1} | {int(x) for tn, it in vis for x in (it["rec_in"], it["rec_out"])
                                      if r0 < int(x) < r1})
            runs = []
            for a, b in zip(cuts, cuts[1:]):
                u_ = under(obj, a)
                if runs and runs[-1][2] == tuple(id(x) for x in u_):
                    runs[-1][1] = b
                else:
                    runs.append([a, b, tuple(id(x) for x in u_), u_])
            for a, b, _k, u_ in runs:
                m_ = b - a
                if not u_ and not alpha_still(obj):
                    units.append({"n": m_, "specs": [piece_spec(edl, lab, obj, a - int(obj["rec_in"]), m_)],
                                  "xfade": None})
                    continue
                if u_ and not st(u_[0]):
                    specs = [piece_spec(edl, lab, u_[0], a - int(u_[0]["rec_in"]), m_)]
                    layers = list(u_[1:]) + [obj]
                else:
                    specs = [{"kind": "gap", "n": m_, "color": "black"}]
                    layers = list(u_) + [obj]
                for it in layers:
                    sp_ = piece_spec(edl, lab, it, a - int(it["rec_in"]), m_)
                    sp_.pop("alpha", None)
                    sp_["layer"] = True
                    specs.append(sp_)
                units.append({"n": m_, "specs": specs, "xfade": None, "comp": True})
            continue
        if kind == "seg":
            units.append({"n": n, "specs": [piece_spec(edl, lab, obj, r0 - int(obj["rec_in"]), n)], "xfade": None})
        elif kind == "gap":
            units.append({"n": n, "specs": [{"kind": "gap", "n": n, "color": "black"}], "xfade": None})
        else:
            x, a, b = obj
            typ = x.get("type", "cross_dissolve")
            xf = typ
            if typ not in fx_lab.LEGACY_TRANSITIONS:
                # a catalogue transition: its preview recipe ([PRV] s3.3, [TR] s5 and s6) with its parameters
                row = fx_lab.tr_row(typ) or {}
                rec = (x.get("preview") or {}).get("recipe") or (row.get("preview") or {}).get("recipe") or "dissolve"
                xf = {"type": typ, "recipe": rec, "params": x.get("params") or {}}
            units.append({"n": n, "xfade": xf,
                          "specs": [piece_spec(edl, lab, a, r0 - int(a["rec_in"]), n),
                                    piece_spec(edl, lab, b, r0 - int(b["rec_in"]), n)]})
    return units


def spec_graph(spec, k, tfps, raster, W, H, sizing):
    """The graph from input k to [ik] for one spec: its chain, then the accents that need their own sources (the
    flash's white layer, the light leak), drawn on picture pieces (not on see-through layers)."""
    g = "[%d:v]%s" % (k, spec_chain(spec, tfps, raster, W, H, sizing, tag="s%d" % k))
    acc = spec.get("accents") or {}
    # a clip comp's move and accents act on the picture's own rectangle (Resolve sizes the comp's output after
    # it): cut the piece back to that rectangle over black, so letterbox bars stay black (fx_lab.picture_clip)
    clip_ = fx_lab.picture_clip(spec, raster, W, H, sizing, layer=bool(spec.get("layer")))
    clip_ = ("," + clip_) if clip_ else ""
    steps = []
    if not spec.get("layer"):
        if acc.get("flash"):
            steps.append(("flash", acc["flash"]))
        if acc.get("leak"):
            steps.append(("leak", acc["leak"]))
    if not steps:
        return g + clip_ + "[i%d]" % k
    cur = "p%d_0" % k
    g += "[%s]" % cur
    for t, (kind, val) in enumerate(steps):
        out = "p%d_%d" % (k, t + 1)
        if kind == "flash":
            g += ";" + fx_lab.flash_graph(cur, out, val, int(spec["n"]), raster, tfps)
        else:
            g += ";" + fx_lab.leak_graph(cur, out, val, int(spec["n"]), 0, raster, tfps)
        cur = out
    # the generated layers run a frame longer than the piece: cut it back to its length
    return g + ";[%s]trim=end_frame=%d,setpts=PTS-STARTPTS%s[i%d]" % (cur, int(spec["n"]), clip_, k)


def unit_key(u, extra):
    specs = [{k: v for k, v in s.items() if k != "file"} for s in u["specs"]]
    return sha1_of({"n": u["n"], "xfade": u["xfade"], "comp": bool(u.get("comp")), "specs": specs, "extra": extra,
                    "v": RENDERER_VERSION})


def render_group(group, tfps, raster, W, H, sizing, out_tmp):
    cmd = [ffmpeg_bin(), "-nostdin", "-hide_banner", "-loglevel", "error", "-y"]
    parts, labels, k = [], [], 0
    for j, u in enumerate(group):
        ins = []
        for s in u["specs"]:
            cmd += spec_inputs(s, tfps, raster)
            parts.append(spec_graph(s, k, tfps, raster, W, H, sizing))
            ins.append(k)
            k += 1
        xf = u["xfade"]
        rec_ = xf.get("recipe") if isinstance(xf, dict) else None
        if xf and rec_ not in (None, "dissolve", "dip"):
            # a catalogue transition drawn by its recipe (fx_lab.transition_graph), then the lab's range and length
            d = u["n"]
            parts.append(fx_lab.transition_graph(rec_, xf.get("params"), "i%d" % ins[0], "i%d" % ins[1], "x%d" % j,
                                                 d, float(tfps), raster))
            parts.append("[x%d]%s,trim=end_frame=%d,setpts=PTS-STARTPTS[u%d]" % (j, OUT_RANGE, d, j))
        elif xf:
            d = u["n"]
            # the frame index inside the dissolve from its timestamp: blend's own N starts at 1 in some ffmpeg
            # versions, which put the whole dissolve one frame ahead of Resolve's (measured: Resolve mixes
            # (k + 0.5) / d of the incoming clip on the k-th dissolve frame, k from 0)
            K = "floor(T*%.9f+0.5)" % float(tfps)
            if xf == "dip_to_black" or rec_ == "dip":
                h = max(1, d // 2)
                expr = "if(lt(%s,%d),A*(1-(%s+0.5)/%d),B*((%s+0.5-%d)/%d))" % (K, h, K, h, K, h, max(1, d - h))
            else:
                expr = "A*(1-(%s+0.5)/%d)+B*((%s+0.5)/%d)" % (K, d, K, d)
            parts.append("[i%d]format=gbrp[ga%d];[i%d]format=gbrp[gb%d];[ga%d][gb%d]blend=all_expr='%s',%s,"
                         "trim=end_frame=%d,setpts=PTS-STARTPTS[u%d]" % (ins[0], j, ins[1], j, j, j, expr, OUT_RANGE,
                                                                          d, j))
        elif u.get("comp"):
            # the pictures under a see-through item, then each layer over them (straight alpha, as Resolve draws a
            # still's transparency and the uncovered frame around a zoomed-out picture)
            cur = "[i%d]" % ins[0]
            for t, idx in enumerate(ins[1:]):
                nxt = "[c%d_%d]" % (j, t)
                parts.append("%s[i%d]overlay=0:0:format=auto:eof_action=repeat%s" % (cur, idx, nxt))
                cur = nxt
            parts.append("%s%s,trim=end_frame=%d,setpts=PTS-STARTPTS[u%d]" % (cur, OUT_RANGE, u["n"], j))
        else:
            parts.append("[i%d]%s[u%d]" % (ins[0], OUT_RANGE, j))
        labels.append("[u%d]" % j)
    parts.append("%sconcat=n=%d:v=1:a=0[vout]" % ("".join(labels), len(group)))
    fr = "%d/%d" % (tfps.numerator, tfps.denominator)
    graph = ";".join(parts)
    gfile = None
    if len(graph) > 60000:
        # keyed moves write one expression per corner: a long graph goes through a file, not the command line
        gfile = out_tmp + ".graph.txt"
        with open(gfile, "w", encoding="utf-8") as fh:
            fh.write(graph)
        cmd += [_graph_file_option(), gfile]
    else:
        cmd += ["-filter_complex", graph]
    cmd += ["-map", "[vout]", "-an", "-r", fr, "-c:v", "libx264", "-preset",
            "ultrafast", "-crf", "26", "-x264-params", "keyint=1:scenecut=0", "-pix_fmt", "yuv420p",
            "-color_range", "pc", "-f", "mp4", out_tmp]
    try:
        run(cmd, "ffmpeg (preview chunk)")
    finally:
        if gfile and os.path.exists(gfile):
            os.remove(gfile)


_GRAPH_OPT = []


def _graph_file_option():
    """ffmpeg 7 and newer read a filter graph from a file with -/filter_complex; older ones with
    -filter_complex_script."""
    if not _GRAPH_OPT:
        opt = "-/filter_complex"
        try:
            out = subprocess.run([ffmpeg_bin(), "-hide_banner", "-version"], capture_output=True, text=True).stdout
            m = re.search(r"version\s+n?(\d+)", out)
            if m and int(m.group(1)) < 7:
                opt = "-filter_complex_script"
        except (OSError, ValueError):
            pass
        _GRAPH_OPT.append(opt)
    return _GRAPH_OPT[0]


def render_video(edl, lab, units, raster, cache_dir, out_path, problems):
    tfps = tl_fps(edl)
    W, H = int(edl["timeline"]["width"]), int(edl["timeline"]["height"])
    sizing = (edl["timeline"].get("resolve") or {}).get("input_sizing", "scaleToFit")
    extra = {"raster": list(raster), "fps": fps_str(tfps), "W": W, "H": H, "sizing": sizing}
    os.makedirs(cache_dir, exist_ok=True)
    groups, cur, ninp = [], [], 0
    for u in units:
        c = len(u["specs"])
        if cur and ninp + c > GROUP_INPUTS:
            groups.append(cur)
            cur, ninp = [], 0
        cur.append(u)
        ninp += c
    if cur:
        groups.append(cur)
    info = []

    def one(g):
        key = sha1_of([unit_key(u, extra) for u in g])
        path = os.path.join(cache_dir, "g%s.mp4" % key)
        want = sum(u["n"] for u in g)
        if os.path.exists(path):
            got = count_frames(path)
            if got != want:
                try:
                    os.remove(path)
                except OSError:
                    pass
                return {"path": path, "expected": want, "frames": got, "cached": True,
                        "problem": "cached chunk %s had %d frames instead of %d; it was deleted, run preview again"
                                   % (os.path.basename(path), got, want)}
            return {"path": path, "expected": want, "frames": got, "cached": True}
        tmp = "%s.%d.%d.tmp.mp4" % (path[:-4], os.getpid(), id(g))
        render_group(g, tfps, raster, W, H, sizing, tmp)
        got = count_frames(tmp)
        if got != want:
            try:
                os.remove(tmp)
            except OSError:
                pass
            return {"path": path, "expected": want, "frames": got, "cached": False,
                    "problem": "chunk rendered %d frames instead of %d" % (got, want)}
        os.replace(tmp, path)
        return {"path": path, "expected": want, "frames": got, "cached": False}

    with ThreadPoolExecutor(min(workers(), max(1, len(groups)))) as ex:
        info = list(ex.map(one, groups))
    for c in info:
        if c.get("problem"):
            problems.append(c["problem"])
    if problems:
        return info
    lst = out_path + ".ffconcat"
    with open(lst, "w", encoding="utf-8") as fh:
        fh.write("ffconcat version 1.0\n")
        for c in info:
            fh.write("file '%s'\n" % _q(c["path"]))
    run([ffmpeg_bin(), "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst,
         "-c", "copy", "-f", "mp4", out_path], "ffmpeg (join chunks)")
    os.remove(lst)
    return info


# ------------------------------------------------------------------------------------------- titles and captions
BOLD_FONTS = ("/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/Library/Fonts/Arial Bold.ttf",
              "C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/segoeuib.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
              "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf")


def _font(size, bold=False):
    """(font, is_bold): a bold sans from the system for captions and titles (KNOWLEDGE section 10), else Pillow's
    own font (drawn thicker by the caller)."""
    from PIL import ImageFont
    if bold:
        for p in BOLD_FONTS:
            if os.path.exists(p):
                try:
                    return ImageFont.truetype(p, max(6, int(size))), True
                except Exception:
                    continue
    try:
        return ImageFont.load_default(size=max(6, int(size))), False
    except Exception:
        return ImageFont.load_default(), False


def overlay_items(edl):
    out = []
    for tr in edl["tracks"]["video"]:
        for it in tr.get("items", []):
            if it.get("kind") == "title" and it.get("enabled", True) is not False:
                out.append(("title", it))
    for tr in edl["tracks"].get("subtitle", []):
        for it in tr.get("items", []):
            out.append(("caption", it))
    return sorted(out, key=lambda x: (int(x[1]["rec_in"]), x[0]))


def _hex_rgb(h, default):
    try:
        return tuple(int(str(h)[i:i + 2], 16) for i in (1, 3, 5))
    except (TypeError, ValueError):
        return default


def _look_font(look, size):
    ff = look.get("font_file")
    if ff and os.path.exists(os.path.expanduser(str(ff))):
        from PIL import ImageFont
        try:
            return ImageFont.truetype(os.path.expanduser(str(ff)), max(6, int(size))), True
        except Exception:
            pass
    return _font(size, bold=True)


def draw_look_png(it, raster, W, H, path):
    """A title or caption drawn with its resolved look, the way the build draws it: text at font_px, lines at
    line_spacing em pitch centred on the box centre, stroke and shadow as given, the box (when the look has one) as
    the real text extent plus pad_em, rounded by round."""
    from PIL import Image, ImageDraw
    look = it.get("look") or {}
    pw, ph = raster
    sx, sy = pw / float(W), ph / float(H)
    box = it.get("box") or [W * 0.1, H * 0.8, W * 0.8, H * 0.1]
    cx, cy = (box[0] + box[2] / 2.0) * sx, (box[1] + box[3] / 2.0) * sy
    lines = it.get("lines") or _wrap(it.get("text", ""), 32)
    fpx = float(it.get("font_px") or 64) * sy
    font, real_bold = _look_font(look, fpx)
    meas = ImageDraw.Draw(Image.new("RGBA", (4, 4)))
    widths = [meas.textlength(ln, font=font) for ln in lines]
    pitch = float(look.get("line_spacing") or 1.25) * fpx
    tw, th = (max(widths) if widths else 0.0), pitch * (len(lines) - 1) + fpx
    layers = []
    bx = look.get("box")
    if isinstance(bx, dict):
        pad = bx.get("pad_em") or [0.4, 0.2]
        x0, y0 = cx - tw / 2 - float(pad[0]) * fpx, cy - th / 2 - float(pad[-1]) * fpx
        x1, y1 = cx + tw / 2 + float(pad[0]) * fpx, cy + th / 2 + float(pad[-1]) * fpx
        lb = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
        fill = _hex_rgb(bx.get("color"), (0, 0, 0)) + (int(round(255 * float(bx.get("opacity", 0.6)))),)
        rad = float(bx.get("round") or 0.0) * (y1 - y0) / 2.0
        dl = ImageDraw.Draw(lb)
        if rad >= 1.0:
            dl.rounded_rectangle([x0, y0, x1, y1], radius=rad, fill=fill)
        else:
            dl.rectangle([x0, y0, x1, y1], fill=fill)
        layers.append(lb)
    al = look.get("align") or "center"

    def xs(k):
        return {"left": cx - tw / 2, "right": cx + tw / 2 - widths[k]}.get(al, cx - widths[k] / 2)
    top = cy - th / 2
    sh = look.get("shadow")
    if isinstance(sh, dict):
        ls = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
        ds = ImageDraw.Draw(ls)
        off = max(1, int(round(0.06 * fpx)))
        fill = _hex_rgb(sh.get("color"), (0, 0, 0)) + (int(round(255 * float(sh.get("opacity", 0.5)))),)
        for k, ln in enumerate(lines):
            ds.text((xs(k) + off, top + k * pitch + off), ln, font=font, fill=fill)
        layers.append(ls)
    lt = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
    dt = ImageDraw.Draw(lt)
    col = _hex_rgb(look.get("color"), (255, 255, 255)) + (255,)
    st = look.get("stroke")
    for k, ln in enumerate(lines):
        if isinstance(st, dict):
            dt.text((xs(k), top + k * pitch), ln, font=font, fill=col,
                    stroke_width=max(1, int(round(float(st.get("em", 0.08)) * fpx))),
                    stroke_fill=_hex_rgb(st.get("color"), (0, 0, 0)) + (255,))
        else:
            dt.text((xs(k), top + k * pitch), ln, font=font, fill=col,
                    stroke_width=0 if real_bold else max(1, int(fpx / 28)), stroke_fill=col)
    layers.append(lt)
    im = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
    for ly in layers:
        im = Image.alpha_composite(im, ly)
    im.save(path)
    return path


def draw_overlay_png(kind, it, raster, W, H, path):
    from PIL import Image, ImageDraw
    if isinstance(it.get("look"), dict):
        return draw_look_png(it, raster, W, H, path)
    pw, ph = raster
    sx, sy = pw / float(W), ph / float(H)
    im = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    box = it.get("box") or [W * 0.1, H * 0.8, W * 0.8, H * 0.1]
    x, y, w, h = box[0] * sx, box[1] * sy, box[2] * sx, box[3] * sy
    lines = it.get("lines") or _wrap(it.get("text", ""), 32)
    fpx = float(it.get("font_px") or (h / max(1, len(lines)) / 1.3 / sy)) * sy
    # captions and titles as KNOWLEDGE prescribes them: a bold sans with a dark stroke (titles on a box too)
    font, real_bold = _font(fpx, bold=True)
    if kind == "title":
        d.rectangle([x, y, x + w, y + h], fill=(0, 0, 0, 150))
    lh = h / max(1, len(lines))
    for k, ln in enumerate(lines):
        tw = d.textlength(ln, font=font)
        tx = x + (w - tw) / 2.0
        ty = y + k * lh + (lh - fpx) / 2.0
        d.text((tx, ty), ln, font=font, fill=(0, 0, 0, 255), stroke_width=max(2, int(fpx / 9)),
               stroke_fill=(0, 0, 0, 255))
        # without a bold font on the system, a thin white stroke thickens the regular one
        d.text((tx, ty), ln, font=font, fill=(255, 255, 255, 255),
               stroke_width=0 if real_bold else max(1, int(fpx / 28)), stroke_fill=(255, 255, 255, 255))
    im.save(path)
    return path


def splice_overlays(edl, base, out, raster, work, N):
    """Draw titles and captions over their frame ranges only; everything else is stream-copied (all-intra)."""
    ovs = [(k, it) for k, it in overlay_items(edl) if int(it["rec_out"]) > 0 and int(it["rec_in"]) < N]
    if not ovs:
        return base
    tfps = tl_fps(edl)
    fr = "%d/%d" % (tfps.numerator, tfps.denominator)
    W, H = int(edl["timeline"]["width"]), int(edl["timeline"]["height"])
    patches, cur = [], None
    for k, it in ovs:
        a, b = max(0, int(it["rec_in"])), min(N, int(it["rec_out"]))
        if b <= a:
            continue
        # an animated title or caption is one image per distinct state of its animation (fx_lab.anim_states, the
        # same states the build keys), each over its own frames; a static one is one image
        runs = None
        if (it.get("anim") or {}).get("id") not in (None, "none"):
            r0_ = int(it["rec_in"])
            runs = [(max(a, r0_ + x0), min(b, r0_ + x1), st) for x0, x1, st in
                    fx_lab.state_runs(fx_lab.anim_states(it, float(tfps)))]
            runs = [r_ for r_ in runs if r_[1] > r_[0]]
        n_in = len(runs) if runs is not None else 1
        if cur is None or a > cur["b"] or (a == cur["b"] and (len(cur["ovs"]) >= 20 or cur["inputs"] + n_in > 40)):
            cur = {"a": a, "b": b, "ovs": [], "inputs": 0}
            patches.append(cur)
        cur["b"] = max(cur["b"], b)
        cur["ovs"].append((k, it, a, b, runs))
        cur["inputs"] += n_in
    lines, pos = ["ffconcat version 1.0"], 0
    made = []

    def piece(a, b, name):
        p = os.path.join(work, name)
        run([ffmpeg_bin(), "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", base, "-ss",
             "%.6f" % ((a - 0.5) / float(tfps)) if a > 0 else "0", "-frames:v", str(b - a), "-map", "0:v", "-c", "copy",
             "-f", "mp4", p], "ffmpeg (copy piece)")
        return p

    for pi, p in enumerate(patches):
        if p["a"] > pos:
            made.append(piece(pos, p["a"], "copy_%03d.mp4" % pi))
        cmd = [ffmpeg_bin(), "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-ss",
               "%.6f" % (max(0.0, (p["a"] - 0.5) / float(tfps))), "-i", base]
        n_p = p["b"] - p["a"]
        fc, last = ["[0:v]trim=start_frame=0:end_frame=%d,setpts=PTS-STARTPTS,settb=%d/%d,setpts=N[b0]"
                    % (n_p, tfps.denominator, tfps.numerator)], "b0"
        inp = 0
        for j, (k, it, a, b, runs) in enumerate(p["ovs"]):
            if runs is None:
                png = os.path.join(work, "ov_%03d_%02d.png" % (pi, j))
                draw_overlay_png(k, it, raster, W, H, png)
                runs_ = [(a, b, png)]
            else:
                runs_ = []
                for q, (x0, x1, st) in enumerate(runs):
                    png = os.path.join(work, "ov_%03d_%02d_%03d.png" % (pi, j, q))
                    fx_lab.draw_state(it, st, W, H, scale=raster[0] / float(W)).resize(tuple(raster)).save(png)
                    runs_.append((x0, x1, png))
            for q, (x0, x1, png) in enumerate(runs_):
                inp += 1
                cmd += ["-i", png]
                lab_ = "t%d_%d" % (j, q)
                fc.append("[%s][%d:v]overlay=0:0:enable='between(n,%d,%d)'[%s]" % (last, inp, x0 - p["a"],
                                                                                    x1 - p["a"] - 1, lab_))
                last = lab_
        pp = os.path.join(work, "patch_%03d.mp4" % pi)
        fc[-1] = fc[-1][:-len("[%s]" % last)] + ",%s[%s]" % (OUT_RANGE, last)
        run(cmd + ["-filter_complex", ";".join(fc), "-map", "[%s]" % last, "-frames:v", str(p["b"] - p["a"]), "-an",
                   "-r", fr, "-c:v", "libx264", "-preset", "ultrafast", "-crf", "26", "-x264-params",
                   "keyint=1:scenecut=0", "-pix_fmt", "yuv420p", "-color_range", "pc", "-f", "mp4", pp],
            "ffmpeg (title patch)")
        made.append(pp)
        pos = p["b"]
    if pos < N:
        made.append(piece(pos, N, "copy_end.mp4"))
    lst = os.path.join(work, "splice.ffconcat")
    with open(lst, "w", encoding="utf-8") as fh:
        fh.write("ffconcat version 1.0\n" + "".join("file '%s'\n" % _q(m) for m in made))
    run([ffmpeg_bin(), "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst,
         "-c", "copy", "-f", "mp4", out], "ffmpeg (join patches)")
    return out


# ---------------------------------------------------------------------------------------------- preview: sound
class WavSource:
    """16-bit PCM WAV read through a memory map (never the whole file in RAM); other formats load once."""

    def __init__(self, path):
        self.path = path
        self.data = None
        with open(path, "rb") as fh:
            head = fh.read(12)
            if head[:4] != b"RIFF" or head[8:12] != b"WAVE":
                raise Fail("%s is not a WAV file" % path)
            fmt = None
            while True:
                h = fh.read(8)
                if len(h) < 8:
                    break
                cid, size = h[:4], int.from_bytes(h[4:8], "little")
                if cid == b"fmt ":
                    b = fh.read(size)
                    fmt = (int.from_bytes(b[0:2], "little"), int.from_bytes(b[2:4], "little"),
                           int.from_bytes(b[4:8], "little"), int.from_bytes(b[14:16], "little"))
                    if size % 2:
                        fh.read(1)
                elif cid == b"data":
                    off = fh.tell()
                    fsize = os.path.getsize(path)
                    if size == 0 or size == 0xFFFFFFFF or off + size > fsize:
                        size = fsize - off
                    self._data_off, self._data_size = off, size
                    break
                else:
                    fh.seek(size + (size % 2), 1)
        if not fmt or not hasattr(self, "_data_off"):
            raise Fail("%s has no audio data" % path)
        tag, self.ch, self.sr, bits = fmt
        if tag in (1, 0xFFFE) and bits == 16:
            n = self._data_size // (2 * self.ch)
            self.data = np.memmap(path, dtype="<i2", mode="r", offset=self._data_off, shape=(n, self.ch))
            self.scale = 1 / 32768.0
        else:
            with wave.open(path) as w:
                raw = w.readframes(w.getnframes())
                sw = w.getsampwidth()
            if sw == 3:
                b = np.frombuffer(raw, np.uint8).reshape(-1, 3)
                v = (b[:, 0].astype(np.int32) | (b[:, 1].astype(np.int32) << 8) | (b[:, 2].astype(np.int32) << 16))
                v = np.where(v >= 2 ** 23, v - 2 ** 24, v)
                self.data = v.reshape(-1, self.ch)
                self.scale = 1 / float(2 ** 23)
            elif sw == 4:
                self.data = np.frombuffer(raw, "<i4").reshape(-1, self.ch)
                self.scale = 1 / float(2 ** 31)
            else:
                raise Fail("%s: unsupported WAV sample format" % path)
        self.n = len(self.data)

    def read(self, a, b):
        """Samples [a, b) as float32 stereo; zeros outside the file."""
        out = np.zeros((max(0, b - a), 2), np.float32)
        lo, hi = max(a, 0), min(b, self.n)
        if hi > lo:
            x = np.asarray(self.data[lo:hi], dtype=np.float32) * self.scale
            if self.ch == 1:
                x = np.repeat(x, 2, axis=1)
            out[lo - a:hi - a] = x[:, :2]
        return out


_WAV_MEMO = {}


def wav_source(path):
    st = os.stat(path)
    key = (path, st.st_mtime_ns, st.st_size)
    if key not in _WAV_MEMO:
        _WAV_MEMO[key] = WavSource(path)
    return _WAV_MEMO[key]


def audio_file_of(lab, m):
    p = m.get("wav")
    if p:
        return lab.rel(p)
    if str(m.get("path", "")).lower().endswith(".wav"):
        return lab.rel(m["path"])
    return None


def _fade(x, linear):
    return x if linear else np.sin(0.5 * np.pi * x)


def audio_xfades(edl):
    """{audio item id: {"out"|"in": (w0, w1, curve)}}: the audio cross fades the transitions ask for ("audio":
    plus3 or zero) on the sound linked to their two pictures (the same link, or the nat sound of the same clip meeting
    at the cut), over the frames Resolve places the transition on ([TR] s2, s7)."""
    out = {}
    vby = {it["id"]: it for k, tr, it in iter_items(edl, ("video",))}
    for x in edl.get("transitions") or []:
        curve = x.get("audio")
        if curve not in ("plus3", "zero"):
            continue
        a, b = vby.get(x.get("from")), vby.get(x.get("to"))
        if a is None or b is None:
            continue
        d = int(x.get("frames", 0) or 0)
        al = x.get("alignment", "center")
        c = int(a["rec_out"])
        w0 = c - (d + 1) // 2 if al == "center" else (c - d if al == "left" else c)
        w1 = w0 + d
        for tr in edl["tracks"].get("audio", []):
            if tr.get("role") not in ("dialogue", "nat"):
                continue
            for it in tr.get("items", []):
                ia, ib = int(it["rec_in"]), int(it["rec_out"])
                if ib == c and ((a.get("link") and it.get("link") == a.get("link")) or
                                (tr.get("role") == "nat" and it.get("media") == a.get("media"))):
                    out.setdefault(it["id"], {})["out"] = (w0, w1, curve)
                if ia == c and ((b.get("link") and it.get("link") == b.get("link")) or
                                (tr.get("role") == "nat" and it.get("media") == b.get("media"))):
                    out.setdefault(it["id"], {})["in"] = (w0, w1, curve)
    return out


def mix_audio(edl, lab, out_wav, only_track=None, sr=SR, want_metrics=True):
    """Numpy mix in 30 s blocks at 48 kHz: gains, pans, fades, volume envelopes, speed (varispeed). Writes 16-bit
    stereo PCM and returns metrics from per-role energy at 100 Hz (no full-length buses in memory)."""
    tfps = tl_fps(edl)
    N = programme_frames(edl)
    total = int(Fraction(N) * sr / tfps)
    win = sr // 100
    nw = (total + win - 1) // win
    roles = {}
    items, notes = [], []
    xf_ = audio_xfades(edl)
    # the EDL mix block: one uniform gain on every enabled audio item, over its own gain, envelope and fades
    mix_g = mix_gain_of(edl)
    for tr in edl["tracks"].get("audio", []):
        if only_track and tr.get("id") != only_track:
            continue
        role = tr.get("role") or "other"
        its = sorted(tr.get("items", []), key=lambda x: int(x["rec_in"]))
        for k, it in enumerate(its):
            if not is_media_item(it) or it.get("enabled", True) is False:
                continue
            m = edl["media"].get(it["media"]) or {}
            f = audio_file_of(lab, m)
            if not f or not os.path.exists(f):
                notes.append("no audio file for %s (%s): silent in the preview" % (it["id"], it["media"]))
                continue
            src = wav_source(f)
            mf = media_fps(edl, it["media"])
            prev = its[k - 1] if k else None
            nxt = its[k + 1] if k + 1 < len(its) else None

            def same(x, y):
                return (x is not None and y is not None and x.get("media") == y.get("media")
                        and int(x["rec_out"]) == int(y["rec_in"]) and int(x.get("src_out", -1)) == int(y.get("src_in", -2)))
            d_ = {"it": it, "role": role, "src": src,
                  "r0": int(Fraction(int(it["rec_in"])) * sr / tfps), "r1": int(Fraction(int(it["rec_out"])) * sr / tfps),
                  "t_src": int(it["src_in"]) / float(mf) - float((m.get("audio") or {}).get("offset_s") or 0.0),
                  "speed": speed_of(it), "lin_in": same(prev, it), "lin_out": same(it, nxt)}
            # an audio cross fade under a video transition (the transition's "audio": plus3 or zero, [TR] s7):
            # the outgoing sound runs on into the window, the incoming one starts at its beginning
            d_["e0"], d_["e1"] = d_["r0"], d_["r1"]
            for side, (w0, w1, curve) in (xf_.get(it["id"]) or {}).items():
                ws0, ws1 = int(Fraction(w0) * sr / tfps), int(Fraction(w1) * sr / tfps)
                d_["xf_" + side] = (ws0, ws1, curve)
                if side == "in":
                    d_["e0"] = max(0, min(d_["e0"], ws0))
                else:
                    d_["e1"] = min(total, max(d_["e1"], ws1))
            items.append(d_)
            roles.setdefault(role, np.zeros(nw, np.float64))
    mix_e = np.zeros(nw, np.float64)
    mix_pk = np.zeros(nw, np.float32)
    block = win * 3000
    peak = 0.0
    tmp = out_wav + ".tmp"
    with wave.open(tmp, "wb") as wv:
        wv.setnchannels(2)
        wv.setsampwidth(2)
        wv.setframerate(sr)
        for b0 in range(0, total, block):
            b1 = min(total, b0 + block)
            buf = np.zeros((b1 - b0, 2), np.float32)
            for d in items:
                x0, x1 = max(b0, d["e0"]), min(b1, d["e1"])
                if x1 <= x0:
                    continue
                it, src, sp = d["it"], d["src"], d["speed"]
                i = np.arange(x0 - d["r0"], x1 - d["r0"], dtype=np.float64)
                pos = (d["t_src"] + i / sr * sp) * src.sr
                p0 = pos[0]
                if abs(sp - 1.0) < 1e-12 and src.sr == sr and abs(p0 - round(p0)) < 1e-6:
                    a = int(round(p0))
                    seg = src.read(a, a + len(i))
                else:
                    lo = int(math.floor(pos.min()))
                    hi = int(math.ceil(pos.max())) + 2
                    raw = src.read(lo, hi)
                    xs = pos - lo
                    j = np.clip(np.floor(xs).astype(np.int64), 0, max(0, len(raw) - 2))
                    fr = (xs - j).astype(np.float32)[:, None]
                    seg = raw[j] * (1 - fr) + raw[np.minimum(j + 1, len(raw) - 1)] * fr
                g = np.full(len(i), float(it.get("gain_db", 0.0)) + mix_g, np.float64)
                env = it.get("volume_env")
                if env:
                    xs = np.array([p[0] for p in env], np.float64) * sr / float(tfps)
                    g += np.interp(i, xs, np.array([p[1] for p in env], np.float64))
                amp = np.power(10.0, g / 20.0)
                L = d["r1"] - d["r0"]
                fi = int(round(int(it.get("fade_in", 0)) * sr / float(tfps)))
                fo = int(round(int(it.get("fade_out", 0)) * sr / float(tfps)))
                if fi > 0 and "xf_in" not in d:
                    msk = i < fi
                    amp[msk] *= _fade((i[msk] + 0.5) / fi, d["lin_in"])
                if fo > 0 and "xf_out" not in d:
                    msk = i >= L - fo
                    amp[msk] *= _fade((L - i[msk] - 0.5) / fo, d["lin_out"])
                for side in ("xf_in", "xf_out"):
                    if side in d:
                        ws0, ws1, curve = d[side]
                        s_ = i + d["r0"]
                        p_ = np.clip((s_ + 0.5 - ws0) / float(max(1, ws1 - ws0)), 0.0, 1.0)
                        if curve == "plus3":
                            g_ = np.sqrt(p_) if side == "xf_in" else np.sqrt(1.0 - p_)
                        else:
                            g_ = p_ if side == "xf_in" else 1.0 - p_
                        amp *= g_
                pan = float(it.get("pan", 0) or 0) / 100.0
                gl, gr = min(1.0, 1.0 - pan), min(1.0, 1.0 + pan)
                seg = seg * amp[:, None].astype(np.float32)
                if pan:
                    seg[:, 0] *= gl
                    seg[:, 1] *= gr
                buf[x0 - b0:x1 - b0] += seg
                if want_metrics:
                    mono = seg.mean(axis=1).astype(np.float64)
                    wi = (np.arange(x0, x1) // win)
                    roles[d["role"]][wi[0]:wi[-1] + 1] += np.bincount(wi - wi[0], weights=mono * mono)
            if want_metrics:
                mono = buf.mean(axis=1).astype(np.float64)
                wi = np.arange(b0, b1) // win
                mix_e[wi[0]:wi[-1] + 1] += np.bincount(wi - wi[0], weights=mono * mono)
                starts = np.arange(0, b1 - b0, win)
                mix_pk[wi[0]:wi[0] + len(starts)] = np.maximum.reduceat(np.abs(buf).max(axis=1), starts)
            if len(buf):
                peak = max(peak, float(np.abs(buf).max()))
            wv.writeframes((np.clip(buf, -1, 1) * 32767).astype("<i2").tobytes())
    os.replace(tmp, out_wav)
    res = {"samples": total, "rate": sr, "peak_dbfs": round(20 * math.log10(peak), 2) if peak > 0 else None, "notes": notes}
    if not want_metrics:
        return res, None
    db = lambda e: 10 * np.log10(e / win + 1e-12)
    env = {"mix_peak": mix_pk, "mix_db": db(mix_e).astype(np.float32)}
    for k, v in roles.items():
        env["role_" + k] = db(v).astype(np.float32)
    dia = roles.get("dialogue")
    mus = roles.get("music")
    res["dialogue_s"] = round(float((db(dia) > -45).sum()) / 100.0, 2) if dia is not None else 0.0
    gap = None
    if dia is not None and mus is not None:
        m = (db(dia) > -45) & (mus > 0)
        if m.sum() >= 10:
            gap = float(10 * np.log10(dia[m].mean() / max(mus[m].mean(), 1e-20)))
    res["speech_music_gap_lu"] = None if gap is None else round(gap, 1)
    sil = []
    d_items = [d["it"] for d in items if d["role"] == "dialogue"]
    if d_items:
        lo = min(int(x["rec_in"]) for x in d_items) / float(tfps)
        hi = max(int(x["rec_out"]) for x in d_items) / float(tfps)
        quiet = env["mix_db"] < -60
        k = 0
        while k < nw:
            if quiet[k]:
                j = k
                while j < nw and quiet[j]:
                    j += 1
                t0, t1 = k / 100.0, j / 100.0
                if t1 - t0 > 0.3 and t0 >= lo - 1e-9 and t1 <= hi + 1e-9:
                    sil.append([round(t0, 2), round(t1, 2)])
                k = j
            else:
                k += 1
    res["silence_gaps"] = sil
    return res, env


# ---------------------------------------------------------------------------------------------- preview command
def preview_state(rdir):
    return load_json_or(os.path.join(rdir, "preview.json"), None)


def preview_fresh(edl, rdir):
    st = preview_state(rdir)
    return bool(st and st.get("edl_hash") == edl_hash(edl) and st.get("result") == "OK"
                and st.get("renderer") == RENDERER_VERSION and os.path.exists(os.path.join(rdir, "preview.mov")))


def render_preview(lab, edl_path, out=None, quiet=False):
    t_start = time.time()
    edl = load_edl(edl_path)
    rdir = review_dir(edl_path, out)
    os.makedirs(rdir, exist_ok=True)
    N = programme_frames(edl)
    problems, notes = [], []
    if N <= 0:
        raise Fail("the EDL has no items")
    for mid, m in edl["media"].items():
        if m.get("kind") in ("av", "video") and not m.get("proxy"):
            notes.append("media %s has no proxy: the preview decodes the original (slow)" % mid)
        elif m.get("proxy") and not os.path.exists(lab.rel(m["proxy"])):
            problems.append("proxy of %s is missing (%s): run media_lab.py proxies" % (mid, m["proxy"]))
    W, H = int(edl["timeline"]["width"]), int(edl["timeline"]["height"])
    raster = preview_size(W, H)
    pieces, fnotes = flatten(edl, N)
    notes += fnotes
    info, audio = [], None
    final = os.path.join(rdir, "preview.mov")
    work = tempfile.mkdtemp(prefix="work_", dir=rdir)
    try:
        if not problems:
            units = build_pieces(edl, lab, pieces)
            vid = os.path.join(work, "video.mp4")
            info = render_video(edl, lab, units, raster, lab.p("cache", "chunks"), vid, problems)
        if not problems:
            vt = splice_overlays(edl, vid, os.path.join(work, "titled.mp4"), raster, work, N)
            wav = os.path.join(work, "mix.wav")
            audio, env = mix_audio(edl, lab, wav)
            notes += audio.pop("notes", [])
            np.savez_compressed(os.path.join(rdir, "audio_env.npz"), **env)
            # integrated loudness and true peak of the preview mix (loudness_off; the EDL mix block is in it)
            lu_, tp_ = measure_lufs(wav)
            audio["lufs_i"] = None if lu_ is None or not math.isfinite(lu_) or lu_ < -70 else round(lu_, 2)
            audio["tp_dbtp"] = None if tp_ is None or not math.isfinite(tp_) else round(tp_, 2)
            audio["mix_gain_db"] = mix_gain_of(edl)
            tmp = final + ".tmp.mov"
            run([ffmpeg_bin(), "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", vt, "-i", wav,
                 "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "pcm_s16le", "-f", "mov", tmp], "ffmpeg (mux)")
            got = count_frames(tmp)
            if got != N:
                problems.append("preview has %d frames instead of %d" % (got, N))
                os.remove(tmp)
            else:
                os.replace(tmp, final)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    res = "STOP" if problems else "OK"
    st = {"schema": "resolve-editor/preview@1", "edl": posix(edl_path), "edl_hash": edl_hash(edl),
          "renderer": RENDERER_VERSION, "result": res,
          "frames": N if not problems else None, "expected": N, "raster": list(raster), "fps": fps_str(tl_fps(edl)),
          "chunks": [{"path": lab.relto(c["path"]), "frames": c["frames"], "expected": c["expected"],
                      "cached": c["cached"]} for c in info],
          "problems": problems, "notes": notes, "audio": audio, "seconds": round(time.time() - t_start, 2)}
    write_json(os.path.join(rdir, "preview.json"), st)
    return st, rdir


def cmd_preview(lab, edl_path, out=None, as_json=False):
    st, rdir = render_preview(lab, edl_path, out)
    if as_json:
        print(json.dumps(st, indent=1))
        return st
    lines = ["preview_frames: %s" % p for p in st["problems"]] + ["note: %s" % n for n in st["notes"]]
    lines.append("frames %s of %d, %d chunks (%d from cache), %.1f s" % (
        st["frames"], st["expected"], len(st["chunks"]), sum(1 for c in st["chunks"] if c["cached"]), st["seconds"]))
    wrote = [os.path.join(rdir, "preview.mov")] if st["result"] == "OK" else []
    print_result(st["result"], lines, wrote + [os.path.join(rdir, "preview.json")])
    return st


# ------------------------------------------------------------------------------------------------------------ checks
CHECK_LEVELS = {
    "edl_invalid": "STOP", "offline_media": "STOP", "preview_frames": "STOP", "flash_frame": "STOP",
    "black_gap": "STOP", "silence_gap": "STOP", "clipped_word": "STOP", "hook_3s": "STOP", "caption_timing": "STOP",
    "caption_layout": "STOP", "safe_zone": "STOP", "duration": "STOP", "music_end": "STOP",
    "words_changed": "STOP", "frame_edge": "STOP", "fps_mismatch": "STOP",
    "audio_fades": "WARN", "beat_sync": "WARN", "pacing": "WARN", "jump_cut": "WARN", "repeat_shot": "WARN",
    "overlay_jump": "WARN", "checks_off_ignored": "WARN", "text_overlap": "WARN", "mix_peak": "WARN", "rate_edge": "WARN",
    "transition_share": "WARN", "filler_left": "WARN", "pause_long": "WARN", "speech_music_gap": "WARN", "abcd": "WARN",
    "subject_cropped": "WARN", "channel_balance": "WARN",
    # round 2: the message layer and the sound bed (caption_unverified is a WARN where captions.verify is "warn")
    "caption_unverified": "STOP", "message_gap": "WARN", "offer_late": "WARN", "duck_pump": "WARN",
    "repeat_setup": "WARN", "edge_energy": "WARN", "caption_title_dup": "WARN", "text_competes": "WARN",
    "ending_tight": "WARN", "music_start_off_bar": "WARN", "still_edge": "WARN", "marker_long": "WARN",
    "markers_many": "WARN", "joined_split": "STOP", "word_twice": "STOP",
    # effects, transitions, animated text and sound effects (fx_lab.fx_checks; FUSION_PLAN s1, D7: taste is WARN,
    # safety is STOP; fx_freeze is a STOP with text on the freeze, else a WARN)
    "fx_density": "WARN", "fx_genre": "WARN", "fx_families": "WARN", "fx_repeat": "WARN", "fx_length": "WARN",
    "fx_zoom": "WARN", "fx_shake": "WARN", "fx_rgb": "WARN", "fx_text_motion": "WARN", "fx_ramp_repeat": "WARN",
    "fx_hook": "WARN", "fx_offbeat": "WARN", "fx_sfx_sync": "WARN", "fx_soft": "WARN", "fx_span": "WARN",
    "fx_text_shake": "STOP", "fx_edges": "STOP", "fx_flash_rule": "STOP", "fx_freeze": "STOP",
    "anim_read_floor": "STOP", "caption_keywords": "WARN", "text_font_metrics": "WARN", "sfx_loud": "WARN",
    "fx_refused": "STOP", "first_frame_text": "WARN",
    "transition_broken": "STOP", "smooth_cut_misuse": "STOP", "transition_amateur": "WARN",
    "transition_flash_risk": "WARN", "transition_black_card": "WARN", "transition_mb_off": "WARN",
    # final fix pass: loudness, title chains and the end card, the shape of a music piece, voice gaps, caption bands
    "loudness_off": "WARN", "title_blink": "WARN", "end_card": "WARN", "pace_decel": "WARN", "music_lull": "WARN",
    "music_open_quiet": "WARN", "vo_gap": "WARN", "ending_tail": "WARN", "caption_jump": "WARN",
    "type_system": "WARN", "recycled_half": "WARN", "title_repeat": "WARN", "reveal_hold": "WARN",
    "text_contrast": "WARN",
}
MIX_PEAK_STOP_DBFS = 6.0
BED_BURIED_LU = 5.0            # speech_music_gap: a bed this far past the preset's duck_lu is buried
TITLE_BLINK_MAX_F = 6          # title_blink: text off for 1 to this many frames between two titles on one track
TITLE_GAP_CLOSE_F = 3          # assemble closes gaps of 1 to this many frames between titles on one track
TITLE_SNAP_F = 3               # a title edge this close to a picture cut moves onto it (title_snapped)
CARD_LAST_S = 5.0              # end_card: a cta or end_card title starting in the last this many seconds makes a card
CARD_CTA_HOLD_S = 2.5          # the CTA holds at least min(this, CARD_CTA_SHARE of the card) to the end
CARD_CTA_SHARE = 0.6
CARD_TOGETHER_S = 1.5          # brand name and CTA on screen together at least this long
CARD_CENTRE_PX = 12            # card text centre at most this far from W / 2
PACE_DECEL_RATIO = 1.4         # pace_decel: late median shot this many times the early one
PACE_DECEL_MAX_S = 30.0        # pace_decel judges pieces under this length (P1's short-piece rule)
LULL_DB, LULL_END_DB, LULL_MIN_S = 8.0, 6.0, 1.5   # music_lull and music_open_quiet thresholds
VO_GAP_LIFT_S = 0.4            # vo_gap: a lifted musical breath may run this much longer
TAIL_MAX_S = 1.2               # ending_tail: at most this long past the last word (unless a card covers it)
MUSIC_INTERLUDE_S = 2.0       # vo_gap, ending_tail: on-camera speech with the music up this long between lines (a
                              # bar at 120 BPM) is a music interlude, not a hole
LONG_FORM_S = 60.0             # ending_tail: from this length on, on-camera speech may end on a music outro
CAPTION_JUMP_TOL = 0.03        # caption_jump: heights closer than this fraction of H are one band
CAPTION_JUMP_EVERY_S = 6.0     # caption_jump: at most one height change per this many seconds of captions
CAPTION_BAND_SNAP = 0.06       # assemble: caption_y farther than this from the main band makes the alternate band
RECYCLED_SHARE = 0.35          # recycled_half
CONTRAST_MIN = 3.0             # text_contrast: WCAG contrast ratio under this on an unboxed, thin-stroked look


def buried_fix(edl, excess):
    """speech_music_gap's fix for a buried bed: per music item under the voice, the duck that raises the bed by the
    excess (its current depth minus the excess; an auto duck's depth includes the bed shift its cap made), or, when
    the duck is shallower than the excess, no duck and the rest as gain_db."""
    excess = round(float(excess), 1)
    its = [it for tr in edl["tracks"].get("audio", []) if tr.get("role") == "music" for it in tr.get("items", [])
           if is_media_item(it) and it.get("enabled", True) is not False and it.get("duck")]
    tips = []
    for it in its:
        dk = it.get("duck") or {}
        eff = float(dk.get("depth_db") or 0.0) + (float(dk.get("base_shift_db") or 0.0) if dk.get("mode") == "auto"
                                                   else 0.0)
        g = float(it.get("gain_db") or 0.0) + (float(dk.get("base_shift_db") or 0.0) if dk.get("mode") == "auto"
                                               else 0.0)
        if eff - excess >= 0.5:
            tips.append("\"duck\": %g on %s (its duck is %g dB now)" % (round(eff - excess, 1), it["id"], round(eff, 1)))
        else:
            tips.append("\"duck\": \"none\" and \"gain_db\": %g on %s (its duck is only %g dB)" % (
                round(g + excess - eff, 1), it["id"], round(eff, 1)))
    if not tips:
        return "raise the music under the voice by about %.0f dB: a higher gain_db on the music item" % excess
    return ("raise the music under the voice by about %.0f dB: a shallower duck, %s; or raise the music's gain_db "
            "by that much (the bed between the lines rises too)" % (excess, "; ".join(tips)))


def loudness_stats(P, au):
    """stats.loudness from the preview's audio block: {lufs_i, tp_dbtp, target, mix_gain_db, gain_more_db,
    residual_lu, tol_lu, codec_tp_db}, or None when the preview has no loudness (older preview, silent mix)."""
    lufs = (au or {}).get("lufs_i")
    if lufs is None:
        return None
    a = P.get("audio") or {}
    target = float(a.get("lufs", -14.0))
    ctp = float(a.get("codec_tp_db", a.get("true_peak_db", -1.0)))
    tol = float(a.get("tol_lu", 0.5))
    tp = au.get("tp_dbtp")
    more = target - float(lufs) if tp is None else min(target - float(lufs), ctp - float(tp))
    more = round(more, 1)
    return {"lufs_i": round(float(lufs), 1), "tp_dbtp": None if tp is None else round(float(tp), 1),
            "target": target, "mix_gain_db": round(float(au.get("mix_gain_db") or 0.0), 1), "gain_more_db": more,
            "residual_lu": round(target - (float(lufs) + more), 1), "tol_lu": tol, "codec_tp_db": ctp}


def loudness_fix(ld, total):
    """loudness_off's fix: the cut list's mix line, and what the true peak leaves for the Deliver page."""
    deliver = ("normalise the %.1f LU left on the Deliver page (Audio: Normalize Audio Levels, Optimize to Standard) "
               "and say so in the hand-over, or lower the loudest peaks (a music hit) to make room" % ld["residual_lu"])
    if abs(ld["gain_more_db"]) < 0.05:
        return ("the mix gain (%+.1f dB) is already as high as the true peak allows (%s dBTP against the codec "
                "ceiling %g): %s" % (ld["mix_gain_db"], ld["tp_dbtp"], ld["codec_tp_db"], deliver))
    if ld["gain_more_db"] < 0 and ld["lufs_i"] < ld["target"]:
        return ("the true peak (%s dBTP) is already over the codec ceiling (%g dBTP), so no uniform gain makes it louder: "
                "set \"mix\": {\"gain_db\": %.1f} at the top of the cut list to clear the ceiling, then %s" % (
                    ld["tp_dbtp"], ld["codec_tp_db"], total, deliver))
    line = ("set \"mix\": {\"gain_db\": %.1f} at the top of the cut list (or run `E level`) and assemble again"
            % total)
    if ld["residual_lu"] > ld["tol_lu"]:
        line += "; the true peak caps the gain, so then %s" % deliver
    return line
# a cut list or EDL may switch off only soft checks and these four (a slow-burn opening, a length set by the brief,
# a song that is meant to be cut, a repeated word that is meant); every other STOP gate protects the viewer or the
# build and stays on
EDL_OFF_OK = {"hook_3s", "duration", "music_end", "word_twice"}


def split_checks_off(ids):
    """(honoured, ignored) check ids of a cut list's or EDL's checks_off."""
    ok, bad = [], []
    for c in ids or []:
        c = str(c)
        (ok if (c in EDL_OFF_OK or CHECK_LEVELS.get(c, "WARN") == "WARN") and c != "checks_off_ignored" else bad).append(c)
    return ok, bad


def picture_continues(edl, a, b, frame):
    """True when item b takes over from item a at this frame with the same picture: the same media, speed and
    framing, and b's source frame continues a's (a J or L cut made with hold_prev_s or early_s). Such a join is not
    a cut."""
    if not (is_media_item(a) and is_media_item(b)) or a.get("media") != b.get("media"):
        return False
    def frame_of(it):
        t = it.get("transform") or {}
        return tuple(round(float(t.get(k, d) or d), 4) for k, d in (("zoom", 1.0), ("pan_px", 0.0), ("tilt_px", 0.0)))
    if abs(speed_of(a) - speed_of(b)) > 1e-9 or frame_of(a) != frame_of(b):
        return False
    try:
        mf, tf = float(media_fps(edl, a["media"])), float(tl_fps(edl))
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return False
    r = speed_of(a) * mf / tf

    def src_at(it, f):
        return int(it["src_in"]) + (int(f) - int(it["rec_in"])) * r
    return abs(src_at(b, frame) - src_at(a, frame)) <= max(1.0, math.ceil(mf / tf)) + 1e-6


def cuts_and_shots(edl, pieces, N):
    """Cut positions (a dissolve counts once, at its middle) and the visible shots between them. A join where the
    picture continues (a held or early picture of the same shot) is not a cut."""
    cuts = []
    for k in range(len(pieces) - 1):
        a, b = pieces[k], pieces[k + 1]
        if a[0] == "xfade" or b[0] == "xfade":
            continue
        if a[0] == "seg" and b[0] == "seg" and picture_continues(edl, a[3], b[3], a[2]):
            continue
        cuts.append({"frame": a[2], "a": a[3]["id"] if a[0] == "seg" else None, "b": b[3]["id"] if b[0] == "seg" else None,
                     "xfade": False})
    for kind, r0, r1, obj in pieces:
        if kind == "xfade":
            cuts.append({"frame": (r0 + r1) // 2, "a": obj[1]["id"], "b": obj[2]["id"], "xfade": True})
    cuts.sort(key=lambda c: c["frame"])
    bounds = [0] + [c["frame"] for c in cuts] + [N]
    # a black gap between two cuts is no shot (check reports it as black_gap)
    gaps = {(int(r0), int(r1)) for kind, r0, r1, obj in pieces if kind == "gap"}
    shots = [[bounds[k], bounds[k + 1]] for k in range(len(bounds) - 1)
             if bounds[k + 1] > bounds[k] and (bounds[k], bounds[k + 1]) not in gaps]
    return cuts, shots


def beat_grid(edl, lab, N):
    mu = edl.get("music") or {}
    if not mu.get("beats"):
        return None
    data = load_json_or(lab.rel(mu["beats"]), None)
    if not data or not data.get("beats"):
        return None
    f = float(tl_fps(edl))
    off = float(mu.get("offset_frames") or 0)
    g = np.round(off + np.array(data["beats"], np.float64) * f, 6)
    return g[(g >= -f) & (g <= N + f)]


def beat_tol(edl, P):
    tol = list((P.get("music") or {}).get("beat_tol_frames") or [-2, 1])
    f = float(tl_fps(edl))
    if f > 31 and tol == [-2, 1]:
        # the preset numbers are 25 to 30 fps frames (80 ms early, 40 ms late); keep the same times at 50 or 60 fps,
        # inside KNOWLEDGE's 90 ms limit for an early cut
        tol = [-int(math.floor(0.080 * f + 1e-6)), int(math.floor(0.040 * f + 1e-6))]
    return tol


def beat_off(c, grid):
    if grid is None or not len(grid):
        return None
    return float(np.round(c - grid[np.argmin(np.abs(grid - c))], 3))


def densest_window(cuts, N, fps, span_s=10.0):
    """(cuts, start frame) of the window of span_s with the most cuts, window starts 1 s apart. On a tie the LATEST
    window wins, so evenly cut pacing never reads as front-loaded: an early densest window is strictly denser than
    every later one."""
    span = int(round(span_s * fps))
    if N <= span or not cuts:
        return (len(cuts), 0)
    cf = np.array([c["frame"] for c in cuts])
    best, at = -1, 0
    for s in range(0, N - span + 1, max(1, int(round(fps)))):
        n = int(((cf >= s) & (cf < s + span)).sum())
        if n >= best:
            best, at = n, s
    return best, at


def _chk(out, cid, frames, items, msg, fix, level=None):
    c = {"id": cid, "at_frames": [int(f) for f in frames], "items": list(items), "msg": msg, "fix": fix}
    if level:
        c["level"] = level           # a finding more severe than its id's usual level (mix_peak far over 0 dBFS)
    out.append(c)


def _edl_words(edl, lab, doc=False):
    """{media id: transcript words} (or the whole transcript with doc=True) through the EDL's media entries."""
    cache = {}

    def get(mid):
        if mid not in cache:
            wf = (edl["media"].get(mid) or {}).get("words")
            cache[mid] = (load_words_file(lab.rel(wf) if lab else wf) if wf else None) or {}
        return cache[mid] if doc else (cache[mid].get("words") or [])
    return get


UNSURE_P = 0.5          # a word whisper scored under this is unsure until a script or a fix confirms it (measured:
                        # the one misheard word of the trial ad scored 0.43, every right word of two voice-overs 0.55+)


def caption_checks(edl, lab, P, raw):
    """caption_unverified: a caption shows a word whisper was unsure of (tagged low_conf, or scored under 0.5 in a
    transcript no script was aligned to) that no script alignment, fix-word or captions.text_fix covers. STOP where
    the preset's captions.verify is "stop" (ads, demos), else WARN."""
    lvl = "STOP" if str((P.get("captions") or {}).get("verify", "warn")).lower() == "stop" else "WARN"
    doc_of = _edl_words(edl, lab, doc=True)
    for tr in edl["tracks"].get("subtitle", []):
        for c in tr.get("items", []):
            fixed = {(m_, i_) for m_, i_ in (c.get("fixed_words") or [])}
            bad = []
            for mid, i in c.get("src_words") or []:
                d_ = doc_of(mid)
                W = d_.get("words") or []
                if not isinstance(i, int) or not 0 <= i < len(W):
                    continue
                tg = W[i].get("tags") or []
                p_ = W[i].get("p")
                aligned = bool((d_.get("script") or {}).get("aligned"))
                unsure = "low_conf" in tg or (not aligned and isinstance(p_, (int, float)) and p_ < UNSURE_P)
                if unsure and "script_fix" not in tg and (mid, i) not in fixed:
                    bad.append((mid, i, W[i].get("w", "")))
            if bad:
                _chk(raw, "caption_unverified", [int(c["rec_in"])], [c["id"]], "caption %s shows %s, which the "
                     "transcript is unsure of and nothing confirms" % (c["id"], ", ".join(
                         "%r (%s word %d)" % (w, m_, i_) for m_, i_, w in bad)),
                     "align the voice-over script (`M script %s --file SCRIPT`), or set captions.text_fix "
                     "{\"%s:%d\": \"the right word\"} from the script, the brief or its glossary; never guess a word "
                     "(when nobody knows it, it goes into the open actions)" % (bad[0][0], bad[0][0], bad[0][1]),
                     level=lvl)


def message_checks(edl, P, words_tl, raw):
    """message_gap and offer_late from the preset's message block {gap_max_s, offer_by} (absent: both off)."""
    msg = P.get("message") or {}
    if not isinstance(msg, dict) or not msg:
        return
    f = float(tl_fps(edl))
    N = programme_frames(edl)
    roles = {tr.get("id"): tr.get("role") for tr in edl["tracks"].get("audio", [])}
    spoken = [w for w in words_tl if roles.get(w.get("track")) != "nat"]
    titles = [it for tr in edl["tracks"]["video"] for it in tr.get("items", [])
              if it.get("kind") == "title" and it.get("enabled", True) is not False]
    caps = [c for tr in edl["tracks"].get("subtitle", []) for c in tr.get("items", [])]
    gmax = msg.get("gap_max_s")
    if gmax is not None and spoken:
        lo, hi = float(spoken[0]["t0"]), float(spoken[-1]["t1"])
        iv = sorted([(float(w["t0"]), float(w["t1"])) for w in spoken] +
                    [(int(t["rec_in"]) / f, int(t["rec_out"]) / f) for t in titles + caps])
        best, at, cur = 0.0, lo, lo
        for a, b in iv:
            if b <= cur:
                continue
            if a > cur and min(a, hi) - cur > best:
                best, at = min(a, hi) - cur, cur
            cur = max(cur, b)
            if cur >= hi:
                break
        if hi - cur > best:
            best, at = hi - cur, cur
        if best > float(gmax) + 0.5 / f:
            _chk(raw, "message_gap", [rnd(at * f)], [], "%.1f s with no voice, title or caption at %.2f-%.2f s (the "
                 "preset allows %g s)" % (best, at, at + best, float(gmax)),
                 "put a super there (the spoken keyword or the next line's promise), bring the next line in earlier, "
                 "or tighten the stretch")
    ob = msg.get("offer_by")
    if ob is not None:
        offers = sorted((t for t in titles if t.get("role") == "offer"), key=lambda t: int(t["rec_in"]))
        if offers and int(offers[0]["rec_in"]) > float(ob) * N + 0.5:
            t = offers[0]
            _chk(raw, "offer_late", [int(t["rec_in"])], [t["id"]], "the offer title %s starts at %.1f s, %.0f %% into "
                 "the piece (the preset wants it by %.0f %%)" % (t["id"], int(t["rec_in"]) / f,
                                                                   100.0 * int(t["rec_in"]) / max(1, N), 100 * float(ob)),
                 "bring the offer (or the reason to act) forward to %.1f s or earlier" % (float(ob) * N / f))
        elif not offers:
            _chk(raw, "offer_late", [], [], "no title has role offer, but the preset wants the offer on screen by "
                 "%.0f %% of the piece (%.1f s); said only in the voice, it is missed with the sound off"
                 % (100 * float(ob), float(ob) * N / f),
                 "add a title with role offer (the offer in a few words) at or before %.1f s, or put the offer "
                 "into an existing title and set its role to offer" % (float(ob) * N / f))


def duck_pump_checks(edl, P, raw):
    """duck_pump: a music envelope that rises and falls 4 dB or more within 2.5 s, a duck ramp that starts in the
    item's first 0.3 s (the bed opens loud and dips before anyone speaks), or a swing past duck_max_db."""
    f = float(tl_fps(edl))
    mx_p = (P.get("music") or {}).get("duck_max_db")
    for tr in edl["tracks"].get("audio", []):
        if tr.get("role") != "music":
            continue
        for it in tr.get("items", []):
            env = sorted(((int(p[0]), float(p[1])) for p in (it.get("volume_env") or [])), key=lambda q: q[0])
            if len(env) < 2 or not is_media_item(it):
                continue
            r0 = int(it["rec_in"])
            runs = []
            for (f0, v0), (f1, v1) in zip(env, env[1:]):
                dv = v1 - v0
                if abs(dv) <= 0.05:
                    continue
                if runs and (dv > 0) == (runs[-1][2] > 0) and f0 <= runs[-1][1]:
                    runs[-1] = (runs[-1][0], f1, runs[-1][2] + dv)
                else:
                    runs.append((f0, f1, dv))
            early = [r for r in runs if r[2] < -1.0 and r[1] > r[0] and r[0] < DUCK_EARLY_S * f]
            if early:
                _chk(raw, "duck_pump", [r0 + early[0][0]], [it["id"]], "the duck of %s ramps down %.1f dB from its first "
                     "frames (%.2f s): the bed opens loud and dips before anyone speaks" % (
                         it["id"], -early[0][2], early[0][0] / f),
                     "start the music ducked (assemble does when the first word comes within 1.5 s of its start), or "
                     "start it after the first line")
            # a lift rises before its segment starts (full level on the drop's first frame): its window starts the
            # longest rise duck_env makes earlier
            dk_ = it.get("duck") if isinstance(it.get("duck"), dict) else {}
            rise_f = int(math.ceil(max(DUCK_LIFT_RISE_S, 2 * math.ceil(float(dk_.get("depth_db") or 0) / DUCK_STEP_DB)
                                       / f) * f - 1e-9)) + 1
            lifted = [(int(x[1]) - r0 - rise_f, int(x[2]) - r0) for x in (dk_.get("lifts") or [])]
            for r, s in zip(runs, runs[1:]):
                if any(a_ - 1 <= r[0] and s[1] <= b_ + 1 for a_, b_ in lifted):
                    continue          # a lift the cut list asked for (the drop): a quick rise and fall on purpose
                if r[2] >= DUCK_PUMP_DB - 0.05 and s[2] <= -(DUCK_PUMP_DB - 0.05) and s[1] - r[0] <= DUCK_PUMP_S * f + 0.5:
                    _chk(raw, "duck_pump", [r0 + r[0]], [it["id"]], "%s rises %.1f dB and falls %.1f dB within %.2f s at "
                         "%.2f s: the music pumps between the lines" % (it["id"], r[2], -s[2], (s[1] - r[0]) / f,
                                                                       (r0 + r[0]) / f),
                         "keep it ducked through the pause (music.duck_hold_s or the item's duck_hold_s), open the "
                         "pause to about 3.1 s or more so the music can lift and settle, or mark the picture-only "
                         "segment in the pause \"lift\": true for a deliberate quick lift")
            mx = (it.get("duck") or {}).get("max_db", mx_p) if isinstance(it.get("duck"), dict) else mx_p
            vals = [v for _, v in env]
            if mx is not None and max(vals) - min(vals) > abs(float(mx)) + 0.05:
                _chk(raw, "duck_pump", [r0], [it["id"]], "%s swings %.1f dB between the lines and the pauses (the cap "
                     "is %g dB)" % (it["id"], max(vals) - min(vals), abs(float(mx))),
                     "assemble again (it lowers the bed and ducks by the cap), or lower the music's gain_db")


MARKER_NOTE_MAX = 120          # characters of a marker's name plus note
MARKERS_PER_30S = 5
EDGE_ENERGY_DB = 12.0          # the 20 ms a dialogue edge throws away this far over the noise floor: a sound is cut


def _src_rms_db(src, t0, t1):
    a, b = int(round(t0 * src.sr)), int(round(t1 * src.sr))
    if b <= a:
        return None
    x = src.read(a, b).astype(np.float64)
    return 10 * math.log10(float((x * x).mean()) + 1e-12)


def _floor_db(src, cache={}):
    """The noise floor of a WAV: the 10th percentile of its 20 ms RMS levels (at most 60 s of it, spread evenly)."""
    key = (src.path, src.n)
    if key not in cache:
        w = int(0.020 * src.sr)
        nwin = src.n // max(1, w)
        step = max(1, nwin // 3000)
        lv = []
        for k in range(0, nwin, step):
            x = src.read(k * w, (k + 1) * w).astype(np.float64)
            lv.append(10 * math.log10(float((x * x).mean()) + 1e-12))
        cache[key] = float(np.percentile(lv, 10)) if lv else -90.0
    return cache[key]


def more_checks(edl, lab, P, words_tl, pieces, cuts, raw):
    """The SHOULD checks of round 2: shot economy (repeat_setup), text on text (caption_title_dup, text_competes),
    endings (ending_tight, music_start_off_bar), dialogue edges (edge_energy), stills (still_edge) and markers
    (marker_long, markers_many)."""
    f = float(tl_fps(edl))
    N = programme_frames(edl)
    titles = [it for tr in edl["tracks"]["video"] for it in tr.get("items", [])
              if it.get("kind") == "title" and it.get("enabled", True) is not False]
    caps = [c for tr in edl["tracks"].get("subtitle", []) for c in tr.get("items", [])]

    def toks(s):
        return [w for w in re.sub(r"[^a-z0-9' ]", " ", str(s).lower().replace("’", "'")).split() if w]
    # markers: one-line notes for an action only the user can take
    mks = edl.get("markers") or []
    for mk in mks:
        txt = ("%s %s" % (mk.get("name", ""), mk.get("note", ""))).strip()
        if len(txt) > MARKER_NOTE_MAX:
            _chk(raw, "marker_long", [int(mk.get("frame", 0))], [], "the marker %r at %.2f s is %d characters long" % (
                str(mk.get("name", ""))[:30], int(mk.get("frame", 0)) / f, len(txt)),
                "keep a marker to one line for an action only the user can take (90 characters); the reasoning "
                "belongs in the item's why")
    if N and len(mks) > MARKERS_PER_30S * max(1.0, N / f / 30.0):
        _chk(raw, "markers_many", [], [], "%d markers in %.1f s (at most %d per 30 s)" % (
            len(mks), N / f, MARKERS_PER_30S), "keep only the markers the user must act on; put the rest into the "
                                               "hand-over")
    # text on text
    for c in caps:
        cw = toks(c.get("text", ""))
        if not cw:
            continue
        for t in titles:
            if int(t["rec_in"]) < int(c["rec_out"]) and int(c["rec_in"]) < int(t["rec_out"]):
                tw = set(toks(" ".join(t.get("lines") or [str(t.get("text", ""))])))
                share = sum(1 for w in cw if w in tw) / float(len(cw))
                if share >= 0.6:
                    _chk(raw, "caption_title_dup", [int(c["rec_in"])], [c["id"], t["id"]], "caption %s repeats title %s "
                         "(%d %% of its words) while both are on screen" % (c["id"], t["id"], round(100 * share)),
                         "set captions.suppress_under_titles, or change the title to the keyword only")
                    break
    for t in titles:
        if int(t["rec_in"]) >= 3 * f:
            continue
        tw = set(toks(" ".join(t.get("lines") or [str(t.get("text", ""))])))
        for c in caps:
            if int(c["rec_in"]) < min(int(t["rec_out"]), 3 * f) and int(t["rec_in"]) < int(c["rec_out"]):
                cw = toks(c.get("text", ""))
                if cw and sum(1 for w in cw if w in tw) / float(len(cw)) < 0.6:
                    _chk(raw, "text_competes", [max(int(t["rec_in"]), int(c["rec_in"]))], [t["id"], c["id"]],
                         "title %s and caption %s say different things together in the first 3 s" % (t["id"], c["id"]),
                         "one message by 3 s: make the hook title the first spoken words (then "
                         "captions.suppress_under_titles drops the cues that repeat it), or start the title after "
                         "those captions, or the captions after the title")
                    break
    # endings
    spoken = [w for w in words_tl if (w.get("track") or "").startswith("A")]
    roles = {tr.get("id"): tr.get("role") for tr in edl["tracks"].get("audio", [])}
    spoken = [w for w in spoken if roles.get(w.get("track")) != "nat"]
    if spoken and N:
        last = max(float(w["t1"]) for w in spoken)
        if N / f - last < 0.5 - 0.5 / f:
            _chk(raw, "ending_tight", [N], [], "the programme ends %.2f s after the last word" % (N / f - last),
                 "give the ending at least 0.5 s after the last word (a held picture, the end card or the music's "
                 "button)")
    music_its = [it for tr in edl["tracks"].get("audio", []) if tr.get("role") == "music" for it in tr.get("items", [])
                 if is_media_item(it)]
    for it in music_its:
        if spoken and int(it.get("fade_out", 0)) > 0:
            last = max(float(w["t1"]) for w in spoken if float(w["t0"]) < int(it["rec_out"]) / f) \
                if any(float(w["t0"]) < int(it["rec_out"]) / f for w in spoken) else None
            fs = (int(it["rec_out"]) - int(it["fade_out"])) / f
            if last is not None and fs < last + 0.2 - 0.5 / f and int(it["rec_out"]) / f > last:
                _chk(raw, "ending_tight", [int(it["rec_out"]) - int(it["fade_out"])], [it["id"]],
                     "the music %s starts fading at %.2f s, before the last word ends (%.2f s) plus 0.2 s" % (
                         it["id"], fs, last), "start the fade after the last word, or end the programme later")
        if int(it.get("src_in", 0)) > 0 and int(it.get("fade_in", 0)) < rnd(0.5 * f) and lab is not None:
            bt = lab.analysis(it["media"], "beats") or {}
            downs = [float(x) for x in (bt.get("downbeats") or [])]
            mf = float(media_fps(edl, it["media"]))
            t_src = int(it["src_in"]) / mf
            beats_ = [float(x) for x in (bt.get("beats") or [])]
            # a start on a beat is a pickup into the next bar (two beats before a drop): fine without a fade
            on_beat = bool(beats_) and min(abs(t_src - x) for x in beats_) <= 0.06
            if downs and not on_beat and min(abs(t_src - d) for d in downs) > 0.06:
                near = min(downs, key=lambda d: abs(t_src - d))
                _chk(raw, "music_start_off_bar", [int(it["rec_in"])], [it["id"]], "music %s starts at %.2f s of the "
                     "song, %d ms from the nearest bar line (%.2f s) and off every beat, with a %d frame fade-in" % (
                         it["id"], t_src, round(1000 * abs(t_src - near)), near, int(it.get("fade_in", 0))),
                     "fade it in over 0.5 s or more (the music item's fade_in_s), or start it on a beat or a bar line "
                     "(in_s %.2f moves every beat after it too)" % near)
    # shot economy: the same setup again. A talking head (items with words or sync sound, or media with speech) and a
    # J or L cut are not repeats
    words_of = _edl_words(edl, lab)

    def speaks(mid):
        return sum(1 for w in words_of(mid) if "low_conf" not in (w.get("tags") or [])) >= 3
    vis = [it for _, it in visible_video(edl) if is_media_item(it) and not it.get("words") and not it.get("link")
           and not speaks(it["media"]) and not {"callback", "hold", "early", "repeat_ok"} & set(it.get("tags") or [])]
    by_media = {}
    for it in sorted(vis, key=lambda x: int(x["rec_in"])):
        by_media.setdefault(it["media"], []).append(it)

    def zf(it):
        tf = it.get("transform") or {}
        return float(tf.get("zoom", 1.0) or 1.0), (float(it["frame_x"]) if it.get("frame_x") is not None else None)

    def continues(a, b):
        # one shot that runs on from one item into the next (a V2 overlay into a V1 breath): one appearance
        return (int(b["rec_in"]) == int(a["rec_out"]) and abs(int(b["src_in"]) - int(a["src_out"])) <= 1
                and abs(speed_of(a) - speed_of(b)) < 1e-9 and zf(a) == zf(b))
    for mid, its in by_media.items():
        apps = []
        for it in its:
            if apps and continues(apps[-1][-1], it):
                apps[-1].append(it)
            else:
                apps.append([it])
        if len(apps) >= 3:
            _chk(raw, "repeat_setup", [int(apps[2][0]["rec_in"])], [x["id"] for x in its], "%s is on screen %d times "
                 "(%s)" % (mid, len(apps), ", ".join("%.1f s" % (int(x[0]["rec_in"]) / f) for x in apps)),
                "use each setup at most twice; for a thin shoot hold a shot longer, slow it, change its size by 30 % "
                "or add a text card; tag a deliberate callback \"callback\"")
            continue
        its = [x[-1] if k_ == 0 else x[0] for k_, x in enumerate(apps)] if len(apps) == 2 else [x[0] for x in apps]
        for a, b in zip(its, its[1:]):
            if int(b["rec_in"]) - int(a["rec_out"]) >= 10 * f or int(b["rec_in"]) == int(a["rec_out"]):
                continue
            (za, xa), (zb, xb) = zf(a), zf(b)
            if max(za, zb) / max(1e-6, min(za, zb)) - 1.0 < 0.30 - 1e-9 and (xa is None or xb is None
                                                                              or abs(xa - xb) < 0.1):
                _chk(raw, "repeat_setup", [int(b["rec_in"])], [a["id"], b["id"]], "%s and %s show the same setup (%s) "
                     "%.1f s apart with the same framing" % (a["id"], b["id"], mid,
                                                             (int(b["rec_in"]) - int(a["rec_out"])) / f),
                     "change the size by 30 % or more, use another setup, or tag the second \"callback\"")
    # dialogue edges: the 20 ms a cut throws away should be quiet
    if lab is not None:
        for tr in edl["tracks"].get("audio", []):
            if tr.get("role") != "dialogue":
                continue
            its = sorted_items(tr)
            for k, it in enumerate(its):
                if not is_media_item(it) or not it.get("words"):
                    continue
                m = edl["media"].get(it["media"]) or {}
                fw = audio_file_of(lab, m)
                if not fw or not os.path.exists(fw):
                    continue
                try:
                    src = wav_source(fw)
                except (Fail, OSError, ValueError):
                    continue
                off = float((m.get("audio") or {}).get("offset_s") or 0.0)
                mf = float(media_fps(edl, it["media"]))
                a = int(it["src_in"]) / mf - off
                b = a + rec_len(it) / f * speed_of(it)
                prev = its[k - 1] if k else None
                nxt = its[k + 1] if k + 1 < len(its) else None

                def joined(x, y):
                    return (x is not None and y is not None and x.get("media") == y.get("media")
                            and int(x["rec_out"]) == int(y["rec_in"]) and int(x.get("src_out", -1)) == int(y.get("src_in", -2)))
                floor = _floor_db(src)
                for side, t0, t1, cont in (("in", a - 0.020, a, joined(prev, it)), ("out", b, b + 0.020, joined(it, nxt))):
                    if cont or t0 < 0:
                        continue
                    lv = _src_rms_db(src, t0, t1)
                    if lv is not None and lv - floor >= EDGE_ENERGY_DB:
                        at = int(it["rec_in"]) if side == "in" else int(it["rec_out"])
                        _chk(raw, "edge_energy", [at], [it["id"]], "the %s point of %s throws away sound %.0f dB over "
                             "the room (a breath, a word's onset or its tail)" % (side, it["id"], lv - floor),
                             "listen at %.2f s; nudge the edge with the segment's edge_ms {\"%s\": %s} or move it to "
                             "a pause" % (at / f, side, "-40" if side == "in" else "40"))
    # stills: a stray line along an edge of a picture (a border of the export) shows as a line on the card
    seen = set()
    for _, it in visible_video(edl):
        m = edl["media"].get(it.get("media")) or {}
        if m.get("kind") != "image" or it.get("media") in seen:
            continue
        seen.add(it.get("media"))
        path = lab.rel(m.get("image") or m.get("path")) if lab is not None else (m.get("image") or m.get("path"))
        if not path or not os.path.exists(path):
            continue
        try:
            from PIL import Image
            with Image.open(path) as im0:
                # measured as Resolve draws it: a transparent border is no line (flattened over black)
                rgba = im0.convert("RGBA")
                flat = Image.new("RGB", rgba.size, (0, 0, 0))
                flat.paste(rgba, mask=rgba.getchannel("A"))
                im = flat.convert("L")
                im.thumbnail((400, 400))
                g = np.asarray(im, np.float64)
        except Exception:
            continue
        if min(g.shape) < 12:
            continue
        bad = []
        for name, edge, inner in (("top", g[0], g[4]), ("bottom", g[-1], g[-5]), ("left", g[:, 0], g[:, 4]),
                                  ("right", g[:, -1], g[:, -5])):
            if float(np.std(inner)) < 12 and abs(float(edge.mean()) - float(inner.mean())) > 25:
                bad.append(name)
        if bad:
            z_ = float((it.get("transform") or {}).get("zoom", 1.0) or 1.0)
            _chk(raw, "still_edge", [int(it["rec_in"])], [it["id"]], "the still %s has a line along its %s edge that "
                 "differs from its background" % (it.get("media"), " and ".join(bad)),
                 "crop the picture a few pixels (zoom 1.01) or export it again without the border" if z_ >= 1.0 else
                 "export it again without the border (or with a transparent one): at zoom %g the whole picture, border "
                 "included, is on screen" % z_)


# ----------------------------------------------------------------------------- final fix pass: shape, card, voice, text
def _titles_of(edl):
    """(track id, title) for every enabled title, track by track, by start."""
    out = []
    for tr in edl["tracks"].get("video", []):
        for it in sorted_items(tr):
            if it.get("kind") == "title" and it.get("enabled", True) is not False:
                out.append((tr.get("id"), it))
    return out


def _frames_union(its, lo, hi):
    s = set()
    for t in its:
        s.update(range(max(int(lo), int(t["rec_in"])), min(int(hi), int(t["rec_out"]))))
    return s


def title_off_frames(it, fps):
    """Per frame of a title: True where its text reads as off (alpha under 0.5, a pop under half its size, nothing
    revealed yet)."""
    n = max(0, rec_len(it))
    if not it.get("anim"):
        return [False] * n
    try:
        st = fx_lab.anim_states(it, fps)
    except Exception:
        return [False] * n
    return [float(s.get("alpha", 1.0)) < 0.5 or float(s.get("scale", 1.0)) < 0.5 or s.get("visible") == 0 for s in st]


def title_blink_checks(edl, raw):
    """title_blink: two consecutive titles on one track leave the text off for 1 to 6 frames (a gap of 0 to 6 frames
    with no title, plus the frames under alpha 0.5 of their fades on both sides of it)."""
    f = float(tl_fps(edl))
    by_tr = {}
    for tid, it in _titles_of(edl):
        by_tr.setdefault(tid, []).append(it)
    for tid, its in by_tr.items():
        for a, b in zip(its, its[1:]):
            gap = int(b["rec_in"]) - int(a["rec_out"])
            if not 0 <= gap <= TITLE_BLINK_MAX_F:
                continue
            oa, ob = title_off_frames(a, f), title_off_frames(b, f)
            ta = next((k for k, v in enumerate(reversed(oa)) if not v), len(oa))
            hb = next((k for k, v in enumerate(ob) if not v), len(ob))
            off = ta + gap + hb
            if not 1 <= off <= TITLE_BLINK_MAX_F:
                continue
            faded = (" and %d faded frame%s" % (ta + hb, "" if ta + hb == 1 else "s")) if ta + hb else ""
            if gap:
                why_ = "a %d frame gap%s" % (gap, faded)
                fix_ = ("end %s on %s's start (give %s \"dur_s\": %.2f, or assemble again: it closes gaps of 1 to %d "
                        "frames and joins the fades), or leave a real pause of %d frames or more" % (
                            a["id"], b["id"], a["id"], (int(b["rec_in"]) - int(a["rec_in"])) / f, TITLE_GAP_CLOSE_F,
                            TITLE_BLINK_MAX_F + 2))
            else:
                why_ = "no gap, %d faded frame%s at the join" % (ta + hb, "" if ta + hb == 1 else "s")
                fix_ = ("assemble again (titles that meet skip the earlier one's exit and the later one's entrance), "
                        "give one of them \"anim\": \"none\", or leave a real pause of %d frames or more" % (
                            TITLE_BLINK_MAX_F + 2))
            _chk(raw, "title_blink", [int(a["rec_out"])], [a["id"], b["id"]], "titles %s and %s on %s leave the text off "
                 "for %d frame%s at %.2f s (%s): it reads as a blink" % (
                     a["id"], b["id"], tid, off, "" if off == 1 else "s", int(a["rec_out"]) / f, why_), fix_)


def card_start(edl, logos=None):
    """The end card's first frame, or None: the last V1 item when it is a still that runs to the end and is a card
    (a logo: tagged "logo" or one of the brand's logo media in `logos`; or a still that carries a title of style
    end_card or cta, or of role brand or cta), or the first title of style end_card or cta that starts in the last
    5 s. A photo montage's last photo with a super on it is not a card."""
    f = float(tl_fps(edl))
    N = programme_frames(edl)
    starts = []
    v1 = [it for it in sorted_items(v1_track(edl)) if it.get("enabled", True) is not False and is_media_item(it)]
    if v1:
        last = max(v1, key=lambda x: int(x["rec_out"]))
        if (edl["media"].get(last.get("media")) or {}).get("kind") == "image" and int(last["rec_out"]) >= N - 1:
            on_ = [t for _, t in _titles_of(edl) if int(t["rec_out"]) > int(last["rec_in"])
                   and int(t["rec_in"]) < int(last["rec_out"])]
            if ("logo" in (last.get("tags") or []) or last.get("media") in (logos or ())
                    or any(t.get("style") in CARD_STYLES or t.get("role") in ("brand", "cta") for t in on_)):
                starts.append(int(last["rec_in"]))
    for _, t in _titles_of(edl):
        if t.get("style") in CARD_STYLES and int(t["rec_in"]) >= N - CARD_LAST_S * f:
            starts.append(int(t["rec_in"]))
    return min(starts) if starts else None


def card_titles(edl, c0=None, logos=None):
    """The titles on the end card (starting at its first frame, or up to 0.2 s before it)."""
    c0 = card_start(edl, logos) if c0 is None else c0
    if c0 is None:
        return []
    lead = rnd(0.2 * float(tl_fps(edl)))
    return [t for _, t in _titles_of(edl) if int(t["rec_in"]) >= c0 - lead]


def end_card_checks(edl, raw, logos=None):
    """end_card: the CTA holds to the end for min(2.5 s, 60 % of the card), the brand name and the CTA are on screen
    together for 1.5 s or more, the CTA is the largest card text, and every card text centres on x = W / 2."""
    f = float(tl_fps(edl))
    N = programme_frames(edl)
    W = float(edl["timeline"]["width"])
    c0 = card_start(edl, logos)
    if c0 is None or N - c0 < 1:
        return
    tts = card_titles(edl, c0)
    if not tts:
        return
    card = N - c0
    ctas = [t for t in tts if t.get("role") == "cta"]
    brands = [t for t in tts if t.get("role") == "brand"]
    if ctas:
        fr = _frames_union(ctas, c0, N)
        hold, k = 0, N - 1
        while k in fr:
            hold += 1
            k -= 1
        need = min(CARD_CTA_HOLD_S * f, CARD_CTA_SHARE * card)
        if hold == 0 and fr:
            # the CTA is on the card but drops off before the last frame: say how early, not "0.00 s"
            gone = N - (max(fr) + 1)
            _chk(raw, "end_card", [max(fr) + 1], [t["id"] for t in ctas], "the CTA %s ends %d frame%s before the "
                 "programme's last frame (at %.2f s of %.2f s): the call to action must hold to the last frame, "
                 "%.2f s or more of the %.2f s card" % (
                     ", ".join(t["id"] for t in ctas), gone, "" if gone == 1 else "s", (max(fr) + 1) / f, N / f,
                     need / f, card / f),
                 "hold the CTA to the last frame (its end on the programme end, frame %d)" % N)
        elif hold < need - 0.5:
            _chk(raw, "end_card", [N - hold if hold else c0], [t["id"] for t in ctas], "the CTA %s is on screen for "
                 "%.2f s to the end of the %.2f s card (from %.2f s): it needs %.2f s or more" % (
                     ", ".join(t["id"] for t in ctas), hold / f, card / f, c0 / f, need / f),
                 "start the CTA on the card's first frame (with the spoken call to action) and hold it to the last "
                 "frame")
        if brands:
            tog = len(_frames_union(brands, c0, N) & fr)
            if tog < CARD_TOGETHER_S * f - 0.5 and tog >= card - 0.5:
                # both are on the whole card, but the card itself is too short for a lockup to read
                _chk(raw, "end_card", [c0], [t["id"] for t in brands + ctas], "the end card is only %.2f s: the name "
                     "%s and the CTA %s are together for all of it, under the %.1f s a lockup needs to read" % (
                         card / f, ", ".join(t["id"] for t in brands), ", ".join(t["id"] for t in ctas),
                         CARD_TOGETHER_S),
                     "make the card longer (3.5 to 5 s: end the last picture earlier and let the card run), keeping "
                     "both titles on all of it")
            elif tog < CARD_TOGETHER_S * f - 0.5:
                _chk(raw, "end_card", [c0], [t["id"] for t in brands + ctas], "the name %s and the CTA %s are on "
                     "screen together for %.2f s: the card never locks them up" % (
                         ", ".join(t["id"] for t in brands), ", ".join(t["id"] for t in ctas), tog / f),
                     "show both for the whole card: overlap the two titles (the second goes on the \"Titles 2\" track "
                     "on its own), the name in style end_card and the CTA in style cta")
        rest = [t for t in tts if t not in ctas and t.get("font_px")]
        if rest and min(float(t.get("font_px") or 0) for t in ctas) < max(float(t["font_px"]) for t in rest) - 0.05:
            big = max(rest, key=lambda t: float(t["font_px"]))
            _chk(raw, "end_card", [c0], [ctas[0]["id"], big["id"]], "the CTA %s (%g px) is smaller than %s (%g px) on "
                 "the card" % (ctas[0]["id"], float(ctas[0].get("font_px") or 0), big["id"], float(big["font_px"])),
                 "make the CTA the largest text on the card (style cta; the name in style end_card)")
    bad = []
    for t in tts:
        if t.get("box"):
            d = float(t["box"][0]) + float(t["box"][2]) / 2.0 - W / 2.0
            if abs(d) > CARD_CENTRE_PX:
                bad.append((t, d))
    if bad:
        _chk(raw, "end_card", [c0], [t["id"] for t, _ in bad], "card text off the frame centre (x %d, where a centred "
             "logo sits): %s" % (int(W / 2), ", ".join("%s centres at x %.0f, %.0f px %s" % (
                 t["id"], W / 2.0 + d, abs(d), "left" if d < 0 else "right") for t, d in bad)),
             "give the card's titles style end_card (the name) or cta (the call to action): they centre on x %d; "
             "other styles centre on the safe box" % int(W / 2))


def pace_decel_checks(edl, P, words_tl, shots, raw):
    """pace_decel: a music-led piece under 30 s (music.use required, or music and no dialogue) whose shots after half
    the length (the last shot aside) run a median 1.4 times or more the median of the shots before. P1's short-piece
    rule: from 30 s on, a slower close (a bridge, a breathing shot, a quiet ending) is the playbooks' own shape."""
    f = float(tl_fps(edl))
    N = programme_frames(edl)
    if N / f >= PACE_DECEL_MAX_S - 1e-9:
        return
    roles = {tr.get("id"): tr.get("role") for tr in edl["tracks"].get("audio", [])}
    dia = [w for w in words_tl if roles.get(w.get("track")) == "dialogue"]
    has_music = any(is_media_item(it) for tr in edl["tracks"].get("audio", []) if tr.get("role") == "music"
                    for it in tr.get("items", []))
    led = str((P.get("music") or {}).get("use")) == "required" or (not dia and has_music)
    if not led or len(shots) < 6:
        return
    half = N / 2.0
    early = [b - a for a, b in shots if a < half]
    late = [b - a for a, b in shots[:-1] if a >= half]
    if len(early) < 2 or not late:
        return
    me, ml = float(np.median(early)) / f, float(np.median(late)) / f
    if ml >= PACE_DECEL_RATIO * me - 1e-9:
        at = [a for a, b in shots[:-1] if a >= half][0]
        _chk(raw, "pace_decel", [at], [], "the cutting slows down: the shots after %.1f s run a median %.2f s against "
             "%.2f s before it (x%.1f); a music piece builds into its payoff" % (half / f, ml, me, ml / max(me, 1e-6)),
             "cut the second half at least as fast as the first (fewer beats per shot after %.1f s, the densest run "
             "just before the payoff); only the final shot may open up, about a bar" % (half / f))


def _runs(mask, join=0):
    """[(start, end)) index runs of True, joining runs split by up to `join` False samples."""
    out = []
    k, n = 0, len(mask)
    while k < n:
        if mask[k]:
            j = k
            while j < n and mask[j]:
                j += 1
            if out and k - out[-1][1] <= join:
                out[-1] = (out[-1][0], j)
            else:
                out.append((k, j))
            k = j
        else:
            k += 1
    return out


def music_energy_checks(edl, lab, P, env, raw):
    """music_lull and music_open_quiet from the preview's music bus (audio_env.npz, 100 Hz). The reference is the
    median level (0.4 s energy average) where music plays at its open level with no dialogue within 0.3 s, outside
    the first 0.5 s, the fades and the ducks. A lull is a run of max(1.5 s, one bar) under it by 8 dB, or the 2 s
    before the last music fade-out under it by 6 dB (the piece ends in a lull); a quiet open is music from frame 0
    with no dialogue in the first 0.5 s, averaging 8 dB under it. Ducked frames (volume_env) count nowhere: a duck
    is the mix's choice, not the song's energy."""
    if env is None or "role_music" not in env:
        return
    f = float(tl_fps(edl))
    N = programme_frames(edl)
    m = np.asarray(env["role_music"], np.float64)
    nw = len(m)
    if nw < 100:
        return
    d = np.asarray(env["role_dialogue"], np.float64)[:nw] if "role_dialogue" in env else np.full(nw, -120.0)
    if len(d) < nw:
        d = np.concatenate([d, np.full(nw - len(d), -120.0)])
    t = (np.arange(nw) + 0.5) / 100.0
    dia = d > -45.0
    near_dia = np.convolve(dia.astype(np.float64), np.ones(61), mode="same") > 0 if dia.any() else dia
    its = [it for tr in edl["tracks"].get("audio", []) if tr.get("role") == "music" for it in tr.get("items", [])
           if is_media_item(it) and it.get("enabled", True) is not False]
    if not its:
        return
    cover = np.zeros(nw, np.int32)
    fades = np.zeros(nw, bool)
    ducked = np.zeros(nw, bool)
    for it in its:
        r0, r1 = int(it["rec_in"]) / f, int(it["rec_out"]) / f
        sel = (t >= r0) & (t < r1)
        cover[sel] += 1
        fades |= sel & ((t < r0 + int(it.get("fade_in", 0)) / f) | (t >= r1 - int(it.get("fade_out", 0)) / f))
        ve = it.get("volume_env") or []
        if len(ve) >= 2:
            xs = np.array([float(p[0]) for p in ve])
            vs = np.array([float(p[1]) for p in ve])
            vals = np.interp(t[sel] * f - int(it["rec_in"]), xs, vs)
            ducked[np.flatnonzero(sel)[vals < vs.max() - 1.0]] = True
    present = m > -90.0
    base = (cover == 1) & ~fades & ~ducked & present
    e = np.power(10.0, m / 10.0)
    w = 40
    num = np.convolve(np.where(base, e, 0.0), np.ones(w), mode="same")
    den = np.convolve(base.astype(np.float64), np.ones(w), mode="same")
    lvl = np.full(nw, np.nan)
    ok = den >= w / 2.0
    lvl[ok] = 10 * np.log10(num[ok] / den[ok] + 1e-12)
    refm = base & ~near_dia & (t >= 0.5) & ok
    if refm.sum() < 100:
        return
    ref = float(np.median(lvl[refm]))
    bar = LULL_MIN_S
    mu = edl.get("music") or {}
    if mu.get("beats") and lab is not None:
        bt = load_json_or(lab.rel(mu["beats"]), None) or {}
        try:
            bpm = float(bt.get("bpm") or 0)
        except (TypeError, ValueError):
            bpm = 0.0
        if bpm > 0:
            bar = max(bar, 4 * 60.0 / bpm)
    # music_open_quiet: a quiet first half second (a pickup, a filtered intro) before the body
    first = min(its, key=lambda x: int(x["rec_in"]))
    # the open and the ending judge the song's own energy: frames the duck holds down (volume_env, the assembler's
    # duck pre-roll before an early first word, or a duck held into the tail) are left out, as in the body
    sel0 = (t < 0.5) & ~ducked
    if int(first["rec_in"]) == 0 and not dia[t < 0.5].any() and sel0.sum() >= 20:
        mean0 = 10 * math.log10(float(e[sel0].mean()) + 1e-12)
        if mean0 < ref - LULL_DB:
            sm = np.convolve(e, np.ones(10) / 10.0, mode="same")
            q = (10 * np.log10(sm + 1e-12) < ref - LULL_DB) & ~ducked
            end0 = next((k for k in range(nw) if not q[k]), nw) / 100.0
            _chk(raw, "music_open_quiet", [0], [first["id"]], "the music opens %.0f dB under its body level for the "
                 "first %.2f s (0 to %.2f s): the first second is a lull, not a hit" % (
                     ref - mean0, max(0.5, end0), max(0.5, end0)),
                 "start the music on the drop or a full bar (in_s at the body; a pickup of 2 frames at most), or put "
                 "the voice or a hit on the first frame")
    # body lulls: every window 6 dB or more under the body, the run 8 dB under on average
    cand = base & ~near_dia & ok & (lvl < ref - LULL_END_DB) & (t >= 0.5)
    for a, b in _runs(cand, join=10):
        if (b - a) / 100.0 < bar - 1e-6:
            continue
        sel_ = np.zeros(nw, bool)
        sel_[a:b] = True
        sel_ &= base
        drop = ref - 10 * math.log10(float(e[sel_].mean()) + 1e-12) if sel_.any() else 0.0
        if drop >= LULL_DB - 1e-9:
            _chk(raw, "music_lull", [rnd(a / 100.0 * f)], [], "the music drops %.0f dB under its body level for %.1f s "
                 "at %.2f-%.2f s with no voice over it: the energy dies there" % (
                     drop, (b - a) / 100.0, a / 100.0, b / 100.0),
                 "move the music (in_s) so a full section plays there, cut that stretch shorter, or put a line of "
                 "voice over it; a stop for the offer is one bar at most")
    # the ending: the 2 s before the last music fade-out (or the programme end)
    last = max(its, key=lambda x: int(x["rec_out"]))
    end_t = min(N / f, (int(last["rec_out"]) - int(last.get("fade_out", 0))) / f)
    sel = (t >= end_t - 2.0) & (t < end_t) & (cover >= 1) & ~near_dia & present & ~ducked
    if sel.sum() >= 100:
        mean_e = 10 * math.log10(float(e[sel].mean()) + 1e-12)
        if mean_e < ref - LULL_END_DB:
            sm = np.convolve(e, np.ones(20) / 20.0, mode="same")
            q = (10 * np.log10(sm + 1e-12) < ref - LULL_END_DB) & ~ducked
            k = min(nw - 1, int(end_t * 100) - 1)
            while k > 0 and q[k - 1] and t[k - 1] >= 0.5:
                k -= 1
            _chk(raw, "music_lull", [rnd(t[k] * f)], [last["id"]], "the piece ends in a lull: the music runs %.0f dB "
                 "under its body level from %.2f s to the end (%.2f s, its fade starts at %.2f s)" % (
                     ref - mean_e, t[k], N / f, end_t),
                 "end on a hit or the song's own ending at full energy: move the music (in_s, or \"align\": \"end\" "
                 "on a strong ending) so its last bars are a full section, or end the piece earlier")


def vo_checks(edl, P, words_tl, raw):
    """vo_gap: consecutive spoken lines further apart than the preset's pause (pause_keep_ms) plus 0.35 s, 0.8 s at
    least, 0.4 s more over a lift; ending_tail: more than 1.2 s after the last word with no card over it.
    Voice-led pieces only: a music-led piece (music.use required, pace_decel's rule) is left alone unless its
    dialogue covers half the programme or more. Between two lines of on-camera speech (not a narration file), a gap
    that the music carries undimmed (no duck) for MUSIC_INTERLUDE_S or more is a music interlude (an interview's
    B-roll break), not a hole, and so is such a tail in a piece of LONG_FORM_S or more (an outro); narration keeps
    the voice-over rule."""
    f = float(tl_fps(edl))
    N = programme_frames(edl)
    roles = {tr.get("id"): tr.get("role") for tr in edl["tracks"].get("audio", [])}
    sp = sorted([w for w in words_tl if roles.get(w.get("track")) == "dialogue"], key=lambda w: float(w["t0"]))
    if not sp:
        return
    a_items = {it["id"]: it for tr in edl["tracks"].get("audio", []) if tr.get("role") != "music"
               for it in tr.get("items", [])}
    if str((P.get("music") or {}).get("use")) == "required":
        said = [a_items[i] for i in {w.get("item") for w in sp} if i in a_items]
        if len(_frames_union(said, 0, N)) < 0.5 * max(1, N):
            return          # a music-led piece built from speech bites (vows, a speech in a music video)

    def narration(w):
        return (edl["media"].get(w.get("media") or (a_items.get(w.get("item")) or {}).get("media")) or {}).get(
            "kind") == "audio"
    keep = (P.get("speech") or {}).get("pause_keep_ms") or [150, 450]
    Lp = max(0.8, float(keep[-1]) / 1000.0 + 0.35)
    titles = [t for _, t in _titles_of(edl)]
    music = [it for tr in edl["tracks"].get("audio", []) if tr.get("role") == "music" for it in tr.get("items", [])
             if is_media_item(it) and it.get("enabled", True) is not False]
    lifts = []
    for it in music:
        dk = it.get("duck") if isinstance(it.get("duck"), dict) else {}
        for x in dk.get("lifts") or []:
            try:
                lifts.append((int(x[1]), int(x[2])))
            except (TypeError, ValueError, IndexError):
                continue

    def covered(a, b, its):
        A, B = rnd(a * f), rnd(b * f)
        return len(_frames_union(its, A, B)) / float(max(1, B - A))

    def ducked(a, b):
        A, B = rnd(a * f), rnd(b * f)
        for it in music:
            ve = it.get("volume_env") or []
            if len(ve) < 2 or int(it["rec_in"]) > A or int(it["rec_out"]) < B or B <= A:
                continue
            vs = [float(p[1]) for p in ve]
            vals = np.interp(np.arange(A, B) - int(it["rec_in"]), [float(p[0]) for p in ve], vs)
            if vals.max() < max(vs) - 1.0:
                return True
        return False

    def interlude(a, b):
        """True when the music plays undimmed (within 1 dB of its own top level) for MUSIC_INTERLUDE_S or more
        between a and b."""
        A, B = max(0, rnd(a * f)), min(N, rnd(b * f))
        if B - A < MUSIC_INTERLUDE_S * f:
            return False
        up = np.zeros(B - A, dtype=bool)
        for it in music:
            r0, r1 = max(A, int(it["rec_in"])), min(B, int(it["rec_out"]))
            if r1 <= r0:
                continue
            ve = it.get("volume_env") or []
            if len(ve) >= 2:
                vs = [float(p[1]) for p in ve]
                vals = np.interp(np.arange(r0, r1) - int(it["rec_in"]), [float(p[0]) for p in ve], vs)
                up[r0 - A:r1 - A] |= vals >= max(vs) - 1.0
            else:
                up[r0 - A:r1 - A] = True
        return int(up.sum()) >= MUSIC_INTERLUDE_S * f - 1e-9
    for w, x in zip(sp, sp[1:]):
        same = w["item"] == x["item"]
        if not same:
            ia, ib = a_items.get(w["item"]), a_items.get(x["item"])
            same = bool(ia and ib and int(ia["rec_out"]) == int(ib["rec_in"]))
        if same:
            continue          # a pause inside a line: pause_long judges it
        a, b = float(w["t1"]), float(x["t0"])
        gap = b - a
        # a lift (a picture-only segment marked "lift") under half the gap or more makes it a musical breath
        lifted = any(min(l1, b * f) - max(l0, a * f) >= 0.5 * gap * f for l0, l1 in lifts)
        lim = Lp + (VO_GAP_LIFT_S if lifted else 0.0)
        if round(gap, 2) < round(lim, 2):
            continue          # (the limit itself is too long: word times are good to about 10 ms)
        if titles and covered(a, b, titles) >= 0.8:
            continue          # a super carries the stretch (message_gap judges it)
        if not (narration(w) or narration(x)) and interlude(a, b):
            continue          # on-camera speech with the music up between the lines: a music interlude
        hole = (not lifted) and ducked(a, b)
        _chk(raw, "vo_gap", [rnd(a * f)], [w["item"], x["item"]], "%.2f s with no voice between %r (%.2f s) and %r "
             "(%.2f s)%s (a voice piece keeps 0.3 to 0.6 s between lines; this one must stay under %.2f s)" % (
                 gap, str(w.get("w", "")).strip(), a, str(x.get("w", "")).strip(), b,
                 ": the bed stays ducked through it, a ducked hole" if hole else
                 (" over a lifted musical breath" if lifted else ""), lim),
             "shorten the picture-only stretch between the lines or start the next line earlier, to about 0.5 s%s" % (
                 "; a lift is for a real musical moment and stays under about %.1f s" % lim if lifted else
                 "; if the pause is meant, lift the music there (\"lift\": true on the picture-only segment)" if hole
                 else ""))
    wl = max(sp, key=lambda w: float(w["t1"]))
    last = float(wl["t1"])
    tail = N / f - last
    if tail > TAIL_MAX_S + 0.5 / f and (narration(wl) or N / f < LONG_FORM_S or not interlude(last, N / f)):
        stills = [it for it in sorted_items(v1_track(edl)) if is_media_item(it)
                  and (edl["media"].get(it.get("media")) or {}).get("kind") == "image"]
        if covered(last, N / f, titles + stills) < 0.8:
            _chk(raw, "ending_tail", [N], [], "the programme runs %.2f s past the last word (%.2f s) with no end card "
                 "or title over it" % (tail, last),
                 "end 0.5 to 1.0 s after the last word (trim the last picture), or put the end card (a still or the "
                 "titles) over the tail")


def cue_line_y(c):
    """A caption's first-line centre in pixels (its box centre when the cue has no font size)."""
    b = c.get("box") or [0, 0, 0, 0]
    fpx = c.get("font_px")
    if not fpx:
        return float(b[1]) + float(b[3]) / 2.0
    lk = c.get("look") or {}
    return float(b[1]) + float(fpx) * look_pad(lk)[1] + float(fpx) * float(lk.get("line_spacing") or 1.25) / 2.0


def caption_jump_checks(edl, raw):
    """caption_jump: caption lines at more than 2 heights (3 % of the frame apart), or a height change more often
    than once per 6 s of captioned time."""
    f = float(tl_fps(edl))
    H = float(edl["timeline"]["height"])
    cues = sorted([c for tr in edl["tracks"].get("subtitle", []) for c in tr.get("items", []) if c.get("box")],
                  key=lambda c: int(c["rec_in"]))
    if len(cues) < 2:
        return
    ys = [cue_line_y(c) for c in cues]
    tol = CAPTION_JUMP_TOL * H
    starts = []
    for y in sorted(ys):
        if not starts or y - starts[-1][0] > tol:
            starts.append([y, [y]])
        else:
            starts[-1][1].append(y)
    centres = [float(np.mean(v)) for _, v in starts]

    def band(y):
        return min(range(len(centres)), key=lambda k: abs(centres[k] - y))
    bands = [band(y) for y in ys]
    changes = [k for k in range(1, len(cues)) if bands[k] != bands[k - 1]]
    capt = sum(rec_len(c) for c in cues) / f
    # one move (to clear a face) is always allowed; more than one change per 6 s of captions hunts
    if len(centres) > 2 or len(changes) > max(1.0, capt / CAPTION_JUMP_EVERY_S) + 1e-9:
        _chk(raw, "caption_jump", [int(cues[changes[0]]["rec_in"])] if changes else [int(cues[0]["rec_in"])],
             [cues[k]["id"] for k in changes[:6]], "the captions sit at %d heights (%s %% of the height) and change "
             "height %d time%s in %.1f s of captions: the eye has to hunt for them" % (
                 len(centres), ", ".join("%.0f" % (100 * c_ / H) for c_ in centres), len(changes),
                 "" if len(changes) == 1 else "s", capt),
             "keep one caption band (the preset's captions.y_band) and at most one other height: caption_y only to "
             "clear a face, never per shot (assemble snaps caption_y to two bands)")


def type_system_checks(edl, raw, P=None, logos=None):
    """type_system: the hook title smaller than a later title outside the card; outlined type (stroke over 0.05 em)
    on a premium piece; more than 3 text looks outside the card when the cut list chose some of them. The preset's
    own role map (the look of each title style in use, and the caption look) is one type system: looks that come
    from it never fire the count, only a look the cut list put on top of it."""
    f = float(tl_fps(edl))
    c0 = card_start(edl, logos)
    on_card = {t["id"] for t in card_titles(edl, c0)} if c0 is not None else set()
    tts = [t for _, t in _titles_of(edl)]
    body = [t for t in tts if t["id"] not in on_card]
    caps = [c for tr in edl["tracks"].get("subtitle", []) for c in tr.get("items", [])]
    for h in [t for t in body if t.get("role") == "hook" and t.get("font_px")]:
        big = [t for t in body if t is not h and int(t["rec_in"]) > int(h["rec_in"]) and t.get("font_px")
               and float(t["font_px"]) > float(h["font_px"]) + 0.05]
        if big:
            b = max(big, key=lambda t: float(t["font_px"]))
            _chk(raw, "type_system", [int(h["rec_in"])], [h["id"], b["id"]], "the hook %s (%g px) is smaller than the "
                 "later title %s (%g px): the most important text should be the largest" % (
                     h["id"], float(h["font_px"]), b["id"], float(b["font_px"])),
                 "make the hook the largest text before the card (hook_top at least the supers' font_px), or the "
                 "later supers smaller")
            break
    if (edl.get("fx_meta") or {}).get("premium") is True:
        outl = {}
        for it in tts + caps:
            lk = it.get("look") or {}
            st = lk.get("stroke") if isinstance(lk.get("stroke"), dict) else None
            if st and float(st.get("em") or 0) > 0.05 + 1e-9:
                outl.setdefault(lk.get("id") or "?", []).append(it["id"])
        if outl:
            _chk(raw, "type_system", [], [v[0] for v in outl.values()], "a premium piece with outlined type: %s" % (
                ", ".join("look %s (%d item%s)" % (k, len(v), "" if len(v) == 1 else "s") for k, v in sorted(outl.items()))),
                 "use the clean looks (hook_clean, super_clean, caption_soft, cta_card): a stroke only where "
                 "text_contrast or a busy background asks for one")
    ids = sorted({(it.get("look") or {}).get("id") for it in body + caps if (it.get("look") or {}).get("id")})
    mapped = set()
    if P is not None:
        pt = P.get("titles") or {}
        for t in body:
            lk_ = (pt.get(t.get("style")) or {}).get("look")
            if lk_:
                mapped.add(lk_)
        if caps:
            pc = P.get("captions") or {}
            if pc.get("look"):
                mapped.add(pc["look"])
            if any(((c.get("anim") or {}).get("id")) == "clean_box" for c in caps):
                mapped.add("caption_clean_box")          # assemble's own box look for clean_box captions
    extra = [i for i in ids if i not in mapped] if P is not None else ids
    if len(ids) > 3 and extra:
        _chk(raw, "type_system", [], [], "%d text looks outside the card (%s), %s on top of the preset's own (%s): "
             "one title look per role and one caption look make one type system" % (
                 len(ids), ", ".join(ids), ", ".join(extra) if P is not None else "all",
                 ", ".join(sorted(mapped)) or "none"),
             "drop the looks the cut list added (%s): give those titles the preset's look for their style, or the "
             "style whose look you want" % ", ".join(extra))


def recycled_half_checks(edl, lab, pieces, raw):
    """recycled_half: in a programme of 12 s or more, over 35 % of the second half's picture (stills aside) shows
    media already seen in the first half (callback and repeat_ok items aside). A talking head is not recycled
    footage: items with words or a linked sync sound, and media whose transcript speaks, count on neither side of
    the share (repeat_setup's rule), so a presenter, interview or podcast camera cut into many segments never
    fires it."""
    f = float(tl_fps(edl))
    N = programme_frames(edl)
    if N / f < 12.0:
        return
    half = N / 2.0
    words_of = _edl_words(edl, lab)
    speaks_ = {}

    def talks(obj):
        if obj.get("words") or obj.get("link"):
            return True
        mid = obj.get("media")
        if mid not in speaks_:
            try:
                speaks_[mid] = sum(1 for w in words_of(mid) if "low_conf" not in (w.get("tags") or [])) >= 3
            except (Fail, OSError, ValueError):
                speaks_[mid] = False
        return speaks_[mid]
    first_media, first_ids = set(), set()
    for kind, r0, r1, obj in pieces:
        if kind == "seg" and r0 < half:
            first_media.add(obj.get("media"))
            first_ids.add(obj.get("id"))
    den, num, reused = 0, 0, {}
    for kind, r0, r1, obj in pieces:
        if kind != "seg" or r1 <= half:
            continue
        a = max(r0, int(math.ceil(half)))
        if r1 <= a:
            continue
        if (edl["media"].get(obj.get("media")) or {}).get("kind") == "image":
            continue
        if talks(obj):
            continue
        den += r1 - a
        if obj.get("id") in first_ids or obj.get("media") not in first_media:
            continue
        if {"callback", "repeat_ok"} & set(obj.get("tags") or []):
            continue
        num += r1 - a
        reused.setdefault(obj.get("media"), []).append(obj.get("id"))
    if den <= 0 or num / float(den) <= RECYCLED_SHARE + 1e-9:
        return
    used = [it for _, it in visible_video(edl) if is_media_item(it)]
    unused = []
    sl_ = load_json_or(lab.p("shotlog.json"), None) if lab is not None else None
    for s in (sl_ or {}).get("shots") or []:
        sc = s.get("score") if isinstance(s, dict) else None
        if not isinstance(sc, (int, float)) or isinstance(sc, bool) or sc < 6:
            continue
        try:
            mid_, a_, b_ = _shot_lookup(lab, s.get("shot"))
        except Fail:
            continue
        if not any(it["media"] == mid_ and int(it.get("src_in", 0)) < b_ and a_ < int(it.get("src_out", 0)) for it in used):
            unused.append((sc, s["shot"]))
    unused = [s_ for _, s_ in sorted(unused, key=lambda x: (-x[0], x[1]))[:5]]
    _chk(raw, "recycled_half", [int(math.ceil(half))], [i for v in reused.values() for i in v],
         "%.0f %% of the second half's picture repeats media from the first half (%s)" % (
             100.0 * num / den, ", ".join("%s in %s" % (k, ", ".join(v)) for k, v in sorted(reused.items()))),
         "use fresh setups in the second half%s; tag a deliberate callback \"callback\"" % (
             (" (unused shots the log scores 6 or more: %s)" % ", ".join(unused)) if unused else ""))


def title_repeat_checks(edl, raw):
    """title_repeat: two titles with the same words (letters and digits), neither a callback, role not brand or cta
    (the name at the start and on the card, and the call to action before the card and on it, are meant twice)."""
    f = float(tl_fps(edl))
    seen = {}
    for _, t in _titles_of(edl):
        if t.get("role") in ("brand", "cta") or "callback" in (t.get("tags") or []):
            continue
        key = re.sub(r"[^a-z0-9]", "", str(t.get("text", "")).lower())
        if not key:
            continue
        if key in seen:
            a = seen[key]
            _chk(raw, "title_repeat", [int(t["rec_in"])], [a["id"], t["id"]], "titles %s (%.2f s) and %s (%.2f s) say "
                 "the same thing: %r" % (a["id"], int(a["rec_in"]) / f, t["id"], int(t["rec_in"]) / f,
                                         one_line(t.get("text", ""))),
                 "say it once (the offer once, as a super), and give the other moment a new proof point; tag a "
                 "deliberate repeat \"callback\"")
        else:
            seen[key] = t


def _rel_lum(rgb):
    c = np.asarray(rgb, np.float64) / 255.0
    c = np.where(c <= 0.04045, c / 12.92, np.power((c + 0.055) / 1.055, 2.4))
    return c[..., 0] * 0.2126 + c[..., 1] * 0.7152 + c[..., 2] * 0.0722


def text_contrast_checks(edl, rdir, raw):
    """text_contrast: a title or caption whose look has no box and a stroke of 0.05 em or less, over a background
    (the ring around its box on the preview frame at its middle) with a WCAG contrast under 3:1 against its fill."""
    st = preview_state(rdir) if rdir else None
    prev = os.path.join(rdir, "preview.mov") if rdir else None
    if not st or st.get("result") != "OK" or not prev or not os.path.exists(prev) or not st.get("raster"):
        return
    f = float(tl_fps(edl))
    W, H = float(edl["timeline"]["width"]), float(edl["timeline"]["height"])
    pw, ph = int(st["raster"][0]), int(st["raster"][1])
    its = [t for _, t in _titles_of(edl)] + [c for tr in edl["tracks"].get("subtitle", []) for c in tr.get("items", [])]
    want = []
    for it in its:
        lk = it.get("look") or {}
        stk = lk.get("stroke") if isinstance(lk.get("stroke"), dict) else None
        if not it.get("box") or lk.get("box") or (stk and float(stk.get("em") or 0) > 0.05 + 1e-9):
            continue
        want.append((it, (int(it["rec_in"]) + int(it["rec_out"])) // 2))
    if not want:
        return
    imgs = grab(prev, [fr for _, fr in want], pw, ph)
    sx, sy = pw / W, ph / H
    for it, fr in want:
        img = imgs.get(fr)
        if img is None:
            continue
        x, y, w_, h_ = [float(v) for v in it["box"]]
        g = max(2.0, 0.25 * float(it.get("font_px") or 40))
        X0, Y0 = int(max(0, (x - g) * sx)), int(max(0, (y - g) * sy))
        X1, Y1 = int(min(pw, (x + w_ + g) * sx)), int(min(ph, (y + h_ + g) * sy))
        x0, y0, x1, y1 = int(x * sx), int(y * sy), int(math.ceil((x + w_) * sx)), int(math.ceil((y + h_) * sy))
        if X1 - X0 < 3 or Y1 - Y0 < 3:
            continue
        msk = np.ones((Y1 - Y0, X1 - X0), bool)
        msk[max(0, y0 - Y0):max(0, y1 - Y0), max(0, x0 - X0):max(0, x1 - X0)] = False
        ring = img[Y0:Y1, X0:X1][msk]
        if len(ring) < 8:
            continue
        lb = float(np.median(_rel_lum(ring)))
        col = _hex_rgb((it.get("look") or {}).get("color") or "#FFFFFF", (255, 255, 255))
        lf = float(_rel_lum(np.array(col[:3], np.float64)))
        ratio = (max(lb, lf) + 0.05) / (min(lb, lf) + 0.05)
        if ratio < CONTRAST_MIN:
            _chk(raw, "text_contrast", [fr], [it["id"]], "%s %s reads at a contrast of %.1f:1 against the picture "
                 "around it at %.2f s (%s text, no box, %s): under 3:1 it washes out" % (
                     "title" if it.get("kind") == "title" else "caption", it["id"], ratio, fr / f,
                     (it.get("look") or {}).get("color") or "#FFFFFF",
                     "a thin stroke" if (it.get("look") or {}).get("stroke") else "no stroke"),
                 "move it over a darker part of the frame, or give it a look with a box or a pill (cta_card, "
                 "caption_clean_box), or a darker scrim on that shot")


def final_checks(edl, lab, P, words_tl, pieces, cuts, shots, raw, rdir=None, preview=None):
    """The final fix pass's checks (title_blink, end_card, pace_decel, music_lull, music_open_quiet, vo_gap,
    ending_tail, caption_jump, type_system, recycled_half, title_repeat, text_contrast)."""
    title_blink_checks(edl, raw)
    try:
        logos = fx_lab.brand_logo_media(edl, lab)
    except Exception:
        logos = set()
    end_card_checks(edl, raw, logos)
    pace_decel_checks(edl, P, words_tl, shots, raw)
    vo_checks(edl, P, words_tl, raw)
    caption_jump_checks(edl, raw)
    type_system_checks(edl, raw, P, logos)
    recycled_half_checks(edl, lab, pieces, raw)
    title_repeat_checks(edl, raw)
    if preview and rdir:
        envf = os.path.join(rdir, "audio_env.npz")
        env = None
        if os.path.exists(envf):
            try:
                env = dict(np.load(envf))
            except (OSError, ValueError):
                env = None
        music_energy_checks(edl, lab, P, env, raw)
        try:
            text_contrast_checks(edl, rdir, raw)
        except (Fail, OSError, ValueError):
            pass


def compute_checks(edl, lab, P, preview=None, words_tl=None, pieces=None, rdir=None):
    """All B gates of the contract -> (checks with levels, stats, notes)."""
    tfps = tl_fps(edl)
    f = float(tfps)
    N = programme_frames(edl)
    raw, notes = [], []
    errs, warns = validate(edl, lab)
    for e in errs:
        cid = "offline_media" if e["code"] in ("missing_file", "hash_changed") else "edl_invalid"
        fix = {"missing_file": "if the file moved, run `M add <its new folder or path>` (the lab finds it again by its "
                               "content and keeps its id), then assemble again; if it was deleted, restore it",
               "hash_changed": "run `M ingest` (the changed file is analysed again), then assemble again"}.get(
            e["code"], "fix the EDL and assemble again")
        _chk(raw, cid, [], [e["item"]] if e["item"] else [], "%s: %s" % (e["code"], e["msg"]), fix)
    notes += ["%s: %s" % (w["code"], w["msg"]) for w in warns]
    if pieces is None:
        pieces, fn = flatten(edl, N, lab=lab, shots=True)
        notes += fn
    cuts, shots = cuts_and_shots(edl, pieces, N)
    ov_changes = overlay_changes(edl, lab, N)
    durs = np.array([b - a for a, b in shots], np.float64) / f if shots else np.array([])
    words_tl = timeline_words(edl, lab) if words_tl is None else words_tl
    items_by_id = {it["id"]: it for k, tr, it in iter_items(edl)}
    loudness_doc = None
    if preview:
        for p in preview.get("problems") or []:
            _chk(raw, "preview_frames", [], [], p, "run preview again; if it repeats, delete LAB/cache/chunks")
        au = preview.get("audio") or {}
        for t0, t1 in au.get("silence_gaps") or []:
            _chk(raw, "silence_gap", [rnd(t0 * f)], [], "the mix is below -60 dBFS for %.2f s at %.2f-%.2f s inside the "
                 "dialogue" % (t1 - t0, t0, t1), "close the gap, add room tone or music under it")
        pk = au.get("peak_dbfs")
        if pk is not None and float(pk) > -1.0:
            # a few dB over is a mix to tame; far over 0 dBFS is nearly always a gain typed as +dB instead of -dB
            _chk(raw, "mix_peak", [], [], "the preview mix peaks at %+.1f dBFS%s; delivery allows -1 dBTP at most" % (
                float(pk), " and clips" if float(pk) > 0 else ""),
                "lower the gain_db of the loudest items by %.0f dB or more (a gain typed as +dB instead of -dB is the "
                "usual cause), then preview again" % max(1.0, math.ceil(float(pk) + 1.0)),
                level="STOP" if float(pk) > MIX_PEAK_STOP_DBFS else None)
        gap = au.get("speech_music_gap_lu")
        lu = float((P.get("music") or {}).get("duck_lu", 14))
        if gap is not None and gap < lu - 0.5:
            _chk(raw, "speech_music_gap", [], [], "music sits only %.1f LU under the voice (target %g)" % (gap, lu),
                 "lower the music gain or deepen the duck by %.0f dB" % (lu - gap))
        elif gap is not None and gap > lu + BED_BURIED_LU:
            # two-sided: a bed far under the voice is buried (no drive, no low end under the lines)
            _chk(raw, "speech_music_gap", [], [], "music sits %.1f LU under the voice, more than %g LU over the "
                 "target %g: the bed is buried" % (gap, BED_BURIED_LU, lu), buried_fix(edl, gap - lu))
        if "lufs_i" not in au and (au or preview.get("edl_hash")):
            # a fresh preview rendered before the loudness measure existed (same renderer, same EDL): measure its
            # sound now (about a second), so loudness_off never passes in silence
            mov_ = os.path.join(rdir, "preview.mov") if rdir else None
            li_, tp_ = measure_lufs(mov_) if mov_ and os.path.exists(mov_) else (None, None)
            if li_ is not None and math.isfinite(li_) and li_ >= -70:
                au = dict(au, lufs_i=round(li_, 2), tp_dbtp=None if tp_ is None or not math.isfinite(tp_)
                          else round(tp_, 2), mix_gain_db=mix_gain_of(edl))
                notes.append("loudness measured on the preview's sound (%.1f LUFS): the preview predates the "
                             "loudness measure" % li_)
            else:
                notes.append("loudness not measured: the preview has no loudness measure and its sound could not be "
                             "measured; run `E preview` (or review) again so loudness_off can judge the mix")
        loud_ = loudness_stats(P, au)
        if loud_ is not None:
            loudness_doc = loud_
            if abs(loud_["target"] - loud_["lufs_i"]) > loud_["tol_lu"]:
                tot_ = round(loud_["mix_gain_db"] + loud_["gain_more_db"], 1)
                _chk(raw, "loudness_off", [], [], "the preview mix measures %.1f LUFS integrated (true peak %s dBTP), "
                     "%.1f LU from the target %g LUFS%s" % (
                         loud_["lufs_i"], "?" if loud_["tp_dbtp"] is None else "%.1f" % loud_["tp_dbtp"],
                         abs(loud_["target"] - loud_["lufs_i"]), loud_["target"],
                         (" (mix gain %+.1f dB already in)" % loud_["mix_gain_db"]) if loud_["mix_gain_db"] else ""),
                     loudness_fix(loud_, tot_))
    # flash frames and black gaps
    for pk, (kind, r0, r1, obj) in enumerate(pieces):
        cont = kind == "seg" and any(
            0 <= q < len(pieces) and pieces[q][0] == "seg" and picture_continues(
                edl, *((pieces[q][3], obj, r0) if q < pk else (obj, pieces[q][3], r1))) for q in (pk - 1, pk + 1))
        if kind == "seg" and r1 - r0 < 3 and not cont and "intentional_flash" not in (obj.get("tags") or []):
            _chk(raw, "flash_frame", [r0], [obj["id"]], "%s is visible for only %d frame%s at %.2f s" % (
                obj["id"], r1 - r0, "" if r1 - r0 == 1 else "s", r0 / f), "remove it or extend it to at least 3 frames")
        elif kind == "seg" and r1 - r0 < 6 and not cont and "intentional_flash" in (obj.get("tags") or []):
            # a deliberate flash stays a choice, but a few frames on V1 still read as a glitch to many viewers
            _chk(raw, "flash_frame", [r0], [obj["id"]], "%s is a deliberate flash of %d frame%s at %.2f s (tagged "
                 "intentional_flash)" % (obj["id"], r1 - r0, "" if r1 - r0 == 1 else "s", r0 / f),
                 "make sure it reads as a choice (on a beat, a hit or a motion peak) and that several flashes do not "
                 "come within one second (photosensitivity)", level="WARN")
        if kind == "gap":
            _chk(raw, "black_gap", [r0], [], "%d frames without picture at %.2f-%.2f s" % (r1 - r0, r0 / f, r1 / f),
                 "close the gap or cover it with a clip or a solid")
    # words
    roles_ = {tr.get("id"): tr.get("role") for tr in edl["tracks"].get("audio", [])}
    for w in words_tl:
        if w["clipped"]:
            it = items_by_id.get(w["item"]) or {}
            nfix = int(math.ceil(w["cut_s"] * f / speed_of(it) - 1e-9)) if it else 1
            seg_ = re.sub(r"_\d+$", "", re.sub(r"a$", "", str(w["item"])))
            # a word in the camera sound under a picture segment (background chatter whisper picked up) or a word
            # whisper was unsure of is worth a listen, not a STOP
            nat = roles_.get(w.get("track")) == "nat"
            low = "low_conf" in (w.get("tags") or [])
            what = ("the camera sound of segment %s" % seg_) if nat else ("segment %s" % seg_)
            _chk(raw, "clipped_word", [rnd(w["t0"] * f)], [w["item"]], "%s (%s) %s inside the word %r (%.2f s cut off)%s" % (
                w["item"], what, "starts" if w["edge"] == "in" else "ends", w["w"], w["cut_s"],
                "; whisper was unsure of this word" if low else "; heard in the background" if nat else ""),
                ("listen: if the word is audible, move the cut on %s by %d frame%s, lower its gain_db or set \"audio\": "
                 "\"none\"" % (seg_, nfix, "" if nfix == 1 else "s")) if nat else
                "%s %s by %d frame%s" % ("extend" if w["edge"] == "out" else "start", seg_ if w["edge"] == "out"
                                          else seg_ + " earlier", nfix, "" if nfix == 1 else "s"),
                level="WARN" if nat or low else None)
    # one spoken word the transcript wrote as several parts (M script tags the later ones joined) plays whole or not
    # at all: a part of it alone is half a word in the voice while the captions show the whole word
    wcache_, heard_ = {}, {(w["media"], w.get("i")) for w in words_tl}
    for w in words_tl:
        mid_ = w["media"]
        if mid_ not in wcache_:
            wf_ = (edl["media"].get(mid_) or {}).get("words")
            wcache_[mid_] = ((load_words_file(lab.rel(wf_) if lab else wf_) if wf_ else None) or {}).get("words") or []
        W_, i_ = wcache_[mid_], w.get("i")
        if not isinstance(i_, int) or not 0 <= i_ < len(W_):
            continue
        h_ = joined_head(W_, i_)
        grp_ = [h_] + joined_parts(W_, h_)
        first_ = [k for k in grp_ if (mid_, k) in heard_]
        lost_ = [k for k in grp_ if (mid_, k) not in heard_]
        if len(grp_) > 1 and lost_ and first_ and first_[0] == i_:
            _chk(raw, "joined_split", [rnd(w["t0"] * f)], [w["item"]], "word %d %r is one spoken word with word%s %s, "
                 "but word%s %s %s not heard: the voice says part of it while the captions show all of it" % (
                     h_, W_[h_].get("w"), "s" if len(grp_) > 2 else "", ", ".join(str(k) for k in grp_[1:]),
                     "s" if len(lost_) > 1 else "", ", ".join(str(k) for k in lost_),
                     "are" if len(lost_) > 1 else "is"),
                 "run the word range over words %d to %d (assemble does this for a cut list), or drop word %d" % (
                     h_, grp_[-1], h_))
    # a source word heard twice in a row (two ranges that share a word, or a range moved back over a word the range
    # before it already plays) is a stutter the edit made
    recent_ = []
    for w in words_tl:
        if roles_.get(w.get("track")) == "nat" or not isinstance(w.get("i"), int):
            continue
        twice_ = [p_ for p_ in recent_ if p_["media"] == w["media"] and p_["i"] == w["i"] and
                  w["t0"] >= p_["t1"] - 0.010]
        if twice_:
            p_ = twice_[-1]
            _chk(raw, "word_twice", [rnd(w["t0"] * f)], [p_["item"], w["item"]], "word %d %r of %s is heard twice in a "
                 "row: in %s at %.2f s and in %s at %.2f s" % (w["i"], w["w"], w["media"], p_["item"], p_["t0"],
                                                                w["item"], w["t0"]),
                 "start the later word range after word %d (or end the earlier one before it); if the repeat is meant, "
                 "add word_twice to checks_off" % w["i"])
        recent_ = (recent_ + [w])[-3:]
    for wc in words_changed(edl, lab):
        _chk(raw, "words_changed", [wc["at"]], [wc["item"]], words_changed_msg(wc),
             "an edge moved into a neighbouring word (a beat snap, an audio lead, or a length Resolve cannot place when "
             "the media rate differs from the timeline): end the word range at a pause, lengthen the tail with pad_ms, "
             "shorten the audio lead or move the music, then assemble again")
    for k_, tr_, it_ in iter_items(edl, ("audio",)):
        et_ = it_.get("edge_tol") or {}
        why_ = et_.get("why") if isinstance(et_.get("why"), dict) else {}
        if not it_.get("words"):
            continue
        for side_ in ("in", "out"):
            tol_ = float(et_.get(side_) or 0.0)
            if tol_ <= 0.020:
                continue
            w0_, w1_ = int(it_["words"][0]), int(it_["words"][-1])
            pair_ = (w0_ - 1, w0_) if side_ == "in" else (w1_, w1_ + 1)
            at_ = int(it_["rec_in"] if side_ == "in" else it_["rec_out"])
            if why_.get(side_, "rate" if side_ == "out" else "abut") == "rate":
                what_ = "a %s fps clip on a %s fps timeline has no length that ends" % (
                    fps_label(media_fps(edl, it_["media"])), fps_label(tfps))
            else:
                what_ = "no %s fps frame edge lies within 20 ms of the boundary" % fps_label(media_fps(edl, it_["media"]))
            _chk(raw, "rate_edge", [at_], [it_["id"]], "%s: words %d and %d abut (no pause) and %s between them; up to "
                 "%d ms of a word is heard or lost at its %s point" % (it_["id"], pair_[0], pair_[1], what_,
                                                                      round(1000 * tol_), side_),
                 "listen at the cut; if it shows, %s the word range at a pause or cover the cut with B-roll" % (
                     "start" if side_ == "in" else "end"))
    for iid, at, msg, fix in frame_edge_issues(edl, lab):
        _chk(raw, "frame_edge", [at], [iid], msg, fix)
    # dialogue from a clip whose stereo track has the voice on one side (or two microphones, one per side)
    seen_ = {}
    for k_, tr_, it_ in iter_items(edl, ("audio",)):
        m_ = (edl["media"].get(it_.get("media")) or {})
        if tr_.get("role") != "music" and m_.get("stereo") and is_media_item(it_) and it_.get("enabled") is not False:
            seen_.setdefault(it_["media"], []).append(it_)
    for mid_, its_ in sorted(seen_.items()):
        kind_ = edl["media"][mid_]["stereo"]
        # the EDL keeps the media's state from when it was assembled: after a fix in Resolve and a new ingest, assemble
        # again before trusting this
        again_ = ("; if the clip was already fixed in Resolve and ingested again, assemble again")
        what_ = {"one_sided": "its sound in one ear only", "two_voices": "a different speaker in each ear"}.get(
            kind_, "a different microphone in each ear")
        if kind_ == "two_voices":
            fix_ = ("keep both channels (setting the clip to one channel drops a speaker); this version cannot centre "
                    "them: after the build ask the user to put these items on an audio track of their own and set its "
                    "Pan Spread to 1 (PNT) in the Fairlight mixer, then render")
        elif kind_ == "one_sided":
            fix_ = ("ask the user to set the clip to mono in Resolve (Clip Attributes > Audio: Format Mono, Source Channel "
                    "the one with the voice), dump, add --from-dump and ingest again; the preview then plays it on both "
                    "sides" + again_)
        else:
            fix_ = ("ask the user to listen to both channels and set the clip to mono from the better one in Resolve "
                    "(Clip Attributes > Audio: Format Mono, Source Channel that one), dump, add --from-dump and ingest "
                    "again" + again_)
        _chk(raw, "channel_balance", [int(its_[0]["rec_in"])], [x["id"] for x in its_],
             "%s plays %s (%d item%s): %s" % (mid_, what_, len(its_), "" if len(its_) == 1 else "s",
                                             ", ".join(x["id"] for x in its_[:6])), fix_)
    r_fps = project_fps(lab)
    if r_fps is not None and r_fps != tfps:
        _chk(raw, "fps_mismatch", [], [], fps_mismatch_msg(edl, r_fps), "re-run init after the dump, then assemble")
    remove = set((P.get("speech") or {}).get("remove") or [])
    clean_gap = float((P.get("speech") or {}).get("clean_gap_ms", 60)) / 1000.0
    prev_w = None
    for w in words_tl:
        tg = remove & set(w["tags"])
        # a repeat is a repeat of the word heard just before it on the timeline (the transcript's tag can be stale:
        # M script took its twin out as extra, or the cut list dropped the twin)
        if "repeat" in tg and (prev_w is None or _nw_(prev_w["w"]) != _nw_(w["w"])):
            tg.discard("repeat")
        if "joined" not in (w.get("tags") or []):
            prev_w = w
        if tg:
            fix = "drop word %s in the cut list" % w.get("i")
            wf = (edl["media"].get(w["media"]) or {}).get("words")
            W = ((load_words_file(lab.rel(wf) if lab else wf) if wf else None) or {}).get("words") or []
            k = w.get("i")
            if isinstance(k, int) and 0 <= k < len(W):
                gb = float(W[k]["t0"]) - float(W[k - 1]["t1"]) if k > 0 else 9.0
                ga = float(W[k + 1]["t0"]) - float(W[k]["t1"]) if k + 1 < len(W) else 9.0
                if min(gb, ga) < clean_gap:
                    fix = ("word %d runs into its neighbours (%.2f s / %.2f s gaps), so assemble keeps it even when "
                           "dropped: split the word range around it (end one segment at word %d, start the next at "
                           "word %d), cover it with B-roll, cut the phrase at a pause, or leave it in" % (
                               k, gb, ga, k - 1, k + 1))
            _chk(raw, "filler_left", [rnd(w["t0"] * f)], [w["item"]], "%s word %r (%s) is still in at %.2f s" % (
                w["item"], w["w"], ",".join(sorted(tg)), w["t0"]), fix)
    sp = P.get("speech") or {}
    kmax = float((sp.get("pause_keep_ms") or [150, 450])[-1]) / 1000.0
    hmax = float((sp.get("handoff_ms") or [400, 600])[-1]) / 1000.0
    a_items = {it["id"]: it for tr in edl["tracks"]["audio"] if tr.get("role") != "music" for it in tr.get("items", [])}
    for w, x in zip(words_tl, words_tl[1:]):
        same = w["item"] == x["item"]
        if not same:
            ia, ib = a_items.get(w["item"]), a_items.get(x["item"])
            same = bool(ia and ib and int(ia["rec_out"]) == int(ib["rec_in"]))
        if not same:
            continue
        gap = x["t0"] - w["t1"]
        lim = hmax if w["speaker"] != x["speaker"] else kmax
        if gap > lim + 1.0 / f:
            _chk(raw, "pause_long", [rnd(w["t1"] * f)], [w["item"], x["item"]], "pause of %.2f s after %r at %.2f s "
                 "(keep at most %.2f s)" % (gap, w["w"], w["t1"], lim), "cut %.2f s out of the pause" % (gap - lim))
    # hook
    hk = P.get("hook") or {}
    titles = [it for tr in edl["tracks"]["video"] for it in tr.get("items", []) if it.get("kind") == "title"]
    if hk.get("gate"):
        by_v, by_p = float(hk.get("visual_change_by_s", 3.0)), float(hk.get("proposition_by_s", 3.0))
        changes = ([c["frame"] for c in cuts] + [int(t["rec_in"]) for t in titles if int(t["rec_in"]) > 0]
                   + list(ov_changes))
        if not any(0 < c <= by_v * f for c in changes):
            at0 = any(int(t["rec_in"]) == 0 for t in titles)
            _chk(raw, "hook_3s", [0], [], "no cut or title starts in the first %.1f s%s" % (
                by_v, " (a title that is there from the first frame is not a change)" if at0 else ""),
                 "cut to a new shot, punch in (zoom 1.15 or more on a split segment), or bring a title in after the "
                 "opening frame and before %.1f s" % by_v)
        fw = words_tl[0]["t0"] if words_tl else None
        if not ((fw is not None and fw < by_p) or any(int(t["rec_in"]) < by_p * f for t in titles)):
            _chk(raw, "hook_3s", [0], [], "no word and no title in the first %.1f s" % by_p,
                 "open on the key line or put the proposition on screen")
    # captions and titles
    cap = P.get("captions") or {}
    min_s, cps = float(cap.get("min_s", 0.35)), cap.get("cps_max")
    maxc, maxl = int(cap.get("max_chars_line", 32)), int(cap.get("max_lines", 2))
    W, H = int(edl["timeline"]["width"]), int(edl["timeline"]["height"])
    sbox = safe_box(edl.get("platform") or P.get("platform"), W, H)

    def outside(box):
        x, y, w_, h_ = box
        return x < sbox[0] - 1 or y < sbox[1] - 1 or x + w_ > sbox[0] + sbox[2] + 1 or y + h_ > sbox[1] + sbox[3] + 1
    for tr in edl["tracks"].get("subtitle", []):
        for c in tr.get("items", []):
            d = rec_len(c) / f
            txt = str(c.get("text", ""))
            if d < min_s - 0.5 / f:
                _chk(raw, "caption_timing", [c["rec_in"]], [c["id"]], "caption %s lasts %.2f s (minimum %.2f s)" % (
                    c["id"], d, min_s), "give the words a longer tail (pad_ms on their segment) so the cue can "
                                         "stay up longer, or keep more words around it; assemble never leaves a lone "
                                         "word in a cue on purpose")
            elif cps and len(txt) / max(d, 1e-6) > float(cps):
                _chk(raw, "caption_timing", [c["rec_in"]], [c["id"]], "caption %s reads at %.1f characters per second "
                     "(max %g)" % (c["id"], len(txt) / d, float(cps)), "split or shorten it")
            lines = c.get("lines") or _wrap(txt, maxc)
            if len(lines) > maxl or any(len(l) > maxc for l in lines):
                _chk(raw, "caption_layout", [c["rec_in"]], [c["id"]], "caption %s has %d lines, longest %d characters "
                     "(max %d x %d)" % (c["id"], len(lines), max(len(l) for l in lines), maxl, maxc), "split the cue")
            elif c.get("font_px") and text_width(lines, float(c["font_px"])) > sbox[2] + 1:
                _chk(raw, "caption_layout", [c["rec_in"]], [c["id"]], "caption %s is about %d px wide at %g px, wider "
                     "than the %d px safe box" % (c["id"], text_width(lines, float(c["font_px"])), float(c["font_px"]),
                                                   int(sbox[2])), "split the cue or shorten it")
            if c.get("box") and outside(c["box"]):
                _chk(raw, "safe_zone", [c["rec_in"]], [c["id"]], "caption %s box %s leaves the %s safe box %s" % (
                    c["id"], c["box"], edl.get("platform"), [int(v) for v in sbox]), "move it into the safe box")
    for t in titles:
        txt = str(t.get("text", ""))
        need = max(0.833, len(txt) / 20.0, len(txt.split()) * 0.33)
        d = rec_len(t) / f
        if d < need - 0.5 / f:
            _chk(raw, "caption_timing", [t["rec_in"]], [t["id"]], "title %s is on screen %.2f s; reading it needs %.2f s"
                 % (t["id"], d, need), "hold it at least %d frames" % int(math.ceil(need * f)))
        st = (P.get("titles") or {}).get(t.get("style")) or {}
        mc = int(st.get("max_chars_line", 28))
        lines = t.get("lines") or _wrap(txt, mc)
        if len(lines) > 3 or any(len(l) > mc for l in lines):
            _chk(raw, "caption_layout", [t["rec_in"]], [t["id"]], "title %s has %d lines, longest %d characters" % (
                t["id"], len(lines), max(len(l) for l in lines)), "shorten the text")
        elif t.get("font_px") and text_width(lines, float(t["font_px"])) > sbox[2] + 1:
            _chk(raw, "caption_layout", [t["rec_in"]], [t["id"]], "title %s is about %d px wide, wider than the %d px "
                 "safe box" % (t["id"], text_width(lines, float(t["font_px"])), int(sbox[2])), "shorten the text")
        if t.get("box") and outside(t["box"]):
            _chk(raw, "safe_zone", [t["rec_in"]], [t["id"]], "title %s box %s leaves the safe box %s" % (
                t["id"], t["box"], [int(v) for v in sbox]), "move it into the safe box")
    # duration
    ls = P.get("length_s") or {}
    L = N / f
    tgt = (edl.get("targets") or {}).get("duration_frames")
    # an exact target the cut list sets (the user asked for 10 s) replaces the preset's length range
    if not tgt and ls.get("min") is not None and L < float(ls["min"]) - 0.5 / f:
        _chk(raw, "duration", [N], [], "length %.2f s is under the minimum %g s" % (L, float(ls["min"])),
             "add material, or set targets.duration_s in the cut list when the user asked for this length")
    if not tgt and ls.get("max") is not None and L > float(ls["max"]) + 0.5 / f:
        _chk(raw, "duration", [N], [], "length %.2f s is over the maximum %g s" % (L, float(ls["max"])),
             "cut %.1f s, or set targets.duration_s in the cut list when the user asked for this length" % (
                 L - float(ls["max"])))
    if tgt and int(tgt) != N:
        _chk(raw, "duration", [N], [], "length %d f differs from the target %d f" % (N, int(tgt)),
             "%s %d frames" % ("cut" if N > int(tgt) else "add", abs(N - int(tgt))))
    # music end
    mcfg = P.get("music") or {}
    for tr in edl["tracks"]["audio"]:
        if tr.get("role") != "music":
            continue
        for it in tr.get("items", []):
            if not is_media_item(it):
                continue
            fr = (edl["media"].get(it["media"]) or {}).get("frames")
            natural = fr is not None and int(it["src_out"]) >= int(fr) - 1
            if mcfg.get("end", "resolve") == "resolve" and int(it["rec_out"]) <= N and int(it.get("fade_out", 0)) < 12 \
                    and not natural:
                _chk(raw, "music_end", [int(it["rec_out"])], [it["id"]], "music %s stops at %.2f s with a %d frame fade and "
                     "not at the end of the song" % (it["id"], int(it["rec_out"]) / f, int(it.get("fade_out", 0))),
                     "fade it over at least 12 frames or end on the song's natural end")
    # audio fades
    atrans = {(x.get("from"), x.get("to")) for x in edl.get("transitions", [])}
    for tr in edl["tracks"]["audio"]:
        if tr.get("role") not in ("dialogue", "music", "nat"):
            continue
        its = sorted_items(tr)
        for a, b in zip(its, its[1:]):
            if int(a["rec_out"]) != int(b["rec_in"]) or (a["id"], b["id"]) in atrans:
                continue
            seamless = a.get("media") == b.get("media") and int(a.get("src_out", -1)) == int(b.get("src_in", -2))
            if not seamless and (int(a.get("fade_out", 0)) == 0 or int(b.get("fade_in", 0)) == 0):
                _chk(raw, "audio_fades", [int(b["rec_in"])], [a["id"], b["id"]], "%s meets %s at %.2f s without a fade"
                     % (a["id"], b["id"], int(b["rec_in"]) / f), "give both sides a 1 frame fade")
    # beat sync
    grid = beat_grid(edl, lab, N)
    tol = beat_tol(edl, P)
    off_cuts = set()
    if grid is not None:
        for c in cuts:
            tagged = any("on_beat" in ((items_by_id.get(i) or {}).get("tags") or []) for i in (c["a"], c["b"]) if i)
            bo = beat_off(c["frame"], grid)
            if tagged and bo is not None and not (tol[0] - 1e-6 <= bo <= tol[1] + 1e-6):
                off_cuts.add(c["frame"])
                mv = int(round(-bo - 1))
                fix = "move the cut %+d frames" % mv
                ia = items_by_id.get(c["a"]) or {}
                if mv < 0 and ia.get("words"):
                    later = [g for g in grid if g - 1 > c["frame"]]
                    if later:
                        add_f = int(round(later[0] - 1 - c["frame"]))
                        fix = ("give %s a longer tail (pad_ms [.., +%d ms]) so the cut lands on the next beat; moving "
                               "it earlier would clip the last word" % (re.sub(r"_\d+$", "", ia["id"]),
                                                                         int(math.ceil(add_f * 1000.0 / f))))
                _chk(raw, "beat_sync", [c["frame"]], [i for i in (c["a"], c["b"]) if i], "cut at %.2f s is %+.1f frames "
                     "from the beat (allowed %+d..%+d)" % (c["frame"] / f, bo, tol[0], tol[1]), fix)
    # pacing
    pc = P.get("pacing") or {}
    stats = {"frames": N, "shots": len(shots), "median_shot_s": None, "cv": None,
             "first_cut_s": round(cuts[0]["frame"] / f, 2) if cuts else None,
             "first_word_s": round(words_tl[0]["t0"], 2) if words_tl else None,
             "speech_music_gap_lu": ((preview or {}).get("audio") or {}).get("speech_music_gap_lu"),
             "loudness": loudness_doc}
    if len(durs):
        med = float(np.median(durs))
        cv = float(durs.std() / durs.mean()) if durs.mean() > 0 and len(durs) > 1 else 0.0
        stats["median_shot_s"], stats["cv"] = round(med, 2), round(cv, 2)
        stats["mean_shot_s"], stats["min_shot_s"], stats["max_shot_s"] = round(float(durs.mean()), 2), round(float(durs.min()), 2), round(float(durs.max()), 2)
        rng = pc.get("median_shot_s")
        if rng and len(durs) >= 3 and not (float(rng[0]) <= med <= float(rng[-1])):
            _chk(raw, "pacing", [], [], "median shot %.2f s is outside %g-%g s" % (med, float(rng[0]), float(rng[-1])),
                 "tighten or loosen the cuts toward the preset")
        rng = pc.get("cv")
        if rng and len(durs) >= 4 and not (float(rng[0]) <= cv <= float(rng[-1])):
            _chk(raw, "pacing", [], [], "shot length variation (cv) %.3f is outside %g-%g" % (cv, float(rng[0]), float(rng[-1])),
                 "vary the shot lengths" if cv < float(rng[0]) else "even out the shot lengths")
        vmax = pc.get("visual_change_max_s")
        if vmax:
            changes = sorted(set([0] + [c["frame"] for c in cuts] + [int(t["rec_in"]) for t in titles] + [N]
                                 + list(ov_changes)))
            for a, b in zip(changes, changes[1:]):
                if (b - a) / f > float(vmax) + 1e-6:
                    _chk(raw, "pacing", [a], [], "%.1f s without a visual change at %.1f-%.1f s" % ((b - a) / f, a / f, b / f),
                         "add a cutaway or a punch-in")
        dw = pc.get("densest_window_after")
        n10, at10 = densest_window(cuts, N, f)
        stats["densest_10s"] = {"cuts": n10, "at_s": round(at10 / f, 1)}
        if dw is not None and L >= 20 and n10 > 2 and at10 < float(dw) * N:
            _chk(raw, "pacing", [at10], [], "the densest 10 s (%d cuts) starts at %.0f s, before %.0f %% of the length" % (
                n10, at10 / f, float(dw) * 100), "build toward the end: move fast cutting later")
        elif dw is not None and 8.0 <= L < 20 and len(cuts) > 2:
            # a short piece: a window of half its length; the densest should not start in the first 40 % of the
            # room the window has (0 to L / 2)
            span_s = L / 2.0
            nw_, atw_ = densest_window(cuts, N, f, span_s)
            stats["densest"] = {"cuts": nw_, "at_s": round(atw_ / f, 1), "span_s": round(span_s, 2)}
            if nw_ > 2 and atw_ < float(dw) * (N - span_s * f):
                _chk(raw, "pacing", [atw_], [], "the densest %.1f s (%d cuts) starts at %.1f s: the fast cutting sits "
                     "at the start of this %.1f s piece instead of building toward its end" % (
                         span_s, nw_, atw_ / f, L), "build toward the end: move the fast cutting later")
    # jump cuts, repeats, transitions
    v1 = [it for it in sorted_items(v1_track(edl)) if is_media_item(it)]
    top_at = {}
    for kind, r0, r1, obj in pieces:
        if kind == "seg":
            top_at[r0] = obj["id"]
    shot_cache = {}

    def shot_of(it):
        mid = it["media"]
        if mid not in shot_cache:
            sp_ = (edl["media"].get(mid) or {}).get("shots")
            data = load_json_or(lab.rel(sp_), None) if (sp_ and lab) else None
            shot_cache[mid] = [(int(x["in"]), int(x["out"]), x.get("id")) for x in (data or {}).get("shots", [])]
        for a_, b_, sid_ in shot_cache[mid]:
            if a_ <= int(it["src_in"]) < b_:
                return sid_
        return None
    Wf = float(edl["timeline"]["width"])

    def shown(it):
        """(lo, hi): the part of the source width the item shows, as fractions (a crop of a wider shot or a punch-in
        shows less than 0 to 1), or None when its size or transform is unknown."""
        tf = it.get("transform") or {}
        try:
            z, pan = float(tf.get("zoom", 1.0) or 1.0), float(tf.get("pan_px", 0) or 0)
        except (TypeError, ValueError):
            return None
        geo = frame_geometry(edl, it)
        if geo is None:
            return None
        iw = geo[0] * z
        if iw <= Wf:
            return 0.0, 1.0
        return 0.5 - pan / iw - Wf / (2 * iw), 0.5 - pan / iw + Wf / (2 * iw)
    for a, b in zip(v1, v1[1:]):
        if a["media"] != b["media"] or int(a["rec_out"]) != int(b["rec_in"]) or (a["id"], b["id"]) in atrans:
            continue
        gap_s = abs(int(b["src_in"]) - int(a["src_out"])) / float(media_fps(edl, a["media"]))
        za = float((a.get("transform") or {}).get("zoom", 1.0))
        zb = float((b.get("transform") or {}).get("zoom", 1.0))
        visible = top_at.get(int(b["rec_in"])) == b["id"]
        same_shot = shot_of(a) is not None and shot_of(a) == shot_of(b)
        # a reframe that shows a different part of a wide shot (a crop cut from one speaker to the other in a vertical
        # piece) is an angle change, like a zoom change of 15 % or more: the shown parts overlap by less than half
        wa, wb = shown(a), shown(b)
        reframed = bool(wa and wb and max(0.0, min(wa[1], wb[1]) - max(wa[0], wb[0])) <
                        0.5 * min(wa[1] - wa[0], wb[1] - wb[0]))
        if (gap_s < 2.0 or same_shot) and max(za, zb) / max(1e-6, min(za, zb)) - 1.0 < 0.15 - 1e-6 and visible \
                and not reframed:
            if re.search(r"_\d+$", b["id"]):
                fix = ("%s is a join inside segment %s (a dropped word or a shortened pause): split the segment at "
                       "that point and give the second part zoom 1.15 or more, or cover the join with an overlay"
                       % (b["id"], re.sub(r"_\d+$", "", b["id"])))
            else:
                fix = "cover it with B-roll or punch in on %s (zoom 1.15 or more)" % b["id"]
            _chk(raw, "jump_cut", [int(b["rec_in"])], [a["id"], b["id"]], "%s to %s is a jump cut at %.2f s (same %s, "
                 "%.2f s apart in the source)" % (a["id"], b["id"], int(b["rec_in"]) / f,
                                                  "shot" if same_shot else "clip", gap_s), fix)
    # a crop of a wider shot (a vertical piece from 16:9 footage, a punch-in) shows only part of its width. It is a
    # problem when no face the shot log places in the shot is inside that part, or when the item frames a face
    # (frame_x) that the crop still cuts. A two-shot cut between speakers (frame_x on each speaker in turn) leaves the
    # listener out on purpose and is not flagged. A face counts as inside when its centre is half a typical face width
    # (0.05 of the source width) inside the crop's edge.
    sl_ = load_json_or(lab.p("shotlog.json"), None) if lab else None
    faces_ = {s.get("shot"): [f for f in (s.get("faces") or []) if isinstance(f, dict) and isinstance(f.get("x"), (int, float))]
              for s in (sl_ or {}).get("shots", []) if isinstance(s, dict)}
    if any(faces_.values()):
        for tn, it in visible_video(edl):
            if not is_media_item(it):
                continue
            tf = it.get("transform") or {}
            try:
                z = float(tf.get("zoom", 1.0) or 1.0)
            except (TypeError, ValueError):
                continue
            geo = frame_geometry(edl, it)
            w_ = shown(it)
            if geo is None or w_ is None or (tn > 1 and z < 1.0 - 1e-6) or geo[0] * z <= Wf * 1.05:
                continue
            lo, hi = w_
            fs = faces_.get(shot_of(it), [])
            if not fs:
                continue
            inside = [f for f in fs if lo + FACE_MARGIN <= float(f["x"]) <= hi - FACE_MARGIN]

            def who(f):
                return "%s (x %.2f)" % (f.get("who") or "a face", float(f["x"]))
            where = "%s shows %.0f to %.0f %% of the width of %s" % (it["id"], 100 * max(0.0, lo), 100 * min(1.0, hi),
                                                                      shot_of(it))
            fx = it.get("frame_x")
            # the face the item frames: the logged face nearest its frame_x (a frame_x far from every logged face
            # frames something else, and then only a crop with no face inside is flagged)
            f0 = min(fs, key=lambda f: abs(float(f["x"]) - float(fx))) if fx is not None else None
            if f0 is not None and abs(float(f0["x"]) - float(fx)) <= 0.15:
                if f0 not in inside:
                    _chk(raw, "subject_cropped", [int(it["rec_in"])], [it["id"]], "%s; it frames %s, but the crop cuts "
                         "that face%s" % (where, who(f0), (" (it shows %s)" % ", ".join(who(f) for f in inside)) if inside
                                          else ""),
                         "set \"frame_x\": %.2f on %s (the face of %s), or use a wider shot"
                         % (float(f0["x"]), it["id"], f0.get("who") or "the subject"))
                continue
            if not inside:
                f0 = min(fs, key=lambda f: abs(float(f["x"]) - 0.5 * (lo + hi)))
                _chk(raw, "subject_cropped", [int(it["rec_in"])], [it["id"]], "%s; no face the shot log places in it is "
                     "inside (%s)" % (where, ", ".join(who(f) for f in fs)),
                     "give %s the \"frame_x\" of the face it is about (the nearest is %s: %.2f), or use another shot"
                     % (it["id"], who(f0), float(f0["x"])))
    vis = [it for tn, it in visible_video(edl) if is_media_item(it) and "repeat_ok" not in (it.get("tags") or [])]
    for i in range(len(vis)):
        for j in range(i + 1, len(vis)):
            x, y = vis[i], vis[j]
            if x["media"] == y["media"]:
                ov = min(int(x["src_out"]), int(y["src_out"])) - max(int(x["src_in"]), int(y["src_in"]))
                if ov > 2:
                    _chk(raw, "repeat_shot", [int(y["rec_in"])], [x["id"], y["id"]], "%s and %s show the same %d source "
                         "frames" % (x["id"], y["id"], ov), "use different material for one of them")
    # titles and captions on top of each other
    tts = [it for tr in edl["tracks"]["video"] for it in tr.get("items", []) if it.get("kind") == "title" and it.get("box")]
    caps = [c for tr in edl["tracks"].get("subtitle", []) for c in tr.get("items", []) if c.get("box")]
    H_ = float(edl["timeline"]["height"])
    for t in tts:
        for c in caps:
            if int(t["rec_in"]) < int(c["rec_out"]) and int(c["rec_in"]) < int(t["rec_out"]):
                (tx, ty, tw, th), (cx, cy, cw, ch) = t["box"], c["box"]
                if tx < cx + cw and cx < tx + tw and ty < cy + ch and cy < ty + th:
                    _chk(raw, "text_overlap", [max(int(t["rec_in"]), int(c["rec_in"]))], [t["id"], c["id"]],
                         "title %s and caption %s overlap on screen at %.2f s" % (
                             t["id"], c["id"], max(int(t["rec_in"]), int(c["rec_in"])) / f),
                         "move the title %s the captions with \"y\" in the cut list (a title stays inside the safe "
                         "area), or the captions with \"caption_y\" on that segment" % (
                             "above" if cy + ch / 2.0 >= H_ / 2.0 else "below"))
                    break
    # a J or L cut made with an overlay of the same clip must continue its picture: an overlay that starts where a
    # V1 item of the same media ends (or ends where one starts) and plays other source frames jumps inside the shot
    by_out = {int(it["rec_out"]): it for it in v1}
    by_in = {int(it["rec_in"]): it for it in v1}
    for tr in edl["tracks"]["video"]:
        if track_num(tr.get("id")) < 2:
            continue
        for o in tr.get("items", []):
            if not is_media_item(o):
                continue
            mf_ = float(media_fps(edl, o["media"]))
            tol = max(1, int(round(mf_ / float(f))))

            def zoom_of(x):
                return float((x.get("transform") or {}).get("zoom", 1.0) or 1.0)

            def reframed(x, y):
                # a punch-in of 15 % or more reads as a new framing, not as the same picture jumping
                return max(zoom_of(x), zoom_of(y)) / max(1e-6, min(zoom_of(x), zoom_of(y))) - 1.0 >= 0.15 - 1e-6
            later = ("a later moment of the same shot straight after it reads as a jump cut: punch in on one of them "
                     "(zoom 1.15 or more) or put another shot between them")
            a = by_out.get(int(o["rec_in"]))
            jump = int(o["src_in"]) - int(a["src_out"]) if a is not None else 0
            if a is not None and a["media"] == o["media"] and abs(jump) > tol and not reframed(a, o):
                _chk(raw, "overlay_jump", [int(o["rec_in"])], [a["id"], o["id"]], "%s takes over from %s at %.2f s but "
                     "jumps %+d source frames inside the same clip" % (o["id"], a["id"], int(o["rec_in"]) / f, jump),
                     later if jump / mf_ > 1.0 else
                     "make the J cut with \"hold_prev_s\" on the next spine segment instead of an overlay")
            b = by_in.get(int(o["rec_out"]))
            jump = int(b["src_in"]) - int(o["src_out"]) if b is not None else 0
            if b is not None and b["media"] == o["media"] and abs(jump) > tol and not reframed(o, b):
                _chk(raw, "overlay_jump", [int(o["rec_out"])], [o["id"], b["id"]], "%s hands over to %s at %.2f s but "
                     "the picture jumps %+d source frames inside the same clip" % (o["id"], b["id"], int(o["rec_out"]) / f,
                                                                                   jump),
                     later if jump / mf_ > 1.0 else
                     "make the L cut with \"early_s\" on %s instead of an overlay" % b["id"])
    # the dissolve habit (effect transitions are fx_repeat's, below)
    vx = [x for x in edl.get("transitions", []) if str(x.get("track", "V1")).startswith("V")
          and ((fx_lab.tr_row(fx_lab.transition_key(x)) or {}).get("family") in (None, "dissolve"))]
    share = len(vx) / float(max(1, len(cuts)))
    mx = float((P.get("transitions") or {}).get("max_share", 0.05))
    if len(vx) >= 2 and share > mx + 1e-9:          # one dissolve in a short piece is a choice, not a habit
        _chk(raw, "transition_share", [], [x.get("id") for x in vx], "%d of %d edit points are dissolves (%.0f %%, max %.0f %%)"
             % (len(vx), len(cuts), share * 100, mx * 100), "turn most dissolves back into cuts")
    # ABCD for ads
    pid = str(P.get("id") or "")
    if pid.startswith("ad") or (P.get("checks") or {}).get("abcd") is True:
        miss = []
        if not len(durs) or durs[0] >= 3.0:
            miss.append("first shot is not under 3 s")
        span = int(round(5 * f))
        if not any(sum(1 for a, b in shots if a < s + span and b > s) >= 5 for s in range(0, max(1, N - span + 1), max(1, int(f / 2)))):
            miss.append("no 5 s window has 5 or more shots")
        if len(durs) and durs.mean() >= 2.0:
            miss.append("mean shot is %.2f s (under 2 s expected)" % durs.mean())
        if miss:
            _chk(raw, "abcd", [], [], "ABCD attract: " + "; ".join(miss), "open faster and cut the first seconds denser")
    # the message layer and the sound bed (round 2)
    caption_checks(edl, lab, P, raw)
    message_checks(edl, P, words_tl, raw)
    duck_pump_checks(edl, P, raw)
    more_checks(edl, lab, P, words_tl, pieces, cuts, raw)
    final_checks(edl, lab, P, words_tl, pieces, cuts, shots, raw, rdir=rdir, preview=preview)
    # effects, transitions, animated text and sound effects (fx_lab.fx_checks: taste WARN, safety STOP, D7)
    grid_ = beat_grid(edl, lab, N) if lab is not None else None
    for c in fx_lab.fx_checks(edl, lab, P, None if grid_ is None else list(grid_), safe=sbox):
        if c.get("level") == "INFO":
            notes.append("%s: %s; %s" % (c["id"], c["msg"], c["fix"]))
            continue
        lvl_ = c.get("level")
        _chk(raw, c["id"], c["at_frames"], c["items"], c["msg"], c["fix"],
             level=lvl_ if lvl_ != CHECK_LEVELS.get(c["id"]) else None)
    # levels, soft and off
    edl_off, edl_bad = split_checks_off(edl.get("checks_off"))
    if edl_bad:
        _chk(raw, "checks_off_ignored", [], [], "checks_off asks to switch off %s; a cut list may switch off only "
             "warnings and %s, so these stay on" % (", ".join(edl_bad), ", ".join(sorted(EDL_OFF_OK))),
             "remove them from checks_off and fix what they report")
    stats["checks_off"] = sorted(edl_off)
    if edl_bad:
        stats["checks_off_ignored"] = sorted(edl_bad)
    soft = set((P.get("checks") or {}).get("soft") or [])
    checks = []
    p_off = set((P.get("checks") or {}).get("off") or [])
    for c in raw:
        lvl = c.get("level") or CHECK_LEVELS.get(c["id"], "WARN")
        # a cut list may switch off a warning, not the same id raised to STOP (mix_peak far over 0 dBFS)
        if c["id"] in p_off or (c["id"] in edl_off and (lvl != "STOP" or c["id"] in EDL_OFF_OK)):
            continue
        if c["id"] in soft and lvl == "STOP":
            lvl = "WARN"
        checks.append(dict({"id": c["id"], "level": lvl}, **{k: v for k, v in c.items() if k not in ("id", "level")}))
    # an id the cut list switched off that still raised a STOP (mix_peak far over 0 dBFS) was not honoured: the
    # header lists it with the ignored ids, not as off
    kept_ = sorted({c["id"] for c in checks if c["level"] == "STOP"} & set(edl_off))
    if kept_:
        stats["checks_off"] = [c for c in stats["checks_off"] if c not in kept_]
        stats["checks_off_ignored"] = sorted(set(stats.get("checks_off_ignored") or []) | set(kept_))
    checks.sort(key=lambda c: (0 if c["level"] == "STOP" else 1, c["at_frames"][0] if c["at_frames"] else -1))
    stats["off_beat_cuts"] = sorted(off_cuts)
    return checks, stats, notes


def checks_result(checks):
    if any(c["level"] == "STOP" for c in checks):
        return "STOP"
    return "WARN" if checks else "OK"


def run_checks(lab, edl_path, out=None, edl=None, words_tl=None, pieces=None):
    edl = edl or load_edl(edl_path)
    P = load_preset(edl.get("preset"), lab, required=False)
    rdir = review_dir(edl_path, out)
    st = preview_state(rdir)
    pdir = rdir
    if st is None and out:
        # a judge or reviewer writes into its own folder but reads the preview next to the EDL
        pdir = review_dir(edl_path, None)
        st = preview_state(pdir)
    notes = []
    if st and st.get("edl_hash") != edl_hash(edl):
        notes.append("the preview is older than the EDL: preview-level checks skipped (run preview or review)")
        st = None
    elif st is None:
        notes.append("no preview yet: preview-level checks (preview_frames, silence_gap, speech_music_gap, "
                     "loudness_off, music_lull, music_open_quiet, text_contrast) skipped")
    checks, stats, n2 = compute_checks(edl, lab, P, st, words_tl, pieces, rdir=pdir if st else None)
    res = checks_result(checks)
    doc = {"schema": "resolve-editor/checks@1", "edl": lab.relto(edl_path), "result": res, "checks": checks,
           "stats": stats, "notes": notes + n2}
    os.makedirs(rdir, exist_ok=True)
    fn = write_json(os.path.join(rdir, "checks.json"), doc)
    return doc, fn


def check_lines(doc):
    ns = sum(1 for c in doc["checks"] if c["level"] == "STOP")
    nw = len(doc["checks"]) - ns
    out = ["%d STOP, %d WARN" % (ns, nw)]
    shown, more = {}, {}
    for c in doc["checks"]:
        k = (c["level"], c["id"])
        shown[k] = shown.get(k, 0) + 1
        if shown[k] > 10:
            more[k] = more.get(k, 0) + 1
            continue
        out.append("%s %s: %s; fix: %s" % (c["level"], c["id"], c["msg"], c["fix"]))
    for (lvl, cid), n in sorted(more.items()):
        out.append("%s %s: and %d more (all of them are in checks.json)" % (lvl, cid, n))
    off = (doc.get("stats") or {}).get("checks_off") or []
    ign = (doc.get("stats") or {}).get("checks_off_ignored") or []
    if off or ign:
        out.append("checks off (cut list): %s%s" % (", ".join(off) or "none", ("; asked but still on: %s" % ", ".join(ign))
                                                     if ign else ""))
    return out + ["note: %s" % n for n in doc.get("notes", [])]


def cmd_check(lab, edl_path, out=None, as_json=False):
    doc, fn = run_checks(lab, edl_path, out)
    if as_json:
        print(json.dumps(doc, indent=1))
        return doc
    print_result(doc["result"], check_lines(doc), [fn])
    return doc


# ---------------------------------------------------------------------------------------------------- review pack
def tl_tc(edl, frame):
    fps = tl_fps(edl)
    st = edl["timeline"].get("start_tc") or "00:00:00:00"
    drop = bool(edl["timeline"].get("drop_frame")) or is_drop_tc(st)
    try:
        base = tc_to_frames(st, fps, drop)
    except ValueError:
        base = 0
    return frames_to_tc(base + int(frame), fps, drop)


def grab(path, wanted, w, h):
    """Decode the preview once at w x h and keep only the wanted frame indexes (streamed, constant memory)."""
    wanted = set(int(x) for x in wanted)
    got = {}
    if not wanted:
        return got
    p = subprocess.Popen([ffmpeg_bin(), "-nostdin", "-v", "error", "-i", path, "-f", "rawvideo", "-pix_fmt", "rgb24",
                          "-s", "%dx%d" % (w, h), "-an", "-"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    n, sz, last = 0, w * h * 3, max(wanted)
    try:
        while n <= last:
            buf = p.stdout.read(sz)
            if len(buf) < sz:
                break
            if n in wanted:
                got[n] = np.frombuffer(buf, np.uint8).reshape(h, w, 3)
            n += 1
    finally:
        p.stdout.close()
        p.kill()
        p.wait()
    return got


def view_stretch(img):
    """For flat log footage: stretch p1..p99 luma to 16..235 (for the picture an agent looks at only)."""
    y = img.astype(np.float32).mean(axis=2)
    p1, p99 = np.percentile(y, 1), np.percentile(y, 99)
    if (p99 < 200 or p1 > 20) and p99 - p1 > 4:
        out = (img.astype(np.float32) - p1) * (219.0 / (p99 - p1)) + 16
        return np.clip(out, 0, 255).astype(np.uint8)
    return img


def thumb_size(raster, long_side):
    pw, ph = raster
    if pw >= ph:
        return long_side, even(long_side * ph / float(pw))
    return even(long_side * pw / float(ph)), long_side


def item_flags(edl, lab, P, checks, pieces, words_tl):
    f = float(tl_fps(edl))
    flags = {}

    def add(i, fl):
        if i:
            flags.setdefault(i, [])
            if fl not in flags[i]:
                flags[i].append(fl)
    for kind, tr, it in iter_items(edl, ("video",)):
        if it.get("kind", "clip") not in ("clip", "solid"):
            continue
        if int(it["rec_in"]) < 3 * f or "hook" in (it.get("tags") or []):
            add(it["id"], "HOOK")
        if speed_of(it) != 1.0:
            add(it["id"], "SPEED")
        if track_num(tr.get("id")) > 1:
            add(it["id"], "OVERLAY")
    for x in edl.get("transitions", []):
        add(x.get("from"), "XFADE")
        add(x.get("to"), "XFADE")
    link_of = {}
    for kind, tr, it in iter_items(edl, ("video", "audio")):
        if it.get("link"):
            link_of.setdefault(it["link"], []).append(it["id"])
    by_audio = {}
    for ids in link_of.values():
        for i in ids:
            by_audio[i] = ids
    for c in checks:
        fl = {"flash_frame": "FLASH", "beat_sync": "OFF-BEAT", "jump_cut": "JUMP?", "clipped_word": "CLIPPED"}.get(c["id"])
        if not fl:
            continue
        its = c["items"][-1:] if c["id"] in ("beat_sync", "jump_cut") else c["items"]
        for i in its:
            for j in by_audio.get(i, [i]):
                add(j, fl)
    return flags


def one_line(text):
    """A title's text on one report line: its line breaks as " / "."""
    return " / ".join(x.strip() for x in str(text).replace("\r\n", "\n").split("\n") if x.strip())


def fmt_off(bo):
    if bo is None:
        return "-"
    return ("%+d" % int(round(bo))) if abs(bo - round(bo)) < 1e-6 else ("%+.1f" % bo)


def fx_report_lines(edl, it, f):
    """The report's fx lines of one item: each effect with its frames, main value and preview class (approximate
    spans are not pixel-checked), its retime and its text animation."""
    out = []
    r0 = int(it.get("rec_in", 0))
    for x in it.get("fx") or []:
        p = x.get("params") or {}
        main = next(("%s %s" % (k, p[k]) for k in ("zoom", "peak", "level", "to", "amp", "px", "min_speed", "hold_s")
                     if k in p), "")
        row = fx_lab.fx_row(x.get("kind")) or {}
        cls = (row.get("preview") or {}).get("class", "exact")
        f_ = x.get("f") or [0, 0]
        out.append("fx %s %s %.2f-%.2f s %s%s%s%s" % (
            x.get("id"), x.get("kind"), (r0 + int(f_[0])) / f, (r0 + int(f_[1]) + 1) / f, main,
            " beat %s" % x["on_beat"] if x.get("on_beat") is not None else "",
            "  APPROXIMATE preview (not pixel-checked)" if cls == "approx" else "",
            "  CLIPPED at the item edge" if x.get("clipped") else ""))
    if it.get("retime_process") == "speed_warp":
        out.append("fx %s speed warp at %g %%  approximate preview (nearest frames)" % (it["id"], speed_of(it) * 100))
    an = it.get("anim") or {}
    if an.get("id") not in (None, "none"):
        out.append("anim %s%s" % (an["id"], (" keyword %d" % an["keyword"]) if an.get("keyword") is not None else ""))
    return out


def transition_report_note(x):
    """The route and preview class of a catalogue transition (empty for the two legacy dissolves)."""
    if x.get("type") in fx_lab.LEGACY_TRANSITIONS and not x.get("preview"):
        return ""
    row = fx_lab.tr_row(fx_lab.transition_key(x)) or {}
    cls = (x.get("preview") or {}).get("class") or (row.get("preview") or {}).get("class") or "exact"
    route = ((x.get("resolve") or {}).get("build")) or (row.get("resolve") or {}).get("build")
    asked = (x.get("params") or {}).get("asked")
    return "  route %s%s  preview %s%s  audio %s" % (
        route, (" (asked %s)" % asked) if asked else "", cls,
        " APPROXIMATE (frames around it are checked, not the transition)" if cls == "approx" else "",
        x.get("audio", "none"))


def effects_summary(edl):
    """The EFFECTS block of the report: genre, budget, events, approximate spans."""
    ev = fx_lab.fx_events(edl)
    caps = [c for k, tr, c in iter_items(edl, ("subtitle",)) if (c.get("anim") or {}).get("id") not in (None, "none")]
    if not ev and not caps and not edl.get("fx_meta"):
        return []
    f = float(tl_fps(edl))
    g = fx_lab.edl_genre(edl)
    G = fx_lab.genre_spec(g)
    meta = edl.get("fx_meta") or {}
    out = ["genre %s (budget %.1f events per 10 s, appetite %s, zoom cap %.2f, families %d)%s" % (
        g, float(G["per10s"]), meta.get("appetite", "normal"), float(G["zoom_max"]), int(G["max_families"]),
        "  premium" if meta.get("premium") else "")]
    # a default fade or slide on a title is part of the look, not an effect event (fx_density counts the same way)
    look_ = [e for e in ev if e["source"] == "anim" and not e.get("counts")]
    ev = [e for e in ev if not (e["source"] == "anim" and not e.get("counts"))]
    out.append("events %d: %s" % (len(ev), ", ".join("%s %s %.2f s" % (e["id"], e.get("anim") or e["kind"], e["at"] / f)
                                                     for e in ev[:30]) + (" ..." if len(ev) > 30 else "")))
    if look_:
        out.append("title animations not counted as events (default fades and slides): %s" % ", ".join(
            "%s %s" % (e["item"], e.get("anim")) for e in look_[:20]))
    if edl.get("fx_refused"):
        out.append("REFUSED at assemble (not in the edit, fx_refused STOP): %s" % "; ".join(
            "%s: %s" % (r.get("id"), one_line(str(r.get("why")))[:120]) for r in edl["fx_refused"][:10]))
    if caps:
        out.append("animated captions %d (%s)" % (len(caps), ", ".join(sorted({c["anim"]["id"] for c in caps}))))
    ap = [s for s in fx_lab.pixel_plan(edl) if s["mark"] or s["min_corr"] is None]
    if ap:
        out.append("approximate in the preview (not pixel-checked): " + ", ".join(
            "%s %.2f-%.2f s" % (s["fx"], s["f0"] / f, s["f1"] / f) for s in ap[:20]))
    return out


def item_beat_grids(edl, lab):
    """[(rec_in, rec_out, beat frames on the timeline)] for every music item whose media has a beats file: its own
    grid through its own source offset and speed (two copies of a loop each carry theirs)."""
    out = []
    if lab is None:
        return out
    f = float(tl_fps(edl))
    for tr in edl["tracks"].get("audio", []):
        if tr.get("role") != "music":
            continue
        for it in tr.get("items", []):
            if not is_media_item(it):
                continue
            bt = lab.analysis(it["media"], "beats") or {}
            if not bt.get("beats"):
                continue
            try:
                mf = float(media_fps(edl, it["media"]))
            except (KeyError, TypeError, ValueError, ZeroDivisionError):
                continue
            sp = speed_of(it) or 1.0
            g = int(it["rec_in"]) + (np.array(bt["beats"], np.float64) - int(it["src_in"]) / mf) / sp * f
            g = np.round(g, 6)
            out.append((int(it["rec_in"]), int(it["rec_out"]), g[(g >= int(it["rec_in"]) - f) & (g <= int(it["rec_out"]) + f)]))
    return out


def write_report(edl, lab, P, checks_doc, words_tl, pieces, rdir, flags):
    f = float(tl_fps(edl))
    N = programme_frames(edl)
    v = edl.get("version") or {}
    stt = checks_doc["stats"]
    cuts, shots = cuts_and_shots(edl, pieces, N)
    grid = beat_grid(edl, lab, N)
    titles = sorted([it for tr in edl["tracks"]["video"] for it in tr.get("items", []) if it.get("kind") == "title"],
                    key=lambda t: int(t["rec_in"]))
    L = []
    L.append("EDIT %s by %s (parent %s)  preset %s  platform %s" % (v.get("id", "?"), v.get("by", "?"),
                                                                     v.get("parent") or "none", edl.get("preset"),
                                                                     edl.get("platform")))
    tgt = (edl.get("targets") or {}).get("duration_frames")
    L.append("TIMELINE %dx%d %s fps start %s  %d f = %.2f s%s" % (
        int(edl["timeline"]["width"]), int(edl["timeline"]["height"]), fps_label(tl_fps(edl)),
        edl["timeline"].get("start_tc"), N, N / f, (" (target %.2f s)" % (int(tgt) / f)) if tgt else ""))
    if stt.get("median_shot_s") is not None:
        d10 = stt.get("densest_10s") or {}
        L.append("SHOTS %d cuts %d  shot s: median %.2f mean %.2f min %.2f max %.2f cv %.2f  densest 10 s: %d cuts at %.0f-%.0f s"
                 % (len(shots), len(cuts), stt["median_shot_s"], stt.get("mean_shot_s", 0), stt.get("min_shot_s", 0),
                    stt.get("max_shot_s", 0), stt.get("cv") or 0, d10.get("cuts", 0), d10.get("at_s", 0),
                    d10.get("at_s", 0) + 10))
    ft = titles[0] if titles else None
    L.append("HOOK first cut %s  first word %s  first title %s" % (
        "%.2f s" % stt["first_cut_s"] if stt.get("first_cut_s") is not None else "none",
        "%.2f s" % stt["first_word_s"] if stt.get("first_word_s") is not None else "none",
        ('%.2f-%.2f s "%s"' % (int(ft["rec_in"]) / f, int(ft["rec_out"]) / f, one_line(ft.get("text", ""))))
        if ft else "none"))
    au = []
    dia_s = sum(w["t1"] - w["t0"] for w in words_tl)
    au.append("dialogue %.1f s" % dia_s)
    for tr in edl["tracks"]["audio"]:
        if tr.get("role") == "music":
            for it in tr.get("items", []):
                au.append("music %s %+g dB duck %s" % (it["id"], float(it.get("gain_db", 0)),
                                                       (it.get("duck") or {}).get("mode", "env" if it.get("volume_env") else "none")))
    gap = stt.get("speech_music_gap_lu")
    if gap is not None:
        au.append("speech/music gap %.1f LU (target %g)" % (gap, float((P.get("music") or {}).get("duck_lu", 14))))
    L.append("AUDIO " + "  ".join(au))
    ns = sum(1 for c in checks_doc["checks"] if c["level"] == "STOP")
    L.append("CHECKS RESULT %s: %d STOP, %d WARN (listed at the end)" % (checks_doc["result"], ns, len(checks_doc["checks"]) - ns))
    L.append("#   rec TC        dur  trk  source               src_in  speed  sound    beat  flags            why")
    rows = sorted([(int(it["rec_in"]), track_num(tr.get("id")), tr.get("id"), it)
                   for kind, tr, it in iter_items(edl, ("video",)) if it.get("kind", "clip") in ("clip", "solid")])
    snd = {}
    for tr in edl["tracks"]["audio"]:
        for a in tr.get("items", []):
            snd[a["id"]] = "%s %+g" % (tr.get("id"), float(a.get("gain_db", 0) or 0))
    grids_ = item_beat_grids(edl, lab)
    for n, (ri, tn, tid, it) in enumerate(rows, 1):
        # the beat grid of the music item playing at this cut (each copy of a chained loop has its own)
        g_ = next((g for a_, b_, g in grids_ if a_ <= ri < b_), None)
        g_ = grid if g_ is None else g_
        bo = beat_off(ri, g_) if (g_ is not None and len(g_) and ri > 0) else None
        L.append("%-3d %s %5d  %-4s %-20s %6s  %5s  %-7s %4s  %-16s %s" % (
            n, tl_tc(edl, ri), rec_len(it), tid, str(it.get("media") or it.get("kind"))[:20],
            it.get("src_in", "-"), ("%g" % speed_of(it)), snd.get(it["id"] + "a", "-"), fmt_off(bo),
            " ".join(flags.get(it["id"], [])) or "-", str(it.get("why", ""))[:60]))
        for ln in fx_report_lines(edl, it, f):
            L.append("      " + ln)
    # shot economy: how often each setup is on screen, and the good shots the cut leaves out
    vis_ = [it for _, it in visible_video(edl) if is_media_item(it)]
    cnt = {}
    for it in vis_:
        cnt[it["media"]] = cnt.get(it["media"], 0) + 1
    if cnt:
        L.append("SETUPS %d distinct on screen: %s" % (len(cnt), ", ".join(
            "%s x%d" % (k, n_) for k, n_ in sorted(cnt.items(), key=lambda kv: (-kv[1], kv[0])))))
    sl_ = load_json_or(lab.p("shotlog.json"), None) if lab is not None else None
    unused = []
    for s in (sl_ or {}).get("shots") or []:
        sc = s.get("score") if isinstance(s, dict) else None
        if not isinstance(sc, (int, float)) or isinstance(sc, bool) or sc < 6:
            continue
        try:
            mid_, a_, b_ = _shot_lookup(lab, s.get("shot"))
        except Fail:
            continue
        if not any(it["media"] == mid_ and int(it["src_in"]) < b_ and a_ < int(it["src_out"]) for it in vis_):
            unused.append((sc, s["shot"]))
    if unused:
        L.append("UNUSED good shots (log score 6 or more): " + ", ".join(
            "%s (%g)" % (sid_, sc) for sc, sid_ in sorted(unused, key=lambda x: (-x[0], x[1]))[:12]))
    L.append("DIALOGUE ON THE TIMELINE")
    cur_item, row = None, []
    for w in words_tl:
        if w["item"] != cur_item:
            if row:
                L.append("  " + " ".join(row))
            row = ["%s %.2f" % (w["speaker"], w["t0"])]
            cur_item = w["item"]
        tok = "%s[%s]" % (w["w"].strip(), w.get("i"))
        if w["clipped"]:
            tok += "@%.2f[CLIPPED]" % w["t0"]
        if set(w["tags"]) & set((P.get("speech") or {}).get("remove") or []):
            tok += "*"
        if "joined" in w["tags"]:
            tok += "(joined)"
        row.append(tok)
    if row:
        L.append("  " + " ".join(row))
    L.append("CAPTIONS")
    for tr in edl["tracks"].get("subtitle", []):
        for c in tr.get("items", []):
            L.append('  %s %.2f-%.2f "%s" (%.2f s, %d chars)' % (c["id"], int(c["rec_in"]) / f, int(c["rec_out"]) / f,
                                                                c.get("text", ""), rec_len(c) / f, len(c.get("text", ""))))
    L.append("TITLES / MUSIC / TRANSITIONS / MARKERS")
    for t in titles:
        L.append('  title %s %.2f-%.2f %s "%s" box %s' % (t["id"], int(t["rec_in"]) / f, int(t["rec_out"]) / f,
                                                         t.get("style"), one_line(t.get("text", "")), t.get("box")))
    for tr in edl["tracks"]["audio"]:
        if tr.get("role") == "music":
            for it in tr.get("items", []):
                L.append("  music %s %.2f-%.2f src %s..%s gain %+g dB fade out %d f%s" % (
                    it["id"], int(it["rec_in"]) / f, int(it["rec_out"]) / f, it["src_in"], it["src_out"],
                    float(it.get("gain_db", 0)), int(it.get("fade_out", 0)),
                    ("  duck %s dB" % (it.get("duck") or {}).get("depth_db")) if it.get("duck") else ""))
    if grid is not None:
        L.append("  beat grid %d beats, offset %s f" % (len(grid), (edl.get("music") or {}).get("offset_frames")))
    for x in edl.get("transitions", []):
        L.append("  transition %s %s %s -> %s %d f %s%s" % (x.get("id"), x.get("type"), x.get("from"), x.get("to"),
                                                           int(x.get("frames", 0)), x.get("alignment", "center"),
                                                           transition_report_note(x)))
    for mk in edl.get("markers", []):
        L.append("  marker %s %.2f s %s %s %s" % (tl_tc(edl, mk["frame"]), int(mk["frame"]) / f, mk.get("color"),
                                                  mk.get("name"), mk.get("note", "")))
    fxl = effects_summary(edl)
    if fxl:
        L.append("EFFECTS")
        L += ["  " + x for x in fxl]
    off = (checks_doc.get("stats") or {}).get("checks_off") or []
    ign = (checks_doc.get("stats") or {}).get("checks_off_ignored") or []
    L.append("CHECKS" + ("  (off by the cut list: %s)" % ", ".join(off) if off else "") +
             ("  (asked off but still on: %s)" % ", ".join(ign) if ign else ""))
    shown, more = {}, {}
    for c in checks_doc["checks"]:
        k = (c["level"], c["id"])
        shown[k] = shown.get(k, 0) + 1
        if shown[k] > 10:
            more[k] = more.get(k, 0) + 1
            continue
        L.append("  %s %s: %s; fix: %s" % (c["level"], c["id"], c["msg"], c["fix"]))
    for (lvl, cid), n in sorted(more.items()):
        L.append("  %s %s: and %d more (all of them are in checks.json)" % (lvl, cid, n))
    for nt in checks_doc.get("notes", []):
        L.append("  note %s" % nt)
    fn = os.path.join(rdir, "report.txt")
    with open(fn, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(L) + "\n")
    return fn


def draw_overview(edl, lab, P, preview, env, words_tl, pieces, checks_doc, flags, rdir, flat):
    from PIL import Image, ImageDraw
    f = float(tl_fps(edl))
    N = programme_frames(edl)
    width, spr = 1960, 30
    if N <= spr * f:
        # a piece shorter than one 30 s row fills the row (an 11.8 s reel used to sit in the left 800 px)
        spr = max(5, int(math.ceil(N / f)))
    pps = (width - 60) / (spr * f)
    raster = preview_size(int(edl["timeline"]["width"]), int(edl["timeline"]["height"]))
    th = 54
    tw = max(8, even(th * raster[0] / float(raster[1])))
    segs = [(r0, r1, obj) for kind, r0, r1, obj in pieces if kind == "seg"]
    mids = {(r0 + r1) // 2 for r0, r1, obj in segs}
    fr = grab(preview, mids, tw, th) if preview and os.path.exists(preview) else {}
    cuts, shots = cuts_and_shots(edl, pieces, N)
    grid = beat_grid(edl, lab, N)
    tol = beat_tol(edl, P)
    bad_cuts = set(checks_doc["stats"].get("off_beat_cuts") or [])
    for c in checks_doc["checks"]:
        if c["id"] in ("flash_frame", "jump_cut", "clipped_word") and c["at_frames"]:
            bad_cuts.update(c["at_frames"])
    rows = max(1, int(math.ceil(N / (spr * f))))
    row_h = 218
    per_img = 9
    font = _font(11)[0]
    files, tokens = [], 0
    peak = env.get("mix_peak") if env is not None else None
    titles = [it for tr in edl["tracks"]["video"] for it in tr.get("items", []) if it.get("kind") == "title"]
    caps = [c for tr in edl["tracks"].get("subtitle", []) for c in tr.get("items", [])]
    fxspans = fx_lab.pixel_plan(edl)
    for img_i in range(0, rows, per_img):
        nr = min(per_img, rows - img_i)
        im = Image.new("RGB", (width, nr * row_h), (20, 20, 20))
        d = ImageDraw.Draw(im)
        for r in range(nr):
            f0 = int(round((img_i + r) * spr * f))
            f1 = min(N, int(round((img_i + r + 1) * spr * f)))
            oy = r * row_h
            X = lambda fr_: 50 + (fr_ - f0) * pps
            for s in range(int(f0 / f), int(f1 / f) + 1):
                d.line([(X(s * f), oy + 12), (X(s * f), oy + 16)], fill=(150, 150, 150))
                if s % 5 == 0:
                    d.text((X(s * f) + 2, oy + 1), "%ds" % s, fill=(170, 170, 170), font=font)
            y_th, y_pc, y_wv, y_wd, y_sp = oy + 18, oy + 18 + th + 4, oy + 18 + th + 44, oy + 18 + th + 108, oy + row_h - 12
            for k, (r0, r1, obj) in enumerate(segs):
                if r1 <= f0 or r0 >= f1:
                    continue
                xa, xb = X(max(r0, f0)), X(min(r1, f1))
                m = (r0 + r1) // 2
                if m in fr and xb - xa > 4:
                    t = fr[m]
                    if flat:
                        t = view_stretch(t)
                    tim = Image.fromarray(t).crop((0, 0, int(min(tw, xb - xa - 1)), th))
                    im.paste(tim, (int(xa), y_th))
                dur = r1 - r0
                hgt = min(36.0, 36.0 * dur / (4 * f))
                col = (230, 60, 60) if dur < 3 else ((90, 170, 255) if k % 2 else (60, 130, 220))
                d.rectangle([xa, y_pc + 36 - hgt, max(xa, xb - 1), y_pc + 36], fill=col)
            if peak is not None and len(peak):
                for x in range(50, int(X(f1))):
                    a = int((f0 + (x - 50) / pps) / f * 100)
                    b = max(a + 1, int((f0 + (x - 49) / pps) / f * 100))
                    v = float(peak[a:b].max()) if a < len(peak) else 0.0
                    d.line([(x, y_wv + 30 - v * 30), (x, y_wv + 30 + v * 30)], fill=(120, 200, 120))
            if grid is not None:
                for bf in grid[(grid >= f0) & (grid < f1)]:
                    d.line([(X(bf), y_wv), (X(bf), y_wv + 7)], fill=(255, 255, 255))
            for c in cuts:
                if f0 <= c["frame"] < f1:
                    red = c["frame"] in bad_cuts
                    d.line([(X(c["frame"]), oy + 16), (X(c["frame"]), y_wv + 60)],
                           fill=(255, 60, 60) if red else (255, 210, 0), width=1)
            lane_end = [-1e9, -1e9]           # two label lanes; a label that fits neither lane is left out
            for k, w in enumerate(words_tl):
                fw = w["t0"] * f
                if f0 <= fw < f1:
                    txt = w["w"].strip()[:12]
                    try:
                        tw = d.textlength(txt, font=font)
                    except Exception:
                        tw = 6.5 * len(txt)
                    x0 = X(fw)
                    lane = 0 if x0 >= lane_end[0] + 3 else (1 if x0 >= lane_end[1] + 3 else None)
                    if lane is None and not w["clipped"]:
                        continue
                    lane = 0 if lane is None else lane
                    lane_end[lane] = x0 + tw
                    d.text((x0, y_wd + 12 * lane), txt,
                           fill=(255, 110, 110) if w["clipped"] else (230, 230, 160), font=font)
            for t in titles:
                if int(t["rec_out"]) > f0 and int(t["rec_in"]) < f1:
                    d.rectangle([X(max(int(t["rec_in"]), f0)), y_sp, X(min(int(t["rec_out"]), f1)), y_sp + 4], fill=(200, 120, 255))
            for c in caps:
                if int(c["rec_out"]) > f0 and int(c["rec_in"]) < f1:
                    d.rectangle([X(max(int(c["rec_in"]), f0)), y_sp + 6, X(min(int(c["rec_out"]), f1)) - 1, y_sp + 9], fill=(90, 220, 230))
            # effect spans under the pace bars: orange where the preview matches Resolve, magenta and "approx"
            # where it only suggests the effect (those frames are not pixel-checked)
            for s_ in fxspans:
                if s_["f1"] > f0 and s_["f0"] < f1:
                    xa, xb = X(max(s_["f0"], f0)), X(min(s_["f1"], f1))
                    apx = s_["mark"] or s_["min_corr"] is None
                    col = (255, 60, 200) if apx else (255, 150, 40)
                    d.rectangle([xa, y_pc + 37, max(xa + 1, xb - 1), y_pc + 39], fill=col)
                    if apx and xb - xa > 34:
                        d.text((xa + 2, y_pc + 1), "approx", fill=col, font=font)
            d.text((4, oy + 2), tl_tc(edl, f0)[3:], fill=(200, 200, 200), font=font)
        fn = os.path.join(rdir, "overview_%02d.png" % (img_i // per_img + 1))
        im.save(fn)
        files.append(fn)
        tokens += vis_tokens(*im.size)
    return files, tokens


def title_fading_frames(edl):
    """Frames where a title is caught in its entrance or exit (not whole: pale, small or partly revealed). The cut
    sheets mark them, so a pale title on a sheet is not read as a contrast problem."""
    f = float(tl_fps(edl))
    out = set()
    for _, t_ in _titles_of(edl):
        if not t_.get("anim"):
            continue
        try:
            for k_, s_ in enumerate(fx_lab.anim_states(t_, f)):
                if not fx_lab.state_whole(t_, s_):
                    out.add(int(t_["rec_in"]) + k_)
        except Exception:
            continue
    return out


def draw_cut_sheets(edl, lab, P, preview, pieces, checks_doc, tier, rdir, flat):
    from PIL import Image, ImageDraw
    f = float(tl_fps(edl))
    N = programme_frames(edl)
    cuts, shots = cuts_and_shots(edl, pieces, N)
    if tier == "quick" or not cuts:
        return [], 0, []
    flagged = set(checks_doc["stats"].get("off_beat_cuts") or [])
    for c in checks_doc["checks"]:
        flagged.update(c["at_frames"] or [])
    near = set()
    items = {it["id"]: it for k, tr, it in iter_items(edl, ("video",))}
    for c in cuts:
        if c["xfade"] or any(speed_of(items.get(i) or {}) != 1.0 for i in (c["a"], c["b"]) if i):
            near.add(c["frame"])
    sel = [c for c in cuts if tier == "best" or c["frame"] in flagged or c["frame"] <= 3 * f or c["frame"] in near]
    if not sel:
        return [], 0, []
    raster = preview_size(int(edl["timeline"]["width"]), int(edl["timeline"]["height"]))
    tw, th = thumb_size(raster, 160)
    want = set()
    for c in sel:
        want |= {max(0, c["frame"] - 2), max(0, c["frame"] - 1), c["frame"], min(N - 1, c["frame"] + 1)}
    fr = grab(preview, want, tw, th)
    cell_w, cell_h = 4 * tw + 3 * 2 + 10 + 6, th + 16
    cols = max(1, min(5, (MAX_SIDE - 10) // cell_w))
    rows = max(1, min(16, MAX_SIDE // cell_h))
    per = cols * rows
    grid = beat_grid(edl, lab, N)
    bounds = [0] + [c["frame"] for c in cuts] + [N]
    font = _font(11)[0]
    files, tokens = [], 0
    fading = title_fading_frames(edl)
    for s in range(0, len(sel), per):
        chunk = sel[s:s + per]
        nrows = int(math.ceil(len(chunk) / float(cols)))
        im = Image.new("RGB", (cols * cell_w, nrows * cell_h), (24, 24, 24))
        d = ImageDraw.Draw(im)
        for j, c in enumerate(chunk):
            k = cuts.index(c) + 1
            x0, y0 = (j % cols) * cell_w + 4, (j // cols) * cell_h
            la = bounds[k] - bounds[k - 1]
            lb = bounds[k + 1] - bounds[k]
            bo = beat_off(c["frame"], grid)
            lab_txt = "cut %d %s %df|%df%s%s%s" % (k, tl_tc(edl, c["frame"]), la, lb,
                                                   "" if bo is None else " beat" + fmt_off(bo), " XFADE" if c["xfade"] else "",
                                                   " TITLE MID-FADE" if any(c["frame"] + q_ in fading for q_ in (-2, -1, 0, 1))
                                                   else "")
            red = c["frame"] in flagged
            d.text((x0, y0 + 1), lab_txt, fill=(255, 90, 90) if red else (230, 230, 230), font=font)
            x = x0
            for q, fi in enumerate((c["frame"] - 2, c["frame"] - 1, c["frame"], c["frame"] + 1)):
                if fi in fr:
                    t = view_stretch(fr[fi]) if flat else fr[fi]
                    im.paste(Image.fromarray(t), (int(x), y0 + 14))
                x += tw + 2 + (10 if q == 1 else 0)
            xl = x0 + 2 * tw + 2 + 5
            d.line([(xl, y0 + 14), (xl, y0 + 14 + th)], fill=(255, 210, 0), width=3)
        fn = os.path.join(rdir, "cuts_%02d.jpg" % (s // per + 1))
        im.save(fn, quality=85)
        files.append(fn)
        tokens += vis_tokens(*im.size)
    return files, tokens, [c["frame"] for c in sel]


def cmd_review(lab, edl_path, tier="standard", out=None, as_json=False):
    t0 = time.time()
    if tier not in ("quick", "standard", "best"):
        raise Fail("--tier must be quick, standard or best")
    edl = load_edl(edl_path)
    P = load_preset(edl.get("preset"), lab, required=False)
    rdir = review_dir(edl_path, out)
    os.makedirs(rdir, exist_ok=True)
    for old in os.listdir(rdir):
        if re.match(r"^(overview|cuts)_\d+\.(png|jpg)$", old):
            os.remove(os.path.join(rdir, old))
    if not preview_fresh(edl, rdir):
        render_preview(lab, edl_path, out)
    st = preview_state(rdir)
    N = programme_frames(edl)
    pieces, _ = flatten(edl, N, lab=lab, shots=True)
    words_tl = timeline_words(edl, lab)
    doc, cfn = run_checks(lab, edl_path, out, edl=edl, words_tl=words_tl, pieces=pieces)
    flags = item_flags(edl, lab, P, doc["checks"], pieces, words_tl)
    rep = write_report(edl, lab, P, doc, words_tl, pieces, rdir, flags)
    prev = os.path.join(rdir, "preview.mov")
    ok_prev = st and st.get("result") == "OK" and os.path.exists(prev)
    envf = os.path.join(rdir, "audio_env.npz")
    env = dict(np.load(envf)) if os.path.exists(envf) else None
    flat = any(m.get("flat_log") for m in edl["media"].values())
    ov, ovt = draw_overview(edl, lab, P, prev if ok_prev else None, env, words_tl, pieces, doc, flags, rdir, flat)
    cs, cst, sel = draw_cut_sheets(edl, lab, P, prev, pieces, doc, tier, rdir, flat) if ok_prev else ([], 0, [])
    rv = {"schema": "resolve-editor/review@1", "edl": lab.relto(edl_path), "tier": tier,
          "preview": "preview.mov" if ok_prev else None, "frames": N, "report": "report.txt",
          "overview": [os.path.basename(x) for x in ov], "cuts": [os.path.basename(x) for x in cs],
          "tokens": {"overview": ovt, "cuts": cst}, "checks": "checks.json", "result": doc["result"],
          "seconds": round(time.time() - t0, 2)}
    rfn = write_json(os.path.join(rdir, "review.json"), rv)
    if as_json:
        print(json.dumps(rv, indent=1))
        return rv
    lines = check_lines(doc)
    from PIL import Image
    for x in ov + cs:
        with Image.open(x) as im:
            lines.append("IMAGE %s (%dx%d, about %d tokens)" % (posix(x), im.size[0], im.size[1], vis_tokens(*im.size)))
    print_result(doc["result"], lines, [rep, cfn, rfn] + ([prev] if ok_prev else []))
    return rv


# ------------------------------------------------------------------------------------------------ frames, srt, stem
def cmd_frames(lab, edl_path, times, frames=None, width=640, out=None, true_levels=False):
    edl = load_edl(edl_path)
    for part in [x for x in str(times or "").split(",") + str(frames or "").split(",") if x.strip()]:
        try:
            float(part)
        except ValueError:
            raise Fail("frames wants timeline seconds like 1.5,4 or --frames 40,100 (got %r)" % part)
    rdir = review_dir(edl_path)
    if not preview_fresh(edl, rdir):
        st, _ = render_preview(lab, edl_path)
        if st["result"] != "OK":
            raise Fail("the preview failed (%s); run preview to see why" % "; ".join(st["problems"]))
    f = float(tl_fps(edl))
    N = programme_frames(edl)
    want = []
    try:
        if frames:
            want = [int(x) for x in str(frames).split(",") if x.strip()]
        if times:
            want += [rnd(float(x) * f) for x in str(times).split(",") if x.strip()]
    except ValueError:
        raise Fail("frames wants timeline seconds like 1.5,4 or --frames 40,100 (got %r)" % (times or frames))
    want = sorted(set(min(max(0, x), N - 1) for x in want))
    if not want:
        raise Fail("give timeline seconds (T,T,...) or --frames N,N")
    raster = preview_size(int(edl["timeline"]["width"]), int(edl["timeline"]["height"]))
    w = min(int(width), MAX_SIDE)
    h = even(w * raster[1] / float(raster[0]))
    if h > MAX_SIDE:
        h = MAX_SIDE
        w = even(h * raster[0] / float(raster[1]))
    got = grab(os.path.join(rdir, "preview.mov"), want, w, h)
    od = os.path.abspath(out) if out else rdir
    os.makedirs(od, exist_ok=True)
    from PIL import Image
    # log footage (flat_log) is contrast stretched to be readable, like the contact sheets; --true-levels keeps the
    # preview's own levels (to compare with Resolve's grabs or a render by eye)
    flat = any(m.get("flat_log") for m in edl["media"].values()) and not true_levels
    for fr in want:
        if fr not in got:
            continue
        fn = os.path.join(od, "zoom_%06d.jpg" % fr)
        Image.fromarray(view_stretch(got[fr]) if flat else got[fr]).save(fn, quality=88)
        print("WROTE %s (frame %d, %.2f s, %s, about %d tokens%s)" % (
            posix(fn), fr, fr / f, tl_tc(edl, fr), vis_tokens(w, h),
            "; contrast stretched for log footage, not the preview's levels: --true-levels keeps them" if flat else
            "; the preview's own levels (--true-levels)" if true_levels else ""))


def srt_time(frame, fps):
    ms = int(Fraction(int(frame)) * 1000 / parse_fps(fps) + Fraction(1, 2))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return "%02d:%02d:%02d,%03d" % (h, m, s, ms)


def cmd_export_srt(lab, edl_path, out, spoken=None):
    """The captions as an SRT file. Two kinds: the picture's cues (what the preview draws: the file to burn into the
    picture) and every spoken cue (the picture's plus the ones suppress_under_titles kept off the picture under a
    title that repeats them: a caption file a platform shows over the video, never burned in). spoken None takes the
    kind from the preset's captions.deliver: "file" writes the spoken cues, "burn" (the default) the picture's."""
    edl = load_edl(edl_path)
    fps = tl_fps(edl)
    deliver = ((load_preset(edl.get("preset"), lab, required=False).get("captions") or {}).get("deliver")
               if edl.get("preset") else None) or "burn"
    if spoken is None:
        spoken = deliver == "file"
    sup = list(edl.get("captions_suppressed") or []) if spoken else []
    cues = sorted([c for tr in edl["tracks"].get("subtitle", []) for c in tr.get("items", [])] + sup,
                  key=lambda c: (int(c["rec_in"]), int(c["rec_out"])))
    if not cues:
        raise Fail("the EDL has no subtitle track (the cut list needs \"captions\": {\"from\": \"dialogue\"})")
    blocks = []
    for n, c in enumerate(cues, 1):
        lines = c.get("lines") or [str(c.get("text", ""))]
        blocks.append("%d\r\n%s --> %s\r\n%s\r\n" % (n, srt_time(c["rec_in"], fps), srt_time(c["rec_out"], fps),
                                                     "\r\n".join(str(l) for l in lines)))
    d = os.path.dirname(os.path.abspath(out))
    os.makedirs(d, exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="") as fh:
        fh.write("\r\n".join(blocks))
    print("WROTE %s" % posix(out))
    ns = len(edl.get("captions_suppressed") or [])
    if spoken:
        kind = ("every spoken cue, for a platform's caption file upload (shown over the video by the platform): do "
                "not burn this file into the picture%s" % (
                    " (%d cue%s here sit%s under a title that repeats %s on the picture)" % (
                        ns, "" if ns == 1 else "s", "s" if ns == 1 else "", "it" if ns == 1 else "them") if ns else ""))
    else:
        kind = ("the picture's captions, the same cues the preview draws: the file to burn in (Deliver > Subtitle "
                "Settings > Export Subtitle, Burn into video)%s" % (
                    "; %d spoken cue%s under a title %s left out (export-srt --spoken writes the upload file)" % (
                        ns, "" if ns == 1 else "s", "is" if ns == 1 else "are") if ns else ""))
    print("KIND %s (preset captions.deliver %s)" % ("spoken" if spoken else "picture", deliver))
    print("%d cues: %s. Times count from the start of the programme. In Resolve: File > Import > Subtitle, then drag "
          "the file onto the timeline at its first frame." % (len(cues), kind))


def cmd_stem(lab, edl_path, track, out):
    edl = load_edl(edl_path)
    ids = [t.get("id") for t in edl["tracks"]["audio"]]
    if track not in ids:
        raise Fail("no audio track %s in the EDL (tracks: %s)" % (track, ", ".join(ids) or "none"))
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    res, _ = mix_audio(edl, lab, os.path.abspath(out), only_track=track, want_metrics=False)
    print("WROTE %s" % posix(out))
    print("%s rendered with gains, fades and volume envelope: %d samples at %d Hz, peak %s dBFS" % (
        track, res["samples"], res["rate"], res["peak_dbfs"]))


# ------------------------------------------------------------------------------------ items, explain, mix (round 2)
def item_rows(edl):
    """Every item of the EDL as one flat row: track, id, kind, times, media, source, text and why."""
    f = float(tl_fps(edl))
    rows = []
    for kind in ("video", "audio", "subtitle"):
        for tr in edl["tracks"].get(kind, []):
            for it in tr.get("items", []):
                r = {"track": tr.get("id"), "id": it.get("id"), "kind": it.get("kind") or ("caption" if kind == "subtitle"
                                                                                             else "clip"),
                     "rec_in": int(it["rec_in"]), "rec_out": int(it["rec_out"]),
                     "at_s": round(int(it["rec_in"]) / f, 2), "dur_s": round(rec_len(it) / f, 2),
                     "media": it.get("media"), "src": [it.get("src_in"), it.get("src_out")] if is_media_item(it) else None,
                     "speed": speed_of(it) if is_media_item(it) else None}
                if kind == "audio":
                    r["gain_db"] = it.get("gain_db")
                    if it.get("duck"):
                        r["duck"] = it["duck"]
                if it.get("words") is not None:
                    r["words"] = it.get("words")
                if it.get("kind") == "title" or kind == "subtitle":
                    r["text"] = " / ".join(it.get("lines") or [str(it.get("text", ""))])
                    if it.get("role"):
                        r["role"] = it["role"]
                    if (it.get("look") or {}).get("id"):
                        r["look"] = it["look"]["id"]
                if it.get("tags"):
                    r["tags"] = list(it["tags"])
                if it.get("why"):
                    r["why"] = it["why"]
                rows.append(r)
    rows.sort(key=lambda r: (r["rec_in"], r["track"] or "", r["id"] or ""))
    return rows


def cmd_items(lab, edl_path, as_json=False):
    edl = load_edl(edl_path)
    rows = item_rows(edl)
    if as_json:
        print(json.dumps(rows, indent=1))
        return rows
    f = float(tl_fps(edl))
    print("ITEMS %d in %s (%.2f s at %s fps)" % (len(rows), posix(edl_path), programme_frames(edl) / f,
                                                fps_label(tl_fps(edl))))
    for r in rows:
        what = r.get("text") or ("%s %s-%s" % (r["media"], r["src"][0], r["src"][1]) if r.get("src") else "")
        extra = []
        if r.get("role"):
            extra.append("role %s" % r["role"])
        if r.get("gain_db") is not None:
            extra.append("%+.1f dB" % float(r["gain_db"]))
        if r.get("speed") not in (None, 1.0):
            extra.append("speed %g" % r["speed"])
        if r.get("tags"):
            extra.append("tags " + ",".join(r["tags"]))
        print("%-4s %-12s %-7s %7.2f %6.2f  %-40s %s%s" % (
            r["track"], r["id"], r["kind"], r["at_s"], r["dur_s"], what[:40], "; ".join(extra),
            ("  why: " + r["why"][:60]) if r.get("why") else ""))
    return rows


def knowledge_entries(ids, path=None, table_only=False):
    """{id: [lines]} for ids: the KNOWLEDGE.md section 14 table rows that name them, else (unless table_only) the
    first lines anywhere in KNOWLEDGE.md that mention them in backticks."""
    path = path or os.path.join(HERE, "KNOWLEDGE.md")
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return {i: [] for i in ids}
    m = re.search(r"(?m)^## 14\b.*?(?=^## \d|\Z)", text, re.S)
    sec = m.group(0) if m else ""
    out = {}
    for i in ids:
        tick = "`%s`" % i
        rows = [ln.strip() for ln in sec.splitlines() if ln.strip().startswith("|") and
                ln.split("|")[1].strip() == tick]
        if not rows and not table_only:
            # the whole sentences that name it (a paragraph's lines are joined first, so no sentence is cut)
            for para in re.split(r"\n\s*\n|\n(?=\s*(?:[-*] |\d+\. |\||#))", text):
                flat = re.sub(r"^(?:[-*] |\d+\. )", "", " ".join(ln.strip() for ln in para.splitlines()
                                                              if not ln.lstrip().startswith("#")))
                if tick not in flat or flat.startswith("|"):
                    continue
                # (a rule's bold label such as **M7** is dropped: section 7's music rules and section 15's
                # manual steps both count from M1)
                rows += [re.sub(r"^\*\*[A-Z]\d+\*\*\s*", "", x.strip())
                         for x in re.split(r"(?<=[.!?])\s+(?=[A-Z`(\"])", flat) if tick in x]
                if len(rows) >= 2:
                    break
            rows = rows[:2]
        out[i] = rows
    return out


VALIDATE_LEVELS = {
    # validate codes of the effects layer (an error is a STOP through edl_invalid in check and review)
    "fx_unknown": "STOP", "fx_span": "WARN", "fx_still_untested": "STOP", "retime_range": "STOP",
    "retime_speed": "STOP", "transition_unknown": "STOP", "transition_handles": "STOP",
    "transition_through_black": "STOP", "anim_unknown": "STOP", "fx_edges": "STOP",
    # the EDL mix block's gain outside -20 to +12 dB, or not a number
    "bad_mix": "STOP",
}
EXPLAIN_LISTS = ("transitions", "fx", "anims", "genres")


def explain_catalog(i):
    """Lines that describe a fx_catalog.json key (a transition, an fx kind, an anim or a genre), or a whole list."""
    cat = fx_lab.load_catalog()
    if i in EXPLAIN_LISTS:
        rows = cat.get(i) or {}
        out = []
        for k in sorted(rows, key=lambda k: (str(rows[k].get("prio", "")), k)):
            r = rows[k]
            if i == "genres":
                out.append("%s: %.1f events per 10 s, %d stylized families, zoom cap %.2f, avoids %s" % (
                    k, r["per10s"], r["max_families"], r["zoom_max"], ", ".join(r.get("banned") or []) or "nothing"))
                continue
            st = "on" if r.get("enabled", True) else ("off (%s)" % ("never" if r.get("never") else
                                                                    "look_is_brief" if r.get("brief_ok") else "refused"))
            pv = (r.get("preview") or {}).get("class", "")
            out.append("%s %s %s%s%s" % (k, r.get("prio", ""), st, ("  preview %s" % pv) if pv else "",
                                         ("  gate %s" % r["gate"]) if r.get("gate") else ""))
        return out
    for sec in ("transitions", "fx", "anims", "genres"):
        r = (cat.get(sec) or {}).get(i)
        if r is None:
            continue
        if sec == "genres":
            return ["genre: %.1f events per 10 s, %d stylized families, zoom cap %.2f, avoids %s" % (
                r["per10s"], r["max_families"], r["zoom_max"], ", ".join(r.get("banned") or []) or "nothing")]
        out = ["%s %s, %s%s" % (sec[:-1] if sec != "fx" else "fx kind", r.get("prio", ""),
                                "enabled" if r.get("enabled", True) else "switched off",
                                (" (gate %s)" % r["gate"]) if r.get("gate") else "")]
        if r.get("what"):
            out.append("what: %s" % r["what"])
        res = r.get("resolve") or {}
        if res.get("type"):
            out.append("Resolve: %r / %s, build %s" % (res["type"], res.get("category"), res.get("build")))
        elif res.get("build"):
            out.append("build: %s" % res["build"])
        if r.get("frames"):
            fr_ = r["frames"]
            out.append("frames at %s fps: %s to %s" % (fr_.get("at_fps"), fr_.get("min"), fr_.get("max")))
        if r.get("params"):
            out.append("params ([min, max, default], ms where named so): %s" % json.dumps(r["params"]))
        pv = r.get("preview") or {}
        if pv:
            out.append("preview: %s (%s)%s; pixel check %s" % (pv.get("recipe"), pv.get("class"),
                                                               ("; " + pv["measured"]) if pv.get("measured") else "",
                                                               r.get("tolerance")))
        for k in ("use_when", "amateur_when", "refuse", "only"):
            if r.get(k):
                out.append("%s: %s" % (k.replace("_", " "), r[k]))
        if r.get("genres", {}).get("avoid"):
            out.append("avoid in: %s" % ", ".join(r["genres"]["avoid"]))
        if r.get("source"):
            out.append("research reference (the research notes do not ship with the skill): %s" % r["source"])
        return out
    if i in ("whip", "zoom"):
        k1, k2 = ("custom_whip", "whip_pair") if i == "whip" else ("custom_zoom_overlap", "custom_zoom_through")
        return (["generic transition type: assemble picks the route from the handles the two shots have",
                "with handles (the measured rule of `E explain transition_handles`): %s, a Fusion transition (`E "
                "explain %s`)" % (k1, k1),
                "without them: %s, a pair of clip comps keyed on the last frames of the outgoing shot and the first "
                "frames of the incoming one (`E explain %s`)" % (k2, k2),
                "the assemble report's fx_routes names the route taken; naming %s or %s forces it" % (k1, k2)]
                + (["direction: the way the PICTURE travels on screen (left, right, up, down); a camera panning "
                    "right moves the picture left, so a whip that follows a right pan says left"]
                   if i == "whip" else ["peak (zoom at the cut, default 2.5) and point ([x, y] in frame fractions "
                                        "from the top left: what the zoom dives into)"]))
    return []


def cmd_explain(ids):
    """What each id means and how to fix it, from KNOWLEDGE.md section 14 and fx_catalog.json (lab-free).
    `explain transitions` (or fx, anims, genres) lists the catalogue."""
    levels = dict(CHECK_LEVELS)
    levels.update(globals().get("VERIFY_LEVELS") or {})
    for k, v in VALIDATE_LEVELS.items():
        levels.setdefault(k, v)
    found = knowledge_entries(ids)
    rows14 = knowledge_entries(ids, table_only=True)
    miss = 0
    for i in ids:
        lvl = levels.get(i)
        cat_ = explain_catalog(i)
        # a catalogue key is explained by its catalogue row (plus a glossary row when one exists); the loose
        # KNOWLEDGE sentences that happen to name it in backticks are about something else (`zoom` the field)
        rows = (rows14.get(i) or []) if cat_ else (found.get(i) or [])
        print("%s%s" % (i, (" (%s)" % lvl) if lvl else ""))
        if not rows and not cat_:
            miss += 1
            print("  not documented in KNOWLEDGE.md; run `E commands` and read KNOWLEDGE.md section 14")
        for r in rows:
            cells = [c.strip() for c in r.strip("|").split("|")] if r.startswith("|") else [r]
            print("  " + " | ".join(c for c in cells[1:] if c) if r.startswith("|") else "  " + r)
        for ln in cat_:
            print("  " + ln)
    return miss


def _meter(path):
    """Integrated loudness (LUFS) and true peak (dBTP) of a file by ffmpeg's EBU R128 meter."""
    r = run([ffmpeg_bin(), "-nostdin", "-hide_banner", "-nostats", "-i", path, "-af", "ebur128=peak=true", "-f", "null",
             "-"], "ffmpeg (loudness)")
    log = r.stderr.decode("utf-8", "replace") if isinstance(r.stderr, bytes) else str(r.stderr)
    summ = log[log.rfind("Summary:"):]

    def grab(pat, block):
        mm = re.search(pat + r"\s*(-?[\d.]+|-inf)", block)
        try:
            v = float(mm.group(1)) if mm else None
        except ValueError:
            return None
        return None if v is None or math.isinf(v) or math.isnan(v) else v
    i_ = grab(r"I:", summ)
    return {"I": i_ if i_ is None or i_ > -70.5 else None,
            "TP": grab(r"Peak:", summ[summ.find("True peak:"):]) if "True peak:" in summ else None}


def _sliding_min(a, before, after):
    """min(a[n - before : n + after + 1]) for every n (van Herk, linear time)."""
    w = before + after + 1
    p = np.concatenate([np.ones(before), a, np.ones(after)])
    nb = -(-len(p) // w)
    q = np.concatenate([p, np.ones(nb * w - len(p))]).reshape(nb, w)
    pre = np.minimum.accumulate(q, axis=1).ravel()
    suf = np.minimum.accumulate(q[:, ::-1], axis=1)[:, ::-1].ravel()
    i = np.arange(len(a))
    return np.minimum(suf[i], pre[i + w - 1])


def true_peak_env(x):
    """Per-sample true peak (linear) of an (n, ch) signal: the largest of the samples and a 4x windowed-sinc
    interpolation between them."""
    k = np.arange(-48, 49)
    tp = np.abs(x).max(axis=1).astype(np.float64)
    for ph in (1, 2, 3):
        t = k - ph / 4.0
        h = np.sinc(t) * np.kaiser(len(t), 8.0)
        h /= h.sum()
        for c in range(x.shape[1]):
            tp = np.maximum(tp, np.abs(np.convolve(x[:, c].astype(np.float64), h[::-1], mode="same")))
    return tp


def tp_limit(x, ceiling_db, sr=SR, look_ms=5.0, release_ms=60.0):
    """A look-ahead true-peak limiter: the gain each sample needs is held back 5 ms ahead and 60 ms after, then
    smoothed over 5 ms, so a peak is already turned down when it arrives."""
    ceil = 10 ** (ceiling_db / 20.0)
    need = np.minimum(1.0, ceil / np.maximum(true_peak_env(x), 1e-12))
    la, rel = max(1, int(sr * look_ms / 1000.0)), max(1, int(sr * release_ms / 1000.0))
    gm = _sliding_min(need, rel, la)
    cs = np.concatenate([[0.0], np.cumsum(gm)])
    i = np.arange(len(gm))
    lo = np.maximum(0, i - la + 1)
    g = (cs[i + 1] - cs[lo]) / (i + 1 - lo)
    return (x * g[:, None]).astype(np.float32)


def write_wav24(path, x, sr=SR):
    v = np.clip(np.asarray(x, np.float64), -1.0, 1.0 - 2 ** -23)
    iv = np.round(v * 8388608.0).astype("<i4")
    b = iv.view(np.uint8).reshape(-1, 4)[:, :3].tobytes()
    with wave.open(path, "wb") as w:
        w.setnchannels(x.shape[1])
        w.setsampwidth(3)
        w.setframerate(sr)
        w.writeframes(b)


def cmd_mix(lab, edl_path, out, as_json=False):
    """The preview mixer's stereo print, turned to the preset's loudness and true-peak limited at codec_tp_db minus
    0.5 dB; re-measured (at most 3 passes). Writes OUT.wav (24-bit) and OUT.json."""
    edl = load_edl(edl_path)
    P = load_preset(edl.get("preset"), lab, required=False)
    au = P.get("audio") or {}
    target = float(au.get("lufs", -14.0))
    ceiling = float(au.get("codec_tp_db", -2.0)) - 0.5
    out = os.path.abspath(out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    raw = out + ".raw.wav"
    try:
        mix_audio(edl, lab, raw, want_metrics=False)
        m0 = _meter(raw)
        if m0["I"] is None:
            raise Fail("the mix is silent: nothing to normalise")
        with wave.open(raw) as w:
            x = np.frombuffer(w.readframes(w.getnframes()), "<i2").reshape(-1, w.getnchannels()).astype(np.float32) / 32768
    finally:
        if os.path.exists(raw):
            os.remove(raw)
    gain, ceil_, passes, m = target - m0["I"], ceiling, 0, None
    while passes < 3:
        passes += 1
        y = tp_limit(x * np.float32(10 ** (gain / 20.0)), ceil_)
        write_wav24(out, y)
        m = _meter(out)
        ok_tp = m["TP"] is None or m["TP"] <= ceiling + 0.1
        ok_i = m["I"] is not None and abs(m["I"] - target) <= 0.3
        if ok_tp and ok_i:
            break
        if not ok_tp:
            ceil_ -= (m["TP"] - ceiling) + 0.05
        if not ok_i and m["I"] is not None:
            gain += target - m["I"]
    res = {"schema": "resolve-editor/mix@1", "edl": lab.relto(edl_path), "lufs_in": round(m0["I"], 2),
           "tp_in": m0["TP"], "gain_db": round(gain, 2), "lufs_out": m["I"], "tp_out": m["TP"], "passes": passes,
           "target_lufs": target, "ceiling_dbtp": round(ceiling, 2), "wav": posix(out)}
    js = os.path.splitext(out)[0] + ".json"
    write_json(js, res)
    if as_json:
        print(json.dumps(res, indent=1))
        return res
    ok = m["I"] is not None and abs(m["I"] - target) <= 0.5 and (m["TP"] is None or m["TP"] <= ceiling + 0.1)
    print_result("OK" if ok else "WARN", ["mix %.1f LUFS -> %s LUFS (target %g), gain %+.1f dB, true peak %s dBTP "
                                          "(ceiling %g), %d pass%s" % (m0["I"], m["I"], target, gain, m["TP"], ceiling,
                                                                       passes, "" if passes == 1 else "es")] +
                 ([] if ok else ["the limiter could not reach the target without pushing past the ceiling: lower "
                                 "the loudest items (music) or accept the level it reached"]), [out, js])
    return res


def cmd_level(lab, edl_path, out, as_json=False):
    """OUT.json = the EDL with the mix gain that brings its fresh preview to the preset's loudness, as far as the
    true peak allows under the codec ceiling ("mix": {"gain_db", "by": "level", "lufs_in", "tp_in", "target_lufs"});
    the residual is left for the Deliver page. Needs a fresh preview of the EDL (`E preview EDL`)."""
    edl = load_edl(edl_path)
    P = load_preset(edl.get("preset"), lab, required=False)
    rdir = review_dir(edl_path)
    st = preview_state(rdir) if preview_fresh(edl, rdir) else None
    au = dict((st or {}).get("audio") or {})
    if st is not None and "lufs_i" not in au and os.path.exists(os.path.join(rdir, "preview.mov")):
        # a fresh preview from before the loudness measure: measure its sound now (as the checks do)
        li_, tp_ = measure_lufs(os.path.join(rdir, "preview.mov"))
        if li_ is not None and math.isfinite(li_) and li_ >= -70:
            au.update(lufs_i=round(li_, 2), tp_dbtp=None if tp_ is None or not math.isfinite(tp_) else round(tp_, 2))
    if st is None or au.get("lufs_i") is None:
        res = {"schema": "resolve-editor/level@1", "edl": posix(edl_path), "result": "STOP",
               "why": "no fresh preview with a loudness measure"}
        if as_json:
            print(json.dumps(res, indent=1))
            return res
        print_result("STOP", ["preview_stale: %s has no fresh preview with a loudness measure: run `E preview %s` "
                              "first, then level again" % (posix(edl_path), posix(edl_path))])
        return res
    au["mix_gain_db"] = mix_gain_of(edl)
    ld = loudness_stats(P, au)
    total = round(min(MIX_GAIN_RANGE[1], max(MIX_GAIN_RANGE[0], ld["mix_gain_db"] + ld["gain_more_db"])), 1)
    new = copy.deepcopy(edl)
    new["mix"] = {"gain_db": total, "by": "level", "lufs_in": ld["lufs_i"], "tp_in": ld["tp_dbtp"],
                  "target_lufs": ld["target"]}
    note = "level: mix gain %+.1f dB (preview %.1f LUFS, true peak %s dBTP, target %g LUFS, %.1f LU left)" % (
        total, ld["lufs_i"], ld["tp_dbtp"], ld["target"], ld["residual_lu"])
    if not isinstance(new.get("version"), dict):
        new["version"] = {}
    v = new["version"]
    v["summary"] = ("%s; %s" % (v.get("summary"), note)) if v.get("summary") else note
    out = os.path.abspath(out)
    write_json(out, new)
    res = {"schema": "resolve-editor/level@1", "edl": posix(edl_path), "out": posix(out),
           "result": "OK" if ld["residual_lu"] <= ld["tol_lu"] else "WARN", "gain_db": total,
           "was_db": ld["mix_gain_db"], "gain_more_db": ld["gain_more_db"], "lufs_in": ld["lufs_i"],
           "tp_in": ld["tp_dbtp"], "target_lufs": ld["target"], "codec_tp_db": ld["codec_tp_db"],
           "residual_lu": ld["residual_lu"]}
    if as_json:
        print(json.dumps(res, indent=1))
        return res
    lines = ["mix gain %+.1f dB (was %+.1f): %.1f LUFS -> about %.1f LUFS (target %g), true peak %s -> about %s dBTP "
             "(codec ceiling %g)" % (total, ld["mix_gain_db"], ld["lufs_i"], ld["lufs_i"] + ld["gain_more_db"],
                                     ld["target"], ld["tp_dbtp"],
                                     None if ld["tp_dbtp"] is None else round(ld["tp_dbtp"] + ld["gain_more_db"], 1),
                                     ld["codec_tp_db"])]
    if ld["residual_lu"] > ld["tol_lu"]:
        lines.append("residual %.1f LU: the true peak caps the gain; normalise the rest on the Deliver page (Audio: "
                     "Normalize Audio Levels, Optimize to Standard) and say so in the hand-over" % ld["residual_lu"])
    lines.append("preview OUT.json again to confirm (or copy its mix block into the cut list)")
    print_result(res["result"], lines, [out])
    return res


def cmd_edl_from_dump(lab, out=None):
    dump = load_json(lab.p("resolve", "dump.json"))
    tl = dump.get("timeline")
    if not tl:
        raise Fail("dump.json has no current timeline")
    tfps = parse_fps(tl.get("fps") or "25/1")
    idx = {canon_path(m.get("path")): mid for mid, m in lab.index.get("media", {}).items()}
    dmed = {canon_path(m.get("path")): m for m in dump.get("media", [])}
    media, notes = {}, []

    def mid_for(path):
        c = canon_path(path)
        if c in idx:
            mid = idx[c]
            if mid not in media:
                media[mid] = edl_media_entry(lab, mid, tfps)
            return mid
        base = safe_name(os.path.splitext(os.path.basename(str(path)))[0])
        mid, k = base, 2
        while mid in media and canon_path(media[mid]["path"]) != c:
            mid, k = "%s_%d" % (base, k), k + 1
        if mid not in media:
            dm = dmed.get(c) or {}
            typ = str(dm.get("type") or "")
            kind = "audio" if typ.lower().startswith("audio") else ("image" if typ.lower() == "still" else "av")
            try:
                f = fps_str(dm.get("fps")) if kind == "av" else fps_str(tfps)
            except (ValueError, TypeError):
                f = fps_str(tfps)
            wh = str(dm.get("resolution") or "").lower().split("x")
            media[mid] = {"kind": kind, "path": path, "hash": None, "fps": f,
                          "width": int(wh[0]) if len(wh) == 2 and wh[0].isdigit() else None,
                          "height": int(wh[1]) if len(wh) == 2 and wh[1].isdigit() else None,
                          "fps_from": "media" if kind == "av" else "timeline",
                          "frames": int(dm["frames"]) if str(dm.get("frames") or "").isdigit() else None,
                          "start_tc": dm.get("start_tc") or "00:00:00:00", "proxy": None, "wav": None, "words": None,
                          "mpi_uid": dm.get("mpi_uid")}
            notes.append("media %s is not in the lab index (no proxy): add it with media_lab.py for a preview" % mid)
        return mid
    tracks = {"video": [], "audio": [], "subtitle": []}
    sem = load_semantics()
    shim = {"timeline": {"width": int(tl.get("width") or 1920), "height": int(tl.get("height") or 1080),
                         "resolve": {"input_sizing": tl.get("input_sizing")
                                     or ((dump.get("project") or {}).get("settings") or {}).get("timelineInputResMismatchBehavior")}},
            "media": media}
    for kind in ("video", "audio"):
        for t in (tl.get("tracks") or {}).get(kind, []):
            items = []
            trs = [x for x in t.get("items", []) if x.get("type") == "transition" and x.get("rec_in") is not None]
            for k, it in enumerate(t.get("items", [])):
                if it.get("type") == "transition":
                    continue
                if not it.get("path"):
                    notes.append("%s %d item %r has no media (title or generator): skipped" % (kind, t.get("index"), it.get("name")))
                    continue
                mid = mid_for(it["path"])
                sp = float(it.get("speed") or 1.0) or 1.0
                ri, ro = int(it["rec_in"]), int(it["rec_out"])
                row = {"id": "%s%d_%03d" % ("v" if kind == "video" else "a", int(t.get("index", 1)), k + 1), "kind": "clip",
                       "media": mid, "src_in": int(it.get("src_in") or 0), "rec_in": ri, "rec_out": ro, "speed": sp,
                       "enabled": bool(it.get("enabled", True))}
                mf = tfps if media[mid].get("fps_from") == "timeline" else parse_fps(media[mid]["fps"])
                if sem.get("source_time_includes_transition_handle") is True:
                    for x in trs:
                        if int(x["rec_in"]) < ri < int(x["rec_out"]):
                            row["src_in"] += rnd((ri - int(x["rec_in"])) * sp * float(mf / tfps))
                row["src_out"] = row["src_in"] + max(1, rnd((ro - ri) * sp * float(mf / tfps)))
                fades = it.get("fades") or [0, 0]
                row["fade_in"], row["fade_out"] = int(round(float(fades[0] or 0))), int(round(float(fades[1] or 0)))
                pr = it.get("props") or {}
                if kind == "video":
                    ku = image_units(shim, mid) if sem.get("pan_tilt_units") == "image" else None
                    kx, ky = ku or (1.0, 1.0)
                    psign = 1.0 if sem.get("pan_positive", "right") == "right" else -1.0
                    tsign = 1.0 if sem.get("tilt_positive", "up") == "up" else -1.0
                    row["transform"] = {"zoom": float(pr.get("ZoomX", 1.0) or 1.0),
                                        "pan_px": round(float(pr.get("Pan", 0) or 0) * kx * psign, 2),
                                        "tilt_px": round(float(pr.get("Tilt", 0) or 0) * ky * tsign, 2),
                                        "rotation": float(pr.get("RotationAngle", 0) or 0),
                                        "opacity": float(pr.get("Opacity", 100) or 100)}
                    row["why"] = "from the Resolve timeline"
                else:
                    row["gain_db"] = float(pr.get("AudioVolume", 0) or 0)
                    row["pan"] = float(pr.get("AudioPan", 0) or 0)
                items.append(row)
            tr = {"id": ("V%d" if kind == "video" else "A%d") % int(t.get("index", 1)), "name": t.get("name") or "",
                  "items": items}
            if kind == "audio":
                tr["role"] = "other"
            tracks[kind].append(tr)
    edl = {"schema": EDL_SCHEMA, "version": {"id": "v000", "parent": None, "by": "resolve", "created": iso_now(),
                                              "summary": "timeline %s as it was in Resolve" % tl.get("name"), "changes": []},
           "preset": lab.project.get("preset"), "platform": None,
           "timeline": {"name": tl.get("name"), "fps": fps_str(tfps), "drop_frame": is_drop_tc(tl.get("start_tc") or ""),
                        "width": int(tl.get("width") or 1920), "height": int(tl.get("height") or 1080),
                        "start_tc": tl.get("start_tc") or "01:00:00:00", "audio_rate": SR,
                        "resolve": {"input_sizing": shim["timeline"]["resolve"]["input_sizing"]}},
           "media": media, "tracks": tracks, "transitions": [], "markers": [], "music": None,
           "targets": {"duration_frames": None}, "checks_off": [], "notes": notes}
    out = out or lab.p("edits", "v000.json")
    write_json(out, edl)
    for n in notes:
        print("note: %s" % n)
    print("WROTE %s" % posix(out))
    return edl


# ------------------------------------------------------------------------------------------------ FCPXML handoff
def _fx_t(secs):
    secs = Fraction(secs)
    if secs == 0:
        return "0s"
    return "%ds" % secs.numerator if secs.denominator == 1 else "%d/%ds" % (secs.numerator, secs.denominator)


def _file_url(p):
    import urllib.parse
    q = posix(p)
    if not q.startswith("/"):
        q = "/" + q
    return "file://" + urllib.parse.quote(q, safe="/:")


def cmd_export_fcpxml(lab, edl_path, out):
    """Handoff timeline for File > Import > Timeline: FCPXML 1.10 with exact rational times. Clips on V1 form the
    spine (gaps fill holes), higher video tracks and titles ride on lanes above, audio that is not the V1 clip's own
    sound rides on lanes below. Speed uses a timeMap; gain and static transforms are written; fades and envelopes
    are not (Resolve ignores them on FCPXML import)."""
    from xml.sax.saxutils import escape, quoteattr
    edl = load_edl(edl_path)
    R = tl_fps(edl)
    W, H = int(edl["timeline"]["width"]), int(edl["timeline"]["height"])
    drop = bool(edl["timeline"].get("drop_frame")) or is_drop_tc(edl["timeline"].get("start_tc") or "")
    tc0 = tc_to_frames(edl["timeline"].get("start_tc") or "00:00:00:00", R, drop)
    N = programme_frames(edl)
    notes, res, ids = [], [], {}
    counter = [0]
    mix_g = float((edl.get("mix") or {}).get("gain_db") or 0.0)     # the EDL mix gain (I1) on every audio item

    def nid():
        counter[0] += 1
        return "r%d" % counter[0]
    ids["tl"] = nid()
    res.append('<format id="%s" frameDuration="%s" width="%d" height="%d" colorSpace="1-1-1 (Rec. 709)"/>'
               % (ids["tl"], _fx_t(1 / R), W, H))
    used = sorted({it["media"] for k, tr, it in iter_items(edl, ("video", "audio")) if is_media_item(it)})
    info = {}
    for mid in used:
        m = edl["media"][mid]
        mf = media_fps(edl, mid)
        try:
            a_start = Fraction(tc_to_frames(m.get("start_tc") or "00:00:00:00", mf, is_drop_tc(m.get("start_tc") or "")), 1) / mf
        except ValueError:
            a_start = Fraction(0)
        frames = int(m.get("frames") or 0) or int(max(int(it["src_out"]) for k, tr, it in iter_items(edl, ("video", "audio"))
                                                     if it.get("media") == mid))
        fid, aid = nid(), nid()
        kind = m.get("kind", "av")
        info[mid] = {"fps": mf, "start": a_start, "dur": Fraction(frames) / mf, "fmt": fid, "asset": aid,
                     "asset_v": aid, "asset_a": aid}
        res.append('<format id="%s" frameDuration="%s" width="%d" height="%d" colorSpace="1-1-1 (Rec. 709)"/>' % (
            fid, _fx_t(1 / mf), int(m.get("width") or W), int(m.get("height") or H)))
        nm, src = quoteattr(os.path.splitext(os.path.basename(str(m.get("path"))))[0]), quoteattr(_file_url(m.get("path")))
        au_ = m.get("audio") or {}
        t1 = au_.get("track1") or {}
        ach = 2 if t1.get("type") == "stereo" else (1 if t1.get("type") == "mono" else int(au_.get("channels") or 2))
        res.append('<asset id="%s" name=%s start="%s" duration="%s" hasVideo="%d" hasAudio="%d" format="%s" '
                   'audioSources="1" audioChannels="%d" audioRate="48000"><media-rep kind="original-media" src=%s/></asset>'
                   % (aid, nm, _fx_t(a_start), _fx_t(info[mid]["dur"]), 0 if kind == "audio" else 1,
                      1 if kind in ("av", "audio") else 0, fid, ach, src))
        # Resolve 21.1 ignores srcEnable on import: a picture-only or sound-only use of an A/V file must point at
        # an asset that has only that part, or the camera sound comes along at 0 dB (measured in the sandbox)
        if kind == "av":
            info[mid]["asset_v"], info[mid]["asset_a"] = nid(), nid()
            res.append('<asset id="%s" name=%s start="%s" duration="%s" hasVideo="1" hasAudio="0" format="%s">'
                       '<media-rep kind="original-media" src=%s/></asset>'
                       % (info[mid]["asset_v"], nm, _fx_t(a_start), _fx_t(info[mid]["dur"]), fid, src))
            res.append('<asset id="%s" name=%s start="%s" duration="%s" hasVideo="0" hasAudio="1" format="%s" '
                       'audioSources="1" audioChannels="%d" audioRate="48000"><media-rep kind="original-media" src=%s/>'
                       '</asset>' % (info[mid]["asset_a"], nm, _fx_t(a_start), _fx_t(info[mid]["dur"]), fid, ach, src))
    ids["dis"], ids["title"] = nid(), nid()
    res.append('<effect id="%s" name="Cross Dissolve" uid="FxPlug:4731E73A-8DAC-4113-9A30-AE85B1761265"/>' % ids["dis"])
    res.append('<effect id="%s" name="Basic Title" uid=".../Titles.localized/Bumper:Opener.localized/Basic Title.localized/'
               'Basic Title.moti"/>' % ids["title"])
    v1 = [it for it in sorted_items(v1_track(edl)) if it.get("kind", "clip") in ("clip", "solid")]
    spine, head = [], 0
    for it in v1:
        if int(it["rec_in"]) > head:
            spine.append({"gap": True, "rec": [head, int(it["rec_in"])]})
        if it.get("kind") == "solid" or edl["media"].get(it.get("media"), {}).get("kind") == "image":
            notes.append("%s: solids and stills become gaps in the FCPXML" % it["id"])
            spine.append({"gap": True, "rec": [int(it["rec_in"]), int(it["rec_out"])]})
        else:
            spine.append({"gap": False, "rec": [int(it["rec_in"]), int(it["rec_out"])], "it": it})
        head = int(it["rec_out"])
    if head < N or not spine:
        spine.append({"gap": True, "rec": [head, max(N, head + 1)]})

    def local_start(el):
        if el["gap"]:
            return Fraction(0)
        it = el["it"]
        mi = info[it["media"]]
        base = mi["start"] + Fraction(int(it["src_in"])) / mi["fps"]
        sp = Fraction(speed_of(it)).limit_denominator(1000)
        return base if sp == 1 else mi["start"] + (base - mi["start"]) / sp

    def parent(rec):
        for el in spine:
            if el["rec"][0] <= rec < el["rec"][1]:
                return el
        return spine[-1]

    def anchor_off(rec):
        el = parent(rec)
        return el, local_start(el) + Fraction(rec - el["rec"][0]) / R
    img_units = load_semantics().get("pan_tilt_units") == "image"

    def fx_transform(it):
        """Resolve reads position x as Pan * 100 / timeline height, and Pan moves the image by (image width after
        input sizing / timeline width) pixels per unit, the same factor the build divides by."""
        tf = it.get("transform") or {}
        # an imported timeline takes the project's input sizing, so say per clip how it fills the frame
        conf = {"scaleToCrop": "fill", "crop": "fill", "fill": "fill", "centerCrop": "none"}.get(
            (edl["timeline"].get("resolve") or {}).get("input_sizing") or "", "")
        conf = '<adjust-conform type="%s"/>' % conf if conf else ""
        if not (float(tf.get("zoom", 1.0)) != 1.0 or tf.get("pan_px") or tf.get("tilt_px")):
            return conf
        ku = (image_units(edl, it["media"]) if img_units else None) or (1.0, 1.0)
        return conf + '<adjust-transform position="%.4f %.4f" scale="%g %g"/>' % (
            float(tf.get("pan_px", 0)) / ku[0] * 100.0 / H, float(tf.get("tilt_px", 0)) / ku[1] * 100.0 / H,
            float(tf.get("zoom", 1.0)), float(tf.get("zoom", 1.0)))
    linked_audio = set()
    for el in spine:
        if el["gap"]:
            continue
        v = el["it"]
        for tr in edl["tracks"]["audio"]:
            for a in tr.get("items", []):
                if (v.get("link") and a.get("link") == v.get("link") and a["media"] == v["media"]
                        and int(a["rec_in"]) == int(v["rec_in"]) and int(a["rec_out"]) == int(v["rec_out"])
                        and int(a["src_in"]) == int(v["src_in"]) and speed_of(a) == speed_of(v)):
                    linked_audio.add(a["id"])
                    el["audio"] = a
    anchored = {}

    def add(rec, xml):
        el, off = anchor_off(rec)
        anchored.setdefault(id(el), []).append(xml.replace("@OFF@", _fx_t(off)))
    for tr in edl["tracks"]["video"]:
        lane = track_num(tr.get("id")) - 1
        if lane < 1:
            continue
        for it in sorted_items(tr):
            dur = _fx_t(Fraction(rec_len(it)) / R)
            if it.get("kind") == "title":
                sid = "ts_%s" % safe_name(it["id"])
                add(int(it["rec_in"]), '<title ref="%s" lane="%d" name=%s offset="@OFF@" start="3600s" duration="%s">'
                    '<text><text-style ref="%s">%s</text-style></text><text-style-def id="%s"><text-style font="Helvetica" '
                    'fontSize="%d" fontColor="1 1 1 1" alignment="center"/></text-style-def></title>'
                    % (ids["title"], lane, quoteattr(it["id"]), dur, sid, escape(str(it.get("text", ""))), sid,
                       int(it.get("font_px") or 60)))
            elif is_media_item(it) and edl["media"][it["media"]].get("kind") != "image":
                mi = info[it["media"]]
                adj = fx_transform(it)
                if speed_of(it) != 1.0:
                    notes.append("%s: a retimed clip above V1 is written at speed 1 in the FCPXML" % it["id"])
                add(int(it["rec_in"]), '<asset-clip ref="%s" lane="%d" name=%s offset="@OFF@" start="%s" duration="%s" '
                    'format="%s" tcFormat="NDF">%s</asset-clip>'
                    % (mi["asset_v"], lane, quoteattr(it["id"]), _fx_t(mi["start"] + Fraction(int(it["src_in"])) / mi["fps"]),
                       dur, mi["fmt"], adj))
    for tr in edl["tracks"]["audio"]:
        lane = -track_num(tr.get("id"))
        for it in sorted_items(tr):
            if not is_media_item(it) or it["id"] in linked_audio:
                continue
            mi = info[it["media"]]
            if speed_of(it) != 1.0:
                notes.append("%s: retimed audio is written at speed 1 in the FCPXML" % it["id"])
            g_ = float(it.get("gain_db", 0) or 0) + mix_g          # the EDL mix gain rides on every audio item
            vol = ('<adjust-volume amount="%gdB"/>' % (round(g_, 3) if mix_g else g_)) if g_ else ""
            if it.get("volume_env"):
                notes.append("%s: the ducking envelope is not written (Resolve ignores FCPXML levels); use `stem`" % it["id"])
            add(int(it["rec_in"]), '<asset-clip ref="%s" lane="%d" name=%s offset="@OFF@" start="%s" duration="%s" '
                'format="%s" tcFormat="NDF" audioRole="%s">%s</asset-clip>'
                % (mi["asset_a"], lane, quoteattr(it["id"]), _fx_t(mi["start"] + Fraction(int(it["src_in"])) / mi["fps"]),
                   _fx_t(Fraction(rec_len(it)) / R), mi["fmt"], "music" if tr.get("role") == "music" else "dialogue", vol))
    for mk in edl.get("markers", []):
        el, off = anchor_off(int(mk["frame"]))
        anchored.setdefault(id(el), []).append('<marker start="%s" duration="%s" value=%s note=%s/>' % (
            _fx_t(off), _fx_t(1 / R), quoteattr(str(mk.get("name", ""))), quoteattr(str(mk.get("note", "")))))
    trans = {}
    for x in edl.get("transitions", []):
        if str(x.get("track", "V1")) != "V1":
            notes.append("transition %s on %s is not written (V1 only)" % (x.get("id"), x.get("track")))
            continue
        a = [it for it in v1 if it["id"] == x.get("from")]
        if a:
            trans[int(a[0]["rec_out"])] = x
    out_sp = []
    for el in spine:
        a0, a1 = el["rec"]
        body = "".join(anchored.get(id(el), []))
        if el["gap"]:
            out_sp.append('<gap name="Gap" offset="%s" start="0s" duration="%s">%s</gap>' % (
                _fx_t(Fraction(a0) / R), _fx_t(Fraction(a1 - a0) / R), body))
        else:
            it = el["it"]
            mi = info[it["media"]]
            sp = Fraction(speed_of(it)).limit_denominator(1000)
            inner = ""
            if sp != 1:
                inner += ('<timeMap><timept time="%s" value="%s" interp="linear"/><timept time="%s" value="%s" '
                          'interp="linear"/></timeMap>' % (_fx_t(mi["start"]), _fx_t(mi["start"]),
                                                           _fx_t(mi["start"] + mi["dur"] / sp), _fx_t(mi["start"] + mi["dur"])))
            inner += fx_transform(it)
            au = el.get("audio")
            if au is not None and float(au.get("gain_db", 0) or 0) + mix_g:
                g_ = float(au.get("gain_db", 0) or 0) + mix_g
                inner += '<adjust-volume amount="%gdB"/>' % (round(g_, 3) if mix_g else g_)
            out_sp.append('<asset-clip ref="%s" name=%s offset="%s" start="%s" duration="%s" format="%s" tcFormat="NDF">'
                          '%s%s</asset-clip>' % (mi["asset"] if au is not None else mi["asset_v"], quoteattr(it["id"]),
                                                 _fx_t(Fraction(a0) / R), _fx_t(local_start(el)),
                                                 _fx_t(Fraction(a1 - a0) / R), mi["fmt"], inner, body))
        if a1 in trans:
            x = trans[a1]
            d = int(x.get("frames", 12))
            al = x.get("alignment", "center")
            w0 = a1 - d // 2 if al == "center" else (a1 - d if al == "left" else a1)
            out_sp.append('<transition name="Cross Dissolve" offset="%s" duration="%s"><filter-video ref="%s" '
                          'name="Cross Dissolve"/></transition>' % (_fx_t(Fraction(w0) / R), _fx_t(Fraction(d) / R), ids["dis"]))
    total = spine[-1]["rec"][1]
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n<fcpxml version="1.10"><resources>%s</resources>'
           '<library><event name="resolve-editor"><project name=%s><sequence format="%s" duration="%s" tcStart="%s" '
           'tcFormat="%s" audioLayout="stereo" audioRate="48k"><spine>%s</spine></sequence></project></event></library>'
           '</fcpxml>\n' % ("".join(res), quoteattr("%s %s" % (edl["timeline"].get("name") or "edit", edl_version(edl, edl_path))),
                             ids["tl"], _fx_t(Fraction(total) / R), _fx_t(Fraction(tc0) / R), "DF" if drop else "NDF",
                             "".join(out_sp)))
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(xml)
    for n in notes:
        print("note: %s" % n)
    if edl["tracks"].get("subtitle"):
        print("note: captions are not in the FCPXML: run export-srt and import the SRT")
    print("WROTE %s" % posix(out))
    print("In Resolve: File > Import > Timeline, pick the file, keep 'Automatically import source clips into media pool' "
          "on. The imported timeline starts at 00:00:00:00 (Resolve ignores the start time in the file) and the markers "
          "sit on the first clip instead of the timeline. The imported timeline is not verified (handoff mode).")
    sz = (edl["timeline"].get("resolve") or {}).get("input_sizing")
    if sz in ("scaleToCrop", "crop", "fill"):
        print("Framing: the edit fills the %dx%d frame (scale full frame with crop). Each clip in the file says so, but "
              "Resolve ignores that on slowed or sped-up clips and the imported timeline takes the project's input "
              "scaling: in Timeline Settings (Use Custom Settings), set the mismatched resolution option to Scale full "
              "frame with crop." % (int(edl["timeline"]["width"]), int(edl["timeline"]["height"])))
    return out


# ----------------------------------------------------------------------------------------------- Resolve snippets
SNIPPET_HEADER = '''try:
    resolve
except NameError:
    import DaVinciResolveScript as dvr_script
    resolve = dvr_script.scriptapp("Resolve")
try:
    project
except NameError:
    project = resolve.GetProjectManager().GetCurrentProject()
'''

COMMON_BODY = r'''
import json, os, time, math


def _re_has(o, name):
    try:
        return o is not None and name in dir(o)
    except Exception:
        return False


def _re_call(o, name, *a):
    try:
        return getattr(o, name)(*a) if _re_has(o, name) else None
    except Exception:
        return None


def _re_const(resolve, name):
    v = getattr(resolve, name, None)
    if v is None or isinstance(v, bool) or not isinstance(v, (int, float)):
        raise KeyError("resolve.%s is not defined in this Resolve build" % name)
    return v


def _re_load(p, default=None):
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default


def _re_save(p, obj):
    d = os.path.dirname(p)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=1)
    os.replace(tmp, p)


def _re_fps(v):
    try:
        s = str(v).split()[0]
        if "/" in s:
            a, b = s.split("/", 1)
            return float(a) / float(b)
        return float(s)
    except Exception:
        return None


def _re_fps_str(v):
    f = _re_fps(v)
    if f is None:
        return None
    for k, s in ((23.976, "24000/1001"), (29.97, "30000/1001"), (47.952, "48000/1001"), (59.94, "60000/1001"),
                 (119.88, "120000/1001")):
        if abs(f - k) < 0.01:
            return s
    if abs(f - round(f)) < 1e-6:
        return "%d/1" % int(round(f))
    return "%d/1000" % int(round(f * 1000))


def _re_tcs(tc, fps):
    try:
        s = str(tc)
        h, m, sec, fr = [int(x) for x in s.replace(";", ":").replace(".", ":").split(":")]
        f = float(fps)
    except Exception:
        return 0.0
    nom = int(round(f))
    frames = ((h * 60 + m) * 60 + sec) * nom + fr
    if (";" in s or "." in s) and (abs(f - 29.97) < 0.01 or abs(f - 59.94) < 0.01):
        dropped = 2 if nom == 30 else 4
        tm = h * 60 + m
        return (frames - dropped * (tm - tm // 10)) / f
    return frames / float(nom)


def _re_settings(o):
    for n in ("GetSettings", "GetSetting"):
        if _re_has(o, n):
            try:
                s = getattr(o, n)()
            except TypeError:
                continue
            if isinstance(s, dict):
                return s
    return {}


def _re_setting(o, key):
    s = _re_settings(o)
    if key in s:
        return s.get(key)
    if _re_has(o, "GetSetting"):
        try:
            return o.GetSetting(key)
        except Exception:
            return None
    return None


def _re_clip_props(mpi):
    try:
        p = mpi.GetClipProperty()
    except Exception:
        p = None
    return p if isinstance(p, dict) else {}


def _re_audio_tracks(mpi):
    """The clip's audio tracks from its pool audio mapping: [{"channels": [...], "type": "mono"}, ...] or None."""
    try:
        d = json.loads(_re_call(mpi, "GetAudioMapping") or "{}")
    except Exception:
        return None
    tm = d.get("track_mapping") if isinstance(d, dict) else None
    if not isinstance(tm, dict):
        return None
    out = []
    for k in sorted(tm, key=lambda x: int(x) if str(x).isdigit() else 0):
        v = tm[k] if isinstance(tm[k], dict) else {}
        out.append({"channels": list(v.get("channel_idx") or []), "type": str(v.get("type") or "").lower()})
    return out


def _re_norm(p, ci=True):
    p = str(p or "").replace("\\", "/")
    while "//" in p[1:]:
        p = p[0] + p[1:].replace("//", "/")
    return p.lower() if ci else p


def _re_pool(pool, limit=50000):
    out, stack = [], [(pool.GetRootFolder(), "")]
    while stack and len(out) < limit:
        fo, path = stack.pop()
        if fo is None:
            continue
        name = _re_call(fo, "GetName") or ""
        here = (path + "/" + name) if path else name
        for c in (_re_call(fo, "GetClipList") or []):
            out.append((c, fo, here))
        for s in (_re_call(fo, "GetSubFolderList") or []):
            stack.append((s, here))
    return out


def _re_timeline_by_uid(project, uid):
    for i in range(1, (_re_call(project, "GetTimelineCount") or 0) + 1):
        t = project.GetTimelineByIndex(i)
        if t is not None and _re_call(t, "GetUniqueId") == uid:
            return t
    return None


def _re_items(tl, kind, idx):
    try:
        return list(tl.GetItemListInTrack(kind, idx) or [])
    except Exception:
        return []


def _re_type(it):
    t = _re_call(it, "GetType")
    if t:
        return t
    return "clip" if _re_call(it, "GetMediaPoolItem") is not None else "other"


def _re_src_in(it, props):
    fps = _re_fps(props.get("FPS"))
    s = _re_call(it, "GetSourceStartTime")
    if fps and isinstance(s, (int, float)):
        return int(round((float(s) - _re_tcs(props.get("Start TC") or "00:00:00:00", fps)) * fps))
    v = _re_call(it, "GetSourceStartFrame")
    return int(v) if isinstance(v, (int, float)) else None


def _re_guard(resolve, project, CFG, FORCE):
    if not bool(_re_call(resolve, "IsStudio")):
        return {"error": "not_studio", "msg": "scripted builds need DaVinci Resolve Studio"}
    if project is None:
        return {"error": "no_project"}
    name = project.GetName()
    if not FORCE:
        if not CFG.get("project"):
            return {"error": "unknown_project", "msg": "project.json names no Resolve project: run dump-script first"}
        if name != CFG["project"]:
            return {"error": "wrong_project", "expected": CFG["project"], "current": name}
        uid = _re_call(project, "GetUniqueId")
        if CFG.get("project_uid") and uid and uid != CFG["project_uid"]:
            return {"error": "wrong_project", "expected_uid": CFG["project_uid"], "current_uid": uid}
    if _re_call(project, "IsRenderingInProgress"):
        return {"error": "render_in_progress", "msg": "wait for the render to finish"}
    return None
'''

DUMP_BODY = r'''

def _re_dump(resolve, project, CFG):
    t0 = time.time()
    if project is None:
        return {"error": "no_project"}
    out = {"schema": "resolve-editor/dump@1", "product": _re_call(resolve, "GetProductName"),
           "version": _re_call(resolve, "GetVersionString"), "studio": bool(_re_call(resolve, "IsStudio")),
           "page": _re_call(resolve, "GetCurrentPage"), "created": time.strftime("%Y-%m-%dT%H:%M:%S")}
    keys = ("timelineFrameRate", "timelineResolutionWidth", "timelineResolutionHeight", "timelineDropFrameTimecode",
            "timelineFrameRateMismatchBehavior", "timelineInputResMismatchBehavior", "videoTimelineRetimeProcess",
            "timelineOutputResolutionWidth", "timelineOutputResolutionHeight")
    ps = {}
    for k in keys:
        v = _re_setting(project, k)
        if v not in (None, ""):
            ps[k] = str(v)
    out["project"] = {"name": project.GetName(), "uid": _re_call(project, "GetUniqueId"), "settings": ps}
    pool = project.GetMediaPool()
    where = {}
    for c, fo, path in _re_pool(pool):
        u = _re_call(c, "GetUniqueId")
        if u:
            where[u] = (c, path)
    media = {}

    def med(m, src=None):
        if m is None:
            return None
        u = _re_call(m, "GetUniqueId") or str(id(m))
        if u not in media:
            cp = _re_clip_props(m)
            media[u] = {"path": cp.get("File Path"), "name": _re_call(m, "GetName"), "mpi_uid": u,
                        "fps": _re_fps_str(cp.get("FPS")), "frames": cp.get("Frames"), "start_tc": cp.get("Start TC"),
                        "type": cp.get("Type"), "resolution": cp.get("Resolution"),
                        "bin": where.get(u, (None, None))[1], "audio_tracks": _re_audio_tracks(m)}
        # where the dump found it: on the current timeline and/or in the bins asked for (add --from-dump adds
        # only the bins' media when bins were named)
        if src == "timeline":
            media[u]["from_timeline"] = True
        elif src:
            fb = media[u].setdefault("from_bin", [])
            if src not in fb:
                fb.append(src)
        return media[u]
    tl = project.GetCurrentTimeline()
    n, cut = 0, False
    if tl is not None:
        start = _re_call(tl, "GetStartFrame") or 0
        fr = _re_setting(tl, "timelineFrameRate") or _re_setting(project, "timelineFrameRate")
        tinfo = {"name": tl.GetName(), "uid": _re_call(tl, "GetUniqueId"), "start_frame": start,
                 "start_tc": _re_call(tl, "GetStartTimecode"), "fps": _re_fps_str(fr),
                 "width": int(_re_fps(_re_setting(tl, "timelineResolutionWidth")) or 0) or None,
                 "height": int(_re_fps(_re_setting(tl, "timelineResolutionHeight")) or 0) or None,
                 "input_sizing": _re_setting(tl, "timelineInputResMismatchBehavior")
                                 or _re_setting(project, "timelineInputResMismatchBehavior"),
                 "tracks": {"video": [], "audio": [], "subtitle": []}}
        for kind in ("video", "audio", "subtitle"):
            for t in range(1, (_re_call(tl, "GetTrackCount", kind) or 0) + 1):
                tr = {"index": t, "name": _re_call(tl, "GetTrackName", kind, t),
                      "enabled": _re_call(tl, "GetIsTrackEnabled", kind, t),
                      "locked": _re_call(tl, "GetIsTrackLocked", kind, t), "items": []}
                for it in _re_items(tl, kind, t):
                    if n >= CFG.get("max_items", 2000):
                        cut = True
                        break
                    n += 1
                    m = _re_call(it, "GetMediaPoolItem")
                    row_m = med(m, "timeline")
                    props = _re_call(it, "GetProperties") or {}
                    if not isinstance(props, dict):
                        props = {}
                    s, e = _re_call(it, "GetStart"), _re_call(it, "GetEnd")
                    sp = _re_call(it, "GetSpeed")
                    fd = _re_call(it, "GetFades")
                    row = {"uid": _re_call(it, "GetUniqueId"), "type": _re_type(it), "name": _re_call(it, "GetName"),
                           "path": (row_m or {}).get("path"),
                           "rec_in": (s - start) if isinstance(s, (int, float)) else None,
                           "rec_out": (e - start) if isinstance(e, (int, float)) else None,
                           "src_in": _re_src_in(it, _re_clip_props(m)) if m is not None else None,
                           "speed": (float(sp.get("Percentage", 100.0)) / 100.0) if isinstance(sp, dict) else None,
                           "enabled": _re_call(it, "GetClipEnabled"),
                           "fades": [fd.get("FadeIn", 0), fd.get("FadeOut", 0)] if isinstance(fd, dict) else [0, 0],
                           "props": dict((k, props[k]) for k in ("ZoomX", "ZoomY", "Pan", "Tilt", "RotationAngle",
                                                                  "Opacity", "AudioVolume", "AudioPan") if k in props)}
                    tr["items"].append(row)
                tinfo["tracks"][kind].append(tr)
        out["timeline"] = tinfo
    else:
        out["timeline"] = None
    bins = [b for b in (CFG.get("bins") or ([CFG["bin"]] if CFG.get("bin") else [])) if b]
    found_bins = {}
    for b in bins:
        bn = str(b).strip().strip("/")
        # names match ignoring case and spaces around them ("music r4 " finds the bin "Music R4")
        bp = [p.strip().lower() for p in bn.split("/")]
        n_b = 0
        for c, fo, path in _re_pool(pool):
            raw_ = path.split("/")
            parts = [p.strip().lower() for p in raw_]
            # the bin by its name ("Day 1") or its path ("Master/Day 1"), with the bins inside it; marked with the
            # bin's own name
            at_ = [k for k in range(len(parts)) if parts[k:k + len(bp)] == bp]
            if at_:
                med(c, "/".join(raw_[at_[0]:at_[0] + len(bp)]))
                n_b += 1
        found_bins[bn] = n_b
    out["bins"] = found_bins
    out["media"] = list(media.values())
    out["truncated"] = cut
    _re_save(CFG["out"], out)
    fps_set = None
    if CFG.get("project_json"):
        pj = _re_load(CFG["project_json"], None)
        if isinstance(pj, dict):
            t = out.get("timeline") or {}
            pfps = _re_fps_str(ps.get("timelineFrameRate")) or t.get("fps")
            pj["resolve"] = {"product": out["product"], "version": out["version"], "studio": out["studio"],
                             "project": out["project"]["name"], "project_uid": out["project"]["uid"],
                             "timeline": t.get("name"), "timeline_uid": t.get("uid"), "fps": t.get("fps"),
                             "project_fps": pfps, "width": t.get("width"), "height": t.get("height"),
                             "frame_rate_mismatch": ps.get("timelineFrameRateMismatchBehavior"),
                             "input_sizing": ps.get("timelineInputResMismatchBehavior"),
                             "retime": ps.get("videoTimelineRetimeProcess")}
            pj["mode"] = "full" if out["studio"] else "handoff"
            ed = CFG.get("edits_dir")
            adopted = False
            if ed and os.path.isdir(ed):
                for fn in os.listdir(ed):
                    if fn.startswith("v") and fn.endswith(".json") and fn[1:-5].isdigit():
                        adopted = True
            if pfps and not adopted:
                # new timelines get the project's rate, so the edit is planned at it (until a version is adopted)
                tl_ = pj.setdefault("timeline", {})
                if tl_.get("fps") != pfps:
                    fps_set = [tl_.get("fps"), pfps]
                tl_["fps"] = pfps
                if t.get("start_tc"):
                    tl_["start_tc"] = t.get("start_tc")
            _re_save(CFG["project_json"], pj)
    res = {"ok": True, "out": CFG["out"], "project": out["project"]["name"], "bins": found_bins,
           "bins_not_found": sorted(k for k, v in found_bins.items() if not v),
           "project_fps": _re_fps_str(ps.get("timelineFrameRate")),
           "timeline": (out.get("timeline") or {}).get("name"), "items": n, "media": len(media), "truncated": cut,
           "seconds": round(time.time() - t0, 2)}
    if fps_set:
        res["edit_fps_changed"] = "the edit now runs at the project's %s fps (was %s)" % (fps_set[1], fps_set[0])
    return res

'''

BACKUP_BODY = r'''

def _re_backup(resolve, project, CFG):
    tl = project.GetCurrentTimeline() if project is not None else None
    if tl is None:
        return {"ok": True, "project": project.GetName() if project is not None else None, "timeline": None,
                "note": "the project has no current timeline, so there is nothing to back up"}
    d = CFG["dir"] + "/" + time.strftime("%Y%m%d_%H%M%S")
    if not os.path.isdir(d):
        os.makedirs(d)
    res = {"dir": d, "project": project.GetName(), "timeline": tl.GetName(), "timeline_uid": _re_call(tl, "GetUniqueId")}
    for key, fname, kind in (("drt", "before.drt", "EXPORT_DRT"), ("otio", "before.otio", "EXPORT_OTIO")):
        p = d + "/" + fname
        try:
            ok = tl.Export(p, _re_const(resolve, kind), _re_const(resolve, "EXPORT_NONE"))
        except Exception as e:
            ok = False
            res[key + "_error"] = str(e)
        res[key] = p
        res[key + "_ok"] = bool(ok) and os.path.isfile(p) and os.path.getsize(p) > 0
    res["dump"] = _re_dump(resolve, project, {"out": d + "/dump.json", "max_items": 5000})
    res["ok"] = res["drt_ok"] and bool(res["dump"].get("ok"))
    _re_save(d + "/backup.json", res)
    return res

'''

BUILD_BODY = r'''

def _re_find(tl, kind, idx, uid=None, start=None):
    for it in _re_items(tl, kind, idx):
        if uid is not None and _re_call(it, "GetUniqueId") == uid:
            return it
    if start is not None:
        for it in _re_items(tl, kind, idx):
            if _re_type(it) != "transition" and _re_call(it, "GetStart") == start:
                return it
    return None


def _re_build(resolve, project, CFG, IMPORT_MISSING=False, FORCE=False):
    t0 = time.time()
    bad = _re_guard(resolve, project, CFG, FORCE)
    if bad:
        return bad
    page = _re_call(resolve, "GetCurrentPage")
    pool = project.GetMediaPool()
    ci = CFG.get("case_insensitive", True)
    mp = _re_load(CFG["map"], None) or {}
    found = {}
    by_path = {}
    for c, fo, path in _re_pool(pool):
        p = _re_norm(_re_clip_props(c).get("File Path"), ci)
        if p and p not in by_path:
            by_path[p] = c
    for mid, m in CFG["media"].items():
        c = by_path.get(_re_norm(m["path"], ci))
        if c is not None:
            found[mid] = c
    missing = [CFG["media"][k]["path"] for k in sorted(CFG["media"]) if k not in found]
    notes = []
    if not mp.get("timeline_uid"):
        if CFG["chunk"] != 1:
            return {"error": "run_first_chunk", "msg": "run build chunk 1 first"}
        if missing and not IMPORT_MISSING:
            return {"error": "missing_media", "paths": missing,
                    "msg": "these files are not in the media pool; import them or set IMPORT_MISSING = True"}
        pf = _re_fps(_re_setting(project, "timelineFrameRate"))
        if pf is None or abs(pf - CFG["fps"]) > 0.001:
            return {"error": "fps_mismatch", "project_fps": pf, "edl_fps": CFG["fps"],
                    "msg": "the EDL frame rate differs from the project timeline rate"}
        root = pool.GetRootFolder()
        before = _re_call(pool, "GetCurrentFolder")
        top = None
        for s in (_re_call(root, "GetSubFolderList") or []):
            if _re_call(s, "GetName") == "resolve-editor":
                top = s
        if top is None:
            top = pool.AddSubFolder(root, "resolve-editor")
        binf = None
        for s in (_re_call(top, "GetSubFolderList") or []):
            if _re_call(s, "GetName") == CFG["bin"]:
                binf = s
        if binf is None and top is not None:
            binf = pool.AddSubFolder(top, CFG["bin"])
        if binf is None:
            return {"error": "bin_failed", "msg": "could not create the bin resolve-editor/" + CFG["bin"]}
        pool.SetCurrentFolder(binf)
        if missing:
            pool.ImportMedia(missing)
            for c, fo, path in _re_pool(pool):
                p = _re_norm(_re_clip_props(c).get("File Path"), ci)
                if p and p not in by_path:
                    by_path[p] = c
            for mid, m in CFG["media"].items():
                if mid not in found and by_path.get(_re_norm(m["path"], ci)) is not None:
                    found[mid] = by_path[_re_norm(m["path"], ci)]
            still = [CFG["media"][k]["path"] for k in sorted(CFG["media"]) if k not in found]
            if still:
                if before is not None:
                    pool.SetCurrentFolder(before)
                return {"error": "import_failed", "paths": still}
            notes.append("imported %d files into resolve-editor/%s" % (len(missing), CFG["bin"]))
        names = set()
        for i in range(1, (_re_call(project, "GetTimelineCount") or 0) + 1):
            t = project.GetTimelineByIndex(i)
            if t is not None:
                names.add(t.GetName())
        name = CFG["name"]
        for suf in "bcdefghijklmnopqrstuvwxyz":
            if name not in names:
                break
            name = CFG["name"] + " " + suf
        tl = pool.CreateEmptyTimeline(name)
        if before is not None:
            pool.SetCurrentFolder(before)
        if tl is None:
            return {"error": "create_failed", "name": name}
        project.SetCurrentTimeline(tl)
        cur = project.GetCurrentTimeline()
        if cur is None or _re_call(cur, "GetUniqueId") != _re_call(tl, "GetUniqueId"):
            return {"error": "not_current", "msg": "the new timeline did not become current", "timeline": name}
        pw = int(_re_fps(_re_setting(project, "timelineResolutionWidth")) or 0)
        ph = int(_re_fps(_re_setting(project, "timelineResolutionHeight")) or 0)
        want_sz = CFG.get("input_sizing") or "scaleToFit"
        # a new timeline takes the project's size and input sizing; the edit (and its preview) may use others, for
        # example a vertical crop in a project set to scale to fit, which would letterbox every 16:9 clip
        if (pw, ph) != (CFG["width"], CFG["height"]) or _re_setting(project, "timelineInputResMismatchBehavior") != want_sz:
            want = {"useCustomSettings": "1", "timelineResolutionWidth": str(CFG["width"]),
                    "timelineResolutionHeight": str(CFG["height"]), "timelineInputResMismatchBehavior": want_sz}
            for k in ("useCustomSettings", "timelineResolutionWidth", "timelineResolutionHeight",
                      "timelineInputResMismatchBehavior"):
                if not (_re_has(tl, "SetSetting") and tl.SetSetting(k, want[k])):
                    notes.append("timeline setting %s was refused" % k)
        got = (_re_setting(tl, "timelineResolutionWidth"), _re_setting(tl, "timelineResolutionHeight"),
               _re_setting(tl, "timelineInputResMismatchBehavior"))
        if (str(got[0]), str(got[1]), got[2]) != (str(CFG["width"]), str(CFG["height"]), want_sz):
            notes.append("the new timeline is %sx%s with input sizing %s, not %dx%d %s as the edit was previewed; "
                         "set it in Timeline > Timeline Settings" % (got[0], got[1], got[2], CFG["width"],
                                                                      CFG["height"], want_sz))
        stc = _re_call(tl, "GetStartTimecode")
        if CFG.get("start_tc") and stc != CFG["start_tc"]:
            if not _re_call(tl, "SetStartTimecode", CFG["start_tc"]):
                notes.append("start timecode %s was refused (kept %s)" % (CFG["start_tc"], stc))
        for kind, n in (("video", CFG["tracks"]["video"]), ("audio", CFG["tracks"]["audio"])):
            while (_re_call(tl, "GetTrackCount", kind) or 0) < n:
                ok = tl.AddTrack(kind, "stereo") if kind == "audio" else tl.AddTrack(kind)
                if not ok:
                    return {"error": "add_track_failed", "kind": kind}
        # the titles' track and the captions' track (one above every track of the edit) carry their names
        for tn in CFG.get("track_names") or []:
            _re_call(tl, "SetTrackName", tn[0], int(tn[1]), tn[2])
        mp = {"schema": "resolve-editor/build-map@1", "edl": CFG["edl"], "version": CFG["version"],
              "project": project.GetName(), "timeline": tl.GetName(), "timeline_uid": _re_call(tl, "GetUniqueId"),
              "start_frame": tl.GetStartFrame(), "chunks": CFG["chunks"], "chunks_done": [], "items": {},
              "refused": [], "tail_trimmed": [], "length_off": [], "transitions": {}, "markers": [], "notes": notes,
              "stills": [], "text": CFG.get("text") or {"mode": "markers", "captions": "file"},
              "page": page, "created": time.strftime("%Y-%m-%dT%H:%M:%S")}
        _re_save(CFG["map"], mp)
    else:
        tl = _re_timeline_by_uid(project, mp["timeline_uid"])
        if tl is None:
            return {"error": "timeline_gone", "msg": "the timeline this build started is gone; delete the build map "
                                                     "and run chunk 1 again", "timeline": mp.get("timeline")}
        cur = project.GetCurrentTimeline()
        if cur is None or _re_call(cur, "GetUniqueId") != mp["timeline_uid"]:
            project.SetCurrentTimeline(tl)
            cur = project.GetCurrentTimeline()
            if cur is None or _re_call(cur, "GetUniqueId") != mp["timeline_uid"]:
                return {"error": "not_current", "msg": "could not make the build timeline current"}
        lack = sorted(set(u["media"] for u in CFG["units"] if u["media"] not in found))
        if lack:
            return {"error": "missing_media", "paths": [CFG["media"][k]["path"] for k in lack]}
    start = tl.GetStartFrame()
    done = mp["items"]
    placed, refused = [], []
    # clips with several audio tracks in the pool (4 channel cameras, two stereo streams) spread them over the
    # following timeline tracks, and Resolve refuses the whole clip when one of those holds an item. Locking the
    # other audio tracks during the append places the first track only, which is what the lab previewed.
    multi = {}

    def note(msg):
        if msg not in notes:
            notes.append(msg)
        if msg not in mp.setdefault("notes", []):
            mp["notes"].append(msg)
    for mid in sorted(set(u["media"] for u in CFG["units"] if any(p["kind"] == "audio" for p in u["parts"]))):
        c = found.get(mid)
        tr = _re_audio_tracks(c) if c is not None else None
        if not tr:
            continue
        want = CFG["media"].get(mid, {}).get("track1")
        if want and (list(want.get("channels") or []) != list(tr[0]["channels"]) or
                     str(want.get("type") or "").lower() != tr[0]["type"]):
            msg = ("%s: Resolve plays channels %s (%s) of this clip but the preview used channels %s (%s); run "
                   "dump-script, add --from-dump and ingest again so they match" % (
                       mid, tr[0]["channels"], tr[0]["type"], want.get("channels"), want.get("type")))
            note(msg)
        if len(tr) > 1:
            multi[mid] = len(tr)
            note("%s has %d audio tracks in Resolve: placed with its first track only, as the preview plays it"
                 % (mid, len(tr)))
    mp["multi_audio"] = dict(mp.get("multi_audio") or {}, **multi)

    def place(u):
        tracks = sorted(set(p["track"] for p in u["parts"] if p["kind"] == "audio"))
        locked = []
        if u["media"] in multi and tracks:
            for k in range(1, (_re_call(tl, "GetTrackCount", "audio") or 0) + 1):
                if k not in tracks and not _re_call(tl, "GetIsTrackLocked", "audio", k):
                    if tl.SetTrackLock("audio", k, True):
                        locked.append(k)
        try:
            return pool.AppendToTimeline([info(u)])
        finally:
            for k in locked:
                tl.SetTrackLock("audio", k, False)

    def settle(u):
        for part in u["parts"]:
            it = _re_find(tl, part["kind"], part["track"], None, start + u["rec_in"])
            if it is not None and _re_call(it, "GetUniqueId"):
                done[part["id"]] = {"uid": it.GetUniqueId(), "kind": part["kind"], "track": part["track"],
                                    "record": start + u["rec_in"], "len": _re_call(it, "GetDuration"), "ok": True}
                placed.append(part["id"])
                if u.get("tail_trimmed") and part["id"] not in mp["tail_trimmed"]:
                    mp["tail_trimmed"].append(part["id"])
            else:
                refused.append(part["id"])
                if part["id"] not in mp["refused"]:
                    mp["refused"].append(part["id"])

    def info(u):
        c = {"mediaPoolItem": found[u["media"]], "startFrame": u["start"], "endFrame": u["end"],
             "recordFrame": start + u["rec_in"], "trackIndex": u["track"]}
        if u.get("media_type"):
            c["mediaType"] = u["media_type"]
        return c
    batch = []

    def flush():
        if not batch:
            return
        got = pool.AppendToTimeline([info(u) for u in batch])
        if not got:
            for u in batch:
                pool.AppendToTimeline([info(u)])
        for u in batch:
            settle(u)
        del batch[:]

    def place_still(u):
        # a still ignores startFrame and endFrame and lands 5 s long; marks 0 to n - 1 on its media pool item give it
        # exactly n frames, and clearing them afterwards keeps the placed length (measured on 21.1)
        # the item's own marks (a logo already in the user's pool may carry some) are put back afterwards
        c = found[u["media"]]
        n = int(u["rec_len"])
        before = _re_call(c, "GetMarkInOut") or {}
        if not c.SetMarkInOut(0, n - 1):
            note("%s: Resolve refused the marks that set the still's length" % u["parts"][0]["id"])
        try:
            return pool.AppendToTimeline([{"mediaPoolItem": c, "recordFrame": start + u["rec_in"],
                                           "trackIndex": u["track"]}])
        finally:
            _re_call(c, "ClearMarkInOut")
            for kind_ in ("video", "audio"):
                mk_ = before.get(kind_) if isinstance(before, dict) else None
                if isinstance(mk_, dict) and mk_.get("in") is not None and mk_.get("out") is not None:
                    _re_call(c, "SetMarkInOut", int(mk_["in"]), int(mk_["out"]), kind_)
    for u in CFG["units"]:
        if all(p["id"] in done for p in u["parts"]):
            continue
        if u.get("still"):
            flush()
            place_still(u)
            settle(u)
            for part in u["parts"]:
                d = done.get(part["id"])
                if not d:
                    continue
                mp.setdefault("stills", [])
                if part["id"] not in mp["stills"]:
                    mp["stills"].append(part["id"])
                if d.get("len") != u["rec_len"] and part["id"] not in mp["length_off"]:
                    mp["length_off"].append(part["id"])
        elif abs(float(u.get("speed", 1.0)) - 1.0) > 1e-9:
            flush()
            place(u)
            settle(u)
            for part in u["parts"]:
                d = done.get(part["id"])
                if not d:
                    continue
                it = _re_find(tl, part["kind"], part["track"], d["uid"])
                ok = it is not None and it.SetSpeed({"Percentage": float(u["speed"]) * 100.0, "PitchCorrection": True,
                                                     "RippleTimeline": False})
                got_len = _re_call(it, "GetDuration")
                d["len"] = got_len
                d["speed_ok"] = bool(ok)
                if got_len != u["rec_len"] and part["id"] not in mp["length_off"]:
                    mp["length_off"].append(part["id"])
        elif u["media"] in multi and any(p["kind"] == "audio" for p in u["parts"]):
            flush()
            place(u)
            settle(u)
        else:
            batch.append(u)
    flush()
    post_fail = []
    for u in CFG["units"]:
        for part in u["parts"]:
            d = done.get(part["id"])
            if not d or d.get("post"):
                continue
            it = _re_find(tl, part["kind"], part["track"], d["uid"], d["record"])
            if it is None:
                continue
            fi, fo = part.get("fades") or [0, 0]
            if fi or fo:
                if not it.SetFades({"FadeIn": int(fi), "FadeOut": int(fo)}):
                    post_fail.append([part["id"], "fades"])
            props = part.get("props") or {}
            if props:
                if not (_re_has(it, "SetProperties") and it.SetProperties(props)):
                    for k, v in props.items():
                        one = {}
                        one[k] = v
                        if not (_re_has(it, "SetProperties") and it.SetProperties(one)):
                            if not (_re_has(it, "SetProperty") and it.SetProperty(k, v)):
                                post_fail.append([part["id"], k])
            d["post"] = True
    for pair in CFG.get("links", []):
        its = []
        for pid in pair:
            d = done.get(pid)
            if d:
                it = _re_find(tl, d["kind"], d["track"], d["uid"])
                if it is not None:
                    its.append(it)
        if len(its) == len(pair):
            if not tl.SetClipsLinked(its, True):
                post_fail.append([pair[0], "link"])
    if CFG["chunk"] not in mp["chunks_done"]:
        mp["chunks_done"].append(CFG["chunk"])
    mp["post_failed"] = mp.get("post_failed", []) + post_fail
    _re_save(CFG["map"], mp)
    return {"ok": not refused and not post_fail, "timeline": mp["timeline"], "chunk": CFG["chunk"],
            "of": CFG["chunks"], "placed": len(placed), "refused": refused, "post_failed": post_fail,
            "notes": notes, "page": page,
            "seconds": round(time.time() - t0, 2)}


def _re_finish(resolve, project, CFG, FORCE=False):
    bad = _re_guard(resolve, project, CFG, FORCE)
    if bad:
        return bad
    mp = _re_load(CFG["map"], None)
    if not mp or not mp.get("timeline_uid"):
        return {"error": "run_first_chunk", "msg": "run the build chunks first"}
    tl = _re_timeline_by_uid(project, mp["timeline_uid"])
    if tl is None:
        return {"error": "timeline_gone"}
    g = mp.get("gfx") or {}
    if CFG.get("gfx_snippet") and not g.get("ran"):
        return {"error": "run_gfx_first", "msg": "run %s (titles and captions) until it no longer reports more, "
                                                 "then this finish snippet" % CFG["gfx_snippet"]}
    done = mp["items"]
    xdone = mp.setdefault("transitions", {})
    lengths = mp.setdefault("transitions_length", [])
    rebuilt = mp.setdefault("transitions_rebuilt", [])
    refused, xoff = [], []
    for x in CFG["transitions"]:
        if x["id"] in xdone:
            continue
        d = done.get(x["from"])
        it = _re_find(tl, d["kind"], d["track"], d["uid"]) if d else None
        dt = done.get(x.get("to")) if x.get("to") else None
        it2 = _re_find(tl, dt["kind"], dt["track"], dt["uid"]) if dt else None
        n = int(x["frames"])
        al = x.get("alignment", "center")
        cut = _re_call(it, "GetEnd") if it is not None else None
        want = None
        if isinstance(cut, (int, float)):
            cut = int(cut)
            want = {"center": [cut - n // 2, cut + n - n // 2], "left": [cut - n, cut], "right": [cut, cut + n]}.get(al)
        # Resolve 21.1 makes about half of the transitions added at a clip's end a frame short after the cut (a
        # Cross Fade 0 dB of the music ducking then renders as a dropout), while the same ones added at the next
        # clip's start come out as asked (measured on 24 joints): a ducking crossfade is added from the incoming
        # piece. Each transition is checked against the frames asked for; one that differs is reported
        # (transitions_off) and verify STOPs on it (transition_off). place_transition (fusion_recipes) adds it from
        # the catalogue's Resolve strings and builds its route: a published macro input (Motion Blur on), or the R1
        # whip and R1b zoom rebuilt inside a Fusion Cross Dissolve; the placed length is read back.
        tr, info = place_transition(it, it2, x)
        got = [info.get("start"), info.get("end")] if tr is not None else None
        if tr is None:
            refused.append(x["id"])
        else:
            xdone[x["id"]] = {"uid": _re_call(tr, "GetUniqueId"), "start": got[0], "end": got[1], "want": want,
                              "dur": info.get("dur"), "build": info.get("build"), "name": info.get("name")}
            if x.get("macro_inputs"):
                xdone[x["id"]]["macro"] = info.get("macro")
            if info.get("rebuilt") is not None:
                xdone[x["id"]]["tools"] = (info["rebuilt"] or {}).get("tools")
                if x["id"] not in rebuilt:
                    rebuilt.append(x["id"])
            if info.get("rebuild_error"):
                xdone[x["id"]]["rebuild_error"] = info["rebuild_error"]
            if x.get("category") != "audio":
                lengths.append({"id": x["id"], "asked": n, "placed": info.get("dur")})
            # a ducking crossfade must land exactly; a picture transition may sit a frame to either side (an odd
            # length has no exact centre) but keeps its length and the cut
            if want is not None and (got != want if x.get("side") == "start" else
                                     not (isinstance(got[0], (int, float)) and isinstance(got[1], (int, float))
                                          and int(got[1]) - int(got[0]) == n and got[0] <= cut <= got[1])):
                xoff.append({"id": x["id"], "want": want, "got": got})
    # a title or caption the graphics snippet could not build as Text+ becomes the old Cream marker, and only that
    built = [k for k, v in (g.get("items") or {}).items() if v.get("uid")]
    fall = [f for f in CFG.get("fallback", []) if f["id"] not in built]
    by = {}
    for mk in CFG["markers"]:
        by[int(mk["frame"])] = dict(mk)
    for f in fall:
        k = int(f["frame"])
        if k in by:
            m0 = by[k]
            m0["name"] = " | ".join(x for x in (m0["name"], f["name"]) if x)
            m0["note"] = " | ".join(x for x in (m0["note"], f["note"]) if x)
            m0["duration"] = max(int(m0["duration"]), int(f["duration"]))
            m0["custom"] = m0["custom"] + "," + f["id"]
        else:
            by[k] = {"frame": k, "color": "Cream", "name": f["name"], "note": f["note"], "duration": int(f["duration"]),
                     "custom": "resolve-editor|%s|%s" % (mp.get("version"), f["id"])}
    have = _re_call(tl, "GetMarkers") or {}
    made, mref = 0, []
    for f in sorted(by):
        mk = by[f]
        cur = have.get(f) or have.get(float(f))
        if isinstance(cur, dict) and str(cur.get("customData", "")).startswith("resolve-editor|"):
            continue
        if tl.AddMarker(f, mk["color"], mk["name"], mk["note"], int(mk["duration"]), mk["custom"]):
            made += 1
        else:
            mref.append(f)
    g["fallback_markers"] = [f["id"] for f in fall if int(f["frame"]) not in mref]
    if g:
        mp["gfx"] = g
    # every track named from the EDL (chunk 1 named them; a name Resolve lost is set again), read back
    tnames = []
    for tn in CFG.get("track_names") or []:
        got_ = _re_call(tl, "GetTrackName", tn[0], int(tn[1]))
        if got_ != tn[2]:
            _re_call(tl, "SetTrackName", tn[0], int(tn[1]), tn[2])
            got_ = _re_call(tl, "GetTrackName", tn[0], int(tn[1]))
        tnames.append([tn[0], int(tn[1]), got_])
    # item names and clip colours: stills, music ducking pieces, the title and caption ranges and their Text+ items
    # on the graphics timeline (SetName renames the timeline item only, measured in 21.1)
    lbl = {"named": 0, "coloured": 0, "refused": []}
    gi_ = g.get("items") or {}
    sheet_ = _re_timeline_by_uid(project, g["sheet_uid"]) if g.get("sheet_uid") else None

    def label_one(x, lb):
        ok_ = True
        n_ = c_ = 0
        if lb.get("name"):
            if _re_call(x, "GetName") != lb["name"]:
                _re_call(x, "SetName", lb["name"])
            if _re_call(x, "GetName") == lb["name"]:
                n_ = 1
            else:
                ok_ = False
        if lb.get("color"):
            if _re_call(x, "GetClipColor") != lb["color"]:
                _re_call(x, "SetClipColor", lb["color"])
            if _re_call(x, "GetClipColor") == lb["color"]:
                c_ = 1
            else:
                ok_ = False
        return ok_, n_, c_
    for pid in sorted(CFG.get("labels") or {}):
        lb = CFG["labels"][pid]
        its = []
        d = done.get(pid)
        if d:
            its.append(_re_find(tl, d["kind"], d["track"], d["uid"]))
        elif (gi_.get(pid) or {}).get("uid"):
            its.append(_re_find(tl, "video", int(gi_[pid]["track"]), gi_[pid]["uid"]))
            if sheet_ is not None and g.get("slot"):
                s0_ = sheet_.GetStartFrame()
                for k in range(int(gi_[pid].get("slots") or 1)):
                    its.append(_re_find(sheet_, "video", 1, None, s0_ + int(gi_[pid]["slot_start"]) + k * int(g["slot"])))
        else:
            continue                    # not built (refused, or a Cream marker): nothing to label
        good, nn, cc = True, 0, 0
        for x in its:
            if x is None:
                good = False
                continue
            ok_, n_, c_ = label_one(x, lb)
            good, nn, cc = good and ok_, max(nn, n_), max(cc, c_)
        lbl["named"] += nn
        lbl["coloured"] += cc
        if not good:
            lbl["refused"].append(pid)
    # clips that carry a comp the effects snippet built: Orange again and the comp's name (a run of the finish after
    # someone recoloured a clip puts it back; before the effects run there is nothing to colour)
    fxi_ = (mp.get("fx") or {}).get("items") or {}
    for pid in sorted(CFG.get("comp_labels") or {}):
        d = done.get(pid)
        if not (fxi_.get(pid) or {}).get("ok") or not d:
            continue
        x = _re_find(tl, d["kind"], d["track"], d["uid"])
        cl_ = CFG["comp_labels"][pid]
        if x is None:
            lbl["refused"].append(pid)
            continue
        ok_, n_, c_ = label_one(x, {"color": cl_.get("color")})
        nl_ = _re_call(x, "GetFusionCompNameList") or []
        if cl_.get("comp_name") and nl_ and nl_[0] != cl_["comp_name"]:
            _re_call(x, "RenameFusionCompByName", nl_[0], cl_["comp_name"])
        lbl["coloured"] += c_
        lbl.setdefault("comps", []).append(pid)
        if not ok_:
            lbl["refused"].append(pid)
    mp["labels"] = lbl
    # the hand-off: a Blue marker at the programme's first free frame from 0 and the same note in the timeline's
    # Comments (its media pool item; Resolve shows the line breaks as spaces there, measured in 21.1)
    hand = None
    ho = CFG.get("handoff")
    if ho:
        note_ = ho["note"].replace("{sheet}", '"%s"' % (g.get("sheet") or "graphics"))
        mk_ = _re_call(tl, "GetMarkers") or {}
        at_ = None
        for f_, v_ in mk_.items():
            if isinstance(v_, dict) and str(v_.get("customData", "")).endswith("|handoff"):
                at_ = int(float(f_))
        if at_ is None:
            taken_ = set(int(float(f_)) for f_ in mk_)
            k_ = 0
            while k_ in taken_:
                k_ += 1
            if tl.AddMarker(k_, ho.get("color") or "Blue", ho["name"], note_, 1, ho["custom"]):
                at_ = k_
        tmpi_ = _re_call(tl, "GetMediaPoolItem")
        c_ok = bool(_re_call(tmpi_, "SetClipProperty", "Comments", note_)) if tmpi_ is not None else False
        if c_ok:
            got_c = _re_call(tmpi_, "GetClipProperty", "Comments")
            c_ok = " ".join(str(got_c or "").split()) == " ".join(note_.split())
        hand = {"marker_frame": at_, "comments": c_ok}
        mp["handoff"] = dict(hand, custom=ho["custom"], note=note_)
    expected = CFG.get("expected", [])
    lost = [i for i in expected if i not in done]
    mp["transitions_refused"] = refused
    mp["transitions_off"] = xoff
    mp["markers_refused"] = mref
    mp["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    _re_save(CFG["map"], mp)
    # the chunks' own notes (a timeline setting Resolve refused, media imported) belong in the final result too
    notes = list(CFG.get("notes", []))
    for n in mp.get("notes") or []:
        if n not in notes:
            notes.append(n)
    # only the build's own setting notes (a media id in another note may contain any word)
    unset = [n for n in (mp.get("notes") or []) if n.startswith(("timeline setting ", "start timecode ", "the new timeline is "))]
    for n in g.get("notes") or []:
        if n not in notes:
            notes.append(n)
    if fall:
        notes.append("%d title%s or caption%s could not be built as Text+ and became Cream markers: %s" % (
            len(fall), "" if len(fall) == 1 else "s", "" if len(fall) == 1 else "s", ", ".join(f["id"] for f in fall)))
    xbad = sorted(k for k, v in xdone.items() if v.get("rebuild_error"))
    for k in xbad:
        notes.append("%s: the custom transition could not be rebuilt (%s); a plain Fusion Cross Dissolve is there"
                     % (k, xdone[k]["rebuild_error"]))
    res = {"ok": not lost and not refused and not xoff and not mref and not mp.get("length_off") and not mp.get("refused")
           and not unset and not fall and not xbad,
           "timeline": mp["timeline"], "timeline_uid": mp["timeline_uid"], "items": len(done),
           "not_placed": lost, "refused": mp.get("refused", []), "tail_trimmed": mp.get("tail_trimmed", []),
           "length_off": mp.get("length_off", []), "transitions_refused": refused, "transitions_off": xoff,
           "transitions_length": lengths, "transitions_rebuilt": rebuilt,
           "markers": made,
           "markers_refused": mref, "settings_refused": unset, "graphics_sheet": g.get("sheet"),
           "stills": len(mp.get("stills") or []), "text_built": len(built), "text_markers": len(g["fallback_markers"]),
           "colorist": {"timeline": mp["timeline"], "timeline_uid": mp["timeline_uid"],
                        "note": "the graphics sheet and stills are not graded; grade this timeline's clips"},
           "notes": notes}
    res["track_names"] = tnames
    res["labels"] = lbl
    if hand is not None:
        res["handoff"] = hand
    lo_ = CFG.get("loudness")
    if lo_ is not None:
        res["loudness"] = {"mix_gain_db": lo_.get("mix_gain_db"), "preview_lufs": lo_.get("preview_lufs"),
                           "residual_lu": lo_.get("residual_lu")}
    off_ = [t for t, w in zip(tnames, CFG.get("track_names") or []) if t[2] != w[2]]
    if off_:
        notes.append("track names Resolve did not take: %s" % ", ".join("%s %d %r" % (t[0], t[1], t[2]) for t in off_))
    if lbl["refused"]:
        notes.append("item names or colours Resolve did not take: %s" % ", ".join(lbl["refused"][:10]))
    if hand is not None and (hand["marker_frame"] is None or not hand["comments"]):
        notes.append("the hand-off %s was not made" % (
            "marker" if hand["marker_frame"] is None else "note in the timeline's Comments"))
    if CFG.get("fx_snippet"):
        res["next"] = "run %s (clip effects: keyed moves, retimes, accents) until it no longer reports more" % (
            CFG["fx_snippet"])
    return res


def _re_fx(resolve, project, CFG, FORCE=False):
    """Clip comps after the transitions exist (D3: a clip comp starts at the first frame the item is SEEN, so its
    keys depend on the placed transitions): per item a TimeStretcher (retime), a Transform (keyed moves), accents
    (flash, RGB split, leak, glitch bands) and Speed Warp. Stops after the time budget with more: true and goes on
    where it stopped; tools whose RE_ name exists are not built twice."""
    t0 = time.time()
    bad = _re_guard(resolve, project, CFG, FORCE)
    if bad:
        return bad
    mp = _re_load(CFG["map"], None)
    if not mp or not mp.get("timeline_uid"):
        return {"error": "run_first_chunk", "msg": "run the build chunks first"}
    if not mp.get("finished"):
        return {"error": "run_finish_first", "msg": "run the finish snippet (transitions) first: clip comps are "
                                                     "timed from the transitions next to them"}
    tl = _re_timeline_by_uid(project, mp["timeline_uid"])
    if tl is None:
        return {"error": "timeline_gone"}
    fx = mp.setdefault("fx", {})
    for k, v in (("items", {}), ("refused", []), ("notes", [])):
        fx.setdefault(k, v)
    done = mp["items"]
    more = False
    built_now = []

    def refuse(sid, why, detail=None):
        fx["refused"] = [r for r in fx["refused"] if r.get("id") != sid]
        row = {"id": sid, "why": why}
        if detail:
            row["detail"] = detail
        fx["refused"].append(row)

    def save():
        _re_save(CFG["map"], mp)
    for spec in CFG["fx_items"]:
        sid = spec["id"]
        if (fx["items"].get(sid) or {}).get("ok") or any(r.get("id") == sid for r in fx["refused"]):
            continue
        if time.time() - t0 > float(CFG.get("budget_s", 40)):
            # run_script_unsafe stops a script after 60 s: stop cleanly, the next run continues here
            more = True
            break
        d = done.get(sid)
        it = _re_find(tl, "video", d["track"], d["uid"]) if d else None
        if it is None:
            refuse(sid, "item_missing", "the item is not on the timeline (see the build's refused list)")
            save()
            continue
        rec = {"ok": True, "uid": d["uid"], "tools": []}
        if spec.get("speed_warp"):
            rec["speed_warp"] = apply_speed_warp(resolve, it)
            if not rec["speed_warp"].get("ok"):
                refuse(sid, "speed_warp_refused", rec["speed_warp"])
                save()
                continue
        if spec.get("motion") or spec.get("accents") or spec.get("retime"):
            comp = clip_comp(it)
            if comp is None:
                refuse(sid, "addtool_failed", "AddFusionComp returned nothing")
                save()
                continue
            vr = vis_range(tl, it)
            ct, info = comp_time_fn(it, comp, tl, spec.get("media_fps"), CFG["fps"], spec.get("speed", 1.0), vr,
                                    still=bool(spec.get("still")))
            rec["timebase"] = {k: info.get(k) for k in ("rs", "re", "gs", "head", "tail", "len", "want_span",
                                                        "got_span", "ok")}
            if not info.get("ok"):
                refuse(sid, "fx_timebase", info.get("why"))
                save()
                continue
            fr = spec["framing"]
            m0 = None
            parts = {}
            if spec.get("retime"):
                m0, how = media_frame_at_start(comp, it, spec.get("media_fps"))
                if m0 is None:
                    m0 = float(spec.get("src_in", 0)) - float(vr["head"]) * float(info.get("ratio") or 1.0)
                rec["m0"] = m0
                parts["retime"] = apply_retime(comp, ct, spec["retime"], m0, info["rs"])
            if spec.get("motion"):
                parts["motion"] = apply_motion(comp, ct, spec["motion"], fr)
            if spec.get("accents"):
                parts["accents"] = apply_accents(comp, ct, spec["accents"], fr)
            bad_ = [k for k, v in parts.items() if not v.get("ok", True)]
            if bad_:
                p_ = parts[bad_[0]]
                refuse(sid, "addtool_failed" if p_.get("why") == "addtool_failed" else "keys_failed",
                       "%s: %s" % (bad_[0], p_.get("why") or p_))
                save()
                continue
            rb = readback_fx(comp, ct, fr, spec.get("samples") or [0], m0, info["rs"])
            rec["tools"] = rb["tools"]
            rec["read"] = rb["samples"]
            # the comp named for what it does (Resolve calls it "Composition 1"), the clip coloured Orange, the
            # Fusion output cache on Auto: the CacheMode constant (21.1 refused every string), read back
            nl_ = _re_call(it, "GetFusionCompNameList") or []
            if spec.get("comp_name") and nl_ and nl_[0] != spec["comp_name"]:
                _re_call(it, "RenameFusionCompByName", nl_[0], spec["comp_name"])
                nl_ = _re_call(it, "GetFusionCompNameList") or []
            rec["comp_name"] = nl_[0] if nl_ else None
            lb_ = spec.get("label") or {}
            if lb_.get("color") and _re_call(it, "GetClipColor") != lb_["color"]:
                _re_call(it, "SetClipColor", lb_["color"])
            rec["color"] = _re_call(it, "GetClipColor")
            try:
                auto_ = _re_const(resolve, "CACHE_AUTO_ENABLED")
            except KeyError:
                auto_ = None
            set_ = _re_call(it, "SetFusionOutputCache", auto_) if auto_ is not None else None
            got_ = _re_call(it, "GetIsFusionOutputCacheEnabled")
            rec["cache"] = {"set": bool(set_) and isinstance(got_, (int, float)) and float(got_) == float(auto_),
                            "read": got_}
        fx["items"][sid] = rec
        built_now.append(sid)
        save()
    if not more:
        fx["ran"] = True
    save()
    items = fx["items"]
    res = {"ok": not fx["refused"] and not more, "timeline": mp["timeline"],
           "fx_built": sorted(k for k, v in items.items() if v.get("ok")), "fx_refused": list(fx["refused"]),
           "fusion_items": sorted(k for k, v in items.items() if v.get("tools")),
           "transitions_rebuilt": list(mp.get("transitions_rebuilt") or []),
           "transitions_length": [r for r in (mp.get("transitions_length") or [])],
           "built_now": built_now, "more": more, "seconds": round(time.time() - t0, 2)}
    withc = [k for k, v in items.items() if v.get("ok") and v.get("cache") is not None]
    res["cache"] = {"set": sum(1 for k in withc if items[k]["cache"].get("set")),
                    "refused": sorted(k for k in withc if not items[k]["cache"].get("set"))}
    res["comp_names"] = dict((k, items[k].get("comp_name")) for k in sorted(withc))
    res["colored"] = sorted(k for k in withc if items[k].get("color") == "Orange")
    if more:
        res["msg"] = "time is up for one run: run this snippet again, it goes on where it stopped"
    if res["fusion_items"]:
        by_ = dict((sp_["id"], sp_) for sp_ in CFG["fx_items"])
        res["fusion_clips"] = [{"id": k, "clip": (by_.get(k) or {}).get("clip"), "track": (by_.get(k) or {}).get("track"),
                                "tc": (by_.get(k) or {}).get("tc")} for k in res["fusion_items"]]
        res["colorist"] = ("these clips carry Fusion comps (effects sit under the grade; the colorist's dump warns "
                           "fusion_comps): %s" % ", ".join("%s on V%s at %s" % (c["clip"], c["track"], c["tc"])
                                                           for c in res["fusion_clips"]))
    return res

'''

VERIFY_BODY = r'''

def _re_verify(resolve, project, CFG):
    if project is None:
        return {"error": "no_project"}
    mp = _re_load(CFG["map"], {}) or {}
    tl = _re_timeline_by_uid(project, mp["timeline_uid"]) if mp.get("timeline_uid") else None
    note = None
    if tl is None:
        tl = project.GetCurrentTimeline()
        note = "no build map: read the CURRENT timeline"
    if tl is None:
        return {"error": "no_timeline"}
    start = tl.GetStartFrame()
    g = mp.get("gfx") or {}
    rows, total = [], 0
    for kind in ("video", "audio"):
        for t in range(1, (_re_call(tl, "GetTrackCount", kind) or 0) + 1):
            for it in _re_items(tl, kind, t):
                if CFG["start"] <= total < CFG["start"] + CFG["count"]:
                    m = _re_call(it, "GetMediaPoolItem")
                    cp = _re_clip_props(m) if m is not None else {}
                    s, e = _re_call(it, "GetStart"), _re_call(it, "GetEnd")
                    sp = _re_call(it, "GetSpeed")
                    fd = _re_call(it, "GetFades")
                    pr = _re_call(it, "GetProperties") or {}
                    if not isinstance(pr, dict):
                        pr = {}
                    mu = _re_call(m, "GetUniqueId") if m is not None else None
                    row = {"kind": kind, "track": t, "type": _re_type(it), "name": _re_call(it, "GetName"),
                           "uid": _re_call(it, "GetUniqueId"),
                           "rec_in": (s - start) if isinstance(s, (int, float)) else None,
                           "rec_out": (e - start) if isinstance(e, (int, float)) else None,
                           "path": cp.get("File Path"), "src_in": _re_src_in(it, cp) if m is not None else None,
                           "speed": float(sp.get("Percentage", 100.0)) if isinstance(sp, dict) else None,
                           "fades": [fd.get("FadeIn", 0), fd.get("FadeOut", 0)] if isinstance(fd, dict) else None,
                           "volume": pr.get("AudioVolume"), "pan": pr.get("AudioPan"), "zoom": pr.get("ZoomX"),
                           "enabled": _re_call(it, "GetClipEnabled"), "mpi_uid": mu, "mpi_type": cp.get("Type"),
                           "color": _re_call(it, "GetClipColor")}
                    # a range of the build's graphics timeline (a title or a caption): which slot it shows
                    if (g.get("sheet_mpi_uid") and mu == g["sheet_mpi_uid"]) or cp.get("Type") == "Timeline":
                        row["left_offset"] = _re_call(it, "GetLeftOffset")
                        row["gfx"] = bool((g.get("sheet_mpi_uid") and mu == g["sheet_mpi_uid"])
                                          or (g.get("sheet") and row["name"] == g["sheet"]))
                    rows.append(row)
                total += 1
    gfx = None
    if CFG["start"] == 0 and g.get("sheet_uid"):
        sheet = _re_timeline_by_uid(project, g["sheet_uid"])
        gfx = {"sheet": g.get("sheet"), "sheet_uid": g["sheet_uid"], "items": [], "switched": False}
        if sheet is None:
            gfx["error"] = "sheet_gone"
        else:
            s0 = sheet.GetStartFrame()

            def _xy(v):
                if isinstance(v, dict):
                    return [v.get(1.0, v.get(1)), v.get(2.0, v.get(2))]
                return v

            def read_sheet():
                out = []
                for it in _re_items(sheet, "video", 1):
                    tool = None
                    try:
                        comp = it.GetFusionCompByIndex(1)
                        tool = comp.FindTool("Template") if comp is not None else None
                    except Exception:
                        tool = None
                    s = _re_call(it, "GetStart")
                    row = {"start": (s - s0) if isinstance(s, (int, float)) else None, "dur": _re_call(it, "GetDuration"),
                           "text": None, "center": None, "size": None, "name": _re_call(it, "GetName"),
                           "color": _re_call(it, "GetClipColor")}
                    if tool is not None:
                        try:
                            # always with a time: a read without one silently drops a connected Follower's
                            # styling from renders and grabs (measured, fusion animated_text notes s4 trap 1)
                            row.update({"text": tool.GetInput("StyledText", 0),
                                        "center": _xy(tool.GetInput("Center", 0)),
                                        "size": tool.GetInput("Size", 0)})
                        except Exception:
                            pass
                        if "text_anim_readback" in globals():
                            try:
                                row["anim"] = text_anim_readback(comp, tool)
                            except Exception as ex:
                                row["anim"] = {"error": str(ex)[:120]}
                    out.append(row)
                return out
            gfx["items"] = read_sheet()
            if gfx["items"] and all(x["text"] is None for x in gfx["items"]):
                # Fusion comps of a timeline that is not current may not answer: read them with the sheet current,
                # then put the current timeline back
                cur = project.GetCurrentTimeline()
                project.SetCurrentTimeline(sheet)
                try:
                    gfx["items"] = read_sheet()
                    gfx["switched"] = True
                finally:
                    if cur is not None:
                        project.SetCurrentTimeline(cur)
    mk = _re_call(tl, "GetMarkers") or {}
    markers = []
    for f in sorted(mk):
        v = mk[f] if isinstance(mk[f], dict) else {}
        markers.append({"frame": int(f), "color": v.get("color"), "name": v.get("name"), "note": v.get("note"),
                        "custom": v.get("customData")})
    sets = {"width": _re_setting(tl, "timelineResolutionWidth"), "height": _re_setting(tl, "timelineResolutionHeight"),
            "input_sizing": _re_setting(tl, "timelineInputResMismatchBehavior"),
            "fps": _re_setting(tl, "timelineFrameRate")}
    tracks_ = None
    if CFG["start"] == 0:
        # track names, read while the built timeline is current (a track's state read on a timeline that is not
        # current can be wrong in 21.1); the current timeline is put back
        tracks_ = {"video": [], "audio": [], "switched": False}
        cur_ = project.GetCurrentTimeline()
        if cur_ is None or _re_call(cur_, "GetUniqueId") != _re_call(tl, "GetUniqueId"):
            tracks_["switched"] = bool(project.SetCurrentTimeline(tl))
        try:
            for kind in ("video", "audio"):
                for t in range(1, (_re_call(tl, "GetTrackCount", kind) or 0) + 1):
                    tracks_[kind].append(_re_call(tl, "GetTrackName", kind, t))
        finally:
            if tracks_["switched"] and cur_ is not None:
                project.SetCurrentTimeline(cur_)
        tm_ = _re_call(tl, "GetMediaPoolItem")
        tracks_["comments"] = _re_call(tm_, "GetClipProperty", "Comments") if tm_ is not None else None
    fxr, xfx = None, None
    if CFG["start"] == 0 and (CFG.get("fx") or CFG.get("transitions_fx")) and "readback_fx" in globals():
        # clip comps: tool names, key counts and values at the fx frames, read with GetInput(name, comp time) only;
        # transitions built from the catalogue: their name, place, length, rebuilt tools and macro inputs
        fxr, xfx = {}, {}
        items_ = mp.get("items") or {}
        for spec in CFG.get("fx") or []:
            d = items_.get(spec["id"])
            it = None
            if d:
                for x in _re_items(tl, "video", int(d["track"])):
                    if _re_call(x, "GetUniqueId") == d.get("uid"):
                        it = x
            if it is None:
                fxr[spec["id"]] = {"found": False}
                continue
            r_ = {"found": True, "comps": _re_call(it, "GetFusionCompCount") or 0}
            if spec.get("speed_warp"):
                r_["retime_process"] = _re_call(it, "GetProperty", "RetimeProcess")
                r_["motion_estimation"] = _re_call(it, "GetProperty", "MotionEstimation")
            if r_["comps"]:
                try:
                    comp = it.GetFusionCompByIndex(1)
                    ct, info = comp_time_fn(it, comp, tl, spec.get("media_fps"), CFG["fps"], spec.get("speed", 1.0),
                                            None, still=bool(spec.get("still")))
                    r_["timebase"] = {k: info.get(k) for k in ("rs", "re", "gs", "head", "tail", "len", "want_span",
                                                                "got_span", "ok")}
                    if ct is not None:
                        m0 = media_frame_at_start(comp, it, spec.get("media_fps"))[0]
                        rb = readback_fx(comp, ct, spec["framing"], spec.get("samples") or [0], m0, info.get("rs"))
                        r_.update(tools=rb["tools"], keys=rb["keys"], samples=rb["samples"], point=rb.get("point"))
                except Exception as ex:
                    r_["error"] = str(ex)[:200]
            fxr[spec["id"]] = r_
        xd = mp.get("transitions") or {}
        for x in CFG.get("transitions_fx") or []:
            d = xd.get(x["id"])
            tr = None
            if d and d.get("uid"):
                for t in range(1, (_re_call(tl, "GetTrackCount", "video") or 0) + 1):
                    for it in _re_items(tl, "video", t):
                        if _re_call(it, "GetUniqueId") == d["uid"]:
                            tr = it
            if tr is None:
                xfx[x["id"]] = {"found": False}
                continue
            s_, e_ = _re_call(tr, "GetStart"), _re_call(tr, "GetEnd")
            r_ = {"found": True, "name": _re_call(tr, "GetName"), "dur": _re_call(tr, "GetDuration"),
                  "rec_in": (s_ - start) if isinstance(s_, (int, float)) else None,
                  "rec_out": (e_ - start) if isinstance(e_, (int, float)) else None}
            if _re_call(tr, "GetFusionCompCount"):
                comp = tr.GetFusionCompByIndex(1)
                r_["tools"] = [tool_name(t_) for t_ in tool_list(comp) if tool_regid(t_) not in MODIFIER_IDS]
                r_["groups"] = sum(1 for t_ in tool_list(comp) if tool_regid(t_) == "GroupOperator")
                if x.get("macro_inputs"):
                    r_["macro"] = dict((k, get_macro_input(tr, k)) for k in sorted(x["macro_inputs"]))
            xfx[x["id"]] = r_
    page = {"schema": "resolve-editor/readback@1", "timeline": tl.GetName(), "timeline_uid": _re_call(tl, "GetUniqueId"),
            "settings": sets,
            "start_frame": start, "start": CFG["start"], "count": CFG["count"], "total": total, "items": rows,
            "markers": markers, "gfx": gfx, "note": note, "created": time.strftime("%Y-%m-%dT%H:%M:%S")}
    if tracks_ is not None:
        page["tracks"] = tracks_
    if fxr is not None:
        page["fx"] = fxr
        page["transitions_fx"] = xfx
    _re_save(CFG["out"], page)
    return {"ok": True, "out": CFG["out"], "items": len(rows), "total": total,
            "more": total > CFG["start"] + CFG["count"]}

'''

TC_BODY = r'''

def _re_tc(frame, fps, drop=False):
    """Absolute frame number to the timecode SetCurrentTimecode takes (drop frame at 29.97 and 59.94 when asked)."""
    nom = int(round(float(fps)))
    f = int(frame)
    sep = ":"
    if drop and nom in (30, 60):
        d = 2 if nom == 30 else 4
        per10, per1 = nom * 600 - d * 9, nom * 60 - d
        tens, rem = divmod(f, per10)
        f += d * 9 * tens + (d * ((rem - d) // per1) if rem > d else 0)
        sep = ";"
    return "%02d:%02d:%02d%s%02d" % (f // (3600 * nom), (f // (60 * nom)) % 60, (f // nom) % 60, sep, f % nom)

'''

GRAB_BODY = r'''

def _re_grab(resolve, project, CFG):
    """Edit-page frame grabs of the built timeline (ExportCurrentFrameAsStill: the full output, Text+ and stills
    included). Changes nothing: the playhead and the current timeline are put back, the page is never switched."""
    t0 = time.time()
    if project is None:
        return {"error": "no_project"}
    if CFG.get("project") and project.GetName() != CFG["project"]:
        return {"error": "wrong_project", "expected": CFG["project"], "current": project.GetName()}
    if _re_call(project, "IsRenderingInProgress"):
        return {"error": "render_in_progress", "msg": "wait for the render to finish"}
    page = _re_call(resolve, "GetCurrentPage")
    if page not in ("edit", "color"):
        return {"error": "wrong_page", "page": page, "msg": "open the Edit page in Resolve, then run this again "
                                                             "(frame grabs work on the Edit and Color pages)"}
    mp = _re_load(CFG["map"], {}) or {}
    tl = _re_timeline_by_uid(project, mp["timeline_uid"]) if mp.get("timeline_uid") else None
    if tl is None:
        return {"error": "no_build", "msg": "the build map names no timeline in this project: build first"}
    out = CFG["out"]
    if not os.path.isdir(out):
        os.makedirs(out)
    # a grab of a frame with 4K clip comps takes about 1 s ([API] s5): at most per_call grabs (and budget_s) per run,
    # then more: true and the next run goes on; a finished set is started afresh by the next run
    old = _re_load(os.path.join(out, "grabs.json"), None) or {}
    resume = bool(old) and old.get("run_id") == CFG.get("run_id") and old.get("complete") is False
    rows = [r for r in (old.get("frames") or []) if r.get("ok")] if resume else []
    if not resume:
        for fn in os.listdir(out):
            if fn.startswith("g_") and fn.endswith(".png"):
                os.remove(os.path.join(out, fn))
    have = set(int(r["frame"]) for r in rows)
    todo = [int(f) for f in CFG["frames"] if int(f) not in have]
    per = int(CFG.get("per_call") or len(todo) or 1)
    cur = project.GetCurrentTimeline()
    switched = cur is None or _re_call(cur, "GetUniqueId") != mp["timeline_uid"]
    keep, more, n_now = None, False, 0
    try:
        if switched:
            project.SetCurrentTimeline(tl)
        keep = _re_call(tl, "GetCurrentTimecode")
        s0 = tl.GetStartFrame()
        fps = _re_fps(_re_setting(tl, "timelineFrameRate")) or CFG["fps"]
        drop = str(_re_setting(tl, "timelineDropFrameTimecode")).lower() in ("1", "true")
        for f in todo:
            if n_now >= per or time.time() - t0 > float(CFG.get("budget_s", 40)):
                more = True
                break
            p = os.path.join(out, "g_%06d.png" % int(f))
            ok = bool(tl.SetCurrentTimecode(_re_tc(s0 + int(f), fps, drop)))
            ok = ok and bool(project.ExportCurrentFrameAsStill(p)) and os.path.exists(p)
            rows.append({"frame": int(f), "file": os.path.basename(p), "ok": bool(ok)})
            n_now += 1
    finally:
        if keep:
            tl.SetCurrentTimecode(keep)
        if switched and cur is not None:
            project.SetCurrentTimeline(cur)
    rows.sort(key=lambda r: r["frame"])
    doc = {"schema": "resolve-editor/grabs@1", "timeline": tl.GetName(), "timeline_uid": mp["timeline_uid"],
           "frames": rows, "playhead_restored": keep, "run_id": CFG.get("run_id"), "complete": not more,
           "created": time.strftime("%Y-%m-%dT%H:%M:%S")}
    _re_save(os.path.join(out, "grabs.json"), doc)
    res = {"ok": bool(rows) and all(r["ok"] for r in rows) and not more, "frames": sum(1 for r in rows if r["ok"]),
           "failed": [r["frame"] for r in rows if not r["ok"]], "out": out, "seconds": round(time.time() - t0, 2),
           "page": _re_call(resolve, "GetCurrentPage"), "more": more, "left": len(todo) - n_now}
    if more:
        res["msg"] = "run this snippet again: %d frames are left to grab" % res["left"]
    return res

'''

DELIVER_BODY = r'''

def _re_deliver(resolve, project, CFG, FORCE=False):
    """A render job for the built timeline with the delivery settings; renders only when CFG start is true. Never
    sets an upload option. The page and the current timeline are put back."""
    t0 = time.time()
    bad = _re_guard(resolve, project, CFG, FORCE)
    if bad:
        return bad
    mp = _re_load(CFG["map"], {}) or {}
    tl = _re_timeline_by_uid(project, mp["timeline_uid"]) if mp.get("timeline_uid") else None
    if tl is None:
        return {"error": "no_build", "msg": "the build map names no timeline in this project: build first"}
    page0 = _re_call(resolve, "GetCurrentPage")
    cur = project.GetCurrentTimeline()
    res = {"job_id": None, "started": False, "status": None, "refused": []}
    try:
        project.SetCurrentTimeline(tl)
        res["preset_loaded"] = bool(project.LoadRenderPreset(CFG["render_preset"]))
        res["mode_set"] = bool(project.SetCurrentRenderMode(1))
        res["format_set"] = bool(project.SetCurrentRenderFormatAndCodec(CFG["format"], CFG["codec"]))
        for k in sorted(CFG["settings"]):
            if "upload" in k.lower():
                continue
            one = {}
            one[k] = CFG["settings"][k]
            if not project.SetRenderSettings(one):
                res["refused"].append(k)
        strict = [k for k in (CFG.get("strict") or []) if k in CFG["settings"]]
        prefer = [k for k in (CFG.get("prefer") or []) if k in CFG["settings"]]
        if any(k in res["refused"] for k in prefer):
            # an encoder without multi-pass or this profile: the job is still made, without them
            res["prefer_refused"] = [k for k in prefer if k in res["refused"]]
            res["refused"] = [k for k in res["refused"] if k not in prefer]
            res["note"] = ("Resolve did not take %s for this encoder; the job renders without %s" % (
                ", ".join(res["prefer_refused"]), "it" if len(res["prefer_refused"]) == 1 else "them"))
        if any(k in res["refused"] for k in strict):
            # no job with a burn-in, a subtitle stream or another profile than asked
            res.update(error="render_settings_refused", keys=[k for k in strict if k in res["refused"]], ok=False)
            return res
        jid = project.AddRenderJob()
        res["job_id"] = jid or None
        job = {}
        for j in (_re_call(project, "GetRenderJobList") or []):
            if isinstance(j, dict) and j.get("JobId") == jid:
                job = j
        res["settings"] = dict((k, job.get(k)) for k in CFG["readback"] if k in job)
        diff_ = [k for k in strict if k in job and job.get(k) != CFG["settings"][k]]
        poff = [k for k in prefer if k in job and job.get(k) != CFG["settings"][k]]
        if poff:
            res["prefer_off"] = poff
        if jid and diff_:
            res["job_deleted"] = bool(project.DeleteRenderJob(jid))
            res.update(error="render_settings_refused", keys=diff_, job_id=None, ok=False)
            return res
        res["path"] = os.path.join(job.get("TargetDir") or CFG["settings"].get("TargetDir", ""),
                                   job.get("OutputFilename") or "") if job else None
        if CFG.get("start") and jid:
            res["started"] = bool(project.StartRendering([jid], False))
            while res["started"] and time.time() - t0 < float(CFG.get("wait_s", 600)):
                time.sleep(0.5)
                st = project.GetRenderJobStatus(jid) or {}
                res["status"] = st
                if not _re_call(project, "IsRenderingInProgress") and st.get("JobStatus") in (
                        "Complete", "Failed", "Cancelled"):
                    break
        if CFG.get("delete_job") and jid and not _re_call(project, "IsRenderingInProgress"):
            res["job_deleted"] = bool(project.DeleteRenderJob(jid))      # a proof render leaves no job behind
    finally:
        if page0 and _re_call(resolve, "GetCurrentPage") != page0:
            res["page_restored"] = bool(resolve.OpenPage(page0))
        if cur is not None and _re_call(cur, "GetUniqueId") != mp["timeline_uid"]:
            project.SetCurrentTimeline(cur)
    res["ok"] = bool(res["job_id"]) and not res["refused"] and (not CFG.get("start") or (
        isinstance(res["status"], dict) and res["status"].get("JobStatus") == "Complete"))
    res["loudness"] = CFG.get("loudness")
    if CFG.get("start") and isinstance(res["status"], dict) and res["status"].get("JobStatus") not in (
            "Complete", "Failed", "Cancelled"):
        # run_script_unsafe stops a script after 60 s; the render goes on in Resolve
        res["note"] = "; ".join(x for x in (res.get("note"), "still rendering after %s s: watch the Deliver page's "
                                                              "render queue" % CFG.get("wait_s")) if x)
    res["page"] = _re_call(resolve, "GetCurrentPage")
    res["seconds"] = round(time.time() - t0, 2)
    return res

'''

GFX_BODY = r'''

def _re_template(it):
    try:
        comp = it.GetFusionCompByIndex(1)
        return comp.FindTool("Template") if comp is not None else None
    except Exception:
        return None


def _re_text_in(key, val):
    if key == "Center" and isinstance(val, (list, tuple)):
        return {1: float(val[0]), 2: float(val[1]), 3: 0.0}
    return val


def _re_same_text(a, b):
    def n(s):
        return str(s if s is not None else "").replace("\r\n", "\n").replace("\r", "\n").strip()
    return n(a) == n(b)


def _re_edit_bin(pool, name):
    root = pool.GetRootFolder()
    top = None
    for s in (_re_call(root, "GetSubFolderList") or []):
        if _re_call(s, "GetName") == "resolve-editor":
            top = s
    if top is None:
        top = pool.AddSubFolder(root, "resolve-editor")
    if top is None:
        return None
    for s in (_re_call(top, "GetSubFolderList") or []):
        if _re_call(s, "GetName") == name:
            return s
    return pool.AddSubFolder(top, name)


def _re_gfx(resolve, project, CFG, FORCE=False):
    """Titles and captions as Text+: one Text+ per item on the build's own graphics timeline (never on the
    programme: the insert ripples V1), then each item appended onto the programme as a range of that timeline."""
    t0 = time.time()
    bad = _re_guard(resolve, project, CFG, FORCE)
    if bad:
        return bad
    mp = _re_load(CFG["map"], None)
    if not mp or not mp.get("timeline_uid"):
        return {"error": "run_first_chunk", "msg": "run the build chunks first"}
    tl = _re_timeline_by_uid(project, mp["timeline_uid"])
    if tl is None:
        return {"error": "timeline_gone", "timeline": mp.get("timeline")}
    pool = project.GetMediaPool()
    g = mp.get("gfx") or {}
    for k, v in (("items", {}), ("refused", []), ("fallback_markers", []), ("notes", [])):
        g.setdefault(k, v)
    mp["gfx"] = g
    notes, refused = [], []
    more = False

    def refuse(sid, why):
        if sid not in g["refused"]:
            g["refused"].append(sid)
        if sid not in refused:
            refused.append(sid)
        msg = "%s: %s" % (sid, why)
        for lst in (g["notes"], notes):
            if msg not in lst:
                lst.append(msg)

    def save():
        _re_save(CFG["map"], mp)
    specs = CFG["gfx"]
    todo = [s for s in specs if s["id"] not in g["items"] and s["id"] not in g["refused"]]
    sheet = _re_timeline_by_uid(project, g["sheet_uid"]) if g.get("sheet_uid") else None
    if g.get("sheet_uid") and sheet is None:
        return {"error": "sheet_gone", "msg": "the graphics timeline %r of this build is gone; delete the build map "
                                              "and build again" % g.get("sheet")}
    if sheet is None and todo:
        names = set()
        for i in range(1, (_re_call(project, "GetTimelineCount") or 0) + 1):
            t = project.GetTimelineByIndex(i)
            if t is not None:
                names.add(t.GetName())
        name = CFG["sheet_name"]
        for suf in "bcdefghijklmnopqrstuvwxyz":
            if name not in names:
                break
            name = CFG["sheet_name"] + " " + suf
        before = _re_call(pool, "GetCurrentFolder")
        binf = _re_edit_bin(pool, CFG["bin"])
        if binf is not None:
            pool.SetCurrentFolder(binf)
        sheet = pool.CreateEmptyTimeline(name)
        if before is not None:
            pool.SetCurrentFolder(before)
        if sheet is None:
            for s in todo:
                refuse(s["id"], "the graphics timeline could not be created")
            todo = []
        else:
            g["sheet"], g["sheet_uid"] = sheet.GetName(), _re_call(sheet, "GetUniqueId")
            g["sheet_mpi_uid"] = _re_call(sheet.GetMediaPoolItem(), "GetUniqueId")
            save()
            # the same raster as the programme, so a range drops in unscaled (Text+ sizes follow the width)
            pw = int(_re_fps(_re_setting(project, "timelineResolutionWidth")) or 0)
            ph = int(_re_fps(_re_setting(project, "timelineResolutionHeight")) or 0)
            if (pw, ph) != (CFG["width"], CFG["height"]):
                for k, v in (("useCustomSettings", "1"), ("timelineResolutionWidth", str(CFG["width"])),
                             ("timelineResolutionHeight", str(CFG["height"]))):
                    if not (_re_has(sheet, "SetSetting") and sheet.SetSetting(k, v)):
                        msg = "timeline setting %s was refused on the graphics timeline" % k
                        for lst in (g["notes"], notes):
                            if msg not in lst:
                                lst.append(msg)
            got = (str(_re_setting(sheet, "timelineResolutionWidth")), str(_re_setting(sheet, "timelineResolutionHeight")))
            if got != (str(CFG["width"]), str(CFG["height"])):
                for s in todo:
                    refuse(s["id"], "the graphics timeline is %sx%s, not %dx%d" % (got[0], got[1], CFG["width"],
                                                                                  CFG["height"]))
                todo = []
    if todo:
        project.SetCurrentTimeline(sheet)
        try:
            cur = project.GetCurrentTimeline()
            if cur is None or _re_call(cur, "GetUniqueId") != g["sheet_uid"]:
                for s in todo:
                    refuse(s["id"], "the graphics timeline did not become current")
                todo = []
            s0 = sheet.GetStartFrame()
            sfps = _re_fps(_re_setting(sheet, "timelineFrameRate")) or CFG["fps"]
            drop = str(_re_setting(sheet, "timelineDropFrameTimecode")).lower() in ("1", "true")
            for spec in todo:
                if time.time() - t0 > float(CFG.get("budget_s", 40)):
                    # run_script_unsafe stops a script after 60 s: stop cleanly, the next run continues here
                    more = True
                    break
                slot = g.get("slot")
                nxt = int(g.get("next", 0))
                ends = [(_re_call(i, "GetEnd") or s0) for i in _re_items(sheet, "video", 1)]
                if ends and max(ends) - s0 > nxt:
                    # a Text+ the map does not know (a run stopped between the insert and the save): never insert
                    # before it (the insert ripples), go on after it; it stays unused on the graphics timeline
                    nxt = max(ends) - s0
                    g["next"] = nxt
                need = 1 if not slot else max(1, -(-int(spec["rec_len"]) // int(slot)))
                made, why, k = 0, None, 0
                while k < need:
                    pos = nxt + k * int(slot or 0)
                    sheet.SetCurrentTimecode(_re_tc(s0 + pos, sfps, drop))
                    it = sheet.InsertFusionTitleIntoTimeline("Text+")
                    if it is None or not _re_call(it, "GetDuration"):
                        why = "Resolve made no Text+ (Effects > Titles > Text+ missing?)"
                        break
                    made += 1
                    if _re_call(it, "GetStart") != s0 + pos:
                        why = "the Text+ landed at frame %s of the graphics timeline instead of %d" % (
                            _re_call(it, "GetStart"), s0 + pos)
                        break
                    if not slot:
                        slot = int(it.GetDuration())
                        g["slot"] = slot
                        need = max(1, -(-int(spec["rec_len"]) // slot))
                    tool = _re_template(it)
                    if tool is None:
                        why = "the Text+ has no Template tool"
                        break
                    # never inside comp.Lock(): locked values read back but do not render (measured). EnabledN
                    # first: an element's other inputs do not exist until it is on, so a colour set before it is
                    # dropped and the element keeps its default (the box turned blue, measured in 21.1)
                    keys = sorted(spec["inputs"], key=lambda k_: (not k_.startswith("Enabled"), k_))
                    for key in keys:
                        tool.SetInput(key, _re_text_in(key, spec["inputs"][key]))
                    # StyledText is read with a time only: without one a connected Follower loses its styling
                    if not _re_same_text(tool.GetInput("StyledText", 0), spec["text"]):
                        why = "the Text+ did not take its text"
                        break
                    for key in keys:
                        want_ = spec["inputs"][key]
                        if isinstance(want_, bool) or not isinstance(want_, (int, float)):
                            continue
                        if key[-1:] in ("2", "3", "4") and not key.startswith("Enabled") and \
                                not spec["inputs"].get("Enabled" + key[-1:], 1):
                            continue            # an element that stays off has no inputs to read back
                        got_ = tool.GetInput(key)
                        if not isinstance(got_, (int, float)) or abs(float(got_) - float(want_)) > 1e-4:
                            tool.SetInput(key, want_)
                            got_ = tool.GetInput(key)
                            if not isinstance(got_, (int, float)) or abs(float(got_) - float(want_)) > 1e-4:
                                msg = "%s: the Text+ input %s reads %r instead of %r" % (spec["id"], key, got_, want_)
                                for lst in (g["notes"], notes):
                                    if msg not in lst:
                                        lst.append(msg)
                    if spec.get("plan"):
                        # the animation (D4): keys on every frame of the cue, the Follower keyed, a Merge fade; a cue
                        # longer than one slot goes on on the next Text+, whose comp starts at 0 again (shift)
                        try:
                            ar_ = apply_text_plan(it.GetFusionCompByIndex(1), tool, spec["plan"], shift=k * int(slot))
                        except Exception as ex:
                            ar_ = {"ok": False, "why": str(ex)[:160]}
                        if not ar_.get("ok"):
                            msg = "%s: the animation was not built (%s); the text is static" % (spec["id"], ar_.get("why"))
                            g.setdefault("anim_failed", [])
                            if spec["id"] not in g["anim_failed"]:
                                g["anim_failed"].append(spec["id"])
                            for lst in (g["notes"], notes):
                                if msg not in lst:
                                    lst.append(msg)
                    k += 1
                if why:
                    if made:
                        ends = [(_re_call(i, "GetEnd") or s0) for i in _re_items(sheet, "video", 1)]
                        g["next"] = max(ends) - s0
                    refuse(spec["id"], why)
                    save()
                    continue
                g["items"][spec["id"]] = {"kind": spec["kind"], "slot_start": nxt, "slots": need,
                                          "len": int(spec["rec_len"]), "track": int(spec["track"]), "record": None,
                                          "uid": None, "text": spec["text"]}
                g["next"] = nxt + need * int(slot)
                save()
        finally:
            project.SetCurrentTimeline(tl)
    cur = project.GetCurrentTimeline()
    if cur is None or _re_call(cur, "GetUniqueId") != mp["timeline_uid"]:
        project.SetCurrentTimeline(tl)          # a new graphics timeline becomes current when it is created
        cur = project.GetCurrentTimeline()
    if cur is None or _re_call(cur, "GetUniqueId") != mp["timeline_uid"]:
        save()
        return {"error": "not_current", "msg": "could not make the build timeline current again"}
    start = tl.GetStartFrame()
    want = max([int(s["track"]) for s in specs] + [1])
    while (_re_call(tl, "GetTrackCount", "video") or 0) < want:
        if not tl.AddTrack("video"):
            break
    smpi = sheet.GetMediaPoolItem() if sheet is not None else None
    for spec in specs:
        d = g["items"].get(spec["id"])
        if not d or d.get("uid") or smpi is None:
            continue
        rec = start + int(spec["rec_in"])
        end = int(d["slot_start"]) + int(d["len"]) - (1 if CFG.get("append_end") == "inclusive" else 0)
        pool.AppendToTimeline([{"mediaPoolItem": smpi, "startFrame": int(d["slot_start"]), "endFrame": end,
                                "recordFrame": rec, "trackIndex": int(spec["track"])}])
        it = _re_find(tl, "video", int(spec["track"]), None, rec)
        if it is None or not _re_call(it, "GetUniqueId"):
            refuse(spec["id"], "the programme refused its range of the graphics timeline (V%d at %d)"
                   % (int(spec["track"]), int(spec["rec_in"])))
            save()
            continue
        d["uid"], d["record"] = it.GetUniqueId(), rec
        n = _re_call(it, "GetDuration")
        if n != int(d["len"]):
            d["len_placed"] = n
        if spec["id"] in g["refused"]:
            g["refused"].remove(spec["id"])
        save()
    if not more:
        g["ran"] = True
    save()
    built = [s for s in specs if (g["items"].get(s["id"]) or {}).get("uid")]
    res = {"ok": not g["refused"] and not more and not g.get("anim_failed"), "timeline": mp["timeline"],
           "sheet": g.get("sheet"),
           "titles": sum(1 for s in built if s["kind"] == "title"),
           "captions": sum(1 for s in built if s["kind"] == "caption"), "refused": list(g["refused"]),
           "animated": sum(1 for s in built if s.get("plan")), "anim_failed": list(g.get("anim_failed") or []),
           "more": more, "notes": notes, "seconds": round(time.time() - t0, 2)}
    if more:
        res["msg"] = "time is up for one run: run this snippet again, it goes on where it stopped"
    return res

'''


# ------------------------------------------------------------------- effects in the build (fusion_recipes.py)
FUSION_RECIPES_PATH = os.path.join(HERE, "fusion_recipes.py")
FX_SAMPLES_MAX = 12          # item frames the build and verify read each clip comp at


def fusion_recipes_text():
    """fusion_recipes.py as snippet text (it runs inside Resolve after COMMON_BODY; standard library only)."""
    with open(FUSION_RECIPES_PATH, encoding="utf-8") as fh:
        return "\n" + fh.read() + "\n"


def fx_samples(it):
    """Item frames worth reading back: the first, event and last frame of every fx, the retime keys' ends and
    middle, the item's first and last frame (at most FX_SAMPLES_MAX, sorted)."""
    L = rec_len(it)
    want = [0, L - 1]
    for x in it.get("fx") or []:
        if x.get("refused"):
            continue
        f = x.get("f") or []
        want += [int(v) for v in f[:2]] + ([int(x["event_f"])] if x.get("event_f") is not None else [])
    rk = (it.get("retime") or {}).get("keys") or []
    if rk:
        want += [int(rk[0][0]), int(rk[len(rk) // 2][0]), int(rk[-1][0])]
    mk = ((it.get("motion") or {}).get("keys") or {})
    for ks in mk.values():
        if ks:
            want += [int(ks[0][0]), int(ks[-1][0])]
    out = sorted(set(int(f) for f in want))
    if len(out) > FX_SAMPLES_MAX:
        mid = out[2:-2]
        step = len(mid) / float(FX_SAMPLES_MAX - 4)
        out = sorted(set(out[:2] + out[-2:] + [mid[int(i * step)] for i in range(FX_SAMPLES_MAX - 4)]))
    return out


def fx_item_specs(edl):
    """The effects snippet's work list (and verify's): one spec per picture item that carries keyed motion, accents,
    a retime or Speed Warp, with what the Resolve side needs: the static framing (frame_geometry and the item's
    transform: Transform Size 1.0 in the clip comp IS that framing), media rate, speed, the sample frames."""
    W, H = int(edl["timeline"]["width"]), int(edl["timeline"]["height"])
    out = []
    for tr in edl["tracks"]["video"]:
        tn = track_num(tr.get("id"))
        for it in tr.get("items", []):
            if not is_media_item(it):
                continue
            sw = it.get("retime_process") == "speed_warp"
            acc = it.get("accents") or {}
            acc = dict((k, v) for k, v in acc.items() if v) if isinstance(acc, dict) else {}
            if not (it.get("motion") or acc or it.get("retime") or sw):
                continue
            m = edl["media"][it["media"]]
            still = m.get("kind") == "image"
            tf = it.get("transform") or {}
            geo = frame_geometry(edl, it)
            iw, ih = (float(geo[0]), float(geo[1])) if geo else (float(W), float(H))
            # the clip's name and record timecode for the hand-over (EDL ids mean nothing in Resolve's UI)
            tl_ = edl["timeline"]
            try:
                fps_ = tl_fps(edl)
                tc_ = frames_to_tc(tc_to_frames(tl_.get("start_tc") or "01:00:00:00", fps_,
                                                bool(tl_.get("drop_frame"))) + int(it["rec_in"]), fps_,
                                   bool(tl_.get("drop_frame")))
            except (ValueError, KeyError, TypeError, ZeroDivisionError):
                tc_ = None
            clip_ = m.get("name") or os.path.basename(str(m.get("path") or it["media"]))
            out.append({"id": it["id"], "track": tn, "rec_in": int(it["rec_in"]), "rec_len": rec_len(it),
                        "clip": clip_, "tc": tc_,
                        "media_fps": None if still else float(media_fps(edl, it["media"])), "speed": speed_of(it),
                        "still": still, "src_in": int(it.get("src_in", 0) or 0),
                        "framing": {"iw": round(iw, 4), "ih": round(ih, 4), "z": float(tf.get("zoom", 1.0) or 1.0),
                                    "pan": float(tf.get("pan_px", 0) or 0), "tilt": float(tf.get("tilt_px", 0) or 0),
                                    "W": W, "H": H},
                        "motion": it.get("motion") or None, "accents": acc or None, "retime": it.get("retime") or None,
                        "speed_warp": sw, "fx_ids": [x.get("id") for x in it.get("fx") or [] if not x.get("refused")],
                        "samples": fx_samples(it)})
            # the comp's name and the clip's colour in Resolve (R7, R8): "RE push 1.00-1.06", Orange
            out[-1]["comp_name"] = comp_name_of(dict(out[-1], tl_fps=float(tl_fps(edl))))
            if fx_expected_tools(out[-1]):
                out[-1]["label"] = {"name": None, "color": ITEM_COLORS["comp"]}
    return out


def fx_transition_specs(edl, sem):
    """The finish snippet's transitions: Resolve strings and build route from the EDL's catalogue block (D5), or the
    semantics table for an EDL written before the catalogue; clip-pair routes (whip_pair, custom_zoom_through) place
    no transition item (their keys live in the two items' motion). An A/V transition with "audio" plus3 or zero adds
    the audio cross fade on the linked audio items ([TR] s7: a video transition leaves the sound a hard cut).
    Returns (specs, clip_pair_ids)."""
    tt = sem.get("transition_types") or {}
    by_link = {}
    for tr in edl["tracks"].get("audio", []):
        for it in tr.get("items", []):
            if it.get("link") and is_media_item(it):
                by_link[it["link"]] = it["id"]
    vlink = {it["id"]: it.get("link") for kind, tr, it in iter_items(edl, ("video",)) if it.get("link")}
    xs, pairs = [], []
    for x in edl.get("transitions", []):
        rv = x.get("resolve") or {}
        if rv.get("build") == "clip_pair":
            pairs.append(x.get("id"))
            continue
        if rv.get("type"):
            nm = [rv["type"], rv.get("category") or "simple"]
        else:
            nm = tt.get(x.get("type")) or tt.get("cross_dissolve") or ["Cross Dissolve", "simple"]
        spec = {"id": x.get("id"), "from": x.get("from"), "to": x.get("to"), "name": nm[0], "category": nm[1],
                "frames": int(x.get("frames", 12)), "alignment": x.get("alignment", "center")}
        if rv:
            spec.update(build=rv.get("build") or "transition", params=x.get("params") or {},
                        macro_inputs=rv.get("macro_inputs") or {})
        xs.append(spec)
        au = x.get("audio")
        if au in ("plus3", "zero"):
            a_from, a_to = by_link.get(vlink.get(x.get("from"))), by_link.get(vlink.get(x.get("to")))
            if a_from and a_to:
                xs.append({"id": "%s.a" % x.get("id"), "from": a_from, "to": a_to,
                           "name": "Cross Fade +3 dB" if au == "plus3" else "Cross Fade 0 dB", "category": "audio",
                           "frames": int(x.get("frames", 12)), "alignment": x.get("alignment", "center")})
    return xs, pairs


def attach_text_plan(spec, it, W, H, fps, notes):
    """An animated title or caption carries its Text+ plan (fx_lab.text_plan, the same code path as the preview,
    D4). The plan's static inputs join the spec's inputs, its text becomes the text the build checks."""
    an = it.get("anim") or {}
    if not isinstance(an, dict) or an.get("id") in (None, "none"):
        return
    try:
        tp = fx_lab.text_plan(it, W, H, fps)
    except Exception as ex:          # an unknown anim, or a stub: built static, verify says so
        notes.append("%s: the animation %r has no Text+ plan (%s); it is built static" % (it.get("id"), an.get("id"), ex))
        return
    plan = (tp or {}).get("plan")
    if not plan:
        return
    spec["plan"] = plan
    if plan.get("text"):
        spec["text"] = plan["text"]
        spec["inputs"]["StyledText"] = plan["text"]
    for k, v in plan.get("static") or []:
        if k != "Center":
            spec["inputs"][k] = v


def snippet_head(lab, name, what, ver=None):
    """The first line of a snippet. With ver (a built version) it names the timeline the build made, from the build
    map; otherwise the timeline read at the start (the dump)."""
    r = lab.project.get("resolve") or {}
    tl = r.get("timeline")
    if ver:
        tl = (load_json_or(lab.p("build", "%s.map.json" % ver), None) or {}).get("timeline") or tl
    where = "project %s timeline %s" % (json.dumps(r.get("project") or "?"), json.dumps(tl or "?"))
    return "# resolve-editor %s for %s, generated %s. %s\n" % (name, where, iso_now(), what)


def run_line(path):
    p = posix(path)
    arg = 'r"%s"' % p if '"' not in p else json.dumps(p)
    return "RUN run_script_unsafe: exec(open(%s, encoding=\"utf-8\").read())" % arg


def cfg_literal(cfg):
    txt = json.dumps(cfg, indent=1, ensure_ascii=False)
    if '"""' in txt or txt.endswith("\\"):
        return "CFG = %r\n" % (cfg,)
    return 'CFG = json.loads(r"""%s""")\n' % txt


def write_snippet(lab, fname, text, quiet=False):
    compile(text, fname, "exec")
    d = lab.p("snippets")
    os.makedirs(d, exist_ok=True)
    fn = os.path.join(d, fname)
    with open(fn, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    if not quiet:
        print(run_line(fn))
    return fn


def cmd_dump_script(lab, bin_name=None):
    """bin_name: one bin name or a list of them (a bin by its name, or its path such as Master/Day 1; the bins
    inside a named bin count too)."""
    os.makedirs(lab.path, exist_ok=True)
    bins = [b for b in ([bin_name] if isinstance(bin_name, str) else list(bin_name or [])) if b and str(b).strip("/")]
    cfg = {"out": posix(lab.p("resolve", "dump.json")),
           "project_json": posix(lab.p("project.json")) if os.path.exists(lab.p("project.json")) else None,
           "edits_dir": posix(lab.p("edits")), "bins": bins, "max_items": 2000}
    text = (snippet_head(lab, "dump", "Read only: writes resolve/dump.json into the lab, changes nothing in Resolve.")
            + SNIPPET_HEADER + COMMON_BODY + DUMP_BODY + cfg_literal(cfg) + "result = _re_dump(resolve, project, CFG)\n")
    return write_snippet(lab, "dump.py", text)


def cmd_backup_script(lab):
    cfg = {"dir": posix(lab.p("backup"))}
    text = (snippet_head(lab, "backup", "Changes nothing in the project: exports the CURRENT timeline as .drt and .otio "
                                        "plus a JSON dump into LAB/backup/<stamp>/.")
            + SNIPPET_HEADER + COMMON_BODY + DUMP_BODY + BACKUP_BODY + cfg_literal(cfg)
            + "result = _re_backup(resolve, project, CFG)\n")
    fn = write_snippet(lab, "backup.py", text)
    print("restore: File > Import > Timeline and pick LAB/backup/<stamp>/before.drt (your timeline itself is never "
          "changed by the build)")
    return fn


def edl_version(edl, edl_path):
    v = (edl.get("version") or {}).get("id")
    return safe_name(v or os.path.splitext(os.path.basename(edl_path))[0])


DUCK_STEP_DB = 6.0      # the deepest level change one Cross Fade 0 dB join of a ducking ramp may make


def duck_split(it):
    """How the build makes an item's volume envelope (music ducking) in Resolve, which has no volume keyframes in its
    API: the item is split in the middle of every ramp, each piece gets the level of its plateau, and a centred
    Cross Fade 0 dB as long as the ramp joins the pieces. The pieces play one continuous stretch of the source, so the
    crossfade is a level ramp. Returns ([{"rec0", "rec1", "gain"}], [{"at", "frames"}]) relative to rec_in, or None.

    A ramp deeper than DUCK_STEP_DB is built as steps of at most that many dB (each piece at least 2 frames): a Cross
    Fade 0 dB between two levels far apart bends the ramp, so one deep crossfade sounds harsher than the preview's
    straight dB ramp (measured up to 4 dB off in the middle of a 16 dB ramp)."""
    # sort by frame only: a step (two points on one frame, like a duck that starts at frame 0) keeps its order
    env = sorted(((int(p[0]), float(p[1])) for p in (it.get("volume_env") or [])), key=lambda q: q[0])
    if not env:
        return None
    L = rec_len(it)
    base = float(it.get("gain_db", 0) or 0)
    xp, fp = [p[0] for p in env], [p[1] for p in env]
    bounds = []
    for (f0, v0), (f1, v1) in zip(env, env[1:]):
        if f1 <= f0 or abs(v1 - v0) <= 0.05:
            continue
        n = max(1, int(math.ceil(abs(v1 - v0) / DUCK_STEP_DB - 1e-9)))
        n = min(n, max(1, (f1 - f0) // 2))
        for i in range(n):
            a_ = f0 + (f1 - f0) * i / float(n)
            b_ = f0 + (f1 - f0) * (i + 1) / float(n)
            at = int(round((a_ + b_) / 2.0))
            d = max(2, int(round(b_ - a_)))
            d += d % 2
            if 2 <= at <= L - 2 and (not bounds or at - bounds[-1][0] >= 2):
                bounds.append([at, d])
    edges = [0] + [b[0] for b in bounds] + [L]
    pieces = []
    for a, b in zip(edges, edges[1:]):
        lv = float(np.median(np.interp(np.arange(a, b) + 0.5, xp, fp)))
        pieces.append({"rec0": a, "rec1": b, "gain": round(base + lv, 2)})
    xf = []
    for k, (at, d) in enumerate(bounds):
        room = min(pieces[k]["rec1"] - pieces[k]["rec0"], pieces[k + 1]["rec1"] - pieces[k + 1]["rec0"])
        d = min(d, 2 * (room // 2))
        if d >= 2 and abs(pieces[k]["gain"] - pieces[k + 1]["gain"]) > 0.05:
            xf.append({"at": at, "frames": d, "piece": k})
    return pieces, xf


def duck_pieces_of(edl):
    """{item id: (track, item, pieces, crossfades)} for every audio item the build splits."""
    out = {}
    for tr in edl["tracks"].get("audio", []):
        for it in tr.get("items", []):
            if is_media_item(it) and it.get("volume_env") and speed_of(it) == 1.0:
                sp = duck_split(it)
                # one piece too: an envelope at one level all the way (music under speech from start to end) is that
                # level, not the item's base gain
                if sp and sp[0]:
                    out[it["id"]] = (tr, it, sp[0], sp[1])
    return out


def piece_item(it, pc, k, n, mf, tfps):
    """One piece of a split item as an EDL-like item."""
    s_in = int(it["src_in"]) + int(round(pc["rec0"] * float(mf) / float(tfps)))
    s_out = int(it["src_in"]) + int(round(pc["rec1"] * float(mf) / float(tfps)))
    return {"id": "%s.p%d" % (it["id"], k + 1), "kind": "clip", "media": it["media"], "src_in": s_in,
            "src_out": s_out if k < n - 1 else int(it["src_out"]), "rec_in": int(it["rec_in"]) + pc["rec0"],
            "rec_out": int(it["rec_in"]) + pc["rec1"], "speed": 1.0, "gain_db": pc["gain"], "pan": it.get("pan", 0),
            "fade_in": min(int(it.get("fade_in", 0)), pc["rec1"] - pc["rec0"]) if k == 0 else 0,
            "fade_out": min(int(it.get("fade_out", 0)), pc["rec1"] - pc["rec0"]) if k == n - 1 else 0}


def build_plan(edl, lab, sem):
    """Placement units in record order (per track), each one clipInfo, with the post steps of its items."""
    tfps = tl_fps(edl)
    ae = sem.get("append_end", "exclusive")
    at_frames = sem.get("append_end_at_media_frames", "unknown")
    psign = 1.0 if sem.get("pan_positive", "right") == "right" else -1.0
    tsign = 1.0 if sem.get("tilt_positive", "up") == "up" else -1.0
    keeps = sem.get("set_speed_no_ripple") == "keeps_duration"
    img_units = sem.get("pan_tilt_units") == "image"
    notes, units, links = [], [], []
    mix_g = float((edl.get("mix") or {}).get("gain_db") or 0.0)

    def rng(it):
        m = edl["media"][it["media"]]
        mf = media_fps(edl, it["media"])
        L = rec_len(it)
        if speed_of(it) == 1.0 or keeps:
            # SetSpeed without ripple keeps the placed length (measured), so a retimed item is placed at the
            # length it must end up with and SetSpeed then changes only how much source it plays. Resolve floors
            # a mixed-rate length (placed_len), so the source count is the one that lands exactly on L.
            n_src = src_for_len(L, mf, tfps)
            if n_src is None:
                n_src = int(math.ceil(Fraction(L) * mf / tfps))
                notes.append("%s: a %s fps clip cannot be exactly %d frames long on this timeline; it comes out %d"
                             % (it["id"], fps_label(mf), L, placed_len(n_src, mf, tfps)))
        else:
            n_src = int(it["src_out"]) - int(it["src_in"])
        end_x = int(it["src_in"]) + n_src
        tail = False
        fr = m.get("frames")
        if fr is not None and end_x > int(fr):
            notes.append("%s: Resolve needs %d source frames after its in point but only %d exist, so it comes out "
                         "short; start it earlier or shorten it" % (it["id"], n_src, int(fr) - int(it["src_in"])))
            end_x = int(fr)
        if fr is not None and end_x == int(fr) and ae == "exclusive" and at_frames != "ok":
            end_x -= 1
            tail = True
        return int(it["src_in"]), (end_x if ae == "exclusive" else end_x - 1), tail

    def vpost(it):
        tf = it.get("transform") or {}
        z = float(tf.get("zoom", 1.0))
        props = {}
        if abs(z - 1.0) > 1e-9:
            props.update({"ZoomX": z, "ZoomY": z})
        ku = image_units(edl, it["media"]) if img_units else None
        if img_units and ku is None and (tf.get("pan_px") or tf.get("tilt_px")):
            notes.append("%s: media size unknown, so pan and tilt are sent as Resolve units (check the framing)" % it["id"])
        kx, ky = ku or (1.0, 1.0)
        if float(tf.get("pan_px", 0) or 0):
            props["Pan"] = round(float(tf["pan_px"]) * psign / kx, 3)
        if float(tf.get("tilt_px", 0) or 0):
            props["Tilt"] = round(float(tf["tilt_px"]) * tsign / ky, 3)
        if float(tf.get("rotation", 0) or 0):
            props["RotationAngle"] = float(tf["rotation"])
        if float(tf.get("opacity", 100)) != 100:
            props["Opacity"] = float(tf["opacity"])
        return {"fades": [int(it.get("fade_in", 0)), int(it.get("fade_out", 0))], "props": props}

    def apost(it):
        props = {}
        # the EDL mix gain (I1) rides on every audio item, ducking pieces included (their gain_db is the piece's)
        g_ = float(it.get("gain_db", 0) or 0) + mix_g if mix_g else float(it.get("gain_db", 0) or 0)
        if g_:
            props["AudioVolume"] = round(g_, 3) if mix_g else g_
        if float(it.get("pan", 0) or 0):
            props["AudioPan"] = float(it["pan"])
        if it.get("volume_env") and it["id"] not in ducked:
            notes.append("%s: the volume envelope of a retimed item is not built; static gain %+g dB set instead. "
                         "Use `stem` for an exact file." % (it["id"], float(it.get("gain_db", 0))))
        return {"fades": [int(it.get("fade_in", 0)), int(it.get("fade_out", 0))], "props": props}
    ducked = duck_pieces_of(edl)
    alinks = {}
    for tr in edl["tracks"]["audio"]:
        for it in tr.get("items", []):
            if it.get("link") and is_media_item(it):
                alinks[it["link"]] = (tr, it)
    used = set()
    for tr in edl["tracks"]["video"]:
        tn = track_num(tr.get("id"))
        for it in tr.get("items", []):
            if it.get("kind", "clip") != "clip":
                if it.get("kind") == "solid":
                    notes.append("%s: a solid is preview only in v1, so the timeline has a hole on V%d at %s (%d "
                                 "frames); add a Solid Color generator there by hand" % (
                                     it["id"], tn, tl_tc(edl, int(it["rec_in"])), rec_len(it)))
                continue
            m = edl["media"][it["media"]]
            if m.get("kind") == "image":
                # a still ignores startFrame and endFrame (it always lands 5 s long); marks on its media pool item
                # set its length, so it is placed on its own with marks 0 to n - 1 (resolve_semantics still_length)
                units.append({"still": True, "media": it["media"], "rec_in": int(it["rec_in"]), "rec_len": rec_len(it),
                              "start": 0, "end": rec_len(it), "track": tn, "speed": 1.0, "tail_trimmed": False,
                              "parts": [dict({"id": it["id"], "kind": "video", "track": tn}, **vpost(it))]})
                continue
            a = alinks.get(it.get("link")) if it.get("link") else None
            s, e, tail = rng(it)
            u = {"media": it["media"], "rec_in": int(it["rec_in"]), "rec_len": rec_len(it), "start": s, "end": e,
                 "track": tn, "speed": speed_of(it), "tail_trimmed": tail,
                 "parts": [dict({"id": it["id"], "kind": "video", "track": tn}, **vpost(it))]}
            if a:
                ta, ai = a
                same = (ai["media"] == it["media"] and int(ai["src_in"]) == int(it["src_in"])
                        and int(ai["src_out"]) == int(it["src_out"]) and int(ai["rec_in"]) == int(it["rec_in"])
                        and int(ai["rec_out"]) == int(it["rec_out"]) and track_num(ta.get("id")) == tn
                        and speed_of(ai) == speed_of(it) == 1.0)
                if same:
                    u["parts"].append(dict({"id": ai["id"], "kind": "audio", "track": tn}, **apost(ai)))
                    used.add(ai["id"])
                else:
                    links.append([it["id"], ai["id"]])
                    u["media_type"] = 1
            else:
                u["media_type"] = 1
            units.append(u)
    for tr in edl["tracks"]["audio"]:
        tn = track_num(tr.get("id"))
        for it in tr.get("items", []):
            if not is_media_item(it) or it["id"] in used:
                continue
            if it["id"] in ducked:
                pieces = ducked[it["id"]][2]
                mf = media_fps(edl, it["media"])
                for k, pc in enumerate(pieces):
                    pit = piece_item(it, pc, k, len(pieces), mf, tfps)
                    s, e, tail = rng(pit)
                    units.append({"media": it["media"], "rec_in": int(pit["rec_in"]), "rec_len": rec_len(pit), "start": s,
                                  "end": e, "track": tn, "speed": 1.0, "tail_trimmed": tail, "media_type": 2,
                                  "parts": [dict({"id": pit["id"], "kind": "audio", "track": tn}, **apost(pit))]})
                continue
            s, e, tail = rng(it)
            units.append({"media": it["media"], "rec_in": int(it["rec_in"]), "rec_len": rec_len(it), "start": s,
                          "end": e, "track": tn, "speed": speed_of(it), "tail_trimmed": tail, "media_type": 2,
                          "parts": [dict({"id": it["id"], "kind": "audio", "track": tn}, **apost(it))]})
    units.sort(key=lambda u: (u["rec_in"], 0 if u["parts"][0]["kind"] == "video" else 1, u["track"]))
    return units, links, notes


def marker_plan(edl, ver, text_markers=True):
    """The finish snippet's markers. text_markers False (titles built as Text+): no Cream title markers; the finish
    makes one only for a title the graphics snippet could not build (gfx_fallback_markers)."""
    tfps = tl_fps(edl)
    by = {}
    for mk in edl.get("markers", []):
        by.setdefault(int(mk["frame"]), []).append({"color": mk.get("color") or "Blue", "name": mk.get("name") or "",
                                                     "note": mk.get("note") or "", "duration": int(mk.get("duration", 1) or 1),
                                                     "id": mk.get("id") or "m%d" % int(mk["frame"])})
    for tr in edl["tracks"]["video"]:
        for it in tr.get("items", []):
            if it.get("kind") == "title" and text_markers:
                by.setdefault(int(it["rec_in"]), []).append({
                    "color": "Cream", "name": "TITLE %s" % it["id"],
                    "note": "%s (%s, %d f): make a Text+ title here" % (it.get("text", ""), it.get("style"), rec_len(it)),
                    "duration": rec_len(it), "id": it["id"]})
    out = []
    for f in sorted(by):
        ms = by[f]
        out.append({"frame": f, "color": ms[0]["color"], "name": " | ".join(m["name"] for m in ms if m["name"]),
                    "note": " | ".join(m["note"] for m in ms if m["note"]), "duration": max(m["duration"] for m in ms),
                    "custom": "resolve-editor|%s|%s" % (ver, ",".join(m["id"] for m in ms))})
    return out


# ------------------------------------------------------- what the build leaves for the next person (I7, final fix)
# Clip colours of the items the build makes (measured in 21.1: TimelineItem.SetClipColor takes these names, the pool
# clip keeps its own colour; SetName renames the timeline item only). Footage stays plain.
ITEM_COLORS = {"title": "Apricot", "caption": "Tan", "still": "Purple", "comp": "Orange", "music": "Teal"}
ITEM_LABEL_MAX = 40
HANDOFF_NAME = "resolve-editor hand-off"


def item_label(prefix, iid, text):
    """"TITLE t01 Precision. Patience." cut to ITEM_LABEL_MAX characters (line breaks as spaces)."""
    return ("%s %s %s" % (prefix, iid, " ".join(str(text or "").split()))).strip()[:ITEM_LABEL_MAX]


def track_name_plan(edl, gp):
    """[[kind, index, name]] for every track the build makes: the EDL's track names, "Captions" for the caption track,
    "(empty)" for a track the EDL does not have (A1 of a music-only piece)."""
    out = []
    for kind in ("video", "audio"):
        trs = edl["tracks"].get(kind) or []
        names = {}
        for tr in trs:
            names[track_num(tr.get("id"))] = str(tr.get("name") or tr.get("id") or "").strip() or "(empty)"
        n = max(list(names) + [1] + ([int(gp.get("tracks_video") or 1)] if kind == "video" else []))
        for i in range(1, n + 1):
            if kind == "video" and gp.get("caption_track") and i == int(gp["caption_track"]):
                out.append([kind, i, "Captions"])
            else:
                out.append([kind, i, names.get(i, "(empty)")])
    return out


def label_plan(edl, units, gp):
    """{part id: {"name": str or None, "color": str}} for what the finish snippet labels on the programme: stills
    (Purple, their name kept), music ducking pieces ("MUSIC m01 -5.5 dB", Teal) and the title and caption ranges
    (TITLE/CAP, Apricot/Tan; the graphics timeline's Text+ items carry the same). Clips that get a Fusion comp are
    coloured Orange by the effects snippet (fx_item_specs "label")."""
    out = {}
    pieces = {}
    for iid, (tr, it, pcs, xfs) in duck_pieces_of(edl).items():
        for k in range(len(pcs)):
            pieces["%s.p%d" % (iid, k + 1)] = iid
    comp = {s["id"] for s in fx_item_specs(edl) if fx_expected_tools(s)}
    for u in units:
        for p in u["parts"]:
            if u.get("still") and p["id"] not in comp:
                out[p["id"]] = {"name": None, "color": ITEM_COLORS["still"]}
            elif p["id"] in pieces:
                g_ = float((p.get("props") or {}).get("AudioVolume", 0.0) or 0.0)
                out[p["id"]] = {"name": item_label("MUSIC", pieces[p["id"]], "%g dB" % round(g_, 2)),
                                "color": ITEM_COLORS["music"]}
    for s in gp.get("items") or []:
        if s.get("label"):
            out[s["id"]] = dict(s["label"])
    return out


def comp_name_of(spec):
    """The clip comp's name in Resolve for a fx spec: "RE push 1.00-1.06", "RE ramp 100-50 %", "RE accent flash"."""
    parts = []
    mk = (spec.get("motion") or {}).get("keys") or {}
    if mk.get("zoom"):
        z = [float(v[1]) for v in mk["zoom"]]
        parts.append("push %.2f-%.2f" % (z[0], z[-1]))
    elif mk:
        parts.append("move")
    rk = (spec.get("retime") or {}).get("keys") or []
    if len(rk) >= 2:
        # speed in % of the media's own rate: source frames per timeline frame x timeline fps / media fps
        k_ = float(spec.get("tl_fps") or 0) / float(spec.get("media_fps") or spec.get("tl_fps") or 1)

        def pct(a, b):
            df = float(b[0]) - float(a[0])
            return 100.0 * (float(b[1]) - float(a[1])) / df * k_ if df else 0.0
        parts.append("ramp %d-%d %%" % (int(round(pct(rk[0], rk[1]))), int(round(pct(rk[-2], rk[-1])))))
    acc = sorted(k for k, v in (spec.get("accents") or {}).items() if v)
    if acc:
        parts.append("accent %s" % "+".join({"rgb_px": "rgb split"}.get(a, a) for a in acc))
    return ("RE " + ", ".join(parts))[:60] if parts else None


def handoff_plan(lab, edl, ver, units, gp, fxs, loud):
    """The hand-off marker and the timeline's Comments: what this timeline depends on and what is left to do, from
    the build facts. "{sheet}" is replaced by the graphics timeline's real name in the finish snippet."""
    lines = ["Built by resolve-editor from %s (%s)." % (ver, lab.path)]
    if gp.get("items"):
        lines.append("Titles and captions are ranges of the timeline {sheet}: keep it in the project.")
    comp = [s for s in fxs if fx_expected_tools(s)]
    if comp:
        lines.append("Fusion comps (Orange): %s." % ", ".join("%s at %s" % (s["clip"], s["tc"]) for s in comp[:8])
                     + (" and %d more" % (len(comp) - 8) if len(comp) > 8 else ""))
    dp = duck_pieces_of(edl)
    if dp:
        lines.append("Music ducking is built as pieces joined by Cross Fade 0 dB (Teal): %s." % ", ".join(
            "%s in %d" % (k, len(v[2])) for k, v in sorted(dp.items())))
    if loud:
        if loud.get("residual_lu") is not None:
            lines.append("Loudness: mix gain %+g dB, preview %.1f LUFS, %+.1f LU left for the Deliver page." % (
                loud.get("mix_gain_db") or 0.0, loud["preview_lufs"], loud["residual_lu"]))
        elif loud.get("mix_gain_db"):
            lines.append("Loudness: mix gain %+g dB (preview not measured)." % loud["mix_gain_db"])
    return {"name": HANDOFF_NAME, "color": "Blue", "note": "\n".join(lines),
            "custom": "resolve-editor|%s|handoff" % ver}


def cmd_build_script(lab, edl_path, name=None, chunk=80, import_missing=False, text="textplus", captions=None):
    """text: textplus (titles and burn captions as Text+ from a graphics timeline) or markers (the old Cream markers).
    captions: burn (Text+ cues) or file (export-srt only); None takes the preset's captions.deliver (default burn)."""
    edl = load_edl(edl_path)
    errs, warns = validate(edl, lab)
    if errs:
        raise Fail("the EDL does not validate (%s); fix it before building" % "; ".join(
            "%s %s" % (e["code"], e["item"]) for e in errs[:5]))
    r_fps = project_fps(lab)
    if r_fps is not None and r_fps != tl_fps(edl):
        raise Fail("fps_mismatch: " + fps_mismatch_msg(edl, r_fps))
    if text not in ("textplus", "markers"):
        raise Fail("--text is textplus or markers, not %r" % text)
    if captions is None:
        try:
            captions = ((load_preset(edl.get("preset"), lab, required=False).get("captions") or {}).get("deliver")
                        or "burn")
        except Fail:
            captions = "burn"
    if captions not in ("burn", "file"):
        raise Fail("--captions is burn or file, not %r" % captions)
    sem = load_semantics()
    ver = edl_version(edl, edl_path)
    proj = lab.project
    r = proj.get("resolve") or {}
    edit = name or proj.get("name") or edl["timeline"].get("name") or "edit"
    units, links, notes = build_plan(edl, lab, sem)
    gp = gfx_plan(edl, lab, sem, captions=captions, text=text)
    notes += gp["notes"]
    if edl.get("fx_refused"):
        notes.append("fx_refused: %d effect%s assemble refused %s not in this EDL and will not be built (%s); fix "
                     "the cut list and assemble again" % (
                         len(edl["fx_refused"]), "" if len(edl["fx_refused"]) == 1 else "s",
                         "is" if len(edl["fx_refused"]) == 1 else "are",
                         ", ".join(str(r.get("id")) for r in edl["fx_refused"][:6])))
    chunk = max(1, int(chunk))
    chunks, cur, n = [], [], 0
    for u in units:
        if cur and n + 1 > chunk:
            chunks.append(cur)
            cur, n = [], 0
        cur.append(u)
        n += 1
    if cur:
        chunks.append(cur)
    if not chunks:
        raise Fail("the EDL has nothing Resolve can build (no clip items)")
    mp_path = lab.p("build", "%s.map.json" % ver)
    base = {"edl": lab.relto(edl_path), "version": ver, "project": r.get("project"), "project_uid": r.get("project_uid"),
            "name": "%s %s (resolve-editor)" % (edit, ver), "bin": safe_name(edit), "fps": float(tl_fps(edl)),
            "width": int(edl["timeline"]["width"]), "height": int(edl["timeline"]["height"]),
            "input_sizing": (edl["timeline"].get("resolve") or {}).get("input_sizing"),
            "start_tc": edl["timeline"].get("start_tc"),
            "tracks": {"video": max([track_num(t.get("id")) for t in edl["tracks"]["video"]] + [1] + [gp["tracks_video"]]),
                       "audio": max([track_num(t.get("id")) for t in edl["tracks"]["audio"]] + [1])},
            # stills are media too: IMPORT_MISSING imports a logo card like any clip
            "media": {mid: {"path": m.get("path"), "kind": m.get("kind"), "track1": (m.get("audio") or {}).get("track1")}
                      for mid, m in edl["media"].items() if any(u["media"] == mid for u in units)},
            "map": posix(mp_path), "case_insensitive": sys.platform in ("darwin", "win32"), "chunks": len(chunks),
            "text": {"mode": text, "captions": captions if text == "textplus" else "file"},
            # every track named from the EDL, the caption track "Captions", a track the EDL lacks "(empty)" (R3)
            "track_names": track_name_plan(edl, gp)}
    write_json(lab.p("build", "%s.build.json" % ver), {"schema": "resolve-editor/build-plan@1", "edl": lab.relto(edl_path),
                                                        "semantics": {k: sem.get(k) for k in ("append_end", "append_end_at_media_frames",
                                                                                               "pan_positive", "tilt_positive")},
                                                        "units": units, "links": links, "notes": notes,
                                                        "text": base["text"], "gfx": gp["items"], "created": iso_now()})
    for old in os.listdir(lab.p("snippets")) if os.path.isdir(lab.p("snippets")) else []:
        if old.startswith("build_%s_" % ver):
            os.remove(lab.p("snippets", old))
    miss = dump_missing_note(lab, edl, sorted(base["media"]), import_missing, base["bin"])
    if miss:
        print(miss)
    files = []
    chunk_of = {p["id"]: k for k, ch in enumerate(chunks, 1) for u in ch for p in u["parts"]}
    for k, ch in enumerate(chunks, 1):
        mine = [l for l in links if all(i in chunk_of for i in l) and max(chunk_of[i] for i in l) == k]
        cfg = dict(base, chunk=k, units=ch, links=mine)
        # chunk 1 creates the timeline and imports what is missing (only after the user said yes: --import-missing)
        head_flags = ("IMPORT_MISSING = %s   # True imports media that is not in the pool into the bin "
                      "resolve-editor/<edit>\n" % ("True" if (import_missing and k == 1) else "False")
                      + "FORCE = False            # True skips the project name check (only when you renamed the project)\n")
        text_ = (snippet_head(lab, "build %s chunk %d of %d" % (ver, k, len(chunks)),
                              "Writes: builds a NEW timeline %r (chunk 1 creates it); never touches other timelines. "
                              "Safe to run again: placed items are skipped." % base["name"])
                 + head_flags + SNIPPET_HEADER + COMMON_BODY + BUILD_BODY + cfg_literal(cfg)
                 + "result = _re_build(resolve, project, CFG, IMPORT_MISSING, FORCE)\n")
        files.append(write_snippet(lab, "build_%s_%02d.py" % (ver, k), text_))
    gfx_name = None
    if gp["items"]:
        gfx_name = "build_%s_gfx.py" % ver
        cfg = dict({k: base[k] for k in ("project", "project_uid", "map", "version", "bin", "fps", "width", "height")},
                   sheet_name="%s %s graphics (resolve-editor)" % (edit, ver), gfx=gp["items"],
                   append_end=sem.get("append_end", "exclusive"), budget_s=40)
        text_ = (snippet_head(lab, "build %s titles and captions" % ver,
                              "Writes: makes the graphics timeline %r (one Text+ per title and caption) and places "
                              "ranges of it on the new timeline; never touches other timelines. Safe to run again."
                              % cfg["sheet_name"])
                 + "FORCE = False\n" + SNIPPET_HEADER + COMMON_BODY + fusion_recipes_text() + BUILD_BODY + TC_BODY
                 + GFX_BODY + cfg_literal(cfg) + "result = _re_gfx(resolve, project, CFG, FORCE)\n")
        files.append(write_snippet(lab, gfx_name, text_))
    tt = sem.get("transition_types") or {}
    xs, pairs = fx_transition_specs(edl, sem)
    fxs = fx_item_specs(edl)
    fx_name = "build_%s_fx.py" % ver if fxs else None
    xf0 = tt.get("audio_xfade_0db") or ["Cross Fade 0 dB", "audio"]
    for iid, (tr, it, pieces, xfs) in sorted(duck_pieces_of(edl).items()):
        for x in xfs:
            # added at the incoming piece's start: Resolve 21.1 made half of these a frame short when they were
            # added at the outgoing piece's end (a dropout in the render), none from the start (measured)
            xs.append({"id": "%s.x%d" % (iid, x["piece"] + 1), "from": "%s.p%d" % (iid, x["piece"] + 1),
                       "to": "%s.p%d" % (iid, x["piece"] + 2), "side": "start", "name": xf0[0],
                       "category": xf0[1], "frames": int(x["frames"]), "alignment": "center"})
        mg_ = float((edl.get("mix") or {}).get("gain_db") or 0.0)
        if len(pieces) == 1:
            print("note: %s: the ducking holds one level all the way, built as a static %g dB" % (
                iid, round(pieces[0]["gain"] + mg_, 3)))
        else:
            print("note: %s: the ducking is built as %d pieces at %s dB joined by %s%s" % (
                iid, len(pieces), "/".join("%g" % round(pc["gain"] + mg_, 3) for pc in pieces), xf0[0],
                " (the mix gain %+g dB included)" % mg_ if mg_ else ""))
    if any(tr.get("items") for tr in edl["tracks"].get("subtitle", [])):
        if text == "markers":
            notes.append("captions are not built with --text markers: run export-srt and use File > Import > Subtitle")
        elif captions == "file":
            notes.append("captions are delivered as a file (captions.deliver file): run export-srt --spoken for the "
                         "upload file (every spoken cue, also the ones under titles); they are not burned into the "
                         "picture")
    cfg = dict({k: base[k] for k in ("project", "project_uid", "map", "version")}, transitions=xs,
               markers=marker_plan(edl, ver, text_markers=(text == "markers")),
               fallback=gfx_fallback_markers(gp), gfx_snippet=gfx_name,
               expected=sorted(p["id"] for u in units for p in u["parts"]), notes=notes,
               track_names=base["track_names"], labels=label_plan(edl, units, gp),
               comp_labels=dict((s_["id"], {"color": s_["label"].get("color"), "comp_name": s_.get("comp_name")})
                                for s_ in fx_item_specs(edl) if s_.get("label")),
               gfx_ids=[s["id"] for s in gp["items"]])
    # the hand-off marker and the timeline's Comments (R6), with the loudness the build carries (R1)
    try:
        loud = loudness_route(lab, edl_path, edl, load_preset(edl.get("preset"), lab, required=False))
    except Fail:
        loud = None
    cfg["loudness"] = ({"mix_gain_db": float((edl.get("mix") or {}).get("gain_db") or 0.0),
                        "preview_lufs": (loud or {}).get("preview_lufs"), "residual_lu": (loud or {}).get("residual_lu"),
                        "target": (loud or {}).get("target")})
    cfg["handoff"] = handoff_plan(lab, edl, ver, units, gp, fxs, cfg["loudness"])
    if fx_name:
        cfg["fx_snippet"] = fx_name
    text_ = (snippet_head(lab, "build %s finish" % ver, "Writes on the new timeline only: transitions and markers "
                                                         "(a title that could not be built becomes a Cream marker), "
                                                         "then a summary.")
             + "FORCE = False\n" + SNIPPET_HEADER + COMMON_BODY + fusion_recipes_text() + BUILD_BODY + cfg_literal(cfg)
             + "result = _re_finish(resolve, project, CFG, FORCE)\n")
    files.append(write_snippet(lab, "build_%s_fin.py" % ver, text_))
    if fx_name:
        # last: a clip comp is timed from the first frame its item is seen, under the transitions next to it (D3)
        cfg = dict({k: base[k] for k in ("project", "project_uid", "map", "version", "fps")}, fx_items=fxs,
                   clip_pairs=pairs, budget_s=40)
        text_ = (snippet_head(lab, "build %s effects" % ver,
                              "Writes on the new timeline only: Fusion comps on its clips (keyed moves, retimes, "
                              "flashes, splits) and Speed Warp; never touches other timelines. Safe to run again.")
                 + "FORCE = False\n" + SNIPPET_HEADER + COMMON_BODY + fusion_recipes_text() + BUILD_BODY
                 + cfg_literal(cfg) + "result = _re_fx(resolve, project, CFG, FORCE)\n")
        files.append(write_snippet(lab, fx_name, text_))
        n_fx = sum(len(f["fx_ids"]) for f in fxs)
        print("builds effects on %d item%s (%d fx entr%s%s) in %s, after the finish snippet" % (
            len(fxs), "" if len(fxs) == 1 else "s", n_fx, "y" if n_fx == 1 else "ies",
            ", clip-pair transitions %s" % ", ".join(str(p) for p in pairs) if pairs else "", fx_name))
    for n_ in notes:
        print("note: %s" % n_)
    for w in warns:
        if w.get("code") != "still_in_build":        # stills are built now (the mark route)
            print("note: %s" % w["msg"])
    n_st = sum(1 for u in units if u.get("still"))
    n_t = sum(1 for s in gp["items"] if s["kind"] == "title")
    n_c = len(gp["items"]) - n_t
    if n_st or gp["items"]:
        print("builds %d still%s, %d title%s and %d caption%s%s" % (
            n_st, "" if n_st == 1 else "s", n_t, "" if n_t == 1 else "s", n_c, "" if n_c == 1 else "s",
            (" (Text+ on the graphics timeline %r)" % ("%s %s graphics (resolve-editor)" % (edit, ver)))
            if gp["items"] else ""))
    n_an = sum(1 for s_ in gp["items"] if s_.get("plan"))
    if n_an:
        print("animates %d title%s and caption%s (Text+ keys on every frame)" % (n_an, "" if n_an == 1 else "s",
                                                                                "" if n_an == 1 else "s"))
    print("run the RUN lines in order (%d chunk%s%s, then finish%s); each one resumes where the last stopped"
          % (len(chunks), "" if len(chunks) == 1 else "s", ", then titles and captions" if gfx_name else "",
             ", then effects" if fx_name else ""))
    return files


def cmd_verify_script(lab, edl_path):
    edl = load_edl(edl_path)
    ver = edl_version(edl, edl_path)
    n = sum(1 for k, tr, it in iter_items(edl, ("video", "audio")))
    n += len(edl.get("transitions", []))
    # the build adds items the EDL does not list one by one: ducking pieces and their crossfades, caption cues
    n += sum(2 * len(v[2]) for v in duck_pieces_of(edl).values())
    n += sum(len(tr.get("items", [])) for tr in edl["tracks"].get("subtitle", []))
    pages = max(1, int(math.ceil((n + 50) / 400.0)))
    vdir = lab.p("verify", ver)
    os.makedirs(vdir, exist_ok=True)
    for old in os.listdir(vdir):
        if old.startswith("readback_p"):
            os.remove(os.path.join(vdir, old))
    files = []
    # page 1 also reads the clip comps (tool names, key counts, values at the fx frames) and the catalogue
    # transitions (name, length, rebuilt tools, macro inputs); Text+ animations are read on the graphics timeline
    fxv = [dict((k, f[k]) for k in ("id", "track", "media_fps", "speed", "still", "framing", "samples", "speed_warp"))
           for f in fx_item_specs(edl)]
    xfv = [{"id": x.get("id"), "build": (x.get("resolve") or {}).get("build"),
            "macro_inputs": (x.get("resolve") or {}).get("macro_inputs") or {}}
           for x in edl.get("transitions", []) if (x.get("resolve") or {}).get("type")]
    for p in range(pages):
        cfg = {"map": posix(lab.p("build", "%s.map.json" % ver)), "start": p * 400, "count": 400,
               "out": posix(os.path.join(vdir, "readback_p%02d.json" % (p + 1)))}
        if p == 0 and (fxv or xfv):
            cfg.update(fx=fxv, transitions_fx=xfv, fps=float(tl_fps(edl)))
        text = (snippet_head(lab, "verify %s page %d of %d" % (ver, p + 1, pages),
                             "Read only: reads the built timeline back into LAB/verify/%s/." % ver, ver)
                + SNIPPET_HEADER + COMMON_BODY + fusion_recipes_text() + VERIFY_BODY + cfg_literal(cfg)
                + "result = _re_verify(resolve, project, CFG)\n")
        files.append(write_snippet(lab, "verify_%s_%02d.py" % (ver, p + 1), text))
    print("then: edit_lab.py LAB verify %s" % posix(edl_path))
    return files


VERIFY_LEVELS = {"missing": "STOP", "extra": "STOP", "moved": "STOP", "src_off": "STOP", "length_off": "STOP",
                 "speed_off": "STOP", "fade_off": "WARN", "volume_off": "WARN", "transition_missing": "WARN",
                 "marker_missing": "WARN", "pixels_unchecked": "WARN", "v1_gap": "STOP", "settings_off": "STOP",
                 "text_missing": "STOP", "text_off": "STOP", "text_marker_only": "WARN", "pixels_off": "STOP",
                 "audio_dropout": "STOP", "transition_off": "STOP", "track_name_off": "WARN", "item_label_off": "WARN",
                 "handoff_missing": "WARN"}


def verify_edl(edl, lab, pages, mp):
    rows, markers, total, seen = [], [], 0, 0
    problems = []
    for pg in pages:
        rows += pg.get("items", [])
        markers = markers or pg.get("markers", [])
        total = max(total, int(pg.get("total", 0)))
        seen = max(seen, int(pg.get("start", 0)) + int(pg.get("count", 0)))
    out = []

    def add(cid, item, frames, msg):
        out.append({"id": cid, "level": VERIFY_LEVELS[cid], "at_frames": frames, "items": [item] if item else [], "msg": msg,
                    "fix": {"missing": "rebuild, or place the item by hand", "extra": "delete the stray item",
                            "moved": "move the item", "src_off": "slip the source", "length_off": "trim the item",
                            "speed_off": "set the clip speed", "fade_off": "set the fades",
                            "volume_off": "set the clip volume", "transition_missing": "add the transition by hand",
                            "transition_off": "delete the transition and add it again from the other clip (Add "
                                              "Transition at the next clip's start), or rebuild with this version",
                            "marker_missing": "add the marker", "pixels_unchecked": "look at the timeline once",
                            "v1_gap": "extend the item before the gap by the missing frames",
                            "settings_off": "set the size and input sizing in Timeline > Timeline Settings (Use custom "
                                            "settings), or rebuild",
                            "text_missing": "run the build's titles and captions snippet again, or rebuild",
                            "text_off": "open the graphics timeline, fix the Text+ or its range, or rebuild",
                            "text_marker_only": "make the Text+ at the Cream marker by hand (Effects > Titles > Text+)",
                            "pixels_off": "look at the listed frames in Resolve and in the preview; rebuild when they "
                                          "differ",
                            "track_name_off": "rename the track in the timeline's track header (or run the build's "
                                              "finish snippet again: it names every track)",
                            "item_label_off": "run the build's finish snippet again (it names and colours the items), "
                                              "or set the name and clip colour by hand",
                            "handoff_missing": "run the build's finish snippet again: it adds the hand-off marker and "
                                               "the note in the timeline's Comments"}[cid]})
    if total > seen:
        problems.append("readback covers %d of %d items: run verify-script again and all its pages" % (seen, total))
    tail = set((mp or {}).get("tail_trimmed") or [])
    refused = set((mp or {}).get("refused") or [])
    ducked = duck_pieces_of(edl)
    mix_g = float((edl.get("mix") or {}).get("gain_db") or 0.0)      # the EDL mix gain rides on every audio item
    g = (mp or {}).get("gfx") or {}
    gfx_rows = [r for r in rows if r.get("gfx") or (g.get("sheet_mpi_uid") and r.get("mpi_uid") == g["sheet_mpi_uid"])]
    rows = [r for r in rows if not any(r is x for x in gfx_rows)]
    exp = []
    for kind in ("video", "audio"):
        for tr in edl["tracks"][kind]:
            for it0 in tr.get("items", []):
                if not is_media_item(it0):
                    continue
                if edl["media"][it0["media"]].get("kind") == "image":
                    # a still: its record range and length (a still's source start reads 1920 in Resolve)
                    exp.append({"id": it0["id"], "mid": it0["media"], "kind": kind, "track": track_num(tr.get("id")),
                                "rec_in": int(it0["rec_in"]), "rec_out": int(it0["rec_out"]),
                                "path": canon_path(edl["media"][it0["media"]].get("path")), "src_in": None,
                                "speed": None, "fades": [int(it0.get("fade_in", 0)), int(it0.get("fade_out", 0))],
                                "gain": 0.0, "env": False, "tol": 0, "still": True})
                    continue
                if it0["id"] in ducked:
                    pcs = ducked[it0["id"]][2]
                    mf_ = media_fps(edl, it0["media"])
                    its_ = [piece_item(it0, pc, k, len(pcs), mf_, tl_fps(edl)) for k, pc in enumerate(pcs)]
                else:
                    its_ = [it0]
                for it in its_:
                    m = edl["media"][it["media"]]
                    exp.append({"id": it["id"], "mid": it["media"], "kind": kind, "track": track_num(tr.get("id")),
                                "rec_in": int(it["rec_in"]), "rec_out": int(it["rec_out"]), "path": canon_path(m.get("path")),
                                "src_in": int(it["src_in"]), "speed": speed_of(it) * 100.0,
                                "fades": [int(it.get("fade_in", 0)), int(it.get("fade_out", 0))],
                                "gain": float(it.get("gain_db", 0) or 0) + mix_g, "env": bool(it.get("volume_env")),
                                "tol": 1 if (it["id"] in tail or speed_of(it) != 1.0) else 0})
    act = [dict(r, path=canon_path(r.get("path"))) for r in rows if r.get("type") not in ("transition",)]
    trans = [r for r in rows if r.get("type") == "transition"]
    handle_in = load_semantics().get("source_time_includes_transition_handle") is True

    def head_handle(r):
        """Timeline frames of the transition that covers this item's start (0 when none). A transition that ends
        on the cut counts too: Resolve squeezes a crossfade between two very short pieces (a 2 frame duck step) to
        end there, and the incoming piece's source start still moves back by its length (measured in 21.1). A
        transition that ends on the cut but belongs to an earlier cut (a shot shorter than half the dissolve before
        it, then a straight cut) does not: some other item starts strictly inside that transition."""
        if r.get("rec_in") is None:
            return 0
        mine = [t for t in trans if t.get("kind") == r.get("kind") and t.get("track") == r.get("track")
                and t.get("rec_in") is not None and t.get("rec_out") is not None]
        for t in mine:
            if t["rec_in"] < r["rec_in"] < t["rec_out"]:
                return int(r["rec_in"]) - int(t["rec_in"])
        for t in mine:
            if t["rec_in"] < r["rec_in"] == t["rec_out"]:
                inside = [x for x in act if x is not r and x.get("kind") == r.get("kind")
                          and x.get("track") == r.get("track") and x.get("rec_in") is not None
                          and t["rec_in"] < x["rec_in"] < t["rec_out"]]
                if not inside:
                    return int(r["rec_in"]) - int(t["rec_in"])
        return 0
    free = list(range(len(act)))
    key = {}
    for i, r in enumerate(act):
        key.setdefault((r["kind"], r["track"], r.get("rec_in")), []).append(i)
    missing_clip = []
    matched = []                    # (EDL or piece id, readback row) for the item label check

    def compare(e, r, moved=None):
        matched.append((e["id"], r))
        if handle_in and r.get("src_in") is not None and head_handle(r):
            m = edl["media"].get(e.get("mid")) or {}
            mf = float(tl_fps(edl)) if m.get("fps_from") == "timeline" else float(parse_fps(m.get("fps") or tl_fps(edl)))
            sp = float(r.get("speed") or 100.0) / 100.0
            r = dict(r, src_in=int(r["src_in"]) + int(round(head_handle(r) * sp * mf / float(tl_fps(edl)))))
        if moved:
            add("moved", e["id"], [e["rec_in"]], "%s is at %d instead of %d (%+d f)" % (e["id"], r["rec_in"], e["rec_in"], moved))
        if r.get("rec_out") is not None and r.get("rec_in") is not None:
            dl = (r["rec_out"] - r["rec_in"]) - (e["rec_out"] - e["rec_in"])
            if abs(dl) > e["tol"]:
                add("length_off", e["id"], [e["rec_in"]], "%s is %d f long instead of %d" % (
                    e["id"], r["rec_out"] - r["rec_in"], e["rec_out"] - e["rec_in"]))
        if r.get("src_in") is not None and e["src_in"] is not None and int(r["src_in"]) != e["src_in"]:
            add("src_off", e["id"], [e["rec_in"]], "%s starts at source frame %d instead of %d (%+d)" % (
                e["id"], r["src_in"], e["src_in"], int(r["src_in"]) - e["src_in"]))
        if r.get("speed") is not None and e["speed"] is not None and abs(float(r["speed"]) - e["speed"]) > 0.01:
            add("speed_off", e["id"], [e["rec_in"]], "%s runs at %g %% instead of %g %%" % (e["id"], float(r["speed"]), e["speed"]))
        fd = r.get("fades")
        if fd is not None and [int(round(float(x or 0))) for x in fd] != e["fades"]:
            add("fade_off", e["id"], [e["rec_in"]], "%s fades %s instead of %s" % (e["id"], [int(round(float(x or 0))) for x in fd], e["fades"]))
        if e["kind"] == "audio":
            v = r.get("volume")
            if v is not None and abs(float(v) - e["gain"]) > 0.05:
                add("volume_off", e["id"], [e["rec_in"]], "%s volume %+g dB instead of %+g dB" % (e["id"], float(v), e["gain"]))
            if e["env"]:
                add("volume_off", e["id"], [e["rec_in"]], "%s: the volume envelope of a retimed item is not in Resolve "
                    "(static gain only); import the stem from `stem` if it matters" % e["id"])
    pending = []
    for e in exp:
        cands = [i for i in key.get((e["kind"], e["track"], e["rec_in"]), []) if i in free and act[i]["path"] == e["path"]]
        if cands:
            free.remove(cands[0])
            compare(e, act[cands[0]])
        else:
            pending.append(e)
    for e in pending:
        near = [i for i in free if act[i]["kind"] == e["kind"] and act[i]["track"] == e["track"]
                and act[i]["path"] == e["path"] and act[i].get("rec_in") is not None and abs(act[i]["rec_in"] - e["rec_in"]) <= 50]
        if near:
            i = min(near, key=lambda j: (abs(act[j]["rec_in"] - e["rec_in"]),
                                         abs((act[j].get("src_in") or 0) - (e["src_in"] or 0))))
            free.remove(i)
            compare(e, act[i], act[i]["rec_in"] - e["rec_in"])
        else:
            add("missing", e["id"], [e["rec_in"]], "%s (%s%s %d at %d) is not on the timeline%s" % (
                e["id"], "still, " if e.get("still") else "", e["kind"], e["track"], e["rec_in"],
                " (Resolve refused it)" if e["id"] in refused else ""))
            if not e.get("still"):
                missing_clip.append(e["id"])
    for i in free:
        r = act[i]
        add("extra", None, [r.get("rec_in") or 0], "unexpected %s item %r on %s %d at %s" % (
            r.get("type"), r.get("name"), r["kind"], r["track"], r.get("rec_in")))
    by_id = {it["id"]: (kind, tr, it) for kind, tr, it in iter_items(edl, ("video", "audio"))}
    for x in edl.get("transitions", []):
        k = by_id.get(x.get("from"))
        if not k:
            continue
        kind, tr, it = k
        c = int(it["rec_out"])
        hit = [t for t in trans if t["kind"] == kind and t["track"] == track_num(tr.get("id"))
               and t.get("rec_in") is not None and t["rec_in"] <= c <= t["rec_out"]]
        if not hit:
            add("transition_missing", x.get("id"), [c], "transition %s at %d is missing%s" % (
                x.get("id"), c, " (Resolve refused it: not enough handles)" if x.get("id") in ((mp or {}).get("transitions_refused") or []) else ""))
        else:
            n_, al_ = int(x.get("frames", 12)), x.get("alignment", "center")
            w_ = {"center": [c - n_ // 2, c + n_ - n_ // 2], "left": [c - n_, c], "right": [c, c + n_]}.get(al_)
            if w_ and int(hit[0]["rec_out"]) - int(hit[0]["rec_in"]) != n_:
                add("transition_off", x.get("id"), [c], "transition %s spans frames %d to %d (%d frames), the EDL asks "
                    "%d to %d (%d frames)" % (x.get("id"), hit[0]["rec_in"], hit[0]["rec_out"],
                                             int(hit[0]["rec_out"]) - int(hit[0]["rec_in"]), w_[0], w_[1], n_))
    for iid, (tr, it, pcs, xfs) in ducked.items():
        for x in xfs:
            c = int(it["rec_in"]) + int(x["at"])
            hit = [t for t in trans if t["kind"] == "audio" and t["track"] == track_num(tr.get("id"))
                   and t.get("rec_in") is not None and t["rec_in"] <= c <= t["rec_out"]]
            if not hit:
                add("transition_missing", "%s.x%d" % (iid, x["piece"] + 1), [c], "the ducking crossfade of %s at %d is "
                    "missing, so the level jumps there%s" % (iid, c, " (Resolve refused it)" if "%s.x%d" % (iid, x["piece"] + 1)
                                                               in ((mp or {}).get("transitions_refused") or []) else ""))
            else:
                n_ = int(x["frames"])
                w_ = [c - n_ // 2, c + n_ - n_ // 2]
                if [int(hit[0]["rec_in"]), int(hit[0]["rec_out"])] != w_:
                    add("transition_off", "%s.x%d" % (iid, x["piece"] + 1), [c], "the ducking crossfade of %s at %d "
                        "spans frames %d to %d, not %d to %d: the render fades the music out and cuts it back in there "
                        "(a dropout)" % (iid, c, hit[0]["rec_in"], hit[0]["rec_out"], w_[0], w_[1]))
    # every frame the EDL fills on V1 must have picture on V1 (a retimed item one frame short passes the length
    # tolerance above but leaves a black frame)
    v1_want = np.zeros(programme_frames(edl) + 1, bool)
    v1_still = np.zeros(len(v1_want), bool)
    for tr in edl["tracks"]["video"]:
        if track_num(tr.get("id")) == 1:
            for it in tr.get("items", []):
                if is_media_item(it):
                    # a tail the build trimmed on purpose (older semantics, reported as tail_trimmed) is not a gap
                    end = int(it["rec_out"]) - (1 if it["id"] in tail else 0)
                    v1_want[max(0, int(it["rec_in"])):max(0, end)] = True
                    if edl["media"][it["media"]].get("kind") == "image":
                        v1_still[max(0, int(it["rec_in"])):max(0, end)] = True
    v1_have = np.zeros(len(v1_want), bool)
    for r in act:
        if r["kind"] == "video" and r["track"] == 1 and r.get("rec_in") is not None and r.get("rec_out") is not None:
            v1_have[max(0, int(r["rec_in"])):max(0, min(len(v1_have), int(r["rec_out"])))] = True
    hole = v1_want & ~v1_have
    if missing_clip:
        # a missing clip already explains its hole; a still's hole is still named (the black end card of round 1)
        hole &= v1_still
    if hole.any():
        idx = np.flatnonzero(hole)
        runs = np.split(idx, np.flatnonzero(np.diff(idx) > 1) + 1)
        for rr in runs[:10]:
            add("v1_gap", None, [int(rr[0])], "V1 is empty for %d frame%s at %d to %d, where the EDL has %s" % (
                len(rr), "" if len(rr) == 1 else "s", int(rr[0]), int(rr[-1]),
                "a still" if v1_still[rr].all() else "picture"))
    # the timeline's raster and input sizing decide the framing of every clip
    sets = next((pg.get("settings") for pg in pages if pg.get("settings")), None)
    if sets:
        T = edl["timeline"]
        want_sz = (T.get("resolve") or {}).get("input_sizing")
        bad = []
        try:
            if (int(float(sets.get("width") or 0)), int(float(sets.get("height") or 0))) != (int(T["width"]), int(T["height"])):
                bad.append("%sx%s instead of %dx%d" % (sets.get("width"), sets.get("height"), int(T["width"]), int(T["height"])))
        except (TypeError, ValueError):
            pass
        if want_sz and sets.get("input_sizing") and sets["input_sizing"] != want_sz:
            bad.append("input sizing %s instead of %s (every clip of another shape is framed differently from the "
                       "preview)" % (sets["input_sizing"], want_sz))
        if bad:
            add("settings_off", None, [], "the timeline is %s" % "; ".join(bad))
    # titles and captions: Text+ ranges of the build's graphics timeline (or, built with --text markers or by an
    # older build, Cream markers)
    tmode = (mp or {}).get("text") or {"mode": "markers", "captions": "file"}
    fb = set(g.get("fallback_markers") or [])
    fb_frames = set()
    if tmode.get("mode") == "textplus":
        gp = gfx_plan(edl, lab, load_semantics(), captions=tmode.get("captions") or "burn", text="textplus")
        sheet = next((pg.get("gfx") for pg in pages if pg.get("gfx")), None) or {}
        slots = [x for x in sheet.get("items") or [] if x.get("start") is not None]

        def norm_t(s):
            return str(s if s is not None else "").replace("\r\n", "\n").replace("\r", "\n").strip()
        # which slot a range shows: its left offset minus the base most ranges share (the offset of a nested
        # timeline's first frame; 0 in the fake, not yet measured in Resolve)
        diffs = []
        for s in gp["items"]:
            d = (g.get("items") or {}).get(s["id"]) or {}
            for r in gfx_rows:
                if (r.get("track"), r.get("rec_in")) == (s["track"], s["rec_in"]) and r.get("left_offset") is not None \
                        and d.get("slot_start") is not None:
                    diffs.append(int(round(float(r["left_offset"]))) - int(d["slot_start"]))
        lo_base = max(set(diffs), key=lambda v: (diffs.count(v), -abs(v))) if diffs else 0
        for s in gp["items"]:
            d = (g.get("items") or {}).get(s["id"]) or {}
            what = "%s %s %r" % (s["kind"], s["id"], s["text"].replace("\n", " / "))
            if s["id"] in fb:
                fb_frames.add(int(s["rec_in"]))
                add("text_marker_only", s["id"], [s["rec_in"]], "%s is a Cream marker: Text+ could not be built" % what)
                continue
            cand = [r for r in gfx_rows if r.get("kind") == "video" and r.get("track") == s["track"]
                    and r.get("rec_in") == s["rec_in"]]
            if not cand:
                add("text_missing", s["id"], [s["rec_in"]], "%s is not on V%d at %d%s" % (
                    what, s["track"], s["rec_in"], " (Resolve refused it)" if s["id"] in (g.get("refused") or []) else ""))
                continue
            r = cand[0]
            gfx_rows = [x for x in gfx_rows if x is not r]
            matched.append((s["id"], r))
            n = (r["rec_out"] - r["rec_in"]) if r.get("rec_out") is not None else None
            if n is not None and n != s["rec_len"]:
                add("text_off", s["id"], [s["rec_in"]], "%s is %d f long instead of %d" % (what, n, s["rec_len"]))
            slot = d.get("slot_start")
            lo = r.get("left_offset")
            if slot is not None and lo is not None and int(round(float(lo))) - lo_base != int(slot):
                add("text_off", s["id"], [s["rec_in"]], "%s shows frame %d of the graphics timeline instead of %d "
                    "(another title's Text+)" % (what, int(round(float(lo))) - lo_base, int(slot)))
                continue
            if slot is not None and slots:
                hit = [x for x in slots if int(x["start"]) <= int(slot) < int(x["start"]) + int(x.get("dur") or 0)]
                if hit and "color" in hit[0]:
                    matched.append((s["id"], dict(hit[0], sheet=True)))
                if not hit:
                    add("text_off", s["id"], [s["rec_in"]], "%s: the graphics timeline has no Text+ at frame %d"
                        % (what, int(slot)))
                elif norm_t(hit[0].get("text")) != norm_t(s["text"]):
                    add("text_off", s["id"], [s["rec_in"]], "%s reads %r on the graphics timeline" % (
                        what, norm_t(hit[0].get("text")).replace("\n", " / ")))
    for r in gfx_rows:
        add("extra", None, [r.get("rec_in") or 0], "unexpected Text+ range %r on %s %d at %s" % (
            r.get("name"), r["kind"], r["track"], r.get("rec_in")))
    # the hand-off marker is the build's own, not one of the EDL's
    hand_mk = [m for m in markers if str(m.get("custom") or "").endswith("|handoff")]
    have = {int(m["frame"]) for m in markers if not str(m.get("custom") or "").endswith("|handoff")}
    want = set(int(m["frame"]) for m in edl.get("markers", []))
    if tmode.get("mode") != "textplus":
        want |= set(int(it["rec_in"]) for tr in edl["tracks"]["video"] for it in tr.get("items", [])
                    if it.get("kind") == "title")
    want |= fb_frames
    for f in sorted(want - have):
        add("marker_missing", None, [f], "marker at frame %d is missing" % f)
    out += verify_handover(edl, lab, pages, mp, matched, hand_mk, tmode)
    add("pixels_unchecked", None, [], "structure verified item by item; pixels were not compared (run grab-script "
                                      "and verify --grabs)")
    return out, problems


def verify_handover(edl, lab, pages, mp, matched, hand_mk, tmode):
    """What a build of this version leaves for the next person, read back: track names (track_name_off), item names and
    clip colours (item_label_off), the hand-off marker and Comments (handoff_missing). All WARN; a build map from
    before these existed (no "labels") is not checked."""
    out = []
    if not mp or mp.get("labels") is None:
        return out
    fix = {"track_name_off": "rename the track in the timeline's track header (or run the build's finish snippet "
                             "again: it names every track)",
           "item_label_off": "run the build's finish snippet again (it names and colours the items), or set the name "
                             "and clip colour by hand",
           "handoff_missing": "run the build's finish snippet again: it adds the hand-off marker and the note in the "
                              "timeline's Comments"}

    def add(cid, item, frames, msg):
        out.append({"id": cid, "level": VERIFY_LEVELS[cid], "at_frames": frames, "items": [item] if item else [],
                    "msg": msg, "fix": fix[cid]})
    sem = load_semantics()
    tm = tmode or {}
    gp = gfx_plan(edl, lab, sem, captions=tm.get("captions") or "burn", text=tm.get("mode") or "markers")
    page0 = next((p for p in pages if int(p.get("start", 0)) == 0), pages[0] if pages else {})
    trk = page0.get("tracks")
    if isinstance(trk, dict):
        bad = []
        for kind, i, name in track_name_plan(edl, gp):
            got = (trk.get(kind) or [])
            g_ = got[i - 1] if i - 1 < len(got) else None
            if g_ != name:
                bad.append("%s %d is %r, not %r" % ("V" if kind == "video" else "A", i, g_, name))
        if bad:
            add("track_name_off", None, [], "track names differ from the EDL's: %s" % "; ".join(bad[:8]))
    units, _l, _n = build_plan(edl, lab, sem)
    want = label_plan(edl, units, gp)
    fxi = ((mp.get("fx") or {}).get("items") or {})
    for s in fx_item_specs(edl):
        if s.get("label") and (fxi.get(s["id"]) or {}).get("ok"):
            want[s["id"]] = dict(s["label"])
    bad = []
    for iid, r in matched:
        lb = want.get(iid)
        if not lb or "color" not in r:
            continue
        where = " on the graphics timeline" if r.get("sheet") else ""
        if lb.get("color") and (r.get("color") or "") != lb["color"]:
            bad.append((iid, "%s%s is coloured %r, not %s" % (iid, where, r.get("color") or "", lb["color"])))
        if lb.get("name") and r.get("name") is not None and r.get("name") != lb["name"]:
            bad.append((iid, "%s%s is named %r, not %r" % (iid, where, r.get("name"), lb["name"])))
    if bad:
        ids = []
        for iid, _m in bad:
            if iid not in ids:
                ids.append(iid)
        out.append({"id": "item_label_off", "level": VERIFY_LEVELS["item_label_off"], "at_frames": [], "items": ids[:20],
                    "msg": "%d item name%s or colour%s differ from the build plan: %s" % (
                        len(bad), "" if len(bad) == 1 else "s", "" if len(bad) == 1 else "s",
                        "; ".join(m for _i, m in bad[:6])), "fix": fix["item_label_off"]})
    if (mp.get("handoff") or {}).get("custom") is not None or mp.get("finished"):
        if not hand_mk:
            add("handoff_missing", None, [0], "the timeline has no hand-off marker (Blue, %r)" % HANDOFF_NAME)
        elif isinstance(trk, dict) and "comments" in trk and "resolve-editor" not in str(trk.get("comments") or ""):
            add("handoff_missing", None, [int(hand_mk[0]["frame"])], "the timeline's Comments lack the hand-off note")
    return out


# Effects, catalogue transitions and Text+ animations (fusion_recipes in the build): ids and levels
FX_VERIFY_LEVELS = {"fx_missing": "STOP", "fx_off": "STOP", "fx_timebase": "STOP", "retime_off": "STOP",
                    "transition_length_off": "STOP", "transition_type_off": "STOP", "text_anim_missing": "STOP",
                    "text_anim_off": "STOP", "text_anim_unchecked": "WARN", "aux_off": "STOP",
                    "fx_pixels_approx": "INFO"}
FX_VERIFY_FIX = {
    "fx_missing": "run the effects snippet again (it builds what is missing), or rebuild",
    "fx_off": "rebuild the item's effects (delete the clip's Fusion comp, run the effects snippet again)",
    "fx_timebase": "the clip comp is not timed as the keys assume (a transition next to it changed, or a retime): "
                   "run the finish snippet first, then the effects snippet; rebuild if it persists",
    "retime_off": "rebuild the item's retime (delete the clip's Fusion comp, run the effects snippet again); for Speed "
                  "Warp set Retime Process Optical Flow and Motion Estimation Speed Warp in the Inspector",
    "transition_length_off": "the handles were short: assemble again with this version (validate checks the measured "
                             "handle rule) or set the length by hand",
    "transition_type_off": "delete the transition and run the finish snippet again (it adds and rebuilds it)",
    "text_anim_missing": "run the titles and captions snippet again on a fresh build (delete the build map and the "
                         "graphics timeline first)",
    "text_anim_off": "look at the listed frames in the proof and the preview; rebuild when the text moves differently",
    "text_anim_unchecked": "look at the listed frames in the proof by eye; to make the title checkable (and readable), "
                           "give it a look with a stroke over 0.05 em or a box, or move it off the light part of the "
                           "picture (text_contrast already warns there)",
    "aux_off": "look at the listed frames in Resolve: the effect is missing or much weaker than in the preview",
    "fx_pixels_approx": "look at these spans once in Resolve: the preview only suggests them"}
FX_ZOOM_TOL, FX_PX_TOL, FX_ANGLE_TOL, FX_SRC_TOL, FX_LEVEL_TOL = 0.002, 0.5, 0.05, 0.01, 0.01


def _fx_chk(cid, item, frames, msg):
    return {"id": cid, "level": FX_VERIFY_LEVELS[cid], "at_frames": [int(f) for f in frames],
            "items": [item] if item else [], "msg": msg, "fix": FX_VERIFY_FIX[cid]}


def fx_expected_tools(spec):
    """The RE_ tools a clip comp must hold for this item's effects."""
    want = []
    if spec.get("retime"):
        want.append("RE_Retime")
    if spec.get("motion"):
        want.append("RE_Motion")
    acc = spec.get("accents") or {}
    if acc.get("flash"):
        want.append("RE_Flash")
    if acc.get("rgb_px"):
        want += ["RE_RGB_R", "RE_RGB_B", "RE_RGB_MixR", "RE_RGB_MixB"]
    if acc.get("leak"):
        want.append("RE_Leak")
    return want


def verify_fx(edl, lab, pages, mp):
    """The effects part of verify: clip comps (tools present, timebase, values at the fx frames against the EDL's
    keys), Speed Warp properties, catalogue transitions (name, placed length, rebuilt tools, macro inputs) and Text+
    animations (Follower, keys, fade Merge). Returns check dicts."""
    out = []
    page0 = next((p for p in pages if int(p.get("start", 0)) == 0), pages[0] if pages else {})
    specs = fx_item_specs(edl)
    fxr = page0.get("fx")
    mfx = (mp or {}).get("fx") or {}
    refused = {r.get("id"): r for r in mfx.get("refused") or []}
    by_id = {it["id"]: it for kind, tr, it in iter_items(edl, ("video",))}
    if specs and fxr is None:
        out.append(_fx_chk("fx_missing", None, [], "the readback holds no clip comps although %d item%s carry effects: "
                           "run the effects snippet, then verify-script and its RUN lines again"
                           % (len(specs), "" if len(specs) == 1 else "s")))
        fxr = {}
    for spec in specs:
        sid = spec["id"]
        it = by_id.get(sid) or {}
        r0 = int(spec["rec_in"])
        r = (fxr or {}).get(sid) or {}
        if sid in refused:
            why = refused[sid].get("why")
            out.append(_fx_chk("fx_timebase" if why == "fx_timebase" else "fx_missing", sid, [r0],
                               "%s: the effects snippet refused it (%s%s)" % (
                                   sid, why, ": %s" % refused[sid]["detail"] if refused[sid].get("detail") else "")))
            continue
        if not r.get("found"):
            out.append(_fx_chk("fx_missing", sid, [r0], "%s: the item carrying the effects was not found" % sid))
            continue
        if spec.get("speed_warp"):
            rp, me = r.get("retime_process"), r.get("motion_estimation")
            if not (isinstance(rp, (int, float)) and int(rp) == 3 and isinstance(me, (int, float)) and int(me) == 5):
                out.append(_fx_chk("retime_off", sid, [r0], "%s: Speed Warp reads Retime Process %r and Motion "
                                   "Estimation %r (3 and 5 wanted)" % (sid, rp, me)))
        want = fx_expected_tools(spec)
        if not want:
            continue
        tb = r.get("timebase") or {}
        if tb and not tb.get("ok"):
            out.append(_fx_chk("fx_timebase", sid, [r0], "%s: the clip comp spans %s source frames where the visible "
                               "range asks %s" % (sid, tb.get("got_span"), tb.get("want_span"))))
            continue
        miss = [t for t in want if t not in (r.get("tools") or [])]
        if miss:
            out.append(_fx_chk("fx_missing", sid, [r0], "%s: the clip comp lacks %s" % (sid, ", ".join(miss))))
            continue
        mk = (spec.get("motion") or {}).get("keys") or {}
        acc = spec.get("accents") or {}
        rt = (spec.get("retime") or {}).get("keys") or []
        offs, roffs = [], []
        for f_, row in sorted(((int(k), v) for k, v in (r.get("samples") or {}).items())):
            checks_ = []
            if mk.get("zoom") and row.get("zoom") is not None:
                checks_.append(("zoom", fx_lab.value_at(mk["zoom"], f_), row["zoom"], FX_ZOOM_TOL))
            if (mk.get("x_px") or mk.get("y_px")) and row.get("x_px") is not None:
                checks_.append(("x_px", fx_lab.value_at(mk.get("x_px") or [], f_, 0.0) or 0.0, row["x_px"], FX_PX_TOL))
                checks_.append(("y_px", fx_lab.value_at(mk.get("y_px") or [], f_, 0.0) or 0.0, row["y_px"], FX_PX_TOL))
            if mk.get("angle") and row.get("angle") is not None:
                checks_.append(("angle", fx_lab.value_at(mk["angle"], f_), row["angle"], FX_ANGLE_TOL))
            if acc.get("flash") and row.get("flash") is not None:
                checks_.append(("flash", fx_lab.value_at(acc["flash"], f_), row["flash"], FX_LEVEL_TOL))
            if acc.get("rgb_px") and row.get("rgb_px") is not None:
                checks_.append(("rgb_px", fx_lab.value_at(acc["rgb_px"], f_), row["rgb_px"], FX_PX_TOL))
            for nm, w_, g_, tol in checks_:
                if w_ is None or g_ is None:
                    continue
                if abs(float(g_) - float(w_)) > tol:
                    offs.append("frame %d %s %.4g instead of %.4g" % (f_, nm, float(g_), float(w_)))
            if rt and row.get("src") is not None:
                w_ = fx_lab.value_at(rt, f_)
                if w_ is not None and abs(float(row["src"]) - float(w_)) > FX_SRC_TOL:
                    roffs.append("frame %d shows source frame %.3f instead of %.3f" % (f_, float(row["src"]), float(w_)))
            elif rt and "src" in row and row.get("src") is None:
                roffs.append("frame %d: the retime reads no source time" % f_)
        if offs:
            out.append(_fx_chk("fx_off", sid, [r0], "%s: %s" % (sid, "; ".join(offs[:6]))))
        if roffs:
            out.append(_fx_chk("retime_off", sid, [r0], "%s: %s" % (sid, "; ".join(roffs[:6]))))
    # catalogue transitions
    xfx = page0.get("transitions_fx") or {}
    for x in edl.get("transitions", []):
        rv = x.get("resolve") or {}
        if not rv.get("type"):
            continue
        r = xfx.get(x.get("id")) or {}
        c = int((by_id.get(x.get("from")) or {}).get("rec_out", 0) or 0)
        if not r.get("found"):
            continue                    # verify_edl reports it (transition_missing)
        if r.get("name") != rv["type"]:
            out.append(_fx_chk("transition_type_off", x.get("id"), [c], "transition %s is %r, the EDL asks %r" % (
                x.get("id"), r.get("name"), rv["type"])))
        if r.get("dur") is not None and int(r["dur"]) != int(x.get("frames", 0) or 0):
            out.append(_fx_chk("transition_length_off", x.get("id"), [c], "transition %s is %s frames long, the EDL "
                               "asks %d (Resolve shortens a transition when the handles are short)" % (
                                   x.get("id"), r["dur"], int(x.get("frames", 0) or 0))))
        if rv.get("build") in ("rebuild_whip", "rebuild_zoom"):
            miss = [t for t in ("RE_XA", "RE_XB", "RE_XMix") if t not in (r.get("tools") or [])]
            if miss or r.get("groups"):
                out.append(_fx_chk("transition_type_off", x.get("id"), [c], "transition %s was not rebuilt as %s "
                                   "(%s)" % (x.get("id"), x.get("type"), "lacks " + ", ".join(miss) if miss else
                                             "the macro is still there")))
        for k, v in sorted((rv.get("macro_inputs") or {}).items()):
            got = (r.get("macro") or {}).get(k)
            if not isinstance(got, (int, float)) or abs(float(got) - float(v)) > 1e-6:
                out.append(_fx_chk("transition_type_off", x.get("id"), [c], "transition %s: %s reads %r instead of %r"
                                   % (x.get("id"), k, got, v)))
    # Text+ animations
    tmode = (mp or {}).get("text") or {}
    g = (mp or {}).get("gfx") or {}
    if tmode.get("mode") == "textplus":
        gp = gfx_plan(edl, lab, load_semantics(), captions=tmode.get("captions") or "burn", text="textplus")
        sheet = next((p_.get("gfx") for p_ in pages if p_.get("gfx")), None) or {}
        rows = [x for x in sheet.get("items") or [] if x.get("start") is not None]
        for s_ in gp["items"]:
            plan = s_.get("plan")
            if not plan:
                continue
            d = (g.get("items") or {}).get(s_["id"]) or {}
            if s_["id"] in (g.get("anim_failed") or []):
                out.append(_fx_chk("text_anim_missing", s_["id"], [s_["rec_in"]], "%s: the build could not animate it"
                                   % s_["id"]))
                continue
            if d.get("slot_start") is None:
                continue                # text_missing (verify_edl) covers it
            hit = [x for x in rows if int(x["start"]) == int(d["slot_start"])]
            an = (hit[0].get("anim") if hit else None)
            if not isinstance(an, dict) or an.get("error"):
                out.append(_fx_chk("text_anim_missing", s_["id"], [s_["rec_in"]], "%s: the animation was not read back "
                                   "(%s)" % (s_["id"], (an or {}).get("error") or "no readback")))
                continue
            lack = []
            if plan.get("follower") and not an.get("follower"):
                lack.append("the Follower")
            for k in (plan.get("keys") or {}):
                if not (an.get("keys") or {}).get(k):
                    lack.append("%s keys" % k)
            if plan.get("center_keys") and not (an.get("keys") or {}).get("Center.Y"):
                lack.append("the slide (Center keys)")
            if plan.get("blend_keys") and not an.get("merge"):
                lack.append("the fade (Merge)")
            if lack:
                out.append(_fx_chk("text_anim_missing", s_["id"], [s_["rec_in"]], "%s (%s) lacks %s" % (
                    s_["id"], plan.get("anim"), ", ".join(lack))))
    return out


def verify_fx_pixels(lab, edl_path, edl, vdir, pix, proof=None):
    """After the pixel compare: the approximate spans that were not correlated (fx_pixels_approx, INFO), the aux
    statistics where correlation is blind (aux_off: a flash or dip level, an RGB split offset, against the same
    statistic in the preview, both relative to a frame of the same shot outside the effect, [PRV] s10), and animated
    text on a proof (text_anim_off, fx_lab.text_compare)."""
    out = []
    try:
        plan = fx_lab.pixel_plan(edl)
    except Exception:
        plan = []
    skipped = [r_ for r_ in (pix or {}).get("rows", []) if r_.get("skipped")]
    if skipped:
        spans = sorted(set(r_.get("rule_fx") for r_ in skipped if r_.get("rule_fx")))
        out.append(_fx_chk("fx_pixels_approx", None, [r_["frame"] for r_ in skipped[:8]],
                           "%d frame%s inside approximate spans were not pixel-checked (%s)" % (
                               len(skipped), "" if len(skipped) == 1 else "s", ", ".join(str(s_) for s_ in spans[:8]))))
    aux_spans = [s_ for s_ in plan if s_.get("aux") and s_.get("aux") in ("rb_offset_px", "relative_level",
                                                                          "relative_level for dips")]
    rdir = review_dir(edl_path)
    prev_path = os.path.join(rdir, "preview.mov")
    if not edl_has_fx(edl) or not os.path.exists(prev_path):
        return out
    pw, ph = _video_size(prev_path)
    if proof:
        out += _text_anim_compare(lab, edl, prev_path, proof, pw, ph)
    if not aux_spans:
        return out
    W = float(edl["timeline"]["width"])
    have = {}
    gdir = os.path.join(vdir, "grabs")
    gdoc = load_json_or(os.path.join(gdir, "grabs.json"), None) or {}
    for r_ in gdoc.get("frames") or []:
        p_ = os.path.join(gdir, r_.get("file") or "")
        if r_.get("ok") and os.path.isfile(p_):
            have[int(r_["frame"])] = ("grab", p_)
    want = set()
    for s_ in aux_spans:
        mid = (int(s_["f0"]) + int(s_["f1"]) - 1) // 2
        want.update([mid, int(s_["f0"]) - 1])
    want = sorted(f for f in want if f >= 0)
    prev = _frames_rgb(prev_path, want, pw, ph)
    rend = _frames_rgb(proof, want, pw, ph) if proof else {}
    for f in want:
        if f not in rend and f in have:
            try:
                from PIL import Image
                with Image.open(have[f][1]) as im:
                    rend[f] = np.asarray(im.convert("RGB").resize((pw, ph), Image.BILINEAR))
            except (OSError, ValueError):
                pass
    for s_ in aux_spans:
        mid, ref = (int(s_["f0"]) + int(s_["f1"]) - 1) // 2, int(s_["f0"]) - 1
        if mid not in rend or mid not in prev:
            continue
        a_r, a_p = fx_lab.aux_stats(rend[mid]), fx_lab.aux_stats(prev[mid])
        if s_["aux"] == "rb_offset_px":
            if abs(a_p["rb_offset_px"]) >= 2 and abs(a_r["rb_offset_px"] - a_p["rb_offset_px"]) > 1:
                out.append(_fx_chk("aux_off", s_.get("fx"), [mid], "%s: the red and blue channels are %d px apart in "
                                   "the build and %d px in the preview (frame %d)" % (
                                       s_.get("fx"), a_r["rb_offset_px"], a_p["rb_offset_px"], mid)))
        elif ref in rend and ref in prev:
            b_r, b_p = fx_lab.aux_stats(rend[ref]), fx_lab.aux_stats(prev[ref])
            dp, dr = a_p["mean"] - b_p["mean"], a_r["mean"] - b_r["mean"]
            if abs(dp) >= 20 and (dr * dp <= 0 or abs(dr) < 0.3 * abs(dp)):
                out.append(_fx_chk("aux_off", s_.get("fx"), [mid], "%s: the level moves %+.0f in the preview but %+.0f "
                                   "in the build (frame %d against %d)" % (s_.get("fx"), dp, dr, mid, ref)))
    return out


def _video_size(path):
    r = run([ffprobe_bin(), "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
             "-of", "csv=p=0", path], "ffprobe (size)")
    # the first line: a Resolve H.264 render lists the stream twice ("540,960", a blank line, "540,960")
    line = [x for x in r.stdout.decode().splitlines() if x.strip()][0]
    w, h = [int(v) for v in line.strip().split(",")[:2]]
    return w, h


def _frames_rgb(path, frames, w, h):
    """{frame: HxWx3 uint8} of the listed frames of a video, scaled to w x h (frames past the end are left out)."""
    frames = sorted(set(int(f) for f in frames if int(f) >= 0))
    if not frames or not path or not os.path.exists(path):
        return {}
    runs, a = [], frames[0]
    for x, y in zip(frames, frames[1:] + [None]):
        if y != x + 1:
            runs.append((a, x))
            a = y
    sel = "+".join(("between(n\\,%d\\,%d)" % (a_, b_)) if b_ > a_ else ("eq(n\\,%d)" % a_) for a_, b_ in runs)
    r = run([ffmpeg_bin(), "-nostdin", "-v", "error", "-i", path, "-vf", "select='%s',scale=%d:%d:flags=area" % (sel, w, h),
             "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], "ffmpeg (frames)")
    a = np.frombuffer(r.stdout, np.uint8)
    n = a.size // (w * h * 3)
    a = a[:n * w * h * 3].reshape(n, h, w, 3)
    return {f: a[i] for i, f in enumerate(frames[:n])}


def _text_compare_roi(it, st, box, W, H, pw, ph):
    """The region (x, y, w, h, compare pixels) where the proof's text is judged against the preview's. For a look
    with a dark box it is the drawn box (fx_lab.animated_ink_box) inset by half its pad, so it holds only the box and
    its glyphs: the EDL box is the estimated text block, taller than the drawn pill (line pitch against the glyph
    height) and seldom its width, and a light thing of the picture right outside the pill (a ring light under a
    caption) passed the fill test next to the dark box edge in the preview and not in the sharper render (integration,
    final fix: the t3 proof STOPped text_anim_off with a 12 px taller preview bbox on two 84 px caption_clean_box
    cues). Other looks keep the box."""
    sx, sy = pw / float(W), ph / float(H)
    roi = [float(box[0]) * sx, float(box[1]) * sy, float(box[2]) * sx, float(box[3]) * sy]
    lk = it.get("look") or {}
    bxl = lk.get("box")
    c = bxl.get("color") if isinstance(bxl, dict) else None
    if not (isinstance(c, str) and re.fullmatch(r"#?[0-9A-Fa-f]{6}", c.strip())):
        return roi
    r_, g_, b_ = _hex_rgb("#" + c.strip().lstrip("#"), (255, 255, 255))
    if 0.2126 * r_ + 0.7152 * g_ + 0.0722 * b_ > 100 or not st:
        return roi
    try:
        ink = fx_lab.animated_ink_box(it, st, W, H)
    except Exception:
        ink = None
    if not ink:
        return roi
    fpx = float(it.get("font_px") or 64)
    px_, py_ = look_pad(lk)
    ix, iy = 0.5 * px_ * fpx, 0.5 * py_ * fpx
    if ink[2] <= 2 * ix + 4 or ink[3] <= 2 * iy + 4:
        return roi
    return [(ink[0] + ix) * sx, (ink[1] + iy) * sy, (ink[2] - 2 * ix) * sx, (ink[3] - 2 * iy) * sy]


def _hex_luma(c):
    """The luma of a #RRGGBB colour, or None."""
    if not (isinstance(c, str) and re.fullmatch(r"#?[0-9A-Fa-f]{6}", c.strip())):
        return None
    r_, g_, b_ = _hex_rgb("#" + c.strip().lstrip("#"), (255, 255, 255))
    return 0.2126 * r_ + 0.7152 * g_ + 0.0722 * b_


def _text_compare_specs(it, st, box, W, H, pw, ph):
    """The compares one animated text needs on the proof: [(what, roi, fill, edge)]. Every look gets the light-text
    compare (pixels near white, next to its dark outline, shadow or box). A look with DARK text on a LIGHT box (the
    cta_card pill: #111111 on white) gets a second compare whose mask is its own glyphs (pixels near the text colour)
    inside the pill's text area: the light-text mask there is only the pill, so a call to action that is missing,
    white or the wrong colour would match the preview (review RS1)."""
    lk = it.get("look") or {}
    out = [("text", _text_compare_roi(it, st, box, W, H, pw, ph), (255, 255, 255), fx_lab.outline_level(lk), None)]
    fl_ = _hex_luma(lk.get("color"))
    bxl = lk.get("box") if isinstance(lk.get("box"), dict) else None
    bl_ = _hex_luma(bxl.get("color")) if bxl else None
    if fl_ is None or bl_ is None or fl_ > 100 or bl_ <= 100:
        return out
    sx, sy = pw / float(W), ph / float(H)
    try:
        ink = fx_lab.animated_ink_box(it, st, W, H) if st else None
    except Exception:
        ink = None
    ink = ink or box
    fpx = float(it.get("font_px") or 64)
    px_, py_ = look_pad(lk)
    ix, iy = px_ * fpx, py_ * fpx
    if ink[2] > 2 * ix + 8 and ink[3] > 2 * iy + 8:
        roi = [(ink[0] + ix) * sx, (ink[1] + iy) * sy, (ink[2] - 2 * ix) * sx, (ink[3] - 2 * iy) * sy]
    else:
        roi = [float(box[0]) * sx, float(box[1]) * sy, float(box[2]) * sx, float(box[3]) * sy]
    # the pill compare above judges the position (text and pill move together); this one judges that the glyphs
    # are there in their colour, so its box tolerance follows the text block: a two-line cta_card in Text+ sits its
    # lines about 8 % of the block tighter than the preview's per-line pills (sandbox, review fix: 6 px of 65 at the
    # 360 px preview raster)
    lim = {"bbox_px": max(8, int(round(0.15 * roi[3])))}
    out.append(("glyphs", roi, tuple(_hex_rgb("#" + lk["color"].strip().lstrip("#"), (17, 17, 17))[:3]), None, lim))
    return out


TEXT_BG_BLIND = 0.03      # text_anim_unchecked: share of the background around light text that passes its mask
                          # (measured: 3 % of a grainy light wall in the mask already let a missing title pass)


def _text_bg_blind(it, st, f0, common, pf, spec, W, H, pw, alphas, fps):
    """Frames (timeline numbers) where the light-text compare is blind: in the preview, more than TEXT_BG_BLIND of
    the region's background (the pixels the drawn title leaves untouched, 2 px clear of it) passes the same mask as
    the glyphs (a light wall, sky or sleeve behind a shadow-only title). Judged on up to 6 solid frames; returns []
    unless most of them are blind (review RS7)."""
    what, roi, fill, edge = spec[:4]
    if not st:
        return []
    solid = [k for k, f in enumerate(common) if (alphas is None or alphas[k] >= 0.9) and 0 <= f - f0 < len(st)]
    if not solid:
        return []
    pick = solid[::max(1, len(solid) // 6)][:6]
    x, y, w, h = [int(round(v)) for v in roi]
    blind = []
    for k in pick:
        f = common[k]
        try:
            lay = np.asarray(fx_lab.draw_state(it, st[f - f0], W, H, scale=pw / float(W), fps=fps))[..., 3]
        except Exception:
            return []
        pa = np.asarray(pf[f])[max(0, y):y + h, max(0, x):x + w]
        la = lay[max(0, y):y + h, max(0, x):x + w]
        if pa.size == 0 or la.shape != pa.shape[:2]:
            continue
        bgm = ~fx_lab._dil(la > 8, 2)
        if bgm.sum() < 50:
            continue
        m, _, _ = fx_lab._masks(pa, None, fill, edge)
        if float((m & bgm).sum()) / float(bgm.sum()) > TEXT_BG_BLIND:
            blind.append(f)
    return blind if len(blind) * 2 > len(pick) else []


def text_compare_all(render, preview, specs, alphas):
    """fx_lab.text_compare for each compare of _text_compare_specs: STOP when any of them STOPs; the fails carry the
    compare's name when there are two."""
    res = {"result": "OK", "fails": [], "n_fail_frames": 0, "worst": {}}
    bad = set()
    for what, roi, fill, edge, lim in specs:
        try:
            r = fx_lab.text_compare(render, preview, [roi] * len(render), fill=fill, alphas=alphas, edge=edge,
                                    limits=lim)
        except Exception as ex:
            r = {"result": "STOP", "fails": [[0, "compare failed: %s" % ex]], "n_fail_frames": 1}
        res["worst"][what] = r.get("worst") or {}
        if r.get("result") != "OK":
            res["result"] = "STOP"
            for x in r.get("fails") or []:
                res["fails"].append([x[0]] + ([("%s:" % what)] if len(specs) > 1 else []) + list(x[1:]))
                bad.add(x[0])
    res["n_fail_frames"] = len(bad)
    res["fails"] = sorted(res["fails"], key=lambda x: x[0] if isinstance(x[0], int) else 0)[:20]
    return res


def _text_anim_compare(lab, edl, prev_path, proof, pw, ph):
    """Animated titles and captions on the proof against the preview (fx_lab.text_compare, [TXT] compare.py limits)."""
    out = []
    W, H = float(edl["timeline"]["width"]), float(edl["timeline"]["height"])
    fps = float(tl_fps(edl))
    items = [it for tr in edl["tracks"]["video"] for it in tr.get("items", []) if it.get("kind") == "title"]
    items += [c for tr in edl["tracks"].get("subtitle", []) for c in tr.get("items", [])]
    for it in items:
        if (it.get("anim") or {}).get("id") in (None, "none") or not it.get("box"):
            continue
        f0, f1 = int(it["rec_in"]), int(it["rec_out"])
        frames = list(range(f0, f1))
        st = []
        try:
            st = fx_lab.text_plan(it, W, H, fps).get("states") or []
            box = fx_lab.animated_box(it, st, W, H) if hasattr(fx_lab, "animated_box") else it["box"]
        except Exception:
            box = it["box"]
        specs = _text_compare_specs(it, st, box, W, H, pw, ph)
        rf, pf = _frames_rgb(proof, frames, pw, ph), _frames_rgb(prev_path, frames, pw, ph)
        common = [f for f in frames if f in rf and f in pf]
        if not common:
            continue
        alphas = [float(st[f - f0].get("alpha", 1.0)) if 0 <= f - f0 < len(st) else 1.0 for f in common] if st else None
        res = text_compare_all([rf[f] for f in common], [pf[f] for f in common], specs, alphas)
        # (a dark text on a light box is judged by its own glyphs: the light-text mask is only the pill there)
        blind = _text_bg_blind(it, st, f0, common, pf, specs[0], W, H, pw, alphas, fps) if len(specs) == 1 else []
        if blind and res.get("result") == "OK":
            out.append(_fx_chk("text_anim_unchecked", it.get("id"), blind[:6], "%s (%s): the picture behind it is "
                               "as light as its text on %d of the frames sampled, so the proof compare cannot tell the "
                               "glyphs from the background there; it passed, but it does not prove the text is right" % (
                                   it.get("id"), (it.get("anim") or {}).get("id"), len(blind))))
        if res.get("result") != "OK":
            fl = res.get("fails") or []
            out.append(_fx_chk("text_anim_off", it.get("id"), [common[int(x[0])] for x in fl[:6] if
                                                                isinstance(x[0], int) and x[0] < len(common)],
                               "%s (%s): the proof's text differs from the preview on %d frame%s: %s" % (
                                   it.get("id"), (it.get("anim") or {}).get("id"), res.get("n_fail_frames", len(fl)),
                                   "" if res.get("n_fail_frames", len(fl)) == 1 else "s",
                                   "; ".join(" ".join(str(v) for v in x[1:]) for x in fl[:3]))))
    return out


def cmd_verify(lab, edl_path, as_json=False, grabs=False, proof=None):
    """grabs: also compare LAB/verify/<ver>/grabs/ (grab-script) with the preview; proof: a rendered file."""
    edl = load_edl(edl_path)
    ver = edl_version(edl, edl_path)
    vdir = lab.p("verify", ver)
    pages = sorted(f for f in (os.listdir(vdir) if os.path.isdir(vdir) else []) if re.match(r"^readback_p\d+\.json$", f))
    if not pages:
        raise Fail("no readback in %s: run verify-script and its RUN lines first" % posix(vdir))
    pg = [load_json(os.path.join(vdir, p)) for p in pages]
    mp = load_json_or(lab.p("build", "%s.map.json" % ver), None)
    checks, problems = verify_edl(edl, lab, pg, mp)
    for p in problems:
        checks.insert(0, {"id": "missing", "level": "STOP", "at_frames": [], "items": [], "msg": p, "fix": "read back again"})
    fxc = verify_fx(edl, lab, pg, mp)
    # a catalogue transition's length is transition_length_off (the placed GetDuration); a clip-pair route places no
    # transition item, so verify_edl's transition_missing does not apply to it
    pair_ids = {x.get("id") for x in edl.get("transitions", []) if (x.get("resolve") or {}).get("build") == "clip_pair"}
    long_ids = {i for c in fxc if c["id"] == "transition_length_off" for i in c["items"]}
    checks = [c for c in checks if not (c["id"] == "transition_missing" and set(c["items"]) & pair_ids)
              and not (c["id"] == "transition_off" and set(c["items"]) & long_ids)]
    checks += fxc
    pix = None
    if grabs or proof:
        pix = verify_pixels(lab, edl_path, edl, vdir, grabs, proof)
        checks += verify_fx_pixels(lab, edl_path, edl, vdir, pix, proof)
    else:
        # a plain verify after --grabs or --proof keeps that pixel result while the EDL and the readback are unchanged
        old = load_json_or(os.path.join(vdir, "pixels.json"), None)
        if (old and old.get("compared") and old.get("edl_hash") == edl_hash(edl)
                and old.get("readback") == readback_stamp(vdir)):
            pix = dict(old, source=str(old.get("source")) + ", from the last pixel check")
    if proof:
        dips = verify_audio(lab, edl_path, edl, proof)
        if dips:
            f_ = float(tl_fps(edl))
            checks.append({"id": "audio_dropout", "level": VERIFY_LEVELS["audio_dropout"],
                           "at_frames": [int(d_[0] * f_) for d_ in dips[:8]], "items": [],
                           "msg": "the render's sound drops out where the preview's does not: %s" % "; ".join(
                               "%.3f s (frame %d) %.0f dB below the sound around it" % (d_[0], int(d_[0] * f_), d_[1])
                               for d_ in dips[:8]),
                           "fix": "listen there in Resolve; a crossfade of the music ducking that ends on an edit of "
                                  "another track does this: assemble again with this version of the skill, or move "
                                  "the music ramp one frame"})
        elif dips is None:
            checks.append({"id": "audio_dropout", "level": "WARN", "at_frames": [], "items": [],
                           "msg": "the proof's sound was not compared (no sound in the proof or no preview)",
                           "fix": "render the proof with sound"})
    if pix is not None and pix["compared"] > 0:
        checks = [c for c in checks if c["id"] != "pixels_unchecked"]
        if pix["bad"]:
            worst = sorted(pix["bad"], key=lambda r_: (r_["corr"] is not None, r_["corr"] or 0))[:8]
            checks.append({"id": "pixels_off", "level": VERIFY_LEVELS["pixels_off"],
                           "at_frames": [r_["frame"] for r_ in worst], "items": [],
                           "msg": "%d of %d compared frames (%s) differ from the preview, text boxes masked: %s" % (
                               len(pix["bad"]), pix["compared"], pix["source"], "; ".join(
                                   "frame %d %s" % (r_["frame"], r_["why"]) for r_ in worst)),
                           "fix": "look at the listed frames in Resolve and in the preview; rebuild when they differ"})
    # INFO checks (fx_pixels_approx) are reported but do not make the result a WARN
    res = checks_result([c for c in checks if c["id"] != "pixels_unchecked" and c["level"] != "INFO"] or [])
    if res == "OK" and any(c["id"] == "pixels_unchecked" for c in checks):
        res = "WARN"
    summary = {}
    for c in checks:
        summary[c["id"]] = summary.get(c["id"], 0) + 1
    doc = {"schema": "resolve-editor/checks@1", "kind": "verify", "edl": lab.relto(edl_path), "result": res,
           "timeline": pg[0].get("timeline"), "checks": checks, "summary": summary,
           "stats": {"items_read": sum(len(p.get("items", [])) for p in pg), "items_expected":
                     sum(1 for k, tr, it in iter_items(edl, ("video", "audio")) if is_media_item(it))}}
    if pix is not None:
        doc["stats"]["pixels"] = {"source": pix["source"], "compared": pix["compared"], "median_corr": pix["median_corr"],
                                  "off": len(pix["bad"])}
    fn = write_json(os.path.join(vdir, "verify.json"), doc)
    if as_json:
        print(json.dumps(doc, indent=1))
        return doc
    print_result(res, ["%s %s: %s" % (c["level"], c["id"], c["msg"]) for c in checks], [fn])
    sk = (pix or {}).get("skipped") if pix is not None and not str(pix.get("source", "")).endswith("pixel check") \
        else None
    if sk:
        print("NOTE %d grab%s not compared: %s; run grab-script and its RUN line again to check %s" % (
            len(sk.get("unreadable") or []) + int(sk.get("missing_or_failed") or 0),
            "" if len(sk.get("unreadable") or []) + int(sk.get("missing_or_failed") or 0) == 1 else "s",
            ", ".join((["%d missing or failed" % sk["missing_or_failed"]] if sk.get("missing_or_failed") else [])
                      + (["unreadable " + ", ".join(sk["unreadable"][:6])] if sk.get("unreadable") else [])),
            "those frames"))
    return doc


# ------------------------------------------------------------------------ titles and captions as Text+ (the build)
# The look the build falls back to when an EDL item carries none: the lab preview's look before looks existed
# (Arial Bold, white, a black outline; titles on a black box at 60 %).
FALLBACK_LOOKS = {
    "title": {"font": "Arial", "style": "Bold", "font_file": None, "case": "as_written", "color": "#FFFFFF",
              "stroke": {"color": "#000000", "em": 0.11}, "shadow": None,
              "box": {"color": "#000000", "opacity": 0.6, "pad_em": [0.25, 0.3], "round": 0.0},
              "line_spacing": 1.25, "align": "center"},
    "caption": {"font": "Arial", "style": "Bold", "font_file": None, "case": "as_written", "color": "#FFFFFF",
                "stroke": {"color": "#000000", "em": 0.11}, "shadow": None, "box": None,
                "line_spacing": 1.25, "align": "center"},
}


def resolved_look(it, kind):
    """The item's look (the EDL carries it resolved) over the fallback, field by field."""
    out = copy.deepcopy(FALLBACK_LOOKS["title" if kind == "title" else "caption"])
    lk = it.get("look")
    if isinstance(lk, dict):
        for k, v in lk.items():
            out[k] = copy.deepcopy(v)
    return out


def rgb01(c, default):
    """A look colour ("#RRGGBB", "#RGB" or [r, g, b]) as three 0 to 1 floats; anything else (an unresolved brand
    token) gives the default."""
    if isinstance(c, (list, tuple)) and len(c) >= 3:
        try:
            v = [float(x) for x in c[:3]]
        except (TypeError, ValueError):
            return default
        return tuple(round(x / 255.0 if max(v) > 1.0 else x, 4) for x in v)
    s = str(c or "").strip()
    if re.match(r"^#[0-9a-fA-F]{6}$", s):
        return tuple(round(int(s[i:i + 2], 16) / 255.0, 4) for i in (1, 3, 5))
    if re.match(r"^#[0-9a-fA-F]{3}$", s):
        return tuple(round(int(s[i] * 2, 16) / 255.0, 4) for i in (1, 2, 3))
    return default


def textplus_inputs(it, kind, W, H, sem):
    """Text+ Template inputs that draw an EDL title or caption as the preview does (resolve_semantics.json holds the
    measured factors). Returns (inputs, look, notes)."""
    lk = resolved_look(it, kind)
    notes = []
    lines = [str(x) for x in (it.get("lines") or [])] or [str(it.get("text", ""))]
    if lk.get("case") == "upper":
        lines = [x.upper() for x in lines]
    box = it.get("box") or [W * 0.1, H * 0.8, W * 0.8, H * 0.1]
    fpx = float(it.get("font_px") or (float(box[3]) / max(1, len(lines)) / 1.3))
    cx, cy = float(box[0]) + float(box[2]) / 2.0, float(box[1]) + float(box[3]) / 2.0
    em = float(sem.get("textplus_em_ratio") or 0.70)
    line_em = float(sem.get("textplus_line_em") or 1.15)
    font = lk.get("font") or "Arial"
    if font == "brand":
        notes.append("%s: the look asks for the brand font but none was resolved; Arial is used" % it.get("id"))
        font = "Arial"
    r, g, b = rgb01(lk.get("color"), (1.0, 1.0, 1.0))
    size = fpx / (em * W)
    # Text+ Size per font ([TXT] s2: em = Size x 0.804 x W / line_em, the font's hhea line height): Arial Bold keeps
    # the measured 0.70, Arial Black draws at 0.57, a brand font by its own metrics; the line pitch follows the same
    # line_em. A font whose metrics cannot be read keeps the Arial rule (noted).
    try:
        le_, measured_ = fx_lab.line_em_of(font, lk.get("style") or "Bold", lk.get("font_file"))
    except Exception:
        le_, measured_ = None, False
    if measured_:
        size = fx_lab.textplus_size(fpx, font, lk.get("style") or "Bold", W, lk.get("font_file"))
        line_em = float(le_)
    elif font not in ("Arial",):
        notes.append("%s: no metrics for the font %r; its Text+ size follows the Arial rule (0.70)" % (it.get("id"), font))
    inp = {"StyledText": "\n".join(lines), "Font": font, "Style": lk.get("style") or "Bold",
           "Size": round(size, 6), "Center": [round(cx / W, 6), round(1.0 - cy / H, 6)],
           "Red1": r, "Green1": g, "Blue1": b, "LineSpacing": round(float(lk.get("line_spacing") or line_em) / line_em, 4)}
    st = lk.get("stroke")
    if isinstance(st, dict) and float(st.get("em") or 0) > 0:
        sr, sg, sb = rgb01(st.get("color"), (0.0, 0.0, 0.0))
        inp.update({"Enabled2": 1, "Thickness2": round(float(st["em"]) * float(sem.get("textplus_thickness_per_em") or 1.14), 4),
                    "Red2": sr, "Green2": sg, "Blue2": sb})
    else:
        # the outline element defaults to red: its colour is set even when it is off
        inp.update({"Enabled2": 0, "Red2": 0.0, "Green2": 0.0, "Blue2": 0.0})
    sh = lk.get("shadow")
    if isinstance(sh, dict):
        hr, hg, hb = rgb01(sh.get("color"), (0.0, 0.0, 0.0))
        inp.update({"Enabled3": 1, "Red3": hr, "Green3": hg, "Blue3": hb, "Opacity3": float(sh.get("opacity", 0.5))})
    else:
        inp["Enabled3"] = 0
    bx = lk.get("box")
    if isinstance(bx, dict):
        pad = list(bx.get("pad_em") or [0.25, 0.3]) + [0.3, 0.3]
        P = sem.get("textplus_box_pad") or {}
        ex = max(0.0, (float(pad[0]) - float(P.get("x_base_em", 0.30))) / float(P.get("x_em_per_extend", 1.26) or 1.26))
        ey = max(0.0, (float(pad[1]) - float(P.get("y_base_em", 0.0))) / float(P.get("y_em_per_extend", 1.43) or 1.43))
        br, bg, bb = rgb01(bx.get("color"), (0.0, 0.0, 0.0))
        inp.update({"Enabled4": 1, "Red4": br, "Green4": bg, "Blue4": bb, "Opacity4": float(bx.get("opacity", 1.0)),
                    "Level4": int(sem.get("textplus_box_level", 0) or 0), "ExtendHorizontal4": round(ex, 4),
                    "ExtendVertical4": round(ey, 4), "Round4": float(bx.get("round") or 0.0)})
    else:
        inp["Enabled4"] = 0
    if (lk.get("align") or "center") != "center":
        notes.append("%s: align %s is built centred (Text+ alignment is not set by the build)" % (it.get("id"), lk["align"]))
    return inp, lk, notes


def gfx_plan(edl, lab, sem, captions="burn", text="textplus"):
    """What the graphics snippet builds: one spec per title (on its EDL track: titles may sit on two tracks, "Titles"
    and "Titles 2") and per caption cue (on a track above every EDL video track), in slot order, each with its Text+
    inputs and its item label. track_names holds only the caption track's name (track_name_plan names the rest)."""
    W, H = int(edl["timeline"]["width"]), int(edl["timeline"]["height"])
    items, notes, names = [], [], []
    top = max([track_num(t.get("id")) for t in edl["tracks"]["video"]] + [1])
    if text != "textplus":
        return {"items": [], "caption_track": None, "tracks_video": top, "track_names": [], "notes": []}
    fps = float(tl_fps(edl))
    for tr in edl["tracks"]["video"]:
        tn = track_num(tr.get("id"))
        titles = [it for it in sorted_items(tr) if it.get("kind") == "title" and it.get("enabled", True) is not False]
        for it in titles:
            inp, lk, nts = textplus_inputs(it, "title", W, H, sem)
            notes += nts
            items.append({"id": it["id"], "kind": "title", "track": tn, "rec_in": int(it["rec_in"]),
                          "rec_len": rec_len(it), "text": inp["StyledText"], "inputs": inp,
                          "label": {"name": item_label("TITLE", it["id"], inp["StyledText"]),
                                    "color": ITEM_COLORS["title"]}})
            attach_text_plan(items[-1], it, W, H, fps, notes)
    cap = None
    cues = [c for tr in edl["tracks"].get("subtitle", []) for c in tr.get("items", []) if rec_len(c) > 0]
    if captions == "burn" and cues:
        cap = top + 1
        for k, c in enumerate(sorted(cues, key=lambda c: (int(c["rec_in"]), int(c["rec_out"])))):
            inp, lk, nts = textplus_inputs(c, "caption", W, H, sem)
            notes += nts
            cid_ = c.get("id") or "cap%03d" % (k + 1)
            items.append({"id": cid_, "kind": "caption", "track": cap,
                          "rec_in": int(c["rec_in"]), "rec_len": rec_len(c), "text": inp["StyledText"], "inputs": inp,
                          "label": {"name": item_label("CAP", cid_, inp["StyledText"]), "color": ITEM_COLORS["caption"]}})
            attach_text_plan(items[-1], c, W, H, fps, notes)
        names.append(["video", cap, "Captions"])
    return {"items": items, "caption_track": cap, "tracks_video": max(top, cap or 0), "track_names": names,
            "notes": notes}


def gfx_fallback_markers(gp):
    """One Cream marker per title or caption, made by the finish snippet only for those Text+ could not build."""
    out = []
    for s in gp["items"]:
        one = s["text"].replace("\n", " ")
        out.append({"id": s["id"], "frame": int(s["rec_in"]), "duration": int(s["rec_len"]),
                    "name": "%s %s" % ("TITLE" if s["kind"] == "title" else "CAPTION", s["id"]),
                    "note": "%s (%d f): Text+ could not be built here; make it by hand" % (one, int(s["rec_len"]))})
    return out


def _pool_path(p, ci):
    """A media path compared the way the build snippet compares it (_re_norm)."""
    p = str(p or "").replace("\\", "/")
    while "//" in p[1:]:
        p = p[0] + p[1:].replace("//", "/")
    return p.lower() if ci else p


def dump_missing_note(lab, edl, mids, import_missing, bin_name):
    """NOTE line naming the EDL media that LAB/resolve/dump.json does not hold (None when all are there or there is
    no dump). The dump lists the current timeline's media and the bins it was asked for."""
    d = load_json_or(lab.p("resolve", "dump.json"), None)
    if not isinstance(d, dict) or not mids:
        return None
    ci = sys.platform in ("darwin", "win32")
    have = {_pool_path(m.get("path"), ci) for m in d.get("media") or [] if isinstance(m, dict) and m.get("path")}
    # media an earlier build placed after the dump was taken (it imported them) are in the pool too
    dump_t = os.path.getmtime(lab.p("resolve", "dump.json"))
    placed = set()
    for mf in glob.glob(lab.p("build", "*.map.json")):
        bm = load_json_or(mf, None)
        if not isinstance(bm, dict) or os.path.getmtime(mf) < dump_t or not bm.get("edl"):
            continue
        be = load_json_or(lab.rel(bm["edl"]), None)
        if not isinstance(be, dict):
            continue
        ok_ids = {k for k, v in (bm.get("items") or {}).items() if isinstance(v, dict) and v.get("ok")}
        ok_ids |= set(bm.get("stills") or [])
        for kind_, tr_, it_ in iter_items(be, ("video", "audio")):
            if is_media_item(it_) and (it_["id"] in ok_ids or any(k.startswith(it_["id"] + ".p") for k in ok_ids)):
                placed.add(_pool_path((be.get("media", {}).get(it_["media"]) or {}).get("path"), ci))
    miss = [mid for mid in mids if _pool_path(edl["media"][mid].get("path"), ci) not in have | placed]
    if not miss:
        return None
    names = ", ".join(os.path.basename(str(edl["media"][m].get("path") or m)) for m in miss[:8])
    names += ", ..." if len(miss) > 8 else ""
    if import_missing:
        how = ("chunk 1 imports whichever of them the media pool really lacks into the bin resolve-editor/%s "
               "(IMPORT_MISSING = True)" % bin_name)
    else:
        how = ("after the user says yes, run build-script again with --import-missing (chunk 1 then imports them into "
               "the bin resolve-editor/%s), or import them by hand" % bin_name)
    return "NOTE: %d of %d media are not in the media pool (as dump.json and the builds since it saw it): %s; %s" % (
        len(miss), len(mids), names, how)


# ------------------------------------------------------------ pixel verify: Edit-page grabs and proof renders
PIX_MIN_CORR = 0.6       # frames compare as 36x64 (or 64x36) grey, each normalised; under this they differ
PIX_FLAT_SD = 2.0        # a grey frame this flat (0 to 255 levels) is black or empty


def grab_frames(edl, max_frames=60):
    """Timeline frames worth grabbing, cuts first: the first and last frame, both sides of every picture cut, then
    the middle of every picture item of 6 frames or more and of every title and caption; at most max_frames."""
    N = programme_frames(edl)
    if N <= 0:
        return []
    cuts, mids = set(), []
    for tr in edl["tracks"]["video"]:
        for it in sorted_items(tr):
            if is_media_item(it):
                cuts.update((int(it["rec_in"]), int(it["rec_out"])))
                if rec_len(it) >= 6:
                    mids.append((int(it["rec_in"]) + int(it["rec_out"])) // 2)
            elif it.get("kind") == "title":
                mids.append((int(it["rec_in"]) + int(it["rec_out"])) // 2)
    for tr in edl["tracks"].get("subtitle", []):
        for c in tr.get("items", []):
            mids.append((int(c["rec_in"]) + int(c["rec_out"])) // 2)
    order = [0, N - 1]
    for c in sorted(cuts):
        order += [c - 1, c]
    order += mids
    seen, out = set(), []
    for f in order:
        if 0 <= f < N and f not in seen:
            seen.add(f)
            out.append(f)
        if len(out) >= max(1, int(max_frames)):
            break
    return sorted(out)


GRABS_PER_CALL = 30          # Edit-page grabs per run of the grab snippet (about 1 s each with 4K comps, [API] s5)


def edl_has_fx(edl):
    """Whether an EDL carries anything of the effects catalogue (keyed fx, a retime, an animated text, a catalogue
    transition). An EDL without them is grabbed and pixel-checked exactly as before."""
    return any(it.get(k) for kind_, tr_, it in iter_items(edl) for k in ("fx", "motion", "accents", "retime", "anim")) or \
        any((x.get("resolve") or {}).get("type") or (x.get("resolve") or {}).get("build") for x in edl.get("transitions", []))


def fx_grab_frames(edl, max_n=30):
    """Frames the effects add to the grabs: fx_lab.grab_targets (every fx peak, every transition's middle, the frames
    around approximate spans) plus, for each span with an aux statistic, the frame before it (the same shot outside
    the effect, the reference the aux check needs)."""
    if not edl_has_fx(edl):
        return []                    # an EDL without effects grabs as before
    try:
        fr = list(fx_lab.grab_targets(edl, max_n))
        fr += [int(s_["f0"]) - 1 for s_ in fx_lab.pixel_plan(edl) if s_.get("aux") and int(s_["f0"]) >= 1]
    except Exception:
        return []
    return sorted(set(int(f) for f in fr if f >= 0))


def cmd_grab_script(lab, edl_path, max_frames=60):
    edl = load_edl(edl_path)
    ver = edl_version(edl, edl_path)
    frames = grab_frames(edl, max_frames)
    N = programme_frames(edl)
    frames = sorted(set(frames) | set(f for f in fx_grab_frames(edl) if 0 <= f < N))
    if not frames:
        raise Fail("the EDL has no frames to grab")
    r = lab.project.get("resolve") or {}
    out = lab.p("verify", ver, "grabs")
    cfg = {"map": posix(lab.p("build", "%s.map.json" % ver)), "out": posix(out), "frames": frames,
           "fps": float(tl_fps(edl)), "project": r.get("project"), "per_call": GRABS_PER_CALL, "budget_s": 40,
           "run_id": iso_now()}
    text = (snippet_head(lab, "grab %s" % ver, "Changes nothing in the project: Edit-page frame grabs of the built "
                                               "timeline into LAB/verify/%s/grabs/ (playhead put back)." % ver, ver)
            + SNIPPET_HEADER + COMMON_BODY + TC_BODY + GRAB_BODY + cfg_literal(cfg)
            + "result = _re_grab(resolve, project, CFG)\n")
    fn = write_snippet(lab, "grab_%s.py" % ver, text)
    runs = int(math.ceil(len(frames) / float(GRABS_PER_CALL)))
    print("grabs %d frames (about %d s; open the Edit page first%s); then: edit_lab.py LAB verify %s --grabs"
          % (len(frames), int(math.ceil(0.5 * len(frames))),
             "; run it %d times, until it no longer reports more" % runs if runs > 1 else "", posix(edl_path)))
    return fn


def _pix_grid(edl):
    W, H = int(edl["timeline"]["width"]), int(edl["timeline"]["height"])
    return (36, 64) if H > W else ((64, 36) if W > H else (48, 48))


def decode_grey(path, gw, gh):
    """Every frame of a video as (n, gh, gw) float grey (area scaled)."""
    r = run([ffmpeg_bin(), "-nostdin", "-v", "error", "-i", path, "-vf", "scale=%d:%d:flags=area,format=gray" % (gw, gh),
             "-f", "rawvideo", "-"], "ffmpeg (grey frames)")
    a = np.frombuffer(r.stdout, np.uint8)
    return a[:a.size - a.size % (gw * gh)].reshape(-1, gh, gw).astype(np.float32)


def image_grey(path, gw, gh):
    from PIL import Image
    with Image.open(path) as im:
        return np.asarray(im.convert("L").resize((gw, gh), Image.BOX), np.float32)


def shrink_grab(path):
    """Resolve 21.1 writes Edit-page grabs as uncompressed PNG (6.2 MB for 1080x1920, measured): store it again
    compressed, same pixels (about a third of the size). Never fails the verify."""
    from PIL import Image
    try:
        with Image.open(path) as im:
            w, h = im.size
            if os.path.getsize(path) < 1.5 * w * h or im.format != "PNG":
                return
            tmp = path + ".tmp"
            im.save(tmp, "PNG", compress_level=6)
        os.replace(tmp, path)
    except (OSError, ValueError):
        pass


def text_mask(edl, f, gw, gh):
    """Grid cells to compare at frame f: every title and caption box is left out (with one cell of margin), because
    Text+ and the preview's text are not drawn by the same engine."""
    W, H = float(edl["timeline"]["width"]), float(edl["timeline"]["height"])
    m = np.ones((gh, gw), bool)
    boxes = [it.get("box") for tr in edl["tracks"]["video"] for it in tr.get("items", [])
             if it.get("kind") == "title" and int(it["rec_in"]) <= f < int(it["rec_out"])]
    boxes += [c.get("box") for tr in edl["tracks"].get("subtitle", []) for c in tr.get("items", [])
              if int(c["rec_in"]) <= f < int(c["rec_out"])]
    for b in boxes:
        if not b:
            continue
        x, y, w, h = [float(v) for v in b]
        xs, xe = max(0, int(x / W * gw) - 1), min(gw, int(math.ceil((x + w) / W * gw)) + 1)
        ys, ye = max(0, int(y / H * gh) - 1), min(gh, int(math.ceil((y + h) / H * gh)) + 1)
        m[ys:ye, xs:xe] = False
    return m


def _anim_text_boxes(edl):
    """[(f0, f1, box)] of every animated title and caption: the box its text reaches at its largest scale or along its
    slide (fx_lab.animated_box), masked out of the pixel check over the cue (the text itself is checked on a proof by
    text_compare)."""
    W, H = float(edl["timeline"]["width"]), float(edl["timeline"]["height"])
    fps = float(tl_fps(edl))
    out = []
    items = [it for tr in edl["tracks"]["video"] for it in tr.get("items", []) if it.get("kind") == "title"]
    items += [c for tr in edl["tracks"].get("subtitle", []) for c in tr.get("items", [])]
    for it in items:
        if (it.get("anim") or {}).get("id") in (None, "none") or not it.get("box"):
            continue
        try:
            st = fx_lab.text_plan(it, W, H, fps).get("states") or []
            box = fx_lab.animated_box(it, st, W, H)
        except Exception:
            b = [float(v) for v in it["box"]]
            box = [b[0] - 0.15 * b[2], b[1] - 0.15 * b[3], 1.3 * b[2], 1.3 * b[3]]
        out.append((int(it["rec_in"]), int(it["rec_out"]), box))
    return out


def pixel_compare(edl, prev, tests):
    """prev: the preview's grey frames; tests: {frame: grey frame of the build}. One row per tested frame.
    Inside an effect span the threshold is the span's tolerance (fx_lab.pixel_plan, [PRV] s10): push_zoom_keyed
    0.95, speed_ramp and exact transitions 0.90, close transitions 0.75 on the span median; frames of approximate
    spans are not correlated (row "skipped", reported as fx_pixels_approx); animated text boxes are masked along
    their whole path. Outside every span PIX_MIN_CORR applies."""
    gh, gw = prev.shape[1], prev.shape[2]
    W, H = float(edl["timeline"]["width"]), float(edl["timeline"]["height"])
    try:
        plan = [s_ for s_ in fx_lab.pixel_plan(edl) if s_.get("tolerance") != "text_animated"]
    except Exception:
        plan = []
    try:
        aboxes = _anim_text_boxes(edl)
    except Exception:
        aboxes = []
    rows, medians = [], {}
    for f in sorted(tests):
        if f >= len(prev):
            rows.append({"frame": f, "corr": None, "bad": True, "why": "past the end of the preview"})
            continue
        m = text_mask(edl, f, gw, gh)
        for f0, f1, b in aboxes:
            if f0 <= f < f1:
                x, y, w, h = [float(v) for v in b]
                xs, xe = max(0, int(x / W * gw) - 1), min(gw, int(math.ceil((x + w) / W * gw)) + 1)
                ys, ye = max(0, int(y / H * gh) - 1), min(gh, int(math.ceil((y + h) / H * gh)) + 1)
                m[ys:ye, xs:xe] = False
        if m.sum() < 16:
            continue
        rule = fx_lab.frame_rule(plan, f) if plan else None
        if rule is not None and (rule.get("mark") or rule.get("min_corr") is None):
            rows.append({"frame": int(f), "corr": None, "bad": False, "why": None, "skipped": "approximate",
                         "rule": rule.get("tolerance"), "rule_fx": rule.get("fx")})
            continue
        thr = PIX_MIN_CORR if rule is None else float(rule["min_corr"])
        a, b = prev[f][m], np.asarray(tests[f], np.float32)[m]
        sa, sb = float(a.std()), float(b.std())
        fa, fb = sa < PIX_FLAT_SD, sb < PIX_FLAT_SD
        corr = None
        if not fa and not fb:
            corr = float(((a - a.mean()) * (b - b.mean())).mean() / (sa * sb))
        why = None
        if fb and not fa:
            why = "the build is flat (black or empty) where the preview has picture"
        elif fa and not fb:
            why = "the build has picture where the preview is flat"
        elif fa and fb and abs(float(a.mean()) - float(b.mean())) > 40:
            why = "both flat but at different levels"
        elif corr is not None and corr < thr and not (rule and rule.get("scope") == "span_median"):
            why = ("the picture differs (correlation %.2f)" % corr if rule is None else
                   "the picture differs (correlation %.2f, %s needs %.2f)" % (corr, rule.get("tolerance"), thr))
        row = {"frame": int(f), "corr": None if corr is None else round(corr, 3), "bad": why is not None, "why": why}
        if rule is not None:
            row.update(rule=rule.get("tolerance"), rule_fx=rule.get("fx"))
            if rule.get("scope") == "span_median" and corr is not None:
                medians.setdefault((rule.get("fx"), thr), []).append(row)
        rows.append(row)
    for (fid, thr), rs in medians.items():
        med = float(np.median([r_["corr"] for r_ in rs]))
        if med < thr:
            for r_ in rs:
                r_.update(bad=True, why="the span %s differs from the preview (median correlation %.2f, needs %.2f)"
                          % (fid, med, thr))
    return rows


def preview_grey(lab, edl_path, edl, gw, gh):
    rdir = review_dir(edl_path)
    if not preview_fresh(edl, rdir):
        st, rdir = render_preview(lab, edl_path, quiet=True)
        if st.get("result") != "OK":
            raise Fail("the preview could not be rendered for the pixel check: %s" % "; ".join(st.get("problems") or []))
    return decode_grey(os.path.join(rdir, "preview.mov"), gw, gh)


def verify_pixels(lab, edl_path, edl, vdir, grabs=False, proof=None):
    """Compare the built timeline's pixels with the preview: Edit-page grabs (grab-script) or a rendered file.
    Returns {"source", "compared", "median_corr", "bad": [rows], "rows"}."""
    gw, gh = _pix_grid(edl)
    tests, src, skipped = {}, None, ([], 0)
    if proof:
        if not os.path.exists(proof):
            raise Fail("no rendered file at %s" % posix(proof))
        got = decode_grey(proof, gw, gh)
        tests = {f: got[f] for f in range(len(got))}
        src = "proof %s (%d frames)" % (os.path.basename(proof), len(got))
    if grabs:
        gdir = os.path.join(vdir, "grabs")
        doc = load_json_or(os.path.join(gdir, "grabs.json"), None)
        if not doc or not doc.get("frames"):
            raise Fail("no grabs in %s: run grab-script and its RUN line first" % posix(gdir))
        unreadable, absent = [], 0
        for r_ in doc["frames"]:
            p = os.path.join(gdir, r_.get("file") or "")
            if not (r_.get("ok") and os.path.isfile(p)):
                absent += 1
                continue
            try:
                tests[int(r_["frame"])] = image_grey(p, gw, gh)
            except (OSError, ValueError, SyntaxError):
                unreadable.append(os.path.basename(p))
                continue
            shrink_grab(p)
        if not tests and not proof:
            raise Fail("no usable grab in %s (%d listed, %d missing or failed, %d unreadable%s): run grab-script and "
                       "its RUN line again, then verify --grabs" % (
                           posix(gdir), len(doc["frames"]), absent, len(unreadable),
                           ": " + ", ".join(unreadable[:4]) if unreadable else ""))
        src = "%d Edit-page grabs" % len(tests)
        skipped = (unreadable, absent)
        if unreadable or absent:
            src += " (%d skipped: %s)" % (len(unreadable) + absent, ", ".join(
                (["%d missing or failed" % absent] if absent else []) + (["unreadable " + ", ".join(unreadable[:4])]
                                                                         if unreadable else [])))
    prev = preview_grey(lab, edl_path, edl, gw, gh)
    rows = pixel_compare(edl, prev, tests)
    if proof and len(tests) != len(prev):
        rows.append({"frame": min(len(tests), len(prev)), "corr": None, "bad": True,
                     "why": "the render has %d frames, the preview %d" % (len(tests), len(prev))})
    cs = [r_["corr"] for r_ in rows if r_.get("corr") is not None]
    res = {"source": src, "compared": len(rows), "median_corr": round(float(np.median(cs)), 3) if cs else None,
           "bad": [r_ for r_ in rows if r_["bad"]], "rows": rows, "edl_hash": edl_hash(edl),
           "readback": readback_stamp(vdir)}
    if skipped[0] or skipped[1]:
        res["skipped"] = {"unreadable": skipped[0], "missing_or_failed": skipped[1]}
    write_json(os.path.join(vdir, "pixels.json"), res)
    return res


PROOF_DIP_DB = 10.0      # a 5 ms stretch of the render this far below the sound around it is a dropout
PROOF_DIP_MARGIN_DB = 8.0  # ... unless the preview dips there too (within this margin)


def decode_mono(path, sr=48000):
    """The sound of a file as mono float samples at sr (empty when it has none)."""
    try:
        r = subprocess.run([ffmpeg_bin(), "-nostdin", "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", str(sr),
                            "-f", "f32le", "-"], capture_output=True, timeout=900)
    except (OSError, subprocess.SubprocessError):
        return np.zeros(0, np.float32)
    return np.frombuffer(r.stdout, np.float32)


def rms_5ms(x, sr=48000):
    hop = sr // 200
    n = len(x) // hop
    if n == 0:
        return np.zeros(0)
    return 10 * np.log10((x[:n * hop].reshape(n, hop).astype(np.float64) ** 2).mean(1) + 1e-12)


def audio_dips(got, ref, sr=48000):
    """Short dropouts in `got` (a render) that `ref` (the preview's mix) does not have: 5 ms windows at least
    PROOF_DIP_DB below the loudest 5 ms of the 20 ms on each side, where the preview at the same moment (within
    15 ms) dips at least PROOF_DIP_MARGIN_DB less. Near silence (both sides under -50 dBFS) is skipped. Returns
    [(seconds, dip dB, preview dip dB)], one per dropout."""
    a, b = rms_5ms(got, sr), rms_5ms(ref, sr)
    n = min(len(a), len(b))
    out = []

    def depth(v, k):
        lo, hi = v[max(0, k - 5):max(0, k - 1)], v[k + 2:k + 6]
        if len(lo) == 0 or len(hi) == 0:
            return None, None
        side = min(float(lo.max()), float(hi.max()))
        return side - float(v[k]), side
    k = 5
    while k < n - 6:
        d, side = depth(a, k)
        if d is not None and d >= PROOF_DIP_DB and side > -50.0:
            dr = [depth(b, j)[0] for j in range(max(5, k - 3), min(n - 6, k + 4))]
            dr = max([x for x in dr if x is not None] or [0.0])
            if d - dr >= PROOF_DIP_MARGIN_DB:
                out.append((round(k * 0.005, 3), round(d, 1), round(dr, 1)))
                k += 8
                continue
        k += 1
    return out


def verify_audio(lab, edl_path, edl, proof):
    """The proof render's sound against the preview's mix: dropouts the preview does not have (audio_dips)."""
    rdir = review_dir(edl_path)
    if not preview_fresh(edl, rdir):
        st, rdir = render_preview(lab, edl_path, quiet=True)
        if st.get("result") != "OK":
            return None
    got, ref = decode_mono(proof), decode_mono(os.path.join(rdir, "preview.mov"))
    if len(got) == 0 or len(ref) == 0:
        return None
    return audio_dips(got, ref)


def readback_stamp(vdir):
    """Newest modification time of the readback pages: a pixel result stays valid while the readback it was compared
    against is unchanged."""
    ts = [os.path.getmtime(os.path.join(vdir, f)) for f in (os.listdir(vdir) if os.path.isdir(vdir) else [])
          if re.match(r"^readback_p\d+\.json$", f)]
    return round(max(ts), 3) if ts else None


# --------------------------------------------------------------------------------------- delivery (render job)
# Per platform: container, codec and video bit rate. VideoQuality takes the bit rate in kb/s (measured in 21.1: 30000
# rendered 23.3 Mb/s of H.264 at 1080x1920, 4000 rendered 3.2 Mb/s at 540x960).
DELIVERY = {
    "reels": {"format": "mp4", "codec": "H264", "kbps": 30000},
    "tiktok": {"format": "mp4", "codec": "H264", "kbps": 30000},
    "shorts": {"format": "mp4", "codec": "H264", "kbps": 30000},
    "vertical_all": {"format": "mp4", "codec": "H264", "kbps": 30000},
    "youtube": {"format": "mp4", "codec": "H264", "kbps": 40000},
    "linkedin": {"format": "mp4", "codec": "H264", "kbps": 20000},
    "x": {"format": "mp4", "codec": "H264", "kbps": 20000},
    "square": {"format": "mp4", "codec": "H264", "kbps": 20000},
}
DELIVERY_COMMON = {"render_preset": "H.264 Master", "audio_codec": "aac", "audio_rate": 48000, "color_space": "Rec.709",
                   "gamma": "Rec.709-A", "network_optimization": True}
DELIVERY_READBACK = ("RenderJobName", "TimelineName", "TargetDir", "OutputFilename", "FormatWidth", "FormatHeight",
                     "FrameRate", "VideoFormat", "VideoCodec", "AudioCodec", "AudioSampleRate", "AudioBitDepth",
                     "NetworkOptimization", "PresetName", "RenderMode", "IsExportVideo", "IsExportAudio", "MarkIn",
                     "MarkOut", "DataBurnIn", "ExportSubtitle", "EncodingProfile", "MultiPassEncode")
# settings the job must keep: one refused when set, or read back different in the job, removes the job
# (render_settings_refused); 21.1 lists EncodingProfile and MultiPassEncode in the job, not the other two
DELIVERY_STRICT = ("DataBurnIn", "ExportSubtitle")        # no job when Resolve refuses these (a burn-in, subtitles)
DELIVERY_PREFER = ("EncodingProfile", "MultiPassEncode")   # asked for, but the job is made without them when an
                                                           # encoder does not offer them (prefer_refused, a note)


def measure_lufs(path):
    """Integrated loudness (LUFS) and true peak (dBTP) of a file's sound by ffmpeg's EBU R128 meter, or (None, None)."""
    try:
        r = subprocess.run([ffmpeg_bin(), "-nostdin", "-hide_banner", "-i", path, "-vn", "-af", "ebur128=peak=true",
                            "-f", "null", "-"], capture_output=True, timeout=900)
    except (OSError, subprocess.SubprocessError):
        return None, None
    txt = r.stderr.decode("utf-8", "replace")
    i = re.findall(r"I:\s+(-?[\d.]+) LUFS", txt)
    p = re.findall(r"Peak:\s+(-?[\d.]+) dBFS", txt)
    return (float(i[-1]) if i else None), (float(p[-1]) if p else None)


def loudness_route(lab, edl_path, edl, P):
    """What the delivered file needs for the preset's loudness, measured on the preview mix (the build's mix matches
    it), and how to get there: Resolve has no scripting key for loudness normalisation."""
    au = P.get("audio") or {}
    target, tp = au.get("lufs"), au.get("codec_tp_db", au.get("true_peak_db"))
    mg = float((edl.get("mix") or {}).get("gain_db") or 0.0)
    rdir = review_dir(edl_path)
    lufs = peak = None
    if preview_fresh(edl, rdir):
        # the preview mix includes the EDL mix gain, as the build does; its own measure first (preview.json)
        pa = (load_json_or(os.path.join(rdir, "preview.json"), None) or {}).get("audio") or {}
        if isinstance(pa.get("lufs_i"), (int, float)) and not isinstance(pa.get("lufs_i"), bool):
            lufs, peak = float(pa["lufs_i"]), pa.get("tp_dbtp")
        else:
            lufs, peak = measure_lufs(os.path.join(rdir, "preview.mov"))
    gain = round(float(target) - lufs, 1) if (lufs is not None and target is not None) else None
    how = ("Resolve has no scripting key for loudness: on the Deliver page, Audio tab, tick Normalize Audio Levels "
           "and choose Optimize to Standard, %s LUFS with true peak %s dBTP, before rendering" % (target, tp))
    if mg:
        how = ("the build already carries the EDL mix gain (%+g dB on every audio item). For what is left: %s"
               % (mg, how))
    if gain is not None and abs(gain) <= 0.5:
        how = "the mix is within 0.5 LU of %s LUFS: nothing is left for the Deliver page" % target
    elif any(c[0] == "mix" for c in COMMANDS):
        how += "; or print the mix with `E mix` and use that file as the sound"
    if lufs is None:
        how += " (render the preview first to measure how far the mix is from the target)"
    return {"preview_lufs": lufs, "preview_peak_dbfs": peak, "target": target, "true_peak_target": tp,
            "gain_needed_db": gain, "mix_gain_db": mg, "residual_lu": gain, "how": how}


def delivery_settings(lab, edl_path, edl, platform=None, out=None, proof=False):
    P = load_preset(edl.get("preset"), lab, required=False)
    plat = platform or edl.get("platform") or P.get("platform") or "vertical_all"
    if plat not in DELIVERY:
        raise Fail("unknown platform %r: %s" % (plat, ", ".join(sorted(DELIVERY))))
    d = dict(DELIVERY[plat], **DELIVERY_COMMON)
    d.update(P.get("delivery") or {})
    ver = edl_version(edl, edl_path)
    W, H = int(edl["timeline"]["width"]), int(edl["timeline"]["height"])
    if proof:
        W, H, d["kbps"] = even(W / 2), even(H / 2), 4000
    target = os.path.abspath(out) if out else (lab.p("verify", ver) if proof else lab.p("deliver", ver))
    name = "proof" if proof else safe_name("%s_%s" % (lab.project.get("name") or edl["timeline"].get("name") or "edit", ver))
    st = {"SelectAllFrames": True, "TargetDir": posix(target), "CustomName": name, "ExportVideo": True, "ExportAudio": True,
          "FormatWidth": W, "FormatHeight": H, "VideoQuality": int(d["kbps"]), "AudioCodec": d["audio_codec"],
          "AudioSampleRate": int(d["audio_rate"]), "NetworkOptimization": bool(d["network_optimization"]),
          "ColorSpaceTag": d["color_space"], "GammaTag": d["gamma"],
          # no data burn-in and no subtitle stream whatever the project says (21.1 takes "None", not "none")
          "DataBurnIn": load_semantics().get("render_data_burn_in_none") or "None", "ExportSubtitle": False}
    if str(d.get("codec")).upper() in ("H264", "H.264"):
        st["EncodingProfile"] = "High"                  # reads back in the job (21.1)
        if not proof:
            st["MultiPassEncode"] = True                # where the encoder offers it; a proof renders in one pass
    return plat, d, st, P


def deliver_snippet(lab, edl_path, platform=None, start=False, out=None, proof=False):
    edl = load_edl(edl_path)
    ver = edl_version(edl, edl_path)
    mp_path = lab.p("build", "%s.map.json" % ver)
    if not os.path.exists(mp_path):
        raise Fail("no build map for %s: run build-script and its RUN lines first" % ver)
    plat, d, st, P = delivery_settings(lab, edl_path, edl, platform, out, proof)
    os.makedirs(st["TargetDir"], exist_ok=True)
    r = lab.project.get("resolve") or {}
    cfg = {"project": r.get("project"), "project_uid": r.get("project_uid"), "map": posix(mp_path),
           "render_preset": d["render_preset"], "format": d["format"], "codec": d["codec"], "settings": st,
           "readback": list(DELIVERY_READBACK), "strict": list(DELIVERY_STRICT), "prefer": list(DELIVERY_PREFER),
           "start": bool(start or proof),
           "wait_s": 50, "delete_job": bool(proof),
           "loudness": None if proof else loudness_route(lab, edl_path, edl, P)}
    what = ("Writes a render job for the built timeline and renders a small proof into LAB/verify/%s/, then deletes the "
            "job and puts the page back." % ver) if proof else (
        "Writes a render job for the built timeline (%s, %s)%s; never an upload option; puts the page back."
        % (plat, os.path.basename(st["TargetDir"]), ", then renders it" if start else ""))
    text = (snippet_head(lab, "%s %s" % ("proof" if proof else "deliver", ver), what, ver) + "FORCE = False\n"
            + SNIPPET_HEADER + COMMON_BODY + DELIVER_BODY + cfg_literal(cfg)
            + "result = _re_deliver(resolve, project, CFG, FORCE)\n")
    fn = write_snippet(lab, "%s_%s.py" % ("proof" if proof else "deliver", ver), text)
    if proof:
        print("then: edit_lab.py LAB verify %s --proof %s" % (posix(edl_path), posix(os.path.join(st["TargetDir"],
                                                                                               "proof.%s" % d["format"]))))
    else:
        lo = cfg["loudness"]
        print("ask the user before running it (it adds a render job to the project%s)" % (
            " and renders" if start else ""))
        print("video: %s %s %dx%d at %d kb/s, audio %s %d Hz"
              % (d["format"], d["codec"], st["FormatWidth"], st["FormatHeight"], st["VideoQuality"], st["AudioCodec"],
                 st["AudioSampleRate"]))
        if lo.get("gain_needed_db") is not None:
            print("loudness: the mix measures %.1f LUFS with the mix gain %+g dB, the target is %s LUFS (residual "
                  "%+.1f LU)" % (lo["preview_lufs"], lo.get("mix_gain_db") or 0.0, lo["target"], lo["residual_lu"]))
        print("loudness: %s" % lo["how"])
    return fn


TEXTLESS_PRESETS = ("ad_15", "ad_30", "product_demo")
TEXTLESS_BODY = r'''

def _re_textless_targets(tl, mp, CFG):
    """The programme's video tracks that carry text: every track holding one of the build's title or caption items
    (the build map's gfx uids), plus every track named as the EDL's title tracks or Captions. Read while the
    programme is current."""
    uids = {str(v.get("uid")) for v in ((mp.get("gfx") or {}).get("items") or {}).values() if v.get("uid")}
    want = {}
    for t in range(1, (_re_call(tl, "GetTrackCount", "video") or 0) + 1):
        nm = _re_call(tl, "GetTrackName", "video", t)
        by_item = bool(uids) and any(str(_re_call(it, "GetUniqueId")) in uids
                                     for it in (_re_call(tl, "GetItemListInTrack", "video", t) or []))
        if by_item or nm in CFG["tracks"]:
            want[t] = nm
    return want


def _re_textless_off(copy_, want, res, CFG):
    """Switch every target track of the (current) copy off and read it back: res["off"], res["refused"]."""
    for t in sorted(want):
        nm = _re_call(copy_, "GetTrackName", "video", t)
        if _re_call(copy_, "GetIsTrackEnabled", "video", t):
            copy_.SetTrackEnable("video", t, False)
            if _re_call(copy_, "GetIsTrackEnabled", "video", t):
                res["refused"].append([t, nm])
                continue
            res.setdefault("switched", []).append([t, nm])
        res["off"].append([t, nm])
        if nm not in CFG["tracks"]:
            res.setdefault("renamed", []).append([t, nm])


def _re_textless(resolve, project, CFG, FORCE=False):
    """A copy of the built timeline with its title and caption tracks switched off: the textless master an ad is
    re-versioned from. The programme is never changed: DuplicateTimeline makes the COPY current (measured in 21.1),
    the tracks are switched off and checked while the copy is current. Track flags read False on a timeline that is
    not current (21.1), so the programme is made current before its flags are read; the timeline that was current
    when the snippet started is current again at the end. The text tracks are the ones holding the build's title and
    caption items (by unique id) plus the ones named for them, so a renamed title track is still switched off. When
    the copy exists already (a run that stopped half way), it is checked and any text track still on is switched
    off; ok only when every text track of the copy is off."""
    t0 = time.time()
    bad = _re_guard(resolve, project, CFG, FORCE)
    if bad:
        return bad
    mp = _re_load(CFG["map"], {}) or {}
    tl = _re_timeline_by_uid(project, mp["timeline_uid"]) if mp.get("timeline_uid") else None
    if tl is None:
        return {"error": "no_build", "msg": "the build map names no timeline in this project: build first"}
    base = tl.GetName()
    if base.endswith(" (resolve-editor)"):
        base = base[:-len(" (resolve-editor)")]
    name = base + " textless (resolve-editor)"
    cur0 = project.GetCurrentTimeline()
    res = {"timeline": name, "programme": tl.GetName(), "off": [], "refused": []}
    pool = project.GetMediaPool()
    before = _re_call(pool, "GetCurrentFolder")
    page0 = _re_call(resolve, "GetCurrentPage")
    copy_ = None
    try:
        if page0 == "deliver":
            # SetTrackEnable answers True on the Deliver page but changes nothing (21.1): switch on the Edit page
            res["page_opened"] = bool(resolve.OpenPage("edit")) and "edit"
        project.SetCurrentTimeline(tl)
        want = _re_textless_targets(tl, mp, CFG)
        nv = _re_call(tl, "GetTrackCount", "video") or 0
        on0 = [bool(_re_call(tl, "GetIsTrackEnabled", "video", t)) for t in range(1, nv + 1)]
        for i in range(1, (_re_call(project, "GetTimelineCount") or 0) + 1):
            t_ = project.GetTimelineByIndex(i)
            if t_ is not None and t_.GetName() == name:
                copy_ = t_
                res["exists"] = True
        if copy_ is None:
            for s in (_re_call(pool.GetRootFolder(), "GetSubFolderList") or []):
                if _re_call(s, "GetName") == "resolve-editor":
                    for b in (_re_call(s, "GetSubFolderList") or []):
                        if _re_call(b, "GetName") == CFG.get("bin"):
                            pool.SetCurrentFolder(b)
            copy_ = tl.DuplicateTimeline(name)
            if copy_ is None:
                return {"error": "duplicate_failed", "msg": "Resolve did not duplicate the timeline", "timeline": name}
        project.SetCurrentTimeline(copy_)
        if not want:
            res["msg"] = "no title or caption track found in the programme"
        _re_textless_off(copy_, want, res, CFG)
        if res.get("exists"):
            res["msg"] = ("the textless copy existed already: checked, %d text track%s switched off now" % (
                len(res.get("switched") or []), "" if len(res.get("switched") or []) == 1 else "s"))
        project.SetCurrentTimeline(tl)
        on1 = [bool(_re_call(tl, "GetIsTrackEnabled", "video", t)) for t in range(1, nv + 1)]
        res["programme_unchanged"] = on1 == on0
    finally:
        back = cur0 if cur0 is not None else tl
        project.SetCurrentTimeline(back)
        if before is not None:
            pool.SetCurrentFolder(before)
        if page0 and _re_call(resolve, "GetCurrentPage") != page0:
            res["page_restored"] = bool(resolve.OpenPage(page0))
    cur = project.GetCurrentTimeline()
    res["current_restored"] = cur is not None and _re_call(cur, "GetUniqueId") == _re_call(back, "GetUniqueId")
    res["ok"] = (bool(want) and len(res["off"]) == len(want) and not res["refused"]
                 and res.get("programme_unchanged", False) and res["current_restored"])
    res["seconds"] = round(time.time() - t0, 2)
    return res

'''


def textless_snippet(lab, edl_path):
    """textless_<ver>.py for an ad (TEXTLESS_PRESETS): a copy of the built timeline without its title and caption
    tracks, for re-versioning. Returns the file, or None for other presets."""
    edl = load_edl(edl_path)
    if edl.get("preset") not in TEXTLESS_PRESETS:
        return None
    ver = edl_version(edl, edl_path)
    mp_path = lab.p("build", "%s.map.json" % ver)
    if not os.path.exists(mp_path):
        raise Fail("no build map for %s: run build-script and its RUN lines first" % ver)
    r = lab.project.get("resolve") or {}
    edit = lab.project.get("name") or edl["timeline"].get("name") or "edit"
    names = ["Captions"]
    for tr in edl["tracks"]["video"]:
        if any(it.get("kind") == "title" for it in tr.get("items", [])):
            names.append(str(tr.get("name") or tr.get("id")))
    for n_ in ("Titles", "Titles 2"):
        if n_ not in names:
            names.append(n_)
    cfg = {"project": r.get("project"), "project_uid": r.get("project_uid"), "map": posix(mp_path),
           "bin": safe_name(edit), "tracks": names}
    text = (snippet_head(lab, "textless %s" % ver, "Writes: makes a NEW timeline, a copy of the build with the title "
                                                   "and caption tracks switched off; never changes the programme.", ver)
            + "FORCE = False\n" + SNIPPET_HEADER + COMMON_BODY + TEXTLESS_BODY + cfg_literal(cfg)
            + "result = _re_textless(resolve, project, CFG, FORCE)\n")
    fn = write_snippet(lab, "textless_%s.py" % ver, text, quiet=True)
    print("textless copy for re-versioning (ask the user first, it adds a timeline): %s" % run_line(fn))
    return fn


def cmd_deliver_script(lab, edl_path, platform=None, start=False, out=None):
    fn = deliver_snippet(lab, edl_path, platform, start, out)
    textless_snippet(lab, edl_path)
    return fn


def cmd_proof_script(lab, edl_path):
    return deliver_snippet(lab, edl_path, proof=True)


# ------------------------------------------------------------------------------------------ doctor, schema, presets
def cmd_doctor(as_json=False):
    rows = []

    def add(ok, what, detail):
        rows.append({"ok": bool(ok), "check": what, "detail": detail})
    v = sys.version_info
    add(v >= (3, 10), "python", "%d.%d.%d" % (v[0], v[1], v[2]) + ("" if v >= (3, 10) else ": need 3.10 or newer"))
    add(True, "numpy", np.__version__)
    try:
        import PIL
        from PIL import ImageFont
        ImageFont.load_default(size=15)
        add(True, "pillow", PIL.__version__)
    except Exception as e:
        add(False, "pillow", "missing or older than 10.1 (%s): run the install script again" % e)
    for tool in (ffmpeg_bin(), ffprobe_bin()):
        try:
            out = subprocess.run([tool, "-version"], capture_output=True, encoding="utf-8", errors="replace",
                                 timeout=20).stdout.split("\n")[0]
            add(bool(out), os.path.basename(tool), out or "no output")
        except Exception:
            add(False, os.path.basename(tool), "not found. Install ffmpeg (macOS: brew install ffmpeg, Windows: winget "
                "install Gyan.FFmpeg, Linux: sudo apt install ffmpeg) or set RE_FFMPEG / RE_FFPROBE")
    names = preset_names()
    add(bool(names), "presets", ("%d presets" % len(names)) if names else "no presets folder next to edit_lab.py")
    try:
        s = load_json(SEMANTICS_PATH)
        add(s.get("schema") == "resolve-editor/semantics@1", "semantics",
            "resolve_semantics.json readable (measured on %s)" % (s.get("measured_on") or "not yet: defaults"))
    except Fail as e:
        add(False, "semantics", str(e))
    ok = all(r["ok"] for r in rows)
    print("DOCTOR: %s" % ("OK" if ok else "PROBLEMS"))
    if as_json:
        print(json.dumps({"ok": ok, "checks": rows}, indent=1))
        return ok
    for r in rows:
        print("%-8s %-10s %s" % ("ok" if r["ok"] else "PROBLEM", r["check"], r["detail"]))
    return ok


SCHEMAS = {
    "edl": {
        "_doc": "resolve-editor/edl@1: integer frames, half open [in, out). rec_* from the timeline start, src_* in media "
                "frames (audio-only media at the timeline rate). Track order bottom to top. Written by assemble. An item "
                "made with frame_x keeps it next to the pan it became; a dialogue item whose edge could not be placed "
                "between two words carries edge_tol {in, out: seconds, why: abut or rate} (check reports rate_edge); "
                "checks_off keeps the cut list's whole request and check honours only the allowed ids. Effects "
                "(fx_catalog.json; assemble writes them): fx_meta {genre, premium, appetite, catalog}; a clip item's fx "
                "[{id, kind, f [first, last] item frames, event_f (the peak or end, where grabs look), anchor_f (the word, "
                "beat or time it was anchored to: a punch's sound lands there), params, family, on_beat, tolerance, "
                "preview, clipped}] "
                "is the record checks and the report read; motion {point [x, y] frame fractions from the top left, "
                "edges mirror|wrap|null, motion_blur, keys {zoom, x_px, y_px, angle}} (zoom multiplies the static "
                "zoom about point; x_px right and y_px down in timeline pixels; angle degrees counter-clockwise), "
                "accents {flash 0 to 1, rgb_px, leak, glitch} and retime {kind, keys [[item frame, media frame]], "
                "src_span} (the item stays at speed 1; retime_process nearest|speed_warp) are what the preview and the "
                "build render: keys are [frame, value], LINEAR between keys and constant outside, from minus the frames "
                "seen under an incoming transition to the length plus those under the outgoing one (a whip pair keys one "
                "frame further on its cut side, so its motion blur moves on every frame). A catalogue "
                "transition carries params, resolve {type, category, build, macro_inputs}, preview {recipe, class}, "
                "tolerance, audio plus3|zero|none and event_f; build clip_pair (whip_pair, custom_zoom_through) has no "
                "transition item: its keys are in the two items' motion. A title or caption may carry anim {id, "
                "word_frames, words, keyword, params} (fx_lab.text_plan turns it into Text+ keys). Sound effects sit "
                "on audio tracks with role sfx, each item with for (its event), event_f and peak_f.",
        "schema": EDL_SCHEMA,
        "version": {"id": "v003", "parent": "v002", "by": "finisher", "created": "2026-01-01T12:00:00",
                    "summary": "hook tightened to 1.9 s", "changes": ["s01 trimmed tail -3 f"], "from_cutlist": "wf/cut_A1/cutlist.json"},
        "preset": "reels_talking_head", "platform": "reels",
        "timeline": {"name": "Studio reel", "fps": "25/1", "drop_frame": False, "width": 1080, "height": 1920,
                     "start_tc": "01:00:00:00", "audio_rate": 48000,
                     "resolve": {"frame_rate_mismatch": "resolve", "input_sizing": "scaleToCrop", "retime": "nearest"}},
        "media": {"CAM_A_001": {"kind": "av", "path": "/abs/CAM_A_001.MP4", "hash": "3f1c0e9a1b2c4d5e", "fps": "50/1",
                                "fps_from": "media", "frames": 840, "start_tc": "10:42:11:05", "width": 3840, "height": 2160,
                                "audio": {"channels": 2, "rate": 48000, "offset_s": 0.0},
                                "proxy": "media/proxies/CAM_A_001.mp4", "wav": "media/proxies/CAM_A_001.wav",
                                "words": "media/analysis/CAM_A_001.words.json", "mpi_uid": None},
                  "SONG": {"kind": "audio", "path": "/abs/song.wav", "hash": "0a1b2c3d4e5f6a7b", "fps": "25/1",
                           "fps_from": "timeline", "frames": 1500, "wav": "media/proxies/SONG.wav"},
                  "CAM_B_004": {"kind": "video", "path": "/abs/CAM_B_004.MP4", "hash": "5d6e7f8091a2b3c4", "fps": "50/1",
                                "fps_from": "media", "frames": 600, "width": 3840, "height": 2160,
                                "proxy": "media/proxies/CAM_B_004.mp4", "mpi_uid": None},
                  "WHOOSH_LOW": {"kind": "audio", "path": "/abs/sfx/whoosh_low.wav", "hash": "9a8b7c6d5e4f3a2b",
                                 "fps": "25/1", "fps_from": "timeline", "frames": 15,
                                 "wav": "media/proxies/WHOOSH_LOW.wav", "sfx": {"peak_s": 0.28}}},
        "tracks": {
            "video": [{"id": "V1", "name": "Picture", "items": [
                {"id": "s01", "kind": "clip", "media": "CAM_A_001", "src_in": 312, "src_out": 348, "rec_in": 0,
                 "rec_out": 18, "speed": 1.0, "enabled": True, "link": "g01",
                 "transform": {"zoom": 1.0, "pan_px": 0, "tilt_px": 0, "rotation": 0.0, "opacity": 100},
                 "fade_in": 0, "fade_out": 0, "why": "hook line", "tags": ["hook"]},
                {"id": "s02", "kind": "clip", "media": "CAM_B_004", "src_in": 120, "src_out": 160, "rec_in": 18,
                 "rec_out": 38, "speed": 1.0, "enabled": True,
                 "transform": {"zoom": 1.0, "pan_px": 0, "tilt_px": 0, "rotation": 0.0, "opacity": 100},
                 "fade_in": 0, "fade_out": 0, "why": "the drop", "tags": [],
                 "fx": [{"id": "s02.fx1", "kind": "bump", "f": [5, 14], "event_f": 6, "anchor_f": 6,
                         "params": {"peak": 1.08},
                         "family": "zoom", "on_beat": 8, "tolerance": "push_zoom_keyed", "preview": "exact"}],
                 "motion": {"point": [0.5, 0.35], "edges": None, "motion_blur": None,
                            "keys": {"zoom": [[-4, 1.0], [4, 1.0], [5, 1.0748], [6, 1.08], [7, 1.0759],
                                              [14, 1.0]]}},
                 "accents": {"flash": [[-1, 0.0], [0, 0.8], [1, 0.3556], [2, 0.0889], [3, 0.0]], "rgb_px": [],
                             "leak": None},
                 "retime": {"kind": "ramp", "keys": [[-4, 112], [0, 120], [8, 168], [16, 184], [19, 187]],
                            "src_span": [112, 187]}, "retime_process": "nearest"}]},
                {"id": "V3", "name": "Titles", "items": [
                    {"id": "t01", "kind": "title", "text": "Three rules for clean lines", "style": "hook_top",
                     "rec_in": 0, "rec_out": 60, "box": [140, 300, 800, 180], "why": "hook text", "role": "hook",
                     "lines": ["Three rules for clean lines"], "font_px": 72.0,
                     "look": {"id": "boxed", "font": "Arial", "style": "Bold", "font_file": None,
                              "case": "as_written", "color": "#FFFFFF", "stroke": {"color": "#000000", "em": 0.11},
                              "shadow": None, "box": {"color": "#000000", "opacity": 0.6, "pad_em": [0.4, 0.2],
                                                      "round": 0.0}, "line_spacing": 1.25, "align": "center"}}]}],
            "audio": [{"id": "A1", "name": "Dialogue", "role": "dialogue", "items": [
                {"id": "s01a", "kind": "clip", "media": "CAM_A_001", "src_in": 312, "src_out": 348, "rec_in": 0,
                 "rec_out": 18, "speed": 1.0, "gain_db": 0.0, "pan": 0, "fade_in": 1, "fade_out": 1, "link": "g01"}]},
                {"id": "A2", "name": "Music", "role": "music", "items": [
                    {"id": "m01", "kind": "clip", "media": "SONG", "src_in": 0, "src_out": 1125, "rec_in": 0,
                     "rec_out": 1125, "speed": 1.0, "gain_db": -6.0, "fade_in": 0, "fade_out": 25,
                     "volume_env": [[0, 0.0], [219, 0.0], [225, -10.0], [372, -10.0], [392, 0.0]],
                     "duck": {"mode": "auto", "depth_db": 10.0, "hold_s": 2.5, "max_db": 10.0, "base_shift_db": 2.3}}]},
                {"id": "A4", "name": "SFX", "role": "sfx", "items": [
                    {"id": "sfx_x1", "kind": "clip", "media": "WHOOSH_LOW", "src_in": 0, "src_out": 15, "rec_in": 179,
                     "rec_out": 194, "speed": 1.0, "gain_db": -8.0, "pan": 0, "fade_in": 0, "fade_out": 0,
                     "for": "x002", "event_f": 186, "peak_f": 186}]}],
            "subtitle": [{"id": "ST1", "items": [{"id": "c001", "rec_in": 6, "rec_out": 28, "text": "Three rules",
                                                  "speaker": "S1", "box": [193, 960, 694, 90],
                                                  "src_words": [["CAM_A_001", 12], ["CAM_A_001", 13]],
                                                  "anim": {"id": "pop_highlight", "word_frames": [0, 9],
                                                           "words": ["Three", "rules"], "keyword": None,
                                                           "params": {"scale_ms": [[0, 0.7], [80, 1.1], [160, 0.97],
                                                                                   [240, 1.0]],
                                                                      "active": {"color": "#FFD93D"}}}}]}]},
        "transitions": [{"id": "x001", "track": "V1", "from": "s03", "to": "s04", "type": "cross_dissolve", "frames": 12,
                         "alignment": "center"},
                        {"id": "x002", "track": "V1", "from": "s05", "to": "s06", "type": "custom_whip", "frames": 8,
                         "alignment": "center", "params": {"asked": "whip", "direction": "left", "quality": 8,
                                                           "shutter": 360},
                         "resolve": {"type": "Cross Dissolve", "category": "fusion", "build": "rebuild_whip",
                                     "macro_inputs": {}, "handles": "measured"},
                         "preview": {"recipe": "whip_r1", "class": "exact"}, "tolerance": "transition_exact",
                         "audio": "none", "event_f": 186}],
        "fx_meta": {"genre": "creator_reel", "premium": False, "appetite": "normal", "catalog": "2026-10-01"},
        "markers": [{"frame": 0, "color": "Blue", "name": "hook", "note": "first 3 s", "duration": 1}],
        "music": {"media": "SONG", "beats": "media/analysis/SONG.beats.json", "offset_frames": 0},
        "targets": {"duration_frames": 1125, "loudness_lufs": -14.0, "true_peak_db": -1.0},
        "checks_off": []},
    "cutlist": {
        "_doc": "resolve-editor/cutlist@1: agents cite word indices, shot ids and beats; `assemble` does the frame math. "
                "Each spine segment has exactly one source form: words [first, last] (+ drop), shot (+ trim fractions), "
                "in_s + dur_s, or in_f + out_f. Optional: speed, dur_s or beats (timeline length), audio "
                "dialogue|nat|none (default: the preset's; in dialogue presets a clip without speech plays as nat "
                "sound and a slowed clip as none), gain_db, zoom, frame_x (the subject's centre as a fraction of the source "
                "width, 0 to 1: assemble pans so it sits in the middle of the frame; for vertical pieces from wider "
                "footage, on segments and overlays), pan_px, tilt_px, caption_y (move this segment's "
                "captions, a fraction of the height), tags, why (required). Long J and L cuts: hold_prev_s x keeps "
                "the previous segment's picture over the first x s of this one (its voice starts under the old "
                "picture), early_s x shows this segment's picture x s early over the end of the previous one; both "
                "continue the picture exactly, so never build them from an overlay of the same shot. Word segments also take "
                "pad_ms [before, after] and pause_keep_ms [min, max] to override the preset (hold a word tail longer so "
                "the next cut lands on the beat, or keep a longer breath). audio_leads start a segment's sound early, "
                "but only through the pause before its first word. A music item with \"align\": \"end\" gets the in_s that puts "
                "the song's natural end on the programme end. targets.duration_s is an exact, frame-exact length "
                "(a STOP gate, for ads): leave it out for a soft target, the preset's length range applies anyway. "
                "Leave the timeline values null: they come from the lab (the Resolve project's frame rate). Titles take "
                "an optional y (a fraction of the height) to move them off the captions, start at at_s or at an "
                "overlay-style \"at\": {\"seg\": ..., \"offset_s\": ...}, and keep a \"\\n\" in their text as a line break. Transition types: "
                "a key of fx_catalog.json (cross_dissolve, dip_to_black, blur_dissolve, brightness_flash_fx, "
                "custom_whip, custom_zoom_overlap, custom_zoom_through, whip_pair and the P1 keys; `E explain "
                "transitions` lists them), or whip and zoom, which take the Fusion transition when both shots have "
                "handles and the clip-comp pair otherwise (the assemble report's fx_routes says which); extra keys: "
                "direction (left, right, up, down), peak, point [x, y] and mix (zoom), motion_blur (Fusion motion "
                "transitions, default on), audio (plus3, zero or none: the cross fade on the linked sound; plus3 by "
                "default under dialogue or nat sound), look_is_brief (P2 looks the brief asks for). Marker colours are Resolve's (Blue, Cyan, Green, Yellow, Red, Pink, "
                "Purple, Fuchsia, Rose, Lavender, Sky, Mint, Lemon, Sand, Cocoa, Cream). checks_off may switch off "
                "only warnings and hook_3s, duration, music_end or word_twice. Title styles: hook_top, lower_third, center, "
                "super, cta, end_card; a title's role (hook, offer, proof, brand, cta, name, other) defaults to hook "
                "for hook_top before 3 s, cta for cta and end_card, name for lower_third (lower thirds name people "
                "only), else other; a title or captions may name a look from the preset's looks. captions.text_fix "
                "{\"<media>:<word index>\": text} shows a word as that text (\"\" hides it in the captions only; the "
                "cue timing stays); fix a word only from the script, the brief or its glossary, never by guessing. "
                "captions.keep_together [\"free studio visit\"] never splits those phrases between cues; "
                "captions.suppress_under_titles drops a cue that mostly repeats a title on screen with it; "
                "captions.look names a look. An overlay's caption_y moves the captions shown over it. A word segment "
                "drops the words a script alignment tagged extra unless its \"keep\": [index] keeps one, and "
                "\"edge_ms\": {\"in\": -80, \"out\": 40} nudges its outer edges (clamped to the pause). A music item "
                "may override the preset's duck_hold_s (gaps shorter than this stay ducked; the bed comes up on its own only in "
                "a voice gap over about 3.1 s, so a value under 2.5 s changes nothing) and duck_max_db (a deeper auto "
                "duck lowers the whole bed instead). A picture-only spine segment with \"lift\": true opens the "
                "ducked music over it (the drop between two lines): full level on its first frame (a 0.2 s rise "
                "that ends there, starting no earlier than 0.05 s after the last word; lift_late when the words leave "
                "no room), a 0.15 s fall before the next word. It needs a segment of the rise plus the fall plus "
                "0.25 s, about 0.6 s or more (longer for a deep duck), else lift_refused. A word range or a drop "
                "always takes a whole spoken word: M transcript marks with + the later parts of a word whisper "
                "wrote as two (joined), and a range that ends on the first part runs over them. Effects (FUSION_PLAN "
                "s2.2): spine segments and overlays take fx [{kind, at, ...}]: kind punch, bump, snap, push, shake, "
                "flash, rgb_split, glitch or light_leak (parameters default from fx_catalog.json, in ms); at "
                "{\"word\": i} (the frame the segment's media word i starts; {\"word\": \"<media>:<index>\"} names a "
                "word of another media heard during the segment, such as the voice-over under a B-roll shot), "
                "{\"beat\": k} (programme beat k from 0), "
                "{\"beat_rel\": n} (the n-th beat inside the segment, from 0), {\"s\": t} (seconds into the segment), "
                "\"cut_in\" or \"cut_out\" (its first or last frame); a beat anchor marks the effect on_beat; push spans "
                "the segment unless at and dur_s are given; point [x, y] is the zoom point in frame fractions from the "
                "top left (one per item). retime: {\"kind\": \"ramp\", \"profile\": [{\"speed\": s, \"dur_s\": t}, ..., "
                "{\"speed\": s}], \"ease_ms\": 300, \"land\": anchor} or {\"kind\": \"freeze\", \"at\": anchor, \"hold_s\": "
                "t} on a shot, in_s or in_f segment at speed 1 (it plays no sound; it repeats source frames where it "
                "runs slower than the media rate allows); retime_process nearest or speed_warp: Speed Warp only for a "
                "constant segment speed under 1 (a ramp or freeze is refused with it: they play whole source frames "
                "at 100 %, so there is nothing to warp). A segment or overlay whose frames carry the brand's logo, "
                "product name or offer takes \"tags\": [\"logo\" | \"product\" | \"offer\"] (items of brand.json's "
                "logo and wordmark media count as logo frames without a tag): shake, flash, rgb_split and glitch on "
                "them STOP (fx_text_shake). A whip's direction is the way the PICTURE travels on screen (a camera "
                "panning right moves the picture left). titles[].anim and captions.anim name an "
                "animation of the preset's anims (pop_highlight, word_pop, keyword, clean_box, box_karaoke for captions; "
                "super_pop, slide_hook, typewriter, fade for titles; fade for both; none), else the preset's default "
                "(a premium brief turns a bouncing default into fade or clean_box); captions.keywords "
                "[\"<media>:<index>\" or the word] get the keyword style (keyword, clean_box and fade). sfx: [{id, media "
                "(added with `M add PATH --sfx ID`, or `M add --sfx ID` for a file already added; ID is its media id or path), on {\"transition\": after segment} | {\"seg\": id, \"fx\": n (from 1)} | "
                "{\"title\": id} | {\"at_s\": t}, gain_db}]: assemble puts the file's peak on the event frame: a "
                "transition's cut, a title's first frame, a punch's anchor (its word: the zoom eases on after it), a "
                "bump's or snap's peak, a shake's hit, a flash's cut frame, a push's last frame. premium "
                "(true or false; the words yes, no, true and false in any case are read too, anything else is "
                "refused) and fx_appetite (none, light, normal, bold; none also switches the preset's default text "
                "animations off) override the project's brief answers. mix {\"gain_db\": x} (top level, -20 to +12 dB) "
                "moves the whole mix by x dB, every audio item alike, to reach the preset's loudness: loudness_off names "
                "x and `E level` writes it; the true peak leaves the rest for the Deliver page.",
        "schema": "resolve-editor/cutlist@1", "id": "cut_A1", "by": "cutter A1", "preset": "reels_talking_head",
        "platform": None, "timeline": {"fps": None, "width": None, "height": None, "start_tc": None},
        "spine": [{"id": "s01", "media": "CAM_A_001", "words": [12, 31], "drop": [15, 16], "why": "hook line"},
                  {"id": "s02", "shot": "CAM_B_004.s02", "trim": [0.10, 0.60], "audio": "none", "frame_x": 0.35,
                   "why": "process detail, the hands sit left of centre"},
                  {"id": "s03", "media": "CAM_B_004", "in_s": 3.20, "dur_s": 1.60, "speed": 0.5, "why": "slow reveal"},
                  {"id": "s04", "shot": "CAM_B_007.s01", "beats": 4, "audio": "none", "lift": True,
                   "why": "drop lands on the reveal, the music comes up for it",
                   "fx": [{"kind": "bump", "at": {"beat": 8}, "peak": 1.08, "why": "downbeat of bar 3"},
                          {"kind": "shake", "at": {"beat": 16}, "amp": 0.02, "rot_deg": 0.6, "decay_ms": 320, "seed": 3,
                           "why": "the drop"}]},
                  {"id": "s05", "media": "CAM_A_001", "words": [40, 52], "why": "the claim",
                   "fx": [{"kind": "punch", "at": {"word": 41}, "zoom": 1.25, "ms": 400, "ease": "in_out_cubic",
                           "point": [0.5, 0.35], "why": "emphasis on 'free'"}]},
                  {"id": "s06", "shot": "CAM_B_011.s02", "dur_s": 2.0, "audio": "none", "why": "payoff",
                   "retime": {"kind": "ramp", "profile": [{"speed": 3.0, "dur_s": 0.5}, {"speed": 0.5}], "ease_ms": 300,
                              "land": {"beat": 24}},
                   "retime_process": "nearest",
                   "fx": [{"kind": "push", "from": 1.0, "to": 1.06, "point": [0.5, 0.5], "ease": "in_out_sine"}]}],
        "overlays": [{"id": "r01", "shot": "CAM_B_009.s03", "trim": [0.2, 0.8], "at": {"seg": "s01", "offset_s": 1.2},
                      "dur_s": 2.0, "track": 2, "caption_y": 0.30,
                      "why": "cover the jump cut while the speaker names the tool",
                      "fx": [{"kind": "push", "to": 1.04}]}],
        "music": [{"id": "m01", "media": "SONG", "in_s": 0.0, "at_s": 0.0, "until": "end", "gain_db": -6.0, "duck": "auto",
                   "fade_out_s": 1.0, "grid": True, "duck_hold_s": 2.5, "duck_max_db": 10}],
        "titles": [{"id": "t01", "text": "Three rules for clean lines", "at_s": 0.0, "dur_s": 2.4, "style": "hook_top",
                    "role": "hook", "anim": "slide_hook"},
                   {"id": "t02", "text": "Free first lesson", "at_s": 9.0, "dur_s": 2.0, "style": "super",
                    "role": "offer", "anim": "super_pop"}],
        "captions": {"from": "dialogue", "text_fix": {"CAM_A_001:14": "stencil,"}, "anim": "keyword",
                     "keywords": ["CAM_A_001:41"]},
        "transitions": [{"after": "s03", "type": "cross_dissolve", "frames": 12},
                        {"after": "s04", "type": "whip", "direction": "left", "frames": 8,
                         "why": "both shots pan right, so the picture already travels left"},
                        {"after": "s05", "type": "zoom", "frames": 10, "peak": 2.5, "point": [0.4, 0.6]},
                        {"after": "s01", "type": "brightness_flash_fx", "frames": 6, "audio": "none"}],
        "sfx": [{"id": "x1", "media": "WHOOSH_LOW", "on": {"transition": "s04"}, "gain_db": -8},
                {"id": "x2", "media": "HIT_01", "on": {"seg": "s05", "fx": 1}, "gain_db": -6}],
        "premium": False, "fx_appetite": "normal",
        "mix": {"gain_db": 0.0},
        "audio_leads": [{"seg": "s03", "lead_ms": 300}],
        "markers": [{"at_s": 0.0, "name": "hook", "note": "first 3 s", "color": "Blue"}],
        "targets": {"duration_s": 45.0}},
    "outline": {
        "_doc": "resolve-editor/outline@1: story architects write it, cutters read it.",
        "schema": "resolve-editor/outline@1", "id": "A", "angle": "hook_first", "logline": "one sentence story",
        "target_s": 45,
        "beats": [{"id": "hook", "purpose": "promise the payoff", "target_s": [0, 3],
                   "material": [{"media": "CAM_A_001", "words": [12, 31]}, {"shot": "CAM_B_004.s02"}],
                   "music": "intro", "notes": "open on the finished result"}],
        "music": {"media": "SONG", "plan": "drop at the reveal"},
        "copy": {"hook_options": ["Ink that outlives trends", "Your first piece, done right", "Learn it by hand"],
                 "titles": [{"text": "Ink that outlives trends", "role": "hook"},
                            {"text": "Small classes", "role": "proof"},
                            {"text": "Enrolment open", "role": "offer"}],
                 "offer": "Enrolment open", "cta": "Book a free studio visit", "end_card": "Ink Studio + Book a free studio visit"},
        "risks": ["weak middle"]},
    "preset": dict(copy.deepcopy(DEFAULT_PRESET), id="my_preset",
                   _doc="resolve-editor/preset@1: SKILL/presets/<id>.json or LAB/presets/<id>.json; missing keys take "
                        "the built-in defaults, which this example shows. To change one shipped preset for one lab "
                        "(a loudness target, a font size), copy SKILL/presets/<id>.json to LAB/presets/<id>.json "
                        "and edit the copy: the lab's file wins over the shipped one."),
    "checks": {
        "_doc": "resolve-editor/checks@1: RESULT is STOP if any STOP, else WARN if any WARN, else OK.",
        "schema": "resolve-editor/checks@1", "edl": "wf/cut_A1/v001.json", "result": "WARN",
        "checks": [{"id": "clipped_word", "level": "STOP", "at_frames": [310], "items": ["s05a"],
                    "msg": "s05a ends inside the word 'skin' (0.06 s cut off)", "fix": "extend s05a by 2 frames"}],
        "stats": {"frames": 1125, "shots": 32, "median_shot_s": 1.28, "cv": 0.52, "first_cut_s": 1.12,
                  "first_word_s": 0.24, "speech_music_gap_lu": 15.1}},
}


def cmd_schema(name):
    if name not in SCHEMAS:
        raise Fail("schema must be one of: %s" % ", ".join(sorted(SCHEMAS)))
    print(json.dumps(SCHEMAS[name], indent=1))


def cmd_presets():
    names = preset_names()
    if not names:
        print("no presets folder next to edit_lab.py (built-in defaults only)")
        return
    for n in names:
        try:
            d = load_json(os.path.join(PRESETS_DIR, n + ".json"))
        except Fail as e:
            print("%-22s BROKEN: %s" % (n, e))
            continue
        print("%-22s %-12s %s" % (n, d.get("platform", ""), d.get("_doc", "")))


# ---------------------------------------------------------------------------------------------------------------- CLI
def _ap(prog):
    import argparse
    return argparse.ArgumentParser(prog="edit_lab.py LAB " + prog)


def main(argv):
    if len(argv) < 2 or argv[1] in ("-h", "--help"):
        print(__doc__)
        return 0 if len(argv) >= 2 else 2
    names = [c[0] for c in COMMANDS]
    if argv[1] in NO_LAB:
        lab, cmd, rest = None, argv[1], argv[2:]
    else:
        if len(argv) < 3:
            raise Fail("usage: edit_lab.py LAB COMMAND [args]; `edit_lab.py commands` lists the commands")
        lab, cmd, rest = argv[1], argv[2], argv[3:]
    if cmd not in names:
        raise Fail("unknown command %r. Commands: %s" % (cmd, " ".join(names)))
    if cmd == "commands":
        for n, u in COMMANDS:
            print("%-14s %s" % (n, u))
        return 0
    if cmd == "doctor":
        a = _ap(cmd)
        a.add_argument("--json", action="store_true")
        cmd_doctor(a.parse_args(rest).json)
        return 0
    if cmd == "schema":
        a = _ap(cmd)
        a.add_argument("name")
        cmd_schema(a.parse_args(rest).name)
        return 0
    if cmd == "presets":
        cmd_presets()
        return 0
    if cmd == "explain":
        a = _ap(cmd)
        a.add_argument("ids", nargs="+")
        cmd_explain(a.parse_args(rest).ids)
        return 0
    L = Lab(resolve_lab_arg(lab))
    if cmd != "init" and not os.path.isdir(L.path):
        raise Fail("no lab at %s: run `edit_lab.py LAB init --name NAME` first" % posix(L.path))
    a = _ap(cmd)
    if cmd == "init":
        a.add_argument("--name", required=True)
        a.add_argument("--preset")
        a.add_argument("--fps")
        a.add_argument("--size")
        a.add_argument("--start-tc", dest="start_tc")
        a.add_argument("--premium", choices=["yes", "no"])
        a.add_argument("--fx", choices=["none", "light", "normal", "bold"])
        o = a.parse_args(rest)
        cmd_init(L, o.name, o.preset, o.fps, o.size, o.start_tc, o.premium, o.fx)
    elif cmd == "dump-script":
        a.add_argument("--bin", action="append", default=[])
        cmd_dump_script(L, a.parse_args(rest).bin)
    elif cmd == "backup-script":
        a.parse_args(rest)
        cmd_backup_script(L)
    elif cmd == "edl-from-dump":
        a.add_argument("out", nargs="?")
        cmd_edl_from_dump(L, a.parse_args(rest).out)
    elif cmd == "diff":
        a.add_argument("a")
        a.add_argument("b")
        a.add_argument("--json", action="store_true")
        o = a.parse_args(rest)
        cmd_diff(L, o.a, o.b, o.json)
    elif cmd == "assemble":
        a.add_argument("cutlist")
        a.add_argument("out")
        a.add_argument("--json", action="store_true")
        o = a.parse_args(rest)
        cmd_assemble(L, o.cutlist, o.out, o.json)
    else:
        a.add_argument("edl")
        if cmd == "validate":
            a.add_argument("--json", action="store_true")
            o = a.parse_args(rest)
            cmd_validate(L, o.edl, o.json)
        elif cmd == "adopt":
            a.add_argument("--by")
            a.add_argument("--summary")
            o = a.parse_args(rest)
            cmd_adopt(L, o.edl, o.by, o.summary)
        elif cmd == "preview":
            a.add_argument("--out")
            a.add_argument("--json", action="store_true")
            o = a.parse_args(rest)
            cmd_preview(L, o.edl, o.out, o.json)
        elif cmd == "review":
            a.add_argument("--tier", default="standard", choices=["quick", "standard", "best"])
            a.add_argument("--out")
            a.add_argument("--json", action="store_true")
            o = a.parse_args(rest)
            cmd_review(L, o.edl, o.tier, o.out, o.json)
        elif cmd == "check":
            a.add_argument("--out")
            a.add_argument("--json", action="store_true")
            o = a.parse_args(rest)
            cmd_check(L, o.edl, o.out, o.json)
        elif cmd == "frames":
            a.add_argument("times", nargs="?")
            a.add_argument("--frames")
            a.add_argument("--width", type=int, default=640)
            a.add_argument("--out")
            a.add_argument("--true-levels", action="store_true")
            o = a.parse_args(rest)
            cmd_frames(L, o.edl, o.times, o.frames, o.width, o.out, o.true_levels)
        elif cmd == "export-srt":
            a.add_argument("out")
            g = a.add_mutually_exclusive_group()
            g.add_argument("--spoken", dest="spoken", action="store_const", const=True, default=None)
            g.add_argument("--picture", dest="spoken", action="store_const", const=False)
            o = a.parse_args(rest)
            cmd_export_srt(L, o.edl, o.out, o.spoken)
        elif cmd == "export-fcpxml":
            a.add_argument("out")
            o = a.parse_args(rest)
            cmd_export_fcpxml(L, o.edl, o.out)
        elif cmd == "stem":
            a.add_argument("--track", required=True)
            a.add_argument("out")
            o = a.parse_args(rest)
            cmd_stem(L, o.edl, o.track, o.out)
        elif cmd == "items":
            a.add_argument("--json", action="store_true")
            o = a.parse_args(rest)
            cmd_items(L, o.edl, o.json)
        elif cmd == "mix":
            a.add_argument("out")
            a.add_argument("--json", action="store_true")
            o = a.parse_args(rest)
            cmd_mix(L, o.edl, o.out, o.json)
        elif cmd == "level":
            a.add_argument("out")
            a.add_argument("--json", action="store_true")
            o = a.parse_args(rest)
            cmd_level(L, o.edl, o.out, o.json)
        elif cmd == "build-script":
            a.add_argument("--name")
            a.add_argument("--chunk", type=int, default=80)
            a.add_argument("--import-missing", dest="import_missing", action="store_true")
            a.add_argument("--text", default="textplus", choices=["textplus", "markers"])
            a.add_argument("--captions", choices=["burn", "file"])
            o = a.parse_args(rest)
            cmd_build_script(L, o.edl, o.name, o.chunk, o.import_missing, o.text, o.captions)
        elif cmd == "verify-script":
            o = a.parse_args(rest)
            cmd_verify_script(L, o.edl)
        elif cmd == "verify":
            a.add_argument("--json", action="store_true")
            a.add_argument("--grabs", action="store_true")
            a.add_argument("--proof")
            o = a.parse_args(rest)
            cmd_verify(L, o.edl, o.json, o.grabs, o.proof)
        elif cmd == "grab-script":
            a.add_argument("--max", type=int, default=60)
            o = a.parse_args(rest)
            cmd_grab_script(L, o.edl, o.max)
        elif cmd == "deliver-script":
            a.add_argument("--platform")
            a.add_argument("--start", action="store_true")
            a.add_argument("--out")
            o = a.parse_args(rest)
            cmd_deliver_script(L, o.edl, o.platform, o.start, o.out)
        elif cmd == "proof-script":
            o = a.parse_args(rest)
            cmd_proof_script(L, o.edl)
    return 0


if __name__ == "__main__":
    utf8_stdio()
    try:
        sys.exit(main(sys.argv))
    except Fail as e:
        sys.stderr.write("ERROR: %s\n" % e)
        sys.exit(2)
    except KeyboardInterrupt:
        sys.stderr.write("ERROR: interrupted\n")
        sys.exit(2)
