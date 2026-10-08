"""Fusion recipes the resolve-editor build runs INSIDE DaVinci Resolve Studio 21.1 (clip comps, transition comps,
animated Text+). Standard library only. edit_lab.py pastes this file's text into the build, effects, graphics and
verify snippets after COMMON_BODY; the tests import it as a module against tests/fake_resolve.py.

Every number below was measured in the Resolve 21.1 sandbox by the fusion research (sources named per function as
[API] fusion_api, [TR] transitions, [TXT] animated_text, [RFX] resolve_fx, [PRV] preview_fidelity, [JDG] the resolve
master judge, [A] this build's own sandbox batches). The rules every recipe keeps:
- comp time of a clip is in SOURCE frames and starts at the first frame the item is SEEN, frames under an incoming
  transition included ([TR] R2): comp_time_fn() converts item-relative timeline frames;
- keys are set one SetInput(value, time) per key and interpolate LINEARLY between keys ([PRV] 4.2); a new spline
  carries a stray key at the comp's current time, deleted one key at a time ([API] s2);
- a point is scaled about Pivot only, Center is a translation ([API] s1, [TR] R1b);
- never SetInput inside comp.Lock() ([JDG]); never a Transform with a colour channel switched off ([API] s4);
- StyledText is read with a time only (GetInput("StyledText", 0)): a read without one drops a connected Follower's
  styling from renders ([TXT] s4 trap 1).
"""
import math
import time

FX_PREFIX = "RE_"
EDGES = {"canvas": 0, "wrap": 1, "duplicate": 2, "mirror": 3}
# ChannelBoolean menu values decoded in 21.1 ([API] s4): FG red/green/blue/alpha 0..3, BG red 4, green 6, blue 7,
# alpha 8
CB_RED_FG, CB_BLUE_FG = 0, 2
CB_RED_BG, CB_GREEN_BG, CB_BLUE_BG, CB_ALPHA_BG = 4, 6, 7, 8
# Resolve constants that read back as these numbers in 21.1 ([RFX] s6: not in stub order)
RETIME_OPTICAL_FLOW_VALUE = 3
MOTION_EST_SPEED_WARP_BETTER_VALUE = 5
KEY_TIME_TOL = 1e-3
# modifiers show in GetToolList named after their tool and input ("RE_MotionSize", [A] batch 1); not tools
MODIFIER_IDS = ("BezierSpline", "XYPath", "PolyPath", "Path", "StyledTextFollower", "StyledTextCLS", "LUTLookup")


# ------------------------------------------------------------------------------------------------ small helpers
def _fr_call(o, name, *a):
    try:
        f = getattr(o, name, None) if o is not None else None
        return f(*a) if callable(f) else None
    except Exception:
        return None


def _fr_attrs(o):
    a = _fr_call(o, "GetAttrs")
    return a if isinstance(a, dict) else {}


def tool_name(t):
    n = _fr_attrs(t).get("TOOLS_Name")
    if n:
        return n
    try:
        return t.Name
    except Exception:
        return None


def tool_regid(t):
    r = _fr_attrs(t).get("TOOLS_RegID")
    if r:
        return r
    try:
        return t.ID
    except Exception:
        return None


def tool_list(comp):
    tl = _fr_call(comp, "GetToolList", False)
    if isinstance(tl, dict):
        return [t for t in tl.values() if t is not None]
    if isinstance(tl, (list, tuple)):
        return [t for t in tl if t is not None]
    return []


def tool_names(comp):
    return sorted(n for n in (tool_name(t) for t in tool_list(comp)) if n)


def find_tool(comp, name):
    return _fr_call(comp, "FindTool", name)


def value_at(keys, f):
    """Linear between keys, constant outside the first and last key (the build's and the preview's contract, D1)."""
    ks = sorted((float(k[0]), float(k[1])) for k in (keys or []))
    if not ks:
        return None
    f = float(f)
    if f <= ks[0][0]:
        return ks[0][1]
    if f >= ks[-1][0]:
        return ks[-1][1]
    for (t0, v0), (t1, v1) in zip(ks, ks[1:]):
        if t0 <= f <= t1:
            return v0 if t1 == t0 else v0 + (v1 - v0) * (f - t0) / (t1 - t0)
    return ks[-1][1]


# ------------------------------------------------------------------------------------------------- comp time (D3)
def _item_track(item):
    tt = _fr_call(item, "GetTrackTypeAndIndex")
    if isinstance(tt, (list, tuple)) and len(tt) >= 2:
        return str(tt[0]), int(tt[1])
    return "video", 1


def track_transitions(tl, kind, idx):
    out = []
    for it in (_fr_call(tl, "GetItemListInTrack", kind, idx) or []):
        if _fr_call(it, "GetType") == "transition":
            s, e = _fr_call(it, "GetStart"), _fr_call(it, "GetEnd")
            if isinstance(s, (int, float)) and isinstance(e, (int, float)):
                out.append((int(s), int(e), it))
    return out


def vis_range(tl, item, kind=None, idx=None):
    """The record frames an item is SEEN on its track: its own range plus the frames of the transitions next to it
    ([TR] R2: a 50 fps item with a centred 8-frame transition at its head had RenderEnd 66 for 30 timeline frames).
    A transition belongs to the cut at an item boundary inside [start, end] of the transition: strictly inside for a
    centred one, at its end for a left (End on Edit) one, at its start for a right (Start on Edit) one. Returns
    {"start", "len", "head", "tail", "item_start", "item_len"} in absolute timeline frames."""
    s, e = _fr_call(item, "GetStart"), _fr_call(item, "GetEnd")
    s, e = int(s), int(e)
    if kind is None or idx is None:
        kind, idx = _item_track(item)
    items = [it for it in (_fr_call(tl, "GetItemListInTrack", kind, idx) or []) if _fr_call(it, "GetType") != "transition"]
    starts = sorted(set(int(_fr_call(it, "GetStart")) for it in items if isinstance(_fr_call(it, "GetStart"), (int, float))))
    ends = sorted(set(int(_fr_call(it, "GetEnd")) for it in items if isinstance(_fr_call(it, "GetEnd"), (int, float))))
    bounds = sorted(set(starts) & set(ends))      # cuts: one item ends where the next starts
    head = tail = 0
    for t0, t1, _tr in track_transitions(tl, kind, idx):
        inside = [c for c in bounds if t0 < c < t1]
        cut = inside[0] if inside else (t1 if t1 in bounds else (t0 if t0 in bounds else None))
        if cut is None:
            # a fade from or to nothing (an item after a gap, or the last one): it borrows the item's own handle
            if t0 < s < t1:
                head = max(head, s - t0)
            if t0 < e < t1:
                tail = max(tail, t1 - e)
            continue
        if cut == s:
            head = max(head, s - t0)
        if cut == e:
            tail = max(tail, t1 - e)
    return {"start": s - head, "len": (e - s) + head + tail, "head": head, "tail": tail, "item_start": s,
            "item_len": e - s}


