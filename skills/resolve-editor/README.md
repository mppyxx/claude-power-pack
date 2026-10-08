# resolve-editor

A Claude Code skill that edits video in DaVinci Resolve the way a careful editor with an assistant team would. It
logs your footage (shots, every spoken word with its exact timing, pauses, fillers, music beats, loudness, sharpness
and shake), plans the story, has several editors cut competing versions, judges them against measured craft rules,
lets critical reviewers attack the winner, and only then builds the approved version in Resolve as a new, fully
editable timeline made from your original clips, with its logo card, titles and captions as editable Text+ in your
brand's font and colours. When the piece calls for them it adds effects with taste and within a budget per kind of
piece: punch-ins on the emphasis word, beat bumps, slow pushes, speed ramps into the drop, whips and zooms through
the cut, animated captions and titles, and your own sound effects on the cuts. It checks that timeline against the
plan and sets up the delivery with you.

What makes it different from one-click AI editors: cut points are snapped to the real audio and checked, so a
clipped word or a word that sneaks in or out of a line stops the edit before you see it; every cut carries a
written reason, pauses keep a natural rhythm instead of sounding nervous, cuts to music land on the beat and the
music dips under the voice without pumping, captions follow reading-speed standards, come from your script's exact
words (never a misheard one) and sit above the platform's buttons, and the result is a normal Resolve timeline you
can keep editing, not a baked video file.

What it touches in your project: it adds one new timeline per approved version in its own bin
(`resolve-editor/<edit name>`), plus a graphics timeline next to it that holds the titles and captions, and only
with your OK it imports missing media into that bin. It exports a backup
of your current timeline first. What it never does: change your existing timelines, clips or bins, change project
settings, delete anything, add render jobs without asking, or upload and publish anything.

## Requirements

- DaVinci Resolve Studio 21.1 or newer for the full mode. The free version has no scripting, so it gets a handoff
  mode: the skill edits from your files and gives you a timeline file and captions to import (see "Free version").
- Claude Code. The standard and best tiers use Claude Code's Workflow tool, which is not available on every plan
  or setup and can be turned off by an organisation. Without it the skill runs the same steps with ordinary
  subagents, or the quick tier.
- Python 3.10 or newer.
- ffmpeg and ffprobe on your PATH (macOS: `brew install ffmpeg`; Windows: `winget install Gyan.FFmpeg`, then open
  a new terminal window and restart Claude Code; Linux: your package manager).
- Optional but recommended, for talking pieces and captions: local transcription with whisper.cpp.
  - macOS: `brew install whisper-cpp`, then `bash install.sh --asr small` to fetch the model (466 MB; `base` is
    142 MB and less accurate).
  - Windows and Linux: `install --asr small` installs the `pywhispercpp` package and fetches the model. Word
    timing is best with whisper.cpp's own `whisper-cli` program; on Windows you can download the
    `whisper-bin-x64.zip` release of whisper.cpp and set the `RE_WHISPER_CLI` environment variable to its
    `whisper-cli.exe`. On Linux with a glibc older than 2.28 (for example CentOS 7) pip finds no ready
    `pywhispercpp` wheel and tries to compile it, which needs cmake and a C++ compiler; build whisper.cpp and use
    `RE_WHISPER_CLI` instead.
  Without it the skill still edits pictures and music, and finds pauses from the audio level, but it cannot edit
  by words or make captions.
- Footage ffmpeg can read: H.264, H.265, ProRes, DNx and similar. RAW formats (BRAW, R3D, ARRIRAW, Canon RAW,
  ProRes RAW) are not supported; render them to ProRes or H.264 first.

## Install

1. Put the `resolve-editor` folder into your Claude Code skills folder, so that `SKILL.md` sits directly in it:
   - macOS and Linux: `~/.claude/skills/resolve-editor/SKILL.md`
   - Windows: `%USERPROFILE%\.claude\skills\resolve-editor\SKILL.md`
   - If you run Claude Code with a custom config folder (`CLAUDE_CONFIG_DIR`), its `skills` folder works too.

   Unzipping often adds one folder level too many (Windows "Extract All" makes
   `resolve-editor\resolve-editor\SKILL.md`). Claude Code does not find the skill there: move the inner
   `resolve-editor` folder up one level. The installer checks this and stops with the same advice.
