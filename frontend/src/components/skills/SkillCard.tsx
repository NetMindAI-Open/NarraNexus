/**
 * @file_name: SkillCard.tsx
 * @author: Bin Liang
 * @date: 2026-03-06
 * @description: Individual skill card with toggle, remove, and study actions
 */

import { useState } from 'react';
import {
  Puzzle,
  ToggleLeft,
  ToggleRight,
  Trash2,
  Loader2,
  AlertCircle,
  BookOpen,
  ChevronDown,
  ChevronRight,
  KeyRound,
  CircleAlert,
} from 'lucide-react';
import { Button, Markdown, ScrollArea } from '@/components/ui';
import { cn } from '@/lib/utils';
import type { SkillInfo } from '@/types/skills';

export function SkillCard({
  skill,
  onToggle,
  onRemove,
  onStudy,
  onConfigure,
  isToggling,
  isRemoving,
  isStudying,
}: {
  skill: SkillInfo;
  onToggle: (skill: SkillInfo) => void;
  onRemove: (skill: SkillInfo) => void;
  onStudy: (skill: SkillInfo) => void;
  onConfigure: (skill: SkillInfo) => void;
  isToggling: boolean;
  isRemoving: boolean;
  isStudying: boolean;
}) {
  const [showResult, setShowResult] = useState(false);
  const studying = isStudying || skill.study_status === 'studying';

  return (
    <div
      className={cn(
        'p-4 rounded-xl transition-colors duration-150',
        'border bg-[var(--bg-elevated)]',
        skill.disabled
          ? 'border-[var(--border-subtle)] opacity-60'
          : 'border-[var(--border-subtle)] hover:border-[var(--border-strong)]'
      )}
    >
      <div className="flex items-start gap-3">
        <div className="w-10 h-10 rounded-lg flex items-center justify-center shrink-0 bg-[var(--accent-secondary)]/10">
          <Puzzle className="w-5 h-5 text-[var(--accent-secondary)]" />
        </div>

        <div className="flex-1 min-w-0">
          <div className="flex items-center justify-between gap-2 mb-1">
            <span
              className={cn(
                'text-sm font-semibold truncate',
                skill.disabled
                  ? 'text-[var(--text-tertiary)] line-through'
                  : 'text-[var(--text-primary)]'
              )}
            >
              {skill.name}
            </span>
          </div>

          {skill.description && (
            <p className="text-xs text-[var(--text-tertiary)] line-clamp-2 mb-2">
              {skill.description}
            </p>
          )}

          {skill.version && (
            <span className="text-[10px] font-mono font-medium text-[var(--text-tertiary)]">
              v{skill.version}
            </span>
          )}

          {/* Env config warning */}
          {skill.requires_env && skill.requires_env.length > 0 && skill.env_configured === false && (
            <div
              className="flex items-center gap-2 mt-2 px-2 py-1.5 rounded-lg bg-[var(--color-warning)]/5 border border-[var(--color-warning)]/10 cursor-pointer hover:bg-[var(--color-warning)]/10 transition-colors"
              onClick={() => onConfigure(skill)}
            >
              <CircleAlert className="w-3 h-3 text-[var(--color-warning)]" />
              <span className="text-xs font-medium text-[var(--color-warning)]">
                Needs config: {skill.requires_env.join(', ')}
              </span>
            </div>
          )}

          {/* Study status display */}
          {studying && (
            <div className="flex items-center gap-2 mt-2 px-2 py-1.5 rounded-lg bg-[var(--accent-primary)]/5 border border-[var(--accent-primary)]/10">
              <Loader2 className="w-3 h-3 animate-spin text-[var(--accent-primary)]" />
              <span className="text-xs font-medium text-[var(--accent-primary)]">Studying...</span>
            </div>
          )}

          {/* Study failed */}
          {skill.study_status === 'failed' && skill.study_error && (
            <div className="flex items-center gap-2 mt-2 px-2 py-1.5 rounded-lg bg-[var(--color-error)]/5 border border-[var(--color-error)]/10">
              <AlertCircle className="w-3 h-3 text-[var(--color-error)]" />
              <span className="text-xs font-medium text-[var(--color-error)] truncate">{skill.study_error}</span>
            </div>
          )}

          {/* Study result */}
          {skill.study_status === 'completed' && skill.study_result && (
            <div className="mt-2">
              <button
                onClick={() => setShowResult(!showResult)}
                className="flex items-center gap-1 text-xs text-[var(--accent-secondary)] hover:text-[var(--accent-primary)] transition-colors"
              >
                {showResult ? (
                  <ChevronDown className="w-3 h-3" />
                ) : (
                  <ChevronRight className="w-3 h-3" />
                )}
                <BookOpen className="w-3 h-3" />
                Study Result
              </button>
              {showResult && (
                <ScrollArea className="mt-1.5 max-h-48 rounded-lg bg-[var(--bg-sunken)] border border-[var(--border-subtle)]" viewportClassName="p-2.5">
                  <div className="text-xs text-[var(--text-secondary)] [&>*:first-child]:mt-0 [&>*:last-child]:mb-0">
                    <Markdown content={skill.study_result!} />
                  </div>
                </ScrollArea>
              )}
            </div>
          )}

          {/* Action buttons */}
          <div className="flex items-center gap-2 mt-3 pt-3 border-t border-[var(--border-subtle)]">
            {/* Study button */}
            <Button
              variant="ghost"
              size="sm"
              onClick={() => onStudy(skill)}
              disabled={studying || skill.disabled}
              className="text-xs text-[var(--accent-primary)] hover:bg-[var(--accent-primary)]/10"
            >
              {studying ? (
                <Loader2 className="w-3 h-3 mr-1.5 animate-spin" />
              ) : (
                <BookOpen className="w-3 h-3 mr-1.5" />
              )}
              {skill.study_status === 'completed' ? 'Re-study' : 'Study'}
            </Button>

            {/* Configure button - shown when skill has env requirements */}
            {skill.requires_env && skill.requires_env.length > 0 && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => onConfigure(skill)}
                disabled={skill.disabled}
                className={cn(
                  'text-xs',
                  skill.env_configured === false
                    ? 'text-[var(--color-warning)] hover:bg-[var(--color-warning)]/10'
                    : 'text-[var(--accent-secondary)] hover:bg-[var(--accent-secondary)]/10'
                )}
              >
                <KeyRound className="w-3 h-3 mr-1.5" />
                {skill.env_configured === false ? 'Configure' : 'Env'}
              </Button>
            )}

            <Button
              variant="ghost"
              size="sm"
              onClick={() => onToggle(skill)}
              disabled={isToggling}
              className={cn(
                'text-xs',
                skill.disabled
                  ? 'text-[var(--color-success)] hover:bg-[var(--color-success)]/10'
                  : 'text-[var(--color-warning)] hover:bg-[var(--color-warning)]/10'
              )}
            >
              {isToggling ? (
                <Loader2 className="w-3 h-3 mr-1.5 animate-spin" />
              ) : skill.disabled ? (
                <ToggleRight className="w-3 h-3 mr-1.5" />
              ) : (
                <ToggleLeft className="w-3 h-3 mr-1.5" />
              )}
              {skill.disabled ? 'Enable' : 'Disable'}
            </Button>

            <Button
              variant="ghost"
              size="sm"
              onClick={() => onRemove(skill)}
              disabled={isRemoving}
              className="text-xs text-[var(--color-error)] hover:bg-[var(--color-error)]/10"
            >
              {isRemoving ? (
                <Loader2 className="w-3 h-3 mr-1.5 animate-spin" />
              ) : (
                <Trash2 className="w-3 h-3 mr-1.5" />
              )}
              Remove
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
