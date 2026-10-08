---
name: resolve-colorist
description: Professional color grade of a DaVinci Resolve Studio timeline, end to end. Reads the timeline, recognises the camera's log format, balances and matches every shot, designs and judges looks, applies the result in Resolve as LUTs after a backup, and verifies it against real Resolve frame grabs. Use when the user asks to color grade, grade, color correct, match shots, fix the colors or give footage a cinematic or film look in DaVinci Resolve.
---

# Resolve colorist

The grade is built in an offline lab (Python, on cached frames) and baked into LUTs: node 1 of every clip gets its
own conversion LUT (camera log decode, white balance, exposure, shot matching, display rendering) and one color
group's post-clip node gets a shared look LUT. Resolve is touched only by small scripts this skill writes into
`LAB/snippets/`, which the user can read. Run every step below; do not skip the gates, the backup or the check.

## Setup (once per session)

- `SKILL` = the folder that contains this SKILL.md (Claude Code shows it as the skill's base directory).
- `PY` = `SKILL/.venv/bin/python` (macOS, Linux) or `SKILL/.venv/Scripts/python.exe` (Windows). If it is missing,
  tell the user and run `bash "SKILL/install.sh"` or `powershell -ExecutionPolicy Bypass -File "SKILL/install.ps1"`
  (it only writes inside SKILL). Below, `G` means `"PY" "SKILL/grade_lab.py"`. Always quote paths; on Windows
  write them with forward slashes.
- `LAB` = `<home>/resolve-colorist-labs/<project>-<timeline>`: keep letters, digits, `-` and `_`, and replace
  every other character (spaces, dots, accents, other scripts) with `_`. When any character had to be replaced,
  append `-` and the first 6 characters of the timeline's uid (step 1), so two timelines whose names differ only in
  those characters get different labs; but when the folder without that suffix already exists (made by an earlier
  version of this skill), use it: the dump refuses a lab that belongs to another timeline (step 2). Before making a
  new lab, run `G find-lab <timeline uid>` (step 1 gives the uid): a renamed timeline or project keeps its uid, and
  the lab it prints holds its earlier grade, backup and FINAL; use that lab. Write `<home>` out as an absolute path.
  Keep the lab after the session: it holds the backup, the final params and the scripts. Commands take it first:
  `G "LAB" <command>`.
- Resolve tools are named by their short names (`get_resolve_status`, `run_script`, `run_script_unsafe`); the
  prefix differs between setups. Some setups list two Resolve servers (for example "DaVinci Resolve" and
  "DaVinci Resolve Studio"): call `get_resolve_status` on each and use the one that reports Resolve running, for
  every Resolve call in the session (when both report it running, pick one and keep it for the whole session).
  Commands that talk to Resolve write a snippet and print a line like
  `RUN run_script_unsafe: exec(open(r"/abs/LAB/snippets/apply_Final.py", encoding="utf-8").read())`.
  Call `run_script_unsafe` with `script` = everything after `RUN run_script_unsafe: ` and `timeout` 60. Several
  RUN lines: one call each, in order. Use `run_script_unsafe` for nothing else. Never write your own Resolve code
  that changes the project; small read-only queries with `run_script` are fine.
- `G commands` lists every lab command and `G defaults` explains every grading parameter.

## Ask the user before

Resetting grades (`RESET`), moving clips between color groups (`MOVE_GROUPS`), applying to a project or timeline
whose name differs from timeline.json (`FORCE`), replacing a color group's own post-clip grade
(`REPLACE_GROUP_LOOK`), grading into a color group that also holds clips of other timelines (`SHARE_GROUP`: their
look changes too), overwriting LUT files of an existing TAG (`luts ... --overwrite`), adding render jobs,
changing any project setting, installing anything outside SKILL. Never switch the color science, never delete the
user's LUTs or grades.

## 0. Setup check
`G doctor`: first line `DOCTOR: OK` or `DOCTOR: PROBLEMS` with fixes. Fix problems before going on.

## 1. Connect
Call `get_resolve_status`. Then `run_script` with
`result = {"product": resolve.GetProductName(), "version": resolve.GetVersionString(), "project": project.GetName(), "timeline": project.GetCurrentTimeline().GetName(), "timeline_uid": project.GetCurrentTimeline().GetUniqueId()}`
(if it fails, ask the user to open the project and the timeline).
Automatic mode needs DaVinci Resolve Studio 21.1 or newer with the AI assistant tools. No Resolve tools, or the
free version: tell the user (README.md, "Connect Resolve") and offer manual mode (bottom of this file).

## 2. Read the timeline
`G "LAB" dump-script`, run the RUN line. It writes `LAB/timeline.json` (clips, layout, existing nodes, groups and
which other timelines use them, color settings). Keys you may set by hand survive a re-dump: `camera`,
`camera_overrides` (their keys move with their clips), `lut_folder` (LUT subfolder, default the project name in
plain letters; the first `luts` records it), `lut_root`, `base_scale`, `group` (the color group, default
`<lut_folder> <timeline> grade`, one per timeline; apply-script records it). If the result is an error with
`gate: other_timeline`, the lab folder already belongs to another timeline: nothing was written; use `LAB-2` (then
`-3`, ...) as LAB for this timeline (after an import or restore of the project, its message says how to carry the
grade over). The result also says what changed since the last dump: clips an edit moved (`moved`, a count), clips
`added` and `removed`, and clips `replaced` at the same place. When clips moved and a params file of an earlier
grade will be used again (FINAL), run `G "LAB" rekey "FINAL" <new file>` right away and use the new file.

## 3. Cameras and gates
`G "LAB" probe` (camera per source file with confidence and evidence), then `G "LAB" check`. The first line is
`RESULT: OK`, `WARN` or `STOP`; details in `LAB/check.json`. On STOP, explain and stop until it is fixed:
- `color_science`: the project uses Color Managed or ACES. Never change it. The user can duplicate the project and
  set DaVinci YRGB in the copy, or grade by hand.
- `timeline_grade`, `input_lut`: a color grade on the Timeline node (anything but texture effects) or clip Input
  LUTs would stack with the baked LUTs; the user removes or disables them.
- `raw_media`: the lab cannot read RAW. The user renders those clips to ProRes or DNxHR in log first.
- `camera_unknown`, `camera_unsupported`: set the camera by hand (below). D-Log M and GP-Log need the maker's
  log-to-Rec.709 LUT: `"camera": "lut:/abs/path/to/vendor.cube"`.
- `lut_root`: the LUT folder is not writable. The user adds a folder of their own under Resolve Preferences >
  System > General (custom LUT location), then set `"lut_root"` in timeline.json or `RESOLVE_LUT_ROOT`.
WARN gates:
- `camera_low_confidence`, `camera_approximate`, `display_referred`: confirm the camera with the user after
  looking at the clips (run step 4, then step 5 and read `LAB/r_baseline/sheet_01.jpg`: the right camera gives a
  normal picture; flat and grey means log treated as normal video, harsh and oversaturated means normal video
  treated as log).
- `node_graph`, `other_group`, `group_preclip`: tell the user now; apply will skip those clips unless they agree.
- `group_postclip`: the color group already has its own post-clip grade or LUT. Tell the user; apply stops rather
  than replace or stack on it unless they agree (`REPLACE_GROUP_LOOK`) or pick another group.
- `group_shared`: the color group also holds clips of other timelines. A group's post-clip look is shared by all
  its clips in every timeline, so grading here would change those timelines too. Tell the user and use the new
  group the message names (`apply-script TAG --group NAME`). Only if the user wants both timelines to share one
  look, set `SHARE_GROUP` in the apply snippet after they agree.
- `node_layers`: clips have a grade on node stack layer 2 or higher, which apply does not see; the user removes
  or disables it, or agrees that it stays.
- `camera_forced`: the camera set in timeline.json differs from a confident detection of the file (often a clip
  replaced or added after the camera was set). Confirm with the user which is right.
- `clip_replaced`: another shot now sits where a graded clip was (or the item points at other media). Its entry in
  clip_overrides was made for the old shot: look at it in the baseline render and reset the entry if it does not
  fit.
- `timeline_texture` or `group_texture` as WARN: Film Look Creator sits after the look; it can also change color,
  so ask the user to keep only its texture parts on.
- `not_studio`: manual mode. `base_scale`, `fractional_fps`: remember for step 9. `schema1`: re-run dump-script.
INFO gates: `replaces_lut` (node 1 of those clips holds a LUT this skill did not make; apply puts the conversion
LUT in its place, so tell the user now), `timeline_texture` and `group_texture` (texture effects after the look,
as step 11 suggests; apply leaves them alone and they show in the grabs), `fusion_comps` (animated or effect
clips; expect low-correlation compare flags there), `clips_moved` (an edit moved clips: their per-clip trims follow
them, and `G "LAB" rekey <params> <out>` stores them under the new keys before anyone edits clip_overrides by
hand), `timeline_color_space` (the file tags; step 10), `many_clips`, `skipped_items`, `probe`, `node_layers`
(re-run dump-script); `tetrahedral` and `project_luts` are asked once at step 8.4.
Set cameras in timeline.json: `"camera": "<key>"` for all clips, or `"camera_overrides": {"<clip key or uid>":
"<key>"}`. Keys: `"PY" "SKILL/cameras.py" list`. Re-run `check` after edits.

## 4. Frames
`G "LAB" cache` (from about 30 s to a few minutes for 17 clips, depending on the computer and how busy it is).
It decodes frames the way Resolve
does (data levels, YCbCr matrix, rotation). Old labs keep their cache; frames of clips that were replaced, slipped
or relinked since they were cached are extracted again (other commands stop with "run cache" until then).

## 5. Auto baseline
`G "LAB" init-params "LAB/auto.json"`, `G "LAB" match "LAB/auto.json" "LAB/baseline_auto.json"`, then
`G "LAB" render "LAB/baseline_auto.json" "LAB/r_baseline"`. Code does the balance (grayness white balance, half-way
auto exposure) and the matching (trims written into `clip_overrides`; match names any clip that kept its previous
trim because the new one would make its skin worse, and any clip whose trim hit the limit; look at those). Look at
`screens_NN.jpg` and read `summary.flagged_clips` in metrics.json; KNOWLEDGE.md explains the flags. Until the brief
sets `look.skin_target_deg`, the `skin off` flags measure against 0, so South or East Asian skin with its natural
+3 to +6 deg can be flagged here; judge those clips by eye.

## 6. Brief
Look at the baseline images, then write `LAB/BRIEF.md` from `SKILL/BRIEF_TEMPLATE.md` (include the flagged
clips, notable numbers and the skin target: `look.skin_target_deg` 0 for white or Black talent, +3 to +6 for South
or East Asian talent, 0 to +3 for mixed groups). Ask in ONE message only what you
cannot see or infer: platform and deliverable, mood or references, and the tier if the user has not chosen:

| tier | how | agents | rough time for 10 to 20 clips |
|---|---|---|---|
| best (default) | Workflow tool, `tier: "best"` | 14 to 17 | 50 to 80 min |
| standard | Workflow tool, `tier: "standard"` | 6 to 7 | 25 to 40 min |
| quick | you, inline | 0 | 10 to 15 min |

Times are estimates; most of it is agents looking at images. Say so, and say that best uses much more of the
user's Claude plan usage than standard or quick (on smaller plans it can hit the usage limit partway). If the user
does not mind, use best.

## 7. Grade
Before you start, tell the user not to edit this timeline (move, add, delete, replace or trim clips) until step 9
is done: edits made meanwhile are not in the grade. apply and grab stop with `timeline_changed` when they notice one.
Standard and best: call the Workflow tool with `scriptPath` = `SKILL/workflow.js` and args
`{lab, skill, py, tier, base: "LAB/baseline_auto.json", directions, max_renders: 8}` (absolute paths, no `~`). Pick
`directions` from the brief (standard uses 2, best 4): product or beauty `clean, film_print, soft_pastel,
warm_editorial`; music, fashion or night `film_print, noir, teal_orange, clean`; food or hospitality
`warm_editorial, clean, film_print, soft_pastel`. If the tool cannot read the script path, pass the file's
text as `script` instead. The result's `final.params_path` is `FINAL`; if `final.render_dir` is empty, render it.
Show the user the final screens and any remaining blocking issues in `verdicts`. Read `verdicts_note` first: when
it says the verdicts come from before the last fix pass (usual in standard), check each blocking issue against
the final render yourself before calling it open or fixed.
- The user wants to choose the look: run with `phase: "design"`, show each candidate's `screens_01.jpg`, then run
  again with `phase: "finish"`, `pick` (the chosen params path) and `notes` (their words).
- No Workflow tool (plan or policy): run the same stages yourself with the Agent tool. The header of workflow.js
  lists the stage order; build each prompt with the same text as its prompt function and run parallel stages as
  parallel Agent calls. No Agent tool either: use quick.

Quick (inline): `G "LAB" wedge "LAB/baseline_auto.json" preset clean_pop,film_print,soft_pastel "LAB/quick/wedge"`
(one image, one row per preset; with dark-skinned talent use moody_dense with shadow_tint [0, 0, 0] in place of soft_pastel, and apply the
dark-skin notes of KNOWLEDGE.md, "Blacks" and "Split toning", to the pick), pick with the user,
`G "LAB" merge "LAB/baseline_auto.json" <preset> "LAB/quick/final.json"`, render, trim with small overlay files and
`merge` (the first overlay sets `look.skin_target_deg` from the brief), stop when the exit criteria in KNOWLEDGE.md
hold. Matching is measured through the look, so once the look is settled run `match` on the current final file
(`G "LAB" match "<current final>.json" "LAB/quick/final_m.json"`) and read its output: when it notes that the look
keeps less chroma than the house look, keep the file you started from unless a clip visibly stands out (KNOWLEDGE.md
section 4). Otherwise render it and keep it if `mean_slice_neutral_spread` drops, `max_slice_neutral_spread` does
not rise, and `max_clipped_pct` does not rise (and is under 1, or trim the clip as KNOWLEDGE.md section 7 says) with
no new `clipped` flag (a warmer trim can clip the red channel on bright warm surfaces; if it does, lower that clip's
stops by about 0.1 in clip_overrides). Look at the screens too. A new `skin off` flag is fine only if match moved
that clip's `skin_hue_offset_deg` by less than about 2 deg (compare the two metrics.json); after a bigger move, set
that clip's temp and tint in clip_overrides back to their values before match (the trim read pink or beige clothing,
wood or a tan wall as a neutral). match measures neutrals only on pixels that were also low-chroma before the look,
and lists clips that kept their previous trim because the new one would make their skin worse and clips whose trim
hit the limit; look at those. Presets: `G presets` or `SKILL/presets/`.

