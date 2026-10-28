/**
 * @file_name: builderConfigPanel.test.tsx
 * @description: Structure and behaviour of the creation studio's panel.
 *
 * Renders the panel DIRECTLY rather than through BookmarkPanelHost's lazy
 * wrapper: under full-suite load the lazy chunk's transform overran waitFor
 * and these tests failed intermittently on "Unable to find ... Identity".
 * The lazy ROUTING is covered separately in
 * components/bookmarks/__tests__/builderTab.test.tsx — that is one assertion,
 * not eight.
 */
import { describe, test, expect, beforeEach, vi } from 'vitest';
import { fireEvent, render, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('@/lib/api', () => ({
  api: {
    getAwareness: vi.fn().mockResolvedValue({ awareness: '' }),
    updateAgent: vi.fn().mockResolvedValue({ success: true }),
    updateAwareness: vi.fn().mockResolvedValue({ success: true }),
    installMarketplaceSkill: vi.fn().mockResolvedValue({}),
    listSkills: vi.fn().mockResolvedValue({ skills: [] }),
  },
}));

// The panel embeds the drawer's own Skills and Channels sections. Stub them:
// this file defends the STUDIO panel, and mounting those two would re-test
// them plus drag in their API surface.
vi.mock('@/components/skills', () => ({
  SkillsPanel: () => <div data-testid="skills-section" />,
}));
vi.mock('@/components/awareness', () => ({
  AwarenessPanel: () => <div data-testid="channels-section" />,
}));

import { api } from '@/lib/api';
import { useUIStore } from '@/stores/uiStore';
import { useConfigStore } from '@/stores/configStore';
import { useStudioStore, isStudioOpen, selectStudioResumable } from '@/stores/studioStore';
import { BuilderConfigPanel } from '../BuilderConfigPanel';

const AGENT = 'agent_test';

function renderPanel() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const { container } = render(
    <QueryClientProvider client={qc}>
      <BuilderConfigPanel agentId={AGENT} />
    </QueryClientProvider>,
  );
  return within(container);
}

const openStudio = (id: string) => useStudioStore.getState().openStudio(id);
const saveRecommendations = (id: string, rec: { skill_ids: string[]; channels: string[] }) =>
  useStudioStore.getState().setRecommendations(id, rec);

beforeEach(() => {
  window.sessionStorage.clear();
  useStudioStore.setState({ open: {}, visited: {}, recommendations: {}, applyError: {} });
  useUIStore.getState().clearPendingPanel();
  vi.mocked(api.updateAgent).mockClear().mockResolvedValue({ success: true } as never);
  useConfigStore.setState({ agents: [] });
  vi.mocked(api.updateAwareness).mockClear().mockResolvedValue({ success: true } as never);
  vi.mocked(api.installMarketplaceSkill).mockClear().mockResolvedValue({} as never);
});

describe('studio panel structure', () => {
  test('shows identity, awareness and the two optional sections', () => {
    const view = renderPanel();
    expect(view.getByText('Identity')).toBeInTheDocument();
    expect(view.getByText('Name')).toBeInTheDocument();
    expect(view.getByText('Awareness')).toBeInTheDocument();
    expect(view.getByText('Skills')).toBeInTheDocument();
    expect(view.getByText('Channels')).toBeInTheDocument();
    expect(view.getByTestId('skills-section')).toBeInTheDocument();
    expect(view.getByTestId('channels-section')).toBeInTheDocument();
    expect(view.getByRole('button', { name: 'Done' })).toBeInTheDocument();
  });

  test('marks Skills and Channels optional — they are genuinely skippable', () => {
    const view = renderPanel();
    expect(view.getAllByText('Optional')).toHaveLength(2);
  });

  test('drops the avatar and description the reference removed', () => {
    // No avatar affordance (the project has no agent-avatar capability) and no
    // description field (machine-facing copy — the conversation writes it and
    // Agent Profile shows it).
    const view = renderPanel();
    expect(view.queryByText('Description')).not.toBeInTheDocument();
    expect(view.queryByText(/avatar/i)).not.toBeInTheDocument();
  });
});

