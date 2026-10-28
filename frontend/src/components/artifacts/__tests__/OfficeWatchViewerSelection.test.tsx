/**
 * @file_name: OfficeWatchViewerSelection.test.tsx
 * @description: The office edit bar's selection channel must only accept
 * messages from ITS OWN iframe (review #334 C2): an html artifact's script
 * runs in a sibling allow-scripts iframe and can postMessage a forged
 * officewatch-selection — without the source gate the user's next Delete
 * lands on attacker-chosen slides (confused deputy, no undo).
 */

import { render, waitFor } from '@testing-library/react';
import { act } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { Artifact } from '@/types/artifact';

vi.mock('@/services/officeWatchApi', () => ({
  officeWatchApi: {
    open: vi.fn(async () => 'http://api/office-watch-proxy/tok/26320/'),
    version: vi.fn(async () => ({ mtime: 1, size: 1, lock: false })),
    sendBatch: vi.fn(),
    getElement: vi.fn(),
    commitEdit: vi.fn(),
    uploadAsset: vi.fn(),
  },
}));
vi.mock('@/lib/tauri', () => ({ isTauri: () => false }));

import OfficeWatchViewer from '../OfficeWatchViewer';

const ART: Artifact = {
  artifact_id: 'art_deck01',
  agent_id: 'agent_x',
  user_id: 'user_y',
  session_id: 's',
  title: 'deck',
  kind: 'application/vnd.officecli-live',
  description: null,
  pinned: false,
  team_id: null,
  file_path: 'ws/deck.pptx',
  size_bytes: 10,
  created_at: '2026-08-19T00:00:00Z',
  updated_at: '2026-08-19T00:00:00Z',
};

async function postSelection(source: Window | null, paths: string[]) {
  await act(async () => {
    window.dispatchEvent(
      new MessageEvent('message', {
        data: { type: 'officewatch-selection', body: JSON.stringify({ paths }) },
        source,
      }),
    );
  });
}

beforeEach(() => vi.clearAllMocks());

describe('OfficeWatchViewer selection source gate', () => {
  it('accepts selection from its own iframe and shows the edit bar', async () => {
    const { container, getByText } = render(<OfficeWatchViewer artifact={ART} />);
    const iframe = await waitFor(() => {
      const el = container.querySelector('iframe');
      if (!el) throw new Error('iframe not mounted');
      return el;
    });
    await postSelection(iframe.contentWindow, ['/slide[1]/sp[1]']);
    expect(getByText(/1 selected/i)).toBeTruthy();
  });

  it('ignores a forged selection from any other window', async () => {
    const { container, queryByText } = render(<OfficeWatchViewer artifact={ART} />);
    await waitFor(() => expect(container.querySelector('iframe')).not.toBeNull());
    await postSelection(window, ['/slide[1]', '/slide[2]']);
    expect(queryByText(/2 selected/i)).toBeNull();
  });
});
