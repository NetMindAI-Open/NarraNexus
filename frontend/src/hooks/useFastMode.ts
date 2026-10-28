/**
 * useFastMode — per-agent fast-mode toggle state, persisted in
 * localStorage. The backend deliberately persists nothing (the
 * TurnProfile rides one turn only — see schema/turn_profile.py), so the
 * browser owns the preference. Keyed per agent to match the per-agent
 * model choice mental model of the composer tools row.
 */
import { useCallback, useState } from 'react';

const STORAGE_KEY = 'narra-nexus-fast-mode';

function readMap(): Record<string, boolean> {
  try {
    const parsed: unknown = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '{}');
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed)
      ? (parsed as Record<string, boolean>)
      : {};
  } catch {
    return {};
  }
}

function readEnabled(agentId: string | undefined): boolean {
  return agentId ? readMap()[agentId] === true : false;
}

export function useFastMode(
  agentId: string | undefined,
): [boolean, (value: boolean) => void] {
  // Storage is only re-read when the agent changes — the
  // adjust-state-during-render pattern avoids an effect (and the extra
  // render pass per streaming update it would cost).
  const [state, setState] = useState(() => ({
    agentId,
    enabled: readEnabled(agentId),
  }));
  if (state.agentId !== agentId) {
    setState({ agentId, enabled: readEnabled(agentId) });
  }

  const set = useCallback(
    (value: boolean) => {
      if (!agentId) return;
      const map = readMap();
      if (value) map[agentId] = true;
      else delete map[agentId];
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(map));
      } catch {
        /* quota/private mode — state still updates for this session */
      }
      setState({ agentId, enabled: value });
    },
    [agentId],
  );

  return [state.enabled, set];
}
