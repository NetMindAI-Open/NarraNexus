/**
 * @file_name: SkillDetailSheet.tsx
 * @author: NetMind.AI
 * @date: 2026-07-21
 * @description: Marketplace skill detail overlay — description, capabilities,
 * config keys, scan verdict, version history, install action.
 */

import { useTranslation } from 'react-i18next';
import {
  X,
  Loader2,
  ShieldCheck,
  ShieldAlert,
  Download,
  KeyRound,
  CheckCircle,
} from 'lucide-react';
import { Button, ScrollArea } from '@/components/ui';
import { useMarketplaceDetail } from '@/hooks/useSkillMarketplace';

export function SkillDetailSheet({
  skillId,
  installed,
  isInstalling,
  onInstall,
  onClose,
}: {
  skillId: string;
  installed?: boolean;
  isInstalling: boolean;
  onInstall: (skillId: string) => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const { data, isLoading } = useMarketplaceDetail(skillId);
  const entry = data?.entry;
  const configKeys = entry?.config_schema ? Object.keys(entry.config_schema) : [];

  return (
    <div
      className="fixed inset-0 flex items-center justify-center z-[60] animate-fade-in"
      style={{ background: 'var(--nm-backdrop)' }}
      data-testid="skill-detail-sheet"
    >
      <div className="bg-[var(--nm-card)] border border-[var(--nm-hairline)] rounded-[var(--radius-lg)] w-full max-w-lg max-h-[80vh] flex flex-col shadow-[var(--nm-elev-3)]">
        <div className="flex items-center justify-between p-5 pb-3">
          <h3 className="text-lg font-semibold text-[var(--text-primary)] truncate">
            {entry?.name ?? skillId}
            {entry && (
              <span className="ml-2 text-xs font-mono font-normal text-[var(--text-tertiary)]">
                v{entry.version}
              </span>
            )}
          </h3>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg hover:bg-[var(--bg-tertiary)] transition-colors"
          >
            <X className="w-4 h-4 text-[var(--text-tertiary)]" />
          </button>
        </div>

        {isLoading || !entry ? (
          <div className="flex items-center justify-center py-12">
            <Loader2 className="w-6 h-6 animate-spin text-[var(--accent-primary)]" />
          </div>
        ) : (
          <>
            <ScrollArea className="flex-1 min-h-0 px-5">
              {entry.description && (
                <p className="text-sm text-[var(--text-secondary)] mb-4">{entry.description}</p>
              )}

              {/* Scan verdict */}
              <div className="flex items-center gap-2 mb-4">
                {entry.scan_status === 'passed' ? (
                  <ShieldCheck className="w-4 h-4 text-[var(--color-success)]" />
                ) : (
                  <ShieldAlert className="w-4 h-4 text-[var(--color-warning)]" />
                )}
                <span className="text-xs text-[var(--text-secondary)]">
                  {t('skills.marketplace.scanLabel')}: {entry.scan_status}
                  {data?.scan && data.scan.low_issues > 0 && (
                    <span className="text-[var(--text-tertiary)]">
                      {' '}({t('skills.marketplace.lowIssues', { count: data.scan.low_issues })})
                    </span>
                  )}
                </span>
              </div>

              {/* Capabilities */}
              {entry.capabilities.length > 0 && (
                <div className="mb-4">
                  <p className="text-[10px] uppercase tracking-[0.12em] text-[var(--text-tertiary)] mb-1.5">
                    {t('skills.marketplace.capabilities')}
                  </p>
                  <div className="flex flex-wrap gap-1.5">
                    {entry.capabilities.map((cap) => (
                      <span
                        key={cap}
                        className="rounded px-1.5 py-0.5 text-[10px] font-mono bg-[var(--accent-secondary)]/10 text-[var(--accent-secondary)]"
                      >
                        {cap}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {/* Config keys */}
              {configKeys.length > 0 && (
                <div className="mb-4">
                  <p className="text-[10px] uppercase tracking-[0.12em] text-[var(--text-tertiary)] mb-1.5 flex items-center gap-1">
                    <KeyRound className="w-3 h-3" />
                    {t('skills.marketplace.configKeys')}
                  </p>
                  <div className="flex flex-wrap gap-1.5">
                    {configKeys.map((key) => (
                      <span
                        key={key}
                        className="rounded px-1.5 py-0.5 text-[10px] font-mono bg-[var(--bg-sunken)] text-[var(--text-secondary)]"
                      >
                        {key}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {/* Version history */}
              {data && data.versions.length > 0 && (
                <div className="mb-4">
                  <p className="text-[10px] uppercase tracking-[0.12em] text-[var(--text-tertiary)] mb-1.5">
                    {t('skills.marketplace.versions')}
                  </p>
                  <ul className="space-y-1">
                    {data.versions.map((v) => (
                      <li
                        key={v.version}
                        className="text-xs font-mono text-[var(--text-secondary)]"
                      >
                        v{v.version}
                        <span className="ml-2 text-[var(--text-tertiary)]">{v.status}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </ScrollArea>

            <div className="flex gap-3 p-5 pt-4 border-t border-[var(--border-subtle)]">
              <Button variant="ghost" onClick={onClose} className="flex-1">
                {t('skills.marketplace.close')}
              </Button>
              <Button
                variant="default"
                className="flex-1"
                disabled={installed || isInstalling}
                onClick={() => onInstall(entry.skill_id)}
              >
                {isInstalling ? (
                  <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                ) : installed ? (
                  <CheckCircle className="w-4 h-4 mr-2" />
                ) : (
                  <Download className="w-4 h-4 mr-2" />
                )}
                {installed
                  ? t('skills.marketplace.installed')
                  : t('skills.marketplace.install')}
              </Button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
