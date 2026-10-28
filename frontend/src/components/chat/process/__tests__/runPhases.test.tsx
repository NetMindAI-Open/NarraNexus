/**
 * RunPhases — the run's preamble, at the head of the in-flight document.
 *
 * It carries the one thing the document flow cannot: the backend pipeline
 * steps that run BEFORE the model produces anything (narrative selection,
 * module load, context build). Without it the window between "send" and the
 * first narration is blank.
 *
 * What it must NOT be is a panel. The framed terminal box this replaced put
 * a second register on the same screen as the frameless turn — the exact
 * thing the document-flow pass removed everywhere else.
 */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { RunPhases } from '../RunPhases';
import type { TurnEvent, Step } from '@/types';

function step(id: string, status: Step['status'] = 'running'): Step {
  return {
    id: `s-${id}`, step: id, title: `Step ${id}`, description: '',
    status, substeps: [], timestamp: 0,
  };
}

const events: TurnEvent[] = [
  { id: 't1', ts: 1, type: 'thinking', content: 'Reading the requirement' },
  { id: 'c1', ts: 2, type: 'tool_call', tool_name: 'glob', tool_input: { pattern: '**/*.md' } },
  { id: 'r1', ts: 3, type: 'reply', content: 'This belongs to the reply, not here' },
];

describe('RunPhases', () => {
  it('takes no surface: no frame, no fill, no shadow', () => {
    // The whole point of dissolving the panel. A border/background/shadow
    // here re-creates the box that made the agent's turn read as two
    // different documents on one screen.
    const { container } = render(<RunPhases events={[]} steps={[step('3')]} />);
    const root = screen.getByTestId('run-phases');
    const cls = root.className;
    expect(cls).not.toMatch(/\bborder\b|\bshadow\b|rounded-\[var\(--radius/);
    expect(cls).not.toMatch(/nm-paper/);
    expect(container.querySelector('[class*="nm-paper"]')).toBeNull();
  });

  it('names the phases by what the backend actually does', () => {
    render(
      <RunPhases
        events={[]}
        steps={[
          step('1', 'completed'), step('2', 'completed'),
          step('2.5', 'completed'), step('3'), step('3.4'),
        ]}
      />,
    );
    expect(screen.getByText(/Selecting narrative/)).toBeInTheDocument();
    expect(screen.getByText(/Loading modules/)).toBeInTheDocument();
    expect(screen.getByText(/Syncing instances/)).toBeInTheDocument();
    expect(screen.getByText(/Building context/)).toBeInTheDocument();
    expect(screen.getByText(/Running agent/)).toBeInTheDocument();
    expect(screen.queryByText(/Loading context/)).toBeNull();
    expect(screen.queryByText(/Loading resources/)).toBeNull();
  });

  it('only whitelisted phases render — sub-steps and housekeeping stay out', () => {
    // Raw English backend titles leaking into the UI is the bug this guards.
    render(
      <RunPhases
        events={events}
        steps={[
          step('3'), step('3.4'), step('3.4.1'),
          step('3.5', 'completed'), step('4'), step('5'),
        ]}
      />,
    );
    expect(screen.queryByText('Step 3.4.1')).toBeNull();
    expect(screen.queryByText('Step 3.5')).toBeNull();
    expect(screen.queryByText('Step 4')).toBeNull();
    expect(screen.queryByText('Step 5')).toBeNull();
    expect(screen.getByText(/Running agent/)).toBeInTheDocument();
  });

  it('shows the starting state before any data arrives', () => {
    // This is the blank window the preamble exists for.
    render(<RunPhases events={[]} steps={[]} />);
    expect(screen.getByText(/Starting up/)).toBeInTheDocument();
  });

  it('never draws the turn content — that is the document below it', () => {
    // Anti-duplication: narration, tool lines and the reply render in the
    // flow through TurnTimeline. Repeating any of them here paints twice.
    render(<RunPhases events={events} steps={[step('3.4')]} />);
    expect(screen.queryByText(/Reading the requirement/)).toBeNull();
    expect(screen.queryByTestId('tool-row-c1')).toBeNull();
    expect(screen.queryByText(/belongs to the reply/)).toBeNull();
  });

  it('keeps the ops count and the elapsed timer the panel used to show', () => {
    // Iron rule #16: dissolving the frame may not cost the user a datum.
    render(<RunPhases events={events} steps={[step('3.4')]} />);
    const meta = screen.getByTestId('run-phases-meta');
    expect(meta).toHaveTextContent('1');
    expect(meta.textContent).toMatch(/\d+:\d{2}/);
  });
});
