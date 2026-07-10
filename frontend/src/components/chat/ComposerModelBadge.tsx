/**
 * ComposerModelBadge — per-agent model indicator + quick-switch, docked
 * bottom-right of the composer tools row.
 *
 * The conversation model is PER-AGENT: each agent picks its own model,
 * overriding the owner's global default (Settings › Providers). This badge
 * shows the active agent's effective model and lets you switch it inline —
 * picking a model here writes a per-agent override (PUT
 * /api/agents/{id}/llm-config/agent). Framework + reasoning + helper live in
 * the detailed AgentLlmConfigPanel, opened from the header (the ⚙ button left
 * of the cost chip) — not from here. ``reloadKey`` bumps when that panel saves
 * so this chip re-reads the model. When the owner has no agent slot at all it
 * falls back to a "set model" link into Settings.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { ChevronDown, Check } from 'lucide-react';
import { api } from '@/lib/api';
import { cn } from '@/lib/utils';
import {
  getModelsForSlot,
  prettifyModel,
  type ProviderSummary,
} from '@/lib/agentFramework';
import type { AgentSlotEffective } from '@/types';

interface Props {
  agentId: string;
  /** Bumped by the header panel on save so the chip re-reads the model. */
  reloadKey?: number;
}

export function ComposerModelBadge({ agentId, reloadKey }: Props) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [eff, setEff] = useState<AgentSlotEffective | null>(null);
  const [inheriting, setInheriting] = useState(true);
  const [models, setModels] = useState<Array<{ model_id: string; display_name: string }>>([]);
  const [loaded, setLoaded] = useState(false);
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    if (!agentId) {
      setLoaded(true);
      return;
    }
    try {
      const [cfgRes, provRes] = await Promise.all([
        api.getAgentLlmConfig(agentId),
        api.getProviders(),
      ]);
      const slot = cfgRes?.data?.slots?.agent;
      const effective = slot?.effective ?? null;
      const providers = (provRes?.data?.providers ?? {}) as Record<string, ProviderSummary>;
      const prov = effective?.provider_id ? providers[effective.provider_id] : undefined;
      setEff(effective);
      setInheriting(slot?.inheriting !== false);
      setModels(prov ? getModelsForSlot(prov, 'agent', effective?.agent_framework, {}) : []);
    } catch {
      /* leave unset → "set model" */
    } finally {
      setLoaded(true);
    }
  }, [agentId]);

  useEffect(() => {
    setLoaded(false);
    void load();
    // reloadKey: re-read after the header panel saves a change.
  }, [load, reloadKey]);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [open]);

  // No agent slot configured at all (owner default missing) → Settings link.
  if (loaded && !eff) {
    return (
      <button
        type="button"
        onClick={() => navigate('/app/settings')}
        title={t('chat.model.configureInSettings')}
        className="inline-flex items-center gap-1 lowercase text-[11px] font-[family-name:var(--font-mono)] text-[var(--text-tertiary)] transition-colors hover:text-[var(--color-carbon)]"
      >
        <span className="max-w-[160px] truncate">{t('chat.model.setModel')}</span>
        <ChevronDown className="h-3 w-3 shrink-0" />
      </button>
    );
  }

  const label = eff?.model ? prettifyModel(eff.model) : '…';

  const choose = async (model: string) => {
    if (!eff || saving) return;
    setOpen(false);
    if (model === eff.model) return;
    const prev = eff;
    const prevInheriting = inheriting;
    setSaving(true);
    setEff({ ...eff, model }); // optimistic
    setInheriting(false);
    try {
      const res = await api.setAgentLlmConfig(agentId, 'agent', {
        provider_id: eff.provider_id,
        model,
        thinking: eff.thinking || '',
        reasoning_effort: eff.reasoning_effort || '',
        agent_framework: eff.agent_framework || null,
      });
      if (!res.success) {
        setEff(prev);
        setInheriting(prevInheriting);
      }
    } catch {
      setEff(prev);
      setInheriting(prevInheriting);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        title={inheriting ? t('chat.model.switchTooltip') : 'Custom model for this agent'}
        className={cn(
          'inline-flex items-center gap-1 lowercase text-[11px] font-[family-name:var(--font-mono)] transition-colors',
          open ? 'text-[var(--color-carbon)]' : 'text-[var(--text-tertiary)] hover:text-[var(--color-carbon)]',
        )}
      >
        {!inheriting && (
          <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-[var(--accent-primary)]" />
        )}
        <span className="max-w-[180px] truncate">{label}</span>
        <ChevronDown className={cn('h-3 w-3 shrink-0 transition-transform', open && 'rotate-180')} />
      </button>

      {open && eff && (
        <div className="absolute bottom-full right-0 z-50 mb-1.5 max-h-[44vh] w-[220px] overflow-y-auto rounded-[var(--radius-lg)] border border-[var(--nm-hairline)] bg-[var(--nm-card)] py-1 shadow-[var(--nm-elev-3)]">
          {models.length === 0 ? (
            <div className="px-3 py-2 text-[11px] text-[var(--text-tertiary)]">{t('chat.model.noModels')}</div>
          ) : (
            models.map((m) => {
              const active = m.model_id === eff.model;
              return (
                <button
                  key={m.model_id}
                  type="button"
                  onClick={() => choose(m.model_id)}
                  className={cn(
                    'flex w-full items-center gap-2 px-3 py-1.5 text-left text-[12px] transition-colors',
                    active
                      ? 'bg-[var(--color-carbon-soft)] text-[var(--nm-ink)]'
                      : 'text-[var(--nm-ink)] hover:bg-[var(--nm-paper-warm)]',
                  )}
                >
                  <span className="min-w-0 flex-1 truncate font-[family-name:var(--font-mono)]">{prettifyModel(m.model_id)}</span>
                  {active && <Check className="h-3.5 w-3.5 shrink-0 text-[var(--color-carbon)]" />}
                </button>
              );
            })
          )}
        </div>
      )}
    </div>
  );
}
