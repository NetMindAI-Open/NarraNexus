/**
 * @file_name: AgentOverviewCard.tsx
 * @author: NexusAgent
 * @date: 2026-08-25
 * @description: Read-only Agent attribution card for the Profile Overview tab
 * — framework/model, current task, jobs/inbox counts, skills/MCP summary.
 * Never clickable; JobsPanel/AgentInboxPanel below it are the interactive
 * surfaces. Flat hairline-row style (no filled tile backgrounds) — Owner
 * feedback 2026-08-25: the filled beige tiles read as heavy "black blocks";
 * follow the plain row-list + thin-divider language of the agent detail
 * card shown as reference instead.
 */
import { type ComponentType, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Inbox, Loader2, Settings2 } from 'lucide-react';
import { PaperCard, StatusDot } from '@/components/nm';
import { useSkillsList } from '@/hooks/useSkills';
import { useMCPList } from '@/hooks/useMCP';
import { cn } from '@/lib/utils';

type BrandIcon = ComponentType<{ className?: string }>;
const MAX_CHIPS = 4;

export interface AgentOverviewCardProps {
  frameworkLabel: string;
  FrameworkIcon: BrandIcon;
  frameworkInvertDark: boolean;
  modelLabel: string;
  ModelIcon: BrandIcon;
  modelInvertDark: boolean;
  isRunning: boolean;
  taskLabel: string;
  jobsCount: number;
  jobsRunning: boolean;
  inboxCount: number;
  inboxUnreadCount: number;
}

export function AgentOverviewCard({
  frameworkLabel,
  FrameworkIcon,
  frameworkInvertDark,
  modelLabel,
  ModelIcon,
  modelInvertDark,
  isRunning,
  taskLabel,
  jobsCount,
  jobsRunning,
  inboxCount,
  inboxUnreadCount,
}: AgentOverviewCardProps) {
  const { t } = useTranslation();
  const { data: skills = [], isLoading: skillsLoading } = useSkillsList(false);
  const { data: mcps = [], isLoading: mcpLoading } = useMCPList();
  const connectedMcpCount = mcps.filter((mcp) => mcp.connection_status === 'connected').length;

  const skillItems = skills.slice(0, MAX_CHIPS).map((skill) => skill.name);
  const skillOverflow = skills.length - skillItems.length;
  const mcpVisible = mcps.slice(0, MAX_CHIPS);
  const mcpOverflow = mcps.length - mcpVisible.length;

  return (
    <PaperCard data-testid="agent-overview-card">
      <h2 className="text-sm font-semibold text-[var(--nm-ink)]">{t('pages.agentProfile.overviewCardLabel')}</h2>

      <div className="mt-2 divide-y divide-[var(--nm-hairline)]">
        <Row
          label={frameworkLabel}
          rowLabel={t('pages.agentProfile.framework')}
          icon={FrameworkIcon}
          invertDark={frameworkInvertDark}
          testId="profile-framework-row"
        />
        <Row
          label={modelLabel}
          rowLabel={t('pages.agentProfile.model')}
          icon={ModelIcon}
          invertDark={modelInvertDark}
          testId="profile-model-row"
        />
        <Row
          label={taskLabel}
          rowLabel={t('pages.agentProfile.currentTask')}
          statusDot={isRunning}
          testId="profile-task-row"
        />
      </div>

      <div className="grid grid-cols-2 gap-4 border-t border-[var(--nm-hairline)] py-4">
        <StatBlock
          icon={Settings2}
          count={jobsCount}
          noun={t('pages.agentProfile.jobsNoun')}
          flag={jobsRunning ? { tone: 'running', text: t('pages.agentProfile.jobsRunningFlag') } : null}
          testId="profile-jobs-stat"
        />
        <StatBlock
          icon={Inbox}
          count={inboxCount}
          noun={t('pages.agentProfile.inboxNoun')}
          flag={
            inboxUnreadCount > 0
              ? { tone: 'unread', text: t('pages.agentProfile.inboxUnreadFlag', { count: inboxUnreadCount }) }
              : null
          }
          testId="profile-inbox-stat"
        />
      </div>

      <Section
        title={t('pages.agentProfile.skills')}
        count={String(skills.length)}
        loading={skillsLoading}
        emptyLabel={t('pages.agentProfile.noSkills')}
        chips={skillItems.map((name) => ({ key: name, label: name }))}
        moreLabel={skillOverflow > 0 ? t('pages.agentProfile.moreCount', { count: skillOverflow }) : null}
        testId="profile-skills-section"
      />

      <Section
        title={t('pages.agentProfile.mcp')}
        count={t('pages.agentProfile.mcpSummary', { count: connectedMcpCount })}
        loading={mcpLoading}
        emptyLabel={t('pages.agentProfile.noMcp')}
        chips={mcpVisible.map((mcp) => ({
          key: mcp.mcp_id,
          label: mcp.name,
          dotClassName:
            mcp.connection_status === 'connected'
              ? 'bg-[var(--color-success)]'
              : mcp.connection_status === 'failed'
                ? 'bg-[var(--color-error)]'
                : 'bg-[var(--nm-ink50)]',
        }))}
        moreLabel={mcpOverflow > 0 ? t('pages.agentProfile.moreCount', { count: mcpOverflow }) : null}
        testId="profile-mcp-section"
      />
    </PaperCard>
  );
}

