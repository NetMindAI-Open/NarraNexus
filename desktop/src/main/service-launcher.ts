/**
 * @file service-launcher.ts
 * @description Phase 3: Service startup with Docker 500 retry logic
 *
 * Launch steps: wait-docker → compose-up → wait-mysql → wait-synapse → init-tables → wait-evermemos → start-services
 * Extracted from process-manager.ts runQuickStart + runAutoSetup Step 6-12.
 *
 * Core fix: waits for Docker daemon to become healthy before running compose,
 * preventing the 500 error → privileges escalation misdiagnosis.
 */

import { join } from 'path'
import { existsSync } from 'fs'
import { EventEmitter } from 'events'
import {
  PROJECT_ROOT,
  TABLE_MGMT_DIR,
  EVERMEMOS_DIR,
  SYNAPSE_TEMPLATE_DIR,
  SYNAPSE_DATA_DIR,
  NEXUS_MATRIX_DIR
} from './constants'
import {
  detectDockerState,
  waitForDockerReady,
  ensureDockerDaemon,
  isEverMemOSAvailable,
  startEverMemOS,
  getDockerConfigOverride,
} from './docker-manager'
import { getExecEnv, execInProject, execWithPrivileges, spawnWithOutput, isPortReachable, delay } from './exec-utils'
import { execFile } from 'child_process'
import { promisify } from 'util'

const execFileAsync = promisify(execFile)
import type { ProcessManager } from './process-manager'
import type { LaunchStep, LaunchStepId } from '../shared/setup-types'

// ─── EverMemOS Infrastructure Ports ───────────────────

const EM_INFRA_PORTS = [
  { port: 27017, name: 'MongoDB' },
  { port: 19200, name: 'Elasticsearch' },
  { port: 19530, name: 'Milvus' },
  { port: 6379,  name: 'Redis' }
]

// ─── Service Launcher ───────────────────────────────

export class ServiceLauncher extends EventEmitter {
  private processManager: ProcessManager

  constructor(processManager: ProcessManager) {
    super()
    this.processManager = processManager
  }

