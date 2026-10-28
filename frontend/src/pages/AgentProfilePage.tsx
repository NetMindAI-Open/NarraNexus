/**
 * @file_name: AgentProfilePage.tsx
 * @author: NexusAgent
 * @date: 2026-08-24
 * @description: Dedicated Agent profile with work overview and atomic capabilities.
 */
import { useEffect, useMemo, useState, type ComponentType, type ReactNode } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import {
  Blocks,
  Bot,
  ChevronLeft,
  Clock3,
  Cpu,
  Eraser,
  MemoryStick,
  MessageSquare,
  MoreHorizontal,
  Network,
  Plug,
  Puzzle,
  Radio,
  Settings2,
  Sparkles,
  Trash2,
  UsersRound,
} from 'lucide-react';
import { useConfigStore, usePreloadStore, useChatStore, useTeamsStore } from '@/stores';
import { useDashboardStore } from '@/stores/dashboardStore';
import { api } from '@/lib/api';
import { BookmarkPanelHost } from '@/components/bookmarks/BookmarkPanelHost';
import { JobsPanel } from '@/components/jobs/JobsPanel';
import { AgentInboxPanel } from '@/components/inbox/AgentInboxPanel';
import { AgentLlmConfigPanel } from '@/components/chat/AgentLlmConfigPanel';
import { AgentCapabilities } from '@/components/chat/AgentCapabilities';
import { ClearAgentDataDialog } from '@/components/layout/ClearAgentDataDialog';
import { AgentTeamAvatars } from '@/components/agents/AgentTeamAvatars';
import { AgentOverviewCard } from '@/components/agents/AgentOverviewCard';
import { AgentActivityCard } from '@/components/agents/AgentActivityCard';
import { AttentionBanners } from '@/components/dashboard/AttentionBanners';
import {
  Button,
  BracketEmptyState,
  PaperCard,
  RingAvatar,
  StatusDot,
  SunkenWell,
} from '@/components/nm';
import { useConfirm } from '@/components/ui';
import { useAgentActions } from '@/hooks/useAgentActions';
import { formatFramework, frameworkBrandIcon, frameworkIconInvertsInDark } from '@/lib/frameworkBrand';
import { getModelBrandIcon, iconInvertsInDark } from '@/lib/modelBrandIcons';
import { AGENT_TEXT_MAX_LENGTH } from '@/lib/agentLimits';
import { cn, formatMessageAge } from '@/lib/utils';
import type { OwnedAgentStatus, UpdateAgentResponse } from '@/types';
import type { BuiltinTabId } from '@/components/bookmarks';

type ProfileTab = 'overview' | 'capabilities' | 'settings';
type CapabilityId = 'network' | 'memory' | 'skills' | 'mcp' | 'channels';
type SettingsSection = 'general' | 'awareness' | 'capabilities' | 'model';
type BrandIcon = ComponentType<{ className?: string }>;
/** Where the user opened this profile from — decides what the breadcrumb
 *  "back" button returns to. Passed via navigate(path, { state }). */
type ProfileEntryState = { from?: 'chat' | 'dashboard' };

/** Same family as the Dashboard agent-list row's Chat button
 *  ([[DashboardPage.tsx]]'s `agentChatButtonClass`) — light bordered card,
 *  never an ink fill — scaled up for the profile header's larger context
 *  (Owner ruling 2026-08-25 (3): same look, bigger than the compact
 *  table-row pill). */
const agentChatButtonClass =
  'inline-flex items-center gap-2 rounded-[var(--radius-sm)] border border-[var(--nm-hairline)] bg-[var(--nm-card)] px-4 py-2 text-sm font-medium text-[var(--nm-ink70)] shadow-[var(--nm-elev-1)] transition-colors hover:border-[var(--nm-ink30)] hover:bg-[var(--nm-paper-warm)] hover:text-[var(--nm-ink)]';

