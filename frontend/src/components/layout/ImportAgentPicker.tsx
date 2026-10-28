/**
 * @file_name: ImportAgentPicker.tsx
 * @author: NetMind.AI
 * @date: 2026-08-27
 * @description: The one-page "pick which agents to import" body — grouped
 * checkbox list, inline per-row detail, and the batch report the same list
 * turns into while the queue runs. NO chrome: no dialog, no footer, no page
 * layout.
 *
 * Split out of ImportAgentModal (2026-08-27) so the sidebar modal and step 2 of
 * the first-run welcome flow render the SAME picker and can only differ where
 * they should — in the buttons around it. All state lives in
 * [[useAgentImport]]; this file is presentation.
 *
 * Row anatomy: checkbox · brand mark · title/meta · confidence · chevron.
 * The chevron lazily scans that source and expands its details inline (rename,
 * per-session checkboxes, skills/memory/MCP counts, plaintext-credential
 * warning). Not expanding a row is a valid choice — it imports as scanned.
 */

import { createElement } from 'react';
import { useTranslation } from 'react-i18next';
import {
  AlertTriangle,
  Brain,
  CheckCircle2,
  ChevronRight,
  CircleSlash,
  FolderSearch,
  Loader2,
  Plug,
  Puzzle,
  RefreshCw,
  XCircle,
} from 'lucide-react';
import { Button, Input } from '@/components/ui';
import { Checkbox } from '@/components/nm';
import { cn } from '@/lib/utils';
import { frameworkIcon, frameworkLabel } from '@/lib/migrationLabels';
import {
  detectionKey,
  detectionTitle,
  groupDetections,
  isSharedConfig,
  sessionCount,
} from '@/lib/migrationDetections';
import { summarizeBatch, type ImportQueueProgress } from '@/lib/migrationImportQueue';
import type { AgentImportController, ImportPhase, ScanState } from '@/hooks/useAgentImport';
import type { FrameworkDetection, StandardizedAgentImport } from '@/types';

/** The framework's brand mark. A static component that resolves the icon via
 *  createElement, because binding `const Icon = frameworkIcon(fw)` inside a
 *  component body is a fresh component identity every render
 *  (react-hooks/static-components). */
function FrameworkIcon({ framework, className }: { framework: string; className?: string }) {
  return createElement(frameworkIcon(framework), { className });
}

export interface ImportAgentPickerProps {
  controller: AgentImportController;
  /** Optional intro line above the list (the welcome flow supplies its own). */
  lede?: string;
}

/** The picker body: an error banner, then either the list or the batch report. */
export function ImportAgentPicker({ controller: c, lede }: ImportAgentPickerProps) {
  const { t } = useTranslation();
  return (
    <>
      {c.error && (
        <div className="mb-3 flex items-start gap-2 rounded-[var(--radius-sm)] border border-[var(--color-error)] bg-[var(--color-error)]/5 px-3 py-2 text-[11px] text-[var(--color-error)]">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span className="break-all">{c.error}</span>
        </div>
      )}

      {c.phase === 'list' ? (
        <ListPhase
          lede={lede ?? t('layout.importAgent.lede')}
          detecting={c.detecting}
          detections={c.detections}
          groups={c.groups}
          selected={c.selected}
          expanded={c.expanded}
          expandedGroups={c.expandedGroups}
          onToggleGroupExpanded={c.toggleGroupExpanded}
          scans={c.scans}
          names={c.names}
          sessions={c.sessions}
          onToggleRow={c.toggleRow}
          onToggleGroup={c.setMany}
          onToggleExpand={c.toggleExpand}
          onRetryScan={c.ensureScan}
          onRename={c.renameRow}
          onToggleSession={c.toggleSession}
          onRescan={() => void c.runDetect()}
          manualPath={c.manualPath}
          manualScanning={c.manualScanning}
          onManualPathChange={c.setManualPath}
          onScanManual={() => void c.scanManualPath()}
        />
      ) : (
        <BatchPhase
          phase={c.phase}
          rows={c.progressRows}
          batch={c.batch}
          stopping={c.stopping}
          onRetry={c.retryRow}
        />
      )}
    </>
  );
}

