# chat.scheduleMessage

## Description
Schedules a message to be sent to a channel.

## Required scope
`chat:write:user`, `chat:write:bot`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `channel` | string | no | Channel, private group, or DM channel to send message to. Can be an encoded ID, or a name. See [below](#channels) for more details. |
| `text` | string | no | How this field works and whether it is required depends on other fields you use in your API call. [See below](#text_usage) for more detail. |
| `post_at` | string | no | Unix EPOCH timestamp of time in future to send the message. |
| `parse` | string | no | Change how messages are treated. Defaults to `none`. See [chat.postMessage](chat.postMessage#formatting). |
| `as_user` | boolean | no | Pass true to post the message as the authed user, instead of as a bot. Defaults to false. See [chat.postMessage](chat.postMessage#authorship). |
| `link_names` | boolean | no | Find and link channel names and usernames. |
| `attachments` | string | no | A JSON-based array of structured attachments, presented as a URL-encoded string. |
| `blocks` | string | no | A JSON-based array of structured blocks, presented as a URL-encoded string. |
| `unfurl_links` | boolean | no | Pass true to enable unfurling of primarily text-based content. |
| `unfurl_media` | boolean | no | Pass false to disable unfurling of media content. |
| `thread_ts` | number | no | Provide another message's `ts` value to make this message a reply. Avoid using a reply's `ts` value; use its parent instead. |
| `reply_broadcast` | boolean | no | Used in conjunction with `thread_ts` and indicates whether reply should be made visible to everyone in the channel or conversation. Defaults to `false`. |

## Example
```python
slack_cli("chat.scheduleMessage", {})
```
