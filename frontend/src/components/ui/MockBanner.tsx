/**
 * MockBanner — tiny dev-mode indicator shown when the API mock layer is
 * active. Archive-style (ink rectangle, DM Mono). Visible in dev so you
 * always know which data source you're looking at.
 *
 * Renders nothing when MOCK_ENABLED is false.
 */

import { MOCK_ENABLED, setMockEnabled } from '@/lib/mock';

export function MockBanner() {
  if (!MOCK_ENABLED) return null;

  return (
    <div className="fixed bottom-3 right-3 z-[60] select-none">
      <div
        className="flex items-center gap-2 px-2.5 py-1.5"
        style={{
          background: 'var(--nm-ink)',
          color: 'var(--nm-paper)',
          border: '1px solid var(--nm-ink)',
          borderRadius: 'var(--radius-sm)',
          boxShadow: 'var(--nm-elev-1)',
        }}
      >
        <span
          className="h-1.5 w-1.5 rounded-full allow-circle animate-pulse"
          style={{ background: 'var(--color-warning)' }}
          aria-hidden
        />
        <span
          className="text-[10px] uppercase tracking-[0.16em]"
          style={{ fontFamily: 'var(--font-mono)' }}
        >
          Mock data
        </span>
        <button
          onClick={() => setMockEnabled(false)}
          className="ml-1 text-[10px] uppercase tracking-[0.10em] opacity-60 hover:opacity-100 pl-2 transition-opacity"
          style={{ fontFamily: 'var(--font-mono)', borderLeft: '1px solid rgba(255,255,255,0.2)' }}
          aria-label="Disable mock mode"
          title="Turn off mock mode (reloads page)"
        >
          turn off
        </button>
      </div>
    </div>
  );
}
