# admin.users.session.invalidate

## Description
Invalidate a single session for a user by session_id

## Required scope
`admin.users:write`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `team_id` | string | yes | ID of the team that the session belongs to |
| `session_id` | integer | yes | — |

## Example
```python
slack_cli("admin.users.session.invalidate", {"team_id": "...", "session_id": 1})
```
