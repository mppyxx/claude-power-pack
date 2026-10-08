# Grading knowledge for resolve-colorist

Craft notes for everyone who grades with this skill: the main session and every agent in the workflow. Numbers
refer to grade_lab params. `grade_lab.py LAB defaults` prints every key with its meaning; this file explains how to
use them well. Where a number was measured, it says so; where it is a taste call, treat it as a starting point.

Contents
1. How the grade is built
2. Looks: craft notes
3. Skin across skin types
4. Balance and matching
5. Display rendering
6. Metrics and exit criteria
7. Presets and directions
8. Social delivery
9. Texture: what the user adds by hand

---------------------------------------------------------------------------------------------------------------

## 1. How the grade is built

```
source file -> Resolve's internal 0..1 data (the lab decodes frames the way Resolve does)
  conversion LUT, one per clip, on node 1 (33^3 for Sony S-Log3 / S-Gamut3.Cine, 65^3 for other cameras):
    camera log decode -> camera gamut to linear Rec.709 -> per-clip gains (auto balance, match trims,
    clip_overrides, global trims) -> render.exposure -> display rendering (tone curve, gamut and hue handling)
    -> Rec.709 gamma 2.4 encode
  look LUT, one for all clips, 65^3, on the color group's post-clip node:
    contrast around a pivot, split toning, saturation, hue bands, skin controls, print density,
    per-hue luminance, black and white points
```

What that means for grading decisions:
- Per-shot work lives in `balance`, `clip_overrides` and `render`. The look lives in `look` and is identical on
  every shot. If a look only works after a per-shot fix, make the fix in `clip_overrides`, not in the look.
- Everything is per pixel. Hue bands and the skin controls are baked qualifiers, but windows, tracked or keyframed
  corrections, qualifier keys that need blur or cleanup, grain, glow, halation, sharpening and vignettes cannot be
  baked. Note them for the hand-over instead.
- Params files are partial overrides deep-merged onto the defaults. `"schema": 2` selects the current defaults.
  A file without `schema` was made by the first version of the skill and renders with the old defaults (legacy
  tone curve, gray-world balance), which reproduces old grades exactly.
- The look starts as a clean commercial house look (contrast 0.38 at pivot 0.475, saturation 1.15 with softer
  highlights and shadows, gentle cool-shadow and warm-highlight split, mild print density). It is a starting point,
  not a style.

---------------------------------------------------------------------------------------------------------------

## 2. Looks: craft notes

### What a look is
- A look is one creative transform applied the same way to every shot, after each shot has been balanced and
  matched.
- Judge a look on all shots, never on one hero frame. A good look moves every shot closer to the intent. If it
  helps ten shots and hurts one, scale it back or fix that one shot underneath it.
- Build order that works: exposure and contrast, then balance, then saturation, then hue work, then texture. Judge
  each step after the previous one is right. Small balance errors get exaggerated by every later step, especially
  saturation, so revisit balance whenever saturation goes up.
- Points that several well-known colorists make in their tutorials: balance before saturation; separation comes
  from balance first, then split toning, then hue rotations; subtractive saturation reads as filmic; keep blacks
  down for dark skin; consistency of skin across shots matters more than landing exactly on a line.

### Contrast and pivot
- An 18 % grey card lands at about 0.475 code value after the conversion. With `look.pivot` near 0.47 to 0.48,
  `contrast` spreads tones around grey without changing exposure. (The first version's default pivot 0.43 also
  brightened mids by about 0.016; keep that in mind when comparing with old grades.)
- `black_lift` and `white_point` come last and remap the whole output range
  (out = black_lift + x * (white_point minus black_lift)), so they move grey too: with the defaults 0.012 and
  0.965, grey comes out at 0.465, not 0.475. A higher black_lift or a lower white_point changes mid-tone
  brightness as well as the ends; check grey with `measure`.
- `contrast` 0.2 to 0.25 reads soft or airy, 0.3 to 0.4 commercial or filmic, 0.45 to 0.55 moody. For reference,
  Kodak 2383 measures about 0.20 code values per stop at grey, Fuji 3513 about 0.18, the house look 0.23.
- Shape the top with a soft shoulder rather than a hard white: `white_point` 0.94 to 0.95 gives a creamy, printed
  top; 0.98 to 0.985 gives glossy, clean social whites.
- Expose so the most important thing in frame looks its best, then bring distracting areas down rather than
  lifting the subject.

### Blacks
- `black_lift` trades density for softness. Measured shadow separation between 3 and 5 stops under grey: 2.98x
  with no lift, 2.34x at 0.012, 1.90x at 0.03, 1.65x at 0.05.
- Dark skin often sits about 1 to 3 stops under grey, depending on the complexion and the light (a colorist's rule
  of thumb from the zone system, not a measurement). Lifted, milky blacks steal its detail and make it look ashy.
  With dark-skinned talent keep `black_lift` at or below about 0.02 (a starting point) and prefer a soft toe over
  a raised floor.
- Print stocks do have raised, slightly cool blacks (Kodak 2383 about 0.03 to 0.047, blue highest). A cool black
  reads as filmic; a blue black on gloves, hair or suits reads as a mistake. Watch black clothing.

