/**
 * AudioRecorder — single-button voice capture for the chat input bar.
 *
 * State machine
 * -------------
 *   idle ──tap──> requesting ──granted──> recording ──tap──> finalizing
 *                              denied──> denied (toast, back to idle on retry)
 *   unsupported (no MediaRecorder API) → button hidden so the layout
 *                                        on iOS < 14.3 falls back gracefully.
 *
 * MIME selection — browsers disagree:
 *   - Chrome / Edge / Firefox prefer `audio/webm;codecs=opus`
 *   - Safari (iOS 14.3+, macOS 14.1+) only supports `audio/mp4`
 *   - Older Firefox: `audio/ogg;codecs=opus`
 * We probe via `MediaRecorder.isTypeSupported` and fall through to the
 * empty default (browser-picked) when nothing matches. The chosen MIME
 * decides the file extension so the upload route's libmagic sniffer
 * can identify it and `audio_transcription.SUPPORTED_AUDIO_EXTENSIONS`
 * accepts it.
 *
 * Lifecycle gotchas
 * -----------------
 * - Always `track.stop()` on every getUserMedia track when recording
 *   ends. Otherwise the browser tab keeps the red mic indicator lit
 *   even after the UI returns to idle.
 * - Re-build the chunk array (`chunksRef.current = []`) on every start.
 *   MediaRecorder fires `ondataavailable` repeatedly; reusing the array
 *   across sessions would concatenate prior recordings.
 * - `onstop` resolves the final blob — assemble it from the chunks
 *   buffer there, not in `ondataavailable`, because the last chunk
 *   only arrives at stop time.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { Mic, Square, AlertCircle } from 'lucide-react';
import { Button } from '@/components/ui';
import { cn } from '@/lib/utils';

interface AudioRecorderProps {
  disabled?: boolean;
  onRecorded: (file: File) => void;
  /**
   * Optional surface for permission denial / device errors. ChatPanel
   * routes this into the same banner used for "no OpenAI provider"
   * so users see one consistent voice-status surface.
   */
  onError?: (message: string) => void;
  /**
   * Whether this user has any transcription-capable provider on the
   * backend. ``undefined`` means "still loading" — the button stays
   * enabled and click is allowed (the post-upload banner takes over
   * if the racing probe says no). ``false`` means we KNOW the user
   * has nothing configured: the button stays visible but click opens
   * a Dialog instead of starting MediaRecorder.
   */
  available?: boolean;
  /** Fires when the user clicks the mic but ``available === false``.
   *  ChatPanel uses this to open a "configure a provider" dialog. */
  onUnavailable?: () => void;
  /**
   * Click-time re-probe. ChatPanel passes a callback that re-fetches
   * `/api/transcription/availability`, updates its cached state, and
   * returns `false` when the freshly-resolved state says we should
   * NOT record (e.g. user just toggled off "Use free quota" in
   * Settings between mount and now). When it returns `false`, the
   * parent has already handled the dialog — we just bail.
   *
   * Why this is needed: the `available` prop is set by a useEffect
   * that only re-runs when `userId` changes; toggling the quota
   * preference doesn't change userId, so the cached value can
   * out-live the underlying capability. A click-time refresh
   * eliminates that staleness window.
   */
  onPreflight?: () => Promise<boolean>;
}

type RecorderState = 'idle' | 'requesting' | 'recording' | 'denied' | 'unsupported';

interface MimeChoice {
  mime: string;
  ext: string;
}

const MIME_CANDIDATES: MimeChoice[] = [
  { mime: 'audio/webm;codecs=opus', ext: 'webm' },
  { mime: 'audio/webm', ext: 'webm' },
  { mime: 'audio/mp4', ext: 'mp4' },
  { mime: 'audio/ogg;codecs=opus', ext: 'ogg' },
];

function pickMimeType(): MimeChoice {
  if (typeof MediaRecorder === 'undefined') {
    return { mime: '', ext: 'webm' };
  }
  for (const c of MIME_CANDIDATES) {
    try {
      if (MediaRecorder.isTypeSupported(c.mime)) return c;
    } catch {
      // isTypeSupported throws on Safari for some unknown strings
    }
  }
  return { mime: '', ext: 'webm' };
}

function formatElapsed(ms: number): string {
  const totalSec = Math.floor(ms / 1000);
  const m = Math.floor(totalSec / 60);
  const s = totalSec % 60;
  return `${m}:${s.toString().padStart(2, '0')}`;
}