2. Run the installer from inside that folder. It creates a private Python environment in `.venv` inside the
   folder and installs numpy and pillow. It needs no admin rights and changes nothing else.
   - macOS and Linux: `bash install.sh`
   - Windows: `powershell -ExecutionPolicy Bypass -File install.ps1`

   Options: `--asr small` / `-Asr small` sets up local transcription (in a terminal it asks before downloading the
   model; without one, as when Claude runs it, it downloads nothing and says so, and Claude asks you in the chat
   first and then adds `--yes` / `-Yes`), `--mcp` / `-Mcp` also registers Resolve with Claude Code (next section),
   `--recreate` / `-Recreate` rebuilds `.venv`, `--dev` / `-Dev` adds the test packages, `--check-location` /
   `-CheckLocation` only checks the folder's place.
3. The installer checks ffmpeg, ffprobe and whisper and then runs both labs' own checks (`media_lab.py doctor` and
   `edit_lab.py doctor`), which report anything else that is missing (first line `DOCTOR: OK` when all is well).

## Connect Resolve to Claude Code

Resolve Studio 21.1 ships its own AI assistant server (ResolveMCP).
1. In Resolve: File > Setup AI Assistants, choose Claude Code. Then restart Claude Code.
2. Or register it yourself (or run the installer with `--mcp`):

       claude mcp add --scope user davinci-resolve -- "<path to ResolveMCP>"

   In Windows PowerShell 5.1 write the two dashes in quotes: `claude mcp add --scope user davinci-resolve '--'
   "C:\Program Files\Blackmagic Design\DaVinci Resolve\ResolveMCP.exe"`.

   | system | ResolveMCP path |
   |---|---|
   | macOS | `/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Applications/ResolveMCP` |
   | Windows | `C:\Program Files\Blackmagic Design\DaVinci Resolve\ResolveMCP.exe` |
   | Linux | `/opt/resolve/bin/ResolveMCP` |

3. If the Resolve tools time out: Resolve > Preferences > System > General > External scripting using: Local.
4. In Claude Code, `/mcp` should list the DaVinci Resolve server as connected. Some setups end up with two
   entries, "DaVinci Resolve" and "DaVinci Resolve Studio". That is fine: Claude uses the one that reports Resolve
   running, and the installer's `--mcp` does not add a third.

## Before the first run

In its default permission mode Claude Code asks before running commands and before reading files outside the
folder it was started in. Editing runs many lab commands and reads many preview images, and the editors in the
standard and best tiers are subagents that may not be able to answer those questions, so allow them once:
- `/add-dir ~/.claude/skills/resolve-editor` and `/add-dir ~/resolve-editor-labs` (create that folder first), so
  Claude can read the skill files and the preview images;
- the first time Claude Code asks to run the skill's Python (a command that starts with the full, quoted path of
  `.venv/bin/python`, on Windows `.venv\Scripts\python.exe`, inside the skill folder), answer "Yes, and don't ask
  again" for that command. Claude Code then writes the matching permission rule itself.
Resolve's own tools still ask each time unless you allow them too; the skill only ever runs its own scripts there,
and you can read each one in the lab's `snippets` folder first.

## First run

Open your project in Resolve (with the footage on the current timeline, or in a bin), start Claude Code in any
folder and ask, for example:

- "Cut a 45 second Instagram reel from the interview on my current timeline. Tight pauses, captions."
- "Make a 60 second vertical clip of the best moment in this podcast episode for Shorts."
- "Cut the footage in the bin Day 1 into a 30 second ad for our new product, with the song in the bin Music."
- "Trim the pauses and ums out of my talking-head timeline, but keep it natural."
- "Edit a 3 minute wedding highlight from these folders: ~/Movies/ceremony and ~/Movies/party." (works without
  Resolve too)

Claude asks its questions in one message (platform, length, the story, must-have moments, music, captions, the
voice-over script word for word, what viewers should do next and how, your brand kit: font, colours, logo, handle,
whether the brand is premium, how much effects you want (none, light, normal or bold), a folder of your own sound
effects if you have one, whether it may import footage into its own bin, and how long you can wait), analyses the
footage, shows you what the footage covers and what is missing, edits, and shows you a low-resolution preview with
a short report before anything happens in Resolve. You approve a version, or ask for changes. Only then does it build the new timeline.

The working files go to `~/resolve-editor-labs/<edit name>/` (the `RE_LABS_ROOT` environment variable moves
them). Keep that folder: it holds the analysis, every version, the backup and the scripts it ran in Resolve.

## Tiers and time

