export const meta = {
  name: 'resolve-editor',
  description: 'Offline edit for DaVinci Resolve: log the footage, plan stories, cut competing versions, judge, finish and review',
  whenToUse: 'From the resolve-editor skill once the lab is ingested (media/index.json, transcripts, contact sheets) and BRIEF.md exists. args: {lab, skill, py, tier, size, preset, phase, pick, notes, max_previews, angles, styles, sheets, selects, premium, fx}',
  phases: [
    { title: 'Log', detail: 'loggers describe every shot from the contact sheets and transcripts (small jobs: one logger also writes the selects and the coverage table)' },
    { title: 'Assist', detail: 'best tier, full size: the assistant editor merges the shot log and writes the selects and the coverage table' },
    { title: 'Structure', detail: 'story architects write outlines from different angles' },
    { title: 'Cut', detail: 'cutters cut competing versions offline, preview and self-review them' },
    { title: 'Judge', detail: 'judges score every candidate through different lenses' },
    { title: 'Finish', detail: 'the finisher builds the final from the winner plus grafts and must-fixes' },
    { title: 'Review', detail: 'adversarial reviewers attack the final' },
    { title: 'Fix', detail: 'one bounded fix pass on blocking issues' },
    { title: 'Polish', detail: 'every reviewer passed: one pass that applies only the fixes the reviewers proved' },
  ],
}

// Stage order (the skill's fallback without the Workflow tool runs the same stages with the Agent tool, building
// each prompt with the same text as its prompt function below):
//   standard:     logPrompt x1 -> architectPrompt x1 (also does the assistant's job; the outline of angle A)
//                 -> cutPrompt x2 (parallel: A1 cuts that outline, B1 plans angle B itself; both use the first
//                 style) -> judgePrompt x1 -> finishPrompt -> reviewPrompt x1 -> at most one fixPrompt or
//                 polishPrompt                                                           (at most 8 agents)
//   best, small:  logAssistPrompt x1 (one logger that also writes the selects and the coverage table) -> cutPrompt
//   (size         x2 (parallel, one per angle, each writes its own outline first, both use the first style)
//   "small")      -> judgePrompt x1 (combined; ad presets add the creative director questions) -> finishPrompt
//                 -> reviewPrompt x2 (technical, devil's advocate) -> at most one fixPrompt or polishPrompt;
//                 no second review round                                                 (at most 8 agents)
//   best, full:   logPrompt x3 (parallel) -> assistPrompt -> architectPrompt x2 -> cutPrompt x4 (2 per outline,
//                 each outline's cutters start as soon as its outline exists) -> judgePrompt x3 (parallel; ad
//                 presets: the audience judge is the creative director) -> finishPrompt -> reviewPrompt x2
//                 (x3 for ad presets: plus the creative director) -> fixPrompt -> reviewPrompt x2 (x3)
//                 -> polishPrompt when every verdict passes and a reviewer proved a fix
//                                                              (at most 20 agents, 22 for ad presets)
//   Everywhere: when every verdict of a review round passes and a reviewer returned `proven` fixes, one
//   polishPrompt applies them (instead of a fix pass). When blocking issues remain after the last fix pass and a
//   reviewer proved an EDL, the result carries it as final.proven_alternative.
//   phase "log" stops after Log (and Assist) and returns selects_path and coverage; phase "design" stops after the
//   judges; phase "finish" starts at finishPrompt from args.pick (an EDL path).
//   args.selects (a selects.md from an earlier run) skips Log and Assist and reuses LAB/shotlog.json.
//   Fail safe: when every judge, or every reviewer of a round, returns nothing (the usage limit or a permission
//   block), the run stops with an error that starts with STAGE_FAILED; call the Workflow tool again with the same
//   scriptPath and args plus resumeFromRunId. A single failed architect, cutter or finisher degrades gracefully.
// Every agent works only inside its own folder under LAB/wf and never touches DaVinci Resolve.

const A = args || {}
if (!A.lab || !A.skill || !A.py) {
  throw new Error('workflow args need lab, skill and py (absolute paths)')
}
const LAB = String(A.lab).replace(/[\\/]+$/, '')
const SKILL = String(A.skill).replace(/[\\/]+$/, '')
const PY = String(A.py)
const TIER_IN = String(A.tier || 'best').trim().toLowerCase()
const TIER = ['standard', 'quick'].includes(TIER_IN) ? 'standard' : 'best'
if (TIER_IN === 'quick') log('Tier "quick" runs inline in the main session; this workflow runs it as "standard".')
const SIZE_IN = String(A.size || 'full').trim().toLowerCase()
const SIZE = TIER === 'best' && SIZE_IN === 'small' ? 'small' : 'full'
if (SIZE_IN === 'small' && TIER !== 'best') log('Size "small" applies to the best tier only; standard already runs at most 8 agents.')
const RUN = ['all', 'log', 'design', 'finish'].includes(A.phase) ? A.phase : 'all'
if (RUN === 'finish' && !A.pick) {
  throw new Error('phase "finish" needs args.pick (the EDL path the user chose)')
}
const MAXP = Number.isFinite(A.max_previews) && A.max_previews > 0 ? Math.floor(A.max_previews) : 6
const PRESET = A.preset ? String(A.preset).replace(/[^A-Za-z0-9_-]/g, '_') : null
const AD = !!PRESET && (/^ad_/.test(PRESET) || PRESET === 'product_demo')
const NOTES = A.notes ? String(A.notes) : ''
// the brief's effects answers (KNOWLEDGE.md section 15): premium moves any preset to the premium budget, the
// appetite scales the events per 10 s; left out, the agents read them from the brief
const PREMIUM = A.premium === undefined || A.premium === null || A.premium === '' ? null
  : (A.premium === true || /^(yes|true|1)$/i.test(String(A.premium).trim()))
const FX_IN = String(A.fx || '').trim().toLowerCase()
const APPETITE = ['none', 'light', 'normal', 'bold'].includes(FX_IN) ? FX_IN : null
if (A.fx && !APPETITE) log(`Unknown effects appetite ${JSON.stringify(A.fx)} ignored (known: none, light, normal, bold)`)
const SELECTS_IN = A.selects ? String(A.selects) : null
const SHEETS = Number.isFinite(A.sheets) && A.sheets >= 0 ? Math.floor(A.sheets) : null
const WF = `${LAB}/wf`
const LIMIT = TIER === 'best' && SIZE === 'full' ? (AD ? 22 : 20) : 8
const q = s => `"${String(s).replace(/"/g, '\\"')}"`
const M = `${q(PY)} ${q(SKILL + '/media_lab.py')} ${q(LAB)}`
const E = `${q(PY)} ${q(SKILL + '/edit_lab.py')} ${q(LAB)}`
const E0 = `${q(PY)} ${q(SKILL + '/edit_lab.py')}`
const PRESET_FILE = PRESET
  ? `${SKILL}/presets/${PRESET}.json`
  : `the preset named by the "preset" key of ${LAB}/project.json (file ${SKILL}/presets/<preset>.json)`
const dirOf = p => String(p).replace(/[\\/][^\\/]*$/, '')

let agentsUsed = 0
const docGaps = []
async function call(prompt, opts) {
  agentsUsed++
  const r = await agent(prompt, opts)
  if (r && Array.isArray(r.doc_gaps)) {
    for (const g of r.doc_gaps) {
      const s = String(g || '').trim()
      if (s && !docGaps.includes(s)) docGaps.push(s)
    }
  }
  return r
}

function stageFailed(stage, who) {
  return new Error(`STAGE_FAILED ${stage}: nothing came back from ${who} (the usage limit or a permission block); nothing after it ran. Resume after the limit resets: call the Workflow tool again with the same scriptPath and args plus resumeFromRunId (the runId of this run); finished agents come back from the cache.`)
}
const whoOf = (n, one, many) => n === 1 ? `the ${one}` : `any of the ${n} ${many}`

// ---------------------------------------------------------------- story angles and cut styles
const ANGLES = {
  hook_first: 'HOOK FIRST. Open on the strongest moment (the payoff, the most surprising line or image, or the result shown first) inside the first 3 s, then give only the context the viewer needs, then deliver the value in order of strength, and close by paying off the opening promise. Every beat must earn the next few seconds.',
  emotional_arc: 'EMOTIONAL ARC. Build a feeling: a grounded start, rising stakes or intimacy, one peak (the most emotional line or moment, held on the face), then a release. Setups and aftermath may be covered with B-roll; the peak never is. A face only when its performance carries the feeling, never a person talking without their sound or looking at someone off screen.',
  chronological: 'CHRONOLOGICAL. Tell it in the order it happened (a process, a day, a journey), compressed: establish, then only the steps that matter, then the result. Jump time on cuts rather than with narration and drop steps that repeat.',
  problem_solution: 'PROBLEM TO SOLUTION. A short problem or before state, the solution in action as early as possible, proof or result, then a clear call to action or takeaway. No long setup and no brand wind-up.',
  music_led: 'MUSIC LED. The song is the outline: map the beats of the piece to its sections (intro, build, chorus or drop, outro), put picture section changes on musical section boundaries, the strongest image on the biggest hit, and end on the music\'s own resolution (a natural end or a phrase-end fade, never a hard stop mid-phrase).',
}
const AD_ARC = ' In an ad the peak is the offer, and the release carries the call to action.'
const STYLES = {
  tight: 'TIGHT. Energetic: pauses at the low end of the preset range, cuts on action, a visual change every 2 to 4 s (a punch-in of 15 % or more on alternate talking segments, a keyed punch on an emphasis word, or B-roll), no dead air, word-chunk captions.',
  breathing: 'BREATHING. Let moments land: pauses toward the high end of the preset range, longer holds on faces after emotional lines, B-roll holds of 2 to 4 s (6 s or more when the shot carries emotion), fewer but more deliberate cuts.',
  montage: 'MONTAGE. Cut the picture to the beat grid in 2 or 4 bar blocks with the cut list\'s "beats" field (it lands each cut 1 frame before the beat); density follows the music sections (more cuts in a chorus or drop, fewer in verses and bridges); let motion peaks land on beats; keep one through-line (a subject, a colour, a direction of motion).',
  story_first: 'STORY FIRST. A paper edit: pick the bites that tell the story best, put them in the clearest order, then cover jump cuts and setups with B-roll only where the picture needs it. Clarity before pace.',
}
// The same defaults as the "workflow" block of each shipped preset (the main session normally passes them).
const PRESET_WORKFLOW = {
  reels_talking_head: { angles: ['hook_first', 'emotional_arc'], styles: ['tight', 'breathing'] },
  reels_montage_music: { angles: ['music_led', 'hook_first'], styles: ['tight', 'montage'] },
  shorts_highlight: { angles: ['hook_first', 'problem_solution'], styles: ['tight', 'story_first'] },
  youtube_long: { angles: ['hook_first', 'chronological'], styles: ['story_first', 'breathing'] },
  interview_doc: { angles: ['emotional_arc', 'chronological'], styles: ['story_first', 'breathing'] },
  podcast_clip: { angles: ['hook_first', 'problem_solution'], styles: ['tight', 'story_first'] },
  ad_15: { angles: ['problem_solution', 'hook_first'], styles: ['tight', 'montage'] },
  ad_30: { angles: ['problem_solution', 'hook_first'], styles: ['tight', 'story_first'] },
  product_demo: { angles: ['problem_solution', 'chronological'], styles: ['story_first', 'tight'] },
  wedding_highlight: { angles: ['emotional_arc', 'chronological'], styles: ['breathing', 'montage'] },
  music_video: { angles: ['music_led', 'emotional_arc'], styles: ['montage', 'tight'] },
  travel_montage: { angles: ['music_led', 'chronological'], styles: ['montage', 'breathing'] },
}
const FALLBACK = { angles: ['hook_first', 'emotional_arc'], styles: ['tight', 'breathing'] }
// The effects genre of each shipped preset ("fx"."genre") and the genre budgets of fx_catalog.json (KNOWLEDGE.md
// section 15): events per 10 s, stylized families, total zoom cap, kinds the genre avoids.
const PRESET_GENRE = {
  reels_talking_head: 'creator_reel', reels_montage_music: 'music_montage', shorts_highlight: 'talking_head',
  youtube_long: 'talking_head', interview_doc: 'premium_brand', podcast_clip: 'talking_head', ad_15: 'performance_ad',
  ad_30: 'performance_ad', product_demo: 'performance_ad', wedding_highlight: 'cinematic_montage',
  music_video: 'music_montage', travel_montage: 'cinematic_montage',
}
const GENRES = {
  premium_brand: { per10s: 1.5, families: 1, zoom: 1.1, avoid: ['glitch', 'rgb_split', 'emoji', 'flash', 'bump', 'spin', 'progress_bar', 'shake'] },
  performance_ad: { per10s: 4, families: 3, zoom: 1.35, avoid: ['spin'] },
  creator_reel: { per10s: 5, families: 3, zoom: 1.35, avoid: [] },
  talking_head: { per10s: 3, families: 2, zoom: 1.3, avoid: ['glitch', 'rgb_split', 'flash', 'light_leak', 'film_burn', 'spin'] },
  music_montage: { per10s: 8, families: 3, zoom: 1.5, avoid: [] },
  cinematic_montage: { per10s: 2.5, families: 2, zoom: 1.15, avoid: ['glitch', 'rgb_split', 'emoji', 'bump', 'progress_bar', 'spin'] },
}
const APPETITE_SCALE = { none: 0, light: 0.5, normal: 1, bold: 1.25 }

