/**
 * Context Panel - Agent awareness, social network list, and file upload
 * Bioluminescent Terminal style - Deep ocean aesthetics
 * Enhanced with Control Center Dashboard design
 */

import { useState, useMemo, useEffect } from 'react';
import { RefreshCw, Brain, Clock, Users, Sparkles, Edit3, Save, X, MessageSquare, Network, TrendingUp, Search, Loader2 } from 'lucide-react';
import { Card, CardHeader, CardTitle, CardContent, Button, Badge, Markdown, Textarea, Dialog, DialogContent, DialogFooter, Input, StatStrip } from '@/components/ui';
import { usePreloadStore, useConfigStore } from '@/stores';
import { cn, formatRelativeTime } from '@/lib/utils';
import { api } from '@/lib/api';

import { EntityCard } from './EntityCard';
import { FileUpload } from './FileUpload';
import { MCPManager } from './MCPManager';
import { LarkConfig } from './LarkConfig';
import type { SocialNetworkEntity } from '@/types';

export function AwarenessPanel() {
  const [isEditModalOpen, setIsEditModalOpen] = useState(false);
  const [editedAwareness, setEditedAwareness] = useState('');
  const [isSaving, setIsSaving] = useState(false);

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

    try {
      const response = await api.updateAwareness(agentId, editedAwareness);

      if (response.success) {
        await refreshAwareness(agentId);
        setIsEditModalOpen(false);
      } else {
        console.error('[AwarenessPanel] Failed to update awareness:', response.error);
      }
    } catch (error) {
      console.error('[AwarenessPanel] Error updating awareness:', error);
    } finally {
      setIsSaving(false);
    }
  };

  // Search handler
  const handleSearch = async () => {
    if (!agentId || !searchQuery.trim()) return;

    setIsSearching(true);
    setHasSearched(true);

    try {
      const response = await api.searchSocialNetwork(agentId, searchQuery.trim(), searchType, 10);
      if (response.success) {
        setSearchResults(response.entities);
      } else {
        console.error('Search failed:', response.error);
        setSearchResults([]);
      }
    } catch (error) {
      console.error('Search error:', error);
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

  return (
    <>
      <Card className="flex flex-col h-full">
        <CardHeader>
          <CardTitle>
            <Brain />
            Context
          </CardTitle>
          <Button
            variant="ghost"
            size="icon"
            onClick={handleRefresh}
            disabled={isLoading}
            title="Refresh"
          >
            <RefreshCw className={cn('w-4 h-4', isLoading && 'animate-spin')} />
          </Button>
        </CardHeader>

        {/* Stat strip — rule-separated, no nested boxes */}
        <StatStrip
          items={[
            { label: 'Contacts', value: socialNetworkList.length, icon: Users },
            { label: 'Chats', value: networkMetrics.totalChats, icon: MessageSquare, tone: 'secondary' },
            { label: 'Strong', value: networkMetrics.strongConnections, icon: TrendingUp, tone: 'success', subtext: `${networkMetrics.avgStrength}% avg` },
          ]}
        />

        <CardContent className="flex-1 overflow-y-auto min-h-0 !p-0">
          {/* ── Section: Agent Awareness ── */}
          <section className="px-5 pt-5 pb-6">
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-1.5 text-[10px] text-[var(--text-tertiary)] font-[family-name:var(--font-mono)] uppercase tracking-[0.16em]">
                <Sparkles className="w-3 h-3" />
                Agent Awareness
              </div>
              <Button
                variant="ghost"
                size="sm"
                onClick={handleOpenEditModal}
                disabled={awarenessLoading}
                className="h-6 px-1.5"
              >
                <Edit3 className="w-3 h-3 mr-1" />
                Edit
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
                <div className="text-[13px] max-h-[180px] overflow-y-auto text-[var(--text-secondary)] leading-relaxed">
                  <Markdown content={awareness} />
                </div>
                {awarenessUpdateTime && (
                  <div className="mt-3 pt-3 border-t border-[var(--rule)] text-[10px] text-[var(--text-tertiary)] font-[family-name:var(--font-mono)] uppercase tracking-[0.1em] flex items-center gap-1.5">
                    <Clock className="w-3 h-3" />
                    Updated {formatRelativeTime(awarenessUpdateTime)}
                  </div>
                )}
              </div>
            ) : (
              <div className="text-xs text-[var(--text-tertiary)] py-6 text-center">
                <Brain className="w-5 h-5 mx-auto mb-2 opacity-30" />
                No awareness data
              </div>
            )}
          </section>

          {/* ── Section: Social Network ── */}
          <section className="px-5 pt-5 pb-6 border-t border-[var(--rule)]">
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-1.5 text-[10px] text-[var(--text-tertiary)] font-[family-name:var(--font-mono)] uppercase tracking-[0.16em]">
                <Network className="w-3 h-3" />
                Social Network
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
                    placeholder="Search contacts…"
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
                  Semantic
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
                  Keyword
                </button>
                {hasSearched && (
                  <button
                    onClick={handleClearSearch}
                    className="ml-auto px-1.5 py-0.5 uppercase tracking-[0.1em] text-[var(--text-tertiary)] hover:text-[var(--color-red-500)] transition-colors"
                  >
                    Clear
                  </button>
                )}
              </div>
            </div>

            {/* Search results */}
            {hasSearched && (
              <div className="mb-3">
                <div className="text-[10px] text-[var(--text-tertiary)] font-[family-name:var(--font-mono)] uppercase tracking-[0.14em] mb-2">
                  {isSearching ? 'Searching…' : `${searchResults.length} results`}
                </div>
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
                <div className="text-xs text-[var(--text-tertiary)] py-6 text-center">
                  <Users className="w-5 h-5 mx-auto mb-2 opacity-30" />
                  No contacts yet
                </div>
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

          {/* ── Section: File Upload ── */}
          <section className="border-t border-[var(--rule)] px-5 py-5">
            <FileUpload />
          </section>

          {/* ── Section: MCP ── */}
          <section className="border-t border-[var(--rule)] px-5 py-5">
            <MCPManager />
          </section>

          {/* ── Section: Lark ── */}
          <section className="border-t border-[var(--rule)] px-5 py-5">
            <LarkConfig />
          </section>
        </CardContent>
      </Card>

      {/* Edit Awareness Modal */}
      <Dialog
        isOpen={isEditModalOpen}
        onClose={() => setIsEditModalOpen(false)}
        title="Edit Agent Awareness"
        size="lg"
      >
        <DialogContent>
          <div className="space-y-3">
            <p className="text-xs text-[var(--text-tertiary)]">
              Edit the agent's self-awareness text. This helps the agent understand its current state, goals, and context.
            </p>
            <Textarea
              value={editedAwareness}
              onChange={(e) => setEditedAwareness(e.target.value)}
              placeholder="Enter agent awareness..."
              rows={12}
              className="font-mono text-sm resize-none"
            />
          </div>
        </DialogContent>
        <DialogFooter>
          <Button
            variant="ghost"
            onClick={() => setIsEditModalOpen(false)}
            disabled={isSaving}
          >
            <X className="w-4 h-4 mr-2" />
            Cancel
          </Button>
          <Button
            variant="default"
            onClick={handleSaveAwareness}
            disabled={isSaving}
            className="bg-[var(--accent-primary)] hover:bg-[var(--accent-primary)]/80"
          >
            {isSaving ? (
              <>
                <RefreshCw className="w-4 h-4 mr-2 animate-spin" />
                Saving...
              </>
            ) : (
              <>
                <Save className="w-4 h-4 mr-2" />
                Save
              </>
            )}
          </Button>
        </DialogFooter>
      </Dialog>
    </>
  );
}

export default AwarenessPanel;