### Saturation and density
- Real saturated surfaces are darker. Saturation that also darkens color (subtractive, film-like) reads premium;
  saturation that brightens color reads as video. `print_density` is the subtractive part. It works by chroma
  only, so high values also darken skin and orange: keep it at 0.25 to 0.4 and let `hue_sat` do hue-specific work.
- `look.hue_lum`: per-hue luminance multipliers, faded out on skin and neutrals.
  This is how print looks darken greens, cyans and reds without muddying skin (for example green 0.78, cyan 0.86,
  red 0.90, blue 0.94). Use it with a lower `print_density` (about 0.18).
- Colorists often boost low-saturation colors more than already saturated ones. The lab has no chroma-dependent
  saturation: `sat` scales every chroma by the same factor, and `sat_hi_reduce` / `sat_sh_reduce` lower it by
  brightness (highlights and shadows), not by how saturated a color already is. Get close with a modest `sat` and
  lower `hue_sat` on the bands that are already loud (often blue and magenta on phone footage); `print_density`
  darkens the most saturated colors, which also tames them. The default look and clean_pop raise blue and sky
  chroma by about 1.17 to 1.27 and turn blues and skies about 3.6 to 4.6 deg toward magenta (measured with
  `measure`). Whether that suits the footage is a taste call: if skies or blue LEDs read purple, set `hue_sat` blue
  to about 1.05 or `hue_shift` blue to -3; on display-referred phone footage also set magenta to about 1.0.
- Pull saturation out of highlights and deep shadows (`sat_hi_reduce` 0.2 to 0.3, `sat_sh_reduce` 0.15 to 0.35).
  Clean whites and clean blacks are among the quickest premium cues.
- Memory colors: people tolerate chroma changes far more than hue changes. Skin, foliage, sky and brand colors may
  get richer or softer, but keep their hue where viewers expect it.

### Color separation
- Separation (distinct hues in the frame, usually a warm subject against a cooler world) often has the biggest
  single impact. Start with balance: the colors should spread over at least two areas of the vectorscope while skin stays
  right. If everything sits in one warm area, the image looks monochrome however saturated it is.
- Orange and teal is only the most common complementary harmony: skin supplies the orange, anything cool supplies
  the teal. Push it only when the image already has both. Forced onto a warm-only image it just tints neutrals,
  and viewers know walls, sheets and white shirts should be near neutral.
- The subtle version usually survives every shot. Go too far on purpose, then back off until nobody could tell a
  grade was applied.

### Split toning
- Anchor the split at middle grey: cool below, warm above, and leave a neutral band around grey so faces in the
  mids stay clean. `tint_range` about [0.3 to 0.45, 0.58 to 0.62].
- Measured print targets: Kodak 2383 (D65) b* about -5.7 at 1 to 2 stops under grey and +3 at 2 stops over;
  2383 (D60) about -4 under, neutral at grey, +5 to +7 over; Fuji 3513 has green-cyan shadows (a* -5.7, b* -3.8)
  and near-neutral highlights. The film_print preset measures about -3.5 at two stops under and +2.7 at two stops
  over (with `measure`): shadows about as cool as the D60 print and a top close to the D65 print (D60-like cool
  shadows, D65-like top). Raise the warm top with `highlight_tint` if the brief wants the warmer D60 print.
- Red moves fast; small numbers go a long way. Warm highlights mostly by lowering blue: `highlight_tint` like
  [0.0 to 0.004, 0.0 to 0.006, -0.02 to -0.03]. Pushing red up clips the red channel first and flattens bright
  skin and white shirts (measured: 2.73 % against 0.70 % clipped for the same warmth).
- With dark skin, teal shadows make skin look dirty. If you add cool shadows, lean them blue rather than green
  (negative green, positive blue), which is the same as a touch of magenta, but only a touch: more turns shadowed
  skin magenta-blue. The film_print, film_print_plus and teal_orange presets push red down in the shadows
  (`shadow_tint` red -0.03 and -0.02), which is the teal move. With dark-skinned talent set `shadow_tint` to [0, 0, 0]
  with film_print, film_print_plus, teal_orange and moody_dense (and `black_lift` 0.02 with film_print or
  film_print_plus). Keep the cool world with the hue moves (blues and cyans) and the warm top instead. Only if the
  brief needs cool shadows, use [-0.012, -0.006, 0.012], and look at the faces in shadow: it still turns skin at 10 %
  luma 15 to 30 deg toward magenta, and on teal_orange it swaps a greyer shadow that keeps the hue for a magenta one. Measured on light, medium and deep
  skin-colored patches graded so they come out at 10 % display luma (about 2.5 stops under grey), against the same
  skin at the same luma with no look: film_print's own shadows turn them 115 to 160 deg toward magenta-blue and keep
  20 to 50 % of their chroma; [-0.02, -0.006, 0.02] still turns them 30 to 70 deg; the halved tint above 15 to 30
  deg, keeping about 55 to 65 % of their chroma; no tint about 2 deg and 90 %. At 15 % luma the halved tint keeps
  about 75 to 90 % and turns them 2 to 9 deg. teal_orange's own shadows keep the hue but only 40 to 70 % of the
  chroma at 10 % luma; with the halved tint it keeps 85 to 100 % and turns them 8 to 16 deg. soft_pastel turns them
  toward green (about 150 deg at 10 %, 30 to 100 deg at 15 %) and keeps 30 to 65 % of their chroma; moody_dense's own shadows keep 40 to 60 % of the chroma
  at 10 % luma and turn it 13 to 29 deg toward magenta-blue (with no shadow tint: 75 to 80 % and about 2 deg); at 15 %
  it keeps nearly all. The skin metrics do not see skin this dark (section 3), so
  judge it by eye.

