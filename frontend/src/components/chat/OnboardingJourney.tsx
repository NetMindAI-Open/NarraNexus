/**
 * OnboardingJourney — the "JourneyBand" empty state for a fresh conversation.
 *
 * Why this exists
 * ---------------
 * When an agent is selected but the conversation has no messages yet, the old
 * empty state was a single generic line ("Start a conversation"). The Narra
 * Agent App design ref replaces that blank moment with a JourneyBand: the
 * carbon·silicon binding-dot eyebrow, a short framing line, the three product
 * stations (Narra·Memory → Nexus·Network → Your Team) with a carbon pulse
 * travelling the baseline, and a few suggested-prompt chips.
 *
 * The chips don't auto-send — clicking one fills the composer (via
 * ChatPanel's composerRef.setText) and focuses it, so the user can edit then
 * hit Enter. The literal day-zero "I just woke up" copy stays in
 * BOOTSTRAP_GREETING (shown for brand-new unnamed agents); this band is the
 * generic fresh-start surface for any selected agent.
 */
import { BookMarked, Share2, Sparkles } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { BindingDot } from '@/components/nm';

interface OnboardingJourneyProps {
  /** Display name of the selected agent, woven into the framing line. */
  agentName?: string;
  /** Fill the composer with a suggested prompt (does not send). */
  onPrompt: (text: string) => void;
}

interface Station {
  icon: typeof BookMarked;
  brandKey: string;
  captionKey: string;
  color: string;
}

const STATIONS: Station[] = [
  { icon: BookMarked, brandKey: 'chat.onboarding.narraBrand', captionKey: 'chat.onboarding.narraCaption', color: 'var(--color-carbon)' },
  { icon: Share2, brandKey: 'chat.onboarding.nexusBrand', captionKey: 'chat.onboarding.nexusCaption', color: 'var(--color-silicon)' },
  { icon: Sparkles, brandKey: 'chat.onboarding.teamBrand', captionKey: 'chat.onboarding.teamCaption', color: 'var(--nm-ink)' },
];

const SUGGESTED_PROMPTS: { dot: string; textKey: string }[] = [
  { dot: 'var(--color-carbon)', textKey: 'chat.onboarding.prompt1' },
  { dot: 'var(--color-silicon)', textKey: 'chat.onboarding.prompt2' },
  { dot: 'var(--nm-ink)', textKey: 'chat.onboarding.prompt3' },
];

export function OnboardingJourney({ agentName, onPrompt }: OnboardingJourneyProps) {
  const { t } = useTranslation();
  const name = agentName?.trim() || t('chat.onboarding.defaultAgentName');

  return (
    <div className="flex-1 min-h-0 overflow-y-auto py-10 px-6 animate-fade-in">
      <div className="mx-auto flex max-w-[640px] flex-col items-center text-center">
        {/* eyebrow: binding-dot + mono label */}
        <div className="mb-6 flex items-center gap-2.5">
          <BindingDot size={7} />
          <span
            className="font-mono uppercase"
            style={{ fontSize: 11, letterSpacing: '0.16em', color: 'var(--nm-ink50)' }}
          >
            {t('chat.onboarding.eyebrow')}
          </span>
        </div>

        <h1
          className="font-display"
          style={{ fontSize: 30, fontWeight: 700, letterSpacing: '-0.02em', lineHeight: 1.1, margin: '0 0 14px', color: 'var(--nm-ink)' }}
        >
          {t('chat.onboarding.heading')}
        </h1>
        <p style={{ fontSize: 15, lineHeight: 1.65, color: 'var(--nm-ink70)', maxWidth: '30rem', margin: '0 0 40px' }}>
          {t('chat.onboarding.framing', { name })}
        </p>

        {/* JourneyBand: memory → network → team */}
        <div className="relative mb-2 w-full" style={{ maxWidth: 520 }}>
          {/* dotted baseline */}
          <div
            style={{ position: 'absolute', top: 24, left: '8%', right: '8%', height: 0, borderTop: '1.5px dotted var(--nm-ink30)' }}
          />
          {/* travelling carbon pulse */}
          <div
            className="animate-travel"
            style={{ position: 'absolute', top: 21, width: 7, height: 7, borderRadius: 9999, background: 'var(--color-carbon)', boxShadow: '0 0 0 5px var(--color-carbon-soft)' }}
          />
          <div className="relative flex items-start justify-between">
            {STATIONS.map((s) => {
              const Icon = s.icon;
              return (
                <div key={s.brandKey} className="flex flex-col items-center gap-2.5" style={{ width: '33%' }}>
                  <span
                    className="inline-flex items-center justify-center"
                    style={{ width: 48, height: 48, borderRadius: 9999, background: 'var(--nm-card)', border: `2px solid ${s.color}`, color: s.color }}
                  >
                    <Icon className="h-5 w-5" strokeWidth={2} />
                  </span>
                  <div>
                    <div
                      className="font-mono uppercase"
                      style={{ fontSize: 10, letterSpacing: '0.1em', color: s.color, fontWeight: 500 }}
                    >
                      {t(s.brandKey)}
                    </div>
                    <div style={{ fontSize: 12, color: 'var(--nm-ink50)', marginTop: 3 }}>{t(s.captionKey)}</div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        {/* suggested prompts */}
        <div className="mt-11 w-full">
          <div
            className="font-mono uppercase"
            style={{ fontSize: 10, letterSpacing: '0.16em', color: 'var(--nm-ink30)', marginBottom: 14 }}
          >
            {t('chat.onboarding.tryAsking')}
          </div>
          <div className="mx-auto flex flex-col gap-2" style={{ maxWidth: 420 }}>
            {SUGGESTED_PROMPTS.map((p) => {
              const promptText = t(p.textKey);
              return (
                <button
                  key={p.textKey}
                  type="button"
                  onClick={() => onPrompt(promptText)}
                  className="hover-lift flex items-center gap-2.5 text-left"
                  style={{ padding: '12px 14px', border: '1px solid var(--nm-hairline)', borderRadius: 'var(--radius-lg)', background: 'var(--nm-card)' }}
                >
                  <span style={{ width: 6, height: 6, borderRadius: 9999, background: p.dot, flexShrink: 0 }} />
                  <span style={{ fontSize: 14, color: 'var(--nm-ink)' }}>{promptText}</span>
                </button>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}
