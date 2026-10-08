#!/usr/bin/env python3
"""Social delivery QC: preview what Instagram Reels, TikTok and YouTube Shorts re-encoding does to a graded master.

  social_qc.py MASTER [--out DIR] [--profiles default|all|ig|yt|tiktok|NAME,NAME] [--start SEC] [--seconds SEC]
               [--samples N] [--keep]

1. Delivery checks on MASTER (ffprobe): resolution and aspect, codec and bit depth, data range, color tags,
   bit rate, frame rate, timecode track, HDR transfer.
2. Platform-like re-encodes with ffmpeg (x264, x265, libvpx-vp9, SVT-AV1) at the bit rates the platforms serve
   for their top 1080x1920 rendition (see PROFILES). These are stress tests that approximate the platforms'
   encoders; they are not exact copies.
3. Measures every re-encode against the master (both at 1080x1920):
     vmaf            Netflix VMAF 0..100 (overall fidelity; >90 transparent-ish, <80 visibly soft/blocky)
     cambi           Netflix CAMBI banding index of the re-encode (0 none, ~5 starts to annoy, 24 max)
     cambi_master    CAMBI of the master itself (banding already baked in before upload)
     blockiness      edge jump at the 8 px block grid / elsewhere, in flat areas (1.0 = none)
     shadow_detail   texture left in the shadows vs the master (1.0 = all, <0.5 = smeared to plastic)
     drift_*         mean luma change in shadows / mids / highlights, in 8-bit code values (should be ~0)
     chroma          saturation kept in colored areas (1.0 = all)
   plus master levels: black point, APL, highlights, clipped / crushed area.
4. Writes DIR/qc.json, DIR/qc.txt and DIR/qc_sheet.jpg (worst frame: master vs each re-encode, a zoom on the
   worst flat area (auto-levelled from the master so dark areas are visible) and a high-pass "contour view" that
   makes banding and blocking obvious).

Needs ffmpeg with the libvmaf filter and the encoders libx264, libx265, libvpx-vp9 and libsvtav1 (the Homebrew
build on macOS and the full builds for Windows and Linux have them), numpy and pillow. A profile whose encoder is
missing is skipped with a notice. RC_FFMPEG / RC_FFPROBE point at other ffmpeg / ffprobe programs.
"""
import argparse, json, os, shutil, subprocess, sys, tempfile, time
import numpy as np

# ------------------------------------------------------------------ platform profiles
# kbps = top 1080x1920 rendition at <=30 fps. Measured with yt-dlp format listings (metadata only) in Sept 2026:
#   YouTube Shorts 1080x1920 @30: AV1 1.0-2.0, VP9 1.7-2.6 (m3u8 2.8-3.1), H.264 3.2-4.8 Mbps  (6 Shorts)
#   Instagram Reels web 1080x1920: VP9 1.6-2.4 Mbps (2 reels); the iOS/Android apps mostly get AV1 (Meta says
#   70% of Reels watch time on iOS was AV1 in 2023) at an unpublished rate, estimated here at ~0.7x VP9.
#   TikTok could not be measured from the test network; third-party reports put 1080p at ~0.5-2.5 Mbps H.265/H.264.
# Above 30 fps the rate is multiplied by 1.5 (the ratio YouTube uses in its own upload guidance, 12 vs 8 Mbps).
PROFILES = {
    "yt_av1":      ("av1",  1600, "YouTube Shorts 1080p AV1 (measured 1.0-2.0 Mbps)"),
    "yt_vp9":      ("vp9",  2500, "YouTube Shorts 1080p VP9 (measured 1.7-2.6 Mbps)"),
    "yt_h264":     ("h264", 4000, "YouTube Shorts 1080p H.264 (measured 3.2-4.8 Mbps)"),
    "ig_vp9":      ("vp9",  2000, "Instagram Reels 1080p VP9, web (measured 1.6-2.4 Mbps)"),
    "ig_av1":      ("av1",  1400, "Instagram Reels 1080p AV1, app (estimated)"),
    "tt_h265":     ("h265", 1500, "TikTok 1080p H.265 (estimated, not measured)"),
    "tt_h264":     ("h264", 2000, "TikTok 1080p H.264 (estimated, not measured)"),
    "stress_h264": ("h264", 1000, "worst case: weak network or data saver"),
}
GROUPS = {
    "default": ["ig_av1", "ig_vp9", "yt_av1", "tt_h265", "stress_h264"],
    "all": list(PROFILES),
    "ig": ["ig_av1", "ig_vp9"], "yt": ["yt_av1", "yt_vp9", "yt_h264"], "tiktok": ["tt_h265", "tt_h264"],
}
W, H = 1080, 1920
FFMPEG = os.environ.get("RC_FFMPEG", "ffmpeg")
FFPROBE = os.environ.get("RC_FFPROBE", "ffprobe")
ENCODER = {"h264": "libx264", "h265": "libx265", "vp9": "libvpx-vp9", "av1": "libsvtav1"}
TAGS = ["-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-color_range", "tv"]

