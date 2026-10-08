export const meta = {
  name: 'resolve-colorist',
  description: 'Lab grade of a Resolve timeline: review the auto baseline, design competing looks, judge, finish and review',
  whenToUse: 'From the resolve-colorist skill once timeline.json, the frame cache, BRIEF.md and the auto baseline exist. args: {lab, skill, py, tier, base, directions, phase, pick, notes, max_renders}',
  phases: [
    { title: 'Audit', detail: 'best tier only: tonal, color and skin, continuity auditors' },
    { title: 'Baseline', detail: 'review the auto baseline, fix flagged clips and taste calls' },
    { title: 'Design', detail: 'one look designer per direction, in parallel' },
    { title: 'Judge', detail: 'judges score every candidate' },
    { title: 'Finish', detail: 'final grade from the winner plus grafts and must-fixes' },
    { title: 'Review', detail: 'technical and taste review with a bounded fix round' },
  ],
}

// Stage order (the skill's fallback without the Workflow tool runs the same stages with the Agent tool):
//   standard: baselinePrompt -> designPrompt x2 (parallel) -> judgePrompt x1 -> finishPrompt -> reviewPrompt x1
//             -> at most one fixPrompt                                                    (at most 7 agents)
//   best:     auditPrompt x3 (parallel) -> baselinePrompt -> designPrompt x4 (parallel) -> judgePrompt x3 (parallel)
//             -> finishPrompt -> reviewPrompt x2 (parallel) -> fixPrompt -> reviewPrompt x2 (at most 17 agents)
//   phase "design" stops after the judges; phase "finish" starts at finishPrompt from args.pick.
// Every agent works only inside its own folder under LAB/wf and never touches DaVinci Resolve.

const A = args || {}
if (!A.lab || !A.skill || !A.py) {
  throw new Error('workflow args need lab, skill and py (absolute paths)')
}
const LAB = String(A.lab).replace(/[\\/]+$/, '')
const SKILL = String(A.skill).replace(/[\\/]+$/, '')
const PY = String(A.py)
const TIER = ['standard', 'quick'].includes(String(A.tier || 'best').trim().toLowerCase()) ? 'standard' : 'best'
const RUN = ['all', 'design', 'finish'].includes(A.phase) ? A.phase : 'all'
const MAX = Number.isFinite(A.max_renders) && A.max_renders > 0 ? Math.floor(A.max_renders) : 8
const BASE = A.base ? String(A.base) : `${LAB}/baseline_auto.json`
const NOTES = A.notes ? String(A.notes) : ''
const WF = `${LAB}/wf`
const q = s => `"${String(s).replace(/"/g, '\\"')}"`
const G = `${q(PY)} ${q(SKILL + '/grade_lab.py')} ${q(LAB)}`

if (RUN === 'finish' && !A.pick) {
  throw new Error('phase "finish" needs args.pick (the params file the user chose)')
}

// ---------------------------------------------------------------- look directions (id -> preset + brief)
const SHARED_RULES = 'Shared rules: keep look.pivot near 0.47 so contrast does not change exposure. Warm highlights by lowering blue, not by raising red. Keep skin in its natural band (about +3 to +6 deg skin_hue_offset_deg for South or East Asian talent, about 0 for white or Black talent) and never let skin read magenta, green or grey. Keep look.skin_target_deg at the value in the brief (BRIEF.md, "People and skin"); the baseline sets it. With dark-skinned talent keep black_lift at or below 0.02 and no teal in the shadows (film_print, film_print_plus, teal_orange and moody_dense tint the shadows cool: set shadow_tint [0, 0, 0]; even [-0.012, -0.006, 0.012] turns shadowed skin magenta-blue; soft_pastel turns it green), and look at faces in shadow yourself, because the skin metrics do not see skin under about 15 % luma. Say in your self-critique which texture (grain, halation, glow) the look would want, because texture cannot be baked into a LUT and the user adds it by hand.'

