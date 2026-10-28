/**
 * The download menu is the portal-shaped popover (trigger and panel in
 * different subtrees), dismissed through useDismissOnOutside's extraRefs.
 * Pins the shape: opening works (the trigger's own pointerdown must not
 * self-dismiss), inside interactions keep it open, outside ones close it.
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

vi.mock('@/components/ui', () => ({
  useNotice: () => ({
    notifyPending: vi.fn(),
    notifyDone: vi.fn(),
    notifyError: vi.fn(),
    dialog: null,
  }),
}));
vi.mock('@/hooks/useArtifactRawUrl', () => ({
  useArtifactRawUrl: () => ({ url: 'blob:fake' }),
}));

import ArtifactDownloadMenu from '../ArtifactDownloadMenu';
import type { Artifact } from '@/types/artifact';

const artifact = {
  artifact_id: 'art1',
  agent_id: 'a1',
  title: 'Report',
  kind: 'text/markdown',
  updated_at: '2026-08-19T00:00:00Z',
} as Artifact;

function openMenu() {
  render(
    <div>
      <ArtifactDownloadMenu artifact={artifact} />
      <button data-testid="elsewhere">elsewhere</button>
    </div>,
  );
  const trigger = screen.getByRole('button', { name: /download/i });
  fireEvent.pointerDown(trigger);
  fireEvent.click(trigger);
  return screen.getByRole('menu');
}

describe('ArtifactDownloadMenu dismissal', () => {
  it('the trigger click opens it — its own pointerdown must not self-dismiss', () => {
    expect(openMenu()).toBeInTheDocument();
  });

  it('pointerdown inside the portal panel keeps it open', () => {
    const menu = openMenu();
    fireEvent.pointerDown(menu);
    expect(screen.getByRole('menu')).toBeInTheDocument();
  });

  it('pointerdown anywhere else closes it', () => {
    openMenu();
    fireEvent.pointerDown(screen.getByTestId('elsewhere'));
    expect(screen.queryByRole('menu')).toBeNull();
  });
});
