/**
 * @file_name: tabs.tsx
 * @description: Radix tabs — Nordic archive style (DM Mono, underline-active)
 */

import * as React from 'react';
import * as TabsPrimitive from '@radix-ui/react-tabs';
import { cn } from '../../lib/utils';

const Tabs = TabsPrimitive.Root;

const TabsList = React.forwardRef<
  React.ElementRef<typeof TabsPrimitive.List>,
  React.ComponentPropsWithoutRef<typeof TabsPrimitive.List>
>(({ className, ...props }, ref) => (
  <TabsPrimitive.List
    ref={ref}
    className={cn(
      'inline-flex items-center justify-start gap-1',
      'border-b border-[var(--rule)] w-full',
      'text-[var(--text-secondary)]',
      className
    )}
    {...props}
  />
));
TabsList.displayName = TabsPrimitive.List.displayName;

const TabsTrigger = React.forwardRef<
  React.ElementRef<typeof TabsPrimitive.Trigger>,
  React.ComponentPropsWithoutRef<typeof TabsPrimitive.Trigger>
>(({ className, ...props }, ref) => (
  <TabsPrimitive.Trigger
    ref={ref}
    className={cn(
      'relative inline-flex items-center justify-center whitespace-nowrap',
      'px-3 py-2 -mb-px',
      'text-[11px] font-normal font-[family-name:var(--font-mono)]',
      'uppercase tracking-[0.12em]',
      'text-[var(--text-tertiary)]',
      'border-b-2 border-transparent',
      'transition-colors duration-150',
      'focus-visible:outline-none focus-visible:text-[var(--text-primary)]',
      'disabled:pointer-events-none disabled:opacity-50',
      'hover:text-[var(--text-primary)]',
      'data-[state=active]:text-[var(--text-primary)]',
      'data-[state=active]:border-[var(--text-primary)]',
      className
    )}
    {...props}
  />
));
TabsTrigger.displayName = TabsPrimitive.Trigger.displayName;

const TabsContent = React.forwardRef<
  React.ElementRef<typeof TabsPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof TabsPrimitive.Content>
>(({ className, ...props }, ref) => (
  <TabsPrimitive.Content
    ref={ref}
    className={cn('mt-4 focus-visible:outline-none', className)}
    {...props}
  />
));
TabsContent.displayName = TabsPrimitive.Content.displayName;

export { Tabs, TabsList, TabsTrigger, TabsContent };