const CAPABILITIES: Array<{
  id: CapabilityId;
  labelKey: string;
  icon: ComponentType<{ className?: string }>;
}> = [
  { id: 'network', labelKey: 'pages.agentProfile.network', icon: Network },
  { id: 'memory', labelKey: 'pages.agentProfile.memory', icon: MemoryStick },
  { id: 'skills', labelKey: 'pages.agentProfile.skills', icon: Puzzle },
  { id: 'mcp', labelKey: 'pages.agentProfile.mcp', icon: Plug },
  { id: 'channels', labelKey: 'pages.agentProfile.channels', icon: Radio },
];

// Shell-authored literal table: builtin tab ids only, so a typo is a compile error.
const CAPABILITY_TO_PANEL: Partial<Record<CapabilityId, BuiltinTabId>> = {
  network: 'social',
  memory: 'memory',
  skills: 'skills',
  mcp: 'mcp',
  channels: 'channels',
};

export function AgentProfilePage() {
  const { agentId: routeAgentId = '' } = useParams<{ agentId: string }>();
  const agentId = decodeURIComponent(routeAgentId);
  const navigate = useNavigate();
  const location = useLocation();
  const cameFromChat = (location.state as ProfileEntryState | null)?.from === 'chat';
  const { t, i18n } = useTranslation();
  const {
    agents,
    userId,
    setAgentId,
    refreshAgents,
  } = useConfigStore();
  const { setActiveAgent, clearAgent, requestHistoryRefresh } = useChatStore();
  const { teams, refresh: refreshTeams } = useTeamsStore();
  const { jobs, agentInboxRooms, agentInboxUnreadCount } = usePreloadStore();
  const statusAgents = useDashboardStore((state) => state.agents);
  const onFetchSuccess = useDashboardStore((state) => state.onFetchSuccess);
  const [tab, setTab] = useState<ProfileTab>('overview');
  const [capability, setCapability] = useState<CapabilityId>('network');
  const [settingsSection, setSettingsSection] = useState<SettingsSection>('general');
  const [llmOpen, setLlmOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [clearOpen, setClearOpen] = useState(false);
  const [clearBusy, setClearBusy] = useState(false);
  const { confirm, alert, dialog: confirmDialog } = useConfirm();
  // Rename-side-effect report and delete are shared with the sidebar agent
  // row's ⋯ menu (Owner-required, reinstated 2026-09-11) through one hook, so
  // the two doors cannot drift. The dialogs render through this page's own
  // useConfirm host.
  const { deleteAgent, warnAboutUpdateSideEffects } = useAgentActions({ confirm, alert });

  const agent = agents.find((item) => item.agent_id === agentId);
  const status = statusAgents.find((item) => item.agent_id === agentId);
  const ownedStatus = status?.owned_by_viewer ? status as OwnedAgentStatus : null;
  // Owner gate for every mutating affordance (kebab, Settings tab). Read off
  // the agent list, which is synchronous and carries `created_by`, NOT off
  // `ownedStatus`: that comes from the dashboard status feed and is null on
  // the first frame, so the owner's own buttons would blink in a poll late.
  // The list already includes other users' PUBLIC agents, and the sidebar
  // kebab this page replaced hid these actions behind the same check.
  const isOwner = agent?.created_by === userId;
  const tabs: ProfileTab[] = isOwner
    ? ['overview', 'capabilities', 'settings']
    : ['overview', 'capabilities'];
  const activeTab: ProfileTab = tabs.includes(tab) ? tab : 'overview';
  const agentTeams = useMemo(
    () => teams.filter((team) => team.member_agent_ids.includes(agentId)),
    [agentId, teams],
  );

  useEffect(() => {
    if (!agentId) return;
    setAgentId(agentId);
    void refreshAgents();
    void refreshTeams();
    void api.getDashboardStatus().then((response) => {
      if (response.success) onFetchSuccess(response.agents);
    }).catch(() => undefined);
  }, [agentId, onFetchSuccess, refreshAgents, refreshTeams, setAgentId]);

  if (!agentId) {
    return <ProfileEmpty label={t('pages.agentProfile.missingAgent')} />;
  }

  if (!agent && !status) {
    return <ProfileEmpty label={t('pages.agentProfile.notFound')} />;
  }

  const name = agent?.name || status?.name || agentId;
  const description = agent?.description || status?.description || '';
  const lastActiveAt = status?.status.last_activity_at || agent?.last_assistant_at || null;
  const isRunning = Boolean(agent?.active_run) || (status ? status.status.kind !== 'idle' : false);
  // No client-side fallback: the backend resolves the effective framework
  // through the same overlay the runtime uses and the platform default lives
  // there. Absent means "not known here" (someone else's agent) and renders
  // as '—' rather than as a brand this agent may not be running on.
  const framework = agent?.agent_framework;
  const model = agent?.model || null;
  const FrameworkIcon = frameworkBrandIcon(framework);
  const ModelIcon = model ? getModelBrandIcon(model) ?? Bot : Bot;
  const runningTask = agent?.active_run;
  const capabilityPanel = CAPABILITY_TO_PANEL[capability];

  const openChat = () => {
    setAgentId(agentId);
    setActiveAgent(agentId);
    navigate('/app/chat');
  };

  const goBack = () => {
    if (cameFromChat) {
      openChat();
      return;
    }
    navigate('/app/dashboard');
  };

  /**
   * Scoped wipe of the agent's conversations and/or memory. Moved here from
   * the sidebar row's ⋮ menu (Owner ruling 2026-08-27) and still only here —
   * the reinstated sidebar menu (2026-09-11) carries Rename / Model &
   * framework / Delete only. Clearing sits directly above delete because they
   * are the same family of irreversible action, ordered by blast radius.
   */
  const handleClearData = async (scopes: { conversations: boolean; memory: boolean }) => {
    setClearBusy(true);
    try {
      const res = await api.clearHistory(agentId, scopes);
      if (res.success) {
        // Drop the in-memory session AND force any mounted ChatPanel to
        // re-fetch server history (now empty) so the chat view doesn't keep
        // showing messages the server no longer has.
        if (scopes.conversations) {
          clearAgent(agentId);
          requestHistoryRefresh();
        }
        if (res.disk_errors?.length) {
          await alert({
            title: t('layout.clearAgentData.title', { name }),
            message: t('layout.clearAgentData.toastDiskWarn', { count: res.disk_errors.length }),
            danger: true,
          });
        }
      } else {
        await alert({
          title: t('layout.agentList.deleteFailedTitle'),
          message: res.error || 'Failed to clear agent data',
          danger: true,
        });
      }
    } catch (err) {
      await alert({
        title: t('layout.agentList.deleteFailedTitle'),
        message: err instanceof Error ? err.message : String(err),
        danger: true,
      });
    } finally {
      setClearBusy(false);
      setClearOpen(false);
    }
  };

  const handleDeleteAgent = async () => {
    setDeleting(true);
    try {
      // The hook confirms, deletes, refreshes the persisted list, drops the
      // cached session and re-points the active agent (this page made it the
      // active one on mount) — then this page leaves the deleted agent.
      const { deleted } = await deleteAgent(agentId, name);
      // The agents tab is the Dashboard's default and carries NO `?tab=`
      // (see [[DashboardPage.tsx]]: `?tab=` is the single source of truth
      // and `agents` is written back as "param removed").
      if (deleted) navigate('/app/dashboard');
    } finally {
      setDeleting(false);
    }
  };

  return (
    <div className="h-full overflow-y-auto bg-[var(--nm-card)] font-sans">
      {confirmDialog}
      <AgentLlmConfigPanel
        agentId={agentId}
        isOpen={llmOpen}
        onClose={() => setLlmOpen(false)}
        onSaved={() => void refreshAgents()}
      />
      {clearOpen && (
        <ClearAgentDataDialog
          agentName={name}
          busy={clearBusy}
          onCancel={() => setClearOpen(false)}
          onConfirm={(scopes) => void handleClearData(scopes)}
        />
      )}

      <header className="border-b border-[var(--nm-hairline)] bg-[var(--nm-card)]">
        <div className="mx-auto max-w-[1180px] px-6 pt-5">
          <button
            type="button"
            onClick={goBack}
            className="inline-flex items-center gap-1 text-xs text-[var(--nm-ink50)] transition-colors hover:text-[var(--nm-ink)]"
          >
            <ChevronLeft className="h-3.5 w-3.5" />
            {cameFromChat ? t('pages.agentProfile.backToChat') : t('pages.agentProfile.breadcrumb')}
          </button>

          <div className="flex flex-col gap-5 py-6 md:flex-row md:items-center">
            <RingAvatar species="silicon" label={name.slice(0, 2)} size="lg" className="shrink-0" />
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-3">
                <h1 className="truncate text-2xl font-semibold text-[var(--nm-ink)]">{name}</h1>
                <span className="inline-flex items-center gap-1.5 text-xs text-[var(--nm-ink70)]">
                  <StatusDot status={isRunning ? 'success' : 'neutral'} size={8} pulse={isRunning} />
                  {isRunning ? t('pages.agentProfile.running') : t('pages.agentProfile.idleStatus')}
                </span>
              </div>
              <p className="mt-1 truncate text-xs text-[var(--nm-ink50)]">{description || agentId}</p>
              <div className="mt-4 flex flex-wrap items-center gap-x-5 gap-y-2 text-xs text-[var(--nm-ink50)]">
                <MetaItem icon={Bot} label={agentId} />
                <span data-testid="profile-team" className="inline-flex items-center gap-2">
                  <UsersRound className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                  <AgentTeamAvatars agentId={agentId} teams={agentTeams} />
                </span>
                <MetaItem
                  icon={Clock3}
                  label={lastActiveAt ? formatMessageAge(lastActiveAt, i18n.language) : '—'}
                />
              </div>
            </div>
            <span className="flex shrink-0 items-center gap-2">
              <button type="button" onClick={openChat} className={agentChatButtonClass}>
                <MessageSquare className="w-4 h-4" />
                {t('pages.agentProfile.chat')}
              </button>
              {isOwner && (
                <AgentHeaderMenu
                  onClearData={() => setClearOpen(true)}
                  onDelete={handleDeleteAgent}
                  disabled={deleting}
                />
              )}
            </span>
          </div>

          <div className="flex gap-7" role="tablist" aria-label={t('pages.agentProfile.views')}>
            {tabs.map((item) => (
              <button
                key={item}
                type="button"
                role="tab"
                aria-selected={activeTab === item}
                onClick={() => setTab(item)}
                className={cn(
                  'relative py-3 text-sm font-medium transition-colors',
                  activeTab === item ? 'text-[var(--nm-ink)]' : 'text-[var(--nm-ink50)] hover:text-[var(--nm-ink)]',
                )}
              >
                {t(`pages.agentProfile.${item}`)}
                {activeTab === item && <span className="absolute inset-x-0 bottom-0 h-0.5 bg-[var(--nm-ink)]" />}
              </button>
            ))}
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-[1180px] px-6 py-6">
        {activeTab === 'overview' ? (
          <div className="space-y-5" role="tabpanel">
            {/* Alerts first: failed jobs, blocked dependencies and quota stops
                are not one panel's business, and this is the only surface that
                spans them. Self-hides when there is nothing to act on, so a
                healthy agent's page is unchanged. */}
            {ownedStatus && (
              <AttentionBanners
                agentId={agentId}
                banners={ownedStatus.attention_banners ?? []}
              />
            )}

            <AgentOverviewCard
              frameworkLabel={formatFramework(framework)}
              FrameworkIcon={FrameworkIcon}
              frameworkInvertDark={frameworkIconInvertsInDark(FrameworkIcon)}
              modelLabel={model || '—'}
              ModelIcon={ModelIcon}
              modelInvertDark={iconInvertsInDark(ModelIcon)}
              isRunning={isRunning}
              taskLabel={runningTask?.current_stage || ownedStatus?.verb_line || t('pages.agentProfile.idle')}
              jobsCount={jobs.length}
              jobsRunning={jobs.some((job) => job.status === 'running')}
              inboxCount={agentInboxRooms.length}
              inboxUnreadCount={agentInboxUnreadCount}
            />

            {/* Rendered only once the status feed has answered. A skeleton
                here would flash and then swap — for a band this far down the
                page, arriving a poll late is quieter than arriving twice. */}
            {ownedStatus && <AgentActivityCard agentId={agentId} status={ownedStatus} />}

            <div className="grid gap-5 lg:grid-cols-2">
              <PaperCard padding="none" className="min-h-[440px] overflow-hidden">
                <JobsPanel embedded />
              </PaperCard>
              <PaperCard padding="none" className="min-h-[440px] overflow-hidden">
                <AgentInboxPanel embedded />
              </PaperCard>
            </div>
          </div>
        ) : activeTab === 'capabilities' ? (
          <div className="grid min-h-[600px] gap-5 md:grid-cols-[220px_minmax(0,1fr)]" role="tabpanel">
            <nav className="space-y-1" aria-label={t('pages.agentProfile.capabilities')}>
              {CAPABILITIES.map((item) => {
                const Icon = item.icon;
                return (
                  <button
                    key={item.id}
                    type="button"
                    aria-pressed={capability === item.id}
                    onClick={() => setCapability(item.id)}
                    className={cn(
                      'flex w-full items-center gap-2.5 rounded-[var(--radius-sm)] px-3 py-2.5 text-left text-sm transition-colors',
                      capability === item.id
                        ? 'bg-[var(--nm-row-active)] text-[var(--nm-ink)]'
                        : 'text-[var(--nm-ink50)] hover:bg-[var(--nm-row-hover)] hover:text-[var(--nm-ink)]',
                    )}
                  >
                    <Icon className="h-3.5 w-3.5" />
                    {t(item.labelKey)}
                  </button>
                );
              })}
            </nav>

            <PaperCard padding="none" className="min-h-[600px] overflow-hidden">
              {capabilityPanel && <BookmarkPanelHost tab={capabilityPanel} agentId={agentId} />}
            </PaperCard>
          </div>
        ) : (
          <div className="grid min-h-[600px] gap-5 md:grid-cols-[220px_minmax(0,1fr)]" role="tabpanel">
            <nav className="space-y-1" aria-label={t('pages.agentProfile.settings')}>
              <SettingsNavItem
                active={settingsSection === 'general'}
                icon={Settings2}
                label={t('pages.agentProfile.general')}
                onClick={() => setSettingsSection('general')}
              />
              <SettingsNavItem
                active={settingsSection === 'awareness'}
                icon={Sparkles}
                label={t('pages.agentProfile.awareness')}
                onClick={() => setSettingsSection('awareness')}
              />
              <SettingsNavItem
                active={settingsSection === 'capabilities'}
                icon={Puzzle}
                label={t('pages.agentProfile.capabilitySwitches')}
                onClick={() => setSettingsSection('capabilities')}
              />
              <SettingsNavItem
                active={settingsSection === 'model'}
                icon={Cpu}
                label={t('pages.agentProfile.modelFramework')}
                onClick={() => setSettingsSection('model')}
              />
            </nav>

            <PaperCard padding="none" className="min-h-[600px] overflow-hidden">
              {settingsSection === 'general' ? (
                <GeneralSettingsPanel
                  key={agentId}
                  agentId={agentId}
                  initialName={name}
                  initialDescription={description}
                  onSaved={refreshAgents}
                  onWarn={warnAboutUpdateSideEffects}
                />
              ) : settingsSection === 'awareness' ? (
                <BookmarkPanelHost tab="awareness" agentId={agentId} />
              ) : settingsSection === 'capabilities' ? (
                <div className="p-5">
                  <AgentCapabilities agentId={agentId} />
                </div>
              ) : (
                <div className="p-6">
                  <div className="mb-5 flex items-center justify-between gap-4">
                    <div>
                      <h2 className="text-lg font-semibold text-[var(--nm-ink)]">
                        {t('pages.agentProfile.modelFramework')}
                      </h2>
                      <p className="mt-1 text-xs text-[var(--nm-ink50)]">
                        {t('pages.agentProfile.modelFrameworkHint')}
                      </p>
                    </div>
                    <Button variant="secondary" size="sm" onClick={() => setLlmOpen(true)}>
                      {t('pages.agentProfile.configure')}
                    </Button>
                  </div>
                  <div className="grid gap-3 sm:grid-cols-2">
                    <ConfigValue
                      label={t('pages.agentProfile.framework')}
                      value={formatFramework(framework)}
                      icon={Blocks}
                      brandIcon={FrameworkIcon}
                      brandInvertDark={frameworkIconInvertsInDark(FrameworkIcon)}
                      testId="profile-framework-config"
                    />
                    <ConfigValue
                      label={t('pages.agentProfile.model')}
                      value={model || '—'}
                      icon={Bot}
                      brandIcon={ModelIcon}
                      brandInvertDark={iconInvertsInDark(ModelIcon)}
                      testId="profile-model-config"
                    />
                  </div>
                </div>
              )}
            </PaperCard>
          </div>
        )}
      </main>
    </div>
  );
}

/**
 * Header "⋮" kebab — the profile's destructive actions live behind it
 * (Owner ruling 2026-08-25 (3): delete shouldn't sit at the same visual
 * weight as Chat). This is the only place an agent's data can be cleared;
 * Delete also lives in the sidebar agent row's ⋯ menu (Owner-required,
 * reinstated 2026-09-11), both through `useAgentActions`. Rename also has a
 * quick inline door there; the description is edited only on this page's
 * Settings tab.
 *
 * Order is by blast radius: Clear data (recoverable-ish — persona, channels
 * and account survive) sits above Delete (the agent is gone).
 */
function AgentHeaderMenu({
  onClearData,
  onDelete,
  disabled,
}: {
  onClearData: () => void;
  onDelete: () => void;
  disabled?: boolean;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);

  return (
    <div className="relative inline-flex">
      <button
        type="button"
        aria-label={t('layout.agentRowMenu.options')}
        onClick={() => setOpen((v) => !v)}
        className={cn(
          'inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-[var(--radius-sm)] border border-[var(--nm-hairline)] bg-[var(--nm-card)] text-[var(--nm-ink50)] shadow-[var(--nm-elev-1)] transition-colors hover:border-[var(--nm-ink30)] hover:bg-[var(--nm-paper-warm)] hover:text-[var(--nm-ink)]',
          open && 'bg-[var(--nm-paper-warm)] text-[var(--nm-ink)]',
        )}
      >
        <MoreHorizontal className="h-4 w-4" />
      </button>

      {open && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div
            className={cn(
              'absolute right-0 top-full z-50 mt-1 min-w-[160px] py-1',
              'rounded-[var(--radius-sm)] border shadow-md',
              'bg-[var(--nm-paper)] border-[var(--nm-hairline)]',
            )}
          >
            <button
              type="button"
              onClick={() => { setOpen(false); onClearData(); }}
              className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs font-medium text-[var(--nm-ink)] transition-colors hover:bg-[var(--nm-paper-warm)]"
            >
              <Eraser className="h-3.5 w-3.5" />
              {t('layout.agentRowMenu.clearData')}
            </button>
            <button
              type="button"
              disabled={disabled}
              onClick={() => { setOpen(false); onDelete(); }}
              className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs font-medium text-[var(--color-error)] transition-colors hover:bg-[var(--color-error)]/10 disabled:opacity-50"
            >
              <Trash2 className="h-3.5 w-3.5" />
              {t('layout.agentRowMenu.delete')}
            </button>
          </div>
        </>
      )}
    </div>
  );
}

