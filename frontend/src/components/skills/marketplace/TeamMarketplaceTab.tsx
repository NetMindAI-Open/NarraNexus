/**
 * @file_name: TeamMarketplaceTab.tsx
 * @author: NetMind.AI
 * @date: 2026-07-21
 * @description: Team Marketplace tab — browse team/agent bundle templates and
 * fork-install them. Install routes into the existing bundle import wizard
 * via the ?teamTemplate= deep-link (server-side install-preflight → review →
 * confirm), so no install logic is duplicated here.
 */

import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { Search, Loader2, AlertCircle, Users, Download } from 'lucide-react';
import { Button, ScrollArea } from '@/components/ui';
import { cn } from '@/lib/utils';
import { api } from '@/lib/api';
import type { TeamTemplate } from '@/types';

export function TeamMarketplaceTab() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [input, setInput] = useState('');
  const [query, setQuery] = useState('');
  const [category, setCategory] = useState<string>('all');
  const [templates, setTemplates] = useState<TeamTemplate[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const handle = setTimeout(() => setQuery(input.trim().toLowerCase()), 300);
    return () => clearTimeout(handle);
  }, [input]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await api.getTeamTemplates();
        if (!cancelled) setTemplates(r.templates);
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const categories = useMemo(() => {
    const set = new Set<string>();
    (templates ?? []).forEach((tpl) => tpl.categories.forEach((c) => set.add(c)));
    return ['all', ...Array.from(set).sort()];
  }, [templates]);

  const items = useMemo(() => {
    return (templates ?? []).filter((tpl) => {
      const catOk = category === 'all' || tpl.categories.includes(category);
      const qOk =
        !query ||
        tpl.name.toLowerCase().includes(query) ||
        tpl.description.toLowerCase().includes(query);
      return catOk && qOk;
    });
  }, [templates, category, query]);

  const handleInstall = (templateId: string) => {
    // Reuse the bundle import wizard: it runs install-preflight (resolve +
    // verify from our store) then the review/confirm flow.
    navigate(`/app/templates/install?teamTemplate=${encodeURIComponent(templateId)}`);
  };

  return (
    <div className="flex flex-col h-full" data-testid="team-marketplace-tab">
      {/* Search + category filter */}
      <div className="px-6 pb-3 flex items-center gap-3 flex-wrap">
        <div className="relative flex-1 min-w-[240px] max-w-md">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-[var(--text-tertiary)]" />
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder={t('teamMarketplace.searchPlaceholder')}
            className="w-full pl-9 pr-4 py-2.5 rounded-xl bg-[var(--bg-sunken)] border border-[var(--border-subtle)] text-sm text-[var(--text-primary)] placeholder:text-[var(--text-tertiary)] focus:outline-none focus:border-[var(--accent-primary)] transition-colors"
          />
        </div>
        <div className="flex gap-1 flex-wrap">
          {categories.map((c) => (
            <button
              key={c}
              onClick={() => setCategory(c)}
              className={cn(
                'px-2.5 py-1 rounded-lg text-xs transition-colors',
                category === c
                  ? 'bg-[var(--accent-primary)]/10 text-[var(--accent-primary)] font-medium'
                  : 'text-[var(--text-tertiary)] hover:bg-[var(--bg-tertiary)]'
              )}
            >
              {c === 'all' ? t('teamMarketplace.categoryAll') : c}
            </button>
          ))}
        </div>
      </div>

      <ScrollArea className="flex-1 min-h-0 px-6 pb-6">
        {error ? (
          <div className="flex items-center justify-center py-16 text-center">
            <div>
              <AlertCircle className="w-8 h-8 text-[var(--color-error)] mx-auto mb-3 opacity-60" />
              <p className="text-xs text-[var(--text-tertiary)] max-w-[320px]">
                {t('teamMarketplace.unavailable')}
              </p>
            </div>
          </div>
        ) : templates === null ? (
          <div className="flex items-center justify-center py-16">
            <Loader2 className="w-5 h-5 text-[var(--text-tertiary)] animate-spin" />
          </div>
        ) : items.length === 0 ? (
          <p className="text-center text-xs text-[var(--text-tertiary)] py-16">
            {t('teamMarketplace.empty')}
          </p>
        ) : (
          <div className="grid gap-3 grid-cols-1 md:grid-cols-2 xl:grid-cols-3">
            {items.map((tpl) => (
              <TeamTemplateCard key={tpl.template_id} template={tpl} onInstall={handleInstall} />
            ))}
          </div>
        )}
      </ScrollArea>
    </div>
  );
}

function TeamTemplateCard({
  template,
  onInstall,
}: {
  template: TeamTemplate;
  onInstall: (id: string) => void;
}) {
  const { t } = useTranslation();
  return (
    <div
      className="p-4 rounded-xl border bg-[var(--bg-elevated)] border-[var(--border-subtle)] hover:border-[var(--border-strong)] transition-colors flex flex-col"
      data-testid={`team-card-${template.template_id}`}
    >
      <div className="flex items-start justify-between gap-2 mb-1">
        <span className="text-sm font-semibold text-[var(--text-primary)]">{template.name}</span>
        <span className="flex items-center gap-1 shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium bg-[var(--accent-secondary)]/10 text-[var(--accent-secondary)]">
          <Users className="w-3 h-3" />
          {t('teamMarketplace.agentCount', { count: template.agent_count })}
        </span>
      </div>
      <p className="text-xs text-[var(--text-tertiary)] line-clamp-3 flex-1">
        {template.description}
      </p>
      <div className="flex flex-wrap gap-1 mt-2">
        {template.categories.map((c) => (
          <span
            key={c}
            className="rounded px-1.5 py-0.5 text-[10px] bg-[var(--bg-sunken)] text-[var(--text-tertiary)]"
          >
            {c}
          </span>
        ))}
      </div>
      <Button size="sm" className="mt-3 w-full" onClick={() => onInstall(template.template_id)}>
        <Download className="w-3.5 h-3.5 mr-1" />
        {t('teamMarketplace.install')}
      </Button>
    </div>
  );
}
