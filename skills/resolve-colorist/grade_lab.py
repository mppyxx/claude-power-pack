#!/usr/bin/env python3
"""resolve-colorist grade lab: read, grade, bake and verify a DaVinci Resolve timeline.

Usage:  grade_lab.py LAB COMMAND [args]
        (doctor, commands, defaults, presets and find-lab work without LAB: grade_lab.py doctor)
        grade_lab.py find-lab TIMELINE_UID [--root DIR] lists the labs of a timeline (after a rename)

LAB is a working folder for one timeline. It holds timeline.json (from dump-script or init-manual), sources.json
(from probe), the frame cache, renders, LUT maps, backups and the Resolve snippets (LAB/snippets/*.py).
`grade_lab.py commands` lists every command, `grade_lab.py defaults` explains every grading parameter.

Pixel pipeline (Resolve's internal 0..1 clip values in, Rec.709 gamma 2.4 display values out):
  per clip "conversion" LUT, node 1 of each clip:
    camera decode -> camera gamut to linear Rec.709 -> per clip gains (auto balance + clip_overrides + global trims)
    -> render.exposure -> [hr: soft gamut compression] -> filmic tone curve -> [hr: hue restore] -> 2.4 encode
    (display referred SDR sources: decode -> gains -> 2.4 encode, no tone curve, identity at neutral)
  one shared "look" LUT, post-clip node of the color group:
    contrast and pivot, split tone, saturation, hue bands, skin controls, print density, hue_lum, output range
"""
import copy
import datetime
import glob
import json
import math
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import cameras  # noqa: E402  (decoders, matrices, detection, frame extraction; lives next to this file)

PRESETS_DIR = os.path.join(HERE, "presets")
NSTRIP = 5
K = 0.5            # scale of the screen composite used for previews
LUMA = np.array([0.2126, 0.7152, 0.0722])
MAX_SIDE = 2000    # every preview image stays at or under this many pixels on its long side
RAW_EXT = (".braw", ".r3d", ".ari", ".arx", ".crm", ".nev", ".dng")
# file extensions the dump reads as video clips (DUMP_BODY holds the same tuple; the apply and grab snippets
# get this one through their settings)
VIDEO_EXT = (".mp4", ".mov", ".mxf", ".mts", ".m2ts", ".avi", ".mkv", ".braw", ".r3d", ".crm", ".ari",
             ".arx", ".nev", ".mpg", ".mpeg", ".m4v", ".webm", ".dng", ".3gp", ".insv")

COMMANDS = ["commands", "defaults", "doctor", "presets", "dump-script", "probe", "check", "cache", "init-params",
            "merge", "match", "rekey", "render", "wedge", "luts", "backup-script", "apply-script", "grab-script",
            "compare", "init-manual", "social-qc", "measure", "find-lab"]
NO_LAB = ("commands", "defaults", "doctor", "presets", "find-lab")


class Fail(Exception):
    """An error the user can fix; printed without a traceback."""


class BadJson(Fail, ValueError):
    """A JSON file that does not parse (a hand edit with a missing comma, for example)."""


def load_json(fn):
    with open(fn, encoding="utf-8") as fh:
        try:
            return json.load(fh)
        except ValueError as e:
            raise BadJson("%s is not valid JSON (%s); fix the file and run the command again" % (fn, e))


def write_json(fn, obj):
    with open(fn, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=1)
        fh.write("\n")


def iso_now():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def posix(p):
    return os.path.abspath(p).replace("\\", "/")


def safe_name(s, keep_space=False):
    """File and folder names that work on macOS, Windows and Linux."""
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", str(s))
    if not keep_space:
        s = re.sub(r"\s+", "_", s)
    return s.strip(" .") or "x"


def workers():
    try:
        n = int(os.environ.get("RC_WORKERS", "0"))
    except ValueError:
        n = 0
    return n if n > 0 else max(2, (os.cpu_count() or 4) - 2)


