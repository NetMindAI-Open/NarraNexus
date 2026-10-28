/**
 * Regression guard for GitHub #107/#110: `dashboard.banners.dismissAria`
 * and `dashboard.banners.dismissTitle` were referenced by this component
 * but never defined in ANY locale file (not even en), so i18next's
 * missing-key fallback rendered the raw dotted key as the dismiss
 * button's accessible name and tooltip — broken in every language,
 * not just non-English ones.
 */
import { describe, expect, test } from 'vitest';
import { render, screen } from '@testing-library/react';
import { AttentionBanners } from '../AttentionBanners';

describe('AttentionBanners dismiss control', () => {
  test('the dismiss button has a real translated name, not the raw i18n key', () => {
    render(
      <AttentionBanners
        agentId="agent_1"
        banners={[{ kind: 'job_failed', level: 'error', message: '1 failed' }]}
      />,
    );

    const dismiss = screen.getByRole('button', { name: 'Dismiss' });
    expect(dismiss).toHaveAttribute('title', 'Dismiss');
    // The literal, unresolved key must never leak into the DOM.
    expect(screen.queryByText('dashboard.banners.dismissAria')).toBeNull();
  });
});
