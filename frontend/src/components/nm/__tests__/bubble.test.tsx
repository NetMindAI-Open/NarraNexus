import { describe, test, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import {
  MessageBubble,
  BubbleGroup,
  BubbleMetaRow,
  TurnBreak,
} from '../bubble';

describe('MessageBubble', () => {
  test.each([
    ['human-other'],
    ['ai-other'],
    ['own'],
    ['own-lilac'],
    ['system'],
    ['tool-result'],
    ['error'],
  ] as const)('renders %s variant', (variant) => {
    const { container } = render(
      <MessageBubble variant={variant}>hi</MessageBubble>
    );
    expect(container.querySelector('[data-nm="message-bubble"]')).toHaveAttribute(
      'data-variant',
      variant
    );
  });

  test('human-other has carbon bracket-edge', () => {
    const { container } = render(
      <MessageBubble variant="human-other">hi</MessageBubble>
    );
    const edge = container.querySelector('[data-nm="bracket-edge"]');
    expect(edge).toHaveAttribute('data-species', 'carbon');
    expect(edge).toHaveAttribute('data-corner', 'tl');
  });

  test('ai-other has silicon bracket-edge', () => {
    const { container } = render(
      <MessageBubble variant="ai-other">hi</MessageBubble>
    );
    const edge = container.querySelector('[data-nm="bracket-edge"]');
    expect(edge).toHaveAttribute('data-species', 'silicon');
  });

  test('own has ink bracket-edge on top-right', () => {
    const { container } = render(<MessageBubble variant="own">hi</MessageBubble>);
    const edge = container.querySelector('[data-nm="bracket-edge"]');
    expect(edge).toHaveAttribute('data-species', 'ink');
    expect(edge).toHaveAttribute('data-corner', 'tr');
  });

  test('system variant has no bubble wrapper', () => {
    const { container } = render(
      <MessageBubble variant="system">Jane joined</MessageBubble>
    );
    const bubble = container.querySelector('[data-nm="message-bubble"]');
    // system variant: NO bracket-edge
    expect(container.querySelector('[data-nm="bracket-edge"]')).toBeNull();
    expect(bubble).toHaveTextContent('Jane joined');
  });

  test('aria-label is applied as accessible note', () => {
    render(
      <MessageBubble variant="own" ariaLabel="You at 12:04">
        message
      </MessageBubble>
    );
    expect(screen.getByRole('note', { name: 'You at 12:04' })).toBeInTheDocument();
  });
});

describe('BubbleGroup', () => {
  test('records gap via data attrs', () => {
    const { container } = render(
      <BubbleGroup sameGap={6} turnGap={20}>
        <MessageBubble variant="own">a</MessageBubble>
      </BubbleGroup>
    );
    const group = container.querySelector('[data-nm="bubble-group"]');
    expect(group).toHaveAttribute('data-same-gap', '6');
    expect(group).toHaveAttribute('data-turn-gap', '20');
  });

  test('default gaps are 4 / 16', () => {
    const { container } = render(<BubbleGroup>x</BubbleGroup>);
    expect(container.firstChild).toHaveAttribute('data-same-gap', '4');
    expect(container.firstChild).toHaveAttribute('data-turn-gap', '16');
  });
});

describe('BubbleMetaRow', () => {
  test('renders sender + time', () => {
    render(<BubbleMetaRow sender="Jane" species="carbon" time="12:04" />);
    expect(screen.getByText('Jane')).toBeInTheDocument();
    expect(screen.getByText('12:04')).toBeInTheDocument();
  });

  test('time is optional', () => {
    render(<BubbleMetaRow sender="Yara" species="silicon" />);
    expect(screen.getByText('Yara')).toBeInTheDocument();
  });
});

describe('TurnBreak', () => {
  test('renders div with calculated height', () => {
    const { container } = render(<TurnBreak turnGap={20} sameGap={4} />);
    const el = container.firstChild as HTMLElement;
    expect(el).toHaveAttribute('data-nm', 'turn-break');
    expect(el.style.height).toBe('16px');
  });
});
