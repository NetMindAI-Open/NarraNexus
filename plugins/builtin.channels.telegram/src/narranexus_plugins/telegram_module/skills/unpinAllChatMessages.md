# unpinAllChatMessages

## Description
Use this method to clear the list of pinned messages in a chat.

## Required scope
Telegram bots are token-scoped, no per-method permissions. In private chats no admin rights needed; in groups/channels the bot must be an administrator with `can_pin_messages` (groups) or `can_edit_messages` (channels) permission.

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `chat_id` | integer or string | yes | Unique identifier for the target chat or username of the target channel (in the format `@channelusername`). |

## Response
Returns `True` on success.

## Example
```python
tg_cli("unpinAllChatMessages", {"chat_id": -1001234567890})
```
