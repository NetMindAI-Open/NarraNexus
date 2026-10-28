# reminders.complete

## Description
Marks a reminder as complete.

## Required scope
`reminders:write`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `reminder` | string | no | The ID of the reminder to be marked as complete |

## Example
```python
slack_cli("reminders.complete", {})
```
