# unpinChatMessage

## Description
Use this method to remove a message from the list of pinned messages in a chat.

## Required scope
Telegram bots are token-scoped, no per-method permissions. In private chats no admin rights needed; in groups/channels the bot must be an administrator with `can_pin_messages` (groups) or `can_edit_messages` (channels) permission.

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `business_connection_id` | string | no | Unique identifier of the business connection on behalf of which the message will be unpinned. |
| `chat_id` | integer or string | yes | Unique identifier for the target chat or username of the target channel (in the format `@channelusername`). |
| `message_id` | integer | no | Identifier of the message to unpin. Required if `business_connection_id` is specified. If not specified, the most recent pinned message (by sending date) will be unpinned. |

## Response
Returns `True` on success.

## Example
```python
tg_cli("unpinChatMessage", {"chat_id": -1001234567890, "message_id": 999})
```
