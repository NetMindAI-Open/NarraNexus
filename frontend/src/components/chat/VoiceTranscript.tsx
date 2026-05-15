/**
 * @file_name: VoiceTranscript.tsx
 * @description: Renders a Whisper-transcribed voice memo as text.
 *
 * Replaces the earlier AudioMessage component that shipped a native
 * `<audio>` player. Per product direction: when the user records via
 * the in-browser AudioRecorder, the transcript IS the message — there
 * is no playback, no "verify what I said" loop. The recording is
 * effectively dictation, and the bubble just shows the transcribed
 * text styled to make it clear it came from voice rather than typing.
 *
 * Plain audio file uploads (Paperclip / drag-drop / paste) intentionally
 * do NOT route through this component — they get the regular file chip
 * because the user is sharing a file, not dictating. The discriminator
 * is `att.source === 'recording'`. The backend transcribes ALL audio/*
 * uploads regardless of source — `source` purely controls how the
 * frontend renders the bubble (this component vs. file chip).
 */

import { Mic, MicOff } from 'lucide-react';
import { cn } from '@/lib/utils';

interface VoiceTranscriptProps {
  /** Whisper transcript text. Null/empty renders the "unavailable"
   *  fallback so the user sees their recording wasn't dropped — it
   *  just couldn't be transcribed. */
  transcript?: string | null;
  /** Compact layout for the pendingAttachments preview row above the
   *  textarea: shorter chip, transcript truncated to one line, no
   *  surrounding container styling. The full layout in the chat
   *  history bubble shows the transcript on its own line with proper
   *  wrapping. */
  compact?: boolean;
  className?: string;
}

export function VoiceTranscript({
  transcript,
  compact = false,
  className,
}: VoiceTranscriptProps) {
  const text = transcript?.trim() ?? '';
  const hasText = text.length > 0;

  if (compact) {
    return (
      <div
        className={cn(
          'flex items-center gap-2 min-w-0 max-w-[260px]',
          className,
        )}
      >
        <div className="w-7 h-7 rounded-full bg-[var(--bg-secondary)] flex items-center justify-center shrink-0">
          {hasText ? (
            <Mic className="w-3.5 h-3.5 text-[var(--text-secondary)]" />
          ) : (
            <MicOff className="w-3.5 h-3.5 text-[var(--text-tertiary)]" />
          )}
        </div>
        <div className="min-w-0 leading-tight">
          <div className="text-[10px] text-[var(--text-tertiary)] font-[family-name:var(--font-mono)] uppercase tracking-[0.1em]">
            voice
          </div>
          <div
            className={cn(
              'text-xs truncate',
              hasText
                ? 'text-[var(--text-primary)]'
                : 'italic text-[var(--text-tertiary)]',
            )}
          >
            {hasText ? text : 'transcription unavailable'}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div
      className={cn(
        'flex flex-col gap-1.5 max-w-[420px] rounded-md border border-[var(--rule)] bg-[var(--bg-tertiary)]/40 px-3 py-2',
        className,
      )}
    >
      <div className="flex items-center gap-1.5">
        {hasText ? (
          <Mic className="w-3.5 h-3.5 shrink-0 text-[var(--text-secondary)]" />
        ) : (
          <MicOff className="w-3.5 h-3.5 shrink-0 text-[var(--text-tertiary)]" />
        )}
        <span className="text-[10px] uppercase tracking-[0.12em] text-[var(--text-tertiary)] font-[family-name:var(--font-mono)]">
          voice
        </span>
      </div>
      {hasText ? (
        <div className="text-sm leading-relaxed whitespace-pre-wrap break-words text-[var(--text-primary)]">
          {text}
        </div>
      ) : (
        <div className="text-xs italic text-[var(--text-tertiary)]">
          Transcription unavailable. Add an OpenAI API key under
          Settings → Providers to enable voice transcription.
        </div>
      )}
    </div>
  );
}