const DIRECTIONS = {
  clean: { preset: 'clean_pop', text: 'CLEAN PREMIUM COMMERCIAL. True-to-life color with clean whites (white_point about 0.98) and clean blacks, clear separation between warm skin and a slightly cooler world, vivid but believable product colors, saturation pulled out of highlights and shadows. Contrast 0.38 to 0.45. It should look like a well-lit brand ad on a phone at full brightness, not like a filter.' },
  noir: { preset: 'moody_dense', text: 'MOODY AND DENSE. Mids darker (grey about 0.43), contrast 0.45 to 0.55, rich blacks that keep detail in hair and dark clothing, highlights held under about 0.94. The world is cooler and less saturated (greens and yellows down), skin stays warm and protected (skin_protect 0.5 or more). Cool shadows lean blue, not green. The preset sets render.exposure 0.62, which replaces any exposure change the baseline made (default 0.685): scale it by that change. On bright footage it can sit just over the clipping criterion: trim the flagged clip (stops about -0.1) or lower look.contrast to about 0.45; moving white_point does not help. With dark-skinned talent set shadow_tint [0, 0, 0].' },
  film_print: { preset: 'film_print', text: `FILMIC PRINT. Targets measured on Kodak 2383: cool low mids (grey b* about -4 at two stops under), neutral grey, warm top (grey b* about +3 at two stops over for 2383 viewed at D65, +5 to +7 for the warmer D60 print; the preset gives about -3.5 and +2.7, check with \`G measure PARAMS\`), raised cool blacks about 0.03, soft shoulder, yellows toward orange, blues and cyans converging on one teal-blue, greens darker and slightly toward cyan, skin luminance untouched. Lower chroma on blue, magenta and green. With dark-skinned talent lower black_lift to 0.02 and set shadow_tint [0, 0, 0] (KNOWLEDGE.md, "Split toning"). Tasteful, not a preset. Also try the preset film_print_plus (it adds look.hue_lum) for denser greens with cleaner skin.` },
  warm_editorial: { preset: 'golden_editorial', text: 'WARM EDITORIAL. Golden, inviting, magazine lifestyle: warm neutrals (grey b* about +2 to +3), gold highlights made by lowering blue, olive greens (green hue toward yellow, saturation about 0.7), muted blues, matte floor about 0.028, soft contrast 0.28 to 0.33. The preset sets balance.global_temp 0.6, which replaces the baseline value: add the two if the baseline had one. Watch skin drifting yellow: it should glow, not turn orange.' },
  soft_pastel: { preset: 'soft_pastel', text: 'SOFT PASTEL. Airy, bright, low contrast (0.22 to 0.26), lifted cyan-green shadows, near-neutral top, pastel palette (sat about 0.95, greens and blues down). The preset sets render.exposure 0.72, which replaces any exposure change the baseline made (default 0.685): scale it by that change. Not for dark-skinned talent in shadow or for night footage.' },
  teal_orange: { preset: 'teal_orange', text: 'COMPLEMENTARY HARMONY. Only when the footage already has warm skin plus cool elements. Split anchored at grey with a neutral band, blues toward cyan, yellows toward orange, neutrals kept close to neutral. Invisible grading: back off until nobody could point to the effect.' },
}
const DEFAULT_DIRECTIONS = TIER === 'best' ? ['clean', 'noir', 'film_print', 'warm_editorial'] : ['clean', 'film_print']

