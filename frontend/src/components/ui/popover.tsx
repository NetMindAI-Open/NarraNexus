/**
 * @file_name: popover.tsx
 * @description: Radix Popover — NM design system (RaisedPanel style).
 * Restyled in M2: paper-raised bg + hairline border + radius-md + soft lift.
 * Replaces the previous Archive "1px ink border, no radius" treatment.
 *
 * Spec: reference/self_notebook/specs/2026-05-18-nm-design-system-design.md §5.9
 */

import * as React from 'react';
import * as PopoverPrimitive from '@radix-ui/react-popover';
import { cn } from '../../lib/utils';

const Popover = PopoverPrimitive.Root;
const PopoverTrigger = PopoverPrimitive.Trigger;
const PopoverAnchor = PopoverPrimitive.Anchor;

const PopoverContent = React.forwardRef<
  React.ElementRef<typeof PopoverPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof PopoverPrimitive.Content>
>(({ className, align = 'center', sideOffset = 6, ...props }, ref) => (
  <PopoverPrimitive.Portal>
    <PopoverPrimitive.Content
      ref={ref}
      align={align}
      sideOffset={sideOffset}
      data-nm="popover"
      className={cn(
        'z-[100] w-72 p-4 outline-none',
        'animate-in zoom-in-95 fade-in-0',
        'data-[state=closed]:animate-out data-[state=closed]:fade-out-0',
        'data-[side=bottom]:slide-in-from-top-1',
        'data-[side=left]:slide-in-from-right-1',
        'data-[side=right]:slide-in-from-left-1',
        'data-[side=top]:slide-in-from-bottom-1',
        className
      )}
      style={{
        background: 'var(--nm-raised)',
        border: '1px solid var(--nm-hairline)',
        borderRadius: 'var(--radius-md)',
        boxShadow: 'var(--nm-elev-2)',
        color: 'var(--nm-ink)',
      }}
      {...props}
    />
  </PopoverPrimitive.Portal>
));
PopoverContent.displayName = PopoverPrimitive.Content.displayName;

export { Popover, PopoverTrigger, PopoverContent, PopoverAnchor };
