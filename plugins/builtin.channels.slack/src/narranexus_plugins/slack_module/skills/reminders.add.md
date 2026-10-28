# reminders.add

## Description
Creates a reminder.

## Required scope
`reminders:write`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `text` | string | yes | The content of the reminder |
| `time` | string | yes | When this reminder should happen: the Unix timestamp (up to five years from now), the number of seconds until the reminder (if within 24 hours), or a natural language description (Ex. "in 15 minutes," or "every Thursday") |
| `user` | string | no | The user who will receive the reminder. If no user is specified, the reminder will go to user who created it. |

## Example
```python
slack_cli("reminders.add", {"text": "...", "time": "..."})
```
