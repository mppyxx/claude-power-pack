"""fx_lab.py: effects, transitions and animated text for resolve-editor, the offline side (nothing here talks to
Resolve). edit_lab.py imports it for assemble, validate, preview, check and verify; the build snippets never do
(the build reads what this module wrote into the EDL).

One baked table, two renderers (FUSION_PLAN D1): every animated effect becomes sparse keys of per-frame values in the
EDL (motion, accents, retime). Values are LINEAR between keys and constant outside the first and last key; eased
curves keep one key per frame inside their span (a key that lies on the straight line between its neighbours is
dropped, which changes no frame). The preview renders those keys with ffmpeg; the build writes the same keys with
SetInput(value, comp_time). Research keys in comments: [REEL] reel_effects, [PRV] preview_fidelity, [TR]
transitions, [TXT] animated_text, [API] fusion_api, [JDG] the resolve master judge, [G] a judgement.

Contract (FUSION_PLAN s2.6): load_catalog, genre_of, bake_segment_fx, slice_keys, value_at, cover_zoom,
transition_route, textplus_size, text_plan, fx_checks, pixel_plan, grab_targets, aux_stats, text_compare.
"""
import json
import math
import os
import re
import struct

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
CATALOG_PATH = os.path.join(HERE, "fx_catalog.json")
_CATALOG = {}


class FxError(ValueError):
    """A cut list or EDL value the effects layer cannot use (the message says what to change)."""


# ------------------------------------------------------------------------------------------------ catalogue
def load_catalog(path=None):
    """fx_catalog.json, cached (one load per path)."""
    p = path or CATALOG_PATH
    if p not in _CATALOG:
        with open(p, encoding="utf-8") as fh:
            _CATALOG[p] = json.load(fh)
    return _CATALOG[p]


def tr_row(key):
    return (load_catalog().get("transitions") or {}).get(key)


def fx_row(kind):
    return (load_catalog().get("fx") or {}).get(kind)


def anim_row(aid):
    return (load_catalog().get("anims") or {}).get(aid)


def tolerance(name):
    return dict((load_catalog().get("tolerance") or {}).get(name) or {"min_corr": 0.60, "scope": "frame",
                                                                       "mark": False, "aux": None})


LEGACY_TRANSITIONS = ("cross_dissolve", "dip_to_black")


def genre_of(preset_id, premium):
    """The effects genre of a preset ([REEL] s2 PRESET_GENRE). A premium or luxury brand takes premium_brand, except
    music videos and montages: they take cinematic_montage, so the cuts still ride the music while bounces, glitch,
    RGB split, emoji and big zooms go."""
    cat = load_catalog()
    return premium_genre((cat.get("preset_genre") or {}).get(str(preset_id or ""), "creator_reel"), premium)


def premium_genre(g, premium):
    """The genre a premium brief moves a genre to (genre_of; also for a genre a lab preset names itself)."""
    if premium and g in ("performance_ad", "creator_reel", "cinematic_montage", "talking_head"):
        return "premium_brand"
    if premium and g == "music_montage":
        return "cinematic_montage"
    return g


def genre_spec(genre):
    return dict((load_catalog().get("genres") or {}).get(genre) or load_catalog()["genres"]["creator_reel"])


# ------------------------------------------------------------------------------------------------ frames, easing
def fr(ms, fps):
    """Milliseconds to whole frames (round, at least 1) [REEL] fr()."""
    return max(1, int(round(float(ms) * float(fps) / 1000.0)))


def ease(t, kind="in_out_cubic"):
    """Penner easings on 0..1 (numpy arrays or floats). 'snap' is the [API] cubic-bezier (0.1, 0.9, 0.2, 1.0)."""
    t = np.clip(np.asarray(t, dtype=float), 0.0, 1.0)
    if kind == "linear":
        return t
    if kind == "in_quad":
        return t * t
    if kind == "out_quad":
        return 1 - (1 - t) ** 2
    if kind == "in_out_quad":
        return np.where(t < 0.5, 2 * t * t, 1 - (-2 * t + 2) ** 2 / 2)
    if kind == "in_cubic":
        return t ** 3
    if kind == "out_cubic":
        return 1 - (1 - t) ** 3
    if kind == "in_out_cubic":
        return np.where(t < 0.5, 4 * t ** 3, 1 - (-2 * t + 2) ** 3 / 2)
    if kind == "in_out_sine":
        return -(np.cos(math.pi * t) - 1) / 2
    if kind == "snap":
        return np.vectorize(lambda u: cubic_bezier(u, 0.1, 0.9, 0.2, 1.0))(t) if np.ndim(t) else \
            cubic_bezier(float(t), 0.1, 0.9, 0.2, 1.0)
    raise FxError("ease %r is unknown (linear, in_quad, out_quad, in_out_quad, in_cubic, out_cubic, in_out_cubic, "
                  "in_out_sine, snap)" % kind)


def cubic_bezier(u, x1, y1, x2, y2):
    """CSS cubic-bezier timing curve solved for x = u [PRV] cubic_bezier."""
    lo, hi = 0.0, 1.0
    for _ in range(40):
        m = (lo + hi) / 2
        x = 3 * (1 - m) ** 2 * m * x1 + 3 * (1 - m) * m * m * x2 + m ** 3
        if x < u:
            lo = m
        else:
            hi = m
    m = (lo + hi) / 2
    return 3 * (1 - m) ** 2 * m * y1 + 3 * (1 - m) * m * m * y2 + m ** 3


def smooth_keys(keys, f):
    """Piecewise smoothstep through [[frame, value], ...], holding the end values ([TXT] curve())."""
    if f <= keys[0][0]:
        return float(keys[0][1])
    for (f0, v0), (f1, v1) in zip(keys, keys[1:]):
        if f0 <= f <= f1:
            x = (f - f0) / float(max(1e-9, f1 - f0))
            x = min(1.0, max(0.0, x))
            return float(v0) + (float(v1) - float(v0)) * (x * x * (3 - 2 * x))
    return float(keys[-1][1])


def ms_keys_to_frames(keys_ms, fps):
    """[[ms, value], ...] -> [[frame, value], ...] (round, strictly increasing frames)."""
    out, last = [], None
    for ms, v in keys_ms:
        f = int(round(float(ms) * float(fps) / 1000.0))
        if last is not None and f <= last:
            f = last + 1
        out.append([f, float(v)])
        last = f
    return out


# ------------------------------------------------------------------------------------------------ keys (D1)
def reduce_keys(pts, eps):
    """Drop every key that lies on the straight line between the keys kept around it (within eps): the curve keeps
    its value on every integer frame. pts: [(frame, value)] sorted, frames consecutive or not."""
    pts = [(int(f), float(v)) for f, v in pts]
    if len(pts) <= 2:
        return [[f, v] for f, v in pts]
    out = [pts[0]]
    a = 0
    k = 2
    while k < len(pts):
        fa, va = pts[a]
        fc, vc = pts[k]
        ok = True
        for j in range(a + 1, k):
            fj, vj = pts[j]
            lin = va + (vc - va) * (fj - fa) / float(fc - fa)
            if abs(lin - vj) > eps:
                ok = False
                break
        if not ok:
            out.append(pts[k - 1])
            a = k - 1
        k += 1
    out.append(pts[-1])
    return [[f, v] for f, v in out]


def dense_to_keys(vals, f0, nd=6):
    """Per-frame values from frame f0 -> sparse keys, exact under linear interpolation at every integer frame."""
    pts = [(f0 + i, round(float(v), nd)) for i, v in enumerate(vals)]
    return reduce_keys(pts, 0.5 * 10.0 ** (-nd) + 1e-12)


def value_at(keys, f, default=None):
    """Linear between keys, constant outside the first and last key (D1)."""
    if not keys:
        return default
    if f <= keys[0][0]:
        return float(keys[0][1])
    if f >= keys[-1][0]:
        return float(keys[-1][1])
    lo, hi = 0, len(keys) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if keys[mid][0] <= f:
            lo = mid
        else:
            hi = mid
    (f0, v0), (f1, v1) = keys[lo], keys[hi]
    return float(v0) + (float(v1) - float(v0)) * (f - f0) / float(f1 - f0)


def slice_keys(keys, f0, f1):
    """The part of one curve between frames f0 and f1 (both included): the inner keys plus a key at each end with
    the curve's value there, so pieces cut from one curve meet with equal values at the joins. Frames are kept (shift
    them with shift_keys)."""
    if not keys:
        return []
    f0, f1 = int(f0), int(f1)
    if f1 < f0:
        return []
    out = [[f0, round(value_at(keys, f0), 9)]]
    out += [[int(f), v] for f, v in keys if f0 < f < f1]
    if f1 > f0:
        out.append([f1, round(value_at(keys, f1), 9)])
    return reduce_keys(out, 1e-9)


def shift_keys(keys, df):
    return [[int(f) + int(df), v] for f, v in keys]


def keys_vary(keys, base):
    return bool(keys) and any(abs(float(v) - base) > 1e-9 for f, v in keys)


def cover_zoom(dx_px, dy_px, angle_deg, W, H):
    """The smallest zoom that keeps the frame covered (no edge shows) under these moves without mirror edges
    ([REEL] zoom_to_hide: 1.060 for 2 % of the width and 0.6 deg on 9:16). Arrays or numbers, timeline pixels."""
    aspect = float(W) / float(H)
    th = np.radians(np.abs(np.asarray(angle_deg, dtype=float)))
    dx = np.abs(np.asarray(dx_px, dtype=float)) / float(W)
    dy = np.abs(np.asarray(dy_px, dtype=float)) / float(H)
    sw = np.cos(th) + (1.0 / aspect) * np.sin(th) + 2 * dx
    sh = np.cos(th) + aspect * np.sin(th) + 2 * dy
    return float(max(np.max(sw), np.max(sh), 1.0)) + 0.002


# ------------------------------------------------------------------------------------------------ generators
# Each returns (start frame, {"len": n, param: array}) like [REEL]; frames relative to the item (or segment).
def gen_bump(peak_f, fps, peak=1.08, attack_ms=80, release_ms=300, attack_f=None):
    a, r = fr(attack_ms, fps), fr(release_ms, fps)
    if attack_f is not None:
        a = max(1, int(attack_f))          # a shorter attack (no frames before the cut): 1 = peak on the first frame
    up = 1 + (peak - 1) * ease(np.arange(1, a + 1) / float(a), "out_quad")
    down = 1 + (peak - 1) * (1 - ease(np.arange(1, r + 1) / float(r), "out_cubic"))
    z = np.concatenate([up, down])
    return peak_f - a + 1, {"len": len(z), "zoom": z}


def gen_punch(start_f, fps, zoom=1.25, ms=400, kind="in_out_cubic"):
    d = fr(ms, fps)
    z = 1 + (zoom - 1) * ease(np.arange(1, d + 1) / float(d), kind)
    return start_f, {"len": d, "zoom": z, "hold_after": zoom}


def gen_push(start_f, end_f, frm=1.0, to=1.06, kind="in_out_sine"):
    n = max(1, end_f - start_f)
    z = frm + (to - frm) * ease(np.arange(n) / float(max(1, n - 1)), kind)
    return start_f, {"len": n, "zoom": z, "hold_after": to, "hold_before": frm}


def gen_snap(hit_f, fps, level=1.2, overshoot=0.04, settle_ms=160):
    s = fr(settle_ms, fps)
    z = level + overshoot * (1 - ease(np.arange(0, s + 1) / float(s), "out_cubic"))
    return hit_f, {"len": len(z), "zoom": z, "hold_after": level}


def gen_shake(hit_f, fps, W, H, amp=0.02, freq_hz=9.0, decay_ms=320, rot_deg=0.6, seed=7):
    """Seeded decaying shake [REEL] shake(): peak ON the hit frame, 10 % left at decay_ms, every partial under
    0.45 x fps (no aliasing). Moves in timeline pixels (x right, y down), angle in degrees counter-clockwise."""
    f = min(float(freq_hz), 0.45 * float(fps))
    tau = float(decay_ms) / 1000.0 / math.log(10)
    n = fr(float(decay_ms) * 1.5, fps) + 1
    rng = np.random.default_rng(int(seed))
    ph = rng.uniform(-0.35, 0.35, 3)
    t = np.arange(n) / float(fps)
    env = np.exp(-t / tau)
    aspect = float(W) / float(H)
    dx = amp * env * (0.8 * np.cos(2 * math.pi * f * t + ph[0]) + 0.2 * np.cos(2 * math.pi * 1.37 * f * t))
    dy = amp * 0.8 * aspect * env * np.sin(2 * math.pi * 0.83 * f * t + math.pi / 2 + ph[1])
    ang = rot_deg * env * np.sin(2 * math.pi * 0.71 * f * t + math.pi / 2 + ph[2])
    return hit_f, {"len": n, "x_px": dx * W, "y_px": dy * H, "angle": ang}


def gen_flash(cut_f, fps, peak=0.8, up=1, down_ms=100):
    d = fr(down_ms, fps)
    up = int(up)
    upv = peak * np.arange(1, up + 1) / float(up + 1) if up else np.zeros(0)
    downv = peak * (1 - ease(np.arange(0, d + 1) / float(d + 1), "out_quad"))
    v = np.concatenate([upv, downv])
    return cut_f - up, {"len": len(v), "flash": v}


def gen_rgb(at_f, fps, W, ms=130, px=0.012, seed=3, hold=1):
    """Red right, blue left by px of the width, alternating sign, the last frame 0 [REEL] rgb_split(); hold > 1
    keeps each value for that many frames (the glitch's held split, [API] glitch)."""
    n = fr(ms, fps)
    rng = np.random.default_rng(int(seed))
    steps = int(math.ceil(n / float(max(1, hold))))
    mags = px * rng.uniform(0.6, 1.0, steps) * np.where(np.arange(steps) % 2 == 0, 1, -1)
    v = np.repeat(mags, max(1, hold))[:n]
    if n > 2:
        v[-1] = 0.0
    return at_f, {"len": n, "rgb_px": v * W}


def whip_travel(t, a, b):
    """The whole whip pair as ONE eased travel, in frame sizes, at frame t relative to the cut (A shows t = -a..-1,
    B shows t = 0..b - 1): 0 with A at rest (t = -a - 1), 1 at the cut (t = -0.5, between A's last and B's first
    frame), 2 with B at rest (t = b). A accelerates (in_cubic over a + 0.5 frames), B decelerates (out_cubic over
    b + 0.5), so the fastest frame-to-frame move is across the cut. No shown frame sits at a whole frame size: with
    wrap edges that would draw the picture at rest in the middle of the whip (measured stutter, fix pass 2)."""
    a, b = max(1, int(a)), max(1, int(b))
    if t <= -0.5:
        return float(ease(min(1.0, max(0.0, (t + a + 1) / (a + 0.5))), "in_cubic"))
    return 1.0 + float(ease(min(1.0, max(0.0, (t + 0.5) / (b + 0.5))), "out_cubic"))


def gen_whip_out(cut_f, a, W, H, direction="left", travel=1.0, b=None):
    """The outgoing side of a whip pair ([REEL] whip, [API] whip): A accelerates out over its last a frames and
    reaches `travel` frame sizes at the cut, half a frame after its last frame. One more key on the frame after
    its last (the curve going on) keeps every sub-frame sample of the last frame moving under the motion blur
    (the item's keys are constant past their last key)."""
    a = max(1, int(a))
    b = max(1, int(b if b is not None else a))
    sign = {"left": -1, "right": 1, "up": -1, "down": 1}[direction]
    axis, size = ("x_px", W) if direction in ("left", "right") else ("y_px", H)
    off = np.array([sign * travel * whip_travel(t, a, b) for t in range(-a, 1)])
    return cut_f - a, {"len": a + 1, axis: off * size}


def gen_whip_in(cut_f, b, W, H, direction="left", travel=1.0, a=None):
    """The incoming side: B arrives from the opposite side on the same travel and decelerates over its first b
    frames, at rest from frame b. One key on the frame before its first keeps the first frame's blur moving."""
    b = max(1, int(b))
    a = max(1, int(a if a is not None else b))
    sign = {"left": -1, "right": 1, "up": -1, "down": 1}[direction]
    axis, size = ("x_px", W) if direction in ("left", "right") else ("y_px", H)
    off = np.array([sign * travel * (whip_travel(t, a, b) - 2.0) for t in range(-1, b + 1)])
    return cut_f - 1, {"len": b + 2, axis: off * size}


def gen_zoom_out_side(cut_f, n, peak=2.5):
    """The outgoing side of a zoom-through (R2): 1 -> peak over its last n frames, accelerating on a log scale."""
    lz = math.log(peak)
    z = np.exp(lz * ease(np.arange(1, n + 1) / float(n), "in_cubic"))
    return cut_f - n, {"len": n, "zoom": z}


def gen_zoom_in_side(cut_f, n, peak=2.5):
    """The incoming side: from peak back to 1 over its first n frames, decelerating."""
    lz = math.log(peak)
    z = np.exp(lz * (1 - ease(np.arange(0, n + 1) / float(n), "out_cubic")))
    return cut_f, {"len": n + 1, "zoom": z}


def speed_profile(segments, fps, ease_ms=300):
    """[(frames, speed), ...] -> per-frame speed, each change eased over ease_ms centred on its boundary
    (in_out_sine) [REEL] speed_profile."""
    sp = np.concatenate([np.full(int(n), float(s)) for n, s in segments if int(n) > 0])
    e = fr(ease_ms, fps)
    b = 0
    out = sp.copy()
    for (n0, s0), (n1, s1) in zip(segments[:-1], segments[1:]):
        b += int(n0)
        lo, hi = max(0, b - e // 2), min(len(sp), b + (e - e // 2))
        if hi > lo:
            w = ease((np.arange(lo, hi) - lo + 0.5) / float(hi - lo), "in_out_sine")
            out[lo:hi] = s0 + (s1 - s0) * w
    return out


# ------------------------------------------------------------------------------------------------ anchors
def _num(d, k, default, what):
    v = d.get(k, default) if isinstance(d, dict) else default
    if v is None:
        v = default
    if isinstance(v, bool):
        raise FxError("%s: %s must be a number (got %r)" % (what, k, v))
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise FxError("%s: %s must be a number (got %r)" % (what, k, v))
    if not math.isfinite(x):
        raise FxError("%s: %s must be a finite number (got %r)" % (what, k, v))
    return x


def resolve_anchor(at, ctx, fps, beats, words_tl, what):
    """(segment frame, beat index or None) for an anchor: {"word": i}, {"beat": k}, {"beat_rel": n}, {"s": t},
    "cut_in" (frame 0) or "cut_out" (the segment's last frame). Programme beats are frames of the music grid."""
    rec0 = int(ctx.get("rec_in", 0))
    L = int(ctx["L"])
    if at in (None, "cut_in"):
        return 0, None
    if at == "cut_out":
        return L - 1, None
    if not isinstance(at, dict):
        raise FxError("%s: at must be {\"word\": i}, {\"beat\": k}, {\"beat_rel\": n}, {\"s\": t}, \"cut_in\" or "
                      "\"cut_out\" (got %r)" % (what, at))
    if "word" in at:
        i = at["word"]
        mid = at.get("media") or ctx.get("media")
        if isinstance(i, str) and re.match(r"^.+:\d+$", i):
            # "<media>:<index>": a word of another media (the voice-over under a B-roll segment)
            mid, i = i.rsplit(":", 1)[0], int(i.rsplit(":", 1)[1])
        if not isinstance(i, int) or isinstance(i, bool):
            raise FxError("%s: word must be a whole word number of the segment's media, or \"<media>:<index>\" "
                          "(got %r)" % (what, i))
        cands = [w for w in (words_tl or []) if w.get("media") == mid and w.get("i") == i]
        if mid != ctx.get("media"):
            cands = [w for w in cands if rec0 - 0.5 <= float(w["t0"]) * float(fps) < rec0 + L]
        mine = [w for w in cands if w.get("item") in set(ctx.get("audio_ids") or []) | set(ctx.get("item_ids") or [])]
        w = (mine or cands or [None])[0]
        if w is None:
            heard = sorted({str(x.get("media")) for x in (words_tl or [])
                            if rec0 - 0.5 <= float(x["t0"]) * float(fps) < rec0 + L})
            under = (" (words heard under it come from %s)" % ", ".join(heard)) if heard else ""
            if ctx.get("overlay") and mid == ctx.get("media"):
                # an overlay's own clip plays no words: the voice under it does
                raise FxError("%s: word %d of %s is not heard on the timeline: an overlay plays no words of its own; "
                              "anchor on the voice under it with \"<media>:<index>\"%s" % (what, i, mid, under))
            raise FxError("%s: word %d of %s is not heard on the timeline while this segment plays (anchor on a word "
                          "it plays, or \"<media>:<index>\" for the voice under it)%s" % (what, i, mid, under))
        return int(round(float(w["t0"]) * float(fps))) - rec0, None
    if "beat" in at:
        k = at["beat"]
        if not isinstance(k, int) or isinstance(k, bool):
            raise FxError("%s: beat must be a whole beat number of the music grid, from 0 (got %r)" % (what, k))
        if not beats:
            raise FxError("%s: a beat anchor needs a music item with \"grid\": true and its beats.json" % what)
        if not 0 <= k < len(beats):
            raise FxError("%s: beat %d is outside the music grid (0 to %d)" % (what, k, len(beats) - 1))
        return int(round(float(beats[k]))) - rec0, k
    if "beat_rel" in at:
        n = at["beat_rel"]
        if not isinstance(n, int) or isinstance(n, bool) or n < 0:
            raise FxError("%s: beat_rel must be a whole number from 0 (the first beat inside the segment)" % what)
        if not beats:
            raise FxError("%s: a beat anchor needs a music item with \"grid\": true and its beats.json" % what)
        inside = [(k, b) for k, b in enumerate(beats) if rec0 - 0.5 <= float(b) < rec0 + L - 0.5]
        if n >= len(inside):
            raise FxError("%s: the segment holds only %d beats (beat_rel %d asked)" % (what, len(inside), n))
        return int(round(float(inside[n][1]))) - rec0, inside[n][0]
    if "s" in at:
        return int(round(_num(at, "s", 0.0, what) * float(fps))), None
    raise FxError("%s: at %r names no anchor (word, beat, beat_rel or s)" % (what, at))


# ------------------------------------------------------------------------------------------------ baking
MOTION_KINDS = ("punch", "bump", "snap", "push", "shake")
ACCENT_KINDS = ("flash", "rgb_split", "glitch", "light_leak")


def fx_params(kind, spec, what):
    """The fx's parameters: the cut list's values over the catalogue defaults (ranges are [min, max, default])."""
    row = fx_row(kind) or {}
    out = {}
    for k, v in (row.get("params") or {}).items():
        dflt = v[2] if isinstance(v, list) and len(v) == 3 and all(isinstance(x, (int, float)) for x in v) else v
        if isinstance(v, list) and len(v) == 2 and all(isinstance(x, (int, float)) for x in v):
            dflt = None
        val = spec.get(k, dflt)
        if isinstance(dflt, (int, float)) and not isinstance(dflt, bool) and val is not None:
            val = _num(spec, k, dflt, what)
        out[k] = val
    for k in ("ease", "point", "color", "dur_s", "seed", "direction"):
        if k in spec:
            out[k] = spec[k]
    for k, val in out.items():
        if not isinstance(val, (int, float)) or isinstance(val, bool):
            continue
        if (k == "ms" or k.endswith("_ms")) and val <= 0:
            raise FxError("%s: %s must be above 0 ms (got %r)" % (what, k, val))
        if k in ("zoom", "peak", "level", "to", "from") and val <= 0:
            raise FxError("%s: %s must be above 0 (got %r)" % (what, k, val))
    # values the generators would otherwise replace without a word (a glitch hold of 0 became 2, a colour 12 became
    # white in the preview): refused, so the cut list says what is built
    for k, unit in (("hold", "frames"), ("slices", "bands")):
        v = out.get(k)
        if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
                              or float(v) != int(v) or v < 1):
            raise FxError("%s: %s must be a whole number of %s, 1 or more (got %r)" % (what, k, unit, spec.get(k, v)))
    v = out.get("seed")
    if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
                          or float(v) != int(v) or v < 0):
        raise FxError("%s: seed must be a whole number, 0 or more (got %r)" % (what, v))
    v = out.get("color")
    if v is not None and not (isinstance(v, str) and re.match(r"^#[0-9A-Fa-f]{6}$", v.strip())):
        raise FxError("%s: color must be a hex colour \"#RRGGBB\" (got %r)" % (what, v))
    return out


