/**
 * @file_name: ConfirmDialog.tsx
 * @description: Pure-React confirm/alert primitive + useConfirm hook.
 *
 * Tauri's wry webview does not render window.confirm / window.alert /
 * window.prompt, so any call to them resolves falsy and the surrounding
 * handler bails out silently. Every interactive confirmation in the app
 * goes through this hook instead so the DMG build behaves identically
 * to the browser dev server (rule #7).
 */

import { useCallback, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Dialog, DialogContent, DialogFooter } from './Dialog';
import { Button } from './Button';

export interface ConfirmOptions {
  title?: string;
  message: ReactNode;
  confirmText?: string;
  cancelText?: string;
  danger?: boolean;
}

export interface AlertOptions {
  title?: string;
  message: ReactNode;
  okText?: string;
  danger?: boolean;
}

interface DialogState {
  mode: 'confirm' | 'alert';
  title: string;
  message: ReactNode;
  confirmText: string;
  cancelText?: string;
  danger: boolean;
  resolve: (value: boolean) => void;
}

export function useConfirm() {
  const [state, setState] = useState<DialogState | null>(null);

  const close = useCallback((value: boolean) => {
    setState((prev) => {
      prev?.resolve(value);
      return null;
    });
  }, []);

  const confirm = useCallback(
    (opts: ConfirmOptions) =>
      new Promise<boolean>((resolve) => {
        setState({
          mode: 'confirm',
          title: opts.title ?? 'Confirm',
          message: opts.message,
          confirmText: opts.confirmText ?? 'Confirm',
          cancelText: opts.cancelText ?? 'Cancel',
          danger: opts.danger ?? false,
          resolve,
        });
      }),
    []
  );

  const alert = useCallback(
    (opts: AlertOptions) =>
      new Promise<void>((resolve) => {
        setState({
          mode: 'alert',
          title: opts.title ?? 'Notice',
          message: opts.message,
          confirmText: opts.okText ?? 'OK',
          cancelText: undefined,
          danger: opts.danger ?? false,
          resolve: () => resolve(),
        });
      }),
    []
  );

  const dialog = state ? (
    <Dialog isOpen onClose={() => close(false)} title={state.title} size="md">
      <DialogContent>
        <div className="text-sm text-[var(--text-secondary)] whitespace-pre-wrap">
          {state.message}
        </div>
      </DialogContent>
      <DialogFooter>
        {state.mode === 'confirm' && (
          <Button variant="ghost" onClick={() => close(false)}>
            {state.cancelText}
          </Button>
        )}
        <Button
          variant={state.danger ? 'danger' : 'accent'}
          onClick={() => close(true)}
        >
          {state.confirmText}
        </Button>
      </DialogFooter>
    </Dialog>
  ) : null;

  return { confirm, alert, dialog };
}

/**
 * `useConfirm` with the notice chrome already filled in.
 *
 * Every notice needs the same three things — a title, an OK label, and whether
 * it is a failure — and only the MESSAGE differs per call site. Spelling that
 * chrome out at each site (as the first pass of the native-alert sweep did) meant
 * nine identical copies across six files: one copy-editing pass on the title
 * would have to find all nine, and missing one is how wording drifts.
 *
 * It also fixes an i18n hole in one place instead of nine. `useConfirm`'s own
 * defaults ('Notice', 'OK', 'Confirm') are hardcoded English and bypass i18n, so
 * a caller that omits them hands non-English users English chrome. Rather than
 * change that 20-plus-caller primitive, the fallbacks live here — once.
 *
 * The three verbs are separate because the TITLE is not interchangeable: telling
 * someone "Just a moment" above "Saved to ~/Downloads/report.pdf" reads as "still
 * working" in every locale we ship.
 *
 * `confirm` is re-exposed so a component needing both a question and a notice
 * holds ONE instance and mounts ONE `dialog`.
 */
export function useNotice() {
  const { t } = useTranslation();
  const { confirm, alert, dialog } = useConfirm();

  /** Something the user tried did not happen. */
  const notifyError = useCallback(
    (message: ReactNode) =>
      alert({
        title: t('common.actionFailedTitle', 'That didn’t work'),
        message,
        okText: t('common.ok', 'OK'),
        danger: true,
      }),
    [alert, t],
  );

  /** Something finished — e.g. a file landed on disk. */
  const notifyDone = useCallback(
    (message: ReactNode) =>
      alert({
        title: t('common.doneTitle', 'Done'),
        message,
        okText: t('common.ok', 'OK'),
      }),
    [alert, t],
  );

  /** Not ready yet; the same action will work shortly. */
  const notifyPending = useCallback(
    (message: ReactNode) =>
      alert({
        title: t('common.noticeTitle', 'Just a moment'),
        message,
        okText: t('common.ok', 'OK'),
      }),
    [alert, t],
  );

  return { confirm, notifyError, notifyDone, notifyPending, dialog };
}
