# admin.users.setAdmin

## Description
Set an existing guest, regular user, or owner to be an admin user.

## Required scope
`admin.users:write`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `team_id` | string | yes | The ID (`T1234`) of the workspace. |
| `user_id` | string | yes | The ID of the user to designate as an admin. |

## Example
```python
slack_cli("admin.users.setAdmin", {"team_id": "...", "user_id": "..."})
```
