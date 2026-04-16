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
    accent === 'warn' ? 'var(--accent-error)' : 'var(--accent-primary)'
  return (
    <div className="mb-2 last:mb-0">
      <div className="flex justify-between text-xs text-[var(--text-secondary)] mb-1">
        <span>{label}</span>
        <span>
          {used.toLocaleString()} / {total.toLocaleString()}
        </span>
      </div>
      <div className="h-1.5 rounded-full bg-[var(--surface-muted)] overflow-hidden">
        <div
          className="h-full rounded-full"
          style={{ width: `${p}%`, backgroundColor: fill }}
        />
      </div>
    </div>
  )
}

export function QuotaPanel() {
  const mode = useRuntimeStore((s) => s.mode)
  const isCloud = mode === 'cloud-app' || mode === 'cloud-web'
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
      <div className="rounded-md border border-[var(--border-muted)] bg-[var(--surface-1)] p-3 text-sm text-[var(--text-secondary)]">
        System free-tier quota is not set up for your account yet.
        Please contact an administrator, or configure your own provider
        below to continue.
      </div>
    )
  }

  const exhausted = data.status === 'exhausted'
  const borderCls = exhausted
    ? 'border-[var(--accent-error)]'
    : 'border-[var(--border-muted)]'
  const inputTotal = data.initial_input_tokens + data.granted_input_tokens
  const outputTotal = data.initial_output_tokens + data.granted_output_tokens
  const preferSystem = data.prefer_system_override

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
      className={`rounded-md border ${borderCls} bg-[var(--surface-1)] p-3`}
    >
      <div className="flex items-center justify-between mb-2">
        <h4 className="text-sm font-medium text-[var(--text-primary)]">
          System Free-Tier Quota
          {exhausted && (
            <span className="ml-2 text-xs text-[var(--accent-error)]">
              (exhausted)
            </span>
          )}
        </h4>
        <span className="text-xs text-[var(--text-secondary)]">
          status: {data.status}
        </span>
      </div>
      <Bar
        label="Input tokens"
        used={data.used_input_tokens}
        total={inputTotal}
        accent={exhausted ? 'warn' : 'ok'}
      />
      <Bar
        label="Output tokens"
        used={data.used_output_tokens}
        total={outputTotal}
        accent={exhausted ? 'warn' : 'ok'}
      />
      <div className="mt-3 pt-3 border-t border-[var(--border-muted)]">
        <label className="flex items-center gap-2 cursor-pointer text-xs text-[var(--text-secondary)]">
          <input
            type="checkbox"
            checked={preferSystem}
            onChange={togglePreference}
            className="accent-[var(--accent-primary)]"
          />
          <span>
            Use free-tier quota even when I have my own provider
            configured
          </span>
        </label>
        <div className="mt-1 text-[11px] text-[var(--text-tertiary)] pl-6">
          {preferSystem
            ? 'Currently: routing through system provider — free-tier usage applies. Falls back to your own provider if the free tier runs out.'
            : 'Currently: using your own provider when configured; free tier only applies when you have no provider set.'}
        </div>
      </div>
      {exhausted && (
        <div className="mt-2 text-xs text-[var(--accent-error)]">
          Free tier consumed. Add your own provider below, or uncheck
          the toggle above to route through your existing provider.
        </div>
      )}
    </div>
  )
}
