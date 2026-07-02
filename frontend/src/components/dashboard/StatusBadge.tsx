/**
 * @file_name: StatusBadge.tsx
 * @description: Kind → icon + label map for agent status. Covers all 7
 * WorkingSource values plus 'idle'. Each variant gets a test-id for
 * automated assertions.
 */
import { useTranslation } from 'react-i18next';
import type { AgentKind } from '@/types';
import {
  Moon,
  MessageCircle,
  Briefcase,
  Radio,
  ArrowLeftRight,
  PhoneCall,
  GraduationCap,
  FlaskConical,
  Grid3x3,
} from 'lucide-react';

const ICON_MAP: Record<
  AgentKind,
  { Icon: typeof Moon; labelKey: string; cls: string }
> = {
  idle:        { Icon: Moon,           labelKey: 'idle',     cls: 'text-[var(--text-secondary)]' },
  CHAT:        { Icon: MessageCircle,  labelKey: 'chat',     cls: 'text-[var(--color-green-500)]' },
  JOB:         { Icon: Briefcase,      labelKey: 'job',      cls: 'text-[var(--color-yellow-500)]' },
  MESSAGE_BUS: { Icon: Radio,          labelKey: 'bus',      cls: 'text-sky-500' },
  A2A:         { Icon: ArrowLeftRight, labelKey: 'a2a',      cls: 'text-violet-500' },
  CALLBACK:    { Icon: PhoneCall,      labelKey: 'callback', cls: 'text-rose-500' },
  SKILL_STUDY: { Icon: GraduationCap,  labelKey: 'skill',    cls: 'text-[var(--color-blue-500)]' },
  LARK:        { Icon: FlaskConical,   labelKey: 'lark',     cls: 'text-fuchsia-500' },
  MATRIX:      { Icon: Grid3x3,        labelKey: 'matrix',   cls: 'text-[var(--color-info)]' },
};

export function StatusBadge({ kind }: { kind: AgentKind }) {
  const { t } = useTranslation();
  const { Icon, labelKey, cls } = ICON_MAP[kind];
  return (
    <span
      data-testid={`status-badge-${kind}`}
      className={`inline-flex items-center gap-1 text-xs font-medium ${cls}`}
    >
      <Icon className="w-3 h-3" aria-hidden />
      {t(`dashboard.statusBadge.${labelKey}`)}
    </span>
  );
}
