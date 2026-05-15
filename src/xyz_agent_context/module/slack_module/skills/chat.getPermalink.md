# chat.getPermalink

## Description
Retrieve a permalink URL for a specific extant message

## Required scope
`none`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `channel` | string | yes | The ID of the conversation or channel containing the message |
| `message_ts` | string | yes | A message's `ts` value, uniquely identifying it within a channel |

## Example
```python
slack_cli("chat.getPermalink", {"channel": "...", "message_ts": "..."})
```
