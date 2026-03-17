/**
 * @file preload/index.ts
 * @description Preload script — exposes safe API to Renderer via contextBridge
 *
 * The Renderer process cannot directly access Node.js APIs.
 * All calls to the Main Process are bridged through here.
 */

import { contextBridge, ipcRenderer } from 'electron'
import { IPC } from '../shared/ipc-channels'

// ─── Type Definitions (used by Renderer side) ────────────────────

export interface NexusAPI {
  // Environment variables
  getEnv: () => Promise<{ config: Record<string, string>; fields: unknown[] }>
  setEnv: (updates: Record<string, string>) => Promise<{ success: boolean }>
  validateEnv: () => Promise<{ valid: boolean; missing: string[]; warnings: string[] }>

  // EverMemOS environment variables
  getEverMemOSEnv: () => Promise<{ available: boolean; config: Record<string, string>; fields: unknown[] }>
  setEverMemOSEnv: (updates: Record<string, string>) => Promise<{ success: boolean }>
  validateEverMemOSEnv: () => Promise<{ valid: boolean; missing: string[]; warnings: string[] }>

  // Docker
  getDockerStatus: () => Promise<{ dockerReady: boolean; groups: unknown[] }>
  startDocker: () => Promise<{ mysql: boolean; evermemos: boolean }>
  stopDocker: () => Promise<{ success: boolean }>

  // Service processes
  startAllServices: () => Promise<{ success: boolean }>
  stopAllServices: () => Promise<{ success: boolean }>
  restartService: (serviceId: string) => Promise<{ success: boolean }>
  getServiceStatus: () => Promise<unknown[]>

  // Health check
  getHealthStatus: () => Promise<unknown>

  // Database
  initDatabase: () => Promise<{ success: boolean; output: string }>

  // Three-phase setup
  runPreflight: () => Promise<unknown>
  installDep: (id: string) => Promise<{ success: boolean; error?: string }>
  retryDep: (id: string) => Promise<{ success: boolean; error?: string }>
  skipDep: (id: string) => Promise<{ success: boolean }>
  installAllDeps: (missingIds: string[]) => Promise<{ success: boolean; failedId?: string }>
  runLaunch: (options?: { skipEverMemOS?: boolean }) => Promise<{ success: boolean; error?: string }>
  onInstallerUpdate: (callback: (state: unknown) => void) => () => void
  onLaunchStep: (callback: (step: unknown) => void) => () => void

  // EverMemOS lifecycle
  launchEverMemOS: () => Promise<{ success: boolean; error?: string }>
  isEverMemOSInstalled: () => Promise<boolean>

  // Claude Code authentication
  getClaudeAuthInfo: () => Promise<unknown>
  startClaudeLogin: () => Promise<unknown>
  cancelClaudeLogin: () => Promise<{ success: boolean }>
  sendClaudeLoginInput: (text: string) => Promise<{ success: boolean }>
  saveSetupToken: (token: string) => Promise<{ valid: boolean; message: string }>
  onClaudeLoginStatus: (callback: (status: unknown) => void) => () => void

  // Auto-updater
  checkForUpdates: () => Promise<void>
  downloadUpdate: () => Promise<void>
  installUpdate: () => Promise<void>
  onUpdateStatus: (callback: (status: unknown) => void) => () => void

  // Miscellaneous
  openExternal: (url: string) => Promise<void>
  getSetupState: () => Promise<{ setupComplete: boolean }>
  setSetupComplete: () => Promise<{ success: boolean }>
  getLogs: (serviceId?: string) => Promise<unknown[]>

  // Event listeners
  onLog: (callback: (entry: unknown) => void) => () => void
  onHealthUpdate: (callback: (status: unknown) => void) => () => void
  onTrayAction: (callback: (action: string) => void) => () => void
  onNavigate: (callback: (page: string) => void) => () => void
}

// ─── Expose API ───────────────────────────────────────