| tier | what happens | rough time for a 30 to 90 s piece from up to 30 min of footage |
|---|---|---|
| quick | Claude reads the transcripts and contact sheets, cuts one version, reviews and fixes it | 15 to 30 min |
| standard | a logger, a story architect, 2 editors competing on two story angles, a judge, a finisher, a reviewer and a fix pass (up to 8 agents) | 30 to 60 min |
| best, small (pieces of 30 s or less) | a logger who also writes the selects, 2 editors who each plan their own story angle, a judge, a finisher, 2 critical reviewers and a fix pass (up to 8 agents) | target 50 to 90 min, about half the usage of full best |
| best, full | 3 loggers, an assistant editor, 2 story architects, 4 editors, 3 judges, a finisher, 2 critical reviewers (3 for ads, with a creative director), a fix pass and a second review (up to 20 agents, 22 for ads) | measured 83 to 122 min for 15 to 30 s pieces from 16 clips |

Add the analysis: proxies are made 2 to 5 times faster than real time for 4K camera files on a recent computer
(slower on older ones), transcription takes 2 to 3 minutes per hour of speech on Apple Silicon (about 7 on a plain
CPU), and building plus checking in Resolve about 10 minutes. These are estimates: longer footage and longer pieces
take longer, and most of the time is agents reading transcripts and looking at images. Best uses much more of your
Claude plan's usage than standard or quick (a full best run on a 30 s piece processed 100 to 150 million tokens);
on smaller plans it can hit the usage limit partway. Then the run stops cleanly and Claude resumes it after the
limit resets, reusing every finished agent. Run one best-tier job at a time: three at once hit the plan limit after
about an hour. If you do not pick a tier, Claude asks, and uses best if you do not mind the wait.

## What changes in your project, and how to undo it

Before building, the skill exports your current timeline to `LAB/backup/<time>/` (a `.drt` timeline file, an OTIO
file and a JSON list of every clip; `LAB` is the lab folder above). If that export fails, it asks you to export the
timeline yourself with File > Export > Timeline before going on.

The build:
- creates a new timeline `<edit name> <version> (resolve-editor)` in the bin `resolve-editor/<edit name>` and
  makes it the current timeline. Your timelines stay exactly as they were;
- places your original clips with the planned in and out points, speeds, fades, punch-ins, audio levels,
  transitions and markers, and stills such as a logo card at their exact length;
- builds titles and captions as Text+ in the looks of the preset and your brand kit (font, colours, logo), on a
  graphics timeline `<edit name> <version> graphics (resolve-editor)` that the new timeline shows. Change a word
  there and the programme follows. Long-form pieces can take their captions as an `.srt` file for the platform
  instead;
- builds music that dips under the voice as pieces at two levels joined by short crossfades, and sets every clip's
  volume to the planned mix: one gain for the whole mix brings it to the loudness target (-14 LUFS for social) as
  far as the true peak allows. When something is left (a piece with loud peaks), the hand-over says how much and
  asks you to tick Normalize Audio Levels and choose Optimize to Standard on the Deliver page's Audio tab (Resolve
  has no script setting for a limiter);
- names every track (Picture, Overlays, Titles, Titles 2, Captions, Dialogue, Music), names and colours the items
  it makes (titles, captions, music pieces, stills, clips with effect comps) and leaves a Blue hand-off marker at the
  start whose note, also in the timeline's Comments, lists what depends on what;
- builds the effects of the approved version inside each clip's own Fusion comp (keyed zooms, speed ramps and
  slow motion, and the impact accents the skill's catalogue enables), the transitions from the catalogue (dissolves,
  Fusion transitions with motion blur, and custom whips and zooms through the cut), animated titles and captions as
  keyed Text+, and sound effects from your own files on their own track. The preview showed the same motion frame by
  frame, and the check afterwards compares the keys and grabbed frames with it;
- adds a render job only when you ask, and never an upload; for an ad it can also make a textless copy of the
  timeline (titles and captions switched off) for re-versioning, again only when you ask;
- imports media into that bin only if some clips are not in your media pool yet, and only after you agree.

Then it reads the new timeline back and compares every item with the plan. If anything differs it tells you which
items and where; it never tries to repair your project on its own.

