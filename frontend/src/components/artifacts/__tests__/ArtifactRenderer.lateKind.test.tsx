/**
 * @file_name: ArtifactRenderer.lateKind.test.tsx
 * @author: Bin Liang
 * @date: 2026-09-07
 * @description: An artifact opened before its kind is registered re-renders in place when a plugin registers the kind — the ARTIFACT_KINDS subscription in the renderer is load-bearing.
 */
import { act, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import { ARTIFACT_KINDS, type KindDescriptor } from '@/platform/registries';
import type { Artifact } from '@/types/artifact';
import ArtifactRenderer from '../ArtifactRenderer';

const KIND = 'application/x-acme-late';
const artifact = { kind: KIND, artifact_id: 'art_1', agent_id: 'agent_1', title: 'late', file_path: 'late.acme' } as unknown as Artifact;

afterEach(() => ARTIFACT_KINDS.removeOwner('acme.plugin'));

describe('ArtifactRenderer and late kind registration', () => {
  it('switches from the unsupported-kind fallback to the plugin renderer without a remount', () => {
    render(<ArtifactRenderer artifact={artifact} />);
    expect(screen.queryByText('acme late renderer')).toBeNull();
    const descriptor: KindDescriptor = { renderer: () => <p>acme late renderer</p>, editSurface: 'none', saveMode: null, selectionToAI: false, preview: 'none' };
    // No second render(): the already-mounted tree must pick the kind up through its subscription.
    act(() => {
      ARTIFACT_KINDS.register(KIND, descriptor, { owner: 'acme.plugin' });
    });
    expect(screen.getByText('acme late renderer')).toBeInTheDocument();
  });
});
