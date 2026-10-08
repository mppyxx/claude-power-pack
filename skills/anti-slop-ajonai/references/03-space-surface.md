# Space, surface, elevation

## Uniform spacing is a semantic error

This is the most important structural point in the skill, and it is not an aesthetic preference.

Proximity carries meaning. Elements arranged in the same spacing pattern are read as related items
of equal weight. So a page that uses one gap value everywhere is *asserting* a flat, equal-weight
structure — and that assertion is almost always false about the content.

The generated signature is a narrow band, typically 16px and 24px, doing every job: gaps inside a
card, gaps between cards, and the rhythm between page sections. Plus `py-20`/`py-24` repeated
identically down the entire page.

**Minimum fix: two registers with different numbers.**

| Register | Job | Typical band |
|---|---|---|
| Intra-component | label→input, icon→text, list rows, card padding | 4–16px |
| Inter-component | between cards, between a heading and its group | 16–32px |
| Section rhythm | between page regions | 64–160px |

IBM Carbon ships a 13-step scale — 2, 4, 8, 12, 16, 24, 32, 40, 48, 64, 80, 96, 160px — precisely
because one band cannot express all three relationships. Note it is not a uniform multiplier: the
steps get further apart as they grow, because the eye judges spacing ratiometrically.

## Vary the section rhythm

Identical `py-24` on every section produces a page with no structure — a stack of equally
important things. Real pages breathe unevenly: a tight cluster, then a large break before a change
of register, then a dense run.

Give at least one section a deliberately different rhythm to mark that something changed. That
single variation does more than any amount of decoration.

## Density is a decision

Generated output is uniformly low-density: large whitespace everywhere, no compression anywhere.
It reads as a template with content dropped in.

Real products vary density by context. A marketing hero is airy; a data table is tight; a settings
page is somewhere between. Compression aids scanning when there's a lot to scan. Decide the density
per region rather than applying one comfortable value globally.

## Headings bind downward

A heading belongs to the content that follows it, so the space *above* a heading must exceed the
space below it. When those are equal — or reversed — every section reads as a caption for the
previous one.

Detectors measure this from rendered rectangles rather than declared margins, because margin
collapsing, flex gaps, and section padding make declared values meaningless. Check it visually.

## Elevation: pick one method

The named defect is **border + wide shadow on the same element**: a hairline (typically ≤1.5px)
co-occurring with a diffuse shadow (blur ≥16px). It is physically contradictory — the hairline says
flat and defined, the blur says floating and soft. Generated output ships both because each was
recommended separately.

Choose one:

**Tonal separation** — surfaces differ by lightness step. Material 3 explicitly reversed its own
earlier guidance here: shadows are no longer applied by default at all elevation levels, and are
reserved for protecting an element against a busy background or encouraging interaction. Surfaces
separate by `surface` / `surface-container-low` / `-high` / `-highest` instead. This is the better
default for dense UI and the only thing that works well in dark mode.

**Shadow** — if you use it, build it properly. A designed shadow is a stack: a tight, dark ambient
layer plus a wider, softer diffuse layer, tinted toward the surface hue rather than pure black.
Tailwind's default `shadow-lg`/`shadow-xl` applied uniformly to everything is the tell, not shadow
itself.

Material's economy principle is worth carrying: the fewer elevation levels in a UI, the more power
each one has.

## Radius

Published rulesets disagree completely — 0, 8–12px, 12–16px, and 32px are each prescribed by
someone as correct, with no derivation offered by any of them. That disagreement is the finding:
there is no universal right answer, so treat radius as a **project constant to be enforced**, not
a value to look up.

Two things that are actually true:

- **Radius should relate to element size.** The same 16px on a 320px card and a 36px button is two
  different visual relationships. Small controls want proportionally less.
- **Nested radii must be concentric.** An inner element inside a padded rounded container needs
  `inner = outer − padding`. Equal inner and outer radii produce a visible gap that reads as
  sloppy even to people who can't name it.

## Separators

You have more options than a 1px line, and generated output uses only that one:

- **Space** — often the correct separator. Try it before drawing anything.
- **Tonal step** — a subtle background change reads as a boundary without a line.
- **Rule** — when you need an explicit edge. One rule, thin, low-contrast.

The specific anti-pattern: `border-t` *and* `border-b` on every row of a long list, producing
doubled 2px lines between rows. Pick one direction, or use space.

## Layout

- Lay out sibling groups with flex/grid and `gap` rather than per-element margins. Margins collapse,
  double, and fight; gaps don't.
- Wide content — tables, code, diagrams — gets `overflow-x: auto` on its own container so the page
  body never scrolls sideways.
- Watch selector specificity when a type-based class (`.section`) and an element-based one (`.cta`)
  both set padding. Cascade collisions silently undo spacing and are hard to see in source.

## Responsive

The generated pattern is a single-breakpoint mindset: `grid-cols-1 md:grid-cols-3` with nothing
between, everything stacking on mobile at the same `py-24` rhythm, producing an endless scroll.

- Mobile is not desktop stacked. Compress the vertical rhythm; it should be a different number.
- Use fluid type (`clamp()`) rather than only breakpoint jumps.
- Minimum touch target 44×44px, with spacing between adjacent targets.
- Check widths *between* your breakpoints. The signature failure is a layout correct at exactly the
  widths named in the class strings and broken between them — 320px, 900px, the gap between 640 and
  768.
- Body text needs at least 16px horizontal padding from the viewport edge, ideally 24–32px.