def comp_span(comp):
    a = _fr_attrs(comp)
    out = {}
    for k, n in (("rs", "COMPN_RenderStart"), ("re", "COMPN_RenderEnd"), ("gs", "COMPN_GlobalStart"),
                 ("ge", "COMPN_GlobalEnd")):
        v = a.get(n)
        out[k] = float(v) if isinstance(v, (int, float)) else None
    return out


def comp_time_fn(item, comp, tl, media_fps, tl_fps, speed=1.0, vr=None, still=False):
    """(ct, info): ct(f) is the comp time of item-relative timeline frame f (0 = the item's first frame; negative
    frames run under an incoming transition). D3: comp_time = RenderStart + (f + head) * (RenderEnd - RenderStart) /
    (vis_len - 1). info["ok"] is False (fx_timebase) when RenderEnd - RenderStart differs from (vis_len - 1) * media fps
    / timeline fps * speed by more than 0.5: the comp is not timed the way the keys assume, so nothing is keyed."""
    vr = vr or vis_range(tl, item)
    sp = comp_span(comp)
    rs, re_ = sp["rs"], sp["re"]
    n = int(vr["len"])
    info = dict(vr, rs=rs, re=re_, gs=sp["gs"], ge=sp["ge"])
    if rs is None or re_ is None or n < 1:
        info.update(ok=False, why="the comp reports no render range")
        return None, info
    if still or not media_fps:
        k = 1.0                                   # titles, generators, stills: 1:1 ([API] s1, [A] batch 2)
    else:
        k = float(media_fps) / float(tl_fps) * float(speed or 1.0)
    want = (n - 1) * k
    got = re_ - rs
    # a retimed item reports the last WHOLE source frame (25 frames at 40 %: RenderEnd 9 for 9.6, [A] batch 2), so
    # the span may sit up to one frame under the exact value; the keys use the exact rate. The same holds for any
    # media rate that is not a whole multiple of the timeline's (30p on 25p, 24p on 30p, 29.97 on 30): when the exact
    # span is not a whole number of source frames, a floored RenderEnd is accepted too
    whole = abs(want - round(want)) < 1e-6
    ok = abs(got - want) <= 0.5 or ((abs(float(speed or 1.0) - 1.0) > 1e-9 or not whole)
                                    and want - 1.0 - 1e-6 <= got <= want + 0.5)
    ratio = k if ok else (got / float(n - 1) if n > 1 else 1.0)
    info.update(ratio=ratio, want_span=want, got_span=got, ok=ok)
    if not ok:
        info["why"] = "RenderEnd - RenderStart is %g, the visible range asks %g" % (got, want)
    head = int(vr["head"])

    def ct(f):
        return rs + (float(f) + head) * ratio
    return ct, info


# ------------------------------------------------------------------------------------------------- tools and keys
def chain_tail(comp):
    mo = find_tool(comp, "MediaOut1")
    try:
        return mo.Input.GetConnectedOutput()
    except Exception:
        return None


def add_tool(comp, tool_id, name=None):
    t = _fr_call(comp, "AddTool", tool_id, -32768, -32768)
    if t is None:
        return None
    if name:
        _fr_call(t, "SetAttrs", {"TOOLS_Name": name})
    return t


def add_to_chain(comp, tool_id, name, input_name="Input"):
    """AddTool, fed from the chain's tail (whatever feeds MediaOut1), made the new tail. None when AddTool refuses
    the id (an unknown tool id returns None, [API] s1)."""
    t = add_tool(comp, tool_id, name)
    if t is None:
        return None
    tail = chain_tail(comp)
    if tail is not None:
        t.ConnectInput(input_name, tail)
    find_tool(comp, "MediaOut1").ConnectInput("Input", t)
    return t


def insert_after_media_in(comp, tool_id, name):
    """Put a tool right after MediaIn1: a retime must see the source before any other effect ([API] speed_ramp)."""
    t = add_tool(comp, tool_id, name)
    if t is None:
        return None
    mine = tool_name(t)
    for other in tool_list(comp):
        if tool_name(other) == mine:
            continue
        inputs = _fr_call(other, "GetInputList") or {}
        for inp in (inputs.values() if isinstance(inputs, dict) else inputs):
            o = _fr_call(inp, "GetConnectedOutput")
            if o is not None and tool_name(_fr_call(o, "GetTool")) == "MediaIn1":
                iid = _fr_attrs(inp).get("INPS_ID")
                if iid:
                    other.ConnectInput(iid, t)
    t.ConnectInput("Input", find_tool(comp, "MediaIn1"))
    if chain_tail(comp) is None:
        find_tool(comp, "MediaOut1").ConnectInput("Input", t)
    return t


def modifier_of(tool, inp):
    """The tool driving an input (a BezierSpline, XYPath, Follower ...) or None."""
    try:
        o = getattr(tool, inp).GetConnectedOutput()
    except Exception:
        return None
    return _fr_call(o, "GetTool") if o is not None else None


def key_count(tool, inp):
    m = modifier_of(tool, inp)
    if m is None:
        return 0
    kf = _fr_call(m, "GetKeyFrames")
    return len(kf) if isinstance(kf, dict) else 0


