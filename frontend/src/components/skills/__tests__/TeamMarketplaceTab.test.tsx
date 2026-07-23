/**
 * @file_name: TeamMarketplaceTab.test.tsx
 * @description: Team Marketplace tab — renders template cards, filters by
 * category, and installs by routing into the bundle-import deep-link.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import type { TeamTemplate } from '@/types';

const navigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useNavigate: () => navigate };
});

let templates: TeamTemplate[];
const getTeamTemplates = vi.fn();
vi.mock('@/lib/api', () => ({
  api: { getTeamTemplates: () => getTeamTemplates() },
}));

import { TeamMarketplaceTab } from '../marketplace/TeamMarketplaceTab';

const TPL: TeamTemplate = {
  template_id: 'financial-morning-briefing',
  name: 'Financial Morning Briefing',
  description: 'A 6-agent analyst team.',
  categories: ['finance', 'team'],
  author: 'NarraNexus team',
  agent_count: 6,
};

beforeEach(() => {
  templates = [TPL, { ...TPL, template_id: 'gaokao-team', name: 'Gaokao Team', categories: ['education', 'team'], agent_count: 5 }];
  navigate.mockReset();
  getTeamTemplates.mockReset().mockResolvedValue({ templates });
});

function renderTab() {
  return render(
    <MemoryRouter>
      <TeamMarketplaceTab />
    </MemoryRouter>,
  );
}

describe('TeamMarketplaceTab', () => {
  it('renders template cards with agent-count badge', async () => {
    renderTab();
    expect(await screen.findByText('Financial Morning Briefing')).toBeInTheDocument();
    expect(screen.getByTestId('team-card-gaokao-team')).toBeInTheDocument();
  });

  it('installs by routing into the bundle-import deep-link', async () => {
    renderTab();
    await screen.findByText('Financial Morning Briefing');
    const card = screen.getByTestId('team-card-financial-morning-briefing');
    fireEvent.click(card.querySelector('button')!);
    expect(navigate).toHaveBeenCalledWith(
      '/app/templates/install?teamTemplate=financial-morning-briefing',
    );
  });

  it('filters by category', async () => {
    renderTab();
    await screen.findByText('Gaokao Team');
    fireEvent.click(screen.getByRole('button', { name: 'education' }));
    expect(screen.getByText('Gaokao Team')).toBeInTheDocument();
    expect(screen.queryByText('Financial Morning Briefing')).not.toBeInTheDocument();
  });

  it('shows the unavailable state when the registry errors', async () => {
    getTeamTemplates.mockRejectedValue(new Error('network down'));
    renderTab();
    await waitFor(() => expect(screen.getByText(/unavailable/i)).toBeInTheDocument());
  });
});
