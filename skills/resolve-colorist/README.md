# resolve-colorist

A Claude Code skill that color grades a DaVinci Resolve timeline the way a careful colorist would, and does the
tedious parts for you. It reads your timeline, recognises your camera's log format, white balances and exposes
every shot, matches shots and split-screen panels to each other, designs and compares looks, applies the result
in Resolve as LUTs, and then checks real frame grabs from Resolve against what it approved.

What it touches in your project: node 1 of each graded clip (one LUT), one color group per timeline with the look
LUT on its post-clip node, and new LUT files in a subfolder of Resolve's LUT folder. It exports a backup of the timeline
first. What it never does on its own: change the color science or other project settings, reset existing grades,
move clips out of your color groups, add render jobs or delete anything. It asks you before any of those.

## Requirements

- DaVinci Resolve Studio 21.1 or newer. The free version has no scripting since 21.1, so it only gets a manual
  mode (the skill makes the LUTs and tells you where to put them; see "Free version" below).
- A project with color science DaVinci YRGB (the default). Color managed (RCM) and ACES projects are refused
  rather than changed.
- Claude Code. The standard and best tiers use Claude Code's Workflow tool, which is not available on every plan
  or setup and can be turned off by an organisation. Without it the skill runs the same steps with ordinary
  subagents, or the quick tier.
- Python 3.10 or newer.
- ffmpeg and ffprobe on your PATH (macOS: `brew install ffmpeg`; Windows: `winget install Gyan.FFmpeg`, then open
  a new terminal window and restart Claude Code so both see the new PATH; Linux: your package manager).
- Footage the lab can decode: log or standard video in H.264, H.265, ProRes, DNx and similar. RAW formats (BRAW,
  R3D, ARRIRAW, Canon RAW, ProRes RAW) are not supported; see "Troubleshooting".

## Install

1. Put the `resolve-colorist` folder into your Claude Code skills folder, so that `SKILL.md` sits directly in it:
   - macOS and Linux: `~/.claude/skills/resolve-colorist/SKILL.md`
   - Windows: `%USERPROFILE%\.claude\skills\resolve-colorist\SKILL.md`
   - If you run Claude Code with a custom config folder (`CLAUDE_CONFIG_DIR`), its `skills` folder works too.

   Unzipping often adds one folder level too many (Windows "Extract All" makes
   `resolve-colorist\resolve-colorist\SKILL.md`). Claude Code does not find the skill there: move the inner
   `resolve-colorist` folder up one level. The installer checks this and stops with the same advice.
2. Run the installer from inside that folder. It creates a private Python environment in `.venv` inside the
   folder and installs numpy and pillow. It needs no admin rights and changes nothing else.
   - macOS and Linux: `bash install.sh`
   - Windows: `powershell -ExecutionPolicy Bypass -File install.ps1`

   Options: `--recreate` / `-Recreate` rebuilds `.venv`, `--mcp` / `-Mcp` also registers Resolve with Claude
   Code (next section), `--dev` / `-Dev` adds the test packages, `--check-location` / `-CheckLocation` only checks
   the folder's place.
3. The installer checks ffmpeg and ffprobe and then runs `grade_lab.py doctor`, which reports anything else that
   is missing (first line `DOCTOR: OK` when all is well).

## Connect Resolve to Claude Code

Resolve Studio 21.1 ships its own AI assistant server (ResolveMCP).
1. In Resolve: File > Setup AI Assistants, choose Claude Code. Then restart Claude Code.
2. Or register it yourself (or run the installer with `--mcp`):

       claude mcp add --scope user davinci-resolve -- "<path to ResolveMCP>"

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
folder it was started in. Grading runs many lab commands and reads many preview images, and the look designers in
the standard and best tiers are subagents that may not be able to answer those questions, so allow them once:
- `/add-dir ~/.claude/skills/resolve-colorist` and `/add-dir ~/resolve-colorist-labs` (create that folder first),
  so Claude can read the skill files and the preview images;
