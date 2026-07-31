/**
 * JobScheduleEditDialog - dialog for editing a job's execution time.
 *
 * The form adapts to the job type and its current trigger_config:
 *   - one_off               → datetime-local for run_at
 *   - scheduled/ongoing+cron     → cron expression text input
 *   - scheduled/ongoing+interval → interval (seconds) number input
 * Every mode can change the IANA timezone. Only changed fields are submitted
 * (matching the backend exclude_none semantics); the backend recomputes
 * next_run. scheduled/ongoing jobs may switch cron <-> interval; one_off is
 * fixed to run_at (a true job_type conversion is out of scope here).
 */

import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Loader2, Clock } from 'lucide-react';
import { Dialog, DialogContent, DialogFooter, Input, Button } from '@/components/ui';
import type { Job } from '@/types/api';

interface JobScheduleEditDialogProps {
  job: Job;
  isOpen: boolean;
  saving: boolean;
  onClose: () => void;
  onSave: (fields: { run_at?: string; cron?: string; interval_seconds?: number; timezone?: string }) => void;
}

type Mode = 'run_at' | 'cron' | 'interval';

/** IANA timezone list: prefer the runtime-provided full set, fall back to a
 *  common subset. */
function listTimezones(current?: string): string[] {
  let zones: string[] = [];
  try {
    // Intl.supportedValuesOf is available in modern browsers / Tauri webview.
    const anyIntl = Intl as unknown as { supportedValuesOf?: (k: string) => string[] };
    if (anyIntl.supportedValuesOf) zones = anyIntl.supportedValuesOf('timeZone');
  } catch {
    zones = [];
  }
  if (zones.length === 0) {
    zones = ['UTC', 'Asia/Shanghai', 'Asia/Tokyo', 'America/New_York', 'America/Los_Angeles', 'Europe/London', 'Europe/Paris'];
  }
  if (current && !zones.includes(current)) zones = [current, ...zones];
  return zones;
}

/** Current instant as a naive ISO ("YYYY-MM-DDTHH:mm:ss") in the given
 *  timezone, used for the past-time guard on one_off run_at. */
function nowInTz(tz: string): string {
  try {
    const parts = new Intl.DateTimeFormat('en-CA', {
      timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
    }).formatToParts(new Date());
    const get = (t: string) => parts.find((p) => p.type === t)?.value ?? '00';
    return `${get('year')}-${get('month')}-${get('day')}T${get('hour')}:${get('minute')}:${get('second')}`;
  } catch {
    return new Date().toISOString().slice(0, 19);
  }
}

