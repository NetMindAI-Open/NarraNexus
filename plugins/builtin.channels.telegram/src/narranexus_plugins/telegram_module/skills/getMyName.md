# getMyName

## Description
Use this method to get the current bot name for the given user language.

## Required scope
Telegram bots are token-scoped, no per-method permissions.

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `language_code` | string | no | A two-letter ISO 639-1 language code or an empty string. |

## Response
Returns a `BotName` object on success (`{"name": "..."}`).

## Example
```python
tg_cli("getMyName", {})
```
