# The current monoculture

**Snapshot date: August 2026.**

Everything in this file is perishable. It is a record of what the mode looked like at a moment, not
a permanent list. The rest of this skill is written to survive this file going stale.

## Why this file has a date

The defaults rotate, and the rotation is driven by the advice. Documented sequence:

| Generation | The escape | What it became |
|---|---|---|
| 1 | Inter, Roboto, Montserrat, generic SaaS blue | The original monoculture |
| 2 | "Use a warm off-white, an editorial serif, a tracked eyebrow" | Cream grounds, italic serif heroes, and eyebrows are now the top detector rules |
| 3 | "Use hairlines instead of heavy shadows" | Models did both — hairline + wide shadow is a named defect |

There is a third-order effect already visible: one detector encodes **three different stylings** of
the eyebrow, because when the first is flagged models *restyle* it rather than delete it. The
avoidance itself has a signature.

**Implication:** if this skill is followed uniformly, its recommendations become generation 4. The
defence is the decision sheet in `00-decision-sheet.md` — derivation from a specific brief doesn't
converge, because briefs don't converge. A ban list does.

## Current visual defaults (Aug 2026)

**Palette.** Purple/violet at HSL 260–310, cyan at 160–200. The literals `#7c3aed` `#8b5cf6`
`#a855f7` `#9333ea` `#6366f1`, and the gradient `#667eea → #764ba2`. Second-generation: warm cream
grounds near `#F4F1EA`, near-black with a single acid-green or vermilion accent, and broadsheet
layouts with hairline rules and zero radius.

**Type.** Older wave: Inter, Roboto, Open Sans, Lato, Montserrat, Arial, Helvetica. Newer wave:
Fraunces, Instrument Sans/Serif, Geist, Mona Sans, Plus Jakarta Sans, Space Grotesk, Recoleta.
Remember Geist is the Next.js/shadcn default — its recurrence is a framework fact, not a taste.

**Structure.** Sticky translucent header with backdrop blur; pill badge above the hero H1; oversized
centered headline; two CTAs (solid + outline, same size); logo cloud; 3-up feature grid of
icon-in-tinted-rounded-square cards; stat band of round numbers; 3-tier pricing with the middle one
scaled up and labelled "Most Popular"; FAQ accordion; final centered CTA; 4-column footer.

**Surface.** `rounded-2xl` on everything regardless of element size; border and shadow together;
Tailwind's default shadow scale applied uniformly; `py-20`/`py-24` on every section.

**Effects.** Gradient text via `background-clip`; blurred gradient orbs behind the hero; colored
glow shadows in the accent's own hue; glassmorphism cards; decorative grid backgrounds;
`hover:scale-105`; `transition-all duration-300`; fade-up on every section.

**Assets.** Lucide at mixed rendered stroke widths; placeholder image hosts; `shadcn.png` avatars;
untouched chart library colors.

## Named rule IDs

Useful when reading detector output or other rulesets. Roughly ordered by how often they appear:

`overused-font` · `ai-color-palette` · `cream-palette` · `gradient-text` · `dark-glow` ·
`radial-halo` · `radial-spotlight-glow` · `icon-tile-stack` · `hero-eyebrow-chip` ·
`kicker-above-heading` · `numbered-section-labels` · `italic-serif-display` · `oversized-h1` ·
`flat-type-hierarchy` · `extreme-negative-tracking` · `monotonous-spacing` · `nested-cards` ·
`gpt-thin-border-wide-shadow` · `repeating-stripes-gradient` · `codex-grid-background` ·
`side-tab` · `border-accent-on-rounded` · `bounce-easing` · `pulsing-dot` · `blinking-cursor` ·
`marquee` · `image-hover-transform` · `shape-assembled-illustration` · `broken-image`

## Contested — do not present as settled

Sources actively disagree on all of these. Pick a position, write it in the decision sheet, be
consistent, and don't cite any of them as law:

- **Glassmorphism** — four positions, from outright ban to "use it as a functional layer with
  strict rules" (material weight encodes hierarchy; never stack two translucent surfaces).
- **Border + shadow** — a named defect in one lineage, the preferred separation device in another;
  both Material and Carbon ship components using both.
- **Radius** — 0, 8–12, 12–16, and 32px each prescribed as correct, none with a derivation.
- **The eyebrow** — total ban vs a rationed quota.
- **Easing and duration** — three canonical curves, a 2.3× duration spread.
- **System fonts** — banned by one ruleset, prescribed by another that also bans what `system-ui`
  resolves to.
- **`side-tab`** (colored left border) — billed by one source as the most recognisable tell,
  refuted 0–3 elsewhere as a Bootstrap-era alert convention predating LLMs by a decade, and still
  the correct severity affordance in a status region.

## How to refresh this file

Every few months, or when output stops looking like this:

1. Generate five pages from five deliberately unrelated briefs, with no styling guidance.
2. Diff them. Anything identical across all five is the current mode — that is the definition of a
   tell, applied directly.
3. Rewrite the sections above. Update the snapshot date.
4. Check whether last cycle's recommendations appear in the diff. If they do, they've become
   generation N+1, and this skill needs them removed rather than promoted.
