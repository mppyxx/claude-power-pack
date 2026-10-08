# Editing knowledge for resolve-editor

Every agent of the editing team reads the sections its prompt names. The numbers are exact and the check ids
match what `edit_lab.py check` and `media_lab.py qc` report, so a finding in `checks.json` can be looked up here
(section 14 lists every id).

How sure each rule is:
- **[V]** verified: a standard, a platform's own help page, a peer-reviewed study, or measured by running code.
- **[S]** practice: consistent advice from working editors, no primary data.
- **[G]** a default to start from: tune it to the brief, never treat it as a law.

Hard gates (STOP) protect the viewer or the build: [V] rules, physical limits (a black frame, a clipped word), and
the brief's own numbers (the preset's length, caption layout and music end, which the user can change). Other
[S] and [G] rules are scored by the judges, not enforced.

Contents
1. How an edit is built here
2. First principles: what a cut is for
3. Attention and continuity
4. Pacing and shot economy
5. Hooks, hook copy and structure
6. Techniques: action, J and L cuts, match cuts, jump cuts, B-roll, voice-over pieces, silence and fillers
7. Cutting to music
8. Transitions
9. Sound
10. Text, captions, titles, looks and end cards
11. Genre playbooks (one per preset)
12. Platform delivery
13. Scorecard, hard gates, ABCD checklist, adversarial prompts
14. Glossary: report flags and check ids
15. Effects, motion and animated text (keyed zooms, speed ramps, transitions, animated captions and titles, sound
    effects, genre budgets, what stays manual)

---------------------------------------------------------------------------------------------------------------

## 1. How an edit is built here

### The pieces
- **The EDL** (`resolve-editor/edl@1`) is the single source of truth. Every item has integer frames and half-open
  ranges `[in, out)`: `rec_in`/`rec_out` count timeline frames from the timeline start, `src_in`/`src_out` count
  frames of the media at the media's own rate (audio-only media uses the timeline rate). Seconds are converted
  with `round(t * fps)`, never `int()` (int gave the wrong frame for a third of cut positions at 24 and 30 fps
  [V]). Speed rule: `src_out - src_in == round((rec_out - rec_in) * speed * media_fps / timeline_fps)`, plus or
  minus 1 frame. Its optional `mix` block, `{"gain_db": 2.4, "by": "cutlist", "lufs_in": -16.4, "tp_in": -4.9,
  "target_lufs": -14.0}`, holds one uniform gain in dB for every enabled audio item (the loudness route of section
  9); only `gain_db` acts, the other keys are a record, and no block (or null, or 0) changes nothing.
- **The cut list** (`resolve-editor/cutlist@1`) is what editors write: transcript word ranges, shot ids, seconds
  and beat counts, each with a `why`. `edit_lab.py assemble` turns it into an EDL and does all frame arithmetic:
  it snaps every speech edge into the pause next to it, adds the preset's pads, a 1-frame audio fade at every
  join, beat anticipation (a `beats` segment ends 1 frame before the beat), captions from the kept words and the
  music ducking envelope. Effects are named the same way (`fx` on a segment anchored to a word or a beat, `retime`,
  a transition type, a title's `anim`; section 15) and assemble bakes them into tables of values per frame. Nobody
  on the team computes frame numbers by hand.
- **The lab** holds all-intra 640x360 proxies at the source frame rate, 48 kHz and 16 kHz WAVs, and the analysis:
  shots, transcripts with word times snapped to the audio, pauses, fillers, beats, loudness, per-shot quality and
  contact sheets. Everything offline reads the proxies, so seeking and joining are frame exact.
- **The preview** is a render of the EDL on the proxies (chunks of about 25 items, joined without re-encoding,
  frame counts checked) with a sample-exact numpy audio mix. Where an item lets the pictures under it show through
  (a still with transparency, an opacity under 100, a zoomed-out or moved picture that leaves part of the frame
  uncovered), the preview draws it over them as Resolve does. The Resolve build is a second render of the same EDL,
  so what the judges saw is what Resolve gets, and `verify` proves it item by item: clips, stills at their exact
  length, titles and caption cues as Text+, and the music ducking as level pieces. The exceptions: solid colour
  items stay preview only, and a retimed music item keeps one static level instead of its ducking.
- **The review pack** (`E review EDL --tier ...`, written to `<EDL folder>/review_<stem>/`, or `LAB/reviews/` for
  an adopted version in `LAB/edits/`): `report.txt` (every
  shot, its timeline time, beat offset and flags, the dialogue as placed on the timeline, captions, titles, music,
  checks), `overview_NN.png` (30 s per row: thumbnails, pacing bars, the mix waveform, beat ticks, cut lines in
  white for an ordinary cut, yellow on the beat and red off the beat or flagged, words in red when clipped, title
  and caption spans),
  `cuts_NN.jpg` (for each cut the frames A-2, A-1 | B0, B+1, labelled `cut 34 01:00:41:10 27f|21f beat+3`),
  `checks.json`, `review.json`, `preview.mov`. Tiers: `quick` = report and overview; `standard` = plus cut sheets
  for flagged cuts, every cut in the first 3 s and cuts next to transitions or speed changes; `best` = cut sheets
  for every cut. Read `report.txt` first, then at most 6 images per review.
- **The build** happens only after the user approves a version: a backup of the current timeline (DRT, OTIO and a
  JSON dump), then a NEW timeline in the skill's own bin (`<edit name> <version> (resolve-editor)`) and, when the
  edit has titles or burned-in captions, a graphics timeline next to it (`<edit name> <version> graphics
  (resolve-editor)`) that holds every Text+. The user's timelines are never edited. `verify` reads the new timeline
  back and diffs it with the EDL, including the text of every title and caption.

### What the build makes in Resolve, and what it cannot
Built natively (Resolve Studio 21.1, measured in a sandbox project):
- Clips with their in and out points, speed, fades, static punch-in and reframe (`zoom`, `frame_x`, `pan_px`,
  `tilt_px`), audio levels (each clip's own gain plus the EDL's `mix.gain_db`), transitions and markers.
- The timeline's own size and input sizing come from the EDL (the preset's `timeline.input_sizing`, usually scale to
  crop), not from the project's setting, so `project.json` may say scale to fit while the built timeline crops.
- Names and labels, so the timeline reads like an editor's: every track is named from the EDL (V1 "Picture", V2
  "Overlays", "Titles", "Titles 2", "Captions" for the caption track, A1 "Dialogue", A2 "Music"; a Resolve track the
  EDL does not use is named "(empty)"; A3 is "Nat sound" when the EDL has one); items the build makes carry a name and
  a clip colour (titles "TITLE <id> <text>" in Apricot, captions "CAP <id> <text>" in Tan, music duck pieces "MUSIC
  <id> <gain> dB" in Teal, stills and logos Purple, clips that carry a Fusion comp Orange, footage plain); a Blue
  hand-off marker ("resolve-editor hand-off") at the programme's first free frame and the same note in the timeline's
  Comments say what depends on what (the graphics timeline, the clips with comps, the music pieces, the loudness
  residual, where the lab is). Verify warns when a name, a label or the marker is missing (`track_name_off`,
  `item_label_off`, `handoff_missing`).
- Stills (a logo card, a photo) at their exact length: the build sets the still's mark in and out to the length
  and appends it without a start and end frame, because Resolve ignores both for a still and would place 125
  frames. Images are imported with the rest of the media when the user allowed the import.
- Titles and caption cues as Text+: each one is a Text+ on the graphics timeline (one slot per item; an item
  longer than a slot takes the next slots), and the programme holds each as a range of that timeline, titles on
  the EDL's title tracks ("Titles", and "Titles 2" for a title that overlaps another, such as the brand name and the
  call to action on an end card) and captions on a track above every video track. Every cue sits at its own height (its
  `box` in the EDL). The look (font, size, colour, stroke, shadow, box, line spacing) comes from the preset's
  `looks` and `LAB/brand.json` (section 10) and is mapped to the Text+ inputs. Editing a Text+ on the graphics
  timeline changes the programme too. With `build-script --text markers`, or where Resolve refuses a Text+, the
  title becomes a Cream marker with its text (`text_marker_only`).
- Captions: `captions.deliver` "burn" (the default) builds them as Text+ cues; "file" (long form) leaves them to
  the SRT from `E export-srt` for the platform's caption upload.
- Music ducking: the build splits a ducked music item in the middle of each ramp of its `volume_env`, gives each
  piece its plateau level and joins the pieces with a Cross Fade 0 dB as long as the ramp; a ramp deeper than 6 dB
  becomes steps of at most 6 dB (each step at least 2 frames, so a 2 or 3 frame ramp stays one step), because one
  deep crossfade bends the ramp (measured up to 4 dB off the preview). Each crossfade is added at the incoming
  piece's start: added at the outgoing piece's end, Resolve 21.1 made about half of them a frame short after the cut
  (measured on 24 joints, the same ones every time), and such a crossfade renders as a dropout (the music fades to
  near silence for a frame, then comes back hard). The build checks every transition against the frames it asked for
  (`transitions_off` in its result) and verify STOPs on one that differs (`transition_off`).

- Keyed effects inside each clip's own Fusion comp (section 15): zoom moves (`punch`, `bump`, `snap`, `push`) as a
  Transform whose Size is keyed about the zoom point; impact accents when the catalogue enables them (`shake`,
  `flash`, `rgb_split`, `glitch`, `light_leak`); speed ramps and freezes as a TimeStretcher whose source frame is keyed
  (the item itself stays at speed 1); Speed Warp slow motion (`"retime_process": "speed_warp"`). The EDL holds every
  effect as a table of values per frame; the preview renders that table with ffmpeg and the build keys the same
  values, linear between keys, so the judges see the curve Resolve plays [V, sandbox: linear keys measured on three
  renders, a keyed push matched the preview at a median correlation of 0.998].
- Transitions from the catalogue (`fx_catalog.json`, section 8): simple ones (dissolves, wipes, Push, Slide), Fusion
  transitions with their published inputs set (motion blur on), and two custom ones rebuilt inside a Fusion
  transition: a whip (`custom_whip`) and a zoom through the cut (`custom_zoom_overlap`). Without handles a whip or a
  zoom becomes a pair of clip comps instead (`whip_pair`, `custom_zoom_through`). The build reads every transition's
  length back.
- Animated titles and captions: the same Text+ cues on the graphics timeline, keyed on every frame (a size pop, a
  slide, a fade through a Merge, a typewriter Write On), with a Follower modifier that colours, sizes or boxes the
  spoken word on its frame. The lab computes the plan per frame once; the preview draws it and the build applies it.
- Audio cross fades under a picture transition on linked dialogue or nat sound (`Cross Fade +3 dB` between two
  different sounds, `Cross Fade 0 dB` inside one sound), and sound effects (whooshes, hits, pops) from the user's own
  files (`M add --sfx`) on their own track, the file's loudest moment on the event frame. Nothing is downloaded.
- The build runs its snippets in this order: the clip chunks, `_gfx.py` (titles and captions, animated or not),
  `_fin.py` (transitions, audio fades, markers), then `_fx.py` (clip comps: moves, accents, retimes, Speed Warp,
  clip-pair transitions). `_fx.py` comes last because a clip comp's timing depends on the transitions placed next to
  the item.

Not possible by script, so the edit must not depend on it:
- No audio volume keys (automation) and no Edit-page keyframes or retime curve. Moves, ramps and accents are keyed in
  the clip's Fusion comp from the EDL; split an item where a sound level should change.
- No loudness normalisation key in the render settings and no scripted limiter: the build reaches the target with
  the EDL's uniform mix gain where the true peak allows it, and the residual goes to the Deliver page (Audio tab:
  tick Normalize Audio Levels, choose Optimize to Standard; section 9).
- No Fairlight effects per parameter, no AI Audio Assistant, no Music Editor, no Detect Music Beats, no IntelliCut
  by script. The lab does these jobs itself. Grading is a separate job (the resolve-colorist skill, when it is
  installed); the graphics timeline and the stills are not graded.
- The P2 effects of section 15 are not built: a finish texture on a nested programme, text behind the subject,
  masked reveals, emoji and sticker pops, progress bars, split screens, 2.5D parallax, glow, lens flare, light rays and
  film looks, stabilising and smart reframe, effects-library templates, per-character staggers and text wipes,
  library titles and audio-reactive motion. Some of them are a manual step for the user (section 15, "What stays
  manual"); assemble refuses them with a message.
- Keyed effects on a still image (a push on a photo or a logo card) are built: the sandbox test of a still's comp
  timing passed (a 1.0 to 1.1 push rendered 1.0, 1.05 and 1.1), so the catalogue sets `stills_ok`. A catalogue
  without it refuses them (`fx_still_untested`).
- Effects sit under the grade: a clip comp runs before the Color page, so a flash or a leak tuned on flat log
  footage looks stronger once the footage is graded. The hand-over names the clips that carry comps.
- A clip comp works at the clip's own resolution, and the timeline applies its input sizing and the Inspector's
  zoom, pan and tilt to the comp's output [V, sandbox]. So a move, a mirror or wrap edge, a flash, an RGB split or a
  leak stays inside the picture's own rectangle: on a letterboxed or pillarboxed clip (scale to fit, a shape other
  than the timeline's), under a zoom below 1 or beside a pan gap, the bars stay black (or show the track under a
  layer). The preview draws it the same way: the comp's chain runs as before and is then cut to that rectangle.

Measured hazards the build avoids (do not work around them by hand):
- `MediaPool.AppendToTimeline` with a subtitle clip (an imported SRT) crashed Resolve 21.1. The skill never appends
  a subtitle item in any form and never makes a subtitle track by script; captions are Text+ cues, and the SRT is
  only a file.
- `InsertFusionTitleIntoTimeline` and `InsertGeneratorIntoTimeline` insert at the playhead and ripple V1. The build
  calls them only on its own graphics timeline, never on a programme timeline.
- A still appended with a start and end frame lands at 125 frames (see above); Dynamic Zoom zooms out and cannot be
  set by script, so it is never used.
- Text+ geometry: Size is relative to the frame width and depends on the font: Size = em px x line height / (0.804
  x width), the line height in em coming from the font's own metrics (Arial Bold 1.1499 gives about 0.70 em per
  unit, Arial Black 1.4102 about 0.57) [V, sandbox]. A font without readable metrics falls back to 0.70
  (`text_font_metrics`). The Center's y runs up from the bottom, and the outline colour defaults to red, so the build
  always sets the outline colour.
- Effect routes that fail in 21.1, so the build never uses them: a Fusion Clip for an effect across a cut (its comp
  cannot be reached by script), adjustment clips (V1 only, no track choice), `SetInput` inside a locked comp (reads
  back, never renders), a Transform with a colour channel switched off or two masked Transforms in a row (the frame
  cannot render), Resolve's random camera shakes (two renders differ, so no check can pass), and the transitions Pop
  Wobble Reverse (a one-frame flash back), DCTL Transition (does nothing until a DCTL is picked by hand) and Logo
  Wipe without its text or logo.
- Reading a Text+'s text without a time strips its Follower's word styling from renders; the build and verify always
  read it at time 0.
- `Timeline.GetIsTrackEnabled` reads False for the tracks of a timeline that is not the current one (21.1); the
  build and verify read track state and track names only while that timeline is current.
- Rendered files carry AAC priming: the audio stream of an MP4 runs about 0.07 s longer than its picture. It is
  harmless (the picture sets the length and the sound stays in sync); do not report it.

> **Known limits of this version (do not report them; the hand-over covers them once).**
> Effects come from the catalogue only (section 15): no audio volume keys, no P2 effects, no retime on a still.
> Frames whose preview is marked approximate (Speed Warp, light leak, glitch, some Fusion transitions) are not
> pixel checked; the frames around them are. Effects in clip comps are processed before the grade.
> No audio sync: separately recorded sound, several cameras of one moment and lip-sync takes are not synced.
> One framing per shot in a vertical crop of wider footage (no subject tracking).
> A retimed music item keeps one static level; solid colour items are preview only.
> Loudness: the EDL's mix gain reaches the target only as far as the true peak allows; the residual is a Deliver
> page step (Optimize to Standard), stated in the hand-over.
> Log footage looks flat until it is graded; the graphics timeline and stills are not graded.
> Text+ letters differ from the preview's drawing by a few pixels (2 to 8 px with per-font metrics and kerning); the
> text itself is verified exactly.
> Only the first audio track of a multi-track camera clip is placed.
> Report a limit only when it changes what the piece needs (a shot that only works animated, a take that needs
> sync): put that into `open_actions`, not into a marker or a must-fix.

### Time and frame facts worth knowing
- Resolve record frames are absolute (`timeline start frame + rec_in`); timeline markers are relative.
- Transitions need unused source (handles) [V, sandbox, 27 measured cases]: a centred transition of d frames needs
  head(B) >= d and tail(A) >= ceil(d/2), a left-aligned one head(B) >= d, a right-aligned one tail(A) >= d and
  head(B) >= d (A the outgoing item, B the incoming one; timeline frames, times media fps / timeline fps in media
  frames). With less, Resolve places it shorter without a word, and a left or right one with no head renders
  through black. `validate` stops both (`transition_handles`, `transition_through_black`), and the build reads every
  placed length back.
- Keys set by script interpolate linearly in 21.1, so every eased curve is written as one key per frame inside its
  span [V, sandbox]. A clip comp counts in source frames and starts at the first frame the item is seen, including
  the frames under an incoming transition; the build converts the EDL's frames with that rule and refuses an item
  where Resolve's own numbers disagree (`fx_timebase`).
- Timecode counts at the nominal rate; drop frame only at 29.97 and 59.94.
- Measured in a Resolve 21.1 sandbox and built into the lab (resolve_semantics.json): a clip on a timeline of a
  different rate shows source frames by rounding down (a 30 fps clip on 25 fps shows 0 1 2 3 4 6 ...), exactly like
  the preview; a retimed clip keeps its placed length when its speed changes, so the build places it at its final
  length first; `pan_px` and `tilt_px` are the pixels the image moves on the timeline (Resolve's own Pan unit is
  scaled to the image size, so a 16:9 clip on a 9:16 crop timeline needs about a third of the value; the build
  converts): a positive `tilt_px` moves the picture up; `zoom` scales about the frame centre. Clips with rotation
  metadata show upright in both.
- Clean speed needs a whole source step per timeline frame: `speed x media fps / timeline fps` of exactly 1 or 2
  (50 fps footage at 50 % or 100 % on a 25 fps timeline). A step of 1.5 (50 fps at 75 % on 25 fps) shows source
  frames 1 and 2 apart in turn, which judders on steady motion; use a whole step, or Speed Warp for a slow speed.
- Mixed rates [V, sandbox]: n source frames of a clip on a timeline of another rate last floor(n x timeline fps /
  media fps) frames, at least 1. So media slower than the timeline cannot take every length (a 24 fps clip on a
  30 fps timeline can be 3 or 5 frames long, never 4). Assemble only makes lengths Resolve can place (it moves a
  cut by a frame when needed, never into a word) and `validate` stops any other length (`rate_length`).
- Slow motion [V, sandbox]: Resolve places a slowed item at full speed first and then slows it, so it needs as
  many source frames after its in point as it has timeline frames (50 % for 2 s needs 2 s of source, of which
  it plays 1 s). The last part of a clip can therefore not be shown slowed at that length: assemble moves the in
  point earlier (`slowmo_moved`) or `validate` stops it (`slowmo_handle`). Clean slow motion also needs a speed of
  at least timeline fps / media fps (50 fps footage on a 25 fps timeline down to 50 %); below that frames repeat
  (`slowmo_repeat`).
- The edit runs at the Resolve project's frame rate (new timelines get it). The dump sets it; `check` and
  `build-script` stop an edit at another rate (`fps_mismatch`).

---------------------------------------------------------------------------------------------------------------

## 2. First principles: what a cut is for

### Murch's rule of six [S]
Murch's own ranking from his practice (In the Blink of an Eye); the weights are his rule of thumb, not a measurement.

| rank | criterion | weight | what to check |
|---|---|---|---|
| 1 | Emotion | 51 % | Is this the moment the feeling peaks or turns? Did we leave the face too early? |
| 2 | Story | 23 % | Does the new shot add information or move the story forward? |
| 3 | Rhythm | 10 % | Does the cut land at a rhythmically right moment (speech cadence, music, motion)? |
| 4 | Eye trace | 7 % | Is the new point of interest near where the eye was before the cut? |
| 5 | 2D plane of the screen | 5 % | Screen direction and the 180 degree line respected? |
| 6 | 3D space of action | 4 % | Positions and geography consistent? |

Emotion alone outweighs the five below it. When criteria conflict, give up from the bottom of the list first.
A cut is a visual blink: it belongs where a thought completes or a new one starts. For dialogue that means in a
pause, at a sentence or clause end, or on a reaction; never inside a word [S].

### Dmytryk's rules [S]
1. Never make a cut without a positive reason. (Every item in a cut list has a `why`; a cut with no reason fails
   review.)
2. When undecided about the exact frame, cut long rather than short.
3. Whenever possible cut in movement.
4. The fresh is preferable to the stale.
5. Scenes should begin and end with continuing action.
6. Cut for performance rather than for a perfect match: prefer the better take with a small mismatch.
7. Substance first, then form.

---------------------------------------------------------------------------------------------------------------

## 3. Attention and continuity

- Continuity comes from carrying attention across the cut, not from a perfect reconstruction of space [V]. Cues
  that carry attention: off-screen sounds, a conversational turn, the start of a motion, gaze direction, pointing,
  a subject leaving frame, an established rhythm.
- **Match on action** [V]: cut at the onset or early part of a movement and show the continuing action at the same
  screen position in the next shot. Trim 1 to 3 frames of repeated motion if it reads as a stutter [G].
- **Eye trace** [V direction, G numbers]: after a cut the gaze is still where it was. Measure the distance between
  the main subject's centre before and after the cut as a fraction of frame width: under 0.25 reads smooth; over
  0.5 on a fast sequence is jarring unless it is motivated.
- Viewers fixate faces; background and prop mismatches are rarely noticed (in one matched exit and entrance test
  only 33 % noticed the actor had been swapped) [V]. Face position, eyeline and direction of motion matter.
- **180 degree rule** [V]: in a conversation, eyelines point toward each other across the cut; a subject leaving
  frame right enters the next shot from the left [S].
- **30 degree rule** [S]: two consecutive shots of the same subject need about 30 degrees of camera angle change
  or a clear change of shot size, or they read as a jump cut. A size change under about 10 % reads as a mistake,
  15 % or more as intentional [G].

---------------------------------------------------------------------------------------------------------------

## 4. Pacing

### Reference numbers
| material | shot length | tag |
|---|---|---|
| Hollywood 1930 to 1955 | mean 10.5 s | V |
| Hollywood 1960 to 1985 | mean 7.0 s | V |
| Hollywood 1990 to 2015 | mean 4.3 s | V |
| Social "cinematic" video ads | mean 1.2 s (15.2 shots in 17.9 s), range 0.5 to 7.5 s | V |
| Google ABCD "Overall Pacing" | mean shot under 2 s | V |
| Google ABCD "Dynamic Start" | first shot under 3 s | V |
| Google ABCD "Quick Pacing" | 5 or more shots within some 5 s window | V |
| Official music videos | mean 4.76 s (an upper bound: detectors miss dissolves); dominant shot about one beat in 2/3 of videos, about one bar in 1/5 | V |
| TV ads | about 30 s long, shots 2 to 5 s | S |

Shot lengths are strongly right-skewed, so the report gives the **median** and the coefficient of variation (CV)
next to the mean, plus the densest 10 s window. Never judge pace by the mean alone [V].

### The shape matters more than the average [V]
Shots lengthen through the setup; in the climax they first shorten sharply, then lengthen again in the epilogue.
Tension builds with shorter shots, and emotion needs time after the peak (about twice as long on the way down).
Music videos accelerate near climaxes and slow in bridges. That arc is measured on films and long pieces with an
epilogue. A piece under 30 s has no epilogue: it builds into its payoff, and a slow second half reads as the edit
running out of ideas (5 of 6 judges marked it down on a 15 s montage whose last shots were 2.6 s long after 0.9 s
shots at the start).

### Rules
- **P1** [V-derived for long pieces, S for short ones] Pieces under 30 s build into the payoff: the shots after
  half the length are as short as or shorter than the shots before it, and only the final shot may open up, for at
  most one bar of the music or 2 s, whichever is longer. A music-led piece that slows down warns `pace_decel` (6
  shots or more, under 30 s: the median length of the shots that start at or after half the length, the last shot
  left out, is 1.4 times the median of the shots before half, or more). In pieces of 30 s and more the densest window sits at or
  just before the climax or payoff, and the last 10 to 15 % may be slower than the densest window (emotion needs
  time after the peak). The densest window is `min(10 s, half the length)` long and is graded from 8 s of programme
  up (a 15 s montage is judged on its densest 7.5 s). Presets set `pacing.densest_window_after` (0 turns it off,
  for pieces that front-load their energy such as ads and clips).
- **P2** [G] CV of shot lengths between about 0.4 and 1.2. Near 0 is metronomic, very high is erratic.
- **P3** [S] No run of more than about 8 shots of identical length (within 2 frames) unless the piece is
  deliberately metronomic to a beat.
- **P4** [V] Fast pace plus arousing content raises the processing load. Shots dense with information (text,
  product detail, a key line) need longer holds than decorative shots.
- **P5** [G] A shot must be long enough to read: about 0.5 s for a simple, high-contrast image or motion beat,
  1.5 to 3 s for a wide or detailed image, longer for a face delivering emotion.
- **P6** [S, V for the failures] In a music-led piece the sound's energy has the same shape as the picture's: the
  first frame is at body level (`music_open_quiet`), the bed never sags 8 dB under its body for a bar or more
  (`music_lull`), and the last shot lands on a hit or the song's own ending at full energy (section 7, M5).

