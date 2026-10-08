#!/usr/bin/env node
/**
 * deliberate — static checker for inherited-default frontend patterns.
 *
 * Reads source, never renders. Every rule is a text-decidable check, and each one
 * is tuned so that a committed choice in either direction passes: only the
 * un-decided middle fires. Rules with real false-positive rates are `advisory`
 * and never fail a run without --strict.
 *
 * Usage:
 *   node check.mjs <paths...> [--json] [--strict] [--only=cat,cat] [--list-rules]
 *
 * Suppress a checked-and-accepted finding on the line above or the same line:
 *   // deliberate-ignore rule-id
 *   <!-- deliberate-ignore rule-id -->
 */

import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, extname, relative } from 'node:path';

const EXT = new Set(['.html', '.htm', '.jsx', '.tsx', '.js', '.ts', '.mjs',
                     '.vue', '.svelte', '.astro', '.css', '.scss', '.sass']);
const SKIP = new Set(['node_modules', '.git', 'dist', 'build', '.next', 'out',
                      'coverage', 'vendor', '.svelte-kit', '.astro', '.venv']);

/* ---------- color helpers ---------- */
const hex2rgb = h => {
  h = h.replace('#', '');
  if (h.length === 3) h = h.split('').map(c => c + c).join('');
  return [0, 2, 4].map(i => parseInt(h.slice(i, i + 2), 16));
};
const chroma = rgb => Math.max(...rgb) - Math.min(...rgb);
const isCream = rgb => {
  const [r, g, b] = rgb;
  return Math.min(r, g, b) >= 209 && r >= g && g >= b && (r - b) >= 6 && (r - b) <= 48;
};
const srgb = c => { c /= 255; return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; };
const lum = rgb => 0.2126 * srgb(rgb[0]) + 0.7152 * srgb(rgb[1]) + 0.0722 * srgb(rgb[2]);
const ratio = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + .05) / (y + .05); };

/* ---------- literal tables ---------- */
const AI_HEX = ['7c3aed','8b5cf6','a855f7','9333ea','7e22ce','6d28d9','6366f1','764ba2','667eea'];
const CHART_HEX = { '8884d8':'Recharts series 1','82ca9d':'Recharts series 2','1f77b4':'matplotlib C0','636efa':'Plotly colorway 1','ef553b':'Plotly colorway 2' };
const FRAMEWORK_HEX = { '0d6efd':'Bootstrap 5 primary','1976d2':'Material UI primary','319795':'Chakra teal-500','1890ff':'Ant Design v4','1677ff':'Ant Design v5','00d1b2':'Bulma primary','646cff':'Vite starter' };
const OVERUSED_FONTS = ['inter','roboto','open sans','lato','montserrat','fraunces','instrument sans','instrument serif','geist','geist sans','geist mono','mona sans','plus jakarta sans','space grotesk','recoleta'];
// White-on-500 contrast, precomputed from Tailwind's palette.
const FILL_500 = { indigo:4.47, violet:4.23, red:3.76, blue:3.68, green:2.28, emerald:3.17, cyan:2.36, teal:2.79, purple:4.99, fuchsia:4.10, pink:4.05, rose:4.05, orange:2.85, amber:2.15, yellow:1.87, lime:2.06, sky:2.85 };
const PLACEHOLDER_HOSTS = ['placehold.co','via.placeholder.com','placeholder.com','source.unsplash.com/random','i.pravatar.cc','api.dicebear.com','github.com/shadcn.png','picsum.photos/200','loremflickr.com'];
const INVENTED_BRANDS = ['acme','techflow','cloudbase','datasync','innovatech','smartflow','cloudly','nexustech'];
const TW_SIZE = { 'text-xs':12,'text-sm':14,'text-base':16,'text-lg':18,'text-xl':20,'text-2xl':24,'text-3xl':30,'text-4xl':36,'text-5xl':48,'text-6xl':60,'text-7xl':72,'text-8xl':96,'text-9xl':128 };
const DISPLAY_SIZES = ['text-5xl','text-6xl','text-7xl','text-8xl','text-9xl'];

