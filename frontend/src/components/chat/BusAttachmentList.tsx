/**
 * @file_name: BusAttachmentList.tsx
 * @description: Render files attached to a message-bus message (agent-to-agent
 *   DM / team group chat) as chips, with authed download. Images additionally
 *   show an inline thumbnail.
 *
 * Bus counterpart to the inline attachment block in MessageBubble. Bus files
 * live in the per-user shared area and are fetched by `rel_path` via
 * `useBusAttachmentBlobUrl` / `api.fetchBusAttachmentBlob` (the plain chat
 * `AttachmentImage` is agent+file_id scoped and can't reach them).
 */

import { Download, FileText, Image as ImageIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { api } from '@/lib/api';
import { useBusAttachmentBlobUrl } from '@/hooks/useBusAttachmentBlobUrl';
import { VoiceTranscript } from './VoiceTranscript';
import type { BusAttachment } from '@/types';

async function downloadBusAttachment(att: BusAttachment): Promise<void> {
  const blob = await api.fetchBusAttachmentBlob(att.rel_path);
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = att.original_name || 'file';
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function BusAttachmentImageThumb({ att }: { att: BusAttachment }) {
  const blobUrl = useBusAttachmentBlobUrl(att.rel_path);
  if (!blobUrl) {
    return (
      <div className="flex h-24 w-24 items-center justify-center rounded border border-[var(--rule)] bg-[var(--bg-secondary)]">
        <ImageIcon className="h-5 w-5 text-[var(--text-tertiary)]" />
      </div>
    );
  }
  return (
    <a href={blobUrl} target="_blank" rel="noopener noreferrer" title={att.original_name} className="block">
      <img
        src={blobUrl}
        alt={att.original_name}
        className="max-h-48 max-w-[280px] rounded border border-[var(--rule)] object-cover"
      />
    </a>
  );
}

function BusAttachmentChip({ att }: { att: BusAttachment }) {
  const { t } = useTranslation();
  return (
    <button
      type="button"
      onClick={() => downloadBusAttachment(att).catch(() => {})}
      title={t('chat.attachments.downloadTitle', { name: att.original_name })}
      className="flex max-w-[280px] items-center gap-2 rounded-md border border-[var(--rule)] bg-[var(--bg-tertiary)]/40 px-2 py-1.5 text-left hover:bg-[var(--bg-tertiary)]/70"
    >
      <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded bg-[var(--bg-secondary)]">
        {att.category === 'image' ? (
          <ImageIcon className="h-4 w-4 text-[var(--text-tertiary)]" />
        ) : (
          <FileText className="h-4 w-4 text-[var(--text-tertiary)]" />
        )}
      </div>
      <div className="min-w-0 leading-tight">
        <div className="truncate text-xs">{att.original_name}</div>
        <div className="font-mono text-[10px] uppercase tracking-[0.1em] text-[var(--text-tertiary)]">
          {att.category} · {Math.max(1, Math.round(att.size_bytes / 1024))} KB
        </div>
      </div>
      <Download className="ml-auto h-3.5 w-3.5 shrink-0 text-[var(--text-tertiary)]" />
    </button>
  );
}

export function BusAttachmentList({ attachments }: { attachments?: BusAttachment[] | null }) {
  if (!attachments || attachments.length === 0) return null;
  return (
    <div className="mt-1.5 flex flex-wrap gap-2">
      {attachments.map((att) =>
        att.source === 'recording' ? (
          <VoiceTranscript key={att.file_id} transcript={att.transcript} />
        ) : att.category === 'image' ? (
          <BusAttachmentImageThumb key={att.file_id} att={att} />
        ) : (
          <BusAttachmentChip key={att.file_id} att={att} />
        ),
      )}
    </div>
  );
}
