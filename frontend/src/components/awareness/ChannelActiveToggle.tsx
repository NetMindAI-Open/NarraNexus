/**
 * ChannelActiveToggle — enable/disable a bound IM channel credential.
 *
 * Flips the credential's active flag (Lark `is_active`, others `enabled`) via
 * the channel's set-active endpoint, without a re-bind. Its primary reason to
 * exist: a credential imported from a bundle lands INACTIVE, and the user must
 * activate it here to claim the single connection slot the IM issues per bot.
 * Deactivating is the reverse (e.g. before moving the agent elsewhere).
 *
 * Shared across LarkConfig / SlackConfig / TelegramConfig / WeChatConfig /
 * DiscordConfig so the enable/disable affordance is identical everywhere.
 */
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Loader2, Power } from 'lucide-react';

interface ChannelActiveToggleProps {
  active: boolean;
  /** Flip to `next`; resolve after the backend call so the parent can refetch. */
  onToggle: (next: boolean) => Promise<void>;
}

export function ChannelActiveToggle({ active, onToggle }: ChannelActiveToggleProps) {
  const { t } = useTranslation();
  const [busy, setBusy] = useState(false);

  const handleClick = async () => {
    if (busy) return;
    setBusy(true);
    try {
      await onToggle(!active);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex items-center justify-between gap-3 py-2">
      <div className="flex items-center gap-2 text-xs">
        <Power className={`w-3.5 h-3.5 ${active ? 'text-[var(--color-green-500)]' : 'text-[var(--text-tertiary)]'}`} />
        <span className={active ? 'text-[var(--text-primary)]' : 'text-[var(--text-tertiary)]'}>
          {active ? t('channelActiveToggle.active') : t('channelActiveToggle.inactive')}
        </span>
      </div>
      <button
        type="button"
        onClick={handleClick}
        disabled={busy}
        className="inline-flex items-center gap-1.5 px-2.5 py-1 text-xs border border-[var(--border-default)] hover:bg-[var(--bg-tertiary)] disabled:opacity-50"
      >
        {busy && <Loader2 className="w-3 h-3 animate-spin" />}
        {active ? t('channelActiveToggle.disable') : t('channelActiveToggle.enable')}
      </button>
    </div>
  );
}
