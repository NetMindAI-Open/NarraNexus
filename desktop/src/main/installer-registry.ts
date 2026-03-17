/**
 * @file installer-registry.ts
 * @description Phase 2: Independent installers for each dependency
 *
 * Each installer has check() / install() methods and can be individually
 * retried or skipped. Logic extracted from process-manager.ts runAutoSetup.
 */

import { join } from 'path'
import { existsSync } from 'fs'
import { EventEmitter } from 'events'
import {
  PROJECT_ROOT,
  FRONTEND_DIR,
  EVERMEMOS_DIR,
  EVERMEMOS_GIT_URL,
  NEXUS_MATRIX_DIR,
  NEXUS_MATRIX_GIT_URL
} from './constants'
import { initShellEnv } from './shell-env'
import { resetComposeDetection } from './docker-manager'
import * as everMemOSEnv from './evermemos-env-manager'
import { execInProject, execWithPrivileges, spawnWithOutput } from './exec-utils'
import type { InstallerState, InstallerStatus } from '../shared/setup-types'

// ─── Types ───────────────────────────────────────

export interface Installer {
  id: string
  label: string
  /** Dependencies: must be installed before this one */
  dependsOn?: string[]
  /** Whether failure blocks the whole flow */
  blocking: boolean
  /** Check if already installed/ready */
  check(): Promise<boolean>
  /** Run the installation */
  install(onOutput: (line: string) => void): Promise<void>
  /** Manual install URL for fallback */
  manualUrl?: string
}

// ─── Installer Definitions ───────────────────────────

function createUvInstaller(): Installer {
  return {
    id: 'uv',
    label: 'uv (Python package manager)',
    blocking: true,
    manualUrl: 'https://docs.astral.sh/uv/getting-started/installation/',
    async check() {
      try {
        await execInProject('uv', ['--version'], { timeout: 10000 })
        return true
      } catch { return false }
    },
    async install(onOutput) {
      onOutput('Installing uv...')
      await spawnWithOutput('sh', ['-c', 'curl -LsSf https://astral.sh/uv/install.sh | sh'], {
        timeout: 300000, onOutput
      })
      // 刷新 shell 环境缓存，让后续安装器能找到 uv
      await initShellEnv()
    }
  }
}

function createNodeInstaller(): Installer {
  return {
    id: 'node',
    label: 'Node.js',
    blocking: false,
    manualUrl: 'https://nodejs.org/',
    async check() {
      try {
        await execInProject('node', ['--version'], { timeout: 10000 })
        return true
      } catch { return false }
    },
    async install(onOutput) {
      const home = process.env.HOME || ''

      // macOS: 优先使用 Homebrew
      if (process.platform === 'darwin') {
        try {
          await execInProject('brew', ['--version'], { timeout: 5000 })
          onOutput('Installing Node.js via Homebrew...')
          await spawnWithOutput('brew', ['install', 'node'], {
            timeout: 300000, onOutput
          })
          // 刷新 shell 环境缓存，让后续安装器能找到 node/npm
          await initShellEnv()
          return
        } catch { /* Homebrew 不可用，降级到 nvm */ }
      }

      // 通用方案：通过 nvm 安装
      onOutput('Installing nvm...')
      await spawnWithOutput('sh', ['-c',
        'curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash'
      ], { timeout: 120000, onOutput })

      onOutput('Installing Node.js LTS via nvm...')
      const nvmScript = [
        `export NVM_DIR="${home}/.nvm"`,
        '[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"',
        'nvm install --lts'
      ].join(' && ')
      await spawnWithOutput('sh', ['-c', nvmScript], {
        timeout: 300000, onOutput
      })

      // 刷新 shell 环境缓存，让后续安装器能找到 node/npm
      await initShellEnv()
    }
  }
}

