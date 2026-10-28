/**
 * B6: TeamMemberAvatars used to carry its own local `formatFramework` — which disagreed with
 * the canonical picker labels on two of three names ("Codex" instead of "Codex CLI", "Nexus
 * Power" instead of "NexusPower-beta"; `lib/frameworkBrand.ts`'s docstring documents this exact
 * class of bug). This asserts the hover card now uses the shared `formatFrameworkFromList`
 * helper (the same one `ProviderSummaryCard.tsx` uses) instead of a private, drifted copy.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import type { AgentInfo, AgentStatus } from '@/types';

vi.mock('@/components/ui/tooltip', () => ({
  TooltipProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  Tooltip: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  TooltipTrigger: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  TooltipContent: ({ children }: { children: React.ReactNode }) => <div role="tooltip">{children}</div>,
}));

import { TeamMemberAvatars } from '../TeamMemberAvatars';

function agent(id: string, framework: string): AgentInfo {
  return { agent_id: id, name: id, agent_framework: framework } as AgentInfo;
}

const IDLE_STATUS: AgentStatus = { status: { kind: 'idle' } } as AgentStatus;

describe('TeamMemberAvatars — framework label (B6)', () => {
  it('renders the canonical picker label for codex_cli, not the old drifted "Codex"', () => {
    const agentsById = new Map([['a1', agent('a1', 'codex_cli')]]);
    const statusById = new Map([['a1', IDLE_STATUS]]);
    render(
      <MemoryRouter>
        <TeamMemberAvatars memberAgentIds={['a1']} agentsById={agentsById} statusById={statusById} />
      </MemoryRouter>,
    );
    expect(screen.getByText('Codex CLI')).toBeInTheDocument();
    expect(screen.queryByText('Codex')).toBeNull();
  });

  it('renders the canonical picker label for nexus_power, not the old drifted "Nexus Power"', () => {
    const agentsById = new Map([['a2', agent('a2', 'nexus_power')]]);
    const statusById = new Map([['a2', IDLE_STATUS]]);
    render(
      <MemoryRouter>
        <TeamMemberAvatars memberAgentIds={['a2']} agentsById={agentsById} statusById={statusById} />
      </MemoryRouter>,
    );
    expect(screen.getByText('NexusPower-beta')).toBeInTheDocument();
  });
});
