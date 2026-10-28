# chat.deleteScheduledMessage

## Description
Deletes a pending scheduled message from the queue.

## Required scope
`chat:write:user`, `chat:write:bot`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `as_user` | boolean | no | Pass true to delete the message as the authed user with `chat:write:user` scope. [Bot users](/bot-users) in this context are considered authed users. If unused or false, the message will be deleted with `chat:write:bot` scope. |
| `channel` | string | yes | The channel the scheduled_message is posting to |
| `scheduled_message_id` | string | yes | `scheduled_message_id` returned from call to chat.scheduleMessage |

## Example
```python
slack_cli("chat.deleteScheduledMessage", {"channel": "...", "scheduled_message_id": "..."})
```
