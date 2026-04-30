/**
 * @file_name: AttentionBanners.tsx
 * @description: v2.3 — per-card banners (error / warning / info) stacked
 * above the main card content. Surfaces failed/blocked/paused jobs without
 * forcing the user to read the queue bar.
 *
 * v2.3: emoji replaced with lucide icons for consistent stroke + sizing.
 *
 * v2.1.1: dismissible. User clicks [×] → banner is hidden in sessionStorage
 * keyed by `${agentId}:banner:${kind}:${signature}`. Signature includes the
 * `message` (which carries the count), so when the underlying count changes
 * (e.g. another job fails: "1 failed" → "2 failed") the new banner re-shows.
 */
import type { AttentionBanner } from '@/types';
import { useExpanded, bannerKey } from './expandState';
import { AlertCircle, AlertTriangle, Info, X } from 'lucide-react';

const LEVEL_STYLE: Record<
  AttentionBanner['level'],
  { wrap: string; Icon: typeof AlertCircle; accent: string }
> = {
  error: {
    wrap: 'border-[var(--color-red-500)]/60 bg-[var(--color-red-500)]/8',
    Icon: AlertCircle,
    accent: 'text-[var(--color-red-500)]',
  },
  warning: {
    wrap: 'border-[var(--color-yellow-500)]/60 bg-[var(--color-yellow-500)]/8',
    Icon: AlertTriangle,
    accent: 'text-[var(--color-yellow-500)]',
  },
  info: {
    wrap: 'border-sky-500/40 bg-sky-500/8',
    Icon: Info,
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
  const key = bannerKey(agentId, banner.kind, banner.message);
  const { expanded: dismissed, set } = useExpanded(key, false);
  if (dismissed) return null;
  const s = LEVEL_STYLE[banner.level];
  const Icon = s.Icon;
  return (
    <div
      data-testid={`banner-${banner.kind}`}
      className={`flex items-center gap-2 rounded-md border px-2.5 py-1.5 text-xs ${s.wrap}`}
    >
      <Icon className={`w-3.5 h-3.5 shrink-0 ${s.accent}`} aria-hidden />
      <span className={`flex-1 leading-snug ${s.accent}`}>{banner.message}</span>
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          set(true);
        }}
        aria-label="Dismiss banner"
        title="Dismiss (re-appears if state changes)"
        className={`shrink-0 p-0.5 rounded hover:bg-black/10 dark:hover:bg-white/10 ${s.accent}`}
      >
        <X className="w-3 h-3" aria-hidden />
      </button>
    </div>
  );
}
