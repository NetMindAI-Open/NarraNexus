/**
 * @file ipc-handlers.ts
 * @description IPC communication registration center
 *
 * Exposes all Main Process capabilities to Renderer via ipcMain.handle,
 * Renderer calls them securely through preload's contextBridge.
 */

import { ipcMain, shell, BrowserWindow } from 'electron'
import { store } from './store'
import { IPC, PROJECT_ROOT, TABLE_MGMT_DIR } from './constants'
import { readEnv, writeEnv, validateEnv, getEnvFields, sanitizeEnvUpdates as sanitizeMainEnvUpdates } from './env-manager'
import { getClaudeAuthInfo, startClaudeLogin, validateSetupToken } from './claude-auth-manager'
import type { LoginProcessStatus } from './claude-auth-manager'
import * as everMemOSEnv from './evermemos-env-manager'
import * as dockerManager from './docker-manager'
import { ProcessManager } from './process-manager'
import { HealthMonitor } from './health-monitor'
import { runPreflight } from './preflight-runner'
import { isEverMemOSAvailable } from './docker-manager'
import { InstallerRegistry } from './installer-registry'
import { ServiceLauncher } from './service-launcher'
import { execFile } from 'child_process'
import { promisify } from 'util'
import { join } from 'path'
import { getShellEnv } from './shell-env'
import { tryOpenExternalUrl } from './external-links'
import { checkForUpdates, downloadUpdate, installUpdate } from './updater'

const execFileAsync = promisify(execFile)

// ─── Register All IPC Handlers ───────────────────────────

