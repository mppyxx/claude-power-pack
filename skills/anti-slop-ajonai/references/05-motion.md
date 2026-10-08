# Motion

## What every independent source agrees on

Three unrelated rulesets converge on exactly four rules. That level of agreement is rare in this
material, so treat these as settled:

1. **Never `ease-in` on UI.** It delays movement at the moment the user is watching most closely.
   Use a strong ease-out: things should start fast and settle.
2. **Never animate layout-driving properties** — `width`, `height`, `top`, `left`, `margin`,
   `padding`, `min-*`, `max-*`. They force layout on every frame. Animate `transform` and `opacity`,
   which the compositor handles. For height, animate `grid-template-rows` instead.
3. **`prefers-reduced-motion` is mandatory** — and a global kill switch is itself a defect.
   Reduced motion means a gentler, non-vestibular equivalent, not the absence of feedback. Replace
   slides and springs with short opacity cross-fades; keep the state change legible.
4. **Every animation needs a named purpose.** Feedback, spatial continuity, state change, or
   directing attention. If it doesn't have one, the fix is deletion.

Also universal: **never `transition: all`.** List properties explicitly. `all` animates things you
didn't intend, including layout properties, and is a reliable marker that nobody chose.

## Where sources disagree — and what that tells you

They do *not* agree on the numbers, and the spread is instructive:

| | Position A | Position B | Position C |
|---|---|---|---|
| Duration ceiling | under 300ms | 700ms default | 500–800ms for one focal moment |
| House ease-out | `cubic-bezier(0.23, 1, 0.32, 1)` | `cubic-bezier(0.16, 1, 0.3, 1)` | `cubic-bezier(0.32, 0.72, 0, 1)` |
| Volume | often zero | motion on every block | one orchestrated sequence |

A 2.3× spread in duration and three different "canonical" curves means these are **conventions, not
physics.** Don't present any of them as law. Pick one, write it in the decision sheet, and be
consistent — consistency is the thing that actually reads as designed.

One negative rule does transfer: don't emit `cubic-bezier(0.4, 0, 0.2, 1)` by reflex. It's
Material's default, it's everywhere, and it's the motion equivalent of `indigo-500`.

## Practical durations

A workable band, offered as a starting point rather than a standard:

| Interaction | Duration |
|---|---|
| Button press / hover | 100–160ms |
| Tooltip, small popover | 125–200ms |
| Dropdown, menu | 150–250ms |
| Modal, drawer, sheet | 200–400ms |
| Page or view transition | 300–500ms |

Smaller distances want shorter durations. A 180ms dropdown feels more responsive than a 400ms one,
and users read the difference as software quality rather than as animation.

## Springs, when motion is gesture-driven

For anything a user can touch and drag, springs beat fixed-duration curves because they are
interruptible and velocity-aware by nature.

Think in two parameters rather than mass/stiffness/damping:

- **Damping ratio** — `1.0` is critically damped, no overshoot. Below 1.0 bounces.
- **Response** — how quickly it reaches target, in seconds. Not a duration; a spring has no fixed
  duration.

| Interaction | Damping | Response |
|---|---|---|
| Move / reposition | 1.0 | 0.4 |
| Rotation | 0.8 | 0.4 |
| Drawer / sheet | 0.8 | 0.3 |

**Default to `damping: 1.0`.** Add bounce only when the gesture itself carried momentum — a flick,
a throw, a drag release. Overshoot on a menu that merely faded in feels wrong; overshoot on a card
you threw feels right.

Momentum projection uses exponential decay, not the physics-textbook `v²/2a`:

```js
// decelerationRate ≈ 0.998 for scroll feel, 0.99 for snappier
const project = (v, d = 0.998) => (v / 1000) * d / (1 - d);
const target  = nearestSnapPoint(currentPosition + project(releaseVelocity));
```

Then hand the release velocity to the spring as its initial velocity, so there's no visible seam
between dragging and animating. That seam is the detail that most separates "fluid" from "fine."

## Interruptibility

The most important principle for gesture UI, and almost never implemented in generated code: every
animation must be grabbable and reversible mid-flight.

- Never lock input during a transition.
- **Always animate from the presentation (current on-screen) value, not the logical target.**
  Starting from the target causes a visible jump on interrupt.
- CSS transitions and `@keyframes` can't be smoothly grabbed and reversed. For anything
  gesture-driven, use a spring library that re-targets from current position and velocity.
- Enter and exit along the same path. A panel that slides in from the right dismisses to the right.
- Anchor transforms to their trigger — set `transform-origin` so a popover scales from the control
  that opened it, not from its own center.

## Decorative motion to skip

Each of these is individually a named tell, and they share a cause: motion applied because a page
should "feel alive" rather than because something changed.

- `hover:scale-105` on cards and buttons
- Auto-scrolling marquees and logo belts — they demand attention they didn't earn and hide half
  their content at any moment
- Pulsing status dots not tied to genuinely live data
- Blinking fake cursors in a hero, simulating typing where no input exists
- Fade-up-on-scroll applied uniformly to every section
- Image scale or rotate on hover
- Bounce and elastic easing — real objects decelerate smoothly

## The failure mode that hides content

Scroll-reveal implemented as `opacity: 0` by default plus a JS class toggle. If the observer never
fires — a JS error, a browser that scroll-restores past the trigger, a bot, a reader with JS off —
the content shipped but never becomes visible.

Make content visible by default and let JS *enhance* its entrance. Never gate existence on script.
