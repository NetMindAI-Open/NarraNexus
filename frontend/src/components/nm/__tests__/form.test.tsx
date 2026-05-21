import { describe, test, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import {
  FormField,
  TextInput,
  Textarea,
  Select,
  Toggle,
  Checkbox,
  Radio,
  Slider,
  SearchInput,
} from '../form';

describe('FormField', () => {
  test('renders label + child + hint', () => {
    render(
      <FormField label="Email" hint="we never share">
        <input data-testid="i" />
      </FormField>
    );
    expect(screen.getByText('Email')).toBeInTheDocument();
    expect(screen.getByText('we never share')).toBeInTheDocument();
    expect(screen.getByTestId('i')).toBeInTheDocument();
  });

  test('error shadow label red + alerts role', () => {
    render(
      <FormField label="x" error="Required">
        <input />
      </FormField>
    );
    expect(screen.getByRole('alert')).toHaveTextContent('Required');
  });

  test('required mark renders', () => {
    render(
      <FormField label="Name" required>
        <input />
      </FormField>
    );
    expect(screen.getByText('*')).toBeInTheDocument();
  });
});

describe('TextInput', () => {
  test('typing fires onChange', () => {
    const onChange = vi.fn();
    render(<TextInput placeholder="Name" onChange={onChange} />);
    fireEvent.change(screen.getByPlaceholderText('Name'), { target: { value: 'Jane' } });
    expect(onChange).toHaveBeenCalled();
  });

  test('error attr propagates', () => {
    const { container } = render(<TextInput error />);
    expect(container.firstChild).toHaveAttribute('data-error', 'true');
  });
});

describe('Textarea', () => {
  test('renders with nx-textarea scrollbar class', () => {
    const { container } = render(<Textarea />);
    expect(container.firstChild).toHaveClass('nx-textarea');
  });
});

describe('Select', () => {
  test('renders options + placeholder', () => {
    render(
      <Select
        defaultValue=""
        placeholder="Pick…"
        options={[
          { value: 'a', label: 'Apple' },
          { value: 'b', label: 'Banana' },
        ]}
        onChange={() => {}}
      />
    );
    expect(screen.getByText('Apple')).toBeInTheDocument();
    expect(screen.getByText('Banana')).toBeInTheDocument();
    expect(screen.getByText('Pick…')).toBeInTheDocument();
  });
});

describe('Toggle', () => {
  test('renders switch role + checked state', () => {
    const onChange = vi.fn();
    render(<Toggle checked onChange={onChange} label="dark mode" />);
    const sw = screen.getByRole('switch');
    expect(sw).toHaveAttribute('aria-checked', 'true');
    fireEvent.click(sw);
    expect(onChange).toHaveBeenCalledWith(false);
  });

  test('disabled blocks onChange', () => {
    const onChange = vi.fn();
    render(<Toggle checked={false} onChange={onChange} disabled label="x" />);
    fireEvent.click(screen.getByRole('switch'));
    expect(onChange).not.toHaveBeenCalled();
  });
});

describe('Checkbox', () => {
  test('renders checkbox role + check icon when checked', () => {
    const onChange = vi.fn();
    const { rerender, container } = render(
      <Checkbox checked={false} onChange={onChange} label="agree" />
    );
    expect(container.querySelector('svg')).toBeNull();
    fireEvent.click(screen.getByRole('checkbox'));
    expect(onChange).toHaveBeenCalledWith(true);
    rerender(<Checkbox checked onChange={onChange} label="agree" />);
    expect(container.querySelector('svg')).toBeInTheDocument();
  });
});

describe('Radio', () => {
  test('renders radio role + onChange fires (no toggle, just fire)', () => {
    const onChange = vi.fn();
    render(<Radio checked={false} onChange={onChange} label="option a" />);
    fireEvent.click(screen.getByRole('radio'));
    expect(onChange).toHaveBeenCalledOnce();
  });
});

describe('Slider', () => {
  test('renders range input + value text', () => {
    const onChange = vi.fn();
    render(<Slider value={50} onChange={onChange} label="opacity" unit="%" />);
    expect(screen.getByText('opacity')).toBeInTheDocument();
    expect(screen.getByText('50%')).toBeInTheDocument();
    const range = document.querySelector('input[type="range"]') as HTMLInputElement;
    expect(range.value).toBe('50');
  });

  test('onChange called with new numeric value', () => {
    const onChange = vi.fn();
    render(<Slider value={20} onChange={onChange} />);
    const range = document.querySelector('input[type="range"]') as HTMLInputElement;
    fireEvent.change(range, { target: { value: '75' } });
    expect(onChange).toHaveBeenCalledWith(75);
  });
});

describe('SearchInput', () => {
  test('typing + clearing', () => {
    let value = '';
    const setValue = vi.fn((v: string) => { value = v; });
    const { rerender } = render(
      <SearchInput value={value} onChange={setValue} />
    );
    fireEvent.change(document.querySelector('input') as HTMLInputElement, {
      target: { value: 'foo' },
    });
    expect(setValue).toHaveBeenCalledWith('foo');
    rerender(<SearchInput value="foo" onChange={setValue} />);
    const clearBtn = screen.getByLabelText('Clear search');
    fireEvent.click(clearBtn);
    expect(setValue).toHaveBeenCalledWith('');
  });

  test('Escape key clears value', () => {
    const setValue = vi.fn();
    render(<SearchInput value="abc" onChange={setValue} />);
    const input = document.querySelector('input') as HTMLInputElement;
    fireEvent.keyDown(input, { key: 'Escape' });
    expect(setValue).toHaveBeenCalledWith('');
  });
});
