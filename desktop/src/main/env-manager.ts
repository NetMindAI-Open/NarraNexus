/**
 * @file env-manager.ts
 * @description .env file reading, writing, and validation
 *
 * Manages the .env file in the project root, supporting initialization from .env.example,
 * reading key-value pairs, writing key-value pairs, and validating required fields.
 */

import { readFileSync, writeFileSync, existsSync, copyFileSync } from 'fs'
import { ENV_FILE_PATH, ENV_EXAMPLE_PATH } from './constants'

// ─── Type Definitions ───────────────────────────────────────

export interface EnvConfig {
  [key: string]: string
}

export interface EnvValidation {
  valid: boolean
  missing: string[]
  warnings: string[]
}

/** Required fields */
const REQUIRED_KEYS = ['OPENAI_API_KEY']

/** Optional but recommended fields */
const OPTIONAL_KEYS = ['GOOGLE_API_KEY', 'ADMIN_SECRET_KEY', 'ANTHROPIC_API_KEY', 'ANTHROPIC_BASE_URL']

// ─── Parsing & Serialization ───────────────────────────────────

/** Parse .env file content into key-value pairs */
export function parseEnvContent(content: string): EnvConfig {
  const config: EnvConfig = {}
  const lines = content.split('\n')

  for (const line of lines) {
    const trimmed = line.trim()
    // Skip empty lines and comments
    if (!trimmed || trimmed.startsWith('#')) continue

    const eqIndex = trimmed.indexOf('=')
    if (eqIndex === -1) continue

    const key = trimmed.substring(0, eqIndex).trim()
    let value = trimmed.substring(eqIndex + 1).trim()

    // Strip surrounding quotes
    if ((value.startsWith('"') && value.endsWith('"')) ||
        (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1)
    }

    config[key] = value
  }

  return config
}

/** Serialize key-value pairs to .env file content (preserving comments and structure) */
export function serializeEnv(config: EnvConfig, template?: string): string {
  if (!template) {
    return Object.entries(config)
      .map(([key, value]) => `${key}="${value}"`)
      .join('\n') + '\n'
  }

  // Preserve comment structure based on template file
  const lines = template.split('\n')
  const result: string[] = []
  const writtenKeys = new Set<string>()

  for (const line of lines) {
    const trimmed = line.trim()

    // Preserve empty lines and comments
    if (!trimmed || trimmed.startsWith('#')) {
      result.push(line)
      continue
    }

    const eqIndex = trimmed.indexOf('=')
    if (eqIndex === -1) {
      result.push(line)
      continue
    }

    const key = trimmed.substring(0, eqIndex).trim()
    if (key in config) {
      result.push(`${key}="${config[key]}"`)
      writtenKeys.add(key)
    } else {
      result.push(line)
      writtenKeys.add(key)
    }
  }

  // Append new keys not in the template
  for (const [key, value] of Object.entries(config)) {
    if (!writtenKeys.has(key)) {
      result.push(`${key}="${value}"`)
    }
  }

  return result.join('\n')
}

// ─── Public API ───────────────────────────────────────

/** Ensure .env file exists (copy from .env.example or create empty file) */
export function ensureEnvFile(): void {
  if (existsSync(ENV_FILE_PATH)) return

  if (existsSync(ENV_EXAMPLE_PATH)) {
    copyFileSync(ENV_EXAMPLE_PATH, ENV_FILE_PATH)
  } else {
    writeFileSync(ENV_FILE_PATH, '', 'utf-8')
  }
}

/** Read .env file, return key-value pairs */
export function readEnv(): EnvConfig {
  ensureEnvFile()
  const content = readFileSync(ENV_FILE_PATH, 'utf-8')
  return parseEnvContent(content)
}

function normalizeEnvValue(value: string): string {
  return value.replace(/\0/g, '').replace(/\r?\n/g, ' ')
}

export function sanitizeEnvUpdates(updates: EnvConfig): EnvConfig {
  const allowedKeys = new Set(getEnvFields().map((field) => field.key))
  const sanitized: EnvConfig = {}
  for (const [key, value] of Object.entries(updates)) {
    if (!allowedKeys.has(key) || typeof value !== 'string') continue
    sanitized[key] = normalizeEnvValue(value)
  }
  return sanitized
}

/** Write key-value pairs to .env file (merge mode, does not overwrite unspecified keys) */
export function writeEnv(updates: EnvConfig): void {
  ensureEnvFile()
  const current = readEnv()
  const merged = { ...current, ...updates }

  // Try to use .env.example as template to preserve formatting
  let template: string | undefined
  if (existsSync(ENV_EXAMPLE_PATH)) {
    template = readFileSync(ENV_EXAMPLE_PATH, 'utf-8')
  }

  const content = serializeEnv(merged, template)
  writeFileSync(ENV_FILE_PATH, content, 'utf-8')
}

/** Validate .env configuration completeness */
export function validateEnv(): EnvValidation {
  const config = readEnv()
  const missing: string[] = []
  const warnings: string[] = []

  for (const key of REQUIRED_KEYS) {
    if (!config[key] || config[key].trim() === '') {
      missing.push(key)
    }
  }

  for (const key of OPTIONAL_KEYS) {
    if (!config[key] || config[key].trim() === '') {
      warnings.push(key)
    }
  }

  return {
    valid: missing.length === 0,
    missing,
    warnings
  }
}

/** Get all configurable keys and their descriptions */
export function getEnvFields(): Array<{
  key: string
  label: string
  required: boolean
  placeholder: string
}> {
  return [
    {
      key: 'OPENAI_API_KEY',
      label: 'OpenAI API Key',
      required: true,
      placeholder: 'sk-...'
    },
    {
      key: 'GOOGLE_API_KEY',
      label: 'Google API Key',
      required: false,
      placeholder: 'AIza...'
    },
    {
      key: 'NETMIND_API_KEY',
      label: 'NetMind API Key (for EverMemOS)',
      required: false,
      placeholder: 'Your NetMind API key'
    },
    {
      key: 'ANTHROPIC_API_KEY',
      label: 'Anthropic API Key (optional)',
      required: false,
      placeholder: 'sk-ant-... (leave empty to use claude CLI login)'
    },
    {
      key: 'ANTHROPIC_BASE_URL',
      label: 'Anthropic Base URL (optional)',
      required: false,
      placeholder: 'https://api.anthropic.com (leave empty for default)'
    },
    {
      key: 'ADMIN_SECRET_KEY',
      label: 'Admin Secret Key',
      required: false,
      placeholder: 'Custom admin secret'
    },
    {
      key: 'DB_HOST',
      label: 'Database Host',
      required: false,
      placeholder: '127.0.0.1'
    },
    {
      key: 'DB_PORT',
      label: 'Database Port',
      required: false,
      placeholder: '3306'
    },
    {
      key: 'DB_NAME',
      label: 'Database Name',
      required: false,
      placeholder: 'xyz_agent_context'
    },
    {
      key: 'DB_USER',
      label: 'Database User',
      required: false,
      placeholder: 'root'
    },
    {
      key: 'DB_PASSWORD',
      label: 'Database Password',
      required: false,
      placeholder: 'xyz_root_pass'
    }
  ]
}
