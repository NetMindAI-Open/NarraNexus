/**
 * Runtime Panel - Bioluminescent Terminal style
 * Combined Execution and History with tabs
 * Enhanced with Control Center Dashboard design
 */

import { useState, useMemo } from 'react';
import { Play, BookOpen, RefreshCw, Activity, CheckCircle2, Clock, Zap, TrendingUp, Layers } from 'lucide-react';
import { Card, CardContent, Badge, Button } from '@/components/ui';
import { useChatStore, usePreloadStore, useConfigStore } from '@/stores';
import { StepCard } from '@/components/steps/StepCard';
import { NarrativeList } from './NarrativeList';
import { cn } from '@/lib/utils';

type RuntimeTab = 'execution' | 'narrative';

// KPI Card Component
function KPICard({
  label,
  value,
  icon: Icon,
  color = 'accent',
  subtext,
  pulse,
}: {
  label: string;
  value: string | number;
  icon: React.ElementType;
  color?: 'accent' | 'success' | 'warning' | 'secondary';
  subtext?: string;
  pulse?: boolean;
}) {
  const colorMap = {
    accent: {
      bg: 'bg-[var(--accent-glow)]',
      icon: 'text-[var(--accent-primary)]',
      value: 'text-[var(--accent-primary)]',
      glow: 'shadow-[0_0_20px_var(--accent-glow)]',
    },
    success: {
      bg: 'bg-[var(--color-success)]/10',
      icon: 'text-[var(--color-success)]',
      value: 'text-[var(--color-success)]',
      glow: 'shadow-[0_0_20px_rgba(34,197,94,0.2)]',
    },
    warning: {
      bg: 'bg-[var(--color-warning)]/10',
      icon: 'text-[var(--color-warning)]',
      value: 'text-[var(--color-warning)]',
      glow: 'shadow-[0_0_20px_rgba(234,179,8,0.2)]',
    },
    secondary: {
      bg: 'bg-[var(--accent-secondary)]/10',
      icon: 'text-[var(--accent-secondary)]',
      value: 'text-[var(--accent-secondary)]',
      glow: 'shadow-[0_0_20px_rgba(192,132,252,0.2)]',
    },
  };

  const colors = colorMap[color];

  return (
    <div
      className={cn(
        'p-3 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elevated)]',
        'transition-all duration-300 hover:border-[var(--accent-primary)]/30',
        pulse && colors.glow
      )}
    >
      <div className="flex items-center gap-2 mb-2">
        <div className={cn('w-7 h-7 rounded-lg flex items-center justify-center', colors.bg)}>
          <Icon className={cn('w-3.5 h-3.5', colors.icon, pulse && 'animate-pulse')} />
        </div>
        <span className="text-[10px] text-[var(--text-tertiary)] uppercase tracking-wider font-medium">{label}</span>
      </div>
      <div className={cn('text-xl font-bold font-mono', colors.value)}>{value}</div>
      {subtext && <div className="text-[9px] text-[var(--text-tertiary)] mt-1 font-mono">{subtext}</div>}
    </div>
  );
}

// Mini Progress Ring
function ProgressRing({ progress, size = 48 }: { progress: number; size?: number }) {
  const radius = (size - 8) / 2;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference - (progress / 100) * circumference;

  return (
    <div className="relative" style={{ width: size, height: size }}>
      <svg className="transform -rotate-90" width={size} height={size}>
        {/* Background circle */}
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke="var(--bg-tertiary)"
          strokeWidth="4"
        />
        {/* Progress circle */}
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke="var(--accent-primary)"
          strokeWidth="4"
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={offset}
          className="transition-all duration-500 ease-out"
          style={{
            filter: 'drop-shadow(0 0 6px var(--accent-glow))',
          }}
        />
      </svg>
      <div className="absolute inset-0 flex items-center justify-center">
        <span className="text-xs font-bold font-mono text-[var(--accent-primary)]">{progress}%</span>
      </div>
    </div>
  );
}

