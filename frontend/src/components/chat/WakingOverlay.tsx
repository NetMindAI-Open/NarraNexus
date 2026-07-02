/**
 * @file WakingOverlay.tsx
 * @description Cold-start "waking up" overlay for the chat surface.
 *
 * When a user's per-user Executor container was idle-culled and a new turn
 * has to spin it back up, the backend emits a ProgressMessage with
 * step "executor.warming" (status "running"). This blurs the chat surface
 * and shows a gentle notice until the now-awake agent emits its first
 * event (the backend's paired "executor.warming" COMPLETED) — or the run
 * ends (isStreaming gate), so a failed/aborted start never leaves it stuck.
 *
 * Scoped to the chat card (absolute inset-0 inside its relative wrapper),
 * not a full-page modal — the rest of the app stays interactive.
 *
 * Copy is English to comply with the English-only-code rule and match the
 * existing frontend strings.
 */
import { useMemo } from 'react';

import { Loader2 } from 'lucide-react';

import { useChatStore } from '@/stores';

const WARMING_STEP = 'executor.warming';

export function WakingOverlay() {
  const currentSteps = useChatStore((s) => s.currentSteps);
  const isStreaming = useChatStore((s) => s.isStreaming);

  const isWaking = useMemo(() => {
    // The latest warming step wins; it clears once the backend pairs it
    // with a COMPLETED (the awake executor's first event).
    for (let i = currentSteps.length - 1; i >= 0; i--) {
      if (currentSteps[i].step === WARMING_STEP) {
        return currentSteps[i].status === 'running';
      }
    }
    return false;
  }, [currentSteps]);

  if (!isWaking || !isStreaming) return null;

  return (
    <div
      data-nm="waking-overlay"
      className="absolute inset-0 z-[40] flex items-center justify-center animate-fade-in"
      style={{ background: 'var(--nm-backdrop)', backdropFilter: 'blur(3px)' }}
      aria-live="polite"
    >
      <div className="flex flex-col items-center gap-3 px-6 text-center">
        <Loader2 className="w-8 h-8 animate-spin text-[var(--accent-primary)]" />
        <p className="text-base font-medium text-[var(--text-primary)]">
          Your agent dozed off — waking it up…
        </p>
        <p className="text-xs text-[var(--text-tertiary)]">
          Spinning up your private runtime, almost there.
        </p>
      </div>
    </div>
  );
}
