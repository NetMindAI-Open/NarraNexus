# users.info

## Description
Gets information about a user.

## Required scope
`users:read`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `include_locale` | boolean | no | Set this to `true` to receive the locale for this user. Defaults to `false` |
| `user` | string | no | User to get info on |

## Example
```python
slack_cli("users.info", {})
```
