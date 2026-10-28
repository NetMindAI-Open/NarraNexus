# sendMessage

## Description
Use this method to send text messages. On success, the sent Message is returned.

## Required scope
Telegram bots are token-scoped, no per-method permissions. The bot must be a member of the chat (or the user must have started a chat with it).

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `business_connection_id` | string | no | Unique identifier of the business connection on behalf of which the message will be sent. |
| `chat_id` | integer or string | yes | Unique identifier for the target chat or username of the target channel (in the format `@channelusername`). |
| `message_thread_id` | integer | no | Unique identifier for the target message thread (topic) of the forum; for forum supergroups only. |
| `text` | string | yes | Text of the message to be sent, 1-4096 characters after entities parsing. |
| `parse_mode` | string | no | Mode for parsing entities in the message text. See formatting options. (`MarkdownV2`, `HTML`, `Markdown`). |
| `entities` | array of MessageEntity | no | A JSON-serialized list of special entities that appear in message text, which can be specified instead of `parse_mode`. |
| `link_preview_options` | LinkPreviewOptions | no | Link preview generation options for the message. |
| `disable_notification` | boolean | no | Sends the message silently. Users will receive a notification with no sound. |
| `protect_content` | boolean | no | Protects the contents of the sent message from forwarding and saving. |
| `allow_paid_broadcast` | boolean | no | Pass True to allow up to 1000 messages per second, ignoring broadcasting limits for a fee of 0.1 Telegram Stars per message. |
| `message_effect_id` | string | no | Unique identifier of the message effect to be added to the message; for private chats only. |
| `reply_parameters` | ReplyParameters | no | Description of the message to reply to. |
| `reply_markup` | InlineKeyboardMarkup or ReplyKeyboardMarkup or ReplyKeyboardRemove or ForceReply | no | Additional interface options. |

## Response
On success, the sent `Message` is returned.

## Example
```python
tg_cli("sendMessage", {"chat_id": 123456789, "text": "Hello, world!"})
```
