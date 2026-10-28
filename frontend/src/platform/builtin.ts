/**
 * @file_name: builtin.ts
 * @author: Bin Liang
 * @date: 2026-09-03
 * @description: The shell registers its own pages, sidebar rows, panels and settings sections here.
 *
 * "Builtins are plugins too": the shell contributes through the same
 * registries a plugin uses, in one place, in the order the UI shows them.
 * Imported once from `main.tsx` before the first render; page and panel
 * components stay lazy so this file adds only metadata to the first chunk.
 */
import { createElement, lazy } from 'react';
import { Navigate } from 'react-router-dom';
import {
  BookOpen,
  FolderOpen,
  Home,
  Inbox,
  ListTodo,
  LayoutDashboard,
  MonitorPlay,
  Network,
  Puzzle,
  Radio,
  Server,
  Sliders,
  Sparkles,
  Store,
  Upload,
  Wand2,
} from 'lucide-react';

import { ARTIFACT_KINDS, CONVERSATION_KINDS, PAGES, PANELS, SIDEBAR, TOOL_RENDERERS, type PanelDef } from '@/platform/registries';
import { acceptsBrowserLookOutput } from '@/lib/browserLook';
import { BUILTIN_ARTIFACT_KINDS } from '@/components/artifacts/kindRegistry';
import type { BuiltinArtifactKind } from '@/types/artifact';
import { ArtifactsGlyph } from '@/components/bookmarks/tabs';
import type { BuiltinTabId } from '@/components/bookmarks/builtinTabIds';
import {
  ArtifactsTab,
  AwarenessTab,
  BrowserTab,
  BuilderTab,
  ChannelsTab,
  InboxTab,
  JobsTab,
  McpTab,
  MemoryTab,
  SkillsTab,
  SmartHomeTab,
  SocialTab,
  WorkspaceTab,
} from '@/platform/builtinPanels';

const OWNER = { owner: 'builtin.ui' };

function SetupRedirect() {
  return createElement(Navigate, { to: '/welcome', replace: true });
}
// Feature-level builtin plugin: its UI row disappears when builtin.teams is disabled (loader.disableBuiltinUi).
const TEAMS = { owner: 'builtin.teams' };

// ---------------------------------------------------------------- pages
// Top-level routes (outside the /app layout).
PAGES.register('login', { path: '/login', element: lazy(() => import('@/pages/LoginPage')), guard: 'public', layout: 'top' }, OWNER);
// NM design system gallery — public dev tool, no auth required.
PAGES.register('nm-playground', { path: '/nm-playground', element: lazy(() => import('@/pages/NMPlaygroundPage')), guard: 'open', layout: 'top' }, OWNER);
// First-run flow (dev #383): /welcome gates itself; /setup was the provider-only
// first-run page and now forwards there (docs and old bookmarks point at it).
PAGES.register('welcome', { path: '/welcome', element: lazy(() => import('@/pages/WelcomePage')), guard: 'protected', layout: 'top', skipWelcomeGate: true }, OWNER);
PAGES.register('setup', { path: '/setup', element: SetupRedirect, guard: 'open', layout: 'top' }, OWNER);
// Website-to-Stripe bounce: the pricing page's plan CTAs point here. The
// protected guard gives a logged-out visitor /login?next=%2Fpay so the payment
// intent survives login/signup; PayPage then mints the checkout session.
// Carries a payment intent through login and out to Stripe: never interrupted by the welcome gate.
PAGES.register('pay', { path: '/pay', element: lazy(() => import('@/pages/PayPage')), guard: 'protected', layout: 'top', skipWelcomeGate: true }, OWNER);