// ── list phase ─────────────────────────────────────────────────────────────

interface ListPhaseProps {
  lede?: string;
  detecting: boolean;
  detections: FrameworkDetection[];
  groups: ReturnType<typeof groupDetections>;
  selected: Set<string>;
  expanded: string | null;
  expandedGroups: Set<string>;
  scans: Record<string, ScanState>;
  names: Record<string, string>;
  sessions: Record<string, Set<string>>;
  onToggleRow: (key: string) => void;
  onToggleGroup: (keys: string[], on: boolean) => void;
  onToggleGroupExpanded: (framework: string) => void;
  onToggleExpand: (d: FrameworkDetection) => void;
  onRetryScan: (d: FrameworkDetection) => void;
  onRename: (key: string, value: string) => void;
  onToggleSession: (key: string, id: string) => void;
  onRescan: () => void;
  manualPath: string;
  manualScanning: boolean;
  onManualPathChange: (v: string) => void;
  onScanManual: () => void;
}

function ListPhase({
  lede,
  detecting,
  detections,
  groups,
  selected,
  expanded,
  expandedGroups,
  scans,
  names,
  sessions,
  onToggleRow,
  onToggleGroup,
  onToggleGroupExpanded,
  onToggleExpand,
  onRetryScan,
  onRename,
  onToggleSession,
  onRescan,
  manualPath,
  manualScanning,
  onManualPathChange,
  onScanManual,
}: ListPhaseProps) {
  const { t } = useTranslation();
  const allKeys = detections.map(detectionKey);
  const allSelected = allKeys.length > 0 && allKeys.every((k) => selected.has(k));

  return (
    <div>
      <p className="text-[13px] leading-relaxed text-[var(--nm-ink70)]">
        {lede ?? t('layout.importAgent.lede')}
      </p>

      {/* summary strip — total found + a way to rescan after installing a tool */}
      <div className="mt-3 flex items-center gap-2 rounded-[var(--radius-md)] border border-[var(--nm-hairline)] bg-[var(--nm-paper-warm)] px-3 py-2.5">
        <span className="font-[family-name:var(--font-display)] text-base font-bold leading-none tabular-nums text-[var(--nm-ink)]">
          {detections.length}
        </span>
        <span className="text-xs text-[var(--nm-ink70)]">
          {t('layout.importAgent.agentsFound', { count: detections.length })}
        </span>
        <span className="text-xs text-[var(--nm-ink30)]">·</span>
        <span className="text-xs text-[var(--nm-ink70)]">
          {t('layout.importAgent.toolsCount', { count: groups.length })}
        </span>
        <Button variant="ghost" size="sm" className="ml-auto" onClick={onRescan} disabled={detecting}>
          {detecting ? (
            <Loader2 className="mr-1 h-3 w-3 animate-spin" />
          ) : (
            <RefreshCw className="mr-1 h-3 w-3" />
          )}
          {t('layout.importAgent.rescan')}
        </Button>
      </div>

      {detecting && detections.length === 0 ? (
        <div className="flex items-center justify-center gap-2 py-8 text-xs text-[var(--nm-ink50)]">
          <Loader2 className="h-4 w-4 animate-spin" />
          {t('layout.importAgent.detecting')}
        </div>
      ) : detections.length === 0 ? (
        <div className="mt-3 rounded-[var(--radius-sm)] border border-dashed border-[var(--nm-hairline)] px-3 py-6 text-center text-xs text-[var(--nm-ink50)]">
          {t('layout.importAgent.noneFound')}
        </div>
      ) : (
        <>
          {/* Select-all sits at the SAME level as the tool rows (Owner
              2026-08-27) — same padding, same type size — because it is the same
              kind of thing: a checkbox that owns a set of rows. It just owns all
              of them. */}
          <div className="mt-4 flex items-center gap-2 rounded-[var(--radius-sm)] px-1.5 py-2">
            <Checkbox
              checked={allSelected}
              onChange={(on) => onToggleGroup(allKeys, on)}
              ariaLabel={t('layout.importAgent.selectAll', { count: detections.length })}
            />
            <span className="text-[13px] font-medium tracking-tight text-[var(--nm-ink)]">
              {t('layout.importAgent.selectedOf', {
                selected: allKeys.filter((k) => selected.has(k)).length,
                total: detections.length,
              })}
            </span>
          </div>

          <div className="max-h-[320px] space-y-0.5 overflow-y-auto">
            {groups.map((group) => {
              const keys = group.detections.map(detectionKey);
              const groupAll = keys.every((k) => selected.has(k));
              const groupSelected = keys.filter((k) => selected.has(k)).length;
              // A one-row tool needs no header — it would say the same thing
              // twice — so that row is rendered directly and always visible.
              const showHeader = group.detections.length > 1;
              // Multi-row groups start CLOSED: 27 Claude Code projects flat on
              // the page read as a wall, not a choice (Owner 2026-08-27). The
              // header carries the count and how many are already checked, so
              // the pre-selection is legible without opening anything.
              const open = !showHeader || expandedGroups.has(group.framework);
              return (
                <div key={group.framework}>
                  {showHeader && (
                    <div
                      className={cn(
                        'group flex items-center gap-2 rounded-[var(--radius-sm)] px-1.5 py-2 transition-shadow',
                        'hover:bg-[var(--nm-row-hover)] hover:shadow-[var(--nm-elev-1)]',
                      )}
                    >
                      <Checkbox
                        checked={groupAll}
                        onChange={(on) => onToggleGroup(keys, on)}
                        ariaLabel={t('layout.importAgent.selectGroup', {
                          framework: frameworkLabel(group.framework),
                        })}
                      />
                      {/* The header IS the disclosure — the whole strip toggles,
                          so the target is the row, not a 14px chevron. */}
                      <button
                        type="button"
                        onClick={() => onToggleGroupExpanded(group.framework)}
                        aria-expanded={open}
                        className="flex min-w-0 flex-1 items-center gap-2 text-left"
                      >
                        <FrameworkIcon
                          framework={group.framework}
                          className="h-4 w-4 shrink-0"
                        />
                        <span className="text-[13px] font-medium tracking-tight text-[var(--nm-ink)]">
                          {frameworkLabel(group.framework)}
                        </span>
                        <span className="font-[family-name:var(--font-mono)] text-[10px] tabular-nums text-[var(--nm-ink50)]">
                          {groupSelected > 0
                            ? t('layout.importAgent.groupCountSelected', {
                                selected: groupSelected,
                                total: group.detections.length,
                              })
                            : t('layout.importAgent.groupCount', {
                                count: group.detections.length,
                              })}
                        </span>
                        <ChevronRight
                          className={cn(
                            'ml-auto h-3.5 w-3.5 shrink-0 text-[var(--nm-ink30)] transition-transform',
                            open && 'rotate-90',
                          )}
                        />
                      </button>
                    </div>
                  )}

                  {open && group.detections.map((d) => {
                    const key = detectionKey(d);
                    return (
                      <DetectionRow
                        key={key}
                        detection={d}
                        rowKey={key}
                        indented={showHeader}
                        showFramework={!showHeader}
                        checked={selected.has(key)}
                        open={expanded === key}
                        scan={scans[key]}
                        name={names[key]}
                        selectedSessions={sessions[key]}
                        onToggle={() => onToggleRow(key)}
                        onToggleExpand={() => onToggleExpand(d)}
                        onRetryScan={() => onRetryScan(d)}
                        onRename={(v) => onRename(key, v)}
                        onToggleSession={(id) => onToggleSession(key, id)}
                      />
                    );
                  })}
                </div>
              );
            })}
          </div>
        </>
      )}

      {/* manual path fallback — a scanned folder joins the list as its own row */}
      <div className="mt-5 flex items-center gap-2 pt-1">
        <span className="shrink-0 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.10em] text-[var(--nm-ink50)]">
          {t('layout.importAgent.manualPathLabel')}
        </span>
        <Input
          value={manualPath}
          onChange={(e) => onManualPathChange(e.target.value)}
          placeholder="~/.claude"
          className="flex-1 text-xs"
        />
        <Button
          variant="outline"
          size="sm"
          onClick={onScanManual}
          disabled={manualScanning || !manualPath.trim()}
        >
          {manualScanning ? (
            <Loader2 className="mr-1 h-3 w-3 animate-spin" />
          ) : (
            <FolderSearch className="mr-1 h-3 w-3" />
          )}
          {t('layout.importAgent.scan')}
        </Button>
      </div>
    </div>
  );
}

