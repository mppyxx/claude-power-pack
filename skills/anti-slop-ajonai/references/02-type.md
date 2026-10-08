# Typography

Visual craft only. Nothing here is about word choice.

## The monocultures, in two generations

Detectors carry a list split by an in-source comment into two waves:

- **Older monoculture:** Inter, Roboto, Open Sans, Lato, Montserrat, Arial, Helvetica
- **Newer monoculture:** Fraunces, Instrument Sans, Instrument Serif, Geist, Mona Sans,
  Plus Jakarta Sans, Space Grotesk, Recoleta

Every face in the second list was, one generation ago, the recommended *escape* from the first.
That is the arms race in one data structure, and it is the reason not to treat this as a ban list.

**The rule is share-and-context, not name.** Detectors fire when the face is *primary* at ≥15% of
text-bearing elements, and they exempt brand faces on their own domains — Geist on `vercel.com`,
Mona Sans on `github.com`, Roboto on Google properties. Same typeface, opposite verdict. The tell
is imitation, not the typeface.

Practical position:

- A system stack for **body** text is fine, and defensible: system faces ship real optical sizing,
  tracking tables, and hinting.
- A monoculture face as the **display voice** of a page whose brief never asked for it is the
  finding.
- Note the contradiction between published rulesets here: one bans Inter/Helvetica/Arial outright,
  another prescribes `system-ui` as correct — and `system-ui` resolves to exactly the banned faces
  on macOS and Windows. The share-and-context rule is the only coherent way through.

Also: `Geist` recurring is not an aesthetic preference at all. It is the shipped Next.js/shadcn
default. A human running `create-next-app` produces it too. Framework defaults are the largest
single source of apparent "AI style."

## Pair for a reason

Two roles minimum: a display face and a text face. Optionally a utility face for data and captions.

Contrast axes that actually work: category (serif + sans), weight (light + heavy), width
(condensed + normal), and personality (geometric + humanist). Low-contrast pairings are often more
sophisticated than high-contrast ones.

The justification is the deliverable, not the face. If your pairing would appear on a different
project this month, it's a habit.

## Scale

`flat-type-hierarchy` fires at **≥3 distinct computed sizes with max/min < 2.0.** Read that
carefully — the failure is *many sizes spanning too narrow a range*. Generated pages use lots of
steps that all look similar. Designed pages use fewer steps with more contrast.

- Build a modular scale on a ratio (1.2 minor third, 1.25 major third, 1.333 perfect fourth) and
  stay on it. Bigger ratios for editorial, smaller for dense UI.
- Span more than 2× between your smallest and largest.
- **Hierarchy is not size alone.** Weight, color, case, and space are all hierarchy tools, and
  weight in particular adds presence without consuming layout. A page that expresses every level
  through size is why the scale ends up flat.

## Tracking is size-specific

A fixed `letter-spacing` is wrong somewhere by definition:

| Size band | Tracking |
|---|---|
| Display (40px+) | −0.02em, tightening as size grows |
| Body | ~0 |
| Small / caption | slightly positive |
| Uppercase labels | +0.05em to +0.12em, always |

Caps genuinely need tracking — that part of the eyebrow convention is correct typography. The
eyebrow is a tell because it arrives unbidden, not because tracked caps are wrong.

Detectors flag tracking tighter than about −0.05em as crushed. Note that Tailwind's own
`tracking-tighter` is exactly `-0.05em`, so the framework default lands on the trigger. Tighten
optically, by eye, at the size you're actually shipping.

## Leading

- Body: **1.5–1.7**. Below 1.3 fires as too tight.
- Display: **1.05–1.2**. Large type needs proportionally less.
- Increase for scripts with tall ascenders/descenders; tighten for dense data UI.

**The Tailwind v4 inversion, worth knowing:** `--text-5xl/6xl/7xl--line-height: 1` but
`--leading-tight: 1.25`. So `text-7xl leading-tight` *widens* display leading versus the default.
The model reaches for a utility named "tight" and makes the type looser. Set display leading
explicitly.

One more: italic display words containing descenders (`y g j p q`) clip under `leading-none`.
Use `leading-[1.1]` with a little bottom padding.

## Measure

**45–75 characters**, 65 ideal. This is the most-cited readability rule and the most-broken one in
responsive layouts. Constrain text containers with `max-width: 65ch`, not the page container.

The generated failure is a paragraph inheriting the full grid width, running 140 characters on a
desktop viewport.

## Numerals

Use `font-variant-numeric: tabular-nums` anywhere digits align or change: stat bands, tables,
timers, prices, dashboards. Proportional figures jitter as values update, which is a small tell
that reads as carelessness.

Oldstyle figures (`onum`) sit better in running prose if the face has them.

## Craft details worth the two minutes

- `text-wrap: balance` on headings prevents one-word last lines.
- Curly quotes and apostrophes, real ellipsis, en dash for ranges, em dash for breaks.
  **Do not** adopt the "ban all em dashes" rule — prescribing a hyphen where an en dash is correct
  damages typography to fool a classifier. Set punctuation correctly for the reader.
- Non-breaking spaces between a number and its unit (`10 MB`, `⌘ K`).
- Turn kerning on. Turn ligatures on where the face needs them.
- Don't underline for emphasis; don't combine bold and italic.
- All-caps only under a line, always tracked.
