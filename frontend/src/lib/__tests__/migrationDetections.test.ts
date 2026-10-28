/**
 * Unit tests for the one-page import list's selection + ordering rules
 * (see lib/migrationDetections.ts). These are product decisions — which rows
 * arrive pre-checked, which order 26 Claude Code projects appear in — so they
 * are pinned here rather than asserted through a rendered modal.
 */
import { describe, it, expect } from 'vitest';
import {
  defaultSelection,
  detectionKey,
  detectionTitle,
  flattenGroups,
  groupDetections,
  isSharedConfig,
  sessionCount,
} from '../migrationDetections';
import type { FrameworkDetection, MigrationConfidence, MigrationFramework } from '@/types';

const det = (
  framework: MigrationFramework,
  path: string,
  confidence: MigrationConfidence = 'high',
  signals: string[] = [],
): FrameworkDetection => ({ framework, path, confidence, signals });

const project = (path: string, sessions: number, confidence: MigrationConfidence = 'high') =>
  det('claude_code', path, confidence, ['project', `sessions:${sessions}`]);

describe('sessionCount', () => {
  it('reads the sessions:N signal', () => {
    expect(sessionCount(project('/a', 12))).toBe(12);
  });

  it('is 0 when the signal is missing or unparseable', () => {
    expect(sessionCount(det('codex', '/c'))).toBe(0);
    expect(sessionCount(det('codex', '/c', 'high', ['sessions:abc']))).toBe(0);
  });
});

describe('detectionTitle', () => {
  it('uses the project folder name for Claude Code project rows', () => {
    expect(detectionTitle(project('/Users/x/work/NarraNexus-1/', 3))).toBe('NarraNexus-1');
  });

  it('uses the framework label for everything else', () => {
    expect(detectionTitle(det('openclaw', '/Users/x/.claude/openclaw'))).toBe('OpenClaw');
  });
});

describe('defaultSelection', () => {
  it('pre-checks high-confidence rows that carry sessions', () => {
    const rows = [project('/a', 12), project('/b', 3)];
    expect(defaultSelection(rows)).toEqual(new Set(rows.map(detectionKey)));
  });

  it('leaves out empty, low-confidence and shared-config rows', () => {
    const empty = project('/empty', 0);
    const low = project('/low', 5, 'low');
    const medium = project('/medium', 5, 'medium');
    const shared = det('claude_code', '/shared', 'high', ['global-shared-config', 'sessions:9']);
    const good = project('/good', 1);
    expect(defaultSelection([empty, low, medium, shared, good])).toEqual(
      new Set([detectionKey(good)]),
    );
    expect(isSharedConfig(shared)).toBe(true);
  });

  it('checks a lone row even when it would not otherwise qualify', () => {
    const only = det('claude_code', '/only', 'low', ['global-shared-config']);
    expect(defaultSelection([only])).toEqual(new Set([detectionKey(only)]));
  });

  it('checks nothing when several rows all fail the bar', () => {
    expect(defaultSelection([project('/a', 0), project('/b', 0, 'low')]).size).toBe(0);
  });
});

describe('groupDetections', () => {
  it('groups by framework in FRAMEWORK_ORDER, richest source first', () => {
    const groups = groupDetections([
      det('hermes', '/h'),
      project('/small', 1),
      det('openclaw', '/o'),
      project('/big', 20),
      project('/medium', 5),
    ]);
    expect(groups.map((g) => g.framework)).toEqual(['claude_code', 'openclaw', 'hermes']);
    expect(groups[0].detections.map((d) => detectionTitle(d))).toEqual([
      'big',
      'medium',
      'small',
    ]);
  });

  it('keeps a framework the frontend does not know yet', () => {
    const unknown = { ...det('claude_code', '/x'), framework: 'future_tool' as MigrationFramework };
    const groups = groupDetections([unknown]);
    expect(groups).toHaveLength(1);
    expect(groups[0].framework).toBe('future_tool');
  });

  it('flattens back to the exact order the rows render in', () => {
    const rows = [project('/a', 1), det('codex', '/c'), project('/b', 9)];
    expect(flattenGroups(groupDetections(rows)).map((d) => d.path)).toEqual(['/b', '/a', '/c']);
  });
});
