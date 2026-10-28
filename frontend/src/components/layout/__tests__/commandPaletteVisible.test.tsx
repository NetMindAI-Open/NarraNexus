/**
 * M-3: `CommandDef.visible` (renamed from `when`, matching `SidebarItemDef.visible` — not
 * `registries/when.ts`'s `WhenClause` string grammar, a different validated-at-registration
 * mechanism that happened to share the name `when`) is an arbitrary predicate called directly
 * at render. A throwing predicate must not crash the whole palette — it should be reported and
 * the offending command hidden, same as any other plugin-boundary failure.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

vi.mock('@/stores', async () => ({
  ...(await vi.importActual<typeof import('@/stores')>('@/stores')),
  useConfigStore: (sel: (s: unknown) => unknown) => sel({ agents: [], setAgentId: vi.fn(), agentId: null }),
  useUIStore: (sel: (s: unknown) => unknown) => sel({ requestPanel: vi.fn() }),
}));

import { CommandPalette } from '../CommandPalette';
import { COMMANDS } from '@/platform/registries';
import { onUiError, resetErrorSink } from '@/platform/errorSink';

afterEach(() => {
  resetErrorSink();
  for (const e of COMMANDS.list().filter((x) => x.owner === 'acme.cmdtest')) COMMANDS.register(e.id, e.value, { owner: e.owner, replace: true })();
});

function renderPalette() {
  render(
    <MemoryRouter>
      <CommandPalette open onClose={vi.fn()} />
    </MemoryRouter>,
  );
}

describe('CommandPalette — plugin command visible() predicate (M-3)', () => {
  it('hides a command whose visible() returns false', () => {
    COMMANDS.register('acme.cmdtest.hidden', { label: 'Hidden Command', run: () => {}, visible: () => false }, { owner: 'acme.cmdtest' });
    renderPalette();
    expect(screen.queryByText('Hidden Command')).toBeNull();
  });

  it('a throwing visible() is caught, reported, and the command is hidden rather than crashing the palette', () => {
    const seen = vi.fn();
    const unsubscribe = onUiError(seen);
    COMMANDS.register(
      'acme.cmdtest.throws',
      {
        label: 'Buggy Command',
        run: () => {},
        visible: () => {
          throw new Error('boom');
        },
      },
      { owner: 'acme.cmdtest' },
    );
    expect(() => renderPalette()).not.toThrow();
    expect(screen.queryByText('Buggy Command')).toBeNull();
    expect(seen).toHaveBeenCalledTimes(1);
    expect(seen.mock.calls[0][0].source).toBe('acme.cmdtest');
    unsubscribe();
  });
});
