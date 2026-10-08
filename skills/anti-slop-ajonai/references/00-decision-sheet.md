# The decision sheet

Fill this before writing markup. It exists because the only intervention with measured support is
front-loading constraints into the first generation — correcting afterwards underperforms starting
from a stronger instruction, and the generic result is the *mode* of the distribution, so
regenerating and raising temperature do not escape it.

Each line below names an axis a brief typically leaves open. An open axis inherits a default.

---

## SUBJECT

One sentence: what this is, who it is for, its single job.

If the brief doesn't pin it, pin it yourself and say so. "A landing page" is not a subject.
"A booking page for a two-chair barbershop in a town of 4,000, used mostly on phones by people
standing outside" is a subject — and it already implies large tap targets, a phone number above
the fold, and no logo cloud.

**Test:** could this sentence describe a different product in the same category? Then it isn't
pinned yet.

## GROUND

The subject's own world: its materials, instruments, artifacts, vernacular, and constraints.

This is where non-generic choices actually come from. A skill or a mood board gives you the same
answers as everyone else; the subject gives you answers nobody else has. A seed company has
germination rates, seasons, soil, packet dimensions, and Latin binomials. A mastering studio has
LUFS, VU ballistics, rack units, and patch bays. A hospice has time, quiet, family, and paperwork.

Write 3–6 concrete nouns from the subject's world. You will use at least one as the signature.

## PALETTE

4–6 hex values, each with a named role. Also state the neutral's hue bias.

- Roles at minimum: ground, surface, primary ink, secondary ink, rule/divider, accent.
- **Name the neutral's bias.** A pure mid-grey reads as unconsidered. Warm greys read organic and
  approachable; cool greys read technical and precise. Pick one on purpose and say which.
- Semantic colors (success/warning/danger) are separate from the accent and do not count as it.
- Verify contrast at this step, not in a later audit pass. See `01-color.md`.

**Test:** if someone could guess your palette from the industry alone, it's the first reflex.
If they could guess it from industry-plus-your-anti-reference ("fintech but not navy-and-gold →
terminal green on black"), it's the second reflex. Both are inherited. Go again.

## TYPE

At least two roles, named faces, and one sentence of justification per face.

- **Display** — carries personality. Used with restraint.
- **Text** — carries legibility. Long-form comfort is the only criterion.
- **Utility** (optional) — captions, data, code. Often a mono.

The justification matters more than the face. "Plex because the subject is engineering
documentation and Plex is the type system of IBM's own technical corpus" is a decision. "Plex
because it looks clean" is a preference, and preferences converge.

**Test:** would this pairing appear on a different project of yours this month? Then it's a habit,
not a choice.

## SPACE

**Two registers, with different numbers.** This is the single most common structural failure.

- **Intra-component**: gaps inside a card, between a label and its input, icon to text.
- **Section rhythm**: the vertical beat between page regions.

One value doing both jobs asserts that everything on the page has equal weight, which is a
semantic claim, not a neutral aesthetic. Proximity is meaning: elements in the same spacing
pattern are read as related items of equal importance.

Write the actual numbers. See `03-space-surface.md` for scale construction.

## SHAPE

- **One radius value** for the primary surface, plus how it nests. Pick a number and enforce
  consistency against *that* number rather than against any canon — published rulesets disagree
  wildly here (0, 8–12, 12–16, and 32px are all prescribed by someone), which tells you there is
  no derivation behind any of them.
- **One elevation policy.** Either separate surfaces by tonal step *or* by shadow. Committing to
  both on the same element is a named defect and is physically contradictory: a hairline says flat,
  a wide blur says floating.

## MOTION

A count of animated moments and a duration band — or the word `none`.

`none` is a legitimate and often correct answer. High-frequency interfaces (command palettes,
keyboard-driven tools) are better with no transition at all. Write the number down so that motion
is a budget rather than an accumulation.

See `05-motion.md`.

## SIGNATURE

The one element this page is remembered by. One.

It should come from GROUND — the subject's own world — not from a catalogue of effects. Everything
around it stays quiet. Boldness distributed evenly across a page reads as generated; boldness
spent in one place reads as art direction.

Examples of the shape of a good signature: a real specimen photographed on the subject's own
equipment; a live calculation the visitor can drive; a data view that only this subject could
produce; a typographic treatment of the subject's actual vocabulary; a single physical material
rendered honestly.

Not signatures: a gradient, a blur, a hover lift, an animated counter. Those are effects, and
effects are available to everyone.

---

## The critique pass

Before writing code, reread the sheet and mark every line that would be identical for a different
subject in the same category. Those lines are inherited. Revise them, and state what you changed
and why.

Then check the second-order trap. Avoiding the first reflex often lands on the second: the
"tasteful" alternative that everyone reaches for once they know the obvious one is burned. If your
sheet reads as "the sophisticated version of the obvious answer," it is still a lookup, not a
derivation.

## Worked example

**Brief:** "a landing page for our AI meeting notes tool"

**Inherited sheet** (what the mode produces — recognise it and discard):

```
SUBJECT    an AI meeting notes SaaS for teams
GROUND     productivity, speed, focus
PALETTE    indigo accent on white, slate neutrals
TYPE       Inter everywhere
SPACE      24px everywhere, py-24 sections
SHAPE      rounded-2xl, border + shadow
MOTION     fade-up on every section
SIGNATURE  gradient headline
```

Every line is answerable without knowing anything about the product.

**Derived sheet:**

```
SUBJECT    a notetaker for clinical research coordinators running consent visits,
           who cannot look at a screen during the visit and must produce an
           auditable record afterwards
GROUND     consent forms, protocol numbers, timestamps, initials boxes, carbon
           copies, the 30-day audit window, regulator-facing paper trails
PALETTE    ground #FCFCFA (warm-neutral, paper bias); surface #FFFFFF;
           ink #1A1916; ink-2 #57544C; rule #DEDAD0;
           accent #7A5C1E (archival ink brown — the subject's own material)
           danger #8C2F1E reserved strictly for audit-integrity failures
TYPE       display: a legal/document serif at modest size, because the artifact
           this replaces is a printed form and the page should feel adjacent to it
           text: a humanist sans for screen comfort
           utility: mono for protocol IDs and timestamps, tabular-nums always
SPACE      intra 6/10/16px; section rhythm 72px, with one 120px break before the
           audit-trail section to mark the change of register
SHAPE      radius 4px only — a form, not a card UI. Elevation by rule and tonal
           step, never shadow.
MOTION     two moments: the recording indicator, and row insertion in the
           transcript. 120-180ms. Everything else static.
SIGNATURE  a real transcript that types itself into a form's initials-and-date
           boxes, showing the record being produced rather than describing it
```

The second sheet could not have been written for a different product. That is the whole test.
Note that it also produces a *warm* ground and a serif — the same surface-level choices the
"cream and serif" cliché produces — but arrived at from the subject's materials, with a specific
paper-bias neutral and a 4px form radius that the cliché would never yield. Derivation can land
near a common answer; that is fine. Lookup that lands there is not.