/* ---------- rule registry ---------- */
const RULES = [];
const rule = (id, category, severity, why, fix) => { const r = { id, category, severity, why, fix }; RULES.push(r); return r; };

// color
rule('ai-palette-hex','color','warn','A canonical generated-palette literal. The gradient pair #667eea to #764ba2 is the most-generated in existence.','Derive the accent from the subject. If purple is right here, use a hue you chose and built a ramp for.');
rule('ai-palette-utility','color','advisory','Purple/violet-to-blue gradient utilities on display type is the signature combination.','Solid color; get emphasis from weight or size.');
rule('gradient-text','color','warn','background-clip:text over a gradient is decorative, never meaningful, and legibility varies across the ramp.','Solid color. Emphasis via weight or size.');
rule('cream-ground','color','advisory','A timid warm tint on the page ground. Pure white passes and a committed warm color passes; only the undecided middle between them fires.','Commit: true white, a cool neutral, or a warm ground with r-b well past 48.');
rule('colored-glow','color','warn','A shadow is occlusion; a colored halo reads as the object emitting light, which objects do not do.','Neutral elevation with a real y-offset, or keep the glow only if the element genuinely is a light source.');
rule('contrast-500-white','color','error','White text on a Tailwind -500 fill fails the 4.5:1 AA body minimum.','Use the -600 or -700 step, or darken your own accent.');
rule('chart-default-color','assets','warn','An untouched chart library default — the purest evidence a value was never chosen.','Override the series colors from your palette tokens.');
rule('framework-default-color','assets','advisory','A starter-template default color.','Theme it, or confirm this is intentional brand use.');
// type
rule('overused-font','type','advisory','A monoculture face. The rule is share-and-context, not the name, so this needs your judgement: is it doing the display work of a page whose brief never asked for it?','A system stack for body is fine. Pick a display face derived from the subject.');
rule('leading-tight-display','type','warn','Tailwind v4 sets display line-height to 1, but leading-tight is 1.25 — so this WIDENS display leading versus the default.','Set display leading explicitly, around 1.05-1.15.');
rule('tracking-crushed','type','advisory','Tracking at or past -0.05em costs legibility. Note this is exactly Tailwind tracking-tighter, so the framework default lands on the trigger.','Tighten optically at the shipping size, around -0.02em.');
rule('flat-type-scale','type','warn','Many type sizes spanning a narrow range. The failure is too many steps that all look alike, not too few.','Fewer steps, more contrast. Span more than 2x, and use weight and color for hierarchy.');
// space / surface
rule('border-plus-shadow','space','warn','A hairline says flat and defined; a wide blur says floating. Committing to both on one element is physically contradictory.','Pick one: a defined edge, or elevation. In dense UI prefer a tonal surface step.');
rule('monotonous-rhythm','space','warn','The same section padding repeated down the page asserts that every region has equal weight.','Vary the rhythm. Give at least one section a different value to mark a change of register.');
rule('padding-conflict','meta','error','Conflicting utilities in one class string. Which wins is decided by emitted-stylesheet order, not by the order written, so the author could not have predicted or checked the result.','Remove one.');
rule('display-conflict','meta','error','Two display/position utilities on one element. Same reasoning as above.','Remove one.');
rule('size-conflict','meta','error','Two font-size utilities on one element.','Remove one.');
rule('invalid-class','meta','error','This utility does not exist and silently does nothing. A value that cannot render reached production because nothing rendered it.','Use a real value from the scale.');
// states
rule('no-active-state','states','warn','This file styles hover but never :active, so a press has no visual receipt. Rated the strongest single item in the audit.','Add an :active state; respond on pointer-down, not on release.');
rule('outline-none','states','error','outline removed with no focus replacement in this file. This deletes a working default and strands keyboard users.','Add :focus-visible with a 2px ring at 3:1 contrast.');
rule('transition-all','motion','warn','transition-all animates properties you did not choose, including layout properties.','List the properties explicitly.');
rule('layout-animation','motion','warn','Animating a layout-driving property forces layout every frame.','Animate transform and opacity. For height use grid-template-rows.');
rule('div-onclick','states','warn','A click handler on a non-interactive element has no keyboard access, no focus, and no role.','Use <button> for actions and <a> for navigation.');
rule('redundant-aria','states','advisory','ARIA that duplicates a native role is a no-op — it signals accessibility without adding any.','Remove it; the native element already carries the role.');
// motion
rule('decorative-motion','motion','advisory','Motion applied because a page should feel alive rather than because something changed.','Give each animation a named purpose, or delete it.');
rule('material-default-ease','motion','advisory','cubic-bezier(0.4, 0, 0.2, 1) is the Material default and is the motion equivalent of indigo-500.','Choose a curve and record it in the decision sheet.');
rule('ease-in-ui','motion','warn','ease-in delays movement at the moment the user is watching most closely.','Use a strong ease-out.');
// assets
rule('placeholder-image','assets','error','A placeholder host. These are the most identifiable artifacts in generated work — shadcn.png in particular is one person\'s face on thousands of unrelated products.','Real screenshot, real photography, generated asset, or an honest empty state.');
rule('placeholder-identity','assets','warn','Placeholder contact data presented as real. 555 is the reserved fictional exchange.','Real details, or omit the block.');
rule('invented-brand','assets','advisory','A logo-cloud name built from glued tech morphemes with no domain behind it.','Real customers, or drop the section.');
rule('round-stat','assets','advisory','A stat band where every figure is suspiciously round.','Real numbers with real precision, or remove the band.');
// meta
rule('frozen-copyright','meta','warn','A copyright year earlier than now. The cheapest check available, and it dates the generator rather than the site.','Render the year, or update it.');
rule('missing-lang','meta','warn','<html> has no lang attribute. Screen readers and translation need it.','Add lang.');
rule('dead-legal-link','meta','warn','A legal link pointing at "#". Full design polish with no operational surface is a composite signature.','Real URL, or remove the link.');
rule('em-dash-saturation','meta','advisory','Em-dash saturation. Fires only on two gates together: 8 or more, at roughly one per 500 characters. Do NOT "fix" this by using hyphens where en dashes are correct.','Vary sentence construction. Keep punctuation typographically correct.');

