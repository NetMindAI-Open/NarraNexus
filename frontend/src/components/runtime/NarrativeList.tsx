/**
 * Narrative List - Display narratives with expandable events
 * Bioluminescent Terminal style - Deep ocean aesthetics
 */

import { useState, useMemo } from 'react';
import { ChevronDown, ChevronRight, BookOpen, Clock, Users, MessageSquare, MessageCircle, Database, Briefcase, Brain, User, Box, Sparkles } from 'lucide-react';
import { Badge } from '@/components/ui';
import { usePreloadStore } from '@/stores';
import { cn, formatDate } from '@/lib/utils';
import { EventCard } from './EventCard';
import type { ChatHistoryNarrative, ChatHistoryEvent, InstanceInfo } from '@/types';

// Module type icon and color config - Bioluminescent colors
const MODULE_CONFIG: Record<string, { icon: React.ElementType; colorClass: string; bgClass: string; label: string }> = {
  ChatModule: { icon: MessageCircle, colorClass: 'text-[var(--accent-primary)]', bgClass: 'bg-[var(--accent-glow)]', label: 'Chat' },
  JobModule: { icon: Briefcase, colorClass: 'text-[var(--color-warning)]', bgClass: 'bg-[var(--color-warning)]/10', label: 'Job' },
  AwarenessModule: { icon: Brain, colorClass: 'text-[var(--accent-secondary)]', bgClass: 'bg-[var(--accent-secondary)]/10', label: 'Awareness' },
  SocialNetworkModule: { icon: User, colorClass: 'text-pink-400', bgClass: 'bg-pink-500/10', label: 'Social' },
  BasicInfoModule: { icon: User, colorClass: 'text-[var(--text-tertiary)]', bgClass: 'bg-[var(--bg-tertiary)]', label: 'Basic Info' },
};

const getModuleConfig = (moduleClass: string) => {
  return MODULE_CONFIG[moduleClass] || { icon: Box, colorClass: 'text-[var(--text-tertiary)]', bgClass: 'bg-[var(--bg-tertiary)]', label: moduleClass };
};

interface NarrativeItemProps {
  narrative: ChatHistoryNarrative;
  eventCount: number;
  isExpanded: boolean;
  onToggle: () => void;
}

// Memory layer component - displays Events
interface MemoryItemProps {
  events: ChatHistoryEvent[];
  isExpanded: boolean;
  onToggle: () => void;
}

function MemoryItem({ events, isExpanded, onToggle }: MemoryItemProps) {
  return (
    <div className="ml-4 border-l-2 border-[var(--color-success)]/30 pl-3">
      <button
        onClick={onToggle}
        className={cn(
          'w-full p-2.5 flex items-center gap-2.5 text-left transition-all duration-300 rounded-xl group',
          isExpanded
            ? 'bg-[var(--color-success)]/10 shadow-[0_0_15px_var(--color-success)/20]'
            : 'hover:bg-[var(--bg-elevated)] hover:shadow-[0_0_10px_var(--color-success)/10]'
        )}
      >
        <span className={cn(
          'transition-transform duration-300',
          isExpanded ? 'text-[var(--color-success)]' : 'text-[var(--text-tertiary)]'
        )}>
          {isExpanded ? (
            <ChevronDown className="w-3.5 h-3.5" />
          ) : (
            <ChevronRight className="w-3.5 h-3.5 group-hover:translate-x-0.5" />
          )}
        </span>

        <div className="w-7 h-7 rounded-lg bg-[var(--color-success)]/10 border border-[var(--color-success)]/20 flex items-center justify-center shrink-0">
          <Database className="w-3.5 h-3.5 text-[var(--color-success)]" />
        </div>

        <span className="text-xs font-medium text-[var(--text-primary)] font-mono tracking-wide">Memory</span>

        <Badge variant={isExpanded ? 'success' : 'default'} size="sm" className="ml-auto" glow={isExpanded}>
          <MessageSquare className="w-3 h-3 mr-1" />
          {events.length}
        </Badge>
      </button>

      {isExpanded && (
        <div className="mt-3 ml-4 space-y-2 animate-fade-in">
          {events.length === 0 ? (
            <div className="text-xs text-[var(--text-tertiary)] text-center py-6 bg-[var(--bg-sunken)] rounded-xl border border-[var(--border-subtle)]">
              No events in memory
            </div>
          ) : (
            events.map((event, index) => (
              <EventCard
                key={event.event_id}
                event={event}
                index={index + 1}
                total={events.length}
              />
            ))
          )}
        </div>
      )}
    </div>
  );
}

