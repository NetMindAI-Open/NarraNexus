/**
 * @file_name: MarketplacePage.tsx
 * @author: NetMind.AI
 * @date: 2026-07-21
 * @description: Marketplace shell — one left-sidebar entry, two tabs:
 * Skills (extend a single agent) and Teams (fork a whole team/agent bundle).
 * Tab is reflected in ?tab= so links and refreshes are stable.
 */

import { useTranslation } from 'react-i18next';
import { useSearchParams } from 'react-router-dom';
import { Store, Puzzle, Users } from 'lucide-react';
import { cn } from '@/lib/utils';
import { SkillMarketplaceTab } from '@/components/skills/marketplace/SkillMarketplaceTab';
import { TeamMarketplaceTab } from '@/components/skills/marketplace/TeamMarketplaceTab';

type Tab = 'skills' | 'teams';

export function MarketplacePage() {
  const { t } = useTranslation();
  const [searchParams, setSearchParams] = useSearchParams();
  const tab: Tab = searchParams.get('tab') === 'teams' ? 'teams' : 'skills';

  const setTab = (next: Tab) => {
    const p = new URLSearchParams(searchParams);
    p.set('tab', next);
    setSearchParams(p, { replace: true });
  };

  return (
    <div className="flex flex-col h-full" data-testid="marketplace-page">
      {/* Header + tabs */}
      <div className="px-6 pt-6 pb-3">
        <h1 className="text-xl font-semibold text-[var(--text-primary)] flex items-center gap-2.5">
          <Store className="w-5 h-5" />
          {t('sidebar.marketplace')}
        </h1>
        <div className="flex gap-1 mt-4 border-b border-[var(--rule)]">
          <TabButton
            active={tab === 'skills'}
            onClick={() => setTab('skills')}
            icon={<Puzzle className="w-4 h-4" />}
            label={t('marketplace.tabs.skills')}
          />
          <TabButton
            active={tab === 'teams'}
            onClick={() => setTab('teams')}
            icon={<Users className="w-4 h-4" />}
            label={t('marketplace.tabs.teams')}
          />
        </div>
      </div>

      <div className="flex-1 min-h-0">
        {tab === 'skills' ? <SkillMarketplaceTab /> : <TeamMarketplaceTab />}
      </div>
    </div>
  );
}

function TabButton({
  active,
  onClick,
  icon,
  label,
}: {
  active: boolean;
  onClick: () => void;
  icon: React.ReactNode;
  label: string;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        'flex items-center gap-1.5 px-3 py-2 text-sm -mb-px border-b-2 transition-colors',
        active
          ? 'border-[var(--accent-primary)] text-[var(--text-primary)] font-medium'
          : 'border-transparent text-[var(--text-tertiary)] hover:text-[var(--text-secondary)]'
      )}
    >
      {icon}
      {label}
    </button>
  );
}

export default MarketplacePage;