def cache_workers():
    """Parallel ffmpeg extractions: RC_WORKERS when set, else half the cores (every ffmpeg decode is itself
    multi-threaded; measured 25 s at 7 or 10 workers against 29 s at 12 on a 14 core machine)."""
    if os.environ.get("RC_WORKERS", "").strip().isdigit() and int(os.environ["RC_WORKERS"]) > 0:
        return int(os.environ["RC_WORKERS"])
    return max(2, (os.cpu_count() or 4) // 2)


def grade_workers():
    """Threads for grading: RC_WORKERS when set, else at most 8 and about one per 2 GB of memory (each grading
    thread holds a few hundred MB of float64 frames; more than 8 threads gains little)."""
    if os.environ.get("RC_WORKERS", "").strip().isdigit() and int(os.environ["RC_WORKERS"]) > 0:
        return int(os.environ["RC_WORKERS"])
    try:
        gb = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2 ** 30
    except (ValueError, OSError, AttributeError):
        gb = 16
    return max(2, min(8, workers(), int(gb // 2)))


def pmap(fn, items, n=None):
    items = list(items)
    n = min(n or workers(), len(items))
    if n <= 1:
        return [fn(x) for x in items]
    with ThreadPoolExecutor(n) as ex:
        return list(ex.map(fn, items))


def utf8_stdio():
    """Print clip names in any script (Devanagari, CJK, emoji) even where the console or pipe uses a legacy code
    page (Windows cp1252): stdout and stderr switch to UTF-8, and anything unprintable is replaced, never fatal."""
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def warn(msg):
    sys.stderr.write("note: %s\n" % msg)


# ---------------------------------------------------------------- params
# v1 defaults, byte for byte the original ones. A params file without "schema" merges onto these (plus the legacy
# tone curve and gray-world balance) so grades made with the first version reproduce exactly.
V1_DEFAULTS = {
    "balance": {"wb_strength": 0.55, "expo_target": 0.085, "expo_strength": 0.5,
                "expo_min": 0.5, "expo_max": 1.6, "global_temp": 0.15, "global_tint": 0.0},
    "clip_overrides": {},
    "render": {"exposure": 0.685, "lum_preserve": 0.25},
    "look": {
        "warmth": 0.0, "tint": 0.0,
        "curve": "s", "contrast": 0.38, "pivot": 0.43,
        "shadow_tint": [-0.002, 0.0, 0.002], "highlight_tint": [0.0, -0.001, -0.007],
        "tint_range": [0.4, 0.6],
        "sat": 1.15, "sat_hi_reduce": 0.2, "sat_sh_reduce": 0.15,
        "hue_sat": {"red": 1.02, "orange": 1.04, "yellow": 1.1, "green": 1.04, "cyan": 1.05, "blue": 1.2, "magenta": 1.12},
        "hue_shift": {"red": 0, "orange": -2, "yellow": 0, "green": 3, "cyan": 0, "blue": 0, "magenta": 0},
        "skin_protect": 0.3, "skin_line_pull": 0.58, "skin_sat": 1.03,
        "print_density": 0.27,
        "black_lift": 0.012, "white_point": 0.965,
    },
}
V1_IMPLIED = {"render": {"tonemap": "legacy"}, "balance": {"method": "gray_world"}}

# v2 defaults: a clean commercial starting point, not a style.
DEFAULTS = {
    "schema": 2,
    "balance": {"method": "grayness", "gi_percent": 1.0, "temp_knee": 3.5, "tint_knee": 8.0, "tint_strength": 1.0,
                "clip_code": None, "expo_target": 0.10, "expo_strength": 0.5, "expo_range": [-3.0, 2.0],
                "global_temp": 0.0, "global_tint": 0.0},
    "clip_overrides": {},
    "render": {"tonemap": "hr", "exposure": 0.685, "lum_preserve": 0.25, "hue_restore": 0.8, "gamut_threshold": 0.9},
    "look": {
        "warmth": 0.0, "tint": 0.0, "curve": "s", "contrast": 0.38, "pivot": 0.475,
        "shadow_tint": [-0.002, 0.0, 0.002], "highlight_tint": [0.0, -0.001, -0.007], "tint_range": [0.4, 0.6],
        "sat": 1.15, "sat_hi_reduce": 0.2, "sat_sh_reduce": 0.15,
        "hue_sat": {"red": 1.02, "orange": 1.04, "yellow": 1.1, "green": 1.04, "cyan": 1.05, "blue": 1.2, "magenta": 1.12},
        "hue_shift": {"red": 0, "orange": -2, "yellow": 0, "green": 3, "cyan": 0, "blue": 0, "magenta": 0},
        "skin_protect": 0.3, "skin_line_pull": 0.4, "skin_sat": 1.03, "skin_target_deg": 0.0,
        "print_density": 0.27, "hue_lum": {}, "black_lift": 0.012, "white_point": 0.965,
    },
}
GRAY_WORLD_KEYS = ("wb_strength", "expo_target", "expo_min", "expo_max")

PARAM_DOC = """
Params files are JSON. A file with "schema": 2 is merged onto the v2 defaults above. A file without "schema" is a
v1 file: it is merged onto the v1 defaults (`defaults --v1`) with the legacy tone curve and gray-world balance, so a
grade made with the first version reproduces exactly. Make new files with init-params and merge (they carry
"schema": 2) and edit them by hand or with merge.

balance.method        "grayness" (v2): white balance measured on the grayest surfaces of the visible picture, plus
                      half-way auto exposure. "gray_world": the v1 method (keys wb_strength, expo_target, expo_min,
                      expo_max; in a v2 file set them together with the method, missing ones come from the v1
                      defaults, and note that expo_target means camera space full frame luma there, 0.085 in v1).
                      "none": no white balance, v2 auto exposure only (concerts, clubs, colored stage light).
balance.gi_percent    share of the visible pixels treated as gray (grayness).
balance.temp_knee     how much warm or cool mood the auto balance keeps: the automatic temperature correction is
                      softly limited to about this many units (3.5 keeps most of a candle light or golden hour cast).
balance.tint_knee     the same limit for green and magenta; large on purpose (it only protects stage or LED light).
balance.tint_strength 0..1, share of the measured tint that is corrected.
balance.clip_code     signal value counted as clipped and left out of the balance (null = the camera's own value).
balance.expo_target   target geometric mean luma of the visible picture in linear Rec.709 (grayness and none).
balance.expo_strength 0..1, how far auto exposure moves toward the target in stops (0.5 = half-way, 0 = off).
balance.expo_range    [min, max] stops of automatic exposure.
balance.global_temp / global_tint   the same trim added to every clip (house warmth). Units as clip_overrides.
clip_overrides        {clip key: {"stops": s, "temp": t, "tint": n}}: per clip trims, applied in linear Rec.709 after
                      the camera matrix, on top of the auto balance and the global trims. temp +1 is about 13 mired
                      warmer near daylight, tint + is magenta, stops is exposure. `match` adds its trims here.
                      match and rekey also write "uid" (the clip's Resolve id) into every entry, so the entry
                      follows its clip when an edit moves it to another key; keep that field. Untagged entries of a
                      clip that moved are found through "moved_keys" in timeline.json. After an edit that moved
                      clips, run rekey on the file before editing entries by hand, and after copying an entry to
                      another clip (rekey tags the copy with that clip's uid). Limits: stops within +-10, temp and
                      tint within +-20 (also balance.global_temp and global_tint).
render.tonemap        "hr" (v2): soft gamut compression into Rec.709, the filmic curve, then a hue restore so bright
                      oranges, skin and saturated lights keep their hue. "legacy": the v1 conversion, only to
                      reproduce an older grade. Any other value is an error.
render.exposure       global linear gain before the tone curve.
render.lum_preserve   0..1: 0 = per channel curve (bright colors desaturate), 1 = luminance preserving.
render.hue_restore    0..1 (hr): how much of the per channel hue skew is removed (0.8 measured best, 0 = legacy skew).
render.gamut_threshold (hr): colors closer to the Rec.709 edge than this are compressed.
                      Display referred sources (Rec.709, sRGB, P3 phone video, vendor LUT output) skip the tone
                      curve, render.exposure and auto exposure; white balance and clip_overrides still apply.
look.warmth / tint    display space temperature (+ warm) and tint (+ magenta). Prefer balance.global_temp for warmth.
look.curve            "s" (power S-curve around pivot; contrast 0.2 gentle, 0.5 punchy) or "legacy" (v1 sine curve).
look.contrast / pivot curve strength and the level it turns around (0.475 keeps mid grey where it was).
look.shadow_tint / highlight_tint   RGB offsets in shadows and highlights (split tone); tint_range = [shadow end,
                      highlight start].
look.sat              global saturation; sat_hi_reduce and sat_sh_reduce lower it in highlights and shadows.
look.hue_sat / hue_shift   per hue band saturation multipliers and hue rotation in degrees. Bands: red, orange,
                      yellow, green, cyan, blue, magenta. + turns counter-clockwise on the vectorscope (green + goes
                      toward cyan, orange - goes toward red).
look.skin_protect     0..1: bring skin chroma back to its value before the look.
look.skin_line_pull   0..1: rotate near-skin hues onto the skin line. look.skin_sat: extra saturation on skin.
look.skin_target_deg  rotates the skin line used by skin_line_pull and skin_protect: 0 suits white and Black skin,
                      +3 to +6 is natural for South and East Asian skin. Metrics keep reporting skin_hue_offset_deg
                      against the standard line.
look.print_density    0..~0.6: film-like density, saturated colors get darker and richer.
look.hue_lum          {band: multiplier}: per hue luminance (like a print stock: greens and cyans darker), faded out
                      on skin and on neutrals. {} = off.
look.black_lift / white_point   output range (filmic fade and soft top). They remap the whole range, so mid grey moves
                      too (0.475 comes out at 0.465 with the defaults).
Unknown look keys are ignored.

metrics.json (render): summary {clips, slices, max_clipped_pct, max_crushed_pct, mean_skin_offset_deg,
mean/max_slice_luma_spread and mean/max_slice_neutral_spread (over slices with two or more panels), flagged_clips}.
cast_ab_midtones (per clip and per panel) is the mean a*, b* of low-chroma mid-tones; with schema 2 params a pixel
counts only when its C*ab is under 14 both graded and before the look (so skin, wood or orange walls that a look
desaturates stay out, in every hue direction alike; pale skin, pastel clothing and light wood under 14 before the
look still count), the same neutral measure `match` uses (schema 1: under 20, graded only). `match` keeps a clip's previous trim when
the new one would cost its skin more than 10 % chroma or move its hue more than 3 deg further from
look.skin_target_deg to beyond 6 deg (the skin off limit).
Per clip flags: "strong cast kept", "mixed light", "light changes", "few gray pixels", "clipped" (1 % or more at the
white point), "crushed" (1.5 % or more at the black level), "skin off" (skin hue more than 6 deg from
look.skin_target_deg on clips with 3 % skin-hued pixels or more; the mask also catches wood, leather and
orange walls, so judge the picture), "camera approximate", "camera low confidence".
"""


def merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = merge(out[k], v)
        else:
            out[k] = v
    return out


def is_v1(P):
    return "schema" not in P or P.get("schema") in (None, 1)


def _num(v):
    try:
        return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
    except OverflowError:
        return False        # an integer too large for a float


# largest trims a params file may hold (far beyond any real grade; bigger numbers overflow the math)
OVERRIDE_LIMITS = {"stops": 10.0, "temp": 20.0, "tint": 20.0}


def _check_shapes(P):
    """Params whose shape is wrong (a number where a list belongs, a word where a number belongs) stop here with a
    message instead of a traceback deep inside the math."""
    L = P.get("look")
    if not isinstance(L, dict):
        raise Fail("look must be an object, got %r" % (L,))
    for key, n in (("tint_range", 2), ("shadow_tint", 3), ("highlight_tint", 3)):
        v = L.get(key)
        if not (isinstance(v, (list, tuple)) and len(v) == n and all(_num(x) for x in v)):
            raise Fail("look.%s must be a list of %d numbers, for example %s, got %r"
                       % (key, n, json.dumps(DEFAULTS["look"][key]), v))
    for key in ("warmth", "tint", "contrast", "pivot", "sat", "sat_hi_reduce", "sat_sh_reduce", "skin_protect",
                "skin_line_pull", "skin_sat", "print_density", "black_lift", "white_point"):
        if key in L and not _num(L[key]):
            raise Fail("look.%s must be a number, got %r" % (key, L[key]))
    if "skin_target_deg" in L and L["skin_target_deg"] is not None and not _num(L["skin_target_deg"]):
        raise Fail("look.skin_target_deg must be a number (degrees), got %r" % (L["skin_target_deg"],))
    for key in ("hue_sat", "hue_shift", "hue_lum"):
        v = L.get(key)
        if v is None and key == "hue_lum":
            continue
        if not (isinstance(v, dict) and all(_num(x) for x in v.values())):
            raise Fail("look.%s must map band names (%s) to numbers, got %r" % (key, ", ".join(BANDS), v))
    co = P.get("clip_overrides")
    if not isinstance(co, dict):
        raise Fail("clip_overrides must be an object {clip key: {\"stops\": s, \"temp\": t, \"tint\": n}}, got %r" % (co,))
    for k, o in co.items():
        if str(k).startswith("_"):
            continue
        if not isinstance(o, dict):
            raise Fail("clip_overrides.%s must be an object like {\"stops\": 0.2}, got %r" % (k, o))
        for kk in ("stops", "temp", "tint"):
            if kk in o and not _num(o[kk]):
                raise Fail("clip_overrides.%s.%s must be a number, got %r" % (k, kk, o[kk]))
            lim = OVERRIDE_LIMITS[kk]
            if kk in o and not abs(o[kk]) <= lim:
                raise Fail("clip_overrides.%s.%s is %r; it must lie between -%g and %g" % (k, kk, o[kk], lim, lim))
    for sec in ("balance", "render"):
        if not isinstance(P.get(sec), dict):
            raise Fail("%s must be an object, got %r" % (sec, P.get(sec)))
    for kk in ("global_temp", "global_tint"):
        v = P["balance"].get(kk)
        if v is not None and not (_num(v) and abs(v) <= OVERRIDE_LIMITS["temp"]):
            raise Fail("balance.%s must be a number between -%g and %g, got %r"
                       % (kk, OVERRIDE_LIMITS["temp"], OVERRIDE_LIMITS["temp"], v))


def resolve_params(raw):
    """raw params dict (as in a file) -> full params."""
    raw = raw or {}
    schema = raw.get("schema")
    if schema in (None, 1):
        P = merge(merge(V1_DEFAULTS, V1_IMPLIED), raw)
        P.pop("schema", None)
    elif schema == 2:
        P = merge(DEFAULTS, raw)
        if P["balance"].get("method") == "gray_world":
            rb = raw.get("balance") or {}
            for k in GRAY_WORLD_KEYS:
                if k not in rb:
                    P["balance"][k] = V1_DEFAULTS["balance"][k]
    else:
        raise Fail("params schema %r is newer than this grade_lab.py understands (1 or 2)" % (schema,))
    thr = P["render"].get("gamut_threshold", 0.9)
    if isinstance(thr, bool) or not (isinstance(thr, (int, float)) and 0 <= thr < 1):
        raise Fail("render.gamut_threshold must be at least 0 and below 1 (0.9 is the default; higher compresses "
                   "fewer colors), got %r" % (thr,))
    L = P["look"]
    _check_shapes(P)
    if not L.get("contrast", 0) > -1:
        raise Fail("look.contrast must be above -1, got %r" % (L.get("contrast"),))
    if not 0 < L.get("pivot", 0.5) < 1:
        raise Fail("look.pivot must be between 0 and 1, got %r" % (L.get("pivot"),))
    if P["render"].get("tonemap", "legacy") not in ("hr", "legacy"):
        raise Fail("render.tonemap must be \"hr\" or \"legacy\", got %r" % (P["render"].get("tonemap"),))
    if P["balance"].get("method", "gray_world") not in ("grayness", "gray_world", "none"):
        raise Fail("balance.method must be \"grayness\", \"gray_world\" or \"none\", got %r" % (P["balance"].get("method"),))
    return P


def params(pfile):
    if not pfile:
        return resolve_params({"schema": 2})
    if not os.path.exists(pfile):
        raise Fail("params file not found: %s" % pfile)
    return resolve_params(load_json(pfile))


def preset_names():
    if not os.path.isdir(PRESETS_DIR):
        return []
    return sorted(f[:-5] for f in os.listdir(PRESETS_DIR) if f.endswith(".json"))


def strip_private(d):
    if not isinstance(d, dict):
        return d
    return {k: strip_private(v) for k, v in d.items() if not str(k).startswith("_")}


def load_overlay(name):
    """a params file path or a preset name -> dict without the '_' keys."""
    if os.path.exists(name):
        d = load_json(name)
    else:
        fn = os.path.join(PRESETS_DIR, name + ".json") if not name.endswith(".json") else os.path.join(PRESETS_DIR, name)
        if not os.path.exists(fn):
            raise Fail("%r is neither a params file nor a preset (presets: %s)" % (name, ", ".join(preset_names()) or "none"))
        d = load_json(fn)
    req = d.get("_requires") or []
    missing = [r for r in req if r not in ("look.hue_lum", "look.skin_target_deg")]
    if missing:
        warn("%s asks for %s, which this grade_lab.py does not have; those keys are ignored" % (name, ", ".join(missing)))
    return strip_private(d)


# ---------------------------------------------------------------- cameras
# Frozen copy of the v1 S-Log3 / S-Gamut3.Cine math, used ONLY for v1 (schema-less) params: v1 computed the curve in
# float32 on cached frames and used this exact matrix, and the camera table computes in float64 with a matrix built
# from the primaries (differences around 2e-6). Keeping it makes old grades reproduce bit for bit.
def _v1_slog3_to_lin(x):
    c = x * 1023.0
    return np.where(c >= 171.2102946929, 10 ** ((c - 420.0) / 261.5) * 0.19 - 0.01,
                    (c - 95.0) * 0.01125 / (171.2102946929 - 95.0))


V1_CAMERAS = {
    "slog3_sg3c": (_v1_slog3_to_lin, np.array([[1.6269474, -0.5401385, -0.0868089],
                                               [-0.1785155, 1.4179409, -0.2394254],
                                               [-0.0444361, -0.1959199, 1.2403560]])),
}
DEFAULT_CAMERA = "slog3_sg3c"
_CAM_CACHE = {}


def camera_entry(key):
    try:
        return cameras.get_camera(key)
    except KeyError as e:
        raise Fail(e.args[0] if e.args else "unknown camera %r" % key)


class Cam:
    def __init__(self, key, v1):
        e = camera_entry(key)
        self.key, self.entry = e.get("key", key), e
        self.kind, self.status = e.get("kind", "log"), e.get("status", "official")
        self.clip_code = float(e.get("clip_code") or 0.92)
        if v1 and self.key in V1_CAMERAS:
            self.decode, self.matrix = V1_CAMERAS[self.key]
        else:
            self.decode, self.matrix = e["decode"], np.asarray(e["matrix"], float)
        self.lim = gamut_limits(self.matrix)


def cam_for(key, P):
    v1 = is_v1(P)
    ck = (key, v1)
    if ck not in _CAM_CACHE:
        _CAM_CACHE[ck] = Cam(key, v1)
    return _CAM_CACHE[ck]


# ---------------------------------------------------------------- timeline
def default_lut_folder(lab, d):
    """The LUT subfolder (and the start of the color group's name) when timeline.json sets none: the project name,
    folded to ASCII when it has other characters (plain names are safe in SetLUT on every system), or "Grade". A lab
    that baked LUTs before this rule keeps the project name as it is, so its earlier grade is still recognised.
    luts records the name it used in timeline.json."""
    import unicodedata
    name = str(d.get("project") or "").strip()
    if not name:
        return "Grade"
    if name.isascii() or glob.glob(os.path.join(glob.escape(lab), "luts_*.json")):
        return name
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    folded = re.sub(r"\s+", " ", folded).strip(" ._-")
    return folded if re.search(r"[A-Za-z0-9]", folded) else "Grade"


class TL:
    def __init__(self, lab, quiet=False):
        self.lab = os.path.abspath(lab)
        fn = os.path.join(self.lab, "timeline.json")
        if not os.path.exists(fn):
            raise Fail("no timeline.json in %s: run dump-script (Resolve Studio) or init-manual (free Resolve) first" % self.lab)
        d = load_json(fn)
        self.d = d
        self.schema = d.get("schema", 1)
        self.W, self.H = d["screen"]
        self.fps = d["fps"]
        self.base_mode = d.get("base_scale", "fill")
        self.lut_folder = d.get("lut_folder") or default_lut_folder(self.lab, d)
        self.items = sorted(d["items"], key=lambda i: (i["track"], i["start"]))
        self.sources_path = os.path.join(self.lab, "sources.json")
        self.sources = {}
        if os.path.exists(self.sources_path):
            self.sources = load_json(self.sources_path).get("sources", {})
        # old clip key -> current clip key for clips an edit moved (written by the dump, see resolve_overrides)
        self.moved = {k: v for k, v in (d.get("moved_keys") or {}).items() if isinstance(k, str) and isinstance(v, str)}
        # camera_overrides live in this file, and the dump moves their keys along with their clips, so they are
        # always keyed for the current layout: by clip key or by uid
        overrides = d.get("camera_overrides") or {}
        defaulted = []
        for it in self.items:
            src = self.sources.get(it.get("path"), {})
            det, pr = src.get("detect") or {}, src.get("probe") or {}
            ov = overrides.get(it["key"]) or (overrides.get(it.get("uid")) if it.get("uid") else None)
            if ov:
                cam, how = ov, "override"
            elif it.get("camera"):
                cam, how = it["camera"], "item"
            elif d.get("camera"):
                cam, how = d["camera"], "timeline"
            elif det.get("key"):
                cam, how = det["key"], "detected"
            else:
                cam, how = DEFAULT_CAMERA, "default"
                defaulted.append(it["key"])
            it["camera"], it["camera_from"] = cam, how
            it["levels"] = cameras.resolve_levels(it.get("data_level"), pr.get("color_range"))
            it["yuv_matrix"] = det.get("yuv_matrix") or "bt709"
            it["vertical"] = it["src_h"] > it["src_w"]
        if defaulted and not quiet:
            warn("%d clip(s) have no camera set and no detection (run probe or check): using %s as in v1"
                 % (len(defaulted), DEFAULT_CAMERA))
        self.by_key = {i["key"]: i for i in self.items}
        # identifies the clips and their moves, for the memos of resolve_overrides and gains_all
        self.ovsig = json.dumps([[i["key"], i.get("uid"), i["camera"]] for i in self.items] + [sorted(self.moved.items())])
        b = sorted({i["start"] for i in self.items} | {i["end"] for i in self.items})
        self.slices = [(a + c) // 2 for a, c in zip(b, b[1:]) if self.active((a + c) // 2)]
        self.cache = os.path.join(self.lab, "cache")

    def active(self, t):
        return [i for i in self.items if i["start"] <= t < i["end"]]

    def short(self, key):
        n = os.path.splitext(self.by_key[key]["name"])[0]
        return n if len(n) <= 14 else n[:5] + ".." + n[-6:]

    def detect_for(self, it):
        return (self.sources.get(it.get("path")) or {}).get("detect") or {}

    def probe_for(self, it):
        return (self.sources.get(it.get("path")) or {}).get("probe") or None

    def base(self, it, mode=None):
        sw, sh = it["src_w"], it["src_h"]
        mode = mode or self.base_mode
        if mode == "fit":
            s = min(self.W / sw, self.H / sh)
        elif mode == "none":
            s = 1.0
        else:  # fill (observed for landscape sources on a 9:16 timeline in Resolve 21)
            s = max(self.W / sw, self.H / sh)
        return s

    def rect(self, it, k=1.0, mode=None):
        s = self.base(it, mode)
        dw, dh = it["src_w"] * s * abs(it["zoom_x"]) * k, it["src_h"] * s * abs(it["zoom_y"]) * k
        cx, cy = (self.W / 2 + it["pan"]) * k, (self.H / 2 - it["tilt"]) * k
        return cx - dw / 2, cy - dh / 2, dw, dh

    def src_frame(self, it, t):
        return it["src_start"] + (t - it["start"]) * (it["src_end"] - it["src_start"]) / max(1, it["end"] - it["start"])

    def visible_box(self, it, t=None, mode=None):
        """normalized (u0,v0,u1,v1) of the item's source that is actually visible on screen at time t."""
        if t is None:
            t = (it["start"] + it["end"]) // 2
        g = 8
        gw, gh = self.W // g, self.H // g
        yy, xx = np.mgrid[0:gh, 0:gw]
        xs, ys = (xx + 0.5) * g, (yy + 0.5) * g

        def inside(r):
            x0, y0, w, h = r
            return (xs >= x0) & (xs < x0 + w) & (ys >= y0) & (ys < y0 + h)
        m = inside(self.rect(it, mode=mode))
        for o in self.active(t):
            if o["track"] > it["track"] and o["opacity"] >= 99:
                m &= ~inside(self.rect(o, mode=mode))
        if not m.any():
            m = inside(self.rect(it, mode=mode))
        if not m.any():
            return (0, 0, 1, 1)
        x0, y0, w, h = self.rect(it, mode=mode)
        ys_, xs_ = ys[m], xs[m]
        u0, u1 = (xs_.min() - g / 2 - x0) / w, (xs_.max() + g / 2 - x0) / w
        v0, v1 = (ys_.min() - g / 2 - y0) / h, (ys_.max() + g / 2 - y0) / h
        return tuple(float(np.clip(v, 0, 1)) for v in (u0, v0, u1, v1))

    def cache_size(self, it):
        x0, y0, w, h = self.rect(it, K)
        long_side = max(640, int(max(w, h)))
        if it["src_w"] >= it["src_h"]:
            cw = long_side
            ch = int(round(cw * it["src_h"] / it["src_w"]))
        else:
            ch = long_side
            cw = int(round(ch * it["src_w"] / it["src_h"]))
        return cw + cw % 2, ch + ch % 2

    def crop_aspect(self, it):
        cw, ch = self.cache_size(it)
        u0, v0, u1, v1 = self.visible_box(it)
        return max(1e-3, (u1 - u0) * cw) / max(1e-3, (v1 - v0) * ch)


# ---------------------------------------------------------------- probe and cache
def cmd_probe(T, stream=None):
    """stream: where the table goes (stdout by default; check sends it to stderr so RESULT stays first)."""
    out = stream or sys.stdout
    paths = []
    for it in T.items:
        if it["path"] not in paths:
            paths.append(it["path"])

    def one(p):
        base = {"key": None, "confidence": "low", "status": "unknown", "kind": None, "color_range": "unknown",
                "yuv_matrix": "bt709", "make": None, "model": None}
        if not p or not os.path.exists(p):
            return p, {"probe": None, "detect": dict(base, evidence=["file not found"],
                                                     note="source file not found: relink or copy the media, then probe again")}
        try:
            pr = cameras.probe(p)
            return p, {"probe": pr, "detect": cameras.detect(p, pr)}
        except Exception as e:  # never stop the whole probe for one file
            return p, {"probe": None, "detect": dict(base, evidence=["probe failed"], note="probe failed: %s" % e)}
    res = dict(pmap(one, paths))
    write_json(T.sources_path, {"schema": 1, "created": iso_now(), "sources": res})
    keys = {}
    for it in T.items:
        keys.setdefault(it["path"], []).append(it["key"])
    print("%-24s %-16s %-6s %-11s %-5s %-7s %s" % ("clips", "camera", "conf", "status", "range", "matrix", "file"), file=out)
    notes = []
    for p in paths:
        de = res[p]["detect"]
        ks = ",".join(keys[p])
        print("%-24s %-16s %-6s %-11s %-5s %-7s %s" % (ks if len(ks) <= 24 else ks[:21] + "...", de.get("key") or "-",
                                                       de.get("confidence"), de.get("status"), de.get("color_range"),
                                                       de.get("yuv_matrix"), os.path.basename(p or "")), file=out)
        if de.get("note"):
            notes.append("%s: %s" % (os.path.basename(p or "?"), de["note"]))
    for n in notes:
        print("  note", n, file=out)
    print("wrote %s (%d sources)" % (T.sources_path, len(paths)), file=out)


def cache_decoder(T):
    meta = os.path.join(T.cache, "meta.json")
    if os.path.exists(meta):
        m = load_json(meta)
        return m.get("decoder", "swscale-1"), m
    if os.path.isdir(T.cache) and any(f.endswith(".npy") for f in os.listdir(T.cache)):
        return "swscale-1", {"decoder": "swscale-1", "chroma_zero": "center"}
    return None, None


def cache_jobs(T):
    jobs = []
    for it in T.items:
        w, h = T.cache_size(it)
        for i in range(NSTRIP):
            f = it["src_start"] + (it["src_end"] - it["src_start"]) * (i + 0.5) / NSTRIP
            jobs.append(("strip_%s_%d" % (it["key"], i), it, f / it["fps"], w, h))
    for si, t in enumerate(T.slices):
        for it in T.active(t):
            w, h = T.cache_size(it)
            jobs.append(("slice_%d_%s" % (si, it["key"]), it, T.src_frame(it, t) / it["fps"], w, h))
    return jobs


def cmd_cache(T):
    t0 = time.time()
    if not os.path.exists(T.sources_path):
        print("no sources.json yet: probing the source files first")
        cmd_probe(T)
        T = TL(T.lab)
    os.makedirs(T.cache, exist_ok=True)
    dec, meta = cache_decoder(T)
    if dec is None:
        dec = cameras.DECODER_VERSION
        meta = {"decoder": dec, "chroma_zero": os.environ.get("RC_CHROMA_ZERO") or "resolve", "created": iso_now()}
        write_json(os.path.join(T.cache, "meta.json"), meta)
    elif dec not in ("swscale-1", cameras.DECODER_VERSION):
        raise Fail("the cache in %s was made by decoder %r, which this version cannot add frames to. Delete that "
                   "folder and run cache again." % (T.cache, dec))
    cz = meta.get("chroma_zero")
    try:
        cz = float(cz)
    except (TypeError, ValueError):
        pass
    jobs = cache_jobs(T)
    # cache/index.json records what each cached frame shows (source file, time, size, decode settings), so frames
    # of a clip that was replaced at the same position, slipped or relinked are extracted again. A cache made
    # before the index existed is trusted as it is (its index is written from the current jobs).
    idx_fn = os.path.join(T.cache, "index.json")
    have = {j[0] for j in jobs if os.path.exists(os.path.join(T.cache, j[0] + ".npy"))}
    if os.path.exists(idx_fn):
        index = _read_index(idx_fn)
    else:
        index = {j[0]: job_sig(j) for j in jobs if j[0] in have}
    todo = [j for j in jobs if j[0] not in have or index.get(j[0]) != job_sig(j)]
    stale = sum(1 for j in todo if j[0] in have)
    bad = {}
    for _, it, _, _, _ in todo:
        pr = T.probe_for(it) or {}
        if not os.path.exists(it["path"]):
            bad[it["key"]] = "source file not found: %s" % it["path"]
        elif pr.get("is_raw") or it["path"].lower().endswith(RAW_EXT):
            bad[it["key"]] = "RAW is decoded by Resolve; the lab cannot read %s" % os.path.basename(it["path"])
    todo = [j for j in todo if j[1]["key"] not in bad]

    def run(j):
        name, it, sec, w, h = j
        out = os.path.join(T.cache, name + ".npy")
        try:
            if dec == "swscale-1":
                arr = cameras.extract_frame_swscale(it["path"], sec, w, h)
            else:
                arr = cameras.extract_frame(it["path"], sec, w, h, data_level=it["levels"], yuv_matrix=it["yuv_matrix"],
                                            chroma_zero=cz, probe_info=T.probe_for(it))
            part = out[:-4] + ".part.npy"
            np.save(part, np.asarray(arr, np.float32))
            os.replace(part, out)
            return None
        except Exception as e:
            return it["key"], "%s: %s" % (name, e)
    results = pmap(run, todo, cache_workers())
    for j, r in zip(todo, results):
        if r:
            bad.setdefault(r[0], r[1])
            index.pop(j[0], None)
        else:
            index[j[0]] = job_sig(j)
    write_json(idx_fn, index)
    print("cached %d frames (%d new%s) for %d items, %d slices -> %s  [%.1f s, decoder %s, %d workers]" % (
        len(jobs) - sum(1 for j in jobs if j[1]["key"] in bad), len(todo),
        ", %d of them replaced because the clip changed" % stale if stale else "", len(T.items), len(T.slices),
        T.cache, time.time() - t0, dec, cache_workers()))
    if bad:
        for k, msg in bad.items():
            print("ERROR", k, msg)
        raise Fail("%d clip(s) could not be cached (see above)" % len(bad))


def job_sig(job):
    """What a cached frame shows: [source path, seconds, width, height, data levels, YCbCr matrix]."""
    name, it, sec, w, h = job
    return [it.get("path"), round(float(sec), 3), int(w), int(h), it.get("levels"), it.get("yuv_matrix")]


def _read_index(fn):
    try:
        d = load_json(fn)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _cache_state(T):
    """(index.json of the cache or None, {frame name: signature of the current timeline}), read once per TL."""
    st = getattr(T, "_cache_state", None)
    if st is None:
        fn = os.path.join(T.cache, "index.json")
        idx = _read_index(fn) if os.path.exists(fn) else None
        st = (idx, {j[0]: job_sig(j) for j in cache_jobs(T)} if idx is not None else {})
        T._cache_state = st
    return st


def load(T, name):
    fn = os.path.join(T.cache, name + ".npy")
    if not os.path.exists(fn):
        raise Fail("frame %s is not cached: run cache first" % name)
    idx, want = _cache_state(T)
    if idx is not None and name in want and idx.get(name) != want[name]:
        raise Fail("frame %s belongs to an older version of the timeline (the clip was replaced, slipped or "
                   "relinked since it was cached): run cache" % name)
    return np.load(fn)


# ---------------------------------------------------------------- color math
def aces_fit(x):
    x = np.maximum(x, 0)
    a, b, c, d, e = 2.51, 0.03, 2.43, 0.59, 0.14
    return np.clip(x * (a * x + b) / (x * (c * x + d) + e), 0, 1)


def smooth(e0, e1, x):
    if e1 == e0:
        return (np.asarray(x) >= e0).astype(float)
    t = np.clip((x - e0) / (e1 - e0), 0, 1)
    return t * t * (3 - 2 * t)


def gamut_limits(M):
    """How far outside Rec.709 a camera gamut reaches: distance from the achromatic axis, (max - c) / max, of its
    primaries and secondaries after the camera to Rec.709 matrix, per channel, plus 4 % margin (floor 1.1)."""
    c = np.array([[1, 0, 0], [0, 1, 0], [0, 0, 1], [0, 1, 1], [1, 0, 1], [1, 1, 0]], float) @ np.asarray(M, float).T
    a = c.max(-1, keepdims=True)
    return np.maximum(((a - c) / np.abs(a)).max(0) * 1.04, 1.1)


def gamut_compress(lin, lim, thr=0.9, pwr=1.2):
    """Soft gamut compression into Rec.709 before the tone curve (the ACES reference gamut compression curve,
    applied hue preserving: the largest distance from the achromatic axis is compressed and all three channels are
    scaled by the same factor). Distances below thr are untouched; lim lands on the gamut edge."""
    lim = np.asarray(lim, float)
    ach = lin.max(-1, keepdims=True)
    aa = np.maximum(np.abs(ach), 1e-10)
    d = np.where(np.abs(ach) > 1e-10, (ach - lin) / aa, 0.0)
    dm = d.max(-1, keepdims=True)
    w = np.maximum(d, 0) ** 4
    ws = w.sum(-1, keepdims=True)
    L = np.where(ws > 1e-10, (w * lim).sum(-1, keepdims=True) / np.maximum(ws, 1e-10), lim.mean())
    s = (L - thr) / (((1 - thr) / (L - thr)) ** -pwr - 1) ** (1 / pwr)
    over = np.maximum(dm - thr, 0)
    dmc = np.where(dm < thr, dm, thr + over / (1 + (over / s) ** pwr) ** (1 / pwr))
    return ach - d * np.where(dm > 1e-10, dmc / np.maximum(dm, 1e-10), 1.0) * np.abs(ach)


def hue_restore(src, dst, amount):
    """Per channel curves skew hues toward yellow, cyan and magenta. Keep dst's brightest and darkest channel and
    move its middle channel so that (mid - min) / (max - min) returns toward src's value (that ratio fixes the hue)."""
    o = np.argsort(src, axis=-1, kind="stable")
    s, d = np.take_along_axis(src, o, -1), np.take_along_axis(dst, o, -1).copy()
    den = s[..., 2] - s[..., 0]
    t = np.where(den > 1e-10, (s[..., 1] - s[..., 0]) / np.maximum(den, 1e-10), 0.0)
    d[..., 1] += amount * (d[..., 0] + t * (d[..., 2] - d[..., 0]) - d[..., 1])
    out = np.empty_like(dst)
    np.put_along_axis(out, o, d, -1)
    return out


def _tone_tail(lin, r, tm):
    Y = lin @ LUMA
    pc = aces_fit(lin)
    lp = np.clip(lin * (aces_fit(Y) / np.maximum(Y, 1e-6))[..., None], 0, 1)
    w = r["lum_preserve"]
    x = np.clip((1 - w) * pc + w * lp, 0, 1)
    if tm == "hr":
        x = np.clip(hue_restore(lin, x, r.get("hue_restore", 0.8)), 0, 1)
    return x ** (1 / 2.4)


def convert(rgb, gains, P, cam=DEFAULT_CAMERA):
    """Resolve internal clip values -> display Rec.709 (the conversion LUT)."""
    c = cam_for(cam, P)
    r = P["render"]
    tm = r.get("tonemap", "legacy")
    if tm not in ("hr", "legacy"):
        raise ValueError("render.tonemap must be 'hr' or 'legacy', got %r" % tm)
    if c.kind == "sdr":
        lin = (c.decode(rgb) @ c.matrix.T) * gains
        return np.clip(lin, 0, 1) ** (1 / 2.4)
    if tm == "legacy":
        lin = np.maximum(c.decode(rgb) @ c.matrix.T, 0) * gains * r["exposure"]
    else:
        lin = (c.decode(rgb) @ c.matrix.T) * gains * r["exposure"]
        lin = np.maximum(gamut_compress(lin, c.lim, r.get("gamut_threshold", 0.9)), 0)
    return _tone_tail(lin, r, tm)


def to_ycc(rgb):
    Y = rgb @ LUMA
    return Y, (rgb[..., 2] - Y) / 1.8556, (rgb[..., 0] - Y) / 1.5748


def from_ycc(Y, cb, cr):
    r = Y + 1.5748 * cr
    b = Y + 1.8556 * cb
    g = (Y - 0.2126 * r - 0.0722 * b) / 0.7152
    return np.stack([r, g, b], -1)


def _ang(rgb):
    _, cb, cr = to_ycc(np.array(rgb, float))
    return np.degrees(np.arctan2(cr, cb)) % 360


BANDS = {
    "red": (_ang([1, 0, 0]), 16), "orange": (_ang([1, 0.5, 0]), 12), "yellow": (_ang([1, 1, 0]), 14),
    "green": (_ang([0, 1, 0]), 30), "cyan": (_ang([0, 1, 1]), 25), "blue": (_ang([0, 0, 1]), 28),
    "magenta": (_ang([1, 0, 1]), 28),
}
SKIN_ANG = _ang([0.80, 0.56, 0.44])


def angdiff(a, b):
    return (a - b + 180) % 360 - 180


def look(rgb, P):
    L = P["look"]
    tgt = L.get("skin_target_deg") or 0.0
    sk = SKIN_ANG + tgt if tgt else SKIN_ANG
    x = rgb * np.array([1 + 0.06 * L["warmth"], 1 - 0.04 * L["tint"], 1 - 0.06 * L["warmth"]])
    x = np.clip(x, 0, 1)
    c, p = L["contrast"], L["pivot"]
    if L["curve"] == "legacy":
        x = np.clip(x + c * np.sin((x - p) * np.pi / 0.9) * (x > 0) * (1 - np.abs(2 * x - 1) ** 3), 0, 1)
    else:
        k = 1 + c
        x = np.where(x < p, p * (np.maximum(x, 0) / p) ** k, 1 - (1 - p) * (np.maximum(1 - x, 0) / (1 - p)) ** k)
    ref = x.copy()
    Y = x @ LUMA
    sh = (1 - smooth(0.0, L["tint_range"][0], Y))[..., None]
    hi = smooth(L["tint_range"][1], 1.0, Y)[..., None]
    x = x + sh * np.array(L["shadow_tint"]) + hi * np.array(L["highlight_tint"])
    Yc, cb, cr = to_ycc(x)
    ang = np.degrees(np.arctan2(cr, cb)) % 360
    mag = np.hypot(cb, cr)
    satf = np.ones_like(mag)
    rot = np.zeros_like(mag)
    for name, (cen, sig) in BANDS.items():
        wgt = np.exp(-0.5 * (angdiff(ang, cen) / sig) ** 2)
        satf += (L["hue_sat"].get(name, 1) - 1) * wgt
        rot += L["hue_shift"].get(name, 0) * wgt
    ws = np.exp(-0.5 * (angdiff(ang, sk) / 14) ** 2) * smooth(0.015, 0.05, mag)
    rot += -angdiff(ang, sk) * L["skin_line_pull"] * ws
    satf *= 1 + (L["skin_sat"] - 1) * ws
    satf *= L["sat"] - L["sat_hi_reduce"] * hi[..., 0] - L["sat_sh_reduce"] * sh[..., 0]
    a2 = np.radians(ang + rot)
    mag2 = mag * np.maximum(satf, 0)
    x = from_ycc(Yc, mag2 * np.cos(a2), mag2 * np.sin(a2))
    if L["skin_protect"] > 0:
        Yr, cbr, crr = to_ycc(ref)
        ang_r = np.degrees(np.arctan2(crr, cbr)) % 360
        b = L["skin_protect"] * np.exp(-0.5 * (angdiff(ang_r, sk) / 16) ** 2) * smooth(0.015, 0.05, np.hypot(cbr, crr))
        Yx, cbx, crx = to_ycc(x)
        x = from_ycc(Yx, cbx * (1 - b) + cbr * b, crx * (1 - b) + crr * b)
    if L["print_density"] > 0:
        _, cb3, cr3 = to_ycc(x)
        C = np.clip(np.hypot(cb3, cr3) / 0.25, 0, 1)
        x = x * (1 - L["print_density"] * 0.35 * C)[..., None]
    x = np.clip(x, 0, 1)
    hl = L.get("hue_lum")
    if hl:
        # per hue luminance (subtractive, like a print stock), weighted by hue band and chroma, never on skin
        _, cb4, cr4 = to_ycc(x)
        ang4 = np.degrees(np.arctan2(cr4, cb4)) % 360
        cw = smooth(0.02, 0.12, np.hypot(cb4, cr4))
        m = np.ones(ang4.shape)
        for name, (cen, sig) in BANDS.items():
            m += (hl.get(name, 1.0) - 1.0) * np.exp(-0.5 * (angdiff(ang4, cen) / sig) ** 2)
        skin = np.exp(-0.5 * (angdiff(ang4, sk) / 12) ** 2)
        m = 1 + (m - 1) * cw * (1 - skin)
        x = np.clip(x * m[..., None], 0, 1)
    return L["black_lift"] + x * (L["white_point"] - L["black_lift"])


def grade(img, it, P, gains):
    return look(convert(img, gains, P, it["camera"]), P)


def to_lab(rgb):
    lin = np.clip(rgb, 0, 1) ** 2.4
    M = np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]])
    xyz = lin @ M.T / np.array([0.9505, 1.0, 1.089])
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], -1)


