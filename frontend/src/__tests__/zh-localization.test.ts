/**
 * @file_name: zh-localization.test.ts
 * @description: Regression coverage for Chinese localization of first-run and model reasoning UI.
 */

import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import en from '@/i18n/locales/en.json';
import zh from '@/i18n/locales/zh.json';

const source = (path: string) => readFileSync(new URL(path, import.meta.url), 'utf8');

describe('Chinese localization completeness', () => {
  it('defines Chinese copy for onboarding, setup, greeting, and reasoning controls', () => {
    expect(zh.onboarding.checklist).toBeDefined();
    expect(zh.pages.setup.welcome).not.toBe(en.pages.setup.welcome);
    expect(zh.chat.bootstrapGreeting).not.toBe(en.chat.bootstrapGreeting);
    expect(zh.chat.securityReminder).not.toBe(en.chat.securityReminder);
    expect(zh.chat.execution.loadingContext).not.toBe(en.chat.execution.loadingContext);
    expect(zh.pages.settings.modelDefaults.reasoningEffort).not.toBe(
      en.pages.settings.modelDefaults.reasoningEffort,
    );
  });

  it('routes the affected frontend surfaces through i18n', () => {
    const agentConfig = source('../components/chat/AgentLlmConfigPanel.tsx');
    const modelDefaults = source('../components/settings/ModelDefaultsSettings.tsx');
    const chatPanel = source('../components/chat/ChatPanel.tsx');
    const settingsPage = source('../pages/SettingsPage.tsx');
    const oneKeyOnboard = source('../components/settings/OneKeyOnboard.tsx');
    const providerSettings = source('../components/settings/ProviderSettings.tsx');

    expect(source('../components/onboarding/OnboardingChecklist.tsx')).toContain(
      "t('onboarding.checklist.gettingStarted')",
    );
    expect(source('../pages/SetupPage.tsx')).toContain("t('pages.setup.welcome')");
    expect(settingsPage).toContain("t('pages.settings.title')");
    expect(settingsPage).toContain("t(item.labelKey)");
    expect(settingsPage).not.toContain('>Settings<');
    expect(settingsPage).not.toContain("label: 'Model Defaults'");
    expect(oneKeyOnboard).toContain("t('settings.provider.oneKeyTitle')");
    expect(oneKeyOnboard).not.toContain('>One key to start<');
    expect(oneKeyOnboard).not.toContain('Setting up...');
    expect(providerSettings).not.toContain('Checking status...');
    expect(providerSettings).not.toContain('>Claude Code Login<');
    expect(providerSettings).not.toContain('>Codex CLI Login<');
    expect(chatPanel).toContain("t('chat.bootstrapGreeting')");
    expect(chatPanel).toContain("t('chat.securityReminder')");
    expect(chatPanel).not.toContain('Security reminder: never paste sensitive');
    expect(chatPanel).toContain('localizeBootstrapGreeting(item.content)');
    // The pipeline-phase labels moved from ChatPanel's message-area
    // indicator into ProcessPanel (2026-07-30), whose phase-label map now
    // lives in the shared render pieces — assert it there.
    const processShared = source('../components/chat/process/processShared.tsx');
    expect(processShared).toContain("'chat.execution.loadingContext'");
    expect(chatPanel).not.toContain("return 'Loading context...'");
    expect(agentConfig).toContain(
      "t('pages.settings.modelDefaults.reasoningEffort')",
    );
    expect(modelDefaults).toContain(
      "t('pages.settings.modelDefaults.reasoningEffort')",
    );

    for (const literal of [
      'Agent model & framework',
      'Agent (main dialogue)',
      'Helper LLM (background tasks)',
      'Select provider…',
      'Select model…',
      'Reset this slot to the global default',
    ]) {
      expect(agentConfig).not.toContain(`>${literal}<`);
      expect(modelDefaults).not.toContain(`>${literal}<`);
    }
  });
});
