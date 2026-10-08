# <Project name>: color grade brief

Fill every <...>. Write "unknown" rather than guessing. Every agent reads this file first.

## The piece
- Project: <what it is, who it is for, the feeling it should leave>
- Platform and deliverable: <Instagram Reels / TikTok / YouTube Shorts / YouTube / client master / ...>,
  <aspect, resolution, fps>. Uploaded from <a phone | a desktop>. <SDR (default) | HDR, and who checks the SDR version>
- Layout: <single frame | N panels on screen at once (split screen, stacked) | picture in picture | ...>
- Content: <subjects, locations, time of day, lighting (daylight, tungsten, LED, neon, mixed), key props,
  product and brand colors that must stay true>

## People and skin
- Skin groups in frame: <for example "one East Asian man, one Black woman", or "no people">
- Natural skin offset to aim for (`skin_hue_offset_deg` in metrics.json): about 0 for white and Black skin,
  +3 to +6 for South and East Asian skin. With several groups, keep each one natural rather than forcing one value.
- Skin target for the grade: `look.skin_target_deg` = <0 for white or Black talent | +3 to +6 (for example +4) for
  South or East Asian talent | 0 to +3 for mixed groups, with look.skin_line_pull 0.2 to 0.35>. The baseline sets
  it and every look keeps it; the skin off flag measures against it.
- Constraints that follow: <dark skin in frame: keep look.black_lift at or below 0.02, no teal in the shadows,
  expose the face so it looks right for the scene | light skin: avoid magenta and pink | none>

## Look
- Mood words: <three to five words, for example "clean, premium, warm, confident">
- References: <films, brands, accounts or frames the user likes, and what they like about them>
- Avoid: <anything the user dislikes: heavy teal and orange, crushed blacks, faded film, ...>
- Texture wanted: <none | subtle grain | halation on highlights | ...> (added by hand after the grade; not baked)
- Tier: <quick | standard | best>. Directions for the look designers: <for example clean, film_print>

## Footage
- Cameras (from `grade_lab.py LAB probe`): <camera key per clip group, confidence, anything approximate>
- Bit depth and codec: <for example 10-bit 4:2:2 H.264>
- Oddities: <mixed cameras, clipped highlights, grades or CST nodes already on clips, clips in other color groups,
  animated panels, speed ramps>

## How the grade reaches Resolve (constraints)
Everything must be expressed as grade_lab params, because it is baked into LUTs:
- node 1 of each clip: its own conversion LUT (33^3 for Sony S-Log3 / S-Gamut3.Cine, 65^3 for other cameras) = camera log decode, camera gamut to Rec.709, per-clip gains
  (auto balance, `match` trims and `clip_overrides`, global trims), exposure, display rendering, Rec.709 2.4 encode.
- the color group's post-clip node: one shared look LUT (65^3) = the `look` section, the same for every clip.
- No windows, tracked or keyframed corrections, qualifier keys that need blur or cleanup, grain, glow or
  sharpening: a LUT cannot hold them. Note them for the hand-over.

## The lab
    PY = <SKILL>/.venv/bin/python   (Windows: <SKILL>/.venv/Scripts/python.exe)
    G  = "PY" "<SKILL>/grade_lab.py" "<LAB>"
    G defaults                                   every parameter with its documentation
    G commands                                   the commands this version has
    G init-params OUT [--preset NAME]            a fresh params file (schema 2)
    G merge BASE OVERLAY [OVERLAY...] OUT        BASE plus small overlay files or preset names
    G render PARAMS OUTDIR                       previews and metrics
    G match PARAMS OUT                           automatic shot matching into clip_overrides
    G wedge PARAMS KEY VALUES OUTDIR             side-by-side variants in one image

Render output: `screens_NN.jpg` (the real screen at every cut, the most important images), `sheet_NN.jpg` (every
clip's middle frame cropped to what is visible), `strips_NN.jpg` (5 frames across each clip), `metrics.json`
(summary, per clip, per slice; glossary in KNOWLEDGE.md), `params_resolved.json`.
Clip keys are `<track>_<timeline start frame>`; match writes each clip's `"uid"` into its clip_overrides entry, keep
it when you edit the entry. The auto baseline is `<LAB>/baseline_auto.json`.

## Known measurements
<paste the notable numbers from the baseline render: flagged clips and why, clipping, casts, skin offsets,
slice neutral spread>

## Rules for every agent
- Work only inside your own folder under `<LAB>/wf/`. Never modify grade_lab.py, cameras.py, the cache or
  timeline.json.
- Do not touch DaVinci Resolve and do not run `luts`, `apply-script`, `grab-script`, `backup-script` or
  `dump-script`. The main session applies the final grade.
- Keep `"schema": 2` in every params file (build them with `init-params` and `merge`).
- Look at the images yourself with the Read tool. Metrics back up what you see; they do not replace it.