# ---------------------------------------------------------------- balance
def trim(temp, tint):
    return np.array([2 ** (0.1 * temp), 2 ** (-0.07 * tint), 2 ** (-0.1 * temp)])


def temp_tint(g):
    """grade_lab temp/tint units of a gain vector (the inverse of trim)."""
    lg = np.log2(np.asarray(g, float))
    return (lg[0] - lg[2]) / 0.2, -(lg[1] - (lg[0] + lg[2]) / 2) / 0.07


def soft_knee(v, k):
    return k * math.tanh(v / k) if k and k > 0 else v


def auto_balance(T, it, bp, P):
    """v1 gray-world balance (bit identical to the first version for v1 params)."""
    c = cam_for(it["camera"], P)
    img = np.concatenate([load(T, "strip_%s_%d" % (it["key"], i)).reshape(-1, 3) for i in range(NSTRIP)], 0)
    lin = np.maximum(c.decode(img), 1e-5)
    Y = lin @ LUMA
    lo, hi = np.percentile(Y, [5, 95])
    m = (Y > lo) & (Y < hi)
    mc = lin[m].mean(0)
    gy = np.exp(np.log(Y[m]).mean())
    gains = (mc.mean() / mc) ** bp["wb_strength"]
    expo = float(np.clip((bp["expo_target"] / gy) ** bp["expo_strength"], bp["expo_min"], bp["expo_max"]))
    if c.kind == "sdr":
        expo = 1.0
    return gains * expo, {"wb_gains": [round(float(x), 4) for x in gains], "auto_expo": round(expo, 3)}, []


def _conv1d(img, k, axis):
    r = len(k) // 2
    pad = [(0, 0)] * img.ndim
    pad[axis] = (r, r)
    p = np.pad(img, pad, mode="edge")
    out = np.zeros(img.shape, np.float64)
    n = img.shape[axis]
    for i, w in enumerate(k):
        sl = [slice(None)] * img.ndim
        sl[axis] = slice(i, i + n)
        out += w * p[tuple(sl)]
    return out


def _box(img, n=7):
    k = np.ones(n) / n
    return _conv1d(_conv1d(img, k, 0), k, 1)


def _grad(img, sigma=0.5):
    r = max(1, int(math.ceil(3 * sigma)))
    x = np.arange(-r, r + 1, dtype=float)
    g = np.exp(-x * x / (2 * sigma * sigma))
    g /= g.sum()
    dg = -x * g / (sigma * sigma)
    gx = _conv1d(_conv1d(img, dg, 1), g, 0)
    gy = _conv1d(_conv1d(img, dg, 0), g, 1)
    return np.sqrt(gx * gx + gy * gy)


def grayness_pixels(lin, code, percent=1.0, clip_code=0.85, dark=0.004, delta=1e-4):
    """Boolean mask of the grayest `percent` % of the pixels of one frame (grayness index, Qian et al. 2019):
    pixels whose log chromaticity does not change across edges are gray surfaces. Near-clipped and noise-floor
    pixels and flat areas without edges are left out."""
    bad = (code.max(-1) >= clip_code) | ((lin @ LUMA) <= dark)
    img = np.maximum(_box(np.maximum(lin, 0)), 1e-6)
    r, g, b = img[..., 0], img[..., 1], img[..., 2]
    L = r + g + b
    bad |= (_grad(r) <= delta) & (_grad(g) <= delta) & (_grad(b) <= delta)
    gi = np.hypot(_grad(np.log(r / L)), _grad(np.log(b / L)))
    gi[bad] = gi.max()
    gi = _box(gi)
    k = min(gi.size - 1, max(10, int(gi.size * percent / 100)))
    thr = np.partition(gi.ravel(), k)[k]
    return (gi <= thr) & ~bad


def _angle(a, b):
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if not (na > 0 and nb > 0) or not np.all(np.isfinite(a)) or not np.all(np.isfinite(b)):
        return float("nan")
    c = float(np.dot(a, b) / na / nb)
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


def _illuminant(lins, masks):
    x = np.concatenate([l[m] for l, m in zip(lins, masks)]) if masks else np.zeros((0, 3))
    return (x.mean(0) if len(x) else np.full(3, np.nan)), len(x)


def _auto_stops(lins, b, kind):
    if kind == "sdr" or not b.get("expo_strength"):
        return 0.0, None
    x = np.concatenate([l.reshape(-1, 3) for l in lins])
    Y = np.maximum(x @ LUMA, 1e-6)
    lo, hi = np.percentile(Y, [5, 95])
    sel = (Y > lo) & (Y < hi)
    if not sel.any():
        sel = np.ones(Y.shape, bool)
    gy = float(np.exp(np.log(Y[sel]).mean()))
    lo_s, hi_s = b.get("expo_range", [-3.0, 2.0])
    return float(np.clip(b["expo_strength"] * math.log2(b["expo_target"] / gy), lo_s, hi_s)), gy


def _visible_linear(T, it, P):
    c = cam_for(it["camera"], P)
    box = T.visible_box(it)
    codes = [crop_norm(load(T, "strip_%s_%d" % (it["key"], i)), box) for i in range(NSTRIP)]
    lins = [np.maximum(np.maximum(c.decode(x), 0) @ c.matrix.T, 0) for x in codes]
    return c, codes, lins


