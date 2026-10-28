/**
 * @file_name: FrameworkBrandIcons.tsx
 * @author: NetMind.AI
 * @date: 2026-08-27
 * @description: Real brand marks for the agent tools we can import FROM
 * (Claude Code / Codex / OpenClaw / Hermes). Same convention as
 * ChannelBrandIcons / ModelBrandIcons: each third-party product is shown with
 * its own mark, not a grey silhouette, so the import list reads as "your tools"
 * instead of a column of identical robots.
 *
 * Owner decision 2026-08-27; design_system §5 (lucide-only) carries the
 * matching exception: third-party PRODUCT IDENTITY may use real marks, UI
 * semantics stay lucide.
 *
 * Provenance per mark — every one is the vendor's own asset, none invented:
 *   Claude Code → ClaudeBrandIcon (Simple Icons CC0, #D97757), reused.
 *   Codex       → OpenAIBrandIcon (Simple Icons CC0), refilled with --nm-ink:
 *                 its canonical black is invisible on dark warm paper.
 *   OpenClaw    → openclaw.ai's own favicon.svg (red-gradient lobster),
 *                 vendored at public/framework-logos/openclaw.svg.
 *   Hermes      → NousResearch/hermes-agent has NO square glyph that survives
 *                 16px (their favicon is a 48px engraving that turns to mush),
 *                 so this is a LETTERMARK taken from their wordmark: white
 *                 serif H on their brand blue #0000A5. Drop a real glyph into
 *                 public/framework-logos/hermes.* and swap this one line.
 *
 * Pure icon components only — framework → icon matching lives in
 * lib/migrationLabels.ts (react-refresh forbids mixing component exports with
 * plain function exports in one file).
 */

import { Bot } from 'lucide-react';
import { ClaudeBrandIcon, OpenAIBrandIcon } from './ModelBrandIcons';

export interface FrameworkIconProps {
  className?: string;
}

export function ClaudeCodeFrameworkIcon({ className }: FrameworkIconProps) {
  return <ClaudeBrandIcon className={className} />;
}

export function CodexFrameworkIcon({ className }: FrameworkIconProps) {
  // fill overrides the mark's hardcoded #000000 (props spread last in
  // ModelBrandIcons' BrandIcon), so the mark follows the theme's ink.
  return <OpenAIBrandIcon className={className} fill="var(--nm-ink)" />;
}

export function OpenClawFrameworkIcon({ className }: FrameworkIconProps) {
  return <img src="/framework-logos/openclaw.svg" alt="OpenClaw" className={className} />;
}

/** Brand blue + white serif H — see the provenance note in the file header. */
export function HermesFrameworkIcon({ className }: FrameworkIconProps) {
  return (
    <span
      aria-label="Hermes"
      role="img"
      className={className}
      style={{
        display: 'grid',
        placeItems: 'center',
        borderRadius: 'var(--radius-xs)',
        background: '#0000A5',
        color: '#FFFFFF',
        fontFamily: 'Georgia, "Times New Roman", serif',
        fontWeight: 600,
        fontSize: '0.72em',
        lineHeight: 1,
      }}
    >
      H
    </span>
  );
}

/** Fallback for a framework the backend detects before the frontend knows it. */
export function UnknownFrameworkIcon({ className }: FrameworkIconProps) {
  return <Bot className={className} />;
}
