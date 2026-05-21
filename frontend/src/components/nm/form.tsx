/**
 * @file_name: form.tsx
 * @author: NM Design System Phase 1 (M2)
 * @date: 2026-05-18
 * @description: Form primitives — TextInput, Textarea, Select, Toggle,
 * Checkbox, Radio, Slider, SearchInput, FormField. Implements NM Axiom #4
 * (sunken paper inputs), #3 (rings on toggles/radios), #6 (bracket motif on
 * checkbox / search).
 *
 * Spec: reference/self_notebook/specs/2026-05-18-nm-design-system-design.md §5.6
 */

import {
  forwardRef,
  useId,
  type ChangeEvent,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from 'react';
import { cn } from '@/lib/utils';

// ---------------------------------------------------------------------------
// FormField — label + control wrapper + hint + error
// ---------------------------------------------------------------------------
export interface FormFieldProps {
  label?: string;
  hint?: ReactNode;
  error?: ReactNode;
  required?: boolean;
  /** ID prefix used to wire label htmlFor → child id */
  id?: string;
  children: ReactNode;
  className?: string;
}

export function FormField({
  label,
  hint,
  error,
  required,
  id: providedId,
  children,
  className,
}: FormFieldProps) {
  const autoId = useId();
  const id = providedId ?? autoId;
  return (
    <div data-nm="form-field" className={cn('flex flex-col gap-1.5', className)}>
      {label && (
        <label
          htmlFor={id}
          className="text-[11px] font-medium uppercase tracking-[0.10em]"
          style={{
            fontFamily: 'var(--font-mono)',
            color: error ? 'var(--color-error)' : 'var(--nm-ink50)',
          }}
        >
          {label}
          {required && <span style={{ color: 'var(--color-error)', marginLeft: 2 }}>*</span>}
        </label>
      )}
      <div data-form-field-control id={id}>
        {children}
      </div>
      {hint && !error && (
        <p className="text-xs" style={{ color: 'var(--nm-ink50)' }}>
          {hint}
        </p>
      )}
      {error && (
        <p className="text-xs" style={{ color: 'var(--color-error)' }} role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// TextInput
// ---------------------------------------------------------------------------
export interface TextInputProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'size'> {
  error?: boolean;
  /** Optional leading node (icon) */
  leading?: ReactNode;
  /** Optional trailing node (icon/button) */
  trailing?: ReactNode;
}

export const TextInput = forwardRef<HTMLInputElement, TextInputProps>(
  ({ error, leading, trailing, className, ...rest }, ref) => {
    return (
      <div
        data-nm="text-input"
        data-error={error ? 'true' : 'false'}
        className={cn(
          'flex items-center gap-2 px-3 h-10 rounded-[var(--radius-sm)]',
          'transition-[box-shadow,background-color] duration-150',
          className
        )}
        style={{
          background: 'var(--nm-paper-warm)',
          boxShadow: error
            ? 'inset 0 -2px 0 0 var(--color-error)'
            : 'inset 0 0 0 1px var(--nm-hairline)',
        }}
        onFocus={(e) => {
          if (!error) {
            (e.currentTarget as HTMLDivElement).style.boxShadow = 'inset 0 -2px 0 0 var(--nm-ink)';
          }
        }}
        onBlur={(e) => {
          if (!error) {
            (e.currentTarget as HTMLDivElement).style.boxShadow = 'inset 0 0 0 1px var(--nm-hairline)';
          }
        }}
      >
        {leading && <span className="shrink-0">{leading}</span>}
        <input
          ref={ref}
          type={rest.type ?? 'text'}
          className="flex-1 bg-transparent border-0 outline-none text-sm placeholder:text-[color:var(--nm-ink30)]"
          style={{ color: 'var(--nm-ink)' }}
          {...rest}
        />
        {trailing && <span className="shrink-0">{trailing}</span>}
      </div>
    );
  }
);
TextInput.displayName = 'TextInput';

// ---------------------------------------------------------------------------
// Textarea
// ---------------------------------------------------------------------------
export interface TextareaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  error?: boolean;
}

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(
  ({ error, className, ...rest }, ref) => {
    return (
      <textarea
        ref={ref}
        data-nm="textarea"
        data-error={error ? 'true' : 'false'}
        className={cn(
          'nx-textarea w-full px-3 py-2 rounded-[var(--radius-sm)] text-sm outline-none resize-y',
          'transition-[box-shadow,background-color] duration-150',
          'placeholder:text-[color:var(--nm-ink30)]',
          className
        )}
        style={{
          background: 'var(--nm-paper-warm)',
          color: 'var(--nm-ink)',
          boxShadow: error
            ? 'inset 0 -2px 0 0 var(--color-error)'
            : 'inset 0 0 0 1px var(--nm-hairline)',
          minHeight: 80,
        }}
        onFocus={(e) => {
          if (!error) {
            e.currentTarget.style.boxShadow = 'inset 0 -2px 0 0 var(--nm-ink)';
          }
          rest.onFocus?.(e);
        }}
        onBlur={(e) => {
          if (!error) {
            e.currentTarget.style.boxShadow = 'inset 0 0 0 1px var(--nm-hairline)';
          }
          rest.onBlur?.(e);
        }}
        {...rest}
      />
    );
  }
);
Textarea.displayName = 'Textarea';

// ---------------------------------------------------------------------------
// Select — native, NM-styled
// ---------------------------------------------------------------------------
export interface SelectOption {
  value: string;
  label: string;
}

export interface SelectProps extends Omit<SelectHTMLAttributes<HTMLSelectElement>, 'size'> {
  options: SelectOption[];
  placeholder?: string;
  error?: boolean;
}

export const Select = forwardRef<HTMLSelectElement, SelectProps>(
  ({ options, placeholder, error, className, ...rest }, ref) => {
    return (
      <div
        data-nm="select"
        data-error={error ? 'true' : 'false'}
        className={cn(
          'relative h-10 rounded-[var(--radius-sm)] flex items-center',
          className
        )}
        style={{
          background: 'var(--nm-paper-warm)',
          boxShadow: error ? 'inset 0 -2px 0 0 var(--color-error)' : 'inset 0 0 0 1px var(--nm-hairline)',
        }}
      >
        <select
          ref={ref}
          className="w-full h-full px-3 pr-8 bg-transparent border-0 outline-none text-sm appearance-none cursor-pointer"
          style={{ color: 'var(--nm-ink)' }}
          {...rest}
        >
          {placeholder && (
            <option value="" disabled>
              {placeholder}
            </option>
          )}
          {options.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
        <svg
          className="pointer-events-none absolute right-2.5"
          width="12"
          height="12"
          viewBox="0 0 12 12"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          style={{ color: 'var(--nm-ink50)' }}
          aria-hidden="true"
        >
          <path d="M3 4.5L6 7.5L9 4.5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </div>
    );
  }
);
Select.displayName = 'Select';

// ---------------------------------------------------------------------------
// Toggle — bracket-wrapped pill `[ ─● ]` / `[ ○─ ]`
// ---------------------------------------------------------------------------
export interface ToggleProps {
  checked: boolean;
  onChange: (next: boolean) => void;
  disabled?: boolean;
  label?: string;
  /** Optional aria-label for icon-only toggles */
  ariaLabel?: string;
  className?: string;
}

export function Toggle({
  checked,
  onChange,
  disabled,
  label,
  ariaLabel,
  className,
}: ToggleProps) {
  const labelId = useId();
  return (
    <label
      data-nm="toggle"
      data-checked={checked ? 'true' : 'false'}
      className={cn(
        'inline-flex items-center gap-2',
        disabled ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer',
        className
      )}
    >
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label={ariaLabel ?? label}
        aria-labelledby={label ? labelId : undefined}
        disabled={disabled}
        onClick={() => !disabled && onChange(!checked)}
        className="relative inline-flex items-center justify-between rounded-full transition-colors duration-150"
        style={{
          width: 44,
          height: 24,
          padding: 2,
          background: checked ? 'var(--nm-ink)' : 'var(--nm-paper-warm)',
          boxShadow: 'inset 0 0 0 1px var(--nm-hairline)',
        }}
      >
        <span
          className="block rounded-full transition-transform duration-150"
          style={{
            width: 18,
            height: 18,
            background: checked ? 'var(--nm-paper)' : 'var(--nm-ink50)',
            transform: checked ? 'translateX(20px)' : 'translateX(0)',
          }}
        />
      </button>
      {label && (
        <span id={labelId} className="text-sm" style={{ color: 'var(--nm-ink)' }}>
          {label}
        </span>
      )}
    </label>
  );
}

// ---------------------------------------------------------------------------
// Checkbox — `[ ]` / `[✓]` true-bracket
// ---------------------------------------------------------------------------
export interface CheckboxProps {
  checked: boolean;
  onChange: (next: boolean) => void;
  disabled?: boolean;
  label?: ReactNode;
  ariaLabel?: string;
  className?: string;
}

export function Checkbox({
  checked,
  onChange,
  disabled,
  label,
  ariaLabel,
  className,
}: CheckboxProps) {
  return (
    <label
      data-nm="checkbox"
      data-checked={checked ? 'true' : 'false'}
      className={cn(
        'inline-flex items-center gap-2',
        disabled ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer',
        className
      )}
    >
      <button
        type="button"
        role="checkbox"
        aria-checked={checked}
        aria-label={ariaLabel ?? (typeof label === 'string' ? label : undefined)}
        disabled={disabled}
        onClick={() => !disabled && onChange(!checked)}
        className="relative inline-flex items-center justify-center transition-colors duration-150"
        style={{
          width: 18,
          height: 18,
          borderRadius: 'var(--radius-xs)',
          border: '1.5px solid var(--nm-ink)',
          background: checked ? 'var(--nm-ink)' : 'transparent',
        }}
      >
        {checked && (
          <svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="var(--nm-paper)" strokeWidth="2" aria-hidden="true">
            <path d="M2.5 6L5 8.5L9.5 3.5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        )}
      </button>
      {label && <span className="text-sm" style={{ color: 'var(--nm-ink)' }}>{label}</span>}
    </label>
  );
}

// ---------------------------------------------------------------------------
// Radio — circle, dot when selected
// ---------------------------------------------------------------------------
export interface RadioProps {
  checked: boolean;
  onChange: () => void;
  disabled?: boolean;
  label?: ReactNode;
  name?: string;
  value?: string;
  ariaLabel?: string;
  className?: string;
}

export function Radio({
  checked,
  onChange,
  disabled,
  label,
  name,
  value,
  ariaLabel,
  className,
}: RadioProps) {
  return (
    <label
      data-nm="radio"
      data-checked={checked ? 'true' : 'false'}
      className={cn(
        'inline-flex items-center gap-2',
        disabled ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer',
        className
      )}
    >
      <button
        type="button"
        role="radio"
        aria-checked={checked}
        aria-label={ariaLabel ?? (typeof label === 'string' ? label : undefined)}
        name={name}
        value={value}
        disabled={disabled}
        onClick={() => !disabled && onChange()}
        className="relative inline-flex items-center justify-center"
        style={{
          width: 18,
          height: 18,
          borderRadius: '50%',
          border: '1.5px solid var(--nm-ink)',
          background: 'transparent',
        }}
      >
        {checked && (
          <span
            style={{
              width: 8,
              height: 8,
              borderRadius: '50%',
              background: 'var(--nm-ink)',
            }}
          />
        )}
      </button>
      {label && <span className="text-sm" style={{ color: 'var(--nm-ink)' }}>{label}</span>}
    </label>
  );
}

// ---------------------------------------------------------------------------
// Slider — thin ink rail + paper-raised handle
// ---------------------------------------------------------------------------
export interface SliderProps {
  value: number;
  onChange: (v: number) => void;
  min?: number;
  max?: number;
  step?: number;
  unit?: string;
  label?: string;
  className?: string;
  disabled?: boolean;
}

export function Slider({
  value,
  onChange,
  min = 0,
  max = 100,
  step = 1,
  unit,
  label,
  className,
  disabled,
}: SliderProps) {
  const labelId = useId();
  return (
    <div data-nm="slider" className={cn('w-full', className)}>
      {label && (
        <div className="flex items-baseline justify-between text-xs mb-2" style={{ color: 'var(--nm-ink70)' }}>
          <span id={labelId}>{label}</span>
          <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--nm-ink50)' }}>
            {value}
            {unit ?? ''}
          </span>
        </div>
      )}
      <input
        type="range"
        aria-labelledby={label ? labelId : undefined}
        min={min}
        max={max}
        step={step}
        value={value}
        disabled={disabled}
        onChange={(e: ChangeEvent<HTMLInputElement>) => onChange(Number(e.target.value))}
        className="w-full nm-slider"
        style={{
          accentColor: 'var(--nm-ink)',
        }}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// SearchInput — TextInput + leading bracket-search icon + esc to clear
// ---------------------------------------------------------------------------
export interface SearchInputProps {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  onClear?: () => void;
  className?: string;
}

export function SearchInput({
  value,
  onChange,
  placeholder = 'Search…',
  onClear,
  className,
}: SearchInputProps) {
  return (
    <TextInput
      data-nm-search="true"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      onKeyDown={(e) => {
        if (e.key === 'Escape' && value) {
          e.preventDefault();
          onChange('');
          onClear?.();
        }
      }}
      className={className}
      leading={
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="var(--nm-ink50)" strokeWidth="1.5" aria-hidden="true">
          <circle cx="6" cy="6" r="4" />
          <path d="M9 9 L12 12" strokeLinecap="round" />
        </svg>
      }
      trailing={
        value ? (
          <button
            type="button"
            onClick={() => {
              onChange('');
              onClear?.();
            }}
            aria-label="Clear search"
            className="opacity-60 hover:opacity-100 transition-opacity"
          >
            <svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
              <path d="M3 3 L9 9 M9 3 L3 9" strokeLinecap="round" />
            </svg>
          </button>
        ) : null
      }
    />
  );
}
