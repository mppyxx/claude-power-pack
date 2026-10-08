---
name: resolve-editor
description: Edits video in DaVinci Resolve Studio like a careful editor with an assistant team. Logs the footage (shots, transcript words, pauses, fillers, beats, loudness, contact sheets), plans the story, cuts competing versions offline, judges them against measured craft rules, then builds the approved version as a NEW Resolve timeline and verifies it. Use when the user asks to edit, cut, make a rough cut, a highlight, a reel, a short or an ad, trim the pauses or fillers, cut to music, or make captions from footage in DaVinci Resolve, including punch-ins, speed ramps, transitions and animated captions or titles.
---

# Resolve editor

The edit is made in an offline lab (Python and ffmpeg, on small proxies of the footage) as an edit decision list
(EDL, JSON). Agents write cut lists in transcript words, shot ids and beats; code does the frame math, renders a
preview, and measures every cut (clipped words, flash frames, beat offsets, caption timing, safe zones, loudness).
Resolve is touched only after the user approves a version, and only by small scripts this skill writes into
`LAB/snippets/`, which the user can read. The approved version becomes a NEW timeline in the skill's own bin; the
user's timelines are never changed. Run every step below; do not skip the gates, the backup or the verify.

## Setup (once per session)

- `SKILL` = the folder that contains this SKILL.md (Claude Code shows it as the skill's base directory).
- `PY` = `SKILL/.venv/bin/python` (macOS, Linux) or `SKILL/.venv/Scripts/python.exe` (Windows). If it is missing,
  tell the user and run `bash "SKILL/install.sh"` or `powershell -ExecutionPolicy Bypass -File "SKILL/install.ps1"`
  (it only writes inside SKILL). Below, `M` means `"PY" "SKILL/media_lab.py"` (footage analysis) and `E` means
  `"PY" "SKILL/edit_lab.py"` (edit, preview, checks, Resolve scripts). Always quote paths; on Windows write them
  with forward slashes. Write `<home>` out as an absolute path, never `~`. The lines assume a bash shell; when
  your shell is PowerShell, start each one with the call operator: `& "PY" "SKILL/edit_lab.py" ...`.
- `LAB` = `<home>/resolve-editor-labs/<edit name>` (the `RE_LABS_ROOT` environment variable changes the root).
  Pick a short edit name from what the user asked for (for example `studio-reel` or `podcast-ep12-clip`); keep
  letters, digits, `-` and `_`, and replace every other character with `_`. If that folder exists and its
  `project.json` belongs to another project or piece, use `<name>-2` (then `-3`). For a revision of an earlier edit,
  reuse its lab: it holds the analysis, the versions and the backup. Commands take the lab first:
  `M "LAB" <command>`, `E "LAB" <command>`. Keep the lab after the session.
- Resolve tools are named by their short names (`get_resolve_status`, `run_script`, `run_script_unsafe`); the
  prefix differs between setups. Some setups list two Resolve servers (for example "DaVinci Resolve" and
  "DaVinci Resolve Studio"): call `get_resolve_status` on each and use the one that reports Resolve running, for
  every Resolve call in the session. Commands that talk to Resolve write a snippet and print a line like
  `RUN run_script_unsafe: exec(open(r"/abs/LAB/snippets/build_v001_01.py", encoding="utf-8").read())` (the build
  writes `build_v001_01.py`, `build_v001_02.py` and so on, then `build_v001_gfx.py`, `build_v001_fin.py` and, when
  the edit has effects, `build_v001_fx.py`).
  Call `run_script_unsafe` with `script` = everything after `RUN run_script_unsafe: ` and `timeout` 60. Several
  RUN lines: one call each, in order. Use `run_script_unsafe` for nothing else. Never write your own Resolve code
  that changes the project; small read-only queries with `run_script` are fine.
- `M commands` and `E commands` list the commands this version has (one per line). `E schema cutlist` (also
  `edl`, `outline`, `preset`, `checks`) prints an annotated example; `E presets` lists the presets.
- Output conventions: gate commands print `RESULT: OK`, `RESULT: WARN` or `RESULT: STOP` first, then one line per
  finding; producing commands print `WROTE <path>`; exit code 2 with `ERROR: ...` means the command itself failed
  (the message says how to fix it).

## Ask the user before

Building anything in Resolve (only after they approve a version), importing media into their project
(`--import-missing`; asked once with the brief, step 3), building into a project or timeline other than the one read at the start (`FORCE`), running
Resolve's own transcription (it writes into the project; this skill transcribes locally instead), adding render
jobs, downloading a whisper model (tell them the size), installing anything outside SKILL. Never change project
settings, never delete or edit the user's timelines, clips or bins, never switch the Resolve page yourself
(the build's new timeline opens on the Edit page by itself) or move the playhead, never upload or publish anything.

## 0. Setup check
`M doctor` and `E doctor`: first line `DOCTOR: OK` or `DOCTOR: PROBLEMS` with fixes. Fix problems before going on.
A missing whisper engine is only a warning: without it there is no transcript, so word-based editing and captions
are off and pauses are found from the audio level alone. Ask the user in chat whether you may download the
whisper model (small is 466 MB, base 142 MB); with a yes run `bash "SKILL/install.sh" --asr small --yes` (on
Windows `-Asr small -Yes`). Without `--yes` the installer cannot ask you anything from Claude's shell, so it
only prints what it would download and exits with an error. On a Mac `brew install whisper-cpp` adds the engine.

## 1. Connect and choose the mode
Call `get_resolve_status`. Then `run_script` (read only) with:
`tl = project.GetCurrentTimeline() if project else None; result = {"product": resolve.GetProductName(), "version": resolve.GetVersionString(), "studio": resolve.IsStudio(), "page": resolve.GetCurrentPage(), "project": project.GetName() if project else None, "timeline": tl.GetName() if tl else None, "timeline_uid": tl.GetUniqueId() if tl else None, "fps": tl.GetSetting("timelineFrameRate") if tl else None}`

| mode | when | what happens |
|---|---|---|
| full | Resolve Studio 21.1 or newer running, the Resolve tools connected, `studio` true | read, edit offline, back up, build a new timeline with its stills, titles and captions, verify, delivery settings |
| handoff | the free version, or no Resolve tools | edit offline from files the user names, then a timeline file and an SRT they import (bottom of this file); unverified |
| offline | no Resolve at all | the same as handoff; the preview is only a low-resolution check, not a master |

If the call fails, ask the user to open the project (and a timeline if the footage is on one). `Untitled Project`
must be saved under a name first. Tell the user which mode you will use and why.

## 2. Lab and footage
1. `E "LAB" init --name "<edit name>" --preset <preset id>` creates LAB and `project.json` (`--fps`, `--size WxH`,
   `--start-tc` only when there is no Resolve to read them from). Presets (`E presets`, KNOWLEDGE.md section 11):
   `reels_talking_head`, `reels_montage_music`, `shorts_highlight`, `youtube_long`, `interview_doc`,
   `podcast_clip`, `ad_15`, `ad_30`, `product_demo`, `wedding_highlight`, `music_video`, `travel_montage`. If the
   piece is not clear yet, pick the closest one; you can change it in step 3.
2. Full mode: `E "LAB" dump-script` and run its RUN line; when the footage or music sits in bins, name each one:
   `--bin "Day 1" --bin "Music"` (a bin's own bins count too). It reads the project, the current timeline and its
   media, and the named bins' media (read only) into `LAB/resolve/dump.json`, and records the project's frame rate
   and settings in `project.json`. Its result counts the clips per bin; a name in `bins_not_found` matched no bin
   (ask the user for the exact name). The edit's size: the project's own raster when it has the preset's shape (a
   UHD project gets a UHD edit and timeline), else the preset's (a vertical piece from a 16:9 project is
   1080x1920). New timelines get the project's frame rate, so the edit must run at it: the dump sets it in `project.json` while no version is adopted
   (its result says `edit_fps_changed` when it did). Then run `E "LAB" init --name "<edit name>" --preset <id>`
   once more so the lab settles on the project's rate before any cutting.
3. Register the footage: `M "LAB" add --from-dump "LAB/resolve/dump.json"` and/or `M "LAB" add <files or folders>`
   (video, audio and image files). When the dump named bins, `--from-dump` adds only their media (the current
   timeline may hold anything); add `--with-timeline` when the timeline's clips are footage too. A file that moved
   is found again by its content: `M "LAB" add <its new folder>` keeps its id. Audio files get their beats analysed; for music inside a video file run
   `M "LAB" add --music <file name or media id>` (a media id is the file name without its extension, with
   characters other than letters, digits, `-` and `_` replaced by `_`). Mark a voice-over (narration recorded
   apart from the pictures, or text to speech) with `M "LAB" add --voiceover <media id>`: no `separate_sound` note
   and no beat analysis. Add the brand's logo and wordmark image files too (their media ids go into `brand.json`,
   step 3), and later the user's sound effects (step 3). The output lists each media id, kind, length and any file
   it could not read.

