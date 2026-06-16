/**
 * @file_name: request.ts
 * @description: Minimal fetch wrapper for NetMind's auth API. Serializes
 * the body as application/x-www-form-urlencoded, attaches the `token`
 * header (NetMind convention, NOT Authorization) when present, and unwraps
 * the {success,data,msg} envelope — rejecting on success:false.
 */
import { getNetmindConfig } from '@/lib/runtimeConfig';

function encodeForm(data: Record<string, unknown>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(data)) {
    if (v !== undefined && v !== null) p.append(k, String(v));
  }
  return p.toString();
}

/** POST to NetMind auth API; returns the unwrapped `data` payload. */
export async function netmindPost<T = unknown>(
  path: string,
  body: Record<string, unknown>,
  token?: string,
): Promise<T> {
  const { authApi } = getNetmindConfig();
  const headers: Record<string, string> = {
    'Content-Type': 'application/x-www-form-urlencoded',
  };
  if (token) headers['token'] = `Bearer ${token}`;
  const resp = await fetch(`${authApi}${path}`, {
    method: 'POST',
    headers,
    body: encodeForm(body),
  });
  const json = (await resp.json()) as { success?: boolean; data?: T; msg?: string };
  if (json?.success === false) {
    throw new Error(json.msg || 'NetMind request failed');
  }
  return json.data as T;
}
