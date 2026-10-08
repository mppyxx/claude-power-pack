# <Edit name>: edit brief

Fill every <...>. Write "unknown" rather than guessing. Every agent reads this file first.

## The piece
- What it is: <a 45 s Instagram reel about ..., a 4 min interview film, a 15 s ad for ...>
- For whom: <audience, where they will see it, what they already know>
- The feeling it should leave, and the one thing to remember: <...>

## Deliverables
- Platform and preset: <reels / tiktok / shorts / youtube / linkedin / x / square>, preset `<preset id>`
- Aspect and size: <1080x1920 | 1920x1080 | 1080x1080>; frame rate: <from the Resolve project, for example 25>
- Length: <target in seconds, and the hard limits (ads: exactly 15 or 30 s)>
- Tier: <quick | standard | best>. Story angles: <for example hook_first, emotional_arc>. Cut styles: <for example
  tight, breathing>
- Other versions later: <a 9:16 cutdown, a 6 s bumper, ...> (each gets its own lab or version)

## Story and message
- Message or logline: <...>
- Structure wanted: <hook first | chronological | problem to solution | emotional arc | music led | open>
- Must include: <moments, lines (with media id and word range when known), shots, facts, a call to action>
- Must avoid: <topics, lines, people, shots, anything the client dislikes>
- What must never be shown? <a child or a bystander next to a working station, an unsafe practice, a messy corner,
  another company's label or logo, a person who did not agree to appear, decor with no story | none>. It overrides
  any shot score, and a picture that contradicts its line (an unsafe scene under a safety line) is a ship blocker
- What should the viewer do next, and how (the route: link in bio, URL, phone, button): <"Book a free visit, link
  in bio" | none for a piece with no next step>
- The call-to-action route, exactly as it must appear on screen: <"Link in bio" | a short URL or handle | the phone
  number | the platform button>. On a vertical card the call-to-action line is at most 2 lines of about 12
  characters (84 px in a pill), so a long URL goes in the platform button or the post's caption text, not on the card
- End-card copy: the name <"Ink Studio"> and the call-to-action line <"Book your\nfree visit">: they lock up
  together on the card for its whole length (3.5 to 5 s), the call to action the largest text, held 2.5 s or more;
  the route there is the button, the caption text or a short line such as "Link in bio"
- The offer or reason to act, word for word, and where it must land: <"Admissions open now", by 40 % of an ad>
- A paper edit from the user: <none | the text, or where it is>

## Voice-over
- Voice-over script (paste it word for word): <the exact text the voice reads, or "none". It corrects the
  transcript (`M "LAB" script`), so captions never show a misheard word>