// ── one detection row (+ its inline detail) ────────────────────────────────

function DetectionRow({
  detection,
  rowKey,
  indented,
  showFramework,
  checked,
  open,
  scan,
  name,
  selectedSessions,
  onToggle,
  onToggleExpand,
  onRetryScan,
  onRename,
  onToggleSession,
}: {
  detection: FrameworkDetection;
  rowKey: string;
  /** Sits under a group header, so it reads as secondary: inset + no mark. */
  indented: boolean;
  /** No group header above this row — carry the framework icon + label here. */
  showFramework: boolean;
  checked: boolean;
  open: boolean;
  scan?: ScanState;
  name?: string;
  selectedSessions?: Set<string>;
  onToggle: () => void;
  onToggleExpand: () => void;
  onRetryScan: () => void;
  onRename: (v: string) => void;
  onToggleSession: (id: string) => void;
}) {
  const { t } = useTranslation();
  const sessions = sessionCount(detection);
  const hasSecrets =
    scan?.status === 'ready' && scan.data.mcp_servers.some((m) => m.secret_fields.length > 0);

  const meta = [
    // Without a group header the row is the only place the tool is named.
    showFramework ? frameworkLabel(detection.framework) : null,
    detection.path,
    isSharedConfig(detection)
      ? t('layout.importAgent.sharedConfig')
      : sessions > 0
        ? t('layout.importAgent.sessionCount', { count: sessions })
        : t('layout.importAgent.noSessions'),
  ]
    .filter(Boolean)
    .join(' · ');

  return (
    <div>
      {/* No dividing rules between rows (Owner 2026-08-27: "去掉横线,做成 hover
          阴影"). Separation comes from the 2px gap plus elevation on hover, so a
          long list reads as a stack of cards instead of a striped table. The
          selected fill stays — it is the §2.5 row ladder, and it has to survive
          without a border to lean on. */}
      <div
        className={cn(
          'flex items-center gap-2 rounded-[var(--radius-sm)] transition-shadow',
          indented && 'ml-6',
          checked
            ? 'bg-[var(--nm-row-active)] hover:shadow-[var(--nm-elev-1)]'
            : 'hover:bg-[var(--nm-row-hover)] hover:shadow-[var(--nm-elev-1)]',
        )}
      >
        <span className="pl-1.5">
          <Checkbox checked={checked} onChange={onToggle} ariaLabel={detectionTitle(detection)} />
        </span>
        {/* Clicking the row OPENS it; only the checkbox selects it (Owner
            2026-08-27). Selecting was the wrong default action for a 40px-tall
            strip whose whole point is "let me look inside first" — and the row
            already has a dedicated hit target for selection. */}
        <button
          type="button"
          onClick={onToggleExpand}
          aria-expanded={open}
          className="flex min-w-0 flex-1 items-center gap-2 py-2 pr-2 text-left"
        >
          {showFramework && (
            <FrameworkIcon
              framework={detection.framework}
              className="h-3.5 w-3.5 shrink-0 text-[var(--nm-ink50)]"
            />
          )}
          <span className="min-w-0 flex-1">
            <span className="flex items-center gap-1.5">
              <span className="truncate text-[13px] font-medium text-[var(--nm-ink)]">
                {detectionTitle(detection)}
              </span>
              {hasSecrets && (
                <AlertTriangle className="h-3 w-3 shrink-0 text-[var(--color-warning)]" />
              )}
            </span>
            <span className="block truncate font-[family-name:var(--font-mono)] text-[10px] text-[var(--nm-ink50)]">
              {meta}
            </span>
          </span>
          <span className="shrink-0 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.10em] text-[var(--nm-ink30)]">
            {detection.confidence}
          </span>
          <ChevronRight
            className={cn(
              'h-3.5 w-3.5 shrink-0 text-[var(--nm-ink30)] transition-transform',
              open && 'rotate-90',
            )}
          />
        </button>
      </div>

      {open && (
        <div
          className={cn(
            'mt-0.5 rounded-[var(--radius-sm)] bg-[var(--nm-row-hover)] px-3 py-3',
            indented ? 'ml-6' : 'ml-0',
          )}
        >
          {!scan || scan.status === 'loading' ? (
            <div className="flex items-center gap-2 py-2 text-[11px] text-[var(--nm-ink50)]">
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              {t('layout.importAgent.detecting')}
            </div>
          ) : scan.status === 'error' ? (
            <div className="flex items-start gap-2 text-[11px] text-[var(--color-error)]">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              <span className="min-w-0 flex-1 break-all">{scan.error}</span>
              <Button variant="ghost" size="sm" onClick={onRetryScan}>
                {t('layout.importAgent.retry')}
              </Button>
            </div>
          ) : (
            <RowDetail
              rowKey={rowKey}
              scan={scan.data}
              name={name ?? scan.data.agent.name}
              selectedSessions={selectedSessions}
              onRename={onRename}
              onToggleSession={onToggleSession}
            />
          )}
        </div>
      )}
    </div>
  );
}

