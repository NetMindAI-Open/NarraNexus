# reminders.info

## Description
Gets information about a reminder.

## Required scope
`reminders:read`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `reminder` | string | no | The ID of the reminder |

## Example
```python
slack_cli("reminders.info", {})
```