function pickFrom(want, table, defaults, limit, kind) {
  const out = []
  for (const d of (Array.isArray(want) && want.length ? want : defaults)) {
    if (typeof d === 'string' && table[d]) {
      if (!out.find(x => x.id === d)) out.push({ id: d, text: table[d] })
    } else if (d && typeof d === 'object' && d.id && d.text) {
      out.push({ id: String(d.id).replace(/[^A-Za-z0-9_-]/g, '_'), text: String(d.text) })
    } else {
      log(`Unknown ${kind} ${JSON.stringify(d)} skipped (known: ${Object.keys(table).join(', ')})`)
    }
  }
  if (out.length > limit) log(`Using the first ${limit} ${kind}s of ${out.length}: ${out.slice(0, limit).map(x => x.id).join(', ')}; dropped ${out.slice(limit).map(x => x.id).join(', ')}`)
  const chosen = out.slice(0, limit)
  return chosen.length ? chosen : defaults.slice(0, limit).map(id => ({ id, text: table[id] }))
}
const DEFAULTS = (PRESET && PRESET_WORKFLOW[PRESET]) || FALLBACK

// ---------------------------------------------------------------- shared prompt parts
const LAB_MAP = `Lab files (read only for you unless they are in your folder):
- ${LAB}/media/index.json: every media file with its id, kind (av, video, audio, image), role (voiceover for narration), fps, frames, duration and a summary.
- ${LAB}/media/analysis/<id>.words.json (transcript: words with indices, times snapped to the audio, tags filler, repeat, flag, low_conf, and script_fix, extra or joined once the transcript was aligned to the voice-over script, whose "script" block says so: extra words are not in the script and assemble drops them, a joined word is a later part of the word before it (one spoken word whisper wrote as two, "joined_to" names its first part; M transcript marks it +): a word range or a drop always takes the whole word, never end a range between its parts; sentences; pauses), <id>.shots.json (shots <id>.sNN with media frame ranges), <id>.beats.json (music: bpm, beats, downbeats, sections, flags), <id>.quality.json (soft, shaky, dark flags per shot), <id>.loudness.json.
- ${LAB}/media/sheets/sheets.json and log_NN.jpg: contact sheets, 5 frames per shot with a label bar.
- ${LAB}/shotlog.json: the merged shot log (descriptions, usable ranges, scores) once the logger or assistant has made it.
- ${LAB}/project.json: timeline fps, size and start timecode. ${LAB}/brand.json (when the brand gave a kit): font, colours, logo and wordmark media ids, handle, URL and call-to-action button; the preset's looks use it.`

function allowed(kind) {
  const read = '`M transcript ID [--from W] [--to W]`, `M shotlist [ID]`, `M search "TEXT" [--media ID]`, `M status`, `E0 schema edl|cutlist|outline|preset|checks`, `E0 presets`, and when `E0 commands` lists them `E0 explain ID...` (what a check id means) and `E items EDL` (every item in one table)'
  const zoom = `\`M sheets --layout zoom --only SHOT,SHOT --out ${'<your folder>'}\``
  const cut = '`E assemble CUTLIST OUT.json`, `E validate EDL`, `E diff A.json B.json`, `E preview EDL`, `E review EDL --tier quick|standard|best`, `E check EDL`, `E frames EDL T,T,... --out <your folder>`'
  if (kind === 'log') return `${read}, ${zoom}, \`M shotlog-check FILE\``
  if (kind === 'assist') return `${read}, ${zoom}, \`M shotlog-check FILE\`, \`M shotlog-merge IN...\``
  if (kind === 'arch') return `${read}, ${zoom}`
  if (kind === 'arch_assist') return `${read}, ${zoom}, \`M shotlog-check FILE\`, \`M shotlog-merge IN...\``
  if (kind === 'judge') return `${read}, \`E check EDL --out <your folder>\` (on another agent's EDL always with --out), \`E diff A.json B.json\`, \`E frames EDL T,T,... --out <your folder>\``
  if (kind === 'review') return `${read}, ${cut} (on an EDL you did not make, run \`E check\` and \`E review\` with \`--out <your folder>\`)`
  return `${read}, ${cut}`
}

const TEXT_RULES = `Words on screen: never guess a misheard word. Captions come from the script-aligned transcript. A caption word the check calls \`caption_unverified\` is fixed with \`captions.text_fix\` only from the script, the brief or its glossary; otherwise it goes into \`open_actions\`. Titles and supers carry only words the brief, the script or the transcript give.
Markers: a marker is a one-line note of at most 90 characters for an action only the user can take, at most 3 per piece; reasoning goes in the item's \`why\`; a fix the lab cannot build goes into \`open_actions\` (or your notes when your result has no such field), not into a marker.`

// ---------------------------------------------------------------- effects (KNOWLEDGE.md section 15)
function fxBudget() {
  const base = PRESET ? PRESET_GENRE[PRESET] : null
  // a premium brand calms the budget: music montages and music videos keep cutting on the music (cinematic_montage)
  const genre = PREMIUM === true ? (base === 'music_montage' ? 'cinematic_montage' : 'premium_brand') : base
  const g = genre ? GENRES[genre] : null
  const scale = APPETITE ? APPETITE_SCALE[APPETITE] : null
  if (APPETITE === 'none') {
    return 'Effects budget: none. The brief asked for no effects: straight cuts (and a dissolve where the story needs one), plain captions and titles, no keyed effects, no retimes beyond plain slow motion, no sound effects.'
  }
  let s = 'Effects budget'
  if (g) {
    const per = scale === null ? g.per10s : Math.round(g.per10s * scale * 100) / 100
    s += ` for this piece: genre ${genre}${PREMIUM === true && base !== 'premium_brand' ? ` (the brief says the brand is premium; the preset alone would be ${base})` : ''}: at most ${per} effect events per 10 s${scale === null ? '' : ` (${g.per10s} x ${scale} for the appetite "${APPETITE}")`}, at most ${g.families} stylized ${g.families === 1 ? 'family' : 'families'}, a total zoom (static times keyed) of at most ${g.zoom}; the genre avoids ${g.avoid.length ? g.avoid.join(', ') : 'nothing in particular (but never an effect on every beat)'}.`
  } else {
    s += ': the genre in the preset\'s "fx" block, with the numbers of the genre table in KNOWLEDGE.md section 15 (events per 10 s, stylized families, total zoom cap, kinds the genre avoids).'
  }
  if (PREMIUM === true || genre === 'premium_brand') s += ' Premium text: "clean_box" or "keyword" captions and "fade" or "slide_hook" titles, whatever the preset\'s defaults say.'
  if (PREMIUM === null && genre !== 'premium_brand') s += ' If the brief says the brand is premium or luxury, use the cinematic_montage budget for a music montage or music video (cuts on the music, no bounce, glitch, RGB split or emoji, total zoom 1.15) and the premium_brand budget for every other piece (1.5 events per 10 s, 1 stylized family, total zoom 1.10, no bounce, no flash, shake or glitch, "clean_box" or "keyword" captions, "fade" or "slide_hook" titles).'
  if (scale === null) s += ' Scale the events per 10 s by the brief\'s effects appetite: none 0, light 0.5, normal 1, bold 1.25.'
  s += ` The kinds, transitions and animations this preset allows are its "fx"."allowed", "transitions"."allowed" and "anims"; its defaults are "captions"."anim" and "titles".<style>."anim" (${PRESET_FILE}).`
  return s
}
const FX_BUDGET = fxBudget()

