# admin.users.setExpiration

## Description
Set an expiration for a guest user

## Required scope
`admin.users:write`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `team_id` | string | yes | The ID (`T1234`) of the workspace. |
| `user_id` | string | yes | The ID of the user to set an expiration for. |
| `expiration_ts` | integer | yes | Timestamp when guest account should be disabled. |

## Example
```python
slack_cli("admin.users.setExpiration", {"team_id": "...", "user_id": "...", "expiration_ts": 1})
```
