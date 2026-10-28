/**
 * @file_name: ChunkErrorBoundary.tsx
 * @author: NarraNexus
 * @date: 2026-08-12
 * @description: Route-level error boundary that turns a crashed render into a
 * useful prompt instead of a blank white screen, and drives stale-chunk
 * recovery.
 *
 * Two distinct failures reach here (the boundary wraps the whole route tree):
 *  - a stale lazy-chunk 404 after a deploy → auto-reload ONCE (fetches the new
 *    manifest), then a "new version — refresh" prompt if that already happened;
 *  - any OTHER render crash (a real bug) → a neutral "something went wrong"
 *    prompt, and — critically — a reported event, because otherwise a render
 *    bug is invisible to us (the old behavior was a silent white screen).
 *
 * Deliberately dependency-light in RENDER: inline styles + plain English, so the
 * fallback shows even if app CSS / i18n resources were part of what failed.
 *
 * Reporting: a render crash is logged to the console with a distinct tag. That
 * is a weak signal (client-side only), but the app has no frontend error sink
 * yet, and the auth-gated analytics endpoint can't carry a crash that happens
 * logged-out (e.g. on /login). A dedicated render-crash beacon is a follow-up;
 * the load-bearing fix here is that a REAL bug no longer masquerades as "a new
 * version — refresh" (which looped users and hid the bug entirely).
 */
import { Component, type ErrorInfo, type ReactNode } from 'react';
import { reportUiError } from '@/platform/errorSink';
import { isChunkLoadError, reloadOncePerSession } from '@/lib/chunkReload';

interface Props {
  children: ReactNode;
  /** Recovery action for a stale-chunk crash. Injectable for tests; production
   *  mounts leave it unset so it defaults to a real page reload. */
  recover?: () => void;
}

interface State {
  error: Error | null;
}

export class ChunkErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // A crash must not be invisible. Distinct tag so a real render bug is
    // greppable in the console (the only sink available today — see the file
    // header). Chunk failures are expected post-deploy and self-heal below, so
    // they log at a lower level.
    const chunk = isChunkLoadError(error);
    (chunk ? console.warn : console.error)(
      chunk ? '[ChunkErrorBoundary] stale chunk, reloading' : '[ChunkErrorBoundary] render crash',
      error,
      info?.componentStack,
    );
    // The error sink attributes the crash to the shell or to the plugin whose
    // chunk threw, so a misbehaving plugin can be suppressed instead of the page.
    reportUiError(error, { componentStack: info?.componentStack ?? undefined, kind: chunk ? 'chunk' : 'render' });
    // A stale-chunk crash after a deploy self-heals with one reload; a genuine
    // bug does not (chunk === false) and is left for the user to see.
    if (chunk) {
      reloadOncePerSession(this.props.recover ?? (() => window.location.reload()));
    }
  }

  render(): ReactNode {
    const { error } = this.state;
    if (!error) return this.props.children;

    const chunk = isChunkLoadError(error);
    const title = chunk ? 'A new version is available' : 'Something went wrong';
    const body = chunk
      ? 'The page couldn’t finish loading — the app was just updated. Refresh to load the latest version.'
      : 'This page hit an unexpected error. Refresh to try again.';

    return (
      <div
        role="alert"
        style={{
          minHeight: '100vh',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          gap: '1rem',
          padding: '2rem',
          textAlign: 'center',
          fontFamily: 'system-ui, sans-serif',
        }}
      >
        <h1 style={{ fontSize: '1.25rem', fontWeight: 600 }}>{title}</h1>
        <p style={{ maxWidth: '28rem', opacity: 0.7 }}>{body}</p>
        <button
          type="button"
          onClick={() => window.location.reload()}
          style={{
            padding: '0.5rem 1.25rem',
            borderRadius: '8px',
            border: '1px solid currentColor',
            cursor: 'pointer',
            background: 'transparent',
            font: 'inherit',
          }}
        >
          Refresh
        </button>
      </div>
    );
  }
}