const FX_FIELDS = `Effects fields of the cut list (KNOWLEDGE.md section 15; \`E0 schema cutlist\` prints them when this version has them; assemble does every frame of the maths and bakes them into per-frame keys):
- "fx" on a spine segment or an overlay: [{"kind": "punch" | "bump" | "snap" | "push" | "shake" | "flash" | "rgb_split" | "glitch" | "light_leak", "at": {"word": i} (word i of the segment's media) | {"beat": k} (programme beat k of the gridded music, from 0) | {"beat_rel": n} (beat n inside the segment, 0 the first) | {"s": t} | "cut_in" | "cut_out", "point": [x, y] (the zoom point, fractions of the frame from the top left), the kind's own numbers ("zoom", "peak", "from", "to", "ms", "ease", "amp" ...; left out, the catalogue's defaults), "why"}]. A push spans its segment unless it has "at" and "dur_s".
- "retime" on a segment: {"kind": "ramp", "profile": [{"speed": 3.0, "dur_s": 0.5}, {"speed": 0.5}], "ease_ms": 300, "land": {"beat": 16}} (the last speed already holds on the "land" anchor) or {"kind": "freeze", "at": {"s": 1.0}, "hold_s": 1.0}. A retimed segment has no "speed", plays no sound and shows whole source frames, so a slow part below timeline fps / media fps repeats frames: ramp the 50 or 60 fps footage. "retime_process": "speed_warp" (default "nearest") is only for a segment at a constant "speed" under 1 (smooth slow motion of 25 or 30 fps footage); assemble refuses it with a ramp or freeze.
- "transitions": "type" is a catalogue key (cross_dissolve, dip_to_black, blur_dissolve, brightness_flash_fx, and the P1 keys of KNOWLEDGE.md section 8 the preset allows) or the generic "whip" or "zoom" (assemble picks the custom Fusion transition when the handles allow, else a clip-comp pair, and names the route in fx_routes); extra keys "direction" (the way the PICTURE travels on screen: a camera panning right moves the picture left), "peak", "point", "mix", "motion_blur", "audio" ("plus3", "zero" or "none") and "look_is_brief" (P2 types only, when the brief asks for exactly that look). One transition per cut.
- "tags": ["logo"] (or "product", "offer") on a segment or overlay whose picture shows the logo, the product name or the offer (shots of brand.json's logo and wordmark media count without a tag): no shake, flash, rgb_split or glitch on them (fx_text_shake STOP); put the final hit on the shot before the logo card.
- "anim" on a title and in "captions" (an id of the preset's "anims", or "none"; left out, the preset's default), and "captions": {"keywords": ["<media>:<word index>"]} for the keyword style (at most one per cue, on about 30 % of cues).
- "sfx": [{"id", "media" (a sound effect the user gave, added with \`M add --sfx\`), "on": {"transition": "<the segment it follows>"} | {"seg": "<id>", "fx": <its place in that fx list, from 1: the effect <id>.fx1 is 1>} | {"title": "<id>"} | {"at_s": t}, "gain_db"}]: assemble lands each file's loudest moment on its event (a punch's event is its word, a bump's its peak, a transition's its cut). No files from the user, no sound effects.`

const FX_RULES = `Effects: an effect serves one moment of the brief (the emphasis word, the drop, the reveal, the offer, the payoff) and earns its place only if that moment gets weaker without it. Never a picture effect on a title, and no shake, flash, rgb_split or glitch on a shot that shows the logo, the product name or the offer (tag it); never a transition effect in the first second (fx_hook; a hook title's own animation is part of the look, and a default one starts whole on the first frame, the cover); never the same transition on every cut; captions never move after their entrance; premium and cinematic pieces never bounce text. ${FX_BUDGET} After every assemble read the EDL's "fx" records and the report's fx_routes, fx_clipped and fx_refused (a refused entry is disabled or P2 in the catalogue: use another one or a straight cut), and fix every effects STOP (fx_refused, fx_flash_rule, fx_text_shake, fx_edges, anim_read_floor, fx_freeze, transition_handles, transition_through_black, transition_broken, smooth_cut_misuse, retime_range, retime_speed).`

const FX_LENS = `Effects (KNOWLEDGE.md sections 13 and 15): read the effects checks in checks.json (the fx_, transition_ and sfx_ ids, anim_read_floor, caption_keywords) and the spans report.txt and the overview mark approximate (there the preview only stands in for Resolve's look; judge timing and placement, not the exact picture). Score effects inside rhythm_pacing, audiovisual, continuity and composition_graphics: an effect earns its place when it serves a moment of the brief inside the budget. Deduct for every cheapener: effects on more than 30 % of the cuts, the same transition on 4 cuts in a row, more stylized families than the genre allows, a kind the genre avoids, bouncing text in a premium piece, a transition in the first second, a bump or ramp landing off the beat, text that moves while it must be read, an effect on a logo or an offer. ${FX_BUDGET}`

function pre(role, dir, sections, kind, budget) {
  return `You are ${role} on a professional video editing team working in an offline lab. The main session builds the approved edit in DaVinci Resolve later, from the files this team makes; nothing you do touches Resolve.

Lab commands: below, M, E and E0 stand for these exact prefixes (run them with Bash; keep the quotes, the paths may contain spaces):
  M  = ${M}
  E  = ${E}
  E0 = ${E0}
For example: E0 schema cutlist

Read first:
1. The brief ${LAB}/BRIEF.md (what the piece is, for whom, platform, length, must-haves, the voice-over script, the next step for the viewer).
2. ${SKILL}/KNOWLEDGE.md, sections ${sections}. Section 1 lists the known limits of this version: do not report them as issues (the hand-over covers them once). Section 14 explains every report flag and check id.
3. The preset ${PRESET_FILE}: length, hook, pacing, speech pads and pauses, captions, titles and looks, music and loudness targets.
${NOTES ? `\nNotes from the user: ${NOTES}\n` : ''}
${LAB_MAP}

Commands you may run: ${allowed(kind)}.
${budget ? `Budget: ${budget}\n` : ''}
Rules: work only inside your own folder ${dir} (create it). You never call DaVinci Resolve tools, in any form. Never run dump-script, backup-script, build-script, verify-script, grab-script, deliver-script, sandbox-script, ingest, clean, adopt, or the transcript editors script, fix-word and edge. Never edit files outside your folder: the media, the analysis files, ${LAB}/edits and the other agents' folders are read only for you. Do not read the skill's Python files (media_lab.py, edit_lab.py): the command outputs, \`E0 schema\` and KNOWLEDGE.md are the interface. When the docs leave a question open, take the safe option and name the gap in your result's \`doc_gaps\` (one line each). Code does the frame math: write cut lists in words, shots, seconds and beats, never frame numbers you computed by hand. Look at images with the Read tool, at most 6 per review, and read report.txt before any image; a sheet the lab makes (an overview, a cut sheet, a contact sheet, one \`E frames\` image) counts as one image, while a montage you tile yourself counts one image per frame in it. Cite timeline times (seconds or timecode) in every finding, and give concrete fixes (move a cut by N frames, swap two shots, drop words [a, b], extend an item by N frames, add a cutaway at t).
${TEXT_RULES}

`
}

// ---------------------------------------------------------------- schemas
const S = (props, required) => ({ type: 'object', properties: props, required })
const STR = { type: 'string' }
const STRS = { type: 'array', items: { type: 'string' } }
const NUM = { type: 'number' }
const RESULT = { type: 'string', enum: ['OK', 'WARN', 'STOP'] }
const GAPS = { doc_gaps: STRS }
const COVERAGE = {
  type: 'array',
  items: S({ need: STR, shots: STRS, status: { type: 'string', enum: ['ok', 'weak', 'missing'] } }, ['need', 'status']),
}

const LOG_SCHEMA = S({ shotlog_path: STR, shots: NUM, notes: STR, ...GAPS }, ['shotlog_path', 'shots', 'notes'])
const LOG_ASSIST_SCHEMA = S({ shotlog_path: STR, shots: NUM, notes: STR, selects_path: STR, coverage: COVERAGE, ...GAPS },
  ['shotlog_path', 'shots', 'notes', 'selects_path', 'coverage'])
const ASSIST_SCHEMA = S({ shotlog_path: STR, selects_path: STR, summary: STR, coverage: COVERAGE, ...GAPS },
  ['shotlog_path', 'selects_path', 'summary', 'coverage'])
const OUTLINE_SCHEMA = S({ outline_path: STR, angle: STR, logline: STR, risks: STRS, selects_path: STR, ...GAPS },
  ['outline_path', 'angle', 'logline', 'risks'])
const CUT_SCHEMA = S({ id: STR, edl_path: STR, review_dir: STR, result: RESULT, rationale: STR, self_critique: STR, previews: NUM, outline_path: STR, ...GAPS },
  ['id', 'edl_path', 'review_dir', 'result', 'rationale', 'self_critique'])
const JUDGE_SCHEMA = S({
  lens: STR,
  scores: {
    type: 'array', items: S({ candidate: STR, score: NUM, per_dimension: { type: 'object' }, strengths: STR, weaknesses: STR },
      ['candidate', 'score', 'strengths', 'weaknesses']),
  },
  winner: STR, grafts: STRS, must_fix: STRS, ...GAPS,
}, ['lens', 'scores', 'winner', 'must_fix'])
const FINAL_SCHEMA = S({
  edl_path: STR, review_dir: STR, result: RESULT, summary: STR, changes: STR, previews: NUM,
  user_summary: STRS, open_actions: STRS, ...GAPS,
}, ['edl_path', 'review_dir', 'result', 'summary', 'user_summary', 'open_actions'])
const PROVEN = { type: 'array', items: S({ what: STR, cutlist_path: STR, edl_path: STR }, ['what', 'edl_path']) }
const REVIEW_SCHEMA = S({
  lens: STR, verdict: { type: 'string', enum: ['pass', 'fix'] },
  blocking: { type: 'array', items: S({ issue: STR, at_s: NUM, fix: STR }, ['issue', 'fix']) },
  minor: STRS, proven: PROVEN, ...GAPS,
}, ['lens', 'verdict', 'blocking'])

// ---------------------------------------------------------------- prompts
const SHOTLOG_SPEC = `Shot log format (schema "resolve-editor/shotlog@1"): {"schema": "resolve-editor/shotlog@1", "by": "<your agent name>", "shots": [{"shot": "<shot id from the sheet label>", "media": "<media id>", "desc": "close-up, hands prepare the work surface", "size": "CU", "move": "static", "subjects": ["person A"], "faces": [{"who": "person A", "x": 0.42, "y": 0.31}], "action": [{"what": "hand enters from right", "at": 0.35}], "usable": [[0.0, 0.7]], "problems": ["obstruction from 0.7", "client risk: a child stands beside the working station from 0.4"], "quality": "good", "mood": ["focused"], "keywords": ["hands", "detail"], "story": "what the shot can do in a story", "score": 7}]}
Required per shot: shot, desc, usable, score (0 to 10). size is one of ECU CU MCU MS MWS WS EWS insert screen other; move is one of static pan_l pan_r tilt_u tilt_d push pull handheld arc slide track zoom other. Fractions: usable and action.at are 0 to 1 of the shot's length (the selects and cut lists quote seconds: the shot's start plus fraction x its length); faces.x is 0 to 1 of the frame width (0 the left edge) and faces.y 0 to 1 of the frame height at the eyes (0 the top). Client risks go into "problems" with the words "client risk:" and the fraction where they show: a child or a bystander next to a working station, an unsafe practice (no gloves where the work needs them, a hazard in reach), mess or an emptied or dirty product, a third-party brand label (another company's bottle or a machine maker's logo), decor with no story (a plant, a shelf, a bare wall). Name people by role ("host", "guest", "person A"), not by guesses at real names, unless the brief names them.`

