/**
 * @file QuotaPanel.tsx
 * @author Bin Liang
 * @date 2026-04-16
 * @description System-default free-tier quota display.
 *
 * Renders only in cloud mode and only when the backend reports the
 * feature enabled. In local mode or when disabled server-side, the
 * component returns null — no layout shift, no "feature off" copy.
 */

import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { api } from '@/lib/api'
import type { QuotaMeResponse } from '@/types'
import { useRuntimeStore } from '@/stores/runtimeStore'

function pct(used: number, total: number): number {
  if (total <= 0) return 0
  return Math.min(100, Math.floor((used / total) * 100))
}

function Bar({
  label,
  used,
  total,
  accent,
}: {
  label: string
  used: number
  total: number
  accent: 'ok' | 'warn'
}) {
  const p = pct(used, total)
  const fill =
    accent === 'warn' ? 'var(--color-error)' : 'var(--accent-primary)'
  return (
    <div className="mb-2 last:mb-0">
      <div className="flex justify-between text-xs text-[var(--text-secondary)] mb-1">
        <span>{label}</span>
        <span>
          {used.toLocaleString()} / {total.toLocaleString()}
        </span>
      </div>
      <div className="h-1.5 rounded-full bg-[var(--bg-sunken)] overflow-hidden">
        <div
          className="h-full rounded-full"
          style={{ width: `${p}%`, backgroundColor: fill }}
        />
      </div>
    </div>
  )
}

export function QuotaPanel() {
  const { t } = useTranslation()
  const mode = useRuntimeStore((s) => s.mode)
  const isCloud = mode === 'cloud-web'
  const [data, setData] = useState<QuotaMeResponse | null>(null)
  const [loaded, setLoaded] = useState(false)

  useEffect(() => {
    if (!isCloud) {
      setLoaded(true)
      return
    }
    let cancelled = false
    api
      .getMyQuota()
      .then((r) => {
        if (!cancelled) {
          setData(r)
          setLoaded(true)
        }
      })
      .catch(() => {
        if (!cancelled) setLoaded(true)
      })
    return () => {
      cancelled = true
    }
  }, [isCloud])

  if (!loaded) return null
  if (!isCloud) return null
  if (!data || data.enabled === false) return null

  if (data.status === 'uninitialized') {
    return (
      <div className="rounded-md border border-[var(--border-default)] bg-[var(--bg-primary)] p-3 text-sm text-[var(--text-secondary)]">
        {t('settings.quota.uninitialized')}
      </div>
    )
  }

  const exhausted = data.status === 'exhausted'
  const borderCls = exhausted
    ? 'border-[var(--color-error)]'
    : 'border-[var(--border-default)]'
  const inputTotal = data.initial_input_tokens + data.granted_input_tokens
  const outputTotal = data.initial_output_tokens + data.granted_output_tokens
  const preferSystem = data.prefer_system_override
  // #48: the toggle is only LOCKED when the free tier is exhausted AND already
  // off — turning the free tier ON needs budget, but turning it OFF (to route
  // through your own provider) must ALWAYS be allowed, matching the backend's
  // `set_preference` guard ("OFF is always allowed"). Previously this was
  // `disabled={exhausted}`, which trapped an opted-in user: exhausted → greyed
  // → could not uncheck → 402 loop.
  const freeTierLocked = exhausted && !preferSystem

  const togglePreference = async () => {
    try {
      const next = await api.setQuotaPreference(!preferSystem)
      setData(next)
    } catch {
      // swallow — the UI will simply stay on the previous state
    }
  }

  return (
    <div
      className={`rounded-md border ${borderCls} bg-[var(--bg-primary)] p-3`}
    >
      <div className="flex items-center justify-between mb-2">
        <h4 className="text-sm font-medium text-[var(--text-primary)]">
          {t('settings.quota.title')}
          {exhausted && (
            <span className="ml-2 text-xs text-[var(--color-error)]">
              {t('settings.quota.exhaustedTag')}
            </span>
          )}
        </h4>
        <span className="text-xs text-[var(--text-secondary)]">
          {t('settings.quota.statusLabel', { status: data.status })}
        </span>
      </div>
      <Bar
        label={t('settings.quota.inputTokens')}
        used={data.used_input_tokens}
        total={inputTotal}
        accent={exhausted ? 'warn' : 'ok'}
      />
      <Bar
        label={t('settings.quota.outputTokens')}
        used={data.used_output_tokens}
        total={outputTotal}
        accent={exhausted ? 'warn' : 'ok'}
      />
      <div className="mt-3 pt-3 border-t border-[var(--border-subtle)]">
        <label
          className={`flex items-center gap-2 text-xs text-[var(--text-secondary)] ${
            freeTierLocked ? 'cursor-not-allowed opacity-60' : 'cursor-pointer'
          }`}
        >
          <input
            type="checkbox"
            checked={preferSystem}
            onChange={togglePreference}
            disabled={freeTierLocked}
            className="accent-[var(--accent-primary)]"
          />
          <span>
            {t('settings.quota.preferToggle')}
          </span>
        </label>
        <div className="mt-1 text-[11px] text-[var(--text-tertiary)] pl-6">
          {!exhausted
            ? preferSystem
              ? t('settings.quota.preferOn')
              : t('settings.quota.preferOff')
            : preferSystem
              ? t('settings.quota.exhaustedPreferOn')
              : t('settings.quota.exhaustedPreferOff')}
        </div>
      </div>
      {exhausted && (
        <div className="mt-2 text-xs text-[var(--color-error)]">
          {t('settings.quota.exhaustedNote')}
        </div>
      )}
    </div>
  )
}
