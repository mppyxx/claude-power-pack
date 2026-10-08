# Sharing resolve-editor

## The short version

You are free to use this skill, change it and pass it on to friends. There is no formal licence and no warranty:
it is a hobby tool shared between people who edit their own footage. Watch the edit with your own eyes and ears
before you deliver it to anyone, and keep the backup the skill makes before it builds anything.

## What is and is not inside

- All the code in this folder was written for this skill. It contains no code or files from Blackmagic Design.
  DaVinci Resolve, its scripting API and its AI assistant server belong to Blackmagic Design and are used as they
  ship with Resolve Studio.
- The editing rules in KNOWLEDGE.md are summaries in our own words of public standards, platform help pages,
  published research and common editing practice; the sources are named below.
- No whisper model is included. The installer can download one from the whisper.cpp project's model repository
  when you ask for it (`--asr`); whisper and whisper.cpp are MIT licensed.
- Apart from the install script asking pip to download numpy, pillow and (optionally) pywhispercpp, and the
  optional model download, nothing in this folder talks to the internet. Your footage, transcripts and edits stay
  on your computer.

## Credits for the rules and numbers

- Editing: Walter Murch, In the Blink of an Eye (rule of six); Edward Dmytryk, On Film Editing; Tim J. Smith, The
  Attentional Theory of Cinematic Continuity (2012); James Cutting and colleagues on shot length, pace and the
  dissolve in Hollywood film; Nick Redfern on cutting rates; Pretet, Richard and Peeters on music video editing
  (ISMIR 2021); Law, Li and Narechania on how professional editors evaluate AI-generated video ads (2026); Lang
  and colleagues on pacing and arousal (1999).
- Timing and loudness standards: ITU-R BT.1359 (audio and video timing), ITU-R BS.1770, EBU R 128 and R 128 s1,
  ATSC A/85, AES TD1008, W3C WCAG 2.2 (1.4.7 background audio, 2.3.1 three flashes).
- Captions: the Netflix Timed Text Style Guide; BBC subtitle guidance as widely summarised.
- Platforms: Meta for Business and Instagram Help (Reels specs, safe zones, hooks), YouTube Help (Shorts, upload
  settings, retention), Google Ads (ABCDs of effective video ads, safe-zone overlays), TikTok for Business creative
  best practices, LinkedIn Help, Apple Podcasts audio requirements.
- Effects, transitions and animated text: the studies and platform guidance named in KNOWLEDGE.md section 15
  ("Where the numbers come from"), ITU-R BT.1702 and Ofcom on flashes, and measurements made for this skill in a
  Resolve 21.1 sandbox project. The `source` field of each `fx_catalog.json` row names the research note behind it;
  those notes are not part of the folder.
- Methods: dynamic-programming beat tracking (Ellis, 2007); GCC-PHAT time delay estimation (Knapp and Carter,
  1976); the open-source tools auto-editor, OpenTimelineIO, Kdenlive, LosslessCut and PySceneDetect for ideas on
  margins, timelines, chunked previews and shot sheets.

## How to share it

Share the folder without the `.venv`, `models` and `__pycache__` folders (they are machine specific and large).
Your friend runs the install script on their own computer, which rebuilds `.venv` and, if they want local
transcription, downloads a model.

macOS or Linux, from the folder that contains `resolve-editor`:

    zip -r resolve-editor.zip resolve-editor -x "resolve-editor/.venv/*" "resolve-editor/models/*" "*/__pycache__/*" "*.pyc" "*/.DS_Store"

Windows PowerShell, from the folder that contains `resolve-editor`:

    $src = "resolve-editor"; $tmp = Join-Path $env:TEMP "resolve-editor-share"
    robocopy $src (Join-Path $tmp "resolve-editor") /E /XD .venv models __pycache__ /XF *.pyc
    Compress-Archive -Path (Join-Path $tmp "resolve-editor") -DestinationPath resolve-editor.zip -Force
    Remove-Item -Recurse -Force $tmp

Your friend unzips it into `~/.claude/skills/` (Windows: `%USERPROFILE%\.claude\skills\`) so that
`~/.claude/skills/resolve-editor/SKILL.md` exists, and follows README.md from "Install". Windows "Extract All"
proposes a folder named after the zip and so adds a second `resolve-editor` level; tell your friend to extract
into the `skills` folder itself, or to move the inner folder up afterwards. The installer refuses to run from the
wrong place and says how to fix it.

Before you share, make sure no lab folders, previews, transcripts, voice-over scripts, `brand.json` or `dump.json`
files ended up inside the skill folder: they contain your project's clip names, file paths, your brand kit and what
people said. The labs live in
`~/resolve-editor-labs/` by default, outside the skill.
