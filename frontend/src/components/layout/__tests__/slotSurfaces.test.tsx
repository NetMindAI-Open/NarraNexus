/**
 * @file_name: slotSurfaces.test.tsx
 * @author: Bin Liang
 * @date: 2026-09-04
 * @description: The top bar and the composer mount their slot points: a registered item/extension shows up, a `when`-gated one stays out.
 */
import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';

import { TopBar } from '../TopBar';
import { Composer } from '@/components/chat/Composer';
import { COMPOSER_EXTENSIONS, TOP_BAR_ITEMS } from '@/platform/registries';

vi.mock('../CommandPalette', () => ({ CommandPalette: () => null }));

const disposers: (() => void)[] = [];
afterEach(() => disposers.splice(0).forEach((d) => d()));

describe('slot surfaces', () => {
  it('top bar items', () => {
    disposers.push(TOP_BAR_ITEMS.register('acme.clock', { component: () => <span>12:00</span> }, { owner: 'acme.t' }));
    disposers.push(TOP_BAR_ITEMS.register('acme.hidden', { component: () => <span>never</span>, when: 'setting:noSuchSetting' }, { owner: 'acme.t' }));
    render(<MemoryRouter><TopBar /></MemoryRouter>);
    expect(screen.getByText('12:00')).toBeInTheDocument();
    expect(screen.queryByText('never')).toBeNull();
  });

  it('composer extensions receive the agent id', () => {
    disposers.push(COMPOSER_EXTENSIONS.register('acme.strip', { component: ({ agentId }) => <button type="button">strip for {agentId}</button> }, { owner: 'acme.t' }));
    render(
      <Composer agentId="a1" disabled={false} placeholder="" onSubmit={() => {}} onDragOver={() => {}} onDragLeave={() => {}} onDrop={() => {}} onPaste={() => {}} />,
    );
    expect(screen.getByText('strip for a1')).toBeInTheDocument();
  });
});
