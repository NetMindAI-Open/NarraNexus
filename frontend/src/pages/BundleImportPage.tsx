/**
 * BundleImportPage — Subproject 2 Import Wizard.
 *
 * Three steps:
 *   1. Upload .nxbundle (drag & drop or file picker)
 *   2. Preflight review — bundle preview + name clashes + warnings
 *   3. Confirm — execute import, show summary toast w/ link to team intro
 */

import { useEffect, useRef, useState } from 'react';
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
      setError(e?.message || 'Preflight failed');
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
              `Waiting for backend (try ${attempt + 1}/${RETRY_DELAYS_MS.length + 1})…`,
            );
            await new Promise((r) => window.setTimeout(r, RETRY_DELAYS_MS[attempt]));
            continue;
          }
          // Final failure: surface message + leave the Retry button to drive
          // a manual re-fire (via retryNonce bump).
          setError(msg || 'Failed to fetch template');
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
        setError('Your preview session expired (>6h or backend restarted). Please re-upload the bundle.');
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
          {urlMode ? 'Install template' : 'Import bundle'}
        </h1>
        <div className="ml-auto w-[360px]">
          <StepIndicator
            steps={[
              { key: 'upload', label: urlMode ? 'Fetch' : 'Upload' },
              { key: 'review', label: 'Review' },
              { key: 'done', label: 'Done' },
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
                    Fetching template from <span className="font-mono break-all">{urlMode}</span>…
                  </p>
                </>
              )}
              {!busy && error && (
                <>
                  <AlertTriangle className="w-10 h-10 mx-auto text-[var(--color-red-500)]" />
                  <p className="mt-3 text-sm text-[var(--text-secondary)]">
                    Could not fetch the template.
                  </p>
                  <Button
                    onClick={() => setRetryNonce((n) => n + 1)}
                    size="sm"
                    className="mt-4 gap-1"
                  >
                    <Loader2 className="w-3.5 h-3.5" />
                    Retry
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
                <p className="text-sm" style={{ color: 'var(--nm-ink70)' }}>Drag &amp; drop a .nxbundle here</p>
                <p className="mt-1 text-xs" style={{ color: 'var(--nm-ink50)', fontFamily: 'var(--font-mono)' }}>— or —</p>
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
                    Choose file
                  </span>
                </label>
                {file && (
                  <div className="mt-4 inline-flex items-center gap-2 text-sm">
                    <Check className="w-4 h-4" style={{ color: 'var(--color-success)' }} />
                    <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--nm-ink)' }}>{file.name}</span>
                    <span style={{ color: 'var(--nm-ink50)' }}>({Math.round(file.size / 1024)} KB)</span>
                  </div>
                )}
              </BracketDropzone>
            </div>
            {error && <ErrorBanner error={error} />}
            <div className="flex justify-end">
              <Button onClick={runPreflight} disabled={!file || busy} size="sm" className="gap-1">
                {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Eye className="w-3.5 h-3.5" />}
                Preview
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
  const m = preflight.manifest;
  return (
    <div className="max-w-3xl mx-auto space-y-4">
      <div className="grid grid-cols-2 gap-3">
        <Stat label="Bundle format" value={m.bundle_format_version} />
        <Stat label="Exported version" value={m.narranexus_version_exported} />
        <Stat label="Exported at" value={m.exported_at?.slice(0, 19) || '?'} />
        <Stat label="Integrity sha256" value={m.integrity_sha256?.slice(0, 12) + '…'} />
      </div>

      <Section title="Will create">
        <Bullet>{m.agents.length} agent{m.agents.length === 1 ? '' : 's'}{preflight.name_clashes.length > 0 && ` (${preflight.name_clashes.length} will be auto-renamed with (N) suffix)`}</Bullet>
        {m.team && (
          <Bullet>1 team "{m.team.name}"{preflight.team_clash && ' (renamed (N))'}</Bullet>
        )}
        {(m.skills || []).length > 0 && (
          <Bullet>{m.skills.length} skill{m.skills.length === 1 ? '' : 's'} ({m.skills.filter((s: any) => s.install_method === 'url').length}× url, {m.skills.filter((s: any) => s.install_method === 'zip').length}× zip, {m.skills.filter((s: any) => s.install_method === 'full_copy').length}× full)</Bullet>
        )}
        {(m.mcp_hints_count || 0) > 0 && (
          <Bullet>{m.mcp_hints_count} suggested external MCP URL{(m.mcp_hints_count || 0) === 1 ? '' : 's'} (you'll be asked to confirm each)</Bullet>
        )}
      </Section>

      {preflight.name_clashes.length > 0 && (
        <Section title="Name clashes (auto-renamed)" warning>
          {preflight.name_clashes.map((c) => (
            <Bullet key={c.agent_id_in_bundle}>"{c.agent_name}" — {c.existing_count} existing agent(s) with same name</Bullet>
          ))}
        </Section>
      )}

      {m.warnings.length > 0 && (
        <Section title="Bundle warnings" warning>
          {m.warnings.map((w, i) => <Bullet key={i}>{w}</Bullet>)}
        </Section>
      )}

      {(m.info && m.info.length > 0) && (
        <Section title="Info (expected, no action needed)">
          {m.info.map((line, i) => <Bullet key={i}>{line}</Bullet>)}
        </Section>
      )}

      <Section title="Embedding compatibility">
        <Bullet>
          Provider: {m.embedding?.provider || 'unknown'} · Model: {m.embedding?.model || 'unknown'} · Dim: {m.embedding?.dim || 'unknown'}
        </Bullet>
        <Bullet>{preflight.embedding_compat.advice}</Bullet>
      </Section>

      <Section title="Stripped (not present in bundle)">
        {m.stripped.map((s, i) => <Bullet key={i}>{s}</Bullet>)}
      </Section>

      {m.team?.intro_md && (
        <Section title="Bundle notes (README.md)">
          <pre className="whitespace-pre-wrap text-xs font-mono bg-[var(--bg-tertiary)] p-3 max-h-[200px] overflow-y-auto">{m.team.intro_md}</pre>
        </Section>
      )}

      {error && <ErrorBanner error={error} />}

      <div className="flex justify-between pt-2">
        <Button onClick={onBack} variant="ghost" size="sm">← Back</Button>
        <Button onClick={onConfirm} size="sm" disabled={busy} className="gap-1">
          {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Check className="w-3.5 h-3.5" />}
          Import now
        </Button>
      </div>
    </div>
  );
}

function DonePanel({
  result, onClose, onViewIntro,
}: { result: BundleConfirmResponse; onClose: () => void; onViewIntro: () => void }) {
  return (
    <div className="max-w-2xl mx-auto space-y-4">
      <div className="border border-[var(--color-green-500)] bg-[var(--color-green-500)]/10 p-5">
        <div className="flex items-center gap-2 mb-3">
          <Check className="w-5 h-5 text-[var(--color-green-500)]" />
          <h2 className="font-mono text-sm">Bundle imported</h2>
        </div>
        <ul className="text-sm font-mono space-y-1">
          <li>{result.agents_created} agent{result.agents_created === 1 ? '' : 's'} created{result.agents_renamed ? ` (${result.agents_renamed} renamed)` : ''}</li>
          {result.team_created && <li>1 team "{result.team_name}" added to sidebar</li>}
          <li>{result.narratives_created} narrative{result.narratives_created === 1 ? '' : 's'}, {result.events_created} chat event{result.events_created === 1 ? '' : 's'}</li>
          <li>{result.instances_created} module instance{result.instances_created === 1 ? '' : 's'}, {result.social_entities_created} social entit{result.social_entities_created === 1 ? 'y' : 'ies'}</li>
          <li>{result.skills_imported} skill{result.skills_imported === 1 ? '' : 's'}{result.skills_imported ? ' (some may need re-study or new credentials)' : ''}</li>
          {result.mcp_hints > 0 && <li>{result.mcp_hints} external MCP URL{result.mcp_hints === 1 ? '' : 's'} suggested — review below</li>}
          {result.warnings.length > 0 && <li className="text-[var(--color-yellow-500)]">{result.warnings.length} warning{result.warnings.length === 1 ? '' : 's'} (see details)</li>}
        </ul>
      </div>
      {(result.mcp_hints_data || []).length > 0 && (
        <div className="border border-[var(--border-default)] p-4">
          <div className="text-xs font-mono uppercase mb-2 text-[var(--text-secondary)]">Suggested external MCP URLs</div>
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
            These are not auto-installed. Add them under each agent's Settings → MCPs if you trust them.
          </div>
        </div>
      )}
      <div className="text-sm text-[var(--text-secondary)]">
        Tip: skills marked as "full copy" already have credentials. URL/Zip skills need re-study or new
        credentials — talk to the imported agents to complete setup.
      </div>
      <div className="flex justify-end gap-2">
        {result.team_id && (
          <Button onClick={onViewIntro} variant="ghost" size="sm" className="gap-1">
            <FileText className="w-3.5 h-3.5" /> View team intro
          </Button>
        )}
        <Button onClick={onClose} size="sm">Done</Button>
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
