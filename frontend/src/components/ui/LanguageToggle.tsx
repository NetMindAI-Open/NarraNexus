/**
 * Language switcher — flagged dropdown over the supported UI languages
 * (the narra.nexus homepage set). Lives in the sidebar footer next to
 * ThemeToggle; opens upward. Driven by SUPPORTED_LANGUAGES so adding a
 * language needs no change here.
 */
import { useState } from 'react';
import { Globe, Check } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { Button } from './Button';
import { Popover, PopoverTrigger, PopoverContent } from './popover';
import { cn } from '@/lib/utils';
import { SUPPORTED_LANGUAGES } from '@/i18n';

export function LanguageToggle() {
  const { i18n } = useTranslation();
  const [open, setOpen] = useState(false);

  const current =
    SUPPORTED_LANGUAGES.find(
      (l) => i18n.resolvedLanguage === l.code || i18n.language?.startsWith(l.code),
    ) ?? SUPPORTED_LANGUAGES[0];

  const select = (code: string) => {
    void i18n.changeLanguage(code);
    setOpen(false);
  };

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          title={`Language: ${current.label}`}
          aria-label={`Language: ${current.label}`}
          // Match ThemeToggle: muted by default, ORANGE on hover, no bg highlight.
          className="relative gap-1 text-[var(--text-secondary)] transition-colors hover:bg-transparent! hover:text-[var(--color-carbon)]"
        >
          <Globe className="h-4 w-4" />
          <span className="text-[9px] font-mono uppercase tracking-wider">{current.code}</span>
        </Button>
      </PopoverTrigger>
      <PopoverContent side="top" align="start" className="w-44 p-1">
        <div className="max-h-72 overflow-y-auto">
          {SUPPORTED_LANGUAGES.map((l) => {
            const active = l.code === current.code;
            return (
              <button
                key={l.code}
                onClick={() => select(l.code)}
                dir={l.code === 'ar' ? 'rtl' : undefined}
                className={cn(
                  'w-full flex items-center gap-2.5 px-2.5 py-1.5 rounded text-sm transition-colors',
                  'hover:bg-[var(--bg-tertiary)]',
                  active ? 'text-[var(--accent-primary)]' : 'text-[var(--text-primary)]',
                )}
              >
                <span className="text-base leading-none">{l.flag}</span>
                <span className="flex-1 text-left">{l.label}</span>
                {active && <Check className="w-3.5 h-3.5 shrink-0" />}
              </button>
            );
          })}
        </div>
      </PopoverContent>
    </Popover>
  );
}
