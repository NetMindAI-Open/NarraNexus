# admin.conversations.setTeams

## Description
Set the workspaces in an Enterprise grid org that connect to a public or private channel.

## Required scope
`admin.conversations:write`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `channel_id` | string | yes | The encoded `channel_id` to add or remove to workspaces. |
| `team_id` | string | no | The workspace to which the channel belongs. Omit this argument if the channel is a cross-workspace shared channel. |
| `target_team_ids` | string | no | A comma-separated list of workspaces to which the channel should be shared. Not required if the channel is being shared org-wide. |
| `org_channel` | boolean | no | True if channel has to be converted to an org channel |

## Example
```python
slack_cli("admin.conversations.setTeams", {"channel_id": "..."})
```