export function RuntimePanel() {
  const [activeTab, setActiveTab] = useState<RuntimeTab>('execution');
  const { currentSteps, isStreaming } = useChatStore();
  const { chatHistoryNarratives, chatHistoryEvents, chatHistoryLoading, refreshChatHistory } = usePreloadStore();
  const { agentId, userId } = useConfigStore();

  // Count main steps (integer IDs: '0','1','2',...), use max step ID + 1 as total
  const mainSteps = currentSteps.filter((s) => /^\d+$/.test(s.step));
  const completedCount = mainSteps.filter((s) => s.status === 'completed').length;
  const maxStepId = mainSteps.reduce((max, s) => Math.max(max, parseInt(s.step, 10)), -1);
  const totalCount = maxStepId >= 0 ? maxStepId + 1 : 1;
  const inProgressCount = mainSteps.filter((s) => s.status === 'running').length;
  const progress = isStreaming
    ? Math.min(99, Math.round((completedCount / totalCount) * 100))
    : (completedCount > 0 ? 100 : 0);

  // Calculate narrative metrics
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
      {/* Tab Header */}
      <div className="flex items-center gap-2 p-3 border-b border-[var(--border-subtle)] bg-[var(--bg-secondary)]/30">
        <button
          onClick={() => setActiveTab('execution')}
          className={cn(
            'flex-1 flex items-center justify-center gap-2 py-2.5 px-4 rounded-xl',
            'text-sm font-medium transition-all duration-300',
            'border',
            activeTab === 'execution'
              ? [
                  'bg-[var(--bg-elevated)]',
                  'text-[var(--accent-primary)]',
                  'border-[var(--accent-primary)]/30',
                  'shadow-[0_0_20px_var(--accent-glow)]',
                ]
              : [
                  'bg-transparent',
                  'text-[var(--text-secondary)]',
                  'border-transparent',
                  'hover:bg-[var(--bg-tertiary)]',
                  'hover:text-[var(--text-primary)]',
                ]
          )}
        >
          <Play className={cn('w-4 h-4', isStreaming && 'animate-pulse')} />
          <span>Execution</span>
          {totalCount > 0 && (
            <Badge
              variant={isStreaming ? 'accent' : 'success'}
              pulse={isStreaming}
              size="sm"
              glow={isStreaming}
            >
              {completedCount}/{totalCount}
            </Badge>
          )}
        </button>

        <button
          onClick={() => setActiveTab('narrative')}
          className={cn(
            'flex-1 flex items-center justify-center gap-2 py-2.5 px-4 rounded-xl',
            'text-sm font-medium transition-all duration-300',
            'border',
            activeTab === 'narrative'
              ? [
                  'bg-[var(--bg-elevated)]',
                  'text-[var(--accent-primary)]',
                  'border-[var(--accent-primary)]/30',
                  'shadow-[0_0_20px_var(--accent-glow)]',
                ]
              : [
                  'bg-transparent',
                  'text-[var(--text-secondary)]',
                  'border-transparent',
                  'hover:bg-[var(--bg-tertiary)]',
                  'hover:text-[var(--text-primary)]',
                ]
          )}
        >
          <BookOpen className="w-4 h-4" />
          <span>Narrative</span>
          <Badge variant="default" size="sm">
            {chatHistoryNarratives.length}
          </Badge>
        </button>

        {/* Refresh button for Narrative tab */}
        {activeTab === 'narrative' && (
          <Button
            variant="ghost"
            size="icon"
            onClick={handleRefreshNarrative}
            disabled={chatHistoryLoading}
            title="Refresh Narratives"
            className="shrink-0 w-9 h-9"
          >
            <RefreshCw className={cn('w-4 h-4', chatHistoryLoading && 'animate-spin')} />
          </Button>
        )}
      </div>

      {/* Tab Content */}
      <CardContent className="flex-1 overflow-y-auto space-y-3 min-h-0 pt-4">
        {activeTab === 'execution' ? (
          // Execution Content
          currentSteps.length === 0 ? (
            <div className="h-full flex flex-col items-center justify-center text-center px-6">
              <div className="relative mb-5">
                <div className="w-16 h-16 rounded-2xl bg-[var(--bg-tertiary)] flex items-center justify-center border border-[var(--border-default)]">
                  <Activity className="w-8 h-8 text-[var(--text-tertiary)]" />
                </div>
                <div className="absolute -inset-2 rounded-3xl bg-[var(--accent-primary)] opacity-5 blur-xl" />
              </div>
              <p className="text-sm text-[var(--text-secondary)] font-medium mb-1">No active execution</p>
              <p className="text-xs text-[var(--text-tertiary)] max-w-[200px]">
                Execution steps will appear here when the agent processes your request
              </p>
            </div>
          ) : (
            <div className="space-y-4">
              {/* Dashboard KPI Section */}
              <div className="flex items-center gap-3 mb-4">
                {/* Progress Ring */}
                <div className="shrink-0">
                  <ProgressRing progress={progress} />
                </div>

                {/* KPI Cards */}
                <div className="flex-1 grid grid-cols-3 gap-2">
                  <KPICard
                    label="Completed"
                    value={completedCount}
                    icon={CheckCircle2}
                    color="success"
                    subtext={`of ${totalCount} steps`}
                  />
                  <KPICard
                    label="In Progress"
                    value={inProgressCount}
                    icon={Zap}
                    color="warning"
                    pulse={inProgressCount > 0}
                    subtext={isStreaming ? 'Processing...' : 'Idle'}
                  />
                  <KPICard
                    label="Total Steps"
                    value={totalCount}
                    icon={Layers}
                    color="secondary"
                    subtext="Pipeline"
                  />
                </div>
              </div>

              {/* Visual Progress Bar */}
              <div className="relative h-2 bg-[var(--bg-tertiary)] rounded-full overflow-hidden mb-4">
                <div
                  className="absolute inset-y-0 left-0 bg-gradient-to-r from-[var(--accent-primary)] to-[var(--accent-secondary)] rounded-full transition-all duration-500"
                  style={{ width: `${progress}%` }}
                />
                {isStreaming && (
                  <div className="absolute inset-0 bg-gradient-to-r from-transparent via-white/20 to-transparent animate-shimmer" />
                )}
              </div>

              {/* Step List */}
              <div className="space-y-3">
                {currentSteps.map((step, index) => (
                  <div
                    key={step.id}
                    className="animate-slide-up"
                    style={{ animationDelay: `${index * 50}ms` }}
                  >
                    <StepCard
                      step={step}
                      isLast={index === currentSteps.length - 1}
                    />
                  </div>
                ))}
              </div>
            </div>
          )
        ) : (
          // Narrative Content with Dashboard
          <div className="space-y-4">
            {/* Narrative Dashboard */}
            {chatHistoryNarratives.length > 0 && (
              <div className="grid grid-cols-3 gap-2 mb-4">
                <KPICard
                  label="Narratives"
                  value={chatHistoryNarratives.length}
                  icon={BookOpen}
                  color="accent"
                  subtext="Story threads"
                />
                <KPICard
                  label="Events"
                  value={narrativeMetrics.totalEvents}
                  icon={Clock}
                  color="secondary"
                  subtext="Interactions"
                />
                <KPICard
                  label="Actors"
                  value={narrativeMetrics.totalActors}
                  icon={TrendingUp}
                  color="success"
                  subtext="Participants"
                />
              </div>
            )}
            <NarrativeList />
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export default RuntimePanel;
