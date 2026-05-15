# getChatMember

## Description
Use this method to get information about a member of a chat. The method is only guaranteed to work for other users if the bot is an administrator in the chat.

## Required scope
Telegram bots are token-scoped, no per-method permissions. Bot must be in the chat; for non-self lookups in groups the bot should be admin.

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `chat_id` | integer or string | yes | Unique identifier for the target chat or username of the target supergroup or channel (in the format `@channelusername`). |
| `user_id` | integer | yes | Unique identifier of the target user. |

## Response
Returns a `ChatMember` object on success (status, user, plus role-specific fields like can_post_messages, until_date, ...).

## Example
```python
tg_cli("getChatMember", {"chat_id": -1001234567890, "user_id": 42})
```
