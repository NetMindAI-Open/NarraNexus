// design_system.md §6.2 exemption: user-picked team accent presets are DATA
// (stored per team via team.color), not UI styling — a fixed series palette,
// not tokens. Single source for every team create/edit surface; order matters:
// [0] is the default color new teams start from.
export const COLOR_PRESETS = [
  '#3b82f6', // blue
  '#22c55e', // green
  '#f59e0b', // amber
  '#ef4444', // red
  '#a855f7', // purple
  '#06b6d4', // cyan
  '#ec4899', // pink
  '#64748b', // slate
];
