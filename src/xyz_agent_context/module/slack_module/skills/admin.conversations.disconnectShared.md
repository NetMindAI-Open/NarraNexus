# admin.conversations.disconnectShared

## Description
Disconnect a connected channel from one or more workspaces.

## Required scope
`admin.conversations:write`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `channel_id` | string | yes | The channel to be disconnected from some workspaces. |
| `leaving_team_ids` | string | no | The team to be removed from the channel. Currently only a single team id can be specified. |

## Example
```python
slack_cli("admin.conversations.disconnectShared", {"channel_id": "..."})
```