// Generic Module Instance component
interface ModuleInstanceItemProps {
  instance: InstanceInfo;
  events?: ChatHistoryEvent[];  // ChatModule has events
  isExpanded: boolean;
  onToggle: () => void;
}

function ModuleInstanceItem({ instance, events = [], isExpanded, onToggle }: ModuleInstanceItemProps) {
  const [memoryExpanded, setMemoryExpanded] = useState(false);
  const config = getModuleConfig(instance.module_class);
  const Icon = config.icon;

  return (
    <div className={cn(
      'ml-4 border-l-2 pl-3 transition-colors duration-300',
      isExpanded ? 'border-[var(--accent-primary)]/50' : 'border-[var(--border-subtle)]'
    )}>
      <button
        onClick={onToggle}
        className={cn(
          'w-full p-2.5 flex items-center gap-2.5 text-left transition-all duration-300 rounded-xl group',
          isExpanded
            ? 'bg-[var(--bg-elevated)] shadow-[0_0_20px_var(--accent-glow)] border border-[var(--accent-primary)]/20'
            : 'hover:bg-[var(--bg-elevated)] hover:shadow-md'
        )}
      >
        <span className={cn(
          'transition-all duration-300',
          isExpanded ? 'text-[var(--accent-primary)]' : 'text-[var(--text-tertiary)]'
        )}>
          {isExpanded ? (
            <ChevronDown className="w-3.5 h-3.5" />
          ) : (
            <ChevronRight className="w-3.5 h-3.5 group-hover:translate-x-0.5 transition-transform" />
          )}
        </span>

        <div className={cn(
          'w-7 h-7 rounded-lg flex items-center justify-center shrink-0 border transition-all duration-300',
          config.bgClass,
          isExpanded ? 'border-current/30 shadow-[0_0_10px_currentColor/20]' : 'border-transparent'
        )}>
          <Icon className={cn('w-3.5 h-3.5', config.colorClass)} />
        </div>

        <div className="flex-1 min-w-0">
          <span className="text-xs font-medium text-[var(--text-primary)] font-mono tracking-wide">
            {config.label}
          </span>
          {instance.description && (
            <span className="text-[10px] text-[var(--text-tertiary)] ml-2 truncate">
              {instance.description}
            </span>
          )}
        </div>

        {/* Status label */}
        <Badge
          variant={instance.status === 'active' ? 'success' : instance.status === 'blocked' ? 'warning' : 'default'}
          size="sm"
          glow={instance.status === 'active'}
        >
          {instance.status}
        </Badge>

        {/* ChatModule event count */}
        {instance.module_class === 'ChatModule' && events.length > 0 && (
          <Badge variant={isExpanded ? 'accent' : 'default'} size="sm">
            {events.length}
          </Badge>
        )}
      </button>

      {isExpanded && (
        <div className="mt-3 ml-4 space-y-3 animate-fade-in">
          {/* Instance details */}
          <div className="text-[10px] text-[var(--text-tertiary)] space-y-1.5 p-3 bg-[var(--bg-sunken)] rounded-xl border border-[var(--border-subtle)] font-mono">
            <div className="flex items-center gap-2">
              <span className="text-[var(--accent-primary)]">ID:</span>
              <span className="text-[var(--text-secondary)]">{instance.instance_id}</span>
            </div>
            {instance.dependencies && instance.dependencies.length > 0 && (
              <div className="flex items-start gap-2">
                <span className="text-[var(--accent-secondary)]">Deps:</span>
                <span className="text-[var(--text-secondary)]">{instance.dependencies.join(', ')}</span>
              </div>
            )}
            {instance.config && Object.keys(instance.config).length > 0 && (
              <div className="flex items-start gap-2">
                <span className="text-[var(--color-warning)]">Config:</span>
                <span className="text-[var(--text-secondary)] break-all">{JSON.stringify(instance.config)}</span>
              </div>
            )}
          </div>

          {/* ChatModule specific: display Memory/Events */}
          {instance.module_class === 'ChatModule' && events.length > 0 && (
            <MemoryItem
              events={events}
              isExpanded={memoryExpanded}
              onToggle={() => setMemoryExpanded(!memoryExpanded)}
            />
          )}

          {/* JobModule specific: display Job details */}
          {instance.module_class === 'JobModule' && instance.config && (
            <div className="text-xs text-[var(--text-secondary)] p-3 bg-[var(--color-warning)]/5 rounded-xl border border-[var(--color-warning)]/20">
              <div className="font-medium text-[var(--color-warning)] mb-2 flex items-center gap-2">
                <Briefcase className="w-3.5 h-3.5" />
                Job Details
              </div>
              <div className="space-y-1 font-mono text-[10px]">
                {instance.config.title ? <div><span className="text-[var(--text-tertiary)]">Title:</span> {String(instance.config.title)}</div> : null}
                {instance.config.job_id ? <div><span className="text-[var(--text-tertiary)]">ID:</span> {String(instance.config.job_id)}</div> : null}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function NarrativeItem({ narrative, eventCount, isExpanded, onToggle }: NarrativeItemProps) {
  const { chatHistoryEvents } = usePreloadStore();
  // Maintain expanded state for each instance
  const [expandedInstances, setExpandedInstances] = useState<Set<string>>(new Set());

  // Get events for this narrative (for ChatModule)
  const narrativeEvents = chatHistoryEvents.filter(
    (e) => e.narrative_id === narrative.narrative_id
  );

  // Toggle instance expanded state
  const toggleInstance = (instanceId: string) => {
    setExpandedInstances(prev => {
      const next = new Set(prev);
      if (next.has(instanceId)) {
        next.delete(instanceId);
      } else {
        next.add(instanceId);
      }
      return next;
    });
  };

  // Get instances (if none, create a default ChatModule instance for backward compatibility)
  // Filter out cancelled and archived Instances (do not display)
  const allInstances = narrative.instances && narrative.instances.length > 0
    ? narrative.instances
    : [{ instance_id: 'default_chat', module_class: 'ChatModule', description: '', status: 'active', dependencies: [], config: {} }];
  const instances = allInstances.filter(inst => inst.status !== 'cancelled' && inst.status !== 'archived');

  return (
    <div
      className={cn(
        'rounded-2xl border overflow-hidden transition-all duration-300',
        isExpanded
          ? 'border-[var(--accent-primary)]/30 shadow-[0_0_30px_var(--accent-glow)] bg-[var(--bg-elevated)]'
          : 'border-[var(--border-default)] hover:border-[var(--accent-primary)]/20 hover:shadow-lg bg-[var(--bg-primary)]'
      )}
    >
      {/* Narrative Header */}
      <button
        onClick={onToggle}
        className={cn(
          'w-full p-4 flex items-start gap-3 text-left transition-all duration-300 group',
          isExpanded
            ? 'bg-gradient-to-r from-[var(--accent-glow)] to-transparent'
            : 'hover:bg-[var(--bg-elevated)]'
        )}
      >
        <span className={cn(
          'mt-1 transition-all duration-300',
          isExpanded ? 'text-[var(--accent-primary)]' : 'text-[var(--text-tertiary)]'
        )}>
          {isExpanded ? (
            <ChevronDown className="w-4 h-4" />
          ) : (
            <ChevronRight className="w-4 h-4 group-hover:translate-x-0.5 transition-transform" />
          )}
        </span>

        <div className={cn(
          'w-10 h-10 rounded-xl flex items-center justify-center shrink-0 transition-all duration-300',
          isExpanded
            ? 'bg-[var(--accent-primary)] shadow-[0_0_20px_var(--accent-primary)]'
            : 'bg-[var(--accent-glow)] group-hover:shadow-[0_0_15px_var(--accent-glow)]'
        )}>
          <BookOpen className={cn(
            'w-5 h-5 transition-colors',
            isExpanded ? 'text-[var(--bg-deep)]' : 'text-[var(--accent-primary)]'
          )} />
        </div>

        <div className="flex-1 min-w-0">
          <div className="text-sm font-semibold text-[var(--text-primary)] truncate group-hover:text-[var(--accent-primary)] transition-colors">
            {narrative.name || 'Untitled Conversation'}
          </div>

          {narrative.description && (
            <div className="text-xs text-[var(--text-secondary)] line-clamp-1 mt-1">
              {narrative.description}
            </div>
          )}

          <div className="flex items-center gap-4 mt-2 text-[10px] text-[var(--text-tertiary)] font-mono">
            <span className="flex items-center gap-1.5">
              <Clock className="w-3 h-3" />
              {formatDate(narrative.created_at)}
            </span>

            {narrative.actors && narrative.actors.length > 0 && (
              <span className="flex items-center gap-1.5">
                <Users className="w-3 h-3" />
                {narrative.actors.length} participant{narrative.actors.length > 1 ? 's' : ''}
              </span>
            )}
          </div>
        </div>

        <Badge variant={isExpanded ? 'accent' : 'default'} size="sm" glow={isExpanded}>
          <MessageSquare className="w-3 h-3 mr-1" />
          {eventCount}
        </Badge>
      </button>

      {/* Narrative Content - new hierarchical structure */}
      {isExpanded && (
        <div className="border-t border-[var(--border-subtle)] bg-[var(--bg-sunken)]/50 animate-fade-in">
          {/* Narrative Summary */}
          {narrative.current_summary && (
            <div className="px-4 py-3 border-b border-[var(--border-subtle)] bg-[var(--bg-elevated)]/50">
              <div className="text-[10px] text-[var(--accent-secondary)] font-medium uppercase tracking-wider mb-2 flex items-center gap-2">
                <Sparkles className="w-3 h-3" />
                Summary
              </div>
              <div className="text-xs text-[var(--text-secondary)] leading-relaxed">
                {narrative.current_summary}
              </div>
            </div>
          )}

          {/* Module Instances layer - dynamic rendering */}
          <div className="p-3 space-y-2">
            {instances.length === 0 ? (
              <div className="text-xs text-[var(--text-tertiary)] text-center py-6 bg-[var(--bg-sunken)] rounded-xl border border-[var(--border-subtle)]">
                No module instances
              </div>
            ) : (
              instances.map((instance) => {
                // Filter events by user_id to ensure each ChatModule only shows its own conversations
                const instanceEvents = instance.module_class === 'ChatModule' && instance.user_id
                  ? narrativeEvents.filter(e => e.user_id === instance.user_id)
                  : instance.module_class === 'ChatModule'
                    ? narrativeEvents  // Backward compatible: if no user_id, show all
                    : [];
                return (
                  <ModuleInstanceItem
                    key={instance.instance_id}
                    instance={instance}
                    events={instanceEvents}
                    isExpanded={expandedInstances.has(instance.instance_id)}
                    onToggle={() => toggleInstance(instance.instance_id)}
                  />
                );
              })
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export function NarrativeList() {
  const { chatHistoryNarratives, chatHistoryEvents, chatHistoryLoading, lastAgentId } = usePreloadStore();
  const [expandedId, setExpandedId] = useState<string | null>(null);

  // Check if data has been initialized (preloadAll has been called at least once)
  const isInitialized = lastAgentId !== null;

  // Count events per narrative (memoized)
  const eventCountMap = useMemo(() => {
    const map = new Map<string, number>();
    chatHistoryEvents.forEach((event) => {
      if (event.narrative_id) {
        map.set(event.narrative_id, (map.get(event.narrative_id) || 0) + 1);
      }
    });
    return map;
  }, [chatHistoryEvents]);

  // Sort narratives by updated_at descending (most recent first) (memoized)
  const sortedNarratives = useMemo(() => {
    return [...chatHistoryNarratives].sort(
      (a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime()
    );
  }, [chatHistoryNarratives]);

  const handleToggle = (narrativeId: string) => {
    setExpandedId(expandedId === narrativeId ? null : narrativeId);
  };

  // Show loading skeleton when loading or not yet initialized
  if (chatHistoryLoading || !isInitialized) {
    return (
      <div className="space-y-3">
        {[1, 2, 3].map((i) => (
          <div key={i} className="animate-pulse rounded-2xl border border-[var(--border-subtle)] bg-[var(--bg-elevated)] p-4">
            <div className="flex items-start gap-3">
              <div className="w-10 h-10 rounded-xl bg-[var(--accent-glow)]" />
              <div className="flex-1 space-y-2">
                <div className="h-4 bg-[var(--bg-tertiary)] rounded-lg w-3/4" />
                <div className="h-3 bg-[var(--bg-tertiary)] rounded-lg w-1/2" />
                <div className="h-3 bg-[var(--bg-tertiary)] rounded-lg w-1/3" />
              </div>
            </div>
          </div>
        ))}
      </div>
    );
  }

  if (sortedNarratives.length === 0) {
    return (
      <div className="h-full flex items-center justify-center">
        <div className="text-center p-8">
          <div className="w-16 h-16 rounded-2xl bg-[var(--accent-glow)] mx-auto mb-4 flex items-center justify-center">
            <BookOpen className="w-8 h-8 text-[var(--accent-primary)]" />
          </div>
          <p className="text-[var(--text-secondary)] text-sm font-medium mb-1">
            No conversation history yet
          </p>
          <p className="text-[var(--text-tertiary)] text-xs">
            Start chatting to create your first narrative
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {sortedNarratives.map((narrative) => (
        <NarrativeItem
          key={narrative.narrative_id}
          narrative={narrative}
          eventCount={eventCountMap.get(narrative.narrative_id) || 0}
          isExpanded={expandedId === narrative.narrative_id}
          onToggle={() => handleToggle(narrative.narrative_id)}
        />
      ))}
    </div>
  );
}
