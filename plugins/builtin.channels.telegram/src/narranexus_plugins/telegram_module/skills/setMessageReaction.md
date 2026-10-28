# setMessageReaction

## Description
Use this method to change the chosen reactions on a message. Service messages of some types can't be reacted to. Automatically forwarded messages from a channel to its discussion group have the same available reactions as messages in the channel.

## Required scope
Telegram bots are token-scoped, no per-method permissions. Bots can't use paid reactions.

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `chat_id` | integer or string | yes | Unique identifier for the target chat or username of the target channel (in the format `@channelusername`). |
| `message_id` | integer | yes | Identifier of the target message. If the message belongs to a media group, the reaction is set to the first non-deleted message in the group instead. |
| `reaction` | array of ReactionType | no | A JSON-serialized list of reaction types to set on the message. Currently, as non-premium users, bots can set up to one reaction per message. A custom emoji reaction can be used if it is either already present on the message or explicitly allowed by chat administrators. |
| `is_big` | boolean | no | Pass True to set the reaction with a big animation. |

## Response
Returns `True` on success.

## Example
```python
tg_cli("setMessageReaction", {"chat_id": 123, "message_id": 999, "reaction": [{"type": "emoji", "emoji": "👍"}]})
```
