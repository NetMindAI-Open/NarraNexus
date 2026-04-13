/**
 * @file_name: AttentionBanners.tsx
 * @description: v2.1 — per-card banners (error / warning / info) stacked
 * above the main card content. Surfaces failed/blocked/paused jobs without
 * forcing the user to read the queue bar.
 */
import type { AttentionBanner } from '@/types';

const LEVEL_STYLE: Record<AttentionBanner['level'], { wrap: string; icon: string; accent: string }> = {
  error: {
    wrap: 'border-red-500/40 bg-red-500/10',
    icon: '🔴',
    accent: 'text-red-600 dark:text-red-400',
  },
  warning: {
    wrap: 'border-amber-500/40 bg-amber-500/10',
    icon: '🟠',
    accent: 'text-amber-600 dark:text-amber-400',
  },
  info: {
    wrap: 'border-sky-500/40 bg-sky-500/10',
    icon: 'ℹ️',
    accent: 'text-sky-600 dark:text-sky-400',
  },
};

export function AttentionBanners({ banners }: { banners: AttentionBanner[] }) {
  if (!banners || banners.length === 0) return null;
  return (
    <div className="mt-2 space-y-1">
      {banners.map((b, i) => {
        const s = LEVEL_STYLE[b.level];
        return (
          <div
            key={`${b.kind}-${i}`}
            data-testid={`banner-${b.kind}`}
            className={`flex items-center gap-2 rounded-md border px-2 py-1 text-xs ${s.wrap}`}
          >
            <span aria-hidden>{s.icon}</span>
            <span className={`flex-1 ${s.accent}`}>{b.message}</span>
          </div>
        );
      })}
    </div>
  );
}