`pacing` (WARN) fires when the median shot or the CV is outside the preset's `pacing.median_shot_s` and `pacing.cv`,
when a stretch longer than `pacing.visual_change_max_s` has no visual change (a cut, an overlay, a title start or a
punch-in counts), or when the densest window (`min(10 s, half the length)`, from 8 s of programme up) comes before
`densest_window_after` of the length. `pace_decel` (WARN) is the short-piece rule of P1, measured on music-led
pieces under 30 s (the preset's `music.use` is `required`, or there are no spoken words and a music item exists);
longer music pieces (a wedding highlight, a music video, a travel film) may slow down after the peak as their
playbooks say. On a tie the densest window is the latest one, so even cutting never reads as front-loaded.

### Pace follows tone [G]
The preset's range is the frame; the brief's tone picks the place inside it. Premium, trust, craft and luxury
pieces: a median shot of 1.2 to 2.5 s, with short bursts on the music's hits. Energetic youth and trend pieces: 0.6
to 1.2 s. A calm piece cut at trend speed reads cheap; a youth piece cut at luxury speed reads slow.

### Shot economy [S, G numbers]
- Count the distinct setups first (a setup is one camera position and framing). The shot budget: a piece can use
  about 1.3 x the setups as shots before the eye notices repeats (the selects state the budget); past that, use a
  longer hold, slow motion, a size change of 30 % or more, or a text card, never the same shot again.
- The same framing within 10 s reads as the same shot, even from another part of the clip. The same media visible
  3 or more times reads as thin coverage (`repeat_setup`). A deliberate return (a callback to the opening image)
  is tagged `callback` on the item and says so in its `why`.
- A fixed length and thin footage are never reasons to keep a repeat while a hold, slow motion, a size change or a
  text card is possible. When the coverage table says a need is missing, tell the user (a pickup shot) instead of
  filling it with a repeat.
- Before repeating anything, check the shot log for good shots (score 6 or more) nobody used; newer reports list
  them, with how often each media is used.

---------------------------------------------------------------------------------------------------------------

## 5. Hooks and structure

### What the platforms measured [V]
- TikTok: introduce the content proposition in the first 3 s; prioritise the hook in the first 6 s; captions or
  text overlays; content inside the UI safe zone.
- Meta with Nielsen: up to 47 % of a video campaign's value comes in the first 3 s, 74 % in the first 10 s; 65 %
  of people who watch 3 s watch at least 10 s; captions raise view time by 12 %; 41 % of videos were basically
  meaningless without sound.
- Google ABCD: Attention, Branding, Connection, Direction; its checks look at the first 5 s for brand, product,
  people, face and speech.
- YouTube long form: the "intro" retention moment is the share of viewers still watching after 30 s.

### Hook rules
- **H1** [V] Something visibly changes before 3.0 s (a cut, a title, strong motion).
- **H2** [V] The proposition (what this is about, or the product) is seen or heard by 3.0 s; for ads the brand or
  product appears in the first 5 s.
- **H3** [S] The first frame is already interesting: no fade from black, no logo sting, no "hey guys" wind-up.
  Default target: first meaningful image and first word of the hook by 1.0 s [G].
- **H4** [V] The hook works with sound off: text or pictures carry the proposition.
- **H5** [S] The hook opens a loop (a question, a promise, a before state, a result shown first, a surprising
  image) and the piece closes it before the end.
- **H6** [S] Short-form endings loop or land on the payoff with no dead tail (tail after the last beat under
  0.5 s [G]). H6 is about a music ending; a spoken payoff needs 0.5 to 1.0 s after its last word to land
  (`ending_tight` under 0.5 s, `ending_tail` past 1.2 s, unless an end card covers the tail).
- **H7** [S] The first second is among the most kinetic or graphic moments of the piece: a motion that starts
  inside the first 10 frames, a strong graphic, a face in action. Never open on the stillest shot or on slow motion
  of nothing happening. In short form the hook text is on screen from frame 1. Rank hook shots by how much happens
  in their first 10 frames (the shot log notes it), not by how beautiful the middle of the shot is.

### Hook copy [S]
- At most 6 words, tension or a specific benefit, and the same message as the first spoken line (or the next step
  of it). Patterns that work: a question the piece answers, a number with a promise ("3 cuts that save a take"), a
  before state ("Still guessing your settings?"), the result first ("Built by hand in 6 hours").
- Never a label ("Learn X at Brand", "Our studio"), never a message the voice contradicts in the same 3 s.
- The architect (or the cutter who plans its own outline) writes three hook options into the outline's `copy`;
  the judges pick the strongest in context.

`hook_3s` (STOP when the preset's `hook.gate` is true): no visual change before `hook.visual_change_by_s`, or no word
and no title before `hook.proposition_by_s`. Turn it off only when the brief says the piece is a slow burn
(`checks_off`).

### Structure templates [S]
- Short-form narrative: hook (0 to 3 s), context or tension (3 to 8 s), value or story beats with a new image or
  idea every few seconds, payoff, then a call to action or a loop.
- Problem to solution (ads, demos): problem or before state, product in action, result or proof, call to action.
  Show the product early; do not spend 10 s on brand setup.
- Long-form YouTube: the first 30 s restate the promise and start delivering; a re-engagement beat every few
  minutes (a reveal, a new segment, a twist); segments that each open with a small hook; the ending pays off the
  title.
- Documentary or brand film: setup, complication, development, climax, in roughly equal parts [V].

### Story angles the architects use
| angle | shape |
|---|---|
| `hook_first` | strongest moment first, then context, then value by strength, close the loop |
| `emotional_arc` | grounded start, rising stakes, one peak (held on a face only when its performance carries the feeling; in an ad the peak is the offer), release |
| `chronological` | the order it happened, compressed; time jumps on cuts |
| `problem_solution` | short problem, solution early, proof, call to action |
| `music_led` | the song's sections are the outline; section changes on musical boundaries |

---------------------------------------------------------------------------------------------------------------

## 6. Techniques

### Cut on action [V, S]
Cut at the onset of a movement (a head turn, a sit down, a door opening, a hand reaching) and continue it at the
same screen position in the next shot. It is the most common technique in social cinematic ads (52 %) [V].

### J and L cuts [S]
- J cut: the next shot's audio starts before its picture. L cut: the outgoing audio continues under the next
  picture. They smooth dialogue and scene changes and let reactions play.
- Dialogue overlaps of 0.5 to 2 s (start near 1 s); quick conversational reactions 4 to 12 frames [G]. Scene
  changes: bring the next scene's sound in 0.5 to 1.5 s early [G]. In this skill the long overlaps are two keys on
  a spine segment, and every word stays where the cut list put it:
  - a long J cut: `"hold_prev_s": 1.0` on the incoming segment keeps the previous segment's picture going over its
    first second (placed on V2, continuing from the exact frame where the previous item stopped); the incoming
    voice starts under the old picture;
  - a long L cut: `"early_s": 1.0` on the incoming segment shows its picture one second early over the end of the
    previous segment (on V2, ending on the exact frame where this segment's own item starts); the old voice runs on
    under the new picture.
  Both are clamped to what the media and the segments allow (`hold_clamped`, `hold_ignored` in the assemble
  report). Never make these overlaps with an overlay of the same shot by its `shot` id: an overlay plays from its
  own trim, not from where the V1 item stopped, so the picture jumps inside the shot (`overlay_jump` flags it).
  Overlays are for other pictures: a cutaway, a reaction, B-roll. Overlays are picture only: their camera sound is
  never placed (an overlay takes no `"audio"` key); a cutaway whose own sound should play is a spine segment.
- `"audio_leads": [{"seg": "s05", "lead_ms": 300}]` starts s05's own audio earlier than its picture, but only
  through the pause before its first kept word and the previous item's tail after its last word; a longer lead is
  cut back (`lead_clamped`), because reaching further would bring back a flubbed take or a dropped filler. Use it
  for short breaths and room tone (4 to 12 frames); use `hold_prev_s` and `early_s` for the 0.5 to 2 s overlaps.
- Re-read the EDL (or the report) after every assemble: the cut list says what you want, the EDL says what you got.

### Match cuts [V prevalence, S practice]
Graphic (shape, colour, composition), movement (motion continues across two scenes) and sound matches. The matched
element sits in the same screen region (within about 10 % of frame width) and moves the same way [G].

### Jump cuts and punch-ins [S]
- Removing pauses and flubs from one camera creates jump cuts. In order of preference: cover with B-roll or a
  graphic; cut to another angle; alternate a punch-in (zoom 1.15 to 1.20, up to 1.30 for emphasis; a change under
  15 % reads as a mistake, section 3, and `jump_cut` counts it as none) on every other segment; or keep the jump
  cut as a deliberate style.
- Punch-in limit [G]: keep the crop above the delivery resolution. UHD source into 1080p delivery can zoom up to
  2.0 without loss; a 1080p source into 1080p delivery should stay at 1.15 or less. For vertical delivery from a
  16:9 source the fill crop already uses part of that headroom (UHD into 1920 high leaves about 1.125), and a
  1080p 16:9 clip filling a 1080x1920 frame is already enlarged 1.78 times, so every punch-in softens it further:
  there a cutaway or another angle beats a punch-in; if you punch in, use 1.15 (the smallest change that reads as
  intended) and check the preview for softness.
- A reframe needs zoom headroom: the image can only move as far as it overhangs the frame. A 16:9 clip filling a
  9:16 frame has no vertical room at zoom 1 (a tilt needs a zoom of at least 1 + 2 x |tilt_px| / 1920), and a
  phone clip on a 9:16 timeline has no room either way. `frame_edge` (STOP) catches a pan, tilt or zoom under 1
  that shows a black edge; judges on small thumbnails would miss a thin strip. A still whose outer band is black (a
  logo on black, or a transparent border) is exempt when it is fully opaque and not rotated: shrunk or moved, the
  frame around it is the same black as its own background.
- Vary the framing (wide, punch, wide, B-roll) and put the punch-in on the emphasis word, not at random.
- Keyed punch-ins [V build, S taste]: a static `zoom` changes the framing at the cut; a keyed `punch` moves into it
  on the word itself: `"fx": [{"kind": "punch", "at": {"word": 41}, "zoom": 1.25, "why": "emphasis on 'free'"}]`
  eases to 1.15 to 1.30 over 300 to 500 ms (10 frames at 25 fps, 12 at 30) and stays there for the rest of the
  segment. Anchor it on the emphasis word (the word index of the segment's media), put the zoom point on the face
  (`"point": [x, y]`, fractions of the frame from the top left) and never stack it on a static punch-in or a bump. The
  total zoom (static times keyed) stays under the genre's cap (section 15) and inside the source's headroom (above:
  `fx_soft` warns past it).
- Slow pushes [V build, S taste]: `{"kind": "push", "to": 1.06}` creeps from 1.00 to 1.03 to 1.08 over the segment
  (2 to 6 s, in_out_sine). A push keeps a long talking take or a payoff shot alive without a cut and is the premium
  move (slow reads as quality); a push across a segment that assemble split at a dropped filler continues across the
  join.

### Reframing a wider shot for a vertical piece [S, V for the geometry]
- A 16:9 shot filling a 9:16 frame shows only the middle 32 % of its width (a 4:3 shot 42 %, a 1:1 shot 56 %). A
  subject framed on a third of a 16:9 frame (x 0.33 or 0.67) is half cut or gone in a centre crop.
- Give each such segment or overlay `"frame_x"`: the subject's centre as a fraction of the source width, 0 at the left
  edge, 1 at the right. Take it from the shot log (`faces[].x`, with `faces[].y` the eye line's height from the top)
  or read it off the contact sheets: for a vertical edit every frame wider than the edit shows the centre crop as
  dashed cyan lines and a ruler of tenths along its top edge. The log sheets (`M sheets`) show 5 frames per shot, at
  0.1, 0.3, 0.5, 0.7 and 0.9 of its length; the zoom sheets (`M sheets --layout zoom`) 4 larger frames, at 0.125,
  0.375, 0.625 and 0.875. Times in the shot log (`usable`, the action's start) are those fractions of the shot (0 to
  1), while the selects and the cut list quote seconds of the media: a shot's start plus fraction x its length.
  Assemble turns it into the pan (`pan_px`) that puts the subject in the middle of the frame, clamped at the image's
  edge; `frame_x_clamped` in the assemble report says where the subject then sits and the zoom that would centre it.
  `frame_x` wins over `pan_px` when both are given.
- One framing per item (no subject tracking): a subject that walks across the frame needs the segment split, each
  part with its own `frame_x`, or a wider shot. Two people far apart in one 16:9 shot do not both fit a 9:16 frame:
  cut between them with a `frame_x` per line.
- A 16:9 shot in a 16:9 edit has no room to pan at zoom 1; with a punch-in (zoom 1.15 or more) `frame_x` pans
  inside the enlarged image, which keeps a punch-in on the face instead of the frame centre.
- `subject_cropped` (WARN): the crop shows none of the faces the shot log places in the shot, or the item's
  `frame_x` frames a face that the crop still cuts (a punch-in too tight, a clamp at the image edge). A face counts
  as inside when its centre sits at least 0.05 of the source width inside the crop's edge (about half a face). A
  two-shot cut between speakers, each line framed on its speaker, leaves the listener out on purpose and is not
  flagged; the fix always names the face the item frames, never the other person.
- `jump_cut` (WARN): consecutive V1 items from the same media, from the same detected shot or with a source gap under
  2 s, a zoom change under 15 %, and crops that show mostly the same part of the shot (a reframe whose shown parts
  overlap by less than half, such as a cut from one speaker to the other in a two-shot, is an angle change). Reordered
  bites from one locked-off take count too. Fix: a zoom of 1.15 or more on one side, or a cutaway overlay across the
  join. A join inside one segment (an item id like `s03_2`, made where a filler was dropped or a pause shortened)
  cannot take its own zoom: split the segment at that point and give the second part the zoom, or cover the join with
  an overlay. When the style wants visible jump cuts, keep them and say so in `why` (the WARN stays in the report).

### B-roll and cutaways [S]
- Cut to B-roll when the speaker names something visual, to cover a jump cut, or to give a breath between ideas.
  Stay on the face for the most emotional or most important line: cover the setup and the aftermath, not the
  moment.
- Holds: 2 to 4 s for a quick factual illustration, 6 to 10 s or more when the B-roll carries emotion or story
  (documentary and interview pacing). Short-form social cuts faster (a mean shot around 1.2 s); there a cutaway
  reads when the viewer already knows what to look for.
- Mix literal shots (the thing just named) with evocative ones; all literal gets dull.
- Every B-roll placement cites the words it illustrates and the shot it uses (`why`). Never reuse the same source
  frames twice (`repeat_shot`).
- Sound of B-roll in a dialogue piece: a spine segment without an `audio` key plays a clip without speech as nat
  sound (-12 dB) and a slowed clip silent (slowed sound drags and pitches down; use unslowed ambience for a slow
  motion moment). Set `audio` to override; report.txt shows every item's sound track and gain.
- Read the performance, not only the content [S]. A person talking without their own sound (lip flap under a
  voice-over or music) reads as a mistake; a person looking at someone off screen pulls the eye out of the frame;
  small talk and a smile to a colleague are not emotion. At an emotional peak use a face whose look carries the
  feeling (concentration, pride, relief), or the hands and the work; never a chat.
- Process order for craft and making-of montages [S]: show the steps in the order the craft happens (preparing,
  the first touch, the work, the finish, the reveal). The result before the process only as the hook, then go back.
- A proof line needs a picture that shows it: "real equipment" over a shot of the equipment in use, "small groups"
  over a group you can count. A proof line over a picture that does not show it is a blocking issue; when the
  footage has no such picture, say it in the coverage table and ask for a pickup or change the line's picture to a
  super.
- Content truth [S, V for the failures]: every cutaway shows the subject of the line it sits under, and never
  contradicts it. Blocking: a child or a bystander next to a working station under a safety line, an emptied or
  dirty product under a hygiene line, an unsafe practice (no gloves where the craft needs them, a hazard left in
  reach) under any line. No decor filler: a plant, a shelf, a wall or a sign with no story is never a cutaway
  (judges marked each one down); use a craft shot, a face at work or a longer hold instead. A third-party
  brand label (another company's bottle, a machine maker's logo) in a cutaway is a composition deduction: pick a
  take or a part of the shot without it, or frame it out with `frame_x` and `zoom`; when nothing else covers the
  line, keep it and list it in `open_actions`. The shot log lists these risks under each shot's `problems`, and
  the brief's never-show list (BRIEF_TEMPLATE.md) overrides any score.

### Voice-over pieces
A voice-over (narration recorded apart from the pictures, or made with text to speech) drives the piece; the
pictures illustrate it. `M "LAB" add --voiceover <media id>` marks the file as narration (no `separate_sound`
note, no beat analysis). When the brief has the script, `M "LAB" script <id> --file <script>`
aligns the transcript to it (section 10), so captions show the script's words.
- The narration's word ranges are the spine: `{"media": "vo", "words": [a, b]}` segments. The voice file has no
  picture, so V1 is empty under those segments and every frame of them needs an overlay (a V2 cutaway), or
  `black_gap` STOPs. Chain the overlays with `"at": {"seg": ..., "offset_s": ...}` so they meet edge to edge.
- Between voice lines the gap is 0.3 to 0.6 s [S, V for the failure: judges heard 0.8 to 1.6 s pauses as holes].
  A picture-only segment (`"audio": "none"`, or nat sound) between two voice blocks carries such a gap, not a
  longer one. A lifted musical breath (`"lift": true`: the drop, a hit, a beat of music between two lines) is at
  most about 1.2 s and only for a real musical moment, never after every sentence. `vo_gap` (WARN) measures every
  gap between consecutive spoken words on the timeline: past `max(0.8 s, pause_keep_ms[1] + 0.35 s)` (0.8 s in the
  short-form presets), or that plus 0.4 s when a lift covers it (1.2 s); a gap that titles cover for 80 % or more is
  left to `message_gap`, and a gap where the music stays ducked is called a "ducked hole" (lift it or close it).
  The ducked music stays down in a voice gap of about 3.1 s or less (section 9). In an ad a stretch over
  `message.gap_max_s` (1.5 s) with no voice, title or caption warns `message_gap`: close it, or cover it with a
  super that carries a new point.
- The end: the programme ends 0.5 to 1.0 s after the last word (`ending_tight` warns under 0.5 s), unless an end card
  follows (then the card's length). `ending_tail` (WARN) fires past 1.2 s, unless titles or a V1 still cover 80 % or
  more of that tail. The payoff line is one caption cue (`keep_together` its words, section 10) that stays whole to
  the last frame of the line; never split the last line into two short cues.
- `hold_prev_s` and `early_s` (the J and L cuts above) do nothing in a voice-over piece: V1 is empty under the
  narration, so there is nothing to hold (assemble says `hold_ignored`). To carry a take from an overlay into the
  picture-only segment after it, give that segment the same media with `in_s` = the overlay's `in_s` plus its
  length (read both from the EDL), so the shot continues frame for frame.
- A voice-over segment may also be written as `{"media": "vo", "in_s": ..., "dur_s": ..., "audio": "dialogue"}`
  instead of a word range: captions, ducking and `clipped_word` follow the words it covers, but `words_changed` cannot
  guard it (it has no range to compare), so prefer word ranges.
- Captions sit in one band (section 10): give an overlay `"caption_y"` only when a face, the product or the key
  action sits in the caption band, never per shot. Every such ask joins one alternate band and assemble snaps each
  cue to the main band or that one (`caption_y_snapped`); `caption_jump` warns when the captions still jump. A cue
  that would change within 2 frames after an overlay cut changes on the cut.
- Ducking: the voice is dialogue, so `duck: "auto"` (the default) ducks the music under it (section 9); a music item
  with no word under it is not ducked at all. `"duck": "none"` turns ducking off, and a number ducks by that many dB.
  When the first word comes within 1.5 s of the music's start, the music starts ducked (no loud first frame that
  dips); a voice gap of about 3.1 s or less stays ducked unless its picture-only segment says `"lift": true`.
- Fit check: when the voice's speech time is under about 60 % of an ad's length, or its words are under the
  playbook's count (section 11), the brief says which fit the user chose (a shorter ad, a text-led stretch with
  supers, or a longer voice-over). Never stretch a 13 s voice-over over 30 s of pictures without saying so. The
  default fit (when the brief does not choose) spreads the voice lines across the whole length, with music between
  them in gaps of 0.3 to 0.6 s and at most one lifted breath of about 1.2 s per musical hit, so the voice is still
  heard in the last third. That spread reaches at most: speech time + 0.6 s x (lines - 1) + 1.2 s x musical hits,
  plus the end card (3.5 to 5 s). A 13 s voice-over in 6 lines with 2 hits reaches about 18 s + a 4.5 s card, short
  of 30 s. When it falls short, the default carries the rest with supers over the longer gaps (each a new point
  from the brief; a gap a title covers for 80 % or more is left to `message_gap`, so `vo_gap` stays quiet there) and
  says so in the outline's risks; a shorter piece needs the user's choice. A text-led stretch is a second message, not a replay: fresh setups (no media seen in the
  first half; `recycled_half` warns past 35 % of the second half's picture time) and new proof points from the brief
  (a number, a guarantee, a name), never the voice's words again (`title_repeat` warns on two titles with the same
  text); the order is proof, then the offer, then the call to action, and the offer is shown once as a super. No
  stretch over 1.5 s without voice or text (`message_gap`).

A worked cut list (media ids `vo`, `cam_a`, `cam_b`, `song`; the shot ids come from the shot log):

    {"schema": "resolve-editor/cutlist@1", "id": "cut_A1", "preset": "ad_30",
     "spine": [
      {"id": "s01", "media": "vo", "words": [0, 11], "why": "the hook line and the promise, from frame 1"},
      {"id": "s02", "shot": "cam_a.s04", "dur_s": 1.2, "audio": "none", "lift": true,
       "why": "a breath: the first touch of the work, the music comes up for it"},
      {"id": "s03", "media": "vo", "words": [12, 24], "why": "the proof, each line over a picture that shows it"}],
     "overlays": [
      {"id": "o01", "shot": "cam_a.s02", "at": {"seg": "s01", "offset_s": 0.0}, "dur_s": 1.4,
       "why": "frame 1: the most kinetic shot under the first words"},
      {"id": "o02", "shot": "cam_b.s01", "at": {"seg": "s01", "offset_s": 1.4}, "dur_s": 2.1, "caption_y": 0.32,
       "why": "a face at work; the hands sit in the caption band, so the captions move up"},
      {"id": "o03", "shot": "cam_b.s05", "at": {"seg": "s03", "offset_s": 0.0}, "dur_s": 2.4,
       "why": "'real equipment' over the equipment in use"}],
     "music": [{"media": "song", "in_s": 0.0, "at_s": 0.0, "until": "end", "gain_db": -8.0, "duck": "auto",
                "fade_out_s": 1.0, "grid": true}],
     "captions": {"from": "dialogue", "text_fix": {"vo:3": "patience,"}},
     "titles": [{"text": "Made by hand", "at_s": 0.0, "dur_s": 2.0, "style": "hook_top", "role": "hook",
                 "why": "the same message as the first line"}],
     "targets": {"duration_s": 30.0}}

The overlays of a segment must add up to its length (the example is shortened); `E items` (when `E commands` lists
it) prints every item in one table, so gaps are easy to see.

### Silence, pauses and fillers
- Pads [V for tool defaults, G for ours]: keep `speech.pad_ms` before the first and after the last word of a kept
  run (60 to 150 ms for tight social, 100 to 250 ms for calm pieces). Zero margin clips breaths and plosives.
- Pauses kept inside a run: `speech.pause_keep_ms` (150 to 450 ms energetic, 300 to 1000 ms calm or emotional);
  speaker handoffs `speech.handoff_ms` (400 to 600 ms). Longer after a punchline or before a new topic. Never
  remove a pause that ends a thought before a new topic.
- Fillers: words tagged `filler` (um, uh, er, ah, hmm, mm) and `repeat` (the same word twice in a row) are removed
  with `drop` when the preset lists them in `speech.remove`. Words tagged `flag` (like, you know, so, I mean) carry
  meaning as often as not: never drop them automatically; drop one only when you have read the sentence and it
  reads better without it.
- A filler is removed only when the gaps on both sides are at least `speech.clean_gap_ms` (60 ms); otherwise
  assemble keeps it and reports `filler_kept_unclean`. Such a filler cannot be dropped: cover it with B-roll, cut
  the phrase at a pause instead, or leave it in. Whisper also misses some soft fillers, so filler removal is best
  effort.
- Retakes and false starts: keep the last complete take unless an earlier one is clearly better; say which in the
  selects.
- Never cut inside a word: `clipped_word` (STOP) fires when a dialogue edge cuts more than 20 ms into a word
  (snapped times). In the camera sound under a picture segment (nat sound), or on a word tagged `low_conf`
  (whisper was unsure of it, or the clip is nearly silent), it is a WARN: listen, then move the cut, lower the
  sound or set `"audio": "none"`.
- Two kinds of edge are measured, not chosen, and reported as `rate_edge` (WARN) instead: words that abut (no
  pause) with no frame edge within 20 ms of their boundary (at 23.976 and 24 fps half a frame is 20.9 ms, so in
  fluent speech about one word range in seven meets it), and a clip whose rate differs from the timeline (25 fps on
  a 30 fps timeline) with no placeable length that ends between two words. The edge then runs up to about one
  source frame into a word; assemble takes the side that cuts less and never half of a short word. Listen at the
  cut, and start or end the range at a pause if it shows.
- Whisper's word ends inside fluent speech are only good to about 0.1 s (at pauses to about 20 ms), so prefer
  edges at pauses. When the ear or the waveform shows a word boundary elsewhere than the transcript (a cut that
  sounds clipped while `clipped_word` is quiet, or the reverse), `M "LAB" edge <media id> <word index> <seconds>`
  moves the start of that word to the time heard (kept across `M script` runs); assemble the cut list again.
  `edge_ms` only nudges an edge inside the transcript's pause, so it cannot reach a boundary the transcript has wrong.
- `filler_left` (WARN): a filler or repeat inside kept dialogue.
- `pause_long` (WARN): a kept pause longer than `pause_keep_ms[1]` (handoffs: `handoff_ms[1]`) inside one audio
  item or across two that abut. A gap carried by a picture-only segment between two voice blocks is `vo_gap`'s.
- Room tone: when a join changes the background noise, lay tone or extend real audio from the handles (section 9).

### Montage [S]
A montage compresses time or builds a feeling by juxtaposition (37 % of social cinematic ads [V]). Keep a
through-line (a subject, a colour, a direction of motion, a music build) so it does not become a pile of shots.

### Cut styles the cutters use
| style | what it means here |
|---|---|
| `tight` | pauses at the low end of the preset range, cuts on action, a visual change every 2 to 4 s, word-chunk captions |
| `breathing` | pauses toward the high end, longer holds on faces after emotional lines, fewer and more deliberate cuts |
| `montage` | beat grid in 2 or 4 bar blocks, density follows the music sections, motion peaks on beats |
| `story_first` | a paper edit first (best bites in the clearest order), B-roll only where the picture needs it |

---------------------------------------------------------------------------------------------------------------

## 7. Cutting to music

### Evidence [V]
- Music video editors favour cuts that coincide with music events, including anticipation (cuts just before the
  downbeat). Pop videos put many cuts on section boundaries; R&B and reggaeton cut most on downbeats. Off-beat cuts
  are used on purpose to surprise; exact sync is not always the goal.
- Audio-visual timing (ITU-R BT.1359): sound early is noticed from about 45 ms, sound late from about 125 ms;
  acceptable up to about 90 ms early and 185 ms late. Viewers forgive sound lagging picture more than leading it.
- So a picture cut can land a little before the beat and still read as on it, but only a little after.

### Tolerance table (frames) [V, computed]
| fps | frame ms | early, unnoticed | late, unnoticed | early, acceptable | late, acceptable |
|---|---|---|---|---|---|
| 23.976 | 41.7 | 2 | 1 | 4 | 2 |
| 24 | 41.7 | 3 (at the limit) | 1 | 4 | 2 |
| 25 | 40.0 | 3 | 1 | 4 | 2 |
| 29.97 / 30 | 33.4 | 3 | 1 | 5 | 2 |
| 50 | 20.0 | 6 | 2 | 9 | 4 |
| 60 | 16.7 | 7 | 2 | 11 | 5 |

### Rules
- **M1** [V-derived] Offset from the target beat within [-2, +1] frames up to 30 fps (negative = picture before the
  beat), [-4, +2] at 50 and 60 fps (the same 80 ms early and 40 ms late). Outside [-4, +2] at 24 and 25 fps and [-5,
  +2] at 30 fps it reads as off the beat (the acceptable columns of the table above); keep it only when it is
  deliberate and say so in `why`. `beat_sync` (WARN) grades every cut on an item tagged `on_beat` (assemble tags
  `beats` segments) against the preset's `music.beat_tol_frames`.
- **M2** [V] Put section changes of the picture (new place, new idea, new act) on musical section boundaries or
  phrase starts (the downbeat of bar 1 of a 4 or 8 bar phrase).
- **M3** [V] Density follows energy: more cuts per bar in choruses and drops, fewer in verses and bridges. A cut on
  every beat only for short bursts of a few bars [S]. A piece under 30 s rides one build into its payoff: its
  second half is cut at least as fast as its first, and only the last shot opens up, for at most one bar or 2 s
  (`pace_decel`, section 4 P1).
- **M4** [S] Big hits (a drop, an impact, a lyric punch) get the strongest visual change. A beat to a bar of
  relative calm before a drop makes it land harder.
- **M5** [S, V for the failures] The music carries full energy from the first frame to the last:
  - The open: the first frame's sound is at body level. Start on the drop or on the downbeat of a full section, never
    on a quiet intro or a pickup as the hook: a pickup into the first downbeat may run at most 2 frames, so the first
    frame is the hit (a 0.84 s pickup at about -31 dBFS was heard by the judges as a quiet, hesitant open).
    `music_open_quiet` (WARN): a music item starts at frame 0, no word is spoken in the first 0.5 s, and the music
    averages 8 dB or more under its body level over 0 to 0.5 s. `beat_snap_impossible` on a deliberate pickup start
    stays a warning on purpose: a pickup start is not a hook; take the music `in_s` the warning prints, which puts a
    downbeat on the start.
  - The body: no lull. The bed never sits 8 dB or more under its body level for a bar or more (at least 1.5 s)
    where no one speaks (`music_lull`, WARN). Choose the song's window from its bar levels: when the beats file has
    them (`bar_db`, the RMS level of each bar of the grid, and `bar_low_db`, its 30 to 120 Hz band), skip the bars
    that sit 6 dB or more under the section around them; otherwise read the preview's music envelope or listen. A
    breakdown or a filtered fill is skipped by cutting over it at a bar line (section "Music to length"), not
    covered with pictures. `M status` lists each song's quietest bars (bar number, start time and dB under the
    median bar).
  - The end: on a hit or the song's own ending at full energy: the last downbeat of a phrase with its ring-out, a
    final button, or the natural end. Never a fade over a thinning section (a breakdown, a filtered fill, an outro
    that drops 6 dB or more): `music_lull` also warns when the 2 s before the last fade-out starts (or before the
    programme end) average 6 dB or more under the body ("ends in a lull"). A fade is fine over full-energy music at
    a phrase end, never mid-phrase ("music cut off" is a closing failure editors flag). `music_end` (STOP when the
    preset's `music.end` is `resolve`): a music item ends inside the programme, or at its end with less than 12
    frames of fade and not at the media's natural end.
  The body level these checks compare against is the median level of the music bus over the frames with music and
  no speech, leaving out the first 0.5 s and every fade-out (the preview's `audio_env.npz`, 100 Hz).
- **M6** [S] Match motion to rhythm: cut on the start or peak of a movement that lands on the beat.
- **M7** [S, G numbers] Beat bumps sit ON downbeats or snares, or in 2 and 4 bar blocks, never on every beat: a
  `bump` peaks 1.05 to 1.12 (default 1.08) on the beat, 80 ms up and 300 ms down. Anchor it with `"at": {"beat": k}`
  (programme beat k) or `{"beat_rel": n}` (beat n inside the segment, 0 the first) so `fx_offbeat` can grade it;
  a shake, flash or glitch on a hit uses the same anchors. The biggest hit gets the strongest move (M4), and at most
  one accent family per hit.
- **M8** [S, V for the mechanism] Speed ramps land on the music: the fast part compresses the run-up, the ramp eases
  over 300 ms (8 frames at 25 fps, 9 at 30) and the slow or normal speed already holds ON the drop or downbeat
  (`"land": {"beat": 16}` on the segment's `retime`). Slow sections below timeline fps / media fps repeat frames:
  slow the 50 or 60 fps footage, not the 25 or 30 fps footage (Speed Warp cannot smooth a ramp, section 15); a
  ramped item plays no sound. Details in section 15.

### Planning
- Plan in 2 or 4 bar blocks. A 15 s ad at 120 BPM is 7.5 bars.
- Use the cut list's `"beats": n` on spine segments (with a music item that has `"grid": true`): assemble ends the
  segment on the n-th beat minus 1 frame and snaps the first beat segment to a downbeat. `"beats": 2` lasts two
  beats counted from the beat the segment starts on (a start up to the preset's early tolerance before a beat
  counts as on it). Never accumulate beat lengths in frames by hand; non-integer frames per beat drift.
  `beats_short` warns when a segment came out more than half a beat short.
- A beat segment that follows a non-beat segment (usually dialogue) is moved onto the nearest beat by trimming or
  extending the previous item, but only inside the room its words leave: never into its last word, never into
  the next word of the source, never more than the longest kept pause past it. When there is no room assemble
  says `beat_snap_impossible` (only when the cut is not already within the beat tolerance) and the cut stays
  OFF-BEAT: give the last word segment a longer tail with
  `"pad_ms": [60, 270]` (its own pads override the preset's) or start the music earlier or later. A pad never runs
  into the next word: it is clamped at the next word's onset (and a lead pad at the previous word's end), so a long
  pad reaches the beat only when the pause after the word is that long. When the first
  beat segment opens the programme, the warning prints the music `in_s` that puts a downbeat on the start.
- `preset.music.cut_on` (`beat`, `downbeat`, `phrase` or `none`) is guidance for the cutters: which grid points
  to aim the `beats` segments at. The lab measures every tagged cut against every beat.
- The beat grid is a measurement, not a given [V, measured]. On 286 real drum loops and synthetic grooves (4/4, 78
  to 174 BPM) with no hint it sat on the beats (within 70 ms) in 176 cases (62 %), and on all 10 synthetic songs
  with chords. Of the misses about half are flagged and half are silent: the grid on the off-beats (a
  four-on-the-floor with an off-beat bass), or at half or double the tempo. Ingest flags what it can:
  - `tempo_uncertain`: a syncopated groove (dancehall, rolling drum and bass, conga patterns) has a pulse at 2:3 or
    4:3 of the beat that is as strong as the beat, or a slow groove (78 to 90 BPM) was doubled on a close call; the
    grid may be at the wrong tempo;
  - `bar1_uncertain` (the beats file's `downbeat_phase_uncertain`): the bar start is a guess and may be one to three
    beats off, most often on drum loops with no chord changes. Measured: bar 1 was right on only about half of the
    test loops. This flag is the one to trust: the beats file's `downbeat_conf` has no threshold of its own (the
    margin between the best and the second best bar phase decides, and the flag already reads it).
  - `bpm_name_mismatch`: the file name gives a tempo ("loop 100 BPM") that differs from the detected one by more than
    3 %; `loop_bars_off`: a short file (40 s or less) is a whole number of bars (4, 8 or 16) at the name's or a
    nearby tempo but not at the detected one. Both mean the detected tempo is probably wrong.
  - `bpm_from_name`: the name gives a tempo and the file is a whole number of bars at it, so `beats` used the name's
    tempo and put bar 1 on the first strong onset (the beats file says `"bpm_from": "name+loop"`; a tempo the user
    gave says `"user"`).
  The click file is how a person checks the grid by ear: `M "LAB" click <id>` writes the music with a soft click on
  every beat and a high click on every bar 1 (`LAB/media/analysis/<id>.click.wav`, 30 s from the first downbeat;
  `--from` and `--dur` choose another stretch). The question for the user: "Does the high click land on beat 1 of
  each bar: yes, it drifts, or N beats early or late?". N beats early or late: `M "LAB" beats --only <id>
  --shift-bar N` moves bar 1 by N beats (negative moves it earlier). It drifts: the tempo is wrong; ask for the
  tempo, or use the name's.
  So when a flag fires for a song the edit cuts to, the main session asks the user (who can listen) with the click
  file, or for the tempo and where bar 1 falls, and passes it: `M "LAB" beats --only <id> --bpm <N> --first-downbeat
  <seconds>`. When nobody can answer (the user is away), the edit uses the grid as it is (the name's tempo when
  `bpm_from_name`) and the hand-over lists the click file so the user can check it later. The bar line always picks
  bar 1. It moves the whole grid only when that is clearly right: the found beats sit half a beat off it (the grid
  locked onto the off-beats), a real onset sits at the bar line, and the moved grid lands on onsets at least half as
  strong. Then the media is flagged `grid_moved` and `beats` prints a NOTE with the amount. A bar line more than
  about 70 ms (or 15 % of a beat) from every found beat otherwise leaves the grid as found, takes the nearest beat as
  bar 1 and flags `bar_line_off_grid`; the grid moves onto it only with `--move-grid`, when the user confirms the time
  is exact. Measured on 291 grooves with the true tempo [V]: a bar line within 40 ms of the kick gave 269 to 271
  right (grid and bar 1), 60 to 100 ms off gave 239 to 262, and no grid that plain nearest-beat bar 1 had right was
  ever broken (pass 4 moved the grid onto any strong onset near the bar line: a time 80 to 100 ms late then broke
  105 to 199 right grids). A time typed by ear or read two or three frames late is 60 to 125 ms off; the exact time
  comes from parking the playhead on the first kick of a bar in Resolve's audio waveform. A bar line before the found
  beats (bar 1 at the song start, over a beatless intro) extends the grid back to it one beat at a time. Until then,
  aim `beats` segments at beats, not at downbeats, and do not rely on the music `in_s` hints that assume bar 1.

### Tempo grid [V]
| BPM | beat s | 4/4 bar s | 8 bars s | frames per beat at 24 | at 25 | at 30 |
|---|---|---|---|---|---|---|
| 80 | 0.750 | 3.00 | 24.0 | 18.0 | 18.75 | 22.5 |
| 90 | 0.667 | 2.67 | 21.3 | 16.0 | 16.7 | 20.0 |
| 100 | 0.600 | 2.40 | 19.2 | 14.4 | 15.0 | 18.0 |
| 120 | 0.500 | 2.00 | 16.0 | 12.0 | 12.5 | 15.0 |
| 128 | 0.469 | 1.88 | 15.0 | 11.2 | 11.7 | 14.1 |
| 140 | 0.429 | 1.71 | 13.7 | 10.3 | 10.7 | 12.9 |
| 170 | 0.353 | 1.41 | 11.3 | 8.5 | 8.8 | 10.6 |

### Music to length
Trim whole bars at downbeats, prefer removing whole 4-bar phrases with matching harmony on both sides, absorb the
remainder by trimming the start, and keep the natural ending. A hard cut off the grid clicks; an on-grid join with
a 30 ms equal-power crossfade ending 5 ms before the downbeat does not [V]. In v1 the simplest safe route is to
start the music later (`in_s`) so its natural end lands on the programme end (`"align": "end"` on the
music item computes that `in_s` for you, and moves the beat grid with it), or to end on a hit at a phrase end with
a short ring-out (M5). A short piece aligned to the end of a long song plays only its outro, usually the quietest
part: assemble warns (`align_end_outro`) and names a strong section to start at instead.
- A music item that starts inside the file (not at its first sample) starts on a bar line or a beat or gets a
  fade-in of 0.5 s or more; otherwise it clicks or lands mid-phrase (`music_start_off_bar`). A piece's first frame
  starts on the drop or a downbeat (M5: a pickup of at most 2 frames).
- The music ends after the story does: its fade starts at least 0.2 s after the last word ends, and the programme
  runs at least 0.5 s past the last word (`ending_tight`). In a voice piece it ends 0.5 to 1.0 s after the last word
  unless an end card follows (`ending_tail` past 1.2 s, section 6).
- Two copies of a short loop laid back to back: write two music items, the second with `at_s` where the first ends
  and the same `in_s`, each with `"until": "end"` or a `dur_s`; `music_end` accepts a copy that ends where the next
  starts. With `"duck": "auto"` the second copy keeps the first copy's depth (assemble note `duck_shared`), so the
  seam does not jump; a numeric duck on both copies does the same. Give only one item `"grid": true`: when two do,
  the later one's grid wins (the report's beat column follows it).
- The drop (the biggest hit) lands in a gap of the voice, at full level, never under words, where it would be
  ducked away: a drop ducked more than 6 dB is a wasted drop. Plan the voice blocks around the song's sections. A
  gap of the voice under about 3.1 s stays ducked on its own; mark the picture-only segment that carries the drop
  `"lift": true` so the music comes up for it (section 9).
- Lines sit over full sections with their low end (a verse with its kick and bass), and the duck puts the bed 10
  to 13 LU under the voice in an ad (`duck_lu` to `duck_lu` + 3 LU in any preset). Never put the voice over a
  filtered intro or a breakdown with no low end: ducked, it vanishes (judges heard a bed about 20 dB under the
  voice as no music at all). `speech_music_gap` warns on both sides (section 9). The picture's peak sits on the
  music's peak.
- A stop for the offer: a break, a hit or a drop-out right before or under the offer line makes it land. It lasts
  at most one bar (2 s at 120 BPM), then the music is straight back at full energy; a longer filtered build after
  the offer is a lull (`music_lull`).
- An ad ends on a button (a final hit or the song's own ending), not on a fade under the card; the end card sits on
  that button (section 10).
- A music-only piece keeps its music at full level; a piece with a voice follows the ducking rules of section 9.

---------------------------------------------------------------------------------------------------------------

## 8. Transitions

- Straight cuts dominate. Hollywood dissolves fell from about 8 % of transitions (1935 to 1955) to about 1 %
  (1970 to 2005), and shots around a dissolve are longer than the film's median [V]. Default: 95 % or more of edit
  points are straight cuts in narrative, documentary, interview and brand work [G number, V direction].
- A dissolve means time passing, a change of place, memory or dream, softness, or a montage of related moments
  [S]; the shots on both sides are held longer than the local median. A fade to or from black means a beginning,
  an ending or a major chapter break.
- Motion transitions (whip pan, match move, zoom through) work in energetic social, travel and event pieces when
  direction and speed match across the cut [S]; they must fit the brand's tone (a wipe broke a luxury brand's tone
  in the study editors reviewed [V]).
- Transitions hurt when they hide weak content, repeat the same effect on every cut, fight the rhythm, come from a
  different visual language, or delay the hook [S].
- `transition_share` (WARN): dissolves above the preset's `transitions.max_share` of all edit points. `fx_repeat`
  (WARN): the same transition effect on 4 cuts in a row, or transition effects on more than 30 % of edit points
  outside a music montage. `fx_hook` (WARN): a transition effect inside the first second delays the hook.
- Flashes: no more than 3 general or red flashes in any 1 s (ITU-R BT.1702 and the Ofcom rule for broadcast: a flash
  is a pair of opposing changes of relative luminance of 10 % or more with the darker state below 0.80, over more
  than a quarter of the screen) [V]. WCAG 2.3.1 is stricter about area (a much smaller patch of the screen counts).
  `photosensitive` (STOP) in the delivery QC measures the BT.1702 luminance rule over a quarter of the frame; it
  does not measure red flashes or small flashing areas, so judge those by eye. Never build strobe edits or flash
  transitions that break it: `fx_flash_rule` (STOP) counts every flash, glitch, flashing split and Brightness Flash
  of the EDL in any 1 s before anything is built.

### The catalogue: which transitions the build makes [V, sandbox: every type below was placed and rendered]
A cut list names a transition by its catalogue key (`fx_catalog.json`; `E explain` and this section) in
`"transitions": [{"after": "s03", "type": "<key>", "frames": n}]`, or by the generic `whip` or `zoom`, for which
assemble picks the route: the custom Fusion transition when both items have the handles, else a pair of clip comps
(the assemble report's `fx_routes` names the route). Naming a concrete key forces it, and it STOPs without handles.
Frames are at 25 fps; scale them with the frame rate.

| key | frames | use when | avoid |
|---|---|---|---|
| `cross_dissolve` | 8 to 12 as a soft cut in reels, 15 to 25 standard | time passing, a change of place, a soft montage | every cut; 2 to 5 frames between unrelated shots (reads as a glitch); hiding a talking-head jump cut (reads as mush) |
| `dip_to_black` | 12 to 24 (reels 8 to 16), 30 to 50 at an ending | a chapter break, before the end card | the start of a piece |
| `blur_dissolve` | 10 to 16 | a dreamy change (beauty, wedding, product) | fast reels |
| `brightness_flash_fx` | 4 to 8 | a snare or a camera-flash moment | more than 2 per piece; it counts toward the flash rule |
| `whip`: `custom_whip` with handles, `whip_pair` without | 6 to 10 with the middle on the beat; as a pair about 3/7 of the frames out and the rest in (7 frames: 3 out and 4 in; 8 frames: 3 and 5), one eased travel whose fastest move is across the cut | both shots move the same way, or the camera whipped | a direction that fights the camera move; a whip on every cut |
| `zoom`: `custom_zoom_overlap` with handles, `custom_zoom_through` without | 8 to 12, peak 2 to 3, the point on the subject; as a pair 4 to 8 frames each side | a punch through the cut into a detail or a new place, on a hit | calm content; a point on an empty part of the frame |

Extra keys on a transition: `direction` (`left`, `right`, `up`, `down` for a whip, pan or slide: the way the PICTURE
travels on screen; a camera panning right moves the picture left, so a whip that carries a right pan on says
`left`), `peak` and `point`
(the zoom's peak size and its point in frame fractions from the top left) and `mix` (the frames over which the zoom
hands over), `motion_blur` (P1 Fusion transitions; on by default), `audio` (below) and `look_is_brief` (P2 only).

P1 transitions (built when the catalogue enables them; assemble refuses a disabled one and says why):
- Motion transitions, always with motion blur (without it mirrored seams show, `transition_mb_off`): `pan_left_fx`,
  `pan_right_fx`, `pan_up_fx`, `pan_down_fx`, `slide_left_fx`, `slide_right_fx`, `slide_up_fx`, `slide_down_fx`,
  `zoom_in_fx`, `crash_zoom_fx` and `stretch_blur_fx`. At 10 frames a Fusion slide reads as a cut with one streaked
  frame.
- `smooth_cut` (2 to 4 frames) ONLY inside one take: the same media, a source gap under 2 s, the head in the same
  place. Anywhere else it morphs one face into another (`smooth_cut_misuse`, STOP).
- Fixed-default transitions: `additive_dissolve`, `edge_wipe`, `burn_away`, `clock_wipe`, `cross_dissolve_fx`,
  `detail_dissolve_fx`, `noise_dissolve_fx`, `push`, `slide`.
- `glitch_fx`, `rgb_splitter_fx`, `camera_shake_fx`: 4 to 8 frames, music or tech pieces only, and the delivery file
  goes through the photosensitive QC.

Fixed defaults of the simple and Resolve FX transitions (a script cannot set them) [V, sandbox]: Dip To Color goes
through black, Push and Slide come in from the left, Edge Wipe and Burn Away run from the bottom to the top. Frame
k of a d-frame transition shows (k + 0.5) / d of the incoming shot, and a centred one covers the frames from the
cut minus ceil(d/2) to the cut plus floor(d/2). Another direction or colour is a manual step for the user (section
15, M3).

Sound under a picture transition [V, sandbox]: a video transition on linked picture and sound items leaves the sound
as a hard cut. `"audio": "plus3"` adds a Cross Fade +3 dB (`audio_xfade_plus3`, equal power) to the audio items,
right between two different sounds and the default under dialogue or nat sound; `"zero"` adds a Cross Fade 0 dB
(`audio_xfade_0db`, linear), right inside one continuous sound; `"none"` keeps the hard cut (the default for
music-only pieces). Both previews match Resolve to 0.1 dB.

P2 transitions ("almost always amateur" in reels [S]): the irises, the shapes (box, heart, star, triangles), band,
radial, spiral, venetian blind, X and centre wipes, barn door, split, the 3D cards over black (spin, cube, flip 3D,
box twist, film strip), page curl, shatter, slice push, drop warp, fold, tile wipe and the rest of the Fusion
templates. Assemble refuses them unless the transition says `"look_is_brief": true` (the brief asks for exactly
that look); `transition_amateur` (WARN) flags one that runs anyway, and `transition_black_card` (WARN) a 3D card
over black on a vertical piece. Never, in any version: `pop_wobble_reverse_fx` (flashes the outgoing shot back for
one frame), `dctl_transition` (does nothing until a DCTL is picked by hand) and `logo_wipe_fx` (shows its sample
text): `transition_broken` (STOP).

Flash risk [V, sandbox]: `block_glitch_fx`, `disarrange_fx`, `edgy_fx`, `three_color_flash_fx`, `inverse_flash_fx`
and `tile_wipe_fx` flicker: `transition_flash_risk` (WARN) names the photosensitive QC the delivery file must pass.

Handles: section 1 has the measured rule; `transition_handles` and `transition_through_black` STOP before anything
is built, and verify STOPs on a transition whose length or type differs (`transition_length_off`,
`transition_type_off`). Lengths outside the catalogue's range warn (`fx_length`: at 30 fps a whip 5 to 13 frames, a
zoom-through 7 to 16, a flash 2 to 7, a glitch 2 to 8).

---------------------------------------------------------------------------------------------------------------

## 9. Sound

### Layers [S]
1. Dialogue or voice-over: the priority lane, clean and even, no clipped words at edits, and on both sides: a
   voice recorded on one channel of a stereo camera track plays in one ear on phones and headphones (`one_sided`;
   the fix is the clip set to mono in Resolve, section 14). Two speakers on separate channels (a two-microphone kit
   in stereo, `two_voices`) must never be set to one channel, which drops a speaker: this version cannot centre
   them, so the user sets the track's Pan Spread to 1 (PNT) in the Fairlight mixer after the build.
2. Room tone and ambience: continuous under dialogue edits so the noise floor never jumps or drops to digital
   silence.
3. Music: supports feeling and pace, ducked under speech, edited to phrases.
4. Effects and Foley: sounds of on-screen actions; editors flag a missing expected sound (a swoosh on a fast move).
5. Designed transitions: whoosh on whip pans and flying text, riser into a reveal, hit on the hero cut. Sparing:
   the same whoosh more than about 3 times in 30 s is repetitive [G].
6. Silence: deliberate removal of layers before a reveal or on an emotional beat [V].

### Loudness targets
| destination | integrated | tolerance | max true peak | tag |
|---|---|---|---|---|
| Web video: YouTube, Instagram, TikTok, Facebook | -14 LUFS | 0.5 LU for our own deliverables | -1 dBTP; -2 dBTP when the file will be AAC or Opus, or is louder than -14 | G for the platforms (none publishes a number), V for the codec overshoot |
| Apple Podcasts | -16 LKFS | 1 dB | -1 dBTP | V |
| EBU R128 broadcast | -23 LUFS | 0.5 LU (1 LU live) | -1 dBTP | V |
| EBU R128 s1 short form (ads, promos) | -23 LUFS, max short-term -18 LUFS | 0.2 LU | -1 dBTP | V |
| ATSC A/85 (US TV) | -24 LKFS | about 2 dB | below -2 dBTP | S |
| Netflix | -27 LKFS dialogue gated | 2 LU | -2 dBTP (limiter at -2.3) | V |

- Measure the delivered file in its real layout and codec: the same mono speech measures 3 LU louder as dual mono,
  and AAC 320k raised true peak by 0.8 dB, Opus 96k by 0.6 dB [V]. Presets carry `audio.lufs`,
  `audio.true_peak_db`, `audio.tol_lu` and `audio.codec_tp_db`.
- Normalise with plain gain from a measurement, then a true-peak limiter only if needed, then measure again
  (at most 3 passes). ffmpeg's `loudnorm` missed by 0.2 LU in linear mode and silently switched to dynamic mode
  when the peak budget was short [V]. In Resolve, the clip command Normalize Audio Levels is gain only and cannot
  fix true peak; on the Deliver page (Audio tab: tick Normalize Audio Levels) choose Optimize to Standard, which reaches
  the target and keeps peaks under the limit (Normalize to Standard is gain only).

### Speech against music
- Accessibility rule (WCAG 1.4.7) for speech-led pieces: background at least 20 dB under the voice [V]. Trade
  practice ducks music 15 to 24 dB under voice; energetic social edits often sit 8 to 14 LU under by taste [G].
  Presets: `music.duck_lu` 10 for ads (the music is part of the message), 12 for talking reels, 14 for other
  social pieces, 20 for interviews, documentaries, podcasts, demos and weddings.
- The bed is neither loud nor buried: during speech it sits `duck_lu` to `duck_lu` + 3 LU under the voice (10 to 13
  LU in an ad) and keeps its low end. `speech_music_gap` (WARN) is two-sided: it fires when the music is less than
  `duck_lu` under the voice ("too loud"), and when it is more than `duck_lu` + 5 LU under it ("the bed is buried":
  15 LU in an ad). A buried bed comes from a quiet or filtered stretch of the song under the voice, a duck deeper
  than the depth cap, or a low `gain_db`: pick fuller bars, or raise the music's `gain_db`.
- Ducking recipe [V]: duck regions from the kept speech (300 ms before speech to 250 ms after), merge gaps shorter
  than `music.duck_hold_s`, 250 ms ramp down, 800 ms ramp up, depth so the music sits `duck_lu` under the voice. A
  keyed curve held the music within 0.5 dB while talking; a sidechain compressor pumped 4.7 dB and let the first
  syllable through. The build makes the same curve in Resolve: the music item is split in the middle of each ramp,
  each piece gets its plateau level, and a Cross Fade 0 dB as long as the ramp joins the pieces. A retimed music
  item keeps one static level; `E stem` renders the exact ducked track as a file for that case.

### Ducking that breathes [V for the measurements, S for the rules]
A duck that opens between every sentence pumps: the bed swells in each breath and dips again, which reads as a
mistake. Measured in the trial pieces: releases in 1.8 s pauses, swings of 16 dB in an ad, and a bed that opened at
full level and dipped 6 dB before the first word. The rules assemble follows:
- Start ducked: when the first speech starts within 1.5 s of the music item's start, the envelope starts at the
  ducked level on frame 0.
- Hold short gaps: speech gaps shorter than `music.duck_hold_s` stay ducked (2.5 s in dialogue presets, 1.05 s in
  music-led ones). A release also needs the music to get at least 1.0 s at the open level and the rise and the fall
  back down to be more than 2.5 s apart. Measured, that means the bed comes up on its own only in a voice gap over
  about 3.1 s (0.25 s after the last word, a 0.8 s rise, at least 1 s open, a 0.25 s fall ending 0.3 s before the
  next word). A `duck_hold_s` under 2.5 s has no effect; only a value over 2.5 s holds longer gaps down too.
- Lift on purpose: a picture-only spine segment with `"lift": true` opens the voice gap it sits in. The music is
  at full level on the segment's first frame, so the drop's hit is never ducked: it rises in 0.2 s (longer for a
  deep duck: 2 frames per 6 dB) to end on that frame, starting no earlier than 0.05 s after the last word. When the
  last word leaves less room than that, the rise is faster, down to 2 frames (the hit masks a fast step); when not
  even 2 frames fit, the rise ends up to a few frames after the cut and assemble warns `lift_late` with the level of
  the first frame (start the segment a frame or three later, or end the words before it earlier). It falls in
  0.15 s to be down by the earlier of the segment's end and 0.05 s before the next word. The segment must be at
  least the rise plus the fall plus 0.25 s at full level long: about 0.6 s at 25 fps with a 10 dB duck, more for a
  deep duck (at 30 dB and 23.976 fps a 1.0 s segment is refused). Use it for the drop and for a musical beat
  between two lines. It is refused (warning `lift_refused`) when words are heard inside the segment or it is too
  short, and `duck_pump` does not count it. The EDL records it in the music item's `duck.lifts`.
- No release in the last 2 s of the programme: the ending stays where it is.
- A depth cap: when the automatic depth passes `music.duck_max_db` (10 dB for ads and reels, 12 for talking pieces
  and demos, 20 for long form), the item's base gain drops by the difference and the duck is `duck_max_db` deep, so
  the swing stays small. The EDL records it in the music item's `duck` block (`base_shift_db`).
- A cut list may override both per music item: `"duck_hold_s"`, `"duck_max_db"`.
- A designed pause (the music lifting between two lines, made with `"lift": true`) is worth it only for a real
  musical moment (a drop, a hit, a phrase start), 0.6 to 1.2 s long in short form, with the music coming up to
  within about 3 LU of the voice; otherwise hold the duck and close the gap to 0.3 to 0.6 s. `vo_gap` warns a lifted
  gap past `max(0.8 s, pause_keep_ms[1] + 0.35 s)` + 0.4 s (1.2 s in the short-form presets), and an unlifted one
  past 0.8 s there; one lift per musical hit, never after every sentence.
- `duck_pump` (WARN) flags what is left: the music gain rising and falling by 4 dB or more within 2.5 s, a duck
  ramp that starts in the first 0.3 s, or a swing past `duck_max_db`.
- Render against preview [V, measured]: one Cross Fade 0 dB join across a deep ramp bends it, up to about 4 dB off
  the preview's straight dB ramp in the middle of a 16 dB ramp. So the build splits every ramp deeper than 6 dB into
  steps of at most 6 dB (a step is at least 2 frames: a lift's 2 or 3 frame rise at a deep duck is one step, masked
  by the drop's hit), and the depth cap above keeps the swings small in the first place.
- A crossfade that Resolve made a frame short after its cut renders as a dropout of about 20 dB for a frame [V,
  measured in proof renders]: the build adds each one at the incoming piece's start (which lands as asked), verify
  STOPs with `transition_off` when one still differs, and `verify --proof` compares the render's sound with the
  preview's mix (`audio_dropout`).

### The loudness route
The trial renders landed 1 to 4 LU under -14 LUFS because nothing set the level of the whole mix. The edit now
carries one uniform gain for it, and every review measures the result [V for the measurements].
- The mix gain (the EDL's `mix.gain_db`): `"mix": {"gain_db": X}` at the top level of the cut list. Assemble copies it
  into the EDL's `mix` block (`{"gain_db": X, "by": "cutlist"}`; an EDL may also hold `lufs_in`, `tp_in` and
  `target_lufs` for the record). X dB is added to every enabled audio item of every audio track (dialogue, music, nat,
  sound effects), on top of the item's own `gain_db`, its volume envelope and its fades: the preview, `E stem` and `E
  mix` play it, and the build sets each clip's volume in Resolve to its own gain plus X (each duck piece too), so the
  timeline plays what the preview measured. No `mix` block, null or 0 changes nothing. `validate` refuses a gain
  outside -20 to +12 dB (`bad_mix`, an `edl_invalid` STOP).
- The measurement: every preview measures its mix (`preview.json` audio `lufs_i`, integrated LUFS, and `tp_dbtp`,
  true peak), and checks.json `stats.loudness` gives `lufs_i`, `tp_dbtp`, `target`, `mix_gain_db`, `gain_more_db`
  and `residual_lu`. `loudness_off` (WARN) fires when the mix is more than `audio.tol_lu` (0.5 LU) from
  `audio.lufs`; its fix names the exact cut list line to write (`"mix": {"gain_db": <the current gain plus
  gain_more_db>}`) and the residual. A preview rendered before this measure existed (fresh, but with no `lufs_i`)
  is measured from its `preview.mov` when the checks run (a note says so), so the check never passes in silence.
- The target: the preset's `audio.lufs` (-14 in every shipped preset) and `audio.codec_tp_db` (-2). A brief that
  asks for another target (-16 for a podcast, -23 for broadcast) gets a lab copy of the preset: copy
  `SKILL/presets/<id>.json` to `LAB/presets/<id>.json` and change those two keys in the copy (a lab preset wins over
  the shipped one; copy the whole file, since missing keys take the built-in defaults, not the shipped preset's).
  The cut list's `mix` block holds only the gain; `target_lufs` in an EDL is a record.
- True peak caps the gain: `gain_more_db` is the smaller of (target minus `lufs_i`) and (`audio.codec_tp_db` minus
  `tp_dbtp`), so the mix never passes the codec's true-peak budget (-2 dBTP in the shipped presets). What plain gain
  cannot reach is the residual (`residual_lu`). Resolve 21.1 has no scripted limiter (its render settings have no
  loudness key, and the clip command Normalize Audio Levels is gain only), so the residual goes to the Deliver page:
  Audio tab, tick Normalize Audio Levels, choose Optimize to Standard at the preset's target and true peak (Normalize
  to Standard is gain only). With a residual of 0 the Deliver page needs nothing. Expect a piece with loud peaks
  (drums, a hit) to keep a residual of 1 to 3 LU.
- `E "LAB" level <EDL> <OUT.json>` (when `E commands` lists `level`; it needs a fresh preview of the EDL, else it
  STOPs with "run `E preview EDL` first") writes a copy of the EDL with `mix` = `{"gain_db": <the suggested total>,
  "by": "level", "lufs_in", "tp_in", "target_lufs"}` and prints the gain and the residual. Put the same `"mix":
  {"gain_db": X}` line into the cut list, or the next assemble drops it.
- Who does what: the finishing editor sets the mix gain from `loudness_off` and reviews again (within 0.5 LU of the
  target, or with only the true-peak residual left); the main session reads `loudness_off` in checks.json before the
  build; the hand-over states the measured loudness, the mix gain and the residual, with the Deliver page step when
  the residual is above 0.
- A separate file: when `E commands` lists `mix`, `E "LAB" mix <EDL> <OUT.wav>` prints the preview mix (with the mix
  gain) with plain gain to the preset's `audio.lufs`, a true-peak limiter at `codec_tp_db` minus 0.5 dB (a margin for
  the limiter's own overshoot and the resampling that follows, so the delivered file stays under `codec_tp_db`) and a
  second measurement (OUT.json holds `lufs_in`, `gain_db`, `lufs_out`, `tp_out`): a mastered mix for an audio-only
  deliverable or a check. Check the exported video with `M "LAB" qc` either way.
- `mix_peak` (WARN): the preview mix peaks above -1 dBFS; above +6 dBFS it is a STOP (a gain typed as +dB instead
  of -dB is the usual cause), and a cut list cannot switch that off.
- `speech_music_gap` (WARN): the music less than `music.duck_lu` LU under the voice during speech, or more than
  `duck_lu` + 5 LU under it (buried), measured on the preview's buses as the power of the dialogue during its words
  against the music's. `duck: "auto"` sets its depth with the same measure, so an auto duck lands on `duck_lu`
  (measured: 14.4 LU for a target of 14).

### Fades at every audio join [V]
- A hard join clicks. Tonal material (music, hum, held vowels) needs 10 ms of fade or more, 20 ms is safe; speech
  in quiet needs 1 ms or more. One frame (33 to 42 ms at 24 to 30 fps, 17 to 20 ms at 50 and 60 fps) is always
  enough, so assemble puts a 1-frame fade on every
  dialogue and nat sound edge. Zero-crossing snapping alone still clicks on tonal material.
- Curves: equal power for different material on the two sides, linear for two parts of the same continuous source.
- A crossfade does not fix a change of background level; that needs room tone.
- `audio_fades` (WARN): a dialogue or music edge that meets another item with no fade and no audio transition.

### Room tone and gaps [V]
- Any fill beats digital silence, but no automatic fill matched real backgrounds closer than about 2.4 to 3.6 dB
  per band. Policy: first extend the same take's real audio from its handles (a J or L cut); otherwise fill with
  tone and flag the spot for listening.
- `silence_gap` (STOP): the mix below -60 dBFS for more than 0.3 s inside the programme where dialogue plays around
  it. Unless the silence is intentional (say so in `why`), close the gap or extend audio under it.

### Dialogue cleanup
Studio's Voice Isolation (amount 70 to 80) and Dialogue Leveler exist but are set by the user in v1. Offline
denoisers barely help against real, uneven noise; never apply heavy denoise unattended [V].

### Delivery QC ids (media_lab qc on the exported file)
`loudness` (STOP: integrated off target by more than `tol_lu`), `true_peak` (STOP: above `true_peak_db`, or above
`codec_tp_db` for AAC or Opus files), `clipping` (STOP: 3 or more consecutive samples at full scale), `dropout`
(STOP: digital silence over 20 ms inside the programme), `polarity` (WARN: L/R correlation below -0.5 or mono
fold-down loss over 6 dB), `channel_balance` (the sound sits on one side, judged on the loud parts: STOP in presets
with dialogue, WARN otherwise; a different speaker in each ear is a STOP in dialogue presets; two different
microphones left and right a WARN in a dialogue preset). Picture ids, when `M commands` shows qc's `--edl` option:
`log_picture` (WARN: the file looks like ungraded log footage), `black_run` (STOP: 0.5 s or more of black inside
the programme), `captions_missing` (WARN: no caption text where the EDL has a cue).

---------------------------------------------------------------------------------------------------------------

## 10. Text and captions

### Reading-time floors [V]
Static text meant to be read stays on screen at least `max(0.833 s, characters / 20, words x 0.33 s)`:
| text | BBC 0.33 s per word | Netflix 20 characters per second (floor 0.833 s) | take the larger |
|---|---|---|---|
| 1 word, 6 characters | 0.33 | 0.83 | 0.83 s |
| 3 words, 17 characters | 1.00 | 0.85 | 1.00 s |
| 4 words, 22 characters | 1.33 | 1.10 | 1.33 s |
| 7 words, 40 characters | 2.33 | 2.00 | 2.33 s |
| 12 words, 70 characters | 4.00 | 3.50 | 4.00 s |

TikTok's "5 to 10 words per second" advice is 2 to 4 times faster than any reading standard; read it as "keep text
short", not as a timing rule.

### Subtitle rules for long form [V]
At most 42 characters per line and 2 lines (1 line when it fits), up to 20 characters per second (17 for
children), each cue at least 5/6 s and at most 7 s, bottom-heavy when two lines, break after punctuation or before
conjunctions and prepositions, never split article and noun, adjective and noun, first and last name, subject and
verb. Presets `youtube_long`, `interview_doc` and `product_demo` use this (`captions.style: sentences`).

### Social captions [S]
- Chunks of 1 to 4 words that follow the speech (each chunk appears on its first word and stays until the next
  chunk), no chunk shorter than `captions.min_s` (0.6 s in the shipped word-chunk presets: a cue that flashes up
  for a third of a second cannot be read), at most 2 lines and 32 characters per line on 9:16 (20 to 32 suits a
  phone).
- One clean caption look for the whole piece: a bold sans-serif (Arial Bold, or the brand's caption font), white,
  a soft shadow and at most a thin stroke of 0.04 em (`caption_soft`, the look of the word-chunk presets); a pill
  (`caption_clean_box`) when the footage behind the band is busy or light. Never the heavy outline of earlier
  versions on a brand piece (`caption_bold`, 0.11 em, reads cheap). Size 84 px on a 1080x1920 frame in the
  word-chunk presets (capitals about 60 px tall; judges found 72 px, about 50 px caps, too small), 64 px in the
  landscape sentence presets. Assemble wraps each line to what fits the safe box at that size (about 15 characters
  at 84 px in `vertical_all`), and `caption_layout` stops a line wider than the box. The look is `captions.look`
  (section below).
- `captions.words` (the preset's chunk size, [1, 4] in the word-chunk presets) wins over an anim's own range:
  `word_pop` and `pop_highlight` show the preset's chunks. `keep_together` wins over the word cap: a kept phrase
  longer than the cap stays whole in one cue (keep such phrases to 6 words or fewer).
- When `E0 schema cutlist` shows them: `"keep_together": ["free studio visit"]` never splits those phrases between
  cues, and `"suppress_under_titles": true` drops a cue whose words are mostly on a title shown at the same time
  from the picture (only the spoken SRT, `E export-srt --spoken`, keeps it: the words are still spoken; the
  picture's SRT, the one to burn in, leaves it out).
- Block centre around 50 to 61 % of the height (`captions.y_band`): below faces framed with eyes near one third,
  above the platform's caption and button area.
- One caption band per piece, plus at most one alternate [S, V for the failure: a block that jumped 9 times between
  18 and 57 % of the height in 25 s was the most cited caption fault]. The main band sits at the middle of
  `captions.y_band`. Captions must not cover faces, the product or key action [V]: when a shot puts a face or the
  action in the band, move that segment's captions with `"caption_y"` (a fraction of the height), and only then;
  never per shot or for variety. Every `caption_y` farther than 0.06 from the main band on one side of it joins one
  alternate band at the median of those asks (the side with more asks; on a tie the one asked farther away), and
  assemble snaps each cue to the nearer of the two (note `caption_y_snapped`, with the asked and the used heights).
  Asks on the other side go back to the main band and assemble warns `caption_y_dropped` (they may cover what they
  were moved off): move those captions to the same side, or reframe the shot. Inside a band every cue is anchored by
  its first line: a two-line cue grows downward from where a one-line cue's line sits, so the first line never
  jumps by half a line; a low band (16:9 presets) sits where a cue of `captions.max_lines` lines still fits the
  safe box, so no cue is pushed up by the safe box and every cue of the band shares one first-line height. `caption_jump` (WARN) fires
  when the cues' first lines take more than 2 heights (3 % of the frame height apart) or change height more than
  once per 6 s of captioned time. Emphasise key words.
- Do not let a caption straddle a hard cut by only a few frames: end it at the cut, or keep it at least about
  0.5 s past the cut [G]. Assemble does this for cuts within 0.5 s after a cue's last word, keeps chunks from
  ending on a lone word, breaks after commas, does not end a full chunk on an article, preposition, conjunction or
  auxiliary ("starts | with a clean stencil", not "starts with | a clean stencil"), and capitalises a word that
  starts a sentence once a filler before it was dropped.
- Every sound-off platform deliverable gets captions or on-screen text [V].
- Names, brands and jargon: use the spelling glossary in the brief; whisper gets them wrong most often.
- Delivery: `captions.deliver` "burn" builds every cue as Text+ in Resolve (the render carries them, nothing to
  tick on the Deliver page); "file" (long form) leaves them to the SRT (`E export-srt`) for the platform's upload.
- `E export-srt` writes one of two files and prints which (`KIND`): the picture's cues (the default for a burn
  preset: the same cues the preview draws, the file to burn in when the timeline is handed over without Text+
  captions) or, with `--spoken` (the default for a "file" preset), every spoken cue, including those
  `suppress_under_titles` kept off the picture under a title that repeats them: that one is for a platform's caption
  upload only and is never burned in, or the picture shows a caption under its title.

### Captions come from the truth, never from a guess [V for the failure]
Whisper mishears words, most of all names, brand words and text-to-speech voices ("patience" became a word that
does not exist, a filler "uh" became "or"), and a wrong word on screen in frame 1 of a paid ad is the worst mistake
a piece can ship with.
- When the brief has the voice-over script, the main session runs `M "LAB" script <id> --file <script>` right after
  the transcription: the transcript takes the script's spelling and punctuation with the times kept, and script
  words the voice skipped are listed. It never renumbers words, so cut lists stay valid, and it refuses a script
  that matches under 60 % of the words (a wrong script). `M "LAB" fix-word <id> <index> "<text>"` fixes one word
  the user confirmed.
- Words the script lacks entirely (an ad-lib, "you know") are tagged `extra` and `filler`: assemble drops them from
  word segments by itself when there is a pause on both sides (`speech.clean_gap_ms`), and a segment keeps one with
  `"keep": [index]`. A word with no pause around it stays (`filler_kept_unclean`); to lose it, split the word range
  around it (end one segment on the word before, start the next on the word after). Read the `extra word` lines
  `M script` prints: each one leaves the voice.
- Where the transcript has different words from the script, the alignment never removes speech. A real filler
  next to a misheard word ("uh, facians" for "patience") is the one tagged `extra` and `filler`; the misheard word
  takes the script's word. Where whisper wrote one spoken word as two ("Ink well" for "Inkwell", "Face? Shans" for
  "patience"), the first part takes the script's word and the rest are tagged `joined`: they stay in the voice and
  are hidden in the captions. Where the script has more words, one transcript word takes several. Every such place
  prints a `LISTEN` line: play those words and check the voice really says the script (`fix-word` when it does
  not). A one for one replacement whose two words look unlike ("Face?" for "patience,") prints "they look unlike:
  play it": read the replaced list for pairs like that too.
- A joined word is one word everywhere: no sentence ends between its parts, `M transcript` marks each later part
  with `+`, a word range that ends on its first part runs over the rest, a range that starts on a later part starts
  on the first part (warning `joined_split`), and a drop takes the whole word (a drop of a later part alone is refused
  with `joined_split`). When the segment of the same media just before it in the spine already plays that word (its
  range ran over it), a range that starts on a later part starts after the word instead, so `[0, 1]` then `[2, 5]`
  plays every word once (the warning says "already plays"). The check `joined_split` (STOP) catches a hand-edited
  EDL that plays part of such a word, and `word_twice` (STOP) a source word heard twice in a row.
- Symbols are read as they are said: "20%" matches the spoken "20 percent", "$49" "49 dollars" (also euros, pounds,
  rupees, yen), "@studio" "at studio", "#tag" "hashtag tag", "50+" "50 plus", "20°" "20 degrees". The spoken unit
  becomes a joined part of the number (kept in the voice; the caption shows the script's "20%"), never an extra word.
  A difference only in how a word is written (a symbol spelled out, a slash, a hyphen: "Hands-on" for "Hands on",
  "24 7" for "24/7", "1 dollar" for "$1", "10 thousand" for "10k", "dot com" for ".com", "3 times" for "3x",
  "20 degrees Celsius" for "20°C", or a currency said first: "rupees 499" or "Rs. 499" for "₹499") prints no
  `LISTEN` line and counts as matched, and "at @studio" is said "at studio" (the "@" is not read twice). A word
  whisper only split in two ("Ink well" for "Inkwell") and a number written as a word ("twenty percent" for "20%")
  still print one.
- Speaker labels and stage directions are optional words. A label opens a line: "VO:", "NARRATOR:", "SPEAKER 1:", a
  screenplay cue ("MAYA (V.O.):"), or a name that opens two lines or more ("MAYA:", "Maya:", "MAYA" alone on its
  line). A direction is a bracketed span ("(beat)", "[MUSIC SWELLS]") or a line such as "SFX: ...", "SUPER: ...",
  "MUSIC: ...". The ones the voice reads (a "MYTH: / FACT:" reel, "TIP:" lists) are aligned like any word, stay in the
  voice and show in the captions; the others are left out, never reported missing, never shown (the summary line
  lists them: "speaker labels not read", "directions not read"). A label the voice reads is never lost: a heard
  word that looks like it ("Mith" for "MYTH", "One" or "First" for a list's "1.") reads it, while an unscripted word
  where an unread label stands ("So" before "MAYA:") is an extra word like any other, never shown as the label.
- Sentence punctuation standing alone ("...", "!") goes with the word before it and an opening bracket or quote with
  the word after it. A dash, a bullet ("•", "*"), a slash, a bar, an arrow or an emoji standing alone is no word, and
  emoji, bullets and arrows inside a word are removed: none of them reaches a caption (the caption font has no
  emoji), and none is reported missing.
- A script written as a list (lines that open with a bullet, a dash, an emoji or a number such as "1." or "2)") ends
  a caption and a sentence at the end of each item and of the line before the first item (`M script` tags that
  word `line_end`), so "Moisturise daily" and "Avoid the sun" never share a caption; a bullet, an arrow or an emoji
  between two words ends one there too. A list number is optional like a label: kept when the voice says it, else
  listed as "list numbers not read". A line that only starts with a number ("10 years of craft.") is no list.
- A stutter the script has once ("that that" read from "that") loses one copy as `extra`; the copy left is no
  `repeat` any more. `filler_left` also judges a `repeat` against the word heard just before it on the timeline.
- In the cut list, `"captions": {"text_fix": {"<media id>:<word index>": "shown text"}}` changes what one caption
  word shows (timing unchanged; "" hides it in the captions and in every SRT file too, while the voice keeps it; the
  next word shown opens the sentence with a capital when the hidden words opened it). Use it only with a spelling
  from the script, the brief or its glossary.
- `caption_unverified` (STOP in ads and demos, WARN elsewhere, from `captions.verify`) flags a caption word whisper
  was unsure of (`low_conf`, or a word it scored under 0.5 when no script was aligned) that no script alignment,
  fix or `text_fix` covers. Never guess a word: when nothing
  confirms it, it goes to the user as an open action ("confirm the word at 0.9 s"), not onto the screen.

### Titles: roles, styles and looks [S]
Every title has a role (what it does for the message) and a style (how it looks):

| role | what it is | usual style |
|---|---|---|
| `hook` | the first words on screen, from frame 1 in short form | `hook_top` |
| `offer` | the offer or the reason to act ("Admissions open", "20 % off this week") | `super` |
| `proof` | a fact the voice claims, shown as its keyword ("6 per group") | `super` |
| `brand` | the brand name when the voice does not say it | `super` or the end card |
| `cta` | the call to action, card-sized ("Book your\nfree visit"; the route in the button, the caption text or a short "Link in bio") | `cta` |
| `name` | a person's name and role at their first appearance | `lower_third` |
| `other` | anything else | `center` |

Set the role on every title: the checks read it (`offer_late` looks for the first `offer`). Without one, assemble
gives `hook` to a `hook_top` title that starts before 3 s, `cta` to the `cta` and `end_card` styles, `name` to a
`lower_third` and `other` to the rest.

- Styles: `hook_top` (high inside the safe box), `lower_third` (names people only, never a brand or a slogan, for 3
  to 7 s at the person's first appearance), `center`, `super` (a big centre word or two: the spoken words or their
  keyword, never a different message in a voice-led stretch; in a text-led stretch a new proof point from the
  brief, section 6), `cta` (the call to action in a holder), `end_card` (the card's text: the brand name). A title
  takes its own `"y"` (a fraction of the height) when it must move; every box is kept inside the safe box (the
  vertical safe boxes end at 65 % of the height, so a vertical card stacks the name at y 0.47 and the CTA at 0.58,
  the mark above 0.44).
  `cta` and `end_card` titles centre on the frame (x = W / 2, on a centred logo), every other style on the safe
  box's centre (x 476 in `vertical_all`).
- Default sizes and looks of the shipped presets (font px on the delivery raster):

  | style | look | vertical (1080x1920) | landscape (1920x1080) | y |
  |---|---|---|---|---|
  | `hook_top` | `hook_clean` (no stroke, soft shadow) | 96 | 80 | 0.20 (0.14 landscape) |
  | `super` | `super_clean` (caps, no stroke, soft shadow) | 88 | 72 | 0.45 |
  | `cta` | `cta_card` (dark text on a white pill) | 84 | 72 | 0.58 (0.74 landscape) |
  | `end_card` | `end_card` (white, soft shadow) | 64 | 60 | 0.47 (0.62 landscape) |
  | `lower_third` | `lower_clean` (white on a soft dark box, no stroke) | 54 | 44 | 0.62 to 0.80 |
  | captions (word chunks) | `caption_soft` | 84 | 64 (sentences) | `captions.y_band` |
- Looks: the preset's `looks` define how text is drawn (in em of the font size): `{"font": "Arial" | "brand",
  "style", "font_file", "case": "as_written" | "upper" | "sentence", "color": "#FFFFFF" | "brand.primary", "stroke":
  {"color", "em"} | null, "shadow": {"color", "opacity"} | null, "box": {"color", "opacity", "pad_em": [x, y],
  "round"} | null, "line_spacing", "align"}`. Each title style names its look (`titles.<style>.look`: `hook_clean`,
  `lower_clean`, `super_clean`, `cta_card`, `end_card`), captions use `captions.look` (`caption_soft` in every preset
  with captions, `caption_clean_box` in product_demo). The four clean looks: `hook_clean` (brand font, as written, white, no
  stroke, shadow 0.45), `super_clean` (brand font, capitals, white, no stroke, shadow 0.5), `caption_soft` (Arial
  Bold, white, stroke 0.04 em, shadow 0.55) and `cta_card` (brand font, #111111 on a white pill, padding 0.6 by 0.3
  em, fully round); a name plate takes `lower_clean` (brand font, white on a black box at 0.6 opacity, padding 0.4
  by 0.2 em, round 0.15, no stroke). The outlined looks of earlier versions (`boxed`, `hook_bold`, `super_heavy`,
  `cta_pill`, `caption_bold`) stay in every preset for a cut list that names them, and the animated styles add four looks:
  `caption_heavy`, `caption_clean_box`, `caption_karaoke` and `super_black` (the table below). The preview draws
  the look and the build maps it to Text+, so what the judges see is what Resolve shows.
- The brand kit: `LAB/brand.json` (`resolve-editor/brand@1`: `name`, `font` {`family`, `style`, `file`},
  `caption_font`, `colors` {`primary`, `text`, `dark`}, `logo` and `wordmark` media ids, `handle`, `url`,
  `cta_button`). A look's "brand" font and "brand.<colour>" tokens come from it; without it they fall back to Arial
  Bold and white. Only the main session writes it, from the user's answers.
- One type system per piece [S, G numbers; V for the failure: four text treatments and a hook smaller than the
  later supers were the most cited text faults of the trial ad]: one look per role (the preset's role map: hook,
  name plate, super, CTA, captions), no more than 3 distinct look ids outside the end card beyond that map, the
  same look for the same role throughout, one message at a time, no grey debug boxes on a brand piece.
  - Clean by default: titles have no stroke; captions at most 0.04 em. A stroke over 0.05 em only where a busy or
    light background defeats the shadow (`text_contrast` measures it when the checks list it, or the cut sheets
    show it), and then on every title of that role. A premium piece never uses a look with a stroke over 0.05 em;
    it takes the clean looks above (the premium swap changes only the motion, so the looks must already be clean).
  - The hook is the largest text before the card: `hook_top` 96 px against supers at 88 px and captions at 84 px
    on a vertical frame. A title with role `hook` is never smaller than a later title outside the card.
  - `type_system` (WARN) fires when the hook is smaller than a later title outside the card, when a premium piece
    has a title or caption look with a stroke over 0.05 em, or when titles and captions outside the card use more
    than 3 look ids and the cut list chose some of them on top of the preset's role map (the preset's own look for
    each title style in use, and its caption look, never count against it).
- Two titles never say the same thing: `title_repeat` (WARN) fires on two titles with the same text (letters and
  digits, any case) unless one is tagged `callback` or has the role `brand` or `cta` (the name at the start and on
  the card, and the call to action before the card and again on it, are fine). The offer is shown once as a super.
- Titles that follow each other on one track meet edge to edge: assemble rounds each title's end time (not its
  length), so chained titles leave no gap, and closes a gap of 1 to 3 frames between two titles on one track (the
  earlier one runs on to the later one's start, note `title_gap_closed`). Where two titles meet, the text swaps in
  place: when the earlier title's exit and the later one's entrance would leave the text off at the join, assemble
  skips both (note `title_chain_joined`; a title that then moves nothing goes static). `title_blink` (WARN) catches
  what is left: two consecutive titles on one track with the text fully off (no title, or alpha under 0.5 inside
  their fades) for 1 to 6 frames, with a gap of 0 to 6 frames between them, which reads as a blink. To keep the
  fades, leave a real pause of 8 frames or more.
- The hook title and the first spoken line share the first 3 s: make them say the same thing or build on each
  other, never two different messages (`text_competes`). A caption that repeats the title shown at the same time
  is noise (`caption_title_dup`). `text_overlap` (WARN) flags a title and a caption on top of each other.
- Never put a word on screen that the brief, the script or the transcript does not give: no invented prices,
  dates, claims or names.

### End cards [S, V for the failures]
- 3.5 to 5 s, on the music's button (its last hit or its natural end), inside the safe box.
- The mark with the brand name (the logo plus the wordmark, or the name set in the brand font) at 40 to 60 % of the
  frame's width; a logo that is only a symbol does not say who the brand is.
- The name and the call to action are locked up together for the whole card: both titles start on the card's
  first frame (or the CTA within its first 0.5 s) and run to its last. Two titles at once go on two title tracks:
  assemble moves a title that overlaps another onto a second track named "Titles 2" (note `title_track_2`); three
  titles at once is an error. Never play the name and then the CTA one after the other.
- One call-to-action line as the largest text on the card (`cta` 84 px against the name's 64 px on a vertical
  frame), in the `cta_card` holder (dark text on a white pill), with its route (link in bio, URL, phone, button),
  held 2.5 s or more and to the last frame; the handle or URL once.
- The spoken call to action plays on the card from its first frame, and its captions are suppressed under the
  title (`"suppress_under_titles": true`): no caption repeats the card's text.
- Centred on the logo: the card's text centres at x = W / 2 (540 on a vertical frame), where a centred logo sits;
  assemble centres `cta` and `end_card` titles there (the `vertical_all` safe box is centred at x 476, which put
  the trial card's text 63 px left of its logo).
- Designed motion: the logo pushes from 1.00 to 1.03 over the card when the card is one item (`"fx": [{"kind": "push",
  "from": 1.0, "to": 1.03}]` on the still's item, section 15; a card split into an overlay and a V1 tail stays still,
  or the push would jump at the seam); when the logo sits on V1 after a picture, it dissolves in over 6 to 8 frames
  (`"transitions": [{"after": "<the segment before the card>", "type": "cross_dissolve", "frames": 7}]`; the outgoing
  shot needs that many frames of handle). The name and the CTA fade in (`fade`, the presets' default for `cta` and
  `end_card`). No pop, bounce or slide on the card.
- `end_card` (WARN) checks the lockup. A piece has a card when the last V1 item is a still that runs to the end and
  is a card (a logo: tagged `logo` or the brand's logo or wordmark media; or a still under a title of style
  `end_card` or `cta`, or role `brand` or `cta`; a photo montage's last photo under a super is not a card), or a
  title with style `end_card` or `cta` starts in the last 5 s; the card starts at the earlier of the two, and its
  titles are those that start on its first frame (or up to 0.2 s before). It fires when the CTA (role `cta`) is on
  screen for less than `min(2.5 s, 60 % of the card)` ending at the programme end; when the brand name (role `brand`)
  and the CTA are on screen together for less than 1.5 s; when the CTA's `font_px` is smaller than another card
  title's; or when a card title's box centre is more than 12 px from W / 2.
- A worked card for a voice-over ad (vertical; media ids `logo` and `vo`; the voice's last line, words 29 to 34, "Book
  your free studio visit today", is the call to action; the card is that line plus a ring-out, 5 s in all). The logo
  is an overlay over the voice segment, so the spoken call to action plays on the card from its first frame, and a
  picture-only V1 segment of the same still, continuing it (`in_s` = the overlay's `in_s` plus its length), carries
  the ring-out:

      "spine": [
        ...,
        {"id": "s12", "media": "vo", "words": [29, 34], "pad_ms": [60, 120],
         "why": "the spoken call to action, on the card from its first frame"},
        {"id": "s13", "media": "logo", "in_s": <s12's length>, "dur_s": <5.0 minus s12's length>, "zoom": 0.43,
         "tilt_px": 300, "audio": "none", "why": "the card holds on the music's last hit"}],
      "overlays": [
        {"id": "o12", "media": "logo", "in_s": 0.0, "at": {"seg": "s12", "offset_s": 0.0}, "dur_s": <s12's length>,
         "zoom": 0.43, "tilt_px": 300, "why": "the logo card under the whole call to action"}],
      "titles": [
        {"id": "t20", "text": "Ink Studio", "style": "end_card", "role": "brand",
         "at": {"seg": "s12", "offset_s": 0.0}, "dur_s": 5.0,
         "why": "the name, locked up with the CTA for the whole card"},
        {"id": "t21", "text": "Book your\nfree visit", "style": "cta", "role": "cta",
         "at": {"seg": "s12", "offset_s": 0.0}, "dur_s": 5.0,
         "why": "the largest text on the card, with the voice's call to action"}],
      "captions": {"from": "dialogue", "suppress_under_titles": true,
                   "text_fix": {"vo:29": "", "vo:30": "", "vo:31": "", "vo:32": "", "vo:33": "", "vo:34": ""}}

  Read s12's length from the EDL after a first assemble (or `E items`) and fill it in. The two titles overlap for
  the whole card, so the CTA goes to "Titles 2"; both centre on x 540 and fade in. Keep the card's CTA to 2 lines of
  at most 12 characters at 84 px (write the break as "\n"; a third line pushes the pill past the safe box, a
  `safe_zone` STOP), with the route on the card or in the platform's button. The captions: `suppress_under_titles`
  drops a cue whose words are mostly on a title, and `text_fix` with "" hides the rest of the spoken call to action
  in the captions only (the voice keeps every word), so no caption runs under the card (`text_overlap`). Keep the mark
  above 0.44 of the height (`tilt_px` about 300 at zoom 0.43), clear of the name at 0.47, and the card 5 s or
  shorter: the titles must start in the last 5 s to count as the card. Tested on a 30 s trial ad: no `end_card`,
  `type_system`, `safe_zone` or `text_overlap` finding. A card without the voice (a music piece) is simpler: the
  logo still is a V1 spine segment with `dur_s`, entered with a 6 to 8 frame `cross_dissolve`, and both titles
  start with it.
- A still's edge rows must match its background, or a thin line shows at the edge (`still_edge`, measured on the
  still as Resolve draws it: a transparent border is no line).
- A logo with a transparent background (PNG with alpha) shows the pictures under it in Resolve, and the preview
  draws it the same way: over the item on the track below, or over black when nothing is under it. A zoomed-out
  overlay (zoom under 1, or a still that does not fill the frame) also shows the track below around it.
- A landscape logo on black for a vertical card: put the still on V1 as a spine segment with `media` (the image's
  media id, not a shot id: stills have no shots) and `dur_s`, and shrink it with `zoom` until the mark is 40 to 60 %
  of the frame's width. With the 1080x1920 timeline cropping a 1920x1080 image to 3413 px wide, a mark that spans
  37 % of the image needs zoom = 0.5 x 1080 / (0.37 x 3413), about 0.43; `tilt_px` lifts it (a positive tilt moves
  it up) to leave room for the call to action. Nothing is needed under it, and no solid: the frame around a
  black-bordered still is black, so `frame_edge` allows it. Example:
  `{"id": "s14", "media": "logo", "dur_s": 3.5, "zoom": 0.43, "tilt_px": 200, "audio": "none", "why": "end card"}`.
  A light or coloured background cannot shrink on V1 (its edge would show on black): ask for a vertical logo,
  or set the brand name as text on a card.
- Brand Reels without an end card still end with a 1 to 2 s call-to-action super. The last image is the strongest
  image of the work, or a face.

### Animated captions and titles [V build and timing, S taste, G numbers]
Captions and titles can move: `"captions": {"from": "dialogue", "anim": "<id>"}` and a title's `"anim": "<id>"`
(or `"none"`). Left out, each takes the preset's default (`captions.anim`, `titles.<style>.anim`; section 11), and
the preset's `anims` block holds the numbers. This includes a cut list written before animations existed: assembled
again, its captions and titles take the preset's default motion; write `"anim": "none"` to keep them still. A
default (never an anim the cut list names) that would leave a title less than its reading floor once it is whole
falls back to `fade`, then to no motion, and the assemble notes say so (`fx_anims`). The motion follows the
transcript's word frames: nobody times it by hand. Sizes are em px on 1080x1920, timings at 25 fps.

| anim | for | what moves | look that fits |
|---|---|---|---|
| `pop_highlight` | captions, the preset's chunks (1 to 4 words) | the chunk pops 0.85 to 1.04 at 100 ms and settles at 1.0 at 160 ms (frames 0, 2 and 4 at 25 fps); the spoken word turns #FFD93D on its frame | `caption_soft` (Arial Bold 84 px, sentence case, stroke 0.04 em, soft shadow), the presets' default; `caption_heavy` (ALL CAPS Arial Black, outline 0.10 em) only when the brief asks for a loud creator style |
| `word_pop` | captions, the preset's chunks (1 to 4 words) | each word appears on its spoken frame at 0.9, reaches 1.02 at 100 ms and 1.0 at 160 ms; the layout never re-flows (word spacing 1.3) | `caption_soft` |
| `keyword` | captions | static; one keyword per cue in the accent colour and 15 % larger (the line re-flows) | `caption_soft` |
| `clean_box` | captions, calm sentences | a 65 % black box per line, the group fades in over 3 frames | `caption_clean_box` (Arial Bold 64 px): assemble uses it whenever `clean_box` plays in a look without a box (the preset's `caption_bold`, a premium swap) and the cut list names no look |
| `box_karaoke` (P1) | captions, 2 to 3 caps words | the spoken word sits on a rounded box in the brand colour that grows 1.0, 1.12, 1.06 | `caption_karaoke` (Arial Black 66 px) |
| `super_pop` | an offer or a number super; the 1 to 3 word text hook of a music montage (the `hook_top` default of reels_montage_music and music_video) | scale 0, 1.15, 0.96, 1.0 at frames 0, 4, 7, 9 | `super_clean` (the presets' default), or `hook_clean` for a montage's hook |
| `slide_hook` | the hook title | slides up 40 px with an ease-out over 8 frames, fades in over 5 and out over the last 4 | `hook_clean` |
| `typewriter` | the hook title | types on at 20 characters per second (the reading speed of this section) | `hook_clean` |
| `fade` | any title | fades in over 4 frames and out over 3 | the style's own look |

- A look is not part of the anim: set `captions.look` (or a title's style look) to the look that fits, or the anim
  plays in the preset's default look. One exception: `clean_box` is a fade into a box, so where it plays (named, the
  preset's default or a premium swap) with no look named and the preset's caption look has no box, assemble uses
  `caption_clean_box` and the notes say so (`fx_anims`).
- A title on the programme's first frame starts whole: the hook text is on screen from frame 1 and the first frame
  is the cover (H7, section 12), so the preset's default animation skips its entrance (fade in, slide, pop from 0,
  typing) there and keeps its exit; a default `super_pop` or `typewriter` there plays still. The assemble notes say
  so (`fx_anims`); to keep an entrance, start the title a frame or more later. An animation the cut list names stays
  as written, and `first_frame_text` (WARN) flags a first-frame title that is not whole on that frame.
- Effects appetite `none` switches the preset's default caption and title animations off too (an animation the cut
  list names stays), and the notes say so (`fx_anims`). Keywords for `keyword` come from `"captions": {"keywords":
  ["<media>:<word index>"]}` (or the literal word): at most one per cue and on about 30 % of cues (`caption_keywords`,
  WARN).
- Taste [S, G numbers]: a caption entrance takes 100 to 160 ms (2 to 4 frames at 25 fps, 3 to 5 at 30), starts at
  0.85 or larger and overshoots by at most 0.05 (the judges marked a word growing from 55 % to 108 % down as
  cheap); a title pop at most 0.30 and 400 ms, on 3 words or fewer. A caption never moves after
  its entrance: text that moves while it must be read is the first cheapener (`fx_text_motion`, WARN). Premium and
  cinematic pieces use `clean_box` or `keyword` captions and `fade` or `slide_hook` titles, never a bounce. A
  typewriter hook finishes typing inside the first 3 s.
- Reading floor of animated text: `max(0.833 s, chars/20, words x 0.33 s)` counts from the frame the whole text is
  visible, not from its first frame (`anim_read_floor`, STOP, the same level as `caption_timing`), for titles. Word-
  reveal captions (`word_pop`: words appear one by one; `box_karaoke` shows the whole line at once and only moves a
  box) are timed by the speech, so their floor is the cue's own `min_s` (0.6 s) from its first word, and
  `reveal_hold` (WARN) adds: a word-reveal cue followed by a pause (0.25 s or more before the next cue) or ending a
  sentence is whole (every word shown) for at least 0.4 s, and the programme's last caption is whole for at least
  0.8 s. A cue in the middle of a fluent sentence is whole only until the next word starts the next cue, so it is
  not judged. A payoff line that cannot meet that
  goes in one cue held to the end (`keep_together`), or its last word gets a longer `pad_ms` tail. The chunking
  rules and the 0.6 s floor of social captions stay as above.
- Keywords and `word_pop`: `captions.keywords` work only with `keyword`, `clean_box` and `fade` captions; with
  `word_pop` or `pop_highlight` they are ignored (assemble note `keywords_ignored`). The caption anim wins: to mark
  a keyword in a word-reveal piece, switch the piece to `keyword` captions (one style per piece), never mix.
- Safe zone at the largest scale: `safe_zone` is measured at the animation's biggest frame (a pop's overshoot
  included), so a pop that fits at rest can still STOP.
- Size per font [V, sandbox]: the build sets Text+ Size from the font's own metrics (section 1), so a brand font is
  drawn at the em size the preview uses; a font whose metrics cannot be read falls back to the Arial Bold ratio and
  warns (`text_font_metrics`).
- The animated cues are still Text+ on the graphics timeline (never a subtitle track); verify checks each animated
  Text+'s keys (`text_anim_missing`, STOP), and the proof render compares the text frame by frame.

### Checks
- `caption_timing` (STOP): a caption cue shorter than `captions.min_s`, or above `captions.cps_max` characters per
  second when set; a read-only title shorter than `max(0.833 s, chars/20, words x 0.33 s)`.
- `caption_layout` (STOP): more than `captions.max_lines` lines or `captions.max_chars_line` characters per line.
- `safe_zone` (STOP): a title or caption box outside the platform safe box below (an animated one at its largest
  scale).
- `anim_read_floor` (STOP), `fx_text_motion`, `caption_keywords` and `text_font_metrics` (WARN): above.
- `caption_jump`, `type_system`, `title_repeat`, `title_blink`, `end_card` and `reveal_hold` (WARN): above.
- `caption_unverified`, `caption_title_dup`, `text_competes` (above); `message_gap` and `offer_late` (section 11).

### Safe zones (keep text, logos, faces and key action inside) [V for Meta and Google, S for TikTok]
| platform key | aspect | safe box x, y, w, h at the render size | source |
|---|---|---|---|
| `reels` | 9:16, 1080x1920 | 65, 269, 950, 979 | Meta: 14 % top, 35 % bottom, 6 % each side free |
| `tiktok` | 9:16, 1080x1920 | 44, 130, 896, 1306 | TikTok template via secondary sources; long captions push the bottom up |
| `shorts` | 9:16, 1080x1920 | 48, 288, 840, 960 | Google's vertical overlay, measured pixel exact |
| `vertical_all` | 9:16, 1080x1920 | 65, 288, 823, 960 | inside all three (the intersection of the boxes, so the union of their margins; default when unsure) |
| `youtube` | 16:9, 1920x1080 | 96, 54, 1728, 972 | title safe 90 % (organic uploads) |
| `linkedin` | any | title safe 90 % of the master | |
| `x` | any | title safe 90 % | |
| `square` | 1:1, 1080x1080 | 48, 48, 931, 642 | Google's square overlay |

Boxes scale linearly with the raster. The `vertical_all` box is not centred (its centre is x 476): captions and most
titles centre there, while `cta` and `end_card` titles centre at x 540 (on the logo), where a box can be at most 694
px wide to stay inside it [V]. Platform interfaces change; re-check before a big launch.

---------------------------------------------------------------------------------------------------------------

## 11. Genre playbooks (one per preset)

Preset keys: `platform` (a safe-zone key above), `timeline` (delivery raster; a Resolve project of the same shape
keeps its own raster, so a UHD project gets a UHD edit; `fps: "project"` uses the Resolve project rate), `length_s`
(min, target, max; `duration` STOP outside min to max), `hook` (section 5), `pacing` (section 4), `speech` (section
6), `spine_audio` (audio of spine segments that are not word ranges: `dialogue`, `nat` at about -12 dB, or `none`),
`music` (`use` optional, required or none; `cut_on`; `beat_tol_frames`; `duck_lu`; `duck_hold_s`; `duck_max_db`; `end`
resolve, fade or hard_ok; section 9), `captions` (section 10: also `font_px`, `look`, `verify` stop or warn for
`caption_unverified`, `deliver` burn or file), `titles` (per style: `y`, `font_px`, `max_chars_line`, `look`, `anim`),
`looks` (section 10), `message` (below), `transitions` (section 8: `allowed` lists the catalogue keys and the generic
`whip` and `zoom` this kind of piece may use), `audio` (section 9), `checks` (`off` turns checks off, `soft` lists the
taste checks the judges weigh), `workflow` (default story angles and cut styles), `judging.weights` (section 13), and
for effects (section 15): `fx` (`genre`, `per10s` effect events per 10 s, `max_families` stylized families, `zoom_max`
the total zoom cap, `allowed` the keyed kinds that fit, `sfx_style`), `anims` (the numbers of every caption and title
animation) and `captions.anim`, the default caption animation.

Effects budgets scale with the brief: the effects appetite (`none`, `light`, `normal`, `bold`) multiplies `per10s` by
0, 0.5, 1 or 1.25, and a premium or luxury brand (`premium: true`) moves a preset to the `premium_brand` budget (a
music montage or music video to `cinematic_montage` instead, so the cuts still ride the music) with `clean_box` or
`keyword` captions and `fade` or `slide_hook` titles. Both answers come from the brief and sit in `project.json` as
`premium` and `fx_appetite` (`E init --premium yes|no --fx none|light|normal|bold`); a cut list may repeat them as
top-level `"premium"` and `"fx_appetite"` to try a variant, and the EDL's `fx_meta` records the genre, premium and
appetite assemble used. `premium` is true or false; the words `yes`, `no`, `true` and `false` in any case are read as
such, and anything else is refused (a string `"no"` once read as true). Appetite `none` also switches the preset's
default caption and title animations off; one the cut list names stays.

The `message` block (ads, demos and reels): `gap_max_s` is the longest stretch between the first and the last word
with no voice, title or caption (`message_gap`, WARN; pieces without dialogue skip it), and `offer_by` is the
latest point, as a fraction of the length, where the first title with role `offer` may start (`offer_late`, WARN,
also when no title has role `offer`; null turns it off). Presets without the block run neither check.

### reels_talking_head (Reels, 15 to 90 s, target 45)
Hook on frame 1: the strongest line or result, text on screen by 3 s. Visual change every 2 to 5 s (punch-ins,
B-roll, text). Tight pauses (150 to 450 ms), fillers out, word-chunk captions (84 px, `caption_soft`) in one band at
50 to 61 % of the height. Music optional, 12 LU under the voice with a duck of at most 10 dB. A voice-over piece
keeps 0.3 to 0.6 s between lines, at most one lifted musical breath of about 1.2 s per musical hit (`vo_gap`), and
ends 0.5 to 1.0 s after the last word (`ending_tight` under 0.5 s, `ending_tail` past 1.2 s) with the payoff line in
one caption cue held to the end (`reveal_hold`). End on the payoff or loop back to the first line; no end card over
about 1 s. A brand Reel ends with a 1 to 2 s call-to-action super; when the brief gives no route (link in bio, URL,
handle), leave the super out and list it in `open_actions` rather than invent one.
From 16:9 footage give every talking segment the speaker's `frame_x` (section 6). The densest-window rule is off
(`densest_window_after` 0): a hook-first piece is densest at its start on purpose.
Effects (`creator_reel`: 5 events per 10 s, 3 stylized families, total zoom 1.35): a keyed punch on an emphasis word
every 2 to 4 s alternating with the plain framing, `word_pop` captions, a `slide_hook` hook title, `super_pop`
supers, one freeze beat at most, a whoosh or a hit under whips and punches. Transitions: cuts, plus a whip, a zoom, a
Brightness Flash, or a smooth cut inside one take.

### reels_montage_music (Reels or TikTok, 7 to 45 s, target 20)
No speech. A text hook inside the safe box by 3 s, in `hook_clean` at 96 px (a refined 2 to 3 word line, no
outline), on the first hit. Shots 0.4 to 1.8 s median, cut to the beat grid in 2 or 4 bar blocks, strongest image
on the biggest hit, native or handheld feel often beats polish on TikTok [V]. The default styles are tight first,
then montage: on the trial pieces the tight cut won every pairing.
- The shape [S, V for the failures]: open on the drop with motion in the first 0.4 s (the first frame's sound at
  body level: `music_open_quiet`; a pickup of at most 2 frames); build into the payoff, with the second half's
  shots as short as or shorter than the first half's (`pace_decel`: 1 to 2 beat cuts through the build, never 4 to
  6 beat holds before the end); only the final shot opens up, for at most one bar or 2 s.
- The music: pick a window of full bars (from `bar_db` when the beats file has it) that ends on a hit or on the
  song's own ending at full energy; never ride a fill, a breakdown or a fade over a thinning section into the end
  (`music_lull`). The last image lands on the final downbeat: the finished result (a reveal) or a face.
- A brand Reel ends with a 1 to 2 s call-to-action super; the last image is the strongest image of the work, or a
  face.
Effects (`music_montage`: 8 events per 10 s, 3 stylized families, total zoom 1.5): bumps on downbeats in 2 or 4 bar
blocks, a ramp into the drop, a shake only on the biggest hit, whips and zooms between shots that move the same way,
a glitch or an RGB split only for electronic or hip-hop music; a `super_pop` text hook, no captions. Never an
effect on every beat.

### shorts_highlight (Shorts, Reels, TikTok, 20 to 90 s, target 45)
One complete idea clipped from a long video: start and end on sentence boundaries with a complete thought, a hook
line in the first 3 s, no dangling references ("as I said earlier"), captions. For Shorts, connection first: a
real person, direct to camera, tight framing (subject filling 60 % or more of the frame) [V]. The adversarial
reviewer looks for missing context and a cut punchline.
Effects (`talking_head`: 3 events per 10 s, 2 stylized families, total zoom 1.30; also podcast_clip and
youtube_long): punches on emphasis words, a slow push on long takes, `pop_highlight` captions, a `slide_hook` hook;
a zoom transition or a smooth cut inside one take. No flash, glitch, RGB split, shake or light leak.

### youtube_long (YouTube, 3 to 30 min, target 10)
The first 30 s restate the promise and start delivering; no long channel intro; show something from the best part
early. A visual change every 3 to 8 s in talking segments, re-engagement beats every few minutes, segments that
open with a mini hook. Music ducked 20 LU under talk. Find likely drop-off points (long static stretches, repeated
information, slow tangents) and cut them.
Effects (`talking_head`): punches on emphasis words and slow pushes keep long takes alive; captions go as a file (no
caption animation); `fade` titles; a blur dissolve or a smooth cut inside one take.

### interview_doc (documentary or interview, 1 to 15 min, target 4)
Build from the transcript first (a paper edit), then picture. Bites of 5 to 20 s; emotional lines stay on the
face; B-roll covers setups, transitions and jump cuts (2 to 4 s factual, 6 to 10 s emotional); J and L cuts
between bites; room tone under every dialogue edit; lower thirds at first appearance for 3 to 7 s. Music
restrained: do not underline every emotion. Pacing arc: shots lengthen in the setup and after the climax.
Effects (`premium_brand`: 1.5 events per 10 s, 1 stylized family, total zoom 1.10): slow pushes, a ramp into 40 to
60 % slow motion on the hero moment, at most one warm light leak; `fade` titles and lower thirds; blur dissolves.
No punches over the zoom cap, bumps, shakes, flashes, glitches or splits.

### podcast_clip (video podcast clip, 20 to 90 s, target 60)
One complete idea with the hook line first, captions. Switch angles on real speaker changes, not on "yeah" or
"right" (no switch for backchannels under about 1 s); the wide two-shot for crosstalk and laughter; cut to the
listener for a strong reaction. Minimum about 2 s per angle, at most about 20 to 30 s on one angle before a
variation. Keep the conversational rhythm while removing long silences. Each angle plays its own camera's sound
(this version cannot sync separately recorded sound): switch angles only when all cameras recorded the same mix,
or the sound changes at every switch.
Effects (`talking_head`): as shorts_highlight; a punch on the punchline, `pop_highlight` captions.

### ad_15 and ad_30 (ads, exactly 15 or 30 s)
One message. First shot under 3 s, product or brand in the first 5 s, supers that match the voice, call to
action on screen and in the voice, works muted. 15 s: about 30 to 40 words of voice-over and 8 to 15 shots; 30 s:
about 60 to 75 words at 2.5 words per second (2 for luxury, over 3 for retail) [S]. The `abcd` check (WARN) is on
for these presets. Plan music in 2 or 4 bar blocks and land the end on a downbeat. The length check allows at
most half a frame over the nominal length (at 23.976 fps a 15 s ad is 359 or 360 frames); never run long.
- The offer or reason to act appears (voice or super) by 40 % of the length: 6 s in a 15 s ad, 12 s in a 30 s ad
  (`offer_late`). An offer in the last third loses most viewers before it lands.
- The call to action appears once before the end card and again on it, in text and in the voice, with a route
  (link in bio, URL, phone, the platform's button); held 3 s or more in total, 2.5 s or more of it on the card,
  locked up with the brand name (section 10, End cards; `end_card`). Give both CTA titles `"role": "cta"` (the
  `cta` style's default): `title_repeat` leaves a repeated call to action alone.
- No stretch longer than 1.5 s without voice or text (`message_gap`, `message.gap_max_s` 1.5 in the ad presets;
  judges marked a 2.1 s empty stretch as dead air).
- The order: the hook, the problem or desire, the proof, the offer, then the call to action. Proof after the offer
  undercuts it. The offer is said once and shown once as a super (`title_repeat` warns on a repeated title).
- One type system: the hook is the boldest and largest text before the card (`hook_clean` 96 px against supers in
  `super_clean` at 88 px), one look per role and no extra looks outside the card (`type_system`).
- The brand appears at the start (in the first 5 s: a mark, a super or the voice) and on the card.
- Captions: a wrong word is a ship blocker, so `caption_unverified` STOPs in the ad presets. Music: 10 to 13 LU
  under the voice with its low end, a duck of at most 10 dB, the drop in a voice gap, a stop for the offer of at
  most one bar, and no lull after it (section 7; `speech_music_gap` both ways, `music_lull`).
- Fit check before cutting: a voice-over whose speech is under about 60 % of the length, or under the word count
  above, does not fill the ad. The main session offers the user a shorter ad (15 or 20 s), a text-led stretch with
  supers the team writes, or a longer voice-over; the brief records the choice. Without a choice the default is to
  spread the voice lines across the whole length with music between them (0.3 to 0.6 s gaps, one lifted breath of
  about 1.2 s per musical hit); it reaches at most speech + 0.6 s per gap + 1.2 s per hit + the card (section 6),
  and supers over the longer gaps carry the rest. A text-led stretch shows fresh setups (no media from the first half: `recycled_half`
  warns past 35 % of the second half's picture time and names unused shots scored 6 or more) and new proof points
  from the brief (a number, a guarantee, a name), never the voice's words again.
- Effects (`performance_ad`: 4 events per 10 s, 3 stylized families, total zoom 1.35): punches on the claims,
  `pop_highlight` captions (`clean_box` when the brand is premium), a `slide_hook` hook, a `super_pop` offer super with
  a pop under it, a zoom through the cut into the product. No shake, flash, RGB split or glitch on a shot that shows
  the logo, the product name or the offer: tag it `logo`, `product` or `offer` (shots of brand.json's logo and
  wordmark media count without a tag), and such an accent there, or under a title with the role offer, brand or proof,
  is a STOP (`fx_text_shake`); a punch or a slow push on the product stays allowed. No picture effect on a title (also
  `fx_text_shake`), never a transition effect in the first second (`fx_hook`).

### product_demo (demo or explainer, 45 to 120 s, target 75)
Problem briefly, the product solving it as early as possible, the outcome, a call to action. Punch into the
active area of screen recordings; never leave the viewer hunting for the click; every step of text on screen for
the reading floor. The product or key detail is recognisable when it matters (close-ups of 30 % or more of the
frame). Like the ads: the reason to act by 40 % of the length, no stretch over 1.5 s without voice or text, and a
wrong word on screen STOPs (`caption_unverified`).
Effects (`performance_ad`): a keyed `punch` with its `point` on the active area of a screen recording, `clean_box`
captions, a `slide_hook` hook, a zoom or a Fusion slide between steps.

### wedding_highlight (highlight film, 2 to 7 min, target 4)
Vows, letters and speeches are the spine: open with a strong emotional line (sound first, then picture), build
through the day, climax on the ceremony or first dance, end on the celebration or a quiet closing line. Music
carries emotion, ducked 20 dB under speech; cuts on phrases; slow motion only for emotional beats; never cut away
during the kiss, the ring or the key reaction. Dissolves allowed up to 15 % of edit points.
Effects (`cinematic_montage`: 2.5 events per 10 s, 2 stylized families, total zoom 1.15): ramps into slow motion on
emotional beats (ramps on 50 or 60 fps footage; a constant slow speed with Speed Warp when the footage is 25 or 30
fps), slow pushes, a warm light leak at most every 10 s,
blur or additive dissolves on time jumps; `fade` titles.

### music_video (the song's length)
Density follows the song's sections, faster in the chorus and slower in bridges; section changes on section
boundaries; dominant shot about 1 beat or 1 bar [V]. Anticipate downbeats by 0 to 2 frames; off-beat cuts only as
a deliberate surprise. This version cannot sync performance takes to the song: use lip-sync shots only where the
mouth is not visible, or leave the sync to the user in Resolve. Obey the flash rule.
Effects (`music_montage`): as reels_montage_music; glitch and RGB split (and their transitions) fit electronic and
hip-hop songs; every flash, glitch and flicker counts toward the flash rule.

### travel_montage (travel or place film, 30 s to 3 min, target 60 s)
Open on the most striking image or a moment, not a map or a title. Alternate energy: a fast run, then a breathing
shot (a landscape, a candid face). Ambience under the music creates place; location changes on musical phrases;
per location establish (wide), detail (close), people, action.
Effects (`cinematic_montage`): ramps into place reveals, whips and zooms between shots that move the same way, pushes
on landscapes, a light leak at most every 10 s; a `slide_hook` hook, `fade` supers.

---------------------------------------------------------------------------------------------------------------

## 12. Platform delivery

### Specs
| platform | aspect | render at | length notes | file |
|---|---|---|---|---|
| Instagram Reels | 9:16 | 1080x1920 | up to 20 min; over 3 min is not recommended to new audiences [V] | MP4 H.264 + AAC |
| Instagram or Facebook Reels ads | 9:16 | 1080x1920 is the common upload (the spec lists 1440x2560) | 0 s to 15 min | MP4 or MOV, H.264, fixed frame rate, stereo AAC 128 kb/s or more, at most 4 GB |
| TikTok | 9:16 | 1080x1920 (at least 720p) | uploads up to 60 min, short wins for reach | MP4 or MOV H.264 |
| YouTube Shorts | 9:16 or 1:1 | 1080x1920 (2160x3840 from 4K sources) | up to 3 min [V] | as YouTube |
| YouTube long form | 16:9 | 3840x2160 or 1920x1080 | 15 min unverified, 12 h verified | MP4 H.264 High, progressive, AAC-LC or Opus 48 kHz, BT.709, fast start |
| LinkedIn | 1:2.4 to 2.4:1 | 1920x1080, 1080x1920 or 1080x1080 | 10 to 15 min, at least 3 s | MP4 only, at most 30 Mb/s for Pages |
| X | 16:9, 1:1, 9:16 | 1920x1080 or 1080x1920 (Premium), else 1280x720 | 2 min 20 s standard | MP4 or MOV H.264 + AAC-LC, 512 MB standard |

YouTube upload bit rates [V] (SDR, 24 to 30 fps / 48 to 60 fps): 2160p 35 to 45 / 53 to 68 Mb/s, 1440p 16 / 24,
1080p 8 / 12, 720p 5 / 7.5. Upload at the frame rate it was shot at.

### Resolve Deliver settings (the user adds the render job, or you ask first)
| target | resolution | codec | bit rate | other |
|---|---|---|---|---|
| Reels, TikTok, vertical LinkedIn and X | 1080x1920 | MP4 H.264 High 8-bit (or H.265) | 20 to 40 Mb/s | AAC 48 kHz 320 kb/s; Network Optimization on |
| YouTube Shorts | 2160x3840 from a 4K source, else 1080x1920 | H.264 or H.265 | 45 to 68 Mb/s at 2160p, 10 to 20 at 1080p | |
| YouTube long form | 3840x2160 or 1920x1080 | H.264 High | at or above YouTube's table | multi-pass on when offered |
| LinkedIn | as the master | H.264 MP4 | at most 30 Mb/s | MP4 only |
| every file | the timeline frame rate | never 10-bit H.264 (it does not play on Apple devices) | | Advanced Settings: Color Space Tag Rec.709 and Gamma Tag Rec.709 (Scene) or Rec.709-A so the file is tagged 1-1-1; data levels Auto or Video; no timecode track for phone apps |
| captions built as Text+ (`captions.deliver` burn: Reels, TikTok, Shorts, ads) | | | | nothing to set: the cues are part of the picture; leave Export Subtitle off |
| captions as a file (`captions.deliver` file: YouTube long form, interviews) | | | | the SRT from `E export-srt`, uploaded with the video; the build makes no cues |
| loudness | | | | Deliver page, Audio tab: tick Normalize Audio Levels, choose Optimize to Standard at the preset's target (-14 LUFS for social) and true peak at the preset's `codec_tp_db` (-2 dBTP for an AAC or Opus file; -1 dBTP only for an uncompressed master); Normalize to Standard is gain only; the preview mix is not normalised |

- When `E commands` lists `deliver-script`, the main session can add the render job itself after asking the user:
  `E "LAB" deliver-script <VER> --platform <key>` writes a snippet that loads the H.264 master preset, sets the
  settings above on the built timeline and adds the job (it renders only with `--start`); its result says the
  gain the preview measure needs and how the loudness gets there. It never sets an upload option. It sets
  Data Burn-in to None and Export Subtitle off and checks both in the job: when Resolve refuses either, or the job
  reads back otherwise, the result is error `render_settings_refused` with the `keys`, and no job is left (a job
  that read back otherwise is deleted, `job_deleted`). Running it again gives the same answer: set those two on
  the Deliver page by hand, add the job there, and say so in the hand-over. H.264 High and multi-pass are asked
  for where the encoder offers them: an encoder that refuses them still gets its job (`prefer_refused` names them
  in a note); a proof render is one pass.
- Captions from an SRT in Resolve (only when the user wants a subtitle track of their own): File > Import >
  Subtitle, drag the clip onto the timeline by hand, then style it in the Inspector (Track tab: a bold sans font
  about 64 to 88 px on a 1080x1920 timeline, a stroke or a background, raised into the preset's `y_band`). Left at
  Resolve's default it sits near the bottom, inside the 35 % that the Reels, TikTok and Shorts interface covers.
  The skill itself never places a subtitle clip (section 1).
- Never tick "Upload directly" or any publishing option: the skill never posts anything.
- Resolve's TikTok preset renders 1920x1080 unless "Use Vertical Resolution" is ticked.
- Adding a render job changes the project: ask first, or give the user these settings as text.
- Check the export with `M "LAB" qc <file> --preset <id>` (the `format` and `tags` ids plus the audio ids of
  section 9 and `photosensitive`).

### Covers and thumbnails
Instagram's profile grid shows 3:4 tiles (1080x1440): a 9:16 cover loses its top and bottom 240 px there, so keep
cover text between y 240 and 1679 and inside the Reels safe box. YouTube thumbnails: 16:9, 3840x2160 recommended,
at least 640 px wide. Keep the first frame usable as a cover for short-form.

---------------------------------------------------------------------------------------------------------------

## 13. Scorecard, hard gates, ABCD checklist, adversarial prompts

### Scorecard (each dimension 0 to 10; weights from the preset's `judging.weights`, defaults below)
| key | dimension | default weight | questions | anchors |
|---|---|---|---|---|
| `emotion_story` | Emotion and story (Murch 1 and 2) | 25 | Does each cut serve the feeling and move the story? Do emotional lines stay on the face long enough? | 10: every key beat lands, nothing cut early. 5: story clear but some beats rushed. 0: confusing or flat |
| `narrative` | Narrative progression | 15 | One story? Understandable order? An effective opening? A resolved ending? | 10: clear hook, build, payoff. 0: disconnected shots or an abrupt end |
| `rhythm_pacing` | Rhythm and pacing | 15 | Pace fits the energy? Speed changes intentional? Density builds to the climax and relaxes after? | P1 to P5 and the preset |
| `audiovisual` | Audio-visual coordination and sound | 15 | Cuts to the beat effective? Sound matches action? J and L cuts bridge shots? Music builds? Silence on purpose? | M1 to M6, section 9 |
| `continuity` | Shot-to-shot continuity | 10 | Motion continuity, setting, light and colour continuity, transitions fit the tone | section 3 |
| `composition_graphics` | Composition and graphics | 10 | Framing right for the platform, captions placed and styled well and not over faces or product, enough variety | section 10 |
| `message` | Message and brand | 10 | Clear what this is and what to take away? Product visible when it matters? On brand? A call to action with a route? | 10: the offer by 40 % of the length, the call to action in text and voice held 3 s or more, the brand at the start and the end, a route. 5: the offer only in the last third, or no route. ABCD for ads |

Camera log footage (S-Log, V-Log, C-Log, flagged `flat_log`) looks flat and grey in the proxies, contact sheets
and preview, because the grade happens later: judge framing, focus, action, timing and emotion, never colour or
contrast, and do not prefer a shot because it looks less washed out.

Weighted score = sum(weight x dimension) / 100, from 0 to 10. Pass bar [G]: 7.5 or more and no hard gate failing.
Judges use the score to rank; the adversarial reviewers score the final and ask for a fix pass when it is under 7.5.
Every deduction quotes a timeline time and proposes a concrete fix (move a cut by N frames, swap shots A and B, drop
words [a, b], add a cutaway at t).

Score as a paying client would, not as the team that made it: in the trials the team's own scores ran 1.2 to 2.9
points above an outside panel's. Score from the brief, the report and the images first, and read the makers' notes
only afterwards. Caps (a dimension or the total can be at most this, whatever else is good):

| when | cap |
|---|---|
| a wrong word on screen (a misheard or misspelled word in a caption or title) | `message` at most 4 |
| the same framing 3 or more times (not a tagged callback) | `continuity` at most 6 |
| a must-have of the brief, or the named final image, missing | `narrative` at most 6 |
| the brief's must-avoid on screen | `narrative` at most 6 |
| the music's drop ducked more than 6 dB under a voice | `audiovisual` at most 7 |
| lip flap or an off-screen look at the emotional peak | `emotion_story` at most 6 |
| an open ship blocker (a wrong word on screen, an offer after half the length, a call to action with no route, a proof line on a picture that does not show it) | the weighted score at most 6.5 |
| text that moves while it must be read, or an effect on the logo, the product name or the offer | `composition_graphics` at most 5 |

Effects are scored inside the dimensions they touch, never as a dimension of their own [S, G anchors]: a punch, a
bump or a ramp counts in `rhythm_pacing` and `audiovisual` (does it land on the word or the beat, does it serve the
moment), a transition in `continuity` (does it fit the tone and the motion on both sides), animated text and every
effect's look in `composition_graphics`. An effect earns its place when it serves one moment of the brief (the
emphasis word, the drop, the reveal, the offer) and the piece's genre budget (section 15); remove it and that moment
gets weaker. Cheapeners, each one a deduction: an effect on more than 30 % of edit points or the same transition on 4
cuts in a row (`fx_repeat`), more stylized families than the genre allows (`fx_families`), an effect kind the genre
avoids (`fx_genre`), events over the budget (`fx_density`), bounce or overshoot on premium titles, a transition in
the first second (`fx_hook`), a bump that misses the beat (`fx_offbeat`), a zoom past the genre cap or the source's
sharpness (`fx_zoom`, `fx_soft`), a P2 transition the brief did not ask for (`transition_amateur`). Judge an effect
from the preview's frames, and remember that frames marked approximate in report.txt and on the overview are a
stand-in for Resolve's look, not the exact picture.

### Hard gates (STOP, all measured)
| gate | check id |
|---|---|
| photosensitivity: at most 3 flashes in any 1 s | `photosensitive` |
| no dialogue word cut inside its duration (an edge no frame can place between two words, at most one source frame, is `rate_edge`, WARN; camera sound under a picture segment and `low_conf` words are WARN) | `clipped_word` |
| every dialogue item plays exactly the words the cut list asked for | `words_changed` |
| no black edge from a pan, tilt or zoom | `frame_edge` |
| the edit runs at the Resolve project's frame rate | `fps_mismatch` |
| static text meant to be read stays up for its floor | `caption_timing` |
| no unconfirmed word on screen (ads and demos; a WARN in other presets) | `caption_unverified` |
| every title and caption the EDL has is on the built timeline with its text | `text_missing`, `text_off` (verify) |
| captions and key text inside the platform safe zone | `safe_zone`, `caption_layout` |
| loudness on target, true peak under the ceiling | `loudness`, `true_peak` |
| no digital silence gaps inside the programme | `silence_gap`, `dropout` |
| music does not stop mid-phrase | `music_end` |
| short-form: something changes and the proposition lands by 3 s | `hook_3s` |
| no flash frames, no black gaps (a deliberate `intentional_flash` under 6 frames is a WARN) | `flash_frame`, `black_gap` |
| length inside the preset range | `duration` |
| the EDL is valid and its media online | `edl_invalid`, `offline_media`, `preview_frames` |
| lip sync within +45 / -125 ms | kept by construction for dialogue items (picture and sound from the same file); not available for separately recorded sound or lip sync to a song (this version has no audio sync) |
| at most 3 flash events (flashes, glitches, flickering splits, Brightness Flash transitions) in any 1 s of the EDL | `fx_flash_rule` |
| no picture effect on a title; no shake, flash, RGB split or glitch on a shot tagged `logo`, `product` or `offer`, a shot of brand.json's logo or wordmark, or under an offer, brand or proof title | `fx_text_shake` |
| no black edge from a keyed move (mirror edges or the cover zoom) | `fx_edges` |
| animated text stays fully visible for its reading floor; a freeze with text holds for it | `anim_read_floor`, `fx_freeze` |
| every transition has its handles, never renders through black and is not on the never list; a smooth cut only inside one take | `transition_handles`, `transition_through_black`, `transition_broken`, `smooth_cut_misuse` |
| a retime never shows a source frame past the media, and only on an item at speed 1 | `retime_range`, `retime_speed` |
| every effect, retime, transition and animated text the EDL has is on the built timeline as planned | `fx_missing`, `fx_off`, `fx_timebase`, `retime_off`, `transition_length_off`, `transition_type_off`, `text_anim_missing`, `text_anim_off`, `text_anim_unchecked`, `aux_off` (verify) |
| a cut list cannot switch these off | `checks_off` honours only warnings and `hook_3s`, `duration`, `music_end`, `word_twice` (a repeat that is meant); anything else is ignored with `checks_off_ignored` |

### ABCD checklist for ads (Google definitions) [V]
- Dynamic start: the first shot changes in under 3 s.
- Quick pacing: 5 or more shots in some 5 consecutive seconds (a sliding window).
- Quick pacing in the first 5 s: at least 5 shot changes (strict; many good ads fail it; soft).
- Overall pacing: mean shot under 2 s (soft; not for slow luxury or emotional spots).
- Brand visuals and brand mention, product visuals and product mention (text and speech): present, and present in
  the first 5 s.
- People present, a visible face in the first 5 s, a face close-up somewhere.
- Speech in the first 5 s.
- Supers present, and they match the audio.
- A call to action in text and in speech.
- (Ours, from the creative director's lens) the offer by 40 % of the length, a route for the call to action, the
  brand legible at the start and on the card, no stretch over 1.5 s without voice or text.
`abcd` (WARN, ad presets): first shot 3 s or longer, no 5 s window with 5 or more shots, or a mean shot of 2 s or
more. The rest of the list is for the audience judge to check by eye and ear. When the brief's tone is premium,
trust, craft or luxury ("Pace follows tone", section 4: a median shot of 1.2 to 2.5 s), a mean shot of 2 s or more
is intended: judges do not count that part of `abcd` against the cut, only a slow first shot.

### Adversarial prompts (the devil's advocate reviewer) [G]
- Which 20 % could be removed without losing the story? Cut it.
- Where would a viewer swipe away? Give the time and the reason.
- Which cut has no reason?
- Where did we leave an emotional face too early, or stay on a boring shot too long?
- Does the ending pay off the hook's promise?
- Watch it muted: does it still make sense?
- Watch only the first 3 s: would you keep watching?
- Which shot or setup did we show twice, and what could replace it (a hold, slow motion, a size change, a card)?
- Is any process shown out of order (a step before the step it needs, a finished result before its making)? Swap it
  (section 6, process order).
- Is every word on screen right? Is every proof line over a picture that shows it? Does any picture contradict its
  line (a child or a bystander at a working station under a safety line, a mess under a hygiene line), or show decor
  with no story (a plant, a shelf)?
- Does the second half show fresh setups, or the first half's footage again (`recycled_half`)? Does the music sag
  anywhere (`music_lull`), open quiet (`music_open_quiet`) or end in a lull instead of on a hit?
- Which effect could go? Name each punch, bump, ramp, accent, transition effect and text animation that serves no
  moment of the brief, or that a viewer would notice before the content, and cut it. Is any text moving while it
  must be read?

### The creative director's lens (ad presets) [S]
Would it stop the scroll in 1 s? One message by 3 s? When does the offer land (by 40 %)? Proof, then the offer once,
then the call to action? A call to action with a route, in text and voice? The brand legible at the start and the
end? One type system, with the hook the boldest and largest text before the card (`type_system`)? On the card, the
name and the call to action locked up together for the whole card, the CTA the largest text, held 2.5 s or more
(`end_card`)? Does it end on a hit? Any stretch over 1.5 s without voice or text? Would the client sign it off? Ship
blockers: a wrong word on screen, an offer after half the length, a call to action with no route, a proof line on
a picture that does not show it, a picture that contradicts its line. In the best tier the ad presets get this lens
as a judge and as a third reviewer.

---------------------------------------------------------------------------------------------------------------

## 14. Glossary: report flags and check ids

### Flags in report.txt
| flag | meaning | usual fix |
|---|---|---|
| `HOOK` | the item plays in the first 3 s | make it the strongest material |
| `FLASH` | a visible segment shorter than 3 frames | extend it or remove it; tag `intentional_flash` only on purpose |
| `OFF-BEAT` | a beat-driven cut outside the beat tolerance | use `beats` on the segment, or move the cut the frames the label shows |
| `JUMP?` | same media on both sides of a cut with a small gap and no zoom change | punch-in of 1.15 or more, a cutaway, or keep on purpose |
| `CLIPPED` | an edge cuts into a word | extend the item into the pause, or change the word range |
| `SPEED` | the item plays at a speed other than 1.0 | check motion and sound |
| `XFADE` | the item has a transition | check the handles and whether a dissolve is justified |
| `OVERLAY` | a V2 or higher cutaway | check it covers the right words |

### Checks (`E check`, `E review`; checks.json)
The shot model of the checks and of report.txt (SHOTS, HOOK, the cut sheets) is what the viewer sees as shots: an
item the pictures under it show through (a logo or still with transparency, a zoomed-out or moved overlay, an
opacity under 100) is not a shot, so the cuts under it count; its own in and out points count as visual changes for
`hook_3s` and `pacing`, not as cuts. Where nothing under it covers the frame (a logo on black) it is the shot. A
whole picture fit to the frame (zoom 1 or more, not moved, fully opaque) whose bars leave at least 70 % of the frame
covered is a cutaway, not an overlay: a DCI 4K, 17:9 drone or 4:3 action camera clip on a 16:9 timeline with scale to
fit counts as a shot like a UHD one. A vertical phone clip fit into 16:9 covers a third of the frame and stays an
overlay (its in and out points are visual changes).

| id | level | fires when | what to do |
|---|---|---|---|
| `edl_invalid` | STOP | `E validate` finds an error (list below) | fix the cut list and assemble again |
| `offline_media` | STOP | a media file is missing or its content hash changed | moved: `M add <its new folder or path>` (found again by its content, same id), then assemble again; changed: ingest again; never build with it |
| `preview_frames` | STOP | the preview or a chunk has a different frame count than the EDL | run the review again; if it persists, report it (renderer problem) |
| `flash_frame` | STOP | a visible V1 segment shorter than 3 frames, not tagged `intentional_flash` (WARN: one tagged `intentional_flash` shorter than 6 frames) | extend or remove the segment; for a deliberate flash, land it on a beat or a hit |
| `black_gap` | STOP | frames inside the programme with no visible video, not covered by a `solid` item | close the gap or cover it |
| `silence_gap` | STOP | the mix under -60 dBFS for more than 0.3 s inside the programme with dialogue around it | extend audio from handles, lay tone, or close the gap |
| `clipped_word` | STOP | a dialogue edge cuts more than 20 ms into a word (WARN in the camera sound under a picture segment and on `low_conf` words) | move the edge into the pause (change the word range; assemble snaps it); for camera sound, move the cut or lower or mute it |
| `joined_split` | STOP | part of one spoken word that whisper wrote as two (M script tagged the later part `joined`) is heard without the rest: the voice says half the word while the captions show all of it (a hand-edited EDL; assemble never makes this) | run the word range over every part of the word, or drop its first part (which drops the whole word) |
| `words_changed` | STOP | a dialogue item plays other words than its cut list range (a word lost or added, for example by a beat snap, an audio lead on a hand-edited EDL, or a length Resolve cannot place across frame rates) | restore the word range: end it at a pause, a longer tail with `pad_ms`, a shorter audio lead, or move the music |
| `rate_edge` | WARN | no frame edge lies within 20 ms of the boundary of two abutting words (23.976 and 24 fps), or a clip whose rate differs from the timeline has no placeable length that ends between two words: the in or out point runs up to one source frame into a word | listen at the cut; start or end the word range at a pause or cover the cut if it shows |
| `frame_edge` | STOP | a V1 item or a full-frame overlay pans or tilts past the image's overhang, or has a zoom under 1, so black shows at an edge (not on an opaque, unrotated still whose border is black: a logo card on black) | zoom to at least the value the fix names, or keep pan and tilt inside the printed room |
| `fps_mismatch` | STOP | the edit's frame rate differs from the Resolve project's (from the dump) | run `init` again after the dump, then assemble again |
| `hook_3s` | STOP | presets with `hook.gate`: no visual change before `visual_change_by_s` (a title there from the first frame is not a change), or no word and no title before `proposition_by_s` | open on the strongest line or image, cut or punch in early, bring a title in after the first frame |
| `caption_timing` | STOP | a caption cue shorter than `captions.min_s` or above `cps_max`; a read-only title under its reading floor | merge chunks, lengthen the title or cut its text |
| `caption_layout` | STOP | more lines or characters per line than the preset allows | shorter chunks, rewrite the title |
| `safe_zone` | STOP | a title or caption box outside the platform safe box | move or shrink the text |
| `duration` | STOP | not equal to `targets.duration_frames` when the cut list sets a target (the target then replaces the preset's range), else a length outside `length_s.min` to `max` | cut or add material; for ads hit the length exactly; when the user asked for a length outside the preset's range, set `targets.duration_s` |
| `music_end` | STOP | `music.end` is `resolve` and a music item ends inside the programme, or at its end with under 12 frames of fade and not at its natural end | start the music later so it ends naturally, or fade at a phrase end |
| `audio_fades` | WARN | a dialogue or music edge meets another item with no fade | assemble adds 1-frame fades; check hand-edited items |
| `beat_sync` | WARN | a cut on an `on_beat` item outside `beat_tol_frames` of the nearest beat | use `beats`, or move the cut |
| `pacing` | WARN | median shot or CV outside the preset, a long stretch with no visual change, the densest window (`min(10 s, half the length)`, graded from 8 s of programme up) too early | add cutaways or punch-ins, trim holds, move the densest run toward the payoff |
| `jump_cut` | WARN | consecutive V1 items of the same media, from the same detected shot or under 2 s apart in the source, zoom change under 15 % | punch-in, cutaway, or accept on purpose |
| `repeat_shot` | WARN | the same source frames used twice | pick another shot or another part of it |
| `transition_share` | WARN | dissolves above `transitions.max_share` of edit points | use straight cuts |
| `filler_left` | WARN | a word tagged in `speech.remove` is still in kept dialogue | add it to `drop`; when it runs into its neighbours (the fix says so) assemble cannot drop it: cover it with B-roll, cut at a pause, or keep it |
| `pause_long` | WARN | a kept pause longer than `pause_keep_ms[1]` (`handoff_ms[1]` at speaker changes) | split the word range at the pause |
| `speech_music_gap` | WARN | two-sided, measured during speech: the music less than `music.duck_lu` LU under the voice ("too loud"), or more than `duck_lu` + 5 LU under it ("the bed is buried", 15 LU in an ad) | too loud: lower the music `gain_db`, keep `duck: auto`; buried: raise its `gain_db`, put fuller bars with their low end under the voice (not a filtered intro), check `duck_max_db` |
| `abcd` | WARN | ads: first shot 3 s or longer, no 5 s window with 5 shots, mean shot 2 s or more | shorter opening shot, faster middle |
| `overlay_jump` | WARN | a V2 overlay of the same clip takes over where a V1 item ends (or hands over where one starts) but plays other source frames, so the picture jumps inside the shot (not when one side is punched in 15 % or more) | a J or L cut: `hold_prev_s` and `early_s`; a later moment of the same shot on purpose: punch in on one side or put another shot between |
| `text_overlap` | WARN | a title and a caption overlap on screen | move the title above (or below) the captions with its `y`, or the captions with `caption_y` on that segment |
| `word_twice` | STOP | a source word is heard twice in a row (within 3 heard words): two word ranges that share a word, or a range that plays a word the range before it already played | start the later range after the word, or end the earlier one before it; a repeat that is meant (a stutter edit) goes in `checks_off` |
| `mix_peak` | WARN | the preview mix peaks above -1 dBFS (STOP above +6 dBFS) | lower the loudest items' `gain_db`; check for a gain typed as +dB |
| `checks_off_ignored` | WARN | the cut list's `checks_off` names a STOP gate other than `hook_3s`, `duration`, `music_end` or `word_twice`; it stays on (the EDL keeps the request, and check and report.txt list the ignored ids) | remove it and fix what it reports |
| `subject_cropped` | WARN | a crop of a wider shot (a vertical piece from 16:9, a punch-in) shows none of the faces the shot log places (`faces[].x`, at least 0.05 inside the edge), or cuts the face the item's `frame_x` frames; a two-shot cut between speakers is not flagged | the `frame_x` the fix names (always the face the item frames), or another shot |
| `channel_balance` | WARN | dialogue from a clip flagged `one_sided`, `split_channels` or `two_voices` (its voice plays in one ear, a different microphone in each ear, or a different speaker in each ear) | one_sided and split: ask the user to set the clip to mono in Resolve (Clip Attributes > Audio), then dump, add --from-dump, ingest and assemble again; two_voices: never one channel (it drops a speaker), the user sets the track's Pan Spread to 1 (PNT) in the Fairlight mixer after the build |
| `caption_unverified` | STOP, or WARN where the preset's `captions.verify` is "warn" | a caption shows a word whisper was unsure of (`low_conf`) that no script alignment (`M script`), `M fix-word` or `captions.text_fix` covers | the main session aligns the script, or asks the user for the word; a cutter uses `text_fix` only with a spelling from the script, the brief or its glossary, else lists it in `open_actions`; never guess |
| `message_gap` | WARN | presets with a `message` block: the longest stretch between the first and the last word with no voice, title or caption is longer than `message.gap_max_s` (1.5 s in the ad and reels presets; pieces without dialogue skip it) | shorten the picture-only stretch, or put a super with a new point from the brief over it |
| `offer_late` | WARN | the first title with role `offer` starts after `message.offer_by` of the length (or there is none while the preset asks for one) | move the offer line (voice and super) earlier: by 40 % in an ad |
| `duck_pump` | WARN | the music gain rises and falls by 4 dB or more within 2.5 s (a `"lift": true` segment does not count), a duck ramp starts in the first 0.3 s, or the swing passes `music.duck_max_db` | keep the defaults (start ducked, `duck_hold_s`), raise `duck_hold_s` on the music item, lower its `gain_db`, or set `duck_max_db`; for a deliberate lift mark the picture-only segment `"lift": true` |
| `repeat_setup` | WARN | the same media is visible 3 or more times, or twice within 10 s with a zoom change under 30 % and a `frame_x` change under 0.1, unless the item is tagged `callback` | another shot, a longer hold, slow motion, a size change of 30 % or a text card (section 4, shot economy) |
| `edge_energy` | WARN | the 20 ms on the thrown-away side of a dialogue edge is 12 dB or more above the noise floor: a breath or a word start is cut | move the edge into quieter audio; when `E0 schema cutlist` shows it, nudge it with `edge_ms` |
| `caption_title_dup` | WARN | 60 % or more of a cue's words are on a title shown at the same time | drop the title, or set `captions.suppress_under_titles` |
| `text_competes` | WARN | a title and a caption with different words are on screen together in the first 3 s | make the hook title say what the first line says, or move one of them |
| `ending_tight` | WARN | the programme ends under 0.5 s after the last word, or the music fade starts before the last word ends plus 0.2 s | give the ending room: a longer last shot, the fade later |
| `music_start_off_bar` | WARN | a music item starts inside the file off every beat and bar line with a fade-in under 0.5 s | give it a fade-in of 0.5 s or more (`fade_in_s`), or start it on a beat or a bar line (`in_s`; this moves the drop too) |
| `still_edge` | WARN | a still's edge rows, as Resolve draws them (a transparent border counts as none), differ from its background: a thin stray line at the edge | re-export the image without the border; a full-frame still may also zoom 1.01 |
| `marker_long` | WARN | a marker note is longer than 120 characters | one line of at most 90 characters, only for an action the user must take; reasoning goes in the item's `why` |
| `markers_many` | WARN | more than 5 user markers per 30 s | keep only the actions the user must take; the rest goes in `open_actions` |
| `loudness_off` | WARN | the preview mix's integrated loudness (`stats.loudness.lufs_i`) is more than `audio.tol_lu` from `audio.lufs`; `stats.loudness` also gives `tp_dbtp`, `target`, `mix_gain_db`, `gain_more_db` (the smaller of target minus loudness and `codec_tp_db` minus true peak) and `residual_lu` (what plain gain cannot reach) | write the cut list line the fix names, `"mix": {"gain_db": <mix_gain_db + gain_more_db>}`, assemble and review again; a residual above 0 goes to the Deliver page (Optimize to Standard) and into the hand-over (section 9) |
| `title_blink` | WARN | two consecutive titles on one track leave the text fully off (no title, or alpha under 0.5 inside their fades) for 1 to 6 frames, with a gap of 0 to 6 frames between them (titles that meet but fade out and back in count) | let the earlier title run to the later one's start (chain them with `"at": {"seg", "offset_s"}` and lengths that meet) and assemble again: titles that meet skip the fades at the join; or leave a real gap of 8 frames or more |
| `end_card` | WARN | the piece has a card (its last V1 item is a logo still, or a still under a card or brand title, or a title with style `end_card` or `cta` starts in the last 5 s) and: the CTA (role `cta`) is on screen for less than `min(2.5 s, 60 % of the card)` ending at the programme end; or the brand name (role `brand`) and the CTA are on screen together for less than 1.5 s; or the CTA's `font_px` is smaller than another card title's; or a card title's box centre is more than 12 px from W / 2 | start the name and the CTA together on the card's first frame and run both to the end (two title tracks), the CTA in the `cta` style (the largest), no `"y"` or style that moves it off centre (section 10, End cards) |
| `pace_decel` | WARN | a music-led piece under 30 s (the preset's `music.use` is `required`, or there are no spoken words and a music item exists) with 6 shots or more: the shots that start at or after half the length, the last shot left out, have a median length 1.4 times the median of the shots before half, or more | cut the second half at 1 to 2 beats per shot; only the final shot opens up, for at most one bar or 2 s (section 4, P1) |
| `music_lull` | WARN | on the preview's music bus (`audio_env.npz`, 100 Hz), against the body level (the median music level over frames with music and no speech, leaving out the first 0.5 s and every fade-out): a run of at least `max(1.5 s, one bar)` with the music 8 dB or more under the body and no speech (outside the final fade), or the 2 s before the last music fade-out starts (or before the programme end) averaging 6 dB or more under it ("ends in a lull") | cut over the thin bars at a bar line, choose a fuller window of the song, or end on a hit (section 7, M5) |
| `music_open_quiet` | WARN | a music item starts at frame 0, no speech in the first 0.5 s, and the music averages 8 dB or more under its body level over 0 to 0.5 s (a quiet intro or a long pickup) | start on the drop or a downbeat of a full section (the music `in_s`); a pickup of at most 2 frames |
| `vo_gap` | WARN | two consecutive spoken words (dialogue, not nat sound) on the timeline are further apart than `max(0.8 s, pause_keep_ms[1] + 0.35 s)`, or that plus 0.4 s when a lift (`duck.lifts`) covers the gap; gaps that titles cover for 80 % or more are left to `message_gap`. "ducked hole": the music stays ducked through the gap | close the gap to 0.3 to 0.6 s (shorter picture-only segment, a tighter `pad_ms`); keep a lifted breath to about 1.2 s and only for a musical hit (section 6) |
| `ending_tail` | WARN | the programme runs more than 1.2 s past the last spoken word, unless titles or a V1 still cover 80 % or more of that tail (an end card) | end 0.5 to 1.0 s after the last word, or put the end card over the tail |
| `caption_jump` | WARN | the captions' first lines (their centres) take more than 2 distinct heights (3 % of the frame height apart), or change height more than once per 6 s of captioned time | one band (`captions.y_band`); `caption_y` only to clear a face or the product, and the same value for every such shot (section 10) |
| `type_system` | WARN | the role `hook` title is smaller (`font_px`) than a later title outside the card; or the piece is premium (`fx_meta.premium`) and a title or caption look has a stroke over 0.05 em; or titles and captions outside the card use more than 3 distinct look ids, some of them chosen by the cut list on top of the preset's role map (the preset's own look for each style in use and its caption look do not count) | the presets' clean looks and sizes (`hook_clean` 96, `super_clean` 88, `lower_clean`, `caption_soft` 84 on a vertical frame); one look per role: drop the looks the cut list added |
| `recycled_half` | WARN | a programme of 12 s or more: more than 35 % of the second half's picture time shows media already seen in the first half (stills and items tagged `callback` or `repeat_ok` left out); the message names up to 5 unused shots scored 6 or more in shotlog.json | fresh setups for the second half (the shots the message names), or tag a deliberate return `callback` |
| `title_repeat` | WARN | two titles with the same text (lowercase letters and digits compared), neither tagged `callback`, role not `brand` or `cta` (the call to action before the card and on it is meant twice) | show the offer once; give the second title a new point from the brief, or drop it; a deliberate repeat is tagged `callback` |
| `hook_motion` | not in this version (planned; nothing reports it, so a quiet report does not mean the hook moves) | little happens in the first second (low motion and no title) | open on a shot whose action starts in its first 10 frames (section 5, H7) |
| `empty_frame` | not in this version (planned; look at the cut sheets) | a frame shows almost nothing (a blank wall, a subject that left) | trim the shot before the subject leaves |
| `text_contrast` | WARN | a title or caption whose look has no box and a stroke of 0.05 em or less sits over a background whose ring luma (the pixels just around the text) gives a WCAG contrast under 3:1 against the fill colour, on the preview frames | a pill (`caption_clean_box`, `cta_card`) or a darker shot under the text; a stroke only for that role, never on a premium piece |
| `reveal_hold` | WARN | a word-reveal caption (`word_pop`: its anim reveals words) that a pause of 0.25 s or more follows, or that ends a sentence, is whole for less than 0.4 s (mid-sentence cues in fluent speech are not judged); or the programme's last caption is whole for less than 0.8 s | keep the payoff line in one cue (`keep_together`) held to the end; a longer `pad_ms` tail on the last word |
| `anim_read_floor` | STOP | an animated title (captions: see `reveal_hold`) is fully visible for less than its reading floor `max(0.833 s, chars/20, words x 0.33 s)`, counted from the frame the whole text shows (a typewriter from its last letter) | a longer cue or title, a quicker entrance (`fade`), or fewer words |
| `fx_text_motion` | WARN | bouncing captions or titles in a premium or cinematic piece; a caption overshoot above 0.15 or an entrance over 250 ms; a title pop over 0.30, longer than 400 ms or on more than 3 words; text that moves after its entrance | a calmer anim (`clean_box`, `keyword`, `fade`, `slide_hook`) or fewer words on the pop |
| `caption_keywords` | WARN | more than 1 keyword in a cue, or keywords on more than 35 % of the cues | one keyword per cue on about 30 % of the cues: the words that carry the message |
| `text_font_metrics` | WARN | a look's font has no readable metrics, so its Text+ Size falls back to the Arial Bold ratio and Resolve may draw it at another size than the preview | a `font_file` for the look, or a font the lab can read; check the title's grab |
| `fx_zoom` | WARN | a static punch-in under 1.15 (reads as a mistake), a bump outside 1.03 to 1.15, or a total zoom (static times keyed) above the genre's `zoom_max` | the zoom the fix names |
| `fx_soft` | WARN | a keyed zoom's peak upscales the source's pixels more than 1.15 times (section 6 headroom: a 1080p source may punch to 1.15; past that the picture softens) | a smaller zoom, a higher resolution source, or a cutaway |
| `fx_span` | WARN | an effect was clipped at its item's edge (its span runs past the frames the item shows): only the part inside the shot shows, and the message names those frames (the pieces of a segment split around a dropped filler count as one shot). A bump or flash on the first frame after a hard cut (`cut_in`, `{"s": 0}`) is not clipped: it starts at its peak on the cut | anchor it earlier or later, or put it on the next segment |
| `fx_offbeat` | WARN | a bump, snap, shake, flash, split or glitch, or a ramp's or freeze's landing, anchored to a beat lands more than about 80 ms (2 frames at 25 fps) from the nearest beat | anchor it with `{"beat": k}` or `{"beat_rel": n}`, or move the segment onto the grid |
| `fx_density` | WARN | effect events in some 10 s window above the genre budget times the effects appetite (section 15). A title animation counts only when it bounces (`super_pop`) or the cut list names it, and the message names the title animations it counted; the skill's default title fades and slides are part of the look | drop the effects that serve no moment |
| `fx_refused` | STOP | an effect assemble refused (an unknown or switched-off kind, a parameter of the wrong type or value, an anchor it could not find): it is not in the edit, and the EDL keeps the refusal so every later check stops on it | fix or drop it in the cut list and assemble again |
| `first_frame_text` | WARN | a title on the programme's first frame does not show its whole text on that frame (an entrance from alpha or scale 0, which a default animation skips there but an animation the cut list names keeps): the first frame is the cover (H7) | drop the named animation (the default then starts whole) or start the title a frame later |
| `fx_length` | WARN | an effect or transition outside its catalogue length (at 30 fps: whip 5 to 13 frames, zoom-through 7 to 16, flash 2 to 7, RGB split or glitch 2 to 8, light leak 15 to 45, bump 5 to 16) | the length the fix names |
| `fx_ramp_repeat` | WARN | a slow part of a ramp below timeline fps / media fps: frames repeat (Speed Warp does not change that: a ramp plays whole source frames at 100 %); also Speed Warp asked for an item at 100 % | footage shot at a higher rate (50 or 60 fps) or a less slow speed; for smooth slow motion of 25 or 30 fps footage a constant segment speed under 1 with `"retime_process": "speed_warp"` |
| `retime_range` | STOP | a ramp or freeze shows a source frame past the media's end (the retime reads beyond the item's out point) | start the segment earlier in the source, or a shorter fast part |
| `retime_speed` | STOP | retime keys on an item whose speed is not 1 | remove the segment's `speed`: the ramp's profile carries every speed |
| `fx_freeze` | STOP or WARN | STOP: a freeze that carries text holds for less than its reading floor; WARN: a freeze without text under 0.6 s or over 2.5 s | hold 0.6 to 2 s, and at least the reading floor with a name or a title on it |
| `transition_handles` | STOP | a transition without the handles the measured rule asks (section 1): Resolve would place it shorter | fewer `frames`, another alignment, items trimmed to free source, or the generic `whip` or `zoom`, which picks a clip-comp route |
| `transition_through_black` | STOP | a left or right aligned transition whose incoming item has no head: it renders through black | a centred transition, or head on the incoming item |
| `transition_broken` | STOP | a transition from the never list: `pop_wobble_reverse_fx`, `dctl_transition`, `logo_wipe_fx` | a straight cut or a catalogue transition |
| `smooth_cut_misuse` | STOP | `smooth_cut` between different media, across a source gap of 2 s or more, or longer than 4 frames | a straight cut, a punch-in or a cutaway |
| `transition_amateur` | WARN | a P2 transition (an iris, a shape, a 3D card, a page curl and the like) without `"look_is_brief": true` | a straight cut or a P0 transition |
| `transition_flash_risk` | WARN | a flickering transition (block glitch, disarrange, edgy, three color flash, inverse flash, tile wipe) | keep it only on purpose: the delivery file must pass `photosensitive` in `M qc` |
| `transition_black_card` | WARN | a 3D card transition over black (spin, cube, flip 3D, box twist, film strip) on a vertical piece | another transition |
| `transition_mb_off` | WARN | a P1 Fusion motion transition with motion blur off (mirrored seams show) | leave `motion_blur` on |
| `fx_repeat` | WARN | the same transition effect on 4 cuts in a row, or transition effects on more than 30 % of edit points (not in a music montage) | straight cuts; keep the effect for the cuts that need it |
| `fx_hook` | WARN | a transition effect inside the first second | open on a straight cut |
| `fx_shake` | WARN | a shake above 0.035 of the width, faster than fps/2, or more than one per 2 s | a smaller, slower or rarer shake |
| `fx_rgb` | WARN | an RGB split wider than 0.025 of the width | 0.5 to 2 % of the width |
| `fx_genre` | WARN | an effect kind the piece's genre avoids (section 15) | drop it, or check the preset and the brief's premium answer |
| `fx_families` | WARN | more stylized families (glitch, RGB split, light leak, film burn, emoji, shake, flash, spin) than the genre allows | keep the one family that fits the brief |
| `fx_edges` | STOP | a keyed move or shake without mirror edges and without the cover zoom, so a black edge shows; also (validate) a keyed zoom that dips under 1 times the item's framing, which shows the picture's edge | a smaller move (assemble adds the cover zoom), more zoom headroom on the item, or keyed zooms of 1 and above |
| `fx_text_shake` | STOP | a picture effect on a title, or an accent (shake, flash, RGB split, glitch) on an item whose frames carry the logo, the product name or the offer: an item tagged `logo`, `product` or `offer`, a shot of brand.json's `logo` or `wordmark` media, or a shot under a title with the role offer, brand or proof | put the effect on another shot (the one before the logo card): text and logos stay still |
| `fx_flash_rule` | STOP | more than 3 flash events (flashes, glitches, flickering splits, Brightness Flash transitions) in some 1 s | space them out or drop some |
| `fx_sfx_sync` | WARN | a sound effect's loudest moment sits more than 1 frame from its event (the cut, the hit, the pop) | anchor it with `on` and let assemble place it; check the file's peak |
| `sfx_loud` | WARN | a sound effect peaks less than 3 dB under the dialogue's peaks (the message says how far under, level with or above them) | lower its `gain_db` to 3 to 6 dB under the dialogue peaks |
| `sfx_missing` | INFO | a creator or music piece has whips or zoom-throughs and no sound effects | a whoosh from the user's sound effects folder, or keep it dry on purpose |

Effects in report.txt: each item and transition with effects gets an `fx` line (the kinds, their frames, the route
of a whip or a zoom), and spans whose preview is approximate are marked approximate there and on the overview image;
verify skips their frames in the pixel check and checks the frames around them.

### Media flags (`M status`, the media index)
| flag | meaning |
|---|---|
| `speech`, `music`, `silent`, `no_audio` | what the sound holds |
| `separate_sound` | a sound-only file with 20 or more words: probably a lav or recorder track; this version cannot sync it, so the edit uses each camera's own sound |
| `multi_track_audio` | Resolve maps the clip's sound as several tracks; Resolve places only the first, and the lab previews that same track |
| `tempo_uncertain` | music only: a rival pulse (2:3 or 4:3 of the beat) is nearly as strong as the beat, or a slow groove was doubled on a close call: the tempo may be wrong |
| `one_sided` | the stereo track Resolve places has its sound on one side (one channel is 12 dB or more quieter): it plays in one ear. Fix in Resolve: Clip Attributes > Audio, Format Mono, Source Channel the one with the voice; then dump, add --from-dump and ingest again |
| `split_channels` | camera sound with speech whose left and right channels hold different sources (little correlation below about 1 kHz: usually a lav on one side and the camera microphone on the other). Same fix, with the better channel |
| `two_voices` | speech whose channels each carry a different speaker (each side has the loud part to itself in 10 % or more of the speech, at levels within 10 dB: a two-microphone kit in stereo). Never set it to one channel; this version cannot centre both, so after the build the user puts its items on their own track and sets that track's Pan Spread to 1 (PNT) in the Fairlight mixer |
| `bar1_uncertain` | music only: the bar start is a guess (it can be one to three beats off) |
| `grid_moved` | the bar line the user gave sat half a beat off the found beats (they were on the off-beats), or the user confirmed it with `--move-grid`: the whole grid moved onto it; the NOTE in `beats`, ingest and status gives the amount |
| `bar_line_off_grid` | the bar line the user gave lies more than about 70 ms from every found beat: the grid stayed and bar 1 is the nearest beat; the user plays the music against the beats, and `--move-grid` moves the grid if their time is exact |
| `vfr`, `rotated`, `long_gop`, `flat_log` | a variable frame rate clip, rotation metadata, a long-GOP camera file, flat log footage (judge it for framing, not colour) |
| `bpm_name_mismatch` | music only: the file name gives a tempo that differs from the detected one by more than 3 %: check the grid with the click file (`M click`) |
| `loop_bars_off` | music only: a file of 40 s or less is a whole number of bars (4, 8 or 16, within 1 %) at the name's or a nearby tempo but not at the detected one: the detected tempo is probably wrong |
| `bpm_from_name` | music only: the name's tempo was used because the file is a whole number of bars at it; bar 1 sits on the first strong onset |
| `script_aligned` | the transcript was aligned to the voice-over script (`M script`): captions show the script's spelling; the original words are kept as `asr` in the transcript and in `<id>.words.asr.json` |
| `script_missing_words` | the voice skipped words of the script (listed in the transcript's `script.missing`): check the take, or the script |
Shot flags in the shot quality data: `soft`, `shaky`, `very_shaky`, `dark`, `bright`, `flat`. Word tags in the
transcripts: `filler`, `repeat`, `flag` (meaning-dependent) and `low_conf` (whisper was unsure of the word, or of
the whole clip: a language guess under 0.8, or a nearly silent clip such as distant chatter under B-roll),
`phantom` (a word the recogniser invented: no length, or over near silence or a music bed with no voice; it is also
`low_conf`, so `caption_unverified` flags it on a caption; leave it out of word ranges), and after
`M script`: `script_fix` (the script or `M fix-word` replaced the heard word; `low_conf` is removed), `extra` (a
word the script lacks, also tagged `filler`; assemble drops it unless the segment's `keep` lists it) and `joined` (a
later part of one spoken word whisper wrote as two; the word before it shows the script's word, this part stays in
the voice and is hidden in the captions; `joined_to` names that word; a range or a drop takes the whole word).
`M transcript` marks them: `-` extra, `+` joined, `=` script_fix (after the filler `*`, repeat `^`, meaning-dependent
`~` and low confidence `?` marks). Word indices never change. A media with
the role `voiceover` (from `M add --voiceover`) is narration: no `separate_sound` note and no beat analysis.

### Delivery QC (`M qc` on an exported file)
| id | level | fires when |
|---|---|---|
| `loudness` | STOP | integrated loudness off target by more than `tol_lu` |
| `true_peak` | STOP | above `true_peak_db` (above `codec_tp_db` for AAC or Opus files) |
| `clipping` | STOP | 3 or more consecutive samples at full scale |
| `dropout` | STOP | digital silence over 20 ms inside the programme |
| `polarity` | WARN | L/R correlation below -0.5 or mono fold-down loss over 6 dB |
| `photosensitive` | STOP | more than 3 luminance flashes in any 1 s over more than a quarter of the frame |
| `format` | STOP | size, frame rate or codec differs from the preset and platform, or 10-bit H.264 |
| `tags` | WARN | colour tags are not 1-1-1 (BT.709) |
| `channel_balance` | STOP (dialogue presets) or WARN | judged on the loud parts of the programme: one side carries the sound alone (12 dB louder, or 6 dB louder with the channels unlike, as a one-sided voice over a centred music bed) over 20 % of them or for 1.5 s at a stretch, or over most of the programme (a silent channel counts): it plays in one ear. STOP in dialogue presets for a different speaker in each ear (each side has the loud part to itself in 10 % or more); WARN for two different microphones left and right in a dialogue preset |
| `log_picture` | WARN | the picture looks like ungraded log footage (8-bit luma p99 under 215 and p1 over 30, low saturation): grade it before delivery (the resolve-colorist skill, when installed) |
| `black_run` | STOP | 0.5 s or more of black inside the programme (with `--edl`, only where the EDL has picture): a still or a clip missing from the build |
| `captions_missing` | WARN | with `--edl`: no bright text pixels inside a cue's box, so the captions were not burned in |

### Verify (`E verify`, after a build in Resolve)
| id | meaning |
|---|---|
| `missing` | STOP: an EDL item is not on the Resolve timeline |
| `extra` | STOP: the timeline has an item the EDL does not |
| `moved` | STOP: the item sits at another record frame (a slip of N frames) |
| `src_off` | STOP: the source in-point differs |
| `length_off` | STOP: the item's length differs |
| `speed_off` | STOP: the speed was not applied as planned |
| `fade_off` | WARN: fades differ |
| `volume_off` | WARN: the clip volume differs |
| `transition_missing` | WARN: a planned transition is not there (often: no handles) |
| `transition_off` | STOP: a ducking crossfade is there but spans other frames than the EDL asks (Resolve made it a frame short after the cut), which renders as a dropout; or a picture transition has another length than asked (a frame to either side of the centre is fine). Delete it and add it again at the next clip's start, or rebuild |
| `audio_dropout` | STOP: with `verify --proof`, the render's sound drops at least 10 dB below the sound around it for a few ms where the preview's mix does not (5 ms windows); listen there. WARN when the proof has no sound or no preview could be made |
| `marker_missing` | WARN: a planned marker is not there |
| `pixels_unchecked` | WARN: the structure matches, but no frames of the built timeline were compared with the preview (run `grab-script` and `verify --grabs` when `E commands` lists them, or play it through) |
| `v1_gap` | STOP: frames where the EDL has picture on V1 and the timeline has none (a retimed item a frame short) |
| `settings_off` | STOP: the timeline's size or input sizing differs from the edit's (for example scale to fit instead of scale to crop, which letterboxes every clip of another shape) |
| `text_missing` | STOP: a title or caption cue of the EDL is not on the timeline as Text+ and has no marker in its place (run the build's graphics snippet again, or rebuild) |
| `text_off` | STOP: a Text+ on the graphics timeline shows other text than the EDL, or its range on the programme sits at another place or length |
| `text_marker_only` | WARN: Resolve refused the Text+, so the build made a Cream marker with the text; make that Text+ by hand (Effects > Titles > Text+) |
| `pixels_off` | STOP: with `verify --grabs` (when `E commands` lists `grab-script`), a grabbed frame of the built timeline correlates under 0.6 with the preview's frame (title and caption boxes masked), or is flat where the preview has picture; frames inside an effect are held to that effect's own threshold (0.95 on a keyed zoom, 0.90 on an exact transition or a ramp) |
| `fx_missing` | STOP: an effect of the EDL has no tool in its item's Fusion comp, or the item has no comp. Run the build's `_fx.py` line again; when it stays, rebuild |
| `fx_off` | STOP: a keyed value differs from the EDL at a sampled frame: a zoom by more than 0.002, a move by more than 0.5 timeline px, a source frame by more than 0.01 frame. Someone changed the comp, or the item moved |
| `fx_timebase` | STOP: Resolve's comp range for the item does not match the EDL's visible frames (a transition next to it changed, or its speed did), so the build refused its effects. Check the transitions next to the item, then rebuild |
| `retime_off` | STOP: a ramp's or freeze's source frames differ from the EDL, or a Speed Warp setting did not read back |
| `transition_length_off` | STOP: a transition sits at another length than asked (usually handles shorter than the measured rule) |
| `transition_type_off` | STOP: a transition is another type than the EDL asks, or a rebuilt whip or zoom lacks its tools |
| `text_anim_missing` | STOP: an animated title or caption has no keys, or no Follower, on its Text+ (run the graphics snippet again, or rebuild) |
| `text_anim_off` | STOP: with `verify --proof`, an animated title or caption in the proof render differs from the preview on solid frames (its box more than 8 px off, less than 0.90 of the text overlapping, the highlight colour missing or more than 14 px away). Fading frames and stray background highlights are not counted, and a look with a dark box (a caption pill) is judged inside its drawn box, inset by half its pad, so a light thing in the picture right outside the pill (a ring light, a white wall) is not read as text. A look with dark text on a light box (`cta_card`) is judged twice: the pill as above, and its own glyphs (pixels near the text colour) inside the pill's text area, so a call to action that is missing, white or the wrong colour STOPs. Look at the listed frames in the proof and the preview; rebuild when the text moves differently |
| `text_anim_unchecked` | WARN: with `verify --proof`, the picture behind an animated light title (a shadow-only look such as `hook_clean` or `super_clean` over a light wall, sky or sleeve) passes the same mask as its glyphs on most solid frames, so the compare passed without seeing the text | look at the listed frames in the proof by eye; give the title a stroke over 0.05 em or a box, or move it off the light part of the picture (`text_contrast` warns there too) |
| `aux_off` | STOP: with grabs or a proof, a picture statistic says an effect is missing where correlation cannot see it (a split's red to blue offset, a flash's or a dip's level, blur or grain) |
| `fx_pixels_approx` | INFO: spans whose preview is approximate were not pixel checked; the frames around them were. Look at one grab of each yourself |
| `track_name_off` | WARN: a track's name in Resolve differs from the EDL's (read while the timeline is current; the caption track is "Captions", a track the EDL lacks "(empty)"). Rename it in the track header, or run the build's finish snippet again |
| `item_label_off` | WARN: an item's name or clip colour differs from the plan (titles, captions, music pieces, stills, clips with comps; section 1). Run the build's finish snippet again, or set the name and colour by hand |
| `handoff_missing` | WARN: the timeline has no hand-off marker (Blue, "resolve-editor hand-off"), or its Comments lack the hand-off note. Run the build's finish snippet again: it adds both. Verify's marker comparison ignores this marker |

Stills now count like clips: a missing still is `missing`, and its hole on V1 is a `v1_gap`. `pixels_unchecked`
stays a WARN whenever no frames were compared.

A verify STOP means: tell the user exactly which items differ and where the backup is; never repair their
project automatically.

### EDL validation codes (`E validate`)
Errors: `schema`, `dup_id`, `unknown_media`, `bad_track`, `empty_range` (an item with no length), `overlap` (items on
one track overlap without a transition), `src_range` (source range plus handles outside the media), `speed_mismatch`,
`transition_handles`, `missing_file`, `hash_changed`, `fps_invalid`, `bad_mix` (the EDL's `mix.gain_db` is not a
number or lies outside -20 to +12 dB), `rate_length` (a clip on a timeline of another rate at a length Resolve cannot
place, or without the source frames its placement needs; the message names the nearest lengths), `slowmo_handle` (a
slowed item without the source frames Resolve places before slowing it). Warnings: `last_media_frame` (only with older
semantics: an item ends on the media's last frame and the build sends it one frame short), `still_in_build` (older
versions only: stills are now built with marks), `unsupported_preview` (opacity under 100 or a partial frame on V2 and
above), `duration_target`, `slowmo_repeat` (slow motion below timeline fps / media fps repeats frames), `vfr_media` (a
variable frame rate clip; make a constant rate copy with `ffmpeg -i IN -fps_mode cfr -r <rate> OUT`). Effects codes
(section 15): `fx_unknown` (an effect kind the catalogue does not have or has disabled; the message gives the
catalogue's reason), `fx_span` (an effect outside the frames its item shows), `fx_still_untested` (a keyed effect on a
still image while the catalogue's `stills_ok` is off; this version's catalogue has it on), `retime_range` (a retimed
source frame past the media), `retime_speed` (retime keys on an item whose speed is not 1), `transition_unknown` (a
transition type the catalogue does not have or refuses: a P2 type without `look_is_brief`, or one from the never
list), `transition_handles` (the measured handle rule of section 1, which asks more head than the old half-and-half
rule), `transition_through_black` (a left or right transition with no head on the incoming item), `anim_unknown` (a
caption or title animation the preset's `anims` and the catalogue do not have) and `fx_edges` (a keyed zoom that dips
under 1 times the item's framing: the picture's edge would show).

### Assemble notes (`OUT.assemble.json`)
Snaps (an edge moved outward or inward to stay out of a word, or a length moved by a frame so Resolve can place
it), pads applied, fillers dropped or kept (`filler_kept_unclean`), long pauses split, beat snaps, captions made.
Warnings: `beat_snap_impossible` (no room inside the words or the source for the snap; or, in a voice-over piece, no
picture on V1 before the beat segment, so there is nothing to trim), `beats_short`,
`not_enough_beats`, `no_beat_grid`, `source_short` (the media ends; the segment was shortened), `slowmo_moved`
(a slowed segment's in point moved earlier so Resolve can place it), `trim_overrun` (beats or dur_s ran past the
shot's trim), `lead_clamped` and `lead_ignored` (audio leads limited to the pause), `speed_len_changed`,
`overlay_clamped`, `overlay_dropped`, `why_missing`, `preset_unknown`, `align_end_off` (no music
start puts the song's end exactly on the programme end with these beat segments; it names the frames to add, or
says the song is shorter than the programme), `align_end_outro` (a short piece aligned to the end of a long song
plays only its quiet outro; it names a strong section to start at), `rate_edge` (see the checks),
`edge_inside_word` (an edge could not leave a word because the words abut; see `rate_edge`), `frame_x_clamped` (the
subject cannot be centred, the image ends first; it names where the subject sits and the zoom that centres it),
`frame_x_and_pan` (both were given; `frame_x` is used), `frame_x_ignored` (the media size is unknown), `hold_clamped`
and `hold_ignored`
(`hold_prev_s` or `early_s` shortened or dropped because the segment or the media has no more room),
`marker_dropped` (a marker past the programme end), `checks_off_ignored`, `text_fix_unused` (a `captions.text_fix` key
names a word that is not heard on the timeline), `title_style_unknown` (a title style the preset does not have;
`hook_top` is used), `title_start_missing` (a title with neither `at_s` nor `"at": {"seg", "offset_s"}`; it
starts at 0), `look_unknown` (a look id the preset does not define; the default look is used),
`lift_refused` (a `"lift": true` segment the music could not lift over: words are heard inside it, or it is
shorter than the rise plus the fall plus 0.25 s at full level, about 0.6 s), `lift_late` (the last word ends too close
to a lifted segment for the music to be at full level on its first frame; the message gives the level of that frame),
`lift_ignored` (`"lift"` with no ducked music), `duck_shared` (a music item that continues the same media where the
previous one ends, a chained loop, keeps that item's auto duck depth), `joined_split` (a word range started on a later part of a spoken word
whisper wrote as two, so it starts on the first part instead; or a drop named only a later part, so it was refused:
drop the first part to remove the whole word), `title_y_clamped` (a title's `"y"` would put it partly outside the safe
area, so it sits inside it: moving it further that way does nothing), `caption_y_dropped` (`caption_y` asks on both
sides of the caption band: the ones on the side with fewer asks went back to the band; section 10),
`overlay_short` (frames with no picture where
an overlay starts or ends: the voice segment under it grew past its overlays after a pad or edge change; lengthen
the overlay), `keywords_ignored` (`captions.keywords` with a
word-reveal caption anim such as `word_pop`: the keywords are not shown; section 10).
Titles and captions (notes): `title_gap_closed` (two titles on one track left a gap of 1 to 3 frames, so the earlier
one now runs to the later one's start; the note names both and the frames), `title_chain_joined` (titles that meet
on one track: the earlier one's exit and the later one's entrance were skipped so the text swaps in place without a
blink; the note names the joins and any title that went static), `title_track_2` (titles that overlap a
title on the title track moved to the second title track, "Titles 2"; more than two titles at once is an error),
`title_snapped` (a free title edge within 3 frames of a picture cut moved onto the cut, keeping its reading floor;
a title end within 3 frames of the programme end moves onto the end, so a CTA never drops off for the last frames)
and `caption_y_snapped` (cues moved to the main caption band or the one alternate band; the note lists the changed
cues with the heights asked and used). A word range that ends on the first part of
such a word runs over its later parts (a note in `snaps`, edge `joined`). Assemble refuses (ERROR) an unknown
transition type, a marker colour Resolve does not have, a title without text and word indices that are not whole
numbers. Errors: `words_changed`, `frame_edge` (the same STOP as in the checks) and
`fps_mismatch` make the result STOP. Read them after every assemble.
Effects (section 15): `fx_routes` (for each generic `whip` or `zoom`, the route assemble chose: the custom Fusion
transition when both items have the handles, else the clip-comp pair; also an animation swapped for a calmer one,
such as a bounce on a premium piece), `fx_clipped` (an effect cut short at its item's edge: anchor it elsewhere),
`fx_refused` (an effect, transition or animation that was not used, with the catalogue's reason: a P2 or never
entry, a P1 entry whose sandbox test has not passed, a retime on a still), `fx_anims` (a caption or title animation
assemble chose: the preset's default where the cut list names none, so an older cut list assembled again starts to
move; `"anim": "none"` keeps it still; or a calmer one on a premium piece or for the reading floor), `retime_audio` (a
ramped or frozen segment asked for sound: it plays none) and `transition_ignored` (a transition after a segment that
is not on V1, or with nothing after it; the message says which: an overlay, a sound-only segment, an unknown id, or
no V1 segment starting where it ends). `transition_ignored` means nothing was placed for that entry: the segment it
follows is an overlay, a sound-only segment or the last picture, so move the transition to a cut between two V1
segments. Assemble also refuses (ERROR, no EDL) two transitions on one cut,
`frames` that is not a whole number above 0, Speed Warp together with a ramp or freeze, a point outside the frame
and parameters of the wrong type, with a message that names the field.

### Effects snippet results (`build_<ver>_fx.py`)
`ok`, `fx_built` (the effects placed), `fx_refused` (`{id, why}`: `fx_timebase` when Resolve's comp range for the
item disagrees with the EDL, `addtool_failed` when Resolve would not add a tool, `keys_failed` when a key did not
read back, `item_missing` when the clip is not on the timeline (the build refused it), `speed_warp_refused` when
Resolve would not set Speed Warp),
`fusion_items` (the EDL ids of the items that now carry a Fusion comp) with `fusion_clips` (`{id, clip, track, tc}`:
the clip name, video track and record timecode of each, the words to use in the hand-over and the colorist note,
which also carries them), `transitions_rebuilt`
(the custom whips and zooms), `transitions_length` (`{id, asked, placed}` for each transition) and `more` (true: its
time for one run was up; run the same RUN line again and it resumes, skipping what is already built).

---------------------------------------------------------------------------------------------------------------

## 15. Effects, motion and animated text

Effects make a piece feel made rather than assembled, and they cheapen it faster than anything else. Four rules
come before every number below:
1. **An effect serves one moment** of the brief: the emphasis word, the drop, the reveal, the offer, the payoff.
   Take it away and that moment should get weaker; if nothing gets weaker, it goes.
2. **Never on text, logos or offers** (a moving or distorted brand mark or price reads as a mistake and cannot be
   read: no picture effect on a title, and no shake, flash, RGB split or glitch on a shot tagged `logo`, `product`
   or `offer`, `fx_text_shake`) and **never in the first second** as a transition effect (`fx_hook`: the hook is the
   content, not the transition into it). A hook title's own animation is part of the look, not an effect here: a
   default one starts whole on the first frame (section 10).
3. **Taste is WARN, safety is STOP.** Budgets, families, lengths and repeats are warnings the judges weigh; text that
   moves while it must be read, black edges, more than 3 flashes in a second, broken transitions and missing handles
   stop the build.
4. **What the judges see is what Resolve plays.** Assemble bakes every effect into a table of values per frame in
   the EDL; the preview renders that table with ffmpeg and the build keys the same values in Resolve, linear between
   keys. Every effect is checkable: verify compares the keys, the grabs and the proof render against the preview,
   with a threshold per effect.

Status words: **P0** ships in this version; **P1** ships once its sandbox acceptance test passes (until then its
catalogue row is disabled and assemble refuses it with the reason); **P2** is documented only (section 1 lists
them; some are manual steps, below). The catalogue (`fx_catalog.json`) is the single list of keys, Resolve routes,
preview recipes, tolerances, lengths and genre fit.

### Writing effects in the cut list
Effects are written in words, beats and seconds, like everything else. An example of the fields (several genres in
one list, not a real piece):

    "spine": [
     {"id": "s02", "media": "m03", "in_s": 1.2, "dur_s": 1.6, "why": "the drop lands on the needle",
      "fx": [{"kind": "bump", "at": {"beat": 8}, "peak": 1.08, "why": "downbeat of bar 3"},
             {"kind": "flash", "at": "cut_in", "peak": 0.8, "why": "the hit"}],
      "retime": {"kind": "ramp", "profile": [{"speed": 3.0, "dur_s": 0.5}, {"speed": 0.5}], "ease_ms": 300,
                 "land": {"beat": 16}}},
     {"id": "s03", "media": "m04", "in_s": 2.0, "dur_s": 1.5, "speed": 0.4, "retime_process": "speed_warp",
      "audio": "none", "why": "the slow reveal (25 fps footage: Speed Warp makes the in-between frames)"},
     {"id": "s04", "media": "m01", "words": [38, 44], "why": "the promise",
      "fx": [{"kind": "punch", "at": {"word": 41}, "zoom": 1.25, "ms": 400, "ease": "in_out_cubic",
              "point": [0.5, 0.35], "why": "emphasis on 'free'"}]},
     {"id": "s07", "shot": "m07.s01", "beats": 8, "why": "payoff",
      "fx": [{"kind": "push", "from": 1.0, "to": 1.06, "point": [0.5, 0.5], "ease": "in_out_sine"}]}],
    "overlays": [{"id": "r01", "shot": "m05.s02", "at_s": 6.0, "dur_s": 2.0, "why": "...",
                  "fx": [{"kind": "push", "to": 1.04}]}],
    "transitions": [{"after": "s03", "type": "whip", "direction": "left", "frames": 8,
                     "why": "both shots pan right, so the picture already travels left"},
                    {"after": "s05", "type": "zoom", "frames": 10, "peak": 2.5, "point": [0.4, 0.6]},
                    {"after": "s08", "type": "edge_wipe", "frames": 12, "audio": "plus3"}],
    "titles": [{"id": "t01", "text": "MADE BY HAND", "style": "super", "role": "hook", "at_s": 0.0, "dur_s": 1.6}],
    "captions": {"from": "dialogue", "anim": "pop_highlight", "keywords": ["m01:41"]},
    "sfx": [{"id": "x1", "media": "whoosh_low", "on": {"transition": "s03"}, "gain_db": -8},
            {"id": "x2", "media": "hit_01", "on": {"seg": "s02", "fx": 1}, "gain_db": -6}]

- `fx` on spine segments and overlays: a list of effects, each with a `kind` from the catalogue (`punch`, `bump`,
  `snap`, `push`, `shake`, `flash`, `rgb_split`, `glitch`, `light_leak`) and a `why`. Every parameter a kind does not
  name takes the catalogue's default; times are in ms and become frames with `round(ms x fps / 1000)`, at least 1.
- Anchors (`at`): `{"word": i}` (the frame on which word i of the segment's media starts on the timeline),
  `{"beat": k}` (programme beat k of the music grid, counted from 0), `{"beat_rel": n}` (beat n inside the
  segment, counted from 0), `{"s": t}` (seconds from the segment's start), `"cut_in"` or `"cut_out"`. A beat anchor
  needs a music item with `"grid": true` and marks the effect as on the beat, so `fx_offbeat` grades it; a word
  anchor needs a word the segment plays. A `push` spans the whole segment unless it has `at` and `dur_s`.
- `point`: the zoom point as fractions of the FRAME, x from the left and y from the TOP (the build converts it to
  Resolve's own pivot through the item's static framing). One point per item.
- `retime`: `{"kind": "ramp", "profile": [...], "ease_ms": 300, "land": <anchor>}` (speeds with their durations, the
  last one runs to the end; `land` is the frame where the last speed must already hold, and it wins over the profile:
  the first part's `dur_s` is stretched or shortened so the last speed holds on the anchor) or `{"kind": "freeze",
  "at": <anchor>, "hold_s": 1.0}`. The item keeps speed 1; the ramp lives in its comp and plays whole source frames.
  `retime_process`: `nearest` (the default) or `speed_warp`, which works only with a constant segment `speed` under 1:
  assemble refuses it together with a ramp or a freeze (those play at 100 %, so Speed Warp has nothing to warp and the
  slow part would still repeat frames).
- `transitions[].type`: a catalogue key, or the generic `whip` or `zoom` (assemble picks the route, section 8). Extra
  keys: `direction`, `peak`, `point`, `mix`, `motion_blur`, `audio` (`plus3`, `zero` or `none`) and `look_is_brief`.
- `E diff A.json B.json` lists an item as changed when only its effects changed too: each fx by id and field (a moved
  push point, a new peak), its retime and accents, its text animation and its parameters, and a title's or caption's
  style, role, size, look and box.
- `titles[].anim` and `captions.anim`: an anim id of the preset's `anims` (section 10), or `none`. `captions.keywords`:
  media word ids `"<media>:<index>"`, or the literal word, shown in the `keyword` style.
- `sfx`: `media` (a sound effect added with `M add --sfx`), `on` (`{"transition": "<the segment it follows>"}`,
  `{"seg": id, "fx": n}` with n the effect's place in that segment's `fx` list counted from 1 (its id is
  `<seg>.fx<n>`), `{"title": id}` or
  `{"at_s": t}`) and `gain_db`. Assemble puts the file's loudest moment on the event frame.
- After assemble, the EDL's items carry `fx` (the record the checks, the report and verify read), `motion`,
  `accents` and `retime` (the per-frame keys both renderers play), and the assemble report names `fx_routes`,
  `fx_clipped` and `fx_refused` (section 14).

### Genre budgets [G numbers, V for the evidence behind them]
Each preset names a genre (`fx.genre`); a premium or luxury brand moves a preset to `premium_brand` (a music montage
or music video to `cinematic_montage`). The numbers are starting points from the evidence below and editors' practice;
they are WARN-level (`fx_density`, `fx_genre`, `fx_families`, `fx_zoom`).

| genre | presets | events per 10 s | stylized families | total zoom cap | signature moves | avoid |
|---|---|---|---|---|---|---|
| `premium_brand` | interview_doc; any piece with `premium: true` except music montages and music videos | 1.5 | 1 | 1.10 | slow push 1.03 to 1.08 over 2 to 6 s, a ramp into 40 to 60 % slow motion on the hero moment, a clean text fade, one warm light leak at most | glitch, RGB split, emoji, flash, bumps, spins, progress bars, shake, bouncing text |
| `performance_ad` | ad_15, ad_30, product_demo | 4 | 3 | 1.35 | punches on claims, kinetic captions with a keyword colour, a zoom through into the product, sound effects on graphics | any effect on the logo or the offer; spins |
| `creator_reel` | reels_talking_head | 5 | 3 | 1.35 | punches every 2 to 4 s alternating with the plain framing, kinetic captions, one freeze beat, whooshes and hits | the same transition on every cut |
| `talking_head` | shorts_highlight, podcast_clip, youtube_long | 3 | 2 | 1.30 | punches on emphasis words, captions, J and L cuts, a slow push on long takes | glitch, RGB split, flash, light leak, film burn, spins |
| `music_montage` | reels_montage_music, music_video | 8 | 3 | 1.5 | bumps on downbeats or snares, shake only on the biggest hits, whips between matching motion, ramps into drops, glitch for fitting music | an effect on every beat; more than 3 flashes a second |
| `cinematic_montage` | wedding_highlight, travel_montage | 2.5 | 2 | 1.15 | slow-motion ramps, warm light leaks, slow pushes, dissolves on time jumps | glitch, RGB split, emoji, bumps, progress bars, spins |

- An event is one keyed effect (punch, bump, snap, push, shake, flash, RGB split, glitch, freeze, ramp, light leak),
  one transition that is not a cut, or one title animation that bounces (`super_pop`) or that the cut list names.
  Caption animation, the skill's default title fades and slides, and static framing are not events.
- Stylized families: glitch, RGB split, light leak, film burn, emoji, shake, flash, spin.
- The effects appetite from the brief scales the events per 10 s: none 0, light 0.5, normal 1, bold 1.25.
- Why budgets [V]: camera changes inside one scene (a punch-in, a reframe) raised arousal and memory without adding
  load even at fast rates (Lang et al. 2000), while fast cuts to unrelated content overloaded viewers (Lang et al.
  1999); slow motion made products look more luxurious across 12 experiments (Jung and Dubois 2023) and slow ads
  turned attention to quality, fast ads to price (Yoon et al. 2020). So a talking head gets cheap same-scene
  changes, and premium gets slower, not busier. Most viewers start muted (69 % in public, a 2019 survey of 5,616 US
  adults), which is why captions carry the motion in creator pieces.

### Keyed zooms: `punch`, `bump`, `snap`, `push` (P0) [V build, S practice, G numbers]
| kind | what | numbers | frames at 25 / 30 fps | genres |
|---|---|---|---|---|
| `punch` | an eased zoom that stays | to 1.15 to 1.30 over 300 to 500 ms, in_out_cubic | 10 / 12 | creator, talking head, ads |
| `bump` | a beat pulse | peak 1.05 to 1.12 (default 1.08) ON the beat, 80 ms up (out_quad), 300 ms down (out_cubic) | 2 + 8 / 2 + 9 | music, creator |
| `snap` | an impact settle | the level plus 0.03 to 0.05 on the hit, settling over 160 ms | 4 / 5 | music, creator |
| `push` | a slow push | 1.00 to 1.03 to 1.08 over 2 to 6 s, in_out_sine | 50 to 150 / 60 to 180 | premium, talking head, cinematic, payoff shots |

- Route [V, sandbox]: a Transform in the clip's Fusion comp, Size keyed per frame about the zoom point (a snap punch
  to 1.25 read 1.24, 1.25, 1.25, 1.125, 1.0 on grabs; keys from 1.0 to 1.6 rendered exactly). Size 1.0 is the item's
  framing on the Edit page, which Resolve applies after the comp, so a keyed zoom multiplies the static `zoom`.
- Preview [V]: ffmpeg's perspective filter, sub-pixel; it matched Resolve renders at median correlations of 0.998
  and better. Pixel threshold: 0.95 on every frame of the move (a missing push scores 0.56 to 0.90, a wrong ease
  0.88).
- Taste: a static punch under 1.15 reads as a mistake (section 6). Bumps only on downbeats or snares, or in 2 and 4
  bar blocks. Never stack a bump on a punch. Keep the total zoom under the genre cap and the crop above the delivery
  resolution (section 6 headroom; `fx_soft`). Put the punch on the emphasis word and its point on the face.
- Checks: `fx_zoom`, `fx_offbeat`, `fx_density`, `fx_length` (a bump 5 to 16 frames at 30 fps), `fx_soft`, `fx_span`.
- On still images (a Ken Burns push on a photo, a logo card) keyed effects are built [V build]: a still's comp
  runs one comp frame per timeline frame (sandbox: a 1.0 to 1.1 push rendered 1.0, 1.05 and 1.1), with comp frame 0
  on the still's first frame also when a transition leads into it (the frames under the transition sit before 0).
  A catalogue without `stills_ok` refuses them (`fx_still_untested`).

### Retime: `ramp` and `speed_warp` (P0), `freeze` (P1)
- `ramp` [V build, S numbers]: fast parts 200 to 1500 %, slow parts 20 to 60 %, the change eased over 300 ms
  (in_out_sine: 8 frames at 25 fps, 9 at 30), landing at 100 % or at the slow speed ON the hit or the downbeat. No
  speed-ups on faces or speech; a ramped item plays no sound. Premium: 100 % into 40 to 60 % on the hero moment, no
  speed-up over 300 %. Route [V, sandbox]: a TimeStretcher right after the clip's media input with its source frame
  keyed per frame (0.47x then 1.49x measured; a planned frame matched the source frame exactly); keys land on whole
  source frames. The retime may read past the item's out point, so validate checks the highest source frame against
  the media (`retime_range`). Preview exact (0 frame differences on four key sets; median 0.9998 against a Resolve
  render); threshold 0.90.
- `speed_warp` [V, sandbox]: Resolve's optical-flow retime with Speed Warp motion estimation makes a new frame on
  every frame instead of repeating them (about 0.25 s per frame to render, Studio). It retimes a segment that plays at
  a constant `speed` under 1 (25 or 30 fps footage slowed); it cannot smooth a ramp or a freeze, which play whole
  source frames at 100 % (assemble refuses the pair). For a ramp whose slow part falls below timeline fps / media fps
  (`fx_ramp_repeat`), use 50 or 60 fps footage or a less slow speed. Its preview repeats the nearest frames, so it is
  marked approximate (threshold 0.60).
- `freeze` [V mechanism]: a flat retime key pair holds one frame. Hold 0.6 to 2.0 s; with a name or a title on it,
  at least the reading floor (`fx_freeze`); optionally a 1.00 to 1.06 push and a 2-frame flash on its first frame.
- Checks: `fx_ramp_repeat`, `retime_range`, `retime_speed`, `fx_freeze`, `fx_offbeat` on the landing.

### Transitions (P0 and P1)
Section 8 has the catalogue picks, the amateur list, the flash-risk list and the fixed defaults; section 1 the
measured handle rule. The two motion transitions:
- `whip` [V, sandbox]: with handles, the custom Fusion whip (two Transforms on one eased offset, motion blur at
  quality 8 and a 360 degree shutter, mirrored edges); its preview matched Resolve at 0.958 to 0.998 in all four
  directions. Without handles, a whip-out on A's last frames and a whip-in on B's first frames that share the
  transition's `frames` about 3 to 4 (A gets `round(frames x 3 / 7)`: 3 + 5 for the usual 8 frames, 3 + 4 for 7), the
  move one frame width each way with its middle on the cut, half a frame after A's last frame (one extra key on each
  side keeps the blur moving on the edge frames), its blur marked approximate. Both shots should move the same way;
  the cut sits on the beat with a whoosh under it.
- `zoom` [V, sandbox]: with handles, a zoom through the cut about `point` that peaks at 2 to 3 (0.967 to 0.995 against
  Resolve); without them, zooms keyed on A's last and B's first 4 to 8 frames. Point it at the subject, never at an
  empty part of the frame.
- Taste: straight cuts stay the default; transition effects on at most about 30 % of edit points outside music
  montages, never the same one on 4 cuts in a row, never in the first second (`fx_repeat`, `fx_hook`).

### Impact accents: `shake`, `flash`, `rgb_split`, `glitch` (P1) [S practice, G numbers]
| kind | numbers | route | genres and limit |
|---|---|---|---|
| `shake` | 0.8 to 3 % of the width (default 2 %), y 0.8 of x, rotation up to 1.5 degrees (default 0.6), 6 to 12 Hz and never above fps/2, the peak ON the hit, decaying to 10 % in 320 ms (13 frames at 25, 15 at 30) | a seeded path keyed per frame on the clip's Transform, with a cover zoom (1.060 for 2 % and 0.6 degrees on 9:16) so no edge shows; never Resolve's random shake | music montage (the biggest hits), creator punchlines; never premium, talking heads or text; at most 1 per 2 s |
| `flash` | peak 0.6 to 0.85 ON the cut frame, 1 frame up, 100 ms down (1 + 3 frames at 25, 1 + 4 at 30) | a keyed brightness in the clip comp, or `brightness_flash_fx` when there are handles | music, creator, performance ads, sparingly; never premium or talking heads |
| `rgb_split` | red and blue apart by 0.5 to 2 % of the width (default 1.2 %), alternating, the last frame 0, 2 to 6 frames | two shifted copies joined channel by channel (24 px keyed, 24 px measured) | electronic or hip-hop music, tech or gaming brands; at most 1 per 4 s; never logos or offers |
| `glitch` | a held split jumping every 2 frames plus horizontal bands, seeded, 2 to 8 frames | masked Transforms in the clip comp | as `rgb_split`; it counts toward the flash rule when it flickers |

Checks: `fx_shake`, `fx_rgb`, `fx_genre`, `fx_families`, `fx_density`, `fx_offbeat` (WARN); `fx_edges`,
`fx_text_shake` and `fx_flash_rule` (STOP: more than 3 flash events in any 1 s, BT.1702 as in section 8). A glitch's
preview is approximate: its frames are skipped by the pixel check and the frames around it are checked.
- Logo, product and offer frames: give a segment or overlay whose picture shows the logo, the product name or the
  offer `"tags": ["logo"]` (or `"product"`, `"offer"`). Shots of the media named as `logo` or `wordmark` in
  `LAB/brand.json` count as logo frames without a tag. A shake, flash, RGB split or glitch on any of them, or under a
  title with the role offer, brand or proof, is a STOP (`fx_text_shake`): the final hit belongs on the shot before the
  logo card, not on it.

### `light_leak` (P1) [S practice, G numbers]
A warm glow screened over the picture: 0.5 to 1.5 s, 30 to 80 % at its peak, at most 1 per 10 s. In this version the
leak lives inside one shot's comp, so it cannot cross a cut: anchored on `cut_in` or `cut_out` only the half inside
the shot shows (`fx_span` says so). Put it inside the shot, on the moment it should glow. Wedding, travel, lifestyle
and nostalgic brands; never tech, corporate or talking heads. Its preview is approximate (threshold 0.80 plus the
picture's level). On log footage a leak tuned on the flat picture looks stronger after grading: the hand-over says so.

### Sound effects (P0) [S practice, V for the placement]
A whoosh under whips and zoom-throughs, a hit on a punch or a shake, a riser ending on a drop or a keyword, a pop on
a title pop. The files are the user's own (the brief asks for a sound effects folder; `M add --sfx` adds them); the
skill downloads nothing. Assemble starts each file so its loudest moment lands on the event frame (a test whoosh
peaking 0.30 s into its file landed at 2.000 s for a cut at 2.000 s). The event frame of each kind: a transition's
cut; a punch's anchor, the emphasis word itself (the zoom eases on for 300 to 500 ms after it, so the hit lands with
the word, not with the end of the move); a bump's or snap's peak; a shake's hit; a flash's cut frame; a push's last
frame (a riser that ends there); a title's first frame. Taste: the loudest moment within 1 frame of
the event and its onset 130 to 270 ms earlier, the direction matching the move, one sound style per piece (the
preset's `fx.sfx_style`: `whoosh_hit`, `pop_whoosh`, `subtle` or `air`), a low and a high whoosh together only for
big moves, peaks 3 to 6 dB under the dialogue's peaks. Checks: `fx_sfx_sync`, `sfx_loud` (WARN), `sfx_missing`
(INFO).

### Animated captions and titles (P0; `box_karaoke` P1)
Section 10 has the styles, their numbers, the reading floor and the checks. Why [V for the survey, S for the
practice]: most viewers start muted, and word motion paces reading with the voice. The cue is one Text+ on the
graphics timeline keyed on every frame; the spoken word is styled by a Follower on its frame; the preview draws the
same states with the font's own kerning and measured boxes (within 8 px of the render on 400 of 400 test frames,
every event on its frame).

### What the preview shows, and how it is checked [V]
Every effect span has its own pixel threshold, the grabs and the proof render are compared against it, and a few
statistics catch what correlation cannot see:

| span | threshold |
|---|---|
| keyed zoom (`push_zoom_keyed`) | 0.95 on every frame of the move |
| ramp, freeze (`speed_ramp`) | 0.90 |
| exact transitions (`transition_exact`), flash | 0.90, plus the frame's level against the frames around it |
| close transitions (`transition_close`, for example Blur Dissolve, Push, Slide, Brightness Flash and the clip-comp pairs `custom_zoom_through` and `whip_pair`, whose 360-degree motion blur the preview only approximates: blurred zoom-through frames measured 0.92 and 0.93, a whip pair's frames 0.84 to 0.99 with a median of 0.98) | 0.75 as the span's median |
| approximate transitions (`transition_approx`), glitch | not checked inside; the frames around them at 0.6 or more |
| shake (`shake_keyed_path`) | 0.90 |
| RGB split | 0.90, and the red to blue offset within 1 px at the preview's size |
| Speed Warp, light leak | 0.60 and 0.80, marked approximate |
| animated text (`text_animated`; on the grabs the text box is masked along its whole path, and on the proof render) | the text box within 8 px, overlap 0.90, the accent colour within 14 px, contrast within 0.20, every word, letter and colour change on its frame |

Report.txt and the overview mark the approximate spans; verify reports them as `fx_pixels_approx` (INFO). Grabs
come in several calls: `grab-script` takes at most 30 frames per run (about 1 s per grab with 4K comps) and says
`more: true` until every effect's peak, every transition's middle and the frames around approximate spans are
grabbed.

### What stays manual (say only what the brief needs, at most one or two per piece)
The hand-over uses these words. Steps marked "reported" come from the manual or educators and were not run by script.
- **M1 Text behind a person** (P2: this version does not build the mask comp, and a Magic Mask tracks only from
  strokes drawn by hand). Keep the title in front of the picture and say: "Words behind a person need a Magic Mask
  drawn by hand on the Fusion page (Studio). I kept the title in front; if you want it behind, mask the person on
  that clip, track the mask through the shot and put the title under the masked person." When a later version
  prebuilds the comp, the step left for the user is one stroke on the MagicMask1 node with Stroke Mode Add, Track
  Fwd Then Reverse in the Inspector, and a fix-up stroke on any frame where the mask slips.
- **M2 Resolve's own word-highlight subtitles** (reported), instead of the skill's Text+ captions: "If you prefer
  Resolve's animated subtitles: I wrote the caption file to <path>. Choose File > Import > Subtitle and pick it, drag
  the subtitle clip to the very start of the timeline on a subtitle track, then open Effects > Titles > Subtitles >
  Animated and drag Word Highlight onto the subtitle track's header. If the words do not light up, run Update Word
  Timings on the subtitle track (the manual names the command; where it sits in the menus was not checked). Turn off
  the Text+ caption track I built, or you will see captions twice." The skill never does this by script: appending
  subtitles crashed Resolve 21.1.
- **M3 Direction or colour of a simple transition**: "Push and Slide come in from the left, Edge Wipe runs bottom to
  top and Dip To Color goes through black. To change one, click the transition on the timeline, open the
  Inspector's Transition tab and change the direction or the colour."
- **M4 A DCTL transition**: "Click the transition, open the Inspector and choose the DCTL file in its list." The
  skill never places one itself.
- **M5 A label that follows a moving object** (reported; node and input names as educators give them): "Open the
  clip on the Fusion page. Add a PlanarTracker node between MediaIn1 and MediaOut1, draw a shape around a flat area
  of the object on the first frame, press Track to End, set Operation Type to Corner Pin and connect the label to its
  Foreground input. Check the whole clip once."
- **M6 Smart Reframe and Stabilizer with your own settings** (the script can only apply the defaults and cannot read
  the result): "Select the clip, open the Inspector: Smart Reframe lets you pick the object of interest, the
  Stabilization panel the mode and strength. Press Reframe or Stabilize again after a change."
- **M7 Effects-library looks the script cannot apply reliably** (Digital Glitch and Colored Border never rendered
  when added by script; whether they render when added by hand was not tested): "In the Effects library open Toolbox
  > Effects (or Fusion Effects), drag the effect onto the clip and set it in the Inspector's Effects tab."
- **M8 One effect across a cut** (a Fusion Clip's comp cannot be reached by script): "Select both clips, right-click
  and choose New Fusion Clip, then open it on the Fusion page."
- **M9 Effects and the grade**: "Punch-ins, flashes, leaks and splits live inside the clips' Fusion comps, which
  Resolve processes before the Color page. After grading, play each flash and leak once: on log footage they look
  stronger after the grade. The clips that carry comps are: <list>." The same list goes to the colorist hand-off.
- **M10 Playback of heavy comps**: the build sets Render Cache Fusion Output to Auto on every clip that carries a
  comp (its result lists them under `cache`), so this is rarely needed: "If the timeline still stutters on the
  effect shots, choose Playback > Render Cache > Smart and let the red line over the clips turn blue."
- **M11 Audio-reactive motion, Lottie stickers** (reported, Resolve 21.0 features): "Not built by the skill. On the
  Fusion page you can drive any value from the timeline audio with the Fairlight Animator modifier; Lottie files
  import into the Media Pool like any clip."
- **M12 Loudness**: the build sets the mix gain (the EDL's `mix.gain_db`), and only a residual above 0 is left:
  "The mix measures <lufs> LUFS; plain gain stopped at the true-peak limit, <residual> LU under <target>. On the
  Deliver page, Audio tab, tick Normalize Audio Levels and choose Optimize to Standard at <target> LUFS, <true peak>
  dBTP." Fill in the target and the true peak `deliver-script` prints (the preset's `audio.lufs` and
  `audio.codec_tp_db`: -14 LUFS and -2 dBTP for social files; -1 dBTP only for an uncompressed master). With a
  residual of 0 say nothing.
- **M13 A speed ramp by hand** (the ramp lives in the clip's comp, not on the Edit page's retime curve): "The ramp on
  <clip> is the TimeStretcher RE_Retime in its Fusion comp (named "RE ramp <speeds>"). To change it, open the clip
  on the Fusion page, select RE_Retime, open the Spline editor and move its Source Time keys; the Edit page's
  Retime Controls do not show it."

### Where the numbers come from
Study and platform evidence [V]: Lang, Zhou, Schwartz, Bolls and Potter 2000 (camera changes inside a scene), Lang,
Bolls, Potter and Kawahara 1999 (pace and load), Jung and Dubois 2023, Journal of Marketing Research (slow motion
and luxury), Yoon, Bang, Choi and Kim 2020, International Journal of Advertising (slow and fast ads), the Verizon
Media and Publicis Media 2019 survey (sound off), ITU-R BT.1702 and Ofcom (flashes), TikTok's and Meta's own creative
guidance (2025 and 2026). Measured in a Resolve 21.1 sandbox project [V]: linear keys, comp time, the handle rule,
every placed transition and its preview class, the zoom, ramp, whip, zoom-through, RGB split and text routes, the
per-font Text+ size, Speed Warp. Editors' practice [S]: tutorials by working editors and template makers (entrance
times, overshoots, bump and whip lengths, shake decay, leak lengths, sound-effect timing). Budgets and limits [G]:
set from both, tuned on the trial pieces.
