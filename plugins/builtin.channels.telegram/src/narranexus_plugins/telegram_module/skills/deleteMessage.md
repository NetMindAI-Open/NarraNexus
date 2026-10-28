# deleteMessage

## Description
Use this method to delete a message, including service messages, with the following limitations: a message can only be deleted if it was sent less than 48 hours ago; bots can delete outgoing messages in private chats, groups, and supergroups; bots granted `can_post_messages` permissions can delete outgoing messages in channels; etc.

## Required scope
Telegram bots are token-scoped, no per-method permissions. The bot must be admin in the chat with `can_delete_messages` for messages it didn't author.

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `chat_id` | integer or string | yes | Unique identifier for the target chat or username of the target channel (in the format `@channelusername`). |
| `message_id` | integer | yes | Identifier of the message to delete. |

## Response
Returns `True` on success.

## Example
```python
tg_cli("deleteMessage", {"chat_id": 123456789, "message_id": 999})
```
