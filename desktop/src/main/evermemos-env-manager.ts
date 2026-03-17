/**
 * @file evermemos-env-manager.ts
 * @description EverMemOS .env file reading, writing, validation, and field metadata
 *
 * Manages .evermemos/.env file, reusing parser utilities from env-manager.
 * Provides grouped field metadata for the settings UI to render forms.
 */

import { readFileSync, writeFileSync, existsSync, copyFileSync } from 'fs'
import { join } from 'path'
import { EVERMEMOS_DIR } from './constants'
import { parseEnvContent, serializeEnv, EnvConfig, EnvValidation } from './env-manager'

// ─── Paths ─────────────────────────────────────────────

const ENV_PATH = join(EVERMEMOS_DIR, '.env')
const TEMPLATE_PATH = join(EVERMEMOS_DIR, 'env.template')

// ─── Field Metadata Types ───────────────────────────────────

export interface EverMemOSEnvField {
  key: string
  label: string
  required: boolean
  placeholder: string
  inputType: 'text' | 'password' | 'select'
  options?: string[]
  group: 'llm' | 'vectorize' | 'rerank' | 'infrastructure' | 'other'
  order: number
}

// ─── Field Definitions ─────────────────────────────────────────

const FIELDS: EverMemOSEnvField[] = [
  // ── LLM ──
  { key: 'LLM_PROVIDER', label: 'LLM Provider', required: false, placeholder: 'openai', inputType: 'select', options: ['openai', 'anthropic', 'google'], group: 'llm', order: 1 },
  { key: 'LLM_MODEL', label: 'LLM Model', required: false, placeholder: 'x-ai/grok-4-fast', inputType: 'text', group: 'llm', order: 2 },
  { key: 'LLM_BASE_URL', label: 'LLM Base URL', required: false, placeholder: 'https://openrouter.ai/api/v1', inputType: 'text', group: 'llm', order: 3 },
  { key: 'LLM_API_KEY', label: 'LLM API Key', required: true, placeholder: 'sk-or-v1-xxxx', inputType: 'password', group: 'llm', order: 4 },
  { key: 'LLM_TEMPERATURE', label: 'Temperature', required: false, placeholder: '0.3', inputType: 'text', group: 'llm', order: 5 },
  { key: 'LLM_MAX_TOKENS', label: 'Max Tokens', required: false, placeholder: '32768', inputType: 'text', group: 'llm', order: 6 },

  // ── Vectorize (Embedding) ──
  { key: 'VECTORIZE_PROVIDER', label: 'Embedding Provider', required: false, placeholder: 'vllm', inputType: 'select', options: ['vllm', 'deepinfra'], group: 'vectorize', order: 1 },
  { key: 'VECTORIZE_API_KEY', label: 'Embedding API Key', required: false, placeholder: 'EMPTY (use EMPTY for vllm)', inputType: 'password', group: 'vectorize', order: 2 },
  { key: 'VECTORIZE_BASE_URL', label: 'Embedding Base URL', required: false, placeholder: 'http://localhost:8000/v1', inputType: 'text', group: 'vectorize', order: 3 },
  { key: 'VECTORIZE_MODEL', label: 'Embedding Model', required: false, placeholder: 'Qwen/Qwen3-Embedding-4B', inputType: 'text', group: 'vectorize', order: 4 },
  { key: 'VECTORIZE_DIMENSIONS', label: 'Vector Dimensions', required: false, placeholder: '1024', inputType: 'text', group: 'vectorize', order: 5 },

  // ── Rerank ──
  { key: 'RERANK_PROVIDER', label: 'Rerank Provider', required: false, placeholder: 'vllm', inputType: 'select', options: ['vllm', 'deepinfra'], group: 'rerank', order: 1 },
  { key: 'RERANK_API_KEY', label: 'Rerank API Key', required: false, placeholder: 'EMPTY (use EMPTY for vllm)', inputType: 'password', group: 'rerank', order: 2 },
  { key: 'RERANK_BASE_URL', label: 'Rerank Base URL', required: false, placeholder: 'http://localhost:12000/v1/rerank', inputType: 'text', group: 'rerank', order: 3 },
  { key: 'RERANK_MODEL', label: 'Rerank Model', required: false, placeholder: 'Qwen/Qwen3-Reranker-4B', inputType: 'text', group: 'rerank', order: 4 },

  // ── Infrastructure ──
  { key: 'REDIS_HOST', label: 'Redis Host', required: false, placeholder: 'localhost', inputType: 'text', group: 'infrastructure', order: 1 },
  { key: 'REDIS_PORT', label: 'Redis Port', required: false, placeholder: '6379', inputType: 'text', group: 'infrastructure', order: 2 },
  { key: 'REDIS_DB', label: 'Redis DB', required: false, placeholder: '8', inputType: 'text', group: 'infrastructure', order: 3 },
  { key: 'MONGODB_HOST', label: 'MongoDB Host', required: false, placeholder: 'localhost', inputType: 'text', group: 'infrastructure', order: 4 },
  { key: 'MONGODB_PORT', label: 'MongoDB Port', required: false, placeholder: '27017', inputType: 'text', group: 'infrastructure', order: 5 },
  { key: 'MONGODB_USERNAME', label: 'MongoDB User', required: false, placeholder: 'admin', inputType: 'text', group: 'infrastructure', order: 6 },
  { key: 'MONGODB_PASSWORD', label: 'MongoDB Password', required: false, placeholder: 'memsys123', inputType: 'password', group: 'infrastructure', order: 7 },
  { key: 'MONGODB_DATABASE', label: 'MongoDB Database', required: false, placeholder: 'memsys', inputType: 'text', group: 'infrastructure', order: 8 },
  { key: 'ES_HOSTS', label: 'Elasticsearch Hosts', required: false, placeholder: 'http://localhost:19200', inputType: 'text', group: 'infrastructure', order: 9 },
  { key: 'MILVUS_HOST', label: 'Milvus Host', required: false, placeholder: 'localhost', inputType: 'text', group: 'infrastructure', order: 10 },
  { key: 'MILVUS_PORT', label: 'Milvus Port', required: false, placeholder: '19530', inputType: 'text', group: 'infrastructure', order: 11 },

  // ── Other ──
  { key: 'MEMORY_LANGUAGE', label: 'Memory Language', required: false, placeholder: 'en', inputType: 'select', options: ['en', 'zh', 'ja', 'ko', 'es', 'fr', 'de'], group: 'other', order: 1 },
  { key: 'LOG_LEVEL', label: 'Log Level', required: false, placeholder: 'INFO', inputType: 'select', options: ['DEBUG', 'INFO', 'WARNING', 'ERROR'], group: 'other', order: 2 }
]

