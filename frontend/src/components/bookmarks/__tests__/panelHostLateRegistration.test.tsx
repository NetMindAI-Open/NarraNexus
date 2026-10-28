/**
 * @file_name: panelHostLateRegistration.test.tsx
 * @author: Bin Liang
 * @date: 2026-09-07
 * @description: A drawer open on a tab whose panel registers later re-renders in place — BookmarkPanelHost's PANELS subscription is load-bearing.
 */
import { act, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import { PANELS } from '@/platform/registries';
import { BookmarkPanelHost } from '../BookmarkPanelHost';

afterEach(() => PANELS.removeOwner('acme.plugin'));

describe('BookmarkPanelHost and late panel registration', () => {
  it('renders the panel once its plugin registers it, without a remount', () => {
    render(<BookmarkPanelHost tab="acme-late" agentId="agent_1" />);
    expect(screen.queryByText('acme late panel')).toBeNull();
    act(() => {
      PANELS.register('acme-late', { component: () => <p>acme late panel</p> }, { owner: 'acme.plugin' });
    });
    expect(screen.getByText('acme late panel')).toBeInTheDocument();
  });
});
