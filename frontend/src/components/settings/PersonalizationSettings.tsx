/**
 * @file_name: PersonalizationSettings.tsx
 * @author: NarraNexus
 * @date: 2026-08-19
 * @description: The Settings "Personalization" pane — theme, language, and
 * the progress-narration display tier.
 *
 * These controls live here, in Settings, and nowhere else. They used to
 * hide inside the sidebar's account popover, which made the popover a second
 * settings surface and left users unsure what the difference between the two
 * was. The account popover now carries identity only (account / version /
 * logout); everything configurable belongs to this page.
 */

import { useTranslation } from 'react-i18next';
import { Monitor, Sun, Moon, Check, Palette } from 'lucide-react';
import { useTheme } from '@/hooks';
import { SUPPORTED_LANGUAGES } from '@/i18n';
import { cn } from '@/lib/utils';
import { useUIStore } from '@/stores/uiStore';
import { useThemeStore } from '@/stores/themeStore';
import { THEMES, useRegistryEntries } from '@/platform/registries';

const THEME_OPTIONS = [
  { value: 'system', icon: Monitor, labelKey: 'sidebar.themeSystem' },
  { value: 'light', icon: Sun, labelKey: 'sidebar.themeLight' },
  { value: 'dark', icon: Moon, labelKey: 'sidebar.themeDark' },
] as const;

