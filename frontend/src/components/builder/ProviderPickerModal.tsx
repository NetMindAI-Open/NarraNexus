/**
 * @file_name: ProviderPickerModal.tsx
 * @author: NetMind.AI
 * @date: 2026-09-03
 * @description: The creation studio's provider gate, as a picker (design
 * "方案 B") rather than the one-key setup card.
 *
 * Two states, and the empty one is the common one: the gate only opens when
 * the user has NO provider, so on first entry the list is empty and only the
 * two "add" entries matter. Rendering an empty list plus a "Choose a
 * provider" heading above nothing would be a worse first impression than
 * simply asking them to connect one, so that state drops both.
 *
 * The API-Key entry embeds OneKeyOnboard instead of re-implementing a key
 * form: that component already handles provider-type detection, aggregator
 * keys with no recognisable prefix, key probing, and the rotate-existing
 * confirm. A second key form would drift from it.
 *
 * The CLI entry is deliberately NOT a uniform button. `claude auth login`
 * can only be driven over Tauri IPC, so on web — and for a bundle without
 * the CLI — the entry degrades to instructions. A button that looks
 * clickable but cannot work is worse than a sentence telling you what to
 * run; see ProviderSettings, which draws the same distinction.
 *
 * Never writes a per-agent LLM override: providers and slots are per-USER
 * here, so "Next" only confirms which existing provider the user means.
 */
import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { KeyRound, TerminalSquare, ChevronRight } from 'lucide-react';
import { api } from '@/lib/api';
import { isTauri, triggerClaudeLogin } from '@/lib/tauri';
import { Button } from '@/components/ui';
import { Dialog } from '@/components/ui';
import { BracketLoading } from '@/components/nm';
import { OneKeyOnboard } from '@/components/settings/OneKeyOnboard';
import { cn } from '@/lib/utils';
import { deriveProviderRows, type PickerRow } from './providerRows';

type AddMode = null | 'api_key' | 'cli';

interface ProviderPickerModalProps {
  /** Called once the user has a provider selected and confirms. No argument: this modal never writes
   *  a per-agent LLM override, so WHICH provider is not the caller's concern. */
  onReady: () => void;
  /** Cancel / close — the caller decides where that goes. */
  onCancel: () => void;
}

