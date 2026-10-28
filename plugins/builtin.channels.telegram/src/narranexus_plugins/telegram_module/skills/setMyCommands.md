# setMyCommands

## Description
Use this method to change the list of the bot's commands. See this manual for more details about bot commands.

## Required scope
Telegram bots are token-scoped, no per-method permissions.

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `commands` | array of BotCommand | yes | A JSON-serialized list of bot commands to be set as the list of the bot's commands. At most 100 commands can be specified. |
| `scope` | BotCommandScope | no | A JSON-serialized object, describing scope of users for which the commands are relevant. Defaults to `BotCommandScopeDefault`. |
| `language_code` | string | no | A two-letter ISO 639-1 language code. If empty, commands will be applied to all users from the given scope, for whose language there are no dedicated commands. |

## Response
Returns `True` on success.

## Example
```python
tg_cli("setMyCommands", {"commands": [{"command": "start", "description": "Start the bot"}]})
```