- Voice-over media id: <id> ; recorded apart from the pictures or made with text to speech: <yes | no>
- Fit: speech <N> s and <N> words for a <N> s piece. When the speech is under about 60 % of an ad's length, the
  user chose: <a shorter piece (15 or 20 s) | a text-led stretch with supers the team writes (fresh shots and new
  proof points from this brief, never the voice's words again) | a longer voice-over | not asked: the voice lines
  spread over the whole length with music between them>
- Proof points the supers may use, beyond what the voice says: <a number, a guarantee, a name the brief allows>

## People
- Speakers: <number; name and role of each, as they should be spelled on screen>
- Who matters most on screen: <...>
- Spelling glossary for captions (names, brands, jargon): <Word = how it must be spelled>

## Music
- Music file(s): <media id, or "none", or "the user will add music later">; rights: <licensed | own | unknown>
- Feel and use: <under dialogue only | drives the cut | ends with the piece>; how it ends: <on its own ending | on a
  hit at a phrase end> (never a fade over a thinning section)
- Where the big moment should land: <the reveal on the drop at about 0:12, ...>

## Tone and references
- Tone words: <three to five, for example "calm, precise, warm, confident">
- References: <videos or accounts the user likes, and what they like about them>
- Pace: <energetic | measured | slow burn (then turn off `hook_3s` in the cut list's checks_off)>

## Effects
- Premium or luxury brand? yes or no: <no> (yes moves a preset to the calm premium budget: slow pushes, slow
  motion on the hero moment, fades; no bounces, flashes, shakes or glitches. A music montage or music video moves to
  the cinematic montage budget instead: it still cuts on the music, without bounces, glitch or big zooms)
- Effects appetite: none, light, normal, bold: <normal> (scales the preset's effects budget by 0, 0.5, 1 or 1.25;
  recorded with `E "LAB" init --premium yes|no --fx <appetite>`)
- Sound effects folder (the user's own whooshes, hits and pops; nothing is downloaded): <path | none>
- Moments that deserve an effect, and effects the user dislikes: <the drop at 0:12 gets a ramp; no glitches; ...>
- Effects never go on text, the logo, the product name or the offer (tag those shots `logo`, `product` or `offer`),
  and no transition effect sits in the first second. Budgets and routes: KNOWLEDGE.md section 15.

## Sound and loudness
- Loudness target: <-14 LUFS integrated, true peak -2 dBTP for a social file (the default of every preset) | -16
  (podcast) | -23 (broadcast) | other>. Another target goes into a lab copy of the preset: copy
  `SKILL/presets/<id>.json` to `LAB/presets/<id>.json` and set `audio.lufs` (and `audio.codec_tp_db`) there before
  the first assemble; the checks, `E "LAB" level` and `deliver-script` all read it. The edit sets one mix gain for the
  target (as far as the true peak allows); any rest is a Deliver page step the hand-over names

## Text and captions
- Captions: <word chunks | sentences | none>, language: <en>, burned in (built as Text+) or delivered as a file
  (SRT for the platform's caption upload). One caption band and one clean look per piece (84 px on a vertical
  frame); a pill when the footage behind the captions is busy or light: <default | pill>
- Titles: <hook text options, supers, the offer line, lower thirds with names and roles, end card text>
- Words that must never appear on screen, and spellings the captions must use: <see the glossary above>

## Branding (also written to `LAB/brand.json`)
- Brand name as it must be written: <...>
- Font: <family and style, and the font file path if it is not installed | none: Arial Bold>
- Colours (hex): primary <#RRGGBB>, text on primary <#RRGGBB>, dark <#RRGGBB>
- Logo mark: <media id or file | none>; wordmark (the name as a graphic): <media id or file | none>. A mark without
  the name does not say who the brand is: the end card then sets the name in the brand font
- Handle and URL: <@handle>, <example.com/visit>; city or area: <...>
- The platform button the ad will use (CTA button): <Book now | Learn more | Sign up | none>
- Claims, prices and names that are allowed on screen: <...>

## Footage notes
- Cameras and formats: <from `M "LAB" status`: resolution, frame rate, log or normal video, variable frame rate
  phone clips>
- Sound: <lav, camera mic, noisy room, music on location, no audio; recorded separately? several cameras of one
  moment? lip sync to a song?>
- Known problems: <soft shots, shaky moves, a false start, a cough at 2:10>
- Where the footage lives: <on the current timeline | in the bin "..." | files on disk>
- May the build import media that is not in the project into the bin `resolve-editor/<edit name>`: <yes | no>

## Constraints of this version
- The edit is built as a NEW timeline in the bin `resolve-editor/<edit name>`, plus a graphics timeline that holds
  its titles and captions as Text+; the user's timelines stay as they are.
- Built in Resolve: clips, stills and logo cards at their exact length, titles and burned-in captions as Text+ in
  the preset's looks (brand font and colours from `LAB/brand.json`), music ducking as level pieces joined by
  crossfades (a retimed music item keeps one level), effects keyed in each clip's Fusion comp, catalogue
  transitions, and animated titles and captions keyed on every frame.
- Effects from the skill's catalogue only (KNOWLEDGE.md section 15): keyed zooms, speed ramps and slow motion,
  transitions, animated captions and titles, sound effects from the user's files, and the impact accents the
  catalogue enables. Not built: text behind a person, emoji and stickers, split screens, progress bars, film looks,
  tracked labels, audio level keys. Effects sit in the clips' Fusion comps, under the grade.
- No audio sync: each clip plays its own camera sound. Separately recorded sound (lav, recorder, podcast
  interface), several cameras of one moment and lip-sync takes to a song cannot be synced by this version.
- Only the first audio track of a multi-channel camera clip is used (the one Resolve places).
- The build names every track, labels and colours the items it makes and leaves a Blue hand-off marker with a
  note in the timeline's Comments. Loudness: the edit's mix gain reaches the target as far as the true peak allows;
  any rest is a Deliver page step (Optimize to Standard). Grading is a separate step.
- Measured gates (KNOWLEDGE.md section 13) must pass before anything is built. KNOWLEDGE.md section 1 lists the
  known limits: they are not issues to report.

## Coverage (from the log phase)
<the coverage table: each must-have, the payoff, a face, each proof point, with shot ids or "missing"; and what the
user decided about missing items: go ahead, pickups, or a changed brief>

## The lab
    PY = <SKILL>/.venv/bin/python   (Windows: <SKILL>/.venv/Scripts/python.exe)
    M  = "PY" "<SKILL>/media_lab.py" "<LAB>"      footage analysis
    E  = "PY" "<SKILL>/edit_lab.py" "<LAB>"       edit, preview, review, checks
    E0 = "PY" "<SKILL>/edit_lab.py"               lab-free: schema, presets

    M transcript ID [--from W] [--to W]           the transcript with word indices, fillers and pauses
    M shotlist [ID]                               shots with times and quality flags
    M search "TEXT" [--media ID]                  where words or phrases are said
    M sheets --layout zoom --only SHOT,... --out DIR   bigger frames of a few shots
    M peek SHOT --at T,T,... --out DIR            source frames with the crop guide (when `M commands` lists it)
    M status                                      what is analysed, and errors
    E0 schema cutlist                             the cut list format (also edl, outline, preset, checks)
    E0 explain ID...                              what a check id means (when `E0 commands` lists it)
    E items EDL                                   every item of an EDL in one table (when listed)
    E assemble CUTLIST OUT.json                   cut list to EDL (snaps, pads, fades, captions, ducking)
    E review EDL --tier quick|standard|best       preview.mov, report.txt, images, checks in review_<name>/
    E check EDL                                   the gates only
    E diff A.json B.json                          what changed between two versions
    E frames EDL T,T,... --out DIR                frames at timeline seconds

Analysis files: `<LAB>/media/index.json`, `<LAB>/media/analysis/<id>.words.json | .shots.json | .beats.json |
.quality.json | .loudness.json`, `<LAB>/media/sheets/`, `<LAB>/shotlog.json`.

## Known measurements
<paste the notable numbers from `M "LAB" status` and the transcripts: speech length per media, loudness, music
bpm and sections, soft or shaky shots, files with errors>

## Rules for every agent
- Work only inside your own folder under `<LAB>/wf/`. Everything else in the lab is read only.
- Never touch DaVinci Resolve, and never run `dump-script`, `backup-script`, `build-script`, `verify-script`,
  `ingest`, `clean` or `adopt`. The main session builds the approved version.
- Write cut lists in words, shots, seconds and beats, with a `why` on every item; code does the frame math.
- Effects serve a moment of this brief and stay inside the preset's budget (KNOWLEDGE.md section 15); never on text,
  logos or offers, no transition effect in the first second (a hook title's own animation is fine).
- Read `report.txt` before any image, look at most at 6 images per review, cite timeline times, give concrete
  fixes.
- Never guess a misheard word: captions come from the script-aligned transcript, and a word nobody can confirm goes
  to the user as an open action.
- A marker is one line of at most 90 characters, only for an action the user must take, at most 3 per piece.
- Do not read the skill's Python files; when the docs leave a question open, take the safe option and name the
  gap in `doc_gaps`.
