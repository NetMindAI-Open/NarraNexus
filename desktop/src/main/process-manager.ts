/**
 * @file process-manager.ts
 * @description Backend service process lifecycle management
 *
 * Manages backend services (Backend, MCP, Poller, Job Trigger, EverMemOS):
 * start, stop, auto-restart on crash. Launches processes via child_process.spawn,
 * captures stdout/stderr output for log display.
 *
 * Setup/install logic has been moved to:
 * - preflight-runner.ts (Phase 1: dependency detection)
 * - installer-registry.ts (Phase 2: dependency installation)
 * - service-launcher.ts (Phase 3: service startup)
 */

import { spawn, ChildProcess, execFile } from 'child_process'
import { join } from 'path'
import { existsSync } from 'fs'
import { EventEmitter } from 'events'
import { promisify } from 'util'
import * as net from 'net'
import { dialog } from 'electron'
import {
  SERVICES,
  ServiceDef,
  PROJECT_ROOT,
  MAX_RESTART_ATTEMPTS,
  RESTART_BACKOFF_BASE,
  MCP_PORTS
} from './constants'
import { getShellEnv } from './shell-env'
import { readEnv } from './env-manager'

const execFileAsync = promisify(execFile)

// ─── Type Definitions ───────────────────────────────────────

export type ProcessStatus = 'stopped' | 'starting' | 'running' | 'crashed'

export interface ProcessInfo {
  serviceId: string
  label: string
  status: ProcessStatus
  pid: number | null
  restartCount: number
  lastError: string | null
}

export interface LogEntry {
  serviceId: string
  timestamp: number
  stream: 'stdout' | 'stderr'
  message: string
}

// ─── ProcessManager ─────────────────────────────────

export class ProcessManager extends EventEmitter {
  private processes = new Map<string, ChildProcess>()
  private statuses = new Map<string, ProcessStatus>()
  private restartCounts = new Map<string, number>()
  private lastErrors = new Map<string, string>()
  private logs: LogEntry[] = []
  private maxLogs = 500
  private shuttingDown = false

  constructor() {
    super()
    for (const svc of SERVICES) {
      this.statuses.set(svc.id, 'stopped')
      this.restartCounts.set(svc.id, 0)
    }
  }

  /** Start a single service */
  async startService(serviceId: string): Promise<boolean> {
    const svc = SERVICES.find((s) => s.id === serviceId)
    if (!svc) return false

    if (this.processes.has(serviceId)) {
      await this.stopService(serviceId)
    }

    // EverMemOS needs Docker infrastructure (MongoDB, ES, Milvus, Redis).
    // Check ALL required ports, not just MongoDB.
    if (svc.id === 'evermemos') {
      const allUp = await this.isEverMemOSInfraReady()
      if (!allUp) {
        this.addLog(svc.id, 'stdout', 'Starting EverMemOS Docker infrastructure...')
        try {
          const { startEverMemOS } = await import('./docker-manager')
          await startEverMemOS()
          const ready = await this.waitForEverMemOSInfra(svc.id)
          if (!ready) {
            this.addLog(svc.id, 'stderr', 'EverMemOS infrastructure did not become ready in time')
            return false
          }
        } catch (e) {
          this.addLog(svc.id, 'stderr', `Failed to start Docker containers: ${e}`)
          return false
        }
      }
    }

    return this.spawnProcess(svc)
  }

  /** Start all services in order (clean up residual port-occupying processes first) */
  async startAll(options?: { skipEverMemOS?: boolean }): Promise<void> {
    // Phase 1: Stop all managed processes (sets shuttingDown = true,
    // which prevents auto-restart from firing during cleanup)
    await this.stopAll()

    // Phase 2: Kill any orphaned processes still occupying service ports
    // (MCP children, stale uvicorn workers, etc.)
    // IMPORTANT: shuttingDown stays true during this phase so no
    // auto-restart sneaks in between stop and port cleanup.
    await this.forceKillServicePorts()

    // Phase 3: NOW it's safe to reset and start fresh
    this.shuttingDown = false
    for (const svc of SERVICES) {
      this.restartCounts.set(svc.id, 0)
    }

    const sorted = [...SERVICES]
      .filter((svc) => !(options?.skipEverMemOS && svc.id === 'evermemos'))
      .sort((a, b) => a.order - b.order)
    for (const svc of sorted) {
      await this.spawnProcess(svc)
      await this.delay(500)
    }
  }

