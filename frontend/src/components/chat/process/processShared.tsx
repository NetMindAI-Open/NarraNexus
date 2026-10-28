/**
 * @file_name: processShared.tsx
 * @author:
 * @date: 2026-07-30
 * @description: Render pieces shared between the single-agent run preamble
 *   (RunPhases) and the team roster's per-member process detail.
 */
/* eslint-disable react-refresh/only-export-components -- this file exists to
   share the terminal-row component AND its helpers; HMR granularity is a fair
   trade for a single source of truth on the process look. */
import { Loader2 } from 'lucide-react';
import type { Step } from '@/types';

/** Pipeline step id → i18n label. The labels name what the backend
 *  ACTUALLY does at each step (step ids from step_3_agent_loop /
 *  the runtime steps), so the panel never says "loading context" while
 *  the backend is selecting a narrative, nor "building context" while the
 *  model is already running. Unknown ids fall back to the backend title. */
export const PHASE_LABEL_KEYS: Record<string, string> = {
  '0': 'chat.execution.initializing',       // step 0  Initialization
  '1': 'chat.execution.selectingNarrative', // step 1  Narrative Selection
  '2': 'chat.execution.loadingModules',     // step 2  Module Loading
  '2.5': 'chat.execution.syncingInstances', // step 2.5 Sync Instance Changes
  '3': 'chat.execution.buildingContext',    // step 3  Build Context (3.1/3.2/3.3)
  '3.4': 'chat.execution.runningAgent',     // step 3.4 Run Agent (LLM loop)
};

/** The step ids that render as phase rows — DERIVED from PHASE_LABEL_KEYS so
 *  there is one source of truth (add a phase in one place, not two). It's a
 *  whitelist on purpose: everything else the backend yields under step 3+
 *  (tool sub-steps "3.4.x", the "3.5" final-thinking echo, post-answer
 *  housekeeping "4" persist / "5" hooks) is NOT "what the agent is doing to
 *  answer you" — it belongs in the tool rows or the reply bubble. Whitelisting
 *  also keeps raw English backend titles from leaking in for any unmapped id.
 *  A Set for membership only: display order is the arrival order of `steps`,
 *  NOT this collection. */
export const PHASE_STEP_IDS = new Set(Object.keys(PHASE_LABEL_KEYS));

/** Is this phase row settled (✓) rather than running (spinner)? The backend
 *  keeps early phases "running" until the turn ends, so ordering is the honest
 *  signal: a phase is done once its own status says so, OR a later phase has
 *  started, OR (for the pre-loop phases, step < 3.4) any process event has
 *  arrived. Shared by RunPhases and TeamMemberPanel — same rule both sides
 *  (铁律 #8) — and BOTH must pass the UNFILTERED steps so the "a later phase
 *  started" check can see run-agent/housekeeping ids beyond the whitelist.
 *  `parseFloat('3.4.1') === 3.4` (second dot truncated), so a tool sub-step
 *  never prematurely settles the 3.4 row — intentional; don't segment-compare. */
export function phaseSettled(
  phase: Step,
  allSteps: Step[],
  hasProcessEvents: boolean,
): boolean {
  if (phase.status === 'completed') return true;
  const n = parseFloat(phase.step);
  return allSteps.some((s) => parseFloat(s.step) > n) || (hasProcessEvents && n < 3.4);
}

/** "mcp__chat_module__get_chat_history" → "get_chat_history" — same
 *  friendly-name rule TurnTimeline uses; the namespace is debug detail. */
export function friendlyToolName(toolName: string): string {
  const parts = toolName.split('__');
  return parts[parts.length - 1] || toolName;
}

export function formatElapsed(s: number): string {
  const m = Math.floor(s / 60);
  return `${String(m).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
}

/* `Activity` / `deriveActivity` retired 2026-08-31 with the framed process
 * panel: they derived that panel's COLLAPSED one-liner, and there is no
 * collapsed state once the process reads inline in the document. Deleted
 * outright rather than left exported (iron rule #2) — a dead export in a
 * shared file is something the next person wires up by mistake, and the
 * concept it hands them ("the collapsed activity line") no longer exists. */

/** The panel's "alive" indicator: a ping halo while live, a still dot
 *  otherwise. Color is the caller's tone (success green for a healthy
 *  run, warning amber for a stalled one, muted for idle). */
export function LiveDot({ color, live }: { color: string; live: boolean }) {
  return (
    <span className="relative flex h-2 w-2 shrink-0">
      {live && (
        <span
          className="absolute inline-flex h-full w-full animate-ping rounded-full opacity-60"
          style={{ background: color }}
        />
      )}
      <span
        className="relative inline-flex h-2 w-2 rounded-full"
        style={{ background: color }}
      />
    </span>
  );
}

/** One pipeline phase line: ✓ once settled, a spinner while running.
 *  Shared by RunPhases and the team member panel so "loading
 *  context…" reads identically everywhere. */
export function PhaseRow({ done, label }: { done: boolean; label: string }) {
  return (
    <div className="flex items-center gap-2 py-0.5">
      {done ? (
        <span aria-hidden="true" className="shrink-0 select-none" style={{ color: 'var(--color-success)' }}>
          ✓
        </span>
      ) : (
        <Loader2 className="h-3 w-3 shrink-0 animate-spin" style={{ color: 'var(--color-silicon)' }} />
      )}
      <span style={{ color: done ? 'var(--nm-ink50)' : 'var(--color-silicon)' }}>
        {label}
      </span>
    </div>
  );
}

/** The terminal's "still running" heartbeat: a prompt glyph and a
 *  pulsing block cursor pinned under the last row. */
export function LiveCursorRow() {
  return (
    <div aria-hidden="true" className="flex gap-2 py-0.5">
      <span className="select-none" style={{ color: 'var(--color-silicon)' }}>❯</span>
      <span
        className="inline-block w-2 animate-pulse select-none"
        style={{ color: 'var(--nm-ink70)' }}
      >
        ▌
      </span>
    </div>
  );
}

