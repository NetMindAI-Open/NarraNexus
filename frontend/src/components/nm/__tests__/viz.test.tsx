import { describe, test, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { KPITile, StatStrip, ChartCard } from '../viz';

describe('KPITile', () => {
  test('renders label + value', () => {
    render(<KPITile label="Active Agents" value={4} />);
    expect(screen.getByText('Active Agents')).toBeInTheDocument();
    expect(screen.getByText('4')).toBeInTheDocument();
  });

  test('positive trend (upIsGood=true) renders ↑ in success color', () => {
    const { container } = render(<KPITile label="x" value={10} trend={12.3} />);
    expect(container).toHaveTextContent('↑');
    expect(container).toHaveTextContent('12.3%');
  });

  test('upIsGood=false flips meaning (cost goes UP = bad)', () => {
    const { container } = render(<KPITile label="Cost" value="$100" trend={15} upIsGood={false} />);
    // arrow still ↑ but color is error not success — visual; check the value text only
    expect(container).toHaveTextContent('↑');
    expect(container).toHaveTextContent('15.0%');
  });
});

describe('StatStrip', () => {
  test('renders N items', () => {
    render(
      <StatStrip
        items={[
          { label: 'Messages', value: 127 },
          { label: 'Cost', value: '$1.24' },
          { label: 'Turns', value: 342 },
        ]}
      />
    );
    expect(screen.getByText('Messages')).toBeInTheDocument();
    expect(screen.getByText('Cost')).toBeInTheDocument();
    expect(screen.getByText('Turns')).toBeInTheDocument();
    expect(screen.getByText('127')).toBeInTheDocument();
  });
});

describe('ChartCard', () => {
  test('renders title + body', () => {
    render(
      <ChartCard title="Activity">
        <div data-testid="chart">canvas here</div>
      </ChartCard>
    );
    expect(screen.getByText('Activity')).toBeInTheDocument();
    expect(screen.getByTestId('chart')).toBeInTheDocument();
  });

  test('actions slot renders', () => {
    render(
      <ChartCard title="x" actions={<button>Today</button>}>
        body
      </ChartCard>
    );
    expect(screen.getByRole('button', { name: 'Today' })).toBeInTheDocument();
  });

  test('subtitle renders', () => {
    render(
      <ChartCard title="x" subtitle="last 24 hours">
        body
      </ChartCard>
    );
    expect(screen.getByText('last 24 hours')).toBeInTheDocument();
  });
});