BANDS = [(0, 5), (5, 10), (10, 20), (20, 40), (40, 70), (70, 101)]
# thresholds for the verdict (rules of thumb, see the notes in the skill docs)
LIMITS = {"cambi_warn": 3.0, "cambi_bad": 5.0, "cambi_added": 1.5, "vmaf_warn": 80.0, "block_warn": 1.25,
          "shadow_warn": 0.5, "drift_warn": 1.0, "chroma_warn": 0.93}


def utf8_stdio():
    """Print clip names in any script (Devanagari, CJK, emoji) even where the console or pipe uses a legacy code
    page (Windows cp1252): stdout and stderr switch to UTF-8, and anything unprintable is replaced, never fatal."""
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, **kw)
    if r.returncode != 0:
        sys.stderr.write((r.stderr or b"").decode(errors="replace")[-3000:])
        raise RuntimeError("command failed: " + " ".join(cmd[:8]) + " ...")
    return r


# ------------------------------------------------------------------ 1. delivery checks
def probe(path):
    d = json.loads(run([FFPROBE, "-v", "error", "-show_streams", "-show_format", "-of", "json", path]).stdout)
    v = next(s for s in d["streams"] if s["codec_type"] == "video")
    num, den = (v.get("avg_frame_rate") or v.get("r_frame_rate") or "0/1").split("/")
    fps = float(num) / float(den) if float(den) else 0.0
    fmt = d["format"]
    dur = float(fmt.get("duration") or v.get("duration") or 0)
    vbr = float(v.get("bit_rate") or 0) or float(fmt.get("bit_rate") or 0)
    pix = v.get("pix_fmt", "")
    depth = 8
    for b in (16, 12, 10):
        if str(b) in pix:
            depth = b
            break
    return {
        "codec": v.get("codec_name"), "profile": v.get("profile"), "pix_fmt": pix, "bit_depth": depth,
        "width": v.get("width"), "height": v.get("height"), "fps": round(fps, 3), "duration": round(dur, 3),
        "video_kbps": round(vbr / 1000), "range": v.get("color_range", "unknown"),
        "primaries": v.get("color_primaries", "unknown"), "transfer": v.get("color_transfer", "unknown"),
        "matrix": v.get("color_space", "unknown"),
        "tracks": [s.get("codec_tag_string") or s.get("codec_name") for s in d["streams"]],
        "audio": [(s.get("codec_name"), s.get("sample_rate")) for s in d["streams"] if s["codec_type"] == "audio"],
    }