function pickDirections() {
  const want = Array.isArray(A.directions) && A.directions.length ? A.directions : DEFAULT_DIRECTIONS
  const out = []
  for (const d of want) {
    if (typeof d === 'string' && DIRECTIONS[d]) {
      out.push({ id: d, preset: DIRECTIONS[d].preset, text: DIRECTIONS[d].text })
    } else if (d && typeof d === 'object' && d.id && d.text) {
      out.push({ id: String(d.id).replace(/[^A-Za-z0-9_-]/g, '_'), preset: d.preset || null, text: String(d.text) })
    } else {
      log(`Unknown direction ${JSON.stringify(d)} skipped (known: ${Object.keys(DIRECTIONS).join(', ')})`)
    }
  }
  const limit = TIER === 'best' ? 4 : 2
  if (out.length > limit) log(`Using the first ${limit} directions of ${out.length} for tier ${TIER}: ${out.slice(0, limit).map(d => d.id).join(', ')}`)
  const chosen = out.slice(0, limit)
  return chosen.length ? chosen : DEFAULT_DIRECTIONS.map(id => ({ id, preset: DIRECTIONS[id].preset, text: DIRECTIONS[id].text }))
}

// ---------------------------------------------------------------- shared prompt parts
const EXIT = `Exit criteria (from the "summary" block of metrics.json; if a render has no summary block, use the per-clip values):
- max_clipped_pct under 1
- max_crushed_pct under 1.5, unless that part of the frame really is black (night sky, black backdrop)
- mean_slice_neutral_spread under 3 wherever simultaneous panels or consecutive shots are meant to match
- skin_hue_offset_deg of clips with people within 3 deg of the natural value for the skin groups in the brief (about 0 for white or Black skin, +3 to +6 for South or East Asian skin)
- no new entries in flagged_clips compared with the baseline render, except a skin off flag on a clip whose skin_hue_offset_deg moved less than about 2 deg`

function pre(dir) {
  return `You are part of a professional color grading team working in an offline lab. The lab bakes the grade into LUTs later; nothing you do touches DaVinci Resolve.

Lab command: below, G stands for this exact prefix (run it with Bash; keep the quotes, the paths may contain spaces):
  ${G}
For example: ${G} defaults

Before you start:
1. Read the brief ${LAB}/BRIEF.md and the craft notes ${SKILL}/KNOWLEDGE.md.
2. Run \`G defaults\` once to see every parameter and what it does. Run \`G commands\` once to see which commands exist.

How to work:
- Params files: create them only with \`G init-params OUT [--preset NAME]\` or \`G merge BASE OVERLAY [OVERLAY...] OUT\`, where OVERLAY is a small json file you write with only the keys you change, or a preset name (\`G presets\` lists them). Every params file must keep "schema": 2. A file without it renders with the old v1 defaults and render prints a WARNING; if you see that warning, fix the file.
- Render: \`G render PARAMS OUTDIR\` writes screens_NN.jpg (the real screen at every cut, the most important images), sheet_NN.jpg (every clip), strips_NN.jpg (5 frames across each clip) and metrics.json.
- Look at the images yourself with the Read tool: at most 6 images per render, screens first, then sheet. Metrics back up what you see; they never replace looking.
- For a side-by-side decision (which contrast, which preset, how warm), use \`G wedge PARAMS KEY VALUES OUTDIR\` (one image, one row per value; KEY like look.contrast or balance.global_temp or preset) instead of several renders.
- Budget: at most ${MAX} renders (a wedge counts as one). Stop earlier once the exit criteria hold and the look is right.

${EXIT}

Rules: work only inside your own folder ${dir} (create it). Never call DaVinci Resolve tools. Never run luts, apply-script, grab-script, backup-script or dump-script. Never edit grade_lab.py, cameras.py, the cache, timeline.json or anything outside your folder.

`
}

// ---------------------------------------------------------------- schemas
const S = (props, required) => ({ type: 'object', properties: props, required })
const STR = { type: 'string' }
const STRS = { type: 'array', items: { type: 'string' } }
const NUM = { type: 'number' }

