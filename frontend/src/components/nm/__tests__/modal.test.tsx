import { describe, test, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { Dialog, ConfirmDialog, Drawer, Sheet } from '../modal';

describe('Dialog', () => {
  test('open=false renders nothing', () => {
    const { container } = render(
      <Dialog open={false} title="Hello">
        body
      </Dialog>
    );
    expect(container.querySelector('[data-nm="dialog"]')).toBeNull();
  });

  test('open=true renders backdrop + dialog with title', () => {
    render(
      <Dialog open title="Hello" onClose={() => {}}>
        body
      </Dialog>
    );
    expect(screen.getByRole('dialog', { name: 'Hello' })).toBeInTheDocument();
    expect(document.querySelector('[data-nm="modal-backdrop"]')).toBeInTheDocument();
  });

  test('close button fires onClose', () => {
    const onClose = vi.fn();
    render(
      <Dialog open onClose={onClose} title="X">
        body
      </Dialog>
    );
    fireEvent.click(screen.getByRole('button', { name: 'Close dialog' }));
    expect(onClose).toHaveBeenCalledOnce();
  });

  test('Escape key fires onClose', () => {
    const onClose = vi.fn();
    render(
      <Dialog open onClose={onClose} title="X">
        body
      </Dialog>
    );
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledOnce();
  });

  test('footer slot renders', () => {
    render(
      <Dialog open title="X" onClose={() => {}} footer={<button>OK</button>}>
        body
      </Dialog>
    );
    expect(screen.getByRole('button', { name: 'OK' })).toBeInTheDocument();
  });
});

describe('ConfirmDialog', () => {
  test('renders confirm + cancel; clicks fire respective handlers', () => {
    const onConfirm = vi.fn();
    const onCancel = vi.fn();
    render(
      <ConfirmDialog
        open
        title="Reset memories?"
        message="This cannot be undone."
        confirmLabel="Reset"
        destructive
        onConfirm={onConfirm}
        onCancel={onCancel}
      />
    );
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(onCancel).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole('button', { name: 'Reset' }));
    expect(onConfirm).toHaveBeenCalledOnce();
    // destructive variant uses danger button
    const resetBtn = screen.getByRole('button', { name: 'Reset' });
    expect(resetBtn).toHaveAttribute('data-variant', 'danger');
  });
});

describe('Drawer', () => {
  test('right side default + renders title', () => {
    render(
      <Drawer open onClose={() => {}} title="Context">
        body
      </Drawer>
    );
    expect(screen.getByRole('dialog', { name: 'Context' })).toHaveAttribute('data-side', 'right');
  });

  test('left side honored', () => {
    render(
      <Drawer open onClose={() => {}} side="left" title="x">
        body
      </Drawer>
    );
    expect(screen.getByRole('dialog')).toHaveAttribute('data-side', 'left');
  });
});

describe('Sheet', () => {
  test('renders title + body when open', () => {
    render(
      <Sheet open onClose={() => {}} title="Filter">
        <div>options</div>
      </Sheet>
    );
    expect(screen.getByRole('dialog', { name: 'Filter' })).toBeInTheDocument();
    expect(screen.getByText('options')).toBeInTheDocument();
  });

  test('open=false renders nothing', () => {
    const { container } = render(<Sheet open={false}>x</Sheet>);
    expect(container.querySelector('[data-nm="sheet"]')).toBeNull();
  });
});
