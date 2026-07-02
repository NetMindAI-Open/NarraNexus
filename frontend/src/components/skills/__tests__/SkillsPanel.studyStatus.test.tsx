/**
 * @file_name: SkillsPanel.studyStatus.test.tsx
 * @description: Regression test for the "spinner never stops after study
 *   completes" bug.
 *
 * Root cause (frontend/src/components/skills/SkillsPanel.tsx): the panel's
 * local `studyingSkillName` state was set on `handleStudy` and only ever
 * cleared in the mutation's `onError` callback — a successful study never
 * cleared it, so `isStudying` stayed `true` forever and the right-panel
 * status badge kept spinning until a manual page reload reset component
 * state. The fix makes the panel read `useStudyStatus`'s polled data and
 * clear `studyingSkillName` once the job reaches a terminal state
 * (`completed` / `failed`).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import type { SkillInfo, SkillStudyResponse } from '@/types/skills';

let skills: SkillInfo[];
let studyStatusData: SkillStudyResponse | undefined;
const studyMutate = vi.fn();

vi.mock('@/hooks/useSkills', () => ({
  useSkillsList: () => ({ data: skills, isLoading: false, error: null, refetch: vi.fn() }),
  useInstallFromGithub: () => ({ mutate: vi.fn(), isPending: false }),
  useInstallFromZip: () => ({ mutate: vi.fn(), isPending: false }),
  useToggleSkill: () => ({ mutate: vi.fn(), isPending: false, variables: undefined }),
  useRemoveSkill: () => ({ mutate: vi.fn(), isPending: false, variables: undefined }),
  useStudySkill: () => ({ mutate: studyMutate, isPending: false }),
  useStudyStatus: () => ({ data: studyStatusData }),
}));

import { SkillsPanel } from '../SkillsPanel';

const SKILL: SkillInfo = {
  name: 'demo-skill',
  description: 'A demo skill',
  path: '/skills/demo-skill',
  disabled: false,
  requires_env: [],
};

beforeEach(() => {
  skills = [{ ...SKILL }];
  studyStatusData = undefined;
  studyMutate.mockReset();
  // Mirrors the real mutation: caller sets local "studying" state before
  // calling mutate(); we don't invoke onSuccess/onError here so the test
  // controls the terminal state purely via `studyStatusData` + rerender,
  // just like the real polling loop would.
  studyMutate.mockImplementation(() => {});
});

describe('SkillsPanel — study status refresh', () => {
  it('stops the studying spinner once polling reports completed', async () => {
    const { rerender } = render(<SkillsPanel />);

    fireEvent.click(screen.getByRole('button', { name: /study/i }));
    expect(studyMutate).toHaveBeenCalledWith('demo-skill', expect.anything());

    // Spinner badge is visible while studying.
    expect(screen.getByText(/studying/i)).toBeInTheDocument();

    // Simulate the poll resolving to "completed": the list refetch (real
    // app invalidates [SKILLS_KEY]) also updates the skill's own status.
    studyStatusData = { success: true, study_status: 'completed' };
    skills = [{ ...SKILL, study_status: 'completed' }];
    await act(async () => {
      rerender(<SkillsPanel />);
    });

    expect(screen.queryByText(/studying/i)).not.toBeInTheDocument();
  });

  it('stops the studying spinner once polling reports failed', async () => {
    const { rerender } = render(<SkillsPanel />);

    fireEvent.click(screen.getByRole('button', { name: /study/i }));
    expect(screen.getByText(/studying/i)).toBeInTheDocument();

    studyStatusData = { success: false, study_status: 'failed' };
    skills = [{ ...SKILL, study_status: 'failed', study_error: 'boom' }];
    await act(async () => {
      rerender(<SkillsPanel />);
    });

    expect(screen.queryByText(/studying/i)).not.toBeInTheDocument();
  });
});