function ProfileEmpty({ label }: { label: string }) {
  return (
    <div className="flex h-full items-center justify-center bg-[var(--nm-card)] p-8">
      <BracketEmptyState label={label} />
    </div>
  );
}

/**
 * Name + description form. Mounted with `key={agentId}` and NOTHING else:
 * switching agents resets the form, but keying on name/description too meant
 * a successful rename changed the key, remounted the panel and dropped the
 * "saved" state on the floor — the one save that succeeded was the one that
 * never said so. The user's just-saved values ARE the truth, so nothing needs
 * re-syncing from the store after `onSaved`.
 */
function GeneralSettingsPanel({
  agentId,
  initialName,
  initialDescription,
  onSaved,
  onWarn,
}: {
  agentId: string;
  initialName: string;
  initialDescription: string;
  onSaved: () => Promise<void>;
  onWarn: (response: UpdateAgentResponse) => Promise<void>;
}) {
  const { t } = useTranslation();
  const [name, setName] = useState(initialName);
  const [description, setDescription] = useState(initialDescription);
  const [saveState, setSaveState] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle');
  const invalid = !name.trim()
    || name.length > AGENT_TEXT_MAX_LENGTH
    || description.length > AGENT_TEXT_MAX_LENGTH;

  const save = async () => {
    if (invalid) return;
    setSaveState('saving');
    try {
      const response = await api.updateAgent(agentId, name.trim(), description);
      if (!response.success) {
        setSaveState('error');
        return;
      }
      await onSaved();
      setSaveState('saved');
      // A successful save can still carry news (duplicate name, identity
      // memory not rewritten) — reported after the state flip so the panel
      // reads "saved" behind the dialog.
      await onWarn(response);
    } catch {
      setSaveState('error');
    }
  };

  return (
    <div className="p-6">
      <div className="mb-6">
        <h2 className="text-lg font-semibold text-[var(--nm-ink)]">
          {t('pages.agentProfile.general')}
        </h2>
        <p className="mt-1 text-xs text-[var(--nm-ink50)]">
          {t('pages.agentProfile.generalHint')}
        </p>
      </div>

      <div className="overflow-hidden rounded-[var(--radius-md)] border border-[var(--nm-hairline)]">
        <div className="flex items-center justify-between gap-6 border-b border-[var(--nm-hairline)] p-5">
          <div>
            <h3 className="text-sm font-semibold text-[var(--nm-ink)]">
              {t('pages.agentProfile.avatar')}
            </h3>
            <p className="mt-1 text-xs text-[var(--nm-ink50)]">
              {t('pages.agentProfile.avatarHint')}
            </p>
          </div>
          <RingAvatar species="silicon" label={(name || agentId).slice(0, 2)} size="lg" />
        </div>

        <GeneralField label={t('pages.agentProfile.name')} length={name.length}>
          <input
            aria-label={t('pages.agentProfile.name')}
            value={name}
            onChange={(event) => {
              setName(event.target.value);
              setSaveState('idle');
            }}
            className="h-10 w-full rounded-[var(--radius-sm)] border border-[var(--nm-hairline)] bg-[var(--nm-card)] px-3 text-sm text-[var(--nm-ink)] outline-none transition-colors focus:border-[var(--nm-ink30)]"
          />
        </GeneralField>

        <GeneralField label={t('pages.agentProfile.description')} length={description.length} last>
          <textarea
            aria-label={t('pages.agentProfile.description')}
            value={description}
            onChange={(event) => {
              setDescription(event.target.value);
              setSaveState('idle');
            }}
            rows={4}
            className="w-full resize-y rounded-[var(--radius-sm)] border border-[var(--nm-hairline)] bg-[var(--nm-card)] px-3 py-2.5 text-sm leading-relaxed text-[var(--nm-ink)] outline-none transition-colors focus:border-[var(--nm-ink30)]"
          />
        </GeneralField>
      </div>

      <div className="mt-5 flex items-center justify-end gap-3">
        {saveState === 'saved' && (
          <span className="text-xs text-[var(--color-success)]">{t('pages.agentProfile.saved')}</span>
        )}
        {saveState === 'error' && (
          <span className="text-xs text-[var(--color-error)]">{t('pages.agentProfile.saveFailed')}</span>
        )}
        <Button
          variant="primary"
          size="sm"
          disabled={invalid || saveState === 'saving'}
          onClick={() => void save()}
        >
          {saveState === 'saving'
            ? t('pages.agentProfile.saving')
            : t('pages.agentProfile.saveGeneral')}
        </Button>
      </div>
    </div>
  );
}

