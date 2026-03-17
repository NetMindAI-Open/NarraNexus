/**
 * @file constants.ts
 * @description Desktop app constants: ports, paths, service configuration
 */

import { app } from 'electron'
import { join } from 'path'
import { cpSync, existsSync } from 'fs'

// ─── Paths ───────────────────────────────────────────

/**
 * Read-only bundled project source (Resources/project)
 * null in development mode
 */
export const BUNDLED_PROJECT_ROOT = app.isPackaged
  ? join(process.resourcesPath, 'project')
  : null

/**
 * Writable project root directory:
 * - Dev mode: repository root (already writable)
 * - Packaged mode: ~/Library/Application Support/NarraNexus/project/
 *
 * The .app bundle uses a read-only filesystem, unable to write .env or .venv,
 * so on first launch the project is copied to the userData directory.
 */
export const PROJECT_ROOT = app.isPackaged
  ? join(app.getPath('userData'), 'project')
  : join(__dirname, '..', '..', '..')  // out/main/ -> desktop/ -> NarraNexus/

/**
 * Ensure the writable project directory stays in sync with bundled resources
 *
 * On each launch, cpSync performs an overlay merge to keep source code and
 * dependency declarations up to date. User data (.env, .venv) only exists
 * in the target directory and won't be overwritten or deleted.
 */
export function ensureWritableProject(): void {
  if (!app.isPackaged || !BUNDLED_PROJECT_ROOT) return

  console.log(`[constants] Syncing bundled project to: ${PROJECT_ROOT}`)
  cpSync(BUNDLED_PROJECT_ROOT, PROJECT_ROOT, { recursive: true })
  console.log('[constants] Project synced successfully')
}

/** Frontend directory */
export const FRONTEND_DIR = join(PROJECT_ROOT, 'frontend')

/** .env file path */
export const ENV_FILE_PATH = join(PROJECT_ROOT, '.env')

/** .env.example file path */
export const ENV_EXAMPLE_PATH = join(PROJECT_ROOT, '.env.example')

/** Docker Compose file path (MySQL) */
export const DOCKER_COMPOSE_PATH = join(PROJECT_ROOT, 'docker-compose.yaml')

/** EverMemOS Docker Compose file path */
export const EVERMEMOS_COMPOSE_PATH = join(PROJECT_ROOT, '.evermemos', 'docker-compose.yaml')

/** Table management script directory */
export const TABLE_MGMT_DIR = join(
  PROJECT_ROOT,
  'src',
  'xyz_agent_context',
  'utils',
  'database_table_management'
)

// ─── Ports ───────────────────────────────────────────

export const PORTS = {
  MYSQL: 3306,
  BACKEND: 8000,
  MCP_START: 7801,
  MCP_END: 7810,
  NEXUS_MATRIX: 8953,
  EVERMEMOS: 1995
} as const

/** EverMemOS project directory */
export const EVERMEMOS_DIR = join(PROJECT_ROOT, '.evermemos')

/** EverMemOS Git repository URL */
export const EVERMEMOS_GIT_URL = 'https://github.com/NetMindAI-Open/EverMemOS.git'

/** NexusMatrix project directory */
export const NEXUS_MATRIX_DIR = join(PROJECT_ROOT, 'related_project', 'NetMind-AI-RS-NexusMatrix')

/** NexusMatrix Git repository URL */
export const NEXUS_MATRIX_GIT_URL = 'https://github.com/protagolabs/NetMind-AI-RS-NexusMatrix.git'

/** Synapse template directory (committed config templates) */
export const SYNAPSE_TEMPLATE_DIR = join(PROJECT_ROOT, 'deploy', 'synapse')

/** Synapse data directory (runtime, gitignored) */
export const SYNAPSE_DATA_DIR = join(PROJECT_ROOT, 'deploy', 'synapse', 'data')

/** All MCP module ports (7801-7805) */
export const MCP_PORTS = Array.from(
  { length: PORTS.MCP_END - PORTS.MCP_START + 1 },
  (_, i) => PORTS.MCP_START + i
)

