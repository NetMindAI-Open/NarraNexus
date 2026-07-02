/**
 * @file_name: SkillsPanel.tsx
 * @author: Bin Liang
 * @date: 2026-02-03
 * @description: Skills management panel (TanStack Query powered)
 *
 * Features:
 * - Display user's installed Skills
 * - Support installing Skills from GitHub or zip files
 * - Support disabling/enabling/removing Skills
 * - Support Study feature: Agent automatically learns Skill documentation
 */

import { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Puzzle,
  RefreshCw,
  Github,
  FileArchive,
  Loader2,
  AlertCircle,
  CheckCircle,
  X,
  KeyRound,
  CircleAlert,
} from 'lucide-react';
import { Card, CardHeader, CardTitle, CardContent, Button, ScrollArea, useConfirm } from '@/components/ui';
import { cn } from '@/lib/utils';
import { api } from '@/lib/api';
import { useConfigStore } from '@/stores/configStore';
import {
  useSkillsList,
  useInstallFromGithub,
  useInstallFromZip,
  useToggleSkill,
  useRemoveSkill,
  useStudySkill,
  useStudyStatus,
} from '@/hooks/useSkills';
import type { SkillInfo } from '@/types/skills';
import { SkillCard } from './SkillCard';
import { InstallDialog } from './InstallDialog';
import type { InstallMode } from './InstallDialog';
import { MCPManager } from './MCPManager';