export function JobScheduleEditDialog({ job, isOpen, saving, onClose, onSave }: JobScheduleEditDialogProps) {
  const { t } = useTranslation();
  const tc = job.trigger_config ?? {};

  const originalMode: Mode = useMemo(() => {
    if (job.job_type === 'one_off') return 'run_at';
    if (tc.cron) return 'cron';
    if (tc.interval_seconds != null) return 'interval';
    return 'cron';
  }, [job.job_type, tc.cron, tc.interval_seconds]);

  // one_off is fixed to run_at; scheduled/ongoing may switch cron <-> interval.
  const canSwitchMode = job.job_type !== 'one_off';

  const originalTz = (tc.timezone as string) || job.next_run_timezone || 'UTC';
  // datetime-local wants "YYYY-MM-DDTHH:mm"; trim seconds off the stored value.
  const originalRunAt = (tc.run_at as string | undefined)?.slice(0, 16) || '';

  const [mode, setMode] = useState<Mode>(originalMode);
  const [runAt, setRunAt] = useState(originalRunAt);
  const [cron, setCron] = useState((tc.cron as string) || '');
  const [interval, setInterval] = useState(tc.interval_seconds != null ? String(tc.interval_seconds) : '');
  const [timezone, setTimezone] = useState(originalTz);
  const [error, setError] = useState<string | null>(null);

  const timezones = useMemo(() => listTimezones(originalTz), [originalTz]);

  const handleSave = () => {
    setError(null);
    const fields: { run_at?: string; cron?: string; interval_seconds?: number; timezone?: string } = {};

    if (mode === 'run_at') {
      if (!runAt) { setError(t('jobs.editSchedule.invalidRunAt')); return; }
      const runAtFull = runAt.length === 16 ? `${runAt}:00` : runAt;
      if (runAtFull <= nowInTz(timezone)) { setError(t('jobs.editSchedule.pastTimeError')); return; }
      if (runAtFull !== (tc.run_at as string)) fields.run_at = runAtFull;
    } else if (mode === 'cron') {
      const trimmed = cron.trim();
      if (!trimmed) { setError(t('jobs.editSchedule.invalidCron')); return; }
      // On a mode switch (interval → cron) always send cron; the backend clears
      // the stale interval_seconds. Otherwise send only when the value changed.
      if (mode !== originalMode || trimmed !== (tc.cron as string)) fields.cron = trimmed;
    } else {
      const n = Number(interval);
      if (!Number.isFinite(n) || n <= 0) { setError(t('jobs.editSchedule.invalidInterval')); return; }
      if (mode !== originalMode || n !== tc.interval_seconds) fields.interval_seconds = n;
    }

    if (timezone && timezone !== originalTz) fields.timezone = timezone;

    if (Object.keys(fields).length === 0) { onClose(); return; }
    onSave(fields);
  };

  return (
    <Dialog isOpen={isOpen} onClose={onClose} title={t('jobs.editSchedule.title')} size="md">
      <DialogContent>
        <div className="space-y-4 text-sm">
          <p className="text-xs text-[var(--text-tertiary)]">{t('jobs.editSchedule.hint')}</p>

          {canSwitchMode && (
            <div className="space-y-1.5">
              <span className="block text-xs text-[var(--text-secondary)]">{t('jobs.editSchedule.modeLabel')}</span>
              <div className="grid grid-cols-2 gap-1 p-1 rounded-lg bg-[var(--bg-sunken)] border border-[var(--border-subtle)]">
                {(['interval', 'cron'] as const).map((m) => (
                  <button
                    key={m}
                    type="button"
                    onClick={() => { setMode(m); setError(null); }}
                    className={
                      'py-1.5 text-xs rounded-md transition-colors ' +
                      (mode === m
                        ? 'bg-[var(--text-primary)] text-[var(--text-inverse)] font-medium'
                        : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]')
                    }
                  >
                    {m === 'interval' ? t('jobs.editSchedule.modeInterval') : t('jobs.editSchedule.modeCron')}
                  </button>
                ))}
              </div>
            </div>
          )}

          {mode === 'run_at' && (
            <label className="block space-y-1">
              <span className="text-xs text-[var(--text-secondary)]">{t('jobs.editSchedule.runAt')}</span>
              <Input
                type="datetime-local"
                value={runAt}
                onChange={(e) => setRunAt(e.target.value)}
                icon={<Clock className="w-3.5 h-3.5" />}
              />
            </label>
          )}

          {mode === 'cron' && (
            <label className="block space-y-1">
              <span className="text-xs text-[var(--text-secondary)]">{t('jobs.editSchedule.cron')}</span>
              <Input
                type="text"
                value={cron}
                placeholder="0 8 * * *"
                onChange={(e) => setCron(e.target.value)}
                className="font-mono"
              />
              <span className="text-[10px] text-[var(--text-tertiary)]">{t('jobs.editSchedule.cronHint')}</span>
            </label>
          )}

          {mode === 'interval' && (
            <label className="block space-y-1">
              <span className="text-xs text-[var(--text-secondary)]">{t('jobs.editSchedule.intervalSeconds')}</span>
              <Input
                type="number"
                min={1}
                value={interval}
                placeholder="3600"
                onChange={(e) => setInterval(e.target.value)}
              />
            </label>
          )}

          <label className="block space-y-1">
            <span className="text-xs text-[var(--text-secondary)]">{t('jobs.editSchedule.timezone')}</span>
            <select
              value={timezone}
              onChange={(e) => setTimezone(e.target.value)}
              className="w-full rounded-lg bg-[var(--bg-sunken)] border border-[var(--border-subtle)] px-3 py-2 text-sm text-[var(--text-primary)] focus:border-[var(--border-strong)] outline-none"
            >
              {timezones.map((tz) => (
                <option key={tz} value={tz}>{tz}</option>
              ))}
            </select>
          </label>

          {error && (
            <p className="text-xs text-[var(--color-error)]">{error}</p>
          )}
        </div>
      </DialogContent>
      <DialogFooter>
        <Button variant="ghost" size="sm" onClick={onClose} disabled={saving}>
          {t('jobs.editSchedule.cancel')}
        </Button>
        <Button variant="default" size="sm" onClick={handleSave} disabled={saving}>
          {saving ? (
            <>
              <Loader2 className="w-3.5 h-3.5 mr-1.5 animate-spin" />
              {t('jobs.action.savingSchedule')}
            </>
          ) : (
            t('jobs.editSchedule.save')
          )}
        </Button>
      </DialogFooter>
    </Dialog>
  );
}

export default JobScheduleEditDialog;