def _point(p, what):
    if p is None:
        return [0.5, 0.5]
    if not (isinstance(p, (list, tuple)) and len(p) == 2 and all(
            isinstance(v, (int, float)) and not isinstance(v, bool) for v in p)):
        raise FxError("%s: point must be [x, y], two numbers in frame fractions (x from the left, y from the top), "
                      "got %r" % (what, p))
    x, y = float(p[0]), float(p[1])
    if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
        raise FxError("%s: point %r must lie inside the frame (0 to 1 each)" % (what, list(p)))
    return [round(x, 4), round(y, 4)]


class _Table:
    """Per-frame arrays over the frames [lo, hi) of one segment (or item)."""

    def __init__(self, lo, hi, vlo=None, vhi=None):
        self.lo, self.hi = int(lo), int(hi)
        # the frames the item is SEEN (an fx reaching past them is clipped); lo..hi may run one frame further on a
        # clip-pair side for the whip's continuation key
        self.vlo = self.lo if vlo is None else int(vlo)
        self.vhi = self.hi if vhi is None else int(vhi)
        n = max(0, self.hi - self.lo)
        self.zoom = np.ones(n)
        self.x = np.zeros(n)
        self.y = np.zeros(n)
        self.ang = np.zeros(n)
        self.flash = np.zeros(n)
        self.rgb = np.zeros(n)
        self.touched = set()

    def place(self, start, g):
        """Composite one generator's arrays from frame start; returns (first, last, clipped) frames written."""
        n = g["len"]
        lo_, hi_ = start, start + n - 1
        clipped = lo_ < self.vlo or hi_ > self.vhi - 1
        for i in range(n):
            f = start + i
            if not self.lo <= f < self.hi:
                continue
            j = f - self.lo
            for k, arr in (("zoom", self.zoom), ("x_px", self.x), ("y_px", self.y), ("angle", self.ang),
                           ("flash", self.flash), ("rgb_px", self.rgb)):
                if k not in g:
                    continue
                v = float(g[k][i])
                if k == "zoom":
                    arr[j] *= v
                elif k == "flash":
                    arr[j] = max(arr[j], v)
                else:
                    arr[j] += v
                self.touched.add(k)
        h = g.get("hold_after")
        if h is not None:
            for f in range(max(self.lo, start + n), self.hi):
                self.zoom[f - self.lo] *= h
        hb = g.get("hold_before")
        if hb is not None and abs(hb - 1.0) > 1e-12:
            for f in range(self.lo, min(self.hi, start)):
                self.zoom[f - self.lo] *= hb
        return lo_, hi_, clipped


def bake_segment_fx(seg, item_ctx, fps, beats, words_tl):
    """One spine segment's (or overlay's) fx and retime -> (fx_list, motion, accents, retime), frames relative to
    the segment's first frame (assemble slices them per item with slice_keys and shifts them per item).

    item_ctx: {"id", "L" (timeline frames), "head", "tail" (frames seen under the transitions at its ends, D3),
    "rec_in" (programme frame of segment frame 0), "W", "H", "kind" (clip or image), "media", "media_fps",
    "src_in", "media_frames", "speed", "item_ids", "audio_ids", "pairs" (clip-pair transition sides from
    transition_route: [{"side": "out"|"in", "kind": "whip"|"zoom", ...}])}. fx records carry "refused" or
    "clipped" when assemble must report them (fx_refused, fx_clipped)."""
    sid = str(item_ctx.get("id") or seg.get("id") or "seg")
    L = int(item_ctx["L"])
    head, tail = int(item_ctx.get("head", 0) or 0), int(item_ctx.get("tail", 0) or 0)
    W, H = float(item_ctx["W"]), float(item_ctx["H"])
    fps = float(fps)
    cat = load_catalog()
    prs_ = [p_ for p_ in (item_ctx.get("pairs") or []) if p_.get("kind") == "whip"]
    ext_in = 1 if any(p_.get("side") == "in" for p_ in prs_) else 0
    ext_out = 1 if any(p_.get("side") == "out" for p_ in prs_) else 0
    tab = _Table(-head - ext_in, L + tail + ext_out, -head, L + tail)
    shake_tab = _Table(-head, L + tail)
    fx_list = []
    point, edges, blur = None, None, None
    leak = None
    glitch = []
    still = item_ctx.get("kind") == "image" and not cat.get("stills_ok")
    specs = seg.get("fx") or []
    if not isinstance(specs, list):
        raise FxError("segment %s: fx must be a list of {\"kind\": ...} entries" % sid)
    for n, spec in enumerate(specs):
        fid = "%s.fx%d" % (sid, n + 1)
        what = "segment %s fx %d" % (sid, n + 1)
        if not isinstance(spec, dict):
            raise FxError("%s must be an object with a kind" % what)
        kind = spec.get("kind")
        row = fx_row(kind)
        rec = {"id": fid, "kind": kind}
        if spec.get("why"):
            rec["why"] = str(spec["why"])
        if row is None or kind in ("ramp", "freeze", "speed_warp"):
            rec["refused"] = ("fx kind %r is unknown; use one of %s (ramps and freezes go in \"retime\")" % (
                kind, ", ".join(k for k in MOTION_KINDS + ACCENT_KINDS)))
            rec["code"] = "fx_unknown"
            fx_list.append(rec)
            continue
        if not row.get("enabled", True):
            rec["refused"] = row.get("refuse") or ("%s is switched off in this version (%s)" % (kind, row.get("gate")))
            rec["code"] = "fx_unknown"
            fx_list.append(rec)
            continue
        if still:
            rec["refused"] = ("keyed effects on a still are not built yet (comp time on a still item was never "
                              "measured, AT-A5): give the still a static zoom instead")
            rec["code"] = "fx_still_untested"
            fx_list.append(rec)
            continue
        P = fx_params(kind, spec, what)
        rec.update({"family": row.get("family"), "tolerance": row.get("tolerance"),
                    "preview": (row.get("preview") or {}).get("class", "exact")})
        if kind == "push" and spec.get("at") is None:
            a_f, on_beat = 0, None
        else:
            a_f, on_beat = resolve_anchor(spec.get("at"), item_ctx, fps, beats, words_tl, what)
        if on_beat is not None:
            rec["on_beat"] = on_beat
        if kind in ("punch", "bump", "snap", "push") and spec.get("point") is not None:
            pt = _point(spec.get("point"), what)
            if point is None:
                point = pt
            elif pt != point:
                rec["note"] = "one zoom point per item: %s uses %s, the item's first point" % (fid, point)
        if kind == "punch":
            st, g = gen_punch(a_f, fps, P["zoom"], P["ms"], str(P.get("ease") or "in_out_cubic"))
            ev = st + g["len"] - 1
        elif kind == "bump":
            # the attack frames before the peak belong to the item before the cut when this item has no frame
            # there (a bump on cut_in or {"s": 0} after a hard cut): then it starts at its peak on the cut, as a
            # flash does, and nothing is cut off
            a_full = fr(P["attack_ms"], fps)
            room = a_f + head + 1
            st, g = gen_bump(a_f, fps, P["peak"], P["attack_ms"], P["release_ms"],
                             attack_f=room if 1 <= room < a_full else None)
            ev = a_f
        elif kind == "snap":
            st, g = gen_snap(a_f, fps, P["level"], P["overshoot"], P["settle_ms"])
            ev = a_f
        elif kind == "push":
            end = L
            if spec.get("dur_s") is not None:
                end = a_f + max(2, int(round(_num(spec, "dur_s", 1.0, what) * fps)))
            st, g = gen_push(a_f, end, P["from"], P["to"], str(P.get("ease") or "in_out_sine"))
            ev = st + g["len"] - 1
        elif kind == "shake":
            st, g = gen_shake(a_f, fps, W, H, P["amp"], P["freq_hz"], P["decay_ms"], P["rot_deg"],
                              _whole(P, "seed", 7))
            ev = a_f
            shake_tab.place(st, g)
        elif kind == "flash":
            # the rise frame before the event belongs to the item before the cut when this item has no frame
            # there: then the flash starts at its peak on the cut (FUSION_PLAN 1.6: peak ON the cut frame)
            up = max(0, min(int(round(P["up"])), a_f + head))
            P["up"] = up
            st, g = gen_flash(a_f, fps, P["peak"], up, P["down_ms"])
            ev = a_f
        elif kind == "rgb_split":
            st, g = gen_rgb(a_f, fps, W, P["ms"], P["px"], _whole(P, "seed", 3))
            ev = a_f
        elif kind == "glitch":
            st, g = gen_rgb(a_f, fps, W, P["ms"], P["px"], _whole(P, "seed", 3), hold=_whole(P, "hold", 2))
            ev = a_f
            glitch.append({"id": fid, "f": [st, st + g["len"] - 1], "px": round(P["px"] * W, 3),
                           "slices": _whole(P, "slices", 4), "seed": _whole(P, "seed", 3),
                           "hold": _whole(P, "hold", 2)})
        elif kind == "light_leak":
            n_l = fr(P["ms"], fps)
            st = a_f - n_l // 2
            g = {"len": n_l}
            ev = a_f
            leak = {"id": fid, "f": [st, st + n_l - 1], "peak": round(P["peak"], 4),
                    "color": str(P.get("color") or "#FF8A3D"), "seed": _whole(P, "seed", 11)}
        else:
            raise FxError("%s: kind %r has no generator" % (what, kind))
        lo_, hi_, clipped = tab.place(st, g) if kind != "light_leak" else (st, st + g["len"] - 1,
                                                                            st < -head or st + g["len"] > L + tail)
        # anchor_f: the frame the cut list anchored it to (the word, beat or time); a punch's sound lands there,
        # while its event_f (the end of the zoom) is where the grabs look (sound_frame)
        rec.update({"f": [int(lo_), int(hi_)], "event_f": int(ev), "anchor_f": int(a_f), "params": _round_params(P)})
        if kind == "shake":
            rec["params"]["seed"] = _whole(P, "seed", 7)
        if clipped:
            rec["clipped"] = True
        fx_list.append(rec)
    # clip-pair transition sides (D6): whip pair and zoom-through keys live in the two items' motion
    for pr in item_ctx.get("pairs") or []:
        side, kind = pr.get("side"), pr.get("kind")
        if kind == "whip":
            if side == "out":
                st, g = gen_whip_out(L, int(pr.get("n", 3)), W, H, pr.get("direction", "left"),
                                     b=int(pr.get("n_other") or pr.get("n", 3)))
            else:
                st, g = gen_whip_in(0, int(pr.get("n", 4)), W, H, pr.get("direction", "left"),
                                    a=int(pr.get("n_other") or pr.get("n", 4)))
            edges = "wrap"          # [API] whip: wrap edges (plan s5: each route keeps its measured setting)
        else:
            nside = int(pr.get("n", 4))
            if side == "out":
                st, g = gen_zoom_out_side(L, nside, float(pr.get("peak", 2.5)))
            else:
                st, g = gen_zoom_in_side(0, nside, float(pr.get("peak", 2.5)))
            if point is None and pr.get("point"):
                point = _point(pr.get("point"), "transition %s" % pr.get("id"))
        tab.place(st, g)
        blur = {"quality": 8, "shutter": 360}
    # the cover zoom of the shakes: the whole item zoomed in so no edge shows ([REEL] 4.3, plan 2.3)
    cover = 1.0
    if "x_px" in shake_tab.touched or "y_px" in shake_tab.touched or "angle" in shake_tab.touched:
        cover = round(cover_zoom(shake_tab.x, shake_tab.y, shake_tab.ang, W, H), 4)
        tab.zoom *= cover
        for rec in fx_list:
            if rec.get("kind") == "shake" and not rec.get("refused"):
                rec["params"]["cover_zoom"] = cover
        if edges is None:
            edges = "mirror"
    motion = None
    keys = {}
    if np.any(np.abs(tab.zoom - 1.0) > 1e-9):
        keys["zoom"] = dense_to_keys(tab.zoom, tab.lo, 6)
    if np.any(np.abs(tab.x) > 1e-9):
        keys["x_px"] = dense_to_keys(tab.x, tab.lo, 3)
    if np.any(np.abs(tab.y) > 1e-9):
        keys["y_px"] = dense_to_keys(tab.y, tab.lo, 3)
    if np.any(np.abs(tab.ang) > 1e-9):
        keys["angle"] = dense_to_keys(tab.ang, tab.lo, 4)
    if keys:
        motion = {"point": point or [0.5, 0.5], "edges": edges, "motion_blur": blur, "keys": keys}
        if cover > 1.0:
            motion["cover_zoom"] = cover
    accents = None
    acc = {}
    if np.any(tab.flash > 1e-9):
        acc["flash"] = dense_to_keys(tab.flash, tab.lo, 4)
    if np.any(np.abs(tab.rgb) > 1e-9):
        acc["rgb_px"] = dense_to_keys(tab.rgb, tab.lo, 3)
    if glitch:
        acc["glitch"] = glitch
    if leak:
        acc["leak"] = leak
    if acc:
        accents = {"flash": acc.get("flash", []), "rgb_px": acc.get("rgb_px", []), "leak": acc.get("leak")}
        if glitch:
            accents["glitch"] = glitch
    retime = bake_retime(seg, item_ctx, fps, beats, words_tl, fx_list)
    return fx_list, motion, accents, retime


def _whole(P, k, default):
    """A whole-number parameter (seed, hold, slices) as given, 0 included (fx_params refused anything else)."""
    v = P.get(k)
    return int(default) if v is None else int(v)


def _round_params(P):
    out = {}
    for k, v in P.items():
        if v is None:
            continue
        if k in ("seed", "hold", "slices") and isinstance(v, (int, float)):
            out[k] = int(v)
        elif isinstance(v, float):
            out[k] = round(v, 6)
        elif isinstance(v, list):
            out[k] = [round(x, 6) if isinstance(x, float) else x for x in v]
        else:
            out[k] = v
    return out


