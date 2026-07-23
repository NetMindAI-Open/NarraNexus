/**
 * @file_name: NewTabOmnibox.tsx
 * @description: The "new artifact tab" dialog — a single omnibox that covers
 * both ways to create a tab, browser-address-bar style:
 *
 *   - type/paste a URL + Enter  → opens a URL-tab artifact (application/x-url)
 *   - type anything else        → live-filters the agent's existing artifacts
 *                                 (including minimized / other-session ones);
 *                                 pick one to focus it
 *
 * One control, two intents — no mode switch for the user to think about. URL
 * detection is heuristic (has a scheme, or looks like host.tld/…); ambiguous
 * input just filters.
 */

import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Globe, Loader2 } from 'lucide-react';

import { Dialog, DialogContent } from '@/components/ui';
import { useArtifactStore } from '@/stores';
import { looksLikeUrl, normalizeUrl } from './urlHeuristics';

interface Props {
  agentId: string;
  isOpen: boolean;
  onClose: () => void;
}

export default function NewTabOmnibox({ agentId, isOpen, onClose }: Props) {
  const { t } = useTranslation();
  const artifacts = useArtifactStore((s) => s.artifacts);
  const setActive = useArtifactStore((s) => s.setActive);
  const restoreTab = useArtifactStore((s) => s.restoreTab);
  const minimizedTabIds = useArtifactStore((s) => s.minimizedTabIds);
  const openUrl = useArtifactStore((s) => s.openUrl);

  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const isUrl = looksLikeUrl(text);
  const matches = useMemo(() => {
    const q = text.trim().toLowerCase();
    if (!q || isUrl) return [];
    return artifacts.filter((a) => a.title.toLowerCase().includes(q)).slice(0, 8);
  }, [text, isUrl, artifacts]);

  const reset = () => { setText(''); setError(null); setBusy(false); };
  const close = () => { reset(); onClose(); };

  const pick = (artifactId: string) => {
    if (minimizedTabIds.has(artifactId)) restoreTab(artifactId);
    else setActive(artifactId);
    close();
  };

  const submitUrl = async () => {
    if (!isUrl || busy) return;
    setBusy(true);
    setError(null);
    try {
      await openUrl(agentId, normalizeUrl(text));
      close();
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
      setBusy(false);
    }
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      if (isUrl) void submitUrl();
      else if (matches.length === 1) pick(matches[0].artifact_id);
    } else if (e.key === 'Escape') {
      close();
    }
  };

  return (
    <Dialog isOpen={isOpen} onClose={close} title={t('artifacts.newTab.title', 'New tab')} size="md">
      <DialogContent>
        <div className="space-y-3">
          <input
            autoFocus
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder={t('artifacts.newTab.placeholder', 'Paste a URL, or search your artifacts…')}
            className="w-full px-3 py-2 rounded border border-[var(--border-default)] bg-[var(--bg-primary)] text-sm outline-none focus:border-[var(--accent)]"
          />

          {isUrl ? (
            <button
              onClick={submitUrl}
              disabled={busy}
              className="w-full flex items-center gap-2 px-3 py-2 rounded bg-[var(--bg-secondary)] hover:opacity-90 text-sm text-left"
            >
              {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Globe className="w-4 h-4" />}
              <span className="truncate">
                {t('artifacts.newTab.openUrl', 'Open')} <span className="opacity-70">{normalizeUrl(text)}</span>
              </span>
            </button>
          ) : (
            <div className="max-h-64 overflow-y-auto">
              {matches.length === 0 && text.trim() !== '' && (
                <div className="text-xs opacity-50 px-1 py-2">
                  {t('artifacts.newTab.noMatch', 'No matching artifact. Paste a full URL to open a web page.')}
                </div>
              )}
              {matches.map((a) => (
                <button
                  key={a.artifact_id}
                  onClick={() => pick(a.artifact_id)}
                  className="w-full text-left px-3 py-2 rounded hover:bg-[var(--bg-secondary)] text-sm flex items-center gap-2"
                >
                  <span className="truncate flex-1">{a.title}</span>
                  <span className="text-[10px] opacity-40">{a.kind.split('/').pop()}</span>
                </button>
              ))}
            </div>
          )}

          {error && <div className="text-xs text-red-400 px-1">{error}</div>}
        </div>
      </DialogContent>
    </Dialog>
  );
}