// ─── Infrastructure Services (Docker Containers) ─────────────────────

/** Infrastructure service definitions (Docker containers, HealthMonitor checks port health) */
export const INFRA_SERVICES = [
  { id: 'mysql',         label: 'MySQL',         port: 3306,  required: true },
  { id: 'synapse',       label: 'Synapse',       port: 8008,  required: false },
  { id: 'mongodb',       label: 'MongoDB',       port: 27017, required: false },
  { id: 'elasticsearch', label: 'Elasticsearch', port: 19200, required: false },
  { id: 'milvus',        label: 'Milvus',        port: 19530, required: false },
  { id: 'redis',         label: 'Redis',         port: 6379,  required: false }
] as const

// ─── Service Definitions ───────────────────────────────────────

export interface ServiceDef {
  /** Unique service identifier */
  id: string
  /** Display name */
  label: string
  /** Start command */
  command: string
  /** Command arguments */
  args: string[]
  /** Working directory (relative to PROJECT_ROOT) */
  cwd?: string
  /** Health check port (null means no port check) */
  port: number | null
  /** Health check URL (null means port-only check) */
  healthUrl: string | null
  /** Start order (lower = starts first) */
  order: number
  /** Optional service: silently skip when cwd doesn't exist (non-blocking) */
  optional?: boolean
  /** Git repository URL: auto-clone when cwd doesn't exist */
  gitRepo?: string
}

/** All backend service definitions */
export const SERVICES: ServiceDef[] = [
  {
    id: 'backend',
    label: 'Backend API',
    command: 'uv',
    args: ['run', 'uvicorn', 'backend.main:app', '--port', '8000'],
    port: PORTS.BACKEND,
    healthUrl: 'http://localhost:8000/docs',
    order: 1
  },
  {
    id: 'mcp',
    label: 'MCP Server',
    command: 'uv',
    args: ['run', 'python', 'src/xyz_agent_context/module/module_runner.py', 'mcp'],
    port: PORTS.MCP_START,
    healthUrl: null,
    order: 2
  },
  {
    id: 'poller',
    label: 'Module Poller',
    command: 'uv',
    args: ['run', 'python', '-m', 'xyz_agent_context.services.module_poller'],
    port: null,
    healthUrl: null,
    order: 3
  },
  {
    id: 'job-trigger',
    label: 'Job Trigger',
    command: 'uv',
    args: [
      'run',
      'python',
      'src/xyz_agent_context/module/job_module/job_trigger.py'
    ],
    port: null,
    healthUrl: null,
    order: 4
  },
  {
    id: 'nexus-matrix',
    label: 'NexusMatrix Server',
    command: 'uv',
    args: ['run', 'python', '-m', 'nexus_matrix.main'],
    cwd: 'related_project/NetMind-AI-RS-NexusMatrix',
    port: PORTS.NEXUS_MATRIX,
    healthUrl: 'http://localhost:8953/health',
    order: 5,
    gitRepo: 'https://github.com/protagolabs/NetMind-AI-RS-NexusMatrix.git'
  },
  {
    id: 'matrix-trigger',
    label: 'Matrix Trigger',
    command: 'uv',
    args: ['run', 'python', '-m', 'xyz_agent_context.module.matrix_module.matrix_trigger'],
    port: null,
    healthUrl: null,
    order: 6
  },
  {
    id: 'evermemos',
    label: 'EverMemOS',
    command: 'uv',
    args: ['run', 'web'],
    cwd: '.evermemos',
    port: PORTS.EVERMEMOS,
    healthUrl: null,
    order: 7,
    optional: true
  }
]

// ─── Health Check ───────────────────────────────────────

/** Health check polling interval (ms) */
export const HEALTH_CHECK_INTERVAL = 5000

/** Max auto-restart attempts after process crash */
export const MAX_RESTART_ATTEMPTS = 3

/** Restart backoff base (ms), actual wait = BASE * 2^attempt */
export const RESTART_BACKOFF_BASE = 1000

// ─── IPC channels (re-exported from shared module for main process) ───
export { IPC } from '../shared/ipc-channels'
