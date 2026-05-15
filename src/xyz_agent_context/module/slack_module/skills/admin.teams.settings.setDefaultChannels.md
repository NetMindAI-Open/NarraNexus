# admin.teams.settings.setDefaultChannels

## Description
Set the default channels of a workspace.

## Required scope
`admin.teams:write`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `team_id` | string | yes | ID for the workspace to set the default channel for. |
| `channel_ids` | string | yes | An array of channel IDs. |

## Example
```python
slack_cli("admin.teams.settings.setDefaultChannels", {"team_id": "...", "channel_ids": "..."})
```
