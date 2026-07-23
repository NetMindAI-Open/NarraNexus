/**
 * BusAttachmentList tests — bus-message attachment rendering.
 *
 * A non-image attachment renders as a downloadable chip (name + size);
 * clicking it fetches the blob via the shared-area endpoint. An image
 * attachment renders through the blob thumbnail branch. Empty/undefined
 * renders nothing.
 */
import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

const fetchBusAttachmentBlobMock = vi.fn(() => Promise.resolve(new Blob(['x'])));

vi.mock('@/lib/api', () => ({
  api: { fetchBusAttachmentBlob: (...a: unknown[]) => fetchBusAttachmentBlobMock(...a) },
}));

// Keep the image branch deterministic: no real fetch, just a null blob URL
// so it renders the placeholder rather than an <img>.
vi.mock('@/hooks/useBusAttachmentBlobUrl', () => ({
  useBusAttachmentBlobUrl: () => null,
}));

import { BusAttachmentList } from '@/components/chat/BusAttachmentList';
import type { BusAttachment } from '@/types';

const doc: BusAttachment = {
  file_id: 'att_1234abcd',
  mime_type: 'application/pdf',
  original_name: 'report.pdf',
  size_bytes: 2048,
  category: 'document',
  rel_path: 'user_a/_shared/bus_files/2026-07-20/att_1234abcd.pdf',
};
const img: BusAttachment = { ...doc, file_id: 'att_5678efab', original_name: 'chart.png', category: 'image' };

beforeEach(() => fetchBusAttachmentBlobMock.mockClear());

describe('BusAttachmentList', () => {
  test('renders nothing when empty', () => {
    const { container } = render(<BusAttachmentList attachments={[]} />);
    expect(container.firstChild).toBeNull();
    const { container: c2 } = render(<BusAttachmentList attachments={null} />);
    expect(c2.firstChild).toBeNull();
  });

  test('renders a downloadable chip for a document', () => {
    render(<BusAttachmentList attachments={[doc]} />);
    expect(screen.getByText('report.pdf')).toBeTruthy();
    expect(screen.getByText(/document · 2 KB/i)).toBeTruthy();
    fireEvent.click(screen.getByRole('button'));
    expect(fetchBusAttachmentBlobMock).toHaveBeenCalledWith(doc.rel_path);
  });

  test('image attachment renders the thumbnail branch (placeholder until blob)', () => {
    const { container } = render(<BusAttachmentList attachments={[img]} />);
    // No chip button for images; the thumbnail placeholder is shown instead.
    expect(screen.queryByRole('button')).toBeNull();
    expect(container.querySelector('img')).toBeNull(); // blob url is null → placeholder
  });
});
