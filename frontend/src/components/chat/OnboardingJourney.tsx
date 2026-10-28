/**
 * OnboardingJourney — the v4 in-stream onboarding card for a fresh
 * conversation (Codex-style: a bordered card at the top of the chat flow,
 * not a full-height hero).
 *
 * Why this exists
 * ---------------
 * When an agent is selected but the conversation has no messages yet, this
 * card frames the blank moment: "<Agent> is ready", one line of guidance,
 * and a few suggested-prompt chips. It is dismissible (v4 tweak #1) — the
 * dismissal persists per agent in localStorage, so a user who closed it
 * isn't nagged on every visit.
 *
 * The chips don't auto-send — clicking one fills the composer (via
 * ChatPanel's composerRef.setText) and focuses it, so the user can edit then
 * hit Enter. The literal day-zero "I just woke up" copy stays in
 * BOOTSTRAP_GREETING (shown for brand-new unnamed agents); this card is the
 * generic fresh-start surface for any selected agent. Prompt copy stays
 * scenario-generic (binding rule #4).
 */
import { useState } from 'react';
import { X } from 'lucide-react';
import { useTranslation } from 'react-i18next';

interface OnboardingJourneyProps {
  /** Persists the per-agent dismissal; without it the card can't be closed. */
  agentId?: string | null;
  /** Display name of the selected agent, woven into the title. */
  agentName?: string;
  /** Fill the composer with a suggested prompt (does not send). */
  onPrompt: (text: string) => void;
}

const SUGGESTED_PROMPTS: string[] = [
  'chat.onboarding.prompt1',
  'chat.onboarding.prompt2',
  'chat.onboarding.prompt3',
];

const DISMISS_KEY_PREFIX = 'onboarding_card_dismissed:';

function readDismissed(agentId: string | null | undefined): boolean {
  if (!agentId || typeof window === 'undefined') return false;
  try {
    return window.localStorage.getItem(DISMISS_KEY_PREFIX + agentId) === '1';
  } catch {
    return false;
  }
}

export function OnboardingJourney({ agentId, agentName, onPrompt }: OnboardingJourneyProps) {
  const { t } = useTranslation();
  const [dismissed, setDismissed] = useState(() => readDismissed(agentId));
  const name = agentName?.trim() || t('chat.onboarding.defaultAgentName');

  if (dismissed) return null;

  const handleDismiss = () => {
    setDismissed(true);
    if (!agentId) return;
    try {
      window.localStorage.setItem(DISMISS_KEY_PREFIX + agentId, '1');
    } catch { /* storage unavailable — dismissal just won't persist */ }
  };

  return (
    <div
      className="relative rounded-[var(--radius-md)] border px-4 py-3.5 animate-fade-in"
      style={{ borderColor: 'var(--nm-hairline)', background: 'var(--nm-paper)' }}
    >
      <button
        type="button"
        onClick={handleDismiss}
        title={t('chat.onboarding.dismiss')}
        aria-label={t('chat.onboarding.dismiss')}
        className="absolute right-2 top-2 rounded p-1 text-[var(--nm-ink30)] transition-colors hover:bg-[var(--nm-paper-warm)] hover:text-[var(--nm-ink)]"
      >
        <X className="h-3.5 w-3.5" />
      </button>
      <div className="mb-1.5 text-[13px] font-semibold text-[var(--nm-ink)]">
        {t('chat.onboarding.readyTitle', { name })}
      </div>
      <div className="mb-2.5 text-[13px] leading-relaxed text-[var(--nm-ink70)]">
        {t('chat.onboarding.readyBody')}
      </div>
      <div className="flex flex-wrap gap-1.5">
        {SUGGESTED_PROMPTS.map((key) => {
          const promptText = t(key);
          return (
            <button
              key={key}
              type="button"
              onClick={() => onPrompt(promptText)}
              className="rounded-[var(--radius-sm)] border px-2.5 py-1.5 text-left text-[12px] text-[var(--nm-ink70)] transition-colors hover:border-[var(--border-strong)] hover:text-[var(--nm-ink)]"
              style={{ borderColor: 'var(--nm-hairline)', background: 'var(--nm-card)' }}
            >
              {promptText}
            </button>
          );
        })}
      </div>
    </div>
  );
}
