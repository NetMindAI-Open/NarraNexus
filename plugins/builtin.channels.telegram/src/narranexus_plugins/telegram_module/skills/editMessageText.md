# editMessageText

## Description
Use this method to edit text and game messages.

## Required scope
Telegram bots are token-scoped, no per-method permissions. The bot must be the author of the message (or, for inline messages, the originator).

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `business_connection_id` | string | no | Unique identifier of the business connection on behalf of which the message to be edited was sent. |
| `chat_id` | integer or string | no | Required if `inline_message_id` is not specified. Unique identifier for the target chat or username of the target channel (in the format `@channelusername`). |
| `message_id` | integer | no | Required if `inline_message_id` is not specified. Identifier of the message to edit. |
| `inline_message_id` | string | no | Required if `chat_id` and `message_id` are not specified. Identifier of the inline message. |
| `text` | string | yes | New text of the message, 1-4096 characters after entities parsing. |
| `parse_mode` | string | no | Mode for parsing entities in the message text. |
| `entities` | array of MessageEntity | no | A JSON-serialized list of special entities that appear in message text. |
| `link_preview_options` | LinkPreviewOptions | no | Link preview generation options for the message. |
| `reply_markup` | InlineKeyboardMarkup | no | A JSON-serialized object for an inline keyboard. |

## Response
On success, if the edited message is not an inline message, the edited `Message` is returned, otherwise `True` is returned.

## Example
```python
tg_cli("editMessageText", {"chat_id": 123456789, "message_id": 999, "text": "updated"})
```