def delivery_checks(p):
    out = []

    def add(level, msg):
        out.append({"level": level, "msg": msg})
    w, h = p["width"], p["height"]
    if w and h:
        if abs(w / h - 9 / 16) > 0.01:
            add("warn", "aspect %dx%d is not 9:16; Reels/TikTok/Shorts will crop or letterbox it" % (w, h))
        if min(w, h) < 1080:
            add("bad", "%dx%d is below 1080x1920; every platform will serve it soft (IG keeps a 720p upload at 720p)" % (w, h))
        elif min(w, h) >= 2160:
            add("info", "2160x3840 master: YouTube Shorts then also serves 1440p/2160p renditions (AV1 ~6-12 Mbps); "
                        "Instagram and TikTok downscale to 1080x1920")
    if p["transfer"] in ("arib-std-b67", "smpte2084"):
        add("info", "HDR master (%s). Platforms make their own SDR version for SDR screens; check it on a normal "
                    "phone. This QC simulates the SDR path only." % p["transfer"])
    else:
        tags = (p["primaries"], p["transfer"], p["matrix"])
        if tags != ("bt709", "bt709", "bt709"):
            add("warn", "color tags are %s/%s/%s (primaries/transfer/matrix); platforms and YouTube's guide expect "
                        "bt709 in all three (1-1-1). In Resolve set Output Color Space and the Deliver tags to Rec.709 "
                        "(Scene) or Rec.709-A" % tags)
        if p["bit_depth"] > 8:
            add("info", "%d-bit SDR master: fine to upload, but the platforms deliver SDR as 8-bit, so this does not "
                        "protect against banding by itself" % p["bit_depth"])
    if p["range"] == "pc":
        add("warn", "full-range (data levels) master. Some players ignore the flag, which lifts blacks and clips "
                    "whites; export Video levels unless you have a reason")
    elif p["range"] in ("unknown", None):
        add("warn", "no data range flag; players will assume video (limited) range")
    fps = p["fps"]
    if fps and min(abs(fps - f) for f in (23.976, 24, 25, 29.97, 30, 50, 59.94, 60)) > 0.05:
        add("warn", "unusual frame rate %.3f fps" % fps)
    need = 8000 * (1.5 if fps > 30 else 1.0) * (4 if min(w or 0, h or 0) >= 2160 else 1)
    if p["codec"] in ("h264", "hevc", "vp9", "av1") and p["video_kbps"] and p["video_kbps"] < need:
        add("warn", "master is only %d kbps; upload at least %d kbps (YouTube's SDR guide; also good for IG/TikTok) so "
                    "the platform is not re-encoding an already starved file" % (p["video_kbps"], need))
    if "tmcd" in p["tracks"]:
        add("info", "file carries a timecode track (tmcd). Harmless for most uploads; Resolve has a render option to "
                    "leave it out for mobile YouTube uploads")
    if p["audio"] and any(a[0] not in ("aac", "opus") for a in p["audio"]):
        add("info", "audio is %s; AAC 48 kHz is the safe choice" % p["audio"])
    return out


# ------------------------------------------------------------------ 2. platform-like encodes
def ingest_vf(p, depth=8):
    fmt = "yuv420p" if depth == 8 else "yuv420p10le"
    rgb = p["pix_fmt"].startswith(("gbr", "rgb", "bgr"))
    fit = "scale=%d:%d:force_original_aspect_ratio=increase:flags=lanczos" % (W, H)  # fill 9:16, crop the rest
    if rgb:
        vf = fit + ":out_color_matrix=bt709:out_range=tv"
    else:
        vf = fit + ":in_color_matrix=bt709:out_color_matrix=bt709:in_range=%s:out_range=tv" % (
            "pc" if p["range"] == "pc" else "tv")
    return vf + ",crop=%d:%d,format=%s" % (W, H, fmt)