function createClaudeInstaller(): Installer {
  return {
    id: 'claude',
    label: 'Claude Code CLI',
    dependsOn: ['node'],
    blocking: false,
    async check() {
      try {
        await execInProject('claude', ['--version'], { timeout: 10000 })
        return true
      } catch { return false }
    },
    async install(onOutput) {
      onOutput('Installing Claude Code...')
      await spawnWithOutput('sh', ['-c', 'curl -fsSL https://claude.ai/install.sh | sh'], {
        timeout: 300000, onOutput
      })
    }
  }
}

function createPythonDepsInstaller(): Installer {
  return {
    id: 'python-deps',
    label: 'Python dependencies',
    dependsOn: ['uv'],
    blocking: true,
    async check() {
      return existsSync(join(PROJECT_ROOT, '.venv'))
    },
    async install(onOutput) {
      onOutput('Running uv sync...')
      await spawnWithOutput('uv', ['sync'], { timeout: 600000, onOutput })
    }
  }
}

function createDockerInstaller(): Installer {
  return {
    id: 'docker',
    label: 'Docker',
    blocking: true,
    manualUrl: 'https://www.docker.com/products/docker-desktop/',
    async check() {
      try {
        await execInProject('docker', ['info'], { timeout: 10000 })
        return true
      } catch { return false }
    },
    async install(onOutput) {
      if (process.platform === 'darwin') {
        await installDockerMacOS(onOutput)
      } else {
        await installDockerLinux(onOutput)
      }
    }
  }
}

/**
 * Get recommended Colima resource allocation based on system specs.
 * Allocates ~50% of system resources, with min/max bounds.
 */
function getColimaResources(): { cpu: number; memory: number } {
  const os = require('os')
  const totalMemGB = Math.floor(os.totalmem() / (1024 * 1024 * 1024))
  const totalCPU = os.cpus().length

  const memory = Math.max(2, Math.min(12, Math.floor(totalMemGB / 2)))
  const cpu = Math.max(2, Math.min(8, Math.floor(totalCPU / 2)))

  console.log(`[installer] System: ${totalMemGB}GB RAM, ${totalCPU} CPUs → Colima: ${memory}GB RAM, ${cpu} CPUs`)
  return { cpu, memory }
}

/**
 * Start Colima: try as regular user first, then retry with admin privileges.
 * Colima needs sudo on macOS for VM networking setup.
 * If Rosetta compatibility error is detected, fail immediately (privileges won't help).
 */
async function startColima(
  colimaCmd: string,
  onOutput: (line: string) => void,
  tag: string
): Promise<void> {
  const { cpu, memory } = getColimaResources()
  const colimaArgs = ['start', '--cpu', String(cpu), '--memory', String(memory)]
  // Apple Silicon + macOS 13+ 使用 Virtualization.framework，无需安装 QEMU
  // Darwin 22.x = macOS 13 Ventura
  if (process.arch === 'arm64' && process.platform === 'darwin') {
    const darwinMajor = parseInt(require('os').release().split('.')[0], 10)
    if (darwinMajor >= 22) {
      colimaArgs.push('--vm-type', 'vz')
    }
  }

  console.log(`[installer] ${tag} Attempting colima start as regular user: ${colimaCmd} ${colimaArgs.join(' ')}`)
  onOutput(`${tag} Starting Colima (${cpu} CPUs, ${memory}GB RAM)...`)
  try {
    await execInProject(colimaCmd, colimaArgs, { timeout: 300000 })
    console.log(`[installer] ${tag} Colima started successfully as regular user`)
    onOutput(`${tag} Colima started`)
    return
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    console.log(`[installer] ${tag} Colima start failed as regular user: ${msg}`)

    // Rosetta / arch mismatch — privileges won't help, fail fast
    if (msg.includes('rosetta') || msg.includes('native arch') || msg.includes('lima compatibility')) {
      console.error(`[installer] ${tag} Colima has architecture mismatch (Rosetta), cannot fix with privileges`)
      onOutput(`${tag} Colima incompatible (Rosetta/arch mismatch), skipping`)
      throw new Error('Colima Rosetta incompatibility')
    }

    onOutput(`${tag} Colima needs admin privileges, retrying with password dialog...`)
  }
  console.log(`[installer] ${tag} Retrying colima start with admin privileges (osascript)`)
  try {
    await execWithPrivileges(`${colimaCmd} ${colimaArgs.join(' ')}`, { timeout: 300000 })
    console.log(`[installer] ${tag} Colima started successfully with admin privileges`)
    onOutput(`${tag} Colima started with admin privileges`)
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    console.error(`[installer] ${tag} Colima start failed even with admin privileges: ${msg}`)
    throw err
  }
}

