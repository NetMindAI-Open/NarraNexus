# setMyDescription

## Description
Use this method to change the bot's description, which is shown in the chat with the bot if the chat is empty.

## Required scope
Telegram bots are token-scoped, no per-method permissions.

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `description` | string | no | New bot description; 0-512 characters. Pass an empty string to remove the dedicated description for the given language. |
| `language_code` | string | no | A two-letter ISO 639-1 language code. If empty, the description will be applied to all users for whose language there is no dedicated description. |

## Response
Returns `True` on success.

## Example
```python
tg_cli("setMyDescription", {"description": "I help you remember conversations."})
```