const byId = Object.fromEntries(RULES.map(r => [r.id, r]));

/* ---------- scanning ---------- */
const findings = [];
const add = (file, line, id, detail) => {
  const r = byId[id];
  findings.push({ file, line, rule: id, category: r.category, severity: r.severity, detail, why: r.why, fix: r.fix });
};

const suppressed = (lines, i, id) => {
  const here = lines[i] || '', prev = lines[i - 1] || '';
  const re = new RegExp(`deliberate-ignore\\s+([\\w,\\s-]*\\b${id}\\b|\\*)`);
  return re.test(here) || re.test(prev);
};

const classStrings = line => {
  const out = [];
  const re = /(?:class|className)\s*=\s*(?:"([^"]*)"|'([^']*)'|\{`([^`]*)`\})/g;
  let m; while ((m = re.exec(line))) out.push(m[1] ?? m[2] ?? m[3] ?? '');
  return out;
};
// group tokens by responsive/state prefix so md:flex never conflicts with block
const groupTokens = cls => {
  const g = {};
  for (const t of cls.split(/\s+/).filter(Boolean)) {
    const i = t.lastIndexOf(':');
    const pre = i === -1 ? '' : t.slice(0, i);
    (g[pre] ||= []).push(i === -1 ? t : t.slice(i + 1));
  }
  return g;
};

