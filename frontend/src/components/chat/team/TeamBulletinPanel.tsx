/**
 * @file_name: TeamBulletinPanel.tsx
 * @author: NarraNexus
 * @date: 2026-08-10
 * @description: The team's bulletin — the rules every member loads every turn.
 *
 * Before this, the only way to give a team a standing instruction was to say it
 * in the chat, where it falls out of view after about twenty messages and is
 * invisible to anyone who joins later. The user's recourse was to repeat
 * themselves, which itself consumed the window that made repeating necessary.
 *
 * Presentational and controlled: the parent owns the data and the reload, for
 * the same reason TeamWorkspacePanel is built that way — a bulletin change
 * posts a system line into the transcript, so the transcript and this panel
 * have to agree about when something changed, and one component must own that.
 *
 * The budget is shown, not discovered. The server refuses an over-long rule
 * rather than trimming it, so the panel's job is to make the ceiling visible
 * BEFORE the user writes something that will be rejected — a counter next to
 * the input costs nothing and turns a refusal into a non-event.
 *
 * Attribution is the safety valve for agent write access. Agents can pin rules;
 * the user can delete any of them. That is only a real check if the user can
 * see at a glance which rules they did not write, so agent-written entries carry
 * a visible label and everything unlabelled is theirs.
 */

import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Check, Loader2, Pencil, Plus, Trash2, X } from 'lucide-react';

import { Button, Textarea } from '@/components/ui';
import { cn } from '@/lib/utils';
import type { BulletinEntry, TeamBulletin } from '@/types/teams';

interface TeamBulletinPanelProps {
  bulletin: TeamBulletin | null;
  loading: boolean;
  error: string | null;
  /** agent_id → display name, for attributing agent-written rules. */
  memberNames: Record<string, string>;
  onAdd: (content: string, tier: 'long_term' | 'current_task') => Promise<string | null>;
  onEdit: (entryId: string, content: string) => Promise<string | null>;
  onDelete: (entryId: string) => Promise<string | null>;
  onClearTier: (tier: 'long_term' | 'current_task') => Promise<string | null>;
}

