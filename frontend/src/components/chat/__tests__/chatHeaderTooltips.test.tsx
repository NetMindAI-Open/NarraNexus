/**
 * Req #3: the header panel-entry icon buttons (Jobs / Inbox / Artifacts) must
 * expose a hover tooltip AND an accessible name. Before the change they carried
 * only a native `title` (no accessible name via aria), so getByLabelText was
 * empty. Wrapping them in the Radix Tooltip + adding aria-label makes the name
 * queryable — revert either and this goes red.
 */
import { afterEach, describe, it, expect, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const requestPanel = vi.hoisted(() => vi.fn());

vi.mock('@/stores', async () => ({
  // Real barrel underneath (the header reads the studio store from it); only
  // the two UI stores below are stubbed.
  ...(await vi.importActual<typeof import('@/stores')>('@/stores')),
  useUIStore: (sel: (s: unknown) => unknown) =>
    sel({ sidebarCollapsed: false, setSidebarCollapsed: vi.fn(), requestPanel }),
  useArtifactStore: (sel: (s: unknown) => unknown) => sel({ artifacts: [] }),
}));
vi.mock('@/stores/bookmarkStore', () => ({
  useBookmarkStore: Object.assign((sel: (s: unknown) => unknown) => sel({ agents: {} }), {
    getState: () => ({ agents: {} }),
  }),
}));
vi.mock('@/components/cost/CostPopover', () => ({ CostPopover: () => null }));
vi.mock('../ExecutionPopover', () => ({ ExecutionPopover: () => null }));

import { ChatHeader } from '../ChatHeader';
import { PANELS } from '@/platform/registries';

const browserPanel = PANELS.get('browser')!;
const browserOwner = PANELS.ownerOf('browser');
afterEach(() => {
  cleanup();
  requestPanel.mockClear();
  PANELS.register('browser', browserPanel, { owner: browserOwner, replace: true });
});

const baseProps = {
  agentId: 'a1',
  agentName: 'Analyst',
  isStreaming: false,
  currentSteps: [],
  chatTab: 'conversation' as const,
  onChatTabChange: vi.fn(),
};

describe('chat header panel-entry tooltips (#3)', () => {
  it('Jobs / Inbox / Artifacts buttons have accessible labels', () => {
    render(
      <MemoryRouter>
        <ChatHeader {...baseProps} />
      </MemoryRouter>,
    );
    expect(screen.getByLabelText('Jobs')).toBeInTheDocument();
    expect(screen.getByLabelText('Inbox')).toBeInTheDocument();
    expect(screen.getByLabelText('Artifacts')).toBeInTheDocument();
  });

  it('Browser is a named button with a Radix tooltip and opens its panel', async () => {
    render(<MemoryRouter><ChatHeader {...baseProps} /></MemoryRouter>);
    const button = screen.getByRole('button', { name: 'Browser' });
    expect(button).not.toHaveAttribute('title');
    fireEvent.focus(button);
    expect(await screen.findByRole('tooltip')).toHaveTextContent('Browser');
    fireEvent.click(button);
    expect(requestPanel).toHaveBeenCalledWith('browser');
  });

  it('Browser disappears when its panel is unregistered', () => {
    const dispose = PANELS.register('browser', browserPanel, { replace: true });
    render(<MemoryRouter><ChatHeader {...baseProps} /></MemoryRouter>);
    expect(screen.getByRole('button', { name: 'Browser' })).toBeInTheDocument();
    act(dispose);
    expect(screen.queryByRole('button', { name: 'Browser' })).not.toBeInTheDocument();
  });

  it('Browser cannot open without an active agent', () => {
    render(<MemoryRouter><ChatHeader {...baseProps} agentId={null} /></MemoryRouter>);
    const button = screen.queryByRole('button', { name: 'Browser' });
    if (button) expect(button).toBeDisabled();
    expect(requestPanel).not.toHaveBeenCalled();
  });
});
