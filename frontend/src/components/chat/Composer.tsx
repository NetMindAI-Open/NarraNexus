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
  useImperativeHandle,
  useRef,
  useState,
} from 'react';
import { Textarea } from '@/components/ui';
import { getChatDraft, setChatDraft } from '@/lib/chatDrafts';

export interface ComposerHandle {
  /** Current textarea value (read at send time). */
  getText: () => string;
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
    },
    ref,
  ) {
    const [text, setText] = useState(() => getChatDraft(agentId ?? ''));
    const textRef = useRef(text);
    textRef.current = text;
    const wasEmptyRef = useRef(text.trim().length === 0);
    const isComposingRef = useRef(false);
    const compositionEndTimeRef = useRef(0);

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
        <Textarea
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
          className="min-h-[52px] max-h-[160px] py-[14px] leading-[24px] resize-none"
          rows={1}
        />
      </div>
    );
  }),
);
