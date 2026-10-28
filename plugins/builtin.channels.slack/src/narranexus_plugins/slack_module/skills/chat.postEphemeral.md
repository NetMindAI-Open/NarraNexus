# chat.postEphemeral

## Description
Sends an ephemeral message to a user in a channel.

## Required scope
`chat:write:user`, `chat:write:bot`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `as_user` | boolean | no | Pass true to post the message as the authed user. Defaults to true if the chat:write:bot scope is not included. Otherwise, defaults to false. |
| `attachments` | string | no | A JSON-based array of structured attachments, presented as a URL-encoded string. |
| `blocks` | string | no | A JSON-based array of structured blocks, presented as a URL-encoded string. |
| `channel` | string | yes | Channel, private group, or IM channel to send message to. Can be an encoded ID, or a name. |
| `icon_emoji` | string | no | Emoji to use as the icon for this message. Overrides `icon_url`. Must be used in conjunction with `as_user` set to `false`, otherwise ignored. See [authorship](#authorship) below. |
| `icon_url` | string | no | URL to an image to use as the icon for this message. Must be used in conjunction with `as_user` set to false, otherwise ignored. See [authorship](#authorship) below. |
| `link_names` | boolean | no | Find and link channel names and usernames. |
| `parse` | string | no | Change how messages are treated. Defaults to `none`. See [below](#formatting). |
| `text` | string | no | How this field works and whether it is required depends on other fields you use in your API call. [See below](#text_usage) for more detail. |
| `thread_ts` | string | no | Provide another message's `ts` value to post this message in a thread. Avoid using a reply's `ts` value; use its parent's value instead. Ephemeral messages in threads are only shown if there is already an active thread. |
| `user` | string | yes | `id` of the user who will receive the ephemeral message. The user should be in the channel specified by the `channel` argument. |
| `username` | string | no | Set your bot's user name. Must be used in conjunction with `as_user` set to false, otherwise ignored. See [authorship](#authorship) below. |

## Example
```python
slack_cli("chat.postEphemeral", {"channel": "...", "user": "..."})
```