Params files carry `"schema": 2`. A file without it (made by the first version of this skill) renders with the old
defaults, so old grades reproduce exactly; do not add the key to them unless the user wants the new pipeline.

## 8. Apply
1. `G "LAB" luts "FINAL" <TAG>`: per-clip conversion LUTs (33^3 for Sony S-Log3 / S-Gamut3.Cine, 65^3 for other
   cameras) and one look LUT (65^3) in the LUT folder, mapping in `LAB/luts_<TAG>.json`. Use a new TAG for every
   version (Final, Final2, ...): Resolve caches LUTs by file name. `luts` refuses a TAG whose files already exist
   (an applied grade may use them) or that was baked into another folder; pick an unused TAG from its message.
   `--overwrite` only after the user agrees.
2. `G "LAB" backup-script`, run it: timeline exported to `LAB/backup/*.drt` plus a JSON snapshot. If it fails, ask
   the user to export the timeline (File > Export > Timeline) before going on.
3. `G "LAB" apply-script <TAG>` (`--group NAME` to choose the group; it prints the group and records it in
   timeline.json, except a group check reports as shared or holding its own post-clip grade), run it. Report the
   result: `applied` (one `[key, how, ok, grouped]` per clip: `ok` false means node 1's conversion LUT did not land,
   so the clip shows the look over its old or no conversion: run the snippet again, or ask the user to put that
   clip's `<TAG>_Conv_...cube` on node 1 by hand); `not_clean` (clips that already have grades, skipped);
   `in_other_group` (skipped); `unmatched` (clips not found on a timeline without clip ids, or on another copy of it
   with `FORCE`); `error` (nothing was changed): another project or timeline is open (ids are compared, so a renamed
   one is fine); `gate: timeline_changed`: clips were deleted or replaced (`unmatched`) or added (`new_since_dump`)
   since the dump, so re-run dump-script, check and cache, then `rekey` FINAL into a new file (and when clips were
   added or replaced, `match FINAL NEW --only <their keys>` so only they get trims and every other clip keeps its trim), render it and look, `luts` with a NEW TAG, and apply-script
   with that TAG; `gate: group_postclip`: the color group already has its own post-clip grade; `gate: group_shared`:
   the group also holds clips of other timelines. When `applied` is not empty, `look` must be `true`; otherwise the
   look LUT did not land, so ask the user to put `<TAG>_Look.cube` on the group's post-clip node by hand, then grab
   and compare again. For skipped clips or a stop ask the user; only with a yes set `RESET`, `MOVE_GROUPS`, `FORCE`,
   `REPLACE_GROUP_LOOK` or `SHARE_GROUP` to `True` at the top of `LAB/snippets/apply_<TAG>.py` and run it again.
4. Ask the user to check once: Project Settings > Color Management > 3D Lookup Table Interpolation = Tetrahedral
   (not scriptable), and Input and Output Lookup Table empty.

## 9. Verify (mandatory in automatic mode)
`G "LAB" grab-script resolve_<TAG>` and run each RUN line (frame grabs from the Color page, about 1.5 s per frame,
15 frames per snippet; page and playhead are restored). Ask the user not to click in Resolve meanwhile. A snippet
stops after 45 s; if its result lists `not_grabbed`, run grab-script again with the `--chunk` its note gives and run
every new RUN line. A snippet whose result has `gate: timeline_changed` grabbed nothing: clips were added, deleted,
replaced, moved or trimmed since the dump; follow step 8.3 for that gate, then grab again. Texture on the Timeline
node or after the look shows in the grabs (glow and halation lift bright areas, grain lowers the pixel correlation).
Then `G "LAB" compare "FINAL" resolve_<TAG>`. Targets: `mean|dY|` under 0.01 and `mean|dab|` under 1.0 (the second
summary line; a fresh cache reads about 0.5 there, and about 1.0 to 1.1 is fine for a lab whose cache was made by
the first version of this skill). Flags with low pixel correlation are editorial animation (slide-ins, keyframed
zoom or position, transitions; the grab snippets already stopped if the timeline changed since the dump): look at
those grabs and move on. Flags with high correlation are real mismatches: extra nodes, clip attributes, color
management, interpolation, `base_scale`, or a conversion LUT that did not land (`ok` false in the apply result).
With a `base_scale` warning or many low-corr flags on still panels, run `compare ... --calibrate`, set its
`suggested_base_scale` in timeline.json and compare again. Look at the grabs yourself and show the user the key ones
(send the files if your session can, otherwise give the paths).

## 10. Deliver
Give the user these Deliver settings (they add the render job themselves, or you ask first): in the render
settings' Advanced Settings, Color Space Tag Rec.709 and Gamma Tag Rec.709 (Scene) or Rec.709-A, so files are
tagged 1-1-1 without changing a project setting (switching the timeline color space does the same, but it is a
project change, and texture effects such as Halation follow the timeline color space); MP4 H.264 High (or H.265);
1080x1920 for Reels and TikTok; 20 to 40 Mbps; data levels Auto or Video, never Full; no timecode track; Instagram
"Upload at highest quality" and TikTok "Upload HD" on. Macs (and likely iPhones) show Rec.709 exports lighter than
Resolve's 2.4 viewer; judge on a phone, do not bake a fix. On a Mac with "Viewers match QuickTime Player" turned
on, Resolve's own viewer also looks lighter with Rec.709 (Scene); that is expected, do not re-grade for it.
Optional QC of their export: `G "LAB" social-qc <export.mp4>`, then read `LAB/social_qc/qc.txt` and look at
`qc_sheet.jpg` (KNOWLEDGE.md, "Social delivery"). Export check only (no timeline or lab yet): use
`LAB` = `<home>/resolve-colorist-labs/qc-<file name>` and run the same command.

## 11. Hand-over
Tell the user, in plain words:
- the look: what it is, why it fits the brief, what changed from the camera original;
- clips to glance at: flagged clips, compare flags, anything the reviewers left open;
- texture the look wants, added by hand after the look: best on a new node after the look node in the color
  group's post-clip graph (the Timeline node also textures titles and graphics): halation, then diffusion or glow,
  sharpening, vignette (the Vignette effect, not a window with a gain change), grain last; keep grain subtle for
  social (KNOWLEDGE.md). Nothing else goes there: a color correction on those nodes would stop the next apply
  (check reports texture-only nodes as INFO and anything else as `timeline_grade` or `group_postclip`);
- delivery settings (step 10);
- where things are: LAB, the LUT folder and `FINAL`;
- how to undo: File > Import > Timeline with `LAB/backup/*.drt` restores the old timeline next to the graded one;
  or remove the node 1 LUTs and the group's post-clip look LUT. The LUT files can stay.
- how to change it later: ask for a tweak; you re-dump, check, `cache` (it only extracts what changed), `rekey`
  FINAL into a new file when check reports `clips_moved`, or `match FINAL NEW --only <keys>` for the clips the dump
  reports as `added` or `replaced` since the grade (new clips otherwise get only the auto balance; never re-match the
  whole FINAL for this, it re-trims clips that were already right), edit params, bake with a new TAG and
  apply again (the texture nodes stay as they are). A clip_overrides entry copied to another clip needs a `rekey`
  afterwards, which tags the copy with its own clip.
- a second timeline in the same project gets its own lab and its own color group, so grading it leaves this one
  alone.

## Manual mode (free Resolve, or no Resolve tools)
Free Resolve 21.1 has no scripting, so nothing can be applied or verified automatically, and check cannot see the
project. First ask the user to confirm three things in Resolve (step 0 of manual mode): Project Settings > Color
Management > Color science is DaVinci YRGB (not Color Managed or ACES); no clip has an Input LUT (Clip Attributes);
the Timeline node has no grade. If one is not true, the same rules as the `color_science`, `input_lut` and
`timeline_grade` gates in step 3 apply. Then `G "LAB" init-manual
<media files>` builds a simple timeline.json (one clip after another, fill), then run steps 3 to 7
as usual. Bake with `G "LAB" luts "FINAL" <TAG> --out <a folder the user picks>`, then `G "LAB" apply-script <TAG>`:
in manual mode it prints the written steps (and saves them to `LAB/apply_<TAG>_manual.txt`) instead of a snippet.
Give the user those steps: add that folder as a LUT location (Preferences > System > General) or copy it into the
LUT folder, put each `<TAG>_Conv_<clip key>_<clip name>.cube` on node 1 of that clip, put all clips in one new color
group (not one that holds clips of another timeline) and the `<TAG>_Look.cube` on the group's post-clip node, set
Tetrahedral interpolation. Say the result is unverified.

## Troubleshooting
- Resolve tools time out or are missing: Resolve Preferences > System > General > External scripting using:
  Local; File > Setup AI Assistants; restart Claude Code.
- A snippet fails: read its error, fix the cause (wrong timeline open, page, missing clip), run it again. Do not
  edit snippets other than the flags at their top.
- One clip is off in compare with high correlation: check its node 1 in Resolve for a hand correction. apply counts
  a clip as clean when node 1 is the only node and its tool list shows nothing but a LUT; a primary correction that
  the tool list does not report would stay under the LUT.
- `cache` fails on a clip: ffmpeg cannot read it (RAW, missing media); check shows which.
- Lab and Resolve disagree everywhere by the same amount: interpolation, a project Input or Output LUT, or clip
  data levels changed in Resolve after the dump. Re-dump and compare again.
- More in README.md, "Troubleshooting".
