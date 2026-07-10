/**
 * OneKeyOnboard component tests (provider-picker version).
 *
 * Covers: default provider (NetMind recommended), explicit provider
 * selection in the onboard call, the Claude-key mismatch nudge, error
 * surfacing, and the disabled state.
 */
import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

const onboardMock = vi.fn();
vi.mock('@/lib/api', () => ({
  api: {
    onboard: (...args: unknown[]) => onboardMock(...args),
  },
}));

import { OneKeyOnboard } from '@/components/settings/OneKeyOnboard';

beforeEach(() => {
  onboardMock.mockReset();
});

function typeKey(value: string) {
  const input = screen.getByPlaceholderText('Paste your API key');
  fireEvent.change(input, { target: { value } });
  return input;
}

function selectProvider(value: string) {
  fireEvent.change(screen.getByRole('combobox'), { target: { value } });
}

describe('OneKeyOnboard', () => {
  test('defaults to Anthropic (official) and sends it explicitly', async () => {
    onboardMock.mockResolvedValue({ success: true });
    const onComplete = vi.fn();
    render(<OneKeyOnboard onComplete={onComplete} />);
    typeKey('sk-ant-abc123');

    fireEvent.click(screen.getByText('Start using NarraNexus'));
    await waitFor(() => expect(onComplete).toHaveBeenCalled());
    expect(onboardMock).toHaveBeenCalledWith('sk-ant-abc123', 'anthropic');
  });

  test('selecting NetMind sends provider_type netmind', async () => {
    onboardMock.mockResolvedValue({ success: true });
    const onComplete = vi.fn();
    render(<OneKeyOnboard onComplete={onComplete} />);
    selectProvider('netmind');
    typeKey('nm-key-123');

    fireEvent.click(screen.getByText('Start using NarraNexus'));
    await waitFor(() => expect(onComplete).toHaveBeenCalled());
    expect(onboardMock).toHaveBeenCalledWith('nm-key-123', 'netmind');
  });

  test('OpenAI-looking key under Anthropic shows the switch nudge', () => {
    render(<OneKeyOnboard onComplete={() => {}} />);
    typeKey('sk-proj-abc123');

    const nudge = screen.getByText(/Looks like an? OpenAI key/);
    fireEvent.click(nudge);
    expect(
      (screen.getByRole('combobox') as HTMLSelectElement).value,
    ).toBe('openai');
  });

  test('sk-ant- key under NetMind shows the Claude switch nudge', () => {
    render(<OneKeyOnboard onComplete={() => {}} />);
    selectProvider('netmind');
    typeKey('sk-ant-abc123');

    const nudge = screen.getByText(/Looks like a Claude key/);
    fireEvent.click(nudge);
    expect(
      (screen.getByRole('combobox') as HTMLSelectElement).value,
    ).toBe('anthropic');
  });

  test('shows a success summary with the wired models', async () => {
    onboardMock.mockResolvedValue({
      success: true,
      agent_model: 'claude-opus-4-8',
      helper_model: 'claude-haiku-4-5',
      agent_framework: 'claude_code',
    });
    render(<OneKeyOnboard onComplete={() => {}} />);
    typeKey('sk-ant-abc');

    fireEvent.click(screen.getByText('Start using NarraNexus'));
    await waitFor(() =>
      expect(screen.getByRole('status').textContent).toContain('You’re all set'),
    );
    expect(screen.getByRole('status').textContent).toContain('claude-opus-4-8');
    expect(screen.getByRole('status').textContent).toContain('claude-haiku-4-5');
    expect(screen.getByRole('status').textContent).toContain('Claude Code');
  });

  test('surfaces backend error detail and does not complete', async () => {
    onboardMock.mockRejectedValue(
      new Error('API error 400: A anthropic provider already exists'),
    );
    const onComplete = vi.fn();
    render(<OneKeyOnboard onComplete={onComplete} />);
    typeKey('sk-ant-x');

    fireEvent.click(screen.getByText('Start using NarraNexus'));
    await waitFor(() =>
      expect(screen.getByRole('alert').textContent).toContain('already exists'),
    );
    expect(onComplete).not.toHaveBeenCalled();
  });

  test('needs_replace prompts to confirm, then re-sends with replace=true', async () => {
    onboardMock
      .mockResolvedValueOnce({
        success: false,
        needs_replace: true,
        provider_type: 'netmind',
        existing_masked: '***fXQA',
      })
      .mockResolvedValueOnce({ success: true, agent_model: 'claude-opus-4-8' });
    const onComplete = vi.fn();
    render(<OneKeyOnboard onComplete={onComplete} />);
    selectProvider('netmind');
    typeKey('nm-key-new');

    fireEvent.click(screen.getByText('Start using NarraNexus'));

    // Confirm dialog names the existing masked key.
    await screen.findByText(/\*\*\*fXQA/);
    fireEvent.click(screen.getByText('Replace key'));

    await waitFor(() => expect(onComplete).toHaveBeenCalled());
    expect(onboardMock).toHaveBeenNthCalledWith(1, 'nm-key-new', 'netmind');
    expect(onboardMock).toHaveBeenNthCalledWith(2, 'nm-key-new', 'netmind', true);
  });

  test('declining the replace prompt does not re-send or complete', async () => {
    onboardMock.mockResolvedValueOnce({
      success: false,
      needs_replace: true,
      provider_type: 'netmind',
      existing_masked: '***fXQA',
    });
    const onComplete = vi.fn();
    render(<OneKeyOnboard onComplete={onComplete} />);
    selectProvider('netmind');
    typeKey('nm-key-new');

    fireEvent.click(screen.getByText('Start using NarraNexus'));
    fireEvent.click(await screen.findByText('Keep current'));

    await waitFor(() => expect(onboardMock).toHaveBeenCalledTimes(1));
    expect(onComplete).not.toHaveBeenCalled();
  });

  test('start button is disabled while the key field is empty', () => {
    render(<OneKeyOnboard onComplete={() => {}} />);
    const button = screen.getByText('Start using NarraNexus').closest('button');
    expect(button?.disabled).toBe(true);
  });

  test('shows a Get Key link for the selected provider', () => {
    render(<OneKeyOnboard onComplete={() => {}} />);
    const link = screen.getByText(/Get your Anthropic API key/).closest('a');
    expect(link?.getAttribute('href')).toContain('console.anthropic.com');

    selectProvider('netmind');
    const link2 = screen.getByText(/Get your NetMind API key/).closest('a');
    expect(link2?.getAttribute('href')).toContain('netmind.ai');
  });
});
