# getMe

## Description
A simple method for testing your bot's authentication token. Requires no parameters. Returns basic information about the bot in form of a User object.

## Required scope
Telegram bots are token-scoped, no per-method permissions.

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| _(none)_ | | | This method takes no parameters. |

## Response
Returns a `User` object describing the bot (id, is_bot, first_name, username, can_join_groups, can_read_all_group_messages, supports_inline_queries).

## Example
```python
tg_cli("getMe", {})
```