function RowDetail({
  rowKey,
  scan,
  name,
  selectedSessions,
  onRename,
  onToggleSession,
}: {
  rowKey: string;
  scan: StandardizedAgentImport;
  name: string;
  selectedSessions?: Set<string>;
  onRename: (v: string) => void;
  onToggleSession: (id: string) => void;
}) {
  const { t } = useTranslation();
  const chosen = selectedSessions ?? new Set(scan.sessions.map((s) => s.session_id));
  const secretCount = scan.mcp_servers.filter((m) => m.secret_fields.length > 0).length;

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <label
          htmlFor={`import-name-${rowKey}`}
          className="shrink-0 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.10em] text-[var(--nm-ink50)]"
        >
          {t('layout.importAgent.nameLabel')}
        </label>
        <Input
          id={`import-name-${rowKey}`}
          value={name}
          onChange={(e) => onRename(e.target.value)}
          placeholder={t('layout.importAgent.unnamed')}
          className="flex-1 text-xs"
        />
      </div>

      <div className="grid grid-cols-3 gap-2">
        <StatTile icon={<Puzzle className="h-3.5 w-3.5" />} n={scan.skills.length} label={t('layout.importAgent.skills')} />
        <StatTile icon={<Brain className="h-3.5 w-3.5" />} n={scan.memory.length} label={t('layout.importAgent.memories')} />
        <StatTile icon={<Plug className="h-3.5 w-3.5" />} n={scan.mcp_servers.length} label={t('layout.importAgent.mcpServers')} />
      </div>

      {scan.sessions.length > 0 && (
        <div>
          <div className="font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.12em] text-[var(--nm-ink50)]">
            {t('layout.importAgent.sessionsLabel', {
              selected: chosen.size,
              total: scan.sessions.length,
            })}
          </div>
          {/* Sessions are one level below the "Sessions · n/m" label they belong
              to, so their checkboxes are inset from it (Owner 2026-08-27) —
              flush-left they read as siblings of the label rather than its
              contents. */}
          <ul className="mt-1 max-h-32 space-y-0.5 overflow-y-auto pl-4">
            {scan.sessions.map((s) => (
              <li key={s.session_id} className="flex items-center gap-2 py-0.5">
                <Checkbox
                  checked={chosen.has(s.session_id)}
                  onChange={() => onToggleSession(s.session_id)}
                  ariaLabel={s.title || t('layout.importAgent.untitledSession')}
                />
                <span className="min-w-0 flex-1 truncate text-[11px] text-[var(--nm-ink70)]">
                  {s.title || t('layout.importAgent.untitledSession')}
                </span>
                <span className="shrink-0 font-[family-name:var(--font-mono)] text-[10px] text-[var(--nm-ink50)]">
                  {t('layout.importAgent.turnCount', { count: s.turns.length })}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {secretCount > 0 && (
        <div className="flex items-start gap-2 rounded-[var(--radius-sm)] border border-[var(--color-warning)] bg-[var(--color-warning)]/5 px-2.5 py-2 text-[11px] leading-relaxed text-[var(--color-warning)]">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span>{t('layout.importAgent.secretWarning', { count: secretCount })}</span>
        </div>
      )}

      {scan.custom.credential_keys.length > 0 && (
        <p className="text-[11px] text-[var(--nm-ink50)]">
          {t('layout.importAgent.credentialNote', { keys: scan.custom.credential_keys.join(', ') })}
        </p>
      )}
      {scan.custom.unmapped_files.length > 0 && (
        <p className="text-[11px] text-[var(--nm-ink50)]">
          {t('layout.importAgent.unmappedNote', { count: scan.custom.unmapped_files.length })}
        </p>
      )}
    </div>
  );
}

function StatTile({ icon, n, label }: { icon: React.ReactNode; n: number; label: string }) {
  return (
    <div className="flex items-center gap-2 rounded-[var(--radius-sm)] border border-[var(--nm-hairline)] bg-[var(--nm-card)] px-2 py-1.5 text-[var(--nm-ink50)]">
      {icon}
      <span className="font-[family-name:var(--font-mono)] text-xs tabular-nums text-[var(--nm-ink)]">
        {n}
      </span>
      <span className="truncate font-[family-name:var(--font-mono)] text-[9px] uppercase tracking-[0.10em]">
        {label}
      </span>
    </div>
  );
}

// ── running / done phase ───────────────────────────────────────────────────

function BatchPhase({
  phase,
  rows,
  batch,
  stopping,
  onRetry,
}: {
  phase: ImportPhase;
  rows: ImportQueueProgress[];
  batch: ReturnType<typeof summarizeBatch>;
  stopping: boolean;
  onRetry: (key: string) => void;
}) {
  const { t } = useTranslation();
  const settled = batch.imported + batch.failed;

  return (
    <div>
      {phase === 'running' ? (
        <>
          <p className="text-[13px] leading-relaxed text-[var(--nm-ink70)]">
            {stopping
              ? t('layout.importAgent.stoppingHint')
              : t('layout.importAgent.runningHint')}
          </p>
          <div className="mt-3 h-0.5 overflow-hidden rounded-[var(--radius-xs)] bg-[var(--nm-hairline)]">
            <div
              className="h-full bg-[var(--nm-ink)] transition-[width] duration-300"
              style={{ width: `${rows.length ? (settled / rows.length) * 100 : 0}%` }}
            />
          </div>
        </>
      ) : (
        <>
          <div className="flex items-center gap-2 rounded-[var(--radius-md)] border border-[var(--nm-hairline)] bg-[var(--nm-paper-warm)] px-3 py-2.5">
            <span className="font-[family-name:var(--font-display)] text-base font-bold leading-none tabular-nums text-[var(--nm-ink)]">
              {batch.imported}
            </span>
            <span className="text-xs text-[var(--nm-ink70)]">
              {t('layout.importAgent.importedCount', { count: batch.imported })}
            </span>
            {batch.failed > 0 && (
              <>
                <span className="text-xs text-[var(--nm-ink30)]">·</span>
                <span className="text-xs text-[var(--color-error)]">
                  {t('layout.importAgent.failedCount', { count: batch.failed })}
                </span>
              </>
            )}
            {batch.skipped > 0 && (
              <>
                <span className="text-xs text-[var(--nm-ink30)]">·</span>
                <span className="text-xs text-[var(--nm-ink50)]">
                  {t('layout.importAgent.skippedCount', { count: batch.skipped })}
                </span>
              </>
            )}
          </div>

          {batch.imported > 0 && (
            <div className="mt-3 grid grid-cols-3 gap-2">
              <StatTile
                icon={<Brain className="h-3.5 w-3.5" />}
                n={batch.narratives}
                label={t('layout.importAgent.narrativesCreated')}
              />
              <StatTile
                icon={<Brain className="h-3.5 w-3.5" />}
                n={batch.memoryTurns}
                label={t('layout.importAgent.turnsRetained')}
              />
              <StatTile
                icon={<Puzzle className="h-3.5 w-3.5" />}
                n={batch.skills}
                label={t('layout.importAgent.skills')}
              />
            </div>
          )}
        </>
      )}

      <div className="mt-4">
        {rows.map((row) => (
          <ProgressRow key={row.key} row={row} onRetry={() => onRetry(row.key)} />
        ))}
      </div>

      {phase === 'done' && batch.summariesDegraded > 0 && (
        <p className="mt-3 text-[11px] leading-relaxed text-[var(--nm-ink50)]">
          {t('layout.importAgent.hurriedNote', { count: batch.summariesDegraded })}
        </p>
      )}

      {phase === 'done' && <BatchNotes batch={batch} />}
    </div>
  );
}

function ProgressRow({ row, onRetry }: { row: ImportQueueProgress; onRetry: () => void }) {
  const { t } = useTranslation();
  const result = row.result;
  const meta =
    row.status === 'done' && result
      ? t('layout.importAgent.doneMeta', {
          narratives: result.narratives_created.length,
          turns: result.memory_turns_retained,
        })
      : row.status === 'failed'
        ? row.error
        : null;

  return (
    <div className="flex items-center gap-2 border-b border-[var(--nm-hairline)] py-2">
      <StatusIcon status={row.status} />
      <div className="min-w-0 flex-1">
        <div className="truncate text-[13px] font-medium text-[var(--nm-ink)]">{row.label}</div>
        {meta && (
          <div
            className={cn(
              'truncate font-[family-name:var(--font-mono)] text-[10px]',
              row.status === 'failed' ? 'text-[var(--color-error)]' : 'text-[var(--nm-ink50)]',
            )}
          >
            {meta}
          </div>
        )}
      </div>
      {row.status === 'failed' ? (
        <Button variant="ghost" size="sm" onClick={onRetry}>
          {t('layout.importAgent.retry')}
        </Button>
      ) : (
        <span className="shrink-0 rounded-[var(--radius-xs)] border border-[var(--nm-hairline)] px-1.5 py-0.5 font-[family-name:var(--font-mono)] text-[9px] uppercase tracking-[0.10em] text-[var(--nm-ink50)]">
          {t(`layout.importAgent.status.${row.status}`)}
        </span>
      )}
    </div>
  );
}

function StatusIcon({ status }: { status: ImportQueueProgress['status'] }) {
  if (status === 'done') {
    return <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-[var(--color-success)]" />;
  }
  if (status === 'failed') {
    return <XCircle className="h-3.5 w-3.5 shrink-0 text-[var(--color-error)]" />;
  }
  if (status === 'skipped') {
    return <CircleSlash className="h-3.5 w-3.5 shrink-0 text-[var(--nm-ink30)]" />;
  }
  if (status === 'queued') {
    return <ChevronRight className="h-3.5 w-3.5 shrink-0 text-[var(--nm-ink30)]" />;
  }
  return <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-[var(--color-info)]" />;
}

/** Post-import notes worth surfacing once for the whole batch (skills with no
 *  match, stdio MCP servers not wired, applier warnings). */
function BatchNotes({ batch }: { batch: ReturnType<typeof summarizeBatch> }) {
  const { t } = useTranslation();
  const unmatched = batch.results.flatMap((r) => r.skills_unmatched);
  const stdio = batch.results.flatMap((r) => r.mcp_stdio_skipped);
  const warnings = batch.results.flatMap((r) => r.warnings);
  if (unmatched.length === 0 && stdio.length === 0 && warnings.length === 0) return null;

  return (
    <div className="mt-3 space-y-1">
      {unmatched.length > 0 && (
        <p className="text-[11px] text-[var(--nm-ink50)]">
          {t('layout.importAgent.skillsUnmatchedNote', { names: unmatched.join(', ') })}
        </p>
      )}
      {stdio.length > 0 && (
        <p className="text-[11px] text-[var(--nm-ink50)]">
          {t('layout.importAgent.stdioSkippedNote', { names: stdio.join(', ') })}
        </p>
      )}
      {warnings.map((w, i) => (
        <div
          key={i}
          className="flex items-start gap-2 text-[11px] text-[var(--color-warning)]"
        >
          <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
          <span>{w}</span>
        </div>
      ))}
    </div>
  );
}