- the first time Claude Code asks to run the skill's Python (a command that starts with the full, quoted path of
  `.venv/bin/python`, on Windows `.venv\Scripts\python.exe`, inside the skill folder), answer "Yes, and don't ask
  again" for that command. Claude Code then writes the matching permission rule itself, which is more reliable than
  typing one by hand in `/permissions`, because the rule has to match the command exactly as Claude runs it.
Resolve's own tools still ask each time unless you allow them too; the skill only ever runs its own scripts there,
and you can read each one in the lab's `snippets` folder first.

## First run

Open the project and the timeline in Resolve, start Claude Code in any folder and ask, for example:

- "Color grade my current Resolve timeline for Instagram Reels. Shot on a Sony FX3 in S-Log3. Clean, premium look."
- "Match all the shots in this timeline and give it a warm film look. Quick tier, I am in a hurry."
- "Grade this timeline, but show me a few looks to choose from before you finish."
- "Check my export for Instagram: ~/Desktop/reel_final.mp4"

Claude will ask a few questions (platform, mood, how long you can wait), show you what it found (cameras, clips
that already have grades, anything risky), and ask before anything that changes your project beyond the grade.
The working files go to `~/resolve-colorist-labs/<project>-<timeline>/` (with a short id added when the names
contain spaces, accents or anything else besides plain letters, digits, `-` and `_`). Keep that folder: it holds the
backup and the final grade settings. After you rename the timeline or the project, Claude finds the old folder by
the timeline's id.

Do not edit the timeline (move, add, delete, replace or trim clips) while Claude grades it, until it has checked
the result in Resolve at the end: the grade is made for the timeline as it was read. If the apply or check step
notices a change, it stops and Claude reads the timeline again.

## Tiers and time

| tier | what happens | rough time for a 10 to 20 clip reel |
|---|---|---|
| quick | Claude compares a few preset looks on your auto-balanced footage and trims the best one | 10 to 15 min |
| standard | a reviewer checks the automatic balance, 2 look designers compete, a judge picks, a finisher polishes, a reviewer checks | 25 to 40 min |
| best (default) | 3 auditors and a baseline lead, 4 look designers, 3 judges, a finisher, 2 critical reviewers, a fix pass and 2 more reviewers (up to 17 agents) | 50 to 80 min |

Best uses much more of your Claude plan's usage than standard or quick; on smaller plans it can hit the usage limit
partway through. These are estimates. Most of the time is agents looking at preview images; the number crunching takes a few
minutes. Longer timelines take longer. Each step before and after the grade (reading the timeline, checking,
applying, verifying) adds about 5 to 10 minutes. If you do not pick a tier, Claude asks, and uses best if you do
not mind the wait.

## What changes in your project, and how to undo it

Before applying, the skill exports your timeline to `LAB/backup/before_<time>.drt` plus a list of every node LUT
(`LAB` is the lab folder, see "First run"). If that export fails, it asks you to export the timeline yourself with
File > Export > Timeline before going on.

The apply step:
- writes LUT files to Resolve's LUT folder, in a subfolder named after your project (`<name>/Final_Conv_*.cube`
  per clip and `<name>/Final_Look.cube`). Every new version gets a new name (Final2, ...); existing LUT files are
  never overwritten without asking;
- puts the clip's conversion LUT on node 1 of each graded clip, but only on clips whose grade is empty or a single
  LUT node; clips with other grades are skipped and listed;
- creates a color group for this timeline (default `<name> <timeline> grade`), adds the graded clips that are not
  in another group, and puts the look LUT on the group's post-clip node. A group's look is shared by its clips in
  every timeline of the project, so each timeline gets its own group, and the skill stops and asks before it
  grades into a group that also holds clips of another timeline. If you ask it to use an existing group that
  already has its own post-clip grade or LUT, it also stops and asks before replacing it. Labs graded with an
  earlier version of the skill keep the group they already use.

To undo:
- File > Import > Timeline and choose the `.drt` from the backup folder. You get the timeline as it was, next to
  the graded one.
