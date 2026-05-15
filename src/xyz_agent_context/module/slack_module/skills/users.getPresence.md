# users.getPresence

## Description
Gets user presence information.

## Required scope
`users:read`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `user` | string | no | User to get presence info on. Defaults to the authed user. |

## Example
```python
slack_cli("users.getPresence", {})
```
