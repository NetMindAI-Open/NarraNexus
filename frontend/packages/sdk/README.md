# @narranexus/sdk

What a NarraNexus frontend plugin imports: `definePlugin({ activate, deactivate })`, `vitePreset()` (one ESM file, host libraries external and resolved from the host global at runtime) and the `HostAPI` / registry types. The types are the app's own definitions re-exported, so the package and the running host cannot drift. See `docs/guides/getting-started.md` (section 4) for a plugin's `frontend/` layout.
