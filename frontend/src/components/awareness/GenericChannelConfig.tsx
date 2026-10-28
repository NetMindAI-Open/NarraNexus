/**
 * @file_name: GenericChannelConfig.tsx
 * @author: Bin Liang
 * @date: 2026-09-04
 * @description: Schema-driven config panel for any channel in ingress.channels (plugin platform batch 4b).
 *
 * Renders the channel's credential schema (from GET /api/channels/{channel}/schema)
 * as a bind form — secrets as password inputs, selects, booleans — and, once
 * bound, the public identity fields with test / activate / unbind. A channel
 * plugin registers `makeGenericChannelConfig('<channel>')` as its Channels row
 * component; the six builtins keep their bespoke panels until batch 4d.
 */
import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';

import { Button } from '@/components/nm';
import { api } from '@/lib/api';
import { useConfigStore } from '@/stores';
import type { ChannelCredentialView, ChannelSchema, ChannelSchemaField } from '@/types';
import type { ChannelConfigProps } from '@/platform/registries';

interface Props extends ChannelConfigProps {
  channel: string;
}

const INPUT = 'w-full rounded-[var(--radius-sm)] border border-[var(--nm-hairline)] bg-[var(--nm-card)] px-2.5 py-1.5 text-sm text-[var(--nm-ink)]';

function Field({ field, value, onChange }: { field: ChannelSchemaField; value: string; onChange: (v: string) => void }) {
  const id = `chan-field-${field.name}`;
  if (field.kind === 'bool') {
    return (
      <label className="flex items-center gap-2 text-sm" htmlFor={id}>
        <input id={id} type="checkbox" checked={value === 'true'} onChange={(e) => onChange(e.target.checked ? 'true' : 'false')} />
        {field.label}
      </label>
    );
  }
  if (field.kind === 'select') {
    return (
      <label className="block text-sm" htmlFor={id}>
        <span className="block text-xs text-[var(--nm-ink70)] mb-1">{field.label}{field.required ? ' *' : ''}</span>
        <select id={id} className={INPUT} value={value} onChange={(e) => onChange(e.target.value)}>
          <option value="">—</option>
          {field.options.map((o) => (
            <option key={o} value={o}>{o}</option>
          ))}
        </select>
      </label>
    );
  }
  return (
    <label className="block text-sm" htmlFor={id}>
      <span className="block text-xs text-[var(--nm-ink70)] mb-1">{field.label}{field.required ? ' *' : ''}</span>
      <input
        id={id}
        className={INPUT}
        type={field.kind === 'secret' ? 'password' : field.kind === 'int' ? 'number' : field.kind === 'url' ? 'url' : 'text'}
        value={value}
        autoComplete="off"
        aria-label={field.label}
        onChange={(e) => onChange(e.target.value)}
      />
      {field.help && <span className="block text-[11px] text-[var(--nm-ink50)] mt-0.5">{field.help}</span>}
    </label>
  );
}