const AUDIT_SCHEMA = S({
  lens: STR, summary: STR,
  findings: { type: 'array', items: S({ severity: { type: 'string', enum: ['high', 'medium', 'low'] }, clips: STRS, issue: STR, evidence: STR, fix: STR }, ['severity', 'issue', 'fix']) },
  recommended_params_path: STR,
}, ['lens', 'summary', 'findings', 'recommended_params_path'])
const BASELINE_SCHEMA = S({ params_path: STR, render_dir: STR, summary: STR, per_clip_decisions: STR, remaining_issues: STRS, renders: NUM }, ['params_path', 'render_dir', 'summary'])
const DESIGN_SCHEMA = S({ direction: STR, params_path: STR, render_dir: STR, rationale: STR, self_critique: STR, texture: STR, renders: NUM }, ['direction', 'params_path', 'render_dir', 'rationale', 'self_critique'])
const JUDGE_SCHEMA = S({
  lens: STR,
  scores: { type: 'array', items: S({ candidate: STR, score: NUM, strengths: STR, weaknesses: STR }, ['candidate', 'score', 'strengths', 'weaknesses']) },
  winner: STR, grafts: STRS, must_fix: STRS,
}, ['lens', 'scores', 'winner', 'must_fix'])
const FINAL_SCHEMA = S({ params_path: STR, render_dir: STR, summary: STR, changes: STR, texture: STR, renders: NUM }, ['params_path', 'render_dir', 'summary'])
const REVIEW_SCHEMA = S({
  lens: STR, verdict: { type: 'string', enum: ['pass', 'fix'] },
  blocking: { type: 'array', items: S({ issue: STR, clips: STRS, fix: STR }, ['issue', 'fix']) },
  minor: STRS,
}, ['lens', 'verdict', 'blocking'])

// ---------------------------------------------------------------- prompts
const AUDITS = [
  { key: 'tonal', text: 'LENS: TONAL AND EXPOSURE. Exposure, contrast, highlight roll-off and shadow handling of every clip: blown or grey highlights, milky or crushed blacks, consistency inside each clip (strips), mid-tones for a phone screen, and whether balance.expo_* and render.* suit this footage.' },
  { key: 'color', text: 'LENS: COLOR BALANCE, SKIN, HUE FIDELITY. White balance and casts per clip (cast_ab_midtones, the balance block and flags in metrics.json), skin for the skin groups in the brief (natural, healthy, never orange, grey, green or magenta), brand and product colors, foliage, and neutrals (walls, paper, white clothes). Decide where the auto balance kept a cast on purpose (warm practicals, golden hour) and where it should not have.' },
  { key: 'continuity', text: 'LENS: CONTINUITY AND THE REAL SCREEN. Using screens_NN.jpg and the slices in metrics.json (luma_spread, neutral_spread), check how simultaneous panels and consecutive shots match in brightness, contrast and color, and how the piece flows from the first slice to the last. Do not flatten deliberate differences.' },
]

function auditPrompt(a) {
  const dir = `${WF}/audit_${a.key}`
  return pre(dir) + `ROLE: AUDITOR. ${a.text}

Start from the auto baseline ${BASE} (made by code: grayness white balance and half-way auto exposure, then \`match\` trims in clip_overrides). Render it into ${dir}/r0 and look. Propose concrete changes (clip_overrides {stops, temp, tint} per clip key, balance or render keys; never the look section), try them in your folder and confirm with a render. Return your findings and the path of your best validated params file (the baseline path itself if it needs nothing).`
}

