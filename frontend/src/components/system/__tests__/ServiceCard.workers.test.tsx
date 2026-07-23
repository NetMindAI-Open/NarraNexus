/**
 * @file ServiceCard.workers.test.tsx
 * @description Guards the 2026-07-22 expandable per-worker detail on the
 * consolidated `workers` card: no worker UI without the prop; a flap warning
 * (any sub-worker restarting / restartCount>0) surfaces even while the process
 * dot reads "running"; expanding lists each sub-worker. i18n is mocked to
 * return keys so assertions don't depend on copy.
 */
import { render, screen, fireEvent } from '@testing-library/react';
import { expect, test, vi } from 'vitest';
import { ServiceCard } from '../ServiceCard';
import type { WorkerLiveness } from '@/types/platform';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, o?: Record<string, unknown>) =>
      o && 'count' in o ? `${k}:${o.count}` : k,
  }),
}));

const healthy: WorkerLiveness[] = [
  { name: 'poller', state: 'running', restartCount: 0, lastError: null },
  { name: 'channels', state: 'running', restartCount: 0, lastError: null },
];

const flapping: WorkerLiveness[] = [
  { name: 'poller', state: 'running', restartCount: 0, lastError: null },
  { name: 'bus', state: 'restarting', restartCount: 3, lastError: 'boom' },
];

test('no worker UI when the workers prop is absent', () => {
  render(<ServiceCard label="MCP Server" status="running" port={null} lastError={null} />);
  expect(screen.queryByTitle('system.serviceCard.workersFlapping')).toBeNull();
  expect(screen.queryByText('system.serviceCard.workerCount:2')).toBeNull();
});

test('healthy workers: toggle present, no flap warning', () => {
  render(
    <ServiceCard label="Workers" status="running" port={null} lastError={null} workers={healthy} />,
  );
  expect(screen.getByText('system.serviceCard.workerCount:2')).toBeTruthy();
  expect(screen.queryByTitle('system.serviceCard.workersFlapping')).toBeNull();
});

test('a restarting sub-worker raises a flap warning even while process runs', () => {
  render(
    <ServiceCard label="Workers" status="running" port={null} lastError={null} workers={flapping} />,
  );
  expect(screen.getByTitle('system.serviceCard.workersFlapping')).toBeTruthy();
});

test('a historical restart (running, restartCount>0) does NOT flap the header', () => {
  const historical: WorkerLiveness[] = [
    { name: 'poller', state: 'running', restartCount: 5, lastError: null },
  ];
  render(
    <ServiceCard label="Workers" status="running" port={null} lastError={null} workers={historical} />,
  );
  // Warning triangle reflects only a CURRENT restart, not an old one.
  expect(screen.queryByTitle('system.serviceCard.workersFlapping')).toBeNull();
});

test('a stale snapshot suppresses the flap warning and shows a stale label', () => {
  render(
    <ServiceCard
      label="Workers"
      status="running"
      port={null}
      lastError={null}
      workers={flapping}
      workerHeartbeatAgeSeconds={600}
    />,
  );
  // Even though a worker is "restarting", the snapshot is 10m old → don't trust
  // it: no flap warning, show the stale marker instead.
  expect(screen.queryByTitle('system.serviceCard.workersFlapping')).toBeNull();
  expect(screen.getByText('system.serviceCard.workersStale')).toBeTruthy();
});

test('expanding lists each sub-worker with its restart count', () => {
  render(
    <ServiceCard label="Workers" status="running" port={null} lastError={null} workers={flapping} />,
  );
  // Collapsed by default.
  expect(screen.queryByText('bus')).toBeNull();
  fireEvent.click(screen.getByText('system.serviceCard.workerCount:2'));
  expect(screen.getByText('poller')).toBeTruthy();
  expect(screen.getByText('bus')).toBeTruthy();
  // restartCount>0 badge only on the flapping worker.
  expect(screen.getByText('system.serviceCard.restarts:3')).toBeTruthy();
});
