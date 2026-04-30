/**
 * Steps Panel - Real-time execution progress
 */

import { Card, CardHeader, CardTitle, CardContent, Badge, ScrollArea } from '@/components/ui';
import { useChatStore } from '@/stores';
import { StepCard } from './StepCard';

// Main step IDs (only count 6 major steps, excluding substeps)
const MAIN_STEP_IDS = new Set(['0', '1', '2', '3', '4', '5']);

export function StepsPanel() {
  const { currentSteps, isStreaming } = useChatStore();

  // Only count progress of main steps, excluding substeps in agent loop
  const mainSteps = currentSteps.filter((s) => MAIN_STEP_IDS.has(s.step));
  const completedCount = mainSteps.filter((s) => s.status === 'completed').length;
  const totalCount = mainSteps.length;

  return (
    <Card className="flex flex-col h-full">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <span className="font-mono text-[var(--accent-primary)]">//</span>
          Execution
        </CardTitle>
        {totalCount > 0 && (
          <Badge variant={isStreaming ? 'accent' : 'success'} pulse={isStreaming}>
            {completedCount}/{totalCount}
          </Badge>
        )}
      </CardHeader>

      <CardContent className="flex-1 overflow-hidden min-h-0 !p-0">
        <ScrollArea className="h-full" viewportClassName="p-5">
          <div className="space-y-3">
        {currentSteps.length === 0 ? (
          <div className="h-full flex items-center justify-center">
            <p className="text-[var(--text-tertiary)] text-sm text-center">
              Execution steps will<br />appear here
            </p>
          </div>
        ) : (
          currentSteps.map((step, index) => (
            <StepCard
              key={step.id}
              step={step}
              isLast={index === currentSteps.length - 1}
            />
          ))
        )}
          </div>
        </ScrollArea>
      </CardContent>
    </Card>
  );
}
