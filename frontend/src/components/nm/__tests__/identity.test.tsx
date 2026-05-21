import { describe, test, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import {
  RingAvatar,
  GroupAvatar,
  SpeciesDot,
  AvatarStack,
  AvatarWithStatus,
} from '../identity';

describe('RingAvatar', () => {
  test('renders label uppercased and trimmed to 2 chars', () => {
    render(<RingAvatar species="carbon" label="jane" />);
    expect(screen.getByText('JA')).toBeInTheDocument();
  });

  test('species attribute matches input', () => {
    const { container } = render(<RingAvatar species="silicon" label="g" />);
    expect(container.querySelector('[data-nm="ring-avatar"]')).toHaveAttribute(
      'data-species',
      'silicon'
    );
  });

  test('size attribute matches input', () => {
    const { container } = render(<RingAvatar species="silicon" label="g" size="xl" />);
    expect(container.querySelector('[data-nm="ring-avatar"]')).toHaveAttribute(
      'data-size',
      'xl'
    );
  });

  test('renders image when src is provided', () => {
    render(<RingAvatar species="carbon" label="m" src="/avatar.png" alt="Max" />);
    const img = screen.getByAltText('Max') as HTMLImageElement;
    expect(img).toBeInTheDocument();
    expect(img.tagName).toBe('IMG');
  });

  test('clickable when onClick provided', () => {
    let clicked = false;
    render(<RingAvatar species="carbon" label="y" onClick={() => { clicked = true; }} />);
    screen.getByRole('button').click();
    expect(clicked).toBe(true);
  });
});

describe('GroupAvatar', () => {
  test('renders member count as default label', () => {
    render(
      <GroupAvatar
        members={[
          { species: 'carbon' },
          { species: 'silicon' },
          { species: 'carbon' },
        ]}
      />
    );
    expect(screen.getByText('3')).toBeInTheDocument();
  });

  test('honors custom label', () => {
    render(<GroupAvatar members={[{ species: 'carbon' }]} label="A" />);
    expect(screen.getByText('A')).toBeInTheDocument();
  });

  test('renders one svg arc per member', () => {
    const { container } = render(
      <GroupAvatar
        members={[{ species: 'carbon' }, { species: 'silicon' }]}
      />
    );
    expect(container.querySelectorAll('circle')).toHaveLength(2);
  });
});

describe('SpeciesDot', () => {
  test('renders with species attribute', () => {
    const { container } = render(<SpeciesDot species="carbon" />);
    expect(container.querySelector('[data-nm="species-dot"]')).toHaveAttribute(
      'data-species',
      'carbon'
    );
  });

  test('filled variant has background, ring variant transparent', () => {
    const filled = render(<SpeciesDot species="silicon" filled />);
    const ring = render(<SpeciesDot species="silicon" />);
    const filledEl = filled.container.querySelector('[data-nm="species-dot"]') as HTMLElement;
    const ringEl = ring.container.querySelector('[data-nm="species-dot"]') as HTMLElement;
    expect(filledEl.style.background).not.toBe('transparent');
    expect(ringEl.style.background).toBe('transparent');
  });
});

describe('AvatarStack', () => {
  test('renders up to max avatars + overflow chip', () => {
    const { container } = render(
      <AvatarStack
        avatars={[
          { species: 'carbon', label: 'a' },
          { species: 'silicon', label: 'b' },
          { species: 'carbon', label: 'c' },
          { species: 'silicon', label: 'd' },
          { species: 'overlap', label: 'e' },
        ]}
        max={3}
      />
    );
    // 3 visible + 1 overflow chip = 4 RingAvatars total
    expect(container.querySelectorAll('[data-nm="ring-avatar"]')).toHaveLength(4);
    expect(container).toHaveTextContent('+2');
  });

  test('no overflow chip when avatars within max', () => {
    const { container } = render(
      <AvatarStack
        avatars={[{ species: 'carbon', label: 'a' }]}
        max={3}
      />
    );
    expect(container.querySelectorAll('[data-nm="ring-avatar"]')).toHaveLength(1);
  });
});

describe('AvatarWithStatus', () => {
  test('wraps child + renders status dot with aria-label', () => {
    render(
      <AvatarWithStatus status="success">
        <RingAvatar species="silicon" label="g" />
      </AvatarWithStatus>
    );
    expect(screen.getByLabelText('Status: success')).toBeInTheDocument();
  });
});
