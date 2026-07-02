/**
 * i18n setup (react-i18next).
 *
 * Frontend UI chrome only — agent replies come back in the user's language
 * from the LLM, so they're not translated here.
 *
 * Languages match the narra.nexus homepage set. Adding/removing one: drop a
 * `locales/<lang>.json` file and add an entry to SUPPORTED_LANGUAGES.
 *
 * Locale files are AUTO-LOADED per language via import.meta.glob — one
 * `locales/<lang>.json` per language (consolidated from the old per-area
 * fragments). English is the source of truth; the other languages are derived
 * assets and all UI strings for a language live in its single JSON file.
 */
import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import LanguageDetector from 'i18next-browser-languagedetector';

/**
 * Languages offered in the UI (narra.nexus homepage set). `flag` is an emoji
 * for the switcher; `label` is the endonym (the language's own name).
 */
export const SUPPORTED_LANGUAGES = [
  { code: 'en', label: 'English', flag: '🇺🇸' },
  { code: 'zh', label: '中文', flag: '🇨🇳' },
  { code: 'ja', label: '日本語', flag: '🇯🇵' },
  { code: 'ko', label: '한국어', flag: '🇰🇷' },
  { code: 'es', label: 'Español', flag: '🇪🇸' },
  { code: 'fr', label: 'Français', flag: '🇫🇷' },
  { code: 'de', label: 'Deutsch', flag: '🇩🇪' },
  { code: 'ru', label: 'Русский', flag: '🇷🇺' },
  { code: 'pt', label: 'Português', flag: '🇧🇷' },
  { code: 'ar', label: 'العربية', flag: '🇸🇦' },
] as const;

export type LanguageCode = (typeof SUPPORTED_LANGUAGES)[number]['code'];

/** Right-to-left languages — drive `dir="rtl"` on the document. */
export const RTL_LANGUAGES: readonly LanguageCode[] = ['ar'];

/** localStorage key the detector caches the chosen language under. */
export const LANG_STORAGE_KEY = 'nx_lang';

type Dict = Record<string, unknown>;

function isObject(v: unknown): v is Dict {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

/** Recursively merge `src` into `target` (later fragments win on leaf conflict). */
function deepMerge(target: Dict, src: Dict): Dict {
  for (const [k, v] of Object.entries(src)) {
    if (isObject(v) && isObject(target[k])) {
      target[k] = deepMerge(target[k] as Dict, v);
    } else {
      target[k] = v;
    }
  }
  return target;
}

// Eager-load every locale file at build time — one `<lang>.json` per language.
// All language codes are two letters; the filename stem is the language.
const fragments = import.meta.glob<Dict>('./locales/*.json', {
  eager: true,
  import: 'default',
});

const resources: Record<string, { translation: Dict }> = {};
for (const [path, frag] of Object.entries(fragments)) {
  const m = path.match(/\.\/locales\/([a-z]{2})\.json$/);
  if (!m) continue;
  const lang = m[1];
  (resources[lang] ??= { translation: {} }).translation = deepMerge(
    resources[lang].translation,
    frag as Dict,
  );
}

i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources,
    fallbackLng: 'en',
    // Detector may resolve to "zh-CN", "pt-BR", etc. — collapse to the base.
    supportedLngs: SUPPORTED_LANGUAGES.map((l) => l.code),
    nonExplicitSupportedLngs: true,
    load: 'languageOnly',
    interpolation: { escapeValue: false }, // React already escapes
    detection: {
      order: ['localStorage', 'navigator'],
      caches: ['localStorage'],
      lookupLocalStorage: LANG_STORAGE_KEY,
    },
  });

/**
 * Keep <html lang> and dir in sync with the active language. RTL languages
 * (Arabic) flip the whole document to right-to-left. Runs on init + change.
 */
function applyDocumentLang(lng: string) {
  const base = (lng || 'en').split('-')[0] as LanguageCode;
  const dir = RTL_LANGUAGES.includes(base) ? 'rtl' : 'ltr';
  if (typeof document !== 'undefined') {
    document.documentElement.lang = base;
    document.documentElement.dir = dir;
  }
}
applyDocumentLang(i18n.resolvedLanguage || i18n.language || 'en');
i18n.on('languageChanged', applyDocumentLang);

export default i18n;