### Hue rotations (sign conventions)
- `hue_shift` is in degrees, positive = counter-clockwise on the vectorscope. Band order going counter-clockwise:
  blue, magenta, red, orange, yellow, green, cyan.
- Yellows toward orange: yellow negative. Blues toward cyan: blue negative. Cyans toward blue: cyan positive.
  Greens toward cyan or teal: green positive. Greens toward olive or yellow: green negative.
- Classic filmic moves (all measured on Kodak 2383): yellows toward orange, blues and cyans converging on one
  teal-blue, greens slightly toward cyan and darker. Warm editorial: greens toward olive and less saturated.
- Keep hue moves on skin tiny. `skin_protect` holds skin chroma while the world moves.
- `skin_line_pull` and `skin_sat` act on every skin-hued surface, not only on faces: orange and tan products, wood
  and leather near the skin line turn by up to about 3 deg with the default pull. For orange or tan brand colors
  that must stay exact, lower `skin_line_pull` to 0.2.

### Film print emulation
- A print look has three parts: a contrast curve with toe and shoulder, a tonal color (cool low mids, warm
  highlights, raised cool blacks) and a color remap (hue-selective density, hue compression). Contrast is easy to
  adjust underneath; the color palette is the hard part, so pick the palette.
- Kodak 2383 is the denser, more primary print with a warmer top; Fuji 3513 is flatter, more pastel and more
  neutral on top.
- Measured on Resolve's own 2383 LUT: skin luminance unchanged, saturated greens about 17 L* darker, cyans about
  10, reds about 9, blues and magentas about 5. In the lab: shadow and highlight tints for the tonal color,
  `hue_shift` for the remap, `hue_sat` to lower greens and blues, `print_density` or better `hue_lum` for density.

---------------------------------------------------------------------------------------------------------------

## 3. Skin across skin types

- Measured skin hue (CIELAB, D65; Wang, Xiao, Wuerger et al. 2015): East Asian 60 deg, South Asian 60 deg,
  white 54 deg, Black 53 deg; the full range across people was 45 to 69 deg.
- On the lab's vectorscope that is about 130 deg for East and South Asian skin and about 125 deg for white and
  Black skin (range 119 to 137). The lab's skin line (124.9 deg) matches white and Black skin.
- So `skin_hue_offset_deg` (metrics) of +3 to +6 is natural for South and East Asian talent, and about 0 for white
  and Black talent. Metrics always report the offset against the standard line.
- `look.skin_target_deg` rotates the line that `skin_line_pull` and `skin_protect` work toward. Set +3 to +6 for
  South or East Asian talent so the pull does not drag their skin pink. With mixed groups in one piece keep it at
  0 to +3 and `skin_line_pull` low (0.2 to 0.35).
- Light skin gets less colorful as it gets lighter; dark skin gets less colorful as it gets darker. Do not force
  one chroma on everyone; match skin to its own neighbours.
- Dark skin: exposure first, then balance, then the look. Set exposure so the skin looks right for the scene and
  shows detail; it often sits about 1 to 3 stops under grey depending on the complexion (see "Blacks"). There is
  no standard amount to add: an underexposed shot needs more, a well exposed one none, and too much makes the skin
  look thin and washed out. Push until it is clearly too bright, then back off to where it still looks real. Then
  balance by eye on the skin itself: remove the cast that is actually there (in one colorist's demonstration it
  was too much cyan), and back off if the skin starts to read magenta. Keep blacks down and avoid teal in the
  shadows; both make dark skin look ashy or dirty.
- The auto exposure reads the whole frame, not the face: it lifts close-ups filled with dark skin or dark clothes
  and pulls a dark-skinned face in front of a bright wall down, so the same face can change brightness from shot to
  shot. Compare the same face across close and wide shots and set `clip_overrides` stops so it sits at one level;
  `balance.expo_strength` 0.25 is a gentler start with dark-skinned talent.