export function ProviderPickerModal({ onReady, onCancel }: ProviderPickerModalProps) {
  const { t } = useTranslation();
  const [rows, setRows] = useState<PickerRow[] | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [addMode, setAddMode] = useState<AddMode>(null);
  const [claude, setClaude] = useState<{ cli_installed: boolean; logged_in: boolean } | null>(null);
  const [loggingIn, setLoggingIn] = useState(false);
  const [nonce, setNonce] = useState(0);

  // Provider list. Every state write happens inside a promise callback so the
  // effect body itself never sets state, and a refresh that lands after
  // unmount cannot write into a dead component.
  useEffect(() => {
    let cancelled = false;
    api
      .getProviders()
      .then((res) => {
        if (cancelled) return;
        const next = deriveProviderRows(res.data?.providers as Record<string, unknown> | undefined);
        setRows(next);
        // After adding one, land on it: the user's next action is a single
        // click on Next rather than hunting for the row they just created.
        setSelected((prev) => prev ?? (next.length > 0 ? next[next.length - 1].id : null));
      })
      .catch(() => {
        if (!cancelled) setRows([]);
      });
    return () => {
      cancelled = true;
    };
  }, [nonce]);

  // CLI state is only needed once the user opens that entry.
  useEffect(() => {
    if (addMode !== 'cli') return;
    let cancelled = false;
    api
      .getClaudeStatus()
      .then((res) => {
        if (cancelled) return;
        setClaude(
          res.data
            ? { cli_installed: !!res.data.cli_installed, logged_in: !!res.data.logged_in }
            : { cli_installed: false, logged_in: false },
        );
      })
      .catch(() => {
        if (!cancelled) setClaude({ cli_installed: false, logged_in: false });
      });
    return () => {
      cancelled = true;
    };
  }, [addMode, nonce]);

  const refresh = useCallback(() => setNonce((n) => n + 1), []);

  const handleClaudeLogin = useCallback(async () => {
    setLoggingIn(true);
    try {
      await triggerClaudeLogin();
    } catch {
      /* the status re-probe below is the source of truth either way */
    } finally {
      setLoggingIn(false);
      refresh();
    }
  }, [refresh]);

  if (rows === null) {
    return (
      <Dialog isOpen onClose={onCancel} title={t('builder.gate.title')} size="xl">
        <div className="flex items-center justify-center py-16">
          <BracketLoading label={t('builder.gate.probing')} />
        </div>
      </Dialog>
    );
  }

  const hasProviders = rows.length > 0;

  return (
    <Dialog isOpen onClose={onCancel} title={t('builder.gate.title')} size="xl">
      <div className="px-5 pt-4 pb-5 space-y-5">
        {hasProviders ? (
          <div className="space-y-3">
            <div>
              <h3 className="text-sm font-medium" style={{ color: 'var(--text-primary)' }}>
                {t('builder.gate.chooseTitle')}
              </h3>
              <p className="mt-0.5 text-xs leading-relaxed" style={{ color: 'var(--text-tertiary)' }}>
                {t('builder.gate.chooseHint')}
              </p>
            </div>
            <div role="radiogroup" aria-label={t('builder.gate.chooseTitle')} className="space-y-2">
              {rows.map((row) => (
                <button
                  key={row.id}
                  type="button"
                  role="radio"
                  aria-checked={selected === row.id}
                  onClick={() => setSelected(row.id)}
                  className={cn(
                    'w-full flex items-center gap-3 px-3.5 py-2.5 rounded-[var(--radius-lg)] text-left',
                    'border transition-colors',
                  )}
                  style={{
                    borderColor: selected === row.id ? 'var(--nm-ink)' : 'var(--nm-hairline)',
                    background: selected === row.id ? 'var(--nm-row-active)' : 'transparent',
                  }}
                >
                  <span className="min-w-0 flex-1">
                    <span className="block text-sm font-medium truncate" style={{ color: 'var(--text-primary)' }}>
                      {row.name}
                    </span>
                    <span
                      className="block text-[10.5px] tracking-wide"
                      style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-tertiary)' }}
                    >
                      {row.access === 'cli' ? t('builder.gate.accessCli') : t('builder.gate.accessApiKey')}
                    </span>
                  </span>
                  <span
                    className="w-1.5 h-1.5 rounded-full shrink-0"
                    style={{
                      background: row.active ? 'var(--color-success)' : 'var(--color-warning)',
                    }}
                  />
                </button>
              ))}
            </div>
          </div>
        ) : (
          <p
            className="text-[13px] leading-relaxed rounded-[var(--radius-lg)] px-4 py-3.5"
            style={{
              color: 'var(--text-secondary)',
              background: 'var(--nm-paper-warm)',
              border: '1px dashed var(--nm-hairline)',
            }}
          >
            {t('builder.gate.emptyLead')}
          </p>
        )}

        {/* ---- Add a provider ---- */}
        <div>
          <span
            className="block mb-2.5 text-[10.5px] font-medium uppercase tracking-[0.13em]"
            style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-tertiary)' }}
          >
            {t('builder.gate.addLabel')}
          </span>

          {addMode === null && (
            <div className="grid grid-cols-2 gap-2.5">
              <Button variant="outline" onClick={() => setAddMode('api_key')} className="justify-center gap-2">
                <KeyRound className="w-4 h-4" />
                {t('builder.gate.addApiKey')}
              </Button>
              <Button variant="outline" onClick={() => setAddMode('cli')} className="justify-center gap-2">
                <TerminalSquare className="w-4 h-4" />
                {t('builder.gate.addCli')}
              </Button>
            </div>
          )}

          {addMode === 'api_key' && (
            <div className="space-y-3">
              <OneKeyOnboard
                onComplete={() => {
                  setAddMode(null);
                  setSelected(null);
                  refresh();
                }}
              />
              <Button variant="ghost" size="sm" onClick={() => setAddMode(null)}>
                {t('builder.gate.back')}
              </Button>
            </div>
          )}

          {addMode === 'cli' && (
            <div
              className="rounded-[var(--radius-lg)] p-4 space-y-3"
              style={{ border: '1px solid var(--nm-hairline)', background: 'var(--nm-paper-warm)' }}
            >
              {claude === null ? (
                <BracketLoading label={t('builder.gate.probing')} />
              ) : isTauri() && claude.cli_installed ? (
                <>
                  <p className="text-xs leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
                    {t('builder.gate.cliDesktopHint')}
                  </p>
                  <Button onClick={handleClaudeLogin} disabled={loggingIn}>
                    {loggingIn ? t('builder.gate.cliLoggingIn') : t('builder.gate.cliLogin')}
                  </Button>
                </>
              ) : (
                // Web mode, or a bundle without the CLI: text, never a button.
                // A control that cannot work must not look like one.
                <div className="flex items-start gap-2.5">
                  <span
                    className="w-1.5 h-1.5 rounded-full shrink-0 mt-1.5"
                    style={{ background: 'var(--text-tertiary)' }}
                  />
                  <p className="text-xs leading-relaxed" style={{ color: 'var(--text-tertiary)' }}>
                    {claude.cli_installed
                      ? t('builder.gate.cliWebHint')
                      : t('builder.gate.cliMissingHint')}
                  </p>
                </div>
              )}
              <Button variant="ghost" size="sm" onClick={() => setAddMode(null)}>
                {t('builder.gate.back')}
              </Button>
            </div>
          )}
        </div>
      </div>

      <div
        className="flex items-center justify-end gap-2.5 px-5 py-3.5 border-t"
        style={{ borderColor: 'var(--nm-hairline)', background: 'var(--nm-paper-warm)' }}
      >
        <Button variant="ghost" onClick={onCancel}>
          {t('common.cancel')}
        </Button>
        <Button disabled={!selected} onClick={() => selected && onReady()} className="gap-1.5">
          {t('builder.gate.next')}
          <ChevronRight className="w-4 h-4" />
        </Button>
      </div>
    </Dialog>
  );
}