function logHow(dir) {
  return `For every shot on your sheets, look at the sheet with the Read tool and log it: what is in frame, shot size, camera move, action and when it happens (note how much happens in the first 10 frames: hooks are ranked by it), which part is usable (soft focus, obstructions, shake, a subject leaving frame), faces and where they sit (faces x = the centre of the face as a fraction of the frame width, faces y = the height of the eyes as a fraction of the frame height from the top; for a vertical edit the sheets mark the centre crop with dashed cyan lines and a ruler of tenths along the top of each frame, so read x off the ruler and note in "problems" when the subject sits outside the crop), whether a person talks without their own sound or looks at someone off screen (lip flap and off-screen eyelines are not emotion), what a client would not want shown (log each as "client risk: ..." in "problems" with where it shows: a child or a bystander at a working station, unsafe practice, mess, a third-party brand label, decor with no story; such a shot never illustrates a safety, hygiene or quality line), mood, keywords, and what the shot could do in a story. For media with speech, read its transcript (\`M transcript ID\`) and note in "story" what is said over the shot, with word index ranges like [12, 31]. When a shot is ambiguous at sheet size, make a zoom sheet of at most 6 shots into your folder (\`M sheets --layout zoom --only SHOT,SHOT --out ${dir}\`), at most 2 zoom sheets in total. Be exact about usable ranges: editors trust them.

${SHOTLOG_SPEC}`
}

const SELECTS_HOW = `1. The piece in one paragraph, as the brief asks for it.
2. Best moments, ranked. Speech: media id and word range [a, b] with the exact words, the time, and why it is strong (a hook line, an emotional peak, a clean explanation, a payoff). Pictures: shot ids with usable ranges and what they show; rank hook shots by the action in their first 10 frames.
3. Speakers: who says what, and how to tell them apart. A voice-over (media role voiceover, or a script in the brief) is narration: its words go on the spine, pictures on overlays (KNOWLEDGE.md section 6, Voice-over pieces).
4. Problems: bad audio, soft or shaky shots, false starts and retakes (say which take is best and why), fillers, words tagged low_conf that the script does not cover, things that must not be used.
5. Music: bpm, sections with start times, downbeats at section starts, the natural ending. Say when \`M status\` flags the media tempo_uncertain, bar1_uncertain, bpm_name_mismatch or loop_bars_off: then the cutters aim at beats, not downbeats, and the main session asks the user to check bar 1 with the click file. Also pass on bpm_from_name (the file name's tempo was used), grid_moved and bar_line_off_grid with the note \`M status\` prints under the media.
6. Coverage: a table of what the brief needs against what the footage has: each must-have, the payoff, a face, and each proof point the voice-over or the brief claims, with the shot ids that show it, or "weak" (only a soft, short or distant shot shows it), or "missing". A proof line needs a picture that shows it.
7. Shot budget: the number of distinct setups (one camera position and framing; the same framing within 10 s reads as the same shot) and the most shots the piece can use without a repeat (about 1.3 x the setups; past that, holds, slow motion, a size change of 30 % or a text card, never a repeat).
8. For a piece with a voice-over or a script: the line-by-shot matrix (each spoken line with its media id and word range, and the shots that could show it).`

function logPrompt(k, n) {
  const dir = `${WF}/log_${k}`
  return pre(`logger ${k} of ${n}`, dir, '1, 3 and 6', 'log', null) + `ROLE: LOGGER ${k} OF ${n}. Open ${LAB}/media/sheets/sheets.json. Your share is the sheets at positions ${k}, ${k + n}, ${k + 2 * n}, ... of its "sheets" list (counting from 1). If your share is empty, write an empty shot log and return 0 shots.

${logHow(dir)}

Write ${dir}/shotlog.json and run \`M shotlog-check ${dir}/shotlog.json\` until its first line is RESULT: OK or RESULT: WARN. Return the path, the number of shots you logged, and notes the editors must know (standout moments with their shot ids, problems, missing coverage).`
}

function logAssistPrompt() {
  const dir = `${WF}/log_1`
  const sheets = SHEETS === 0
    ? `There are no contact sheets: log what you can from \`M shotlist\` and the transcripts, and write in the selects which shots nobody has looked at.`
    : `Open ${LAB}/media/sheets/sheets.json: every sheet is yours.`
  return pre('the logger and assistant editor', dir, '1, 2, 3, 4, 5, 6 and 7', 'assist', null) + `ROLE: LOGGER AND ASSISTANT EDITOR. This is a small job with one logger, so you log the footage and write the selects in one pass.

Step 1, the shot log. ${sheets}
${logHow(dir)}
Write ${dir}/shotlog.json and run \`M shotlog-check ${dir}/shotlog.json\` until its first line is RESULT: OK or RESULT: WARN. Then \`M shotlog-merge ${q(dir + '/shotlog.json')}\` (it writes ${LAB}/shotlog.json) and \`M shotlog-check ${LAB}/shotlog.json\`.

Step 2, the selects. Read every transcript (\`M transcript ID\`) and the beats file of every music media (media/analysis/<id>.beats.json). Write ${dir}/selects.md for the cutters:
${SELECTS_HOW}

Return the shot log path (${LAB}/shotlog.json), the number of shots, notes the editors must know, the selects path and the coverage table as "coverage": [{"need", "shots", "status": "ok" | "weak" | "missing"}].`
}

function assistPrompt(logs) {
  const dir = `${WF}/assist`
  const inputs = logs.length
    ? `Logger results:\n${JSON.stringify(logs, null, 1)}\n\nMerge them with \`M shotlog-merge ${logs.map(l => q(l.shotlog_path)).join(' ')}\` (it writes ${LAB}/shotlog.json) and check the result with \`M shotlog-check ${LAB}/shotlog.json\`.`
    : `No logger results came back (or there are no contact sheets). Work from the transcripts, \`M shotlist\` and ${LAB}/shotlog.json if it exists; write in the selects which shots nobody has looked at.`
  return pre('the assistant editor', dir, '1, 2, 4, 5, 6 and 7', 'assist', null) + `ROLE: ASSISTANT EDITOR. ${inputs}

Then read every transcript (\`M transcript ID\`), the shot log, and the beats file of every music media (media/analysis/<id>.beats.json). Write ${dir}/selects.md for the story architects and cutters:
${SELECTS_HOW}
Return the shot log path (${LAB}/shotlog.json, or the best path you have), the selects path, a short summary and the coverage table as "coverage": [{"need", "shots", "status": "ok" | "weak" | "missing"}].`
}

const COPY_STEP = `Copy step (before any cutting): write the words on screen into the outline's "copy" field: three hook title options (at most 6 words each, tension or a specific benefit, the same message as the first spoken line, never "Learn X at Brand"), the supers (the spoken words or their keyword over a voice line; over a text-led stretch, new proof points from the brief, never the voice's words again), and for a piece with an offer or a next step (every ad) the offer line (shown once), the call to action with its route from the brief (link in bio, URL, phone, button) and the end-card copy (the brand name and the call to action, locked up together on the card). Order the supers proof, then offer, then call to action. Give every title its role (hook, offer, proof, brand, cta, name, other; KNOWLEDGE.md section 10). Never invent a price, a date, a claim or a name the brief does not give. When the voice-over's speech time is under about 60 % of the length, or its words are under the playbook's count (KNOWLEDGE.md section 11), name the fit option the brief chose (a shorter piece, a text-led stretch with supers, or a longer voice-over) in the outline's risks; when the brief is silent the default is to spread the voice lines across the whole length with music between them (gaps of 0.3 to 0.6 s, one lifted musical breath of about 1.2 s per hit). That spread reaches at most the speech time + 0.6 s per gap + 1.2 s per hit + the end card; when it falls short of the length, write supers (new proof points) for the longer gaps (a super covering 80 % of a gap keeps vo_gap quiet; message_gap allows 1.5 s) and say so in the outline's risks. A text-led stretch shows fresh setups (no media seen in the first half: recycled_half) and new proof points; no stretch over 1.5 s without voice or text.`

const OUTLINE_SPEC = `{"schema": "resolve-editor/outline@1", "id": "<letter>", "angle": "<angle id>", "logline": "...", "target_s": <seconds>, "beats": [{"id": "hook", "purpose": "promise the payoff", "target_s": [0, 3], "material": [{"media": "<id>", "words": [a, b]}, {"shot": "<shot id>"}], "music": "intro", "notes": "..."}], "music": {"media": "<id>", "plan": "..."}, "copy": {"hook_options": ["..."], "titles": [{"text": "...", "role": "hook"}], "offer": "...", "cta": "...", "end_card": "..."}, "risks": ["..."]}`

function architectPrompt(angle, assist, logs) {
  const dir = `${WF}/arch_${angle.id}`
  let inputs
  let kind = 'arch'
  if (assist && assist.selects_path) {
    inputs = `Selects: ${assist.selects_path} (summary: ${assist.summary || 'none'}). Shot log: ${assist.shotlog_path || LAB + '/shotlog.json'}.`
  } else {
    kind = 'arch_assist'
    const merge = logs.length
      ? `First merge the logger results with \`M shotlog-merge ${logs.map(l => q(l.shotlog_path)).join(' ')}\` (it writes ${LAB}/shotlog.json) and check it with \`M shotlog-check ${LAB}/shotlog.json\`. Logger notes:\n${JSON.stringify(logs, null, 1)}\n`
      : `No shot log came back from the loggers; work from the transcripts and \`M shotlist\`, and use ${LAB}/shotlog.json if it exists.\n`
    inputs = `You also do the assistant editor's job. ${merge}Then read every transcript (\`M transcript ID\`) and the beats of any music media, and write ${dir}/selects.md for every cutter (one of them plans another angle from it):
${SELECTS_HOW}
Return its path as selects_path.`
  }
  return pre('a story architect', dir, '1, 2, 4, 5, 10 and 11', kind, null) + `ROLE: STORY ARCHITECT. Angle: ${angle.text}

${inputs}

Write ${dir}/outline.json (schema "resolve-editor/outline@1"; \`E0 schema outline\` prints an annotated example): ${OUTLINE_SPEC.replace('<angle id>', angle.id)}.
Every beat cites exact material (word ranges from the transcripts, shot ids from the shot log). Check that the spoken material fits the preset's length: add up the times of the word ranges from the transcript and leave room for pauses and B-roll holds. Keep to the shot budget in the selects. Follow the brief's must-haves and must-avoids.
${COPY_STEP}
Name the risks honestly (a weak middle, a missing shot, a line that needs context, a proof line without a picture). Return the outline path, the angle, a one-line logline and the risks.`
}

