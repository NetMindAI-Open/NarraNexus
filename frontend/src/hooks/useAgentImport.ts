/**
 * @file_name: useAgentImport.ts
 * @author: NetMind.AI
 * @date: 2026-08-27
 * @description: All the state behind "import agents from other tools" — detect,
 * selection, lazy per-row scan, the manual-folder row, and the sequential
 * scan→apply queue with its per-row progress.
 *
 * Extracted from ImportAgentModal (2026-08-27) because the same picker now has
 * two homes: the sidebar's modal and step 2 of the first-run welcome flow
 * ([[WelcomePage]]). Only the CHROME differs (dialog footer vs full-width flow
 * CTA), so the chrome is what stays in the components — everything else lives
 * here, once. Two copies of this logic is exactly how the old welcome dialog
 * and the import modal drifted apart in the first place.
 *
 * The hook deliberately does NOT own `onApplied` / `onClose`: what happens when
 * a batch finishes is the caller's business (the modal closes itself, the flow
 * advances a step).
 *
 * LOCAL ONLY: detect/scan read the user's filesystem and 503 on cloud, so every
 * caller mounts this in local mode only.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api } from '@/lib/api';
import {
  detectionKey,
  detectionTitle,
  defaultSelection,
  flattenGroups,
  groupDetections,
  sessionCount,
} from '@/lib/migrationDetections';
import {
  applyImportEdits,
  runImportQueue,
  summarizeBatch,
  type ImportQueueItem,
  type ImportQueueProgress,
} from '@/lib/migrationImportQueue';
import type { FrameworkDetection, StandardizedAgentImport } from '@/types';

/** list → the picker; running/done → the batch report (same list, new skin). */
export type ImportPhase = 'list' | 'running' | 'done';

/** Per-row lazy scan state (only rows the user expands, or imports, get one). */
export type ScanState =
  | { status: 'loading' }
  | { status: 'ready'; data: StandardizedAgentImport }
  | { status: 'error'; error: string };

export interface UseAgentImportOptions {
  /** Detections the caller already fetched — skips this hook's own detect so
   *  the welcome flow doesn't scan the filesystem twice. */
  initialDetections?: FrameworkDetection[];
}

const errorMessage = (e: unknown): string => (e instanceof Error ? e.message : String(e));

