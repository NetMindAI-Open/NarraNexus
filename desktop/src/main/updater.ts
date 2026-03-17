/**
 * @file updater.ts
 * @description Auto-updater module — checks GitHub Releases for new versions
 *
 * Uses electron-updater to check, download, and install updates.
 * Sends progress events to Renderer via IPC for UI display.
 *
 * Note: macOS auto-update requires code signing. Without signing,
 * update-downloaded will not fire — user must manually download DMG.
 */

import { BrowserWindow } from 'electron'
import { autoUpdater, type UpdateInfo, type ProgressInfo } from 'electron-updater'
import { IPC } from '../shared/ipc-channels'

/** Update status sent to Renderer */
export interface UpdateStatus {
  state: 'checking' | 'available' | 'not-available' | 'downloading' | 'downloaded' | 'error'
  version?: string
  releaseNotes?: string
  progress?: number        // 0-100
  bytesPerSecond?: number
  error?: string
}

/**
 * Initialize auto-updater and bind events to BrowserWindow
 *
 * Call this after main window is created. The updater will automatically
 * check for updates on launch, then periodically every 4 hours.
 */
export function initUpdater(mainWindow: BrowserWindow): void {
  // Configuration
  autoUpdater.autoDownload = false       // Let user decide
  autoUpdater.autoInstallOnAppQuit = true // Install pending update on quit
  autoUpdater.allowDowngrade = false

  const send = (status: UpdateStatus): void => {
    if (!mainWindow.isDestroyed()) {
      mainWindow.webContents.send(IPC.ON_UPDATE_STATUS, status)
    }
  }

  // ─── Events ────────────────────────────────────────

  autoUpdater.on('checking-for-update', () => {
    console.log('[updater] Checking for updates...')
    send({ state: 'checking' })
  })

  autoUpdater.on('update-available', (info: UpdateInfo) => {
    console.log(`[updater] Update available: v${info.version}`)
    const notes = typeof info.releaseNotes === 'string'
      ? info.releaseNotes
      : Array.isArray(info.releaseNotes)
        ? info.releaseNotes.map(n => (typeof n === 'string' ? n : n.note)).join('\n')
        : undefined
    send({
      state: 'available',
      version: info.version,
      releaseNotes: notes
    })
  })

  autoUpdater.on('update-not-available', () => {
    console.log('[updater] No updates available')
    send({ state: 'not-available' })
  })

  autoUpdater.on('download-progress', (progress: ProgressInfo) => {
    send({
      state: 'downloading',
      progress: Math.round(progress.percent),
      bytesPerSecond: progress.bytesPerSecond
    })
  })

  autoUpdater.on('update-downloaded', (info: UpdateInfo) => {
    console.log(`[updater] Update downloaded: v${info.version}`)
    send({
      state: 'downloaded',
      version: info.version
    })
  })

  autoUpdater.on('error', (err: Error) => {
    console.error('[updater] Error:', err.message)
    send({
      state: 'error',
      error: err.message
    })
  })

  // ─── Initial check (delay 5s after launch) ──────────
  setTimeout(() => {
    autoUpdater.checkForUpdates().catch((err) => {
      console.error('[updater] Initial check failed:', err.message)
    })
  }, 5000)

  // ─── Periodic check every 4 hours ──────────────────
  setInterval(() => {
    autoUpdater.checkForUpdates().catch((err) => {
      console.error('[updater] Periodic check failed:', err.message)
    })
  }, 4 * 60 * 60 * 1000)
}

/** Manually trigger update check */
export function checkForUpdates(): void {
  autoUpdater.checkForUpdates().catch((err) => {
    console.error('[updater] Manual check failed:', err.message)
  })
}

/** Start downloading the available update */
export function downloadUpdate(): void {
  autoUpdater.downloadUpdate().catch((err) => {
    console.error('[updater] Download failed:', err.message)
  })
}

/** Quit app and install the downloaded update */
export function installUpdate(): void {
  autoUpdater.quitAndInstall(false, true)
}
