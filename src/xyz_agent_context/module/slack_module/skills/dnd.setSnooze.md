# dnd.setSnooze

## Description
Turns on Do Not Disturb mode for the current user, or changes its duration.

## Required scope
`dnd:write`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `num_minutes` | string | yes | Number of minutes, from now, to snooze until. |

## Example
```python
slack_cli("dnd.setSnooze", {"num_minutes": "..."})
```
