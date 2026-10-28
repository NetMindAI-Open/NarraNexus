/**
 * @file_name: BrowserLookOutput.test.tsx
 * @date: 2026-09-24
 * @description: The browser_look output row: a readable one-line summary, an Open-browser action, and the raw output one click away.
 */
import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import { BrowserLookOutput } from '../BrowserLookOutput';
import { acceptsBrowserLookOutput } from '@/lib/browserLook';
import { useUIStore } from '@/stores/uiStore';

const META = {
  outcome: 'OK',
  observation_id: 'view_1',
  url: 'https://example.com/chart',
  title: 'Quarterly chart',
  viewport: { width: 1280, height: 800 },
  region: { x: 100, y: 50, width: 400, height: 300 },
  image: { width: 1200, height: 900, mime_type: 'image/png' },
};

afterEach(() => useUIStore.setState({ pendingPanel: null }));

describe('BrowserLookOutput', () => {
  it('summarises what the agent looked at instead of dumping JSON', () => {
    render(<BrowserLookOutput toolName="mcp__browser_module__browser_look" output={JSON.stringify(META)} />);
    expect(screen.getByText('Looked at the page')).toBeInTheDocument();
    expect(screen.getByText('1200×900')).toBeInTheDocument();
    expect(screen.getByText('area 400×300')).toBeInTheDocument();
    expect(screen.getByText('Quarterly chart')).toBeInTheDocument();
    expect(screen.queryByText(/observation_id/)).toBeNull();
  });

  it('opens the browser panel (open, not toggle)', () => {
    render(<BrowserLookOutput toolName="browser_look" output={JSON.stringify(META)} />);
    fireEvent.click(screen.getByRole('button', { name: 'Open browser' }));
    expect(useUIStore.getState().pendingPanel).toBe('browser');
    expect(useUIStore.getState().pendingPanelMode).toBe('open');
  });

  it('keeps the raw output reachable (iron rule #16)', () => {
    render(<BrowserLookOutput toolName="browser_look" output={JSON.stringify(META)} />);
    fireEvent.click(screen.getByRole('button', { name: /Looked at the page/ }));
    expect(screen.getByText(/"observation_id":"view_1"/)).toBeInTheDocument();
  });

  it('shows why a look failed', () => {
    const failed = JSON.stringify({ outcome: 'ERROR', message: 'Element is hidden' });
    render(<BrowserLookOutput toolName="browser_look" output={failed} />);
    expect(screen.getByText("Couldn't look at the page")).toBeInTheDocument();
    expect(screen.getByText('Element is hidden')).toBeInTheDocument();
  });

  it('declines outputs it cannot read, leaving the generic row', () => {
    expect(acceptsBrowserLookOutput(JSON.stringify(META))).toBe(true);
    expect(acceptsBrowserLookOutput('Browser operation failed')).toBe(false);
  });
});
