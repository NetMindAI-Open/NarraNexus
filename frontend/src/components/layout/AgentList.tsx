/**
 * AgentList - Agent selection, creation, editing, and management
 * Shows running indicators and completion badges for multi-agent concurrent chat.
 */

import { useState, useEffect } from 'react';
import {
  Bot,
  RefreshCw,
  Circle,
  Plus,
  Pencil,
  Check,
  X,
  Globe,
  Lock,
  Trash2,
  Loader2,
} from 'lucide-react';
import { Button } from '@/components/ui';
import { useConfigStore, useChatStore } from '@/stores';
import { api } from '@/lib/api';
import { cn } from '@/lib/utils';

interface AgentListProps {
  collapsed: boolean;
}

export function AgentList({ collapsed }: AgentListProps) {
  const [loadingAgents, setLoadingAgents] = useState(false);
  const [creatingAgent, setCreatingAgent] = useState(false);
  const [editingAgentId, setEditingAgentId] = useState<string | null>(null);
  const [editingName, setEditingName] = useState('');
  const [savingName, setSavingName] = useState(false);
  const [deletingAgentId, setDeletingAgentId] = useState<string | null>(null);

  const { userId, agentId, agents, setAgentId, setAgents, refreshAgents } = useConfigStore();
  const { setActiveAgent, clearAgent, isAgentStreaming, completedAgentIds } = useChatStore();

  // Fetch agents on mount
  useEffect(() => {
    fetchAgents();
  }, []);

  const fetchAgents = async () => {
    setLoadingAgents(true);
    try {
      await refreshAgents();
      const currentAgents = useConfigStore.getState().agents;
      if (!agentId && currentAgents.length > 0) {
        setAgentId(currentAgents[0].agent_id);
      }
    } catch (err) {
      console.error('Failed to fetch agents:', err);
    } finally {
      setLoadingAgents(false);
    }
  };

  const handleSelectAgent = (id: string) => {
    if (id !== agentId) {
      setAgentId(id);
      setActiveAgent(id); // Also clears completion badge for this agent
    }
  };

  const handleCreateAgent = async () => {
    setCreatingAgent(true);
    try {
      const res = await api.createAgent(userId);
      if (res.success && res.agent) {
        const newAgent = {
          agent_id: res.agent.agent_id,
          name: res.agent.name,
          description: res.agent.description,
          status: res.agent.status,
          created_at: res.agent.created_at,
          created_by: userId,
          bootstrap_active: res.agent.bootstrap_active,
        };
        setAgents([newAgent, ...agents]);
        setAgentId(res.agent.agent_id);
        setActiveAgent(res.agent.agent_id);
      } else {
        console.error('Failed to create agent:', res.error);
      }
    } catch (err) {
      console.error('Error creating agent:', err);
    } finally {
      setCreatingAgent(false);
    }
  };

  const handleTogglePublic = async (agent: typeof agents[0], e: React.MouseEvent) => {
    e.stopPropagation();
    const newIsPublic = !agent.is_public;
    try {
      const res = await api.updateAgent(agent.agent_id, undefined, undefined, newIsPublic);
      if (res.success) {
        setAgents(agents.map(a =>
          a.agent_id === agent.agent_id ? { ...a, is_public: newIsPublic } : a
        ));
      } else {
        console.error('Failed to toggle public:', res.error);
      }
    } catch (err) {
      console.error('Error toggling public:', err);
    }
  };

  const handleStartEdit = (agent: typeof agents[0], e: React.MouseEvent) => {
    e.stopPropagation();
    setEditingAgentId(agent.agent_id);
    setEditingName(agent.name || agent.agent_id);
  };

  const handleCancelEdit = (e: React.MouseEvent) => {
    e.stopPropagation();
    setEditingAgentId(null);
    setEditingName('');
  };

  const handleSaveEdit = async (targetAgentId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!editingName.trim()) return;

    setSavingName(true);
    try {
      const res = await api.updateAgent(targetAgentId, editingName.trim());
      if (res.success && res.agent) {
        setAgents(agents.map(a =>
          a.agent_id === targetAgentId
            ? { ...a, name: res.agent?.name }
            : a
        ));
        setEditingAgentId(null);
        setEditingName('');
      } else {
        console.error('Failed to update agent:', res.error);
      }
    } catch (err) {
      console.error('Error updating agent:', err);
    } finally {
      setSavingName(false);
    }
  };

  const handleDeleteAgent = async (agent: typeof agents[0], e: React.MouseEvent) => {
    e.stopPropagation();
    if (!confirm(`Delete agent "${agent.name || agent.agent_id}"? This will permanently remove all related data (narratives, events, instances, jobs, etc.).`)) {
      return;
    }

    setDeletingAgentId(agent.agent_id);
    try {
      const res = await api.deleteAgent(agent.agent_id, userId);
      if (res.success) {
        const remaining = agents.filter(a => a.agent_id !== agent.agent_id);
        setAgents(remaining);
        clearAgent(agent.agent_id);
        if (agentId === agent.agent_id) {
          if (remaining.length > 0) {
            setAgentId(remaining[0].agent_id);
            setActiveAgent(remaining[0].agent_id);
          } else {
            setAgentId('');
          }
        }
      } else {
        console.error('Failed to delete agent:', res.error);
        alert(`Failed to delete agent: ${res.error}`);
      }
    } catch (err) {
      console.error('Error deleting agent:', err);
      alert('Error deleting agent. Please try again.');
    } finally {
      setDeletingAgentId(null);
    }
  };

  /** Render agent status icon (running spinner, completed badge, or default) */
  const renderAgentStatusIcon = (id: string, isSelected: boolean) => {
    const streaming = isAgentStreaming(id);
    const completed = completedAgentIds.includes(id);

    if (streaming) {
      return (
        <Loader2 className="w-5 h-5 animate-spin text-[var(--accent-primary)]" />
      );
    }

    return (
      <Bot className={cn(
        'w-5 h-5 transition-colors',
        isSelected
          ? 'text-[var(--accent-primary)]'
          : completed
            ? 'text-[var(--accent-primary)]'
            : 'text-[var(--text-secondary)] group-hover:text-[var(--accent-primary)]'
      )} />
    );
  };

  // Collapsed mode: show compact agent icons
  if (collapsed) {
    return (
      <div className="p-2 space-y-2">
        <button
          onClick={handleCreateAgent}
          disabled={creatingAgent}
          className={cn(
            'w-full aspect-square rounded-xl flex items-center justify-center transition-all',
            'bg-[var(--bg-tertiary)] text-[var(--accent-primary)]',
            'hover:bg-[var(--accent-glow)] hover:shadow-[0_0_20px_var(--accent-glow)]',
            'border border-dashed border-[var(--accent-primary)]/30',
            creatingAgent && 'opacity-50 cursor-not-allowed'
          )}
          title="Create New Agent"
        >
          <Plus className={cn('w-5 h-5', creatingAgent && 'animate-pulse')} />
        </button>
        {agents.slice(0, 4).map((agent, index) => {
          const isSelected = agentId === agent.agent_id;
          const completed = completedAgentIds.includes(agent.agent_id);
          return (
            <div key={agent.agent_id} className="relative">
              <button
                onClick={() => handleSelectAgent(agent.agent_id)}
                className={cn(
                  'w-full aspect-square rounded-xl flex items-center justify-center transition-all duration-300',
                  'animate-fade-in',
                  isSelected
                    ? 'bg-[var(--accent-primary)]/20 shadow-[0_0_20px_var(--accent-glow)]'
                    : 'bg-[var(--bg-tertiary)] border border-[var(--border-default)] text-[var(--text-secondary)] hover:border-[var(--accent-primary)]/50 hover:shadow-[0_0_15px_var(--accent-glow)]'
                )}
                style={{ animationDelay: `${index * 50}ms` }}
                title={agent.agent_id}
              >
                {renderAgentStatusIcon(agent.agent_id, isSelected)}
              </button>
              {/* Completion badge dot */}
              {completed && !isSelected && (
                <div className="absolute -top-0.5 -right-0.5 w-2.5 h-2.5 rounded-full bg-[var(--accent-primary)] border-2 border-[var(--bg-deep)] shadow-[0_0_6px_var(--accent-primary)]" />
              )}
            </div>
          );
        })}
      </div>
    );
  }

  // Expanded mode: full agent list
  return (
    <div className="p-3">
      <div className="flex items-center justify-between mb-3 px-1">
        <span className="text-[10px] font-semibold text-[var(--text-tertiary)] uppercase tracking-[0.15em] font-[family-name:var(--font-mono)]">
          Agents
        </span>
        <div className="flex items-center gap-1">
          <Button
            variant="ghost"
            size="icon"
            onClick={handleCreateAgent}
            disabled={creatingAgent}
            className="w-7 h-7"
            title="Create New Agent"
          >
            <Plus className={cn('w-3.5 h-3.5', creatingAgent && 'animate-pulse')} />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            onClick={fetchAgents}
            disabled={loadingAgents}
            className="w-7 h-7"
            title="Refresh Agents"
          >
            <RefreshCw className={cn('w-3 h-3', loadingAgents && 'animate-spin')} />
          </Button>
        </div>
      </div>

      {agents.length === 0 ? (
        <div className="text-center py-10 px-4">
          <div className="w-16 h-16 mx-auto mb-4 rounded-2xl bg-[var(--bg-tertiary)] flex items-center justify-center border border-dashed border-[var(--border-default)]">
            <Bot className="w-8 h-8 text-[var(--text-tertiary)]" />
          </div>
          <p className="text-sm text-[var(--text-tertiary)] mb-4">No agents found</p>
          <Button
            variant="outline"
            size="sm"
            onClick={handleCreateAgent}
            disabled={creatingAgent}
            className="gap-1.5"
          >
            <Plus className="w-3.5 h-3.5" />
            Create Agent
          </Button>
        </div>
      ) : (
        <div className="space-y-1.5">
          {agents.map((agent, index) => {
            const isSelected = agentId === agent.agent_id;
            const streaming = isAgentStreaming(agent.agent_id);
            const completed = completedAgentIds.includes(agent.agent_id);

            return (
              <div
                key={agent.agent_id}
                onClick={() => handleSelectAgent(agent.agent_id)}
                className={cn(
                  'w-full text-left p-3 rounded-xl transition-all duration-300 cursor-pointer',
                  'hover:bg-[var(--bg-tertiary)] group relative',
                  'animate-slide-up',
                  isSelected && [
                    'bg-[var(--accent-primary)]/10',
                    'border border-[var(--accent-primary)]/30',
                    'shadow-[0_0_30px_var(--accent-glow),inset_0_0_20px_var(--accent-glow)]',
                  ],
                  !isSelected && 'border border-transparent'
                )}
                style={{ animationDelay: `${index * 50}ms` }}
              >
                {/* Active indicator bar */}
                {isSelected && (
                  <div className="absolute left-0 top-1/2 -translate-y-1/2 w-[3px] h-8 bg-[var(--accent-primary)] rounded-r-full shadow-[0_0_10px_var(--accent-primary)]" />
                )}

                <div className="flex items-start gap-3">
                  <div
                    className={cn(
                      'w-10 h-10 rounded-xl flex items-center justify-center shrink-0 transition-all duration-300 relative',
                      isSelected
                        ? 'bg-[var(--accent-primary)]/20 shadow-[0_0_20px_var(--accent-glow)]'
                        : 'bg-[var(--bg-tertiary)] border border-[var(--border-default)] group-hover:border-[var(--accent-primary)]/50 group-hover:shadow-[0_0_15px_var(--accent-glow)]'
                    )}
                  >
                    {renderAgentStatusIcon(agent.agent_id, isSelected)}
                    {/* Completion badge dot */}
                    {completed && !isSelected && (
                      <div className="absolute -top-1 -right-1 w-3 h-3 rounded-full bg-[var(--accent-primary)] border-2 border-[var(--bg-deep)] shadow-[0_0_6px_var(--accent-primary)]" />
                    )}
                  </div>
                  <div className="flex-1 min-w-0 pt-0.5">
                    {editingAgentId === agent.agent_id ? (
                      /* Editing Mode */
                      <div className="flex items-center gap-1" onClick={e => e.stopPropagation()}>
                        <input
                          type="text"
                          value={editingName}
                          onChange={e => setEditingName(e.target.value)}
                          className="flex-1 min-w-0 px-2 py-0.5 text-sm font-mono text-[var(--text-primary)] bg-[var(--bg-tertiary)] border border-[var(--accent-primary)]/50 rounded focus:outline-none focus:border-[var(--accent-primary)]"
                          autoFocus
                          onKeyDown={e => {
                            if (e.key === 'Enter') handleSaveEdit(agent.agent_id, e as any);
                            if (e.key === 'Escape') handleCancelEdit(e as any);
                          }}
                        />
                        <button
                          onClick={(e) => handleSaveEdit(agent.agent_id, e)}
                          disabled={savingName}
                          className="p-1 shrink-0 hover:bg-[var(--color-success)]/20 rounded transition-colors"
                          title="Save (Enter)"
                        >
                          <Check className={cn('w-3.5 h-3.5 text-[var(--color-success)]', savingName && 'animate-pulse')} />
                        </button>
                        <button
                          onClick={handleCancelEdit}
                          className="p-1 shrink-0 hover:bg-[var(--color-error)]/20 rounded transition-colors"
                          title="Cancel (Esc)"
                        >
                          <X className="w-3.5 h-3.5 text-[var(--color-error)]" />
                        </button>
                      </div>
                    ) : (
                      /* Display Mode */
                      <>
                        <div className="flex items-center gap-1.5">
                          <span
                            className={cn(
                              'font-mono text-sm truncate transition-colors',
                              isSelected
                                ? 'text-[var(--accent-primary)] font-semibold'
                                : 'text-[var(--text-primary)] group-hover:text-[var(--accent-primary)]'
                            )}
                          >
                            {agent.name || agent.agent_id}
                          </span>
                          {agent.is_public && agent.created_by !== userId && (
                            <span title={`Public · by ${agent.created_by}`}>
                              <Globe className="w-3 h-3 text-[var(--text-tertiary)] shrink-0" />
                            </span>
                          )}
                          {/* Running indicator */}
                          {streaming && (
                            <span className="text-[9px] px-1.5 py-0.5 rounded-full bg-[var(--accent-primary)]/15 text-[var(--accent-primary)] font-medium uppercase tracking-wider">
                              Running
                            </span>
                          )}
                          {/* Selected indicator (only when not streaming) */}
                          {isSelected && !streaming && (
                            <Circle className="w-2 h-2 shrink-0 fill-[var(--color-success)] text-[var(--color-success)] animate-pulse" />
                          )}
                        </div>
                        {isSelected && (
                          <div className="flex items-center gap-0.5 mt-1">
                            {agent.created_by === userId && (
                              <button
                                onClick={(e) => handleTogglePublic(agent, e)}
                                className="p-1 hover:bg-[var(--bg-tertiary)] rounded transition-all"
                                title={agent.is_public ? 'Set to Private' : 'Set to Public'}
                              >
                                {agent.is_public ? (
                                  <Globe className="w-3 h-3 text-[var(--accent-primary)]" />
                                ) : (
                                  <Lock className="w-3 h-3 text-[var(--text-tertiary)] hover:text-[var(--accent-primary)]" />
                                )}
                              </button>
                            )}
                            <button
                              onClick={(e) => handleStartEdit(agent, e)}
                              className="p-1 hover:bg-[var(--bg-tertiary)] rounded transition-all"
                              title="Edit name"
                            >
                              <Pencil className="w-3 h-3 text-[var(--text-tertiary)] hover:text-[var(--accent-primary)]" />
                            </button>
                            {agent.created_by === userId && (
                              <button
                                onClick={(e) => handleDeleteAgent(agent, e)}
                                disabled={deletingAgentId === agent.agent_id}
                                className="p-1 hover:bg-[var(--color-error)]/10 rounded transition-all"
                                title="Delete agent"
                              >
                                <Trash2 className={cn(
                                  'w-3 h-3 text-[var(--text-tertiary)] hover:text-[var(--color-error)]',
                                  deletingAgentId === agent.agent_id && 'animate-pulse'
                                )} />
                              </button>
                            )}
                          </div>
                        )}
                      </>
                    )}
                    {agent.description && editingAgentId !== agent.agent_id && (
                      <p className="text-xs text-[var(--text-tertiary)] mt-1 line-clamp-2 leading-relaxed">
                        {agent.description}
                      </p>
                    )}
                    {agent.name && agent.name !== agent.agent_id && editingAgentId !== agent.agent_id && (
                      <p className="text-[10px] text-[var(--text-tertiary)] mt-0.5 font-mono opacity-60">
                        {agent.agent_id}
                      </p>
                    )}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