To undo: delete the new timeline (and the skill's bin, if media was imported). Nothing else was changed. The backup
`.drt` restores the timeline that was current at the start with File > Import > Timeline, in case anything ever
went wrong.

## Privacy

The lab runs on your computer. Your footage is read locally by ffmpeg, transcribed locally by whisper, and the
proxies, previews and analysis stay in the lab folder. Nothing is uploaded by the skill. As in any Claude
conversation, the transcripts, contact sheets and preview images that Claude reads while editing are sent to
Claude as part of the conversation.

## Disk use

Per edit: proxies take about 15 to 30 MB per minute of footage (the more frames per second and the busier the
picture, the more) plus about 12 MB per minute
of audio, and each reviewed version adds a preview of about 20 MB per minute of the edit. The whisper model takes
142 or 466 MB once. When an edit is done, ask Claude to "clean up the proxies of this edit" (it runs the lab's
`clean --proxies`; a later revision makes them again), or delete the whole lab folder once you no longer need its
versions and backup.

## Free version (handoff mode) and no Resolve at all

Free Resolve 21.1 has no scripting, so nothing can be built or checked automatically. Tell Claude where the
footage is on disk; it analyses and edits in the lab as usual and then gives you:
- a timeline file (FCPXML, when this version has the export) to bring in with File > Import > Timeline; if clips
  show as offline, relink them to the original files;
- an `.srt` caption file (File > Import > Subtitle);
- the preview and the report, so you can check the edit.
The result is unverified: the timeline file has no fades, no effects (keyed zooms, ramps, animated text, Fusion
transitions) and the music sits at one level, so check fades, music levels and punch-ins by eye and ear (Claude
can give you the music with its dips as a separate file: put it at 0 dB in place of the song). The imported timeline starts at 00:00:00:00, and its markers sit on the first clip.
For a vertical edit from wider clips, set the timeline's mismatched resolution option to Scale full frame with crop
(Timeline Settings, Use Custom Settings), or slowed clips show black bars.
Without Resolve at all it works the same; the preview is a low-resolution check, not a finished video.

## What it cannot do yet

- Sync sound recorded separately (a lav, a recorder, a podcast interface), several cameras of one moment, or
  lip-sync takes to a song. Each clip plays its own camera sound. Sync such clips in Resolve first, or use the
  separate sound only as a voice-over.
- Find dissolves and fades inside footage that was already edited (hard cuts are found).
- Build every effect: the effects come from the skill's catalogue (keyed zooms, speed ramps, slow motion,
  transitions, animated captions and titles, and the impact accents it enables). Text behind a person, emoji and
  sticker pops, split screens, progress bars, film looks, glow and tracking a label to a moving object are not
  built; Claude tells you the manual steps for the ones a piece needs. Audio levels cannot be keyed by script, so a level change is a cut between two items. Effects
  live in the clips' Fusion comps, which Resolve processes before grading: play each flash or light leak once after
  you grade log footage. A few previews are approximate (Speed Warp slow motion, light leaks, glitches, some Fusion
  transitions): those frames are compared by eye, not pixel by pixel.
- Put captions on a Resolve subtitle track by itself: placing a subtitle clip by script crashed Resolve 21.1, so
  the captions are Text+ (or an SRT file you import yourself).
- Choose a camera's audio channels by itself: Resolve places each clip's first audio track, and the preview uses
  the same. When a clip has several tracks, or its voice on one side of a stereo track (it would play in one ear),
  Claude tells you and you set the channels in Clip Attributes > Audio before the edit.
- Put two speakers recorded on separate channels (a two-microphone kit in stereo, guest left and host right) in
  the centre. The skill finds such clips and never sets them to one channel (that would drop a speaker), but the
  preview and the build play one speaker in each ear. After the build you put those clips on an audio track of their
  own and set that track's Pan Spread to 1 (PNT) in Resolve's Fairlight mixer. The skill has not measured that
  setting itself; its delivery QC checks the render and stops while each ear still hears a different speaker.
  When the two microphones were set at very different gains, the quieter speaker can be missed and the clip called
  one-sided. The note then says the other channel is not silent and asks you to listen before choosing a channel.
