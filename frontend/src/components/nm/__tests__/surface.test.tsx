import { describe, test, expect } from 'vitest';
import { render } from '@testing-library/react';
import { PaperCard, RaisedPanel, SunkenWell, Divider } from '../surface';

describe('PaperCard', () => {
  test('renders children inside paper-card div', () => {
    const { container } = render(<PaperCard>content</PaperCard>);
    expect(container.querySelector('[data-nm="paper-card"]')).toHaveTextContent('content');
  });

  test('default padding is md', () => {
    const { container } = render(<PaperCard>x</PaperCard>);
    expect(container.firstChild).toHaveClass('p-4');
  });

  test('honors padding prop', () => {
    const { container } = render(<PaperCard padding="lg">x</PaperCard>);
    expect(container.firstChild).toHaveClass('p-6');
  });
});

describe('RaisedPanel', () => {
  test('renders with raised-panel marker', () => {
    const { container } = render(<RaisedPanel>x</RaisedPanel>);
    expect(container.querySelector('[data-nm="raised-panel"]')).toBeInTheDocument();
  });

  test('has paper-raised lift via --nm-elev-1 token', () => {
    const { container } = render(<RaisedPanel>x</RaisedPanel>);
    const el = container.firstChild as HTMLElement;
    // Use the elevation token so the shadow auto-swaps per theme
    // (light = ink-warm-alpha, dark = pure-black-alpha).
    expect(el.style.boxShadow).toBe('var(--nm-elev-1)');
  });
});

describe('SunkenWell', () => {
  test('renders with sunken-well marker and inset shadow', () => {
    const { container } = render(<SunkenWell>x</SunkenWell>);
    const el = container.querySelector('[data-nm="sunken-well"]') as HTMLElement;
    expect(el).toBeInTheDocument();
    expect(el.style.boxShadow).toContain('inset');
  });
});

describe('Divider', () => {
  test('horizontal default is 1px hairline', () => {
    const { container } = render(<Divider />);
    const el = container.querySelector('[data-nm="divider"]') as HTMLElement;
    expect(el).toHaveAttribute('data-orientation', 'horizontal');
    expect(el).toHaveAttribute('data-variant', 'default');
    expect(el.style.height).toBe('1px');
  });

  test('thick variant is 2px ink', () => {
    const { container } = render(<Divider variant="thick" />);
    const el = container.firstChild as HTMLElement;
    expect(el.style.height).toBe('2px');
    expect(el).toHaveAttribute('data-variant', 'thick');
  });

  test('vertical orientation renders div not hr', () => {
    const { container } = render(<Divider orientation="vertical" />);
    const el = container.firstChild as HTMLElement;
    expect(el.tagName).toBe('DIV');
    expect(el).toHaveAttribute('data-orientation', 'vertical');
  });
});
