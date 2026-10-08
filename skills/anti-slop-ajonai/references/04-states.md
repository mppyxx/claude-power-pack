# Interaction states

**Read this by default.** When a large pool of candidate tells was adversarially audited, this
layer had the highest survival rate of any — higher than color, type, layout, or hero composition.

The reason is worth internalising: a screenshot of a generated page is usually fine. The page falls
apart the moment you interact with it. It is **inert**. Everything below is cheap, and almost none
of it is present in default output.

## The states that are missing

Ordered by signal strength, strongest first.

### `:active` — the click has no receipt

Rated the single strongest item in the audit. Generated UI ships `hover:` and stops. Press a button
and nothing acknowledges the press; the state change only appears when the action completes, which
on a slow network is hundreds of milliseconds of dead interface.

```css
.btn:active { transform: scale(0.97); transition: transform 100ms ease-out; }
```

The principle behind it: **respond on pointer-down, not on release.** Waiting for the click event
to show feedback feels dead. The moment latency appears, the sense of directness falls off a cliff.

Note `hover:` alone is also wrong on touch, where it either never fires or sticks after tap.

### `:focus-visible` — keyboard users are invisible

Two failures, both common:

- No focus style at all.
- `outline: none` with nothing replacing it. This is the more damaging one because it removes a
  working default.

```css
:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
```

Use `:focus-visible`, not `:focus`, so the ring appears for keyboard navigation and not on mouse
click. Use `:focus-within` for compound controls so the whole group signals focus. The indicator
needs 3:1 contrast against adjacent colors.

### Disabled

Generated buttons are either always enabled or disabled with only `opacity-50`, which produces
unreadable text and keeps the hover state firing. Disabled needs: reduced contrast that still
passes non-text minimums, `cursor: not-allowed`, no hover response, and — critically — an
explanation somewhere of *why* it's disabled.

### Loading

No skeletons, no spinners, no pending state on submit. The specific pattern that matters: a submit
button stays enabled until the request actually starts, then shows progress in place. Disabling
before the request begins loses clicks.

Anything over ~300ms needs a visible pending state. Skeletons beat spinners when you know the shape
of what's arriving.

### Empty

The single most-skipped screen in generated applications. Every list, table, search result, and
dashboard has a zero state, and it is the first thing a new user sees. An empty screen is an
invitation to act — it needs a reason and a next step, not a shrug.

### Error

Not just a red border. Error states need: what went wrong, how to fix it, in the interface's voice,
positioned next to the field that failed, with focus moved to the first error on submit. Generated
forms typically validate nothing and display errors nowhere.

Also: `aria-live="polite"` on async updates so the error is announced, not just drawn.

## The six surfaces nobody themes

Called out in the corpus as **"the cheapest signal that a page was built rather than generated."**
These take about twelve lines total and almost no generated page has any of them:

```css
::selection      { background: var(--accent-soft); color: var(--ink); }
:root            { caret-color: var(--accent); }
::-webkit-scrollbar-thumb { background: var(--rule-2); }
:focus-visible   { outline: 2px solid var(--accent); outline-offset: 2px; }
a                { text-underline-offset: 3px; text-decoration-thickness: 1px; }
.tabular         { font-variant-numeric: tabular-nums; }
```

Plus `color-scheme` on the root so native controls, scrollbars, and the caret follow your theme.

## Form craft

The gap between generated forms and real ones is mostly unglamorous correctness:

- `autocomplete` and a meaningful `name` on every input. This is the difference between a form that
  fills in one tap and one that doesn't.
- Correct `type` and `inputmode` — `email`, `tel`, `url`, `number` — so mobile shows the right
  keyboard.
- Labels that are clickable, wired with `for`/`htmlFor`. Checkbox and radio labels share a single
  hit target with the control, with no dead zone between.
- Never block paste. Blocking paste on a password or code field is user-hostile and breaks password
  managers.
- `spellcheck={false}` on emails, usernames, codes.
- Inputs sized to their expected content. A 2-digit field at full container width is a generated
  signature.
- Warn before navigating away from unsaved changes.
- Inputs at 16px minimum on iOS or the viewport zooms on focus.

## Tables and data

- Right-align numbers, left-align text, and use tabular figures so columns don't jitter.
- One thin rule under the header row; drop the rest of the grid. Data creates its own implied grid,
  and full borders are clutter.
- Sticky header on anything long. Row hover on anything clickable.
- An empty state, a loading state, and a "no results match your filter" state that differs from
  "there's nothing here yet."

## Destructive actions

Never immediate. Either a confirmation or — better — an undo window. Confirmations used on
non-destructive actions train people to click through them, which is how the real confirmation gets
dismissed.

## Navigation and URL state

- Filters, tabs, pagination, and expanded panels belong in the URL. If it uses local state,
  consider whether it should be linkable.
- Real links are `<a>`/`<Link>`, so cmd-click and middle-click work. A `<div onClick>` that
  navigates breaks every browser affordance.
- `<button>` for actions, `<a>` for navigation. Not interchangeable.
- Every screen should answer: where am I, where can I go, what's here, how do I get out.

## Accessibility that isn't theatre

Generated output often adds ARIA that signals accessibility without doing anything:
`role="button"` on a real `<button>`, `aria-label="Sign up"` on a button already reading "Sign up",
`tabIndex={0}` on a div, `aria-hidden` on meaningful content. These are no-ops at best.

What actually matters: semantic elements before ARIA, icon-only buttons get a real label,
decorative icons get `aria-hidden="true"`, images get `alt` (or `alt=""` when decorative), heading
levels don't skip, and a skip link exists.

On alt text: the tell isn't absence, it's register. A decorative gradient blob carrying
`alt="A person sitting at a desk with a laptop, representing productivity"` where the correct value
is `alt=""` — and the same 15-word structure on every image. Alt text is a functional label, not a
description of the picture.
