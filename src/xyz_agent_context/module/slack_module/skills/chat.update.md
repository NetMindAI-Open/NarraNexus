# chat.update

## Description
Updates a message.

## Required scope
`chat:write:user`, `chat:write:bot`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `as_user` | string | no | Pass true to update the message as the authed user. [Bot users](/bot-users) in this context are considered authed users. |
| `attachments` | string | no | A JSON-based array of structured attachments, presented as a URL-encoded string. This field is required when not presenting `text`. If you don't include this field, the message's previous `attachments` will be retained. To remove previous `attachments`, include an empty array for this field. |
| `blocks` | string | no | A JSON-based array of [structured blocks](/block-kit/building), presented as a URL-encoded string. If you don't include this field, the message's previous `blocks` will be retained. To remove previous `blocks`, include an empty array for this field. |
| `channel` | string | yes | Channel containing the message to be updated. |
| `link_names` | string | no | Find and link channel names and usernames. Defaults to `none`. If you do not specify a value for this field, the original value set for the message will be overwritten with the default, `none`. |
| `parse` | string | no | Change how messages are treated. Defaults to `client`, unlike `chat.postMessage`. Accepts either `none` or `full`. If you do not specify a value for this field, the original value set for the message will be overwritten with the default, `client`. |
| `text` | string | no | New text for the message, using the [default formatting rules](/reference/surfaces/formatting). It's not required when presenting `blocks` or `attachments`. |
| `ts` | string | yes | Timestamp of the message to be updated. |

## Example
```python
slack_cli("chat.update", {"channel": "...", "ts": "..."})
```
