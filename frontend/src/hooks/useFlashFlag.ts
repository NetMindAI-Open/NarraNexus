/**
 * @file_name: useFlashFlag.ts
 * @date: 2026-09-10
 * @description: A boolean that turns itself off a fixed time after it was
 * last raised — the "✓ Saved" confirmation shared by the model editors
 * (AgentLlmConfigPanel, ModelDefaultsSettings).
 *
 * Raising the flag again restarts the countdown instead of letting the
 * earlier timer cut the newer confirmation short, and the pending timer is
 * cleared on unmount.
 */
import { useCallback, useEffect, useRef, useState } from 'react';

export function useFlashFlag(durationMs: number): [boolean, () => void] {
  const [on, setOn] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const flash = useCallback(() => {
    if (timer.current !== null) clearTimeout(timer.current);
    setOn(true);
    timer.current = setTimeout(() => {
      timer.current = null;
      setOn(false);
    }, durationMs);
  }, [durationMs]);

  useEffect(() => () => {
    if (timer.current !== null) clearTimeout(timer.current);
  }, []);

  return [on, flash];
}
