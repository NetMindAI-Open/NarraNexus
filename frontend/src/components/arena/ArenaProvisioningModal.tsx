/**
 * Small non-blocking toast shown while the Arena agent is being provisioned.
 *
 * The full app renders underneath; this only communicates progress so the user
 * isn't staring at a bare spinner. Auto-dismisses shortly after the agent is
 * ready (or after an error).
 */
import { useEffect } from 'react';
import { useArenaLandingStore } from '../../stores/arenaLandingStore';

export function ArenaProvisioningModal() {
  const { status, arenaName, error, reset } = useArenaLandingStore();

  // Auto-dismiss once ready (after the agent has opened) or on error.
  useEffect(() => {
    if (status === 'ready') {
      const t = setTimeout(reset, 1800);
      return () => clearTimeout(t);
    }
    if (status === 'error') {
      const t = setTimeout(reset, 4000);
      return () => clearTimeout(t);
    }
  }, [status, reset]);

  if (status === 'idle') return null;

  return (
    <div
      className="fixed top-4 left-0 right-0 z-[60] flex justify-center pointer-events-none font-[family-name:var(--font-sans)]"
      role="status"
      aria-live="polite"
    >
      <div className="pointer-events-auto flex items-center gap-3 rounded-lg border border-[var(--border-subtle,#2a2a2a)] bg-[var(--bg-elevated,#1a1a1a)] px-4 py-3 shadow-lg max-w-[90vw]">
        {status === 'provisioning' && (
          <>
            <div className="w-4 h-4 shrink-0 border-2 border-[var(--accent-primary)] border-t-transparent rounded-full animate-spin" />
            <span className="text-sm text-[var(--text-primary,#e5e5e5)]">
              Setting up your Arena agent…
            </span>
          </>
        )}
        {status === 'ready' && (
          <>
            <span className="text-[var(--accent-primary)] text-sm">✓</span>
            <span className="text-sm text-[var(--text-primary,#e5e5e5)]">
              {arenaName ? `${arenaName} is ready — opening…` : 'Arena agent ready — opening…'}
            </span>
          </>
        )}
        {status === 'error' && (
          <>
            <span className="text-[var(--color-red-500,#ef4444)] text-sm">!</span>
            <span className="text-sm text-[var(--text-primary,#e5e5e5)]">
              Couldn't set up your Arena agent. {error ? '' : ''}Please retry.
            </span>
          </>
        )}
      </div>
    </div>
  );
}
