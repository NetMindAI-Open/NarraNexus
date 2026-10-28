# admin.users.assign

## Description
Add an Enterprise user to a workspace.

## Required scope
`admin.users:write`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `team_id` | string | yes | The ID (`T1234`) of the workspace. |
| `user_id` | string | yes | The ID of the user to add to the workspace. |
| `is_restricted` | boolean | no | True if user should be added to the workspace as a guest. |
| `is_ultra_restricted` | boolean | no | True if user should be added to the workspace as a single-channel guest. |
| `channel_ids` | string | no | Comma separated values of channel IDs to add user in the new workspace. |

## Example
```python
slack_cli("admin.users.assign", {"team_id": "...", "user_id": "..."})
```
