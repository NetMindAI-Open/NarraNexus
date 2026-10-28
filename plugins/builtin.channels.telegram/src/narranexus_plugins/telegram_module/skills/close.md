# close

## Description
Use this method to close the bot instance before moving it from one local server to another. You need to delete the webhook before calling this method to ensure that the bot isn't launched again after server restart. The method will return error 429 in the first 10 minutes after the bot is launched. Requires no parameters.

## Required scope
Telegram bots are token-scoped, no per-method permissions.

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| _(none)_ | | | This method takes no parameters. |

## Response
Returns `True` on success.

## Example
```python
tg_cli("close", {})
```