  /**
   * Launch all services (Phase 3)
   * Emits 'launch-step' events for each step progress update.
   */
  async launch(options?: { skipEverMemOS?: boolean }): Promise<{ success: boolean; error?: string }> {
    let skipEM = options?.skipEverMemOS ?? false

    const steps: LaunchStep[] = [
      { id: 'wait-docker', label: 'Wait for Docker', status: 'pending' },
      { id: 'compose-up', label: 'Start containers', status: 'pending' },
      { id: 'wait-mysql', label: 'Wait for MySQL', status: 'pending' },
      { id: 'wait-synapse', label: 'Wait for Synapse', status: 'pending' },
      { id: 'init-tables', label: 'Initialize database', status: 'pending' },
      { id: 'wait-evermemos', label: 'Wait for EverMemOS infra', status: 'pending' },
      { id: 'start-services', label: 'Start services', status: 'pending' }
    ]

    const updateStep = (id: LaunchStepId, partial: Partial<LaunchStep>) => {
      const step = steps.find((s) => s.id === id)
      if (step) {
        Object.assign(step, partial)
        this.emit('launch-step', { ...step })
      }
    }

    try {
      console.log('[launcher] ========== LAUNCH STARTED ==========')
      console.log(`[launcher] skipEverMemOS=${skipEM}, platform=${process.platform}, arch=${process.arch}`)

      // ─── Step 1: Wait for Docker daemon ─────────────────
      console.log('[launcher] Step 1: wait-docker')
      updateStep('wait-docker', { status: 'running', message: 'Checking Docker daemon...' })
      const dockerState = await detectDockerState()
      console.log(`[launcher] Step 1: dockerState = ${dockerState}`)

      if (dockerState === 'not_installed') {
        console.log('[launcher] Step 1: FAILED — Docker not installed')
        updateStep('wait-docker', { status: 'error', message: 'Docker is not installed.' })
        return { success: false, error: 'Docker is not installed' }
      }

      if (dockerState === 'starting') {
        console.log('[launcher] Step 1: Docker starting, waiting up to 120s...')
        updateStep('wait-docker', { status: 'running', message: 'Docker daemon is starting, waiting...' })
        const ready = await waitForDockerReady(120000, 2000)
        if (!ready) {
          console.log('[launcher] Step 1: FAILED — Docker timed out after 120s')
          updateStep('wait-docker', { status: 'error', message: 'Docker daemon failed to become ready after 120s' })
          return { success: false, error: 'Docker daemon timeout' }
        }
        console.log('[launcher] Step 1: Docker became ready')
      } else if (dockerState === 'not_running') {
        console.log('[launcher] Step 1: Docker not running, trying to start...')
        updateStep('wait-docker', { status: 'running', message: 'Starting Docker daemon...' })
        const started = await ensureDockerDaemon()
        if (!started) {
          console.log('[launcher] Step 1: FAILED — could not start Docker')
          updateStep('wait-docker', { status: 'error', message: 'Failed to start Docker daemon. Please start Docker Desktop manually.' })
          return { success: false, error: 'Failed to start Docker' }
        }
        console.log('[launcher] Step 1: Docker started successfully')
      }
      console.log('[launcher] Step 1: DONE — Docker is ready')
      updateStep('wait-docker', { status: 'done', message: 'Docker is ready' })

      // ─── Step 1.5: Ensure Synapse config exists ───────
      // Copy template files to data directory if homeserver.yaml is missing.
      // Templates live at deploy/synapse/ (committed); runtime data at deploy/synapse/data/ (gitignored).
      const synapseConfig = join(SYNAPSE_DATA_DIR, 'homeserver.yaml')
      if (!existsSync(synapseConfig)) {
        console.log('[launcher] Synapse config not found, copying templates...')
        updateStep('compose-up', { status: 'running', message: 'Initializing Synapse config (first run)...' })
        try {
          const { mkdirSync, copyFileSync } = require('fs')
          mkdirSync(SYNAPSE_DATA_DIR, { recursive: true })
          copyFileSync(join(SYNAPSE_TEMPLATE_DIR, 'homeserver.yaml'), synapseConfig)
          copyFileSync(
            join(SYNAPSE_TEMPLATE_DIR, 'localhost.log.config'),
            join(SYNAPSE_DATA_DIR, 'localhost.log.config')
          )
          console.log('[launcher] Synapse config templates copied successfully')
        } catch (err) {
          console.warn(`[launcher] Synapse config setup failed: ${err instanceof Error ? err.message : err}`)
        }
      }

      // ─── Step 2: docker compose up ─────
      console.log('[launcher] Step 2: compose-up')
      const COMPOSE_TIMEOUT = 600000 // 10 minutes for image pull
      updateStep('compose-up', { status: 'running', message: 'Starting containers (MySQL + Synapse)...' })

      // Both ports reachable = containers already running, skip compose
      const [mysqlUp, synapseUp] = await Promise.all([isPortReachable(3306), isPortReachable(8008)])
      if (mysqlUp && synapseUp) {
        console.log('[launcher] Step 2: Ports 3306+8008 already reachable, skipping compose-up')
        updateStep('compose-up', { status: 'done', message: 'Containers already running' })
      } else {
        // Ensure Docker is truly healthy before compose
        const preComposeState = await detectDockerState()
        console.log(`[launcher] Step 2: preComposeState = ${preComposeState}`)
        if (preComposeState !== 'healthy') {
          console.log('[launcher] Step 2: Docker not fully ready, waiting 30s...')
          updateStep('compose-up', { status: 'running', message: 'Waiting for Docker to be fully ready...' })
          await waitForDockerReady(30000, 1000)
        }

        // Log which docker binary will be resolved
        const env = getExecEnv()
        console.log(`[launcher] Step 2: PATH first 5 entries: ${env.PATH?.split(':').slice(0, 5).join(':')}`)
        try {
          const { stdout: whichDocker } = await execFileAsync('which', ['docker'], { env, timeout: 5000 })
          console.log(`[launcher] Step 2: which docker = ${whichDocker.trim()}`)
        } catch { console.log('[launcher] Step 2: which docker — not found in PATH') }
        try {
          const { stdout: composeVer } = await execFileAsync('docker', ['compose', 'version'], { env, timeout: 5000 })
          console.log(`[launcher] Step 2: docker compose version = ${composeVer.trim()}`)
        } catch (e) {
          console.log(`[launcher] Step 2: docker compose version FAILED: ${e instanceof Error ? e.message : e}`)
        }

        // 预先检测 Docker 凭证助手是否可用（结果会缓存，供后续所有 Docker 命令使用）
        await getDockerConfigOverride(env)

        let composeSuccess = false
        for (let attempt = 0; attempt < 3; attempt++) {
          try {
            // Try V2 → V1 → privileged
            let ok = false
            try {
              console.log('[launcher] compose-up: trying docker compose (V2)...')
              await spawnWithOutput('docker', ['compose', 'up', '-d'], {
                timeout: COMPOSE_TIMEOUT,
                onOutput: (line) => updateStep('compose-up', { status: 'running', message: line })
              })
              ok = true
              console.log('[launcher] compose-up: docker compose (V2) succeeded')
            } catch (e) {
              console.log(`[launcher] compose-up: V2 failed: ${e instanceof Error ? e.message : e}`)
            }

            if (!ok) {
              try {
                console.log('[launcher] compose-up: trying docker-compose (V1)...')
                await spawnWithOutput('docker-compose', ['up', '-d'], {
                  timeout: COMPOSE_TIMEOUT,
                  onOutput: (line) => updateStep('compose-up', { status: 'running', message: line })
                })
                ok = true
                console.log('[launcher] compose-up: docker-compose (V1) succeeded')
              } catch (e) {
                console.log(`[launcher] compose-up: V1 failed: ${e instanceof Error ? e.message : e}`)
              }
            }

            if (!ok) {
              console.log('[launcher] compose-up: trying with elevated privileges...')
              updateStep('compose-up', { status: 'running', message: 'Retrying with elevated privileges...' })
              await execWithPrivileges(
                `cd "${PROJECT_ROOT}" && (docker compose up -d || docker-compose up -d)`,
                { timeout: COMPOSE_TIMEOUT }
              )
              console.log('[launcher] compose-up: privileged compose succeeded')
            }

            composeSuccess = true
            break
          } catch (err) {
            const errMsg = err instanceof Error ? err.message : String(err)
            // 容器名冲突 = 之前的容器还在，当作成功
            if (errMsg.includes('already in use')) {
              console.warn('[launcher] compose-up: container already exists, treating as success')
              composeSuccess = true
              break
            }
            // 500 错误 = daemon 还在启动，等一下重试
            if (errMsg.includes('500') && attempt < 2) {
              updateStep('compose-up', {
                status: 'running',
                message: `Docker daemon not fully ready, retrying in 5s... (attempt ${attempt + 2}/3)`
              })
              await delay(5000)
              continue
            }
            throw err
          }
        }

        if (!composeSuccess) {
          console.log('[launcher] Step 2: FAILED — compose never succeeded')
          updateStep('compose-up', { status: 'error', message: 'Failed to start containers' })
          return { success: false, error: 'docker compose up failed' }
        }
      }

      // Start EverMemOS infrastructure
      if (!skipEM && isEverMemOSAvailable()) {
        console.log('[launcher] Step 2: Starting EverMemOS infrastructure...')
        updateStep('compose-up', { status: 'running', message: 'Starting EverMemOS infrastructure...' })
        const emResult = await startEverMemOS()
        if (!emResult.success) {
          console.error('[launcher] Step 2: EverMemOS containers failed:', emResult.output)
        } else {
          console.log('[launcher] Step 2: EverMemOS infrastructure started')
        }
      } else {
        console.log(`[launcher] Step 2: EverMemOS skipped (skipEM=${skipEM}, available=${isEverMemOSAvailable()})`)
      }
      console.log('[launcher] Step 2: DONE — containers started')
      updateStep('compose-up', { status: 'done', message: 'Containers started' })

      // ─── Step 3: Wait for MySQL ─────────────────────────
      console.log('[launcher] Step 3: wait-mysql')
      updateStep('wait-mysql', { status: 'running', message: 'Waiting for MySQL port...' })
      let mysqlReady = false
      for (let i = 0; i < 60; i++) {
        if (await isPortReachable(3306)) {
          mysqlReady = true
          console.log(`[launcher] Step 3: MySQL port reachable after ${i + 1}s`)
          break
        }
        if (i % 5 === 4) {
          console.log(`[launcher] Step 3: MySQL port not reachable yet (${i + 1}s)`)
          updateStep('wait-mysql', {
            status: 'running',
            message: `Waiting for MySQL port... (${i + 1}s)`
          })
        }
        await delay(1000)
      }
      if (!mysqlReady) {
        console.log('[launcher] Step 3: FAILED — MySQL port timeout (60s)')
        updateStep('wait-mysql', { status: 'error', message: 'MySQL port timeout (60s)' })
        return { success: false, error: 'MySQL port timeout' }
      }
      // Extra buffer for MySQL initialization
      console.log('[launcher] Step 3: Waiting 5s extra for MySQL initialization...')
      await delay(5000)
      console.log('[launcher] Step 3: DONE — MySQL is ready')
      updateStep('wait-mysql', { status: 'done', message: 'MySQL is ready' })

      // ─── Step 3.5: Wait for Synapse ────────────────────
      console.log('[launcher] Step 3.5: wait-synapse')
      updateStep('wait-synapse', { status: 'running', message: 'Waiting for Synapse port...' })
      let synapseReady = false
      for (let i = 0; i < 90; i++) {
        if (await isPortReachable(8008)) {
          synapseReady = true
          console.log(`[launcher] Step 3.5: Synapse port reachable after ${i + 1}s`)
          break
        }
        if (i % 5 === 4) {
          console.log(`[launcher] Step 3.5: Synapse port not reachable yet (${i + 1}s)`)
          updateStep('wait-synapse', {
            status: 'running',
            message: `Waiting for Synapse port... (${i + 1}s)`
          })
        }
        await delay(1000)
      }
      if (!synapseReady) {
        // Synapse failure is non-fatal — Matrix features won't work but the app is usable
        console.warn('[launcher] Step 3.5: Synapse port timeout (90s) — Matrix features will be unavailable')
        updateStep('wait-synapse', { status: 'error', message: 'Synapse timeout — Matrix features unavailable' })
      } else {
        console.log('[launcher] Step 3.5: DONE — Synapse is ready')
        // Create admin user if NexusMatrix scripts are available
        const createAdminScript = join(NEXUS_MATRIX_DIR, 'scripts', 'create_admin.py')
        if (existsSync(createAdminScript)) {
          try {
            console.log('[launcher] Step 3.5: Creating Synapse admin user...')
            updateStep('wait-synapse', { status: 'running', message: 'Creating admin user...' })
            await execInProject('python', [
              createAdminScript,
              '--homeserver', 'http://localhost:8008',
              '--shared-secret', '9_HimzS2a&CBS^DPyP&mLBT2Nry-e-tR=39.w&jkwf9IGkOCGH',
              '--username', 'nexus_admin',
              '--password', 'nexus_admin_password',
              '--admin',
              '--display-name', 'NexusAdmin'
            ], { timeout: 15000 })
            console.log('[launcher] Step 3.5: Admin user ready')
          } catch (err) {
            // Non-fatal: admin user may already exist
            console.warn(`[launcher] Step 3.5: Admin user creation: ${err instanceof Error ? err.message : err}`)
          }
        }
        updateStep('wait-synapse', { status: 'done', message: 'Synapse is ready' })
      }

      // ─── Step 4: Initialize database tables ─────────────
      console.log('[launcher] Step 4: init-tables')
      updateStep('init-tables', { status: 'running', message: 'Creating database tables...' })
      const createScript = join(TABLE_MGMT_DIR, 'create_all_tables.py')
      let tableCreated = false
      let lastTableErr = ''
      for (let attempt = 1; attempt <= 5; attempt++) {
        try {
          console.log(`[launcher] Step 4: create_all_tables attempt ${attempt}/5`)
          await execInProject('uv', ['run', 'python', createScript], { timeout: 60000 })
          tableCreated = true
          console.log(`[launcher] Step 4: create_all_tables succeeded on attempt ${attempt}`)
          break
        } catch (err) {
          lastTableErr = err instanceof Error ? err.message : String(err)
          console.log(`[launcher] Step 4: create_all_tables attempt ${attempt} failed: ${lastTableErr}`)
          updateStep('init-tables', {
            status: 'running',
            message: `Attempt ${attempt} failed, ${attempt < 5 ? 'retrying...' : 'max retries reached'}`
          })
          if (attempt < 5) await delay(5000)
        }
      }
      if (!tableCreated) {
        console.log(`[launcher] Step 4: FAILED — table creation failed after 5 attempts`)
        updateStep('init-tables', { status: 'error', message: `Table creation failed: ${lastTableErr}` })
        return { success: false, error: 'Table creation failed' }
      }

      // Auto-apply safe schema changes (ENUM expansion, VARCHAR growth, new columns)
      // Dangerous changes are skipped silently — no user prompts needed.
      console.log('[launcher] Step 4: Syncing table schema (auto-safe)...')
      updateStep('init-tables', { status: 'running', message: 'Syncing table schema...' })
      try {
        const syncScript = join(TABLE_MGMT_DIR, 'sync_all_tables.py')
        await execInProject('uv', ['run', 'python', syncScript, '--auto-safe'], { timeout: 60000 })
        console.log('[launcher] Step 4: sync_all_tables --auto-safe succeeded')
      } catch (syncErr) {
        console.log(`[launcher] Step 4: sync_all_tables failed (non-blocking): ${syncErr}`)
      }
      console.log('[launcher] Step 4: DONE — database initialized')
      updateStep('init-tables', { status: 'done', message: 'Database initialized' })

      // ─── Step 5: Wait for EverMemOS infrastructure ──────
      console.log(`[launcher] Step 5: wait-evermemos (skipEM=${skipEM}, dir exists=${existsSync(EVERMEMOS_DIR)})`)
      if (!skipEM && existsSync(EVERMEMOS_DIR)) {
        updateStep('wait-evermemos', { status: 'running', message: 'Waiting for EverMemOS infrastructure...' })
        let allInfraReady = true
        for (const { port, name } of EM_INFRA_PORTS) {
          let ready = false
          for (let i = 0; i < 180; i++) {
            if (await isPortReachable(port)) { ready = true; break }
            if (i % 5 === 4) {
              console.log(`[launcher] Step 5: Waiting for ${name}:${port}... (${i + 1}s)`)
              updateStep('wait-evermemos', {
                status: 'running',
                message: `Waiting for ${name}:${port}... (${i + 1}s)`
              })
            }
            await delay(1000)
          }
          if (!ready) {
            console.log(`[launcher] Step 5: ${name}:${port} timeout after 180s`)
            updateStep('wait-evermemos', {
              status: 'error',
              message: `${name}:${port} timeout after 180s — EverMemOS will not start`
            })
            allInfraReady = false
            skipEM = true
            break
          }
          console.log(`[launcher] Step 5: ${name}:${port} ready`)
        }
        if (allInfraReady) {
          console.log('[launcher] Step 5: DONE — all EverMemOS infra ready')
          updateStep('wait-evermemos', { status: 'done', message: 'EverMemOS infrastructure ready' })
        }
      } else {
        console.log('[launcher] Step 5: SKIPPED — EverMemOS not configured')
        updateStep('wait-evermemos', { status: 'skipped', message: 'EverMemOS not configured' })
      }

      // ─── Step 6: Start backend services ─────────────────
      console.log(`[launcher] Step 6: start-services (skipEverMemOS=${skipEM})`)
      updateStep('start-services', { status: 'running', message: 'Starting all services...' })
      await this.processManager.startAll({ skipEverMemOS: skipEM })
      console.log('[launcher] Step 6: DONE — all services started')
      updateStep('start-services', { status: 'done', message: 'All services started' })

      console.log('[launcher] ========== LAUNCH COMPLETED SUCCESSFULLY ==========')
      return { success: true }
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err)
      console.error(`[launcher] ========== LAUNCH FAILED: ${message} ==========`)
      return { success: false, error: message }
    }
  }

  /**
   * Launch EverMemOS independently (called from config/done phase after user fills API keys).
   * 1. Start EverMemOS docker containers
   * 2. Wait for infrastructure ports (MongoDB/ES/Milvus/Redis)
   * 3. Restart services with skipEverMemOS=false
   */
  async launchEverMemOS(): Promise<{ success: boolean; error?: string }> {
    try {
      console.log('[launcher] ========== LAUNCH EVERMEMOS ==========')

      // 1. Start EverMemOS docker containers
      if (!isEverMemOSAvailable()) {
        return { success: false, error: 'EverMemOS is not installed' }
      }
      const emResult = await startEverMemOS()
      if (!emResult.success) {
        console.error('[launcher] EverMemOS containers failed:', emResult.output)
        return { success: false, error: emResult.output }
      }
      console.log('[launcher] EverMemOS containers started')

      // 2. Wait for infrastructure ports
      for (const { port, name } of EM_INFRA_PORTS) {
        let ready = false
        for (let i = 0; i < 180; i++) {
          if (await isPortReachable(port)) { ready = true; break }
          if (i % 10 === 9) {
            console.log(`[launcher] Waiting for ${name}:${port}... (${i + 1}s)`)
          }
          await delay(1000)
        }
        if (!ready) {
          console.error(`[launcher] ${name}:${port} timeout after 180s`)
          return { success: false, error: `${name}:${port} timeout after 180s` }
        }
        console.log(`[launcher] ${name}:${port} ready`)
      }

      // 3. Restart services to load EverMemOS
      console.log('[launcher] Restarting services with EverMemOS enabled...')
      await this.processManager.startAll({ skipEverMemOS: false })
      console.log('[launcher] ========== EVERMEMOS LAUNCH COMPLETED ==========')
      return { success: true }
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err)
      console.error(`[launcher] EverMemOS launch failed: ${message}`)
      return { success: false, error: message }
    }
  }
}
