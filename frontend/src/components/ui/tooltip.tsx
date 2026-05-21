/**
 * @file_name: tooltip.tsx
 * @description: Radix Tooltip — NM design system (inverted ink-on-paper).
 * Restyled in M2: warm-tinted inverted bubble + radius-sm + paper text +
 * subtle motion. No shadow, no border (the inversion carries the lift).
 *
 * Spec: reference/self_notebook/specs/2026-05-18-nm-design-system-design.md §5.9
 */

import * as React from 'react';
import * as TooltipPrimitive from '@radix-ui/react-tooltip';
import { cn } from '../../lib/utils';

const TooltipProvider = TooltipPrimitive.Provider;
const Tooltip = TooltipPrimitive.Root;
const TooltipTrigger = TooltipPrimitive.Trigger;

const TooltipContent = React.forwardRef<
  React.ElementRef<typeof TooltipPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof TooltipPrimitive.Content>
>(({ className, sideOffset = 6, ...props }, ref) => (
  <TooltipPrimitive.Portal>
    <TooltipPrimitive.Content
      ref={ref}
      sideOffset={sideOffset}
      data-nm="tooltip"
      className={cn(
        'z-[100] px-2.5 py-1.5 max-w-xs',
        'text-xs leading-snug',
        'animate-in fade-in-0 zoom-in-95',
        'data-[state=closed]:animate-out data-[state=closed]:fade-out-0',
        className
      )}
      style={{
        background: 'var(--nm-ink)',
        color: 'var(--nm-paper)',
        borderRadius: 'var(--radius-sm)',
        fontFamily: 'var(--font-sans)',
      }}
      {...props}
    />
  </TooltipPrimitive.Portal>
));
TooltipContent.displayName = TooltipPrimitive.Content.displayName;

export { Tooltip, TooltipTrigger, TooltipContent, TooltipProvider };
