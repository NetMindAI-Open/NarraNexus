# admin.teams.settings.setDescription

## Description
Set the description of a given workspace.

## Required scope
`admin.teams:write`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `team_id` | string | yes | ID for the workspace to set the description for. |
| `description` | string | yes | The new description for the workspace. |

## Example
```python
slack_cli("admin.teams.settings.setDescription", {"team_id": "...", "description": "..."})
```