function baselinePrompt(audits) {
  const dir = `${WF}/baseline`
  const from = audits && audits.length
    ? `Auditor reports:\n${JSON.stringify(audits, null, 1)}\n\nReconcile them into ONE baseline. Where auditors disagree, test both and look.`
    : 'Review it yourself: fix only what is actually wrong.'
  return pre(dir) + `ROLE: LEAD COLORIST, BASELINE. The auto baseline ${BASE} was made by code: grayness white balance and half-way auto exposure per clip, then \`match\` trims that pull every clip's neutral cast onto one target (they are ordinary clip_overrides you can edit). Render it into ${dir}/r0 and look. ${from}

Only fix: clips listed in summary.flagged_clips when the image confirms the problem, anything visibly wrong (a cast, a bad exposure, a panel that does not match its neighbours), and taste calls the brief asks for (for example keep warm practical light by easing that clip's temp trim, or add house warmth with balance.global_temp). Touch only balance, clip_overrides and render; leave the look section at the defaults, with one exception: set look.skin_target_deg to the value in the brief (BRIEF.md, "People and skin": 0 for white or Black talent, +3 to +6 for South or East Asian talent, 0 to +3 for mixed groups), so the skin flags measure against the right line from here on. Useful knobs are in KNOWLEDGE.md, "Balance and matching". \`G match PARAMS OUT [--target median|CLIPKEY]\` re-runs the automatic matching if you change balance settings; it adds its trims to existing clip_overrides.

Save the result as ${dir}/baseline.json (or return ${BASE} itself if it needs nothing) and render it to ${dir}/render_final. Return the path, the per-clip decisions and anything still imperfect.`
}

function designPrompt(d, base) {
  const dir = `${WF}/cand_${d.id}`
  const start = d.preset
    ? `Start with \`G merge ${q(base.params_path)} ${d.preset} ${q(dir + '/start.json')}\` (the baseline plus the ${d.preset} preset).`
    : `Start from the baseline ${base.params_path}.`
  return pre(dir) + `ROLE: LOOK DESIGNER. Direction: ${d.text}

${SHARED_RULES}

Matched baseline: ${base.params_path} (render: ${base.render_dir || 'not rendered yet'}; notes: ${base.summary || 'none'}). ${start} Design the look section for THIS footage and brief. You may also change render.lum_preserve, render.exposure, render.hue_restore or balance.global_temp / global_tint globally, but keep the baseline's per-clip matching unless your look needs a small global trim. The matching is measured through the look: once your look is settled, run \`G match PARAMS ${q(dir + '/matched.json')}\` once on your look file (it only adds small temp/tint trims to clip_overrides) and read its output. When it notes that the look keeps less chroma than the house look, keep your unmatched file unless a clip visibly stands out (on such looks a second match made grey and white surfaces match worse on a reference reel, although its own spread number dropped). Otherwise render it and keep the matched file if mean_slice_neutral_spread drops, max_slice_neutral_spread does not rise and max_clipped_pct does not rise (and is under 1, or trim the clip as KNOWLEDGE.md section 7 says) with no new clipped flag (a warmer trim can clip the red channel on bright warm surfaces; if it does, lower that clip's stops by about 0.1 in clip_overrides); look at its screens too. A new skin off flag is fine only if match moved that clip's skin_hue_offset_deg by less than about 2 deg (compare the two metrics.json); after a bigger move, set that clip's temp and tint in clip_overrides back to their values before match (the trim read pink or beige clothing, wood or a tan wall as a neutral). Save your best version as ${dir}/final.json, rendered to ${dir}/render_final. Be honest in the self-critique: what is weak, which clips suffer, which texture the look wants.`
}

function candidateList(cands, base) {
  const lines = cands.map(c => `- ${c.id}: params ${c.params_path}, render ${c.render_dir}. Designer's rationale: ${c.rationale}. Self-critique: ${c.self_critique || 'none'}`)
  return `Reference (no look work): baseline ${base.params_path}, render ${base.render_dir || 'not rendered'}.\nCandidates (refer to them by id):\n${lines.join('\n')}`
}

