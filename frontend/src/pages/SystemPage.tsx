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

import { useState, useEffect, useCallback, useRef } from 'react';
import { Play, Square, ServerCrash } from 'lucide-react';
import { Button, ScrollArea } from '@/components/ui';
import { ServiceCard } from '@/components/system/ServiceCard';
import { HealthStatusBar } from '@/components/system/HealthStatusBar';
import { LogViewer } from '@/components/system/LogViewer';
import { platform } from '@/lib/platform';
import type {
  ProcessInfo,
  OverallHealth,
  LogEntry,
  ServiceHealth,
} from '@/types/platform';
import type { ServiceStatus } from '@/components/system/ServiceCard';

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
  const [processes, setProcesses] = useState<ProcessInfo[]>([]);
  const [health, setHealth] = useState<OverallHealth | null>(null);
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [platformError, setPlatformError] = useState<string | null>(null);

  const unsubHealthRef = useRef<(() => void) | null>(null);
  const unsubLogRef = useRef<(() => void) | null>(null);

  // Fetch service status
  const fetchStatus = useCallback(async () => {
    try {
      const procs = await platform.getServiceStatus();
      setProcesses(procs);
      setPlatformError(null);
    } catch (err) {
      setPlatformError(
        err instanceof Error ? err.message : 'Platform not available',
      );
    } finally {
      setIsLoading(false);
    }
  }, []);

  // Fetch initial logs
  const fetchLogs = useCallback(async () => {
    try {
      const entries = await platform.getLogs();
      setLogs(entries);
    } catch {
      // Logs not available — not critical
    }
  }, []);

  // Subscribe to health updates and log stream
  useEffect(() => {
    fetchStatus();
    fetchLogs();

    // Poll every 3 seconds
    const interval = setInterval(fetchStatus, 3000);

    // Subscribe to real-time events
    try {
      unsubHealthRef.current = platform.onHealthUpdate((h) => setHealth(h));
    } catch {
      // Subscription not available
    }

    try {
      unsubLogRef.current = platform.onLog((entry) =>
        setLogs((prev) => [...prev.slice(-499), entry]),
      );
    } catch {
      // Subscription not available
    }

    return () => {
      clearInterval(interval);
      unsubHealthRef.current?.();
      unsubLogRef.current?.();
    };
  }, [fetchStatus, fetchLogs]);

  // Service actions
  const handleStartAll = async () => {
    try {
      await platform.startAllServices();
      await fetchStatus();
    } catch {
      // Platform not available
    }
  };

  const handleStopAll = async () => {
    try {
      await platform.stopAllServices();
      await fetchStatus();
    } catch {
      // Platform not available
    }
  };

  const handleRestart = async (serviceId: string) => {
    try {
      await platform.restartService(serviceId);
      await fetchStatus();
    } catch {
      // Platform not available
    }
  };

  // Platform not available — show placeholder
  if (platformError && processes.length === 0) {
    return (
      <div className="h-full flex flex-col items-center justify-center gap-4 text-center p-8">
        <ServerCrash className="w-12 h-12 text-[var(--text-tertiary)]" />
        <h2 className="text-lg font-semibold text-[var(--text-primary)]">
          Platform Not Available
        </h2>
        <p className="text-sm text-[var(--text-secondary)] max-w-md">
          Service management requires the desktop runtime (Tauri). This feature
          will be available once the desktop app is built.
        </p>
        <p className="text-xs text-[var(--text-tertiary)] font-mono">
          {platformError}
        </p>
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
        <h1 className="text-lg font-semibold text-[var(--text-primary)] flex-1">
          Services
        </h1>
        <Button variant="accent" size="sm" onClick={handleStartAll}>
          <Play className="w-3.5 h-3.5" />
          Start All
        </Button>
        <Button variant="danger" size="sm" onClick={handleStopAll}>
          <Square className="w-3.5 h-3.5" />
          Stop All
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
