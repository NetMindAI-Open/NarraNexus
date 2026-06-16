---
code_file: frontend/src/components/help/measure.ts
last_verified: 2026-06-11
stub: false
---

## 2026-06-11 (round 9) — near-target fallback for 'top' notes

'top'-rail anchors that hug the top edge (cost chip) have no room
above — the note now sits just BELOW the anchor with a short arrow up,
instead of being routed through the right rail far away.

## 2026-06-11 (round 8) — proximity column + staggered same-row entries

Owner: Cost note stranded far from its chip (the centered legend's
fault) and some lines overlapped. (1) Notes hug their target's height
again (monotonic push-down keeps the column tidy and leaders
non-crossing). (2) Targets sharing a row (adjacent toolbar buttons)
used to receive two entry segments on ONE horizontal line — entries
now step down 12px per subsequent same-row item.

## 2026-06-11 (round 7) — legend columns; ellipses deleted

Owner: 排版仍乱 + 圈选显得不专业. Two changes: (1) rail notes now
stack at an EVEN rhythm, vertically centered in the band between
header and footer — a tidy legend list; leaders (engage at >40px
drift) carry the correspondence to targets. (2) The ellipse stroke is
GONE from the entire system (wobble.ts no longer exports it; manifest
has no circle field) — pointing language is arrow/leader only.

## 2026-06-11 (round 6) — region notes for large targets

Owner screenshot review: circling the full-width composer drew two
parallel lines across the screen, and "The conversation" dragged a
long arrow to its region's far border. Root cause: one stroke language
for both controls and AREAS. Targets larger than 38% vw / 50% vh now
become `kind: 'region'` — the note is written ON the area (or just
above a bottom strip like the composer) with a short handwritten
underline; no arrow, no circle. Controls keep `kind: 'point'`
(arrow / leader / ellipse).

## 2026-06-11 (round 5) — leader lanes

Owner: arrows into the strip were near-vertical and overlapped
indistinguishably — the right rail hugged the strip, leaving a ~16px
corridor. Rails moved a full corridor inward; when an annotation's
vertical travel exceeds 56px the stroke becomes an elbow LEADER
(horizontal out → vertical run in a per-item lane → horizontal entry
at the target's center height, [[wobble]] wobblyLeader). Lanes stagger
16px apart so parallel runs never merge. Short hops keep the direct
wobbly arrow.

## 2026-06-11 (round 4) — fixed rails + nearest-border arrows

Two layout bugs from Owner review with the artifact column expanded:
the right rail's x was derived from target extents, so the artifact
column (a huge rect reaching mid-screen) dragged the rail across the
page and arrows to the strip stretched the full width. Rails now sit
at FIXED screen-edge offsets, and arrows aim at the nearest point on
the target's BORDER (clamp-to-rect, with an inside-rect escape through
the nearer vertical edge), backed off 8px. Arrow origin = the
headline's vertical center on the side facing the target; PlacedAnnotation
gained `align` so the note text justifies toward its arrow.

## 2026-06-11 (PM)

Gained `layoutAnnotations` — the pure rail-stacking placement (left/
right note columns + top mode), keeping notes clear of the bottom-
center controls. Estimated note height accounts for the detail line.



# measure.ts — Anchor measurement for the help overlay

Pure function `measureAnnotations(manifest)`: querySelector by
`data-help-id`, skip missing / zero-size / fully-offscreen anchors,
sort by priority. Separate module so tests can import it without
breaking react-refresh's only-export-components rule on the component
file. Used by [[HelpOverlay]].
