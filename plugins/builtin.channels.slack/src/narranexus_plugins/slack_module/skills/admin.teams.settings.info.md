# admin.teams.settings.info

## Description
Fetch information about settings in a workspace

## Required scope
`admin.teams:read`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `team_id` | string | yes | — |

## Example
```python
slack_cli("admin.teams.settings.info", {"team_id": "..."})
```
