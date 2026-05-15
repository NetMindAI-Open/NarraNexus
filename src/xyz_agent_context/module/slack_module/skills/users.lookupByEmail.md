# users.lookupByEmail

## Description
Find a user with an email address.

## Required scope
`users:read.email`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `email` | string | yes | An email address belonging to a user in the workspace |

## Example
```python
slack_cli("users.lookupByEmail", {"email": "..."})
```
