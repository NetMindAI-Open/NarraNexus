/**
 * @file_name: processShared.tsx
 * @author:
 * @date: 2026-07-30
 * @description: Render pieces shared between the single-agent ProcessPanel
 *   and the team roster's per-member process detail.
 */
/* eslint-disable react-refresh/only-export-components -- this file exists to
   share the terminal-row component AND its helpers; HMR granularity is a fair
   trade for a single source of truth on the process look. */
import type { TFunction } from 'i18next';
import { Loader2 } from 'lucide-react';
import type { Step, TurnEvent } from '@/types';

/** Pipeline step id → i18n label. Same mapping the message-area
 *  indicator used before it moved in here. Unknown ids fall back to the
 *  backend-provided title. */
export const PHASE_LABEL_KEYS: Record<string, string> = {
  '0': 'chat.execution.initializing',
  '1': 'chat.execution.loadingContext',
  '2': 'chat.execution.loadingResources',
  '2.5': 'chat.execution.preparingWorkspace',
  '3': 'chat.execution.buildingContext',
};

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

export type Activity = { text: string; tool?: boolean; pending?: boolean };

/** What is the agent doing RIGHT NOW — the collapsed view's one line.
 *  Latest tool/thinking wins; before the loop starts, the latest phase. */
export function deriveActivity(
  process: TurnEvent[],
  phases: Step[],
  t: TFunction,
): Activity {
  for (let i = process.length - 1; i >= 0; i -= 1) {
    const e = process[i];
    if (e.type === 'tool_call') {
      return { text: friendlyToolName(e.tool_name), tool: true, pending: e.pending };
    }
    if (e.type === 'thinking') {
      return { text: t('chat.execution.thinking', 'Thinking…') };
    }
  }
  const last = phases[phases.length - 1];
  if (last) {
    const key = PHASE_LABEL_KEYS[last.step];
    return { text: key ? t(key) : last.title };
  }
  return { text: t('chat.execution.startingUp', 'Starting up…') };
}

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
 *  Shared by ProcessPanel and the team member panel so "loading
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

/** Terminal-style rows for one turn's process events (thinking / tool
 *  call / tool output). Shared by the single-agent ProcessPanel and the
 *  team roster's member detail. Pure render — no scrolling, no state. */
export function ProcessEventRows({ process }: { process: TurnEvent[] }) {
  return (
    <>
      {process.map((event) => {
        if (event.type === 'thinking') {
          return (
            <div key={event.id} className="flex gap-2 py-0.5">
              <span aria-hidden="true" className="shrink-0 select-none" style={{ color: 'var(--color-carbon)' }}>
                ∴
              </span>
              <span className="whitespace-pre-wrap italic" style={{ color: 'var(--nm-ink50)' }}>
                {event.content}
              </span>
            </div>
          );
        }
        if (event.type === 'tool_call') {
          // Show the first argument value as a one-line summary; an
          // ellipsis until arguments arrive — the visible form of
          // pending: name decided, arguments still being written.
          const firstArg = Object.values(event.tool_input ?? {})[0];
          return (
            <div
              key={event.id}
              data-testid={`tool-row-${event.id}`}
              data-pending={event.pending ? 'true' : 'false'}
              className="flex items-center gap-2 rounded px-1 py-0.5 -mx-1 hover:bg-[var(--nm-paper-warm)]"
            >
              {event.pending ? (
                <Loader2
                  className="h-3 w-3 shrink-0 animate-spin"
                  style={{ color: 'var(--color-warning)' }}
                />
              ) : (
                <span
                  aria-hidden="true"
                  className="shrink-0 select-none font-semibold"
                  style={{ color: 'var(--color-success)' }}
                >
                  $
                </span>
              )}
              <span className="shrink-0 font-semibold" style={{ color: 'var(--color-silicon)' }}>
                {friendlyToolName(event.tool_name)}
              </span>
              <span className="truncate" style={{ color: 'var(--nm-ink50)' }}>
                {event.pending ? '…' : String(firstArg ?? '')}
              </span>
            </div>
          );
        }
        if (event.type === 'tool_output') {
          return (
            <div key={event.id} className="flex gap-2 py-0.5 pl-5">
              <span aria-hidden="true" className="shrink-0 select-none" style={{ color: 'var(--nm-ink30)' }}>
                ↳
              </span>
              <span className="truncate" style={{ color: 'var(--nm-ink50)' }}>
                {event.output}
              </span>
            </div>
          );
        }
        // Callers pass pre-filtered process events; anything else is noise.
        return null;
      })}
    </>
  );
}
