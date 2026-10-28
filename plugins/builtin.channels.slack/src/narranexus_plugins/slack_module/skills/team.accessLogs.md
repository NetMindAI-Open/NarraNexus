# team.accessLogs

## Description
Gets the access logs for the current team.

## Required scope
`admin`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `before` | string | no | End of time range of logs to include in results (inclusive). |
| `count` | string | no | — |
| `page` | string | no | — |

## Example
```python
slack_cli("team.accessLogs", {})
```
