/**
 * @file_name: AttachmentImage.tsx
 * @description: Inline image renderer for JWT-protected attachments.
 *
 * Wraps `useAttachmentBlobUrl` with a thin presentation layer so callers
 * can swap a plain `<img src=raw_url>` for `<AttachmentImage .../>`
 * without changing surrounding markup. Hooks can't run inside `.map()`,
 * so this component exists to be the per-item hook host.
 */

import { Image as ImageIcon } from 'lucide-react';
import { useAttachmentBlobUrl } from '@/hooks/useAttachmentBlobUrl';
import { cn } from '@/lib/utils';

interface AttachmentImageProps {
  agentId: string;
  userId: string;
  fileId: string;
  alt: string;
  className?: string;
  /** When true and a blob URL is available, wrap the <img> in an <a> that
   *  opens the image in a new tab. The <a> uses the same blob URL, which
   *  is valid for navigation as long as this component stays mounted. */
  zoomable?: boolean;
}

export function AttachmentImage({
  agentId,
  userId,
  fileId,
  alt,
  className,
  zoomable = false,
}: AttachmentImageProps) {
  const blobUrl = useAttachmentBlobUrl(agentId, userId, fileId);

  if (!blobUrl) {
    return (
      <div
        className={cn(
          'flex items-center justify-center bg-[var(--bg-secondary)] text-[var(--text-tertiary)]',
          className,
        )}
        role="img"
        aria-label={alt}
      >
        <ImageIcon className="w-4 h-4" />
      </div>
    );
  }

  const img = <img src={blobUrl} alt={alt} className={className} />;
  if (zoomable) {
    return (
      <a href={blobUrl} target="_blank" rel="noopener noreferrer" className="block">
        {img}
      </a>
    );
  }
  return img;
}
