/**
 * @file_name: OneKeyOnboard.tsx
 * @author: NarraNexus
 * @date: 2026-06-10
 * @description: One-key setup card — pick a provider, paste one key, go.
 *
 * THE single quick-setup surface, shared by step 1 of the first-run flow
 * (WelcomePage, which passes hideHeader + bare) and Settings → Providers
 * (Step 1). Provider picker covers the five one-key sources (NetMind
 * recommended, Claude Code SDK / Codex SDK, Yunwu, OpenRouter); submission
 * goes through POST /api/providers/onboard
 * which wires the agent framework + provider + BOTH slots server-side —
 * unlike the old Quick Add path, this also switches the framework, which
 * an official OpenAI key requires (codex_cli).
 *
 * Semantics: "make this key my active setup" — both slots are
 * (re)assigned to the new provider with recommended defaults. Per-slot
 * fine-tuning lives in the Advanced area (ProviderSettings).
 */

import { useMemo, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { ArrowRight, CheckCircle2, ExternalLink, KeyRound, Loader2 } from 'lucide-react';
import { Button, useConfirm } from '@/components/ui';
import { PaperCard, FormField, TextInput } from '@/components/nm';
import { ClaudeBrandIcon, OpenAIBrandIcon } from '@/components/icons/ModelBrandIcons';
import { NexusPowerBrandIcon } from '@/components/icons/ChannelBrandIcons';
import { cn } from '@/lib/utils';
import { api, type OnboardProviderType } from '@/lib/api';

interface OneKeyProvider {
  id: OnboardProviderType;
  labelKey: string;
  /** Short vendor name for the "Get your X API key" link. */
  keyName: string;
  descKey: string;
  getKeyUrl: string;
}

const ONE_KEY_PROVIDERS: OneKeyProvider[] = [
  // NetMind first: it is the recommended source and the DEFAULT selection
  // (Owner decision 2026-08-28) — one NetMind key covers both protocols.
  {
    id: 'netmind',
    labelKey: 'NetMind.AI Power',
    keyName: 'NetMind',
    descKey: 'settings.provider.netmindDesc',
    getKeyUrl: 'https://www.netmind.ai/user/dashboard',
  },
  {
    id: 'anthropic',
    // Named after the SDK the key actually switches the agent to, not after
    // the vendor ("Anthropic (official)" told the user nothing about what
    // changes): an anthropic-protocol key runs claude_code, a pure OpenAI key
    // runs codex_cli (see providers/user_service.py's framework resolution).
    labelKey: 'settings.provider.claudeCodeSdk',
    keyName: 'Anthropic',
    descKey: 'settings.provider.claudeCodeSdkDesc',
    getKeyUrl: 'https://console.anthropic.com/settings/keys',
  },
  {
    id: 'openai',
    labelKey: 'settings.provider.codexSdk',
    keyName: 'OpenAI',
    descKey: 'settings.provider.codexSdkDesc',
    getKeyUrl: 'https://platform.openai.com/api-keys',
  },
  {
    id: 'yunwu',
    labelKey: 'Yunwu',
    keyName: 'Yunwu',
    descKey: 'settings.provider.proxyDesc',
    getKeyUrl: 'https://yunwu.ai',
  },
  {
    id: 'openrouter',
    labelKey: 'OpenRouter',
    keyName: 'OpenRouter',
    descKey: 'settings.provider.proxyDesc',
    getKeyUrl: 'https://openrouter.ai/keys',
  },
];

/** Official-key prefix detection, used only to nudge an obvious mismatch. */
const detectOfficialType = (key: string): OnboardProviderType | null => {
  const k = key.trim();
  if (!k) return null;
  if (k.startsWith('sk-ant-')) return 'anthropic';
  if (k.startsWith('sk-')) return 'openai';
  return null;
};

/** The vendor mark for a one-key source. Real marks where they exist
 *  (design_system §5.1); the two aggregators have none, so they get a neutral
 *  lettermark rather than a fabricated logo. OpenAI's canonical black is
 *  refilled with --nm-ink, which is invisible-on-dark otherwise. */
function ProviderMark({ id, className }: { id: OnboardProviderType; className?: string }) {
  if (id === 'anthropic') return <ClaudeBrandIcon className={className} />;
  if (id === 'openai') return <OpenAIBrandIcon className={className} fill="var(--nm-ink)" />;
  if (id === 'netmind') return <NexusPowerBrandIcon className={className} />;
  return (
    <span
      aria-hidden
      className={cn(
        'grid place-items-center rounded-[var(--radius-xs)] border border-[var(--nm-hairline)] bg-[var(--nm-paper-warm)] font-[family-name:var(--font-display)] text-[9px] font-bold text-[var(--nm-ink70)]',
        className,
      )}
    >
      {id === 'yunwu' ? 'Y' : 'OR'}
    </span>
  );
}

/** PaperCard's shape, minus the surface — `padding` is accepted and ignored so
 *  it can stand in for PaperCard without a conditional at the call site. */
function BareSurface({ children }: { padding?: string; children?: ReactNode }) {
  return <>{children}</>;
}

interface OneKeyOnboardProps {
  /** Called after the backend confirms everything is wired. */
  onComplete: () => void;
  /** Drop the card's own title + description. The welcome flow frames this
   *  step with its own page heading, and two headings stacked read as a bug;
   *  Settings has no outer heading, so it keeps them. */
  hideHeader?: boolean;
  /** Render without the PaperCard surface — the welcome flow already sits on
   *  its own content pane and a card inside a card is a surface level too many
   *  (design_system §2.5). */
  bare?: boolean;
}

export function OneKeyOnboard({ onComplete, hideHeader, bare }: OneKeyOnboardProps) {
  const { t } = useTranslation();
  const [providerType, setProviderType] = useState<OnboardProviderType>('netmind');
  const [apiKey, setApiKey] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  // Success summary from the onboard response; cleared on next input.
  // activated=false = register-only (cloud non-staff): the key was saved but
  // framework/slots stay on NetMind, so the panel must not claim "you're now
  // running on <model>".
  const [done, setDone] = useState<{
    agentModel: string;
    helperModel: string;
    framework: string;
    keyCheck: string;
    activated: boolean;
  } | null>(null);

  const { confirm, dialog: confirmDialog } = useConfirm();
  const selected = ONE_KEY_PROVIDERS.find((p) => p.id === providerType)!;
  const detected = useMemo(() => detectOfficialType(apiKey), [apiKey]);
  // NetMind joins the nudge sources because it is now the DEFAULT: real
  // NetMind keys are 32-hex with no sk- prefix, so any sk-* pasted while
  // NetMind is selected is a mis-pick worth flagging. Yunwu/OpenRouter
  // stay OUT of the predicate on purpose — their legitimate keys DO
  // start with sk-, and including them would flag every valid key.
  const mismatch =
    detected !== null &&
    detected !== providerType &&
    (providerType === 'anthropic' ||
      providerType === 'openai' ||
      providerType === 'netmind' ||
      detected === 'anthropic');

  const finishSuccess = (res: Awaited<ReturnType<typeof api.onboard>>) => {
    setApiKey('');
    // Explicit success state — in the welcome flow onComplete advances the step
    // immediately, but in Settings the card stays mounted and a
    // silent success reads as "nothing happened".
    setDone({
      agentModel: res.agent_model ?? '',
      helperModel: res.helper_model ?? '',
      framework: res.agent_framework ?? '',
      keyCheck: res.key_check ?? '',
      activated: res.activated !== false,
    });
    onComplete();
  };

  const handleStart = async () => {
    const key = apiKey.trim();
    if (!key) {
      setError(t('settings.provider.enterApiKey'));
      return;
    }
    setSubmitting(true);
    setError('');
    setDone(null);
    try {
      let res = await api.onboard(key, providerType);
      // Key rotation: the user already has a key for this provider. Confirm the
      // swap, then re-send with replace=true — the backend atomically switches
      // both slots to the new key (no manual delete-then-add dance).
      if (res.needs_replace) {
        const ok = await confirm({
          title: t('settings.provider.replaceKeyTitle'),
          message: t('settings.provider.replaceKeyMessage', {
            provider: selected.keyName,
            masked: res.existing_masked ?? '***',
          }),
          confirmText: t('settings.provider.replaceKey'),
          cancelText: t('settings.provider.keepCurrentKey'),
        });
        if (!ok) {
          setSubmitting(false);
          return;
        }
        res = await api.onboard(key, providerType, true);
      }
      if (res.success) {
        finishSuccess(res);
      } else {
        setError(res.detail || t('settings.provider.setupFailed'));
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t('settings.provider.networkError'));
    }
    setSubmitting(false);
  };

  const Surface = bare ? BareSurface : PaperCard;

  return (
    <Surface padding="lg">
      <div className="flex flex-col gap-4">
        {!hideHeader && (
          <div>
            <h2
              className="text-lg font-bold"
              style={{ color: 'var(--nm-ink)', fontFamily: 'var(--font-display)' }}
            >
              {t('settings.provider.oneKeyTitle')}
            </h2>
            <p className="text-sm mt-1" style={{ color: 'var(--nm-ink70)' }}>
              {t('settings.provider.oneKeyDescription')}
            </p>
          </div>
        )}

        {/* Radio ROWS, not a <select> (Owner-approved first-run design,
            2026-08-27): each row carries the vendor's real mark and its one-line
            consequence, so the choice is legible without opening a dropdown —
            and each option's description is visible at the same time as the
            others, which a select hides. */}
        <FormField label={t('settings.provider.providerLabel')}>
          <div className="flex flex-col gap-1.5">
            {ONE_KEY_PROVIDERS.map((p) => {
              const on = p.id === providerType;
              return (
                <button
                  key={p.id}
                  type="button"
                  role="radio"
                  aria-checked={on}
                  onClick={() => {
                    setProviderType(p.id);
                    if (error) setError('');
                  }}
                  className={cn(
                    'flex w-full items-center gap-2.5 rounded-[var(--radius-md)] border px-3 py-2.5 text-left transition-colors',
                    on
                      ? 'border-[var(--nm-ink30)] bg-[var(--nm-row-active)]'
                      : 'border-[var(--nm-hairline)] hover:bg-[var(--nm-row-hover)]',
                  )}
                >
                  <span
                    className={cn(
                      'grid h-3.5 w-3.5 shrink-0 place-items-center rounded-full border',
                      on ? 'border-[var(--nm-ink)]' : 'border-[var(--nm-ink30)]',
                    )}
                  >
                    {on && <span className="h-1.5 w-1.5 rounded-full bg-[var(--nm-ink)]" />}
                  </span>
                  <ProviderMark id={p.id} className="h-4 w-4 shrink-0" />
                  <span className="min-w-0 flex-1">
                    <span className="block text-[13px] font-medium text-[var(--nm-ink)]">
                      {p.labelKey.startsWith('settings.') ? t(p.labelKey) : p.labelKey}
                    </span>
                    <span className="mt-0.5 block text-[11px] leading-relaxed text-[var(--nm-ink50)]">
                      {t(p.descKey)}
                    </span>
                  </span>
                  {p.id === 'netmind' && (
                    <span className="shrink-0 rounded-[var(--radius-xs)] border border-[color:var(--color-silicon-hair)] bg-[var(--color-silicon-soft)] px-1.5 py-0.5 font-[family-name:var(--font-mono)] text-[9px] uppercase tracking-[0.10em] text-[var(--color-silicon)]">
                      {t('pages.login.recommended')}
                    </span>
                  )}
                </button>
              );
            })}
          </div>
        </FormField>

        <FormField label={t('settings.provider.apiKeyLabel')} error={error || undefined}>
          <div>
            <TextInput
              type="password"
              value={apiKey}
              placeholder={t('settings.provider.pasteApiKey')}
              autoComplete="off"
              onChange={(e) => {
                setApiKey(e.target.value);
                if (error) setError('');
                if (done) setDone(null);
              }}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !submitting) handleStart();
              }}
            />
            <div className="flex items-center justify-between mt-1.5">
              <a
                href={selected.getKeyUrl}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1 text-xs underline underline-offset-2 hover:opacity-80"
                style={{ color: 'var(--nm-ink70)' }}
              >
                <ExternalLink className="w-3 h-3" />
                {t('settings.provider.getProviderKey', { provider: selected.keyName })}
              </a>
              {mismatch && detected && (
                <button
                  type="button"
                  className="inline-flex items-center gap-1 text-xs underline underline-offset-2 hover:opacity-80"
                  style={{ color: 'var(--nm-ink70)' }}
                  onClick={() => setProviderType(detected)}
                >
                  <KeyRound className="w-3 h-3" />
                  {t('settings.provider.looksLikeKey', {
                    provider: detected === 'anthropic' ? 'Claude' : 'OpenAI',
                  })}
                </button>
              )}
            </div>
          </div>
        </FormField>

        <Button
          variant="accent"
          size="lg"
          disabled={submitting || !apiKey.trim()}
          onClick={handleStart}
          className="w-full"
        >
          {submitting ? (
            <>
              <Loader2 className="w-4 h-4 mr-2 animate-spin" />
              {t('settings.provider.settingUp')}
            </>
          ) : (
            <>
              {t('settings.provider.startUsing')}
              <ArrowRight className="w-4 h-4 ml-2" />
            </>
          )}
        </Button>

        {done && (
          <div
            className="flex items-start gap-2 text-sm rounded-[var(--radius-sm)] p-3"
            role="status"
            style={{
              background: 'var(--nm-paper-warm)',
              boxShadow: 'inset 0 0 0 1px var(--nm-hairline)',
              color: 'var(--nm-ink)',
            }}
          >
            <CheckCircle2
              className="w-4 h-4 mt-0.5 shrink-0"
              style={{ color: 'var(--color-success)' }}
            />
            <div>
              <div className="font-medium">
                {done.activated
                  ? t('settings.provider.allSet')
                  : t('settings.provider.oneKeySaved', 'Key saved')}
              </div>
              {done.activated ? (
                <div className="text-xs mt-0.5" style={{ color: 'var(--nm-ink70)' }}>
                  {t('settings.provider.agentSummary', { model: done.agentModel })}
                  {done.framework ? ` (${done.framework === 'codex_cli' ? 'Codex CLI' : 'Claude Code'})` : ''}
                  {' · '}{t('settings.provider.helperSummary', { model: done.helperModel })}
                </div>
              ) : (
                <div className="text-xs mt-0.5" style={{ color: 'var(--nm-ink70)' }}>
                  {t(
                    'settings.provider.oneKeyRegisterOnly',
                    "The cloud version keeps running on your NetMind account — this key wasn't activated here. To run models on your own keys, use the local desktop version.",
                  )}
                </div>
              )}
              {done.keyCheck.startsWith('unverified') && (
                <div className="text-xs mt-1" style={{ color: 'var(--color-warning)' }}>
                  {t('settings.provider.unverifiedKey', {
                    reason: done.keyCheck.replace(/^unverified \(|\)$/g, ''),
                  })}
                </div>
              )}
            </div>
          </div>
        )}
      </div>
      {confirmDialog}
    </Surface>
  );
}

export default OneKeyOnboard;
