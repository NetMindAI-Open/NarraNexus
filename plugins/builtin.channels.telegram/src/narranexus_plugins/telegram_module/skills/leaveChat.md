# leaveChat

## Description
Use this method for your bot to leave a group, supergroup or channel.

## Required scope
Telegram bots are token-scoped, no per-method permissions. The bot must be a member of the chat to leave it.

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `chat_id` | integer or string | yes | Unique identifier for the target chat or username of the target supergroup or channel (in the format `@channelusername`). |

## Response
Returns `True` on success.

## Example
```python
tg_cli("leaveChat", {"chat_id": -1001234567890})
```
