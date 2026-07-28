/**
 * @file SettingsModal.tsx
 * @description Full-screen settings modal (ChatGPT-style) with sidebar navigation.
 *
 * Replaces the small popover with a spacious modal containing:
 *   - Provider Management (add/remove providers)
 *   - Model Assignment (Agent / Helper LLM with descriptions)
 *
 * Each slot section includes a plain-language explanation of what it does and
 * how it affects the Agent's behavior, making it accessible to non-technical users.
 */

import { useState, useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { X, Cpu, Info, Shield, Monitor } from 'lucide-react';
import { createPortal } from 'react-dom';
import { cn } from '@/lib/utils';
import { Button, ScrollArea } from '@/components/ui';
import { ProviderSettings } from './ProviderSettings';
import { useConfigStore } from '@/stores/configStore';
import { usePowerStore } from '@/stores/powerStore';
import { api } from '@/lib/api';
import { isTauri } from '@/lib/tauri';

// =============================================================================
// Sidebar navigation sections
// =============================================================================

interface NavSection {
  id: string;
  labelKey: string;
  icon: typeof Cpu;
}

const NAV_SECTIONS: NavSection[] = [
  { id: 'providers', labelKey: 'settings.modal.navProviders', icon: Cpu },
  { id: 'privacy', labelKey: 'settings.modal.navPrivacy', icon: Shield },
  // Desktop-only power controls — filtered out on web in the component.
  { id: 'desktop', labelKey: 'settings.modal.navDesktop', icon: Monitor },
];

// =============================================================================
// Slot explanation cards (shown above the provider settings)
// =============================================================================

const SLOT_EXPLANATIONS = [
  {
    nameKey: 'settings.modal.slotAgentName',
    color: 'var(--accent-primary)',
    descriptionKey: 'settings.modal.slotAgentDesc',
    protocolKey: 'settings.modal.slotAgentProtocol',
  },
  {
    nameKey: 'settings.modal.slotHelperName',
    color: 'var(--color-warning)',
    descriptionKey: 'settings.modal.slotHelperDesc',
    protocolKey: 'settings.modal.slotHelperProtocol',
  },
];

// =============================================================================
// Props
// =============================================================================

interface SettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
}

// =============================================================================
// Component
// =============================================================================

