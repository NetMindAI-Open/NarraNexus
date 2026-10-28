# admin.conversations.restrictAccess.listGroups

## Description
List all IDP Groups linked to a channel

## Required scope
`admin.conversations:read`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `channel_id` | string | yes | — |
| `team_id` | string | no | The workspace where the channel exists. This argument is required for channels only tied to one workspace, and optional for channels that are shared across an organization. |

## Example
```python
slack_cli("admin.conversations.restrictAccess.listGroups", {"channel_id": "..."})
```
