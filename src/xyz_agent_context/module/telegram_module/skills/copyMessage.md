# copyMessage

## Description
Use this method to copy messages of any kind. Service messages, paid media messages, giveaway messages, giveaway winners messages, and invoice messages can't be copied. The method is analogous to the method forwardMessage, but the copied message doesn't have a link to the original message.

## Required scope
Telegram bots are token-scoped, no per-method permissions. The bot must have access to both source and target chats.

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `chat_id` | integer or string | yes | Unique identifier for the target chat or username of the target channel (in the format `@channelusername`). |
| `message_thread_id` | integer | no | Unique identifier for the target message thread (topic) of the forum; for forum supergroups only. |
| `from_chat_id` | integer or string | yes | Unique identifier for the chat where the original message was sent. |
| `message_id` | integer | yes | Message identifier in the chat specified in `from_chat_id`. |
| `video_start_timestamp` | integer | no | New start timestamp for the copied video in the message. |
| `caption` | string | no | New caption for media, 0-1024 characters after entities parsing. If not specified, the original caption is kept. |
| `parse_mode` | string | no | Mode for parsing entities in the new caption. |
| `caption_entities` | array of MessageEntity | no | A JSON-serialized list of special entities that appear in the new caption. |
| `show_caption_above_media` | boolean | no | Pass True, if the caption must be shown above the message media. |
| `disable_notification` | boolean | no | Sends the message silently. |
| `protect_content` | boolean | no | Protects the contents of the sent message from forwarding and saving. |
| `allow_paid_broadcast` | boolean | no | Pass True to allow up to 1000 messages per second, ignoring broadcasting limits for a fee. |
| `reply_parameters` | ReplyParameters | no | Description of the message to reply to. |
| `reply_markup` | InlineKeyboardMarkup or ReplyKeyboardMarkup or ReplyKeyboardRemove or ForceReply | no | Additional interface options. |

## Response
Returns the `MessageId` of the sent message on success.

## Example
```python
tg_cli("copyMessage", {"chat_id": 123, "from_chat_id": 456, "message_id": 999})
```