describe('live state from the conversation', () => {
  test('a recommendation that arrives AFTER mount shows up without any text change', async () => {
    // Regression: recommendations were read from sessionStorage at render
    // time, so a turn that only recommended a skill (no name/awareness
    // change, hence no other re-render) never surfaced in the panel.
    const view = renderPanel();
    expect(view.queryByText('From the conversation')).not.toBeInTheDocument();
    saveRecommendations(AGENT, { skill_ids: ['web-search'], channels: [] });
    await waitFor(() => {
      expect(view.getByText('From the conversation')).toBeInTheDocument();
    });
    expect(view.getByText('web-search')).toBeInTheDocument();
  });

  test('a failed model-driven write is shown as one line', async () => {
    const view = renderPanel();
    useStudioStore.getState().setApplyError(AGENT, 'Error: 422 description too long');
    await waitFor(() => {
      expect(view.getByText(/422 description too long/)).toBeInTheDocument();
    });
  });

  test('blurring an EMPTY name does not write it, and the field snaps back', async () => {
    // Same rule as the model path: empty means "not changing it" — so the
    // field must show the real name again, not stay blank as if cleared.
    useConfigStore.setState({ agents: [{ agent_id: AGENT, name: 'Kept' } as never] });
    const view = renderPanel();
    const input = view.getByPlaceholderText(/Morning Market Brief/i) as HTMLInputElement;
    expect(input.value).toBe('Kept');
    fireEvent.change(input, { target: { value: '   ' } });
    fireEvent.blur(input);
    await new Promise((r) => setTimeout(r, 0));
    expect(api.updateAgent).not.toHaveBeenCalled();
    expect(input.value).toBe('Kept');
  });

  test('a manual-edit error does not hide a later model-write error', async () => {
    vi.mocked(api.updateAgent).mockResolvedValueOnce({ success: false, message: 'manual nope' } as never);
    const view = renderPanel();
    const input = view.getByPlaceholderText(/Morning Market Brief/i);
    fireEvent.change(input, { target: { value: 'New name' } });
    fireEvent.blur(input);
    await waitFor(() => expect(view.getByText(/manual nope/)).toBeInTheDocument());
    useStudioStore.getState().setApplyError(AGENT, 'model nope');
    await waitFor(() => expect(view.getByText(/model nope/)).toBeInTheDocument());
    expect(view.getByText(/manual nope/)).toBeInTheDocument();
  });
});

