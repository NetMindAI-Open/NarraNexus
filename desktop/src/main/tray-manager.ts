/**
 * @file tray-manager.ts
 * @description System tray icon + context menu
 *
 * Provides a system tray icon that displays a service status overview
 * and supports quick actions: start/stop services, open settings, quit app.
 */

import { Tray, Menu, nativeImage, app, BrowserWindow } from 'electron'
import { join } from 'path'
import { HealthMonitor, HealthState } from './health-monitor'
import { ProcessManager } from './process-manager'

// ─── TrayManager ────────────────────────────────────

export class TrayManager {
  private tray: Tray | null = null
  private mainWindow: BrowserWindow | null = null
  private healthMonitor: HealthMonitor
  private processManager: ProcessManager

  constructor(healthMonitor: HealthMonitor, processManager: ProcessManager) {
    this.healthMonitor = healthMonitor
    this.processManager = processManager
  }

  /** Create system tray icon */
  create(mainWindow: BrowserWindow): void {
    this.mainWindow = mainWindow

    // Create a 16x16 tray icon (using a simple circular icon)
    const icon = this.createTrayIcon()
    this.tray = new Tray(icon)
    this.tray.setToolTip('NarraNexus')

    // Click tray icon to show main window
    this.tray.on('click', () => {
      this.showMainWindow()
    })

    // Initialize menu
    this.updateMenu()

    // Listen for health state changes to update menu
    this.healthMonitor.on('health-update', () => {
      this.updateMenu()
    })
  }

  /** Destroy tray */
  destroy(): void {
    this.tray?.destroy()
    this.tray = null
  }

  // ─── Internal Methods ─────────────────────────────────────

  /** Create tray icon (16x16 solid circle) */
  private createTrayIcon(): nativeImage {
    // Use project Logo as tray icon
    const iconPath = app.isPackaged
      ? join(process.resourcesPath, 'icon.png')
      : join(__dirname, '..', '..', 'resources', 'icon.png')

    try {
      const icon = nativeImage.createFromPath(iconPath)
      return icon.resize({ width: 16, height: 16 })
    } catch {
      // If icon file is not found, create a simple placeholder icon
      return nativeImage.createEmpty()
    }
  }

  /** Update tray context menu */
  private updateMenu(): void {
    if (!this.tray) return

    const health = this.healthMonitor.getStatus()
    const processes = this.processManager.getAllStatus()

    // Build service status submenu
    const statusItems: Electron.MenuItemConstructorOptions[] = []

    // MySQL (in infrastructure, not services)
    const mysqlHealth = health.infrastructure.find((s) => s.serviceId === 'mysql')
    statusItems.push({
      label: `${stateIcon(mysqlHealth?.state)} MySQL`,
      enabled: false
    })

    // Other services
    for (const proc of processes) {
      const svcHealth = health.services.find((s) => s.serviceId === proc.serviceId)
      const state = svcHealth?.state ?? (proc.status === 'running' ? 'healthy' : 'unhealthy')
      statusItems.push({
        label: `${stateIcon(state)} ${proc.label}  ${proc.status === 'running' ? 'Running' : 'Stopped'}`,
        enabled: false
      })
    }

    const contextMenu = Menu.buildFromTemplate([
      {
        label: 'Open NarraNexus',
        click: () => this.showMainWindow()
      },
      { type: 'separator' },
      {
        label: 'Service Status',
        submenu: statusItems
      },
      { type: 'separator' },
      {
        label: 'Start All Services',
        click: () => {
          this.mainWindow?.webContents.send('tray-action', 'start-all')
        }
      },
      {
        label: 'Stop All Services',
        click: () => {
          this.mainWindow?.webContents.send('tray-action', 'stop-all')
        }
      },
      { type: 'separator' },
      {
        label: 'View Logs',
        click: () => {
          this.showMainWindow()
          this.mainWindow?.webContents.send('navigate', 'logs')
        }
      },
      {
        label: 'Settings (API Keys)',
        click: () => {
          this.showMainWindow()
          this.mainWindow?.webContents.send('navigate', 'settings')
        }
      },
      { type: 'separator' },
      {
        label: 'Quit',
        click: () => {
          // app.quit() triggers 'before-quit' handler which does full cleanup
          // (processes + Docker + port sweep) before actually exiting.
          app.quit()
        }
      }
    ])

    this.tray.setContextMenu(contextMenu)
  }

  /** Show main window */
  private showMainWindow(): void {
    if (!this.mainWindow) return
    if (this.mainWindow.isMinimized()) {
      this.mainWindow.restore()
    }
    this.mainWindow.show()
    this.mainWindow.focus()
  }
}

// ─── Utility Functions ───────────────────────────────────────

/** State icon mapping */
function stateIcon(state?: HealthState): string {
  switch (state) {
    case 'healthy':
      return '●'
    case 'unhealthy':
      return '○'
    default:
      return '◌'
  }
}