export function GenericChannelConfig({ channel, onBindStateChange }: Props) {
  const { t } = useTranslation();
  const agentId = useConfigStore((s) => s.agentId);
  const [schema, setSchema] = useState<ChannelSchema | null>(null);
  const [credential, setCredential] = useState<ChannelCredentialView | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  const load = useCallback(async () => {
    if (!agentId) return;
    setError('');
    try {
      const [s, c] = await Promise.all([api.channelSchema(channel), api.channelCredential(channel, agentId)]);
      if (!s.success || !s.data) throw new Error(s.error || t('awareness.generic.errLoad'));
      setSchema(s.data);
      setCredential(c.success && c.data ? c.data : null);
    } catch (e) {
      setError(e instanceof Error ? e.message : t('awareness.generic.errLoad'));
    }
  }, [agentId, channel, t]);

  useEffect(() => {
    void load();
  }, [load]);

  const run = async (key: string, fn: () => Promise<{ success: boolean; error?: string }>, done?: string) => {
    if (!agentId) return;
    setBusy(key);
    setError('');
    setNotice('');
    try {
      const res = await fn();
      if (!res.success) {
        setError(res.error || t('awareness.generic.errBind'));
        return;
      }
      if (done) setNotice(done);
      await load();
      onBindStateChange?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : t('awareness.generic.errBind'));
    } finally {
      setBusy(null);
    }
  };

  const bind = (e: FormEvent) => {
    e.preventDefault();
    if (!agentId || !schema) return;
    const missing = schema.bind_fields.filter((f) => f.required && !(values[f.name] ?? '').trim());
    if (missing.length) {
      setError(t('awareness.generic.fieldRequired', { field: missing[0].label }));
      return;
    }
    const fields: Record<string, unknown> = {};
    for (const f of schema.bind_fields) {
      const v = values[f.name];
      if (v === undefined || v === '') continue;
      fields[f.name] = f.kind === 'bool' ? v === 'true' : f.kind === 'int' ? Number(v) : v;
    }
    void run('bind', () => api.channelBind(channel, agentId, fields), t('awareness.generic.bound'));
  };

  if (!agentId) return null;
  if (error && !schema) return <p className="text-sm text-[var(--color-error)]" role="alert">{error}</p>;
  if (!schema) return <p className="text-sm text-[var(--nm-ink50)]">{t('awareness.generic.loading')}</p>;

  if (credential) {
    const identity = schema.fields.filter((f) => f.public && credential[f.name] !== undefined && credential[f.name] !== null && credential[f.name] !== '');
    return (
      <div className="space-y-3" data-testid={`generic-channel-${channel}`}>
        <div className="text-xs text-[var(--nm-ink70)]">
          {t('awareness.generic.boundAs', { name: schema.display_name })}
          {credential.enabled ? '' : ` · ${t('awareness.generic.inactive')}`}
        </div>
        {!credential.enabled && credential.disabled_reason && (
          <div className="text-xs text-[var(--color-error)] break-words" data-testid="channel-disabled-reason">
            {t('channelActiveToggle.disabledReason', { reason: credential.disabled_reason })}
          </div>
        )}
        {identity.length > 0 && (
          <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
            {identity.map((f) => (
              <div key={f.name} className="contents">
                <dt className="text-[var(--nm-ink50)]">{f.label}</dt>
                <dd className="font-mono text-[var(--nm-ink)]">{String(credential[f.name])}</dd>
              </div>
            ))}
          </dl>
        )}
        {schema.transport === 'webhook' && <p className="text-[11px] text-[var(--nm-ink50)]">{t('awareness.generic.webhookHint', { path: `/api/channels/${channel}/webhook/${agentId}` })}</p>}
        <div className="flex flex-wrap items-center gap-2">
          {schema.has_test && (
            <Button size="sm" variant="secondary" disabled={busy !== null} loading={busy === 'test'} onClick={() => void run('test', () => api.channelTest(channel, agentId), t('awareness.generic.testOk'))}>
              {t('awareness.generic.test')}
            </Button>
          )}
          <Button size="sm" variant="secondary" disabled={busy !== null} loading={busy === 'active'} onClick={() => void run('active', () => api.channelSetActive(channel, agentId, !credential.enabled))}>
            {credential.enabled ? t('awareness.generic.deactivate') : t('awareness.generic.activate')}
          </Button>
          <Button size="sm" variant="ghost" disabled={busy !== null} loading={busy === 'unbind'} onClick={() => void run('unbind', () => api.channelUnbind(channel, agentId), t('awareness.generic.unbound'))}>
            {t('awareness.generic.unbind')}
          </Button>
        </div>
        {error && <p className="text-xs text-[var(--color-error)]" role="alert">{error}</p>}
        {notice && <p className="text-xs text-[var(--color-success)]" role="status">{notice}</p>}
      </div>
    );
  }

  if (!schema.has_bind) return <p className="text-sm text-[var(--nm-ink50)]">{t('awareness.generic.ownFlow', { name: schema.display_name })}</p>;

  return (
    <form className="space-y-3" onSubmit={bind} data-testid={`generic-channel-${channel}`}>
      {schema.bind_fields.map((f) => (
        <Field key={f.name} field={f} value={values[f.name] ?? ''} onChange={(v) => setValues((s) => ({ ...s, [f.name]: v }))} />
      ))}
      <div className="flex items-center gap-2">
        <Button size="sm" type="submit" disabled={busy !== null} loading={busy === 'bind'}>
          {t('awareness.generic.bind')}
        </Button>
      </div>
      {error && <p className="text-xs text-[var(--color-error)]" role="alert">{error}</p>}
    </form>
  );
}
