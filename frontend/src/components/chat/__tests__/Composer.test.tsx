/**
 * @file_name: Composer.test.tsx
 * @description: Behavior contract for the isolated chat <Composer>.
 *
 * Locks the pieces ChatPanel depends on after the typing-lag refactor:
 *   - typing updates the textarea (local state, not ChatPanel state)
 *   - Enter (no Shift) → onSubmit; Shift+Enter inserts a newline (no submit)
 *   - onEmptyChange fires only on the empty↔non-empty flip
 *   - the imperative handle getText()/clear() works (ChatPanel reads on send)
 *   - the per-agent draft restores on mount
 */
import { createRef } from 'react';
import { describe, expect, test, vi, beforeEach } from 'vitest';
import { render, fireEvent, act } from '@testing-library/react';
import { Composer, type ComposerHandle } from '../Composer';
import { setChatDraft, getChatDraft } from '@/lib/chatDrafts';

function renderComposer(overrides: Partial<React.ComponentProps<typeof Composer>> = {}) {
  const ref = createRef<ComposerHandle>();
  const onSubmit = vi.fn();
  const onEmptyChange = vi.fn();
  const noop = vi.fn();
  const utils = render(
    <Composer
      ref={ref}
      agentId="agent_x"
      disabled={false}
      placeholder="type…"
      onSubmit={onSubmit}
      onEmptyChange={onEmptyChange}
      onDragOver={noop}
      onDragLeave={noop}
      onDrop={noop}
      onPaste={noop}
      {...overrides}
    />,
  );
  const textarea = utils.container.querySelector('textarea') as HTMLTextAreaElement;
  return { ref, onSubmit, onEmptyChange, textarea, ...utils };
}

beforeEach(() => {
  localStorage.clear();
});

describe('Composer', () => {
  test('typing updates the textarea value and getText() reflects it', () => {
    const { textarea, ref } = renderComposer();
    fireEvent.change(textarea, { target: { value: 'hello' } });
    expect(textarea.value).toBe('hello');
    expect(ref.current?.getText()).toBe('hello');
  });

  test('Enter submits, Shift+Enter does not', () => {
    const { textarea, onSubmit } = renderComposer();
    fireEvent.change(textarea, { target: { value: 'hi' } });
    fireEvent.keyDown(textarea, { key: 'Enter', shiftKey: true });
    expect(onSubmit).not.toHaveBeenCalled();
    fireEvent.keyDown(textarea, { key: 'Enter', shiftKey: false });
    expect(onSubmit).toHaveBeenCalledTimes(1);
  });

  test('onEmptyChange fires on the empty↔non-empty transitions only', () => {
    const { textarea, onEmptyChange } = renderComposer();
    onEmptyChange.mockClear(); // ignore the mount-time initial report
    fireEvent.change(textarea, { target: { value: 'a' } });
    fireEvent.change(textarea, { target: { value: 'ab' } }); // still non-empty → no extra fire
    expect(onEmptyChange).toHaveBeenCalledTimes(1);
    expect(onEmptyChange).toHaveBeenLastCalledWith(false);
    fireEvent.change(textarea, { target: { value: '' } });
    expect(onEmptyChange).toHaveBeenCalledTimes(2);
    expect(onEmptyChange).toHaveBeenLastCalledWith(true);
  });

  test('clear() empties text and wipes the persisted draft', () => {
    const { textarea, ref } = renderComposer();
    fireEvent.change(textarea, { target: { value: 'draft text' } });
    act(() => ref.current?.clear());
    expect(textarea.value).toBe('');
    expect(ref.current?.getText()).toBe('');
    expect(getChatDraft('agent_x')).toBe('');
  });

  test('restores the per-agent draft on mount', () => {
    setChatDraft('agent_x', 'saved draft');
    const { textarea } = renderComposer();
    expect(textarea.value).toBe('saved draft');
  });
});
