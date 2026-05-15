# admin.teams.settings.setIcon

## Description
Sets the icon of a workspace.

## Required scope
`admin.teams:write`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `image_url` | string | yes | Image URL for the icon |
| `team_id` | string | yes | ID for the workspace to set the icon for. |

## Example
```python
slack_cli("admin.teams.settings.setIcon", {"image_url": "...", "team_id": "..."})
```
