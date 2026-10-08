#!/usr/bin/env python3
"""Camera formats for the resolve-colorist skill: log decoders, gamut matrices, format detection,
Resolve-like frame extraction and .cube LUT reading.

Only numpy is needed at import time. Pillow is used for non-integer downscales, ffmpeg and ffprobe
for reading video files, and colour-science only by the optional self test.

    python cameras.py list                 table of supported formats
    python cameras.py detect FILE...       guess the camera format of video files
    python cameras.py selftest             check every formula (uses colour-science when installed)

Conventions
-----------
v (the signal value)
    Every decoder takes v, the value DaVinci Resolve holds internally for the clip (0..1 after
    Resolve's Data Levels scaling), and returns scene linear light where 0.18 is an 18 percent grey
    card. Results can be below 0 and above 1. Display-referred entries (kind "sdr") return display
    linear light 0..1 instead. float32 input stays float32 (the same arithmetic as the v1 lab),
    anything else is computed in float64.
    The published formula is applied to v as it is. For curves written in 10-bit code values (Sony,
    Panasonic, Fujifilm, Nikon, Canon, DJI D-Log, Leica) this reads v as CV/1023, which is also what
    Resolve's own shaper LUTs do (checked in the self test).
matrix
    3x3, camera gamut linear RGB to linear Rec.709 RGB, D65 white, no chromatic adaptation.
status
    verified     manufacturer formula, checked against colour-science and the published values
    official     manufacturer formula, checked against published values or an official LUT only
    approximate  no published formula; only the lut:<path> route (a vendor LUT) is approximate
    display      display-referred video (Rec.709, sRGB, PQ), not a camera log curve
kind
    log (scene-referred log), hdr (HLG or PQ), sdr (display-referred SDR, graded without a tone curve)
clip_code
    signal value above which pixels count as sensor-clipped when balancing. 0.85 was measured on
    Sony S-Log3 footage. 0.92 for other log and HDR formats is a guess. 0.98 for SDR video.

Not verified yet (confirm with the compare step on the first real clip of a new camera):
    * Video-range (tv) files with curves written in code values: Resolve may or may not rescale
      them before its own curve. decode(..., policy="strict") gives the other reading.
    * The chroma zero of the Resolve-like decoder, (2^n-1)/2 for full range, was measured on 10-bit
      full-range files. 8 and 12 bit, and 2^(n-1) for video range, follow by analogy.
    * Untagged files are decoded with the BT.709 YCbCr matrix. Measured on UHD files only; Resolve
      might use BT.601 for untagged SD files.
"""
import json
import math
import os
import re
import subprocess
import sys
import threading

import numpy as np

FFMPEG = os.environ.get("RC_FFMPEG", "ffmpeg")
FFPROBE = os.environ.get("RC_FFPROBE", "ffprobe")
DECODER_VERSION = "raw-1"

# ---------------------------------------------------------------- colour maths helpers

D65 = (0.3127, 0.3290)


def npm(prims, white=D65):
    """RGB to XYZ normalised primary matrix from xy primaries ((xr, yr), (xg, yg), (xb, yb)) and white xy."""
    P = np.array([[x / y, 1.0, (1 - x - y) / y] for x, y in prims]).T
    wx, wy = white
    W = np.array([wx / wy, 1.0, (1 - wx - wy) / wy])
    return P * np.linalg.solve(P, W)


PRIMS = {
    # gamut: ((R), (G), (B)), white. Sources are in the CAMERAS table below.
    "rec709": (((0.64, 0.33), (0.30, 0.60), (0.15, 0.06)), D65),
    "rec2020": (((0.708, 0.292), (0.170, 0.797), (0.131, 0.046)), D65),
    "p3d65": (((0.680, 0.320), (0.265, 0.690), (0.150, 0.060)), D65),
    "sgamut3": (((0.730, 0.280), (0.140, 0.855), (0.100, -0.050)), D65),  # also S-Gamut (S-Log2)
    "sgamut3cine": (((0.766, 0.275), (0.225, 0.800), (0.089, -0.087)), D65),
    "vgamut": (((0.730, 0.280), (0.165, 0.840), (0.100, -0.030)), D65),
    "cinemagamut": (((0.740, 0.270), (0.170, 1.140), (0.080, -0.100)), D65),
    "fgamutc": (((0.7347, 0.2653), (0.0263, 0.9737), (0.1173, -0.0224)), D65),
    "dgamut": (((0.71, 0.31), (0.21, 0.88), (0.09, -0.08)), D65),
    # D-Gamut2: xy recovered from the 4-decimal D-Gamut2 to XYZ matrix in DJI's D-Log2 DCTL
    "dgamut2": (((0.7347, 0.2653), (0.16, 0.84), (0.09, -0.08)), D65),
    "awg3": (((0.6840, 0.3130), (0.2210, 0.8480), (0.0861, -0.1020)), D65),
    "awg4": (((0.7347, 0.2653), (0.1424, 0.8576), (0.0991, -0.0308)), D65),
    "rwg": (((0.780308, 0.304253), (0.121595, 1.493994), (0.095612, -0.084589)), D65),
    "bmdwg": (((0.7177215, 0.3171181), (0.2280410, 0.8615690), (0.1005841, -0.0820452)),
              (0.3127170, 0.3290312)),
    "dwg": (((0.8000, 0.3130), (0.1682, 0.9877), (0.0790, -0.1155)), D65),
    "protune_native": (((0.698480461, 0.193026320), (0.329555378, 1.024596312),
                        (0.108442794, -0.034678540)), D65),
    "apple_wide_gamut": (((0.725, 0.301), (0.221, 0.814), (0.068, -0.076)), D65),
}

NPM709_INV = np.linalg.inv(npm(*PRIMS["rec709"]))


def to709(gamut):
    """camera gamut linear RGB to linear Rec.709 RGB (no chromatic adaptation)."""
    prims, white = PRIMS[gamut]
    return NPM709_INV @ npm(prims, white)


# S-Gamut3.Cine to Rec.709 exactly as the v1 lab used it (verified against Resolve 21.1). It equals
# to709("sgamut3cine") to 8e-8; keeping the rounded constants keeps v1 grades bit-identical.
SGAMUT3CINE_TO_709_V1 = np.array([[1.6269474, -0.5401385, -0.0868089],
                                  [-0.1785155, 1.4179409, -0.2394254],
                                  [-0.0444361, -0.1959199, 1.2403560]])

# ---------------------------------------------------------------- decoders
# All: v (Resolve internal value) -> scene linear, 0.18 = grey. float32 stays float32.


def _as_float(v):
    a = np.asarray(v)
    if a.dtype == np.float32 or a.dtype == np.float64:
        return a
    return a.astype(np.float64)


def _w(fn):
    def g(v):
        v = _as_float(v)
        with np.errstate(all="ignore"):
            return fn(v)
    g.__name__ = fn.__name__
    g.__doc__ = fn.__doc__
    return g


@_w
def slog3(x):
    """Sony S-Log3. Sony 'Technical Summary for S-Gamut3.Cine/S-Log3 and S-Gamut3/S-Log3'.
    Same expression as the v1 lab's slog3_to_lin, so results are bit-identical for any dtype."""
    c = x * 1023.0
    return np.where(c >= 171.2102946929, 10 ** ((c - 420.0) / 261.5) * 0.19 - 0.01,
                    (c - 95.0) * 0.01125 / (171.2102946929 - 95.0))


_SLOG2_T0 = (0.432699 * math.log10(0.037584) + 0.616596 + 0.03) * 876.0 / 1023.0 + 64.0 / 1023.0


@_w
def slog2(v):
    """Sony S-Log2 (older a7S II / FS5 era). Sony S-Log2 Technical Paper. 0.18 at CV 347."""
    x = (v * 1023.0 - 64.0) / 876.0
    lin = np.where(v >= _SLOG2_T0, 10 ** ((x - 0.616596 - 0.03) / 0.432699) - 0.037584,
                   (x - 0.030001222851889303) / 5.0)
    return 219.0 * lin * 0.9 / 155.0


@_w
def vlog(v):
    """Panasonic V-Log. V-Log/V-Gamut Reference Manual: 0% 128, 18% 433, 90% 602 (10-bit)."""
    b, c, d = 0.00873, 0.241514, 0.598206
    return np.where(v < 0.181, (v - 0.125) / 5.6, 10 ** ((v - d) / c) - b)


@_w
def flog(v):
    """Fujifilm F-Log. F-Log Data Sheet Ver.1.1: 0% 95, 18% 470, 90% 705."""
    a, b, c, d, e, f = 0.555556, 0.009468, 0.344676, 0.790453, 8.735631, 0.092864
    return np.where(v >= 0.100537775223865, 10 ** ((v - d) / c) / a - b / a, (v - f) / e)


@_w
def flog2(v):
    """Fujifilm F-Log2 (and F-Log2 C, same curve). Data Sheets Ver.1.0: 0% 95, 18% 400, 90% 570."""
    a, b, c, d, e, f = 5.555556, 0.064829, 0.245281, 0.384316, 8.799461, 0.092864
    return np.where(v >= 0.100686685370811, 10 ** ((v - d) / c) / a - b / a, (v - f) / e)


@_w
def nlog(v):
    """Nikon N-Log. N-Log Specification Document 1.0.0 (formula written in 10-bit code values)."""
    x = v * 1023.0
    return np.where(x < 452.0, (x / 650.0) ** 3 - 0.0075, np.exp((x - 619.0) / 150.0))


@_w
def dlog(v):
    """DJI D-Log (Zenmuse X5S/X7/X9, Inspire, Mavic 3 Cine, Ronin 4D). DJI white paper: 0% 95, 18% 408, 90% 586."""
    return np.where(v <= 0.14, (v - 0.0929) / 6.025, (10 ** (3.89616 * v - 2.27752) - 0.0108) / 0.9892)


_DLOG2_LOG2_GREY = math.log2(0.18)


@_w
def dlog2(v):
    """DJI D-Log2 (Osmo Pocket 4P and newer). From DJI's official D-Log2 / D-Gamut2 DCTL."""
    H, a = 475.0, 16.285770761945304
    k1, b1 = 0.059439938321493, 0.304985337243402
    k2, b2 = 2.960935245492250, 0.148314799066323
    return np.where(v >= b1, H / (2 ** a - 1) * (2 ** (a * v) - 1),
                    np.where(v >= b2, 2 ** ((v - b1) / k1 + _DLOG2_LOG2_GREY), (v - b2) / k2 + 0.028961695254132))


@_w
def canonlog(v):
    """Canon Log (v1.2 constants, as colour-science). Canon Log transfer characteristic paper."""
    return 0.9 * np.where(v < 0.12512248, -(10 ** ((0.12512248 - v) / 0.45310179) - 1) / 10.1596,
                          (10 ** ((v - 0.12512248) / 0.45310179) - 1) / 10.1596)


@_w
def canonlog2(v):
    """Canon Log 2 (v1.2). Canon 'Canon Log Gamma Curves' white paper."""
    return 0.9 * np.where(v < 0.092864125, -(10 ** ((0.092864125 - v) / 0.24136077) - 1) / 87.09937546,
                          (10 ** ((v - 0.092864125) / 0.24136077) - 1) / 87.09937546)


@_w
def canonlog3(v):
    """Canon Log 3 (v1.2)."""
    return 0.9 * np.select(
        (v < 0.097465473, v <= 0.15277891),
        (-(10 ** ((0.12783901 - v) / 0.36726845) - 1) / 14.98325, (v - 0.12512219) / 1.9754798),
        (10 ** ((v - 0.12240537) / 0.36726845) - 1) / 14.98325)


@_w
def logc3(v):
    """ARRI LogC3, EI 800, scene linear. ARRI 'ALEXA Log C Curve, Usage in VFX'."""
    cut, a, b, c, d, e, f = 0.010591, 5.555556, 0.052272, 0.247190, 0.385537, 5.367655, 0.092809
    return np.where(v > e * cut + f, (10 ** ((v - d) / c) - b) / a, (v - f) / e)


_A4 = (2 ** 18 - 16) / 117.45
_B4 = (1023 - 95) / 1023
_C4 = 95 / 1023
_S4 = (7 * math.log(2) * 2 ** (7 - 14 * _C4 / _B4)) / (_A4 * _B4)
_T4 = (2 ** (14 * (-_C4 / _B4) + 6) - 64) / _A4


