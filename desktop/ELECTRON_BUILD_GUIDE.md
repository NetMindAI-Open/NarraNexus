# NarraNexus Electron Desktop App -- A Complete Guide from Zero to Packaging

> **Target Audience**: Frontend/backend developers with no prior Electron experience.
> **What You Will Learn**: After reading this guide, you will be able to independently build an Electron + React + TypeScript desktop application from scratch, and package it as a macOS `.dmg` or Linux `.AppImage`.
> **Project Reference**: All example code in this guide is taken from the actual NarraNexus Desktop source code.

---

## Table of Contents

- [Chapter 1: What is Electron](#chapter-1-what-is-electron)
- [Chapter 2: The Three-Process Model in Detail](#chapter-2-the-three-process-model-in-detail)
- [Chapter 3: IPC Communication Mechanism](#chapter-3-ipc-communication-mechanism)
- [Chapter 4: Project Directory Structure Design](#chapter-4-project-directory-structure-design)
- [Chapter 5: electron-vite Build System](#chapter-5-electron-vite-build-system)
- [Chapter 6: TypeScript Configuration Strategy](#chapter-6-typescript-configuration-strategy)
- [Chapter 7: Type Safety in the Renderer](#chapter-7-type-safety-in-the-renderer)
- [Chapter 8: Development Mode vs Production Mode](#chapter-8-development-mode-vs-production-mode)
- [Chapter 9: electron-builder Packaging Configuration in Detail](#chapter-9-electron-builder-packaging-configuration-in-detail)
- [Chapter 10: Complete Packaging Pipeline](#chapter-10-complete-packaging-pipeline)
- [Chapter 11: Special Handling for the Production Environment](#chapter-11-special-handling-for-the-production-environment)
- [Chapter 12: How to Create a Similar Project from Scratch](#chapter-12-how-to-create-a-similar-project-from-scratch)
- [Chapter 13: Common Pitfalls and Troubleshooting](#chapter-13-common-pitfalls-and-troubleshooting)

---

## Chapter 1: What is Electron

### 1.1 One-Sentence Explanation

Electron is essentially **"packaging a Chrome browser and a Node.js runtime into a single desktop application"**.

The HTML/CSS/JS pages you write run inside the built-in Chrome (Chromium) to display the UI, while the Node.js code you write runs in the background, handling tasks like file system operations, spawning child processes, and calling system APIs -- things that a browser cannot do.

### 1.2 Why It Can Build Desktop Applications

Regular web pages run inside the browser's sandbox and cannot access the file system or spawn child processes. But Electron does something special: it **compiles Chromium and Node.js into the same process space**. This means:

```
+-------------------------------------------------+
|              Electron Application                |
|                                                  |
|   +--------------+     +----------------------+  |
|   |  Chromium     |     |  Node.js             |  |
|   |  (Render UI)  | <-> |  (Files/Processes/   |  |
|   |  HTML/CSS/JS  |     |   Network)           |  |
|   +--------------+     |  fs/child_process     |  |
|                         +----------------------+  |
|                                                  |
|   These two are welded together!                 |
+-------------------------------------------------+
```

- **UI portion**: Just a web page -- you can use React, Vue, or even plain HTML
- **Backend portion**: Just a Node.js program that can do anything Node.js can do
- They communicate with each other through a mechanism called **IPC (Inter-Process Communication)**

### 1.3 Its Role in NarraNexus

NarraNexus is an AI Agent platform that includes a Python backend + React frontend + Docker database. The desktop application is responsible for:

1. **One-click installation**: Detecting/installing dependencies like uv, Docker, Claude CLI, etc.
2. **Process management**: Starting/stopping/monitoring 4 background Python services
3. **Docker management**: Starting/stopping MySQL containers
4. **Environment configuration**: Managing API Keys in the `.env` file
5. **Health monitoring**: Periodically checking whether each service is running normally

All of these "OS-level" operations (spawning child processes, checking ports, managing Docker) are handled by Electron's Main Process (Node.js side). The beautiful UI that the user sees is rendered by the Renderer Process (Chromium side) using React.

---

## Chapter 2: The Three-Process Model in Detail

This is the most important chapter for understanding Electron. Much of the confusion Electron newcomers face comes from not being clear about "where exactly is my code running?"

### 2.1 What Are the Three Processes

```
+----------------------------------------------------------+
|                     Electron Application                   |
|                                                            |
|  +-----------------------------------------------------+  |
|  |                 Main Process                          |  |
|  |          (src/main/index.ts)                          |  |
|  |                                                       |  |
|  |  - Runtime environment: Node.js                       |  |
|  |  - Capabilities: File I/O, spawn child processes,     |  |
|  |    manage windows                                     |  |
|  |  - Quantity: Only 1                                   |  |
|  |  - Analogy: Backend server                            |  |
|  +------------------------+------------------------------+  |
|                           |                                 |
|              Preload script executes here                   |
|                           |                                 |
|  +------------------------v------------------------------+  |
|  |               Preload Script                           |  |
|  |          (src/preload/index.ts)                         |  |
|  |                                                        |  |
|  |  - Runtime environment: Special Node.js sandbox        |  |
|  |  - Capabilities: Limited Node.js API + contextBridge   |  |
|  |  - Responsibility: Act as a secure middleman,          |  |
|  |    exposing a whitelisted API                          |  |
|  |  - Analogy: API Gateway                                |  |
|  +------------------------+------------------------------+  |
|                           |                                 |
|              Exposes API via contextBridge                  |
|                           |                                 |
|  +------------------------v------------------------------+  |
|  |              Renderer Process                          |  |
|  |          (src/renderer/App.tsx)                         |  |
|  |                                                        |  |
|  |  - Runtime environment: Chromium (just a browser)      |  |
|  |  - Capabilities: HTML/CSS/JS, React rendering          |  |
|  |  - Limitation: Cannot directly access Node.js API      |  |
|  |  - Analogy: Frontend web page                          |  |
|  +--------------------------------------------------------+  |
|                                                            |
+------------------------------------------------------------+
```

### 2.2 Why Three Processes Are Needed

You might ask: wouldn't two be enough? Main handles the backend, Renderer handles the UI -- why add a Preload?

The answer is **security**.

Imagine this: if the Renderer (web page) could directly call Node.js's `fs.rmSync('/')` or `child_process.exec('rm -rf /')`, any XSS vulnerability would become a system-level disaster. Early Electron applications (like older versions of VS Code) did allow the Renderer to access Node.js directly, which introduced enormous security risks.

Modern Electron's security model:

| Setting | Value | Meaning |
|---------|-------|---------|
| `contextIsolation` | `true` | Preload and Renderer JS contexts are completely isolated |
| `nodeIntegration` | `false` | Cannot use `require('fs')` in the Renderer |
| `sandbox` | `false`/`true` | Controls whether Preload can use Node.js APIs |

In NarraNexus's `main/index.ts`:

```typescript
// desktop/src/main/index.ts
const win = new BrowserWindow({
  webPreferences: {
    preload: join(__dirname, '../preload/index.js'),
    sandbox: false,         // Preload can use Node.js APIs
    contextIsolation: true, // Preload and Renderer JS contexts are isolated
    nodeIntegration: false  // Cannot use Node.js in the Renderer
  }
})
```

The combined effect of these three settings:

- The Renderer (React page) can only use `window.nexus.xxx()` to call whitelisted APIs exposed by Preload
- Preload can use `ipcRenderer` and `contextBridge`, but does not handle business logic directly
- The Main Process is where the actual work happens

**Analogy**:

```
Renderer  =  Mobile App (can only see the UI, tap buttons)
Preload   =  API Gateway (only exposes safe interfaces, does forwarding)
Main      =  Backend Server (actually executes operations: file I/O, process spawning)
```

### 2.3 Responsibilities of Each Process in NarraNexus

**Main Process (`src/main/`)**:
- `index.ts` -- Creates windows, manages application lifecycle
- `process-manager.ts` -- Uses `child_process.spawn` to start Python backend services
- `docker-manager.ts` -- Calls `docker compose` to manage containers
- `dependency-checker.ts` -- Detects whether uv, Node.js, Docker are installed
- `health-monitor.ts` -- Periodically checks port and HTTP health status
- `env-manager.ts` -- Reads and writes the `.env` configuration file
- `shell-env.ts` -- Parses macOS login shell environment variables
- `store.ts` -- JSON file persistent storage
- `tray-manager.ts` -- System tray icon and menu
- `ipc-handlers.ts` -- IPC request handler registration center

**Preload Script (`src/preload/`)**:
- `index.ts` -- Exposes Main Process capabilities to the Renderer as a secure API

**Renderer Process (`src/renderer/`)**:
- `App.tsx` -- React root component
- `pages/SetupWizard.tsx` -- Initial setup wizard page
- `pages/Dashboard.tsx` -- Main control panel page
- `components/` -- UI components (ServiceCard, LogViewer, etc.)

---

## Chapter 3: IPC Communication Mechanism

### 3.1 What is IPC

IPC = Inter-Process Communication. Because Main and Renderer are two separate processes, they cannot directly call each other's functions -- they must communicate through IPC channels provided by Electron.

It is like two people talking through a wall -- they must use a walkie-talkie.

### 3.2 Two Communication Patterns

```
Pattern 1: invoke / handle (Request-Response, similar to HTTP)
=============================================

  Renderer                    Main
  +------+    invoke          +------+
  |      | --------------->   |      |
  |      |    "Start service" |      |  Processing...
  |      |                    |      |
  |      |    Returns Promise |      |
  |      | <---------------   |      |
  +------+  { success: true } +------+


Pattern 2: send / on (One-way push, similar to WebSocket)
=============================================

  Main                        Renderer
  +------+    send            +------+
  |      | --------------->   |      |
  |      |  "New log arrived" |      |  Update UI...
  |      |                    |      |
  |      |    send            |      |
  |      | --------------->   |      |
  |      |  "Another new log" |      |  Update UI...
  +------+                    +------+
```

### 3.3 Complete Chain Walkthrough: `window.nexus.startAllServices()`

Below we walk through the complete IPC chain using NarraNexus's real "start all services" feature, from beginning to end.

#### Step 1: Define channel names (`shared/ipc-channels.ts`)

First, Main and Preload need to agree on a "channel name". Just like walkie-talkies need to be tuned to the same frequency.

```typescript
// desktop/src/shared/ipc-channels.ts
export const IPC = {
  SERVICE_START_ALL: 'service-start-all',  // <-- This is the channel name
  ON_LOG: 'on-log',
  // ... other channels
} as const
```

This file is placed in the `shared/` directory so both Main and Preload can import it. The reason for using constants instead of hardcoded strings is to avoid typos (if you write `'service-start-al'` with a missing "l" somewhere, you might spend hours debugging).

#### Step 2: Register handler on the Main side (`main/ipc-handlers.ts`)

The Main Process needs to "listen" on this channel and execute the corresponding action when a message is received:

```typescript
// desktop/src/main/ipc-handlers.ts
import { ipcMain } from 'electron'
import { IPC } from './constants'

export function registerIpcHandlers(processManager, healthMonitor, mainWindow) {
  // Register handler: when Renderer calls invoke('service-start-all'),
  // execute processManager.startAll() and return the result
  ipcMain.handle(IPC.SERVICE_START_ALL, async () => {
    await processManager.startAll()
    return { success: true }
  })

  // Register event forwarding: one-way push from Main to Renderer
  processManager.on('log', (entry) => {
    mainWindow.webContents.send(IPC.ON_LOG, entry)
  })
}
```

`ipcMain.handle()` is like `app.get('/api/start', handler)` in Express -- it registers a request handler.

#### Step 3: Preload bridges the gap (`preload/index.ts`)

Preload wraps `ipcRenderer.invoke()` into what looks like a regular function and exposes it to the Renderer via `contextBridge`:

```typescript
// desktop/src/preload/index.ts
import { contextBridge, ipcRenderer } from 'electron'
import { IPC } from '../shared/ipc-channels'

const nexusAPI = {
  // Request-response pattern: call and wait for Main to return a result
  startAllServices: () => ipcRenderer.invoke(IPC.SERVICE_START_ALL),

  // Event listener pattern: register a callback that fires when Main pushes data
  onLog: (callback) => {
    const handler = (_event, entry) => callback(entry)
    ipcRenderer.on(IPC.ON_LOG, handler)
    // Return an unsubscribe function (to prevent memory leaks)
    return () => ipcRenderer.removeListener(IPC.ON_LOG, handler)
  }
}

// Key! Mount the nexusAPI object onto window.nexus in the Renderer
contextBridge.exposeInMainWorld('nexus', nexusAPI)
```

`contextBridge.exposeInMainWorld('nexus', nexusAPI)` does something clever: it attaches a `nexus` property to the Renderer's `window` object, but the Renderer can only call these functions -- it cannot access the `ipcRenderer` object itself. This is what "whitelisted API" means.

#### Step 4: Renderer calls the API (in React components)

Now React components in the Renderer can use it just like calling a regular function:

```typescript
// desktop/src/renderer/pages/Dashboard.tsx
const handleStartAll = async () => {
  // Just like calling a regular async function!
  const result = await window.nexus.startAllServices()
  console.log('Start result:', result)  // { success: true }
}

// Listen for log pushes
useEffect(() => {
  const unsubscribe = window.nexus.onLog((entry) => {
    console.log('New log:', entry.message)
  })
  return () => unsubscribe()  // Unsubscribe when the component unmounts
}, [])
```

#### Complete Chain Diagram

```
  Renderer (React)            Preload                Main (Node.js)
  ===============          ===============         ===================
        |                        |                        |
        |  window.nexus          |                        |
        |  .startAllServices()   |                        |
        | --------------------->  |                        |
        |                        |  ipcRenderer.invoke    |
        |                        |  ('service-start-all') |
        |                        | --------------------->  |
        |                        |                        |
        |                        |              ipcMain.handle(...)
        |                        |              processManager.startAll()
        |                        |              spawn('uv', ['run', ...])
        |                        |                        |
        |                        |    Returns Promise     |
        |                        | <---------------------  |
        |  { success: true }     |                        |
        | <---------------------  |                        |
        |                        |                        |
        |                        |                   (Log produced...)
        |                        |                   mainWindow.webContents
        |                        |   ipcRenderer.on      .send('on-log', entry)
        |                        | <---------------------  |
        |  onLog callback(entry) |                        |
        | <---------------------  |                        |
        |                        |                        |
```

### 3.4 Communication Pattern Reference Table

| Direction | API | Preload Side | Main Side | Use Case |
|-----------|-----|-------------|-----------|----------|
| Renderer -> Main (Request-Response) | `ipcRenderer.invoke(channel, ...args)` | Wrapped as `nexusAPI.xxx()` | `ipcMain.handle(channel, handler)` | Start services, check dependencies, read/write configuration |
| Main -> Renderer (One-way push) | `ipcRenderer.on(channel, handler)` | Wrapped as `nexusAPI.onXxx(callback)` | `mainWindow.webContents.send(channel, data)` | Log pushes, health status updates, installation progress |

---

## Chapter 4: Project Directory Structure Design

### 4.1 Complete Directory Structure

```
desktop/
├── src/
│   ├── main/                    # Main Process (Node.js side)
│   │   ├── index.ts             # App entry, window creation, lifecycle
│   │   ├── constants.ts         # Path constants, port constants, service definitions
│   │   ├── ipc-handlers.ts      # IPC request handler registration center
│   │   ├── process-manager.ts   # Background service process management (spawn/kill)
│   │   ├── docker-manager.ts    # Docker container management
│   │   ├── dependency-checker.ts# System dependency detection (uv/Node/Docker)
│   │   ├── health-monitor.ts    # Service health status polling
│   │   ├── env-manager.ts       # .env file read/write
│   │   ├── shell-env.ts         # macOS shell environment variable parsing
│   │   ├── store.ts             # JSON persistent storage
│   │   └── tray-manager.ts      # System tray icon + menu
│   │
│   ├── preload/                 # Preload Script (security bridge)
│   │   └── index.ts             # contextBridge API exposure
│   │
│   ├── shared/                  # Code shared between Main and Preload
│   │   └── ipc-channels.ts      # IPC channel name constants
│   │
│   └── renderer/                # Renderer Process (React UI)
│       ├── index.html           # HTML entry point
│       ├── main.tsx             # React mount point
│       ├── App.tsx              # Root component (routing/state logic)
│       ├── env.d.ts             # Global type declarations (window.nexus)
│       ├── pages/               # Page components
│       │   ├── SetupWizard.tsx  # Initial setup wizard
│       │   └── Dashboard.tsx    # Main control panel
│       ├── components/          # UI components
│       │   ├── StepIndicator.tsx
│       │   ├── ServiceCard.tsx
│       │   └── LogViewer.tsx
│       └── styles/
│           └── index.css        # Tailwind CSS entry point
│
├── resources/                   # Build resources (icons, etc.)
│   ├── icon.icns                # macOS icon
│   └── icon.png                 # Universal icon
│
├── electron.vite.config.ts      # electron-vite build configuration
├── electron-builder.yml         # electron-builder packaging configuration
├── package.json                 # Dependencies and scripts
├── tsconfig.json                # TypeScript root config (references)
├── tsconfig.node.json           # TS config for Main + Preload
├── tsconfig.web.json            # TS config for Renderer
├── tailwind.config.js           # Tailwind CSS configuration
└── postcss.config.js            # PostCSS configuration
```

### 4.2 Why This Organization

Core principle: **Organize directories by process boundaries**.

```
src/
├── main/      <-- Runs in Node.js, can use fs, child_process, etc.
├── preload/   <-- Runs in a special sandbox, can use ipcRenderer, contextBridge
├── shared/    <-- Shared by Main and Preload, must not depend on any process-specific API
└── renderer/  <-- Runs in Chromium, can only use browser APIs + window.nexus
```

**Why not put all code together?**

Because the code in these three directories runs in **completely different runtime environments**:
- In `main/` you can write `import { app } from 'electron'`, but you cannot write `document.getElementById`
- In `renderer/` you can write `document.getElementById`, but you cannot write `import fs from 'fs'`
- In `shared/` you cannot use either -- it can only contain pure data definitions (constants, types)

If you mix them up, the TypeScript compiler will throw errors and the electron-vite build will fail. The directory structure itself serves as a form of "compile-time enforced isolation".

### 4.3 The Power of `shared/`

`shared/ipc-channels.ts` only exports pure data constants and does not depend on any Electron API:

```typescript
// desktop/src/shared/ipc-channels.ts
export const IPC = {
  CHECK_DEPENDENCIES: 'check-dependencies',
  SERVICE_START_ALL: 'service-start-all',
  ON_LOG: 'on-log',
  // ...
} as const
```

It is imported in two places:
- `main/ipc-handlers.ts` uses it to register `ipcMain.handle(IPC.SERVICE_START_ALL, ...)`
- `preload/index.ts` uses it to send `ipcRenderer.invoke(IPC.SERVICE_START_ALL)`

This ensures that both sides use the **exact same string constants** -- change one place and both sides automatically stay in sync.

> Note: Code in `shared/` **cannot** be imported directly by `renderer/`. Because the Renderer is a pure browser environment handled by a separate Vite build configuration. If the Renderer needs IPC channel names, it doesn't need to know them at all -- Preload has already encapsulated the IPC details, and the Renderer only needs to call `window.nexus.xxx()`.

---

## Chapter 5: electron-vite Build System

### 5.1 Why electron-vite is Needed

The native Electron development experience is poor:

- Main Process TypeScript needs manual compilation with `tsc`
- The Renderer requires you to configure Webpack or Vite yourself
- Changing Main code during development requires a manual restart
- Build configurations for the three "processes" are completely independent, with high maintenance costs

**electron-vite** unifies all three:

```
electron-vite = Vite(Main) + Vite(Preload) + Vite(Renderer)
```

One configuration file, three build segments, one command handles both development and builds.

### 5.2 Configuration in Detail

```typescript
// desktop/electron.vite.config.ts

import { resolve } from 'path'
import { defineConfig, externalizeDepsPlugin } from 'electron-vite'
import react from '@vitejs/plugin-react'
import tailwindcss from 'tailwindcss'
import autoprefixer from 'autoprefixer'

export default defineConfig({
  // ============================================
  // Segment 1: Main Process build configuration
  // ============================================
  main: {
    plugins: [externalizeDepsPlugin()],
    build: {
      rollupOptions: {
        input: {
          index: resolve(__dirname, 'src/main/index.ts')
        }
      }
    }
  },

  // ============================================
  // Segment 2: Preload Script build configuration
  // ============================================
  preload: {
    plugins: [externalizeDepsPlugin()],
    build: {
      rollupOptions: {
        input: {
          index: resolve(__dirname, 'src/preload/index.ts')
        }
      }
    }
  },

  // ============================================
  // Segment 3: Renderer Process build configuration
  // ============================================
  renderer: {
    root: resolve(__dirname, 'src/renderer'),
    build: {
      rollupOptions: {
        input: {
          index: resolve(__dirname, 'src/renderer/index.html')
        }
      }
    },
    plugins: [react()],
    css: {
      postcss: {
        plugins: [
          tailwindcss(),
          autoprefixer()
        ]
      }
    }
  }
})
```

### 5.3 What Each Segment Does

| Segment | Input | Output | Runtime Environment | Special Handling |
|---------|-------|--------|--------------------| ----------------|
| `main` | `src/main/index.ts` | `out/main/index.js` | Node.js | `externalizeDepsPlugin` |
| `preload` | `src/preload/index.ts` | `out/preload/index.js` | Node.js (sandbox) | `externalizeDepsPlugin` |
| `renderer` | `src/renderer/index.html` | `out/renderer/index.html` + JS | Chromium | React plugin + Tailwind |

Compiled output structure:

```
out/
├── main/
│   └── index.js          # Main Process compiled output
├── preload/
│   └── index.js          # Preload compiled output
└── renderer/
    ├── index.html         # Renderer HTML
    ├── assets/
    │   ├── index-xxxxx.js # Renderer JS (React bundled)
    │   └── index-xxxxx.css# Styles
    └── ...
```

### 5.4 What is `externalizeDepsPlugin`

This plugin does something very important: **it tells Vite not to bundle Node.js native modules and electron into the output bundle**.

Why is this needed? Vite by default bundles everything that is `import`-ed into a single large JS file. But some modules cannot be bundled:

- `electron` -- Provided at runtime by the Electron framework, not in `node_modules`
- `fs`, `path`, `child_process` -- Node.js built-in modules that cannot be bundled
- Native C++ modules (`.node` files) -- Not JS, Vite cannot process them

`externalizeDepsPlugin` automatically identifies these modules and marks them as "external dependencies":

```javascript
// Before compilation (your source code)
import { app } from 'electron'
import { spawn } from 'child_process'

// After compilation (after externalize processing)
const { app } = require('electron')      // Kept as require, resolved at runtime
const { spawn } = require('child_process')
```

**Only Main and Preload need this plugin**, because only they run in a Node.js environment. The Renderer runs in a browser and should never import these modules.

### 5.5 Special Configuration for the Renderer Segment

The Renderer segment is nearly identical to a regular Vite project configuration:

- `root: resolve(__dirname, 'src/renderer')` -- Tells Vite "your root directory is here, the HTML entry is here"
- `plugins: [react()]` -- Enables JSX/TSX support
- `css.postcss.plugins` -- Tailwind CSS processing

This is why writing React in the Renderer feels exactly like a regular Vite + React project.

### 5.6 The Magic of Development Mode

When running `npm run dev` (i.e., `electron-vite dev`):

1. Vite starts a dev server for the Renderer (e.g., `http://localhost:5173`)
2. electron-vite injects this URL into the environment variable `ELECTRON_RENDERER_URL`
3. The Main Process reads this variable and uses `win.loadURL(process.env.ELECTRON_RENDERER_URL)` to load the page

```typescript
// desktop/src/main/index.ts -- Page loading logic
if (process.env.ELECTRON_RENDERER_URL) {
  win.loadURL(process.env.ELECTRON_RENDERER_URL)  // Dev mode: load from dev server
} else {
  win.loadFile(join(__dirname, '../renderer/index.html'))  // Production mode: load local file
}
```

This way, during development, modifying React code gives you Vite's HMR (Hot Module Replacement) without restarting the entire Electron app.

---

## Chapter 6: TypeScript Configuration Strategy

### 6.1 Why 3 tsconfig Files Are Needed

In one sentence: **Because Main and Renderer run in completely different runtime environments, TypeScript needs to know "what APIs can this code use"**.

- The Main Process runs in Node.js, so it can use `fs.readFileSync()`, but it cannot use `document.getElementById()`
- The Renderer Process runs in a browser, so it can use `document.getElementById()`, but it cannot use `fs.readFileSync()`

With just one tsconfig, TypeScript would not know which APIs to suggest -- either everything is suggested (unsafe) or nothing is suggested (unhelpful).

### 6.2 Relationship Between the Three Configuration Files

```
tsconfig.json                    # Root config (references only, no compilation)
├── tsconfig.node.json           # Main + Preload (Node.js environment)
└── tsconfig.web.json            # Renderer (browser environment)
```

The root configuration file is very simple -- it just "links" the other two:

```json
// desktop/tsconfig.json
{
  "files": [],
  "references": [
    { "path": "./tsconfig.node.json" },
    { "path": "./tsconfig.web.json" }
  ]
}
```

`"files": []` means the root config itself does not compile any files -- it is just a "conductor".

### 6.3 `tsconfig.node.json` -- Main + Preload

```json
// desktop/tsconfig.node.json
{
  "compilerOptions": {
    "composite": true,
    "module": "ESNext",
    "moduleResolution": "bundler",
    "target": "ESNext",
    "lib": ["ESNext"],              // <-- Only ESNext, no DOM!
    "outDir": "./out",
    "strict": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "forceConsistentCasingInFileNames": true,
    "resolveJsonModule": true,
    "isolatedModules": true,
    "types": ["node"]               // <-- Loads Node.js type definitions
  },
  "include": ["src/main/**/*", "src/preload/**/*", "src/shared/**/*"]
}
```

Key configuration explained:

| Setting | Value | Meaning |
|---------|-------|---------|
| `lib: ["ESNext"]` | Only includes ECMAScript standard APIs | Cannot write `document.getElementById()` because DOM types are not loaded |
| `types: ["node"]` | Loads `@types/node` | Can write `process.env`, `Buffer`, `require()`, and other Node.js APIs |
| `include` | `src/main/**/*`, `src/preload/**/*`, `src/shared/**/*` | Only manages these three directories |

**Effect**: Writing `fs.readFileSync()` in `src/main/` gives type hints and no errors; writing `document.getElementById()` will produce an error (because there are no DOM types).

### 6.4 `tsconfig.web.json` -- Renderer

```json
// desktop/tsconfig.web.json
{
  "compilerOptions": {
    "composite": true,
    "module": "ESNext",
    "moduleResolution": "bundler",
    "target": "ESNext",
    "lib": ["ESNext", "DOM", "DOM.Iterable"],  // <-- DOM added!
    "outDir": "./out",
    "strict": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "forceConsistentCasingInFileNames": true,
    "resolveJsonModule": true,
    "isolatedModules": true,
    "jsx": "react-jsx",                         // <-- Supports JSX
    "types": []                                  // <-- Empty! Does not load Node types
  },
  "include": ["src/renderer/**/*"]
}
```

Key configuration explained:

| Setting | Value | Meaning |
|---------|-------|---------|
| `lib: ["ESNext", "DOM", "DOM.Iterable"]` | DOM types loaded | Can write `document`, `window`, `HTMLElement`, etc. |
| `types: []` | Empty array, does not load any `@types/*` | Writing `process.env` will produce an error (good! Renderer should not touch Node APIs) |
| `jsx: "react-jsx"` | React 17+ JSX transform | Supports writing JSX in TSX files |
| `include` | `src/renderer/**/*` | Only manages the Renderer directory |

**Effect**: Writing `document.getElementById()` in `src/renderer/` gives type hints; writing `import fs from 'fs'` will produce an error.

### 6.5 Difference Between `lib` and `types`

These two are often confused:

- **`lib`** -- Controls TypeScript's built-in type libraries. `"DOM"` is TypeScript's built-in browser API type definitions, and `"ESNext"` is the ECMAScript standard API. No packages need to be installed.
- **`types`** -- Controls which third-party type definitions are loaded from `node_modules/@types/`. `"node"` refers to the `@types/node` package. Setting it to `[]` means no `@types/*` are loaded automatically.

```
lib: ["ESNext"]           -> Promise, Map, Set, Array, and other JS standard APIs
lib: ["DOM"]              -> document, window, HTMLElement, and other browser APIs
types: ["node"]           -> fs, path, process, Buffer, and other Node.js APIs
types: []                 -> Do not load any @types/*
```

### 6.6 The Role of `composite: true`

This setting enables TypeScript's **Project References** feature. Its benefits are:

1. **Incremental compilation**: Only recompiles the modified project parts
2. **Boundary enforcement**: Code managed by `tsconfig.node.json` cannot import code managed by `tsconfig.web.json`, and vice versa
3. **IDE support**: VS Code can correctly understand the multi-project structure

---

## Chapter 7: Type Safety in the Renderer

### 7.1 The Problem: Where does `window.nexus` come from?

In the Renderer's React code, you can write:

```typescript
const result = await window.nexus.startAllServices()
```

But TypeScript does not know by default that there is a `nexus` property on `window`. If you write it directly, the IDE will show a red underline error:

```
Property 'nexus' does not exist on type 'Window & typeof globalThis'
```

### 7.2 Solution: `env.d.ts`

NarraNexus declares global types in `src/renderer/env.d.ts`:

```typescript
// desktop/src/renderer/env.d.ts

/** Types for the Nexus API exposed by Preload */
interface NexusAPI {
  checkDependencies: () => Promise<DependencyStatus[]>
  startAllServices: () => Promise<{ success: boolean }>
  stopAllServices: () => Promise<{ success: boolean }>
  onLog: (callback: (entry: LogEntry) => void) => () => void
  // ... etc.
}

interface LogEntry {
  serviceId: string
  timestamp: number
  stream: 'stdout' | 'stderr'
  message: string
}

// Extend the Window interface
interface Window {
  nexus: NexusAPI
}
```

### 7.3 How Does This Work?

TypeScript has a "Declaration Merging" mechanism. `Window` is a built-in TypeScript type (from `lib: ["DOM"]`), and you can "append" properties to it in any `.d.ts` file:

```
TypeScript's built-in Window:
  - document
  - location
  - localStorage
  - ...

Your env.d.ts appends:
  + nexus: NexusAPI         <-- Merged in!
```

After merging, TypeScript knows that `window.nexus` exists, with complete type hints:

```typescript
// Now this has full IDE support!
window.nexus.startAllServices()  // Returns Promise<{ success: boolean }>
window.nexus.onLog((entry) => {
  // entry is automatically inferred as LogEntry type
  console.log(entry.serviceId)  // string
  console.log(entry.message)    // string
})
```

### 7.4 Why `.d.ts` Instead of `.ts`

- `.d.ts` files are pure type declaration files that do not contain runtime code and are not compiled to JS
- `.ts` files contain runtime code and are compiled
- Global type extensions (like `interface Window { ... }`) should be placed in `.d.ts` files

### 7.5 How to Keep Types in Sync

Note an important point: the `NexusAPI` interface in `env.d.ts` is **maintained manually**, and it needs to stay in sync with the API actually exposed in `preload/index.ts`. If Preload adds a new API but you forget to update `env.d.ts`, TypeScript will report an error when you try to call that API in Renderer code.

This is actually a **benefit** -- it forces you to update the type declaration whenever you add an API, acting as a kind of "contract".

In NarraNexus, the Preload side also defines a `NexusAPI` interface (to constrain the actual implementation), forming a double guarantee with the declaration in `env.d.ts`:

```typescript
// preload/index.ts -- Interface on the implementation side
export interface NexusAPI {
  startAllServices: () => Promise<{ success: boolean }>
  // ...
}
const nexusAPI: NexusAPI = { ... }  // <-- TypeScript checks if the implementation matches
```

```typescript
// renderer/env.d.ts -- Interface on the consumer side
interface NexusAPI {
  startAllServices: () => Promise<{ success: boolean }>
  // ...
}
```

---

## Chapter 8: Development Mode vs Production Mode

### 8.1 Overview Comparison

| Aspect | Development Mode (`npm run dev`) | Production Mode (packaged .app) |
|--------|----------------------------------|--------------------------------|
| Renderer loading method | `win.loadURL('http://localhost:5173')` | `win.loadFile('out/renderer/index.html')` |
| Project root directory | Repository root (directly writable) | `~/Library/Application Support/NarraNexus/project/` |
| Hot reloading | Vite HMR, changes take effect immediately | None, requires repackaging |
| DevTools | Opens automatically | Does not open |
| Environment variables | Inherits the full `$PATH` from the terminal | Only the minimal launchd environment (macOS) |
| Detection method | `app.isPackaged === false` | `app.isPackaged === true` |

### 8.2 Path Differences in Detail

This is the area most prone to mistakes.

```typescript
// desktop/src/main/constants.ts

// Read-only project directory inside the packaged .app
export const BUNDLED_PROJECT_ROOT = app.isPackaged
  ? join(process.resourcesPath, 'project')  // /path/to/NarraNexus.app/Contents/Resources/project
  : null                                     // Does not exist in dev mode

// Actual writable project directory used at runtime
export const PROJECT_ROOT = app.isPackaged
  ? join(app.getPath('userData'), 'project') // ~/Library/Application Support/NarraNexus/project
  : join(__dirname, '..', '..', '..')        // Dev mode: repository root
```

**Why are there two paths?**

After packaging, the project source code is copied to `.app/Contents/Resources/project/` by `extraResources`. But this location is **read-only** (macOS security restrictions on `.app` bundles). However, our application needs to write `.env` files, create `.venv` virtual environments, and so on.

Solution: On first launch, copy the read-only `Resources/project` to the writable `userData/project`.

```typescript
// desktop/src/main/constants.ts
export function ensureWritableProject(): void {
  if (!app.isPackaged || !BUNDLED_PROJECT_ROOT) return  // Skip in dev mode
  if (existsSync(join(PROJECT_ROOT, 'pyproject.toml'))) return  // Already copied

  cpSync(BUNDLED_PROJECT_ROOT, PROJECT_ROOT, { recursive: true })
}
```

### 8.3 Renderer Loading Differences

```typescript
// desktop/src/main/index.ts

if (process.env.ELECTRON_RENDERER_URL) {
  // Dev mode: electron-vite automatically injects this environment variable
  // Value is something like http://localhost:5173
  win.loadURL(process.env.ELECTRON_RENDERER_URL)
} else {
  // Production mode: load the compiled local HTML file
  win.loadFile(join(__dirname, '../renderer/index.html'))
}

// Open DevTools in dev mode
if (process.env.ELECTRON_RENDERER_URL) {
  win.webContents.openDevTools({ mode: 'detach' })
}
```

### 8.4 Environment Variable Differences (macOS-specific Issue)

On macOS, applications launched by double-clicking a `.app` **do not inherit** the environment variables you set in `~/.zshrc` (such as `$PATH`, API Keys, etc.). This is because `.app` is launched by `launchd`, which only provides a minimal set of environment variables.

But NarraNexus needs to call commands like `uv`, `docker`, `node`, etc., which are typically located in `/usr/local/bin` or `~/.local/bin` -- paths not in launchd's default `$PATH`.

See Chapter 11 for a detailed explanation.

---

## Chapter 9: electron-builder Packaging Configuration in Detail

### 9.1 Complete Configuration Line-by-Line

```yaml
# desktop/electron-builder.yml

# ===============================================
# Basic Information
# ===============================================

appId: com.narranexus.desktop
# The application's unique identifier (reverse domain format)
# macOS uses it to distinguish data storage for different applications
# Affects the path returned by app.getPath('userData')

productName: NarraNexus
# Application display name
# Appears in: macOS menu bar, Dock, installer name, etc.

directories:
  buildResources: resources
  # Build resources directory, contains icons and other files
  # Relative to the desktop/ directory
  output: dist
  # Packaging output directory
  # The final .dmg / .AppImage files will appear in desktop/dist/

# ===============================================
# macOS Configuration
# ===============================================

mac:
  category: public.app-category.developer-tools
  # macOS application category, shown in Finder "Get Info"
  # Common values:
  #   public.app-category.developer-tools  Developer Tools
  #   public.app-category.productivity     Productivity
  #   public.app-category.utilities        Utilities

  target:
    - target: dmg
      arch:
        - universal
  # Package format: DMG (Disk Image)
  # universal = includes both Intel (x64) and Apple Silicon (arm64)
  # A single installer works on all Macs

  icon: resources/icon.icns
  # macOS icon file (.icns format, contains multiple sizes)

  entitlementsInherit: null
  # Application entitlements inheritance setting, null = no sandbox entitlements
  # Needs to be configured if publishing to the App Store

# ===============================================
# DMG Installation Interface Configuration
# ===============================================

dmg:
  title: "NarraNexus"
  # Text displayed in the DMG window title bar when opened

  contents:
    - x: 130
      y: 220
    # Position of the application icon in the DMG window
    # (130, 220) = left side
    - x: 410
      y: 220
      type: link
      path: /Applications
    # Position of the Applications shortcut
    # (410, 220) = right side
    # type: link + path: /Applications = creates a symbolic link to /Applications

# What the user sees when opening the DMG:
#
# +-------------------------------------+
# |        NarraNexus                   |
# |                                     |
# |   [NarraNexus]  ->  [Applications] |
# |                                     |
# |   Drag left to right to install!    |
# +-------------------------------------+

# ===============================================
# Linux Configuration
# ===============================================

linux:
  target:
    - target: AppImage
    # AppImage = single-file executable that runs without installation
    # Double-click to run, no sudo needed
    - target: deb
    # deb = Debian/Ubuntu installation package
    # Can be installed with dpkg -i
  category: Development
  # Linux desktop category

# ===============================================
# Extra Resources (Critical!)
# ===============================================

extraResources:
  - from: "../"
    to: "project"
    filter:
      - "**/*"
      - "!**/node_modules/**"
      - "!**/.venv/**"
      - "!**/.git/**"
      - "!**/desktop/**"
      - "!**/__pycache__/**"
      - "!**/*.pyc"
```

### 9.2 `extraResources` -- Bundling Project Source Code into the App

This is the most critical and often most confusing part.

**What it is**: `extraResources` tells electron-builder "in addition to the Electron application itself, also package these extra files".

**Why it is needed**: NarraNexus Desktop is not just a UI -- it needs to start Python backend services. This means the packaged `.app` must contain the complete Python project source code (`src/`, `backend/`, `frontend/`, `pyproject.toml`, etc.).

**How it works**:

```
Repository structure before packaging:     Internal structure of packaged .app:
NarraNexus/                                NarraNexus.app/Contents/
├── src/            -->                    ├── Resources/
├── backend/        -->                    │   ├── project/        <-- extraResources copies here!
├── frontend/       -->                    │   │   ├── src/
├── pyproject.toml  -->                    │   │   ├── backend/
├── desktop/        X excluded             │   │   ├── frontend/
├── .git/           X excluded             │   │   └── pyproject.toml
├── .venv/          X excluded             │   ├── app.asar        <-- Electron app itself
└── node_modules/   X excluded             │   └── icon.icns
                                           └── MacOS/
                                               └── NarraNexus      <-- Executable file
```

Configuration breakdown:

```yaml
extraResources:
  - from: "../"           # Start from one level above desktop/ (repository root)
    to: "project"         # Copy to Resources/project/
    filter:
      - "**/*"            # Include all files by default
      - "!**/node_modules/**"   # Exclude node_modules (too large, npm install at runtime)
      - "!**/.venv/**"         # Exclude Python virtual environment
      - "!**/.git/**"         # Exclude git history
      - "!**/desktop/**"      # Exclude the desktop directory itself (no nesting)
      - "!**/__pycache__/**"  # Exclude Python cache
      - "!**/*.pyc"           # Exclude compiled Python files
```

### 9.3 Internal Structure of a macOS `.app` Bundle

A macOS `.app` is actually a **folder** (right-click in Finder -> Show Package Contents). The structure generated by electron-builder is as follows:

```
NarraNexus.app/
└── Contents/
    ├── Info.plist              # Application metadata (name, version, icon, etc.)
    ├── PkgInfo                 # Package type identifier
    ├── MacOS/
    │   └── NarraNexus          # Executable file (Electron main program)
    ├── Frameworks/
    │   ├── Electron Framework.framework/   # Chromium + Node.js
    │   └── ...
    └── Resources/
        ├── app.asar            # Your Electron code (main + preload + renderer)
        │                       # Compressed into asar archive format (similar to zip)
        ├── icon.icns           # Application icon
        └── project/            # <-- Copied in by extraResources!
            ├── src/
            ├── backend/
            ├── frontend/
            ├── pyproject.toml
            └── ...
```

**asar format**: electron-builder by default compresses your `out/` output into `app.asar`. This is an archive format created by Electron, similar to zip but readable without extraction. Benefits include:
- Fewer files, faster installation
- Avoids path-too-long issues on Windows
- Provides a degree of source code protection

---

## Chapter 10: Complete Packaging Pipeline

### 10.1 Every Step from Source Code to .dmg

NarraNexus provides a packaging script `build-desktop.sh` that automates the entire process:

```
bash build-desktop.sh
```

The complete pipeline is as follows:

```
+------------------------------------------------------+
| Step 1: check_prerequisites                            |
| Check that Node.js >= 20 is installed                  |
+---------------+--------------------------------------+
                |
+---------------v--------------------------------------+
| Step 2: clean                                          |
| Remove old build artifacts: rm -rf dist/ out/          |
+---------------+--------------------------------------+
                |
+---------------v--------------------------------------+
| Step 3: install_deps                                   |
| cd desktop && npm install                              |
| Install Electron, electron-vite, React, etc.           |
+---------------+--------------------------------------+
                |
+---------------v--------------------------------------+
| Step 4: build_frontend                                 |
| cd frontend && npm run build                           |
| Build NarraNexus's React frontend (note: this is the  |
| project frontend, NOT the desktop Renderer!)           |
| Output: frontend/dist/                                 |
+---------------+--------------------------------------+
                |
+---------------v--------------------------------------+
| Step 5: compile_electron                               |
| cd desktop && npx electron-vite build                  |
| Compile the three parts of Electron code:              |
|   src/main/     -> out/main/index.js                   |
|   src/preload/  -> out/preload/index.js                |
|   src/renderer/ -> out/renderer/index.html + assets    |
+---------------+--------------------------------------+
                |
+---------------v--------------------------------------+
| Step 6: package_app                                    |
| cd desktop && npx electron-builder --mac               |
|                                                        |
| What electron-builder does:                            |
| 1. Downloads prebuilt Electron binaries for the target |
|    platform                                            |
| 2. Packages out/ build output into app.asar            |
| 3. Places app.asar into .app/Contents/Resources/       |
| 4. Copies files specified by extraResources into       |
|    .app/Contents/Resources/project/                    |
| 5. Generates the .dmg disk image                       |
|                                                        |
| Output: desktop/dist/NarraNexus-1.0.0-universal.dmg   |
+------------------------------------------------------+
```

### 10.2 Key Code from build-desktop.sh

```bash
# build-desktop.sh main flow

main() {
  local target="${1:-}"

  # If no platform specified, auto-detect
  if [ -z "$target" ]; then
    target=$(detect_platform)   # Darwin -> mac, Linux -> linux
  fi

  check_prerequisites   # Ensure Node.js >= 20
  clean                 # rm -rf desktop/dist desktop/out
  install_deps          # cd desktop && npm install
  build_frontend        # cd frontend && npm run build (project frontend)
  compile_electron      # cd desktop && npx electron-vite build
  package_app "$target" # cd desktop && npx electron-builder --mac/--linux
}
```

### 10.3 Do Not Confuse the Two "Frontends"

NarraNexus has **two frontends**:

| Frontend | Directory | Purpose | When to Build |
|----------|-----------|---------|--------------|
| Project frontend | `frontend/` | NarraNexus AI Agent's Web UI (React) | Step 4: `build_frontend` |
| Desktop frontend | `desktop/src/renderer/` | Electron desktop app's management interface (React) | Step 5: `compile_electron` |

The project frontend is for **end users** (Agent management page). It is packaged into `.app`'s `Resources/project/frontend/dist/` and served by the Python backend.

The desktop frontend is for **ops/developers** (service management panel). It is part of the Electron Renderer, compiled to `out/renderer/` and packaged into `app.asar`.

### 10.4 Build Artifacts

After packaging, `desktop/dist/` will contain:

macOS:
```
desktop/dist/
├── NarraNexus-1.0.0-universal.dmg     # Installer (distributed to users)
├── NarraNexus-1.0.0-universal-mac.zip # ZIP format (for auto-updates)
├── mac-universal/
│   └── NarraNexus.app/                 # Unpacked application (for debugging)
└── builder-effective-config.yaml       # Actual complete configuration used (for debugging)
```

Linux:
```
desktop/dist/
├── NarraNexus-1.0.0.AppImage          # No-install single file
└── narranexus-desktop_1.0.0_amd64.deb # Debian package
```

---

## Chapter 11: Special Handling for the Production Environment

### 11.1 Read-Only File System Issue

**Problem**: macOS enforces read-only protection on files inside `.app` bundles. After packaging, your project source code is at `.app/Contents/Resources/project/`, which is a read-only directory. But NarraNexus needs to:
- Write to the `.env` file (to store API Keys)
- Create a `.venv` virtual environment (`uv sync` creates this)
- Generate `node_modules` (`npm install` creates this)

**Solution**: `ensureWritableProject()`

```typescript
// desktop/src/main/constants.ts

// Read-only packaged location
export const BUNDLED_PROJECT_ROOT = app.isPackaged
  ? join(process.resourcesPath, 'project')
  : null

// Writable working location
export const PROJECT_ROOT = app.isPackaged
  ? join(app.getPath('userData'), 'project')
  : join(__dirname, '..', '..', '..')

export function ensureWritableProject(): void {
  if (!app.isPackaged || !BUNDLED_PROJECT_ROOT) return
  if (existsSync(join(PROJECT_ROOT, 'pyproject.toml'))) return  // Already copied

  console.log(`Copying bundled project to writable location: ${PROJECT_ROOT}`)
  cpSync(BUNDLED_PROJECT_ROOT, PROJECT_ROOT, { recursive: true })
}
```

Workflow:

```
First launch of the .app:
1. Detects app.isPackaged === true
2. Detects ~/Library/Application Support/NarraNexus/project/ does not exist
3. Copies .app/Contents/Resources/project/ entirely to the above path
4. All subsequent operations (writing .env, creating .venv) happen in the writable directory

Subsequent launches:
1. Detects pyproject.toml already exists
2. Skips copying, uses existing directory directly
```

Location of `app.getPath('userData')` on each platform:

| Platform | Path |
|----------|------|
| macOS | `~/Library/Application Support/NarraNexus/` |
| Linux | `~/.config/NarraNexus/` |
| Windows | `%APPDATA%/NarraNexus/` |

### 11.2 macOS Shell Environment Variable Issue

**Problem**: When a `.app` is launched by double-clicking on macOS, the application **does not inherit** environment variables set in `~/.zshrc` (such as `$PATH`, API Keys, etc.). This is because `.app` is launched by `launchd`, which only provides a minimal set of environment variables.

Specific impact:

```
PATH when launched from terminal:
  /usr/local/bin:/opt/homebrew/bin:/Users/xxx/.local/bin:...

PATH when launched from .app:
  /usr/bin:/bin:/usr/sbin:/sbin
  (Cannot find uv, docker, node!)
```

**Solution**: `shell-env.ts`

This module executes the user's login shell once at application startup to obtain the full set of environment variables:

```typescript
// desktop/src/main/shell-env.ts

let cachedEnv: Record<string, string> | null = null

export async function initShellEnv(): Promise<void> {
  if (process.platform !== 'darwin') {
    // Linux is launched from a terminal and already inherits the full environment
    cachedEnv = { ...process.env } as Record<string, string>
    return
  }

  try {
    const shell = process.env.SHELL || '/bin/zsh'
    // Execute a login shell, use env -0 to print all environment variables (NUL-separated)
    const { stdout } = await execFileAsync(shell, ['-ilc', 'env -0'], {
      timeout: 10000,
      maxBuffer: 10 * 1024 * 1024
    })

    // Parse KEY=VALUE\0KEY=VALUE\0... format
    const parsed: Record<string, string> = {}
    for (const entry of stdout.split('\0')) {
      if (!entry) continue
      const eqIndex = entry.indexOf('=')
      if (eqIndex === -1) continue
      parsed[entry.substring(0, eqIndex)] = entry.substring(eqIndex + 1)
    }

    cachedEnv = parsed
  } catch {
    // On failure, use a fallback: manually add common paths
    cachedEnv = buildFallbackEnv()
  }
}

// All child processes use this environment when spawned
export function getShellEnv(): Record<string, string> {
  return cachedEnv || buildFallbackEnv()
}
```

**How it works**: The meaning of `shell -ilc 'env -0'`:
- `-i` = interactive (interactive mode, loads `~/.zshrc`)
- `-l` = login (login mode, loads `~/.zprofile`)
- `-c` = command (execute the following command)
- `env -0` = print all environment variables, separated by NUL characters (safer than newlines, since values may contain newlines)

**Usage**: All `child_process.spawn()` and `execFile()` calls pass `getShellEnv()` as the `env` parameter:

```typescript
// desktop/src/main/process-manager.ts
const proc = spawn(svc.command, svc.args, {
  cwd,
  env: getShellEnv(),  // <-- Use the parsed full environment
  detached: true
})
```

### 11.3 Special Handling for Process Management

The packaged application needs to manage multiple child processes (Python backend, MCP server, etc.), with some production-specific issues:

**1. Process Group Management**

NarraNexus uses `detached: true` to create new process groups, so on exit the entire process group (including child processes of child processes) can be killed using a negative PID:

```typescript
// desktop/src/main/process-manager.ts

const proc = spawn(svc.command, svc.args, {
  detached: true  // Create a new process group
})

// Kill the entire process group when stopping
private killProcessGroup(proc: ChildProcess, signal: NodeJS.Signals): void {
  if (!proc.pid) return
  try {
    process.kill(-proc.pid, signal)  // Negative PID = kill entire process group
  } catch {
    try { proc.kill(signal) } catch {}
  }
}
```

**2. Automatic Crash Restart**

Backend services automatically restart after a crash, using an exponential backoff strategy (to avoid rapid restart loops):

```typescript
private async tryAutoRestart(svc: ServiceDef): Promise<void> {
  const count = (this.restartCounts.get(svc.id) ?? 0) + 1
  if (count > MAX_RESTART_ATTEMPTS) return  // Maximum 3 restarts

  const waitMs = RESTART_BACKOFF_BASE * Math.pow(2, count - 1)
  // 1st attempt waits 1 second, 2nd waits 2 seconds, 3rd waits 4 seconds
  await this.delay(waitMs)
  this.spawnProcess(svc)
}
```

**3. Port Conflict Handling**

Before starting, check if ports are occupied and prompt the user with a dialog to confirm whether to terminate the occupying process:

```typescript
private async killStalePorts(): Promise<void> {
  for (const { port, label } of portsToCheck) {
    const { stdout } = await execFileAsync('lsof', ['-ti', `:${port}`])
    // If a process is occupying the port, show a dialog
    const { response } = await dialog.showMessageBox({
      type: 'warning',
      title: 'Port Conflict',
      message: 'The following ports are occupied by other processes. Terminate them?',
      buttons: ['Terminate and Continue', 'Skip']
    })
  }
}
```

### 11.4 Persistent Storage

The application needs to remember some state (e.g., "has initial setup been completed"). NarraNexus uses a simple JSON file store:

```typescript
// desktop/src/main/store.ts

class SimpleStore {
  private filePath: string
  private data: StoreData

  constructor() {
    this.filePath = join(app.getPath('userData'), 'config.json')
    // Stored at ~/Library/Application Support/NarraNexus/config.json
    this.data = this.load()
  }

  get<K extends keyof StoreData>(key: K): StoreData[K] {
    return this.data[key]
  }

  set<K extends keyof StoreData>(key: K, value: StoreData[K]): void {
    this.data[key] = value
    this.save()  // Write to disk immediately
  }
}

export const store = new SimpleStore()
```

Why not use `electron-store` (a popular Electron storage library)? Because `electron-store` is ESM-only and has compatibility issues with electron-vite's CJS output. Writing your own takes only about 50 lines and has no dependencies.

---

## Chapter 12: How to Create a Similar Project from Scratch

Below is a complete step-by-step tutorial from an empty directory to a packaged `.dmg`.

### Step 1: Initialize the Project

```bash
mkdir my-desktop-app && cd my-desktop-app
npm init -y
```

### Step 2: Install Core Dependencies

```bash
# Electron runtime
npm install -D electron

# electron-vite (build toolchain)
npm install -D electron-vite

# electron-builder (packaging tool)
npm install -D electron-builder

# React ecosystem
npm install -D react react-dom @types/react @types/react-dom

# TypeScript
npm install -D typescript

# Vite plugin
npm install -D @vitejs/plugin-react

# Electron toolkit (optional, provides some utilities)
npm install -D @electron-toolkit/preload @electron-toolkit/utils

# Tailwind CSS (optional)
npm install -D tailwindcss postcss autoprefixer
```

### Step 3: Create the Directory Structure

```bash
mkdir -p src/main src/preload src/shared src/renderer/pages src/renderer/styles resources
```

### Step 4: Configure package.json

```json
{
  "name": "my-desktop-app",
  "version": "1.0.0",
  "main": "./out/main/index.js",
  "scripts": {
    "dev": "electron-vite dev",
    "build": "electron-vite build",
    "build:mac": "electron-vite build && electron-builder --mac",
    "build:linux": "electron-vite build && electron-builder --linux",
    "postinstall": "electron-builder install-app-deps"
  }
}
```

Key notes:
- `"main": "./out/main/index.js"` -- Tells Electron where to find the Main Process entry point
- `"postinstall"` -- Automatically recompiles native modules after installing dependencies (if any)

### Step 5: Create the electron-vite Configuration

```typescript
// electron.vite.config.ts

import { resolve } from 'path'
import { defineConfig, externalizeDepsPlugin } from 'electron-vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  main: {
    plugins: [externalizeDepsPlugin()],
    build: {
      rollupOptions: {
        input: {
          index: resolve(__dirname, 'src/main/index.ts')
        }
      }
    }
  },
  preload: {
    plugins: [externalizeDepsPlugin()],
    build: {
      rollupOptions: {
        input: {
          index: resolve(__dirname, 'src/preload/index.ts')
        }
      }
    }
  },
  renderer: {
    root: resolve(__dirname, 'src/renderer'),
    build: {
      rollupOptions: {
        input: {
          index: resolve(__dirname, 'src/renderer/index.html')
        }
      }
    },
    plugins: [react()]
  }
})
```

### Step 6: Create TypeScript Configuration

```json
// tsconfig.json
{
  "files": [],
  "references": [
    { "path": "./tsconfig.node.json" },
    { "path": "./tsconfig.web.json" }
  ]
}
```

```json
// tsconfig.node.json
{
  "compilerOptions": {
    "composite": true,
    "module": "ESNext",
    "moduleResolution": "bundler",
    "target": "ESNext",
    "lib": ["ESNext"],
    "outDir": "./out",
    "strict": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "resolveJsonModule": true,
    "isolatedModules": true,
    "types": ["node"]
  },
  "include": ["src/main/**/*", "src/preload/**/*", "src/shared/**/*"]
}
```

```json
// tsconfig.web.json
{
  "compilerOptions": {
    "composite": true,
    "module": "ESNext",
    "moduleResolution": "bundler",
    "target": "ESNext",
    "lib": ["ESNext", "DOM", "DOM.Iterable"],
    "outDir": "./out",
    "strict": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "resolveJsonModule": true,
    "isolatedModules": true,
    "jsx": "react-jsx",
    "types": []
  },
  "include": ["src/renderer/**/*"]
}
```

### Step 7: Write IPC Channel Definitions

```typescript
// src/shared/ipc-channels.ts
export const IPC = {
  GREET: 'greet',
  ON_MESSAGE: 'on-message'
} as const
```

### Step 8: Write the Main Process

```typescript
// src/main/index.ts
import { app, BrowserWindow, ipcMain } from 'electron'
import { join } from 'path'
import { IPC } from '../shared/ipc-channels'

let mainWindow: BrowserWindow | null = null

function createWindow(): BrowserWindow {
  const win = new BrowserWindow({
    width: 800,
    height: 600,
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      sandbox: false,
      contextIsolation: true,
      nodeIntegration: false
    },
    show: false
  })

  win.once('ready-to-show', () => win.show())

  if (process.env.ELECTRON_RENDERER_URL) {
    win.loadURL(process.env.ELECTRON_RENDERER_URL)
  } else {
    win.loadFile(join(__dirname, '../renderer/index.html'))
  }

  return win
}

// Register IPC handler
ipcMain.handle(IPC.GREET, async (_event, name: string) => {
  return `Hello, ${name}! From the Main Process`
})

app.whenReady().then(() => {
  mainWindow = createWindow()

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      mainWindow = createWindow()
    }
  })
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit()
  }
})
```

### Step 9: Write the Preload

```typescript
// src/preload/index.ts
import { contextBridge, ipcRenderer } from 'electron'
import { IPC } from '../shared/ipc-channels'

const api = {
  greet: (name: string) => ipcRenderer.invoke(IPC.GREET, name)
}

contextBridge.exposeInMainWorld('api', api)
```

### Step 10: Write the Renderer

```html
<!-- src/renderer/index.html -->
<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <title>My Desktop App</title>
</head>
<body>
  <div id="root"></div>
  <script type="module" src="./main.tsx"></script>
</body>
</html>
```

```typescript
// src/renderer/env.d.ts
interface MyAPI {
  greet: (name: string) => Promise<string>
}
interface Window {
  api: MyAPI
}
```

```typescript
// src/renderer/main.tsx
import React from 'react'
import ReactDOM from 'react-dom/client'

function App() {
  const [message, setMessage] = React.useState('')

  const handleClick = async () => {
    const result = await window.api.greet('World')
    setMessage(result)
  }

  return (
    <div style={{ padding: 20 }}>
      <h1>My Desktop App</h1>
      <button onClick={handleClick}>Say Hello</button>
      <p>{message}</p>
    </div>
  )
}

ReactDOM.createRoot(document.getElementById('root')!).render(<App />)
```

### Step 11: Create electron-builder Configuration

```yaml
# electron-builder.yml
appId: com.example.my-desktop-app
productName: MyDesktopApp
directories:
  buildResources: resources
  output: dist

mac:
  category: public.app-category.developer-tools
  target:
    - target: dmg
      arch:
        - universal

linux:
  target:
    - target: AppImage
  category: Development
```

### Step 12: Develop and Package

```bash
# Development mode (hot reload)
npm run dev

# Package for macOS
npm run build:mac

# Package for Linux
npm run build:linux
```

### Step 13: Verify

In development mode, you should see a window. Clicking the button should display "Hello, World! From the Main Process".

After packaging, find the `.dmg` (macOS) or `.AppImage` (Linux) in the `dist/` directory.

---

## Chapter 13: Common Pitfalls and Troubleshooting

### Pitfall 1: macOS Environment Variables Lost

**Symptom**: The packaged `.app` reports "command not found" when executing commands like `uv`, `docker`, etc. Works perfectly in development mode.

**Cause**: A `.app` launched by double-clicking inherits the minimal `$PATH` from `launchd`, which only includes `/usr/bin:/bin:/usr/sbin:/sbin`.

**Solution**: Refer to NarraNexus's `shell-env.ts` -- execute a login shell at application startup to obtain the full environment.

```typescript
// Core code
const { stdout } = await execFileAsync(shell, ['-ilc', 'env -0'], { timeout: 10000 })
```

### Pitfall 2: asar Read-Only Causes Write Failures

**Symptom**: Writing files after packaging throws "ENOENT" or "EROFS" errors.

**Cause**: `app.asar` is a read-only archive, and files under `process.resourcesPath` are also read-only (macOS `.app` signature protection).

**Solution**: Place any files that need writing under `app.getPath('userData')`:

```typescript
// Always use this path for writable data
const writablePath = join(app.getPath('userData'), 'my-config.json')
```

### Pitfall 3: Paths Differ Between Development and Production Modes

**Symptom**: Resource paths are correct in development mode, but files cannot be found after packaging.

**Cause**: In development mode, `__dirname` points to the source code directory; after packaging, it points inside `app.asar`.

**Solution**: Always use `app.isPackaged` to differentiate:

```typescript
const iconPath = app.isPackaged
  ? join(process.resourcesPath, 'icon.png')     // After packaging
  : join(__dirname, '..', '..', 'resources', 'icon.png')  // Development mode
```

### Pitfall 4: ESM/CJS Compatibility Issues

**Symptom**: Certain npm packages (like `electron-store`, `got`, etc.) throw import errors in electron-vite.

**Cause**: These packages only provide ESM format (`export default`), but electron-vite's Main Process defaults to CJS output (`module.exports`). Mixing them causes `require()` to fail when loading ESM modules.

**Solutions**:

Option A: Implement a simple replacement yourself (NarraNexus's approach)
```typescript
// Write a simple JSON store yourself, replacing electron-store
class SimpleStore {
  private filePath: string
  private data: StoreData
  // ... Done in about 50 lines
}
```

Option B: Mark the package as non-externalized in the electron-vite configuration
```typescript
main: {
  plugins: [externalizeDepsPlugin({ exclude: ['electron-store'] })],
}
```

### Pitfall 5: Cannot Import Renderer Code in Preload

**Symptom**: Importing React components or Renderer utility functions in Preload causes build errors.

**Cause**: Preload is compiled by `tsconfig.node.json` while Renderer is compiled by `tsconfig.web.json`. They are completely isolated compilation contexts.

**Solution**: Place shared code in the `src/shared/` directory, containing only pure data (constants, types) without depending on any environment-specific APIs.

### Pitfall 6: Serialization Limits of `contextBridge.exposeInMainWorld`

**Symptom**: Objects passed through IPC lose their methods (functions), `Date` becomes a string, `Map`/`Set` become empty objects.

**Cause**: IPC communication uses the Structured Clone Algorithm, which cannot transfer functions, Symbols, DOM nodes, etc.

**Solution**: Only pass pure data (JSON-serializable objects):

```typescript
// Do not pass functions
ipcMain.handle('bad', () => {
  return { doSomething: () => {} }  // Functions will be lost!
})

// Only pass pure data
ipcMain.handle('good', () => {
  return { status: 'ok', count: 42, items: ['a', 'b'] }
})
```

### Pitfall 7: Leftover Child Processes

**Symptom**: After closing the Electron application, Python backend services are still running and occupying ports.

**Cause**: `uv run python xxx.py` actually starts two processes: `uv` and `python`. Killing only the `uv` process may leave the `python` child process as an orphan.

**Solution**: Use `detached: true` + process group management (NarraNexus's approach):

```typescript
// Create a new process group at startup
const proc = spawn(cmd, args, { detached: true })

// Kill the entire process group when stopping
process.kill(-proc.pid, 'SIGTERM')  // Negative PID = entire process group
```

### Pitfall 8: `ready-to-show` Event to Avoid White Screen Flash

**Symptom**: A white window appears briefly when the application starts, then the content loads.

**Cause**: `BrowserWindow` is displayed immediately upon creation, but the HTML has not finished loading yet.

**Solution**: Hide on creation, show after loading is complete:

```typescript
const win = new BrowserWindow({
  show: false  // Do not show on creation
})

win.once('ready-to-show', () => {
  win.show()   // Show after HTML has loaded
})
```

### Pitfall 9: macOS -- Closing a Window Does Not Equal Quitting the App

**Symptom**: Clicking the red close button quits the app. The macOS convention is to close the window but keep the app running.

**Solution**: Intercept the `close` event and hide the window instead:

```typescript
// desktop/src/main/index.ts

// Minimize to tray when window is closed, instead of quitting
win.on('close', (event) => {
  if (!app.isQuitting) {
    event.preventDefault()
    win.hide()
  }
})

// macOS: Show window again when clicking the Dock icon
app.on('activate', () => {
  if (mainWindow) mainWindow.show()
})

// Only allow closing when actually quitting
app.on('before-quit', () => {
  app.isQuitting = true
})
```

### Pitfall 10: External Links Open Inside Electron

**Symptom**: Clicking an `<a href="https://..." target="_blank">` link opens it inside the Electron window instead of the system browser.

**Solution**: Intercept new window requests and open them in the system browser:

```typescript
// desktop/src/main/index.ts
win.webContents.setWindowOpenHandler(({ url }) => {
  shell.openExternal(url)  // Open with the system's default browser
  return { action: 'deny' }  // Prevent Electron from opening a new window
})
```

---

## Appendix A: Core File Quick Reference

| File | Process | Responsibility |
|------|---------|----------------|
| `src/main/index.ts` | Main | App entry, window creation, lifecycle management |
| `src/main/constants.ts` | Main | Path/port/service definition constants |
| `src/main/ipc-handlers.ts` | Main | IPC request handler registration center |
| `src/main/process-manager.ts` | Main | Background service process management (spawn/kill/restart) |
| `src/main/docker-manager.ts` | Main | Docker container management |
| `src/main/dependency-checker.ts` | Main | System dependency detection |
| `src/main/health-monitor.ts` | Main | Service health status polling |
| `src/main/env-manager.ts` | Main | .env file read/write and validation |
| `src/main/shell-env.ts` | Main | macOS shell environment variable parsing |
| `src/main/store.ts` | Main | JSON persistent storage |
| `src/main/tray-manager.ts` | Main | System tray icon + menu |
| `src/preload/index.ts` | Preload | contextBridge secure API exposure |
| `src/shared/ipc-channels.ts` | Shared | IPC channel name constants |
| `src/renderer/index.html` | Renderer | HTML entry point |
| `src/renderer/main.tsx` | Renderer | React mount point |
| `src/renderer/App.tsx` | Renderer | Root component, routing/state control |
| `src/renderer/env.d.ts` | Renderer | window.nexus type declarations |
| `electron.vite.config.ts` | Build | electron-vite three-segment build configuration |
| `electron-builder.yml` | Build | electron-builder packaging configuration |
| `tsconfig.node.json` | Build | TypeScript configuration for Main + Preload |
| `tsconfig.web.json` | Build | TypeScript configuration for Renderer |

## Appendix B: Command Quick Reference

```bash
# Development mode (hot reload, DevTools auto-open)
npm run dev

# Compile (no packaging, output in out/ directory)
npm run build

# Package macOS DMG
npm run build:mac

# Package Linux AppImage + deb
npm run build:linux

# One-click packaging (using the script in the project root)
bash build-desktop.sh         # Auto-detect platform
bash build-desktop.sh mac     # Specify macOS
bash build-desktop.sh linux   # Specify Linux
```

## Appendix C: Further Learning Resources

- [Electron Official Documentation](https://www.electronjs.org/docs)
- [electron-vite Documentation](https://electron-vite.org/)
- [electron-builder Documentation](https://www.electron.build/)
- [Electron Security Best Practices](https://www.electronjs.org/docs/latest/tutorial/security)
