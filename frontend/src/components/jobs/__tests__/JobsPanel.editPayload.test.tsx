/**
 * @file_name: JobsPanel.editPayload.test.tsx
 * @date: 2026-09-11
 * @description: JobsPanel's save path for the job content editor (GitHub #86).
 *
 * The dialog itself and api.updateJob's request shape have their own tests;
 * this pins the layer between them, which is where "saving actually works"
 * lives: the request carries the job's OWN agent_id (the backend's ownership
 * check rejects any other), the dialog only closes and the list only
 * refreshes on success, and a failed or thrown save stays open with a
 * visible error in the real confirm dialog.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { Job } from '@/types/api';

const { updateJob, refreshJobs } = vi.hoisted(() => ({
  updateJob: vi.fn(),
  refreshJobs: vi.fn(),
}));

// The job belongs to a different agent than the one the panel is focused on.
const JOB: Job = {
  job_id: 'job_42',
  agent_id: 'agent_owner',
  user_id: 'user_me',
  job_type: 'scheduled',
  title: 'Morning briefing',
  description: 'Daily summary',
  status: 'pending',
  payload: 'Summarize overnight news.',
} as Job;

vi.mock('@/stores', () => ({
  useConfigStore: () => ({ agentId: 'agent_focused', userId: 'user_me' }),
  usePreloadStore: () => ({ jobs: [JOB], jobsLoading: false, refreshJobs }),
}));

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>();
  return { ...actual, api: { ...actual.api, updateJob } };
});

import { JobsPanel } from '../JobsPanel';

function openEditorAndSave(newPayload: string) {
  render(<JobsPanel embedded />);
  fireEvent.click(screen.getByText('Morning briefing'));
  fireEvent.click(screen.getByRole('button', { name: /^Edit Content$/ }));
  fireEvent.change(screen.getByDisplayValue('Summarize overnight news.'), {
    target: { value: newPayload },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Save' }));
}

const editorOpen = () => screen.queryByText('Edit Job Content') !== null;

/** Acknowledge the failure notice; the handler only settles after this. */
async function dismissFailure() {
  fireEvent.click(screen.getByRole('button', { name: 'OK' }));
  await waitFor(() => expect(screen.queryByText('Update failed')).toBeNull());
}

beforeEach(() => {
  updateJob.mockReset();
  refreshJobs.mockReset();
});

describe('JobsPanel — saving job content', () => {
  it('sends only the changed field with the job\'s own agent_id, then closes and refreshes', async () => {
    updateJob.mockResolvedValue({ success: true, job_id: 'job_42', updated_fields: ['payload'] });
    openEditorAndSave('Three bullets only.');

    await waitFor(() => expect(editorOpen()).toBe(false));
    expect(updateJob).toHaveBeenCalledTimes(1);
    expect(updateJob).toHaveBeenCalledWith('job_42', 'agent_owner', { payload: 'Three bullets only.' });
    expect(refreshJobs).toHaveBeenCalledWith('agent_focused', 'user_me');
  });

  it('keeps the editor open and shows the server message when the save is refused', async () => {
    updateJob.mockResolvedValue({ success: false, message: 'Job is running' });
    openEditorAndSave('Three bullets only.');

    // The real useConfirm dialog renders the failure next to the editor.
    expect(await screen.findByText('Update failed')).toBeInTheDocument();
    expect(screen.getByText('Job is running')).toBeInTheDocument();
    await dismissFailure();
    // Still open with the user's edit intact, so they can retry.
    expect(editorOpen()).toBe(true);
    expect(screen.getByDisplayValue('Three bullets only.')).toBeInTheDocument();
    expect(refreshJobs).not.toHaveBeenCalled();
  });

  it('keeps the editor open and shows the error when the request throws', async () => {
    updateJob.mockRejectedValue(new Error('Network down'));
    const err = vi.spyOn(console, 'error').mockImplementation(() => {});
    openEditorAndSave('Three bullets only.');

    expect(await screen.findByText('Update failed')).toBeInTheDocument();
    expect(screen.getByText('Network down')).toBeInTheDocument();
    await dismissFailure();
    expect(editorOpen()).toBe(true);
    expect(screen.getByDisplayValue('Three bullets only.')).toBeInTheDocument();
    expect(refreshJobs).not.toHaveBeenCalled();
    err.mockRestore();
  });
});
