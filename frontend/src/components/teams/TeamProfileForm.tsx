/**
 * @file_name: TeamProfileForm.tsx
 * @author: NarraNexus
 * @date: 2026-09-03
 * @description: A team's profile — name, colour, intro — as one form with
 * one Save. Nothing else.
 *
 * Extracted from TeamManagementModal so the team room's management tab can
 * offer "edit profile" WITHOUT mounting the whole team manager (whose left
 * column switches teams, creates teams, and whose delete does not leave the
 * room). Both the modal and the tab render this; the fields and the save
 * call exist once.
 *
 * One Save. Extra fields a caller renders through `children` are saved by
 * that same button (the caller merges them in `onSave`), so a dialog never
 * has two same-named saves that each store half and reset the other half's
 * draft.
 *
 * Seeds its draft from `team` and re-seeds when the team changes identity
 * or the server reports a newer `updated_at` — a room-side rename must not
 * be overwritten by a stale draft sitting here.
 */

import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Check, FileText, Loader2 } from 'lucide-react';
import { Button } from '@/components/ui';
import { cn } from '@/lib/utils';
import { COLOR_PRESETS } from './teamColors';

export interface TeamProfilePatch {
  name: string;
  color: string;
  intro_md: string;
}

export interface TeamProfileFormProps {
  team: { team_id: string; name: string; color?: string | null; intro_md?: string | null; updated_at?: string | null };
  onSave: (patch: TeamProfilePatch) => Promise<void>;
  /** Optional slot rendered on the same row as Save (the modal puts its
   *  delete button there; the management tab has its own delete section). */
  trailing?: React.ReactNode;
  /** Optional extra fields rendered ABOVE the Save row and saved by the same
   *  button — the modal puts its lead select here so the dialog has exactly
   *  one Save, and `onSave` merges whatever those fields hold. */
  children?: React.ReactNode;
  className?: string;
}

export function TeamProfileForm({ team, onSave, trailing, children, className }: TeamProfileFormProps) {
  const { t } = useTranslation();
  const [name, setName] = useState(team.name);
  const [color, setColor] = useState(team.color || COLOR_PRESETS[0]);
  const [intro, setIntro] = useState(team.intro_md || '');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setName(team.name);
    setColor(team.color || COLOR_PRESETS[0]);
    setIntro(team.intro_md || '');
    // Re-seed on identity or a newer server version only — not on every
    // render, or typing would be undone by the parent's re-render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [team.team_id, team.updated_at]);

  const save = async () => {
    setSaving(true);
    try {
      await onSave({ name, color, intro_md: intro });
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className={cn('space-y-4', className)} data-testid="team-profile-form">
      <div className="space-y-2">
        <label className="text-xs uppercase text-[var(--text-tertiary)]">{t('teams.nameLabel')}</label>
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          data-testid="team-profile-name"
          className="w-full px-3 py-2 text-sm font-mono bg-[var(--bg-tertiary)] border border-[var(--border-default)] focus:outline-none"
        />
        <div className="flex items-center gap-2 text-xs text-[var(--text-tertiary)]">
          <span>{t('teams.colorLabel')}</span>
          {COLOR_PRESETS.map((c) => (
            <button
              key={c}
              type="button"
              onClick={() => setColor(c)}
              aria-label={c}
              className={cn(
                'w-5 h-5 rounded-full',
                color === c ? 'ring-2 ring-offset-1 ring-[var(--text-primary)]' : '',
              )}
              style={{ backgroundColor: c }}
            />
          ))}
        </div>
      </div>

      <div className="space-y-2">
        <label className="text-xs uppercase text-[var(--text-tertiary)] flex items-center gap-1">
          <FileText className="w-3 h-3" /> {t('teams.introLabel')}
        </label>
        <textarea
          value={intro}
          onChange={(e) => setIntro(e.target.value)}
          rows={6}
          placeholder={t('teams.introPlaceholder', { name })}
          data-testid="team-profile-intro"
          className="w-full px-3 py-2 text-sm font-mono bg-[var(--bg-tertiary)] border border-[var(--border-default)] focus:outline-none resize-y"
        />
      </div>

      {children}

      <div className="flex justify-between">
        <Button onClick={save} disabled={saving} size="sm" className="gap-1" data-testid="team-profile-save">
          {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Check className="w-3.5 h-3.5" />}
          {t('teams.saveChanges')}
        </Button>
        {trailing}
      </div>
    </div>
  );
}
