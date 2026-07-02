/**
 * @file_name: ProviderSummaryCard.tsx
 * @author: NarraNexus
 * @date: 2026-06-10
 * @description: At-a-glance summary of the user's current LLM wiring.
 *
 * The simple face of the Settings → Providers section (mirror of the
 * /setup logic: simple surface first, full ProviderSettings behind the
 * "Advanced configuration" disclosure). Answers, without scrolling:
 * which framework + model the agent runs on, what the helper uses, and
 * which provider keys are registered. Read-only — every edit affordance
 * lives in the Advanced area.
 */

import { useEffect, useState } from 'react';
import { Bot, Wrench, KeyRound } from 'lucide-react';
import { PaperCard } from '@/components/nm';
import { api } from '@/lib/api';

interface ProviderInfo {
  name: string;
  source: string;
  protocol: string;
  api_key_masked?: string;
  is_active: boolean;
}

interface SlotInfo {
  config: { provider_id: string; model: string } | null;
}

const FRAMEWORK_LABELS: Record<string, string> = {
  claude_code: 'Claude Code',
  codex_cli: 'Codex CLI',
};

function SummaryRow({
  icon,
  label,
  value,
  detail,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  detail?: string;
}) {
  return (
    <div className="flex items-center gap-3 py-1.5">
      <span style={{ color: 'var(--nm-ink50)' }}>{icon}</span>
      <span
        className="w-24 shrink-0 text-[11px] font-medium uppercase tracking-[0.10em]"
        style={{ fontFamily: 'var(--font-mono)', color: 'var(--nm-ink50)' }}
      >
        {label}
      </span>
      <span className="text-sm font-medium" style={{ color: 'var(--nm-ink)' }}>
        {value}
      </span>
      {detail && (
        <span className="text-xs" style={{ color: 'var(--nm-ink50)' }}>
          {detail}
        </span>
      )}
    </div>
  );
}

interface ProviderSummaryCardProps {
  /** Bump to force a re-fetch (e.g. after Advanced edits or onboard). */
  refreshToken?: number;
}

export function ProviderSummaryCard({ refreshToken = 0 }: ProviderSummaryCardProps) {
  const [providers, setProviders] = useState<Record<string, ProviderInfo>>({});
  const [slots, setSlots] = useState<Record<string, SlotInfo>>({});
  const [framework, setFramework] = useState<string>('claude_code');
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const [prov, fw] = await Promise.all([
          api.getProviders(),
          api.getAgentFramework(),
        ]);
        if (cancelled) return;
        if (prov.success && prov.data) {
          setProviders((prov.data.providers ?? {}) as Record<string, ProviderInfo>);
          setSlots((prov.data.slots ?? {}) as Record<string, SlotInfo>);
        }
        if (fw.success && fw.data?.framework) {
          setFramework(fw.data.framework);
        }
      } catch {
        // Backend not ready — render nothing rather than a broken card
      }
      if (!cancelled) setLoaded(true);
    };
    load();
    return () => {
      cancelled = true;
    };
  }, [refreshToken]);

  if (!loaded) return null;

  const agentCfg = slots.agent?.config ?? null;
  const helperCfg = slots.helper_llm?.config ?? null;
  const providerName = (pid: string | undefined) =>
    (pid && providers[pid]?.name) || '—';

  const activeProviders = Object.values(providers).filter((p) => p.is_active);

  return (
    <PaperCard padding="md">
      <div className="flex flex-col">
        <SummaryRow
          icon={<Bot className="w-4 h-4" />}
          label="Agent"
          value={
            agentCfg?.model
              ? `${agentCfg.model}`
              : 'Not configured'
          }
          detail={
            agentCfg
              ? `${FRAMEWORK_LABELS[framework] ?? framework} · ${providerName(agentCfg.provider_id)}`
              : undefined
          }
        />
        <SummaryRow
          icon={<Wrench className="w-4 h-4" />}
          label="Helper"
          value={helperCfg?.model ? `${helperCfg.model}` : 'Not configured'}
          detail={helperCfg ? providerName(helperCfg.provider_id) : undefined}
        />
        <SummaryRow
          icon={<KeyRound className="w-4 h-4" />}
          label="Keys"
          value={
            activeProviders.length > 0
              ? activeProviders
                  .map((p) => `${p.name}${p.api_key_masked ? ` (${p.api_key_masked})` : ''}`)
                  .join('  ·  ')
              : 'None'
          }
        />
      </div>
    </PaperCard>
  );
}

export default ProviderSummaryCard;