async function installDockerMacOS(onOutput: (line: string) => void): Promise<void> {
  console.log('[installer] === installDockerMacOS starting ===')
  console.log(`[installer] Platform: ${process.platform}, Arch: ${process.arch}`)

  // Strategy 1: Launch Docker Desktop (use `open -Ra` to find it anywhere, not just /Applications)
  let dockerDesktopFound = false
  try {
    await execInProject('open', ['-Ra', 'Docker'], { timeout: 5000 })
    dockerDesktopFound = true
  } catch { /* Docker Desktop not registered with macOS */ }

  if (dockerDesktopFound) {
    console.log('[installer] [Strategy 1/5] Docker Desktop found via macOS launch services')
    onOutput('[Strategy 1/5] Launching Docker Desktop...')
    try {
      await execInProject('open', ['-a', 'Docker'], { timeout: 10000 })
      for (let i = 0; i < 30; i++) {
        await new Promise((r) => setTimeout(r, 2000))
        try {
          await execInProject('docker', ['info'], { timeout: 5000 })
          console.log('[installer] [Strategy 1/5] Docker Desktop is ready')
          onOutput('Docker Desktop is ready')
          return
        } catch { /* not ready yet */ }
        if (i % 5 === 4) onOutput(`[Strategy 1/5] Waiting for Docker Desktop to start... (${(i + 1) * 2}s)`)
      }
      console.log('[installer] [Strategy 1/5] Docker Desktop launch timed out after 60s')
      onOutput('[Strategy 1/5] Docker Desktop launch timed out, trying next strategy...')
    } catch (err) {
      console.log(`[installer] [Strategy 1/5] Docker Desktop launch error: ${err}`)
      onOutput('[Strategy 1/5] Docker Desktop failed to launch, trying next strategy...')
    }
  } else {
    console.log('[installer] [Strategy 1/5] Docker Desktop not found on this system')
    onOutput('[Strategy 1/5] Docker Desktop not installed, skipping')
  }

  // Strategy 2: Start Colima (only if installed)
  try {
    const colimaVersion = await execInProject('colima', ['version'], { timeout: 5000 })
    console.log(`[installer] [Strategy 2/5] Colima found: ${colimaVersion.stdout.trim()}`)
    onOutput('[Strategy 2/5] Starting existing Colima...')
    await startColima('colima', onOutput, '[Strategy 2/5]')
    await execInProject('docker', ['info'], { timeout: 10000 })
    console.log('[installer] [Strategy 2/5] Colima started, docker info OK')
    onOutput('Colima started successfully')
    return
  } catch (err) {
    console.log(`[installer] [Strategy 2/5] Colima not available or start failed: ${err}`)
    onOutput('[Strategy 2/5] Colima not available, trying next strategy...')
  }

  // Strategy 3: brew install colima + docker (skip if Intel brew on Apple Silicon)
  try {
    const brewVersion = await execInProject('brew', ['--version'], { timeout: 10000 })
    const brewBin = await execInProject('which', ['brew'], { timeout: 5000 })
    const brewPath = brewBin.stdout.trim()
    const isIntelBrewOnArm = process.arch === 'arm64' && brewPath.startsWith('/usr/local')
    console.log(`[installer] [Strategy 3/5] Homebrew found: ${brewVersion.stdout.trim().split('\n')[0]}, path: ${brewPath}, intelOnArm: ${isIntelBrewOnArm}`)

    if (isIntelBrewOnArm) {
      console.log('[installer] [Strategy 3/5] Skipping — Intel Homebrew on Apple Silicon will install x86 binaries (Rosetta incompatible)')
      onOutput('[Strategy 3/5] Skipping Intel Homebrew on Apple Silicon (would cause Rosetta errors)')
    } else {
      onOutput('[Strategy 3/5] Installing Docker via Homebrew (colima + docker CLI)...')
      try {
        // Apple Silicon + macOS 13+ 用 Virtualization.framework，其他情况需要 QEMU
        const brewPackages = ['colima', 'docker', 'docker-compose']
        const darwinMajor3 = parseInt(require('os').release().split('.')[0], 10)
        const useVz3 = process.arch === 'arm64' && darwinMajor3 >= 22
        if (!useVz3) brewPackages.push('qemu')
        await spawnWithOutput('brew', ['install', ...brewPackages], {
          timeout: 600000, onOutput
        })
      } catch (brewErr) {
        // brew link may fail due to permission issues from previous Docker Desktop install
        const brewMsg = brewErr instanceof Error ? brewErr.message : String(brewErr)
        if (brewMsg.includes('Permission denied') || brewMsg.includes('not symlinked')) {
          console.log('[installer] [Strategy 3/5] brew link failed, retrying with link --overwrite...')
          onOutput('[Strategy 3/5] Fixing brew link permissions...')
          try {
            await spawnWithOutput('brew', ['link', '--overwrite', 'docker'], { timeout: 30000, onOutput })
          } catch { /* ignore */ }
          try {
            await spawnWithOutput('brew', ['link', '--overwrite', 'docker-compose'], { timeout: 30000, onOutput })
          } catch { /* ignore */ }
        } else {
          throw brewErr
        }
      }
      console.log('[installer] [Strategy 3/5] brew install done, starting Colima...')
      onOutput('[Strategy 3/5] Starting Colima VM...')
      await startColima('colima', onOutput, '[Strategy 3/5]')
      await execInProject('docker', ['info'], { timeout: 10000 })
      console.log('[installer] [Strategy 3/5] Success — docker info OK')
      onOutput('Docker installed via Homebrew + Colima')
      resetComposeDetection()
      return
    }
  } catch (err) {
    console.error(`[installer] [Strategy 3/5] Failed: ${err}`)
    onOutput('[Strategy 3/5] Homebrew install failed, trying next strategy...')
  }

  // Strategy 4: Install Homebrew (as regular user) + colima + docker
  // Homebrew refuses to run as root, so we CANNOT use execWithPrivileges for it.
  // Instead: prepare /opt/homebrew with privileges, then install Homebrew as regular user.
  // Strategy 4: Install native ARM Homebrew + colima + docker
  // Uses git clone instead of official install script (avoids sudo requirement)
  console.log('[installer] [Strategy 4/5] Starting — install native Homebrew + Docker')
  onOutput('[Strategy 4/5] Installing native Homebrew + Docker...')
  try {
    const brewPrefix = process.arch === 'arm64' ? '/opt/homebrew' : '/usr/local'
    const brewPath = `${brewPrefix}/bin/brew`
    const currentUser = process.env.USER || process.env.LOGNAME || 'nobody'
    console.log(`[installer] [Strategy 4/5] brewPrefix=${brewPrefix}, user=${currentUser}, arch=${process.arch}`)

    if (!existsSync(brewPath)) {
      // 检查 git 是否可用（fresh macOS 没有 Xcode CLI Tools 时 git 不存在）
      try {
        await execInProject('git', ['--version'], { timeout: 5000 })
      } catch {
        console.log('[installer] [Strategy 4/5] git not found, skipping (needs Xcode CLI Tools)')
        onOutput('[Strategy 4/5] git not available, skipping to next strategy...')
        throw new Error('git not found — Xcode CLI Tools not installed')
      }

      // Step 1: Create directory with correct ownership (needs admin privileges)
      console.log(`[installer] [Strategy 4/5] Step 1: Creating ${brewPrefix} with admin privileges`)
      onOutput('[Strategy 4/5] Creating Homebrew directory (admin privileges required)...')
      await execWithPrivileges(
        `mkdir -p "${brewPrefix}" && chown -R ${currentUser}:staff "${brewPrefix}"`,
        { timeout: 30000 }
      )

      // Step 2: Git clone Homebrew (as regular user — no sudo needed)
      console.log('[installer] [Strategy 4/5] Step 2: git clone Homebrew')
      onOutput('[Strategy 4/5] Downloading Homebrew via git...')
      await spawnWithOutput('git', ['clone', '--depth=1', 'https://github.com/Homebrew/brew', brewPrefix], {
        timeout: 300000, onOutput
      })

      // Step 3: Initial brew update
      console.log('[installer] [Strategy 4/5] Step 3: brew update')
      onOutput('[Strategy 4/5] Updating Homebrew...')
      await execInProject(brewPath, ['update', '--force', '--quiet'], { timeout: 300000 })
      console.log('[installer] [Strategy 4/5] Step 3: Homebrew ready')
    } else {
      console.log(`[installer] [Strategy 4/5] Native Homebrew already exists at ${brewPath}`)
      onOutput('[Strategy 4/5] Native Homebrew already installed')
    }

    // Step 4: Install colima + docker CLI
    const s4Packages = ['colima', 'docker', 'docker-compose']
    const darwinMajor4 = parseInt(require('os').release().split('.')[0], 10)
    const useVz4 = process.arch === 'arm64' && darwinMajor4 >= 22
    if (!useVz4) s4Packages.push('qemu')
    console.log(`[installer] [Strategy 4/5] Step 4: brew install ${s4Packages.join(' ')}`)
    onOutput('[Strategy 4/5] Installing colima + docker via Homebrew...')
    await spawnWithOutput(brewPath, ['install', ...s4Packages], {
      timeout: 600000, onOutput
    })
    console.log('[installer] [Strategy 4/5] Step 4: brew install done')

    // Step 5: Start Colima
    const colimaPath = `${brewPrefix}/bin/colima`
    console.log(`[installer] [Strategy 4/5] Step 5: Starting Colima at ${colimaPath}`)
    onOutput('[Strategy 4/5] Starting Colima VM...')
    await startColima(colimaPath, onOutput, '[Strategy 4/5]')
    await execInProject('docker', ['info'], { timeout: 10000 })
    console.log('[installer] [Strategy 4/5] Success — docker info OK')
    onOutput('Docker installed via native Homebrew + Colima')
    resetComposeDetection()
    return
  } catch (err) {
    console.error(`[installer] [Strategy 4/5] Failed: ${err}`)
    onOutput('[Strategy 4/5] Native Homebrew + Docker install failed, trying next strategy...')
  }

  // Strategy 5: Download and install Docker Desktop .dmg directly
  const arch = process.arch === 'arm64' ? 'arm64' : 'amd64'
  const dmgUrl = `https://desktop.docker.com/mac/main/${arch}/Docker.dmg`
  const dmgPath = '/tmp/NarraNexus_Docker.dmg'
  console.log(`[installer] [Strategy 5/5] Downloading Docker Desktop .dmg (arch=${arch})`)
  onOutput(`[Strategy 5/5] Downloading Docker Desktop for ${arch}...`)
  try {
    await spawnWithOutput('curl', ['-fSL', '--progress-bar', '-o', dmgPath, dmgUrl], {
      timeout: 600000, onOutput
    })
    console.log('[installer] [Strategy 5/5] Download complete, mounting dmg...')
    onOutput('[Strategy 5/5] Installing Docker Desktop (admin privileges required)...')
    await execWithPrivileges(
      `hdiutil attach "${dmgPath}" -nobrowse -quiet`
      + ` && cp -R "/Volumes/Docker/Docker.app" /Applications/`
      + ` && hdiutil detach "/Volumes/Docker" -quiet`,
      { timeout: 120000 }
    )
    console.log('[installer] [Strategy 5/5] Docker Desktop installed to /Applications')

    // Clean up dmg
    try { await execInProject('rm', ['-f', dmgPath], { timeout: 5000 }) } catch { /* ignore */ }

    onOutput('[Strategy 5/5] Launching Docker Desktop...')
    await execInProject('open', ['-a', 'Docker'], { timeout: 10000 })

    for (let i = 0; i < 60; i++) {
      await new Promise((r) => setTimeout(r, 2000))
      try {
        await execInProject('docker', ['info'], { timeout: 5000 })
        console.log(`[installer] [Strategy 5/5] Docker Desktop ready after ${(i + 1) * 2}s`)
        onOutput('Docker Desktop installed and ready')
        resetComposeDetection()
        return
      } catch { /* not ready yet */ }
      if (i % 5 === 4) {
        onOutput(`[Strategy 5/5] Waiting for Docker Desktop to initialize... (${(i + 1) * 2}s)`)
      }
    }
    throw new Error('Docker Desktop installed but failed to start within 120s')
  } catch (err) {
    // Clean up on failure
    try {
      await execInProject('sh', ['-c',
        `rm -f "${dmgPath}"; hdiutil detach "/Volumes/Docker" 2>/dev/null || true`
      ], { timeout: 5000 })
    } catch { /* ignore */ }
    console.error(`[installer] [Strategy 5/5] Failed: ${err}`)
    onOutput('[Strategy 5/5] Docker Desktop download/install failed')
  }

  throw new Error('All Docker installation strategies failed. Please install Docker Desktop manually from https://docker.com/products/docker-desktop/')
}

