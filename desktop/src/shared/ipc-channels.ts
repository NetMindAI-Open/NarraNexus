/**
 * @file ipc-channels.ts
 * @description IPC channel name constants — shared between main and preload
 *
 * Independent of electron.app, safe for preload to import.
 */

export const IPC = {
  // Environment variables
  GET_ENV: 'get-env',
  SET_ENV: 'set-env',
  VALIDATE_ENV: 'validate-env',

  // EverMemOS environment variables
  GET_EVERMEMOS_ENV: 'get-evermemos-env',
  SET_EVERMEMOS_ENV: 'set-evermemos-env',
  VALIDATE_EVERMEMOS_ENV: 'validate-evermemos-env',

  // Docker
  DOCKER_STATUS: 'docker-status',
  DOCKER_START: 'docker-start',
  DOCKER_STOP: 'docker-stop',

  // Service processes
  SERVICE_START_ALL: 'service-start-all',
  SERVICE_STOP_ALL: 'service-stop-all',
  SERVICE_RESTART: 'service-restart',
  SERVICE_STATUS: 'service-status',

  // Health check
  HEALTH_STATUS: 'health-status',
  HEALTH_SUBSCRIBE: 'health-subscribe',

  // Database
  INIT_DATABASE: 'init-database',

  // Three-phase setup
  RUN_PREFLIGHT: 'run-preflight',
  INSTALL_DEP: 'install-dep',
  RETRY_DEP: 'retry-dep',
  SKIP_DEP: 'skip-dep',
  INSTALL_ALL_DEPS: 'install-all-deps',
  RUN_LAUNCH: 'run-launch',
  ON_INSTALLER_UPDATE: 'on-installer-update',
  ON_LAUNCH_STEP: 'on-launch-step',

  // Claude Code authentication
  CLAUDE_AUTH_INFO: 'claude-auth-info',
  CLAUDE_LOGIN_START: 'claude-login-start',
  CLAUDE_LOGIN_CANCEL: 'claude-login-cancel',
  CLAUDE_LOGIN_INPUT: 'claude-login-input',
  CLAUDE_SAVE_SETUP_TOKEN: 'claude-save-setup-token',
  ON_CLAUDE_LOGIN_STATUS: 'on-claude-login-status',

  // EverMemOS lifecycle
  LAUNCH_EVERMEMOS: 'launch-evermemos',
  IS_EVERMEMOS_INSTALLED: 'is-evermemos-installed',

  // Auto-updater
  CHECK_FOR_UPDATES: 'check-for-updates',
  DOWNLOAD_UPDATE: 'download-update',
  INSTALL_UPDATE: 'install-update',
  ON_UPDATE_STATUS: 'on-update-status',

  // Miscellaneous
  OPEN_EXTERNAL: 'open-external',
  GET_SETUP_STATE: 'get-setup-state',
  SET_SETUP_COMPLETE: 'set-setup-complete',
  GET_LOGS: 'get-logs',
  ON_LOG: 'on-log',
  ON_HEALTH_UPDATE: 'on-health-update'
} as const
