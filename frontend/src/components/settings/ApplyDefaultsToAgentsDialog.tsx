/**
 * @file_name: ApplyDefaultsToAgentsDialog.tsx
 * @author:
 * @date: 2026-08-26
 * @description: After saving the owner default model, optionally clear
 *   per-agent overrides across all agents (clear-to-inherit) per selected
 *   slot. Slots with zero overrides are disabled. Running agents take effect
 *   on their next turn — the dialog copy says so; nothing is force-stopped.
 */
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Dialog, DialogContent, DialogFooter } from '@/components/ui';
import type { SlotOverrideStats } from '@/types/api';

type SlotKey = 'agent' | 'helper_llm';

interface Props {
  isOpen: boolean;
  stats: SlotOverrideStats;
  /** Slots the user actually changed this save — only these are offered, so a
   *  helper-only change never presents the main-model slot for clearing. */
  dirtySlots: SlotKey[];
  onClose: () => void;
  onApply: (slots: string[]) => Promise<void>;
}

const ALL_SLOTS: Array<{ key: SlotKey; labelKey: string; fallback: string }> = [
  { key: 'agent', labelKey: 'pages.settings.modelDefaults.applyAgentSlot', fallback: 'Main model (agent)' },
  { key: 'helper_llm', labelKey: 'pages.settings.modelDefaults.applyHelperSlot', fallback: 'Helper (helper_llm)' },
];

export function ApplyDefaultsToAgentsDialog({ isOpen, stats, dirtySlots, onClose, onApply }: Props) {
  const { t } = useTranslation();
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [applying, setApplying] = useState(false);
  // Only offer the slots the user changed this save.
  const slots = ALL_SLOTS.filter((s) => dirtySlots.includes(s.key));
  // Nothing to offer → render nothing (the caller only opens the dialog when a
  // dirty slot has overrides, so this is a defensive guard for a second caller).
  if (slots.length === 0) return null;

  const toggle = (slot: string) =>
    setChecked((prev) => {
      const next = new Set(prev);
      if (next.has(slot)) next.delete(slot);
      else next.add(slot);
      return next;
    });

  const apply = async () => {
    if (applying || checked.size === 0) return;
    setApplying(true);
    try {
      await onApply([...checked]);
      onClose();
    } finally {
      setApplying(false);
    }
  };

  return (
    <Dialog
      isOpen={isOpen}
      onClose={onClose}
      title={t('pages.settings.modelDefaults.applyTitle', 'Default model updated')}
      size="md"
    >
      <DialogContent>
        <p className="text-[13px] text-[var(--nm-ink70)] mb-3">
          {t('pages.settings.modelDefaults.applyBody',
            'Apply = clear these agents’ manual overrides so they inherit the new default; future default changes will follow too.')}
        </p>
        <div className="space-y-2">
          {slots.map(({ key, labelKey, fallback }) => {
            const count = stats[key];
            const disabled = count === 0;
            return (
              <label key={key} className="flex items-center gap-2 text-[13px]">
                <input
                  type="checkbox"
                  data-testid={`apply-slot-${key}`}
                  disabled={disabled}
                  checked={checked.has(key)}
                  onChange={() => toggle(key)}
                />
                <span>{t(labelKey, fallback)}</span>
                <span className="text-[var(--nm-ink50)]">
                  {disabled
                    ? t('pages.settings.modelDefaults.noOverrides', 'no overrides')
                    : t('pages.settings.modelDefaults.willClear', '{{n}} agents will be cleared', { n: count })}
                </span>
              </label>
            );
          })}
        </div>
        <p className="text-[12px] text-[var(--nm-ink50)] mt-3">
          {t('pages.settings.modelDefaults.applyTotalNote', 'You have {{total}} agents in total.', { total: stats.total_agents })}
          {' '}
          {t('pages.settings.modelDefaults.applyRunningNote',
            'Running agents take effect on their next turn; the current run is not interrupted.')}
        </p>
      </DialogContent>
      <DialogFooter>
        <button
          data-testid="apply-cancel-btn"
          onClick={onClose}
          className="px-3 py-1.5 text-[13px] text-[var(--nm-ink70)]"
        >
          {t('pages.settings.modelDefaults.saveDefaultOnly', 'Save default only')}
        </button>
        <button
          data-testid="apply-confirm-btn"
          onClick={apply}
          disabled={applying || checked.size === 0}
          className="px-3 py-1.5 text-[13px] font-semibold disabled:opacity-50"
        >
          {t('pages.settings.modelDefaults.applyToChecked', 'Apply to selected agents')}
        </button>
      </DialogFooter>
    </Dialog>
  );
}
