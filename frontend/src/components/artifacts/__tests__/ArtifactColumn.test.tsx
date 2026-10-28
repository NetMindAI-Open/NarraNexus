/**
 * @file_name: ArtifactColumn.test.tsx
 * @description: Locks the display-layer half of the 0802 blank-column fix
 * (review #290-③). The store guarantees `activeArtifactId` is always a
 * visible tab, but ArtifactColumn's `effectiveActiveId` fallback and the
 * chart pool's `display` binding are what actually paint. This test feeds
 * a DELIBERATELY BROKEN store state (active pointing at a minimized tab) —
 * the state the fix promises can no longer occur — to prove the display
 * layer is a real backstop, not a second source of truth that goes blank.
 *
 * Uses the REAL store (setState injection) so the coupling is exercised;
 * only the heavy children (ArtifactRenderer → echarts, tab strip, download
 * menu, zoom modal) and i18n are stubbed.
 */
import { describe, expect, test, vi, beforeEach } from 'vitest';
import { render } from '@testing-library/react';

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k: string) => k }) }));
vi.mock('../ArtifactRenderer', () => ({
  default: ({ artifact }: { artifact: { artifact_id: string } }) => (
    <div data-artifact={artifact.artifact_id} />
  ),
}));
vi.mock('../ArtifactTabStrip', () => ({ default: () => null }));
vi.mock('../ArtifactDownloadMenu', () => ({ default: () => null }));
vi.mock('../ArtifactZoomModal', () => ({ default: () => null }));

import ArtifactColumn from '../ArtifactColumn';
import { useArtifactStore } from '@/stores';
import type { Artifact } from '@/types/artifact';

const CHART = 'application/vnd.echarts+json';
function chart(id: string): Artifact {
  return { artifact_id: id, agent_id: 'agent_x', kind: CHART, title: id } as unknown as Artifact;
}

beforeEach(() => {
  useArtifactStore.setState({
    artifacts: [chart('c1'), chart('c2')],
    activeArtifactId: 'c1', // broken invariant: c1 is minimized (below)
    minimizedTabIds: new Set(['c1']),
    chartLruOrder: ['c1', 'c2'],
  });
});

describe('ArtifactColumn chart pool visibility', () => {
  test('with active pointing at a minimized chart, exactly one pane is visible and it is the first visible chart', () => {
    const { container } = render(<ArtifactColumn agentId="agent_x" />);
    const panes = Array.from(
      container.querySelectorAll<HTMLDivElement>('.absolute.inset-0'),
    );
    const visible = panes.filter((p) => p.style.display !== 'none');
    expect(visible).toHaveLength(1);
    expect(visible[0].querySelector('[data-artifact]')?.getAttribute('data-artifact')).toBe('c2');
  });
});
