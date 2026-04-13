/**
 * @file_name: Sparkline.tsx
 * @description: v2.1 — 24-bar micro-viz of events/hour. Loaded lazily per
 * agent via `/api/dashboard/agents/{id}/sparkline`; cached until the card
 * is unmounted. Colored by agent health.
 */
import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { HEALTH_COLORS } from './healthColors';
import type { AgentHealth } from '@/types';

interface Props {
  agentId: string;
  health: AgentHealth;
  hours?: number;
}

export function Sparkline({ agentId, health, hours = 24 }: Props) {
  const [buckets, setBuckets] = useState<number[] | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await api.getAgentSparkline(agentId, hours);
        if (cancelled) return;
        if (res.success) setBuckets(res.buckets);
        else setError(true);
      } catch {
        if (!cancelled) setError(true);
      }
    })();
    return () => { cancelled = true; };
  }, [agentId, hours]);

  const barColor = HEALTH_COLORS[health].accent;

  if (error) {
    return <div className="text-[10px] text-[var(--text-secondary)]">24h · —</div>;
  }
  if (buckets === null) {
    return <div className="flex items-end gap-0.5 h-6 opacity-30">
      {Array.from({ length: hours }).map((_, i) => (
        <div key={i} className={`w-[3px] ${barColor} rounded-sm`} style={{ height: '2px' }} />
      ))}
    </div>;
  }

  const max = Math.max(1, ...buckets);
  return (
    <div className="flex items-end gap-0.5 h-6" data-testid="sparkline">
      {buckets.map((v, i) => {
        const h = Math.max(2, Math.round((v / max) * 22));
        const isLast = i === buckets.length - 1;
        return (
          <div
            key={i}
            className={`w-[3px] ${barColor} rounded-sm ${isLast ? 'opacity-100' : 'opacity-60'}`}
            style={{ height: `${h}px` }}
            title={`${hours - buckets.length + i + 1}h ago · ${v} events`}
          />
        );
      })}
    </div>
  );
}
