# Auditing an interface

Use when reviewing existing UI — yours or someone else's — or self-checking before shipping.

## Calibrate first

Read this before you start flagging things.

When 326 candidate tells were rated by three judges working from deliberately different lenses
(base-rate, forensic, gestalt), only **32 — 9.8% — were unanimously high-signal.** 17.5% were
unanimously noise. Pairwise agreement was 64–72%.

Three judges who all accept the phenomenon is real agree on about one item in ten. **The remaining
two-thirds is contested.** Any confident list of tells, including the ones in this skill, is a
minority of firm items wrapped in a majority of plausible assertion.

The gestalt judge returned less than half as many high ratings as the other two. That is the
syndrome model showing up in data: asked "does this convict on its own?", most individual elements
don't. A rounded card doesn't. Inter doesn't. A blurred sticky header doesn't. What convicts is
fourteen of them arriving together, none of them chosen.

**So: report co-occurrence, not items.** "Fourteen properties on this page are framework defaults"
is a finding. "This uses Inter" is not.

## Say "inherited," not "AI"

Never assert authorship. The honest claim is that a value appears to have been inherited rather
than chosen, and that is both more accurate and more useful — it points at the fix.

This matters practically. The population most likely to be falsely accused is hand-built amateur
sites, and several things people score as AI tells are actually evidence *against* machine
authorship:

- Off-scale magic numbers (`margin-top: 37px`)
- A double space after a period
- Sub-pixel misalignment
- A novelty display face
- Inconsistent margins someone clearly lived with
- A typo, a joke, an opinion that costs the author something

Machines produce clean, on-scale, consistent, impersonal output. Idiosyncrasy is a human signal.
A rubric that counts it as a positive is broken.

## Order of work

Highest yield first. This ordering comes from survival rates under audit, not from convention.

1. **Interact with it.** Tab through it. Click and hold. Submit an empty form. Load it on a slow
   connection. Most findings live here and none are visible in a screenshot.
2. **Check the free axes.** Palette, type pairing, radius, shadow policy, motion budget — are these
   decided or defaulted? Run the checker.
3. **Check spacing registers.** One value doing every job, or two-plus with different numbers?
4. **Check the states.** `:active`, `:focus-visible`, disabled, loading, empty, error.
5. **Check assets.** Placeholder services, chart defaults, favicon, fake identity data.
6. **Then look at composition.** Hero, grid, icons. Lowest survival rate — do this last, and hold
   findings here loosely.

## Countable assertions, not adjectives

The most valuable property of a good rule is that it's countable. "This feels generic" is
unactionable. Convert to a count:

| Instead of | Count |
|---|---|
| "spacing feels monotonous" | distinct gap values in use; how many are section-level vs intra-component |
| "the type hierarchy is flat" | distinct font sizes; ratio of largest to smallest (under 2.0 is the trigger) |
| "too many effects" | elements carrying both a border and a shadow ≥16px blur |
| "the palette is generic" | color literals not traceable to a declared token |
| "it feels inert" | interactive elements lacking `:active`; lacking `:focus-visible` |
| "the icons look off" | distinct rendered stroke widths across the page |
| "eyebrows everywhere" | tracked-uppercase micro-labels sitting directly above a heading |
| "no real content" | images pointing at a placeholder host; invented company names |

## The report

For each finding: **what** (the literal value), **where** (file:line), **why it reads as
inherited** (not "why it's bad"), and **the specific replacement**. A finding without a concrete
replacement is a complaint.

Rank by co-occurrence and by cost-to-fix. Lead with the cluster, not the longest list.

Flag your own uncertainty. If a finding would plausibly appear in good human-designed work — and
most individual findings would — say so. An audit that overstates gets ignored on the second read.

## Self-check before shipping

Fast pass, in order:

- [ ] Every color on screen traces to a declared token
- [ ] Contrast verified — body 4.5:1, large text (**24px / 18.67px bold**) and UI 3:1
- [ ] At least two spacing registers with genuinely different numbers
- [ ] Type scale spans more than 2× and has fewer, more distinct steps
- [ ] Display leading and tracking set explicitly, not inherited from a utility
- [ ] Text measure constrained to 45–75 characters
- [ ] `:active`, `:focus-visible`, disabled, loading, empty, error all exist
- [ ] `::selection`, `caret-color`, scrollbar, focus ring themed
- [ ] `prefers-reduced-motion` honored with a gentler equivalent, not a kill switch
- [ ] No `transition: all`; no animated layout properties
- [ ] One elevation method, not border-and-shadow together
- [ ] Nested radii concentric
- [ ] No placeholder image hosts, no invented companies, no `555` numbers
- [ ] Chart colors overridden
- [ ] Real favicon; `lang` on `<html>`; copyright year not frozen
- [ ] Checked at 320px, 900px, and between the breakpoints — not only at them
- [ ] One signature element, and everything else quieter than it
- [ ] Every value on screen answerable with "because *this* subject"
