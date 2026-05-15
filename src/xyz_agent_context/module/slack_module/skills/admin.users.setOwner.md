# admin.users.setOwner

## Description
Set an existing guest, regular user, or admin user to be a workspace owner.

## Required scope
`admin.users:write`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `team_id` | string | yes | The ID (`T1234`) of the workspace. |
| `user_id` | string | yes | Id of the user to promote to owner. |

## Example
```python
slack_cli("admin.users.setOwner", {"team_id": "...", "user_id": "..."})
```
