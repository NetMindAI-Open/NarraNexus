/**
 * Theme toggle component - Bioluminescent Terminal style
 * Animated theme toggle button
 */

import { Sun, Moon, Monitor } from 'lucide-react';
import { useTheme } from '@/hooks';
import { Button } from './Button';
import { cn } from '@/lib/utils';

export function ThemeToggle() {
  const { theme, toggleTheme, isDark } = useTheme();

  const Icon = theme === 'system' ? Monitor : isDark ? Moon : Sun;
  const label = theme === 'system' ? 'System' : isDark ? 'Dark' : 'Light';

  return (
    <Button
      variant="ghost"
      size="icon"
      onClick={toggleTheme}
      title={`Theme: ${label}`}
      className="relative"
    >
      <Icon className={cn('h-4 w-4 transition-colors duration-150')} />
    </Button>
  );
}
