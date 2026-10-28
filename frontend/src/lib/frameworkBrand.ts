/**
 * @file_name: frameworkBrand.ts
 * @author: NetMind.AI
 * @date: 2026-09-04
 * @description: framework id → brand mark, plus the two rendering fallbacks
 * the directory and the profile page rely on. LABELS ARE NOT DEFINED HERE:
 * they are forwarded from `agentFramework.AGENT_FRAMEWORKS`, the list the
 * framework pickers already render — so the directory column, the profile
 * card and the settings dropdown can never disagree on a name.
 *
 * The Dashboard directory and the Agent Profile page each carried their own
 * label + icon map, and the two had already drifted once (one knew
 * `nexus_power`, the other did not — tsc said nothing). The first cut of this
 * module fixed that by adding a THIRD label table, which disagreed with the
 * pickers on two of three names ("Codex" vs "Codex CLI") — the same page could
 * show both. Deriving from the picker list is the actual fix.
 *
 * Two behaviours callers rely on, both deliberate:
 *   - an UNKNOWN id is title-cased from the raw string, never mapped to some
 *     other brand — "unknown but present" beats "wrong";
 *   - a MISSING id (undefined) renders as '—' and the generic Bot glyph. The
 *     backend leaves it undefined for agents whose configuration is not the
 *     viewer's to see; inventing a default here would show a brand the agent
 *     may not be running on.
 *
 * Read the bare `AGENT_FRAMEWORKS`, never `availableFrameworks()`: the latter
 * is filtered by what the current user's providers can back, and an agent
 * running a framework this user cannot pick must still be labelled truthfully.
 */
import type { ComponentType } from 'react';
import { Bot } from 'lucide-react';
import { ClaudeBrandIcon, OpenAIBrandIcon } from '@/components/icons/ModelBrandIcons';
import { NexusPowerBrandIcon } from '@/components/icons/ChannelBrandIcons';
import { AGENT_FRAMEWORKS } from '@/lib/agentFramework';
import { iconInvertsInDark } from '@/lib/modelBrandIcons';

export type BrandIconComponent = ComponentType<{ className?: string }>;

const FRAMEWORK_ICONS: Record<string, BrandIconComponent> = {
  claude_code: ClaudeBrandIcon,
  codex_cli: OpenAIBrandIcon,
  nexus_power: NexusPowerBrandIcon,
};

/** Human label for a framework id — the picker's label; '—' when missing. */
export function formatFramework(framework?: string | null): string {
  if (!framework) return '—';
  const known = AGENT_FRAMEWORKS.find((f) => f.id === framework);
  if (known) return known.label;
  return framework
    .split('_')
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');
}

/**
 * Human label for a framework id, preferring the LIVE `display_name` from
 * `GET/POST /api/providers/agent-framework`'s `frameworks[]` (B6, 2026-09-07) over the static
 * picker label — this is the ONE place a caller with that response in hand asks for a label,
 * instead of growing a third hardcoded id→label table (`ProviderSummaryCard.tsx`'s
 * `FRAMEWORK_LABELS` and `TeamMemberAvatars.tsx`'s own `formatFramework` both were exactly that,
 * and both are now gone in favor of this). Falls back to `formatFramework` (the static
 * picker-label-then-title-case chain) whenever the list is absent (not loaded yet, or the caller
 * has no live data at all) or has no entry for this id.
 */
export function formatFrameworkFromList(
  framework: string | null | undefined,
  frameworks: Array<{ name: string; display_name?: string }> | undefined,
): string {
  if (!framework) return '—';
  const live = frameworks?.find((f) => f.name === framework)?.display_name;
  return live || formatFramework(framework);
}

/** Brand mark for a framework id; the generic Bot glyph when unknown or missing. */
export function frameworkBrandIcon(framework?: string | null): BrandIconComponent {
  return (framework && FRAMEWORK_ICONS[framework]) || Bot;
}

/** Forwarded to the brand-icon layer's rule; kept so callers of this module
 *  need not import icon components to ask. */
export function frameworkIconInvertsInDark(icon: BrandIconComponent): boolean {
  return iconInvertsInDark(icon);
}