/** Template placeholder values, treated as "not filled" */
const PLACEHOLDER_VALUES = ['sk-or-v1-xxxx', 'xxxxx']

// ─── In-Memory Staging ─────────────────────────────────────────
// When the directory hasn't been cloned yet, user-entered values are staged here and flushed to disk after clone completes

let pendingEnv: EnvConfig = {}

// ─── Public API ─────────────────────────────────────────

/**
 * UI availability: always returns true, SetupWizard always shows the EverMemOS config section.
 * Use isCloned() to check if the directory has actually been cloned.
 */
export function isAvailable(): boolean {
  return true
}

/** Check if .evermemos/ directory has been cloned locally */
export function isCloned(): boolean {
  return existsSync(EVERMEMOS_DIR)
}

/** Ensure .env file exists (initialize from env.template) */
export function ensureEnvFile(): void {
  if (existsSync(ENV_PATH)) return

  if (existsSync(TEMPLATE_PATH)) {
    copyFileSync(TEMPLATE_PATH, ENV_PATH)
  } else {
    writeFileSync(ENV_PATH, '', 'utf-8')
  }
}

/** Read .evermemos/.env, return in-memory staged values when directory doesn't exist */
export function readEnv(): EnvConfig {
  if (!isCloned()) return { ...pendingEnv }
  ensureEnvFile()
  const content = readFileSync(ENV_PATH, 'utf-8')
  return parseEnvContent(content)
}

function normalizeEnvValue(value: string): string {
  return value.replace(/\0/g, '').replace(/\r?\n/g, ' ')
}

export function sanitizeEnvUpdates(updates: EnvConfig): EnvConfig {
  const allowedKeys = new Set(FIELDS.map((field) => field.key))
  const sanitized: EnvConfig = {}
  for (const [key, value] of Object.entries(updates)) {
    if (!allowedKeys.has(key) || typeof value !== 'string') continue
    sanitized[key] = normalizeEnvValue(value)
  }
  return sanitized
}

/** Write key-value pairs; write to in-memory staging when directory doesn't exist */
export function writeEnv(updates: EnvConfig): void {
  if (!isCloned()) {
    pendingEnv = { ...pendingEnv, ...updates }
    return
  }
  ensureEnvFile()
  const current = readEnv()
  const merged = { ...current, ...updates }

  let template: string | undefined
  if (existsSync(TEMPLATE_PATH)) {
    template = readFileSync(TEMPLATE_PATH, 'utf-8')
  }

  const content = serializeEnv(merged, template)
  writeFileSync(ENV_PATH, content, 'utf-8')
}

/**
 * Merge in-memory staged values to the .env file on disk.
 * Called after git clone completes to ensure user-entered values in UI are not lost.
 */
export function flushPendingEnv(): void {
  if (Object.keys(pendingEnv).length === 0) return
  if (!isCloned()) return

  ensureEnvFile()
  const current = (() => {
    const content = readFileSync(ENV_PATH, 'utf-8')
    return parseEnvContent(content)
  })()
  const merged = { ...current, ...pendingEnv }

  let template: string | undefined
  if (existsSync(TEMPLATE_PATH)) {
    template = readFileSync(TEMPLATE_PATH, 'utf-8')
  }

  const content = serializeEnv(merged, template)
  writeFileSync(ENV_PATH, content, 'utf-8')
  pendingEnv = {}
}

/**
 * Check if user has configured LLM_API_KEY (to determine whether to start EverMemOS).
 * Checks both in-memory staged and on-disk values.
 */
export function isConfigured(): boolean {
  const config = readEnv()
  const key = config['LLM_API_KEY']?.trim() ?? ''
  return !!key && !PLACEHOLDER_VALUES.includes(key)
}

/** Validate required fields */
export function validateEnv(): EnvValidation {
  const config = readEnv()
  const missing: string[] = []
  const warnings: string[] = []

  for (const field of FIELDS) {
    const value = config[field.key]?.trim() ?? ''
    if (field.required) {
      if (!value || PLACEHOLDER_VALUES.includes(value)) {
        missing.push(field.key)
      }
    }
  }

  return { valid: missing.length === 0, missing, warnings }
}

/** Return field metadata list */
export function getFields(): EverMemOSEnvField[] {
  return FIELDS
}
