/**
 * @file_name: useSkillMarketplace.ts
 * @author: NetMind.AI
 * @date: 2026-07-21
 * @description: TanStack Query hooks for the Skill Marketplace
 * (search / detail / install / update check).
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { useConfigStore } from '@/stores';

const MARKETPLACE_KEY = 'skill-marketplace';
const SKILLS_KEY = 'skills';

/** Search the marketplace. Pass the DEBOUNCED query string. */
export function useMarketplaceSearch(q: string, enabled: boolean = true) {
  const { agentId } = useConfigStore();
  return useQuery({
    queryKey: [MARKETPLACE_KEY, 'search', agentId, q],
    queryFn: () =>
      api.searchMarketplaceSkills({ q: q || undefined, agentId: agentId ?? undefined, limit: 30 }),
    enabled,
    staleTime: 30_000,
  });
}

export function useMarketplaceDetail(skillId: string | null) {
  return useQuery({
    queryKey: [MARKETPLACE_KEY, 'detail', skillId],
    queryFn: () => api.getMarketplaceSkillDetail(skillId!),
    enabled: !!skillId,
    staleTime: 30_000,
  });
}

/** Install a marketplace skill for the current agent. Invalidates both the
 *  installed-skills list and marketplace searches (installed flags). */
export function useMarketplaceInstall() {
  const { agentId } = useConfigStore();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ skillId, version }: { skillId: string; version?: string }) =>
      api.installMarketplaceSkill(skillId, agentId!, version),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: [SKILLS_KEY] });
      qc.invalidateQueries({ queryKey: [MARKETPLACE_KEY] });
    },
  });
}

/** Which installed skills have a newer marketplace version. */
export function useSkillUpdates() {
  const { agentId, userId } = useConfigStore();
  return useQuery({
    queryKey: [MARKETPLACE_KEY, 'updates', agentId],
    queryFn: () => api.checkSkillUpdates(agentId!),
    enabled: !!agentId && !!userId,
    staleTime: 5 * 60_000,
    select: (data) => data.updates,
  });
}
