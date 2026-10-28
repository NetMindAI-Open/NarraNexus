/**
 * @file_name: useOauthAllowed.ts
 * @author: NarraNexus
 * @date: 2026-08-28
 * @description: May this caller use OAuth subscription cards?
 *
 * `null` while the probe is in flight; `false` only for cloud non-staff
 * (the status routes' `allowed` flag — the same is_cloud+not-staff
 * predicate that 403s OAuth card types). Callers use it to hide ENTRY
 * POINTS (ProviderSettings drops the Sign-in tab); SubscriptionConnect
 * itself still self-explains when rendered anyway.
 *
 * Probe failure fails OPEN (returns true) — a failed probe is not a
 * verdict, and the backend 403 remains the actual security boundary.
 * The `enabled` flag defers the probe until the consumer actually needs
 * the answer (the status route spawns a real `claude auth status`
 * subprocess on local — don't pay that for a closed modal).
 */
import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { useConfigStore } from '@/stores';

export function useOauthAllowed(enabled: boolean = true): boolean | null {
  const userId = useConfigStore((sel) => sel.userId);
  const [allowed, setAllowed] = useState<boolean | null>(null);
  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    api
      .getClaudeStatus()
      .then((res) => {
        if (!cancelled) setAllowed(res.data?.allowed !== false);
      })
      .catch(() => {
        if (!cancelled) setAllowed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [enabled, userId]);
  return allowed;
}

export default useOauthAllowed;