## 3. Brief
Run `M "LAB" estimate` (minutes and disk for the analysis). Then write `LAB/BRIEF.md` from
`SKILL/BRIEF_TEMPLATE.md` with everything you know or can infer, and ask the user in ONE message only what you
cannot, all at once:
- the deliverable and platform, the length, the story or message, must-have and must-avoid moments, music (file,
  rights, feel), captions, people's names for the captions;
- when there is a voice-over: the voice-over script word for word (it corrects the transcript, so no misheard word
  reaches the screen);
- for every brand piece (ads, demos, brand Reels): "What should the viewer do next, and how?" (the route: link in
  bio, URL, phone, the platform's button), the offer or reason to act, word for word, and the end-card copy (the
  name and the call-to-action line, which lock up together on the card);
- what must never be shown (a child or a bystander at a working station, a competitor's label, a messy corner, a
  person who did not agree to appear): it overrides any shot score;
- the loudness target when it is not the presets' default (-14 LUFS, true peak -2 dBTP for social and web files);
  then copy `SKILL/presets/<id>.json` to `LAB/presets/<id>.json` and set `audio.lufs` (and `audio.codec_tp_db`) in
  the copy before the first assemble: the lab's copy wins over the shipped preset, and the checks (`loudness_off`),
  `E "LAB" level` and `deliver-script` all read it;
- the brand kit: the font (family and style, and the font file when it is not installed), the colours as hex, the
  logo mark and the wordmark (image files), the handle or URL, the city, the call-to-action button;
- whether the sound was recorded separately (a lav, a recorder, a podcast interface), whether several cameras filmed
  the same moment, whether it is lip sync to a song;
- effects: "Is this a premium or luxury brand? (yes or no)" (yes moves a preset to the calm premium budget; a music montage or music video to the cinematic montage budget, so it still cuts on the music),
  "How much effects should it have: none, light, normal or bold?" (it scales the preset's effects budget by 0, 0.5,
  1 or 1.25; KNOWLEDGE.md section 15) and "Do you have a folder of your own sound effects (whooshes, hits, pops)?";
- permission to import footage that is not in the project yet into the bin `resolve-editor/<edit name>` (the build
  then runs with `--import-missing`, step 7);
- the tier (table below).
Write the answers into BRIEF.md, the script into `LAB/script.txt`, and the brand kit into `LAB/brand.json` (skip
what the user does not have; the looks fall back to Arial Bold and white):

    {"schema": "resolve-editor/brand@1", "name": "North Studio", "font": {"family": "Montserrat",
     "style": "ExtraBold", "file": null}, "caption_font": null, "colors": {"primary": "#E4572E",
     "text": "#FFFFFF", "dark": "#111111"}, "logo": "<media id>", "wordmark": null, "handle": "@northstudio",
     "url": "northstudio.example", "cta_button": "Book now"}

Sound effects: when the user gave a folder of their own, `M "LAB" add "<folder>"`, then mark its files with
`M "LAB" add --sfx <media id> ...` (no beat analysis; each file's loudest moment is stored so assemble can land it on
the cut). The skill never downloads sound effects; without the user's files the edit has none.

