# admin.users.session.reset

## Description
Wipes all valid sessions on all devices for a given user

## Required scope
`admin.users:write`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `user_id` | string | yes | The ID of the user to wipe sessions for |
| `mobile_only` | boolean | no | Only expire mobile sessions (default: false) |
| `web_only` | boolean | no | Only expire web sessions (default: false) |

## Example
```python
slack_cli("admin.users.session.reset", {"user_id": "..."})
```
