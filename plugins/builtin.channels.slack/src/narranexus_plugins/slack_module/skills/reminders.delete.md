# reminders.delete

## Description
Deletes a reminder.

## Required scope
`reminders:write`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `reminder` | string | no | The ID of the reminder |

## Example
```python
slack_cli("reminders.delete", {})
```