  /** Stop a single service (kill entire process group) */
  async stopService(serviceId: string): Promise<void> {
    const proc = this.processes.get(serviceId)
    if (!proc) {
      this.statuses.set(serviceId, 'stopped')
      return
    }

    return new Promise((resolve) => {
      const timeout = setTimeout(() => {
        this.killProcessGroup(proc, 'SIGKILL')
        this.processes.delete(serviceId)
        this.statuses.set(serviceId, 'stopped')
        resolve()
      }, 5000)

      proc.once('exit', () => {
        clearTimeout(timeout)
        this.processes.delete(serviceId)
        this.statuses.set(serviceId, 'stopped')
        resolve()
      })

      this.killProcessGroup(proc, 'SIGTERM')
    })
  }

  /** Stop All Services */
  async stopAll(): Promise<void> {
    this.shuttingDown = true

    // Phase 1: SIGTERM each managed process group
    const stopPromises = SERVICES.map((svc) => this.stopService(svc.id))
    await Promise.all(stopPromises)

    // Phase 2: Kill any orphaned child processes still occupying service ports.
    // Python's multiprocessing (used by MCP module_runner) spawns children that
    // escape the parent process group and survive SIGTERM. Same as run.sh's
    // port-based cleanup.
    await this.forceKillServicePorts()
  }

  /** Restart a single service */
  async restartService(serviceId: string): Promise<boolean> {
    this.restartCounts.set(serviceId, 0)
    await this.stopService(serviceId)
    return this.startService(serviceId)
  }

  /** Get all service statuses */
  getAllStatus(): ProcessInfo[] {
    return SERVICES.map((svc) => ({
      serviceId: svc.id,
      label: svc.label,
      status: this.statuses.get(svc.id) ?? 'stopped',
      pid: this.processes.get(svc.id)?.pid ?? null,
      restartCount: this.restartCounts.get(svc.id) ?? 0,
      lastError: this.lastErrors.get(svc.id) ?? null
    }))
  }

  /** Get logs */
  getLogs(serviceId?: string, limit = 100): LogEntry[] {
    let filtered = this.logs
    if (serviceId) {
      filtered = filtered.filter((l) => l.serviceId === serviceId)
    }
    return filtered.slice(-limit)
  }

  // ─── Internal Methods ─────────────────────────────────────

  /**
   * Get execution environment merged with .env (Shell env + .env key-values)
   *
   * Merge rules:
   * - Fields with non-empty values in .env → override shell env
   * - Fields with empty values in .env → don't override, keep shell env values
   */
  private getExecEnv(): Record<string, string> {
    const shellEnv = getShellEnv()
    const dotEnv = readEnv()
    const nonEmptyDotEnv: Record<string, string> = {}
    for (const [key, value] of Object.entries(dotEnv)) {
      if (value.trim()) {
        nonEmptyDotEnv[key] = value
      }
    }
    const noProxyHosts = 'localhost,127.0.0.1'
    return { ...shellEnv, ...nonEmptyDotEnv, NO_PROXY: noProxyHosts, no_proxy: noProxyHosts }
  }