- Or by hand: remove the LUT from node 1 of each clip and from the color group's post-clip node.
- Group grades live in the project, not in the timeline. If the grade was applied to a color group that already
  existed, importing the .drt does not restore that group's post-clip grade; its previous LUT is listed in
  `LAB/backup/snapshot_*.json` and has to be put back by hand.
- The LUT files can stay; they do nothing unless used.

Disk space: every version (TAG) writes a full set of LUTs, about 7.5 MB per clip plus 7.5 MB for the look, so a 17
clip timeline takes roughly 130 MB per TAG (about 25 MB when every clip is Sony S-Log3 / S-Gamut3.Cine, whose
LUTs are smaller). To clean up, delete the
`<TAG>_*.cube` files of an old TAG from the LUT subfolder, but only for a TAG that no timeline in any project uses
any more: Resolve looks LUTs up by file name, and a timeline you restore from a backup `.drt` still points at the
LUTs it had then. The lab's `luts_<TAG>.json` files show which TAGs exist; the TAG of the grade on screen is the
one in the node 1 LUT names (`<TAG>_Conv_...`). If in doubt, keep them.

Adding texture after the grade: grain, halation, glow, sharpening and vignettes cannot live in a LUT, so Claude
suggests them for you to add by hand, best on a new node after the look node in the color group's post-clip graph
(the Timeline node works too, but it also puts the texture on titles and logos). Put only those effects there, and
use the Vignette effect rather than a window with a gain change. Later tweaks leave them in place; a color
correction there would make the next apply stop and ask.

Set Project Settings > Color Management > 3D Lookup Table Interpolation to Tetrahedral (scripts cannot set it),
and leave the project's Input and Output Lookup Table empty.

## Delivery tips for social

- In the render settings' Advanced Settings: Color Space Tag Rec.709 and Gamma Tag Rec.709 (Scene) or Rec.709-A,
  so the file is tagged 1-1-1 (what phones and browsers expect). Switching the timeline color space does the same,
  but it changes a project setting, and effects such as Halation follow the timeline color space.
- Data levels Auto or Video, never Full.
- 1080x1920, H.264 High (or H.265), 20 to 40 Mbps, the timeline frame rate, AAC 48 kHz, no timecode track.
- Turn on Instagram "Upload at highest quality" and TikTok "Upload HD", and upload on Wi-Fi.
- Macs, and most likely iPhones too, show Rec.709 exports lighter and flatter in the shadows than Resolve's
  viewer. That is how Apple displays video; do not bake a correction into the file. Check the export on a phone.
- Ask Claude to check the export ("check my export for Instagram"): it simulates the platforms' re-encoding and
  reports banding, blocking and lost shadow detail. It needs an ffmpeg with libvmaf, SVT-AV1, libvpx, x264 and
  x265. The Homebrew ffmpeg and Gyan's full build on Windows have them; many Linux distribution builds lack libvmaf
  or SVT-AV1, so use a static build (for example from BtbN or johnvansickle) and point the `RC_FFMPEG` and
  `RC_FFPROBE` environment variables at its ffmpeg and ffprobe. Profiles whose encoder is missing are skipped.

## Troubleshooting

- **Claude Code does not know the skill**: `SKILL.md` must sit directly in `.claude/skills/resolve-colorist/`, not
  one folder deeper (see "Install"). `bash install.sh --check-location` (Windows: `install.ps1 -CheckLocation`)
  tells you whether the place is right.
