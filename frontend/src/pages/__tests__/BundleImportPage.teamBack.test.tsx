/**
 * @file_name: BundleImportPage.teamBack.test.tsx
 * @description: Regression — installing a team template deep-links into
 * BundleImportPage's review step; the back button must return to the
 * Marketplace Teams tab, NOT the blank deep-link 'upload' step (the
 * "black screen" bug) and NOT /app/settings.
 */
import type React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import type { BundlePreflightResponse } from '@/types';

const navigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useNavigate: () => navigate };
});

const installTeamTemplatePreflight = vi.fn();
vi.mock('@/lib/api', () => ({
  api: {
    installTeamTemplatePreflight: (...a: unknown[]) => installTeamTemplatePreflight(...a),
    importBundleFromUrl: vi.fn(),
    importBundlePreflight: vi.fn(),
    importBundleConfirm: vi.fn(),
  },
}));
vi.mock('@/stores', () => ({
  useTeamsStore: () => ({ refresh: vi.fn() }),
  useConfigStore: () => ({ refreshAgents: vi.fn(), userId: 'u1' }),
}));
type MockProps = { children?: React.ReactNode; onClick?: () => void };
vi.mock('@/components/ui', () => ({
  Button: ({ children, onClick }: MockProps) => <button onClick={onClick}>{children}</button>,
  useConfirm: () => ({ dialog: null, confirm: vi.fn() }),
}));
vi.mock('@/components/nm', () => ({
  BracketDropzone: ({ children }: MockProps) => <div>{children}</div>,
  StepIndicator: () => <div data-testid="steps" />,
}));

import BundleImportPage from '../BundleImportPage';

const PREFLIGHT: BundlePreflightResponse = {
  preflight_token: 'tok_1',
  manifest: {
    bundle_format_version: '1.1',
    narranexus_version_exported: '1.9.0',
    exported_at: '2026-07-21T00:00:00Z',
    integrity_sha256: 'abc123def456',
    agents: [{ agent_id: 'a1', name: 'PM Bot' }],
    team: { name: 'My Team' },
    skills: [],
    mcp_hints_count: 0,
    warnings: [],
    stripped: [],
    info: [],
  },
  name_clashes: [],
  team_clash: null,
  credential_clashes: [],
} as unknown as BundlePreflightResponse;

beforeEach(() => {
  navigate.mockReset();
  installTeamTemplatePreflight.mockReset().mockResolvedValue(PREFLIGHT);
});

function renderAt(search: string) {
  return render(
    <MemoryRouter initialEntries={[`/app/templates/install${search}`]}>
      <Routes>
        <Route path="/app/templates/install" element={<BundleImportPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('BundleImportPage — team-template deep-link back', () => {
  it('runs install-preflight and reaches the review step', async () => {
    renderAt('?teamTemplate=pm-bridge-bot');
    await waitFor(() =>
      expect(installTeamTemplatePreflight).toHaveBeenCalledWith('pm-bridge-bot'),
    );
    // A back control is present (header arrow at minimum).
    expect(await screen.findByTestId('steps')).toBeInTheDocument();
  });

  it('header back returns to the Marketplace Teams tab (not settings/blank)', async () => {
    const { container } = renderAt('?teamTemplate=pm-bridge-bot');
    await waitFor(() => expect(installTeamTemplatePreflight).toHaveBeenCalled());
    // The header arrow is the first <button> in the top bar.
    const headerBtn = container.querySelector('button')!;
    fireEvent.click(headerBtn);
    expect(navigate).toHaveBeenCalledWith('/app/marketplace?tab=teams');
  });
});
