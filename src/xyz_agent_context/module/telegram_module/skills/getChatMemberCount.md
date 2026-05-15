# getChatMemberCount

## Description
Use this method to get the number of members in a chat.

## Required scope
Telegram bots are token-scoped, no per-method permissions. The bot must be a member of the chat.

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `chat_id` | integer or string | yes | Unique identifier for the target chat or username of the target supergroup or channel (in the format `@channelusername`). |

## Response
Returns the count as an `Integer` on success.

## Example
```python
tg_cli("getChatMemberCount", {"chat_id": -1001234567890})
```
