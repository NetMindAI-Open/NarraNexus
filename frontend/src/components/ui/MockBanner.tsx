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
    <div
      className="fixed bottom-3 right-3 z-[60] select-none"
      style={{ borderRadius: 0 }}
    >
      <div className="flex items-center gap-2 bg-[var(--color-ink,_#111214)] text-white px-2.5 py-1.5 border border-[var(--color-ink,_#111214)] shadow-[0_2px_8px_rgba(0,0,0,0.2)]">
        <span
          className="h-1.5 w-1.5 rounded-full allow-circle bg-[var(--color-yellow-500)] animate-pulse"
          aria-hidden
        />
        <span className="text-[10px] font-[family-name:var(--font-mono)] uppercase tracking-[0.18em]">
          Mock data
        </span>
        <button
          onClick={() => setMockEnabled(false)}
          className="ml-1 text-[10px] font-[family-name:var(--font-mono)] uppercase tracking-[0.12em] text-white/60 hover:text-white border-l border-white/20 pl-2 transition-colors"
          aria-label="Disable mock mode"
          title="Turn off mock mode (reloads page)"
        >
          turn off
        </button>
      </div>
    </div>
  );
}
