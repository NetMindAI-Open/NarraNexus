# getUpdates

## Description
Use this method to receive incoming updates using long polling. Returns an Array of Update objects. Should not be used if a webhook is set.

## Required scope
Telegram bots are token-scoped, no per-method permissions.

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `offset` | integer | no | Identifier of the first update to be returned. Must be greater by one than the highest among the identifiers of previously received updates. |
| `limit` | integer | no | Limits the number of updates to be retrieved. Values between 1-100 are accepted. Defaults to 100. |
| `timeout` | integer | no | Timeout in seconds for long polling. Defaults to 0, i.e. usual short polling. Should be positive, short polling should be used for testing purposes only. |
| `allowed_updates` | array of string | no | A JSON-serialized list of the update types you want your bot to receive (e.g. `["message", "edited_channel_post", "callback_query"]`). |

## Response
Returns an Array of `Update` objects.

## Example
```python
tg_cli("getUpdates", {"offset": 0, "timeout": 30})
```
