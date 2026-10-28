# getMyCommands

## Description
Use this method to get the current list of the bot's commands for the given scope and user language.

## Required scope
Telegram bots are token-scoped, no per-method permissions.

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `scope` | BotCommandScope | no | A JSON-serialized object, describing scope of users. Defaults to `BotCommandScopeDefault`. |
| `language_code` | string | no | A two-letter ISO 639-1 language code or an empty string. |

## Response
Returns an Array of `BotCommand` objects. If commands aren't set, an empty list is returned.

## Example
```python
tg_cli("getMyCommands", {})
```
