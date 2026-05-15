# chat.postMessage

## Description
Sends a message to a channel.

## Required scope
`chat:write:user`, `chat:write:bot`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `as_user` | string | no | Pass true to post the message as the authed user, instead of as a bot. Defaults to false. See [authorship](#authorship) below. |
| `attachments` | string | no | A JSON-based array of structured attachments, presented as a URL-encoded string. |
| `blocks` | string | no | A JSON-based array of structured blocks, presented as a URL-encoded string. |
| `channel` | string | yes | Channel, private group, or IM channel to send message to. Can be an encoded ID, or a name. See [below](#channels) for more details. |
| `icon_emoji` | string | no | Emoji to use as the icon for this message. Overrides `icon_url`. Must be used in conjunction with `as_user` set to `false`, otherwise ignored. See [authorship](#authorship) below. |
| `icon_url` | string | no | URL to an image to use as the icon for this message. Must be used in conjunction with `as_user` set to false, otherwise ignored. See [authorship](#authorship) below. |
| `link_names` | boolean | no | Find and link channel names and usernames. |
| `mrkdwn` | boolean | no | Disable Slack markup parsing by setting to `false`. Enabled by default. |
| `parse` | string | no | Change how messages are treated. Defaults to `none`. See [below](#formatting). |
| `reply_broadcast` | boolean | no | Used in conjunction with `thread_ts` and indicates whether reply should be made visible to everyone in the channel or conversation. Defaults to `false`. |
| `text` | string | no | How this field works and whether it is required depends on other fields you use in your API call. [See below](#text_usage) for more detail. |
| `thread_ts` | string | no | Provide another message's `ts` value to make this message a reply. Avoid using a reply's `ts` value; use its parent instead. |
| `unfurl_links` | boolean | no | Pass true to enable unfurling of primarily text-based content. |
| `unfurl_media` | boolean | no | Pass false to disable unfurling of media content. |
| `username` | string | no | Set your bot's user name. Must be used in conjunction with `as_user` set to false, otherwise ignored. See [authorship](#authorship) below. |

## Example
```python
slack_cli("chat.postMessage", {"channel": "..."})
```