def key_series(tool, inp, pairs, always_key=False):
    """Key a Number input: pairs [(comp_time, value)]. One SetInput(value, time) per key (linear between keys, [PRV]
    4.2), then every key at a time that is not ours (the stray key a new spline carries at the comp's current time)
    deleted one by one ([API] s2), then the key count read back. One distinct value is set plainly, without a spline,
    unless always_key: a Text+ Follower's styling renders only when KEYED ([TXT] s4 trap 2; a constant colour set
    plainly drew no accent at all in [A] batch 5). Returns {"ok", "keys", "want"} (and "why" when it failed)."""
    seen = {}
    for t, v in pairs:
        seen[round(float(t), 6)] = float(v)
    ks = sorted(seen.items())
    if not ks:
        return {"ok": True, "keys": 0, "want": 0}
    if not always_key and len(set(v for _, v in ks)) == 1 and modifier_of(tool, inp) is None:
        tool.SetInput(inp, ks[0][1])
        return {"ok": True, "keys": 0, "want": 0, "static": ks[0][1]}
    spl = modifier_of(tool, inp)
    if spl is None or tool_regid(spl) != "BezierSpline":
        if not tool.AddModifier(inp, "BezierSpline"):
            return {"ok": False, "keys": 0, "want": len(ks), "why": "AddModifier BezierSpline refused on %s" % inp}
        spl = modifier_of(tool, inp)
        if spl is None:
            return {"ok": False, "keys": 0, "want": len(ks), "why": "no spline on %s after AddModifier" % inp}
    for t, v in ks:
        tool.SetInput(inp, v, t)
    mine = [t for t, _ in ks]
    for t in list((_fr_call(spl, "GetKeyFrames") or {}).keys()):
        if min(abs(float(t) - m) for m in mine) > KEY_TIME_TOL:
            spl.DeleteKeyFrames(float(t))
    got = len(_fr_call(spl, "GetKeyFrames") or {})
    res = {"ok": got == len(ks), "keys": got, "want": len(ks)}
    if not res["ok"]:
        res["why"] = "%s: %d keys set, %d read back" % (inp, len(ks), got)
    return res


def key_point(tool, inp, xs, ys, x0=0.5, y0=0.5):
    """Key a Point input (Center) through an XYPath ([API] s2): xs and ys are [(comp_time, value)] lists, keyed
    independently (either may be empty: that axis stays at x0 or y0)."""
    xy = modifier_of(tool, inp)
    if xy is None or tool_regid(xy) != "XYPath":
        if not tool.AddModifier(inp, "XYPath"):
            return {"ok": False, "why": "AddModifier XYPath refused on %s" % inp}
        xy = modifier_of(tool, inp)
        if xy is None:
            return {"ok": False, "why": "no XYPath on %s after AddModifier" % inp}
    if xs:
        rx = key_series(xy, "X", xs)
    else:
        xy.SetInput("X", float(x0))
        rx = {"ok": True, "keys": 0}
    if ys:
        ry = key_series(xy, "Y", ys)
    else:
        xy.SetInput("Y", float(y0))
        ry = {"ok": True, "keys": 0}
    out = {"ok": bool(rx.get("ok")) and bool(ry.get("ok")), "x": rx, "y": ry}
    if not out["ok"]:
        out["why"] = rx.get("why") or ry.get("why")
    return out


# --------------------------------------------------------------------------------------------- framing (static)
def framing(src_w, src_h, tl_w, tl_h, sizing="scaleToCrop", zoom=1.0, pan_px=0.0, tilt_px=0.0):
    """The static framing T of a clip as the preview draws it: input sizing scales the source to (iw, ih), the
    Inspector zoom scales that about the frame centre, pan moves it right and tilt moves it up, in timeline pixels.
    Transform Size 1.0 in the clip comp IS this framing: the Inspector applies after the comp ([JDG] 3b, [RFX] s4)."""
    src_w, src_h, tl_w, tl_h = float(src_w), float(src_h), float(tl_w), float(tl_h)
    if sizing in ("stretch", "scaleToStretch"):
        iw, ih = tl_w, tl_h
    else:
        k = max(tl_w / src_w, tl_h / src_h) if sizing in ("scaleToCrop", "crop", "fill") else \
            min(tl_w / src_w, tl_h / src_h)
        iw, ih = src_w * k, src_h * k
    return {"iw": iw, "ih": ih, "z": float(zoom or 1.0), "pan": float(pan_px or 0.0), "tilt": float(tilt_px or 0.0),
            "W": tl_w, "H": tl_h}


def frame_to_comp(fr, qx, qy):
    """A point in FRAME fractions (x from the left, y from the TOP) to the clip comp's fractions (x, y UP): the
    inverse of the static framing. A scaling about that comp point followed by the framing equals the framing
    followed by a scaling about the frame point."""
    W, H, iw, ih, z = fr["W"], fr["H"], fr["iw"], fr["ih"], fr["z"]
    u = 0.5 + (float(qx) * W - W / 2.0 - fr["pan"]) / (iw * z)
    v = 0.5 + (float(qy) * H - H / 2.0 + fr["tilt"]) / (ih * z)
    return u, 1.0 - v


def comp_to_frame(fr, u, v_up):
    W, H, iw, ih, z = fr["W"], fr["H"], fr["iw"], fr["ih"], fr["z"]
    x = W / 2.0 + (float(u) - 0.5) * iw * z + fr["pan"]
    y = H / 2.0 + ((1.0 - float(v_up)) - 0.5) * ih * z - fr["tilt"]
    return x / W, y / H


def px_to_center(fr, x_px, y_px):
    """Timeline pixels (x right, y DOWN) to a Transform Center in comp fractions (y up)."""
    return 0.5 + float(x_px) / (fr["iw"] * fr["z"]), 0.5 - float(y_px) / (fr["ih"] * fr["z"])


def center_to_px(fr, cx, cy):
    return (float(cx) - 0.5) * fr["iw"] * fr["z"], -(float(cy) - 0.5) * fr["ih"] * fr["z"]


# --------------------------------------------------------------------------------------------------- clip comps
def clip_comp(item, create=True):
    n = _fr_call(item, "GetFusionCompCount") or 0
    if n >= 1:
        return item.GetFusionCompByIndex(1)
    return _fr_call(item, "AddFusionComp") if create else None


