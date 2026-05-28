/**
 * @file_name: UpdateBanner.tsx
 * @description: Global ChatGPT-style update banner.
 *
 * Surfaces the unified updater state machine (see stores/updaterStore +
 * Rust commands/updater.rs). Renders ONLY at state.kind === 'ready' so
 * the user is never interrupted while the pipeline is silently working
 * in the background (download + install run end-to-end before any UI
 * shows up). On Ready: small floating pill at top-center, dismissable,
 * one click to restart.
 *
 * Why not show 'available' / 'downloading' / 'failed' here too:
 *  - 'available' is transient (microseconds before Downloading starts).
 *  - 'downloading' & 'installing' run silently per Owner spec (Q1=A,
 *    background download). Users who want detail open Settings.
 *  - 'failed' is shown in Settings only — a hard error banner every
 *    startup over a network blip would train users to dismiss without
 *    reading.
 *
 * Auto-mount from `App.tsx`; the store's `init()` (called on App mount)
 * wires the live `updater:state` listener.
 */

import { useState } from 'react';
import { X, Download } from 'lucide-react';
import { useUpdaterStore } from '@/stores/updaterStore';
import { restartForUpdate } from '@/lib/tauri';

export default function UpdateBanner() {
  const state = useUpdaterStore((s) => s.state);
  // Session-scoped dismiss. Re-shows on next time state.kind transitions
  // back to 'ready' (e.g. user dismissed, then a newer build dropped
  // and finished downloading later). We compare on (kind, version) so a
  // genuinely-new Ready re-arms the banner even if `version` differs.
  const [dismissedKey, setDismissedKey] = useState<string | null>(null);

  if (state.kind !== 'ready') return null;
  const key = `ready:${state.version}`;
  if (dismissedKey === key) return null;

  return (
    <div
      // Centered floating pill at the very top, above the app chrome.
      // pointer-events-none on the wrapper + auto on the pill so clicks
      // outside the pill pass through to the underlying UI (no
      // accidental blocking of the workspace).
      className="fixed top-3 left-0 right-0 z-50 flex justify-center pointer-events-none font-[family-name:var(--font-sans)]"
      role="status"
      aria-live="polite"
    >
      <div
        className="pointer-events-auto flex items-center gap-3 px-4 py-2 rounded-full shadow-lg border border-[var(--nm-line)] bg-[var(--bg-elev)] text-[var(--nm-ink)] text-sm"
      >
        <Download className="w-4 h-4 text-[var(--accent-primary)]" aria-hidden="true" />
        <span>
          NarraNexus <b>{state.version}</b> downloaded and ready
        </span>
        <button
          type="button"
          onClick={() => restartForUpdate()}
          className="px-3 py-1 rounded-full text-xs font-medium bg-[var(--accent-primary)] text-white hover:opacity-90 transition"
        >
          Restart now
        </button>
        <button
          type="button"
          onClick={() => setDismissedKey(key)}
          aria-label="Dismiss update notification"
          title="Dismiss (we'll wait — restart later from Settings)"
          className="p-1 rounded-full text-[var(--nm-ink50)] hover:text-[var(--nm-ink)] hover:bg-[var(--nm-line)]/40 transition"
        >
          <X className="w-3.5 h-3.5" />
        </button>
      </div>
    </div>
  );
}