Fit check, before any cutting: when the voice-over's speech time (from `M "LAB" status` or the transcript) is under
about 60 % of an ad's length, or its words are under the playbook's count (KNOWLEDGE.md section 11: 30 to 40 words
for 15 s, 60 to 75 for 30 s), tell the user and offer: a 15 or 20 s ad, a text-led stretch with supers the team writes
(fresh shots and new proof points, never the voice's words again), or a longer voice-over. Write the choice into the
brief; without one the team spreads the voice lines over the whole length with music between them. That spread
reaches at most the speech time plus 0.6 s per gap between lines plus about 1.2 s per musical hit, plus the end
card (KNOWLEDGE.md section 6); when that is short of the length, supers carry the longer gaps (new points from the
brief), and the outline's risks say so. Never stretch a short voice over a long ad silently.

| tier | how | agents | time and usage |
|---|---|---|---|
| best, small | Workflow tool, `tier: "best"`, `size: "small"`: a piece of 30 s or less from at most 2 contact sheets | up to 8 | target 50 to 90 min, about half the usage of full best |
| best, full (default for longer pieces) | Workflow tool, `tier: "best"` | up to 20 (22 for ad presets) | measured 83 to 122 min and 100 to 150 M tokens processed for 15 to 30 s pieces from 16 clips |
| standard | Workflow tool, `tier: "standard"` | up to 8 | 30 to 60 min |
| quick | you, inline | 0 | 15 to 30 min |

Plus the analysis (step 4) and about 10 minutes for backup, build and verify. Times are estimates: most of it is
agents reading transcripts and looking at images, and longer footage or longer pieces take longer. Say so, and say
that best uses much more of the user's Claude plan usage than standard or quick (on smaller plans it can hit the
usage limit partway; the run then stops cleanly and resumes, step 5). Run one best-tier job at a time: three at once
hit the plan limit after about an hour. If the user does not mind, use best. Update the preset in `project.json`
(run `init` again with `--preset`) if the answers change it, and record the effects answers with
`E "LAB" init --name "<edit name>" --preset <id> --premium yes|no --fx none|light|normal|bold` (they default to no
and normal; assemble copies them into the EDL).

Limits of this version to say before cutting when they apply: it plays each clip's own camera sound and cannot sync
separately recorded sound, several camera angles of one moment, or lip-sync takes to a song. With separate sound,
the edit uses the camera sound (or the user syncs the clips in Resolve first and you dump again); with lip-sync
takes, performance shots are used where the mouth is not visible or left for the user to sync. Offer to go ahead on
those terms or stop. Dissolves and fades inside already edited source are not found as shot changes (hard cuts are).
A vertical piece from wider footage gets one fixed framing per shot, from where the shot log places the subject
(no tracking of a subject who moves across the frame). Effects come from the skill's catalogue only (KNOWLEDGE.md
section 15): keyed zooms, speed ramps, transitions, animated captions and titles, and the impact accents the
catalogue enables; text behind a person, emoji, split screens, progress bars and film looks are not built (some are
a manual step at the hand-over).

## 4. Ingest (analysis)
Tell the user the estimate first, and that transcripts, contact sheets and preview frames are sent to Claude as
part of the conversation while the footage itself stays on their computer. Then
`M "LAB" ingest --asr auto` (add `--language en` or another code when known, `--speakers 2` for two speakers). It
makes proxies and WAVs, finds shots, transcribes with word times snapped to the audio, marks pauses and fillers,
analyses music beats, loudness and per-shot quality, and draws contact sheets. It skips work that is already done
for unchanged files, so it is safe to run again. Rough speed on a recent laptop: proxies 2 to 5 times faster than
real time for 4K camera files (much slower for 10-bit 4:2:2 on older computers), transcription 2 to 3 minutes per
hour of speech with whisper small on Apple Silicon (about 7 on a CPU), the rest a few minutes. Long jobs: run it in
the background and check `M "LAB" status`.
Then, when the brief has a voice-over script, align it right away: `M "LAB" script <vo id> --file "LAB/script.txt"`.
The transcript takes the script's spelling with the times kept, words the script lacks become fillers, pieces of
one spoken word whisper split in two are `joined` (kept in the voice, hidden in the captions, one word for every
range and drop; `+` in `M "LAB" transcript <id>`), a unit the script writes as a symbol ("20%", "$49") keeps its spoken word,
and the original words stay in `<id>.words.asr.json`. Speaker labels ("VO:", "MAYA:", "MAYA (V.O.):") and stage
directions ("(beat)", "[MUSIC SWELLS]", "SFX: ...") are optional: the ones the voice reads (a "MYTH: / FACT:" reel)
stay, the others are listed as "not read" and never counted missing; bullets, arrows and emoji never reach a caption,
and each item of a list script (bullets, dashes or "1." opening its lines) ends its own caption.
Read every `extra word` line: each of those words leaves the voice (tell the user when one is a word they want kept:
`"keep": [index]` on the segment). Play every `LISTEN` line it prints (a place where the
transcript and the script have different word counts) and every replaced word marked "they look unlike: play it" (a
one for one fix of two words that look nothing alike), and check the voice says the script. It exits 2 when under 60 % of the words match (a wrong script or a
wrong file): ask the user, and use `--force` only when they confirm. When it lists words the voice skipped
(`script_missing_words`), tell the user. `M "LAB" fix-word <id> <index> "<text>"` fixes one word the user
confirms. Never guess a misheard word yourself.
Then `M "LAB" status`: every media with its analyses and errors. Explain errors in plain words (an unreadable file,
no audio, a variable frame rate phone clip). Count the sheets listed in `LAB/media/sheets/sheets.json`. Explain
the notes and flags ingest prints:
- `separate_sound`: a sound-only file with speech, probably a lav or recorder track. This version cannot sync it;
  the edit uses the camera sound. Use it only as a voice-over (`M "LAB" add --voiceover <id>` removes the note),
  or let the user sync it in Resolve first.
- `multi_track_audio`: Resolve maps the clip's sound as several tracks (a 4 channel camera, two stereo streams).
  Resolve places only the first one, so the preview and the build use it too. When the note says it is nearly
  silent, ask the user to set Clip Attributes > Audio to the channels they want, then dump, add and ingest again.
