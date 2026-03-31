/**
 * @file index.ts
 * @description Electron main process entry point
 *
 * Initializes BrowserWindow and Manager modules,
 * manages application lifecycle (startup, shutdown, tray minimize).
 */

import { app, BrowserWindow, dialog } from 'electron'
import { join } from 'path'
import { ensureWritableProject } from './constants'
import { initShellEnv } from './shell-env'
import { ProcessManager } from './process-manager'
import { stopAll as stopDocker } from './docker-manager'
import { HealthMonitor } from './health-monitor'
import { TrayManager } from './tray-manager'
import { InstallerRegistry } from './installer-registry'
import { ServiceLauncher } from './service-launcher'
import { registerIpcHandlers } from './ipc-handlers'
import { tryOpenExternalUrl } from './external-links'
import { initUpdater } from './updater'

// ─── Global Instances ───────────────────────────────────────

let mainWindow: BrowserWindow | null = null
let processManager: ProcessManager | null = null
const healthMonitor = new HealthMonitor()
let trayManager: TrayManager | null = null
let installerRegistry: InstallerRegistry | null = null
let serviceLauncher: ServiceLauncher | null = null

// ─── Window Creation ───────────────────────────────────────

function createMainWindow(): BrowserWindow {
  const win = new BrowserWindow({
    width: 1000,
    height: 750,
    minWidth: 800,
    minHeight: 600,
    title: 'NarraNexus',
    titleBarStyle: 'hiddenInset',
    trafficLightPosition: { x: 15, y: 15 },
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      sandbox: false,
      contextIsolation: true,
      nodeIntegration: false,
      webSecurity: false  // Disable CORS — local-only app talking to localhost backend
    },
    show: false // Wait for ready-to-show before displaying, avoid white flash
  })

  // Show window once ready
  win.once('ready-to-show', () => {
    win.show()
  })

  // Intercept external links, open in system browser
  win.webContents.setWindowOpenHandler(({ url }) => {
    tryOpenExternalUrl(url)
    return { action: 'deny' }
  })

  // Quit app when window is closed
  win.on('close', () => {
    app.isQuitting = true
    app.quit()
  })

  // Load page
  if (process.env.ELECTRON_RENDERER_URL) {
    // Dev mode: dev server URL injected by electron-vite
    win.loadURL(process.env.ELECTRON_RENDERER_URL)
  } else {
    // Production mode: load build artifacts
    const indexPath = join(__dirname, '../renderer/index.html')
    console.log('[main] Loading renderer from:', indexPath)
    win.loadFile(indexPath)
  }

  // Capture renderer process load errors
  win.webContents.on('did-fail-load', (_event, errorCode, errorDescription) => {
    console.error('[main] Renderer failed to load:', errorCode, errorDescription)
  })

  // Open DevTools in dev mode (docked at bottom for easy debugging)
  if (process.env.ELECTRON_RENDERER_URL) {
    win.webContents.openDevTools({ mode: 'bottom' })
  }

  return win
}

// ─── Application Lifecycle ───────────────────────────────────

// Flag for quitting state (to distinguish closing window from quitting app)
declare module 'electron' {
  interface App {
    isQuitting: boolean
  }
}
app.isQuitting = false

