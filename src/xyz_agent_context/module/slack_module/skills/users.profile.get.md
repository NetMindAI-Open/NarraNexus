# users.profile.get

## Description
Retrieves a user's profile information.

## Required scope
`users.profile:read`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `include_labels` | boolean | no | Include labels for each ID in custom profile fields |
| `user` | string | no | User to retrieve profile info for |

## Example
```python
slack_cli("users.profile.get", {})
```
