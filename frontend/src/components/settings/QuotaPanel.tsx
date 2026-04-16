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
  const [data, setData] = useState<QuotaMeResponse | null>(null)
  const [loaded, setLoaded] = useState(false)

  useEffect(() => {
    if (mode !== 'cloud') {
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
  }, [mode])

  if (!loaded) return null
  if (mode !== 'cloud') return null
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
      {exhausted && (
        <div className="mt-2 text-xs text-[var(--accent-error)]">
          Free tier consumed. Add your own provider below to keep using
          the app.
        </div>
      )}
    </div>
  )
}
