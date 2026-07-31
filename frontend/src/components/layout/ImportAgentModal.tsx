/**
 * @file_name: ImportAgentModal.tsx
 * @author: NetMind.AI
 * @date: 2026-07-21
 * @description: "Create Agent (from other source)" flow — detect other-framework
 * agents on the local machine, preview the scanned config, and apply it as a
 * new NarraNexus agent.
 *
 * Four stages:
 *   framework → GET  /api/migrate/detect  (group hits; pick a framework first)
 *   source    → pick which source under that framework (e.g. one Claude Code
 *               project among many); or type a folder path
 *   preview   → POST /api/migrate/scan    (extract one source → standardized JSON)
 *   done      → POST /api/migrate/apply   (create + populate the agent)
 *
 * The framework→source split exists because Claude Code is per-project: detect
 * returns one row per project, so we group by framework first, then let the user
 * drill into the project list.
 *
 * LOCAL ONLY: detect/scan read the user's filesystem, so the parent only mounts
 * this in local mode. The preview flags MCP servers that carry secrets (it shows
 * name + transport + a warning, never the credential values). Each source session
 * becomes one Narrative (summarized on apply) with its turns kept as memory.
 */

import { useState, useEffect, useCallback, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Bot,
  FolderSearch,
  Loader2,
  AlertTriangle,
  CheckCircle2,
  ArrowLeft,
  ChevronRight,
  Puzzle,
  Brain,
  Plug,
} from 'lucide-react';
import { Dialog, DialogContent, DialogFooter, Button, Input } from '@/components/ui';
import { api } from '@/lib/api';
import { cn } from '@/lib/utils';
import { FRAMEWORK_LABELS as FRAMEWORK_LABEL, FRAMEWORK_ORDER } from '@/lib/migrationLabels';
import type {
  FrameworkDetection,
  MigrationFramework,
  StandardizedAgentImport,
  MigrationApplyResult,
} from '@/types';

type Stage = 'framework' | 'source' | 'preview' | 'done';

export interface ImportAgentModalProps {
  onClose: () => void;
  /** Called after a successful apply so the parent can refresh + select. */
  onApplied: (result: MigrationApplyResult) => void;
}


/** Claude Code enumerates one detection per project — suffix the project's
 *  folder name so the repeated "Claude Code" rows are distinguishable. */
function detectionTitle(d: FrameworkDetection): string {
  const label = FRAMEWORK_LABEL[d.framework] ?? d.framework;
  if (d.framework === 'claude_code' && d.signals.includes('project')) {
    const base = d.path.replace(/\/+$/, '').split('/').pop() || d.path;
    return `${label} · ${base}`;
  }
  return label;
}

/** A short right-aligned hint derived from detection signals (session count /
 *  global-config fallback marker). */
function detectionHint(d: FrameworkDetection): string {
  if (d.signals.includes('global-shared-config')) return 'shared config';
  const sess = d.signals.find((s) => s.startsWith('sessions:'));
  if (sess) return `${sess.split(':')[1]} sessions`;
  return '';
}

