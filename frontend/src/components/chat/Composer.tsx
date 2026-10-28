/**
 * Composer — the chat message textarea, isolated from ChatPanel.
 *
 * Why this exists
 * ---------------
 * The draft text used to be `input` state living directly in ChatPanel (a
 * ~1300-line component that also renders the whole message timeline and
 * subscribes to the entire chat store). Every keystroke called `setInput`,
 * re-rendering that monolith; and because ChatPanel re-renders on every
 * streaming delta too, typing *while an agent streamed* (esp. one-char-per-
 * token models) made the two re-render storms collide and the input lag.
 *
 * Pulling the text state into this small memoized child means:
 *   - a keystroke re-renders only <Composer>, never the timeline; and
 *   - as long as ChatPanel passes STABLE props (useCallback'd handlers),
 *     <Composer> does not re-render during streaming either — so typing
 *     stays smooth no matter what the agent is doing.
 *
 * ChatPanel reads the current text imperatively (getText) on send and clears
 * it (clear) after a successful send. Send-button emptiness is reported via
 * `onEmptyChange`, which fires only on empty↔non-empty transitions (not every
 * keystroke), so the button stays correct without re-rendering ChatPanel per
 * character.
 *
 * Draft persistence is debounced here (was a synchronous localStorage write on
 * every keystroke) and flushed on unmount; ChatPanel remounts this component
 * via `key={agentId}` so each agent's draft restores cleanly.
 */
import {
  forwardRef,
  memo,
  useEffect,
  useCallback,
  useImperativeHandle,
  useLayoutEffect,
  useRef,
  useState,
} from 'react';
import { Textarea } from '@/components/ui';
import { cn } from '@/lib/utils';
import { getChatDraft, setChatDraft } from '@/lib/chatDrafts';
import { SlotOutlet } from '@/platform/SlotOutlet';
import { COMPOSER_EXTENSIONS } from '@/platform/registries';
import { useWhenContext } from '@/platform/whenContext';

export interface ComposerHandle {
  /** Current textarea value (read at send time). */
  getText: () => string;
  /** Replace the textarea value (e.g. a suggested-prompt chip) and focus it.
   *  Does not send — the user reviews then hits Enter. */
  setText: (value: string) => void;
  /** Clear the textarea + its persisted draft (after a successful send). */
  clear: () => void;
}

interface ComposerProps {
  agentId: string | null;
  /** Textarea stays editable while the agent runs; only *sending* is gated
   *  upstream. `disabled` is just "no agent selected". */
  disabled: boolean;
  placeholder: string;
  /** Enter (no Shift, not mid-IME) → ask ChatPanel to send. */
  onSubmit: () => void;
  /** Fires only when the trimmed-empty state flips, for the send button. */
  onEmptyChange?: (isEmpty: boolean) => void;
  // Drag/paste handlers are owned by ChatPanel (also bound to the wrapper
  // div) and must sit on the <textarea> too — the native default (insert
  // dropped file path / paste-as-text) wins otherwise.
  onDragOver: (e: React.DragEvent<HTMLElement>) => void;
  onDragLeave: (e: React.DragEvent<HTMLElement>) => void;
  onDrop: (e: React.DragEvent<HTMLElement>) => void;
  onPaste: (e: React.ClipboardEvent<HTMLTextAreaElement>) => void;
  /** How many action buttons overlay the textarea's right edge. The textarea
   *  reserves right padding so text never slides under them: 1 button (Send, or
   *  Stop alone) → pr-12; 2 buttons (a steerable run shows Stop + a steer Send)
   *  → pr-24. ChatPanel owns the buttons, so it tells the input how much room to
   *  keep. Defaults to 1 (the everyday single-button layout). */
  trailingSlots?: 1 | 2;
}

const DRAFT_PERSIST_DEBOUNCE_MS = 400;

