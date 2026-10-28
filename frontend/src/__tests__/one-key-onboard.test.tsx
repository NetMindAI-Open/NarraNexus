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

// The provider picker is a list of radio ROWS (2026-08-27 first-run design),
// not a <select>: click the row, read `aria-checked` to assert the selection.
const PROVIDER_ROW_LABEL: Record<string, RegExp> = {
  anthropic: /Claude Code SDK/,
  openai: /Codex SDK/,
  netmind: /NetMind\.AI Power/,
  yunwu: /^Yunwu/,
  openrouter: /^OpenRouter/,
};

function selectProvider(value: string) {
  fireEvent.click(screen.getByRole('radio', { name: PROVIDER_ROW_LABEL[value] }));
}

function selectedProvider(): string | undefined {
  return Object.keys(PROVIDER_ROW_LABEL).find(
    (id) =>
      screen
        .getByRole('radio', { name: PROVIDER_ROW_LABEL[id] })
        .getAttribute('aria-checked') === 'true',
  );
}

describe('OneKeyOnboard', () => {
  test('defaults to NetMind (recommended) and sends it explicitly', async () => {
    // Owner decision 2026-08-28: NetMind is the recommended one-key
    // source and must be the default selection, listed first.
    onboardMock.mockResolvedValue({ success: true });
    const onComplete = vi.fn();
    render(<OneKeyOnboard onComplete={onComplete} />);
    expect(selectedProvider()).toBe('netmind');
    typeKey('nm-key-123');

    fireEvent.click(screen.getByText('Start using NarraNexus'));
    await waitFor(() => expect(onComplete).toHaveBeenCalled());
    expect(onboardMock).toHaveBeenCalledWith('nm-key-123', 'netmind');
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

  test('OpenAI-looking key under the NetMind default shows the switch nudge', () => {
    // Real NetMind keys are 32-hex with no sk- prefix, so an sk-* paste
    // while the default is selected is the most likely first-run mistake.
    render(<OneKeyOnboard onComplete={() => {}} />);
    typeKey('sk-proj-abc123');

    const nudge = screen.getByText(/Looks like an? OpenAI key/);
    fireEvent.click(nudge);
    expect(selectedProvider()).toBe('openai');
  });

  test('OpenAI-looking key under Anthropic shows the switch nudge', () => {
    render(<OneKeyOnboard onComplete={() => {}} />);
    selectProvider('anthropic');
    typeKey('sk-proj-abc123');

    const nudge = screen.getByText(/Looks like an? OpenAI key/);
    fireEvent.click(nudge);
    expect(selectedProvider()).toBe('openai');
  });

  test('sk-ant- key under NetMind shows the Claude switch nudge', () => {
    render(<OneKeyOnboard onComplete={() => {}} />);
    selectProvider('netmind');
    typeKey('sk-ant-abc123');

    const nudge = screen.getByText(/Looks like a Claude key/);
    fireEvent.click(nudge);
    expect(selectedProvider()).toBe('anthropic');
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

  test('register-only response (activated=false) shows "Key saved", not model claims', async () => {
    // Cloud non-staff: the backend stores the key but keeps slots on NetMind.
    onboardMock.mockResolvedValue({
      success: true,
      activated: false,
      agent_model: 'claude-opus-4-8',
      helper_model: 'claude-haiku-4-5',
      agent_framework: 'claude_code',
    });
    render(<OneKeyOnboard onComplete={() => {}} />);
    typeKey('sk-ant-abc');

    fireEvent.click(screen.getByText('Start using NarraNexus'));
    await waitFor(() =>
      expect(screen.getByRole('status').textContent).toContain('Key saved'),
    );
    const status = screen.getByRole('status').textContent ?? '';
    expect(status).toContain('NetMind');
    expect(status).toContain('local desktop version');
    expect(status).not.toContain('claude-opus-4-8');
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
    // NetMind is the default selection now.
    const link = screen.getByText(/Get your NetMind API key/).closest('a');
    expect(link?.getAttribute('href')).toContain('netmind.ai');

    selectProvider('anthropic');
    const link2 = screen.getByText(/Get your Anthropic API key/).closest('a');
    expect(link2?.getAttribute('href')).toContain('console.anthropic.com');
  });
});
