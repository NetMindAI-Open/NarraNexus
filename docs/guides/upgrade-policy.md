# Upgrade and compatibility policy (plugins)

- Contract kinds carry an integer `API_VERSIONS[kind]`; a plugin declares the
  versions it needs under `api` and is `incompatible` (not loaded) on a
  mismatch. Batches 0–5 keep every kind at alpha (0); stability is declared per
  `docs/API_POLICY.md`.
- `minAppVersion` gates the host version; `versions.json` maps plugin
  versions to the minimum host so older hosts pick an older release.
- Upgrading a plugin keeps the last-known-good registry snapshot; `narranexus
  plugin rollback` restores it.
- Uninstall keeps plugin tables by default; data purge is an explicit action.
- The host refreshes the official blocklist daily; a blocked version is
  disabled with its reason shown.
