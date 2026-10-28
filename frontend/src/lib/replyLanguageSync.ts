/**
 * Reply-language sync (PR #284 r2/r3).
 *
 * The UI language is usually DETECTED (localStorage nx_lang / navigator),
 * not clicked, so a toggle-only write-through never covers existing users.
 * Two jobs with two lifetimes: the i18n languageChanged subscription is
 * per PAGE (exactly once — N subscriptions would fire N PUTs per switch);
 * the boot backfill is per USER (logout/login is pure SPA — a module-level
 * boolean would skip user B in the same tab). Fire-and-forget throughout.
 */
import i18n, { SUPPORTED_LANGUAGES } from '@/i18n';
import { api } from '@/lib/api';

let subscribed = false;
const backfilledUsers = new Set<string>();

function isSupported(code: string | undefined): code is string {
  return !!code && SUPPORTED_LANGUAGES.some((l) => code === l.code || code.startsWith(l.code));
}

export function initReplyLanguageSync(userId: string | null | undefined): void {
  if (!subscribed) {
    subscribed = true;
    i18n.on('languageChanged', (code: string) => {
      void api.setReplyLanguage(code).catch(() => undefined);
    });
  }
  // Backfill needs an authenticated identity: never PUT in the logout gap
  // (it would land on the previous identity or 401 -> auth-expired toast).
  if (!userId || backfilledUsers.has(userId)) return;
  backfilledUsers.add(userId);
  void (async () => {
    try {
      const stored = await api.getReplyLanguage();
      const current = i18n.resolvedLanguage;
      if (stored === null && isSupported(current)) {
        await api.setReplyLanguage(current);
      }
    } catch {
      // Best-effort; the next language switch writes through anyway.
    }
  })();
}