  /**
   * Force clean all residual processes on service ports.
   *
   * Used at startup to kill stale processes from a previous unclean exit,
   * and in stopAll() to kill orphaned child processes (MCP workers etc.).
   */
  async forceKillServicePorts(): Promise<void> {
    const portsSet = new Set<number>()
    for (const svc of SERVICES) {
      if (svc.port !== null) portsSet.add(svc.port)
    }
    for (const p of MCP_PORTS) {
      portsSet.add(p)
    }

    // Get our own PID and parent PID to avoid killing ourselves or Electron
    const safePids = new Set([process.pid, process.ppid])

    let killed = false
    for (const port of portsSet) {
      try {
        // Use -sTCP:LISTEN to only find the LISTENING process, not all
        // connected clients (e.g., Cursor SSH port forwarding). This
        // prevents accidentally killing the IDE or other unrelated processes.
        const { stdout } = await execFileAsync('lsof', [
          '-ti', `:${port}`, '-sTCP:LISTEN'
        ], {
          timeout: 5000,
          env: getShellEnv()
        })
        const pids = stdout.trim().split('\n').filter(Boolean)
        for (const pid of pids) {
          const numPid = Number(pid)
          if (safePids.has(numPid)) continue // Never kill ourselves
          try {
            process.kill(numPid, 'SIGKILL')
            this.addLog('system', 'stderr', `Killed stale process on port ${port} (PID: ${pid})`)
            killed = true
          } catch { /* process may have already exited */ }
        }
      } catch { /* Port not occupied, normal */ }
    }

    if (killed) {
      await this.delay(1000)
    }
  }

  private async ensureServiceRepo(svc: ServiceDef, cwd: string): Promise<boolean> {
    if (!svc.gitRepo || existsSync(cwd)) return true

    // Auto-clone the repository
    const parentDir = join(cwd, '..')
    const dirName = cwd.split('/').pop() || cwd.split('\\').pop() || ''

    // Ensure parent directory exists (ENOENT from spawn is often a missing cwd, not a missing binary)
    const fs = await import('fs')
    fs.mkdirSync(parentDir, { recursive: true })

    this.addLog(svc.id, 'stdout', `Cloning ${svc.gitRepo} ...`)
    this.statuses.set(svc.id, 'starting')
    this.emit('status-change', svc.id, 'starting')

    try {
      const env = this.getExecEnv()
      // Use shell: true to let the OS resolve git via PATH and handle
      // macOS Xcode shim (/usr/bin/git) which fails under Hardened Runtime
      // when called directly via execFile.
      const shell = process.platform === 'darwin' ? '/bin/zsh' : true
      await new Promise<void>((resolve, reject) => {
        const proc = spawn('git', ['clone', svc.gitRepo!, dirName], {
          cwd: parentDir,
          env,
          shell,
          stdio: ['ignore', 'pipe', 'pipe'],
          timeout: 120_000,
        })
        let stderr = ''
        proc.stderr?.on('data', (d: Buffer) => { stderr += d.toString() })
        proc.on('exit', (code) => code === 0 ? resolve() : reject(new Error(stderr.trim() || `exit code ${code}`)))
        proc.on('error', reject)
      })
      this.addLog(svc.id, 'stdout', 'Repository cloned successfully')
      return true
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      this.addLog(svc.id, 'stderr', `Failed to clone repository: ${msg}`)
      this.statuses.set(svc.id, 'crashed')
      this.lastErrors.set(svc.id, `Git clone failed: ${msg}`)
      this.emit('status-change', svc.id, 'crashed')
      return false
    }
  }

