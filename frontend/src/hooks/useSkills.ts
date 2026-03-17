/**
 * @file_name: useSkills.ts
 * @author: Bin Liang
 * @date: 2026-03-06
 * @description: TanStack Query hooks for Skills CRUD operations
 */

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { useConfigStore } from '@/stores';

const SKILLS_KEY = 'skills';

function useSkillsQueryKey(showDisabled: boolean) {
  const { agentId, userId } = useConfigStore();
  return [SKILLS_KEY, agentId, userId, showDisabled] as const;
}

/** Fetch the skill list with automatic caching and refetch */
export function useSkillsList(showDisabled: boolean) {
  const { agentId, userId } = useConfigStore();
  const queryKey = useSkillsQueryKey(showDisabled);

  return useQuery({
    queryKey,
    queryFn: () => api.listSkills(agentId!, userId!, showDisabled),
    enabled: !!agentId && !!userId,
    select: (data) => data.skills,
  });
}

/** Install skill from GitHub */
export function useInstallFromGithub() {
  const { agentId, userId } = useConfigStore();
  const qc = useQueryClient();

  return useMutation({
    mutationFn: ({ url, branch }: { url: string; branch: string }) =>
      api.installSkillFromGithub(agentId!, userId!, url, branch),
    onSuccess: () => qc.invalidateQueries({ queryKey: [SKILLS_KEY] }),
  });
}

/** Install skill from zip file */
export function useInstallFromZip() {
  const { agentId, userId } = useConfigStore();
  const qc = useQueryClient();

  return useMutation({
    mutationFn: (file: File) =>
      api.installSkillFromZip(agentId!, userId!, file),
    onSuccess: () => qc.invalidateQueries({ queryKey: [SKILLS_KEY] }),
  });
}

/** Toggle skill enabled/disabled */
export function useToggleSkill() {
  const { agentId, userId } = useConfigStore();
  const qc = useQueryClient();

  return useMutation({
    mutationFn: ({ name, disabled }: { name: string; disabled: boolean }) =>
      disabled
        ? api.enableSkill(name, agentId!, userId!)
        : api.disableSkill(name, agentId!, userId!),
    onSuccess: () => qc.invalidateQueries({ queryKey: [SKILLS_KEY] }),
  });
}

/** Remove a skill */
export function useRemoveSkill() {
  const { agentId, userId } = useConfigStore();
  const qc = useQueryClient();

  return useMutation({
    mutationFn: (name: string) =>
      api.removeSkill(name, agentId!, userId!),
    onSuccess: () => qc.invalidateQueries({ queryKey: [SKILLS_KEY] }),
  });
}

/** Start studying a skill */
export function useStudySkill() {
  const { agentId, userId } = useConfigStore();
  const qc = useQueryClient();

  return useMutation({
    mutationFn: (name: string) =>
      api.studySkill(name, agentId!, userId!),
    onSuccess: () => qc.invalidateQueries({ queryKey: [SKILLS_KEY] }),
  });
}

/** Poll study status for a skill (enabled only when studying) */
export function useStudyStatus(skillName: string | null) {
  const { agentId, userId } = useConfigStore();
  const qc = useQueryClient();

  return useQuery({
    queryKey: [SKILLS_KEY, 'study-status', skillName],
    queryFn: async () => {
      const status = await api.getSkillStudyStatus(skillName!, agentId!, userId!);
      if (status.study_status === 'completed' || status.study_status === 'failed') {
        // Study finished — invalidate the skill list to refresh
        qc.invalidateQueries({ queryKey: [SKILLS_KEY] });
      }
      return status;
    },
    enabled: !!skillName && !!agentId && !!userId,
    refetchInterval: (query) => {
      const status = query.state.data?.study_status;
      if (status === 'completed' || status === 'failed') return false;
      return 3000; // Poll every 3s while studying
    },
  });
}