const JUDGES_BEST = [
  { key: 'brand', text: 'LENS: BRAND AND SOCIAL. Creative director for the platform in the brief. Which look stops the scroll, feels on-brand and aspirational, and holds up on a phone at full brightness and in dark mode after platform compression?' },
  { key: 'colorist', text: 'LENS: SENIOR COLORIST, TECHNICAL. Skin, highlight roll-off, black density against crushing, clipping, banding risk, color separation, and shot and panel matching in every slice. Open metrics.json for each candidate. Penalise technical flaws hard.' },
  { key: 'cinema', text: 'LENS: CINEMATOGRAPHER, TASTE. Cohesion, mood, restraint, whether the piece reads as one film, and whether the look will age well rather than feel like a trendy preset.' },
]
const JUDGE_STANDARD = { key: 'combined', text: 'LENS: SENIOR COLORIST AND CREATIVE DIRECTOR IN ONE. Weigh the brief first (mood, platform, brand), then technical quality: skin, highlights, blacks, clipping, matching in every slice (open metrics.json for each candidate).' }

function judgePrompt(j, cands, base) {
  const dir = `${WF}/judge_${j.key}`
  return pre(dir) + `ROLE: JUDGE. ${j.text}

${candidateList(cands, base)}

Look at screens_NN.jpg and sheet_NN.jpg of every candidate (at most 6 images per candidate) and read each metrics.json summary. You may render small experiments in your folder to test a graft, but you do not have to. Score each candidate 0 to 10 using its id, pick a winner, list grafts from other candidates worth adding to the winner, and list must-fix issues.`
}

function finishPrompt(start, context, dir) {
  return pre(dir) + `ROLE: FINISHING COLORIST. ${context}

Start from ${start} (copy it with \`G merge ${q(start)} <your overlay> OUT\`, never edit it in place). Graft the endorsed ideas and fix every must-fix without losing what made the look win. If you changed the look section, run \`G match PARAMS ${q(dir + '/matched.json')}\` once on your file afterwards and read its output: when it notes that the look keeps less chroma than the house look, keep your unmatched file unless a clip visibly stands out; otherwise render it and keep the matched file if mean_slice_neutral_spread drops, max_slice_neutral_spread does not rise and max_clipped_pct does not rise (and is under 1, or trim the clip as KNOWLEDGE.md section 7 says) with no new clipped flag (a warmer trim can clip the red channel on bright warm surfaces; if it does, lower that clip's stops by about 0.1 in clip_overrides); look at its screens too. A new skin off flag is fine only if match moved that clip's skin_hue_offset_deg by less than about 2 deg (compare the two metrics.json); after a bigger move, set that clip's temp and tint in clip_overrides back to their values before match (the trim read pink or beige clothing, wood or a tan wall as a neutral) (matching is measured through the look). This goes to the client: every slice must hold up. Save ${dir}/final.json, render it to ${dir}/render_final and check the exit criteria. Return the path, what you changed and why, and the texture the look would want (added by hand later).`
}

const REVIEWERS_BEST = [
  { key: 'technical', text: 'LENS: TECHNICAL QC. Find anything wrong: clipping, crushed blacks, skin off its natural band, casts, a shot or panel that does not match its neighbours in any slice, flicker inside a clip (strips), hue-band artefacts on saturated colors, foliage or practical lights, banding risk in smooth gradients. Verdict "fix" for anything a paying client would notice.' },
  { key: 'taste', text: 'LENS: CLIENT AND CREATIVE DIRECTOR. Find reasons this is not the best it can be for the brief: dull, over-processed, unflattering skin, wrong mood, inconsistent feel. It must be clearly better than the baseline. Verdict "fix" only for issues that genuinely matter.' },
]
const REVIEWER_STANDARD = { key: 'combined', text: 'LENS: TECHNICAL QC AND CLIENT TASTE IN ONE. First anything a paying client would notice (clipping, crushed blacks, skin off its natural band, casts, mismatched shots or panels in any slice, flicker, banding risk), then whether it fits the brief and is clearly better than the baseline. Verdict "fix" only for issues that genuinely matter.' }