export function SettingsModal({ isOpen, onClose }: SettingsModalProps) {
  const { t } = useTranslation();
  const [activeSection, setActiveSection] = useState('providers');

  // Desktop-only sections are hidden on web — the power controls need the
  // Tauri shell (caffeinate lives on the Rust side).
  const navSections = NAV_SECTIONS.filter((s) => s.id !== 'desktop' || isTauri());

  // Locked Use (prevent sleep) — desktop only.
  const preventSleep = usePowerStore((s) => s.preventSleep);
  const setPreventSleep = usePowerStore((s) => s.setPreventSleep);
  const [preventSleepBusy, setPreventSleepBusy] = useState(false);
  const handlePreventSleepToggle = useCallback(async () => {
    if (preventSleepBusy) return;
    setPreventSleepBusy(true);
    try {
      await setPreventSleep(!preventSleep);
    } finally {
      setPreventSleepBusy(false);
    }
  }, [preventSleep, preventSleepBusy, setPreventSleep]);

  // Analytics opt-out state: true = analytics ON (opted_out = false)
  const userId = useConfigStore((s) => s.userId);
  const [analyticsEnabled, setAnalyticsEnabled] = useState(true);
  const [analyticsLoading, setAnalyticsLoading] = useState(false);

  // Load analytics opt-out state when the Privacy section is first opened.
  // userId only gates "is someone logged in" — identity itself travels in
  // the auth header set by the api client.
  useEffect(() => {
    if (!isOpen || !userId || activeSection !== 'privacy') return;
    api.getAnalyticsOptOut().then((optedOut) => {
      setAnalyticsEnabled(!optedOut);
    }).catch(() => {
      // non-critical — keep current optimistic state
    });
  }, [isOpen, userId, activeSection]);

  const handleAnalyticsToggle = useCallback(async () => {
    if (!userId || analyticsLoading) return;
    const nextEnabled = !analyticsEnabled;
    setAnalyticsEnabled(nextEnabled);
    setAnalyticsLoading(true);
    try {
      await api.setAnalyticsOptOut(!nextEnabled);
    } catch {
      // revert on failure
      setAnalyticsEnabled(!nextEnabled);
    } finally {
      setAnalyticsLoading(false);
    }
  }, [userId, analyticsEnabled, analyticsLoading]);

  // ESC key to close + lock body scroll
  const handleEscape = useCallback((e: KeyboardEvent) => {
    if (e.key === 'Escape') onClose();
  }, [onClose]);

  useEffect(() => {
    if (isOpen) {
      document.addEventListener('keydown', handleEscape);
      document.body.style.overflow = 'hidden';
    }
    return () => {
      document.removeEventListener('keydown', handleEscape);
      document.body.style.overflow = '';
    };
  }, [isOpen, handleEscape]);

  if (!isOpen) return null;

  return createPortal(
    <div className="fixed inset-0 z-50">
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-[rgba(17,18,20,0.6)] animate-fade-in"
        onClick={onClose}
      />

      {/* Modal container */}
      <div className="fixed inset-0 flex items-center justify-center p-6">
        <div
          className={cn(
            'relative w-full max-w-4xl h-[85vh] overflow-hidden',
            'bg-[var(--bg-primary)] border border-[var(--text-primary)]',
            'animate-slide-up',
            'flex flex-col',
          )}
          onClick={(e) => e.stopPropagation()}
        >

          {/* ─── Header ─── */}
          <div className="relative flex items-center justify-between px-6 py-4 border-b border-[var(--border-subtle)] shrink-0">
            <h2 className="text-lg font-semibold text-[var(--text-primary)]">{t('settings.modal.title')}</h2>
            <Button
              variant="ghost"
              size="icon"
              onClick={onClose}
              className="w-8 h-8 hover:bg-[var(--bg-tertiary)]"
            >
              <X className="w-4 h-4" />
            </Button>
          </div>

          {/* ─── Body: sidebar + content ─── */}
          <div className="relative flex flex-1 min-h-0">
            {/* Sidebar */}
            <nav className="w-48 shrink-0 border-r border-[var(--border-subtle)] py-3 px-2">
              {navSections.map((section) => {
                const Icon = section.icon;
                const isActive = activeSection === section.id;

                return (
                  <button
                    key={section.id}
                    onClick={() => setActiveSection(section.id)}
                    className={cn(
                      'w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm transition-colors',
                      isActive
                        ? 'bg-[var(--accent-primary)]/10 text-[var(--accent-primary)] font-medium'
                        : 'text-[var(--text-secondary)] hover:bg-[var(--bg-tertiary)] hover:text-[var(--text-primary)]',
                    )}
                  >
                    <Icon className="w-4 h-4 shrink-0" />
                    {t(section.labelKey)}
                  </button>
                );
              })}
            </nav>

            {/* Content area */}
            <ScrollArea className="flex-1" viewportClassName="p-6">
            <div>
              {/* ─── LLM Providers Section ─── */}
              {activeSection === 'providers' && (
                <div className="space-y-6 max-w-2xl">
                  {/* Slot explanation cards */}
                  <div>
                    <div className="flex items-center gap-2 mb-3">
                      <Info className="w-4 h-4 text-[var(--text-tertiary)]" />
                      <h3 className="text-sm font-medium text-[var(--text-secondary)]">
                        {t('settings.modal.slotsHeading')}
                      </h3>
                    </div>
                    <p className="text-xs text-[var(--text-tertiary)] mb-4">
                      {t('settings.modal.slotsIntro')}
                    </p>
                    <div className="grid grid-cols-1 gap-3">
                      {SLOT_EXPLANATIONS.map((slot) => (
                        <div
                          key={slot.nameKey}
                          className="p-3 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-tertiary)]"
                        >
                          <div className="flex items-center gap-2 mb-1">
                            <div
                              className="w-2 h-2 rounded-full"
                              style={{ backgroundColor: slot.color }}
                            />
                            <span className="text-sm font-medium text-[var(--text-primary)]">
                              {t(slot.nameKey)}
                            </span>
                            <span className="text-[10px] px-1.5 py-0.5 rounded bg-[var(--bg-primary)] text-[var(--text-tertiary)]">
                              {t(slot.protocolKey)}
                            </span>
                          </div>
                          <p className="text-xs text-[var(--text-tertiary)] leading-relaxed ml-4">
                            {t(slot.descriptionKey)}
                          </p>
                        </div>
                      ))}
                    </div>
                  </div>

                  {/* Divider */}
                  <div className="border-t border-[var(--border-subtle)]" />

                  {/* Provider Settings (reused from existing component) */}
                  <ProviderSettings />
                </div>
              )}

              {/* ─── Privacy Section ─── */}
              {activeSection === 'privacy' && (
                <div className="space-y-4 max-w-2xl">
                  <div>
                    <h3 className="text-sm font-medium text-[var(--text-primary)] mb-2">
                      {t('settings.modal.privacyHeading')}
                    </h3>
                    <p className="text-xs text-[var(--text-tertiary)] leading-relaxed">
                      {t('settings.modal.privacyIntro')}
                    </p>
                  </div>

                  {/* Analytics toggle row */}
                  <div className="flex items-center justify-between p-4 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-tertiary)]">
                    <div className="flex-1 min-w-0 pr-4">
                      <p className="text-sm font-medium text-[var(--text-primary)]">
                        {t('settings.modal.analyticsTitle')}
                      </p>
                      <p className="text-xs text-[var(--text-tertiary)] mt-0.5 leading-relaxed">
                        {t('settings.modal.analyticsDesc')}
                      </p>
                    </div>
                    {/* Inline toggle button */}
                    <button
                      type="button"
                      role="switch"
                      aria-checked={analyticsEnabled}
                      disabled={analyticsLoading || !userId}
                      onClick={handleAnalyticsToggle}
                      className={cn(
                        'relative inline-flex h-6 w-11 shrink-0 cursor-pointer items-center rounded-full',
                        'transition-colors duration-200 focus-visible:outline-none',
                        'disabled:cursor-not-allowed disabled:opacity-50',
                        analyticsEnabled
                          ? 'bg-[var(--accent-primary)]'
                          : 'bg-[var(--bg-primary)] border border-[var(--border-subtle)]',
                      )}
                    >
                      <span
                        className={cn(
                          'pointer-events-none inline-block h-4 w-4 rounded-full bg-white shadow-sm',
                          'transition-transform duration-200',
                          analyticsEnabled ? 'translate-x-6' : 'translate-x-1',
                        )}
                      />
                    </button>
                  </div>
                </div>
              )}

              {/* ─── Desktop Section (Tauri only) ─── */}
              {activeSection === 'desktop' && isTauri() && (
                <div className="space-y-4 max-w-2xl">
                  <div>
                    <h3 className="text-sm font-medium text-[var(--text-primary)] mb-2">
                      {t('settings.modal.desktopHeading')}
                    </h3>
                    <p className="text-xs text-[var(--text-tertiary)] leading-relaxed">
                      {t('settings.modal.desktopIntro')}
                    </p>
                  </div>

                  {/* Locked Use toggle row */}
                  <div className="flex items-center justify-between p-4 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-tertiary)]">
                    <div className="flex-1 min-w-0 pr-4">
                      <p className="text-sm font-medium text-[var(--text-primary)]">
                        {t('settings.modal.lockedUseTitle')}
                      </p>
                      <p className="text-xs text-[var(--text-tertiary)] mt-0.5 leading-relaxed">
                        {t('settings.modal.lockedUseDesc')}
                      </p>
                    </div>
                    <button
                      type="button"
                      role="switch"
                      aria-checked={preventSleep}
                      disabled={preventSleepBusy}
                      onClick={handlePreventSleepToggle}
                      className={cn(
                        'relative inline-flex h-6 w-11 shrink-0 cursor-pointer items-center rounded-full',
                        'transition-colors duration-200 focus-visible:outline-none',
                        'disabled:cursor-not-allowed disabled:opacity-50',
                        preventSleep
                          ? 'bg-[var(--accent-primary)]'
                          : 'bg-[var(--bg-primary)] border border-[var(--border-subtle)]',
                      )}
                    >
                      <span
                        className={cn(
                          'pointer-events-none inline-block h-4 w-4 rounded-full bg-white shadow-sm',
                          'transition-transform duration-200',
                          preventSleep ? 'translate-x-6' : 'translate-x-1',
                        )}
                      />
                    </button>
                  </div>
                </div>
              )}
            </div>
            </ScrollArea>
          </div>
        </div>
      </div>
    </div>,
    document.body,
  );
}