const CUTLIST_SPEC = `Cut list essentials (schema "resolve-editor/cutlist@1"; \`E0 schema cutlist\` prints the full annotated example):
- "spine": segments placed back to back on V1 from the start. Exactly one source per segment: {"media": ID, "words": [first, last], "drop": [indices]} (inclusive word indices of that media's transcript; drop removes fillers or repeats inside the range), or {"shot": SHOT_ID, "trim": [a, b]} (fractions of the shot; default its first usable range), or {"media": ID, "in_s": S, "dur_s": D}, or {"media": ID, "in_f": F, "out_f": F}. Optional: "speed", "dur_s" or "beats" (timeline length; "beats" needs a music item with "grid": true), "audio" ("dialogue", "nat" or "none"; "words" segments are always dialogue; left out, a clip without speech plays as nat sound at -12 dB in dialogue presets and a slowed clip plays silent), "gain_db", "zoom" (static punch-in, 1.15 or more reads as intentional), "frame_x" (the subject's centre as a fraction of the source width, 0 at the left edge to 1 at the right: assemble pans so the subject sits in the middle of the frame, clamped at the image edge; put it on every segment and overlay of a vertical piece cut from wider footage whose subject is off centre, from the shot log's faces x or the contact sheet ruler; KNOWLEDGE section 6), "pan_px", "tilt_px" (the pixels the image moves on the timeline; frame_x wins over pan_px), "pad_ms": [before, after] and "pause_keep_ms": [min, max] (word segments only: override the preset, for example a longer tail so the next cut lands on the beat), "caption_y" (a fraction of the height: moves the captions over this segment off the key action), "hold_prev_s" (a long J cut: the previous segment's picture continues over the first x s of this one while its voice starts) and "early_s" (a long L cut: this segment's picture starts x s early over the end of the previous one while the old voice runs on), "tags" ("callback" on a deliberate repeat), and "why" (required on every item). When \`E0 schema cutlist\` shows it, a word segment also takes "edge_ms": {"in": -80, "out": 40} (nudge a word edge, clamped to the measured pause).
- Voice-over pieces: the narration's word ranges are the spine (audio only, or over picture-only V1 segments) and the pictures are overlays; KNOWLEDGE.md section 6 has a worked example.
- "overlays": video-only cutaways on V2 (their camera sound is never placed): {"shot": ..., "trim": [...], "at": {"seg": "s03", "offset_s": 1.2}, "dur_s": 2.0, "why": ...}, or {"media": ID, "in_s": S, ...} instead of a shot, or "at_s" timeline seconds; they take "speed", "zoom", "frame_x", "tilt_px" and "caption_y" too. Captions sit in one band: give "caption_y" only where a face or the product sits in the band, and the same value for every such shot (assemble snaps all asks to one alternate band, caption_y_snapped; caption_jump warns).
- "music": [{"media": ID, "in_s": 0.0, "at_s": 0.0, "until": "end", "gain_db": -6.0, "duck": "auto", "fade_out_s": 1.0, "grid": true}]; add "align": "end" to start the song where its natural ending lands on the programme end (assemble computes in_s); "duck_hold_s" and "duck_max_db" override the preset's duck hold and depth cap for one item. Ducked music comes up on its own only in a voice gap over about 3.1 s (a duck_hold_s under 2.5 s changes nothing): to lift it in a shorter gap (the drop, a musical beat between two lines), give the picture-only spine segment in that gap "lift": true.
- "titles": [{"text": ..., "at_s": ... (or "at": {"seg": "s03", "offset_s": 0.4} like an overlay), "dur_s": ..., "style": "hook_top" | "lower_third" | "center" | "super" | "cta" | "end_card", "role": "hook" | "offer" | "proof" | "brand" | "cta" | "name" | "other", "y": optional fraction of the height, "why": ...}]; a "\n" in the text is a line break. Lower thirds name people only; a super shows the spoken words or their keyword (in a text-led stretch, a new proof point from the brief); the look (font, colour, stroke, box) comes from the preset's looks (clean by default: hook_clean, super_clean, caption_soft, cta_card) and ${LAB}/brand.json, never from the cut list. Two titles may be on screen at once (the second goes to the "Titles 2" track, never three): on the end card the brand name (style "end_card", role "brand") and the call to action (style "cta", role "cta") start together on the card's first frame and run to its end, both centred on the logo; titles that follow each other on one track meet edge to edge (chain them with "at": {"seg", "offset_s"}). "captions": {"from": "dialogue", "text_fix": {"<media id>:<word index>": "shown text"} ("" hides a word in the captions only; only from the script, the brief or its glossary), "look": optional look id} when the preset has captions; when \`E0 schema cutlist\` shows them, also "keep_together": ["phrase"] and "suppress_under_titles": true.
- "mix": {"gain_db": X} (top level): one uniform gain in dB on every audio item for the loudness target; after a review, write the value the loudness_off fix names (KNOWLEDGE.md section 9).
- "transitions": [{"after": "s03", "type": "cross_dissolve", "frames": 12}] (a type from the preset's "transitions"."allowed": catalogue keys and the generic "whip" and "zoom"; the effects fields below); "audio_leads": [{"seg": "s03", "lead_ms": 300}] for short J cuts of 4 to 12 frames (a lead only reaches through the pause before the segment's first word). For 0.5 to 2 s J and L cuts use "hold_prev_s" and "early_s" on the incoming segment (KNOWLEDGE section 6), never an overlay of the same shot: an overlay plays from its own trim, so the picture jumps inside the shot (overlay_jump). Re-read the EDL after every assemble. "markers" (Resolve colours: Blue, Cyan, Green, Yellow, Red, Pink, Purple, Fuchsia, Rose, Lavender, Sky, Mint, Lemon, Sand, Cocoa, Cream; one line of at most 90 characters, only for an action the user must take); "targets": {"duration_s": ...} only for an exact length (ads): it becomes a frame-exact STOP gate, so leave it out for a soft target (the preset's length range already applies).
Assemble snaps every speech edge into the pause next to it, adds the preset's pads and 1-frame audio fades, lands beat segments on the grid without touching any word, only makes lengths Resolve can place, builds captions from the kept words (in the script's spelling when the transcript was aligned) and the music ducking, and reports what it did in OUT.assemble.json.`

function cutPrompt(c, outline, selectsPath) {
  const dir = `${WF}/cut_${c.id}`
  let plan
  if (outline && outline.outline_path) {
    plan = `Outline ${outline.id} (${outline.angle}): ${outline.outline_path}. Logline: ${outline.logline || 'none'}. Risks the architect named: ${JSON.stringify(outline.risks || [])}.`
  } else {
    const why = c.planOwn ? 'You plan your own story first (no architect in this tier).' : `No outline came back for the angle ${c.angle}.`
    plan = `${why} Angle: ${c.angleText || ANGLES[c.angle] || c.angle}
Before cutting, write ${dir}/outline.json (\`E0 schema outline\`): ${OUTLINE_SPEC.replace('<angle id>', c.angle)}. Every beat cites exact material (word ranges, shot ids); check that the spoken material fits the preset's length; keep to the shot budget in the selects.
${COPY_STEP}`
  }
  return pre(`cutter ${c.id}`, dir, '1 to 11 and 15', 'cut', `at most ${MAXP} \`E review\` runs in total, including the final one. Stop earlier once the cut is right and nothing STOPs.`) + `ROLE: CUTTER ${c.id}. Cut style: ${c.styleText}

${plan}
Selects: ${selectsPath || `${LAB}/wf/assist/selects.md or ${LAB}/wf/log_1/selects.md if one exists, else the transcripts and the shot log`}.

${CUTLIST_SPEC}

${FX_FIELDS}
${FX_RULES}

How to work:
1. Read the outline, the selects, the transcripts of the media you use (\`M transcript ID --from W --to W\`) and the shot log entries of the shots you use.
2. Write ${dir}/cutlist.json with "id": "cut_${c.id}", "by": "cutter ${c.id}"${PRESET ? `, "preset": "${PRESET}"` : ''}. Use only media in media/index.json.
3. \`E assemble ${dir}/cutlist.json ${dir}/v001.json\`, read its RESULT lines and ${dir}/v001.assemble.json (snaps, pads, fillers kept and why).
4. \`E review ${dir}/v001.json --tier standard\` writes ${dir}/review_v001/: read report.txt first, then checks.json, then at most 6 images (overview first, then the cut sheets of flagged cuts). Compare the report's DIALOGUE ON THE TIMELINE with your cut list's word ranges: every range must read exactly as you chose it (words_changed is a STOP). Watch for CLIPPED words, FLASH frames, OFF-BEAT cuts, JUMP? cuts, black edges from a pan or tilt (frame_edge), a face cut off by a vertical crop (subject_cropped: set the frame_x the fix names, which is always the face that segment frames; in a two-shot, frame each line on the person speaking, a cut between them is right), the same setup shown again (repeat_setup), long stretches with no voice or text, long stretches with no visual change, a weak first 3 s and an ending that does not pay off. Log footage looks flat and grey here: judge framing, focus, action and emotion, not colour.
5. Revise the cut list, assemble the next version (v002.json, v003.json, ...), review it; \`E diff\` shows what changed between two versions.
6. Final: assemble your best cut list to ${dir}/edl.json and review it (\`E review ${dir}/edl.json --tier standard\`, it counts in the budget), so its review folder is ${dir}/review_edl/. No STOP may remain unless you explain in the self-critique why it cannot be fixed with this footage.
Return your id "${c.id}", the EDL path, the review folder, the RESULT of its checks, a rationale (the story in two or three sentences, the key decisions), an honest self-critique (what is weak, where a viewer may drop off, what you could not fix)${outline && outline.outline_path ? '' : ' and the path of the outline you wrote'}.`
}

function candidateTable(cands) {
  const lines = cands.map(c => `- ${c.id} (angle ${c.angle}, style ${c.style}): EDL ${c.edl_path}, review ${c.review_dir}, checks ${c.result}`)
  return `Candidates (refer to them by id):\n${lines.join('\n')}`
}
function rationales(cands) {
  const lines = cands.map(c => `- ${c.id} notes. Rationale: ${c.rationale || 'none'}. Self-critique: ${c.self_critique || 'none'}`)
  return `The cutters' own notes (read them only after you scored; they explain intent, they are not evidence):\n${lines.join('\n')}`
}

