# team.profile.get

## Description
Retrieve a team's profile.

## Required scope
`users.profile:read`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `visibility` | string | no | Filter by visibility. |

## Example
```python
slack_cli("team.profile.get", {})
```