@_w
def logc4(v):
    """ARRI LogC4. ARRI LogC4 Specification (2022)."""
    return np.where(v >= 0, (2 ** (14 * (v - _C4) / _B4 + 6) - 64) / _A4, v * _S4 + _T4)


@_w
def bmdfilm_gen5(v):
    """Blackmagic Film Generation 5. Blackmagic Generation 5 Color Science technical reference."""
    A, B, C, D, E = 0.08692876065491224, 0.005494072432257808, 0.5300133392291939, 8.283605932402494, 0.09246575342465753
    return np.where(v < D * 0.005 + E, (v - E) / D, np.exp((v - C) / A) - B)


@_w
def davinci_intermediate(v):
    """DaVinci Intermediate. Blackmagic 'DaVinci Resolve 17 Wide Gamut Intermediate' (18% at 0.336043)."""
    return np.where(v > 0.02740668, 2 ** (v / 0.07329248 - 7.0) - 0.0075, v / 10.44426855)


@_w
def log3g10(v):
    """RED Log3G10 v3 (IPP2). RED white paper on REDWideGamutRGB and Log3G10. 18% at 1/3."""
    a, b, c, g = 0.224282, 155.975327, 0.01, 15.1927
    return np.where(v < 0.0, v / g - c, (10 ** (v / a) - 1.0) / b - c)


@_w
def protune(v):
    """GoPro Protune log (HERO4 to HERO7 era 'Protune Flat', ACES 1.0.3 config). Not GP-Log."""
    return (113.0 ** v - 1.0) / 112.0


@_w
def gplog2(v):
    """GoPro GP-Log2. GoPro Labs: L = (600^v - 1)/599, 18% grey at L = 0.0517 (+1.8 EV)."""
    return (600.0 ** v - 1.0) / 599.0 * (0.18 / 0.0517)


@_w
def samsung_log(v):
    """Samsung Log (Galaxy S24 and later). Matches Samsung's 'Samsung Log to Linear' LUT shipped in Resolve."""
    return np.where(v >= 0.206561909, 10 ** ((v - 0.720504856) / 0.258984868) - 0.0003645,
                    np.maximum(-(10 ** ((v + 0.24597) / -0.20942)) + 0.016904, -0.05))


@_w
def ilog(v):
    """Insta360 I-Log (Luna, X5, Ace Pro 2, Go Ultra). Insta360 10-bit I-Log White Paper."""
    alpha, beta, delta, eta, theta = 5.77837328, 0.09055934, 0.623992, 0.280055, 0.01
    return np.where(v < 0.154402, (v - beta) / alpha, 10 ** ((v - delta) / eta) - theta)


@_w
def applelog(v):
    """Apple Log (iPhone 15 Pro and later), also used by Apple Log 2. Apple Log (2) White Paper."""
    R0, Rt, c, beta, gamma, delta = -0.05641088, 0.01, 47.28711236, 0.00964052, 0.08550479, 0.69336945
    Pt = c * (Rt - R0) ** 2
    return np.where(v >= Pt, 2 ** ((v - delta) / gamma) - beta,
                    np.where(v > 0, np.sqrt(np.maximum(v, 0) / c) + R0, R0))


@_w
def llog(v):
    """Leica L-Log (SL2, SL3, Q3, M11 video). Leica L-Log Reference Manual."""
    return np.where(v <= 0.1380, (v - 0.09) / 8.0, (10 ** ((v - 0.6) / 0.27) - 0.0115) / 1.3)


HLG_GREY_GAIN = 0.18 / (0.38 ** 2 / 3.0)   # BT.2408: 18% grey card at 38% HLG signal
_HLG_A = 0.17883277
_HLG_B = 1 - 4 * _HLG_A
_HLG_C = 0.5 - _HLG_A * math.log(4 * _HLG_A)


@_w
def hlg(v):
    """BT.2100 HLG inverse OETF, scaled so BT.2408 grey (HLG 0.38) = 0.18 and HLG 0.75 (reference white) is about 1.0."""
    e = np.where(v <= 0.5, np.maximum(v, 0) ** 2 / 3.0, (np.exp((v - _HLG_C) / _HLG_A) + _HLG_B) / 12.0)
    return e * HLG_GREY_GAIN


@_w
def pq(v):
    """SMPTE ST 2084 (HDR10 / HDR10+ video). Display light, scaled so 26 nits (BT.2408 grey) = 0.18."""
    m1, m2 = 2610 / 16384, 2523 / 4096 * 128
    c1, c2, c3 = 3424 / 4096, 2413 / 4096 * 32, 2392 / 4096 * 32
    p = np.maximum(v, 0) ** (1 / m2)
    nits = 10000.0 * (np.maximum(p - c1, 0) / (c2 - c3 * p)) ** (1 / m1)
    return nits * 0.18 / 26.0


@_w
def bt1886(v):
    """Rec.709 video as displayed (BT.1886, gamma 2.4, black 0). Display-referred."""
    return np.sign(v) * np.abs(v) ** 2.4


@_w
def srgb(v):
    """sRGB (IEC 61966-2-1) piecewise EOTF. Display-referred."""
    a = np.abs(v)
    return np.sign(v) * np.where(a <= 0.04045, a / 12.92, ((a + 0.055) / 1.055) ** 2.4)


@_w
def rec709_scene(v):
    """Inverse BT.709 camera OETF (what Resolve calls 'Rec.709 (Scene)'). Helper, not a camera entry."""
    return np.where(v < 1.099 * 0.018 ** 0.45 - 0.099, v / 4.5, ((v + 0.099) / 1.099) ** (1 / 0.45))


# ---------------------------------------------------------------- camera table

_SONY = "https://pro.sony/s3/cms-static-content/uploadfile/06/1237494271406.pdf"
_CANON = "https://downloads.canon.com/nw/learn/white-papers/cinema-eos/white-paper-canon-log-gamma-curves.pdf"


def _cam(key, decode, matrix, name, status, kind, source, resolve_name, resolve_gamut, clip_code=None):
    if clip_code is None:
        clip_code = 0.98 if kind == "sdr" else 0.92
    return dict(key=key, decode=decode, matrix=np.asarray(matrix, dtype=np.float64), name=name, status=status,
                kind=kind, clip_code=clip_code, source=source, resolve_name=resolve_name,
                resolve_gamut=resolve_gamut)


_ROWS = [
    # key, decode, matrix, name, status, kind, source, Resolve 21.1 gamma name, Resolve gamut name
    ("slog3_sg3c", slog3, SGAMUT3CINE_TO_709_V1, "Sony S-Log3 / S-Gamut3.Cine", "verified", "log", _SONY,
     "Sony S-Log3", "Sony S-Gamut3.Cine", 0.85),
    ("slog3_sg3", slog3, to709("sgamut3"), "Sony S-Log3 / S-Gamut3", "verified", "log", _SONY,
     "Sony S-Log3", "Sony S-Gamut3", 0.85),
    ("slog2_sg", slog2, to709("sgamut3"), "Sony S-Log2 / S-Gamut", "verified", "log",
     "Sony S-Log2 Technical Paper (2012)", "Sony S-Log2", "Sony S-Gamut", 0.85),
    ("applelog", applelog, to709("rec2020"), "Apple Log / Rec.2020 (iPhone 15 Pro, 16 Pro)", "verified", "log",
     "https://developer.apple.com/download/all/?q=Apple%20log%20profile", "Apple Log", "Rec.2020"),
    ("applelog2", applelog, to709("apple_wide_gamut"), "Apple Log 2 / Apple Wide Gamut (iPhone 17 Pro)",
     "official", "log", "Apple Log 2 White Paper, September 2025 (developer.apple.com)", "Apple Log 2",
     "Apple Wide Gamut"),
    ("dlog_dgamut", dlog, to709("dgamut"), "DJI D-Log / D-Gamut", "verified", "log",
     "https://dl.djicdn.com/downloads/zenmuse+x7/20171010/D-Log_D-Gamut_Whitepaper.pdf", "DJI D-Log",
     "DJI D-Gamut"),
    ("dlog2_dgamut2", dlog2, to709("dgamut2"), "DJI D-Log2 / D-Gamut2 (Osmo Pocket 4P)", "official", "log",
     "DJI D-Log2 / D-Gamut2 DCTL published by DJI", "DJI D-Log2", "DJI D-Gamut2"),
    ("vlog_vgamut", vlog, to709("vgamut"), "Panasonic V-Log / V-Gamut", "verified", "log",
     "https://pro-av.panasonic.net/en/cinema_camera_varicam_eva/support/pdf/VARICAM_V-Log_V-Gamut.pdf",
     "Panasonic V-Log", "Panasonic V-Gamut"),
    ("clog_cg", canonlog, to709("cinemagamut"), "Canon Log / Cinema Gamut", "verified", "log",
     "http://downloads.canon.com/CDLC/Canon-Log_Transfer_Characteristic_6-20-2012.pdf", "Canon Log",
     "Canon Cinema Gamut"),
    ("clog2_cg", canonlog2, to709("cinemagamut"), "Canon Log 2 / Cinema Gamut", "verified", "log", _CANON,
     "Canon Log 2", "Canon Cinema Gamut"),
    ("clog3_cg", canonlog3, to709("cinemagamut"), "Canon Log 3 / Cinema Gamut", "verified", "log", _CANON,
     "Canon Log 3", "Canon Cinema Gamut"),
    ("clog3_2020", canonlog3, to709("rec2020"), "Canon Log 3 / BT.2020 (mirrorless 'BT.2020' color matrix)",
     "verified", "log", _CANON + " + ITU-R BT.2020", "Canon Log 3", "Rec.2020"),
    ("flog_fgamut", flog, to709("rec2020"), "Fujifilm F-Log / F-Gamut (= BT.2020)", "verified", "log",
     "https://dl.fujifilm-x.com/support/lut/F-Log_DataSheet_E_Ver.1.1.pdf", "Fujifilm F-Log", "Rec.2020"),
    ("flog2_fgamut", flog2, to709("rec2020"), "Fujifilm F-Log2 / F-Gamut (= BT.2020)", "verified", "log",
     "https://dl.fujifilm-x.com/support/lut/F-Log2_DataSheet_E_Ver.1.0.pdf", "Fujifilm F-Log2", "Rec.2020"),
    ("flog2c_fgamutc", flog2, to709("fgamutc"), "Fujifilm F-Log2 C / F-Gamut C", "verified", "log",
     "https://dl.fujifilm-x.com/support/lut/F-Log2C_DataSheet_E_Ver.1.0.pdf", "Fujifilm F-Log2 C",
     "Fujifilm F-Gamut C"),
    ("nlog", nlog, to709("rec2020"), "Nikon N-Log / BT.2020", "verified", "log",
     "https://download.nikonimglib.com/archive3/hDCmK00m9JDI03RPruD74xpoU905/N-Log_Specification_(En)01.pdf",
     "Nikon N-Log", "Rec.2020"),
    ("logc3_awg3", logc3, to709("awg3"), "ARRI LogC3 (EI 800) / AWG3", "verified", "log",
     "https://www.arri.com/en/learn-help/learn-help-camera-system/image-science/log-c", "ARRI LogC3",
     "ARRI Wide Gamut 3"),
    ("logc4_awg4", logc4, to709("awg4"), "ARRI LogC4 / AWG4", "verified", "log",
     "https://www.arri.com/resource/blob/278790/bea879ac0d041a925bed27a096ab3ec2/2022-05-arri-logc4-specification-data.pdf",
     "ARRI LogC4", "ARRI Wide Gamut 4"),
    ("bmd_gen5", bmdfilm_gen5, to709("bmdwg"), "Blackmagic Film Gen 5 / BMD Wide Gamut Gen 4/5", "verified", "log",
     "Blackmagic Generation 5 Color Science technical reference", "Blackmagic Design Film Gen 5",
     "Blackmagic Design Wide Gamut Gen 4 / 5"),
    ("di_dwg", davinci_intermediate, to709("dwg"), "DaVinci Intermediate / DaVinci Wide Gamut", "verified", "log",
     "https://documents.blackmagicdesign.com/InformationNotes/DaVinci_Resolve_17_Wide_Gamut_Intermediate.pdf",
     "DaVinci Intermediate", "DaVinci WG"),
    ("log3g10_rwg", log3g10, to709("rwg"), "RED Log3G10 / REDWideGamutRGB", "verified", "log",
     "https://www.red.com/download/white-paper-on-redwidegamutrgb-and-log3g10", "RED Log3G10", "REDWideGamutRGB"),
    ("gplog2", gplog2, to709("rec2020"), "GoPro GP-Log2 / Rec.2020 (GoPro Labs)", "official", "log",
     "https://gopro.github.io/labs/log/", "GoPro GP-Log2", "Rec.2020"),
    ("protune_native", protune, to709("protune_native"), "GoPro Protune log / Protune Native (legacy, HERO4 to HERO7)",
     "verified", "log", "ACES 1.0.3 OpenColorIO config (GoPro Protune)", None, None),
    ("samsung_log", samsung_log, to709("rec2020"), "Samsung Log / BT.2020", "official", "log",
     "https://developer.samsung.com/mobile/samsung-log-video.html (white paper and 1D LUT)", "Samsung Log",
     "Rec.2020"),
    ("ilog", ilog, to709("rec2020"), "Insta360 I-Log / BT.2020", "official", "log",
     "https://wassets.insta360.com/common/31f0330509384b7b8ba0a07f4ff4eb13/Insta360_10-bit_I-Log_White_Paper.pdf",
     "Insta360 I-Log", "Rec.2020"),
    ("llog_2020", llog, to709("rec2020"), "Leica L-Log / BT.2020", "verified", "log",
     "https://leica-camera.com/sites/default/files/pm-65976-210914__L-Log_Reference_Manual_EN.pdf", "Leica L-Log",
     "Rec.2020"),
    ("hlg_2020", hlg, to709("rec2020"), "HLG / BT.2020 (phone HDR video, Dolby Vision 8.4 base layer)", "verified",
     "hdr", "ITU-R BT.2100 + BT.2408", "Rec.2100 HLG", "Rec.2020"),
    ("hlg_709", hlg, np.eye(3), "HLG / Rec.709 primaries (camera HLG with the BT.709 color option)", "verified",
     "hdr", "ITU-R BT.2100 + BT.2408", "Rec.2100 HLG", "Rec.709"),
    ("pq_2020", pq, to709("rec2020"), "PQ / BT.2020 (HDR10, HDR10+ video)", "display", "hdr",
     "SMPTE ST 2084 + ITU-R BT.2408", "Rec.2100 ST2084", "Rec.2020"),
    ("rec709", bt1886, np.eye(3), "Rec.709 video (SDR, gamma 2.4)", "display", "sdr", "ITU-R BT.1886",
     "Gamma 2.4", "Rec.709"),
    ("rec2020_sdr", bt1886, to709("rec2020"), "Rec.2020 SDR video (gamma 2.4)", "display", "sdr",
     "ITU-R BT.2020 + BT.1886", "Gamma 2.4", "Rec.2020"),
    ("srgb", srgb, np.eye(3), "sRGB (screen recordings, stills)", "display", "sdr", "IEC 61966-2-1", "sRGB", "sRGB"),
    ("p3_display", srgb, to709("p3d65"), "Display P3 video (some phone apps)", "display", "sdr",
     "SMPTE EG 432-1 + IEC 61966-2-1", "sRGB", "P3-D65"),
]

