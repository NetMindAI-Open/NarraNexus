# getChatAdministrators

## Description
Use this method to get a list of administrators in a chat, which aren't bots. Returns an Array of ChatMember objects.

## Required scope
Telegram bots are token-scoped, no per-method permissions. The bot must be a member of the chat.

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `chat_id` | integer or string | yes | Unique identifier for the target chat or username of the target supergroup or channel (in the format `@channelusername`). |

## Response
Returns an Array of `ChatMember` objects that contain information about all chat administrators except other bots. If the chat is a basic group or supergroup and no administrators were appointed, only the creator will be returned.

## Example
```python
tg_cli("getChatAdministrators", {"chat_id": -1001234567890})
```
