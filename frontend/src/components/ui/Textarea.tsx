/**
 * Textarea — Nordic archive style
 *
 * Flat 1px-ruled surface, no glow, no decoration.
 *
 * Auto-resize behaviour
 * ---------------------
 * Sets `overflow-y: hidden` and grows its height to fit content (capped at
 * the inline `maxHeight` style or a CSS-derived max-height class). Reason:
 * macOS WKWebView in "Always show scrollbars" mode renders the wide AppKit
 * NSScroller for any native overflow scroller — completely bypassing
 * `::-webkit-scrollbar`. There is no CSS workaround; the only way to keep
 * an empty textarea from showing a system scrollbar is to never let it
 * overflow. We do that by sizing the textarea to its content on every
 * change. Once content really exceeds max-height, the native scrollbar
 * appears (acceptable: that's a deliberate "you've typed a lot" signal).
 *
 * Pass `autoResize={false}` to opt out for fixed-height multi-line forms.
 */

import { forwardRef, useEffect, useRef, type TextareaHTMLAttributes } from 'react';
import { cn } from '../../lib/utils';

interface TextareaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  error?: boolean;
  /** Auto-resize to fit content (default: true). */
  autoResize?: boolean;
}

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(
  ({ className, error = false, autoResize = true, value, ...props }, ref) => {
    const innerRef = useRef<HTMLTextAreaElement | null>(null);

    // Combine the forwarded ref with our internal ref so callers still get
    // their ref, while we can also read/write the element for resize.
    const setRef = (node: HTMLTextAreaElement | null) => {
      innerRef.current = node;
      if (typeof ref === 'function') {
        ref(node);
      } else if (ref) {
        (ref as React.MutableRefObject<HTMLTextAreaElement | null>).current = node;
      }
    };

    const resize = () => {
      if (!autoResize) return;
      const el = innerRef.current;
      if (!el) return;
      // Reset height first so shrinking works (otherwise scrollHeight is
      // pinned to the previous, taller height).
      el.style.height = 'auto';
      const next = el.scrollHeight;
      // Read the computed max-height from the element itself — set by the
      // caller via Tailwind `max-h-[Xpx]` or inline style.
      const maxH = parseFloat(getComputedStyle(el).maxHeight || '0') || Infinity;
      // Pin to scrollHeight, but never above max-height. When content wins
      // (scrollHeight > maxH) we re-enable native overflow so nothing gets
      // clipped — that's the only case the user sees a scrollbar.
      const target = Math.min(next, maxH);
      el.style.height = `${target}px`;
      el.style.overflowY = next > maxH ? 'auto' : 'hidden';
    };

    // Resize whenever the controlled value changes externally — e.g. after
    // a successful send clears the field, or programmatic insert.
    useEffect(() => {
      resize();
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [value, autoResize]);

    return (
      <div className="relative">
        <textarea
          ref={setRef}
          value={value}
          onInput={(e) => {
            resize();
            props.onInput?.(e);
          }}
          className={cn(
            'w-full',
            'bg-[var(--bg-primary)]',
            'border border-[var(--border-default)]',
            'px-3 py-2.5',
            'text-sm text-[var(--text-primary)]',
            'placeholder:text-[var(--text-tertiary)] placeholder:font-light',
            'font-[family-name:var(--font-sans)]',
            'transition-colors duration-150',
            'resize-none min-h-[100px]',

            'focus:outline-none',
            'focus:border-[var(--text-primary)]',

            'hover:border-[var(--border-strong)]',

            'disabled:opacity-50 disabled:cursor-not-allowed disabled:bg-[var(--bg-secondary)]',

            error && [
              'border-[var(--color-red-500)]',
              'focus:border-[var(--color-red-500)]',
            ],

            className
          )}
          // overflowY is managed dynamically inside `resize()`: hidden while
          // content fits (no system scrollbar can appear), auto only when
          // content actually exceeds max-height. autoResize=false falls back
          // to native overflow.
          style={{
            borderRadius: 0,
            overflowY: autoResize ? 'hidden' : 'auto',
          }}
          {...props}
        />
      </div>
    );
  }
);

Textarea.displayName = 'Textarea';
