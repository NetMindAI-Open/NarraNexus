/**
 * Restrict Desktop-triggered external links to a small allowlist.
 */

import { shell } from 'electron'

const ALLOWED_HOSTS = [
  'localhost',
  '127.0.0.1',
  'github.com',
  'www.github.com',
  'www.docker.com',
  'docker.com',
  'docs.astral.sh',
  'nodejs.org',
  'www.nodejs.org',
  'platform.openai.com',
  'aistudio.google.com',
  'www.netmind.ai',
  'netmind.ai',
  'console.anthropic.com',
  'openrouter.ai',
]

function isAllowedHost(hostname: string): boolean {
  return ALLOWED_HOSTS.some((allowed) => (
    hostname === allowed || hostname.endsWith(`.${allowed}`)
  ))
}

export function isAllowedExternalUrl(rawUrl: string): boolean {
  try {
    const url = new URL(rawUrl)
    if (!['http:', 'https:'].includes(url.protocol)) return false
    if (url.username || url.password) return false
    return isAllowedHost(url.hostname)
  } catch {
    return false
  }
}

export function tryOpenExternalUrl(rawUrl: string): void {
  if (!isAllowedExternalUrl(rawUrl)) {
    console.warn(`[external-links] blocked external URL: ${rawUrl}`)
    return
  }
  void shell.openExternal(rawUrl)
}