CAMERAS = {r[0]: _cam(*r) for r in _ROWS}

ALIASES = {
    "slog3": "slog3_sg3c", "slog3cine": "slog3_sg3c", "slog3_cine": "slog3_sg3c", "slog2": "slog2_sg",
    "apple_log": "applelog", "apple_log2": "applelog2",
    "dlog": "dlog_dgamut", "dlog2": "dlog2_dgamut2",
    "vlog": "vlog_vgamut",
    "clog": "clog_cg", "clog2": "clog2_cg", "clog3": "clog3_cg",
    "flog": "flog_fgamut", "flog2": "flog2_fgamut", "flog2c": "flog2c_fgamutc",
    "logc": "logc3_awg3", "logc3": "logc3_awg3", "logc4": "logc4_awg4",
    "bmdfilm": "bmd_gen5", "bmd": "bmd_gen5", "davinci_intermediate": "di_dwg", "dwg": "di_dwg",
    "log3g10": "log3g10_rwg", "protune": "protune_native", "samsung": "samsung_log", "llog": "llog_2020",
    "hlg": "hlg_2020", "iphone_hdr": "hlg_2020", "pq": "pq_2020", "hdr10": "pq_2020",
    "bt709": "rec709", "sdr": "rec709", "p3": "p3_display",
}

# Formats people shoot that have no published formula (or none found yet). Detection can return these
# keys with status "unsupported"; get_camera() refuses them and explains the vendor LUT route.
UNSUPPORTED = {
    "dlogm": "DJI D-Log M",
    "gplog": "GoPro GP-Log (HERO12 and newer)",
    "milog": "Xiaomi Mi-Log",
    "olog": "OPPO O-Log",
    "vivolog": "vivo Log",
    "zlog2": "Z CAM Z-Log2",
    "kinelog3": "Kinefinity KineLog3",
}
_UNSUPPORTED_ALIASES = {"dlog_m": "dlogm", "d-log m": "dlogm", "dlogm_fit": "dlogm", "gp-log": "gplog",
                        "gplog1": "gplog"}

# Curves whose white paper is written in 10-bit code values stored in the file (IRE = (CV-64)/876). Only
# decode(policy="strict") uses this list; the lab and the baked LUTs use policy "resolve". The list may be partial.
CV_DOMAIN = {"slog3_sg3c", "slog3_sg3", "slog2_sg", "vlog_vgamut", "flog_fgamut", "flog2_fgamut", "flog2c_fgamutc",
             "nlog", "dlog_dgamut", "clog_cg", "clog2_cg", "clog3_cg", "clog3_2020", "llog_2020", "logc3_awg3"}


class CameraKeyError(KeyError):
    """KeyError with a readable message (plain KeyError prints its message in quotes)."""

    def __str__(self):
        return str(self.args[0]) if self.args else ""


LUT_ROUTE = ("For a format without a published formula, download the camera maker's official log to "
             "Rec.709 LUT (.cube) and use the camera key lut:<absolute path to the .cube> (for example in "
             "camera_overrides in timeline.json). The lab then treats the LUT output as Rec.709 video; the "
             "grade is approximate.")


def camera_keys():
    """Sorted canonical camera keys."""
    return sorted(CAMERAS)


def camera_table():
    """[{key, name, status, kind, resolve_name}] sorted by key, for docs and `cameras.py list`."""
    return [{"key": k, "name": c["name"], "status": c["status"], "kind": c["kind"],
             "resolve_name": c["resolve_name"]} for k, c in sorted(CAMERAS.items())]


def _unsupported_key(k):
    k = _UNSUPPORTED_ALIASES.get(k, k)
    if k in UNSUPPORTED:
        return k
    if k.startswith("dlogm"):
        return "dlogm"
    return None


def get_camera(key):
    """Camera entry for a key, an alias, or the pseudo key "lut:<path to .cube>".

    Raises CameraKeyError (a KeyError) for unknown or unsupported keys; the message lists the valid
    keys and explains the lut:<path> route. Treat the returned dict as read-only.
    """
    if not isinstance(key, str) or not key.strip():
        raise CameraKeyError("no camera key given. Valid keys: %s. %s" % (", ".join(camera_keys()), LUT_ROUTE))
    raw = key.strip()
    if raw[:4].lower() == "lut:":
        return _lut_camera(raw)
    k = raw.lower()
    k = ALIASES.get(k, k)
    if k in CAMERAS:
        return CAMERAS[k]
    u = _unsupported_key(k)
    if u:
        raise CameraKeyError("%s (%r) has no published formula, so the lab cannot decode it by itself. %s"
                             % (UNSUPPORTED[u], key, LUT_ROUTE))
    raise CameraKeyError("unknown camera key %r. Valid keys: %s. Aliases: %s. %s"
                         % (key, ", ".join(camera_keys()), ", ".join(sorted(ALIASES)), LUT_ROUTE))


def legacy(key):
    """(decode, matrix) tuple in the shape the v1 grade_lab CAMERAS dict used."""
    c = get_camera(key)
    return c["decode"], c["matrix"]


def resolve_levels(data_level, color_range):
    """How Resolve scales a clip: "full" or "video".

    data_level is Resolve's clip property "Data Level" ("Auto", "Video" or "Full", any case, may be None).
    color_range is the file flag from probe() ("pc", "tv" or "unknown"). Auto follows the file flag
    (verified: Sony XAVC S-I files flagged pc are treated as full range).
    """
    d = (data_level or "auto").strip().lower()
    if d in ("video", "full"):
        return d
    return "full" if (color_range or "").strip().lower() == "pc" else "video"


def decode(v, key, levels="full", policy="resolve"):
    """Decode Resolve internal values v for one clip with the camera `key`.

    policy="resolve" (default): apply the curve to v as it is (what Resolve's own shaper LUTs and the
      vendor LUTs do; identical to the manufacturer definition when the clip is full range).
    policy="strict": for curves written in code values on a video-range clip, first rebuild the file
      code value CV = 64 + 876 v and decode CV/1023, as the white papers define it. Differs from
      "resolve" by about 0.33 stop at 90% white for S-Log3. Which one Resolve uses is not verified.
    """
    if policy not in ("resolve", "strict"):
        raise ValueError("policy must be 'resolve' or 'strict', not %r" % (policy,))
    if levels not in ("full", "video"):
        raise ValueError("levels must be 'full' or 'video', not %r" % (levels,))
    c = get_camera(key)
    v = _as_float(v)
    if policy == "strict" and levels == "video" and c["key"] in CV_DOMAIN:
        v = (64.0 + 876.0 * v) / 1023.0
    return c["decode"](v)


# ---------------------------------------------------------------- .cube LUTs


def load_cube(path):
    """Read a 3D .cube file. Returns {"size", "table" (N, N, N, 3) float64 indexed [r, g, b],
    "domain_min", "domain_max", "title", "in_video_range", "out_video_range"}."""
    size, title = None, ""
    dmin, dmax = [0.0, 0.0, 0.0], [1.0, 1.0, 1.0]
    in_video = out_video = False
    rows = []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            s = line.strip()
            if not s or s[0] == "#":
                continue
            head = s.split(None, 1)[0].upper()
            if head[0].isalpha():
                parts = s.split()
                if head == "TITLE":
                    title = s[5:].strip().strip('"')
                elif head == "LUT_3D_SIZE":
                    size = int(parts[1])
                elif head == "LUT_1D_SIZE":
                    raise ValueError("%s is a 1D LUT; only 3D .cube files are supported" % path)
                elif head == "DOMAIN_MIN":
                    dmin = [float(x) for x in parts[1:4]]
                elif head == "DOMAIN_MAX":
                    dmax = [float(x) for x in parts[1:4]]
                elif head == "LUT_3D_INPUT_RANGE":
                    dmin, dmax = [float(parts[1])] * 3, [float(parts[2])] * 3
                elif head == "LUT_IN_VIDEO_RANGE":
                    in_video = True
                elif head == "LUT_OUT_VIDEO_RANGE":
                    out_video = True
                continue
            vals = s.split()
            rows.append((float(vals[0]), float(vals[1]), float(vals[2])))
    if not size:
        raise ValueError("%s has no LUT_3D_SIZE line; it is not a 3D .cube file" % path)
    if len(rows) != size ** 3:
        raise ValueError("%s: expected %d rows for LUT_3D_SIZE %d, found %d" % (path, size ** 3, size, len(rows)))
    # .cube order: red changes fastest, so the flat list reshapes to [b, g, r]
    table = np.ascontiguousarray(np.array(rows, dtype=np.float64).reshape(size, size, size, 3).transpose(2, 1, 0, 3))
    return {"size": size, "table": table, "domain_min": np.array(dmin, dtype=np.float64),
            "domain_max": np.array(dmax, dtype=np.float64), "title": title,
            "in_video_range": in_video, "out_video_range": out_video}


