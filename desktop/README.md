# NarraNexus Desktop

Electron desktop application -- packages NarraNexus as a one-click installable macOS DMG / Linux AppImage.

After installation, users open the app, fill in their API Keys, click Apply, and all environment setup and service startup is handled automatically.

---

## Table of Contents

- [Quick Start](#quick-start)
- [User Guide](#user-guide)
- [Packaging and Distribution](#packaging-and-distribution)
- [Architecture](#architecture)
- [Source Code Updates and Packaging](#source-code-updates-and-packaging)
- [Scenarios Requiring Packaging Config Changes](#scenarios-requiring-packaging-config-changes)
- [Directory Structure](#directory-structure)
- [FAQ](#faq)

---

## Quick Start

### Development Mode

```bash
cd desktop
npm install
npm run dev
```

This starts Electron + Vite dev server with hot-reload support. In this mode, `PROJECT_ROOT` points to the repository root directory, reading and writing local files directly.

### Building

```bash
# Run from the project root directory
bash build-desktop.sh          # Auto-detect platform
bash build-desktop.sh mac      # macOS DMG
bash build-desktop.sh linux    # Linux AppImage/deb
```

Build artifacts are placed in `desktop/dist/`.

---

## User Guide

### Installation (macOS)

1. Double-click the `.dmg` file and drag NarraNexus into Applications
2. On first launch, Gatekeeper will block the app. Workarounds:
   - **Option A**: Right-click NarraNexus.app -> Open -> Confirm Open
   - **Option B**: Run in terminal: `xattr -cr /Applications/NarraNexus.app`
   - **Option C**: System Settings -> Privacy & Security -> Allow from Anywhere (requires running `sudo spctl --master-disable` first)

### Prerequisites

Users only need one thing pre-installed on their machine:

- **Docker Desktop** -- used to run the MySQL database container

All other dependencies (uv, Python, Claude Code) are installed automatically by the app.

### First-Time Usage Flow

```
Open NarraNexus.app
  |
  +-- Display SetupWizard configuration page
  |   +-- Fill in API Keys (OPENAI_API_KEY is required)
  |   +-- Database configuration can use default values
  |   +-- Click "Apply & Start"
  |
  +-- Automatically execute 10-step installation (progress bar updates in real time):
  |   +--  1. Detect/install uv (Python package manager)
  |   +--  2. Detect/install Claude Code + verify login
  |   +--  3. uv sync (install Python dependencies)
  |   +--  4. Detect Docker (provide download link if not installed)
  |   +--  5. docker compose up -d (start MySQL container)
  |   +--  6. Wait for MySQL to be ready (up to 60 seconds)
  |   +--  7. Create database tables (auto-retry up to 5 times on failure)
  |   +--  8. Sync table schemas
  |   +--  9. Build frontend (if dist/ does not exist)
  |   +-- 10. Start 4 background services
  |
  +-- Automatically switch to Dashboard
      +-- Click "Open NarraNexus" -> opens http://localhost:8000 in the browser
```

### Dashboard Features

- **Service Status**: 6 cards (MySQL, Backend, MCP, Poller, Job Trigger, Frontend)
- **Log Viewer**: Log area at the bottom, supports tab switching by service (All / Backend / MCP / Poller / Job Trigger)
- **Start/Stop**: One-click start or stop all services
- **Port Conflicts**: If a port is already in use at startup, a dialog shows the occupying process name and PID; the user can confirm to terminate it
- **System Tray**: Closing the window minimizes to the tray instead of quitting the app
- **Settings**: Click the gear icon to modify .env configuration

### Subsequent Launches

After the initial setup is complete, opening the app goes directly to the Dashboard without showing the SetupWizard.

### Data Storage Locations

| Data | Location (Packaged) | Location (Dev Mode) |
|------|---------------------|---------------------|
| API Keys & DB config | `~/Library/Application Support/NarraNexus/project/.env` | `NarraNexus/.env` |
| App state (setupComplete) | `~/Library/Application Support/NarraNexus/config.json` | same |
| Project source & dependencies | `~/Library/Application Support/NarraNexus/project/` | repo root |
| Python virtual environment | `~/Library/Application Support/NarraNexus/project/.venv/` | `NarraNexus/.venv/` |

On Linux, `~/Library/Application Support/NarraNexus/` is replaced by `~/.config/NarraNexus/`.

> **Note**: To reset the app to a clean state, delete the `~/Library/Application Support/NarraNexus/` directory (or `~/.config/NarraNexus/` on Linux) and reopen the app. This will trigger a fresh setup.

---

## Packaging and Distribution

### Build Process

`build-desktop.sh` executes 4 steps in sequence:

```
1. npm install          -> Install Electron dependencies
2. frontend: npm build  -> Build frontend static files to frontend/dist/
3. electron-vite build  -> Compile Electron source code to desktop/out/
4. electron-builder     -> Package as DMG/AppImage, artifacts in desktop/dist/
```

### Packaged Contents

The `extraResources` field in `electron-builder.yml` bundles the project root directory into the app's `Resources/project/`:

```yaml
extraResources:
  - from: "../"              # Project root directory
    to: "project"            # -> Resources/project/
    filter:
      - "**/*"               # Include everything
      - "!**/node_modules/**"  # Exclude (Python deps installed at runtime via uv sync)
      - "!**/.venv/**"
      - "!**/.git/**"
      - "!**/desktop/**"       # Exclude the desktop directory itself
      - "!**/__pycache__/**"
      - "!**/*.pyc"
      # frontend/dist/ is NOT excluded -> pre-built frontend is included
```

### Distributing to Users

Simply send the `desktop/dist/NarraNexus-*.dmg` file to users.

---

## Architecture

### Overview

```
+-------------------------------------------------+
|                 Electron App                     |
|                                                  |
|  +---------+  IPC   +----------------------+    |
|  |Renderer |<------>|    Main Process       |    |
|  |(React)  |        |                        |    |
|  |         |        |  +------------------+  |    |
|  | Setup   |        |  | ProcessManager   |  |    |
|  | Wizard  |        |  |  spawn 4 services |  |    |
|  |         |        |  +--------+---------+  |    |
|  | Dash-   |        |  +-------+----------+  |    |
|  | board   |        |  | HealthMonitor    |  |    |
|  |         |        |  | DockerManager    |  |    |
|  | Log     |        |  | TrayManager      |  |    |
|  | Viewer  |        |  | EnvManager       |  |    |
|  +---------+        |  +-----------------+   |    |
|                      +----------------------+    |
+------------------+-----------------------------+
                   | child_process.spawn
        +----------+-------------+
        v          v             v
  +----------+ +--------+  +-----------+
  | Backend  | |  MCP   |  |  Poller   | ...
  | :8000    | | :7801  |  | (no port) |
  |          | +--------+  +-----------+
  | serves   |
  | frontend |
  | static   |
  +----------+
```

### Core Design Decisions

#### 1. Backend Serves Frontend Static Files

Instead of running a separate frontend dev server (which requires a Node.js runtime), the approach is:
- Pre-build `frontend/dist/` during packaging
- `backend/main.py` mounts StaticFiles, with all non-API requests falling back to `index.html` (SPA routing)
- Users access `http://localhost:8000` to see the frontend

**Benefit**: Users do not need Node.js installed on their machine.

#### 2. Read-Only to Writable Project Directory

The macOS `.app` bundle has a read-only filesystem, making it impossible to write `.env` or `.venv` inside it. The solution:

```
During packaging: project source code -> Resources/project/ (read-only)
First launch:    copy to ~/Library/Application Support/NarraNexus/project/ (writable)
Subsequent launches: if pyproject.toml exists, skip copying
```

Key code (`constants.ts`):
```typescript
export const BUNDLED_PROJECT_ROOT = app.isPackaged
  ? join(process.resourcesPath, 'project') : null

export const PROJECT_ROOT = app.isPackaged
  ? join(app.getPath('userData'), 'project')    // Writable location
  : join(__dirname, '..', '..', '..')           // Dev mode: repo root
```

#### 3. IPC Channel Isolation

The preload script cannot reference `electron.app` (it causes a white-screen crash). Therefore, IPC channel names are defined in `src/shared/ipc-channels.ts` (pure string constants with zero dependencies), and both main and preload import from there.

#### 4. Process Group Management

Background services are started via `child_process.spawn` with `detached: true` to create independent process groups. When stopping, `process.kill(-pid, signal)` is used to kill the entire process group, ensuring the full `uv -> python` subprocess chain is properly cleaned up.

#### 5. Port Conflict Detection

Before starting services, all service ports (8000, 7801) are scanned. If a port is in use:
- `lsof` is used to get the PID and name of the occupying process
- A native system dialog asks the user to confirm whether to terminate it
- MySQL port 3306 is not checked (the user may have a local database running continuously)

#### 6. Crash Auto-Restart

`ProcessManager` listens to process `exit` events and auto-restarts on abnormal exits:
- Exponential backoff: wait time = 1s x 2^(attempt-1)
- Maximum of 3 restart attempts
- Counter resets on manual restart

### Inter-Process Communication

```
Renderer (React)
    |  contextBridge.exposeInMainWorld('nexus', {...})
    v
Preload (ipc-channels.ts)
    |  ipcRenderer.invoke / ipcRenderer.on
    v
Main Process (ipc-handlers.ts)
    |  ipcMain.handle / mainWindow.webContents.send
    v
ProcessManager / DockerManager / HealthMonitor / EnvManager
```

| Direction | Purpose | Example |
|-----------|---------|---------|
| Renderer -> Main | Invoke operations | `startAllServices()`, `setEnv()`, `autoSetup()` |
| Main -> Renderer | Push events | `onLog()`, `onHealthUpdate()`, `onSetupProgress()` |

### Page Structure

| Page | File | When Displayed |
|------|------|----------------|
| Loading | App.tsx | While checking the setupComplete flag |
| SetupWizard | SetupWizard.tsx | First-time use / clicking Settings |
| Dashboard | Dashboard.tsx | After configuration is complete |

### Main Process Modules

| Module | Responsibility |
|--------|---------------|
| `index.ts` | App lifecycle, window creation |
| `constants.ts` | Paths, ports, service definitions, IPC channels |
| `process-manager.ts` | Service process start/stop, auto-restart, one-click setup |
| `health-monitor.ts` | TCP/HTTP health polling |
| `docker-manager.ts` | Docker Compose container management |
| `tray-manager.ts` | System tray menu |
| `ipc-handlers.ts` | IPC handler registry |
| `env-manager.ts` | .env file read/write |
| `dependency-checker.ts` | System dependency detection |
| `store.ts` | Persistent storage (setupComplete, etc.) |

---

## Source Code Updates and Packaging

### Key Takeaway

> **After updating code in `src/`, `backend/`, or `frontend/`, simply re-run `bash build-desktop.sh` and the new code will automatically be included in the DMG. No packaging configuration changes are needed.**

### How It Works

```
When build-desktop.sh runs:
  1. npm run build (frontend/)     -> Rebuild frontend -> frontend/dist/ updated
  2. electron-vite build           -> Compile Electron source -> desktop/out/ updated
  3. electron-builder              -> Reads extraResources rules during packaging:
     from: "../" -> to: "project"
     Copies the project root (including latest src/, backend/, frontend/dist/)
     entirely into the app's Resources/project/
```

Therefore:

| What You Changed | What To Do | Config Changes Needed? |
|------------------|-----------|:---------------------:|
| Python code under `src/` | Re-run `bash build-desktop.sh` | No |
| `backend/` routes/logic | Re-run `bash build-desktop.sh` | No |
| `frontend/` components/pages | Re-run `bash build-desktop.sh` | No |
| `pyproject.toml` dependencies | Re-run `bash build-desktop.sh` | No |
| `.env.example` fields | Re-run `bash build-desktop.sh` | No |
| `docker-compose.yaml` | Re-run `bash build-desktop.sh` | No |

### User-Side Updates

**Note**: After a user installs a new DMG version, `~/Library/Application Support/NarraNexus/project/` will not update automatically (because `ensureWritableProject()` detects that `pyproject.toml` already exists and skips copying).

To force an update, users need to delete that directory and reopen the app, or we can implement an incremental update mechanism in the future.

---

## Scenarios Requiring Packaging Config Changes

The following scenarios require modifying code under `desktop/`:

### 1. Adding a New Background Service Process

Modify the `SERVICES` array in `constants.ts`:

```typescript
// For example, adding a scheduler service
{
  id: 'scheduler',
  label: 'Scheduler',
  command: 'uv',
  args: ['run', 'python', '-m', 'xyz_agent_context.services.scheduler'],
  port: null,           // Use null if no port
  healthUrl: null,
  order: 5              // Startup order
}
```

Also update the service card list and `LOG_TABS` in `Dashboard.tsx`.

### 2. Adding New Large Directories to Exclude

Modify the `filter` in `electron-builder.yml`:

```yaml
filter:
  - "!**/new_large_dir/**"
```

### 3. Modifying the Electron UI

Edit files under `desktop/src/renderer/` or `desktop/src/main/`, then rebuild.

### 4. Adding New IPC Channels

1. `src/shared/ipc-channels.ts` -- Add channel name constants
2. `src/main/ipc-handlers.ts` -- Register handlers
3. `src/preload/index.ts` -- Expose to renderer
4. `src/renderer/env.d.ts` -- Add type declarations

---

## Directory Structure

```
desktop/
├── src/
│   ├── main/                    # Electron main process
│   │   ├── index.ts             # App entry point, window creation, lifecycle
│   │   ├── constants.ts         # Paths, ports, service definitions
│   │   ├── process-manager.ts   # Service process management + one-click setup
│   │   ├── health-monitor.ts    # Health status polling
│   │   ├── docker-manager.ts    # Docker Compose management
│   │   ├── tray-manager.ts      # System tray
│   │   ├── ipc-handlers.ts      # IPC handler registry
│   │   ├── env-manager.ts       # .env read/write
│   │   ├── dependency-checker.ts # Dependency detection
│   │   └── store.ts             # Persistent storage
│   ├── preload/
│   │   └── index.ts             # contextBridge, exposes nexus API
│   ├── shared/
│   │   └── ipc-channels.ts      # IPC channel names (pure constants, no electron dependency)
│   └── renderer/                # React frontend
│       ├── App.tsx              # Routing: loading -> setup -> dashboard
│       ├── env.d.ts             # Global type declarations
│       ├── pages/
│       │   ├── SetupWizard.tsx  # Configuration page: .env form + install progress
│       │   └── Dashboard.tsx    # Main panel: service status + logs + controls
│       ├── components/
│       │   ├── ServiceCard.tsx  # Service status card
│       │   ├── LogViewer.tsx    # Real-time log viewer
│       │   └── StepIndicator.tsx
│       └── styles/
│           └── index.css        # Tailwind + custom styles
├── resources/                   # Icons and other static assets
├── electron-builder.yml         # Packaging configuration
├── electron.vite.config.ts      # electron-vite configuration
├── package.json
├── tsconfig.json                # Root TypeScript config
├── tsconfig.node.json           # Main/Preload compilation config
├── tsconfig.web.json            # Renderer compilation config
├── tailwind.config.js
└── postcss.config.js
```

---

## FAQ

### Q: User sees a white screen after installation?
Check whether the preload script references `electron.app`. The preload can only import from `src/shared/`, not from `src/main/`.

### Q: User reports an EROFS error (read-only filesystem)?
Ensure all file write operations use `PROJECT_ROOT` (writable directory) and never write to `BUNDLED_PROJECT_ROOT` (read-only).

### Q: MySQL table creation fails?
On first launch, MySQL may still be initializing even after the port is available. A retry mechanism is already in place (5 attempts, 5-second intervals).

### Q: Services won't stop?
Ensure processes are created with `detached: true` to form process groups, and use `process.kill(-pid)` to kill the entire group when stopping.

### Q: User needs to update to a new version?
Currently, users must delete `~/Library/Application Support/NarraNexus/project/` and reinstall. Version detection + incremental updates can be implemented in the future.

### Q: Docker command not found after packaging?
Electron's PATH is very short after packaging. Ensure that `docker-manager.ts` and `process-manager.ts` use an enhanced PATH (including `/usr/local/bin`, `/opt/homebrew/bin`).
