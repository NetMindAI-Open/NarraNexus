/**
 * @file_name: request.ts
 * @description: Minimal fetch wrapper for NetMind's auth API. Serializes
 * the body as application/x-www-form-urlencoded, attaches the `token`
 * header (NetMind convention, NOT Authorization) when present, and unwraps
 * the {success,data,msg} envelope — rejecting on success:false.
 */
import { getNetmindConfig } from '@/lib/runtimeConfig';

/**
 * The upstream deliberately REJECTED the request (envelope `success:false`) —
 * as opposed to a transport failure (offline / DNS / a 502 HTML gateway page
 * that isn't even JSON). Callers use this to tell "your credentials were
 * refused" apart from "the service is unreachable", so a NetMind outage doesn't
 * get shown to every user as "wrong password".
 */
export class NetmindApiError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'NetmindApiError';
  }
}

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
    // A real rejection from the upstream (bad credentials, etc.) — mark it so
    // callers can mask it without also masking transport failures above.
    throw new NetmindApiError(json.msg || 'NetMind request failed');
  }
  return json.data as T;
}
