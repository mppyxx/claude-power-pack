# Icons, imagery, charts, data

## The one rule

An untouched default is high-signal evidence because it proves a value was never chosen. That
applies far beyond CSS — most of this file is that same logic pointed at layers people forget to
look at.

## Icons

**The stroke problem.** Lucide ships every glyph at `viewBox="0 0 24 24"` with `stroke-width="2"`,
drawn for 24px. Generated code resizes it with CSS box utilities only. In a shadcn button, the
icon slot forces 16px — and does so with a selector specific enough to override a hand-written
`h-5 w-5`. So one page renders 1.333px strokes in buttons and 2px strokes in feature tiles. Stroke
weight is the loudest signal in an outline set, and it never settles into one hand.

Below 24px the glyph is also being rendered outside the geometry it was drawn to: minimum interior
gaps and corner radii scale down with it, so multi-element glyphs lose separation.

Fixes, best first:

- **Use a set drawn per size.** Heroicons ships four separate cuts (24 outline at 1.5px, 24 solid,
  20 mini, 16 micro — the small ones solid, not stroked). Material Symbols exposes an `opsz` axis.
  IBM Carbon draws 16/20/24/32 as four artworks.
- **Pin the stroke** if staying with one master: lucide's `absoluteStrokeWidth` compensates
  (`strokeWidth × 24 / size`).
- At minimum, use one size everywhere and check it renders at the weight of the text beside it.

**Metaphor fatigue.** Zap for fast, Shield for secure, Sparkles for AI, Rocket for launch,
CheckCircle for everything. Not wrong individually; recognisable as a set because the mapping is
the first one anyone reaches for.

## Decoration

**Blurred gradient orbs.** The canonical form is a 320px circle at 30% alpha with a 64px blur,
absolutely positioned and negative-offset behind a hero, inside `overflow-hidden`. Two problems:
it's radially symmetric, so it's a glow with no light source and no direction; and the clip slices
its soft edge dead flat at the section boundary, which real light never does.

If you want atmosphere: anchor it to an actual element so it reads as emission from something, let
it bleed past the container instead of clipping, or build one continuous multi-point surface rather
than several discrete blobs that cross-fade into a muddy band where they overlap.

**Also on the list:** decorative grid and dot-field backgrounds drawn with hairline gradients on a
fixed pixel cell (reserve those for canvases, maps, blueprints — surfaces where a grid means
something); repeating-gradient stripes; and hero-sized SVG "illustrations" assembled from a pile of
primitive shapes, which read as placeholder clip art. If you can't render the scene with a real
asset, ship no illustration.

## Imagery

The placeholder services are the most identifiable artifacts in generated work:

| Service | What it produces |
|---|---|
| `placehold.co` | A grey rectangle printing its own pixel dimensions |
| `source.unsplash.com/random` | A different photo every load — three unrelated crops in a testimonial row |
| `i.pravatar.cc`, `api.dicebear.com` | Avatar rows with identical framing that read as a stock strip |
| `github.com/shadcn.png` | One recognisable person's face on thousands of unrelated products |

That last one is the single most identifiable artifact in the corpus. It's the shadcn Avatar docs
example, replicated verbatim into shadcn-svelte, shadcn-vue, and downstream.

What to ship instead: real product screenshots in their own window chrome; commissioned or
generated illustration in a consistent house style; real photography you have rights to; or — the
honest option — nothing. An empty state or a plain tonal block beats a stock face.

For avatars specifically, a deterministic monogram tile built from initials and your own color
tokens is better than any face you don't own.

**Never build a fake product UI out of styled `<div>`s** — a fake dashboard, terminal, or task list
in the hero. One corpus calls this the single most recognisable generated-design tell. Use a real
screenshot, a real embedded component, or nothing.

## Charts — the layer nobody audits

Chart libraries ship a first-series color that appears in every tutorial. These are the purest
untouched defaults available:

| Library | Default |
|---|---|
| Recharts | `#8884d8` series 1, `#82ca9d` series 2 |
| Chart.js | `rgba(75, 192, 192, 1)` border, `rgba(75, 192, 192, 0.2)` fill |
| matplotlib | `C0` = `#1f77b4`, `figsize=(6.4, 4.8)`, `dpi=100` |
| Plotly | colorway starting `#636efa`, `#EF553B`; `Plasma` on heatmaps |

Ten seconds in a theme file removes all of them. Nobody spends it.

**Fabricated data has the wrong shape.** Real series are lumpy. Generated ones are drawn:

- A sparkline that's a clean sine or strictly monotonic curve
- A bar chart with no outlier
- A y-axis with tick values but no unit label
- A legend rendered for a single series
- Stat bands where every figure is round: `10,000+`, `99.9%`, `24/7`, `500+`

Give charts the same care as type: an area fill, a faint grid, an emphasised endpoint, real units.

## Other framework defaults

Same logic, other scaffolds. Worth checking because most anti-slop material is Tailwind-only:

| Scaffold | Untouched marker |
|---|---|
| Bootstrap 5 | `#0d6efd` on `.btn-primary` |
| Material UI | `#1976d2` primary, Roboto 400 |
| Chakra | teal `#319795` |
| Ant Design | `#1890ff` (v4) / `#1677ff` (v5) |
| Bulma | `#00d1b2` |
| Vite / CRA | `#646cff` link hover, spinning-logo `App.css` |

Plus: the favicon still `vite.svg` or the stock Next.js icon, and an unmodified starter `README.md`.

## Placeholder identity

Checkable artifacts, not style judgements:

`hello@example.com` · `+1 (555) 123-4567` (555 is the reserved fictional exchange) ·
`123 Main St, San Francisco, CA` · `@yourcompany` · `Company, Inc.`

And invented brands built from two glued tech morphemes with no domain behind them: Acme, TechFlow,
CloudBase, DataSync, Nexus, Vertex. Weaker evidence — pre-launch human sites ship placeholder logo
clouds too — but worth flagging.

## Images, mechanically

- Explicit `width`/`height` (or `aspect-ratio`) so the layout doesn't shift as images load.
- `loading="lazy"` below the fold; `fetchpriority="high"` on the one above it.
- Real `srcset` for anything large.
- A real favicon, an OG image, and a `manifest`. Their absence, alongside full design polish, is
  the "no operational surface" signature.
