import { describe, test, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import {
  BracketMarkLogo,
  BracketEdge,
  BracketSectionLabel,
  BracketCornerMarks,
  BracketEmptyState,
  BracketDropzone,
  BracketLoading,
} from '../bracket';

describe('BracketMarkLogo', () => {
  test('renders with wordmark by default', () => {
    render(<BracketMarkLogo />);
    expect(screen.getByText('narra')).toBeInTheDocument();
  });

  test('hides wordmark when showWordmark=false', () => {
    render(<BracketMarkLogo showWordmark={false} />);
    expect(screen.queryByText('narra')).not.toBeInTheDocument();
  });

  test('has data-nm marker', () => {
    const { container } = render(<BracketMarkLogo />);
    expect(container.querySelector('[data-nm="bracket-mark-logo"]')).toBeInTheDocument();
  });
});

describe('BracketEdge', () => {
  test('records corner + species as data attrs', () => {
    const { container } = render(
      <div style={{ position: 'relative' }}>
        <BracketEdge corner="tl" species="carbon" />
      </div>
    );
    const el = container.querySelector('[data-nm="bracket-edge"]');
    expect(el).toHaveAttribute('data-corner', 'tl');
    expect(el).toHaveAttribute('data-species', 'carbon');
  });

  test('each corner has unique style positioning', () => {
    const corners = ['tl', 'tr', 'bl', 'br'] as const;
    corners.forEach((c) => {
      const { container } = render(
        <div style={{ position: 'relative' }}>
          <BracketEdge corner={c} />
        </div>
      );
      const el = container.querySelector('[data-nm="bracket-edge"]');
      expect(el).toHaveAttribute('data-corner', c);
    });
  });
});

describe('BracketSectionLabel', () => {
  test('renders children inside bracket-wrapped label', () => {
    render(<BracketSectionLabel>Active Agents</BracketSectionLabel>);
    expect(screen.getByText('Active Agents')).toBeInTheDocument();
  });

  test('renders trailing slot', () => {
    render(
      <BracketSectionLabel trailing={<span>12</span>}>People</BracketSectionLabel>
    );
    expect(screen.getByText('12')).toBeInTheDocument();
  });
});

describe('BracketCornerMarks', () => {
  test('wraps children and renders 4 corner edges', () => {
    const { container } = render(
      <BracketCornerMarks>
        <div>card content</div>
      </BracketCornerMarks>
    );
    expect(container).toHaveTextContent('card content');
    expect(container.querySelectorAll('[data-nm="bracket-edge"]')).toHaveLength(4);
  });
});

describe('BracketEmptyState', () => {
  test('renders label + optional hint + cta', () => {
    render(
      <BracketEmptyState
        label="暂无对话"
        hint="Tap + to start a chat"
        cta={<button>+ NEW</button>}
      />
    );
    expect(screen.getByText('暂无对话')).toBeInTheDocument();
    expect(screen.getByText('Tap + to start a chat')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '+ NEW' })).toBeInTheDocument();
  });
});

describe('BracketDropzone', () => {
  test('renders children and active state attr', () => {
    const { container, rerender } = render(
      <BracketDropzone>Drop here</BracketDropzone>
    );
    expect(container.querySelector('[data-nm="bracket-dropzone"]')).toHaveAttribute(
      'data-active',
      'false'
    );
    rerender(<BracketDropzone active>Drop here</BracketDropzone>);
    expect(container.querySelector('[data-nm="bracket-dropzone"]')).toHaveAttribute(
      'data-active',
      'true'
    );
  });
});

describe('BracketLoading', () => {
  test('default label is "Loading"', () => {
    render(<BracketLoading />);
    expect(screen.getByText('Loading')).toBeInTheDocument();
  });

  test('custom label honored', () => {
    render(<BracketLoading label="加载中" />);
    expect(screen.getByText('加载中')).toBeInTheDocument();
  });

  test('has aria status role for screen readers', () => {
    render(<BracketLoading />);
    expect(screen.getByRole('status')).toBeInTheDocument();
  });
});
