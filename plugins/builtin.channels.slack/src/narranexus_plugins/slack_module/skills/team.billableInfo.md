# team.billableInfo

## Description
Gets billable users information for the current team.

## Required scope
`admin`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `user` | string | no | A user to retrieve the billable information for. Defaults to all users. |

## Example
```python
slack_cli("team.billableInfo", {})
```
