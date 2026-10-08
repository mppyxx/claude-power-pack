# Color

## The flagged regions, and why they're shaped that way

Detectors flag two hue windows, but only above a saturation gate and only on display-sized text:

| Window | HSL hue | Gate |
|---|---|---|
| Purple / violet | 260–310 | channel spread `max(r,g,b) − min(r,g,b) ≥ 50`, on h1–h3 or text ≥ 20px |
| Cyan | 160–200 | same |

Named literals: `#7c3aed` `#8b5cf6` `#a855f7` `#9333ea` `#7e22ce` `#6d28d9` `#6366f1`
`#764ba2` `#667eea`. The pair `#667eea → #764ba2` is the most-generated gradient in existence.

**Purple is not the problem.** Purple arriving on a page whose brief never mentioned purple is the
problem. If the subject is a grape cooperative, use purple and use it well.

The cream rule shows the real logic. It fires only on:

```
min(r,g,b) ≥ 209   AND   r ≥ g ≥ b   AND   6 ≤ (r − b) ≤ 48
```

Pure white passes (warmth < 6). A committed warm ground passes (warmth > 48). Any cool grey passes
(fails the ordering). **Only the timid tint fires** — the one that means "I was told not to use
`#ffffff` and stopped there." `#FDFBF7` fires at warmth exactly 6; `#F7F6F3` clears at 4. Two units
out of 255 separate flagged from clean.

Generalise that: **commitment in either direction passes. Timidity is the failure.**

## Build a ramp, don't pick a hue

The deepest color tell is not which hue — it's that there is no *system*. Generated palettes pick
an accent and use it at one saturation. Designed palettes have ramps with structure.

Work in a perceptual space (OKLCH, or HCT) rather than HSL. In HSL, equal lightness values look
wildly unequal across hues — `hsl(60 100% 50%)` and `hsl(240 100% 50%)` are nowhere near the same
brightness. OKLCH's L is perceptually uniform, so a ramp built in it stays even.

Ramp construction:

1. **Lightness is the spine.** Pick your stops first, evenly spaced perceptually, not by eye.
2. **Reduce chroma at the extremes.** Maximum chroma belongs in the mid-range. Near-white and
   near-black cannot hold saturation without turning muddy or neon.
3. **Shift hue across lightness.** This is what separates a designed ramp from a tint/shade of one
   swatch. Warm hues drift toward yellow as they lighten and toward red as they darken; cool hues
   drift the opposite way. Even 5–10° of drift reads as considered.
4. **Tint the neutrals toward the accent.** A grey carrying 2–4% of the accent's chroma binds the
   palette. A pure `#808080` reads as unconsidered.

Reference systems that do this explicitly: Material 3's HCT tonal palettes, Radix Colors' 12-step
scales, IBM Carbon's token ramps.

## Contrast is a palette decision, not an audit finding

Verified ratios, white text on Tailwind `-500` fills:

| Fill | Ratio | AA body (4.5:1) |
|---|---|---|
| `indigo-500` | 4.47 | fail |
| `violet-500` | 4.23 | fail |
| `red-500` | 3.76 | fail |
| `blue-500` | 3.68 | fail |
| `green-500` | 2.28 | fail |

Every one fails. Use the `-600`/`-700` step for filled buttons carrying white text, or darken your
own accent until it clears.

**Large text is 24px, or 18.67px bold** — 18pt and 14pt at 96dpi. Several published anti-slop
skills state 18px, which lets 18–23.99px text pass at 3:1 where AA requires 4.5:1. Don't inherit
that bug.

Non-text contrast (borders of inputs, icon-only controls, focus indicators) needs 3:1 against
adjacent colors. This is the most commonly missed requirement in generated UI.

## Gradients

- **Gradient text** (`background-clip: text` over a gradient) is decorative, never meaningful, and
  it degrades legibility across the ramp. Use a solid color; get emphasis from weight or size.
- **A gradient with no light source** is the underlying error. A radially symmetric blurred blob
  behind a hero implies illumination from nowhere, and then `overflow-hidden` slices its soft edge
  dead flat at the section boundary, which no light does.
- If you want atmosphere, anchor it: let it emanate from an actual element, let it bleed past the
  container, or build one continuous multi-point surface across the whole region rather than two
  or three discrete circles that cross-fade into a muddy band where they overlap.

## Shadows are occlusion, not emission

A colored glow is the single clearest optical error in generated UI, and it's worth stating why
rather than just banning it: **a shadow is the absence of light.** A colored halo is the object
*emitting* light. Objects don't do that, so the eye rejects it even when the viewer can't name the
reason.

The detector encodes this as: chroma ≥ 30 AND blur > 4px AND (zero x/y offset on any background, OR
any chromatic blurred shadow on a background below 0.1 relative luminance). Achromatic zero-offset
shadows stay legal — that's ambient occlusion, which is real.

If a colored glow is genuinely wanted (a neon sign, a screen, a flame — something that actually
emits), keep it and make it emphatic. Just make sure the thing glowing is a light source.

## Dark mode

Dark mode is not an inversion, and it is not `#0a0a0a` with white text.

- **Build an elevated surface system.** Real dark UI separates layers by lightness steps:
  a ground, a raised surface, a further-raised surface. Pure black gives you nowhere to go —
  every layer above the ground has to be lighter, and you've spent your floor.
- **Shadows barely work on dark grounds.** Elevation reads through surface lightness instead. This
  is the main structural difference from light mode, and it's why a naive inversion looks flat.
- **Reduce text weight slightly.** Light-on-dark blooms optically; the same weight reads heavier.
- **Desaturate accents.** A hue at full chroma on a dark ground vibrates. Pull chroma down and
  raise lightness.
- Set `color-scheme: dark` on the root so form controls, scrollbars, and the caret follow.

## Theming that actually works

If the page can render in a viewer-controlled theme, note that "system" is a third state, not a
default branch. An explicit choice stamps an attribute on the root; the system setting stamps
nothing, so only `prefers-color-scheme` separates light from dark there.

The failure this produces is specific and common: a color whose *only* definition sits inside a
`@media (prefers-color-scheme: dark)` or `[data-theme]` block never applies in the unstamped state,
and the page renders one theme's text on the other theme's ground.

Structure it token-level:

1. Bare `:root` defines the **complete** palette.
2. `@media (prefers-color-scheme: dark)` redefines **only tokens**, guarded so an explicit light
   choice still wins.
3. `[data-theme="dark"]` redefines the same tokens again so the toggle wins both directions.
4. Style components through tokens, never with a literal inside a theme block.
5. Give `body` an explicit background from a token — a transparent body borrows whatever is behind
   it.
