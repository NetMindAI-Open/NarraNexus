# chat.meMessage

## Description
Share a me message into a channel.

## Required scope
`chat:write:user`, `chat:write:bot`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `channel` | string | no | Channel to send message to. Can be a public channel, private group or IM channel. Can be an encoded ID, or a name. |
| `text` | string | no | Text of the message to send. |

## Example
```python
slack_cli("chat.meMessage", {})
```