const CD_QUESTIONS = 'Creative director questions: does the first second stop the scroll? Is there one message by 3 s? When does the offer (or the reason to act) land: by 40 % of the length? Is there a call to action with a route (link in bio, URL, phone, button), in text and in the voice? Is the brand legible at the start and at the end? Is the typography premium: one type system, clean looks, the hook the boldest and largest text before the card (type_system)? Proof, then the offer once, then the call to action? On the end card, are the name and the call to action locked up together for the whole card, the CTA the largest text, held 2.5 s or more (end_card)? Does it end on a hit, not in a lull? Is there any stretch over 1.5 s without voice or text? Would the client sign it off? Ship blockers: a wrong word on screen, an offer after half the length, a call to action with no route, a proof line on a picture that does not show it, a picture that contradicts its line.'
const JUDGES_BEST = [
  { key: 'story', text: 'LENS: STORY AND EMOTION. Emotion first (Murch weights it 51 %), then story: does each cut serve the feeling of the moment and move the story, do emotional lines stay on the face long enough (a face only when its performance carries the feeling), is the order clear, is the message clear, does the ending pay off the hook, does every cut have a reason.' },
  { key: 'rhythm_sound', text: 'LENS: RHYTHM AND SOUND. Pacing against the preset (median shot, CV, visual change gaps, densest window), cut-to-beat offsets (OFF-BEAT flags, beat_sync), J and L cuts, pauses and fillers left in, silence used on purpose or by accident, clipped words, speech to music gap both ways (the bed within the preset\'s duck_lu to duck_lu + 3 LU under the voice, never buried: speech_music_gap), music that pumps or opens loud before the first word (duck_pump), voice gaps over 0.6 s and tails over 1 s after the last word (vo_gap, ending_tail), the pace shape (a short piece builds into its payoff: pace_decel), a quiet first second (music_open_quiet), lulls in the music (music_lull), the music ending on a hit or the song\'s own ending at full energy, the mix loudness against the target (loudness_off), audio fades at every join.' },
  { key: 'audience', text: 'LENS: AUDIENCE AND PLATFORM. The first 3 s (would you keep watching?), where a viewer would swipe away and why, muted viewing (does it work with captions and text alone?), caption timing and layout, safe zones, framing for the platform (a face cut off by a vertical crop of wider footage), the platform length, whether the hook title and the first spoken line agree or build on each other (two different messages in the same 3 s compete), the type system (one caption band, captions 84 px on a vertical frame, the hook the largest text before the card, clean looks: caption_jump, type_system), and on an end card the name and the call to action locked up together (end_card).' },
]
const JUDGE_CD = { key: 'creative_director', text: `LENS: CREATIVE DIRECTOR (the client's eyes, also audience and platform). ${CD_QUESTIONS} Also muted viewing, caption timing and layout, safe zones, framing for the platform, and the ABCD checklist (KNOWLEDGE.md section 13).` }
const JUDGE_COMBINED = { key: 'combined', text: 'LENS: STORY, RHYTHM AND AUDIENCE IN ONE. Weigh the brief first (message, feeling, platform), then emotion and story, then rhythm and sound (pacing and the pace shape into the payoff, beat offsets, pauses and voice gaps, clipped words, audio joins, music that pumps, opens quiet, sags or ends in a lull, the bed level against duck_lu, loudness_off), then the first 3 s, muted viewing, captions (one band, the size) and safe zones.' }
const judgeList = () => {
  if (TIER === 'best' && SIZE === 'full') return AD ? [JUDGES_BEST[0], JUDGES_BEST[1], JUDGE_CD] : JUDGES_BEST
  return [AD ? { key: 'combined', text: `${JUDGE_COMBINED.text} ${CD_QUESTIONS}` } : JUDGE_COMBINED]
}

function judgePrompt(j, cands) {
  const dir = `${WF}/judge_${j.key}`
  return pre('a judge', dir, '2 to 11, 13 and 15', 'judge', null) + `ROLE: JUDGE. ${j.text}
${FX_LENS}

${candidateTable(cands)}

Score from the brief and each candidate's own evidence first, the way a paying client would see it: read its review folder (report.txt and checks.json first, then at most 6 of its images: overview first, then cut sheets). For a close look at a moment, \`E frames EDL T,T --out ${dir}/<candidate id>\`. Score each candidate with the scorecard in KNOWLEDGE.md section 13, including its caps: 0 to 10 per dimension, using the preset's judging.weights, and "score" = the weighted score from 0 to 10. A candidate whose checks RESULT is STOP scores at most 5 unless every candidate STOPs. Quote timeline times for every point you deduct. Pick a winner (its id), list grafts from other candidates worth adding to the winner (for example "B1's opening 0.0 to 3.2 s", "A1's music ending"), and list must-fix issues with times and concrete fixes.

${rationales(cands)}`
}

function finishPrompt(start, context, dir) {
  const from = start
    ? `Start from the EDL ${start}. Its cut list is named in the EDL's "version"."from_cutlist" field (usually cutlist.json in the same folder). Copy that cut list to ${dir}/cutlist.json and edit only the copy. Graft the endorsed ideas and fix every must-fix without losing what made this version win.`
    : `There is no EDL to start from: write ${dir}/cutlist.json from scratch (\`E0 schema cutlist\` prints the format).`
  return pre('the finishing editor', dir, '1 to 15', 'cut', `at most ${MAXP} \`E review\` runs.`) + `ROLE: FINISHING EDITOR. ${context}

${FX_FIELDS}
${FX_RULES}

${from} Then \`E assemble ${dir}/cutlist.json ${dir}/edl.json\`, \`E review ${dir}/edl.json --tier best\` (review folder ${dir}/review_edl/) and \`E check ${dir}/edl.json\`: the final must not STOP. Before you return, read loudness_off in checks.json: when it fires, write the mix line its fix names (\"mix\": {\"gain_db\": X}) into your cut list, assemble and review again (the gain is capped by the true peak; the residual goes into user_summary for the Deliver page). This goes to the client: every cut, word and caption must hold up; titles carry their roles and the copy from the winning outline. Return the EDL path, the review folder, the RESULT, a summary (the story, the length, the key decisions), what you changed and why, "user_summary" (at most 5 plain lines for the user) and "open_actions" (at most 5 one-line actions only the user can take, for example a word to confirm or a pickup shot to add); put anything longer in ${dir}/notes.md.`
}

const BLOCKING_RULE = 'Anything a paying client would notice in one viewing is blocking. A fixed length and thin footage are never reasons to keep a repeat while a hold, slow motion, a size change or a text card is possible. A wrong word on screen, a proof line on a picture that does not show it, and a picture that contradicts its line (an unsafe scene under a safety line, a child or a bystander at a working station, a mess under a hygiene line) are always blocking.'
const REVIEWERS_BEST = [
  { key: 'technical', text: `LENS: TECHNICAL QC. Run \`E check\` on the final and read every flag in report.txt: clipped words, words changed from the cut list (compare DIALOGUE ON THE TIMELINE with the cut list word ranges), words on screen the check calls caption_unverified, flash frames, black or silent gaps, black edges from pans or tilts, faces cut off by a crop (subject_cropped), voices in one ear or a different speaker in each ear (channel_balance), caption timing and layout, safe zones, beat offsets, music that pumps (duck_pump), audio fades, the speech to music gap, duration, jump cuts, the frame rate against the Resolve project, and the effects checks (more than 3 flashes in a second, an effect on text or an offer, black edges from a move, animated text under its reading floor, transitions without handles or through black, a smooth cut outside one take, a retime past the media). Read the craft checks too: title_blink, end_card, pace_decel, music_lull, music_open_quiet, vo_gap, ending_tail, caption_jump, speech_music_gap (too loud or buried), loudness_off (the mix gain it names, and the residual), and when checks.json lists them type_system, recycled_half, title_repeat, reveal_hold and text_contrast. Verdict "fix" for any STOP. ${BLOCKING_RULE}` },
  { key: 'devils_advocate', text: `LENS: DEVIL'S ADVOCATE. Attack the edit: which 20 % could go without losing the story? Where would a viewer swipe away, and why? Which cut has no reason? Which shot or setup repeats? Is any process shown out of order (a step before the one it needs, a result before its making)? Where did we leave an emotional face too early, or stay on a boring shot too long? Watch only the first 3 s: would you keep watching? Imagine it muted: does it still make sense? Does the ending pay off the hook's promise? Which effect could go: name every punch, bump, ramp, accent, transition effect or text animation that serves no moment of the brief, or that a viewer notices before the content, and any text that moves while it must be read. Score the final with the scorecard (KNOWLEDGE.md section 13, with its caps): under the 7.5 pass bar is a "fix" verdict naming the weakest dimensions and what would lift them. ${BLOCKING_RULE}` },
]
const REVIEWER_CD = { key: 'creative_director', text: `LENS: CREATIVE DIRECTOR. Watch it once as the client would. ${CD_QUESTIONS} Every ship blocker is blocking; score the message dimension with the anchors of KNOWLEDGE.md section 13. ${BLOCKING_RULE}` }
const REVIEWER_STANDARD = { key: 'combined', text: `LENS: TECHNICAL QC AND DEVIL'S ADVOCATE IN ONE. First run \`E check\` and read every flag (clipped words, words changed from the cut list ranges, caption_unverified, flash frames, gaps, black edges, caption timing and layout, safe zones, beat offsets, music that pumps, audio joins, duration, and the craft checks title_blink, end_card, pace_decel, music_lull, music_open_quiet, vo_gap, ending_tail, caption_jump, speech_music_gap and loudness_off), then attack the edit: what could go, what repeats, where a viewer would swipe away, the first 3 s, muted viewing, whether the ending pays off the hook, and which effect could go (one that serves no moment, or that a viewer notices before the content). Score it with the scorecard (KNOWLEDGE.md section 13, with its caps): under the 7.5 pass bar is a "fix" verdict naming the weakest dimensions. ${BLOCKING_RULE}` }
const CD_BLOCKERS = ' This is an ad: the creative director\'s ship blockers are blocking too (an offer after half the length, a call to action with no route, the brand not legible at the start and the end).'
const reviewerList = () => {
  if (TIER === 'best' && SIZE === 'full') return AD ? [...REVIEWERS_BEST, REVIEWER_CD] : REVIEWERS_BEST
  const base = TIER === 'best' ? REVIEWERS_BEST : [REVIEWER_STANDARD]
  if (!AD) return base
  return base.map(v => v.key === 'technical' ? v : Object.assign({}, v, { text: v.text + CD_BLOCKERS }))
}

function reviewPrompt(v, cur, round) {
  const dir = `${WF}/review_${v.key}_r${round}`
  return pre('an adversarial reviewer', dir, '4 to 11 and 13 to 15', 'review', 'at most 2 `E review` runs, only to prove that a fix works.') + `ROLE: ADVERSARIAL REVIEWER. ${v.text}
${FX_LENS}

Final edit: EDL ${cur.edl_path}, review folder ${cur.review_dir || '(missing: copy the EDL into your folder and run `E review <your copy> --tier standard` there)'}.
Judge it from the brief, report.txt, checks.json and at most 6 images (overview first) before you read the finisher's notes at the end of this prompt. \`E frames EDL T,T --out ${dir}\` for close looks. To prove a fix works you may copy the final's cut list into your folder, change it, \`E assemble\` it there and review it there; return every fix you proved in "proven" ([{"what", "cutlist_path", "edl_path"}]): proven fixes are applied in a polish pass even when your verdict is pass. Give every blocking issue its timeline time in seconds (at_s) and a concrete fix. At most 5 minor notes, ranked, one line each.

Finisher notes (read last): ${cur.summary || 'none'}`
}

