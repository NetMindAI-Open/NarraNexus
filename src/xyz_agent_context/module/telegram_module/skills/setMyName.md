# setMyName

## Description
Use this method to change the bot's name.

## Required scope
Telegram bots are token-scoped, no per-method permissions.

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `name` | string | no | New bot name; 0-64 characters. Pass an empty string to remove the dedicated name for the given language. |
| `language_code` | string | no | A two-letter ISO 639-1 language code. If empty, the name will be shown to all users for whose language there is no dedicated name. |

## Response
Returns `True` on success.

## Example
```python
tg_cli("setMyName", {"name": "NarraBot"})
```