export function useAgentImport({ initialDetections }: UseAgentImportOptions = {}) {
  const [detections, setDetections] = useState<FrameworkDetection[]>(initialDetections ?? []);
  const [detecting, setDetecting] = useState(!initialDetections);
  const [error, setError] = useState<string | null>(null);

  const [selected, setSelected] = useState<Set<string>>(() =>
    initialDetections ? defaultSelection(initialDetections) : new Set(),
  );
  const [expanded, setExpanded] = useState<string | null>(null);
  /** Which framework groups are OPEN. Empty by default on purpose: a machine
   *  with 27 Claude Code projects used to dump 27 rows into the page, which read
   *  as a wall rather than a choice (Owner 2026-08-27). Tracking the open set
   *  rather than the closed one means no effect has to seed it when detections
   *  arrive or change. */
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set());
  const [scans, setScans] = useState<Record<string, ScanState>>({});
  /** Row edits, keyed by row. Absent = untouched = import as scanned. */
  const [names, setNames] = useState<Record<string, string>>({});
  const [sessions, setSessions] = useState<Record<string, Set<string>>>({});

  const [manualPath, setManualPath] = useState('');
  const [manualScanning, setManualScanning] = useState(false);

  const [phase, setPhase] = useState<ImportPhase>('list');
  const [progress, setProgress] = useState<Record<string, ImportQueueProgress>>({});
  const [stopping, setStopping] = useState(false);
  const stopRef = useRef(false);
  /** Stable per-row apply handle, so "stop" can tell the server to hurry the
   *  row that is already mid-write instead of making the user wait it out. */
  const importIds = useRef<Record<string, string>>({});
  /** The row currently being applied — the only one a hurry can help. */
  const runningKey = useRef<string | null>(null);

  // ── detect ───────────────────────────────────────────────────────────────
  const runDetect = useCallback(async () => {
    setDetecting(true);
    setError(null);
    try {
      const res = await api.migrateDetect();
      setDetections(res.detections);
      setSelected(defaultSelection(res.detections));
      setScans({});
      setNames({});
      setSessions({});
      setExpanded(null);
      setExpandedGroups(new Set());
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setDetecting(false);
    }
  }, []);

  useEffect(() => {
    if (initialDetections) return;
    void runDetect();
  }, [initialDetections, runDetect]);

  // ── derived ──────────────────────────────────────────────────────────────
  const groups = useMemo(() => groupDetections(detections), [detections]);
  const ordered = useMemo(() => flattenGroups(groups), [groups]);
  const selectedRows = useMemo(
    () => ordered.filter((d) => selected.has(detectionKey(d))),
    [ordered, selected],
  );
  /** Session total across checked rows: the scan knows the real number, the
   *  detector's `sessions:N` signal is the estimate until then. */
  const selectedSessionTotal = useMemo(
    () =>
      selectedRows.reduce((n, d) => {
        const key = detectionKey(d);
        const scan = scans[key];
        if (scan?.status === 'ready') return n + (sessions[key]?.size ?? scan.data.sessions.length);
        return n + sessionCount(d);
      }, 0),
    [selectedRows, scans, sessions],
  );

  // ── selection ────────────────────────────────────────────────────────────
  const toggleRow = (key: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  const setMany = (keys: string[], on: boolean) =>
    setSelected((prev) => {
      const next = new Set(prev);
      for (const k of keys) {
        if (on) next.add(k);
        else next.delete(k);
      }
      return next;
    });

  // ── lazy scan (row expanded, or needed by the queue) ─────────────────────
  const ensureScan = useCallback(
    async (d: FrameworkDetection) => {
      const key = detectionKey(d);
      setScans((prev) =>
        prev[key]?.status === 'ready' || prev[key]?.status === 'loading'
          ? prev
          : { ...prev, [key]: { status: 'loading' } },
      );
      try {
        const data = await api.migrateScan(d.path, d.framework);
        setScans((prev) => ({ ...prev, [key]: { status: 'ready', data } }));
        setNames((prev) => (key in prev ? prev : { ...prev, [key]: data.agent.name }));
        setSessions((prev) =>
          key in prev
            ? prev
            : { ...prev, [key]: new Set(data.sessions.map((s) => s.session_id)) },
        );
      } catch (e) {
        setScans((prev) => ({ ...prev, [key]: { status: 'error', error: errorMessage(e) } }));
      }
    },
    [],
  );

  const toggleGroupExpanded = (framework: string) =>
    setExpandedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(framework)) next.delete(framework);
      else next.add(framework);
      return next;
    });

  const toggleExpand = (d: FrameworkDetection) => {
    const key = detectionKey(d);
    if (expanded === key) {
      setExpanded(null);
      return;
    }
    setExpanded(key);
    const state = scans[key];
    if (!state || state.status === 'error') void ensureScan(d);
  };

  // ── manual folder scan → a row of its own ────────────────────────────────
  const scanManualPath = async () => {
    const path = manualPath.trim();
    if (!path) return;
    setManualScanning(true);
    setError(null);
    try {
      const data = await api.migrateScan(path);
      const detection: FrameworkDetection = {
        framework: data.source.framework,
        path: data.source.detected_path,
        confidence: data.source.detection_confidence,
        signals: [`sessions:${data.sessions.length}`, 'manual'],
      };
      const key = detectionKey(detection);
      setDetections((prev) =>
        prev.some((d) => detectionKey(d) === key) ? prev : [...prev, detection],
      );
      setScans((prev) => ({ ...prev, [key]: { status: 'ready', data } }));
      setNames((prev) => ({ ...prev, [key]: data.agent.name }));
      setSessions((prev) => ({
        ...prev,
        [key]: new Set(data.sessions.map((s) => s.session_id)),
      }));
      setSelected((prev) => new Set(prev).add(key));
      // Reveal what the user just scanned — a row added into a collapsed group
      // would look like nothing happened.
      setExpandedGroups((prev) => new Set(prev).add(detection.framework));
      setExpanded(key);
      setManualPath('');
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setManualScanning(false);
    }
  };

  // ── import ───────────────────────────────────────────────────────────────
  const buildItem = useCallback(
    (d: FrameworkDetection): ImportQueueItem => {
      const key = detectionKey(d);
      const scan = scans[key];
      // A NEW id per attempt, never reused across a retry. The id is the
      // handle "stop" marks server-side; if a previous attempt of this row
      // was stopped (or died and leaked its mark), a retry under the same id
      // would run hurried — degraded summaries although the user is now
      // willing to wait. Assigned here, before the row enters `importing`,
      // so `requestStop` always finds the id of the attempt actually running.
      importIds.current[key] = `imp_${key.replace(/[^a-zA-Z0-9]/g, '').slice(-16)}_${
        performance.now().toString(36).replace('.', '')
      }`;
      return {
        key,
        importId: importIds.current[key],
        path: d.path,
        framework: d.framework,
        label: detectionTitle(d),
        scanned: scan?.status === 'ready' ? scan.data : null,
        transform: (s) => applyImportEdits(s, names[key], sessions[key]),
      };
    },
    [scans, names, sessions],
  );

  const runQueue = useCallback(
    async (rows: FrameworkDetection[], reset: boolean) => {
      if (rows.length === 0) return;
      stopRef.current = false;
      setStopping(false);
      setPhase('running');
      const items = rows.map(buildItem);
      if (reset) {
        setProgress(
          Object.fromEntries(
            items.map((i) => [i.key, { key: i.key, label: i.label, status: 'queued' as const }]),
          ),
        );
      } else {
        setProgress((prev) => {
          const next = { ...prev };
          for (const i of items) next[i.key] = { key: i.key, label: i.label, status: 'queued' };
          return next;
        });
      }
      await runImportQueue(items, {
        scan: (path, framework) => api.migrateScan(path, framework),
        apply: (data, importId) => api.migrateApply(data, undefined, importId),
        onProgress: (p) => {
          runningKey.current = p.status === 'importing' ? p.key : runningKey.current;
          if (p.status === 'done' || p.status === 'failed') runningKey.current = null;
          setProgress((prev) => ({ ...prev, [p.key]: p }));
        },
        shouldStop: () => stopRef.current,
      });
      runningKey.current = null;
      setPhase('done');
    },
    [buildItem],
  );

  // Progress rendered in the order the user saw the rows, and the batch summary
  // derived from that same order — so "Open …" names the agent whose row is
  // first, which is also `batch.results[0]` that useAgentImported selects.
  const progressRows = useMemo(
    () => ordered.map(detectionKey).filter((k) => k in progress).map((k) => progress[k]),
    [ordered, progress],
  );
  const batch = useMemo(() => summarizeBatch(progressRows), [progressRows]);
  const skippedKeys = useMemo(
    () => progressRows.filter((p) => p.status === 'skipped').map((p) => p.key),
    [progressRows],
  );
  /** The agent the "Open …" button will land on — the first row that made it
   *  through, matching `batch.results[0]` which useAgentImported selects. */
  const firstImportedName = useMemo(
    () => progressRows.find((p) => p.status === 'done')?.label ?? '',
    [progressRows],
  );

  /** Stop = skip every row that hasn't started, AND tell the server to finish
   *  the one that has without further LLM summaries. Aborting the in-flight
   *  request instead would leave a half-populated agent; waiting it out can be
   *  minutes (one model call per session). See lib/migrationImportQueue. */
  const requestStop = () => {
    stopRef.current = true;
    setStopping(true);
    const key = runningKey.current;
    const importId = key ? importIds.current[key] : undefined;
    // Best-effort: if the hurry never reaches the worker, the import simply
    // keeps its summaries. Nothing to recover, nothing to report.
    if (importId) void api.migrateHurry(importId).catch(() => {});
  };

  const retryRow = (key: string) => {
    const d = detections.find((x) => detectionKey(x) === key);
    if (d) void runQueue([d], false);
  };

  const resumeSkipped = () => {
    const rows = ordered.filter((d) => skippedKeys.includes(detectionKey(d)));
    void runQueue(rows, false);
  };
  return {
    // detect
    detections,
    detecting,
    error,
    runDetect,
    // list shape
    groups,
    ordered,
    // selection
    selected,
    selectedRows,
    selectedSessionTotal,
    toggleRow,
    setMany,
    // groups
    expandedGroups,
    toggleGroupExpanded,
    // per-row detail
    expanded,
    scans,
    names,
    sessions,
    toggleExpand,
    ensureScan,
    renameRow: (key: string, value: string) =>
      setNames((prev) => ({ ...prev, [key]: value })),
    toggleSession: (key: string, id: string) =>
      setSessions((prev) => {
        const next = new Set(prev[key] ?? []);
        if (next.has(id)) next.delete(id);
        else next.add(id);
        return { ...prev, [key]: next };
      }),
    // manual folder
    manualPath,
    manualScanning,
    setManualPath,
    scanManualPath,
    // queue
    phase,
    progressRows,
    batch,
    stopping,
    skippedKeys,
    firstImportedName,
    startImport: (rows: FrameworkDetection[]) => void runQueue(rows, true),
    requestStop,
    retryRow,
    resumeSkipped,
  };
}

export type AgentImportController = ReturnType<typeof useAgentImport>;
