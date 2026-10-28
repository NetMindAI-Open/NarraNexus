/**
 * @file_name: PluginBoundary.tsx
 * @author: Bin Liang
 * @date: 2026-09-07
 * @description: The one error boundary every surface a plugin can render into wraps its content in — a plugin's render crash never takes the host surface down with it.
 *
 * Extracted from `SlotOutlet.tsx` (where it started as a private class) so
 * the two content registries — `MessageBubble`'s message renderer and
 * `TurnTimeline`'s timeline-event component — can use the exact same
 * isolation `SlotOutlet` already gives the six slot points, instead of
 * rendering the plugin's component bare. Before this file existed, a
 * throwing message renderer bubbled to the nearest boundary above
 * `MessageBubble` (`ChunkErrorBoundary` around the whole route content),
 * replacing the entire conversation with an error page over one bad
 * message.
 */
import { Component, type ErrorInfo, type ReactNode } from 'react';

import { reportUiError } from './errorSink';

export interface PluginBoundaryProps {
  /** The owner (plugin id, or "shell") attributed on a caught error. */
  owner: string;
  children: ReactNode;
  /**
   * Rendered instead of `children` once a crash is caught. Evaluated lazily
   * (a function, not a node) so a fallback that reconstructs a whole shell
   * component (e.g. `MessageBubble`'s own bubble) is not built on every
   * render — only on the one render that follows a crash.
   */
  fallback?: () => ReactNode;
}

export class PluginBoundary extends Component<PluginBoundaryProps, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    reportUiError(error, { kind: 'render', source: this.props.owner, context: info.componentStack ?? 'plugin' });
  }

  render() {
    if (this.state.failed) return this.props.fallback ? this.props.fallback() : null;
    return this.props.children;
  }
}
