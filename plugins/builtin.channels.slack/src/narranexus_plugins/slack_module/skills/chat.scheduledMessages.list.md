# chat.scheduledMessages.list

## Description
Returns a list of scheduled messages.

## Required scope
`none`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `channel` | string | no | The channel of the scheduled messages |
| `latest` | number | no | A UNIX timestamp of the latest value in the time range |
| `oldest` | number | no | A UNIX timestamp of the oldest value in the time range |
| `limit` | integer | no | Maximum number of original entries to return. |
| `cursor` | string | no | For pagination purposes, this is the `cursor` value returned from a previous call to `chat.scheduledmessages.list` indicating where you want to start this call from. |

## Example
```python
slack_cli("chat.scheduledMessages.list", {})
```
