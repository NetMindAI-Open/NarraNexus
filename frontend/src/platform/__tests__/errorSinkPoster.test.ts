/**
 * @file_name: errorSinkPoster.test.ts
 * @author: Bin Liang
 * @date: 2026-09-03
 * @description: Plugin-attributed errors are posted to the factory API (throttled per plugin); shell errors are not.
 */
import { afterEach, expect, it, vi } from 'vitest';

import { reportUiError, resetErrorSink, setErrorPoster } from '@/platform/errorSink';

afterEach(() => resetErrorSink());

it('posts plugin errors once per throttle window and never shell errors', async () => {
  const poster = vi.fn(async () => undefined);
  setErrorPoster(poster);
  reportUiError(new Error('boom'), { source: 'acme.weather', context: 'activate' });
  reportUiError(new Error('boom again'), { source: 'acme.weather' });
  reportUiError(new Error('shell'), { source: 'shell' });
  await Promise.resolve();
  expect(poster).toHaveBeenCalledTimes(1);
  expect(poster.mock.calls[0][0]).toBe('acme.weather');
  expect((poster.mock.calls[0][1] as { message: string }).message).toBe('activate: boom');
});
