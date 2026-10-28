/**
 * @file_name: StepAgent.tsx
 * @author: NetMind.AI
 * @date: 2026-08-27
 * @description: Welcome step 3 (always last) — introduce the guide agent the
 * login path provisioned server-side, and hand the user into its conversation.
 *
 * Why this step exists: the guide agent used to arrive unannounced. A new user
 * landed in a chat with an agent they never chose, plus a coach-mark pointing at
 * "+". This screen says out loud what it is (name, persona, its opening line),
 * so the closing CTA — "chat with <name>" — lands somewhere the user recognises.
 *
 * Provisioning is fire-and-forget at login, so the agent may not exist yet when
 * the flow reaches this step. Being LAST makes that rare (two screens have
 * passed); the remaining gap is covered by polling with a skeleton, and a hard
 * bail-out: after GUIDE_WAIT_MS the CTA turns into "go to the app" rather than
 * holding the user hostage on the last screen (binding rule #14's spirit — no
 * forced waiting for the user either).
 */

import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ArrowRight, Bot, CalendarDays, Brain, Loader2, Pencil } from 'lucide-react';
import { Button, Input } from '@/components/ui';
import { api } from '@/lib/api';
import { pickGuideAgent } from '@/lib/guideAgent';
import { WelcomeStepFrame } from './WelcomeStepFrame';
import type { AgentInfo } from '@/types';

/** How long to wait for a fire-and-forget provision before letting the user
 *  through without it. Long enough for a slow disk, short enough that nobody
 *  stares at a skeleton wondering if it broke. */
const GUIDE_WAIT_MS = 3000;
const POLL_MS = 700;

export interface StepAgentProps {
  /** Agents the page already fetched; may be empty on a brand-new account. */
  initialAgents: AgentInfo[];
  onDone: (agent: AgentInfo | null) => void;
  onSkip: () => void;
  onBack?: () => void;
}

export function StepAgent({ initialAgents, onDone, onSkip, onBack }: StepAgentProps) {
  const { t } = useTranslation();
  const [agent, setAgent] = useState<AgentInfo | null>(() => pickGuideAgent(initialAgents));
  const [gaveUp, setGaveUp] = useState(false);
  /** Stamped inside the effect, not during render — Date.now() in a render body
   *  is impure (react-hooks/purity) and would drift on every re-render. */
  const startedAt = useRef<number | null>(null);

  // Poll until the guide agent shows up, or until the wait budget runs out.
  useEffect(() => {
    if (agent) return;
    let alive = true;
    startedAt.current ??= Date.now();
    const tick = async () => {
      if (!alive) return;
      try {
        const res = await api.getAgents();
        const found = pickGuideAgent(res.agents ?? []);
        if (alive && found) {
          setAgent(found);
          return;
        }
      } catch {
        /* best-effort; the bail-out below covers a backend that never answers */
      }
      if (!alive) return;
      if (Date.now() - (startedAt.current ?? Date.now()) >= GUIDE_WAIT_MS) {
        setGaveUp(true);
        return;
      }
      window.setTimeout(() => void tick(), POLL_MS);
    };
    const id = window.setTimeout(() => void tick(), POLL_MS);
    return () => {
      alive = false;
      window.clearTimeout(id);
    };
  }, [agent]);

  const name = agent?.name?.trim() || t('pages.welcome.agent.fallbackName');

  if (!agent && !gaveUp) {
    return (
      <WelcomeStepFrame
        title={t('pages.welcome.agent.preparingTitle')}
        subtitle={t('pages.welcome.agent.preparingSubtitle')}
        onBack={onBack}
        primaryLabel={t('pages.welcome.agent.cta')}
        onPrimary={() => {}}
        primaryDisabled
        skipLabel={t('pages.welcome.agent.skip')}
        onSkip={onSkip}
      >
        <AgentCardSkeleton />
      </WelcomeStepFrame>
    );
  }

  return (
    <WelcomeStepFrame
      title={t('pages.welcome.agent.title', { name })}
      subtitle={t('pages.welcome.agent.subtitle')}
      onBack={onBack}
      // Product-level wording (Owner 2026-08-27): it reads right whether or not
      // the guide agent has landed, so there is no second label for the
      // not-ready case. The agent's name still carries the heading and card.
      primaryLabel={t('pages.welcome.agent.cta')}
      primaryIcon={<ArrowRight className="ml-0.5 h-4 w-4 order-2" />}
      onPrimary={() => onDone(agent)}
      skipLabel={t('pages.welcome.agent.skip')}
      onSkip={onSkip}
    >
      {agent ? (
        <>
          <div className="rounded-[var(--radius-md)] border border-[var(--nm-hairline)] bg-[var(--nm-paper-warm)] p-3.5">
            <div className="flex items-center gap-3">
              <span className="grid h-10 w-10 shrink-0 place-items-center rounded-full border border-[color:var(--color-silicon-hair)] bg-[var(--color-silicon-soft)] text-[var(--color-silicon)]">
                <Bot className="h-5 w-5" />
              </span>
              <div className="min-w-0">
                <div className="truncate text-[15px] font-semibold tracking-tight text-[var(--nm-ink)]">
                  {name}
                </div>
                <div className="mt-0.5 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.10em] text-[var(--nm-ink50)]">
                  {t('pages.welcome.agent.role')}
                </div>
              </div>
            </div>
            {agent.bootstrap_greeting && (
              <div className="mt-3 border-t border-[var(--nm-hairline)] pt-3">
                <p className="border-l-2 border-[var(--color-silicon)] pl-2.5 text-xs leading-relaxed text-[var(--nm-ink70)]">
                  {firstParagraph(agent.bootstrap_greeting)}
                </p>
              </div>
            )}
          </div>

          <RenameDisclosure agent={agent} onRenamed={(next) => setAgent(next)} />

          <div className="mt-3 grid grid-cols-1 gap-1.5 sm:grid-cols-2">
            <Capability icon={<Brain className="h-3.5 w-3.5" />} label={t('pages.welcome.agent.capMemory')} />
            <Capability
              icon={<CalendarDays className="h-3.5 w-3.5" />}
              label={t('pages.welcome.agent.capCheckin')}
            />
          </div>
        </>
      ) : (
        <p className="text-[13px] leading-relaxed text-[var(--nm-ink70)]">
          {t('pages.welcome.agent.notReady')}
        </p>
      )}
    </WelcomeStepFrame>
  );
}

