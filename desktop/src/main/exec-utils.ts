/**
 * @file exec-utils.ts
 * @description Shared process execution utilities for desktop app
 *
 * Extracted from installer-registry.ts and service-launcher.ts to eliminate
 * duplicated getExecEnv / execInProject / execWithPrivileges / spawnWithOutput.
 */

import { spawn, execFile } from 'child_process'
import { promisify } from 'util'
import * as net from 'net'
import { PROJECT_ROOT } from './constants'
import { getShellEnv } from './shell-env'
import { readEnv } from './env-manager'
import { getDockerConfigOverride, getCachedDockerConfigDir } from './docker-manager'

const execFileAsync = promisify(execFile)

// ─── Environment ─────────────────────────────────────

/**
 * Build a merged environment for child processes:
 * shell env + .env overrides + NO_PROXY + Docker Desktop PATH (macOS) + DOCKER_CONFIG
 */
export function getExecEnv(): Record<string, string> {
  const shellEnv = getShellEnv()
  const dotEnv = readEnv()
  const nonEmptyDotEnv: Record<string, string> = {}
  for (const [key, value] of Object.entries(dotEnv)) {
    if (value.trim()) nonEmptyDotEnv[key] = value
  }
  const noProxyHosts = 'localhost,127.0.0.1'
  const merged = { ...shellEnv, ...nonEmptyDotEnv, NO_PROXY: noProxyHosts, no_proxy: noProxyHosts }

  // On macOS, ensure Docker Desktop bin is first in PATH — on Intel Mac, Homebrew's
  // /usr/local/bin/docker is a CLI-only binary without compose plugin.
  if (process.platform === 'darwin') {
    const ddBin = '/Applications/Docker.app/Contents/Resources/bin'
    const currentPath = merged.PATH || ''
    if (!currentPath.startsWith(ddBin)) {
      const parts = currentPath.split(':').filter(p => p !== ddBin)
      merged.PATH = [ddBin, ...parts].join(':')
    }
  }

  // Use temp config dir if credential helper is unavailable
  const dockerConfigDir = getCachedDockerConfigDir()
  if (dockerConfigDir) {
    merged.DOCKER_CONFIG = dockerConfigDir
  }

  return merged
}

// ─── Exec helpers ────────────────────────────────────

export async function execInProject(
  cmd: string,
  args: string[],
  options?: { cwd?: string; timeout?: number }
): Promise<{ stdout: string; stderr: string }> {
  return execFileAsync(cmd, args, {
    cwd: options?.cwd ?? PROJECT_ROOT,
    timeout: options?.timeout ?? 120000,
    env: getExecEnv()
  })
}

/**
 * Execute sudo-required commands via system native privilege elevation dialog
 * (macOS: osascript, Linux: pkexec).
 * Includes Docker config override in privileged shell on macOS.
 */
export async function execWithPrivileges(
  script: string,
  options?: { timeout?: number; logPrefix?: string }
): Promise<{ stdout: string; stderr: string }> {
  const prefix = options?.logPrefix ?? 'exec'
  if (process.platform === 'darwin') {
    // Docker Desktop bin MUST come first — on Intel Mac, Homebrew's /usr/local/bin/docker
    // is a CLI-only binary without compose plugin; Docker Desktop's docker has compose built in.
    const extraPaths = [
      '/Applications/Docker.app/Contents/Resources/bin',
      '/usr/local/bin',
      '/opt/homebrew/bin',
    ].join(':')
    // Set HOME so root shell can find user's ~/.docker/cli-plugins/
    const home = process.env.HOME || ''
    // Apply DOCKER_CONFIG override in privileged shell if credential helper unavailable
    const env = getShellEnv()
    const configOverride = await getDockerConfigOverride(env)
    const dockerConfigExport = configOverride ? `export DOCKER_CONFIG="${configOverride}" && ` : ''
    const fullScript = `export PATH="${extraPaths}:$PATH" && export HOME="${home}" && ${dockerConfigExport}${script}`
    const escaped = fullScript.replace(/\\/g, '\\\\').replace(/"/g, '\\"')
    const osascriptCmd = `do shell script "${escaped}" with administrator privileges`
    console.log(`[${prefix}] execWithPrivileges: PATH order = ${extraPaths}`)
    console.log(`[${prefix}] execWithPrivileges: HOME = ${home}`)
    console.log(`[${prefix}] execWithPrivileges: script = ${script}`)
    return execInProject('osascript', ['-e', osascriptCmd], options)
  } else {
    console.log(`[${prefix}] execWithPrivileges (Linux): script = ${script}`)
    return execInProject('pkexec', ['sh', '-c', script], options)
  }
}

export function spawnWithOutput(
  cmd: string,
  args: string[],
  options: { cwd?: string; timeout?: number; logPrefix?: string; onOutput: (line: string) => void }
): Promise<void> {
  const prefix = options.logPrefix ?? 'spawn'
  return new Promise((resolve, reject) => {
    const proc = spawn(cmd, args, {
      cwd: options.cwd ?? PROJECT_ROOT,
      env: getExecEnv(),
      stdio: ['ignore', 'pipe', 'pipe']
    })
    const processData = (data: Buffer) => {
      const lines = data.toString().split('\n').filter(l => l.trim())
      for (const line of lines) {
        console.log(`[${prefix}] ${line}`)
      }
      if (lines.length > 0) {
        options.onOutput(lines[lines.length - 1].trim().substring(0, 200))
      }
    }
    proc.stdout?.on('data', processData)
    proc.stderr?.on('data', processData)

    const timer = setTimeout(() => {
      proc.kill('SIGTERM')
      reject(new Error(`Command timed out after ${(options.timeout ?? 120000) / 1000}s`))
    }, options.timeout ?? 120000)

    proc.on('close', (code) => {
      clearTimeout(timer)
      if (code === 0) resolve()
      else reject(new Error(`Process exited with code ${code}`))
    })
    proc.on('error', (err) => { clearTimeout(timer); reject(err) })
  })
}

// ─── Network / timing utilities ──────────────────────

export function isPortReachable(port: number, host = '127.0.0.1', timeout = 2000): Promise<boolean> {
  return new Promise((resolve) => {
    const socket = new net.Socket()
    socket.setTimeout(timeout)
    socket.on('connect', () => { socket.destroy(); resolve(true) })
    socket.on('error', () => resolve(false))
    socket.on('timeout', () => { socket.destroy(); resolve(false) })
    socket.connect(port, host)
  })
}

export function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}