async function installDockerLinux(onOutput: (line: string) => void): Promise<void> {
  // Strategy 1: systemctl start (unprivileged)
  onOutput('[Strategy 1/3] Trying to start Docker daemon...')
  try {
    await execInProject('systemctl', ['start', 'docker'], { timeout: 30000 })
    await execInProject('docker', ['info'], { timeout: 10000 })
    onOutput('Docker daemon started')
    return
  } catch {
    onOutput('[Strategy 1/3] Cannot start Docker without privileges, trying next strategy...')
  }

  // Strategy 2: Privileged systemctl
  onOutput('[Strategy 2/3] Starting Docker daemon (admin privileges required)...')
  try {
    await execWithPrivileges('systemctl start docker', { timeout: 30000 })
    await execInProject('docker', ['info'], { timeout: 10000 })
    onOutput('Docker daemon started with admin privileges')
    return
  } catch {
    onOutput('[Strategy 2/3] Docker not installed or start failed, trying next strategy...')
  }

  // Strategy 3: Install via get.docker.com
  onOutput('[Strategy 3/3] Installing Docker Engine via get.docker.com (admin privileges required)...')
  try {
    const user = process.env.USER || 'root'
    await execWithPrivileges(
      'curl -fsSL https://get.docker.com | sh'
      + ' && (apt-get install -y docker-compose-plugin 2>/dev/null'
      + '    || yum install -y docker-compose-plugin 2>/dev/null'
      + '    || true)'
      + ` && usermod -aG docker ${user}`
      + ' && systemctl start docker',
      { timeout: 600000 }
    )
    resetComposeDetection()
    try {
      await execInProject('docker', ['info'], { timeout: 10000 })
    } catch {
      await execWithPrivileges('docker info', { timeout: 10000 })
    }
    onOutput('Docker Engine installed and started')
    return
  } catch {
    onOutput('[Strategy 3/3] Docker installation failed or cancelled by user')
  }

  throw new Error('Docker installation failed. Please install Docker manually.')
}

