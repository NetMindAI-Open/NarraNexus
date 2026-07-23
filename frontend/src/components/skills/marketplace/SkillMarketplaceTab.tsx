/**
 * @file_name: MarketplacePage.tsx
 * @author: NetMind.AI
 * @date: 2026-07-21
 * @description: Full-page Skill Marketplace (left-sidebar entry point).
 *
 * The browse-first experience: search + category filter + card grid +
 * detail sheet. Complements (does not replace) the Skill tab's dialog
 * entry — both share the same hooks and install pipeline.
 */

import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Search, Loader2, AlertCircle } from 'lucide-react';
import { ScrollArea } from '@/components/ui';
import { cn } from '@/lib/utils';
import {
  useMarketplaceInstall,
  useMarketplaceSearch,
} from '@/hooks/useSkillMarketplace';
import { MarketplaceCard } from '@/components/skills/marketplace/MarketplaceBrowser';
import { SkillDetailSheet } from '@/components/skills/marketplace/SkillDetailSheet';

const CATEGORIES = ['all', 'fallback', 'utility', 'integration', 'enhancement'] as const;

export function SkillMarketplaceTab() {
  const { t } = useTranslation();
  const [input, setInput] = useState('');
  const [query, setQuery] = useState('');
  const [category, setCategory] = useState<(typeof CATEGORIES)[number]>('all');
  const [detailId, setDetailId] = useState<string | null>(null);
  const [installError, setInstallError] = useState<string | null>(null);

  useEffect(() => {
    const handle = setTimeout(() => setQuery(input.trim()), 300);
    return () => clearTimeout(handle);
  }, [input]);

  const { data, isLoading, error } = useMarketplaceSearch(query);
  const install = useMarketplaceInstall();

  const items = useMemo(() => {
    const all = data?.items ?? [];
    return category === 'all' ? all : all.filter((i) => i.category === category);
  }, [data, category]);

  const handleInstall = (skillId: string) => {
    setInstallError(null);
    install.mutate(
      { skillId },
      {
        onSuccess: () => setDetailId(null),
        onError: (err) =>
          setInstallError(err instanceof Error ? err.message : String(err)),
      }
    );
  };

  return (
    <div className="flex flex-col h-full" data-testid="skill-marketplace-tab">
      {/* Search + category filter */}
      <div className="px-6 pb-3 flex items-center gap-3 flex-wrap">
        <div className="relative flex-1 min-w-[240px] max-w-md">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-[var(--text-tertiary)]" />
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder={t('skills.marketplace.searchPlaceholder')}
            className="w-full pl-9 pr-4 py-2.5 rounded-xl bg-[var(--bg-sunken)] border border-[var(--border-subtle)] text-sm text-[var(--text-primary)] placeholder:text-[var(--text-tertiary)] focus:outline-none focus:border-[var(--accent-primary)] transition-colors"
          />
        </div>
        <div className="flex gap-1">
          {CATEGORIES.map((c) => (
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
              {t(`skills.marketplace.category.${c}`)}
            </button>
          ))}
        </div>
      </div>

      {installError && (
        <div className="mx-6 mb-2 flex items-center gap-2 px-3 py-2 rounded-lg bg-[var(--color-error)]/10 border border-[var(--color-error)]/20">
          <AlertCircle className="w-4 h-4 text-[var(--color-error)] shrink-0" />
          <span className="text-xs text-[var(--color-error)]">{installError}</span>
        </div>
      )}

      {/* Results grid */}
      <ScrollArea className="flex-1 min-h-0 px-6 pb-6">
        {error ? (
          <div className="flex items-center justify-center py-16 text-center">
            <div>
              <AlertCircle className="w-8 h-8 text-[var(--color-error)] mx-auto mb-3 opacity-60" />
              <p className="text-xs text-[var(--text-tertiary)] max-w-[320px]">
                {t('skills.marketplace.unavailable')}
              </p>
            </div>
          </div>
        ) : isLoading ? (
          <div className="flex items-center justify-center py-16">
            <Loader2 className="w-5 h-5 text-[var(--text-tertiary)] animate-spin" />
          </div>
        ) : items.length === 0 ? (
          <p className="text-center text-xs text-[var(--text-tertiary)] py-16">
            {t('skills.marketplace.empty')}
          </p>
        ) : (
          <div className="grid gap-3 grid-cols-1 md:grid-cols-2 xl:grid-cols-3">
            {items.map((item) => (
              <MarketplaceCard
                key={item.skill_id}
                item={item}
                isInstalling={
                  install.isPending && install.variables?.skillId === item.skill_id
                }
                onInstall={handleInstall}
                onOpenDetail={() => setDetailId(item.skill_id)}
              />
            ))}
          </div>
        )}
      </ScrollArea>

      {detailId && (
        <SkillDetailSheet
          skillId={detailId}
          installed={items.find((i) => i.skill_id === detailId)?.installed}
          isInstalling={install.isPending}
          onInstall={handleInstall}
          onClose={() => setDetailId(null)}
        />
      )}
    </div>
  );
}


