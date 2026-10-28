/**
 * @file_name: bootstrapGreeting.test.ts
 * @author: Bin Liang
 * @date: 2026-09-08
 * @description: The generic and the named system greetings are translated; scenario greetings pass through.
 */
import { describe, expect, it } from 'vitest';
import i18n from '@/i18n';
import { localizeBootstrapGreeting } from '@/lib/bootstrapGreeting';

const en = (key: string, opts?: Record<string, unknown>) => i18n.t(key, { lng: 'en', ...opts }) as string;

describe('localizeBootstrapGreeting', () => {
  it('translates the generic English greeting into the active language', async () => {
    await i18n.changeLanguage('zh');
    const out = localizeBootstrapGreeting(en('chat.bootstrapGreeting'), i18n.t.bind(i18n));
    expect(out).toBe(i18n.t('chat.bootstrapGreeting'));
    expect(out).not.toBe(en('chat.bootstrapGreeting'));
  });

  it('translates the named English greeting and keeps the name', async () => {
    await i18n.changeLanguage('zh');
    const backendText = en('chat.bootstrapGreetingNamed', { name: '汉钟离', interpolation: { escapeValue: false } });
    const out = localizeBootstrapGreeting(backendText, i18n.t.bind(i18n));
    expect(out).toContain('汉钟离');
    expect(out).toBe(i18n.t('chat.bootstrapGreetingNamed', { name: '汉钟离', interpolation: { escapeValue: false } }));
  });

  it('leaves a scenario-authored greeting verbatim', async () => {
    await i18n.changeLanguage('zh');
    expect(localizeBootstrapGreeting('Welcome, commander.', i18n.t.bind(i18n))).toBe('Welcome, commander.');
  });
});
