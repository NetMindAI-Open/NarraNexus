/**
 * @file_name: commandPalette.test.tsx
 * @author: Bin Liang
 * @date: 2026-09-07
 * @description: The ⌘K palette offers dev's studio-gated panel list AND batch 2's plugin commands — both halves of a hand-merged file, each pinned so a replay cannot silently drop one.
 */
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { CommandPalette } from '@/components/layout/CommandPalette';
import { COMMANDS } from '@/platform/registries';
import { useConfigStore, useStudioStore } from '@/stores';

const AGENT = 'agent_cmdk_1';
const OWNER = { owner: 'acme.test' };
const disposers: Array<() => void> = [];

function mount() {
  return render(
    <MemoryRouter>
      <CommandPalette open onClose={() => {}} />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  useConfigStore.setState({ agentId: AGENT, agents: [{ agent_id: AGENT, name: 'Cmdk Agent' }] } as never);
  useStudioStore.setState({ open: {}, visited: {} } as never);
});
afterEach(() => {
  cleanup();
  while (disposers.length) disposers.pop()!();
});

describe('CommandPalette', () => {
  it("offers the agent's panels but not Builder while the studio is neither open nor resumable", () => {
    mount();
    expect(screen.getByRole('button', { name: /Awareness/ })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Builder/ })).toBeNull();
  });

  it('offers Builder once the studio is open on this agent (the one shared visibility rule)', () => {
    useStudioStore.setState({ open: { [AGENT]: true }, visited: {} } as never);
    mount();
    expect(screen.getByRole('button', { name: /Builder/ })).toBeInTheDocument();
  });

  it('lists a plugin command from the COMMANDS registry and runs it on Enter', () => {
    const run = vi.fn();
    disposers.push(COMMANDS.register('acme.test.hello', { label: 'Acme hello', run }, OWNER));
    mount();
    const input = screen.getByPlaceholderText(/./);
    fireEvent.change(input, { target: { value: 'Acme hello' } });
    expect(screen.getByRole('button', { name: /Acme hello/ })).toBeInTheDocument();
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(run).toHaveBeenCalledTimes(1);
  });

  it('hides a plugin command whose `visible` predicate says no', () => {
    disposers.push(COMMANDS.register('acme.test.hidden', { label: 'Acme hidden', run: () => {}, visible: () => false }, OWNER));
    mount();
    expect(screen.queryByRole('button', { name: /Acme hidden/ })).toBeNull();
  });
});
