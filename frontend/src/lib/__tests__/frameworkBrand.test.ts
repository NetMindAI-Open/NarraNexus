/**
 * The two fallbacks the directory and profile page rely on, and the one
 * invariant that labels come from the picker list rather than a private copy.
 */
import { describe, it, expect } from 'vitest';
import { Bot } from 'lucide-react';
import { formatFramework, formatFrameworkFromList, frameworkBrandIcon, frameworkIconInvertsInDark } from '../frameworkBrand';
import { AGENT_FRAMEWORKS } from '../agentFramework';
import { OpenAIBrandIcon, ClaudeBrandIcon } from '@/components/icons/ModelBrandIcons';

describe('frameworkBrand', () => {
  it('labels every known framework exactly as the picker does', () => {
    for (const f of AGENT_FRAMEWORKS) expect(formatFramework(f.id)).toBe(f.label);
    expect(formatFramework('nexus_power')).toBe('NexusPower-beta');
  });

  it('title-cases an unknown id instead of mapping it to another brand', () => {
    expect(formatFramework('some_new_fw')).toBe('Some New Fw');
    expect(frameworkBrandIcon('some_new_fw')).toBe(Bot);
  });

  it('renders a missing id as — with the generic glyph, never a default brand', () => {
    expect(formatFramework(undefined)).toBe('—');
    expect(formatFramework(null)).toBe('—');
    expect(frameworkBrandIcon(undefined)).toBe(Bot);
  });

  describe('formatFrameworkFromList — the backend-provided display_name (B6)', () => {
    // ProviderSummaryCard.tsx / TeamMemberAvatars.tsx previously each carried their own hardcoded
    // id→label table, duplicating what this module already centralizes. B6: the backend now
    // sends `display_name` per entry in `GET /api/providers/agent-framework`'s `frameworks[]` —
    // this is the ONE place that gets threaded through instead of a THIRD hardcoded copy.
    it('prefers the live list entry\'s display_name over the static picker label', () => {
      expect(formatFrameworkFromList('claude_code', [{ name: 'claude_code', display_name: 'Claude Code (Beta)' }])).toBe('Claude Code (Beta)');
    });

    it('falls back to the static picker label when the list has no entry for this id', () => {
      expect(formatFrameworkFromList('claude_code', [{ name: 'codex_cli', display_name: 'Codex CLI' }])).toBe('Claude Code');
    });

    it('falls back to the static picker label (then raw-name title-case) when the list has not loaded', () => {
      expect(formatFrameworkFromList('claude_code', undefined)).toBe('Claude Code');
      expect(formatFrameworkFromList('some_new_fw', undefined)).toBe('Some New Fw');
    });

    it('renders a missing id as — regardless of the list', () => {
      expect(formatFrameworkFromList(undefined, [{ name: 'claude_code', display_name: 'x' }])).toBe('—');
    });
  });

  it('knows which mark needs inverting in dark mode', () => {
    expect(frameworkIconInvertsInDark(OpenAIBrandIcon)).toBe(true);
    expect(frameworkIconInvertsInDark(ClaudeBrandIcon)).toBe(false);
    expect(frameworkIconInvertsInDark(Bot)).toBe(false);
  });
});
