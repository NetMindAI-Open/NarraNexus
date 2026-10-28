/**
 * @file_name: channelActiveToggle.test.tsx
 * @author: Bin Liang
 * @date: 2026-09-10
 * @description: The shared enable/disable toggle shows the platform's disable reason only while inactive AND a reason exists.
 */
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { ChannelActiveToggle } from '../ChannelActiveToggle';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string, opts?: { reason?: string }) => (opts?.reason ? `${key}:${opts.reason}` : key) }),
}));

const noop = async () => {};

describe('ChannelActiveToggle', () => {
  it('renders the reason under the status while inactive', () => {
    render(<ChannelActiveToggle active={false} reason="TelegramSDKError: getUpdates failed (HTTP 401: Unauthorized)" onToggle={noop} />);
    expect(screen.getByTestId('channel-disabled-reason').textContent).toBe(
      'channelActiveToggle.disabledReason:TelegramSDKError: getUpdates failed (HTTP 401: Unauthorized)',
    );
  });

  it('hides the reason while active', () => {
    render(<ChannelActiveToggle active reason="stale" onToggle={noop} />);
    expect(screen.queryByTestId('channel-disabled-reason')).toBeNull();
  });

  it('hides the reason when inactive by hand (empty reason)', () => {
    render(<ChannelActiveToggle active={false} reason="" onToggle={noop} />);
    expect(screen.queryByTestId('channel-disabled-reason')).toBeNull();
  });
});