/** "This one's name doesn't fit me" — the draft's rename affordance. Collapsed,
 *  because the generated name is usually fine; inline, because sending a user to
 *  Settings mid-onboarding to fix a name is worse than the name.
 *
 *  Rename only. Changing the persona means editing Awareness, which is a real
 *  editor (AwarenessPanel) and not something to smuggle into a first-run step. */
function RenameDisclosure({
  agent,
  onRenamed,
}: {
  agent: AgentInfo;
  onRenamed: (next: AgentInfo) => void;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState(agent.name ?? '');
  const [saving, setSaving] = useState(false);

  const save = async () => {
    const next = value.trim();
    if (!next || next === agent.name) {
      setOpen(false);
      return;
    }
    setSaving(true);
    try {
      await api.updateAgent(agent.agent_id, next);
      onRenamed({ ...agent, name: next });
      setOpen(false);
    } catch {
      /* keep the field open with what they typed; the name is not load-bearing */
    } finally {
      setSaving(false);
    }
  };

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="mt-3 inline-flex items-center gap-1.5 text-xs text-[var(--nm-ink50)] hover:text-[var(--nm-ink)]"
      >
        <Pencil className="h-3.5 w-3.5" />
        {t('pages.welcome.agent.rename')}
      </button>
    );
  }

  return (
    <div className="mt-3 flex items-center gap-2">
      <Input
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') void save();
          if (e.key === 'Escape') setOpen(false);
        }}
        aria-label={t('pages.welcome.agent.rename')}
        className="flex-1 text-xs"
        autoFocus
      />
      <Button variant="outline" size="sm" onClick={() => void save()} disabled={saving}>
        {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : t('common.ok')}
      </Button>
    </div>
  );
}

/** The provisioned greeting is bilingual (EN then --- then 中文, rendered at
 *  provision time when the user's locale is unknown). One paragraph is all this
 *  card wants — the full text is the first message in the conversation. */
function firstParagraph(greeting: string): string {
  const [head] = greeting.split(/\n\s*---\s*\n/);
  return (head ?? greeting).trim().split(/\n{2,}/)[0]?.trim() ?? '';
}

function Capability({ icon, label }: { icon: React.ReactNode; label: string }) {
  return (
    <div className="flex items-center gap-2 rounded-[var(--radius-sm)] border border-[var(--nm-hairline)] bg-[var(--nm-card)] px-2.5 py-2 text-xs text-[var(--nm-ink70)]">
      <span className="text-[var(--nm-ink50)]">{icon}</span>
      {label}
    </div>
  );
}

function AgentCardSkeleton() {
  return (
    <div className="rounded-[var(--radius-md)] border border-[var(--nm-hairline)] bg-[var(--nm-paper-warm)] p-3.5">
      <div className="flex items-center gap-3">
        <span className="h-10 w-10 shrink-0 animate-pulse rounded-full bg-[var(--nm-row-active)]" />
        <div className="flex-1 space-y-2">
          <span className="block h-2.5 w-24 animate-pulse rounded-[var(--radius-xs)] bg-[var(--nm-row-active)]" />
          <span className="block h-2 w-36 animate-pulse rounded-[var(--radius-xs)] bg-[var(--nm-row-active)]" />
        </div>
      </div>
      <div className="mt-3 space-y-2 border-t border-[var(--nm-hairline)] pt-3">
        <span className="block h-2.5 w-full animate-pulse rounded-[var(--radius-xs)] bg-[var(--nm-row-active)]" />
        <span className="block h-2.5 w-4/5 animate-pulse rounded-[var(--radius-xs)] bg-[var(--nm-row-active)]" />
      </div>
    </div>
  );
}