const nexusAPI: NexusAPI = {
  // ─── Environment Variables ─────────────────────────────────────
  getEnv: () => ipcRenderer.invoke(IPC.GET_ENV),
  setEnv: (updates) => ipcRenderer.invoke(IPC.SET_ENV, updates),
  validateEnv: () => ipcRenderer.invoke(IPC.VALIDATE_ENV),

  // ─── EverMemOS Environment Variables ─────────────────────────────
  getEverMemOSEnv: () => ipcRenderer.invoke(IPC.GET_EVERMEMOS_ENV),
  setEverMemOSEnv: (updates) => ipcRenderer.invoke(IPC.SET_EVERMEMOS_ENV, updates),
  validateEverMemOSEnv: () => ipcRenderer.invoke(IPC.VALIDATE_EVERMEMOS_ENV),

  // ─── Docker ───────────────────────────────────────
  getDockerStatus: () => ipcRenderer.invoke(IPC.DOCKER_STATUS),
  startDocker: () => ipcRenderer.invoke(IPC.DOCKER_START),
  stopDocker: () => ipcRenderer.invoke(IPC.DOCKER_STOP),

  // ─── Service Processes ─────────────────────────────────────
  startAllServices: () => ipcRenderer.invoke(IPC.SERVICE_START_ALL),
  stopAllServices: () => ipcRenderer.invoke(IPC.SERVICE_STOP_ALL),
  restartService: (serviceId) => ipcRenderer.invoke(IPC.SERVICE_RESTART, serviceId),
  getServiceStatus: () => ipcRenderer.invoke(IPC.SERVICE_STATUS),

  // ─── Health Check ─────────────────────────────────────
  getHealthStatus: () => ipcRenderer.invoke(IPC.HEALTH_STATUS),

  // ─── Database ───────────────────────────────────────
  initDatabase: () => ipcRenderer.invoke(IPC.INIT_DATABASE),

  // ─── Three-Phase Setup ─────────────────────────────────
  runPreflight: () => ipcRenderer.invoke(IPC.RUN_PREFLIGHT),
  installDep: (id) => ipcRenderer.invoke(IPC.INSTALL_DEP, id),
  retryDep: (id) => ipcRenderer.invoke(IPC.RETRY_DEP, id),
  skipDep: (id) => ipcRenderer.invoke(IPC.SKIP_DEP, id),
  installAllDeps: (missingIds) => ipcRenderer.invoke(IPC.INSTALL_ALL_DEPS, missingIds),
  runLaunch: (options?) => ipcRenderer.invoke(IPC.RUN_LAUNCH, options),
  onInstallerUpdate: (callback) => {
    const handler = (_event: unknown, state: unknown) => callback(state)
    ipcRenderer.on(IPC.ON_INSTALLER_UPDATE, handler)
    return () => ipcRenderer.removeListener(IPC.ON_INSTALLER_UPDATE, handler)
  },
  onLaunchStep: (callback) => {
    const handler = (_event: unknown, step: unknown) => callback(step)
    ipcRenderer.on(IPC.ON_LAUNCH_STEP, handler)
    return () => ipcRenderer.removeListener(IPC.ON_LAUNCH_STEP, handler)
  },

  // ─── EverMemOS Lifecycle ─────────────────────────────────────
  launchEverMemOS: () => ipcRenderer.invoke(IPC.LAUNCH_EVERMEMOS),
  isEverMemOSInstalled: () => ipcRenderer.invoke(IPC.IS_EVERMEMOS_INSTALLED),

  // ─── Claude Code Authentication ─────────────────────────────
  getClaudeAuthInfo: () => ipcRenderer.invoke(IPC.CLAUDE_AUTH_INFO),
  startClaudeLogin: () => ipcRenderer.invoke(IPC.CLAUDE_LOGIN_START),
  cancelClaudeLogin: () => ipcRenderer.invoke(IPC.CLAUDE_LOGIN_CANCEL),
  sendClaudeLoginInput: (text: string) => ipcRenderer.invoke(IPC.CLAUDE_LOGIN_INPUT, text),
  saveSetupToken: (token) => ipcRenderer.invoke(IPC.CLAUDE_SAVE_SETUP_TOKEN, token),
  onClaudeLoginStatus: (callback) => {
    const handler = (_event: unknown, status: unknown) => callback(status)
    ipcRenderer.on(IPC.ON_CLAUDE_LOGIN_STATUS, handler)
    return () => ipcRenderer.removeListener(IPC.ON_CLAUDE_LOGIN_STATUS, handler)
  },

  // ─── Auto-Updater ─────────────────────────────────────────
  checkForUpdates: () => ipcRenderer.invoke(IPC.CHECK_FOR_UPDATES),
  downloadUpdate: () => ipcRenderer.invoke(IPC.DOWNLOAD_UPDATE),
  installUpdate: () => ipcRenderer.invoke(IPC.INSTALL_UPDATE),
  onUpdateStatus: (callback) => {
    const handler = (_event: unknown, status: unknown) => callback(status)
    ipcRenderer.on(IPC.ON_UPDATE_STATUS, handler)
    return () => ipcRenderer.removeListener(IPC.ON_UPDATE_STATUS, handler)
  },

  // ─── Miscellaneous ─────────────────────────────────────────
  openExternal: (url) => ipcRenderer.invoke(IPC.OPEN_EXTERNAL, url),
  getSetupState: () => ipcRenderer.invoke(IPC.GET_SETUP_STATE),
  setSetupComplete: () => ipcRenderer.invoke(IPC.SET_SETUP_COMPLETE),
  getLogs: (serviceId?) => ipcRenderer.invoke(IPC.GET_LOGS, serviceId),

  // ─── Event Listeners (returns unsubscribe function) ─────────────────
  onLog: (callback) => {
    const handler = (_event: unknown, entry: unknown) => callback(entry)
    ipcRenderer.on(IPC.ON_LOG, handler)
    return () => ipcRenderer.removeListener(IPC.ON_LOG, handler)
  },

  onHealthUpdate: (callback) => {
    const handler = (_event: unknown, status: unknown) => callback(status)
    ipcRenderer.on(IPC.ON_HEALTH_UPDATE, handler)
    return () => ipcRenderer.removeListener(IPC.ON_HEALTH_UPDATE, handler)
  },

  onTrayAction: (callback) => {
    const handler = (_event: unknown, action: string) => callback(action)
    ipcRenderer.on('tray-action', handler)
    return () => ipcRenderer.removeListener('tray-action', handler)
  },

  onNavigate: (callback) => {
    const handler = (_event: unknown, page: string) => callback(page)
    ipcRenderer.on('navigate', handler)
    return () => ipcRenderer.removeListener('navigate', handler)
  }
}

contextBridge.exposeInMainWorld('nexus', nexusAPI)
