/**
 * JobPayloadEditDialog — the payload/title/description sibling of
 * JobScheduleEditDialog, added for GitHub #86 (the PUT /api/jobs/{job_id}
 * route already existed with no frontend edit form at all).
 */
import { describe, expect, test, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { JobPayloadEditDialog } from '../JobPayloadEditDialog';
import type { Job } from '@/types/api';

const baseJob: Job = {
  job_id: 'job_1',
  agent_id: 'agent_1',
  user_id: 'user_1',
  job_type: 'one_off' as Job['job_type'],
  title: 'Morning briefing',
  description: 'Sends the daily summary',
  status: 'pending' as Job['status'],
  payload: 'Summarize overnight news.',
};

describe('JobPayloadEditDialog', () => {
  test('submits only the fields the user actually changed', () => {
    const onSave = vi.fn();
    render(
      <JobPayloadEditDialog job={baseJob} isOpen saving={false} onClose={() => {}} onSave={onSave} />,
    );

    const payloadBox = screen.getByDisplayValue('Summarize overnight news.');
    fireEvent.change(payloadBox, { target: { value: 'Summarize overnight news in 3 bullets.' } });

    fireEvent.click(screen.getByRole('button', { name: /save/i }));

    expect(onSave).toHaveBeenCalledWith({ payload: 'Summarize overnight news in 3 bullets.' });
  });

  test('title and description are submitted trimmed; the payload keeps its whitespace', () => {
    const onSave = vi.fn();
    render(
      <JobPayloadEditDialog job={baseJob} isOpen saving={false} onClose={() => {}} onSave={onSave} />,
    );

    fireEvent.change(screen.getByDisplayValue('Morning briefing'), {
      target: { value: '  Evening briefing  ' },
    });
    fireEvent.change(screen.getByDisplayValue('Sends the daily summary'), {
      target: { value: ' Sends the nightly summary\n' },
    });
    fireEvent.change(screen.getByDisplayValue('Summarize overnight news.'), {
      target: { value: 'Summarize overnight news.\n' },
    });
    fireEvent.click(screen.getByRole('button', { name: /save/i }));

    expect(onSave).toHaveBeenCalledWith({
      title: 'Evening briefing',
      description: 'Sends the nightly summary',
      payload: 'Summarize overnight news.\n',
    });
  });

  test('whitespace-only edits to title/description count as unchanged', () => {
    const onSave = vi.fn();
    const onClose = vi.fn();
    render(
      <JobPayloadEditDialog job={baseJob} isOpen saving={false} onClose={onClose} onSave={onSave} />,
    );

    fireEvent.change(screen.getByDisplayValue('Morning briefing'), {
      target: { value: 'Morning briefing  ' },
    });
    fireEvent.click(screen.getByRole('button', { name: /save/i }));

    expect(onSave).not.toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
  });

  test('closes without saving when nothing changed', () => {
    const onSave = vi.fn();
    const onClose = vi.fn();
    render(
      <JobPayloadEditDialog job={baseJob} isOpen saving={false} onClose={onClose} onSave={onSave} />,
    );

    fireEvent.click(screen.getByRole('button', { name: /save/i }));

    expect(onSave).not.toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
  });

  test('an empty title is rejected client-side (no onSave call)', () => {
    const onSave = vi.fn();
    render(
      <JobPayloadEditDialog job={baseJob} isOpen saving={false} onClose={() => {}} onSave={onSave} />,
    );

    const titleBox = screen.getByDisplayValue('Morning briefing');
    fireEvent.change(titleBox, { target: { value: '   ' } });
    fireEvent.click(screen.getByRole('button', { name: /save/i }));

    expect(onSave).not.toHaveBeenCalled();
    expect(screen.getByText(/cannot be empty/i)).toBeTruthy();
  });
});