function fixPrompt(cur, blocking, minor, proven, round) {
  const dir = `${WF}/final_r${round}`
  const provenText = proven.length
    ? `\nFixes the reviewers proved in their own folders (their cut list and EDL; \`E diff\` against the current final shows each change): adopt each one that solves its issue.\n${JSON.stringify(proven, null, 1)}\n`
    : ''
  return pre('the finishing editor', dir, '1 to 15', 'cut', `at most ${MAXP} \`E review\` runs.`) + `ROLE: FINISHING EDITOR, FIX PASS ${round}. Current final: EDL ${cur.edl_path}, review folder ${cur.review_dir || 'none'}.
Blocking issues:
${JSON.stringify(blocking, null, 1)}
Minor notes: ${JSON.stringify(minor)}
${provenText}${FX_RULES}

Copy the current final's cut list (named in its "version"."from_cutlist" field) to ${dir}/cutlist.json and edit only the copy. Fix every blocking issue without breaking anything else, then \`E assemble ${dir}/cutlist.json ${dir}/edl.json\`, \`E review ${dir}/edl.json --tier best\` and \`E check ${dir}/edl.json\` (no STOP). Before you return, read loudness_off in checks.json: when it fires, write the mix line its fix names (\"mix\": {\"gain_db\": X}) into your cut list, assemble and review again (the gain is capped by the true peak; the residual goes into user_summary for the Deliver page). Check your own work against every blocking issue (nobody reviews after you${TIER === 'best' && SIZE === 'full' ? ' except one more review round' : ''}). Return the EDL path, the review folder, the RESULT, a summary, what you changed (per blocking issue: fixed, or why not), "user_summary" (at most 5 plain lines) and "open_actions" (at most 5 one-line actions only the user can take, including every blocking issue you could not fix).`
}

function polishPrompt(cur, proven, round) {
  const dir = `${WF}/final_r${round}`
  return pre('the finishing editor', dir, '1 to 15', 'cut', `at most ${MAXP} \`E review\` runs.`) + `ROLE: FINISHING EDITOR, POLISH PASS. Every reviewer passed the current final (EDL ${cur.edl_path}, review folder ${cur.review_dir || 'none'}), and they proved these improvements in their own folders:
${JSON.stringify(proven, null, 1)}

This list is your only input: change nothing else. Copy the current final's cut list (named in its "version"."from_cutlist" field) to ${dir}/cutlist.json and apply each proven change (compare the reviewer's cut list with the final's, and \`E diff\` the two EDLs). Then \`E assemble ${dir}/cutlist.json ${dir}/edl.json\`, \`E review ${dir}/edl.json --tier best\` and \`E check ${dir}/edl.json\`. Keep a change only when it does what its "what" says and adds no STOP or new WARN; leave out any change that makes something worse and say why. The one change you make yourself: when loudness_off fires on the result, write the mix line its fix names (\"mix\": {\"gain_db\": X}), assemble and review again, and put the residual in user_summary. Return the EDL path, the review folder, the RESULT, a summary, what you applied and what you left out, "user_summary" (at most 5 plain lines) and "open_actions" (at most 5 one-line actions only the user can take).`
}

// ---------------------------------------------------------------- run
let logs = []
let assist = null
let outlines = []
let candidates = []
let judgments = []
let tally = {}
let winner = null
let final = null
let verdicts = []
let selectsPath = SELECTS_IN
let coverage = []
const LOGGER_ASSISTS = SIZE === 'small' || (TIER === 'standard' && RUN === 'log')

if (RUN !== 'finish') {
  if (SELECTS_IN) {
    log(`Reusing the earlier log: ${LAB}/shotlog.json and ${SELECTS_IN}; Log and Assist are skipped.`)
    assist = { shotlog_path: `${LAB}/shotlog.json`, selects_path: SELECTS_IN, summary: 'selects from an earlier run' }
  } else if (LOGGER_ASSISTS) {
    phase('Log')
    const r = await call(logAssistPrompt(), { label: 'log:1', phase: 'Log', schema: LOG_ASSIST_SCHEMA })
    if (r && r.shotlog_path) logs = [r]
    if (r && r.selects_path) {
      selectsPath = r.selects_path
      coverage = Array.isArray(r.coverage) ? r.coverage : []
      assist = { shotlog_path: r.shotlog_path || `${LAB}/shotlog.json`, selects_path: r.selects_path, summary: r.notes || '', coverage }
      log(`Logger and assistant: ${Number(r.shots) || 0} shots logged; ${coverage.filter(c => c && c.status !== 'ok').length} coverage items weak or missing`)
    } else {
      log('The logger returned no selects; the cutters work from the transcripts and the shot log.')
    }
  } else {
    const nLog = SHEETS === 0 ? 0 : (TIER === 'best' ? Math.min(3, SHEETS === null ? 3 : SHEETS) : 1)
    if (nLog > 0) {
      phase('Log')
      logs = (await parallel(Array.from({ length: nLog }, (_, i) => () => call(logPrompt(i + 1, nLog),
        { label: `log:${i + 1}`, phase: 'Log', schema: LOG_SCHEMA })))).filter(r => r && r.shotlog_path)
      log(`Loggers: ${logs.length} of ${nLog} returned, ${logs.reduce((n, l) => n + (Number(l.shots) || 0), 0)} shots logged`)
    } else {
      log('No contact sheets (sheets: 0), so there is no logging stage.')
    }
    if (TIER === 'best') {
      phase('Assist')
      assist = await call(assistPrompt(logs), { label: 'assist', phase: 'Assist', schema: ASSIST_SCHEMA })
      if (assist && assist.selects_path) {
        selectsPath = assist.selects_path
        coverage = Array.isArray(assist.coverage) ? assist.coverage : []
      } else { assist = null; log('The assistant editor returned nothing; the architects build their own selects.') }
    }
  }

  if (RUN === 'log') {
    if (!selectsPath) throw stageFailed(LOGGER_ASSISTS ? 'Log' : 'Assist', LOGGER_ASSISTS ? 'the logger' : 'the assistant editor')
    log(`Agents used: ${agentsUsed} (limit ${LIMIT} for tier ${TIER}${TIER === 'best' ? `, size ${SIZE}` : ''})`)
    return {
      tier: TIER, size: SIZE, phase: RUN, selects_path: selectsPath, coverage,
      shotlog_path: (assist && assist.shotlog_path) || `${LAB}/shotlog.json`, logs, agents: agentsUsed, doc_gaps: docGaps,
    }
  }

  const angles = pickFrom(A.angles, ANGLES, DEFAULTS.angles, 2, 'angle')
    .map(a => AD && a.id === 'emotional_arc' ? Object.assign({}, a, { text: a.text + AD_ARC }) : a)
  const styles = pickFrom(A.styles, STYLES, DEFAULTS.styles, 2, 'style')
  const LETTERS = 'ABCDEFGH'
  const candOf = (job, r2) => r2 && r2.edl_path ? {
    id: job.id, angle: job.angle, style: job.style, edl_path: r2.edl_path,
    review_dir: r2.review_dir || `${dirOf(r2.edl_path)}/review_${String(r2.edl_path).replace(/^.*[\\/]/, '').replace(/\.json$/, '')}`,
    result: r2.result || 'WARN', rationale: r2.rationale || '', self_critique: r2.self_critique || '', previews: r2.previews || 0,
  } : null

  if (TIER === 'best' && SIZE === 'full') {
    log(`Angles: ${angles.map(a => a.id).join(', ')}; styles: ${styles.map(s => s.id).join(', ')}`)
    phase('Structure')
    const perOutline = await pipeline(angles,
      (angle, _item, i) => call(architectPrompt(angle, assist, logs),
        { label: `arch:${angle.id}`, phase: 'Structure', schema: OUTLINE_SCHEMA }),
      async (r, angle, i) => {
        const o = r && r.outline_path
          ? { id: LETTERS[i], angle: angle.id, outline_path: r.outline_path, logline: r.logline || '', risks: r.risks || [], selects_path: r.selects_path || null }
          : null
        if (!o) log(`No outline came back for the angle ${angle.id}; its cutters plan from the selects.`)
        const sel = selectsPath || (o && o.selects_path) || null
        const jobs = styles.map((s, k) => ({ id: `${LETTERS[i]}${k + 1}`, angle: angle.id, angleText: angle.text, style: s.id, styleText: s.text }))
        const cuts = await parallel(jobs.map(c => () => call(cutPrompt(c, o, sel),
          { label: `cut:${c.id}`, phase: 'Cut', schema: CUT_SCHEMA })))
        return {
          outline: o || { id: LETTERS[i], angle: angle.id, outline_path: null, logline: '', risks: ['no outline returned'] },
          selects_path: sel,
          cands: cuts.map((r2, k) => candOf(jobs[k], r2)),
          missing: jobs.filter((c, k) => !(cuts[k] && cuts[k].edl_path)).map(c => c.id),
        }
      })
    for (const p of perOutline.filter(Boolean)) {
      outlines.push(p.outline)
      if (!selectsPath && p.selects_path) selectsPath = p.selects_path
      candidates.push(...p.cands.filter(Boolean))
      if (p.missing.length) log(`No candidate from: ${p.missing.join(', ')}`)
    }
  } else {
    // standard and small best: two cutters, one per angle, both with the first style (one angle given: two styles)
    const jobs = angles.length > 1
      ? angles.slice(0, 2).map((a, i) => ({ id: `${LETTERS[i]}1`, angle: a.id, angleText: a.text, style: styles[0].id, styleText: styles[0].text }))
      : styles.slice(0, 2).map((s, k) => ({ id: `A${k + 1}`, angle: angles[0].id, angleText: angles[0].text, style: s.id, styleText: s.text }))
    let outlineA = null
    if (TIER === 'standard') {
      phase('Structure')
      const r = await call(architectPrompt(angles[0], assist, logs), { label: `arch:${angles[0].id}`, phase: 'Structure', schema: OUTLINE_SCHEMA })
      if (r && r.selects_path && !selectsPath) selectsPath = r.selects_path
      outlineA = r && r.outline_path
        ? { id: 'A', angle: angles[0].id, outline_path: r.outline_path, logline: r.logline || '', risks: r.risks || [], selects_path: r.selects_path || null }
        : null
      if (!outlineA) log(`No outline came back for the angle ${angles[0].id}; its cutter plans from the selects.`)
      outlines.push(outlineA || { id: 'A', angle: angles[0].id, outline_path: null, logline: '', risks: ['no outline returned'] })
    }
    for (const j of jobs) {
      j.outline = outlineA && j.angle === angles[0].id ? outlineA : null
      j.planOwn = !j.outline
    }
    log(`Cutters: ${jobs.map(c => `${c.id} (${c.angle}, ${c.style}${c.planOwn ? ', plans its own outline' : ''})`).join(', ')}`)
    phase('Cut')
    const cuts = await parallel(jobs.map(c => () => call(cutPrompt(c, c.outline, selectsPath),
      { label: `cut:${c.id}`, phase: 'Cut', schema: CUT_SCHEMA })))
    cuts.forEach((r2, k) => {
      const job = jobs[k]
      const cand = candOf(job, r2)
      if (cand) candidates.push(cand)
      else log(`No candidate from: ${job.id}`)
      if (job.planOwn) {
        outlines.push({ id: job.id, angle: job.angle, logline: '', risks: [], by: `cut:${job.id}`,
          outline_path: r2 ? (r2.outline_path || `${WF}/cut_${job.id}/outline.json`) : null })
      }
    })
  }
  log(`Candidates: ${candidates.map(c => `${c.id} (${c.result})`).join(', ') || 'none'}`)

  if (candidates.length) {
    phase('Judge')
    const judges = judgeList()
    const raw = await parallel(judges.map(j => () => call(judgePrompt(j, candidates),
      { label: `judge:${j.key}`, phase: 'Judge', schema: JUDGE_SCHEMA })))
    if (!raw.some(Boolean)) throw stageFailed('Judge', whoOf(judges.length, 'judge', 'judges'))
    judgments = raw.map((r, i) => r ? Object.assign({}, r, { key: judges[i].key }) : null).filter(Boolean)
    const idOf = x => {
      const n = String(x || '').trim().replace(/^cut_/i, '').toLowerCase()
      const hit = candidates.find(c => c.id.toLowerCase() === n)
      return hit ? hit.id : null
    }
    for (const c of candidates) tally[c.id] = 0
    for (const j of judgments) {
      for (const s of j.scores || []) {
        const id = s ? idOf(s.candidate) : null
        if (id && Number.isFinite(s.score)) tally[id] += s.score
      }
    }
    const best = Math.max(...Object.values(tally))
    const top = Object.keys(tally).filter(k => tally[k] === best)
    const story = judgments.find(j => j.key === 'story' || j.key === 'combined') || judgments[0]
    const sw = story ? idOf(story.winner) : null
    winner = top.length > 1 && sw && top.includes(sw) ? sw : top[0]
    log(`Judge totals: ${JSON.stringify(tally)}; winner ${winner}`)
  }
}