def bake_retime(seg, ctx, fps, beats, words_tl, fx_list):
    """seg["retime"] -> {"kind", "keys": [[item frame, media frame]], "src_span": [lo, hi]} (whole media frames,
    linear between keys, D1) or None. Records a fx entry (kind ramp or freeze) for the checks and the report."""
    rt = seg.get("retime")
    if not rt:
        return None
    sid = str(ctx.get("id") or seg.get("id"))
    what = "segment %s retime" % sid
    if not isinstance(rt, dict) or rt.get("kind") not in ("ramp", "freeze"):
        raise FxError("%s: kind must be ramp or freeze (got %r)" % (what, rt.get("kind") if isinstance(rt, dict) else rt))
    kind = rt["kind"]
    row = fx_row(kind) or {}
    rec = {"id": "%s.rt" % sid, "kind": kind, "family": "retime", "tolerance": row.get("tolerance", "speed_ramp"),
           "preview": (row.get("preview") or {}).get("class", "exact")}
    if not row.get("enabled", True):
        rec.update({"refused": row.get("refuse") or "%s is switched off in this version" % kind, "code": "fx_unknown"})
        fx_list.append(rec)
        return None
    if ctx.get("kind") == "image":
        rec.update({"refused": "a still has no source frames to retime", "code": "fx_still_untested"})
        fx_list.append(rec)
        return None
    if ctx.get("split"):
        rec.update({"refused": "a retime needs one continuous source range: this word segment was split around a "
                               "dropped filler; give the ramp to a shot or in_s segment", "code": "fx_unknown"})
        fx_list.append(rec)
        return None
    if abs(float(ctx.get("speed", 1.0) or 1.0) - 1.0) > 1e-9:
        raise FxError("%s: a retimed segment plays at speed 1 (the ramp sets the speed); remove \"speed\"" % what)
    L = int(ctx["L"])
    head, tail = int(ctx.get("head", 0) or 0), int(ctx.get("tail", 0) or 0)
    r = float(ctx["media_fps"]) / float(fps)
    src_in = int(ctx["src_in"])
    lo, hi = -head, L + tail
    if kind == "ramp":
        prof = rt.get("profile")
        if not isinstance(prof, list) or len(prof) < 2:
            raise FxError("%s: profile must list at least two {\"speed\": s, \"dur_s\": t} sections (the last one "
                          "runs to the end)" % what)
        ease_ms = _num(rt, "ease_ms", 300, what)
        secs = []
        used = 0
        if _num(rt, "ease_ms", 300, what) < 0:
            raise FxError("%s: ease_ms must be 0 or more" % what)
        for k, p in enumerate(prof):
            if not isinstance(p, dict):
                raise FxError("%s: profile section %d must be {\"speed\": s, \"dur_s\": t} (got %r)" % (what, k + 1, p))
            s = _num(p, "speed", 1.0, what)
            if s <= 0:
                raise FxError("%s: speed must be above 0 (a freeze is kind freeze)" % what)
            if k < len(prof) - 1:
                nf = max(1, int(round(_num(p, "dur_s", 0.5, what) * fps)))
                secs.append([nf, s])
                used += nf
            else:
                secs.append([max(1, L - used), s])
        if rt.get("land") is not None:
            land_f, lb = resolve_anchor(rt.get("land"), ctx, fps, beats, words_tl, what + " land")
            e = fr(ease_ms, fps)
            b = land_f - (e - e // 2)
            before = sum(n for n, s in secs[:-2])
            n_prev = b - before
            if n_prev < 1 or b >= L:
                raise FxError("%s: the landing at frame %d leaves no room for the ramp before it (the last speed "
                              "change eases over %d frames)" % (what, land_f, e))
            secs[-2][0] = n_prev
            secs[-1][0] = max(1, L - b)
            rec["land_f"] = int(land_f)
            if lb is not None:
                rec["on_beat"] = lb
        speeds = speed_profile([(n, s) for n, s in secs], fps, ease_ms)
        speeds = np.concatenate([np.full(head, secs[0][1]), speeds[:L], np.full(tail, secs[-1][1])])
        if len(speeds) < hi - lo:
            speeds = np.concatenate([speeds, np.full(hi - lo - len(speeds), secs[-1][1])])
        step = speeds * r
        # frame 0 shows src_in; frames under the head transition run backwards at the first speed
        pos = np.concatenate([[0.0], np.cumsum(step[:-1])])
        pos = pos - pos[head]
        src = src_in + pos
        changes = [f for f in range(1, L) if abs(speeds[head + f] - speeds[head + f - 1]) > 1e-9]
        f_span = [changes[0] if changes else 0, changes[-1] if changes else L - 1]
        rec.update({"params": {"profile": [{"speed": s, "frames": n} for n, s in secs], "ease_ms": ease_ms,
                               "min_speed": round(float(min(s for n, s in secs)), 4),
                               "max_speed": round(float(max(s for n, s in secs)), 4)},
                    "f": f_span, "event_f": int(rec.get("land_f", f_span[-1]))})
    else:
        at_f, ob = resolve_anchor(rt.get("at"), ctx, fps, beats, words_tl, what)
        hold = max(1, int(round(_num(rt, "hold_s", 1.0, what) * fps)))
        if not 0 <= at_f < L:
            raise FxError("%s: the freeze at frame %d is outside the segment (0 to %d)" % (what, at_f, L - 1))
        j = []
        for f in range(lo, hi):
            j.append(f if f < at_f else (at_f if f < at_f + hold else f - hold + 1))
        src = src_in + np.array(j, dtype=float) * r
        rec.update({"params": {"hold": hold, "hold_s": round(hold / fps, 3)}, "f": [at_f, at_f + hold - 1],
                    "event_f": at_f})
        if ob is not None:
            rec["on_beat"] = ob
        if at_f + hold > L:
            rec["clipped"] = True
    whole = np.floor(src + 1e-6)
    # whole source frames: a key is dropped only where the straight line between its neighbours meets every frame
    # exactly (so the build keys whole frames and the TimeStretcher never blends two, [PRV] s5)
    keys = reduce_keys([(lo + i, float(x)) for i, x in enumerate(whole)], 1e-6)
    keys = [[int(f), int(round(v))] for f, v in keys]
    span = [int(whole.min()), int(whole.max())]
    rec["params"]["src_span"] = span
    fx_list.append(rec)
    out = {"kind": kind, "keys": keys, "src_span": span}
    return out


# ------------------------------------------------------------------------------------------------ transitions
def need_handles(d, alignment="center"):
    """(tail of A, head of B) in timeline frames that give exactly d frames (measured in 21.1, [TR] s3)."""
    d = int(d)
    if alignment == "left":
        return 0, d
    if alignment == "right":
        return d, d
    return int(math.ceil(d / 2.0)), d


def placed_length(d, alignment, tail, head):
    """What Resolve 21.1 places for d frames with these handles: (frames, how) with how ok, shortened,
    through_black or refused ([TR] s3, the 27 measured cases of e2c_handles)."""
    d, tail, head = int(d), int(tail), int(head)
    if alignment == "center":
        if head <= 0 or tail <= 0:
            return 0, "refused"
        n = min(d, head, 2 * tail)
        return n, ("ok" if n == d else "shortened")
    if alignment == "left":
        if head <= 0:
            return d, "through_black"
        n = min(d, head)
        return n, ("ok" if n == d else "shortened")
    if tail <= 0:
        return 0, "refused"
    if head <= 0:
        return d, "through_black"
    n = min(d, tail, head)
    return n, ("ok" if n == d else "shortened")


def overlap_of(d, alignment):
    """(frames of A seen after the cut, frames of B seen before it) for a placed transition of d frames: centred
    placement is [cut - ceil(d/2), cut + floor(d/2)) ([TR] s2)."""
    d = int(d)
    if alignment == "left":
        return 0, d
    if alignment == "right":
        return d, 0
    return d // 2, (d + 1) // 2


def transition_route(x, tail_tl, head_tl):
    """D5/D6: the catalogue key, the build route and the length Resolve would place for one cut list (or EDL)
    transition, given the outgoing item's tail and the incoming item's head in timeline frames.
    Returns {"key", "asked", "ok", "code", "why", "build", "frames", "placed", "how", "alignment", "need", "have",
    "row"}; ok False carries the check id that refuses it (transition_unknown, transition_broken,
    transition_handles, transition_through_black, transition_amateur)."""
    cat = load_catalog()
    asked = str(x.get("type") or "cross_dissolve")
    try:
        d = int(x.get("frames", 12))
    except (TypeError, ValueError):
        d = 0
    al = str(x.get("alignment") or "center")
    out = {"asked": asked, "frames": d, "alignment": al, "have": [int(tail_tl), int(head_tl)], "ok": True,
           "code": None, "why": None}
    if d <= 0:
        out.update({"ok": False, "code": "transition_unknown", "why": "frames must be a whole number above 0"})
        return out
    if al not in ("left", "center", "right"):
        out.update({"ok": False, "code": "transition_unknown", "why": "alignment must be left, center or right"})
        return out
    key = asked
    gen = (cat.get("generic") or {}).get(asked)
    nt, nh = need_handles(d, al)
    if gen:
        if int(tail_tl) >= nt and int(head_tl) >= nh:
            key = gen[0]
            out["why"] = "handles allow the Fusion transition (tail %d of %d, head %d of %d)" % (tail_tl, nt, head_tl, nh)
        else:
            key = gen[1]
            out["why"] = ("no handles for the Fusion transition (tail %d of %d, head %d of %d): clip comps on both "
                          "items instead" % (tail_tl, nt, head_tl, nh))
    row = (cat.get("transitions") or {}).get(key)
    out["key"] = key
    if row is None:
        out.update({"ok": False, "code": "transition_unknown",
                    "why": "type %r is not in the catalogue; use a catalogue key (E explain transitions) or whip, "
                           "zoom" % asked})
        return out
    out["row"] = row
    out["build"] = (row.get("resolve") or {}).get("build")
    if (row.get("resolve") or {}).get("category") == "audio":
        out.update({"ok": False, "code": "transition_unknown",
                    "why": "%s is an audio cross fade; a video cut takes a video transition (the audio fade follows "
                           "with \"audio\")" % key})
        return out
    if row.get("never"):
        out.update({"ok": False, "code": "transition_broken", "why": row.get("refuse")})
        return out
    if not row.get("enabled", True):
        if not (row.get("brief_ok") and x.get("look_is_brief") is True):
            out.update({"ok": False, "code": "transition_amateur" if row.get("brief_ok") else "transition_unknown",
                        "why": row.get("refuse") or "%s is switched off in this version" % key})
            return out
    out["need"] = [nt, nh]
    if out["build"] == "clip_pair":
        out.update({"placed": d, "how": "clip_pair"})
        return out
    n, how = placed_length(d, al, tail_tl, head_tl)
    out.update({"placed": n, "how": how})
    if how == "refused":
        out.update({"ok": False, "code": "transition_handles",
                    "why": "Resolve refuses a %s %d-frame transition with tail %d and head %d (needs tail %d, head %d "
                           "timeline frames)" % (al, d, tail_tl, head_tl, nt, nh)})
    elif how == "through_black":
        out.update({"ok": False, "code": "transition_through_black",
                    "why": "a %s-aligned transition with no head on the incoming clip renders a fade through black, "
                           "not a dissolve; give it head frames or centre it" % al})
    elif how == "shortened":
        out.update({"ok": False, "code": "transition_handles",
                    "why": "Resolve would place only %d of %d frames (tail %d, head %d; needs tail %d, head %d)" % (
                        n, d, tail_tl, head_tl, nt, nh)})
    return out


# ------------------------------------------------------------------------------------------------ fonts and Text+
FONT_FILES = {
    ("arial", "bold"): ["/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/Library/Fonts/Arial Bold.ttf",
                        "C:/Windows/Fonts/arialbd.ttf", "/usr/share/fonts/truetype/msttcorefonts/Arial_Bold.ttf"],
    ("arial", "regular"): ["/System/Library/Fonts/Supplemental/Arial.ttf", "/Library/Fonts/Arial.ttf",
                           "C:/Windows/Fonts/arial.ttf", "/usr/share/fonts/truetype/msttcorefonts/Arial.ttf"],
    ("arial black", "regular"): ["/System/Library/Fonts/Supplemental/Arial Black.ttf", "/Library/Fonts/Arial Black.ttf",
                                 "C:/Windows/Fonts/ariblk.ttf", "/usr/share/fonts/truetype/msttcorefonts/Arial_Black.ttf"],
    ("impact", "regular"): ["/System/Library/Fonts/Supplemental/Impact.ttf", "/Library/Fonts/Impact.ttf",
                            "C:/Windows/Fonts/impact.ttf"],
}
FALLBACK_FONTS = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
                  "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
                  "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"]
# Text+ scales the font so its hhea line height = Size x K_LINE x width ([TXT] s2 measured 0.804). K_LINE is set to
# 0.70 x 1.1499 = 0.80493 so Arial (line_em 1.1499) keeps the skill's measured 0.70 exactly; both agree within 0.1 %.
K_LINE = 0.70 * 1.1499
DEFAULT_LINE_EM = 1.1499


def font_file(family, style, font_file_hint=None):
    """A font file for a Text+ family and style, or None."""
    if font_file_hint and os.path.exists(os.path.expanduser(str(font_file_hint))):
        return os.path.expanduser(str(font_file_hint))
    fam = str(family or "Arial").strip().lower()
    sty = str(style or "Regular").strip().lower()
    if fam == "arial black":
        sty = "regular"
    for p in FONT_FILES.get((fam, sty), []):
        if os.path.exists(p):
            return p
    return None


_METRICS = {}


def font_metrics(path):
    """hhea ascender, descender, line gap and unitsPerEm of a TrueType or OpenType file ([TXT] fontmetrics.py)."""
    if path in _METRICS:
        return _METRICS[path]
    try:
        with open(path, "rb") as fh:
            data = fh.read()
        off = 0
        if data[:4] == b"ttcf":
            off = struct.unpack(">I", data[12:16])[0]
        num = struct.unpack(">H", data[off + 4:off + 6])[0]
        t = {}
        for i in range(num):
            tag, _cs, o, ln = struct.unpack(">4sIII", data[off + 12 + 16 * i: off + 28 + 16 * i])
            t[tag.decode("latin-1")] = (o, ln)
        o, _ = t["head"]
        upm = struct.unpack(">H", data[o + 18:o + 20])[0]
        o, _ = t["hhea"]
        asc, desc, gap = struct.unpack(">hhh", data[o + 4:o + 10])
        m = {"upm": upm, "asc": asc, "desc": desc, "gap": gap, "line_em": (asc - desc + gap) / float(upm)}
    except (OSError, KeyError, struct.error, IndexError):
        m = None
    _METRICS[path] = m
    return m


def line_em_of(family, style, font_file_hint=None):
    """(line_em, measured): the font's hhea line height in em, or the Arial value when it cannot be read."""
    p = font_file(family, style, font_file_hint)
    m = font_metrics(p) if p else None
    if not m:
        return DEFAULT_LINE_EM, False
    return float(m["line_em"]), True


def textplus_size(em_px, family, style, W, font_file_hint=None):
    """Text+ Size that draws a font at em_px pixels on a W wide timeline: Size = em_px x line_em / (K_LINE x W)
    ([TXT] s2: Arial 0.70, Arial Black 0.570 per em per width)."""
    le, _ = line_em_of(family, style, font_file_hint)
    return float(em_px) * le / (K_LINE * float(W))


_KERN = {}


def kerning(path):
    """{(left, right): value in em} from the font's kern table, format 0 ([TXT] fontkern.py); {} without one."""
    if not path:
        return {}
    if path in _KERN:
        return _KERN[path]
    pairs = {}
    try:
        with open(path, "rb") as fh:
            data = fh.read()
        num = struct.unpack(">H", data[4:6])[0]
        t = {}
        for i in range(num):
            tag, _cs, o, ln = struct.unpack(">4sIII", data[12 + 16 * i: 28 + 16 * i])
            t[tag.decode("latin-1")] = (o, ln)
        o, _ = t["head"]
        upm = float(struct.unpack(">H", data[o + 18:o + 20])[0])
        cmap = _cmap(data, t["cmap"][0])
        gl = {}
        for c, g in cmap.items():
            gl.setdefault(g, []).append(chr(c))
        if "kern" in t:
            o, ln = t["kern"]
            ver, nt = struct.unpack(">HH", data[o:o + 4])
            p = o + 4
            for _ in range(nt if ver == 0 else 0):
                _sv, slen, cov = struct.unpack(">HHH", data[p:p + 6])
                if (cov >> 8) == 0 and (cov & 1):
                    npairs = struct.unpack(">H", data[p + 6:p + 8])[0]
                    for i in range(npairs):
                        l_, r_, v = struct.unpack(">HHh", data[p + 14 + 6 * i: p + 20 + 6 * i])
                        for a in gl.get(l_, ()):
                            for b in gl.get(r_, ()):
                                pairs[(a, b)] = v / upm
                p += slen
    except (OSError, KeyError, struct.error, IndexError):
        pairs = {}
    _KERN[path] = pairs
    return pairs


def _cmap(data, off):
    n = struct.unpack(">H", data[off + 2:off + 4])[0]
    best = None
    for i in range(n):
        pid, eid, so = struct.unpack(">HHI", data[off + 4 + 8 * i: off + 12 + 8 * i])
        fmt = struct.unpack(">H", data[off + so: off + so + 2])[0]
        if (pid == 3 and eid in (1, 10)) or pid == 0:
            if best is None or fmt == 12:
                best = (fmt, off + so)
    if best is None:
        return {}
    fmt, o = best
    out = {}
    if fmt == 4:
        segx2 = struct.unpack(">H", data[o + 6:o + 8])[0]
        seg = segx2 // 2
        ends = struct.unpack(">%dH" % seg, data[o + 14:o + 14 + segx2])
        starts = struct.unpack(">%dH" % seg, data[o + 16 + segx2:o + 16 + 2 * segx2])
        deltas = struct.unpack(">%dh" % seg, data[o + 16 + 2 * segx2:o + 16 + 3 * segx2])
        ro_off = o + 16 + 3 * segx2
        ros = struct.unpack(">%dH" % seg, data[ro_off:ro_off + segx2])
        for s in range(seg):
            if starts[s] == 0xFFFF:
                continue
            for c in range(starts[s], min(ends[s], 0x24FF) + 1):
                if ros[s] == 0:
                    g = (c + deltas[s]) & 0xFFFF
                else:
                    p = ro_off + 2 * s + ros[s] + 2 * (c - starts[s])
                    g = struct.unpack(">H", data[p:p + 2])[0]
                    if g:
                        g = (g + deltas[s]) & 0xFFFF
                if g:
                    out[c] = g
    elif fmt == 12:
        ng = struct.unpack(">I", data[o + 12:o + 16])[0]
        for i in range(ng):
            a, b, g0 = struct.unpack(">III", data[o + 16 + 12 * i: o + 28 + 12 * i])
            for c in range(a, min(b, 0x24FF) + 1):
                out[c] = g0 + (c - a)
    return out


# ------------------------------------------------------------------------------------------------ text animation
def spans(text):
    """[(start, end_exclusive)] of the words in text; spaces and newlines count as characters, as in Text+."""
    out, i, n = [], 0, len(text)
    while i < n:
        while i < n and text[i] in " \n":
            i += 1
        j = i
        while j < n and text[j] not in " \n":
            j += 1
        if j > i:
            out.append((i, j))
        i = j
    return out


def shown_text(item):
    """The text Text+ shows for a title or caption: its lines joined by newlines, upper-cased for an upper look
    (the same string textplus_inputs builds)."""
    lk = item.get("look") or {}
    lines = [str(x) for x in (item.get("lines") or [])] or [str(item.get("text", ""))]
    if lk.get("case") == "upper":
        lines = [x.upper() for x in lines]
    return "\n".join(lines)


def anim_params(anim):
    """An anim's parameters: the EDL's resolved params over the catalogue defaults."""
    row = anim_row(anim.get("id")) or {}
    out = json.loads(json.dumps(row.get("params") or {}))
    for k, v in (anim.get("params") or {}).items():
        out[k] = v
    return out


def word_frames_for(item, text):
    """Word start frames (from the cue start) for every word span of text: the anim's word_frames, spread over a
    shown word that became several (a text_fix with a space)."""
    anim = item.get("anim") or {}
    sp = spans(text)
    wf = [int(x) for x in (anim.get("word_frames") or [])]
    if len(wf) == len(sp):
        return wf
    words = anim.get("words") or []
    if words and len(words) == len(wf):
        out = []
        for w, f in zip(words, wf):
            out += [f] * max(1, len(str(w).split()))
        if len(out) == len(sp):
            return out
    if not wf:
        return [0] * len(sp)
    # fall back: the known frames in order, the rest at the last known frame
    return (wf + [wf[-1]] * len(sp))[:len(sp)]


def state_whole(item, st):
    """True when one anim state shows the whole text at full strength: every character, alpha 0.99 or more, scale
    0.95 or more and no slide offset over 2 px (anim_read_floor's whole-on-screen rule)."""
    L_ = len(shown_text(item))
    return ((st["visible"] is None or st["visible"] >= L_) and st["alpha"] >= 0.99 and st["scale"] >= 0.95
            and abs(st.get("dy", 0.0)) <= 2.0)


def anim_states(item, fps):
    """Per-frame state of an animated title or caption ([TXT] cue_states, generalised to any frame rate and the
    item's own look): scale, alpha, dy (timeline px, + down), visible (characters shown, None = all), active (word
    index), active_scale, keyword (word index)."""
    anim = item.get("anim") or {}
    P = anim_params(anim)
    text = shown_text(item)
    sp = spans(text)
    n = max(1, int(item["rec_out"]) - int(item["rec_in"]))
    wf = word_frames_for(item, text)
    kw = anim.get("keyword")
    kw = int(kw) if isinstance(kw, int) and not isinstance(kw, bool) and 0 <= kw < len(sp) else None
    scale_keys = ms_keys_to_frames(P["scale_ms"], fps) if P.get("scale_ms") else None
    act = P.get("active") if isinstance(P.get("active"), dict) else None
    act_keys = None
    if act and act.get("scale_ms"):
        act_keys = ms_keys_to_frames(act["scale_ms"], fps)
    if P.get("word_scale_ms"):
        act_keys = ms_keys_to_frames(P["word_scale_ms"], fps)
    fade_in = fr(P["fade_in_ms"], fps) if P.get("fade_in_ms") else 0
    fade_out = fr(P["fade_out_ms"], fps) if P.get("fade_out_ms") else 0
    slide = P.get("slide") if isinstance(P.get("slide"), dict) else None
    reveal = P.get("reveal")
    if anim.get("skip_in"):
        # a title on the programme's first frame: no entrance (the cover shows the whole text), the exit stays. A
        # scale that starts at 0.95 or more is whole on the first frame already (a gentle settle): it stays
        try:
            settle = bool(scale_keys) and float(P["scale_ms"][0][1]) >= 0.95 - 1e-9
        except (TypeError, ValueError, IndexError, KeyError):
            settle = False
        if not settle:
            scale_keys = None
        slide, fade_in = None, 0
        if reveal == "chars":
            reveal = None
    if anim.get("skip_out"):
        # a title that runs into the next title on its track (a chain): no exit, so the text never drops out at
        # the join (edit_lab's title chain rule)
        fade_out = 0
    out = []
    for f in range(n):
        st = {"scale": 1.0, "alpha": 1.0, "dy": 0.0, "visible": None, "active": None, "active_scale": 1.0,
              "keyword": kw}
        if scale_keys:
            st["scale"] = smooth_keys(scale_keys, f)
        if slide:
            sf = fr(slide.get("ms", 320), fps)
            st["dy"] = float(slide.get("dy_px", 40)) * (1 - float(ease(f / float(sf), str(slide.get("ease") or "out_cubic"))))
            if fade_in:
                # the fade's (f + 1) / (n + 1) ramp: never alpha 0 on the title's first frame, whole after n frames
                st["alpha"] = min(1.0, (f + 1) / float(fade_in + 1)) if f < fade_in else 1.0
        elif fade_in:
            st["alpha"] = min(1.0, (f + 1) / float(fade_in + 1)) if f < fade_in else 1.0
        if fade_out:
            left = n - 1 - f
            if left < fade_out:
                st["alpha"] = min(st["alpha"], left / float(fade_out))
        if act is not None or act_keys is not None:
            cur = None
            for i, w in enumerate(wf):
                if f >= w:
                    cur = i
            st["active"] = cur
            if cur is not None and act_keys:
                st["active_scale"] = smooth_keys(act_keys, f - wf[cur])
        if reveal == "words":
            k = -1
            for i, w in enumerate(wf):
                if f >= w:
                    k = i
            st["visible"] = 0 if k < 0 else sp[k][1]
        elif reveal == "chars":
            st["visible"] = min(len(text), int(f * float(P.get("cps", 20)) / float(fps)) + 1)
        out.append(st)
    return out


def _rgb01(c, default=(1.0, 1.0, 1.0)):
    s = str(c or "").strip()
    if re.match(r"^#[0-9a-fA-F]{6}$", s):
        return tuple(round(int(s[i:i + 2], 16) / 255.0, 4) for i in (1, 3, 5))
    return default


