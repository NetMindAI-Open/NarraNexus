# deleteMyCommands

## Description
Use this method to delete the list of the bot's commands for the given scope and user language. After deletion, higher level commands will be shown to affected users.

## Required scope
Telegram bots are token-scoped, no per-method permissions.

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `scope` | BotCommandScope | no | A JSON-serialized object, describing scope of users for which the commands are relevant. Defaults to `BotCommandScopeDefault`. |
| `language_code` | string | no | A two-letter ISO 639-1 language code. If empty, commands will be applied to all users from the given scope, for whose language there are no dedicated commands. |

## Response
Returns `True` on success.

## Example
```python
tg_cli("deleteMyCommands", {})
```
