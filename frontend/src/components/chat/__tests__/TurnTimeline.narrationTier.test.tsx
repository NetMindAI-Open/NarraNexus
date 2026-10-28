/**
 * The two tiers actually reach the DOM, in the promoted layout (design B).
 *
 * The value of this feature is entirely in the rendering, and it is the layer
 * a screenshot would check and a unit test usually would not. Pinned here:
 *  1. narration is VISIBLE without clicking anything, at near-body weight;
 *  2. provider reasoning collapses to an affordance and is still reachable
 *     (iron rule #16 is about content being reachable, not about how many
 *     pixels it takes by default);
 *  3. turning the preference off returns narration to the receded tone;
 *  4. either way the text is byte-identical — the tier adds and removes
 *     nothing.
 */
import { describe, expect, test, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { TurnTimeline } from '../TurnTimeline';
import { useUIStore } from '@/stores/uiStore';
import type { TurnEvent } from '@/types';

const NARRATION = 'Official support confirmed. Checking your machine next.';
const COT = 'The user probably means the plugin, not the CLI.';

const thinking = (id: string, content: string, monologue: boolean): TurnEvent =>
  ({ id, ts: 0, type: 'thinking', content, monologue }) as TurnEvent;

/** The rendered block wrapper carries the tier tone as an inline colour. */
const toneOf = (text: string): string => {
  const el = screen.getByText(text);
  const block = el.closest('div[style*="color"]') as HTMLElement | null;
  return block?.style.color ?? '';
};

describe('TurnTimeline tiers', () => {
  beforeEach(() => {
    useUIStore.setState({ interimNarration: true });
  });

  test('narration is visible with no click, at near-body weight', () => {
    render(<TurnTimeline isStreaming events={[thinking('a', NARRATION, true)]} />);

    expect(screen.getByText(NARRATION)).toBeTruthy();
    expect(toneOf(NARRATION)).toContain('--text-secondary');
    // It is prose, not a labelled artifact — no uppercase tier label row.
    expect(screen.queryByText('Thought')).toBeNull();
  });

  test('provider reasoning collapses, and its text is one click away', () => {
    render(<TurnTimeline isStreaming events={[thinking('b', COT, false)]} />);

    expect(screen.queryByText(COT)).toBeNull();
    const toggle = screen.getByRole('button', { expanded: false });
    expect(toggle.textContent).toContain('Thought');

    fireEvent.click(toggle);
    expect(screen.getByText(COT)).toBeTruthy();
    expect(toneOf(COT)).toContain('--nm-ink50');
  });

  test('the two tiers are visually distinguishable in one turn', () => {
    render(
      <TurnTimeline
        isStreaming
        events={[thinking('a', NARRATION, true), thinking('b', COT, false)]}
      />,
    );

    // Narration reads; reasoning is a one-line affordance beside it.
    expect(screen.getByText(NARRATION)).toBeTruthy();
    expect(screen.queryByText(COT)).toBeNull();
    expect(screen.getByRole('button', { expanded: false })).toBeTruthy();
  });

  test('with the preference off, narration returns to the receded tone', () => {
    useUIStore.setState({ interimNarration: false });
    render(<TurnTimeline isStreaming events={[thinking('a', NARRATION, true)]} />);

    // It becomes an ordinary reasoning block: collapsed, dim.
    expect(screen.queryByText(NARRATION)).toBeNull();
    const toggle = screen.getByRole('button', { expanded: false });
    fireEvent.click(toggle);
    expect(screen.getByText(NARRATION)).toBeTruthy();
    expect(toneOf(NARRATION)).toContain('--nm-ink50');
  });

  test('red line 2: the same text is present under both preferences', () => {
    const events = [thinking('a', NARRATION, true), thinking('b', COT, false)];
    const allText = () => {
      screen.getAllByRole('button').forEach((b) => {
        if (b.getAttribute('aria-expanded') === 'false') fireEvent.click(b);
      });
      return [NARRATION, COT].map((t) => !!screen.queryByText(t));
    };

    useUIStore.setState({ interimNarration: true });
    const on = render(<TurnTimeline isStreaming events={events} />);
    expect(allText()).toEqual([true, true]);
    on.unmount();

    useUIStore.setState({ interimNarration: false });
    render(<TurnTimeline isStreaming events={events} />);
    expect(allText()).toEqual([true, true]);
  });

  test('the SETTLED path carries the tier through the markdown variant class', () => {
    // Reloaded history renders with isStreaming=false, which goes through
    // <Markdown> instead of the plain-text branch — the path every historical
    // turn actually uses. Without this, deleting the .markdown-progress rule
    // would break the tier on all history with every other test still green.
    const { container } = render(
      <TurnTimeline
        isStreaming={false}
        events={[thinking('a', NARRATION, true), thinking('b', COT, false)]}
      />,
    );

    expect(container.querySelector('.markdown-progress')?.textContent).toContain(NARRATION);

    fireEvent.click(screen.getByRole('button', { expanded: false }));
    expect(container.querySelector('.markdown-dim')?.textContent).toContain(COT);
  });

  test('both tiers colour through TOKENS, which is what makes light and dark correct', () => {
    // design_system §8: every --nm-* / semantic token is redefined under
    // [data-theme], so a colour that goes through a token is correct in both
    // themes by construction, and a hex or a raw palette value is wrong in one
    // of them by construction. jsdom resolves no CSS variables, so asserting
    // the resolved colour would prove nothing — asserting that the value IS a
    // token is the check that actually carries the guarantee.
    const { container } = render(
      <TurnTimeline
        isStreaming
        events={[thinking('a', NARRATION, true), thinking('b', COT, false)]}
      />,
    );

    const styled = Array.from(container.querySelectorAll('[style*="color"]'));
    expect(styled.length).toBeGreaterThan(0);
    for (const el of styled) {
      const colour = (el as HTMLElement).style.color;
      expect(colour).toMatch(/^var\(--/);
      expect(colour).not.toMatch(/#[0-9a-f]{3,8}/i);
    }
  });

  test('historical events with no monologue flag render as reasoning, without throwing', () => {
    render(
      <TurnTimeline isStreaming events={[{ id: 'x', ts: 0, type: 'thinking', content: COT } as TurnEvent]} />,
    );

    expect(screen.queryByText(COT)).toBeNull();
    fireEvent.click(screen.getByRole('button', { expanded: false }));
    expect(toneOf(COT)).toContain('--nm-ink50');
  });
});