- **LUT folder not writable** (common on Linux and for non-admin users): in Resolve, Preferences > System >
  General, add a LUT location in a folder you own and restart Resolve. Then tell Claude that folder (it sets
  `lut_root` in the lab's timeline.json), or set the `RESOLVE_LUT_ROOT` environment variable.
  Default LUT folders: macOS `/Library/Application Support/Blackmagic Design/DaVinci Resolve/LUT`, Windows
  `C:\ProgramData\Blackmagic Design\DaVinci Resolve\Support\LUT`, Linux `/opt/resolve/LUT`.
- **Resolve tools missing in Claude Code**: see "Connect Resolve". Resolve must be running. `/mcp` shows the
  server state. Restart Claude Code after File > Setup AI Assistants.
- **Free version of Resolve**: manual mode. Claude reads your media files directly, grades in the lab, writes the
  LUTs to a folder you choose and gives you step-by-step instructions to apply them. It cannot check the result
  in Resolve.
- **Color managed (RCM) or ACES project**: the skill stops. Changing color science would change your existing
  grades, so it never does that. Duplicate the project, set the copy to DaVinci YRGB and grade the copy.
- **RAW footage**: the lab cannot read RAW. Render those clips to ProRes or DNxHR in their log format first and
  grade those, or grade the RAW clips by hand.
- **DJI D-Log M or GoPro GP-Log**: their makers have not published the formulas, so they are not built in. Download
  the maker's official D-Log M or GP-Log to Rec.709 LUT and tell Claude where it is; the skill then decodes the
  footage through that LUT (marked approximate). It has to be a plain 3D .cube; LUTs with a 1D shaper need
  converting first.
- **Some clips were skipped by apply**: they already had grades (`not_clean`) or belong to another color group
  (`in_other_group`). Claude asks whether to reset those grades or move the clips; it never does it on its own.
- **Grading a second timeline changed the first one's look**: earlier versions of the skill used one color group
  for every timeline of a project, and a group's look is shared by all its timelines. The skill now gives every
  timeline its own group and warns (`group_shared`) when a group is already shared. To separate two timelines
  graded that way, let Claude move one timeline's clips into its own group (it asks first), then re-apply the
  other timeline's grade from its own lab.
- **"the timeline changed since it was read"** (apply or the frame grabs stop with `timeline_changed`): clips were
  added, deleted, replaced, moved or trimmed after Claude read the timeline. Nothing was changed. Claude reads it
  again, matches only the new clips (the rest keep their trims), bakes the LUTs under a new name and re-applies.
- **You edited the timeline after the grade** (clips moved, deleted or replaced): tell Claude. Reading the timeline
  again records which clips moved, so their per-clip trims stay with them; a clip replaced by another shot is
  reported, and its frames are read again.
- **"this lab holds project ... timeline ..."**: two timelines ended up with the same lab folder name, or the
  project was imported or restored (.drp, .dra), which gives it new ids. Nothing was written; Claude uses a new lab
  folder. After an import or restore it copies the final grade settings into the new folder and re-keys them there,
  so the grade carries over.
- **Compare flags clips with low correlation**: those panels are animated (slide-ins, keyframed zoom or position,
  transitions). The check compares against a still layout, so this is expected; look at the grab to be sure.
- **Workflow tool unavailable** (plan or organisation policy): the skill runs the same stages with ordinary
  subagents, or does the quick tier inline.
- **Claude cannot read the workflow script** because the skill folder is outside the project: Claude passes the
  script text directly instead. See "Before the first run" for `/add-dir`.
- **The standard or best tier returns only the baseline**: the subagents were probably blocked by permission
  questions. See "Before the first run".
- **The export looks different on a Mac**: see "Delivery tips". Compare on a phone.

## Supported cameras

Run `.venv/bin/python cameras.py list` (Windows: `.venv\Scripts\python.exe cameras.py list`) for the exact table of
your copy. Status: *verified* = the maker's formula checked against an independent reference, *official* = the
maker's formula, no independent check, *approximate* = a best effort, *display* = normal video, not log.

| key | format | status |
|---|---|---|
| slog3_sg3c | Sony S-Log3 / S-Gamut3.Cine | verified |
| slog3_sg3 | Sony S-Log3 / S-Gamut3 | verified |
| slog2_sg | Sony S-Log2 / S-Gamut | verified |
| applelog | Apple Log (iPhone 15 Pro, 16 Pro) | verified |
| applelog2 | Apple Log 2 / Apple Wide Gamut (iPhone 17 Pro) | official |
| dlog_dgamut | DJI D-Log / D-Gamut | verified |
| dlog2_dgamut2 | DJI D-Log2 / D-Gamut2 | official |
| vlog_vgamut | Panasonic V-Log / V-Gamut | verified |
| clog_cg | Canon Log / Cinema Gamut | verified |
| clog2_cg | Canon Log 2 / Cinema Gamut | verified |
| clog3_cg, clog3_2020 | Canon Log 3 / Cinema Gamut or BT.2020 | verified |
| flog_fgamut | Fujifilm F-Log / F-Gamut | verified |
| flog2_fgamut | Fujifilm F-Log2 / F-Gamut | verified |
| flog2c_fgamutc | Fujifilm F-Log2 C / F-Gamut C | verified |
| nlog | Nikon N-Log / BT.2020 | verified |
| logc3_awg3 | ARRI LogC3 (EI 800) / AWG3 | verified |
| logc4_awg4 | ARRI LogC4 / AWG4 | verified |
| bmd_gen5 | Blackmagic Film Gen 5 / Blackmagic Wide Gamut | verified |
| di_dwg | DaVinci Intermediate / DaVinci Wide Gamut | verified |
| log3g10_rwg | RED Log3G10 / REDWideGamutRGB | verified |
| gplog2 | GoPro GP-Log2 / Rec.2020 (GoPro Labs) | official |
| protune_native | GoPro Protune log (older models, HERO4 to HERO7) | verified |
| samsung_log | Samsung Log / BT.2020 | official |
| ilog | Insta360 I-Log / BT.2020 | official |
| llog_2020 | Leica L-Log / BT.2020 | verified |
| hlg_2020 | HLG / BT.2020 (phone HDR video, including iPhone) | verified |
| hlg_709 | HLG with Rec.709 primaries (camera HLG with the BT.709 color option) | verified |
| pq_2020 | PQ / BT.2020 (HDR10 video) | display |
| rec709 | Rec.709 video (standard phone and camera video) | display |
| rec2020_sdr | Rec.2020 SDR video (gamma 2.4) | display |
| srgb | sRGB (screen recordings) | display |
| p3_display | Display P3 video (some phone apps) | display |
| `lut:/path/to/file.cube` | any camera through the maker's log-to-Rec.709 .cube LUT | approximate |

Not built in because their makers have not published the formulas: DJI D-Log M, GoPro GP-Log (HERO12 and
newer), Kinefinity KineLog3, Xiaomi Mi-Log, OPPO O-Log, vivo Log and Z CAM Z-Log2. For these, download the
maker's official log-to-Rec.709 LUT and use it through the `lut:` key.

Only Sony S-Log3 / S-Gamut3.Cine has been checked end to end against Resolve (lab prediction within 0.005 luma
of Resolve's own frames). The others use the same machinery with the published formulas; the verify step checks
every grade against Resolve anyway. Normal video (rec709, srgb, p3_display) is graded without a second filmic
tone curve, so it keeps its look when no grade is applied, but it has less room than log.

## Privacy

The lab runs on your computer. Your media is read locally by ffmpeg, and frames, renders, LUTs and settings stay in
the lab folder and Resolve's LUT folder. Nothing is uploaded by the skill. As in any Claude conversation, the
preview images and text that Claude reads while grading are sent to Claude as part of the conversation.

## Files

| file | what it is |
|---|---|
| SKILL.md | instructions for Claude |
| KNOWLEDGE.md | grading craft notes the agents read |
| BRIEF_TEMPLATE.md | the brief Claude fills in for each project |
| workflow.js | the standard and best tiers for the Workflow tool |
| grade_lab.py | the lab: renders, balance, matching, LUTs, Resolve scripts, checks |
| cameras.py | camera log formats, detection, frame decoding, .cube reading |
| social_qc.py | the social media export check |
| presets/ | look presets |
| install.sh, install.ps1 | setup |
| tests/ | unit tests (`.venv/bin/python -m unittest discover -s tests`) |

## Sharing

See SHARING.md: free to share with friends, no warranty, and how to zip the folder without your `.venv`.