def balance_grayness(T, it, b, P):
    c, codes, lins = _visible_linear(T, it, P)
    clip_code = b.get("clip_code") or c.clip_code
    pct = b.get("gi_percent", 1.0)
    masks = [grayness_pixels(l, x, pct, clip_code) for l, x in zip(lins, codes)]
    e, n_gray = _illuminant(lins, masks)
    flags = []
    if n_gray == 0 or not np.all(np.isfinite(e)) or e.min() <= 0:
        t_raw = n_raw = t_app = n_app = 0.0
        wb = np.ones(3)
    else:
        t_raw, n_raw = temp_tint(1.0 / e)
        t_app = soft_knee(t_raw, b.get("temp_knee", 3.5))
        n_app = soft_knee(b.get("tint_strength", 1.0) * n_raw, b.get("tint_knee", 8.0))
        wb = trim(t_app, n_app)
        wb = wb / (wb @ LUMA)                               # white balance only, luma neutral
    stops, gy = _auto_stops(lins, b, c.kind)
    per_frame = [l[m].mean(0) if m.any() else np.full(3, np.nan) for l, m in zip(lins, masks)]
    fr = [_angle(p, q) for p in per_frame for q in per_frame]
    frame_spread = max([v for v in fr if v == v] or [0.0])
    h, w = codes[0].shape[:2]
    quads = []
    for (y0, y1, x0, x1) in [(0, h // 2, 0, w // 2), (0, h // 2, w // 2, w), (h // 2, h, 0, w // 2), (h // 2, h, w // 2, w)]:
        ql, qc = [l[y0:y1, x0:x1] for l in lins], [x[y0:y1, x0:x1] for x in codes]
        if min(y1 - y0, x1 - x0) < 8:
            continue
        qm = [grayness_pixels(l, x, 2.0, clip_code) for l, x in zip(ql, qc)]
        quads.append(_illuminant(ql, qm)[0])
    qs = [_angle(p, q) for p in quads for q in quads]
    quad_spread = max([v for v in qs if v == v] or [0.0])
    knee = b.get("temp_knee", 3.5)
    if knee and abs(t_raw) > 2 * knee:
        flags.append("strong cast kept partly (%s, raw temp %+.1f, applied %+.1f): check the mood is wanted"
                     % ("warm" if t_raw < 0 else "cool", t_raw, t_app))
    if quad_spread > 12:
        flags.append("mixed light (quadrant spread %.0f deg): look at this clip" % quad_spread)
    if frame_spread > 3:
        flags.append("light changes inside the clip (frame spread %.1f deg): one balance may not fit all of it" % frame_spread)
    if n_gray < 500:
        flags.append("few gray pixels (%d): the balance is a guess" % n_gray)
    info = {"method": "grayness", "cast_temp_tint": [round(float(t_raw), 2), round(float(n_raw), 2)],
            "applied_temp_tint": [round(float(t_app), 2), round(float(n_app), 2)], "auto_stops": round(stops, 2),
            "geo_mean_luma": round(gy, 4) if gy else None, "gray_pixels": int(n_gray),
            "frame_spread_deg": round(frame_spread, 2), "quadrant_spread_deg": round(quad_spread, 2)}
    return wb * 2 ** stops, info, flags


def balance_none(T, it, b, P):
    c, codes, lins = _visible_linear(T, it, P)
    stops, gy = _auto_stops(lins, b, c.kind)
    return np.ones(3) * 2 ** stops, {"method": "none", "auto_stops": round(stops, 2),
                                     "geo_mean_luma": round(gy, 4) if gy else None}, []


def _tagged(v):
    return isinstance(v, dict) and bool(v.get("uid"))


def foreign_tags(T, table):
    """True when clip_overrides entries carry uids and none of them is a clip of this timeline: the file was made for
    another timeline (a copy of the edit, or the project before an import or restore gave it new ids)."""
    tags = {v["uid"] for v in (table or {}).values() if _tagged(v)}
    live = {it.get("uid") for it in T.items if it.get("uid")}
    return bool(tags) and bool(live) and not (tags & live)


_RESOLVED_MEMO = {}


def resolve_overrides(T, table):
    """Which clip_overrides entry belongs to which clip: ({current clip key: stored key}, [stored keys no clip
    uses]).

    Entries are stored under the clip key (track_start) of the timeline.json they were made with. An edit that
    moves clips (a ripple insert or delete) gives them new keys, and a key can then belong to a different clip. So:
    1. match and rekey write the clip's uid into its entry ("uid"): such an entry follows its clip wherever it is;
    2. an untagged entry (older files, hand edits) under a key whose clip moved follows that clip to its new key
       (timeline.json "moved_keys", written by the dump);
    3. an untagged entry under a clip's current key that no moved clip took belongs to the clip there;
    4. an untagged entry stored under a clip's uid belongs to that clip.
    Each entry belongs to at most one clip. A key that "moved_keys" maps to "" belonged to a clip that is gone:
    its untagged entry belongs to nobody. When none of the table's uids is in this timeline (a file made for a
    copy of the edit, or for the project before an import), the uids are ignored and every entry goes by key."""
    table = table if isinstance(table, dict) else {}
    ck = (T.lab, T.ovsig, json.dumps(table, sort_keys=True, default=str))
    hit = _RESOLVED_MEMO.get(ck)
    if hit is not None:
        return hit
    res, used, copies = {}, set(), set()
    by_uid = {}
    tagged = _tagged if not foreign_tags(T, table) else (lambda v: False)
    for k, v in table.items():
        if tagged(v):
            by_uid.setdefault(v["uid"], []).append(k)
    for it in T.items:
        ks = by_uid.get(it.get("uid")) if it.get("uid") else None
        if ks:
            k = it["key"] if it["key"] in ks else ks[0]
            res[it["key"]] = k
            used.add(k)
            copies |= set(ks) - {k}       # an entry copied whole to another clip's key: that key's entry now

    def free(k):
        return k in table and k not in used and (not tagged(table[k]) or k in copies)
    for k0, k1 in T.moved.items():
        if k1 in T.by_key and k1 not in res and free(k0) and k0 not in copies:
            res[k1] = k0
            used.add(k0)
    for it in T.items:
        k = it["key"]
        if k not in res and free(k) and T.moved.get(k) != "":
            res[k] = k
            used.add(k)
    for it in T.items:
        u = it.get("uid")
        if it["key"] not in res and u and free(u):
            res[it["key"]] = u
            used.add(u)
    out = (res, [k for k in table if k not in used])
    if len(_RESOLVED_MEMO) > 256:
        _RESOLVED_MEMO.clear()
    _RESOLVED_MEMO[ck] = out
    return out


def override_for(T, table, it):
    """The clip_overrides entry of one clip ({} when it has none); see resolve_overrides."""
    res, _ = resolve_overrides(T, table)
    k = res.get(it["key"])
    v = table.get(k) if k is not None else None
    return v if isinstance(v, dict) else {}


def override_report(T, P):
    """Warnings about a params file's clip_overrides: entries that follow a moved clip (edit them only after
    rekey) and entries no clip uses (their clip was deleted or replaced)."""
    table = P.get("clip_overrides") or {}
    res, unused = resolve_overrides(T, table)
    foreign = foreign_tags(T, table)
    moved = sorted("%s -> %s" % (k, cur) for cur, k in res.items() if k != cur and (foreign or not _tagged(table[k])))
    moved_tagged = [k for cur, k in res.items() if k != cur and not foreign and _tagged(table[k])]
    unused = [k for k in unused if k != "_doc"]
    out = []
    if foreign:
        out.append("clip_overrides: the clip uids in this file belong to another timeline (a copy of this edit, or "
                   "the project before an import or restore), so every entry is matched by its clip key. Check "
                   "that the keys fit this timeline, then run `rekey PARAMS OUT` to store them for it")
    if moved:
        out.append("clip_overrides: %d entries without a clip uid follow clips the edit moved (%s). Check which clip "
                   "each moved entry belongs to (an entry written by hand after the edit may be meant for the clip "
                   "now at its key), then run `rekey PARAMS OUT`, which stores every entry under its clip's current "
                   "key with the clip's uid" % (len(moved), ", ".join(moved[:6]) + (" ..." if len(moved) > 6 else "")))
    if moved_tagged:
        out.append("clip_overrides: %d entries follow clips the edit moved through their clip uid; before editing "
                   "clip_overrides by hand, run `rekey PARAMS OUT` so every entry sits under its clip's current key"
                   % len(moved_tagged))
    if unused:
        out.append("clip_overrides: no clip in timeline.json uses the entries %s (the clip was deleted or replaced "
                   "since the grade was made). Their trims do nothing now; look at the clips that took their place"
                   % ", ".join(unused[:8]) + (" ..." if len(unused) > 8 else ""))
    return out


def clip_gains(T, it, P):
    """(gains, info) of one clip: auto balance x clip_overrides x global trims."""
    g, info, _ = clip_gains_full(T, it, P)
    return g, info


def clip_gains_full(T, it, P):
    b = P["balance"]
    method = b.get("method", "gray_world")
    if method == "gray_world":
        g, info, flags = auto_balance(T, it, b, P)
    elif method == "grayness":
        g, bal, flags = balance_grayness(T, it, b, P)
        info = {"balance": bal}
    else:
        g, bal, flags = balance_none(T, it, b, P)
        info = {"balance": bal}
    o = override_for(T, P["clip_overrides"], it)
    temp = o.get("temp", 0) + b.get("global_temp", 0)
    tint = o.get("tint", 0) + b.get("global_tint", 0)
    tr = np.array([2 ** (0.1 * temp), 2 ** (-0.07 * tint), 2 ** (-0.1 * temp)])
    g = g * tr * 2 ** o.get("stops", 0)
    info["final_gains"] = [round(float(x), 4) for x in g]
    return g, info, flags


_GAINS_MEMO = {}


def gains_all(T, P):
    """(gains, info, flags) per clip. The auto balance only depends on the balance settings, the clip overrides and
    the cameras, so a wedge over look or render keys reuses it."""
    key = (T.lab, T.ovsig, json.dumps([P["balance"], P["clip_overrides"], is_v1(P)], sort_keys=True, default=str))
    if key not in _GAINS_MEMO:
        res = pmap(lambda it: clip_gains_full(T, it, P), T.items, grade_workers())
        _GAINS_MEMO[key] = ({it["key"]: r[0] for it, r in zip(T.items, res)},
                            {it["key"]: r[1] for it, r in zip(T.items, res)},
                            {it["key"]: r[2] for it, r in zip(T.items, res)})
    g, info, flags = _GAINS_MEMO[key]
    return dict(g), copy.deepcopy(info), copy.deepcopy(flags)


# ---------------------------------------------------------------- stage 2 matching
def skin_mask(x):
    """Skin-hued pixels of display RGB x (N, 3), the same test as the metrics: hue within 25 deg of the skin line,
    some chroma, neither dark nor bright. Also catches wood, leather and tan walls."""
    Y, cb, cr = to_ycc(x)
    ang = np.degrees(np.arctan2(cr, cb)) % 360
    mag = np.hypot(cb, cr)
    return (np.abs(angdiff(ang, SKIN_ANG)) < 25) & (mag > 0.03) & (mag < 0.2) & (Y > 0.15) & (Y < 0.9)


NEUTRAL_CHROMA = 14.0     # C*ab below this counts as a neutral surface (graded, and before the look)
NEUTRAL_CHROMA_V1 = 20.0  # the first version's threshold (schema 1 params, which have no picture before the look)


def neutral_mask(x, lab, pre=None):
    """Low-chroma mid-tones of the graded picture x (N, 3) with Lab values lab: the pixels whose average a*, b* is a
    clip's neutral cast. pre (N, 3): the same pixels before the look; a pixel must have been low-chroma there too.
    A look that lowers chroma (soft_pastel, for example) would otherwise let skin, wood or orange walls into the
    mask. The test is the same in every hue direction, so warm-lit whites and greys stay in the mask and a warm clip
    does not read cool (a skin-hue exclusion would drop them and flip the measured cast of warm light). Pale skin,
    pastel clothing and light wood that are under the threshold before the look still pass; match's per-clip skin
    guard (_skin_worse) is the backstop. Without pre (schema 1) the first version's threshold applies."""
    Y = x @ LUMA
    lim = NEUTRAL_CHROMA_V1 if pre is None else NEUTRAL_CHROMA
    m = (Y > 0.25) & (Y < 0.75) & (np.hypot(lab[:, 1], lab[:, 2]) < lim)
    if pre is not None:
        lp = to_lab(pre)
        m &= np.hypot(lp[:, 1], lp[:, 2]) < lim
    return m


def _mid_cast(x, pre=None):
    x = x.reshape(-1, 3)
    lab = to_lab(x)
    m = neutral_mask(x, lab, None if pre is None else pre.reshape(-1, 3))
    return lab[m][:, 1:].mean(0) if m.sum() > 50 else None


def _graded_pair(c, it, P, gains):
    """(picture before the look, graded picture) of one frame; schema 1 params get no 'before' (v1 metrics)."""
    pre = convert(c, gains, P, it["camera"])
    return (None if is_v1(P) else pre), look(pre, P)


def _output_cast(codes, it, P, gains):
    vals = [v for v in (_mid_cast(x, pre) for pre, x in (_graded_pair(c, it, P, gains) for c in codes))
            if v is not None]
    return np.mean(vals, 0) if vals else None


SKIN_EXIT_CHROMA = 0.9    # match keeps a clip's previous trim if its skin would lose more than 10 % chroma
SKIN_EXIT_HUE = 6.0       # ... or end beyond the skin off flag's limit (6 deg from the skin target)
SKIN_EXIT_STEP = 3.0      # ... after moving more than 3 deg further from it


def _skin_sel(codes, it, P, gains):
    """Skin pixels of each frame, found before the look with the clip's current gains, or None when the clip has
    less than 3 % skin (the 'skin off' flag's rule)."""
    sel = [skin_mask(convert(c, gains, P, it["camera"]).reshape(-1, 3)) for c in codes]
    n, tot = sum(int(s.sum()) for s in sel), sum(s.size for s in sel)
    return sel if n >= 200 and n >= 0.03 * tot else None


def _skin_state(codes, sel, it, P, gains):
    """(mean hue offset from look.skin_target_deg in deg, mean chroma C*ab) of the selected skin pixels, graded."""
    tgt = P["look"].get("skin_target_deg") or 0.0
    xs = np.concatenate([grade(c, it, P, gains).reshape(-1, 3)[s] for c, s in zip(codes, sel)])
    _, cb, cr = to_ycc(xs)
    off = float(np.mean(angdiff(np.degrees(np.arctan2(cr, cb)) % 360, SKIN_ANG + tgt)))
    lab = to_lab(xs)
    return off, float(np.mean(np.hypot(lab[:, 1], lab[:, 2])))


def _skin_worse(before, after):
    """None, or why the trim would hurt this clip's skin (sallow: chroma lost or hue pushed off the skin line)."""
    (o0, c0), (o1, c1) = before, after
    if c1 < SKIN_EXIT_CHROMA * c0:
        return "skin would lose %d %% chroma (hue %+.1f -> %+.1f deg)" % (round(100 * (1 - c1 / c0)), o0, o1)
    if abs(o1) > SKIN_EXIT_HUE and abs(o1) > abs(o0) + SKIN_EXIT_STEP:
        return "skin hue would move from %+.1f to %+.1f deg off the skin target" % (o0, o1)
    return None


def match_neutrals(T, P, gains, target="median", limit=2.0, iters=3, damp=0.8, step=3):
    """Per clip temp/tint trims that move each clip's rendered mid-tone neutral cast (a*, b*) to one target.
    target: "median" of all clips, a clip key (match to a hero shot) or [a*, b*].
    Returns ({key: [temp, tint]}, target, casts, kept): kept {key: reason} lists clips that keep their previous
    trim (trim [0, 0]) because the new one would make their skin worse. Trims at +-limit are clamped (see
    clamped_trims)."""
    subs = {}
    for it in T.items:
        box = T.visible_box(it)
        subs[it["key"]] = [crop_norm(load(T, "strip_%s_%d" % (it["key"], i)), box)[::step, ::step] for i in range(NSTRIP)]
    casts = dict(zip([it["key"] for it in T.items],
                     pmap(lambda it: _output_cast(subs[it["key"]], it, P, gains[it["key"]]), T.items, grade_workers())))
    if isinstance(target, str) and target == "median":
        ok = [c for c in casts.values() if c is not None]
        if not ok:
            raise Fail("no clip has enough mid-tone neutral pixels to match")
        tgt = np.median(ok, 0)
    elif isinstance(target, str):
        if target not in casts:
            raise Fail("unknown clip key %r for --target" % target)
        if casts[target] is None:
            raise Fail("clip %s has too few mid-tone neutral pixels to be the match target" % target)
        tgt = casts[target]
    else:
        tgt = np.asarray(target, float)

    def solve(it):
        k = it["key"]
        if casts[k] is None:
            return None
        acc = np.zeros(2)
        codes = subs[k]
        # keep the best iterate: never return a trim that leaves the clip further from the target than no trim
        best, best_err = acc.copy(), float(np.linalg.norm(casts[k] - tgt))
        for _ in range(iters):
            g = gains[k] * trim(*acc)
            c = _output_cast(codes, it, P, g)
            ct = _output_cast(codes, it, P, g * trim(0.5, 0))
            cn = _output_cast(codes, it, P, g * trim(0, 0.5))
            if c is None or ct is None or cn is None:
                break
            err = float(np.linalg.norm(c - tgt))
            if err < best_err:
                best, best_err = acc.copy(), err
            J = np.stack([(ct - c) / 0.5, (cn - c) / 0.5], 1)
            acc = np.clip(acc + damp * np.linalg.lstsq(J, tgt - c, rcond=None)[0], -limit, limit)
        c = _output_cast(codes, it, P, gains[k] * trim(*acc))
        if c is not None and float(np.linalg.norm(c - tgt)) < best_err:
            best = acc
        best = [round(float(best[0]), 3), round(float(best[1]), 3)]
        # per clip exit: a trim that turns this clip's skin sallow (mixed light, neutrals lit differently from the
        # face) is dropped and the clip keeps its previous trim
        sel = _skin_sel(codes, it, P, gains[k]) if any(best) else None
        if sel is not None:
            why = _skin_worse(_skin_state(codes, sel, it, P, gains[k]),
                              _skin_state(codes, sel, it, P, gains[k] * trim(*best)))
            if why:
                return [0.0, 0.0], why
        return best, None
    out, kept = {}, {}
    for it, r in zip(T.items, pmap(solve, T.items, grade_workers())):
        if r is not None:
            out[it["key"]] = r[0]
            if r[1]:
                kept[it["key"]] = r[1]
    return out, [round(float(v), 2) for v in tgt], casts, kept


def clamped_trims(trims, limit):
    """{key: "temp" / "tint" / "temp and tint"} of the trims that reached +-limit (the solver wanted more)."""
    out = {}
    for k, (t, n) in trims.items():
        hit = [name for name, v in (("temp", t), ("tint", n)) if limit > 0 and abs(v) >= limit - 1e-3]
        if hit:
            out[k] = " and ".join(hit)
    return out


def rekeyed_overrides(T, table):
    """clip_overrides stored under every clip's current key, each entry tagged with its clip's uid (clips without an
    entry get {"uid": ...}, so later edits cannot mix them up). Returns (table, moved [(old, new)], dropped [keys of
    entries no clip uses: deleted or replaced clips])."""
    table = table if isinstance(table, dict) else {}
    res, unused = resolve_overrides(T, table)
    new, moved = {}, []
    for it in T.items:
        k = it["key"]
        e = copy.deepcopy(table[res[k]]) if k in res and isinstance(table[res[k]], dict) else {}
        if k in res and res[k] != k:
            moved.append((res[k], k))
        if it.get("uid"):
            e["uid"] = it["uid"]
        if e or k in res:
            new[k] = e
    dropped = []
    for k in unused:
        if str(k).startswith("_") and k not in new:
            new[k] = table[k]
        else:
            dropped.append(k)
    return new, moved, dropped


def _write_params(outfile, obj):
    try:
        d = os.path.dirname(os.path.abspath(outfile))
        os.makedirs(d, exist_ok=True)
        write_json(outfile, obj)
    except OSError as e:
        raise Fail("cannot write %s: %s" % (outfile, e))


def _say_rekey(moved, dropped):
    if moved:
        print("moved clips: their entries now sit under their current keys: %s" % ", ".join("%s -> %s" % m for m in moved))
    if dropped:
        print("left out entries no clip uses (their clip was deleted or replaced since the grade was made): %s"
              % ", ".join(dropped))


def cmd_rekey(T, pfile, outfile):
    """Store every clip_overrides entry of a params file under its clip's current key (after an edit moved clips)."""
    raw = load_json(pfile)
    resolve_params(raw)
    out = copy.deepcopy(raw)
    co, moved, dropped = rekeyed_overrides(T, raw.get("clip_overrides"))
    out["clip_overrides"] = co
    _write_params(outfile, out)
    _say_rekey(moved, dropped)
    print("wrote %s (%d clip entries, %d moved, %d left out)" % (outfile, len(co), len(moved), len(dropped)))


def cmd_match(T, pfile, outfile, target="median", limit=2.0, only=None):
    raw = load_json(pfile)
    P = resolve_params(raw)
    if only:
        only = {k.strip() for k in only.split(",") if k.strip()}
        unknown = sorted(k for k in only if k not in T.by_key)
        if unknown:
            raise Fail("--only: no clip with key %s in this timeline (keys are in timeline.json)" % ", ".join(unknown))
    if target not in ("median",) and target not in T.by_key:
        try:
            target = [float(v) for v in target.split(",")]
            assert len(target) == 2
        except Exception:
            raise Fail("--target must be median, a clip key or a*,b* (two numbers), got %r" % target)
    if not is_v1(P) and not only:
        f, house = look_chroma_factor(P), look_chroma_factor(resolve_params({"schema": 2}))
        if f < house - 0.02:
            print("note: this look keeps less chroma than the house look (x%.2f against x%.2f). match measures the "
                  "neutrals through the look; after such a look a second match (on top of the baseline's trims) "
                  "matched grey and white surfaces a little worse on a reference reel, although "
                  "mean_slice_neutral_spread dropped. Keep the file it started from unless a clip visibly stands "
                  "out (KNOWLEDGE.md section 4)." % (f, house))
    gains, _, _ = gains_all(T, P)
    trims, tgt, casts, kept = match_neutrals(T, P, gains, target, limit)
    clamped = clamped_trims(trims, limit)
    out = copy.deepcopy(raw)
    co, moved, dropped = rekeyed_overrides(T, raw.get("clip_overrides"))
    out["clip_overrides"] = co
    print("target a*/b* %s (%s)" % (tgt, target if isinstance(target, str) else "given"))
    print("%-10s %-14s %-16s %s" % ("clip", "name", "cast a*/b*", "added temp / tint"))
    for it in T.items:
        k = it["key"]
        if only and k not in only:
            print("%-10s %-14s unchanged (--only)" % (k, T.short(k)))
            continue
        if k not in trims:
            print("%-10s %-14s %-16s skipped (too few neutral pixels)" % (k, T.short(k), "-"))
            continue
        o = co.setdefault(k, {"uid": it["uid"]} if it.get("uid") else {})
        o["temp"] = round(o.get("temp", 0) + trims[k][0], 3)
        o["tint"] = round(o.get("tint", 0) + trims[k][1], 3)
        c = casts[k]
        note = ""
        if k in kept:
            note = "   kept its previous trim: %s; look at it" % kept[k]
        elif k in clamped:
            note = "   %s hit the --limit %g: the clip wanted more, look at it" % (clamped[k], limit)
        print("%-10s %-14s %+6.2f %+6.2f    %+.3f / %+.3f%s" % (k, T.short(k), c[0], c[1], trims[k][0], trims[k][1], note))
    _write_params(outfile, out)
    print("wrote", outfile)
    _say_rekey(moved, dropped)
    if only:
        kept = {k: v for k, v in kept.items() if k in only}
        clamped = {k: v for k, v in clamped.items() if k in only}
    if kept:
        print("%d clip(s) kept their previous trim because the new one would make their skin worse: %s"
              % (len(kept), ", ".join(sorted(kept))))
    if clamped:
        print("%d clip(s) reached the --limit %g (the neutrals wanted a bigger trim; mixed light or a strong cast "
              "kept on purpose): %s. Look at them; the totals are in clip_overrides."
              % (len(clamped), limit, ", ".join(sorted(clamped))))


# ---------------------------------------------------------------- images
def resize(img, w, h):
    from PIL import Image
    w, h = max(1, int(round(w))), max(1, int(round(h)))
    chans = []
    for ch in range(3):
        im = Image.fromarray((np.clip(img[..., ch], 0, 1) * 255).astype(np.uint8))
        chans.append(np.asarray(im.resize((w, h), Image.LANCZOS), np.float32) / 255)
    return np.stack(chans, -1)


def crop_norm(img, box):
    h, w, _ = img.shape
    u0, v0, u1, v1 = box
    x0, x1 = int(round(u0 * w)), max(int(round(u0 * w)) + 1, int(round(u1 * w)))
    y0, y1 = int(round(v0 * h)), max(int(round(v0 * h)) + 1, int(round(v1 * h)))
    return img[y0:y1, x0:x1]


def paste(canvas, im, x0, y0):
    H, W, _ = canvas.shape
    h, w, _ = im.shape
    xs, ys = max(0, -x0), max(0, -y0)
    xe, ye = min(w, W - x0), min(h, H - y0)
    if xe > xs and ye > ys:
        canvas[y0 + ys:y0 + ye, x0 + xs:x0 + xe] = im[ys:ye, xs:xe]


def letterbox(img, cw, ch):
    h, w, _ = img.shape
    s = min(cw / w, ch / h)
    tw, th = max(1, min(cw, int(round(w * s)))), max(1, min(ch, int(round(h * s))))
    out = np.zeros((ch, cw, 3), np.float32)
    x0, y0 = (cw - tw) // 2, (ch - th) // 2
    out[y0:y0 + th, x0:x0 + tw] = resize(img, tw, th)
    return out


def _font():
    from PIL import ImageFont
    try:
        return ImageFont.load_default(size=15)
    except Exception:
        return ImageFont.load_default()


def save(arr, path, labels=None):
    from PIL import Image, ImageDraw
    im = Image.fromarray((np.clip(arr, 0, 1) * 255 + 0.5).astype(np.uint8))
    if labels:
        d = ImageDraw.Draw(im)
        font = _font()
        for (x, y, text) in labels:
            try:
                tw = d.textlength(text, font=font)
            except Exception:
                tw = 8 * len(text)
            d.rectangle([x, y, x + tw + 6, y + 19], fill=(0, 0, 0))
            d.text((x + 3, y + 1), text, fill=(255, 230, 0), font=font)
    im.save(path, quality=92)


def metrics(img, P, pre=None):
    """pre: the same pixels before the look (schema 2): cast_ab_midtones then only uses low-chroma mid-tones that
    were also low-chroma before the look, the same mask as match (neutral_mask)."""
    x = img.reshape(-1, 3)
    Y = x @ LUMA
    lab = to_lab(x)
    C = np.hypot(lab[:, 1], lab[:, 2])
    _, cb, cr = to_ycc(x)
    ang = np.degrees(np.arctan2(cr, cb)) % 360
    mag = np.hypot(cb, cr)
    skin = (np.abs(angdiff(ang, SKIN_ANG)) < 25) & (mag > 0.03) & (mag < 0.2) & (Y > 0.15) & (Y < 0.9)
    wp, bl = P["look"]["white_point"], P["look"]["black_lift"]
    mid = (Y > 0.25) & (Y < 0.75) & (C < (NEUTRAL_CHROMA_V1 if pre is None else NEUTRAL_CHROMA))
    if pre is not None:
        lp = to_lab(pre.reshape(-1, 3))
        mid &= np.hypot(lp[:, 1], lp[:, 2]) < NEUTRAL_CHROMA
    out = {
        "luma_mean": round(float(Y.mean()), 3),
        "luma_p2_p50_p98": [round(float(v), 3) for v in np.percentile(Y, [2, 50, 98])],
        "clipped_pct": round(float((x.max(1) >= wp - 0.012).mean() * 100), 2),
        "crushed_pct": round(float((Y <= bl + 0.015).mean() * 100), 2),
        "mean_chroma_Cab": round(float(C.mean()), 1),
        "cast_ab_midtones": [round(float(v), 2) for v in lab[mid][:, 1:].mean(0)] if mid.any() else None,
        "skin_pct": round(float(skin.mean() * 100), 1),
    }
    if skin.sum() > 200:
        out["skin_hue_offset_deg"] = round(float(angdiff(ang[skin], SKIN_ANG).mean()), 1)
        out["skin_chroma_Cab"] = round(float(C[skin].mean()), 1)
    return out


def clip_flags(T, it, m, P, bal_flags):
    flags = list(bal_flags)
    if m["clipped_pct"] >= 1.0:
        flags.append("clipped (%.1f %% of the picture at the white point)" % m["clipped_pct"])
    if m["crushed_pct"] >= 1.5:
        flags.append("crushed (%.1f %% of the picture at the black level)" % m["crushed_pct"])
    tgt = P["look"].get("skin_target_deg") or 0.0
    if "skin_hue_offset_deg" in m and m["skin_pct"] >= 3.0 and abs(m["skin_hue_offset_deg"] - tgt) > 6.0:
        flags.append("skin off (hue %+.1f deg from the skin line, target %+.1f)" % (m["skin_hue_offset_deg"], tgt))
    try:
        st = cam_for(it["camera"], P).status
    except Fail:
        st = "unknown"
    if st == "approximate":
        flags.append("camera approximate (%s has no published formula; confirm the look of this clip)" % it["camera"])
    if it.get("camera_from") == "detected" and T.detect_for(it).get("confidence") == "low":
        flags.append("camera low confidence (detected %s from weak evidence; confirm it)" % it["camera"])
    return flags


def summarize(met, sl_met):
    clips = list(met.values())
    skin = [c["skin_hue_offset_deg"] for c in clips if "skin_hue_offset_deg" in c]
    multi = [s for s in sl_met if len(s["panels"]) >= 2]
    ls = [s["luma_spread"] for s in multi]
    ns = [s["neutral_spread"] for s in multi]
    r = lambda v, n=3: round(float(v), n)
    return {"clips": len(met), "slices": len(sl_met),
            "max_clipped_pct": r(max([c["clipped_pct"] for c in clips] or [0]), 2),
            "max_crushed_pct": r(max([c["crushed_pct"] for c in clips] or [0]), 2),
            "mean_skin_offset_deg": r(np.mean(skin), 1) if skin else None,
            "mean_slice_luma_spread": r(np.mean(ls)) if ls else 0.0,
            "max_slice_luma_spread": r(max(ls)) if ls else 0.0,
            "mean_slice_neutral_spread": r(np.mean(ns), 2) if ns else 0.0,
            "max_slice_neutral_spread": r(max(ns), 2) if ns else 0.0,
            "flagged_clips": {k: c["flags"] for k, c in met.items() if c.get("flags")}}


def neutral_spread(casts):
    cs = [np.asarray(c, float) for c in casts if c is not None]
    if len(cs) < 2:
        return 0.0
    return round(float(max(np.linalg.norm(a - b) for a in cs for b in cs)), 2)


def _page_cells(T, items, cols, cell_w, lo, hi):
    a = float(np.median([T.crop_aspect(it) for it in items])) if items else 16 / 9
    return int(np.clip(round(cell_w / a), lo, hi))


def _screen_grid(n, aspect, canvas_w):
    best = None
    for c in range(1, n + 1):
        rows = -(-n // c)
        w = int(min(MAX_SIDE // c, MAX_SIDE * aspect / rows, canvas_w))
        key = (rows * c - n, -w)
        if best is None or key < best[0]:
            best = (key, c, rows, w)
    return best[1], best[2], best[3], max(1, int(round(best[3] / aspect)))


def evaluate(T, P, thumbs=None):
    """Grade every strip frame and every slice panel once (in parallel). Returns gains, per clip metrics,
    per slice metrics, the preview composites of every slice at scale K and the requested thumbnails.
    thumbs: {key: [(cell_w, cell_h, frame_indices)]}."""
    gains, info, bflags = gains_all(T, P)
    boxes = {it["key"]: T.visible_box(it) for it in T.items}

    def clip_job(it):
        k = it["key"]
        pairs = [_graded_pair(crop_norm(load(T, "strip_%s_%d" % (k, j)), boxes[k]), it, P, gains[k])
                 for j in range(NSTRIP)]
        vis = [x for _, x in pairs]
        pre = None if is_v1(P) else np.concatenate([p.reshape(-1, 3) for p, _ in pairs])
        m = metrics(np.concatenate([v.reshape(-1, 3) for v in vis]), P, pre)
        rec = dict(name=T.short(k), **m, **info[k])
        try:
            st = cam_for(it["camera"], P).status
        except Fail:
            st = "unknown"
        rec["camera"], rec["camera_status"] = it["camera"], st
        rec["flags"] = clip_flags(T, it, m, P, bflags[k])
        th = []
        for (cw, ch, idx) in (thumbs or {}).get(k, []):
            th.append([letterbox(vis[j], cw, ch) for j in idx])
        return rec, th
    res = pmap(clip_job, T.items, grade_workers())
    met = {it["key"]: r[0] for it, r in zip(T.items, res)}
    thumbs_out = {it["key"]: r[1] for it, r in zip(T.items, res)}

    jobs = [(si, it) for si, t in enumerate(T.slices) for it in sorted(T.active(t), key=lambda i: i["track"])]

    def panel_job(job):
        si, it = job
        t = T.slices[si]
        pre, img = _graded_pair(load(T, "slice_%d_%s" % (si, it["key"])), it, P, gains[it["key"]])
        box = T.visible_box(it, t)
        m = metrics(crop_norm(img, box), P, None if pre is None else crop_norm(pre, box))
        if it.get("flip_x"):
            img = img[:, ::-1]
        if it.get("flip_y"):
            img = img[::-1]
        x0, y0, w, h = T.rect(it, K)
        return m, resize(img, w, h), (int(round(x0)), int(round(y0)))
    pres = dict(zip([(si, it["key"]) for si, it in jobs], pmap(panel_job, jobs, grade_workers())))
    sl_met, screens = [], []
    for si, t in enumerate(T.slices):
        canvas = np.zeros((int(T.H * K), int(T.W * K), 3), np.float32)
        pan = {}
        for it in sorted(T.active(t), key=lambda i: i["track"]):
            m, small, (x0, y0) = pres[(si, it["key"])]
            paste(canvas, small, x0, y0)
        for it in T.active(t):
            m = pres[(si, it["key"])][0]
            pan["T%d %s" % (it["track"], T.short(it["key"]))] = {
                "key": it["key"], "luma_mean": m["luma_mean"], "cast_ab_midtones": m["cast_ab_midtones"],
                "mean_chroma_Cab": m["mean_chroma_Cab"]}
        lm = [v["luma_mean"] for v in pan.values()]
        sl_met.append({"slice": si, "timeline_frame": t, "panels": pan, "luma_spread": round(max(lm) - min(lm), 3),
                       "neutral_spread": neutral_spread([v["cast_ab_midtones"] for v in pan.values()])})
        screens.append(canvas)
    return {"gains": gains, "clips": met, "slices": sl_met, "screens": screens, "thumbs": thumbs_out,
            "summary": summarize(met, sl_met)}


def cmd_render(T, pfile, outdir):
    t0 = time.time()
    raw = load_json(pfile) if pfile and os.path.exists(pfile) else None
    if raw is not None and "schema" not in raw:
        print("WARNING: %s has no \"schema\" key, so it renders as a v1 file (v1 defaults, legacy tone curve, "
              "gray-world balance). Make new grades with init-params." % os.path.basename(pfile))
    P = params(pfile)
    for w in override_report(T, P):
        print("WARN " + w)
    os.makedirs(outdir, exist_ok=True)
    for fn in os.listdir(outdir):
        if re.match(r"^(screens|sheet|strips)(_\d+)?\.jpg$", fn):
            os.remove(os.path.join(outdir, fn))
    items = T.items
    # page layouts first, so each clip job returns its thumbnails at their final size
    sheet_pages = [items[i:i + 24] for i in range(0, len(items), 24)]
    strip_pages = [items[i:i + 12] for i in range(0, len(items), 12)]
    thumbs = {it["key"]: [] for it in items}
    sheet_cells, strip_cells = [], []
    for pg in sheet_pages:
        cw = 330
        ch = _page_cells(T, pg, 6, cw, 150, 440)
        sheet_cells.append((cw, ch))
        for it in pg:
            thumbs[it["key"]].append((cw, ch, [NSTRIP // 2]))
    for pg in strip_pages:
        a = float(np.median([T.crop_aspect(it) for it in pg]))
        h0 = min(240, MAX_SIDE // len(pg))
        cw = int(min(MAX_SIDE // NSTRIP, max(80, round(h0 * a))))
        ch = int(min(h0, max(60, round(cw / a))))
        strip_cells.append((cw, ch))
        for it in pg:
            thumbs[it["key"]].append((cw, ch, list(range(NSTRIP))))
    ev = evaluate(T, P, thumbs)
    files = []
    for n, (pg, (cw, ch)) in enumerate(zip(sheet_pages, sheet_cells), 1):
        cols = min(6, len(pg))
        rows = -(-len(pg) // cols)
        sheet = np.zeros((rows * ch, cols * cw, 3), np.float32)
        labels = []
        for i, it in enumerate(pg):
            r, q = divmod(i, cols)
            sheet[r * ch:(r + 1) * ch, q * cw:(q + 1) * cw] = ev["thumbs"][it["key"]][0][0]
            labels.append((q * cw + 3, r * ch + 3, "%s %s" % (it["key"], T.short(it["key"]))))
        name = "sheet_%02d.jpg" % n
        save(sheet, os.path.join(outdir, name), labels)
        files.append(name)
    for n, (pg, (cw, ch)) in enumerate(zip(strip_pages, strip_cells), 1):
        strips = np.zeros((len(pg) * ch, NSTRIP * cw, 3), np.float32)
        labels = []
        for i, it in enumerate(pg):
            for j, im in enumerate(ev["thumbs"][it["key"]][-1]):
                strips[i * ch:(i + 1) * ch, j * cw:(j + 1) * cw] = im
            labels.append((3, i * ch + 3, "%s %s" % (it["key"], T.short(it["key"]))))
        name = "strips_%02d.jpg" % n
        save(strips, os.path.join(outdir, name), labels)
        files.append(name)
    aspect = T.W / T.H
    canvas_w = int(T.W * K)
    for n in range(0, len(ev["screens"]), 5):
        grp = ev["screens"][n:n + 5]
        cols, rows, w, h = _screen_grid(len(grp), aspect, canvas_w)
        page = np.zeros((rows * h, cols * w, 3), np.float32)
        labels = []
        for j, scr in enumerate(grp):
            r, q = divmod(j, cols)
            page[r * h:(r + 1) * h, q * w:(q + 1) * w] = resize(scr, w, h)
            labels.append((q * w + 3, r * h + 3, "slice %d" % (n + j)))
        name = "screens_%02d.jpg" % (n // 5 + 1)
        save(page, os.path.join(outdir, name), labels)
        files.insert(n // 5, name)
    write_json(os.path.join(outdir, "metrics.json"), {"summary": ev["summary"], "clips": ev["clips"], "slices": ev["slices"]})
    write_json(os.path.join(outdir, "params_resolved.json"), P)
    s = ev["summary"]
    print("rendered ->", outdir, ":", " ".join(files), "metrics.json")
    print("summary: max clipped %.2f %%  max crushed %.2f %%  slice luma spread mean %.3f  neutral spread mean %.2f max %.2f"
          "  skin offset %s  flagged clips %d  [%.1f s]" % (
              s["max_clipped_pct"], s["max_crushed_pct"], s["mean_slice_luma_spread"], s["mean_slice_neutral_spread"],
              s["max_slice_neutral_spread"], "%+.1f deg" % s["mean_skin_offset_deg"] if s["mean_skin_offset_deg"] is not None else "n/a",
              len(s["flagged_clips"]), time.time() - t0))


# ---------------------------------------------------------------- params commands
def cmd_init_params(outfile, presets):
    P = copy.deepcopy(DEFAULTS)
    for name in presets:
        P = merge(P, load_overlay(name))
    P["schema"] = 2
    resolve_params(P)
    _write_params(outfile, P)
    print("wrote %s (schema 2 defaults%s)" % (outfile, " + " + " + ".join(presets) if presets else ""))


def cmd_merge(args):
    if len(args) < 3:
        raise Fail("usage: merge BASE OVERLAY [OVERLAY...] OUT")
    base_fn, overlays, outfile = args[0], args[1:-1], args[-1]
    base = strip_private(load_json(base_fn)) if os.path.exists(base_fn) else load_overlay(base_fn)
    out = copy.deepcopy(base)
    for o in overlays:
        out = merge(out, load_overlay(o))
    if "schema" in base:
        out["schema"] = base["schema"]
    else:
        out.pop("schema", None)
    resolve_params(out)
    _write_params(outfile, out)
    print("wrote %s (%s%s)" % (outfile, " + ".join([base_fn] + list(overlays)),
                               "" if "schema" in out else "; no schema key, so this stays a v1 file"))


def set_path(d, dotted, value):
    parts = dotted.split(".")
    cur = d
    for p in parts[:-1]:
        if not isinstance(cur.get(p), dict):
            cur[p] = {}
        cur = cur[p]
    cur[parts[-1]] = value
    return d


def parse_value(s):
    try:
        return json.loads(s)
    except ValueError:
        return s.strip()


def cmd_wedge(T, pfile, key, values, outdir, slices=None, sep=","):
    t0 = time.time()
    raw = load_json(pfile)
    vals = [parse_value(v) for v in values.split(sep) if v.strip() != ""]
    if not vals:
        raise Fail("no values given")
    n = len(T.slices)
    if slices:
        idx = [int(v) for v in slices.split(",")]
        bad = [i for i in idx if not 0 <= i < n]
        if bad:
            raise Fail("--slices %s outside 0..%d" % (bad, n - 1))
    else:
        idx = sorted({int(round(v)) for v in np.linspace(0, n - 1, min(3, n))})
    os.makedirs(outdir, exist_ok=True)
    rows, out = [], []
    for v in vals:
        r2 = copy.deepcopy(raw)
        if key == "preset":
            r2 = merge(r2, load_overlay(str(v)))
            if "schema" in raw:
                r2["schema"] = raw["schema"]
            else:
                r2.pop("schema", None)
        else:
            set_path(r2, key, v)
        ev = evaluate(T, resolve_params(r2))
        rows.append([ev["screens"][i] for i in idx])
        out.append({"value": v, "summary": ev["summary"]})
    aspect = T.W / T.H
    cols = len(idx)
    w = int(min(T.W * K, MAX_SIDE // cols))
    h = int(round(w / aspect))
    if h * len(rows) > MAX_SIDE:
        h = MAX_SIDE // len(rows)
        w = int(round(h * aspect))
    grid = np.zeros((h * len(rows), w * cols, 3), np.float32)
    labels = []
    for r, (row, v) in enumerate(zip(rows, vals)):
        for q, scr in enumerate(row):
            grid[r * h:(r + 1) * h, q * w:(q + 1) * w] = resize(scr, w, h)
        labels.append((3, r * h + 3, "%s=%s" % (key, json.dumps(v))))
    for q, i in enumerate(idx):
        labels.append((q * w + 3, h - 22, "slice %d" % i))
    save(grid, os.path.join(outdir, "wedge.jpg"), labels)
    write_json(os.path.join(outdir, "wedge.json"), {"key": key, "slices": idx, "values": out})
    print("wedge -> %s: wedge.jpg (%d rows x %d slices) wedge.json  [%.1f s]" % (outdir, len(vals), cols, time.time() - t0))
    for o in out:
        s = o["summary"]
        print("  %s=%-14s clipped %.2f  crushed %.2f  neutral spread %.2f  skin %s  flagged %d" % (
            key, json.dumps(o["value"]), s["max_clipped_pct"], s["max_crushed_pct"], s["mean_slice_neutral_spread"],
            s["mean_skin_offset_deg"], len(s["flagged_clips"])))


# ---------------------------------------------------------------- LUTs
def default_lut_root():
    if sys.platform == "darwin":
        return "/Library/Application Support/Blackmagic Design/DaVinci Resolve/LUT"
    if os.name == "nt":
        return os.path.join(os.environ.get("PROGRAMDATA", r"C:\ProgramData"), "Blackmagic Design", "DaVinci Resolve",
                            "Support", "LUT")
    return "/opt/resolve/LUT"


def lut_root_for(T=None):
    if T is not None and T.d.get("lut_root"):
        return T.d["lut_root"], "lut_root in timeline.json"
    if os.environ.get("RESOLVE_LUT_ROOT"):
        return os.environ["RESOLVE_LUT_ROOT"], "RESOLVE_LUT_ROOT"
    return default_lut_root(), "the default for this system"


LUT_ROOT_ADVICE = (
    "In Resolve open Preferences > System > General, add a folder you can write to under the LUT locations "
    "(Custom LUT paths), restart Resolve, then either set the environment variable RESOLVE_LUT_ROOT to that folder "
    "or add \"lut_root\": \"<that folder>\" to timeline.json. For a manual install use luts ... --out DIR.")


def writable_dir(d):
    """True when files can be created in folder d. On Windows os.access only reads the folder's read-only attribute
    and says nothing about its permissions (C:\\ProgramData\\...\\LUT for a normal user), so there a small temp
    file is created and removed; elsewhere os.access is reliable."""
    if os.name != "nt":
        return os.access(d, os.W_OK)
    import tempfile
    try:
        fd, fn = tempfile.mkstemp(prefix=".rc_write_test_", dir=d)
        os.close(fd)
        os.remove(fn)
        return True
    except OSError:
        return False


def lut_root_problem(root, folder=None):
    if not os.path.isdir(root):
        return "the LUT folder %s does not exist" % root
    if not writable_dir(root):
        return "the LUT folder %s is not writable" % root
    if folder and os.path.isdir(folder) and not writable_dir(folder):
        return "the LUT folder %s is not writable" % folder
    return None


def cube_table(f, N):
    g = np.linspace(0, 1, N)
    b, gg, r = np.meshgrid(g, g, g, indexing="ij")
    return np.clip(f(np.stack([r, gg, b], -1).reshape(-1, 3)), 0, 1)


def _bad_entries(out):
    return int((~np.isfinite(out)).any(-1).sum())


NAN_ADVICE = "check the params (render.gamut_threshold, look.tint_range, look.contrast, look.pivot) and render again"


def write_table(fn, out, title, N):
    """Write a .cube atomically: into fn.part first, then renamed over fn, so Resolve never sees half a LUT (a full
    disk or a crash leaves only a .part file, which the next luts run removes)."""
    bad = _bad_entries(out)
    if bad:
        raise Fail("%s would contain %d entries that are not numbers; %s" % (os.path.basename(fn), bad, NAN_ADVICE))
    body = ("%.6f %.6f %.6f\n" * len(out)) % tuple(out.ravel().tolist())
    part = fn + ".part"
    try:
        with open(part, "w", encoding="utf-8", newline="\n") as fh:
            fh.write('TITLE "%s"\nLUT_3D_SIZE %d\n' % (title, N))
            fh.write(body)
        os.replace(part, fn)
    except BaseException:
        try:
            os.remove(part)
        except OSError:
            pass
        raise


def clean_parts(folder, tag, max_age=600):
    """Remove leftover .cube.part files: this TAG's, and any older than max_age seconds (from a crashed run)."""
    try:
        names = os.listdir(folder)
    except OSError:
        return
    now = time.time()
    for f in names:
        if not f.endswith(".cube.part"):
            continue
        p = os.path.join(folder, f)
        try:
            mine = f == "%s_Look.cube.part" % tag or f.startswith("%s_Conv_" % tag)
            if mine or now - os.path.getmtime(p) > max_age:
                os.remove(p)
        except OSError:
            pass


def write_cube(fn, f, title, N):
    write_table(fn, cube_table(f, N), title, N)


def ascii_name(s):
    """The clip name part of a LUT file name, folded to ASCII letters, digits, _ and - (accents dropped, other
    scripts and emoji removed): the clip key before it already makes each name unique, and plain names are safe in
    Resolve's LUT browser and SetLUT on every system."""
    import unicodedata
    s = unicodedata.normalize("NFKD", str(s).replace("..", "_")).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^A-Za-z0-9_-]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_-")
    return s or "clip"


def check_tag(tag):
    if not re.match(r"^[A-Za-z0-9_-]{1,40}$", tag or ""):
        raise Fail("TAG must be letters, digits, _ or - (for example Final or LookB), got %r" % tag)


def tags_in_use(folder):
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    return sorted(f[:-len("_Look.cube")] for f in names if f.endswith("_Look.cube"))


AUTO_CONV_33 = ("slog3_sg3c",)   # measured fine at 33^3 and checked against Resolve; every other camera gets 65^3


def conv_size_for(it, P, conv_size="auto"):
    """33 or 65 points per side for a clip's conversion LUT. auto: 33 for Sony S-Log3 / S-Gamut3.Cine, else 65,
    because steep log and display curves near black (Canon Log, sRGB, D-Log2 and others) and wide gamuts (S-Gamut3:
    p99 dE 0.79 at 33) interpolate worse at 33."""
    if conv_size in (33, 65):
        return conv_size
    return 33 if cam_for(it["camera"], P).key in AUTO_CONV_33 else 65


def cmd_luts(T, pfile, tag, out=None, conv_size="auto", overwrite=False):
    t0 = time.time()
    check_tag(tag)
    if str(conv_size) in ("33", "65"):
        conv_size = int(conv_size)
    elif conv_size != "auto":
        raise Fail("--conv-size must be 33, 65 or auto (auto: 33 for Sony S-Log3 / S-Gamut3.Cine, 65 for other "
                   "cameras)")
    P = params(pfile)
    if out:
        folder, root = os.path.abspath(out), None
    else:
        root, how = lut_root_for(T)
        folder = os.path.join(root, safe_name(T.lut_folder, keep_space=True))
        prob = lut_root_problem(root, folder)
        if prob:
            raise Fail("%s (LUT root from %s). %s" % (prob, how, LUT_ROOT_ADVICE))
    map_fn = os.path.join(T.lab, "luts_%s.json" % tag)
    if os.path.exists(map_fn) and not overwrite:
        try:
            old_folder = load_json(map_fn).get("folder")
        except (OSError, ValueError, AttributeError):
            old_folder = None
        if old_folder and os.path.normcase(os.path.abspath(old_folder)) != os.path.normcase(os.path.abspath(folder)):
            raise Fail("TAG %s was already baked into %s (luts_%s.json); baking it again into %s would replace that "
                       "map. Pick a new TAG, or pass --overwrite only after the user agrees." % (tag, old_folder, tag, folder))
    look_fn = os.path.join(folder, "%s_Look.cube" % tag)
    conv_fn = {it["key"]: os.path.join(folder, "%s_Conv_%s_%s.cube" % (tag, it["key"], ascii_name(T.short(it["key"]))))
               for it in T.items}
    existing = [f for f in [look_fn] + list(conv_fn.values()) if os.path.exists(f)]
    if existing and not overwrite:
        used = tags_in_use(folder)
        raise Fail("LUT files for TAG %s already exist in %s (%d files, for example %s). An applied grade may use them "
                   "and Resolve caches LUTs by file name, so pick a new TAG (already used: %s), or pass --overwrite "
                   "only after the user agrees." % (tag, folder, len(existing), os.path.basename(existing[0]),
                                                    ", ".join(used) or tag))
    # the folder is made only now, so a refused call leaves nothing behind
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError as e:
        raise Fail("cannot create %s: %s%s" % (folder, e, "" if out else ". " + LUT_ROOT_ADVICE))
    if out and not writable_dir(folder):
        raise Fail("%s is not writable" % folder)
    clean_parts(folder, tag)
    for w in override_report(T, P):
        print("WARN " + w)
    gains, _, _ = gains_all(T, P)
    sizes = {it["key"]: conv_size_for(it, P, conv_size) for it in T.items}
    jobs = [("look", None)] + [("clip", it) for it in T.items]

    def run(j):
        if j[0] == "look":
            return look_fn, cube_table(lambda x: look(x, P), 65), "%s look" % tag, 65
        k = j[1]["key"]
        return (conv_fn[k], cube_table(lambda x: convert(x, gains[k], P, j[1]["camera"]), sizes[k]),
                "%s conv %s" % (tag, k), sizes[k])
    tables = pmap(run, jobs, grade_workers())
    bad = [(os.path.basename(t[0]), _bad_entries(t[1])) for t in tables if _bad_entries(t[1])]
    if bad:
        raise Fail("no LUTs written: %s would contain entries that are not numbers; %s" % (
            ", ".join("%s (%d)" % b for b in bad), NAN_ADVICE))

    def write(t):
        try:
            write_table(*t)
        except OSError as e:
            raise Fail("%s: %s. %s" % (t[0], e, LUT_ROOT_ADVICE))
    pmap(write, tables, grade_workers())
    clips = {it["key"]: {"lut": conv_fn[it["key"]], "uid": it.get("uid"), "name": it.get("name"),
                         "path": it.get("path"), "size": sizes[it["key"]]} for it in T.items}
    used = sorted(set(sizes.values()))
    mp = {"tag": tag, "lut_root": root, "folder": folder, "conv_size": used[0] if len(used) == 1 else "mixed",
          "look": look_fn, "clips": clips}
    try:
        write_json(map_fn, mp)
    except OSError as e:
        map_fn = os.path.join(folder, "luts_%s.json" % tag)
        write_json(map_fn, mp)
        print("note: the lab is not writable (%s); the map went to %s. Copy it into the lab before apply-script." % (e, map_fn))
    print("wrote %d LUTs to %s (map in %s)  [%.1f s]" % (len(clips) + 1, folder, os.path.basename(map_fn),
                                                      time.time() - t0))
    if not T.d.get("lut_folder"):
        # keep the folder and group name fixed from now on, whatever later versions or a project rename do
        try:
            fn = os.path.join(T.lab, "timeline.json")
            d = load_json(fn)
            d["lut_folder"] = T.lut_folder
            write_json(fn, d)
            T.d["lut_folder"] = T.lut_folder
        except OSError:
            pass
    if root is None:
        print("note: written with --out. Resolve only finds LUTs inside its LUT folder or a Custom LUT path: copy the "
              "folder there (or add it as a Custom LUT path) and click Update Lists before applying.")


# ---------------------------------------------------------------- Resolve snippets
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


def snippet_head(name, T, what):
    if T is None:
        where = "the current project and timeline"
    else:
        where = "project %s timeline %s" % (json.dumps(T.d.get("project")), json.dumps(T.d.get("timeline")))
    return "# resolve-colorist %s for %s, generated %s. %s\n%s" % (name, where, iso_now(), what, SNIPPET_HEADER)


def run_line(path):
    p = posix(path)
    arg = 'r"%s"' % p if '"' not in p else json.dumps(p)
    return "RUN run_script_unsafe: exec(open(%s, encoding=\"utf-8\").read())" % arg


def write_snippet(lab, fname, text):
    compile(text, fname, "exec")
    d = os.path.join(os.path.abspath(lab), "snippets")
    os.makedirs(d, exist_ok=True)
    fn = os.path.join(d, fname)
    with open(fn, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    print(run_line(fn))
    return fn


DUMP_BODY = r'''

def _rc_dump(resolve, project, OUT):
    import json, os
    import time
    KEEP = ("base_scale", "lut_folder", "lut_root", "camera", "camera_overrides", "group")
    t_start = time.time()
    VIDEO_EXT = (".mp4", ".mov", ".mxf", ".mts", ".m2ts", ".avi", ".mkv", ".braw", ".r3d", ".crm", ".ari",
                 ".arx", ".nev", ".mpg", ".mpeg", ".m4v", ".webm", ".dng", ".3gp", ".insv")

    def has(o, name):
        try:
            return o is not None and name in dir(o)
        except Exception:
            return False

    def call(o, name, *a):
        if not has(o, name):
            return None
        try:
            return getattr(o, name)(*a)
        except Exception:
            return None

    def getter(o, dict_calls, single):
        d = None
        for n in dict_calls:
            v = call(o, n)
            if isinstance(v, dict) and v:
                d = v
                break

        def get(key, default=None):
            if d is not None and key in d:
                return d[key]
            v = call(o, single, key)
            return default if v is None else v
        return get

    def num(v, default=0.0):
        try:
            return float(str(v).split()[0])
        except Exception:
            return default

    def tools(g):
        n = (call(g, "GetNumNodes") or 0) if g else 0
        return [list(call(g, "GetToolsInNode", i) or []) for i in range(1, n + 1)]

    def luts(g):
        n = (call(g, "GetNumNodes") or 0) if g else 0
        return [call(g, "GetLUT", i) or "" for i in range(1, n + 1)]

    tl = call(project, "GetCurrentTimeline")
    if not tl:
        return {"error": "no current timeline: open the timeline to grade in Resolve first"}
    ps = getter(project, ("GetSettings", "GetSetting"), "GetSetting")
    ts = getter(tl, ("GetSettings", "GetSetting"), "GetSetting")
    try:
        layers = max(1, int(num(ps("nodeStackLayers", 1), 1)))
    except Exception:
        layers = 1
    items, skipped = [], []
    for t in range(1, (call(tl, "GetTrackCount", "video") or 0) + 1):
        enabled = call(tl, "GetIsTrackEnabled", "video", t)
        for it in call(tl, "GetItemListInTrack", "video", t) or []:
            name = call(it, "GetName") or ""
            st, en = call(it, "GetStart"), call(it, "GetEnd")
            if enabled is False:
                skipped.append([t, name, st, en, "disabled track"])
                continue
            if call(it, "GetClipEnabled") is False:
                skipped.append([t, name, st, en, "disabled"])
                continue
            m = call(it, "GetMediaPoolItem")
            if not m:
                skipped.append([t, name, st, en, "generator or title"])
                continue
            cp = getter(m, ("GetClipProperty",), "GetClipProperty")
            fn = str(cp("File Name") or "")
            typ = str(cp("Type") or "").lower()
            if not fn.lower().endswith(VIDEO_EXT):
                if "compound" in typ or "timeline" in typ:
                    why = "compound"
                elif "generator" in typ or "fusion" in typ:
                    why = "generator"
                elif "title" in typ:
                    why = "title"
                else:
                    why = "graphic"
                skipped.append([t, name, st, en, why])
                continue
            try:
                sw, sh = [int(v) for v in str(cp("Resolution") or "").lower().split("x")[:2]]
            except Exception:
                sw, sh = 0, 0
            ip = getter(it, ("GetProperties", "GetProperty"), "GetProperty")
            g = call(it, "GetNodeGraph")
            nn = (call(g, "GetNumNodes") or 0) if g else None
            cg = call(it, "GetColorGroup")
            ver = call(it, "GetCurrentVersion")
            items.append({
                "key": "%d_%d" % (t, st), "track": t, "name": fn, "path": cp("File Path"),
                "start": st, "end": en, "src_start": call(it, "GetSourceStartFrame"), "src_end": call(it, "GetSourceEndFrame"),
                "fps": num(cp("FPS"), 0.0), "src_w": sw, "src_h": sh,
                "pan": num(ip("Pan", 0.0)), "tilt": num(ip("Tilt", 0.0)), "zoom_x": num(ip("ZoomX", 1.0), 1.0),
                "zoom_y": num(ip("ZoomY", 1.0), 1.0), "rotation": num(ip("RotationAngle", 0.0)),
                "flip_x": bool(ip("FlipX", False)), "flip_y": bool(ip("FlipY", False)),
                "crop": [num(ip("CropLeft", 0.0)), num(ip("CropRight", 0.0)), num(ip("CropTop", 0.0)), num(ip("CropBottom", 0.0))],
                "opacity": num(ip("Opacity", 100.0), 100.0), "composite": ip("CompositeMode", 0), "scaling": ip("Scaling", 0),
                "nodes": nn,
                "node_labels": [call(g, "GetNodeLabel", i) or "" for i in range(1, nn + 1)] if g else None,
                "node_tools": tools(g) if g else None,
                "node_luts": luts(g) if g else None,
                "layer_tools": [tools(call(it, "GetNodeGraph", L)) for L in range(2, layers + 1)],
                "color_group": call(cg, "GetName") if cg else None,
                "fusion_comps": call(it, "GetFusionCompCount"),
                "input_color_space": cp("Input Color Space"), "gamma_notes": cp("Gamma Notes"),
                "camera_type": cp("Camera Type"), "codec": cp("Video Codec"),
                "uid": call(it, "GetUniqueId"), "data_level": cp("Data Level"), "input_lut": cp("Input LUT"),
                "idt": cp("IDT"), "input_gamma": cp("Input Gamma"),
                "version": ver.get("versionName") if isinstance(ver, dict) else None})
    # A color group's pre-clip and post-clip grades apply to its clips in every timeline of the project, so record
    # which other timelines have clips in each group (apply refuses to change a group shared that way).
    others, can_list = [], has(project, "GetTimelineCount") and has(project, "GetTimelineByIndex")
    me, my_name = call(tl, "GetUniqueId"), call(tl, "GetName")
    for i in range(1, ((call(project, "GetTimelineCount") or 0) if can_list else 0) + 1):
        t2 = call(project, "GetTimelineByIndex", i)
        if t2 is None:
            continue
        u = call(t2, "GetUniqueId")
        if (u == me) if (u and me) else (call(t2, "GetName") == my_name):
            continue
        others.append(t2)
    groups = {}
    for grp in call(project, "GetColorGroupsList") or []:
        post = call(grp, "GetPostClipNodeGraph")
        other = {} if can_list else None
        for t2 in others:
            if time.time() - t_start > 30:
                other = None      # not checked (a very large project); apply checks again live
                break
            n = len(call(grp, "GetClipsInTimeline", t2) or [])
            if n:
                nm = call(t2, "GetName") or "?"
                other[nm] = other.get(nm, 0) + n
        groups[call(grp, "GetName") or "?"] = {
            "pre": tools(call(grp, "GetPreClipNodeGraph")), "post": tools(post), "post_luts": luts(post),
            "clips": len(call(grp, "GetClipsInTimeline", tl) or []), "other_timelines": other}
    product = call(resolve, "GetProductName")
    studio = call(resolve, "IsStudio")
    if studio is None:
        studio = "studio" in str(product or "").lower()
    pc = {k: ps(k) for k in ("colorScienceMode", "isAutoColorManage", "colorSpaceTimeline", "colorSpaceOutput",
                             "nodeStackLayers", "videoDataLevels")}
    out = {"schema": 2, "product": product, "version": call(resolve, "GetVersionString"), "studio": bool(studio),
           "project": call(project, "GetName"), "timeline": call(tl, "GetName"),
           "project_uid": call(project, "GetUniqueId"), "timeline_uid": me,
           "screen": [int(num(ts("timelineResolutionWidth"))), int(num(ts("timelineResolutionHeight")))],
           "fps": num(ts("timelineFrameRate")), "drop_frame": str(ts("timelineDropFrameTimecode", "0")).lower() in ("1", "true"),
           "start_timecode": call(tl, "GetStartTimecode"), "input_mismatch": ts("timelineInputResMismatchBehavior"),
           "color_science": pc["colorScienceMode"], "timeline_color_space": pc["colorSpaceTimeline"], "project_color": pc,
           "timeline_nodes": tools(call(tl, "GetNodeGraph")),
           "groups": groups, "items": items, "skipped_items": skipped}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    old = {}
    if os.path.exists(OUT):
        try:
            with open(OUT, encoding="utf-8") as fh:
                old = json.load(fh)
        except Exception:
            old = {}
    # One lab holds one timeline. Two timelines can map to the same lab folder name (names in other scripts, or
    # "Final (IG)" and "Final [IG]"); writing here would hand this timeline the other one's cameras, group and
    # frame cache, so stop instead. A renamed timeline keeps its uid and passes.
    other = None
    for k, label in (("project_uid", "project"), ("timeline_uid", "timeline")):
        if old.get(k) and out.get(k) and old[k] != out[k]:
            other = label
    if other is None and not old.get("timeline_uid") and not old.get("manual"):
        # a lab from an earlier version records no timeline uid: the same timeline still has some of its clips
        old_uids = {i.get("uid") for i in old.get("items") or [] if i.get("uid")}
        if old_uids and not old_uids & {i.get("uid") for i in items if i.get("uid")}:
            other = "timeline: none of its clips are here"
    if other:
        return {"error": "this lab holds project %r timeline %r (another %s); nothing was written. Use another "
                         "lab folder for project %r timeline %r. If it is the same project after an import or a "
                         "restore (.drp, .dra), Resolve gave it new ids: use another lab folder as well, copy the "
                         "final params file into it and run rekey on that copy after dump, check and cache"
                         % (old.get("project"), old.get("timeline"), other, out["project"], out["timeline"]),
                "gate": "other_timeline"}
    for k in KEEP:
        if k in old:
            out[k] = old[k]
    # An edit that moves clips (ripple insert or delete) changes their track_start keys. Params files keep their
    # per-clip trims under the old keys, so record old key -> current key for every clip (found by uid) that moved,
    # through all earlier dumps; a key can then belong to another clip, which the lab sorts out with this map.
    # An untagged entry was most likely written for the oldest layout, so the moves recorded by earlier dumps win
    # over this dump's one-step moves: such a key follows its first clip, is dropped when that clip is back at its
    # first key, and maps to "" (nobody) when that clip is gone.
    prev = {i.get("uid"): i.get("key") for i in old.get("items") or [] if i.get("uid") and i.get("key")}
    prev_uid = {v: k for k, v in prev.items()}
    cur = {i["uid"]: i["key"] for i in items if i.get("uid")}
    moved, chained = {}, set()
    for a, b in (old.get("moved_keys") or {}).items():
        chained.add(a)
        u = prev_uid.get(b) if b else None
        if u in cur:
            if cur[u] != a:
                moved[a] = cur[u]
        else:
            moved[a] = ""
    step = {k0: cur[u] for u, k0 in prev.items() if u in cur and cur[u] != k0}
    for k0, k1 in step.items():
        if k0 not in chained:
            moved[k0] = k1
    out["moved_keys"] = moved
    # camera_overrides are keyed for the layout of the previous dump: move their keys along with their clips
    co = out.get("camera_overrides")
    if isinstance(co, dict) and prev:
        new_co = {}
        for k, v in co.items():
            u = prev_uid.get(k)
            if u in cur:
                new_co[cur[u]] = v
        for k, v in co.items():
            if prev_uid.get(k) not in cur:
                new_co.setdefault(k, v)       # a uid, or a clip that is gone: kept as it is
        out["camera_overrides"] = new_co
    # clips replaced since the previous dump: same key, another shot (its trims and camera override were made for
    # the old one) or the same item pointing at other media
    old_items = {i.get("key"): i for i in old.get("items") or [] if i.get("key")}
    replaced = {}
    for i in items:
        o = old_items.get(i["key"])
        if not o:
            continue
        other_shot = o.get("uid") and i.get("uid") and o["uid"] != i["uid"] and o["uid"] not in cur
        if other_shot or (o.get("uid") == i.get("uid") and o.get("path") and o.get("path") != i.get("path")):
            replaced[i["key"]] = {"was": o.get("name"), "now": i.get("name"), "same_item": not other_shot}
    out["replaced_keys"] = replaced
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(out, indent=1))
    # what changed since the previous dump (empty lists on the first dump)
    added = sorted(i["key"] for i in items if prev and i.get("uid") and i["uid"] not in prev)
    removed = sorted(k for u, k in prev.items() if u not in cur)
    return {"wrote": OUT, "items": len(items), "skipped": skipped, "project": out["project"],
            "timeline": out["timeline"], "color_science": out["color_science"], "studio": out["studio"],
            "moved": len(step), "added": added, "removed": removed, "replaced": sorted(replaced)}

'''

APPLY_BODY = r'''

def _rc_apply(resolve, project, CFG, RESET, MOVE_GROUPS, FORCE, REPLACE_GROUP_LOOK=False, SHARE_GROUP=False):
    def has(o, name):
        try:
            return o is not None and name in dir(o)
        except Exception:
            return False

    def texture_kind(tools):
        # the same as grade_lab.texture_kind: None, "texture", "flc" or "grade"
        names = [str(x).strip().lower() for x in (tools or []) if str(x).strip()]
        if not names:
            return None

        def fx(n, keys):
            return not n.startswith("lut:") and any(k in n for k in keys)
        tfx, lfx = CFG.get("texture_fx") or (), CFG.get("look_fx") or ()
        if all(fx(n, tfx) or fx(n, lfx) for n in names):
            return "flc" if any(fx(n, lfx) for n in names) else "texture"
        return "grade"

    def other_timelines(g):
        # other timelines of this project with clips in color group g (its grades apply to them too)
        out = {}
        if not (has(project, "GetTimelineCount") and has(project, "GetTimelineByIndex")):
            return out
        me = tl.GetUniqueId() if has(tl, "GetUniqueId") else None
        for i in range(1, (project.GetTimelineCount() or 0) + 1):
            t2 = project.GetTimelineByIndex(i)
            if t2 is None:
                continue
            u = t2.GetUniqueId() if has(t2, "GetUniqueId") else None
            if (u == me) if (u and me) else (t2.GetName() == tl.GetName()):
                continue
            n = len(g.GetClipsInTimeline(t2) or [])
            if n:
                out[t2.GetName()] = out.get(t2.GetName(), 0) + n
        return out

    def uid_of(o):
        try:
            return o.GetUniqueId() if has(o, "GetUniqueId") else None
        except Exception:
            return None

    tl = project.GetCurrentTimeline()
    if not tl:
        return {"error": "no current timeline"}
    found = [project.GetName(), tl.GetName()]
    want = [CFG["project"], CFG["timeline"]]
    if CFG.get("timeline_uid") and uid_of(tl):
        # ids when both sides have them: a renamed timeline or project still matches, another one with the same
        # name does not
        same = uid_of(tl) == CFG["timeline_uid"] and (not (CFG.get("project_uid") and uid_of(project))
                                                      or uid_of(project) == CFG["project_uid"])
    else:
        same = found == want
    if not FORCE and not same:
        return {"error": "the open project or timeline is not the one the grade was made for (another one, even if "
                         "the name is the same). Open it, or ask the user before setting FORCE = True",
                "expected": want, "found": found}
    VIDEO_EXT = tuple(CFG.get("video_ext") or ())
    by_uid, by_key, live, media = {}, {}, [], {}
    for t in range(1, (tl.GetTrackCount("video") or 0) + 1):
        track_on = tl.GetIsTrackEnabled("video", t) if has(tl, "GetIsTrackEnabled") else True
        for it in tl.GetItemListInTrack("video", t) or []:
            k = "%d_%d" % (t, it.GetStart())
            by_key[k] = it
            uid = uid_of(it)
            if uid:
                by_uid[uid] = it
            # the clips the dump reads: enabled video clips on enabled tracks
            try:
                m = it.GetMediaPoolItem() if has(it, "GetMediaPoolItem") else None
                if uid and m:
                    media[uid] = str(m.GetClipProperty("File Path") or "")
                on = track_on is not False and not (has(it, "GetClipEnabled") and it.GetClipEnabled() is False)
                if on and m and str(m.GetClipProperty("File Name") or "").lower().endswith(VIDEO_EXT):
                    live.append((k, uid))
            except Exception:
                pass
    # The timeline can change between the dump and this apply. When it is the timeline the grade was read from
    # (some of its clips are here), every graded clip must still be here and no new clip may have come in: a shot
    # replaced at the same place would get the old clip's conversion LUT, and a new one would stay ungraded.
    known = {c.get("uid") for c in CFG["clips"] if c.get("uid")}
    same_tl = bool(known) and ((bool(CFG.get("timeline_uid")) and uid_of(tl) == CFG["timeline_uid"])
                               or any(u in by_uid for u in known))
    if same_tl:
        missing = sorted(c["key"] for c in CFG["clips"] if c.get("uid") and (c["uid"] not in by_uid or (
            c.get("path") and media.get(c["uid"]) and media[c["uid"]] != c["path"])))   # gone, or its media swapped
        new = sorted(k for k, u in live if u and u not in known)
        if missing or new:
            return {"error": "the timeline changed since it was read: clips deleted or replaced %s, clips added or "
                             "replaced %s. Nothing was changed. Read the timeline again (dump-script), check, cache, "
                             "then bake the LUTs with a new TAG and make a new apply snippet" % (missing, new),
                    "gate": "timeline_changed", "unmatched": missing, "new_since_dump": new}
    grp = None
    for g in project.GetColorGroupsList() or []:
        if g.GetName() == CFG["group"]:
            grp = g
    look = str(CFG["look"]).replace("\\", "/")
    folder_name = look.rsplit("/", 2)[-2] if look.count("/") >= 1 else ""

    def ours(p):
        # empty, this exact look LUT, or an earlier TAG's look LUT from the same lab folder
        p = str(p or "").replace("\\", "/")
        return p in ("", "None", look) or (p.endswith("_Look.cube") and ("/%s/" % folder_name) in ("/" + p))
    group_post, shared = None, {}
    if grp is not None:
        shared = other_timelines(grp)
        if shared and not SHARE_GROUP:
            return {"error": "color group %r also holds clips of other timelines (%s). A group's post-clip look is "
                             "shared by all its clips in every timeline, so applying here would change their look "
                             "too. Nothing was changed. Use another group (apply-script TAG --group NAME), or ask "
                             "the user before setting SHARE_GROUP = True" % (CFG["group"], shared),
                    "gate": "group_shared", "other_timelines": shared}
        post = grp.GetPostClipNodeGraph()
        pn = (post.GetNumNodes() or 0) if post else 0
        ptools = [list(post.GetToolsInNode(i) or []) for i in range(1, pn + 1)] if post else []
        plut = (post.GetLUT(1) or "") if pn else ""
        group_post = {"nodes": pn, "tools": ptools, "lut": plut}
        # nodes after node 1 may hold texture ResolveFX (the hand-over puts grain or halation there); apply only
        # replaces node 1's look LUT
        busy = (any(texture_kind(t) == "grade" for t in ptools[1:])
                or any(not str(x).startswith("LUT:") for x in (ptools[0] if ptools else [])) or not ours(plut))
        if busy and not REPLACE_GROUP_LOOK:
            return {"error": "color group %r already has its own post-clip grade; apply would replace or stack on it. "
                             "Nothing was changed. Ask the user before setting REPLACE_GROUP_LOOK = True, or use "
                             "another group" % CFG["group"], "gate": "group_postclip", "group_post": group_post}
    project.RefreshLUTList()
    applied, not_clean, in_other_group, unmatched, todo = [], [], [], [], []
    for c in CFG["clips"]:
        it, how = (by_uid.get(c["uid"]), "uid") if c.get("uid") else (None, "")
        if it is None and (not c.get("uid") or not same_tl):
            it, how = by_key.get(c["key"]), "key"      # no uids, or another copy of the timeline (FORCE): by place
        if it is None:
            unmatched.append(c["key"])
            continue
        g = it.GetNodeGraph()
        n = g.GetNumNodes() if g else 0
        node_tools = [list(g.GetToolsInNode(i) or []) for i in range(1, n + 1)] if g else []
        clean = n == 1 and all(str(x).startswith("LUT:") for x in node_tools[0])
        cg = it.GetColorGroup()
        cgn = cg.GetName() if cg else None
        if not clean and not RESET:
            not_clean.append([c["key"], n, node_tools])
            continue
        if cgn not in (None, CFG["group"]) and not MOVE_GROUPS:
            in_other_group.append([c["key"], cgn])
            continue
        todo.append((c, it, how, clean))
    if todo and grp is None:
        grp = project.AddColorGroup(CFG["group"])
    for c, it, how, clean in todo:
        if not clean:
            it.GetNodeGraph().ResetAllGrades()
        ok = bool(it.GetNodeGraph().SetLUT(1, c["lut"]))
        cg = it.GetColorGroup()
        grouped = (cg is not None and cg.GetName() == CFG["group"]) or bool(grp is not None and it.AssignToColorGroup(grp))
        applied.append([c["key"], how, ok, grouped])
    look_ok, look_lut = None, None
    if todo and grp is not None:
        post = grp.GetPostClipNodeGraph()
        look_ok = bool(post.SetLUT(1, CFG["look"])) if post else False
        look_lut = post.GetLUT(1) if post else None
    return {"applied": applied, "not_clean": not_clean, "in_other_group": in_other_group, "unmatched": unmatched,
            "look": look_ok, "look_lut": look_lut, "group": CFG["group"], "group_post_before": group_post,
            "other_timelines": shared, "flags": [RESET, MOVE_GROUPS, FORCE, REPLACE_GROUP_LOOK, SHARE_GROUP]}

'''

GRAB_BODY = r'''

def _rc_grab(resolve, project, FRAMES, OUT, TIMELINE, FORCE, SLEEP, BUDGET=45, LAYOUT=None):
    import time

    def has(o, name):
        try:
            return o is not None and name in dir(o)
        except Exception:
            return False

    def call(o, name, *a):
        try:
            return getattr(o, name)(*a) if has(o, name) else None
        except Exception:
            return None

    L = LAYOUT or {}
    tl = project.GetCurrentTimeline()
    if not tl:
        return {"error": "no current timeline"}
    uid = call(tl, "GetUniqueId")
    same = (uid == L["timeline_uid"]) if (uid and L.get("timeline_uid")) else (tl.GetName() == TIMELINE)
    if not FORCE and not same:
        return {"error": "the open timeline is %r, the grade was made for %r (another timeline, even if the name is "
                         "the same). Open it, or ask the user before setting FORCE = True" % (tl.GetName(), TIMELINE)}
    # The frames are timecodes of the layout the lab read. If clips were added, deleted, replaced, moved or trimmed
    # since then, the grabs would show other clips than the lab renders: stop before touching the page.
    want = {c[1]: c for c in L.get("clips") or [] if c[1]}
    live, ext = {}, tuple(L.get("video_ext") or ())
    for t in range(1, (call(tl, "GetTrackCount", "video") or 0) + 1):
        track_on = call(tl, "GetIsTrackEnabled", "video", t)
        for it in call(tl, "GetItemListInTrack", "video", t) or []:
            m = call(it, "GetMediaPoolItem")
            if track_on is False or call(it, "GetClipEnabled") is False or not m:
                continue
            if str(call(m, "GetClipProperty", "File Name") or "").lower().endswith(ext) and call(it, "GetUniqueId"):
                live[call(it, "GetUniqueId")] = ["%d_%d" % (t, call(it, "GetStart")), call(it, "GetEnd"),
                                                 call(it, "GetSourceStartFrame"),
                                                 call(m, "GetClipProperty", "File Path")]
    if want and ((uid and uid == L.get("timeline_uid")) or any(u in live for u in want)):
        changed = []
        for u, c in want.items():
            now = live.get(u)
            if now is None:
                changed.append("%s gone" % c[0])
            elif now[0] != c[0] or now[1] != c[2] or (now[2] is not None and c[3] is not None and now[2] != c[3]):
                changed.append("%s moved or trimmed" % c[0])
            elif len(c) > 4 and c[4] and now[3] and now[3] != c[4]:
                changed.append("%s media replaced" % c[0])
        changed += ["%s new" % v[0] for u, v in live.items() if u not in want]
        if changed:
            return {"error": "the timeline changed since it was read (%s). Nothing was grabbed. Read the timeline "
                             "again (dump-script); if clips were added, deleted or replaced, bake and apply the grade "
                             "again first" % ", ".join(sorted(changed)[:8]), "gate": "timeline_changed",
                    "changed": sorted(changed)}
    page0, tc0 = resolve.GetCurrentPage(), tl.GetCurrentTimecode()
    grabs, err, t0 = [], None, time.time()
    try:
        resolve.OpenPage("color")
        for f, code in FRAMES:
            if time.time() - t0 > BUDGET:
                break         # stay inside the tool's 60 s limit; the rest is reported as not grabbed
            tl.SetCurrentTimecode(code)
            time.sleep(SLEEP)
            grabs.append([f, bool(project.ExportCurrentFrameAsStill(OUT + "/f_%d.png" % f))])
    except Exception as e:
        err = "%s: %s" % (type(e).__name__, e)
    finally:
        try:
            if tc0:
                tl.SetCurrentTimecode(tc0)
        except Exception:
            pass
        try:
            if page0 and resolve.GetCurrentPage() != page0:
                resolve.OpenPage(page0)
        except Exception:
            pass
    out = {"grabs": grabs, "restored_page": resolve.GetCurrentPage(), "restored_tc": tl.GetCurrentTimecode(),
           "seconds": round(time.time() - t0, 1)}
    missed = [f for f, _ in FRAMES[len(grabs):]]
    if missed:
        out["not_grabbed"] = missed
        if not err:
            out["note"] = ("this computer is slow for %d frames per snippet: run grab-script again with --chunk %d "
                           "(the frames already grabbed are simply grabbed again)"
                           % (len(FRAMES), max(1, len(grabs) // 2)))
    if err:
        out["error"] = err
    return out

'''

BACKUP_BODY = r'''

def _rc_backup(resolve, project, DIR):
    import json, os, time

    def has(o, name):
        try:
            return o is not None and name in dir(o)
        except Exception:
            return False

    def luts(g):
        if not g:
            return None
        return [g.GetLUT(i) or "" for i in range(1, (g.GetNumNodes() or 0) + 1)]

    tl = project.GetCurrentTimeline()
    if not tl:
        return {"error": "no current timeline"}
    stamp = time.strftime("%Y%m%d_%H%M%S")
    os.makedirs(DIR, exist_ok=True)
    drt = DIR + "/before_%s.drt" % stamp
    ok = tl.Export(drt, resolve.EXPORT_DRT, resolve.EXPORT_NONE)
    s = project.GetSettings() if has(project, "GetSettings") else {}
    try:
        layers = int((s or {}).get("nodeStackLayers") or 1)
    except Exception:
        layers = 1
    snap = {"project": project.GetName(), "timeline": tl.GetName(), "stamp": stamp, "drt": drt, "groups": {}, "items": []}
    for grp in project.GetColorGroupsList() or []:
        snap["groups"][grp.GetName()] = {"pre_luts": luts(grp.GetPreClipNodeGraph()),
                                         "post_luts": luts(grp.GetPostClipNodeGraph())}
    for t in range(1, (tl.GetTrackCount("video") or 0) + 1):
        for it in tl.GetItemListInTrack("video", t) or []:
            if not it.GetMediaPoolItem():
                continue
            cg = it.GetColorGroup()
            ver = it.GetCurrentVersion() if has(it, "GetCurrentVersion") else None
            snap["items"].append({"uid": it.GetUniqueId() if has(it, "GetUniqueId") else None,
                                  "key": "%d_%d" % (t, it.GetStart()), "name": it.GetName(),
                                  "version": ver.get("versionName") if isinstance(ver, dict) else None,
                                  "group": cg.GetName() if cg else None,
                                  "layers": [luts(it.GetNodeGraph(L)) for L in range(1, layers + 1)]})
    path = DIR + "/snapshot_%s.json" % stamp
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(snap, fh, indent=1)
    return {"drt": drt, "drt_ok": bool(ok), "snapshot": path, "items": len(snap["items"])}

'''


# ResolveFX that only add texture (the hand-over's halation, diffusion or glow, sharpening, vignette and grain). A
# node that holds nothing else may sit after the grade: on the Timeline node or after the look node in the group's
# post-clip graph. Matched by tool name, lower case. Film Look Creator bundles texture with color and contrast
# controls, so a node with it is reported as a warning rather than information.
TEXTURE_FX = ("grain", "halation", "glow", "bloom", "diffusion", "mist", "sharpen", "soften", "blur", "vignette",
              "lens reflection", "lens flare", "film damage", "chromatic aberration", "noise reduction", "deband")
LOOK_FX = ("film look creator",)


def texture_kind(tools, texture_fx=TEXTURE_FX, look_fx=LOOK_FX):
    """What one node holds, from its GetToolsInNode list: None (nothing), "texture" (only texture ResolveFX),
    "flc" (texture plus Film Look Creator) or "grade" (anything else, LUTs included). The apply snippet carries a
    copy of this function (APPLY_BODY); keep the two the same."""
    names = [str(x).strip().lower() for x in (tools or []) if str(x).strip()]
    if not names:
        return None

    def fx(n, keys):
        return not n.startswith("lut:") and any(k in n for k in keys)
    if all(fx(n, texture_fx) or fx(n, look_fx) for n in names):
        return "flc" if any(fx(n, look_fx) for n in names) else "texture"
    return "grade"


def legacy_group(T):
    """The color group name the first versions used for every timeline of a project (shared, which is the bug)."""
    return "%s grade" % T.lut_folder


def default_group(T):
    """One color group per timeline: a group's pre-clip and post-clip grades are project wide, so a group shared by
    two timelines would give both the look of whichever was graded last."""
    return "%s %s grade" % (T.lut_folder, T.d.get("timeline") or "timeline")


def group_post_problem(T, name):
    """None when color group `name`'s post-clip graph is empty or holds only this lab's look LUT on node 1 (an
    earlier TAG from the same LUT folder) plus texture nodes after it, else (level, text) for check."""
    g = (T.d.get("groups") or {}).get(name) or {}
    post = [list(n or []) for n in (g.get("post") or [])]
    luts = g.get("post_luts") or []
    if not any(post) and not any(luts):
        return None
    first = post[0] if post else []
    if any(texture_kind(n) == "grade" for n in post[1:]) or any(not str(x).startswith("LUT:") for x in first):
        return "WARN", ("color group \"%s\" has its own post-clip grade %s; apply puts the look LUT on its node 1 "
                        "and would replace or stack on it, so the apply snippet stops unless REPLACE_GROUP_LOOK "
                        "is set after asking the user" % (name, post))
    lut = str((luts or [""])[0] or "").replace("\\", "/")
    mine = safe_name(T.lut_folder, keep_space=True)
    if first and not (lut.endswith("_Look.cube") and ("/%s/" % mine) in ("/" + lut)):
        return "WARN", ("color group \"%s\" has the LUT %s on its post-clip node; apply would replace it with the "
                        "look LUT, so the apply snippet stops unless REPLACE_GROUP_LOOK is set after asking the "
                        "user" % (name, lut or first))
    return None     # empty, or an earlier look LUT from this lab's folder: apply simply replaces it


def group_shared(T, name):
    """{other timeline: clips} of color group `name` from the dump ({} when none, None when not checked)."""
    return ((T.d.get("groups") or {}).get(name) or {}).get("other_timelines")


def group_for(T, group=None):
    """(group name, where it came from). --group wins, then "group" in timeline.json (recorded by apply-script,
    kept across re-dumps), then the old shared name when this timeline's clips already sit in it (a lab graded by
    an earlier version keeps its group), then the group every clip of this timeline already sits in when this
    skill graded it (its post-clip node holds a look LUT from this lab's LUT folder) and no other timeline uses it,
    else the per-timeline default."""
    if group:
        return group, "--group"
    if T.d.get("group"):
        return T.d["group"], "timeline.json"
    old = legacy_group(T)
    if any(it.get("color_group") == old for it in T.items):
        return old, "earlier grade"
    names = {it.get("color_group") for it in T.items}
    if len(names) == 1 and None not in names:
        g = next(iter(names))
        post = ((T.d.get("groups") or {}).get(g) or {}).get("post_luts") or []
        if (group_shared(T, g) == {} and group_post_problem(T, g) is None
                and str((post or [""])[0] or "").endswith("_Look.cube")):
            return g, "in use"
    return default_group(T), "default"


GROUP_HOW = {"--group": "from --group", "timeline.json": "recorded in timeline.json",
             "earlier grade": "the group an earlier version of this skill used for these clips",
             "in use": "the group this timeline's clips already sit in, graded by this skill",
             "default": "one group per timeline"}


def record_group(T, name):
    """Remember the group in timeline.json, unless the dump shows it as shared with other timelines or holding its
    own post-clip grade: the apply snippet refuses those without the user's yes, and a remembered group would be
    offered again by every later apply-script. Returns the reason it was not recorded, or None."""
    sh, prob = group_shared(T, name), group_post_problem(T, name)
    if sh or prob:
        return ("it also holds clips of other timelines %s" % sh) if sh else "it has its own post-clip grade or LUT"
    fn = os.path.join(T.lab, "timeline.json")
    d = load_json(fn)
    if d.get("group") != name:
        d["group"] = name
        write_json(fn, d)
    T.d["group"] = name
    return None


def cmd_find_lab(uid, root=None):
    """Print the labs whose timeline.json belongs to the timeline with this uid (a renamed timeline or project gets
    a new lab folder name; its grade history is in the old folder)."""
    root = os.path.abspath(os.path.expanduser(root or os.path.join("~", "resolve-colorist-labs")))
    hits = []
    for fn in sorted(glob.glob(os.path.join(glob.escape(root), "*", "timeline.json"))):
        try:
            d = load_json(fn)
        except (OSError, ValueError):
            continue
        if isinstance(d, dict) and uid and d.get("timeline_uid") == uid:
            hits.append((os.path.dirname(fn), d.get("project"), d.get("timeline")))
    for lab, pr, tl in hits:
        print("LAB %s  (project %s, timeline %s)" % (lab, json.dumps(pr, ensure_ascii=False), json.dumps(tl, ensure_ascii=False)))
    if not hits:
        print("no lab in %s holds timeline uid %s" % (root, uid))


def cmd_dump_script(lab):
    lab = os.path.abspath(lab)
    os.makedirs(lab, exist_ok=True)
    text = (snippet_head("dump", None, "Read only: writes timeline.json into the lab, changes nothing in Resolve.")
            + "OUT = r\"%s\"\n" % posix(os.path.join(lab, "timeline.json")) + DUMP_BODY
            + "result = _rc_dump(resolve, project, OUT)\n")
    write_snippet(lab, "dump.py", text)


def cmd_backup_script(T):
    d = os.path.join(T.lab, "backup")
    os.makedirs(d, exist_ok=True)
    text = (snippet_head("backup", T, "Changes nothing: exports the timeline as .drt plus a JSON snapshot of every "
                                      "node LUT into the lab's backup folder.")
            + "DIR = r\"%s\"\n" % posix(d) + BACKUP_BODY + "result = _rc_backup(resolve, project, DIR)\n")
    write_snippet(T.lab, "backup.py", text)
    print("restore: in Resolve use File > Import > Timeline and pick the .drt, or re-apply the LUT paths listed in "
          "the snapshot json")


def lut_map(T, tag):
    fn = os.path.join(T.lab, "luts_%s.json" % tag)
    if not os.path.exists(fn):
        raise Fail("no luts_%s.json in the lab: run luts PARAMS %s first" % (tag, tag))
    mp = load_json(fn)
    if "clips" not in mp:   # v1 map: {key: path, "look": path}
        mp = {"tag": tag, "lut_root": None, "folder": os.path.dirname(mp["look"]), "conv_size": 33, "look": mp["look"],
              "clips": {k: {"lut": v, "uid": (T.by_key.get(k) or {}).get("uid"), "name": (T.by_key.get(k) or {}).get("name")}
                        for k, v in mp.items() if k != "look"}}
    return mp


def cmd_apply_script(T, tag, group=None):
    check_tag(tag)
    mp = lut_map(T, tag)
    group, how = group_for(T, group)
    why_not = record_group(T, group) if how != "timeline.json" else None
    print("color group: %s (%s)%s" % (group, GROUP_HOW[how], "" if not why_not else
                                      "; not recorded in timeline.json because %s: if the user agrees to use it and "
                                      "the apply succeeds, pass --group again next time" % why_not))
    clips = [{"key": k, "uid": v.get("uid") or (T.by_key.get(k) or {}).get("uid"), "lut": posix(v["lut"]),
              "name": v.get("name"), "path": v.get("path")} for k, v in mp["clips"].items()]
    if T.d.get("manual"):
        lines = ["MANUAL APPLY (the free Resolve has no scripting). Do this in Resolve:",
                 "1. Project Settings > Color Management > Lookup Tables > Open LUT Folder: copy the folder %s into it, "
                 "then click Update Lists." % mp["folder"],
                 "2. Color page, for each clip: right-click node 1 > LUT > %s > the file for that clip:"
                 % os.path.basename(mp["folder"])]
        for c in clips:
            lines.append("     %-12s %-24s %s" % (c["key"], c.get("name") or "", os.path.basename(c["lut"])))
        lines += ["3. Put all these clips in one new color group, for example \"%s\" (right-click a thumbnail > "
                  "Groups > Add into a New Group). Do not reuse a group that holds clips of another timeline: a "
                  "group's look is shared by all its clips in every timeline. Switch the node editor to Group "
                  "Post-Clip and put the look LUT %s on its node." % (group, os.path.basename(mp["look"])),
                  "4. Project Settings > Color Management > 3D Lookup Table Interpolation: Tetrahedral.",
                  "Undo: remove the LUT from node 1 of each clip and from the group's post-clip node."]
        fn = os.path.join(T.lab, "apply_%s_manual.txt" % tag)
        with open(fn, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        print("\n".join(lines))
        print("wrote", fn)
        return
    cfg = {"project": T.d.get("project"), "timeline": T.d.get("timeline"), "project_uid": T.d.get("project_uid"),
           "timeline_uid": T.d.get("timeline_uid"), "group": group, "look": posix(mp["look"]), "clips": clips,
           "texture_fx": list(TEXTURE_FX), "look_fx": list(LOOK_FX), "video_ext": list(VIDEO_EXT)}
    text = (snippet_head("apply %s" % tag, T, "Puts the conversion LUTs on node 1 of each graded clip, assigns the "
                                               "clips to the color group and the look LUT to the group's post-clip node.")
            + "RESET = False        # ask the user before setting True: resets the grade of clips whose node graph is not clean\n"
            + "MOVE_GROUPS = False  # ask the user before setting True: moves clips out of another color group into this one\n"
            + "FORCE = False        # ask the user before setting True: applies even if the project or timeline is another one\n"
            + "REPLACE_GROUP_LOOK = False  # ask the user before setting True: the color group already has its own "
              "post-clip grade and the look LUT goes on its node 1\n"
            + "SHARE_GROUP = False  # ask the user before setting True: the color group also holds clips of other "
              "timelines, whose look changes too\n"
            + "CFG = %r\n" % (cfg,) + APPLY_BODY
            + "result = _rc_apply(resolve, project, CFG, RESET, MOVE_GROUPS, FORCE, REPLACE_GROUP_LOOK, SHARE_GROUP)\n")
    write_snippet(T.lab, "apply_%s.py" % tag, text)
    if mp.get("lut_root") is None:
        print("note: these LUTs were written with --out; SetLUT only finds LUTs inside Resolve's LUT folder or a "
              "Custom LUT path")


def tc(frame, fps, drop=False):
    f = int(round(fps))
    frame = int(frame)
    if drop and f in (30, 60):
        d = 2 if f == 30 else 4
        per10, per1 = f * 600 - 9 * d, f * 60 - d
        tens, rem = divmod(frame, per10)
        frame += 9 * d * tens + (d * ((rem - d) // per1) if rem > d else 0)
    s, fr = divmod(frame, f)
    return "%02d:%02d:%02d%s%02d" % (s // 3600, (s // 60) % 60, s % 60, ";" if drop and f in (30, 60) else ":", fr)


def cmd_grab_script(T, name, chunk=15):
    name = safe_name(name)
    chunk = max(1, min(35, int(chunk)))
    out = os.path.join(T.lab, name)
    os.makedirs(out, exist_ok=True)
    drop = bool(T.d.get("drop_frame"))
    frames = [[t, tc(t, T.fps, drop)] for t in T.slices]
    parts = [frames[i:i + chunk] for i in range(0, len(frames), chunk)] or [[]]
    # the layout the frames belong to: the snippet stops if the timeline changed since the dump
    layout = {"timeline_uid": T.d.get("timeline_uid"), "video_ext": list(VIDEO_EXT),
              "clips": [[it["key"], it.get("uid"), it.get("end"), it.get("src_start"), it.get("path")] for it in T.items]}
    for n, part in enumerate(parts, 1):
        text = (snippet_head("grab %s %d/%d" % (name, n, len(parts)), T,
                             "Exports the listed frames as stills into the lab. It opens the Color page and moves the "
                             "playhead, then puts both back.")
                + "FORCE = False  # ask the user before setting True: grabs even if the open timeline is another one\n"
                + "SLEEP = 1.2\nBUDGET = 45  # seconds; frames left after that are listed in the result\n"
                + "FRAMES = %r\nOUT = r\"%s\"\nTIMELINE = %r\nLAYOUT = %r\n" % (part, posix(out), T.d.get("timeline"), layout)
                + GRAB_BODY + "result = _rc_grab(resolve, project, FRAMES, OUT, TIMELINE, FORCE, SLEEP, BUDGET, LAYOUT)\n")
        write_snippet(T.lab, "grab_%s_%02d.py" % (name, n), text)
    if len(parts) > 1:
        print("run the %d snippets one after the other (each stays under the 60 s tool limit; pass timeout 60)" % len(parts))
    if int(round(T.fps)) != T.fps and "drop_frame" not in T.d:
        print("# WARNING: fractional fps and no drop_frame info (old timeline.json): verify the timecodes land on "
              "the right frames, or run dump-script again.")


# ---------------------------------------------------------------- compare
def compose(T, t, graded, k, mode=None):
    canvas = np.zeros((int(T.H * k), int(T.W * k), 3), np.float32)
    for it in sorted(T.active(t), key=lambda i: i["track"]):
        img = graded[it["key"]]
        if it.get("flip_x"):
            img = img[:, ::-1]
        if it.get("flip_y"):
            img = img[::-1]
        x0, y0, w, h = T.rect(it, k, mode)
        paste(canvas, resize(img, w, h), int(round(x0)), int(round(y0)))
    return canvas


def _panel_rows(T, si, t, res, lab_img, mode=None):
    rows = []
    for it in T.active(t):
        u0, v0, u1, v1 = T.visible_box(it, t, mode)
        x0, y0, w, h = T.rect(it, mode=mode)
        X0, X1 = int(max(0, x0 + u0 * w)), int(min(T.W, x0 + u1 * w))
        Y0, Y1 = int(max(0, y0 + v0 * h)), int(min(T.H, y0 + v1 * h))
        mx, my = (X1 - X0) // 20, (Y1 - Y0) // 20
        R = res[Y0 + my:Y1 - my, X0 + mx:X1 - mx].reshape(-1, 3)
        Lb = lab_img[Y0 + my:Y1 - my, X0 + mx:X1 - mx].reshape(-1, 3)
        if len(R) < 100:
            continue
        yr, yl = float((R @ LUMA).mean()), float((Lb @ LUMA).mean())
        labr, labl = to_lab(R), to_lab(Lb)
        ar, al = labr[:, 1:].mean(0), labl[:, 1:].mean(0)
        with np.errstate(invalid="ignore", divide="ignore"):
            corr = float(np.corrcoef(R @ LUMA, Lb @ LUMA)[0, 1])
        dab = ar - al
        rows.append({"slice": si, "frame": t, "key": it["key"], "name": T.short(it["key"]), "resolve_Y": round(yr, 3),
                     "lab_Y": round(yl, 3), "dY": round(yr - yl, 3), "resolve_ab": [round(float(v), 1) for v in ar],
                     "lab_ab": [round(float(v), 1) for v in al], "pixel_corr": round(corr, 3),
                     "dab": [round(float(v), 2) for v in dab],
                     "dE_ab": round(float(np.linalg.norm(labr.mean(0) - labl.mean(0))), 2)})
    return rows


def cmd_compare(T, pfile, name, out=None, calibrate=False):
    from PIL import Image
    P = params(pfile)
    gdir = os.path.join(T.lab, name)
    frames = [(si, t) for si, t in enumerate(T.slices) if os.path.exists(os.path.join(gdir, "f_%d.png" % t))]
    if not frames:
        raise Fail("no Resolve grabs (f_<frame>.png) in %s: run grab-script %s first" % (gdir, name))
    gains, _, _ = gains_all(T, P)
    modes = [T.base_mode] + ([m for m in ("fill", "fit", "none") if m != T.base_mode] if calibrate else [])

    def job(ft):
        si, t = ft
        res = np.asarray(Image.open(os.path.join(gdir, "f_%d.png" % t)).convert("RGB").resize((T.W, T.H)), np.float32) / 255
        graded = {it["key"]: grade(load(T, "slice_%d_%s" % (si, it["key"])), it, P, gains[it["key"]]) for it in T.active(t)}
        return {m: _panel_rows(T, si, t, res, compose(T, t, graded, 1.0, m), m) for m in modes}
    per = pmap(job, frames, min(grade_workers(), 6))
    rows = [r for p in per for r in p[modes[0]]]
    if not rows:
        raise Fail("no panel large enough to compare")
    bad = []
    for r in rows:
        dab = math.hypot(*r["dab"])
        why = []
        if r["pixel_corr"] < 0.9 or r["pixel_corr"] != r["pixel_corr"]:
            why.append("layout or animation differs from the static timeline.json (low corr)")
        else:
            if abs(r["dY"]) > 0.02:
                why.append("brightness differs although the picture lines up: a grade mismatch")
            if dab > 2.5:
                why.append("color differs although the picture lines up: a grade mismatch")
        if why:
            r["flag"] = "; ".join(why)
            bad.append(r)
    for r in rows:
        print("s%-2d %-9s %-14s Y res %.3f lab %.3f d%+.3f  ab res %s lab %s corr %.3f  dab %+.1f %+.1f" % (
            r["slice"], r["key"], r["name"], r["resolve_Y"], r["lab_Y"], r["dY"], r["resolve_ab"], r["lab_ab"],
            r["pixel_corr"], r["dab"][0], r["dab"][1]))
    dys = [abs(r["dY"]) for r in rows]
    good = [r for r in rows if r["pixel_corr"] >= 0.9] or rows
    da, db = float(np.mean([r["dab"][0] for r in good])), float(np.mean([r["dab"][1] for r in good]))
    mab = float(np.mean([math.hypot(*r["dab"]) for r in good]))
    print("\npanels %d  mean|dY| %.4f  max|dY| %.4f  flagged %d" % (len(rows), np.mean(dys), np.max(dys), len(bad)))
    print("chroma  mean da %+.2f  db %+.2f  mean|dab| %.2f" % (da, db, mab))
    for r in bad:
        print("FLAG", r["key"], r["name"], "slice", r["slice"], "dY", r["dY"], "corr", r["pixel_corr"], "dab", r["dab"],
              "(%s)" % r["flag"])
    summary = {"panels": len(rows), "mean_abs_dY": round(float(np.mean(dys)), 4), "max_abs_dY": round(float(np.max(dys)), 4),
               "mean_da": round(da, 2), "mean_db": round(db, 2), "mean_abs_dab": round(mab, 2),
               "chroma_panels": len(good), "flagged": len(bad), "base_scale": T.base_mode,
               "base_scale_scores": None, "suggested_base_scale": None}
    if calibrate:
        scores = {}
        for m in modes:
            cs = [r["pixel_corr"] for p in per for r in p[m] if r["pixel_corr"] == r["pixel_corr"]]
            scores[m] = round(float(np.median(cs)), 3) if cs else None
        best = max((m for m in scores if scores[m] is not None), key=lambda m: scores[m])
        summary["base_scale_scores"], summary["suggested_base_scale"] = scores, best
        print("base_scale  " + "  ".join("%s %s" % (m, scores[m]) for m in ("fill", "fit", "none")) +
              "  -> suggested %s (timeline.json uses %s)" % (best, T.base_mode))
        if best != T.base_mode:
            print("set \"base_scale\": \"%s\" in timeline.json and run compare again" % best)
    out = out or os.path.join(gdir, "compare.json")
    write_json(out, {"summary": summary, "rows": rows, "flagged": bad})


# ---------------------------------------------------------------- check
def cmd_check(T):
    probed = not os.path.exists(T.sources_path)
    if probed:
        sys.stderr.write("no sources.json yet: probing the source files first\n")
        cmd_probe(T, sys.stderr)
        T = TL(T.lab)
    d = T.d
    gates = []

    def gate(level, gid, msg, clips=None):
        gates.append({"level": level, "id": gid, "message": msg, "clips": list(clips or [])})
    pcol = d.get("project_color") or {}
    cs = d.get("color_science") or pcol.get("colorScienceMode")
    if cs and cs != "davinciYRGB":
        gate("STOP", "color_science", "the project uses %s, not DaVinci YRGB. The baked LUTs assume YRGB; never switch "
             "it on this project. Offer to grade a duplicate project set to DaVinci YRGB instead." % cs)
    tn = [x for x in (d.get("timeline_nodes") or []) if x]
    tk = [texture_kind(x) for x in tn]
    if "grade" in tk:
        gate("STOP", "timeline_grade", "the timeline itself has a grade (%s). It would sit on top of the lab's result; "
             "ask the user to remove or bypass it first." % tn)
    elif tn:
        gate("WARN" if "flc" in tk else "INFO", "timeline_texture", "the Timeline node holds texture effects only "
             "(%s), as the hand-over suggests; apply leaves them alone. The grabs include them, so compare can differ "
             "a little in bright areas (glow, halation) and flag grainy panels with lower correlation.%s" % (
                 tn, " Film Look Creator can also change color and contrast: ask the user to keep only its texture "
                 "parts on, or its color controls add to the look." if "flc" in tk else ""))
    il = [it["key"] for it in T.items if str(it.get("input_lut") or "").strip() not in ("", "None")
          or str(it.get("idt") or "").strip() not in ("", "None")]
    if il:
        gate("STOP", "input_lut", "clips have an Input LUT or IDT in their clip attributes; node 1 would no longer see "
             "the camera signal. Ask the user to clear them.", il)
    raw = [it["key"] for it in T.items if it["path"].lower().endswith(RAW_EXT) or (T.probe_for(it) or {}).get("is_raw")]
    if raw:
        gate("STOP", "raw_media", "RAW clips are decoded by Resolve; the lab cannot read them. Transcode them to a log "
             "ProRes or grade them by hand.", raw)
    unknown, unsup, low, approx, disp = [], [], [], [], []
    msgs = []
    for it in T.items:
        if it["key"] in raw:
            continue
        det = T.detect_for(it)
        if it["camera_from"] == "default":
            unknown.append(it["key"])
            if det.get("note"):
                msgs.append("%s: %s" % (it["key"], det["note"]))
            continue
        try:
            e = camera_entry(it["camera"])
        except Fail as f:
            unsup.append(it["key"])
            msgs.append("%s: %s" % (it["key"], f))
            continue
        if it["camera_from"] == "detected" and det.get("confidence") == "low":
            low.append(it["key"])
        if e.get("status") == "approximate":
            approx.append(it["key"])
        if e.get("kind") == "sdr":
            disp.append(it["key"])
    forced = []
    for it in T.items:
        det = T.detect_for(it)
        if (it["key"] not in raw and it["camera_from"] in ("timeline", "override") and det.get("key")
                and det.get("confidence") == "high" and det.get("status") != "unsupported"
                and det["key"] != it["camera"]):
            forced.append((it["key"], it["camera"], det["key"], it["camera_from"]))
    if forced:
        gate("WARN", "camera_forced", "the camera set in timeline.json (%s) differs from a high-confidence detection "
             "of the file (%s). A wrong camera decodes the clip wrongly; confirm with the user which is right (a clip "
             "replaced or added since the camera was set is the usual cause)." % (
                 ", ".join(sorted({"%s from %s" % (f[1], "camera_overrides" if f[3] == "override" else "\"camera\"")
                                   for f in forced})),
                 ", ".join("%s detected %s" % (f[0], f[2]) for f in forced[:6])), [f[0] for f in forced])
    rep = d.get("replaced_keys") or {}
    if rep:
        gate("WARN", "clip_replaced", "these clips changed since the previous dump: another shot sits at the same place "
             "(%s), or the item points at other media. A camera_overrides entry under that key, and a clip_overrides "
             "entry without a clip uid (made by hand or by an older version), were made for the old shot and now "
             "apply to the new one; an entry that match or rekey tagged with the old clip's uid applies to nothing "
             "now, so a new shot gets only the auto balance (when the same item points at other media, its tagged "
             "entry still applies). Look at the clip in the baseline render: reset an entry that does not fit, and "
             "run match to trim the new shot." % ", ".join("%s: %s -> %s" % (k, v.get("was"), v.get("now")) for k, v
                                                           in sorted(rep.items())[:6]), sorted(rep))
    if any(T.moved.values()):
        now = {k1 for k1 in T.moved.values() if k1}
        gate("INFO", "clips_moved", "an edit moved %d clip(s) since the grade was first read (new keys %s). Per-clip "
             "trims in params files follow them automatically; before editing clip_overrides by hand, run `rekey "
             "PARAMS OUT` so every entry sits under its clip's current key." % (
                 len(now), ", ".join(sorted(now)[:8]) + (" ..." if len(now) > 8 else "")), sorted(now))
    if unknown:
        gate("STOP", "camera_unknown", "no camera could be detected. Look at a frame, then set camera_overrides in "
             "timeline.json (keys from `cameras.py list`). " + " ".join(msgs[:3]), unknown)
    if unsup:
        gate("STOP", "camera_unsupported", "unsupported camera format. " + " ".join(sorted(set(
            m.split(": ", 1)[1] for m in msgs if m.split(":")[0] in unsup))[:2]), unsup)
    root, how = lut_root_for(T)
    prob = lut_root_problem(root, os.path.join(root, safe_name(T.lut_folder, keep_space=True)))
    if prob:
        gate("WARN" if d.get("manual") else "STOP", "lut_root", "%s (from %s). %s" % (prob, how, LUT_ROOT_ADVICE))
    if d.get("manual") or d.get("studio") is False:
        gate("WARN", "not_studio", "not Resolve Studio: manual mode. The lab makes the LUTs (luts --out) and written "
             "steps; nothing can be applied or verified automatically.")
    ng = []
    for it in T.items:
        nt = it.get("node_tools")
        if it.get("nodes") is None and nt is None:
            continue
        if it.get("nodes") != 1 or any(not str(x).startswith("LUT:") for x in (nt or [[]])[0]):
            ng.append(it["key"])
    if ng:
        gate("WARN", "node_graph", "clips already carry a grade that is not a single LUT node. apply skips them; resetting "
             "them (RESET) destroys that grade, so ask the user first.", ng)
    rl = [it for it in T.items if it["key"] not in ng and (it.get("node_luts") or [""])[0]
          and "_Conv_" not in os.path.basename(str(it["node_luts"][0]).replace("\\", "/"))]
    if rl:
        gate("INFO", "replaces_lut", "node 1 of these clips holds a LUT that this skill did not make (%s). apply puts "
             "the conversion LUT in its place; tell the user before applying." % sorted(
                 {str(it["node_luts"][0]) for it in rl})[:4], [it["key"] for it in rl])
    target, target_how = group_for(T)
    groups = d.get("groups") or {}

    def group_post(name):
        return group_post_problem(T, name)

    def shared(name):
        return group_shared(T, name) or {}
    og = [it for it in T.items if it.get("color_group") not in (None, target)]
    names = sorted({it["color_group"] for it in og})
    if og:
        tip = ""
        if len(names) == 1:
            tip = " To keep them where they are, use apply-script TAG --group \"%s\"." % names[0]
            gp = group_post(names[0])
            if gp:
                tip += " Note: " + gp[1] + "."
            if shared(names[0]):
                tip += (" Note: that group also holds clips of other timelines %s, so apply stops there unless "
                        "SHARE_GROUP is set after asking the user." % shared(names[0]))
        gate("WARN", "other_group", "clips are in color group(s) %s, not \"%s\". apply skips them unless MOVE_GROUPS is "
             "set after asking the user.%s" % (", ".join(names), target, tip), [it["key"] for it in og])
    if shared(target):
        why = GROUP_HOW[target_how]
        fresh = default_group(T) if target != default_group(T) else target + " 2"
        inside = [it["key"] for it in T.items if it.get("color_group") == target]
        gate("WARN", "group_shared", "color group \"%s\" (%s) also holds clips of other timelines %s. A group's "
             "post-clip look is shared by all its clips in every timeline, so grading this timeline there would change "
             "the look of those too. apply stops unless SHARE_GROUP is set after asking the user; otherwise use a new "
             "group: apply-script TAG --group \"%s\"%s." % (
                 target, why, shared(target), fresh,
                 " (this timeline's clips already sit in \"%s\", so moving them needs MOVE_GROUPS, after asking the "
                 "user)" % target if inside else ""), inside)
    for name in [target] + names:
        gp = group_post(name)
        if gp:
            gate(gp[0], "group_postclip", gp[1] + ".",
                 [it["key"] for it in T.items if it.get("color_group") == name])
    for name in [target] + names:
        post = [list(n or []) for n in ((groups.get(name) or {}).get("post") or [])]
        kinds = [texture_kind(n) for n in post[1:]]
        if not group_post(name) and any(k in ("texture", "flc") for k in kinds):
            gate("WARN" if "flc" in kinds else "INFO", "group_texture", "color group \"%s\" has texture effects after "
                 "its look node (%s); apply only replaces the look LUT on node 1 and leaves them. The grabs include "
                 "them, so compare can differ a little in bright or grainy areas.%s" % (
                     name, post[1:], " Film Look Creator can also change color and contrast: ask the user to keep "
                     "only its texture parts on." if "flc" in kinds else ""))
    lay = [it["key"] for it in T.items if any(any(node) for node in (it.get("layer_tools") or []))]
    try:
        nlayers = int(float(str(pcol.get("nodeStackLayers") or 1).split()[0]))
    except (ValueError, IndexError):
        nlayers = 1
    if lay:
        gate("WARN", "node_layers", "these clips have a grade on node stack layer 2 or higher. It is mixed with or sits "
             "on top of the conversion LUT on layer 1, which apply does not see or change; ask the user to remove or "
             "disable it (or to agree that it stays).", lay)
    elif nlayers > 1 and not any("layer_tools" in it for it in T.items):
        gate("INFO", "node_layers", "the project uses %d node stack layers and this timeline.json does not list "
             "them: run dump-script again so check can see grades on the other layers." % nlayers)
    for gname, g in (d.get("groups") or {}).items():
        if gname in ({target} | {it.get("color_group") for it in T.items}) and any(x for x in g.get("pre") or []):
            gate("WARN", "group_preclip", "color group \"%s\" has a pre-clip grade %s that runs before the conversion "
                 "LUT; ask the user to clear or bypass it." % (gname, g["pre"]))
    fc = [it["key"] for it in T.items if (it.get("fusion_comps") or 0) > 0]
    if fc:
        gate("INFO", "fusion_comps", "these clips have Fusion compositions (often animation or effects). The lab sees "
             "the plain clip, so compare may flag them with low correlation; look at their grabs.", fc)
    if low:
        gate("WARN", "camera_low_confidence", "camera detected from weak evidence: look at a frame and confirm with "
             "the user, then set camera_overrides if it is wrong.", low)
    if approx:
        gate("WARN", "camera_approximate", "these cameras use an approximate decode (no published formula or a vendor "
             "LUT): tell the user and check the result closely.", approx)
    if disp:
        gate("WARN", "display_referred", "display referred sources (Rec.709, sRGB, P3 or vendor LUT output): graded "
             "without the filmic tone curve. If they are really log, set camera_overrides.", disp)
    if (T.fps and int(round(T.fps)) != T.fps) or d.get("drop_frame"):
        gate("WARN", "fractional_fps", "timeline at %s fps%s: check that the grabs land on the right frames." % (
            T.fps, " with drop-frame timecode" if d.get("drop_frame") else ""))
    if "base_scale" not in d and d.get("input_mismatch") not in (None, "scaleToFill"):
        gate("WARN", "base_scale", "Resolve reports %r for mismatched resolution, which is not always what it does. "
             "After the first grab run compare FINAL NAME --calibrate and set base_scale if it suggests another mode."
             % d.get("input_mismatch"))
    if T.schema < 2:
        gate("WARN", "schema1", "timeline.json comes from the old dump (no uid, data levels, input LUTs, groups). Run "
             "dump-script again before applying.")
    if d.get("skipped_items"):
        gate("INFO", "skipped_items", "%d timeline items are not graded by the lab (titles, graphics, compound clips, "
             "disabled): %s" % (len(d["skipped_items"]), [s[1] for s in d["skipped_items"]][:8]))
    if len(T.items) > 60:
        gate("INFO", "many_clips", "%d clips: renders and grabs take longer; consider grading scene by scene." % len(T.items))
    tcs = d.get("timeline_color_space")
    if tcs and tcs not in ("Rec.709 (Scene)", "Rec.709-A"):
        gate("INFO", "timeline_color_space", "timeline color space is %r, so renders are tagged from it by default. "
             "For web delivery set Color Space Tag Rec.709 and Gamma Tag Rec.709 (Scene) or Rec.709-A in the "
             "render settings' Advanced Settings (1-1-1 tags without a project change)." % tcs)
    if probed:
        gate("INFO", "probe", "probed the source files first; per file cameras are in sources.json (run probe to "
             "see the table)")
    gate("INFO", "tetrahedral", "ask the user to set Project Settings > Color Management > 3D Lookup Table "
         "Interpolation to Tetrahedral (not scriptable).")
    gate("INFO", "project_luts", "the project's Input and Output Lookup Tables must be empty (not readable by script): "
         "ask the user to confirm.")
    result = "STOP" if any(g["level"] == "STOP" for g in gates) else "WARN" if any(g["level"] == "WARN" for g in gates) else "OK"
    write_json(os.path.join(T.lab, "check.json"), {"result": result, "gates": gates})
    print("RESULT: %s" % result)
    for g in gates:
        print("%-4s %-22s %s%s" % (g["level"], g["id"], g["message"],
                                   ("  [clips: %s]" % ", ".join(g["clips"][:12]) + (" ..." if len(g["clips"]) > 12 else ""))
                                   if g["clips"] else ""))


# ---------------------------------------------------------------- manual mode, doctor, presets, social QC, measure
def cmd_init_manual(lab, files, screen=None, fps=None):
    lab = os.path.abspath(lab)
    os.makedirs(lab, exist_ok=True)
    files = [os.path.abspath(f) for f in files]
    missing = [f for f in files if not os.path.exists(f)]
    if missing:
        raise Fail("files not found: %s" % ", ".join(missing))
    probes = pmap(cameras.probe, files)
    fps = float(fps or probes[0].get("fps") or 25.0)
    if screen:
        try:
            W, H = [int(v) for v in screen.lower().split("x")]
        except ValueError:
            raise Fail("--screen must look like 1080x1920")
    else:
        # The lab screen only sizes the previews and the frame cache (LUTs do not depend on it), so a 4K clip gets
        # a 1920 px screen with the same shape: 4 times fewer pixels to grade on every render.
        W, H = int(probes[0].get("width") or 0), int(probes[0].get("height") or 0)
        if not (W and H):
            raise Fail("could not read the picture size of %s: pass --screen WxH" % files[0])
        s = min(1.0, 1920.0 / max(W, H))
        W, H = max(2, int(round(W * s / 2)) * 2), max(2, int(round(H * s / 2)) * 2)
    t = int(round(3600 * fps))
    items = []
    for f, p in zip(files, probes):
        sf = float(p.get("fps") or fps)
        n = max(2, int(round((p.get("duration") or 1.0) * fps)))
        items.append({"key": "1_%d" % t, "track": 1, "name": os.path.basename(f), "path": f, "start": t, "end": t + n,
                      "src_start": 0, "src_end": max(1, int(round((p.get("duration") or 1.0) * sf)) - 1), "fps": sf,
                      "src_w": int(p["width"]), "src_h": int(p["height"]), "pan": 0.0, "tilt": 0.0, "zoom_x": 1.0,
                      "zoom_y": 1.0, "rotation": 0.0, "flip_x": False, "flip_y": False, "crop": [0.0, 0.0, 0.0, 0.0],
                      "opacity": 100.0, "composite": 0, "scaling": 0, "nodes": None, "node_labels": None,
                      "node_tools": None, "node_luts": None, "color_group": None, "fusion_comps": 0,
                      "input_color_space": None, "gamma_notes": None, "camera_type": None, "codec": p.get("codec"),
                      "uid": None, "data_level": "Auto", "input_lut": None, "idt": None, "input_gamma": None,
                      "version": None})
        t += n
    d = {"schema": 2, "manual": True, "product": None, "version": None, "studio": False,
         "project": os.path.basename(lab), "timeline": "manual", "screen": [W, H], "fps": fps, "drop_frame": False,
         "start_timecode": "01:00:00:00", "input_mismatch": "scaleToFill", "color_science": None,
         "timeline_color_space": None, "project_color": {}, "timeline_nodes": [], "groups": {}, "items": items,
         "skipped_items": [], "base_scale": "fill"}
    fn = os.path.join(lab, "timeline.json")
    if os.path.exists(fn):
        old = load_json(fn)
        for k in ("base_scale", "lut_folder", "lut_root", "camera", "camera_overrides", "group"):
            if k in old:
                d[k] = old[k]
    write_json(fn, d)
    srcs = {f: {"probe": p, "detect": cameras.detect(f, p)} for f, p in zip(files, probes)}
    write_json(os.path.join(lab, "sources.json"), {"schema": 1, "created": iso_now(), "sources": srcs})
    print("wrote %s (%d clips, %dx%d at %g fps, manual mode) and sources.json" % (fn, len(items), W, H, fps))
    for f in files:
        de = srcs[f]["detect"]
        print("  %-28s camera %-16s %s %s" % (os.path.basename(f), de.get("key") or "-", de.get("confidence"), de.get("status")))


def cmd_doctor(as_json=False):
    rows = []

    def add(ok, what, detail):
        rows.append({"ok": bool(ok), "check": what, "detail": detail})
    v = sys.version_info
    add(v >= (3, 10), "python", "%d.%d.%d at %s" % (v[0], v[1], v[2], sys.executable) + ("" if v >= (3, 10) else ": need 3.10 or newer"))
    add(True, "numpy", np.__version__)
    try:
        import PIL
        from PIL import ImageFont
        ImageFont.load_default(size=15)
        add(True, "pillow", PIL.__version__)
    except Exception as e:
        add(False, "pillow", "missing or older than 10.1 (%s): run the install script again" % e)
    add(True, "cameras", "%d camera formats (decoder %s)" % (len(cameras.camera_keys()), cameras.DECODER_VERSION))
    for tool in (cameras.FFMPEG, cameras.FFPROBE):
        try:
            out = subprocess.run([tool, "-version"], capture_output=True, encoding="utf-8", errors="replace",
                                 timeout=20).stdout.split("\n")[0]
            add(bool(out), os.path.basename(tool), out or "no output")
        except Exception:
            add(False, os.path.basename(tool), "not found. Install ffmpeg (macOS: brew install ffmpeg, Windows: winget "
                "install Gyan.FFmpeg, Linux: sudo apt install ffmpeg) or set RC_FFMPEG / RC_FFPROBE to the programs")
    root, how = lut_root_for(None)
    prob = lut_root_problem(root)
    add(prob is None, "lut folder", ("%s (%s) is writable" % (root, how)) if prob is None else "%s. %s" % (prob, LUT_ROOT_ADVICE))
    add(os.path.isdir(PRESETS_DIR), "presets", "%d presets" % len(preset_names()) if os.path.isdir(PRESETS_DIR) else "presets folder missing")
    try:
        import importlib.util
        has_colour = importlib.util.find_spec("colour") is not None
    except Exception:
        has_colour = False
    info = "colour-science %s (only the tests use it)" % ("installed" if has_colour else "not installed")
    ok = all(r["ok"] for r in rows)
    print("DOCTOR: %s" % ("OK" if ok else "PROBLEMS"))
    if as_json:
        print(json.dumps({"ok": ok, "checks": rows, "info": info}, indent=1))
        return
    for r in rows:
        print("%-8s %-12s %s" % ("ok" if r["ok"] else "PROBLEM", r["check"], r["detail"]))
    print("info     %s" % info)


def cmd_presets():
    names = preset_names()
    if not names:
        print("no presets folder next to grade_lab.py")
        return
    for n in names:
        d = load_json(os.path.join(PRESETS_DIR, n + ".json"))
        req = d.get("_requires")
        print("%-18s %s%s" % (n, d.get("_doc", ""), ("  (needs %s)" % ", ".join(req)) if req else ""))


def cmd_social_qc(lab, rest):
    script = os.path.join(HERE, "social_qc.py")
    if not os.path.exists(script):
        raise Fail("social_qc.py is missing next to grade_lab.py")
    if not rest:
        raise Fail("usage: social-qc MASTER [social_qc.py options]")
    args = list(rest)
    if "--out" not in args:
        args += ["--out", os.path.join(os.path.abspath(lab), "social_qc")]
    r = subprocess.run([sys.executable, script] + args)
    if r.returncode:
        raise Fail("social_qc.py exited with %d" % r.returncode)


MEASURE_HUES = {"red": [1, 0.05, 0.05], "orange": [1, 0.45, 0.1], "yellow": [1, 0.9, 0.1], "green": [0.1, 1, 0.1],
                "cyan": [0.1, 0.9, 1], "blue": [0.08, 0.15, 1], "magenta": [1, 0.1, 0.9],
                "skin_light": [0.56, 0.36, 0.26], "skin_medium": [0.42, 0.24, 0.15], "skin_deep": [0.30, 0.16, 0.10],
                "foliage": [0.20, 0.30, 0.08], "sky": [0.35, 0.55, 0.9]}


def look_chroma_factor(P):
    """How much the look scales chroma: mean output C*ab over mean input C*ab of the measure colors at mid-tone
    level, at full, half and quarter saturation (1 = unchanged; the house look is about 1.19, clean_pop 1.25,
    film_print 0.90, soft_pastel 0.82)."""
    xs = []
    for c in MEASURE_HUES.values():
        c = np.array(c, float)
        d = c / (c @ LUMA) * 0.45
        xs += [0.45 + s * (d - 0.45) for s in (0.25, 0.5, 1.0)]
    x = np.clip(np.array(xs), 0, 1)
    y = look(x, P)
    return float(np.hypot(*to_lab(y)[:, 1:].T).mean() / np.hypot(*to_lab(x)[:, 1:].T).mean())


def _measure_pipe(lin, P):
    b, r = P["balance"], P["render"]
    tm = r.get("tonemap", "legacy")
    x = np.asarray(lin, float) * trim(b.get("global_temp", 0), b.get("global_tint", 0)) * r["exposure"]
    if tm == "hr":
        x = gamut_compress(x, gamut_limits(np.eye(3)), r.get("gamut_threshold", 0.9))
    return look(_tone_tail(np.maximum(x, 0), r, tm), P)


def cmd_measure(pfile):
    """Look yardstick: an 18 % grey card and color patches (clip gains 1 plus the global trims) through the
    conversion and the look, measured with this file's own Lab conversion."""
    P = params(pfile)
    f = lambda lin: _measure_pipe(np.atleast_2d(lin), P)
    stops = np.arange(-8, 6.01, 1.0)
    g = f(np.repeat((0.18 * 2.0 ** stops)[:, None], 3, 1))
    Y = g @ LUMA
    e = 0.25
    y0, y1 = [float((f(np.full((1, 3), 0.18 * 2 ** s)) @ LUMA)[0]) for s in (-e, e)]
    labg = to_lab(g)
    print("grey out %.3f  slope per stop at grey %.3f  black %s  white %s" % (
        Y[8], (y1 - y0) / (2 * e), [round(float(v), 4) for v in f(np.zeros((1, 3)))[0]],
        [round(float(v), 4) for v in f(np.full((1, 3), 0.18 * 2 ** 10))[0]]))
    print("grey curve Y at -6 -4 -2 0 +2 +4 stops: %s" % [round(float(Y[i]), 3) for i in (2, 4, 6, 8, 10, 12)])
    print("grey a*/b* at -2 0 +2 stops: %s" % [[round(float(labg[i][1]), 2), round(float(labg[i][2]), 2)] for i in (6, 8, 10)])
    fine = 0.18 * 2.0 ** np.linspace(-12, 8, 801)
    curve = f(np.repeat(fine[:, None], 3, 1)).mean(1)

    def ref(lin):
        lg = np.log2(np.maximum(lin, 1e-6) / 0.18)
        return np.stack([np.interp(lg[..., i], np.log2(fine / 0.18), curve) for i in range(3)], -1)
    print("mid level patches (18 % luma) against the neutral tone curve: dL, chroma ratio, hue shift")
    for hn, c in MEASURE_HUES.items():
        c = np.array(c, float)
        lin = c * 0.18 / (c @ LUMA)
        a, b = to_lab(f(lin)[0]), to_lab(ref(lin[None])[0])
        Ca, Cb = math.hypot(a[1], a[2]), math.hypot(b[1], b[2])
        dh = (math.degrees(math.atan2(a[2], a[1]) - math.atan2(b[2], b[1])) + 180) % 360 - 180
        print("  %-12s dL %+6.2f  C x%.2f  dh %+6.1f" % (hn, a[0] - b[0], Ca / max(Cb, 1e-6), dh))
    print("look chroma factor x%.2f (the house look x%.2f)" % (look_chroma_factor(P),
                                                              look_chroma_factor(resolve_params({"schema": 2}))))


# ---------------------------------------------------------------- CLI
def _ap(prog):
    import argparse
    return argparse.ArgumentParser(prog="grade_lab.py LAB " + prog)


def main(argv):
    if len(argv) < 2 or argv[1] in ("-h", "--help"):
        print(__doc__)
        return 0 if len(argv) >= 2 else 1
    if argv[1] in NO_LAB:
        lab, cmd, rest = None, argv[1], argv[2:]
    else:
        if len(argv) < 3:
            print(__doc__)
            return 1
        lab, cmd, rest = argv[1], argv[2], argv[3:]
    if cmd not in COMMANDS:
        raise Fail("unknown command %r. Commands: %s" % (cmd, " ".join(COMMANDS)))
    if cmd == "commands":
        print("\n".join(COMMANDS))
    elif cmd == "defaults":
        a = _ap(cmd)
        a.add_argument("--v1", action="store_true")
        o = a.parse_args(rest)
        print(json.dumps(V1_DEFAULTS if o.v1 else DEFAULTS, indent=1))
        print(PARAM_DOC)
    elif cmd == "doctor":
        a = _ap(cmd)
        a.add_argument("--json", action="store_true")
        cmd_doctor(a.parse_args(rest).json)
    elif cmd == "presets":
        cmd_presets()
    elif cmd == "find-lab":
        a = _ap(cmd)
        a.add_argument("timeline_uid")
        a.add_argument("--root")
        o = a.parse_args(rest)
        cmd_find_lab(o.timeline_uid, o.root)
    elif cmd == "dump-script":
        cmd_dump_script(lab)
    elif cmd == "init-manual":
        a = _ap(cmd)
        a.add_argument("files", nargs="+")
        a.add_argument("--screen")
        a.add_argument("--fps", type=float)
        o = a.parse_args(rest)
        cmd_init_manual(lab, o.files, o.screen, o.fps)
    elif cmd == "init-params":
        a = _ap(cmd)
        a.add_argument("out")
        a.add_argument("--preset", action="append", default=[])
        o = a.parse_args(rest)
        cmd_init_params(o.out, o.preset)
    elif cmd == "merge":
        cmd_merge(rest)
    elif cmd == "measure":
        a = _ap(cmd)
        a.add_argument("params")
        cmd_measure(a.parse_args(rest).params)
    elif cmd == "social-qc":
        cmd_social_qc(lab, rest)
    else:
        T = TL(lab, quiet=cmd in ("probe", "check", "cache") and not os.path.exists(os.path.join(lab, "sources.json")))
        a = _ap(cmd)
        if cmd == "probe":
            cmd_probe(T)
        elif cmd == "check":
            cmd_check(T)
        elif cmd == "cache":
            cmd_cache(T)
        elif cmd == "match":
            a.add_argument("params")
            a.add_argument("out")
            a.add_argument("--target", default="median")
            a.add_argument("--limit", type=float, default=2.0)
            a.add_argument("--only", help="comma separated clip keys: trim only these (e.g. clips added after the "
                                          "grade) and leave every other clip_overrides entry exactly as it is")
            o = a.parse_args(rest)
            cmd_match(T, o.params, o.out, o.target, o.limit, o.only)
        elif cmd == "rekey":
            a.add_argument("params")
            a.add_argument("out")
            o = a.parse_args(rest)
            cmd_rekey(T, o.params, o.out)
        elif cmd == "render":
            a.add_argument("params")
            a.add_argument("outdir")
            o = a.parse_args(rest)
            cmd_render(T, o.params, o.outdir)
        elif cmd == "wedge":
            a.add_argument("params")
            a.add_argument("key")
            a.add_argument("values")
            a.add_argument("outdir")
            a.add_argument("--slices")
            a.add_argument("--sep", default=",")
            o = a.parse_args(rest)
            cmd_wedge(T, o.params, o.key, o.values, o.outdir, o.slices, o.sep)
        elif cmd == "luts":
            a.add_argument("params")
            a.add_argument("tag")
            a.add_argument("--out")
            a.add_argument("--conv-size", default="auto")
            a.add_argument("--overwrite", action="store_true")
            o = a.parse_args(rest)
            cmd_luts(T, o.params, o.tag, o.out, o.conv_size, o.overwrite)
        elif cmd == "apply-script":
            a.add_argument("tag")
            a.add_argument("--group")
            o = a.parse_args(rest)
            cmd_apply_script(T, o.tag, o.group)
        elif cmd == "backup-script":
            cmd_backup_script(T)
        elif cmd == "grab-script":
            a.add_argument("name")
            a.add_argument("--chunk", type=int, default=15)
            o = a.parse_args(rest)
            cmd_grab_script(T, o.name, o.chunk)
        elif cmd == "compare":
            a.add_argument("params")
            a.add_argument("name")
            a.add_argument("--out")
            a.add_argument("--calibrate", action="store_true")
            o = a.parse_args(rest)
            cmd_compare(T, o.params, o.name, o.out, o.calibrate)
    return 0


if __name__ == "__main__":
    utf8_stdio()
    try:
        sys.exit(main(sys.argv))
    except Fail as e:
        sys.stderr.write("ERROR: %s\n" % e)
        sys.exit(1)
