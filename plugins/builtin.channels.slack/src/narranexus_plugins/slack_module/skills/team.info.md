# team.info

## Description
Gets information about the current team.

## Required scope
`team:read`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `team` | string | no | Team to get info on, if omitted, will return information about the current team. Will only return team that the authenticated token is allowed to see through external shared channels |

## Example
```python
slack_cli("team.info", {})
```
