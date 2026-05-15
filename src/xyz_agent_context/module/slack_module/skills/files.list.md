# files.list

## Description
List for a team, in a channel, or from a user with applied filters.

## Required scope
`files:read`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `user` | string | no | Filter files created by a single user. |
| `channel` | string | no | Filter files appearing in a specific channel, indicated by its ID. |
| `ts_from` | number | no | Filter files created after this timestamp (inclusive). |
| `ts_to` | number | no | Filter files created before this timestamp (inclusive). |
| `types` | string | no | Filter files by type ([see below](#file_types)). You can pass multiple values in the types argument, like `types=spaces,snippets`.The default value is `all`, which does not filter the list. |
| `count` | string | no | — |
| `page` | string | no | — |
| `show_files_hidden_by_limit` | boolean | no | Show truncated file info for files hidden due to being too old, and the team who owns the file being over the file limit. |

## Example
```python
slack_cli("files.list", {})
```
