/**
 * @file_name: appRoutes.test.tsx
 * @author: Bin Liang
 * @date: 2026-09-07
 * @description: The shell's route table really is built from the PAGES registry — delete the wiring in App.tsx and this goes red.
 *
 * `pageRoutes.test.tsx` proves the builder; `builtin.test.ts` proves the
 * registry's content. This closes the last link: `AppRoutes` (what `App`
 * renders) consumes `PAGES`, for the top-level routes and for the children
 * of the protected /app layout.
 */
import { act, render, screen, waitFor } from '@testing-library/react';
import { Suspense, useEffect } from 'react';
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from 'react-router-dom';
import { afterEach, describe, expect, it } from 'vitest';

import { AppRoutes, PluginPagePending } from '@/App';
import { PAGES } from '@/platform/registries';
import { loadPlugins, pluginsBootSettled } from '@/platform/loader';

function LocationProbe() {
  const loc = useLocation();
  return <output data-testid="location">{loc.pathname + loc.search}</output>;
}

/** Navigates on demand from inside the router (so a test can move without remounting). */
let go: ((to: string) => void) | null = null;
function NavigateHandle() {
  const navigate = useNavigate();
  useEffect(() => {
    go = navigate;
    return () => {
      go = null;
    };
  }, [navigate]);
  return null;
}

function mount(at: string) {
  return render(
    <MemoryRouter initialEntries={[at]}>
      <Suspense fallback={<p>loading</p>}>
        <AppRoutes />
      </Suspense>
      <LocationProbe />
      <NavigateHandle />
    </MemoryRouter>,
  );
}

const disposers: Array<() => void> = [];
afterEach(() => {
  while (disposers.length) disposers.pop()!();
});

describe('AppRoutes', () => {
  it('renders a builtin top-level page from the registry (/nm-playground is open, no guard)', async () => {
    mount('/nm-playground');
    expect(await screen.findByText('NM Design System Playground')).toBeInTheDocument();
  });

  it('a top-level page registered after mount is routable on the next entry', async () => {
    mount('/late');
    // Nothing registered at /late: the catch-all sends the visitor to `/`.
    expect(screen.getByTestId('location')).not.toHaveTextContent('/late');
    act(() => {
      disposers.push(
        PAGES.register('late', { path: '/late', element: () => <p>late page</p>, guard: 'open', layout: 'top' }, { owner: 'acme.plugin' }),
      );
    });
    // Re-enter the path now that it exists.
    mount('/late');
    expect(await screen.findByText('late page')).toBeInTheDocument();
  });

  it('a page registered after mount is reachable by navigating the SAME mounted tree (the PAGES subscription)', async () => {
    mount('/nm-playground');
    expect(await screen.findByText('NM Design System Playground')).toBeInTheDocument();
    act(() => {
      disposers.push(
        PAGES.register('late-nav', { path: '/late-nav', element: () => <p>late nav page</p>, guard: 'open', layout: 'top' }, { owner: 'acme.plugin' }),
      );
    });
    // No second render(): navigate inside the mounted router. Without the
    // subscription AppRoutes would still hold the old table and the catch-all
    // would bounce this navigation to `/`.
    act(() => {
      go!('/late-nav');
    });
    expect(await screen.findByText('late nav page')).toBeInTheDocument();
  });

  it('a page registered under /app is a child of the protected layout', async () => {
    disposers.push(
      PAGES.register('probe', { path: 'probe', element: () => <p>probe</p>, guard: 'protected', layout: 'app' }, { owner: 'acme.plugin' }),
    );
    mount('/app/probe');
    // Logged out: the layout's ProtectedRoute bounces to /login and keeps the
    // deep link as ?next=. An unregistered /app child would not match the
    // layout at all and fall to the catch-all (no ?next=).
    // ProtectedRoute settles the app mode in an effect; under a full-suite load the default 1 s budget is not enough.
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('/login?next=%2Fapp%2Fprobe'), { timeout: 5000 });
  });

  it('an unregistered /app child falls through to the catch-all (no deep link kept)', async () => {
    mount('/app/nope');
    await waitFor(() => expect(screen.getByTestId('location')).not.toHaveTextContent('/app/nope'), { timeout: 5000 });
    expect(screen.getByTestId('location')).not.toHaveTextContent('next=');
  });

  it('an unregistered /app/x/* URL matches the layout (deep link kept), unlike any other unregistered /app child', async () => {
    // Logged out, so the proof is the same as for a registered page: the layout's
    // ProtectedRoute bounces to /login and KEEPS the deep link as ?next=. Before the
    // x/* hold existed this fell to the catch-all and the plugin URL was lost.
    mount('/app/x/acme');
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('/login?next=%2Fapp%2Fx%2Facme'), { timeout: 5000 });
  });

  it('the plugin-page hold waits for the plugin boot to settle, then leaves for chat', async () => {
    let release: () => void = () => {};
    const pending = new Promise<Response>((resolve) => { release = () => resolve(new Response('{}', { status: 401 })); });
    const boot = loadPlugins({ fetchImpl: () => pending });
    render(
      <MemoryRouter initialEntries={['/app/x/acme']}>
        <Routes>
          <Route path="/app/x/*" element={<PluginPagePending />} />
          <Route path="/app/chat" element={<p>chat page</p>} />
        </Routes>
        <LocationProbe />
      </MemoryRouter>,
    );
    // Boot in flight: still on the plugin URL, no chat page.
    await new Promise((r) => setTimeout(r, 200));
    expect(screen.getByTestId('location')).toHaveTextContent('/app/x/acme');
    expect(screen.queryByText('chat page')).toBeNull();
    await act(async () => { release(); await boot; });
    // Boot settled and nothing registered at x/acme: now (and only now) leave.
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('/app/chat'), { timeout: 5000 });
  });

  it('two overlapping loadPlugins passes settle only when the last one finishes', async () => {
    const release: Array<() => void> = [];
    const pendingFetch = () => new Promise<Response>((resolve) => { release.push(() => resolve(new Response('{}', { status: 401 }))); });
    const a = loadPlugins({ fetchImpl: pendingFetch });
    const b = loadPlugins({ fetchImpl: pendingFetch });
    expect(pluginsBootSettled()).toBe(false);
    await act(async () => { release[0](); await a; });
    // Pass B is still registering: a deep-link hold must not leave yet.
    expect(pluginsBootSettled()).toBe(false);
    await act(async () => { release[1](); await b; });
    expect(pluginsBootSettled()).toBe(true);
  });
});
