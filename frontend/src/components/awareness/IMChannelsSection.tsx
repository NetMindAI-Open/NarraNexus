/**
 * IMChannelsSection — three-level disclosure for IM channel bindings.
 *
 *   Level 1 (collapsed):  ▶ IM Channels  N/M connected   [Manage]
 *   Level 2 (expanded):   ▼ IM Channels                            [list of channel cards]
 *   Level 3 (one open):   ▼ IM Channels                            [card with config inline]
 *
 * Adding a future channel is one registration in the `ui.channels` registry
 * (see `registerBuiltinChannels.ts` for the builtin pattern, or a channel
 * plugin's own `activate(host)`) — no change required here.
 */

import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ChevronDown, ChevronRight, Link as LinkIcon } from 'lucide-react';

import { Button } from '@/components/ui';
import { useConfigStore } from '@/stores';


/**
 * Props every IM-channel config component must accept. The parent passes
 * ``onBindStateChange`` so that bind/unbind/test in the child fans out to
 * the parent's connected-badge refresh — otherwise the parent badge stays
 * stale until the user manually clicks "Refresh status".
 */
import { CHANNELS, sortedChannels, useRegistryEntries, type ChannelStatus } from '@/platform/registries';
import './registerBuiltinChannels';

export function IMChannelsSection() {
  // Rows come from the ui.channels registry: builtins register in
  // registerBuiltinChannels.ts (owner = the channel plugin), channel plugins
  // add theirs; a disabled builtin's row is gone with the plugin.
  const channels = sortedChannels(useRegistryEntries(CHANNELS));
  const { t } = useTranslation();
  const { agentId } = useConfigStore();
  // Expanded by default: opening Channels should show the channel list, not a
  // collapsed one-liner the user has to click to reveal.
  const [sectionOpen, setSectionOpen] = useState(true);
  const [expandedChannel, setExpandedChannel] = useState<string | null>(null);
  const [statusMap, setStatusMap] = useState<Record<string, ChannelStatus>>({});

  const refreshConnected = useCallback(async () => {
    if (!agentId) return;
    const entries = await Promise.all(
      channels.map(async (ch) => [ch.id, await ch.value.fetchStatus(agentId)] as const),
    );
    setStatusMap(Object.fromEntries(entries));
  }, [agentId, channels]);

  // Pre-fetch on mount and whenever the active agent changes, so the
  // Level-1 collapsed summary ("X/Y connected") shows the real count
  // immediately. Without this the count is stuck at 0/3 until the user
  // expands the section, which made bindings made elsewhere (agent
  // chat, frontend Bind button while the section was collapsed, prior
  // session) look like they hadn't taken effect.
  //
  // react-hooks/set-state-in-effect flags this because refreshConnected
  // calls setConnectedMap. The rule prefers Suspense / React Query /
  // SWR for server-state mount fetches; we use raw useEffect across
  // this codebase (see LarkConfig, SlackConfig) so adopting that here
  // would be an out-of-scope refactor. Disabling per-call with the
  // rationale logged.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    refreshConnected();
  }, [refreshConnected]);

  // Collapse any inline-expanded channel when the agent changes — different
  // agents have different binding states so prior expansion is no longer
  // relevant. Done via "render-time set" (allowed by react-hooks rules) rather
  // than a useEffect.
  const [lastSeenAgent, setLastSeenAgent] = useState<string | null>(null);
  if (lastSeenAgent !== agentId) {
    setLastSeenAgent(agentId);
    if (expandedChannel !== null) setExpandedChannel(null);
  }

  const handleToggleSection = useCallback(() => {
    setSectionOpen((open) => {
      const nextOpen = !open;
      // Refresh again on each open so a stale state from earlier in
      // the session (binding changed in another tab, MCP-triggered
      // bind/unbind from the agent chat) gets corrected as soon as
      // the user reveals the panel. The initial mount fetch lives in
      // the useEffect above — this is the "user is actively looking
      // at it again" refresh.
      if (nextOpen) {
        void refreshConnected();
      }
      return nextOpen;
    });
  }, [refreshConnected]);

  const connectedCount = channels.filter((c) => statusMap[c.id] === 'active').length;
  const totalCount = channels.length;

  const toggleChannel = (key: string) => {
    setExpandedChannel((prev) => (prev === key ? null : key));
  };

  return (
    <div>
      {/* Level 1: collapsed summary */}
      <button
        onClick={handleToggleSection}
        className="w-full flex items-center justify-between py-1 text-left group"
        aria-expanded={sectionOpen}
      >
        <span className="flex items-center gap-2">
          {sectionOpen ? (
            <ChevronDown className="w-4 h-4 text-[var(--text-secondary)]" />
          ) : (
            <ChevronRight className="w-4 h-4 text-[var(--text-secondary)]" />
          )}
          <span className="text-sm font-medium text-[var(--text-primary)]">{t('awareness.channels.title')}</span>
          <span className="text-xs text-[var(--text-secondary)]">
            {t('awareness.channels.connectedCount', { connected: connectedCount, total: totalCount })}
          </span>
        </span>
        {!sectionOpen && (
          <span className="text-xs text-[var(--accent-primary)] opacity-70 group-hover:opacity-100">
            {t('awareness.channels.manage')}
          </span>
        )}
      </button>

      {/* Levels 2 + 3 */}
      {sectionOpen && (
        <div className="mt-3 space-y-2">
          {channels.map((ch) => {
            const isExpanded = expandedChannel === ch.id;
            const status = statusMap[ch.id];
            const Icon = ch.value.icon;
            const Component = ch.value.component;
            return (
              <div
                key={ch.id}
                className="border border-[var(--border-default)] rounded"
              >
                <button
                  onClick={() => toggleChannel(ch.id)}
                  className="w-full flex items-center justify-between px-3 py-2 hover:bg-[var(--nm-paper-warm)] transition-colors text-left"
                  aria-expanded={isExpanded}
                >
                  <span className="flex items-center gap-2 text-sm text-[var(--text-primary)]">
                    <Icon className="w-4 h-4 text-[var(--text-secondary)]" />
                    {ch.value.labelIsKey ? t(ch.value.label) : ch.value.label}
                    {status === 'active' ? (
                      <span className="ml-2 text-xs text-[var(--color-success)]">{t('awareness.channels.connectedBadge')}</span>
                    ) : status === 'inactive' ? (
                      <span className="ml-2 text-xs text-[var(--color-warning)] inline-flex items-center gap-1">
                        <LinkIcon className="w-3 h-3" /> {t('awareness.channels.inactiveBadge')}
                      </span>
                    ) : (
                      <span className="ml-2 text-xs text-[var(--text-secondary)] inline-flex items-center gap-1">
                        <LinkIcon className="w-3 h-3" /> {t('awareness.channels.notBound')}
                      </span>
                    )}
                  </span>
                  {isExpanded ? (
                    <ChevronDown className="w-4 h-4 text-[var(--text-secondary)]" />
                  ) : (
                    <ChevronRight className="w-4 h-4 text-[var(--text-secondary)]" />
                  )}
                </button>
                {/* Conditional render — heavy components don't fetch when collapsed */}
                {isExpanded && (
                  <div className="px-3 pb-3 pt-1">
                    <Component onBindStateChange={refreshConnected} />
                  </div>
                )}
              </div>
            );
          })}
          <div className="flex justify-end pt-1">
            <Button
              size="sm"
              variant="outline"
              onClick={() => refreshConnected()}
              className="text-xs"
            >
              {t('awareness.channels.refreshStatus')}
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
