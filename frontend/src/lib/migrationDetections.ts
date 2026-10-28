/**
 * @file_name: migrationDetections.ts
 * @author: NetMind.AI
 * @date: 2026-08-27
 * @description: Pure helpers that turn a raw `/api/migrate/detect` response into
 * what the one-page import list needs: a stable row key, the row title/meta
 * strings, the pre-checked default selection, and the per-framework grouping.
 *
 * Extracted from ImportAgentModal so the selection rules (which rows come
 * pre-checked, how rows are ordered) are unit-testable without rendering a
 * modal — they are product decisions, not layout details.
 */

import { FRAMEWORK_ORDER, frameworkLabel } from './migrationLabels';
import type { FrameworkDetection, MigrationFramework } from '@/types';

/** Stable identity for a row. `/detect` never returns the same
 *  (framework, path) pair twice, so this is a safe React key + selection id. */
export const detectionKey = (d: FrameworkDetection): string => `${d.framework}::${d.path}`;

/** The detector's "no per-project config, here is the shared one" fallback row.
 *  Real enough to import, too vague to pre-check. */
export const isSharedConfig = (d: FrameworkDetection): boolean =>
  d.signals.includes('global-shared-config');

/** `sessions:N` signal → N (0 when the detector reported no sessions). */
export function sessionCount(d: FrameworkDetection): number {
  const signal = d.signals.find((s) => s.startsWith('sessions:'));
  if (!signal) return 0;
  const n = Number.parseInt(signal.slice('sessions:'.length), 10);
  return Number.isFinite(n) && n > 0 ? n : 0;
}

/** Row title. Claude Code enumerates one detection PER PROJECT, all with the
 *  same framework label, so the project folder name is the only thing that
 *  distinguishes them; every other framework has a single row where its own
 *  name reads better. */
export function detectionTitle(d: FrameworkDetection): string {
  if (d.framework === 'claude_code' && d.signals.includes('project')) {
    return d.path.replace(/\/+$/, '').split('/').pop() || d.path;
  }
  return frameworkLabel(d.framework);
}

/** Pre-checked rows (Owner decision 2026-08-27): confidence `high` AND at least
 *  one session — i.e. the ones that obviously carry history worth keeping. The
 *  long tail (low confidence, empty folders, the shared-config fallback) is left
 *  to the user, because every checked row costs one LLM summarization pass.
 *
 *  Exception: when the whole scan found exactly ONE row there is nothing to
 *  choose between, so it comes checked. */
export function defaultSelection(detections: FrameworkDetection[]): Set<string> {
  const picked = detections.filter(
    (d) => d.confidence === 'high' && sessionCount(d) > 0 && !isSharedConfig(d),
  );
  if (picked.length === 0 && detections.length === 1) {
    return new Set([detectionKey(detections[0])]);
  }
  return new Set(picked.map(detectionKey));
}

export interface DetectionGroup {
  framework: MigrationFramework;
  detections: FrameworkDetection[];
}

/** Group rows by framework in FRAMEWORK_ORDER, richest source first inside each
 *  group (most sessions, then alphabetical). With 26 Claude Code projects the
 *  interesting ones have to be at the top — detector order is filesystem order,
 *  which is meaningless to the user. A framework the frontend doesn't know yet
 *  still gets its own group instead of vanishing. */
export function groupDetections(detections: FrameworkDetection[]): DetectionGroup[] {
  const byFramework = new Map<MigrationFramework, FrameworkDetection[]>();
  for (const d of detections) {
    const arr = byFramework.get(d.framework) ?? [];
    arr.push(d);
    byFramework.set(d.framework, arr);
  }
  const known = FRAMEWORK_ORDER.filter((fw) => byFramework.has(fw));
  const unknown = Array.from(byFramework.keys()).filter((fw) => !FRAMEWORK_ORDER.includes(fw));
  return [...known, ...unknown].map((framework) => ({
    framework,
    detections: [...byFramework.get(framework)!].sort(
      (a, b) =>
        sessionCount(b) - sessionCount(a) || detectionTitle(a).localeCompare(detectionTitle(b)),
    ),
  }));
}

/** Grouped rows flattened back into one ordered array — the order the import
 *  queue runs in, so progress rows appear in the same order the user saw. */
export const flattenGroups = (groups: DetectionGroup[]): FrameworkDetection[] =>
  groups.flatMap((g) => g.detections);