function createEverMemosCloneInstaller(): Installer {
  return {
    id: 'evermemos-clone',
    label: 'Clone / Update EverMemOS',
    blocking: false,
    async check() {
      // Always run install() to pull latest — never skip
      return false
    },
    async install(onOutput) {
      if (everMemOSEnv.isCloned()) {
        onOutput('Updating EverMemOS...')
        await spawnWithOutput('git', ['pull', '--ff-only'], {
          cwd: EVERMEMOS_DIR, timeout: 120000, onOutput
        })
        everMemOSEnv.flushPendingEnv()
        return
      }
      onOutput('Cloning EverMemOS repository...')
      await spawnWithOutput('git', ['clone', '--depth', '1', '--progress', EVERMEMOS_GIT_URL, '.evermemos'], {
        timeout: 600000, onOutput
      })
      everMemOSEnv.flushPendingEnv()
    }
  }
}

function createEverMemosDepsInstaller(): Installer {
  return {
    id: 'evermemos-deps',
    label: 'EverMemOS dependencies',
    dependsOn: ['uv', 'evermemos-clone'],
    blocking: false,
    async check() {
      // Always run uv sync to pick up dependency changes
      return false
    },
    async install(onOutput) {
      if (!existsSync(EVERMEMOS_DIR)) {
        throw new Error('EverMemOS directory not found, skipping')
      }
      onOutput('Syncing EverMemOS Python dependencies...')
      await spawnWithOutput('uv', ['sync'], {
        cwd: EVERMEMOS_DIR, timeout: 600000, onOutput
      })
    }
  }
}