def apply_cube(rgb, cube, method="tetrahedral"):
    """Apply a cube from load_cube() to rgb (..., 3). Inputs are clamped to the LUT domain.
    method: "tetrahedral" (Resolve's recommended setting) or "trilinear". Returns float64."""
    if method not in ("tetrahedral", "trilinear"):
        raise ValueError("method must be 'tetrahedral' or 'trilinear', not %r" % (method,))
    x = np.asarray(rgb, dtype=np.float64)
    if x.shape[-1] != 3:
        raise ValueError("apply_cube needs RGB input with a last axis of 3, got shape %s" % (x.shape,))
    if cube.get("in_video_range"):
        x = (64.0 + 876.0 * x) / 1023.0
    t = cube["table"]
    n = t.shape[0]
    lo, hi = cube["domain_min"], cube["domain_max"]
    shape = x.shape
    x = ((np.clip(x, lo, hi) - lo) / (hi - lo) * (n - 1)).reshape(-1, 3)
    if n == 1:
        out = np.broadcast_to(t[0, 0, 0], x.shape).copy()
    else:
        i0 = np.clip(np.floor(x).astype(np.intp), 0, n - 2)
        f = x - i0
        r0, g0, b0 = i0[:, 0], i0[:, 1], i0[:, 2]
        c000 = t[r0, g0, b0]
        c111 = t[r0 + 1, g0 + 1, b0 + 1]
        if method == "trilinear":
            fr, fg, fb = f[:, 0:1], f[:, 1:2], f[:, 2:3]
            c100, c010, c001 = t[r0 + 1, g0, b0], t[r0, g0 + 1, b0], t[r0, g0, b0 + 1]
            c110, c101, c011 = t[r0 + 1, g0 + 1, b0], t[r0 + 1, g0, b0 + 1], t[r0, g0 + 1, b0 + 1]
            c00 = c000 * (1 - fr) + c100 * fr
            c10 = c010 * (1 - fr) + c110 * fr
            c01 = c001 * (1 - fr) + c101 * fr
            c11 = c011 * (1 - fr) + c111 * fr
            c0 = c00 * (1 - fg) + c10 * fg
            c1 = c01 * (1 - fg) + c11 * fg
            out = c0 * (1 - fb) + c1 * fb
        else:
            # tetrahedral: walk from the base corner along the axes in order of decreasing fraction
            order = np.argsort(-f, axis=1, kind="stable")
            w = np.take_along_axis(f, order, axis=1)
            eye = np.eye(3, dtype=np.intp)
            a_off = eye[order[:, 0]]
            b_off = a_off + eye[order[:, 1]]
            ca = t[r0 + a_off[:, 0], g0 + a_off[:, 1], b0 + a_off[:, 2]]
            cb = t[r0 + b_off[:, 0], g0 + b_off[:, 1], b0 + b_off[:, 2]]
            out = (c000 + w[:, 0:1] * (ca - c000) + w[:, 1:2] * (cb - ca) + w[:, 2:3] * (c111 - cb))
    if cube.get("out_video_range"):
        out = (out * 1023.0 - 64.0) / 876.0
    return out.reshape(shape)


_LOCK = threading.Lock()
_LUT_CAMS = {}


def _lut_camera(key):
    path = os.path.abspath(os.path.expanduser(key[4:].strip().strip('"')))
    if not os.path.isfile(path):
        raise CameraKeyError("camera %r: LUT file not found at %s. %s" % (key, path, LUT_ROUTE))
    stamp = (path, os.path.getmtime(path))
    with _LOCK:
        cam = _LUT_CAMS.get(stamp)
    if cam is not None:
        return cam
    try:
        cube = load_cube(path)
    except (OSError, ValueError) as e:
        raise CameraKeyError("camera %r: could not read the LUT (%s). %s" % (key, e, LUT_ROUTE))

    def dec(v, _cube=cube):
        """Vendor LUT to Rec.709, then BT.1886 display linear. Non-RGB input is treated as neutral grey."""
        a = _as_float(v)
        if a.ndim and a.shape[-1] == 3:
            return bt1886(apply_cube(a, _cube))
        rgb = np.repeat(a[..., None], 3, axis=-1)
        return bt1886(apply_cube(rgb, _cube)).mean(axis=-1)

    cam = dict(key=key, decode=dec, matrix=np.eye(3), name="Vendor LUT %s" % os.path.basename(path),
               status="approximate", kind="sdr",
               # the source is still log footage, so the log clip level applies (guess)
               clip_code=0.92, source=path, resolve_name=None, resolve_gamut=None, lut=path)
    with _LOCK:
        _LUT_CAMS[stamp] = cam
    return cam


# ---------------------------------------------------------------- probe


def utf8_stdio():
    """Print clip names in any script (Devanagari, CJK, emoji) even where the console or pipe uses a legacy code
    page (Windows cp1252): stdout and stderr switch to UTF-8, and anything unprintable is replaced, never fatal."""
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def _run(cmd, text=False):
    try:
        # ffprobe writes UTF-8 whatever the system code page is (file names and tags in any script)
        kw = {"encoding": "utf-8", "errors": "replace"} if text else {}
        return subprocess.run(cmd, capture_output=True, **kw)
    except FileNotFoundError:
        env = "RC_FFPROBE" if cmd[0] == FFPROBE else "RC_FFMPEG"
        raise RuntimeError("%s was not found. Install ffmpeg (it includes ffprobe) or set %s to the full path "
                           "of the program." % (cmd[0], env))


RAW_EXTENSIONS = {".braw", ".r3d", ".ari", ".arx", ".crm", ".nev", ".dng"}
_TAG_KEEP = re.compile(r"make|model|manufacturer|encoder|handler_name|brand|software|firmware|gamma|colou?r|"
                       r"profile|vendor|dji|gopro|samsung|android", re.I)
_TAG_DROP = re.compile(r"serial|location|gps|iso6709|uuid|identifier|owner|author|artist|comment|title|copyright|"
                       r"creation|date|time|keyword|description|user", re.I)
_PIXFMT_BITS = re.compile(r"p(\d{1,2})(le|be)?$")


def _num(x, default=0.0):
    try:
        if isinstance(x, str) and "/" in x:
            a, b = x.split("/", 1)
            return float(a) / float(b) if float(b) else default
        return float(x)
    except (TypeError, ValueError):
        return default


def _bits_from(pix_fmt, raw_bits):
    try:
        b = int(raw_bits)
        if b > 0:
            return b
    except (TypeError, ValueError):
        pass
    if not pix_fmt or pix_fmt == "unknown":
        return 0
    if pix_fmt.startswith(("p010", "p210", "p410", "y210", "x2rgb10", "x2bgr10", "v210")):
        return 10
    if pix_fmt.startswith(("p016", "p216", "p416", "rgb48", "bgr48", "rgba64", "bgra64")):
        return 16
    m = _PIXFMT_BITS.search(pix_fmt)
    return int(m.group(1)) if m else 8


def _chroma_from(pix_fmt):
    p = pix_fmt or ""
    if p.startswith(("gbr", "rgb", "bgr", "argb", "abgr", "rgba", "bgra", "x2rgb", "x2bgr", "0rgb", "rgb0")):
        return "rgb"
    if "420" in p or p.startswith(("nv12", "nv21", "p010", "p016")):
        return "420"
    if "422" in p or p.startswith(("nv16", "p210", "p216", "y210", "uyvy", "yuyv", "yvyu", "v210")):
        return "422"
    if "444" in p or p.startswith(("nv24", "p410", "p416", "ayuv", "vuya", "v410", "xv30", "xv36")):
        return "444"
    return "other"


def _display_matrix(side):
    """(rotation 0/90/180/270 counter-clockwise, mirrored, odd angle) from a Display Matrix side data entry."""
    try:
        r = float(side.get("rotation", 0) or 0)
    except (TypeError, ValueError):
        r = 0.0
    mirrored = False
    dm = side.get("displaymatrix")
    if isinstance(dm, str):
        vals = []
        for line in dm.strip().splitlines():
            if ":" in line:
                vals += [int(x) for x in line.split(":", 1)[1].split()]
        if len(vals) >= 5:
            mirrored = vals[0] * vals[4] - vals[1] * vals[3] < 0
    rot = int(round(r)) % 360
    odd = rot % 90 != 0
    return (rot if not odd else 0), mirrored, odd


def _empty_probe(path):
    return {"path": path, "codec": "unknown", "profile": "unknown", "pix_fmt": "unknown", "bits": 0,
            "chroma": "other", "coded_w": 0, "coded_h": 0, "width": 0, "height": 0, "rotation": 0,
            "color_range": "unknown", "color_space": "unknown", "color_transfer": "unknown",
            "color_primaries": "unknown", "fps": 0.0, "duration": 0.0,
            "is_raw": os.path.splitext(str(path))[1].lower() in RAW_EXTENSIONS, "make": None, "model": None,
            # extra keys (not in the plan's list, used by extract_frame and detect)
            "stream_index": None, "chroma_location": "unspecified", "mirrored": False, "odd_rotation": False,
            "format_name": "unknown", "codec_tag": "", "tags": {}, "dovi": None, "side_data": []}


def probe(path):
    """One ffprobe call. Never raises for a readable file (missing fields become "unknown", 0 or None).

    Keys: path, codec, profile, pix_fmt, bits, chroma ("420" | "422" | "444" | "rgb" | "other"),
    coded_w, coded_h (stored picture size), width, height (display orientation, after rotation),
    rotation (0 | 90 | 180 | 270: counter-clockwise rotation to apply to the stored picture, the same
    sign as ffprobe's rotation value), color_range ("pc" | "tv" | "unknown"), color_space,
    color_transfer, color_primaries, fps, duration, is_raw, make, model (no serial numbers).
    Extra keys: stream_index, chroma_location, mirrored, odd_rotation, format_name, codec_tag, tags
    (a filtered set of maker tags), dovi, side_data.
    """
    path = str(path)
    if not os.path.exists(path):
        raise FileNotFoundError("no such file: %s" % path)
    info = _empty_probe(path)
    r = _run([FFPROBE, "-v", "error", "-show_streams", "-show_format", "-of", "json", path], text=True)
    try:
        d = json.loads(r.stdout or "{}")
    except ValueError:
        d = {}
    fmt = d.get("format") or {}
    streams = d.get("streams") or []
    vids = [s for s in streams if s.get("codec_type") == "video"]
    vs = next((s for s in vids if not (s.get("disposition") or {}).get("attached_pic")), vids[0] if vids else {})
    info["format_name"] = fmt.get("format_name") or "unknown"
    tags = {}
    for src in (vs.get("tags") or {}, fmt.get("tags") or {}):
        for k, v in src.items():
            kl = str(k).lower()
            if _TAG_KEEP.search(kl) and not _TAG_DROP.search(kl):
                tags[kl] = str(v).strip()[:200]
    info["tags"] = tags

    def first(*keys):
        for k in keys:
            if tags.get(k):
                return tags[k]
        return None
    info["make"] = first("com.apple.quicktime.make", "make", "com.android.manufacturer", "manufacturer")
    info["model"] = first("com.apple.quicktime.model", "model", "com.android.model")
    info["duration"] = _num(vs.get("duration"), 0.0) or _num(fmt.get("duration"), 0.0)
    if not vs:
        return info
    pix = vs.get("pix_fmt") or "unknown"
    info.update(stream_index=vs.get("index"), codec=vs.get("codec_name") or "unknown",
                profile=vs.get("profile") or "unknown", pix_fmt=pix, codec_tag=vs.get("codec_tag_string") or "",
                chroma_location=vs.get("chroma_location") or "unspecified")
    info["bits"] = _bits_from(pix, vs.get("bits_per_raw_sample"))
    info["chroma"] = _chroma_from(pix)
    for k in ("color_range", "color_space", "color_transfer", "color_primaries"):
        val = vs.get(k)
        info[k] = str(val) if val and val != "unspecified" else "unknown"
    if info["color_range"] not in ("pc", "tv"):
        info["color_range"] = "pc" if pix.startswith("yuvj") else "unknown"
    w, h = int(vs.get("width") or 0), int(vs.get("height") or 0)
    rot, mirrored, odd = 0, False, False
    sides = []
    for sd in vs.get("side_data_list") or []:
        t = sd.get("side_data_type", "")
        sides.append(t)
        if "display matrix" in t.lower() or "rotation" in sd:
            rot, mirrored, odd = _display_matrix(sd)
        if "dovi" in t.lower():
            info["dovi"] = {"profile": sd.get("dv_profile"), "compat_id": sd.get("dv_bl_signal_compatibility_id")}
    if not rot and (vs.get("tags") or {}).get("rotate"):   # old ffprobe builds
        rot = int(_num(vs["tags"]["rotate"], 0)) % 360
        rot = (360 - rot) % 360 if rot % 90 == 0 else 0     # the old tag was clockwise
    info.update(side_data=sides, rotation=rot, mirrored=mirrored, odd_rotation=odd, coded_w=w, coded_h=h)
    info["width"], info["height"] = (h, w) if rot in (90, 270) else (w, h)
    fps = _num(vs.get("avg_frame_rate"), 0.0) or _num(vs.get("r_frame_rate"), 0.0)
    info["fps"] = round(fps, 4)
    codec, tag = info["codec"].lower(), info["codec_tag"].lower()
    if ("raw" in codec and codec.startswith("prores")) or tag in ("aprn", "aprh") or codec in ("r3d", "braw", "arriraw"):
        info["is_raw"] = True
    return info