def apply_retime(comp, ct, retime, m0, rs):
    """TimeStretcher RE_Retime right after MediaIn1, SourceTime keyed ([JDG], [API] speed_ramp, [PRV] s5).
    retime["keys"] = [[item frame, media frame], ...] (whole media frames, linear between keys). SourceTime is comp
    time: RenderStart + (media frame - m0), m0 the media frame the comp shows at RenderStart. Frame blending is
    switched off so a fractional source time shows one frame, as the preview does."""
    if find_tool(comp, FX_PREFIX + "Retime") is not None:
        return {"ok": True, "skipped": "exists"}
    ts = insert_after_media_in(comp, "TimeStretcher", FX_PREFIX + "Retime")
    if ts is None:
        return {"ok": False, "why": "addtool_failed", "tool": "TimeStretcher"}
    ts.SetInput("InterpolateBetweenFrames", 0)
    pairs = [(ct(f), float(rs) + (float(m) - float(m0))) for f, m in retime.get("keys") or []]
    r = key_series(ts, "SourceTime", pairs)
    r["tool"] = tool_name(ts)
    return r


def apply_motion(comp, ct, motion, fr):
    """Transform RE_Motion ([API] punch, [TR] R1b): Size keyed (zoom about `point`, Pivot only), Center through an
    XYPath (x_px, y_px in timeline pixels, y down), Angle keyed (degrees, counter-clockwise as Fusion), Edges (mirror by
    default), optional motion blur."""
    if find_tool(comp, FX_PREFIX + "Motion") is not None:
        return {"ok": True, "skipped": "exists"}
    keys = motion.get("keys") or {}
    tr = add_to_chain(comp, "Transform", FX_PREFIX + "Motion")
    if tr is None:
        return {"ok": False, "why": "addtool_failed", "tool": "Transform"}
    pt = motion.get("point") or [0.5, 0.5]
    pu, pv = frame_to_comp(fr, pt[0], pt[1])
    tr.SetInput("Pivot", {1: pu, 2: pv, 3: 0.0})
    tr.SetInput("Edges", int(EDGES.get(str(motion.get("edges") or "mirror"), 3)))
    mb = motion.get("motion_blur")
    if mb:
        mb = mb if isinstance(mb, dict) else {}
        tr.SetInput("MotionBlur", 1)
        tr.SetInput("Quality", int(mb.get("quality", 8)))
        tr.SetInput("ShutterAngle", float(mb.get("shutter", 360.0)))
    out = {"ok": True, "tool": tool_name(tr), "pivot": [round(pu, 6), round(pv, 6)]}
    if keys.get("zoom"):
        out["zoom"] = key_series(tr, "Size", [(ct(f), v) for f, v in keys["zoom"]])
    xs = [(ct(f), px_to_center(fr, v, 0.0)[0]) for f, v in keys.get("x_px") or []]
    ys = [(ct(f), px_to_center(fr, 0.0, v)[1]) for f, v in keys.get("y_px") or []]
    if xs or ys:
        out["center"] = key_point(tr, "Center", xs, ys)
    if keys.get("angle"):
        out["angle"] = key_series(tr, "Angle", [(ct(f), v) for f, v in keys["angle"]])
    out["ok"] = all(v.get("ok", True) for v in out.values() if isinstance(v, dict))
    return out


def apply_accents(comp, ct, accents, fr):
    """Flash (BrightnessContrast RE_Flash: Gain = 1 - v, Brightness = v, a lerp to white; alpha untouched), RGB split
    (two shifted Transforms and two ChannelBooleans, [API] rgb_split: never a Transform with a channel switched off)
    and an in-clip light leak (FastNoise screened through a Merge RE_Leak, Blend keyed, [API] light_leak)."""
    out = {"ok": True}
    fl = accents.get("flash") or []
    if fl and find_tool(comp, FX_PREFIX + "Flash") is None:
        bc = add_to_chain(comp, "BrightnessContrast", FX_PREFIX + "Flash")
        if bc is None:
            return {"ok": False, "why": "addtool_failed", "tool": "BrightnessContrast"}
        bc.SetInput("Alpha", 0)
        out["flash_gain"] = key_series(bc, "Gain", [(ct(f), 1.0 - float(v)) for f, v in fl])
        out["flash_brightness"] = key_series(bc, "Brightness", [(ct(f), float(v)) for f, v in fl])
    rp = accents.get("rgb_px") or []
    if rp and find_tool(comp, FX_PREFIX + "RGB_MixB") is None:
        tail = chain_tail(comp)
        shifted = {}
        for ch, sgn in (("R", 1.0), ("B", -1.0)):
            t = add_tool(comp, "Transform", FX_PREFIX + "RGB_" + ch)
            if t is None:
                return {"ok": False, "why": "addtool_failed", "tool": "Transform"}
            if tail is not None:
                t.ConnectInput("Input", tail)
            t.SetInput("Edges", 3)
            out["rgb_" + ch] = key_point(t, "Center", [(ct(f), px_to_center(fr, sgn * float(v), 0.0)[0])
                                                       for f, v in rp], [], 0.5, 0.5)
            shifted[ch] = t
        m1 = add_tool(comp, "ChannelBoolean", FX_PREFIX + "RGB_MixR")
        m2 = add_tool(comp, "ChannelBoolean", FX_PREFIX + "RGB_MixB")
        if m1 is None or m2 is None:
            return {"ok": False, "why": "addtool_failed", "tool": "ChannelBoolean"}
        if tail is not None:
            m1.ConnectInput("Background", tail)
        m1.ConnectInput("Foreground", shifted["R"])
        for k, v in (("Operation", 0), ("ToRed", CB_RED_FG), ("ToGreen", CB_GREEN_BG), ("ToBlue", CB_BLUE_BG),
                     ("ToAlpha", CB_ALPHA_BG)):
            m1.SetInput(k, v)
        m2.ConnectInput("Background", m1)
        m2.ConnectInput("Foreground", shifted["B"])
        for k, v in (("Operation", 0), ("ToRed", CB_RED_BG), ("ToGreen", CB_GREEN_BG), ("ToBlue", CB_BLUE_FG),
                     ("ToAlpha", CB_ALPHA_BG)):
            m2.SetInput(k, v)
        find_tool(comp, "MediaOut1").ConnectInput("Input", m2)
    lk = accents.get("leak")
    if isinstance(lk, dict) and (lk.get("keys") or lk.get("f")) and find_tool(comp, FX_PREFIX + "Leak") is None:
        col = _rgb01(lk.get("color") or "#FF8A3D")
        if not lk.get("keys"):
            # the envelope the preview draws: peak x sin(pi u) over the leak's frames, one key per frame (D1)
            a, b = int(lk["f"][0]), int(lk["f"][1])
            span = max(1, b - a)
            lk = dict(lk, keys=[[f, round(float(lk.get("peak", 0.55)) * math.sin(math.pi * (f - a) / float(span)), 5)]
                                for f in range(a, b + 1)])
        nz = add_tool(comp, "FastNoise", FX_PREFIX + "LeakNoise")
        tail = chain_tail(comp)
        mg = add_tool(comp, "Merge", FX_PREFIX + "Leak")
        if nz is None or mg is None:
            return {"ok": False, "why": "addtool_failed", "tool": "FastNoise/Merge"}
        for k, v in (("Detail", 1.0), ("Contrast", 1.6), ("Brightness", -0.1), ("XScale", float(lk.get("scale", 0.6))),
                     ("SeetheRate", 0.03), ("Color1Red", 0.0), ("Color1Green", 0.0), ("Color1Blue", 0.0),
                     ("Color1Alpha", 0.0), ("Color2Red", col[0]), ("Color2Green", col[1]), ("Color2Blue", col[2]),
                     ("Color2Alpha", 1.0)):
            nz.SetInput(k, v)
        if tail is not None:
            mg.ConnectInput("Background", tail)
        mg.ConnectInput("Foreground", nz)
        mg.SetInput("ApplyMode", "Screen")
        find_tool(comp, "MediaOut1").ConnectInput("Input", mg)
        out["leak"] = key_series(mg, "Blend", [(ct(f), float(v)) for f, v in lk["keys"]])
        fs = [float(k[0]) for k in lk["keys"]]
        drift = float(lk.get("drift", 0.25))
        out["leak_drift"] = key_point(nz, "Center", [(ct(min(fs)), 0.5 - drift), (ct(max(fs)), 0.5 + drift)],
                                      [(ct(min(fs)), 0.5), (ct(max(fs)), 0.55)])
    for g in accents.get("glitch") or []:
        out["glitch_" + str(g.get("id"))] = _glitch_bands(comp, ct, g)
    out["ok"] = all(v.get("ok", True) for v in out.values() if isinstance(v, dict))
    return out


