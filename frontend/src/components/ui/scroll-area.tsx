/**
 * @file_name: scroll-area.tsx
 * @description: ScrollArea — Nordic archive scrollbar.
 *
 * WHY THIS EXISTS
 * ---------------
 * macOS users with System Settings → Appearance → Show scroll bars set to
 * "Always" trigger WebKit's AppKit NSScroller code path, which COMPLETELY
 * bypasses `::-webkit-scrollbar` CSS rules. No amount of CSS — `!important`,
 * `-webkit-appearance: none`, attribute selectors — can override that
 * fallback. The native scroll bar shows up wide and light-grey, breaking
 * the dark archive aesthetic.
 *
 * The fix: use `overflow: hidden` + JS-rendered scroll thumb (Radix UI's
 * ScrollArea primitive). The page never asks the OS to draw a scrollbar,
 * so no system fallback can hijack it. Every scroll surface in the app
 * routes through this component for consistent, theme-aware scrollbars.
 *
 * USAGE
 * -----
 *   <ScrollArea className="flex-1">
 *     {children}
 *   </ScrollArea>
 *
 * For chat-style auto-scroll-to-bottom, pass `viewportRef` and treat it
 * as the scrollable container (read/write `scrollTop`, attach `onScroll`):
 *
 *   const ref = useRef<HTMLDivElement>(null);
 *   <ScrollArea viewportRef={ref} onViewportScroll={...}>
 *     {messages}
 *   </ScrollArea>
 *   useEffect(() => { ref.current!.scrollTop = ref.current!.scrollHeight }, ...);
 */

import * as React from 'react';
import * as ScrollAreaPrimitive from '@radix-ui/react-scroll-area';
import { cn } from '../../lib/utils';

interface ScrollAreaProps
  extends React.ComponentPropsWithoutRef<typeof ScrollAreaPrimitive.Root> {
  /** Forward a ref to the inner Viewport — the actual scrolling element. */
  viewportRef?: React.Ref<HTMLDivElement>;
  /** Tailwind classes applied to the Viewport (use this to add padding etc). */
  viewportClassName?: string;
  /** Native scroll handler attached to the Viewport. */
  onViewportScroll?: React.UIEventHandler<HTMLDivElement>;
  /** Show a horizontal scrollbar when content overflows on X. */
  horizontal?: boolean;
  /** Hide the scrollbar entirely (useful for tabs that scroll horizontally
   *  but shouldn't show a track). */
  hideScrollbar?: boolean;
}

const ScrollArea = React.forwardRef<
  React.ElementRef<typeof ScrollAreaPrimitive.Root>,
  ScrollAreaProps
>(
  (
    {
      className,
      viewportClassName,
      viewportRef,
      onViewportScroll,
      horizontal = false,
      hideScrollbar = false,
      children,
      ...props
    },
    ref
  ) => (
    <ScrollAreaPrimitive.Root
      ref={ref}
      className={cn('relative overflow-hidden', className)}
      // `scrollHideDelay` 600ms feels less twitchy than the 300ms default.
      scrollHideDelay={600}
      {...props}
    >
      <ScrollAreaPrimitive.Viewport
        ref={viewportRef}
        onScroll={onViewportScroll}
        // `display: block` on Viewport is critical: Radix sets it to `table`
        // by default which breaks `flex-1` parents and `min-h-0` chains.
        className={cn('h-full w-full [&>div]:!block', viewportClassName)}
      >
        {children}
      </ScrollAreaPrimitive.Viewport>
      {!hideScrollbar && <ScrollBar orientation="vertical" />}
      {!hideScrollbar && horizontal && <ScrollBar orientation="horizontal" />}
      <ScrollAreaPrimitive.Corner />
    </ScrollAreaPrimitive.Root>
  )
);
ScrollArea.displayName = 'ScrollArea';

const ScrollBar = React.forwardRef<
  React.ElementRef<typeof ScrollAreaPrimitive.ScrollAreaScrollbar>,
  React.ComponentPropsWithoutRef<typeof ScrollAreaPrimitive.ScrollAreaScrollbar>
>(({ className, orientation = 'vertical', ...props }, ref) => (
  <ScrollAreaPrimitive.ScrollAreaScrollbar
    ref={ref}
    orientation={orientation}
    className={cn(
      // Touch + transition base
      'flex touch-none select-none transition-opacity',
      // Vertical track — 8px wide, sits flush at the right edge
      orientation === 'vertical' && 'h-full w-2 p-0',
      // Horizontal track — 8px tall, sits flush at the bottom edge
      orientation === 'horizontal' && 'h-2 w-full flex-col p-0',
      // Hidden until interaction (Radix toggles `data-state`)
      'data-[state=hidden]:opacity-0',
      'data-[state=visible]:opacity-100',
      className
    )}
    {...props}
  >
    <ScrollAreaPrimitive.ScrollAreaThumb
      className={cn(
        // Square (Nordic archive: no rounded), theme-aware via CSS variables
        'relative flex-1',
        'bg-[var(--border-default)]',
        'hover:bg-[var(--text-tertiary)]',
        'transition-colors duration-150',
        // The `before` pseudo widens the hit-target so users don't have to
        // hit a 6px-wide thumb precisely.
        'before:content-[""] before:absolute before:left-1/2 before:top-1/2',
        'before:-translate-x-1/2 before:-translate-y-1/2',
        'before:min-w-[24px] before:min-h-[24px] before:w-full before:h-full'
      )}
    />
  </ScrollAreaPrimitive.ScrollAreaScrollbar>
));
ScrollBar.displayName = 'ScrollBar';

export { ScrollArea, ScrollBar };