export function AudioRecorder({
  disabled,
  onRecorded,
  onError,
  available,
  onUnavailable,
  onPreflight,
}: AudioRecorderProps) {
  const [state, setState] = useState<RecorderState>(() =>
    typeof MediaRecorder === 'undefined' ? 'unsupported' : 'idle',
  );
  const [elapsed, setElapsed] = useState(0);

  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const startedAtRef = useRef<number>(0);
  const tickRef = useRef<number | null>(null);
  const mimeRef = useRef<MimeChoice>({ mime: '', ext: 'webm' });

  const cleanupStream = useCallback(() => {
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    if (tickRef.current !== null) {
      window.clearInterval(tickRef.current);
      tickRef.current = null;
    }
  }, []);

  // Stop tracks on unmount so the OS-level mic indicator doesn't
  // linger after the user navigates away mid-recording.
  useEffect(() => {
    return () => cleanupStream();
  }, [cleanupStream]);

  const startRecording = useCallback(async () => {
    if (state === 'recording' || state === 'requesting') return;
    if (state === 'unsupported') return;

    // Click-time pre-flight: when the cached prop already says no
    // provider, fail-fast without a network round-trip.
    if (available === false) {
      onUnavailable?.();
      return;
    }

    // Click-time RE-probe via parent callback. Catches the staleness
    // case where `available` was true at mount but the user toggled
    // off "Use free quota" before clicking the mic. Parent decides
    // (and renders the dialog) — we just respect the verdict.
    if (onPreflight) {
      try {
        const ok = await onPreflight();
        if (!ok) return;
      } catch {
        // Probe network failure → don't block recording; the
        // upload-time error path will still catch a hard 402.
      }
    }

    setState('requesting');
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;

      const choice = pickMimeType();
      mimeRef.current = choice;
      const recorder = choice.mime
        ? new MediaRecorder(stream, { mimeType: choice.mime })
        : new MediaRecorder(stream);

      chunksRef.current = [];
      recorder.ondataavailable = (e: BlobEvent) => {
        if (e.data && e.data.size > 0) chunksRef.current.push(e.data);
      };
      recorder.onstop = () => {
        const effectiveMime =
          recorder.mimeType || choice.mime || 'audio/webm';
        const blob = new Blob(chunksRef.current, { type: effectiveMime });
        chunksRef.current = [];
        const filename = `voice_${Date.now()}.${choice.ext}`;
        const file = new File([blob], filename, { type: effectiveMime });
        cleanupStream();
        setState('idle');
        setElapsed(0);
        if (file.size > 0) onRecorded(file);
      };

      recorderRef.current = recorder;
      startedAtRef.current = Date.now();
      tickRef.current = window.setInterval(() => {
        setElapsed(Date.now() - startedAtRef.current);
      }, 250);

      recorder.start(250);
      setState('recording');
    } catch (e) {
      cleanupStream();
      const err = e instanceof Error ? e.name : 'unknown';
      if (err === 'NotAllowedError' || err === 'SecurityError') {
        setState('denied');
        onError?.(
          'Microphone access denied. Enable mic permission in your browser settings, then try again.',
        );
      } else if (err === 'NotFoundError' || err === 'OverconstrainedError') {
        setState('idle');
        onError?.('No microphone detected.');
      } else {
        setState('idle');
        onError?.(`Recording failed: ${e instanceof Error ? e.message : String(e)}`);
      }
    }
  }, [state, cleanupStream, onRecorded, onError, available, onUnavailable, onPreflight]);

  const stopRecording = useCallback(() => {
    if (recorderRef.current && recorderRef.current.state !== 'inactive') {
      recorderRef.current.stop();  // triggers onstop → assembles blob
    } else {
      cleanupStream();
      setState('idle');
      setElapsed(0);
    }
  }, [cleanupStream]);

  if (state === 'unsupported') {
    return null;
  }

  if (state === 'recording') {
    return (
      <Button
        variant="danger"
        onClick={stopRecording}
        className="shrink-0 h-[52px] px-3 gap-2 font-[family-name:var(--font-mono)] text-xs uppercase tracking-[0.1em]"
        title="Stop recording"
      >
        <span className="relative inline-flex w-2.5 h-2.5">
          <span className="absolute inset-0 rounded-full bg-white opacity-75 animate-ping" />
          <span className="relative inline-flex rounded-full w-2.5 h-2.5 bg-white" />
        </span>
        <span className="tabular-nums">{formatElapsed(elapsed)}</span>
        <Square className="w-3.5 h-3.5 fill-current" />
      </Button>
    );
  }

  if (state === 'denied') {
    return (
      <Button
        variant="ghost"
        size="icon"
        onClick={() => {
          // Allow retry — getUserMedia will re-prompt if the user
          // granted/changed permission via browser UI.
          setState('idle');
          startRecording();
        }}
        disabled={disabled}
        className={cn(
          'shrink-0 h-[52px] w-[52px]',
          'text-[color:var(--color-error)]',
        )}
        title="Microphone access denied — click to retry after enabling permission"
      >
        <AlertCircle className="w-4 h-4" />
      </Button>
    );
  }

  return (
    <Button
      variant="ghost"
      size="icon"
      onClick={startRecording}
      disabled={disabled || state === 'requesting'}
      className="shrink-0 h-[52px] w-[52px]"
      title="Record voice message"
    >
      <Mic className="w-4 h-4" />
    </Button>
  );
}