# ---------------------------------------------------------------- detection

SONY_GAMMA = {"s-log3-cine": "slog3", "s-log3": "slog3", "s-log2": "slog2_sg",
              "hlg": "hlg", "hlg1": "hlg", "hlg2": "hlg", "hlg3": "hlg", "rec2100-hlg": "hlg",
              "rec709": "rec709", "rec709-xvycc": "rec709", "s-cinetone": "rec709", "still": "rec709",
              "cine1": "rec709", "cine2": "rec709", "cine3": "rec709", "cine4": "rec709"}
DJI_GAMMA = {"d-log": "dlog_dgamut", "dlog": "dlog_dgamut", "d-log2": "dlog2_dgamut2", "dlog2": "dlog2_dgamut2",
             "d-log m": "dlogm", "d-logm": "dlogm", "dlog-m": "dlogm", "dlogm": "dlogm", "dlog m": "dlogm",
             "rec.2100 hlg": "hlg_2020", "hlg": "hlg_2020", "rec.709": "rec709", "rec709": "rec709",
             "normal": "rec709"}
_UNTAGGED = ("unknown", "unspecified", "", "reserved")
_SDR_TRC = ("bt709", "smpte170m", "bt470bg", "bt470m", "bt2020-10", "bt2020-12", "gamma22", "gamma28", "bt1361e",
            "iec61966-2-4", "smpte240m")
_IMAGE_CODECS = ("png", "bmp", "tiff", "webp", "gif", "jpegls", "jpeg2000")

# vendor: (make substrings, byte markers that may appear in the file's head or tail)
VENDORS = [
    ("apple", ("apple",), ()),
    ("samsung", ("samsung",), (b"samsung", b"SAMSUNG")),
    ("dji", ("dji",), (b"com.dji.",)),
    ("gopro", ("gopro",), (b"GoPro",)),
    ("insta360", ("insta360", "arashi"), (b"Insta360",)),
    ("nikon", ("nikon",), (b"NIKON",)),
    ("canon", ("canon",), (b"Canon",)),
    ("fujifilm", ("fujifilm",), (b"FUJIFILM",)),
    ("panasonic", ("panasonic",), (b"Panasonic",)),
    ("leica", ("leica",), (b"LEICA", b"Leica Camera")),
    ("sony", ("sony",), ()),
    ("blackmagic", ("blackmagic",), (b"Blackmagic",)),
    ("zcam", ("z cam", "zcam", "z-cam"), (b"Z CAM",)),
    ("xiaomi", ("xiaomi", "redmi"), ()),
    ("oppo", ("oppo",), ()),
    ("vivo", ("vivo",), ()),
]
_LOG_VENDORS = {"sony", "panasonic", "fujifilm", "canon", "nikon", "dji", "gopro", "blackmagic", "leica", "zcam",
                "insta360"}

# 10-bit clips with no transfer tag, per vendor: (key, confidence, note)
VENDOR_LOG = {
    "samsung": ("samsung_log", "medium", ""),
    "insta360": ("ilog", "medium", "Insta360 changed the I-Log curve in July 2025; very old clips may differ."),
    "nikon": ("nlog", "medium", ""),
    "gopro": ("gplog", "low", "GoPro HERO12 and newer record GP-Log, which has no published formula. If this clip "
              "was recorded with GoPro Labs GP-Log2, set the camera to gplog2 instead."),
    "canon": (None, "low", "Canon log clip: Canon Log 3 is most common. Pick clog3_cg (Cinema Gamut) or "
              "clog3_2020 (BT.2020 color matrix) from the camera's menu setting."),
    "fujifilm": ("flog2_fgamut", "low", "Fujifilm log clip: could also be F-Log (flog_fgamut) or F-Log2 C "
                 "(flog2c_fgamutc). Check the camera's film simulation setting."),
    "panasonic": ("vlog_vgamut", "low", "Panasonic 10-bit clip without tags: V-Log is a guess."),
    "leica": ("llog_2020", "low", "Leica 10-bit clip without tags: L-Log is a guess."),
    "blackmagic": ("bmd_gen5", "low", "Blackmagic camera file: Film Gen 5 is a guess (older bodies use "
                   "earlier Film curves)."),
    "zcam": ("zlog2", "low", ""),
    "xiaomi": ("milog", "low", ""),
    "oppo": ("olog", "low", ""),
    "vivo": ("vivolog", "low", ""),
}

_NAME_HINTS = [
    (re.compile(r"^C\d{4}\.(mp4|mxf)$", re.I), "sony", "file name looks like a Sony camera clip (C0001.MP4)"),
    (re.compile(r"^DJI_", re.I), "dji", "file name looks like a DJI clip (DJI_...)"),
    (re.compile(r"^G[HX]\d{6}\.mp4$", re.I), "gopro", "file name looks like a GoPro clip (GX01xxxx.MP4)"),
    (re.compile(r"^IMG_\d{4}\.mov$", re.I), "apple", "file name looks like an iPhone clip (IMG_xxxx.MOV)"),
    (re.compile(r"^DSCF\d{4}\.mov$", re.I), "fujifilm", "file name looks like a Fujifilm clip (DSCFxxxx.MOV)"),
    (re.compile(r"^P\d{7}\.(mov|mp4)$", re.I), "panasonic", "file name looks like a Panasonic clip (Pxxxxxxx.MOV)"),
    (re.compile(r"^[A-Z]\d{3}C\d{3}_", re.I), None, "file name looks like a cinema camera clip (A001C001_...)"),
]


def _matrix_name(tag):
    t = (tag or "").lower()
    if t.startswith("bt2020"):
        return "bt2020"
    if t in ("smpte170m", "bt470bg", "bt601", "fcc"):
        return "bt601"
    return "bt709"


def _vendor(p, blob):
    """(vendor name or None, evidence) from maker tags first, then byte markers in the file."""
    fields = " ".join(str(x) for x in [p.get("make"), p.get("model")] + list((p.get("tags") or {}).values()) if x)
    fields = fields.lower()
    for name, makes, _marks in VENDORS:
        if any(m in fields for m in makes):
            return name, "maker tag says %s" % name
    for name, _makes, marks in VENDORS:
        if any(m in blob for m in marks):
            return name, "file contains %s metadata" % name
    return None, ""


def _attr(xml, pattern):
    a = re.search(pattern, xml)
    return a.group(1).decode("ascii", "replace").strip() if a else ""


def _sony_xml(xml):
    """(gamma, primaries, coding, manufacturer, model) from Sony NonRealTimeMeta XML bytes, or None.
    The Device element also holds the serial number, which is never read."""
    gamma = _attr(xml, rb'CaptureGammaEquation"\s+value="([^"]+)"').lower()
    if not gamma:
        return None
    dev = re.search(rb"<Device\b[^>]*>", xml)
    dev = dev.group(0) if dev else b""
    return (gamma, _attr(xml, rb'CaptureColorPrimaries"\s+value="([^"]+)"').lower(),
            _attr(xml, rb'CodingEquations"\s+value="([^"]+)"').lower(),
            _attr(dev, rb'manufacturer="([^"]*)"'), _attr(dev, rb'modelName="([^"]*)"'))


def _dji_gamma(p, blob):
    for k, v in (p.get("tags") or {}).items():
        if "colorgamma" in k or k.endswith("gammasxs"):
            return v.strip().lower(), "maker tag %s=%s" % (k, v.strip())
    m = re.search(rb"ColorGammaSxS.{0,64}?(D-Log2|D-Log M|D-LogM|D-Log|Rec\.2100 HLG|Rec\.709|HLG|Normal)", blob, re.S)
    if m:
        g = m.group(1).decode("ascii", "replace")
        return g.lower(), "DJI ColorGammaSxS=%s" % g
    return None, ""