// Children of the /app layout (MainLayout). Order is route order.
// `chat` and `teams/:teamId/chat` render nothing here: MainLayout draws the
// chat views in its main slot so they are not sub-page overlays.
PAGES.register('chat', { path: 'chat', element: null, guard: 'protected', layout: 'app' }, OWNER);
PAGES.register('dashboard', { path: 'dashboard', element: lazy(() => import('@/pages/DashboardPage')), guard: 'protected', layout: 'app' }, OWNER);
PAGES.register('marketplace', { path: 'marketplace', element: lazy(() => import('@/pages/MarketplacePage')), guard: 'protected', layout: 'app' }, OWNER);
PAGES.register('you', { path: 'you', element: lazy(() => import('@/pages/YouWorkspace')), guard: 'protected', layout: 'app' }, OWNER);
PAGES.register('system', { path: 'system', element: lazy(() => import('@/pages/SystemPage')), guard: 'protected', layout: 'app' }, OWNER);
PAGES.register('settings', { path: 'settings', element: lazy(() => import('@/pages/SettingsPage')), guard: 'protected', layout: 'app' }, OWNER);
// Legacy alias: the account surface lives inside Settings (?tab=account);
// this route only forwards old links there with the query preserved.
PAGES.register('account', { path: 'account', element: lazy(() => import('@/pages/AccountPage')), guard: 'protected', layout: 'app' }, OWNER);
PAGES.register('bundle-export', { path: 'bundle/export', element: lazy(() => import('@/pages/BundleExportPage')), guard: 'protected', layout: 'app' }, OWNER);
// One lazy component for both import routes (as the old JSX table had it):
// two `lazy()` calls would be two component types and remount on navigation.
const BundleImportPage = lazy(() => import('@/pages/BundleImportPage'));
PAGES.register('bundle-import', { path: 'bundle/import', element: BundleImportPage, guard: 'protected', layout: 'app' }, OWNER);
// Deep-link entry from the narra.nexus templates marketplace: same component
// as bundle/import; the URL query (?url=&sha256=) triggers auto-fetch-then-preflight.
PAGES.register('templates-install', { path: 'templates/install', element: BundleImportPage, guard: 'protected', layout: 'app' }, OWNER);
// Static segment ranks above :teamId in v6 route ranking, but it also reads clearer listed first.
// Creation studio entry + the agent profile page (dev #382/#383). `agents/new` is a
// static segment and must be registered before `agents/:agentId`.
PAGES.register('agents-new', { path: 'agents/new', element: lazy(() => import('@/pages/ChooseCreateMethodPage')), guard: 'protected', layout: 'app' }, OWNER);
PAGES.register('agent-profile', { path: 'agents/:agentId', element: lazy(() => import('@/pages/AgentProfilePage')), guard: 'protected', layout: 'app' }, OWNER);
PAGES.register('teams-new', { path: 'teams/new', element: lazy(() => import('@/pages/CreateTeamPage')), guard: 'protected', layout: 'app' }, TEAMS);
PAGES.register('team-detail', { path: 'teams/:teamId', element: lazy(() => import('@/pages/TeamDetailPage')), guard: 'protected', layout: 'app' }, TEAMS);
PAGES.register('team-chat', { path: 'teams/:teamId/chat', element: null, guard: 'protected', layout: 'app' }, TEAMS);

// -------------------------------------------------------------- conversation kinds
// `when: conversationKind:<k>` may name these; the shell's single chat is
// `chat`, builtin.teams brings `team` (gone with the plugin).
CONVERSATION_KINDS.register('chat', { labelKey: 'sidebar.chats' }, OWNER);
CONVERSATION_KINDS.register('team', { labelKey: 'sidebar.teams' }, TEAMS);

// -------------------------------------------------------------- sidebar
const prefetchDashboard = () => {
  // The dashboard chunk is the largest sub-page; warm it on hover/focus.
  import('@/pages/DashboardPage').catch(() => {});
};
const dashboardTab = (search: string) => new URLSearchParams(search).get('tab');

SIDEBAR.register('export', {
  labelKey: 'sidebar.export',
  titleKey: 'sidebar.exportTitle',
  icon: Upload,
  to: '/app/dashboard?tab=export',
  order: 10,
  helpId: 'sidebar.export',
  prefetch: prefetchDashboard,
  isActive: (loc) => loc.pathname === '/app/dashboard' && dashboardTab(loc.search) === 'export',
}, OWNER);
SIDEBAR.register('dashboard', {
  labelKey: 'sidebar.dashboard',
  icon: LayoutDashboard,
  to: '/app/dashboard',
  order: 20,
  helpId: 'sidebar.manage-agents',
  prefetch: prefetchDashboard,
  isActive: (loc) => loc.pathname === '/app/dashboard' && dashboardTab(loc.search) !== 'export',
}, OWNER);
SIDEBAR.register('marketplace', { labelKey: 'sidebar.marketplace', icon: Store, to: '/app/marketplace', order: 30 }, OWNER);
SIDEBAR.register('workspace', { labelKey: 'sidebar.workspace', icon: BookOpen, to: '/app/you', order: 40 }, OWNER);
SIDEBAR.register('settings', { labelKey: 'sidebar.settings', titleKey: 'sidebar.settingsTitle', icon: Sliders, to: '/app/settings', order: 50 }, OWNER);
SIDEBAR.register('system', {
  labelKey: 'sidebar.system',
  icon: Server,
  to: '/app/system',
  order: 60,
  visible: (features) => features.showSystemPage,
}, OWNER);