function Row({
  rowLabel,
  label,
  icon: Icon,
  invertDark,
  statusDot,
  testId,
}: {
  rowLabel: string;
  label: string;
  icon?: BrandIcon;
  invertDark?: boolean;
  statusDot?: boolean;
  testId: string;
}) {
  return (
    <div data-testid={testId} className="grid grid-cols-[120px_1fr] items-center gap-3 py-2.5">
      <span className="text-sm text-[var(--nm-ink50)]">{rowLabel}</span>
      <span className="flex min-w-0 items-center gap-2 text-sm text-[var(--nm-ink)]">
        {statusDot && <StatusDot status={statusDot ? 'success' : 'neutral'} size={6} pulse={statusDot} />}
        {Icon && <Icon className={cn('h-3.5 w-3.5 shrink-0', invertDark && 'dark:invert')} />}
        <span className="truncate">{label}</span>
      </span>
    </div>
  );
}

function StatBlock({
  icon: Icon,
  count,
  noun,
  flag,
  testId,
}: {
  icon: BrandIcon;
  count: number;
  noun: string;
  flag: { tone: 'running' | 'unread'; text: string } | null;
  testId: string;
}) {
  return (
    <div data-testid={testId}>
      <div className="flex items-baseline gap-2">
        <span className="font-display text-2xl font-semibold leading-none text-[var(--nm-ink)]">{count}</span>
        {flag && (
          <span
            className={cn(
              'inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[10px] font-semibold',
              flag.tone === 'running' && 'text-[var(--color-success)]',
              flag.tone === 'unread' && 'bg-[var(--color-error)] text-white',
            )}
          >
            {flag.tone === 'running' && <span className="h-1.5 w-1.5 rounded-full bg-[var(--color-success)]" />}
            {flag.text}
          </span>
        )}
      </div>
      <span className="mt-0.5 inline-flex items-center gap-1.5 text-xs text-[var(--nm-ink50)]">
        <Icon className="h-3 w-3 shrink-0" />
        {noun}
      </span>
    </div>
  );
}

function Section({
  title,
  count,
  loading,
  emptyLabel,
  chips,
  moreLabel,
  testId,
}: {
  title: string;
  count: ReactNode;
  loading: boolean;
  emptyLabel: string;
  chips: Array<{ key: string; label: string; dotClassName?: string }>;
  moreLabel: string | null;
  testId: string;
}) {
  return (
    <div data-testid={testId} className="border-t border-[var(--nm-hairline)] py-4">
      <div className="flex items-center justify-between gap-3">
        <span className="text-sm font-semibold text-[var(--nm-ink)]">{title}</span>
        <span className="text-sm text-[var(--nm-ink50)]">{count}</span>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        {loading ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin text-[var(--nm-ink50)]" />
        ) : chips.length === 0 ? (
          <span className="text-sm text-[var(--nm-ink50)]">{emptyLabel}</span>
        ) : (
          <>
            {chips.map((chip) => (
              <span
                key={chip.key}
                className="inline-flex items-center gap-1.5 rounded-full border border-[var(--nm-hairline)] px-2.5 py-1 text-xs font-medium text-[var(--nm-ink)]"
              >
                {chip.dotClassName && <span className={cn('h-1.5 w-1.5 rounded-full', chip.dotClassName)} />}
                {chip.label}
              </span>
            ))}
            {moreLabel && <span className="text-[11.5px] text-[var(--nm-ink50)]">{moreLabel}</span>}
          </>
        )}
      </div>
    </div>
  );
}
