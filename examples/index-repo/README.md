# narranexus-plugins — the official plugin index (template)

This is the layout of `protagolabs/narranexus-plugins`, the index a NarraNexus host reads (`narranexus.kernel.plugins.install.index`, cached for a day):

- `index.json` — a list of entries `{id, repo, author, description, tags, kinds}`; `repo` is `owner/repo` on GitHub, releases are tagged with the manifest version.
- `blocked_versions.json` — `{plugin id: {version: reason}}`; a listed version is refused at install and at boot.
- `validate_index.py` + the workflow — the metadata check every PR runs. Inclusion is a metadata check, not a code review; the factory page says so to users.

To list a plugin: open a PR adding one entry. To pull a version: add it to the blocklist with a reason.
