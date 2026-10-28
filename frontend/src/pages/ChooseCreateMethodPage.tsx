/**
 * @file_name: ChooseCreateMethodPage.tsx
 * @author: NetMind.AI
 * @date: 2026-09-03
 * @description: The fork the sidebar's "+" now leads to — configure an agent
 * yourself, or describe what you want and let the agent draft its own
 * configuration.
 *
 * The page exists for a product reason, not a technical one: creating an
 * agent used to be a single click straight into chat, and users came away
 * without noticing they had created anything (they stayed in a
 * "session + task" mindset). One deliberate step is the intervention. It is
 * therefore fine that this page adds a click — that is the feature.
 *
 * BOTH paths end in the same existing useCreateAgent() call, so a blank
 * agent is created identically either way. The AI path differs in exactly
 * two things: it insists on a provider first (a first message that dies on
 * "no provider configured" is a terrible first impression), and it marks the
 * new agent so its first outgoing message carries the builder instruction.
 *
 * Nothing is created on this page itself — leaving it has no side effects.
 */
import { useCallback, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { ChevronRight, FileText, MessageSquare, Loader2 } from 'lucide-react';
import { api } from '@/lib/api';
import { useCreateAgent } from '@/hooks';
import { useStudioStore, useUIStore } from '@/stores';
import { ProviderPickerModal } from '@/components/builder';
import { cn } from '@/lib/utils';

type Phase = 'idle' | 'probing' | 'gated' | 'creating';

export default function ChooseCreateMethodPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { createAgent } = useCreateAgent();
  const openPanel = useUIStore((s) => s.openPanel);
  const openStudio = useStudioStore((s) => s.openStudio);
  const [phase, setPhase] = useState<Phase>('idle');

  const busy = phase === 'probing' || phase === 'creating';

  /** Create the agent, then land on it. `studio` also arms the builder
   *  instruction for whatever the user types first. */
  const create = useCallback(
    async (studio: boolean) => {
      setPhase('creating');
      const agentId = await createAgent();
      if (!agentId) {
        setPhase('idle');
        return;
      }
      if (studio) {
        openStudio(agentId);
        // Reveal the configuration panel straight away: the whole point of
        // this path is that the conversation fills in a panel, so the panel
        // has to be visible before the first message.
        //
        // openPanel, NOT requestPanel — the latter TOGGLES, so entering the
        // studio while the panel already happened to be the open tab would
        // close it instead.
        openPanel('builder');
      }
      navigate('/app/chat');
    },
    [createAgent, navigate, openPanel, openStudio],
  );

  /**
   * Probe once before the AI path.
   *
   * Fails CLOSED: a probe that errors opens the picker rather than letting
   * the user into a conversation whose first message dies on "no provider
   * configured". Recovering from a false block costs one click; recovering
   * from a false pass costs a dead conversation the user has to diagnose.
   */
  const startStudio = useCallback(async () => {
    setPhase('probing');
    try {
      const res = await api.getProviders();
      const count = Object.keys(res.data?.providers ?? {}).length;
      if (count > 0) {
        await create(true);
        return;
      }
    } catch {
      /* fall through to the gate */
    }
    setPhase('gated');
  }, [create]);

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-4xl px-6 py-20 text-center">
        <span
          className="block text-[11px] font-medium uppercase tracking-[0.14em]"
          style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-tertiary)' }}
        >
          {t('builder.choose.eyebrow')}
        </span>
        <h1
          className="mt-3.5 text-[34px] leading-tight font-semibold tracking-[-0.025em] text-balance"
          style={{ fontFamily: 'var(--font-display)', color: 'var(--text-primary)' }}
        >
          {t('builder.choose.title')}
        </h1>
        <p
          className="mx-auto mt-3 max-w-[46ch] text-sm leading-relaxed"
          style={{ color: 'var(--text-secondary)' }}
        >
          {t('builder.choose.subtitle')}
        </p>

        <div className="mt-11 grid gap-5 text-left sm:grid-cols-2">
          <MethodCard
            icon={<FileText className="w-[19px] h-[19px]" />}
            title={t('builder.choose.blankTitle')}
            body={t('builder.choose.blankBody')}
            cta={t('builder.choose.continue')}
            disabled={busy}
            onClick={() => void create(false)}
          />
          <MethodCard
            icon={<MessageSquare className="w-[19px] h-[19px]" />}
            title={t('builder.choose.aiTitle')}
            body={t('builder.choose.aiBody')}
            cta={t('builder.choose.continue')}
            badge={t('builder.choose.recommended')}
            disabled={busy}
            busy={phase === 'probing' || phase === 'creating'}
            onClick={() => void startStudio()}
          />
        </div>
      </div>

      {phase === 'gated' && (
        <ProviderPickerModal
          onReady={() => void create(true)}
          onCancel={() => setPhase('idle')}
        />
      )}
    </div>
  );
}

interface MethodCardProps {
  icon: React.ReactNode;
  title: string;
  body: string;
  cta: string;
  badge?: string;
  disabled?: boolean;
  busy?: boolean;
  onClick: () => void;
}

function MethodCard({ icon, title, body, cta, badge, disabled, busy, onClick }: MethodCardProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={cn(
        'relative w-full rounded-[var(--radius-xl)] p-6 text-left',
        'border transition-colors disabled:opacity-60 disabled:cursor-not-allowed',
        'hover:bg-[var(--nm-row-hover)]',
      )}
      style={{ borderColor: 'var(--nm-hairline)', background: 'var(--bg-primary)' }}
    >
      {badge && (
        <span
          className="absolute right-4 top-4 rounded-full px-2.5 py-0.5 text-[10px] font-medium uppercase tracking-[0.1em]"
          style={{
            fontFamily: 'var(--font-mono)',
            border: '1px solid var(--nm-hairline)',
            background: 'var(--nm-paper-warm)',
            color: 'var(--text-secondary)',
          }}
        >
          {badge}
        </span>
      )}
      <span
        className="flex h-10 w-10 items-center justify-center rounded-[var(--radius-lg)]"
        style={{
          background: 'var(--nm-paper-warm)',
          border: '1px solid var(--nm-hairline)',
          color: 'var(--text-secondary)',
        }}
      >
        {icon}
      </span>
      <h2
        className="mt-11 text-[19px] font-semibold tracking-[-0.01em]"
        style={{ fontFamily: 'var(--font-display)', color: 'var(--text-primary)' }}
      >
        {title}
      </h2>
      <p className="mt-2 text-[13.5px] leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
        {body}
      </p>
      <span
        className="mt-5 inline-flex items-center gap-1.5 text-[13px] font-medium"
        style={{ color: 'var(--text-primary)' }}
      >
        {cta}
        {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <ChevronRight className="w-3.5 h-3.5" />}
      </span>
    </button>
  );
}
