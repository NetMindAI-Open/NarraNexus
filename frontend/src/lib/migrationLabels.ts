/**
 * @file_name: migrationLabels.ts
 * @author: NetMind.AI
 * @date: 2026-07-30
 * @description: Shared display labels for migration source frameworks, so the
 * import modal, the guided flow, and any future entry point don't each keep
 * their own copy (they drifted before — a new framework had to be added in N
 * places).
 */

import type { MigrationFramework } from '@/types';

export const FRAMEWORK_LABELS: Record<string, string> = {
  claude_code: 'Claude Code',
  hermes: 'Hermes',
  openclaw: 'OpenClaw',
  codex: 'Codex',
  custom: 'Custom',
};

export const frameworkLabel = (fw: string): string => FRAMEWORK_LABELS[fw] ?? fw;

/** Stable display order for the framework list. */
export const FRAMEWORK_ORDER: MigrationFramework[] = [
  'claude_code',
  'openclaw',
  'codex',
  'hermes',
  'custom',
];
