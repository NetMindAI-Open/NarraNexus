/**
 * @file_name: MarketplaceBrowser.test.tsx
 * @description: Marketplace browser — renders search results with flags,
 * installs via the mutation, disables the button for installed skills, and
 * shows the unavailable state on error (desktop-offline path).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import type { MarketplaceSkillItem } from '@/types/skills';

let items: MarketplaceSkillItem[];
let searchError: Error | null;
const installMutate = vi.fn();

vi.mock('@/hooks/useSkillMarketplace', () => ({
  useMarketplaceSearch: () => ({
    data: searchError ? undefined : { items, total: items.length, page: 1, limit: 30 },
    isLoading: false,
    error: searchError,
  }),
  useMarketplaceDetail: () => ({ data: undefined, isLoading: true }),
  useMarketplaceInstall: () => ({
    mutate: installMutate,
    isPending: false,
    variables: undefined,
  }),
}));

import { MarketplaceBrowser } from '../marketplace/MarketplaceBrowser';

const ITEM: MarketplaceSkillItem = {
  skill_id: 'web-search-fallback',
  version: '1.2.0',
  name: 'Web Search Fallback',
  description: 'Search the web when the model cannot',
  capabilities: ['search:web'],
  tags: ['search'],
  downloads: 42,
  scan_status: 'passed',
  status: 'published',
  installed: false,
};

beforeEach(() => {
  items = [{ ...ITEM }];
  searchError = null;
  installMutate.mockReset();
});

describe('MarketplaceBrowser', () => {
  it('renders result cards and installs on click', () => {
    render(<MarketplaceBrowser onClose={vi.fn()} />);

    expect(screen.getByText('Web Search Fallback')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /install/i }));
    expect(installMutate).toHaveBeenCalledWith(
      { skillId: 'web-search-fallback' },
      expect.anything()
    );
  });

  it('disables the button for already-installed skills', () => {
    items = [{ ...ITEM, installed: true }];
    render(<MarketplaceBrowser onClose={vi.fn()} />);

    const button = screen.getByRole('button', { name: /installed/i });
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(installMutate).not.toHaveBeenCalled();
  });

  it('shows the update-available badge', () => {
    items = [{ ...ITEM, installed: true, update_available: true }];
    render(<MarketplaceBrowser onClose={vi.fn()} />);
    expect(screen.getByText(/update/i)).toBeInTheDocument();
  });

  it('shows the unavailable state when the registry is unreachable', () => {
    searchError = new Error('network down');
    render(<MarketplaceBrowser onClose={vi.fn()} />);
    expect(screen.getByText(/unavailable/i)).toBeInTheDocument();
  });

  it('opens the detail sheet when a card is clicked', () => {
    render(<MarketplaceBrowser onClose={vi.fn()} />);
    fireEvent.click(screen.getByTestId('marketplace-card-web-search-fallback'));
    expect(screen.getByTestId('skill-detail-sheet')).toBeInTheDocument();
  });
});
