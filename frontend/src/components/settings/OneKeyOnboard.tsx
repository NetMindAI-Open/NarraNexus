/**
 * @file_name: OneKeyOnboard.tsx
 * @author: NarraNexus
 * @date: 2026-06-10
 * @description: One-key setup card — pick a provider, paste one key, go.
 *
 * THE single quick-setup surface, shared by first-run /setup (primary
 * card) and Settings → Providers (Step 1). Provider picker covers the
 * five one-key sources (NetMind recommended, official Claude / OpenAI,
 * Yunwu, OpenRouter); submission goes through POST /api/providers/onboard
 * which wires the agent framework + provider + BOTH slots server-side —
 * unlike the old Quick Add path, this also switches the framework, which
 * an official OpenAI key requires (codex_cli).
 *
 * Semantics: "make this key my active setup" — both slots are
 * (re)assigned to the new provider with recommended defaults. Per-slot
 * fine-tuning lives in the Advanced area (ProviderSettings).
 */

import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ArrowRight, CheckCircle2, ExternalLink, KeyRound, Loader2 } from 'lucide-react';
import { Button, useConfirm } from '@/components/ui';
import { PaperCard, FormField, TextInput } from '@/components/nm';
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
  {
    id: 'anthropic',
    labelKey: 'settings.provider.officialAnthropic',
    keyName: 'Anthropic',
    descKey: 'settings.provider.officialAnthropicDesc',
    getKeyUrl: 'https://console.anthropic.com/settings/keys',
  },
  {
    id: 'openai',
    labelKey: 'settings.provider.officialOpenai',
    keyName: 'OpenAI',
    descKey: 'settings.provider.officialOpenaiDesc',
    getKeyUrl: 'https://platform.openai.com/api-keys',
  },
  {
    id: 'netmind',
    labelKey: 'NetMind.AI Power',
    keyName: 'NetMind',
    descKey: 'settings.provider.netmindDesc',
    getKeyUrl: 'https://www.netmind.ai/user/dashboard',
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

interface OneKeyOnboardProps {
  /** Called after the backend confirms everything is wired. */
  onComplete: () => void;
}

export function OneKeyOnboard({ onComplete }: OneKeyOnboardProps) {
  const { t } = useTranslation();
  const [providerType, setProviderType] = useState<OnboardProviderType>('anthropic');
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
  const mismatch =
    detected !== null &&
    detected !== providerType &&
    (providerType === 'anthropic' || providerType === 'openai' || detected === 'anthropic');

  const finishSuccess = (res: Awaited<ReturnType<typeof api.onboard>>) => {
    setApiKey('');
    // Explicit success state — on /setup onComplete navigates away
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

  return (
    <PaperCard padding="lg">
      <div className="flex flex-col gap-4">
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

        <FormField label={t('settings.provider.providerLabel')}>
          <div>
            <select
              value={providerType}
              onChange={(e) => {
                setProviderType(e.target.value as OnboardProviderType);
                if (error) setError('');
              }}
              className="w-full px-3 h-10 text-sm rounded-[var(--radius-sm)] outline-none"
              style={{
                background: 'var(--nm-paper-warm)',
                boxShadow: 'inset 0 0 0 1px var(--nm-hairline)',
                color: 'var(--nm-ink)',
              }}
            >
              {ONE_KEY_PROVIDERS.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.labelKey.startsWith('settings.') ? t(p.labelKey) : p.labelKey}
                </option>
              ))}
            </select>
            <p className="text-xs mt-1.5" style={{ color: 'var(--nm-ink50)' }}>
              {t(selected.descKey)}
            </p>
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
              style={{ color: 'var(--color-success, #16a34a)' }}
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
                <div className="text-xs mt-1" style={{ color: 'var(--color-warning, #b45309)' }}>
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
    </PaperCard>
  );
}

export default OneKeyOnboard;
