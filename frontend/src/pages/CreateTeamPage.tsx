/**
 * @file_name: CreateTeamPage.tsx
 * @author:
 * @date: 2026-08-06
 * @description: Chat UI v4 — team creation as a full page (the sidebar New
 * menu's "Create team" entry). Pure creation only: name, description, color
 * swatch, and an agent multi-select checklist. Management of existing teams
 * (rename / intro / members / delete) lives in the Dashboard's Teams view.
 *
 * Member selection is DEFERRED: nothing hits the API until Create — the
 * page first createTeam({name, description, color}), then loops addMember
 * for each checked agent (per-row best effort, failures surfaced via
 * useNotice — never window.alert, wry drops it). On success it navigates
 * straight into the new team's group chat so the result is visible.
 */
import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { ArrowLeft, Loader2, Search } from 'lucide-react';
import { Button, ScrollArea, useNotice } from '@/components/ui';
import { RingAvatar } from '@/components/nm';
import { useConfigStore, useTeamsStore } from '@/stores';
import { cn } from '@/lib/utils';

import { COLOR_PRESETS } from '@/components/teams/teamColors';

export default function CreateTeamPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const agents = useConfigStore((s) => s.agents);
  const createTeam = useTeamsStore((s) => s.createTeam);
  const addMember = useTeamsStore((s) => s.addMember);
  const { notifyError, dialog } = useNotice();

  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [color, setColor] = useState(COLOR_PRESETS[0]);
  const [members, setMembers] = useState<Set<string>>(new Set());
  const [memberQuery, setMemberQuery] = useState('');
  const [busy, setBusy] = useState(false);

  const canCreate = name.trim().length > 0 && !busy;
  const selectedCount = members.size;

  // Search narrows the checklist by name / id; selections OUTSIDE the
  // current filter are kept (the Set is independent of the view).
  const matchedAgents = useMemo(() => {
    const q = memberQuery.trim().toLowerCase();
    if (!q) return [...agents];
    return agents.filter(
      (a) =>
        (a.name || '').toLowerCase().includes(q) ||
        a.agent_id.toLowerCase().includes(q),
    );
  }, [agents, memberQuery]);

  const toggleMember = (agentId: string) => {
    setMembers((prev) => {
      const next = new Set(prev);
      if (next.has(agentId)) next.delete(agentId);
      else next.add(agentId);
      return next;
    });
  };

  const handleCreate = async () => {
    if (!canCreate) return;
    setBusy(true);
    try {
      const teamId = await createTeam({
        name: name.trim(),
        description: description.trim() || undefined,
        color,
      });
      if (!teamId) throw new Error('createTeam returned no team_id');
      const failed: string[] = [];
      for (const aid of Array.from(members)) {
        try {
          await addMember(teamId, aid);
        } catch {
          failed.push(aid);
        }
      }
      if (failed.length) {
        await notifyError(
          t('pages.createTeam.membersFailed', {
            count: failed.length,
            ids: failed.slice(0, 3).join(', '),
          }),
        );
      }
      navigate(`/app/teams/${teamId}/chat`);
    } catch (e) {
      await notifyError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const fieldLabel =
    'font-mono text-[11px] uppercase tracking-[0.1em] text-[var(--nm-ink50)]';
  const inputCls =
    'w-full rounded-[var(--radius-sm)] border border-[var(--border-subtle)] bg-[var(--nm-card)] px-3 text-[13px] text-[var(--nm-ink)] placeholder:text-[var(--nm-ink30)] focus:outline-none focus:border-[var(--border-strong)]';

  return (
    <div className="h-full flex flex-col">
    <ScrollArea className="flex-1 min-h-0" viewportClassName="px-6 py-7">
      <div className="max-w-[600px] mx-auto flex flex-col gap-5">
        {dialog}

        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={() => navigate('/app/chat')}
            title={t('pages.createTeam.back')}
            aria-label={t('pages.createTeam.back')}
            className="inline-flex h-[30px] w-[30px] items-center justify-center rounded-[var(--radius-sm)] text-[var(--nm-ink50)] transition-colors hover:bg-[var(--nm-paper-warm)] hover:text-[var(--nm-ink)]"
          >
            <ArrowLeft className="h-4 w-4" />
          </button>
          <h1
            className="text-xl font-bold tracking-tight"
            style={{ color: 'var(--nm-ink)', fontFamily: 'var(--font-display)' }}
          >
            {t('pages.createTeam.title')}
          </h1>
        </div>

        <div className="flex flex-col gap-1.5">
          <span className={fieldLabel}>{t('pages.createTeam.nameLabel')}</span>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={t('pages.createTeam.namePlaceholder')}
            className={cn(inputCls, 'h-9')}
          />
        </div>

        <div className="flex flex-col gap-1.5">
          <span className={fieldLabel}>{t('pages.createTeam.descLabel')}</span>
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder={t('pages.createTeam.descPlaceholder')}
            rows={3}
            className={cn(inputCls, 'resize-none py-2.5 leading-relaxed')}
          />
        </div>

        <div className="flex flex-col gap-1.5">
          <span className={fieldLabel}>{t('pages.createTeam.colorLabel')}</span>
          <div className="flex items-center gap-1.5">
            {COLOR_PRESETS.map((c) => (
              <button
                key={c}
                type="button"
                onClick={() => setColor(c)}
                aria-label={c}
                className={cn(
                  'h-[18px] w-[18px] rounded-full transition-shadow',
                  color === c && 'outline outline-2 outline-offset-[1.5px] outline-[var(--nm-ink)]',
                )}
                style={{ background: c }}
              />
            ))}
          </div>
        </div>

        <div className="flex flex-col gap-2">
          <span className={fieldLabel}>
            {t('pages.createTeam.membersLabel')}{' '}
            <span className="normal-case tracking-normal text-[var(--nm-ink30)]">
              · {t('pages.createTeam.selectedCount', { count: selectedCount })}
            </span>
          </span>
          {/* Search — narrows the checklist without dropping selections. */}
          {agents.length > 0 && (
            <div className="flex items-center gap-2 h-8 px-2.5 rounded-[var(--radius-sm)] border border-[var(--border-subtle)] bg-[var(--nm-card)] max-w-[340px]">
              <Search className="w-3 h-3 shrink-0 text-[var(--nm-ink30)]" />
              <input
                value={memberQuery}
                onChange={(e) => setMemberQuery(e.target.value)}
                placeholder={t('pages.createTeam.searchPlaceholder')}
                className="flex-1 bg-transparent text-[12px] text-[var(--nm-ink)] placeholder:text-[var(--nm-ink30)] focus:outline-none"
              />
            </div>
          )}
          {agents.length === 0 ? (
            <div className="text-[12px] text-[var(--nm-ink50)]">
              {t('pages.createTeam.noAgents')}
            </div>
          ) : matchedAgents.length === 0 ? (
            <div className="text-[12px] text-[var(--nm-ink50)]">
              {t('pages.createTeam.noMatch')}
            </div>
          ) : (
            matchedAgents.map((a) => {
              const checked = members.has(a.agent_id);
              return (
                <label
                  key={a.agent_id}
                  className={cn(
                    'flex cursor-pointer items-center gap-2.5 rounded-[var(--radius-sm)] border bg-[var(--nm-card)] px-3 py-2 transition-colors',
                    checked ? 'border-[var(--border-default)]' : 'border-[var(--nm-hairline)] hover:border-[var(--border-subtle)]',
                  )}
                  onClick={() => toggleMember(a.agent_id)}
                >
                  <span
                    className={cn(
                      'inline-flex h-3.5 w-3.5 items-center justify-center rounded-[2px] border text-[10px] leading-none',
                      checked
                        ? 'border-[var(--nm-ink)] bg-[var(--nm-ink)] text-[var(--nm-paper)]'
                        : 'border-[var(--border-default)] bg-[var(--nm-card)] text-transparent',
                    )}
                    role="checkbox"
                    aria-checked={checked}
                    aria-label={a.name || a.agent_id}
                    tabIndex={0}
                    onKeyDown={(e) => {
                      if (e.key === ' ' || e.key === 'Enter') {
                        e.preventDefault();
                        toggleMember(a.agent_id);
                      }
                    }}
                  >
                    ✓
                  </span>
                  <RingAvatar
                    species="silicon"
                    label={(a.name || a.agent_id).slice(0, 2)}
                    size="sm"
                    className="shrink-0"
                  />
                  <span className="min-w-0 flex-1 truncate text-[13px] font-medium text-[var(--nm-ink)]">
                    {a.name || a.agent_id}
                  </span>
                  <span className="shrink-0 font-mono text-[10px] text-[var(--nm-ink30)]">
                    {a.agent_id}
                  </span>
                </label>
              );
            })
          )}
        </div>

      </div>
    </ScrollArea>

    {/* Sticky action bar — always visible regardless of checklist length
        (UI/UX doc 2026-08-06: the create button used to sit below the fold
        with no cancel). */}
    <div className="shrink-0 border-t border-[var(--nm-hairline)] bg-[var(--nm-paper)]">
      <div className="max-w-[600px] mx-auto flex items-center justify-between gap-3 px-6 py-3">
        <span className="text-[12px] text-[var(--nm-ink30)] truncate">
          {t('pages.createTeam.manageHint')}
        </span>
        <div className="flex items-center gap-2 shrink-0">
          <Button variant="ghost" onClick={() => navigate('/app/chat')} disabled={busy}>
            {t('pages.createTeam.cancel')}
          </Button>
          <Button onClick={handleCreate} disabled={!canCreate} className="gap-1.5">
            {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            {t('pages.createTeam.createButton')}
          </Button>
        </div>
      </div>
    </div>
    </div>
  );
}
