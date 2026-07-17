/**
 * BundleImportPage — Subproject 2 Import Wizard.
 *
 * Three steps:
 *   1. Upload .nxbundle (drag & drop or file picker)
 *   2. Preflight review — bundle preview + name clashes + warnings
 *   3. Confirm — execute import, show summary toast w/ link to team intro
 */

import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  ArrowLeft,
  Upload,
  AlertTriangle,
  Check,
  Loader2,
  Package,
  Eye,
  Info,
  FileText,
} from 'lucide-react';
import { Button, useConfirm } from '@/components/ui';
import { BracketDropzone, StepIndicator } from '@/components/nm';
import { useTeamsStore, useConfigStore } from '@/stores';
import { api } from '@/lib/api';
import { cn } from '@/lib/utils';
import type { BundlePreflightResponse, BundleConfirmResponse } from '@/types';

type Step = 'upload' | 'review' | 'done';

export default function BundleImportPage() {
  const navigate = useNavigate();
  const { t } = useTranslation();
  const { refresh: refreshTeams } = useTeamsStore();
  const { refreshAgents, userId } = useConfigStore();
  const { dialog } = useConfirm();

  // Deep-link mode: when mounted at /app/templates/install?url=…&sha256=…
  // the page skips the upload step and auto-fetches the bundle via the
  // server-side from-url endpoint. Used by the website templates page.
  const [searchParams] = useSearchParams();
  const urlMode = searchParams.get('url') || null;
  const expectedSha256 = searchParams.get('sha256') || undefined;

  const [step, setStep] = useState<Step>('upload');
  const [file, setFile] = useState<File | null>(null);
  const [preflight, setPreflight] = useState<BundlePreflightResponse | null>(null);
  const [confirmResult, setConfirmResult] = useState<BundleConfirmResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const dropRef = useRef<HTMLDivElement>(null);

  const [dragActive, setDragActive] = useState(false);
  useEffect(() => {
    const el = dropRef.current; if (!el) return;
    const onOver = (e: DragEvent) => { e.preventDefault(); setDragActive(true); };
    const onLeave = () => setDragActive(false);
    const onDrop = (e: DragEvent) => {
      e.preventDefault();
      setDragActive(false);
      const f = e.dataTransfer?.files?.[0];
      if (f) setFile(f);
    };
    el.addEventListener('dragover', onOver);
    el.addEventListener('dragleave', onLeave);
    el.addEventListener('drop', onDrop);
    return () => {
      el.removeEventListener('dragover', onOver);
      el.removeEventListener('dragleave', onLeave);
      el.removeEventListener('drop', onDrop);
    };
  }, []);

  const runPreflight = async () => {
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      const r = await api.importBundlePreflight(file);
      setPreflight(r);
      setStep('review');
    } catch (e: any) {
      setError(e?.message || t('pages.bundleImport.preflightFailed'));
    } finally {
      setBusy(false);
    }
  };

  // Manual retry counter — incrementing re-fires the auto-fetch useEffect
  // below. Used by the "Retry" button when auto-retry has exhausted.
  const [retryNonce, setRetryNonce] = useState(0);

  // URL-mode auto-fetch: fire on mount when the route receives a ?url=
  // query, before the user sees the upload step. Builds in exponential-
  // backoff retry so that the desktop-app cold-start race (~10-15s where
  // the Tauri parent has loaded the frontend but the Python sidecar on
  // :8000 isn't listening yet) doesn't surface as "Load failed" to the
  // user. Retries are network-error-only — a 4xx from a reachable backend
  // (allowlist reject, sha256 mismatch, malformed URL) is a real error
  // and surfaces immediately.
  useEffect(() => {
    if (!urlMode || preflight) return;
    let cancelled = false;
    const RETRY_DELAYS_MS = [1000, 2000, 4000, 8000]; // ~15s total
    (async () => {
      setBusy(true);
      setError(null);
      for (let attempt = 0; attempt <= RETRY_DELAYS_MS.length; attempt++) {
        if (cancelled) return;
        try {
          const r = await api.importBundleFromUrl(urlMode, expectedSha256);
          if (cancelled) return;
          // Clear the "Waiting for backend..." message a previous attempt
          // may have set — otherwise the warning persists on the review
          // page even though the fetch ultimately succeeded.
          setError(null);
          setPreflight(r);
          setStep('review');
          setBusy(false);
          return;
        } catch (e: any) {
          if (cancelled) return;
          const msg = e?.message || String(e);
          // Distinguish "network-not-ready" from "real backend error".
          // The former is retriable (cold-start race), the latter isn't.
          const isNetworkLike =
            /load failed|failed to fetch|network|fetch failed|connection|refused|econnrefused/i.test(msg);
          if (isNetworkLike && attempt < RETRY_DELAYS_MS.length) {
            setError(
              t('pages.bundleImport.waitingForBackend', {
                attempt: attempt + 1,
                total: RETRY_DELAYS_MS.length + 1,
              }),
            );
            await new Promise((r) => window.setTimeout(r, RETRY_DELAYS_MS[attempt]));
            continue;
          }
          // Final failure: surface message + leave the Retry button to drive
          // a manual re-fire (via retryNonce bump).
          setError(msg || t('pages.bundleImport.fetchFailed'));
          setBusy(false);
          return;
        }
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [urlMode, retryNonce]);

  const runConfirm = async () => {
    if (!preflight) return;
    setBusy(true);
    setError(null);
    try {
      const r = await api.importBundleConfirm(preflight.preflight_token);
      setConfirmResult(r);
      setStep('done');
      await refreshTeams();
      await refreshAgents();
      // A confirmed import counts as "applied a template" for the
      // onboarding checklist. Best-effort — never let it surface as an
      // import error.
      if (userId) {
        api.markOnboardingStep(userId, 'template_applied').catch(() => {});
      }
    } catch (e: any) {
      const msg = e?.message || String(e);
      // Special case: preflight session expired (>6h since upload, or
      // backend was restarted on a host without persistent volume).
      // Bounce back to upload step so the user can re-pick the file.
      if (/preflight.*(not found|expired|missing)/i.test(msg)) {
        setError(t('pages.bundleImport.sessionExpired'));
        setStep('upload');
        setPreflight(null);
        setFile(null);
      } else {
        setError(msg);
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="h-full flex flex-col bg-[var(--bg-primary)]">
      <div className="px-6 py-4 border-b border-[var(--border-default)] flex items-center gap-3">
        <button onClick={() => navigate('/app/settings')} className="p-1 hover:bg-[var(--bg-tertiary)]">
          <ArrowLeft className="w-4 h-4" />
        </button>
        <Package className="w-5 h-5" />
        <h1 className="font-mono text-base">
          {urlMode ? t('pages.bundleImport.installTemplate') : t('pages.bundleImport.importBundle')}
        </h1>
        <div className="ml-auto w-[360px]">
          <StepIndicator
            steps={[
              { key: 'upload', label: urlMode ? t('pages.bundleImport.stepFetch') : t('pages.bundleImport.stepUpload') },
              { key: 'review', label: t('pages.bundleImport.stepReview') },
              { key: 'done', label: t('pages.bundleImport.stepDone') },
            ]}
            currentIndex={step === 'upload' ? 0 : step === 'review' ? 1 : 2}
          />
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-6">
        {step === 'upload' && urlMode && (
          <div className="max-w-2xl mx-auto space-y-4">
            <div className="border border-[var(--border-default)] rounded-md p-10 text-center bg-[var(--bg-secondary)]">
              {busy && (
                <>
                  <Loader2 className="w-10 h-10 mx-auto text-[var(--text-tertiary)] animate-spin" />
                  <p className="mt-3 text-sm text-[var(--text-secondary)]">
                    {t('pages.bundleImport.fetchingFrom')} <span className="font-mono break-all">{urlMode}</span>…
                  </p>
                </>
              )}
              {!busy && error && (
                <>
                  <AlertTriangle className="w-10 h-10 mx-auto text-[var(--color-red-500)]" />
                  <p className="mt-3 text-sm text-[var(--text-secondary)]">
                    {t('pages.bundleImport.couldNotFetch')}
                  </p>
                  <Button
                    onClick={() => setRetryNonce((n) => n + 1)}
                    size="sm"
                    className="mt-4 gap-1"
                  >
                    <Loader2 className="w-3.5 h-3.5" />
                    {t('pages.bundleImport.retry')}
                  </Button>
                </>
              )}
            </div>
            {error && <ErrorBanner error={error} />}
          </div>
        )}
        {step === 'upload' && !urlMode && (
          <div className="max-w-2xl mx-auto space-y-4">
            <div ref={dropRef}>
              <BracketDropzone active={dragActive}>
                <Upload className="w-10 h-10 mx-auto mb-3" />
                <p className="text-sm" style={{ color: 'var(--nm-ink70)' }}>{t('pages.bundleImport.dragDrop')}</p>
                <p className="mt-1 text-xs" style={{ color: 'var(--nm-ink50)', fontFamily: 'var(--font-mono)' }}>{t('pages.bundleImport.orSeparator')}</p>
                <label className="mt-3 inline-block">
                  <input
                    type="file"
                    accept=".nxbundle,.zip"
                    onChange={(e) => setFile(e.target.files?.[0] || null)}
                    className="hidden"
                  />
                  <span
                    className="inline-flex items-center px-3 py-1.5 cursor-pointer text-sm font-mono transition-colors"
                    style={{
                      border: '1px solid var(--nm-ink)',
                      borderRadius: 'var(--radius-sm)',
                      color: 'var(--nm-ink)',
                    }}
                  >
                    {t('pages.bundleImport.chooseFile')}
                  </span>
                </label>
                {file && (
                  <div className="mt-4 inline-flex items-center gap-2 text-sm">
                    <Check className="w-4 h-4" style={{ color: 'var(--color-success)' }} />
                    <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--nm-ink)' }}>{file.name}</span>
                    <span style={{ color: 'var(--nm-ink50)' }}>{t('pages.bundleImport.fileSizeKb', { size: Math.round(file.size / 1024) })}</span>
                  </div>
                )}
              </BracketDropzone>
            </div>
            {error && <ErrorBanner error={error} />}
            <div className="flex justify-end">
              <Button onClick={runPreflight} disabled={!file || busy} size="sm" className="gap-1">
                {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Eye className="w-3.5 h-3.5" />}
                {t('pages.bundleImport.preview')}
              </Button>
            </div>
          </div>
        )}

        {step === 'review' && preflight && (
          <ReviewPanel
            preflight={preflight}
            onBack={() => setStep('upload')}
            onConfirm={runConfirm}
            busy={busy}
            error={error}
          />
        )}

        {step === 'done' && confirmResult && (
          <DonePanel
            result={confirmResult}
            onClose={() => navigate('/app/settings')}
            onViewIntro={() => {
              if (confirmResult.team_id) {
                navigate(`/app/teams/${confirmResult.team_id}`);
              }
            }}
          />
        )}
      </div>
      {dialog}
    </div>
  );
}

function ErrorBanner({ error }: { error: string }) {
  return (
    <div className="px-3 py-2 border border-[var(--color-red-500)] bg-[var(--color-red-500)]/10 text-xs text-[var(--color-red-500)] flex items-center gap-2">
      <AlertTriangle className="w-3.5 h-3.5" />
      <span>{error}</span>
    </div>
  );
}

function ReviewPanel({
  preflight, onBack, onConfirm, busy, error,
}: {
  preflight: BundlePreflightResponse;
  onBack: () => void;
  onConfirm: () => void;
  busy: boolean;
  error: string | null;
}) {
  const { t } = useTranslation();
  const m = preflight.manifest;
  return (
    <div className="max-w-3xl mx-auto space-y-4">
      <div className="grid grid-cols-2 gap-3">
        <Stat label={t('pages.bundleImport.review.bundleFormat')} value={m.bundle_format_version} />
        <Stat label={t('pages.bundleImport.review.exportedVersion')} value={m.narranexus_version_exported} />
        <Stat label={t('pages.bundleImport.review.exportedAt')} value={m.exported_at?.slice(0, 19) || '?'} />
        <Stat label={t('pages.bundleImport.review.integritySha')} value={m.integrity_sha256?.slice(0, 12) + '…'} />
      </div>

      <Section title={t('pages.bundleImport.review.willCreate')}>
        <Bullet>{t('pages.bundleImport.review.agentsLine', { count: m.agents.length })}{preflight.name_clashes.length > 0 && ` ${t('pages.bundleImport.review.agentsRenamedSuffix', { count: preflight.name_clashes.length })}`}</Bullet>
        {m.team && (
          <Bullet>{t('pages.bundleImport.review.teamLine', { name: m.team.name })}{preflight.team_clash && ` ${t('pages.bundleImport.review.teamRenamedSuffix')}`}</Bullet>
        )}
        {(m.skills || []).length > 0 && (
          <Bullet>{t('pages.bundleImport.review.skillsLine', {
            count: m.skills.length,
            url: m.skills.filter((s: any) => s.install_method === 'url').length,
            zip: m.skills.filter((s: any) => s.install_method === 'zip').length,
            full: m.skills.filter((s: any) => s.install_method === 'full_copy').length,
          })}</Bullet>
        )}
        {(m.mcp_hints_count || 0) > 0 && (
          <Bullet>{t('pages.bundleImport.review.mcpHintsLine', { count: m.mcp_hints_count })}</Bullet>
        )}
      </Section>

      {preflight.name_clashes.length > 0 && (
        <Section title={t('pages.bundleImport.review.nameClashesTitle')} warning>
          {preflight.name_clashes.map((c) => (
            <Bullet key={c.agent_id_in_bundle}>{t('pages.bundleImport.review.nameClashLine', { name: c.agent_name, count: c.existing_count })}</Bullet>
          ))}
        </Section>
      )}

      {(preflight.credential_clashes && preflight.credential_clashes.length > 0) && (
        <Section title={t('pages.bundleImport.review.credentialClashesTitle')} warning>
          {preflight.credential_clashes.map((c, i) => (
            <Bullet key={i}>{t('pages.bundleImport.review.credentialClashLine', { channel: c.table, identity: Object.values(c.identity).join(' / ') })}</Bullet>
          ))}
        </Section>
      )}

      {m.warnings.length > 0 && (
        <Section title={t('pages.bundleImport.review.warningsTitle')} warning>
          {m.warnings.map((w, i) => <Bullet key={i}>{w}</Bullet>)}
        </Section>
      )}

      {(m.info && m.info.length > 0) && (
        <Section title={t('pages.bundleImport.review.infoTitle')}>
          {m.info.map((line, i) => <Bullet key={i}>{line}</Bullet>)}
        </Section>
      )}

      <Section title={t('pages.bundleImport.review.strippedTitle')}>
        {m.stripped.map((s, i) => <Bullet key={i}>{s}</Bullet>)}
      </Section>

      {m.team?.intro_md && (
        <Section title={t('pages.bundleImport.review.bundleNotesTitle')}>
          <pre className="whitespace-pre-wrap text-xs font-mono bg-[var(--bg-tertiary)] p-3 max-h-[200px] overflow-y-auto">{m.team.intro_md}</pre>
        </Section>
      )}

      {error && <ErrorBanner error={error} />}

      <div className="flex justify-between pt-2">
        <Button onClick={onBack} variant="ghost" size="sm">{t('pages.bundleImport.review.back')}</Button>
        <Button onClick={onConfirm} size="sm" disabled={busy} className="gap-1">
          {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Check className="w-3.5 h-3.5" />}
          {t('pages.bundleImport.review.importNow')}
        </Button>
      </div>
    </div>
  );
}

function DonePanel({
  result, onClose, onViewIntro,
}: { result: BundleConfirmResponse; onClose: () => void; onViewIntro: () => void }) {
  const { t } = useTranslation();
  return (
    <div className="max-w-2xl mx-auto space-y-4">
      <div className="border border-[var(--color-green-500)] bg-[var(--color-green-500)]/10 p-5">
        <div className="flex items-center gap-2 mb-3">
          <Check className="w-5 h-5 text-[var(--color-green-500)]" />
          <h2 className="font-mono text-sm">{t('pages.bundleImport.done.bundleImported')}</h2>
        </div>
        <ul className="text-sm font-mono space-y-1">
          <li>{t('pages.bundleImport.done.agentsCreated', { count: result.agents_created })}{result.agents_renamed ? ` ${t('pages.bundleImport.done.agentsRenamedSuffix', { count: result.agents_renamed })}` : ''}</li>
          {result.team_created && <li>{t('pages.bundleImport.done.teamAdded', { name: result.team_name })}</li>}
          <li>{t('pages.bundleImport.done.narrativesEvents', { narratives: result.narratives_created, events: result.events_created })}</li>
          <li>{t('pages.bundleImport.done.instancesEntities', { instances: result.instances_created, entities: result.social_entities_created })}</li>
          <li>{t('pages.bundleImport.done.skillsImported', { count: result.skills_imported })}{result.skills_imported ? ` ${t('pages.bundleImport.done.skillsCredsSuffix')}` : ''}</li>
          {result.mcp_hints > 0 && <li>{t('pages.bundleImport.done.mcpHints', { count: result.mcp_hints })}</li>}
          {(result.channel_credentials_imported || 0) > 0 && <li>{t('pages.bundleImport.done.channelCredentialsImported', { count: result.channel_credentials_imported })}</li>}
          {(result.channel_credentials_skipped_conflict || 0) > 0 && <li className="text-[var(--color-yellow-500)]">{t('pages.bundleImport.done.channelCredentialsSkipped', { count: result.channel_credentials_skipped_conflict })}</li>}
          {result.warnings.length > 0 && <li className="text-[var(--color-yellow-500)]">{t('pages.bundleImport.done.warnings', { count: result.warnings.length })}</li>}
        </ul>
      </div>
      {result.agents_created > 0 && (
        <div className="border border-[var(--color-yellow-500)]/50 bg-[var(--color-yellow-500)]/5 p-4">
          <div className="text-xs font-mono uppercase mb-2 text-[var(--color-yellow-500)] flex items-center gap-1.5">
            <AlertTriangle className="w-3.5 h-3.5" /> {t('pages.bundleImport.done.configureLlmTitle')}
          </div>
          <div className="text-xs text-[var(--text-secondary)]">{t('pages.bundleImport.done.configureLlmHint')}</div>
        </div>
      )}
      {(result.channel_credentials_imported || 0) > 0 && (
        <div className="border border-[var(--color-yellow-500)]/40 bg-[var(--color-yellow-500)]/5 p-4">
          <div className="text-xs font-mono uppercase mb-2 text-[var(--text-secondary)]">{t('pages.bundleImport.done.activateChannelsTitle')}</div>
          <div className="text-xs text-[var(--text-secondary)]">{t('pages.bundleImport.done.activateChannelsHint')}</div>
        </div>
      )}
      {(result.mcp_hints_data || []).length > 0 && (
        <div className="border border-[var(--border-default)] p-4">
          <div className="text-xs font-mono uppercase mb-2 text-[var(--text-secondary)]">{t('pages.bundleImport.done.suggestedMcpTitle')}</div>
          <ul className="space-y-1 text-xs font-mono">
            {(result.mcp_hints_data || []).map((m, i) => (
              <li key={i} className="flex items-center gap-2">
                <Info className="w-3 h-3 text-[var(--text-tertiary)]" />
                <span>{m.agent_id}</span>
                <span>·</span>
                <span className="font-bold">{m.name}</span>
                <span className="text-[var(--text-tertiary)] truncate">{m.url}</span>
              </li>
            ))}
          </ul>
          <div className="mt-2 text-[10px] text-[var(--text-tertiary)]">
            {t('pages.bundleImport.done.mcpNotAutoInstalled')}
          </div>
        </div>
      )}
      <div className="text-sm text-[var(--text-secondary)]">
        {t('pages.bundleImport.done.skillsTip')}
      </div>
      <div className="flex justify-end gap-2">
        {result.team_id && (
          <Button onClick={onViewIntro} variant="ghost" size="sm" className="gap-1">
            <FileText className="w-3.5 h-3.5" /> {t('pages.bundleImport.done.viewTeamIntro')}
          </Button>
        )}
        <Button onClick={onClose} size="sm">{t('pages.bundleImport.done.doneButton')}</Button>
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="border border-[var(--border-subtle)] px-3 py-2">
      <div className="text-[10px] uppercase text-[var(--text-tertiary)]">{label}</div>
      <div className="text-sm font-mono mt-0.5 truncate">{value}</div>
    </div>
  );
}

function Section({ title, warning, children }: { title: string; warning?: boolean; children: any }) {
  return (
    <div className={cn(
      'border p-3',
      warning ? 'border-[var(--color-yellow-500)] bg-[var(--color-yellow-500)]/10' : 'border-[var(--border-subtle)]'
    )}>
      <div className={cn(
        'text-[10px] uppercase mb-1 font-mono tracking-widest flex items-center gap-1',
        warning ? 'text-[var(--color-yellow-500)]' : 'text-[var(--text-tertiary)]'
      )}>
        {warning && <AlertTriangle className="w-3 h-3" />}
        {title}
      </div>
      <div className="space-y-0.5">{children}</div>
    </div>
  );
}

function Bullet({ children }: { children: any }) {
  return <div className="text-xs text-[var(--text-secondary)]">• {children}</div>;
}
