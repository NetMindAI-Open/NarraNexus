# dnd.info

## Description
Retrieves a user's current Do Not Disturb status.

## Required scope
`dnd:read`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `user` | string | no | User to fetch status for (defaults to current user) |

## Example
```python
slack_cli("dnd.info", {})
```