  private async spawnProcess(svc: ServiceDef): Promise<boolean> {
    try {
      const cwd = svc.cwd ? join(PROJECT_ROOT, svc.cwd) : PROJECT_ROOT

      // Optional service: silently skip when working directory doesn't exist
      if (svc.optional && !existsSync(cwd)) {
        this.addLog(svc.id, 'stderr', `Skipping optional service: directory not found (${cwd})`)
        this.statuses.set(svc.id, 'stopped')
        return false
      }

      // Auto-clone repository if gitRepo is configured and cwd is missing
      if (svc.gitRepo && !existsSync(cwd)) {
        const cloned = await this.ensureServiceRepo(svc, cwd)
        if (!cloned) return false
      }

      const proc = spawn(svc.command, svc.args, {
        cwd,
        stdio: ['ignore', 'pipe', 'pipe'],
        env: this.getExecEnv(),
        detached: true
      })

      this.processes.set(svc.id, proc)
      this.statuses.set(svc.id, 'starting')
      this.lastErrors.delete(svc.id)

      proc.stdout?.on('data', (data: Buffer) => {
        const message = data.toString().trim()
        if (!message) return
        this.addLog(svc.id, 'stdout', message)
        if (this.statuses.get(svc.id) === 'starting') {
          this.statuses.set(svc.id, 'running')
          this.emit('status-change', svc.id, 'running')
        }
      })

      proc.stderr?.on('data', (data: Buffer) => {
        const message = data.toString().trim()
        if (!message) return
        this.addLog(svc.id, 'stderr', message)
        if (this.statuses.get(svc.id) === 'starting') {
          this.statuses.set(svc.id, 'running')
          this.emit('status-change', svc.id, 'running')
        }
      })

      proc.on('exit', (code, signal) => {
        this.processes.delete(svc.id)

        if (this.shuttingDown) {
          this.statuses.set(svc.id, 'stopped')
          this.emit('status-change', svc.id, 'stopped')
          return
        }

        if (code !== 0 && code !== null) {
          this.statuses.set(svc.id, 'crashed')
          this.lastErrors.set(svc.id, `Process exited with code: ${code}`)
          this.emit('status-change', svc.id, 'crashed')
          this.addLog(svc.id, 'stderr', `Process exited (code=${code}, signal=${signal})`)
          this.tryAutoRestart(svc)
        } else {
          this.statuses.set(svc.id, 'stopped')
          this.emit('status-change', svc.id, 'stopped')
        }
      })

      proc.on('error', (err) => {
        this.processes.delete(svc.id)
        this.statuses.set(svc.id, 'crashed')
        this.lastErrors.set(svc.id, err.message)
        this.emit('status-change', svc.id, 'crashed')
        this.addLog(svc.id, 'stderr', `Failed to start: ${err.message}`)
      })

      this.emit('status-change', svc.id, 'starting')
      return true
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err)
      this.statuses.set(svc.id, 'crashed')
      this.lastErrors.set(svc.id, message)
      return false
    }
  }

  /** Auto-restart after crash (exponential backoff) */
  private async tryAutoRestart(svc: ServiceDef): Promise<void> {
    // First check: abort immediately if shutdown is in progress
    if (this.shuttingDown) return

    const count = (this.restartCounts.get(svc.id) ?? 0) + 1
    this.restartCounts.set(svc.id, count)

    if (count > MAX_RESTART_ATTEMPTS) {
      this.addLog(svc.id, 'stderr',
        `Max restart attempts reached (${MAX_RESTART_ATTEMPTS}), stopping auto-restart`)
      return
    }

    // EverMemOS depends on Docker infrastructure (MongoDB, ES, Milvus, Redis).
    // If any infrastructure port is down, start the Docker containers first.
    if (svc.id === 'evermemos') {
      if (this.shuttingDown) return // Re-check after potential async gap
      const allUp = await this.isEverMemOSInfraReady()
      if (!allUp) {
        if (this.shuttingDown) return
        this.addLog(svc.id, 'stderr', 'Infrastructure not running, starting EverMemOS Docker containers...')
        try {
          const { startEverMemOS } = await import('./docker-manager')
          await startEverMemOS()
        } catch (e) {
          this.addLog(svc.id, 'stderr', `Failed to start Docker containers: ${e}`)
        }
      }
      if (this.shuttingDown) return
      const infraReady = await this.waitForEverMemOSInfra(svc.id)
      if (!infraReady) {
        this.restartCounts.set(svc.id, count - 1)
        this.addLog(svc.id, 'stderr', 'Infrastructure not ready after 180s, skipping restart')
        return
      }
    }

    const waitMs = RESTART_BACKOFF_BASE * Math.pow(2, count - 1)
    this.addLog(svc.id, 'stderr', `Auto-restarting in ${waitMs}ms (attempt ${count})`)

    await this.delay(waitMs)

    // Check again after delay — startAll() or stopAll() may have been called
    if (this.shuttingDown) {
      this.addLog(svc.id, 'stderr', 'Shutdown triggered during backoff, aborting auto-restart')
      return
    }

    // Check if something else already started this service (e.g., startAll)
    if (this.processes.has(svc.id)) {
      this.addLog(svc.id, 'stderr', 'Service already running, skipping auto-restart')
      return
    }

    await this.spawnProcess(svc)
  }

  /** Quick check if ALL EverMemOS infrastructure ports are reachable */
  private async isEverMemOSInfraReady(): Promise<boolean> {
    for (const { port } of ProcessManager.EM_INFRA_PORTS) {
      if (!await this.isPortReachable(port)) return false
    }
    return true
  }

  /** EverMemOS infrastructure port list */
  private static readonly EM_INFRA_PORTS = [
    { port: 27017, name: 'MongoDB' },
    { port: 19200, name: 'Elasticsearch' },
    { port: 19530, name: 'Milvus' },
    { port: 6379,  name: 'Redis' }
  ]

  /** Wait for EverMemOS infrastructure ports (for auto-restart) */
  private async waitForEverMemOSInfra(logServiceId: string): Promise<boolean> {
    this.addLog(logServiceId, 'stderr', 'Waiting for infrastructure ports before restart...')
    for (const { port, name } of ProcessManager.EM_INFRA_PORTS) {
      let ready = false
      for (let i = 0; i < 180; i++) {
        if (this.shuttingDown) return false
        if (await this.isPortReachable(port)) { ready = true; break }
        if (i % 10 === 9) {
          this.addLog(logServiceId, 'stderr', `Still waiting for ${name}:${port}... (${i + 1}s)`)
        }
        await this.delay(1000)
      }
      if (!ready) {
        this.addLog(logServiceId, 'stderr', `${name}:${port} not reachable after 180s, aborting restart`)
        return false
      }
    }
    this.addLog(logServiceId, 'stderr', 'All infrastructure ports ready')
    return true
  }

  /** Check if a TCP port is reachable */
  private isPortReachable(port: number, host = '127.0.0.1', timeout = 2000): Promise<boolean> {
    return new Promise((resolve) => {
      const socket = new net.Socket()
      socket.setTimeout(timeout)
      socket.on('connect', () => { socket.destroy(); resolve(true) })
      socket.on('error', () => resolve(false))
      socket.on('timeout', () => { socket.destroy(); resolve(false) })
      socket.connect(port, host)
    })
  }

  /** Kill process group (negative PID) */
  private killProcessGroup(proc: ChildProcess, signal: NodeJS.Signals): void {
    if (!proc.pid) return
    try {
      process.kill(-proc.pid, signal)
    } catch {
      try { proc.kill(signal) } catch { /* Already exited */ }
    }
  }

  private addLog(serviceId: string, stream: 'stdout' | 'stderr', message: string): void {
    const entry: LogEntry = {
      serviceId,
      timestamp: Date.now(),
      stream,
      message
    }
    this.logs.push(entry)

    if (this.logs.length > this.maxLogs) {
      this.logs = this.logs.slice(-this.maxLogs)
    }

    this.emit('log', entry)
  }

  private delay(ms: number): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms))
  }

  /**
   * Resolve a command name to its full path via the given PATH env.
   * execFile doesn't spawn a shell, so it needs the absolute path on macOS
   * when launched from Finder/Dock (minimal launchd PATH).
   */
  private async resolveCommand(cmd: string, env: Record<string, string>): Promise<string> {
    const pathDirs = (env.PATH || '').split(':')
    for (const dir of pathDirs) {
      const fullPath = join(dir, cmd)
      if (existsSync(fullPath)) return fullPath
    }
    // Fallback: try common known locations
    const fallbacks = [
      `/usr/bin/${cmd}`,
      `/usr/local/bin/${cmd}`,
      `/opt/homebrew/bin/${cmd}`,
    ]
    for (const p of fallbacks) {
      if (existsSync(p)) return p
    }
    // Last resort: return the bare command name (will likely fail with ENOENT)
    return cmd
  }
}
