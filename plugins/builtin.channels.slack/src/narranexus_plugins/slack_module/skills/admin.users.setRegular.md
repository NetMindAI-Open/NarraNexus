# admin.users.setRegular

## Description
Set an existing guest user, admin user, or owner to be a regular user.

## Required scope
`admin.users:write`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `team_id` | string | yes | The ID (`T1234`) of the workspace. |
| `user_id` | string | yes | The ID of the user to designate as a regular user. |

## Example
```python
slack_cli("admin.users.setRegular", {"team_id": "...", "user_id": "..."})
```