function reviewPrompt(v, cur, round) {
  const dir = `${WF}/review_${v.key}_r${round}`
  return pre(dir) + `ROLE: ADVERSARIAL REVIEWER. ${v.text}

Final grade: params ${cur.params_path}, render ${cur.render_dir}. Finisher notes: ${cur.summary}
Look at every screens_NN.jpg and the sheet, read metrics.json, and render experiments in your folder to prove that a fix works. Give concrete parameter-level fixes (keys and values) for anything blocking.`
}

function fixPrompt(cur, blocking, minor, round) {
  const dir = `${WF}/final_r${round}`
  return pre(dir) + `ROLE: FINISHING COLORIST, FIX PASS ${round}. Current final: params ${cur.params_path}, render ${cur.render_dir}.
Blocking issues:
${JSON.stringify(blocking, null, 1)}
Minor notes: ${JSON.stringify(minor)}

Fix every blocking issue without breaking anything else, then re-check the exit criteria yourself (nobody reviews after you${TIER === 'best' ? ' except one more review round' : ''}). Save ${dir}/final.json, render it to ${dir}/render_final.`
}

// ---------------------------------------------------------------- run
const baseRef = { params_path: BASE, render_dir: null, summary: 'auto baseline (grayness balance, half-way exposure, match trims)' }
let baseline = baseRef
let candidates = []
let judgments = []
let tally = {}
let winner = null
let final = null
let verdicts = []