def text_plan(item, W, H, fps):
    """The animation of one animated title or caption as Text+ keys on every frame of the cue ([TXT] textplus_plan,
    applied by the build's apply_text_plan) plus the per-frame states the preview draws (one code path, D4).
    The plan holds only what the animation changes or needs over the static Text+ inputs: static (extra Template
    inputs: Size by the per-font rule, WordSpacing, the active box element), keys (Template Size and Write On End
    per frame), center_keys ([frame, x, y] Center per frame for a slide), follower ({static, keys}: the spoken word or
    the keyword, keyed colour), blend_keys (the group fade, a Merge after the Text+)."""
    anim = item.get("anim") or {}
    aid = anim.get("id")
    if not aid or aid == "none":
        return {"states": [], "plan": None}
    if anim_row(aid) is None:
        raise FxError("%s: anim %r is unknown" % (item.get("id"), aid))
    P = anim_params(anim)
    lk = item.get("look") or {}
    states = anim_states(item, fps)
    text = shown_text(item)
    n = len(states)
    W, H = float(W), float(H)
    em = float(item.get("font_px") or 64)
    S = textplus_size(em, lk.get("font") or "Arial", lk.get("style") or "Bold", W, lk.get("font_file"))
    le, measured = line_em_of(lk.get("font") or "Arial", lk.get("style") or "Bold", lk.get("font_file"))
    static = [["Size", round(S, 6)]]
    ws = P.get("word_spacing")
    if ws and abs(float(ws) - 1.0) > 1e-9:
        static.append(["WordSpacing", float(ws)])
    act = P.get("active") if isinstance(P.get("active"), dict) else None
    abox = act.get("box") if act else None
    if abox:
        pad = act.get("pad_em") or [0.18, 0.10]
        ex = max(-0.238, (float(pad[0]) - 0.30) / 1.26)     # [TXT] s2: negative values shrink, floor -0.238
        ey = max(0.0, float(pad[1]) / 1.43)
        bc = _rgb01(abox if isinstance(abox, str) else "#7C3AED", (0.486, 0.227, 0.929))
        static += [["Enabled4", 1], ["Level4", 2], ["Red4", bc[0]], ["Green4", bc[1]], ["Blue4", bc[2]],
                   ["Opacity4", 0.0], ["ExtendHorizontal4", round(ex, 4)], ["ExtendVertical4", round(ey, 4)],
                   ["Round4", float(act.get("round", 0.35))]]
    keys = {}
    sc = [s["scale"] for s in states]
    if any(abs(v - sc[0]) > 1e-9 for v in sc) or abs(sc[0] - 1.0) > 1e-9:
        keys["Size"] = [[f, round(S * v, 6)] for f, v in enumerate(sc)]
    L = len(text)
    if any(s["visible"] is not None for s in states):
        # Text+ draws ceil(End x L) characters, so n characters need End = (n - 0.5) / L ([TXT] s2)
        keys["End"] = [[f, round(1.0 if (s["visible"] is None or s["visible"] >= L) else
                                 max(0.0, (s["visible"] - 0.5) / float(L)), 6)] for f, s in enumerate(states)]
    box = item.get("box") or [W * 0.1, H * 0.8, W * 0.8, H * 0.1]
    cx, cy = float(box[0]) + float(box[2]) / 2.0, float(box[1]) + float(box[3]) / 2.0
    center_keys = None
    dys = [s["dy"] for s in states]
    if any(abs(v) > 1e-9 for v in dys):
        center_keys = [[f, round(cx / W, 6), round(1.0 - (cy + v) / H, 6)] for f, v in enumerate(dys)]
    alphas = [s["alpha"] for s in states]
    blend_keys = [[f, round(a, 6)] for f, a in enumerate(alphas)] if any(abs(a - 1.0) > 1e-9 for a in alphas) else None
    follower = None
    sp = spans(text)
    kw = states[0].get("keyword") if states else None
    if act is not None or P.get("word_scale_ms"):
        fk, fs = {}, [["Range", 1], ["Delay", 0.0]]
        act_i = [s["active"] for s in states]
        fk["FirstCharacter"] = [[f, sp[a][0] if a is not None else L + 1] for f, a in enumerate(act_i)]
        fk["LastCharacter"] = [[f, sp[a][1] - 1 if a is not None else L + 1] for f, a in enumerate(act_i)]
        if act and act.get("color"):
            ac = _rgb01(act["color"])
            for nm, v in zip(("Red1", "Green1", "Blue1"), ac):
                fk[nm] = [[0, v], [n - 1, v]]        # Follower styling must be KEYED ([TXT] s4 trap 2)
        if abox:
            fk["Opacity4"] = [[0, 1.0], [n - 1, 1.0]]
        if (act and act.get("scale_ms")) or P.get("word_scale_ms"):
            vals = [s["active_scale"] for s in states]
            fk["WordSizeX"] = [[f, round(v, 6)] for f, v in enumerate(vals)]
            fk["WordSizeY"] = [[f, round(v, 6)] for f, v in enumerate(vals)]
        follower = {"static": fs, "keys": fk}
    elif kw is not None:
        K = P.get("keyword") or {"color": "#FFD93D", "scale": 1.15, "reflow": True}
        fs = [["Range", 1], ["Delay", 0.0], ["FirstCharacter", sp[kw][0]], ["LastCharacter", sp[kw][1] - 1]]
        fk = {}
        kc = _rgb01(K.get("color", "#FFD93D"))
        for nm, v in zip(("Red1", "Green1", "Blue1"), kc):
            fk[nm] = [[0, v], [n - 1, v]]
        if K.get("scale") and K.get("reflow", True):
            # the Follower's Size is an ABSOLUTE Text+ Size and re-flows the line ([TXT] s2, s4 trap 3)
            fk["Size"] = [[0, round(S * float(K["scale"]), 6)], [n - 1, round(S * float(K["scale"]), 6)]]
        elif K.get("scale"):
            fk["WordSizeX"] = [[0, float(K["scale"])], [n - 1, float(K["scale"])]]
            fk["WordSizeY"] = [[0, float(K["scale"])], [n - 1, float(K["scale"])]]
        follower = {"static": fs, "keys": fk}
    plan = {"anim": aid, "n_frames": n, "text": text, "size_base": round(S, 6), "line_em": round(le, 4),
            "metrics_measured": measured, "static": static, "keys": keys, "center_keys": center_keys,
            "follower": follower, "blend_keys": blend_keys}
    return {"states": states, "plan": plan}


def max_scale(states):
    """The largest scale the block reaches (a pop's overshoot, a keyword or active word bigger than the rest)."""
    m = 1.0
    for s in states:
        m = max(m, float(s.get("scale", 1.0)), float(s.get("active_scale", 1.0)))
    return m


def state_key(st):
    return (round(st["scale"], 4), round(st["alpha"], 3), round(st["dy"], 2), st.get("visible"), st.get("active"),
            round(st.get("active_scale", 1.0), 4), st.get("keyword"))


def state_runs(states):
    """[(a, b_exclusive, state)]: one entry per run of identical states (the preview draws each once)."""
    out, cur, a = [], None, 0
    for f, st in enumerate(list(states) + [None]):
        k = state_key(st) if st is not None else None
        if k != cur:
            if cur is not None:
                out.append((a, f, states[a]))
            cur, a = k, f
    return out


_PFONT = {}


def _pil_font(path, px):
    from PIL import ImageFont
    k = (path, int(round(px * 4)))
    if k not in _PFONT:
        try:
            _PFONT[k] = ImageFont.truetype(path, max(2, int(round(px))))
        except Exception:
            _PFONT[k] = ImageFont.load_default()
    return _PFONT[k]


_INK = {}


def _ink(path, px, ch):
    """(left, right) ink extent of one character from its pen position, px ([TXT] preview._ink)."""
    from PIL import Image, ImageDraw
    k = (path, int(round(px * 4)), ch)
    if k not in _INK:
        ft = _pil_font(path, px)
        w = int(4 * px) + 20
        m = Image.new("L", (w, int(3 * px) + 20), 0)
        try:
            ImageDraw.Draw(m).text((10, int(2 * px) + 10), ch, font=ft, fill=255, anchor="ls")
        except Exception:
            ImageDraw.Draw(m).text((10, 10), ch, font=ft, fill=255)
        cols = m.getbbox()
        _INK[k] = None if cols is None else (cols[0] - 10, cols[2] - 10)
    return _INK[k]


def _hex(c, a=255):
    s = str(c or "#FFFFFF").lstrip("#")
    try:
        return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4)) + (a,)
    except ValueError:
        return (255, 255, 255, a)


def _positions(s, big, ft, ft_big, em, em_big, ws_extra, kern):
    pos, x = [], 0.0
    for i, ch in enumerate(s):
        pos.append(x)
        b = big(i)
        x += (ft_big if b else ft).getlength(ch) + (ws_extra if (ch == " " and not b) else 0.0)
        if kern and i + 1 < len(s):
            x += kern.get((ch, s[i + 1]), 0.0) * (em_big if b else em)
    pos.append(x)
    return pos


def draw_state(item, st, W, H, scale=1.0, fps=25):
    """One frame of an animated title or caption as an RGBA image of (W x scale, H x scale), laid out the way Text+
    lays it out ([TXT] preview.draw_state, measured within 8 px of the render): em from the font's hhea line height,
    pair kerning from its kern table, boxes from the glyph ink, all outlines before all fills, the group fade on the
    whole layer. The block is centred on the item's box centre."""
    from PIL import Image, ImageDraw, ImageFilter
    lk = item.get("look") or {}
    P = anim_params(item.get("anim") or {})
    text = shown_text(item)
    Wp, Hp = max(2, int(round(W * scale))), max(2, int(round(H * scale)))
    im = Image.new("RGBA", (Wp, Hp), (0, 0, 0, 0))
    path = font_file(lk.get("font") or "Arial", lk.get("style") or "Bold", lk.get("font_file"))
    if path is None:
        for p in FALLBACK_FONTS + FONT_FILES[("arial", "bold")]:
            if os.path.exists(p):
                path = p
                break
    em0 = float(item.get("font_px") or 64) * scale
    em = em0 * float(st["scale"])
    if em < 1.0 or st["alpha"] <= 0.0 or path is None:
        return im
    m = font_metrics(path) or {"asc": 1854, "desc": -434, "gap": 67, "upm": 2048}
    asc, desc, gap = m["asc"] / float(m["upm"]), -m["desc"] / float(m["upm"]), m["gap"] / float(m["upm"])
    # Text+ LineSpacing = look line_spacing / 1.15 (textplus_inputs), times the font's own line height
    lsp = float(lk.get("line_spacing") or 1.15) / 1.15
    kern = kerning(path)
    ft = _pil_font(path, em)
    cap = ft.getbbox("H")
    cap_h = cap[3] - cap[1]
    lines = text.split("\n")
    sp = spans(text)
    act = st.get("active")
    kw = st.get("keyword")
    K = P.get("keyword") or {}
    A = P.get("active") if isinstance(P.get("active"), dict) else {}
    rf, rf_scale = set(), 1.0
    if kw is not None and K.get("reflow", True) and K.get("scale"):
        rf = set(range(*sp[kw]))
        rf_scale = float(K["scale"])
    ft_rf = _pil_font(path, em * rf_scale)
    ws = float(P.get("word_spacing") or 1.0)
    ws_extra = (ws - 1.0) * ft.getlength(" ")
    starts, i0 = [], 0
    for ln in lines:
        starts.append(i0)
        i0 += len(ln) + 1
    pos = [_positions(ln, lambda i, s0=s0: (s0 + i) in rf, ft, ft_rf, em, em * rf_scale, ws_extra, kern)
           for ln, s0 in zip(lines, starts)]
    em_line = [em * (rf_scale if any((s0 + k) in rf for k in range(len(ln))) else 1.0) for ln, s0 in zip(lines, starts)]
    rel = [cap_h * em_line[0] / em]
    for i in range(len(lines) - 1):
        rel.append(rel[-1] + lsp * ((desc + gap) * em_line[i] + asc * em_line[i + 1]))
    box = item.get("box") or [W * 0.1, H * 0.8, W * 0.8, H * 0.1]
    cx = (float(box[0]) + float(box[2]) / 2.0) * scale
    cy = (float(box[1]) + float(box[3]) / 2.0 + float(st["dy"])) * scale
    top_cap = cy - rel[-1] / 2.0
    geo = [(ln, cx - ps[-1] / 2.0, top_cap + rb, s0, ps) for ln, s0, rb, ps in zip(lines, starts, rel, pos)]

    def ink_span(chars_x, size):
        lo, hi = None, None
        for x, ch in chars_x:
            e = _ink(path, size, ch) if ch != " " else None
            if e is None:
                continue
            lo = x + e[0] if lo is None else min(lo, x + e[0])
            hi = x + e[1] if hi is None else max(hi, x + e[1])
        return lo, hi

    vis = st.get("visible")
    stroke = lk.get("stroke") if isinstance(lk.get("stroke"), dict) and float(lk["stroke"].get("em") or 0) > 0 else None
    sfill = _hex(stroke.get("color", "#000000")) if stroke else None
    layers = []
    lbox = lk.get("box") if isinstance(lk.get("box"), dict) else None
    abox = A.get("box")
    if lbox:
        bl = Image.new("RGBA", (Wp, Hp), (0, 0, 0, 0))
        pad = list(lbox.get("pad_em") or [0.35, 0.18]) + [0.18]
        padx, pady = float(pad[0]) * em, float(pad[1]) * em
        fill = _hex(lbox.get("color", "#000000"), int(round(255 * float(lbox.get("opacity", 0.65)))))
        mk = Image.new("L", (Wp, Hp), 0)
        dm = ImageDraw.Draw(mk)
        for ln, x0, base, s0, ps in geo:
            lo, hi = ink_span([(x0 + ps[i], ch) for i, ch in enumerate(ln)], em)
            if lo is None:
                continue
            r = [lo - padx, base - asc * em - pady, hi + padx, base + desc * em + pady]
            dm.rounded_rectangle(r, radius=float(lbox.get("round") or 0.0) * (r[3] - r[1]) / 2.0, fill=255)
        bl.paste(Image.new("RGBA", (Wp, Hp), fill), (0, 0), mk)
        layers.append(bl)

    def word_geo(k):
        a, b = sp[k]
        for ln, x0, base, s0, ps in geo:
            if s0 <= a < s0 + len(ln) + 1:
                return x0 + ps[a - s0], base, text[a:b], ps[b - s0] - ps[a - s0]
        return None

    def word_chars(k, s):
        wx, base, word, wadv = word_geo(k)
        if k == kw and rf:
            size = em * rf_scale
            ps = _positions(word, lambda i: True, ft, ft_rf, em, size, 0.0, kern)
            return [(wx + ps[i], ch) for i, ch in enumerate(word)], base, size
        size = em * s
        fs = _pil_font(path, size)
        ps = _positions(word, lambda i: False, fs, fs, size, size, 0.0, kern)
        x = wx + wadv / 2.0 - ps[-1] / 2.0
        return [(x + ps[i], ch) for i, ch in enumerate(word)], base, size

    if abox and act is not None:
        s = st.get("active_scale", 1.0)
        chars, base, size = word_chars(act, s)
        lo, hi = ink_span(chars, size)
        if lo is not None:
            pad = A.get("pad_em") or [0.18, 0.10]
            padx, pady = float(pad[0]) * size, float(pad[1]) * size
            r = [lo - padx, base - asc * size - pady, hi + padx, base + desc * size + pady]
            bl = Image.new("RGBA", (Wp, Hp), (0, 0, 0, 0))
            ImageDraw.Draw(bl).rounded_rectangle(r, radius=float(A.get("round", 0.35)) * (r[3] - r[1]) / 2.0,
                                                 fill=_hex(abox if isinstance(abox, str) else "#7C3AED"))
            layers.append(bl)
    special = set()
    if act is not None and (A.get("color") or abox or A.get("scale_ms") or P.get("word_scale_ms")):
        special.add(act)
    if kw is not None:
        special.add(kw)
    skip = set()
    for k in special:
        skip.update(range(*sp[k]))
    glyphs = []
    col = _hex(lk.get("color") or "#FFFFFF")
    for ln, x0, base, s0, ps in geo:
        n_show = len(ln) if vis is None else max(0, min(len(ln), vis - s0))
        for j in range(n_show):
            if ln[j] != " " and (s0 + j) not in skip:
                glyphs.append((x0 + ps[j], base, ln[j], em, col))
    for k in special:
        a, b = sp[k]
        if vis is not None and vis < b:
            continue
        s, c = 1.0, col
        if k == act:
            s = st.get("active_scale", 1.0)
            if A.get("color"):
                c = _hex(A["color"])
        if k == kw:
            c = _hex(K.get("color", "#FFD93D"))
            s = float(K.get("scale", 1.0))
        chars, base, size = word_chars(k, s)
        glyphs += [(x, base, ch, size, c) for x, ch in chars]
    tl = Image.new("RGBA", (Wp, Hp), (0, 0, 0, 0))
    if stroke:
        dst = ImageDraw.Draw(tl)
        for x, base, ch, size, c in glyphs:
            swp = max(1, int(round(float(stroke["em"]) * size)))
            dst.text((x, base), ch, font=_pil_font(path, size), fill=sfill, anchor="ls", stroke_width=swp,
                     stroke_fill=sfill)
    fl = Image.new("RGBA", (Wp, Hp), (0, 0, 0, 0))
    dfl = ImageDraw.Draw(fl)
    for x, base, ch, size, c in glyphs:
        dfl.text((x, base), ch, font=_pil_font(path, size), fill=c, anchor="ls")
    tl = Image.alpha_composite(tl, fl)
    sh = lk.get("shadow") if isinstance(lk.get("shadow"), dict) else None
    if sh:
        a = tl.split()[3]
        off = 0.08 * em
        smk = Image.new("L", (Wp, Hp), 0)
        smk.paste(a, (int(round(off)), int(round(off))))
        smk = smk.filter(ImageFilter.GaussianBlur(max(1.0, 0.04 * em)))
        op = float(sh.get("opacity", 0.5))
        smk = smk.point(lambda v: int(v * op))
        shl = Image.new("RGBA", (Wp, Hp), _hex(sh.get("color", "#000000"), 0))
        shl.putalpha(smk)
        layers.append(shl)
    layers.append(tl)
    for ly in layers:
        im = Image.alpha_composite(im, ly)
    if st["alpha"] < 1.0:
        al = float(st["alpha"])
        im.putalpha(im.split()[3].point(lambda v: int(round(v * al))))
    return im


def animated_box(item, states, W, H):
    """The text block's box (x, y, w, h) at the largest size it reaches, timeline pixels (safe_zone checks it): the
    whole block grows by its own scale (a pop); a single word that grows (a word pop, the active word, a re-flowed
    keyword) widens its line by about its own growth."""
    box = item.get("box") or [W * 0.1, H * 0.8, W * 0.8, H * 0.1]
    s = max([float(st.get("scale", 1.0)) for st in states] or [1.0])
    a = max([float(st.get("active_scale", 1.0)) for st in states] or [1.0])
    P = anim_params(item.get("anim") or {})
    if (item.get("anim") or {}).get("keyword") is not None and (P.get("keyword") or {}).get("scale"):
        a = max(a, float(P["keyword"]["scale"]))
    words = shown_text(item).split() or [""]
    fpx = float(item.get("font_px") or 64)
    ex = max(0.0, a - 1.0) * fpx * 0.6 * max(len(w_) for w_ in words) / 2.0
    ey = max(0.0, a - 1.0) * fpx / 2.0
    cx, cy = float(box[0]) + float(box[2]) / 2.0, float(box[1]) + float(box[3]) / 2.0
    dys = [float(st.get("dy", 0.0)) for st in states] or [0.0]
    w, h = float(box[2]) * s + 2 * ex, float(box[3]) * s + 2 * ey
    return [cx - w / 2.0, cy - h / 2.0 + min(0.0, min(dys)), w, h + (max(dys) - min(0.0, min(dys)))]


def animated_ink_box(item, states, W, H, scale=0.25):
    """The union of the drawn text's extent (ink, outline, shadow and box) over every distinct state of the
    animation, timeline pixels, or None when no font can be found (the preview's own drawing, so the check sees
    what the viewer sees; [TXT] check_safe)."""
    lk = item.get("look") or {}
    if font_file(lk.get("font") or "Arial", lk.get("style") or "Bold", lk.get("font_file")) is None:
        return None
    x0 = y0 = None
    x1 = y1 = None
    for a, b, st in state_runs(states):
        if st.get("alpha", 1.0) <= 0.0:
            continue
        st_ = dict(st, alpha=1.0)
        bb = draw_state(item, st_, W, H, scale=scale).split()[3].getbbox()
        if bb is None:
            continue
        x0 = bb[0] if x0 is None else min(x0, bb[0])
        y0 = bb[1] if y0 is None else min(y0, bb[1])
        x1 = bb[2] if x1 is None else max(x1, bb[2])
        y1 = bb[3] if y1 is None else max(y1, bb[3])
    if x0 is None:
        return None
    return [x0 / scale, y0 / scale, (x1 - x0) / scale, (y1 - y0) / scale]


# ------------------------------------------------------------------------------------------------ the EDL view
def _items(edl, kinds=("video", "audio", "subtitle")):
    for kind in kinds:
        for tr in (edl.get("tracks") or {}).get(kind, []):
            for it in tr.get("items", []):
                yield kind, tr, it


def _fps(edl):
    from fractions import Fraction
    s = str((edl.get("timeline") or {}).get("fps") or "25/1")
    try:
        return float(Fraction(s))
    except (ValueError, ZeroDivisionError):
        return 25.0


def _track_num(tid):
    m = re.match(r"^(?:V|A|ST)(\d+)$", str(tid))
    return int(m.group(1)) if m else 0


def edl_genre(edl):
    meta = edl.get("fx_meta") or {}
    if meta.get("genre"):
        return meta["genre"]
    return genre_of(edl.get("preset"), bool(meta.get("premium")))


def item_overlaps(edl):
    """{item id: (head, tail)}: frames each V item is seen under the transitions at its ends (D3)."""
    out = {}
    for x in edl.get("transitions") or []:
        b_ = ((x.get("resolve") or {}).get("build"))
        if b_ == "clip_pair":
            continue
        a_after, b_before = overlap_of(int(x.get("frames", 0) or 0), x.get("alignment", "center"))
        h, t = out.get(x.get("from"), (0, 0))
        out[x.get("from")] = (h, t + a_after)
        h, t = out.get(x.get("to"), (0, 0))
        out[x.get("to")] = (h + b_before, t)
    return out


def transition_key(x):
    """The catalogue key of an EDL transition (its type)."""
    return str(x.get("key") or x.get("type") or "cross_dissolve")


