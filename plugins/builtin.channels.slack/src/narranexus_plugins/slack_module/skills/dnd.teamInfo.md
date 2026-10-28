# dnd.teamInfo

## Description
Retrieves the Do Not Disturb status for up to 50 users on a team.

## Required scope
`dnd:read`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `users` | string | no | Comma-separated list of users to fetch Do Not Disturb status for |

## Example
```python
slack_cli("dnd.teamInfo", {})
```
