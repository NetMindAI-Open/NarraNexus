import { describe, test, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import {
  TabBar,
  SidebarNavItem,
  Breadcrumb,
  StepIndicator,
  BottomNavBar,
} from '../nav';

describe('TabBar', () => {
  test('renders tabs + marks active', () => {
    render(
      <TabBar
        tabs={[
          { key: 'a', label: 'All' },
          { key: 'b', label: 'Active' },
        ]}
        active="b"
        onChange={() => {}}
      />
    );
    expect(screen.getByRole('tab', { name: 'All' })).toHaveAttribute('aria-selected', 'false');
    expect(screen.getByRole('tab', { name: 'Active' })).toHaveAttribute('aria-selected', 'true');
  });

  test('onChange called when clicking tab', () => {
    const onChange = vi.fn();
    render(
      <TabBar
        tabs={[{ key: 'a', label: 'X' }]}
        active=""
        onChange={onChange}
      />
    );
    fireEvent.click(screen.getByRole('tab', { name: 'X' }));
    expect(onChange).toHaveBeenCalledWith('a');
  });

  test('count badge renders', () => {
    render(
      <TabBar
        tabs={[{ key: 'a', label: 'Inbox', count: 12 }]}
        active="a"
        onChange={() => {}}
      />
    );
    expect(screen.getByText('12')).toBeInTheDocument();
  });
});

describe('SidebarNavItem', () => {
  test('active state sets aria-current=page', () => {
    render(<SidebarNavItem active>Dashboard</SidebarNavItem>);
    expect(screen.getByRole('button', { name: 'Dashboard' })).toHaveAttribute('aria-current', 'page');
  });

  test('inactive state has no aria-current', () => {
    render(<SidebarNavItem>Dashboard</SidebarNavItem>);
    expect(screen.getByRole('button', { name: 'Dashboard' })).not.toHaveAttribute('aria-current');
  });
});

describe('Breadcrumb', () => {
  test('renders items separated by / + last is aria-current', () => {
    render(
      <Breadcrumb
        items={[
          { label: 'Home', href: '/' },
          { label: 'Agents', href: '/agents' },
          { label: 'Yara' },
        ]}
      />
    );
    expect(screen.getByText('Yara')).toHaveAttribute('aria-current', 'page');
    expect(document.querySelectorAll('nav[data-nm="breadcrumb"] > *').length).toBeGreaterThan(0);
  });
});

describe('StepIndicator', () => {
  test('renders steps + marks current as aria-current=step', () => {
    render(
      <StepIndicator
        steps={[
          { key: 'a', label: 'Welcome' },
          { key: 'b', label: 'Identity' },
          { key: 'c', label: 'Done' },
        ]}
        currentIndex={1}
      />
    );
    const items = document.querySelectorAll('[data-state]');
    expect(items[0]).toHaveAttribute('data-state', 'done');
    expect(items[1]).toHaveAttribute('data-state', 'current');
    expect(items[2]).toHaveAttribute('data-state', 'pending');
  });
});

describe('BottomNavBar', () => {
  test('renders tabs and emits onChange', () => {
    const onChange = vi.fn();
    render(
      <BottomNavBar
        tabs={[
          { key: 'chats', label: 'Chats', icon: <span>💬</span> },
          { key: 'me', label: 'Me', icon: <span>👤</span> },
        ]}
        active="chats"
        onChange={onChange}
      />
    );
    fireEvent.click(screen.getByRole('tab', { name: /Me/ }));
    expect(onChange).toHaveBeenCalledWith('me');
  });
});
