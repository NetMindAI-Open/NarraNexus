/**
 * RuntimePanel — execution & narrative, archive style
 *
 * Layout:
 *   ┌ Tabs (underline) ───────── Refresh ┐
 *   │ Execution · Narrative              │
 *   ├────────── StatStrip ───────────────┤
 *   │ progress bar (thin rule-like)      │
 *   │ step list (rule-separated)         │
 */

import { useMemo, useState } from 'react';
import { Play, BookOpen, RefreshCw, Activity, CheckCircle2, Zap, TrendingUp, Layers, Clock, Loader2, ChevronDown, ChevronRight, Terminal } from 'lucide-react';
import { Card, CardContent, Button, StatStrip, ScrollArea } from '@/components/ui';
import { useChatStore, usePreloadStore, useConfigStore } from '@/stores';
import { StepCard } from '@/components/steps/StepCard';
import { NarrativeList } from './NarrativeList';
import { cn } from '@/lib/utils';
import type { AgentToolCall } from '@/types';

type RuntimeTab = 'execution' | 'narrative';

export function RuntimePanel() {
  const [activeTab, setActiveTab] = useState<RuntimeTab>('execution');
  const { currentSteps, currentToolCalls, isStreaming } = useChatStore();
  const { chatHistoryNarratives, chatHistoryEvents, chatHistoryLoading, refreshChatHistory } = usePreloadStore();
  const { agentId, userId } = useConfigStore();

  // Count main steps; use max step ID + 1 as total
  const mainSteps = currentSteps.filter((s) => /^\d+$/.test(s.step));
  const completedCount = mainSteps.filter((s) => s.status === 'completed').length;
  const maxStepId = mainSteps.reduce((max, s) => Math.max(max, parseInt(s.step, 10)), -1);
  const totalCount = maxStepId >= 0 ? maxStepId + 1 : 1;
  const inProgressCount = mainSteps.filter((s) => s.status === 'running').length;
  const progress = isStreaming
    ? Math.min(99, Math.round((completedCount / totalCount) * 100))
    : (completedCount > 0 ? 100 : 0);

  const narrativeMetrics = useMemo(() => {
    const totalEvents = chatHistoryEvents.length;
    const totalActors = new Set(
      chatHistoryNarratives.flatMap((n) => n.actors?.map((a) => a.id) || [])
    ).size;
    return { totalEvents, totalActors };
  }, [chatHistoryNarratives, chatHistoryEvents]);

  const handleRefreshNarrative = async () => {
    if (agentId && userId) {
      await refreshChatHistory(agentId, userId);
    }
  };

  return (
    <Card className="flex flex-col h-full overflow-hidden">
      {/* Underline tab row */}
      <div className="flex items-center justify-between px-5 border-b border-[var(--rule)] min-h-[48px]">
        <div className="flex items-center gap-5">
          <TabBtn
            active={activeTab === 'execution'}
            onClick={() => setActiveTab('execution')}
            icon={<Play className={cn('w-3 h-3', isStreaming && 'animate-pulse')} />}
            label="Execution"
            count={totalCount > 0 ? `${completedCount}/${totalCount}` : undefined}
            isStreaming={isStreaming}
          />
          <TabBtn
            active={activeTab === 'narrative'}
            onClick={() => setActiveTab('narrative')}
            icon={<BookOpen className="w-3 h-3" />}
            label="Narrative"
            count={chatHistoryNarratives.length > 0 ? String(chatHistoryNarratives.length) : undefined}
          />
        </div>
        {activeTab === 'narrative' && (
          <Button
            variant="ghost"
            size="icon"
            onClick={handleRefreshNarrative}
            disabled={chatHistoryLoading}
            title="Refresh Narratives"
          >
            <RefreshCw className={cn('w-4 h-4', chatHistoryLoading && 'animate-spin')} />
          </Button>
        )}
      </div>

      {/* ===== Execution tab ===== */}
      {activeTab === 'execution' ? (
        currentSteps.length === 0 ? (
          <EmptyState
            icon={Activity}
            title="No active execution"
            hint="Execution steps will appear here when the agent processes your request"
          />
        ) : (
          <>
            <StatStrip
              items={[
                { label: 'Completed', value: completedCount, icon: CheckCircle2, tone: 'success', subtext: `of ${totalCount} steps` },
                { label: 'Running', value: inProgressCount, icon: Zap, tone: 'warning', pulse: inProgressCount > 0, subtext: isStreaming ? 'Processing' : 'Idle' },
                { label: 'Total', value: totalCount, icon: Layers, tone: 'secondary', subtext: 'Pipeline' },
              ]}
            />
            {/* Slim progress rule */}
            <div className="relative h-[3px] bg-[var(--bg-secondary)]">
              <div
                className="absolute inset-y-0 left-0 bg-[var(--text-primary)] transition-all duration-500"
                style={{ width: `${progress}%` }}
              />
              {isStreaming && (
                <div className="absolute inset-0 bg-gradient-to-r from-transparent via-white/20 to-transparent animate-shimmer" />
              )}
            </div>
            {/* Step list. The tool-call console is injected into the
                "Execute Agent Loop" step (step "3") as extra expanded
                content, so tool calls live under the step that owns
                them rather than as a free-floating block above. Visual
                language stays distinct from the chat panel's inline
                tool pills — terminal log rows vs. rounded chat pills,
                same data different lens. */}
            <CardContent className="flex-1 overflow-hidden min-h-0 !p-0">
              <ScrollArea className="h-full" viewportClassName="px-5 py-3">
                <div className="space-y-2">
              {mainSteps.map((step, index) => (
                <StepCard
                  key={step.id}
                  step={step}
                  isLast={index === mainSteps.length - 1}
                  extraExpandedContent={
                    step.step === '3' && currentToolCalls.length > 0
                      ? <ToolCallLog toolCalls={currentToolCalls} />
                      : undefined
                  }
                />
              ))}
                </div>
              </ScrollArea>
            </CardContent>
          </>
        )
      ) : (
        // ===== Narrative tab =====
        <>
          {chatHistoryNarratives.length > 0 && (
            <StatStrip
              items={[
                { label: 'Narratives', value: chatHistoryNarratives.length, icon: BookOpen, subtext: 'Story threads' },
                { label: 'Events', value: narrativeMetrics.totalEvents, icon: Clock, tone: 'secondary', subtext: 'Interactions' },
                { label: 'Actors', value: narrativeMetrics.totalActors, icon: TrendingUp, tone: 'success', subtext: 'Participants' },
              ]}
            />
          )}
          <CardContent className="flex-1 overflow-hidden min-h-0 !p-0">
            <ScrollArea className="h-full">
              <NarrativeList />
            </ScrollArea>
          </CardContent>
        </>
      )}
    </Card>
  );
}

