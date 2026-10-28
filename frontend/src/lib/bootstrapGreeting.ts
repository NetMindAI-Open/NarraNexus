/**
 * @file_name: bootstrapGreeting.ts
 * @author: Bin Liang
 * @date: 2026-09-08
 * @description: Localise the backend's first-run greeting (generic and named forms) without touching scenario-authored greetings.
 *
 * The backend persists two system greetings in English: the generic "who am
 * I" opener for a blank agent and, once the agent has a real name, a named
 * opener (bootstrap/template.py). Both are translated here — before and after
 * persistence — by matching the exact English template; any other greeting is
 * a scenario's own text and passes through verbatim.
 */
import type { TFunction } from 'i18next';

const NAME_TOKEN = '{{name}}';

function templateToRegExp(template: string): RegExp {
  const parts = template.split(NAME_TOKEN).map((p) => p.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
  return new RegExp(`^${parts.join('(.+?)')}$`, 's');
}

export function localizeBootstrapGreeting(content: string | undefined, t: TFunction): string {
  const genericEn = t('chat.bootstrapGreeting', { lng: 'en' });
  if (!content || content === genericEn) return t('chat.bootstrapGreeting');
  const namedEn = t('chat.bootstrapGreetingNamed', { lng: 'en', name: NAME_TOKEN, interpolation: { escapeValue: false } });
  const match = templateToRegExp(namedEn).exec(content);
  if (match) return t('chat.bootstrapGreetingNamed', { name: match[1], interpolation: { escapeValue: false } });
  return content;
}
