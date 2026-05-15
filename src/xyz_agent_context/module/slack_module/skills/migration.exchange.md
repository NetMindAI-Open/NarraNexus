# migration.exchange

## Description
For Enterprise Grid workspaces, map local user IDs to global user IDs

## Required scope
`tokens.basic`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `users` | string | yes | A comma-separated list of user ids, up to 400 per request |
| `team_id` | string | no | Specify team_id starts with `T` in case of Org Token |
| `to_old` | boolean | no | Specify `true` to convert `W` global user IDs to workspace-specific `U` IDs. Defaults to `false`. |

## Example
```python
slack_cli("migration.exchange", {"users": "..."})
```
