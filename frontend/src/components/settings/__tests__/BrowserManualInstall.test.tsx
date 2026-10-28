/**
 * @file_name: BrowserManualInstall.test.tsx
 * @description: Manual installation copies installer-owned text without inventing commands.
 */
import { afterEach, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import BrowserManualInstall from '../BrowserManualInstall';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

test('copies the installer command verbatim, including quoting and line breaks', async () => {
  const writeText = vi.fn().mockResolvedValue(undefined);
  vi.stubGlobal('navigator', { clipboard: { writeText } });
  const command = '# installer-provided test fixture\n"/path with spaces/installer" --runtime "browser"';
  render(<BrowserManualInstall command={command} />);
  expect(screen.getByRole('textbox')).toHaveValue(command);
  fireEvent.click(screen.getByRole('button', { name: /copy/i }));
  expect(await screen.findByRole('status')).toHaveTextContent(/copied/i);
  expect(writeText).toHaveBeenCalledWith(command);
});

test('a rejected clipboard operation stays recoverable and does not claim success', async () => {
  const writeText = vi.fn().mockRejectedValueOnce(new Error('clipboard unavailable')).mockResolvedValueOnce(undefined);
  vi.stubGlobal('navigator', { clipboard: { writeText } });
  render(<BrowserManualInstall command="installer-owned test fixture" />);
  fireEvent.click(screen.getByRole('button', { name: /copy/i }));
  expect(await screen.findByRole('alert')).toHaveTextContent(/copy/i);
  expect(screen.queryByRole('status')).not.toBeInTheDocument();
  expect(screen.getByRole('textbox')).toHaveFocus();
  fireEvent.click(screen.getByRole('button', { name: /copy/i }));
  expect(await screen.findByRole('status')).toHaveTextContent(/copied/i);
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});

test('does not invent a fallback when the installer supplies no command', () => {
  render(<BrowserManualInstall command={null} />);
  expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /copy/i })).not.toBeInTheDocument();
});

test('changing the command clears the previous copied confirmation', async () => {
  vi.stubGlobal('navigator', { clipboard: { writeText: vi.fn().mockResolvedValue(undefined) } });
  const view = render(<BrowserManualInstall command="first installer fixture" />);
  fireEvent.click(screen.getByRole('button', { name: /copy/i }));
  await screen.findByRole('status');
  view.rerender(<BrowserManualInstall command="second installer fixture" />);
  expect(screen.queryByRole('status')).not.toBeInTheDocument();
  expect(screen.getByRole('textbox')).toHaveValue('second installer fixture');
});

test('status and cancellation copy the corresponding installer-provided command', async () => {
  const writeText = vi.fn().mockResolvedValue(undefined);
  vi.stubGlobal('navigator', { clipboard: { writeText } });
  render(<BrowserManualInstall command="install fixture" statusCommand="status fixture" cancelCommand="cancel fixture"
    shell="powershell" root={'C:\\Browser Runtime'} />);
  expect(screen.getByText(/PowerShell/)).toBeInTheDocument();
  expect(screen.getByText('C:\\Browser Runtime')).toBeInTheDocument();
  fireEvent.change(screen.getByRole('combobox', { name: /command/i }), { target: { value: 'status' } });
  expect(screen.getByRole('textbox')).toHaveValue('status fixture');
  fireEvent.click(screen.getByRole('button', { name: /copy/i }));
  await screen.findByRole('status');
  expect(writeText).toHaveBeenLastCalledWith('status fixture');
  fireEvent.change(screen.getByRole('combobox', { name: /command/i }), { target: { value: 'cancel' } });
  expect(screen.queryByRole('status')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: /copy/i }));
  await screen.findByRole('status');
  expect(writeText).toHaveBeenLastCalledWith('cancel fixture');
});