// --------------------------------------------------------------- panels
// The shell's own panels carry the shell's own tab ids (`BUILTIN_TAB_IDS`): a
// typo here is a compile error rather than a silently empty drawer.
const builtinPanel = (id: BuiltinTabId, def: PanelDef) => PANELS.register(id, def, OWNER);
// Each panel's `strip` field is the single source `bookmarks/tabs.ts` derives
// the drawer strip from (label/icon/category/order/conditional) — replaces
// the formerly-separate `STRIP_CATEGORIES` literal array (see I-3 in the
// 2026-09 plugin-platform review: a plugin registering a panel here also
// gets its strip entry, instead of the strip being a second table only the
// shell could edit).
// Creation studio panel (dev #382): conditional — renders only while the studio is open on this agent.
builtinPanel('builder', { component: BuilderTab, strip: { label: 'Builder', labelKey: 'rail.builder', icon: Wand2, category: 'config', order: 10, conditional: 'studio' } });
builtinPanel('awareness', { component: AwarenessTab, strip: { label: 'Awareness', labelKey: 'rail.awareness', icon: Sparkles, category: 'config', order: 20 } });
builtinPanel('workspace', { component: WorkspaceTab, strip: { label: 'Workspace', labelKey: 'rail.workspace', icon: FolderOpen, category: 'config', order: 30 } });
builtinPanel('channels', { component: ChannelsTab, strip: { label: 'Channels', labelKey: 'rail.channels', icon: Radio, category: 'config', order: 40 } });
builtinPanel('smarthome', { component: SmartHomeTab, strip: { label: 'Smart Home', labelKey: 'rail.smarthome', icon: Home, category: 'config', order: 50 } });
builtinPanel('jobs', { component: JobsTab, strip: { label: 'Jobs', labelKey: 'rail.jobs', icon: ListTodo, category: 'activity', order: 10 } });
builtinPanel('inbox', { component: InboxTab, strip: { label: 'Inbox', labelKey: 'rail.inbox', icon: Inbox, category: 'activity', order: 20 } });
// The live browser sits with the other activity panels: it is something the
// agent is DOING right now, not a configuration surface.
PANELS.register('browser', { component: BrowserTab, strip: { label: 'Browser', labelKey: 'rail.browser', icon: MonitorPlay, category: 'activity', order: 25 } }, { owner: 'builtin.browser' });
// browser_look's output row: what the agent looked at, plus a way into the panel
// above. Same owner, so disabling builtin.browser removes both. Lazy like every
// other component here; the timeline shows the generic row until it loads. The
// `accepts` check is a dependency-free parser, so it stays in the first chunk.
TOOL_RENDERERS.register('browser_look', {
  component: lazy(() => import('@/components/chat/BrowserLookOutput').then((m) => ({ default: m.BrowserLookOutput }))),
  accepts: acceptsBrowserLookOutput,
}, { owner: 'builtin.browser' });
builtinPanel('artifacts', { component: ArtifactsTab, strip: { label: 'Artifacts', labelKey: 'rail.artifacts', icon: ArtifactsGlyph, category: 'activity', order: 30 } });
builtinPanel('memory', { component: MemoryTab, strip: { label: 'Memory', labelKey: 'rail.memory', icon: BookOpen, category: 'narra', order: 10 } });
builtinPanel('social', { component: SocialTab, strip: { label: 'Social Network', labelKey: 'rail.social', icon: Network, stripLabel: 'Network', stripLabelKey: 'rail.socialShort', category: 'nexus', order: 10 } });
builtinPanel('skills', { component: SkillsTab, strip: { label: 'Skills', labelKey: 'rail.skills', icon: Puzzle, category: 'skills', order: 10 } });
builtinPanel('mcp', { component: McpTab, strip: { label: 'MCP Servers', labelKey: 'rail.mcp', icon: Server, stripLabel: 'MCP', stripLabelKey: 'rail.mcpShort', category: 'skills', order: 20 } });

// Settings sections are registered by `pages/settings/registerBuiltinSections.ts`
// from inside the settings chunk (the panes stay lazy with the page).

// -------------------------------------------------------- artifact kinds
// The shell's renderer/edit/preview descriptors, one registry entry per
// builtin kind; a plugin adds a kind the same way through
// `host.registries.artifactKinds`.
for (const kind of Object.keys(BUILTIN_ARTIFACT_KINDS) as BuiltinArtifactKind[]) {
  ARTIFACT_KINDS.register(kind, BUILTIN_ARTIFACT_KINDS[kind], OWNER);
}