def _rgb01(c, default=(1.0, 0.54, 0.24)):
    if isinstance(c, (list, tuple)) and len(c) >= 3:
        v = [float(x) for x in c[:3]]
        return [x / 255.0 for x in v] if max(v) > 1.0 else v
    s = str(c or "").strip().lstrip("#")
    if len(s) == 6:
        try:
            return [int(s[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]
        except ValueError:
            pass
    return list(default)


class _Seeded(object):
    """A tiny deterministic generator (snippets import only json, os, time and math): a 32-bit xorshift."""
    def __init__(self, seed):
        self.s = (int(seed) * 2654435761 + 1) & 0xFFFFFFFF or 1

    def uniform(self, a, b):
        x = self.s
        x ^= (x << 13) & 0xFFFFFFFF
        x ^= x >> 17
        x ^= (x << 5) & 0xFFFFFFFF
        self.s = x & 0xFFFFFFFF
        return a + (b - a) * (self.s / 4294967296.0)


def _glitch_bands(comp, ct, g):
    """Horizontal bands of a glitch burst ([API] glitch): per slice a RectangleMask on a Transform (FlattenTransform 1:
    two masked Transforms in a row without it made the frame unrenderable, [API] s4) whose Center jumps sideways every
    `hold` frames, the mask's height and place seeded. Deterministic for a seed; the preview marks glitch frames
    approximate and skips them."""
    rnd = _Seeded(int(g.get("seed", 3)))
    a, b = int(g["f"][0]), int(g["f"][1])
    hold = max(1, int(g.get("hold", 2)))
    out = {"ok": True, "bands": []}
    for k in range(int(g.get("slices", 2))):
        name = "%sSlice%s_%d" % (FX_PREFIX, str(g.get("id", "g")).replace(".", "_"), k)
        if find_tool(comp, name) is not None:
            continue
        m = add_tool(comp, "RectangleMask", name + "Mask")
        tr = add_to_chain(comp, "Transform", name)
        if m is None or tr is None:
            return {"ok": False, "why": "addtool_failed"}
        m.SetInput("Width", 1.2)
        m.SetInput("Height", rnd.uniform(0.03, 0.12))
        m.SetInput("SoftEdge", 0.0)
        tr.SetInput("Edges", 1)
        tr.SetInput("FlattenTransform", 1)
        tr.ConnectInput("EffectMask", m)
        xs, ys = [(ct(a - 1), 0.5)], [(ct(a - 1), 0.5)]
        for f0 in range(a, b + 1, hold):
            x, y = 0.5 + rnd.uniform(-0.08, 0.08), rnd.uniform(0.15, 0.85)
            for f in range(f0, min(b + 1, f0 + hold)):
                xs.append((ct(f), x))
                ys.append((ct(f), y))
        xs.append((ct(b + 1), 0.5))
        r1 = key_point(tr, "Center", xs, [], 0.5, 0.5)
        r2 = key_point(m, "Center", [], ys, 0.5, 0.5)
        out["bands"].append(name)
        if not (r1.get("ok") and r2.get("ok")):
            return {"ok": False, "why": r1.get("why") or r2.get("why")}
    return out


def _const(resolve, name, default):
    v = getattr(resolve, name, None) if resolve is not None else None
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else default


def apply_speed_warp(resolve, item):
    """Speed Warp retiming on an item placed with SetSpeed ([RFX] s6): RetimeProcess optical flow (reads back 3) and
    MotionEstimation Speed Warp Better (reads back 5). Item properties, set outside any comp."""
    rp = _const(resolve, "RETIME_OPTICAL_FLOW", RETIME_OPTICAL_FLOW_VALUE)
    me = _const(resolve, "MOTION_EST_SPEED_WARP_BETTER", MOTION_EST_SPEED_WARP_BETTER_VALUE)
    a = _fr_call(item, "SetProperty", "RetimeProcess", rp)
    b = _fr_call(item, "SetProperty", "MotionEstimation", me)
    got = [_fr_call(item, "GetProperty", "RetimeProcess"), _fr_call(item, "GetProperty", "MotionEstimation")]
    ok = all(isinstance(g, (int, float)) for g in got) and int(got[0]) == int(rp) and int(got[1]) == int(me)
    return {"ok": bool(ok), "set": [bool(a), bool(b)], "retime_process": got[0], "motion_estimation": got[1]}


# -------------------------------------------------------------------------------------------------- transitions
def set_macro_input(tr, display_name, value):
    """A published input of a built-in Fusion transition's macro ([TR] s6): match the GroupOperator input's INPS_Name
    ignoring case and spaces ("Motion Blur" or "MotionBlur"), SetInput its INPS_ID, return the value read back (None
    when the macro publishes no such input)."""
    want = str(display_name).replace(" ", "").lower()
    comp = _fr_call(tr, "GetFusionCompByIndex", 1)
    grp = [t for t in tool_list(comp) if tool_regid(t) == "GroupOperator"]
    if not grp:
        return None
    g = grp[0]
    inputs = _fr_call(g, "GetInputList") or {}
    for inp in (inputs.values() if isinstance(inputs, dict) else inputs):
        a = _fr_attrs(inp)
        if str(a.get("INPS_Name", "")).replace(" ", "").lower() == want or \
                str(a.get("INPS_ID", "")).replace(" ", "").lower() == want:
            g.SetInput(a["INPS_ID"], value)
            return g.GetInput(a["INPS_ID"], 0)
    return None


def get_macro_input(tr, display_name):
    """Read a published macro input of a placed Fusion transition (verify; None when there is no such input)."""
    want = str(display_name).replace(" ", "").lower()
    comp = _fr_call(tr, "GetFusionCompByIndex", 1)
    for g in [t for t in tool_list(comp) if tool_regid(t) == "GroupOperator"][:1]:
        inputs = _fr_call(g, "GetInputList") or {}
        for inp in (inputs.values() if isinstance(inputs, dict) else inputs):
            a = _fr_attrs(inp)
            if str(a.get("INPS_Name", "")).replace(" ", "").lower() == want or \
                    str(a.get("INPS_ID", "")).replace(" ", "").lower() == want:
                return g.GetInput(a["INPS_ID"], 0)
    return None


def _rebuild_base(tr):
    """Delete the Fusion Cross Dissolve's macro and add two Transforms and a Merge between MediaIn1 (OUTGOING),
    MediaIn2 (INCOMING) and MediaOut1 ([TR] R1). Returns (comp, ta, tb, mg) or raises."""
    comp = tr.GetFusionCompByIndex(1)
    if comp is None:
        raise RuntimeError("the transition has no Fusion comp (category must be fusion)")
    for g in [t for t in tool_list(comp) if tool_regid(t) == "GroupOperator"]:
        g.Delete()
    mi_out, mi_in, mo = find_tool(comp, "MediaIn1"), find_tool(comp, "MediaIn2"), find_tool(comp, "MediaOut1")
    if mi_out is None or mi_in is None or mo is None:
        raise RuntimeError("the transition comp lacks MediaIn1, MediaIn2 or MediaOut1")
    ta = add_tool(comp, "Transform", FX_PREFIX + "XA")
    tb = add_tool(comp, "Transform", FX_PREFIX + "XB")
    mg = add_tool(comp, "Merge", FX_PREFIX + "XMix")
    if ta is None or tb is None or mg is None:
        raise RuntimeError("AddTool refused Transform or Merge in the transition comp")
    ta.ConnectInput("Input", mi_out)
    tb.ConnectInput("Input", mi_in)
    mg.ConnectInput("Background", ta)
    mg.ConnectInput("Foreground", tb)
    mo.ConnectInput("Input", mg)
    return comp, ta, tb, mg


def rebuild_whip(tr, frames, direction="left", quality=8, shutter=360.0, a_edges=3):
    """R1 custom whip ([TR] s6): both pictures ride one smoothstep offset s = p*p*(3-2p), p = (time + 0.5)/d (frame k
    shows (k + 0.5)/d like every transition); the incoming picture abuts the outgoing one; motion blur Quality 8,
    Shutter 360; the OUTGOING Transform's Edges Mirror fills under the incoming edge (no dark seam). direction = the
    way the pictures travel (Fusion y runs up)."""
    comp, ta, tb, mg = _rebuild_base(tr)
    d = int(frames)
    p = "((time + 0.5) / %d)" % d
    s = "(%s * %s * (3 - 2 * %s))" % (p, p, p)
    dx, dy = {"left": (-1, 0), "right": (1, 0), "up": (0, 1), "down": (0, -1)}[str(direction)]
    ta.Center.SetExpression("Point(0.5 + %d * %s, 0.5 + %d * %s)" % (dx, s, dy, s))
    tb.Center.SetExpression("Point(0.5 + %d * (%s - 1), 0.5 + %d * (%s - 1))" % (dx, s, dy, s))
    for t in (ta, tb):
        t.SetInput("MotionBlur", 1)
        t.SetInput("Quality", int(quality))
        t.SetInput("ShutterAngle", float(shutter))
    if a_edges is not None:
        ta.SetInput("Edges", int(a_edges))
    return {"tools": tool_names(comp), "center_a": [_point(ta.GetInput("Center", 0)), _point(ta.GetInput("Center", d - 1))]}


def rebuild_zoom(tr, frames, peak=2.5, mix=2, point=(0.5, 0.5), motion_blur=True, quality=8, shutter=360.0):
    """R1b zoom across the overlap ([TR] s6): outgoing Size 1 + (peak-1) qa^2 (ease in), incoming peak - (peak-1)
    (1 - (1-qb)^2) (ease out), Merge Blend switches over `mix` frames around the middle; point in FRAME fractions (y
    from the top) set as Pivot only (Center moved the pictures and showed black, [TR] E8)."""
    comp, ta, tb, mg = _rebuild_base(tr)
    d = int(frames)
    p = "((time + 0.5) / %d)" % d
    qa = "min(1, max(0, 2 * %s))" % p
    qb = "min(1, max(0, 2 * %s - 1))" % p
    ta.Size.SetExpression("1 + %g * %s * %s" % (float(peak) - 1.0, qa, qa))
    tb.Size.SetExpression("%g - %g * (1 - (1 - %s) * (1 - %s))" % (float(peak), float(peak) - 1.0, qb, qb))
    mg.Blend.SetExpression("min(1, max(0, (%s - 0.5) * %g + 0.5))" % (p, d / float(mix or 1)))
    pv = {1: float(point[0]), 2: 1.0 - float(point[1]), 3: 0.0}
    for t in (ta, tb):
        t.SetInput("Pivot", pv)
        t.SetInput("MotionBlur", 1 if motion_blur else 0)
        t.SetInput("Quality", int(quality))
        t.SetInput("ShutterAngle", float(shutter))
    return {"tools": tool_names(comp), "size_a": [ta.GetInput("Size", 0), ta.GetInput("Size", d - 1)],
            "blend": [mg.GetInput("Blend", 0), mg.GetInput("Blend", d - 1)]}


def _point(v):
    if isinstance(v, dict):
        return [v.get(1.0, v.get(1)), v.get(2.0, v.get(2))]
    return v


def place_transition(out_item, in_item, x):
    """Add one catalogue transition at the cut between out_item and in_item and build its route ([TR] s2, s6).
    x: {"name", "category", "frames", "alignment", "side" ("end" default or "start"), "build" ("plain", "macro",
    "rebuild_whip", "rebuild_zoom"), "macro_inputs" {display name: value}, "params" {...}}.
    Returns (transition item or None, info). The placed length is read back (GetDuration) and reported: Resolve
    shortens a transition silently when the handles are short ([TR] s3)."""
    n = int(x["frames"])
    al = x.get("alignment", "center")
    sides = [(in_item, "start"), (out_item, "end")] if x.get("side") == "start" else \
        [(out_item, "end"), (in_item, "start")]
    tr = None
    for obj, pos in sides:
        if obj is None:
            continue
        tr = _fr_call(obj, "AddTransition", {"type": x["name"], "category": x["category"], "position": pos,
                                             "alignment": al, "duration": n})
        if tr is not None:
            break
    if tr is None:
        return None, {"refused": True}
    info = {"refused": False, "start": _fr_call(tr, "GetStart"), "end": _fr_call(tr, "GetEnd"),
            "dur": _fr_call(tr, "GetDuration"), "name": _fr_call(tr, "GetName"), "build": x.get("build") or "plain"}
    prm = x.get("params") or {}
    d = int(info["dur"]) if isinstance(info["dur"], (int, float)) and info["dur"] else n
    try:
        if info["build"] == "rebuild_whip":
            info["rebuilt"] = rebuild_whip(tr, d, prm.get("direction", "left"), int(prm.get("quality", 8)),
                                           float(prm.get("shutter", 360.0)))
        elif info["build"] == "rebuild_zoom":
            pt = prm.get("point") or [0.5, 0.5]
            info["rebuilt"] = rebuild_zoom(tr, d, float(prm.get("peak", 2.5)), float(prm.get("mix", 2)), pt,
                                           bool(prm.get("motion_blur", True)), int(prm.get("quality", 8)),
                                           float(prm.get("shutter", 360.0)))
    except Exception as ex:          # a rebuild that fails leaves a plain Fusion Cross Dissolve: reported
        info["rebuild_error"] = str(ex)[:200]
    mi = x.get("macro_inputs") or {}
    if mi:
        info["macro"] = {}
        for k in sorted(mi):
            info["macro"][k] = set_macro_input(tr, k, mi[k])
    return tr, info


# ------------------------------------------------------------------------------------------- animated Text+ (D4)
def sparse_keys(pairs, eps=1e-7):
    """Drop every key that lies on the straight line between its kept neighbours. Keys interpolate linearly ([PRV]
    4.2), so the value at every frame stays exactly the same; a hold or a linear stretch of a per-frame plan costs
    two SetInput calls instead of one per frame (a 200-frame cue took 4 s a Text+ keyed on every frame, [A] batch 6)."""
    pts = sorted((float(t), float(v)) for t, v in pairs)
    if len(pts) <= 2:
        return pts
    out = [pts[0]]
    for i in range(1, len(pts) - 1):
        (t0, v0), (t1, v1), (t2, v2) = out[-1], pts[i], pts[i + 1]
        if t2 == t0 or abs(v0 + (v2 - v0) * (t1 - t0) / (t2 - t0) - v1) > eps:
            out.append(pts[i])
    out.append(pts[-1])
    return out


def apply_text_plan(comp, tool, plan, shift=0):
    """Write one cue's plan on its Text+ ([TXT] apply_plan): the Follower first (Range, Delay, characters, styling
    KEYED: a plain SetInput of a Follower colour renders nothing, [TXT] trap 2), the static inputs (an element's
    Enabled before its other inputs), the Template keys (Size, End, ...) on every frame, Center through an XYPath, and
    a group fade as a transparent Background plus a Merge with Blend keyed (never the element opacities). `shift`
    moves every key: a cue longer than one Text+ slot continues on the next Text+, whose comp starts at 0 again.
    Never reads StyledText without a time."""
    sh = float(shift or 0)
    out = {"ok": True, "keys": {}, "follower": False, "merge": False}
    fo = None
    if plan.get("follower"):
        if not tool.AddModifier("StyledText", "StyledTextFollower"):
            return {"ok": False, "why": "AddModifier StyledTextFollower refused"}
        fo = modifier_of(tool, "StyledText")
        if fo is None:
            return {"ok": False, "why": "no Follower on StyledText after AddModifier"}
        out["follower"] = True
    for k, v in plan.get("static") or []:
        if k == "Center":
            tool.SetInput("Center", {1: float(v[0]), 2: float(v[1]), 3: 0.0})
        else:
            tool.SetInput(k, v)
    for k, kv in sorted((plan.get("keys") or {}).items()):
        out["keys"][k] = key_series(tool, k, sparse_keys([(float(f) - sh, v) for f, v in kv]), always_key=True)
    ck = plan.get("center_keys")
    if ck:
        xy = modifier_of(tool, "Center")
        if xy is None or tool_regid(xy) != "XYPath":
            tool.AddModifier("Center", "XYPath")
            xy = modifier_of(tool, "Center")
        if xy is None:
            return dict(out, ok=False, why="no XYPath on Center after AddModifier")
        xy.SetInput("X", float(ck[0][1]))
        out["keys"]["Center"] = key_series(xy, "Y", sparse_keys([(float(f) - sh, y) for f, x, y in ck]), always_key=True)
    if fo is not None:
        for k, v in plan["follower"].get("static") or []:
            fo.SetInput(k, v)
        for k, kv in sorted((plan["follower"].get("keys") or {}).items()):
            out["keys"]["Follower." + k] = key_series(fo, k, sparse_keys([(float(f) - sh, v) for f, v in kv]),
                                                      always_key=True)
    bk = plan.get("blend_keys")
    if bk:
        bg = add_tool(comp, "Background", FX_PREFIX + "TextBg")
        mg = add_tool(comp, "Merge", FX_PREFIX + "TextFade")
        if bg is None or mg is None:
            return dict(out, ok=False, why="AddTool refused Background or Merge")
        for k, v in (("UseFrameFormatSettings", 1), ("TopLeftRed", 0.0), ("TopLeftGreen", 0.0), ("TopLeftBlue", 0.0),
                     ("TopLeftAlpha", 0.0)):
            bg.SetInput(k, v)
        mg.ConnectInput("Background", bg)
        mg.ConnectInput("Foreground", tool)
        find_tool(comp, "MediaOut1").ConnectInput("Input", mg)
        out["keys"]["Blend"] = key_series(mg, "Blend", sparse_keys([(float(f) - sh, v) for f, v in bk]), always_key=True)
        out["merge"] = True
    bad = [k for k, v in out["keys"].items() if not v.get("ok", True)]
    if bad:
        out.update(ok=False, why="keys not set on " + ", ".join(bad))
    return out


def text_anim_readback(comp, tool):
    """What an animated Text+ carries, read with times only: the Follower (by its registry id), key counts of the
    Template inputs and of the Follower, and the fade Merge. Never GetInput("StyledText") without a time."""
    fo = modifier_of(tool, "StyledText")
    out = {"follower": tool_regid(fo) == "StyledTextFollower" if fo is not None else False, "keys": {},
           "merge": find_tool(comp, FX_PREFIX + "TextFade") is not None}
    for k in ("Size", "End"):
        c = key_count(tool, k)
        if c:
            out["keys"][k] = c
    xy = modifier_of(tool, "Center")
    if xy is not None:
        out["keys"]["Center.Y"] = key_count(xy, "Y")
    if fo is not None and out["follower"]:
        for k in ("FirstCharacter", "LastCharacter", "Red1", "Green1", "Blue1", "Opacity4", "WordSizeX", "Size"):
            c = key_count(fo, k)
            if c:
                out["keys"]["Follower." + k] = c
    mg = find_tool(comp, FX_PREFIX + "TextFade")
    if mg is not None:
        out["keys"]["Blend"] = key_count(mg, "Blend")
    return out


# -------------------------------------------------------------------------------------------------- read back
def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def readback_fx(comp, ct, fr, samples, m0=None, rs=None):
    """The fx tools of a clip comp and their values at item-relative timeline frames `samples`, in EDL units: zoom,
    x_px and y_px (timeline pixels, y down), angle, src (media frame shown), flash (0..1), rgb_px (red shift). Key
    counts per keyed input. Read with GetInput(name, comp_time) only (it predicts the render, [API] s0)."""
    out = {"tools": sorted(n for n in (tool_name(t) for t in tool_list(comp)
                                       if tool_regid(t) not in MODIFIER_IDS) if n and n.startswith(FX_PREFIX)),
           "keys": {}, "samples": {}}
    mo = find_tool(comp, FX_PREFIX + "Motion")
    rt = find_tool(comp, FX_PREFIX + "Retime")
    fl = find_tool(comp, FX_PREFIX + "Flash")
    rr = find_tool(comp, FX_PREFIX + "RGB_R")
    lk = find_tool(comp, FX_PREFIX + "Leak")
    if mo is not None:
        out["keys"]["Size"] = key_count(mo, "Size")
        out["keys"]["Angle"] = key_count(mo, "Angle")
        xy = modifier_of(mo, "Center")
        if xy is not None:
            out["keys"]["Center.X"] = key_count(xy, "X")
            out["keys"]["Center.Y"] = key_count(xy, "Y")
        pv = _point(mo.GetInput("Pivot", ct(0)))
        if isinstance(pv, list) and None not in pv:
            out["point"] = [round(v, 5) for v in comp_to_frame(fr, pv[0], pv[1])]
    if rt is not None:
        out["keys"]["SourceTime"] = key_count(rt, "SourceTime")
    if fl is not None:
        out["keys"]["Gain"] = key_count(fl, "Gain")
    if lk is not None:
        out["keys"]["Blend"] = key_count(lk, "Blend")
    for f in samples:
        t = ct(f)
        row = {"t": round(t, 4)}
        if mo is not None:
            row["zoom"] = _num(mo.GetInput("Size", t))
            c = _point(mo.GetInput("Center", t))
            if isinstance(c, list) and None not in c:
                x, y = center_to_px(fr, c[0], c[1])
                row["x_px"], row["y_px"] = round(x, 3), round(y, 3)
            row["angle"] = _num(mo.GetInput("Angle", t))
        if rt is not None and m0 is not None and rs is not None:
            v = _num(rt.GetInput("SourceTime", t))
            row["src"] = None if v is None else round(v - float(rs) + float(m0), 4)
        if fl is not None:
            v = _num(fl.GetInput("Brightness", t))
            row["flash"] = None if v is None else round(v, 4)
        if rr is not None:
            c = _point(rr.GetInput("Center", t))
            if isinstance(c, list) and c[0] is not None:
                row["rgb_px"] = round(center_to_px(fr, c[0], 0.5)[0], 3)
        if lk is not None:
            row["leak"] = _num(lk.GetInput("Blend", t))
        out["samples"][str(int(f))] = row
    return out


def media_frame_at_start(comp, item, media_fps):
    """m0: the media frame a clip comp shows at RenderStart. From the comp's GlobalStart (the media's first frame at
    comp time GlobalStart, [API] s1: in point 100 gave GlobalStart -100), else from the item's source start time
    (which includes a transition's borrowed head, as the comp does)."""
    sp = comp_span(comp)
    if sp["gs"] is not None and sp["rs"] is not None:
        return sp["rs"] - sp["gs"], "global_start"
    return None, None


def stopwatch():
    t0 = time.time()
    return lambda: time.time() - t0