export function ImportAgentModal({ onClose, onApplied }: ImportAgentModalProps) {
  const { t } = useTranslation();
  const [stage, setStage] = useState<Stage>('framework');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [detections, setDetections] = useState<FrameworkDetection[]>([]);
  const [framework, setFramework] = useState<MigrationFramework | null>(null);
  const [manualPath, setManualPath] = useState('');
  const [scan, setScan] = useState<StandardizedAgentImport | null>(null);
  const [result, setResult] = useState<MigrationApplyResult | null>(null);
  // Editable in the preview: the agent name + which sessions to import (all =
  // per-project, one = per-session). Initialized from the scan.
  const [agentName, setAgentName] = useState('');
  const [selectedSessions, setSelectedSessions] = useState<Set<string>>(new Set());

  useEffect(() => {
    if (scan) {
      setAgentName(scan.agent.name || '');
      setSelectedSessions(new Set(scan.sessions.map((s) => s.session_id)));
    }
  }, [scan]);

  const runDetect = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.migrateDetect();
      setDetections(res.detections);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void runDetect();
  }, [runDetect]);

  // Group detections by framework, in the stable display order.
  const frameworkGroups = useMemo(() => {
    const by = new Map<MigrationFramework, FrameworkDetection[]>();
    for (const d of detections) {
      const arr = by.get(d.framework) ?? [];
      arr.push(d);
      by.set(d.framework, arr);
    }
    return FRAMEWORK_ORDER.filter((fw) => by.has(fw)).map((fw) => ({
      framework: fw,
      sources: by.get(fw)!,
    }));
  }, [detections]);

  const sources = useMemo(
    () => (framework ? detections.filter((d) => d.framework === framework) : []),
    [detections, framework],
  );

  const runScan = useCallback(
    async (path?: string, fw?: MigrationFramework) => {
      setLoading(true);
      setError(null);
      try {
        const res = await api.migrateScan(path, fw);
        setScan(res);
        setStage('preview');
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setLoading(false);
      }
    },
    [],
  );

  // Pick a framework → drill into its source list. (Single-source frameworks
  // still show the list for a consistent two-step flow.)
  const pickFramework = (fw: MigrationFramework) => {
    setError(null);
    setFramework(fw);
    setStage('source');
  };

  const runApply = useCallback(async () => {
    if (!scan) return;
    setLoading(true);
    setError(null);
    try {
      // Apply the user's edits: the (possibly renamed) agent + only the selected
      // sessions (all = per-project, one = per-session).
      const importData: StandardizedAgentImport = {
        ...scan,
        agent: { ...scan.agent, name: agentName.trim() || scan.agent.name },
        sessions: scan.sessions.filter((s) => selectedSessions.has(s.session_id)),
      };
      const res = await api.migrateApply(importData);
      setResult(res);
      setStage('done');
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [scan, agentName, selectedSessions]);

  const toggleSession = (id: string) =>
    setSelectedSessions((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const title =
    stage === 'preview'
      ? t('layout.importAgent.previewTitle')
      : stage === 'done'
        ? t('layout.importAgent.doneTitle')
        : stage === 'source'
          ? t('layout.importAgent.sourceTitle')
          : t('layout.importAgent.title');

  return (
    <Dialog isOpen onClose={onClose} title={title} size="lg">
      <DialogContent>
        {error && (
          <div className="mb-3 flex items-start gap-2 rounded-[var(--radius-sm)] border border-[var(--nm-danger)] bg-[var(--nm-danger)]/5 px-3 py-2 text-xs text-[var(--nm-danger)]">
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <span className="break-all">{error}</span>
          </div>
        )}

        {stage === 'framework' && (
          <FrameworkStage
            loading={loading}
            groups={frameworkGroups}
            manualPath={manualPath}
            onManualPathChange={setManualPath}
            onPick={pickFramework}
            onScanManual={() => runScan(manualPath.trim() || undefined)}
          />
        )}

        {stage === 'source' && framework && (
          <SourceStage
            loading={loading}
            framework={framework}
            sources={sources}
            onPick={(d) => runScan(d.path, d.framework)}
          />
        )}

        {stage === 'preview' && scan && (
          <PreviewStage
            scan={scan}
            agentName={agentName}
            onAgentNameChange={setAgentName}
            selectedSessions={selectedSessions}
            onToggleSession={toggleSession}
          />
        )}

        {stage === 'done' && result && <DoneStage result={result} />}
      </DialogContent>

      <DialogFooter>
        {stage === 'framework' && (
          <Button variant="ghost" onClick={onClose}>
            {t('common.cancel')}
          </Button>
        )}
        {stage === 'source' && (
          <Button variant="ghost" onClick={() => { setStage('framework'); setFramework(null); }}>
            <ArrowLeft className="mr-1 h-3.5 w-3.5" />
            {t('common.back')}
          </Button>
        )}
        {stage === 'preview' && (
          <>
            <Button
              variant="ghost"
              onClick={() => { setStage('source'); setScan(null); }}
            >
              <ArrowLeft className="mr-1 h-3.5 w-3.5" />
              {t('common.back')}
            </Button>
            <Button onClick={runApply} disabled={loading}>
              {loading && <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />}
              {t('layout.importAgent.confirmImport')}
            </Button>
          </>
        )}
        {stage === 'done' && result && (
          <Button
            onClick={() => {
              onApplied(result);
              onClose();
            }}
          >
            {t('layout.importAgent.openAgent')}
          </Button>
        )}
      </DialogFooter>
    </Dialog>
  );
}

// ── framework stage (step 1) ────────────────────────────────────────────────

function FrameworkStage({
  loading,
  groups,
  manualPath,
  onManualPathChange,
  onPick,
  onScanManual,
}: {
  loading: boolean;
  groups: Array<{ framework: MigrationFramework; sources: FrameworkDetection[] }>;
  manualPath: string;
  onManualPathChange: (v: string) => void;
  onPick: (fw: MigrationFramework) => void;
  onScanManual: () => void;
}) {
  const { t } = useTranslation();

  return (
    <div className="space-y-4">
      <p className="text-xs text-[var(--nm-ink-soft)]">{t('layout.importAgent.frameworkHint')}</p>

      {loading && groups.length === 0 ? (
        <div className="flex items-center justify-center gap-2 py-8 text-xs text-[var(--nm-ink-soft)]">
          <Loader2 className="h-4 w-4 animate-spin" />
          {t('layout.importAgent.detecting')}
        </div>
      ) : groups.length === 0 ? (
        <div className="rounded-[var(--radius-sm)] border border-dashed border-[var(--nm-hairline)] px-3 py-6 text-center text-xs text-[var(--nm-ink-soft)]">
          {t('layout.importAgent.noneFound')}
        </div>
      ) : (
        <div className="space-y-2">
          {groups.map(({ framework, sources }) => (
            <button
              key={framework}
              onClick={() => onPick(framework)}
              className={cn(
                'flex w-full items-center gap-3 rounded-[var(--radius-sm)] border px-3 py-2.5 text-left transition-colors',
                'border-[var(--nm-hairline)] hover:bg-[var(--nm-paper-warm)]',
              )}
            >
              <Bot className="h-4 w-4 shrink-0 text-[var(--nm-ink-soft)]" />
              <div className="min-w-0 flex-1">
                <span className="text-xs font-medium text-[var(--nm-ink)]">
                  {FRAMEWORK_LABEL[framework] ?? framework}
                </span>
                <div className="text-[11px] text-[var(--nm-ink-soft)]">
                  {t(
                    framework === 'claude_code'
                      ? 'layout.importAgent.countProjects'
                      : 'layout.importAgent.countSources',
                    { count: sources.length },
                  )}
                </div>
              </div>
              <ChevronRight className="h-4 w-4 shrink-0 text-[var(--nm-ink-soft)]" />
            </button>
          ))}
        </div>
      )}

      {/* manual path fallback */}
      <div className="space-y-1.5 border-t border-[var(--nm-hairline)] pt-3">
        <label className="text-[11px] text-[var(--nm-ink-soft)]">
          {t('layout.importAgent.manualPathLabel')}
        </label>
        <div className="flex gap-2">
          <Input
            value={manualPath}
            onChange={(e) => onManualPathChange(e.target.value)}
            placeholder="~/.claude"
            className="flex-1 text-xs"
          />
          <Button variant="outline" onClick={onScanManual} disabled={loading || !manualPath.trim()}>
            <FolderSearch className="mr-1 h-3.5 w-3.5" />
            {t('layout.importAgent.scan')}
          </Button>
        </div>
      </div>
    </div>
  );
}

// ── source stage (step 2) ───────────────────────────────────────────────────

function SourceStage({
  loading,
  framework,
  sources,
  onPick,
}: {
  loading: boolean;
  framework: MigrationFramework;
  sources: FrameworkDetection[];
  onPick: (d: FrameworkDetection) => void;
}) {
  const { t } = useTranslation();

  return (
    <div className="space-y-4">
      <p className="text-xs text-[var(--nm-ink-soft)]">
        {t('layout.importAgent.sourceHint', { framework: FRAMEWORK_LABEL[framework] ?? framework })}
      </p>

      <div className="space-y-2">
        {sources.map((d) => (
          <button
            key={`${d.framework}-${d.path}`}
            onClick={() => onPick(d)}
            disabled={loading}
            className={cn(
              'flex w-full items-center gap-3 rounded-[var(--radius-sm)] border px-3 py-2.5 text-left transition-colors',
              'border-[var(--nm-hairline)] hover:bg-[var(--nm-paper-warm)]',
              loading && 'pointer-events-none opacity-50',
            )}
          >
            <Bot className="h-4 w-4 shrink-0 text-[var(--nm-ink-soft)]" />
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span className="truncate text-xs font-medium text-[var(--nm-ink)]">
                  {detectionTitle(d)}
                </span>
                <ConfidenceBadge confidence={d.confidence} />
                {detectionHint(d) && (
                  <span className="shrink-0 text-[10px] text-[var(--nm-ink-soft)]">
                    {detectionHint(d)}
                  </span>
                )}
              </div>
              <div className="truncate text-[11px] text-[var(--nm-ink-soft)]">{d.path}</div>
            </div>
            {loading && <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-[var(--nm-ink-soft)]" />}
          </button>
        ))}
      </div>
    </div>
  );
}

function ConfidenceBadge({ confidence }: { confidence: string }) {
  const tone =
    confidence === 'high'
      ? 'text-[var(--nm-success)]'
      : confidence === 'medium'
        ? 'text-[var(--nm-warning)]'
        : 'text-[var(--nm-ink-soft)]';
  return <span className={cn('text-[10px] uppercase tracking-wide', tone)}>{confidence}</span>;
}

// ── preview stage ─────────────────────────────────────────────────────────

function PreviewStage({
  scan,
  agentName,
  onAgentNameChange,
  selectedSessions,
  onToggleSession,
}: {
  scan: StandardizedAgentImport;
  agentName: string;
  onAgentNameChange: (v: string) => void;
  selectedSessions: Set<string>;
  onToggleSession: (id: string) => void;
}) {
  const { t } = useTranslation();
  const hasSecrets = scan.mcp_servers.some((m) => m.secret_fields.length > 0);

  return (
    <div className="space-y-4 text-xs">
      {/* agent identity — name is editable before import */}
      <div>
        <div className="flex items-center gap-2">
          <Bot className="h-4 w-4 shrink-0 text-[var(--nm-ink-soft)]" />
          <Input
            value={agentName}
            onChange={(e) => onAgentNameChange(e.target.value)}
            placeholder={t('layout.importAgent.unnamed')}
            className="flex-1 text-sm"
          />
          <span className="shrink-0 rounded-full border border-[var(--nm-hairline)] px-2 py-0.5 text-[10px] text-[var(--nm-ink-soft)]">
            {FRAMEWORK_LABEL[scan.source.framework] ?? scan.source.framework}
          </span>
        </div>
        <div className="mt-1 truncate text-[11px] text-[var(--nm-ink-soft)]">
          {scan.source.detected_path}
        </div>
      </div>

      {/* sessions to import — all checked = per-project, one = per-session */}
      {scan.sessions.length > 0 && (
        <Section
          title={`${t('layout.importAgent.narrative')} · ${selectedSessions.size}/${scan.sessions.length}`}
        >
          <p className="mb-1.5 text-[11px] text-[var(--nm-ink-soft)]">
            {t('layout.importAgent.sessionsHint', { count: selectedSessions.size })}
          </p>
          <ul className="max-h-44 space-y-1 overflow-y-auto">
            {scan.sessions.map((s) => (
              <li key={s.session_id}>
                <label className="flex cursor-pointer items-center gap-2 rounded-[var(--radius-sm)] px-1 py-1 hover:bg-[var(--nm-paper-warm)]">
                  <input
                    type="checkbox"
                    checked={selectedSessions.has(s.session_id)}
                    onChange={() => onToggleSession(s.session_id)}
                    className="shrink-0"
                  />
                  <span className="min-w-0 flex-1 truncate text-[var(--nm-ink)]">
                    {s.title || t('layout.importAgent.untitledSession')}
                  </span>
                  <span className="shrink-0 text-[10px] text-[var(--nm-ink-soft)]">
                    {t('layout.importAgent.turnCount', { count: s.turns.length })}
                  </span>
                </label>
              </li>
            ))}
          </ul>
        </Section>
      )}

      {/* dimensions summary */}
      <div className="grid grid-cols-3 gap-2">
        <StatCard icon={<Puzzle className="h-3.5 w-3.5" />} n={scan.skills.length} label={t('layout.importAgent.skills')} />
        <StatCard icon={<Brain className="h-3.5 w-3.5" />} n={scan.memory.length} label={t('layout.importAgent.memories')} />
        <StatCard icon={<Plug className="h-3.5 w-3.5" />} n={scan.mcp_servers.length} label={t('layout.importAgent.mcpServers')} />
      </div>

      {/* skills detail */}
      {scan.skills.length > 0 && (
        <Section title={t('layout.importAgent.skills')}>
          <ul className="space-y-1">
            {scan.skills.map((s) => (
              <li key={s.name} className="flex items-center justify-between gap-2">
                <span className="truncate text-[var(--nm-ink)]">{s.name}</span>
                <span className="shrink-0 text-[10px] text-[var(--nm-ink-soft)]">
                  {s.local_path
                    ? t('layout.importAgent.willCopy')
                    : t('layout.importAgent.willMatch')}
                </span>
              </li>
            ))}
          </ul>
        </Section>
      )}

      {/* mcp detail + plaintext credential warning */}
      {scan.mcp_servers.length > 0 && (
        <Section title={t('layout.importAgent.mcpServers')}>
          <ul className="space-y-1">
            {scan.mcp_servers.map((m) => (
              <li key={m.name} className="flex items-center justify-between gap-2">
                <span className="truncate text-[var(--nm-ink)]">{m.name}</span>
                <span className="shrink-0 text-[10px] uppercase text-[var(--nm-ink-soft)]">
                  {m.transport}
                  {m.transport === 'stdio' && ` · ${t('layout.importAgent.deferred')}`}
                </span>
              </li>
            ))}
          </ul>
        </Section>
      )}

      {hasSecrets && (
        <div className="flex items-start gap-2 rounded-[var(--radius-sm)] border border-[var(--nm-warning)] bg-[var(--nm-warning)]/5 px-3 py-2 text-[var(--nm-warning)]">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span>{t('layout.importAgent.secretWarning')}</span>
        </div>
      )}

      {/* warnings from the scan */}
      {(scan.custom.credential_keys.length > 0 || scan.custom.unmapped_files.length > 0) && (
        <Section title={t('layout.importAgent.notes')}>
          {scan.custom.credential_keys.length > 0 && (
            <p className="text-[11px] text-[var(--nm-ink-soft)]">
              {t('layout.importAgent.credentialNote', {
                keys: scan.custom.credential_keys.join(', '),
              })}
            </p>
          )}
          {scan.custom.unmapped_files.length > 0 && (
            <p className="text-[11px] text-[var(--nm-ink-soft)]">
              {t('layout.importAgent.unmappedNote', {
                count: scan.custom.unmapped_files.length,
              })}
            </p>
          )}
        </Section>
      )}
    </div>
  );
}

function StatCard({ icon, n, label }: { icon: React.ReactNode; n: number; label: string }) {
  return (
    <div className="flex flex-col items-center gap-1 rounded-[var(--radius-sm)] border border-[var(--nm-hairline)] py-2.5">
      <div className="flex items-center gap-1 text-[var(--nm-ink-soft)]">{icon}</div>
      <span className="text-base font-medium text-[var(--nm-ink)]">{n}</span>
      <span className="text-[10px] text-[var(--nm-ink-soft)]">{label}</span>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1.5">
      <div className="text-[10px] uppercase tracking-wide text-[var(--nm-ink-soft)]">{title}</div>
      {children}
    </div>
  );
}

// ── done stage ────────────────────────────────────────────────────────────

function DoneStage({ result }: { result: MigrationApplyResult }) {
  const { t } = useTranslation();
  const rows: Array<[string, string]> = [
    [t('layout.importAgent.awareness'), result.awareness_written ? '✓' : '—'],
    [t('layout.importAgent.memories'), String(result.memory_written)],
    [t('layout.importAgent.defaultSkills'), String(result.default_skills_installed.length)],
    [t('layout.importAgent.skillsCopied'), String(result.skills_copied.length)],
    [t('layout.importAgent.skillsInstalled'), String(result.skills_installed.length)],
    [t('layout.importAgent.mcpAdded'), String(result.mcp_added.length)],
    [t('layout.importAgent.narrativesCreated'), String(result.narratives_created.length)],
    [t('layout.importAgent.turnsRetained'), String(result.memory_turns_retained)],
  ];
  return (
    <div className="space-y-4 text-xs">
      <div className="flex items-center gap-2 text-[var(--nm-success)]">
        <CheckCircle2 className="h-4 w-4" />
        <span className="text-sm font-medium">{t('layout.importAgent.doneMsg')}</span>
      </div>

      <div className="divide-y divide-[var(--nm-hairline)] rounded-[var(--radius-sm)] border border-[var(--nm-hairline)]">
        {rows.map(([k, v]) => (
          <div key={k} className="flex items-center justify-between px-3 py-1.5">
            <span className="text-[var(--nm-ink-soft)]">{k}</span>
            <span className="text-[var(--nm-ink)]">{v}</span>
          </div>
        ))}
      </div>

      {result.skills_unmatched.length > 0 && (
        <p className="text-[11px] text-[var(--nm-ink-soft)]">
          {t('layout.importAgent.skillsUnmatchedNote', {
            names: result.skills_unmatched.join(', '),
          })}
        </p>
      )}
      {result.mcp_stdio_skipped.length > 0 && (
        <p className="text-[11px] text-[var(--nm-ink-soft)]">
          {t('layout.importAgent.stdioSkippedNote', {
            names: result.mcp_stdio_skipped.join(', '),
          })}
        </p>
      )}
      {result.warnings.map((w, i) => (
        <div key={i} className="flex items-start gap-2 text-[11px] text-[var(--nm-warning)]">
          <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
          <span>{w}</span>
        </div>
      ))}
    </div>
  );
}
