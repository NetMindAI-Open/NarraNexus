# admin.users.remove

## Description
Remove a user from a workspace.

## Required scope
`admin.users:write`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `team_id` | string | yes | The ID (`T1234`) of the workspace. |
| `user_id` | string | yes | The ID of the user to remove. |

## Example
```python
slack_cli("admin.users.remove", {"team_id": "...", "user_id": "..."})
```
