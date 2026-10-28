/**
 * @file_name: commands.ts
 * @author: Bin Liang
 * @date: 2026-09-03
 * @description: Command registry — entries the command palette (⌘K) lists next to the shell's own.
 *
 * A command is a label (literal or i18n key), an optional hint, an optional
 * icon and a `run`. Plugins register commands from their bundle; the
 * loader registers a lazy gate for commands a manifest declares so the
 * palette shows them before the plugin's code is loaded.
 */
import type { LucideIcon } from 'lucide-react';

import { Registry } from './registry';

export interface CommandDef {
  /** Literal label, or an i18n key when `labelIsKey` is set (plugins use `plugin:<id>` namespace keys). */
  label: string;
  labelIsKey?: boolean;
  hint?: string;
  icon?: LucideIcon;
  run: () => void | Promise<void>;
  /** Hide from the palette when false (evaluated at render). Named to match `SidebarItemDef`'s
   *  `visible` — NOT the `WhenClause` string grammar (`registries/when.ts`), which is a
   *  different, validated-at-registration mechanism with the same word in its name (M-3). */
  visible?: () => boolean;
}

export const COMMANDS = new Registry<CommandDef>('ui.commands');