const winCand = candidates.find(c => c.id === winner) || candidates[0] || null

if (RUN === 'design') {
  final = winCand
    ? { edl_path: winCand.edl_path, review_dir: winCand.review_dir, result: winCand.result, summary: `Design phase only: the judged winner is "${winCand.id}", not finished yet. Show the user the candidates' previews and reports, then run phase "finish" with the pick.` }
    : { edl_path: null, review_dir: null, result: 'STOP', summary: 'Design phase only: no candidate came back.' }
  log(`Agents used: ${agentsUsed} (limit ${LIMIT} for tier ${TIER}${TIER === 'best' ? `, size ${SIZE}` : ''})`)
  return {
    tier: TIER, size: SIZE, phase: RUN, outlines, candidates, judgments, tally, winner, final, verdicts,
    verdicts_note: 'design phase: no review ran yet', selects_path: selectsPath, coverage, agents: agentsUsed,
    doc_gaps: docGaps, user_summary: [], open_actions: [],
  }
}

phase('Finish')
const norm = s => String(s || '').toLowerCase().replace(/\s+/g, ' ').trim()
let start, context
if (RUN === 'finish') {
  start = String(A.pick)
  context = `The user looked at the candidates and picked ${start}. The user's notes: ${NOTES || '(none)'}. Keep the character of the chosen version, apply the notes, and fix every technical problem its checks and report show.`
} else if (winCand) {
  start = winCand.edl_path
  const seen = new Set()
  const slim = judgments.map(j => ({
    judge: j.key,
    winner: j.winner,
    scores: (j.scores || []).filter(Boolean).map(s => [s.candidate, s.score]),
    must_fix: (j.must_fix || []).filter(m => {
      const k = norm(m)
      if (!k || seen.has(k)) return false
      seen.add(k)
      return true
    }),
    grafts: j.grafts || [],
  }))
  context = `${candidateTable(candidates)}

Judges (score totals ${JSON.stringify(tally)}; must-fixes listed once, one judge per line):
${slim.map(j => JSON.stringify(j)).join('\n')}

The winner is "${winCand.id}". Its cutter's own self-critique: ${winCand.self_critique || 'none'}`
} else {
  start = null
  context = `No candidate came back. Cut the piece yourself: plan it from the selects (${selectsPath || 'the transcripts and the shot log'}) and the outlines (${JSON.stringify(outlines.map(o => o.outline_path).filter(Boolean))}).`
}
const f1 = await call(finishPrompt(start, context, `${WF}/final_r1`),
  { label: 'finish:r1', phase: 'Finish', schema: FINAL_SCHEMA })
if (f1 && f1.edl_path) final = f1
else {
  log('The finisher returned nothing; the final falls back to its starting point.')
  final = start
    ? { edl_path: start, review_dir: winCand && winCand.edl_path === start ? winCand.review_dir : null, result: winCand && winCand.edl_path === start ? winCand.result : 'WARN', summary: 'The finisher did not return; this is the unfinished starting point. Review it before building.' }
    : null
}

const reviewers = reviewerList()
const MAX_REVIEW_ROUNDS = TIER === 'best' && SIZE === 'full' ? 2 : 1
const MAX_FIXES = 1
let round = 1
let fixes = 0
let polished = false
let reviewedFinal = false   // do the returned verdicts belong to the returned final?
let polishedFinal = false   // is the returned final a polish of the reviewed one?
let remaining = []          // blocking issues still open after the last review
let lastProven = []
while (final && final.edl_path) {
  const cur = final
  phase('Review')
  const raw = await parallel(reviewers.map(v => () => call(reviewPrompt(v, cur, round),
    { label: `review:${v.key}:r${round}`, phase: 'Review', schema: REVIEW_SCHEMA })))
  if (!raw.some(Boolean)) throw stageFailed(`Review round ${round}`, whoOf(reviewers.length, 'reviewer', 'reviewers'))
  verdicts = raw.map((r, i) => r ? Object.assign({}, r, { key: reviewers[i].key }) : null).filter(Boolean)
  reviewedFinal = true
  const blocking = verdicts.flatMap(v => v.verdict === 'fix' ? (v.blocking || []) : [])
  const minor = verdicts.flatMap(v => (v.minor || []).slice(0, 5))
  lastProven = verdicts.flatMap(v => (Array.isArray(v.proven) ? v.proven : [])
    .filter(p => p && p.edl_path)
    .map(p => ({ what: String(p.what || ''), cutlist_path: p.cutlist_path || null, edl_path: p.edl_path, by: `review:${v.key}:r${round}`, verdict: v.verdict })))
  log(`Review round ${round}: ${verdicts.map(v => `${v.key}=${v.verdict}`).join(', ')} (${blocking.length} blocking, ${lastProven.length} proven)`)
  if (!blocking.length) {
    if (lastProven.length && !polished) {
      phase('Polish')
      const proven = lastProven.map(({ what, cutlist_path, edl_path, by }) => ({ what, cutlist_path, edl_path, by }))
      const next = await call(polishPrompt(cur, proven, round + 1), { label: `polish:r${round + 1}`, phase: 'Polish', schema: FINAL_SCHEMA })
      polished = true
      if (next && next.edl_path) { final = next; reviewedFinal = false; polishedFinal = true }
      else log('The polish pass returned nothing; keeping the reviewed final.')
    }
    break
  }
  if (fixes >= MAX_FIXES) {
    remaining = blocking
    log('Blocking issues remain after the fix round; they are in verdicts and open_actions for the user to decide.')
    break
  }
  phase('Fix')
  const proven = lastProven.map(({ what, cutlist_path, edl_path, by }) => ({ what, cutlist_path, edl_path, by }))
  const next = await call(fixPrompt(cur, blocking, minor, proven, round + 1), { label: `fix:r${round + 1}`, phase: 'Fix', schema: FINAL_SCHEMA })
  fixes++
  if (next && next.edl_path) { final = next; reviewedFinal = false }
  else { log('The fix pass returned nothing; keeping the previous final.'); remaining = blocking; break }
  if (round >= MAX_REVIEW_ROUNDS) {
    log('The fix pass checked its own work; there is no further review round in this tier.')
    lastProven = []
    break
  }
  round++
}

if (final && remaining.length && lastProven.length) {
  const pick = lastProven.find(p => p.verdict === 'fix') || lastProven[0]
  final = Object.assign({}, final, { proven_alternative: { edl_path: pick.edl_path, what: pick.what, by: pick.by } })
  log(`A reviewer proved an alternative EDL for the open issues: ${pick.edl_path}`)
}

log(`Agents used: ${agentsUsed} (limit ${LIMIT} for tier ${TIER}${TIER === 'best' ? `, size ${SIZE}` : ''})`)
const verdicts_note = !verdicts.length
  ? 'no review ran (no final EDL came back): review it yourself before building'
  : reviewedFinal
    ? 'the verdicts are the review of the final edit'
    : polishedFinal
      ? 'every reviewer passed the edit before the polish pass; the polish pass applied only the fixes they had proved and checked its own work'
      : 'the verdicts are from the review BEFORE the last fix pass; the fix pass checked its own work and nobody reviewed the final after it, so check the blocking issues against the final report yourself'
const lines = x => (Array.isArray(x) ? x : String(x || '').split('\n')).map(s => String(s || '').trim()).filter(Boolean)
const openActions = []
for (const b of remaining) {
  const at = Number.isFinite(b && b.at_s) ? ` at ${b.at_s} s` : ''
  openActions.push(`Open blocking issue${at}: ${b.issue} (fix: ${b.fix})`)
}
if (final && final.proven_alternative) openActions.push(`A reviewer proved an alternative for the open issues: ${final.proven_alternative.edl_path}; compare it with E diff and E check before adopting it`)
for (const a of lines(final && final.open_actions)) if (!openActions.includes(a)) openActions.push(a)
return {
  tier: TIER, size: SIZE, phase: RUN, outlines, candidates, judgments, tally, winner, final, verdicts, verdicts_note,
  selects_path: selectsPath, coverage, agents: agentsUsed, doc_gaps: docGaps,
  user_summary: lines(final && final.user_summary).slice(0, 5), open_actions: openActions.slice(0, 5),
}
