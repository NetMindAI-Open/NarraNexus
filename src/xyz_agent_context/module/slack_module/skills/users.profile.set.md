# users.profile.set

## Description
Set the profile information for a user.

## Required scope
`users.profile:write`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `name` | string | no | Name of a single key to set. Usable only if `profile` is not passed. |
| `profile` | string | no | Collection of key:value pairs presented as a URL-encoded JSON hash. At most 50 fields may be set. Each field name is limited to 255 characters. |
| `user` | string | no | ID of user to change. This argument may only be specified by team admins on paid teams. |
| `value` | string | no | Value to set a single key to. Usable only if `profile` is not passed. |

## Example
```python
slack_cli("users.profile.set", {})
```