// Environment configuration dialog
function EnvConfigDialog({
  skill,
  agentId,
  userId,
  onClose,
  onSaved,
}: {
  skill: SkillInfo;
  agentId: string;
  userId: string;
  onClose: () => void;
  onSaved: () => void;
}) {
  const { t } = useTranslation();
  const [envValues, setEnvValues] = useState<Record<string, string>>({});
  const [envStatus, setEnvStatus] = useState<Record<string, boolean>>({});
  const [requiresEnv, setRequiresEnv] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchConfig = async () => {
      try {
        const res = await api.getSkillEnvConfig(skill.name, agentId);
        setRequiresEnv(res.requires_env);
        setEnvStatus(res.env_configured);
      } catch (err) {
        setError(err instanceof Error ? err.message : t('skills.env.errorLoad'));
      } finally {
        setLoading(false);
      }
    };
    fetchConfig();
  }, [skill.name, agentId, userId, t]);

  const handleSave = async () => {
    // Only send non-empty values
    const toSave: Record<string, string> = {};
    for (const [key, value] of Object.entries(envValues)) {
      if (value.trim()) {
        toSave[key] = value.trim();
      }
    }
    if (Object.keys(toSave).length === 0) {
      onClose();
      return;
    }

    setSaving(true);
    setError(null);
    try {
      const res = await api.setSkillEnvConfig(skill.name, agentId, toSave);
      if (res.success) {
        onSaved();
        onClose();
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : t('skills.env.errorSave'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      className="fixed inset-0 flex items-center justify-center z-50 animate-fade-in"
      style={{ background: 'var(--nm-backdrop)' }}
    >
      <div className="bg-[var(--nm-card)] border border-[var(--nm-hairline)] rounded-[var(--radius-lg)] p-6 w-full max-w-md shadow-[var(--nm-elev-3)]">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-lg font-semibold text-[var(--text-primary)] flex items-center gap-2">
            <KeyRound className="w-5 h-5" />
            {t('skills.env.title', { name: skill.name })}
          </h3>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg hover:bg-[var(--bg-tertiary)] transition-colors"
          >
            <X className="w-4 h-4 text-[var(--text-tertiary)]" />
          </button>
        </div>

        {loading ? (
          <div className="flex items-center justify-center py-8">
            <Loader2 className="w-6 h-6 animate-spin text-[var(--accent-primary)]" />
          </div>
        ) : requiresEnv.length === 0 ? (
          <p className="text-sm text-[var(--text-tertiary)] py-4">
            {t('skills.env.noneRequired')}
          </p>
        ) : (
          <div className="space-y-3">
            <p className="text-xs text-[var(--text-tertiary)]">
              {t('skills.env.instructions')}
            </p>
            {requiresEnv.map((varName) => (
              <div key={varName}>
                <label className="flex items-center gap-2 text-xs font-medium text-[var(--text-secondary)] mb-1.5">
                  {envStatus[varName] ? (
                    <CheckCircle className="w-3.5 h-3.5 text-[var(--color-success)]" />
                  ) : (
                    <CircleAlert className="w-3.5 h-3.5 text-[var(--color-warning)]" />
                  )}
                  {varName}
                </label>
                <input
                  type="password"
                  value={envValues[varName] || ''}
                  onChange={(e) =>
                    setEnvValues((prev) => ({ ...prev, [varName]: e.target.value }))
                  }
                  placeholder={envStatus[varName] ? t('skills.env.placeholderConfigured') : t('skills.env.placeholderEnter')}
                  className="w-full px-4 py-2.5 rounded-xl bg-[var(--bg-sunken)] border border-[var(--border-subtle)] text-sm text-[var(--text-primary)] placeholder:text-[var(--text-tertiary)] focus:outline-none focus:border-[var(--accent-primary)] transition-colors font-mono"
                />
              </div>
            ))}
          </div>
        )}

        {error && (
          <div className="flex items-center gap-2 px-3 py-2 mt-3 rounded-lg bg-[var(--color-error)]/10 border border-[var(--color-error)]/20">
            <AlertCircle className="w-4 h-4 text-[var(--color-error)]" />
            <span className="text-xs text-[var(--color-error)]">{error}</span>
          </div>
        )}

        <div className="flex gap-3 pt-4 mt-4 border-t border-[var(--border-subtle)]">
          <Button variant="ghost" onClick={onClose} disabled={saving} className="flex-1">
            {t('skills.env.cancel')}
          </Button>
          <Button
            variant="default"
            onClick={handleSave}
            disabled={saving || requiresEnv.length === 0}
            className="flex-1"
          >
            {saving ? (
              <>
                <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                {t('skills.env.saving')}
              </>
            ) : (
              t('skills.env.save')
            )}
          </Button>
        </div>
      </div>
    </div>
  );
}

export type SkillsSectionId = 'skills' | 'mcp';

interface SkillsPanelProps {
  /** Skip the outer Card chrome + duplicate title when hosted inside the
   *  bookmark drawer. Functional actions are kept. */
  embedded?: boolean;
  /** Atomic mode: render exactly ONE section (bookmark-strip IA). */
  section?: SkillsSectionId;
}

export function SkillsPanel({ embedded = false, section }: SkillsPanelProps = {}) {
  const { t } = useTranslation();
  const { agentId, userId } = useConfigStore();
  const [installMode, setInstallMode] = useState<InstallMode>(null);
  const [configuringSkill, setConfiguringSkill] = useState<SkillInfo | null>(null);
  const [showDisabled, setShowDisabled] = useState(false);
  const [studyingSkillName, setStudyingSkillName] = useState<string | null>(null);
  const { confirm, dialog: confirmDialog } = useConfirm();

  // ── Queries ──────────────────────────────────────────────────────────────
  const { data: skills = [], isLoading, error, refetch } = useSkillsList(showDisabled);

  // Auto-detect skills being studied (resume polling after page load)
  const activeStudying = studyingSkillName
    ?? skills.find((s: { study_status?: string }) => s.study_status === 'studying')?.name
    ?? null;
  const { data: studyStatus } = useStudyStatus(activeStudying);

  // Once the polled study job reaches a terminal state, clear the local
  // "studying" flag so the spinner in SkillCard stops. Without this, a
  // study triggered via `handleStudy` never clears `studyingSkillName` on
  // success (only `onError` did), so the card kept spinning forever until
  // a manual page reload reset the component state.
  //
  // Adjusted during render rather than in a useEffect (React's "adjusting
  // state when a prop changes" pattern) — this avoids an extra
  // commit-then-effect render pass, and keeps `seenStudyStatus` as the
  // guard so the reset only fires once per status transition.
  const [seenStudyStatus, setSeenStudyStatus] = useState<string | undefined>(undefined);
  if (studyStatus?.study_status !== seenStudyStatus) {
    setSeenStudyStatus(studyStatus?.study_status);
    if (studyStatus?.study_status === 'completed' || studyStatus?.study_status === 'failed') {
      setStudyingSkillName(null);
    }
  }

  // ── Mutations ────────────────────────────────────────────────────────────
  const installGithub = useInstallFromGithub();
  const installZip = useInstallFromZip();
  const toggleSkill = useToggleSkill();
  const removeSkill = useRemoveSkill();
  const studySkill = useStudySkill();

  // ── Handlers ─────────────────────────────────────────────────────────────
  const handleInstall = (data: { url?: string; branch?: string; file?: File }) => {
    if (installMode === 'github' && data.url) {
      installGithub.mutate(
        { url: data.url, branch: data.branch || 'main' },
        { onSuccess: () => setInstallMode(null) }
      );
    } else if (installMode === 'zip' && data.file) {
      installZip.mutate(data.file, { onSuccess: () => setInstallMode(null) });
    }
  };

  const handleToggle = (skill: SkillInfo) => {
    toggleSkill.mutate({ name: skill.name, disabled: skill.disabled });
  };

  const handleRemove = async (skill: SkillInfo) => {
    const ok = await confirm({
      title: t('skills.remove.title'),
      message: t('skills.remove.message', { name: skill.name }),
      confirmText: t('skills.remove.confirm'),
      danger: true,
    });
    if (!ok) return;
    removeSkill.mutate(skill.name);
  };

  const handleStudy = (skill: SkillInfo) => {
    setStudyingSkillName(skill.name);
    studySkill.mutate(skill.name, {
      onError: () => setStudyingSkillName(null),
    });
  };

  const isInstalling = installGithub.isPending || installZip.isPending;

  const inner = (
    <>
      {confirmDialog}
      <CardHeader className={cn(embedded && 'justify-end py-1')}>
        {!embedded && (
        <CardTitle>
          <Puzzle />
          {t('skills.title')}
          <span className="ml-1 text-[var(--text-tertiary)] tabular-nums normal-case tracking-normal">
            · {skills.length}
          </span>
        </CardTitle>
        )}
        <Button
          variant="ghost"
          size="icon"
          onClick={() => refetch()}
          disabled={isLoading}
          title={t('skills.refresh')}
        >
          <RefreshCw className={cn('w-4 h-4', isLoading && 'animate-spin')} />
        </Button>
      </CardHeader>

      {/* Action bar */}
      <div className="px-5 py-2.5 flex items-center justify-between gap-2 border-b border-[var(--rule)]">
        <div className="flex gap-1">
          <Button variant="ghost" size="sm" onClick={() => setInstallMode('github')}>
            <Github className="w-3 h-3 mr-1.5" />
            {t('skills.actionBar.github')}
          </Button>
          <Button variant="ghost" size="sm" onClick={() => setInstallMode('zip')}>
            <FileArchive className="w-3 h-3 mr-1.5" />
            {t('skills.actionBar.zip')}
          </Button>
        </div>

        <label className="flex items-center gap-1.5 text-[10px] font-[family-name:var(--font-mono)] uppercase tracking-[0.12em] text-[var(--text-tertiary)] cursor-pointer select-none">
          <input
            type="checkbox"
            checked={showDisabled}
            onChange={(e) => setShowDisabled(e.target.checked)}
          />
          {t('skills.actionBar.showDisabled')}
        </label>
      </div>

      <CardContent className="flex-1 overflow-hidden min-h-0 !p-0">
        <ScrollArea className="h-full">
          {/* ── Section: Skills ── */}
          {(!section || section === 'skills') && (
          <section className="px-5 py-4">
            {error ? (
              <div className="flex items-center justify-center px-8 py-10">
                <div className="text-center">
                  <AlertCircle className="w-8 h-8 text-[var(--color-red-500)] mx-auto mb-4 opacity-60" />
                  <p className="text-[var(--color-red-500)] text-sm mb-1.5">{t('skills.error.title')}</p>
                  <p className="text-[var(--text-tertiary)] text-xs max-w-[260px]">
                    {error instanceof Error ? error.message : t('skills.error.loadFailed')}
                  </p>
                  <Button variant="ghost" size="sm" onClick={() => refetch()} className="mt-4">
                    <RefreshCw className="w-3 h-3 mr-1.5" />
                    {t('skills.error.retry')}
                  </Button>
                </div>
              </div>
            ) : isLoading ? (
              <div className="flex items-center justify-center py-10">
                <Loader2 className="w-5 h-5 text-[var(--text-tertiary)] animate-spin" />
              </div>
            ) : skills.length === 0 ? (
              <div className="flex items-center justify-center px-8 py-10">
                <div className="text-center">
                  <Puzzle className="w-8 h-8 text-[var(--text-tertiary)] opacity-40 mx-auto mb-4" />
                  <p className="text-[var(--text-primary)] text-sm mb-1.5">
                    {t('skills.empty.title')}
                  </p>
                  <p className="text-[var(--text-tertiary)] text-xs max-w-[260px]">
                    {t('skills.empty.hint')}
                  </p>
                </div>
              </div>
            ) : (
              <div className="space-y-2">
                {skills.map((skill: SkillInfo) => (
                  <SkillCard
                    key={skill.name}
                    skill={skill}
                    onToggle={handleToggle}
                    onRemove={handleRemove}
                    onStudy={handleStudy}
                    onConfigure={setConfiguringSkill}
                    isToggling={toggleSkill.isPending && toggleSkill.variables?.name === skill.name}
                    isRemoving={removeSkill.isPending && removeSkill.variables === skill.name}
                    isStudying={activeStudying === skill.name}
                  />
                ))}
              </div>
            )}
          </section>

          )}

          {/* ── Section: MCP Servers ── */}
          {(!section || section === 'mcp') && (
          <section className={cn('px-5 py-5', !section && 'border-t border-[var(--rule)]')}>
            <MCPManager />
          </section>
          )}
        </ScrollArea>
      </CardContent>

      {/* Install dialog */}
      <InstallDialog
        mode={installMode}
        onClose={() => setInstallMode(null)}
        onInstall={handleInstall}
        isInstalling={isInstalling}
      />

      {/* Env config dialog */}
      {configuringSkill && agentId && userId && (
        <EnvConfigDialog
          skill={configuringSkill}
          agentId={agentId}
          userId={userId}
          onClose={() => setConfiguringSkill(null)}
          onSaved={() => refetch()}
        />
      )}
    </>
  );

  if (embedded) {
    return <div className="flex flex-col h-full">{inner}</div>;
  }

  return (
    <Card variant="glass" className="flex flex-col h-full">
      {inner}
    </Card>
  );
}

export default SkillsPanel;
