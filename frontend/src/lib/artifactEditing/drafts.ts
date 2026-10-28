/**
 * @file_name: drafts.ts
 * @author: NetMind.AI
 * @date: 2026-08-20
 * @description: The localStorage draft layer for artifact editing surfaces —
 * key template, size gate, removal and stale sweep in ONE place (review #334
 * r2 I5: the store had hand-copied the key template the hook had just
 * exported; same fact, two spellings). No React in here: the zustand store
 * consumes removeDraft without dragging hooks into its module graph.
 */

export const DRAFT_PREFIX = 'narra:artifact-draft:';

// Measured in UTF-16 code units (String.length), NOT bytes — a CJK document
// at this cap is ~1.5 MB stored, still well under the ~5 MB origin quota
// (review #334 r2 M3: the old name said BYTES and misled tuners).
export const DRAFT_MAX_CHARS = 512 * 1024;

// Drafts are short-lived by nature — anything older is a leftover from a
// deleted/abandoned artifact and only eats the shared quota. Deliberately
// NOT keyed on any artifact list: the store only knows the CURRENT agent's
// artifacts, and deleting by that list would kill other agents' live drafts.
const DRAFT_STALE_MS = 14 * 24 * 60 * 60 * 1000;

export interface Draft {
  text: string;
  baseHash: string;
  /** Written at save time; lets the stale sweep age drafts out. */
  ts?: number;
}

export function readDraft(artifactId: string): Draft | null {
  try {
    const raw = localStorage.getItem(DRAFT_PREFIX + artifactId);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (typeof parsed?.text !== 'string' || typeof parsed?.baseHash !== 'string') return null;
    return parsed as Draft;
  } catch {
    return null;
  }
}

/** Returns false when the draft could NOT be persisted (too large / quota /
    storage disabled) — the caller surfaces that, never swallows it.

    Every failure path also INVALIDATES the previous draft under the key
    (review #334 r3 I3): a stale smaller draft with a still-matching
    baseHash would otherwise be restored on the next mount as "your unsaved
    changes" — silently handing the user an OLDER text is worse than
    honestly having nothing. */
export function writeDraft(artifactId: string, draft: Draft | null): boolean {
  const key = DRAFT_PREFIX + artifactId;
  try {
    if (draft === null) {
      localStorage.removeItem(key);
      return true;
    }
    if (draft.text.length > DRAFT_MAX_CHARS) {
      localStorage.removeItem(key);
      return false;
    }
    localStorage.setItem(key, JSON.stringify(draft));
    return true;
  } catch {
    try {
      localStorage.removeItem(key);
    } catch {
      /* storage disabled */
    }
    return false;
  }
}

export function removeDraft(artifactId: string): void {
  try {
    localStorage.removeItem(DRAFT_PREFIX + artifactId);
  } catch {
    /* storage disabled — nothing to clean */
  }
}

let sweptThisSession = false;

/** Once per session: age out drafts older than DRAFT_STALE_MS.
 *
 * Reads ONLY the ts field via regex — parsing every (possibly 512K-char)
 * draft JSON on the first editor mount was a visible main-thread stall
 * (review #334 r2 M4). A draft with no readable ts is treated as stale;
 * the sweep runs before any restore by design (documented contract, not an
 * effect-ordering accident — callers invoke it explicitly at mount). */
export function sweepStaleDrafts(): void {
  if (sweptThisSession) return;
  sweptThisSession = true;
  try {
    const stale: string[] = [];
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i);
      if (!key || !key.startsWith(DRAFT_PREFIX)) continue;
      const raw = localStorage.getItem(key) ?? '';
      const m = /"ts":\s*(\d+)/.exec(raw);
      const ts = m ? Number(m[1]) : 0;
      if (Date.now() - ts > DRAFT_STALE_MS) stale.push(key);
    }
    stale.forEach((k) => localStorage.removeItem(k));
  } catch {
    /* storage disabled */
  }
}