def _detect_from(p, blob=b"", sidecar_xml=None):
    """Pure detection rules. p = probe() dict, blob = bytes from the head and tail of the file,
    sidecar_xml = bytes of a Sony M01.XML sidecar or None. Returns the detect() dict."""
    blob = blob or b""
    path = str(p.get("path") or "")
    trc = (p.get("color_transfer") or "unknown").lower()
    prim = (p.get("color_primaries") or "unknown").lower()
    csp = (p.get("color_space") or "unknown").lower()
    rng = p.get("color_range") or "unknown"
    bits = int(p.get("bits") or 0)
    model_l = (p.get("model") or "").lower()
    ev = ["ffprobe codec=%s pix_fmt=%s bits=%d transfer=%s primaries=%s matrix=%s range=%s"
          % (p.get("codec"), p.get("pix_fmt"), bits, trc, prim, csp, rng)]
    if p.get("make") or p.get("model"):
        ev.append("make=%s model=%s" % (p.get("make") or "-", p.get("model") or "-"))
    res = dict(key=None, confidence="low", status="unknown", kind=None, evidence=ev, color_range=rng,
               yuv_matrix=_matrix_name(csp), make=p.get("make"), model=p.get("model"), note="")

    def done(key, confidence, note=""):
        res["key"], res["confidence"] = key, confidence
        if key in CAMERAS:
            res["status"], res["kind"] = CAMERAS[key]["status"], CAMERAS[key]["kind"]
            if CAMERAS[key]["kind"] == "sdr" and not note:
                note = "Display-referred video (not log): graded without the filmic tone curve."
        elif key in UNSUPPORTED:
            res["status"], res["kind"] = "unsupported", "log"
            note = ("%s has no published formula, so the lab cannot decode it by itself. %s %s"
                    % (UNSUPPORTED[key], LUT_ROUTE, note)).strip()
        else:
            res["status"], res["kind"] = "unknown", None
        if confidence == "low" and key in CAMERAS:
            note = (note + " This is a guess: look at a frame and confirm the camera and picture profile with "
                    "the user, then set camera_overrides if it is wrong.").strip()
        res["note"] = note
        return res

    # 0. RAW and files without a video stream
    if p.get("is_raw"):
        ev.append("RAW file (extension or codec)")
        res.update(key=None, confidence="high", status="unsupported", kind=None,
                   note="RAW is decoded by Resolve; the lab cannot read it. Render these clips to a log "
                        "ProRes or DNxHR file first, or grade them by hand.")
        return res
    if not p.get("codec") or p.get("codec") == "unknown":
        ev.append("no video stream found")
        return done(None, "low", "No video stream: check the file.")

    # 1. Sony: NonRealTimeMeta XML inside the file (trailing meta box) or the M01.XML sidecar
    sony = _sony_xml(blob) or (_sony_xml(sidecar_xml) if sidecar_xml else None)
    if sony:
        g, prims, coding, maker, model = sony
        ev.append("Sony XML gamma=%s primaries=%s coding=%s" % (g, prims or "-", coding or "-"))
        if model:
            res["model"] = model
            res["make"] = maker or "Sony"
            ev.append("Sony XML device model=%s" % model)
        if csp in _UNTAGGED and coding:
            res["yuv_matrix"] = "bt2020" if "2020" in coding else ("bt601" if "601" in coding else "bt709")
        kind = SONY_GAMMA.get(g)
        if kind == "slog3":
            cine = "cine" in prims or (not prims and "cine" in g)
            return done("slog3_sg3c" if cine else "slog3_sg3", "high")
        if kind == "slog2_sg":
            return done("slog2_sg", "high")
        if kind == "hlg":
            return done("hlg_709" if "709" in prims else "hlg_2020", "high")
        if kind == "rec709":
            note = "" if g.startswith("rec709") else ("Sony picture profile %s has its own display curve; graded as "
                                                      "Rec.709 video." % g)
            return done("rec2020_sdr" if "2020" in prims else "rec709", "high" if not note else "medium", note)
        ev.append("Sony gamma value not in the table")

    vendor, vev = _vendor(p, blob)
    if vendor:
        ev.append(vev)

    # 2. DJI: QuickTime key com.dji.camera.ColorGammaSxS (DJI tags Rec.709 in colr even for log)
    g, gev = _dji_gamma(p, blob)
    if g:
        ev.append(gev)
        key = DJI_GAMMA.get(g)
        if key:
            return done(key, "high")
    if vendor == "dji" and bits >= 10 and trc not in ("arib-std-b67", "smpte2084"):
        ev.append("DJI 10-bit clip without a color profile tag")
        return done(None, "low", "DJI 10-bit clip without a color profile tag: it is probably D-Log M (no "
                                 "published formula; use DJI's official LUT via lut:<path>) or D-Log "
                                 "(dlog_dgamut). Ask the user which profile was used.")

    # 3. Standard HDR signalling
    if trc == "arib-std-b67":
        dv = p.get("dovi")
        note = ""
        if dv:
            ev.append("Dolby Vision profile %s compatibility id %s" % (dv.get("profile"), dv.get("compat_id")))
            note = "Dolby Vision with an HLG base layer: the lab grades the HLG base layer."
        return done("hlg_709" if prim == "bt709" else "hlg_2020", "high", note)
    if trc == "smpte2084":
        return done("pq_2020", "high", "PQ (HDR10) video: display light, tone mapped to SDR by the lab.")

    # 4. Still images
    if (p.get("codec") or "").lower() in _IMAGE_CODECS or "image2" in (p.get("format_name") or "") \
            or (p.get("format_name") or "").endswith("_pipe"):
        return done("p3_display" if prim == "smpte432" else "srgb", "medium",
                    "Still image: treated as sRGB display-referred.")

    # 5. 10-bit or more with no transfer tag: some vendor log
    wide = prim == "bt2020" or csp.startswith("bt2020")
    if bits >= 10 and trc in _UNTAGGED:
        if vendor == "apple":
            if re.search(r"iphone (1[7-9]|[2-9]\d)", model_l):
                return done("applelog2", "medium", "Apple Log 2 is assumed for iPhone 17 and newer.")
            if model_l.startswith("iphone"):
                return done("applelog", "medium" if wide else "low")
            return done("applelog", "low")
        if vendor in VENDOR_LOG:
            key, conf, note = VENDOR_LOG[vendor]
            if vendor == "canon":
                key = "clog3_2020" if wide else "clog3_cg"
            if not wide and conf == "medium":
                conf = "low"
            return done(key, conf, note)
        if vendor == "sony":
            return done("slog3_sg3c", "low", "Sony 10-bit clip without Sony metadata: S-Log3 / S-Gamut3.Cine is "
                                             "a guess.")
        for rx, hint_vendor, text in _NAME_HINTS:
            if rx.search(os.path.basename(path)):
                ev.append(text + " (weak hint)")
                if hint_vendor == "sony":
                    return done("slog3_sg3c", "low", "The Sony metadata was not found; S-Log3 / S-Gamut3.Cine is a "
                                                     "guess from the file name.")
                break
        if wide:
            ev.append("BT.2020 10-bit with no transfer tag: some vendor log")
        return done(None, "low", "10-bit video without a transfer tag, probably log from a camera the lab could not "
                                 "identify. Look at a frame and ask the user which camera and profile they used.")

    # 6. GoPro without log tags
    if vendor == "gopro":
        return done("rec709", "low", "GoPro clip: the Protune color profile is not readable here. If it was "
                                     "shot in GP-Log, use GoPro's LUT via lut:<path>.")

    # 7. Display-referred SDR
    if trc in _SDR_TRC or trc == "iec61966-2-1" or bits == 8 or (bits and trc in _UNTAGGED):
        if trc == "iec61966-2-1":
            key = "p3_display" if prim == "smpte432" else "srgb"
        elif prim == "bt2020":
            key = "rec2020_sdr"
        elif prim == "smpte432":
            key = "p3_display"
        else:
            key = "rec709"
        conf = "medium"
        note = ""
        if bits >= 10 and vendor in _LOG_VENDORS:
            conf = "low"
            note = ("10-bit clip from a camera that can record log, tagged as Rec.709: check the picture "
                    "profile.")
        if trc in _UNTAGGED:
            conf = "low"
            ev.append("no transfer tag")
        for rx, _v, text in _NAME_HINTS:
            if rx.search(os.path.basename(path)):
                ev.append(text + " (weak hint)")
                break
        return done(key, conf, note)

    ev.append("No decisive metadata. File names can help (C0001 = Sony, DJI_ = DJI, GX/GH = GoPro, "
              "A001C001 = cinema cameras); log footage looks flat with grey near 0.35 to 0.55.")
    return done(None, "low", "Could not tell the format from the file. Look at a frame and ask the user.")


def _scan(path, head=2 << 20, tail=6 << 20):
    """Head and tail bytes of the file: vendor XML and QuickTime keys live in moov or a trailing meta box."""
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        a = f.read(min(head, size))
        f.seek(max(len(a), size - tail))
        b = f.read()
    return a + b


def _sidecar(path):
    base = os.path.splitext(path)[0]
    for cand in (base + "M01.XML", base + "M01.xml"):
        if os.path.isfile(cand):
            try:
                with open(cand, "rb") as f:
                    return f.read(1 << 20)
            except OSError:
                return None
    return None


def detect(path, probe_info=None):
    """Guess the camera format of one file.

    Returns {key, confidence ("high" | "medium" | "low"), status ("verified" | "official" | "approximate" |
    "display" | "unsupported" | "unknown"), kind ("log" | "hdr" | "sdr" | None), evidence (list of strings,
    never serial numbers), color_range, yuv_matrix ("bt709" | "bt2020" | "bt601"), make, model, note}.
    "high" means explicit maker metadata, "medium" color tags plus make, "low" a guess to confirm by eye.
    """
    path = str(path)
    p = probe_info if probe_info else probe(path)
    blob = b""
    try:
        blob = _scan(path)
    except OSError:
        pass
    return _detect_from(p, blob, _sidecar(path))


# ---------------------------------------------------------------- frame extraction

KR_KB = {"bt709": (0.2126, 0.0722), "bt601": (0.299, 0.114), "bt2020": (0.2627, 0.0593),
         "smpte240m": (0.212, 0.087), "fcc": (0.30, 0.11)}
_MATRIX_ALIASES = {"bt2020nc": "bt2020", "bt2020c": "bt2020", "smpte170m": "bt601", "bt470bg": "bt601",
                   "bt470": "bt601", "rec709": "bt709", "rec2020": "bt2020", "rec601": "bt601", "709": "bt709",
                   "2020": "bt2020", "601": "bt601"}
_YUV_FMT = re.compile(r"^(yuv|yuvj|yuva)(420|422|444)p(\d{1,2})?(le)?$")
_GBR_FMT = re.compile(r"^gbr(a)?p(\d{1,2})?(le)?$")
_warned = set()


def _warn_once(path, msg):
    with _LOCK:
        if path in _warned:
            return
        _warned.add(path)
    sys.stderr.write("cameras: %s: %s\n" % (os.path.basename(path), msg))


def _layout(pix_fmt):
    """Planar layout the raw decoder can read, or None (then the swscale fallback is used)."""
    m = _YUV_FMT.match(pix_fmt or "")
    if m:
        fam, sub, bits, le = m.groups()
        bits = int(bits) if bits else 8
        if (bits > 8 and not le) or not 8 <= bits <= 16:
            return None
        sx, sy = {"420": (2, 2), "422": (2, 1), "444": (1, 1)}[sub]
        return {"rgb": False, "bits": bits, "sx": sx, "sy": sy, "alpha": fam == "yuva", "jpeg": fam == "yuvj"}
    m = _GBR_FMT.match(pix_fmt or "")
    if m:
        alpha, bits, le = m.groups()
        bits = int(bits) if bits else 8
        if (bits > 8 and not le) or not 8 <= bits <= 16:
            return None
        return {"rgb": True, "bits": bits, "sx": 1, "sy": 1, "alpha": bool(alpha), "jpeg": False}
    return None


def _norm_matrix(name):
    n = (name or "").strip().lower()
    n = _MATRIX_ALIASES.get(n, n)
    if n not in KR_KB:
        raise ValueError("unknown YCbCr matrix %r (use bt709, bt601 or bt2020)" % (name,))
    return n


def _chroma_zero_code(chroma_zero, bits, full):
    cz = chroma_zero if chroma_zero is not None else os.environ.get("RC_CHROMA_ZERO", "resolve")
    if isinstance(cz, str):
        s = cz.strip().lower()
        if s in ("", "resolve"):
            return (2 ** bits - 1) / 2.0 if full else float(2 ** (bits - 1))
        if s == "center":
            return float(2 ** (bits - 1))
        try:
            return float(s)
        except ValueError:
            raise ValueError("chroma_zero must be 'resolve', 'center' or a code value, not %r" % (cz,))
    return float(cz)


def _up(C, n, axis, cosited):
    """Linear 2x chroma upsample along axis to length n. cosited: sample j sits on luma 2j,
    otherwise halfway between luma 2j and 2j+1."""
    C = np.moveaxis(C, axis, 0)
    nxt = np.concatenate([C[1:], C[-1:]], 0)
    shape = (C.shape[0] * 2,) + C.shape[1:]
    o = np.empty(shape, dtype=C.dtype)
    if cosited:
        o[0::2] = C
        o[1::2] = 0.5 * (C + nxt)
    else:
        prv = np.concatenate([C[:1], C[:-1]], 0)
        o[0::2] = 0.75 * C + 0.25 * prv
        o[1::2] = 0.75 * C + 0.25 * nxt
    return np.moveaxis(o[:n], 0, axis)


def _area(img, w, h):
    """Area average (h0, w0, 3) -> (h, w, 3) float64: integer box when exact, PIL BOX otherwise."""
    H, W = img.shape[:2]
    if (H, W) == (h, w):
        return img.astype(np.float64)
    if H % h == 0 and W % w == 0:
        fy, fx = H // h, W // w
        return img.reshape(h, fy, w, fx, img.shape[2]).mean(axis=(1, 3), dtype=np.float64)
    from PIL import Image
    flt = Image.BOX if (w <= W and h <= H) else Image.BILINEAR
    return np.stack([np.asarray(Image.fromarray(np.ascontiguousarray(img[..., c], dtype=np.float32), "F")
                                .resize((w, h), flt), dtype=np.float64) for c in range(img.shape[2])], -1)


def _swscale(path, sec, w, h, yuv_matrix="bt709", in_range=None):
    vf = "scale=%d:%d:flags=area:in_color_matrix=%s:out_range=full" % (w, h, yuv_matrix)
    if in_range:
        vf += ":in_range=%s" % in_range
    raw = _run([FFMPEG, "-v", "error", "-ss", "%.4f" % max(sec, 0), "-i", path, "-frames:v", "1",
                "-vf", vf, "-f", "rawvideo", "-pix_fmt", "rgb48le", "-"]).stdout
    if len(raw) != w * h * 6:
        raise RuntimeError("ffmpeg failed for %s at %.3fs" % (path, sec))
    return (np.frombuffer(raw, "<u2").reshape(h, w, 3) / 65535.0).astype(np.float32)


def extract_frame_swscale(path, sec, w, h, yuv_matrix="bt709"):
    """The v1 lab's extraction through ffmpeg's swscale (bit-identical to the old grade_lab.extract()
    with yuv_matrix="bt709"). Used for legacy caches and as the fallback for unusual pixel formats."""
    return _swscale(path, sec, w, h, yuv_matrix)