export const Composer = memo(
  forwardRef<ComposerHandle, ComposerProps>(function Composer(
    {
      agentId,
      disabled,
      placeholder,
      onSubmit,
      onEmptyChange,
      onDragOver,
      onDragLeave,
      onDrop,
      onPaste,
      trailingSlots = 1,
    },
    ref,
  ) {
    const [text, setText] = useState(() => getChatDraft(agentId ?? ''));
    const textRef = useRef(text);
    textRef.current = text;
    const textareaRef = useRef<HTMLTextAreaElement>(null);
    const wasEmptyRef = useRef(text.trim().length === 0);
    const isComposingRef = useRef(false);
    const compositionEndTimeRef = useRef(0);
    const whenCtx = useWhenContext({ conversationKind: 'chat', agentId });

    const reportEmpty = (value: string) => {
      const empty = value.trim().length === 0;
      if (empty !== wasEmptyRef.current) {
        wasEmptyRef.current = empty;
        onEmptyChange?.(empty);
      }
    };

    useImperativeHandle(
      ref,
      () => ({
        getText: () => textRef.current,
        setText: (value: string) => {
          setText(value);
          reportEmpty(value);
          // Focus + drop the caret at the end so the user can keep typing
          // or hit Enter to send the filled-in prompt.
          requestAnimationFrame(() => {
            const el = textareaRef.current;
            if (el) {
              el.focus();
              el.setSelectionRange(value.length, value.length);
            }
          });
        },
        clear: () => {
          setText('');
          if (agentId) setChatDraft(agentId, '');
          reportEmpty('');
        },
      }),
      // reportEmpty/onEmptyChange are stable enough; agentId is the only
      // value the closure must track.
      // eslint-disable-next-line react-hooks/exhaustive-deps
      [agentId],
    );

    // Report initial emptiness once on mount (covers a restored non-empty
    // draft after an agent switch so the send button enables correctly).
    useEffect(() => {
      onEmptyChange?.(textRef.current.trim().length === 0);
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    // Debounced draft persistence (replaces the old per-keystroke sync write).
    useEffect(() => {
      if (!agentId) return;
      const id = setTimeout(
        () => setChatDraft(agentId, text),
        DRAFT_PERSIST_DEBOUNCE_MS,
      );
      return () => clearTimeout(id);
    }, [text, agentId]);

    // Flush the draft immediately on unmount (agent switch remounts via key)
    // so text typed inside the debounce window isn't lost.
    useEffect(() => {
      return () => {
        if (agentId) setChatDraft(agentId, textRef.current);
      };
    }, [agentId]);

    // Grow the field with its content: height tracks scrollHeight (plus the
    // border, which border-box height includes but scrollHeight does not),
    // the CSS max-height caps it, and past the cap the textarea scrolls
    // internally. Collapsing to 'auto' first lets it shrink again when
    // lines are deleted.
    const resizeToContent = useCallback(() => {
      const el = textareaRef.current;
      if (!el) return;
      el.style.height = 'auto';
      // Border must be read AFTER the collapse to auto, and added back:
      // border-box height includes it, scrollHeight does not.
      const borderPx = el.offsetHeight - el.clientHeight;
      el.style.height = `${el.scrollHeight + borderPx}px`;
    }, []);
    // useLayoutEffect (not the change handler) so programmatic setText /
    // clear and the restored draft resize the same way typing does.
    useLayoutEffect(() => {
      resizeToContent();
    }, [text, resizeToContent]);
    // Width changes rewrap the text (drawer drag, pin toggle, sidebar
    // collapse, window resize) — the height must follow those too. The
    // observer recomputes ONLY when the width changed: this callback only
    // writes height, so filtering on width is what actually breaks the
    // self-feedback loop (the write itself re-fires the observer).
    useEffect(() => {
      const el = textareaRef.current;
      if (!el || typeof ResizeObserver === 'undefined') return;
      // -1 so the initial delivery from observe() always runs, even in the
      // edge case of a zero-width hidden container.
      let lastWidth = -1;
      const ro = new ResizeObserver((entries) => {
        const width = entries[0].contentRect.width;
        if (width === lastWidth) return;
        lastWidth = width;
        resizeToContent();
      });
      ro.observe(el);
      return () => ro.disconnect();
    }, [resizeToContent]);

    const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
      const v = e.target.value;
      setText(v);
      reportEmpty(v);
    };

    const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      const isIMEComposing = e.nativeEvent.isComposing || isComposingRef.current;
      const justFinishedComposition =
        Date.now() - compositionEndTimeRef.current < 100;
      if (e.key === 'Enter' && !e.shiftKey) {
        if (isIMEComposing || justFinishedComposition) return;
        e.preventDefault();
        onSubmit();
      }
    };

    return (
      <div className="flex-1 relative">
        {/* Plugin controls above the input (ui.composerExtensions), gated by `when`. */}
        <SlotOutlet registry={COMPOSER_EXTENSIONS} ctx={whenCtx} agentId={agentId} as="div" className="flex flex-wrap items-center gap-2 px-1 pb-1" />
        <Textarea
          ref={textareaRef}
          value={text}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          onCompositionStart={() => {
            isComposingRef.current = true;
          }}
          onCompositionUpdate={() => {
            isComposingRef.current = true;
          }}
          onCompositionEnd={() => {
            compositionEndTimeRef.current = Date.now();
            setTimeout(() => {
              isComposingRef.current = false;
            }, 0);
          }}
          onDragOver={onDragOver}
          onDragLeave={onDragLeave}
          onDrop={onDrop}
          onPaste={onPaste}
          placeholder={placeholder}
          disabled={disabled}
          // Input-field convention: the field is the LIGHTEST surface on
          // the card and focus is signalled by the border deepening — so bg
          // overrides the Textarea base to nm-card, and the base's
          // hover(border-strong)/focus(ink) treatments are no longer pinned
          // back to the hairline. Height is managed by the autosize effect;
          // max-h caps it at ~a third of the viewport before it scrolls.
          className={cn(
            'nx-composer-input block min-h-[52px] max-h-[min(320px,35vh)] overflow-y-auto py-[14px] leading-[24px] resize-none bg-[color:var(--nm-card)]',
            // Reserve room for the button(s) pinned over the right edge so typed
            // text never slides underneath them. 2 buttons (steerable run:
            // Stop @ right-2 + steer Send @ right-12, each w-9) span 8–84px, so
            // pr-24 (96px); the single-button default spans 8–44px → pr-12.
            trailingSlots === 2 ? 'pr-24' : 'pr-12',
          )}
          rows={1}
        />
      </div>
    );
  }),
);
