import { describe, test, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { Chip, Tag, Badge, CodeBlock, Kbd, Link } from '../misc';

describe('Chip', () => {
  test('renders content', () => {
    render(<Chip>chat</Chip>);
    expect(screen.getByText('chat')).toBeInTheDocument();
  });

  test('species attr propagates', () => {
    const { container } = render(<Chip species="carbon">human</Chip>);
    expect(container.firstChild).toHaveAttribute('data-species', 'carbon');
  });

  test('onDismiss renders x button', () => {
    const onDismiss = vi.fn();
    render(<Chip onDismiss={onDismiss}>x</Chip>);
    fireEvent.click(screen.getByRole('button', { name: 'Remove' }));
    expect(onDismiss).toHaveBeenCalledOnce();
  });
});

describe('Tag', () => {
  test('renders text', () => {
    render(<Tag>BETA</Tag>);
    expect(screen.getByText('BETA')).toBeInTheDocument();
  });
});

describe('Badge', () => {
  test('count=0 renders nothing', () => {
    const { container } = render(<Badge count={0} />);
    expect(container.firstChild).toBeNull();
  });

  test('count<=max renders exact count', () => {
    render(<Badge count={12} />);
    expect(screen.getByText('12')).toBeInTheDocument();
  });

  test('count > max renders "max+"', () => {
    render(<Badge count={150} max={99} />);
    expect(screen.getByText('99+')).toBeInTheDocument();
  });

  test('dot variant always renders, even count=0', () => {
    const { container } = render(<Badge count={0} dot />);
    expect(container.querySelector('[data-dot="true"]')).toBeInTheDocument();
  });
});

describe('CodeBlock', () => {
  test('renders code', () => {
    render(<CodeBlock code="const x = 1;" />);
    expect(screen.getByText('const x = 1;')).toBeInTheDocument();
  });

  test('language label renders', () => {
    render(<CodeBlock code="x" language="javascript" />);
    expect(screen.getByText('javascript')).toBeInTheDocument();
  });

  test('copy button calls clipboard API', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    render(<CodeBlock code="hello" language="text" />);
    fireEvent.click(screen.getByRole('button', { name: 'Copy' }));
    expect(writeText).toHaveBeenCalledWith('hello');
  });
});

describe('Kbd', () => {
  test('renders each key in own kbd element', () => {
    const { container } = render(<Kbd keys={['Cmd', 'K']} />);
    expect(container.querySelectorAll('kbd')).toHaveLength(2);
    expect(screen.getByText('Cmd')).toBeInTheDocument();
    expect(screen.getByText('K')).toBeInTheDocument();
  });

  test('separator renders between keys', () => {
    const { container } = render(<Kbd keys={['Shift', 'A']} />);
    expect(container).toHaveTextContent('Shift');
    expect(container).toHaveTextContent('+');
    expect(container).toHaveTextContent('A');
  });
});

describe('Link', () => {
  test('renders anchor + external attrs', () => {
    render(
      <Link href="https://x.com" external>
        outside
      </Link>
    );
    const a = screen.getByRole('link', { name: /outside/ });
    expect(a).toHaveAttribute('target', '_blank');
    expect(a).toHaveAttribute('rel', 'noopener noreferrer');
  });

  test('internal link no target/rel', () => {
    render(<Link href="/inside">inside</Link>);
    const a = screen.getByRole('link');
    expect(a).not.toHaveAttribute('target');
  });
});