/* ─────────────────────────────── helpers ──────────── */

function TabBtn({
  active, onClick, icon, label, count, isStreaming,
}: {
  active: boolean;
  onClick: () => void;
  icon: React.ReactNode;
  label: string;
  count?: string;
  isStreaming?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        'flex items-center gap-1.5 py-3 -mb-px',
        'text-[11px] font-[family-name:var(--font-mono)] uppercase tracking-[0.16em]',
        'border-b-2 transition-colors duration-150',
        active
          ? 'border-[var(--text-primary)] text-[var(--text-primary)]'
          : 'border-transparent text-[var(--text-tertiary)] hover:text-[var(--text-primary)]'
      )}
    >
      {icon}
      {label}
      {count && (
        <span className={cn(
          'tabular-nums text-[10px] normal-case tracking-normal',
          isStreaming ? 'text-[var(--color-yellow-500)]' : 'text-[var(--text-tertiary)]'
        )}>
          · {count}
        </span>
      )}
    </button>
  );
}

function EmptyState({ icon: Icon, title, hint }: { icon: React.ElementType; title: string; hint: string }) {
  return (
    <div className="flex-1 flex flex-col items-center justify-center text-center px-6 py-12">
      <Icon className="w-8 h-8 text-[var(--text-tertiary)] opacity-40 mb-4" />
      <p className="text-sm text-[var(--text-primary)] mb-1.5">{title}</p>
      <p className="text-xs text-[var(--text-tertiary)] max-w-[240px] leading-relaxed">{hint}</p>
    </div>
  );
}

