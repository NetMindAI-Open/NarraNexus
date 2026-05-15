/**
 * @file_name: QuotaExceededModal.tsx
 * @description: Modal that pops when an agent tool call returns an
 * ArtifactQuotaExceeded error. Provides a single explicit jump-point to
 * Settings → Artifacts so the user can clear room without hunting through
 * the chat to figure out what went wrong.
 *
 * Driven by `artifactStore.quotaError`: any non-null value renders the
 * modal; clicking the action or close button calls setQuotaError(null).
 *
 * Mounted once at the layout level (MainLayout). The modal is a portal-style
 * fixed overlay so it covers the entire viewport regardless of which route
 * the user happens to be on.
 */

import { useNavigate } from 'react-router-dom';
import { AlertTriangle } from 'lucide-react';
import { Button } from '@/components/ui';
import { useArtifactStore } from '@/stores';

export default function QuotaExceededModal() {
  const navigate = useNavigate();
  const message = useArtifactStore((s) => s.quotaError);
  const setQuotaError = useArtifactStore((s) => s.setQuotaError);

  if (!message) return null;

  const handleManage = () => {
    setQuotaError(null);
    navigate('/app/settings');
    // Settings → Artifacts is on the same page; the section heading and
    // anchored URL evolution are out of scope for this MVP.
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="quota-modal-title"
    >
      <div className="w-full max-w-md border border-[var(--border-default)] bg-[var(--bg-primary)] p-5 shadow-2xl">
        <div className="flex items-start gap-3 mb-3">
          <AlertTriangle className="w-5 h-5 text-amber-400 mt-0.5 shrink-0" />
          <div>
            <h2 id="quota-modal-title" className="text-base font-semibold mb-1">
              Artifact limit reached
            </h2>
            <p className="text-sm text-[var(--text-secondary)] whitespace-pre-line">
              {message}
            </p>
          </div>
        </div>
        <div className="flex justify-end gap-2 mt-4">
          <Button variant="outline" size="sm" onClick={() => setQuotaError(null)}>
            Dismiss
          </Button>
          <Button size="sm" onClick={handleManage}>
            Manage in Settings
          </Button>
        </div>
      </div>
    </div>
  );
}