- `tempo_uncertain`, `bar1_uncertain`, `bpm_name_mismatch` or `loop_bars_off` on a song the edit cuts to: the beat
  grid may be at the wrong tempo, or bar 1 may be off by one to three beats (KNOWLEDGE.md section 7). Make the click
  file, `M "LAB" click <id>` (the music with a soft click on every beat and a high click on every bar 1; it prints
  the path), send it to the user (or give the path) and ask one question: "Does the high click land on beat 1 of
  each bar: yes, it drifts, or N beats early or late?". N beats early or late: `M "LAB" beats --only <id>
  --shift-bar N` (negative for early). It drifts: ask for the tempo and the time of bar 1, then
  `M "LAB" beats --only <id> --bpm <N> --first-downbeat <seconds>`, and check the new `bpm` in `M "LAB" status`.
  No flag: no question. `bpm_from_name`: the file name's tempo was used because the file is a whole number of bars
  at it; that is usually right. When the user is away, go on with the grid as it is (the name's tempo when
  `bpm_from_name`) and list the click file in the hand-over (step 10), so they can check it later.
  The most exact bar-1 time comes from Resolve: park the playhead on the first kick of a bar in the clip's audio
  waveform and read the time. A time typed by ear is often 60 to 100 ms off; that is fine, the grid stays and bar 1
  is the nearest beat. `beats` prints a NOTE whenever the bar line did more than pick bar 1; pass it on and ask the
  user to play the music against the beats:
  - `grid_moved`: the found beats sat half a beat off the bar line (on the off-beats, as with an off-beat bass), so
    the whole grid moved onto it. The note gives the amount in ms.
  - `bar_line_off_grid`: the bar line lies more than about 70 ms from every found beat. The grid stays and bar 1 is
    the nearest beat. If the user is sure the time is exact (read on the waveform, not typed by ear) and the beats
    sound off, run the same command with `--move-grid`: the grid then moves onto the bar line.
- `one_sided`: the clip's stereo track has its sound on one channel, so the voice plays in one ear. Ask the user to
  select the clip in the Media Pool, open Clip Attributes > Audio, set the track's Format to Mono and its Source
  Channel to the channel the note names, then run dump-script, add --from-dump and ingest again. Until then the
  preview and the build play it in one ear, and delivery QC stops a dialogue piece (`channel_balance`).
- `split_channels`: camera sound with speech whose left and right channels hold different microphones (a lav on
  one side, the camera microphone on the other). The same fix, with the channel that sounds better.
- `two_voices`: speech whose left and right channels each carry a different speaker (a two-microphone kit recorded
  in stereo: the guest on one side, the host on the other). Never set it to one channel: that drops a speaker. This
  version cannot put both in the centre: the preview and the build play one speaker in each ear, `check` warns
  (`channel_balance`) and delivery QC stops a dialogue piece. Tell the user before the edit. After the build they
  put those items on an audio track of their own and, in the Fairlight mixer, open that track's Pan window and set
  Spread to 1 (PNT), which plays both channels from the centre; then they render and you run qc.

## 5. Edit
Tell the user the edit happens offline and nothing in Resolve changes until they approve a version.

Standard and best: call the Workflow tool with `scriptPath` = `SKILL/workflow.js` and args
`{lab, skill, py, tier, size, preset, phase, angles, styles, sheets, max_previews: 6, notes, selects, premium, fx}`:
absolute paths, no `~`; `size: "small"` for best on a piece of 30 s or less with at most 2 contact sheets (else leave
it out); `angles` and `styles` from the preset's `workflow` block unless the brief suggests others (angles:
`hook_first`, `emotional_arc`, `chronological`, `problem_solution`, `music_led`; styles: `tight`, `breathing`,
`montage`, `story_first`); `sheets` = the number of sheets in `sheets.json`; `notes` = the user's own words about
the edit; `premium` (true or false) and `fx` (`none`, `light`, `normal` or `bold`) = the brief's effects answers, so
every agent gets the same effects budget. If the tool cannot read the script path, pass the file's text as `script`
instead.
1. First run `phase: "log"`. It returns `selects_path` and `coverage` (each need of the brief with its shots and a
   status ok, weak or missing). Show the user the weak and missing items in a short table with three options: go
   ahead as it is, add pickups (they film or find the shots; add and ingest them, then run phase "log" again), or
   change the brief. Write the decision into the brief's Coverage section.
2. Then run `phase: "all"` with `selects` = the returned `selects_path` (it skips logging the footage again). The
   result's `final.edl_path` is `FINAL`; `final.review_dir` holds `preview.mov`, `report.txt` and the images.
- Show the user `user_summary` and `open_actions` only (at most 5 lines each), not the agents' long summaries.
  `doc_gaps` lists questions the docs left open for the agents: keep them for the skill's maintainer, do not show
  them to the user.
- `final.proven_alternative` (`edl_path`, `what`, `by`): blocking issues remained after the last fix pass, and a
  reviewer proved an EDL that fixes them. Run `E "LAB" diff "FINAL" "<its edl_path>"` and `E "LAB" check "<its
  edl_path>"`; when it changes only what the open issues need and check has no STOP, use it as FINAL (say so).