function createNexusMatrixCloneInstaller(): Installer {
  return {
    id: 'nexus-matrix-clone',
    label: 'Clone / Update NexusMatrix',
    blocking: false,
    async check() {
      // Always run install() to pull latest — never skip
      return false
    },
    async install(onOutput) {
      if (existsSync(NEXUS_MATRIX_DIR)) {
        onOutput('Updating NexusMatrix...')
        await spawnWithOutput('git', ['pull', '--ff-only'], {
          cwd: NEXUS_MATRIX_DIR, timeout: 120000, onOutput
        })
        return
      }
      onOutput('Cloning NexusMatrix repository...')
      const parentDir = join(NEXUS_MATRIX_DIR, '..')
      const { mkdirSync } = require('fs')
      mkdirSync(parentDir, { recursive: true })
      const dirName = 'NetMind-AI-RS-NexusMatrix'
      await spawnWithOutput('git', ['clone', '--depth', '1', '--progress', NEXUS_MATRIX_GIT_URL, dirName], {
        cwd: parentDir, timeout: 600000, onOutput
      })
    }
  }
}

function createNexusMatrixDepsInstaller(): Installer {
  return {
    id: 'nexus-matrix-deps',
    label: 'NexusMatrix dependencies',
    dependsOn: ['uv', 'nexus-matrix-clone'],
    blocking: false,
    async check() {
      // Always run uv sync to pick up dependency changes
      return false
    },
    async install(onOutput) {
      if (!existsSync(NEXUS_MATRIX_DIR)) {
        throw new Error('NexusMatrix directory not found, skipping')
      }
      onOutput('Syncing NexusMatrix Python dependencies...')
      await spawnWithOutput('uv', ['sync'], {
        cwd: NEXUS_MATRIX_DIR, timeout: 600000, onOutput
      })
    }
  }
}

