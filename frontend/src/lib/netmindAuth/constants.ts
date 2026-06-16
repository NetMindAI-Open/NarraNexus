import { getNetmindConfig } from '@/lib/runtimeConfig';

/** Common params NetMind's auth API expects on every request. */
export function baseRequestParams(): Record<string, string | number> {
  return {
    deviceId: 123231,
    clientType: 5,
    clientVersion: '1.0.0',
    sysCode: getNetmindConfig().sysCode,
  };
}