function SettingsNavItem({
  active,
  icon: Icon,
  label,
  onClick,
}: {
  active: boolean;
  icon: BrandIcon;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={cn(
        'flex w-full items-center gap-2.5 rounded-[var(--radius-sm)] px-3 py-2.5 text-left text-sm transition-colors',
        active
          ? 'bg-[var(--nm-row-active)] text-[var(--nm-ink)]'
          : 'text-[var(--nm-ink50)] hover:bg-[var(--nm-row-hover)] hover:text-[var(--nm-ink)]',
      )}
    >
      <Icon className="h-3.5 w-3.5" />
      {label}
    </button>
  );
}

function GeneralField({
  label,
  length,
  last = false,
  children,
}: {
  label: string;
  length: number;
  last?: boolean;
  children: ReactNode;
}) {
  const overLimit = length > AGENT_TEXT_MAX_LENGTH;
  return (
    <div className={cn('grid gap-4 p-5 sm:grid-cols-[180px_minmax(0,1fr)]', !last && 'border-b border-[var(--nm-hairline)]')}>
      <div className="flex items-start justify-between gap-3 sm:block">
        <label className="text-sm font-semibold text-[var(--nm-ink)]">{label}</label>
        <span className={cn('mt-1 block text-[10px] tabular-nums', overLimit ? 'text-[var(--color-error)]' : 'text-[var(--nm-ink50)]')}>
          {length}/{AGENT_TEXT_MAX_LENGTH}
        </span>
      </div>
      {children}
    </div>
  );
}

