# deleteWebhook

## Description
Use this method to remove webhook integration if you decide to switch back to getUpdates.

## Required scope
Telegram bots are token-scoped, no per-method permissions.

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `drop_pending_updates` | boolean | no | Pass True to drop all pending updates. |

## Response
Returns `True` on success.

## Example
```python
tg_cli("deleteWebhook", {"drop_pending_updates": True})
```