export function registerIpcHandlers(
  processManager: ProcessManager,
  healthMonitor: HealthMonitor,
  mainWindow: BrowserWindow,
  installerRegistry: InstallerRegistry,
  serviceLauncher: ServiceLauncher
): void {
  // ─── Environment Variables ─────────────────────────────────────

  ipcMain.handle(IPC.GET_ENV, () => {
    return { config: readEnv(), fields: getEnvFields() }
  })

  ipcMain.handle(IPC.SET_ENV, (_event, updates: Record<string, string>) => {
    writeEnv(sanitizeMainEnvUpdates(updates))
    return { success: true }
  })

  ipcMain.handle(IPC.VALIDATE_ENV, () => {
    return validateEnv()
  })

  // ─── EverMemOS Environment Variables ─────────────────────────────

  ipcMain.handle(IPC.GET_EVERMEMOS_ENV, () => {
    return {
      available: true,
      config: everMemOSEnv.readEnv(),
      fields: everMemOSEnv.getFields()
    }
  })

  ipcMain.handle(IPC.SET_EVERMEMOS_ENV, (_event, updates: Record<string, string>) => {
    everMemOSEnv.writeEnv(everMemOSEnv.sanitizeEnvUpdates(updates))
    return { success: true }
  })

  ipcMain.handle(IPC.VALIDATE_EVERMEMOS_ENV, () => {
    return everMemOSEnv.validateEnv()
  })

  // ─── Docker ───────────────────────────────────────

  ipcMain.handle(IPC.DOCKER_STATUS, async () => {
    const ready = await dockerManager.isDockerReady()
    const groups = ready ? await dockerManager.getAllStatus() : []
    return { dockerReady: ready, groups }
  })

  ipcMain.handle(IPC.DOCKER_START, async () => {
    return dockerManager.startAll()
  })

  ipcMain.handle(IPC.DOCKER_STOP, async () => {
    await dockerManager.stopAll()
    return { success: true }
  })

  // ─── Service Processes ─────────────────────────────────────

  ipcMain.handle(IPC.SERVICE_START_ALL, async () => {
    await processManager.startAll()
    return { success: true }
  })

  ipcMain.handle(IPC.SERVICE_STOP_ALL, async () => {
    await processManager.stopAll()
    return { success: true }
  })

  ipcMain.handle(IPC.SERVICE_RESTART, async (_event, serviceId: string) => {
    const success = await processManager.restartService(serviceId)
    return { success }
  })

  ipcMain.handle(IPC.SERVICE_STATUS, () => {
    return processManager.getAllStatus()
  })

  // ─── Health Check ─────────────────────────────────────

  ipcMain.handle(IPC.HEALTH_STATUS, async () => {
    // Run a fresh health check instead of returning stale cache.
    // This ensures the UI reflects the real state immediately after
    // start/stop operations, rather than waiting for the next poll cycle.
    return healthMonitor.checkAll()
  })

  // ─── Three-Phase Setup ─────────────────────────────────

  // Phase 1: Preflight
  ipcMain.handle(IPC.RUN_PREFLIGHT, async () => {
    return runPreflight()
  })

  // Phase 2: Install
  ipcMain.handle(IPC.INSTALL_DEP, async (_event, id: string) => {
    try {
      await installerRegistry.install(id)
      return { success: true }
    } catch (err) {
      return { success: false, error: err instanceof Error ? err.message : String(err) }
    }
  })

  ipcMain.handle(IPC.RETRY_DEP, async (_event, id: string) => {
    try {
      await installerRegistry.retry(id)
      return { success: true }
    } catch (err) {
      return { success: false, error: err instanceof Error ? err.message : String(err) }
    }
  })

  ipcMain.handle(IPC.SKIP_DEP, (_event, id: string) => {
    installerRegistry.skip(id)
    return { success: true }
  })

  ipcMain.handle(IPC.INSTALL_ALL_DEPS, async (_event, missingIds: string[]) => {
    console.log(`[ipc] INSTALL_ALL_DEPS called with missingIds:`, missingIds)
    const result = await installerRegistry.installAll(missingIds)
    console.log(`[ipc] INSTALL_ALL_DEPS completed:`, result)
    return result
  })

  // Phase 3: Launch
  ipcMain.handle(IPC.RUN_LAUNCH, async (_event, options?: { skipEverMemOS?: boolean }) => {
    return serviceLauncher.launch(options)
  })

  // Push installer updates in real-time
  installerRegistry.on('installer-update', (state) => {
    mainWindow.webContents.send(IPC.ON_INSTALLER_UPDATE, state)
  })

  // Push launch step updates in real-time
  serviceLauncher.on('launch-step', (step) => {
    mainWindow.webContents.send(IPC.ON_LAUNCH_STEP, step)
  })

  // ─── EverMemOS Lifecycle ─────────────────────────────────────

  ipcMain.handle(IPC.LAUNCH_EVERMEMOS, async () => {
    return serviceLauncher.launchEverMemOS()
  })

  ipcMain.handle(IPC.IS_EVERMEMOS_INSTALLED, () => {
    return isEverMemOSAvailable()
  })

  // ─── Claude Code Authentication ─────────────────────────────

  let activeLogin: { cancel: () => void; sendInput: (text: string) => void } | null = null

  ipcMain.handle(IPC.CLAUDE_AUTH_INFO, async () => {
    return getClaudeAuthInfo(readEnv)
  })

  ipcMain.handle(IPC.CLAUDE_LOGIN_START, async () => {
    if (activeLogin) {
      return { state: 'running', message: 'Login already in progress' } as LoginProcessStatus
    }

    const onStatusChange = (status: LoginProcessStatus) => {
      mainWindow.webContents.send(IPC.ON_CLAUDE_LOGIN_STATUS, status)
      if (status.state !== 'running') {
        activeLogin = null
      }
    }

    const handle = startClaudeLogin(
      onStatusChange,
      (url) => { tryOpenExternalUrl(url) }
    )
    activeLogin = handle
    return handle.promise
  })

  ipcMain.handle(IPC.CLAUDE_LOGIN_CANCEL, async () => {
    if (activeLogin) {
      activeLogin.cancel()
      activeLogin = null
      return { success: true }
    }
    return { success: false }
  })

  ipcMain.handle(IPC.CLAUDE_LOGIN_INPUT, async (_event, text: string) => {
    if (activeLogin) {
      activeLogin.sendInput(text)
      return { success: true }
    }
    return { success: false }
  })

  ipcMain.handle(IPC.CLAUDE_SAVE_SETUP_TOKEN, async (_event, token: string) => {
    const validation = validateSetupToken(token)
    if (!validation.valid) {
      return validation
    }
    writeEnv({ ANTHROPIC_API_KEY: token.trim() })
    return { valid: true, message: 'Setup token saved successfully' }
  })

  // ─── Database Initialization ─────────────────────────────────

  ipcMain.handle(IPC.INIT_DATABASE, async () => {
    try {
      const scriptPath = join(TABLE_MGMT_DIR, 'create_all_tables.py')
      const { stdout, stderr } = await execFileAsync(
        'uv',
        ['run', 'python', scriptPath],
        {
          cwd: PROJECT_ROOT,
          timeout: 60000,
          env: getShellEnv()
        }
      )
      return { success: true, output: stdout + stderr }
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : String(err)
      return { success: false, output: message }
    }
  })

  // ─── Miscellaneous ─────────────────────────────────────────

  ipcMain.handle(IPC.OPEN_EXTERNAL, async (_event, url: string) => {
    tryOpenExternalUrl(url)
  })

  ipcMain.handle(IPC.GET_SETUP_STATE, () => {
    return { setupComplete: store.get('setupComplete') as boolean }
  })

  ipcMain.handle(IPC.SET_SETUP_COMPLETE, () => {
    store.set('setupComplete', true)
    return { success: true }
  })

  ipcMain.handle(IPC.GET_LOGS, (_event, serviceId?: string) => {
    return processManager.getLogs(serviceId)
  })

  // ─── Auto-Updater ──────────────────────────────────────
  ipcMain.handle(IPC.CHECK_FOR_UPDATES, () => checkForUpdates())
  ipcMain.handle(IPC.DOWNLOAD_UPDATE, () => downloadUpdate())
  ipcMain.handle(IPC.INSTALL_UPDATE, () => installUpdate())

  // ─── Event Forwarding (Main → Renderer) ──────────────────

  processManager.on('log', (entry) => {
    mainWindow.webContents.send(IPC.ON_LOG, entry)
  })

  healthMonitor.on('health-update', (status) => {
    mainWindow.webContents.send(IPC.ON_HEALTH_UPDATE, status)
  })
}
