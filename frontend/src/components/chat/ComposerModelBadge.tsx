/**
 * ComposerModelBadge — the model indicator + switcher docked bottom-right of
 * the composer tools row.
 *
 * The conversation model is the user's `agent` provider slot (Settings ›
 * Providers). This surfaces it in chat: click to open a list of the models
 * available on that slot's provider and pick one — it PUTs the slot, so the
 * change is the same one Settings would make (it applies to the agent slot for
 * the user's agents, rule #15 — the platform doesn't pick models for you, it
 * just makes your choice quick to reach). When no slot is configured it falls
 * back to a "set model" link into Settings.
 */
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { ChevronDown, Check } from 'lucide-react';
import { api } from '@/lib/api';
import { cn } from '@/lib/utils';

const AGENT_SLOT = 'agent';

interface SlotCfg {
  provider_id: string;
  model: string;
  thinking?: string;
  reasoning_effort?: string;
}

function prettify(model: string): string {
  if (!model || model === 'default') return 'default';
  // "deepseek-ai/DeepSeek-V4-Pro" → "DeepSeek-V4-Pro"
  return model.includes('/') ? model.split('/').pop() || model : model;
}

export function ComposerModelBadge() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [cfg, setCfg] = useState<SlotCfg | null>(null);
  const [models, setModels] = useState<string[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  // Load the agent slot + its provider's available models once.
  useEffect(() => {
    let alive = true;
    void (async () => {
      try {
        const res = await api.getProviders();
        const data = res?.data;
        const slots = (data?.slots ?? {}) as Record<string, { config?: SlotCfg | null }>;
        const slot = slots[AGENT_SLOT]?.config ?? null;
        const providers = (data?.providers ?? {}) as Record<string, { models?: string[] }>;
        const prov = slot?.provider_id ? providers[slot.provider_id] : undefined;
        if (alive) {
          setCfg(slot);
          setModels(Array.isArray(prov?.models) ? prov!.models! : []);
        }
      } catch {
        /* leave unset → "set model" */
      } finally {
        if (alive) setLoaded(true);
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  // Close the menu on an outside click.
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [open]);

  // No configured slot (or still loading with nothing) → link into Settings.
  if (loaded && !cfg) {
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

  const label = cfg?.model ? prettify(cfg.model) : '…';

  const choose = async (model: string) => {
    if (!cfg || saving) return;
    setOpen(false);
    if (model === cfg.model) return;
    const prev = cfg;
    setSaving(true);
    setCfg({ ...cfg, model }); // optimistic
    try {
      const res = await api.setProviderSlot(AGENT_SLOT, {
        provider_id: cfg.provider_id,
        model,
        thinking: cfg.thinking || '',
        reasoning_effort: cfg.reasoning_effort || '',
      });
      if (!res.success) setCfg(prev); // revert on failure
    } catch {
      setCfg(prev);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        title={t('chat.model.switchTooltip')}
        className={cn(
          'inline-flex items-center gap-1 lowercase text-[11px] font-[family-name:var(--font-mono)] transition-colors',
          open ? 'text-[var(--color-carbon)]' : 'text-[var(--text-tertiary)] hover:text-[var(--color-carbon)]',
        )}
      >
        <span className="max-w-[180px] truncate">{label}</span>
        <ChevronDown className={cn('h-3 w-3 shrink-0 transition-transform', open && 'rotate-180')} />
      </button>

      {open && cfg && (
        <div className="absolute bottom-full right-0 z-50 mb-1.5 max-h-[44vh] w-[220px] overflow-y-auto rounded-[var(--radius-lg)] border border-[var(--nm-hairline)] bg-[var(--nm-card)] py-1 shadow-[var(--nm-elev-3)]">
          {models.length === 0 ? (
            <div className="px-3 py-2 text-[11px] text-[var(--text-tertiary)]">{t('chat.model.noModels')}</div>
          ) : (
            models.map((m) => {
              const active = m === cfg.model;
              return (
                <button
                  key={m}
                  type="button"
                  onClick={() => choose(m)}
                  className={cn(
                    'flex w-full items-center gap-2 px-3 py-1.5 text-left text-[12px] transition-colors',
                    active
                      ? 'bg-[var(--color-carbon-soft)] text-[var(--nm-ink)]'
                      : 'text-[var(--nm-ink)] hover:bg-[var(--nm-paper-warm)]',
                  )}
                >
                  <span className="min-w-0 flex-1 truncate font-[family-name:var(--font-mono)]">{prettify(m)}</span>
                  {active && <Check className="h-3.5 w-3.5 shrink-0 text-[var(--color-carbon)]" />}
                </button>
              );
            })
          )}
          <div className="mt-1 border-t border-[var(--nm-hairline)] px-3 pb-0.5 pt-1.5">
            <button
              type="button"
              onClick={() => navigate('/app/settings')}
              className="font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.12em] text-[var(--text-tertiary)] transition-colors hover:text-[var(--color-carbon)]"
            >
              {t('chat.model.moreInSettings')}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