- The skin metrics (`skin_pct`, `skin_hue_offset_deg`, the `skin off` flag and match's skin guard) only see skin
  brighter than about 15 % display luma. Skin 2 stops or more under grey is not measured, so judge dark skin in
  shadow by eye.
- Light skin: magenta or pink quickly reads as unhealthy; slightly warm and slightly yellow usually reads
  healthier. Practitioners agree that skin should read a little warmer than the environment, never blue or green,
  even at night.
- Consistency across shots beats landing exactly on a line; slightly magenta or green is acceptable if every shot
  agrees.
- The skin metric is hue-based: wood, tan walls, orange products and leather can count as skin. Check the image
  before chasing a skin number.

---------------------------------------------------------------------------------------------------------------

## 4. Balance and matching

### What the code does
Stage 1, per clip (`balance`, method `grayness`):
- Decodes the clip's 5 strip frames to linear Rec.709 and looks only at the part that is visible on screen.
- Ignores near-clipped pixels (the camera's `clip_code`) and the noise floor.
- Finds the light color with the grayness index: the average of the 1 % of pixels whose color stays the same
  across edges (walls, paper, clothing, metal, shadows). Plants, orange walls and skin close-ups are usually not
  mistaken for a cast as long as something grey-ish is in frame.
- Corrects tint (green and magenta) in full and temperature (amber and blue) through a soft knee: small casts are
  removed, large warm or cool casts are kept partly, like a colorist keeps part of the mood of tungsten or golden
  hour light.
- Sets exposure half-way toward a target (geometric mean luma 0.10), within -3 to +2 stops: bright scenes come
  down, dark ones come up, but high-key stays brighter than low-key.
Stage 2, the `match` command: renders every clip, measures the mid-tone neutral cast (a*, b*) and nudges temp and
tint (at most about 2 units per clip and run, `--limit`; runs add up, so check the clip_overrides totals on clips
flagged `strong cast kept`) so every clip lands on one target: the median of all clips by default, a hero clip
(`--target CLIPKEY`), or a fixed a*, b* value (`--target 0.5,1.0`). The trims are written into `clip_overrides`,
where agents can see and edit them. match names clips whose trim hit the limit (the neutrals wanted more: mixed
light, or a cast the balance kept on purpose); look at those. On a reference reel this matched simultaneous panels
as tightly as expert agents working by eye.
The cast is measured through the whole grade, look included, so the number moves when the look changes: on the
reference reel the film_print preset moved `mean_slice_neutral_spread` from 0.8 (house look) to 1.0, and a second
`match` on the look file brought it to 0.6. Grey and white surfaces measured on their own (pixels with C*ab under
6, no skin hues) told another story: the look itself had tightened them (0.8 to 0.6) and the second match loosened
them again (0.8). The same held for every look tried that keeps less chroma than the house look (film_print,
soft_pastel, moody_dense, golden_editorial and teal_orange: up to 0.25 worse on average, and 1.7 worse on one
clip), while after clean_pop, which adds chroma, the second match helped. So once the look is settled, run
`match` on that file and read its output: when it notes that the look keeps less chroma than the house look,
keep the file you started from (the baseline's trims) unless a clip visibly stands out. Otherwise keep the matched
file when `mean_slice_neutral_spread` drops, `max_slice_neutral_spread` does not rise, and `max_clipped_pct` does
not rise (and is under 1, or trim the clip as section 7 says) with no new `clipped` flag (a warmer trim can clip
the red channel on bright warm surfaces; if it does, lower that clip's stops by about 0.1 in clip_overrides). Look
at the screens too. A new `skin off` flag is fine only if match moved that clip's `skin_hue_offset_deg` by less
than about 2 deg (compare the two metrics.json); after a bigger move, set that clip's temp and tint in
clip_overrides back to their values before match (the trim read pink or beige clothing, wood or a tan wall as a
neutral). `match` never returns a trim that leaves a clip further from the target than no trim.
Its neutral mask is the mid-tones whose C*ab is under 14 both in the graded picture and before the look. Without
the second test a look that lowers chroma (soft_pastel, for example) would let skin, wood or orange walls into the
mask, and match would then cool and green those clips until skin looks sallow. It is not a perfect filter: pale
skin, pastel clothing (a pale pink top), beige fabric and light wood that are under 14 before the look still
count as neutral. The test treats every hue alike, so a white wall under warm light still counts as a neutral and
a warm clip reads warm. The backstop is a per-clip skin guard: a clip whose skin would lose more than 10 % chroma,
or move more than 3 deg further from `look.skin_target_deg` to beyond 6 deg (the skin off limit), keeps its
previous trim, and match lists it ("kept its previous trim"). Which clips it keeps depends on the look and the
footage; on the reference reel it was a clip in mixed light (a strong warm cast kept on purpose, a pale pink top
and a plaid shirt in frame) in almost every look. Wood, a warm-lit table or fabric can count as skin there, so the
guard sometimes protects them rather than a face. Such a clip's slice can keep a larger neutral spread (about 3 to
5 there), which a second match run does not change: look at such clips and trim them by hand if they still need
it (the `strong cast kept` flag says how much of the light's color the balance kept on purpose).

### Knobs
| key | default | what it does |
|---|---|---|
| balance.method | grayness | `grayness`; `gray_world` (first version's method); `none` (no white balance, exposure still automatic). Use `none` for concerts, clubs and neon, where the colored light is the point |
| balance.temp_knee | 3.5 | how much of a warm or cool cast is removed before the knee (about 45 mired). Lower keeps more mood, higher neutralises more |
| balance.tint_knee | 8 | protects deliberate magenta or green light (stage, LED); tint is corrected in full below about 4 units |
| balance.tint_strength | 1 | fraction of the tint correction applied |
| balance.gi_percent | 1 | share of greyest pixels used; raise to 2 to 3 on noisy footage |
| balance.clip_code | null | near-clip threshold; null uses the camera's value |
| balance.expo_target | 0.10 | target geometric mean luma (linear Rec.709, visible area) |
| balance.expo_strength | 0.5 | 0 = no auto exposure, 1 = full normalisation (flattens high-key and low-key; avoid) |
| balance.expo_range | [-3, 2] | limits of the auto exposure in stops |
| balance.global_temp, global_tint | 0, 0 | the same trim on every clip: house warmth or coolness |
| clip_overrides.KEY | {} | `{"stops": s, "temp": t, "tint": n}` per clip, added after the auto balance, in linear Rec.709 |
| render.exposure | 0.685 | global gain before the tone curve (camera sources only) |

Units: `temp` +1 is about 13 mired warmer near daylight (6500 K to 5000 K is about +3.5). Positive `tint` is
magenta, negative is green. `stops` is exposure in stops. Overrides stack on the auto balance, the match trims and the global
trims, so edit the existing value rather than adding a second entry.

### Flags (metrics.json, per clip and in summary.flagged_clips)
| flag | meaning | what to do |
|---|---|---|
| strong cast kept | a large warm or cool cast was only partly removed by the knee | look: deliberate (practicals, sunset)? keep it. A mistake? add temp in clip_overrides |
| mixed light | different parts of the frame have different light colors (window plus tungsten, LED plus daylight) | one gain cannot fix it; choose what matters (usually skin), note a window or secondary for the hand-over |
| light changes | the light color changes inside the clip (camera moves from window to interior) | pick the part that is on screen longest; note keyframing for the hand-over |
| few gray pixels | almost nothing neutral in frame (one color fills it) | trust the image over the numbers; match it by eye to its neighbours |
| clipped | many pixels at the top of the range | lower exposure for that clip or soften the look's top; blown sky or practicals may be acceptable |
| crushed | many pixels at the bottom | raise exposure or lower contrast, unless it really is black |
| skin off | skin hue more than 6 deg from `look.skin_target_deg`, on a clip with at least 3 % skin-hued pixels | check it is skin first (wood, tan walls and leather count too); then temp and tint on that clip, or skin controls in the look |
| camera approximate, camera low confidence | the camera decode is a best guess | confirm the camera with the user before trusting color |

### When to override the automatic result
- The shot has deliberate colored light (golden hour, candles, neon): ease the temp or tint trim, or use
  `method: none` for the whole piece if it is stage lighting. `match` pulls every clip up to 2 temp and tint units
  (about 26 mired) toward the target, including clips flagged `strong cast kept`: with `method: none` or deliberate
  colored light run `match ... --limit 0.5`, or set those clips' match trims back to 0 in `clip_overrides`.
- A hero shot defines the look of the piece: `match ... --target <hero clip key>`.
- After an edit: clip keys are track and start frame, so a ripple edit gives moved clips new keys. The dump records
  the moves, and every command finds a moved clip's trims under its old key (match writes each clip's uid into its
  entry, so later files follow their clips directly). Before editing clip_overrides by hand after such an edit, run
  `rekey PARAMS OUT`: it stores every entry under its clip's current key and leaves out entries of deleted clips.
  A clip replaced by another shot at the same place (check warns `clip_replaced`): an entry that match or rekey
  wrote carries the old clip's uid and no longer applies, so the new shot gets only the auto balance until you run
  `match PARAMS OUT --only <its key>` (only that clip is trimmed; the others keep theirs); an entry without a uid (hand-made, or from an older file) stays under the key and now applies to
  the new shot, so reset it if it does not fit.
- Two clips cut from the same source file should share one balance; copy one's override to the other if they
  drift, then run `rekey` on the file: it tags the copy with its own clip's uid (a copy that keeps the other clip's
  uid stops applying once that clip is deleted).
- A panel is partly covered or animated: the balance only sees the visible part; if that part is a flat color,
  match the clip by eye.
- Exposure: the auto exposure is a good start, not the answer. Bring the subject to where it looks best.

---------------------------------------------------------------------------------------------------------------

## 5. Display rendering

The conversion turns scene light into a picture. `render.tonemap`:
- `hr` (default for schema 2): soft gamut compression into Rec.709 first, then the filmic tone curve per channel,
  then a hue restore that moves the middle channel most of the way back to the scene's ratio. Bright oranges, skin
  highlights and saturated lights keep their hue instead of drifting toward yellow, cyan or magenta, and colors no
  longer get stuck at a primary when exposure rises. It also bakes into a 33^3 LUT more cleanly.
- `legacy`: the first version's per-channel curve (warmer, yellower bright oranges). Only to reproduce an old
  grade, or if the judges genuinely prefer that rendering.
- `render.hue_restore` 0 to 1 (0.8): 0 behaves like legacy's hue skew, 1 restores the scene hue fully. Lower it a
  little if bright warm colors should glow toward yellow.
- `render.gamut_threshold` (0.9): where gamut compression starts, from 0 to 0.99 (1 or more is refused because it
  would put invalid numbers into the LUTs). Leave it unless saturated lights look flat.
- `render.lum_preserve` 0 to 1 (0.25): 0 = per-channel curve (highlights desaturate naturally), 1 = luminance
  preserving (saturation kept in highlights, can look electronic). 0.2 to 0.35 is the useful range.
- `render.exposure`: global exposure of the whole piece before the curve (camera sources only).

Display-referred sources (Rec.709 phone video, sRGB, P3, footage that already went through a vendor LUT) are
already tone-mapped. For those cameras the tone curve, `render.exposure` and auto exposure are skipped; white
balance and clip_overrides still apply, and a neutral grade gives back the original picture. Expect less latitude:
blown highlights stay blown. To change their overall brightness use `clip_overrides` stops, or `look.pivot` and
`look.contrast`.
- sRGB and Display P3 sources are decoded with the sRGB curve (as Resolve's sRGB transform does) and shown at 2.4,
  which lifts near-black a little (5/255 becomes about 17/255). Screen recordings made for a 2.2 display can look
  lifted in the shadows; if so, try `rec709` for those clips in `camera_overrides` (it passes the picture through
  unchanged) or lower `look.black_lift`.
- The `lut:` route needs a 3D-only .cube. A vendor LUT that starts with a 1D shaper is refused with "is a 1D LUT";
  convert it to a plain 3D cube first (in Resolve: put it alone on a spare clip, right-click the clip thumbnail >
  Generate LUT > 65 Point Cube).

---------------------------------------------------------------------------------------------------------------

## 6. Metrics and exit criteria

`render` writes `metrics.json`:
- `summary`: `clips`, `slices`, `max_clipped_pct`, `max_crushed_pct`, `mean_skin_offset_deg` (null without skin),
  `mean_slice_luma_spread`, `max_slice_luma_spread`, `mean_slice_neutral_spread`, `max_slice_neutral_spread`,
  `flagged_clips` {clip key: [flags]}.
- `clips[key]`: `luma_mean`; `luma_p2_p50_p98` (display luma percentiles); `clipped_pct` (pixels at the white
  point); `crushed_pct` (pixels at the black floor); `mean_chroma_Cab`; `cast_ab_midtones` (average a*, b* of
  low-chroma mid-tones: a* + is magenta, - green; b* + is yellow, - blue; near 0 means neutral); `skin_pct`;
  `skin_hue_offset_deg` (+ toward yellow, - toward red or magenta); `skin_chroma_Cab`; `camera`,
  `camera_status`; `balance` (method, raw cast, applied correction, auto stops, grey pixels found, spreads);
  `final_gains`; `flags`. For schema 2 params `cast_ab_midtones` uses only low-chroma mid-tones that were also
  low-chroma before the look, like `match`; schema 1 renders measure it the old way.
- `slices[i]`: one entry per moment between cuts: `panels` (per visible clip: luma, cast, chroma), `luma_spread`
  (brightness difference between panels on screen together), `neutral_spread` (largest a*/b* distance between
  their mid-tone casts; 0 for a single panel).

Reading them:
- Clipping: under 1 % per clip is clean; speculars and practicals may clip on purpose.
- Crushing: under 1.5 %, unless it is truly black (night sky, black backdrop).
- Casts: `cast_ab_midtones` within about 2 of neutral reads neutral; bigger values should be deliberate.
- Neutral spread between panels or neighbouring shots: under 3 reads matched; above 5 viewers notice.
- Luma spread is content driven (a white product next to a dark face); judge it by eye.

Exit criteria for any finished grade:
- `max_clipped_pct` under 1
- `max_crushed_pct` under 1.5, unless the frame really is black there
- `mean_slice_neutral_spread` under 3 wherever panels or neighbouring shots are meant to match
- skin offset within 3 deg of the natural value for the skin groups in the brief
- no new flags compared with the baseline render, except a `skin off` flag on a clip whose `skin_hue_offset_deg`
  moved less than about 2 deg
- and it looks right on the screens images, which matters more than any number.

Helper tools:
- `wedge PARAMS KEY VALUES OUTDIR`: one image with one row per value, for side-by-side decisions (a timing wedge).
  KEY is a dotted path (`look.contrast`, `balance.global_temp`, `clip_overrides.1_120.stops`) or `preset`.
  Values are separated by commas; use `--sep ;` when a value is a list.
- `measure PARAMS`: the look's numbers without footage: grey out, slope per stop at grey, black and white RGB,
  grey a*/b* at -2, 0 and +2 stops, luminance change per hue. Use it to hit the print targets above.

---------------------------------------------------------------------------------------------------------------

## 7. Presets and directions

Presets are partial params in `presets/` (merge them onto a baseline with `merge BASE <name> OUT`). They set
the `look` and sometimes one global `render` or `balance` value. Measured on a reference reel (Sony S-Log3, studio
with daylight and LED) on top of its matched baseline.

| preset | character | use for | watch out |
|---|---|---|---|
| neutral | no look at all | people who grade by hand on top | |
| clean_pop | contrast 0.42, black 0.006, white 0.985, sat 1.22, gentle split | product, beauty, brand social | noisy low light |
| film_print | 2383-like: contrast 0.30, cool raised blacks 0.03, warm top, yellows to orange, greens darker | brand films, fashion | black clothing turning blue; dark skin: shadow_tint [0, 0, 0] and black_lift 0.02 (section 2, "Split toning") |
| film_print_plus | film_print with hue_lum density (needs look.hue_lum) | same, denser greens, cleaner skin | the same shadows as film_print: the same dark-skin change |
| soft_pastel | exposure 0.72, contrast 0.24, cyan-green lifted shadows, pastel | lifestyle, wellness, fashion editorial | dark skin in shadow, night |
| moody_dense | exposure 0.62, contrast 0.50, rich blacks, cool muted world, protected skin | night, music, drama, premium tech | bright happy brand pieces; dark skin: shadow_tint [0, 0, 0] (section 2, "Split toning") |
| golden_editorial | global_temp 0.6, contrast 0.30, warm matte floor, olive greens, muted blues | food, hospitality, travel, craft | skin sliding yellow on warm footage |
| teal_orange | split anchored at grey with a neutral band, blues to cyan, yellows to orange | footage with warm skin and cool elements | tinted neutrals on warm-only footage; dark skin: shadow_tint [0, 0, 0] (section 2, "Split toning") |

`golden_editorial` sets `balance.global_temp` to 0.6, which replaces the baseline's value: add the two if the
baseline had one. `soft_pastel` and `moody_dense` set `render.exposure` (0.72 and 0.62), which likewise replaces
any exposure change the baseline made (the default is 0.685): scale the preset's value by the baseline's change.
On display-referred sources (Rec.709, sRGB, P3) `render.exposure` does nothing: there use `clip_overrides` stops,
or `look.pivot` and `look.contrast`, instead.
`moody_dense` sits right at the clipping criterion (1 %) on bright footage. `clipped_pct` counts pixels within
0.012 of the white point wherever it is, so moving the white point does not help. Trim the flagged clip first
(`clip_overrides` stops about -0.1); for the whole piece lower `look.contrast` to about 0.45 or `render.exposure`
to about 0.60, then render again and check `max_clipped_pct`.

Workflow directions map to presets: clean -> clean_pop, noir -> moody_dense, film_print -> film_print,
warm_editorial -> golden_editorial, soft_pastel -> soft_pastel, teal_orange -> teal_orange. Choosing four from the
brief: product or beauty social: clean, film_print, soft_pastel, warm_editorial. Music, fashion or night:
film_print, noir, teal_orange, clean. Hospitality or food: warm_editorial, clean, film_print, soft_pastel.

---------------------------------------------------------------------------------------------------------------

## 8. Social delivery

### What the platforms do
- Instagram Reels and YouTube Shorts re-encode every upload to about 1 to 2.5 Mbps AV1 or VP9 (3 to 5 Mbps
  H.264) at 1080x1920. TikTok serves H.264 and its own HEVC; its rates were not measured. Measured on the others:
  the re-encode does not change levels, gamma or saturation, but it removes a quarter to a third of the fine
  texture, most of it in the lower half of the range, and turns clean gradients into bands.
- Fine texture that matters (hair, fabric, fine lines, skin pores) survives better above about 40 % luma. Do not let
  a story point depend on texture below 20 %. Large smooth dark areas band on every codec.

### Export settings (Resolve Deliver page)
| setting | Reels and TikTok | YouTube Shorts |
|---|---|---|
| format and codec | MP4, H.264 High or H.265 | MP4 or MOV, H.264, H.265 or ProRes 422 HQ |
| resolution | 1080x1920 | 2160x3840 when the source allows, else 1080x1920 |
| frame rate | the timeline rate | the timeline rate |
| bit rate | 20 to 40 Mbps for 1080p | 10 to 20 Mbps at 1080p, 45 to 68 Mbps at 2160p |
| color | Advanced Settings: Color Space Tag Rec.709, Gamma Tag Rec.709 (Scene) or Rec.709-A, so the file is tagged 1-1-1 | same |
| data levels | Auto or Video; never Full for social | same |
| audio | AAC 320 kb/s, 48 kHz | AAC or Opus, 48 kHz |
| other | no timecode track; Instagram "Upload at highest quality" and TikTok "Upload HD" on; upload on Wi-Fi | desktop upload for 4K |

- Never export 10-bit H.264 for upload (it does not play on Apple devices). 10-bit belongs in H.265 Main10 or
  ProRes.
- Adding a render job changes the project: ask first, or let the user do it.

### Tags and the Mac gamma shift
- In a DaVinci YRGB project, rendered files take their color tags from the timeline color space unless the render
  settings say otherwise. Set them in the Deliver page's Advanced Settings (Color Space Tag Rec.709, Gamma Tag
  Rec.709 (Scene) or Rec.709-A): that tags the file 1-1-1 without changing a project setting. Switching the timeline
  color space to Rec.709 (Scene) or Rec.709-A does the same but changes the project, and effects such as Halation
  process in the timeline color space by default, so hand-added texture can change with it; use it only as a
  fallback. Research on current macOS found 1-2-1 and 1-1-1 files both shown with about the 1.96 curve below.
- Macs (QuickTime, Safari, Chrome on macOS), and likely also iPhones, show 1-1-1 Rec.709 video with a curve of
  about 1.96, not 2.4. Shadows and low mids look lighter and flatter: about +1 stop at 20 % code, +0.44 at 50 %. Chrome on
  other systems and most phones show it with about 2.2. There is no single correct look online; the grade has to
  survive 1.96 to 2.4.
- Do not bake a gamma correction into the file to fight the Mac: it makes the file wrong everywhere else. Check the
  export on an iPhone and an Android phone at medium brightness instead.
- The lab's jpg previews are shown as sRGB, which is close to what Windows and Android viewers see.
- On a Mac with "Viewers match QuickTime Player when using Rec.709 Scene" turned on (Resolve Preferences), a
  timeline in Rec.709 (Scene) makes Resolve's own viewer look lighter too. That is expected; do not re-grade because
  of it.

### Levels for SDR social
| item | target |
|---|---|
| range | all picture inside 16 to 235 (8-bit); anything outside is clipped by players |
| black point | 0 to 2 % for clean looks (clean_pop sits at about 0.6 %); 2 to 4 % only for a deliberate matte or print look, and not with dark-skinned talent (see "Blacks") |
| skin | a rule of thumb, not measured: light and medium skin often around 45 to 70 %; dark skin lower, about 1 to 3 stops under grey (roughly 10 to 30 % with the default look). Expose for the scene; do not force skin into a band |
| average picture level | a rule of thumb: roughly 35 to 55 % bright commercial, 20 to 35 % moody |
| whites | 90 to 100 % for clean whites; SDR next to HDR posts looks dim, so do not mute the top |
| saturation | leave headroom: Android phones in vivid modes add about 20 to 30 % chroma |

### HDR
Deliver SDR unless the client asks for HDR. Instagram on iPhone keeps Dolby Vision 8.4 and YouTube accepts HLG
or PQ, but every SDR viewer then sees the platform's own tone mapping. If HDR is wanted, someone has to check the
platform's SDR version on an SDR phone.

### The QC command
`social-qc MASTER` re-encodes the user's export the way the platforms roughly do and
writes `qc.txt`, `qc.json` and `qc_sheet.jpg` into `LAB/social_qc`. Per profile:
- CAMBI (Netflix's banding index, 0 = none, 24 = max): 3 or more is a WARN (some banding), 5 or more a FAIL
  (visible banding). VMAF measures overall fidelity (under 80 is visibly soft or blocky); it does not see banding,
  so never judge banding by VMAF.
- Also: blocking in flat areas, shadow texture kept (under 0.5 means smeared), luma drift (over 1 code value means
  wrong levels or tags), chroma kept.
- The sheet shows the worst frame, a zoom on the flat area that changed most, and a contour view where banding
  shows as stair steps and blocking as squares.

### Grain and dither
- Normal camera footage with visible sensor noise does not need grain or dither against banding. After the
  simulated platform encodes, banding stayed at or below the WARN level whatever was added: a darker real segment
  sat at CAMBI about 3.3 to 3.5 on the AV1 profiles with 8-bit or 10-bit masters, fine grain and dither; soft grain
  lowered it a little (about 2.5 to 3.2), static fine grain to about 1.5, and only static soft grain lowered it much
  (to about 0.2). 10-bit against 8-bit made no difference. Do not add grain by default.
- If QC shows banding in clean gradients (skies, backdrops, vignettes, heavy noise reduction, graphics): first
  reduce noise reduction and contrast on those areas. Then upload a 10-bit H.265 Main10 or ProRes master where the
  platform accepts it. Random dither does not help: the encoders remove it. Re-run the QC.
- Still banding, mostly on static shots or graphics: static soft grain (the same pattern every frame, about 2 to
  3 px, about 1.5 code values; Resolve's Film Grain with Freeze on, Saturation 0 and some Softness). It stays put
  when the camera moves, so keep it for locked-off shots and title cards.
- If the client wants a grain look: soft grain about 2 to 3 px, moving is fine, and expect only about 30 % of it to
  survive AV1. Never fine 1 px moving grain for social: the encoders smear it and it costs fidelity.

---------------------------------------------------------------------------------------------------------------

## 9. Texture: what the user adds by hand

A LUT cannot hold anything that depends on neighbouring pixels or time, and Resolve's scripting cannot add effects
to nodes. If the look wants texture, tell the user to add it after the look, best on a new node after the look node
in the color group's post-clip graph (the Timeline node works too, but it also textures titles, logos and other
graphics), and to put nothing else there. check recognises nodes
that hold only texture effects (by their tool names: grain, halation, glow, bloom, diffusion, mist, sharpen, soften,
blur, vignette and similar) and a later apply leaves them in place; a color correction on those nodes stops the
next apply. Film Look Creator is reported as a warning, because it can also change color and contrast. Add them in
this order:
1. Halation: the red-orange glow around bright sources. Subtle, limited to the brightest highlights.
2. Diffusion or glow.
3. Sharpening or softening.
4. Vignette: use the Vignette effect. A power window with a gain drop holds color tools, so check treats that node
   as a grade and the next apply stops at it.
5. Grain last (see "Grain and dither" for social).
Resolve Studio has Halation, Glow and Film Grain effects, and its Film Look Creator bundles halation, bloom, grain
and vignette. A halation built by hand (a highlight qualifier, a blur and an orange gain on one node) looks fine
too, but that node holds color tools, so check treats it as a grade and the next tweak stops at it; prefer the
effects.
Judge texture after compression on a phone, never on the timeline alone.

Also hand over what a LUT cannot do per shot: "clip X: soft window on the face, +0.3 stop", "clip Y: exposure
ramps, keyframe it", "clip Z: mixed light, a qualifier on the window".
