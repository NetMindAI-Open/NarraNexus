/**
 * ComposerFastToggle — fast-mode switch docked in the composer tools row,
 * left of ComposerModelBadge. Pure presentational: state lives in
 * ChatPanel via useFastMode; when on, the WS first payload carries
 * `fast_mode: true` and AgentRuntime maps it to a TurnProfile
 * (lighter narrative retrieval + fast framework + low reasoning effort).
 */
import { useTranslation } from 'react-i18next';
import { Zap } from 'lucide-react';
import { cn } from '@/lib/utils';

interface Props {
  enabled: boolean;
  onToggle: (value: boolean) => void;
  disabled?: boolean;
}

export function ComposerFastToggle({ enabled, onToggle, disabled }: Props) {
  const { t } = useTranslation();
  return (
    <button
      type="button"
      aria-pressed={enabled}
      disabled={disabled}
      onClick={() => onToggle(!enabled)}
      title={t('chat.fastMode.tooltip')}
      className={cn(
        'flex h-7 items-center gap-1 rounded-[var(--radius-md)] border px-2 text-xs transition-colors',
        'disabled:cursor-not-allowed disabled:opacity-50',
        enabled
          ? 'border-[var(--color-carbon)] bg-[var(--color-carbon-soft)] text-[var(--color-carbon)]'
          : 'border-transparent text-[var(--text-tertiary)] hover:text-[var(--color-carbon)]',
      )}
    >
      <Zap className={cn('h-3.5 w-3.5', enabled && 'fill-current')} />
      {t('chat.fastMode.label')}
    </button>
  );
}
