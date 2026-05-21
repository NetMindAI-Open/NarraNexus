import { describe, test, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Skeleton, Spinner, ProgressBar } from '../feedback';

describe('Skeleton', () => {
  test('default variant=rect with default width/height', () => {
    const { container } = render(<Skeleton />);
    const el = container.firstChild as HTMLElement;
    expect(el).toHaveAttribute('data-nm', 'skeleton');
    expect(el).toHaveAttribute('data-variant', 'rect');
  });

  test('text variant renders N lines with last line at 60%', () => {
    const { container } = render(<Skeleton variant="text" lines={3} />);
    const spans = container.querySelectorAll('span.skeleton');
    expect(spans).toHaveLength(3);
    const lastWidth = (spans[2] as HTMLElement).style.width;
    expect(lastWidth).toBe('60%');
  });

  test('circle variant default 40x40 pill', () => {
    const { container } = render(<Skeleton variant="circle" />);
    const el = container.firstChild as HTMLElement;
    expect(el.style.borderRadius).toBe('9999px');
  });
});

describe('Spinner', () => {
  test('default size=18, role=status, aria-label=Loading', () => {
    render(<Spinner />);
    const el = screen.getByRole('status', { name: 'Loading' });
    expect(el).toHaveAttribute('data-nm', 'spinner');
  });

  test('species changes border color', () => {
    const { rerender, container } = render(<Spinner species="carbon" />);
    let el = container.firstChild as HTMLElement;
    expect(el.style.borderColor).toContain('var(--color-carbon)');
    rerender(<Spinner species="silicon" />);
    el = container.firstChild as HTMLElement;
    expect(el.style.borderColor).toContain('var(--color-silicon)');
  });

  test('custom label honored', () => {
    render(<Spinner label="加载中" />);
    expect(screen.getByRole('status', { name: '加载中' })).toBeInTheDocument();
  });
});

describe('ProgressBar', () => {
  test('renders progressbar role with aria-valuenow', () => {
    render(<ProgressBar value={45} />);
    const el = screen.getByRole('progressbar');
    expect(el).toHaveAttribute('aria-valuenow', '45');
  });

  test('clamps value above 100', () => {
    const { container } = render(<ProgressBar value={150} />);
    const fill = container.querySelector('[data-nm="progress-fill"]') as HTMLElement;
    expect(fill.style.width).toBe('100%');
  });

  test('clamps value below 0', () => {
    const { container } = render(<ProgressBar value={-10} />);
    const fill = container.querySelector('[data-nm="progress-fill"]') as HTMLElement;
    expect(fill.style.width).toBe('0%');
  });

  test('label + showPercent renders both', () => {
    render(<ProgressBar value={62} label="Uploading" showPercent />);
    expect(screen.getByText('Uploading')).toBeInTheDocument();
    expect(screen.getByText('62%')).toBeInTheDocument();
  });

  test('custom max produces correct percentage', () => {
    const { container } = render(<ProgressBar value={50} max={200} />);
    const fill = container.querySelector('[data-nm="progress-fill"]') as HTMLElement;
    expect(fill.style.width).toBe('25%');
  });
});