/* ── Tool call console ─────────────────────────────────────────────
 * Differentiation from the chat panel's tool pills (see
 * TurnTimeline.ToolCallBlock): the chat version is rounded, accent-
 * filled, with an inline one-line arg preview — optimised for skimming
 * a single turn alongside the agent's reply. Runtime panel rows are
 * deliberately log/terminal flavoured: monospace, no rounded fill,
 * left-rule indent, status icon up front, full input + output on
 * expand. Same data, different audience: chat readers want narrative,
 * runtime watchers want a process log.
 *
 * No sequence numbers (per Bin 2026-05-12 review) — the order on screen
 * IS the order of execution, that's all the rank info that matters.
 */
function ToolCallLog({ toolCalls }: { toolCalls: AgentToolCall[] }) {
  // Rendered inside a StepCard's expanded section (the "Execute Agent
  // Loop" step). Parent provides spacing; we only own the inner box.
  return (
    <div className="border border-[var(--border-subtle)] bg-[var(--bg-sunken)]/40">
      <div className="flex items-center gap-1.5 px-3 py-1.5 border-b border-[var(--border-subtle)] text-[10px] uppercase tracking-[0.16em] font-mono text-[var(--text-tertiary)]">
        <Terminal className="w-3 h-3 text-[var(--accent-primary)]" />
        <span>Tool calls</span>
        <span className="ml-auto tabular-nums">{toolCalls.length}</span>
      </div>
      <div>
        {toolCalls.map((tc, idx) => (
          <ToolCallRow key={`${tc.tool_name}-${tc.timestamp}-${idx}`} tc={tc} />
        ))}
      </div>
    </div>
  );
}

function ToolCallRow({ tc }: { tc: AgentToolCall }) {
  const [expanded, setExpanded] = useState(false);
  const friendlyName = tc.tool_name.split('__').pop() || tc.tool_name;
  const done = !!tc.tool_output;
  return (
    <div className="border-l-2 border-[var(--accent-primary)]">
      <button
        type="button"
        onClick={() => setExpanded((p) => !p)}
        className={cn(
          'flex items-center gap-2 w-full px-2.5 py-1 text-left',
          'text-xs font-mono text-[var(--text-secondary)]',
          'hover:bg-[var(--bg-tertiary)]/40 transition-colors',
        )}
      >
        {expanded ? (
          <ChevronDown className="w-3 h-3 shrink-0 text-[var(--text-tertiary)]" />
        ) : (
          <ChevronRight className="w-3 h-3 shrink-0 text-[var(--text-tertiary)]" />
        )}
        {done ? (
          <CheckCircle2 className="w-3 h-3 shrink-0 text-[var(--color-success)]" />
        ) : (
          <Loader2 className="w-3 h-3 shrink-0 animate-spin text-[var(--accent-primary)]" />
        )}
        <span className="truncate text-[var(--text-primary)]">{friendlyName}</span>
      </button>
      {expanded && (
        <div className="px-2.5 pb-2 pl-8 space-y-1.5 text-[10px] font-mono text-[var(--text-tertiary)]">
          <div>
            <div className="uppercase tracking-[0.16em] mb-1 text-[var(--text-tertiary)]">Input</div>
            <pre className="whitespace-pre-wrap break-all bg-[var(--bg-sunken)] px-2 py-1.5 border border-[var(--border-subtle)]">
              {safeJsonStringify(tc.tool_input)}
            </pre>
          </div>
          {tc.tool_output ? (
            <div>
              <div className="uppercase tracking-[0.16em] mb-1 text-[var(--text-tertiary)]">Output</div>
              <pre className="whitespace-pre-wrap break-all bg-[var(--bg-sunken)] px-2 py-1.5 border border-[var(--border-subtle)] max-h-48 overflow-auto">
                {tc.tool_output}
              </pre>
            </div>
          ) : (
            <div className="italic text-[var(--text-tertiary)]">awaiting output…</div>
          )}
        </div>
      )}
    </div>
  );
}

function safeJsonStringify(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

export default RuntimePanel;