if (RUN !== 'finish') {
  let audits = []
  if (TIER === 'best') {
    phase('Audit')
    audits = (await parallel(AUDITS.map(a => () => agent(auditPrompt(a),
      { label: `audit:${a.key}`, phase: 'Audit', schema: AUDIT_SCHEMA })))).filter(Boolean)
    log(`Audits: ${audits.map(a => `${a.lens} (${(a.findings || []).length} findings)`).join(', ') || 'none returned'}`)
  }

  phase('Baseline')
  const b = await agent(baselinePrompt(audits), { label: 'baseline', phase: 'Baseline', schema: BASELINE_SCHEMA })
  if (b && b.params_path) baseline = b
  else log('Baseline reviewer returned nothing; the looks start from the auto baseline.')
  log(`Baseline: ${baseline.params_path}`)

  phase('Design')
  const dirs = pickDirections()
  const designs = await parallel(dirs.map(d => () => agent(designPrompt(d, baseline),
    { label: `design:${d.id}`, phase: 'Design', schema: DESIGN_SCHEMA })))
  candidates = designs.map((r, i) => r && r.params_path ? {
    id: dirs[i].id, params_path: r.params_path, render_dir: r.render_dir, rationale: r.rationale || '',
    self_critique: r.self_critique || '', texture: r.texture || '',
  } : null).filter(Boolean)
  const missing = dirs.filter(d => !candidates.find(c => c.id === d.id)).map(d => d.id)
  if (missing.length) log(`No candidate from: ${missing.join(', ')}`)
  log(`Candidates: ${candidates.map(c => c.id).join(', ') || 'none'}`)

  if (candidates.length) {
    phase('Judge')
    const judges = TIER === 'best' ? JUDGES_BEST : [JUDGE_STANDARD]
    judgments = (await parallel(judges.map(j => () => agent(judgePrompt(j, candidates, baseline),
      { label: `judge:${j.key}`, phase: 'Judge', schema: JUDGE_SCHEMA })))).filter(Boolean)
    const idOf = x => {
      const n = String(x || '').toLowerCase().replace(/^cand_/, '').trim()
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
    const colorist = judgments.find(j => /colorist/i.test(j.lens || '')) || judgments[0]
    const cw = colorist ? idOf(colorist.winner) : null
    winner = top.length > 1 && cw && top.includes(cw) ? cw : top[0]
    log(`Judge totals: ${JSON.stringify(tally)}; winner ${winner}`)
  }
}

const winCand = candidates.find(c => c.id === winner) || candidates[0] || null

if (RUN === 'design') {
  final = winCand
    ? { params_path: winCand.params_path, render_dir: winCand.render_dir, summary: `Design phase only: judged winner "${winCand.id}", not finished yet. Show the user the candidates, then run phase "finish" with the pick.` }
    : { params_path: baseline.params_path, render_dir: baseline.render_dir, summary: 'Design phase only: no candidate came back, this is the baseline.' }
  return { tier: TIER, phase: RUN, baseline, candidates, judgments, tally, winner, final, verdicts }
}

phase('Finish')
let start, context
if (RUN === 'finish') {
  start = String(A.pick)
  context = `The user looked at the candidates and picked ${start}. The user's notes: ${NOTES || '(none)'}. Keep the character of the chosen look, apply the notes, and fix technical problems.`
} else if (winCand) {
  start = winCand.params_path
  context = `Candidates:\n${JSON.stringify(candidates, null, 1)}\n\nJudges (score totals ${JSON.stringify(tally)}):\n${JSON.stringify(judgments, null, 1)}\n\nThe winner is "${winCand.id}".${NOTES ? ' Notes from the user: ' + NOTES : ''}`
} else {
  start = baseline.params_path
  context = `No look candidate came back, so build a clean, restrained look on the baseline yourself (start from the defaults' look, which is a clean commercial starting point).${NOTES ? ' Notes from the user: ' + NOTES : ''}`
}
const f1 = await agent(finishPrompt(start, context, `${WF}/final_r1`), { label: 'finish:r1', phase: 'Finish', schema: FINAL_SCHEMA })
if (f1 && f1.params_path) final = f1
else {
  log('Finisher returned nothing; the final grade falls back to its starting point.')
  final = { params_path: start, render_dir: winCand && winCand.params_path === start ? winCand.render_dir : null, summary: 'Finisher did not return; this is the unfinished starting point. Review it before applying.' }
}

phase('Review')
const reviewers = TIER === 'best' ? REVIEWERS_BEST : [REVIEWER_STANDARD]
const MAX_REVIEW_ROUNDS = TIER === 'best' ? 2 : 1
const MAX_FIXES = 1
let round = 1
let fixes = 0
let reviewedFinal = false   // do the returned verdicts belong to the returned final?
while (final && final.render_dir) {
  const cur = final
  verdicts = (await parallel(reviewers.map(v => () => agent(reviewPrompt(v, cur, round),
    { label: `review:${v.key}:r${round}`, phase: 'Review', schema: REVIEW_SCHEMA })))).filter(Boolean)
  reviewedFinal = true
  const blocking = verdicts.flatMap(v => v.verdict === 'fix' ? (v.blocking || []) : [])
  log(`Review round ${round}: ${verdicts.map(v => `${v.lens}=${v.verdict}`).join(', ') || 'no reviewer returned'} (${blocking.length} blocking)`)
  if (!blocking.length) break
  if (fixes >= MAX_FIXES) {
    log('Blocking issues remain after the fix round; they are in verdicts for the user to decide.')
    break
  }
  const minor = verdicts.flatMap(v => v.minor || [])
  const next = await agent(fixPrompt(cur, blocking, minor, round + 1), { label: `fix:r${round + 1}`, phase: 'Finish', schema: FINAL_SCHEMA })
  fixes++
  if (next && next.params_path) { final = next; reviewedFinal = false }
  else { log('Fix pass returned nothing; keeping the previous final.'); break }
  if (round >= MAX_REVIEW_ROUNDS) {
    log('The fix pass checked its own work; there is no further review round in this tier.')
    break
  }
  round++
}

const verdicts_note = !verdicts.length
  ? 'no review ran (the final has no render yet): render it and look before applying'
  : reviewedFinal
    ? 'the verdicts are the review of the final grade'
    : 'the verdicts are from the review BEFORE the last fix pass; the fix pass checked its own work and nobody reviewed the final after it, so check the blocking issues against the final render yourself'
return { tier: TIER, phase: RUN, baseline, candidates, judgments, tally, winner, final, verdicts, verdicts_note }
