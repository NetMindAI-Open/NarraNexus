# admin.teams.settings.setName

## Description
Set the name of a given workspace.

## Required scope
`admin.teams:write`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `team_id` | string | yes | ID for the workspace to set the name for. |
| `name` | string | yes | The new name of the workspace. |

## Example
```python
slack_cli("admin.teams.settings.setName", {"team_id": "...", "name": "..."})
```
