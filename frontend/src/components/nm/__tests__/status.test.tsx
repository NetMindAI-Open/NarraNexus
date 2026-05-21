import { describe, test, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import {
  StatusDot,
  StatusBadge,
  ConnectionBanner,
  Toast,
} from '../status';

describe('StatusDot', () => {
  test.each(['success', 'warning', 'error', 'info', 'neutral'] as const)(
    'renders %s with data attr',
    (status) => {
      const { container } = render(<StatusDot status={status} />);
      expect(container.querySelector('[data-nm="status-dot"]')).toHaveAttribute(
        'data-status',
        status
      );
    }
  );

  test('filled=false gives transparent bg', () => {
    const { container } = render(<StatusDot status="success" filled={false} />);
    const el = container.firstChild as HTMLElement;
    expect(el.style.background).toBe('transparent');
  });
});

describe('StatusBadge', () => {
  test('renders label + dot', () => {
    render(<StatusBadge status="success">Online</StatusBadge>);
    expect(screen.getByText('Online')).toBeInTheDocument();
    expect(document.querySelector('[data-nm="status-dot"]')).toBeInTheDocument();
  });

  test('status attr propagates', () => {
    const { container } = render(<StatusBadge status="warning">Throttled</StatusBadge>);
    expect(container.firstChild).toHaveAttribute('data-status', 'warning');
  });
});

describe('ConnectionBanner', () => {
  test('synced state renders nothing', () => {
    const { container } = render(<ConnectionBanner state="synced" />);
    expect(container.firstChild).toBeNull();
  });

  test.each(['connecting', 'sync-error', 'offline'] as const)(
    '%s state renders banner',
    (state) => {
      const { container } = render(<ConnectionBanner state={state} />);
      expect(container.querySelector('[data-nm="connection-banner"]')).toHaveAttribute(
        'data-state',
        state
      );
    }
  );

  test('sync-error shows Retry button when onRetry provided', () => {
    const onRetry = vi.fn();
    render(<ConnectionBanner state="sync-error" onRetry={onRetry} />);
    const btn = screen.getByRole('button', { name: 'Retry' });
    fireEvent.click(btn);
    expect(onRetry).toHaveBeenCalledOnce();
  });

  test('sync-error WITHOUT onRetry: no button', () => {
    render(<ConnectionBanner state="sync-error" />);
    expect(screen.queryByRole('button', { name: 'Retry' })).not.toBeInTheDocument();
  });
});

describe('Toast', () => {
  test('renders title + optional description', () => {
    render(<Toast title="Saved" description="Your changes are live" />);
    expect(screen.getByText('Saved')).toBeInTheDocument();
    expect(screen.getByText('Your changes are live')).toBeInTheDocument();
  });

  test('dismiss button calls onDismiss', () => {
    const onDismiss = vi.fn();
    render(<Toast title="x" onDismiss={onDismiss} />);
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }));
    expect(onDismiss).toHaveBeenCalledOnce();
  });

  test('action slot renders custom node', () => {
    render(<Toast title="x" action={<button>Undo</button>} />);
    expect(screen.getByRole('button', { name: 'Undo' })).toBeInTheDocument();
  });

  test('status propagates to data attr', () => {
    const { container } = render(<Toast title="x" status="warning" />);
    expect(container.firstChild).toHaveAttribute('data-status', 'warning');
  });
});
