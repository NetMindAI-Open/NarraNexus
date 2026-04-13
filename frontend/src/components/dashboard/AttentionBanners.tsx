/**
 * @file_name: AttentionBanners.tsx
 * @description: v2.1 — per-card banners (error / warning / info) stacked
 * above the main card content. Surfaces failed/blocked/paused jobs without
 * forcing the user to read the queue bar.
 *
 * v2.1.1: dismissible. User clicks [×] → banner is hidden in sessionStorage
 * keyed by `${agentId}:banner:${kind}:${signature}`. Signature includes the
 * `message` (which carries the count), so when the underlying count changes
 * (e.g. another job fails: "1 failed" → "2 failed") the new banner re-shows.
 * When the user clears all failures (count → 0) the banner is naturally
 * removed by the backend; sessionStorage entries become inert.
 */
import type { AttentionBanner } from '@/types';
import { useExpanded } from './expandState';

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

export function AttentionBanners({
  agentId,
  banners,
}: {
  agentId: string;
  banners: AttentionBanner[];
}) {
  if (!banners || banners.length === 0) return null;
  return (
    <div className="mt-2 space-y-1">
      {banners.map((b, i) => (
        <BannerRow key={`${b.kind}-${i}`} agentId={agentId} banner={b} />
      ))}
    </div>
  );
}

function BannerRow({ agentId, banner }: { agentId: string; banner: AttentionBanner }) {
  // Signature embeds the message (which contains the live count). When the
  // underlying count changes the signature changes → banner re-appears.
  const sig = encodeURIComponent(banner.message);
  // useExpanded stores `true` when expanded. We invert to "dismissed".
  const { expanded: dismissed, set } = useExpanded(
    `${agentId}:banner:${banner.kind}:${sig}`,
    false,
  );
  if (dismissed) return null;
  const s = LEVEL_STYLE[banner.level];
  return (
    <div
      data-testid={`banner-${banner.kind}`}
      className={`flex items-center gap-2 rounded-md border px-2 py-1 text-xs ${s.wrap}`}
    >
      <span aria-hidden>{s.icon}</span>
      <span className={`flex-1 ${s.accent}`}>{banner.message}</span>
      <button
        type="button"
        onClick={(e) => { e.stopPropagation(); set(true); }}
        aria-label="Dismiss banner"
        title="Dismiss (re-appears if state changes)"
        className={`shrink-0 rounded px-1 hover:bg-black/10 dark:hover:bg-white/10 ${s.accent}`}
      >
        ×
      </button>
    </div>
  );
}
