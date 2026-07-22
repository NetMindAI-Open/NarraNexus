/**
 * Context Panel - Agent awareness, social network list, and file upload
 * Bioluminescent Terminal style - Deep ocean aesthetics
 * Enhanced with Control Center Dashboard design
 */

import { useState, useMemo, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { RefreshCw, Brain, Clock, Users, Sparkles, Edit3, Save, X, MessageSquare, Network, TrendingUp, Search, Loader2 } from 'lucide-react';
import { Card, CardHeader, CardTitle, CardContent, Button, Markdown, Textarea, Dialog, DialogContent, DialogFooter, Input, StatStrip, ScrollArea } from '@/components/ui';
import { BracketEmptyState } from '@/components/nm';
import { usePreloadStore, useConfigStore } from '@/stores';
import { cn, formatRelativeTime } from '@/lib/utils';
import { api } from '@/lib/api';

import { EntityCard } from './EntityCard';
import { FileUpload } from './FileUpload';
import { IMChannelsSection } from './IMChannelsSection';
import { HomeAssistantConfig } from './HomeAssistantConfig';
import type { SocialNetworkEntity } from '@/types';

export type AwarenessSectionId = 'awareness' | 'workspace' | 'channels' | 'smarthome' | 'social';

interface AwarenessPanelProps {
  /** Skip the outer Card chrome + duplicate title when hosted inside the
   *  bookmark drawer. Functional actions are kept. */
  embedded?: boolean;
  /** Atomic mode: render exactly ONE section (bookmark-strip IA:
   *  one small tab = one content). Omit for the legacy full stack. */
  section?: AwarenessSectionId;
}

export function AwarenessPanel({ embedded = false, section }: AwarenessPanelProps = {}) {
  const { t } = useTranslation();
  const [isEditModalOpen, setIsEditModalOpen] = useState(false);
  const [editedAwareness, setEditedAwareness] = useState('');
  const [isSaving, setIsSaving] = useState(false);
  // Error banners are user-facing — DevTools console.error was the bug.
  const [saveError, setSaveError] = useState('');
  const [searchError, setSearchError] = useState('');

  // Search-related state
  const [searchQuery, setSearchQuery] = useState('');
  const [searchType, setSearchType] = useState<'keyword' | 'semantic'>('semantic');
  const [isSearching, setIsSearching] = useState(false);
  const [searchResults, setSearchResults] = useState<SocialNetworkEntity[]>([]);
  const [hasSearched, setHasSearched] = useState(false);

  const {
    awareness,
    awarenessUpdateTime,
    socialNetworkList,
    chatHistoryEvents,
    awarenessLoading,
    socialNetworkLoading,
    awarenessError,
    refreshAwareness,
    refreshSocialNetwork,
  } = usePreloadStore();

  const { agentId, userId, clearAwarenessUpdate } = useConfigStore();

  // Clear the red dot notification when the awareness tab is opened (component mounts)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (agentId) {
      clearAwarenessUpdate(agentId);
    }
  }, [agentId]);

  const handleRefresh = async () => {
    await Promise.all([
      refreshAwareness(agentId),
      refreshSocialNetwork(agentId),
    ]);
  };

  const handleOpenEditModal = () => {
    setEditedAwareness(awareness || '');
    setIsEditModalOpen(true);
  };

  const handleSaveAwareness = async () => {
    if (!agentId) return;

    setIsSaving(true);
    setSaveError('');

    try {
      const response = await api.updateAwareness(agentId, editedAwareness);

      if (response.success) {
        await refreshAwareness(agentId);
        setIsEditModalOpen(false);
      } else {
        setSaveError(response.error || t('awareness.panel.errSave'));
      }
    } catch (error) {
      setSaveError(error instanceof Error ? error.message : t('awareness.panel.errSave'));
    } finally {
      setIsSaving(false);
    }
  };

  // Search handler
  const handleSearch = async () => {
    if (!agentId || !searchQuery.trim()) return;

    setIsSearching(true);
    setHasSearched(true);
    setSearchError('');

    try {
      const response = await api.searchSocialNetwork(agentId, searchQuery.trim(), searchType, 10);
      if (response.success) {
        setSearchResults(response.entities);
      } else {
        setSearchError(response.error || t('awareness.panel.errSearch'));
        setSearchResults([]);
      }
    } catch (error) {
      setSearchError(error instanceof Error ? error.message : t('awareness.panel.errSearch'));
      setSearchResults([]);
    } finally {
      setIsSearching(false);
    }
  };

  // Clear search
  const handleClearSearch = () => {
    setSearchQuery('');
    setSearchResults([]);
    setHasSearched(false);
  };

  // Trigger search on Enter key
  const handleSearchKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      handleSearch();
    }
  };

  const isLoading = awarenessLoading || socialNetworkLoading;

  // Calculate actual chat count from chatHistoryEvents for each entity (memoized)
  const entityChatCountMap = useMemo(() => {
    const map = new Map<string, number>();
    chatHistoryEvents.forEach((event) => {
      if (event.user_id) {
        map.set(event.user_id, (map.get(event.user_id) || 0) + 1);
      }
    });
    return map;
  }, [chatHistoryEvents]);

  // Sort social network list: current user first, then by actual chat count (memoized)
  const sortedEntities = useMemo(() => {
    return [...socialNetworkList].sort((a, b) => {
      if (a.entity_id === userId) return -1;
      if (b.entity_id === userId) return 1;
      const countA = entityChatCountMap.get(a.entity_id) || 0;
      const countB = entityChatCountMap.get(b.entity_id) || 0;
      return countB - countA;
    });
  }, [socialNetworkList, userId, entityChatCountMap]);

  // Calculate network metrics
  const networkMetrics = useMemo(() => {
    const totalChats = chatHistoryEvents.length;
    const avgStrength = socialNetworkList.length > 0
      ? socialNetworkList.reduce((sum, e) => sum + e.relationship_strength, 0) / socialNetworkList.length
      : 0;
    const strongConnections = socialNetworkList.filter(e => e.relationship_strength >= 0.7).length;
    return { totalChats, avgStrength: Math.round(avgStrength * 100), strongConnections };
  }, [chatHistoryEvents, socialNetworkList]);

  const CardShell = embedded ? 'div' : Card;
  return (
    <>
      <CardShell className="flex flex-col h-full">
        <CardHeader className={cn(embedded && 'justify-end py-1')}>
          {!embedded && (
          <CardTitle>
            <Brain />
            {t('awareness.panel.context')}
          </CardTitle>
          )}
          <Button
            variant="ghost"
            size="icon"
            onClick={handleRefresh}
            disabled={isLoading}
            title={t('awareness.common.refresh')}
          >
            <RefreshCw className={cn('w-4 h-4', isLoading && 'animate-spin')} />
          </Button>
        </CardHeader>

        {/* Stat strip (social metrics) — only with the social section */}
        {(!section || section === 'social') && (
        <StatStrip
          items={[
            { label: t('awareness.panel.statContacts'), value: socialNetworkList.length, icon: Users },
            { label: t('awareness.panel.statChats'), value: networkMetrics.totalChats, icon: MessageSquare, tone: 'secondary' },
            { label: t('awareness.panel.statStrong'), value: networkMetrics.strongConnections, icon: TrendingUp, tone: 'success', subtext: t('awareness.panel.statAvg', { pct: networkMetrics.avgStrength }) },
          ]}
        />
        )}

        <CardContent className="flex-1 overflow-hidden min-h-0 !p-0">
        <ScrollArea className="h-full">
          {/* ── Section: Agent Awareness ── */}
          {(!section || section === 'awareness') && (
          <section className="px-5 pt-5 pb-6">
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-1.5 text-[10px] text-[var(--text-tertiary)] font-[family-name:var(--font-mono)] uppercase tracking-[0.16em]">
                <Sparkles className="w-3 h-3" />
                {t('awareness.panel.agentAwareness')}
              </div>
              <Button
                variant="ghost"
                size="sm"
                onClick={handleOpenEditModal}
                disabled={awarenessLoading}
                className="h-6 px-1.5"
              >
                <Edit3 className="w-3 h-3 mr-1" />
                {t('awareness.panel.edit')}
              </Button>
            </div>

            {awarenessLoading ? (
              <div className="animate-pulse space-y-2">
                <div className="h-3 bg-[var(--bg-tertiary)] w-3/4" />
                <div className="h-3 bg-[var(--bg-tertiary)] w-1/2" />
                <div className="h-3 bg-[var(--bg-tertiary)] w-2/3" />
              </div>
            ) : awarenessError ? (
              <div className="text-xs text-[var(--color-red-500)] py-2 font-[family-name:var(--font-mono)]">
                {awarenessError}
              </div>
            ) : awareness ? (
              // Framed thesis block: 2px ink on the left as emphasis,
              // hairline rules on the other three sides so the block
              // reads as a contained quote in both light and dark modes.
              <div
                className="pl-4 pr-4 py-3 border-t border-r border-b border-[var(--rule)]"
                style={{ borderLeft: '2px solid var(--text-primary)' }}
              >
                {/*
                 * Two layouts share this block:
                 *   - Embedded (the docked rail panel) shows ONLY this section,
                 *     so the outer ScrollArea (h-full) already scrolls it — an
                 *     inner cap here would strand the profile in the top ~40vh
                 *     and waste the rest of the tall panel. Render the markdown
                 *     directly and let it fill/scroll with the panel.
                 *   - Non-embedded (the full page) stacks every section, so the
                 *     profile keeps an inner max-h-[40vh] cap + its own scroll
                 *     to stay readable without pushing the other sections down.
                 *     `type="auto"` reveals the scrollbar on overflow, and
                 *     overscroll-contain (default in ./ui/scroll-area.tsx) keeps
                 *     the wheel inside the inner viewport until its boundary.
                 */}
                {embedded ? (
                  <div className="text-[13px] text-[var(--text-secondary)] leading-relaxed">
                    <Markdown content={awareness} />
                  </div>
                ) : (
                  <ScrollArea
                    type="auto"
                    className="max-h-[40vh] text-[13px] text-[var(--text-secondary)] leading-relaxed"
                  >
                    <Markdown content={awareness} />
                  </ScrollArea>
                )}
                {awarenessUpdateTime && (
                  <div className="mt-3 pt-3 border-t border-[var(--rule)] text-[10px] text-[var(--text-tertiary)] font-[family-name:var(--font-mono)] uppercase tracking-[0.1em] flex items-center gap-1.5">
                    <Clock className="w-3 h-3" />
                    {t('awareness.panel.updated', { time: formatRelativeTime(awarenessUpdateTime) })}
                  </div>
                )}
              </div>
            ) : (
              <BracketEmptyState
                label={t('awareness.panel.noAwarenessLabel')}
                hint={t('awareness.panel.noAwarenessHint')}
                className="!py-6"
              />
            )}
          </section>
          )}

          {/* ── Section: Workspace ── */}
          {(!section || section === 'workspace') && (
          <section className={cn('px-5 py-5', !section && 'border-t border-[var(--rule)]')}>
            <FileUpload />
          </section>
          )}

          {/* ── Section: IM Channels (Lark / Slack / Telegram) ── */}
          {(!section || section === 'channels') && (
          <section className={cn('px-5 py-5', !section && 'border-t border-[var(--rule)]')}>
            <IMChannelsSection />
          </section>
          )}

          {/* ── Section: Smart Home (Home Assistant) ── */}
          {(!section || section === 'smarthome') && (
          <section className={cn('px-5 py-5', !section && 'border-t border-[var(--rule)]')}>
            <HomeAssistantConfig />
          </section>
          )}

          {/* ── Section: Social Network ── */}
          {(!section || section === 'social') && (
          <section className={cn('px-5 pt-5 pb-6', !section && 'border-t border-[var(--rule)]')}>
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-1.5 text-[10px] text-[var(--text-tertiary)] font-[family-name:var(--font-mono)] uppercase tracking-[0.16em]">
                <Network className="w-3 h-3" />
                {t('awareness.panel.socialNetwork')}
              </div>
              <span className="text-[10px] font-[family-name:var(--font-mono)] text-[var(--text-tertiary)] tabular-nums">
                {socialNetworkList.length}
              </span>
            </div>

            {/* Search input + type toggle */}
            <div className="space-y-2 mb-3">
              <div className="flex gap-2">
                <div className="flex-1 relative">
                  <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-[var(--text-tertiary)]" />
                  <Input
                    type="text"
                    placeholder={t('awareness.panel.searchPlaceholder')}
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    onKeyDown={handleSearchKeyDown}
                    className="pl-8 h-8 text-[13px]"
                  />
                </div>
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={handleSearch}
                  disabled={isSearching || !searchQuery.trim()}
                  className="h-8 w-8"
                >
                  {isSearching ? (
                    <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  ) : (
                    <Search className="w-3.5 h-3.5" />
                  )}
                </Button>
              </div>
              <div className="flex items-center gap-1 text-[10px] font-[family-name:var(--font-mono)]">
                <button
                  onClick={() => setSearchType('semantic')}
                  className={cn(
                    'px-1.5 py-0.5 uppercase tracking-[0.1em] transition-colors',
                    searchType === 'semantic'
                      ? 'text-[var(--text-primary)] border-b border-[var(--text-primary)]'
                      : 'text-[var(--text-tertiary)] border-b border-transparent hover:text-[var(--text-primary)]'
                  )}
                >
                  {t('awareness.panel.semantic')}
                </button>
                <button
                  onClick={() => setSearchType('keyword')}
                  className={cn(
                    'px-1.5 py-0.5 uppercase tracking-[0.1em] transition-colors',
                    searchType === 'keyword'
                      ? 'text-[var(--text-primary)] border-b border-[var(--text-primary)]'
                      : 'text-[var(--text-tertiary)] border-b border-transparent hover:text-[var(--text-primary)]'
                  )}
                >
                  {t('awareness.panel.keyword')}
                </button>
                {hasSearched && (
                  <button
                    onClick={handleClearSearch}
                    className="ml-auto px-1.5 py-0.5 uppercase tracking-[0.1em] text-[var(--text-tertiary)] hover:text-[var(--color-red-500)] transition-colors"
                  >
                    {t('awareness.panel.clear')}
                  </button>
                )}
              </div>
            </div>

            {/* Search results */}
            {hasSearched && (
              <div className="mb-3">
                <div className="text-[10px] text-[var(--text-tertiary)] font-[family-name:var(--font-mono)] uppercase tracking-[0.14em] mb-2">
                  {isSearching ? t('awareness.panel.searching') : t('awareness.panel.resultsCount', { count: searchResults.length })}
                </div>
                {searchError && (
                  <div
                    role="alert"
                    className="text-xs text-[var(--color-red-500)] border border-[var(--color-red-500)] px-2 py-1.5 mb-2"
                  >
                    {searchError}
                  </div>
                )}
                {searchResults.length > 0 && (
                  <div className="space-y-1.5">
                    {searchResults.map((entity) => (
                      <EntityCard
                        key={`search-${entity.entity_id}`}
                        entity={entity}
                        isCurrentUser={entity.entity_id === userId}
                        actualChatCount={entityChatCountMap.get(entity.entity_id) || 0}
                      />
                    ))}
                  </div>
                )}
              </div>
            )}

            {/* Original list */}
            {!hasSearched && (
              socialNetworkLoading ? (
                <div className="space-y-2">
                  {[1, 2].map((i) => (
                    <div key={i} className="animate-pulse py-3 border-b border-[var(--rule)] last:border-b-0">
                      <div className="flex items-center gap-3">
                        <div className="w-8 h-8 bg-[var(--bg-tertiary)]" />
                        <div className="flex-1 space-y-2">
                          <div className="h-3 bg-[var(--bg-tertiary)] w-2/3" />
                          <div className="h-2 bg-[var(--bg-tertiary)] w-1/3" />
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              ) : sortedEntities.length === 0 ? (
                <BracketEmptyState
                  label={t('awareness.panel.noContactsLabel')}
                  hint={t('awareness.panel.noContactsHint')}
                  className="!py-6"
                />
              ) : (
                <div className="space-y-1.5">
                  {sortedEntities.map((entity) => (
                    <EntityCard
                      key={entity.entity_id}
                      entity={entity}
                      isCurrentUser={entity.entity_id === userId}
                      actualChatCount={entityChatCountMap.get(entity.entity_id) || 0}
                    />
                  ))}
                </div>
              )
            )}
          </section>
          )}

        </ScrollArea>
        </CardContent>
      </CardShell>

      {/* Edit Awareness Modal */}
      <Dialog
        isOpen={isEditModalOpen}
        onClose={() => setIsEditModalOpen(false)}
        title={t('awareness.panel.editModalTitle')}
        size="lg"
      >
        <DialogContent>
          <div className="space-y-3">
            <p className="text-xs text-[var(--text-tertiary)]">
              {t('awareness.panel.editModalDesc')}
            </p>
            <Textarea
              value={editedAwareness}
              onChange={(e) => setEditedAwareness(e.target.value)}
              placeholder={t('awareness.panel.editModalPlaceholder')}
              rows={12}
              className="font-mono text-sm resize-none"
            />
            {saveError && (
              <div
                role="alert"
                className="text-xs text-[var(--color-red-500)] border border-[var(--color-red-500)] px-2 py-1.5"
              >
                {saveError}
              </div>
            )}
          </div>
        </DialogContent>
        <DialogFooter>
          <Button
            variant="ghost"
            onClick={() => setIsEditModalOpen(false)}
            disabled={isSaving}
          >
            <X className="w-4 h-4 mr-2" />
            {t('awareness.common.cancel')}
          </Button>
          <Button
            variant="accent"
            onClick={handleSaveAwareness}
            disabled={isSaving}
          >
            {isSaving ? (
              <>
                <RefreshCw className="w-4 h-4 mr-2 animate-spin" />
                {t('awareness.panel.saving')}
              </>
            ) : (
              <>
                <Save className="w-4 h-4 mr-2" />
                {t('awareness.common.save')}
              </>
            )}
          </Button>
        </DialogFooter>
      </Dialog>
    </>
  );
}

export default AwarenessPanel;
