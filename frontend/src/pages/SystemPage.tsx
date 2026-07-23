/**
 * @file_name: SystemPage.tsx
 * @author: NexusAgent
 * @date: 2026-04-02
 * @description: System management page (Desktop Dashboard migrated to main frontend)
 *
 * Displays service health status, service cards with controls, and a
 * real-time log viewer. Gracefully handles the case where the platform
 * bridge is not yet available (Tauri not built).
 */

import { useState, useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { Play, Square } from 'lucide-react';
import { Button, ScrollArea } from '@/components/ui';
import { BracketEmptyState, BracketSectionLabel } from '@/components/nm';
import { ServiceCard } from '@/components/system/ServiceCard';
import { HealthStatusBar } from '@/components/system/HealthStatusBar';
import { LogViewer } from '@/components/system/LogViewer';
import { platform } from '@/lib/platform';
import { api } from '@/lib/api';
import type {
  ProcessInfo,
  OverallHealth,
  LogEntry,
  ServiceHealth,
  WorkerStatus,
} from '@/types/platform';
import type { ServiceStatus } from '@/components/system/ServiceCard';

/** Stable key for de-duping log entries across polls. */
function logKey(e: LogEntry): string {
  return `${e.serviceId}|${e.timestamp}|${e.stream}|${e.message}`;
}

/** Map ProcessInfo + ServiceHealth into the unified status the card expects */
function resolveStatus(
  proc: ProcessInfo | undefined,
  health: ServiceHealth | undefined,
): ServiceStatus {
  if (health) {
    if (health.state === 'healthy') return 'healthy';
    if (health.state === 'unhealthy') return 'unhealthy';
  }
  if (proc) {
    return proc.status as ServiceStatus;
  }
  return 'unknown';
}

export function SystemPage() {
  const { t } = useTranslation();
  const [processes, setProcesses] = useState<ProcessInfo[]>([]);
  const [health, setHealth] = useState<OverallHealth | null>(null);
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [workerStatus, setWorkerStatus] = useState<WorkerStatus | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [platformError, setPlatformError] = useState<string | null>(null);

  // Single poll tick that fetches services + health + logs in parallel.
  // 2026-05-28: replaced the prior `onHealthUpdate` / `onLog` event
  // subscriptions (which threw because Rust never emitted those events).
  // 3 s tick matches the previous service-status poll cadence — slow
  // enough not to spam the IPC channel, fast enough that a manual
  // start/stop is visible within ~one tick.
  const poll = useCallback(async () => {
    try {
      const procs = await platform.getServiceStatus();
      setProcesses(procs);
      setPlatformError(null);
    } catch (err) {
      setPlatformError(
        err instanceof Error ? err.message : t('pages.system.platformNotAvailable'),
      );
      // Don't try the other endpoints if the first fails — the platform
      // bridge is unavailable as a whole.
      setIsLoading(false);
      return;
    } finally {
      setIsLoading(false);
    }
    try {
      const h = await platform.getHealthStatus();
      setHealth(h);
    } catch {
      // Health endpoint failure is non-fatal; cards fall back to the
      // process-level status from getServiceStatus above.
    }
    try {
      // Per-worker liveness for the consolidated `workers` card. Backed by the
      // backend :8000 (not the Tauri bridge), so it works whenever the backend
      // is reachable; a failure is non-fatal (the card just omits the detail).
      const ws = await api.getWorkerStatus();
      setWorkerStatus(ws.available ? ws : null);
    } catch {
      setWorkerStatus(null);
    }
    try {
      const entries = await platform.getLogs();
      // Dedup-merge: keep at most last 500. New entries are appended
      // ordered by their natural log timestamps (ProcessManager already
      // emits them chronologically).
      setLogs((prev) => {
        const seen = new Set(prev.map(logKey));
        const merged = [...prev];
        for (const e of entries) {
          const k = logKey(e);
          if (!seen.has(k)) {
            merged.push(e);
            seen.add(k);
          }
        }
        return merged.length > 500 ? merged.slice(-500) : merged;
      });
    } catch {
      // Logs not available — non-critical
    }
  }, [t]);

  useEffect(() => {
    poll();
    const interval = setInterval(poll, 3000);
    return () => clearInterval(interval);
  }, [poll]);

  // Service actions — after a mutation, kick a poll immediately so the
  // user sees the result without waiting for the next 3 s tick.
  const handleStartAll = async () => {
    try {
      await platform.startAllServices();
      await poll();
    } catch {
      // Platform not available
    }
  };

  const handleStopAll = async () => {
    try {
      await platform.stopAllServices();
      await poll();
    } catch {
      // Platform not available
    }
  };

  const handleRestart = async (serviceId: string) => {
    try {
      await platform.restartService(serviceId);
      await poll();
    } catch {
      // Platform not available
    }
  };

  // Platform not available — show placeholder
  if (platformError && processes.length === 0) {
    return (
      <div className="h-full flex items-center justify-center p-8">
        <BracketEmptyState
          label={t('pages.system.platformNotAvailable')}
          hint={
            <>
              {t('pages.system.platformNotAvailableHint')}
              <br />
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--nm-ink50)' }}>
                {platformError}
              </span>
            </>
          }
        />
      </div>
    );
  }

  // Build the service card data by merging processes and health
  const healthMap = new Map(
    health?.services.map((s) => [s.serviceId, s]) ?? [],
  );

  return (
    <ScrollArea className="h-full" viewportClassName="p-6">
      <div className="flex flex-col gap-4">
      {/* Health status bar */}
      <HealthStatusBar health={health} isLoading={isLoading} />

      {/* Controls */}
      <div className="flex items-center gap-3">
        <div className="flex-1">
          <h1
            className="text-2xl font-bold tracking-tight"
            style={{ color: 'var(--nm-ink)', fontFamily: 'var(--font-display)' }}
          >
            {t('pages.system.title')}
          </h1>
          <div className="mt-1">
            <BracketSectionLabel>
              {t('pages.system.processesCount', { count: processes.length })}
            </BracketSectionLabel>
          </div>
        </div>
        <Button variant="accent" size="sm" onClick={handleStartAll}>
          <Play className="w-3.5 h-3.5" />
          {t('pages.system.startAll')}
        </Button>
        <Button variant="danger" size="sm" onClick={handleStopAll}>
          <Square className="w-3.5 h-3.5" />
          {t('pages.system.stopAll')}
        </Button>
      </div>

      {/* Service cards grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3">
        {processes.map((proc) => {
          const sh = healthMap.get(proc.serviceId);
          return (
            <ServiceCard
              key={proc.serviceId}
              label={proc.label}
              status={resolveStatus(proc, sh)}
              port={sh?.port ?? null}
              lastError={proc.lastError}
              onRestart={() => handleRestart(proc.serviceId)}
              workers={
                proc.serviceId === 'workers'
                  ? workerStatus?.workers
                  : undefined
              }
              workerHeartbeatAgeSeconds={
                proc.serviceId === 'workers'
                  ? workerStatus?.heartbeatAgeSeconds ?? null
                  : null
              }
            />
          );
        })}
      </div>

      {/* Log viewer */}
      <LogViewer logs={logs} />
      </div>
    </ScrollArea>
  );
}

export default SystemPage;