function scanFile(path, root) {
  let src; try { src = readFileSync(path, 'utf8'); } catch { return; }
  const rel = relative(root, path) || path;
  const lines = src.split('\n');
  const ext = extname(path);
  const isStyle = ['.css', '.scss', '.sass'].includes(ext);
  const lower = src.toLowerCase();
  const F = (i, id, detail) => { if (!suppressed(lines, i, id)) add(rel, i + 1, id, detail); };

  // ---- file-level state rules ----
  const hasHover = /hover:|:hover/.test(src);
  const hasActive = /active:|:active/.test(src);
  const hasFocusVis = /focus-visible|:focus-visible/.test(src);
  if (hasHover && !hasActive) {
    const i = lines.findIndex(l => /hover:|:hover/.test(l));
    F(i < 0 ? 0 : i, 'no-active-state', 'file styles :hover but never :active');
  }

  // ---- file-level type scale ----
  const sizes = new Set();
  for (const l of lines) for (const k of Object.keys(TW_SIZE)) {
    if (new RegExp(`(?:^|[\\s"'\`:])${k}(?![\\w-])`).test(l)) sizes.add(TW_SIZE[k]);
  }
  if (sizes.size >= 3) {
    const a = [...sizes].sort((x, y) => x - y);
    const r = a[a.length - 1] / a[0];
    if (r < 2.0) {
      const i = lines.findIndex(l => /text-(xs|sm|base|lg|xl|\dxl)/.test(l));
      F(i < 0 ? 0 : i, 'flat-type-scale', `${sizes.size} sizes spanning ${a[0]}-${a[a.length-1]}px, ratio ${r.toFixed(2)} (< 2.0)`);
    }
  }

  // ---- file-level section rhythm ----
  const rhythm = {};
  for (const l of lines) for (const m of l.matchAll(/\bpy-(16|20|24|28|32)\b/g)) rhythm[m[1]] = (rhythm[m[1]] || 0) + 1;
  for (const [v, n] of Object.entries(rhythm)) if (n >= 4) {
    const i = lines.findIndex(l => new RegExp(`\\bpy-${v}\\b`).test(l));
    F(i, 'monotonous-rhythm', `py-${v} used ${n}x as section padding`);
  }

  // ---- em-dash saturation: both gates ----
  const dashes = (src.match(/—/g) || []).length;
  const textLen = src.replace(/<[^>]*>/g, '').length;
  if (dashes >= 8 && textLen > 0 && (textLen / dashes) <= 500) {
    const i = lines.findIndex(l => l.includes('—'));
    F(i, 'em-dash-saturation', `${dashes} em-dashes, ~1 per ${Math.round(textLen / dashes)} chars`);
  }

  // ---- html-only ----
  if (['.html', '.htm'].includes(ext)) {
    const hi = lines.findIndex(l => /<html\b/i.test(l));
    if (hi >= 0 && !/\blang\s*=/i.test(lines[hi])) F(hi, 'missing-lang', '<html> without lang');
  }

  /* ---- per-line ---- */
  lines.forEach((line, i) => {
    const L = line.toLowerCase();

    // color literals
    for (const h of AI_HEX) if (L.includes('#' + h)) F(i, 'ai-palette-hex', `#${h}`);
    for (const [h, name] of Object.entries(CHART_HEX)) if (L.includes('#' + h)) F(i, 'chart-default-color', `#${h} (${name})`);
    for (const [h, name] of Object.entries(FRAMEWORK_HEX)) if (L.includes('#' + h)) F(i, 'framework-default-color', `#${h} (${name})`);
    if (/rgba?\(\s*75\s*,\s*192\s*,\s*192/.test(L)) F(i, 'chart-default-color', 'rgba(75,192,192) — Chart.js default');

    // gradient utilities / gradient text
    if (/from-(purple|violet|indigo|fuchsia)-\d+/.test(L) && /to-(blue|purple|violet|indigo|pink|cyan|fuchsia)-\d+/.test(L))
      F(i, 'ai-palette-utility', line.trim().slice(0, 80));
    if ((/bg-clip-text/.test(L) && /text-transparent/.test(L)) ||
        (/background-clip\s*:\s*text/.test(L) && /gradient/.test(L)))
      F(i, 'gradient-text', 'background-clip:text over a gradient');

    // cream ground
    if (/(?:^|[^-\w])(?:body|html|:root)\b/.test(L) || /\bbg-\[#/.test(L)) {
      for (const m of line.matchAll(/#([0-9a-fA-F]{6})\b/g)) {
        const rgb = hex2rgb(m[1]);
        if (isCream(rgb) && /background|bg-\[/.test(L)) F(i, 'cream-ground', `#${m[1]} (warmth ${rgb[0]-rgb[2]})`);
      }
    }

    // colored glow
    for (const m of line.matchAll(/(?:box-shadow|text-shadow)\s*:\s*([^;]+)/gi)) {
      const decl = m[1];
      const nums = [...decl.matchAll(/(-?[\d.]+)px/g)].map(x => parseFloat(x[1]));
      const hexes = [...decl.matchAll(/#([0-9a-fA-F]{6})\b/g)].map(x => hex2rgb(x[1]));
      const rgbs = [...decl.matchAll(/rgba?\(\s*(\d+)[\s,]+(\d+)[\s,]+(\d+)/g)].map(x => [+x[1], +x[2], +x[3]]);
      const cols = [...hexes, ...rgbs];
      if (nums.length >= 3 && nums[2] > 4 && cols.some(c => chroma(c) >= 30)) {
        if (nums[0] === 0 && nums[1] === 0) F(i, 'colored-glow', `zero-offset chromatic shadow, blur ${nums[2]}px`);
      }
    }
    if (/shadow-(purple|violet|indigo|blue|pink|fuchsia|cyan)-\d+\/\d+/.test(L))
      F(i, 'colored-glow', 'accent-hued shadow (emission, not occlusion)');

    // white on -500
    for (const [hue, r] of Object.entries(FILL_500)) {
      if (new RegExp(`bg-${hue}-500\\b`).test(L) && /text-white\b/.test(L) && r < 4.5)
        F(i, 'contrast-500-white', `bg-${hue}-500 + text-white = ${r}:1 (needs 4.5)`);
    }

    // fonts
    for (const m of line.matchAll(/font-family\s*:\s*([^;}\n]+)/gi)) {
      const first = m[1].split(',')[0].trim().replace(/['"]/g, '').toLowerCase();
      if (OVERUSED_FONTS.includes(first)) F(i, 'overused-font', `${first} as first family`);
    }
    for (const m of line.matchAll(/fonts\.googleapis\.com\/css2\?family=([^&"'\s]+)/gi)) {
      const fam = decodeURIComponent(m[1].split(':')[0]).replace(/\+/g, ' ').toLowerCase();
      if (OVERUSED_FONTS.includes(fam)) F(i, 'overused-font', `${fam} via Google Fonts`);
    }

    // display leading inversion + tracking
    if (DISPLAY_SIZES.some(s => new RegExp(`(?:^|[\\s"'\`:])${s}(?![\\w-])`).test(line)) && /\bleading-(tight|snug|normal|relaxed)\b/.test(L))
      F(i, 'leading-tight-display', 'display size with leading-tight (1.25) widens vs the default 1');
    if (/\btracking-tighter\b/.test(L)) F(i, 'tracking-crushed', 'tracking-tighter = -0.05em');
    for (const m of line.matchAll(/letter-spacing\s*:\s*(-[\d.]+)em/gi))
      if (parseFloat(m[1]) <= -0.05) F(i, 'tracking-crushed', `letter-spacing ${m[1]}em`);

    // border + shadow
    for (const cls of classStrings(line)) {
      const g = groupTokens(cls);
      for (const [pre, toks] of Object.entries(g)) {
        const t = new Set(toks);
        const hasBorder = toks.some(x => /^border(-[trblxy])?$/.test(x) || /^border-\d+$/.test(x));
        const bigShadow = toks.some(x => /^shadow-(lg|xl|2xl)$/.test(x));
        if (hasBorder && bigShadow) F(i, 'border-plus-shadow', `${pre ? pre + ':' : ''}border + shadow`);

        // conflicts
        const disp = toks.filter(x => ['flex','block','grid','inline','inline-flex','inline-block','hidden','contents'].includes(x));
        if (disp.length > 1) F(i, 'display-conflict', disp.join(' + '));
        const pos = toks.filter(x => ['absolute','relative','fixed','sticky','static'].includes(x));
        if (pos.length > 1) F(i, 'display-conflict', pos.join(' + '));
        const ts = toks.filter(x => TW_SIZE[x] !== undefined);
        if (ts.length > 1) F(i, 'size-conflict', ts.join(' + '));
        const pAll = toks.some(x => /^p-\d/.test(x));
        const pAxis = toks.filter(x => /^p[xytblr]-\d/.test(x));
        if (pAll && pAxis.length) F(i, 'padding-conflict', `p-* with ${pAxis.join(' ')}`);

        // invalid utilities
        for (const x of toks) {
          if (/^(text|bg|border)-(gray|slate|zinc|neutral|stone)-1000$/.test(x)) F(i, 'invalid-class', x);
          if (/^shadow-([3-9]xl)$/.test(x)) F(i, 'invalid-class', x);
          if (/^rounded-([4-9]xl)$/.test(x)) F(i, 'invalid-class', x);
          if (/^text-(1[0-9])xl$/.test(x)) F(i, 'invalid-class', x);
        }
      }
    }

    // states / motion
    if (/transition-all|transition\s*:\s*all\b/.test(L)) F(i, 'transition-all', 'transition-all');
    if (/(?:transition|animation)[^;]*\b(width|height|top|left|right|bottom|margin|padding)\b/.test(L))
      F(i, 'layout-animation', line.trim().slice(0, 70));
    if (/outline\s*:\s*none|outline-none/.test(L) && !hasFocusVis) F(i, 'outline-none', 'outline removed, no :focus-visible in file');
    if (/<div[^>]*\bonclick/i.test(L) || /<div[^>]*\bonClick=/.test(line)) F(i, 'div-onclick', '<div> with click handler');
    if (/<button[^>]*role\s*=\s*["']button["']/i.test(L)) F(i, 'redundant-aria', 'role="button" on <button>');
    if (/cubic-bezier\(\s*0?\.4\s*,\s*0\s*,\s*0?\.2\s*,\s*1\s*\)/.test(L)) F(i, 'material-default-ease', 'Material default curve');
    if (/timing-function\s*:\s*ease-in\b(?!-out)/.test(L)) F(i, 'ease-in-ui', 'ease-in timing function');
    else if (/(?:^|[\s"'`:])ease-in(?![\w-])/.test(line)) F(i, 'ease-in-ui', 'ease-in utility');
    if (/hover:scale-1\d\d|animate-(bounce|pulse)\b/.test(L)) F(i, 'decorative-motion', line.trim().slice(0, 60).replace(/\s+/g, ' '));

    // assets
    for (const h of PLACEHOLDER_HOSTS) if (L.includes(h)) F(i, 'placeholder-image', h);
    if (/\b\(?555\)?[\s.-]?\d{3}[\s.-]?\d{4}\b/.test(line)) F(i, 'placeholder-identity', '555 reserved fictional number');
    if (/@(example|company|yourcompany|domain)\.com\b/i.test(L)) F(i, 'placeholder-identity', line.match(/[\w.]+@[\w.]+/)?.[0] || 'placeholder email');
    if (/123\s+main\s+st/i.test(L)) F(i, 'placeholder-identity', '123 Main St');
    for (const b of INVENTED_BRANDS) if (new RegExp(`["'>\\s]${b}\\b`, 'i').test(line)) F(i, 'invented-brand', b);
    const ROUND = /(?:^|[\s>"'(])(99\.9+%|100%|24\/7|10,?000\+|1,?000\+|500\+|100\+|1M\+|5x|10x)(?=[\s<"').,!]|$)/;
    { const m = line.match(ROUND); if (m) F(i, 'round-stat', m[1]); }

    // meta
    for (const m of line.matchAll(/(?:©|&copy;|copyright)\s*(\d{4})/gi)) {
      const y = +m[1], now = new Date().getFullYear();
      if (y < now) F(i, 'frozen-copyright', `© ${y}, current year is ${now}`);
    }
    if (/href\s*=\s*["']#["']/.test(line) && /privacy|terms|legal|cookie/i.test(line))
      F(i, 'dead-legal-link', 'legal link href="#"');
  });
}

/* ---------- walk ---------- */
function walk(p, root, out) {
  let st; try { st = statSync(p); } catch { return; }
  if (st.isDirectory()) {
    for (const e of readdirSync(p)) { if (!SKIP.has(e) && !e.startsWith('.')) walk(join(p, e), root, out); }
  } else if (EXT.has(extname(p))) out.push(p);
}

/* ---------- main ---------- */
const argv = process.argv.slice(2);
const flags = new Set(argv.filter(a => a.startsWith('--')));
const only = (argv.find(a => a.startsWith('--only=')) || '').replace('--only=', '').split(',').filter(Boolean);
const paths = argv.filter(a => !a.startsWith('--'));

if (flags.has('--list-rules')) {
  const w = Math.max(...RULES.map(r => r.id.length));
  for (const c of [...new Set(RULES.map(r => r.category))]) {
    console.log(`\n${c}`);
    for (const r of RULES.filter(x => x.category === c))
      console.log(`  ${r.id.padEnd(w)}  ${r.severity.padEnd(9)} ${r.why.slice(0, 76)}`);
  }
  process.exit(0);
}
if (!paths.length) {
  console.error('usage: node check.mjs <paths...> [--json] [--strict] [--only=cat] [--list-rules]');
  process.exit(2);
}

const root = process.cwd();
const files = [];
for (const p of paths) walk(p, root, files);
for (const f of files) scanFile(f, root);

let out = findings;
if (only.length) out = out.filter(f => only.includes(f.category));

if (flags.has('--json')) {
  console.log(JSON.stringify({ files: files.length, findings: out }, null, 2));
} else {
  const C = { error: '\x1b[31m', warn: '\x1b[33m', advisory: '\x1b[90m', dim: '\x1b[90m', b: '\x1b[1m', r: '\x1b[0m' };
  const byFile = {};
  for (const f of out) (byFile[f.file] ||= []).push(f);
  for (const [file, fs_] of Object.entries(byFile)) {
    console.log(`\n${C.b}${file}${C.r}`);
    for (const f of fs_.sort((a, b) => a.line - b.line)) {
      console.log(`  ${C[f.severity]}${String(f.line).padStart(4)}  ${f.severity.padEnd(8)} ${f.rule}${C.r}  ${f.detail}`);
      console.log(`        ${C.dim}${f.why}${C.r}`);
      console.log(`        ${C.dim}→ ${f.fix}${C.r}`);
    }
  }
  const n = s => out.filter(f => f.severity === s).length;
  console.log(`\n${C.b}${files.length} files${C.r}  ${C.error}${n('error')} error${C.r}  ${C.warn}${n('warn')} warn${C.r}  ${C.advisory}${n('advisory')} advisory${C.r}`);
  if (!out.length) console.log(`${C.dim}No inherited defaults detected. This checks source text only — it cannot see rendering, spacing rhythm, or whether your choices suit the subject.${C.r}`);
  else console.log(`${C.dim}Static source checks only. Absence of findings is not evidence of good design.${C.r}`);
}

const strict = flags.has('--strict');
const fail = out.some(f => f.severity === 'error' || (strict && f.severity !== 'advisory') || (strict && f.severity === 'advisory'));
process.exit(fail ? 1 : 0);
