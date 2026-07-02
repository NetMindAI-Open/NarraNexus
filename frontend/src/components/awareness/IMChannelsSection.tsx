/**
 * IMChannelsSection — three-level disclosure for IM channel bindings.
 *
 *   Level 1 (collapsed):  ▶ IM Channels  N/M connected   [Manage]
 *   Level 2 (expanded):   ▼ IM Channels                            [list of channel cards]
 *   Level 3 (one open):   ▼ IM Channels                            [card with config inline]
 *
 * Adding a future channel (e.g. Telegram) is just one entry in
 * IM_CHANNELS — no other change required here.
 */

import { useCallback, useEffect, useState, type ComponentType } from 'react';
import { useTranslation } from 'react-i18next';
import { ChevronDown, ChevronRight, MessageSquare, Hash, Send, MessageCircle, QrCode, Bot, Link as LinkIcon } from 'lucide-react';

import { Button } from '@/components/ui';
import { useConfigStore } from '@/stores';
import { api } from '@/lib/api';

import { LarkConfig } from './LarkConfig';
import { SlackConfig } from './SlackConfig';
import { TelegramConfig } from './TelegramConfig';
import { WeChatConfig } from './WeChatConfig';
import { NarramessengerConfig } from './NarramessengerConfig';
import { DiscordConfig } from './DiscordConfig';

/**
 * Props every IM-channel config component must accept. The parent passes
 * ``onBindStateChange`` so that bind/unbind/test in the child fans out to
 * the parent's connected-badge refresh — otherwise the parent badge stays
 * stale until the user manually clicks "Refresh status".
 */
export interface ChannelConfigProps {
  onBindStateChange?: () => void;
}

interface ChannelEntry {
  key: string;
  label: string;
  Icon: ComponentType<{ className?: string }>;
  Component: ComponentType<ChannelConfigProps>;
  /** True iff this channel currently has a credential bound for the active agent. */
  fetchConnected: (agentId: string) => Promise<boolean>;
}

const IM_CHANNELS: ChannelEntry[] = [
  {
    key: 'lark',
    label: 'Lark / Feishu',
    Icon: MessageSquare,
    Component: LarkConfig,
    fetchConnected: async (agentId) => {
      try {
        const res = await api.getLarkCredential(agentId);
        return Boolean(res.success && res.data && (res.data.is_active ?? true));
      } catch {
        return false;
      }
    },
  },
  {
    key: 'slack',
    label: 'Slack',
    Icon: Hash,
    Component: SlackConfig,
    fetchConnected: async (agentId) => {
      try {
        const res = await api.getSlackCredential(agentId);
        return Boolean(res.success && res.data && res.data.enabled);
      } catch {
        return false;
      }
    },
  },
  {
    key: 'telegram',
    label: 'Telegram',
    Icon: Send,
    Component: TelegramConfig,
    fetchConnected: async (agentId) => {
      try {
        const res = await api.getTelegramCredential(agentId);
        return Boolean(res.success && res.data && res.data.enabled);
      } catch {
        return false;
      }
    },
  },
  {
    key: 'wechat',
    label: 'WeChat',
    Icon: QrCode,
    Component: WeChatConfig,
    fetchConnected: async (agentId) => {
      try {
        const res = await api.getWeChatCredential(agentId);
        return Boolean(res.success && res.data && res.data.enabled);
      } catch {
        return false;
      }
    },
  },
  {
    key: 'narramessenger',
    label: 'NarraMessenger',
    Icon: MessageCircle,
    Component: NarramessengerConfig,
    fetchConnected: async (agentId) => {
      try {
        const res = await api.getNarramessengerCredential(agentId);
        return Boolean(res.success && res.data && res.data.enabled);
      } catch {
        return false;
      }
    },
  },
  {
    key: 'discord',
    label: 'Discord',
    Icon: Bot,
    Component: DiscordConfig,
    fetchConnected: async (agentId) => {
      try {
        const res = await api.getDiscordCredential(agentId);
        return Boolean(res.success && res.data && res.data.enabled);
      } catch {
        return false;
      }
    },
  },
];

export function IMChannelsSection() {
  const { t } = useTranslation();
  const { agentId } = useConfigStore();
  // Expanded by default: opening Channels should show the channel list, not a
  // collapsed one-liner the user has to click to reveal.
  const [sectionOpen, setSectionOpen] = useState(true);
  const [expandedChannel, setExpandedChannel] = useState<string | null>(null);
  const [connectedMap, setConnectedMap] = useState<Record<string, boolean>>({});

  const refreshConnected = useCallback(async () => {
    if (!agentId) return;
    const entries = await Promise.all(
      IM_CHANNELS.map(async (ch) => [ch.key, await ch.fetchConnected(agentId)] as const),
    );
    setConnectedMap(Object.fromEntries(entries));
  }, [agentId]);

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

  const connectedCount = IM_CHANNELS.filter((c) => connectedMap[c.key]).length;
  const totalCount = IM_CHANNELS.length;

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
          {IM_CHANNELS.map((ch) => {
            const isExpanded = expandedChannel === ch.key;
            const isConnected = connectedMap[ch.key];
            const Icon = ch.Icon;
            const Component = ch.Component;
            return (
              <div
                key={ch.key}
                className="border border-[var(--border-default)] rounded"
              >
                <button
                  onClick={() => toggleChannel(ch.key)}
                  className="w-full flex items-center justify-between px-3 py-2 hover:bg-[var(--bg-tertiary)] transition-colors text-left"
                  aria-expanded={isExpanded}
                >
                  <span className="flex items-center gap-2 text-sm text-[var(--text-primary)]">
                    <Icon className="w-4 h-4 text-[var(--text-secondary)]" />
                    {ch.label}
                    {isConnected ? (
                      <span className="ml-2 text-xs text-[var(--color-green-500)]">{t('awareness.channels.connectedBadge')}</span>
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
