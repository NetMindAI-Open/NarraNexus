# getChat

## Description
Use this method to get up-to-date information about the chat (current name of the user for one-on-one conversations, current username of a user, group or channel, etc.).

## Required scope
Telegram bots are token-scoped, no per-method permissions. The bot must be a member of the chat (or be able to access it).

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `chat_id` | integer or string | yes | Unique identifier for the target chat or username of the target supergroup or channel (in the format `@channelusername`). |

## Response
Returns a `ChatFullInfo` object on success (id, type, title, username, photo, bio, description, invite_link, pinned_message, permissions, ...).

## Example
```python
tg_cli("getChat", {"chat_id": "@somechannel"})
```
