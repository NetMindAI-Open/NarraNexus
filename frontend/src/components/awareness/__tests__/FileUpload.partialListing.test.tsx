/**
 * @file_name: FileUpload.partialListing.test.tsx
 * @author: Bin Liang
 * @date: 2026-10-28
 * @description: The backend caps the workspace tree (node count / depth) and
 * says so with `truncated`. The panel must turn that into a visible
 * "partial listing" note so files past the cap read as "not shown", never as
 * deleted — and must not show it for a complete listing or a failed load.
 */
import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  listFiles: vi.fn(),
  workspaceFileRawUrl: vi.fn(() => '/raw'),
}));
vi.mock('@/lib/api', () => ({ api: mocks }));
vi.mock('@/stores', () => ({
  useConfigStore: () => ({ agentId: 'agent_a1', userId: 'user_u1' }),
  useArtifactStore: { getState: () => ({ loadPinned: vi.fn() }) },
}));

import { FileUpload } from '../FileUpload';

const NOTE = /Partial listing/;
const tree = [
  { name: 'notes.md', path: 'notes.md', is_dir: false, size: 10, modified_at: '0', children: null },
];

beforeEach(() => {
  mocks.listFiles.mockReset();
});

describe('FileUpload partial listing note', () => {
  it('shows the note when the backend truncated the tree', async () => {
    mocks.listFiles.mockResolvedValue({ success: true, tree, workspace_path: '/w', truncated: true });
    render(<FileUpload />);
    expect(await screen.findByText('notes.md')).toBeInTheDocument();
    expect(screen.getByText(NOTE)).toBeInTheDocument();
  });

  it('shows no note for a complete listing', async () => {
    mocks.listFiles.mockResolvedValue({ success: true, tree, workspace_path: '/w', truncated: false });
    render(<FileUpload />);
    expect(await screen.findByText('notes.md')).toBeInTheDocument();
    expect(screen.queryByText(NOTE)).toBeNull();
  });

  it('shows no note when the listing failed', async () => {
    mocks.listFiles.mockResolvedValue({ success: false, tree: [], workspace_path: '', truncated: false, error: 'boom' });
    render(<FileUpload />);
    await waitFor(() => expect(screen.getByText('boom')).toBeInTheDocument());
    expect(screen.queryByText(NOTE)).toBeNull();
  });
});
