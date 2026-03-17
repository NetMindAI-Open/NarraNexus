/**
 * Context Panel - Agent awareness, social network list, and file upload
 * Bioluminescent Terminal style - Deep ocean aesthetics
 * Enhanced with Control Center Dashboard design
 */

import { useState, useMemo, useEffect } from 'react';
import { RefreshCw, Brain, Clock, Users, Sparkles, Edit3, Save, X, MessageSquare, Network, TrendingUp, Search, Loader2 } from 'lucide-react';
import { Card, CardHeader, CardTitle, CardContent, Button, Badge, Markdown, Textarea, Dialog, DialogContent, DialogFooter, Input, KPICard } from '@/components/ui';
import { usePreloadStore, useConfigStore } from '@/stores';
import { cn, formatRelativeTime } from '@/lib/utils';
import { api } from '@/lib/api';
import { EntityCard } from './EntityCard';
import { FileUpload } from './FileUpload';
import { RAGUpload } from './RAGUpload';
import { MCPManager } from './MCPManager';
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
      <Card variant="glass" className="flex flex-col h-full">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <div className="w-7 h-7 rounded-lg bg-[var(--accent-secondary)]/10 flex items-center justify-center">
              <Brain className="w-4 h-4 text-[var(--accent-secondary)]" />
            </div>
            <span>Context</span>
          </CardTitle>
          <Button
            variant="ghost"
            size="icon"
            onClick={handleRefresh}
            disabled={isLoading}
            title="Refresh"
            className="hover:bg-[var(--accent-glow)]"
          >
            <RefreshCw className={cn('w-4 h-4', isLoading && 'animate-spin')} />
          </Button>
        </CardHeader>

        <CardContent className="flex-1 overflow-y-auto space-y-4 min-h-0">
          {/* Dashboard KPI Section */}
          <div className="grid grid-cols-3 gap-2">
            <KPICard
              label="Contacts"
              value={socialNetworkList.length}
              icon={Users}
              color="accent"
              subtext="In network"
            />
            <KPICard
              label="Chats"
              value={networkMetrics.totalChats}
              icon={MessageSquare}
              color="secondary"
              subtext="Total interactions"
            />
            <KPICard
              label="Strong"
              value={networkMetrics.strongConnections}
              icon={TrendingUp}
              color="success"
              subtext={`${networkMetrics.avgStrength}% avg`}
            />
          </div>

          {/* Agent Awareness Section */}
          <section className="space-y-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2 text-[10px] text-[var(--accent-secondary)] font-medium uppercase tracking-wider">
                <Sparkles className="w-3 h-3" />
                Agent Awareness
              </div>
              <Button
                variant="ghost"
                size="sm"
                onClick={handleOpenEditModal}
                disabled={awarenessLoading}
                className="h-7 px-2 text-[10px] hover:bg-[var(--accent-glow)] hover:text-[var(--accent-primary)]"
              >
                <Edit3 className="w-3 h-3 mr-1" />
                Edit
              </Button>
            </div>

            {awarenessLoading ? (
              <div className="animate-pulse rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elevated)] p-4">
                <div className="space-y-2">
                  <div className="h-3 bg-[var(--bg-tertiary)] rounded w-3/4" />
                  <div className="h-3 bg-[var(--bg-tertiary)] rounded w-1/2" />
                  <div className="h-3 bg-[var(--bg-tertiary)] rounded w-2/3" />
                </div>
              </div>
            ) : awarenessError ? (
              <div className="text-xs text-[var(--color-error)] p-3 bg-[var(--color-error)]/10 rounded-xl border border-[var(--color-error)]/20">
                {awarenessError}
              </div>
            ) : awareness ? (
              <div className="p-3 bg-[var(--bg-elevated)] rounded-xl border border-[var(--border-subtle)] space-y-2 relative overflow-hidden">
                {/* Subtle glow effect */}
                <div className="absolute top-0 left-0 w-full h-1 bg-gradient-to-r from-transparent via-[var(--accent-secondary)]/20 to-transparent" />
                <div className="text-xs max-h-[150px] overflow-y-auto text-[var(--text-secondary)] leading-relaxed">
                  <Markdown content={awareness} />
                </div>
                {awarenessUpdateTime && (
                  <div className="text-[9px] text-[var(--text-tertiary)] font-mono flex items-center gap-1.5 pt-2 border-t border-[var(--border-subtle)]">
                    <Clock className="w-3 h-3 text-[var(--accent-secondary)]" />
                    Updated {formatRelativeTime(awarenessUpdateTime)}
                  </div>
                )}
              </div>
            ) : (
              <div className="text-xs text-[var(--text-tertiary)] p-6 bg-[var(--bg-sunken)] rounded-xl border border-[var(--border-subtle)] text-center">
                <Brain className="w-6 h-6 mx-auto mb-2 opacity-30" />
                No awareness data
              </div>
            )}
          </section>

          {/* Divider with glow */}
          <div className="relative">
            <div className="border-t border-[var(--border-subtle)]" />
            <div className="absolute top-0 left-1/2 -translate-x-1/2 w-16 h-px bg-gradient-to-r from-transparent via-[var(--accent-primary)]/30 to-transparent" />
          </div>

          {/* Social Network Section */}
          <section className="space-y-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2 text-[10px] text-[var(--accent-primary)] font-medium uppercase tracking-wider">
                <Network className="w-3 h-3" />
                Social Network
              </div>
              <Badge variant="default" size="sm" className="font-mono">{socialNetworkList.length}</Badge>
            </div>

            {/* Semantic search box */}
            <div className="space-y-2">
              <div className="flex gap-2">
                <div className="flex-1 relative">
                  <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-[var(--text-tertiary)]" />
                  <Input
                    type="text"
                    placeholder="Search contacts..."
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    onKeyDown={handleSearchKeyDown}
                    className="pl-9 pr-3 h-9 text-xs bg-[var(--bg-elevated)] border-[var(--border-subtle)] focus:border-[var(--accent-primary)]/50"
                  />
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={handleSearch}
                  disabled={isSearching || !searchQuery.trim()}
                  className="h-9 px-3 hover:bg-[var(--accent-glow)]"
                >
                  {isSearching ? (
                    <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  ) : (
                    <Search className="w-3.5 h-3.5" />
                  )}
                </Button>
              </div>

              {/* Search type toggle */}
              <div className="flex items-center gap-2">
                <button
                  onClick={() => setSearchType('semantic')}
                  className={cn(
                    'px-2 py-1 text-[9px] rounded-lg transition-all duration-200 font-mono',
                    searchType === 'semantic'
                      ? 'bg-[var(--accent-primary)] text-[var(--bg-deep)]'
                      : 'bg-[var(--bg-elevated)] text-[var(--text-tertiary)] hover:text-[var(--text-secondary)]'
                  )}
                >
                  Semantic
                </button>
                <button
                  onClick={() => setSearchType('keyword')}
                  className={cn(
                    'px-2 py-1 text-[9px] rounded-lg transition-all duration-200 font-mono',
                    searchType === 'keyword'
                      ? 'bg-[var(--accent-primary)] text-[var(--bg-deep)]'
                      : 'bg-[var(--bg-elevated)] text-[var(--text-tertiary)] hover:text-[var(--text-secondary)]'
                  )}
                >
                  Keyword
                </button>
                {hasSearched && (
                  <button
                    onClick={handleClearSearch}
                    className="ml-auto px-2 py-1 text-[9px] rounded-lg bg-[var(--bg-elevated)] text-[var(--text-tertiary)] hover:text-[var(--color-error)] transition-colors font-mono"
                  >
                    Clear
                  </button>
                )}
              </div>
            </div>

            {/* Search results */}
            {hasSearched && (
              <div className="space-y-2">
                <div className="text-[9px] text-[var(--text-tertiary)] font-mono">
                  {isSearching ? 'Searching...' : `${searchResults.length} results found`}
                </div>
                {searchResults.length > 0 && (
                  <div className="space-y-2 p-2 bg-[var(--accent-glow)] rounded-xl border border-[var(--accent-primary)]/20">
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

            {/* Original list (hidden during search) */}
            {!hasSearched && (
              socialNetworkLoading ? (
                <div className="space-y-2">
                  {[1, 2].map((i) => (
                    <div key={i} className="animate-pulse rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elevated)] p-3">
                      <div className="flex items-center gap-3">
                        <div className="w-9 h-9 rounded-xl bg-[var(--bg-tertiary)]" />
                        <div className="flex-1 space-y-2">
                          <div className="h-3 bg-[var(--bg-tertiary)] rounded w-2/3" />
                          <div className="h-2 bg-[var(--bg-tertiary)] rounded w-1/3" />
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              ) : sortedEntities.length === 0 ? (
                <div className="text-xs text-[var(--text-tertiary)] p-6 bg-[var(--bg-sunken)] rounded-xl border border-[var(--border-subtle)] text-center">
                  <Users className="w-6 h-6 mx-auto mb-2 opacity-30" />
                  No contacts yet
                </div>
              ) : (
                <div className="space-y-2">
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

          {/* Divider with glow */}
          <div className="relative">
            <div className="border-t border-[var(--border-subtle)]" />
            <div className="absolute top-0 left-1/2 -translate-x-1/2 w-16 h-px bg-gradient-to-r from-transparent via-[var(--color-success)]/30 to-transparent" />
          </div>

          {/* RAG Upload Section */}
          <RAGUpload />

          {/* Divider */}
          <div className="border-t border-[var(--border-subtle)]" />

          {/* File Upload Section */}
          <FileUpload />

          {/* Divider */}
          <div className="border-t border-[var(--border-subtle)]" />

          {/* MCP Manager Section */}
          <MCPManager />
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
