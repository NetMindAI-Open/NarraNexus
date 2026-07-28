/**
 * @file_name: nav.tsx
 * @author: NM Design System Phase 1 (M2)
 * @date: 2026-05-18
 * @description: Navigation primitives — TabBar, SidebarNavItem, Breadcrumb,
 * StepIndicator, BottomNavBar.
 *
 */

import { Fragment, type ReactNode, type ButtonHTMLAttributes } from 'react';
import { cn } from '@/lib/utils';

// ---------------------------------------------------------------------------
// TabBar — underline-only, ink (NOT carbon)
// ---------------------------------------------------------------------------
export interface TabItem {
  key: string;
  label: ReactNode;
  /** Optional count badge */
  count?: number;
  disabled?: boolean;
}

export interface TabBarProps {
  tabs: TabItem[];
  active: string;
  onChange: (key: string) => void;
  className?: string;
}

export function TabBar({ tabs, active, onChange, className }: TabBarProps) {
  return (
    <div
      data-nm="tab-bar"
      role="tablist"
      className={cn(
        'flex items-center border-b',
        className
      )}
      style={{ borderColor: 'var(--nm-hairline)' }}
    >
      {tabs.map((t) => {
        const isActive = t.key === active;
        return (
          <button
            key={t.key}
            type="button"
            role="tab"
            aria-selected={isActive}
            disabled={t.disabled}
            onClick={() => !t.disabled && onChange(t.key)}
            data-active={isActive ? 'true' : 'false'}
            className={cn(
              'relative px-4 py-2.5 text-xs font-medium uppercase tracking-[0.10em]',
              'transition-colors duration-150',
              t.disabled ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer hover:text-[color:var(--nm-ink)]'
            )}
            style={{
              fontFamily: 'var(--font-mono)',
              color: isActive ? 'var(--nm-ink)' : 'var(--nm-ink50)',
            }}
          >
            {t.label}
            {typeof t.count === 'number' && (
              <span
                className="ml-1.5 inline-block"
                style={{ color: 'var(--nm-ink30)', fontFamily: 'var(--font-mono)' }}
              >
                {t.count}
              </span>
            )}
            <span
              aria-hidden="true"
              className="absolute left-0 right-0 bottom-[-1px] transition-[width] duration-150"
              style={{
                height: 2,
                width: isActive ? '100%' : 0,
                background: 'var(--nm-ink)',
              }}
            />
          </button>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// SidebarNavItem — left-edge accent for active
// ---------------------------------------------------------------------------
export interface SidebarNavItemProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  active?: boolean;
  icon?: ReactNode;
  children: ReactNode;
}

export function SidebarNavItem({
  active,
  icon,
  children,
  className,
  ...rest
}: SidebarNavItemProps) {
  return (
    <button
      type="button"
      data-nm="sidebar-nav-item"
      data-active={active ? 'true' : 'false'}
      className={cn(
        'relative w-full flex items-center gap-3 px-4 py-2.5 text-sm transition-colors duration-150',
        active ? 'font-semibold' : 'font-normal',
        'hover:bg-[color:var(--nm-paper-warm)]',
        'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-[color:var(--nm-ink)]',
        className
      )}
      style={{
        color: active ? 'var(--nm-ink)' : 'var(--nm-ink70)',
        background: active ? 'var(--nm-paper-warm)' : 'transparent',
      }}
      aria-current={active ? 'page' : undefined}
      {...rest}
    >
      {active && (
        <span
          aria-hidden="true"
          className="absolute left-0 top-0 bottom-0"
          style={{ width: 2, background: 'var(--nm-ink)' }}
        />
      )}
      {icon && <span className="shrink-0 inline-flex">{icon}</span>}
      <span className="truncate flex-1 text-left">{children}</span>
    </button>
  );
}

// ---------------------------------------------------------------------------
// Breadcrumb — `/` mono separators
// ---------------------------------------------------------------------------
export interface BreadcrumbItem {
  label: ReactNode;
  href?: string;
  onClick?: () => void;
}

export interface BreadcrumbProps {
  items: BreadcrumbItem[];
  className?: string;
}

export function Breadcrumb({ items, className }: BreadcrumbProps) {
  return (
    <nav
      data-nm="breadcrumb"
      aria-label="Breadcrumb"
      className={cn('flex items-center text-xs', className)}
      style={{ fontFamily: 'var(--font-mono)', color: 'var(--nm-ink50)' }}
    >
      {items.map((it, i) => {
        const isLast = i === items.length - 1;
        const node = it.href || it.onClick ? (
          <a
            href={it.href}
            onClick={(e) => {
              if (it.onClick) {
                e.preventDefault();
                it.onClick();
              }
            }}
            className="hover:text-[color:var(--nm-ink)] transition-colors"
            aria-current={isLast ? 'page' : undefined}
            style={{ color: isLast ? 'var(--nm-ink)' : undefined }}
          >
            {it.label}
          </a>
        ) : (
          <span style={{ color: isLast ? 'var(--nm-ink)' : undefined }} aria-current={isLast ? 'page' : undefined}>
            {it.label}
          </span>
        );
        return (
          <Fragment key={i}>
            {node}
            {!isLast && <span className="mx-2 opacity-50">/</span>}
          </Fragment>
        );
      })}
    </nav>
  );
}

// ---------------------------------------------------------------------------
// StepIndicator — `[1]──[2]──[3]`
// ---------------------------------------------------------------------------
export interface StepIndicatorStep {
  key: string;
  label?: ReactNode;
}

export interface StepIndicatorProps {
  steps: StepIndicatorStep[];
  /** Index of the current (active) step */
  currentIndex: number;
  className?: string;
}

export function StepIndicator({
  steps,
  currentIndex,
  className,
}: StepIndicatorProps) {
  return (
    <ol
      data-nm="step-indicator"
      className={cn('flex items-center w-full', className)}
    >
      {steps.map((s, i) => {
        const isCurrent = i === currentIndex;
        const isDone = i < currentIndex;
        const color = isCurrent
          ? 'var(--nm-ink)'
          : isDone
          ? 'var(--nm-ink50)'
          : 'var(--nm-ink30)';
        return (
          <Fragment key={s.key}>
            <li
              className="flex flex-col items-center"
              aria-current={isCurrent ? 'step' : undefined}
              data-state={isCurrent ? 'current' : isDone ? 'done' : 'pending'}
            >
              <span
                className="inline-flex items-center justify-center"
                style={{
                  width: 28,
                  height: 28,
                  border: `1.5px solid ${color}`,
                  borderRadius: 'var(--radius-xs)',
                  color,
                  background: isCurrent ? 'var(--nm-ink)' : 'transparent',
                  fontFamily: 'var(--font-mono)',
                  fontSize: 12,
                  fontWeight: 600,
                }}
              >
                <span style={{ color: isCurrent ? 'var(--nm-paper)' : color }}>{i + 1}</span>
              </span>
              {s.label && (
                <span
                  className="mt-1 text-xs"
                  style={{ color, fontFamily: 'var(--font-mono)' }}
                >
                  {s.label}
                </span>
              )}
            </li>
            {i < steps.length - 1 && (
              <span
                aria-hidden="true"
                className="flex-1 mx-2"
                style={{
                  height: 1.5,
                  background: i < currentIndex ? 'var(--nm-ink50)' : 'var(--nm-ink30)',
                }}
              />
            )}
          </Fragment>
        );
      })}
    </ol>
  );
}

// ---------------------------------------------------------------------------
// BottomNavBar — mobile 5-tab bottom nav
// ---------------------------------------------------------------------------
export interface BottomNavTab {
  key: string;
  label: string;
  icon: ReactNode;
}

export interface BottomNavBarProps {
  tabs: BottomNavTab[];
  active: string;
  onChange: (key: string) => void;
  className?: string;
}

export function BottomNavBar({
  tabs,
  active,
  onChange,
  className,
}: BottomNavBarProps) {
  return (
    <nav
      data-nm="bottom-nav-bar"
      role="tablist"
      className={cn(
        'flex items-stretch w-full',
        className
      )}
      style={{
        background: 'var(--nm-card)',
        borderTop: '1px solid var(--nm-hairline)',
      }}
    >
      {tabs.map((t) => {
        const isActive = t.key === active;
        return (
          <button
            key={t.key}
            type="button"
            role="tab"
            aria-selected={isActive}
            onClick={() => onChange(t.key)}
            className="flex-1 flex flex-col items-center justify-center gap-1 py-2 transition-colors duration-150"
            style={{
              color: isActive ? 'var(--nm-ink)' : 'var(--nm-ink50)',
              minHeight: 56,
            }}
          >
            <span className="inline-flex" aria-hidden="true">
              {t.icon}
            </span>
            <span className="text-[10px] uppercase tracking-wider" style={{ fontFamily: 'var(--font-mono)' }}>
              {t.label}
            </span>
          </button>
        );
      })}
    </nav>
  );
}