- Guarantee the beat grid: measured on real drum loops and grooves, the grid lands on the beats about 6 times in
  10 without help, and half of the misses carry no warning. A tempo in the file name ("loop 100 BPM") is used when
  the file is a whole number of bars at it. When a warning fires, Claude sends you a click file (the music with a
  click on every beat and a high click on bar 1) and asks one question: does the high click land on beat 1? You can
  also give the tempo and where bar 1 falls (it cannot listen itself). With the tempo and a bar-1 time within 40 ms of the beat (read
  it on Resolve's audio waveform) the grid was right in 269 to 271 of 291 test grooves. A time typed by ear is often
  60 to 100 ms off: then the grid stays as found (239 to 262 of 291) and Claude tells you which beat became bar 1.
  The grid moves by itself only when the found beats sat half a beat off your bar line; otherwise it moves only when
  you confirm your time is exact.
- Follow a moving subject in a vertical crop of wider footage: each shot gets one fixed framing, taken from where
  the shot log places the person, so a subject who crosses the frame needs the shot split or a wider shot. There is
  no automatic face tracking.
- Put every edge exactly between two words that run together: with no pause and no frame edge close to the word
  boundary, an edge can run up to about one frame into a word (40 ms at 25 fps, more when the clip's frame rate
  differs from the timeline's). It never cuts away half of a short word. Those cuts are listed for a listen.

## Troubleshooting

- **Claude Code does not know the skill**: `SKILL.md` must sit directly in `.claude/skills/resolve-editor/`, not
  one folder deeper (see "Install"). `bash install.sh --check-location` (Windows: `install.ps1 -CheckLocation`)
  tells you whether the place is right.
- **Resolve tools missing in Claude Code**: see "Connect Resolve". Resolve must be running. `/mcp` shows the
  server state. Restart Claude Code after File > Setup AI Assistants.
- **No transcript, so no captions or word editing**: install whisper (see "Requirements") and ask Claude to run
  the analysis again; finished steps are skipped.
- **Analysis is slow**: 10-bit 4:2:2 camera files decode slowly on some computers. Let it run in the background, or
  start with the clips the edit needs.
- **A file could not be read**: RAW formats and some damaged files cannot be decoded by ffmpeg; the rest of the
  footage is analysed. Render a ProRes or H.264 copy of that file.
- **"missing media" when building**: some clips are not in your media pool. Claude asks with the brief whether it
  may import them into its own bin, and builds with that permission; without it, nothing changes.
- **"frame rate mismatch" when building**: the edit was planned at another frame rate than your project. The skill
  never changes project settings; it re-plans at the project's rate.
- **The run stopped at the usage limit** (or with `STAGE_FAILED`): nothing is lost. After the limit resets, ask
  Claude to resume; it calls the workflow again with the same settings and the stopped run's id, and every finished
  agent comes back from the cache.
- **The standard or best tier returns nothing useful**: the subagents were probably blocked by permission
  questions (see "Before the first run"). Allow them and resume, or try standard or quick.
- **Captions on a subtitle track**: the skill never places subtitle clips by script (it crashed Resolve 21.1). If
  you want a subtitle track of your own, import the `.srt` with File > Import > Subtitle and drag it on by hand.
- **Workflow tool unavailable** (plan or organisation policy): the skill runs the same stages with ordinary
  subagents, or does the quick tier inline.
- **Claude cannot read the workflow script** because the skill folder is outside the project: Claude passes the
  script text directly instead. See "Before the first run" for `/add-dir`.
- **Captions have wrong words or names**: give Claude the voice-over script or the right spellings; the transcript
  is aligned to the script (or the one word fixed), and the captions are made again. The skill never guesses a
  word it is unsure of: it asks you.
- **The verify step reports differences**: Claude lists the items and times. The usual causes are clips without
  enough extra footage for a transition, or a clip replaced in the media pool. A rebuild goes to another new
  timeline.

## Files

| file | what it is |
|---|---|
| SKILL.md | instructions for Claude |
| KNOWLEDGE.md | editing craft notes, rules and check ids the agents read |
| BRIEF_TEMPLATE.md | the brief Claude fills in for each edit |
| workflow.js | the standard and best tiers for the Workflow tool |
| media_lab.py | footage analysis: proxies, shots, transcripts, pauses, beats, loudness, quality, contact sheets, delivery QC |
| edit_lab.py | the edit: cut lists to EDLs, previews, review packs, checks, captions, Resolve scripts |
| fx_lab.py, fx_catalog.json | effects, transitions and animated text: the catalogue (every key, its Resolve route, preview and checks) and the offline code that plans and checks them |
| fusion_recipes.py | the Fusion code the Resolve scripts use to key effects, rebuild transitions and animate Text+ |
| resolve_semantics.json | measured facts about Resolve's scripting behaviour that the build relies on |
| presets/ | presets per kind of piece (length, pacing, captions, loudness, story angles) |
| install.sh, install.ps1 | setup |
| requirements*.txt | Python packages (runtime, optional transcription, tests) |
| tests/ | unit tests (`.venv/bin/python -m unittest discover -s tests`) |

## Sharing

See SHARING.md: free to share with friends, no warranty, and how to zip the folder without your `.venv` and
models.