- The user wants to choose between versions: run with `phase: "design"`, show each candidate's preview and the
  first lines of its report, then run again with `phase: "finish"`, `pick` (the chosen candidate's `edl_path`),
  `notes` (their words) and `selects`.
- Read `verdicts_note` first: when it says the verdicts come from before the last fix pass (usual in standard and
  small), check each blocking issue against the final report yourself before calling it open or fixed.
- STAGE_FAILED (the error of a run whose whole stage returned nothing), or the plan's usage limit: nothing after
  that stage ran. Wait for the limit to reset, then call the Workflow tool again with the same `scriptPath` and
  args plus `resumeFromRunId` (the runId of the stopped run); finished agents come back from the cache in seconds.
  If the session is gone, run `phase: "finish"` with `pick` = the judged winner's EDL and `selects`. Never carry
  an unreviewed fallback to the user as the final.
- Run one best-tier job at a time.
- No Workflow tool (plan or policy): run the same stages yourself with the Agent tool. The header of workflow.js
  lists the stage order; build each prompt with the same text as its prompt function, and run parallel stages as
  parallel Agent calls. No Agent tool either: use quick.

Quick (inline): look at the contact sheets (`LAB/media/sheets/log_NN.jpg`, at most 6) and read the transcripts
(`M "LAB" transcript <id>`; `M "LAB" search "words"` finds lines). For a vertical piece from wider footage the
sheets show the centre crop (dashed cyan lines) and a ruler of tenths: give every segment and overlay whose subject
sits off centre its `"frame_x"` (the subject's centre, 0 to 1 of the source width; KNOWLEDGE.md section 6). Then
write `LAB/wf/quick/cutlist.json` (format: `E schema cutlist` and KNOWLEDGE.md sections 1, 4 to 7, 10, 11 and 15;
every title with its `role`, supers that are the spoken words, `captions.text_fix` only with a spelling from the
script, the brief or its glossary; effects only where they serve a moment, inside the preset's budget, never a
picture effect on a title, no shake, flash, RGB split or glitch on a logo, product or offer shot, and no transition
effect in the first second), `E "LAB" assemble "LAB/wf/quick/cutlist.json" "LAB/wf/quick/v001.json"`,
`E "LAB" review "LAB/wf/quick/v001.json" --tier standard` and read
`LAB/wf/quick/review_v001/report.txt`, `checks.json` and the overview image. Fix what it flags in the cut list,
assemble `v002.json`, review again, and stop when `E "LAB" check` has no STOP and the cut serves the brief.

Loudness before the approval: read `loudness_off` in the final's `checks.json` (`stats.loudness`). The finishing
editor sets the mix gain it names (`"mix": {"gain_db": X}` in the cut list, KNOWLEDGE.md section 9); when it still
fires, write that line into a copy of the cut list yourself, assemble and review it (or, when `E commands` lists
`level`, `E "LAB" level "FINAL" "LAB/wf/level/edl.json"` writes the EDL with the gain and prints the gain and the
residual; put the same line into the cut list so a later assemble keeps it). Plain gain stops at the true peak; what
is left (`residual_lu`) is a Deliver page step for the hand-over.

Show the user: the preview (send the `preview.mov` file if your session can, otherwise give its path; it is a
low-resolution check, not the master), the first six lines of `report.txt` (length, shots, pacing, hook, audio,
checks), the story in two sentences, and any open issues. Ask for changes or approval. Changes: copy the final's
cut list into `LAB/wf/rev_<n>/cutlist.json`, edit it, assemble, review and check (or run the workflow's `finish`
phase with `pick` = FINAL and the user's notes).

## 6. Approve
Nothing touches Resolve before the user says yes to a version. Then
`E "LAB" adopt "FINAL" --by resolve-editor --summary "<one line>"`: it copies the EDL to `LAB/edits/v00N.json`
(printed as `WROTE`). Below, `VER` is that path. Adopted versions are never changed; a later revision becomes
`v00N+1`.

## 7. Back up and build (full mode)
1. `E "LAB" backup-script`, run its RUN line: the current timeline is exported to `LAB/backup/<time>/` as a DRT,
   an OTIO file and a JSON dump. It changes nothing. If it fails, ask the user to export the timeline themselves
   (File > Export > Timeline) before going on.
2. `E "LAB" build-script "VER" --import-missing` when the user allowed the import in step 3 (without that yes,
   leave the flag out); `--name "<timeline name>"` chooses the name. Before any Resolve call it prints how many of
   the EDL's media (images included) are not in the media pool (`NOTE: N of M media are not in the media pool`).
   Run every RUN line in order: the chunk snippets (`build_<ver>_01.py`, `_02.py` and so on: clips and stills),
   then `build_<ver>_gfx.py` (titles and captions as Text+, animated ones keyed on every frame; only when the edit
   has them), then `build_<ver>_fin.py` (transitions from the catalogue including the rebuilt whips and zooms, audio
   cross fades, markers, the summary), then `build_<ver>_fx.py` (only when the edit has effects: the clips' Fusion
   comps with their keyed moves, accents, ramps and Speed Warp, and the clip-pair whips and zooms; it runs last
   because a comp's timing depends on the transitions next to its clip). They build a NEW timeline named
   `<edit name> <version> (resolve-editor)` in the bin `resolve-editor/<edit name>`, plus the graphics timeline
   `<edit name> <version> graphics
   (resolve-editor)` that holds every Text+, and make the new timeline current; a stopped build resumes from its
   build map when you run the remaining lines. `--text markers` makes every title a marker instead of Text+;
   `--captions file` leaves the captions to the SRT (the default follows the preset's `captions.deliver`). Read
   each result:
   - `error: missing_media` (with the paths): some media is not in the media pool and the import was not allowed.
     Nothing was changed. Ask the user; with a yes run `build-script` again with `--import-missing` (the media goes
     into the skill's own bin) and run the new lines. Never edit the snippets.
   - `error: not_studio`, a project name that differs from `project.json`, or a render in progress: nothing was
     changed. Open the right project (or wait); set `FORCE = True` only when the user confirms a renamed project.
   - `error: fps_mismatch` (from `build-script` or a snippet, and a STOP `fps_mismatch` in `check`): the EDL's
     frame rate differs from the project's. Never change the project setting: explain, run `E "LAB" init` again
     (it takes the project's rate from the dump), assemble and review the cut list again, and adopt the result as
     a new version.
   - The graphics snippet's result: `titles` and `captions` built, `refused` (ids Resolve would not take as Text+;
     the finish snippet puts a Cream marker with the text there instead). `more: true` means its time for one run
     was up (a run stops after about 40 s, well inside the 60 s a script may take): run the same RUN line again, it
     goes on where it stopped. The finish snippet refuses to run (`run_gfx_first`) until the graphics are done.
   - The finish snippet's result has `ok` false when anything is off: report `not_placed`, `refused`,
     `length_off` (items Resolve placed at another length; verify will name them), `transitions_refused` (usually
     no handles), `markers_refused`, tail trims, the notes (solid colour items are preview only and leave holes on
     the timeline, named in the notes), `stills`, `text_built` and `text_markers`, and the new timeline's name.
     Keep `timeline_uid`, `graphics_sheet` and `colorist` (the timeline's name and uid for the grading hand-off,
     step 10). Building makes the new timeline current, and Resolve switches to the Edit page when a timeline is
     created; tell the user their own timeline is unchanged and one click away in the timeline list.
   - The effects snippet's result (`build_<ver>_fx.py`): `ok`, `fx_built`, `fx_refused` (`{id, why}`; `why`
     `fx_timebase`: Resolve's comp range for the clip disagrees with the EDL, usually a transition next to it that
     differs from the plan, so nothing was keyed on it; `addtool_failed`: Resolve would not add a tool;
     `keys_failed`: a key did not read back; `item_missing`: the build had refused the clip; `speed_warp_refused`:
     Resolve would not set Speed Warp), `fusion_items` (the EDL ids of the items that now carry a Fusion comp) and
     `fusion_clips` (`{id, clip, track, tc}`: the clip name, track and record timecode of each, the words for the
     hand-over and the colorist, since EDL ids mean nothing in Resolve), `transitions_rebuilt` (the custom whips and
     zooms),
     `transitions_length` (`{id, asked, placed}`: a transition placed shorter than asked, usually short handles, is a
     verify STOP, `transition_length_off`) and `more`. `more: true` means its time for one run was up: run the same
     RUN line again, it skips what is built and goes on. It needs the finish snippet first (it reads the placed
     transitions). Report every refused effect in plain words; never key effects by hand.
   - The finish result also reports what makes the timeline readable: `track_names` (every track named from the
     EDL: "Picture", "Overlays", "Titles", "Titles 2", "Captions", "Dialogue", "Music"; a track the EDL lacks is
     "(empty)"), `labels` (`named`, `coloured`, `refused`: titles "TITLE <id> <text>" in Apricot, captions "CAP <id>
     <text>" in Tan, music pieces "MUSIC <id> <gain> dB" in Teal, stills Purple, clips with comps Orange),
     `handoff` (the Blue "resolve-editor hand-off" marker at the first free frame and the same note in the
     timeline's Comments: what depends on what, the loudness residual, where the lab is) and `loudness`
     (`mix_gain_db`, `preview_lufs`, `residual_lu`). Every clip's volume is its own gain plus the EDL's
     `mix.gain_db`, so the timeline plays the preview's level.
   - The graphics timeline holds the Text+ of every title and caption; the programme shows ranges of it, so
     editing a Text+ there changes the programme. Tell the user not to delete it while the programme uses it.
   - Running the same version's build lines again resumes the same timeline (placed items are skipped, the
     graphics timeline is reused). For a fresh build of a changed edit, adopt it as a new version; it gets its own
     timelines.
   - The skill never places a subtitle clip or makes a subtitle track by script (it crashed Resolve); captions are
     Text+ cues, or the SRT as a file.
   - Music ducking: the build cuts a ducked music item into pieces at the level of each stretch (the level between
     lines, and the ducked level under the voice) and joins them with short Cross Fade 0 dB transitions, so the
     music dips under the voice as in the preview (a ramp deeper than 6 dB is built as steps of at most 6 dB, each
     at least 2 frames long, so a 2 or 3 frame ramp stays one step). Each
     crossfade is added at the incoming piece's start and checked: the finish result lists any transition Resolve
     placed on other frames (`transitions_off`), and verify STOPs on it (`transition_off`, a dropout). Only a
     retimed music item keeps one static level (verify reports `volume_off`); for that case offer the exact ducked
     music as a file: `E "LAB" stem "VER" --track A2 "LAB/deliver/music_ducked.wav"`. The stem already holds the
     music's gain: the user deletes the song clip and puts the stem on the music track at 0 dB clip volume.
   - Clips with several audio tracks (a note names them): each is placed with its first audio track only, as the
     preview played it.
   - The new timeline keeps the project's settings when the edit has the project's size and input sizing (a UHD
     project gives a UHD timeline). Otherwise it gets the edit's size and input sizing as its own timeline
     settings (a vertical piece in a 16:9 project); the project settings stay as they are. The finish result
     lists `settings_refused` (and `ok` is false) if Resolve refused them.

## 8. Verify (mandatory in full mode)
`E "LAB" verify-script "VER"`, run its RUN line (read only), then `E "LAB" verify "VER"`. It compares every item
on the new timeline with the EDL, stills and the text of every title and caption included: RESULT OK or WARN
means the timeline matches. RESULT STOP: tell the user exactly which items differ (`missing`, `extra`, `moved`,
`src_off`, `length_off`, `speed_off`, `fade_off`, `volume_off`, `transition_missing`, `marker_missing`,
`text_missing`, `text_off`, with their times) and where the backup is (`v1_gap` means frames where the EDL has
picture and the timeline has none; `settings_off` means the timeline's size or input sizing differs from the
edit's, so clips are framed differently from the preview). The WARNs `track_name_off`, `item_label_off` and
`handoff_missing` mean a track name, an item's name or colour, or the hand-off marker is missing or changed: run the
finish snippet's RUN line again. `text_marker_only` (WARN): a title became a marker;
the user makes that Text+ by hand. With effects it also reads every clip comp, transition and animated Text+ and
STOPs on `fx_missing` (an effect's tools are not in the comp), `fx_off` (a keyed value differs: a zoom by more than
0.002, a move by more than half a pixel, a source frame by more than 0.01 frame), `fx_timebase` (the comp's range
disagrees with the EDL, so the effects were refused), `retime_off`, `transition_length_off`, `transition_type_off`
and `text_anim_missing` (KNOWLEDGE.md section 14 says what each means). Never repair their project automatically; a
rebuild goes to a new timeline.
Pixels: when `E commands` lists `grab-script`, run `E "LAB" grab-script "VER"` and its RUN line (it only reads:
frame grabs of the built timeline on the Edit page, the playhead put back), then `E "LAB" verify "VER" --grabs`:
it compares each grab with the preview's frame (text boxes masked) and STOPs with `pixels_off` and the worst
frames. Without grabs `pixels_unchecked` stays a WARN: ask the user to play it through once. When no grab can be
read (all failed or deleted) it refuses instead of passing: run grab-script again. An edit with effects needs more
grabs (the peak of every effect, the middle of every transition, the frames around approximate spans) than one run
takes: the grab snippet takes at most 30 frames per run and says `more: true` until all are grabbed, so run the same
RUN line again until `more` is false, then `verify --grabs`. Frames inside an effect are held to that effect's own
threshold (0.95 on a keyed zoom; KNOWLEDGE.md section 15), `aux_off` STOPs where a picture statistic shows an effect
missing, and `fx_pixels_approx` (INFO) lists the spans whose preview is only approximate: look at one grab of each
yourself. A plain `verify` later keeps the
pixel result while the EDL and the readback are unchanged. Because the text boxes
are masked, look yourself at the grab of one title, one caption and the end card (`LAB/verify/<ver>/grabs/
g_<frame>.png`, frame numbers from the timeline start) next to the preview: position, size, line breaks, box and
outline colours. When `E commands` lists `proof-script` and the user agrees to a short render,
`E "LAB" proof-script "VER"` and its RUN line render a half-size proof into `LAB/verify/<ver>/` (the job is deleted
again), and `E "LAB" verify "VER" --proof "LAB/verify/<ver>/proof.mp4"` compares every frame, the length and the
sound: `audio_dropout` STOPs where the render's sound drops out and the preview's mix does not, and
`text_anim_off` STOPs where an animated title or caption in the proof moves, sits or colours differently from the
preview (it names the frames: look at them in both before rebuilding). `text_anim_unchecked` (WARN) names a light
title over a light picture that the compare cannot see: look at those frames of the proof by eye.

## 9. Captions and delivery
- Captions: with `captions.deliver` "burn" (the default for social pieces and ads) they are already Text+ cues on
  the new timeline, styled and placed by the preset's look: nothing to import, nothing to tick on the Deliver page
  (leave Export Subtitle off). With "file" (long form): `E "LAB" export-srt "VER" "LAB/deliver/<edit name>.srt"`
  (it writes every spoken cue, `KIND spoken`), uploaded with the video on the platform; never burn that file in.
- Delivery: ask the user first. When `E commands` lists `deliver-script`: `E "LAB" deliver-script "VER" --platform
  <key>` and its RUN line add a render job on the built timeline with the platform's settings (H.264 master, size
  from the timeline, Rec.709 tags); add `--start` only when the user wants it rendered now. It never sets an
  upload option. Otherwise give the Deliver page settings from KNOWLEDGE.md section 12 as text. Never tick an
  upload option. A result with error `render_settings_refused` made no job (or deleted the one it made): Resolve
  refused or changed the keys it names (`DataBurnIn`, `ExportSubtitle`), so a job would burn in data or export a
  subtitle stream. Running it again gives the same answer: set those on the Deliver page by hand (Data Burn-in
  off, Export Subtitle off), add the job there, and say so in the hand-over. `prefer_refused` (a note, the job is
  made) names `MultiPassEncode` or `EncodingProfile` that this encoder does not offer.
- Loudness: the EDL's `mix.gain_db` (set before the approval, step 5) brings the mix to the preset's target as far
  as the true peak allows, and the build plays it (every clip's volume includes it). Resolve 21.1 has no scripted
  limiter, so a residual (`residual_lu` in the finish result's `loudness`, the same number `loudness_off` printed)
  is a Deliver page step: Audio tab, tick Normalize Audio Levels and choose Optimize to Standard with the preset's
  target and the true peak `deliver-script` prints (the preset's `audio.codec_tp_db`, -2 dBTP for social: an AAC or
  Opus file gains up to about 0.8 dB of true peak in the encode, so the plain -1 dBTP ceiling is for uncompressed
  masters only); Normalize to Standard is gain only and stops short when a peak limits it. A residual of 0 needs no
  Deliver page step. When `E commands` lists `mix`, `E "LAB" mix "VER" "LAB/deliver/<edit name>_mix.wav"` prints
  the mix normalised and true-peak limited as a separate file (its limiter sits 0.5 dB under `codec_tp_db`, -2.5
  dBTP for social, as a margin for the limiter's own overshoot and the resampling that follows).
- Ads (`ad_15`, `ad_30`, `product_demo`): `deliver-script` also writes `textless_<ver>.py`, a copy of the built
  timeline with the title and caption tracks switched off (the textless master an ad is re-versioned from). It adds
  a timeline, so ask the user before running its RUN line; it never changes the programme. It finds the text
  tracks by the build's title and caption items, so a renamed title track is still switched off (`renamed` lists
  it). Its result is ok only when every text track of the copy is off; when `refused` lists tracks, run the RUN line
  again: it checks the existing copy and switches off what is still on, and reports it.
- QC of their export: `M "LAB" qc "<export file>" --preset <preset id>` (loudness, true peak, clipping, dropouts,
  polarity, sound on one side or a different speaker in each ear, judged on the loud parts so a one-sided
  voice-over over a centred music bed counts, flashes, format, colour tags; with `--edl "VER"` when `M commands`
  shows that option, also ungraded log picture, black runs and missing captions), then explain each finding.

## 10. Hand-over
At most 10 lines, in plain words, about one line per point below (open actions one line each):
- what was built and where: the new timeline's name and bin, the graphics timeline, the lab folder (and the SRT
  when captions go as a file);
- the loudness, one line: the measured mix (`preview_lufs`), the mix gain the build set and the residual; with a
  residual above 0, the Deliver page step (Normalize Audio Levels, Optimize to Standard at the target), else "on
  target, nothing to tick";
- up to 5 open actions: the workflow's `open_actions`, anything verify or qc left, the click file of a song whose
  bar 1 nobody checked, a retimed music item that kept a static level (then offer the ducked music as a file:
  `E "LAB" stem "VER" --track A2 "LAB/deliver/music_ducked.wav"`);
- when the edit has effects: which clips carry Fusion comps (the effects result's `fusion_clips`: clip name, track
  and timecode, never the EDL ids alone), and that those
  effects sit under the grade (on log footage a flash or a light leak looks stronger once graded: play each one after
  grading); and, only when the brief needs one, at most one or two manual steps in the exact words of KNOWLEDGE.md
  section 15, "What stays manual" (Resolve's own word-highlight subtitles, the direction or colour of a simple
  transition, text behind a person, a label that follows an object, Smart Reframe or Stabilizer settings, a speed
  ramp changed by hand);
- that the timeline explains itself: named tracks, labelled and coloured items, and the Blue hand-off marker whose
  note (also in the timeline's Comments) lists the graphics timeline, the clips with comps and the music pieces;
- how to undo: their own timelines were never changed. Delete the new timeline and its graphics timeline (and, if
  media was imported, the skill's bin) to remove everything; the backup in `LAB/backup/<time>/` (DRT) restores the
  timeline that was current at the start through File > Import > Timeline, if anything ever went wrong.
Then offer the next steps, one line each: grading the new timeline with the resolve-colorist skill when it is
installed (name and uid from the finish result's `colorist`, plus the effects result's `fusion_clips`, so the
colorist knows
which clips carry effect comps; the graphics timeline and the stills are not graded);
for an ad, 15 s and 6 s cutdowns as a next job; a revision (you edit a copy of the cut list, review it, adopt it as
the next version and build it as another new timeline). The lab holds proxies and previews (roughly 15 to 30 MB per
minute of footage plus about 20 MB per minute of each preview); `M "LAB" clean --proxies` frees the proxies when the
edit is done.

## Handoff mode (free Resolve, or no Resolve tools)
Free Resolve 21.1 has no scripting, so nothing can be built or verified automatically. Run steps 2 to 6 with the
files the user names (`E "LAB" init` with `--fps` and `--size` from the project they will use, then
`M "LAB" add <files or folders>`). Then:
- If `E commands` lists `export-fcpxml`: `E "LAB" export-fcpxml "VER" "LAB/deliver/<edit name>.fcpxml"`. The user
  imports it with File > Import > Timeline, lets Resolve import the source clips, and relinks any clip that shows
  offline. The imported timeline starts at 00:00:00:00 (Resolve ignores the start time in the file) and the markers
  sit on the first clip.
- For a vertical edit from wider clips (or the other way round), tell them to set the imported timeline's
  mismatched resolution option to Scale full frame with crop (Timeline Settings, Use Custom Settings), as the
  command prints; otherwise slowed clips are letterboxed.
- If `E commands` does not list `export-fcpxml`, give them the report's shot list (source file, in and out
  timecode per item) to assemble by hand.
- `E "LAB" export-srt "VER" "LAB/deliver/<edit name>.srt"` for captions. For a burn preset (social pieces and ads)
  it writes the picture's cues (`KIND picture`: the cues the preview draws, none under a title that repeats them):
  the user imports it (File > Import > Subtitle), drags the subtitle clip onto the timeline by hand, and styles the
  track in the Inspector (Track tab: a bold sans font about 64 to 88 px on a 1080x1920 timeline, a stroke or a
  background, raised into the preset's `y_band`, about 50 to 61 % of the height for vertical); left at Resolve's
  default the captions sit where the platform's buttons cover them. For a vertical piece they tick Export Subtitle,
  Burn into video on the Deliver page: burn only this picture file, never the `--spoken` one (`KIND spoken`, for a
  platform's caption upload), or a caption appears under the title it repeats.
- Say that the result is unverified, and that the file carries no fades and no music ducking (the music sits at
  one level): offer `E "LAB" stem "VER" --track A2 "LAB/deliver/music_ducked.wav"` (put it at 0 dB in place of the
  song) and ask for a check by eye and ear. It carries no effects either: keyed zooms, ramps, accents, animated
  captions and titles and the Fusion transitions exist only in the preview, so in handoff mode plan the cut without
  them, or tell the user which moments the preview animates.
Offline mode is the same; the preview shows the edit but is not a deliverable.

## Troubleshooting
- Resolve tools time out or are missing: Resolve Preferences > System > General > External scripting using:
  Local; File > Setup AI Assistants; restart Claude Code. The free version has no scripting (handoff mode).
- A snippet fails: read its error, fix the cause (another project open, a render running, media missing), run it
  again. Do not edit snippets other than the flags at their top.
- `offline_media` in check: a file moved or changed. Moved: `M "LAB" add <its new folder or path>` (same id), then
  assemble again. Changed: `M "LAB" ingest`, then assemble again.
- `ingest` reports an error for one file: the rest is analysed; explain the file's error (for example a RAW
  format ffmpeg cannot read: ask for a ProRes or H.264 copy).
- No transcript (`--asr none`, or whisper missing): edits from pictures and music still work; talking pieces need
  whisper (step 0).
- `preview_frames` STOP: run the review again; if it stays, say the preview renderer failed on this edit and show
  the checks without the preview.
- The workflow stops with `STAGE_FAILED` or at the usage limit: wait for the reset and call it again with the
  same `scriptPath` and args plus `resumeFromRunId` (step 5); finished agents are not paid for twice. It returns
  no candidates or only the fallback: the subagents were probably blocked by permission questions (README.md,
  "Before the first run"); fix that and resume, or use standard or quick.
- Never append a subtitle clip to a timeline by script, and never call Resolve's title or generator insert on a
  programme timeline: the first crashed Resolve 21.1, the second ripples V1. The build's snippets avoid both.
- An effect, transition or animation refused by assemble (`fx_refused`, `fx_unknown`, `transition_unknown`): the
  message gives the catalogue's reason (a P2 entry, an entry whose sandbox test has not passed yet, a retime on a
  still, Speed Warp asked together with a ramp or freeze, a parameter of the wrong type or value). The EDL keeps
  the refusal, so `check` and the review STOP on it (`fx_refused`) until the cut list fixes or drops the effect. Use
  another catalogue entry or a straight cut; never build it by hand in the user's project.
- Disk full: `M "LAB" clean --proxies`, or delete old `review_*` folders of versions nobody needs (reviews of adopted
  versions are in `LAB/reviews/`). `LAB/cache` holds preview chunks and can be deleted too, and so can the frame
  grabs in `LAB/verify/<ver>/grabs/` once `verify --grabs` has run (about 1 to 2 MB each at 1080x1920; its result
  stays in `pixels.json`, and a new `verify --grabs` needs a new grab run).
- More in README.md, "Troubleshooting".