app.whenReady().then(async () => {
  // 启动初始化时显示 splash 窗口，避免用户以为 app 卡死
  let splash: BrowserWindow | null = null
  try {
    if (app.isPackaged) {
      splash = new BrowserWindow({
        width: 360, height: 200,
        frame: false, transparent: false, alwaysOnTop: true,
        resizable: false, skipTaskbar: true,
        webPreferences: { nodeIntegration: false, contextIsolation: true }
      })
      splash.loadURL(`data:text/html;charset=utf-8,
        <html><body style="margin:0;display:flex;align-items:center;justify-content:center;
          height:100vh;background:#1a1a2e;color:#e0e0e0;font-family:-apple-system,system-ui,sans-serif;
          font-size:16px;-webkit-app-region:drag;">
          <div style="text-align:center">
            <div style="font-size:24px;margin-bottom:12px">NarraNexus</div>
            <div style="color:#888">Initializing...</div>
          </div>
        </body></html>`)
      splash.show()
    }

    // Ensure writable project directory exists (copied on first packaged launch)
    ensureWritableProject()

    // Resolve full env vars from user login shell (required for macOS .app launch)
    await initShellEnv()

    // Close splash before showing main window
    if (splash && !splash.isDestroyed()) {
      splash.close()
      splash = null
    }
  } catch (err) {
    // 启动失败时显示错误对话框，而不是静默崩溃
    if (splash && !splash.isDestroyed()) splash.close()
    const message = err instanceof Error ? err.message : String(err)
    console.error('[main] Startup initialization failed:', message)
    dialog.showErrorBox('NarraNexus - Startup Error',
      `Failed to initialize the application:\n\n${message}\n\nPlease check disk space and permissions, then try again.`)
    app.quit()
    return
  }

  // Initialize process manager (depends on shell-env being initialized)
  processManager = new ProcessManager()

  // Initialize three-phase setup modules
  installerRegistry = new InstallerRegistry()
  serviceLauncher = new ServiceLauncher(processManager)

  // Create main window
  mainWindow = createMainWindow()

  // Register IPC handlers
  registerIpcHandlers(processManager, healthMonitor, mainWindow, installerRegistry, serviceLauncher)

  // Create system tray
  trayManager = new TrayManager(healthMonitor, processManager)
  trayManager.create(mainWindow)

  // Clean up stale processes from a previous unclean exit BEFORE health
  // monitor starts, so Dashboard doesn't flash stale green indicators.
  await processManager.forceKillServicePorts()

  // Wire HealthMonitor → ProcessManager: when a port becomes reachable,
  // promote the service from 'starting' to 'running'. This ensures
  // "running" means "actually serving", not just "process emitted stdout".
  healthMonitor.on('service-health-change', (serviceId: string, state: string) => {
    if (state === 'healthy' && processManager.getServiceStatus(serviceId) === 'starting') {
      processManager.promoteToRunning(serviceId)
    }
  })

  // Start health checks (AFTER port cleanup so first check is accurate)
  healthMonitor.start()

  // Initialize auto-updater (checks GitHub Releases)
  initUpdater(mainWindow)

  // macOS: re-show window when Dock icon is clicked
  app.on('activate', () => {
    if (mainWindow) {
      mainWindow.show()
    }
  })
})

// When all windows are closed (non-macOS)
app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit()
  }
})

// Before app quit: clean up ALL background processes, Docker containers, and ports.
// Uses preventDefault() to block quit until cleanup completes (or hard timeout).
let cleanupDone = false

app.on('before-quit', (e) => {
  app.isQuitting = true

  if (cleanupDone) return // Cleanup done, allow quit

  e.preventDefault() // Prevent immediate quit

  // Stop health checks immediately
  healthMonitor.stop()

  // Destroy tray
  trayManager?.destroy()

  const cleanup = async () => {
    // stopAll() handles processes + port sweep. After it completes, Docker
    // compose down runs. All ports are clear so compose down won't hang.
    // Note: stopAll resets activeOperation to 'idle' at the end, but since
    // the app is quitting, the cleanupDone flag prevents any re-entry.
    await (processManager?.stopAll() ?? Promise.resolve())
    await stopDocker()
  }

  // Hard timeout: force quit after 15s even if cleanup hangs
  const forceQuitTimer = setTimeout(() => {
    console.error('[main] Cleanup timed out after 15s, forcing quit')
    cleanupDone = true
    app.quit()
  }, 15000)

  cleanup().finally(() => {
    clearTimeout(forceQuitTimer)
    cleanupDone = true
    app.quit()
  })
})