function createFrontendBuildInstaller(): Installer {
  return {
    id: 'frontend-build',
    label: 'Build frontend',
    dependsOn: ['node'],
    blocking: true,
    async check() {
      return existsSync(join(FRONTEND_DIR, 'dist', 'index.html'))
    },
    async install(onOutput) {
      onOutput('Installing npm packages...')
      await spawnWithOutput('npm', ['install', '--no-audit', '--no-fund'], {
        cwd: FRONTEND_DIR, timeout: 300000, onOutput
      })
      onOutput('Compiling frontend...')
      await spawnWithOutput('npm', ['run', 'build'], {
        cwd: FRONTEND_DIR, timeout: 300000, onOutput
      })
    }
  }
}

// ─── Installer Registry ───────────────────────────────

export class InstallerRegistry extends EventEmitter {
  private installers: Installer[]
  private states = new Map<string, InstallerState>()

  constructor() {
    super()
    this.installers = [
      createUvInstaller(),
      createNodeInstaller(),
      createClaudeInstaller(),
      createPythonDepsInstaller(),
      createDockerInstaller(),
      createNexusMatrixCloneInstaller(),
      createNexusMatrixDepsInstaller(),
      createEverMemosCloneInstaller(),
      createEverMemosDepsInstaller(),
      createFrontendBuildInstaller()
    ]
    for (const inst of this.installers) {
      this.states.set(inst.id, {
        id: inst.id,
        label: inst.label,
        status: 'pending',
        canSkip: !inst.blocking
      })
    }
  }