export function TeamBulletinPanel({
  bulletin,
  loading,
  error,
  memberNames,
  onAdd,
  onEdit,
  onDelete,
  onClearTier,
}: TeamBulletinPanelProps) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState('');
  const [draftTier, setDraftTier] = useState<'long_term' | 'current_task'>('long_term');
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editText, setEditText] = useState('');
  const [busy, setBusy] = useState(false);
  // Server-side refusals (over budget) surface here rather than in a toast: the
  // message names the limit, and it belongs next to the text that broke it.
  const [refusal, setRefusal] = useState<string | null>(null);

  const entries = bulletin?.entries ?? [];
  const rules = entries.filter((e) => e.source !== 'auto_summary');
  const summary = entries.find((e) => e.source === 'auto_summary') ?? null;
  const longTerm = rules.filter((e) => e.tier !== 'current_task');
  const currentTask = rules.filter((e) => e.tier === 'current_task');

  const usage = bulletin?.usage ?? { entry_count: 0, total_chars: 0 };
  const limits = bulletin?.limits ?? {
    max_entries: 20,
    max_entry_chars: 500,
    max_total_chars: 2000,
  };
  const full =
    usage.entry_count >= limits.max_entries || usage.total_chars >= limits.max_total_chars;

  const run = async (fn: () => Promise<string | null>) => {
    setBusy(true);
    setRefusal(null);
    try {
      const err = await fn();
      if (err) setRefusal(err);
      return err;
    } finally {
      setBusy(false);
    }
  };

  const submitDraft = async () => {
    const text = draft.trim();
    if (!text) return;
    const err = await run(() => onAdd(text, draftTier));
    if (!err) setDraft('');
  };

  const label = (entry: BulletinEntry): string | null => {
    if (entry.source !== 'agent' || !entry.author_id) return null;
    return memberNames[entry.author_id] || entry.author_id;
  };

  const renderEntry = (entry: BulletinEntry) => {
    const who = label(entry);
    const editing = editingId === entry.entry_id;
    return (
      <li
        key={entry.entry_id}
        data-testid={`bulletin-entry-${entry.entry_id}`}
        className="group flex items-start gap-2 rounded-md px-2 py-1.5 hover:bg-[var(--bg-hover)]"
      >
        {editing ? (
          <>
            <Textarea
              value={editText}
              onChange={(e) => setEditText(e.target.value)}
              rows={2}
              className="flex-1 text-sm"
              aria-label={t('chat.team.bulletin.editLabel')}
            />
            <button
              type="button"
              disabled={busy}
              aria-label={t('common.save')}
              onClick={async () => {
                const err = await run(() => onEdit(entry.entry_id, editText));
                if (!err) setEditingId(null);
              }}
              className="p-1 text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
            >
              <Check className="w-3.5 h-3.5" />
            </button>
            <button
              type="button"
              aria-label={t('common.cancel')}
              onClick={() => {
                setEditingId(null);
                setRefusal(null);
              }}
              className="p-1 text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </>
        ) : (
          <>
            <span className="flex-1 text-sm text-[var(--text-primary)] whitespace-pre-wrap break-words">
              {entry.content}
              {who && (
                <span
                  data-testid={`bulletin-author-${entry.entry_id}`}
                  className="ml-2 text-[10px] font-mono text-[var(--text-tertiary)]"
                >
                  {t('chat.team.bulletin.addedBy', { name: who })}
                </span>
              )}
            </span>
            {/* Always mounted, not hover-only: a keyboard user cannot hover, and
                delete is the control that makes agent write access safe. */}
            <button
              type="button"
              aria-label={t('chat.team.bulletin.editEntry')}
              onClick={() => {
                setEditingId(entry.entry_id);
                setEditText(entry.content);
                setRefusal(null);
              }}
              className="p-1 text-[var(--text-tertiary)] hover:text-[var(--text-primary)]"
            >
              <Pencil className="w-3.5 h-3.5" />
            </button>
            <button
              type="button"
              disabled={busy}
              aria-label={t('chat.team.bulletin.deleteEntry')}
              data-testid={`bulletin-delete-${entry.entry_id}`}
              onClick={() => run(() => onDelete(entry.entry_id))}
              className="p-1 text-[var(--text-tertiary)] hover:text-[var(--danger)]"
            >
              <Trash2 className="w-3.5 h-3.5" />
            </button>
          </>
        )}
      </li>
    );
  };

  if (loading && !bulletin) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <Loader2 className="w-4 h-4 animate-spin text-[var(--text-tertiary)]" />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3 p-3 overflow-y-auto" data-testid="team-bulletin-panel">
      <p className="text-xs text-[var(--text-tertiary)]">
        {t('chat.team.bulletin.intro')}
      </p>

      {error && <p className="text-xs text-[var(--danger)]">{error}</p>}

      {rules.length === 0 && !error && (
        <p className="text-xs text-[var(--text-tertiary)]" data-testid="bulletin-empty">
          {t('chat.team.bulletin.empty')}
        </p>
      )}

      {longTerm.length > 0 && (
        <section>
          <h4 className="text-[10px] font-mono uppercase text-[var(--text-tertiary)] mb-1">
            {t('chat.team.bulletin.longTerm')}
          </h4>
          <ul>{longTerm.map(renderEntry)}</ul>
        </section>
      )}

      {currentTask.length > 0 && (
        <section>
          <div className="flex items-center justify-between mb-1">
            <h4 className="text-[10px] font-mono uppercase text-[var(--text-tertiary)]">
              {t('chat.team.bulletin.currentTask')}
            </h4>
            {/* Scoped to this tier on purpose: the standing rules are what the
                user least wants to retype, and retyping is the thing the whole
                feature exists to prevent. */}
            <button
              type="button"
              disabled={busy}
              data-testid="bulletin-clear-current-task"
              onClick={() => run(() => onClearTier('current_task'))}
              className="text-[10px] text-[var(--text-tertiary)] hover:text-[var(--danger)]"
            >
              {t('chat.team.bulletin.clearCurrentTask')}
            </button>
          </div>
          <ul>{currentTask.map(renderEntry)}</ul>
        </section>
      )}

      {summary && (
        <section data-testid="bulletin-summary">
          <h4 className="text-[10px] font-mono uppercase text-[var(--text-tertiary)] mb-1">
            {t('chat.team.bulletin.progress')}
          </h4>
          {/* Labelled as generated, and visually quieter than the rules. It is
              best-effort machine output sitting next to instructions a human
              typed; presented identically it would read as equally authoritative. */}
          <p className="text-xs text-[var(--text-secondary)] whitespace-pre-wrap px-2">
            {summary.content}
          </p>
          <p className="text-[10px] text-[var(--text-tertiary)] px-2 mt-1">
            {t('chat.team.bulletin.progressHint')}
          </p>
        </section>
      )}

      <div className="border-t border-[var(--border-subtle)] pt-2">
        <Textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          rows={2}
          maxLength={limits.max_entry_chars}
          disabled={full}
          placeholder={
            full
              ? t('chat.team.bulletin.fullPlaceholder')
              : t('chat.team.bulletin.placeholder')
          }
          aria-label={t('chat.team.bulletin.newEntry')}
          data-testid="bulletin-input"
          className="text-sm"
        />
        <div className="flex items-center justify-between mt-1.5">
          <div className="flex items-center gap-2">
            <select
              value={draftTier}
              onChange={(e) => setDraftTier(e.target.value as 'long_term' | 'current_task')}
              aria-label={t('chat.team.bulletin.tierLabel')}
              data-testid="bulletin-tier"
              className="text-xs bg-transparent text-[var(--text-secondary)] border border-[var(--border-subtle)] rounded px-1.5 py-0.5"
            >
              <option value="long_term">{t('chat.team.bulletin.longTerm')}</option>
              <option value="current_task">{t('chat.team.bulletin.currentTask')}</option>
            </select>
            {/* The ceiling, before the user hits it. */}
            <span
              data-testid="bulletin-usage"
              className={cn(
                'text-[10px] font-mono',
                full ? 'text-[var(--danger)]' : 'text-[var(--text-tertiary)]',
              )}
            >
              {t('chat.team.bulletin.usage', {
                entries: usage.entry_count,
                maxEntries: limits.max_entries,
                chars: usage.total_chars,
                maxChars: limits.max_total_chars,
              })}
            </span>
          </div>
          <Button
            size="sm"
            disabled={busy || full || !draft.trim()}
            onClick={submitDraft}
            data-testid="bulletin-add"
          >
            <Plus className="w-3.5 h-3.5 mr-1" />
            {t('chat.team.bulletin.add')}
          </Button>
        </div>
        {refusal && (
          <p className="text-xs text-[var(--danger)] mt-1.5" data-testid="bulletin-refusal">
            {refusal}
          </p>
        )}
      </div>
    </div>
  );
}

export default TeamBulletinPanel;
