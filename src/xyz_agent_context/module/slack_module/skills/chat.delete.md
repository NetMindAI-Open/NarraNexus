# chat.delete

## Description
Deletes a message.

## Required scope
`chat:write:user`, `chat:write:bot`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `ts` | number | no | Timestamp of the message to be deleted. |
| `channel` | string | no | Channel containing the message to be deleted. |
| `as_user` | boolean | no | Pass true to delete the message as the authed user with `chat:write:user` scope. [Bot users](/bot-users) in this context are considered authed users. If unused or false, the message will be deleted with `chat:write:bot` scope. |

## Example
```python
slack_cli("chat.delete", {})
```