function MetaItem({
  icon: Icon,
  brandIcon: BrandIcon,
  label,
  brandInvertDark = false,
  testId,
}: {
  icon: BrandIcon;
  brandIcon?: BrandIcon;
  label: string;
  brandInvertDark?: boolean;
  testId?: string;
}) {
  return (
    <span data-testid={testId} className="inline-flex min-w-0 items-center gap-1.5">
      <Icon className="h-3.5 w-3.5 shrink-0 text-[var(--nm-ink50)]" />
      {BrandIcon && (
        <BrandIcon className={cn('h-3.5 w-3.5 shrink-0', brandInvertDark && 'dark:invert')} />
      )}
      <span className="max-w-[240px] truncate">{label}</span>
    </span>
  );
}

function ConfigValue({
  label,
  value,
  icon: Icon,
  brandIcon: BrandIcon,
  brandInvertDark = false,
  testId,
}: {
  label: string;
  value: string;
  icon: BrandIcon;
  brandIcon: BrandIcon;
  brandInvertDark?: boolean;
  testId?: string;
}) {
  return (
    <SunkenWell className="min-w-0" data-testid={testId}>
      <div className="text-[10px] font-medium uppercase tracking-[0.12em] text-[var(--nm-ink50)]">{label}</div>
      <div className="mt-3 flex min-w-0 items-center gap-2 text-sm font-medium text-[var(--nm-ink)]">
        <Icon className="h-4 w-4 shrink-0 text-[var(--nm-ink50)]" />
        <BrandIcon className={cn('h-4 w-4 shrink-0', brandInvertDark && 'dark:invert')} />
        <span className="truncate">{value}</span>
      </div>
    </SunkenWell>
  );
}

export default AgentProfilePage;
