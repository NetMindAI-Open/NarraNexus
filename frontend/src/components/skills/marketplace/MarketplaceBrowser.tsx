/**
 * @file_name: MarketplaceBrowser.tsx
 * @author: NetMind.AI
 * @date: 2026-07-21
 * @description: Skill Marketplace browse/search dialog (Skill Tab → Add Skill).
 *
 * Search with 300ms debounce; cards show scan status, downloads and the
 * installed / update-available flags injected by the backend; Install runs
 * the marketplace pipeline; clicking a card opens SkillDetailSheet.
 */

import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  X,
  Search,
  Store,
  Loader2,
  AlertCircle,
  Download,
  CheckCircle,
  ShieldCheck,
  ShieldAlert,
  ArrowUpCircle,
} from 'lucide-react';
import { Button, ScrollArea } from '@/components/ui';
import { cn } from '@/lib/utils';
import {
  useMarketplaceInstall,
  useMarketplaceSearch,
} from '@/hooks/useSkillMarketplace';
import type { MarketplaceSkillItem } from '@/types/skills';
import { SkillDetailSheet } from './SkillDetailSheet';

export function MarketplaceBrowser({
  onClose,
  onInstalled,
}: {
  onClose: () => void;
  onInstalled?: () => void;
}) {
  const { t } = useTranslation();
  const [input, setInput] = useState('');
  const [query, setQuery] = useState('');
  const [detailId, setDetailId] = useState<string | null>(null);
  const [installError, setInstallError] = useState<string | null>(null);

  // 300ms debounce: `query` (the query-key input) trails `input`.
  useEffect(() => {
    const handle = setTimeout(() => setQuery(input.trim()), 300);
    return () => clearTimeout(handle);
  }, [input]);

  const { data, isLoading, error } = useMarketplaceSearch(query);
  const install = useMarketplaceInstall();

  const handleInstall = (skillId: string) => {
    setInstallError(null);
    install.mutate(
      { skillId },
      {
        onSuccess: () => {
          setDetailId(null);
          onInstalled?.();
        },
        onError: (err) =>
          setInstallError(err instanceof Error ? err.message : String(err)),
      }
    );
  };

  const items = data?.items ?? [];

  return (
    <div
      className="fixed inset-0 flex items-center justify-center z-50 animate-fade-in"
      style={{ background: 'var(--nm-backdrop)' }}
      data-testid="marketplace-browser"
    >
      <div className="bg-[var(--nm-card)] border border-[var(--nm-hairline)] rounded-[var(--radius-lg)] w-full max-w-xl max-h-[80vh] flex flex-col shadow-[var(--nm-elev-3)]">
        {/* Header */}
        <div className="flex items-center justify-between p-5 pb-3">
          <h3 className="text-lg font-semibold text-[var(--text-primary)] flex items-center gap-2">
            <Store className="w-5 h-5" />
            {t('skills.marketplace.title')}
          </h3>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg hover:bg-[var(--bg-tertiary)] transition-colors"
            aria-label={t('skills.marketplace.close')}
          >
            <X className="w-4 h-4 text-[var(--text-tertiary)]" />
          </button>
        </div>

        {/* Search box */}
        <div className="px-5 pb-3">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-[var(--text-tertiary)]" />
            <input
              autoFocus
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder={t('skills.marketplace.searchPlaceholder')}
              className="w-full pl-9 pr-4 py-2.5 rounded-xl bg-[var(--bg-sunken)] border border-[var(--border-subtle)] text-sm text-[var(--text-primary)] placeholder:text-[var(--text-tertiary)] focus:outline-none focus:border-[var(--accent-primary)] transition-colors"
            />
          </div>
        </div>

        {installError && (
          <div className="mx-5 mb-2 flex items-center gap-2 px-3 py-2 rounded-lg bg-[var(--color-error)]/10 border border-[var(--color-error)]/20">
            <AlertCircle className="w-4 h-4 text-[var(--color-error)] shrink-0" />
            <span className="text-xs text-[var(--color-error)]">{installError}</span>
          </div>
        )}

        {/* Results */}
        <ScrollArea className="flex-1 min-h-0 px-5 pb-5">
          {error ? (
            <div className="flex items-center justify-center py-10 text-center">
              <div>
                <AlertCircle className="w-8 h-8 text-[var(--color-error)] mx-auto mb-3 opacity-60" />
                <p className="text-xs text-[var(--text-tertiary)] max-w-[280px]">
                  {t('skills.marketplace.unavailable')}
                </p>
              </div>
            </div>
          ) : isLoading ? (
            <div className="flex items-center justify-center py-10">
              <Loader2 className="w-5 h-5 text-[var(--text-tertiary)] animate-spin" />
            </div>
          ) : items.length === 0 ? (
            <p className="text-center text-xs text-[var(--text-tertiary)] py-10">
              {t('skills.marketplace.empty')}
            </p>
          ) : (
            <div className="space-y-2">
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
      </div>

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

export function MarketplaceCard({
  item,
  isInstalling,
  onInstall,
  onOpenDetail,
}: {
  item: MarketplaceSkillItem;
  isInstalling: boolean;
  onInstall: (skillId: string) => void;
  onOpenDetail: () => void;
}) {
  const { t } = useTranslation();
  return (
    <div
      className={cn(
        'p-4 rounded-xl border bg-[var(--bg-elevated)] cursor-pointer',
        'border-[var(--border-subtle)] hover:border-[var(--border-strong)] transition-colors'
      )}
      onClick={onOpenDetail}
      data-testid={`marketplace-card-${item.skill_id}`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5 mb-1">
            <span className="text-sm font-semibold text-[var(--text-primary)] truncate">
              {item.name}
            </span>
            <span className="text-[10px] font-mono text-[var(--text-tertiary)]">
              v{item.version}
            </span>
            {item.scan_status === 'passed' ? (
              <ShieldCheck className="w-3.5 h-3.5 text-[var(--color-success)] shrink-0" />
            ) : (
              <ShieldAlert className="w-3.5 h-3.5 text-[var(--color-warning)] shrink-0" />
            )}
            {item.update_available && (
              <span className="flex items-center gap-0.5 shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium bg-[var(--accent-primary)]/10 text-[var(--accent-primary)]">
                <ArrowUpCircle className="w-3 h-3" />
                {t('skills.marketplace.updateAvailable')}
              </span>
            )}
          </div>
          {item.description && (
            <p className="text-xs text-[var(--text-tertiary)] line-clamp-2">
              {item.description}
            </p>
          )}
          <p className="text-[10px] text-[var(--text-tertiary)] mt-1.5">
            {t('skills.marketplace.downloads', { count: item.downloads })}
          </p>
        </div>

        <Button
          variant={item.installed ? 'ghost' : 'default'}
          size="sm"
          disabled={item.installed || isInstalling}
          onClick={(e) => {
            e.stopPropagation();
            onInstall(item.skill_id);
          }}
        >
          {isInstalling ? (
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
          ) : item.installed ? (
            <>
              <CheckCircle className="w-3.5 h-3.5 mr-1" />
              {t('skills.marketplace.installed')}
            </>
          ) : (
            <>
              <Download className="w-3.5 h-3.5 mr-1" />
              {t('skills.marketplace.install')}
            </>
          )}
        </Button>
      </div>
    </div>
  );
}