def encode(master, p, name, seg, work):
    codec, kbps, _ = PROFILES[name]
    k = int(kbps * (1.5 if p["fps"] > 30 else 1.0))
    out = os.path.join(work, name + ".mkv")
    src = ["-ss", str(seg[0]), "-t", str(seg[1]), "-i", master]
    base = [FFMPEG, "-v", "error", "-y"] + src + ["-an", "-sn", "-dn", "-map", "0:v:0", "-vf", ingest_vf(p)]
    rate = ["-b:v", "%dk" % k, "-maxrate", "%dk" % int(k * 1.5), "-bufsize", "%dk" % (k * 2)]
    log = os.path.join(work, name + "_pass")
    t0 = time.time()
    if codec == "h264":
        enc = ["-c:v", "libx264", "-preset", "medium", "-profile:v", "high"] + rate
        run(base + enc + ["-pass", "1", "-passlogfile", log, "-f", "null", "-"])
        run(base + enc + ["-pass", "2", "-passlogfile", log] + TAGS + [out])
    elif codec == "h265":
        xp = "log-level=error:vbv-maxrate=%d:vbv-bufsize=%d:stats=%s.log" % (int(k * 1.5), k * 2, log)
        enc = ["-c:v", "libx265", "-preset", "medium", "-b:v", "%dk" % k]
        run(base + enc + ["-x265-params", xp + ":pass=1", "-f", "null", "-"])
        run(base + enc + ["-x265-params", xp + ":pass=2"] + TAGS + [out])
    elif codec == "vp9":
        enc = ["-c:v", "libvpx-vp9", "-b:v", "%dk" % k, "-minrate", "%dk" % (k // 2), "-maxrate", "%dk" % int(k * 1.5),
               "-deadline", "good", "-row-mt", "1", "-tile-columns", "1", "-auto-alt-ref", "1", "-lag-in-frames", "25"]
        run(base + enc + ["-cpu-used", "4", "-pass", "1", "-passlogfile", log, "-f", "null", "-"])
        run(base + enc + ["-cpu-used", "2", "-pass", "2", "-passlogfile", log] + TAGS + [out])
    elif codec == "av1":
        enc = ["-c:v", "libsvtav1", "-preset", "8", "-b:v", "%dk" % k, "-svtav1-params", "rc=1"]
        run(base + enc + TAGS + [out])
    else:
        raise ValueError(codec)
    size = os.path.getsize(out)
    return out, {"target_kbps": k, "actual_kbps": round(size * 8 / 1000 / seg[1]), "encode_s": round(time.time() - t0, 1)}


# ------------------------------------------------------------------ 3. measurements
def libvmaf(sim, master, p, seg, work, name, sub):
    log = os.path.join(work, name + "_vmaf.json")
    lav = ("[0:v]format=yuv420p10le,setpts=PTS-STARTPTS[d];[1:v]%s,setpts=PTS-STARTPTS[r];"
           "[d][r]libvmaf=feature=name=cambi\\\\:full_ref=true|name=psnr:log_fmt=json:log_path=%s:n_threads=%d:"
           "n_subsample=%d:shortest=1" % (ingest_vf(p, 10), log, os.cpu_count() or 4, sub))
    run([FFMPEG, "-v", "error", "-i", sim, "-ss", str(seg[0]), "-t", str(seg[1]), "-i", master,
         "-lavfi", lav, "-f", "null", "-"])
    with open(log, encoding="utf-8", errors="replace") as fh:
        d = json.load(fh)
    fr = d["frames"]

    def col(k):
        return np.array([f["metrics"][k] for f in fr if k in f["metrics"]], float)
    cam, cams, vm = col("cambi"), col("cambi_source"), col("vmaf")
    worst = fr[int(np.argmax(cam))]["frameNum"] if len(cam) else 0
    return {"vmaf": round(float(vm.mean()), 1), "vmaf_p5": round(float(np.percentile(vm, 5)), 1),
            "cambi": round(float(cam.mean()), 2), "cambi_max": round(float(cam.max()), 2),
            "cambi_master": round(float(cams.mean()), 2), "psnr_y": round(float(col("psnr_y").mean()), 2),
            "worst_frame": int(worst), "_cambi_series": [round(float(x), 2) for x in cam]}


def read_frames(path, vf, idx, seg=None):
    """10-bit 4:2:0 planes of the frames with index in idx (list), as float arrays."""
    sel = "+".join("eq(n\\,%d)" % i for i in idx)
    cmd = [FFMPEG, "-v", "error"] + (["-ss", str(seg[0]), "-t", str(seg[1])] if seg else []) + ["-i", path,
           "-vf", vf + ",select='%s'" % sel, "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "yuv420p10le", "-"]
    raw = run(cmd).stdout
    n = W * H * 3 // 2
    a = np.frombuffer(raw, "<u2")
    out = []
    for i in range(len(a) // n):
        f = a[i * n:(i + 1) * n].astype(np.float32)
        Y = f[:W * H].reshape(H, W)
        U = f[W * H:W * H + W * H // 4].reshape(H // 2, W // 2)
        V = f[W * H + W * H // 4:].reshape(H // 2, W // 2)
        out.append((Y, U, V))
    return out


def box(x, r):
    """box blur with radius r (edge-clamped), via cumulative sums."""
    p = np.pad(x, r, mode="edge")
    c = p.cumsum(0).cumsum(1)
    c = np.pad(c, ((1, 0), (1, 0)))
    k = 2 * r + 1
    return (c[k:, k:] - c[:-k, k:] - c[k:, :-k] + c[:-k, :-k]) / (k * k)


def blockiness(Y, flat):
    """mean |step| across 8 px block edges / mean |step| elsewhere, both directions, flat areas only."""
    res = []
    for axis in (0, 1):
        d = np.abs(np.diff(Y, axis=axis))
        f = flat[:-1, :] & flat[1:, :] if axis == 0 else flat[:, :-1] & flat[:, 1:]
        pos = np.arange(d.shape[axis])
        edge = ((pos + 1) % 8 == 0)
        e = edge[:, None] if axis == 0 else edge[None, :]
        be, bo = d[f & e], d[f & ~e]
        if be.size > 500 and bo.size > 500:
            res.append((be.mean() + 0.25) / (bo.mean() + 0.25))  # +0.25 code: ignore sub-LSB noise
    return float(np.mean(res)) if res else 1.0


def frame_metrics(ref, sim):
    Yr, Ur, Vr = ref
    Ys, Us, Vs = sim
    v = (Yr - 64) / 876.0
    zones = {"shadows": v < 0.2, "mids": (v >= 0.2) & (v <= 0.7), "highlights": v > 0.7}
    m = {}
    for z, mk in zones.items():
        m["drift_" + z] = float((Ys - Yr)[mk].mean() / 4) if mk.sum() > 1000 else 0.0
        m["area_" + z] = float(mk.mean())
    hr, hs = Yr - box(Yr, 1), Ys - box(Ys, 1)
    loc = np.sqrt(np.maximum(box(Yr * Yr, 4) - box(Yr, 4) ** 2, 0))
    flat = loc < 4.0  # < 1 code value (8-bit) of local variation in the master
    tex = (loc > 1.2) & (loc < 12.0)  # fine texture (grain, skin, fabric), no hard edges
    sh = zones["shadows"] & tex
    m["shadow_detail"] = float(hs[sh].std() / max(hr[sh].std(), 1e-3)) if sh.sum() > 2000 else 1.0
    m["detail"] = float(hs[tex].std() / max(hr[tex].std(), 1e-3)) if tex.sum() > 2000 else 1.0
    for lo, hi in BANDS:  # fine texture kept per luma band (percent of video range)
        mk = (v * 100 >= lo) & (v * 100 < hi) & tex
        m["detail_%d_%d" % (lo, hi)] = float(hs[mk].std() / max(hr[mk].std(), 1e-3)) if mk.sum() > 2000 else float("nan")
    m["flat_area"] = float(flat.mean())
    m["blockiness"] = blockiness(Ys, flat)
    m["blockiness_master"] = blockiness(Yr, flat)
    Cr, Cs = np.hypot(Ur - 512, Vr - 512), np.hypot(Us - 512, Vs - 512)
    col = Cr > 24
    m["chroma"] = float(Cs[col].mean() / Cr[col].mean()) if col.sum() > 1000 else 1.0
    return m, flat


def picture_frames(path, vf, seg, nfr, want):
    """indices of `want` evenly spread frames that are real picture (not black or flat title cards)."""
    raw = run([FFMPEG, "-v", "error", "-ss", str(seg[0]), "-t", str(seg[1]), "-i", path, "-vf",
               vf + ",scale=108:192,format=gray", "-f", "rawvideo", "-"]).stdout
    a = np.frombuffer(raw, np.uint8).reshape(-1, 192, 108).astype(np.float32)
    v = (a - 16) / 219.0
    ok = [i for i in range(len(v)) if (v[i] < 0.03).mean() < 0.5 and v[i].std() > 0.03]
    cards = len(v) - len(ok)
    if not ok:
        ok = list(range(len(v)))
    pick = sorted(set(ok[int(round(j))] for j in np.linspace(0, len(ok) - 1, min(want, len(ok)))))
    return pick, cards


def master_levels(frames):
    Y = np.concatenate([f[0].ravel() for f in frames])
    v = (Y - 64) / 876.0 * 100  # percent of video range (IRE-like)
    return {"black_p0.5_pct": round(float(np.percentile(v, 0.5)), 1), "p50_pct": round(float(np.percentile(v, 50)), 1),
            "white_p99.5_pct": round(float(np.percentile(v, 99.5)), 1), "apl_pct": round(float(v.mean()), 1),
            "crushed_pct_area": round(float((v <= 1.0).mean() * 100), 2),
            "clipped_pct_area": round(float((v >= 99.0).mean() * 100), 2)}


# ------------------------------------------------------------------ 4. contact sheet
def to_rgb(f):
    Y, U, V = f
    y = (Y - 64) / 876.0
    cb = np.repeat(np.repeat((U - 512) / 896.0, 2, 0), 2, 1)
    cr = np.repeat(np.repeat((V - 512) / 896.0, 2, 0), 2, 1)
    rgb = np.stack([y + 1.5748 * cr, y - 0.1873 * cb - 0.4681 * cr, y + 1.8556 * cb], -1)
    return np.clip(rgb, 0, 1)


def contour(Y):
    y = (Y - 64) / 876.0
    return np.clip((y - box(y, 12)) * 10 + 0.5, 0, 1)


def sheet(ref, sims, flat, out):
    from PIL import Image, ImageDraw
    Yr = ref[0]
    # zoom window: the flat area where the re-encodes differ most from the master
    err = np.zeros_like(Yr)
    for _, f in sims:
        err += np.abs((f[0] - box(f[0], 3)) - (Yr - box(Yr, 3)))
    err = box(err * flat, 30)
    cy, cx = np.unravel_index(np.argmax(err[180:-180, 180:-180]), err[180:-180, 180:-180].shape)
    cy, cx = cy + 180, cx + 180
    Z = 180
    tiles = [("master", ref)] + sims
    tw, th = 270, 480
    img = Image.new("RGB", (tw * len(tiles), th + 2 * tw + 20), (20, 20, 20))
    d = ImageDraw.Draw(img)
    for i, (lab, f) in enumerate(tiles):
        rgb = to_rgb(f)
        full = Image.fromarray((rgb * 255 + 0.5).astype(np.uint8)).resize((tw, th), Image.LANCZOS)
        dd = ImageDraw.Draw(full)
        dd.rectangle([(cx - Z) * tw / W, (cy - Z) * th / H, (cx + Z) * tw / W, (cy + Z) * th / H], outline=(255, 220, 0))
        img.paste(full, (i * tw, 0))
        crop = rgb[cy - Z:cy + Z, cx - Z:cx + Z]
        if i == 0:  # auto-levels from the master's crop, reused for every tile so they stay comparable
            lo, hi = np.percentile(crop, 1), np.percentile(crop, 99)
            gain = 1.0 / max(hi - lo, 0.02)
        crop = np.clip((crop - lo) * gain, 0, 1)
        img.paste(Image.fromarray((crop * 255 + 0.5).astype(np.uint8)).resize((tw, tw), Image.NEAREST), (i * tw, th))
        c = contour(f[0])[cy - Z:cy + Z, cx - Z:cx + Z]
        img.paste(Image.fromarray((c * 255 + 0.5).astype(np.uint8)).resize((tw, tw), Image.NEAREST).convert("RGB"),
                  (i * tw, th + tw))
        d.rectangle([i * tw, th + 2 * tw, (i + 1) * tw - 1, th + 2 * tw + 19], fill=(0, 0, 0))
        d.text((i * tw + 4, th + 2 * tw + 4), lab, fill=(255, 220, 0))
    img.save(out, quality=90)


# ------------------------------------------------------------------ main
def available():
    """(encoders, has_libvmaf) of the ffmpeg in use."""
    try:
        enc = subprocess.run([FFMPEG, "-hide_banner", "-encoders"], capture_output=True, encoding="utf-8",
                             errors="replace").stdout
        flt = subprocess.run([FFMPEG, "-hide_banner", "-filters"], capture_output=True, encoding="utf-8",
                             errors="replace").stdout
    except OSError:
        sys.exit("ffmpeg not found: install it or set RC_FFMPEG")
    have = {c for c, lib in ENCODER.items() if (" %s " % lib) in enc}
    return have, " libvmaf " in flt


def verdict(r):
    notes = []
    L = LIMITS
    added = r["cambi"] - r["cambi_master"]
    src = (", %+.1f of it added by the re-encode" % added) if added >= L["cambi_added"] else ", already in the master"
    if r["cambi"] >= L["cambi_bad"] or r["cambi_max"] >= 2 * L["cambi_bad"]:
        notes.append("visible banding (CAMBI %.1f, max %.1f%s)" % (r["cambi"], r["cambi_max"], src))
    elif r["cambi"] >= L["cambi_warn"]:
        notes.append("some banding (CAMBI %.1f%s)" % (r["cambi"], src))
    if r["vmaf"] < L["vmaf_warn"] or r["vmaf_p5"] < L["vmaf_warn"] - 10:
        notes.append("heavy compression (VMAF %.0f, worst 5%% of frames %.0f)" % (r["vmaf"], r["vmaf_p5"]))
    if r["blockiness"] >= L["block_warn"] and r["blockiness"] / r["blockiness_master"] >= 1.08:
        notes.append("blocking in flat areas (%.2f, master %.2f)" % (r["blockiness"], r["blockiness_master"]))
    if r["shadow_detail"] < L["shadow_warn"]:
        notes.append("shadow texture smeared (%.0f%% kept)" % (r["shadow_detail"] * 100))
    for z in ("shadows", "mids", "highlights"):
        if abs(r["drift_" + z]) > L["drift_warn"]:
            notes.append("%s shifted %+.1f code values (check range/tags)" % (z, r["drift_" + z]))
    if r["chroma"] < L["chroma_warn"]:
        notes.append("saturation drops to %.0f%%" % (r["chroma"] * 100))
    level = "PASS" if not notes else ("FAIL" if any(k in " ".join(notes) for k in ("visible banding", "heavy", "shifted")) else "WARN")
    return level, notes


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("master")
    ap.add_argument("--out", default=None)
    ap.add_argument("--profiles", default="default")
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--seconds", type=float, default=0.0, help="0 = whole file (max 30 s)")
    ap.add_argument("--samples", type=int, default=12, help="frames used for the detail/blocking/drift metrics")
    ap.add_argument("--keep", action="store_true", help="keep the re-encoded files")
    a = ap.parse_args()

    names = []
    for tok in a.profiles.split(","):
        names += GROUPS.get(tok, [tok])
    names = [n for i, n in enumerate(names) if n in PROFILES and n not in names[:i]]
    out = a.out or os.path.splitext(a.master)[0] + "_social_qc"
    os.makedirs(out, exist_ok=True)
    work = tempfile.mkdtemp(prefix="qc_", dir=out)

    p = probe(a.master)
    checks = delivery_checks(p)
    have, vmaf_ok = available()
    skipped = {}
    if not vmaf_ok:
        for n in names:
            skipped[n] = "this ffmpeg has no libvmaf filter, so the re-encodes cannot be measured"
    for n in names:
        if n not in skipped and PROFILES[n][0] not in have:
            skipped[n] = "this ffmpeg has no %s encoder" % ENCODER[PROFILES[n][0]]
    for n, why in skipped.items():
        checks.append({"level": "info", "msg": "profile %s skipped: %s" % (n, why)})
        print("%-12s SKIPPED  %s" % (n, why), flush=True)
    names = [n for n in names if n not in skipped]
    dur = min(p["duration"] - a.start, a.seconds or 30.0, 30.0)
    seg = (a.start, round(dur, 3))
    nfr = int(dur * p["fps"])
    sub = max(1, nfr // 150)
    idx, cards = picture_frames(a.master, ingest_vf(p), seg, nfr, a.samples)
    if cards:
        checks.append({"level": "info", "msg": "%d black or flat title-card frames left out of the level and detail "
                                                "stats" % cards})

    ref = read_frames(a.master, ingest_vf(p, 10), idx, seg)
    levels = master_levels(ref)
    results, sim_frames, flat_ref = {}, {}, None
    for name in list(names):
        try:
            sim, enc = encode(a.master, p, name, seg, work)
            r = dict(enc)
            r.update(libvmaf(sim, a.master, p, seg, work, name, sub))
        except RuntimeError as e:
            names.remove(name)
            checks.append({"level": "info", "msg": "profile %s skipped: %s" % (name, e)})
            print("%-12s SKIPPED  %s" % (name, e), flush=True)
            continue
        fr = read_frames(sim, "format=yuv420p10le", idx)
        ms = []
        for rf, sf in zip(ref, fr):
            m, flat = frame_metrics(rf, sf)
            ms.append(m)
        for k in ms[0]:
            vals = [m[k] for m in ms if not np.isnan(m[k])]
            r[k] = round(float(np.mean(vals)), 3) if vals else None
        r["verdict"], r["notes"] = verdict(r)
        r["desc"] = PROFILES[name][2]
        results[name] = r
        # worst frame for the sheet
        wf = read_frames(sim, "format=yuv420p10le", [r["worst_frame"]])
        sim_frames[name] = wf[0] if wf else fr[0]
        if a.keep:
            shutil.copy(sim, os.path.join(out, name + ".mkv"))
        print("%-12s %-4s %5d kbps  VMAF %5.1f (p5 %5.1f)  CAMBI %4.1f max %4.1f (master %4.1f)  block %.2f (master %.2f)"
              "  shadow %.2f  chroma %.3f  drift %+.2f/%+.2f/%+.2f  %s" % (
                  name, r["verdict"], r["actual_kbps"], r["vmaf"], r["vmaf_p5"], r["cambi"], r["cambi_max"],
                  r["cambi_master"], r["blockiness"], r["blockiness_master"], r["shadow_detail"], r["chroma"],
                  r["drift_shadows"], r["drift_mids"], r["drift_highlights"], "; ".join(r["notes"])), flush=True)

    # sheet on the frame where the worst profile bands most
    wfi = None
    if results:
        worst = max(results, key=lambda n: results[n]["cambi"])
        wfi = results[worst]["worst_frame"]
        rf = read_frames(a.master, ingest_vf(p, 10), [wfi], seg)[0]
        sims = []
        for n in names:
            f = read_frames(os.path.join(work, n + ".mkv"), "format=yuv420p10le", [wfi])[0]
            sims.append(("%s %s" % (n, results[n]["verdict"]), f))
        loc = np.sqrt(np.maximum(box(rf[0] ** 2, 4) - box(rf[0], 4) ** 2, 0))
        sheet(rf, sims, loc < 4.0, os.path.join(out, "qc_sheet.jpg"))

    for r in results.values():
        r.pop("_cambi_series", None)
    report = {"master": a.master, "probe": p, "segment_s": seg, "delivery_checks": checks, "master_levels": levels,
              "profiles": results, "skipped": skipped, "sheet": "qc_sheet.jpg" if results else None,
              "sheet_frame": wfi, "limits": LIMITS}
    with open(os.path.join(out, "qc.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=1)
    lines = ["SOCIAL QC  %s" % os.path.basename(a.master),
             "master: %s %s %dx%d %.3gfps %d-bit %s range, tags %s/%s/%s, %d kbps" % (
                 p["codec"], p["profile"] or "", p["width"], p["height"], p["fps"], p["bit_depth"], p["range"],
                 p["primaries"], p["transfer"], p["matrix"], p["video_kbps"]),
             "levels: black %.1f%%  median %.1f%%  APL %.1f%%  white %.1f%%  crushed %.2f%% clipped %.2f%% of area" % (
                 levels["black_p0.5_pct"], levels["p50_pct"], levels["apl_pct"], levels["white_p99.5_pct"],
                 levels["crushed_pct_area"], levels["clipped_pct_area"]), ""]
    lines += ["[%s] %s" % (c["level"], c["msg"]) for c in checks] + [""]
    for n, r in results.items():
        lines.append("%-12s %-4s %s" % (n, r["verdict"], "; ".join(r["notes"]) or "ok"))
    txt = "\n".join(lines)
    with open(os.path.join(out, "qc.txt"), "w", encoding="utf-8") as fh:
        fh.write(txt + "\n")
    print("\n" + txt + "\n\nwrote", os.path.join(out, "qc.json"), os.path.join(out, "qc_sheet.jpg") if results else "(no sheet)")
    shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    utf8_stdio()
    main()
