/**
 * api.updateJob — the frontend half of GitHub #86 (PUT /api/jobs/{job_id}
 * already existed backend-side with no frontend caller at all). Asserts the
 * exact request shape: method, URL, and body (agent_id + only the fields
 * the caller passed), since that shape is the contract the backend's
 * JobUpdateBody/JobUpdateFields expects.
 */
import { afterEach, describe, expect, test, vi } from 'vitest';
import { api, ApiError } from '../api';

afterEach(() => vi.restoreAllMocks());

describe('api.updateJob', () => {
  test('PUTs /api/jobs/{job_id} with agent_id plus only the changed fields', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ success: true, job_id: 'job_1', updated_fields: ['payload'] }),
    } as unknown as Response);

    const result = await api.updateJob('job_1', 'agent_1', { payload: 'new prompt text' });

    expect(result).toEqual({ success: true, job_id: 'job_1', updated_fields: ['payload'] });
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const [url, init] = fetchSpy.mock.calls[0];
    expect(String(url)).toContain('/api/jobs/job_1');
    expect(init?.method).toBe('PUT');
    expect(JSON.parse(init?.body as string)).toEqual({
      agent_id: 'agent_1',
      payload: 'new prompt text',
    });
    // Fields the caller did not pass (title, description) must not appear —
    // the backend treats a present key as "change this field", even if the
    // value happens to be undefined-coerced-to-omitted by JSON.stringify.
    expect(JSON.parse(init?.body as string)).not.toHaveProperty('title');
    expect(JSON.parse(init?.body as string)).not.toHaveProperty('description');
  });

  test('a non-2xx response throws ApiError with the backend detail', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: false,
      status: 404,
      statusText: 'Not Found',
      json: async () => ({ detail: 'job not found' }),
    } as unknown as Response);

    await expect(api.updateJob('missing_job', 'agent_1', { title: 'x' })).rejects.toThrow(ApiError);
    await expect(api.updateJob('missing_job', 'agent_1', { title: 'x' })).rejects.toThrow(
      /job not found/,
    );
  });
});