describe('Done', () => {
  test('ENDS the studio and leaves the drawer to useStudioLifecycle', async () => {
    // Done must not also ask the drawer to toggle: that request raced the
    // lifecycle effect (both land in one React commit; the toggle then read
    // the already-collapsed tab as "not open" and re-opened an empty panel).
    // The drawer collapsing after Done is pinned in useStudioLifecycle.test.
    openStudio(AGENT);
    const view = renderPanel();

    fireEvent.click(view.getByRole('button', { name: 'Done' }));

    await waitFor(() => {
      expect(isStudioOpen(AGENT)).toBe(false);
    });
    // Done ENDS the studio — unlike collapsing the drawer, it is not resumable.
    expect(selectStudioResumable(AGENT)(useStudioStore.getState())).toBe(false);
    expect(useUIStore.getState().pendingPanel).toBeNull();
  });

  test('does NOT end the studio when the flush fails — the error stays visible', async () => {
    // Ending it would unmount this panel (and its error line) and collapse the
    // drawer in the same tick: a clean close over an edit that never landed,
    // with no way back in. The panel stays, Done can be pressed again.
    openStudio(AGENT);
    vi.mocked(api.updateAwareness).mockRejectedValueOnce(new Error('gateway 502'));
    const view = renderPanel();
    fireEvent.change(view.getByPlaceholderText(/What this agent does/i), {
      target: { value: 'last-minute edit' },
    });
    fireEvent.click(view.getByRole('button', { name: 'Done' }));
    await waitFor(() => expect(view.getByText(/gateway 502/)).toBeInTheDocument());
    expect(isStudioOpen(AGENT)).toBe(true);
    expect(view.getByRole('button', { name: 'Done' })).toBeEnabled();

    // second attempt succeeds → now it ends
    fireEvent.click(view.getByRole('button', { name: 'Done' }));
    await waitFor(() => expect(isStudioOpen(AGENT)).toBe(false));
  });

  test('a rejected name does not stop the instructions from being written', async () => {
    // Both fields changed, so BOTH commits take the real request path (an
    // unchanged field early-returns and would make this test vacuous).
    openStudio(AGENT);
    useConfigStore.setState({ agents: [{ agent_id: AGENT, name: 'Old', description: '' }] as never });
    vi.mocked(api.updateAgent).mockResolvedValueOnce({ success: false, message: 'name too long' } as never);
    const view = renderPanel();
    fireEvent.change(view.getByPlaceholderText(/Morning Market Brief/i), { target: { value: 'X'.repeat(200) } });
    fireEvent.change(view.getByPlaceholderText(/What this agent does/i), { target: { value: 'keep me' } });
    fireEvent.click(view.getByRole('button', { name: 'Done' }));
    // Short-circuit sentinel: the only assertion a `&&` would turn red.
    await waitFor(() => expect(api.updateAwareness).toHaveBeenCalledWith(AGENT, 'keep me'));
    // Slot sentinel: the awareness commit's own "clear" must not erase name's failure.
    await waitFor(() => expect(view.getByText(/name too long/)).toBeInTheDocument());
    expect(isStudioOpen(AGENT)).toBe(true);
  });

  test('two writers failing show two lines, not one', async () => {
    openStudio(AGENT);
    useConfigStore.setState({ agents: [{ agent_id: AGENT, name: 'Old', description: '' }] as never });
    vi.mocked(api.updateAgent).mockResolvedValueOnce({ success: false, message: 'name nope' } as never);
    vi.mocked(api.updateAwareness).mockRejectedValueOnce(new Error('awareness nope'));
    const view = renderPanel();
    fireEvent.change(view.getByPlaceholderText(/Morning Market Brief/i), { target: { value: 'New' } });
    fireEvent.change(view.getByPlaceholderText(/What this agent does/i), { target: { value: 'new text' } });
    fireEvent.click(view.getByRole('button', { name: 'Done' }));
    await waitFor(() => expect(view.getByText(/name nope/)).toBeInTheDocument());
    await waitFor(() => expect(view.getByText(/awareness nope/)).toBeInTheDocument());
    expect(isStudioOpen(AGENT)).toBe(true);
  });

  test('a stale name error clears once the field is back in sync', async () => {
    useConfigStore.setState({ agents: [{ agent_id: AGENT, name: 'Old', description: '' }] as never });
    vi.mocked(api.updateAgent).mockResolvedValueOnce({ success: false, message: 'name nope' } as never);
    const view = renderPanel();
    const input = view.getByPlaceholderText(/Morning Market Brief/i);
    fireEvent.change(input, { target: { value: 'New' } });
    fireEvent.blur(input);
    await waitFor(() => expect(view.getByText(/name nope/)).toBeInTheDocument());
    fireEvent.change(input, { target: { value: 'Old' } });
    fireEvent.blur(input);
    await waitFor(() => expect(view.queryByText(/name nope/)).not.toBeInTheDocument());
  });

  test('flushes an unblurred edit instead of losing it', async () => {
    // A real browser blurs the textarea before the button's click, but nothing
    // guarantees that commit finished — and in jsdom no blur fires at all. So
    // Done awaits both commits itself.
    openStudio(AGENT);
    const view = renderPanel();

    fireEvent.change(view.getByPlaceholderText(/What this agent does/i), {
      target: { value: '## Role\nMorning brief' },
    });
    fireEvent.click(view.getByRole('button', { name: 'Done' }));

    await waitFor(() => {
      expect(api.updateAwareness).toHaveBeenCalledWith(AGENT, '## Role\nMorning brief');
    });
  });

  test('stays clickable while a skill is installing', async () => {
    // Requirement (Owner, 2026-09-03): installing/studying a skill must NOT
    // gate Done. Study runs for minutes inside the agent's workspace and keeps
    // running after the studio closes — blocking on it would strand the user.
    saveRecommendations(AGENT, { skill_ids: ['web-search'], channels: [] });
    vi.mocked(api.installMarketplaceSkill).mockReturnValue(new Promise(() => undefined) as never);
    const view = renderPanel();

    fireEvent.click(view.getByRole('button', { name: /Install/i }));
    await waitFor(() => {
      expect(view.getByRole('button', { name: /Install/i })).toBeDisabled();
    });
    expect(view.getByRole('button', { name: 'Done' })).toBeEnabled();
  });
});
