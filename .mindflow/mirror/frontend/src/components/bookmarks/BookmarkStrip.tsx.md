---
code_file: frontend/src/components/bookmarks/BookmarkStrip.tsx
last_verified: 2026-06-23
stub: false
---

## 2026-06-23 — agent identity header + quick switcher (`AgentRailHeader`)

Owner: the rail is entirely agent-scoped (Config / Memory / Network / … all
belong to ONE agent), but it started cold at AWARENESS with no statement of
*whose* rail it is. Added `AgentRailHeader` pinned above the categories:
a tiny mono `agent` eyebrow + the agent name in sentence-case `font-semibold`
(distinguishing it as a heading from the uppercase tab captions), divider
below. This is the silicon-side mirror of the left rail's `You · <owner>`
(carbon) header — the owner/agent scope split made legible.

- Name comes from `configStore.agents.find(...).name` ([[configStore]]),
  truncated + `title` tooltip for long names. **No avatar** (Owner: it broke
  the icon-strip style).
- **Doubles as a quick agent switcher**: the header is a Radix Popover
  trigger (`ChevronsUpDown` affordance); the content lists every agent, the
  current one marked carbon + check. Selecting runs the SAME action as the
  left sidebar's `handleSelectAgent` — `configStore.setAgentId` +
  `chatStore.setActiveAgent` + `navigate('/app/chat')` — so the user can hop
  agents without crossing to the left rail.
- Popover is a Radix **portal** on purpose: the strip is `overflow-x-hidden`
  and 64px wide, so an inline dropdown would be clipped; the portal escapes
  to body and opens `side="left"`.
- Header (and its divider) is hidden until the agent record/name is loaded —
  but the hooks (`useNavigate`, stores) run before that guard, so any test
  rendering `BookmarkStrip` must wrap it in a router (see
  `bookmarkStrip.test.tsx` `renderStrip`).
- `data-help-id="bookmarks.agent"` added — keep in sync with [[helpContent]].

## 2026-06-20 — cleaner strip: no text headers, carbon highlight, centered

Owner-driven polish:
- **Category text headers removed** (CONFIG / ACTIVITY / …). Groups are now
  divided by a hairline only (`border-t` between categories) — the strip reads
  as icon-only. `category.label` survives solely as the React key.
- **Carbon (orange) highlight**: hover and active now tint the tab — bg
  `--color-carbon-soft`, icon + caption `--color-carbon`, and the active
  "bookmark tongue" edge rule is carbon (was ink/`--text-primary`). Resting
  icons stay `--text-tertiary` so the species color reads as accent, not noise
  (Axiom #1). Implemented with a `group` + `group-hover:` on the button.
- **Centered**: caption gained `text-center`; icon/caption already centered.
- MCP caption now "MCP" (see [[tabs]] `stripLabel`).

# BookmarkStrip.tsx — Right-edge strip, atomic-tab IA

## 2026-06-11 (PM) — 64px strip: icon + caption, horizontal category headers

Owner: icons alone were not understandable. Strip widened 36→64px;
each atomic tab is icon + 8px mono caption (stripLabel ?? label);
category headers are horizontal micro-labels instead of rotated text.

## 为什么存在

~36px of right edge is the ONLY persistent footprint; chat + artifacts
get the rest. 2026-06-11 Owner revision: the first iteration (2 big
bookmarks opening multi-section panels) was rejected — "每一个小标签页
里面就一个内容". The smallest unit is now an atomic tab: ONE tab opens
exactly ONE panel; categories ([[tabs]] STRIP_CATEGORIES) only group
visually.

## 设计决策

- All structure lives in the [[tabs]] registry — strip just renders it.
- Live signals overlay the owning tab (derived per render via
  deriveTabStatus): failed jobs → carbon pulse + count badge; running
  job → corner spinner; inbox unread → pulse + count; awareness
  external update → yellow info dot.
- Active tab gets a 2px inset ink rule on the outer edge — the visible
  tongue of an inserted bookmark.
- Category labels are rotated 8px mono dividers, aria-hidden (tabs
  carry their own aria-labels).
- Strip scrolls vertically (scrollbar hidden) on short windows.

## 新人易踩的坑

data-help-id anchors (`bookmarks.<tabId>`, `bookmarks.strip`) are help
overlay contract — keep in sync with [[helpContent]].