def extract_frame(path, sec, w, h, data_level="auto", yuv_matrix=None, chroma_zero=None, probe_info=None):
    """One frame as float32 (h, w, 3) RGB in Resolve's internal 0..1 data, like Resolve decodes it.

    sec: time in the file (seek like v1: "-ss sec" before "-i"). The picture is rotated to display
    orientation, area-averaged to w x h and clipped to 0..1.
    data_level: "auto" (follow the file's range flag, like Resolve's Auto), "video" or "full".
    yuv_matrix: None (from the file's tag, BT.709 when untagged), "bt709", "bt601" or "bt2020".
    chroma_zero: None (env RC_CHROMA_ZERO, else "resolve"), "resolve" ((2^n-1)/2 for full range,
      2^(n-1) for video range), "center" (2^(n-1)) or a code value at the file's bit depth.
    Planar YUV 8 to 16 bit 4:2:0 / 4:2:2 / 4:4:4 and planar RGB are decoded from the raw samples with
    numpy; anything else falls back to extract_frame_swscale (one warning per file on stderr).
    """
    path = str(path)
    p = probe_info
    if not p or p.get("pix_fmt") in (None, "unknown") or not p.get("coded_w") or not p.get("coded_h"):
        p = probe(path)
    lay = _layout(p.get("pix_fmt"))
    dl = (data_level or "auto").strip().lower()
    if dl not in ("auto", "video", "full"):
        raise ValueError("data_level must be auto, video or full, not %r" % (data_level,))
    mat = _norm_matrix(yuv_matrix if yuv_matrix else _matrix_name(p.get("color_space")))
    rot = int(p.get("rotation") or 0) % 360
    if lay is None or p.get("mirrored") or p.get("odd_rotation") or rot % 90:
        why = ("pixel format %s" % p.get("pix_fmt")) if lay is None else "mirrored or unusual rotation"
        _warn_once(path, "%s is not read by the Resolve-like decoder; using ffmpeg's conversion instead "
                         "(small color differences to Resolve are possible)" % why)
        in_range = {"video": "tv", "full": "pc"}.get(dl)
        return _swscale(path, sec, w, h, mat, in_range)
    if dl == "auto":
        full = lay["rgb"] or lay["jpeg"] or p.get("color_range") == "pc"
        if lay["rgb"] and p.get("color_range") == "tv":
            full = False
    else:
        full = dl == "full"
    W, H = int(p.get("coded_w") or 0), int(p.get("coded_h") or 0)
    if not W or not H:
        raise RuntimeError("probe found no picture size for %s" % path)
    bits, sx, sy = lay["bits"], lay["sx"], lay["sy"]
    cw, ch = -(-W // sx), -(-H // sy)
    n_y, n_c = W * H, cw * ch
    planes = [n_y, n_c, n_c] + ([n_y] if lay["alpha"] else [])
    bps = 1 if bits <= 8 else 2
    cmd = [FFMPEG, "-v", "error", "-noautorotate", "-ss", "%.4f" % max(sec, 0), "-i", path]
    if p.get("stream_index") is not None:
        cmd += ["-map", "0:%d" % int(p["stream_index"])]
    cmd += ["-frames:v", "1", "-f", "rawvideo", "-pix_fmt", p["pix_fmt"], "-"]
    r = _run(cmd)
    raw = r.stdout
    if len(raw) != sum(planes) * bps:
        err = (r.stderr or b"").decode("utf-8", "replace").strip().splitlines()[-1:]
        raise RuntimeError("ffmpeg failed for %s at %.3fs%s" % (path, sec, (": " + err[0]) if err else ""))
    a = np.frombuffer(raw, np.uint8 if bps == 1 else "<u2")
    P0 = a[:n_y].reshape(H, W).astype(np.float32)
    P1 = a[n_y:n_y + n_c].reshape(ch, cw).astype(np.float32)
    P2 = a[n_y + n_c:n_y + 2 * n_c].reshape(ch, cw).astype(np.float32)
    if lay["rgb"]:
        img = np.stack([P2, P0, P1], -1)            # planar order is G, B, R
    else:
        loc = (p.get("chroma_location") or "unspecified").lower()
        if sx == 2:
            cos = loc in ("left", "topleft", "bottomleft", "unspecified")
            P1, P2 = _up(P1, W, 1, cos), _up(P2, W, 1, cos)
        if sy == 2:
            cos = loc in ("topleft", "top")
            P1, P2 = _up(P1, H, 0, cos), _up(P2, H, 0, cos)
        img = np.stack([P0, P1, P2], -1)
    del P0, P1, P2
    if rot:
        img = np.rot90(img, k=rot // 90)
    img = _area(img, w, h)
    M = float(2 ** bits - 1)
    s = 2.0 ** (bits - 8)
    if lay["rgb"]:
        rgb = img / M if full else (img - 16.0 * s) / (219.0 * s)
    else:
        z = _chroma_zero_code(chroma_zero, bits, full)
        if full:
            y, cb, cr = img[..., 0] / M, (img[..., 1] - z) / M, (img[..., 2] - z) / M
        else:
            y = (img[..., 0] - 16.0 * s) / (219.0 * s)
            cb, cr = (img[..., 1] - z) / (224.0 * s), (img[..., 2] - z) / (224.0 * s)
        kr, kb = KR_KB[mat]
        r_ = y + 2 * (1 - kr) * cr
        b_ = y + 2 * (1 - kb) * cb
        g_ = (y - kr * r_ - kb * b_) / (1 - kr - kb)
        rgb = np.stack([r_, g_, b_], -1)
    return np.clip(rgb, 0.0, 1.0).astype(np.float32)


# ---------------------------------------------------------------- self test

WP_CODES = {
    # key: [(reflectance, 10-bit code value)] from the manufacturer papers (published, rounded)
    "slog3_sg3c": [(0.0, 95), (0.18, 420), (0.90, 598)],
    "vlog_vgamut": [(0.0, 128), (0.18, 433), (0.90, 602)],
    "flog_fgamut": [(0.0, 95), (0.18, 470), (0.90, 705)],
    "flog2_fgamut": [(0.0, 95), (0.18, 400), (0.90, 570)],
    "dlog_dgamut": [(0.0, 95), (0.18, 408), (0.90, 586)],
    "logc3_awg3": [(0.18, 400)],
    "ilog": [(0.0, 93), (0.18, 432)],
    "applelog": [(0.0, 154), (0.18, 500), (0.90, 697)],
    "samsung_log": [(0.18, 540)],
    "gplog2": [(0.18, 554)],
    "dlog2_dgamut2": [(0.18, 312)],
}
WP_FLOATS = {
    # key: [(reflectance, float signal)] where the paper gives more precision
    "applelog": [(0.0, 0.150477), (0.18, 0.488272), (0.90, 0.681686), (12.0, 1.0)],
    "applelog2": [(0.0, 0.150477), (0.18, 0.488272), (0.90, 0.681686), (12.0, 1.0)],
    "ilog": [(0.0, 0.0905593), (0.18, 0.422003), (22.0, 1.0)],
    "di_dwg": [(0.0, 0.0), (0.18, 0.336043), (1.0, 0.513837), (10.0, 0.756599), (100.0, 1.0)],
    "log3g10_rwg": [(0.18, 1 / 3)],
    "hlg_2020": [(0.18, 0.38)],
}
PUBLISHED_TO709 = {
    # matrices printed in the white papers (4 or 6 decimals)
    "vlog_vgamut": [[1.806576, -0.695697, -0.110879], [-0.170090, 1.305955, -0.135865],
                    [-0.025206, -0.154468, 1.179674]],
    "dlog_dgamut": [[1.6746, -0.5797, -0.0949], [-0.0981, 1.3340, -0.2359], [-0.0410, -0.2430, 1.2840]],
}
# D-Gamut2 to XYZ as printed (4 decimals) in DJI's D-Log2 DCTL
DJI_DGAMUT2_XYZ = [[0.6917, 0.1596, 0.0990], [0.2498, 0.8381, -0.0880], [0.0, 0.0, 1.0891]]


def colour_oracles(colour):
    """{key: colour-science decode function} for the formats colour-science implements."""
    M = colour.models
    return {
        "slog3_sg3c": M.log_decoding_SLog3, "slog2_sg": M.log_decoding_SLog2, "vlog_vgamut": M.log_decoding_VLog,
        "flog_fgamut": M.log_decoding_FLog, "flog2_fgamut": M.log_decoding_FLog2, "nlog": M.log_decoding_NLog,
        "dlog_dgamut": M.log_decoding_DJIDLog, "clog_cg": M.log_decoding_CanonLog,
        "clog2_cg": M.log_decoding_CanonLog2, "clog3_cg": M.log_decoding_CanonLog3,
        "logc3_awg3": M.log_decoding_ARRILogC3, "logc4_awg4": M.log_decoding_ARRILogC4,
        "bmd_gen5": M.oetf_inverse_BlackmagicFilmGeneration5, "di_dwg": M.oetf_inverse_DaVinciIntermediate,
        "log3g10_rwg": M.log_decoding_Log3G10, "protune_native": M.log_decoding_Protune,
        "applelog": M.log_decoding_AppleLogProfile, "llog_2020": M.log_decoding_LLog,
        "hlg_2020": lambda x: M.oetf_inverse_BT2100_HLG(x) * HLG_GREY_GAIN,
        "pq_2020": lambda x: M.eotf_ST2084(x) * 0.18 / 26.0,
        "rec709_scene": M.oetf_inverse_BT709,
    }


# camera key -> colour-science RGB colourspace name of its gamut
COLOUR_GAMUTS = {"slog3_sg3c": "S-Gamut3.Cine", "slog3_sg3": "S-Gamut3", "slog2_sg": "S-Gamut",
                 "vlog_vgamut": "V-Gamut", "clog_cg": "Cinema Gamut", "clog2_cg": "Cinema Gamut",
                 "clog3_cg": "Cinema Gamut", "flog2c_fgamutc": "F-Gamut C", "flog_fgamut": "F-Gamut",
                 "nlog": "N-Gamut", "dlog_dgamut": "DJI D-Gamut", "logc3_awg3": "ARRI Wide Gamut 3",
                 "logc4_awg4": "ARRI Wide Gamut 4", "bmd_gen5": "Blackmagic Wide Gamut",
                 "di_dwg": "DaVinci Wide Gamut", "log3g10_rwg": "REDWideGamutRGB",
                 "protune_native": "Protune Native", "applelog": "ITU-R BT.2020", "hlg_2020": "ITU-R BT.2020",
                 "p3_display": "P3-D65", "rec2020_sdr": "ITU-R BT.2020"}


def resolve_lut_dirs():
    """Resolve's system LUT folders for this OS (read only here; the self test compares shaper LUTs)."""
    if sys.platform == "darwin":
        return ["/Library/Application Support/Blackmagic Design/DaVinci Resolve/LUT"]
    if os.name == "nt":
        base = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
        return [os.path.join(base, "Blackmagic Design", "DaVinci Resolve", "Support", "LUT")]
    return ["/opt/resolve/LUT", "/home/resolve/LUT"]


def _load_1d(fn):
    rows = []
    with open(fn, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if line and (line[0].isdigit() or line[0] in "-."):
                rows.append(float(line.split()[0]))
    return np.array(rows)


def _bisect(dec, target, lo=-0.2, hi=1.2):
    for _ in range(80):
        mid = (lo + hi) / 2
        if dec(np.array([mid]))[0] < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def _v1_slog3_to_lin(x):
    """The v1 lab's decoder, kept only to prove bit identity."""
    c = x * 1023.0
    return np.where(c >= 171.2102946929, 10 ** ((c - 420.0) / 261.5) * 0.19 - 0.01,
                    (c - 95.0) * 0.01125 / (171.2102946929 - 95.0))


def _synthetic_probe(**kw):
    p = _empty_probe(kw.pop("path", "/clips/clip.mov"))
    p.update(codec="hevc", pix_fmt="yuv420p10le", bits=10, chroma="420", coded_w=3840, coded_h=2160,
             width=3840, height=2160, fps=30.0, duration=5.0)
    p.update(kw)
    return p


def selftest(verbose=True, use_colour=True):
    """Run every formula check. Returns (number of checks, list of failure messages)."""
    fails = []
    count = [0]

    def check(ok, msg):
        count[0] += 1
        if verbose or not ok:
            print(("ok   " if ok else "FAIL ") + msg)
        if not ok:
            fails.append(msg)

    v = np.linspace(0.0, 1.0, 4097)
    # 1. every entry complete, decoders finite and strictly increasing on 0..1 (needed for the LUT bake)
    for k, c in sorted(CAMERAS.items()):
        ok = (c["key"] == k and c["status"] in ("verified", "official", "approximate", "display")
              and c["kind"] in ("log", "hdr", "sdr") and c["matrix"].shape == (3, 3) and 0.5 < c["clip_code"] < 1.0
              and callable(c["decode"]) and isinstance(c["name"], str) and isinstance(c["source"], str))
        check(ok, "%-16s entry has every field" % k)
        y = c["decode"](v)
        check(bool(np.all(np.isfinite(y)) and np.all(np.diff(y) > 0)), "%-16s finite + strictly increasing on 0..1" % k)
        check(bool(np.allclose(c["matrix"].sum(1), 1.0, atol=2e-3)), "%-16s matrix maps white to white" % k)
    if verbose:
        print("\n18% grey placement (signal value / 10-bit code):")
        for k, c in sorted(CAMERAS.items()):
            g = _bisect(c["decode"], 0.18)
            print("   %-16s %.4f  %6.1f   decode(1.0) = %.2f" % (k, g, g * 1023, c["decode"](np.array([1.0]))[0]))
        print()
    # 2. published code values: the reflectance must fall inside decode(CV +- 0.5)
    for k, pts in WP_CODES.items():
        dec = CAMERAS[k]["decode"]
        for r, cv in pts:
            lo, hi = dec(np.array([(cv - 0.5) / 1023, (cv + 0.5) / 1023]))
            check(bool(lo <= r <= hi), "%-16s %4.0f%% -> CV %d (decoded interval %.5f..%.5f)" % (k, r * 100, cv, lo, hi))
    for k, pts in WP_FLOATS.items():
        dec = CAMERAS[k]["decode"]
        for r, s in pts:
            got = dec(np.array([s]))[0]
            check(bool(abs(got - r) <= max(2e-4, 2e-4 * abs(r))), "%-16s signal %.6f -> %.6f (paper %.4f)" % (k, s, got, r))
    # 3. published matrices and the v1 matrix
    for k, ref in PUBLISHED_TO709.items():
        err = np.abs(CAMERAS[k]["matrix"] - np.array(ref)).max()
        check(bool(err < 6e-4), "%-16s matrix == white paper (max abs err %.1e)" % (k, err))
    err = np.abs(npm(*PRIMS["dgamut2"]) - np.array(DJI_DGAMUT2_XYZ)).max()
    check(bool(err < 2e-3), "dgamut2          NPM from rounded primaries vs DJI DCTL matrix (max abs err %.1e)" % err)
    err = np.abs(to709("sgamut3cine") - SGAMUT3CINE_TO_709_V1).max()
    check(bool(err < 1e-6), "slog3_sg3c       computed S-Gamut3.Cine matrix == v1 verified matrix (max abs err %.1e)" % err)
    check(CAMERAS["slog3_sg3c"]["matrix"] is not None and np.array_equal(CAMERAS["slog3_sg3c"]["matrix"],
                                                                         SGAMUT3CINE_TO_709_V1),
          "slog3_sg3c       uses the v1 matrix constants exactly")
    # 4. v1 bit identity
    for dt in (np.float64, np.float32):
        x = v.astype(dt)
        a, b = _v1_slog3_to_lin(x), get_camera("slog3_sg3c")["decode"](x)
        check(a.dtype == b.dtype and np.array_equal(a, b), "slog3_sg3c       decode == v1 slog3_to_lin exactly (%s)"
              % np.dtype(dt).name)
    # 5. keys, aliases, unsupported formats
    for alias, key in sorted(ALIASES.items()):
        check(get_camera(alias) is CAMERAS[key], "alias %-14s -> %s" % (alias, key))
    for k in sorted(UNSUPPORTED):
        try:
            get_camera(k)
            check(False, "%-16s must raise KeyError" % k)
        except KeyError as e:
            check("lut:" in str(e), "%-16s raises KeyError that explains lut:<path>" % k)
    # 6. detection rules on synthetic metadata
    for name, p, blob, want in _detection_cases():
        got = _detect_from(p, blob, None)
        ok = all(got.get(kk) == vv for kk, vv in want.items()) and not any("serial" in e.lower() for e in got["evidence"])
        check(ok, "detect %-28s -> %s %s %s" % (name, got["key"], got["status"], got["confidence"]))
    # 7. .cube reading and interpolation
    n = 5
    grid = np.linspace(0, 1, n)
    rr, gg, bb = np.meshgrid(grid, grid, grid, indexing="ij")
    table = np.stack([rr ** 0.5, gg ** 2, 0.5 * bb + 0.25 * rr], -1)
    cube = {"size": n, "table": table, "domain_min": np.zeros(3), "domain_max": np.ones(3), "title": "t"}
    pts = np.random.default_rng(1).random((2000, 3))
    ident = np.stack([rr, gg, bb], -1)
    icube = dict(cube, table=ident)
    check(bool(np.abs(apply_cube(pts, icube) - pts).max() < 1e-12), "apply_cube identity cube (tetrahedral)")
    check(bool(np.abs(apply_cube(pts, icube, "trilinear") - pts).max() < 1e-12), "apply_cube identity cube (trilinear)")
    # 8. colour-science oracle
    colour = None
    if use_colour and not os.environ.get("RC_NO_COLOUR"):
        try:
            import warnings
            warnings.filterwarnings("ignore")
            import colour
        except ImportError:
            colour = None
    if colour is None:
        print("colour-science not available: oracle checks skipped")
    else:
        for k, f in colour_oracles(colour).items():
            ours = rec709_scene(v) if k == "rec709_scene" else CAMERAS[k]["decode"](v)
            ref = np.asarray(f(v), dtype=np.float64)
            err = np.max(np.abs(ours - ref) / np.maximum(1.0, np.abs(ref)))
            check(bool(err < 1e-6), "%-16s decode == colour-science (max rel err %.1e)" % (k, err))
        r709 = colour.RGB_COLOURSPACES["ITU-R BT.709"]
        for k, cs in COLOUR_GAMUTS.items():
            ref = colour.matrix_RGB_to_RGB(colour.RGB_COLOURSPACES[cs], r709, chromatic_adaptation_transform=None)
            err = np.abs(CAMERAS[k]["matrix"] - ref).max()
            check(bool(err < 5e-4), "%-16s matrix == colour %s (max abs err %.1e)" % (k, cs, err))
        ref = colour.algebra.table_interpolation_tetrahedral(pts, table)
        err = np.abs(apply_cube(pts, cube) - ref).max()
        check(bool(err < 1e-9), "apply_cube tetrahedral == colour (max abs err %.1e)" % err)
        ref = colour.algebra.table_interpolation_trilinear(pts, table)
        err = np.abs(apply_cube(pts, cube, "trilinear") - ref).max()
        check(bool(err < 1e-9), "apply_cube trilinear == colour (max abs err %.1e)" % err)
    # 9. Resolve's own shaper LUTs (read only) show Resolve's input convention
    for root in resolve_lut_dirs():
        if not os.path.isdir(root):
            continue
        tests = [("Samsung/Samsung Log to Linear.cube", samsung_log, 1.0, 1e-5),
                 ("VFX IO/ARRI LogC to Linear.cube", logc3, 1.0, 2e-6),
                 ("HDR Hybrid Log-Gamma/HLG to Gamma 1.0.cube", hlg, 12.0 / HLG_GREY_GAIN, 1e-6),
                 ("VFX IO/sLog2 to Linear.cube", slog2, 1.0 / 0.9, 1e-6)]
        for fn, dec, scale, tol in tests:
            fp = os.path.join(root, fn)
            if not os.path.exists(fp):
                continue
            lut = _load_1d(fp)
            x = np.linspace(0, 1, len(lut))
            err = np.max(np.abs(dec(x) * scale - lut) / np.maximum(1.0, np.abs(lut)))
            check(bool(err < tol), "Resolve LUT %-44s == our decode x %.4g (max rel err %.1e)" % (fn, scale, err))
        break
    print("\n%d checks, %d failure(s)" % (count[0], len(fails)))
    return count[0], fails


def _detection_cases():
    """(name, probe dict, blob, expected fields) used by the self test and the unit tests."""
    sony_xml = (b'<Device manufacturer="Sony" modelName="ILME-FX3" serialNo="1234567"/>'
                b'<Item name="CaptureGammaEquation" value="s-log3-cine"/>'
                b'<Item name="CaptureColorPrimaries" value="s-gamut3-cine"/>'
                b'<Item name="CodingEquations" value="rec709"/>')
    return [
        ("sony xml s-log3-cine", _synthetic_probe(path="/c/C0001.MP4", codec="h264", pix_fmt="yuv422p10le",
                                                  color_range="pc"), sony_xml,
         dict(key="slog3_sg3c", confidence="high", status="verified", kind="log", model="ILME-FX3")),
        ("iphone hlg dolby vision", _synthetic_probe(path="/c/IMG_0001.MOV", color_transfer="arib-std-b67",
                                                     color_primaries="bt2020", color_space="bt2020nc",
                                                     color_range="tv", make="Apple", model="iPhone 16 Pro",
                                                     dovi={"profile": 8, "compat_id": 4}), b"",
         dict(key="hlg_2020", confidence="high", kind="hdr", yuv_matrix="bt2020")),
        ("dji d-log m blob", _synthetic_probe(path="/c/DJI_0001.MP4", color_transfer="bt709",
                                              color_primaries="bt709", color_space="bt709"),
         b"\x00com.dji.camera.ColorGammaSxS\x00\x00\x00D-Log M\x00",
         dict(key="dlogm", status="unsupported", kind="log", confidence="high")),
        ("dji d-log tag", _synthetic_probe(path="/c/DJI_0002.MP4", tags={"com.dji.camera.colorgammasxs": "D-Log"}),
         b"", dict(key="dlog_dgamut", status="verified", confidence="high")),
        ("rec709 8-bit", _synthetic_probe(path="/c/clip.mp4", codec="h264", pix_fmt="yuv420p", bits=8,
                                          color_transfer="bt709", color_primaries="bt709", color_space="bt709",
                                          color_range="tv"), b"",
         dict(key="rec709", status="display", kind="sdr")),
        ("apple log untagged transfer", _synthetic_probe(path="/c/IMG_0002.MOV", color_primaries="bt2020",
                                                         color_space="bt2020nc", color_range="tv", make="Apple",
                                                         model="iPhone 15 Pro"), b"",
         dict(key="applelog", status="verified", kind="log", yuv_matrix="bt2020")),
        ("apple log 2 by model", _synthetic_probe(color_primaries="bt2020", color_space="bt2020nc", make="Apple",
                                                  model="iPhone 17 Pro"), b"",
         dict(key="applelog2", status="official")),
        ("raw extension", _synthetic_probe(path="/c/A001_C001.braw", is_raw=True), b"",
         dict(key=None, status="unsupported", kind=None)),
        ("pq hdr10", _synthetic_probe(color_transfer="smpte2084", color_primaries="bt2020", color_space="bt2020nc"),
         b"", dict(key="pq_2020", status="display", kind="hdr")),
        ("samsung log", _synthetic_probe(color_primaries="bt2020", color_space="bt2020nc", make="samsung"), b"",
         dict(key="samsung_log", status="official", confidence="medium")),
        ("gopro gp-log", _synthetic_probe(path="/c/GX010001.MP4", color_primaries="bt2020", color_space="bt2020nc",
                                          tags={"handler_name": "GoPro H.265"}), b"",
         dict(key="gplog", status="unsupported")),
        ("unknown 10-bit", _synthetic_probe(path="/c/clip.mov"), b"", dict(key=None, status="unknown")),
    ]


def _print_table():
    print("%-16s %-9s %-4s %-29s %s" % ("key", "status", "kind", "Resolve gamma", "name"))
    for r in camera_table():
        print("%-16s %-9s %-4s %-29s %s" % (r["key"], r["status"], r["kind"], r["resolve_name"] or "-", r["name"]))
    print("\nNot supported directly (no published formula): %s" % ", ".join(
        "%s (%s)" % (k, n) for k, n in sorted(UNSUPPORTED.items())))
    print(LUT_ROUTE)
    print("\nAliases: %s" % ", ".join("%s -> %s" % kv for kv in sorted(ALIASES.items())))


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "help"
    if cmd == "list":
        _print_table()
        return 0
    if cmd == "detect":
        if len(argv) < 3:
            print("usage: python cameras.py detect FILE [FILE...]")
            return 2
        for fp in argv[2:]:
            try:
                r = detect(fp)
            except (OSError, RuntimeError) as e:
                print("%s -> error: %s" % (os.path.basename(fp), e))
                continue
            print("%s -> %s (%s confidence, %s) range=%s yuv=%s"
                  % (os.path.basename(fp), r["key"], r["confidence"], r["status"], r["color_range"], r["yuv_matrix"]))
            for e in r["evidence"]:
                print("    " + e)
            if r["note"]:
                print("    note: " + r["note"])
        return 0
    if cmd == "selftest":
        n, fails = selftest(verbose="-q" not in argv)
        return 1 if fails else 0
    print(__doc__)
    print("usage: python cameras.py list | detect FILE... | selftest [-q]")
    return 0 if cmd in ("help", "-h", "--help") else 2


if __name__ == "__main__":
    utf8_stdio()
    sys.exit(main(sys.argv))
