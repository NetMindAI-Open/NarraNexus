/**
 * @file_name: SettingsPage.tsx
 * @author: Bin Liang
 * @date: 2026-09-03
 * @description: Settings — a master–detail page whose panes come from the SETTINGS_SECTIONS registry.
 *
 * Global (not per-agent) settings. Per-agent model/framework selection lives
 * in the chat page; import/export entries live in the sidebar's New menu +
 * Export row. The left nav lists the registered sections (builtin ones are
 * registered by `platform/builtin.ts`, plugins add theirs) filtered by the
 * same gates as before: `desktopOnly` (Tauri only), `cloudHidden` (no plugin
 * installs on cloud), `neverDefault` (reachable but never the landing pane).
 */
import { Suspense, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Loader2 } from 'lucide-react';
import { ScrollArea } from '@/components/ui';
import { isTauri } from '@/lib/tauri';
import { MASTER_DETAIL_ROW_CLASS, MASTER_NAV_CLASS, masterNavItemClass } from '@/lib/masterDetailNav';
import { isForcedCloud } from '@/lib/runtimeConfig';
import { SETTINGS_SECTIONS, sortedSettingsSections, useRegistryEntries } from '@/platform/registries';
import './settings/registerBuiltinSections';

export default function SettingsPage() {
  const { t } = useTranslation();
  // `isForcedCloud()` reads window.__NARRANEXUS_CONFIG__ SYNCHRONOUSLY with
  // zero persistence — so the `active` initializer below sees the filtered
  // list on the very FIRST frame and a cloud `?tab=plugins` deep link falls
  // back to the first visible pane instead of opening an empty one.
  const isCloud = isForcedCloud();
  // Subscribed: a plugin adding a section re-renders the nav.
  const items = sortedSettingsSections({ isTauri: isTauri(), isCloud }, useRegistryEntries(SETTINGS_SECTIONS));
  const [searchParams] = useSearchParams();
  // `?tab=<section id>` opens a pane directly (Stripe returns payers to
  // /app/settings?tab=account&status=…). Only the FIRST render honors the
  // URL; afterwards the user's clicks own the selection. An unknown id, or
  // one this session cannot see, falls back to the first visible item.
  const [active, setActive] = useState(() => {
    const requested = searchParams.get('tab');
    if (requested && items.some((it) => it.id === requested)) return requested;
    return items.find((it) => !it.value.neverDefault)?.id ?? 'providers';
  });
  const current = SETTINGS_SECTIONS.get(active);
  const Pane = current?.component;

  return (
    <div className="h-full flex flex-col">
      <header className="px-6 pt-6 pb-4 shrink-0">
        <h1
          className="text-3xl font-bold tracking-tight"
          style={{ color: 'var(--nm-ink)', fontFamily: 'var(--font-display)' }}
        >
          {t('pages.settings.title')}
        </h1>
      </header>

      <div className={MASTER_DETAIL_ROW_CLASS}>
        {/* Left nav (master). Below md there's no room for a fixed 224px
            column (it ate most of a 360px viewport, GitHub #130) — it
            becomes a horizontal scroll strip above the content instead. */}
        <nav
          className={MASTER_NAV_CLASS}
          style={{ borderColor: 'var(--nm-line)' }}
        >
          {items.map((entry) => {
            const item = entry.value;
            const Icon = item.icon;
            const isActive = active === entry.id;
            return (
              <button
                key={entry.id}
                type="button"
                onClick={() => setActive(entry.id)}
                className={masterNavItemClass(isActive)}
              >
                <Icon className="w-4 h-4 shrink-0" />
                {t(item.labelKey)}
              </button>
            );
          })}
        </nav>

        {/* Content (detail) */}
        <ScrollArea className="flex-1" viewportClassName="p-6">
          <div className="max-w-3xl mx-auto">
            <Suspense
              fallback={
                <div className="flex items-center justify-center py-16">
                  <Loader2 className="w-5 h-5 animate-spin" style={{ color: 'var(--text-tertiary)' }} />
                </div>
              }
            >
              {Pane ? <Pane navigate={setActive} /> : null}
            </Suspense>
          </div>
        </ScrollArea>
      </div>
    </div>
  );
}
