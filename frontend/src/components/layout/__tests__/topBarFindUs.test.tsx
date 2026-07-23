/**
 * Tests for the TopBar "Find Us" community entry.
 *
 * Ops requirement (2026-07): a prominent community-entry button in the
 * global top strip, placed to the LEFT of the LOCAL/CLOUD runtime label,
 * linking to the marketing site's social hub. Shortens the
 * "sign up → join the community" path.
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import { TopBar } from '../TopBar';

// The palette pulls in the full agent/page index; it is irrelevant to the
// strip layout under test, so stub it out.
vi.mock('../CommandPalette', () => ({
  CommandPalette: () => null,
}));

const renderTopBar = () =>
  render(
    <MemoryRouter>
      <TopBar />
    </MemoryRouter>
  );

describe('TopBar Find Us entry', () => {
  it('renders a Find Us link to the marketing connect page', () => {
    renderTopBar();
    const link = screen.getByRole('link', { name: /find us/i });
    expect(link).toHaveAttribute('href', 'https://www.narra.nexus/connect');
  });

  it('opens in a new tab without leaking the opener', () => {
    renderTopBar();
    const link = screen.getByRole('link', { name: /find us/i });
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', expect.stringContaining('noopener'));
    expect(link).toHaveAttribute('rel', expect.stringContaining('noreferrer'));
  });

  it('sits to the left of the runtime (LOCAL/CLOUD) label', () => {
    renderTopBar();
    const link = screen.getByRole('link', { name: /find us/i });
    // runtimeStore mode is unset in tests → the label renders the "—" dash.
    const runtime = screen.getByTitle(/runtime/i);
    expect(
      // DOCUMENT_POSITION_FOLLOWING = the runtime label comes AFTER the link.
      link.compareDocumentPosition(runtime) & Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy();
  });
});
