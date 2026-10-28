/**
 * @file_name: migrationLabels.ts
 * @author: NetMind.AI
 * @date: 2026-07-30
 * @description: Shared display labels + icons for migration source frameworks,
 * so the import modal, the guided flow, and any future entry point don't each
 * keep their own copy (they drifted before — a new framework had to be added in
 * N places).
 */

import type { ComponentType } from 'react';
import { FolderSearch } from 'lucide-react';
import {
  ClaudeCodeFrameworkIcon,
  CodexFrameworkIcon,
  HermesFrameworkIcon,
  OpenClawFrameworkIcon,
  UnknownFrameworkIcon,
  type FrameworkIconProps,
} from '@/components/icons/FrameworkBrandIcons';
import type { MigrationFramework } from '@/types';

export const FRAMEWORK_LABELS: Record<string, string> = {
  claude_code: 'Claude Code',
  hermes: 'Hermes',
  openclaw: 'OpenClaw',
  codex: 'Codex',
  custom: 'Custom',
};

export const frameworkLabel = (fw: string): string => FRAMEWORK_LABELS[fw] ?? fw;

/** ComponentType, not a plain function type: lucide's icons are forwardRef
 *  exotic components, our brand marks are plain functions — both must fit. */
export type FrameworkIconComponent = ComponentType<FrameworkIconProps>;

/** One mark per source framework — the vendor's real brand mark where one
 *  exists (see [[FrameworkBrandIcons]] for provenance), so a list of 29 imported
 *  agents reads as "your tools" instead of 29 identical robots. `custom` is a
 *  user-typed folder, not a product, so it keeps a lucide glyph. Kept beside the
 *  labels: adding a framework stays a one-line change here instead of an
 *  icon-less row somewhere downstream. */
export const FRAMEWORK_ICONS: Record<string, FrameworkIconComponent> = {
  claude_code: ClaudeCodeFrameworkIcon,
  hermes: HermesFrameworkIcon,
  openclaw: OpenClawFrameworkIcon,
  codex: CodexFrameworkIcon,
  custom: FolderSearch,
};

export const frameworkIcon = (fw: string): FrameworkIconComponent =>
  FRAMEWORK_ICONS[fw] ?? UnknownFrameworkIcon;

/** Stable display order for the framework list — the two coding agents first
 *  (they carry the most sources on a developer machine), then the rest
 *  (Owner 2026-08-27). */
export const FRAMEWORK_ORDER: MigrationFramework[] = [
  'claude_code',
  'codex',
  'openclaw',
  'hermes',
  'custom',
];
