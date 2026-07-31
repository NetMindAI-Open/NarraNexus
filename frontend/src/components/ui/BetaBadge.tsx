/**
 * @file_name: BetaBadge.tsx
 * @date: 2026-07-28
 * @description: Brand "Beta" marker shown next to the NarraNexus logo.
 *
 * The label is a deliberate untranslated literal — "Beta" is part of the
 * brand lockup and, per industry convention, stays in Latin script across
 * every locale. Only the hover note (expectation management) is translated.
 */

import { useTranslation } from 'react-i18next';
import { Badge } from './Badge';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from './tooltip';

export function BetaBadge({ className }: { className?: string }) {
  const { t } = useTranslation();
  const note = t('common.betaTooltip');

  return (
    <TooltipProvider delayDuration={200}>
      <Tooltip>
        <TooltipTrigger asChild>
          {/* aria-label keeps the note reachable for screen readers and
              touch devices, where Radix's hover-only content never mounts. */}
          <Badge size="sm" aria-label={note} className={className}>
            Beta
          </Badge>
        </TooltipTrigger>
        <TooltipContent>{note}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
