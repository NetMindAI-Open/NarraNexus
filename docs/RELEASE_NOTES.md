# Release notes

One section per version. "Deprecated" entries follow `docs/API_POLICY.md` §4 (since / removal / replacement).

## Unreleased

### Changed
- Plugin platform: every contract kind in `narranexus.contracts.API_VERSIONS` is declared stable per kind in `STABILITY`; `api[kind]` in a manifest is accepted between `MIN_SUPPORTED_VERSIONS[kind]` and `API_VERSIONS[kind]`.
- Plugin isolation prefixes are `ext_<id>__` (tables) and `NXP_<ID>__<KEY>` (settings env); first release with user plugin tables, no migration.

### Deprecated
- `xyz_agent_context` (alias of `narranexus.platform`) — since the plugin platform release, removed at 1.22.0; import `narranexus.platform`.
