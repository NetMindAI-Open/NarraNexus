/**
 * @file_name: AnchoredCoachmark.test.tsx
 * @description: The one-bubble-per-anchor invariant. The component exists
 * because two verbatim coachmark copies rendered pixel-overlapped at the same
 * fixed coordinates (local first run arms both the migration nudge and the
 * new-user guide nudge) — these tests are what makes deleting the claim map,
 * or forgetting the cleanup release, go red instead of silently reintroducing
 * the overlap / the never-shows-again leak.
 *
 * anchorHolders is MODULE-level state, so every test imports the component
 * fresh via vi.resetModules() + dynamic import — a leaked claim from one test
 * must not decide the next one.
 */
import { describe, expect, test, vi, beforeEach, afterEach } from 'vitest';
import { render, cleanup } from '@testing-library/react';
import { act } from 'react';

const ANCHOR_ID = 'sidebar.create-agent';
const SELECTOR = `[data-help-id="${ANCHOR_ID}"]`;

function mountAnchor(): HTMLElement {
  const el = document.createElement('button');
  el.setAttribute('data-help-id', ANCHOR_ID);
  // jsdom's getBoundingClientRect returns all zeros and the component
  // deliberately ignores zero-size anchors (collapsed rail) — stub a real box.
  el.getBoundingClientRect = () =>
    ({ top: 100, right: 200, left: 150, bottom: 130, width: 50, height: 30, x: 150, y: 100, toJSON: () => ({}) }) as DOMRect;
  document.body.appendChild(el);
  return el;
}

async function importFresh() {
  vi.resetModules();
  return (await import('../AnchoredCoachmark')).AnchoredCoachmark;
}

const bubbles = () => document.querySelectorAll('[data-testid="coachmark-bubble"]');

beforeEach(() => {
  vi.useFakeTimers();
  mountAnchor();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  document.body.innerHTML = '';
});

describe('AnchoredCoachmark anchor queueing', () => {
  test('two coachmarks on the same anchor render ONE bubble', async () => {
    const AnchoredCoachmark = await importFresh();
    render(
      <>
        <AnchoredCoachmark anchorSelector={SELECTOR} onDismiss={() => {}} dismissLabel="ok">
          <span data-testid="coachmark-bubble">first</span>
        </AnchoredCoachmark>
        <AnchoredCoachmark anchorSelector={SELECTOR} onDismiss={() => {}} dismissLabel="ok">
          <span data-testid="coachmark-bubble">second</span>
        </AnchoredCoachmark>
      </>,
    );
    act(() => {
      vi.advanceTimersByTime(1_000);
    });
    // Without the claim map both portals mount at identical fixed
    // coordinates — the exact overlap bug this component exists to prevent.
    expect(bubbles()).toHaveLength(1);
  });

  test('the queued bubble takes over after the holder unmounts', async () => {
    const AnchoredCoachmark = await importFresh();
    const holder = render(
      <AnchoredCoachmark anchorSelector={SELECTOR} onDismiss={() => {}} dismissLabel="ok">
        <span data-testid="coachmark-bubble">first</span>
      </AnchoredCoachmark>,
    );
    const waiter = render(
      <AnchoredCoachmark anchorSelector={SELECTOR} onDismiss={() => {}} dismissLabel="ok">
        <span data-testid="coachmark-bubble">second</span>
      </AnchoredCoachmark>,
    );
    act(() => {
      vi.advanceTimersByTime(1_000);
    });
    expect(bubbles()).toHaveLength(1);
    expect(document.body.textContent).toContain('first');

    // Holder dismissed/unmounted → its cleanup releases the claim; the
    // waiter's still-running interval claims on the next tick. If cleanup
    // ever stops releasing, this goes red as "second never appears".
    holder.unmount();
    act(() => {
      vi.advanceTimersByTime(1_000);
    });
    expect(bubbles()).toHaveLength(1);
    expect(document.body.textContent).toContain('second');
    waiter.unmount();
  });

  test('different anchors never queue on each other', async () => {
    const AnchoredCoachmark = await importFresh();
    const other = document.createElement('div');
    other.setAttribute('data-help-id', 'other.anchor');
    other.getBoundingClientRect = () =>
      ({ top: 10, right: 20, left: 10, bottom: 20, width: 10, height: 10, x: 10, y: 10, toJSON: () => ({}) }) as DOMRect;
    document.body.appendChild(other);

    render(
      <>
        <AnchoredCoachmark anchorSelector={SELECTOR} onDismiss={() => {}} dismissLabel="ok">
          <span data-testid="coachmark-bubble">a</span>
        </AnchoredCoachmark>
        <AnchoredCoachmark anchorSelector='[data-help-id="other.anchor"]' onDismiss={() => {}} dismissLabel="ok">
          <span data-testid="coachmark-bubble">b</span>
        </AnchoredCoachmark>
      </>,
    );
    act(() => {
      vi.advanceTimersByTime(1_000);
    });
    expect(bubbles()).toHaveLength(2);
  });
});
