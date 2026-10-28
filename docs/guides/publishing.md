# Publishing a plugin

1. `narranexus plugin publish-check .` — manifest valid, `description`, `license`,
   `README.md`, `versions.json` with the current version, built
   `frontend/dist/plugin.js` with `frontend.integrity` (sha256 SRI), `tests/`.
2. Push the repository to GitHub (public).
3. Tag the release with the manifest version (no `v` prefix) and attach:
   `narranexus-plugin.json`, `backend.zip` (the `backend/` package), `plugin.js`
   (and `styles.css`), `versions.json`.
4. Users install with `owner/repo@1.2.0` (release), `owner/repo#ref`
   (repository), or from the official index.
5. To enter the index, open a metadata PR to `protagolabs/narranexus-plugins`
   (`id`, `repo`, `author`, `description`, `tags`, `kinds`). Inclusion is a
   metadata check, not a code review — the factory page says so to users.
   The index repo's layout and its validator are in `examples/index-repo/`.

`narranexus plugin new` writes `.github/workflows/plugin-ci.yml` into the plugin
repo: every push runs the tests and `publish-check`; a tag equal to the manifest
version publishes the release assets of step 3. `narranexus plugin list` shows
each installed plugin's quality level.

## Quality levels

- **bronze**: manifest valid, README, a Release, contract tests pass.
- **silver**: tests, declared `permissions`, `CHANGELOG.md`, `versions.json`.
- **gold**: eval set, i18n in two or more languages, size self-report matches, no
  alpha contract dependency.

## Compatibility

`minAppVersion` and `versions.json` let an older host install the newest
version it supports instead of refusing. The blocklist
(`blocked_versions.json`) can disable a version with a reason; hosts refresh
it daily.