export function PersonalizationSettings() {
  const { t, i18n } = useTranslation();
  const { theme, setTheme } = useTheme();
  const pluginTheme = useThemeStore((s) => s.pluginTheme);
  const setPluginTheme = useThemeStore((s) => s.setPluginTheme);
  // Themes plugins contributed (ui.themes); the list grows as plugins activate.
  const pluginThemes = useRegistryEntries(THEMES);
  const interimNarration = useUIStore((s) => s.interimNarration);
  const setInterimNarration = useUIStore((s) => s.setInterimNarration);
  const currentLang =
    SUPPORTED_LANGUAGES.find(
      (l) => i18n.resolvedLanguage === l.code || i18n.language?.startsWith(l.code),
    ) ?? SUPPORTED_LANGUAGES[0];

  return (
    <div className="space-y-8">
      {/* Theme */}
      <div>
        <h3 className="text-sm font-medium text-[var(--nm-ink)] mb-2">
          {t('sidebar.theme')}
        </h3>
        <div className="flex gap-2" role="radiogroup" aria-label={t('sidebar.theme')}>
          {THEME_OPTIONS.map(({ value, icon: Icon, labelKey }) => {
            const active = theme === value;
            return (
              <button
                key={value}
                type="button"
                role="radio"
                aria-checked={active}
                onClick={() => setTheme(value)}
                className={cn(
                  'flex items-center gap-2 px-4 py-2 rounded-[var(--radius-md)] border text-sm transition-colors',
                  active
                    ? 'border-[var(--accent-primary)] text-[var(--accent-primary)] bg-[var(--accent-primary)]/8'
                    : 'border-[var(--border-default)] text-[var(--nm-ink70)] hover:border-[var(--border-strong)]',
                )}
              >
                <Icon className="w-4 h-4" />
                {t(labelKey)}
              </button>
            );
          })}
        </div>
      </div>

      {/* Plugin themes — token overlays contributed through ui.themes; shown only when one exists. */}
      {pluginThemes.length > 0 && (
        <div>
          <h3 className="text-sm font-medium text-[var(--nm-ink)] mb-2">
            {t('pages.settings.personalization.pluginThemesTitle')}
          </h3>
          <div className="flex flex-wrap gap-2" role="radiogroup" aria-label={t('pages.settings.personalization.pluginThemesTitle')}>
            {[null, ...pluginThemes.map((e) => e.id)].map((id) => {
              const entry = id === null ? null : pluginThemes.find((e) => e.id === id)!;
              const active = pluginTheme === id;
              const label = entry ? entry.value.displayName : t('pages.settings.personalization.pluginThemesNone');
              return (
                <button
                  key={id ?? '__none'}
                  type="button"
                  role="radio"
                  aria-checked={active}
                  data-testid={id ? `plugin-theme-${id}` : 'plugin-theme-none'}
                  onClick={() => {
                    setPluginTheme(id);
                    // A plugin theme declares which base it was designed on; follow it.
                    if (entry && entry.value.dark !== undefined) setTheme(entry.value.dark ? 'dark' : 'light');
                  }}
                  className={cn(
                    'flex items-center gap-2 px-4 py-2 rounded-[var(--radius-md)] border text-sm transition-colors',
                    active
                      ? 'border-[var(--accent-primary)] text-[var(--accent-primary)] bg-[var(--accent-primary)]/8'
                      : 'border-[var(--border-default)] text-[var(--nm-ink70)] hover:border-[var(--border-strong)]',
                  )}
                >
                  <Palette className="w-4 h-4" />
                  {label}
                </button>
              );
            })}
          </div>
        </div>
      )}

      {/* Language */}
      <div>
        <h3 className="text-sm font-medium text-[var(--nm-ink)] mb-2">
          {t('sidebar.language')}
        </h3>
        <div
          className="max-w-sm rounded-[var(--radius-md)] border border-[var(--border-default)] overflow-hidden"
          role="radiogroup"
          aria-label={t('sidebar.language')}
        >
          {SUPPORTED_LANGUAGES.map((l) => {
            const active = l.code === currentLang.code;
            return (
              <button
                key={l.code}
                type="button"
                role="radio"
                aria-checked={active}
                dir={l.code === 'ar' ? 'rtl' : undefined}
                onClick={() => void i18n.changeLanguage(l.code)}
                className={cn(
                  'w-full flex items-center gap-2.5 px-3 py-2 text-sm text-left transition-colors',
                  'border-b border-[var(--nm-hairline)] last:border-b-0',
                  'hover:bg-[var(--nm-paper-warm)]',
                  active ? 'text-[var(--nm-ink)] font-medium' : 'text-[var(--nm-ink70)]',
                )}
              >
                <span className="text-base leading-none">{l.flag}</span>
                <span className="flex-1">{l.label}</span>
                {active && <Check className="w-3.5 h-3.5 shrink-0" />}
              </button>
            );
          })}
        </div>
      </div>

      {/* Progress narration — display tier for the agent's own between-step
          notes. Purely local (localStorage): what it governs is whether the
          reader finds them noisy, so it never reaches the backend. */}
      <div>
        <h3 id="narration-label" className="text-sm font-medium text-[var(--nm-ink)] mb-2">
          {t('pages.settings.personalization.narrationTitle')}
        </h3>
        {/* role=checkbox, not switch: the visual is a tick box, and a screen
            reader should hear the same control the eye sees. The accessible
            NAME is the heading (labelledby) — without it the name would be
            the whole hint sentence and "Progress narration" would never be
            announced. */}
        <button
          type="button"
          role="checkbox"
          aria-checked={interimNarration}
          aria-labelledby="narration-label"
          aria-describedby="narration-hint"
          onClick={() => setInterimNarration(!interimNarration)}
          className={cn(
            'max-w-sm w-full flex items-start gap-3 px-3 py-2.5 text-left',
            'rounded-[var(--radius-md)] border transition-colors',
            interimNarration
              ? 'border-[var(--accent-primary)] bg-[var(--accent-primary)]/8'
              : 'border-[var(--border-default)] hover:border-[var(--border-strong)]',
          )}
        >
          <span
            aria-hidden="true"
            className={cn(
              'mt-0.5 w-4 h-4 shrink-0 rounded-[var(--radius-sm)] border flex items-center justify-center',
              interimNarration
                ? 'border-[var(--accent-primary)] text-[var(--accent-primary)]'
                : 'border-[var(--border-strong)]',
            )}
          >
            {interimNarration && <Check className="w-3 h-3" />}
          </span>
          <span id="narration-hint" className="text-xs leading-relaxed text-[var(--nm-ink70)]">
            {t('pages.settings.personalization.narrationHint')}
          </span>
        </button>
      </div>
    </div>
  );
}
