/**
 * @file_name: senderIdentity.ts
 * @author: NarraNexus
 * @date: 2026-08-12
 * @description: One agent, one colour, on every surface that shows it.
 *
 * A team room with six members is unreadable if every message is the same grey:
 * the only thing separating two agents today is a 10px name above the bubble,
 * and on mobile not even that. Colour is what makes a multi-party transcript
 * scannable.
 *
 * That only works if the colour is an IDENTITY — stable across the room, the
 * inbox and the dashboard. It was not. Two copies of this hash existed
 * (`AgentInboxPanel.senderColor` and `dashboard/SessionSection.colorForSeed`)
 * and they had ALREADY diverged: both open green/sky/yellow/rose/violet, then
 * one continues teal/indigo/fuchsia and the other fuchsia/teal/indigo. Any
 * agent hashing into slots 5-7 was showing two different colours in two places,
 * with nothing anywhere to make that visible. Adding a third copy for the team
 * room would have made it three.
 *
 * Seeded on the AGENT ID, never the display name: renaming an agent must not
 * change its colour, and a rename is precisely when a reader most needs the
 * colour to stay put. It also leaves room for "let the user pick a colour"
 * later as an override at read time, with no data migration.
 */

export interface SenderIdentity {
  /** Avatar / status-dot background class. */
  dot: string;
  /** Left-accent border class, so the bubble edge carries the same identity. */
  accent: string;
  /** Short label for an avatar with no image. */
  initials: string;
}

/**
 * Eight slots. Each row carries BOTH a fill and a matching accent so a caller
 * cannot colour half a surface: the avatar and the bubble edge must agree, or
 * the identity reads as two different agents in one message.
 *
 * Ordering is load-bearing — it is what makes the colour stable across
 * releases, so rows are appended, never inserted or reordered.
 */
export const PALETTE: ReadonlyArray<{ dot: string; accent: string }> = [
  { dot: 'bg-[var(--color-green-500)]', accent: 'border-l-[var(--color-green-500)]' },
  { dot: 'bg-sky-500', accent: 'border-l-sky-500' },
  { dot: 'bg-[var(--color-yellow-500)]', accent: 'border-l-[var(--color-yellow-500)]' },
  { dot: 'bg-rose-500', accent: 'border-l-rose-500' },
  { dot: 'bg-violet-500', accent: 'border-l-violet-500' },
  { dot: 'bg-teal-500', accent: 'border-l-teal-500' },
  { dot: 'bg-indigo-500', accent: 'border-l-indigo-500' },
  { dot: 'bg-fuchsia-500', accent: 'border-l-fuchsia-500' },
];

/** djb2-ish, byte-identical to both implementations this replaces — the hash was
 *  never where they differed.
 *
 *  The PALETTE was: the two copies had drifted apart at slots 5–7
 *  (`AgentInboxPanel` had teal/indigo/fuchsia where `SessionSection` had
 *  fuchsia/teal/indigo). Converging means picking one, and the inbox's order
 *  won, so a dashboard seed that lands in those three slots DOES change colour
 *  once. That is the cost of having had two copies; there is no ordering that
 *  leaves both surfaces untouched. */
function hash(seed: string): number {
  let h = 0;
  for (let i = 0; i < seed.length; i++) {
    h = (h * 31 + seed.charCodeAt(i)) | 0;
  }
  return Math.abs(h);
}

/**
 * Initials for an avatar with no image.
 *
 * A CJK name is sliced to ONE character, not two: two full-width characters
 * overflow the avatar, which is why this is not a plain `slice(0, 2)`.
 */
function initialsFor(display: string): string {
  const parts = display.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return '?';
  if (parts.length === 1) {
    const one = parts[0];
    // Any non-ASCII script is treated as full-width for this purpose.
    return /^[\x20-\x7F]+$/.test(one) ? one.slice(0, 2).toUpperCase() : one.slice(0, 1);
  }
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

/**
 * The visual identity of one sender.
 *
 * @param seed - Stable id (agent_id / user_id). NOT the display name.
 * @param display - Optional display name, used only for initials.
 */
export function senderIdentity(seed: string, display = ''): SenderIdentity {
  const slot = PALETTE[hash(seed) % PALETTE.length];
  return { ...slot, initials: initialsFor(display) };
}