def transition_window(edl, x, by_id):
    """[w0, w1) programme frames a transition covers (clip pairs: the keyed frames on both sides)."""
    a = by_id.get(x.get("from"))
    if a is None:
        return None
    c = int(a["rec_out"])
    d = int(x.get("frames", 0) or 0)
    if ((x.get("resolve") or {}).get("build")) == "clip_pair":
        n_out = int((x.get("params") or {}).get("n_out", d // 2) or d // 2)
        n_in = int((x.get("params") or {}).get("n_in", d - d // 2) or (d - d // 2))
        return c - n_out, c + n_in + 1
    al = x.get("alignment", "center")
    w0 = c - (d + 1) // 2 if al == "center" else (c - d if al == "left" else c)
    return w0, w0 + d


# ------------------------------------------------------------------------------------------------ checks
def _chk(cid, level, frames, items, msg, fix):
    return {"id": cid, "level": level, "at_frames": [int(f) for f in frames], "items": list(items), "msg": msg,
            "fix": fix}


def fx_events(edl):
    """Every effect event of the programme: [{"kind", "family", "at" (programme frame), "frames", "item", "id",
    "params", "on_beat", "source" (fx, transition or anim), "stylized"}]."""
    cat = load_catalog()
    styl = set(cat.get("stylized") or [])
    fps = _fps(edl)
    ev = []
    for kind, tr, it in _items(edl, ("video",)):
        r0 = int(it.get("rec_in", 0))
        for x in it.get("fx") or []:
            if x.get("refused"):
                continue
            k = x.get("kind")
            fam = x.get("family") or (fx_row(k) or {}).get("family")
            f = x.get("f") or [x.get("event_f", 0), x.get("event_f", 0)]
            ev.append({"kind": k, "family": fam, "at": r0 + int(x.get("event_f", f[0])), "frames": int(f[1]) - int(f[0]) + 1,
                       "f0": r0 + int(f[0]), "f1": r0 + int(f[1]), "item": it["id"], "id": x.get("id"),
                       "params": x.get("params") or {}, "on_beat": x.get("on_beat"), "source": "fx",
                       "stylized": k in styl, "clipped": bool(x.get("clipped")), "track": tr.get("id")})
        if it.get("kind") == "title" and (it.get("anim") or {}).get("id") not in (None, "none"):
            an_ = it["anim"]
            ev.append({"kind": "title_anim", "family": "text", "at": r0, "frames": 0, "item": it["id"],
                       "id": "%s.anim" % it["id"], "params": {}, "on_beat": None, "source": "anim",
                       "stylized": False, "track": tr.get("id"), "anim": an_.get("id"),
                       "counts": bool((anim_row(an_.get("id")) or {}).get("bounce") or an_.get("asked"))})
    by_id = {it["id"]: it for k, tr, it in _items(edl, ("video",))}
    for x in edl.get("transitions") or []:
        key = transition_key(x)
        row = tr_row(key) or {}
        if (row.get("resolve") or {}).get("category") == "audio":
            continue
        a = by_id.get(x.get("from"))
        at = int(x.get("event_f", int(a["rec_out"]) if a else 0))
        fam = row.get("family") or "dissolve"
        ev.append({"kind": key, "family": fam, "at": at, "frames": int(x.get("frames", 0) or 0), "item": x.get("to"),
                   "id": x.get("id"), "params": x.get("params") or {}, "on_beat": x.get("on_beat"),
                   "source": "transition", "stylized": fam in ("glitch",) or key in ("glitch_fx", "rgb_splitter_fx"),
                   "track": x.get("track", "V1")})
    return sorted(ev, key=lambda e: (e["at"], str(e["id"])))


def sound_frame(x):
    """The item frame a sound effect on fx record x lands on: the anchor (the emphasis word) for a punch, whose zoom
    eases on for 300 to 500 ms after it ([REEL] 4.15: a hit on the punch-in frame); the event frame for the others
    (a bump's or snap's peak, a shake's hit, a flash's cut frame, a push's end)."""
    if x.get("kind") == "punch" and x.get("anchor_f") is not None:
        return int(x["anchor_f"])
    return int(x.get("event_f", (x.get("f") or [0])[0]))


def sound_events(edl):
    """{fx or transition or title id: programme frame of its sound event} (place_sfx and fx_sfx_sync)."""
    out = {}
    for kind, tr, it in _items(edl, ("video",)):
        r0 = int(it.get("rec_in", 0))
        for x in it.get("fx") or []:
            if x.get("id") and not x.get("refused"):
                out[x["id"]] = r0 + sound_frame(x)
        if it.get("kind") == "title":
            out.setdefault(it.get("id"), r0)
    for x in edl.get("transitions") or []:
        if x.get("id") is not None and x.get("event_f") is not None:
            out[x["id"]] = int(x["event_f"])
    return out


def _read_floor(text):
    words = len(str(text).split())
    return max(0.833, len(str(text).replace("\n", " ")) / 20.0, words * 0.33)


REVEAL_HOLD_S = 0.4          # reveal_hold: a word-reveal caption whole at least this long after its last word
REVEAL_PAUSE_S = 0.25        # reveal_hold: ... when a pause this long (or a sentence end) follows the cue; mid-sentence
                             # in fluent speech the next cue takes over on the next word, and no tail is possible
REVEAL_LAST_S = 0.8          # and the programme's last caption (the payoff line) at least this long


def one_line_text(text):
    """A shown text on one line, quoted, its line breaks as " / "."""
    return '"%s"' % " / ".join(x.strip() for x in str(text).split("\n") if x.strip())


def _media_id_of(name):
    """The media id `M add` gives a file: its name without the extension, characters other than letters, digits, -
    and _ replaced by _."""
    base = os.path.splitext(os.path.basename(str(name)))[0]
    return re.sub(r"[^A-Za-z0-9_-]", "_", base)


def brand_logo_media(edl, lab):
    """The media ids whose frames carry the brand's logo or wordmark: brand.json's "logo" and "wordmark" (media
    ids or file names). Items of these media count as logo frames for fx_text_shake, like a "logo" tag."""
    b = None
    path = None
    try:
        path = lab.p("brand.json") if lab is not None and hasattr(lab, "p") else None
    except Exception:
        path = None
    if path and os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as fh:
                b = json.load(fh)
        except (OSError, ValueError):
            b = None
    out = set()
    if isinstance(b, dict):
        med = edl.get("media") or {}
        for key in ("logo", "wordmark"):
            vals = b.get(key)
            for v in (vals if isinstance(vals, list) else [vals]):
                if not isinstance(v, str) or not v.strip() or v.strip().lower() == "none":
                    continue
                v = v.strip()
                out.add(v)
                out.add(_media_id_of(v))
                for mid, m in med.items():
                    if os.path.basename(str((m or {}).get("path") or "")) == os.path.basename(v):
                        out.add(mid)
    return out


SOFT_UPSCALE = 1.15      # the largest upscale of the source's pixels a keyed zoom may reach (KNOWLEDGE s6 headroom)


def fx_checks(edl, lab, P, beats, safe=None):
    """Effect, transition and animated text checks of the programme (FUSION_PLAN s1.1 to s1.9, [REEL] s3) -> check
    dicts in the compute_checks shape ({id, level, at_frames, items, msg, fix}). Taste is WARN, safety is STOP (D7);
    level INFO entries are notes. beats: programme beat frames or None; safe: the platform safe box (x, y, w, h)
    for the animated text at its largest scale."""
    cat = load_catalog()
    fps = _fps(edl)
    out = []
    W, H = int((edl.get("timeline") or {}).get("width") or 1920), int((edl.get("timeline") or {}).get("height") or 1080)
    meta = edl.get("fx_meta") or {}
    Pfx = (P or {}).get("fx") if isinstance((P or {}).get("fx"), dict) else {}
    genre = meta.get("genre") or (Pfx.get("genre") if Pfx.get("genre") and not meta.get("premium") else None) \
        or edl_genre(edl)
    G = genre_spec(genre)
    if Pfx and not meta.get("premium") and Pfx.get("genre", genre) == genre:
        # the preset's own budget (FUSION_PLAN s2.4) over the genre's starting values
        for k in ("per10s", "max_families", "zoom_max"):
            if isinstance(Pfx.get(k), (int, float)) and not isinstance(Pfx.get(k), bool):
                G[k] = Pfx[k]
    allowed_fx = set(Pfx.get("allowed") or []) if Pfx.get("allowed") else None
    allowed_tr = set((((P or {}).get("transitions") or {}).get("allowed")) or []) or None
    appetite = float((cat.get("appetite") or {}).get(str(meta.get("appetite") or "normal"), 1.0))
    N = max([int(it["rec_out"]) for k, tr, it in _items(edl, ("video", "audio"))] or [0])
    ev = fx_events(edl)
    by_id = {it["id"]: it for k, tr, it in _items(edl, ("video",))}
    # effects assemble refused: not in the edit, so the gate stays shut until the cut list fixes or drops them
    for r in edl.get("fx_refused") or []:
        rid = str(r.get("id"))
        it_ = by_id.get(rid) or by_id.get(rid.split(".")[0]) or {}
        out.append(_chk("fx_refused", "STOP", [int(it_["rec_in"])] if it_ else [], [rid], "%s was refused at assemble "
                        "and is not in the edit: %s" % (rid, r.get("why")),
                        "fix or drop it in the cut list and assemble again"))
    # an event: one keyed fx, one non-cut transition or one title animation that moves for its own sake (FUSION_PLAN
    # s1.9). The skill's own default fades and slides on titles are part of the look, not effect events: a title
    # animation counts when it bounces (super_pop) or the cut list names it
    events = [e for e in ev if e["source"] != "anim" or e.get("counts")]
    s30 = fps / 30.0
    limits = cat.get("limits30") or {}
    # density in any 10 s window (genre budget times the brief's appetite)
    win = int(round(10 * fps))
    ats = sorted(e["at"] for e in events)
    worst, at_w = 0, 0
    for i, a in enumerate(ats):
        c = sum(1 for b in ats[i:] if b < a + win)
        if c > worst:
            worst, at_w = c, a
    budget = float(G["per10s"]) * appetite
    if N < win:
        budget *= N / float(win)
    if worst > math.ceil(budget - 1e-9) and worst > 0:
        tas = ["%s %s" % (e["item"], e.get("anim")) for e in events if e["source"] == "anim"
               and at_w <= e["at"] < at_w + win]
        out.append(_chk("fx_density", "WARN", [at_w], [], "%d effect events in one 10 s window from %.2f s%s; %s at "
                        "appetite %s allows %.1f" % (worst, at_w / fps, (" (title animations counted: %s)" % ", ".join(
                            tas)) if tas else "", genre, meta.get("appetite") or "normal", budget),
                        "keep the effects that serve a moment (a word, a hit, a reveal) and cut the rest back to plain "
                        "cuts"))
    banned = set(G.get("banned") or [])
    fams = {}
    for e in ev:
        k, fam = e["kind"], e["family"]
        names = {k, fam}
        if e["source"] == "transition" and fam == "flash":
            names.add("flash")
        hit = sorted(names & banned)
        if e["source"] == "transition" and genre in (((tr_row(k) or {}).get("genres") or {}).get("avoid") or []):
            hit = hit or [k]
        if hit and e["source"] != "anim":
            out.append(_chk("fx_genre", "WARN", [e["at"]], [e["item"]], "%s (%s) at %.2f s does not fit %s" % (
                e["id"], k, e["at"] / fps, genre), "drop it, or use what %s uses (KNOWLEDGE section 15)" % genre))
        elif e["source"] == "fx" and allowed_fx is not None and k not in allowed_fx:
            out.append(_chk("fx_genre", "WARN", [e["at"]], [e["item"]], "%s (%s) at %.2f s is not among the preset's "
                            "effects (%s)" % (e["id"], k, e["at"] / fps, ", ".join(sorted(allowed_fx))),
                            "use one of the preset's effects, or a cut"))
        elif e["source"] == "transition" and allowed_tr is not None and k not in allowed_tr and not (
                (k in ("custom_whip", "whip_pair") and "whip" in allowed_tr) or
                (k in ("custom_zoom_overlap", "custom_zoom_through") and "zoom" in allowed_tr)):
            out.append(_chk("fx_genre", "WARN", [e["at"]], [e["item"]], "transition %s (%s) is not among the preset's "
                            "transitions (%s)" % (e["id"], k, ", ".join(sorted(allowed_tr))),
                            "use one of the preset's transitions, or a cut"))
        if e["stylized"] or fam in (cat.get("stylized") or []):
            fams.setdefault(fam if fam in (cat.get("stylized") or []) else k, []).append(e)
    if len(fams) > int(G["max_families"]):
        out.append(_chk("fx_families", "WARN", [], [], "%d stylized families (%s) mix visual languages; %s allows %d"
                        % (len(fams), ", ".join(sorted(fams)), genre, int(G["max_families"])),
                        "keep one or two families that fit the piece"))
    for e in ev:
        k, fam, p = e["kind"], e["family"], e["params"]
        lim_key = {"bump": "bump", "flash": "flash", "rgb_split": "rgb_split", "glitch": "glitch",
                   "light_leak": "light_leak"}.get(k)
        if e["source"] == "transition":
            lim_key = {"whip": "whip", "zoom": "zoom_through", "flash": "flash", "glitch": "glitch"}.get(fam)
        lim = limits.get(lim_key) if lim_key else None
        if lim and e["frames"]:
            lo, hi = int(round(lim[0] * s30)), int(round(lim[1] * s30))
            if not lo <= e["frames"] <= hi:
                out.append(_chk("fx_length", "WARN", [e["at"]], [e["item"]], "%s (%s) lasts %d frames; keep %d to %d "
                                "at %g fps" % (e["id"], k, e["frames"], lo, hi, fps), "set its length inside the range"))
        if e["source"] != "fx":
            continue
        it = by_id.get(e["item"]) or {}
        z0 = float((it.get("transform") or {}).get("zoom", 1.0) or 1.0)
        if k in ("punch", "bump", "snap", "push"):
            peak = {"punch": p.get("zoom"), "bump": p.get("peak"), "snap": (p.get("level") or 1.0) + (p.get("overshoot") or 0),
                    "push": p.get("to")}[k] or 1.0
            mz = max([v for f, v in ((it.get("motion") or {}).get("keys") or {}).get("zoom", [])] or [float(peak)])
            total = z0 * max(float(peak), mz)
            if total > float(G["zoom_max"]) + 1e-9:
                out.append(_chk("fx_zoom", "WARN", [e["at"]], [e["item"]], "%s zooms to %.2f (static %.2f x keyed "
                                "%.2f), above %.2f for %s" % (e["id"], total, z0, max(float(peak), mz),
                                                             float(G["zoom_max"]), genre),
                                "lower the zoom or the static framing"))
            if k == "punch" and float(peak) < 1.15 - 1e-9:
                out.append(_chk("fx_zoom", "WARN", [e["at"]], [e["item"]], "punch %s to %.2f is under 1.15 and reads "
                                "as a mistake" % (e["id"], float(peak)), "punch to 1.15 to 1.30, or use a push"))
            if k == "bump" and not 1.03 - 1e-9 <= float(peak) <= 1.15 + 1e-9:
                out.append(_chk("fx_zoom", "WARN", [e["at"]], [e["item"]], "bump %s peaks at %.2f, outside 1.03 to 1.15"
                                % (e["id"], float(peak)), "keep a beat bump at 1.05 to 1.12"))
        if k == "shake":
            amp, frq = float(p.get("amp") or 0.02), float(p.get("freq_hz") or 9.0)
            if amp > 0.035:
                out.append(_chk("fx_shake", "WARN", [e["at"]], [e["item"]], "shake %s moves %.3f of the width: violent"
                                % (e["id"], amp), "keep 0.008 to 0.03"))
            if frq > fps / 2.0:
                out.append(_chk("fx_shake", "WARN", [e["at"]], [e["item"]], "shake %s at %.1f Hz is above fps/2 (%.1f): "
                                "it aliases into random jumps" % (e["id"], frq, fps / 2.0), "keep it at 6 to 12 Hz"))
        if k in ("rgb_split", "glitch") and float(p.get("px") or 0.0) > 0.025:
            out.append(_chk("fx_rgb", "WARN", [e["at"]], [e["item"]], "%s splits %.3f of the width; keep under 0.02"
                            % (e["id"], float(p["px"])), "lower px"))
        if e.get("clipped"):
            out.append(_chk("fx_span", "WARN", [e["at"]], [e["item"]], "%s (%s) is cut off at an edge of %s: only the "
                            "part inside the shot shows (programme frames %d..%d planned)%s" % (
                                e["id"], k, e["item"], e.get("f0", e["at"]), e.get("f1", e["at"]),
                                "; a light leak stays inside one shot in this version" if k == "light_leak" else ""),
                            "move it inside the item (or give the item handles under a transition)"))
    # beat sync of the accents marked on a beat
    if beats is not None and len(beats):
        b = np.asarray(beats, dtype=float)
        tol = int(max(1, round(0.08 * fps)))
        for e in ev:
            if e["on_beat"] is None or e["kind"] not in ("bump", "snap", "shake", "flash", "rgb_split", "glitch",
                                                          "ramp", "freeze"):
                continue
            d = int(round(float(np.min(np.abs(b - e["at"])))))
            if d > tol:
                out.append(_chk("fx_offbeat", "WARN", [e["at"]], [e["item"]], "%s lands %d frames off the nearest beat"
                                % (e["id"], d), "anchor it with {\"beat\": k}"))
    # the flash rule: more than 3 flash events in any 1 s (ITU-R BT.1702, KNOWLEDGE section 8)
    fl = sorted(e["at"] for e in ev if e["kind"] in ("flash", "glitch", "rgb_split") or
                (e["source"] == "transition" and (e["family"] == "flash" or (tr_row(e["kind"]) or {}).get("flash_risk"))))
    for i, a in enumerate(fl):
        if sum(1 for x in fl[i:] if x < a + fps) > 3:
            out.append(_chk("fx_flash_rule", "STOP", [a], [], "more than 3 flash events within 1 s from %.2f s "
                            "(photosensitivity, ITU-R BT.1702)" % (a / fps), "spread the flashes or drop some"))
            break
    # hooks, edges, text under accents
    for e in ev:
        if e["at"] < fps and ((e["source"] == "transition" and e["family"] in ("whip", "zoom", "flash", "glitch"))
                              or e["kind"] in ("flash", "glitch", "light_leak")):
            out.append(_chk("fx_hook", "WARN", [e["at"]], [e["item"]], "%s (%s) sits in the first second at %.2f s; it "
                            "delays the hook" % (e["id"], e["kind"], e["at"] / fps), "move it after the first second"))
    titles = [it for k, tr, it in _items(edl, ("video",)) if it.get("kind") == "title"]
    logo_media = brand_logo_media(edl, lab)
    for t in titles:
        if t.get("fx") or t.get("motion") or t.get("accents"):
            out.append(_chk("fx_text_shake", "STOP", [int(t["rec_in"])], [t["id"]], "title %s carries a picture effect; "
                            "text must stay readable" % t["id"], "remove the effect from the title (animate it with "
                                                                  "an anim instead)"))
    for k_, tr, it in _items(edl, ("video",)):
        mo = it.get("motion") or {}
        ks = mo.get("keys") or {}
        if any(keys_vary(ks.get(n), 0.0) for n in ("x_px", "y_px", "angle")) and mo.get("edges") not in ("mirror", "wrap"):
            xs = [abs(v) for f, v in ks.get("x_px") or []] or [0.0]
            ys = [abs(v) for f, v in ks.get("y_px") or []] or [0.0]
            an = [abs(v) for f, v in ks.get("angle") or []] or [0.0]
            need = cover_zoom(max(xs), max(ys), max(an), W, H)
            zmin = min([v for f, v in ks.get("zoom") or []] or [1.0]) * float((it.get("transform") or {}).get("zoom", 1.0) or 1.0)
            if zmin < need - 1e-6:
                out.append(_chk("fx_edges", "STOP", [int(it["rec_in"])], [it["id"]], "%s moves without mirror edges and "
                                "zooms only %.3f where %.3f hides the edge" % (it["id"], zmin, need),
                                "set edges to mirror or zoom to %.3f" % need))
        acc = [x for x in it.get("fx") or [] if x.get("kind") in ("shake", "flash", "rgb_split", "glitch")
               and not x.get("refused")]
        if acc:
            tg = set(it.get("tags") or [])
            if it.get("media") in logo_media:
                tg.add("logo")
            for x in acc:
                a0, a1 = int(it["rec_in"]) + int(x["f"][0]), int(it["rec_in"]) + int(x["f"][1]) + 1
                over = [t for t in titles if int(t["rec_in"]) < a1 and a0 < int(t["rec_out"])
                        and t.get("role") in ("offer", "brand", "proof")]
                if tg & {"logo", "product", "offer"} or over:
                    out.append(_chk("fx_text_shake", "STOP", [a0], [it["id"]] + [t["id"] for t in over],
                                    "%s (%s) moves or distorts %s" % (x["id"], x["kind"], "the logo, product or offer "
                                                                      "on %s" % it["id"] if not over else
                                                                      "the picture under %s %s" % (
                                                                          over[0].get("role"), over[0]["id"])),
                                    "never put an accent on a logo, a product name or an offer: move it off those "
                                    "frames"))
    # retime: frames repeat, freezes, the landing
    for k_, tr, it in _items(edl, ("video",)):
        rt = it.get("retime")
        mid = it.get("media")
        m = (edl.get("media") or {}).get(mid) or {}
        try:
            from fractions import Fraction
            mf = float(Fraction(str(m.get("fps") or "%g" % fps)))
        except (ValueError, ZeroDivisionError):
            mf = fps
        proc = it.get("retime_process") or "nearest"
        for x in it.get("fx") or []:
            if x.get("refused"):
                continue
            if x.get("kind") == "ramp":
                slow = float((x.get("params") or {}).get("min_speed") or 1.0)
                # whatever retime_process says: a ramp is keyed on whole source frames at 100 %, so Speed Warp
                # has nothing to retime and the slow part repeats frames
                if slow * mf < fps * 0.999:
                    out.append(_chk("fx_ramp_repeat", "WARN", [int(it["rec_in"]) + int(x["f"][0])], [it["id"]],
                                    "%s slows to %.0f %% of a %g fps clip on a %g fps timeline: frames repeat" % (
                                        x["id"], slow * 100, mf, fps),
                                    "use 50 or 60 fps media or raise the slow speed to %.0f %% or more (Speed Warp "
                                    "cannot smooth a ramp; for smooth slow motion use a constant segment speed under "
                                    "1 with retime_process speed_warp)" % math.ceil(fps / mf * 100)))
            if x.get("kind") == "freeze":
                hold = int((x.get("params") or {}).get("hold") or 0)
                a0 = int(it["rec_in"]) + int(x["f"][0])
                over = [t for t in titles if int(t["rec_in"]) < a0 + hold and a0 < int(t["rec_out"])]
                if over:
                    need = max(_read_floor(t.get("text", "")) for t in over)
                    if hold / fps + 1e-9 < need:
                        out.append(_chk("fx_freeze", "STOP", [a0], [it["id"]] + [t["id"] for t in over],
                                        "%s holds %.2f s with text on it; the text needs %.2f s" % (x["id"], hold / fps, need),
                                        "hold the freeze at least %.2f s or shorten the text" % need))
                elif hold / fps < 0.6 - 1e-9 or hold / fps > 2.5 + 1e-9:
                    out.append(_chk("fx_freeze", "WARN", [a0], [it["id"]], "%s holds %.2f s; keep 0.6 to 2.0 s" % (
                        x["id"], hold / fps), "set hold_s inside 0.6 to 2.0 s"))
        if proc == "speed_warp" and abs(float(it.get("speed", 1.0) or 1.0) - 1.0) < 1e-9:
            out.append(_chk("fx_ramp_repeat", "WARN", [int(it["rec_in"])], [it["id"]], "%s asks for speed_warp at 100 %%: "
                            "nothing is retimed%s" % (it["id"], " (its ramp or freeze is keyed on whole source frames at "
                                                      "100 %, so Speed Warp cannot smooth it)" if rt else ""),
                            "set a constant speed under 1 or drop retime_process"))
    # transitions: repeats, share, the never list, amateur, flash risk, black cards, motion blur, smooth cut
    tx = [x for x in edl.get("transitions") or [] if (tr_row(transition_key(x)) or {}).get("family") != "audio"]
    run_k, prev = 0, None
    effect_x = []
    for x in sorted(tx, key=lambda x: int((by_id.get(x.get("from")) or {}).get("rec_out", 0))):
        key = transition_key(x)
        row = tr_row(key) or {}
        a = by_id.get(x.get("from")) or {}
        b = by_id.get(x.get("to")) or {}
        c = int(a.get("rec_out", 0))
        if key not in LEGACY_TRANSITIONS + ("cross_dissolve_fx",):
            effect_x.append(x)
        run_k = run_k + 1 if prev == key else 1
        prev = key
        if run_k == 4 and key not in LEGACY_TRANSITIONS:
            out.append(_chk("fx_repeat", "WARN", [c], [x.get("id")], "%s on 4 cuts in a row (to %s)" % (key, x.get("id")),
                            "vary the transitions or go back to plain cuts"))
        if row.get("never"):
            out.append(_chk("transition_broken", "STOP", [c], [x.get("id")], "%s: %s" % (x.get("id"), row.get("refuse")),
                            "use another transition"))
        elif row.get("brief_ok") and not row.get("enabled", True) and x.get("look_is_brief") is not True:
            out.append(_chk("transition_amateur", "WARN", [c], [x.get("id")], "%s uses %s, which reads as dated or "
                            "amateur in reels" % (x.get("id"), key),
                            "use a cut, a dissolve or a P0 transition, or mark look_is_brief when the brief asks for it"))
        if row.get("flash_risk"):
            out.append(_chk("transition_flash_risk", "WARN", [c], [x.get("id")], "%s (%s) flashes; run the delivery "
                            "photosensitivity check" % (x.get("id"), key), "keep it short and check it in the QC"))
        if row.get("black_card") and H > W:
            out.append(_chk("transition_black_card", "WARN", [c], [x.get("id")], "%s (%s) turns a card over black, which "
                            "shows wide black bars on 9:16" % (x.get("id"), key), "use a whip, a zoom or a cut"))
        if row.get("motion_blur") and (x.get("params") or {}).get("motion_blur") is False:
            out.append(_chk("transition_mb_off", "WARN", [c], [x.get("id")], "%s (%s) runs with motion blur off: mirrored "
                            "seams show mid-transition" % (x.get("id"), key), "leave motion_blur on"))
        if key == "smooth_cut":
            ma, mb = a.get("media"), b.get("media")
            bad = None
            if ma != mb:
                bad = "joins two different clips (%s, %s)" % (ma, mb)
            else:
                try:
                    from fractions import Fraction
                    mf = float(Fraction(str(((edl.get("media") or {}).get(ma) or {}).get("fps") or "25")))
                except (ValueError, ZeroDivisionError):
                    mf = fps
                gap = (int(b.get("src_in", 0)) - int(a.get("src_out", 0))) / mf
                if gap < 0 or gap >= 2.0:
                    bad = "skips %.2f s of the take (Smooth Cut hides only small jumps inside one take)" % gap
                ta, tb = a.get("transform") or {}, b.get("transform") or {}
                if any(abs(float(ta.get(k_, 0) or 0) - float(tb.get(k_, 0) or 0)) > 1e-6 for k_ in ("zoom", "pan_px", "tilt_px")):
                    bad = "changes the framing across the join"
            if bad:
                out.append(_chk("smooth_cut_misuse", "STOP", [c], [x.get("id")], "%s %s" % (x.get("id"), bad),
                                "use Smooth Cut only inside one take (same clip, under 2 s skipped, same framing); "
                                "otherwise a cut or a punch-in"))
    # edit points: every frame where the visible picture changes, on any picture track (a voice-over piece cuts
    # on its V2 overlays as much as on V1), titles left out
    pts = set()
    for k, tr, it in _items(edl, ("video",)):
        if it.get("kind", "clip") == "title":
            continue
        for f_ in (int(it.get("rec_in", 0)), int(it.get("rec_out", 0))):
            if 0 < f_ < N:
                pts.add(f_)
    n_cuts = max(1, len(pts))
    if effect_x and genre != "music_montage" and len(effect_x) / float(n_cuts) > 0.30:
        out.append(_chk("fx_repeat", "WARN", [], [x.get("id") for x in effect_x], "%d of %d edit points carry a "
                        "transition effect (%.0f %%, keep under 30 %%)" % (len(effect_x), n_cuts,
                                                                          100.0 * len(effect_x) / n_cuts),
                        "turn most of them back into cuts"))
    # animated text
    caps = [c for k, tr, c in _items(edl, ("subtitle",))]
    kw_cues = 0
    # the payoff line: the programme's last caption holds whole for REVEAL_LAST_S
    last_cap = max(caps, key=lambda c: (int(c["rec_out"]), int(c["rec_in"]))) if caps else None
    if last_cap is not None and (last_cap.get("anim") or {}).get("id") in (None, "none") and \
            (int(last_cap["rec_out"]) - int(last_cap["rec_in"])) / fps + 1e-9 < REVEAL_LAST_S:
        out.append(_chk("reveal_hold", "WARN", [int(last_cap["rec_in"])], [last_cap["id"]], "the last caption %s is on "
                        "screen for %.2f s: the payoff line needs %.1f s whole" % (
                            last_cap["id"], (int(last_cap["rec_out"]) - int(last_cap["rec_in"])) / fps, REVEAL_LAST_S),
                        "hold the last line to the end of the piece (end the picture later or give the words a longer "
                        "tail), in one cue"))
    caps_by_start = sorted(caps, key=lambda c: (int(c["rec_in"]), int(c["rec_out"])))
    next_cap = {id(a): b for a, b in zip(caps_by_start, caps_by_start[1:])}
    for t in titles + caps:
        an = t.get("anim") or {}
        aid = an.get("id")
        if not aid or aid == "none":
            continue
        row = anim_row(aid) or {}
        P = anim_params(an)
        role_caption = t in caps
        try:
            st = anim_states(t, fps)
        except Exception:
            st = []
        if row.get("bounce") and not G.get("bounce_ok", True):
            out.append(_chk("fx_text_motion", "WARN", [int(t["rec_in"])], [t["id"]], "%s bounces (%s) in a %s piece" % (
                t["id"], aid, genre), "use fade or slide_hook for titles, clean_box or keyword for captions"))
        sk = P.get("scale_ms") or (P.get("active") or {}).get("scale_ms") or P.get("word_scale_ms")
        if sk:
            peak = max(float(v) for ms, v in sk)
            ent = max(float(ms) for ms, v in sk if float(v) >= peak - 1e-9)
            last_ms = max(float(ms) for ms, v in sk)
            limit = 0.15 if role_caption else 0.30
            if peak - 1.0 > limit + 1e-9:
                out.append(_chk("fx_text_motion", "WARN", [int(t["rec_in"])], [t["id"]], "%s overshoots %.2f (keep %s at "
                                "%.2f or less)" % (t["id"], peak - 1.0, "captions" if role_caption else "titles", limit),
                                "lower the overshoot"))
            if role_caption and ent > 250 + 1e-9:
                out.append(_chk("fx_text_motion", "WARN", [int(t["rec_in"])], [t["id"]], "%s enters over %d ms (captions "
                                "250 ms or less)" % (t["id"], ent), "shorten the entrance"))
            if not role_caption and P.get("scale_ms") and (last_ms > 400 + 1e-9 or len(str(t.get("text", "")).split()) > 3):
                out.append(_chk("fx_text_motion", "WARN", [int(t["rec_in"])], [t["id"]], "title pop %s runs %d ms on %d "
                                "words (a pop is for 1 to 3 words within 400 ms)" % (
                                    t["id"], last_ms, len(str(t.get("text", "")).split())),
                                "use slide_hook or fade for longer titles"))
        if not role_caption and st and int(t["rec_in"]) == 0 and not state_whole(t, st[0]):
            out.append(_chk("first_frame_text", "WARN", [0], [t["id"]], "%s opens the piece but its first frame does "
                            "not show the whole text (%s entrance: alpha %.2f, scale %.2f): the first frame is the "
                            "cover and the hook text belongs on it (KNOWLEDGE H7)" % (
                                t["id"], aid, st[0]["alpha"], st[0]["scale"]),
                            "drop the named animation (a default one starts whole on the first frame) or start the "
                            "title a frame later"))
        if not role_caption and st:
            L_ = len(shown_text(t))
            full = next((k for k, s in enumerate(st) if (s["visible"] is None or s["visible"] >= L_)
                         and s["alpha"] >= 0.99 and s["scale"] >= 0.95), len(st))
            need = _read_floor(t.get("text", ""))
            if (len(st) - full) / fps + 1e-9 < need:
                out.append(_chk("anim_read_floor", "STOP", [int(t["rec_in"])], [t["id"]], "%s is whole on screen for %.2f "
                                "s after its animation; it needs %.2f s to be read" % (t["id"], (len(st) - full) / fps, need),
                                "lengthen the title (dur_s) or shorten its text"))
            if P.get("reveal") == "chars" and t.get("role") == "hook" and (int(t["rec_in"]) + full) / fps > 3.0 + 1e-9:
                out.append(_chk("fx_text_motion", "WARN", [int(t["rec_in"])], [t["id"]], "typewriter hook %s finishes "
                                "typing at %.2f s (inside the first 3 s, please)" % (t["id"], (int(t["rec_in"]) + full) / fps),
                                "shorten the hook or start it earlier"))
        if role_caption and st:
            # reveal_hold: a word-reveal cue before a pause or a sentence end whole for under 0.4 s, or the last
            # caption whole for under 0.8 s
            L_ = len(shown_text(t))
            full = next((k for k, s in enumerate(st) if (s["visible"] is None or s["visible"] >= L_)
                         and s["alpha"] >= 0.99 and s["scale"] >= 0.95), len(st))
            whole = (len(st) - full) / fps
            nx_ = next_cap.get(id(t))
            # the 0.4 s floor where a tail is possible: before a pause or after a sentence end. Mid-sentence in
            # fluent speech the next cue starts on the next word, so the cue is whole for one word's length
            # whatever the edit does (the payoff is the last caption's rule)
            tail_ok = (nx_ is None or (int(nx_["rec_in"]) - int(t["rec_out"])) / fps + 1e-9 >= REVEAL_PAUSE_S
                       or bool(re.search(r"[.!?\u2026][\"')\]\u201d\u2019]*\s*$", shown_text(t))))
            lim = REVEAL_LAST_S if t is last_cap else (REVEAL_HOLD_S if P.get("reveal") == "words" and tail_ok
                                                       else None)
            if lim is not None and whole + 1e-9 < lim:
                out.append(_chk("reveal_hold", "WARN", [int(t["rec_in"]) + full], [t["id"]], "%s%s is whole on screen "
                                "for %.2f s after its last word appears (%s): it needs %.1f s" % (
                                    "the last caption " if t is last_cap else "", t["id"], whole,
                                    one_line_text(shown_text(t)), lim),
                                "keep the line in one cue and hold it: give the words a longer tail (pad_ms) or end "
                                "the picture later%s; a reveal on a short cue shows it whole too briefly" % (
                                    " (the payoff line holds to the end)" if t is last_cap else "")))
        if st:
            sbox = safe
            if sbox:
                bx = animated_ink_box(t, st, W, H) or animated_box(t, st, W, H)
                if bx[0] < sbox[0] - 1 or bx[1] < sbox[1] - 1 or bx[0] + bx[2] > sbox[0] + sbox[2] + 1 or \
                        bx[1] + bx[3] > sbox[1] + sbox[3] + 1:
                    out.append(_chk("safe_zone", "STOP", [int(t["rec_in"])], [t["id"]], "%s leaves the safe area at its "
                                    "largest scale (%.2f)" % (t["id"], max_scale(st)),
                                    "shorten the text, lower the overshoot or move it inside the safe area"))
        lk = t.get("look") or {}
        le, measured = line_em_of(lk.get("font") or "Arial", lk.get("style") or "Bold", lk.get("font_file"))
        if not measured:
            out.append(_chk("text_font_metrics", "WARN", [int(t["rec_in"])], [t["id"]], "%s: the font %r %r was not "
                            "found, so its Text+ size falls back to the Arial rule (0.70)" % (
                                t["id"], lk.get("font"), lk.get("style")),
                            "install the font or give the look a font_file"))
        if role_caption and an.get("keyword") is not None:
            kw_cues += 1
        if role_caption and isinstance(an.get("keywords_in_cue"), int) and an["keywords_in_cue"] > 1:
            out.append(_chk("caption_keywords", "WARN", [int(t["rec_in"])], [t["id"]], "%s holds %d keywords (one per "
                            "cue reads)" % (t["id"], an["keywords_in_cue"]), "keep one keyword per cue"))
    an_caps = [c for c in caps if (c.get("anim") or {}).get("id") not in (None, "none")]
    if an_caps and kw_cues > 0.35 * len(an_caps) + 1e-9 and len(an_caps) >= 3:
        out.append(_chk("caption_keywords", "WARN", [], [], "keywords on %d of %d cues (%.0f %%, keep about 30 %%)" % (
            kw_cues, len(an_caps), 100.0 * kw_cues / len(an_caps)), "keep keywords for the words that carry the "
                                                                    "message"))
    # sound effects
    sfx = [(tr, it) for k, tr, it in _items(edl, ("audio",)) if tr.get("role") == "sfx"]
    snd = sound_events(edl)
    for tr, it in sfx:
        pf = it.get("peak_f")
        # the effect's own sound frame when the item names it (a punch: its word), else the item's event
        ev_f = snd.get(it.get("for"), it.get("event_f"))
        if pf is not None and ev_f is not None and abs(int(pf) - int(ev_f)) > 1:
            out.append(_chk("fx_sfx_sync", "WARN", [int(ev_f)], [it["id"]], "%s peaks %+d frames from its event (%s)" % (
                it["id"], int(pf) - int(ev_f), it.get("for")), "assemble places the peak on the event: assemble again"))
    if lab is not None and sfx:
        d_pk = _peak_db_of_role(edl, lab, "dialogue")
        for tr, it in sfx:
            s_pk = _peak_db_item(edl, lab, it)
            if d_pk is not None and s_pk is not None and s_pk > d_pk - 3.0:
                rel = ("%.1f dB above" % (s_pk - d_pk)) if s_pk > d_pk + 0.05 else (
                    "level with" if s_pk >= d_pk - 0.05 else "only %.1f dB under" % (d_pk - s_pk))
                out.append(_chk("sfx_loud", "WARN", [int(it["rec_in"])], [it["id"]], "%s peaks at %.1f dBFS, %s "
                                "the dialogue peaks (keep 3 to 6 dB under)" % (it["id"], s_pk, rel),
                                "lower its gain_db by %.0f dB" % math.ceil(s_pk - d_pk + 3.0)))
    moves = [x for x in tx if (tr_row(transition_key(x)) or {}).get("family") in ("whip", "zoom")]
    if moves and not sfx and genre in ("creator_reel", "music_montage"):
        out.append(_chk("sfx_missing", "INFO", [], [x.get("id") for x in moves], "%d whips or zooms and no sound "
                        "effect under them" % len(moves), "add a whoosh from the user's library (M add --sfx)"))
    # fx_soft: a peak zoom beyond the source's resolution (KNOWLEDGE section 6 headroom)
    for k_, tr, it in _items(edl, ("video",)):
        ks = (it.get("motion") or {}).get("keys") or {}
        if not ks.get("zoom"):
            continue
        m = (edl.get("media") or {}).get(it.get("media")) or {}
        try:
            w, h = float(m.get("width") or 0), float(m.get("height") or 0)
        except (TypeError, ValueError):
            w = h = 0
        if w <= 0 or h <= 0:
            continue
        if int(float(m.get("rotation") or 0)) % 180 == 90:
            w, h = h, w
        sizing = ((edl.get("timeline") or {}).get("resolve") or {}).get("input_sizing") or "scaleToFit"
        k = max(W / w, H / h) if sizing in ("scaleToCrop", "crop", "fill") else min(W / w, H / h)
        z0 = float((it.get("transform") or {}).get("zoom", 1.0) or 1.0)
        zt = z0 * max(v for f, v in ks["zoom"])
        # only the keyed zoom's own softness: a source already upscaled by its static framing is another matter.
        # KNOWLEDGE s6 headroom: up to 1.15 x over the source's pixels reads fine (a 1080p source into 1080p
        # delivery may punch to 1.15), beyond it the picture softens
        if k * zt > SOFT_UPSCALE + 1e-3 and k * z0 <= 1.0 + 1e-3:
            out.append(_chk("fx_soft", "WARN", [int(it["rec_in"])], [it["id"]], "%s zooms to %.2f: %s is upscaled %.2f x "
                            "at the peak (over %.2f) and looks soft" % (it["id"], zt, it.get("media"), k * zt,
                                                                       SOFT_UPSCALE),
                            "zoom less or use a higher resolution source"))
    return out


def _peak_db_item(edl, lab, it):
    m = (edl.get("media") or {}).get(it.get("media")) or {}
    p = m.get("wav") or (m.get("path") if str(m.get("path", "")).lower().endswith(".wav") else None)
    if not p:
        return None
    pk = _wav_peak(lab.rel(p) if lab is not None else p)
    if pk is None or pk <= 0:
        return None
    return 20 * math.log10(pk) + float(it.get("gain_db", 0.0) or 0.0)


def _peak_db_of_role(edl, lab, role):
    best = None
    for tr in (edl.get("tracks") or {}).get("audio", []):
        if tr.get("role") != role:
            continue
        for it in tr.get("items", []):
            v = _peak_db_item(edl, lab, it)
            if v is not None and (best is None or v > best):
                best = v
    return best


_WPEAK = {}


def _wav_peak(path):
    try:
        st = os.stat(path)
    except OSError:
        return None
    k = (path, st.st_size, st.st_mtime_ns)
    if k not in _WPEAK:
        x = read_wav_mono(path)
        _WPEAK[k] = None if x is None else float(np.max(np.abs(x))) if len(x) else 0.0
    return _WPEAK[k]


def read_wav_mono(path):
    """A 16-bit PCM WAV as float mono samples (None for other files)."""
    import wave
    try:
        with wave.open(path) as w:
            if w.getsampwidth() != 2:
                return None
            raw = w.readframes(w.getnframes())
            ch = w.getnchannels()
            sr = w.getframerate()
    except (OSError, EOFError, Exception):
        return None
    a = np.frombuffer(raw, "<i2").astype(np.float32) / 32768.0
    if ch > 1:
        a = a[: len(a) - len(a) % ch].reshape(-1, ch).mean(axis=1)
    read_wav_mono.sr = sr
    return a


def audio_peak_s(samples, sr, win_ms=20):
    """Seconds into a sound of its loudest 20 ms window, where a whoosh or hit lands ([REEL] audio_peak_s)."""
    x = np.asarray(samples, dtype=np.float64)
    if x.ndim > 1:
        x = x.mean(axis=1)
    if not len(x):
        return 0.0
    w = max(1, int(sr * win_ms / 1000.0))
    e = np.convolve(x * x, np.ones(w) / w, mode="same")
    return float(np.argmax(e)) / sr


# ------------------------------------------------------------------------------------------------ pixel plan, grabs
def pixel_plan(edl):
    """The pixel-check rule of every effect span (timeline frames, half open [f0, f1)): [{"f0", "f1", "tolerance",
    "min_corr", "scope", "aux", "mark", "fx"}] sorted ([PRV] s10). On a frame inside several spans a mark wins
    (the frame is not correlated), then a close-class transition's span median, else the highest min_corr applies
    (see frame_rule)."""
    cat = load_catalog()
    out = []

    def add(f0, f1, tol_name, fid, mark=None):
        t = tolerance(tol_name)
        out.append({"f0": int(f0), "f1": int(f1), "tolerance": tol_name, "min_corr": t.get("min_corr"),
                    "scope": t.get("scope"), "aux": t.get("aux"), "mark": bool(t.get("mark")) if mark is None else mark,
                    "fx": fid})
    by_id = {}
    for kind, tr, it in _items(edl, ("video",)):
        by_id[it["id"]] = it
        r0 = int(it.get("rec_in", 0))
        for x in it.get("fx") or []:
            if x.get("refused"):
                continue
            f = x.get("f") or [x.get("event_f", 0)] * 2
            row = fx_row(x.get("kind")) or {}
            approx = (row.get("preview") or {}).get("class") == "approx"
            add(r0 + int(f[0]), r0 + int(f[1]) + 1, x.get("tolerance") or row.get("tolerance") or "push_zoom_keyed",
                x.get("id"), True if approx else None)
        if it.get("retime_process") == "speed_warp" and abs(float(it.get("speed", 1.0) or 1.0) - 1.0) > 1e-9:
            add(r0, int(it["rec_out"]), "speed_warp", "%s.speed_warp" % it["id"])
        if it.get("kind") == "title" and (it.get("anim") or {}).get("id") not in (None, "none"):
            add(r0, int(it["rec_out"]), "text_animated", "%s.anim" % it["id"])
    for kind, tr, c in _items(edl, ("subtitle",)):
        if (c.get("anim") or {}).get("id") not in (None, "none"):
            add(int(c["rec_in"]), int(c["rec_out"]), "text_animated", "%s.anim" % c["id"])
    for x in edl.get("transitions") or []:
        key = transition_key(x)
        row = tr_row(key) or {}
        if (row.get("resolve") or {}).get("category") == "audio":
            continue
        w = transition_window(edl, x, by_id)
        if w is None:
            continue
        tname = x.get("tolerance") or row.get("tolerance") or "transition_exact"
        add(w[0], w[1], tname, x.get("id"))
    out.sort(key=lambda s: (s["f0"], s["f1"], str(s["fx"])))
    return out


def frame_rule(plan, f):
    """The rule for one timeline frame: None outside every span, else the merged span rule."""
    hit = [s for s in plan if s["f0"] <= f < s["f1"]]
    if not hit:
        return None
    if any(s["mark"] or s["min_corr"] is None for s in hit):
        m = [s for s in hit if s["mark"] or s["min_corr"] is None][0]
        return dict(m)
    # a close-class transition (checked on its span median) keeps its frames when a keyed fx overlaps it: its
    # frames carry the transition's own blur, which a per-frame rule would flag (a bump on the cut of a blurred
    # zoom-through scored 0.92 against 0.95)
    med = [s for s in hit if s.get("scope") == "span_median"]
    if med:
        return dict(min(med, key=lambda s: s["min_corr"]))
    return dict(max(hit, key=lambda s: s["min_corr"]))


def grab_targets(edl, max_n=30):
    """Timeline frames to grab for the pixel check: the peak (event frame) of every fx, the middle of every
    transition, the frames just outside approximate spans, deterministic and sorted. When more are wanted than
    max_n, the fx peaks come first, then transition middles, then the frames around approximate spans."""
    N = max([int(it["rec_out"]) for k, tr, it in _items(edl, ("video", "audio"))] or [0])
    peaks, mids, around = [], [], []
    by_id = {}
    for kind, tr, it in _items(edl, ("video",)):
        by_id[it["id"]] = it
        r0 = int(it.get("rec_in", 0))
        for x in it.get("fx") or []:
            if x.get("refused"):
                continue
            peaks.append(r0 + int(x.get("event_f", (x.get("f") or [0])[0])))
    for x in edl.get("transitions") or []:
        key = transition_key(x)
        if ((tr_row(key) or {}).get("resolve") or {}).get("category") == "audio":
            continue
        w = transition_window(edl, x, by_id)
        if w:
            mids.append((w[0] + w[1]) // 2)
    for s in pixel_plan(edl):
        if s["mark"] or s["min_corr"] is None:
            around += [s["f0"] - 1, s["f1"]]
    out = []
    for f in peaks + mids + around:
        f = int(f)
        if 0 <= f < N and f not in out:
            out.append(f)
        if len(out) >= max_n:
            break
    return sorted(out)


# ------------------------------------------------------------------------------------------------ measurements
def aux_stats(rgb):
    """rgb: (h, w, 3) uint8 at the preview raster -> mean level, red-against-blue horizontal offset in px (an RGB
    split), high-frequency energy (grain up; blur and motion blur down) and the corner-to-centre ratio (vignette,
    leaks) ([PRV] aux_stats). Use them RELATIVE to a frame of the same shot outside the effect."""
    f = np.asarray(rgb).astype(np.float32)
    grey = f.mean(2)
    out = {"mean": float(grey.mean())}

    def prof(c):
        g = np.diff(f[:, :, c], axis=1)
        return g - g.mean()
    r, b = prof(0), prof(2)
    best, arg = -1e18, 0
    for s in range(-24, 25):
        if s >= 0:
            v = float((r[:, s:] * b[:, :r.shape[1] - s]).sum())
        else:
            v = float((r[:, :s] * b[:, -s:]).sum())
        if v > best:
            best, arg = v, s
    out["rb_offset_px"] = arg
    k = (grey[:-2, :-2] + grey[1:-1, :-2] + grey[2:, :-2] + grey[:-2, 1:-1] + grey[1:-1, 1:-1] + grey[2:, 1:-1] +
         grey[:-2, 2:] + grey[1:-1, 2:] + grey[2:, 2:]) / 9.0
    out["hf_energy"] = float(np.abs(grey[1:-1, 1:-1] - k).mean())
    h, w = grey.shape
    cs = max(4, h // 8)
    corners = np.concatenate([grey[:cs, :cs].ravel(), grey[:cs, -cs:].ravel(), grey[-cs:, :cs].ravel(),
                              grey[-cs:, -cs:].ravel()])
    centre = grey[h // 2 - cs:h // 2 + cs, w // 2 - cs:w // 2 + cs]
    out["corner_ratio"] = float(corners.mean() / max(1.0, centre.mean()))
    return out


TEXT_LIMITS = {"bbox_px": 8, "overlap": 0.90, "accent_dx": 14, "strength": 0.20}
TEXT_STRENGTH_MIN_PX = 300     # under this many text pixels (a word popping in at half size) the mean level is its
                               # anti-aliased edge: Text+ draws it about 35 % bolder than the preview (measured on a
                               # word_pop proof: 87 to 256 px at the pop frame, 900 to 2100 px when whole)


def outline_level(look):
    """The luma under which a pixel is the dark outline, shadow or box of a light text look (its darkest such colour
    plus 80), or None when the look has none: then a light fill alone marks the text. A soft drop shadow only darkens
    what is under it by its opacity, so it counts at its level over white (a 0.45 black shadow: 140, edge 220):
    keyed on full black, a shadow-only title over a light wall lost the glyphs whose shadow fell on the wall, and
    the render and the preview differed there by 10 px (sandbox proof, final fix)."""
    lv = []
    for k in ("stroke", "shadow", "box"):
        v = (look or {}).get(k)
        c = v.get("color") if isinstance(v, dict) else None
        if isinstance(c, str) and re.fullmatch(r"#?[0-9A-Fa-f]{6}", c.strip()):
            h = c.strip().lstrip("#")
            r_, g_, b_ = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
            lum = 0.2126 * r_ + 0.7152 * g_ + 0.0722 * b_
            if lum > 100:
                continue
            if k == "shadow":
                try:
                    o = min(1.0, max(0.0, float(v.get("opacity", 1.0) if v.get("opacity") is not None else 1.0)))
                except (TypeError, ValueError):
                    o = 1.0
                lum = min(170.0, o * lum + (1.0 - o) * 255.0)
            lv.append(lum)
    return (min(lv) + 80.0) if lv else None


def _masks(a, bg, fill, edge=None):
    a = np.asarray(a)
    if bg is not None:
        d = np.abs(a.astype(np.int16) - np.int16(bg)).max(-1)
        m = d > 40
    else:
        d = 255 - np.abs(a.astype(np.int16) - np.array(fill, np.int16)).max(-1)
        m = d > 215
        if edge is not None:
            # a light thing in the picture (a ring light, a white wall, a sleeve) passes the fill test too, sharper in
            # the render than in the proxy preview; glyphs with a dark outline, shadow or box are fill pixels next to
            # it (within 3 px), and the light things of the picture are not
            lum = a[..., :3].astype(np.float32) @ np.array([0.2126, 0.7152, 0.0722], np.float32)
            m = m & _dil(lum <= edge, 3)
    yel = (a[..., 0] > 200) & (a[..., 1] > 170) & (a[..., 2] < 130)
    pur = (a[..., 2] > 150) & (a[..., 0] < 170) & (a[..., 1] < 120)
    return m, d.astype(np.float32), yel | pur


def _dil(m, r):
    from PIL import Image, ImageFilter
    im = Image.fromarray((m * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(2 * r + 1))
    return np.asarray(im) > 0


def _dense(m, r=2, k=4):
    """The mask without specks: pixels with at least k mask pixels in their (2r+1) square (a stray highlight of the
    background is a pixel or two; a glyph stroke has many neighbours)."""
    from PIL import Image, ImageFilter
    im = Image.fromarray((np.asarray(m) * 255).astype(np.uint8)).filter(ImageFilter.BoxBlur(r))
    return np.asarray(m) & (np.asarray(im, np.float32) * ((2 * r + 1) ** 2) / 255.0 >= k - 0.5)


def _bbox(m):
    ys, xs = np.nonzero(m)
    return None if len(xs) == 0 else (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))


def text_compare(render_frames, preview_frames, boxes, bg=None, fill=(255, 255, 255), alphas=None, tol=4,
                 limits=None, edge=None):
    """Animated text on the proof render against the preview, frame by frame ([TXT] compare.py): inside each
    frame's box (x, y, w, h, timeline px of the same raster as the frames), the text mask's bounding box (limit 8
    px), a tolerant overlap (each mask inside the other dilated by tol px, limit 0.90), the accent colour centroid
    (14 px) and the text strength (0.20). With bg (a flat background level) the mask is the difference to it, else
    pixels close to the fill colour (with edge, outline_level of the look, only those next to its dark outline,
    shadow or box). render_frames, preview_frames, boxes: equal-length lists (frames HxWx3 uint8).
    Returns {"result": OK|STOP, "frames", "worst", "fails"}."""
    lim = dict(TEXT_LIMITS, **(limits or {}))
    worst, fails = {}, []
    n = min(len(render_frames), len(preview_frames), len(boxes))
    for i in range(n):
        x, y, w, h = [int(round(v)) for v in boxes[i]]
        ra = np.asarray(render_frames[i])[max(0, y):y + h, max(0, x):x + w]
        pa = np.asarray(preview_frames[i])[max(0, y):y + h, max(0, x):x + w]
        if ra.size == 0 or pa.size == 0:
            continue
        mr, dr, ar = _masks(ra, bg, fill, edge)
        mp, dp, ap = _masks(pa, bg, fill, edge)
        alpha = 1.0 if alphas is None else float(alphas[i])
        # with a flat background the difference mask holds at partial alpha ([TXT] s7: 0.6); against real footage the
        # fill-colour mask of a half-faded word depends on the background level (integration: identical-looking fade
        # frames of a slide_hook title scored bbox errors of 100 px), so the shape is judged on solid frames only
        geo = 0.6 if bg is not None else 0.9
        fl = []
        br, bp = _bbox(_dense(mr)), _bbox(_dense(mp))
        if (br is None) != (bp is None):
            # a fading frame sits on the mask threshold (sandbox AT-A6: white 252 in the render, 255 in the preview at
            # alpha 0.75), so presence is judged only on frames at 0.9 or more
            if alpha >= 0.9:
                fl.append("presence render=%s preview=%s" % (br is not None, bp is not None))
        elif br is not None and alpha >= geo:
            db = max(abs(u - v) for u, v in zip(br, bp))
            worst["bbox_px"] = max(worst.get("bbox_px", 0), db)
            if db > lim["bbox_px"]:
                fl.append("bbox %s vs %s" % (br, bp))
            o1 = (mp & _dil(mr, tol)).sum() / max(1, mp.sum())
            o2 = (mr & _dil(mp, tol)).sum() / max(1, mr.sum())
            ov = float(min(o1, o2))
            worst["overlap"] = min(worst.get("overlap", 1.0), round(ov, 3))
            if ov < lim["overlap"]:
                fl.append("overlap %.2f" % ov)
        u = mr | mp
        if br is not None and bp is not None and alpha >= geo and int(u.sum()) >= TEXT_STRENGTH_MIN_PX:
            sr, sp_ = float(dr[u].mean()), float(dp[u].mean())
            ds = abs(sr - sp_) / max(1.0, max(sr, sp_))
            worst["strength"] = max(worst.get("strength", 0.0), round(ds, 3))
            if ds > lim["strength"]:
                fl.append("strength %.0f vs %.0f" % (sr, sp_))
        nr, np_ = int(ar.sum()), int(ap.sum())
        if (nr > 150) != (np_ > 150) and max(nr, np_) > 2 * min(nr, np_):
            # present in one and (nearly) absent in the other; 148 against 154 is the same accent
            fl.append("accent render=%d px preview=%d px" % (nr, np_))
        elif nr > 150:
            cxr = float(np.nonzero(ar)[1].mean())
            cxp = float(np.nonzero(ap)[1].mean())
            worst["accent_dx"] = max(worst.get("accent_dx", 0.0), round(abs(cxr - cxp), 1))
            if abs(cxr - cxp) > lim["accent_dx"]:
                fl.append("accent x %.0f vs %.0f" % (cxr, cxp))
        if fl:
            fails.append([i] + fl)
    return {"result": "OK" if not fails else "STOP", "frames": n, "worst": worst, "fails": fails[:20],
            "n_fail_frames": len(fails), "limits": lim}


# ------------------------------------------------------------------------------------------------ preview filters
PERSP_FRAME = "(on-1)"       # perspective's frame counters start at 1 ([PRV] s4.1)


def keyed(keys, var="on"):
    """A piecewise LINEAR ffmpeg expression of var through [[frame, value], ...] as a balanced if-tree (a flat chain
    of about 100 ifs is refused by the parser) ([PRV] keyed)."""
    keys = sorted([(float(f), float(v)) for f, v in keys])
    if not keys:
        return "0"
    if len(keys) == 1:
        return "%.6f" % keys[0][1]

    def seg(k0, k1):
        (f0, v0), (f1, v1) = k0, k1
        if abs(v1 - v0) < 1e-12:
            return "%.6f" % v0
        return "(%.6f+(%.6f)*(%s-%.4f)/%.4f)" % (v0, v1 - v0, var, f0, max(1e-6, f1 - f0))

    def tree(lo, hi):
        if hi - lo == 1:
            return seg(keys[lo], keys[lo + 1])
        mid = (lo + hi) // 2
        return "if(lt(%s,%.4f),%s,%s)" % (var, keys[mid][0], tree(lo, mid), tree(mid, hi))
    return "if(lt(%s,%.4f),%.6f,if(gte(%s,%.4f),%.6f,%s))" % (var, keys[0][0], keys[0][1], var, keys[-1][0],
                                                               keys[-1][1], tree(0, len(keys) - 1))


def piece_keys(keys, k0, n):
    """The keys of item frames [k0, k0 + n) as piece frames 0..n-1."""
    if not keys:
        return []
    return shift_keys(slice_keys(keys, k0, k0 + n - 1), -k0)


def picture_rect(spec, raster, W, H, sizing):
    """The frame rectangle (x0, y0, x1, y1, whole preview pixels) the item's own picture covers after the input
    sizing and the static zoom, pan and tilt, or None when it covers the whole frame (or its size is unknown).
    A clip comp works at the SOURCE resolution and the timeline sizes its output afterwards (fusion_api notes,
    verified): every keyed move, mirror or wrap edge and accent stays inside this rectangle, and the letterbox or
    pillarbox bars, a zoom under 1 or a pan gap stay black (or show the tracks under a layer)."""
    pic = spec.get("pic")
    if not pic or not pic[0] or not pic[1]:
        return None
    pw, ph = raster
    w, h = float(pic[0]), float(pic[1])
    if sizing in ("stretch", "scaleToStretch"):
        iw, ih = float(pw), float(ph)
    else:
        k = max(pw / w, ph / h) if sizing in ("scaleToCrop", "crop", "fill") else min(pw / w, ph / h)
        iw, ih = w * k, h * k
    z0 = float(spec.get("zoom", 1.0) or 1.0)
    cx = pw / 2.0 + float(spec.get("pan", 0.0) or 0.0) * pw / float(W)
    cy = ph / 2.0 - float(spec.get("tilt", 0.0) or 0.0) * ph / float(H)
    x0 = max(0, int(round(cx - z0 * iw / 2.0)))
    x1 = min(int(pw), int(round(cx + z0 * iw / 2.0)))
    y0 = max(0, int(round(cy - z0 * ih / 2.0)))
    y1 = min(int(ph), int(round(cy + z0 * ih / 2.0)))
    if x0 <= 0 and y0 <= 0 and x1 >= pw and y1 >= ph:
        return None
    if x1 <= x0 or y1 <= y0:
        return (0, 0, 0, 0)
    return (x0, y0, x1, y1)


def picture_clip(spec, raster, W, H, sizing, layer=False):
    """The filters that cut a clip-comp piece (a keyed move or an accent) back to its picture's rectangle and put it
    over black (transparent for a layer): '' when the picture fills the frame. See picture_rect."""
    if not (spec.get("motion") or spec.get("accents")):
        return ""
    r = picture_rect(spec, raster, W, H, sizing)
    if r is None:
        return ""
    pw, ph = raster
    x0, y0, x1, y1 = r
    fmt, bg = ("rgba", "black@0") if layer else ("yuv444p", "black")
    if x1 <= x0 or y1 <= y0:
        # nothing of the picture is in the frame
        if layer:
            return "format=rgba,colorchannelmixer=rr=0:gg=0:bb=0:aa=0,format=rgba"
        return "format=yuv444p,drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill,format=yuv420p"
    return "format=%s,crop=%d:%d:%d:%d:exact=1,pad=%d:%d:%d:%d:%s,format=%s" % (
        fmt, x1 - x0, y1 - y0, x0, y0, pw, ph, x0, y0, bg, "rgba" if layer else "yuv420p")


def motion_chain(spec, raster, W, H, sizing, tfps, tag="m", bg="black"):
    """The keyed move of one clip piece as an ffmpeg chain: fill the frame as the input sizing does (the image may
    overhang it), the static zoom, pan and tilt, then the keyed zoom about the point, the translation and the
    rotation, all in ONE perspective transform per frame (sub-pixel, no zoompan jitter, [PRV] s4), on a canvas big
    enough for the move (black, or mirrored or wrapped copies for edges mirror and wrap), cut to the frame.
    spec["motion"]: {"point", "edges", "motion_blur", "keys"} in piece frames. Returns a filter chain string."""
    pw, ph = raster
    mo = spec["motion"]
    ks = mo.get("keys") or {}
    z0 = float(spec.get("zoom", 1.0) or 1.0)
    px = float(spec.get("pan", 0.0) or 0.0) * pw / float(W)
    py = float(spec.get("tilt", 0.0) or 0.0) * ph / float(H)
    qx = (float((mo.get("point") or [0.5, 0.5])[0]) - 0.5) * pw
    qy = (float((mo.get("point") or [0.5, 0.5])[1]) - 0.5) * ph
    n = int(spec["n"])
    zk, xk, yk, ak = ks.get("zoom") or [[0, 1.0]], ks.get("x_px") or [], ks.get("y_px") or [], ks.get("angle") or []
    sx, sy = pw / float(W), ph / float(H)
    xk = [[f, v * sx] for f, v in xk]
    yk = [[f, v * sy] for f, v in yk]
    blur = mo.get("motion_blur")
    sub = 8 if blur else 1
    # reach: the farthest source point of the frame's corners over the piece (with a margin)
    reach_x, reach_y = pw / 2.0, ph / 2.0
    for f in [i / float(sub) - 0.5 + 0.5 / sub for i in range(n * sub)] if sub > 1 else range(n):
        m = max(1e-3, value_at(zk, f, 1.0))       # (validate refuses a keyed zoom under 1; never divide by 0)
        tx = value_at(xk, f, 0.0)
        ty = value_at(yk, f, 0.0)
        th = math.radians(value_at(ak, f, 0.0))
        c, s = math.cos(th), math.sin(th)
        for ux, uy in ((-pw / 2.0, -ph / 2.0), (pw / 2.0, -ph / 2.0), (-pw / 2.0, ph / 2.0), (pw / 2.0, ph / 2.0)):
            ax, ay = ux - qx - tx, uy - qy - ty
            bx = (c * ax - s * ay) / m + qx
            by = (s * ax + c * ay) / m + qy
            reach_x = max(reach_x, abs((bx - px) / z0))
            reach_y = max(reach_y, abs((by + py) / z0))
    cw = 2 * int(math.ceil(reach_x + 3))
    ch = 2 * int(math.ceil(reach_y + 3))
    var = PERSP_FRAME if sub == 1 else "((%s-%d/2+0.5)/%d)" % (PERSP_FRAME, sub, sub)
    m_e = keyed(zk, var) if keys_vary(zk, zk[0][1]) or abs(zk[0][1] - 1.0) > 1e-12 else "1"
    tx_e = keyed(xk, var) if xk else "0"
    ty_e = keyed(yk, var) if yk else "0"
    rot = bool(ak) and keys_vary(ak, 0.0)
    a_e = "(%s)*PI/180" % keyed(ak, var) if rot else None
    f = []
    edges = mo.get("edges")
    if sizing in ("scaleToCrop", "crop", "fill"):
        f.append("scale=%d:%d:force_original_aspect_ratio=increase:flags=bilinear" % (pw, ph))
    elif sizing in ("stretch", "scaleToStretch"):
        f.append("scale=%d:%d:flags=bilinear" % (pw, ph))
    else:
        f.append("scale=%d:%d:force_original_aspect_ratio=decrease:flags=bilinear" % (pw, ph))
    # a full-resolution chroma format: the canvas pads and crops by odd pixels, which 4:2:0 would round to even
    # (measured: a one pixel shift of the whole picture)
    f.append("format=%s" % ("yuva444p" if "@" in str(bg) else "yuv444p"))
    if edges in ("mirror", "wrap"):
        # three copies each way (mirrored or plain), so any reach within one image size finds picture
        t_ = tag
        if edges == "mirror":
            f.append(("split=3[{t}_a][{t}_b][{t}_c];[{t}_b]hflip[{t}_bh];[{t}_c]hflip[{t}_ch];"
                      "[{t}_bh][{t}_a][{t}_ch]hstack=3,split=3[{t}_r0][{t}_r1][{t}_r2];[{t}_r0]vflip[{t}_v0];"
                      "[{t}_r2]vflip[{t}_v2];[{t}_v0][{t}_r1][{t}_v2]vstack=3").format(t=t_))
        else:
            f.append(("split=3[{t}_a][{t}_b][{t}_c];[{t}_a][{t}_b][{t}_c]hstack=3,split=3[{t}_r0][{t}_r1][{t}_r2];"
                      "[{t}_r0][{t}_r1][{t}_r2]vstack=3").format(t=t_))
    f.append("pad=max(iw\\,%d):max(ih\\,%d):(ow-iw)/2:(oh-ih)/2:%s" % (cw, ch, bg))
    f.append("crop=%d:%d:(iw-%d)/2:(ih-%d)/2" % (cw, ch, cw, ch))
    if sub > 1:
        f.append("fps=%s" % _rate_s(float(tfps) * sub))

    def src(ux, uy):
        # canvas point (ux, uy) of the output -> source point on the canvas (frame centre = canvas centre)
        ax = "(%s-W/2-(%.4f)-(%s))" % (ux, qx, tx_e)
        ay = "(%s-H/2-(%.4f)-(%s))" % (uy, qy, ty_e)
        if rot:
            bx = "((cos(%s)*%s-sin(%s)*%s)/(%s)+(%.4f))" % (a_e, ax, a_e, ay, m_e, qx)
            by = "((sin(%s)*%s+cos(%s)*%s)/(%s)+(%.4f))" % (a_e, ax, a_e, ay, m_e, qy)
        else:
            bx = "(%s/(%s)+(%.4f))" % (ax, m_e, qx)
            by = "(%s/(%s)+(%.4f))" % (ay, m_e, qy)
        return ("W/2+(%s-(%.4f))/%.6f" % (bx, px, z0)), ("H/2+(%s+(%.4f))/%.6f" % (by, py, z0))
    corners = [src("0", "0"), src("W", "0"), src("0", "H"), src("W", "H")]
    f.append("perspective=x0='%s':y0='%s':x1='%s':y1='%s':x2='%s':y2='%s':x3='%s':y3='%s':interpolation=linear:"
             "eval=frame" % tuple(v for c in corners for v in c))
    f.append("crop=%d:%d:(iw-%d)/2:(ih-%d)/2:exact=1" % (pw, ph, pw, ph))
    if sub > 1:
        # 360 degree shutter: the sub-frame samples of each frame averaged, back to the timeline rate and length
        f.append("tmix=frames=%d,select='not(mod(n+1,%d))',setpts=N/(%s*TB),fps=%s,trim=end_frame=%d,"
                 "setpts=PTS-STARTPTS" % (sub, sub, float(tfps), _rate_s(tfps), n))
    return ",".join(f)


def flash_graph(src, out, keys, n, raster, tfps):
    """A lerp to white by v per frame (BrightnessContrast Gain 1 - v, Brightness v in the build): a white layer
    whose alpha rides a tiny keyed mask, overlaid ([PRV] mix_graph idea, 0.8 ms a frame)."""
    pw, ph = raster
    w = keyed(keys, "N")
    fr_ = float(tfps)
    return ("color=c=white:s=%dx%d:r=%s:d=%.4f,format=rgba[%s_w];color=c=black:s=2x2:r=%s:d=%.4f,format=gray,"
            "geq=lum='255*clip(%s,0,1)',scale=%d:%d:flags=neighbor[%s_m];[%s_w][%s_m]alphamerge[%s_wa];"
            "[%s][%s_wa]overlay=format=auto:eof_action=pass,format=yuv420p[%s]"
            % (pw, ph, fr_, (n + 1) / fr_, out, fr_, (n + 1) / fr_, w, pw, ph, out, out, out, out, src, out, out))


def rgb_chain(keys, n, raster, W, tfps):
    """Red right and blue left by the keyed timeline px (scaled to the preview), sub-pixel through geq on the frames
    that split ([PRV] rgb_split_animated; red moves RIGHT for a positive value, as the build keys it). None when
    nothing splits inside the piece."""
    pw, ph = raster
    sk = [[f, v * pw / float(W)] for f, v in keys]
    on = [f for f in range(n) if abs(value_at(sk, f, 0.0)) > 1e-6]
    if not on:
        return None
    S = keyed(sk, "floor(T*%.9f+0.5)" % float(tfps))
    return ("format=gbrp,geq=r='r(X-(%s),Y)':g='g(X,Y)':b='b(X+(%s),Y)':interpolation=bilinear:"
            "enable='between(n,%d,%d)',format=yuv420p" % (S, S, min(on), max(on)))


def ramp_setpts(keys, fps):
    """The retime of a piece as ffmpeg filters on the clip's native frames ([PRV] ramp_setpts): keys [[piece frame,
    source frame from the first frame read]], linear between keys; each source frame goes to the first output frame
    that shows it, select drops frames a later one replaces, fps repeats frames where the ramp is slower than the
    source. Put settb=1/fps (timeline) before it."""
    segs = []
    for (t0, s0), (t1, s1) in zip(keys, keys[1:]):
        if s1 > s0:
            segs.append((float(s0), float(s1), float(t0), float(t1)))
    if not segs:
        segs = [(float(keys[0][1]), float(keys[0][1]) + 1.0, float(keys[0][0]), float(keys[-1][0]) + 1.0)]
    first_t, first_s = float(keys[0][0]), float(keys[0][1])

    def kexpr(v):
        # source frame v -> the FIRST output frame that shows it. A source frame that ends a rising segment belongs
        # to that segment (lte): it is where a following hold (freeze, or a flat stretch of a ramp) starts, so the
        # hold shows it from its first frame, as the build's keys do. Anything at or below the first key's source
        # frame shows from the first key (a leading hold).
        expr = None
        for s0, s1, t0, t1 in reversed(segs):
            kv = "(%.12g+(%s-%.12g)*%.12g)" % (t0, v, s0, (t1 - t0) / float(s1 - s0))
            expr = kv if expr is None else "if(lte(%s,%.12g),%s,%s)" % (v, s1, kv, expr)
        return "if(lte(%s,%.12g),%.12g,%s)" % (v, first_s, first_t, expr)
    e = 1e-4
    fps_s = fps if isinstance(fps, str) else "%.9g" % float(fps)
    return ("setpts='max(0,ceil(%s-%g))',select='lt(ceil(%s-%g),ceil(%s-%g))',fps=fps=%s:round=near"
            % (kexpr("N"), e, kexpr("n"), e, kexpr("(n+1)"), e, fps_s))


def leak_graph(src, out, leak, n, k0, raster, tfps):
    """A warm leak screened over the picture (approximate, [PRV] light_leak): a soft blob drawn at a quarter size
    whose centre drifts and whose strength follows the envelope (0 at the ends, the peak in the middle), on RGB."""
    pw, ph = raster
    qw, qh = max(8, pw // 4), max(8, ph // 4)
    a0, a1 = int(leak["f"][0]) - k0, int(leak["f"][1]) - k0
    span = max(1, a1 - a0)
    col = _hex(leak.get("color", "#FF8A3D"))
    pk = float(leak.get("peak", 0.55))
    fr_ = float(tfps)
    u = "clip((floor(T*%.9f+0.5)-%d)/%d,0,1)" % (fr_, a0, span)
    env = "(%.4f*sin(PI*%s)*between(floor(T*%.9f+0.5),%d,%d))" % (pk, u, fr_, a0, a1)
    cxe = "(%d*(0.2+0.6*%s))" % (qw, u)
    blob = "exp(-((X-%s)*(X-%s)+(Y-%d)*(Y-%d))/(2*%d*%d))" % (cxe, cxe, qh // 3, qh // 3, qw // 2, qw // 2)
    return ("color=c=black:s=%dx%d:r=%s:d=%.4f,format=gbrp,geq=r='%d*%s*%s':g='%d*%s*%s':b='%d*%s*%s',"
            "scale=%d:%d:flags=bicubic[%s_l];[%s]format=gbrp[%s_p];[%s_p][%s_l]blend=all_mode=screen,format=yuv420p[%s]"
            % (qw, qh, fr_, (n + 1) / fr_, col[0], env, blob, col[1], env, blob, col[2], env, blob, pw, ph, out, src,
               out, out, out, out))


def mix_weights(d, ein="linear", eout=None, phase=0.5):
    out = []
    for k in range(d):
        u = (k + phase) / float(d)
        out.append(_ease_pair(u, ein, eout or ein))
    return out


def _ease_in(name, u):
    if name == "linear":
        return u
    if name == "quad":
        return u * u
    if name == "cubic":
        return u ** 3
    if name == "quart":
        return u ** 4
    if name == "sine":
        return 1 - math.cos(u * math.pi / 2)
    if name == "expo":
        return 0.0 if u <= 0 else 2 ** (10 * (u - 1))
    raise FxError(name)


def _ease_pair(u, ein, eout):
    u = min(1.0, max(0.0, u))
    if u < 0.5:
        return 0.5 * _ease_in(ein, 2 * u)
    return 1 - 0.5 * _ease_in(eout, 2 * (1 - u))


def _ein_expr(name, u):
    if name == "linear":
        return "(%s)" % u
    if name in ("quad", "cubic", "quart"):
        return "pow(%s,%d)" % (u, {"quad": 2, "cubic": 3, "quart": 4}[name])
    if name == "sine":
        return "(1-cos((%s)*PI/2))" % u
    if name == "expo":
        return "if(lte(%s,0),0,pow(2,10*((%s)-1)))" % (u, u)
    raise FxError(name)


def ease_expr(u, ein="linear", eout=None):
    eout = eout or ein
    uc = "clip(%s,0,1)" % u
    return "if(lt(%s,0.5),0.5*%s,1-0.5*%s)" % (uc, _ein_expr(ein, "(2*%s)" % uc), _ein_expr(eout, "(2*(1-%s))" % uc))


def mix_graph(a, b, out, d, fps, weights, pw, ph):
    """[a] and [b] mixed with a per-frame weight list -> [out] ([PRV] mix_graph: the weight rides in a 2x2 mask)."""
    wexpr = keyed([(k, w) for k, w in enumerate(weights)], "N")
    return ("color=c=black:s=2x2:r=%s:d=%.4f,format=gray,geq=lum='255*(%s)',scale=%d:%d:flags=neighbor[%s_m];"
            "[%s][%s_m]alphamerge[%s_ba];[%s][%s_ba]overlay=format=auto:eof_action=pass,format=yuv420p[%s]"
            % (float(fps), (d + 1) / float(fps), wexpr, pw, ph, out, b, out, out, a, out, out))


def tr_brightness_flash(a, b, out, d, fps, pw, ph, brightness=0.67, saturation=1.83):
    """Fusion Brightness Flash from its template ([PRV] s3.3, 0.992 against Resolve): a Quart in-out dissolve, then
    BrightnessContrast blended in by a mirrored Sine in-out curve."""
    g = mix_graph(a, b, "%s_fx" % out, d, fps, mix_weights(d, "quart"), pw, ph)
    u = "((n+0.5)/%d)" % d
    w = ease_expr("1-abs(2*%s-1)" % u, "sine")
    return g + ";[%s_fx]eq=brightness='%.3f*%s':saturation='1+%.3f*%s':eval=frame[%s]" % (
        out, brightness, w, saturation - 1, w, out)


def xfade_graph(a, b, out, d, fps, transition):
    """ffmpeg xfade with Resolve's phase (k + 0.5)/d ([PRV] s3.1: settb 1/1000 and a -0.5 frame offset)."""
    off = -0.5 / float(fps)
    return ("[%s]settb=1/1000[%s_x];[%s]settb=1/1000[%s_y];[%s_x][%s_y]xfade=transition=%s:duration=%.6f:offset=%.6f,"
            "settb=1/%s,setpts=N,format=yuv420p[%s]" % (a, out, b, out, out, out, transition, d / float(fps), off,
                                                         _rate_s(fps), out))


def _rate_s(fps):
    from fractions import Fraction
    fr_ = Fraction(float(fps)).limit_denominator(1001)
    return "%d" % fr_.numerator if fr_.denominator == 1 else "%d/%d" % (fr_.numerator, fr_.denominator)


def tr_slide(a, b, out, d, fps, pw, ph, direction="left"):
    """Fusion Slide Left/Right/Up/Down from its template ([PRV] tr_slide, 0.87 left and right)."""
    u = "((n+0.5)/%d)" % d
    D = "(%s)" % ease_expr(u, "cubic")
    horiz = direction in ("left", "right")
    L = pw if horiz else ph
    if horiz:
        sa = "[%s]split[%s_a1][%s_a2];[%s_a2]hflip[%s_a3];" % (a, out, out, out, out)
        sb = "[%s]hflip,split[%s_b1][%s_b2];[%s_b2]hflip[%s_b3];" % (b, out, out, out, out)
        if direction == "left":
            ga = sa + "[%s_a1][%s_a3]hstack=2[%s_sa];" % (out, out, out)
            gb = sb + "[%s_b1][%s_b3]hstack=2[%s_sb];" % (out, out, out)
            x = "trunc(%s*%d)" % (D, L)
        else:
            ga = sa + "[%s_a3][%s_a1]hstack=2[%s_sa];" % (out, out, out)
            gb = sb + "[%s_b3][%s_b1]hstack=2[%s_sb];" % (out, out, out)
            x = "trunc(%d-%s*%d)" % (L, D, L)
        crop = "crop=%d:%d:'%s':0:exact=1" % (pw, ph, x)
    else:
        sa = "[%s]split[%s_a1][%s_a2];[%s_a2]vflip[%s_a3];" % (a, out, out, out, out)
        sb = "[%s]vflip,split[%s_b1][%s_b2];[%s_b2]vflip[%s_b3];" % (b, out, out, out, out)
        if direction == "up":
            ga = sa + "[%s_a1][%s_a3]vstack=2[%s_sa];" % (out, out, out)
            gb = sb + "[%s_b1][%s_b3]vstack=2[%s_sb];" % (out, out, out)
            y = "trunc(%s*%d)" % (D, L)
        else:
            ga = sa + "[%s_a3][%s_a1]vstack=2[%s_sa];" % (out, out, out)
            gb = sb + "[%s_b3][%s_b1]vstack=2[%s_sb];" % (out, out, out)
            y = "trunc(%d-%s*%d)" % (L, D, L)
        crop = "crop=%d:%d:0:'%s':exact=1" % (pw, ph, y)
    g = ga + gb + "[%s_sa]%s[%s_ca];[%s_sb]%s[%s_cb];" % (out, crop, out, out, crop, out)
    return g + mix_graph("%s_ca" % out, "%s_cb" % out, out, d, fps, mix_weights(d, "expo"), pw, ph)


def tr_pan(a, b, out, d, fps, pw, ph, direction="left"):
    """Fusion Pan Left/Right/Up/Down without its lens bulge ([PRV] tr_pan, approximate)."""
    u = "((n+0.5)/%d)" % d
    D = ease_expr(u, "quad")
    horiz = direction in ("left", "right")
    L = pw if horiz else ph
    st, fl = ("hstack", "hflip") if horiz else ("vstack", "vflip")
    g = ""
    for src, tag in ((a, "a"), (b, "b")):
        g += "[%s]split=3[%s_%s1][%s_%s2][%s_%s3];[%s_%s2]%s[%s_%s4];[%s_%s1][%s_%s4][%s_%s3]%s=3[%s_s%s];" % (
            src, out, tag, out, tag, out, tag, out, tag, fl, out, tag, out, tag, out, tag, out, tag, st, out, tag)
    if direction in ("left", "up"):
        pos = "trunc(2*%s*%d)" % (D, L)
    else:
        pos = "trunc(2*%d-2*%s*%d)" % (L, D, L)
    crop = ("crop=%d:%d:'%s':0:exact=1" % (pw, ph, pos)) if horiz else ("crop=%d:%d:0:'%s':exact=1" % (pw, ph, pos))
    g += "[%s_sa]%s[%s_ca];[%s_sb]%s[%s_cb];" % (out, crop, out, out, crop, out)
    return g + mix_graph("%s_ca" % out, "%s_cb" % out, out, d, fps, mix_weights(d, "quad"), pw, ph)


def _zoom_persp(z, pw, ph, cx=0.5, cy=0.5):
    X, Y = "%.6f*W" % cx, "%.6f*H" % cy
    xs = lambda u: "%s+(%s-%s)/(%s)" % (X, u, X, z)
    ys = lambda v: "%s+(%s-%s)/(%s)" % (Y, v, Y, z)
    return ("perspective=x0='%s':y0='%s':x1='%s':y1='%s':x2='%s':y2='%s':x3='%s':y3='%s':interpolation=linear:"
            "eval=frame" % (xs("0"), ys("0"), xs("W"), ys("0"), xs("0"), ys("H"), xs("W"), ys("H")))


def tr_zoom_in(a, b, out, d, fps, pw, ph):
    """Fusion Zoom In from its template ([PRV] tr_zoom_in, 0.968): A 1 -> 2, B 0.6 -> 1 from a mirror tile, a Cubic
    in-out dissolve."""
    u = "((%s+0.5)/%d)" % (PERSP_FRAME, d)
    e = ease_expr(u, "cubic")
    g = "[%s]%s[%s_za];" % (a, _zoom_persp("1+%s" % e, pw, ph), out)
    g += ("[{b}]split=3[{o}_t0][{o}_t1][{o}_t2];[{o}_t1]hflip[{o}_t1h];[{o}_t2]hflip[{o}_t2h];"
          "[{o}_t1h][{o}_t0][{o}_t2h]hstack=3,split=3[{o}_r0][{o}_r1][{o}_r2];[{o}_r0]vflip[{o}_v0];"
          "[{o}_r2]vflip[{o}_v2];[{o}_v0][{o}_r1][{o}_v2]vstack=3[{o}_t];").format(b=b, o=out)
    cw, ch = 2 * int(math.ceil(pw / 0.6 / 2 + 2)), 2 * int(math.ceil(ph / 0.6 / 2 + 2))
    g += "[%s_t]crop=%d:%d:%d:%d,%s,crop=%d:%d:%d:%d[%s_zb];" % (
        out, cw, ch, (3 * pw - cw) // 2, (3 * ph - ch) // 2, _zoom_persp("0.6+0.4*%s" % e, cw, ch), pw, ph,
        (cw - pw) // 2, (ch - ph) // 2, out)
    return g + mix_graph("%s_za" % out, "%s_zb" % out, out, d, fps, mix_weights(d, "cubic"), pw, ph)


def whip_r1_graph(a, b, out, d, fps, pw, ph, direction="left", sub=8):
    """The custom whip (R1) as [TR] whip_preview_filter: both pictures side by side, a window sliding on the same
    smoothstep as the Fusion expression, 8 sub-frame samples over a 360 degree shutter, back to the timeline rate
    (luma correlation 0.958 to 0.998 against the renders, [TR] s5)."""
    F = float(fps) * sub
    pos = "((n - %d/2 + 0.5) / %d)" % (sub, sub)
    p = "clip((%s + 0.5) / %d, 0, 1)" % (pos, d)
    s = "(%s*%s*(3-2*%s))" % (p, p, p)
    if direction in ("left", "right"):
        stack = "[%s_a8][%s_b8]hstack=inputs=2" % (out, out) if direction == "left" else \
            "[%s_b8][%s_a8]hstack=inputs=2" % (out, out)
        x = ("%d*%s" % (pw, s)) if direction == "left" else ("%d*(1-%s)" % (pw, s))
        crop = "crop=w=%d:h=%d:x='%s':y=0:exact=1" % (pw, ph, x)
    else:
        stack = "[%s_a8][%s_b8]vstack=inputs=2" % (out, out) if direction == "up" else \
            "[%s_b8][%s_a8]vstack=inputs=2" % (out, out)
        y = ("%d*%s" % (ph, s)) if direction == "up" else ("%d*(1-%s)" % (ph, s))
        crop = "crop=w=%d:h=%d:x=0:y='%s':exact=1" % (pw, ph, y)
    return ("[%s]fps=%g[%s_a8];[%s]fps=%g[%s_b8];%s,%s,tmix=frames=%d,select='not(mod(n+1,%d))',setpts=N/(%g*TB),"
            "fps=%s,format=yuv420p[%s]" % (a, F, out, b, F, out, stack, crop, sub, sub, float(fps), _rate_s(fps), out))


def zoom_r1b_graph(a, b, out, d, fps, pw, ph, peak=2.5, mix=2, point=(0.5, 0.5), motion_blur=True, sub=8):
    """The custom zoom across the overlap (R1b) as [TR] zoom_in_preview_filter (0.967 to 0.995 against the renders).
    point in frame fractions, y from the TOP (the build gives Fusion y = 1 - y)."""
    sub = sub if motion_blur else 1
    F = float(fps) * sub
    pos = "((on - %d/2 + 0.5) / %d)" % (sub, sub) if sub > 1 else "(on)"
    p = "((%s + 0.5) / %d)" % (pos, d)
    za = "1+%g*pow(clip(2*%s,0,1),2)" % (peak - 1.0, p)
    zb = "%g-%g*(1-pow(1-clip(2*%s-1,0,1),2))" % (peak, peak - 1.0, p)
    px_, py_ = float(point[0]), float(point[1])
    xy = "x='iw*%g*(1-1/zoom)':y='ih*%g*(1-1/zoom)'" % (px_, py_)

    def chain(src, z, lab):
        c = "[%s]scale=%d:%d:flags=bicubic,fps=%g,zoompan=z='%s':%s:d=1:s=%dx%d:fps=%g" % (src, 2 * pw, 2 * ph, F, z,
                                                                                         xy, pw, ph, F)
        if sub > 1:
            c += ",tmix=frames=%d,select='not(mod(n+1,%d))',setpts=N/(%g*TB),fps=%s" % (sub, sub, float(fps),
                                                                                          _rate_s(fps))
        return c + "[%s]" % lab
    w = "clip((((T*%g+0.5)/%d)-0.5)*%g+0.5,0,1)" % (float(fps), d, d / float(mix))
    return ("%s;%s;[%s_za]format=gbrp[%s_ga];[%s_zb]format=gbrp[%s_gb];[%s_ga][%s_gb]blend=all_expr='A*(1-%s)+B*%s',"
            "format=yuv420p[%s]" % (chain(a, za, "%s_za" % out), chain(b, zb, "%s_zb" % out), out, out, out, out, out,
                                     out, w, w, out))


def transition_graph(recipe, params, a, b, out, d, fps, raster):
    """The preview graph of one transition recipe from [a] and [b] (d frames each, framed to the raster) to [out]
    (yuv420p). recipe: dissolve, dip, brightness_flash, xfade:<name>, slide:<dir>, pan:<dir>, zoom_in, whip_r1,
    zoom_r1b, hold_a. None for the two the lab draws with its own blend (dissolve and dip)."""
    pw, ph = raster
    params = params or {}
    if recipe in ("dissolve", "dip", None):
        return None
    if recipe == "brightness_flash":
        return tr_brightness_flash(a, b, out, d, fps, pw, ph)
    if recipe.startswith("xfade:"):
        return xfade_graph(a, b, out, d, fps, recipe.split(":", 1)[1])
    if recipe.startswith("slide:"):
        return tr_slide(a, b, out, d, fps, pw, ph, recipe.split(":", 1)[1])
    if recipe.startswith("pan:"):
        return tr_pan(a, b, out, d, fps, pw, ph, recipe.split(":", 1)[1])
    if recipe == "zoom_in":
        return tr_zoom_in(a, b, out, d, fps, pw, ph)
    if recipe == "whip_r1":
        return whip_r1_graph(a, b, out, d, fps, pw, ph, params.get("direction", "left"))
    if recipe == "zoom_r1b":
        return zoom_r1b_graph(a, b, out, d, fps, pw, ph, float(params.get("peak", 2.5)), float(params.get("mix", 2)),
                              params.get("point") or (0.5, 0.5), params.get("motion_blur", True) is not False)
    if recipe == "hold_a":
        return "[%s]format=yuv420p[%s];[%s]nullsink" % (a, out, b)
    raise FxError("preview recipe %r is unknown" % recipe)


def audio_xfade_gains(curve, p):
    """(gain of A, gain of B) at progress p of an audio cross fade ([TR] s7: +3 dB square-root, 0 dB linear,
    -3 dB power 1.5)."""
    p = min(1.0, max(0.0, p))
    if curve == "plus3":
        return math.sqrt(1 - p), math.sqrt(p)
    if curve == "minus3":
        return (1 - p) ** 1.5, p ** 1.5
    return 1 - p, p