  /** Get current state of all installers */
  getAllStates(): InstallerState[] {
    return this.installers.map((inst) => this.states.get(inst.id)!)
  }

  /** Install a single dependency by ID */
  async install(id: string): Promise<void> {
    const inst = this.installers.find((i) => i.id === id)
    if (!inst) throw new Error(`Unknown installer: ${id}`)

    this.updateState(id, { status: 'running', currentOutput: '', error: undefined })
    try {
      await inst.install((line) => {
        this.updateState(id, { status: 'running', currentOutput: line })
      })
      this.updateState(id, { status: 'done', currentOutput: undefined })
    } catch (err) {
      const error = err instanceof Error ? err.message : String(err)
      this.updateState(id, { status: 'error', error, currentOutput: undefined })
      throw err
    }
  }

  /** Retry a failed installation */
  async retry(id: string): Promise<void> {
    return this.install(id)
  }

  /** Skip an installer (mark as skipped) */
  skip(id: string): void {
    this.updateState(id, { status: 'skipped' })
  }

  /**
   * Install all missing dependencies in order.
   * Respects dependency ordering (uv before python-deps, etc.)
   * @param missingIds IDs of missing dependencies to install
   */
  async installAll(missingIds: string[]): Promise<{ success: boolean; failedId?: string }> {
    // Filter to only known installers, preserving registry order
    const toInstall = this.installers.filter((inst) => missingIds.includes(inst.id))
    console.log(`[installer] installAll: ${toInstall.length} installers to process: [${toInstall.map(i => i.id).join(', ')}]`)

    for (const inst of toInstall) {
      // Check if already done
      const current = this.states.get(inst.id)
      if (current?.status === 'done' || current?.status === 'skipped') {
        console.log(`[installer] ${inst.id}: already ${current.status}, skipping`)
        continue
      }

      // Check dependencies
      if (inst.dependsOn) {
        const unmet = inst.dependsOn.filter((depId) => {
          const depState = this.states.get(depId)
          return depState?.status !== 'done'
        })
        if (unmet.length > 0) {
          // Skip if dependencies not met and installer is non-blocking
          if (!inst.blocking) {
            console.log(`[installer] ${inst.id}: unmet deps [${unmet.join(', ')}], skipping (non-blocking)`)
            this.skip(inst.id)
            continue
          }
          console.log(`[installer] ${inst.id}: unmet deps [${unmet.join(', ')}] but blocking, proceeding anyway`)
        }
      }

      try {
        // First check if already ready (might have been installed externally)
        console.log(`[installer] ${inst.id}: running check()...`)
        if (await inst.check()) {
          console.log(`[installer] ${inst.id}: check passed, marking done`)
          this.updateState(inst.id, { status: 'done' })
          continue
        }
        console.log(`[installer] ${inst.id}: check failed, running install()...`)
        await this.install(inst.id)
        console.log(`[installer] ${inst.id}: install completed`)
      } catch (err) {
        console.error(`[installer] ${inst.id}: failed:`, err instanceof Error ? err.message : err)
        if (inst.blocking) {
          return { success: false, failedId: inst.id }
        }
        // Non-blocking: continue with others
      }
    }
    console.log(`[installer] installAll: finished`)
    return { success: true }
  }

  private updateState(id: string, partial: Partial<InstallerState>): void {
    const current = this.states.get(id)
    if (!current) return
    const updated = { ...current, ...partial }
    this.states.set(id, updated)
    this.emit('installer-update', updated)
  }
}
