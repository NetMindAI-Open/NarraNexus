/**
 * @file_name: StatusBadge.tsx
 * @description: Kind → icon + label map for agent status. Covers all 7
 * WorkingSource values plus 'idle'. Each variant gets a test-id for
 * automated assertions.
 */
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
} from 'lucide-react';

const ICON_MAP: Record<
  AgentKind,
  { Icon: typeof Moon; label: string; cls: string }
> = {
  idle:        { Icon: Moon,           label: 'Idle',         cls: 'text-[var(--text-secondary)]' },
  CHAT:        { Icon: MessageCircle,  label: 'Chat',         cls: 'text-emerald-500' },
  JOB:         { Icon: Briefcase,      label: 'Job',          cls: 'text-amber-500' },
  MESSAGE_BUS: { Icon: Radio,          label: 'Bus',          cls: 'text-sky-500' },
  A2A:         { Icon: ArrowLeftRight, label: 'A2A',          cls: 'text-violet-500' },
  CALLBACK:    { Icon: PhoneCall,      label: 'Callback',     cls: 'text-rose-500' },
  SKILL_STUDY: { Icon: GraduationCap,  label: 'Skill',        cls: 'text-blue-500' },
  LARK:        { Icon: FlaskConical,   label: 'Lark',         cls: 'text-fuchsia-500' },
};

export function StatusBadge({ kind }: { kind: AgentKind }) {
  const { Icon, label, cls } = ICON_MAP[kind];
  return (
    <span
      data-testid={`status-badge-${kind}`}
      className={`inline-flex items-center gap-1 text-xs font-medium ${cls}`}
    >
      <Icon className="w-3 h-3" aria-hidden />
      {label}
    </span>
  );
}
