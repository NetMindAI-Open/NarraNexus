# pinChatMessage

## Description
Use this method to add a message to the list of pinned messages in a chat.

## Required scope
Telegram bots are token-scoped, no per-method permissions. In private chats, this method can be called without admin rights; in groups/channels, the bot must be an administrator with `can_pin_messages` (groups) or `can_edit_messages` (channels) permission.

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `business_connection_id` | string | no | Unique identifier of the business connection on behalf of which the message will be pinned. |
| `chat_id` | integer or string | yes | Unique identifier for the target chat or username of the target channel (in the format `@channelusername`). |
| `message_id` | integer | yes | Identifier of a message to pin. |
| `disable_notification` | boolean | no | Pass True if it is not necessary to send a notification to all chat members about the new pinned message. |

## Response
Returns `True` on success.

## Example
```python
tg_cli("pinChatMessage", {"chat_id": -1001234567890, "message_id": 999})
```
