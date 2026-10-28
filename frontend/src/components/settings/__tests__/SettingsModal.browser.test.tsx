/**
 * @file_name: SettingsModal.browser.test.tsx
 * @description: The legacy settings modal follows the browser feature's registry lifecycle.
 */
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import { PANELS, enableOwner } from '@/platform/registries';
import { disableBuiltinUi } from '@/platform/loader';
import { SettingsModal } from '../SettingsModal';

vi.mock('../BrowserSettings', () => ({ default: () => <div data-testid="browser-settings-pane" /> }));
vi.mock('../ProviderSettings', () => ({ ProviderSettings: () => <div data-testid="providers-pane" /> }));

const browserPanel = PANELS.get('browser')!;
const owner = PANELS.ownerOf('browser');

afterEach(() => {
  cleanup();
  enableOwner('builtin.browser');
  PANELS.register('browser', browserPanel, { owner, replace: true });
});

test('a disabled browser feature has no settings modal entry', () => {
  disableBuiltinUi('builtin.browser');
  render(<SettingsModal isOpen onClose={vi.fn()} />);
  expect(screen.queryByRole('button', { name: 'Browser' })).not.toBeInTheDocument();
  expect(screen.queryByTestId('browser-settings-pane')).not.toBeInTheDocument();
});

test('disabling the selected browser section removes its content and restores a usable pane', () => {
  render(<SettingsModal isOpen onClose={vi.fn()} />);
  fireEvent.click(screen.getByRole('button', { name: 'Browser' }));
  expect(screen.getByTestId('browser-settings-pane')).toBeInTheDocument();
  act(() => { disableBuiltinUi('builtin.browser'); });
  expect(screen.queryByRole('button', { name: 'Browser' })).not.toBeInTheDocument();
  expect(screen.queryByTestId('browser-settings-pane')).not.toBeInTheDocument();
  expect(screen.getByTestId('providers-pane')).toBeInTheDocument();
});
