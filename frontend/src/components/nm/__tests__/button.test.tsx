import { describe, test, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Button, IconButton, ButtonGroup, SplitButton } from '../button';

describe('Button', () => {
  test('renders text + default variant primary', () => {
    render(<Button>Save</Button>);
    const btn = screen.getByRole('button', { name: 'Save' });
    expect(btn).toBeInTheDocument();
    expect(btn).toHaveAttribute('data-variant', 'primary');
  });

  test('honors variant prop', () => {
    const { rerender } = render(<Button variant="danger">Delete</Button>);
    expect(screen.getByRole('button', { name: 'Delete' })).toHaveAttribute(
      'data-variant',
      'danger'
    );
    rerender(<Button variant="ghost">Cancel</Button>);
    expect(screen.getByRole('button', { name: 'Cancel' })).toHaveAttribute(
      'data-variant',
      'ghost'
    );
  });

  test('loading state disables button + shows spinner', () => {
    render(<Button loading>Save</Button>);
    const btn = screen.getByRole('button', { name: 'Save' });
    expect(btn).toBeDisabled();
    expect(btn).toHaveAttribute('data-loading', 'true');
    expect(btn.querySelector('[data-nm="inline-spinner"]')).toBeInTheDocument();
  });

  test('disabled prop disables button', () => {
    render(<Button disabled>X</Button>);
    expect(screen.getByRole('button', { name: 'X' })).toBeDisabled();
  });

  test('leading and trailing slots render', () => {
    render(
      <Button leading={<span data-testid="leading">L</span>} trailing={<span data-testid="trailing">T</span>}>
        Mid
      </Button>
    );
    expect(screen.getByTestId('leading')).toBeInTheDocument();
    expect(screen.getByTestId('trailing')).toBeInTheDocument();
  });

  test('size prop changes height class', () => {
    const { rerender, container } = render(<Button size="sm">x</Button>);
    expect(container.firstChild).toHaveClass('h-8');
    rerender(<Button size="lg">x</Button>);
    expect(container.firstChild).toHaveClass('h-12');
  });
});

describe('IconButton', () => {
  test('requires + applies aria-label', () => {
    render(
      <IconButton label="Close dialog">
        <svg width="12" height="12" />
      </IconButton>
    );
    expect(screen.getByRole('button', { name: 'Close dialog' })).toBeInTheDocument();
  });

  test('default appearance is ring (border)', () => {
    const { container } = render(
      <IconButton label="x">
        <svg />
      </IconButton>
    );
    expect(container.firstChild).toHaveAttribute('data-appearance', 'ring');
  });
});

describe('ButtonGroup', () => {
  test('renders children with group marker', () => {
    const { container } = render(
      <ButtonGroup>
        <Button>A</Button>
        <Button>B</Button>
        <Button>C</Button>
      </ButtonGroup>
    );
    expect(container.querySelector('[data-nm="button-group"]')).toBeInTheDocument();
    expect(container.querySelectorAll('[data-nm="button"]')).toHaveLength(3);
  });
});

describe('SplitButton', () => {
  test('renders primary button + dropdown trigger', () => {
    render(
      <SplitButton onPrimaryClick={() => {}} onDropdownClick={() => {}}>
        Deploy
      </SplitButton>
    );
    expect(screen.getByRole('button', { name: 'Deploy' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'More options' })).toBeInTheDocument();
  });
});
